"""Respuestas con fuentes sobre lo recuperado.

- consultar(entrada): preguntas de la HU3 (matriz, procedimiento, catálogo, equivalencias).
- responder(entrada): recibe la Entrada de la PE2 (una factura), extrae los datos con el motor y
  resuelve con los documentos el artículo del ERP y quién autoriza.

Si no hay evidencia suficiente, la respuesta sale con requiere_humano = true: o porque la
búsqueda no trajo nada para ese rol (y entonces no se llama al modelo), o porque el modelo
indicó que los fragmentos no alcanzan.

Uso: python -m src.rag.respuesta --rol analista "¿Quién aprueba una factura de $ 750.000?"
     python -m src.rag.respuesta --factura 1
"""

from __future__ import annotations

import argparse
import logging
import re
import sys

from chromadb.api.models.Collection import Collection
from pydantic import ValidationError

from src.contratos import Clasificacion, Entrada, Resultado, TipoError
from src.motor import (
    FalloLLM,
    Medidor,
    _campos_fallidos,
    cargar_casos,
    crear_cliente,
    generar_validado,
    procesar_factura,
)
from src.prompts import (
    PLANTILLA_CONSULTA,
    PLANTILLA_FRAGMENTO,
    PLANTILLA_REINTENTO_RAG,
    PLANTILLA_RESOLUCION,
    PROMPT_SISTEMA_CONSULTA,
    PROMPT_SISTEMA_RESOLUCION,
)
from src.proveedores import ClienteLLM, ErrorProveedor
from src.rag.busqueda import buscar
from src.rag.contratos import (
    EntradaConsulta,
    Fragmento,
    Fuente,
    ResolucionFactura,
    RespuestaConsulta,
    RespuestaFactura,
    Rol,
    SalidaConsulta,
    Usuario,
)
from src.rag.embeddings import Embedder

K = 4
SIN_EVIDENCIA = "La búsqueda no encontró documentos accesibles para este usuario que respondan esto."


def _bloque(fragmentos: list[Fragmento]) -> str:
    return "\n\n".join(
        PLANTILLA_FRAGMENTO.format(
            fragmento_id=f.fragmento_id, titulo=f.titulo, seccion=f.seccion, texto=f.texto
        )
        for f in fragmentos
    )


def _fuentes(citas: list[str], fragmentos: list[Fragmento]) -> list[Fuente]:
    por_id = {f.fragmento_id: f for f in fragmentos}
    return [
        Fuente(documento=por_id[c].documento, seccion=por_id[c].seccion, fragmento_id=c)
        for c in dict.fromkeys(citas)
    ]


def consultar(
    datos: dict,
    cliente: ClienteLLM,
    *,
    coleccion: Collection | None = None,
    embedder: Embedder | None = None,
) -> RespuestaConsulta:
    medidor = Medidor(cliente.modelo)
    try:
        entrada = EntradaConsulta.model_validate(datos)
    except ValidationError as exc:
        return RespuestaConsulta(
            ok=False, tipo_error=TipoError.ENTRADA_INVALIDA,
            mensaje="La consulta no cumple el contrato; no se buscó nada.",
            campos_fallidos=_campos_fallidos(exc), metricas=medidor.metricas(),
        )

    try:
        fragmentos = buscar(entrada.pregunta, entrada.usuario, K, coleccion=coleccion, embedder=embedder)
    except ErrorProveedor as exc:
        return RespuestaConsulta(ok=False, tipo_error=exc.tipo, mensaje=exc.mensaje, metricas=medidor.metricas())
    if not fragmentos:
        salida = SalidaConsulta(requiere_humano=True, respuesta=None, motivo=SIN_EVIDENCIA, citas=[])
        return RespuestaConsulta(ok=True, salida=salida, metricas=medidor.metricas())

    mensaje = PLANTILLA_CONSULTA.format(pregunta=entrada.pregunta, fragmentos=_bloque(fragmentos))
    contexto = {"recuperados": {f.fragmento_id: f.texto for f in fragmentos}}
    try:
        salida = generar_validado(
            cliente, PROMPT_SISTEMA_CONSULTA, mensaje, SalidaConsulta, medidor,
            PLANTILLA_REINTENTO_RAG, contexto,
        )
    except FalloLLM as fallo:
        return RespuestaConsulta(
            ok=False, tipo_error=fallo.tipo, mensaje=fallo.mensaje,
            campos_fallidos=fallo.campos_fallidos, metricas=medidor.metricas(),
        )
    if salida.respuesta:
        salida = salida.model_copy(update={"respuesta": _sin_ids(salida.respuesta)})
    return RespuestaConsulta(
        ok=True, salida=salida, fuentes=_fuentes(salida.citas, fragmentos), metricas=medidor.metricas()
    )


def _sin_ids(texto: str) -> str:
    """Saca los ids de fragmento del texto: ya están validados en `citas`.

    gemini-3.5-flash-lite los repite dentro de la respuesta ("... Mantenimiento catalogo_articulos#02.")
    aunque el prompt le pide que no. Rechazarlo costaría un reintento en 4 de cada 6 preguntas.
    """
    texto = re.sub(r"\s*[\(\[]?\b[a-z_]+#\d{2}\b(?:\s*,\s*[a-z_]+#\d{2}\b)*[\)\]]?", "", texto)
    return re.sub(r"\s+([.,;])", r"\1", texto).strip()


