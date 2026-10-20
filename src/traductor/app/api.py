"""Funciones que la interfaz llama (por pywebview o por HTTP local).

Todas reciben y devuelven datos simples (texto, números, listas, diccionarios).
Las operaciones largas (emparejar, preparar, traducir) corren en segundo plano
y la interfaz consulta su progreso con ``task_status``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from .. import __version__, components, feedback, i18n, inline, knowledge, profiles, qa, qa_fix, review, support, updates
from .. import search as finder
from ..epub import EpubBook, EpubError
from ..i18n import N_, tr
from ..pdf2epub import PdfError
from ..inline import localname
from ..project import Project, read_json, slugify, write_json
from ..prompts import LANG_NAMES, VARIANTS
from ..providers import KINDS, ProviderError, config_dir, get_api_key, make_provider, save_api_key
from ..providers.anthropic_provider import PRICES, SUGGESTED_MODELS, estimate_cost
from ..providers.base import Usage
from ..review import Reviewer
from ..translate import (CHAT_PRESETS, DEFAULT_MANUAL_BLOCK, PLANS, Options, Translator, chat_models, model_tier,
                         pseudo_slots, recommended_block)

LOCAL_KINDS = {"ollama", "lmstudio", "manual"}


class TaskRunner:
    """Una tarea larga a la vez, con progreso, registro y cancelación."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.state = self._empty()

    @staticmethod
    def _empty() -> dict:
        return {"activa": False, "nombre": "", "clave": "", "progreso": 0.0, "mensaje": "", "registro": [],
                "error": None, "resultado": None, "capitulo": None, "terminada": None}

    def start(self, name: str, fn, key: str = "") -> None:
        """``name`` es el nombre visible (en español, se traduce) y ``key`` una clave fija
        para que la interfaz reconozca la tarea sin depender del idioma."""
        with self._lock:
            if self.state["activa"]:
                raise ValueError(tr("Ya hay una tarea en curso: {nombre}. Espera o detenla.", nombre=self.state['nombre']))
            self.state = self._empty()
            self.state.update(activa=True, nombre=tr(name), clave=key, mensaje=tr("Empezando…"))
            self._cancel.clear()
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()

    def _run(self, fn) -> None:
        try:
            result = fn(self)
            self.state["resultado"] = result
            self.state["mensaje"] = tr("Detenida.") if self._cancel.is_set() else tr("Terminada.")
            if not self._cancel.is_set():
                self.state["progreso"] = 1.0
        except (ProviderError, EpubError, PdfError, ValueError, KeyError, FileNotFoundError) as e:
            self.state["error"] = str(e)
            self.log(tr("Error: {error}", error=e))
        except Exception as e:  # pragma: no cover - errores imprevistos
            self.state["error"] = tr("Error inesperado: {error}", error=e)
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
            self.log(tr("Deteniendo al terminar el bloque actual…"))


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
        self._ready_update: tuple[Path, Path, str] | None = None  # versión nueva ya descargada
        self._settings_lock = threading.RLock()

    # -- ajustes y proyectos recientes ---------------------------------------

    def _settings_path(self) -> Path:
        return config_dir() / "ajustes.json"

    def _settings(self) -> dict:
        return read_json(self._settings_path(), {}) or {}

    def _save_settings(self, data: dict) -> None:
        write_json(self._settings_path(), data)

    def _update_settings(self, change) -> dict:
        """Lee, cambia y guarda los ajustes de una sola vez. La interfaz hace varias llamadas a la
        vez (la consulta de versión nueva puede tardar mientras se abre un proyecto) y ninguna
        tiene que pisar lo que guardó otra."""
        with self._settings_lock:
            data = self._settings()
            change(data)
            self._save_settings(data)
            return data

    def _remember(self, folder: Path, name: str, position: int = 0) -> None:
        def change(data):
            recent = [r for r in data.get("recientes", []) if r.get("carpeta") != str(folder)]
            recent.insert(max(0, min(position, len(recent))), {"carpeta": str(folder), "nombre": name})
            data["recientes"] = recent[:12]
        self._update_settings(change)

    def _recent_list(self) -> list[dict]:
        return [dict(r, existe=(Path(r["carpeta"]) / "proyecto.json").exists())
                for r in self._settings().get("recientes", [])]

    def _rename_recent(self, folder: Path, name: str) -> None:
        def change(data):
            for r in data.get("recientes", []):
                if r.get("carpeta") == str(folder):
                    r["nombre"] = name
        self._update_settings(change)

    def remove_recent(self, folder: str) -> dict:
        """Quita un proyecto de la lista de recientes. No borra nada del disco."""
        with self._settings_lock:
            data = self._settings()
            recent = data.get("recientes", [])
            position = next((i for i, r in enumerate(recent) if r.get("carpeta") == folder), None)
            if position is None:
                raise ValueError(tr("Ese proyecto ya no está en la lista de recientes."))
            removed = recent.pop(position)
            self._save_settings(data)
        return {"quitado": removed, "posicion": position, "recientes": self._recent_list()}

    def restore_recent(self, entry: dict, position: int = 0) -> list[dict]:
        """Deshace «Quitar de recientes»: el proyecto vuelve al lugar donde estaba."""
        folder = str((entry or {}).get("carpeta") or "").strip()
        if not folder:
            raise ValueError(tr("No sé qué proyecto volver a poner en la lista."))
        self._remember(Path(folder), str(entry.get("nombre") or Path(folder).name), int(position))
        return self._recent_list()

    def app_info(self) -> dict:
        return {
            "version": __version__,
            "modo": self._mode,
            "idioma_ui": i18n.language(),
            "proveedores": {k: tr(v) for k, v in KINDS.items()},
            "idiomas": {k: tr(v[1]) for k, v in LANG_NAMES.items()},
            "variantes": list(VARIANTS),
            "modelos_claude": [(m, tr(label)) for m, label in SUGGESTED_MODELS],
            "chats": [{"id": k, "nombre": tr(v[0]), "bloque": v[1],
                       "modelos": [{"id": m, "nombre": tr(n), "nivel": tier} for m, n, tier in chat_models(k)]}
                      for k, v in CHAT_PRESETS.items()],
            "planes": [{"id": k, "nombre": tr(v)} for k, v in PLANS.items()],
            "carpeta_por_defecto": str(default_projects_dir()),
            "perfiles": profiles.list_profiles(),
            "recientes": self._recent_list(),
            "proyecto_abierto": str(self._project.root) if self._project else None,
            "apoyo": support.links(),
            "sugerencias": feedback.configured(),
            "portable": updates.app_dir() is not None,
            # Se abrió en el navegador porque falta el componente de Microsoft para la ventana propia.
            "falta_webview2": self._mode == "navegador" and updates.app_dir() is not None
                              and not components.webview2_version(),
        }

    # -- idioma de la interfaz ---------------------------------------------------

    def init_language(self) -> str:
        """Al abrir la app: el idioma guardado o, la primera vez, el del sistema."""
        return i18n.set_language(self._settings().get("idioma_ui") or i18n.system_language())

    def ui_strings(self) -> dict:
        """Idioma actual y sus traducciones (las claves son los textos en español)."""
        lang = i18n.language()
        return {"idioma": lang, "idiomas": i18n.LANGS, "textos": i18n.catalog(lang)}

    def set_ui_language(self, lang: str) -> str:
        if lang not in i18n.LANGS:
            raise ValueError(tr("Idioma desconocido."))
        self._update_settings(lambda data: data.update(idioma_ui=lang))
        return i18n.set_language(lang)

    def choose_path(self, kind: str = "epub", directory: str = "") -> str | None:
        """Diálogo nativo para elegir archivo o carpeta (solo en modo escritorio)."""
        if self._window is None:
            return None
        import webview

        if kind == "carpeta":
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=directory)
        elif kind == "pdf":
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, directory=directory, file_types=(tr("Documentos PDF (*.pdf)"), tr("Todos (*.*)")))
        elif kind == "libro":
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, directory=directory,
                file_types=(tr("Libros (*.pdf;*.docx;*.txt;*.fb2;*.mobi;*.azw3;*.azw)"), tr("Todos (*.*)")))
        else:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, directory=directory, file_types=(tr("Libros EPUB (*.epub)"), tr("Todos (*.*)")))
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else str(result)

    def quit_app(self) -> bool:
        """Cierra la app en modo navegador (en modo ventana basta con cerrarla)."""
        if self._tasks.state["activa"]:
            raise ValueError(tr("Hay una tarea en curso. Detenla antes de salir."))
        threading.Timer(0.5, os._exit, [0]).start()
        return True

    # -- versiones nuevas ---------------------------------------------------------

    UPDATE_EVERY = timedelta(hours=4)

    def check_update(self, force: bool = False) -> dict | None:
        """La versión publicada más reciente, si es más nueva que esta, con ``auto``
        si esta copia se puede actualizar sola.

        Consulta GitHub como mucho cada 4 horas; sin conexión, no avisa."""
        settings = self._settings()
        saved = settings.get("actualizacion") or {}
        latest = saved.get("ultima")
        try:
            stale = datetime.now() - datetime.fromisoformat(str(saved.get("consultado"))) > self.UPDATE_EVERY
        except ValueError:
            stale = True
        if force or stale:
            try:
                latest = updates.latest_release()
            except Exception:  # sin internet, GitHub no responde…: no molesta a nadie
                latest = None
            else:
                # Se vuelven a leer los ajustes: mientras se esperaba a GitHub pudo cambiar otra cosa.
                checked = {"consultado": datetime.now().isoformat(timespec="seconds"), "ultima": latest}
                self._update_settings(lambda data: data.update(actualizacion=checked))
        if latest and updates.is_newer(latest.get("version", ""), __version__):
            return dict(latest, auto=bool(latest.get("descarga")) and updates.can_self_update())
        return None

    def update_app(self) -> dict:
        """Descarga y prepara la versión nueva (en segundo plano). Solo en la carpeta portable de Windows."""
        info = self.check_update(force=True)
        if not info:
            raise ValueError(tr("Ya tienes la última versión de Jerónimo."))
        folder = updates.app_dir()
        if not info.get("auto") or folder is None:
            raise ValueError(tr("Esta copia de Jerónimo no se puede actualizar sola. Descarga la versión nueva desde "
                                "la página del proyecto."))

        def run(task: TaskRunner):
            archive = Path(tempfile.gettempdir()) / f"Jeronimo-{info['version']}.zip"
            task.log(tr("Descargando Jerónimo {version}…", version=info["version"]))
            updates.download(info["descarga"], archive, size=info.get("tamano") or 0, sha256=info.get("sha256") or "",
                             progress=lambda f: task.progress(0.9 * f, tr("Descargando… {pct} %", pct=round(100 * f))),
                             should_stop=task.cancelled)
            task.progress(0.92, tr("Preparando la versión nueva…"))
            try:
                new = updates.unpack(archive, folder)
            finally:
                archive.unlink(missing_ok=True)
            self._ready_update = (folder, new, info["version"])
            task.log(tr("Listo: Jerónimo se va a cerrar y volver a abrir con la versión {version}.",
                        version=info["version"]))
            return {"version": info["version"]}

        self._tasks.start(N_("Actualizar Jerónimo"), run, "actualizar")
        return self.task_status()

    def restart_for_update(self) -> bool:
        """Cierra Jerónimo; un ayudante pone la versión nueva en lugar de la vieja y vuelve a abrirlo."""
        if not self._ready_update:
            raise ValueError(tr("No hay ninguna actualización lista."))
        if self._tasks.state["activa"]:
            raise ValueError(tr("Hay una tarea en curso. Detenla antes de salir."))
        folder, new, _version = self._ready_update
        self._update_settings(lambda data: data.update(actualizado_desde=__version__))
        updates.start_swap(folder, new, os.getpid())
        threading.Timer(0.8, os._exit, [0]).start()
        return True

    # -- al abrir la app ----------------------------------------------------------------

    def startup_notices(self) -> dict:
        """Lo que la interfaz muestra al abrir: si se acaba de actualizar y si ofrecer accesos directos."""
        with self._settings_lock:
            data = self._settings()
            previous = data.pop("actualizado_desde", None)
            if previous is not None:
                self._save_settings(data)
        portable = updates.app_dir() is not None
        offer = portable and not data.get("accesos_ofrecidos")
        return {"actualizado_a": __version__ if previous and previous != __version__ else None,
                "ofrecer_accesos": offer}

    def create_shortcuts(self, create: bool = True) -> list[str]:
        """Accesos directos en el escritorio y en el menú Inicio (se ofrecen una sola vez)."""
        self._update_settings(lambda data: data.update(accesos_ofrecidos=True))
        folder = updates.app_dir()
        if not create:
            return []
        if folder is None:
            raise ValueError(tr("Los accesos directos solo se crean en la versión para Windows."))
        return components.create_shortcuts(folder / updates.EXE)

    # -- programas que pueden faltar ------------------------------------------------------

    def component_status(self) -> dict:
        return components.status()

    INSTALLERS = {"webview2": (N_("Instalar WebView2"), components.install_webview2,
                               N_("Listo: cierra Jerónimo y vuelve a abrirlo para usar su ventana propia."),
                               N_("WebView2 no quedó instalado. Puedes descargarlo desde la página de Microsoft.")),
                  "calibre": (N_("Instalar Calibre"), components.install_calibre,
                              N_("Listo: Calibre quedó instalado. Ya puedes convertir libros de Kindle."),
                              N_("Calibre no quedó instalado. Puedes descargarlo desde su página."))}

    def install_component(self, name: str) -> dict:
        """Instala WebView2 o Calibre (en segundo plano; Windows puede pedir permiso)."""
        if name not in self.INSTALLERS:
            raise ValueError(tr("Componente desconocido."))
        title, install, ok_text, fail_text = self.INSTALLERS[name]

        def run(task: TaskRunner):
            if not install(task.log):
                raise ValueError(tr(fail_text))
            return {"mensaje": tr(ok_text)}

        self._tasks.start(title, run, "componente")
        return self.task_status()

    def open_component_page(self, name: str) -> bool:
        """Abre la página de descarga de un programa que falta."""
        if name not in components.PAGES:
            raise ValueError(tr("Componente desconocido."))
        return bool(webbrowser.open(components.PAGES[name]))

    def open_windows_settings(self, page: str) -> bool:
        """Abre la configuración de Windows para agregar idiomas de OCR o voces."""
        components.open_settings(page)
        return True

    def open_url(self, url: str) -> bool:
        """Abre en el navegador una página del proyecto (descargas, ayuda)."""
        if not (url == updates.REPO_URL or url.startswith(updates.REPO_URL + "/")):
            raise ValueError(tr("Solo se pueden abrir páginas del proyecto Jerónimo."))
        return bool(webbrowser.open(url))

    def feedback_details(self) -> str:
        """Los datos técnicos que se pueden sumar a un reporte, para mostrarlos antes de enviar."""
        return feedback.technical_details(mode=self._mode, ui_language=i18n.language(),
                                          log_path=config_dir() / "registro.txt")

    def open_feedback(self, kind: str, text: str, include_details: bool = False) -> bool:
        """Abre el formulario de sugerencias ya completado; la persona lo revisa y lo envía."""
        details = self.feedback_details() if include_details else ""
        return bool(webbrowser.open(feedback.form_url(kind, text, details)))

    def open_support(self, kind: str) -> bool:
        """Abre en el navegador una de las formas de apoyar el proyecto (solo las configuradas)."""
        link = support.url(kind)
        if not link:
            raise ValueError(tr("Esa forma de apoyar el proyecto no está disponible."))
        return bool(webbrowser.open(link))

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
            raise FileNotFoundError(tr("No encuentro el archivo «{pdf}».", pdf=pdf))
        out = Path(out_path.strip().strip('"')) if out_path and out_path.strip() else pdf.with_suffix(".epub")

        def run(task: TaskRunner):
            task.log(tr("Convirtiendo «{name}»…", name=pdf.name))
            path, report = convert(pdf, out, progress=task.progress)
            task.log(tr("Listo: {chapters} capítulos, {notes} notas, {removed_lines} encabezados o números de "
                        "página quitados.", chapters=report.chapters, notes=report.notes, removed_lines=report.removed_lines))
            for w in report.warnings:
                task.log(tr("Aviso: {aviso}", aviso=w))
            return {"ruta": str(path), "paginas": report.pages, "capitulos": report.chapters,
                    "notas": report.notes, "lineas_quitadas": report.removed_lines,
                    "paginas_omitidas": report.dropped_pages, "avisos": report.warnings,
                    "titulo": report.title, "autor": report.author, "idioma": report.language}

        self._tasks.start(N_("Convertir PDF a EPUB"), run, "convertir")
        return self.task_status()

    def convert_book(self, path: str, out_path: str = "", ocr_lang: str = "") -> dict:
        """Convierte a EPUB un PDF, Word, TXT, FB2, MOBI o AZW3 (en segundo plano)."""
        from ..convert import FORMATS, convert, kind_of
        from ..pdf2epub import OCR_WARNING

        src = Path(path.strip().strip('"'))
        if not src.is_file():
            raise FileNotFoundError(tr("No encuentro el archivo «{src}».", src=src))
        kind = kind_of(src)
        if kind not in FORMATS or kind == ".epub":
            raise ValueError(tr("Elige un PDF, Word (DOCX), TXT, FB2, MOBI o AZW3.")
                             if kind != ".epub" else tr("Ese libro ya es un EPUB: úsalo directamente para crear el proyecto."))
        out = Path(out_path.strip().strip('"')) if out_path and out_path.strip() else None

        def run(task: TaskRunner):
            task.log(tr("Convirtiendo «{name}» ({formato})…", name=src.name, formato=tr(FORMATS[kind])))
            result, report = convert(src, out, progress=task.progress, ocr_lang=ocr_lang, should_stop=task.cancelled)
            task.log(tr("Listo: {chapters} capítulos, {notes} notas.", chapters=report.chapters, notes=report.notes))
            for w in report.warnings:
                task.log(tr("Aviso: {aviso}", aviso=w))
            ocr_note = tr(OCR_WARNING, motor=report.ocr) if report.ocr else None
            return {"ruta": str(result), "formato": tr(FORMATS[kind]), "paginas": report.pages,
                    "capitulos": report.chapters, "notas": report.notes, "lineas_quitadas": report.removed_lines,
                    "paginas_omitidas": report.dropped_pages,
                    "avisos": [w for w in report.warnings if w != ocr_note],
                    "titulo": report.title, "autor": report.author, "idioma": report.language,
                    "ocr": report.ocr}

        self._tasks.start(N_("Convertir a EPUB"), run, "convertir")
        return self.task_status()

    # -- proyectos -----------------------------------------------------------

    def _need_project(self) -> Project:
        if self._project is None:
            raise ValueError(tr("No hay ningún proyecto abierto."))
        return self._project

    def create_project(self, epub_path: str, folder: str = "", target_lang: str = "es",
                       variant: str = "auto", profile: str = "") -> dict:
        epub = Path(epub_path.strip().strip('"'))
        if not epub.is_file():
            raise FileNotFoundError(tr("No encuentro el archivo «{epub}».", epub=epub))
        if not folder:
            with EpubBook(epub) as book:
                folder = str(default_projects_dir() / slugify(book.title))
        target = Path(folder.strip().strip('"'))
        project = Project.create(target, epub, target_lang=target_lang, variant=variant)
        default_provider = self._settings().get("proveedor")
        if default_provider:
            project.config["proveedor"] = dict(default_provider)
            project.save_config()
        if profile:
            profiles.apply_profile(project, profile)
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
            raise ValueError(tr("Espera a que termine la tarea en curso antes de cerrar el proyecto."))
        self._project = None
        return True

    def rename_project(self, name: str) -> dict:
        """Cambia el nombre que se ve del proyecto abierto (también en recientes). La carpeta y el
        título del libro, que usa la IA, no cambian; los archivos exportados sí llevan el nombre nuevo."""
        name = " ".join((name or "").split())
        if not name:
            raise ValueError(tr("Escribe un nombre para el proyecto."))
        if len(name) > 120:
            raise ValueError(tr("El nombre es demasiado largo (máximo 120 caracteres)."))
        p = self._need_project()
        p.config["nombre"] = name
        p.save_config()
        self._rename_recent(p.root, name)
        return self.project_summary()

    def set_settings(self, values: dict) -> dict:
        p = self._need_project()
        for key in ("nombre", "variante", "titulo_traducido", "revision", "idioma_destino"):
            if key in values:
                p.config[key] = values[key]
        opts = p.config.setdefault("opciones", {})
        if "chat_manual" in values:
            chat = values["chat_manual"]
            if chat not in CHAT_PRESETS:
                raise ValueError(tr("Chat desconocido."))
            if chat != opts.get("chat_manual"):
                opts["modelo_manual"] = chat_models(chat)[0][0]  # el más capaz de ese chat
            opts["chat_manual"] = chat
        if "modelo_manual" in values:
            chat = opts.get("chat_manual") or "claude"
            if values["modelo_manual"] not in {m for m, _n, _t in chat_models(chat)}:
                raise ValueError(tr("Modelo desconocido para ese chat."))
            opts["modelo_manual"] = values["modelo_manual"]
        if "plan_manual" in values:
            if values["plan_manual"] not in PLANS:
                raise ValueError(tr("Plan desconocido."))
            opts["plan_manual"] = values["plan_manual"]
        if any(k in values for k in ("chat_manual", "modelo_manual", "plan_manual")) and "bloque_manual" not in values:
            opts["bloque_manual"] = recommended_block(opts.get("chat_manual") or "claude", opts.get("modelo_manual", ""),
                                                      opts.get("plan_manual", "pago"))
        if "firma" in values:
            p.config["firma"] = bool(values["firma"])
        if "bloques_livianos" in values:
            p.config.setdefault("opciones", {})["bloques_livianos"] = bool(values["bloques_livianos"])
        if "bloque_manual" in values:
            size = int(values["bloque_manual"])
            if not 1000 <= size <= 60000:
                raise ValueError(tr("El tamaño de bloque debe estar entre 1.000 y 60.000 caracteres."))
            p.config.setdefault("opciones", {})["bloque_manual"] = size
        p.save_config()
        if "nombre" in values:
            self._rename_recent(p.root, p.config["nombre"])
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
            "modelo_manual": self._manual_model(cfg),
            "plan_manual": (cfg.get("opciones") or {}).get("plan_manual") or "pago",
            "bloque_recomendado": recommended_block((cfg.get("opciones") or {}).get("chat_manual") or "claude",
                                                    self._manual_model(cfg),
                                                    (cfg.get("opciones") or {}).get("plan_manual") or "pago"),
            "bloques_livianos": bool((cfg.get("opciones") or {}).get("bloques_livianos")),
            "perfil_autor": cfg.get("perfil_autor", ""),
            "firma": p.signed,
            "paquete": self._package_state(p),
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
                raise FileNotFoundError(tr("No encuentro el archivo «{path}».", path=path))

        def run(task: TaskRunner):
            task.log(tr("Leyendo los dos libros…"))
            stats = p.add_reference(original.strip().strip('"'), translation.strip().strip('"'),
                                    name=name or None,
                                    progress=lambda f: task.progress(f, tr("Emparejando párrafos…")))
            task.log(tr("Listo: {pairs} pares, cobertura {coverage:.0%}, calidad {quality}.", pairs=stats.pairs, coverage=stats.coverage, quality=tr(stats.quality)))
            return asdict(stats)

        self._tasks.start(N_("Emparejar referencia"), run, "emparejar")
        return self.task_status()

    # -- perfiles de autor ---------------------------------------------------------

    def save_author_profile(self, name: str = "") -> dict:
        """Guarda referencias, guía, glosario y lo aprobado como perfil del autor."""
        return profiles.save_profile(self._need_project(), name)

    def apply_author_profile(self, profile_id: str, replace_style: bool = False) -> dict:
        result = profiles.apply_profile(self._need_project(), profile_id, replace_style=replace_style)
        return dict(result, resumen=self.project_summary())

    def delete_author_profile(self, profile_id: str) -> list[dict]:
        profiles.delete_profile(profile_id)
        return profiles.list_profiles()

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
        self._update_settings(lambda data: data.update(proveedor=clean))
        where = None
        if api_key and api_key.strip():
            where = save_api_key(clean["tipo"], api_key.strip())
        return {"guardado": True, "clave_en": where,
                "tiene_clave": clean["tipo"] in LOCAL_KINDS or bool(get_api_key(clean["tipo"]))}

    def _provider(self):
        cfg = self._need_project().config["proveedor"]
        if cfg.get("tipo") == "manual":
            raise ValueError(tr("Estás en modo manual: en la revisión del capítulo usa «Copiar bloque» y "
                             "«Pegar respuesta», o elige una IA con clave en «Inteligencia artificial»."))
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
            raise ValueError(tr("Elige al menos un paso."))
        if any(s in valid for s in ("estilo", "glosario")) and not p.references():
            raise ValueError(tr("Para la guía de estilo y el glosario oficial primero añade un libro de referencia."))

        def run(task: TaskRunner):
            for i, step in enumerate(valid):
                if task.cancelled():
                    break
                task.progress(i / len(valid))
                knowledge.prepare(p, provider, steps=(step,), log=task.log)
            return {"pasos": valid}

        self._tasks.start(N_("Preparar conocimiento"), run, "conocimiento")
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
                 "sugerencia": s.get("sugerencia"), "revisado_ia": bool(s.get("revisado_ia")),
                 "marcas": {str(k): v for k, v in marks.get(s["n"], {}).items()}}
                for s in data["segmentos"]
            ],
        }

    def _check_not_busy(self, index: int) -> None:
        if self._tasks.state["activa"] and self._tasks.state.get("capitulo") == index:
            raise ValueError(tr("Este capítulo se está traduciendo ahora mismo. Espera a que termine para editarlo."))

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
            raise ProviderError(tr("La IA no devolvió la traducción de este párrafo. Prueba otra vez."))
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
                task.log(tr("No quedan capítulos pendientes."))
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
                                  tr("«{title}»: {done} de {total} párrafos", title=title, done=done, total=total))

                report = translator.translate_chapter(idx, progress=prog, should_stop=task.cancelled)
                reports.append(asdict(report))
                if report.missing:
                    task.log(tr("«{title}»: quedaron {n} párrafos sin traducir; reintenta o tradúcelos a mano.", title=title, n=len(report.missing)))
                else:
                    task.log(tr("«{title}» traducido. Revísalo y apruébalo.", title=title))
            return {"capitulos": reports}

        self._tasks.start(N_("Traducción"), run, "traducir")
        return self.task_status()

    def _manual_state(self, translator: Translator, index: int) -> dict:
        data = self._need_project().load_chapter(index)
        return {"bloques_restantes": len(translator.manual_chunks(data)),
                "parrafos_pendientes": sum(1 for s in data["segmentos"] if not s.get("tgt"))}

    # -- paquete para Proyectos (claude.ai, ChatGPT, Gemini) -------------------------

    @staticmethod
    def _package_state(p: Project) -> dict | None:
        info = p.config.get("paquete")
        if not info:
            return None
        current = Translator(p, None).knowledge_fingerprint()
        return {"fecha": info.get("fecha", ""), "desactualizado": info.get("huella") != current,
                "ruta": info.get("ruta", "")}

    def project_package(self) -> dict:
        """Genera las instrucciones y el archivo de conocimiento para subir a un Proyecto."""
        p = self._need_project()
        translator = Translator(p, None)
        instructions = translator.project_instructions()
        knowledge_text = translator.project_knowledge()
        out = p.root / "salida" / f"{slugify(p.config.get('nombre') or 'libro')}-conocimiento.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(knowledge_text, encoding="utf-8")
        (out.parent / out.name.replace("-conocimiento.md", "-instrucciones.md")).write_text(instructions, encoding="utf-8")
        p.config["paquete"] = {"fecha": datetime.now().isoformat(timespec="minutes"),
                               "huella": translator.knowledge_fingerprint(), "ruta": str(out)}
        p.save_config()
        return {"instrucciones": instructions, "ruta": str(out), "caracteres_instrucciones": len(instructions),
                "caracteres_conocimiento": len(knowledge_text), "paquete": self._package_state(p)}

    def package_instructions(self) -> str:
        """Solo el texto de las instrucciones del Proyecto (para copiarlo)."""
        return Translator(self._need_project(), None).project_instructions()

    @staticmethod
    def _manual_model(cfg: dict) -> str:
        opts = cfg.get("opciones") or {}
        chat = opts.get("chat_manual") or "claude"
        valid = [m for m, _n, _t in chat_models(chat)]
        return opts.get("modelo_manual") if opts.get("modelo_manual") in valid else valid[0]

    def manual_book_estimate(self) -> dict:
        """Bloques (mensajes al chat) que faltan para terminar el libro en modo manual."""
        p = self._need_project()
        translator = Translator(p, None)
        blocks = pending = 0
        for i in p.chapter_indices():
            data = p.load_chapter(i)
            if data.get("omitir"):
                continue
            blocks += len(translator.manual_chunks(data))
            pending += sum(1 for s in data["segmentos"] if not s.get("tgt"))
        return {"bloques": blocks, "parrafos": pending, "bloque": translator.manual_block_chars()}

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
            raise ValueError(tr("No encontré párrafos traducidos en lo que pegaste. Copia la respuesta completa "
                             "(con el botón «Copiar» del bloque de código del chat)."))
        return dict({"guardados": saved, "faltan": missing}, **self._manual_state(translator, index))

    # -- preparación en modo manual (copiar y pegar en cualquier chat) ----------

    def _manual_prep(self):
        return knowledge.ManualPrep(self._need_project())

    def manual_prep_status(self) -> dict | None:
        return self._manual_prep().status()

    def manual_prep_start(self, step: str) -> dict:
        if self._tasks.state["activa"]:
            raise ValueError(tr("Espera a que termine la tarea en curso."))
        return self._manual_prep().start(step)

    def manual_prep_prompt(self) -> dict:
        return self._manual_prep().prompt()

    def manual_prep_import(self, text: str) -> dict:
        return self._manual_prep().import_response(text)

    def manual_prep_cancel(self) -> bool:
        self._manual_prep().cancel()
        return True

    # -- segunda pasada: revisión con IA -----------------------------------------

    def review_status(self, index: int) -> dict:
        return review.status(self._need_project(), index)

    def review_start(self, index: int) -> dict:
        """Revisa con la IA conectada los párrafos traducidos aún sin revisar (en segundo plano)."""
        p = self._need_project()
        provider = self._provider()
        title = p.load_chapter(index).get("titulo") or tr("Documento {index}", index=index)

        def run(task: TaskRunner):
            task.state["capitulo"] = index
            task.log(tr("Revisando «{title}» con {model}…", title=title, model=provider.model))
            reviewer = Reviewer(p, provider, log=task.log)
            result = reviewer.review_chapter(
                index, should_stop=task.cancelled,
                progress=lambda k, total: task.progress(k / total if total else 1, tr("Bloque {k} de {total}", k=min(k + 1, total), total=total)))
            task.log(tr("Listo: {sugerencias} sugerencia(s) en {bloques} bloque(s).", sugerencias=result['sugerencias'], bloques=result['bloques']))
            return result

        self._tasks.start(N_("Revisión con IA"), run, "revisar")
        return self.task_status()

    def review_manual_prompt(self, index: int) -> dict | None:
        self._check_not_busy(index)
        return Reviewer(self._need_project()).manual_prompt(index)

    def review_manual_import(self, index: int, text: str) -> dict:
        self._check_not_busy(index)
        p = self._need_project()
        result = Reviewer(p).manual_import(index, text)
        return dict(result, **review.status(p, index))

    def review_resolve(self, index: int, n: int | None, accept: bool) -> dict:
        """Acepta o descarta una sugerencia (o todas las del capítulo si ``n`` es None)."""
        self._check_not_busy(index)
        p = self._need_project()
        done = review.resolve(p, index, n, accept)
        return dict(review.status(p, index), resueltas=done)

    def review_reset(self, index: int) -> dict:
        self._check_not_busy(index)
        p = self._need_project()
        review.reset(p, index)
        return review.status(p, index)

    # -- control de calidad (sin IA) -------------------------------------------

    def quality(self, index: int | None = None) -> dict:
        """Avisos de calidad de todo el libro o de un capítulo, con cómo se corrige cada uno."""
        return qa_fix.check(self._need_project(), index)

    def quality_ignore(self, index: int, n: int, kind: str, key: str = "") -> bool:
        """No volver a mostrar ese aviso (en ese párrafo, o ese nombre en todo el libro)."""
        self._check_not_busy(index)
        qa.ignore(self._need_project(), index, n, kind, key)
        return True

    # -- corregir los avisos con un clic -----------------------------------------------

    def _check_idle(self) -> None:
        if self._tasks.state["activa"]:
            raise ValueError(tr("Espera a que termine la tarea en curso."))

    def quality_fix(self, kind: str, items: list | None = None) -> dict:
        """Corrige sin IA los avisos de un tipo: los de ciertos párrafos ([[doc, n], …]) o todos."""
        if items is None:
            self._check_idle()
        for doc, _n in items or []:
            self._check_not_busy(int(doc))
        return qa_fix.fix(self._need_project(), kind, items)

    def quality_unify_name(self, forms: list[str], keep: str) -> dict:
        """Escribe un nombre siempre igual en todo el libro."""
        self._check_idle()
        return qa_fix.unify_name(self._need_project(), forms, keep)

    def quality_undo(self, batch: str | None = None) -> dict:
        """Deshace una corrección (la última si no se dice cuál)."""
        self._check_idle()
        return qa_fix.undo(self._need_project(), batch)

    def quality_ai_fix(self, index: int, n: int) -> dict:
        """Propuesta de la IA conectada para los avisos de un párrafo."""
        self._check_not_busy(index)
        p = self._need_project()
        fixer = qa_fix.Fixer(p, self._provider())
        entries = fixer.pending(items=[[index, n]])
        if not entries:
            raise ValueError(tr("Este párrafo ya no tiene avisos para corregir con la IA."))
        result = fixer.run(entries)
        seg = p.load_chapter(index)["segmentos"][n]
        return dict(result, sugerencia=seg.get("sugerencia"), tgt=seg.get("tgt") or "",
                    opinion=(seg.get("qa_opinion") or {}).get("motivo") if result["bien"] else None)

    def quality_ai_fix_all(self, kinds: list[str] | None = None) -> dict:
        """Propuestas de la IA conectada para todos los avisos (o los de ciertos tipos), en segundo plano."""
        p = self._need_project()
        provider = self._provider()
        fixer = qa_fix.Fixer(p, provider)
        entries = fixer.pending(kinds or None)
        if not entries:
            raise ValueError(tr("No quedan avisos para corregir con la IA."))

        def run(task: TaskRunner):
            task.log(tr("Pidiendo a {model} propuestas para {n} párrafo(s)…", model=provider.model, n=len(entries)))
            fixer.translator.log = task.log
            result = fixer.run(entries, should_stop=task.cancelled,
                               progress=lambda k, total: task.progress(k / total if total else 1, tr("Bloque {k} de {total}", k=min(k + 1, total), total=total)))
            task.log(tr("Listo: {sugerencias} propuesta(s); en {bien} párrafo(s) la IA no vio nada que cambiar.",
                        sugerencias=result["sugerencias"], bien=result["bien"]))
            s, ok = result["sugerencias"], result["bien"]
            if s:
                message = tr("La IA propone 1 corrección: revísala en cada aviso.") if s == 1 else \
                    tr("La IA propone {n} correcciones: revísalas en cada aviso.", n=s)
            elif ok:
                message = tr("La IA revisó los avisos y no ve nada que cambiar.")
            else:
                message = tr("La IA no devolvió propuestas. Prueba otra vez.")
            return dict(result, mensaje=message)

        self._tasks.start(N_("Corregir avisos con IA"), run, "calidad")
        return self.task_status()

    def quality_ai_manual_prompt(self, kinds: list[str] | None = None, items: list | None = None) -> dict | None:
        """Modo manual: las instrucciones para corregir con cualquier chat (el primer bloque pendiente)."""
        self._check_idle()
        return qa_fix.Fixer(self._need_project()).manual_prompt(kinds or None, items)

    def quality_ai_manual_import(self, text: str) -> dict:
        """Modo manual: guarda las propuestas que devolvió el chat."""
        self._check_idle()
        return qa_fix.Fixer(self._need_project()).manual_import(text)

    # -- buscar y reemplazar -------------------------------------------------------

    @staticmethod
    def _search_options(options: dict | None) -> dict:
        o = options or {}
        return {"case": bool(o.get("mayusculas")), "whole_word": bool(o.get("palabra"))}

    def find_text(self, query: str, options: dict | None = None) -> dict:
        """Busca en la traducción o, con ``{"en": "original"}``, en el original."""
        field = "src" if (options or {}).get("en") == "original" else "tgt"
        return finder.search(self._need_project(), query, field=field, **self._search_options(options))

    def replace_text(self, query: str, replacement: str, options: dict | None = None,
                     only: list | None = None) -> dict:
        """Reemplaza en la traducción (en todos los párrafos o solo en ``only``)."""
        if self._tasks.state["activa"]:
            raise ValueError(tr("Espera a que termine la tarea en curso para reemplazar."))
        return finder.replace(self._need_project(), query, replacement, only=only,
                              **self._search_options(options))

    def undo_replace(self) -> dict:
        if self._tasks.state["activa"]:
            raise ValueError(tr("Espera a que termine la tarea en curso."))
        return finder.undo_replacement(self._need_project())

    # -- Word y PDF ------------------------------------------------------------------

    DOC_FORMATS = {
        "docx": (N_("Exportar a Word"), "docx", ""),
        "docx-bilingue": (N_("Exportar a Word bilingüe"), "docx", "bilingue"),
        "pdf-a5": (N_("Exportar a PDF"), "pdf", "a5"),
        "pdf-a4": (N_("Exportar a PDF"), "pdf", "a4"),
    }

    def export_document(self, kind: str) -> dict:
        """Exporta a Word o PDF en segundo plano."""
        from .. import export_docs

        if kind not in self.DOC_FORMATS:
            raise ValueError(tr("Formato desconocido."))
        p = self._need_project()
        name, ext, variant = self.DOC_FORMATS[kind]
        stem = slugify(p.config.get("nombre") or "libro")
        suffix = f"bilingue-{p.source_lang}-{p.target_lang}" if variant == "bilingue" else p.target_lang
        out = p.root / "salida" / f"{stem}.{suffix}{'.' + variant if variant in ('a5', 'a4') else ''}.{ext}"

        def run(task: TaskRunner):
            task.log(tr("Creando {name}…", name=out.name))
            if ext == "docx":
                export_docs.export_docx(p, out, bilingual=variant == "bilingue", progress=task.progress)
            else:
                export_docs.export_pdf(p, out, page=variant, progress=task.progress)
            task.log(tr("Listo."))
            return {"ruta": str(out), "formato": kind}

        self._tasks.start(name, run, "documento")
        return self.task_status()

    # -- metadatos de la edición traducida ------------------------------------------

    def book_metadata(self) -> dict:
        p = self._need_project()
        with EpubBook(p.book_path) as book:
            original = book.metadata()
        return {"datos": p.book_metadata(), "original": original, "editados": sorted((p.config.get("metadatos") or {}))}

    def set_book_metadata(self, values: dict) -> dict:
        self._need_project().set_book_metadata(values or {})
        return self.book_metadata()

    # -- portada traducida ---------------------------------------------------------

    def cover_settings(self) -> dict:
        p = self._need_project()
        with EpubBook(p.book_path) as book:
            has_original = book.cover_item() is not None
        return dict(p.cover_settings(), tiene_original=has_original)

    def set_cover(self, values: dict) -> dict:
        p = self._need_project()
        current = p.cover_settings()
        style = values.get("estilo", current["estilo"])
        if style not in ("difuminada", "lisa", "propia"):
            raise ValueError(tr("Estilo de portada desconocido."))
        if style == "propia" and not p.own_cover_path.exists():
            raise ValueError(tr("Primero sube tu imagen de portada."))
        p.config["portada"] = {"usar": bool(values.get("usar", current["usar"])), "estilo": style,
                               "titulo": str(values.get("titulo", current["titulo"])).strip(),
                               "autor": str(values.get("autor", current["autor"])).strip()}
        p.save_config()
        return self.cover_settings()

    def set_cover_image(self, data_url: str) -> dict:
        """Guarda la imagen de portada propia (``data:image/...;base64,...``)."""
        import base64
        import binascii

        p = self._need_project()
        head, _, body = (data_url or "").partition(",")
        if not head.startswith("data:image/") or ";base64" not in head:
            raise ValueError(tr("Elige una imagen JPG, PNG o WEBP."))
        if len(body) > 20_000_000:
            raise ValueError(tr("La imagen es demasiado grande (máximo unos 15 MB)."))
        try:
            data = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError(tr("No se pudo leer la imagen.")) from e
        p.set_own_cover(data)
        return self.cover_settings()

    def remove_cover_image(self) -> dict:
        self._need_project().remove_own_cover()
        return self.cover_settings()

    def cover_preview(self, values: dict | None = None) -> dict:
        """Vista previa de la portada nueva y de la original (imágenes pequeñas)."""
        import base64
        import io

        from PIL import Image

        from ..cover import make_cover, unsupported_chars

        p = self._need_project()
        cfg = dict(p.cover_settings(), **(values or {}))
        with EpubBook(p.book_path) as book:
            item = book.cover_item()
            original = book.read(item["path"]) if item else None
        aspect, thumb = 1.5, None
        if original:
            try:
                with Image.open(io.BytesIO(original)) as im:
                    aspect = min(1.8, max(1.2, im.height / im.width))
                    small = im.convert("RGB")
                    small.thumbnail((360, 640))
                    buf = io.BytesIO()
                    small.save(buf, "JPEG", quality=85)
                    thumb = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception:
                original = None
        if cfg.get("estilo") == "propia" and p.own_cover_path.exists():
            with Image.open(p.own_cover_path) as im:
                small = im.convert("RGB")
                small.thumbnail((360, 640))
                buf = io.BytesIO()
                small.save(buf, "JPEG", quality=85)
            return {"nueva": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
                    "original": thumb, "faltan": []}
        missing = sorted(unsupported_chars(cfg["titulo"] + cfg["autor"]))
        new = make_cover(cfg["titulo"], cfg["autor"], style=cfg["estilo"] if cfg["estilo"] != "propia" else "difuminada",
                         original=original, aspect=aspect, size=(360, round(360 * aspect)))
        return {"nueva": "data:image/jpeg;base64," + base64.b64encode(new).decode("ascii"),
                "original": thumb, "faltan": missing[:20]}

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
            return {"ok": False, "error": tr("Función desconocida: {name}", name=name)}
        try:
            return {"ok": True, "resultado": getattr(self._api, name)(*(args or []))}
        except KeyError as e:  # str(KeyError) añade comillas
            return {"ok": False, "error": str(e.args[0]) if e.args else str(e)}
        except (ProviderError, EpubError, PdfError, ValueError, FileNotFoundError, FileExistsError) as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # pragma: no cover
            _log_error(traceback.format_exc())
            return {"ok": False, "error": tr("Error inesperado: {error}", error=e)}
