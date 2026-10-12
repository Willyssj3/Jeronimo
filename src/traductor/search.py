"""Buscar y reemplazar en todo el libro.

Se busca en el texto que se ve (sin las marcas de formato) y se reemplaza
solo dentro de los trozos de texto, así las cursivas, notas e imágenes de
cada párrafo se conservan. El último reemplazo se puede deshacer."""

from __future__ import annotations

import html
import re
from datetime import datetime

from .inline import TOKEN_RE, strip_marks
from .project import read_json, write_json

MAX_RESULTS = 500
CONTEXT = 70


def pattern(query: str, *, case: bool = False, whole_word: bool = False) -> re.Pattern:
    query = (query or "").strip()
    if not query:
        raise ValueError("Escribe qué quieres buscar.")
    body = re.escape(query)
    if whole_word:
        body = r"(?<!\w)" + body + r"(?!\w)"
    return re.compile(body, 0 if case else re.IGNORECASE)


def match_case(found: str, replacement: str) -> str:
    """Al reemplazar sin distinguir mayúsculas, respeta las del texto encontrado:
    «Farero» → «Guardián», «FARERO» → «GUARDIÁN»."""
    if not replacement:
        return replacement
    letters = [c for c in found if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return replacement.upper()
    if found[:1].isupper() and replacement[:1].islower():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def replace_marked(marked: str, regex: re.Pattern, replacement: str, *, keep_case: bool) -> tuple[str, int]:
    """Reemplaza en los trozos de texto de un texto marcado, sin tocar las marcas."""
    total = 0

    def fix(text: str) -> str:
        nonlocal total
        plain = html.unescape(text)
        new, count = regex.subn(lambda m: match_case(m.group(0), replacement) if keep_case else replacement, plain)
        if not count:
            return text
        total += count
        return html.escape(new, quote=False)

    out, last = [], 0
    for m in TOKEN_RE.finditer(marked):
        out.append(fix(marked[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fix(marked[last:]))
    return "".join(out), total


def _snippet(text: str, start: int, end: int) -> dict:
    before = text[max(0, start - CONTEXT):start]
    after = text[end:end + CONTEXT]
    if start > CONTEXT:
        before = "…" + before.split(" ", 1)[-1] if " " in before else "…" + before
    if end + CONTEXT < len(text):
        after = after.rsplit(" ", 1)[0] + "…" if " " in after else after + "…"
    return {"antes": before, "texto": text[start:end], "despues": after}


def search(project, query: str, *, case: bool = False, whole_word: bool = False, field: str = "tgt") -> dict:
    """Coincidencias en la traducción (``tgt``) o en el original (``src``)."""
    regex = pattern(query, case=case, whole_word=whole_word)
    results, matches, truncated = [], 0, False
    for index in project.chapter_indices():
        data = project.load_chapter(index)
        if data.get("omitir") and field == "src":
            continue
        for seg in data["segmentos"]:
            value = seg.get(field)
            if not value:
                continue
            text = strip_marks(value)
            found = list(regex.finditer(text))
            if not found:
                continue
            matches += len(found)
            if len(results) >= MAX_RESULTS:
                truncated = True
                continue
            row = {"doc": index, "titulo": data.get("titulo") or f"Documento {index}", "n": seg["n"],
                   "coincidencias": len(found), "fragmento": _snippet(text, found[0].start(), found[0].end())}
            if field == "src":
                other = strip_marks(seg.get("tgt") or "")
                row["traduccion"] = other if len(other) <= 220 else other[:217].rsplit(" ", 1)[0] + "…"
            results.append(row)
    return {"resultados": results, "coincidencias": matches, "parrafos": len(results), "truncado": truncated,
            "deshacer": last_replacement(project)}


def _undo_path(project):
    return project.knowledge_dir / "ultimo_reemplazo.json"


def replace(project, query: str, replacement: str, *, case: bool = False, whole_word: bool = False,
            only: list | None = None, busy: set[int] | None = None) -> dict:
    """Reemplaza en la traducción; ``only`` limita a ciertos párrafos [[doc, n], …]."""
    regex = pattern(query, case=case, whole_word=whole_word)
    allowed = {(int(d), int(n)) for d, n in only} if only is not None else None
    busy = busy or set()
    changes, total = [], 0
    for index in project.chapter_indices():
        if allowed is not None and not any(d == index for d, _n in allowed):
            continue
        if index in busy:
            raise ValueError("Hay un capítulo traduciéndose ahora mismo. Espera a que termine para reemplazar.")
        data = project.load_chapter(index)
        touched = False
        for seg in data["segmentos"]:
            if not seg.get("tgt") or (allowed is not None and (index, seg["n"]) not in allowed):
                continue
            new, count = replace_marked(seg["tgt"], regex, replacement, keep_case=not case)
            if count:
                changes.append({"doc": index, "n": seg["n"], "antes": seg["tgt"], "despues": new})
                seg["tgt"] = new
                seg["editado"] = True
                total += count
                touched = True
        if touched:
            project.save_chapter(data)
    if changes:
        write_json(_undo_path(project), {"fecha": datetime.now().isoformat(timespec="seconds"),
                                          "buscar": query.strip(), "reemplazar": replacement, "cambios": changes})
    return {"reemplazos": total, "parrafos": len(changes), "deshacer": last_replacement(project)}


def last_replacement(project) -> dict | None:
    data = read_json(_undo_path(project))
    if not data or not data.get("cambios"):
        return None
    return {"buscar": data["buscar"], "reemplazar": data["reemplazar"], "parrafos": len(data["cambios"]),
            "fecha": data.get("fecha", "")}


def undo_replacement(project) -> dict:
    """Deshace el último reemplazo en los párrafos que no se tocaron después."""
    data = read_json(_undo_path(project))
    if not data or not data.get("cambios"):
        raise ValueError("No hay ningún reemplazo que deshacer.")
    restored = skipped = 0
    by_doc: dict[int, list[dict]] = {}
    for change in data["cambios"]:
        by_doc.setdefault(change["doc"], []).append(change)
    for index, items in by_doc.items():
        chapter = project.load_chapter(index)
        segs = {s["n"]: s for s in chapter["segmentos"]}
        for change in items:
            seg = segs.get(change["n"])
            if seg is not None and seg.get("tgt") == change["despues"]:
                seg["tgt"] = change["antes"]
                restored += 1
            else:
                skipped += 1
        project.save_chapter(chapter)
    _undo_path(project).unlink()
    return {"restaurados": restored, "omitidos": skipped}
