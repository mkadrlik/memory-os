#!/usr/bin/env python3
"""Run offline regression tests using this interpreter, with temporary profile paths."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory(prefix='memory-os-tests-') as temp:
        env=dict(os.environ)
        # Tests never use credentials or profile data inherited from a live agent.
        for key in list(env):
            if any(word in key for word in ('API_KEY','TOKEN','PASSWORD')) or key.startswith(('ICARUS_','HERMES_','FABRIC_','MEMORY_OS_','EMBEDDING_','QDRANT_','REDIS_')):
                env.pop(key,None)
        env.update(HERMES_HOME=temp, FABRIC_DIR=str(Path(temp)/'fabric'), OPENROUTER_API_KEY='offline-test', PYTHONDONTWRITEBYTECODE='1')
        commands=[['-m','unittest','discover','-s','tests','-v'],['_test_collapse.py'],['_test_sanitize.py'],['_test_local_embeddings.py'],['_test_hermes_env.py'],['_test_setup_profile.py'],['docker/worker/tasks/_test_path_containment.py']]
        failed=[]
        for command in commands:
            child=dict(env)
            if command==['_test_hermes_env.py']:
                child.pop('FABRIC_DIR',None)
            if command==['_test_setup_profile.py']:
                child.pop('HERMES_HOME',None)
            print('\nRUN '+ ' '.join(command),flush=True)
            p=subprocess.run([sys.executable,*command],cwd=ROOT,env=child)
            if p.returncode:failed.append(command)
        if failed:
            print('FAILED:',failed);return 1
        print('All offline suites passed.');return 0
if __name__=='__main__':raise SystemExit(main())
