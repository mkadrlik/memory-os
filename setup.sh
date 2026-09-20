#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Memory OS — Setup Script
# ──────────────────────────────────────────────────────────────────────────────
# Installs the complete Memory OS stack into your Hermes Agent.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/ClaudioDrews/memory-os/main/setup.sh | bash
#
# Or, if you already cloned the repo:
#   bash setup.sh
#
# What this script does:
#   1. Checks prerequisites (Docker, Python, Hermes)
#   2. Clones the repo (if needed)
#   3. Installs Python dependencies
#   4. Creates SQLite databases (state.db, memory_store.db)
#   5. Installs the Icarus plugin
#   6. Creates wiki/vault directory structure
#   7. Starts Redis + Qdrant + Worker (Docker Compose)
#   8. Configures environment variables
#   9. Applies rulebook modifications
#
# Idempotent — safe to run multiple times.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Safe defaults for optional env vars ──────────────────────────────────────
# Must come before any reference to these names.  set -u would abort with
# \"unbound variable\" if they were never exported by the caller.
QDRANT_API_KEY="${QDRANT_API_KEY:-}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

ok()  { printf "  ${GREEN}✅${NC} %s\n" "$1"; PASS=$((PASS + 1)); }
fail() { printf "  ${RED}❌${NC} %s\n" "$1"; FAIL=$((FAIL + 1)); }
warn() { printf "  ${YELLOW}⚠️${NC}  %s\n" "$1"; WARN=$((WARN + 1)); }
info() { printf "  📘 %s\n" "$1"; }

banner() {
    echo ""
    echo -e "${BOLD}── $1 ──${NC}"
    echo ""
}

# ── Detect script directory ──────────────────────────────────────────────────
# When run via curl|bash, SCRIPT_DIR is the current directory.
# When run from a cloned repo, it's the script's location.
if [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]}" != "bash" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$(pwd)"
fi

REPO_URL="https://github.com/ClaudioDrews/memory-os.git"
REPO_DIR="${MEMORY_OS_ROOT:-${SCRIPT_DIR}}"
if [ ! -f "${REPO_DIR}/docker/docker-compose.yml" ]; then REPO_DIR="${HOME}/memory-os"; fi

# ── Profile support ─────────────────────────────────────────────────────────
# Pass --profile <name> to install into that Hermes profile instead of the
# default ~/.hermes.  Each profile gets its own HERMES_HOME
# (<root>/profiles/<name>), so its memory (fabric, state.db, logs) stays
# isolated from other profiles.
PROFILE_NAME=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --profile)
            if [ "$#" -lt 2 ] || [ -z "$2" ]; then fail "--profile requires a name"; exit 1; fi
            PROFILE_NAME="$2"; shift 2 ;;
        --profile=*)
            PROFILE_NAME="${1#--profile=}"
            if [ -z "$PROFILE_NAME" ]; then fail "--profile requires a name"; exit 1; fi
            shift ;;
        *) fail "Unknown option: $1"; exit 1 ;;
    esac
done

# Restrict to a safe charset — PROFILE_NAME becomes a directory component
# below, so reject anything that could escape ~/.hermes/profiles/.
case "${PROFILE_NAME}" in
    *[!A-Za-z0-9_-]*)
        fail "Invalid --profile name '${PROFILE_NAME}' — use only letters, numbers, '_' and '-'"
        exit 1
        ;;
esac

if [ -n "${PROFILE_NAME}" ]; then
    HERMES_HOME="${HOME}/.hermes/profiles/${PROFILE_NAME}"
else
    HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"
fi
# Export so subprocesses (setup/setup_db.py, python3 invocations below) that
# read HERMES_HOME from the environment see the active profile instead of
# silently falling back to ~/.hermes.
export HERMES_HOME

VAULT_PATH="${VAULT_PATH:-${HERMES_HOME}/vault}"
ENV_FILE="${HERMES_HOME}/.env"
# A profile must never replace another profile's containers/volumes or ports.
if [ -n "${PROFILE_NAME}" ]; then
    if [ -z "${QDRANT_HOST_PORT:-}" ] || [ -z "${REDIS_HOST_PORT:-}" ]; then
        fail "Profile installs require distinct QDRANT_HOST_PORT and REDIS_HOST_PORT"
        exit 1
    fi
    VAULT_PATH="${VAULT_PATH:-${HERMES_HOME}/vault}"
