"""Búsqueda híbrida con permisos: vectorial (ChromaDB) + palabras clave (BM25), fusionadas con RRF.

El permiso se aplica en la consulta a la base, en las dos ramas: la vectorial pide a Chroma solo
los fragmentos del rol (`where`), y BM25 se arma solo con los fragmentos que Chroma devuelve para
ese rol. Un fragmento que el rol no puede leer nunca sale de la base, así que no puede llegar a
los resultados ni al prompt.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum

from chromadb.api.models.Collection import Collection
from rank_bm25 import BM25Okapi

from src.rag.contratos import Fragmento, Usuario
from src.rag.embeddings import Embedder, crear_embedder

CANDIDATOS = 20  # resultados que aporta cada rama antes de fusionar
RRF_K = 60  # constante habitual de Reciprocal Rank Fusion: suaviza el peso del primer puesto

PALABRAS_VACIAS = set(
    "a al como con cual cuales cuando de del el en es la las lo los me mi para por que qué se "
    "si su un una uno y o hay hace tiene tienen está esta este esto son ser fue cómo cuál cuánto "
    "cuánta quién quien le les ya".split()
)


class Modo(str, Enum):
    HIBRIDA = "hibrida"
    VECTORIAL = "vectorial"
    PALABRAS = "palabras"


def filtro_rol(usuario: Usuario) -> dict:
    return {"roles": {"$contains": usuario.rol.value}}


def tokenizar(texto: str) -> list[str]:
    """Minúsculas y sin tildes. Los códigos quedan enteros y además partidos:
    'ART-4410' -> ['art-4410', 'art', '4410'], para que también coincida '4410' solo."""
    texto = unicodedata.normalize("NFKD", texto.lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    tokens = []
    for token in re.findall(r"[a-z0-9]+(?:[-.][a-z0-9]+)*", texto):
        if token in PALABRAS_VACIAS:
            continue
        tokens.append(token)
        partes = re.split(r"[-.]", token)
        if len(partes) > 1:
            tokens += [p for p in partes if p not in PALABRAS_VACIAS]
    return tokens


def _vectorial(coleccion: Collection, consulta: str, usuario: Usuario, embedder: Embedder) -> list[tuple[str, float]]:
    resultado = coleccion.query(
        query_embeddings=[embedder.consulta(consulta)],
        n_results=CANDIDATOS,
        where=filtro_rol(usuario),
        include=["distances"],
    )
    # Distancia coseno -> similitud: 1 es idéntico.
    return [(i, 1 - d) for i, d in zip(resultado["ids"][0], resultado["distances"][0])]


def _palabras(coleccion: Collection, consulta: str, usuario: Usuario) -> list[tuple[str, float]]:
    permitidos = coleccion.get(where=filtro_rol(usuario), include=["documents", "metadatas"])
    if not permitidos["ids"]:
        return []
    # El índice se arma por consulta y solo con lo permitido: con decenas de fragmentos es
    # instantáneo. Con miles, se precalcularía un índice por rol al ingestar.
    corpus = [
        tokenizar(f"{meta['titulo']} {meta['seccion']} {texto}")
        for texto, meta in zip(permitidos["documents"], permitidos["metadatas"])
    ]
    puntajes = BM25Okapi(corpus).get_scores(tokenizar(consulta))
    ranking = sorted(zip(permitidos["ids"], puntajes), key=lambda par: par[1], reverse=True)
    return [(i, float(p)) for i, p in ranking[:CANDIDATOS] if p > 0]


def _fusionar(*rankings: list[tuple[str, float]]) -> list[tuple[str, float]]:
    puntajes: dict[str, float] = {}
    for ranking in rankings:
        for posicion, (fragmento_id, _) in enumerate(ranking, start=1):
            puntajes[fragmento_id] = puntajes.get(fragmento_id, 0) + 1 / (RRF_K + posicion)
    return sorted(puntajes.items(), key=lambda par: par[1], reverse=True)


def buscar(
    consulta: str,
    usuario: Usuario,
    k: int = 4,
    *,
    coleccion: Collection | None = None,
    embedder: Embedder | None = None,
    modo: Modo = Modo.HIBRIDA,
) -> list[Fragmento]:
    if coleccion is None:
        from src.rag.ingesta import CONFIG_POR_DEFECTO, abrir_chroma

        coleccion = abrir_chroma().get_collection(f"facturas_{CONFIG_POR_DEFECTO}")
    if modo is not Modo.PALABRAS:
        embedder = embedder or crear_embedder()

    if modo is Modo.VECTORIAL:
        ranking = _vectorial(coleccion, consulta, usuario, embedder)
    elif modo is Modo.PALABRAS:
        ranking = _palabras(coleccion, consulta, usuario)
    else:
        ranking = _fusionar(
            _vectorial(coleccion, consulta, usuario, embedder),
            _palabras(coleccion, consulta, usuario),
        )
    ranking = ranking[:k]
    if not ranking:
        return []

    # Se vuelve a pedir con el mismo filtro: el texto sale de la base, nunca de otra fuente.
    datos = coleccion.get(
        ids=[i for i, _ in ranking], where=filtro_rol(usuario), include=["documents", "metadatas"]
    )
    por_id = {i: (t, m) for i, t, m in zip(datos["ids"], datos["documents"], datos["metadatas"])}
    return [
        Fragmento(
            documento=por_id[i][1]["documento"],
            fragmento_id=i,
            titulo=por_id[i][1]["titulo"],
            seccion=por_id[i][1]["seccion"],
            texto=por_id[i][0],
            puntaje=round(puntaje, 6),
        )
        for i, puntaje in ranking
        if i in por_id
    ]
