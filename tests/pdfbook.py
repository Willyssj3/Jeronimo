"""Genera PDF de libro de prueba parecidos a los que exporta InDesign
(texto inventado): encabezados alternos, números de página, párrafos
justificados con sangría, palabras cortadas con guion, cursivas, notas al pie,
citas, separadores, índice impreso y portada."""

from __future__ import annotations

import io
import re

from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

W, H = 345.84, 535.92
TOP, BOTTOM = 58.0, 62.0
INNER, OUTER = 52.0, 40.0
BODY, LEAD = 10.5, 13.6
NOTE, NOTE_LEAD = 8.2, 10.2
INDENT = 12.0
FONTS = {"r": "Times-Roman", "i": "Times-Italic", "b": "Times-Bold"}


def tokens(text: str):
    """Palabras con estilo: *cursiva*, **negrita** y ^N (llamada a nota)."""
    out = []
    for raw in text.split():
        style = "r"
        word = raw
        if word.startswith("**") and word.endswith("**") and len(word) > 4:
            style, word = "b", word[2:-2]
        elif word.startswith("*") and word.endswith("*") and len(word) > 2:
            style, word = "i", word[1:-1]
        note = None
        m = re.search(r"\^(\d+)", word)
        if m:
            note = m.group(1)
            word = word[:m.start()] + word[m.end():]
        out.append((word, style, note))
    return out


