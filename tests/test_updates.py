"""Versiones nuevas: aviso, descarga, descompresión y reemplazo de la carpeta (GitHub simulado)."""

import hashlib
import io
import shutil
import subprocess
import sys
import time
import zipfile

import pytest

from traductor import __version__, updates
from traductor.app.api import Api

ZIP_URL = f"{updates.REPO_URL}/releases/download/v9.1.0/{updates.ASSET}"


def test_version_comparison():
    assert updates.parse_version("v0.10.2") == (0, 10, 2)
    assert updates.parse_version("1.2") == (1, 2, 0)
    assert updates.parse_version("v2.0.0-beta.1") == (2, 0, 0)
    assert updates.is_newer("v0.10.0", "0.9.9")
    assert not updates.is_newer("v0.2.0", "0.2.0")
    assert not updates.is_newer("", "0.2.0")


def test_latest_release_reads_github_answer():
    seen = []

    def fake_fetch(url, timeout):
        seen.append(url)
        return {"tag_name": "v9.1.0", "html_url": f"{updates.REPO_URL}/releases/tag/v9.1.0",
                "body": "## Novedades\n- Algo nuevo.", "published_at": "2026-11-01T10:00:00Z",
                "assets": [{"name": "otro.txt", "browser_download_url": "https://x"},
                           {"name": updates.ASSET, "browser_download_url": ZIP_URL, "size": 1234,
                            "digest": "sha256:" + "AB" * 32}]}

    r = updates.latest_release(fetch=fake_fetch)
    assert seen == [updates.API_URL]
    assert r == {"version": "9.1.0", "url": f"{updates.REPO_URL}/releases/tag/v9.1.0",
                 "notas": "## Novedades\n- Algo nuevo.", "fecha": "2026-11-01",
                 "descarga": ZIP_URL, "tamano": 1234, "sha256": "ab" * 32}
    # Un enlace que no es del proyecto se cambia por la página de descargas, y sin ZIP no hay descarga.
    r = updates.latest_release(fetch=lambda u, t: {"tag_name": "v9.1.0", "html_url": "https://example.com/x"})
    assert r["url"] == updates.RELEASES_URL and r["descarga"] == "" and r["tamano"] == 0
    # Un ZIP que no viene de las descargas del proyecto no se usa.
    r = updates.latest_release(fetch=lambda u, t: {"tag_name": "v9.1.0", "assets": [
        {"name": updates.ASSET, "browser_download_url": "https://example.com/Jeronimo-Windows.zip", "size": 9}]})
    assert r["descarga"] == ""


def test_old_repository_name_still_finds_the_releases():
    # La app vieja (0.2.0) pregunta por «TraductordeLibros»: GitHub redirige al nombre nuevo.
    assert updates.REPO == "Willyssj3/Jeronimo"
    assert updates.DOWNLOAD_URL == f"https://github.com/Willyssj3/Jeronimo/releases/latest/download/{updates.ASSET}"


