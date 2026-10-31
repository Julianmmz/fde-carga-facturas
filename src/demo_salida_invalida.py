"""Demostración del ciclo completo de una salida inválida: campo que falla, reintento y error.

Un modelo real casi nunca falla dos veces seguidas, así que se usa el cliente simulado de los
tests: devuelve dos veces una factura cuyo total no cierra. El motor y la validación son los reales.

Uso: python -m src.demo_salida_invalida
"""

from __future__ import annotations

import logging
import sys

from src.motor import procesar_factura
from tests.test_motor import ENTRADA, FACTURA, ClienteFalso


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mala = FACTURA | {"total": 999}
    resultado = procesar_factura(ENTRADA, ClienteFalso(mala, mala))
    print(resultado.model_dump_json(indent=2, exclude_none=True))
    return 0 if resultado.ok else 1


if __name__ == "__main__":
    sys.exit(main())