class BookPDF:
    def __init__(self, title: str, author: str):
        self.title, self.author = title, author
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=(W, H))
        self.c.setTitle(title)
        self.c.setAuthor(author)
        self.page = 0
        self.chapter = ""
        self.y = 0.0
        self.notes: list[tuple[str, str]] = []
        self.pending_notes: dict[str, str] = {}
        self.page_open = False
        self.chapter_start = False

    # -- páginas ---------------------------------------------------------------

    def margins(self):
        odd = self.page % 2 == 1
        return (INNER, W - OUTER) if odd else (OUTER, W - INNER)

    def new_page(self, *, chapter_start=False, bare=False):
        if self.page_open:
            self.finish_page()
        self.page += 1
        self.page_open = True
        self.bare = bare
        self.chapter_start = chapter_start
        self.y = H - TOP
        self.notes = []

    def finish_page(self):
        c = self.c
        if not self.bare:
            if not self.chapter_start:
                header = self.title.upper() if self.page % 2 == 0 else self.chapter
                c.setFont("Times-Roman", 7.5)
                c.drawCentredString(W / 2, H - 32, header)
            c.setFont("Times-Roman", 9)
            c.drawCentredString(W / 2, 30, str(self.page))
            if self.notes:
                left, right = self.margins()
                y = BOTTOM + 4 + NOTE_LEAD * sum(n[2] for n in self.notes)
                c.setLineWidth(0.4)
                c.line(left, y + 6, left + 50, y + 6)
                for number, text, _lines in self.notes:
                    words = tokens(text)
                    first = True
                    for line in self.wrap(words, right - left - 8, NOTE):
                        y -= NOTE_LEAD
                        x = left
                        if first:
                            c.setFont("Times-Roman", 5.5)
                            c.drawString(x, y + 3, number)
                            first = False
                        self.draw_words(line, x + 8, y, NOTE, right - left - 8, justify=False)
        c.showPage()
        self.page_open = False

    def space_left(self):
        notes = sum(n[2] for n in self.notes) * NOTE_LEAD + (12 if self.notes else 0)
        return self.y - BOTTOM - notes

    # -- texto -------------------------------------------------------------------

    @staticmethod
    def width(word, style, size):
        return stringWidth(word, FONTS[style], size)

    def wrap(self, words, width, size, first_indent=0.0):
        """Reparte palabras en líneas; parte con guion las que no caben."""
        lines, line, used = [], [], first_indent
        space = stringWidth(" ", "Times-Roman", size)
        queue = list(words)
        while queue:
            word, style, note = queue.pop(0)
            w = self.width(word, style, size) + (stringWidth(note, "Times-Roman", size * 0.62) if note else 0)
            need = w + (space if line else 0)
            if used + need <= width:
                line.append((word, style, note))
                used += need
                continue
            # ¿Se puede cortar con guion?
            if len(word) >= 8 and not note:
                for cut in range(len(word) - 3, 2, -1):
                    head = word[:cut] + "-"
                    if used + space + self.width(head, style, size) <= width:
                        line.append((head, style, None))
                        queue.insert(0, (word[cut:], style, note))
                        break
                else:
                    queue.insert(0, (word, style, note))
            else:
                queue.insert(0, (word, style, note))
            lines.append(line)
            line, used = [], 0.0
        if line:
            lines.append(line)
        return lines

    def draw_words(self, line, x, y, size, width, justify=True):
        c = self.c
        natural = sum(self.width(w, s, size) for w, s, _n in line)
        gaps = len(line) - 1
        space = stringWidth(" ", "Times-Roman", size)
        if justify and gaps:
            space = (width - natural - sum(stringWidth(n, "Times-Roman", size * .62) for _w, _s, n in line if n)) / gaps
        for word, style, note in line:
            c.setFont(FONTS[style], size)
            c.drawString(x, y, word)
            x += self.width(word, style, size)
            if note:
                c.setFont("Times-Roman", size * 0.62)
                c.drawString(x, y + size * 0.38, note)
                x += stringWidth(note, "Times-Roman", size * 0.62)
            x += space

    def paragraph(self, text, *, indent=True, quote=False, notes=None, center=False):
        size = BODY - 1 if quote else BODY
        lead = LEAD - 1 if quote else LEAD
        left, right = self.margins()
        inset = 20 if quote else 0
        words = tokens(text)
        lines = self.wrap(words, right - left - 2 * inset, size, INDENT if indent and not quote else 0)
        for k, line in enumerate(lines):
            line_notes = [n for _w, _s, n in line if n]
            extra = sum(len(self.wrap(tokens(notes[n]), right - left - 8, NOTE)) for n in line_notes) * NOTE_LEAD
            if self.space_left() - extra - (12 if line_notes and not self.notes else 0) < lead:
                self.new_page()
                left, right = self.margins()
            for n in line_notes:
                self.notes.append((n, notes[n], len(self.wrap(tokens(notes[n]), right - left - 8, NOTE))))
            self.y -= lead
            x = left + inset + (INDENT if k == 0 and indent and not quote else 0)
            width = right - left - 2 * inset - (INDENT if k == 0 and indent and not quote else 0)
            if center:
                natural = sum(self.width(w, s, size) for w, s, _n in line) + stringWidth(" ", "Times-Roman", size) * (len(line) - 1)
                self.draw_words(line, (left + right - natural) / 2, self.y, size, width, justify=False)
            else:
                self.draw_words(line, x, self.y, size, width, justify=k < len(lines) - 1)
        if quote:
            self.y -= 6

    # -- bloques -----------------------------------------------------------------

    def cover(self, color=(40, 70, 110)):
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (600, 930), color)
        ImageDraw.Draw(img).rectangle([60, 120, 540, 400], outline=(240, 220, 180), width=8)
        data = io.BytesIO()
        img.save(data, "JPEG", quality=85)
        self.new_page(bare=True)
        self.c.drawImage(ImageReader(io.BytesIO(data.getvalue())), 0, 0, W, H)

    def title_page(self):
        self.new_page(bare=True)
        self.c.setFont("Times-Bold", 20)
        self.c.drawCentredString(W / 2, H - 170, self.title)
        self.c.setFont("Times-Roman", 13)
        self.c.drawCentredString(W / 2, H - 200, self.author)

    def copyright(self, lines):
        self.new_page(bare=True)
        self.c.setFont("Times-Roman", 7.5)
        y = 120
        for line in lines:
            self.c.drawString(OUTER, y, line)
            y -= 10

    def toc(self, entries):
        self.new_page(chapter_start=True)
        self.c.setFont("Times-Bold", 15)
        self.c.drawCentredString(W / 2, H - 120, "Índice")
        y = H - 160
        left, right = self.margins()
        for text, page in entries:
            self.c.setFont("Times-Roman", BODY)
            self.c.drawString(left, y, text)
            self.c.drawRightString(right, y, str(page))
            y -= LEAD + 2

    def chapter_title(self, title):
        self.chapter = title
        self.new_page(chapter_start=True)
        self.c.setFont("Times-Bold", 16)
        self.c.drawCentredString(W / 2, H - 120, title)
        self.y = H - 150

    def subheading(self, text):
        if self.space_left() < LEAD * 4:
            self.new_page()
        self.y -= 14
        left, _right = self.margins()
        self.c.setFont("Times-Bold", 11.5)
        self.c.drawString(left, self.y - 4, text)
        self.y -= 14

    def scene_break(self):
        self.y -= 8
        self.paragraph("* * *", indent=False, center=True)
        self.y -= 8

    def save(self, path):
        if self.page_open:
            self.finish_page()
        self.c.save()
        with open(path, "wb") as f:
            f.write(self.buf.getvalue())


