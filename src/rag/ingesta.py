"""Ingesta: documentos/ -> fragmentos con metadatos -> ChromaDB.

Uso: python -m src.rag.ingesta            (las dos configuraciones de fragmentado)
     python -m src.rag.ingesta --config chico
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from src.proveedores import ErrorProveedor
from src.rag.documentos import CONFIGS, ConfigFragmentado, fragmentar, leer_carpeta
from src.rag.embeddings import Embedder, crear_embedder

RAIZ = Path(__file__).resolve().parent.parent.parent
CARPETA_DOCUMENTOS = RAIZ / "documentos"
CARPETA_CHROMA = RAIZ / ".chroma"
CONFIG_POR_DEFECTO = "grande"


def nombre_coleccion(config: ConfigFragmentado) -> str:
    return f"facturas_{config.nombre}"


def abrir_chroma() -> ClientAPI:
    return chromadb.PersistentClient(path=str(CARPETA_CHROMA))


def ingestar(
    carpeta: Path = CARPETA_DOCUMENTOS,
    config: ConfigFragmentado = CONFIGS[CONFIG_POR_DEFECTO],
    *,
    chroma: ClientAPI | None = None,
    embedder: Embedder | None = None,
    nombre: str | None = None,
) -> Collection:
    """Lee la carpeta, fragmenta y guarda en una colección nueva (se reemplaza si ya existía)."""
    chroma = chroma or abrir_chroma()
    embedder = embedder or crear_embedder()
    nombre = nombre or nombre_coleccion(config)

    fragmentos = [f for doc in leer_carpeta(carpeta) for f in fragmentar(doc, config)]
    # Se calculan antes de borrar la colección: si la API falla, la anterior sigue sirviendo.
    vectores = embedder.documentos(
        [f.texto for f in fragmentos], [f"{f.titulo} — {f.seccion}" for f in fragmentos]
    )
    if nombre in [c.name for c in chroma.list_collections()]:
        chroma.delete_collection(nombre)
    coleccion = chroma.create_collection(nombre, metadata={"hnsw:space": "cosine"})
    coleccion.add(
        ids=[f.fragmento_id for f in fragmentos],
        documents=[f.texto for f in fragmentos],
        embeddings=vectores,
        metadatas=[
            {
                "documento": f.documento,
                "titulo": f.titulo,
                "seccion": f.seccion,
                "roles": [r.value for r in f.roles],
                "orden": f.orden,
                "config": config.nombre,
            }
            for f in fragmentos
        ],
    )
    return coleccion


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingesta los documentos en ChromaDB")
    parser.add_argument("--config", choices=list(CONFIGS), help="por defecto, todas")
    args = parser.parse_args()
    configs = [CONFIGS[args.config]] if args.config else list(CONFIGS.values())

    try:
        chroma, embedder = abrir_chroma(), crear_embedder()
        for config in configs:
            coleccion = ingestar(config=config, chroma=chroma, embedder=embedder)
            datos = coleccion.get(include=["metadatas"])
            por_documento: dict[str, list] = {}
            for meta in datos["metadatas"]:
                por_documento.setdefault(meta["documento"], []).append(meta)
            print(f"\n== {coleccion.name}: {coleccion.count()} fragmentos "
                  f"(tamaño {config.tamano}, solapamiento {config.solapamiento}) ==")
            for documento, metas in sorted(por_documento.items()):
                secciones = len({m["seccion"] for m in metas})
                print(f"  {documento:<28} {len(metas):>3} fragmentos  {secciones} secciones  "
                      f"roles={metas[0]['roles']}")
    except ErrorProveedor as exc:
        print(f"Error de embeddings ({exc.tipo.value}): {exc.mensaje}", file=sys.stderr)
        return 1

    ejemplo = coleccion.get(ids=["presupuesto_areas#02"], include=["metadatas", "documents"])
    if ejemplo["ids"]:
        print("\nEjemplo de fragmento:")
        print(json.dumps({"id": ejemplo["ids"][0], "metadatos": ejemplo["metadatas"][0],
                          "texto": ejemplo["documents"][0]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
