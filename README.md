# fde-carga-facturas

Sistema de carga asistida de facturas de proveedores del curso Forward Deployed Engineer.

- **PE2 — motor LLM:** recibe el texto de una factura y devuelve un borrador validado con
  Pydantic, más tokens, costo y latencia (HU1 de la ficha de la Pre-Entrega 1).
- **PE3 — RAG con permisos:** responde con los documentos del cliente (matriz de autorización,
  procedimiento, catálogo de artículos, equivalencias y presupuesto) con búsqueda híbrida,
  filtro por rol en la consulta a la base y fuentes citadas (HU3). Ver [RAG](#rag-pe3).

Un analista revisa siempre el borrador: el motor no registra nada en ningún sistema. Todos
los datos del repositorio son ficticios.

**Proveedor de esta entrega: Gemini (`gemini-3.5-flash-lite`).** Es barato (USD 0,30 / 2,50
por millón de tokens), tiene nivel gratuito y resolvió bien las 5 consultas de medición. El
motor no depende del proveedor: con `ANTHROPIC_API_KEY` en el `.env` pasa a usar Claude y
Gemini queda de respaldo.

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

## Limitaciones

- La entrada es texto: leer PDF o fotos queda para una entrega posterior.
- Las métricas de costo cuentan el modelo de lenguaje; el costo de los embeddings no se suma.
- Una factura con varias alícuotas, percepciones u otros tributos se marca como `dudoso`.
- La confianza por campo la propone el modelo: no es una probabilidad calibrada.
