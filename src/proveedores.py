"""Acceso a los modelos. El motor solo conoce ClienteLLM, así cambiar de proveedor no lo toca.

Anthropic es el proveedor principal y Gemini el respaldo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import anthropic
import httpx
# Función interna del SDK: es la que usa messages.parse para adaptar el esquema a lo que
# acepta la API. Por eso requirements.txt fija la versión de anthropic.
from anthropic.lib._parse._transform import transform_schema
from google import genai
from google.genai import errors, types

from src.contratos import TipoError

MAX_TOKENS = 16000

log = logging.getLogger("motor")


@dataclass
class RespuestaLLM:
    texto: str
    modelo: str
    tokens_entrada: int
    tokens_salida: int
    tokens_razonamiento: int


class ErrorProveedor(Exception):
    def __init__(self, tipo: TipoError, mensaje: str):
        super().__init__(mensaje)
        self.tipo = tipo
        self.mensaje = mensaje


class ClienteLLM(Protocol):
    modelo: str

    def generar(self, sistema: str, usuario: str, esquema: dict) -> RespuestaLLM: ...


class ClienteAnthropic:
    def __init__(self, clave: str, modelo: str):
        self.modelo = modelo
        self._cliente = anthropic.Anthropic(api_key=clave)

    def generar(self, sistema: str, usuario: str, esquema: dict) -> RespuestaLLM:
        try:
            respuesta = self._cliente.messages.create(
                model=self.modelo,
                max_tokens=MAX_TOKENS,
                system=sistema,
                messages=[{"role": "user", "content": usuario}],
                # Extraer campos de un texto es una tarea simple: no necesita más esfuerzo.
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": transform_schema(esquema)},
                },
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ErrorProveedor(TipoError.AUTENTICACION, "Anthropic rechazó la clave.") from exc
        except anthropic.NotFoundError as exc:
            raise ErrorProveedor(TipoError.MODELO_NO_DISPONIBLE, "El modelo no está disponible.") from exc
        except anthropic.RateLimitError as exc:
            raise ErrorProveedor(TipoError.LIMITE_DE_USO, "Se alcanzó el límite de uso de Anthropic.") from exc
        except anthropic.APIStatusError as exc:
            raise ErrorProveedor(
                TipoError.ERROR_DEL_PROVEEDOR, f"Anthropic devolvió el código {exc.status_code}."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ErrorProveedor(TipoError.SIN_CONEXION, "No se pudo conectar con Anthropic.") from exc

        if respuesta.stop_reason == "refusal":
            raise ErrorProveedor(TipoError.ERROR_DEL_PROVEEDOR, "Anthropic rechazó la consulta.")
        return RespuestaLLM(
            texto="".join(b.text for b in respuesta.content if b.type == "text"),
            modelo=self.modelo,
            tokens_entrada=respuesta.usage.input_tokens,
            # output_tokens ya incluye el razonamiento, que se cobra igual que la salida.
            tokens_salida=respuesta.usage.output_tokens,
            tokens_razonamiento=0,
        )


class ClienteGemini:
    def __init__(self, clave: str, modelo: str):
        self.modelo = modelo
        self._cliente = genai.Client(api_key=clave)

    def generar(self, sistema: str, usuario: str, esquema: dict) -> RespuestaLLM:
        configuracion = types.GenerateContentConfig(
            system_instruction=sistema,
            response_mime_type="application/json",
            response_json_schema=esquema,
            temperature=0,
        )
        try:
            respuesta = self._cliente.models.generate_content(
                model=self.modelo, contents=usuario, config=configuracion
            )
        except errors.APIError as exc:
            raise self._traducir(exc) from exc
        except httpx.TransportError as exc:
            raise ErrorProveedor(TipoError.SIN_CONEXION, "No se pudo conectar con Gemini.") from exc
        uso = respuesta.usage_metadata
        return RespuestaLLM(
            texto=respuesta.text or "",
            modelo=self.modelo,
            tokens_entrada=(uso and uso.prompt_token_count) or 0,
            tokens_salida=(uso and uso.candidates_token_count) or 0,
            tokens_razonamiento=(uso and uso.thoughts_token_count) or 0,
        )

    @staticmethod
    def _traducir(exc: errors.APIError) -> ErrorProveedor:
        # Solo se usa el código: el mensaje del proveedor podría repetir datos de la consulta.
        if exc.code == 429:
            return ErrorProveedor(TipoError.LIMITE_DE_USO, "Se alcanzó el límite de uso de Gemini.")
        if exc.code in (401, 403) or (exc.code == 400 and "API key" in str(exc)):
            return ErrorProveedor(TipoError.AUTENTICACION, "Gemini rechazó la clave.")
        if exc.code == 404:
            return ErrorProveedor(TipoError.MODELO_NO_DISPONIBLE, "El modelo no está disponible.")
        return ErrorProveedor(TipoError.ERROR_DEL_PROVEEDOR, f"Gemini devolvió el código {exc.code}.")


class ClienteConRespaldo:
    """Usa el principal y, si falla el proveedor, repite la misma consulta en el respaldo.

    Una respuesta que no valida no pasa por acá: eso lo resuelve el reintento del motor.
    """

    def __init__(self, principal: ClienteLLM, respaldo: ClienteLLM):
        self.modelo = principal.modelo
        self._principal = principal
        self._respaldo = respaldo

    def generar(self, sistema: str, usuario: str, esquema: dict) -> RespuestaLLM:
        try:
            return self._principal.generar(sistema, usuario, esquema)
        except ErrorProveedor as exc:
            log.warning(
                "principal=%s error=%s respaldo=%s",
                self._principal.modelo, exc.tipo.value, self._respaldo.modelo,
            )
            return self._respaldo.generar(sistema, usuario, esquema)
