"""Corregir los avisos del control de calidad con un clic.

Lo mecánico se corrige al instante, sin gastar IA, y se puede deshacer: signos
de apertura (¿ ¡), espacios y puntuación, guiones en lugar de rayas de diálogo
y nombres escritos de varias formas (se elige cuál queda). Lo que necesita
entender el texto (glosario, párrafos sin traducir, palabras en el idioma
original, números, posibles omisiones, formato perdido, el estilo de los
diálogos) lo propone la IA para ese párrafo: la propuesta queda como sugerencia
y se acepta o se descarta igual que en la revisión con IA. En modo manual, las
instrucciones se copian en cualquier chat."""

from __future__ import annotations

import difflib
import html
import re
import uuid
from datetime import datetime
from itertools import groupby

from . import inline, prompts, qa
from .i18n import tr
from .project import read_json, write_json
from .review import _same
from .translate import Options, Translator, pseudo_slots

DIRECT_KINDS = ("signos", "espacios", "dialogo")  # los que pueden corregirse sin IA
UNDO_LIMIT = 20


# -- cambiar el texto sin tocar las marcas de formato ---------------------------------

def edit_marked(marked: str, transform) -> str:
    """Aplica a un texto marcado un cambio pensado sobre su texto plano: lo que
    cambia se reemplaza dentro de los trozos de texto donde estaba, así las
    cursivas, las notas y las imágenes del párrafo quedan como estaban."""
    pieces: list[list] = []  # [¿texto?, contenido (texto plano o la marca), ¿cambiado?, original]
    last = 0
    for m in inline.TOKEN_RE.finditer(marked):
        raw = marked[last:m.start()]
        pieces.append([True, html.unescape(raw), False, raw])
        pieces.append([False, m.group(0), False, m.group(0)])
        last = m.end()
    pieces.append([True, html.unescape(marked[last:]), False, marked[last:]])
    old = "".join(p[1] for p in pieces if p[0])
    new = transform(old)
    if new == old:
        return marked
    owner: list[tuple[int, int]] = []  # letra del texto plano → (trozo, posición en el trozo)
    for k, piece in enumerate(pieces):
        if piece[0]:
            owner.extend((k, j) for j in range(len(piece[1])))
    edits: list[tuple[int, int, int, str]] = []  # (trozo, posición, letras que se quitan, texto que se agrega)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        added = new[j1:j2]
        if i1 == i2:
            k, j = _insertion_point(pieces, owner, i1)
            edits.append((k, j, 0, added))
            continue
        for step, (k, group) in enumerate(groupby(range(i1, i2), key=lambda i: owner[i][0])):
            positions = list(group)
            edits.append((k, owner[positions[0]][1], len(positions), added if step == 0 else ""))
    for k, j, removed, added in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = pieces[k][1]
        pieces[k][1] = text[:j] + added + text[j + removed:]
        pieces[k][2] = True
    return "".join((html.escape(p[1], quote=False) if p[2] else p[3]) if p[0] else p[1] for p in pieces)


def _insertion_point(pieces: list, owner: list, i: int) -> tuple[int, int]:
    """Dónde va lo que se agrega delante de la letra ``i``. Si esa letra empieza un
    formato (una cursiva, por ejemplo), lo nuevo queda afuera: «¿<i>Vienes</i>?»."""
    if i < len(owner):
        k, j = owner[i]
        if j == 0:
            m = k - 1
            while m >= 0 and not pieces[m][0] and pieces[m][1].startswith("<g"):
                m -= 1
            if 0 <= m < k - 1 and pieces[m][0]:
                return m, len(pieces[m][1])
        return k, j
    if owner:
        k = owner[-1][0]
        return k, len(pieces[k][1])
    return len(pieces) - 1, 0


# -- arreglos mecánicos (sobre el texto plano) ------------------------------------------

# Fin de la oración o del inciso anterior: la pregunta empieza después.
_BREAK_RE = re.compile(r"[.?!…]+[»”\"’')\]]*\s+|[:;]\s+|—[,;:]\s+")
_SKIP = set("«“\"‘'([—–-  ")


def _add_openings(text: str, close: str, opening: str) -> str:
    inserts = []
    for run in re.finditer(r"[?!]+", text):
        if close not in run.group(0):
            continue
        start = 0
        for m in _BREAK_RE.finditer(text, 0, run.start()):
            start = m.end()
        if opening in text[start:run.start()]:
            continue  # ya tiene su signo de apertura
        while start < run.start() and text[start] in _SKIP:
            start += 1
        if start < run.start():
            inserts.append(start)
    for pos in reversed(inserts):
        text = text[:pos] + opening + text[pos:]
    return text


