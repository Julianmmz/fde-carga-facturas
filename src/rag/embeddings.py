"""Embeddings con Gemini. ChromaDB guarda y busca los vectores; acá solo se calculan.

Los vectores se pasan a Chroma ya calculados (no se le registra una función de embeddings),
porque los documentos y las consultas llevan formatos distintos: gemini-embedding-2 no acepta
task_type y la tarea va escrita en el texto.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Protocol

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from src.contratos import TipoError
from src.proveedores import ClienteGemini, ErrorProveedor

MODELO_EMBEDDINGS = "gemini-embedding-2"
DIMENSION = 768
LOTE = 50  # textos por llamada
REINTENTOS_LIMITE = 2
ESPERA_LIMITE_S = 60

log = logging.getLogger("rag")


class Embedder(Protocol):
    def documentos(self, textos: list[str], titulos: list[str]) -> list[list[float]]: ...

    def consulta(self, texto: str) -> list[float]: ...


class EmbedderGemini:
    def __init__(self, clave: str, modelo: str = MODELO_EMBEDDINGS):
        self.modelo = modelo
        self._cliente = genai.Client(api_key=clave)
        self._config = types.EmbedContentConfig(output_dimensionality=DIMENSION)
        self._cache: dict[str, list[float]] = {}

    def documentos(self, textos: list[str], titulos: list[str]) -> list[list[float]]:
        formateados = [f"title: {t} | text: {x}" for t, x in zip(titulos, textos)]
        vectores = []
        for i in range(0, len(formateados), LOTE):
            vectores += self._embeber(formateados[i:i + LOTE])
        return vectores

    def consulta(self, texto: str) -> list[float]:
        # La búsqueda híbrida y la comparación por modos embeben la misma consulta varias veces.
        if texto not in self._cache:
            self._cache[texto] = self._embeber([f"task: search result | query: {texto}"])[0]
        return self._cache[texto]

    def _embeber(self, textos: list[str]) -> list[list[float]]:
        # Cada texto va en su propio Content. Con una lista de strings, gemini-embedding-2 los
        # toma como partes de un único contenido y devuelve UN vector para todo el lote
        # (probado: 2 textos -> 1 embedding). Sin esto la ingesta guarda vectores mezclados.
        contenidos = [types.Content(parts=[types.Part(text=t)]) for t in textos]
        for intento in range(1, REINTENTOS_LIMITE + 2):
            try:
                respuesta = self._cliente.models.embed_content(
                    model=self.modelo, contents=contenidos, config=self._config
                )
                break
            except errors.APIError as exc:
                # Nivel gratuito: 100 textos por minuto, y cada texto de un lote cuenta como un
                # pedido (la ingesta de las dos configuraciones ya usa 85). Se espera y se reintenta.
                if exc.code == 429 and intento <= REINTENTOS_LIMITE:
                    log.warning("límite de embeddings; espero %ds (intento %d)", ESPERA_LIMITE_S, intento)
                    time.sleep(ESPERA_LIMITE_S)
                    continue
                raise ClienteGemini._traducir(exc) from exc
            except httpx.TransportError as exc:
                raise ErrorProveedor(TipoError.SIN_CONEXION, "No se pudo conectar con Gemini.") from exc
        vectores = [e.values for e in respuesta.embeddings or []]
        if len(vectores) != len(textos):
            raise ErrorProveedor(
                TipoError.ERROR_DEL_PROVEEDOR,
                f"Gemini devolvió {len(vectores)} embeddings para {len(textos)} textos.",
            )
        return vectores


def crear_embedder() -> EmbedderGemini:
    load_dotenv()
    clave = os.environ.get("GEMINI_API_KEY")
    if not clave:
        raise ErrorProveedor(TipoError.SIN_CLAVE, "Falta GEMINI_API_KEY en el .env: la usan los embeddings.")
    return EmbedderGemini(clave)
