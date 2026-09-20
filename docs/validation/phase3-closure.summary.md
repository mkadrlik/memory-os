# Phase 3 closure — install, operational checks, deactivation, reboot

Session: 2026-09-20, acceptance session 1 (etapa 1, "fechar a Fase 3").
Runner: Hermes Principal, non-interactive, coordinated by Grok via Cosmic-net.

## Revision tested

`consolidate/review-2026-09-19`, HEAD `3b7576b` (local branch, no remote).

The revision range exercised by this round is `07d26ff..3b7576b`. Commits
`959261b` (enable Icarus, point compose `ps` at `docker/`) and everything before
it came from the earlier session/other agent; this round added `e6066e9`,
`2b2cc41`, `bb7a8a9`, `c20733b`, `3b7576b`.

The **installed tree was verified byte-identical** to the HEAD tree: every
tracked file hashed on the host (`git archive` export) and inside the guest
(`find`, `__pycache__` excluded) — 109 files, identical set and content.

## Environment (disposable, not the host)

QEMU/KVM guest `memory-os-acceptance` (Ubuntu 24.04 cloud image), SSH only on
loopback `127.0.0.1:2222`, host `$HOME` not mounted. Local embedding provider
(Ollama `nomic-embed-text`, 768 dims); no paid provider used. The QEMU process
was never restarted — "reboot" below means a guest reset via the QEMU monitor.

## 1. Clean install and exact revision

Two clean installs were run from a **copied tree without `.git`** (the only way
to install a local branch that has no remote):

| Run | Result |
|---|---|
| `c20733b` | `setup.sh` exit 0 — 20 passed, 0 failed, 0 warnings |
| `3b7576b` (final) | `setup.sh` exit 0 — 20 passed, 0 failed, 0 warnings |

Each run started from a torn-down guest (no containers, no named volumes, no
`vault/`, no `state.db`/`memory_store.db`, no plugin, no cron entry) and
re-extracted the candidate archive, i.e. the documented "clone or copy the
repository, then run `setup.sh`" path.

## 2. Services, auth, volumes, permissions, Icarus, watcher, verification command

- Stack healthy: `qdrant` (v1.17.1), `redis` (7-alpine), `worker` — all
  `Up (healthy)`. Published on loopback only (`127.0.0.1:6333`, `:6379`).
- **Redis requires auth.** `CONFIG GET requirepass` returns `NOAUTH
  Authentication required.` without credentials. Note for future runs: the
  redis container exports `REDISCLI_AUTH`, so a bare `docker exec … redis-cli
  ping` succeeds *because it authenticates automatically* — it does not prove
  auth is off. Neutralise it (`docker exec -e REDISCLI_AUTH= …`) to test.
- **Qdrant runs without auth** in the default install: `QDRANT_API_KEY` is empty
  in the Compose env, the compose entrypoint unsets the variable, and the port
  is loopback-only. This is by design but was undocumented; now documented.
- Volumes: two named volumes (`memory-os-default_qdrant_data`,
  `memory-os-default_redis_data`).
- Permissions: `vault/` 775, `vault/wiki/` and `vault/fabric/` 755, owned by the
  installer user (the worker mounts `/fabric` read-write and `/wiki` read-only).
- Icarus: `enabled`, v0.3.0, source `user` (`plugins.enabled: [icarus]` in
  `config.yaml`). Verified with both `hermes plugins list` and `plugins show`.
- Watcher: exactly one crontab entry, hourly
  (`# memory-os wiki watcher` → `scripts/wiki_continuous_ingest.py`).
- Documented Compose verification command
  (`docker compose -f docker/docker-compose.yml --env-file
  ~/.hermes/memory-os-compose.env -p memory-os-default ps`) runs and lists the
  three services.

## 3. Second installer run — idempotency

A second `setup.sh` run on the **same revision**, with state captured before and
after:

- before/after diff: **empty** (no observable change)
- `Passed: 20, Failed: 0, Warnings: 0`
- **no nested plugin**: exactly one `~/.hermes/plugins/icarus` directory
- cron: exactly 1 watcher line; Compose env: 17 keys, 0 duplicates; Hermes
  `.env`: 0 duplicate keys
- 3 containers, 2 volumes, 1 collection — no duplicates
- log markers confirm the idempotent paths (`Repo already exists`, `Wiki watcher
  cron already installed`, `Mandatory Pre-Action Protocol already in SOUL.md`)

## 4. Deactivation, per the documented procedure

All three documented variants (section 11 of `setup/install.md`) were executed
**from the repository root** with out-of-scope sentinels in place (an unrelated
Docker volume, an unrelated directory, a file). Result: **26 checks, 0
failures.**

- *Stop the stack, keep the data*: containers gone, both named volumes, `vault/`
  and both SQLite DBs intact; stack restarts cleanly.
- *Remove the software, keep the data*: plugin, context enhancer and cron entry
  removed; volumes, `vault/`, DBs, unrelated data intact.
- *Remove the data too*: `down -v` removed only the two `memory-os-default_*`
  volumes; `vault/` and both DBs removed; **the unrelated volume, the unrelated
  directory and the file all survived**.

## 5. Guest reboot — services, persisted data, new memory operation

Guest reset via the QEMU monitor (`system_reset`; the QEMU process itself was
not restarted), performed twice:

- after the first reset: all three containers `Up (healthy)`; the Redis key
  written before the reboot was still present; the `knowledge_base` collection
  survived; both SQLite DB hashes unchanged.
- new memory operation after the reboot: a Markdown file placed in
  `vault/wiki/raw/` was ingested through the documented watcher
  (`wiki_continuous_ingest.py`) → ARQ job → worker → dense (768 dims) + sparse
  (BM25) upsert into `knowledge_base`. Job status `upserted`, payload carries
  the resolved `file_path`.
