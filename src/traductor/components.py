"""Programas que Jerónimo necesita y que pueden faltar en el equipo, y accesos directos.

- **WebView2** (de Microsoft): la ventana propia de la app. Viene con Windows 10
  y 11; si falta, la app se abre en el navegador y ofrece instalarlo.
- **Calibre**: para abrir libros de Kindle (MOBI, AZW3). Se instala con winget.

Nada se instala sin que la persona lo pida, y Windows puede pedir su permiso
(control de cuentas de usuario). Para los idiomas del OCR y las voces de lectura
se abre la página de Configuración de Windows que corresponde."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from .convert import find_calibre
from .i18n import tr

WEBVIEW2_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_SETUP = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"  # instalador oficial de Microsoft
SETTINGS_PAGES = {"idioma": "ms-settings:regionlanguage", "voz": "ms-settings:speech"}
PAGES = {"calibre": "https://calibre-ebook.com/download_windows",
         "webview2": "https://developer.microsoft.com/microsoft-edge/webview2/"}
CREATE_NO_WINDOW = 0x08000000
_NO_WINDOW = {"creationflags": CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}


def webview2_version() -> str:
    """Versión de WebView2 instalada («» si falta o no es Windows)."""
    if not sys.platform.startswith("win"):
        return ""
    import winreg

    keys = ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"))
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as key:
                value = str(winreg.QueryValueEx(key, "pv")[0] or "")
        except OSError:
            continue
        if value and value != "0.0.0.0":
            return value
    return ""


def status() -> dict:
    windows = sys.platform.startswith("win")
    return {"windows": windows, "webview2": bool(webview2_version()), "calibre": bool(find_calibre()),
            "winget": bool(windows and shutil.which("winget"))}


def _powershell(script: str, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """Corre un script de PowerShell guardado en un archivo temporal (con BOM, por las tildes)."""
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", encoding="utf-8-sig", delete=False) as f:
        f.write(script)
    try:
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", f.name, *args], capture_output=True, timeout=timeout, **_NO_WINDOW)
    finally:
        Path(f.name).unlink(missing_ok=True)


def _signed_by_microsoft(path: Path) -> bool:
    done = _powershell("param([string]$Path)\n$s = Get-AuthenticodeSignature -LiteralPath $Path\n"
                       "[Console]::Out.Write($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)", str(path))
    status_, _, subject = done.stdout.decode("utf-8", "replace").partition("|")
    return status_.strip() == "Valid" and "O=Microsoft Corporation" in subject


def install_webview2(log, *, opener=urllib.request.urlopen, run=subprocess.run) -> bool:
    """Descarga el instalador oficial de Microsoft, comprueba su firma y lo abre."""
    setup = Path(tempfile.gettempdir()) / "MicrosoftEdgeWebview2Setup.exe"
    log(tr("Descargando el instalador de WebView2 desde Microsoft…"))
    try:
        with opener(urllib.request.Request(WEBVIEW2_SETUP, headers={"User-Agent": "Jeronimo-traductor"}),
                    timeout=60) as response:
            setup.write_bytes(response.read())
    except OSError as e:
        raise ValueError(tr("No se pudo descargar el instalador de WebView2: {error}", error=e)) from e
    try:
        if not _signed_by_microsoft(setup):
            raise ValueError(tr("El instalador descargado no tiene la firma de Microsoft: no se abrió."))
        log(tr("Instalando WebView2 (Windows puede pedir permiso)…"))
        run([str(setup)], timeout=1800, check=False)
    finally:
        setup.unlink(missing_ok=True)
    return bool(webview2_version())


def install_calibre(log, *, run=subprocess.run) -> bool:
    """Instala Calibre con winget (el instalador de aplicaciones de Windows)."""
    winget = shutil.which("winget")
    if not winget:
        raise ValueError(tr("Este Windows no tiene el instalador de aplicaciones (winget). Descarga Calibre "
                            "desde su página."))
    log(tr("Instalando Calibre (Windows puede pedir permiso)…"))
    done = run([winget, "install", "--id", "calibre.calibre", "-e", "--source", "winget",
                "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"],
               capture_output=True, timeout=3600, check=False, **_NO_WINDOW)
    for line in (done.stdout or b"").decode("utf-8", "replace").splitlines()[-6:]:
        if line.strip():
            log(line.strip())
    return bool(find_calibre())


def open_settings(page: str) -> None:
    """Abre una página de la Configuración de Windows (idiomas o voz)."""
    if page not in SETTINGS_PAGES or not sys.platform.startswith("win"):
        raise ValueError(tr("Esa página de la configuración de Windows no está disponible."))
    os.startfile(SETTINGS_PAGES[page])  # type: ignore[attr-defined]


SHORTCUT_SCRIPT = r"""param([string]$Exe)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    if (-not $dir) { continue }
    $path = Join-Path $dir 'Jerónimo.lnk'
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $Exe
    $link.WorkingDirectory = Split-Path -Parent $Exe
    $link.IconLocation = "$Exe,0"
    $link.Description = 'Jerónimo'
    $link.Save()
    [Console]::Out.WriteLine($path)
}
"""


def create_shortcuts(exe: Path) -> list[str]:
    """Accesos directos a la app en el escritorio y en el menú Inicio."""
    if not sys.platform.startswith("win"):
        raise ValueError(tr("Los accesos directos solo se crean en Windows."))
    done = _powershell(SHORTCUT_SCRIPT, str(exe))
    made = [line.strip() for line in done.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    if done.returncode != 0 or not made:
        raise ValueError(tr("No se pudieron crear los accesos directos."))
    return made
