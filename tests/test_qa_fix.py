"""Corregir los avisos del control de calidad: al instante (con deshacer) y con la IA."""

import pytest
from test_pipeline import FakeAI, project  # noqa: F401  (fixture)

from traductor import qa, qa_fix, review
from traductor.qa_fix import add_opening_signs, edit_marked, fix_dialogue_dashes, fix_spacing

GLOSSARY = [{"termino": "Harrowgate", "traduccion": "Puertoarado", "tipo": "lugar", "nota": "", "origen": "oficial"}]


@pytest.mark.parametrize("text, expected", [
    ("—Bailas? —preguntó Tobías.", "—¿Bailas? —preguntó Tobías."),
    ("Qué?", "¿Qué?"),
    ("¿Vienes? Y tú, vienes?", "¿Vienes? ¿Y tú, vienes?"),
    ("Le preguntó: «Vienes?».", "Le preguntó: «¿Vienes?»."),
    ("Y entonces... qué?", "Y entonces... ¿qué?"),
    ("—No sé —dijo ella—. Vienes?", "—No sé —dijo ella—. ¿Vienes?"),
    ("—Hola —dijo—, vienes mañana?", "—Hola —dijo—, ¿vienes mañana?"),
    ("—¿Vienes —preguntó ella— mañana?", "—¿Vienes —preguntó ella— mañana?"),
    ("¡Hola! Qué tal?", "¡Hola! ¿Qué tal?"),
    ("Ay, qué susto!", "¡Ay, qué susto!"),
])
def test_opening_signs(text, expected):
    assert add_opening_signs(text) == expected


def test_spacing_and_french():
    assert fix_spacing("Hola  mundo , dijo .") == "Hola mundo, dijo."
    assert fix_spacing("¿ Qué ? « Hola »") == "¿Qué? «Hola»"
    assert fix_spacing("Fin ...") == "Fin ..."  # los puntos suspensivos separados no se tocan
    # En francés, el espacio antes de ; : ? ! y dentro de « » es correcto.
    assert fix_spacing("Quoi ? « Oui » ; bon , fini .", french=True) == "Quoi ? « Oui » ; bon, fini."


@pytest.mark.parametrize("text, expected", [
    ("- Hola - dijo ella -. ¿Vienes?", "—Hola —dijo ella—. ¿Vienes?"),
    ("—No sé -dijo-, pero vienes.", "—No sé —dijo—, pero vienes."),
    ("—Hola -dijo el franco-argentino-.", "—Hola —dijo el franco-argentino—."),
    ("- ¿Qué?", "—¿Qué?"),
    ("–5 grados", "–5 grados"),
    ("—Sí -dijo-. Y tú -añadió- vienes?", "—Sí —dijo—. Y tú —añadió— vienes?"),
])
def test_dialogue_dashes(text, expected):
    assert fix_dialogue_dashes(text, dialogue=True) == expected


def test_edit_marked_keeps_format_and_notes():
    assert edit_marked("<g1>Vienes</g1>?", add_opening_signs) == "¿<g1>Vienes</g1>?"
    assert edit_marked("—Bailas<x1/>? —preguntó <g1>Tobías</g1>.", add_opening_signs) == \
        "—¿Bailas<x1/>? —preguntó <g1>Tobías</g1>."
    assert edit_marked("palabra <g1>cursiva</g1> .", fix_spacing) == "palabra <g1>cursiva</g1>."
    assert edit_marked("Tom &amp; Jerry  dijeron .", fix_spacing) == "Tom &amp; Jerry dijeron."
    assert edit_marked("sin cambios", fix_spacing) == "sin cambios"


def translate(project, texts):
    for doc, segs in texts.items():
        data = project.load_chapter(doc)
        for n, text in segs.items():
            data["segmentos"][n]["tgt"] = text
        project.save_chapter(data)


@pytest.fixture
def translated(project):  # noqa: F811
    project.set_glossary(GLOSSARY)
    translate(project, {
        0: {0: "El puerto",
            1: "Mirela caminó hasta Harrowgate antes del alba, como cada <g1>santo</g1> día.",
            2: "- Los barcos llegan tarde - dijo el farero<x1/> .",
            3: "Asintió y miró a Tobias, y Tobías miró el agua. Tardarán mucho?"},
        1: {0: "Mirela durmió."},
    })
    return project


