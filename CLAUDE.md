# Jerónimo — guía para Claude Code

Jerónimo es una app de escritorio para Windows, libre y gratuita, que traduce libros EPUB con IA
imitando la voz del traductor oficial del autor: aprende de otros libros del mismo autor junto con
su traducción oficial (pares de referencia). Repositorio público: https://github.com/Willyssj3/TraductordeLibros

## Cómo trabajamos (léelo primero)

- **Escribe siempre en español** al usuario: respuestas, preguntas, descripciones de comandos,
  nombres de tareas y mensajes de commit. Él no programa: explica qué hiciste y para qué sirve,
  con palabras simples y sin jerga.
- Cuando haya que decidir algo que es suyo (funciones, diseño, publicar), pregúntale con opciones
  para elegir (herramienta AskUserQuestion), con la recomendada primero y marcada «(Recomendado)».
  Lo técnico resuélvelo tú.
- Trabaja en la rama `claude/book-translation-spanish-vr753r` (la única del repo). Haz commits
  descriptivos en español (título corto + viñetas) y súbelos con `git push -u origin <rama>`.
- No abras pull requests ni publiques versiones sin que el usuario lo pida explícitamente.
- Antes de decir que algo está listo: `pytest -q` sin fallos y, si tocaste la interfaz, pruébala
  (ver «Probar la interfaz») y muéstrale capturas.
- Lleva el avance en el tablero de Trello del proyecto (el usuario te pasa el link), con las listas
  «Pendiente», «En curso», «Hecho» e «Ideas»: una tarjeta por tarea (descripción simple y lista de
  pasos), movida a «En curso» al empezar y a «Hecho» al terminar. Las tareas nuevas que surjan,
  como tarjeta nueva.

## Reglas que no se rompen

- **Nunca subas libros** (EPUB, PDF, MOBI…), ni proyectos de traducción, ni textos con derechos de
  autor: el repositorio es público. `.gitignore` ya los excluye. Las pruebas usan libros inventados
  (`tests/helpers.py`, `tests/pdfbook.py`).
- No copies en el chat, el código ni las pruebas fragmentos de libros reales del usuario.
- Las claves de API nunca van al repo ni a los proyectos (se guardan en el almacén de Windows).
- Todo texto visible nuevo va marcado para traducir (ver «Idiomas de la interfaz»).

## Puesta en marcha en Windows

```
git clone -b claude/book-translation-spanish-vr753r https://github.com/Willyssj3/TraductordeLibros.git
cd TraductordeLibros
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .[dev,escritorio]   # «escritorio»: ventana propia y claves en el almacén de Windows
pytest -q
jeronimo              # abre la app (ventana); «jeronimo --navegador» la abre en el navegador
```

Requisitos: Python 3.11 o más nuevo (mejor 3.12), Git. Opcionales: Node.js + Playwright para las
pruebas de pantalla; Tesseract para la prueba de OCR (sin él, esa prueba se salta); Calibre para
convertir MOBI/AZW3.

## Probar la interfaz

- `python tools/servidor_prueba.py CARPETA [PUERTO] [es|en|pt]`: abre la app en modo navegador con
  una **IA simulada** (no gasta nada) y libros inventados (`book.epub`, `ref_en.epub`,
  `ref_es.epub` dentro de CARPETA). Imprime la dirección con su token.
- `python tools/ui/correr.py ui_test.mjs [PUERTO] [es|en|pt]`: corre una prueba de pantalla de
  `tools/ui/` (Playwright) contra ese servidor; las capturas quedan en `pruebas-ui/` (ignorada por
  Git). Requiere `npm install -g playwright` y `npx playwright install chromium`.
- Pruebas incluidas: flujo completo (`ui_test`), revisión, calidad, buscar y reemplazar, borrar
  traducción, perfiles, paquete para Proyectos, lectura, modo manual con plan, formatos de entrada
  (todas en español, buscan los botones por su texto).
