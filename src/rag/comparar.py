"""Consultas exactas (códigos, CUIT) donde se compara la búsqueda solo vectorial con la híbrida.

Acierto: el primer fragmento devuelto contiene el código o CUIT exacto que se pregunta.

Uso: python -m src.rag.comparar
"""

from __future__ import annotations

import sys

from src.proveedores import ErrorProveedor
from src.rag.busqueda import Modo, buscar
from src.rag.contratos import Usuario
from src.rag.embeddings import crear_embedder
from src.rag.ingesta import abrir_chroma

CONSULTAS = [
    ("¿Qué es el artículo ART-4401?", "ART-4401"),
    ("¿A qué cuenta imputa el artículo ART-6102?", "ART-6102"),
    ("¿Qué artículo se usa para el CUIT 30-66666666-2?", "30-66666666-2"),
    ("¿Qué factura el proveedor con CUIT 30-44444444-0?", "30-44444444-0"),
]
CONFIG = "chico"


def main() -> int:
    usuario = Usuario(id="ana.perez", rol="analista")
    try:
        coleccion = abrir_chroma().get_collection(f"facturas_{CONFIG}")
        embedder = crear_embedder()
        print(f"Colección facturas_{CONFIG}, usuario analista, top-3\n")
        for consulta, exacto in CONSULTAS:
            print(f"Consulta: {consulta}   (acierta si el 1.º contiene {exacto})")
            for modo in (Modo.VECTORIAL, Modo.PALABRAS, Modo.HIBRIDA):
                resultados = buscar(consulta, usuario, 3, coleccion=coleccion, embedder=embedder, modo=modo)
                acierta = bool(resultados) and exacto in resultados[0].texto
                primero = resultados[0].texto.splitlines()[-1][:70] if resultados else "-"
                print(f"  {modo.value:<9} {'ACIERTA' if acierta else 'FALLA  '} "
                      f"{[f.fragmento_id for f in resultados]}  1.º: {primero}")
            print()
    except ErrorProveedor as exc:
        print(f"Error de Gemini ({exc.tipo.value}): {exc.mensaje}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
