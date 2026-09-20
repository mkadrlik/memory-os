#!/usr/bin/env bash
# Memory OS Smoke Test
# Verifies the entire stack is functional without needing to know what to check.
#
# Usage:
#   ./setup/smoke_test.sh              # All checks
#   ./setup/smoke_test.sh --quick       # Skip ingestion test (faster)
#   ./setup/smoke_test.sh --help        # Show help
#
# Environment:
#   REDIS_PASSWORD        Redis password
#   QDRANT_API_KEY        Qdrant API key (default: "")
#   REDIS_HOST            Redis host (default: localhost)
#   REDIS_PORT            Redis port (default: 6379)
#   QDRANT_HOST           Qdrant host (default: localhost)
#   QDRANT_PORT           Qdrant port (default: 6333)
#   COLLECTION_NAME       Qdrant collection (default: knowledge_base)

set -euo pipefail

PASS=0
FAIL=0
QUICK_MODE=false

for arg in "$@"; do
    case "$arg" in
        --quick) QUICK_MODE=true ;;
        --help)  echo "Usage: ./setup/smoke_test.sh [--quick]"; exit 0 ;;
    esac
done

RED=''
GREEN=''
NC=''
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    NC='\033[0m'
fi

check() {
    local label="$1"
    local cmd="$2"
    printf "  %-40s " "$label"
    if eval "$cmd" >/dev/null 2>&1; then
        printf "${GREEN}✅${NC}\n"
        PASS=$((PASS + 1))
    else
        printf "${RED}❌${NC}\n"
        FAIL=$((FAIL + 1))
    fi
}

# ── Resolve env vars ─────────────────────────────────────────────────────────
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
QDRANT_HOST="${QDRANT_HOST:-localhost}"
QDRANT_PORT="${QDRANT_PORT:-6333}"
QDRANT_API_KEY="${QDRANT_API_KEY:-}"
COLLECTION_NAME="${COLLECTION_NAME:-knowledge_base}"
# The profile directory is not always ~/.hermes (named profiles live under
# <root>/profiles/<name>), so honour HERMES_HOME instead of assuming the default.
HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"
EMBEDDING_DIMS="${EMBEDDING_DIMS:-4096}"

echo "=== Memory OS Smoke Test ==="
echo "  Redis:  ${REDIS_HOST}:${REDIS_PORT}"
echo "  Qdrant: ${QDRANT_HOST}:${QDRANT_PORT}"
echo "  Collection: ${COLLECTION_NAME}"
echo "  Profile:    ${HERMES_HOME}"
echo "  Embed dims: ${EMBEDDING_DIMS}"
echo ""

# ── 1. Infrastructure ────────────────────────────────────────────────────────
echo "── Infrastructure ──"

check "Docker running" \
    "docker info"

check "Redis reachable" \
    "python3 -c \"
import redis
r = redis.Redis(host='${REDIS_HOST}', port=${REDIS_PORT},
                password='${REDIS_PASSWORD}' or None)
assert r.ping()
\""

# Note: Qdrant healthcheck uses grep on /proc/net/tcp (shell built-in) because
# the qdrant/qdrant image does not include curl, wget, or python3.
# Port 6333 = 0x18BD in hex.
check "Qdrant health" \
    "python3 -c \"
from qdrant_client import QdrantClient
c = QdrantClient(host='${QDRANT_HOST}', port=${QDRANT_PORT},
                 api_key='${QDRANT_API_KEY}' or None, https=False)
collections = c.get_collections()
assert len(collections.collections) >= 1
\""

check "Qdrant collection '${COLLECTION_NAME}'" \
    "python3 -c \"
from qdrant_client import QdrantClient
c = QdrantClient(host='${QDRANT_HOST}', port=${QDRANT_PORT},
                 api_key='${QDRANT_API_KEY}' or None, https=False)
info = c.get_collection('${COLLECTION_NAME}')
assert info.config.params.vectors is not None
\""

# ── 2. Icarus plugin ─────────────────────────────────────────────────────────
echo ""
echo "── Icarus Plugin ──"

check "Icarus plugin installed" \
    "test -f \"${HERMES_HOME}/plugins/icarus/__init__.py\""

check "Icarus plugin loaded" \
    "hermes plugins show icarus 2>/dev/null | grep -q 'Status: enabled'"

# ── 3. Ingestion pipeline ────────────────────────────────────────────────────
# Runs before the embedding check: that check reads a stored vector, and on a
# fresh install the collection is empty until something has been ingested.
echo ""
echo "── Ingestion Pipeline ──"

if [ "$QUICK_MODE" = true ]; then
    echo "  (skipped — --quick mode)"
else
    check "End-to-end ingestion" \
        "python3 scripts/test_ingestion.py"
fi

# ── 4. Embedding ─────────────────────────────────────────────────────────────
echo ""
echo "── Embedding ──"

if [ "$QUICK_MODE" = true ]; then
    echo "  (skipped — --quick mode; nothing ingested yet)"
else
check "Embedding produces ${EMBEDDING_DIMS}d vectors" \
    "python3 << 'PYEOF'
from qdrant_client import QdrantClient
c = QdrantClient(host='${QDRANT_HOST}', port=${QDRANT_PORT},
                 api_key='${QDRANT_API_KEY}' or None, https=False)
points, _ = c.scroll('${COLLECTION_NAME}', limit=1, with_vectors=True)
assert len(points) > 0, 'no points found in collection'
assert len(points[0].vector['dense']) == ${EMBEDDING_DIMS}, \\
    f'expected ${EMBEDDING_DIMS} dims, got {len(points[0].vector[\"dense\"])}'
PYEOF"
fi

# ── 5. Cron jobs ─────────────────────────────────────────────────────────────
echo ""
echo "── Cron Jobs ──"

# setup.sh installs the hourly wiki watcher as a system crontab entry; extra
# `hermes cron` jobs are an optional, documented alternative (setup/install.md).
# Accept either so a clean install through the documented path is not reported
# as a failure.
check "Scheduled ingestion active (cron or timers)" \
    "crontab -l 2>/dev/null | grep -qF '# memory-os wiki watcher' || systemctl --user list-timers --all 2>/dev/null | grep -q 'memoryos-'"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────"
if [ "$QUICK_MODE" = true ]; then
    echo "Result (quick mode): $PASS passed, $FAIL failed, 2 skipped (ingestion, embedding)"
else
    echo "Result: $PASS passed, $FAIL failed"
fi

if [ "$FAIL" -eq 0 ]; then
    echo "✅ All checks passed — Memory OS is operational."
    exit 0
else
    echo "❌ $FAIL check(s) failed — review output above."
    exit 1
fi
