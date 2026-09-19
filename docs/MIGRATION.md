# Reviewing and deploying the consolidation candidate

Keep code, configuration and memory data separate. This candidate does not automatically migrate databases or rewrite existing memories.

1. Save the installed source revision and sanitized configuration, and take consistent backups of SQLite/Qdrant data before any later deployment.
2. Review the PR imports and local changes described in CONSOLIDATION.md. The production Principal plugin currently may be a symlink: never switch branches in that checkout to review this candidate.
3. Install this checkout's dependencies in a new `.venv`; run `python scripts/test_offline.py` there. The local audit used an already installed dependency environment read-only; it did not install packages into a running agent.
4. Select Docker or native staging. Use a distinct profile, data paths and ports. Do not start a second worker against a production queue during validation.
5. Confirm retrieval and capture with synthetic conversations, including two interleaved sessions, refusal to memorize, duplicate callbacks and unavailable providers. Confirm default and named profiles cannot read each other's databases.
6. Review differences in the profile's identity/permissions separately. In particular, Lucidus's consultant restrictions and opt-out policy must remain configured. Do not replace his entire plugin/configuration with this generic plugin without merging those adapters.
7. Only after review, update a single lab installation, observe it, then roll out the pinned revision to the remaining agents. A rollback restores code/configuration, not old memory databases over new conversations.

## Docker

`setup.sh --profile NAME` now requires `QDRANT_HOST_PORT` and `REDIS_HOST_PORT`. Every profile uses its own Compose project, volumes and `HERMES_HOME/memory-os-compose.env`. The default project is `memory-os-default`.

**Existing installations:** record the existing Compose project name and pass `COMPOSE_PROJECT_NAME` if you intend to retain its volumes. A new project name creates a separate empty store; it is not a migration. Existing `.env` keys are preserved by the installer: audit stale Fabric, Redis, wiki and provider settings before using it.

The installer still performs real package installation, plugin installation, cron setup and container startup. It was not executed during consolidation. Avoid running it against a live agent merely to inspect configuration; use `docker compose config` for parsing.

## Native (generate only)

```bash
python setup/prepare_native.py \
  --output /absolute/path/to/empty-staging-directory \
  --profile /absolute/path/to/test-profile \
  --wiki /absolute/path/to/test-wiki \
  --qdrant /absolute/path/to/qdrant \
  --redis /absolute/path/to/redis-server \
  --name lab --qdrant-port 26333 --redis-port 26379
```

This creates three user-service files, Redis/Qdrant configuration and a private `worker.env`. It never installs or starts services and refuses to overwrite a nonempty target. Review paths, fill credentials, initialize databases and install dependencies before any authorized service deployment. Keep the generated directory in place because the units reference it. No timers are created.

For host-side ingestion/reflection, load the same worker environment, set `HERMES_HOME`, and use `scripts/run-script.sh`. `MEMORY_OS_PYTHON` can select the intended Python executable. Do not copy keys into version control.

## Optional local index

Set `MEMORY_OS_LOCAL_ROOT` to an isolated directory containing `wiki/` and `documents/`. The CLI stores its rebuildable index below `memory-index/`.

```bash
python scripts/local_memory.py sync --lexical-only
python scripts/local_memory.py search 'your query'
```

Set `MEMORY_OS_LOCAL_SEMANTIC_SEARCH=0` for strictly offline search. Vector sync is opt-in (`sync` without `--lexical-only`) and uses `EMBEDDING_API_BASE`, `EMBEDDING_MODEL`, `EMBEDDING_DIMS` and the appropriate API key. Model/dimension changes require a compatible vector index; do not silently reuse an incompatible collection.
