#!/usr/bin/env bash
# Memory OS — wrapper para rodar scripts de manutenção com o ambiente do Hermes
# Uso: run-script.sh <script.py> [args...]
# Carrega /home/crdrews/.hermes/.env e executa o script com o venv do Memory OS.
set -euo pipefail
VENV_PY=/home/crdrews/Work/memory-os/.venv/bin/python
exec "$VENV_PY" - "$@" <<'PYEOF'
import sys, runpy, os
from pathlib import Path
from dotenv import load_dotenv

env = Path('/home/crdrews/.hermes/.env')
if env.exists():
    load_dotenv(env, override=True)

script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
# scripts/ faz parte do caminho de import para alguns utilitários
os.environ.setdefault('PYTHONPATH', str(Path(script).resolve().parent.parent))
runpy.run_path(script, run_name='__main__')
PYEOF
