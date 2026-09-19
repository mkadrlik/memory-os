#!/usr/bin/env python3
"""Generate a native stack in an explicit staging directory; never install/start it."""
import argparse
import json
from pathlib import Path
import re
import sys


def quote(value):
    text = str(value)
    if '\n' in text or '\r' in text or '\0' in text:
        raise ValueError('Paths must not contain control characters')
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('%','%%').replace('$','$$') + '"'


def render(output, profile, wiki, repo, python, qdrant, redis, name='default', qdrant_port=6333, redis_port=6379):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
        raise ValueError('Service name must contain lowercase letters, numbers, hyphens or underscores')
    if qdrant_port == redis_port or not all(1024 <= p <= 65535 for p in (qdrant_port, redis_port)):
        raise ValueError('Choose distinct unprivileged ports')
    paths = [Path(p).expanduser().resolve() for p in (output,profile,wiki,repo,python,qdrant,redis)]
    output,profile,wiki,repo,python,qdrant,redis = paths
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory must be empty; existing configuration is never overwritten')
    for p in paths: quote(p)
    output.mkdir(parents=True, exist_ok=True)
    (output/'data/qdrant').mkdir(parents=True)
    (output/'data/redis').mkdir(parents=True)
    prefix='memoryos-'+name
    # JSON is valid YAML, with paths safely escaped.
    (output/'qdrant.yaml').write_text(json.dumps({'storage':{'storage_path':str(output/'data/qdrant')}, 'service':{'host':'127.0.0.1','http_port':qdrant_port,'grpc_port':None}},indent=2)+'\n')
    (output/'redis.conf').write_text(f'bind 127.0.0.1\nprotected-mode yes\nport {redis_port}\nappendonly yes\nmaxmemory 512mb\nmaxmemory-policy noeviction\ndir '+json.dumps(str(output/'data/redis'))+'\n')
    env={'HERMES_HOME':str(profile),'STATE_DB_PATH':str(profile/'state.db'),'WIKI_PATH':str(wiki),'WIKI_ROOT':str(wiki),'WORKER_WIKI_ROOT':str(wiki),'REDIS_HOST':'127.0.0.1','REDIS_PORT':str(redis_port),'QDRANT_HOST':'127.0.0.1','QDRANT_PORT':str(qdrant_port),'QDRANT_URL':f'http://127.0.0.1:{qdrant_port}','COLLECTION_NAME':'knowledge_base','EMBEDDING_API_BASE':'https://openrouter.ai/api/v1','EMBEDDING_MODEL':'qwen/qwen3-embedding-8b','EMBEDDING_DIMS':'4096','LLM_BACKEND':'openrouter','OPENROUTER_API_KEY':'','OPENROUTER_MODEL':'deepseek/deepseek-v4.1-flash'}
    (output/'worker.env').write_text(''.join(k+'='+json.dumps(v)+'\n' for k,v in env.items()))
    (output/'worker.env').chmod(0o600)
    commands={'qdrant':f'{quote(qdrant)} --config-path {quote(output/"qdrant.yaml")}', 'redis':f'{quote(redis)} {quote(output/"redis.conf")}', 'worker':f'{quote(python)} {quote(repo/"docker/worker/main.py")} --run-worker'}
    for kind, command in commands.items():
        dependency=f'Requires={prefix}-qdrant.service {prefix}-redis.service\nAfter={prefix}-qdrant.service {prefix}-redis.service\n' if kind=='worker' else ''
        (output/f'{prefix}-{kind}.service').write_text(f'[Unit]\nDescription=Memory OS {name} {kind}\n'+dependency+f'\n[Service]\nType=simple\nWorkingDirectory={quote(repo/"docker/worker" if kind=="worker" else output)}\nEnvironmentFile={quote(output/"worker.env")}\nExecStart={command}\nRestart=on-failure\nRestartSec=5\nUMask=0077\n\n[Install]\nWantedBy=default.target\n')
    (output/'README.txt').write_text('Staged configuration only. No services were installed or started.\nFill worker.env credentials; initialize the profile databases with setup/setup_db.py.\nInstall dependencies in a dedicated repo .venv and verify the configured binary paths.\nCopy the three .service files to your user systemd directory, then daemon-reload and enable --now them when deployment is authorized.\nThe paths in these units refer to this staging directory: retain it or regenerate for the final location.\nConfigure the same HERMES_HOME and worker.env for host ingestion/reflection commands. No timers are installed.\n')
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('output','profile','wiki','qdrant','redis'):p.add_argument('--'+arg,required=True,type=Path)
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--python',type=Path)
    p.add_argument('--name',default='default');p.add_argument('--qdrant-port',type=int,default=6333);p.add_argument('--redis-port',type=int,default=6379)
    args=vars(p.parse_args());args['python']=args['python'] or args['repo']/'.venv/bin/python'
    print(render(**args))
if __name__=='__main__':main()
