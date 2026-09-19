"""Optional local wiki/document memory (adapted from Lucidus recovery): FTS5 + persistent embedded Qdrant.

No Docker, Redis, reflection job or agent session is needed to index files.
Embeddings keep the original Qwen 4096d representation. Unavailable network
degrades retrieval to FTS, never advances the vector completion checkpoint.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

ROOT = Path(os.environ.get('MEMORY_OS_LOCAL_ROOT', Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')) / 'local-memory')).expanduser()
DATA = ROOT / 'memory-index'
MODEL = os.environ.get('EMBEDDING_MODEL', 'qwen/qwen3-embedding-8b')
DIMS = int(os.environ.get('EMBEDDING_DIMS', '4096'))
COLLECTION = 'local_knowledge'


def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / 'sources.sqlite3', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE IF NOT EXISTS files (source TEXT PRIMARY KEY, hash TEXT, vector_hash TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, source TEXT, text TEXT)')
    con.execute('CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, source UNINDEXED, text)')
    return con


@contextlib.contextmanager
def vector_client(nonblocking=False):
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / 'vectors.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        client = QdrantClient(path=str(DATA / 'qdrant'))
        try:
            if not client.collection_exists(COLLECTION):
                client.create_collection(COLLECTION, vectors_config=VectorParams(size=DIMS, distance=Distance.COSINE))
            yield client
        finally:
            client.close()


def _request_embeddings(texts):
    import httpx
    base = os.environ.get('EMBEDDING_API_BASE', 'https://openrouter.ai/api/v1').rstrip('/')
    from urllib.parse import urlsplit
    hosted = urlsplit(base).hostname == 'openrouter.ai'
    key = os.environ.get('OPENROUTER_API_KEY') if hosted else os.environ.get('EMBEDDING_API_KEY')
    if hosted and not key:
        raise RuntimeError('Embedding credential unavailable')
    with httpx.Client(timeout=180) as client:
        r = client.post(base + '/embeddings',
                        headers={'Authorization': 'Bearer ' + key} if key else {},
                        json={'model': MODEL, 'input': texts, 'dimensions': DIMS})
        # Do not expose response bodies or credentials in operational reports.
        if r.status_code != 200:
            raise RuntimeError('Embedding API HTTP ' + str(r.status_code))
        data = sorted(r.json()['data'], key=lambda x: x['index'])
        vectors = [x['embedding'] for x in data]
        if len(vectors) != len(texts) or any(len(v) != DIMS for v in vectors):
            raise RuntimeError('Invalid embedding dimensions/count')
        return vectors


def embed(texts):
    # Persistent per-content cache permits small batched API calls and retries.
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / 'embedding-cache.sqlite3', timeout=60)
    con.execute('CREATE TABLE IF NOT EXISTS vectors(hash TEXT PRIMARY KEY,vector TEXT)')
    keys = [hashlib.sha256((MODEL + str(DIMS) + t).encode()).hexdigest() for t in texts]
    found = {}
    for key in keys:
        row = con.execute('SELECT vector FROM vectors WHERE hash=?',(key,)).fetchone()
        if row: found[key] = json.loads(row[0])
    missing = list(dict.fromkeys(k for k in keys if k not in found))
    if missing:
        lookup = dict(zip(keys,texts))
        import time
        for attempt in range(3):
            try:
                vectors = _request_embeddings([lookup[k] for k in missing])
                break
            except Exception:
                if attempt == 2:
                    con.close()
                    raise
                time.sleep(2 ** attempt)
        with con:
            con.executemany('INSERT OR REPLACE INTO vectors VALUES (?,?)',
                            [(k,json.dumps(v)) for k,v in zip(missing,vectors)])
        found.update(zip(missing,vectors))
    con.close()
    return [found[k] for k in keys]


def prefetch(listing, con):
    from concurrent.futures import ThreadPoolExecutor
    unique = {}
    for source,p in listing:
        text=p.read_text(encoding='utf-8',errors='replace')
        digest=hashlib.sha256(text.encode()).hexdigest()
        vh=hashlib.sha256((digest+MODEL+str(DIMS)).encode()).hexdigest()
        row=con.execute('SELECT vector_hash FROM files WHERE source=?',(source,)).fetchone()
        if row and row[0]==vh: continue
        for chunk in split_text(text): unique[hashlib.sha256(chunk.encode()).hexdigest()]=chunk
    texts=list(unique.values())
    batches=[texts[i:i+8] for i in range(0,len(texts),8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,_ in enumerate(pool.map(embed,batches),1):
            if i%10==0: print(json.dumps({'embedding_batches':i,'total_batches':len(batches)}),flush=True)


def split_text(text, size=2400, overlap=200):
    return [text[i:i+size] for i in range(0, len(text), size-overlap) if text[i:i+size].strip()]


def sources():
    for label, root in [('wiki', ROOT / 'wiki'), ('documents', ROOT / 'documents')]:
        if not root.exists():
            raise RuntimeError('Missing source root: ' + str(root))
        for p in sorted(root.rglob('*.md')):
            rel = p.relative_to(root)
            if p.is_symlink() or any(x in {'.trash', '.obsidian', '_archive', 'fabric'} for x in rel.parts):
                continue
            if p.name.lower() in {'index.md','log.md','scope_audit.md'}:
                continue
            yield label + '/' + rel.as_posix(), p


def sync(lexical_only=False, limit=None):
    """Checkpoint only completed writes. A second unchanged run performs zero embeddings."""
    DATA.mkdir(parents=True, exist_ok=True)
    stats = {'indexed': 0, 'vectors': 0, 'unchanged': 0, 'deleted': 0, 'failed': 0}
    with open(DATA / 'sync.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        listing = list(sources())
        seen = {source for source, _ in listing}
        con = connection()
        stack = contextlib.ExitStack()
        try:
            if not lexical_only and limit is None:
                try:
                    prefetch(listing, con)
                except Exception as exc:
                    stats['failed'] += 1
                    print(json.dumps({'error': str(exc)[:160]}), flush=True)
                    return stats
            vector_store = None
            for source, p in listing:
                text = p.read_text(encoding='utf-8', errors='replace')
                digest = hashlib.sha256(text.encode()).hexdigest()
                vh = hashlib.sha256((digest + MODEL + str(DIMS)).encode()).hexdigest()
                old = con.execute('SELECT * FROM files WHERE source=?', (source,)).fetchone()
                if old and old['hash'] == digest and (lexical_only or old['vector_hash'] == vh):
                    stats['unchanged'] += 1
                    continue
                if limit is not None and stats['indexed'] >= limit:
                    continue
                chunks = split_text(text)
                ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, 'memory-os-local:' + source + ':' + str(i))) for i in range(len(chunks))]
                if not lexical_only:
                    try:
                        from qdrant_client.models import PointStruct, PointIdsList
                        vectors = []
                        for i in range(0, len(chunks), 16):
                            vectors.extend(embed(chunks[i:i+16]))
                        previous = {r[0] for r in con.execute('SELECT id FROM chunks WHERE source=?', (source,))}
                        if vector_store is None:
                            vector_store = stack.enter_context(vector_client())
                        with contextlib.nullcontext(vector_store) as q:
                            if ids:
                                q.upsert(COLLECTION, points=[PointStruct(id=i, vector=v, payload={
                                    'source': source, 'text': t, 'source_type': 'unknown', 'content_hash': digest
                                }) for i,t,v in zip(ids,chunks,vectors)], wait=True)
                            stale = list(previous-set(ids))
                            if stale:
                                q.delete(COLLECTION, points_selector=PointIdsList(points=stale), wait=True)
                        stats['vectors'] += len(ids)
                    except Exception as exc:
                        stats['failed'] += 1
                        print(json.dumps({'source':source,'error':str(exc)[:160]}), flush=True)
                        # Stop a failing API from being called for the whole corpus.
                        break
                with con:
                    con.execute('DELETE FROM chunks WHERE source=?', (source,))
                    con.execute('DELETE FROM chunks_fts WHERE source=?', (source,))
                    con.executemany('INSERT INTO chunks VALUES (?,?,?)', zip(ids,[source]*len(ids),chunks))
                    con.executemany('INSERT INTO chunks_fts VALUES (?,?,?)', zip(ids,[source]*len(ids),chunks))
                    con.execute('INSERT OR REPLACE INTO files VALUES (?,?,?)', (source,digest, None if lexical_only else vh))
                stats['indexed'] += 1
                if stats['indexed'] % 25 == 0:
                    print(json.dumps(stats), flush=True)
            # Delete from vectors only in vector sync, avoiding stale semantic results.
            if not lexical_only and not stats['failed']:
                from qdrant_client.models import PointIdsList
                for row in list(con.execute('SELECT source FROM files')):
                    if row['source'] in seen:
                        continue
                    ids = [r[0] for r in con.execute('SELECT id FROM chunks WHERE source=?', (row['source'],))]
                    if ids:
                        if vector_store is None:
                            vector_store = stack.enter_context(vector_client())
                        with contextlib.nullcontext(vector_store) as q:
                            q.delete(COLLECTION, points_selector=PointIdsList(points=ids), wait=True)
                    with con:
                        for table in ['files','chunks','chunks_fts']:
                            con.execute('DELETE FROM '+table+' WHERE source=?',(row['source'],))
                    stats['deleted'] += 1
        finally:
            stack.close()
            con.close()
    return stats


def search(query, limit=3, semantic=None):
    if not (DATA / 'sources.sqlite3').exists():
        return []
    stop = set('para com que uma como dos das por sobre este esta esse essa isso isso sua seu seus suas qual quais diga teste recuperação leitura apenas based the and with from this that what which you your'.split())
    tokens = list(dict.fromkeys(t for t in re.findall(r'[^\W_]{3,}', query.casefold(), flags=re.UNICODE) if t not in stop))[:60]
    if not tokens:
        return []
    con = connection()
    try:
        # Query vocabulary selects meaningful terms even in a long instruction.
        con.execute('CREATE VIRTUAL TABLE IF NOT EXISTS vocabulary USING fts5vocab(chunks_fts, row)')
        frequencies = {t: con.execute('SELECT doc FROM vocabulary WHERE term=?',(t,)).fetchone() for t in tokens}
        tokens = sorted((t for t in tokens if frequencies[t]), key=lambda t: (not any(c.isdigit() for c in t), frequencies[t][0]))[:12]
        # A semantic query need not have lexical matches.
        rows = con.execute('SELECT id,source,text FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 12',
                           (' OR '.join('"'+t+'"' for t in tokens),)).fetchall() if tokens else []
        results = {r['id']:dict(r) for r in rows}
        scores = {r['id']:1/(60+i) for i,r in enumerate(rows,1)}
        if semantic is None:
            semantic = os.environ.get('MEMORY_OS_LOCAL_SEMANTIC_SEARCH','1') == '1'
        if semantic and (DATA / 'qdrant').exists():
            try:
                vector = embed([query])[0]
                with vector_client(nonblocking=True) as q:
                    points = q.query_points(COLLECTION, query=vector, limit=12).points
                for i,p in enumerate(points,1):
                    # Ignore stale/deleted vectors when lexical state has newer content.
                    valid = con.execute('SELECT 1 FROM files WHERE source=? AND hash=?',
                                        (p.payload['source'],p.payload['content_hash'])).fetchone()
                    if not valid:
                        continue
                    key = str(p.id)
                    results[key] = {'id':key,'source':p.payload['source'],'text':p.payload['text']}
                    scores[key] = scores.get(key,0)+1/(60+i)
            except Exception:
                pass  # FTS remains available offline.
        return [results[k] for k in sorted(scores,key=scores.get,reverse=True)[:limit]]
    finally:
        con.close()


def main():
    from dotenv import load_dotenv
    profile = Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')).expanduser()
    load_dotenv(profile / '.env', override=False)
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['sync','search'])
    parser.add_argument('query',nargs='?',default='')
    parser.add_argument('--lexical-only',action='store_true')
    parser.add_argument('--limit',type=int)
    args = parser.parse_args()
    result = sync(args.lexical_only,args.limit) if args.command=='sync' else search(args.query)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if isinstance(result,dict) and result.get('failed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
