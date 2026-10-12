"""Prueba completa de la actualización automática con la app empaquetada (solo Windows).

Uso:  python tools/prueba_actualizacion.py CARPETA_DIST_JERONIMO

CARPETA_DIST_JERONIMO es la carpeta que arma PyInstaller (la que tiene
Jeronimo.exe y _internal). La prueba:

1. copia esa carpeta a %TEMP%\\prueba-jeronimo\\Jeronimo (la «versión vieja»);
2. arma un ZIP «Jeronimo-Windows.zip» con la misma app más un archivo marcador
   (la «versión nueva», 9.9.9) y lo sirve con un GitHub falso en 127.0.0.1;
3. abre la app vieja en modo navegador, le pide que busque la versión nueva, la
   descargue y se reinicie, todo por su API;
4. comprueba que la carpeta quedó cambiada, que la app se volvió a abrir sola y
   que la copia vieja se borró al arrancar. Al final cierra la app de prueba.

No toca los ajustes reales (usa un APPDATA de prueba) ni la carpeta original."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

PORT = 8899
EXE = "Jeronimo.exe"


def call(base: str, token: str, name: str, *args):
    request = urllib.request.Request(f"{base}/api/{name}", data=json.dumps({"args": list(args)}).encode(),
                                     headers={"Content-Type": "application/json", "X-TDL-Token": token})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    if not result.get("ok"):
        raise RuntimeError(f"{name}: {result.get('error')}")
    return result["resultado"]


def processes_from(folder: Path) -> list[int]:
    script = (f"Get-Process -Name Jeronimo -ErrorAction SilentlyContinue | "
              f"Where-Object {{ $_.Path -like '{folder}*' }} | ForEach-Object {{ $_.Id }}")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, text=True)
    return [int(x) for x in done.stdout.split()]


def main() -> int:
    dist = Path(sys.argv[1]).resolve()
    if not (dist / EXE).is_file():
        print(f"No encuentro {EXE} en {dist}")
        return 2
    work = Path(tempfile.gettempdir()) / "prueba-jeronimo"
    shutil.rmtree(work, ignore_errors=True)
    app = work / "Jeronimo"
    shutil.copytree(dist, app)
    (app / "LEEME.txt").write_text("Prueba", encoding="utf-8")

    # La «versión nueva»: la misma app con un archivo marcador, en un ZIP como el que publica GitHub.
    served = work / "github"
    served.mkdir()
    new_src = work / "fuente-nueva" / "Jeronimo"
    shutil.copytree(dist, new_src)
    (new_src / "_internal" / "MARCA-VERSION-NUEVA.txt").write_text("9.9.9", encoding="utf-8")
    archive = served / "Jeronimo-Windows.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for f in new_src.rglob("*"):
            z.write(f, Path("Jeronimo") / f.relative_to(new_src))
    base_url = f"http://127.0.0.1:{PORT}"
    (served / "latest.json").write_text(json.dumps({
        "tag_name": "v9.9.9", "html_url": "https://github.com/Willyssj3/Jeronimo/releases/tag/v9.9.9",
        "body": "Versión de prueba", "published_at": "2026-10-12T00:00:00Z",
        "assets": [{"name": "Jeronimo-Windows.zip", "browser_download_url": f"{base_url}/Jeronimo-Windows.zip",
                    "size": archive.stat().st_size}]}), encoding="utf-8")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(served), **k)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    address_file = work / "direccion.txt"
    env = dict(os.environ, APPDATA=str(work / "appdata"), JERONIMO_PRUEBA_ACTUALIZACION=base_url,
               JERONIMO_DIRECCION_EN=str(address_file))
    old = subprocess.Popen([str(app / EXE), "--navegador", "--sin-abrir", "--puerto", "8897"], env=env, cwd=app)
    ok = True
    try:
        for _ in range(120):
            if address_file.exists() and address_file.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.5)
        url = address_file.read_text(encoding="utf-8").strip()
        base, token = url.split("/?token=")
        print("· la versión vieja abrió en", base)
        info = call(base, token, "check_update", True)
        print("· versión nueva encontrada:", info["version"], "| se actualiza sola:", info["auto"])
        if not info["auto"]:
            print("ERROR: la app no se ofreció a actualizarse sola")
            return 1
        call(base, token, "update_app")
        for _ in range(240):
            st = call(base, token, "task_status")
            if not st["activa"]:
                break
            time.sleep(0.5)
        print("· descarga:", st["mensaje"], "| error:", st["error"])
        if st["error"]:
            return 1
        print("· nueva carpeta preparada:", (work / "Jeronimo.nuevo").is_dir())
        call(base, token, "restart_for_update")
        old.wait(timeout=30)
        print("· la versión vieja se cerró")
        marker = app / "_internal" / "MARCA-VERSION-NUEVA.txt"
        for _ in range(120):
            if marker.exists():
                break
            time.sleep(0.5)
        print("· carpeta cambiada por la versión nueva:", marker.exists())
        ok &= marker.exists()
        relaunched = []
        for _ in range(60):
            relaunched = processes_from(app)
            if relaunched:
                break
            time.sleep(0.5)
        print("· la app se volvió a abrir sola:", bool(relaunched))
        ok &= bool(relaunched)
        for _ in range(60):
            if not (work / "Jeronimo.anterior").exists():
                break
            time.sleep(0.5)
        print("· la copia vieja se borró al arrancar:", not (work / "Jeronimo.anterior").exists())
        ok &= not (work / "Jeronimo.anterior").exists()
        log = Path(tempfile.gettempdir()) / "jeronimo-actualizar.log"
        if log.exists():
            print("· registro del ayudante:", log.read_text(encoding="utf-8-sig").strip().splitlines()[-2:])
    finally:
        for pid in processes_from(app):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        if old.poll() is None:
            old.kill()
        server.shutdown()
    print("RESULTADO:", "bien" if ok else "FALLÓ")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
