"""Conversión entre el contenido de un bloque XHTML y "texto marcado".

La IA nunca ve el HTML real. Cada párrafo se convierte en texto plano donde el
formato en línea se representa con marcas numeradas:

* ``<g1>…</g1>``: formato cuyo texto hay que traducir (cursiva, negrita,
  enlaces, versalitas…). La IA puede moverlo dentro de la frase.
* ``<x2/>``: elemento fijo que se copia tal cual (imágenes, saltos de línea,
  llamadas a notas al pie, anclas vacías…).

Al volver, las marcas se sustituyen por los elementos originales, así el EPUB
traducido conserva el formato, los enlaces y las notas.
"""

from __future__ import annotations

import copy
import html
import re
from dataclasses import dataclass, field

from lxml import etree

# Elementos que se copian siempre tal cual, sin traducir su contenido.
ATOMIC_TAGS = {
    "img", "br", "hr", "wbr", "svg", "math", "object", "embed", "input",
    "image", "audio", "video", "iframe", "canvas", "source", "track",
}

LETTER_RE = re.compile(r"[^\W\d_]")
# Solo espacios ASCII: el espacio duro (U+00A0) tiene valor tipográfico.
WS_RE = re.compile(r"[ \t\n\r\f]+")
TOKEN_RE = re.compile(r"<(/?)g(\d+)\s*>|<x(\d+)\s*/?\s*>|</x\d+\s*>")


def localname(el) -> str:
    """Nombre de etiqueta sin espacio de nombres ('' para comentarios)."""
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return etree.QName(tag).localname.lower()


def has_letters(text: str) -> bool:
    return bool(LETTER_RE.search(text or ""))


def element_text(el) -> str:
    """Texto visible de un elemento, con los espacios normalizados."""
    # itertext() ya omite el texto de comentarios e instrucciones.
    return WS_RE.sub(" ", "".join(el.itertext())).strip(" ")


@dataclass
class Marked:
    """Texto marcado de un bloque y los elementos que representan sus marcas."""

    text: str
    # id -> ("g" | "x", elemento original)
    slots: dict[int, tuple[str, etree._Element]] = field(default_factory=dict)


def _is_atomic(el) -> bool:
    if localname(el) in ATOMIC_TAGS:
        return True
    # Sin letras (anclas vacías, "1" de una nota, "*"): se conserva tal cual.
    return not has_letters("".join(el.itertext()))


def to_marked(block) -> Marked:
    """Convierte el contenido de ``block`` en texto marcado."""
    slots: dict[int, tuple[str, etree._Element]] = {}
    parts: list[str] = []
    counter = 0

    def esc(t: str) -> str:
        return html.escape(t, quote=False)

    def walk(el) -> None:
        nonlocal counter
        for child in el:
            if not isinstance(child.tag, str):  # comentario / instrucción
                if child.tail:
                    parts.append(esc(child.tail))
                continue
            counter += 1
            n = counter
            if _is_atomic(child):
                slots[n] = ("x", child)
                parts.append(f"<x{n}/>")
            else:
                slots[n] = ("g", child)
                parts.append(f"<g{n}>")
                if child.text:
                    parts.append(esc(child.text))
                walk(child)
                parts.append(f"</g{n}>")
            if child.tail:
                parts.append(esc(child.tail))

    if block.text:
        parts.append(esc(block.text))
    walk(block)
    text = WS_RE.sub(" ", "".join(parts)).strip(" ")
    return Marked(text=text, slots=slots)


def strip_marks(marked_text: str) -> str:
    """Texto plano (sin marcas) de un texto marcado."""
    return html.unescape(TOKEN_RE.sub("", marked_text))


def _tokens(marked_text: str):
    for m in TOKEN_RE.finditer(marked_text):
        if m.group(2):
            yield m, ("close" if m.group(1) else "open"), int(m.group(2))
        elif m.group(3):
            yield m, "atom", int(m.group(3))
        else:
            yield m, "stray", 0


def problems(marked_text: str, slots: dict) -> list[str]:
    """Lista de problemas de marcas (vacía si el texto es válido)."""
    found: list[str] = []
    opens: dict[int, int] = {}
    atoms: dict[int, int] = {}
    stack: list[int] = []
    for _m, kind, n in _tokens(marked_text):
        if kind == "stray":
            found.append("marca de cierre suelta")
            continue
        expected = "x" if kind == "atom" else "g"
        if n not in slots or slots[n][0] != expected:
            found.append(f"marca desconocida {n}")
            continue
        if kind == "open":
            opens[n] = opens.get(n, 0) + 1
            stack.append(n)
        elif kind == "close":
            if not stack or stack[-1] != n:
                found.append(f"marca {n} mal anidada")
            else:
                stack.pop()
        else:
            atoms[n] = atoms.get(n, 0) + 1
    if stack:
        found.append("marcas sin cerrar")
    for n, (kind, _el) in slots.items():
        count = (opens if kind == "g" else atoms).get(n, 0)
        if count == 0:
            found.append(f"falta la marca {n}")
        elif count > 1:
            found.append(f"marca {n} repetida")
    return found


