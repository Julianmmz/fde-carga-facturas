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
from typing import TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from src.contratos import CampoFallido, Entrada, Metricas, Resultado, Salida, TipoError
from src.precios import PRECIOS_USD_POR_MILLON, calcular_costo_usd
from src.prompts import PLANTILLA_REINTENTO, PLANTILLA_USUARIO, PROMPT_SISTEMA
from src.proveedores import (
    ClienteAnthropic,
    ClienteConRespaldo,
    ClienteGemini,
    ClienteLLM,
    ErrorProveedor,
    RespuestaLLM,
)

MODELO_ANTHROPIC = "claude-opus-5-5"
MODELO_GEMINI = "gemini-3.5-flash-lite"
MAX_REINTENTOS = 1
CASOS = Path(__file__).resolve().parent.parent / "datos" / "casos.json"

log = logging.getLogger("motor")
M = TypeVar("M", bound=BaseModel)


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


class Medidor:
    """Acumula intentos, tokens y costo de todas las llamadas de una misma operación."""

    def __init__(self, modelo: str):
        self.modelo = modelo
        self.intentos = self.tokens_entrada = self.tokens_salida = self.tokens_razonamiento = 0
        self.costo_usd = 0.0
        self._inicio = time.perf_counter()

    def sumar(self, respuesta: RespuestaLLM) -> None:
        # El reintento también consume tokens: se acumulan los de todos los intentos.
        self.tokens_entrada += respuesta.tokens_entrada
        self.tokens_salida += respuesta.tokens_salida
        self.tokens_razonamiento += respuesta.tokens_razonamiento
        # Con respaldo, cada intento puede venir de un modelo con otro precio.
        self.modelo = respuesta.modelo
        self.costo_usd += calcular_costo_usd(
            respuesta.modelo, respuesta.tokens_entrada, respuesta.tokens_salida,
            respuesta.tokens_razonamiento,
        )

    def metricas(self) -> Metricas:
        return Metricas(
            modelo=self.modelo,
            intentos=self.intentos,
            tokens_entrada=self.tokens_entrada,
            tokens_salida=self.tokens_salida,
            tokens_razonamiento=self.tokens_razonamiento,
            costo_usd=round(self.costo_usd, 8),
            latencia_ms=round((time.perf_counter() - self._inicio) * 1000),
        )


class FalloLLM(Exception):
    """La llamada no dio una respuesta válida: error del proveedor o salida inválida tras reintentar."""

    def __init__(self, tipo: TipoError, mensaje: str, campos_fallidos: list[CampoFallido] | None = None):
        super().__init__(mensaje)
        self.tipo = tipo
        self.mensaje = mensaje
        self.campos_fallidos = campos_fallidos or []


def generar_validado(
    cliente: ClienteLLM,
    sistema: str,
    mensaje: str,
    modelo: type[M],
    medidor: Medidor,
    plantilla_reintento: str = PLANTILLA_REINTENTO,
    contexto: dict | None = None,
) -> M:
    """Llama al modelo y valida contra `modelo`. Si no valida, reintenta una vez avisando qué falló.

    `contexto` llega a los validadores de Pydantic: así el RAG chequea que las citas sean de
    fragmentos que de verdad se recuperaron.
    """
    esquema = modelo.model_json_schema()
    for intento in range(1, MAX_REINTENTOS + 2):
        medidor.intentos += 1
        try:
            respuesta = cliente.generar(sistema, mensaje, esquema)
        except ErrorProveedor as exc:
            log.error("intento=%d error_proveedor=%s", intento, exc.tipo.value)
            raise FalloLLM(exc.tipo, exc.mensaje) from exc
        medidor.sumar(respuesta)
        try:
            return modelo.model_validate_json(respuesta.texto, context=contexto)
        except ValidationError as exc:
            fallidos = _campos_fallidos(exc)
            for fallo in fallidos:
                log.warning(
                    "intento=%d campo=%s regla=%s mensaje=%s",
                    intento, fallo.campo, fallo.regla, fallo.mensaje,
                )
            if intento <= MAX_REINTENTOS:
                detalle = "\n".join(f"- {f.campo}: {f.mensaje}" for f in fallidos)
                mensaje += plantilla_reintento.format(errores=detalle)
                continue
            raise FalloLLM(
                TipoError.SALIDA_INVALIDA,
                "La respuesta siguió inválida después del reintento.",
                fallidos,
            ) from exc
    raise AssertionError("el ciclo de intentos siempre devuelve o falla")


def procesar_factura(datos: dict, cliente: ClienteLLM) -> Resultado:
    medidor = Medidor(cliente.modelo)
    try:
        entrada = Entrada.model_validate(datos)
    except ValidationError as exc:
        return Resultado(
            ok=False,
            tipo_error=TipoError.ENTRADA_INVALIDA,
            mensaje="La entrada no cumple el contrato; no se llamó al modelo.",
            campos_fallidos=_campos_fallidos(exc),
            metricas=medidor.metricas(),
        )

    mensaje = PLANTILLA_USUARIO.format(canal=entrada.canal.value, texto_factura=entrada.texto_factura)
    try:
        salida = generar_validado(cliente, PROMPT_SISTEMA, mensaje, Salida, medidor)
    except FalloLLM as fallo:
        if fallo.tipo is TipoError.SALIDA_INVALIDA:
            fallo.mensaje += " Cargar a mano."
        return Resultado(
            ok=False,
            tipo_error=fallo.tipo,
            mensaje=fallo.mensaje,
            campos_fallidos=fallo.campos_fallidos,
            metricas=medidor.metricas(),
        )
    return Resultado(ok=True, salida=salida, metricas=medidor.metricas())


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
