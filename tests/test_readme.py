"""El README muestra los contratos en código literal: si el código cambia, el README tiene que cambiar."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BLOQUE = re.compile(r"`(src/[\w/]+\.py)`, líneas (\d+)–(\d+):\n\n```python\n(.*?)\n```", re.S)


def test_los_bloques_de_codigo_del_readme_son_literales():
    bloques = BLOQUE.findall((RAIZ / "README.md").read_text(encoding="utf-8"))
    assert bloques, "el README no tiene bloques de contratos"
    for ruta, desde, hasta, codigo in bloques:
        lineas = (RAIZ / ruta).read_text(encoding="utf-8").splitlines()
        esperado = "\n".join(lineas[int(desde) - 1:int(hasta)]).rstrip()
        assert codigo == esperado, f"{ruta} líneas {desde}–{hasta} ya no coincide con el README"
