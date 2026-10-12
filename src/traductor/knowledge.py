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
from dataclasses import asdict, dataclass, field

from . import prompts
from .inline import strip_marks
from .project import Project
from .providers import Provider
from .i18n import N_, notes_language, tr

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


# ---------------------------------------------------------------------------
# Trabajos: cada petición a la IA se planifica igual tanto si se envía por API
# como si el usuario la copia y pega en un chat (modo manual).

PROFILES = {
    # Modelo local con poco contexto.
    "local": {"style": 9000, "gloss_batch": 8, "gloss_limit": 60, "analysis": 24000, "terms_batch": 10},
    # API en la nube con mucho contexto.
    "nube": {"style": 60000, "gloss_batch": 30, "gloss_limit": 200, "analysis": 350000, "terms_batch": 60},
    # Chat en el navegador (copiar y pegar): mensajes de tamaño razonable.
    "chat": {"style": 30000, "gloss_batch": 25, "gloss_limit": 150, "analysis": 60000, "terms_batch": 40},
}

STEP_NAMES = {
    "estilo": N_("Guía de estilo"),
    "glosario": N_("Glosario oficial"),
    "analisis": N_("Análisis del libro"),
    "terminos": N_("Proponer términos"),
}


@dataclass
class Job:
    step: str
    label: str
    system: str
    user: str
    max_tokens: int = 16000
    data: dict = field(default_factory=dict)  # lo que el aplicador necesita (p. ej. tipos de término)

    def chat_text(self) -> str:
        """Mensaje único para pegar en un chat."""
        return self.system + "\n\n" + self.user + prompts.MANUAL_ANSWER

    def to_dict(self) -> dict:
        return asdict(self)


def _profile(provider: Provider | None) -> dict:
    if provider is None:
        return PROFILES["chat"]
    return PROFILES["local"] if provider.local else PROFILES["nube"]


def _need_refs(project: Project) -> list:
    pairs = project.reference_pairs(max_cost=6.0)
    if not pairs:
        raise ValueError(tr("Primero añade al menos un libro de referencia (original + traducción oficial)."))
    return pairs


def plan_style(project: Project, profile: dict) -> list[Job]:
    sample = _sample_pairs(_need_refs(project), profile["style"])
    user = prompts.STYLE_GUIDE_USER.format(
        author=project.config.get("autor") or "this author",
        src=prompts.lang_name(project.source_lang), tgt=prompts.lang_name(project.target_lang),
        passages=prompts.passages_block(sample), notes_lang=notes_language())
    return [Job("estilo", tr("Guía de estilo ({n} pasajes de las traducciones oficiales)", n=len(sample)),
                prompts.ANALYST_SYSTEM, user, 16000)]


def plan_glossary(project: Project, profile: dict) -> list[Job]:
    pairs = _need_refs(project)
    # Con pocas referencias (un libro corto) basta con que el término aparezca dos veces.
    min_count = 2 if len(pairs) < 200 else 3
    terms = candidate_terms([p.src for p in pairs], min_count=min_count, limit=profile["gloss_limit"])
    jobs = []
    size = profile["gloss_batch"]
    for start in range(0, len(terms), size):
        batch = terms[start:start + size]
        blocks = []
        for term, _count in batch:
            examples = sorted((p for p in pairs if term in p.src), key=lambda p: len(p.src))[:2]
            blocks.append(f'<term name="{term}">\n{prompts.passages_block([(p.src, p.tgt) for p in examples])}\n</term>')
        user = prompts.GLOSSARY_USER.format(
            author=project.config.get("autor") or "this author",
            src=prompts.lang_name(project.source_lang), tgt=prompts.lang_name(project.target_lang),
            terms="\n".join(blocks), notes_lang=notes_language())
        jobs.append(Job("glosario", tr("Glosario oficial: términos {start}–{start2} de {n}", start=start + 1, start2=start + len(batch), n=len(terms)),
                        prompts.ANALYST_SYSTEM, user, 8000))
    return jobs


