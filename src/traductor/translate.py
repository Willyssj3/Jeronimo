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

import re
import time
from dataclasses import dataclass, field

from . import inline, prompts
from .project import Project
from .providers import Provider, RateLimitError, RefusalError


# Caracteres del original por bloque en el modo manual (copiar y pegar en un chat).
MANUAL_BLOCKS = {"normal": 6000, "grande": 15000, "muy-grande": 25000}
DEFAULT_MANUAL_BLOCK = MANUAL_BLOCKS["grande"]


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

    def build_user(self, data: dict, chunk: list[dict]) -> str:
        plain = "\n".join(inline.strip_marks(s["src"]) for s in chunk)
        glossary = self.relevant_glossary(plain)
        examples = self.index.search(plain, k=self.options.examples, max_chars=self.options.example_chars,
                                     must_include=[g["termino"] for g in glossary])
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
                warnings.append("posible párrafo sin traducir")
            seg["tgt"] = t
            seg["avisos"] = warnings
            seg["nota"] = notes.get(seg["n"]) or None
            seg["editado"] = False
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
                self.log(f"Límite de peticiones alcanzado; espero {delays[attempt]} s y reintento…")
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
            seg["avisos"] = ["la IA se negó a traducir este párrafo: tradúcelo a mano"]
            return
        self.project.record_usage(result.model or self.provider.model, result.usage)
        missing = self.apply_response(chunk, result.text)
        if not missing:
            return
        if result.truncated and len(missing) > 1:
            self.log("La respuesta se cortó; reparto el bloque en partes más pequeñas…")
            half = len(missing) // 2
            self._translate_chunk(data, missing[:half], depth + 1)
            self._translate_chunk(data, missing[half:], depth + 1)
        elif depth < self.options.max_retries:
            self.log(f"Faltaron {len(missing)} párrafo(s) en la respuesta; reintento…")
            self._translate_chunk(data, missing, depth + 1)

    def translate_chapter(self, index: int, *, progress=None, should_stop=None) -> ChapterReport:
        if self.provider is None:
            raise ValueError("No hay un proveedor de IA configurado.")
        data = self.project.load_chapter(index)
        report = ChapterReport(index)
        total = len(data["segmentos"])
        chunks = self.pending_chunks(data)
        if not chunks:
            return report
        self.log(f"Capítulo {index} «{data.get('titulo', '')}»: {len(chunks)} bloque(s).")
        for k, chunk in enumerate(chunks, 1):
            if should_stop and should_stop():
                report.stopped = True
                break
            self.log(f"  Bloque {k}/{len(chunks)} ({len(chunk)} párrafos)…")
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

    def manual_prompt(self, index: int, block_chars: int | None = None) -> tuple[str, list[int], int] | None:
        """Prompt completo del siguiente bloque pendiente, para pegarlo en un chat.
        Devuelve (texto, párrafos del bloque, bloques pendientes en el capítulo)."""
        data = self.project.load_chapter(index)
        chunks = self.manual_chunks(data, block_chars)
        if not chunks:
            return None
        chunk = chunks[0]
        text = self.system + "\n\n" + self.build_user(data, chunk) + prompts.MANUAL_OUTPUT
        return text, [s["n"] for s in chunk], len(chunks)

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
