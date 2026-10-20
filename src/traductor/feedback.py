"""Sugerencias y reporte de errores con un formulario de Google que no pide cuenta.

La app arma el mensaje y abre el formulario ya completado en el navegador; la persona lo revisa y
aprieta «Enviar». Las respuestas le llegan al autor en una planilla privada. Los datos técnicos
son optativos, se muestran antes de enviar y no llevan carpetas, nombres de usuario ni texto de
libros. Mientras el formulario no esté configurado, la app no muestra la opción."""

from __future__ import annotations

import getpass
import os
import platform
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

from . import __version__
from .i18n import tr

# El formulario (vínculo «viewform») y el número de cada pregunta (entry.NNN), sacados del
# «vínculo previamente completado» que arma su dueño en Google Forms.
FORM_URL = ""
ENTRIES = {"tipo": "", "texto": "", "datos": ""}
# Opciones de la pregunta «Tipo», escritas exactamente como en el formulario.
KINDS = {"idea": "Idea o sugerencia", "error": "Error"}

MAX_TEXT = 2500   # el resto se puede seguir escribiendo en el formulario
MAX_DETAILS = 1200
FORM_PREFIX = "https://docs.google.com/forms/d/"

_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|*?]*")


def configured() -> bool:
    return FORM_URL.startswith(FORM_PREFIX) and all(ENTRIES.values())


def redact(text: str) -> str:
    """Saca rutas y nombres de usuario: de cada ruta queda solo el nombre del archivo de la app
    (los .py, que sirven para encontrar el error); el resto, «<ruta>»."""
    def keep(match: re.Match) -> str:
        name = re.split(r"[\\/]", match.group(0).rstrip("\\/."))[-1]
        return name if name.endswith(".py") else "<ruta>"

    text = _PATH_RE.sub(keep, text)
    try:
        user = getpass.getuser()
    except Exception:  # sin usuario conocido
        user = ""
    for name in {user, os.environ.get("USERNAME", ""), Path.home().name}:
        if len(name) >= 3:
            text = re.sub(re.escape(name), "<usuario>", text, flags=re.IGNORECASE)
    return text


def last_error(log_path: Path, max_lines: int = 25) -> str:
    """El último error imprevisto que quedó en el registro (sus últimas líneas)."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    entries = [e.strip() for e in re.split(r"\n(?=\[\d{4}-\d{2}-\d{2}T)", text) if e.strip()]
    if not entries:
        return ""
    lines = entries[-1].splitlines()
    return "\n".join(lines[:1] + lines[1:][-max_lines:])


def technical_details(*, mode: str, ui_language: str, log_path: Path) -> str:
    """Versión, sistema y el último error, sin datos personales."""
    if sys.platform.startswith("win"):
        system = f"Windows {platform.release()} ({platform.version()})"
    else:
        system = platform.platform(terse=True)
    lines = [f"Jerónimo {__version__}", system, f"modo {mode} · idioma {ui_language}"]
    error = last_error(log_path)
    if error:
        lines += ["", tr("Último error:"), error]
    details = redact("\n".join(lines))
    return details if len(details) <= MAX_DETAILS else details[:MAX_DETAILS - 1] + "…"


def form_url(kind: str, text: str, details: str = "") -> str:
    """El formulario ya completado."""
    if not configured():
        raise ValueError(tr("El formulario de sugerencias todavía no está disponible."))
    if kind not in KINDS:
        raise ValueError(tr("Elige si es una idea o un error."))
    text = (text or "").strip()
    if not text:
        raise ValueError(tr("Escribe tu idea o qué pasó."))
    params = {f"entry.{ENTRIES['tipo']}": KINDS[kind], f"entry.{ENTRIES['texto']}": text[:MAX_TEXT]}
    if details:
        params[f"entry.{ENTRIES['datos']}"] = details[:MAX_DETAILS]
    return f"{FORM_URL}?usp=pp_url&{urlencode(params)}"
