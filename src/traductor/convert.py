"""Convertir otros formatos a EPUB: PDF, Word (DOCX), texto (TXT), FictionBook
(FB2) y Kindle sin DRM (MOBI, AZW3, con Calibre si está instalado).

Word, TXT y FB2 se leen con su estructura (títulos, párrafos, citas, cursivas,
negritas y notas al pie) y se escriben con el mismo maquetador de EPUB que el
conversor de PDF."""

from __future__ import annotations

import base64
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

from .pdf2epub import Block, PdfError, Report, Run, build_epub, detect_language
from .i18n import N_, tr

FORMATS = {".epub": "EPUB", ".pdf": "PDF", ".docx": "Word", ".txt": N_("texto"), ".fb2": "FictionBook",
           ".mobi": "Kindle (MOBI)", ".azw3": "Kindle (AZW3)", ".azw": "Kindle (AZW)"}


class ConvertError(PdfError):
    """Error al convertir un libro (se muestra tal cual a la persona)."""


def kind_of(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".fb2.zip"):
        return ".fb2"
    return path.suffix.lower()


# ---------------------------------------------------------------------------
# Word (DOCX)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _on(rpr, tag: str) -> bool:
    if rpr is None:
        return False
    el = rpr.find(W + tag)
    return el is not None and el.get(W + "val", "true").lower() not in ("0", "false", "off", "none")


def _docx_runs(container, notes_src: dict[str, list[Run]], notes: dict, counter: list[int]):
    """Trozos con formato de un párrafo de Word y sus llamadas a notas."""
    runs: list[Run] = []
    refs: list[tuple[str, str]] = []
    for r in container.iter(W + "r"):
        ref = r.find(W + "footnoteReference")
        if ref is not None:
            counter[0] += 1
            nid = f"n{counter[0]}"
            notes[nid] = notes_src.get(ref.get(W + "id"), [])
            runs.append(Run(str(counter[0]), sup=True))
            refs.append((str(counter[0]), nid))
            continue
        rpr = r.find(W + "rPr")
        valign = rpr.find(W + "vertAlign") if rpr is not None else None
        sup = valign is not None and valign.get(W + "val") == "superscript"
        parts = []
        for el in r:
            if el.tag == W + "t":
                parts.append(el.text or "")
            elif el.tag in (W + "tab", W + "br", W + "cr"):
                parts.append(" ")
        text = "".join(parts)
        if text:
            runs.append(Run(text, italic=_on(rpr, "i"), bold=_on(rpr, "b"), sup=sup))
    return runs, refs


def _docx_footnotes(doc) -> dict[str, list[Run]]:
    out: dict[str, list[Run]] = {}
    for rel in doc.part.rels.values():
        if rel.reltype.endswith("/footnotes"):
            root = etree.fromstring(rel.target_part.blob)
            for fn in root.iter(W + "footnote"):
                if fn.get(W + "type") in ("separator", "continuationSeparator", "continuationNotice"):
                    continue
                runs, _refs = _docx_runs(fn, {}, {}, [0])
                out[fn.get(W + "id")] = [r for r in runs if r.text.strip() or r.text == " "]
    return out


def docx_blocks(path: Path) -> tuple[list[Block], dict, dict, list[str]]:
    from docx import Document

    try:
        doc = Document(str(path))
    except Exception as e:  # archivo dañado o que no es DOCX
        raise ConvertError(tr("No se pudo abrir «{name}» como documento de Word: {error}", name=path.name, error=e)) from e
    notes_src = _docx_footnotes(doc)
    notes: dict[str, list[Run]] = {}
    counter = [0]
    blocks: list[Block] = []
    title = ""
    warnings = []
    if doc.inline_shapes:
        warnings.append(tr("Las imágenes del documento no se incluyen en el EPUB."))
    for p in doc.paragraphs:
        runs, refs = _docx_runs(p._p, notes_src, notes, counter)
        text = "".join(r.text for r in runs).strip()
        if not text:
            continue
        style = (p.style.name if p.style is not None else "").lower()
        m = re.match(r"(?:heading|título|titulo|encabezado)\s*(\d)", style)
        if style in ("title", "título", "titulo"):
            if not title:
                title = text
                continue
            blocks.append(Block("h", runs, level=1, notes=refs))
        elif m:
            blocks.append(Block("h", runs, level=int(m.group(1)), notes=refs))
        elif "quote" in style or "cita" in style:
            blocks.append(Block("quote", runs, notes=refs))
        elif style in ("subtitle", "subtítulo") or (p.alignment is not None and int(p.alignment) == 1):
            blocks.append(Block("center", runs, notes=refs))
        else:
            blocks.append(Block("p", runs, notes=refs))
    _promote_headings(blocks)
    core = doc.core_properties
    meta = {"title": (core.title or "").strip() or title, "author": (core.author or "").strip(),
            "language": (core.language or "").split("-")[0].lower()}
    return blocks, notes, meta, warnings


