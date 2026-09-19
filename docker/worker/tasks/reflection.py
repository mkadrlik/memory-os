"""
Tasks — Reflection Engine v2.
Reviews old memories, generates insights, CREATES NEW INDEXABLE POINTS in Qdrant.
"""
import logging
import json
import os
import uuid
from datetime import datetime, timezone

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct, Filter, FieldCondition, Range

# Alias for qdrant_client.models used in Micro Reflection
import qdrant_client.models as qmodels

from services.llm import ollama_chat
from services.embedding import get_embedding

logger = logging.getLogger("cognitive-worker.reflection")

COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "knowledge_base")
REFLECTION_PROMPT = """
You are a cognitive memory assistant. Analyze the provided memories and extract:
1. Recurring patterns
2. Connections between memories
3. Insights or learnings
4. Suggested actions

Memories:
{memories}

Respond in JSON with keys: patterns, connections, insights, actions.
"""


async def reflect_on_memories(qdrant: AsyncQdrantClient) -> dict:
    """
    Runs a reflection cycle on unreflected or old memories.
    GENERATES NEW indexable points in Qdrant with the insights.
    """
    # Fetch memories with low reflection_count or old
    filter_ref = Filter(
        must=[
            FieldCondition(
                key="reflection_count",
                range=Range(lt=3),
            ),
        ]
    )

    results = await qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=filter_ref,
        limit=5,  # reduced from 20 to avoid LLM timeout
        with_payload=True,
        with_vectors=False,
    )

    points = results[0]  # scroll returns (points, next_page_offset)
    if not points:
        logger.info("No memories need reflection")
        return {"status": "no-op", "processed": 0}

    parent_ids = [p.id for p in points]

    # Prepare batch of memories for LLM
    memories_text = "\n\n".join(
        f"- [{p.id[:8]}] Source: {p.payload.get('source', '?')} | {p.payload.get('text', '')[:400]}"
        for p in points
    )

    prompt = REFLECTION_PROMPT.format(memories=memories_text)

    try:
        response = await ollama_chat(prompt)
        reflection_data = json.loads(response)
    except json.JSONDecodeError:
        logger.warning("Reflection returned invalid JSON, saving raw")
        reflection_data = {"raw": response}
    except Exception as e:
        logger.error(f"Reflection LLM error: {e}")
        raise

    # ─── CREATE NEW INDEXABLE POINT with the insight ─────────────────────────
    # Text for embedding: concatenation of insights
    insight_text = json.dumps(reflection_data, ensure_ascii=False, indent=2)
    reflection_vector = await get_embedding(insight_text)

    now = datetime.now(timezone.utc).isoformat()

    reflection_point = PointStruct(
        id=str(uuid.uuid4()),
        vector={"dense": reflection_vector},
        payload={
            "text": insight_text,
            "source": "reflection",
            "tags": ["reflection", "auto-generated", "insight"],
            "created_at": now,
            "reflection_count": 0,
            "last_reflected": None,
            "parent_ids": parent_ids,
            "title": f"Reflection batch ({len(points)} memories)",
            "word_count": len(insight_text.split()),
        },
    )

    await qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[reflection_point],
        wait=True,
    )

    logger.info(f"Reflection point created: {reflection_point.id[:8]} (parents: {len(parent_ids)})")

    # Update metadata of processed memories (lifecycle)
    for point in points:
        new_count = point.payload.get("reflection_count", 0) + 1
        await qdrant.set_payload(
            collection_name=COLLECTION_NAME,
            payload={
                "reflection_count": new_count,
                "last_reflected": now,
            },
            points=[point.id],
        )

    logger.info(f"Reflection completed: {len(points)} memories processed + 1 new indexable point")

    return {
        "status": "reflected",
        "processed": len(points),
        "reflection_point_id": reflection_point.id,
        "reflection": reflection_data,
    }


# ─── MICRO REFLECTION (Phase 3) — Consolidation, not cogitation ────────────

