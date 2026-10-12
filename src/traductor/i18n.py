"""Idioma de la interfaz: español (el original), inglés y portugués.

Los textos en español son la clave: ``tr("Guardar")`` devuelve la traducción
del idioma elegido o el mismo texto si falta. Las traducciones están en
``locales/<idioma>.json`` y las usan tanto Python como la interfaz (``t()``
en app.js). ``N_("…")`` solo marca un texto para traducirlo más tarde (en
constantes que se definen al importar el módulo).

El idioma es uno para toda la app (la eligen en la barra superior); la línea
de comandos sigue en español."""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

LANGS = {"es": "Español", "en": "English", "pt": "Português"}
# Idioma en el que la IA escribe sus notas y explicaciones (en inglés, como el resto del prompt).
NOTES_LANGUAGE = {"es": "Spanish", "en": "English", "pt": "Portuguese"}
LOCALES = Path(__file__).parent / "locales"
_current = "es"


def N_(text: str) -> str:
    """Marca ``text`` para traducir sin traducirlo todavía."""
    return text


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    path = LOCALES / f"{lang}.json"
    if lang == "es" or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def set_language(lang: str) -> str:
    global _current
    _current = lang if lang in LANGS else "es"
    return _current


def language() -> str:
    return _current


def notes_language() -> str:
    return NOTES_LANGUAGE[_current]


def system_language() -> str:
    """El idioma del sistema si es uno de los de la app; si no, inglés (y español si no se sabe)."""
    code = ""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            import locale

            code = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
        else:
            import locale

            code = locale.getlocale()[0] or os.environ.get("LANG", "")
    except Exception:  # sin información del sistema
        code = ""
    base = code.replace("-", "_").split("_")[0].lower()
    if base in LANGS:
        return base
    return "en" if base and base not in ("c", "posix") else "es"


def tr(text: str, **values) -> str:
    """Traduce ``text`` al idioma de la interfaz y rellena ``{nombre}`` con ``values``."""
    out = catalog(_current).get(text, text) if _current != "es" else text
    return out.format(**values) if values else out
