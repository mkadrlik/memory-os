# Phase 5 — real Hermes and memory utility (redacted summary)

Etapa 3 of the acceptance run. Environment: disposable QEMU guest (Ubuntu 24.04),
host `HOME` not mounted, SSH only on `127.0.0.1:2222`. No active agent profile,
session, plugin or data was used, and no active project was touched. The qemu
process was never restarted.

## 1. What was identified

| item | value |
|---|---|
| Hermes Agent | v0.21.3 (2026.9.14), upstream `c1488ac9`, install method `git`, Python 3.12.3 |
| profile under test | the VM's disposable profile (`~/.hermes`), no real provider key |
| second profile | `p5off` — created for the comparison; **no** Icarus plugin, **no** Memory OS env |
| Icarus | v0.3.0, copied from `icarus/` of the candidate tree, enabled with `hermes plugins enable icarus` |
| hooks | fired by the Hermes runtime, not by direct calls: `hermes_plugins.icarus.hooks: ICARUS_RECALL: fabric=N qdrant=N sessions=N facts=N` |
| provider, free arm | local Ollama in the guest (`qwen2.5:1.5b-instruct`, 64K window via `OLLAMA_CONTEXT_LENGTH` + `model.ollama_num_ctx`; `nomic-embed-text`, 768 dims) |
| provider, paid arm | OpenRouter with Cláudio's test key: agent `deepseek/deepseek-v4.1-flash`, extraction `deepseek/deepseek-v4-flash` (the documented default) |
| paid spend | **US$ 0.063** of the US$ 5 ceiling (key `usage` before 0 → after 0.06307; `limit_remaining` 9.9369) |

Case set and thresholds were written **before** the runs (`p5/phase5-cases.md`):
17 synthetic cases covering the a–j scenarios, obscure tokens (values no model can
know a priori), opt-out, contradiction, interleaving, degradation and restart.

## 2. Defects demonstrated and fixed in this phase

### 2.1 The installed rulebook makes the agent stop and ask for authorization

`modifications/execution-agent-protocol.md` (the file `setup.sh` appends to
`SOUL.md`) shipped `Step 4 — Gate: plane, present, wait`: any response needing
more than one tool call had to be presented as a plan and wait for explicit user
authorization — "no exceptions for triviality", and it applied "even when injected
memory provides clear answers". The sibling copy of the same protocol,
`modifications/soul-rulebook.md`, already carried the corrected
`Step 4 — Then act`, so the tree contradicted itself and the installer used the
gating copy.

Observed with the real provider (non-interactive, no user to authorize): four of
six recall probes never answered at all — e.g. *"Per the pre-action gate, here's
the plan before I execute anything"*, *"Two tool calls would be needed here…
here's the plan"* — and the capture session refused to record the user's own
stated values, calling them unverifiable.

Fix: `Step 4` is now `Then act` (Steps 1–3, then act; no confirmation gate).
Re-tested in the VM with `SOUL.md` regenerated from the fixed file: the same
probes answer directly instead of stopping at a plan.

### 2.2 Automatic capture silently wrote nothing (extraction budget)

`icarus/hooks.py` defaulted `ICARUS_EXTRACTION_MAX_TOKENS` to 1024 while
`setup/install.md` marks 4096 as "strongly recommended". The documented default
extraction model is a reasoning model and **reasoning tokens count against that
budget**, so the JSON reply is cut off mid-array (`finish_reason=length`), the
parse fails, and the turn is recorded as `empty_or_unavailable` with nothing
written. The caller only saw `LLM extraction returned non-list: <class 'NoneType'>`.

Controlled repetition on one real transcript (agent `deepseek/deepseek-v4.1-flash`,
extraction `deepseek/deepseek-v4-flash`, 4 runs each):

| `ICARUS_EXTRACTION_MAX_TOKENS` | runs yielding entries | failures |
|---|---|---|
| 1024 (old default) | 2 / 4 | 2 — `finish_reason=length`, truncated JSON |
| 4096 (documented value) | 4 / 4 | 0 |

Whole experiment cost US$ 0.00093. In the same window, 7 of 9 paid turns ended
`empty_or_unavailable`. After the default was raised, one capture turn produced a
stored entry whose summary carries the identifier, and the probes that followed
had memory to consume.

Fixes: default raised to 4096 (matching the installer's own recommendation), plus
two diagnostics that were missing — a truncation warning naming
`ICARUS_EXTRACTION_MAX_TOKENS`, and a warning when a reply parses as JSON but
every entry is rejected by validation (a different JSON shape was silently
filtered before).

### 2.3 A deliberate capture stop was reported as a failure

`icarus/lifecycle.py` mapped every capture exception to `status='failed'` and
logged only the exception *type*. The daily automatic-capture budget
(`ICARUS_MAX_DAILY_ENTRIES`, default 12, undocumented) therefore appeared as
`Icarus capture failed: RuntimeError` — indistinguishable from a transient error,
while every later turn that day was also dropped.

Observed in the VM (budget already spent): five turns `failed`.
Fix + re-test on the same state: the turn is now recorded as `budget_reached` and
the log says *"Icarus capture skipped: Daily automatic capture budget reached —
turn recorded as budget_reached; raise ICARUS_MAX_DAILY_ENTRIES (default 12) to
capture more today"*.

### 2.4 Documentation: the local-provider path captures nothing

With a local embedding endpoint and no provider key — the recipe documented in
`install.md` §5 and advertised by QUICKSTART as "no API key is required" — Icarus
cannot extract anything, because `ICARUS_ENDPOINT` / `ICARUS_API_KEY_ENV` were
undocumented and the code falls back to OpenRouter/DeepSeek keys only.

Observed (case `LGAP`, 747-character answer listing three explicit values): zero
new entries in `FABRIC_DIR` (6 before, 6 after), log line *"icarus: no LLM API key
found (checked ICARUS_API_KEY_ENV, DEEPSEEK_API_KEY, OPENROUTER_API_KEY) —
skipping LLM extraction"*, turn recorded `empty_or_unavailable`. The identical
case with `ICARUS_ENDPOINT` pointed at the local endpoint did store the entry.

Fix: `install.md` §5 documents both variables with the Ollama example and the
consequence of omitting them, plus `ICARUS_MAX_DAILY_ENTRIES`; QUICKSTART's claim
is corrected to distinguish embeddings from session extraction.

## 3. Measurements (paid arm, same model, new sessions, no expected answer in the prompt)

Scoring: the expected token counts as recalled only if it appears (accents and
punctuation normalised, one-character tolerance inside the token). "minimal
harness" = `-t safe` (no file/terminal/session tools, so the answer can only come
from injected context); "full toolset" = the shipped configuration.