def _promote_headings(blocks: list[Block]) -> None:
    """Si no hay títulos de nivel 1, el nivel más alto que haya marca los capítulos."""
    levels = sorted({b.level for b in blocks if b.kind == "h"})
    if levels and levels[0] > 1:
        shift = levels[0] - 1
        for b in blocks:
            if b.kind == "h":
                b.level -= shift


# ---------------------------------------------------------------------------
# Texto (TXT)

CHAPTER_RE = re.compile(
    r"^(cap[ií]tulo|chapter|parte|part|libro|book|pr[oó]logo|prologue|ep[ií]logo|epilogue|"
    r"introducci[oó]n|introduction|chapitre|kapitel|capitolo)\b.{0,60}$", re.IGNORECASE)
SCENE_RE = re.compile(r"^[\s*#~·•—–-]{3,}$")
NUMBER_RE = re.compile(r"^([IVXLCDM]{1,7}|\d{1,3})[.)]?$")
EMPH_RE = re.compile(r"(?<![\w*_])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*_])")


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _emphasis(text: str) -> list[Run]:
    runs, last = [], 0
    for m in EMPH_RE.finditer(text):
        if m.start() > last:
            runs.append(Run(text[last:m.start()]))
        runs.append(Run(m.group(2), italic=True))
        last = m.end()
    if last < len(text):
        runs.append(Run(text[last:]))
    return runs


def _is_heading(par: str) -> int:
    """Nivel de título (0 si es un párrafo normal)."""
    md = re.match(r"^(#{1,3})\s+\S", par)
    if md:
        return len(md.group(1))
    if len(par) > 80 or par.endswith((".", ",", ";", ":")) and not NUMBER_RE.match(par):
        return 0
    if CHAPTER_RE.match(par) or NUMBER_RE.match(par):
        return 1
    letters = [c for c in par if c.isalpha()]
    if len(par) <= 60 and len(letters) >= 3 and sum(c.isupper() for c in letters) >= 0.8 * len(letters):
        return 1
    return 0


def txt_blocks(path: Path) -> tuple[list[Block], dict, dict, list[str]]:
    text = read_text(path).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = text.split("\n")
    blank = sum(1 for line in lines if not line.strip())
    paragraphs: list[str] = []
    if blank >= 0.15 * max(1, len(lines) - blank):
        current: list[str] = []
        for line in lines + [""]:
            if line.strip():
                current.append(line.strip())
            elif current:
                paragraphs.append(" ".join(current))
                current = []
    else:  # un párrafo por línea
        paragraphs = [line.strip() for line in lines if line.strip()]
    blocks: list[Block] = []
    for par in paragraphs:
        if SCENE_RE.match(par):
            blocks.append(Block("center", [Run("* * *")]))
            continue
        level = _is_heading(par)
        if level:
            blocks.append(Block("h", [Run(par.lstrip("#").strip())], level=level))
        else:
            blocks.append(Block("p", _emphasis(par)))
    _promote_headings(blocks)
    return blocks, {}, {"title": "", "author": "", "language": ""}, []


# ---------------------------------------------------------------------------
# FictionBook (FB2)


def _fb2_root(path: Path):
    data = path.read_bytes()
    if data[:2] == b"PK":  # .fb2.zip
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".fb2")), None)
            if not name:
                raise ConvertError(tr("El ZIP no contiene ningún archivo FB2."))
            data = z.read(name)
    parser = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True)
    root = etree.fromstring(data, parser)
    if root is None:
        raise ConvertError(tr("«{name}» no parece un FB2 válido.", name=path.name))
    return root


def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _href(el) -> str:
    for k, v in el.attrib.items():
        if k.endswith("href"):
            return v
    return ""


