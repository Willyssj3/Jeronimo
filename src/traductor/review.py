"""Segunda pasada con IA: una IA relee la traducción junto al original y
propone correcciones. Nada se cambia solo: cada sugerencia se acepta o se
descarta desde la revisión del capítulo.

Funciona con la IA conectada (en segundo plano) o en modo manual, copiando
los bloques en cualquier chat."""

from __future__ import annotations

import re

from . import inline, prompts
from .translate import Options, Translator, pseudo_slots
from .i18n import tr


def _same(a: str, b: str) -> bool:
    return re.sub(r"\s+", " ", a or "").strip() == re.sub(r"\s+", " ", b or "").strip()


class Reviewer:
    def __init__(self, project, provider=None, *, options: Options | None = None, log=None):
        self.project = project
        self.provider = provider
        self.translator = Translator(project, provider, options=options, log=log)
        self.log = self.translator.log
        cfg = project.config
        bible = project.bible()
        self.system = prompts.review_system(
            title=cfg.get("titulo") or cfg.get("nombre", ""), author=cfg.get("autor", ""),
            src=project.source_lang, tgt=project.target_lang, variant=cfg.get("variante", "auto"),
            style_guide=project.style_guide(), synopsis=bible.get("sinopsis", ""))
        self.summaries = bible.get("capitulos", {})

    # -- bloques --------------------------------------------------------------------

    def chunks(self, data: dict, budget: int) -> list[list[dict]]:
        """Bloques de párrafos traducidos que todavía no se revisaron."""
        chunks: list[list[dict]] = []
        current: list[dict] = []
        size = 0
        for seg in data["segmentos"]:
            if not seg.get("tgt") or seg.get("revisado_ia"):
                if current:
                    chunks.append(current)
                    current, size = [], 0
                continue
            length = len(seg["src"]) + len(seg["tgt"])
            if current and size + length > budget:
                chunks.append(current)
                current, size = [], 0
            current.append(seg)
            size += length
        if current:
            chunks.append(current)
        return chunks

    def build_user(self, data: dict, chunk: list[dict]) -> str:
        plain = "\n".join(inline.strip_marks(s["src"]) for s in chunk)
        return prompts.review_user(
            segments=[(s["n"], s["src"], s["tgt"]) for s in chunk],
            glossary=self.translator.relevant_glossary(plain),
            chapter_summary=self.summaries.get(str(data["doc"]), ""))

    # -- respuestas -------------------------------------------------------------------

    def apply_response(self, chunk: list[dict], text: str) -> int:
        """Guarda las sugerencias de un bloque y lo marca como revisado. Devuelve cuántas hay."""
        fixes = prompts.parse_fixes(text)
        count = 0
        for seg in chunk:
            seg["revisado_ia"] = True
            fix = fixes.get(seg["n"])
            if not fix or not fix[0] or _same(fix[0], seg["tgt"]):
                continue
            new, why = fix
            slots = pseudo_slots(seg["src"])
            warnings: list[str] = []
            if inline.problems(new, slots):
                new, warnings = inline.repair(new, slots)
            seg["sugerencia"] = {"texto": new, "motivo": why, "avisos": warnings}
            count += 1
        return count

    # -- con la IA conectada ----------------------------------------------------------

    def review_chapter(self, index: int, *, progress=None, should_stop=None) -> dict:
        if self.provider is None:
            raise ValueError(tr("No hay un proveedor de IA configurado."))
        data = self.project.load_chapter(index)
        chunks = self.chunks(data, self.translator.options.chunk_chars * 2)
        found = 0
        for k, chunk in enumerate(chunks):
            if should_stop and should_stop():
                break
            if progress:
                progress(k, len(chunks))
            result = self.translator._call(self.system, self.build_user(data, chunk))
            self.project.record_usage(result.model or self.provider.model, result.usage)
            if not prompts.looks_like_review(result.text) and not result.truncated:
                self.log(tr("La respuesta no tenía el formato esperado; ese bloque queda sin revisar."))
                continue
            found += self.apply_response(chunk, result.text)
            self.project.save_chapter(data)
        if progress:
            progress(len(chunks), len(chunks))
        return {"bloques": len(chunks), "sugerencias": found}

    # -- modo manual (copiar y pegar) ---------------------------------------------------

    def manual_prompt(self, index: int, block_chars: int | None = None) -> dict | None:
        data = self.project.load_chapter(index)
        chunks = self.chunks(data, block_chars or self.translator.manual_block_chars())
        if not chunks:
            return None
        chunk = chunks[0]
        text = self.system + "\n\n" + self.build_user(data, chunk) + prompts.REVIEW_MANUAL_OUTPUT
        return {"texto": text, "ids": [s["n"] for s in chunk], "bloques_restantes": len(chunks),
                "caracteres": len(text)}

    def manual_import(self, index: int, response: str, block_chars: int | None = None) -> dict:
        """Importa la respuesta del chat para el bloque que se copió (el primero pendiente)."""
        text = prompts.strip_code_fences(response)
        if not prompts.looks_like_review(text):
            raise ValueError(tr("No encontré correcciones (<fix …>) ni <nofixes/> en lo que pegaste. "
                             "Copia la respuesta completa con el botón del bloque de código."))
        data = self.project.load_chapter(index)
        chunks = self.chunks(data, block_chars or self.translator.manual_block_chars())
        if not chunks:
            raise ValueError(tr("Este capítulo ya está revisado."))
        ids = set(prompts.parse_fixes(text))
        chunk = chunks[0]
        # Si la respuesta se cortó, los párrafos posteriores a la última corrección quedan pendientes.
        if ids and not text.rstrip().lower().endswith(("</fix>", "<nofixes/>")) and max(ids) < chunk[-1]["n"]:
            chunk = [s for s in chunk if s["n"] <= max(ids)]
        found = self.apply_response(chunk, text)
        self.project.save_chapter(data)
        return {"sugerencias": found, "revisados": len(chunk)}


# -- aceptar, descartar y estado --------------------------------------------------------

def status(project, index: int) -> dict:
    data = project.load_chapter(index)
    translated = [s for s in data["segmentos"] if s.get("tgt")]
    return {"por_revisar": sum(1 for s in translated if not s.get("revisado_ia")),
            "revisados": sum(1 for s in translated if s.get("revisado_ia")),
            "sugerencias": sum(1 for s in data["segmentos"] if s.get("sugerencia"))}


def resolve(project, index: int, n: int | None, accept: bool) -> int:
    """Acepta o descarta la sugerencia de un párrafo (o todas si ``n`` es None)."""
    data = project.load_chapter(index)
    done = 0
    for seg in data["segmentos"]:
        if (n is not None and seg["n"] != n) or not seg.get("sugerencia"):
            continue
        fix = seg.pop("sugerencia")
        if accept:
            seg["tgt"] = fix["texto"]
            seg["avisos"] = fix.get("avisos") or []
            seg["editado"] = True
        done += 1
    project.save_chapter(data)
    return done


def reset(project, index: int) -> None:
    """Para volver a revisar todo el capítulo (se conservan las sugerencias pendientes)."""
    data = project.load_chapter(index)
    for seg in data["segmentos"]:
        seg.pop("revisado_ia", None)
    project.save_chapter(data)
