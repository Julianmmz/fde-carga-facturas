"""Medición top-k sobre datos/preguntas.json con las dos configuraciones de fragmentado.

Dos aciertos, sobre las 8 preguntas que tienen documento (6 normales con el rol que pregunta,
2 restringidas con el rol que sí tiene acceso):
- documento: el documento esperado aparece entre los primeros k fragmentos (lo que pide la consigna).
- evidencia: entre los primeros k hay un fragmento que contiene la respuesta. Con 5 documentos el
  acierto por documento sale 8/8 casi siempre y no distingue configuraciones; este sí.
Aparte se verifica que las restringidas no le devuelvan nada al analista.

Uso: python -m src.rag.medir                (solo búsqueda: no llama al modelo de lenguaje)
     python -m src.rag.medir --respuestas   (además responde las 10 con la mejor configuración)
Requiere haber corrido antes: python -m src.rag.ingesta
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.contratos import Resultado
from src.motor import crear_cliente
from src.proveedores import ErrorProveedor
from src.rag.busqueda import Modo, buscar
from src.rag.contratos import Usuario
from src.rag.documentos import CONFIGS
from src.rag.embeddings import crear_embedder
from src.rag.ingesta import CONFIG_POR_DEFECTO, abrir_chroma, nombre_coleccion
from src.rag.respuesta import consultar

RAIZ = Path(__file__).resolve().parent.parent.parent
PREGUNTAS = RAIZ / "datos" / "preguntas.json"
SALIDA = RAIZ / "resultados" / "medicion_rag.json"
KS = (1, 3)


def _usuario(rol: str) -> Usuario:
    return Usuario(id=f"eval.{rol}", rol=rol)


def medir_busqueda(preguntas, chroma, embedder) -> tuple[list[dict], list[dict]]:
    filas, detalle = [], []
    for nombre, config in CONFIGS.items():
        coleccion = chroma.get_collection(nombre_coleccion(config))
        for modo in (Modo.VECTORIAL, Modo.HIBRIDA):
            aciertos = {f"{tipo}{k}": 0 for tipo in ("doc", "evid") for k in KS}
            con_documento = [p for p in preguntas if p["documento_esperado"]]
            for p in con_documento:
                rol = p.get("rol_con_acceso", p["rol"])
                encontrados = buscar(p["pregunta"], _usuario(rol), max(KS),
                                     coleccion=coleccion, embedder=embedder, modo=modo)
                for k in KS:
                    aciertos[f"doc{k}"] += p["documento_esperado"] in [f.documento for f in encontrados[:k]]
                    aciertos[f"evid{k}"] += any(p["evidencia"] in f.texto for f in encontrados[:k])
                detalle.append({"config": nombre, "modo": modo.value, "id": p["id"], "rol": rol,
                                "esperado": p["documento_esperado"],
                                "top": [f.fragmento_id for f in encontrados]})
            # Permisos: el rol que pregunta no tiene que recibir nada del documento restringido.
            fugas = sum(
                p["documento_esperado"] in [f.documento for f in buscar(
                    p["pregunta"], _usuario(p["rol"]), 10, coleccion=coleccion, embedder=embedder, modo=modo)]
                for p in preguntas if p["tipo"] == "restringida"
            )
            filas.append({"config": nombre, "tamano": config.tamano, "modo": modo.value,
                          "n": len(con_documento), **aciertos, "fugas_restringidas": fugas})
    return filas, detalle


def ganadora(filas: list[dict]) -> str | None:
    """Con búsqueda híbrida: más evidencia en top-3, después en top-1, después documento.
    Si empatan en todo, no hay ganadora: no se elige una al azar."""
    hibridas = [f for f in filas if f["modo"] == Modo.HIBRIDA.value]
    clave = lambda f: (f["evid3"], f["evid1"], f["doc3"], f["doc1"])  # noqa: E731
    mejor = max(hibridas, key=clave)
    empatadas = [f for f in hibridas if clave(f) == clave(mejor)]
    return mejor["config"] if len(empatadas) == 1 else None


def medir_respuestas(preguntas, coleccion, embedder) -> list[dict]:
    cliente = crear_cliente()
    if isinstance(cliente, Resultado):
        raise SystemExit(cliente.mensaje)
    filas = []
    for p in preguntas:
        r = consultar({"pregunta": p["pregunta"], "usuario": _usuario(p["rol"]).model_dump()},
                      cliente, coleccion=coleccion, embedder=embedder)
        humano = r.salida.requiere_humano if r.ok else True
        filas.append({
            "id": p["id"], "tipo": p["tipo"], "rol": p["rol"], "esperado": p["esperado"],
            "obtenido": "requiere_humano" if humano else "responde",
            "correcto": (p["esperado"] == "requiere_humano") == humano,
            "respuesta": r.salida.respuesta if r.ok else None,
            "motivo": (r.salida.motivo if r.ok else r.mensaje),
            "fuentes": [f.fragmento_id for f in r.fuentes],
            "intentos": r.metricas.intentos, "tokens_entrada": r.metricas.tokens_entrada,
            "tokens_salida": r.metricas.tokens_salida, "costo_usd": r.metricas.costo_usd,
            "latencia_ms": r.metricas.latencia_ms,
        })
    return filas


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Medición top-k del RAG")
    parser.add_argument("--respuestas", action="store_true", help="también responde las 10 preguntas")
    args = parser.parse_args()
    preguntas = json.loads(PREGUNTAS.read_text(encoding="utf-8"))

    try:
        chroma, embedder = abrir_chroma(), crear_embedder()
        filas, detalle = medir_busqueda(preguntas, chroma, embedder)
        mejor = ganadora(filas)
        print("| Fragmentado | Modo | Documento top-1 | Documento top-3 | Evidencia top-1 | "
              "Evidencia top-3 | Fugas restringidas |")
        print("|---|---|---|---|---|---|---|")
        for f in filas:
            n = f["n"]
            print(f"| {f['config']} ({f['tamano']} car.) | {f['modo']} | {f['doc1']}/{n} | {f['doc3']}/{n} | "
                  f"{f['evid1']}/{n} | {f['evid3']}/{n} | {f['fugas_restringidas']}/2 |")
        print(f"\nGanadora: {mejor or 'empate (no se elige ninguna)'}")

        respuestas = []
        if args.respuestas:
            usada = mejor or CONFIG_POR_DEFECTO
            print(f"\nRespuestas con la configuración {usada}:")
            coleccion = chroma.get_collection(nombre_coleccion(CONFIGS[usada]))
            respuestas = medir_respuestas(preguntas, coleccion, embedder)
            print("\n| # | Tipo | Esperado | Obtenido | Fuentes |")
            print("|---|---|---|---|---|")
            for r in respuestas:
                marca = "ok" if r["correcto"] else "MAL"
                print(f"| {r['id']} | {r['tipo']} | {r['esperado']} | {r['obtenido']} ({marca}) | "
                      f"{', '.join(r['fuentes']) or '-'} |")
            print(f"\nComportamiento correcto: {sum(r['correcto'] for r in respuestas)}/{len(respuestas)}")
    except ErrorProveedor as exc:
        print(f"Error de Gemini ({exc.tipo.value}): {exc.mensaje}", file=sys.stderr)
        return 1

    SALIDA.write_text(json.dumps(
        {"busqueda": filas, "ganadora": mejor, "detalle": detalle, "respuestas": respuestas},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
