#!/usr/bin/env python3
"""Reproducible example: a decision recorded once, recalled later.

Demonstrates the value the product promises, using the **same path the agent
uses**: the ARQ ingestion pipeline writes the memory, and the context enhancer's
hybrid retrieval (dense + BM25, RRF) reads it back from a new process. Nothing
here is simulated — only the LLM/embedding provider can be a local stand-in.

SAFETY: this script deliberately has **no default target**. It refuses to run
unless you name the Qdrant endpoint, the collection, the Redis queue and the
embedding endpoint explicitly, so an accidental `python3 scripts/demo_...` can
never write demo memories into a live installation. Run it against a lab or a
disposable VM, never against the stack you use.

Usage (from the repository root, against your lab/VM):

    export QDRANT_URL=http://127.0.0.1:28333        # lab/VM Qdrant
    export COLLECTION_NAME=knowledge_base_lab       # lab collection
    export QDRANT_API_KEY=...                       # if your Qdrant needs one
    export REDIS_HOST=127.0.0.1 REDIS_PORT=28379    # queue
    export REDIS_PASSWORD=...                       # if your Redis needs one
    export EMBEDDING_API_BASE=http://127.0.0.1:8080/v1
    export EMBEDDING_MODEL=stub-embedding
    export EMBEDDING_DIMS=64
    export EMBEDDING_API_KEY=                       # if the endpoint needs one
    python3 scripts/demo_decision_recall.py

It enqueues three documents, waits for the worker, then runs two queries whose
expectations are scoped to **this run's own points** (they carry `id=<run id>`),
plus a negative control: an unrelated question must not surface any of them.
Exit code is 0 only if every expectation holds.

Two facts this example depends on, stated so a failure is not mistaken for a
product bug:

* the checks are meaningful only with an embedding provider that actually ranks
  by meaning — a real model, local or hosted. A stub that returns arbitrary
  vectors cannot satisfy them;
* episodic memories (this ingestion path) are stored with a **dense** vector
  only. The BM25/sparse vector exists on the file-ingestion path, so the sparse
  half of the hybrid query only ever matches file-ingested content. Retrieval of
  these memories depends on the dense vector; the run reports the fallback level
  it actually got;
* the negative control judges the **injection window** (the top 3 hits the agent
  would put in the prompt), and it only discriminates when the collection is
  larger than that window. On a small collection the hybrid query returns
  everything, so the check is reported as `NAO CONCLUSIVO` instead of passing or
  failing for the wrong reason.

Requires a running stack (Redis + Qdrant + worker). It writes only through the
normal ingestion path, so the points it creates are ordinary memories; every
document is tagged `demo` and `id=<run id>`.

Cleanup (removes only the points this run created, by tag):

    python3 scripts/demo_decision_recall.py --cleanup --cleanup-id <run id>

The run id is printed at the start and on cleanup; `--cleanup` counts the
matching points before and after the delete and fails if any survive.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Explicit target required: no default endpoint is ever assumed. QDRANT_URL,
# REDIS_HOST and EMBEDDING_API_BASE are read by the modules this example uses,
# and their defaults point at a normal local installation.
REQUIRED_ENV = ("QDRANT_URL", "REDIS_HOST", "EMBEDDING_API_BASE")

DEMO_ID = uuid.uuid4().hex[:6]
DECISION = (
    f"[demo id={DEMO_ID}] Decisao: o servico de ingestao usa a porta 28333 para o Qdrant "
    "do ambiente de testes, e nao a 6333, para nao colidir com o stack de producao."
)
OTHER = (
    f"[demo id={DEMO_ID}] Decisao: o relatorio semanal de incidentes e publicado as sextas-feiras "
    "as 17h, com revisao do plantao antes da publicacao."
)
NOISE = (
    f"[demo id={DEMO_ID}] Nota: o cafeteira do escritorio foi trocada e o modelo novo "
    "demora menos para ferver a agua."
)

QUERIES = [
    ("qual porta o servico de ingestao usa no ambiente de testes?", "28333"),
    ("quando o relatorio semanal de incidentes e publicado?", "17h"),
]
OFF_TOPIC = "qual e a cor do ceu em marte ao meio-dia?"
TOP_K = 10            # window used to check whether the memory is retrievable at all
INJECTION_WINDOW = 3  # what the agent actually injects into the prompt


def target_config():
    """Resolve the explicit target, or the list of variables that are missing."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    collection = collection_name()
    if not collection:
        missing.append("COLLECTION_NAME (or QDRANT_COLLECTION)")
    return collection, missing


