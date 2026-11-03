"""Tests del RAG sin llamar a ninguna API: embeddings simulados y cliente LLM simulado.

Usan los documentos reales de documentos/, cargados en un ChromaDB en memoria.
"""

import hashlib
import json
import math
import re
import uuid
from pathlib import Path

import chromadb
import pytest

from src.contratos import TipoError
from src.rag.busqueda import Modo, buscar, filtro_rol, tokenizar
from src.rag.contratos import Usuario
from src.rag.documentos import CONFIGS, ConfigFragmentado, fragmentar, leer_carpeta, leer_documento
from src.rag.ingesta import CARPETA_DOCUMENTOS, ingestar
from src.rag.respuesta import consultar, responder
from tests.test_motor import ENTRADA, FACTURA, ClienteFalso

RAIZ = Path(__file__).resolve().parent.parent
ANALISTA = Usuario(id="ana.perez", rol="analista")
GERENCIA = Usuario(id="gerente.compras", rol="gerencia")
PREGUNTA_PRESUPUESTO = "¿Cuánto presupuesto disponible le queda al área Logística este mes?"
# Datos que solo están en el documento restringido.
RESTRINGIDO = ["presupuesto_areas", "1.488.000", "Confidencial", "4.712.000"]


class EmbedderFalso:
    """Bolsa de palabras con hashing: determinístico, sin red, y parecido lo que comparte palabras."""

    def _vector(self, texto: str) -> list[float]:
        v = [0.0] * 64
        for token in tokenizar(texto):
            v[int(hashlib.md5(token.encode()).hexdigest(), 16) % 64] += 1
        norma = math.sqrt(sum(x * x for x in v)) or 1
        return [x / norma for x in v]

    def documentos(self, textos, titulos):
        return [self._vector(f"{t} {x}") for t, x in zip(titulos, textos)]

    def consulta(self, texto):
        return self._vector(texto)


class ColeccionEspia:
    """Registra el filtro de cada consulta a la base, para probar dónde se aplica el permiso."""

    def __init__(self, real):
        self._real = real
        self.filtros = []

    def query(self, **kwargs):
        self.filtros.append(("query", kwargs.get("where")))
        return self._real.query(**kwargs)

    def get(self, **kwargs):
        self.filtros.append(("get", kwargs.get("where")))
        return self._real.get(**kwargs)


@pytest.fixture(scope="module")
def coleccion():
    return ingestar(
        CARPETA_DOCUMENTOS, CONFIGS["grande"], chroma=chromadb.EphemeralClient(),
        embedder=EmbedderFalso(), nombre=f"test-{uuid.uuid4().hex[:8]}",
    )


def _ids(fragmentos):
    return [f.fragmento_id for f in fragmentos]


def _consultar(pregunta, usuario, cliente, coleccion):
    datos = {"pregunta": pregunta, "usuario": usuario.model_dump()}
    return consultar(datos, cliente, coleccion=coleccion, embedder=EmbedderFalso())


HUMANO = {"requiere_humano": True, "respuesta": None, "citas": [],
          "motivo": "Los fragmentos no tienen esa información."}


# --- Ingesta y metadatos ---

def test_cada_fragmento_lleva_documento_seccion_y_roles(coleccion):
    datos = coleccion.get(include=["metadatas"])
    assert datos["ids"]
    for meta in datos["metadatas"]:
        assert meta["documento"] and meta["seccion"] and meta["roles"]
    roles_presupuesto = {tuple(m["roles"]) for m in datos["metadatas"] if m["documento"] == "presupuesto_areas"}
    assert roles_presupuesto == {("gerencia",)}


def test_un_documento_sin_roles_no_se_ingesta(tmp_path):
    (tmp_path / "sin_roles.md").write_text("---\ntitulo: X\n---\n\n## A\ntexto", encoding="utf-8")
    with pytest.raises(ValueError, match="roles"):
        leer_documento(tmp_path / "sin_roles.md")


def test_limpieza_saca_comentarios_y_separadores_de_tabla():
    documentos = {d.nombre: d for d in leer_carpeta(CARPETA_DOCUMENTOS)}
    assert "Nota de edición" not in documentos["procedimiento_carga"].cuerpo
    assert "|---" not in documentos["catalogo_articulos"].cuerpo


def test_fragmento_chico_repite_el_encabezado_de_la_tabla():
    catalogo = leer_documento(CARPETA_DOCUMENTOS / "catalogo_articulos.md")
    for f in fragmentar(catalogo, CONFIGS["chico"]):
        if "ART-" in f.texto:
            assert f.texto.startswith("| Código |")


