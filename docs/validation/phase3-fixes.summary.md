# Phase 3 — defects found and fixed (redacted)

Run: 2026-09-20. Tree: `consolidate/review-2026-09-19`, base `aee86c82`.
No credentials, raw logs or environment dumps are recorded here.

## D1 — Pre-write deduplication never authenticated (`docker/worker/tasks/file_ingestion.py`)

**Defect.** The dedup search posted to `/collections/<c>/points/search` with no
`api-key` header while every other Qdrant call site authenticated. Against an
authenticated Qdrant this is HTTP 401; the surrounding `except Exception`
swallowed it and fell through to a plain upsert.

**Effect.** Silent loss of deduplication — the duplicate the dedup existed to
prevent was created anyway, with no error.

**Fix.** Send the key when `QDRANT_API_KEY` is configured; raise `QdrantAuthError`
on 401/403 so the job fails; keep the historical fail-open insert for transient
transport errors. Password-free mode preserved (no header when unset).

**Evidence.** Mechanism proven on the live lab: request without the key → 401,
with the key → 200. End-to-end afterwards: three distinct documents → three
points; re-ingesting the same document → `status: dedup`, similarity ~1.0, point
count unchanged.

## D2 — Five more direct-Qdrant scripts had no credentials

`pre_validator.py`, `backfill_decay_metadata.py`, `bulk_wiki_ingest.py`,
`decay_scanner.py`, `semantic_dedup.py` (plus the `retry_failed_ingest.py` GET).
`bulk_wiki_ingest.py` is the documented bulk-ingestion path, so a documented
feature did not work against an authenticated Qdrant at all.

**Fix.** Same header pattern already used by `context_enhancer.py`. A final sweep
reports no direct Qdrant call left without authentication.

## D3 — `qdrant-client` unbounded, resolving an incompatible minor

`requirements.txt` and `docker/worker/requirements.txt` asked for
`qdrant-client>=1.17.0`; PyPI resolved 1.19.1 while the pinned server is 1.17.1.
The client itself warned at worker startup that the versions are incompatible.

**Fix.** `>=1.17.0,<1.18.0`. Verified in a dedicated venv with
`importlib.metadata.version("qdrant-client")` → `1.17.1`. The API surface the
project uses was then exercised against the live stack, not merely assumed.

## D4 — Redis healthcheck exposed the password

The compose healthcheck interpolated the password into the command
(`redis-cli -a <pw> ping`), so `docker inspect` printed the credential.

**Fix.** The password reaches the container through `REDISCLI_AUTH`; the test is
`["CMD", "redis-cli", "ping"]`. Password-free mode still works. The worker
healthcheck remains authenticated. This does not hide the credential from anyone
who can already read the container environment; it removes an unnecessary
reproduction of it in a command line.

## D5 — Test defects that made the checks unable to pass

- `setup/smoke_test.sh`: hardcoded `~/.hermes` (wrong for named profiles),
  asserted 4096 dims, and required ≥3 `hermes cron` jobs — which the installer
  never creates (it installs one wiki-watcher crontab entry). Now honours
  `HERMES_HOME`, reads `EMBEDDING_DIMS`, and accepts the crontab entry or timers.
- `scripts/test_ingestion.py`: enqueued a container path while writing the file
  into a host tempdir; filtered on `source_file` while the payload stores
  `file_path`; hardcoded 4096 dims. Rewritten with explicit host/container paths,
  an ambiguity refusal, configurable dims, dense+sparse verification, a dedup
  pass, an invalid-path check and cleanup.

## D6 — Classification note (test defect, not product defect)

A first draft of the dedup tests passed `sparse_vector=None` and the product
raised a `PointStruct` validation error. Investigation showed
`get_sparse_embedding` never returns None — it raises when the model is missing —
so there is no dense-only fallback on the ingestion path and the assumption
belonged to the test. The test was corrected to the real contract, and the
consequence was recorded as a documented dependency: file ingestion requires the
BM25 model.
