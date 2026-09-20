# Phase 4 — deterministic integration (redacted)

Run: 2026-09-20. Tree: `consolidate/review-2026-09-19` + the Phase 3 fixes.
Bundle exercised in the lab: sha256 `e951ffb1a518696e10a2ccda6e047f2042aeb92f3e6f6109f7ad4ae24b789dc4`.

## Environment

- Host E1: development machine, dedicated venv (Python 3.13), `qdrant-client 1.17.1`.
- Host E2: ThinkPad, dedicated Compose project, own network/volumes/ports, no host
  port publishing (the network is `internal: true`), plus a local fake server for
  the LLM (`/api/generate`) and embeddings (`/embeddings`).
- The BM25 model (`Qdrant/bm25`) was pre-fetched on the host and mounted read-only
  into the worker, so the container never needed egress. Container egress: zero
  mentions of any external destination in all four containers' logs.

## Static and offline gates

| gate | result |
|---|---|
| `python scripts/test_offline.py` | exit 0 — 42 tests in `unittest discover` + 5, no failures (added later, for the Phase 2 example: 49 + 5, see `phase2-example.summary.md`) |
| `tests/test_dedup_auth.py` | 9 tests, all pass |
| `bash -n` on `setup.sh`, `setup/smoke_test.sh`, `scripts/run-script.sh` | syntax ok |
| `python -m compileall` over `scripts/`, `docker/worker/`, `setup/`, `tests/`, `icarus/` | exit 0 |
| `git diff --check` | clean |
| `docker compose config` with dummy credentials | exit 0 |
| `scripts/validate_compose.py` on the rendered file | PASS |

## Real stack scenarios

| scenario | result | evidence |
|---|---|---|
| queue ingestion | APROVADO | 5 jobs `ingested`, points carry `dense:4` |
| contradiction lowers confidence | APROVADO | 1.0 → 0.8, `contradiction_unresolved=true`, note `[CONTRADICTION HIGH]` |
| later consistent verdict does not clear it | APROVADO | `frozen=1`; confidence stayed 0.8, flag stayed true, note `[CONSISTENT frozen]` |
| two invalid answers → abstention, no mutation | APROVADO | `processed=0, unanalyzed=5`; every point hash identical to the previous state |
| invalid first answer repaired on retry | APROVADO | `json_ok_repair=5`, `unanalyzed=0` |
| batch reflection | APROVADO | `status=reflected`, derived point with `parent_ids`, `dense` vector |
| resolver preview | APROVADO | `applied=false`; payload hash unchanged |
| resolver apply | APROVADO | flag cleared, confidence and text preserved, one audit event, point count unchanged |
| persistence across restart | APROVADO | same payload hash after restarting the Qdrant container |
| authenticated dedup (BM25) | APROVADO | distinct docs → separate points; repeat → `dedup` (similarity ≈1.0), count unchanged |
| failed job is not a false success | APROVADO | invalid path → `ValueError`, reported as failure |
| checkpoint only after success | APROVADO | with the correct worker path 2/2 files marked ingested; with a wrong path the new file was **not** marked and the failure was persisted |
| no writes outside the test directories | APROVADO | worker mounts only the lab's wiki/hermes/fabric plus the read-only model cache |
| active agents untouched | APROVADO | same container IDs, ports, volumes, networks, crontab on both live hosts |

## Lab-side limits found (not product defects)

- The fake server derived every dimension from a single 32-byte digest, so with
  `EMBEDDING_DIMS=64` the dimensions past the eighth collapsed to zero and
  unrelated texts looked similar. Fixed in the harness; with independent
  dimensions the similarity between distinct documents drops as expected.
- Worker tuning knobs (`MICRO_REFLECTION_MAX_PER_HOUR`) have to be passed through
  the Compose environment; they do not reach the container from an env-file that
  is only used for interpolation. Configuring them via the compose override fixed
  a spurious `budget_exceeded`.
- Micro-reflection needs at least two neighbours per chunk; a two-point corpus is
  skipped ("too few neighbors"). A three-document corpus is the minimum.
