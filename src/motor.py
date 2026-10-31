"""Motor LLM: de texto de factura a borrador validado, con costo y latencia medidos.

Uso: python -m src.motor --caso 1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from src.contratos import CampoFallido, Entrada, Metricas, Resultado, Salida, TipoError
from src.precios import PRECIOS_USD_POR_MILLON, calcular_costo_usd
from src.prompts import PLANTILLA_REINTENTO, PLANTILLA_USUARIO, PROMPT_SISTEMA
from src.proveedores import (
    ClienteAnthropic,
    ClienteConRespaldo,
    ClienteGemini,
    ClienteLLM,
    ErrorProveedor,
)

MODELO_ANTHROPIC = "claude-opus-5-5"
MODELO_GEMINI = "gemini-3.5-flash-lite"
MAX_REINTENTOS = 1
CASOS = Path(__file__).resolve().parent.parent / "datos" / "casos.json"

log = logging.getLogger("motor")


def _campos_fallidos(exc: ValidationError) -> list[CampoFallido]:
    # include_input=False: el log no tiene que repetir datos de la factura.
    return [
        CampoFallido(
            campo=".".join(map(str, error["loc"])) or "respuesta",
            regla=error["type"],
            mensaje=error["msg"],
        )
        for error in exc.errors(include_input=False, include_url=False)
    ]


def procesar_factura(datos: dict, cliente: ClienteLLM) -> Resultado:
    inicio = time.perf_counter()
    entrada_tok = salida_tok = razonamiento_tok = intentos = 0
    costo_usd = 0.0
    modelo_usado = cliente.modelo

    def metricas() -> Metricas:
        return Metricas(
            modelo=modelo_usado,
            intentos=intentos,
            tokens_entrada=entrada_tok,
            tokens_salida=salida_tok,
            tokens_razonamiento=razonamiento_tok,
            costo_usd=round(costo_usd, 8),
            latencia_ms=round((time.perf_counter() - inicio) * 1000),
        )

    try:
        entrada = Entrada.model_validate(datos)
    except ValidationError as exc:
        return Resultado(
            ok=False,
            tipo_error=TipoError.ENTRADA_INVALIDA,
            mensaje="La entrada no cumple el contrato; no se llamó al modelo.",
            campos_fallidos=_campos_fallidos(exc),
            metricas=metricas(),
        )

    mensaje = PLANTILLA_USUARIO.format(canal=entrada.canal.value, texto_factura=entrada.texto_factura)
    esquema = Salida.model_json_schema()

    for intento in range(1, MAX_REINTENTOS + 2):
        intentos = intento
        try:
            respuesta = cliente.generar(PROMPT_SISTEMA, mensaje, esquema)
        except ErrorProveedor as exc:
            log.error("intento=%d error_proveedor=%s", intento, exc.tipo.value)
            return Resultado(ok=False, tipo_error=exc.tipo, mensaje=exc.mensaje, metricas=metricas())

        # El reintento también consume tokens: se acumulan los de todos los intentos.
        entrada_tok += respuesta.tokens_entrada
        salida_tok += respuesta.tokens_salida
        razonamiento_tok += respuesta.tokens_razonamiento
        # Con respaldo, cada intento puede venir de un modelo con otro precio.
        modelo_usado = respuesta.modelo
        costo_usd += calcular_costo_usd(
            respuesta.modelo, respuesta.tokens_entrada, respuesta.tokens_salida,
            respuesta.tokens_razonamiento,
        )
        try:
            salida = Salida.model_validate_json(respuesta.texto)
        except ValidationError as exc:
            fallidos = _campos_fallidos(exc)
            for fallo in fallidos:
                log.warning(
                    "intento=%d campo=%s regla=%s mensaje=%s",
                    intento, fallo.campo, fallo.regla, fallo.mensaje,
                )
            if intento <= MAX_REINTENTOS:
                detalle = "\n".join(f"- {f.campo}: {f.mensaje}" for f in fallidos)
                mensaje += PLANTILLA_REINTENTO.format(errores=detalle)
                continue
            return Resultado(
                ok=False,
                tipo_error=TipoError.SALIDA_INVALIDA,
                mensaje="La respuesta siguió inválida después del reintento. Cargar a mano.",
                campos_fallidos=fallidos,
                metricas=metricas(),
            )
        return Resultado(ok=True, salida=salida, metricas=metricas())
    raise AssertionError("el ciclo de intentos siempre devuelve un resultado")


def crear_cliente() -> ClienteLLM | Resultado:
    """Anthropic con Gemini de respaldo. Con una sola clave, usa ese proveedor solo."""
    load_dotenv()
    modelo_anthropic = os.environ.get("ANTHROPIC_MODEL") or MODELO_ANTHROPIC
    modelo_gemini = os.environ.get("GEMINI_MODEL") or MODELO_GEMINI
    clave_anthropic = os.environ.get("ANTHROPIC_API_KEY")
    clave_gemini = os.environ.get("GEMINI_API_KEY")

    def error(tipo: TipoError, mensaje: str) -> Resultado:
        vacias = Metricas(
            modelo=modelo_anthropic, intentos=0, tokens_entrada=0, tokens_salida=0,
            tokens_razonamiento=0, costo_usd=0, latencia_ms=0,
        )
        return Resultado(ok=False, tipo_error=tipo, mensaje=mensaje, metricas=vacias)

    if not clave_anthropic and not clave_gemini:
        return error(TipoError.SIN_CLAVE, "Falta ANTHROPIC_API_KEY o GEMINI_API_KEY en el .env.")
    # Solo los proveedores con clave: un modelo sin usar no tiene por qué frenar al otro.
    en_uso = [m for m, clave in ((modelo_anthropic, clave_anthropic), (modelo_gemini, clave_gemini)) if clave]
    for modelo in en_uso:
        if modelo not in PRECIOS_USD_POR_MILLON:
            return error(
                TipoError.MODELO_NO_DISPONIBLE, f"{modelo} no tiene precio en src/precios.py."
            )
    principal = ClienteAnthropic(clave_anthropic, modelo_anthropic) if clave_anthropic else None
    respaldo = ClienteGemini(clave_gemini, modelo_gemini) if clave_gemini else None
    if principal and respaldo:
        return ClienteConRespaldo(principal, respaldo)
    if not principal:
        log.warning("Sin ANTHROPIC_API_KEY: se usa solo el respaldo %s", modelo_gemini)
    return principal or respaldo


def cargar_casos() -> list[dict]:
    return json.loads(CASOS.read_text(encoding="utf-8"))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Procesa una factura de datos/casos.json")
    parser.add_argument("--caso", required=True, help="id del caso, por ejemplo 1 o error_cuit")
    caso_id = parser.parse_args().caso

    caso = next((c for c in cargar_casos() if str(c["id"]) == caso_id), None)
    if caso is None:
        print(f"No existe el caso {caso_id}.", file=sys.stderr)
        return 2
    cliente = crear_cliente()
    resultado = cliente if isinstance(cliente, Resultado) else procesar_factura(caso["entrada"], cliente)
    print(resultado.model_dump_json(indent=2, exclude_none=True))
    return 0 if resultado.ok else 1


if __name__ == "__main__":
    sys.exit(main())
