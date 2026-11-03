"""Contratos del RAG: quién consulta, qué se recupera y qué devuelve el modelo.

Las citas se validan contra los fragmentos que de verdad se recuperaron para ese usuario
(llegan en el contexto de validación). Una cita inventada, o de un documento que el usuario
no puede ver, es un error en el campo `citas` y dispara el mismo reintento que en la PE2.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from src.contratos import (
    CampoFallido,
    Contrato,
    IdUsuario,
    Metricas,
    Salida,
    TipoError,
    _rechazar,
)

FragmentoId = Annotated[str, Field(pattern=r"^[a-z_]+#\d{2}$")]
CodigoArticulo = Annotated[str, Field(pattern=r"^ART-\d{4}$")]
# Datos exactos que, si aparecen en una respuesta, tienen que estar escritos en lo citado.
DATOS_EXACTOS = re.compile(r"ART-\d{4}|\b\d{2}-\d{8}-\d\b")


def _textos_citados(citas: list[str], info: ValidationInfo) -> str | None:
    recuperados = (info.context or {}).get("recuperados")
    if recuperados is None:
        return None
    return " ".join(recuperados.get(c, "") for c in citas)


class Rol(str, Enum):
    ANALISTA = "analista"
    GERENCIA = "gerencia"


class Usuario(Contrato):
    id: IdUsuario
    rol: Rol


class Fragmento(BaseModel):
    """Lo que devuelve buscar(): un fragmento con su origen y su puntaje en esa búsqueda."""

    documento: str
    fragmento_id: str
    titulo: str
    seccion: str
    texto: str
    puntaje: float


class Fuente(Contrato):
    documento: str
    seccion: str
    fragmento_id: str


def _citas_recuperadas(citas: list[str], info: ValidationInfo) -> list[str]:
    recuperados = (info.context or {}).get("recuperados")
    if recuperados is None:  # sin contexto (por ejemplo, al armar el esquema) no hay contra qué validar
        return citas
    ajenas = [c for c in citas if c not in recuperados]
    if ajenas:
        raise ValueError(f"cita fragmentos que no se recuperaron: {', '.join(ajenas)}")
    return citas


class EntradaConsulta(Contrato):
    pregunta: str = Field(min_length=10, max_length=500)
    usuario: Usuario


class SalidaConsulta(Contrato):
    """Lo que devuelve el modelo para una consulta de la HU3."""

    requiere_humano: bool
    respuesta: str | None = Field(min_length=10, max_length=800)
    motivo: str | None = Field(min_length=10, max_length=300)
    citas: list[FragmentoId] = Field(max_length=6)

    _validar_citas = field_validator("citas")(_citas_recuperadas)

    @model_validator(mode="after")
    def _coherencia(self, info: ValidationInfo) -> SalidaConsulta:
        errores = []
        if self.requiere_humano:
            if self.motivo is None:
                errores.append(("motivo", "requiere_humano necesita un motivo"))
            if self.respuesta is not None:
                errores.append(("respuesta", "si requiere un humano no se responde"))
        else:
            if self.respuesta is None:
                errores.append(("respuesta", "falta la respuesta"))
            if not self.citas:
                errores.append(("citas", "una respuesta tiene que citar al menos un fragmento"))
        # Caso real: respondió ART-2205 (correcto) citando la fila de fertilizantes (ART-2310).
        # El dato era cierto, pero la cita mandaba a verificarlo al lugar equivocado.
        citados = _textos_citados(self.citas, info)
        if self.respuesta and citados is not None:
            sin_respaldo = [d for d in DATOS_EXACTOS.findall(self.respuesta) if d not in citados]
            if sin_respaldo:
                errores.append(("respuesta", f"{', '.join(sin_respaldo)} no figura en los fragmentos citados"))
        if errores:
            _rechazar(errores)
        return self


class Autoriza(str, Enum):
    ANALISTA = "analista"
    GERENCIA = "gerencia"


class ResolucionFactura(Contrato):
    """Lo que resuelve el RAG sobre una factura ya extraída: artículo del ERP y quién autoriza."""

    requiere_humano: bool
    articulo_erp: CodigoArticulo | None
    autoriza: Autoriza | None
    motivo: str | None = Field(min_length=10, max_length=300)
    citas: list[FragmentoId] = Field(max_length=6)

    _validar_citas = field_validator("citas")(_citas_recuperadas)

    @model_validator(mode="after")
    def _coherencia(self, info: ValidationInfo) -> ResolucionFactura:
        errores = []
        if self.requiere_humano:
            if self.motivo is None:
                errores.append(("motivo", "requiere_humano necesita un motivo"))
        else:
            for campo in ("articulo_erp", "autoriza"):
                if getattr(self, campo) is None:
                    errores.append((campo, "falta en una resolución completa"))
            if not self.citas:
                errores.append(("citas", "una resolución tiene que citar al menos un fragmento"))
        # El código tiene que estar escrito en un fragmento citado: si no, el modelo lo inventó.
        citados = _textos_citados(self.citas, info)
        if self.articulo_erp and citados is not None:
            if self.articulo_erp not in citados:
                errores.append(("articulo_erp", "el artículo no figura en los fragmentos citados"))
        if errores:
            _rechazar(errores)
        return self


class RespuestaConsulta(Contrato):
    ok: bool
    salida: SalidaConsulta | None = None
    fuentes: list[Fuente] = Field(default_factory=list)
    tipo_error: TipoError | None = None
    mensaje: str | None = None
    campos_fallidos: list[CampoFallido] = Field(default_factory=list)
    metricas: Metricas


class RespuestaFactura(Contrato):
    """responder(): la Salida de la PE2 más lo que resolvió el RAG y sus fuentes."""

    ok: bool
    salida: Salida | None = None
    resolucion: ResolucionFactura | None = None
    fuentes: list[Fuente] = Field(default_factory=list)
    requiere_humano: bool
    tipo_error: TipoError | None = None
    mensaje: str | None = None
    campos_fallidos: list[CampoFallido] = Field(default_factory=list)
    metricas: Metricas
