"""Paquete para Proyectos y bloques livianos del modo manual (libro inventado)."""

from test_pipeline import FakeAI, project  # noqa: F401  (fixture)

from traductor import knowledge, prompts
from traductor.translate import Translator


def test_package_contents_and_light_blocks(project):  # noqa: F811
    knowledge.prepare(project, FakeAI())
    tr = Translator(project, None)
    instructions = tr.project_instructions()
    assert instructions.startswith(tr.system.rstrip()) and "# How this project works" in instructions
    assert "Diálogos con raya" in instructions  # la guía de estilo va en las instrucciones

    text = tr.project_knowledge()
    assert "- Harrowgate → Puertoarado" in text
    assert "## Chapter summaries" in text and "Resumen del capítulo 0." in text
    assert "<translation>Mirela remendaba las redes" in text or "<translation>—Entra" in text

    full, ids, _ = tr.manual_prompt(0)
    project.config.setdefault("opciones", {})["bloques_livianos"] = True
    light, light_ids, _ = Translator(project, None).manual_prompt(0)
    assert light_ids == ids
    assert light.startswith("Translate the segments below into Spanish")
    assert "# Style guide" not in light and "Diálogos con raya" not in light
    assert '<seg id="1">' in light and "Harrowgate → Puertoarado" in light and prompts.MANUAL_OUTPUT in light
    assert len(light) < len(full)


def test_fingerprint_detects_changes(project):  # noqa: F811
    before = Translator(project, None).knowledge_fingerprint()
    project.set_glossary([{"termino": "Mirela", "traduccion": "Mirela", "origen": "usuario"}])
    assert Translator(project, None).knowledge_fingerprint() != before


def test_api_package(project, tmp_path, monkeypatch):  # noqa: F811
    from traductor.app.api import Api

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    api = Api(mode="navegador")
    api.open_project(str(project.root))
    assert api.project_summary()["paquete"] is None
    r = api.project_package()
    assert r["caracteres_instrucciones"] > 500 and r["caracteres_conocimiento"] > 0
    assert (project.root / "salida").glob("*-conocimiento.md") and r["ruta"].endswith("-conocimiento.md")
    assert api.project_summary()["paquete"]["desactualizado"] is False
    assert api.package_instructions() == r["instrucciones"]
    api.save_style("## Guía nueva\n- Comillas angulares.")
    assert api.project_summary()["paquete"]["desactualizado"] is True
    assert api.set_settings({"bloques_livianos": True})["bloques_livianos"] is True
