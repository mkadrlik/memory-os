#!/usr/bin/env python3
"""End-to-end ingestion test for Memory OS.

Verifies the real pipeline: file lands in the wiki the worker sees → enqueue →
ARQ worker → embedding → Qdrant upsert (dense + sparse) → dedup on a repeat.

Host and container see *different* wiki paths, and the collection's vector
dimensions come from configuration, not from a constant. Both are therefore
explicit here, and the test refuses to run when the target is ambiguous (missing
host wiki directory) instead of enqueuing a path the worker cannot resolve and
then blaming the pipeline.

Usage:
    python3 scripts/test_ingestion.py
    python3 scripts/test_ingestion.py --keep-file   # leave the test document

Environment:
    REDIS_PASSWORD        Redis password (required when the server demands one)
    REDIS_HOST            default: localhost
    REDIS_PORT            default: 6379
    QDRANT_HOST           default: localhost
    QDRANT_PORT           default: 6333
    QDRANT_API_KEY        default: "" (no auth header when empty)
    COLLECTION_NAME       default: knowledge_base
    EMBEDDING_DIMS        default: 4096 — must match the collection
    WIKI_HOST_PATH        host directory the worker's wiki is mounted from
                          (default: <repo>/docker/wiki)
    WIKI_CONTAINER_PATH   the same directory as seen inside the worker
                          (default: /wiki)

Returns exit code 0 on success, 1 on failure.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import NoReturn
import uuid

# ── Config from env ──────────────────────────────────────────────────────────
REPO_DIR = Path(__file__).resolve().parents[1]


def _env_file_value(path: Path, key: str):
    """Return KEY's value from a simple dotenv file, or None. Prints nothing."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _load_profile_env() -> None:
    """Fill connection/embedding settings from the files setup.sh writes.

    Explicit environment variables always win. Without this, running the script
    standalone asserted 4096 dims against a 768-dim collection (the installer's
    value lives in the Compose env file) and reported a failure on a healthy
    stack.
    """
    hermes_home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()
    keys = (
        "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD",
        "QDRANT_HOST", "QDRANT_PORT", "QDRANT_API_KEY",
        "COLLECTION_NAME", "EMBEDDING_DIMS",
    )
    for name in ("memory-os-compose.env", ".env"):
        path = hermes_home / name
        for key in keys:
            if os.environ.get(key):
                continue
            value = _env_file_value(path, key)
            if value:
                os.environ[key] = value


_load_profile_env()

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "knowledge_base")
EMBEDDING_DIMS = int(os.environ.get("EMBEDDING_DIMS", "4096"))


def _resolve_wiki_host_path() -> Path:
    """Resolve the host directory the worker's wiki is mounted from.

    Order: an explicit WIKI_HOST_PATH, then MEMORY_OS_WIKI_PATH from the Compose
    env file setup.sh writes, then WIKI_ROOT from the Hermes profile .env, and
    finally the by-hand layout default (<repo>/docker/wiki).

    Without this the check only worked for a stack started by hand from docker/
    with its relative ./wiki and failed on an installer-created stack, whose
    wiki lives under the profile (MEMORY_OS_WIKI_PATH), telling the operator to
    set WIKI_HOST_PATH by hand.
    """
    explicit = os.environ.get("WIKI_HOST_PATH")
    if explicit:
        return Path(explicit).expanduser()

    hermes_home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()
    value = _env_file_value(hermes_home / "memory-os-compose.env", "MEMORY_OS_WIKI_PATH")
    if not value:
        value = _env_file_value(hermes_home / ".env", "WIKI_ROOT")
    if value:
        return Path(value).expanduser()
    return REPO_DIR / "docker" / "wiki"


WIKI_HOST_PATH = _resolve_wiki_host_path()
WIKI_CONTAINER_PATH = os.environ.get("WIKI_CONTAINER_PATH", "/wiki").rstrip("/")

TIMEOUT = 90  # seconds for ARQ job completion
POLL_INTERVAL = 2  # seconds between polls