fi
QDRANT_HOST_PORT="${QDRANT_HOST_PORT:-6333}"
REDIS_HOST_PORT="${REDIS_HOST_PORT:-6379}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-memory-os-${PROFILE_NAME:-default}}"


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1: Bootstrap — clone repo if needed
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 1: Bootstrap"

if [ -d "${REPO_DIR}/.git" ]; then
    ok "Repo already exists at ${REPO_DIR}"
    cd "${REPO_DIR}"
elif [ -f "${REPO_DIR}/docker/docker-compose.yml" ] && [ -f "${REPO_DIR}/setup.sh" ]; then
    # A usable checkout is a tree that carries the compose file — not
    # necessarily a clone. Archive-extracted trees (GitHub "Download ZIP", a
    # release tarball) and plain copies have no .git; testing only for .git made
    # the installer `git clone` over a non-empty directory and abort with
    # `fatal: destination path ... already exists` (exit 128 under pipefail).
    ok "Using existing checkout at ${REPO_DIR} (copied tree, no .git)"
    cd "${REPO_DIR}"
elif [ -e "${REPO_DIR}" ] && [ -n "$(ls -A "${REPO_DIR}" 2>/dev/null)" ]; then
    fail "${REPO_DIR} exists but is not a Memory OS checkout"
    fail "Move it aside, or set MEMORY_OS_ROOT to the checkout location, then re-run"
    exit 1
else
    info "Cloning Memory OS..."
    git clone "${REPO_URL}" "${REPO_DIR}" 2>&1 | tail -1
    cd "${REPO_DIR}"
    ok "Repo cloned to ${REPO_DIR}"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Phase 2: Pre-flight Checks
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 2: Pre-flight Checks"

# Docker
if docker info >/dev/null 2>&1; then
    ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"
else
    fail "Docker is not running — install and start Docker first"
    exit 1
fi

# Docker Compose
if docker compose version >/dev/null 2>&1; then
    ok "Docker Compose $(docker compose version --short 2>/dev/null || echo 'ok')"
else
    warn "Docker Compose plugin not detected — required to start the stack"
fi

# Python
PYTHON_VERSION=$(python3 --version 2>/dev/null | awk '{print $2}' || echo "none")
if [ "$PYTHON_VERSION" != "none" ]; then
    ok "Python ${PYTHON_VERSION}"
else
    fail "Python 3 not found"
    exit 1
fi

# Hermes
if command -v hermes >/dev/null 2>&1 || [ -f "${HERMES_HOME}/hermes-agent/cli.py" ]; then
    ok "Hermes Agent detected at ${HERMES_HOME}"
else
    warn "Hermes Agent CLI not found — some features will be limited"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Phase 3: Python Dependencies
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 3: Python Dependencies"

REQ_FILE="${REPO_DIR}/requirements.txt"
if [ -f "${REQ_FILE}" ]; then
    : # dependencies are installed below, into both interpreters that need them
else
    fail "requirements.txt not found at $(pwd)"
    exit 1
fi

# The Hermes install ships uv, which installs into any interpreter without
# system privileges — the only route that works on a Hermes venv (it has no
# pip) and on PEP 668 "externally managed" system pythons.
UV_BIN=""
if [ -x "${HERMES_HOME}/bin/uv" ]; then
    UV_BIN="${HERMES_HOME}/bin/uv"
elif command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
fi

