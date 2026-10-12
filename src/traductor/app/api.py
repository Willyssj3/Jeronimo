"""Funciones que la interfaz llama (por pywebview o por HTTP local).

Todas reciben y devuelven datos simples (texto, números, listas, diccionarios).
Las operaciones largas (emparejar, preparar, traducir) corren en segundo plano
y la interfaz consulta su progreso con ``task_status``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback
import webbrowser
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from .. import __version__, inline, knowledge, qa, updates
from ..epub import EpubBook, EpubError
from ..pdf2epub import PdfError
from ..inline import localname
from ..project import Project, read_json, slugify, write_json
from ..prompts import LANG_NAMES, VARIANTS
from ..providers import KINDS, ProviderError, config_dir, get_api_key, make_provider, save_api_key
from ..providers.anthropic_provider import PRICES, SUGGESTED_MODELS, estimate_cost
from ..providers.base import Usage
from ..translate import CHAT_PRESETS, DEFAULT_MANUAL_BLOCK, Options, Translator, pseudo_slots

LOCAL_KINDS = {"ollama", "lmstudio", "manual"}


class TaskRunner:
    """Una tarea larga a la vez, con progreso, registro y cancelación."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.state = self._empty()

    @staticmethod
    def _empty() -> dict:
        return {"activa": False, "nombre": "", "progreso": 0.0, "mensaje": "", "registro": [],
                "error": None, "resultado": None, "capitulo": None, "terminada": None}

    def start(self, name: str, fn) -> None:
        with self._lock:
            if self.state["activa"]:
                raise ValueError(f"Ya hay una tarea en curso: {self.state['nombre']}. Espera o detenla.")
            self.state = self._empty()
            self.state.update(activa=True, nombre=name, mensaje="Empezando…")
            self._cancel.clear()
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()

    def _run(self, fn) -> None:
        try:
            result = fn(self)
            self.state["resultado"] = result
            self.state["mensaje"] = "Detenida." if self._cancel.is_set() else "Terminada."
            if not self._cancel.is_set():
                self.state["progreso"] = 1.0
        except (ProviderError, EpubError, PdfError, ValueError, KeyError, FileNotFoundError) as e:
            self.state["error"] = str(e)
            self.log(f"Error: {e}")
        except Exception as e:  # pragma: no cover - errores imprevistos
            self.state["error"] = f"Error inesperado: {e}"
            self.log(self.state["error"])
            _log_error(traceback.format_exc())
        finally:
            self.state["activa"] = False
            self.state["capitulo"] = None
            self.state["terminada"] = datetime.now().isoformat(timespec="seconds")

    def log(self, msg: str) -> None:
        reg = self.state["registro"]
        reg.append(f"{datetime.now():%H:%M:%S}  {msg}")
        del reg[:-300]
        self.state["mensaje"] = msg

    def progress(self, fraction: float, message: str | None = None) -> None:
        self.state["progreso"] = max(0.0, min(1.0, fraction))
        if message:
            self.state["mensaje"] = message

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        if self.state["activa"]:
            self._cancel.set()
            self.log("Deteniendo al terminar el bloque actual…")


def _log_error(text: str) -> None:
    try:
        with open(config_dir() / "registro.txt", "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n{text}\n")
    except OSError:
        pass


def default_projects_dir() -> Path:
    docs = Path.home() / "Documents"
    base = docs if docs.is_dir() else Path.home()
    return base / "TraductorDeLibros"


