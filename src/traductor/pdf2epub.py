"""Conversión de PDF de libro (con texto seleccionable) a EPUB 3 reflowable.

Pensado para PDF de imprenta (InDesign, LaTeX, Word…) de una sola columna:

* Quita encabezados repetidos, números de página e índices impresos.
* Reconstruye los párrafos (sangría, espacios, finales de línea cortos) y une
  las palabras cortadas con guion a final de línea, también entre páginas.
* Detecta títulos por tamaño y peso de la letra y divide en capítulos.
* Conserva cursivas, negritas y versalitas simples; convierte las llamadas en
  superíndice y las notas al pie en notas emergentes del EPUB.
* Detecta citas sangradas y líneas centradas (separadores, epígrafes).
* Usa la primera página como portada y los metadatos del PDF.

Los PDF escaneados (solo imagen) necesitan OCR y no se pueden convertir.
"""

from __future__ import annotations

import html
import io
import re
import statistics
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from .i18n import N_, tr


class PdfError(Exception):
    """Error de conversión con un mensaje pensado para el usuario."""


# ---------------------------------------------------------------------------
# Modelo


@dataclass
class Run:
    text: str
    italic: bool = False
    bold: bool = False
    sup: bool = False


@dataclass
class Line:
    page: int
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    runs: list[Run]
    bold: bool = False  # casi toda la línea en negrita

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs).strip()


@dataclass
class Page:
    number: int
    width: float
    height: float
    lines: list[Line]
    image_area: float = 0.0  # fracción de la página cubierta por imágenes


@dataclass
class Block:
    kind: str  # "h" | "p" | "quote" | "center"
    runs: list[Run]
    level: int = 0
    page: int = 0
    notes: list[tuple[str, str]] = field(default_factory=list)  # (marcador, id de nota)


@dataclass
class Report:
    pages: int = 0
    chapters: int = 0
    notes: int = 0
    removed_lines: int = 0
    dropped_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    language: str = ""
    title: str = ""
    author: str = ""
    ocr: str = ""  # motor de OCR usado (si el PDF estaba escaneado)


# ---------------------------------------------------------------------------
# Lectura del PDF

_ITALIC_RE = re.compile(r"italic|oblique|[-,]it$|[-,]ita$|kursiv", re.IGNORECASE)
_BOLD_RE = re.compile(r"bold|black|heavy|semibold|demi|extrab", re.IGNORECASE)
SPACE_GAP = 0.11  # hueco (en em) a partir del cual hay un espacio entre letras


def _style(fontname: str) -> tuple[bool, bool]:
    name = fontname.split("+", 1)[-1]
    return bool(_ITALIC_RE.search(name)), bool(_BOLD_RE.search(name))


def _build_line(page_no: int, chars: list[dict]) -> Line | None:
    chars = [c for c in chars if c.get("text")]
    if not chars:
        return None
    chars.sort(key=lambda c: c["x0"])
    sizes = Counter(round(c["size"], 1) for c in chars if c["text"].strip())
    if not sizes:
        return None
    main = sizes.most_common(1)[0][0]
    normal = [c for c in chars if abs(c["size"] - main) < 0.6 and c["text"].strip()]
    baseline = statistics.median(c["bottom"] for c in normal) if normal else max(c["bottom"] for c in chars)
    runs: list[Run] = []
    prev = None
    pending_space = False
    bold_chars = 0
    total = 0
    for c in chars:
        t = c["text"]
        if t.isspace():
            pending_space = True
            continue
        italic, bold = _style(c.get("fontname", ""))
        sup = c["size"] < main * 0.8 and c["bottom"] < baseline - main * 0.12
        total += 1
        bold_chars += bold
        if prev is not None:
            gap = c["x0"] - prev["x1"]
            ref = max(c["size"], prev["size"]) if not sup else main
            if pending_space or gap > SPACE_GAP * ref:
                _append(runs, " ", runs[-1].italic and italic, runs[-1].bold and bold, False)
        _append(runs, t, italic, bold, sup)
        prev = c
        pending_space = False
    if not runs:
        return None
    return Line(page=page_no, x0=min(c["x0"] for c in chars), x1=max(c["x1"] for c in chars),
                top=min(c["top"] for c in normal or chars), bottom=baseline, size=main,
                runs=runs, bold=total > 0 and bold_chars / total > 0.8)


