# Memory OS — Quick Start

## One-command install

```bash
curl -sSL https://raw.githubusercontent.com/ClaudioDrews/memory-os/main/setup.sh | bash
```

This installs everything: Docker stack (Redis + Qdrant + Worker), Icarus plugin, SQLite databases, wiki vault, and environment variables. Safe to re-run — all steps are idempotent.

**Requires:** Docker, Python 3.11+, Hermes Agent. The script auto-detects your OpenRouter key and prompts only if missing.

`setup.sh` installs the Python dependencies for you, into both interpreters that
need them: the Hermes runtime (`~/.hermes/hermes-agent/venv`, no pip of its own —
the script uses the `uv` that ships with Hermes) and the system `python3` used by
the scheduled scripts. No `pip` package or sudo is required for that step.

No API key is required if you use a local embedding endpoint — see
[setup/install.md](setup/install.md) section 5 for the Ollama example.

> Prefer manual control? Follow [setup/install.md](setup/install.md) — step-by-step guide with validation checkpoints.

## Prerequisites

- **Docker** (Docker Compose v2)
- **Python 3.11+**
- **Hermes Agent** (v0.14.0 or later)
- **OpenRouter API key** (or local Ollama for embeddings)

## 1. Clone

```bash
git clone https://github.com/ClaudioDrews/memory-os.git
cd memory-os
```

No git? Download the archive (Code → Download ZIP, or a release tarball) and
extract it anywhere — `setup.sh` accepts a copied tree that has no `.git` as
long as it contains `docker/docker-compose.yml`.

## 2. Install

Follow [setup/install.md](setup/install.md) — step-by-step guide with validation checkpoints.

## 3. Verify

Once installed, confirm the stack is operational:

```bash
# Docker services (compose file lives in docker/; setup.sh writes the env file)
docker compose -f docker/docker-compose.yml --env-file ~/.hermes/memory-os-compose.env -p memory-os-default ps
# qdrant + redis + worker should be "healthy"

# Qdrant
curl -s http://localhost:6333/healthz    # should return "ok"

# Icarus plugin (must be *enabled*, not only copied into ~/.hermes/plugins)
hermes plugins list | grep icarus        # Status column: enabled

# Gateway (needed for messaging and Hermes cron; CLI chat loads plugins without it)
hermes gateway status
```

## 4. Use

Open Hermes. From the next session onward, it will:
- Recall past decisions (Icarus Fabric)
- Search your vault documents (Qdrant)
- Cross-reference facts you've mentioned (fact_store)

## 5. Add content

```bash
# Adjust to your vault path
mkdir -p ~/vault/wiki/raw
echo "# My notes" > ~/vault/wiki/raw/notes.md
```

The worker detects and indexes new files automatically.

## Next steps

- Full install guide: [setup/install.md](setup/install.md)
- Architecture: [layers/](layers/)
- How to contribute: [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md)