def test_check_update_is_remembered(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    calls = []

    def newer(*a, **k):
        calls.append(1)
        return {"version": "99.0.0", "url": updates.RELEASES_URL, "notas": "", "fecha": "", "descarga": ZIP_URL}

    monkeypatch.setattr(updates, "latest_release", newer)
    api = Api(mode="navegador")
    r = api.check_update()
    assert r["version"] == "99.0.0" and r["auto"] is False  # desde el código fuente no se actualiza sola
    assert api.check_update()["version"] == "99.0.0"  # recordado: no vuelve a consultar enseguida
    assert len(calls) == 1

    monkeypatch.setattr(updates, "latest_release", lambda *a, **k: {"version": __version__, "url": "", "notas": ""})
    assert api.check_update(force=True) is None  # ya es la última

    def offline(*a, **k):
        raise OSError("sin conexión")

    monkeypatch.setattr(updates, "latest_release", offline)
    assert api.check_update(force=True) is None


def test_open_url_only_project_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    api = Api(mode="navegador")
    assert api.open_url(updates.RELEASES_URL)
    assert opened == [updates.RELEASES_URL]
    for bad in ("https://example.com", "https://github.com/Willyssj3/JeronimoFalso", "file:///C:/x.exe"):
        with pytest.raises(ValueError):
            api.open_url(bad)


# -- descarga ---------------------------------------------------------------------------

class FakeResponse(io.BytesIO):
    def __init__(self, data, url="https://release-assets.githubusercontent.com/x"):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_download_checks_source_size_and_hash(tmp_path):
    data = b"PK" + b"x" * 5000
    good = hashlib.sha256(data).hexdigest()
    steps = []
    dest = tmp_path / "nuevo.zip"
    updates.download(ZIP_URL, dest, size=len(data), sha256=good, progress=steps.append,
                     opener=lambda req, timeout: FakeResponse(data))
    assert dest.read_bytes() == data and steps[-1] == 1.0
    cases = [
        ("https://example.com/Jeronimo-Windows.zip", {}, FakeResponse(data)),           # otro sitio
        (ZIP_URL, {"size": len(data) + 1}, FakeResponse(data)),                         # incompleta
        (ZIP_URL, {"sha256": "0" * 64}, FakeResponse(data)),                            # alterada
        (ZIP_URL, {}, FakeResponse(data, url="http://release-assets.githubusercontent.com/x")),  # sin https
        (ZIP_URL, {}, FakeResponse(data, url="https://example.com/x")),                 # redirige a otro sitio
    ]
    for url, kwargs, response in cases:
        out = tmp_path / "malo.zip"
        with pytest.raises(updates.UpdateError):
            updates.download(url, out, opener=lambda req, timeout, r=response: r, **kwargs)
        assert not out.exists() and not (tmp_path / "malo.zip.parcial").exists()
    with pytest.raises(updates.UpdateError, match="cancelada"):
        updates.download(ZIP_URL, tmp_path / "c.zip", should_stop=lambda: True,
                         opener=lambda req, timeout: FakeResponse(data))


def test_download_from_a_local_test_server_only_when_asked(tmp_path, monkeypatch):
    local = "http://127.0.0.1:8899/Jeronimo-Windows.zip"
    with pytest.raises(updates.UpdateError):
        updates.download(local, tmp_path / "a.zip", opener=lambda req, timeout: FakeResponse(b"PK", url=local))
    monkeypatch.setenv(updates.TEST_SOURCE_ENV, "http://127.0.0.1:8899")
    assert updates.api_url() == "http://127.0.0.1:8899/latest.json"
    updates.download(local, tmp_path / "a.zip", opener=lambda req, timeout: FakeResponse(b"PK", url=local))
    monkeypatch.setenv(updates.TEST_SOURCE_ENV, "http://example.com")  # fuera del equipo: no vale
    assert updates.api_url() == updates.API_URL


# -- descomprimir y reemplazar -------------------------------------------------------------

def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def make_app(folder, version="vieja", real_exe=False):
    (folder / "_internal").mkdir(parents=True)
    if real_exe:  # un programa de Windows que se abre y se cierra sin mostrar nada
        shutil.copy(r"C:\Windows\System32\rundll32.exe", folder / updates.EXE)
    else:
        (folder / updates.EXE).write_text(version)
    (folder / "_internal" / "base.dat").write_text(version)
    return folder


def test_unpack_puts_the_new_folder_next_to_the_app(tmp_path):
    app = make_app(tmp_path / "Jeronimo")
    archive = make_zip(tmp_path / "n.zip", {"Jeronimo/Jeronimo.exe": "nueva", "Jeronimo/_internal/base.dat": "nueva",
                                            "Jeronimo/LEEME.txt": "hola"})
    new = updates.unpack(archive, app)
    assert new == tmp_path / "Jeronimo.nuevo" and (new / updates.EXE).read_text() == "nueva"
    assert not (tmp_path / "Jeronimo.descomprimiendo").exists()
    # También sirve un ZIP con los archivos sueltos.
    loose = make_zip(tmp_path / "s.zip", {"Jeronimo.exe": "suelta", "_internal/base.dat": "x"})
    assert (updates.unpack(loose, app) / updates.EXE).read_text() == "suelta"


def test_unpack_rejects_broken_or_strange_zips(tmp_path):
    app = make_app(tmp_path / "Jeronimo")
    (tmp_path / "roto.zip").write_bytes(b"esto no es un zip")
    bad = [tmp_path / "roto.zip",
           make_zip(tmp_path / "sin-exe.zip", {"Jeronimo/otra.txt": "x"}),
           make_zip(tmp_path / "trampa.zip", {"../fuera.txt": "x", "Jeronimo/Jeronimo.exe": "x"})]
    for archive in bad:
        with pytest.raises(updates.UpdateError):
            updates.unpack(archive, app)
        assert not (tmp_path / "Jeronimo.nuevo").exists()
    assert not (tmp_path / "fuera.txt").exists()


def test_only_a_dedicated_app_folder_updates_itself(tmp_path):
    app = make_app(tmp_path / "Jeronimo")
    (app / "LEEME.txt").write_text("hola")
    assert updates.can_self_update(app)
    # Si la descomprimieron suelta en otra carpeta (Descargas, Escritorio…), no se toca.
    (app / "mis-fotos.jpg").write_text("x")
    assert not updates.can_self_update(app)
    assert not updates.can_self_update(tmp_path / "no-existe")
    assert updates.app_dir() is None  # desde el código fuente


def test_cleanup_removes_what_the_last_update_left(tmp_path):
    app = make_app(tmp_path / "Jeronimo")
    for suffix in updates.LEFTOVERS:
        (tmp_path / f"Jeronimo{suffix}").mkdir()
    updates.cleanup_leftovers(app)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Jeronimo"]


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="ayudante de PowerShell")
def test_swap_helper_replaces_the_folder_after_the_app_closes(tmp_path):
    base = tmp_path / "Programas con tildes y ñ"
    app = make_app(base / "Jeronimo", "vieja", real_exe=True)
    new = make_app(base / "Jeronimo.nuevo", "nueva", real_exe=True)
    # La «app» es un proceso que tarda un poco en cerrarse y mientras tanto usa la carpeta.
    fake_app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"], cwd=app)
    started = []
    updates.start_swap(app, new, fake_app.pid, popen=lambda cmd, **kw: started.append(subprocess.Popen(cmd, **kw)))
    fake_app.wait()
    started[0].wait(timeout=60)
    assert (app / "_internal" / "base.dat").read_text() == "nueva"
    assert (base / "Jeronimo.anterior" / "_internal" / "base.dat").read_text() == "vieja"
    assert not new.exists()


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="ayudante de PowerShell")
def test_swap_helper_keeps_the_old_version_if_the_new_one_is_missing(tmp_path):
    app = make_app(tmp_path / "Jeronimo", "vieja", real_exe=True)
    started = []
    updates.start_swap(app, tmp_path / "Jeronimo.nuevo", 999999,  # proceso que ya no existe
                       popen=lambda cmd, **kw: started.append(subprocess.Popen(cmd, **kw)))
    started[0].wait(timeout=60)
    assert (app / "_internal" / "base.dat").read_text() == "vieja"