install_requirements() {
    # $1 = interpreter, $2 = label, $3 = "system" for a non-venv interpreter
    local py="$1" label="$2" kind="${3:-venv}"
    [ -x "${py}" ] || return 2
    if [ -n "${UV_BIN}" ]; then
        if [ "${kind}" = "system" ]; then
            # uv has no --user: install into this interpreter's user site
            # directory, which is on its sys.path and needs no privileges.
            local usersite usersite_cmd='import site; print(site.getusersitepackages())'
            usersite="$("${py}" -c "${usersite_cmd}" 2>/dev/null || true)"
            if [ -n "${usersite}" ] && \
               "${UV_BIN}" pip install --quiet --target "${usersite}" --python "${py}" -r "${REQ_FILE}" 2>&1 | tail -2; then
                ok "Python dependencies installed for ${label} (uv --target)"
                return 0
            fi
        elif "${UV_BIN}" pip install --quiet --python "${py}" -r "${REQ_FILE}" 2>&1 | tail -2; then
            ok "Python dependencies installed for ${label} (uv)"
            return 0
        fi
    fi
    if "${py}" -m pip --version >/dev/null 2>&1; then
        # --user keeps the system environment clean; --break-system-packages is
        # what a PEP 668 ("externally managed") interpreter requires.
        if "${py}" -m pip install --quiet --user --break-system-packages -r "${REQ_FILE}" 2>&1 | tail -2 \
           || "${py}" -m pip install --quiet --break-system-packages -r "${REQ_FILE}" 2>&1 | tail -2; then
            ok "Python dependencies installed for ${label} (pip)"
            return 0
        fi
    fi
    return 1
}

# Two interpreters matter, and they are not the same one:
#   - the system python3 runs the scheduled scripts (wiki watcher, triggers);
#   - the Hermes runtime (its own venv) imports the Icarus plugin and the
#     context enhancer, so it needs the same dependencies.
DEPS_MISSING=0
SYSTEM_PY="$(command -v python3 || true)"
if install_requirements "${SYSTEM_PY}" "system python3 (scheduled scripts)" "system"; then
    :
else
    fail "Could not install dependencies for ${SYSTEM_PY:-python3}"
    DEPS_MISSING=1
fi

HERMES_PY="${HERMES_HOME}/hermes-agent/venv/bin/python"
if [ -x "${HERMES_PY}" ]; then
    if install_requirements "${HERMES_PY}" "Hermes runtime (plugin, context enhancer)"; then
        :
    else
        fail "Could not install dependencies into the Hermes runtime (${HERMES_PY})"
        DEPS_MISSING=1
    fi
else
    warn "Hermes runtime interpreter not found at ${HERMES_PY} — skipped those dependencies"
    info "If the plugin reports missing modules, run: ${UV_BIN:-uv} pip install --python <hermes venv python> -r requirements.txt"
fi

if [ "${DEPS_MISSING}" -eq 1 ]; then
    fail "Python dependencies are incomplete — the plugin or the scheduled scripts will fail to import"
    info "Install uv (ships with Hermes at ${HERMES_HOME}/bin/uv) or pip (e.g. apt-get install python3-pip), then re-run this script"
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
# Phase 4: SQLite Databases
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 4: Database Setup"

if [ -f "setup/setup_db.py" ]; then
    python3 setup/setup_db.py 2>&1 && \
        ok "SQLite databases created (state.db, memory_store.db)" || \
        fail "setup_db.py failed"
else
    fail "setup/setup_db.py not found"
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
# Phase 5: Icarus Plugin
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 5: Icarus Plugin"

ICARUS_DEST="${HERMES_HOME}/plugins/icarus"

if [ -d "icarus" ]; then
    mkdir -p "${HERMES_HOME}/plugins"
    # Replace the dest on every run. `cp -r icarus/ dest/` into an existing
    # dest nests a second icarus/ directory, which is what a second
    # (advertised-idempotent) installer run used to do.
    rm -rf "${ICARUS_DEST}"
    cp -r icarus "${ICARUS_DEST}"
    ok "Icarus plugin installed at ${ICARUS_DEST}"
else
    fail "icarus/ directory not found"
    exit 1
fi

# Hermes 0.21+ copies plugins as opt-in. Without this step, `hermes plugins
# list` shows icarus as "not enabled" and the hooks never run. --no-allow-tool-override
# skips the TTY prompt so curl|bash stays non-interactive.
if command -v hermes >/dev/null 2>&1; then
    if hermes plugins enable icarus --no-allow-tool-override >/dev/null 2>&1; then
        ok "Icarus plugin enabled"
    else
        warn "Icarus copied but not enabled — run: hermes plugins enable icarus"
    fi
else
    warn "'hermes' not on PATH — enable later: hermes plugins enable icarus"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Phase 5b: Context Enhancer Symlink
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 5b: Context Enhancer"

CE_SRC="${REPO_DIR}/scripts/context_enhancer.py"
CE_DEST="${HERMES_HOME}/scripts/context_enhancer.py"

