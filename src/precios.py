"""Precios publicados, en USD por millón de tokens.

Gemini: https://ai.google.dev/gemini-api/docs/pricing (tarifa paga, página del 2026-10-30).
Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
El nivel gratuito de Gemini no genera cargo; este costo es lo que saldría con la tarifa paga.
"""

PRECIOS_USD_POR_MILLON = {
    "claude-opus-5-5": {"entrada": 4.00, "salida": 20.00},
    "claude-haiku-5-5": {"entrada": 0.10, "salida": 0.50},
    "gemini-3.5-flash-lite": {"entrada": 0.30, "salida": 2.50},
    "gemini-3.1-flash-lite": {"entrada": 0.25, "salida": 1.50},
    "gemini-2.5-flash-lite": {"entrada": 0.10, "salida": 0.40},
}


def calcular_costo_usd(modelo: str, entrada: int, salida: int, razonamiento: int) -> float:
    """El razonamiento se cobra a precio de salida. Sin precio publicado, falla."""
    precio = PRECIOS_USD_POR_MILLON[modelo]
    costo = entrada * precio["entrada"] + (salida + razonamiento) * precio["salida"]
    return round(costo / 1_000_000, 8)
