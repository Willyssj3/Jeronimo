"""Jerónimo con una IA simulada y libros inventados, para probar la interfaz.

Uso:  python tools/servidor_prueba.py CARPETA_DE_TRABAJO [PUERTO] [IDIOMA]

Crea en CARPETA_DE_TRABAJO un libro de prueba (book.epub) y un par de
referencia (ref_en.epub y ref_es.epub), abre la app en modo navegador con
una IA falsa (no gasta nada) e imprime la dirección (con su token). IDIOMA
(es, en o pt) fija el idioma de la interfaz. Nunca uses libros reales aquí."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    work = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    lang = sys.argv[3] if len(sys.argv) > 3 else "es"
    sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src")]
    os.environ["APPDATA"] = str(work / "appdata")  # ajustes de prueba, aparte de los reales
    from helpers import make_epub
    from test_pipeline import BODY, REF_SRC, REF_TGT, FakeAI

    from traductor import i18n
    from traductor.app.api import Api
    from traductor.app.server import serve

    work.mkdir(parents=True, exist_ok=True)
    chapters = [("The Harbor", BODY),
                ("Second", "<p>Mirela slept <i>deeply</i> that night.</p><p>The wind kept blowing from the north.</p>"),
                ("Credits", "<p>Published by Nobody Press.</p>")]
    make_epub(work / "book.epub", chapters)
    make_epub(work / "ref_en.epub", REF_SRC, title="Nets")
    make_epub(work / "ref_es.epub", REF_TGT, lang="es", title="Redes")
    i18n.set_language(lang)
    api = Api(mode="navegador", provider_factory=lambda cfg: FakeAI())
    server, url = serve(api, port=port, open_browser=False)
    print(url, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
