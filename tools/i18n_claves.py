"""Textos de la interfaz que hay que traducir (inglés y portugués).

Reúne las claves (los textos en español) de:
- app.js: ``t("…")`` y ``N_("…")``;
- index.html: elementos con ``data-t`` (su texto) y ``data-t-title`` (su title);
- Python: ``tr("…")`` y ``N_("…")``.

Uso:
    python tools/i18n_claves.py            # resumen de lo que falta o sobra
    python tools/i18n_claves.py faltan en  # las claves sin traducir al inglés, en JSON
"""

from __future__ import annotations

import ast
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "traductor"
UI = PKG / "app" / "ui"
LOCALES = PKG / "locales"
LANGS = ("en", "pt")

JS_CALL = re.compile(r'(?<![\w.$])(?:t|N_)\(\s*"((?:[^"\\\n]|\\.)*)"')
JS_BAD = re.compile(r"(?<![\w.$])(?:t|N_)\(\s*['`]")
PLACEHOLDER = re.compile(r"\{(\w+)(?:[:!][^{}]*)?\}")


def js_keys(path: Path = UI / "app.js") -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {json.loads(f'"{m.group(1)}"') for m in JS_CALL.finditer(text)}


def js_bad_calls(path: Path = UI / "app.js") -> list[str]:
    """Llamadas a t() con comillas simples o plantillas: no se pueden extraer."""
    text = path.read_text(encoding="utf-8")
    return [text[m.start():m.start() + 60] for m in JS_BAD.finditer(text)]


class _HtmlKeys(HTMLParser):
    def __init__(self):
        super().__init__()
        self.keys: set[str] = set()
        self._stack: list[bool] = []
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "data-t-title" in a and a.get("title"):
            self.keys.add(a["title"].strip())
        if "data-t" in a:
            self._text = []
            self._stack.append(True)

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()
            text = " ".join("".join(self._text).split())
            if text:
                self.keys.add(text)

    def handle_data(self, data):
        if self._stack:
            self._text.append(data)


def html_keys(path: Path = UI / "index.html") -> set[str]:
    parser = _HtmlKeys()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.keys


def py_keys(root: Path = PKG) -> set[str]:
    keys: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("tr", "N_") \
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
    return keys


def all_keys() -> set[str]:
    # El título de la ventana se traduce desde app.js con el mismo texto.
    return js_keys() | html_keys() | py_keys()


def catalog(lang: str) -> dict[str, str]:
    path = LOCALES / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def report() -> dict:
    keys = all_keys()
    out = {"claves": len(keys)}
    for lang in LANGS:
        cat = catalog(lang)
        out[lang] = {
            "faltan": sorted(keys - set(cat)),
            "sobran": sorted(set(cat) - keys),
            "marcadores_distintos": sorted(k for k in keys & set(cat) if placeholders(k) != placeholders(cat[k])),
            "vacias": sorted(k for k, v in cat.items() if not v.strip()),
        }
    return out


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "faltan":
        missing = sorted(all_keys() - set(catalog(argv[1])))
        print(json.dumps({k: "" for k in missing}, ensure_ascii=False, indent=1))
        return 0
    r = report()
    print(f"{r['claves']} textos para traducir")
    for lang in LANGS:
        info = r[lang]
        print(f"{lang}: faltan {len(info['faltan'])}, sobran {len(info['sobran'])}, "
              f"marcadores distintos {len(info['marcadores_distintos'])}, vacías {len(info['vacias'])}")
    bad = js_bad_calls()
    if bad:
        print("t() con comillas simples o plantillas (cámbialas a comillas dobles):", *bad, sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
