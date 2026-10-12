"""Programas que pueden faltar (WebView2, Calibre), configuración de Windows y accesos directos.
Nada se instala de verdad: los instaladores y PowerShell están simulados."""

import io
import subprocess
import sys

import pytest

from traductor import components, updates
from traductor.app.api import Api


def test_status_has_every_component():
    st = components.status()
    assert set(st) == {"windows", "webview2", "calibre", "winget"}
    assert st["windows"] == sys.platform.startswith("win")
    if not st["windows"]:
        assert st["webview2"] is False and st["winget"] is False


def test_only_known_settings_pages_open(monkeypatch):
    opened = []
    monkeypatch.setattr(components.os, "startfile", lambda uri: opened.append(uri), raising=False)
    with pytest.raises(ValueError):
        components.open_settings("ms-settings:privacy")
    if sys.platform.startswith("win"):
        components.open_settings("voz")
        assert opened == ["ms-settings:speech"]


def test_calibre_needs_winget(monkeypatch):
    monkeypatch.setattr(components.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="winget"):
        components.install_calibre(lambda msg: None)


def test_calibre_is_installed_with_winget(monkeypatch):
    monkeypatch.setattr(components.shutil, "which", lambda name: r"C:\winget.exe")
    monkeypatch.setattr(components, "find_calibre", lambda: r"C:\Program Files\Calibre2\ebook-convert.exe")
    seen, log = [], []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"Instalado correctamente\n", stderr=b"")

    assert components.install_calibre(log.append, run=fake_run) is True
    assert seen[0][:4] == [r"C:\winget.exe", "install", "--id", "calibre.calibre"]
    assert "Instalado correctamente" in log


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_webview2_installer_must_be_signed_by_microsoft(monkeypatch):
    runs = []
    monkeypatch.setattr(components, "_signed_by_microsoft", lambda path: False)
    with pytest.raises(ValueError, match="firma"):
        components.install_webview2(lambda msg: None, opener=lambda req, timeout: FakeResponse(b"MZ"),
                                    run=lambda *a, **k: runs.append(a))
    assert runs == []  # no se abrió
    monkeypatch.setattr(components, "_signed_by_microsoft", lambda path: True)
    monkeypatch.setattr(components, "webview2_version", lambda: "130.0.1")
    assert components.install_webview2(lambda msg: None, opener=lambda req, timeout: FakeResponse(b"MZ"),
                                       run=lambda *a, **k: runs.append(a)) is True
    assert len(runs) == 1


def test_shortcuts_use_the_app_exe(monkeypatch, tmp_path):
    if not sys.platform.startswith("win"):
        with pytest.raises(ValueError):
            components.create_shortcuts(tmp_path / "Jeronimo.exe")
        return
    calls = []

    def fake_ps(script, *args, timeout=60):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="C:\\Escritorio\\Jerónimo.lnk\n".encode(), stderr=b"")

    monkeypatch.setattr(components, "_powershell", fake_ps)
    assert components.create_shortcuts(tmp_path / "Jeronimo.exe") == ["C:\\Escritorio\\Jerónimo.lnk"]
    assert calls == [(str(tmp_path / "Jeronimo.exe"),)]


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="PowerShell de Windows")
def test_powershell_scripts_keep_accents():
    done = components._powershell("param([string]$Texto)\n[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
                                  "[Console]::Out.Write('Jerónimo · ' + $Texto)", "año")
    assert done.stdout.decode("utf-8") == "Jerónimo · año"


def test_api_components_and_first_run(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    api = Api(mode="navegador")
    assert set(api.component_status()) == {"windows", "webview2", "calibre", "winget"}
    with pytest.raises(ValueError):
        api.install_component("photoshop")
    with pytest.raises(ValueError):
        api.open_component_page("https://example.com")
    assert api.open_component_page("calibre") and opened == [components.PAGES["calibre"]]
    info = api.app_info()
    assert info["portable"] is False and info["falta_webview2"] is False  # desde el código fuente
    # La primera vez (solo en la carpeta portable) se ofrecen los accesos directos; contestar los apaga.
    monkeypatch.setattr(updates, "app_dir", lambda: tmp_path / "Jeronimo")
    assert api.startup_notices() == {"actualizado_a": None, "ofrecer_accesos": True}
    assert api.create_shortcuts(False) == []
    assert api.startup_notices()["ofrecer_accesos"] is False


def test_install_component_runs_in_the_background(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    title, _install, ok_text, fail_text = Api.INSTALLERS["calibre"]
    monkeypatch.setitem(Api.INSTALLERS, "calibre", (title, lambda log: log("instalando") or True, ok_text, fail_text))
    api = Api(mode="navegador")
    api.install_component("calibre")
    import time

    for _ in range(100):
        if not api.task_status()["activa"]:
            break
        time.sleep(0.05)
    st = api.task_status()
    assert st["error"] is None and st["clave"] == "componente" and "Calibre" in st["resultado"]["mensaje"]
