"""Aviso de versiones nuevas: consulta la última versión publicada en GitHub.

Solo hace una petición de lectura a la API pública de GitHub; no envía ningún
dato del usuario ni de sus libros."""

from __future__ import annotations

import json
import re
import urllib.request

REPO = "Willyssj3/TraductordeLibros"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases/latest"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    """«v0.10.2» → (0, 10, 2). Se ignoran sufijos como «-beta»."""
    core = (text or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    nums = [int(n) for n in re.findall(r"\d+", core)[:3]]
    return tuple(nums + [0] * (3 - len(nums))) if nums else (0, 0, 0)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _fetch(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Jeronimo-traductor"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (URL fija)
        return json.loads(response.read().decode("utf-8"))


def latest_release(timeout: float = 6.0, fetch=_fetch) -> dict | None:
    """Última versión publicada: ``{"version", "url", "notas", "fecha"}`` o None.

    Las versiones preliminares y los borradores no cuentan (GitHub no los
    devuelve como «latest»)."""
    data = fetch(API_URL, timeout)
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return None
    url = str(data.get("html_url") or RELEASES_URL)
    if not url.startswith(REPO_URL):
        url = RELEASES_URL
    notes = str(data.get("body") or "").strip()
    if len(notes) > 2000:
        notes = notes[:2000].rsplit("\n", 1)[0] + "\n…"
    return {"version": tag.lstrip("vV"), "url": url, "notas": notes,
            "fecha": str(data.get("published_at") or "")[:10]}