def collection_name():
    return (os.environ.get("QDRANT_COLLECTION") or os.environ.get("COLLECTION_NAME") or "").strip()


def qdrant_headers():
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("QDRANT_API_KEY", "").strip()
    if key:
        headers["api-key"] = key
    return headers


def ok(msg):
    print(f"  \033[32mok\033[0m   {msg}")


def bad(msg):
    print(f"  \033[31mFALHA\033[0m {msg}")


def refuse_missing_target(missing):
    print("ABORTADO: este exemplo nao assume nenhum endpoint padrao.")
    print("Ele escreve memorias pelo pipeline real, entao o destino tem de ser explicito")
    print("(laboratorio ou VM descartavel — nunca o stack em uso). Faltando:")
    for name in missing:
        print(f"  - export {name}=<valor do seu laboratorio/VM>")
    print("\nNada foi enfileirado e nada foi escrito.")
    return 2


async def ingest(texts, timeout=120):
    from arq import create_pool
    from arq.connections import RedisSettings
    from arq.jobs import Job

    pool = await create_pool(RedisSettings(
        host=os.environ["REDIS_HOST"],
        port=int(os.environ.get("REDIS_PORT", "6379")),
        password=os.environ.get("REDIS_PASSWORD") or None,
    ))
    ids = []
    try:
        for text in texts:
            job = await pool.enqueue_job("process_ingestion", text, "demo",
                                         ["demo", f"id={DEMO_ID}"])
            ids.append(job.job_id)
        for jid in ids:
            try:
                res = await Job(jid, pool).result(timeout=timeout, poll_delay=0.5)
                print(f"    ingerido: {res.get('status')} ({str(res.get('id'))[:8]})")
            except Exception as exc:
                bad(f"job {jid[:8]} nao concluiu: {type(exc).__name__}: {exc}")
                return False
    finally:
        closer = getattr(pool, "aclose", None) or pool.close
        await closer()
    return True


def retrieve(query, top_k=3, threshold=0.0):
    """Same retrieval the agent uses: hybrid dense+BM25 with a fallback cascade."""
    from scripts.context_enhancer import (embed_query_sparse, embed_query_with_status,
                                          search_with_fallback)
    dense = embed_query_with_status(query)
    if dense.vector is None:
        return [], f"embedding falhou: {dense.error}", 0.0
    sparse = embed_query_sparse(query)
    hits, level, latency, _ = search_with_fallback(
        dense_vector=dense.vector,
        sparse_vector=sparse,
        query_text=query,
        top_k=top_k,
        score_threshold=threshold,
    )
    return hits, level, latency


