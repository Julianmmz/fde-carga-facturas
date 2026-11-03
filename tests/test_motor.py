"""Prueba contrato, reintento, errores y respaldo sin llamar a ninguna API."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.contratos import Salida, TipoError
from src.motor import procesar_factura
from src.proveedores import ClienteConRespaldo, ErrorProveedor, RespuestaLLM

ENTRADA = {
    "texto_factura": "FACTURA A de prueba con texto suficiente para el contrato",
    "canal": "mail",
    "analista": "ana.perez",
}
CONFIANZA = dict.fromkeys(
    ["cuit", "razon_social", "tipo_comprobante", "punto_venta", "numero", "periodo",
     "vencimiento", "producto_servicio", "neto", "alicuota_iva", "iva", "total"],
    0.95,
)
FACTURA = {
    "clasificacion": "factura", "motivo": None, "cuit": "30-88888888-4",
    "razon_social": "ELECTRO SERVICIOS DEMO", "tipo_comprobante": "01", "punto_venta": 3,
    "numero": 4521, "periodo_desde": "2026-09-12", "periodo_hasta": "2026-09-12",
    "vencimiento": "2026-09-17", "producto_servicio": "Reparación de tablero",
    "neto": 84000, "alicuota_iva": "21", "iva": 17640, "total": 101640,
    "confianza": CONFIANZA,
}
VACIO = {c: None for c in FACTURA if c not in ("clasificacion", "motivo", "confianza")}


class ClienteFalso:
    modelo = "claude-haiku-5-5"

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.mensajes = []

    def generar(self, sistema, usuario, esquema):
        self.mensajes.append(usuario)
        respuesta = self.respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return RespuestaLLM(json.dumps(respuesta), self.modelo, 100, 50, 0)


def test_factura_valida_mide_tokens_y_costo():
    r = procesar_factura(ENTRADA, ClienteFalso(FACTURA))
    assert r.ok and r.metricas.intentos == 1
    assert r.metricas.tokens_entrada == 100
    assert r.metricas.costo_usd == pytest.approx(0.000035)
    assert r.salida.campos_a_revisar == []


def test_reintenta_una_vez_y_avisa_que_campo_fallo():
    mala = FACTURA | {"cuit": "30-88888888-5"}
    cliente = ClienteFalso(mala, FACTURA)
    r = procesar_factura(ENTRADA, cliente)
    assert r.ok and r.metricas.intentos == 2
    assert r.metricas.tokens_entrada == 200  # el reintento también cuenta
    assert "cuit" in cliente.mensajes[1]


def test_despues_del_reintento_devuelve_error_con_el_campo():
    mala = FACTURA | {"total": 999}
    r = procesar_factura(ENTRADA, ClienteFalso(mala, mala))
    assert not r.ok and r.tipo_error is TipoError.SALIDA_INVALIDA
    assert [f.campo for f in r.campos_fallidos] == ["total"]


def test_respuesta_que_no_es_json_tambien_es_salida_invalida():
    class Roto(ClienteFalso):
        def generar(self, sistema, usuario, esquema):
            return RespuestaLLM("no es json", self.modelo, 1, 1, 0)

    assert procesar_factura(ENTRADA, Roto()).tipo_error is TipoError.SALIDA_INVALIDA


def test_entrada_invalida_no_llama_al_modelo():
    cliente = ClienteFalso()
    r = procesar_factura(ENTRADA | {"texto_factura": "corto"}, cliente)
    assert r.tipo_error is TipoError.ENTRADA_INVALIDA and cliente.mensajes == []
    assert r.campos_fallidos[0].campo == "texto_factura"


def test_error_de_api_devuelve_error_estructurado():
    limite = ErrorProveedor(TipoError.LIMITE_DE_USO, "Límite")
    r = procesar_factura(ENTRADA, ClienteFalso(limite))
    assert r.tipo_error is TipoError.LIMITE_DE_USO and r.metricas.intentos == 1


def test_si_falla_el_principal_usa_el_respaldo_y_su_precio():
    principal = ClienteFalso(ErrorProveedor(TipoError.AUTENTICACION, "Clave"))
    respaldo = ClienteFalso(FACTURA)
    respaldo.modelo = "gemini-2.5-flash-lite"
    r = procesar_factura(ENTRADA, ClienteConRespaldo(principal, respaldo))
    assert r.ok and r.metricas.modelo == "gemini-2.5-flash-lite"
    assert r.metricas.costo_usd == pytest.approx(0.00003)


def test_si_fallan_los_dos_proveedores_informa_el_ultimo():
    error = ErrorProveedor(TipoError.SIN_CONEXION, "Sin red")
    cliente = ClienteConRespaldo(ClienteFalso(error), ClienteFalso(error))
    assert procesar_factura(ENTRADA, cliente).tipo_error is TipoError.SIN_CONEXION


def test_respuesta_invalida_no_activa_el_respaldo():
    mala = FACTURA | {"total": 999}
    principal, respaldo = ClienteFalso(mala, FACTURA), ClienteFalso()
    assert procesar_factura(ENTRADA, ClienteConRespaldo(principal, respaldo)).ok
    assert respaldo.mensajes == []


@pytest.mark.parametrize(
    "cambio, campo",
    [
        ({"cuit": "30888888884"}, "cuit"),
        ({"tipo_comprobante": "99"}, "tipo_comprobante"),
        ({"alicuota_iva": "19"}, "alicuota_iva"),
        ({"punto_venta": 0}, "punto_venta"),
        ({"razon_social": "AB"}, "razon_social"),
        ({"neto": 80000}, "total"),
        ({"alicuota_iva": "10.5"}, "iva"),
        ({"vencimiento": "2026-09-01"}, "vencimiento"),
        ({"motivo": "No corresponde en una factura clara"}, "motivo"),
        ({"cuit": None}, "cuit"),
    ],
)
def test_reglas_de_salida_informan_el_campo(cambio, campo):
    with pytest.raises(ValidationError) as exc:
        Salida.model_validate(FACTURA | cambio)
    assert campo in {e["loc"][0] for e in exc.value.errors()}


@pytest.mark.parametrize(
    "error, campo",
    [
        ({"cuit": "30-88888888-5"}, "cuit"),  # dígito verificador incorrecto
        ({"total": 110000}, "total"),  # neto + IVA no cierra
        ({"iva": 8820, "total": 92820}, "iva"),  # cierra el total, pero no es el 21 % del neto
    ],
)
def test_confianza_alta_no_salva_un_dato_incorrecto(error, campo):
    """El modelo puede estar seguro y equivocado: la confianza no reemplaza la validación local."""
    segura = FACTURA | error | {"confianza": dict.fromkeys(CONFIANZA, 1.0)}
    r = procesar_factura(ENTRADA, ClienteFalso(segura, segura))
    assert not r.ok and r.tipo_error is TipoError.SALIDA_INVALIDA
    assert campo in [f.campo for f in r.campos_fallidos]


def test_factura_c_no_discrimina_iva():
    datos = FACTURA | {"tipo_comprobante": "11", "alicuota_iva": "0", "iva": 17640}
    with pytest.raises(ValidationError):
        Salida.model_validate(datos)


def test_dudoso_acepta_datos_faltantes_pero_exige_motivo():
    dudoso = FACTURA | VACIO | {"clasificacion": "dudoso"}
    with pytest.raises(ValidationError):
        Salida.model_validate(dudoso)
    assert Salida.model_validate(
        dudoso | {"motivo": "El CUIT no se lee."}
    ).requiere_revision_completa


def test_no_factura_no_lleva_datos():
    datos = FACTURA | {"clasificacion": "no_factura", "motivo": "Es un mensaje personal."}
    with pytest.raises(ValidationError):
        Salida.model_validate(datos)


def test_campos_de_baja_confianza_se_resaltan():
    confianza = CONFIANZA | {"neto": 0.7}
    assert Salida.model_validate(FACTURA | {"confianza": confianza}).campos_a_revisar == ["neto"]


def test_casos_cumplen_el_contrato_y_los_valores_esperados_cierran():
    casos = json.loads((Path(__file__).parent.parent / "datos" / "casos.json").read_text("utf-8"))
    assert len([c for c in casos if isinstance(c["id"], int)]) == 5
    for caso in casos:
        esperado = caso["esperado"]
        if esperado and esperado["clasificacion"] == "factura":
            Salida.model_validate(
                FACTURA | esperado | {"producto_servicio": "Servicio de prueba"}
            )