def fail(msg: str) -> NoReturn:
    print(f"❌ {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"✅ {msg}")


def info(msg: str) -> None:
    print(f"   {msg}")


# ── Test document ────────────────────────────────────────────────────────────
TEST_ID = uuid.uuid4().hex[:8]
REL_TEST_PATH = f"raw/test/ingestion-test-{TEST_ID}.md"
HOST_TEST_FILE = WIKI_HOST_PATH / REL_TEST_PATH
CONTAINER_TEST_PATH = f"{WIKI_CONTAINER_PATH}/{REL_TEST_PATH}"
TEST_TEXT = (
    f"Memory OS ingestion test document {TEST_ID}. "
    "This file was automatically generated by test_ingestion.py to verify the "
    "end-to-end pipeline: enqueue, worker, embedding, Qdrant upsert."
)


async def wait_for_job(redis, job_id: str, label: str):
    """Wait for an ARQ job to finish and return its result.

    Uses the public arq ``Job`` API. The previous implementation called
    ``ArqRedis.get_job_result``, which does not exist in arq 0.28 (the method
    there is ``_get_job_result``), so this script died with AttributeError
    before it verified anything on a stack that was otherwise healthy. In arq
    0.28 ``Job.result_info()`` is a single non-blocking read (no timeout
    argument), so the polling loop stays here.
    """
    from arq.jobs import Job

    job = Job(job_id, redis)
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        job_info = await job.result_info()
        if job_info is not None:
            if job_info.success:
                return job_info.result
            fail(f"{label}: job failed → {job_info.result}")
        await asyncio.sleep(POLL_INTERVAL)
    fail(f"{label}: job timed out after {TIMEOUT}s — is the ARQ worker running?")
    return None


def write_document() -> None:
    HOST_TEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOST_TEST_FILE.write_text(f"# Test {TEST_ID}\n\n{TEST_TEXT}\n", encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-file", action="store_true",
                        help="do not delete the test document at the end")
    args = parser.parse_args()

    print(f"=== Memory OS Ingestion Test (doc: {TEST_ID}) ===")
    print(f"  Redis:      {REDIS_HOST}:{REDIS_PORT}")
    print(f"  Qdrant:     {QDRANT_HOST}:{QDRANT_PORT} (auth: {'yes' if QDRANT_API_KEY else 'no'})")
    print(f"  Collection: {COLLECTION_NAME} (expecting {EMBEDDING_DIMS} dims)")
    print(f"  Wiki host:  {WIKI_HOST_PATH}")
    print(f"  Wiki cont.: {WIKI_CONTAINER_PATH}")
    print()

    # ── 0. Explicit target ───────────────────────────────────────────────────
    if not WIKI_HOST_PATH.is_dir():
        fail(
            f"host wiki directory not found: {WIKI_HOST_PATH}. "
            "Point WIKI_HOST_PATH at the directory the worker's wiki is mounted "
            "from (see MEMORY_OS_WIKI_PATH in the compose env)."
        )

    # ── 1. Place the document where the worker will read it ──────────────────
    print("1. Creating test document in the wiki...")
    write_document()
    ok(f"Wrote {HOST_TEST_FILE}")
    info(f"worker will be asked for {CONTAINER_TEST_PATH}")

    try:
        from arq import create_pool
        from arq.connections import RedisSettings
    except ImportError:
        fail("arq not installed — run: pip install -r requirements.txt")

    redis = await create_pool(RedisSettings(
        host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD or None,
    ))

    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client = AsyncQdrantClient(host=QDRANT_HOST, port=QDRANT_PORT,
                               api_key=QDRANT_API_KEY or None, https=False)

    def by_path(value: str) -> Filter:
        return Filter(must=[FieldCondition(key="file_path", match=MatchValue(value=value))])

    async def points_for(path_value: str):
        pts, _ = await client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=by_path(path_value),
            with_vectors=True,
            limit=10,
        )
        return pts

    last_job = None
    try:
        # ── 2. First ingestion ───────────────────────────────────────────────
        print("2. Enqueuing process_wiki_file (first run)...")
        job = await redis.enqueue_job("process_wiki_file", CONTAINER_TEST_PATH)
        last_job = job
        ok(f"Job {job.job_id} enqueued")
        result = await wait_for_job(redis, job.job_id, "first ingestion")
        ok(f"Job completed → {json.dumps(result, default=str)}")
        if result.get("status") not in ("upserted", "updated", "dedup"):
            fail(f"unexpected ingestion status: {result.get('status')!r}")

        # ── 3. Verify the point ──────────────────────────────────────────────
        print("3. Verifying the ingested point...")
        points = await points_for(CONTAINER_TEST_PATH)
        if not points:
            fail(
                f"no point with file_path={CONTAINER_TEST_PATH!r} in "
                f"{COLLECTION_NAME!r}. The worker stores the path it resolved."
            )
        point = points[0]
        ok(f"Found point {str(point.id)[:8]}…")

        if "dense" not in (point.vector or {}):
            fail("point has no 'dense' vector — named vectors are not configured")
        dims = len(point.vector["dense"])
        if dims != EMBEDDING_DIMS:
            fail(f"expected {EMBEDDING_DIMS} dimensions, got {dims}")
        ok(f"dense vector: {dims} dims ✓")
        if "sparse" in (point.vector or {}):
            ok("sparse (BM25) vector present ✓")
        else:
            info("no sparse vector on this point (BM25 model unavailable?)")

        payload = point.payload or {}
        if payload.get("file_path") != CONTAINER_TEST_PATH:
            fail(f"payload file_path mismatch: {payload.get('file_path')!r}")
        if TEST_ID not in (payload.get("text") or ""):
            fail("payload text does not contain the test document body")
        ok(f"payload ok (source={payload.get('source')!r}, tags={payload.get('tags')!r})")

        # ── 4. Deduplication on a repeat ─────────────────────────────────────
        print("4. Re-ingesting the same document (dedup path)...")
        before = len(await points_for(CONTAINER_TEST_PATH))
        job2 = await redis.enqueue_job("process_wiki_file", CONTAINER_TEST_PATH)
        last_job = job2
        result2 = await wait_for_job(redis, job2.job_id, "second ingestion")
        after_points = await points_for(CONTAINER_TEST_PATH)
        ok(f"Second run → {json.dumps(result2, default=str)}")
        if len(after_points) > before:
            fail(
                f"duplicate created: {before} point(s) before, "
                f"{len(after_points)} after. Dedup did not merge."
            )
        ok(f"no duplicate: {len(after_points)} point(s) for this file ✓")

        # ── 5. Invalid path must fail loudly ─────────────────────────────────
        # The job is *expected* to fail here, so poll the result directly:
        # wait_for_job() reports a failed job through fail() (SystemExit), which
        # is the right behaviour everywhere else but made this step impossible —
        # it aborted the script exactly when the guard worked.
        print("5. Invalid path must be rejected...")
        from arq.jobs import Job
        job3 = await redis.enqueue_job("process_wiki_file", "/etc/passwd")
        last_job = job3
        info3 = None
        deadline3 = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline3:
            info3 = await Job(job3.job_id, redis).result_info()
            if info3 is not None:
                break
            await asyncio.sleep(POLL_INTERVAL)
        if info3 is None:
            fail("invalid path: the worker never produced a result")
        if info3.success:
            fail(f"invalid path was accepted → {info3.result!r}")
        ok(f"invalid path rejected → {info3.result!r}")

    finally:
        # ── 6. Cleanup ───────────────────────────────────────────────────────
        print("6. Cleaning up...")
        # A crash while a job is still queued would otherwise leave the worker to
        # create the point *after* this cleanup runs — an orphan that the next
        # run then collapses via dedup and reports as a failure on a healthy
        # stack. Let the last job settle first (best effort).
        if last_job is not None:
            try:
                await last_job.result(timeout=TIMEOUT)
            except Exception:  # noqa: BLE001 - settling is best effort
                pass
        try:
            stale = await points_for(CONTAINER_TEST_PATH)
            if stale:
                await client.delete(collection_name=COLLECTION_NAME,
                                    points_selector=[p.id for p in stale])
                ok(f"deleted {len(stale)} test point(s)")
        except Exception as exc:  # noqa: BLE001
            info(f"cleanup of points failed: {exc}")
        if not args.keep_file and HOST_TEST_FILE.exists():
            HOST_TEST_FILE.unlink()
            ok(f"removed {HOST_TEST_FILE}")
        await client.close()
        await redis.close()

    print()
    print("═" * 60)
    ok("All checks passed — ingestion pipeline is operational")


if __name__ == "__main__":
    asyncio.run(main())
