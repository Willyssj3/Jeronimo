"""OCR para PDF escaneados: reconoce el texto de cada página y lo entrega al
conversor de PDF como renglones, para reconstruir párrafos y capítulos igual
que con un PDF con texto.

Motores, en este orden:
- el OCR de Windows (Windows 10 y 11), con los idiomas instalados en el equipo;
- Tesseract (gratuito), si está instalado.

Las cursivas y negritas no se reconocen; las notas al pie quedan al final."""

from __future__ import annotations

import shutil
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from .i18n import N_, tr

DPI = 300
BODY_PT = 11.0
TESSERACT_LANGS = {"en": "eng", "es": "spa", "pt": "por", "fr": "fra", "it": "ita", "de": "deu", "ca": "cat",
                   "gl": "glg", "eu": "eus", "nl": "nld", "ru": "rus", "ja": "jpn", "zh": "chi_sim", "ko": "kor"}
NO_ENGINE = (N_("Este PDF está escaneado (sus páginas son imágenes). Para reconocer el texto hace falta un OCR: "
             "en Windows 10 y 11, Jerónimo usa el de Windows con el idioma del libro instalado (Configuración → "
             "Hora e idioma → Idioma y región → Agregar un idioma). También puedes instalar Tesseract, gratuito."))


@dataclass
class OcrWord:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass
class OcrLine:
    words: list[OcrWord] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words if w.text.strip())

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (min(w.x0 for w in self.words), min(w.top for w in self.words),
                max(w.x1 for w in self.words), max(w.bottom for w in self.words))


# -- Tesseract -----------------------------------------------------------------------------


def _no_window() -> dict:
    return {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}


class TesseractEngine:
    name = "Tesseract"

    def __init__(self, exe: str):
        self.exe = exe
        self._langs: set[str] | None = None

    @staticmethod
    def find() -> str | None:
        exe = shutil.which("tesseract")
        if exe:
            return exe
        for base in (r"C:\Program Files\Tesseract-OCR", r"C:\Program Files (x86)\Tesseract-OCR"):
            candidate = Path(base) / "tesseract.exe"
            if candidate.is_file():
                return str(candidate)
        return None

    def languages(self) -> set[str]:
        if self._langs is None:
            try:
                out = subprocess.run([self.exe, "--list-langs"], capture_output=True, text=True, timeout=30,
                                     **_no_window())
                self._langs = {line.strip() for line in (out.stdout + out.stderr).splitlines()[1:] if line.strip()}
            except (OSError, subprocess.TimeoutExpired):
                self._langs = set()
        return self._langs

    def supports(self, lang: str) -> bool:
        return TESSERACT_LANGS.get(lang, "") in self.languages()

    def lang_arg(self, lang: str) -> str:
        have = self.languages()
        wanted = [TESSERACT_LANGS[lang]] if lang in TESSERACT_LANGS else ["eng", "spa"]
        chosen = [code for code in wanted if code in have] or [c for c in ("eng",) if c in have]
        if not chosen:
            raise ValueError(tr("Tesseract no tiene instalado ningún idioma utilizable."))
        return "+".join(chosen)

    def recognize(self, image, lang: str) -> list[OcrLine]:
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "pagina.png"
            image.save(png)
            done = subprocess.run([self.exe, str(png), "stdout", "-l", self.lang_arg(lang), "--psm", "3", "tsv"],
                                  capture_output=True, timeout=600, **_no_window())
        if done.returncode != 0:
            raise RuntimeError((done.stderr or b"").decode("utf-8", "replace").strip()[:300])
        return parse_tsv(done.stdout.decode("utf-8", "replace"))


def parse_tsv(tsv: str) -> list[OcrLine]:
    lines: dict[tuple, OcrLine] = {}
    order: list[tuple] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        if not text or cols[10].strip() == "-1":
            continue
        key = (cols[1], cols[2], cols[3], cols[4])
        if key not in lines:
            lines[key] = OcrLine()
            order.append(key)
        left, top, width, height = (float(v) for v in cols[6:10])
        lines[key].words.append(OcrWord(text, left, top, left + width, top + height))
    return [lines[k] for k in order]


# -- OCR de Windows ------------------------------------------------------------------------