if [ -f "${CE_SRC}" ]; then
    mkdir -p "${HERMES_HOME}/scripts"
    ln -sf "${CE_SRC}" "${CE_DEST}"
    ok "context_enhancer.py symlinked to ${CE_DEST}"
else
    fail "${CE_SRC} not found"
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
# Phase 6: Wiki + Vault Structure (BEFORE Docker — prevents root ownership)
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 6: Wiki & Vault"

mkdir -p "${VAULT_PATH}/wiki/"{raw,concepts,entities,comparisons,_meta,_archive}
mkdir -p "${VAULT_PATH}/fabric"
# Ensure Docker worker can read wiki files (umask may create 600)
chmod -R 755 "${VAULT_PATH}/wiki"
chmod -R 755 "${VAULT_PATH}/fabric"
ok "Directory structure created at ${VAULT_PATH}"
info "Permissions set to 755 on wiki/ and fabric/ (ensures Docker worker read access)"

if [ -n "${PROFILE_NAME}" ]; then
    # Profiles default to their own fabric under HERMES_HOME (see Phase 8 —
    # we deliberately don't write FABRIC_DIR into the profile .env), so
    # create it here rather than relying on the shared ${VAULT_PATH}/fabric.
    mkdir -p "${HERMES_HOME}/fabric"
    chmod -R 755 "${HERMES_HOME}/fabric"
    ok "Profile fabric directory created at ${HERMES_HOME}/fabric"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Phase 7: Docker Stack
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 7: Docker Stack"

DOCKER_DIR="${REPO_DIR}/docker"

if [ ! -d "${DOCKER_DIR}" ]; then
    fail "docker/ directory not found at ${REPO_DIR}"
    exit 1
fi

cd "${DOCKER_DIR}"

# ── Embedding / LLM provider ────────────────────────────────────────────────
# Precedence: environment variable > value already in the profile .env > default.
# A local or self-hosted OpenAI-compatible endpoint needs no API key at all, so
# the key is only requested when the endpoint really is OpenRouter.
env_value() { sed -n "s/^$1=//p" "${ENV_FILE}" 2>/dev/null | tail -1; }

EMBEDDING_API_BASE="${EMBEDDING_API_BASE:-$(env_value EMBEDDING_API_BASE)}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-$(env_value EMBEDDING_MODEL)}"
EMBEDDING_API_KEY="${EMBEDDING_API_KEY:-$(env_value EMBEDDING_API_KEY)}"
EMBEDDING_DIMS="${EMBEDDING_DIMS:-$(env_value EMBEDDING_DIMS)}"
LLM_BACKEND="${LLM_BACKEND:-$(env_value LLM_BACKEND)}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-$(env_value OLLAMA_BASE_URL)}"
OLLAMA_MODEL="${OLLAMA_MODEL:-$(env_value OLLAMA_MODEL)}"
COLLECTION_NAME="${COLLECTION_NAME:-$(env_value COLLECTION_NAME)}"
EMBEDDING_API_BASE="${EMBEDDING_API_BASE:-https://openrouter.ai/api/v1}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-qwen/qwen3-embedding-8b}"
EMBEDDING_DIMS="${EMBEDDING_DIMS:-4096}"
COLLECTION_NAME="${COLLECTION_NAME:-knowledge_base}"
LLM_BACKEND="${LLM_BACKEND:-ollama}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-deepseek-v4-flash:cloud}"

# Detect API key from Hermes .env
OPENROUTER_KEY=""
if [ -f "${ENV_FILE}" ]; then
    OPENROUTER_KEY=$(grep -oP '(?:OPENROUTER.*API_KEY|LLM_API_KEY)=\K.*' "${ENV_FILE}" 2>/dev/null | head -1 || true)
fi
OPENROUTER_KEY="${OPENROUTER_KEY:-${OPENROUTER_API_KEY:-}}"

if [ -n "${OPENROUTER_KEY}" ]; then
    ok "Embedding endpoint ${EMBEDDING_API_BASE} with a configured key"