def rows(project, kind=None):
    return [r for r in qa_fix.check(project)["problemas"] if kind is None or r["tipo"] == kind]


def test_how_each_warning_is_fixed(translated):
    by_kind = {r["tipo"]: r for r in rows(translated)}
    assert by_kind["glosario"]["arreglo"] == "ia"
    assert by_kind["signos"]["arreglo"] == "directo"
    assert by_kind["espacios"]["arreglo"] == "directo"
    assert by_kind["dialogo"]["arreglo"] == "directo"
    assert by_kind["nombres"]["arreglo"] == "nombres"
    assert sorted(f for f, _c in by_kind["nombres"]["formas"]) == ["Tobias", "Tobías"]


def test_fix_all_of_a_kind_and_undo(translated):
    before = translated.load_chapter(0)["segmentos"][3]["tgt"]
    result = qa_fix.fix(translated, "signos")
    assert result["corregidos"] == 1 and result["deshacer"]["parrafos"] == 1
    seg = translated.load_chapter(0)["segmentos"][3]
    assert seg["tgt"].endswith("¿Tardarán mucho?") and seg["editado"]
    assert not rows(translated, "signos")

    undone = qa_fix.undo(translated)
    assert undone == {"restaurados": 1, "omitidos": 0, "deshacer": None}
    assert translated.load_chapter(0)["segmentos"][3]["tgt"] == before
    with pytest.raises(ValueError):
        qa_fix.undo(translated)


def test_fix_one_paragraph_keeps_the_note(translated):
    qa_fix.fix(translated, "dialogo", [[0, 2]])
    qa_fix.fix(translated, "espacios", [[0, 2]])
    assert translated.load_chapter(0)["segmentos"][2]["tgt"] == "—Los barcos llegan tarde —dijo el farero<x1/>."
    assert not rows(translated, "dialogo") and not rows(translated, "espacios")
    # Se deshace la última corrección, y después la anterior.
    qa_fix.undo(translated)
    assert translated.load_chapter(0)["segmentos"][2]["tgt"] == "—Los barcos llegan tarde —dijo el farero<x1/> ."
    qa_fix.undo(translated)
    assert translated.load_chapter(0)["segmentos"][2]["tgt"] == "- Los barcos llegan tarde - dijo el farero<x1/> ."


def test_undo_skips_paragraphs_edited_afterwards(translated):
    qa_fix.fix(translated, "signos")
    translate(translated, {0: {3: "Otra versión, escrita a mano."}})
    assert qa_fix.undo(translated)["omitidos"] == 1
    assert translated.load_chapter(0)["segmentos"][3]["tgt"] == "Otra versión, escrita a mano."


def test_direct_fix_refuses_ai_kinds(translated):
    with pytest.raises(ValueError):
        qa_fix.fix(translated, "glosario")


def test_unify_name_in_the_whole_book(translated):
    result = qa_fix.unify_name(translated, ["Tobias", "Tobías"], "Tobías")
    assert result["corregidos"] == 1
    assert "Tobias" not in translated.load_chapter(0)["segmentos"][3]["tgt"]
    assert not rows(translated, "nombres")
    qa_fix.undo(translated, result["lote"])
    assert "a Tobias," in translated.load_chapter(0)["segmentos"][3]["tgt"]
    with pytest.raises(ValueError):
        qa_fix.unify_name(translated, ["Tobías"], "Tobías")


def test_dialogue_style_needs_the_ai(project):  # noqa: F811
    # El libro usa raya; un diálogo entre comillas no se puede arreglar sin reescribir.
    chapters = [{"doc": 0, "segmentos": [
        {"n": i, "src": "“Come,” she said.", "tgt": "—Ven —dijo ella.", "avisos": []} for i in range(3)
    ] + [{"n": 3, "src": "“Come,” she said.", "tgt": "«Ven», dijo ella.", "avisos": []}]}]
    checker = qa.Checker(chapters, [], target_lang="es")
    issue = [i for i in checker.run() if i.tipo == "dialogo"][0]
    assert qa_fix.direct_fix(checker, 0, chapters[0]["segmentos"][3], issue.tipo) is None