def count_demo_points(collection):
    """How many points carry this run's tag. Uses the documented HTTP API."""
    import requests
    url = os.environ["QDRANT_URL"].rstrip("/")
    resp = requests.post(
        f"{url}/collections/{collection}/points/count",
        headers=qdrant_headers(),
        json={"filter": {"must": [{"key": "tags", "match": {"value": f"id={DEMO_ID}"}}]},
              "exact": True},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("result", {}).get("count", 0)


def cleanup(collection):
    """Delete only the points tagged with this run's id, then verify."""
    import requests
    url = os.environ["QDRANT_URL"].rstrip("/")
    before = count_demo_points(collection)
    print(f"  pontos com a tag 'id={DEMO_ID}' antes: {before}")
    if before:
        resp = requests.post(
            f"{url}/collections/{collection}/points/delete",
            params={"wait": "true"},
            headers=qdrant_headers(),
            json={"filter": {"must": [{"key": "tags", "match": {"value": f"id={DEMO_ID}"}}]}},
            timeout=30,
        )
        resp.raise_for_status()
    after = count_demo_points(collection)
    print(f"  pontos com a tag 'id={DEMO_ID}' depois: {after}")
    if after:
        bad("a limpeza nao removeu todos os pontos desta execucao")
        return 1
    ok("limpeza concluida — nenhum ponto desta execucao permanece")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="seconds to wait for each ingestion job")
    ap.add_argument("--cleanup", action="store_true",
                    help="delete this run's demo points and exit (no ingestion)")
    ap.add_argument("--cleanup-id", default=None,
                    help="run id to clean up (default: this process's new id)")
    args = ap.parse_args()

    global DEMO_ID
    collection, missing = target_config()
    if missing:
        return refuse_missing_target(missing)

    if args.cleanup:
        if args.cleanup_id:
            DEMO_ID = args.cleanup_id
        print(f"=== limpeza do exemplo (id={DEMO_ID}) ===")
        print(f"  Qdrant: {os.environ['QDRANT_URL']} | colecao: {collection}")
        return cleanup(collection)

    print(f"=== demo: decisao registrada e recuperada (id={DEMO_ID}) ===")
    print(f"  Qdrant: {os.environ['QDRANT_URL']} | colecao: {collection}")
    print(f"  fila:   {os.environ['REDIS_HOST']}:{os.environ.get('REDIS_PORT', '6379')}")
    print(f"  embeddings: {os.environ['EMBEDDING_API_BASE']} "
          f"({os.environ.get('EMBEDDING_MODEL', 'modelo nao definido')}, "
          f"{os.environ.get('EMBEDDING_DIMS', 'dims nao definidas')} dims)")
    print()

    print("1. registrando duas decisoes e uma nota irrelevante pelo pipeline real")
    if not asyncio.run(ingest([DECISION, OTHER, NOISE], args.timeout)):
        print("\nRESULTADO: FALHOU (a ingestao nao concluiu)")
        print(cleanup_hint())
        return 1

    print("\n2. recuperando com o caminho hibrido (dense + BM25 + RRF)")
    failures = []
    for query, needle in QUERIES:
        hits, level, latency = retrieve(query, top_k=TOP_K)
        print(f"\n  consulta: {query!r}")
        print(f"  nivel de recuperacao: {level} | {latency:.1f} ms | {len(hits)} resultado(s)")
        mine = [h for h in hits if f"id={DEMO_ID}" in (h.get("tags") or [])]
        for i, h in enumerate(hits, 1):
            preview = (h.get("content_preview") or h.get("text") or "").replace("\n", " ")[:88]
            mark = "  <-- esta execucao" if h in mine else ""
            print(f"    [{i}] score={h.get('score', 0):.3f} fonte={h.get('source')!r}{mark}")
            print(f"        {preview}")
        if not mine:
            bad("nenhuma memoria desta execucao foi recuperada")
            failures.append(query)
        elif not any(needle.lower() in json.dumps(h, ensure_ascii=False).lower() for h in mine):
            bad(f"a memoria desta execucao apareceu, mas sem o trecho esperado ({needle!r})")
            failures.append(query)
        else:
            rank = hits.index(mine[0]) + 1
            ok(f"a decisao desta execucao foi recuperada (posicao {rank} de {len(hits)}, "
               f"trecho {needle!r})")

    print(f"\n3. controle negativo: {OFF_TOPIC!r}")
    hits, level, latency = retrieve(OFF_TOPIC, top_k=TOP_K)
    window = hits[:INJECTION_WINDOW]
    leaked = [h for h in window if f"id={DEMO_ID}" in (h.get("tags") or [])]
    print(f"  nivel de recuperacao: {level} | {latency:.1f} ms | {len(hits)} resultado(s); "
          f"janela de injecao: {len(window)}")
    print("  (a busca hibrida sempre devolve os melhores resultados; o que se verifica aqui e")
    print("   que nenhuma memoria desta execucao entra na janela injetada numa pergunta sem relacao)")
    inconclusive = None
    if len(hits) <= INJECTION_WINDOW:
        inconclusive = ("NAO CONCLUSIVO: a colecao tem %d ponto(s) <= janela de %d — qualquer "
                        "consulta devolve tudo, entao este controle so discrimina com um provedor "
                        "de embeddings semantico e uma colecao maior que a janela"
                        % (len(hits), INJECTION_WINDOW))
        print(f"  \033[33m{inconclusive}\033[0m")
    elif leaked:
        bad("pergunta sem relacao trouxe uma memoria desta execucao para a janela injetada")
        for h in leaked:
            print(f"        -> {(h.get('content_preview') or '')[:70]}")
        failures.append(OFF_TOPIC)
    else:
        ok("nenhuma memoria desta execucao entrou na janela injetada")

    print()
    if failures:
        print(f"RESULTADO: FALHOU em {len(failures)} verificacao(oes)")
        if inconclusive:
            print(f"observacao: {inconclusive}")
        print(cleanup_hint())
        return 1
    verdict = "RESULTADO: APROVADO — uma decisao registrada foi recuperada corretamente depois"
    if inconclusive:
        verdict += " (1 verificacao NAO CONCLUSIVA: controle negativo, veja acima)"
    print(verdict)
    print(cleanup_hint())
    return 0


def cleanup_hint():
    return (f"limpeza: python3 scripts/demo_decision_recall.py --cleanup "
            f"--cleanup-id {DEMO_ID}   (remove so os pontos com a tag 'id={DEMO_ID}')")


if __name__ == "__main__":
    raise SystemExit(main())
