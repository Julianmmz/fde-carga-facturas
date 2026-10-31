"""Contrato de datos del motor para la HU1 (carga asistida de facturas).

El esquema ayuda al modelo a devolver JSON bien formado, pero Gemini ignora varias
restricciones (largos, patrones). Por eso la validación que vale es esta, local.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    WithJsonSchema,
    computed_field,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

CONFIANZA_MINIMA = 0.85  # debajo de esto el campo se resalta para revisar
TOLERANCIA_IMPORTES = Decimal("0.05")  # redondeo de centavos


def _validar_digito_cuit(cuit: str) -> str:
    digitos = [int(c) for c in cuit.replace("-", "")]
    pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    esperado = 11 - sum(d * p for d, p in zip(digitos, pesos)) % 11
    # 11 se escribe 0 y 10 se escribe 9, igual que python-stdnum.
    esperado = {11: 0, 10: 9}.get(esperado, esperado)
    if digitos[10] != esperado:
        raise ValueError("el dígito verificador del CUIT no coincide")
    return cuit


# WithJsonSchema simplifica lo que ve el modelo; Field sigue validando en local.
Cuit = Annotated[
    str,
    Field(pattern=r"^\d{2}-\d{8}-\d$"),
    AfterValidator(_validar_digito_cuit),
    WithJsonSchema({"type": "string", "description": "CUIT con formato NN-NNNNNNNN-N"}),
]
Importe = Annotated[
    Decimal,
    Field(ge=0, max_digits=14, decimal_places=2),
    WithJsonSchema({"type": "number", "minimum": 0}),
]
Confianza = Annotated[float, Field(ge=0, le=1)]
IdUsuario = Annotated[str, Field(pattern=r"^[a-z][a-z0-9._]{2,29}$")]


class Contrato(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Canal(str, Enum):
    MAIL = "mail"
    PORTAL = "portal"


class Clasificacion(str, Enum):
    FACTURA = "factura"
    DUDOSO = "dudoso"
    NO_FACTURA = "no_factura"


class TipoComprobante(str, Enum):
    FACTURA_A = "01"
    FACTURA_B = "06"
    FACTURA_C = "11"


class AlicuotaIVA(str, Enum):
    CERO = "0"
    DIEZ_Y_MEDIO = "10.5"
    VEINTIUNO = "21"
    VEINTISIETE = "27"


class Entrada(Contrato):
    """Lo que recibe el motor. La lectura de PDF o foto queda fuera de esta entrega."""

    texto_factura: str = Field(min_length=30, max_length=8000)
    canal: Canal
    analista: IdUsuario


class Confianzas(Contrato):
    """Confianza que propone el modelo por campo. No es una probabilidad calibrada."""

    cuit: Confianza
    razon_social: Confianza
    tipo_comprobante: Confianza
    punto_venta: Confianza
    numero: Confianza
    periodo: Confianza
    vencimiento: Confianza
    producto_servicio: Confianza
    neto: Confianza
    alicuota_iva: Confianza
    iva: Confianza
    total: Confianza


CAMPOS_DATOS = (
    "cuit", "razon_social", "tipo_comprobante", "punto_venta", "numero", "periodo_desde",
    "periodo_hasta", "vencimiento", "producto_servicio", "neto", "alicuota_iva", "iva", "total",
)


def _rechazar(errores: list[tuple[str, str]]) -> None:
    """Falla indicando el campo: un model_validator común dejaría la ubicación vacía."""
    detalles = [
        InitErrorDetails(
            type=PydanticCustomError("regla_de_negocio", mensaje),
            loc=(campo,),
            input=None,
        )
        for campo, mensaje in errores
    ]
    raise ValidationError.from_exception_data("Salida", detalles)


class Salida(Contrato):
    """Borrador para que el analista revise. Nunca es una carga registrada."""

    clasificacion: Clasificacion
    motivo: str | None = Field(min_length=10, max_length=300)
    cuit: Cuit | None
    razon_social: str | None = Field(min_length=3, max_length=120)
    tipo_comprobante: TipoComprobante | None
    punto_venta: int | None = Field(ge=1, le=99_999)
    numero: int | None = Field(ge=1, le=99_999_999)
    periodo_desde: date | None
    periodo_hasta: date | None
    vencimiento: date | None
    # El artículo del ERP se resuelve en la PE3 con el catálogo; acá va lo que dice la factura.
    producto_servicio: str | None = Field(min_length=3, max_length=200)
    neto: Importe | None
    alicuota_iva: AlicuotaIVA | None
    iva: Importe | None
    total: Importe | None
    confianza: Confianzas

    @model_validator(mode="after")
    def _reglas_de_negocio(self) -> Salida:
        errores: list[tuple[str, str]] = []
        if self.clasificacion is Clasificacion.FACTURA:
            if self.motivo is not None:
                errores.append(("motivo", "una factura clara no lleva motivo"))
            errores += [
                (campo, "falta en una factura clasificada como clara")
                for campo in CAMPOS_DATOS
                if getattr(self, campo) is None
            ]
            if not errores:
                errores += self._coherencia()
        else:
            if self.motivo is None:
                errores.append(("motivo", "dudoso o no_factura necesitan un motivo"))
            if self.clasificacion is Clasificacion.NO_FACTURA:
                errores += [
                    (campo, "un documento que no es factura no lleva datos")
                    for campo in CAMPOS_DATOS
                    if getattr(self, campo) is not None
                ]
        if errores:
            _rechazar(errores)
        return self

    def _coherencia(self) -> list[tuple[str, str]]:
        errores = []
        if self.periodo_desde > self.periodo_hasta:
            errores.append(("periodo_hasta", "el período termina antes de empezar"))
        if self.vencimiento < self.periodo_desde:
            errores.append(("vencimiento", "vence antes del período facturado"))
        if abs(self.neto + self.iva - self.total) > TOLERANCIA_IMPORTES:
            errores.append(("total", "neto + IVA no cierra con el total"))
        alicuota = Decimal(self.alicuota_iva.value) / 100
        if abs(self.neto * alicuota - self.iva) > TOLERANCIA_IMPORTES:
            errores.append(("iva", "el IVA no corresponde a neto x alícuota"))
        if self.tipo_comprobante is TipoComprobante.FACTURA_C and self.iva != 0:
            errores.append(("iva", "una factura C no discrimina IVA"))
        return errores

    @computed_field
    @property
    def campos_a_revisar(self) -> list[str]:
        return [
            campo
            for campo, valor in self.confianza.model_dump().items()
            if valor < CONFIANZA_MINIMA
        ]

    @computed_field
    @property
    def requiere_revision_completa(self) -> bool:
        return self.clasificacion is not Clasificacion.FACTURA


class TipoError(str, Enum):
    ENTRADA_INVALIDA = "entrada_invalida"
    SALIDA_INVALIDA = "salida_invalida"
    SIN_CLAVE = "sin_clave"
    AUTENTICACION = "autenticacion"
    LIMITE_DE_USO = "limite_de_uso"
    SIN_CONEXION = "sin_conexion"
    MODELO_NO_DISPONIBLE = "modelo_no_disponible"
    ERROR_DEL_PROVEEDOR = "error_del_proveedor"


class CampoFallido(Contrato):
    campo: str
    regla: str
    mensaje: str


class Metricas(Contrato):
    modelo: str
    intentos: int = Field(ge=0)
    tokens_entrada: int = Field(ge=0)
    tokens_salida: int = Field(ge=0)
    tokens_razonamiento: int = Field(ge=0)
    costo_usd: float = Field(ge=0)
    latencia_ms: int = Field(ge=0)


class Resultado(Contrato):
    """Respuesta única del motor: siempre trae métricas, salga bien o mal."""

    ok: bool
    salida: Salida | None = None
    tipo_error: TipoError | None = None
    mensaje: str | None = None
    campos_fallidos: list[CampoFallido] = Field(default_factory=list)
    metricas: Metricas
