"""Proyecto de traducción: una carpeta con el libro, las referencias, el
conocimiento (guía de estilo, glosario, resúmenes) y el progreso por capítulo.

    mi-libro/
      proyecto.json             configuración
      libro/original.epub       copia del libro a traducir
      capitulos/000.json        segmentos (original, traducción, notas, avisos)
      referencias/<nombre>/     pares emparejados de cada libro de referencia
      conocimiento/estilo.md    guía de estilo (editable)
      conocimiento/glosario.json
      conocimiento/biblia.json  sinopsis, resúmenes de capítulo y términos
      salida/                   EPUB generados

Los libros nunca deben subirse a un repositorio público: esta carpeta es local.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import align as aligner
from .epub import EpubBook
from .retrieval import Example, ExampleIndex

PROJECT_FILE = "proyecto.json"
VERSION = 1

DEFAULT_CONFIG = {
    "version": VERSION,
    "nombre": "",
    "idioma_origen": "en",
    "idioma_destino": "es",
    "variante": "auto",
    "titulo_traducido": "",
    "revision": "capitulo",  # "capitulo" | "final"
    "proveedor": {"tipo": "anthropic", "modelo": "", "esfuerzo": "medium", "base_url": None},
    "opciones": {},
    "uso": {},  # modelo -> tokens acumulados
}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:60] or "libro"


def write_json(path: Path, data) -> None:
    """Escritura atómica: nunca deja un JSON a medias si se corta la luz."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


@dataclass
class ChapterInfo:
    index: int
    title: str
    status: str  # "vacío" | "omitido" | "pendiente" | "en curso" | "traducido" | "aprobado"
    total: int
    done: int
    words: int
    in_spine: bool