def add_opening_signs(text: str) -> str:
    """Agrega los ¿ ¡ que faltan al comienzo de cada pregunta o exclamación:
    «—Bailas? —preguntó» → «—¿Bailas? —preguntó»."""
    for close, opening in (("?", "¿"), ("!", "¡")):
        text = _add_openings(text, close, opening)
    return text


def fix_spacing(text: str, *, french: bool = False) -> str:
    """Espacios dobles y espacios mal puestos junto a los signos."""
    text = re.sub(r"(?<=\S) {2,}(?=\S)", " ", text)
    if french:
        return re.sub(r"\s+(?=,|\.(?!\.))", "", text)
    text = re.sub(r"\s+(?=[,;:]|\.(?!\.))", "", text)
    text = re.sub(r"(?<=[¿¡«])\s+", "", text)
    return re.sub(r"\s+(?=[?!»])", "", text)


_ASIDE_OPEN = re.compile(r"(?<=[^\W\d_])([.,!?…]?)\s[-–]\s?(?=[^\W\d_])")
_ASIDE_CLOSE = re.compile(r"(?<=[^\W\d_])\s?[-–](?=[.,;:!?…]|\s|$)")


def fix_dialogue_dashes(text: str, *, dialogue: bool) -> str:
    """Guiones en lugar de rayas: «- Hola - dijo ella -.» → «—Hola —dijo ella—.»"""
    lead = re.match(r"(\s*)[-–]\s*(?=[^\W\d_]|[¿¡])", text)
    if lead:
        text = lead.group(1) + "—" + text[lead.end():]
    if not dialogue:
        return text
    out, pos = [], 0
    while True:
        opened = _ASIDE_OPEN.search(text, pos)
        if not opened:
            break
        out.append(text[pos:opened.start()] + opened.group(1) + " —")
        pos = opened.end()
        closed = _ASIDE_CLOSE.search(text, pos)
        following = _ASIDE_OPEN.search(text, pos)
        if closed and (not following or closed.start() < following.start()):
            out.append(text[pos:closed.start()] + "—")
            pos = closed.end()
    out.append(text[pos:])
    return "".join(out)


def direct_fix(checker: qa.Checker, doc: int, seg: dict, kind: str) -> str | None:
    """La traducción corregida sin IA, o None si ese aviso necesita la IA."""
    if kind not in DIRECT_KINDS or not seg.get("tgt"):
        return None
    if kind == "signos":
        transform = add_opening_signs
    elif kind == "espacios":
        def transform(text):
            return fix_spacing(text, french=checker.french)
    else:
        dialogue = inline.strip_marks(seg["src"]).lstrip()[:1] in qa.DIALOGUE_OPENERS

        def transform(text):
            return fix_dialogue_dashes(text, dialogue=dialogue)
    new = edit_marked(seg["tgt"], transform)
    if new == seg["tgt"]:
        return None

    def count(s: dict) -> int:
        return sum(1 for i in checker.segment_issues(doc, s) if i.tipo == kind)

    return new if count(dict(seg, tgt=new)) < count(seg) else None


# -- deshacer --------------------------------------------------------------------

def _undo_path(project):
    return project.knowledge_dir / "correcciones_calidad.json"


def _push(project, description: str, changes: list[dict]) -> dict | None:
    if not changes:
        return None
    batches = (read_json(_undo_path(project)) or {}).get("lotes", [])
    batch = {"id": uuid.uuid4().hex[:10], "fecha": datetime.now().isoformat(timespec="seconds"),
             "descripcion": description, "cambios": changes}
    batches.append(batch)
    write_json(_undo_path(project), {"lotes": batches[-UNDO_LIMIT:]})
    return batch


def last_batch(project) -> dict | None:
    batches = (read_json(_undo_path(project)) or {}).get("lotes", [])
    if not batches:
        return None
    b = batches[-1]
    return {"id": b["id"], "descripcion": b["descripcion"], "parrafos": len(b["cambios"]), "fecha": b["fecha"]}


