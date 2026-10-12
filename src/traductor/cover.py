"""Portada traducida: una portada nueva con el título y el autor de la edición
traducida, en tipografía de libro (EB Garamond).

Dos estilos: «difuminada» (la portada original muy desenfocada y oscurecida,
de fondo, para conservar sus colores sin que se lea el título original) y
«lisa» (fondo azul tinta con filetes dorados)."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FONTS = Path(__file__).parent / "assets" / "fonts"
WIDTH = 1600
INK = (20, 42, 67)
INK_2 = (29, 58, 92)
GOLD = (216, 171, 76)
CREAM = (243, 233, 214)
STYLES = ("difuminada", "lisa")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"EBGaramond-{name}.ttf"), size)


@lru_cache(maxsize=1)
def _covered() -> frozenset[int]:
    from reportlab.pdfbase.ttfonts import TTFontFile

    return frozenset(TTFontFile(str(FONTS / "EBGaramond-SemiBold.ttf")).charToGlyph)


def unsupported_chars(text: str) -> set[str]:
    """Caracteres que la tipografía de la portada no tiene (p. ej. chino o japonés)."""
    covered = _covered()
    return {ch for ch in text if not ch.isspace() and ord(ch) not in covered}


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _fit_title(draw, text: str, width: int, max_lines: int = 4) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    size = 160
    while True:
        font = _font("SemiBold", size)
        lines = _wrap(draw, text, font, width)
        widest = max((draw.textlength(line, font=font) for line in lines), default=0)
        if (len(lines) <= max_lines and widest <= width) or size <= 64:
            return font, lines
        size = int(size * 0.92)


def _spaced(draw, xy_center: tuple[int, int], text: str, font, fill, spacing: float) -> None:
    """Texto con espaciado entre letras, centrado en ``xy_center``."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = xy_center[0] - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, xy_center[1]), ch, font=font, fill=fill, anchor="lm")
        x += w + spacing


def _ornament(draw, cx: int, y: int, half: int, color) -> None:
    draw.line([(cx - half, y), (cx - 22, y)], fill=color, width=3)
    draw.line([(cx + 22, y), (cx + half, y)], fill=color, width=3)
    draw.polygon([(cx, y - 11), (cx + 11, y), (cx, y + 11), (cx - 11, y)], fill=color)


def _gradient(size, top, bottom) -> Image.Image:
    w, h = size
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return grad.resize(size)


def _background(style: str, original: bytes | None, size: tuple[int, int]) -> Image.Image:
    if style == "difuminada" and original:
        try:
            img = Image.open(io.BytesIO(original)).convert("RGB")
        except Exception:  # imagen dañada o formato raro: fondo liso
            img = None
        if img is not None:
            img = ImageOps.fit(img, size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(42))
            shade = _gradient(size, (12, 24, 40), (8, 16, 28))
            return Image.blend(img, shade, 0.58)
    base = _gradient(size, INK_2, INK)
    draw = ImageDraw.Draw(base)
    m = 70
    draw.rectangle([m, m, size[0] - m, size[1] - m], outline=GOLD, width=4)
    draw.rectangle([m + 18, m + 18, size[0] - m - 18, size[1] - m - 18], outline=GOLD, width=1)
    return base


def make_cover(title: str, author: str = "", *, style: str = "difuminada", original: bytes | None = None,
               fmt: str = "JPEG", aspect: float = 1.5, size: tuple[int, int] | None = None) -> bytes:
    """Imagen de portada (JPEG o PNG) con el título y el autor.

    ``aspect`` es la proporción alto/ancho (la de la portada original, para que
    los lectores no la deformen); ``size`` reduce el resultado (vista previa)."""
    title = " ".join((title or "").split()) or "Sin título"
    author = " ".join((author or "").split())
    canvas = (WIDTH, round(WIDTH * min(1.8, max(1.2, aspect))))
    h = canvas[1]
    img = _background(style if style in STYLES else "difuminada", original, canvas)
    draw = ImageDraw.Draw(img)
    cx = WIDTH // 2
    width = WIDTH - 2 * 220

    if author:
        font = _font("Regular", 58)
        text = author.upper()
        while draw.textlength(text, font=font) + 0.16 * 58 * len(text) > width and font.size > 34:
            font = _font("Regular", font.size - 4)
        _spaced(draw, (cx, round(h * 0.196)), text, font, GOLD, spacing=font.size * 0.16)
    _ornament(draw, cx, round(h * 0.246), 190, GOLD)

    font, lines = _fit_title(draw, title, width)
    line_h = int(font.size * 1.13)
    top = round(h * 0.517) - (line_h * len(lines)) // 2
    for k, line in enumerate(lines):
        y = top + k * line_h + line_h // 2
        draw.text((cx + 3, y + 4), line, font=font, fill=(0, 0, 0), anchor="mm")  # sombra suave
        draw.text((cx, y), line, font=font, fill=CREAM, anchor="mm")
    _ornament(draw, cx, top + line_h * len(lines) + 90, 120, GOLD)

    if size:
        img = img.resize(size, Image.LANCZOS)
    out = io.BytesIO()
    if fmt.upper() == "PNG":
        img.save(out, "PNG", optimize=True)
    else:
        img.save(out, "JPEG", quality=90, optimize=True, progressive=True)
    return out.getvalue()