def _chapter_texts(project: Project) -> list[tuple[int, str, str]]:
    out = []
    for i in project.chapter_indices():
        data = project.load_chapter(i)
        if not data.get("en_spine", True) or not data["segmentos"] or data.get("omitir"):
            continue
        text = "\n".join(strip_marks(s["src"]) for s in data["segmentos"])
        out.append((i, data.get("titulo", ""), text))
    return out


def _book_parts(project: Project, budget: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for idx, title, text in _chapter_texts(project):
        piece = f"[[CHAPTER {idx}: {title}]]\n{text}\n"
        while len(piece) > budget:  # capítulo enorme: se reparte
            cut = piece.rfind("\n", 0, budget)
            cut = cut if cut > 0 else budget
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
    return parts


def plan_analysis(project: Project, profile: dict) -> list[Job]:
    parts = _book_parts(project, profile["analysis"])
    title = project.config.get("titulo") or project.config.get("nombre", "")
    author = project.config.get("autor") or ""
    return [Job("analisis", tr("Análisis del libro: parte {k} de {n}", k=k, n=len(parts)), prompts.ANALYST_SYSTEM,
                prompts.ANALYZE_USER.format(part=k, parts=len(parts), title=title,
                                            author_part=f" by {author}" if author else "", text=text,
                                            notes_lang=notes_language()), 16000)
            for k, text in enumerate(parts, 1)]


def merge_job(project: Project, summaries: list[str]) -> Job:
    title = project.config.get("titulo") or project.config.get("nombre", "")
    return Job("analisis", tr("Análisis del libro: sinopsis final"), prompts.ANALYST_SYSTEM,
               prompts.SYNOPSIS_MERGE_USER.format(title=title, summaries="\n\n".join(summaries),
                                                  notes_lang=notes_language()), 4000,
               {"fusion": True})


def plan_terms(project: Project, profile: dict) -> list[Job]:
    bible = project.bible()
    glossary = project.glossary()
    known = {e["termino"].lower() for e in glossary}
    pending = [t for t in bible.get("terminos", []) if t["term"].lower() not in known]
    official = "\n".join(f"- {e['termino']} → {e['traduccion']}" for e in glossary if e.get("origen") != "propuesta")
    jobs = []
    size = profile["terms_batch"]
    for start in range(0, len(pending), size):
        batch = pending[start:start + size]
        listing = "\n".join(
            f"- {t['term']}" + (f" ({t['type']}: {t['note']})" if t.get("note") else "") for t in batch)
        user = prompts.PROPOSE_USER.format(
            title=project.config.get("titulo") or project.config.get("nombre", ""),
            src=prompts.lang_name(project.source_lang), tgt=prompts.lang_name(project.target_lang),
            style_guide=project.style_guide() or "(no style guide)",
            official=official or "(empty)", terms=listing, notes_lang=notes_language())
        jobs.append(Job("terminos", tr("Proponer términos: {start}–{start2} de {n}", start=start + 1, start2=start + len(batch), n=len(pending)),
                        prompts.ANALYST_SYSTEM, user, 8000, {"tipos": {t["term"]: t.get("type", "") for t in batch}}))
    return jobs


PLANNERS = {"estilo": plan_style, "glosario": plan_glossary, "analisis": plan_analysis, "terminos": plan_terms}


# -- aplicar respuestas ---------------------------------------------------------

def _entries(text: str, origin: str, types: dict | None = None) -> list[dict]:
    out = []
    for obj in prompts.parse_json_lines(prompts.strip_code_fences(text)):
        term, tr = str(obj.get("term", "")).strip(), str(obj.get("translation", "")).strip()
        if term and tr:
            out.append({"termino": term, "traduccion": tr,
                        "tipo": (types or {}).get(term, obj.get("type", "")) or "",
                        "nota": obj.get("note", "") or "", "origen": origin})
    return out


def apply_response(project: Project, job: Job, text: str, acc: dict) -> int:
    """Guarda lo que trae la respuesta de un trabajo. Devuelve cuántos elementos
    útiles encontró (0 si la respuesta no sirve). ``acc`` acumula el análisis."""
    if job.step == "estilo":
        guide = prompts.strip_code_fences(text).strip()
        if len(guide) < 20:
            return 0
        project.set_style_guide(guide)
        return 1
    if job.step in ("glosario", "terminos"):
        found = _entries(text, "oficial" if job.step == "glosario" else "propuesta", job.data.get("tipos"))
        if found:
            project.set_glossary(project.glossary() + found)
        return len(found)
    if job.step == "analisis":
        if job.data.get("fusion"):
            synopsis = prompts.strip_code_fences(text).strip()
            if not synopsis:
                return 0
            acc["sinopsis"] = synopsis
            return 1
        data = prompts.parse_json_object(prompts.strip_code_fences(text))
        if not data:
            return 0
        if data.get("summary"):
            acc.setdefault("resumenes", []).append(str(data["summary"]))
        caps = acc.setdefault("capitulos", {})
        for ch in data.get("chapters") or []:
            cid = str(ch.get("id", "")).strip()
            if cid and ch.get("summary"):
                caps[cid] = f"{caps[cid]} {ch['summary']}" if cid in caps else str(ch["summary"])
        terms = acc.setdefault("terminos", {})
        for t in data.get("terms") or []:
            name = str(t.get("term", "")).strip()
            if name and name not in terms:
                terms[name] = {"term": name, "type": t.get("type", ""), "note": t.get("note", "")}
        return 1
    raise ValueError(tr("Paso desconocido: {step}", step=job.step))


def finish_analysis(project: Project, acc: dict) -> dict:
    """Guarda sinopsis, resúmenes y términos del análisis."""
    summaries = acc.get("resumenes", [])
    synopsis = acc.get("sinopsis") or (summaries[0] if len(summaries) == 1 else "\n\n".join(summaries))
    terms = dict(acc.get("terminos", {}))
    all_text = [t for _i, _title, t in _chapter_texts(project)]
    for term, _count in candidate_terms(all_text, min_count=3, limit=120):
        terms.setdefault(term, {"term": term, "type": "", "note": ""})
    bible = project.bible()
    bible.update({"sinopsis": synopsis, "capitulos": acc.get("capitulos", {}), "terminos": list(terms.values())})
    project.set_bible(bible)
    return bible


# -- con API --------------------------------------------------------------------

def run_step(project: Project, provider: Provider, step: str, *, log=None) -> None:
    jobs = PLANNERS[step](project, _profile(provider))
    acc: dict = {}
    for job in jobs:
        _say(log, job.label + "…")
        result = provider.complete(job.system, job.user, max_tokens=job.max_tokens)
        project.record_usage(result.model or provider.model, result.usage)
        if not apply_response(project, job, result.text, acc) and step == "estilo":
            raise ValueError(tr("La IA no devolvió la guía de estilo. Prueba de nuevo."))
    if step == "analisis":
        if len(acc.get("resumenes", [])) > 1:
            job = merge_job(project, acc["resumenes"])
            _say(log, job.label + "…")
            result = provider.complete(job.system, job.user, max_tokens=job.max_tokens)
            project.record_usage(result.model or provider.model, result.usage)
            apply_response(project, job, result.text, acc)
        finish_analysis(project, acc)


def build_style_guide(project: Project, provider: Provider, *, log=None) -> str:
    run_step(project, provider, "estilo", log=log)
    return project.style_guide()


def build_reference_glossary(project: Project, provider: Provider, *, log=None) -> list[dict]:
    run_step(project, provider, "glosario", log=log)
    return project.glossary()


def analyze_book(project: Project, provider: Provider, *, log=None) -> dict:
    run_step(project, provider, "analisis", log=log)
    return project.bible()


def propose_terms(project: Project, provider: Provider, *, log=None) -> list[dict]:
    run_step(project, provider, "terminos", log=log)
    return project.glossary()


def prepare(project: Project, provider: Provider, *, steps=("estilo", "glosario", "analisis", "terminos"),
            log=None) -> None:
    """Ejecuta los pasos de preparación indicados, en orden."""
    has_refs = bool(project.references())
    for step in ("estilo", "glosario", "analisis", "terminos"):
        if step in steps and (has_refs or step in ("analisis", "terminos")):
            run_step(project, provider, step, log=log)


# -- modo manual (copiar y pegar en cualquier chat) ------------------------------

class ManualPrep:
    """Preparación paso a paso copiando las instrucciones en un chat de IA.
    El estado se guarda en ``conocimiento/manual.json`` para poder seguir otro día."""

    def __init__(self, project: Project):
        self.project = project
        self.path = project.knowledge_dir / "manual.json"

    def state(self) -> dict | None:
        from .project import read_json

        return read_json(self.path)

    def _save(self, state: dict | None) -> None:
        from .project import write_json

        if state is None:
            self.path.unlink(missing_ok=True)
        else:
            write_json(self.path, state)

    def start(self, step: str, *, analysis_budget: int | None = None) -> dict:
        if step not in PLANNERS:
            raise ValueError(tr("Paso desconocido: {step}", step=step))
        profile = dict(PROFILES["chat"])
        if analysis_budget:
            profile["analysis"] = analysis_budget
        jobs = PLANNERS[step](self.project, profile)
        if not jobs:
            raise ValueError(tr("No hay nada que preparar en este paso (por ejemplo, no quedan términos sin decidir)."))
        state = {"paso": step, "trabajos": [j.to_dict() for j in jobs], "hechos": 0, "acumulado": {}}
        self._save(state)
        return self.status()

    def status(self) -> dict | None:
        st = self.state()
        if not st:
            return None
        total = len(st["trabajos"])
        current = st["trabajos"][st["hechos"]] if st["hechos"] < total else None
        return {"paso": st["paso"], "nombre": tr(STEP_NAMES.get(st["paso"], st["paso"])), "hechos": st["hechos"],
                "total": total, "etiqueta": current["label"] if current else "", "terminado": current is None}

    def prompt(self) -> dict:
        st = self.state()
        if not st or st["hechos"] >= len(st["trabajos"]):
            raise ValueError(tr("No hay ningún paso de preparación manual en curso."))
        job = Job(**st["trabajos"][st["hechos"]])
        return {"texto": job.chat_text(), "etiqueta": job.label, "parte": st["hechos"] + 1,
                "total": len(st["trabajos"]), "caracteres": len(job.chat_text())}

    def import_response(self, text: str) -> dict:
        st = self.state()
        if not st or st["hechos"] >= len(st["trabajos"]):
            raise ValueError(tr("No hay ningún paso de preparación manual en curso."))
        job = Job(**st["trabajos"][st["hechos"]])
        acc = st["acumulado"]
        found = apply_response(self.project, job, text, acc)
        if not found:
            raise ValueError(tr("No encontré lo que esperaba en la respuesta. Copia la respuesta completa del chat "
                             "(el bloque de código entero) y vuelve a intentarlo."))
        st["hechos"] += 1
        if st["paso"] == "analisis" and st["hechos"] == len(st["trabajos"]):
            if len(acc.get("resumenes", [])) > 1 and "sinopsis" not in acc:
                st["trabajos"].append(merge_job(self.project, acc["resumenes"]).to_dict())
            else:
                finish_analysis(self.project, acc)
        self._save(st)
        status = self.status()
        status["encontrados"] = found
        if status["terminado"]:
            self._save(None)
        return status

    def cancel(self) -> None:
        self._save(None)