- after the second reset: the ingested point was still there (and the Redis
  probe again). Repeat-run dedup returned `dedup` instead of creating a
  duplicate point.

## Defects found and fixed in this round

All were demonstrated on the guest, not inferred from reading code. Seven
commits, `e6066e9`..`3b7576b`:

1. `setup.sh` Phase 1 required `${REPO_DIR}/.git`. A copied or archive-extracted
   tree fell through to `git clone` over a non-empty directory and aborted with
   `fatal: destination path ... already exists` (exit 128 under
   `set -euo pipefail`) — the install never started. Now a tree carrying
   `docker/docker-compose.yml` is accepted, and a non-checkout directory fails
   with an explicit message. Regression tests cover four cases (clone, copied
   tree, junk directory, absent directory) with a stubbed `git`.
2. `setup/install.md` section 11 documented `docker compose … down` without
   `-f docker/docker-compose.yml`; from the repository root it fails with `no
   configuration file provided: not found`, so the documented deactivation never
   ran. Fixed and cross-checked against the corrected QUICKSTART form.
3. The documented local-embedding path was unreachable: Ollama listens on
   `127.0.0.1` only, so the worker (whose `127.0.0.1` is itself) got
   `Connection refused` (111) and ingestion silently upserted nothing. Documented
   `OLLAMA_HOST` and how to verify reachability from the worker; a troubleshooting
   entry added. In the guest this was fixed with a systemd override, after which
   dense (768) + sparse (BM25) ingestion worked.
4. `QDRANT_API_KEY` is honoured by the compose file but appeared in neither
   `.env.example` nor `install.md`. Documented (empty by default, loopback-only).
5. `setup/smoke_test.sh` ran its checks with `pipefail` on, so
   `producer | grep -q` raced: `grep -q` exits at the first match, the producer
   dies on SIGPIPE (141) and `pipefail` reported a failure on a correct install
   (`hermes plugins show icarus | grep -q 'Status: enabled'` passed by hand and
   failed inside the script). `pipefail` is now disabled for the duration of
   each check.
6. `setup/smoke_test.sh` read a *stored vector* for its embedding check, which
   cannot pass on a fresh install (empty collection, and the ingestion test
   cleans up after itself). It now asks the configured endpoint for a vector,
   and both tools pick their settings up from the files `setup.sh` writes
   (`memory-os-compose.env`, then the profile `.env`), explicit env winning.
   Without that the Redis check failed with `NOAUTH` although the password is in
   the Compose env file.
7. `scripts/test_ingestion.py` (a) called `ArqRedis.get_job_result`, removed in
   arq 0.28 (now `_get_job_result`), so it died with `AttributeError` before
   verifying anything; (b) step 5 ("invalid path must be rejected") raised
   through `fail()`/`SystemExit` when the job failed, i.e. it aborted exactly
   when the guard worked; (c) defaulted `EMBEDDING_DIMS` to 4096 and the wiki to
   `<repo>/docker/wiki`, reporting failures on a correct installer-created
   stack; (d) could orphan the point the worker created after a crash, poisoning
   the next run through dedup. All four fixed.

## Verification after the fixes (final revision `3b7576b`)

| Check | Result |
|---|---|
| clean install from a copied tree | exit 0 — 20 passed, 0 failed, 0 warnings |
| installed tree vs HEAD | identical, 109 files |
| `python3 scripts/test_offline.py` | exit 0 — 49 tests via `unittest discover` + all standalone suites (collapse, sanitize 24, local embeddings 5, hermes_env, setup.sh profile incl. the 4 new bootstrap cases, path containment) |
| `bash setup/smoke_test.sh`, nothing exported | 9 passed, 0 failed (exit 0) |
| `python3 scripts/test_ingestion.py`, standalone | exit 0 — upsert, 768 dense, sparse BM25, payload, dedup, invalid-path rejection |
| `python3 scripts/validate_compose.py` on the rendered file | PASS (35 checks) |
| `bash -n` on `setup.sh`, `setup/smoke_test.sh`, `scripts/run-script.sh` | ok |
| guest reboot ×2 | services healthy, data persisted, new memory operation succeeded |

## Not done / residual risk

- **Phases 4–6 were not started** (BM25 on a synthetic corpus, metadata /
  dedup / update / contradiction, recall through the real path, failure and
  recovery, Icarus in a disposable Hermes profile, cross-session cases, the
  usefulness comparison with memory on/off, error/latency/token/cost metrics,
  final consolidation and the publication verdict). No readiness verdict is
  given here.
- **No paid call was made.** The authorised test key was not read or used, so
  no spend was incurred and no comparison against a paid provider exists.
- The Ollama bind-address change was applied **inside the disposable guest**
  (systemd override listing `0.0.0.0:11434`). On a real host that step needs
  `sudo`; it is documented, not automated.
- The Docker daemon creates a missing bind-mount source as `root`. If a user
  removes `vault/` and then starts the stack again, the documented
  `rm -rf ~/.hermes/vault` fails for their user. Documented as a troubleshooting
  entry; not changed in code. It did not occur when the documented order was
  followed (down first, then remove).
- Redis has no authentication test *suite* — only the manual checks recorded
  above. `smoke_test.sh` covers reachability with the password, not that an
  unauthenticated client is refused.

## Artifacts (outside git, on the host)

Raw logs and the candidate archives stay out of the repository (as
`docs/validation/.gitignore` requires): candidate tarballs and manifests,
`install-run*.log`, `install-clean.log`, `c1.out`, `c4.out`, `c3-before/after`
under `~/Work/memory-os-vm-acceptance/`.
