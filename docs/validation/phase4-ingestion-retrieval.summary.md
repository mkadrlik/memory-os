# Phase 4 — ingestion, BM25 and retrieval (synthetic corpus)

Session: 2026-09-20, acceptance session 2 (etapa 2, "Fase 4 — ingestão e
recuperação"). Runner: Hermes Principal, non-interactive, coordinated by Grok
via Cosmic-net. Only this phase was worked on.

## Revision tested

`consolidate/review-2026-09-19`, HEAD `ad2917b` (local branch, no remote, no
push). Entry revision for this session was `ea31528`; this session added
`6731d7d`, `1ce3fa8`, `ad2917b`. The only change after the tested revision is
this summary file, i.e. the installed tree under test is exactly `ad2917b`.

The installed tree was re-installed from `git archive` of `ad2917b`
(sha256 `0c814794ee926c8db044711a27a3c0df91acc8c8ebd7006c912d997f0dbb64f8`),
using the same recipe as Etapa 1 (local embedding provider), so the tree under
test is the HEAD tree.

## Environment (disposable, not the host)

QEMU/KVM guest `memory-os-acceptance` (Ubuntu 24.04), SSH only on
`127.0.0.1:2222`, host `$HOME` not mounted. Three containers:
`qdrant` v1.17.1, `redis` 7-alpine, `worker`; all published on guest loopback
only. Embedding: local Ollama `nomic-embed-text` (768 dims) — no paid provider
and no paid call in this session. Collection `knowledge_base`: named dense
(`size 768`, Cosine) and `sparse` (`modifier: idf`).

Corpus: 14 synthetic documents written under the wiki's
`raw/acceptance-fase4/`, plus a small number of direct enqueues. No real
document was modified. Raw logs stay outside the repository; nothing in this
summary contains credentials or private data.

Before the tests the collection held 1 point (from Etapa 1); after the cleanup
it held the same 1 point.

## Gates

| gate | result |
|---|---|
| `python3 scripts/test_offline.py` | exit 0 — 52 tests in `unittest discover` (49 before, +3 new) plus every standalone suite |
| `tests/test_dedup_auth.py` | 12 tests, all pass (9 before, +3 new) |
| `bash -n setup.sh setup/smoke_test.sh scripts/run-script.sh` | ok |
| `python -m compileall docker/worker scripts setup icarus tests` | exit 0 |
| `git diff --check` | clean |
| pre-write dedup endpoint (`POST /collections/.../points/search`) | HTTP 200 on qdrant v1.17.1 — the dedup search is live, not failing open into a plain insert |
| clean reinstall from the fixed tree (`setup.sh`, local provider) | exit 0 — 20 passed, 0 failed, 0 warnings, from a torn-down guest |

## Scenarios (all through the documented path)

The documented ingestion path is `scripts/wiki_continuous_ingest.py`
(hash-based scanner and checkpoint) → Redis/ARQ → `process_wiki_file` →
`tasks/file_ingestion.ingest_file`. The documented retrieval path is
`scripts/context_enhancer.py`, the module the Icarus hook loads.

| scenario | result | evidence |
|---|---|---|
| distinct documents | APROVADO | 5 distinct documents → 5 points, `dense` 768 dims, `sparse` 16–36 non-zero terms, payload with `text`, `title`, `tags`, `source=wiki-raw`, `file_path`, `word_count`, `importance_score` |
| YAML variants | APROVADO | list form; comma-separated string form; mixed types (int `42` → `"42"`, `null` dropped); no frontmatter → title = filename stem, tags = `[folder]`; malformed YAML → full text kept, tags = `[folder]` |
| duplicate document | APROVADO | byte-identical copy → dedup similarity 1.000 against the original point, no second point, watcher reported success |
| near-duplicate, different fact | REPROVADO (limit) | similarity 0.992 → merged: the differing fact is not stored and the original text is kept |
| contradiction between two documents | REPROVADO (limit) | similarity 0.955 → merged: the contradicting value is lost |
| empty file / whitespace-only file | APROVADO | `{"status": "skipped", "reason": "empty file"}`, no point; the watcher counts that as ingested |
| invalid path | APROVADO | `/etc/passwd`, a missing file under the wiki, `/wiki/../etc/passwd.md` and a deep `../../../../` traversal all fail the job with an explicit error and create no point |
| updated document | REPROVADO, then CORRIGIDO | see defect 1 |
| queue/worker confirmation | APROVADO | 13 files enqueued; the checkpoint was written only after the worker confirmed each job |
| retrieval through the real agent path | REPROVADO, then CORRIGIDO | see defect 2 |
| BM25 for real | APROVADO (after defect 2) | sparse query embedding returns 6 terms; sparse-only search ranks the matching document 25.18 vs 3.56 for the runner-up; RRF hybrid ranks it first; every probe query reports `fallback_level 0 (hybrid)` with the expected title, `source=wiki-raw`, tags and path |
| Redis unavailable | APROVADO | watcher prints "Redis indisponível: … Connection refused" then "Redis não pronto … Abortando", exit 0, no stack trace, no state change |
| Qdrant unavailable during ingestion | APROVADO | job fails (`Temporary failure in name resolution`); the checkpoint stays unmarked (`ingested_at: null`) and the failure is persisted with timestamp, file, error, `failure_class` and `retry_count` |
| embedding endpoint unavailable | APROVADO | job fails (`All connection attempts failed`), no point created |
| recovery after each outage | APROVADO | after Redis/Qdrant/Ollama came back the watcher completed, the checkpoint was written, and the point was written with status `updated`/`upserted` |
| Redis auth | APROVADO | a client without the password gets `NOAUTH Authentication required` — closes the gap recorded at the end of Etapa 1 |
| safe cleanup | APROVADO | 9 synthetic points deleted, the corpus directory removed, 14 state entries removed, collection back to the 1 pre-existing point, no leftover DLQ entry for the synthetic files |

## Defects found and fixed

All three were first reproduced on the VM; each fix was re-tested on the VM.

### 1. An edited document did not update its own content

Re-ingesting `11-atualizacao.md` after rewriting `100 unidades` → `250 unidades`
returned `{"status": "dedup", "similarity": 0.969}` and merged payload fields
into the point derived from its own previous revision. The stored `text` and the
stored vector stayed on the old revision, the watcher logged `Ingerido` and wrote
the checkpoint, so the update was never retried — and retrieval would keep
answering with the stale content.

Fix: `upsert_with_dedup` takes `source_path`; when a neighbour above the
threshold has the same `file_path`, the payload and both vectors are replaced on
that point (`status: "updated"`). Cross-document dedup keeps merging. The
watcher and `scripts/test_ingestion.py` accept the new status; 3 regression
tests added.

Re-tested: the same file rewritten to `750 unidades` → worker logged
`Update: replaced content of chunk 4c5028d2-… (score=0.968)`, the point kept the
same id, its text became `750 unidades`, and no duplicate was created.

### 2. The real agent retrieval path could not embed anything

`context_enhancer.py` with the environment the installer had written produced,
for every query:

```
[CE-ERROR] Dense embedding attempt 1/1 failed: … Failed to resolve 'host.docker.internal'
[CE-FALLBACK] Qdrant general error (Dense embedding unavailable), falling back to lexical.
[CE-FALLBACK] SQLite keyword search returned 2 results
```

Telemetry recorded `fallback_level 3 (sqlite)`: the answer came from session
history, not from the collection. Two independent causes:

1. `setup.sh` wrote the container-facing `EMBEDDING_API_BASE` (and
   `OLLAMA_BASE_URL`) into the **profile** `.env`. `host.docker.internal` is
   resolved inside the worker container by `extra_hosts`; on the host it does not
   resolve (verified: `getent hosts host.docker.internal` empty, `curl` 000,
   while `127.0.0.1:11434` answers 200). The profile `.env` now gets the
   host-reachable form; the Compose env file keeps the container-facing value.
2. `embed_query_sparse` computed `FASTEMBED_SITEPKGS` only as a local default and
   never exported it, while the child reads `os.environ["FASTEMBED_SITEPKGS"]`.
   Nothing in the installer sets that variable, so the child died with KeyError
   and an empty stdout — surfacing as a bare `Expecting value: line 1 column 1`
   from `json.loads`. BM25 query embedding therefore never worked on a
   documented install and hybrid search silently became dense-only. The resolved
   value is now exported to the child, and a non-zero exit or empty stdout
   raises with the child's own stderr.

Re-tested, both on the VM: dense 768 dims, sparse 6 terms, sparse-only search
25.18 vs 3.56, RRF hybrid first, and all four probe queries at
`fallback_level 0 (hybrid)` with the expected source/title/tags/path.

End-to-end confirmation: a clean reinstall from the fixed tree (the profile
`.env` embedding keys stripped first, as on a fresh install) produced
`EMBEDDING_API_BASE=http://127.0.0.1:11434/v1` and
`OLLAMA_BASE_URL=http://127.0.0.1:11434` in the profile `.env`, while the Compose
env file and the worker container kept `host.docker.internal` — each side with
the value that resolves for it. One document was then ingested through the
watcher and retrieved through `context_enhancer.py` **with the environment as
installed, no override**: telemetry recorded `fallback_level 0`,
`retrieval_mode hybrid`, `qdrant_latency_ms 2.47`, and the answer carried the
right title, `source=wiki-raw` and tags. Before the fix the same path recorded
`fallback_level 3` (`retrieval_mode: sqlite`) and returned session history
instead of the document.

### 3. The documented cron removal left the watcher behind

`setup/install.md` §11 documented
`crontab -l | grep -v "memory-os wiki watcher" | crontab -`. The scheduled line
does not contain that text, so it survived: the hourly watcher kept running
against a removed stack, and a later reinstall appended a second copy. The
acceptance VM was found with four identical watcher lines (one install, one
deactivation, one reinstall, plus earlier residue). The documented command now
matches both the marker and `wiki_continuous_ingest.py`, and a teardown followed
by a reinstall left exactly one watcher entry (checked: 1 marker + 1 entry).

## Limits and risks (not fixed in this round)

- **Cross-document dedup discards the second text.** Two *different* files above
  the 0.92 similarity threshold are merged and the incoming document's text is
  never stored (0.992 and 0.955 measured here). A document that differs from
  another by a single fact therefore does not become retrievable on its own.
  Documented in `setup/install.md`; the threshold is a constant in
  `docker/worker/tasks/file_ingestion.py`. This is dedup by design, but the
  failure mode is silent and worth a decision.
- **DLQ classification misses DNS/service-name failures.**
  `Temporary failure in name resolution` was classified `unknown` (the patterns
  cover "connection"/"refused"/"timeout"), so a retry tool keyed on `transient`
  would skip it.
- **The watcher logs `Ingerido` for `skipped` files.** Honest status is printed by
  the worker (`{"status": "skipped", "reason": "empty file"}`) and the watcher now
  appends the status, but the line still reads "Ingerido".
- The first BM25 query on a fresh machine loads the model in a subprocess with a
  15 s timeout, so a cold cache can lose the sparse half of the first query
  (dense-only fallback). Not observed after the model cache was populated.
- `indexed_vectors_count` stayed at 1 while `points_count` was 9 during the
  tests: the HNSW index is built lazily (`indexing_threshold: 10000`). Expected
  at this size, recorded so nobody reads it as a defect.

## Not done (next session)

- Phase 5: real Hermes with Icarus in a disposable profile, memory on/off
  comparison, error/latency/token/cost metrics. No paid call was made.
- Phase 6: consolidation, secrets sweep, final suite on the frozen HEAD, and the
  final report.
- **No readiness verdict for publication.** Phase 4 shows the ingestion and
  retrieval path working end to end after the fixes, with three defects
  corrected and three limits open. Whether the open limits block publication is
  a Phase 6 decision.

## State left on the VM for the next session

- Tree `~/memory-os` extracted from `ad2917b`
  (sha256 `0c814794ee926c8db044711a27a3c0df91acc8c8ebd7006c912d997f0dbb64f8`), a
  clean install (`setup.sh` exit 0, 20/0/0).
- Stack `memory-os-default` up: qdrant, redis (auth required), worker — all
  healthy, published on guest loopback only.
- Collection `knowledge_base` empty (0 points); `vault/wiki` empty; checkpoint
  and DLQ files empty; crontab holds one watcher entry. All synthetic data from
  this round was removed.
- The QEMU process was never restarted; only the guest's containers and files
  changed. A backup of the pre-reinstall profile `.env` is at
  `~/.hermes/.env.bak-c5` inside the guest.
