# Memory OS — Scripts

Standalone Python scripts that maintain the Qdrant vector database and wiki pipeline.

## Qdrant Maintenance

| Script | What it does | Run |
|--------|-------------|-----|
| `decay_scanner.py` | Archives low-importance, aged AI content based on half-life decay | Weekly cron |
| `backfill_decay_metadata.py` | Populates missing `importance_score`, `last_accessed_at`, `confidence_score` in Qdrant points | Run once before enabling decay scanner |
| `semantic_dedup.py` | Merges near-duplicate points (cosine >0.92) | Monthly cron |

## Explicit contradiction resolution

After reviewing the evidence, preview a resolution for one point:

```bash
python scripts/resolve_contradiction.py --url http://127.0.0.1:6333 \
  --collection knowledge_base --point-id 123 --actor reviewer \
  --reason "Source checked; document the evidence supporting resolution here"
```

Use the actual endpoint, collection and point ID. Authentication uses
`QDRANT_API_KEY`; no profile or endpoint is selected implicitly. Add `--apply`
to persist. Without it the operation only reads and prints a preview.

Before applying, pause reflection triggers and drain/stop active workers and
other writers to this point; keep them paused until the command finishes.
The operation uses a read followed by a payload update, not a compare-and-swap
transaction. Concurrent reflection or resolution can overwrite audit history
or apply a stale verdict. Resume writers after completion.

Resolution appends an audit event containing UTC time, actor, reason and prior
reflection fields. It clears the unresolved flag, replaces the legacy conflict
note with a resolved marker and resets `reflection_count` to zero so the point
can be selected again. Confidence, text, vectors, archive status and
`last_reflected` are preserved. Archived points remain excluded from selection.
Subsequent valid reflection may change confidence or identify a new conflict.
Missing points, already-resolved points, empty justification/actor and malformed
history are rejected. Existing resolution events are retained; the audit captures
the metadata available at resolution, not earlier notes already overwritten by
past reflection cycles.

For offline verification use `python scripts/test_offline.py`. Direct unittest
execution refuses an inherited `MEMORY_OS_ROOT` pointing outside this checkout,
before importing Icarus. Unset that variable or use the isolated runner.

## Context Injection

| Script | What it does | Used by |
|--------|-------------|---------|
| `context_enhancer.py` | Embedding pipeline: query → embed → search Qdrant (4-level fallback). Also provides BM25 sparse embedding via FastEmbed. | Icarus `pre_llm_call` hook |

## Wiki Pipeline

| Script | What it does | Run |
|--------|-------------|-----|
| `wiki_continuous_ingest.py` | SHA-256 diff detection: finds new/modified wiki files, enqueues ARQ jobs in Redis | Hourly cron |
| `bulk_wiki_ingest.py` | One-shot bulk ingestion of all wiki files into Qdrant | After initial setup or collection rebuild |

## Quality Control

| Script | What it does | Run |
|--------|-------------|-----|
| `pre_validator.py` | Pre-flight validation of wiki documents: YAML frontmatter, required fields, link targets | Before ingestion |
| `reflection_trigger.py` | Idle detection for ARQ worker — enqueues micro-reflection when queue is empty and within hourly budget | Every 5min cron |

## Monitoring

| Script | What it does | Run |
|--------|-------------|-----|
| `dlq_manager.py` | Dead letter queue monitoring and reporting | Every 6h cron |

## Environment variables

All scripts read configuration from environment variables. See `.env.example` in the project root for the full reference.

Key variables:
- `EMBEDDING_API_BASE` / `EMBEDDING_MODEL` — any OpenAI-compatible embedding
  endpoint and model. `EMBEDDING_API_KEY` is optional for authenticated custom
  endpoints; `OPENROUTER_API_KEY` remains the legacy OpenRouter credential.
- `EMBEDDING_REQUEST_TIMEOUT` / `EMBEDDING_REQUEST_RETRIES` — bound synchronous
  query-time retrieval, including local model cold starts.
- `ICARUS_SPARSE_QUERY_ENABLED=0` — skip query-time FastEmbed BM25 when it is
  not installed; Qdrant falls back immediately to dense-only search.
- `WIKI_PATH` — wiki root directory (default: `~/vault/wiki`)
- `COLLECTION_NAME` — Qdrant collection (default: `knowledge_base`)
- `EMBEDDING_DIMS` — vector dimensions (default: 4096)
- `REDIS_PASSWORD` — Redis auth (required for wiki ingest)
- `QDRANT_URL` — Qdrant endpoint (default: `http://localhost:6333`)
