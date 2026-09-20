# Phase 6 closure — review closed, final verdict (redacted summary)

Session: 2026-09-20, acceptance session 4 (etapa 4, "fechar a revisão").
Runner: Hermes Principal, non-interactive, coordinated by Grok via Cosmic-net.
Only this stage was worked on. No GitHub action, no remote configured, no push.

## 1. Tree under test and its state

| item | value |
|---|---|
| checkout | the candidate tree on the development host (absolute path deliberately not published) |
| branch | `consolidate/review-2026-09-19` |
| HEAD | `74b9cc845cee10f7951e147574b1d2456efd521b` — *docs(validation): record the phase-5 real Hermes and utility round* |
| commits over base `e03db1f` | 37 |
| authorship preserved | 29 Claudio Drews, 6 Hermes Principal, 1 plasmaStray (PR #35), 1 Brian Doherty (PR #37) |
| working tree | clean: no modified, no untracked, no ignored-but-publishable files; no stash |
| remotes | **none**. No push, no PR, no release was performed or attempted |

Revision exercised by each round, so no result is attributed to the wrong tree:

| round | revision tested |
|---|---|
| phase 3 closure | `3b7576b` (this round added `e6066e9`…`3b7576b`) |
| phase 4 ingestion/BM25/retrieval | `ad2917b` |
| phase 5 real Hermes and utility | `74b9cc8` (fixes `4140ccb`, `aa8bf85`, `3bcf677`; record `74b9cc8`) |
| phase 6 closure (this round) | `74b9cc8` — final HEAD, re-installed and re-tested from scratch |

Tested code == committed content: the guest tree was extracted from `git archive`
of HEAD (`sha256 0301ca147597c887c5197d6f2cfd797df5b0c55965c4aeb33353dc05326009d1`,
133 archive entries, no `.git`, no caches, no databases), giving 112 files,
tree `sha256 34512eff91f625e861d62167610747943b9d21009c9eb0460adf341ab0f24a1b`.

## 2. Gates on the frozen HEAD (host)

| gate | result |
|---|---|
| `python scripts/test_offline.py` | **exit 0** — 57 tests via `unittest discover` (consolidation, dedup auth, resolution, local memory, example safety) plus the standalone suites: collapse, sanitization (24), local embeddings (5), hermes_env, installer profile/bootstrap, path containment — "All offline suites passed." |
| `bash -n setup.sh setup/smoke_test.sh scripts/run-script.sh` | ok |
| `python -m compileall docker/worker scripts setup icarus tests` | exit 0 |
| `git diff --check` / `git show --check HEAD` | clean |
| `docker compose config` on the host | not available (no daemon access for this user); the rendered file was validated inside the guest, where the stack is real (phase 4: 35/35 checks; this round: live stack) |

Secret / private-data sweep on everything publishable (all tracked files at HEAD,
plus the untracked and ignored sets):

- the authorised test key value: **0 occurrences** in the working tree and 0 in `git HEAD`;
- no development-host absolute paths (`/home/<user>/…`); the only `/home/…` strings are
  documented placeholders (`/home/your-user/…`) and test fixtures;
- no lab IPs, no personal e-mail, no private corpus tokens, no credentials in
  `.env.example`/`worker.env.example`/compose files;
- credential patterns (`sk-…`, `ghp_…`, `AKIA…`, `BEGIN … PRIVATE KEY`, `xox…`, `EAA…`):
  one hit only — the literal example `sk-or-v1-...` inside the installer's own prompt text;
- `.gitignore` verified: `.env`, `worker.env`, `qdrant-storage/`, `bin/`, `redis*/`,
  `native-staging/`, `.venv/`, `__pycache__/`, `docs/validation/*` (except the redacted
  summaries) are excluded and untracked;
- two hygiene fixes were applied to files that would be published: a development-workspace
  path removed from a validation summary, and a local profile name replaced by a generic
  wording in the preserved Hermes patch.

## 3. Clean install through the user path, at the final HEAD

The disposable QEMU/KVM guest (Ubuntu 24.04, host `$HOME` not mounted, SSH on
`127.0.0.1:2222` only) was torn down completely and re-installed from the HEAD archive.
The QEMU process was never restarted (`pid 357489` throughout); "reboot" below is a guest
reset through the QEMU monitor.

State before the install (teardown verified): 0 containers, 0 `memory-os-default_*`
volumes, no `vault/`, no `state.db`/`memory_store.db`, no Icarus plugin, cron watcher
count 0 — and the out-of-scope sentinel file still present, which proves the installer
does not touch what is outside its scope.

| step | result |
|---|---|
| `bash setup.sh` from the extracted tree | **exit 0**, `Passed: 20  Failed: 0  Warnings: 0` |
| stack | qdrant v1.17.1, redis 7-alpine, worker — all `Up (healthy)`, loopback only |
| collection | `knowledge_base` green: dense `size 768` Cosine + sparse `modifier idf` |
| Icarus | installed and enabled by the installer (plugin copy, v0.3.0) |
| installed rulebook | `SOUL.md` carries `### Step 4 — Then act`; the gating sentence ("present it as a plan") occurs **0 times** |
| schedules | exactly 1 watcher cron entry |
| profile `.env` vs Compose env | host-reachable `EMBEDDING_API_BASE=http://127.0.0.1:11434/v1` in the profile, container-facing `host.docker.internal` in the Compose file — the phase-4 split still holds at HEAD |
| manual interventions needed | none |

**Second installer run (idempotency), same revision:** `setup.sh` exit 0, 20/0/0; the
before/after state capture (setup-hash, tree-hash, plugin directories, cron lines, `.env`
and Compose-env keys and duplicates, containers, volumes, collections, points, Redis size,
DB hashes, vault files, rulebook) differenced **empty**; log markers confirm the
idempotent paths (`Wiki watcher cron already installed`,
`Mandatory Pre-Action Protocol already in SOUL.md`). No duplicate volume, container,
collection or cron line.

## 4. Reboot, persistence, and a new memory operation

A memory operation was performed **before** the reset through the documented path
(watcher → ARQ → worker → Qdrant): file `acceptance-c6/00-reboot.md`, job `974a6233`,
status `upserted`; point `c115bfb6…` with `source=wiki-raw`, resolved tags and the
marker string in its stored text.

After the guest reset:

- docker daemon active; all three containers carry `restart=unless-stopped` and came back
  `Up (healthy)` **without any intervention**;
- collection green with the same point present (text and metadata intact);
- the Redis marker written before the reset was still readable;
- `state.db` and `memory_store.db` `sha256` identical before and after
  (`4b41d371…`, `06b88003…`) — byte-for-byte persistence;
- plugin, single cron entry and the installed rulebook unchanged.

New memory operation after the reboot: file `acceptance-c6/01-posboot.md` ingested through
the watcher (job `03f83e05`, `upserted`) → 2 points in the collection.
Retrieval through the **real documented path** (`scripts/context_enhancer.py`, run with the
environment as installed, no override): `retrieval_mode hybrid`, `fallback_level 0`,
`qdrant_latency_ms 2.48`, both documents returned with title, tags and `source=wiki-raw`,
end-to-end in 0.6 s. Repeat-run dedup: second watcher pass reported
`Nada novo. 2 arquivos rastreados, 2 inalterados` — no third point, no duplicate.

## 5. Real Hermes and Icarus at the final HEAD (authorised paid provider)

Hermes Agent **v0.21.3 (2026.9.14)**, upstream `c1488ac9`, installed only inside the
disposable VM; plugin installed by the installer, hooks fired by the Hermes runtime
(the recall block is produced by `hermes_plugins.icarus.hooks`, not by a direct call).
Two synthetic, single-turn sessions with distinct new tokens, same model, new sessions,
no expected answer in the prompt:

| session | result |
|---|---|
| capture (`TUCANO-2026` / port `7712`) | rc 0, 41.2 s — a fabric entry was written (`hermes-note-veleiro-…tucano…md`) containing the value; the capture ledger moved `processed` 2 → 3 |
| probe, new session, shipped toolset | rc 0, 34.1 s — answered `TUCANO-2026` / `7712` and named the injected `[fabric]` block of that timestamp as its source |

The three phase-5 fixes hold at the committed HEAD: no truncation warning named
`ICARUS_EXTRACTION_MAX_TOKENS`, no `budget_reached` misfiled as a failure, and the
non-gating rulebook answered instead of stopping at a plan.

Paid ceiling: the authorised test key went from `usage 0.063074353` to `0.067110631` —
**US$ 0.004036** for this round, US$ 0.0671 accumulated across the whole acceptance,
against the US$ 5 ceiling (`limit_remaining 9.9329` of a US$ 10 limit). The key was
copied into the guest over SSH only, never printed, removed from the profile `.env` and
shredded at the end of the round. All corpora were synthetic.

## 6. Defects corrected in this round

1. **The documented data removal did not remove the capture ledger.**
   `setup/install.md`'s "remove the data too" block removed `vault/`, `state.db` and
   `memory_store.db` but not `~/.hermes/icarus-capture.sqlite3`. Observed on the VM: after
   a full teardown and a fresh install, the ledger still held the previous round's rows
   (`empty_or_unavailable` ×7, `processed` ×2). Consequence for a user following the
   documented procedure: the daily automatic-capture budget and the "already captured this
   turn" ledger survive a data wipe, so a reinstalled agent can start the day already at
   its cap. Fixed in `setup/install.md` (the ledger and the ingest checkpoint/DLQ files are
   now listed in the removal block, with the reason).
2. **Two published files carried avoidable local identifiers** (a development-workspace
   path, a local profile name). Reworded generically; no behavioural content changed.

Method note (harness, not product): a QEMU hard reset issued without flushing the guest
loses writes still in the guest page cache. In this round one synthetic Markdown body
(`00-reboot.md`) came back as a 0-byte file and one state-capture file was emptied. The
product behaved correctly in both cases — the worker reported the 0-byte file as
`skipped / empty file` instead of indexing it, and the capture file was regenerated. The
Qdrant point, the Redis key and both SQLite databases had already been persisted and
survived. Reboot/persistence evidence is therefore taken from the point, the Redis key and
the two DB hashes, not from the affected file.

Observation (model behaviour, not plumbing): one paid answer came back in Chinese for a
Portuguese prompt; the following session answered in Portuguese. Language stability of the
configured agent model is a quality item, outside this candidate's scope.

## 7. Criteria — approved, reproved, blocked

Approved with evidence (gate executed at the revision in the table of §1):

- **Phase 3 (F1–F9)**: dedup auth sent from every direct Qdrant call, fail-loud on
  401/403, no-key mode preserved, sweep of the other call sites, requirement ranges
  aligned with the running Qdrant 1.17.1, Redis healthcheck without the password, worker
  healthcheck still authenticated, tests not assuming a live `~/.hermes`/host paths/fixed
  dimensions, docs consistent with evidence.
- **Phase 4**: offline suite green, dedup unit coverage (12 tests), rendered Compose
  checks, real Redis + Qdrant + worker with a local fake provider, deterministic
  scenarios (queue, checkpoint after success, contradiction lowers confidence, repair,
  abstention, resolver preview/apply, persistence).
- **Phase 5**: clean install through the documented path (this round again at HEAD),
  second installer run idempotent, reboot with services returning and data persisting, a
  new memory operation working, documented disable/remove procedures.
- **Phase 6**: real BM25 and the documented ingestion path; synthetic corpus with
  distinct/duplicate/near-duplicate/typed-YAML/updated/empty/invalid cases; dense and
  sparse vectors; authenticated dedup; distinct documents preserved; checkpoint only after
  success; explicit failure on invalid path; retrieval through the real path; Redis/Qdrant/
  embedding outages and recovery.
- **Phase 7**: Hermes version identified and installed only in the VM; disposable profiles;
  Icarus through the documented path; capture and recall demonstrated with the paid
  provider; opt-out and leakage thresholds met; no duplicated turns; latency with no
  material regression; backend down degrading honestly.
- **Phase 8/9/10**: main path documented; reproducible synthetic example; README,
  QUICKSTART, install guide and CONSOLIDATION reconciled with the evidence in this round;
  full diff and untracked set reviewed; secret/PII sweep clean; local commits with imported
  credits preserved; final HEAD recorded; offline suite re-run on that HEAD; no remote, no
  push.

Reproved / declared thresholds not met (state them, do not hide them):

- **Memory-dependent improvement ≥ 60 % (ON) and ≥ 40 pp over OFF**: **not met** in the
  uncontaminated comparison — in the minimal harness ON recalled 0/5 obscure values after
  the fixes and OFF 0/6; the full-toolset arm reached 2/2, but its OFF baseline is
  contaminated (the "no memory" profile can read the other profile's files inside the same
  `HOME`). Cause is understood and is a design property, not a plumbing failure: the
  automatic `[fabric]` injection carries only the entry's 80-character summary, while the
  values sit in the body; the `[sessions]` layer carries ~200 characters. Real utility came
  from the session-history layer with the shipped toolset.
- **Cross-document dedup discards the second text** (similarity 0.955/0.992 merged): a
  document differing from another by a single fact does not become retrievable on its own.
  Documented; the threshold is a constant in `docker/worker/tasks/file_ingestion.py`.
- **DLQ classification misses DNS/service-name failures**; the watcher still prints
  "Ingerido" for `skipped` files; the first BM25 query on a cold cache can lose the sparse
  half within its 15 s subprocess timeout. All documented as limits.

Blocked / not covered, with the reason:

- **The literal advertised one-command install** (`curl -sSL …/main/setup.sh | bash`) was
  **not** exercised: it fetches the installer from the published `main` branch, which does
  not carry this candidate, and configuring a remote or pushing is forbidden by the
  standing rules. The candidate tree's documented installer path was exercised instead
  (four clean installs across the acceptance, one of them at the final HEAD). The README
  claim is now stated with exactly this scope.
- **Docker image build with an empty layer cache** was not repeated in this round; the
  images had been built in the same disposable VM during the earlier rounds.
- **Concurrent sessions inside one process** (gateway/Telegram) were not exercised: a CLI
  session is its own process. Interleaved sessions and profile isolation were.
- **Semantic degradation with a populated knowledge base while Qdrant is down** was not
  measured; the collection was empty in the arm that tested degradation.
- **Semantic (embedding) deduplication** is not claimed: content dedup is exact and the
  token-overlap similarity is explicitly a heuristic.

## 8. Verdict

**The consolidation candidate passes every gate that was executed, including the clean
install at the final HEAD, the second installer run, the reboot with persistence, the
BM25/ingestion/dedup/retrieval round and the real Hermes + Icarus integration with the paid
provider — and it is not declared ready for publication.**

The two reasons are evidence-backed, not cautious wording:

1. one pre-declared acceptance threshold — *memory-dependent improvement* — is **not met**
   in the only uncontaminated comparison available, and the arm that did meet it has a
   contaminated baseline. Publishing now would advertise memory utility that the evidence
   does not support; the `[fabric]` summary-only injection is a design decision that has to
   be taken (inject the body, or re-declare the threshold) before that claim is made;
2. the main installation form advertised in the README (the remote one-command installer)
   has never been executed, in this acceptance or before it, and cannot be executed without
   publishing first.

Everything else the acceptance set out to prove is demonstrated at the final HEAD, with
reproducible commands, and the local commit history preserves authorship of the imported
contributions. Nothing was pushed, no PR or release was opened, no active agent profile,
memory or service was touched, and the mutable state of this round lived only inside the
disposable VM.

## 9. Artifacts

Raw logs and candidate archives stay outside the repository (as
`docs/validation/.gitignore` requires): the HEAD archive and manifest, `c6.out`,
`c7.out`, `c8.out`, `c9.out`, `install-c6.log`, `install-c7.log`, the state captures and
the case prompts live under the acceptance workspace on the host and inside the guest.
Committed here: this summary, the installer data-removal fix, and the documentation
reconciliation of the round.
