"""Capturas del README con un libro de muestra inventado (texto propio, sin derechos de autor).

Uso:  python tools/capturas_readme.py      → guarda las imágenes en docs/capturas/

Arma un cuento corto en inglés con su traducción al español escrita a mano, abre la
app con una IA simulada y saca las capturas con Playwright (tools/ui/capturas_readme.mjs).
Para no mostrar rutas personales, la carpeta del usuario aparece como C:\\Users\\Lector."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src")]

CHAPTERS = [
    ("The Harbor",
     "<h1>The Harbor</h1>"
     "<p>Mirela walked to Harrowgate before dawn, as she did every <i>single</i> day, with the salt wind at her back.</p>"
     "<p>The fishing boats were still asleep against the pier, their lanterns swinging like tired eyes.</p>"
     "<p>“The boats are late,” said the keeper<a href='#n1'><sup>1</sup></a>, without looking up from his nets.</p>"
     "<p>“They are always late in October,” she answered. “The sea likes to keep them a little longer.”</p>"
     "<p>Tobias laughed, a dry sound that the gulls repeated from the rooftops.</p>"
     "<p>She looked at the grey water for a long time, as if the answer to some old question were written there.</p>"),
    ("The Festival",
     "<h1>The Festival</h1>"
     "<p>That night the whole of Harrowgate came down to the square, where the lanterns hung from string like a second sky.</p>"
     "<p>“Are you dancing?” asked Tobias.</p>"
     "<p>Mirela smiled and took his hand, and for a moment the wind forgot to blow.</p>"),
]

# Traducción escrita a mano. En «La fiesta» hay dos errores a propósito (el glosario y un «¿»)
# para que el control de calidad y la revisión con IA tengan algo que mostrar.
TRANSLATION = {
    "The Harbor": [
        "El puerto",
        "Mirela caminó hasta Puertoarado antes del alba, como cada <g1>santo</g1> día, con el viento salado a la espalda.",
        "Los barcos pesqueros aún dormían contra el muelle, con los faroles balanceándose como ojos cansados.",
        "—Los barcos llegan tarde —dijo el farero<x1/>, sin levantar la vista de sus redes.",
        "—En octubre siempre llegan tarde —respondió ella—. Al mar le gusta retenerlos un poco más.",
        "Tobías se rió, un sonido seco que las gaviotas repitieron desde los tejados.",
        "Ella se quedó mirando el agua gris durante largo rato, como si la respuesta a alguna vieja pregunta estuviera escrita allí.",
    ],
    "The Festival": [
        "La fiesta",
        "Esa noche todo Harrowgate bajó a la plaza, donde los faroles colgaban de los cordeles como un segundo cielo.",
        "—Bailas? —preguntó Tobías.",
        "Mirela sonrió y le tomó la mano, y por un momento el viento se olvidó de soplar.",
    ],
}

REVIEW_FIX = (
    '<fix id="1"><text>Esa noche todo Puertoarado bajó a la plaza, donde los faroles colgaban de los cordeles '
    'como un segundo cielo.</text><why>El glosario oficial traduce «Harrowgate» como «Puertoarado».</why></fix>\n'
    '<fix id="2"><text>—¿Bailas? —preguntó Tobías.</text><why>Falta el signo de apertura de la pregunta.</why></fix>')

STYLE = """## Voz del traductor oficial
- Español neutro, con tuteo entre amigos y usted con los desconocidos.
- Diálogos con raya (—); las acotaciones del narrador, entre rayas.
- Frases cortas y concretas: el mar y el viento aparecen casi como personajes.

## Nombres y lugares
- Los nombres de personas se mantienen (Mirela; Tobías, con tilde).
- Los lugares con nombre «parlante» se traducen: Harrowgate → Puertoarado.
"""

GLOSSARY = [
    {"termino": "Harrowgate", "traduccion": "Puertoarado", "tipo": "lugar", "nota": "nombre parlante", "origen": "oficial"},
    {"termino": "keeper", "traduccion": "farero", "tipo": "oficio", "nota": "el del viejo faro", "origen": "oficial"},
    {"termino": "Tobias", "traduccion": "Tobías", "tipo": "persona", "nota": "con tilde", "origen": "oficial"},
    {"termino": "nets", "traduccion": "redes", "tipo": "objeto", "nota": "", "origen": "propuesta"},
]

SYNOPSIS = ("Mirela, una pescadora de Puertoarado, y Tobías, el viejo farero, esperan el regreso de los barcos "
            "en un otoño ventoso. La fiesta del pueblo los reúne bajo los faroles.")


def marks(text: str) -> list[str]:
    return sorted(re.findall(r"</?[gx]\d+/?>", text))


def main() -> int:
    from helpers import make_epub
    from test_pipeline import REF_SRC, REF_TGT, FakeAI

    from traductor import i18n, updates
    from traductor.app.api import Api

    updates.latest_release = lambda *a, **k: None  # sin avisos de versión nueva en las capturas
    from traductor.app.server import serve
    from traductor.epub import EpubBook

    # Dónde está Playwright (antes de cambiar APPDATA, que es donde npm guarda lo global).
    node_path = os.environ.get("NODE_PATH") or subprocess.run(
        "npm root -g", shell=True, capture_output=True, text=True).stdout.strip()
    work = ROOT / "pruebas-ui" / "capturas-readme"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    notes = "<p id='n1'><sup>1</sup> Tobias, keeper of the old lighthouse since the storm of 1987.</p>"
    chapters = [CHAPTERS[0][0:1] + (CHAPTERS[0][1] + notes,), CHAPTERS[1]]
    make_epub(work / "book.epub", chapters, title="The Salt Keeper")
    make_epub(work / "ref_en.epub", REF_SRC, title="Nets")
    make_epub(work / "ref_es.epub", REF_TGT, lang="es", title="Redes")

    # Las marcas de formato de la traducción tienen que coincidir con las del original.
    translation = {k: list(v) for k, v in TRANSLATION.items()}
    with EpubBook(work / "book.epub") as book:
        for doc in book.documents:
            title = book.doc_title(doc)
            if title not in translation:
                continue
            segs = book.segments(doc)
            if title == "The Harbor":
                translation[title].append("<x1/> Tobías, farero del viejo faro desde la tormenta de 1987.")
            for seg, text in zip(segs, translation[title]):
                if marks(seg.source) != marks(text):
                    raise SystemExit(f"Marcas distintas en «{seg.source}» → «{text}»")
            if len(segs) != len(translation[title]):
                raise SystemExit(f"{title}: {len(segs)} párrafos en el libro y {len(translation[title])} traducidos")

    os.environ["APPDATA"] = str(work / "appdata")
    os.environ["USERPROFILE"] = r"C:\Users\Lector"  # rutas de ejemplo en lugar de las personales
    i18n.set_language("es")
    api = Api(mode="navegador", provider_factory=lambda cfg: FakeAI())
    server, url = serve(api, port=8766, open_browser=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (work / "demo.json").write_text(json.dumps({
        "traduccion": translation, "revision": REVIEW_FIX, "estilo": STYLE, "glosario": GLOSSARY,
        "sinopsis": SYNOPSIS, "titulo": "El guardián de la sal"}, ensure_ascii=False), encoding="utf-8")
    out = ROOT / "docs" / "capturas"
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, NODE_PATH=node_path)
    time.sleep(0.3)
    done = subprocess.run(["node", str(ROOT / "tools" / "ui" / "capturas_readme.mjs"), url, str(work), str(out)], env=env)
    server.shutdown()
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main())