def _append(runs: list[Run], text: str, italic: bool, bold: bool, sup: bool) -> None:
    if runs and (runs[-1].italic, runs[-1].bold, runs[-1].sup) == (italic, bold, sup):
        runs[-1].text += text
    else:
        runs.append(Run(text, italic, bold, sup))


def read_pdf(path: str | Path, progress=None) -> tuple[list[Page], dict]:
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover
        raise PdfError(tr("Falta el componente pdfplumber para leer PDF.")) from e
    try:
        pdf = pdfplumber.open(str(path))
    except Exception as e:
        raise PdfError(tr("No se pudo abrir el PDF: {error}", error=e)) from e
    pages: list[Page] = []
    with pdf:
        meta = dict(pdf.metadata or {})
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            lines = []
            try:
                raw = page.extract_text_lines(return_chars=True, strip=True, x_tolerance=1, y_tolerance=2.5)
            except Exception:
                raw = []
            for ln in raw:
                line = _build_line(i + 1, ln.get("chars") or [])
                if line and line.text:
                    lines.append(line)
            lines.sort(key=lambda l: (round(l.top, 0), l.x0))
            area = 0.0
            for im in page.images:
                w = max(0.0, min(im["x1"], page.width) - max(im["x0"], 0))
                h = max(0.0, min(im["bottom"], page.height) - max(im["top"], 0))
                area += w * h
            pages.append(Page(i + 1, float(page.width), float(page.height), lines,
                              min(1.0, area / (page.width * page.height))))
            if progress:
                progress((i + 1) / total * 0.7, tr("Leyendo página {i} de {total}…", i=i + 1, total=total))
    return pages, meta


# ---------------------------------------------------------------------------
# Análisis

_PAGE_NUM_RE = re.compile(r"^(?:[-–—]\s*)?(\d{1,4}|[ivxlcdm]{1,7})(?:\s*[-–—])?$", re.IGNORECASE)
_SENT_END = ".!?:;…»”\")’"
_NOTE_START_RE = re.compile(r"^(\d{1,3}|[*†‡§])[.)]?\s+\S")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", text)).strip().lower()


def _weighted_body_size(pages: list[Page]) -> float:
    counter: Counter = Counter()
    for p in pages:
        for l in p.lines:
            counter[round(l.size * 2) / 2] += len(l.text)
    if not counter:
        raise PdfError(tr("El PDF no tiene texto seleccionable (parece escaneado). "
                       "Para convertirlo haría falta reconocimiento de texto (OCR)."))
    return counter.most_common(1)[0][0]


def _remove_furniture(pages: list[Page], body: float, report: Report) -> None:
    """Quita encabezados, pies y números de página."""
    counts: Counter = Counter()
    for p in pages:
        seen = set()
        for l in p.lines:
            if l.bottom < p.height * 0.13 or l.top > p.height * 0.87:
                key = _norm(l.text)
                if key and key not in seen:
                    counts[key] += 1
                    seen.add(key)
    threshold = max(2, int(len(pages) * 0.12))
    for p in pages:
        keep = []
        body_lines = [l for l in p.lines if abs(l.size - body) < 0.8]
        first_body_top = min((l.top for l in body_lines), default=p.height)
        last_body_bottom = max((l.bottom for l in body_lines), default=0)
        for l in p.lines:
            zone_top = l.bottom < p.height * 0.13
            zone_bottom = l.top > p.height * 0.87
            text = l.text
            drop = False
            if zone_top or zone_bottom:
                if _PAGE_NUM_RE.match(text):
                    drop = True
                elif counts[_norm(text)] >= threshold:
                    drop = True
                elif zone_top and l.size < body * 0.92 and first_body_top - l.bottom > body * 1.6:
                    drop = True  # encabezado pequeño y separado del texto
                elif zone_bottom and l.size < body * 0.92 and l.top - last_body_bottom > body * 1.6 \
                        and len(text) < 60 and not _NOTE_START_RE.match(text):
                    drop = True
            if drop:
                report.removed_lines += 1
            else:
                keep.append(l)
        p.lines = keep