MICRO_REFLECTION_PROMPT = """
You are a cognitive memory assistant. Your job is to CONSOLIDATE existing data, never generate new knowledge.

Analyze the following memory chunk and its similar neighbors. Detect factual contradictions,
inconsistencies, or problematic patterns that could reduce the reliability of this chunk.

Main chunk:
{chunk_text}

Similar neighbors:
{neighbors_text}

Respond in JSON:
{{
    "contradiction_found": true | false,
    "severity": "low" | "medium" | "high",
    "explanation": "Concise description of the problem or confirmation of consistency"
}}
"""

import sqlite3
import os
from typing import Optional

STATE_DB_PATH = os.environ.get("STATE_DB_PATH", "/hermes/state.db")


def get_budget_for_hour(hour_window: str) -> int:
    """Returns how many micro-reflections have run in this hour window."""
    try:
        conn = sqlite3.connect(STATE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count FROM reflection_budget WHERE hour_window = ?",
            (hour_window,),
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        logger.warning(f"Error checking budget: {e}")
        return 0  # fail-open: if can't check, allow through


def increment_budget(hour_window: str, tokens_used: int = 0):
    """Increments the reflection counter for the current hour."""
    try:
        conn = sqlite3.connect(STATE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reflection_budget (hour_window, count, tokens_used)
            VALUES (?, 1, ?)
            ON CONFLICT(hour_window)
            DO UPDATE SET count = count + 1, tokens_used = tokens_used + ?
        """, (hour_window, tokens_used, tokens_used))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Error incrementing budget: {e}")


def _as_verdict(parsed) -> dict | None:
    """Accept a verdict object, including a wrapper that echoes json_object schema."""
    if not isinstance(parsed, dict):
        return None
    if "contradiction_found" in parsed:
        return parsed
    for value in parsed.values():
        if isinstance(value, dict) and "contradiction_found" in value:
            return value
    return None


def _extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of a single JSON object from an LLM reply.

    Tolerates markdown fences and surrounding prose by scanning for balanced
    ``{...}`` blocks. Returns None when nothing parses; the caller must treat
    that as "not analysed", never as "consistent".
    """
    import json as _json
    import re as _re

    if not text:
        return None
    cleaned = _re.sub(r"```(?:json)?", "", text).strip()
    candidates = [cleaned]
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(cleaned[start : i + 1])
                    break
        start = cleaned.find("{", start + 1)
    for candidate in candidates:
        try:
            parsed = _json.loads(candidate)
        except (ValueError, TypeError):
            continue
        verdict = _as_verdict(parsed)
        if verdict is not None:
            return verdict
    return None


_JSON_REPAIR_INSTRUCTION = (
    "\n\nSTRICT: your previous reply was not a parsable JSON object. "
    "Reply with ONLY the JSON object, no prose, no markdown fences."
)


async def _analyze_chunk(prompt: str) -> tuple[dict | None, int]:
    """Ask the LLM to classify a chunk. Two attempts, then give up cleanly.

    Returns ``(verdict, attempt)``. attempt is 1 (first JSON), 2 (repair), or 0
    (no verdict). Returning None is a first-class outcome: the caller skips
    the chunk instead of recording a fake verdict and mutating confidence_score.
    """
    for attempt, text in enumerate((prompt, prompt + _JSON_REPAIR_INSTRUCTION), start=1):
        try:
            response = await ollama_chat(text, json_mode=True)
        except Exception as e:
            logger.warning(f"LLM error (attempt {attempt}): {e}")
            continue
        analysis = _extract_json_object(response)
        if analysis is not None:
            if attempt > 1:
                logger.info("JSON parsed on repair attempt")
            return analysis, attempt
        logger.warning(
            f"Attempt {attempt}: unparsable JSON ({len(response)} chars): {response[:120]!r}"
        )
    return None, 0


def _chunk_sort_key(point) -> tuple:
    payload = point.payload or {}
    rc = int(payload.get("reflection_count") or 0)
    last = str(payload.get("last_reflected") or "")
    created = str(payload.get("created_at") or "")
    return (rc, bool(last), last, created)


async def _select_chunks(qdrant: AsyncQdrantClient, max_chunks: int) -> list:
    """Prefer never-reflected chunks; do not ruminate the collection head.

    Qdrant 1.17 scroll has no reliable created_at DESC here. Page with a
    reflection_count < 3 filter, drop archived=True in Python (missing field
    counts as not archived), then sort by rc, last_reflected, created_at.
    """
    filter_chunks = Filter(
        must=[
            FieldCondition(
                key="reflection_count",
                range=Range(lt=3),
            ),
        ]
    )
    eligible = []
    offset = None
    while True:
        points, offset = await qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=filter_chunks,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break
        for point in points:
            if (point.payload or {}).get("archived") is True:
                continue
            eligible.append(point)
        if offset is None:
            break
    eligible.sort(key=_chunk_sort_key)
    chosen = eligible[:max_chunks]
    n_new = sum(
        1 for p in chosen if int((p.payload or {}).get("reflection_count") or 0) == 0
    )
    logger.info(
        "Micro-reflection select: eligible=%d chosen=%d rc0=%d ids=%s",
        len(eligible),
        len(chosen),
        n_new,
        ",".join(str(p.id)[:8] for p in chosen),
    )
    return chosen


async def micro_reflection(qdrant: AsyncQdrantClient) -> dict:
    """
    Micro-reflection: consolidates freshly ingested chunks using LLM.
    Does NOT create new points. Only updates confidence_score and reflection_notes.
    """
    now = datetime.now(timezone.utc)
    hour_window = now.strftime("%Y-%m-%dT%H")
    
    # ── Budget check ──────────────────────────────────────────────────────
    max_per_hour = int(os.environ.get("MICRO_REFLECTION_MAX_PER_HOUR", "5"))
    current_count = get_budget_for_hour(hour_window)
    
    if current_count >= max_per_hour:
        logger.info(f"Micro-reflection budget exhausted for {hour_window} ({current_count}/{max_per_hour})")
        return {"status": "budget_exceeded", "processed": 0}
    
    # ── Select chunks ─────────────────────────────────────────────────────
    max_chunks = int(os.environ.get("MICRO_REFLECTION_MAX_CHUNKS", "10"))
    try:
        points = await _select_chunks(qdrant, max_chunks)
    except Exception as e:
        logger.warning(f"Error fetching chunks for reflection: {e}")
        return {"status": "error", "error": str(e)}
    
    if not points:
        logger.info("No eligible chunks for micro-reflection")
        return {"status": "no-op", "processed": 0}
    
    processed = 0
    contradictions = 0
    consistencies = 0
    unanalyzed = 0
    frozen = 0
    json_ok_first = 0
    json_ok_repair = 0
    
    for point in points:
        chunk_text = point.payload.get("text", "")
        chunk_id = point.id
        
        if not chunk_text:
            continue
        
        # Fetch similar neighbors via REST API
        try:
            import httpx
            point_data = await qdrant.retrieve(
                collection_name=COLLECTION_NAME,
                ids=[chunk_id],
                with_vectors=True,
            )
            if not point_data or not point_data[0].vector:
                logger.warning(f"Could not get vector for chunk {chunk_id}")
                continue
            
            vector = point_data[0].vector.get("dense") if isinstance(point_data[0].vector, dict) else point_data[0].vector
            
            async with httpx.AsyncClient() as client:
                qdrant_host = os.environ.get("QDRANT_HOST", "qdrant-maas")
                qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
                resp = await client.post(
                    f"http://{qdrant_host}:{qdrant_port}/collections/{COLLECTION_NAME}/points/search",
                    json={
                        "vector": {"name": "dense", "vector": vector},
                        "limit": 4,
                        "with_payload": True,
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                neighbors = resp.json().get("result", [])
            
            # Filter out the chunk itself
            neighbor_texts = []
            for n in neighbors:
                if n["id"] != chunk_id and n.get("payload", {}).get("text"):
                    neighbor_texts.append(f"[{str(n['id'])[:8]}] {n['payload']['text'][:300]}")
            
            if len(neighbor_texts) < 2:
                logger.info(f"Chunk {chunk_id[:8]} has too few neighbors, skipping")
                continue
            
        except Exception as e:
            logger.warning(f"Error fetching neighbors for {chunk_id}: {e}")
            continue
        
        # LLM analysis
        neighbors_text = "\n\n".join(neighbor_texts[:3])
        prompt = MICRO_REFLECTION_PROMPT.format(
            chunk_text=chunk_text[:600],
            neighbors_text=neighbors_text,
        )
        
        analysis, json_attempt = await _analyze_chunk(prompt)
        if analysis is None:
            # No verdict means no payload mutation: an un-analysed chunk must
            # never collect the "consistent" confidence bonus, which inverted
            # the decay signal (19/30 chunks on 2026-09-19).
            unanalyzed += 1
            logger.warning(f"Chunk {chunk_id[:8]} skipped: LLM produced no parsable JSON")
            continue
        if json_attempt == 1:
            json_ok_first += 1
        elif json_attempt == 2:
            json_ok_repair += 1
        
        # Apply result
        current_confidence = point.payload.get("confidence_score", 1.0)
        prev_notes = str(point.payload.get("reflection_notes") or "")
        contradiction_found = analysis.get("contradiction_found", False)
        severity = analysis.get("severity", "low")
        explanation = analysis.get("explanation", "")
        
        if contradiction_found:
            severity_mult = {"low": 0.05, "medium": 0.1, "high": 0.2}.get(severity, 0.1)
            new_confidence = max(0.0, current_confidence - severity_mult)
            contradictions += 1
            reflection_note = f"[CONTRADICTION {severity.upper()}] {explanation}"
        elif prev_notes.startswith("[CONTRADICTION"):
            new_confidence = current_confidence
            consistencies += 1
            frozen += 1
            reflection_note = f"[CONSISTENT frozen] {explanation}"
            logger.info("confidence frozen for %s (prior contradiction)", str(chunk_id)[:8])
        else:
            new_confidence = min(1.0, current_confidence + 0.05)
            consistencies += 1
            reflection_note = f"[CONSISTENT] {explanation}"
        
        # Update payload in Qdrant
        new_count = point.payload.get("reflection_count", 0) + 1
        try:
            await qdrant.set_payload(
                collection_name=COLLECTION_NAME,
                payload={
                    "confidence_score": round(new_confidence, 3),
                    "reflection_count": new_count,
                    "last_reflected": now.isoformat(),
                    "reflection_notes": reflection_note,
                },
                points=[chunk_id],
            )
            processed += 1
            logger.info(f"Micro-reflection {chunk_id[:8]}: {reflection_note[:80]}")
        except Exception as e:
            logger.warning(f"Error updating chunk {chunk_id}: {e}")
    
    # Increment budget
    increment_budget(hour_window, tokens_used=0)
    
    logger.info(
        f"Micro-reflection completed: {processed} chunks written, "
        f"{contradictions} contradictions, {consistencies} consistent, "
        f"{frozen} frozen, {unanalyzed} skipped (no parsable JSON), "
        f"json_ok_first={json_ok_first} json_ok_repair={json_ok_repair}"
    )
    if unanalyzed:
        logger.warning(
            f"INSTRUMENTATION: {unanalyzed} of {unanalyzed + processed} sampled chunks got no LLM "
            "verdict; the consistency counts above exclude them"
        )
    
    return {
        "status": "completed",
        "processed": processed,
        "contradictions": contradictions,
        "consistencies": consistencies,
        "frozen": frozen,
        "unanalyzed": unanalyzed,
        "json_ok_first": json_ok_first,
        "json_ok_repair": json_ok_repair,
        "budget_hour": hour_window,
        "budget_used": current_count + 1,
    }
