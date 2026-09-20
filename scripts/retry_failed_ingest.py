#!/usr/bin/env python3
"""Retry pontual da ingestão da wiki — os 7 arquivos que falharam no bulk inicial.

Motivo de existir: bulk_wiki_ingest.py gera ids com uuid4(), então re-rodá-lo
inteiro DUPLICARIA os 808 pontos já indexados. Este script reingere apenas os
arquivos que falharam, reutilizando as funções do próprio bulk_wiki_ingest
(payload idêntico) e usando ids determinísticos (uuid5 do caminho do arquivo),
de modo que pode ser re-executado sem criar duplicatas.

Uso: bash scripts/run-script.sh scripts/retry_failed_ingest.py /path/to/failed-files.json
"""
import argparse
import asyncio
import importlib.util
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parent.parent
FEITOS = 0
FALHAS = []


def carrega_modulo():
    spec = importlib.util.spec_from_file_location("bwi", REPO / "scripts" / "bulk_wiki_ingest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # executa o nível de módulo (checa OPENROUTER_API_KEY)
    return mod





async def main(mod, targets):
    alvos = json.loads(targets.read_text())
    if not alvos:
        print("sem alvos")
        return
    connectors = aiohttp.TCPConnector(limit=4)
    async with aiohttp.ClientSession(connector=connectors) as s:
        for path_str in alvos:
            p = Path(path_str)
            if not p.exists():
                FALHAS.append(f"ausente: {p}")
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            meta, body = mod.parse_frontmatter(text)
            title = meta.get("title", p.stem)
            embed_text = f"{title}\n\n{body}"[: mod.MAX_TEXT_LEN]
            dense = await mod.get_embedding(s, embed_text)
            if not dense:
                FALHAS.append(f"embedding: {p}")
                continue
            sparse = mod.get_sparse_vector(embed_text)
            vector = {"dense": dense}
            if sparse:
                vector["sparse"] = sparse
            now_iso = datetime.now(timezone.utc).isoformat()
            point = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, str(p))),
                "vector": vector,
                "payload": {
                    "text": embed_text,
                    "source": mod.get_source_tag(p),
                    "tags": mod.get_tags_from_frontmatter(meta),
                    "created_at": now_iso,
                    "reflection_count": 0,
                    "last_reflected": None,
                    "file_path": str(p),
                    "title": title,
                    "word_count": len(embed_text.split()),
                    "lineage_id": None,
                    "generation_model": None,
                    "generation_context_hash": None,
                    "retrieved_chunk_ids": None,
                    "decay_score": 1.0,
                    "last_accessed_at": now_iso,
                    "importance_score": 0.5,
                    "source_type": "human",
                    "confidence_score": 1.0,
                    "archived": False,
                },
            }
            ok = await mod.upsert_to_qdrant(s, [point])
            if ok:
                global FEITOS
                FEITOS += 1
                print(f"  ✅ {p.name}")
            else:
                FALHAS.append(f"upsert: {p}")
                print(f"  ⚠️  {p.name}")
        async with s.get(f"{mod.QDRANT_URL}/collections/{mod.COLLECTION}", headers=mod.QDRANT_AUTH) as r:
            total = (await r.json()).get("result", {}).get("points_count", "?")
    print(f"\nreingestados: {FEITOS} | falhas: {len(FALHAS)} | total na coleção: {total}")
    for f in FALHAS:
        print("  -", f)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retry an explicit JSON list of wiki file paths")
    parser.add_argument("targets", type=Path)
    args = parser.parse_args()
    mod = carrega_modulo()
    asyncio.run(main(mod, args.targets))
    sys.exit(0 if not FALHAS else 1)
