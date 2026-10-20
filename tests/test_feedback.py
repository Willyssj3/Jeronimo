"""Sugerencias y errores: el formulario ya completado, sin datos personales."""

from urllib.parse import parse_qs, urlparse

import pytest

from traductor import feedback
from traductor.app import api as api_module
from traductor.app.api import Api

FORM = "https://docs.google.com/forms/d/e/EJEMPLO/viewform"


@pytest.fixture
def form(monkeypatch):
    monkeypatch.setattr(feedback, "FORM_URL", FORM)
    monkeypatch.setattr(feedback, "ENTRIES", {"tipo": "111", "texto": "222", "datos": "333"})


def params(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_redact_paths_and_user(monkeypatch):
    monkeypatch.setattr(feedback.getpass, "getuser", lambda: "lectora")
    text = ('File "C:\\Users\\lectora\\Jeronimo\\_internal\\traductor\\epub.py", line 3\n'
            "No encuentro «D:\\Libros\\Mi novela secreta.epub» de lectora")
    clean = feedback.redact(text)
    assert 'File "epub.py", line 3' in clean
    assert "Mi novela" not in clean and "<ruta>" in clean
    assert "lectora" not in clean and "<usuario>" in clean


def test_last_error(tmp_path):
    log = tmp_path / "registro.txt"
    assert feedback.last_error(log) == ""
    log.write_text("\n[2026-10-01T10:00:00]\nError viejo\n\n[2026-10-02T11:00:00]\nTraceback\nValueError: nuevo\n",
                   encoding="utf-8")
    last = feedback.last_error(log)
    assert last.startswith("[2026-10-02T11:00:00]") and "nuevo" in last and "viejo" not in last


def test_technical_details(tmp_path):
    (tmp_path / "registro.txt").write_text("\n[2026-10-02T11:00:00]\nValueError: algo\n", encoding="utf-8")
    details = feedback.technical_details(mode="escritorio", ui_language="es", log_path=tmp_path / "registro.txt")
    assert details.startswith(f"Jerónimo {feedback.__version__}") and "ValueError: algo" in details
    assert len(details) <= feedback.MAX_DETAILS


def test_form_url_only_when_configured():
    with pytest.raises(ValueError):
        feedback.form_url("idea", "Hola")


def test_form_url(form):
    p = params(feedback.form_url("error", "  Se cerró al exportar ", "Jerónimo 0.3.2"))
    assert p == {"usp": "pp_url", "entry.111": "Error", "entry.222": "Se cerró al exportar", "entry.333": "Jerónimo 0.3.2"}
    assert params(feedback.form_url("idea", "x" * 9000))["entry.222"] == "x" * feedback.MAX_TEXT
    assert "entry.333" not in params(feedback.form_url("idea", "Una idea"))
    with pytest.raises(ValueError):
        feedback.form_url("idea", "   ")
    with pytest.raises(ValueError):
        feedback.form_url("otra", "Hola")


def test_api_opens_the_filled_form(form, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    opened = []
    monkeypatch.setattr(api_module.webbrowser, "open", lambda link: opened.append(link) or True)
    api = Api(mode="navegador")
    assert api.app_info()["sugerencias"] is True
    assert api.feedback_details().startswith("Jerónimo")
    assert api.open_feedback("idea", "Me gustaría X", False)
    assert opened[0].startswith(FORM) and "entry.333" not in params(opened[0])
    api.open_feedback("error", "Falló Y", True)
    assert params(opened[1])["entry.333"].startswith("Jerónimo")
