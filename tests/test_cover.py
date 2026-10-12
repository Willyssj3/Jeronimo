"""Portada traducida (títulos inventados)."""

import io
import zipfile

from helpers import make_epub
from PIL import Image

from traductor.cover import make_cover, unsupported_chars
from traductor.project import Project


def test_make_cover_sizes_and_formats():
    jpg = make_cover("El puerto de las redes", "Autora Inventada", style="lisa")
    with Image.open(io.BytesIO(jpg)) as im:
        assert (im.format, im.size) == ("JPEG", (1600, 2400))
    png = make_cover("Un título bastante largo para que ocupe varias líneas en la portada", "", fmt="PNG", aspect=1.6)
    with Image.open(io.BytesIO(png)) as im:
        assert (im.format, im.size) == ("PNG", (1600, 2560))
    small = make_cover("Breve", "A", size=(360, 540))
    with Image.open(io.BytesIO(small)) as im:
        assert im.size == (360, 540)
    # Con la portada original de fondo (una imagen inventada).
    bg = io.BytesIO()
    Image.new("RGB", (300, 450), (200, 90, 40)).save(bg, "JPEG")
    assert make_cover("Título", "Autora", style="difuminada", original=bg.getvalue())[:2] == b"\xff\xd8"


def test_unsupported_characters():
    assert unsupported_chars("Señor Ñandú, ¿qué? «Война» Ελλάδα") == set()
    assert unsupported_chars("東京の夜") == {"東", "京", "の", "夜"}


def _with_cover_property(src, dst):
    """Copia el EPUB marcando su imagen como portada (EPUB 3)."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith(".opf"):
                data = data.replace(b'media-type="image/png"/>', b'media-type="image/png" properties="cover-image"/>')
            zout.writestr(item, data)
    return dst


def _project(tmp_path, book):
    p = Project.create(tmp_path / "p", book)
    p.config["portada"] = {"usar": True, "estilo": "difuminada", "titulo": "El puerto", "autor": "Autora"}
    p.save_config()
    return p


def test_export_replaces_existing_cover(tmp_path):
    plain = make_epub(tmp_path / "a.epub", [("One", "<p>Mirela walked.</p>")])
    book = _with_cover_property(plain, tmp_path / "b.epub")
    p = _project(tmp_path, book)
    out, _ = p.export()
    with zipfile.ZipFile(out) as z:
        names = [n for n in z.namelist() if n.endswith("pic.png")]
        assert len(names) == 1
        with Image.open(io.BytesIO(z.read(names[0]))) as im:
            assert im.format == "PNG" and im.size == (1600, 1920)  # 1×1 → proporción mínima 1,2
        assert not any("jeronimo-portada" in n for n in z.namelist())
    # La edición bilingüe conserva la portada original.
    out, _ = p.export(bilingual=True)
    with zipfile.ZipFile(out) as z:
        with Image.open(io.BytesIO(z.read(names[0]))) as im:
            assert im.size == (1, 1)


def test_export_adds_cover_when_missing(tmp_path):
    book = make_epub(tmp_path / "a.epub", [("One", "<p>Mirela walked.</p>")])
    p = _project(tmp_path, book)
    out, _ = p.export()
    with zipfile.ZipFile(out) as z:
        opf_name = next(n for n in z.namelist() if n.endswith(".opf"))
        opf = z.read(opf_name).decode("utf-8")
        cover = [n for n in z.namelist() if n.endswith("jeronimo-portada.jpg")]
        assert len(cover) == 1 and z.read(cover[0])[:2] == b"\xff\xd8"
    assert 'id="jeronimo-portada"' in opf and 'properties="cover-image"' in opf
    assert '<meta name="cover" content="jeronimo-portada"/>' in opf

    # Con letras que la tipografía no tiene, se mantiene la original.
    p.config["portada"]["titulo"] = "東京の夜"
    p.save_config()
    out, _ = p.export()
    with zipfile.ZipFile(out) as z:
        assert not any(n.endswith("jeronimo-portada.jpg") for n in z.namelist())


def test_cover_api(tmp_path, monkeypatch):
    from traductor.app.api import Api

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    book = make_epub(tmp_path / "a.epub", [("One", "<p>Mirela walked.</p>")], title="Nets")
    api = Api(mode="navegador")
    api.create_project(str(book), str(tmp_path / "p"))
    cfg = api.cover_settings()
    assert cfg == {"usar": False, "estilo": "difuminada", "titulo": "Nets", "autor": "Test Author", "tiene_original": False}
    cfg = api.set_cover({"usar": True, "estilo": "lisa", "titulo": "Redes"})
    assert cfg["usar"] and cfg["estilo"] == "lisa" and cfg["titulo"] == "Redes"
    pv = api.cover_preview({"titulo": "Redes de prueba"})
    assert pv["nueva"].startswith("data:image/jpeg;base64,") and pv["original"] is None and pv["faltan"] == []