| arm | what it is | obscure-value probes recalled |
|---|---|---|
| PON | paid ON, before the fixes (gating rulebook) | 1 / 5 |
| PON2 | paid ON, rulebook fixed, extraction still truncated | 0 / 6 |
| PON4 | paid ON, all fixes, entry stored **with the value** | 0 / 5 |
| POFF, POFF2, POFF3 | paid OFF (`p5off`), minimal harness | 0 / 6, 0 / 6, 0 / 4 |
| PON5 | paid ON, **full toolset** | 2 / 2 |
| POFF5 | paid OFF, full toolset | 1 / 1 — contaminated, see §5 |
| LON / LOFF | free local model (`qwen2.5:1.5b`), ON / OFF | 3 / 5, 0 / 2 |

Latency / tokens / cost (paid, per session, from `--usage-file`… see
`session_model_usage`): ON sessions 8–43 s and 0.0003–0.0019 US$ per session;
OFF sessions 3–43 s at 0 US$ (no provider call). The injection itself is
sub-millisecond (measured in the phase-4 round: 2.47 ms end-to-end retrieval).

Why the minimal-harness ON arm does not beat OFF: the automatic `[fabric]`
injection carries only the entry's **80-character one-line summary**. In PON4 the
stored entry's summary was *"Registrar config de homologação do Veleiro: pacote,
porta, token"* — the field names, not the values — so the agent correctly reported
*"it doesn't contain the values"*. The `[sessions]` layer injects a ~200-character
snippet, which in these runs did not include the value either. With the shipped
toolset the agent can (and did) call the session-history search and recover the
value, and it explicitly reported that the fabric had none. That is a design
property of the injection layer, not a plumbing failure, and it is left for
phase 6 as Grok directed.

## 4. Pre-declared thresholds — verdicts

| threshold | verdict |
|---|---|
| no forbidden token in the opt-out probe and in the irrelevant question | **met** — `5551-0000` never appears in any arm; nothing stored under that string anywhere in `FABRIC_DIR`; the irrelevant-question answer never carries a project token |
| ≤ 1 capture failure per 10 substantive turns | **not met before the fixes** (5 `failed` + 7 `empty_or_unavailable` across ~20 paid turns); after the fixes a capture turn was `processed` 1/1 |
| 0 duplicated turns, ≤ 2 entries per session | **met** — 0 duplicate `(session,turn)` rows in every run; never more than 2 entries per turn |
| ON ≥ 60 % and ON − OFF ≥ 40 pp on obscure-value probes | **not met in the minimal harness** (ON 0/5 after the fixes, OFF 0/6); reached with the full toolset but the OFF baseline there cannot be measured cleanly (§5) |
| no material latency regression | **met** — ON and OFF wall-clock ranges overlap (8–43 s vs 3–43 s); the memory path adds no measurable per-turn cost |
| backend down degrades honestly | **met** — with the embedding backend stopped, the `[memory-retrieval-warning]` block is injected and the agent names it in its inventory before answering instead of claiming full context |

## 5. Limits and what is not covered

- The full-toolset OFF baseline is contaminated: with terminal/file tools the
  "no memory" profile can still read the ON profile's files inside the same
  `HOME`, and it did (it quoted the identifier and dismissed it as unconfirmed).
  A clean full-toolset comparison needs the two profiles in separate `HOME`s.
- Concurrent sessions inside one process (gateway/Telegram) were not exercised:
  each CLI session is its own process, so per-session globals cannot interleave.
  Interleaved alternation across sessions (`s8`/`s9`/`s10`) and profile isolation
  (`p5off` never saw the default profile's memory) were exercised.
- Contrasted with a capable model, the free local model (`qwen2.5:1.5b`) both
  ignored the rulebook and ignored injected context unpredictably: it answered
  with a wrong identifier, emitted JSON where prose was expected and got 17×23
  wrong. Its numbers are kept only as evidence that the plumbing works offline.
- Semantic degradation with a **populated** knowledge base (Qdrant down, fallback
  returning rows) still belongs to phase 6; in this round the collection was empty.
- The `[fabric]` summary-only injection, the capture of "not found" notes from
  probe sessions, and dedup thresholds remain design questions for phase 6.
- All corpora were synthetic. No real profile data, no credentials and no keys
  entered the repository, the summaries or the network board; the test key was
  copied into the guest only over the SSH channel, mode 0600, and never printed.

## 6. Artifacts

Raw runs, snapshots and the case set stay outside the repository
(`~/p5/runs`, `~/p5/cases`, `~/p5/evidence` inside the guest; host copies under
the acceptance workspace). Committed here: the four fixes above and this summary.