def fb2_blocks(path: Path) -> tuple[list[Block], dict, dict, list[str], bytes | None]:
    root = _fb2_root(path)
    find = lambda el, name: next((c for c in el.iter() if _local(c) == name), None)  # noqa: E731
    info = find(root, "title-info")
    title = author = lang = ""
    if info is not None:
        t = find(info, "book-title")
        title = " ".join("".join(t.itertext()).split()) if t is not None else ""
        a = find(info, "author")
        if a is not None:
            parts = [" ".join("".join(c.itertext()).split()) for c in a if _local(c) in ("first-name", "middle-name", "last-name")]
            author = " ".join(p for p in parts if p) or " ".join("".join(a.itertext()).split())
        lg = find(info, "lang")
        lang = (lg.text or "").strip().lower() if lg is not None else ""

    binaries = {b.get("id"): b.text or "" for b in root.iter() if _local(b) == "binary"}
    cover = None
    coverpage = find(info, "coverpage") if info is not None else None
    if coverpage is not None:
        img = find(coverpage, "image")
        ref = _href(img).lstrip("#") if img is not None else ""
        if ref in binaries:
            try:
                cover = base64.b64decode(binaries[ref])
            except (ValueError, TypeError):
                cover = None

    bodies = [b for b in root if _local(b) == "body"]
    note_bodies = [b for b in bodies if (b.get("name") or "").lower() in ("notes", "comments", "footnotes")]
    main = [b for b in bodies if b not in note_bodies]
    note_src: dict[str, list[Run]] = {}
    for nb in note_bodies:
        for sec in nb.iter():
            if _local(sec) == "section" and sec.get("id"):
                runs: list[Run] = []
                for p in sec:
                    if _local(p) == "p":
                        if runs:
                            runs.append(Run(" "))
                        runs += _fb2_inline(p, None, None)
                note_src[sec.get("id")] = runs

    notes: dict[str, list[Run]] = {}
    counter = [0]
    blocks: list[Block] = []
    warnings: list[str] = []

    def para(el, kind="p", level=0):
        refs: list[tuple[str, str]] = []
        runs = _fb2_inline(el, (note_src, notes, counter), refs)
        if "".join(r.text for r in runs).strip():
            blocks.append(Block(kind, runs, level=level, notes=refs))

    def walk(el, depth: int) -> None:
        for child in el:
            name = _local(child)
            if name == "title":
                for p in child:
                    if _local(p) == "p":
                        para(p, "h", min(3, max(1, depth)))
            elif name == "section":
                walk(child, depth + 1)
            elif name == "p":
                para(child)
            elif name == "subtitle":
                para(child, "center")
            elif name in ("epigraph", "cite", "poem", "stanza"):
                for p in child.iter():
                    if _local(p) in ("p", "v", "text-author"):
                        para(p, "quote")
            elif name == "image" and "imagen" not in " ".join(warnings):
                warnings.append(tr("Las imágenes del interior no se incluyen en el EPUB."))
            elif name == "empty-line":
                continue

    for body in main:
        walk(body, 0)
    _promote_headings(blocks)
    return blocks, notes, {"title": title, "author": author, "language": lang}, warnings, cover


def _fb2_inline(el, note_ctx, refs) -> list[Run]:
    """Trozos con formato de un párrafo FB2 (énfasis, negrita, superíndice y notas)."""
    runs: list[Run] = []

    def rec(node, italic=False, bold=False, sup=False):
        if node.text:
            runs.append(Run(node.text, italic, bold, sup))
        for child in node:
            name = _local(child)
            if name == "a" and (child.get("type") == "note" or _href(child).startswith("#")) and note_ctx:
                note_src, notes, counter = note_ctx
                target = _href(child).lstrip("#")
                if target in note_src:
                    counter[0] += 1
                    nid = f"n{counter[0]}"
                    notes[nid] = note_src[target]
                    runs.append(Run(str(counter[0]), sup=True))
                    refs.append((str(counter[0]), nid))
                else:
                    rec(child, italic, bold, sup)
            else:
                rec(child, italic or name == "emphasis", bold or name == "strong", sup or name == "sup")
            if child.tail:
                runs.append(Run(child.tail, italic, bold, sup))

    rec(el)
    for r in runs:
        r.text = re.sub(r"\s+", " ", r.text)
    return runs


