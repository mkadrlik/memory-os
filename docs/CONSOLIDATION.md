# Consolidation candidate — 19 September 2026

This is a local review candidate based on public `e03db1f`. It has not been published or deployed. Existing agent installations and memory stores are not migration targets until the release checks below are completed.

## Provenance and review decisions

| Source | Disposition |
|---|---|
| Principal/Grok + Hermes Principal, local `86ed776` | Imported with history; native environment, reflection repair, chunk rotation, ARQ idle/ingestion confirmation, Icarus dedup and recall fixes preserved and hardened. |
| PR #37, Brian Doherty, `fc3634c` | Imported. Additional fixes prevent missing-profile DB fallback, isolate custom homes and Compose projects, require explicit ports for additional profiles, and reject incomplete installer arguments. |
| PR #35, plasmaStray, commits through `377e13a` | Imported. Reconciled with RRF thresholds; query dimensions/auth match ingestion settings. Missing embeddings go directly to lexical fallback. Degradation is diagnostic context rather than a mandated scripted preamble. |
| PR #31, ruoxi001, `86816e4` | **Not merged**: the actual patch only repeats embedding environment wiring already in `e03db1f`; it does not change YAML tag parsing. A new tested scalar-normalization fix implements the reported behavior. |
| Issue #38 | Both reflection paths now parse fenced/surrounded JSON, validate the expected schema and retry before abstaining. Ollama `thinking` is accepted as a fallback string, never trusted without validation. Invalid reflection does not generate an opaque raw memory or change source confidence. |
| Lucidus recovery plugin/runtime | Ported session isolation, incremental capture ledger, opt-out detection, write serialization, exact-content dedup, automatic daily cap and optional local FTS/Qdrant index. Personal identity, migration text, consultant tool permissions and Cosmic-net tools remain profile-specific. |
| Hermes_Jr `7de2265` + local Compose edit | The observed empty Qdrant key workaround is generalized at container startup: remove the empty environment variable, retain configured authentication. No fresh SSH export was obtained in this execution; the prior read-only inventory remains the evidence. |
| Hermes session_search patch | Preserved under `modifications/session_search_tool.py.diff`; belongs upstream in Hermes, not a replacement of its installed source. |
| Vault Curator | Separate companion checkout and commit, not vendored here. Includes source/model invalidation, idempotent phases, Fabric exclusions and filtered status. |

PR links: [#31](https://github.com/ClaudioDrews/memory-os/pull/31), [#35](https://github.com/ClaudioDrews/memory-os/pull/35), [#37](https://github.com/ClaudioDrews/memory-os/pull/37). Issue: [#38](https://github.com/ClaudioDrews/memory-os/issues/38).

## Behavioral changes

- Reflection verdicts require an actual boolean, known severity and nonempty explanation. Batch reflection requires arrays of strings. Braces inside quoted JSON strings are handled by the JSON decoder.
- Contradictions set `contradiction_unresolved`; later consistent verdicts cannot erase that state or restore confidence automatically. Resolving contradictions is a separate explicit operation; no automatic clearing tool is introduced.
- Chunk selection paginates and prioritizes unseen points, includes missing counters, excludes archived points, and stops revisiting points at the configured historical ceiling of three reflections.
- `LLM_BACKEND=ollama` is the backward-compatible default. OpenRouter requires explicit selection and a key. Providing an embedding key alone never silently switches the reflection provider.
- Ingestion checkpoints advance only after a successful worker status. Set `WORKER_WIKI_ROOT=/wiki` for Docker, or to the actual absolute wiki path for native workers. Redis address/port and profile state files are configurable.
- Redis uses `noeviction` so queue/result keys are not evicted as a cache policy.
- Registered Icarus lifecycle callbacks use session-scoped retrieval sets and per-turn capture. They do not rebuild `MEMORY.md` from Fabric. A persistent ledger prevents repeated callbacks/restarts from capturing the same turn again. Interrupted, failed or unavailable extractions are not automatically retried; conversation history remains the source for manual recovery.
- Automatic capture defaults to 12 entries/day. Manual `fabric_write` is not capped. Exact dedup normalizes whitespace/case; existing summary-token similarity is a heuristic, **not semantic embedding dedup**.
- Explicit `FABRIC_DIR` or DB overrides still express deliberate sharing. Shared Fabric has a shared write ledger and automatic budget.

## Verification

Run using a dedicated environment with `requirements.txt` installed:

```bash
python scripts/test_offline.py
bash -n setup.sh scripts/run-script.sh
docker compose -f docker/docker-compose.yml config --quiet
```

The runner uses temporary profile paths and dummy credentials. Tests use mocked HTTP/ARQ services and a real embedded Qdrant store with synthetic vectors. They do not call paid APIs, download embedding models or contact existing services. Standalone suites cover collapse, sanitization, query providers, profile paths, installer parsing and ingestion path containment. New regressions cover JSON repair/abstention, confidence preservation, chunk selection, ingestion acknowledgment, session isolation, capture budgets, local indexing and native staging.

The recorded run passed 29 new unittest regressions, 5 provider tests, and the existing collapse, 24 sanitization checks, profile, setup and path-containment suites. The companion curator passed 4 additional offline tests. Generated native units also passed `systemd-analyze --user verify` (with sandbox socket-option warnings); their commands used inert placeholders, so this is a syntax check, not a service run.

A ResourceWarning was observed inside the installed qdrant-client local collection persistence during collection creation; the client is closed in a `finally` block. The tests pass, but this dependency warning is recorded rather than suppressed.

## Remaining release checks

- No fresh Docker image build/start, native daemon start, paid-provider evaluation, reboot test or live Hermes conversation was performed. Those require network/runtime access and belong to an isolated staging deployment. Compose parsing is not a Docker installation test.
- Validate the exact target Hermes version and hook timing before deployment. Legacy hook helpers remain in the code for compatibility/tests; the plugin registers the new lifecycle adapter.
- Check real Ollama/OpenRouter response shapes, budget accounting under concurrent jobs, and query latency with a cold local embedding model. Reflection budget accounting remains the existing SQLite design; this consolidation does not claim a distributed quota guarantee.
- The local FTS/Qdrant mode is opt-in CLI functionality, not automatically substituted into Icarus. Its index is separate and rebuildable; it is not the lost original collection.
- Refresh the Jr snapshot when SSH access is available without a new approval. Verify its current runtime against the consolidated source before scheduling a lab deployment.
- Review the Hermes and Vault Curator changes in their respective projects. No issues/PRs were commented on, closed or merged remotely.

Issues #16 (benchmarks), #17 (other harnesses), #27 (discussion), #32 (Hermes itself inside Docker), and #36 (listing) are triaged in the local inventory; they are not advertised as implemented by this candidate.
