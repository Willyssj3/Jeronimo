"""Conversión de PDF a EPUB con libros de prueba generados (texto inventado)."""

import re
import zipfile

import pytest
from pdfbook import BookPDF, make_book_pdf

from traductor.epub import EpubBook
from traductor.pdf2epub import PdfError, convert, detect_language


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    folder = tmp_path_factory.mktemp("pdf")
    expected = make_book_pdf(folder / "libro.pdf")
    out, report = convert(folder / "libro.pdf", folder / "libro.epub")
    with zipfile.ZipFile(out) as z:
        files = {n: z.read(n) for n in z.namelist()}
    text = "\n".join(v.decode() for n, v in files.items() if n.startswith("OEBPS/texto"))
    return expected, out, report, files, text


def test_metadata_and_structure(converted):
    expected, out, report, files, _text = converted
    assert report.title == expected["titulo"] and report.author == expected["autor"]
    assert report.language == "es"
    assert report.chapters == len(expected["capitulos"])
    assert report.notes == expected["notas"]
    assert report.dropped_pages == [4]  # índice impreso
    assert list(files)[0] == "mimetype"
    assert "OEBPS/imagenes/portada.jpg" in files
    nav = files["OEBPS/nav.xhtml"].decode()
    for title in expected["capitulos"]:
        assert title in nav
    assert "El despacho" in nav  # subtítulo en el índice


def test_page_furniture_removed(converted):
    _expected, _out, report, _files, text = converted
    assert report.removed_lines >= 7
    assert "LA CIUDAD DE LOS MAPAS" not in text  # encabezado de página par
    assert not re.search(r"<p[^>]*>\s*\d+\s*</p>", text)  # números de página sueltos
    # El índice impreso no aparece como texto.
    assert "la niebla 5" not in text


def test_paragraphs_hyphenation_and_styles(converted):
    _expected, _out, _report, _files, text = converted
    assert "costumbre de encender" in text  # palabra cortada con guion, unida
    assert not re.search(r"\w-\s+\w", text.replace("000-0-00-000000-0", ""))
    assert "<i>extraordinariamente</i>" in text and "<b>barca</b>" in text and "<i>faro</i>" in text
    assert "<blockquote>" in text and '<p class="centrado">* * *</p>' in text
    assert '<p class="linea">ISBN 000-0-00-000000-0</p>' in text


def test_footnotes_are_linked(converted):
    _expected, _out, _report, _files, text = converted
    assert 'epub:type="noteref" href="#n1"' in text and 'epub:type="noteref" href="#n2"' in text
    assert '<aside epub:type="footnote" id="n1">' in text
    assert "Nota inventada que explica una costumbre local" in text


def test_output_opens_as_project_book(converted):
    _expected, out, _report, _files, _text = converted
    with EpubBook(out) as book:
        assert book.language == "es"
        chapter = next(d for d in book.documents if d.in_spine and "Primera parte" in book.doc_title(d))
        segs = book.segments(chapter)
        assert segs[0].source == "Primera parte: la niebla"
        assert any("<g" in s.source for s in segs)  # cursivas y notas como marcas


def test_scanned_pdf_is_rejected(tmp_path):
    book = BookPDF("Escaneado", "Nadie")
    book.cover()
    book.save(tmp_path / "scan.pdf")
    with pytest.raises(PdfError, match="escaneado"):
        convert(tmp_path / "scan.pdf", tmp_path / "scan.epub")


def test_missing_file(tmp_path):
    with pytest.raises(PdfError):
        convert(tmp_path / "no.pdf")


def test_detect_language():
    assert detect_language("The man and the sea were in the story of the old boat") == "en"
    assert detect_language("El hombre y el mar estaban en la historia de la barca") == "es"