# ---------------------------------------------------------------------------
# Kindle (MOBI, AZW3) con Calibre


def find_calibre() -> str | None:
    exe = shutil.which("ebook-convert")
    if exe:
        return exe
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), r"C:\Program Files"):
        for folder in ("Calibre2", "Calibre"):
            candidate = Path(base or "") / folder / "ebook-convert.exe"
            if base and candidate.is_file():
                return str(candidate)
    return None


def calibre_convert(path: Path, out: Path) -> None:
    exe = find_calibre()
    if not exe:
        raise ConvertError(tr("Para abrir libros MOBI o AZW3 hace falta Calibre, un programa gratuito "
                           "(calibre-ebook.com). Instálalo y vuelve a intentarlo, o convierte el libro a EPUB "
                           "con Calibre."))
    flags = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}  # sin ventana de consola
    try:
        done = subprocess.run([exe, str(path), str(out)], capture_output=True, text=True, timeout=900, **flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ConvertError(tr("Calibre no pudo convertir el libro: {error}", error=e)) from e
    output = (done.stdout or "") + (done.stderr or "")
    if done.returncode != 0 or not out.is_file():
        if "DRM" in output:
            raise ConvertError(tr("El libro tiene DRM (protección anticopia): no se puede convertir."))
        tail = " ".join(output.strip().splitlines()[-2:])[:300]
        raise ConvertError(tr("Calibre no pudo convertir el libro. {tail}", tail=tail).strip())


# ---------------------------------------------------------------------------
# Punto de entrada


def convert(path: str | Path, out_path: str | Path | None = None, *, title: str | None = None,
            author: str | None = None, language: str | None = None, progress=None, ocr_lang: str = "",
            should_stop=None) -> tuple[Path, Report]:
    """Convierte un libro (PDF, DOCX, TXT, FB2, MOBI, AZW3) a EPUB."""
    path = Path(path)
    if not path.is_file():
        raise ConvertError(tr("No encuentro el archivo «{path}».", path=path))
    kind = kind_of(path)
    if kind not in FORMATS:
        raise ConvertError(tr("Formato no admitido. Usa EPUB, PDF, Word (DOCX), TXT, FB2, MOBI o AZW3."))
    if kind == ".epub":
        raise ConvertError(tr("Ese libro ya es un EPUB: úsalo directamente para crear el proyecto."))
    stem = path.name[:-len(".fb2.zip")] if path.name.lower().endswith(".fb2.zip") else path.stem
    out = Path(out_path) if out_path else path.with_name(stem + ".epub")
    if kind == ".pdf":
        from .pdf2epub import convert as pdf_convert

        return pdf_convert(path, out, title=title, author=author, language=language, progress=progress,
                           ocr_lang=ocr_lang, should_stop=should_stop)

    report = Report()
    if kind in (".mobi", ".azw3", ".azw"):
        if progress:
            progress(0.1, tr("Convirtiendo con Calibre…"))
        out.parent.mkdir(parents=True, exist_ok=True)
        calibre_convert(path, out)
        report.title = title or stem
        if progress:
            progress(1.0, tr("Listo."))
        return out, report

    if progress:
        progress(0.2, tr("Leyendo el archivo de {formato}…", formato=tr(FORMATS[kind])))
    cover = None
    if kind == ".docx":
        blocks, notes, meta, warnings = docx_blocks(path)
    elif kind == ".fb2":
        blocks, notes, meta, warnings, cover = fb2_blocks(path)
    else:
        blocks, notes, meta, warnings = txt_blocks(path)
    if not blocks:
        raise ConvertError(tr("No se encontró texto en el archivo."))
    report.warnings += warnings
    sample = " ".join(r.text for b in blocks[:400] for r in b.runs)
    report.language = language or meta.get("language") or detect_language(sample)
    report.title = title or meta.get("title") or stem.replace("_", " ")
    report.author = author if author is not None else meta.get("author", "")
    if progress:
        progress(0.7, tr("Escribiendo el EPUB…"))
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_out = Path(tmp) / "libro.epub"
        build_epub(blocks, notes, title=report.title, author=report.author, lang=report.language,
                   cover=cover, out_path=tmp_out, report=report)
        shutil.move(str(tmp_out), out)
    if progress:
        progress(1.0, tr("Listo."))
    return out, report
