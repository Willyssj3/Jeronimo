"""Control de calidad sin IA, con frases inventadas para las pruebas."""

from traductor import qa


def seg(n, src, tgt, **extra):
    return dict({"n": n, "src": src, "tgt": tgt, "avisos": []}, **extra)


def kinds(issues):
    return [(i.n, i.tipo) for i in issues]


def check(segments, glossary=(), **kw):
    chapters = [{"doc": 0, "titulo": "Uno", "segmentos": segments}]
    return qa.Checker(chapters, list(glossary), **kw).run()


GLOSSARY = [
    {"termino": "Harrowgate", "traduccion": "Puertoarado", "tipo": "lugar"},
    {"termino": "net", "traduccion": "red", "tipo": "objeto"},
    {"termino": "Mirela", "traduccion": "Mirela", "tipo": "persona"},
]


def test_glossary_names_and_common_nouns():
    issues = check([
        seg(0, "Mirela walked to Harrowgate.", "Mirela caminó hasta Harrowgate."),
        seg(1, "She mended the old nets.", "Remendó las viejas redes."),          # plural: correcto
        seg(2, "The net was torn.", "La malla estaba rota."),                   # no usa «red»
        seg(3, "Mirela walked to Harrowgate.", "Mirela caminó hasta Puertoarado."),
        seg(4, "Mirela smiled.", "Mirella sonrió."),                            # nombre mal escrito
    ], GLOSSARY)
    assert kinds(issues) == [(0, "glosario"), (2, "glosario"), (4, "glosario")]
    assert "quedó sin traducir" in issues[0].mensaje
    assert "«red»" in issues[1].mensaje
    assert issues[2].mensaje == "«Mirela» no aparece en la traducción."


def test_identical_leftovers_and_opening_marks():
    issues = check([
        seg(0, "The boats are late again today.", "The boats are late again today."),
        seg(1, "He looked at the sea with fear.", "Miró the mar with miedo."),
        seg(2, "Are you coming? Yes!", "¿Vienes? Sí!"),
    ])
    # «The» con mayúscula no cuenta como resto (podría ser un título): basta con «igual».
    assert kinds(issues) == [(0, "igual"), (1, "restos"), (2, "signos")]
    assert "«the», «with»" in issues[1].mensaje
    assert "«¡»" in issues[2].mensaje


def test_numbers_length_dialogue_and_spacing():
    long_src = ("The keeper climbed the ninety steps of the lighthouse every evening, "
                "lit the great lamp and watched the dark water until the sun came back.")
    issues = check([
        seg(0, "She paid 1,500 coins in 1921.", "Pagó 1.500 monedas en 1912."),
        seg(1, "She paid 300 coins.", "Pagó trescientas monedas."),             # en palabras: bien
        seg(2, long_src, "El farero subía."),
        seg(3, "“Come inside,” he said.", "-Entra -dijo él."),
        seg(4, "It was cold .", "Hacía  frío ."),
    ])
    assert kinds(issues) == [(0, "numeros"), (2, "longitud"), (3, "dialogo"), (4, "espacios")]
    assert "1912" in issues[0].mensaje
    assert "espacios dobles" in issues[3].mensaje and "antes de un signo" in issues[3].mensaje


def test_dialogue_style_follows_the_book():
    dialogues = [seg(k, f"“Line {k},” she said.", f"—Línea {k} —dijo ella.") for k in range(4)]
    dialogues.append(seg(4, "“Wait,” he said.", "«Espera», dijo él."))
    issues = check(dialogues)
    assert kinds(issues) == [(4, "dialogo")]
    assert "resto del libro usa raya" in issues[0].mensaje


def test_names_written_in_two_ways_and_ignoring():
    segments = [seg(k, f"He spoke with Yeonsu number {k}.", f"Habló con Yeonsu, el número {k}.") for k in range(3)]
    segments.append(seg(3, "He spoke with Yeonsu again.", "Habló otra vez con Yeon-su."))
    chapters = [{"doc": 0, "titulo": "Uno", "segmentos": segments}]
    issues = qa.Checker(chapters, []).run()
    assert kinds(issues) == [(3, "nombres")]
    assert "«Yeonsu» (3) y «Yeon-su» (1)" in issues[0].mensaje
    key = issues[0].clave
    assert qa.Checker(chapters, [], ignored_names=[key]).run() == []

    # Ignorar un tipo de aviso en un párrafo concreto.
    segments[3]["qa_ignorar"] = ["nombres"]
    assert qa.Checker(chapters, [], ignored_names=[]).segment_issues(0, segments[3]) == []


def test_sentence_start_capitals_are_not_names():
    issues = check([
        seg(0, "Night fell. Then silence.", "Cayó la noche. Entonces, silencio."),
        seg(1, "Silence came then.", "Llegó entonces el silencio."),
    ])
    assert issues == []


def test_check_project_and_ignore(tmp_path):
    from helpers import make_epub

    from traductor.project import Project

    book = make_epub(tmp_path / "b.epub", [("One", "<p>Mirela walked to Harrowgate before dawn.</p>")])
    p = Project.create(tmp_path / "p", book)
    p.set_glossary([dict(e, origen="usuario") for e in GLOSSARY])
    p.set_translation(0, 0, "Mirela caminó hasta Harrowgate antes del alba.")
    report = qa.check_project(p)
    assert report["resumen"] == {"glosario": 1}
    row = report["problemas"][0]
    assert row["titulo"] == "One" and row["nivel"] == "error" and row["fragmento"].startswith("Mirela caminó")
    qa.ignore(p, row["doc"], row["n"], "glosario")
    assert qa.check_project(p)["problemas"] == []
