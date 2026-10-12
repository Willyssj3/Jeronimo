"""Buscar y reemplazar sin romper el formato (frases inventadas)."""

import pytest
from helpers import make_epub

from traductor import search
from traductor.project import Project


def test_replace_keeps_marks_entities_and_case():
    regex = search.pattern("farero", whole_word=True)
    text = "El <g1>farero</g1> dijo: «Farero &amp; amigo»<x2/>. FARERO."
    new, count = search.replace_marked(text, regex, "guardián", keep_case=True)
    assert count == 3
    assert new == "El <g1>guardián</g1> dijo: «Guardián &amp; amigo»<x2/>. GUARDIÁN."
    # Palabra completa: no toca «fareros».
    assert search.replace_marked("Los fareros", regex, "x", keep_case=True) == ("Los fareros", 0)
    # Distinguir mayúsculas.
    regex = search.pattern("Mar", case=True)
    assert search.replace_marked("El mar y el Mar", regex, "Océano", keep_case=False)[0] == "El mar y el Océano"
    with pytest.raises(ValueError):
        search.pattern("   ")


def test_match_case():
    assert search.match_case("Puerto", "bahía") == "Bahía"
    assert search.match_case("PUERTO", "bahía") == "BAHÍA"
    assert search.match_case("puerto", "Bahía Azul") == "Bahía Azul"
    assert search.match_case("P", "bahía") == "Bahía"


@pytest.fixture
def project(tmp_path):
    book = make_epub(tmp_path / "b.epub", [
        ("One", "<p>Mirela walked to the harbor.</p><p>The harbor was quiet.</p>"),
        ("Two", "<p>Back at the <i>harbor</i>, Mirela slept.</p>"),
    ])
    p = Project.create(tmp_path / "p", book)
    p.set_translation(0, 0, "Mirela caminó hasta el puerto.")
    p.set_translation(0, 1, "El puerto estaba en calma.")
    p.set_translation(1, 0, "De vuelta en el <g1>puerto</g1>, Mirela durmió.")
    return p


def test_search_translation_and_original(project):
    r = search.search(project, "puerto")
    assert r["coincidencias"] == 3 and r["parrafos"] == 3
    first = r["resultados"][0]
    assert (first["doc"], first["n"], first["titulo"]) == (0, 0, "One")
    assert first["fragmento"] == {"antes": "Mirela caminó hasta el ", "texto": "puerto", "despues": "."}
    r = search.search(project, "harbor", field="src")
    assert r["parrafos"] == 3 and r["resultados"][2]["traduccion"] == "De vuelta en el puerto, Mirela durmió."


def test_replace_selected_and_undo(project):
    r = search.replace(project, "puerto", "muelle", only=[[0, 1], [1, 0]])
    assert (r["reemplazos"], r["parrafos"]) == (2, 2)
    assert r["deshacer"]["buscar"] == "puerto" and r["deshacer"]["parrafos"] == 2
    assert project.load_chapter(0)["segmentos"][0]["tgt"] == "Mirela caminó hasta el puerto."  # no marcado
    assert project.load_chapter(0)["segmentos"][1]["tgt"] == "El muelle estaba en calma."
    assert project.load_chapter(1)["segmentos"][0]["tgt"] == "De vuelta en el <g1>muelle</g1>, Mirela durmió."

    # Un párrafo editado después del reemplazo no se pisa al deshacer.
    project.set_translation(0, 1, "El muelle dormía.")
    u = search.undo_replacement(project)
    assert u == {"restaurados": 1, "omitidos": 1}
    assert project.load_chapter(1)["segmentos"][0]["tgt"] == "De vuelta en el <g1>puerto</g1>, Mirela durmió."
    assert search.last_replacement(project) is None
    with pytest.raises(ValueError):
        search.undo_replacement(project)


def test_api_refuses_replace_during_a_task(project, tmp_path, monkeypatch):
    from traductor.app.api import Api

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    api = Api(mode="navegador")
    api.open_project(str(project.root))
    assert api.find_text("PUERTO", {"mayusculas": True})["coincidencias"] == 0
    assert api.find_text("harbor", {"en": "original"})["parrafos"] == 3
    api._tasks.state["activa"] = True
    with pytest.raises(ValueError):
        api.replace_text("puerto", "muelle")
    api._tasks.state["activa"] = False
    assert api.replace_text("puerto", "muelle")["reemplazos"] == 3
    assert api.undo_replace()["restaurados"] == 3
