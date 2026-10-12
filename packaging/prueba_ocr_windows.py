"""Prueba del OCR de Windows en la compilación: dibuja un texto inventado y lo
reconoce. Solo informa (no detiene la compilación si el equipo no tiene OCR)."""

import sys

from PIL import Image, ImageDraw, ImageFont

from traductor.cover import FONTS
from traductor.ocr import WindowsEngine

if not WindowsEngine.available():
    print("Este equipo no tiene el OCR de Windows con idiomas instalados.")
    sys.exit(0)
print("Idiomas de OCR:", WindowsEngine.languages())
img = Image.new("RGB", (1800, 500), "white")
draw = ImageDraw.Draw(img)
font = ImageFont.truetype(str(FONTS / "EBGaramond-Regular.ttf"), 72)
draw.text((60, 80), "The lighthouse keeper counted the boats.", font=font, fill="black")
draw.text((60, 260), "Mirela walked to the harbor at dawn.", font=font, fill="black")
lines = WindowsEngine().recognize(img, "en")
text = " | ".join(line.text for line in lines)
print("Reconocido:", text)
if "lighthouse" not in text.lower() or "harbor" not in text.lower():
    sys.exit("El OCR de Windows no reconoció el texto de prueba.")
print("OCR de Windows: correcto.")