def _is_toc_page(p: Page) -> bool:
    lines = [l for l in p.lines if l.text]
    if len(lines) < 3:
        return False
    numbered = sum(1 for l in lines if re.search(r"(\.{2,}|\s)\s*(\d{1,4}|[ivxlcdm]{1,6})$", l.text, re.IGNORECASE)
                   and not _NOTE_START_RE.match(l.text))
    heading = any(re.fullmatch(r"(índice|indice|contenido|contenidos|sumario|tabla de contenido|contents|table of contents|"
                               r"sommaire|índice general)", l.text.strip().lower()) for l in lines)
    if heading:
        return numbered >= 2 and numbered / max(1, len(lines) - 1) >= 0.6
    return numbered >= 3 and numbered / len(lines) > 0.6


def _split_notes(p: Page, body: float) -> tuple[list[Line], list[Line]]:
    """Separa las notas al pie (letra pequeña al final de la página)."""
    lines = p.lines
    if not lines:
        return lines, []
    has_body = any(abs(l.size - body) < 0.8 for l in lines)
    if not has_body:
        return lines, []
    k = len(lines)
    while k > 0 and lines[k - 1].size <= body * 0.9:
        k -= 1
    notes = lines[k:]
    if not notes or k == 0:
        return lines, []
    first = notes[0]
    starts = first.runs[0].sup or bool(_NOTE_START_RE.match(first.text))
    gap = first.top - lines[k - 1].bottom
    if not starts and gap < body * 1.2:
        return lines, []
    return lines[:k], notes


def _parse_notes(note_lines: list[Line]) -> list[tuple[str | None, list[Run]]]:
    notes: list[tuple[str | None, list[Run]]] = []
    for l in note_lines:
        runs = [Run(r.text, r.italic, r.bold, r.sup) for r in l.runs]
        number = None
        if runs[0].sup and re.fullmatch(r"\s*[\d*†‡§]+\s*", runs[0].text):
            number = runs[0].text.strip()
            runs = runs[1:]
        else:
            m = _NOTE_START_RE.match(l.text)
            if m:
                number = m.group(1)
                first = runs[0].text.lstrip()
                runs[0].text = re.sub(r"^\s*" + re.escape(number) + r"[.)]?\s*", "", first, count=1)
        if runs:
            runs[0].text = runs[0].text.lstrip()
        if number is not None or not notes:
            notes.append((number, runs))
        else:
            _join_runs(notes[-1][1], runs)
    return notes


def _join_runs(acc: list[Run], new: list[Run]) -> None:
    """Une una línea nueva al final de un párrafo: guiones de corte y espacios."""
    if not new:
        return
    if acc:
        last = acc[-1]
        stripped = last.text.rstrip()
        nxt = new[0].text.lstrip()
        if (stripped.endswith(("-", "­", "‐")) and len(stripped) >= 2 and stripped[-2].isalpha()
                and nxt[:1].islower()):
            last.text = stripped[:-1]
            new = [Run(nxt, new[0].italic, new[0].bold, new[0].sup)] + new[1:]
        else:
            last.text = stripped
            sep_style = (last.italic and new[0].italic, last.bold and new[0].bold)
            acc.append(Run(" ", sep_style[0], sep_style[1], False))
            new = [Run(nxt, new[0].italic, new[0].bold, new[0].sup)] + new[1:]
    for r in new:
        if acc and (acc[-1].italic, acc[-1].bold, acc[-1].sup) == (r.italic, r.bold, r.sup):
            acc[-1].text += r.text
        else:
            acc.append(Run(r.text, r.italic, r.bold, r.sup))


def _percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    idx = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
    return values[idx]


