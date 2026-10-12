"""Segunda pasada con IA (revisor simulado, frases inventadas)."""

import pytest
from test_pipeline import FakeAI, project  # noqa: F401  (fixture)

from traductor import prompts, review
from traductor.review import Reviewer
from traductor.translate import Translator


def test_parse_fixes():
    text = ('```xml\n<fix id="3"><text>Hola,\n  mundo.</text><why>Coma.</why></fix>\n'
            '<fix id="5"><text>Sin motivo.</text></fix>\n<fix id="7"><why>sin texto</why></fix>\n```')
    assert prompts.parse_fixes(prompts.strip_code_fences(text)) == {3: ("Hola, mundo.", "Coma."), 5: ("Sin motivo.", "")}
    assert prompts.looks_like_review("<nofixes/>") and not prompts.looks_like_review("nada")


def test_review_with_connected_ai(project):  # noqa: F811
    ai = FakeAI()
    Translator(project, ai).translate_chapter(0)
    result = Reviewer(project, ai).review_chapter(0)
    assert result == {"bloques": 1, "sugerencias": 1}
    data = project.load_chapter(0)
    fix = data["segmentos"][1]["sugerencia"]
    assert "PUERTOARADO" in fix["texto"] and "<g1>SINGLE</g1>" in fix["texto"] and fix["motivo"].startswith("Glosario")
    assert all(s["revisado_ia"] for s in data["segmentos"])
    assert review.status(project, 0) == {"por_revisar": 0, "revisados": 4, "sugerencias": 1}
    # Ya revisado: no hay nada más que enviar.
    assert Reviewer(project, ai).review_chapter(0) == {"bloques": 0, "sugerencias": 0}

    assert review.resolve(project, 0, 1, accept=True) == 1
    seg = project.load_chapter(0)["segmentos"][1]
    assert "PUERTOARADO" in seg["tgt"] and seg["editado"] and "sugerencia" not in seg

    review.reset(project, 0)
    assert review.status(project, 0)["por_revisar"] == 4


def test_new_translation_drops_old_suggestion(project):  # noqa: F811
    ai = FakeAI()
    tr = Translator(project, ai)
    tr.translate_chapter(0)
    Reviewer(project, ai).review_chapter(0)
    data = project.load_chapter(0)
    tr.apply_response([data["segmentos"][1]], '<seg id="1">Otra versión.</seg>')
    assert "sugerencia" not in data["segmentos"][1] and "revisado_ia" not in data["segmentos"][1]


def test_manual_review_blocks(project):  # noqa: F811
    Translator(project, FakeAI()).translate_chapter(0)
    rev = Reviewer(project)
    p = rev.manual_prompt(0)
    assert p["ids"] == [0, 1, 2, 3] and p["bloques_restantes"] == 1
    assert "# Segments to review" in p["texto"] and "ONE single code block" in p["texto"]
    assert "<translation>ES MIRELA WALKED" in p["texto"]
    with pytest.raises(ValueError):
        rev.manual_import(0, "Todo bien, no hay cambios.")
    # Respuesta cortada: solo cuenta como revisado hasta la última corrección completa.
    r = rev.manual_import(0, '```\n<fix id="1"><text>Corregido.</text><why>Estilo.</why></fix>\n<fix id="2"><text>Corta')
    assert r == {"sugerencias": 1, "revisados": 2}
    assert rev.manual_prompt(0)["ids"] == [2, 3]
    r = rev.manual_import(0, "```\n<nofixes/>\n```")
    assert r == {"sugerencias": 0, "revisados": 2}
    assert rev.manual_prompt(0) is None
    assert review.resolve(project, 0, None, accept=False) == 1
    assert review.status(project, 0)["sugerencias"] == 0