class Project:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        cfg = read_json(self.root / PROJECT_FILE)
        if cfg is None:
            raise FileNotFoundError(f"No hay un proyecto en «{self.root}».")
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        merged.update(cfg)
        self.config = merged
        # La interfaz y las tareas en segundo plano pueden guardar a la vez.
        self._lock = threading.RLock()

    # -- creación -----------------------------------------------------------

    @classmethod
    def create(cls, root: str | Path, book_path: str | Path, *, target_lang: str = "es",
               variant: str = "auto", name: str | None = None) -> "Project":
        root = Path(root)
        if (root / PROJECT_FILE).exists():
            raise FileExistsError(f"Ya existe un proyecto en «{root}».")
        (root / "libro").mkdir(parents=True, exist_ok=True)
        dest = root / "libro" / "original.epub"
        shutil.copyfile(book_path, dest)
        with EpubBook(dest) as book:
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
            cfg.update({
                "nombre": name or book.title,
                "titulo": book.title,
                "autor": book.author,
                "idioma_origen": book.language or "en",
                "idioma_destino": target_lang,
                "variante": variant,
                "archivo_original": Path(book_path).name,
                "creado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            write_json(root / PROJECT_FILE, cfg)
            toc = book.toc_labels()
            for doc in book.documents:
                segs = book.segments(doc)
                write_json(root / "capitulos" / f"{doc.index:03d}.json", {
                    "doc": doc.index,
                    "ruta": doc.path,
                    "tipo": doc.kind,
                    "en_spine": doc.in_spine,
                    "titulo": book.doc_title(doc, segs, toc),
                    "aprobado": False,
                    "omitir": False,
                    "segmentos": [
                        {"n": s.n, "tag": s.tag, "src": s.source, "tgt": None, "nota": None,
                         "avisos": [], "editado": False}
                        for s in segs
                    ],
                })
        for sub in ("referencias", "conocimiento", "salida"):
            (root / sub).mkdir(exist_ok=True)
        return cls(root)

    # -- configuración -------------------------------------------------------

    def save_config(self) -> None:
        with self._lock:
            write_json(self.root / PROJECT_FILE, json.loads(json.dumps(self.config)))

    @property
    def book_path(self) -> Path:
        return self.root / "libro" / "original.epub"

    @property
    def source_lang(self) -> str:
        return self.config["idioma_origen"]

    @property
    def target_lang(self) -> str:
        return self.config["idioma_destino"]

    def record_usage(self, model: str, usage) -> None:
        with self._lock:
            acc = self.config.setdefault("uso", {}).setdefault(model, {})
            for k, v in usage.to_dict().items():
                acc[k] = acc.get(k, 0) + v
            self.save_config()

    # -- capítulos ----------------------------------------------------------

    def chapter_path(self, index: int) -> Path:
        return self.root / "capitulos" / f"{index:03d}.json"

    def chapter_indices(self) -> list[int]:
        return sorted(int(p.stem) for p in (self.root / "capitulos").glob("[0-9][0-9][0-9].json"))

    def load_chapter(self, index: int) -> dict:
        data = read_json(self.chapter_path(index))
        if data is None:
            raise KeyError(f"No existe el capítulo {index}.")
        return data

    def save_chapter(self, data: dict) -> None:
        write_json(self.chapter_path(data["doc"]), data)

    def chapter_info(self, data: dict) -> ChapterInfo:
        segs = data["segmentos"]
        done = sum(1 for s in segs if s.get("tgt"))
        if not segs:
            status = "vacío"
        elif data.get("omitir"):
            status = "omitido"
        elif data.get("aprobado"):
            status = "aprobado"
        elif done == len(segs):
            status = "traducido"
        elif done:
            status = "en curso"
        else:
            status = "pendiente"
        words = sum(len(re.findall(r"\w+", s["src"])) for s in segs)
        return ChapterInfo(data["doc"], data.get("titulo", ""), status, len(segs), done, words,
                           data.get("en_spine", True))

    def chapters(self) -> list[ChapterInfo]:
        return [self.chapter_info(self.load_chapter(i)) for i in self.chapter_indices()]

    def set_approved(self, index: int, approved: bool = True) -> None:
        data = self.load_chapter(index)
        if approved and any(not s.get("tgt") for s in data["segmentos"]):
            raise ValueError("No se puede aprobar un capítulo con párrafos sin traducir.")
        data["aprobado"] = approved
        self.save_chapter(data)

    def set_skipped(self, index: int, skipped: bool = True) -> None:
        """Marca un documento para no traducirlo (p. ej. páginas de la editorial)."""
        data = self.load_chapter(index)
        data["omitir"] = skipped
        self.save_chapter(data)

    def set_translation(self, index: int, n: int, text: str) -> None:
        """Corrección manual de un párrafo desde la revisión."""
        data = self.load_chapter(index)
        seg = data["segmentos"][n]
        seg["tgt"] = text
        seg["editado"] = True
        seg["avisos"] = []
        self.save_chapter(data)

    # -- referencias (traducciones oficiales) ---------------------------------

    def add_reference(self, original: str | Path, translation: str | Path, *, name: str | None = None,
                      progress=None) -> aligner.AlignStats:
        """Empareja un libro del autor con su traducción oficial y guarda los pares."""
        with EpubBook(original) as src_book, EpubBook(translation) as tgt_book:
            name = name or src_book.title
            src_paras = [s.plain for d in src_book.documents if d.in_spine for s in src_book.segments(d)]
            tgt_paras = [s.plain for d in tgt_book.documents if d.in_spine for s in tgt_book.segments(d)]
            info = {
                "nombre": name,
                "titulo_original": src_book.title,
                "titulo_traduccion": tgt_book.title,
                "autor": src_book.author,
                "idioma_traduccion": tgt_book.language,
            }
        pairs, stats = aligner.align(src_paras, tgt_paras, progress=progress)
        folder = self.root / "referencias" / slugify(name)
        folder.mkdir(parents=True, exist_ok=True)
        info["estadisticas"] = stats.__dict__
        write_json(folder / "info.json", info)
        with open(folder / "pares.jsonl", "w", encoding="utf-8") as f:
            for p in pairs:
                f.write(json.dumps(p.to_dict(), ensure_ascii=False) + "\n")
        return stats

    def references(self) -> list[dict]:
        out = []
        for folder in sorted((self.root / "referencias").glob("*")):
            info = read_json(folder / "info.json")
            if info:
                info["carpeta"] = folder.name
                out.append(info)
        return out

    def remove_reference(self, folder_name: str) -> None:
        shutil.rmtree(self.root / "referencias" / folder_name, ignore_errors=True)

    def reference_pairs(self, *, max_cost: float = 8.0) -> list[Example]:
        out: list[Example] = []
        for folder in sorted((self.root / "referencias").glob("*")):
            info = read_json(folder / "info.json") or {}
            path = folder / "pares.jsonl"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    p = json.loads(line)
                    if p.get("cost", 0) <= max_cost:
                        out.append(Example(p["src"], p["tgt"], info.get("nombre", folder.name)))
        return out

    def approved_pairs(self) -> list[Example]:
        """Párrafos ya aprobados de este libro: también sirven de ejemplo."""
        from .inline import strip_marks

        out = []
        for i in self.chapter_indices():
            data = self.load_chapter(i)
            if not data.get("aprobado"):
                continue
            for s in data["segmentos"]:
                if s.get("tgt") and len(s["src"]) > 60:
                    out.append(Example(strip_marks(s["src"]), strip_marks(s["tgt"]), "este libro"))
        return out

    def example_index(self) -> ExampleIndex:
        return ExampleIndex(self.reference_pairs() + self.approved_pairs())

    # -- conocimiento --------------------------------------------------------

    @property
    def knowledge_dir(self) -> Path:
        return self.root / "conocimiento"

    def style_guide(self) -> str:
        try:
            return (self.knowledge_dir / "estilo.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def set_style_guide(self, text: str) -> None:
        self.knowledge_dir.mkdir(exist_ok=True)
        (self.knowledge_dir / "estilo.md").write_text(text, encoding="utf-8")

    def glossary(self) -> list[dict]:
        return read_json(self.knowledge_dir / "glosario.json", [])

    def set_glossary(self, entries: list[dict]) -> None:
        seen: dict[str, dict] = {}
        for e in entries:
            term = (e.get("termino") or "").strip()
            if term and (e.get("traduccion") or "").strip():
                # Las decisiones del usuario mandan sobre las de la IA.
                prev = seen.get(term)
                if prev is None or e.get("origen") == "usuario" or prev.get("origen") == "propuesta":
                    seen[term] = e
        write_json(self.knowledge_dir / "glosario.json",
                   sorted(seen.values(), key=lambda e: e["termino"].lower()))

    def bible(self) -> dict:
        return read_json(self.knowledge_dir / "biblia.json", {})

    def set_bible(self, data: dict) -> None:
        write_json(self.knowledge_dir / "biblia.json", data)

    # -- exportación ---------------------------------------------------------

    def export(self, *, bilingual: bool = False, out_path: str | Path | None = None) -> tuple[Path, list[str]]:
        translations: dict[int, list[str | None]] = {}
        for i in self.chapter_indices():
            data = self.load_chapter(i)
            translations[i] = [s.get("tgt") for s in data["segmentos"]]
        title = self.config.get("titulo_traducido") or None
        stem = slugify(self.config.get("nombre") or "libro")
        suffix = f"bilingue-{self.source_lang}-{self.target_lang}" if bilingual else self.target_lang
        out = Path(out_path) if out_path else self.root / "salida" / f"{stem}.{suffix}.epub"
        with EpubBook(self.book_path) as book:
            warnings = book.write(out, translations, target_lang=self.target_lang,
                                  bilingual=bilingual, title=None if bilingual else title)
        return out, warnings