def analyze(pages: list[Page], report: Report) -> tuple[list[Block], dict[str, list[Run]], dict[str, str]]:
    body = _weighted_body_size(pages)
    _remove_furniture(pages, body, report)

    # Interlineado típico del cuerpo.
    gaps = []
    for p in pages:
        bl = [l for l in p.lines if abs(l.size - body) < 0.6]
        gaps += [b.bottom - a.bottom for a, b in zip(bl, bl[1:]) if 0 < b.bottom - a.bottom < body * 3]
    leading = statistics.median(gaps) if gaps else body * 1.3

    # Clases de títulos por tamaño (sin contar portadilla, créditos ni índice impreso).
    heading_sizes: Counter = Counter()
    for p in pages:
        if _is_toc_page(p) or not any(abs(l.size - body) < 0.8 for l in p.lines):
            continue
        for l in p.lines:
            if l.size >= body * 1.18 and len(l.text) < 140:
                heading_sizes[round(l.size * 2) / 2] += 1
    classes = sorted(heading_sizes, reverse=True)
    repeated = [s for s in classes if heading_sizes[s] >= 2]
    chapter_size = repeated[0] if repeated else (classes[0] if classes else None)

    def level_for(line: Line) -> int:
        if line.size >= body * 1.18:
            size = round(line.size * 2) / 2
            if chapter_size is not None and size > chapter_size and heading_sizes[size] <= 1:
                return 0  # título del libro en la portadilla: no abre capítulo
            if chapter_size is not None and size >= chapter_size:
                return 1
            smaller = [s for s in classes if chapter_size is None or s < chapter_size]
            return 2 if not smaller or size >= smaller[0] else 3
        # Negrita del tamaño del cuerpo: subtítulo de segundo nivel si no hay otro.
        smaller = [s for s in classes if chapter_size is not None and s < chapter_size]
        return 3 if smaller else 2

    blocks: list[Block] = []
    notes_text: dict[str, list[Run]] = {}
    note_ids: dict[str, str] = {}  # "página:número" -> id
    current: Block | None = None
    last_line: Line | None = None
    serial = 0

    for p in pages:
        if not p.lines:
            continue
        if _is_toc_page(p):
            report.dropped_pages.append(p.number)
            continue
        body_lines, note_lines = _split_notes(p, body)
        # Notas de esta página.
        page_notes = {}
        for number, runs in _parse_notes(note_lines):
            if number is None:
                # Continuación de la última nota de la página anterior.
                if notes_text:
                    _join_runs(notes_text[list(notes_text)[-1]], runs)
                continue
            serial += 1
            nid = f"n{serial}"
            notes_text[nid] = runs
            page_notes[number] = nid
            note_ids[f"{p.number}:{number}"] = nid

        texty = [l for l in body_lines if abs(l.size - body) < 1.2]
        loose = not any(abs(l.size - body) < 0.8 for l in body_lines)
        left = _percentile([l.x0 for l in texty], 0.2) if texty else min((l.x0 for l in body_lines), default=0)
        right = _percentile([l.x1 for l in texty], 0.8) if texty else max((l.x1 for l in body_lines), default=p.width)

        for idx, l in enumerate(body_lines):
            size = l.size
            inset_l, inset_r = l.x0 - left, right - l.x1
            is_heading = (size >= body * 1.18 and len(l.text) < 140) or (
                l.bold and size >= body * 0.98 and len(l.text) < 90 and not l.text.endswith((".", ",", ";"))
                and (last_line is None or l.bottom - last_line.bottom > leading * 1.25 or last_line.page != l.page))
            if is_heading:
                level = level_for(l)
                gap_ok = last_line is not None and last_line.page == l.page and l.top - last_line.bottom < size * 1.9
                if current and current.kind == "h" and current.level == level and gap_ok:
                    _join_runs(current.runs, _plain(l.runs))
                else:
                    current = Block("h", _plain(l.runs), level=level, page=p.number)
                    blocks.append(current)
                last_line = l
                continue

            if loose:
                current = Block("line", [Run(r.text, r.italic, r.bold, r.sup) for r in l.runs], page=p.number)
                blocks.append(current)
                last_line = l
                continue
            centered = (inset_l > size * 2 and abs(inset_l - inset_r) < size * 1.6
                        and (l.x1 - l.x0) < (right - left) * 0.75)
            nxt = body_lines[idx + 1] if idx + 1 < len(body_lines) else None
            quote = inset_l >= size * 1.1 and (
                size < body * 0.97 or (nxt is not None and nxt.x0 - left >= size * 1.1 and inset_r >= size * 1.1))
            kind = "center" if centered and not quote else "quote" if quote else "p"

            new_par = True
            if current is not None and current.kind == kind and kind != "center" and last_line is not None:
                same_page = last_line.page == l.page
                gap = l.bottom - last_line.bottom if same_page else leading
                indent = kind == "p" and size * 0.5 < inset_l < size * 5
                prev_short = (right - last_line.x1) > size * 2.5 and last_line.text.rstrip().endswith(tuple(_SENT_END))
                if not indent and not prev_short and gap <= leading * 1.45:
                    new_par = False
            if new_par:
                current = Block(kind, [], page=p.number)
                blocks.append(current)
                _join_runs(current.runs, [Run(r.text, r.italic, r.bold, r.sup) for r in l.runs])
            else:
                _join_runs(current.runs, [Run(r.text, r.italic, r.bold, r.sup) for r in l.runs])
            # Llamadas a nota en esta línea.
            for r in l.runs:
                if r.sup and r.text.strip() in page_notes:
                    current.notes.append((r.text.strip(), page_notes[r.text.strip()]))
            last_line = l

    # Superíndices sin nota en la misma página: buscar en la siguiente (nota desplazada).
    return blocks, notes_text, note_ids