def undo(project, batch_id: str | None = None, *, busy: frozenset = frozenset()) -> dict:
    """Deshace una corrección (la última si no se dice cuál) en los párrafos que no
    se tocaron después."""
    batches = (read_json(_undo_path(project)) or {}).get("lotes", [])
    batch = batches[-1] if batches and batch_id is None else next((b for b in batches if b["id"] == batch_id), None)
    if batch is None:
        raise ValueError(tr("Esa corrección ya no se puede deshacer."))
    if any(c["doc"] in busy for c in batch["cambios"]):
        raise ValueError(tr("Hay un capítulo traduciéndose ahora mismo. Espera a que termine."))
    restored = skipped = 0
    for index, items in groupby(sorted(batch["cambios"], key=lambda c: c["doc"]), key=lambda c: c["doc"]):
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
    batches.remove(batch)
    write_json(_undo_path(project), {"lotes": batches})
    return {"restaurados": restored, "omitidos": skipped, "deshacer": last_batch(project)}


def _summary(project, batch: dict | None, changes: list[dict]) -> dict:
    return {"corregidos": len(changes), "lote": batch["id"] if batch else None, "deshacer": last_batch(project),
            "cambios": [{"doc": c["doc"], "n": c["n"], "antes": c["antes"], "despues": c["despues"]} for c in changes]}


# -- corregir al instante ------------------------------------------------------------

def _load(project) -> tuple[qa.Checker, dict[int, dict]]:
    chapters = [project.load_chapter(i) for i in project.chapter_indices()]
    return qa.make_checker(project, chapters), {c["doc"]: c for c in chapters}


def fix(project, kind: str, items: list | None = None, *, busy: frozenset = frozenset()) -> dict:
    """Corrige sin IA los avisos de un tipo: los de ciertos párrafos (``items``:
    [[doc, n], …]) o todos los del libro."""
    if kind not in DIRECT_KINDS:
        raise ValueError(tr("Este tipo de aviso se corrige con la IA."))
    checker, chapters = _load(project)
    wanted = {(int(d), int(n)) for d, n in items} if items is not None else None
    changes: list[dict] = []
    done: set[tuple[int, int]] = set()
    for issue in checker.run():
        key = (issue.doc, issue.n)
        if issue.tipo != kind or key in done or (wanted is not None and key not in wanted):
            continue
        if issue.doc in busy:
            if wanted is not None:
                raise ValueError(tr("Este capítulo se está traduciendo ahora mismo. Espera a que termine para editarlo."))
            continue
        seg = chapters[issue.doc]["segmentos"][issue.n]
        if seg.get("sugerencia"):
            continue  # la propuesta pendiente de la IA ya trae su versión del párrafo
        new = direct_fix(checker, issue.doc, seg, kind)
        if new is None:
            continue
        done.add(key)
        changes.append({"doc": issue.doc, "n": issue.n, "antes": seg["tgt"], "despues": new})
        seg["tgt"] = new
        seg["editado"] = True
    for doc in sorted({c["doc"] for c in changes}):
        project.save_chapter(chapters[doc])
    batch = _push(project, tr("Corregir «{tipo}»", tipo=tr(qa.KINDS[kind][0])), changes)
    return _summary(project, batch, changes)


def unify_name(project, forms: list[str], keep: str, *, busy: frozenset = frozenset()) -> dict:
    """Escribe un nombre siempre igual en todo el libro: cambia las otras formas por ``keep``."""
    keep = (keep or "").strip()
    others = sorted({f.strip() for f in forms if f and f.strip() and f.strip() != keep}, key=len, reverse=True)
    if not keep or not others:
        raise ValueError(tr("Elige cómo queda escrito el nombre."))
    regex = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(f) for f in others) + r")(?!\w)")
    changes: list[dict] = []
    for index in project.chapter_indices():
        data = project.load_chapter(index)
        touched = False
        for seg in data["segmentos"]:
            if not seg.get("tgt"):
                continue
            new = edit_marked(seg["tgt"], lambda text: regex.sub(keep, text))
            if new == seg["tgt"]:
                continue
            if index in busy:
                raise ValueError(tr("Hay un capítulo traduciéndose ahora mismo. Espera a que termine."))
            changes.append({"doc": index, "n": seg["n"], "antes": seg["tgt"], "despues": new})
            seg["tgt"] = new
            seg["editado"] = True
            touched = True
        if touched:
            project.save_chapter(data)
    batch = _push(project, tr("Unificar «{nombre}»", nombre=keep), changes)
    return _summary(project, batch, changes)


# -- cómo se corrige cada aviso (para la interfaz) -----------------------------------

