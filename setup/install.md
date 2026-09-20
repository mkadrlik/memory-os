# Setup Guide

> **Prefer automated install?** Run `curl -sSL https://raw.githubusercontent.com/ClaudioDrews/memory-os/main/setup.sh | bash` — one command, 10 phases, fully idempotent. This manual guide is kept for reference and troubleshooting.

> Step-by-step installation of the Memory OS stack. Assumes Hermes Agent is already installed and configured.

## Prerequisites

- Hermes Agent 0.14.0+ (tested on 0.15.2)
- Python 3.11+
- Docker 24.0+
- OpenRouter API key **only if using OpenRouter as embedding backend** (Ollama/vLLM/llama.cpp local providers do not require a key — see [Layer 5: Qdrant](../layers/05-qdrant.md))
- 16 GB RAM recommended (8 GB minimum)

## Installation

### 1. Icarus Plugin (bundled)

A **symlink** into the repo keeps the installed plugin identical to the checkout,
so edits here are what Hermes loads:

```bash
ln -sfn "$(pwd)/icarus" ~/.hermes/plugins/icarus
```

A plain copy (`cp -r icarus/ ~/.hermes/plugins/icarus/`) is the portable install; it will **not** see later repo edits until recopied.

The plugin runs inside the **Hermes runtime**, which is its own virtual
environment (`~/.hermes/hermes-agent/venv`) — not your system Python. Its
dependencies come from `requirements.txt` and the Hermes installer ships `uv`,
so the reliable command is:

```bash
~/.hermes/bin/uv pip install --python ~/.hermes/hermes-agent/venv/bin/python -r requirements.txt
```

The scheduled scripts (section 8) run with the **system** `python3` from
crontab, so they need the same requirements installed there:

```bash
python3 -m pip install --break-system-packages -r requirements.txt
```

`bash setup.sh` does both for you; these two commands are what it runs.

