"""Perfiles de autor: guardar lo aprendido de un libro y usarlo en el siguiente (libros inventados)."""

import pytest
from helpers import make_epub
from test_pipeline import FakeAI, project  # noqa: F401  (fixture)

from traductor import knowledge, profiles
from traductor.project import Project
from traductor.translate import Translator


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


def test_save_and_apply_profile(project, tmp_path):  # noqa: F811
    ai = FakeAI()
    knowledge.prepare(project, ai)  # guía de estilo y propuesta de la IA («Harrowgate»)
    project.set_glossary(project.glossary() + [{"termino": "Mirela", "traduccion": "Mirela", "origen": "usuario"}])
    Translator(project, ai).translate_chapter(0)
    project.set_approved(0)

    info = profiles.save_profile(project, "Autora de prueba")
    assert info["id"] == "autora-de-prueba" and info["libros"] == ["A Test Book"]
    assert info["referencias"] == 2 and info["estilo"]  # «Nets» + lo aprobado de este libro
    folder = profiles.profiles_dir() / info["id"]
    approved = (folder / "referencias" / "a-test-book-aprobado" / "pares.jsonl").read_text(encoding="utf-8")
    assert "ES MIRELA WALKED TO HARROWGATE" in approved
    terms = {e["termino"] for e in profiles.read_json(folder / "glosario.json")}
    assert "Mirela" in terms and "Harrowgate" not in terms  # las propuestas no viajan
    assert [p["nombre"] for p in profiles.list_profiles()] == ["Autora de prueba"]

    # Otro libro de la misma autora empieza con todo eso.
    book2 = make_epub(tmp_path / "book2.epub", [("Tide", "<p>Mirela waited for the tide.</p>")], title="Second Book")
    p2 = Project.create(tmp_path / "proyecto2", book2)
    r = profiles.apply_profile(p2, info["id"])
    assert r == {"referencias": 2, "estilo": True, "terminos": len(terms)}
    assert p2.style_guide() == project.style_guide()
    assert {e["termino"] for e in p2.glossary()} == terms
    assert p2.config["perfil_autor"] == "Autora de prueba"
    assert any(ex.origin == "A Test Book (traducción aprobada)" for ex in p2.reference_pairs())
    # Aplicarlo otra vez no duplica nada ni pisa la guía propia.
    p2.set_style_guide("## Mi guía")
    assert profiles.apply_profile(p2, info["id"]) == {"referencias": 0, "estilo": False, "terminos": 0}
    assert p2.style_guide().startswith("## Mi guía")

    # Actualizar el perfil desde el segundo libro suma el título.
    assert profiles.save_profile(p2, "Autora de prueba")["libros"] == ["A Test Book", "Second Book"]
    profiles.delete_profile(info["id"])
    assert profiles.list_profiles() == []
    with pytest.raises(ValueError):
        profiles.apply_profile(p2, info["id"])


def test_create_project_with_profile_through_api(project, tmp_path):  # noqa: F811
    from traductor.app.api import Api

    project.set_style_guide("## Guía de la autora")
    profiles.save_profile(project, "Autora")
    book2 = make_epub(tmp_path / "otro.epub", [("Tide", "<p>Mirela waited.</p>")], title="Otro")
    api = Api(mode="navegador")
    assert [p["id"] for p in api.app_info()["perfiles"]] == ["autora"]
    summary = api.create_project(str(book2), str(tmp_path / "p-otro"), "es", "auto", "autora")
    assert summary["perfil_autor"] == "Autora" and summary["estilo"] and len(summary["referencias"]) == 1
