# Phase 2 — synthetic example made safe, and the defects found doing it (redacted)

Run: 2026-09-20, after the Phase 3/4 acceptance run. Tree: `consolidate/review-2026-09-19`.
Subject under review: `scripts/demo_decision_recall.py`, until then untracked.

No credential, key, database, cache, model or raw log is reproduced here. The
lab's keys stayed in the lab's own environment file, outside the repository.

## Defects found in the example, and what changed

| # | defect | consequence | fix |
|---|---|---|---|
| 1 | the target was read from variables with production defaults (`http://localhost:6333`, `localhost:6379`, `knowledge_base`) | running it by accident on a machine with a live installation would enqueue demo memories into that installation | no default target at all: `QDRANT_URL`, `REDIS_HOST`, `EMBEDDING_API_BASE` and a collection name must be explicit, otherwise exit `2` listing what is missing, before anything is enqueued |
| 2 | expectations matched a substring anywhere in the result set | a point from an earlier run, or an unrelated corpus document, could satisfy or break a check; the first lab execution failed on a leftover `id=472a18` from a previous session | every check is scoped to the points tagged `id=<run id>` of the current run |
| 3 | the negative control asserted "no relevant marker in the results" | the hybrid query fuses with RRF and never applies the cosine threshold, so it always returns `top_k`; on any populated collection the check was a guaranteed false failure | it now judges the **injection window** (top 3, what the agent puts in the prompt) and prints `NAO CONCLUSIVO` when the collection is not larger than the window |
| 4 | cleanup was prose ("filter by the tag") | no reproducible way to remove what the run created | `--cleanup --cleanup-id <id>` deletes only the tagged points, counts before and after, and exits non-zero if any survive |
| 5 | `pool.close()` on a redis-py 5.x pool | deprecation warning in a user-facing example | `aclose()` when available |

Regression coverage added: `tests/test_demo_example_safety.py` (7 tests, offline,
no endpoint contacted). Offline suite after the change: **49 tests** in
`unittest discover` plus the 5 standalone suites, exit 0, on the dedicated venv
(Python 3.13, `qdrant-client 1.17.1`). Running `unittest discover` directly
without that runner still refuses, by design, when `MEMORY_OS_ROOT` points
outside the checkout — expected, and unrelated to this change. It asserts the refusal (exit `2`, nothing enqueued), the
partial-configuration case, the cleanup guard, both collection variable names, the
absence of any baked-in `localhost` target, and the presence of the
`NAO CONCLUSIVO` path.

## Lab evidence (E2, dedicated Compose project, fake provider, `internal: true` network)

The example ran **inside** the lab network, through the real path: enqueue in ARQ →
worker → Qdrant, then retrieval through `scripts/context_enhancer.py` in a separate
process. Counts are points in the lab collection.

| step | result |
|---|---|
| residues of earlier executions | `id=472a18` → 0; `id=56895f` → 3, removed by the documented `--cleanup` |
| baseline after residue removal | 3 points (the lab's own synthetic wiki documents) |
| ingestion through the queue | 3 jobs, all `ingested` |
| recall query 1 ("which port…") | run's decision recovered, rank 4 of 6, level `hybrid` |
| recall query 2 ("when is the weekly report…") | run's decision recovered, rank 4 of 6, level `hybrid` |
| negative control (unrelated question) | no point of this run in the 3-hit injection window |
| total after ingestion | 6 (= baseline + 3) |
| cleanup, documented path | tag count 3 → 0, total 6 → 3 |
| example exit code | 0 |

## What this does *not* establish

- The lab's fake provider returns arbitrary vectors: with it, this run's own points
  ranked 4th of 6 while unrelated documents took the first three places. The
  `APROVADO` therefore exercises the plumbing — capture, retrieval, ranking,
  cleanup — and says nothing about semantic recall quality. Judging recall quality
  needs a provider that ranks by meaning, which is the blocked paid/local-model
  phase.
- Episodic memories (the ARQ ingestion path) are stored with a dense vector only;
  the BM25/sparse vector belongs to the file-ingestion path. The sparse half of the
  hybrid query can therefore only match file-ingested content. Recorded as a
  product characteristic in the script and in `scripts/README.md`; not changed here,
  since that is a product decision beyond the acceptance criteria.
- The clean-install and reboot criteria are still untested: they need the
  disposable VM, which needs `qemu`, which is still absent from this host.