def _plain(runs: list[Run]) -> list[Run]:
    """Texto de un título: sin negrita (ya lo es) y sin llamadas."""
    return [Run(r.text, r.italic, False, False) for r in runs if not r.sup]


# ---------------------------------------------------------------------------
# Idioma

_STOPWORDS = {
    "es": "de la que el en los se del las por un una con para es su al lo como más pero sus le ya o fue este ha sí porque esta",
    "en": "the of and to in a is that it was for on as with his he be at by i this had not are but from",
    "pt": "de a o que e do da em um para é com não uma os no se na por mais as dos como mas foi ao",
    "fr": "de la le et les des en un du une que est pour qui dans ne pas au sur se par il plus",
    "it": "di e il la che in a per un è del non sono le si con da una della gli al",
    "de": "der die und in den von zu das mit sich des auf für ist im dem nicht ein eine als auch",
}


def detect_language(text: str) -> str:
    words = re.findall(r"[a-záéíóúüñàèìòùâêîôûçãõäöß]+", text.lower())
    counts = Counter(words)
    scores = {lang: sum(counts[w] for w in ws.split()) for lang, ws in _STOPWORDS.items()}
    return max(scores, key=scores.get) if any(scores.values()) else "es"


# ---------------------------------------------------------------------------
# Escritura del EPUB

