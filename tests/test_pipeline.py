"""Flujo completo con una IA simulada: proyecto, referencia, preparación,
traducción, aprobación y exportación."""

import json
import re
import zipfile

import pytest
from helpers import make_epub

from traductor import knowledge
from traductor.project import Project
from traductor.providers.base import Completion, Provider, RefusalError, Usage
from traductor.translate import Translator

SEG_RE = re.compile(r'<seg id="(\d+)">(.*?)</seg>', re.DOTALL)


class FakeAI(Provider):
    """Simula una IA: 'traduce' poniendo el texto en mayúsculas con prefijo."""

    kind = "falso"
    model = "falso-1"

    def __init__(self, drop_once=None, break_tags=False, refuse=None):
        self.calls = []
        self.drop_once = set(drop_once or [])
        self.break_tags = break_tags
        self.refuse = refuse

    def complete(self, system, user, *, max_tokens=None):
        self.calls.append((system, user))
        usage = Usage(input_tokens=len(user) // 4, output_tokens=100)
        if "write a practical style guide" in user:
            return Completion("## Guía\n- Diálogos con raya.\n- Tuteo entre amigos.", usage, self.model)
        if "candidate terms" in user:
            return Completion('{"term": "Mirela", "translation": "Mirela", "type": "persona", "note": "pescadora"}',
                              usage, self.model)
        if "Reply with JSON only" in user:
            ids = re.findall(r"\[\[CHAPTER (\d+):", user)
            return Completion(json.dumps({
                "summary": "Una pescadora y un farero.",
                "chapters": [{"id": i, "summary": f"Resumen del capítulo {i}."} for i in ids],
                "terms": [{"term": "Harrowgate", "type": "lugar", "note": "puerto"}],
            }), usage, self.model)
        if "decide how to render each term" in user:
            return Completion('{"term": "Harrowgate", "translation": "Puertoarado", "note": "nombre parlante"}',
                              usage, self.model)
        segments = SEG_RE.findall(user.split("# Segments to translate", 1)[1])
        if self.refuse and any(int(n) == self.refuse for n, _ in segments):
            raise RefusalError("no")
        out = []
        for n, text in segments:
            if int(n) in self.drop_once:
                self.drop_once.discard(int(n))
                continue
            t = re.sub(r"(<[^>]+>|&\w+;)|([^<&]+)", lambda m: m.group(1) or m.group(2).upper(), text)
            if self.break_tags:
                t = re.sub(r"</?g\d+>", "", t)
            out.append(f'<seg id="{n}">ES {t}</seg>')
        return Completion("\n".join(out) + '\n<notes><note id="1">Duda de prueba.</note></notes>',
                          usage, self.model)


BODY = (
    "<h1>The Harbor</h1>"
    "<p>Mirela walked to Harrowgate before dawn, as she did every <i>single</i> day.</p>"
    "<p>“The boats are late,” said the keeper<a href='#n1'><sup>1</sup></a>.</p>"
    "<p>She nodded and looked at the grey water for a long time.</p>"
)
REF_SRC = [
    ("One", "<p>Mirela mended the nets while Tobias counted the fish in the cold morning.</p>"
            "<p>“Come inside,” Tobias said to Mirela, “the wind is getting stronger.”</p>"),
]
REF_TGT = [
    ("Uno", "<p>Mirela remendaba las redes mientras Tobias contaba los peces en la fría mañana.</p>"
            "<p>—Entra —le dijo Tobias a Mirela—, el viento está arreciando.</p>"),
]


@pytest.fixture
def project(tmp_path):
    book = make_epub(tmp_path / "book.epub", [("The Harbor", BODY), ("Second", "<p>Mirela slept.</p>")])
    ref_en = make_epub(tmp_path / "ref_en.epub", REF_SRC, title="Nets")
    ref_es = make_epub(tmp_path / "ref_es.epub", REF_TGT, lang="es", title="Redes")
    p = Project.create(tmp_path / "proyecto", book)
    stats = p.add_reference(ref_en, ref_es, name="Nets")
    assert stats.pairs >= 2
    return p


def test_full_flow(project, tmp_path):
    ai = FakeAI()
    knowledge.prepare(project, ai)
    assert "raya" in project.style_guide()
    terms = {e["termino"]: e for e in project.glossary()}
    assert terms["Harrowgate"]["traduccion"] == "Puertoarado"
    assert project.bible()["capitulos"]["0"].startswith("Resumen")

    tr = Translator(project, ai)
    assert tr.next_chapter() == 0
    report = tr.translate_chapter(0)
    assert report.missing == []
    data = project.load_chapter(0)
    seg = data["segmentos"][1]
    assert seg["tgt"].startswith("ES MIRELA WALKED") and "<g1>SINGLE</g1>" in seg["tgt"]
    assert seg["nota"] == "Duda de prueba."

    # El prompt lleva el glosario relevante, ejemplos oficiales y el resumen del capítulo.
    system, user = ai.calls[-1]
    assert "Diálogos con raya" in system and "Una pescadora y un farero" in system
    assert "Harrowgate → Puertoarado" in user
    assert "Mirela remendaba las redes" in user or "Entra" in user
    assert "Resumen del capítulo 0." in user

    project.set_approved(0)
    assert any(e.origin == "este libro" for e in project.approved_pairs())

    tr.reload_knowledge()
    tr.translate_chapter(1)
    _, user = ai.calls[-1]
    assert "Previous text" in user  # contexto del capítulo anterior

    out, warnings = project.export()
    with zipfile.ZipFile(out) as z:
        ch1 = z.read("OEBPS/text/ch01.xhtml").decode()
    assert "ES MIRELA WALKED TO HARROWGATE" in ch1
    assert '<sup>1</sup>' in ch1
    assert warnings == []
    assert project.config["uso"]["falso-1"]["output_tokens"] > 0


def test_missing_segment_is_retried(project):
    ai = FakeAI(drop_once=[2])
    report = Translator(project, ai).translate_chapter(0)
    assert report.missing == []
    assert len(ai.calls) == 2


def test_broken_tags_are_repaired_and_flagged(project):
    ai = FakeAI(break_tags=True)
    Translator(project, ai).translate_chapter(0)
    seg = project.load_chapter(0)["segmentos"][1]
    assert "<g1>" not in seg["tgt"]
    assert seg["avisos"]


def test_refusal_isolates_one_paragraph(project):
    ai = FakeAI(refuse=2)
    report = Translator(project, ai).translate_chapter(0)
    assert report.missing == [2]
    seg = project.load_chapter(0)["segmentos"][2]
    assert "a mano" in seg["avisos"][0]


def test_manual_mode(project):
    tr = Translator(project, None)
    prompt, ids, blocks = tr.manual_prompt(0)
    assert "<seg id=\"0\">The Harbor</seg>" in prompt and ids == [0, 1, 2, 3] and blocks == 1
    assert "ONE single code block" in prompt  # salida fácil de copiar desde el chat
    # La respuesta pegada desde un chat suele venir dentro de un bloque de código.
    reply = '```xml\n<seg id="0">El puerto</seg>\n<seg id="3">Ella asintió.</seg>\n```'
    saved, missing = tr.manual_import(0, reply)
    assert saved == 2 and missing == []
    data = project.load_chapter(0)
    assert data["segmentos"][0]["tgt"] == "El puerto"
    _prompt, ids, _blocks = tr.manual_prompt(0)
    assert ids == [1, 2]


def test_manual_block_size(project):
    tr = Translator(project, None)
    *_rest, blocks_small = tr.manual_prompt(0, block_chars=60)
    *_rest, blocks_big = tr.manual_prompt(0, block_chars=20000)
    assert blocks_small > blocks_big == 1


def test_cannot_approve_incomplete_chapter(project):
    with pytest.raises(ValueError):
        project.set_approved(0)


def test_front_matter_pairs_are_not_examples():
    from traductor.project import is_front_matter

    assert is_front_matter("Translation copyright © 2014 by Someone", "28002 Madrid Tel.: 91 000 00 00")
    assert is_front_matter("First published in English in 2014", "La edición en lengua española se publica")
    assert is_front_matter("A long paragraph " * 20, "Corto.")
    assert not is_front_matter("Mirela mended the nets in the morning.", "Mirela remendaba las redes por la mañana.")


def test_titles_only_block_has_no_examples(project):
    tr = Translator(project, None)
    data = project.load_chapter(0)
    user = tr.build_user(data, data["segmentos"][:1])  # solo el título del capítulo
    assert "# Reference examples" not in user
    user = tr.build_user(data, data["segmentos"][1:3])
    assert "# Reference examples" in user


def test_manual_preparation_any_chat(project):
    from traductor.knowledge import ManualPrep

    prep = ManualPrep(project)
    st = prep.start("estilo")
    assert st == {"paso": "estilo", "nombre": "Guía de estilo", "hechos": 0, "total": 1,
                  "etiqueta": st["etiqueta"], "terminado": False}
    prompt = prep.prompt()
    assert "Mirela remendaba las redes" in prompt["texto"] and "ONE single code block" in prompt["texto"]
    with pytest.raises(ValueError):
        prep.import_response("sin nada útil")
    done = prep.import_response("```markdown\n## Guía\n- Diálogos con raya.\n- Tuteo entre amigos y familiares.\n```")
    assert done["terminado"] and prep.status() is None
    assert project.style_guide().startswith("## Guía") and "```" not in project.style_guide()

    # Una referencia con nombres repetidos en mitad de la frase (candidatos al glosario).
    folder = project.root.parent
    make_epub(folder / "ref2_en.epub", [("Two", "".join(
        f"<p>At dawn Mirela and Tobias walked to the harbor number {k}.</p>" for k in range(4)))], title="Dawn")
    make_epub(folder / "ref2_es.epub", [("Dos", "".join(
        f"<p>Al alba Mirela y Tobias caminaron hasta el puerto número {k}.</p>" for k in range(4)))], lang="es", title="Alba")
    project.add_reference(folder / "ref2_en.epub", folder / "ref2_es.epub", name="Dawn")
    st = prep.start("glosario")
    assert "Mirela" in prep.prompt()["texto"]
    prep.import_response('```json\n{"term": "Mirela", "translation": "Mirela", "type": "persona", "note": "pescadora"}\n```')
    assert any(e["termino"] == "Mirela" and e["origen"] == "oficial" for e in project.glossary())

    # Análisis en dos partes: al final se pide una sinopsis que une ambas.
    st = prep.start("analisis", analysis_budget=200)
    assert st["total"] >= 2
    for k in range(st["total"]):
        r = prep.import_response('```json\n{"summary": "Parte %d.", "chapters": [{"id": "0", "summary": "Resumen %d."}], '
                                 '"terms": [{"term": "Harrowgate", "type": "lugar", "note": "puerto"}]}\n```' % (k, k))
    assert not r["terminado"] and "sinopsis" in r["etiqueta"]
    assert "Parte 0." in prep.prompt()["texto"]
    r = prep.import_response("```\nUna pescadora, un farero y un puerto envuelto en niebla.\n```")
    assert r["terminado"]
    bible = project.bible()
    assert bible["sinopsis"].startswith("Una pescadora") and bible["capitulos"]["0"].startswith("Resumen 0.")

    prep.start("terminos")
    prep.import_response('{"term": "Harrowgate", "translation": "Puertoarado", "note": "nombre parlante"}')
    assert {e["termino"]: e for e in project.glossary()}["Harrowgate"]["origen"] == "propuesta"
    with pytest.raises(ValueError):
        prep.prompt()  # no queda nada en curso
