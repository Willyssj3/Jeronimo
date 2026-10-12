"""OCR de PDF escaneados (libro inventado convertido en imágenes)."""

import zipfile

import pytest
from pdfbook import make_book_pdf

from traductor import ocr
from traductor.pdf2epub import PdfError, convert

TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1400\t-1\t",
    "5\t1\t1\t1\t1\t1\t100\t200\t80\t30\t96\tEra",
    "5\t1\t1\t1\t1\t2\t190\t200\t60\t30\t95\tuna",
    "5\t1\t1\t1\t2\t1\t100\t240\t120\t30\t91\tmañana",
    "5\t1\t1\t1\t2\t2\t230\t240\t10\t30\t-1\t ",
])


def test_parse_tsv():
    lines = ocr.parse_tsv(TSV)
    assert [ln.text for ln in lines] == ["Era una", "mañana"]
    assert lines[0].box == (100, 200, 250, 230)


def _line(text, top, height, x0=100):
    return ocr.OcrLine([ocr.OcrWord(text, x0, top, x0 + 10 * len(text), top + height)])


def test_to_pages_normalizes_sizes():
    body = "Una línea de texto normal del libro, bastante larga."
    lines = [_line("Primera parte", 100, 60)] + [_line(body, 200 + 40 * k, 30 + (k % 3)) for k in range(8)] \
        + [_line("1 Nota al pie.", 900, 20)]
    pages = ocr.to_pages([(1, 432.0, 648.0, 300 / 72, lines)])
    sizes = [ln.size for ln in pages[0].lines]
    assert sizes[0] > 15 and set(sizes[1:9]) == {ocr.BODY_PT} and sizes[9] < ocr.BODY_PT
    first = pages[0].lines[0]
    assert round(first.top, 1) == round(100 / (300 / 72), 1) and pages[0].image_area == 1.0


def test_no_engine_explains_what_to_do(monkeypatch):
    monkeypatch.setattr(ocr.WindowsEngine, "available", staticmethod(lambda: False))
    monkeypatch.setattr(ocr.TesseractEngine, "find", staticmethod(lambda: None))
    with pytest.raises(PdfError, match="escaneado"):
        ocr.pick_engine("es")


def _scanned_copy(src, dst, dpi=200):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(src))
    images = [pdf[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(pdf))]
    pdf.close()
    images[0].save(dst, save_all=True, append_images=images[1:], resolution=dpi)
    return dst


@pytest.mark.skipif(ocr.TesseractEngine.find() is None, reason="Tesseract no está instalado")
def test_scanned_pdf_with_tesseract(tmp_path):
    expected = make_book_pdf(tmp_path / "libro.pdf")
    scanned = _scanned_copy(tmp_path / "libro.pdf", tmp_path / "escaneado.pdf")
    out, report = convert(scanned, tmp_path / "escaneado.epub", ocr_lang="es")
    assert report.ocr == "Tesseract" and any("escaneado" in w for w in report.warnings)
    assert report.chapters >= 2
    with zipfile.ZipFile(out) as z:
        text = " ".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml"))
    for chapter in expected["capitulos"]:
        assert chapter.split(":")[0] in text  # «Primera parte», «Segunda parte»
    assert "cartógrafo" in text and "tinta violeta" in text
