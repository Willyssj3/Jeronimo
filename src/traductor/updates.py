"""Versiones nuevas de Jerónimo.

- Consulta la última versión publicada en GitHub: una petición de lectura a la
  API pública, sin enviar ningún dato del usuario ni de sus libros.
- En la app empaquetada (la carpeta portable de Windows) descarga el ZIP de esa
  versión, solo desde las descargas del proyecto, lo verifica y lo descomprime
  al lado de la carpeta de la app («<carpeta>.nuevo»). Cuando Jerónimo se
  cierra, un ayudante cambia una carpeta por la otra y vuelve a abrir la app.
  La carpeta vieja queda como «<carpeta>.anterior» hasta que la nueva arranca.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from .i18n import tr

REPO = "Willyssj3/Jeronimo"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases/latest"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "Jeronimo-Windows.zip"  # nombre fijo: el enlace de descarga directa no cambia nunca
DOWNLOAD_URL = f"{RELEASES_URL}/download/{ASSET}"
EXE = "Jeronimo.exe"
# Lo único que puede haber en la carpeta de la app para que se reemplace sola: si
# alguien la descomprimió suelta en Descargas o en el Escritorio, no se toca nada.
APP_FILES = {"jeronimo.exe", "_internal", "leeme.txt", "jeronimo.exe.webview2"}
LEFTOVERS = (".anterior", ".nuevo", ".descomprimiendo")
# Para probar la actualización completa contra un GitHub falso en el propio equipo.
TEST_SOURCE_ENV = "JERONIMO_PRUEBA_ACTUALIZACION"
CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP = 0x08000000, 0x00000200


class UpdateError(ValueError):
    """Algo salió mal al actualizar (el mensaje se muestra tal cual)."""


def parse_version(text: str) -> tuple[int, ...]:
    """«v0.10.2» → (0, 10, 2). Se ignoran sufijos como «-beta»."""
    core = (text or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    nums = [int(n) for n in re.findall(r"\d+", core)[:3]]
    return tuple(nums + [0] * (3 - len(nums))) if nums else (0, 0, 0)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _test_source() -> str:
    """Dirección de un GitHub falso, solo si está en el propio equipo (pruebas)."""
    base = os.environ.get(TEST_SOURCE_ENV, "").rstrip("/")
    return base if base.startswith("http://") and urlparse(base).hostname in ("127.0.0.1", "localhost") else ""


def api_url() -> str:
    test = _test_source()
    return f"{test}/latest.json" if test else API_URL


def _download_prefixes() -> tuple[str, ...]:
    test = _test_source()
    return (f"{REPO_URL}/releases/download/",) + ((f"{test}/",) if test else ())


def _fetch(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Jeronimo-traductor"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (URL fija)
        return json.loads(response.read().decode("utf-8"))


def latest_release(timeout: float = 6.0, fetch=_fetch) -> dict | None:
    """Última versión publicada: ``{"version", "url", "notas", "fecha", "descarga", "tamano", "sha256"}``
    o None. ``descarga`` es el ZIP portable (vacío si la versión no lo trae).

    Las versiones preliminares y los borradores no cuentan (GitHub no los
    devuelve como «latest»)."""
    data = fetch(api_url(), timeout)
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return None
    url = str(data.get("html_url") or RELEASES_URL)
    if not url.startswith(REPO_URL):
        url = RELEASES_URL
    notes = str(data.get("body") or "").strip()
    if len(notes) > 2000:
        notes = notes[:2000].rsplit("\n", 1)[0] + "\n…"
    asset = next((a for a in data.get("assets") or [] if isinstance(a, dict) and a.get("name") == ASSET), {})
    download = str(asset.get("browser_download_url") or "")
    if not download.startswith(_download_prefixes()):
        download = ""
    digest = str(asset.get("digest") or "") if download else ""
    return {"version": tag.lstrip("vV"), "url": url, "notas": notes,
            "fecha": str(data.get("published_at") or "")[:10],
            "descarga": download, "tamano": int(asset.get("size") or 0) if download else 0,
            "sha256": digest[7:].lower() if digest.startswith("sha256:") else ""}


# -- actualizar la carpeta portable -------------------------------------------------

def app_dir() -> Path | None:
    """Carpeta de la app empaquetada para Windows, o None si corre desde el código."""
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("win"):
        return None
    return Path(sys.executable).resolve().parent


def can_self_update(folder: Path | None = None) -> bool:
    """Si esta copia se puede reemplazar sola: la carpeta portable de la app, con
    solo sus archivos, en un lugar donde se puede escribir."""
    folder = folder or app_dir()
    if folder is None or not (folder / EXE).is_file() or not (folder / "_internal").is_dir():
        return False
    try:
        if any(p.name.lower() not in APP_FILES for p in folder.iterdir()):
            return False
        probe = Path(tempfile.mkdtemp(prefix=".jeronimo-", dir=folder.parent))
        probe.rmdir()
    except OSError:
        return False
    return True


def download(url: str, dest: Path, *, size: int = 0, sha256: str = "", progress=None, should_stop=None,
             opener=urllib.request.urlopen, timeout: float = 60.0) -> Path:
    """Descarga el ZIP de la versión nueva en ``dest``, solo desde las descargas
    del proyecto, y comprueba que llegó entero (tamaño y, si GitHub lo da, SHA-256)."""
    if not url.startswith(_download_prefixes()):
        raise UpdateError(tr("Solo se descargan versiones publicadas en la página del proyecto."))
    request = urllib.request.Request(url, headers={"User-Agent": "Jeronimo-traductor"})
    partial = dest.with_name(dest.name + ".parcial")
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(request, timeout=timeout) as response, open(partial, "wb") as out:
            final = urlparse(response.geturl())
            if not _test_source() and (final.scheme != "https" or not (
                    final.hostname or "").endswith(("github.com", "githubusercontent.com"))):
                raise UpdateError(tr("Solo se descargan versiones publicadas en la página del proyecto."))
            total = size or int(response.headers.get("Content-Length") or 0)
            while True:
                if should_stop and should_stop():
                    raise UpdateError(tr("Actualización cancelada."))
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(1.0, done / total))
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except OSError as e:
        partial.unlink(missing_ok=True)
        raise UpdateError(tr("No se pudo descargar la versión nueva: {error}", error=e)) from e
    if done == 0 or (size and done != size):
        partial.unlink(missing_ok=True)
        raise UpdateError(tr("La descarga quedó incompleta. Prueba de nuevo."))
    if sha256 and digest.hexdigest() != sha256.lower():
        partial.unlink(missing_ok=True)
        raise UpdateError(tr("La descarga no coincide con la versión publicada. Prueba de nuevo."))
    os.replace(partial, dest)
    return dest


def unpack(archive: Path, folder: Path) -> Path:
    """Descomprime la versión nueva al lado de la app, en «<carpeta>.nuevo»."""
    target = folder.with_name(folder.name + ".nuevo")
    work = folder.with_name(folder.name + ".descomprimiendo")
    for leftover in (target, work):
        shutil.rmtree(leftover, ignore_errors=True)
    damaged = tr("La descarga está dañada. Prueba de nuevo.")
    try:
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:
                raise UpdateError(damaged)
            base = work.resolve()
            if any(not (work / name).resolve().is_relative_to(base) for name in z.namelist()):
                raise UpdateError(damaged)
            z.extractall(work)
        # El ZIP trae la carpeta «Jeronimo» (o los archivos sueltos).
        if (work / EXE).is_file():
            work.rename(target)
        else:
            inner = [p for p in work.iterdir() if p.is_dir() and (p / EXE).is_file()]
            if len(inner) != 1 or not (inner[0] / "_internal").is_dir():
                raise UpdateError(tr("La versión descargada no trae «{exe}».", exe=EXE))
            inner[0].rename(target)
    except zipfile.BadZipFile as e:
        raise UpdateError(damaged) from e
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


# Ayudante de PowerShell: espera a que Jerónimo se cierre, pone la carpeta nueva
# en lugar de la vieja (que queda como «.anterior») y vuelve a abrir la app. Si
# algo falla, deja la versión vieja en su lugar y la abre igual.
SWAP_SCRIPT = r"""param([int]$ProcessId, [string]$App, [string]$New, [string]$Exe)
$old = $App + '.anterior'
$log = Join-Path $env:TEMP 'jeronimo-actualizar.log'
function Note($text) { Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + '  ' + $text) -Encoding UTF8 }
Note "Actualizando $App"
try { Wait-Process -Id $ProcessId -Timeout 120 -ErrorAction Stop } catch {}
if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue }
$moved = $false
for ($i = 0; $i -lt 60 -and -not $moved; $i++) {
    try { Move-Item -LiteralPath $App -Destination $old -ErrorAction Stop; $moved = $true }
    catch { Start-Sleep -Milliseconds 500 }
}
if ($moved) {
    try { Move-Item -LiteralPath $New -Destination $App -ErrorAction Stop; Note 'Listo' }
    catch { Note ('No se pudo poner la version nueva: ' + $_); Move-Item -LiteralPath $old -Destination $App -ErrorAction SilentlyContinue }
} else { Note 'La carpeta seguia en uso: queda la version anterior' }
Start-Process -FilePath (Join-Path $App $Exe) -WorkingDirectory $App
"""


def start_swap(folder: Path, new: Path, pid: int, *, popen=subprocess.Popen) -> None:
    """Deja corriendo el ayudante que, cuando Jerónimo se cierra, cambia la
    carpeta vieja por la nueva y vuelve a abrir la app."""
    script = Path(tempfile.gettempdir()) / "jeronimo-actualizar.ps1"
    script.write_text(SWAP_SCRIPT, encoding="utf-8-sig")  # con BOM: PowerShell 5 lee bien las tildes
    popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
           "-ProcessId", str(pid), "-App", str(folder), "-New", str(new), "-Exe", EXE],
          cwd=tempfile.gettempdir(), creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, close_fds=True)


def cleanup_leftovers(folder: Path | None = None) -> None:
    """Al arrancar: borra la copia vieja y los restos de la última actualización."""
    folder = folder or app_dir()
    if folder is None:
        return
    for suffix in LEFTOVERS:
        shutil.rmtree(folder.with_name(folder.name + suffix), ignore_errors=True)
