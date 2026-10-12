"""Idiomas de la interfaz: catálogos completos, tr() y cambio de idioma desde la API."""

import importlib.util
from pathlib import Path

import pytest

from traductor import i18n, prompts
from traductor.app.api import Api, Bridge

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("i18n_claves", ROOT / "tools" / "i18n_claves.py")
claves = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claves)


@pytest.fixture(autouse=True)
def back_to_spanish():
    yield
    i18n.set_language("es")


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_catalog_is_complete(lang):
    r = claves.report()[lang]
    assert r["faltan"] == [], f"faltan traducciones al {lang}: {r['faltan'][:10]}"
    assert r["marcadores_distintos"] == [], r["marcadores_distintos"][:10]
    assert r["vacias"] == []
    assert r["sobran"] == [], f"traducciones que ya no se usan ({lang}): {r['sobran'][:10]}"


def test_js_texts_can_be_extracted():
    assert claves.js_bad_calls() == []


def test_tr_translates_and_fills_values():
    assert i18n.tr("Guardar") == "Guardar"
    assert i18n.tr("Documento {index}", index=3) == "Documento 3"
    i18n.set_language("en")
    assert i18n.tr("Guardar") != "Guardar"
    doc = i18n.tr("Documento {index}", index=3)
    assert "3" in doc and "{" not in doc and doc != "Documento 3"
    assert i18n.tr("un texto que no está en el catálogo") == "un texto que no está en el catálogo"
    assert i18n.set_language("fr") == "es"


def test_system_language_is_supported():
    assert i18n.system_language() in i18n.LANGS


def test_ai_notes_follow_the_interface_language():
    args = dict(title="T", author="", src="en", tgt="es", variant="auto", style_guide="", synopsis="")
    assert "in Spanish, in a final block" in prompts.translate_system(**args)
    i18n.set_language("pt")
    text = prompts.translate_system(**args)
    assert "in Portuguese, in a final block" in text
    assert "in Portuguese</why>" in prompts.review_system(**args)


def test_api_changes_and_remembers_the_language(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    api = Api(mode="navegador")
    assert api.init_language() in i18n.LANGS
    assert api.set_ui_language("en") == "en"
    strings = api.ui_strings()
    assert strings["idioma"] == "en" and strings["textos"]["Guardar"] != "Guardar"
    info = api.app_info()
    assert info["idioma_ui"] == "en"
    assert info["planes"][1]["nombre"] != "Gratuito"
    # los errores llegan traducidos
    r = Bridge(api).call("open_project", [str(tmp_path / "no-existe")])
    assert r["ok"] is False and "No hay un proyecto" not in r["error"]
    with pytest.raises(ValueError):
        api.set_ui_language("fr")
    # al abrir la app otra vez se usa el idioma guardado
    i18n.set_language("es")
    assert Api(mode="navegador").init_language() == "en"