def _opinion(seg: dict | None, kind: str) -> str | None:
    """Lo que dijo la IA si revisó ese aviso y le pareció que estaba bien."""
    op = (seg or {}).get("qa_opinion")
    if op and op.get("tgt") == seg.get("tgt") and kind in op.get("tipos", []):
        return op.get("motivo") or ""
    return None


def check(project, only: int | None = None) -> dict:
    """El control de calidad, con lo necesario para corregir cada aviso."""
    def extra(checker, issue, seg):
        row: dict = {}
        if issue.tipo == "nombres":
            forms = checker.name_forms.get(issue.clave)
            row["arreglo"] = "nombres"
            row["formas"] = forms.most_common() if forms else []
        elif issue.tipo in DIRECT_KINDS and seg and direct_fix(checker, issue.doc, seg, issue.tipo):
            row["arreglo"] = "directo"
        else:
            row["arreglo"] = "ia"
        if seg and seg.get("sugerencia"):
            row["sugerencia"] = seg["sugerencia"]
            row["tgt"] = seg.get("tgt") or ""
        opinion = _opinion(seg, issue.tipo)
        if opinion is not None:
            row["opinion"] = opinion
        return row

    result = qa.check_project(project, only, extra=extra)
    result["deshacer"] = last_batch(project)
    return result


# -- con la IA ----------------------------------------------------------------------

def _pending_path(project):
    return project.knowledge_dir / "calidad_ia_pendiente.json"


def _seg_id(entry: dict) -> int:
    """Número de cada párrafo en las instrucciones: dice de qué capítulo y párrafo es, así una
    respuesta pegada por error (de otras instrucciones) no cae en párrafos que no se pidieron."""
    return entry["doc"] * 100000 + entry["n"]