def test_update_needs_the_portable_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(updates, "latest_release", lambda *a, **k: {
        "version": "99.0.0", "url": updates.RELEASES_URL, "notas": "", "descarga": ZIP_URL})
    api = Api(mode="navegador")
    with pytest.raises(ValueError, match="no se puede actualizar sola"):
        api.update_app()
    with pytest.raises(ValueError):
        api.restart_for_update()  # no hay nada descargado
    monkeypatch.setattr(updates, "latest_release", lambda *a, **k: {"version": __version__, "url": "", "notas": ""})
    with pytest.raises(ValueError, match="última versión"):
        api.update_app()


def test_update_task_downloads_and_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    app = make_app(tmp_path / "Jeronimo")
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as z:
        z.writestr("Jeronimo/Jeronimo.exe", "nueva")
        z.writestr("Jeronimo/_internal/base.dat", "nueva")
    data = archive_bytes.getvalue()
    monkeypatch.setattr(updates, "latest_release", lambda *a, **k: {
        "version": "99.0.0", "url": updates.RELEASES_URL, "notas": "", "descarga": ZIP_URL, "tamano": len(data)})
    monkeypatch.setattr(updates, "app_dir", lambda: app)
    real_download = updates.download
    monkeypatch.setattr(updates, "download", lambda url, dest, **kw: real_download(
        url, dest, opener=lambda req, timeout: FakeResponse(data), **kw))
    swaps, exits = [], []
    monkeypatch.setattr(updates, "start_swap", lambda folder, new, pid: swaps.append((folder, new)))
    monkeypatch.setattr("traductor.app.api.os._exit", lambda code: exits.append(code))
    api = Api(mode="navegador")
    assert api.check_update(force=True)["auto"] is True
    api.update_app()
    for _ in range(100):
        if not api.task_status()["activa"]:
            break
        time.sleep(0.05)
    st = api.task_status()
    assert st["error"] is None and st["resultado"] == {"version": "99.0.0"}
    assert ((tmp_path / "Jeronimo.nuevo") / updates.EXE).read_text() == "nueva"
    assert api.restart_for_update() is True
    assert swaps == [(app, tmp_path / "Jeronimo.nuevo")]
    time.sleep(1)
    assert exits == [0]
    # Al volver a abrir, avisa que se actualizó (una sola vez).
    monkeypatch.setattr("traductor.app.api.__version__", "99.0.0")
    assert api.startup_notices()["actualizado_a"] == "99.0.0"
    assert api.startup_notices()["actualizado_a"] is None