def responder(
    datos: dict,
    cliente: ClienteLLM,
    *,
    coleccion: Collection | None = None,
    embedder: Embedder | None = None,
) -> RespuestaFactura:
    """Entrada de la PE2 -> Salida de la PE2 + artículo del ERP, quién autoriza y fuentes."""
    extraccion: Resultado = procesar_factura(datos, cliente)
    if not extraccion.ok:
        return RespuestaFactura(
            ok=False, requiere_humano=True, tipo_error=extraccion.tipo_error,
            mensaje=extraccion.mensaje, campos_fallidos=extraccion.campos_fallidos,
            metricas=extraccion.metricas,
        )
    salida = extraccion.salida
    if salida.clasificacion is not Clasificacion.FACTURA:
        # Una factura dudosa o un documento que no es factura ya van a revisión completa.
        return RespuestaFactura(
            ok=True, salida=salida, requiere_humano=True, metricas=extraccion.metricas,
            mensaje="La factura no se resolvió contra los documentos: requiere revisión completa.",
        )

    # El usuario es el analista que carga: ve lo que ve su rol, nada más.
    usuario = Usuario(id=Entrada.model_validate(datos).analista, rol=Rol.ANALISTA)
    consultas = [
        f"artículo del ERP para {salida.razon_social} CUIT {salida.cuit} {salida.producto_servicio}",
        f"quién autoriza una factura de importe total $ {salida.total}",
    ]
    medidor = Medidor(cliente.modelo)
    try:
        fragmentos: dict[str, Fragmento] = {}
        for consulta in consultas:
            for f in buscar(consulta, usuario, K, coleccion=coleccion, embedder=embedder):
                fragmentos.setdefault(f.fragmento_id, f)
    except ErrorProveedor as exc:
        return RespuestaFactura(
            ok=False, salida=salida, requiere_humano=True, tipo_error=exc.tipo,
            mensaje=exc.mensaje, metricas=extraccion.metricas,
        )

    mensaje = PLANTILLA_RESOLUCION.format(
        cuit=salida.cuit, razon_social=salida.razon_social,
        producto_servicio=salida.producto_servicio, total=salida.total,
        fragmentos=_bloque(list(fragmentos.values())),
    )
    contexto = {"recuperados": {i: f.texto for i, f in fragmentos.items()}}
    try:
        resolucion = generar_validado(
            cliente, PROMPT_SISTEMA_RESOLUCION, mensaje, ResolucionFactura, medidor,
            PLANTILLA_REINTENTO_RAG, contexto,
        )
    except FalloLLM as fallo:
        return RespuestaFactura(
            ok=False, salida=salida, requiere_humano=True, tipo_error=fallo.tipo,
            mensaje=fallo.mensaje, campos_fallidos=fallo.campos_fallidos,
            metricas=_sumar(extraccion, medidor),
        )
    return RespuestaFactura(
        ok=True, salida=salida, resolucion=resolucion,
        fuentes=_fuentes(resolucion.citas, list(fragmentos.values())),
        requiere_humano=resolucion.requiere_humano or salida.requiere_revision_completa,
        metricas=_sumar(extraccion, medidor),
    )


def _sumar(extraccion: Resultado, medidor: Medidor):
    """Métricas de la operación completa: extracción (PE2) + resolución con documentos."""
    previa, actual = extraccion.metricas, medidor.metricas()
    return actual.model_copy(update={
        "intentos": previa.intentos + actual.intentos,
        "tokens_entrada": previa.tokens_entrada + actual.tokens_entrada,
        "tokens_salida": previa.tokens_salida + actual.tokens_salida,
        "tokens_razonamiento": previa.tokens_razonamiento + actual.tokens_razonamiento,
        "costo_usd": round(previa.costo_usd + actual.costo_usd, 8),
        "latencia_ms": previa.latencia_ms + actual.latencia_ms,
    })


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Consulta los documentos o resuelve una factura")
    parser.add_argument("pregunta", nargs="?")
    parser.add_argument("--rol", choices=[r.value for r in Rol], default="analista")
    parser.add_argument("--factura", help="id de un caso de datos/casos.json")
    args = parser.parse_args()
    if not args.pregunta and not args.factura:
        parser.error("indicá una pregunta o --factura")

    cliente = crear_cliente()
    if isinstance(cliente, Resultado):
        print(cliente.mensaje, file=sys.stderr)
        return 1
    if args.factura:
        caso = next((c for c in cargar_casos() if str(c["id"]) == args.factura), None)
        if caso is None:
            print(f"No existe el caso {args.factura}.", file=sys.stderr)
            return 2
        resultado = responder(caso["entrada"], cliente)
    else:
        usuario = {"id": f"demo.{args.rol}", "rol": args.rol}
        resultado = consultar({"pregunta": args.pregunta, "usuario": usuario}, cliente)
    print(resultado.model_dump_json(indent=2, exclude_none=True))
    return 0 if resultado.ok else 1


if __name__ == "__main__":
    sys.exit(main())
