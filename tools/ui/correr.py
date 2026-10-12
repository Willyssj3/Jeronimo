"""Corre una prueba de pantalla (Playwright) contra el servidor de prueba.

Uso:  python tools/ui/correr.py ui_test.mjs [PUERTO] [IDIOMA]

IDIOMA (es, en o pt; por defecto es) es el idioma de la interfaz. Necesita Node.js y
Playwright (npm install -g playwright; npx playwright install chromium). Las capturas
quedan en pruebas-ui/<nombre>/shots, o pruebas-ui/<nombre>-<idioma>/shots (fuera de Git)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main() -> int:
    script = HERE / Path(sys.argv[1]).name
    port = sys.argv[2] if len(sys.argv) > 2 else "8765"
    lang = sys.argv[3] if len(sys.argv) > 3 else "es"
    work = ROOT / "pruebas-ui" / (script.stem if lang == "es" else f"{script.stem}-{lang}")
    shutil.rmtree(work, ignore_errors=True)
    (work / "shots").mkdir(parents=True)
    log = open(work / "servidor.log", "w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, str(ROOT / "tools" / "servidor_prueba.py"), str(work), port, lang],
                              stdout=subprocess.PIPE, stderr=log, text=True)
    try:
        url = server.stdout.readline().strip()
        if not url.startswith("http"):
            print("El servidor de prueba no arrancó; mira", work / "servidor.log")
            return 1
        time.sleep(0.3)
        env = dict(os.environ)
        if "NODE_PATH" not in env:
            npm_root = subprocess.run("npm root -g", shell=True, capture_output=True, text=True).stdout.strip()
            env["NODE_PATH"] = npm_root
        env["UI_IDIOMA"] = lang  # por variable: el cuarto argumento ya lo usa ui_reader_test (claro u oscuro)
        done = subprocess.run(["node", str(script), url, str(work), str(work / "shots")], env=env)
        print(f"[{script.stem}] código {done.returncode}")
        return done.returncode
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
