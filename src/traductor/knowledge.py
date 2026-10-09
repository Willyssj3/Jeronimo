"""Preparación del "conocimiento" antes de traducir:

1. Guía de estilo: la IA estudia pasajes emparejados de las traducciones
   oficiales y describe cómo traduce ese traductor.
2. Glosario oficial: cómo se tradujeron nombres, lugares y términos.
3. Análisis del libro: sinopsis, resumen de cada capítulo, personajes y
   términos nuevos (con una traducción propuesta coherente con lo anterior).

Todo queda en ``conocimiento/`` y el usuario puede revisarlo y editarlo.
"""

from __future__ import annotations

import re
from collections import Counter

from . import prompts
from .inline import strip_marks
from .project import Project
from .providers import Provider

_SENT_SPLIT = re.compile(r"(?<=[.!?…:;\"”’)])\s+")
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ][\w'’\-]*")
_DIALOGUE_RE = re.compile(r"[\"“”«—]")
_POSSESSIVE_RE = re.compile(r"[’']s$")
_CONTRACTION_RE = re.compile(r"[’'](m|ve|d|ll|re|t|s)$|^I[’']", re.IGNORECASE)


def _say(log, msg: str) -> None:
    if log:
        log(msg)


def candidate_terms(texts: list[str], *, min_count: int = 3, limit: int = 150) -> list[tuple[str, int]]:
    """Nombres propios y términos probables: palabras con mayúscula que no
    abren frase, solas o en grupos de hasta tres (``New Avalon``)."""
    from .align import _STOP

    counts: Counter[str] = Counter()
    for text in texts:
        for sent in _SENT_SPLIT.split(text):
            tokens = _TOKEN_RE.findall(sent)
            run: list[str] = []
            for k, tok in enumerate(tokens + ["."]):
                word = _POSSESSIVE_RE.sub("", tok).strip("'’-")
                is_cap = (k > 0 and k < len(tokens) and word[:1].isupper()
                          and word.lower() not in _STOP and not _CONTRACTION_RE.search(word))
                if is_cap:
                    run.append(word)
                    continue
                if run:
                    if len(run) <= 3:
                        counts[" ".join(run)] += 1
                    else:
                        for word in run:
                            counts[word] += 1
                    run = []
    common = [(t, c) for t, c in counts.most_common() if c >= min_count and len(t) > 2]
    return common[:limit]


def _sample_pairs(pairs, budget_chars: int) -> list[tuple[str, str]]:
    """Muestra variada (diálogo y narración, de todo el libro) que cabe en el presupuesto."""
    good = [p for p in pairs if 120 <= len(p.src) <= 900]
    if not good:
        good = list(pairs)
    dialogue = [p for p in good if _DIALOGUE_RE.search(p.src)]
    narration = [p for p in good if not _DIALOGUE_RE.search(p.src)]

    def spread(items, n):
        if n <= 0 or not items:
            return []
        step = max(1, len(items) // n)
        return items[::step][:n]

    approx = max(4, budget_chars // 700)
    chosen = spread(dialogue, approx // 2) + spread(narration, approx - approx // 2)
    out, used = [], 0
    for p in chosen:
        size = len(p.src) + len(p.tgt)
        if used + size > budget_chars:
            continue
        out.append((p.src, p.tgt))
        used += size
    return out


def build_style_guide(project: Project, provider: Provider, *, log=None) -> str:
    pairs = project.reference_pairs(max_cost=5.0)
    if not pairs:
        raise ValueError("Primero añade al menos un libro de referencia (original + traducción oficial).")
    budget = 9000 if provider.local else 60000
    sample = _sample_pairs(pairs, budget)
    _say(log, f"Analizando el estilo del traductor con {len(sample)} pasajes…")
    user = prompts.STYLE_GUIDE_USER.format(
        author=project.config.get("autor") or "this author",
        src=prompts.lang_name(project.source_lang), tgt=prompts.lang_name(project.target_lang),
        passages=prompts.passages_block(sample))
    result = provider.complete(prompts.ANALYST_SYSTEM, user)
    project.record_usage(result.model, result.usage)
    guide = result.text.strip()
    if not guide:
        raise ValueError("La IA no devolvió la guía de estilo. Prueba de nuevo.")
    project.set_style_guide(guide)
    return guide


def build_reference_glossary(project: Project, provider: Provider, *, log=None) -> list[dict]:
    pairs = project.reference_pairs(max_cost=6.0)
    if not pairs:
        raise ValueError("Primero añade al menos un libro de referencia (original + traducción oficial).")
    terms = candidate_terms([p.src for p in pairs], min_count=3, limit=60 if provider.local else 200)
    if not terms:
        return project.glossary()
    batch_size = 8 if provider.local else 30
    found: list[dict] = []
    for start in range(0, len(terms), batch_size):
        batch = terms[start:start + batch_size]
        _say(log, f"Glosario oficial: términos {start + 1}–{start + len(batch)} de {len(terms)}…")
        blocks = []
        for term, _count in batch:
            examples = sorted((p for p in pairs if term in p.src), key=lambda p: len(p.src))[:2]
            blocks.append(f'<term name="{term}">\n{prompts.passages_block([(p.src, p.tgt) for p in examples])}\n</term>')
        user = prompts.GLOSSARY_USER.format(
            author=project.config.get("autor") or "this author",
            src=prompts.lang_name(project.source_lang), tgt=prompts.lang_name(project.target_lang),
            terms="\n".join(blocks))
        result = provider.complete(prompts.ANALYST_SYSTEM, user, max_tokens=8000)
        project.record_usage(result.model, result.usage)
        for obj in prompts.parse_json_lines(result.text):
            term, tr = str(obj.get("term", "")).strip(), str(obj.get("translation", "")).strip()
            if term and tr:
                found.append({"termino": term, "traduccion": tr, "tipo": obj.get("type", ""),
                              "nota": obj.get("note", ""), "origen": "oficial"})
    project.set_glossary(project.glossary() + found)
    return project.glossary()


def _chapter_texts(project: Project) -> list[tuple[int, str, str]]:
    out = []
    for i in project.chapter_indices():
        data = project.load_chapter(i)
        if not data.get("en_spine", True) or not data["segmentos"]:
            continue
        text = "\n".join(strip_marks(s["src"]) for s in data["segmentos"])
        out.append((i, data.get("titulo", ""), text))
    return out


def analyze_book(project: Project, provider: Provider, *, log=None) -> dict:
    budget = 24000 if provider.local else 350000
    chapters = _chapter_texts(project)
    parts: list[str] = []
    current = ""
    for idx, title, text in chapters:
        piece = f"[[CHAPTER {idx}: {title}]]\n{text}\n"
        while len(piece) > budget:  # capítulo enorme: se reparte
            cut = piece.rfind("\n", 0, budget) or budget
            if current:
                parts.append(current)
                current = ""
            parts.append(piece[:cut])
            piece = f"[[CHAPTER {idx}: {title} (cont.)]]\n" + piece[cut:]
        if len(current) + len(piece) > budget and current:
            parts.append(current)
            current = ""
        current += piece
    if current:
        parts.append(current)

    title = project.config.get("titulo") or project.config.get("nombre", "")
    author = project.config.get("autor") or ""
    summaries: list[str] = []
    chapter_summaries: dict[str, str] = {}
    terms: dict[str, dict] = {}
    for k, text in enumerate(parts, 1):
        _say(log, f"Leyendo el libro: parte {k} de {len(parts)}…")
        user = prompts.ANALYZE_USER.format(part=k, parts=len(parts), title=title,
                                           author_part=f" by {author}" if author else "", text=text)
        result = provider.complete(prompts.ANALYST_SYSTEM, user, max_tokens=16000)
        project.record_usage(result.model, result.usage)
        data = prompts.parse_json_object(result.text) or {}
        if data.get("summary"):
            summaries.append(str(data["summary"]))
        for ch in data.get("chapters") or []:
            cid = str(ch.get("id", "")).strip()
            if cid and ch.get("summary"):
                prev = chapter_summaries.get(cid)
                chapter_summaries[cid] = f"{prev} {ch['summary']}" if prev else str(ch["summary"])
        for t in data.get("terms") or []:
            name = str(t.get("term", "")).strip()
            if name and name not in terms:
                terms[name] = {"term": name, "type": t.get("type", ""), "note": t.get("note", "")}

    if len(summaries) > 1:
        _say(log, "Uniendo los resúmenes…")
        user = prompts.SYNOPSIS_MERGE_USER.format(title=title, summaries="\n\n".join(summaries))
        result = provider.complete(prompts.ANALYST_SYSTEM, user, max_tokens=4000)
        project.record_usage(result.model, result.usage)
        synopsis = result.text.strip()
    else:
        synopsis = summaries[0] if summaries else ""

    # Candidatos por frecuencia que la IA no haya listado.
    all_text = [t for _i, _title, t in chapters]
    for term, _count in candidate_terms(all_text, min_count=3, limit=120):
        terms.setdefault(term, {"term": term, "type": "", "note": ""})

    bible = {"sinopsis": synopsis, "capitulos": chapter_summaries, "terminos": list(terms.values())}
    project.set_bible(bible)
    return bible


def propose_terms(project: Project, provider: Provider, *, log=None) -> list[dict]:
    """Decide cómo traducir los términos del libro que no están en el glosario."""
    bible = project.bible()
    glossary = project.glossary()
    known = {e["termino"].lower() for e in glossary}
    pending = [t for t in bible.get("terminos", []) if t["term"].lower() not in known]
    if not pending:
        return glossary
    official = "\n".join(f"- {e['termino']} → {e['traduccion']}" for e in glossary if e.get("origen") != "propuesta")
    batch_size = 10 if provider.local else 60
    found: list[dict] = []
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        _say(log, f"Proponiendo traducciones: términos {start + 1}–{start + len(batch)} de {len(pending)}…")
        listing = "\n".join(
            f"- {t['term']}" + (f" ({t['type']}: {t['note']})" if t.get("note") else "") for t in batch)
        user = prompts.PROPOSE_USER.format(
            title=project.config.get("titulo") or project.config.get("nombre", ""),
            src=prompts.lang_name(project.source_lang), tgt=prompts.lang_name(project.target_lang),
            style_guide=project.style_guide() or "(no style guide)",
            official=official or "(empty)", terms=listing)
        result = provider.complete(prompts.ANALYST_SYSTEM, user, max_tokens=8000)
        project.record_usage(result.model, result.usage)
        types = {t["term"]: t.get("type", "") for t in batch}
        for obj in prompts.parse_json_lines(result.text):
            term, tr = str(obj.get("term", "")).strip(), str(obj.get("translation", "")).strip()
            if term and tr:
                found.append({"termino": term, "traduccion": tr, "tipo": types.get(term, ""),
                              "nota": obj.get("note", ""), "origen": "propuesta"})
    project.set_glossary(glossary + found)
    return project.glossary()


def prepare(project: Project, provider: Provider, *, steps=("estilo", "glosario", "analisis", "terminos"),
            log=None) -> None:
    """Ejecuta los pasos de preparación indicados, en orden."""
    has_refs = bool(project.references())
    if "estilo" in steps and has_refs:
        build_style_guide(project, provider, log=log)
    if "glosario" in steps and has_refs:
        build_reference_glossary(project, provider, log=log)
    if "analisis" in steps:
        analyze_book(project, provider, log=log)
    if "terminos" in steps:
        propose_terms(project, provider, log=log)
