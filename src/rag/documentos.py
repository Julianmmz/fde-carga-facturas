"""Lectura, limpieza y fragmentado de los documentos del cliente.

Cada documento es un Markdown con un encabezado que dice su título y qué roles lo pueden leer:

    ---
    titulo: Matriz de autorización de gastos
    roles: analista, gerencia
    ---

Los permisos viven con el documento: si alguien agrega uno nuevo sin `roles`, la ingesta falla
en lugar de publicarlo para todos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.rag.contratos import Rol

SECCION_INICIAL = "Introducción"


@dataclass(frozen=True)
class ConfigFragmentado:
    nombre: str
    tamano: int  # caracteres máximos por fragmento
    solapamiento: int  # caracteres del final de un fragmento que se repiten al inicio del siguiente


CONFIGS = {
    "chico": ConfigFragmentado("chico", tamano=200, solapamiento=40),
    "grande": ConfigFragmentado("grande", tamano=1000, solapamiento=150),
}


@dataclass(frozen=True)
class Documento:
    nombre: str  # nombre del archivo sin extensión: es el id estable del documento
    titulo: str
    roles: tuple[Rol, ...]
    cuerpo: str


@dataclass(frozen=True)
class FragmentoTexto:
    documento: str
    titulo: str
    seccion: str
    orden: int
    texto: str
    roles: tuple[Rol, ...]

    @property
    def fragmento_id(self) -> str:
        return f"{self.documento}#{self.orden:02d}"


def leer_documento(ruta: Path) -> Documento:
    crudo = ruta.read_text(encoding="utf-8")
    encabezado = re.match(r"^---\n(.*?)\n---\n", crudo, flags=re.S)
    if not encabezado:
        raise ValueError(f"{ruta.name}: falta el encabezado con titulo y roles")
    campos = dict(
        linea.split(":", 1) for linea in encabezado.group(1).splitlines() if ":" in linea
    )
    campos = {clave.strip(): valor.strip() for clave, valor in campos.items()}
    if not campos.get("roles"):
        raise ValueError(f"{ruta.name}: el encabezado no dice qué roles pueden leerlo")
    roles = tuple(Rol(r.strip()) for r in campos["roles"].split(","))
    return Documento(
        nombre=ruta.stem,
        titulo=campos.get("titulo", ruta.stem),
        roles=roles,
        cuerpo=limpiar(crudo[encabezado.end():]),
    )


def limpiar(texto: str) -> str:
    """Saca lo que no aporta a la búsqueda: comentarios, énfasis de Markdown y espacios de más."""
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.S)
    texto = texto.replace("**", "").replace("__", "")
    # Separador de tablas (|---|---|), con su salto de línea: si queda una línea vacía entre el
    # encabezado y la primera fila, la primera fila se confunde con el encabezado.
    texto = re.sub(r"^\|[-| :]+\|\n", "", texto, flags=re.M)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r" *\n *", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def secciones(documento: Documento) -> list[tuple[str, str]]:
    """Divide por títulos `##`. Lo que está antes del primero queda en la sección inicial."""
    actual, lineas, resultado = SECCION_INICIAL, [], []
    for linea in documento.cuerpo.splitlines():
        if linea.startswith("## "):
            resultado.append((actual, "\n".join(lineas).strip()))
            actual, lineas = linea[3:].strip(), []
        elif not linea.startswith("# "):  # el título del documento ya va en los metadatos
            lineas.append(linea)
    resultado.append((actual, "\n".join(lineas).strip()))
    return [(seccion, contenido) for seccion, contenido in resultado if contenido]


def _partir(contenido: str, config: ConfigFragmentado) -> list[str]:
    """Junta líneas enteras hasta el tamaño: una fila de tabla nunca queda cortada al medio."""
    lineas = []
    for linea in contenido.splitlines():
        # Una línea más larga que el fragmento se corta por palabras.
        while len(linea) > config.tamano:
            corte = linea.rfind(" ", 0, config.tamano)
            corte = corte if corte > 0 else config.tamano
            lineas.append(linea[:corte])
            linea = linea[corte:].strip()
        lineas.append(linea)

    partes, actual, encabezado = [], [], None
    for i, linea in enumerate(lineas):
        es_fila = linea.startswith("|")
        if es_fila and (i == 0 or not lineas[i - 1].startswith("|")):
            encabezado = linea
        elif not es_fila:
            encabezado = None
        if actual and len("\n".join(actual + [linea])) > config.tamano:
            partes.append("\n".join(actual))
            # Solapamiento: las últimas líneas del fragmento anterior, si entran.
            arrastre = []
            for previa in reversed(actual):
                if len("\n".join([previa] + arrastre)) > config.solapamiento:
                    break
                arrastre.insert(0, previa)
            # Una fila de tabla sin su encabezado pierde el sentido de las columnas.
            if es_fila and encabezado and encabezado not in arrastre and linea != encabezado:
                arrastre.insert(0, encabezado)
            actual = arrastre
        actual.append(linea)
    if actual:
        partes.append("\n".join(actual))
    return [p.strip() for p in partes if p.strip()]


def fragmentar(documento: Documento, config: ConfigFragmentado) -> list[FragmentoTexto]:
    fragmentos = []
    for seccion, contenido in secciones(documento):
        for parte in _partir(contenido, config):
            fragmentos.append(FragmentoTexto(
                documento=documento.nombre,
                titulo=documento.titulo,
                seccion=seccion,
                orden=len(fragmentos) + 1,
                texto=parte,
                roles=documento.roles,
            ))
    return fragmentos


def leer_carpeta(carpeta: Path) -> list[Documento]:
    rutas = sorted(carpeta.glob("*.md"))
    if not rutas:
        raise FileNotFoundError(f"No hay documentos .md en {carpeta}")
    return [leer_documento(ruta) for ruta in rutas]
