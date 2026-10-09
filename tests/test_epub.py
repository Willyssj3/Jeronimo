import re
import zipfile

import pytest
from helpers import make_epub
from lxml import etree

from traductor.epub import EpubBook, EpubError

BODY_1 = (
    '<h1>Chapter One</h1>\n'
    '<p>The <i>old</i> keeper waved<a href="#n1" id="r1"><sup>1</sup></a> at the boats.</p>\n'
    '<p>* * *</p>\n'
    '<div>Loose words <b>here</b><p>Inner line.</p>after words</div>\n'
    '<ul><li>First point<ul><li>Nested point</li></ul></li></ul>\n'
    '<p>Picture <img src="../images/pic.png" alt="pic"/> below.</p>\n'
    '<table><tr><td>Cell text</td><td>42</td></tr></table>'
)
BODY_2 = '<h2>Chapter Two</h2><p>Salt &amp; wind &lt;forever&gt;.</p>'


@pytest.fixture
def book_path(tmp_path):
    return make_epub(tmp_path / "book.epub", [("Chapter One", BODY_1), ("Chapter Two", BODY_2)])


def fake_translate(marked: str) -> str:
    return re.sub(r"(<[^>]+>|&\w+;)|([^<&]+)", lambda m: m.group(1) or m.group(2).upper(), marked)


def test_metadata_and_documents(book_path):
    with EpubBook(book_path) as b:
        assert (b.title, b.author, b.language) == ("A Test Book", "Test Author", "en")
        kinds = [(d.kind, d.in_spine) for d in b.documents]
        assert kinds == [("xhtml", True), ("xhtml", True), ("xhtml", False), ("ncx", False)]


def test_segments(book_path):
    with EpubBook(book_path) as b:
        segs = b.segments(b.documents[0])
        texts = [s.source for s in segs]
        assert texts == [
            "Chapter One",
            "The <g1>old</g1> keeper waved<x2/> at the boats.",
            "Loose words <g1>here</g1>",
            "Inner line.",
            "after words",
            "First point",
            "Nested point",
            "Picture <x1/> below.",
            "Cell text",
        ]
        assert b.doc_title(b.documents[0], segs) == "Chapter One"
        assert [s.source for s in b.segments(b.documents[3])] == ["Chapter One", "Chapter Two"]


def _translations(book):
    return {d.index: [fake_translate(s.source) for s in book.segments(d)] for d in book.documents}


def test_write_translated(book_path, tmp_path):
    out = tmp_path / "out.es.epub"
    with EpubBook(book_path) as b:
        warnings = b.write(out, _translations(b), target_lang="es")
    assert warnings == []
    with zipfile.ZipFile(out) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        ch1 = z.read("OEBPS/text/ch01.xhtml").decode()
        assert '<p>THE <i>OLD</i> KEEPER WAVED<a href="#n1" id="r1"><sup>1</sup></a> AT THE BOATS.</p>' in ch1
        assert "<p>* * *</p>" in ch1
        assert 'lang="es"' in ch1
        assert "<!DOCTYPE html>" in ch1
        assert "<td>42</td>" in ch1
        assert "<dc:language>es</dc:language>" in z.read("OEBPS/content.opf").decode()
        assert z.read("OEBPS/images/pic.png").startswith(b"\x89PNG")
    with EpubBook(out) as b2:
        assert b2.segments(b2.documents[1])[1].source == "SALT &amp; WIND &lt;FOREVER&gt;."


def test_write_bilingual_has_unique_ids(book_path, tmp_path):
    out = tmp_path / "out.bi.epub"
    with EpubBook(book_path) as b:
        b.write(out, _translations(b), target_lang="es", bilingual=True)
    with zipfile.ZipFile(out) as z:
        root = etree.fromstring(z.read("OEBPS/text/ch01.xhtml"))
        ids = [el.get("id") for el in root.iter() if el.get("id")]
        assert len(ids) == len(set(ids))
        text = etree.tostring(root, encoding="unicode")
        assert "The <i>old</i> keeper" in text and "THE <i>OLD</i> KEEPER" in text
        assert 'class="tdl-trad"' in text
        opf = z.read("OEBPS/content.opf").decode()
        assert "<dc:language>en</dc:language>" in opf and "<dc:language>es</dc:language>" in opf


def test_untranslated_segments_are_left_alone(book_path, tmp_path):
    out = tmp_path / "partial.epub"
    with EpubBook(book_path) as b:
        b.write(out, {0: [None, "El viejo farero saludó<x2/>."]}, target_lang="es")
    with zipfile.ZipFile(out) as z:
        ch1 = z.read("OEBPS/text/ch01.xhtml").decode()
        assert "<h1>Chapter One</h1>" in ch1
        assert 'El viejo farero saludó<a href="#n1" id="r1"><sup>1</sup></a>.' in ch1


def test_drm_detected(book_path, tmp_path):
    drm = tmp_path / "drm.epub"
    with zipfile.ZipFile(book_path) as zin, zipfile.ZipFile(drm, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info.filename))
        zout.writestr("META-INF/encryption.xml", (
            '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
            'xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:CipherData>'
            '<enc:CipherReference URI="OEBPS/text/ch01.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>'))
    with pytest.raises(EpubError, match="DRM"):
        EpubBook(drm)


def test_not_an_epub(tmp_path):
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not a zip")
    with pytest.raises(EpubError):
        EpubBook(bad)