def test_ninguna_configuracion_supera_su_tamano():
    for config in CONFIGS.values():
        for documento in leer_carpeta(CARPETA_DOCUMENTOS):
            assert all(len(f.texto) <= config.tamano for f in fragmentar(documento, config))


# --- Permisos: el filtro va en la consulta a la base ---

@pytest.mark.parametrize("modo", list(Modo))
def test_analista_no_recibe_nada_del_presupuesto(coleccion, modo):
    resultados = buscar(PREGUNTA_PRESUPUESTO, ANALISTA, 10, coleccion=coleccion,
                        embedder=EmbedderFalso(), modo=modo)
    assert all(f.documento != "presupuesto_areas" for f in resultados)


def test_gerencia_si_recibe_el_presupuesto(coleccion):
    resultados = buscar(PREGUNTA_PRESUPUESTO, GERENCIA, 4, coleccion=coleccion, embedder=EmbedderFalso())
    assert resultados[0].documento == "presupuesto_areas"


@pytest.mark.parametrize("usuario", [ANALISTA, GERENCIA], ids=["analista", "gerencia"])
def test_el_filtro_por_rol_va_en_cada_consulta_a_la_base(coleccion, usuario):
    espia = ColeccionEspia(coleccion)
    buscar(PREGUNTA_PRESUPUESTO, usuario, 4, coleccion=espia, embedder=EmbedderFalso())
    assert {tipo for tipo, _ in espia.filtros} == {"query", "get"}
    assert all(filtro == filtro_rol(usuario) for _, filtro in espia.filtros)


def test_analista_no_ve_el_presupuesto_ni_en_el_prompt(coleccion):
    cliente = ClienteFalso(HUMANO)
    r = _consultar(PREGUNTA_PRESUPUESTO, ANALISTA, cliente, coleccion)
    assert r.ok and r.salida.requiere_humano and r.fuentes == []
    prompt = cliente.mensajes[0]
    assert not [dato for dato in RESTRINGIDO if dato in prompt]


def test_gerencia_ve_el_presupuesto_en_el_prompt(coleccion):
    """Control del test anterior: el mismo chequeo encuentra lo restringido cuando corresponde."""
    cliente = ClienteFalso(HUMANO)
    _consultar(PREGUNTA_PRESUPUESTO, GERENCIA, cliente, coleccion)
    assert [dato for dato in RESTRINGIDO if dato in cliente.mensajes[0]]


# --- Respuesta con fuentes ---

def _id_con(prompt, texto):
    """El id del primer fragmento del prompt que contiene `texto`."""
    for bloque in prompt.split("\n\n["):
        if texto in bloque:
            return re.search(r"([a-z_]+#\d{2})\]", "[" + bloque).group(1)
    raise AssertionError(f"ningún fragmento del prompt contiene {texto!r}")


class ClienteQueCita(ClienteFalso):
    """Responde citando un fragmento que sí está en el prompt."""

    def __init__(self, armar):
        super().__init__()
        self.armar = armar

    def generar(self, sistema, usuario, esquema):
        self.respuestas.append(self.armar(usuario))
        return super().generar(sistema, usuario, esquema)


def test_respuesta_cita_el_documento_de_origen(coleccion):
    def armar(prompt):
        return {"requiere_humano": False, "motivo": None, "citas": [_id_con(prompt, "500.000")],
                "respuesta": "La aprueba Gerencia, porque supera $ 500.000."}

    r = _consultar("¿Quién aprueba una factura de $ 750.000?", ANALISTA, ClienteQueCita(armar), coleccion)
    assert r.ok and not r.salida.requiere_humano
    assert r.fuentes[0].documento == "matriz_autorizacion"


def test_cita_a_un_fragmento_no_recuperado_falla_en_citas_y_reintenta(coleccion):
    inventada = {"requiere_humano": False, "motivo": None, "citas": ["presupuesto_areas#02"],
                 "respuesta": "Le quedan $ 1.488.000 disponibles."}
    cliente = ClienteFalso(inventada, HUMANO)
    r = _consultar(PREGUNTA_PRESUPUESTO, ANALISTA, cliente, coleccion)
    assert r.ok and r.salida.requiere_humano and r.metricas.intentos == 2
    assert "citas" in cliente.mensajes[1]


