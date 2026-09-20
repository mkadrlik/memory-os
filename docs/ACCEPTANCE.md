# Acceptance plan — consolidated candidate

This document is the acceptance plan for taking the consolidation candidate to a
publishable state. It is created by the acceptance run itself and is part of the
candidate: every criterion below must be either **demonstrated with a command**
or explicitly marked as **blocked / not covered**, with the reason.

Do not weaken, remove or stub the component under test to obtain a green result.
Mocks are allowed in deterministic tests but must be identified and complemented by
the corresponding real integration.

## 1. Tree under test (identified)

| item | value |
|---|---|
| checkout | the candidate tree on the development host (local absolute path deliberately not published) |
| branch | `consolidate/review-2026-09-19` |
| base | `aee86c82` |
| issue #19 artifacts | `scripts/resolve_contradiction.py`, `tests/test_resolution.py`, guard in `tests/test_consolidation.py`, `docs/CONSOLIDATION.md`, `scripts/README.md` |
| remotes | none (publication target is Cláudio's decision) |

## 2. Main installation path (from the README)

The README advertises a **one-command install**:

```bash
curl -sSL https://raw.githubusercontent.com/ClaudioDrews/memory-os/main/setup.sh | bash
```

`QUICKSTART.md` repeats it, and `setup/install.md` is the 10-step manual fallback.

**Note for this run:** the advertised command fetches `setup.sh` from the remote
`main` branch. To exercise the *candidate*, the installer must be run from the
candidate tree (`bash setup.sh` inside the checkout). Both forms must be accounted
for in the installation test; the candidate tree is authoritative.

## 3. Environments

| # | environment | purpose | status |
|---|---|---|---|
| E1 | Omarchy + dedicated venv | syntax, offline suites, compose parsing, dedup regression | available |
| E2 | ThinkPad lab (dedicated Compose project) | real Redis/Qdrant/worker + local fake LLM/embedding server | available; stopped between uses (`stop`, never `down -v`), credentials only in the lab's own env file outside this repository |
| E3 | Disposable VM, clean OS image | clean install through the documented path, reboot, Hermes integration | **available — used** (QEMU/KVM guest; clean installs, second run, reboot and Hermes integration at `74b9cc8`) |
| E4 | Real provider (paid) | memory utility comparison | **available — used** (Cláudio's test key, US$ 5 ceiling; US$ 0.0671 spent in total) |

### E3 / E4 block — resolved during the run

This section originally recorded both environments as **blocked**: creating a VM needs
`qemu-system-x86`/`qemu-img`, which were not installed, and no usable fallback existed
(no Docker access for this user on the Omarchy; `/dev/kvm` restricted on the ThinkPad). A
container restart is still **not** an acceptable substitute for the VM reboot test.

Both blockers were cleared later in the acceptance run — a QEMU/KVM host became available
(nothing in this run used `sudo` on the host) and the paid provider was explicitly
authorised with Cláudio's test key under a US$ 5 ceiling. E3 and E4 were then exercised in
full; the per-criterion verdicts, the amounts spent and the criteria that remain unmet or
blocked are recorded in [`validation/phase6-closure.summary.md`](validation/phase6-closure.summary.md).

## 4. Acceptance criteria

> **Status (2026-09-20, review closed).** The checkboxes below are the plan's original
> skeleton, left as written so the plan is not rewritten after the fact. The per-criterion
> verdicts for the whole plan — approved, reproved or blocked, each with the revision and
> the command that produced the evidence — are recorded in
> [`validation/phase6-closure.summary.md`](validation/phase6-closure.summary.md).

### Phase 3 — fixes
- [ ] **F1 dedup auth**: `docker/worker/tasks/file_ingestion.py` sends the Qdrant key when configured.
- [ ] **F2 fail loud**: HTTP 401/403 in the dedup path produces an identifiable failure and never silently falls through to plain insert.
- [ ] **F3 no-key mode**: behaviour without a configured key is preserved when that is intentional.
- [ ] **F4 sweep**: other direct-Qdrant call sites are checked for the same defect.
- [ ] **F5 version alignment**: `requirements.txt` and `docker/worker/requirements.txt` pin a range compatible with `qdrant/qdrant:v1.17.1`; the installed version is confirmed with `importlib.metadata.version`.
- [ ] **F6 redis healthcheck**: the composed `Test` no longer interpolates the password.
- [ ] **F7 worker healthcheck**: still authenticated (regression guard).
- [ ] **F8 tests reviewed**: smoke/ingestion tests do not assume a live `~/.hermes`, host/container path differences, or hardcoded dimensions incompatible with configuration.
- [ ] **F9 docs**: validation claims match current evidence; no contradictions between README, QUICKSTART, install guide and CONSOLIDATION.

### Phase 4 — regressions and deterministic integration
- [ ] offline suite exits 0; syntax checks; compose parse with dummy credentials; whitespace check
- [ ] dedup unit coverage: header present with key; no-key allowed; 401 and 403 without upsert; successful initial insert; duplicate recognised and merged; failures reported
- [ ] rendered Compose: Redis `Test` free of the password value; worker healthcheck reads the credential at runtime; versions/paths/ports coherent; no active-agent mounts
- [ ] real Redis + Qdrant + worker with a local fake server (no host port publishing needed)
- [ ] deterministic scenarios: queue ingestion; checkpoint only after success; failed job is not a false success; contradiction lowers confidence; later consistent verdict preserves the pending conflict; invalid answer then valid repair; two invalid answers abstain without improper mutation; batch reflection; resolver preview and apply; persistence after restart; no writes outside test directories
- [ ] SQLite state initialised; missing-table / unpersisted-budget warnings are **not** treated as an integral run

### Phase 5 — clean install through the user path
- [ ] clean VM restored; candidate transferred by commit/manifest (no venv, no caches, no DBs)
- [ ] README/QUICKSTART followed as a newcomer; every manual intervention recorded
- [ ] problems fixed in product or docs, then the install repeated from clean state
- [ ] services, auth, DBs/collections, mounts/permissions, plugin in the test profile, expected schedules, no duplicates
- [ ] second installer run, if advertised as safe
- [ ] VM reboot; services return; data persists; a new memory operation works
- [ ] documented disable/remove procedure, distinguishing software removal from data deletion

### Phase 6 — wiki, BM25, dedup and recovery for real
- [ ] real BM25 model and the documented file-ingestion path
- [ ] synthetic corpus (distinct, duplicates, similar-but-different, typed YAML tags, updated doc, conflicting info, empty file, invalid path)
- [ ] ingestion through the user-facing mechanism
- [ ] content/metadata correct; dense and sparse vectors; authenticated dedup; distinct docs preserved; update handling; checkpoint only after success; auth failure without silent insert; clear error for invalid path
- [ ] corpus queried through the real retrieval path; returned info matches expected sources; irrelevant docs are not sufficient evidence
- [ ] temporary Redis/Qdrant/embedding unavailability and recovery

### Phase 7 — real Hermes and memory utility
- [ ] identified Hermes version, installed only in the VM
- [ ] disposable profiles; authorised real provider
- [ ] Icarus installed through the documented path (not replaced by direct hook calls)
- [ ] pre-defined case set (question/action, available information, expected memory, acceptable behaviour, failing condition)
- [ ] scenarios a–j (capture/recall across sessions; preference influences answer; opt-out; no duplicate capture on repeated callbacks/restart; interleaved sessions isolated; two profiles isolated; irrelevant question not polluted; conflicting info not asserted; backend down degrades honestly; memory usable after restart)
- [ ] activated/deactivated comparison: same model, new sessions, no expected answer in the prompt, no reused context
- [ ] measured: correct recall, improper recalls, source support, capture failures, duplicates, latency, calls/tokens, cost
- [ ] pre-declared thresholds (leakage, opt-out, loss/duplication, memory-dependent improvement, no material regression)
- [ ] ambiguous results investigated with controlled repetitions

### Phase 8 / 9 / 10 — product, docs, commit, delivery
- [ ] main path documented (requirements, install, provider config, first example, how to inspect what was remembered, how to opt out, conflict handling, backup/recovery/disable, costs)
- [ ] reproducible synthetic example: a decision recorded in one session and recalled in another
  - exercised on 2026-09-20 inside the lab network through the real path (enqueue → worker → Qdrant, retrieval through `context_enhancer` in a separate process): recall recovered, cleanup verified, no default target, checks scoped to the run — `docs/validation/phase2-example.summary.md`. With the lab's fake provider the ranking is arbitrary, so this demonstrates the plumbing, not semantic recall quality; the latter still needs the VM/provider phase.
- [ ] README, QUICKSTART, install guide, CONSOLIDATION, migration notes consistent
- [ ] limitations documented where appropriate; no hidden failure
- [ ] validation tests/tools preserved in the repo, no private data
- [ ] what the deterministic suite covers vs what needs a paid real test is stated
- [ ] full diff and untracked reviewed; #19 included; secret/PII scan; exclusions verified; clear local commits preserving imported credits
- [ ] final HEAD recorded; tested code == committed content; offline suite re-run; integration re-run if the install path changed
- [ ] no remote configured, no push

## 5. Redacted results

Redacted results live in `docs/validation/`. Raw logs, credentials, databases,
caches and models **must not** be committed; `docs/validation/.gitignore` keeps
them out.
