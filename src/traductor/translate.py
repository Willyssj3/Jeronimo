"""Traducción capítulo a capítulo.

Cada capítulo se divide en bloques de párrafos consecutivos. Para cada bloque
se envía a la IA: instrucciones + guía de estilo + sinopsis (fijo y cacheado),
y el glosario relevante, ejemplos parecidos de las traducciones oficiales, el
resumen del capítulo y los últimos párrafos ya traducidos (variable).

El progreso se guarda después de cada bloque: si se corta, se retoma donde
quedó. También hay un modo manual (copiar el prompt y pegar la respuesta) para
quien quiera usar la IA desde su navegador.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field

from . import inline, prompts
from .project import Project
from .providers import Provider, RateLimitError, RefusalError
from .i18n import N_, tr


# Caracteres del original por bloque en el modo manual (copiar y pegar en un chat).
MANUAL_BLOCKS = {"normal": 6000, "grande": 15000, "muy-grande": 25000}
DEFAULT_MANUAL_BLOCK = MANUAL_BLOCKS["grande"]

# Chats de IA para el modo manual: nombre y tamaño de bloque recomendado. Son
# valores prudentes; si un chat corta la respuesta, basta con bajar el tamaño.
CHAT_PRESETS = {
    "claude": ("Claude (claude.ai)", 15000),
    "chatgpt": ("ChatGPT", 12000),
    "gemini": ("Gemini", 15000),
    "copilot": ("Microsoft Copilot", 6000),
    "deepseek": ("DeepSeek", 12000),
    "lechat": ("Le Chat (Mistral)", 10000),
    "otro": (N_("Otro chat o modelo local"), 6000),
}

# Modelos de cada chat por nivel («avanzado», «estandar», «rapido»). Se nombran
# por familia (sin versión) para que no queden viejos.
_GENERIC_MODELS = [("avanzado", N_("El más capaz (con razonamiento)"), "avanzado"),
                   ("estandar", N_("Estándar"), "estandar"),
                   ("rapido", N_("Rápido o ligero (mini, flash…)"), "rapido")]
CHAT_MODELS = {
    "claude": [("opus", N_("Opus (el más capaz)"), "avanzado"), ("sonnet", N_("Sonnet (equilibrado)"), "estandar"),
               ("haiku", N_("Haiku (rápido)"), "rapido")],
    "gemini": [("pro", N_("Pro (el más capaz)"), "avanzado"), ("flash", N_("Flash (rápido)"), "rapido")],
}
PLANS = {"pago": N_("De pago (Pro, Plus, Advanced…)"), "gratis": N_("Gratuito")}
# Proporción del bloque recomendado según el nivel del modelo y el plan.
_TIER_FACTOR = {"avanzado": 1.0, "estandar": 0.85, "rapido": 0.6}
_PLAN_FACTOR = {"pago": 1.0, "gratis": 0.5}
BLOCK_SIZES = (4000, 6000, 8000, 10000, 12000, 15000, 25000)


def chat_models(chat: str) -> list[tuple[str, str, str]]:
    return CHAT_MODELS.get(chat, _GENERIC_MODELS)


def model_tier(chat: str, model: str) -> str:
    return next((tier for mid, _name, tier in chat_models(chat) if mid == model), "avanzado")


def recommended_block(chat: str, model: str = "", plan: str = "pago") -> int:
    """Tamaño de bloque prudente para ese chat, modelo y plan."""
    base = CHAT_PRESETS.get(chat, CHAT_PRESETS["otro"])[1]
    size = base * _TIER_FACTOR[model_tier(chat, model)] * _PLAN_FACTOR.get(plan, 1.0)
    return max([b for b in BLOCK_SIZES if b <= size] or [BLOCK_SIZES[0]])


@dataclass
class Options:
    chunk_chars: int = 6000      # tamaño de cada bloque (caracteres del original)
    examples: int = 6            # ejemplos de las traducciones oficiales por bloque
    example_chars: int = 5000    # presupuesto de caracteres para ejemplos
    context_segments: int = 3    # párrafos anteriores que se muestran como contexto
    context_chars: int = 1500
    max_retries: int = 2         # reintentos para párrafos que faltan en la respuesta

    @classmethod
    def for_provider(cls, provider: Provider | None, overrides: dict | None = None) -> "Options":
        if provider is not None and provider.local:
            opts = cls(chunk_chars=2000, examples=2, example_chars=1200, context_segments=2, context_chars=600)
        else:
            opts = cls()
        for k, v in (overrides or {}).items():
            if hasattr(opts, k):
                setattr(opts, k, type(getattr(opts, k))(v))
        return opts


@dataclass
class ChapterReport:
    index: int
    translated: int = 0
    missing: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stopped: bool = False


def pseudo_slots(source: str) -> dict:
    """Tipos de marca presentes en un texto marcado (para validar respuestas)."""
    slots = {}
    for _m, kind, n in inline._tokens(source):
        if kind == "open":
            slots[n] = ("g", None)
        elif kind == "atom":
            slots[n] = ("x", None)
    return slots


def _varied(pairs: list) -> list:
    """Pares de referencia variados: alterna diálogos y narración, y prefiere
    los de longitud media (los más útiles como ejemplo)."""
    useful = [p for p in pairs if 40 <= len(p.src) <= 900]
    dialogue = [p for p in useful if p.src.lstrip()[:1] in "\"“‘'«—-"]
    narration = [p for p in useful if p not in dialogue]
    out = []
    for k in range(max(len(dialogue), len(narration))):
        if k < len(narration):
            out.append(narration[k])
        if k < len(dialogue):
            out.append(dialogue[k])
    return out


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", inline.strip_marks(text)).strip().lower()


class Translator:
    def __init__(self, project: Project, provider: Provider | None, *, options: Options | None = None, log=None):
        self.project = project
        self.provider = provider
        self.options = options or Options.for_provider(provider, project.config.get("opciones"))
        self.log = log or (lambda msg: None)
        self.reload_knowledge()

    def reload_knowledge(self) -> None:
        """Vuelve a leer guía, glosario, sinopsis y ejemplos (p. ej. tras aprobar un capítulo)."""
        cfg = self.project.config
        bible = self.project.bible()
        self.chapter_summaries = bible.get("capitulos", {})
        self.system = prompts.translate_system(
            title=cfg.get("titulo") or cfg.get("nombre", ""), author=cfg.get("autor", ""),
            src=self.project.source_lang, tgt=self.project.target_lang, variant=cfg.get("variante", "auto"),
            style_guide=self.project.style_guide(), synopsis=bible.get("sinopsis", ""))
        self.glossary = self.project.glossary()
        self._glossary_patterns = []
        for entry in self.glossary:
            term = entry["termino"]
            flags = 0 if term[:1].isupper() else re.IGNORECASE
            self._glossary_patterns.append((re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", flags), entry))
        self.index = self.project.example_index()

    # -- construcción de prompts ----------------------------------------------

    def pending_chunks(self, data: dict) -> list[list[dict]]:
        """Bloques de párrafos consecutivos sin traducir."""
        chunks: list[list[dict]] = []
        current: list[dict] = []
        size = 0
        for seg in data["segmentos"]:
            if seg.get("tgt"):
                if current:
                    chunks.append(current)
                    current, size = [], 0
                continue
            length = len(seg["src"])
            if current and size + length > self.options.chunk_chars:
                chunks.append(current)
                current, size = [], 0
            current.append(seg)
            size += length
        if current:
            chunks.append(current)
        return chunks

    def relevant_glossary(self, text: str) -> list[dict]:
        return [entry for pattern, entry in self._glossary_patterns if pattern.search(text)][:80]

    def previous_context(self, data: dict, first_n: int) -> list[tuple[str, str]]:
        k, budget = self.options.context_segments, self.options.context_chars
        before = [s for s in data["segmentos"] if s["n"] < first_n and s.get("tgt")]
        if not before and data["doc"] > 0:
            try:
                prev = self.project.load_chapter(data["doc"] - 1)
                before = [s for s in prev["segmentos"] if s.get("tgt")]
            except KeyError:
                before = []
        out: list[tuple[str, str]] = []
        used = 0
        for s in reversed(before[-k:]):
            src, tgt = inline.strip_marks(s["src"]), inline.strip_marks(s["tgt"])
            if used + len(src) + len(tgt) > budget and out:
                break
            out.append((src, tgt))
            used += len(src) + len(tgt)
        return list(reversed(out))

    def build_user(self, data: dict, chunk: list[dict], *, light: bool = False) -> str:
        plain = "\n".join(inline.strip_marks(s["src"]) for s in chunk)
        glossary = self.relevant_glossary(plain)
        # Bloques de solo títulos (índices, encabezados): los ejemplos no aportan.
        titles_only = all(len(inline.strip_marks(s["src"])) < 60 for s in chunk)
        # Con el paquete subido a un Proyecto, bastan un par de ejemplos muy parecidos.
        k, budget = (2, 1500) if light else (self.options.examples, self.options.example_chars)
        examples = [] if titles_only else self.index.search(
            plain, k=k, max_chars=budget, must_include=[g["termino"] for g in glossary])
        return prompts.translate_user(
            segments=[(s["n"], s["src"]) for s in chunk],
            glossary=glossary,
            examples=examples,
            chapter_summary=self.chapter_summaries.get(str(data["doc"]), ""),
            previous=self.previous_context(data, chunk[0]["n"]),
        )

    # -- aplicación de respuestas ---------------------------------------------

    def apply_response(self, chunk: list[dict], text: str) -> list[dict]:
        """Guarda en los segmentos lo que vino en la respuesta; devuelve los que faltan."""
        segs, notes = prompts.parse_segments(text)
        missing = []
        for seg in chunk:
            t = segs.get(seg["n"])
            if not t:
                missing.append(seg)
                continue
            slots = pseudo_slots(seg["src"])
            warnings: list[str] = []
            if inline.problems(t, slots):
                t, warnings = inline.repair(t, slots)
            src_norm = _norm(seg["src"])
            if src_norm == _norm(t) and len(src_norm.split()) >= 4:
                warnings.append(N_("posible párrafo sin traducir"))
            seg["tgt"] = t
            seg["avisos"] = warnings
            seg["nota"] = notes.get(seg["n"]) or None
            seg["editado"] = False
            # Una traducción nueva se puede volver a revisar; la sugerencia anterior ya no vale.
            seg.pop("sugerencia", None)
            seg.pop("revisado_ia", None)
        return missing

    # -- traducción con IA ----------------------------------------------------

    def _call(self, system: str, user: str):
        delays = [20, 60, 180]
        for attempt in range(len(delays) + 1):
            try:
                return self.provider.complete(system, user)
            except RateLimitError:
                if attempt == len(delays):
                    raise
                self.log(tr("Límite de peticiones alcanzado; espero {segundos} s y reintento…", segundos=delays[attempt]))
                time.sleep(delays[attempt])
        raise AssertionError("inalcanzable")

    def _translate_chunk(self, data: dict, chunk: list[dict], depth: int = 0) -> None:
        user = self.build_user(data, chunk)
        try:
            result = self._call(self.system, user)
        except RefusalError:
            if len(chunk) > 1:
                half = len(chunk) // 2
                self._translate_chunk(data, chunk[:half], depth)
                self._translate_chunk(data, chunk[half:], depth)
                return
            seg = chunk[0]
            seg["avisos"] = [N_("la IA se negó a traducir este párrafo: tradúcelo a mano")]
            return
        self.project.record_usage(result.model or self.provider.model, result.usage)
        missing = self.apply_response(chunk, result.text)
        if not missing:
            return
        if result.truncated and len(missing) > 1:
            self.log(tr("La respuesta se cortó; reparto el bloque en partes más pequeñas…"))
            half = len(missing) // 2
            self._translate_chunk(data, missing[:half], depth + 1)
            self._translate_chunk(data, missing[half:], depth + 1)
        elif depth < self.options.max_retries:
            self.log(tr("Faltaron {n} párrafo(s) en la respuesta; reintento…", n=len(missing)))
            self._translate_chunk(data, missing, depth + 1)

    def translate_chapter(self, index: int, *, progress=None, should_stop=None) -> ChapterReport:
        if self.provider is None:
            raise ValueError(tr("No hay un proveedor de IA configurado."))
        data = self.project.load_chapter(index)
        report = ChapterReport(index)
        total = len(data["segmentos"])
        chunks = self.pending_chunks(data)
        if not chunks:
            return report
        self.log(tr("Capítulo {index} «{titulo}»: {n} bloque(s).", index=index, titulo=data.get("titulo", ""), n=len(chunks)))
        for k, chunk in enumerate(chunks, 1):
            if should_stop and should_stop():
                report.stopped = True
                break
            self.log("  " + tr("Bloque {k}/{n} ({parrafos} párrafos)…", k=k, n=len(chunks), parrafos=len(chunk)))
            self._translate_chunk(data, chunk)
            self.project.save_chapter(data)
            if progress:
                progress(sum(1 for s in data["segmentos"] if s.get("tgt")), total)
        report.translated = sum(1 for s in data["segmentos"] if s.get("tgt"))
        report.missing = [s["n"] for s in data["segmentos"] if not s.get("tgt")]
        report.warnings = [f"§{s['n']}: {w}" for s in data["segmentos"] for w in s.get("avisos", [])]
        return report

    def next_chapter(self) -> int | None:
        """Primer capítulo (en orden de lectura) con párrafos pendientes."""
        for info in self.project.chapters():
            if info.total and info.done < info.total and info.status != "omitido":
                return info.index
        return None

    # -- modo manual (copiar y pegar) -----------------------------------------

    def manual_block_chars(self) -> int:
        size = (self.project.config.get("opciones") or {}).get("bloque_manual")
        return int(size) if size else DEFAULT_MANUAL_BLOCK

    def manual_chunks(self, data: dict, block_chars: int | None = None) -> list[list[dict]]:
        saved = self.options.chunk_chars
        self.options.chunk_chars = block_chars or self.manual_block_chars()
        try:
            return self.pending_chunks(data)
        finally:
            self.options.chunk_chars = saved

    def light_blocks(self) -> bool:
        """¿Bloques livianos? (las instrucciones y el conocimiento ya están en un Proyecto)."""
        return bool((self.project.config.get("opciones") or {}).get("bloques_livianos"))

    def manual_prompt(self, index: int, block_chars: int | None = None) -> tuple[str, list[int], int] | None:
        """Prompt completo del siguiente bloque pendiente, para pegarlo en un chat.
        Devuelve (texto, párrafos del bloque, bloques pendientes en el capítulo)."""
        data = self.project.load_chapter(index)
        chunks = self.manual_chunks(data, block_chars)
        if not chunks:
            return None
        chunk = chunks[0]
        if self.light_blocks():
            header = prompts.LIGHT_BLOCK_HEADER.format(tgt=prompts.lang_name(self.project.target_lang))
            text = header + "\n\n" + self.build_user(data, chunk, light=True) + prompts.MANUAL_OUTPUT
        else:
            text = self.system + "\n\n" + self.build_user(data, chunk) + prompts.MANUAL_OUTPUT
        return text, [s["n"] for s in chunk], len(chunks)

    # -- paquete para Proyectos ------------------------------------------------

    def knowledge_fingerprint(self) -> str:
        """Huella de lo que va en el paquete: si cambia, hay que volver a subirlo."""
        bible = self.project.bible()
        parts = [self.project.style_guide(), json.dumps(self.glossary, ensure_ascii=False, sort_keys=True),
                 bible.get("sinopsis", ""), json.dumps(bible.get("capitulos", {}), sort_keys=True),
                 str(len(self.project.reference_pairs()))]
        return hashlib.sha1("\u241e".join(parts).encode("utf-8")).hexdigest()[:16]

    def project_instructions(self) -> str:
        """Texto para las instrucciones del Proyecto (rol, reglas, guía de estilo y sinopsis)."""
        return self.system.rstrip() + "\n" + prompts.PROJECT_NOTE

    def project_knowledge(self, max_example_chars: int = 50000) -> str:
        """Archivo de conocimiento: glosario completo, resúmenes y pasajes oficiales."""
        cfg = self.project.config
        out = [f"# {cfg.get('titulo') or cfg.get('nombre', '')} — material de referencia para la traducción", ""]
        if self.glossary:
            out += ["## Glossary (mandatory renderings)", ""]
            for g in self.glossary:
                kind = f" [{g['tipo']}]" if g.get("tipo") else ""
                note = f" — {g['nota']}" if g.get("nota") else ""
                out.append(f"- {g['termino']} → {g['traduccion']}{kind}{note}")
            out.append("")
        summaries = {int(k): v for k, v in self.chapter_summaries.items() if v}
        if summaries:
            titles = {i: (self.project.load_chapter(i).get("titulo") or f"Documento {i}") for i in summaries
                      if self.project.chapter_path(i).exists()}
            out += ["## Chapter summaries", ""]
            for i in sorted(summaries):
                out.append(f"- {titles.get(i, f'Documento {i}')}: {summaries[i]}")
            out.append("")
        pairs = self.project.approved_pairs()[-40:] + self.project.reference_pairs()
        if pairs:
            out += ["## Passages from the official translations (and approved passages of this book)", ""]
            used = 0
            for ex in _varied(pairs):
                block = f"<source>{ex.src}</source>\n<translation>{ex.tgt}</translation>\n"
                if used + len(block) > max_example_chars:
                    break
                out.append(block)
                used += len(block)
        return "\n".join(out).rstrip() + "\n"

    def manual_import(self, index: int, response: str) -> tuple[int, list[int]]:
        """Importa la respuesta pegada por el usuario. Devuelve (guardados, faltantes)."""
        data = self.project.load_chapter(index)
        pending = [s for s in data["segmentos"] if not s.get("tgt")]
        segs, _notes = prompts.parse_segments(response)
        candidates = [s for s in pending if s["n"] in segs]
        missing = self.apply_response(candidates, response)
        self.project.save_chapter(data)
        saved = len(candidates) - len(missing)
        return saved, [s["n"] for s in missing]