- `ui_idiomas_test.mjs` recorre las 38 pantallas en el idioma pedido (`correr.py
  ui_idiomas_test.mjs 8765 en`), busca los botones por su texto traducido y avisa de textos de la
  interfaz que sigan en español (lista en `pruebas-ui/ui_idiomas_test-en/textos-a-revisar.txt`).
  Córrela en `en` y `pt` cada vez que agregues textos o pantallas.

## Arquitectura

- `src/traductor/` (paquete Python):
  - `epub.py` lee y escribe EPUB (2 y 3), portada y metadatos. `inline.py`: el texto de cada párrafo
    se guarda «marcado» (`<g1>…</g1>` formato, `<x1/>` elementos fijos como notas o imágenes) para
    que la IA conserve cursivas, enlaces y notas; `repair()` arregla marcas perdidas.
  - `project.py`: la carpeta de un proyecto (`proyecto.json`, `capitulos/NNN.json`, `referencias/`,
    `conocimiento/` con guía de estilo, glosario y análisis, `portada/`, `salida/`). Exporta el EPUB.
  - `align.py` + `retrieval.py`: emparejan las referencias párrafo a párrafo y eligen ejemplos.
  - `prompts.py` (instrucciones a la IA, en inglés; las notas para el usuario salen en el idioma de
    la interfaz), `translate.py` (traducción por bloques, modo manual, chats/modelos/planes),
    `knowledge.py` (guía de estilo, glosario oficial, análisis del libro, términos), `review.py`
    (segunda pasada con IA), `qa.py` (control de calidad sin IA), `search.py` (buscar y reemplazar),
    `profiles.py` (perfiles de autor), `cover.py` (portada traducida), `export_docs.py` (Word y PDF),
    `convert.py` + `pdf2epub.py` + `ocr.py` (convertir PDF, Word, TXT, FB2, MOBI a EPUB, con OCR),
    `updates.py` (aviso de versión nueva), `i18n.py` + `locales/` (idiomas de la interfaz),
    `providers/` (Anthropic, OpenAI y compatibles, Gemini, Ollama…), `cli.py` (línea de comandos,
    solo en español).
  - `app/`: `main.py` arranca la ventana (pywebview) o el navegador; `server.py` es el servidor
    local del modo navegador (con token); `api.py` es la API que llama la interfaz (todo método
    público de `Api` se llama desde JS con `rpc("nombre", …args)` y devuelve `{ok, resultado|error}`);
    las tareas largas van en segundo plano con `TaskRunner` (una a la vez; la interfaz consulta
    `task_status`; cada tarea tiene `nombre` visible y `clave` fija).
  - `app/ui/`: `index.html`, `app.js` (sin frameworks; helper `h()` para crear elementos, vistas
    `render*`), `app.css` (estética del logo: azul tinta y dorado, tipografía EB Garamond), `fonts/`.
- Ajustes del usuario: `%APPDATA%\TraductorDeLibros\ajustes.json` (recientes, idioma, aviso de
  versión). Errores imprevistos: `registro.txt` en esa misma carpeta.
- `packaging/`: PyInstaller (`Jeronimo.spec`), instalador Inno Setup (`instalador.iss`), prueba del
  OCR de Windows. `.github/workflows/build.yml`: pruebas en Linux, compilación del instalador y la
  versión portátil en Windows, y publicación de versiones.
- `docs/PLAN.md` (plan y decisiones), `docs/versiones/vX.Y.Z.md` (notas de cada versión).

## Idiomas de la interfaz (español, inglés, portugués)

- El español es el original y la **clave** de las traducciones: en `app.js` se escribe
  `t("Texto en español")` o `t("Hay {n} capítulos", { n })`; en Python `tr("…", n=…)`. Para textos
  en constantes que se leen al cargar, marca con `N_("…")` y traduce al mostrarlos (`t(x)`/`tr(x)`).
  En `index.html`, los textos fijos llevan `data-t` (y `data-t-title` para el `title`).