SENTENCES = [
    "La niebla bajaba desde las colinas y cubría los tejados de la ciudad con una paciencia antigua.",
    "Nadie recordaba cuándo había empezado la costumbre de encender faroles al mediodía.",
    "El cartógrafo anotaba cada calle con una minuciosidad que sus vecinos consideraban extravagante.",
    "Decía que una ciudad sin mapa termina por olvidarse de sí misma, como un viajero sin memoria.",
    "Por las tardes caminaba hasta el puerto y contaba las barcas que volvían vacías.",
    "Su hermana le escribía cartas larguísimas que él contestaba con dibujos de puentes imposibles.",
]


def sample_text(seed: int, n: int) -> str:
    return " ".join(SENTENCES[(seed + k) % len(SENTENCES)] for k in range(n))


def make_book_pdf(path) -> dict:
    """Crea el PDF de prueba y devuelve lo que el conversor debería encontrar."""
    book = BookPDF("La ciudad de los mapas", "Autora Inventada")
    book.cover()
    book.title_page()
    book.copyright(["© Autora Inventada, 2026", "Texto de prueba creado para las pruebas automáticas.", "ISBN 000-0-00-000000-0"])
    book.toc([("Primera parte: la niebla", 5), ("Segunda parte: el puerto", 8)])
    notes1 = {"1": "Nota inventada que explica una costumbre local de los faroleros.",
              "2": "Otra aclaración breve del narrador."}
    book.chapter_title("Primera parte: la niebla")
    book.paragraph("Era una mañana *extraordinariamente* gris cuando llegó la carta^1 al despacho del cartógrafo. " + sample_text(0, 5), indent=False, notes=notes1)
    book.paragraph(sample_text(2, 7) + " El sobre traía un sello desconocido^2 y una dirección escrita con tinta violeta.", notes=notes1)
    book.paragraph("Quien dibuja una ciudad la inventa un poco; quien la recorre, la corrige cada día. Ese era el lema del oficio.", quote=True)
    book.paragraph(sample_text(4, 9))
    book.subheading("El despacho")
    book.paragraph(sample_text(1, 6), indent=False)
    book.scene_break()
    book.paragraph(sample_text(3, 10), indent=False)
    book.chapter_title("Segunda parte: el puerto")
    book.paragraph(sample_text(5, 8), indent=False)
    book.paragraph("Las palabras **barca** y *faro* aparecían en todas sus notas. " + sample_text(0, 6))
    book.save(path)
    return {
        "titulo": "La ciudad de los mapas",
        "autor": "Autora Inventada",
        "capitulos": ["Primera parte: la niebla", "Segunda parte: el puerto"],
        "notas": 2,
        "paginas": book.page,
    }
