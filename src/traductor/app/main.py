"""Arranque de Jerónimo: ventana nativa (pywebview) o, si no es posible, el navegador."""

from __future__ import annotations

import argparse
import sys

from .api import Api, Bridge, _log_error
from .server import UI_DIR, serve


def _say(msg: str) -> None:
    # En el .exe sin consola no hay salida estándar.
    if sys.stdout:
        print(msg)


def run_browser(port: int = 0, open_browser: bool = True) -> int:
    api = Api(mode="navegador")
    server, url = serve(api, port=port, open_browser=open_browser)
    _say(f"Jerónimo abierto en {url}")
    _say("Para salir, pulsa «Salir» en la app o cierra esta ventana.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jeronimo", description="Jerónimo, traductor de libros con IA")
    parser.add_argument("--navegador", action="store_true", help="abrir la app en el navegador")
    parser.add_argument("--puerto", type=int, default=0)
    parser.add_argument("--sin-abrir", action="store_true", help="no abrir el navegador automáticamente")
    args = parser.parse_args(argv)

    if not args.navegador:
        try:
            import webview
        except ImportError:
            args.navegador = True
    if args.navegador:
        return run_browser(args.puerto, not args.sin_abrir)

    api = Api(mode="escritorio")
    try:
        window = webview.create_window(
            "Jerónimo — Traductor de libros", url=str(UI_DIR / "index.html"), js_api=Bridge(api),
            width=1280, height=840, min_size=(960, 640), text_select=True)
        api._window = window
        webview.start()
    except Exception:  # sin WebView2 u otro problema gráfico: usar el navegador
        import traceback

        _log_error(traceback.format_exc())
        return run_browser()
    return 0


if __name__ == "__main__":
    sys.exit(main())