def test_ai_fix_with_connected_ai(translated):
    ai = FakeAI()
    fixer = qa_fix.Fixer(translated, ai)
    entries = fixer.pending(items=[[0, 1]])
    assert [(e["doc"], e["n"], e["tipos"]) for e in entries] == [(0, 1, ["glosario"])]
    assert "Puertoarado" in entries[0]["problemas"][0]
    assert fixer.run(entries) == {"sugerencias": 1, "bien": 0, "sin_respuesta": 0, "bloques": 1}
    system, user = ai.calls[-1]
    assert "# Segments to fix" in user and "Harrowgate → Puertoarado" in user and "quality check" in system

    row = rows(translated, "glosario")[0]
    assert "Puertoarado" in row["sugerencia"]["texto"] and "<g1>santo</g1>" in row["sugerencia"]["texto"]
    assert row["sugerencia"]["origen"] == "calidad"
    # Con una propuesta pendiente, ese párrafo no se vuelve a pedir.
    assert fixer.pending(items=[[0, 1]]) == []
    review.resolve(translated, 0, 1, accept=True)
    assert not rows(translated, "glosario")


def test_ai_says_the_paragraph_is_fine(translated):
    translate(translated, {0: {3: "Asintió y miró el agua gris durante 2 horas."}})
    fixer = qa_fix.Fixer(translated, FakeAI())
    entries = fixer.pending(kinds=["numeros"])
    assert [(e["doc"], e["n"]) for e in entries] == [(0, 3)]
    assert fixer.run(entries)["bien"] == 1
    row = rows(translated, "numeros")[0]
    assert row["opinion"] == "Está bien así." and "sugerencia" not in row
    assert fixer.pending(kinds=["numeros"]) == []  # ya revisado por la IA
    # Si el párrafo cambia, la opinión deja de valer.
    translate(translated, {0: {3: "Asintió y miró el agua gris durante 3 horas."}})
    assert "opinion" not in rows(translated, "numeros")[0]


def test_ai_fix_in_manual_mode(translated):
    fixer = qa_fix.Fixer(translated)
    with pytest.raises(ValueError):
        fixer.manual_import("<fix id=\"1\"><text>x</text></fix>")  # todavía no se copió nada
    prompt = fixer.manual_prompt(kinds=["glosario"])
    assert prompt["parrafos"] == 1 and prompt["restantes"] == 0
    assert "# Segments to fix" in prompt["texto"] and "ONE single code block" in prompt["texto"]
    with pytest.raises(ValueError):
        fixer.manual_import("Lo siento, no entendí.")
    answer = ('```\n<fix id="1"><text>Mirela caminó hasta Puertoarado antes del alba, como cada <g1>santo</g1> día.</text>'
              '<why>Glosario.</why></fix>\n```')
    assert fixer.manual_import(answer) == {"sugerencias": 1, "bien": 0, "sin_respuesta": 0}
    assert "Puertoarado" in translated.load_chapter(0)["segmentos"][1]["sugerencia"]["texto"]
    assert fixer.manual_prompt(kinds=["glosario"]) is None


def test_manual_answer_from_other_instructions_is_ignored(translated):
    fixer = qa_fix.Fixer(translated)
    prompt = fixer.manual_prompt(kinds=["glosario"])
    assert '<seg id="1">' in prompt["texto"]  # capítulo 0, párrafo 1
    # Una respuesta vieja, para otro párrafo: no se aplica a lo que se pidió ahora.
    answer = '<fix id="3"><text>Otra cosa.</text><why>x</why></fix>'
    assert fixer.manual_import(answer) == {"sugerencias": 0, "bien": 0, "sin_respuesta": 1}
    assert all("sugerencia" not in s for s in translated.load_chapter(0)["segmentos"])


def test_manual_answer_for_a_paragraph_that_changed(translated):
    fixer = qa_fix.Fixer(translated)
    fixer.manual_prompt(kinds=["glosario"])
    translate(translated, {0: {1: "Mirela caminó hasta Harrowgate al alba."}})
    answer = '<fix id="1"><text>Mirela caminó hasta Puertoarado antes del alba.</text><why>Glosario.</why></fix>'
    assert fixer.manual_import(answer)["sin_respuesta"] == 1
    assert "sugerencia" not in translated.load_chapter(0)["segmentos"][1]