class Fixer:
    """Pide a la IA una propuesta para los avisos que necesitan entender el texto."""

    def __init__(self, project, provider=None, *, options: Options | None = None, log=None):
        self.project = project
        self.provider = provider
        self.translator = Translator(project, provider, options=options, log=log)
        cfg = project.config
        self.system = prompts.qa_fix_system(
            title=cfg.get("titulo") or cfg.get("nombre", ""), author=cfg.get("autor", ""),
            src=project.source_lang, tgt=project.target_lang, variant=cfg.get("variante", "auto"),
            style_guide=project.style_guide(), synopsis=project.bible().get("sinopsis", ""))

    def pending(self, kinds: list[str] | None = None, items: list | None = None) -> list[dict]:
        """Párrafos con avisos para la IA (de ciertos tipos o de ciertos párrafos), cada
        uno con todos sus problemas. Se saltan los que ya tienen una propuesta, los que la
        IA ya revisó y los que se corrigen sin IA."""
        checker, chapters = _load(self.project)
        wanted = {(int(d), int(n)) for d, n in items} if items is not None else None
        entries: dict[tuple[int, int], dict] = {}
        mechanical: dict[tuple[int, int], list[str]] = {}
        for issue in checker.run():
            key = (issue.doc, issue.n)
            if issue.tipo == "nombres" or (wanted is not None and key not in wanted):
                continue
            seg = chapters[issue.doc]["segmentos"][issue.n]
            if not seg.get("tgt") or seg.get("sugerencia") or _opinion(seg, issue.tipo) is not None:
                continue
            problem = prompts.qa_problem(issue.tipo, issue.mensaje)
            if issue.tipo in DIRECT_KINDS and direct_fix(checker, issue.doc, seg, issue.tipo):
                # Se arregla sin IA; pero si el párrafo va a la IA, que lo arregle de paso.
                mechanical.setdefault(key, []).append(problem)
                continue
            if kinds and issue.tipo not in kinds:
                continue
            entry = entries.setdefault(key, {"doc": issue.doc, "n": issue.n, "src": seg["src"], "tgt": seg["tgt"],
                                             "tipos": [], "problemas": []})
            if issue.tipo not in entry["tipos"]:
                entry["tipos"].append(issue.tipo)
            entry["problemas"].append(problem)
        for key, entry in entries.items():
            entry["problemas"].extend(mechanical.get(key, []))
        return [entries[k] for k in sorted(entries)]

    @staticmethod
    def chunks(entries: list[dict], budget: int) -> list[list[dict]]:
        chunks: list[list[dict]] = []
        size = 0
        for entry in entries:
            length = len(entry["src"]) + len(entry["tgt"])
            if not chunks or (chunks[-1] and size + length > budget):
                chunks.append([])
                size = 0
            chunks[-1].append(entry)
            size += length
        return chunks

    def build_user(self, chunk: list[dict]) -> str:
        plain = "\n".join(inline.strip_marks(e["src"]) for e in chunk)
        return prompts.qa_fix_user(
            segments=[(_seg_id(e), e["src"], e["tgt"], " ".join(e["problemas"])) for e in chunk],
            glossary=self.translator.relevant_glossary(plain))

    def apply(self, chunk: list[dict], text: str) -> dict:
        """Guarda las propuestas de un bloque (si el párrafo no cambió mientras tanto)."""
        fixes, oks = prompts.parse_fixes(text), prompts.parse_oks(text)
        counts = {"sugerencias": 0, "bien": 0, "sin_respuesta": 0}
        chapters = {doc: self.project.load_chapter(doc) for doc in {e["doc"] for e in chunk}}
        for entry in chunk:
            k = _seg_id(entry)
            seg = chapters[entry["doc"]]["segmentos"][entry["n"]]
            fix = fixes.get(k)
            if seg.get("tgt") != entry["tgt"]:
                counts["sin_respuesta"] += 1
            elif fix and fix[0] and not _same(fix[0], seg["tgt"]):
                new, why = fix
                slots = pseudo_slots(seg["src"])
                warnings: list[str] = []
                if inline.problems(new, slots):
                    new, warnings = inline.repair(new, slots)
                seg["sugerencia"] = {"texto": new, "motivo": why, "avisos": warnings, "origen": "calidad"}
                seg.pop("qa_opinion", None)
                counts["sugerencias"] += 1
            elif k in oks or fix:
                seg["qa_opinion"] = {"tipos": entry["tipos"], "motivo": oks.get(k) or (fix[1] if fix else ""),
                                     "tgt": seg["tgt"]}
                counts["bien"] += 1
            else:
                counts["sin_respuesta"] += 1
        for data in chapters.values():
            self.project.save_chapter(data)
        return counts

    def run(self, entries: list[dict], *, progress=None, should_stop=None) -> dict:
        """Con la IA conectada."""
        if self.provider is None:
            raise ValueError(tr("No hay un proveedor de IA configurado."))
        chunks = self.chunks(entries, self.translator.options.chunk_chars * 2)
        total = {"sugerencias": 0, "bien": 0, "sin_respuesta": 0, "bloques": len(chunks)}
        for k, chunk in enumerate(chunks):
            if should_stop and should_stop():
                break
            if progress:
                progress(k, len(chunks))
            result = self.translator._call(self.system, self.build_user(chunk))
            self.project.record_usage(result.model or self.provider.model, result.usage)
            if not prompts.looks_like_qa_fix(result.text):
                self.translator.log(tr("La respuesta no tenía el formato esperado; esos párrafos quedan sin propuesta."))
                total["sin_respuesta"] += len(chunk)
                continue
            for name, value in self.apply(chunk, result.text).items():
                total[name] += value
        if progress:
            progress(len(chunks), len(chunks))
        return total

    # -- modo manual (copiar y pegar) ---------------------------------------------------

    def manual_prompt(self, kinds: list[str] | None = None, items: list | None = None,
                      block_chars: int | None = None) -> dict | None:
        entries = self.pending(kinds, items)
        if not entries:
            return None
        chunk = self.chunks(entries, block_chars or self.translator.manual_block_chars())[0]
        write_json(_pending_path(self.project), {
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "parrafos": [{"doc": e["doc"], "n": e["n"], "tgt": e["tgt"], "tipos": e["tipos"]} for e in chunk]})
        text = self.system + "\n\n" + self.build_user(chunk) + prompts.QA_FIX_MANUAL_OUTPUT
        return {"texto": text, "parrafos": len(chunk), "restantes": len(entries) - len(chunk), "caracteres": len(text)}

    def manual_import(self, response: str) -> dict:
        text = prompts.strip_code_fences(response)
        if not prompts.looks_like_qa_fix(text):
            raise ValueError(tr("No encontré propuestas (<fix …>) ni párrafos correctos (<ok …>) en lo que pegaste. "
                                "Copia la respuesta completa con el botón del bloque de código."))
        pending = read_json(_pending_path(self.project)) or {}
        chunk = pending.get("parrafos") or []
        if not chunk:
            raise ValueError(tr("Primero copia las instrucciones de corrección con «Copiar instrucciones»."))
        counts = self.apply(chunk, text)
        _pending_path(self.project).unlink(missing_ok=True)
        return counts
