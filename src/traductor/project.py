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


_FRONT_MATTER_RE = re.compile(
    r"©|\bISBN\b|\bcopyright\b|\bdep[oó]sito legal\b|\btel\.|\bfax\b|www\.|https?://|@\w|"
    r"\bprinted in\b|\bimpreso en\b|\ball rights reserved\b|\breservados todos\b|"
    r"\bfirst published\b|\bpublished by\b|\bt[ií]tulo original\b|\bebook edition\b",
    re.IGNORECASE)


def is_front_matter(src: str, tgt: str) -> bool:
    """Créditos, direcciones o avisos legales: no sirven como ejemplo de estilo
    y suelen quedar mal emparejados porque cada edición tiene los suyos."""
    if _FRONT_MATTER_RE.search(src) or _FRONT_MATTER_RE.search(tgt):
        return True
    ratio = (len(tgt) + 1) / (len(src) + 1)
    return not 0.4 <= ratio <= 2.5


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

    def _deleted_path(self, index: int) -> Path:
        return self.root / "capitulos" / "borrados" / f"{index:03d}.json"

    def clear_translation(self, index: int) -> int:
        """Borra toda la traducción de un capítulo y devuelve cuántos párrafos tenía.

        Antes guarda una copia (``capitulos/borrados/``) para poder recuperarla."""
        data = self.load_chapter(index)
        done = sum(1 for s in data["segmentos"] if s.get("tgt"))
        if not done:
            return 0
        write_json(self._deleted_path(index), data)
        for seg in data["segmentos"]:
            seg.update(tgt=None, avisos=[], nota=None, editado=False)
            for key in ("qa_ignorar", "sugerencia", "revisado_ia"):
                seg.pop(key, None)
        data["aprobado"] = False
        self.save_chapter(data)
        return done

    def has_deleted_translation(self, index: int) -> bool:
        return self._deleted_path(index).exists()

    def restore_translation(self, index: int) -> int:
        """Recupera la traducción borrada en los párrafos que siguen vacíos."""
        backup = read_json(self._deleted_path(index))
        if not backup:
            raise ValueError("No hay ninguna traducción borrada de este capítulo.")
        data = self.load_chapter(index)
        saved = {s["n"]: s for s in backup.get("segmentos", [])}
        restored = 0
        for seg in data["segmentos"]:
            old = saved.get(seg["n"])
            if not seg.get("tgt") and old and old.get("tgt") and old.get("src") == seg["src"]:
                for key in ("tgt", "avisos", "nota", "editado", "qa_ignorar"):
                    if key in old:
                        seg[key] = old[key]
                restored += 1
        self.save_chapter(data)
        self._deleted_path(index).unlink()
        return restored

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
                    if p.get("cost", 0) <= max_cost and not is_front_matter(p["src"], p["tgt"]):
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

    # -- metadatos de la edición traducida -------------------------------------

    META_FIELDS = ("titulo", "autor", "traductor", "editorial", "descripcion", "fecha", "temas", "serie",
                   "serie_numero", "isbn")

    def book_metadata(self) -> dict:
        """Metadatos de la edición traducida: los editados o, si faltan, los del original."""
        with EpubBook(self.book_path) as book:
            original = book.metadata()
        out = dict(original, traductor="", idioma=self.target_lang)
        out["titulo"] = self.config.get("titulo_traducido") or original["titulo"]
        out.update(self.config.get("metadatos") or {})
        return out

    def set_book_metadata(self, values: dict) -> dict:
        """Guarda los metadatos editados (solo estos se cambian en el EPUB)."""
        clean = {k: " ".join(str(values.get(k) or "").split()) for k in self.META_FIELDS if k in values}
        if "descripcion" in values:  # la descripción conserva sus saltos de párrafo
            clean["descripcion"] = "\n".join(" ".join(line.split()) for line in str(values["descripcion"] or "").splitlines()).strip()
        if clean.get("fecha") and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", clean["fecha"]):
            raise ValueError("La fecha debe ser un año (2026) o una fecha completa (2026-10-12).")
        if clean.get("isbn"):
            isbn = re.sub(r"[-\s]", "", clean["isbn"]).upper()
            if not re.fullmatch(r"\d{13}|\d{9}[\dX]", isbn):
                raise ValueError("El ISBN debe tener 10 o 13 dígitos.")
            clean["isbn"] = isbn
        if clean.get("serie_numero") and not re.fullmatch(r"\d+([.,]\d+)?", clean["serie_numero"]):
            raise ValueError("El número en la serie debe ser un número.")
        saved = {k: v for k, v in {**(self.config.get("metadatos") or {}), **clean}.items() if v}
        self.config["metadatos"] = saved
        if "titulo" in clean:
            self.config["titulo_traducido"] = clean["titulo"]
        self.save_config()
        return self.book_metadata()

    def export(self, *, bilingual: bool = False, out_path: str | Path | None = None) -> tuple[Path, list[str]]:
        translations: dict[int, list[str | None]] = {}
        for i in self.chapter_indices():
            data = self.load_chapter(i)
            translations[i] = [s.get("tgt") for s in data["segmentos"]]
        title = self.config.get("titulo_traducido") or None
        edited = dict(self.config.get("metadatos") or {})
        if bilingual:
            edited.pop("titulo", None)  # la edición bilingüe conserva el título original
        stem = slugify(self.config.get("nombre") or "libro")
        suffix = f"bilingue-{self.source_lang}-{self.target_lang}" if bilingual else self.target_lang
        out = Path(out_path) if out_path else self.root / "salida" / f"{stem}.{suffix}.epub"
        with EpubBook(self.book_path) as book:
            warnings = book.write(out, translations, target_lang=self.target_lang,
                                  bilingual=bilingual, title=None if bilingual else title,
                                  cover=None if bilingual else self.cover_maker(), metadata=edited)
        return out, warnings

    @property
    def own_cover_path(self) -> Path:
        return self.root / "portada" / "propia.jpg"

    def set_own_cover(self, data: bytes) -> None:
        """Guarda una imagen de portada hecha por la persona (JPG, PNG o WEBP)."""
        import io

        from PIL import Image

        try:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                img = im.convert("RGB")
        except Exception as e:
            raise ValueError("No se pudo leer la imagen: usa un archivo JPG, PNG o WEBP.") from e
        if min(img.size) < 300:
            raise ValueError("La imagen es muy pequeña: usa una de al menos 300 píxeles de ancho.")
        if img.height > 3200:
            img = img.resize((round(img.width * 3200 / img.height), 3200), Image.LANCZOS)
        self.own_cover_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(self.own_cover_path, "JPEG", quality=92, optimize=True)
        cover = dict(self.config.get("portada") or {}, usar=True, estilo="propia")
        self.config["portada"] = cover
        self.save_config()

    def remove_own_cover(self) -> None:
        self.own_cover_path.unlink(missing_ok=True)
        cover = dict(self.config.get("portada") or {})
        if cover.get("estilo") == "propia":
            cover["estilo"] = "difuminada"
        self.config["portada"] = cover
        self.save_config()

    def cover_settings(self) -> dict:
        """Portada traducida: si se usa, estilo, título y autor que se muestran."""
        saved = self.config.get("portada") or {}
        meta = self.book_metadata()
        style = saved.get("estilo") or "difuminada"
        if style == "propia" and not self.own_cover_path.exists():
            style = "difuminada"
        return {"usar": bool(saved.get("usar")), "estilo": style,
                "titulo": saved.get("titulo") or meta["titulo"], "autor": saved.get("autor") or meta["autor"],
                "tiene_propia": self.own_cover_path.exists()}

    def cover_maker(self, settings: dict | None = None):
        """Función que crea la portada nueva para ``EpubBook.write`` (o None)."""
        from .cover import make_cover

        from .cover import unsupported_chars

        cfg = settings or self.cover_settings()
        if not cfg["usar"]:
            return None
        if cfg["estilo"] == "propia":
            own = self.own_cover_path.read_bytes()

            def convert(fmt, _original, _aspect):
                import io

                from PIL import Image

                if fmt.upper() == "JPEG":
                    return own
                buf = io.BytesIO()
                with Image.open(io.BytesIO(own)) as im:
                    im.save(buf, "PNG", optimize=True)
                return buf.getvalue()

            return convert
        if unsupported_chars(cfg["titulo"] + cfg["autor"]):
            return None  # letras que la tipografía no tiene: se queda la original
        return lambda fmt, original, aspect: make_cover(cfg["titulo"], cfg["autor"], style=cfg["estilo"],
                                                          original=original, fmt=fmt, aspect=aspect)