def test_sin_fragmentos_requiere_humano_sin_llamar_al_modelo():
    vacia = chromadb.EphemeralClient().create_collection(f"vacia-{uuid.uuid4().hex[:8]}")
    cliente = ClienteFalso()
    r = _consultar("¿Quién aprueba una factura de $ 750.000?", ANALISTA, cliente, vacia)
    assert r.ok and r.salida.requiere_humano and cliente.mensajes == []


def _factura_y_luego(resolucion):
    """Primera llamada (extracción de la PE2): la factura. Segunda (con documentos): la resolución."""
    return lambda prompt: resolucion(prompt) if "Datos de la factura" in prompt else FACTURA


def test_responder_recibe_la_entrada_de_la_pe2_y_devuelve_salida_y_fuentes(coleccion):
    def resolucion(prompt):
        return {"requiere_humano": False, "articulo_erp": "ART-4410", "autoriza": "analista",
                "motivo": None, "citas": [_id_con(prompt, "ART-4410"), _id_con(prompt, "500.000")]}

    r = responder(ENTRADA, ClienteQueCita(_factura_y_luego(resolucion)), coleccion=coleccion,
                  embedder=EmbedderFalso())
    assert r.ok and r.salida.cuit == "30-88888888-4"
    assert r.resolucion.articulo_erp == "ART-4410" and not r.requiere_humano
    assert {f.documento for f in r.fuentes} == {"equivalencias_proveedores", "matriz_autorizacion"}
    assert r.metricas.intentos == 2  # extracción + resolución


def test_un_articulo_que_no_esta_en_lo_citado_es_invalido(coleccion):
    def inventado(prompt):
        return {"requiere_humano": False, "articulo_erp": "ART-9999", "autoriza": "analista",
                "motivo": None, "citas": [_id_con(prompt, "ART-4410")]}

    r = responder(ENTRADA, ClienteQueCita(_factura_y_luego(inventado)), coleccion=coleccion,
                  embedder=EmbedderFalso())
    assert not r.ok and r.requiere_humano and r.tipo_error is TipoError.SALIDA_INVALIDA
    assert "articulo_erp" in [f.campo for f in r.campos_fallidos]


# --- Búsqueda y preguntas ---

def test_los_ids_se_sacan_del_texto_pero_no_los_codigos():
    from src.rag.respuesta import _sin_ids

    assert _sin_ids("Va a la 5.2.10 Mantenimiento catalogo_articulos#02.") == "Va a la 5.2.10 Mantenimiento."
    assert _sin_ids("El mismo número (procedimiento_carga#05).") == "El mismo número."
    assert _sin_ids("El CUIT 30-66666666-2 usa ART-2205.") == "El CUIT 30-66666666-2 usa ART-2205."


def test_tokenizar_conserva_los_codigos_y_sus_partes():
    assert {"art-4410", "art", "4410", "30-66666666-2"} <= set(tokenizar("ART-4410 y CUIT 30-66666666-2"))
    assert "articulo" in tokenizar("Artículo")


def test_archivo_de_preguntas():
    preguntas = json.loads((RAIZ / "datos" / "preguntas.json").read_text("utf-8"))
    assert len(preguntas) == 10
    tipos = [p["tipo"] for p in preguntas]
    assert (tipos.count("normal"), tipos.count("sin_respuesta"), tipos.count("restringida")) == (6, 2, 2)
    documentos = {d.nombre: d for d in leer_carpeta(CARPETA_DOCUMENTOS)}
    for p in preguntas:
        if p["tipo"] == "sin_respuesta":
            assert p["documento_esperado"] is None
            continue
        documento = documentos[p["documento_esperado"]]
        # La evidencia tiene que caer entera en algún fragmento, con las dos configuraciones:
        # si no, la medición por evidencia daría 0 sin que sea culpa de la búsqueda.
        for config in CONFIGS.values():
            assert any(p["evidencia"] in f.texto for f in fragmentar(documento, config)), (p["id"], config.nombre)


def test_respuesta_con_un_codigo_que_no_esta_en_lo_citado_es_invalida(coleccion):
    def armar(prompt):
        # Cita un fragmento real y recuperado, pero que no dice ART-2205.
        return {"requiere_humano": False, "motivo": None, "citas": [_id_con(prompt, "ART-7105")],
                "respuesta": "Para semillas de pastura se usa ART-2205."}

    cliente = ClienteQueCita(armar)
    r = _consultar("¿Qué artículo se usa para semillas o para TELCO DEMO?", ANALISTA, cliente, coleccion)
    assert not r.ok and r.tipo_error is TipoError.SALIDA_INVALIDA
    assert "respuesta" in [f.campo for f in r.campos_fallidos]
