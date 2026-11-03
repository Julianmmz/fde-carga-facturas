# fde-carga-facturas

Sistema de carga asistida de facturas de proveedores del curso Forward Deployed Engineer.

- **PE2 — motor LLM:** recibe el texto de una factura y devuelve un borrador validado con
  Pydantic, más tokens, costo y latencia (HU1 de la ficha de la Pre-Entrega 1).
- **PE3 — RAG con permisos:** responde con los documentos del cliente (matriz de autorización,
  procedimiento, catálogo de artículos, equivalencias y presupuesto) con búsqueda híbrida,
  filtro por rol en la consulta a la base y fuentes citadas (HU3). Ver [RAG](#rag-pe3).

Un analista revisa siempre el borrador: el motor no registra nada en ningún sistema. Todos
los datos del repositorio son ficticios.

**Evidencia:** las salidas reales de terminal están en este README: [PE3](#evidencia-pe3--salidas-reales),
[PE2](#evidencia-pe2--salidas-reales) y [contratos en código literal](#contratos-código-literal).
Las salidas completas están en `resultados/`.

**Proveedor del modelo de lenguaje: Gemini (`gemini-3.5-flash-lite`).** Todas las corridas reales de
este repositorio se hicieron con Gemini, porque no hay crédito en Anthropic ni en OpenAI. El
cliente de Anthropic (`claude-opus-5-5`) está implementado y probado con tests, pero **no se corrió
contra la API real**. Con `ANTHROPIC_API_KEY` en el `.env` pasa a ser el principal y Gemini queda de
respaldo, sin tocar el resto del código.

## Cómo correrlo

Requiere Python 3.12. Desde la carpeta del repositorio, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Completá en `.env` al menos una clave:

- `ANTHROPIC_API_KEY`: proveedor principal.
- `GEMINI_API_KEY`: respaldo. Se usa si Anthropic falla o si no hay clave de Anthropic.

Opcionales: `ANTHROPIC_MODEL` y `GEMINI_MODEL` cambian el modelo. Tiene que ser un id exacto
con precio en `src/precios.py` (por ejemplo `claude-opus-5-5`, no "Opus 5.5" ni un alias
`-latest`): sin precio conocido el costo sería inventado, y el motor no arranca.

```powershell
# Tests, sin llamar a ninguna API
.\.venv\Scripts\python.exe -m pytest -q

# Una factura de datos/casos.json
.\.venv\Scripts\python.exe -m src.motor --caso 1

# Demostración de error: CUIT con dígito verificador incorrecto (falla y reintenta)
.\.venv\Scripts\python.exe -m src.motor --caso error_cuit

# Demostración de error: entrada fuera de contrato (no llama al modelo)
.\.venv\Scripts\python.exe -m src.motor --caso error_entrada

# Demostración del error final: falla dos veces y devuelve salida_invalida (cliente simulado, sin clave)
.\.venv\Scripts\python.exe -m src.demo_salida_invalida

# Las 5 consultas de medición; guarda resultados/medicion.json
.\.venv\Scripts\python.exe -m src.medir
```

## Estructura

| Archivo | Qué hace |
|---|---|
| `src/contratos.py` | Clases `Entrada` y `Salida`, reglas de validación y error estructurado |
| `src/prompts.py` | Plantillas del prompt, separadas del código |
| `src/proveedores.py` | Clientes de Anthropic y Gemini, y el respaldo entre ellos |
| `src/precios.py` | Precios publicados y cálculo del costo |
| `src/motor.py` | Validación, llamada, un reintento y métricas |
| `src/medir.py` | Corre las 5 consultas y arma la tabla |
| `src/demo_salida_invalida.py` | Muestra el ciclo campo que falla → reintento → error estructurado |
| `datos/casos.json` | Facturas ficticias y valores esperados |
| `resultados/` | Salidas reales de terminal de cada entrega (medidas con `gemini-3.5-flash-lite`) |

## Qué pasa cuando algo falla

- **Entrada inválida:** no se llama al modelo.
- **Respuesta inválida:** se registra qué campo falló, se reintenta una vez avisándole al
  modelo los errores y, si sigue mal, devuelve un error estructurado para cargar a mano.
- **Falla del proveedor** (clave, límite de uso, conexión): se repite la consulta en el
  respaldo. Si también falla, devuelve un error estructurado.

## RAG (PE3)

Base vectorial: **ChromaDB** (local, en `.chroma/`, no se sube al repositorio). Embeddings:
**`gemini-embedding-2`** (768 dimensiones), con la misma `GEMINI_API_KEY`. Búsqueda por palabras
clave: **BM25**. Los dos rankings se fusionan con RRF.

```powershell
# 1. Ingesta: un solo comando procesa documentos/ con las dos configuraciones de fragmentado
.\.venv\Scripts\python.exe -m src.rag.ingesta

# 2. Consultas (HU3), con el rol de quien pregunta
.\.venv\Scripts\python.exe -m src.rag.respuesta --rol analista "¿Quién aprueba una factura de $ 750.000?"
.\.venv\Scripts\python.exe -m src.rag.respuesta --rol gerencia "¿Cuánto presupuesto le queda a Logística?"

# 3. Factura de la PE2 resuelta con los documentos: artículo del ERP, quién autoriza y fuentes
.\.venv\Scripts\python.exe -m src.rag.respuesta --factura 1

# 4. Consultas exactas donde la híbrida acierta y la solo vectorial falla
.\.venv\Scripts\python.exe -m src.rag.comparar

# 5. Medición top-k de las 10 preguntas con las dos configuraciones (y --respuestas: punta a punta)
.\.venv\Scripts\python.exe -m src.rag.medir --respuestas
```

| Archivo | Qué hace |
|---|---|
| `documentos/*.md` | Documentos ficticios del cliente. El encabezado de cada uno dice qué roles lo leen |
| `src/rag/documentos.py` | Lectura, limpieza y fragmentado (por sección y por tamaño) |
| `src/rag/embeddings.py` | Embeddings con Gemini, con espera ante el límite del nivel gratuito |
| `src/rag/ingesta.py` | `ingestar()`: carpeta → colección de ChromaDB con metadatos |
| `src/rag/busqueda.py` | `buscar(consulta, usuario, k)`: vectorial + BM25 + RRF, con el filtro por rol |
| `src/rag/respuesta.py` | `consultar()` (HU3) y `responder()` (factura de la PE2) con fuentes |
| `src/rag/contratos.py` | Usuario, roles, fragmentos, fuentes y validación de las citas |
| `src/rag/medir.py`, `src/rag/comparar.py` | Medición top-k y comparación vectorial / híbrida |
| `datos/preguntas.json` | 10 preguntas: 6 normales, 2 sin respuesta y 2 sobre el documento restringido |
| `tests/test_rag.py` | Permisos por rol, prompt sin datos restringidos, citas, ingesta (sin API) |

**Permisos:** `presupuesto_areas` es solo de Gerencia. El filtro por rol va en la consulta a
ChromaDB (`where`), tanto en la búsqueda vectorial como al armar el índice BM25: lo que el rol no
puede leer nunca sale de la base, así que no llega ni a los resultados ni al prompt.

**Sin evidencia:** si la búsqueda no trae nada para ese rol, devuelve `requiere_humano = true`
sin llamar al modelo; si trae algo pero no alcanza, el modelo lo indica. Una cita a un fragmento
no recuperado, o un código que no figura en lo citado, es un error de validación y se reintenta.

## Evidencia PE3 — salidas reales

Corridas del 03/11/2026 con `gemini-embedding-2` y `gemini-3.5-flash-lite`. Archivos completos en `resultados/`.

### Ingesta (`resultados/ingesta_rag.txt`)

```text
PS> .\.venv\Scripts\python.exe -m src.rag.ingesta

== facturas_chico: 56 fragmentos (tamaño 200, solapamiento 40) ==
  catalogo_articulos            15 fragmentos  6 secciones  roles=['analista', 'gerencia']
  equivalencias_proveedores     10 fragmentos  6 secciones  roles=['analista', 'gerencia']
  matriz_autorizacion           11 fragmentos  6 secciones  roles=['analista', 'gerencia']
  presupuesto_areas              8 fragmentos  4 secciones  roles=['gerencia']
  procedimiento_carga           12 fragmentos  7 secciones  roles=['analista', 'gerencia']

== facturas_grande: 29 fragmentos (tamaño 1000, solapamiento 150) ==
  catalogo_articulos             6 fragmentos  6 secciones  roles=['analista', 'gerencia']
  equivalencias_proveedores      6 fragmentos  6 secciones  roles=['analista', 'gerencia']
  matriz_autorizacion            6 fragmentos  6 secciones  roles=['analista', 'gerencia']
  presupuesto_areas              4 fragmentos  4 secciones  roles=['gerencia']
  procedimiento_carga            7 fragmentos  7 secciones  roles=['analista', 'gerencia']

Ejemplo de fragmento:
{
  "id": "presupuesto_areas#02",
  "metadatos": {
    "roles": [
      "gerencia"
    ],
    "documento": "presupuesto_areas",
    "titulo": "Presupuesto mensual por área y consumo",
    "config": "grande",
    "orden": 2,
    "seccion": "Consumo por área"
  },
  "texto": "| Área | Presupuesto mensual | Consumido al 02/11/2026 | Disponible | % consumido |\n| Planta de acopio | $ 9.000.000 | … |\n| Logística | $ 6.200.000 | $ 4.712.000 | $ 1.488.000 | 76 % |\n| … |"
}
```

### Tests de permisos por rol (`resultados/tests_rag.txt`, sin API)

```text
PS> .\.venv\Scripts\python.exe -m pytest -v tests/test_rag.py
tests/test_rag.py::test_cada_fragmento_lleva_documento_seccion_y_roles PASSED [  4%]
tests/test_rag.py::test_un_documento_sin_roles_no_se_ingesta PASSED      [  9%]
tests/test_rag.py::test_limpieza_saca_comentarios_y_separadores_de_tabla PASSED [ 13%]
tests/test_rag.py::test_fragmento_chico_repite_el_encabezado_de_la_tabla PASSED [ 18%]
tests/test_rag.py::test_ninguna_configuracion_supera_su_tamano PASSED    [ 22%]
tests/test_rag.py::test_analista_no_recibe_nada_del_presupuesto[hibrida] PASSED [ 27%]
tests/test_rag.py::test_analista_no_recibe_nada_del_presupuesto[vectorial] PASSED [ 31%]
tests/test_rag.py::test_analista_no_recibe_nada_del_presupuesto[palabras] PASSED [ 36%]
tests/test_rag.py::test_gerencia_si_recibe_el_presupuesto PASSED         [ 40%]
tests/test_rag.py::test_el_filtro_por_rol_va_en_cada_consulta_a_la_base[analista] PASSED [ 45%]
tests/test_rag.py::test_el_filtro_por_rol_va_en_cada_consulta_a_la_base[gerencia] PASSED [ 50%]
tests/test_rag.py::test_analista_no_ve_el_presupuesto_ni_en_el_prompt PASSED [ 54%]
tests/test_rag.py::test_gerencia_ve_el_presupuesto_en_el_prompt PASSED   [ 59%]
tests/test_rag.py::test_respuesta_cita_el_documento_de_origen PASSED     [ 63%]
tests/test_rag.py::test_cita_a_un_fragmento_no_recuperado_falla_en_citas_y_reintenta PASSED [ 68%]
tests/test_rag.py::test_sin_fragmentos_requiere_humano_sin_llamar_al_modelo PASSED [ 72%]
tests/test_rag.py::test_responder_recibe_la_entrada_de_la_pe2_y_devuelve_salida_y_fuentes PASSED [ 77%]
tests/test_rag.py::test_un_articulo_que_no_esta_en_lo_citado_es_invalido PASSED [ 81%]
tests/test_rag.py::test_los_ids_se_sacan_del_texto_pero_no_los_codigos PASSED [ 86%]
tests/test_rag.py::test_tokenizar_conserva_los_codigos_y_sus_partes PASSED [ 90%]
tests/test_rag.py::test_archivo_de_preguntas PASSED                      [ 95%]
tests/test_rag.py::test_respuesta_con_un_codigo_que_no_esta_en_lo_citado_es_invalida PASSED [100%]
============================= 22 passed in 3.67s ==============================
```

`test_el_filtro_por_rol_va_en_cada_consulta_a_la_base` espía la colección y verifica que **todas**
las llamadas a ChromaDB (`query` y `get`) lleven `where={"roles": {"$contains": rol}}`.
`test_analista_no_ve_el_presupuesto_ni_en_el_prompt` captura el mensaje real enviado al modelo y
verifica que no tenga ningún dato del documento restringido. Su control positivo
(`test_gerencia_ve_el_presupuesto_en_el_prompt`) muestra que el mismo chequeo sí los encuentra
cuando el rol tiene acceso.

### Búsqueda híbrida contra solo vectorial (`resultados/comparar_hibrida.txt`)

```text
PS> .\.venv\Scripts\python.exe -m src.rag.comparar
Colección facturas_chico, usuario analista, top-3

Consulta: ¿Qué es el artículo ART-4401?   (acierta si el 1.º contiene ART-4401)
  vectorial FALLA   ['catalogo_articulos#04', 'catalogo_articulos#02', 'catalogo_articulos#05']  1.º: | ART-4411 | Materiales y repuestos eléctricos | 5.2.11 Repuestos | Pl
  palabras  ACIERTA ['equivalencias_proveedores#05', 'catalogo_articulos#02', 'equivalencias_proveedores#02']  1.º: | 30-44444444-0 | METALÚRGICA DEMO S.A. | Mantenimiento de norias y se
  hibrida   ACIERTA ['catalogo_articulos#02', 'equivalencias_proveedores#05', 'equivalencias_proveedores#04']  1.º: | ART-4401 | Mantenimiento mecánico de maquinaria de planta | 5.2.10 M

Consulta: ¿A qué cuenta imputa el artículo ART-6102?   (acierta si el 1.º contiene ART-6102)
  vectorial FALLA   ['catalogo_articulos#10', 'catalogo_articulos#09', 'catalogo_articulos#12']  1.º: | ART-6120 | Honorarios de asesoramiento impositivo y contable | 5.4.0
  palabras  ACIERTA ['catalogo_articulos#09', 'catalogo_articulos#01', 'equivalencias_proveedores#02']  1.º: | ART-6102 | Honorarios legales | 5.4.02 Honorarios | Administración |
  hibrida   ACIERTA ['catalogo_articulos#09', 'equivalencias_proveedores#07', 'catalogo_articulos#01']  1.º: | ART-6102 | Honorarios legales | 5.4.02 Honorarios | Administración |

Consulta: ¿Qué artículo se usa para el CUIT 30-66666666-2?   (acierta si el 1.º contiene 30-66666666-2)
  vectorial FALLA   ['equivalencias_proveedores#04', 'equivalencias_proveedores#07', 'equivalencias_proveedores#09']  1.º: | 30-88888888-4 | ELECTRO SERVICIOS DEMO S.R.L. | Venta de repuestos e
  palabras  ACIERTA ['equivalencias_proveedores#06', 'equivalencias_proveedores#02', 'equivalencias_proveedores#01']  1.º: | 30-66666666-2 | AGROINSUMOS DEMO S.A. | Fertilizantes | ART-2310 |
  hibrida   ACIERTA ['equivalencias_proveedores#06', 'equivalencias_proveedores#04', 'equivalencias_proveedores#07']  1.º: | 30-66666666-2 | AGROINSUMOS DEMO S.A. | Fertilizantes | ART-2310 |

Consulta: ¿Qué factura el proveedor con CUIT 30-44444444-0?   (acierta si el 1.º contiene 30-44444444-0)
  vectorial FALLA   ['equivalencias_proveedores#04', 'equivalencias_proveedores#05', 'equivalencias_proveedores#03']  1.º: | 30-88888888-4 | ELECTRO SERVICIOS DEMO S.R.L. | Venta de repuestos e
  palabras  ACIERTA ['equivalencias_proveedores#05', 'equivalencias_proveedores#06', 'equivalencias_proveedores#08']  1.º: | 30-44444444-0 | METALÚRGICA DEMO S.A. | Mantenimiento de norias y se
  hibrida   ACIERTA ['equivalencias_proveedores#05', 'equivalencias_proveedores#04', 'equivalencias_proveedores#06']  1.º: | 30-44444444-0 | METALÚRGICA DEMO S.A. | Mantenimiento de norias y se
```

Estas fallas de la vectorial aparecen con fragmentos **chicos**, donde cada fila es un fragmento.
Con los grandes, que ganaron la medición, cada fragmento es la tabla entera y la vectorial también
lo encuentra. La híbrida se vuelve imprescindible cuando el catálogo real no entra en una sección.

### Respuesta con fuentes y `requiere_humano` (`resultados/consulta_*.txt`)

```text
PS> .\.venv\Scripts\python.exe -m src.rag.respuesta --rol analista "¿Qué artículo del ERP se usa para las semillas de pastura del CUIT 30-66666666-2?"
{
  "ok": true,
  "salida": {
    "requiere_humano": false,
    "respuesta": "Para las semillas de pastura del CUIT 30-66666666-2 se utiliza el artículo del ERP ART-2205.",
    "citas": [
      "equivalencias_proveedores#03"
    ]
  },
  "fuentes": [
    {
      "documento": "equivalencias_proveedores",
      "seccion": "Proveedores de insumos",
      "fragmento_id": "equivalencias_proveedores#03"
    }
  ],
  "campos_fallidos": [],
  "metricas": {
    "modelo": "gemini-3.5-flash-lite",
    "intentos": 1,
    "tokens_entrada": 806,
    "tokens_salida": 79,
    "tokens_razonamiento": 0,
    "costo_usd": 0.0004393,
    "latencia_ms": 3931
  }
}

PS> .\.venv\Scripts\python.exe -m src.rag.respuesta --rol analista "¿Qué artículo del ERP uso para las facturas de TRANSPORTES DEMO?"
{
  "ok": true,
  "salida": {
    "requiere_humano": true,
    "motivo": "El proveedor TRANSPORTES DEMO no tiene equivalencia cargada porque factura fletes y no está definido si son de insumos o de granos.",
    "citas": [
      "equivalencias_proveedores#06"
    ]
  },
  "fuentes": [
    {
      "documento": "equivalencias_proveedores",
      "seccion": "Proveedores sin equivalencia cargada",
      "fragmento_id": "equivalencias_proveedores#06"
    }
  ],
  "campos_fallidos": [],
  "metricas": {
    "modelo": "gemini-3.5-flash-lite",
    "intentos": 1,
    "tokens_entrada": 748,
    "tokens_salida": 63,
    "tokens_razonamiento": 0,
    "costo_usd": 0.0003819,
    "latencia_ms": 4756
  }
}
```

`responder()` con la factura 1 de la PE2 (`resultados/responder_factura.txt`) devuelve la `Salida`
de la PE2 más esta resolución, con dos llamadas reales (2.213 / 488 tokens, USD 0,0019):

```text
  "resolucion": {
    "requiere_humano": false,
    "articulo_erp": "ART-4410",
    "autoriza": "analista",
    "citas": [
      "equivalencias_proveedores#02",
      "matriz_autorizacion#03",
      "matriz_autorizacion#02"
    ]
  },
```

### Las 10 preguntas y la medición top-k (`resultados/medicion_rag.txt`)

```text
PS> .\.venv\Scripts\python.exe -m src.rag.medir --respuestas
| Fragmentado | Modo | Documento top-1 | Documento top-3 | Evidencia top-1 | Evidencia top-3 | Fugas restringidas |
|---|---|---|---|---|---|---|
| chico (200 car.) | vectorial | 7/8 | 8/8 | 5/8 | 8/8 | 0/2 |
| chico (200 car.) | hibrida | 8/8 | 8/8 | 6/8 | 8/8 | 0/2 |
| grande (1000 car.) | vectorial | 8/8 | 8/8 | 8/8 | 8/8 | 0/2 |
| grande (1000 car.) | hibrida | 8/8 | 8/8 | 8/8 | 8/8 | 0/2 |

Ganadora: grande

Respuestas con la configuración grande:

| # | Tipo | Esperado | Obtenido | Fuentes |
|---|---|---|---|---|
| 1 | normal | responde | responde (ok) | matriz_autorizacion#03 |
| 2 | normal | responde | responde (ok) | catalogo_articulos#02 |
| 3 | normal | responde | responde (ok) | equivalencias_proveedores#03 |
| 4 | normal | responde | responde (ok) | equivalencias_proveedores#05 |
| 5 | normal | responde | responde (ok) | procedimiento_carga#05 |
| 6 | normal | responde | responde (ok) | procedimiento_carga#07 |
| 7 | sin_respuesta | requiere_humano | requiere_humano (ok) | - |
| 8 | sin_respuesta | requiere_humano | requiere_humano (ok) | - |
| 9 | restringida | requiere_humano | requiere_humano (ok) | - |
| 10 | restringida | requiere_humano | requiere_humano (ok) | - |

Comportamiento correcto: 10/10
```

Las preguntas están en `datos/preguntas.json`, cada una con su documento esperado. **Documento**
mide si el documento correcto está entre los primeros k, que es lo que pide la consigna.
**Evidencia** mide si está el fragmento que contiene la respuesta: con 5 documentos, el acierto por
documento sale 8/8 casi siempre y no distingue entre configuraciones.

## Evidencia PE2 — salidas reales

Archivos completos en `resultados/`.

### Medición de 5 consultas (`resultados/medicion.txt`)

```text
| # | Consulta | ¿Validó? | Tokens (ent/sal) | Costo USD | Latencia ms |
|---|---|---|---|---|---|
| 1 | Factura A clara, 21% | sí | 703 / 391 | 0.001188 | 5849 |
| 2 | Factura C de monotributista | sí | 670 / 377 | 0.001143 | 1577 |
| 3 | Factura A clara, 10,5% | sí | 648 / 388 | 0.001164 | 1805 |
| 4 | Confusa: CUIT ilegible y total que no cierra | sí | 1140 / 751 | 0.002220 | 3159 |
| 5 | Fuera de tema: no es una factura | sí | 448 / 309 | 0.000907 | 1419 |
| | Promedio | 5/5 | 722 / 443 | 0.001325 | 2762 |
```

### Falla de validación con el modelo real (`resultados/ejecucion_error.txt`)

El CUIT `30-88888888-5` tiene el dígito verificador mal. El primer intento falla en `cuit`, el
reintento le avisa al modelo y el modelo la reclasifica como `dudoso` con `cuit` en null:

```text
WARNING motor: intento=1 campo=cuit regla=value_error mensaje=Value error, el dígito verificador del CUIT no coincide
INFO httpx: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent "HTTP/1.1 200 OK"
{
  "ok": true,
  "salida": {
    "clasificacion": "dudoso",
    "motivo": "El dígito verificador del CUIT informado no coincide con el algoritmo de validación de CUIT.",
    …
    "campos_a_revisar": [
      "cuit"
    ],
    "requiere_revision_completa": true
  },
  "metricas": {
    "modelo": "gemini-3.5-flash-lite",
    "intentos": 2,
    …
```

**Falla después del reintento:** con el modelo real no se consiguió reproducir un caso que falle
dos veces seguidas; en todos los casos probados se recuperó en el reintento. El error estructurado
`salida_invalida` está cubierto por tests y por `python -m src.demo_salida_invalida`, que usa un
**cliente simulado** (`resultados/ejecucion_salida_invalida.txt`). No es una corrida real.

`tests/test_motor.py::test_confianza_alta_no_salva_un_dato_incorrecto` prueba que una respuesta
con confianza 1.0 en todos los campos pero con un CUIT, un total o un IVA incorrectos igual se
rechaza: la que frena el error es la validación local, no la confianza que declara el modelo.

## Contratos (código literal)

Fragmentos copiados del código sin modificar, con su rango de líneas. Las **validaciones declarativas** son tipos, `Enum` y `Field(...)`: Pydantic las chequea campo por campo. Las **reglas de negocio** cruzan campos o dependen de lo recuperado: van en validadores, y cada error indica el campo que falló.

### PE2 — validaciones declarativas

`src/contratos.py`, líneas 30–54:

```python
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
```

`src/contratos.py`, líneas 61–90:

```python
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
```

`src/contratos.py`, líneas 129–148:

```python
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
```

### PE2 — reglas de negocio

`src/contratos.py`, líneas 150–189:

```python
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
```

### PE3 — validaciones declarativas

`src/rag/contratos.py`, líneas 26–29:

```python
FragmentoId = Annotated[str, Field(pattern=r"^[a-z_]+#\d{2}$")]
CodigoArticulo = Annotated[str, Field(pattern=r"^ART-\d{4}$")]
# Datos exactos que, si aparecen en una respuesta, tienen que estar escritos en lo citado.
DATOS_EXACTOS = re.compile(r"ART-\d{4}|\b\d{2}-\d{8}-\d\b")
```

`src/rag/contratos.py`, líneas 39–46:

```python
class Rol(str, Enum):
    ANALISTA = "analista"
    GERENCIA = "gerencia"


class Usuario(Contrato):
    id: IdUsuario
    rol: Rol
```

`src/rag/contratos.py`, líneas 81–89:

```python
class SalidaConsulta(Contrato):
    """Lo que devuelve el modelo para una consulta de la HU3."""

    requiere_humano: bool
    respuesta: str | None = Field(min_length=10, max_length=800)
    motivo: str | None = Field(min_length=10, max_length=300)
    citas: list[FragmentoId] = Field(max_length=6)

    _validar_citas = field_validator("citas")(_citas_recuperadas)
```

`src/rag/contratos.py`, líneas 121–130:

```python
class ResolucionFactura(Contrato):
    """Lo que resuelve el RAG sobre una factura ya extraída: artículo del ERP y quién autoriza."""

    requiere_humano: bool
    articulo_erp: CodigoArticulo | None
    autoriza: Autoriza | None
    motivo: str | None = Field(min_length=10, max_length=300)
    citas: list[FragmentoId] = Field(max_length=6)

    _validar_citas = field_validator("citas")(_citas_recuperadas)
```

### PE3 — reglas que dependen de lo recuperado

`src/rag/contratos.py`, líneas 66–73:

```python
def _citas_recuperadas(citas: list[str], info: ValidationInfo) -> list[str]:
    recuperados = (info.context or {}).get("recuperados")
    if recuperados is None:  # sin contexto (por ejemplo, al armar el esquema) no hay contra qué validar
        return citas
    ajenas = [c for c in citas if c not in recuperados]
    if ajenas:
        raise ValueError(f"cita fragmentos que no se recuperaron: {', '.join(ajenas)}")
    return citas
```

`src/rag/contratos.py`, líneas 91–113:

```python
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
```

`src/rag/contratos.py`, líneas 132–151:

```python
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
```

## Limitaciones

- El modelo de lenguaje de todas las corridas reales es Gemini: el cliente de Anthropic no se
  probó contra la API real (ver arriba).
- La entrada es texto: leer PDF o fotos queda para una entrega posterior.
- Las métricas de costo cuentan el modelo de lenguaje; el costo de los embeddings no se suma.
- Una factura con varias alícuotas, percepciones u otros tributos se marca como `dudoso`.
- La confianza por campo la propone el modelo: no es una probabilidad calibrada.