elif [ "${EMBEDDING_API_BASE#*openrouter.ai}" != "${EMBEDDING_API_BASE}" ]; then
    echo ""
    echo -e "  ${YELLOW}No API key found for OpenRouter, the configured embedding endpoint.${NC}"
    echo "  The worker needs an embedding-capable API key (OpenRouter or compatible)."
    echo "  Press Enter to skip and point EMBEDDING_API_BASE at a local endpoint instead"
    echo "  (e.g. Ollama: EMBEDDING_API_BASE=http://host.docker.internal:11434/v1 EMBEDDING_MODEL=nomic-embed-text EMBEDDING_DIMS=768)."
    echo ""
    if [ -t 0 ]; then
        read -r -p "  Paste your API key (e.g. sk-or-v1-...), or Enter to skip: " OPENROUTER_KEY || OPENROUTER_KEY=""
    else
        info "Not running interactively — continuing without OpenRouter key"
    fi
    echo ""
    if [ -z "${OPENROUTER_KEY}" ]; then
        warn "No embedding credential: the worker will fail until EMBEDDING_API_BASE points at a local endpoint"
    fi
else
    info "Embedding endpoint ${EMBEDDING_API_BASE} — no API key required"
fi

# Generate random Redis password
REDIS_PW="${REDIS_PASSWORD:-}"
if [ -z "$REDIS_PW" ] && [ -f "$ENV_FILE" ]; then REDIS_PW=$(sed -n 's/^REDIS_PASSWORD=//p' "$ENV_FILE" | head -1); fi
REDIS_PW="${REDIS_PW:-$(openssl rand -hex 16)}"

# Create Docker Compose .env
DOCKER_ENV_FILE="${HERMES_HOME}/memory-os-compose.env"
if [ -n "${PROFILE_NAME}" ]; then STACK_FABRIC="${HERMES_HOME}/fabric"; else STACK_FABRIC="${VAULT_PATH}/fabric"; fi
umask 077
cat > "${DOCKER_ENV_FILE}" << DOCKERENV
OPENROUTER_API_KEY=${OPENROUTER_KEY}
REDIS_PASSWORD=${REDIS_PW}
QDRANT_API_KEY=${QDRANT_API_KEY}
QDRANT_HOST_PORT=${QDRANT_HOST_PORT}
REDIS_HOST_PORT=${REDIS_HOST_PORT}
EMBEDDING_API_BASE=${EMBEDDING_API_BASE}
EMBEDDING_MODEL=${EMBEDDING_MODEL}
EMBEDDING_API_KEY=${EMBEDDING_API_KEY}
EMBEDDING_DIMS=${EMBEDDING_DIMS}
COLLECTION_NAME=${COLLECTION_NAME}
LLM_BACKEND=${LLM_BACKEND}
OLLAMA_BASE_URL=${OLLAMA_BASE_URL}
OLLAMA_MODEL=${OLLAMA_MODEL}
LOG_LEVEL=INFO
MEMORY_OS_WIKI_PATH=${VAULT_PATH}/wiki
MEMORY_OS_HERMES_HOME=${HERMES_HOME}
MEMORY_OS_FABRIC_DIR=${STACK_FABRIC}
DOCKERENV

ok "Profile-specific Compose environment created"
info "Worker embedding: ${EMBEDDING_MODEL} @ ${EMBEDDING_API_BASE}, ${EMBEDDING_DIMS} dims, collection ${COLLECTION_NAME}"
compose() { docker compose --env-file "${DOCKER_ENV_FILE}" -p "${COMPOSE_PROJECT_NAME}" "$@"; }

# Pull pre-built images first (Redis, Qdrant) — fast
info "Downloading pre-built images (Redis, Qdrant)..."
compose pull redis qdrant 2>&1 | tail -3
ok "Base images downloaded"

# Build worker image — SLOW on first run (gcc + build-essential)
info "Building worker image (may take 5-10 minutes on first run)..."
info "  (Future builds will use Docker cache)"
if compose build worker 2>&1; then
    ok "Worker image built"
else
    fail "Failed to build worker image"
    exit 1
fi

# Start everything
info "Starting containers..."
if compose up -d 2>&1; then
    ok "Docker stack started (redis, qdrant, worker)"
else
    fail "docker compose up failed — check Docker"
    exit 1
fi

# Wait for healthy
info "Waiting for services to become healthy..."
sleep 3
if compose ps --format json 2>/dev/null | grep -q '"Health":"healthy"'; then
    ok "All services healthy"
