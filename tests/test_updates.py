"""Aviso de versiones nuevas (sin conexión: GitHub simulado)."""

import pytest

from traductor import __version__, updates
from traductor.app.api import Api


def test_version_comparison():
    assert updates.parse_version("v0.10.2") == (0, 10, 2)
    assert updates.parse_version("1.2") == (1, 2, 0)
    assert updates.parse_version("v2.0.0-beta.1") == (2, 0, 0)
    assert updates.is_newer("v0.10.0", "0.9.9")
    assert not updates.is_newer("v0.2.0", "0.2.0")
    assert not updates.is_newer("", "0.2.0")


def test_latest_release_reads_github_answer():
    seen = []

    def fake_fetch(url, timeout):
        seen.append(url)
        return {"tag_name": "v9.1.0", "html_url": "https://github.com/Willyssj3/TraductordeLibros/releases/tag/v9.1.0",
                "body": "## Novedades\n- Algo nuevo.", "published_at": "2026-11-01T10:00:00Z"}

    r = updates.latest_release(fetch=fake_fetch)
    assert seen == [updates.API_URL]
    assert r == {"version": "9.1.0", "url": "https://github.com/Willyssj3/TraductordeLibros/releases/tag/v9.1.0",
                 "notas": "## Novedades\n- Algo nuevo.", "fecha": "2026-11-01"}
    # Un enlace que no es del proyecto se cambia por la página de descargas.
    r = updates.latest_release(fetch=lambda u, t: {"tag_name": "v9.1.0", "html_url": "https://example.com/x"})
    assert r["url"] == updates.RELEASES_URL


def test_check_update_once_a_day(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    calls = []

    def newer(*a, **k):
        calls.append(1)
        return {"version": "99.0.0", "url": updates.RELEASES_URL, "notas": "", "fecha": ""}

    monkeypatch.setattr(updates, "latest_release", newer)
    api = Api(mode="navegador")
    assert api.check_update()["version"] == "99.0.0"
    assert api.check_update()["version"] == "99.0.0"  # recordado: no vuelve a consultar hoy
    assert len(calls) == 1

    monkeypatch.setattr(updates, "latest_release", lambda *a, **k: {"version": __version__, "url": "", "notas": ""})
    assert api.check_update(force=True) is None  # ya es la última

    def offline(*a, **k):
        raise OSError("sin conexión")

    monkeypatch.setattr(updates, "latest_release", offline)
    assert api.check_update(force=True) is None


def test_open_url_only_project_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    api = Api(mode="navegador")
    assert api.open_url(updates.RELEASES_URL)
    assert opened == [updates.RELEASES_URL]
    for bad in ("https://example.com", "https://github.com/Willyssj3/TraductordeLibrosFalso", "file:///C:/x.exe"):
        with pytest.raises(ValueError):
            api.open_url(bad)