def repair(marked_text: str, slots: dict) -> tuple[str, list[str]]:
    """Corrige las marcas de una traducción para que siempre se pueda
    reconstruir el HTML. Devuelve el texto corregido y avisos para revisión.

    * Marcas desconocidas o sueltas: se eliminan.
    * Formato (g) mal anidado o repetido: se quita ese formato, se conserva el texto.
    * Elementos fijos (x) repetidos: se deja solo el primero.
    * Elementos fijos que faltan: se añaden al final del párrafo.
    """
    warnings: list[str] = []
    text = marked_text

    for _ in range(5):
        bad_pairs: set[int] = set()
        unknown = False
        opens: dict[int, int] = {}
        closes: dict[int, int] = {}
        stack: list[int] = []
        for _m, kind, n in _tokens(text):
            if kind == "stray":
                unknown = True
                continue
            expected = "x" if kind == "atom" else "g"
            if n not in slots or slots[n][0] != expected:
                unknown = True
                continue
            if kind == "open":
                opens[n] = opens.get(n, 0) + 1
                stack.append(n)
            elif kind == "close":
                closes[n] = closes.get(n, 0) + 1
                if stack and stack[-1] == n:
                    stack.pop()
                else:
                    bad_pairs.add(n)
                    if n in stack:
                        # Cierra lo que quedó abierto en medio: también es inválido.
                        idx = len(stack) - 1 - stack[::-1].index(n)
                        bad_pairs.update(stack[idx:])
                        del stack[idx:]
        bad_pairs.update(stack)
        for n in set(opens) | set(closes):
            if opens.get(n, 0) != 1 or closes.get(n, 0) != 1:
                bad_pairs.add(n)
        if not bad_pairs and not unknown:
            break

        def fix(m: re.Match) -> str:
            if m.group(2):
                n = int(m.group(2))
                if n in bad_pairs or n not in slots or slots[n][0] != "g":
                    return ""
                return m.group(0)
            if m.group(3):
                n = int(m.group(3))
                if n not in slots or slots[n][0] != "x":
                    return ""
                return m.group(0)
            return ""

        if bad_pairs:
            warnings.append("se perdió parte del formato (cursiva, negrita o enlace)")
        if unknown:
            warnings.append("se eliminaron marcas desconocidas")
        text = TOKEN_RE.sub(fix, text)

    # Elementos fijos: exactamente una vez cada uno.
    seen: set[int] = set()

    def dedupe(m: re.Match) -> str:
        if m.group(3):
            n = int(m.group(3))
            if n in seen:
                return ""
            seen.add(n)
        return m.group(0)

    before = text
    text = TOKEN_RE.sub(dedupe, text)
    if text != before:
        warnings.append("había elementos fijos repetidos")
    missing = [n for n, (kind, _el) in sorted(slots.items()) if kind == "x" and n not in seen]
    if missing:
        text = text.rstrip(" ") + "".join(f"<x{n}/>" for n in missing)
        warnings.append("se recolocó al final un elemento fijo (imagen, nota o salto de línea)")
    if any(kind == "g" for kind, _el in slots.values()):
        present = {n for _m, kind, n in _tokens(text) if kind == "open"}
        if any(kind == "g" and n not in present for n, (kind, _el) in slots.items()):
            if "se perdió parte del formato (cursiva, negrita o enlace)" not in warnings:
                warnings.append("se perdió parte del formato (cursiva, negrita o enlace)")
    return text, warnings


def apply_marked(target, marked_text: str, slots: dict, *, strip_ids: bool = False) -> None:
    """Reemplaza el contenido de ``target`` por el texto marcado ``marked_text``.

    El texto debe estar validado (usar :func:`repair` antes). Con
    ``strip_ids`` se quitan los atributos ``id`` copiados, para no duplicar
    identificadores cuando el original sigue en el documento (modo bilingüe).
    """
    for child in list(target):
        target.remove(child)
    target.text = None
    stack = [target]

    def add_text(raw: str) -> None:
        if not raw:
            return
        t = html.unescape(raw)
        parent = stack[-1]
        if len(parent):
            last = parent[-1]
            last.tail = (last.tail or "") + t
        else:
            parent.text = (parent.text or "") + t

    pos = 0
    for m, kind, n in _tokens(marked_text):
        add_text(marked_text[pos:m.start()])
        pos = m.end()
        if kind == "open":
            src = slots[n][1]
            el = target.makeelement(src.tag, dict(src.attrib))
            stack[-1].append(el)
            stack.append(el)
        elif kind == "close":
            if len(stack) > 1:
                stack.pop()
        elif kind == "atom":
            el = copy.deepcopy(slots[n][1])
            el.tail = None
            stack[-1].append(el)
    add_text(marked_text[pos:])

    if strip_ids:
        for el in target.iterdescendants():
            if isinstance(el.tag, str) and "id" in el.attrib:
                del el.attrib["id"]