else
    warn "Services may still be starting — check with: docker compose ps"
fi

# Return to repo directory
cd "${REPO_DIR}"

# ──────────────────────────────────────────────────────────────────────────────
# Phase 7b: Wiki Watcher Cron
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 7b: Wiki Watcher"

CRON_ENTRY="0 * * * * cd \"${REPO_DIR}\" && HERMES_HOME=\"${HERMES_HOME}\" python3 scripts/wiki_continuous_ingest.py >> \"${HERMES_HOME}/logs/wiki-ingest.log\" 2>&1"
# System crontab doesn't propagate Hermes's profile env the way Hermes cron
# does, so HERMES_HOME is set inline above. The marker is per-profile too —
# otherwise a second `setup.sh --profile X` run would see the first
# profile's marker and skip installing its own cron entry.
if [ -n "${PROFILE_NAME}" ]; then
    CRON_MARKER="# memory-os wiki watcher (${PROFILE_NAME})"
else
    CRON_MARKER="# memory-os wiki watcher"
fi

if crontab -l 2>/dev/null | grep -qxF "${CRON_MARKER}"; then
    ok "Wiki watcher cron already installed"
else
    (crontab -l 2>/dev/null || true; echo "${CRON_MARKER}"; echo "${CRON_ENTRY}") | crontab -
    ok "Wiki watcher cron installed (hourly ingestion)"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Phase 8: Environment Variables
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 8: Hermes .env"

if [ ! -f "${ENV_FILE}" ]; then
    warn "${ENV_FILE} not found — creating a new one"
    touch "${ENV_FILE}"
fi

add_env() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "${ENV_FILE}" 2>/dev/null; then
        # Already exists — don't overwrite
        return 0
    fi
    echo "${key}=${value}" >> "${ENV_FILE}"
}

if [ -z "${PROFILE_NAME}" ]; then
    add_env "FABRIC_DIR" "${VAULT_PATH}/fabric"
else
    # Leave FABRIC_DIR unset so each profile falls back to its own
    # <HERMES_HOME>/fabric (hermes_env.fabric_dir()) instead of every
    # profile colliding on the shared ${VAULT_PATH}/fabric.
    info "FABRIC_DIR left unset — profile '${PROFILE_NAME}' defaults to ${HERMES_HOME}/fabric"
fi
add_env "ICARUS_EXTRACTION_MAX_TOKENS" "4096"
add_env "ICARUS_EXTRACTION_MODEL" "deepseek/deepseek-v4-flash"
# `host.docker.internal` is a Docker-only name: it resolves inside the worker
# container (via extra_hosts), but NOT on the host, where the Hermes gateway and
# the Icarus hook run scripts/context_enhancer.py. Writing it into the profile
# .env made the host-side retrieval path fail to embed — dense and sparse both
# errored, every query degraded to lexical fallback, and hybrid (BM25) retrieval
# never ran. The Compose env file keeps the container-facing value; the profile
# .env gets the host-reachable one.
host_reachable() { printf '%s' "${1//host.docker.internal/127.0.0.1}"; }
add_env "EMBEDDING_API_BASE" "$(host_reachable "${EMBEDDING_API_BASE}")"
add_env "EMBEDDING_MODEL" "${EMBEDDING_MODEL}"
add_env "EMBEDDING_API_KEY" "${EMBEDDING_API_KEY}"
add_env "EMBEDDING_REQUEST_TIMEOUT" "30"
add_env "EMBEDDING_REQUEST_RETRIES" "1"
add_env "EMBEDDING_DIMS" "${EMBEDDING_DIMS}"
add_env "COLLECTION_NAME" "${COLLECTION_NAME}"
add_env "LLM_BACKEND" "${LLM_BACKEND}"
add_env "OLLAMA_BASE_URL" "$(host_reachable "${OLLAMA_BASE_URL}")"
add_env "OLLAMA_MODEL" "${OLLAMA_MODEL}"
add_env "HERMES_AGENT_NAME" "${PROFILE_NAME:-hermes}"
add_env "QDRANT_URL" "http://127.0.0.1:${QDRANT_HOST_PORT}"
add_env "REDIS_HOST" "127.0.0.1"
add_env "REDIS_PORT" "${REDIS_HOST_PORT}"
add_env "WIKI_ROOT" "${VAULT_PATH}/wiki"
add_env "WORKER_WIKI_ROOT" "/wiki"
add_env "REDIS_PASSWORD" "${REDIS_PW}"
add_env "OPENROUTER_API_KEY" "${OPENROUTER_KEY}"