- Las traducciones están en `src/traductor/locales/en.json` y `pt.json` (las usan Python y la
  interfaz). `python tools/i18n_claves.py` dice qué falta o sobra; `python tools/i18n_claves.py
  faltan en` lista lo que falta en JSON. `tests/test_i18n.py` falla si falta alguna traducción o si
  no coinciden los `{marcadores}`. Inglés de EE. UU. y portugués de Brasil (con «você»); en ambos
  las comillas son “ ” en lugar de « ».
- Los números con unidad o plural van con `plural(n, t("párrafo"), t("párrafos"))` dentro del
  texto (`t("Traducción borrada: {parrafos}.", { parrafos: … })`), no «{n} párrafos», para que
  no salga «1 párrafos».
- El idioma se elige en la barra superior y se guarda en los ajustes; la primera vez se usa el del
  sistema. Las notas que escribe la IA (dudas, motivos de la revisión, guía, resúmenes) salen en el
  idioma elegido en ese momento.

## Publicar una versión (solo si el usuario lo pide)

1. Subir `__version__` en `src/traductor/__init__.py` (por ejemplo `0.3.0`).
2. Escribir las notas en `docs/versiones/v0.3.0.md` (en español, para el usuario final) y
   actualizar el README si hace falta.
3. Commit y push; esperar que las pruebas pasen.
4. Ejecutar el flujo «Pruebas e instalador de Windows» a mano con el campo `publicar` = `v0.3.0`
   (`gh workflow run build.yml --ref claude/book-translation-spanish-vr753r -f publicar=v0.3.0`, o
   en GitHub: Actions → el flujo → «Run workflow»). Crea la etiqueta y la versión con el
   instalador y el ZIP portátil. La app avisa sola a quienes tienen una versión anterior.

## Estado y pendientes

Versión publicada: **0.2.0**. Desde entonces se agregaron (sin publicar todavía): control de
calidad, buscar y reemplazar, vista de lectura con voz, segunda pasada con IA, paquete para
Proyectos de los chats, perfiles de autor, portada traducida (o una imagen propia), datos del libro
editables, exportar a Word y PDF, convertir desde Word/TXT/FB2/MOBI/AZW3, OCR para PDF escaneados,
modo manual con elección de modelo y plan, borrar la traducción de un capítulo y la interfaz
completa en inglés y portugués (verificada pantalla por pantalla el 12/10/2026).

Pendiente, en este orden (lo fijó el usuario):
1. **Actualizar el README** con todas las funciones nuevas desde la 0.2.0 y capturas actuales
   (siempre con libros inventados). Ya explica cómo cambiar el idioma.
2. **Preparar la versión 0.3.0** (número y notas en `docs/versiones/v0.3.0.md`) y preguntarle al
   usuario antes de publicarla (ver «Publicar una versión»).
3. **Corregir los avisos de calidad con un clic** (después de la 0.3.0; el usuario eligió el
   diseño): botón «Corregir» en cada aviso y uno para todos los de un tipo. Lo mecánico (espacios,
   signos ¿ ¡, guion → raya, nombres con dos grafías) se corrige al instante y se puede deshacer;
   lo que necesita entender el texto (glosario, palabras sin traducir, números, omisiones) lo
   propone la IA para ese párrafo y se acepta o descarta como en la revisión con IA (en modo
   manual, se copia al chat).
4. **Instalador firmado** (sin el aviso de Windows SmartScreen): en pausa hasta que el usuario lo
   pida. Plan: el usuario solicita el certificado gratuito para proyectos de código abierto de
   SignPath Foundation (verificar los requisitos actuales en signpath.org); después se agrega un
   paso de firma al flujo de GitHub con los secretos que den. Explicarle los pasos de forma simple.
5. **Proponer mejoras** con opciones para elegir. Ideas ya anotadas: los títulos que agrega el
   conversor de PDF a EPUB («Notas», «Índice», «Portada») salen en español aunque el libro esté en
   otro idioma; las reglas de rayas de diálogo y signos de apertura del control de calidad son
   solo para español, pero el libro se puede traducir a otros idiomas (`LANG_NAMES` en `prompts.py`).
