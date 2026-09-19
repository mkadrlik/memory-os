#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MEMORY_OS_PYTHON="${MEMORY_OS_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
if [ "$#" -lt 1 ]; then
    echo "Usage: run-script.sh <script.py> [args...]" >&2
    exit 2
fi
exec "$MEMORY_OS_PYTHON" - "$@" <<'PYCODE'
import sys, runpy, os
from pathlib import Path
from dotenv import load_dotenv
home = Path(os.environ.get('HERMES_HOME', '').strip() or Path.home() / '.hermes').expanduser()
load_dotenv(home / '.env', override=False)
script = Path(sys.argv[1]).resolve()
sys.argv = [str(script)] + sys.argv[2:]
sys.path.insert(0, str(script.parent))
sys.path.insert(0, str(script.parent.parent))
runpy.run_path(str(script), run_name='__main__')
PYCODE