ok "Environment variables added to Hermes .env"

# ──────────────────────────────────────────────────────────────────────────────
# Phase 9: Rulebook Modifications
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 9: Rulebook"

SOUL_FILE="${HERMES_HOME}/SOUL.md"
RULEBOOK="${HERMES_HOME}/rulebook.md"
PROTOCOL_FILE="${REPO_DIR}/modifications/execution-agent-protocol.md"
MARKER="Mandatory Pre-Action Protocol"

if [ ! -f "${PROTOCOL_FILE}" ]; then
    warn "execution-agent-protocol.md not found — skipping modifications"
else
    # Try SOUL.md first — behavioral tests show 3/6 compliance when protocol
    # is in SOUL.md vs 0/6 when it is only in rulebook.md.
    if [ -f "${SOUL_FILE}" ]; then
        if grep -q "${MARKER}" "${SOUL_FILE}" 2>/dev/null; then
            ok "Mandatory Pre-Action Protocol already in SOUL.md"
        else
            echo "" >> "${SOUL_FILE}"
            echo "<!-- Memory OS additions — do not duplicate -->" >> "${SOUL_FILE}"
            cat "${PROTOCOL_FILE}" >> "${SOUL_FILE}"
            ok "Mandatory Pre-Action Protocol appended to SOUL.md"
        fi
    elif [ -f "${RULEBOOK}" ]; then
        if grep -q "${MARKER}" "${RULEBOOK}" 2>/dev/null; then
            ok "Rulebook amendments already applied"
        else
            echo "" >> "${RULEBOOK}"
            cat "${PROTOCOL_FILE}" >> "${RULEBOOK}"
            ok "Mandatory Pre-Action Protocol appended to rulebook"
        fi
    else
        warn "Neither SOUL.md nor rulebook.md found"
        info "To install the protocol manually:"
        info "  cat ${PROTOCOL_FILE} >> ${HERMES_HOME}/SOUL.md"
    fi
fi

# ──────────────────────────────────────────────────────────────────────────────
# Phase 10: Gateway
# ──────────────────────────────────────────────────────────────────────────────
banner "Phase 10: Gateway"

if command -v hermes >/dev/null 2>&1; then
    # `hermes gateway restart` on an installation without a gateway *service*
    # starts the gateway in the foreground and never returns, which used to hang
    # this script before it printed its summary. Only a real service is
    # restarted, and even that is bounded.
    if systemctl --user list-unit-files 2>/dev/null | grep -q "^hermes-gateway"; then
        info "Restarting the Hermes gateway service..."
        if timeout 120 hermes gateway restart >/dev/null 2>&1; then
            ok "Gateway restarted"
        else
            warn "Gateway restart failed — run: hermes gateway restart"
        fi
    else
        info "No Hermes gateway service installed — nothing to restart"
        info "Messaging and Hermes cron need it: hermes gateway install"
    fi
else
    warn "'hermes' command not available — install the gateway later with: hermes gateway install"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────────────
banner "Summary"

echo "  Passed:  ${PASS}"
echo "  Failed:  ${FAIL}"
echo "  Warnings: ${WARN}"
echo ""

if [ "${FAIL}" -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}✅ Memory OS installed successfully!${NC}"
    echo ""
    echo "  To verify:"
    echo "    • hermes plugins list → icarus enabled"
    echo "    • docker compose -f docker/docker-compose.yml --env-file ${DOCKER_ENV_FILE} -p ${COMPOSE_PROJECT_NAME} ps → 3 services (redis, qdrant, worker)"
    echo "    • fabric_brief()    → fabric entries (initially empty)"
    echo "    • qdrant_search()   → semantic search (requires populated wiki)"
    echo ""
    echo "  Next step: add .md files to ${VAULT_PATH}/wiki/raw/"
    echo "  and the ingestion pipeline will index them automatically."
    echo ""
else
    echo -e "  ${RED}${BOLD}❌ ${FAIL} error(s) found — review the output above.${NC}"
    exit 1
fi
