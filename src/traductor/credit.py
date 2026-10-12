"""Firma de Jerónimo en los libros exportados: una página final con el logo,
«Traducido con Jerónimo» y el enlace del proyecto, en el idioma del libro
traducido (no en el de la interfaz). Se puede quitar desde «Exportar»."""

from __future__ import annotations

import html
from pathlib import Path

URL = "https://github.com/Willyssj3/Jeronimo"
URL_TEXT = "github.com/Willyssj3/Jeronimo"
LOGO = Path(__file__).parent / "assets" / "logo-firma.png"

# Título y descripción por idioma del libro. Si el idioma no está, en inglés.
TEXTS = {
    "es": ("Traducido con Jerónimo",
           "Programa libre y gratuito que traduce libros con inteligencia artificial imitando la voz de su "
           "traductor oficial."),
    "en": ("Translated with Jerónimo",
           "A free, open-source program that translates books with artificial intelligence in the voice of "
           "their official translator."),
    "pt": ("Traduzido com o Jerónimo",
           "Programa livre e gratuito que traduz livros com inteligência artificial imitando a voz do tradutor "
           "oficial."),
    "fr": ("Traduit avec Jerónimo",
           "Logiciel libre et gratuit qui traduit des livres avec l’intelligence artificielle en imitant la voix "
           "de leur traducteur officiel."),
    "it": ("Tradotto con Jerónimo",
           "Programma libero e gratuito che traduce libri con l’intelligenza artificiale imitando la voce del "
           "traduttore ufficiale."),
    "de": ("Übersetzt mit Jerónimo",
           "Ein freies, kostenloses Programm, das Bücher mit künstlicher Intelligenz in der Stimme ihres "
           "offiziellen Übersetzers überträgt."),
    "ca": ("Traduït amb Jerónimo",
           "Programa lliure i gratuït que tradueix llibres amb intel·ligència artificial imitant la veu del seu "
           "traductor oficial."),
    "gl": ("Traducido con Jerónimo",
           "Programa libre e gratuíto que traduce libros con intelixencia artificial imitando a voz do seu "
           "tradutor oficial."),
    "nl": ("Vertaald met Jerónimo",
           "Een vrij en gratis programma dat boeken met kunstmatige intelligentie vertaalt in de stem van hun "
           "officiële vertaler."),
}

STYLE = """body { margin: 0; padding: 0; text-align: center; }
div.jeronimo { margin: 28% 8% 0 8%; }
div.jeronimo img { width: 20%; max-width: 140px; height: auto; }
div.jeronimo p { margin: 0; text-indent: 0; text-align: center; }
div.jeronimo p.titulo { margin-top: 1.2em; font-size: 1.15em; font-weight: bold; }
div.jeronimo p.desc { margin-top: .6em; font-size: .85em; }
div.jeronimo p.enlace { margin-top: .8em; font-size: .8em; }"""


def texts(lang: str) -> tuple[str, str]:
    """(«Traducido con Jerónimo», descripción) en el idioma del libro."""
    return TEXTS.get((lang or "").split("-")[0].lower(), TEXTS["en"])


def logo_png() -> bytes:
    return LOGO.read_bytes()


def xhtml(lang: str, *, epub3: bool, logo_href: str = "jeronimo-logo.png") -> bytes:
    """La página final del EPUB (XHTML válido para EPUB 2 y 3)."""
    title, desc = (html.escape(t) for t in texts(lang))
    doctype = ("<!DOCTYPE html>" if epub3 else '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
               '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">')
    lang_attr = html.escape(lang or "en", quote=True)
    page = f"""<?xml version="1.0" encoding="utf-8"?>
{doctype}
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang_attr}"{f' lang="{lang_attr}"' if epub3 else ''}>
<head>
<title>{title}</title>
<style type="text/css">
{STYLE}
</style>
</head>
<body>
<div class="jeronimo">
<p><img src="{html.escape(logo_href, quote=True)}" alt="Jerónimo"/></p>
<p class="titulo">{title}</p>
<p class="desc">{desc}</p>
<p class="enlace"><a href="{URL}">{URL_TEXT}</a></p>
</div>
</body>
</html>
"""
    return page.encode("utf-8")
