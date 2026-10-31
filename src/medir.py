"""Corre las 5 consultas de datos/casos.json y guarda la tabla de medición.

Uso: python -m src.medir
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from src.contratos import Resultado
from src.motor import cargar_casos, crear_cliente, procesar_factura

SALIDA = Path(__file__).resolve().parent.parent / "resultados" / "medicion.json"
CONFIANZA_DE = {"periodo_desde": "periodo", "periodo_hasta": "periodo"}


def comparar(resultado: Resultado, esperado: dict) -> dict:
    """Cuenta campos mal y, entre ellos, los que el motor no resaltó (KPI técnico)."""
    if not resultado.ok:
        return {"clasificacion_ok": False, "campos_mal": [], "no_detectados": []}
    salida = resultado.salida.model_dump(mode="json")
    campos_mal = []
    for campo, valor in esperado.items():
        if campo == "clasificacion":
            continue
        obtenido = salida[campo]
        if isinstance(valor, (int, float)) and obtenido is not None:
            obtenido = float(obtenido)
        if obtenido != valor:
            campos_mal.append(campo)
    return {
        "clasificacion_ok": salida["clasificacion"] == esperado["clasificacion"],
        "campos_mal": campos_mal,
        "no_detectados": [
            c for c in campos_mal if CONFIANZA_DE.get(c, c) not in salida["campos_a_revisar"]
        ],
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cliente = crear_cliente()
    if isinstance(cliente, Resultado):
        print(cliente.mensaje, file=sys.stderr)
        return 1

    filas = []
    for caso in (c for c in cargar_casos() if isinstance(c["id"], int)):
        resultado = procesar_factura(caso["entrada"], cliente)
        m = resultado.metricas
        filas.append({
            "id": caso["id"],
            "consulta": caso["descripcion"],
            "valido": resultado.ok,
            "clasificacion": resultado.salida.clasificacion.value if resultado.ok else None,
            "tipo_error": resultado.tipo_error.value if resultado.tipo_error else None,
            "campos_fallidos": [f.model_dump() for f in resultado.campos_fallidos],
            "modelo": m.modelo,
            "intentos": m.intentos,
            "tokens_entrada": m.tokens_entrada,
            "tokens_salida": m.tokens_salida + m.tokens_razonamiento,
            "costo_usd": m.costo_usd,
            "latencia_ms": m.latencia_ms,
            "comparacion": comparar(resultado, caso["esperado"]),
        })
        time.sleep(2)  # margen para los límites del nivel gratuito

    n = len(filas)
    promedios = {
        clave: round(sum(f[clave] for f in filas) / n, 8)
        for clave in ("tokens_entrada", "tokens_salida", "costo_usd", "latencia_ms")
    }
    SALIDA.write_text(
        json.dumps({"filas": filas, "promedios": promedios}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("| # | Consulta | ¿Validó? | Tokens (ent/sal) | Costo USD | Latencia ms |")
    print("|---|---|---|---|---|---|")
    for f in filas:
        print(
            f"| {f['id']} | {f['consulta']} | {'sí' if f['valido'] else 'no'} | "
            f"{f['tokens_entrada']} / {f['tokens_salida']} | {f['costo_usd']:.6f} | {f['latencia_ms']} |"
        )
    print(
        f"| | Promedio | {sum(f['valido'] for f in filas)}/{n} | "
        f"{promedios['tokens_entrada']:.0f} / {promedios['tokens_salida']:.0f} | "
        f"{promedios['costo_usd']:.6f} | {promedios['latencia_ms']:.0f} |"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