After `hermes update`, re-apply `modifications/session_search_tool.py.diff` so `session_search(profile=...)` stays fail-closed (does not fall back to the default profile's `state.db`).

Native Redis (`redis-conf/redis.conf`, gitignored because it holds `requirepass`) must use `maxmemory-policy noeviction` so ARQ keys are not LRU-evicted. `worker.env` is also gitignored; copy `worker.env.example`.

### 2. Database Setup

Install the Python dependencies first:

```bash
pip install -r requirements.txt
```

Memory OS requires two SQLite databases with FTS5 full-text search indexes:
`state.db` (session history, lineage, reflection budget) and `memory_store.db`
(facts, entities, memory banks). The setup script creates both with idempotent
`CREATE TABLE IF NOT EXISTS` statements — safe to run multiple times.

```bash
python setup/setup_db.py
```

**What it creates:**

| Database | Tables |
|---|---|
| `state.db` | `sessions`, `messages`, `messages_fts` (FTS5), `messages_fts_trigram`, `lineage`, `reflection_budget`, `compression_locks`, `schema_version`, `state_meta` |
| `memory_store.db` | `entities`, `facts`, `facts_fts` (FTS5), `fact_entities`, `memory_banks` |

Options:

```bash
python setup/setup_db.py --dry-run          # preview without executing
python setup/setup_db.py --state-db /custom/path/state.db
python setup/setup_db.py --memory-db /custom/path/memory_store.db
```

Environment variables override defaults:

```bash
export STATE_DB_PATH=/home/your-user/.hermes/state.db
export MEMORY_STORE_PATH=/home/your-user/.hermes/memory_store.db
```

### 3. Enable Icarus in Hermes Config

Copying `icarus/` into `~/.hermes/plugins/icarus` is not enough: Hermes 0.21+
treats user plugins as opt-in. `bash setup.sh` runs the enable step for you.

```bash
hermes plugins enable icarus
```

That writes `plugins.enabled: [icarus]` in `~/.hermes/config.yaml` (not a
top-level `enabled:` list). Then restart the gateway if it is installed as a
service:

```bash
hermes gateway restart    # skip if `hermes gateway status` says it is not running
```

CLI `hermes chat` loads enabled plugins in-process without a gateway.

Verify the plugin loaded:

```bash
hermes plugins list
# → icarus  enabled  0.3.0
hermes plugins show icarus
# → Status: enabled
```

### 4. Docker Infrastructure

The compose file lives in the `docker/` directory of this repository and must be run **in-place** — the worker build context (`./worker`) is relative to the compose file location.

```bash
# Navigate to the docker directory inside your clone
cd /path/to/memory-os/docker

# Create .env with required variables
cat > .env << EOF
# Required only for OpenRouter embedding backend; safe to leave empty for local providers
OPENROUTER_API_KEY=sk-or-...
REDIS_PASSWORD=$(openssl rand -hex 16)
# Optional overrides (defaults shown)
EMBEDDING_DIMS=4096
COLLECTION_NAME=knowledge_base
LOG_LEVEL=INFO
EOF

# Optional — if you want the Docker stack to use your existing production
# directories instead of local test volumes, uncomment and set these:
# MEMORY_OS_WIKI_PATH=/home/your-user/vault/wiki
# MEMORY_OS_HERMES_HOME=/home/your-user/.hermes
# MEMORY_OS_FABRIC_DIR=/home/your-user/vault/fabric
# ⚠️  Do NOT set these to production paths unless you understand the risk.
#     The worker mounts /fabric and /hermes as read-write.

# Start the stack
docker compose up -d
```

Verify all three services are running (run from the same `docker/` directory as the block above — a bare `docker compose` from the repository root will not find the file):

```bash
docker compose ps
# → Should show redis, qdrant, and worker all with Status: Up

curl -s http://localhost:6333/healthz  # → {"title":"ok","version":"1.17.1"}
redis-cli -a "$REDIS_PASSWORD" ping    # → PONG
```

### 5. Environment Variables

Two places read embedding settings, and they are different files:

| Consumer | File |
|---|---|
| Worker/container (ingestion, reflection) | the Compose env file, `~/.hermes/memory-os-compose.env` (created by `setup.sh`; `docker/.env` if you started the stack by hand) |
| Icarus plugin / context enhancer (query time) | the Hermes profile `.env`, e.g. `~/.hermes/.env` |

Set both when you change provider, or the ingestion side and the query side will
disagree. `bash setup.sh` copies the values it finds (environment first, then
`~/.hermes/.env`) into the Compose env file for you.

**Local embedding provider (no key):** point the worker at an OpenAI-compatible
endpoint and make `EMBEDDING_DIMS` match the model, before the first start:

```bash
export EMBEDDING_API_BASE=http://host.docker.internal:11434/v1   # Ollama on the host
export EMBEDDING_MODEL=nomic-embed-text
export EMBEDDING_DIMS=768
export EMBEDDING_API_KEY=            # not needed for a local endpoint
bash setup.sh
```

The collection is created with `EMBEDDING_DIMS` dimensions on first start; if you
change the model afterwards, recreate the collection (see Troubleshooting).
`host.docker.internal` resolves on Docker Desktop, and on Linux because the
Compose file maps it to the host gateway.

> ⚠️ **A local Ollama listens on `127.0.0.1` only, which the worker cannot
> reach.** Inside the container `127.0.0.1` is the container itself, so the
> worker's embedding calls fail with `Connection refused` (error code 111) and
> ingestion silently produces no vectors. Make Ollama listen on an address the
> Docker bridge can reach:
>
> ```bash
> sudo systemctl edit ollama
> # [Service]
> # Environment="OLLAMA_HOST=0.0.0.0:11434"
> sudo systemctl restart ollama
> ```
>
> Prefer the bridge address (`OLLAMA_HOST=172.17.0.1:11434`) over `0.0.0.0` when
> the machine is not otherwise firewalled — `0.0.0.0` exposes the model endpoint
> to your whole network. Verify reachability from inside the worker before
> relying on ingestion:
>
> ```bash
> docker compose -f docker/docker-compose.yml --env-file ~/.hermes/memory-os-compose.env -p memory-os-default exec worker \
>   python -c "import socket;s=socket.socket();s.settimeout(3);print(s.connect_ex(('host.docker.internal',11434)))"
> # → 0 = reachable; 111 = connection refused (Ollama still on 127.0.0.1)
> ```

Add to your Hermes profile `.env` (e.g. `~/.hermes/.env`):

```bash
# Required
FABRIC_DIR=/home/your-user/vault/fabric

# Required when using OpenRouter — for embeddings *and* for Icarus session
# extraction (the automatic capture)
OPENROUTER_API_KEY=sk-or-...

# Strongly recommended
ICARUS_EXTRACTION_MAX_TOKENS=4096
ICARUS_EXTRACTION_MODEL=deepseek/deepseek-v4-flash
EMBEDDING_DIMS=4096

# Local / non-OpenRouter LLM for Icarus session extraction.
# Icarus resolves its extraction endpoint in this order: ICARUS_ENDPOINT →
# DEEPSEEK_API_KEY → OPENROUTER_API_KEY. With none of them set it cannot
# extract anything, the turn is recorded as `empty_or_unavailable` in
# ~/.hermes/icarus-capture.sqlite3, and **no memory is written** — the session
# looks normal and the loss is silent. For a local model, point ICARUS_ENDPOINT
# at its OpenAI-compatible chat endpoint and name any non-empty variable in
# ICARUS_API_KEY_ENV (local endpoints ignore the key value):
# ICARUS_ENDPOINT=http://127.0.0.1:11434/v1/chat/completions
# ICARUS_API_KEY_ENV=OLLAMA_PLACEHOLDER_KEY
# ICARUS_EXTRACTION_MODEL=qwen2.5:7b-instruct
# OLLAMA_PLACEHOLDER_KEY=ollama-local

# Optional — Embedding backend (defaults to OpenRouter)
# EMBEDDING_API_BASE=https://openrouter.ai/api/v1
# EMBEDDING_MODEL=qwen/qwen3-embedding-8b

# Optional — automatic-capture budget: distinct entries the automatic capture
# may write per day (manual fabric_write is not counted). Reaching it stops
# automatic capture for the rest of the day; each turn is recorded as
# `budget_reached` in ~/.hermes/icarus-capture.sqlite3.
# ICARUS_MAX_DAILY_ENTRIES=12

# Optional — API key for non-OpenRouter authenticated embedding endpoints
# (vLLM with --api-key, custom hosted services). Not needed for OpenRouter
# or local unauthenticated providers.
# EMBEDDING_API_KEY=your-key-here
# EMBEDDING_REQUEST_TIMEOUT=30
# EMBEDDING_REQUEST_RETRIES=1
# ICARUS_MEMORY_DEGRADED_WARNING=⚠️ Semantic Wiki search is temporarily unavailable; this response may use incomplete context.
# ICARUS_SPARSE_QUERY_ENABLED=0  # when FastEmbed is absent in the Hermes runtime

# Optional
ICARUS_OBSIDIAN=1
ICARUS_RESULT_MAX_CHARS=500
ICARUS_TASK_MAX_CHARS=300
```

**⚠️ Use absolute paths.** The Hermes gateway runs as a systemd service — `~` is not expanded. Always use `/home/your-user/...`.

### 6. Core File Modifications

Apply the additions documented in
[setup/rulebook.md](rulebook.md) and
[modifications/soul-rulebook.md](../modifications/soul-rulebook.md):

**`~/.hermes/rulebook.md`** — apply the three amendments from
`modifications/execution-agent-protocol.md` (see `setup/rulebook.md`
for a summary). Each amendment targets a specific section of the
Execution Agent protocol — insert it after the referenced section.

- Each amendment starts with `<!-- Memory OS amendment — do not duplicate -->`.
  Before applying, check whether this marker already exists in your
  rulebook — if it does, skip that amendment.

**`SOUL.md`** — add Ground Truth level 2 (injected memory) and context
injection convention as documented in `modifications/soul-rulebook.md`.

**`~/.hermes/.env`** — set `HERMES_AGENT_NAME=hermes` (or any unique name).
This distinguishes your agent in fabric entries and enables multi-agent
handoff. Without it, all entries use the fallback `agent: "agent"` and
cross-agent features are disabled.

These modifications ensure the agent treats injected memory as more
authoritative than training knowledge, and knows where to find
persisted information without re-discovering it.

### 7. Wiki + Vault Setup

Memory OS stores its knowledge pipeline inside an Obsidian vault. The vault
path is user-specific — set it as an environment variable first:

```bash
# Set this to your Obsidian vault path
export VAULT_PATH=/home/your-user/path/to/vault
```

Create the wiki directory structure:

```bash
mkdir -p $VAULT_PATH/wiki/{raw,concepts,entities,comparisons,_meta,_archive}
```

**What goes where:**
- `raw/` — source documents to be ingested and curated
- `concepts/`, `entities/`, `comparisons/` — auto-generated by vault-curator
- `_meta/` — pipeline metadata (SCHEMA.md, indexes)
- `_archive/` — aged-out content from decay scanner

The wiki starts empty. Add source documents to `raw/` and the wiki-continuous-ingest
cronjob (step 7) will begin extracting structured pages.

**Optional — Vault Curator:** For automatic enrichment, semantic linking, and
MOC generation, install [vault-curator](https://github.com/ClaudioDrews/vault-curator)
as a separate tool. It runs independently and is not required for Memory OS
core functionality.

### 8. Maintenance Scripts

The `scripts/` directory in this repository contains the maintenance tools
that keep the memory stack healthy. Copy them to a location of your choice
(e.g. `~/memory-os-scripts/`) and schedule them.

| Script | Schedule | Purpose |
|---|---|---|
| `wiki_continuous_ingest.py` | Hourly | Detects new/modified .md files and enqueues them to the ARQ worker |
| `decay_scanner.py` | Weekly (Sun 3am) | Archives low-importance chunks based on age and importance_score |
| `dlq_manager.py` | Every 6 hours | Reads, classifies, and reports dead letter queue failures |
| `semantic_dedup.py` | Monthly (1st Sun) | Scans for near-duplicate vectors (cosine > 0.92) |
| `backfill_decay_metadata.py` | One-shot / on-demand | Populates missing metadata (created_at, importance_score) for decay scanner |
| `pre_validator.py` | On-demand | Semantic linter — queries knowledge_base before I/O actions |
| `reflection_trigger.py` | Every 5 min | Triggers micro_reflection when ARQ worker is idle |
| `bulk_wiki_ingest.py` | One-shot | Initial bulk ingestion of existing wiki content |
| `holographic-memory-backup.py` | Weekly (Mon 4am) | Dump and compress `memory_store.db` to backup directory |
| `wiki-raw-ingest-monitor.py` | Twice/week (Mon/Thu 3am) | Detects new or drifted files in `raw/` vs FTS5 index |
| `maas-heartbeat.py` | Every 6 hours | Health-check ping against Qdrant, Redis, and ARQ queue depth |

**Using Hermes cron (recommended):**

```bash
hermes cron create \
  --name "wiki-continuous-ingest" \
  --schedule "0 * * * *" \
  --script /path/to/scripts/wiki_continuous_ingest.py \
  --no-agent \
  --deliver local

hermes cron create \
  --name "decay-scanner" \
  --schedule "0 3 * * 0" \
  --script /path/to/scripts/decay_scanner.py \
  --no-agent \
  --deliver local

hermes cron create \
  --name "dlq-manager" \
  --schedule "0 */6 * * *" \
  --script /path/to/scripts/dlq_manager.py \
  --no-agent \
  --deliver local

hermes cron create \
  --name "semantic-dedup" \
  --schedule "0 3 1 * *" \
  --script /path/to/scripts/semantic_dedup.py \
  --no-agent \
  --deliver local

hermes cron create \
  --name "holographic-memory-backup" \
  --schedule "0 4 * * 1" \
  --script /path/to/scripts/holographic-memory-backup.py \
  --no-agent \
  --deliver local

hermes cron create \
  --name "wiki-raw-ingest-monitor" \
  --schedule "0 3 * * 1,4" \
  --script /path/to/scripts/wiki-raw-ingest-monitor.py \
  --no-agent \
  --deliver local

hermes cron create \
  --name "maas-heartbeat" \
  --schedule "0 */6 * * *" \
  --script /path/to/scripts/maas-heartbeat.py \
  --no-agent \
  --deliver local
```

**Before enabling decay scanner:** run `backfill_decay_metadata.py` once to
populate `created_at`, `last_accessed_at`, `importance_score`, and
`confidence_score` on existing Qdrant points. Without backfill, the decay
scanner will find zero eligible points.

**Exempting collections:** Set `DECAY_EXEMPT_PREFIXES` and
`DEDUP_EXEMPT_PREFIXES` env vars (comma-separated prefixes) to exclude
specific Qdrant collections from automated maintenance.

### 9. Gateway Restart

```bash
hermes gateway status     # running? as a service or manually?
hermes gateway restart    # only meaningful if it is installed as a service
```

If `status` says *"Running manually, not as a system service"*, `restart` starts
the gateway in the foreground and does not return — install the service once
instead, then use `restart`:

```bash
hermes gateway install    # background service (+ starts it now)
```

Changes to `.env`, `SOUL.md`, `rulebook.md`, and Icarus plugin code only take effect after restart.

### 10. Verify

Inside Hermes chat:

```
/plugins
# → Should show: icarus v0.3.0 (16 tools, 4 hooks)

fabric_brief()
# → Should show recent fabric entries (initially empty)

qdrant_search("test query")
# → Should return results from knowledge_base (if wiki has content)

fact_store(action='probe', entity='test')
# → Should return empty (no facts stored yet)
```

### 11. Disable or remove

**Disable the plugin, keep all memory** (fastest way to stop injection):

```bash
# remove "icarus" from the enabled list in ~/.hermes/config.yaml
hermes gateway restart
```

**Stop the stack, keep the data** (named volumes survive):

Run these from the **repository root** — the compose file lives in `docker/`,
so it must be named explicitly (same `-f`/`--env-file`/`-p` triple `setup.sh`
uses). `docker compose` does not search subdirectories, so a bare
`docker compose down` from the repository root fails with
`no configuration file provided: not found`.

```bash
docker compose -f docker/docker-compose.yml --env-file ~/.hermes/memory-os-compose.env -p memory-os-default down
```

**Remove the software, keep the data:**

```bash
docker compose -f docker/docker-compose.yml --env-file ~/.hermes/memory-os-compose.env -p memory-os-default down
rm -rf ~/.hermes/plugins/icarus ~/.hermes/scripts/context_enhancer.py
crontab -l | grep -v -e "memory-os wiki watcher" -e "wiki_continuous_ingest.py" | crontab -
```

The cron removal matches **both** lines the installer writes: the marker comment
and the scheduled command. Matching only the marker left the hourly watcher
running against a removed stack, and a later reinstall added a second copy of
the same entry (verified on the acceptance VM: four identical watcher lines
after one install, one deactivation and one reinstall).

**Remove the data too** — this deletes what the agent remembered. Do it only
after backing up what you want to keep:

```bash
docker compose -f docker/docker-compose.yml --env-file ~/.hermes/memory-os-compose.env -p memory-os-default down -v
rm -rf ~/.hermes/vault            # wiki + fabric
rm -f  ~/.hermes/state.db ~/.hermes/memory_store.db
rm -f  ~/.hermes/icarus-capture.sqlite3   # capture ledger + daily budget
rm -f  ~/.hermes/wiki_ingest_state.json ~/.hermes/wiki_ingest_failures.json
```

Those databases also hold the host agent's own session history and facts, so
delete them only if you really mean it. Qdrant vectors live in the named volume
`memory-os-default_qdrant_data`, removed by `down -v`.

`icarus-capture.sqlite3` is what Icarus uses to remember which turns it already
captured and how much of today's automatic budget is spent. Leaving it behind
after a data wipe means a reinstalled agent can start the day already at its cap
and silently skip captures — observed on the acceptance guest, where the ledger
still held the previous round's rows (`empty_or_unavailable` ×7, `processed` ×2)
after a full teardown and a fresh install. The ingest checkpoint and DLQ files are
the same kind of state for the wiki watcher: keep them if you want it to remember
what it already indexed, delete them for a genuinely clean slate.

## What to expect

**Day 1:** Infrastructure running. Fabric entries begin accumulating at session end. Qdrant indexing starts as wiki files are added.

**Week 1:** Context injection active. Agent references past decisions automatically. Wiki pipeline producing curated pages from raw documents.

**Month 1:** Decay scanner has aged content to evaluate. Structured facts accumulating with trust scores.

## Troubleshooting

### Qdrant collection shows 0 points
Check: `EMBEDDING_DIMS=4096` matches collection schema. Mismatch → vectors rejected silently.

### Fabric entries are truncated
Check: `ICARUS_EXTRACTION_MAX_TOKENS=4096` in `.env` AND gateway was restarted after setting it.

### Memory tool reports "Icarus write conflict"
Icarus is writing to MEMORY.md instead of CREATIVE.md. Verify Icarus fork is installed (not upstream esaradev version).

### Context injection not working
Check: the configured embedding endpoint and its required credential are set,
`context_enhancer.py` can import, and the gateway was restarted after
`hooks.py` or embedding environment changes.

Two failure modes worth checking first, because both degrade silently to
lexical-only retrieval instead of reporting an error:

- `EMBEDDING_API_BASE` must be reachable **from the host**. `host.docker.internal`
  is a Docker-only name: inside the worker container `extra_hosts` maps it, on
  the host it does not resolve. With a local Ollama, the profile `.env` needs
  `EMBEDDING_API_BASE=http://127.0.0.1:11434/v1` (the Compose env file keeps
  `host.docker.internal`, which is correct there). Symptom:
  `[CE-ERROR] Dense embedding attempt 1/1 failed: ... Failed to resolve
  'host.docker.internal'` and `[CE-FALLBACK] ... falling back to lexical`.
- The BM25 query embedding runs as a subprocess that needs `fastembed`
  importable. If sparse embedding fails, the hook logs
  `[CE-ERROR] Embedding sparse failed: ...` and answers come from dense-only
  search — hybrid search never runs. Check by hand:
  `python3 -c "import fastembed"`.

### Answers do not reflect an edited wiki file
Editing a file under the wiki is detected by hash, so the watcher re-enqueues
it. Re-ingestion replaces the stored content *in place* when the similarity
search matches the same `file_path` (worker log: `Update: replaced content of
chunk ...`). If answers keep showing the previous revision, confirm the worker
was rebuilt after upgrading (`docker compose ... build worker`) and that the
file's hash really changed in `~/.hermes/wiki_ingest_state.json`.

Note: two *different* files whose content is similar above the dedup threshold
(0.92 by default) are still merged into one point and the second file's text is
not stored. Lower the load of near-duplicate documents, or raise
`dedup_threshold` in `docker/worker/tasks/file_ingestion.py`, if documents that
differ in a single fact must both remain retrievable.

### Decay scanner produces "0 archived" every week
Most likely: point payloads missing `last_accessed_at` or `importance_score` metadata. Run backfill before enabling decay.

### Multiple collections in Qdrant dashboard

The Memory OS uses the `knowledge_base` collection exclusively. Other
collections you may see (e.g., from other Hermes agent plugins or standalone
agents) are safe to coexist — Qdrant isolates each collection at the storage
and query level. Do NOT delete collections you did not create — they may
belong to other agents sharing the same Qdrant instance.

### Ingestion reports success but the collection stays at 0 points

The worker accepted the job but the embedding call failed. On a clean install
this is almost always the local-Ollama bind address: Ollama listens on
`127.0.0.1` only and the worker cannot reach it (see the warning in section 5).
Check the worker log for `Connection refused`, fix `OLLAMA_HOST`, and re-run the
watcher (`scripts/wiki_continuous_ingest.py`).

### `rm -rf ~/.hermes/vault` fails with "Permission denied" after a reinstall

The Docker daemon creates the *source* of a bind mount as `root` when the path
does not exist yet. So if you removed `~/.hermes/vault` (the "remove the data
too" step), then started the stack again, Docker re-created `vault/`,
`vault/wiki/` and `vault/fabric/` owned by `root`. The documented cleanup then
fails for your user. Either remove them with `sudo rm -rf ~/.hermes/vault`, or
re-run `bash setup.sh` (which re-creates them owned by you) before deactivating
again. Do not start the stack between "remove the data" and the next install.
