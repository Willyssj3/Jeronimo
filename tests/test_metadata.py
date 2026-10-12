"""Metadatos editables y portada propia (datos e imágenes inventados)."""

import base64
import io
import zipfile

import pytest
from helpers import make_epub
from PIL import Image

from traductor.epub import EpubBook
from traductor.project import Project

META = {"titulo": "El puerto", "autor": "Autora Inventada, Otra Autora", "traductor": "Ana Traductora",
        "editorial": "Editorial Inventada", "fecha": "2026", "temas": "Mar, Pesca", "serie": "Redes",
        "serie_numero": "2", "isbn": "978-0-00-000000-2", "descripcion": "Una pescadora y un farero.\nSegunda línea."}


def _project(tmp_path, version="3.0"):
    book = make_epub(tmp_path / "b.epub", [("One", "<p>Mirela walked.</p>")])
    if version != "3.0":
        src, dst = book, tmp_path / "b2.epub"
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.endswith(".opf"):
                    data = data.replace(b'version="3.0"', b'version="2.0"')
                zout.writestr(item, data)
        book = dst
    return Project.create(tmp_path / "p", book)


def _opf(epub):
    with zipfile.ZipFile(epub) as z:
        return z.read(next(n for n in z.namelist() if n.endswith(".opf"))).decode("utf-8")


def test_original_metadata_and_validation(tmp_path):
    p = _project(tmp_path)
    m = p.book_metadata()
    assert (m["titulo"], m["autor"], m["idioma"], m["traductor"]) == ("A Test Book", "Test Author", "es", "")
    for bad in ({"fecha": "12/10/2026"}, {"isbn": "123"}, {"serie_numero": "dos"}):
        with pytest.raises(ValueError):
            p.set_book_metadata(bad)
    m = p.set_book_metadata(META)
    assert m["isbn"] == "9780000000002" and m["descripcion"] == "Una pescadora y un farero.\nSegunda línea."
    assert p.config["titulo_traducido"] == "El puerto"
    # Borrar un campo vuelve al valor del original.
    assert p.set_book_metadata({"editorial": ""})["editorial"] == ""


def test_export_epub3_metadata(tmp_path):
    p = _project(tmp_path)
    p.set_book_metadata(META)
    out, _ = p.export()
    opf = _opf(out)
    assert "<dc:title>El puerto</dc:title>" in opf
    assert '<dc:creator id="autor1">Autora Inventada</dc:creator>' in opf and 'id="autor2">Otra Autora<' in opf
    assert '<meta refines="#traduccion1" property="role" scheme="marc:relators">trl</meta>' in opf
    assert "<dc:publisher>Editorial Inventada</dc:publisher>" in opf and "<dc:date>2026</dc:date>" in opf
    assert "<dc:subject>Mar</dc:subject>" in opf and '<meta name="calibre:series" content="Redes"/>' in opf
    assert 'property="belongs-to-collection">Redes</meta>' in opf
    assert '<dc:identifier id="isbn-traduccion">urn:isbn:9780000000002</dc:identifier>' in opf
    with EpubBook(out) as book:
        m = book.metadata()
    assert (m["autor"], m["serie"], m["serie_numero"], m["isbn"]) == ("Autora Inventada, Otra Autora", "Redes", "2", "9780000000002")

    # La bilingüe conserva el título original y lleva el resto.
    out, _ = p.export(bilingual=True)
    opf = _opf(out)
    assert "<dc:title>A Test Book</dc:title>" in opf and "Ana Traductora" in opf


def test_export_epub2_metadata(tmp_path):
    p = _project(tmp_path, version="2.0")
    p.set_book_metadata({"traductor": "Ana Traductora", "isbn": "0-00-000000-X"})
    out, _ = p.export()
    opf = _opf(out)
    assert 'opf:role="trl"' in opf and ">Ana Traductora</dc:contributor>" in opf
    assert 'opf:scheme="ISBN"' in opf and ">000000000X</dc:identifier>" in opf
    assert "refines" not in opf


def _png(size):
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 90, 140)).save(buf, "PNG")
    return buf.getvalue()


def test_own_cover(tmp_path):
    p = _project(tmp_path)
    with pytest.raises(ValueError):
        p.set_own_cover(_png((100, 150)))
    with pytest.raises(ValueError):
        p.set_own_cover(b"no es una imagen")
    p.set_own_cover(_png((600, 900)))
    assert p.cover_settings()["estilo"] == "propia" and p.cover_settings()["usar"]
    out, _ = p.export()
    with zipfile.ZipFile(out) as z:
        name = next(n for n in z.namelist() if n.endswith("jeronimo-portada.jpg"))
        with Image.open(io.BytesIO(z.read(name))) as im:
            assert (im.format, im.size) == ("JPEG", (600, 900))
    p.remove_own_cover()
    assert p.cover_settings()["estilo"] == "difuminada" and not p.cover_settings()["tiene_propia"]


def test_api_metadata_and_cover_image(tmp_path, monkeypatch):
    from traductor.app.api import Api

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    book = make_epub(tmp_path / "a.epub", [("One", "<p>Mirela walked.</p>")])
    api = Api(mode="navegador")
    api.create_project(str(book), str(tmp_path / "p"))
    r = api.set_book_metadata({"traductor": "Ana Traductora"})
    assert r["datos"]["traductor"] == "Ana Traductora" and r["original"]["autor"] == "Test Author"
    assert r["editados"] == ["traductor"]
    cfg = api.set_cover_image("data:image/png;base64," + base64.b64encode(_png((600, 900))).decode())
    assert cfg["tiene_propia"] and cfg["estilo"] == "propia"
    assert api.cover_preview()["nueva"].startswith("data:image/jpeg;base64,")
    with pytest.raises(ValueError):
        api.set_cover_image("data:text/plain;base64,aG9sYQ==")
    assert api.remove_cover_image()["tiene_propia"] is False