CSS = """\
body { margin: 0 3%; font-family: serif; line-height: 1.45; }
p { margin: 0; text-indent: 1.3em; text-align: justify; hyphens: auto; -webkit-hyphens: auto; widows: 2; orphans: 2; }
h1 + p, h2 + p, h3 + p, p.sin-sangria, blockquote + p, p.centrado + p, .portadilla p { text-indent: 0; }
h1, h2, h3 { font-weight: bold; line-height: 1.25; hyphens: none; -webkit-hyphens: none; page-break-after: avoid; }
h1 { font-size: 1.45em; text-align: center; margin: 2.5em 0 1.6em; }
h2 { font-size: 1.2em; margin: 1.6em 0 0.8em; }
h3 { font-size: 1.05em; margin: 1.3em 0 0.6em; }
blockquote { margin: 0.9em 1.4em; font-size: 0.95em; }
blockquote p { text-indent: 0; }
blockquote p + p { text-indent: 1.3em; }
p.centrado { text-align: center; text-indent: 0; margin: 0.9em 0; }
.portadilla { text-align: center; margin-top: 15%; }
.portadilla p { text-align: center; text-indent: 0; margin: 0.4em 0; }
p.t-grande { font-size: 1.6em; font-weight: bold; margin: 1em 0 0.4em; }
p.t-medio { font-size: 1.2em; margin: 0.3em 0 1.2em; }
p.linea { text-indent: 0; text-align: left; font-size: 0.85em; margin: 0.15em 0; }
.portadilla p.linea { text-align: center; }
a.nota { vertical-align: super; font-size: 0.7em; line-height: 0; text-decoration: none; }
section.notas { margin-top: 2.5em; font-size: 0.88em; border-top: 1px solid #999; padding-top: 0.6em; }
section.notas aside p { text-indent: 0; margin: 0 0 0.5em; }
img.portada { max-width: 100%; max-height: 100%; }
div.portada { text-align: center; margin: 0; padding: 0; }
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _runs_html(runs: list[Run], refs: list[tuple[str, str]] | None = None) -> str:
    out = []
    pending = list(refs or [])
    for r in runs:
        text = r.text
        if r.sup:
            key = text.strip()
            nid = None
            if pending and pending[0][0] == key:
                nid = pending.pop(0)[1]
            if nid:
                out.append(f'<a class="nota" epub:type="noteref" href="#{nid}" id="r{nid}">{_esc(key)}</a>')
            else:
                out.append(f"<sup>{_esc(text)}</sup>")
            continue
        piece = _esc(text)
        if r.bold:
            piece = f"<b>{piece}</b>"
        if r.italic:
            piece = f"<i>{piece}</i>"
        out.append(piece)
    html_text = "".join(out)
    html_text = re.sub(r"</i><i>|</b><b>", "", html_text)
    return re.sub(r"\s+", " ", html_text).strip()


def _xhtml(title: str, lang: str, body: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'lang="{lang}" xml:lang="{lang}">\n<head>\n<meta charset="utf-8"/>\n<title>{_esc(title)}</title>\n'
            '<link rel="stylesheet" type="text/css" href="estilos.css"/>\n</head>\n'
            f"<body>\n{body}\n</body>\n</html>\n")


def _render_cover(pdf_path: Path, page_number: int) -> bytes | None:
    try:
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as pdf:
            page = pdf.pages[page_number - 1]
            img = page.to_image(height=1600).original.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=88)
            return buf.getvalue()
    except Exception:
        return None


def build_epub(blocks: list[Block], notes: dict[str, list[Run]], *, title: str, author: str, lang: str,
               cover: bytes | None, out_path: Path, report: Report) -> None:
    # Capítulos: se corta en cada título de nivel 1.
    chapters: list[tuple[str, list[Block]]] = []
    for b in blocks:
        if b.kind == "h" and b.level == 1:
            chapters.append(("".join(r.text for r in b.runs).strip(), [b]))
        else:
            if not chapters:
                chapters.append(("", []))
            chapters[-1][1].append(b)
    files = []
    toc = []  # (archivo, título, nivel, ancla)
    used_notes = set()
    for k, (ch_title, ch_blocks) in enumerate(chapters):
        name = f"texto{k:03d}.xhtml"
        parts = []
        chapter_notes: list[str] = []
        front = not ch_title
        if front:
            parts.append('<section class="portadilla" epub:type="frontmatter">')
        else:
            parts.append('<section epub:type="chapter">')
        first_after_heading = True
        quote_open = False
        for j, b in enumerate(ch_blocks):
            refs = [(m, n) for m, n in b.notes if n not in used_notes]
            for _m, n in refs:
                used_notes.add(n)
            chapter_notes += [n for _m, n in refs]
            content = _runs_html(b.runs, refs)
            if not content:
                continue
            if b.kind != "quote" and quote_open:
                parts.append("</blockquote>")
                quote_open = False
            if b.kind == "h" and (front or b.level == 0):
                cls = "t-grande" if b.level <= 1 else "t-medio"
                parts.append(f'<p class="{cls}">{content}</p>')
                continue
            if b.kind == "line":
                parts.append(f'<p class="linea">{content}</p>')
                continue
            if b.kind == "h":
                anchor = f"t{k}_{j}"
                level = min(3, max(1, b.level))
                parts.append(f'<h{level} id="{anchor}">{content}</h{level}>')
                plain = "".join(r.text for r in b.runs).strip()
                if not front and level <= 2:
                    toc.append((name, plain, level, anchor))
                first_after_heading = True
                continue
            if b.kind == "quote":
                if not quote_open:
                    parts.append("<blockquote>")
                    quote_open = True
                parts.append(f"<p>{content}</p>")
            elif b.kind == "center":
                parts.append(f'<p class="centrado">{content}</p>')
            else:
                cls = ' class="sin-sangria"' if first_after_heading else ""
                parts.append(f"<p{cls}>{content}</p>")
            first_after_heading = False
        if quote_open:
            parts.append("</blockquote>")
        if chapter_notes:
            parts.append('<section class="notas" epub:type="footnotes">')
            for n in chapter_notes:
                parts.append(f'<aside epub:type="footnote" id="{n}"><p><a href="#r{n}">↑</a> '
                             f'{_runs_html(notes[n])}</p></aside>')
            parts.append("</section>")
        parts.append("</section>")
        label = ch_title or title
        files.append((name, label, "\n".join(parts)))
    if not toc and files:
        toc = [(files[0][0], title, 1, "")]

    report.chapters = sum(1 for t, _b in chapters if t)
    report.notes = len(notes)
    missing = [n for n in notes if n not in used_notes]
    if missing:
        report.warnings.append(tr("{n} nota(s) sin llamada reconocida en el texto: se añadieron al final.", n=len(missing)))
        extra = ['<section class="notas" epub:type="footnotes"><h2>Notas</h2>']
        for n in missing:
            extra.append(f'<aside id="{n}"><p>{_runs_html(notes[n])}</p></aside>')
        extra.append("</section>")
        files.append((f"texto{len(files):03d}.xhtml", "Notas", "\n".join(extra)))

    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="estilos.css" media-type="text/css"/>']
    spine = []
    if cover:
        manifest.append('<item id="portada-img" href="imagenes/portada.jpg" media-type="image/jpeg" properties="cover-image"/>')
        manifest.append('<item id="portada" href="portada.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="portada" linear="yes"/>')
    for k, (name, _label, _body) in enumerate(files):
        manifest.append(f'<item id="t{k}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="t{k}"/>')

    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{book_id}</dc:identifier>
<dc:title>{_esc(title)}</dc:title>
{f'<dc:creator>{_esc(author)}</dc:creator>' if author else ''}
<dc:language>{lang}</dc:language>
<meta property="dcterms:modified">{modified}</meta>
{'<meta name="cover" content="portada-img"/>' if cover else ''}
</metadata>
<manifest>
{chr(10).join(manifest)}
</manifest>
<spine toc="ncx">
{chr(10).join(spine)}
</spine>
</package>
"""
    nav_items = []
    open_sub = False
    for i, (name, text, level, anchor) in enumerate(toc):
        href = f"{name}#{anchor}" if anchor else name
        if level == 1:
            if open_sub:
                nav_items.append("</ol></li>")
                open_sub = False
            elif nav_items:
                nav_items.append("</li>")
            nav_items.append(f'<li><a href="{href}">{_esc(text)}</a>')
        else:
            if not open_sub:
                nav_items.append("<ol>")
                open_sub = True
            nav_items.append(f'<li><a href="{href}">{_esc(text)}</a></li>')
    if open_sub:
        nav_items.append("</ol></li>")
    elif nav_items:
        nav_items.append("</li>")
    if not any(t[2] == 1 for t in toc):  # solo subtítulos: lista plana
        nav_items = [f'<li><a href="{n}#{a}">{_esc(t)}</a></li>' for n, t, _l, a in toc]
    nav = _xhtml("Índice", lang, '<nav epub:type="toc" id="toc"><h1>Índice</h1>\n<ol>\n'
                 + "\n".join(nav_items) + "\n</ol>\n</nav>")
    points = []
    for i, (name, text, _level, anchor) in enumerate(toc, 1):
        href = f"{name}#{anchor}" if anchor else name
        points.append(f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{_esc(text)}</text></navLabel>'
                      f'<content src="{href}"/></navPoint>')
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{lang}">
<head><meta name="dtb:uid" content="{book_id}"/><meta name="dtb:depth" content="1"/>
<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>
<docTitle><text>{_esc(title)}</text></docTitle>
<navMap>
{chr(10).join(points)}
</navMap>
</ncx>
"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        z.writestr(info, "application/epub+zip")

        def put(name: str, data) -> None:
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data,
                       compress_type=zipfile.ZIP_DEFLATED)

        put("META-INF/container.xml", '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>")
        put("OEBPS/content.opf", opf)
        put("OEBPS/nav.xhtml", nav)
        put("OEBPS/toc.ncx", ncx)
        put("OEBPS/estilos.css", CSS)
        if cover:
            put("OEBPS/imagenes/portada.jpg", cover)
            put("OEBPS/portada.xhtml", _xhtml("Portada", lang, '<div class="portada" epub:type="cover">'
                                                               '<img class="portada" src="imagenes/portada.jpg" alt="Portada"/></div>'))
        for name, label, body in files:
            put(f"OEBPS/{name}", _xhtml(label, lang, body))
    tmp.replace(out_path)


# ---------------------------------------------------------------------------
# Punto de entrada


def is_scanned(pages: list[Page]) -> bool:
    """¿Páginas casi sin texto? (un PDF escaneado: sus páginas son imágenes)."""
    chars = sum(len(line.text) for p in pages for line in p.lines)
    return bool(pages) and chars < 40 * len(pages)


OCR_WARNING = N_("PDF escaneado: el texto se reconoció con {motor}. Revisa el resultado; "
                 "las cursivas y negritas no se reconocen.")


def convert(pdf_path: str | Path, out_path: str | Path | None = None, *, title: str | None = None,
            author: str | None = None, language: str | None = None, progress=None, ocr_lang: str = "",
            should_stop=None, ocr_engine=None) -> tuple[Path, Report]:
    """Convierte un PDF en EPUB. Devuelve la ruta del EPUB y un informe.

    Si el PDF está escaneado, reconoce el texto con OCR (``ocr_lang``: idioma
    del libro, p. ej. «es»; vacío para probar con los idiomas disponibles)."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise PdfError(tr("No encuentro el archivo «{pdf_path}».", pdf_path=pdf_path))
    out = Path(out_path) if out_path else pdf_path.with_suffix(".epub")
    report = Report()
    pages, meta = read_pdf(pdf_path, progress)
    report.pages = len(pages)
    if is_scanned(pages):
        from .ocr import ocr_pdf

        pages, report.ocr = ocr_pdf(pdf_path, ocr_lang or language or "", progress=progress,
                                    should_stop=should_stop, engine=ocr_engine)
        report.warnings.append(tr(OCR_WARNING, motor=report.ocr))
    if progress:
        progress(0.75, tr("Reconstruyendo párrafos y capítulos…"))
    blocks, notes, _ids = analyze(pages, report)
    if not blocks:
        raise PdfError(tr("No se encontró texto en el PDF."))
    text_sample = " ".join(r.text for b in blocks[:400] for r in b.runs)
    report.language = language or detect_language(text_sample)
    report.title = (title or str(meta.get("Title") or "").strip() or pdf_path.stem.replace("_", " "))
    report.author = (author if author is not None else str(meta.get("Author") or "").strip())
    cover_page = next((p.number for p in pages if p.image_area > 0.5), None) or 1
    if cover_page > 3:
        cover_page = 1
    cover = _render_cover(pdf_path, cover_page)
    # La página de portada (solo imagen) no aporta texto; si tenía texto, se conserva en el cuerpo.
    if progress:
        progress(0.9, tr("Escribiendo el EPUB…"))
    build_epub(blocks, notes, title=report.title, author=report.author, lang=report.language,
               cover=cover, out_path=out, report=report)
    if progress:
        progress(1.0, tr("Listo."))
    return out, report
