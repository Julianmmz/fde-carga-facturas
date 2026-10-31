# fde-carga-facturas

Motor LLM de la Pre-Entrega 2 del curso Forward Deployed Engineer. Recibe el texto de una
factura de proveedor y devuelve un borrador validado con Pydantic, más tokens, costo y
latencia. Implementa la HU1 (carga asistida) de la ficha de la Pre-Entrega 1.

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
| `resultados/` | Salidas reales de terminal y `medicion.json` de la entrega (medidas con `gemini-3.5-flash-lite`) |

## Qué pasa cuando algo falla

- **Entrada inválida:** no se llama al modelo.
- **Respuesta inválida:** se registra qué campo falló, se reintenta una vez avisándole al
  modelo los errores y, si sigue mal, devuelve un error estructurado para cargar a mano.
- **Falla del proveedor** (clave, límite de uso, conexión): se repite la consulta en el
  respaldo. Si también falla, devuelve un error estructurado.

## Limitaciones

- La entrada es texto: leer PDF o fotos queda para una entrega posterior.
- El artículo del ERP se resuelve en la PE3; acá se devuelve lo que dice la factura.
- Una factura con varias alícuotas, percepciones u otros tributos se marca como `dudoso`.
- La confianza por campo la propone el modelo: no es una probabilidad calibrada.