class Api:
    def __init__(self, mode: str = "escritorio", provider_factory=None):
        self._mode = mode
        self._window = None  # ventana de pywebview (para los diálogos de archivos)
        self._project: Project | None = None
        self._tasks = TaskRunner()
        self._provider_factory = provider_factory or make_provider
        self._marks_cache: dict[int, dict[int, dict[int, str]]] = {}

    # -- ajustes y proyectos recientes ---------------------------------------

    def _settings_path(self) -> Path:
        return config_dir() / "ajustes.json"

    def _settings(self) -> dict:
        return read_json(self._settings_path(), {}) or {}

    def _save_settings(self, data: dict) -> None:
        write_json(self._settings_path(), data)

    def _remember(self, folder: Path, name: str) -> None:
        data = self._settings()
        recent = [r for r in data.get("recientes", []) if r.get("carpeta") != str(folder)]
        recent.insert(0, {"carpeta": str(folder), "nombre": name})
        data["recientes"] = recent[:12]
        self._save_settings(data)

    def app_info(self) -> dict:
        return {
            "version": __version__,
            "modo": self._mode,
            "proveedores": KINDS,
            "idiomas": {k: v[1] for k, v in LANG_NAMES.items()},
            "variantes": list(VARIANTS),
            "modelos_claude": SUGGESTED_MODELS,
            "chats": [{"id": k, "nombre": v[0], "bloque": v[1]} for k, v in CHAT_PRESETS.items()],
            "carpeta_por_defecto": str(default_projects_dir()),
            "recientes": [dict(r, existe=(Path(r["carpeta"]) / "proyecto.json").exists())
                          for r in self._settings().get("recientes", [])],
            "proyecto_abierto": str(self._project.root) if self._project else None,
        }

    def choose_path(self, kind: str = "epub", directory: str = "") -> str | None:
        """Diálogo nativo para elegir archivo o carpeta (solo en modo escritorio)."""
        if self._window is None:
            return None
        import webview

        if kind == "carpeta":
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=directory)
        elif kind == "pdf":
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, directory=directory, file_types=("Documentos PDF (*.pdf)", "Todos (*.*)"))
        else:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, directory=directory, file_types=("Libros EPUB (*.epub)", "Todos (*.*)"))
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else str(result)

    def quit_app(self) -> bool:
        """Cierra la app en modo navegador (en modo ventana basta con cerrarla)."""
        if self._tasks.state["activa"]:
            raise ValueError("Hay una tarea en curso. Detenla antes de salir.")
        threading.Timer(0.5, os._exit, [0]).start()
        return True

    # -- versiones nuevas ---------------------------------------------------------

    def check_update(self, force: bool = False) -> dict | None:
        """La versión publicada más reciente, si es más nueva que esta.

        Consulta GitHub como mucho una vez al día; sin conexión, no avisa."""
        settings = self._settings()
        saved = settings.get("actualizacion") or {}
        today = date.today().isoformat()
        latest = saved.get("ultima")
        if force or saved.get("consultado") != today:
            try:
                latest = updates.latest_release()
            except Exception:  # sin internet, GitHub no responde…: no molesta a nadie
                latest = None
            else:
                settings["actualizacion"] = {"consultado": today, "ultima": latest}
                self._save_settings(settings)
        if latest and updates.is_newer(latest.get("version", ""), __version__):
            return latest
        return None

    def open_url(self, url: str) -> bool:
        """Abre en el navegador una página del proyecto (descargas, ayuda)."""
        if not (url == updates.REPO_URL or url.startswith(updates.REPO_URL + "/")):
            raise ValueError("Solo se pueden abrir páginas del proyecto Jerónimo.")
        return bool(webbrowser.open(url))

    def open_folder(self, path: str) -> bool:
        target = Path(path)
        folder = target if target.is_dir() else target.parent
        try:
            if sys.platform.startswith("win"):
                if target.is_file():
                    subprocess.Popen(["explorer", "/select,", str(target)])
                else:
                    os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
            return True
        except OSError:
            return False

    # -- PDF a EPUB -------------------------------------------------------------

    def convert_pdf(self, pdf_path: str, out_path: str = "") -> dict:
        """Convierte un PDF propio en EPUB (en segundo plano)."""
        from ..pdf2epub import convert

        pdf = Path(pdf_path.strip().strip('"'))
        if not pdf.is_file():
            raise FileNotFoundError(f"No encuentro el archivo «{pdf}».")
        out = Path(out_path.strip().strip('"')) if out_path and out_path.strip() else pdf.with_suffix(".epub")

        def run(task: TaskRunner):
            task.log(f"Convirtiendo «{pdf.name}»…")
            path, report = convert(pdf, out, progress=task.progress)
            task.log(f"Listo: {report.chapters} capítulos, {report.notes} notas, "
                     f"{report.removed_lines} encabezados o números de página quitados.")
            for w in report.warnings:
                task.log(f"Aviso: {w}")
            return {"ruta": str(path), "paginas": report.pages, "capitulos": report.chapters,
                    "notas": report.notes, "lineas_quitadas": report.removed_lines,
                    "paginas_omitidas": report.dropped_pages, "avisos": report.warnings,
                    "titulo": report.title, "autor": report.author, "idioma": report.language}

        self._tasks.start("Convertir PDF a EPUB", run)
        return self.task_status()

    # -- proyectos -----------------------------------------------------------

    def _need_project(self) -> Project:
        if self._project is None:
            raise ValueError("No hay ningún proyecto abierto.")
        return self._project

    def create_project(self, epub_path: str, folder: str = "", target_lang: str = "es",
                       variant: str = "auto") -> dict:
        epub = Path(epub_path.strip().strip('"'))
        if not epub.is_file():
            raise FileNotFoundError(f"No encuentro el archivo «{epub}».")
        if not folder:
            with EpubBook(epub) as book:
                folder = str(default_projects_dir() / slugify(book.title))
        target = Path(folder.strip().strip('"'))
        project = Project.create(target, epub, target_lang=target_lang, variant=variant)
        default_provider = self._settings().get("proveedor")
        if default_provider:
            project.config["proveedor"] = dict(default_provider)
            project.save_config()
        self._open(project)
        return self.project_summary()

    def open_project(self, folder: str) -> dict:
        self._open(Project(Path(folder.strip().strip('"'))))
        return self.project_summary()

    def _open(self, project: Project) -> None:
        self._project = project
        self._marks_cache.clear()
        self._remember(project.root, project.config.get("nombre", project.root.name))

    def close_project(self) -> bool:
        if self._tasks.state["activa"]:
            raise ValueError("Espera a que termine la tarea en curso antes de cerrar el proyecto.")
        self._project = None
        return True

    def set_settings(self, values: dict) -> dict:
        p = self._need_project()
        for key in ("nombre", "variante", "titulo_traducido", "revision", "idioma_destino"):
            if key in values:
                p.config[key] = values[key]
        if "chat_manual" in values:
            chat = values["chat_manual"]
            if chat not in CHAT_PRESETS:
                raise ValueError("Chat desconocido.")
            opts = p.config.setdefault("opciones", {})
            opts["chat_manual"] = chat
            if "bloque_manual" not in values:
                opts["bloque_manual"] = CHAT_PRESETS[chat][1]
        if "bloque_manual" in values:
            size = int(values["bloque_manual"])
            if not 1000 <= size <= 60000:
                raise ValueError("El tamaño de bloque debe estar entre 1.000 y 60.000 caracteres.")
            p.config.setdefault("opciones", {})["bloque_manual"] = size
        p.save_config()
        return self.project_summary()

    def project_summary(self) -> dict:
        p = self._need_project()
        cfg = p.config
        chapters = p.chapters()
        active = [c for c in chapters if c.total and c.status != "omitido"]
        prov = dict(cfg.get("proveedor") or {})
        kind = prov.get("tipo") or "anthropic"
        usage = []
        for model, u in (cfg.get("uso") or {}).items():
            usage.append({"modelo": model, **u, "coste": estimate_cost(model, Usage(**u))})
        return {
            "carpeta": str(p.root),
            "nombre": cfg.get("nombre"),
            "titulo": cfg.get("titulo"),
            "autor": cfg.get("autor"),
            "idioma_origen": p.source_lang,
            "idioma_destino": p.target_lang,
            "variante": cfg.get("variante"),
            "titulo_traducido": cfg.get("titulo_traducido", ""),
            "revision": cfg.get("revision", "capitulo"),
            "bloque_manual": (cfg.get("opciones") or {}).get("bloque_manual") or DEFAULT_MANUAL_BLOCK,
            "chat_manual": (cfg.get("opciones") or {}).get("chat_manual") or "claude",
            "proveedor": prov,
            "tiene_clave": kind in LOCAL_KINDS or bool(get_api_key(kind)),
            "capitulos": [asdict(c) for c in chapters],
            "progreso": {
                "hechos": sum(c.done for c in active),
                "total": sum(c.total for c in active),
                "palabras": sum(c.words for c in active),
                "caps_aprobados": sum(1 for c in active if c.status == "aprobado"),
                "caps_total": len(active),
            },
            "referencias": p.references(),
            "estilo": bool(p.style_guide().strip()),
            "glosario": len(p.glossary()),
            "sinopsis": bool((p.bible().get("sinopsis") or "").strip()),
            "uso": usage,
            "estimacion": self.estimate(),
        }

    # -- referencias ---------------------------------------------------------

    def add_reference(self, original: str, translation: str, name: str = "") -> dict:
        p = self._need_project()
        for path in (original, translation):
            if not Path(path.strip().strip('"')).is_file():
                raise FileNotFoundError(f"No encuentro el archivo «{path}».")

        def run(task: TaskRunner):
            task.log("Leyendo los dos libros…")
            stats = p.add_reference(original.strip().strip('"'), translation.strip().strip('"'),
                                    name=name or None,
                                    progress=lambda f: task.progress(f, "Emparejando párrafos…"))
            task.log(f"Listo: {stats.pairs} pares, cobertura {stats.coverage:.0%}, calidad {stats.quality}.")
            return asdict(stats)

        self._tasks.start("Emparejar referencia", run)
        return self.task_status()

    def remove_reference(self, folder_name: str) -> dict:
        self._need_project().remove_reference(folder_name)
        return self.project_summary()

    # -- proveedor de IA -------------------------------------------------------

    def set_provider(self, cfg: dict, api_key: str = "") -> dict:
        p = self._need_project()
        clean = {
            "tipo": cfg.get("tipo") or "anthropic",
            "modelo": (cfg.get("modelo") or "").strip(),
            "esfuerzo": cfg.get("esfuerzo") or "medium",
            "base_url": (cfg.get("base_url") or "").strip() or None,
        }
        if cfg.get("num_ctx"):
            clean["num_ctx"] = int(cfg["num_ctx"])
        p.config["proveedor"] = clean
        p.save_config()
        settings = self._settings()
        settings["proveedor"] = clean
        self._save_settings(settings)
        where = None
        if api_key and api_key.strip():
            where = save_api_key(clean["tipo"], api_key.strip())
        return {"guardado": True, "clave_en": where,
                "tiene_clave": clean["tipo"] in LOCAL_KINDS or bool(get_api_key(clean["tipo"]))}

    def _provider(self):
        cfg = self._need_project().config["proveedor"]
        if cfg.get("tipo") == "manual":
            raise ValueError("Estás en modo manual: en la revisión del capítulo usa «Copiar bloque» y "
                             "«Pegar respuesta», o elige una IA con clave en «Inteligencia artificial».")
        return self._provider_factory(cfg)

    def list_models(self) -> list[str]:
        return self._provider().list_models()

    def test_provider(self) -> dict:
        provider = self._provider()
        result = provider.complete("Answer with exactly one word.", "Say: OK", max_tokens=2000)
        self._need_project().record_usage(result.model or provider.model, result.usage)
        return {"ok": True, "respuesta": result.text.strip()[:200], "modelo": result.model or provider.model}

    # -- conocimiento ----------------------------------------------------------

    def knowledge(self) -> dict:
        p = self._need_project()
        bible = p.bible()
        summaries = bible.get("capitulos", {})
        return {
            "estilo": p.style_guide(),
            "glosario": p.glossary(),
            "sinopsis": bible.get("sinopsis", ""),
            "capitulos": [{"index": c.index, "titulo": c.title, "resumen": summaries.get(str(c.index), "")}
                          for c in p.chapters() if c.total and c.in_spine],
        }

    def save_style(self, text: str) -> bool:
        self._need_project().set_style_guide(text)
        return True

    def save_synopsis(self, text: str, chapter_summaries: dict | None = None) -> bool:
        p = self._need_project()
        bible = p.bible()
        bible["sinopsis"] = text
        if chapter_summaries is not None:
            bible["capitulos"] = {str(k): v for k, v in chapter_summaries.items() if (v or "").strip()}
        p.set_bible(bible)
        return True

    def save_glossary(self, entries: list[dict]) -> int:
        p = self._need_project()
        clean = []
        for e in entries:
            term, tr = (e.get("termino") or "").strip(), (e.get("traduccion") or "").strip()
            if term and tr:
                clean.append({"termino": term, "traduccion": tr, "tipo": (e.get("tipo") or "").strip(),
                              "nota": (e.get("nota") or "").strip(), "origen": e.get("origen") or "usuario"})
        # Lo que se guarda desde la interfaz reemplaza el glosario completo.
        write_json(p.knowledge_dir / "glosario.json", [])
        p.set_glossary(clean)
        return len(p.glossary())

    def prepare(self, steps: list[str]) -> dict:
        p = self._need_project()
        provider = self._provider()
        valid = [s for s in ("estilo", "glosario", "analisis", "terminos") if s in steps]
        if not valid:
            raise ValueError("Elige al menos un paso.")
        if any(s in valid for s in ("estilo", "glosario")) and not p.references():
            raise ValueError("Para la guía de estilo y el glosario oficial primero añade un libro de referencia.")

        def run(task: TaskRunner):
            for i, step in enumerate(valid):
                if task.cancelled():
                    break
                task.progress(i / len(valid))
                knowledge.prepare(p, provider, steps=(step,), log=task.log)
            return {"pasos": valid}

        self._tasks.start("Preparar conocimiento", run)
        return self.task_status()

    # -- capítulos y revisión --------------------------------------------------

    def _marks(self, index: int) -> dict[int, dict[int, str]]:
        """Etiqueta HTML de cada marca de cada párrafo (para mostrar el formato)."""
        if index in self._marks_cache:
            return self._marks_cache[index]
        p = self._need_project()
        marks: dict[int, dict[int, str]] = {}
        with EpubBook(p.book_path) as book:
            doc = next((d for d in book.documents if d.index == index), None)
            if doc is not None and doc.kind == "xhtml":
                _tree, elements = book._load(doc)
                for n, el in enumerate(elements):
                    m = inline.to_marked(el)
                    marks[n] = {k: localname(v[1]) for k, v in m.slots.items()}
        self._marks_cache[index] = marks
        return marks

    def chapter(self, index: int) -> dict:
        p = self._need_project()
        data = p.load_chapter(index)
        marks = self._marks(index)
        info = p.chapter_info(data)
        return {
            "index": index,
            "titulo": data.get("titulo", ""),
            "estado": info.status,
            "aprobado": bool(data.get("aprobado")),
            "omitir": bool(data.get("omitir")),
            "total": info.total,
            "hechos": info.done,
            "resumen": p.bible().get("capitulos", {}).get(str(index), ""),
            "bloqueado": self._tasks.state["activa"] and self._tasks.state.get("capitulo") == index,
            "copia_borrada": p.has_deleted_translation(index),
            "segmentos": [
                {"n": s["n"], "tag": s.get("tag", ""), "src": s["src"], "tgt": s.get("tgt"),
                 "nota": s.get("nota"), "avisos": s.get("avisos", []), "editado": s.get("editado", False),
                 "marcas": {str(k): v for k, v in marks.get(s["n"], {}).items()}}
                for s in data["segmentos"]
            ],
        }

    def _check_not_busy(self, index: int) -> None:
        if self._tasks.state["activa"] and self._tasks.state.get("capitulo") == index:
            raise ValueError("Este capítulo se está traduciendo ahora mismo. Espera a que termine para editarlo.")

    def save_segment(self, index: int, n: int, text: str) -> dict:
        self._check_not_busy(index)
        p = self._need_project()
        data = p.load_chapter(index)
        seg = data["segmentos"][n]
        text = inline.WS_RE.sub(" ", text or "").strip()
        warnings: list[str] = []
        if not text:
            seg["tgt"] = None
        else:
            slots = pseudo_slots(seg["src"])
            if inline.problems(text, slots):
                text, warnings = inline.repair(text, slots)
            seg["tgt"] = text
        seg["editado"] = True
        seg["avisos"] = warnings
        if data.get("aprobado") and not seg["tgt"]:
            data["aprobado"] = False
        p.save_chapter(data)
        return {"tgt": seg["tgt"], "avisos": warnings}

    def retranslate_segment(self, index: int, n: int) -> dict:
        self._check_not_busy(index)
        p = self._need_project()
        translator = Translator(p, self._provider())
        data = p.load_chapter(index)
        seg = data["segmentos"][n]
        previous = seg.get("tgt")
        seg["tgt"] = None
        translator._translate_chunk(data, [seg])
        if not seg.get("tgt"):
            seg["tgt"] = previous
            raise ProviderError("La IA no devolvió la traducción de este párrafo. Prueba otra vez.")
        p.save_chapter(data)
        return {"tgt": seg["tgt"], "avisos": seg.get("avisos", []), "nota": seg.get("nota")}

    def clear_translation(self, index: int) -> dict:
        """Borra toda la traducción de un capítulo (se guarda una copia)."""
        self._check_not_busy(index)
        deleted = self._need_project().clear_translation(index)
        return {"borrados": deleted, "resumen": self.project_summary()}

    def restore_translation(self, index: int) -> dict:
        """Recupera la traducción borrada de un capítulo."""
        self._check_not_busy(index)
        restored = self._need_project().restore_translation(index)
        return {"recuperados": restored, "resumen": self.project_summary()}

    def set_approved(self, index: int, approved: bool = True) -> dict:
        self._need_project().set_approved(index, approved)
        return self.chapter(index)

    def set_skipped(self, index: int, skipped: bool = True) -> dict:
        self._need_project().set_skipped(index, skipped)
        return self.project_summary()

    def translate(self, mode: str = "siguiente", index: int | None = None) -> dict:
        p = self._need_project()
        provider = self._provider()  # comprueba clave y modelo antes de empezar

        def run(task: TaskRunner):
            translator = Translator(p, provider, log=task.log)
            if mode == "capitulo" and index is not None:
                targets = [index]
            elif mode == "todo":
                targets = [c.index for c in p.chapters()
                           if c.total and c.done < c.total and c.status != "omitido"]
            else:
                nxt = translator.next_chapter()
                targets = [nxt] if nxt is not None else []
            if not targets:
                task.log("No quedan capítulos pendientes.")
                return {"capitulos": []}
            reports = []
            for k, idx in enumerate(targets):
                if task.cancelled():
                    break
                data = p.load_chapter(idx)
                title = data.get("titulo", "")
                task.state["capitulo"] = idx

                def prog(done, total, k=k, title=title):
                    task.progress((k + done / max(1, total)) / len(targets),
                                  f"«{title}»: {done} de {total} párrafos")

                report = translator.translate_chapter(idx, progress=prog, should_stop=task.cancelled)
                reports.append(asdict(report))
                if report.missing:
                    task.log(f"«{title}»: quedaron {len(report.missing)} párrafos sin traducir; reintenta o tradúcelos a mano.")
                else:
                    task.log(f"«{title}» traducido. Revísalo y apruébalo.")
            return {"capitulos": reports}

        self._tasks.start("Traducción", run)
        return self.task_status()

    def _manual_state(self, translator: Translator, index: int) -> dict:
        data = self._need_project().load_chapter(index)
        return {"bloques_restantes": len(translator.manual_chunks(data)),
                "parrafos_pendientes": sum(1 for s in data["segmentos"] if not s.get("tgt"))}

    def manual_status(self, index: int) -> dict:
        translator = Translator(self._need_project(), None)
        return dict(self._manual_state(translator, index), bloque=translator.manual_block_chars())

    def manual_prompt(self, index: int) -> dict | None:
        self._check_not_busy(index)
        translator = Translator(self._need_project(), None)
        res = translator.manual_prompt(index)
        if res is None:
            return None
        text, ids, blocks = res
        return {"texto": text, "ids": ids, "caracteres": len(text), "bloques_restantes": blocks}

    def manual_import(self, index: int, text: str) -> dict:
        self._check_not_busy(index)
        translator = Translator(self._need_project(), None)
        saved, missing = translator.manual_import(index, text)
        if saved == 0:
            raise ValueError("No encontré párrafos traducidos en lo que pegaste. Copia la respuesta completa "
                             "(con el botón «Copiar» del bloque de código del chat).")
        return dict({"guardados": saved, "faltan": missing}, **self._manual_state(translator, index))

    # -- preparación en modo manual (copiar y pegar en cualquier chat) ----------

    def _manual_prep(self):
        return knowledge.ManualPrep(self._need_project())

    def manual_prep_status(self) -> dict | None:
        return self._manual_prep().status()

    def manual_prep_start(self, step: str) -> dict:
        if self._tasks.state["activa"]:
            raise ValueError("Espera a que termine la tarea en curso.")
        return self._manual_prep().start(step)

    def manual_prep_prompt(self) -> dict:
        return self._manual_prep().prompt()

    def manual_prep_import(self, text: str) -> dict:
        return self._manual_prep().import_response(text)

    def manual_prep_cancel(self) -> bool:
        self._manual_prep().cancel()
        return True

    # -- control de calidad (sin IA) -------------------------------------------

    def quality(self, index: int | None = None) -> dict:
        """Avisos de calidad de todo el libro o de un capítulo."""
        return qa.check_project(self._need_project(), index)

    def quality_ignore(self, index: int, n: int, kind: str, key: str = "") -> bool:
        """No volver a mostrar ese aviso (en ese párrafo, o ese nombre en todo el libro)."""
        self._check_not_busy(index)
        qa.ignore(self._need_project(), index, n, kind, key)
        return True

    # -- exportación -----------------------------------------------------------

    def export(self, bilingual: bool = False) -> dict:
        out, warnings = self._need_project().export(bilingual=bilingual)
        return {"ruta": str(out), "avisos": warnings[:50], "total_avisos": len(warnings)}

    # -- coste ------------------------------------------------------------------

    def estimate(self) -> dict:
        """Estimación aproximada de lo que falta por traducir y su coste."""
        p = self._need_project()
        chars = words = 0
        for i in p.chapter_indices():
            data = p.load_chapter(i)
            if data.get("omitir"):
                continue
            for s in data["segmentos"]:
                if not s.get("tgt"):
                    plain = inline.strip_marks(s["src"])
                    chars += len(plain)
                    words += len(plain.split())
        prov = p.config.get("proveedor") or {}
        kind = prov.get("tipo") or "anthropic"
        local = kind in ("ollama", "lmstudio")
        opts = Options.for_provider(None, None) if not local else Options(chunk_chars=2000)
        calls = max(1, round(chars / opts.chunk_chars)) if chars else 0
        src_tokens = chars / 3.8
        overhead = 700 if local else 2200
        tokens_in = int(src_tokens + calls * overhead)
        cache_read = int(calls * 2000) if kind == "anthropic" else 0
        thinking = 1.6 if kind == "anthropic" else 1.3
        tokens_out = int(src_tokens * thinking)
        model = prov.get("modelo") or ("claude-opus-5-5" if kind == "anthropic" else "")
        cost = None
        if kind == "anthropic" and model in PRICES:
            cost = estimate_cost(model, Usage(input_tokens=tokens_in, output_tokens=tokens_out,
                                              cache_read_tokens=cache_read))
        elif local or kind == "manual":
            cost = 0.0
        return {"palabras": words, "llamadas": calls, "tokens_entrada": tokens_in,
                "tokens_salida": tokens_out, "coste_usd": cost, "modelo": model}

    # -- tareas -------------------------------------------------------------------

    def task_status(self) -> dict:
        state = dict(self._tasks.state)
        state["registro"] = state["registro"][-120:]
        return state

    def cancel_task(self) -> dict:
        self._tasks.cancel()
        return self.task_status()


class Bridge:
    """Único objeto expuesto a JavaScript: despacha ``call(nombre, args)`` y
    devuelve siempre ``{ok, resultado | error}``."""

    def __init__(self, api: Api):
        self._api = api

    def call(self, name: str, args: list | None = None):
        if name.startswith("_") or not hasattr(Api, name):
            return {"ok": False, "error": f"Función desconocida: {name}"}
        try:
            return {"ok": True, "resultado": getattr(self._api, name)(*(args or []))}
        except (ProviderError, EpubError, PdfError, ValueError, KeyError, FileNotFoundError, FileExistsError) as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # pragma: no cover
            _log_error(traceback.format_exc())
            return {"ok": False, "error": f"Error inesperado: {e}"}