class WindowsEngine:
    name = N_("OCR de Windows")

    @staticmethod
    def available() -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            from winrt.windows.media.ocr import OcrEngine

            return len(list(OcrEngine.available_recognizer_languages)) > 0
        except Exception:
            return False

    @staticmethod
    def languages() -> list[str]:
        from winrt.windows.media.ocr import OcrEngine

        return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]

    def supports(self, lang: str) -> bool:
        return any(tag.lower().split("-")[0] == lang for tag in self.languages())

    def _engine(self, lang: str):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        for tag in self.languages():
            if tag.lower().split("-")[0] == lang:
                engine = OcrEngine.try_create_from_language(Language(tag))
                if engine is not None:
                    return engine
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError(tr("Windows no tiene idiomas de OCR instalados."))
        return engine

    def recognize(self, image, lang: str) -> list[OcrLine]:
        import asyncio
        import io

        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        buf = io.BytesIO()
        image.convert("RGB").save(buf, "PNG")
        engine = self._engine(lang)

        async def run():
            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream)
            writer.write_bytes(buf.getvalue())
            await writer.store_async()
            await writer.flush_async()
            writer.detach_stream()
            stream.seek(0)
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            return await engine.recognize_async(bitmap)

        result = asyncio.run(run())
        lines = []
        for line in result.lines:
            words = []
            for w in line.words:
                r = w.bounding_rect
                words.append(OcrWord(w.text, r.x, r.y, r.x + r.width, r.y + r.height))
            if words:
                lines.append(OcrLine(words))
        return lines


def pick_engine(lang: str = ""):
    """El mejor motor de OCR disponible para ese idioma (o error con instrucciones)."""
    windows = WindowsEngine() if WindowsEngine.available() else None
    exe = TesseractEngine.find()
    tesseract = TesseractEngine(exe) if exe else None
    if tesseract is not None and not tesseract.languages():
        tesseract = None
    if lang and windows and windows.supports(lang):
        return windows
    if lang and tesseract and tesseract.supports(lang):
        return tesseract
    if windows:
        return windows
    if tesseract:
        return tesseract
    from .pdf2epub import PdfError

    raise PdfError(tr(NO_ENGINE))


# -- de las páginas reconocidas al modelo del conversor de PDF ------------------------------


def to_pages(scans: list[tuple[int, float, float, float, list[OcrLine]]]):
    """Convierte (número, ancho_pt, alto_pt, escala, renglones) en páginas del conversor.

    El OCR no da tamaños de letra: los renglones de altura normal cuentan como
    cuerpo de texto y los demás, en proporción (títulos más altos, notas más bajas)."""
    from .pdf2epub import Line, Page, Run

    heights = [ln.box[3] - ln.box[1] for *_rest, lines in scans for ln in lines if len(ln.text) >= 25]
    typical = statistics.median(heights) if heights else 0
    pages = []
    for number, width, height, scale, lines in scans:
        out = []
        for ln in lines:
            text = ln.text.strip()
            if not text:
                continue
            x0, top, x1, bottom = (v / scale for v in ln.box)
            ratio = (ln.box[3] - ln.box[1]) / typical if typical else 1.0
            size = BODY_PT if 0.8 <= ratio <= 1.28 else round(BODY_PT * ratio * 2) / 2
            out.append(Line(page=number, x0=x0, x1=x1, top=top, bottom=bottom, size=size, runs=[Run(text)]))
        out.sort(key=lambda l: (round(l.top, 0), l.x0))
        pages.append(Page(number, width, height, out, image_area=1.0))
    return pages


def ocr_pdf(path: Path, lang: str = "", *, progress=None, should_stop=None, engine=None):
    """Reconoce todas las páginas de un PDF escaneado. Devuelve (páginas, nombre del motor)."""
    import pypdfium2 as pdfium

    from .pdf2epub import PdfError

    engine = engine or pick_engine(lang)
    pdf = pdfium.PdfDocument(str(path))
    scans = []
    total = len(pdf)
    scale = DPI / 72
    try:
        for i in range(total):
            if should_stop and should_stop():
                raise PdfError(tr("Conversión detenida."))
            if progress:
                progress(0.05 + 0.65 * i / max(1, total), tr("Reconociendo el texto (OCR): página {i} de {total}…", i=i + 1, total=total))
            page = pdf[i]
            width, height = page.get_size()
            image = page.render(scale=scale, grayscale=True).to_pil()
            try:
                lines = engine.recognize(image, lang)
            except PdfError:
                raise
            except Exception as e:
                raise PdfError(tr("El OCR falló en la página {i}: {error}", i=i + 1, error=e)) from e
            scans.append((i + 1, float(width), float(height), scale, lines))
    finally:
        pdf.close()
    return to_pages(scans), tr(engine.name)
