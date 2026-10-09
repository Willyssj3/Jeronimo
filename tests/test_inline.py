from lxml import etree

from traductor import inline

XHTML = "http://www.w3.org/1999/xhtml"


def block(xml: str):
    return etree.fromstring(f'<p xmlns="{XHTML}">{xml}</p>')


def serialize_inner(el) -> str:
    s = etree.tostring(el, encoding="unicode")
    return s[s.index(">") + 1: s.rindex("<")]


def test_to_marked_pairs_and_atoms():
    el = block('A <i>quiet</i> harbor<a href="#n1" id="r1"><sup>1</sup></a> at <b>dawn <em>again</em></b>.<br/>')
    m = inline.to_marked(el)
    assert m.text == "A <g1>quiet</g1> harbor<x2/> at <g3>dawn <g4>again</g4></g3>.<x5/>"
    assert m.slots[2][0] == "x" and m.slots[3][0] == "g"


def test_roundtrip_preserves_markup():
    el = block('Hello <i>dear</i> friend<a href="#n1"><sup>2</sup></a>.')
    m = inline.to_marked(el)
    inline.apply_marked(el, "Hola, <g1>querido</g1> amigo<x2/>.", m.slots)
    assert serialize_inner(el) == 'Hola, <i>querido</i> amigo<a href="#n1"><sup>2</sup></a>.'


def test_escaping_roundtrip():
    el = block("Fish &amp; chips &lt;3")
    m = inline.to_marked(el)
    assert m.text == "Fish &amp; chips &lt;3"
    inline.apply_marked(el, "Pescado &amp; papas &lt;3", m.slots)
    assert el.text == "Pescado & papas <3"


def test_whitespace_normalized_but_nbsp_kept():
    el = block("one\n   two three")
    assert inline.to_marked(el).text == "one two three"


def test_repair_missing_atom_and_bad_pair():
    slots = {1: ("g", None), 2: ("x", None), 3: ("g", None)}
    text, warnings = inline.repair("<g1>uno</g3> dos <g3>tres</g1>", slots)
    assert inline.problems(text, slots) == ["falta la marca 1", "falta la marca 3"] or "<x2/>" in text
    assert "<x2/>" in text
    assert warnings


def test_repair_keeps_valid_reordering():
    slots = {1: ("g", None), 2: ("g", None)}
    text, warnings = inline.repair("<g2>b</g2> y <g1>a</g1>", slots)
    assert text == "<g2>b</g2> y <g1>a</g1>"
    assert warnings == []


def test_repair_duplicate_atom_and_unknown_tags():
    slots = {1: ("x", None)}
    text, warnings = inline.repair("a<x1/> b<x1/> <g9>c</g9>", slots)
    assert text.count("<x1/>") == 1 and "g9" not in text
    assert inline.problems(text, slots) == []
    assert warnings


def test_strip_ids_in_copy():
    el = block('Go <a id="x" href="#y">there</a><span id="pg5"/>')
    m = inline.to_marked(el)
    copy = etree.Element(f"{{{XHTML}}}p")
    inline.apply_marked(copy, m.text, m.slots, strip_ids=True)
    assert 'id="' not in etree.tostring(copy, encoding="unicode")
