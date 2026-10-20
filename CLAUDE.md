# Jerónimo — guía para Claude Code

Jerónimo es una app de escritorio para Windows, libre y gratuita, que traduce libros EPUB con IA
imitando la voz del traductor oficial del autor: aprende de otros libros del mismo autor junto con
su traducción oficial (pares de referencia). Repositorio público: https://github.com/Willyssj3/Jeronimo
(se llamaba «TraductordeLibros» y era privado hasta el 12/10/2026; GitHub redirige el nombre viejo).

## Cómo trabajamos (léelo primero)

- **Escribe siempre en español** al usuario: respuestas, preguntas, descripciones de comandos,
  nombres de tareas y mensajes de commit. El usuario no programa: explica qué hiciste y para qué
  sirve, con palabras simples y sin jerga.
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
git clone -b claude/book-translation-spanish-vr753r https://github.com/Willyssj3/Jeronimo.git
cd Jeronimo
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
  (todas en español, buscan los botones por su texto). `ui_qa_fix_test` (corregir los avisos con
  un clic, deshacer, unificar nombres, propuestas de la IA conectada y en modo manual) y
  `ui_proyectos_test` (renombrar proyectos, quitar de recientes y deshacer) sirven en los tres
  idiomas: `correr.py ui_qa_fix_test.mjs 8771 en`, `correr.py ui_proyectos_test.mjs 8772 pt`.
- `python tools/prueba_actualizacion.py CARPETA_DIST`: prueba de punta a punta de la actualización
  automática con la app empaquetada (`pyinstaller` desde `packaging/`): un GitHub falso local
  sirve una «versión 9.9.9», la app vieja la descarga, se cierra, el ayudante cambia la carpeta y
  la app se vuelve a abrir sola (abre una ventana de Jerónimo unos segundos). Córrela antes de
  publicar si tocaste `updates.py`, `main.py` o el empaquetado.
- `ui_idiomas_test.mjs` recorre las 38 pantallas en el idioma pedido (`correr.py
  ui_idiomas_test.mjs 8765 en`), busca los botones por su texto traducido y avisa de textos de la
  interfaz que sigan en español (lista en `pruebas-ui/ui_idiomas_test-en/textos-a-revisar.txt`).
  Córrela en `en` y `pt` cada vez que agregues textos o pantallas.
- `python tools/capturas_readme.py`: rehace las capturas del README (`docs/capturas/`) con un
  cuento inventado («The Salt Keeper», traducción escrita a mano) y rutas de ejemplo
  (`C:\Users\Lector`). Úsalo cuando cambie el aspecto de la app; revisa las imágenes antes de
  subirlas.

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
    (segunda pasada con IA), `qa.py` (control de calidad sin IA), `qa_fix.py` (corregir sus avisos:
    lo mecánico al instante sobre el texto plano sin tocar las marcas, con una pila de «deshacer»
    en `conocimiento/correcciones_calidad.json`; lo demás, una propuesta de la IA que queda como
    `sugerencia` del párrafo con `origen: "calidad"`), `search.py` (buscar y reemplazar),
    `profiles.py` (perfiles de autor), `cover.py` (portada traducida), `export_docs.py` (Word y PDF),
    `convert.py` + `pdf2epub.py` + `ocr.py` (convertir PDF, Word, TXT, FB2, MOBI a EPUB, con OCR),
    `updates.py` (versión nueva: aviso, descarga del ZIP, reemplazo de la carpeta con un ayudante
    de PowerShell y vuelta atrás), `components.py` (lo que puede faltar: WebView2, Calibre con
    winget, páginas de Configuración de Windows, accesos directos), `support.py` (enlaces de
    «Apoyar el proyecto»: si están vacíos no se muestra nada; solo se abren páginas de cada
    servicio), `i18n.py` + `locales/`
    (idiomas de la interfaz),
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
  versión). Errores imprevistos: `registro.txt` en esa misma carpeta. Se cambian siempre con
  `Api._update_settings()` (lee, cambia y guarda bajo un cerrojo): la interfaz hace llamadas a la
  vez, y antes la consulta de versión nueva guardaba una copia vieja y borraba recientes.
- El proyecto tiene `nombre` (el que se ve y se puede cambiar; da nombre a los archivos exportados)
  y `titulo` (el título original del libro, que va en las instrucciones a la IA y no cambia).
- `packaging/`: PyInstaller (`Jeronimo.spec`, carpeta con `Jeronimo.exe` y `_internal`),
  `LEEME.txt` (va dentro de la carpeta), prueba del OCR de Windows. `.github/workflows/build.yml`:
  pruebas en Linux, compilación en Windows y publicación. **Se distribuye solo como carpeta
  portable** (decisión del usuario, 12/10/2026): `Jeronimo-Windows.zip`, siempre con ese nombre,
  así `https://github.com/Willyssj3/Jeronimo/releases/latest/download/Jeronimo-Windows.zip`
  baja siempre la última versión y la app la reconoce para actualizarse sola. No hay instalador.
- Actualización automática: al abrir, si hay versión nueva, la app pregunta; con «Actualizar
  ahora» descarga el ZIP (solo de las descargas del proyecto; tamaño y SHA-256), lo descomprime en
  `<carpeta>.nuevo`, se cierra y el ayudante pone la carpeta nueva en lugar de la vieja (que queda
  como `<carpeta>.anterior` hasta que la nueva arranca) y la vuelve a abrir. Solo se reemplaza una
  carpeta que tenga únicamente los archivos de la app (`updates.APP_FILES`); si no, abre la página.
  La primera vez ofrece accesos directos (escritorio y menú Inicio).
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
3. Commit y push; esperar que las pruebas pasen (`gh run list --branch <rama>`).
4. Ejecutar el flujo «Pruebas y versión portable de Windows» a mano con el campo `publicar` =
   `v0.3.0` (`gh workflow run build.yml --ref claude/book-translation-spanish-vr753r -f
   publicar=v0.3.0`, o en GitHub: Actions → el flujo → «Run workflow»). Crea la etiqueta y la
   versión con `Jeronimo-Windows.zip`. La app avisa sola a quienes tienen una versión anterior.
5. Comprobar que el enlace fijo de descarga baja el ZIP nuevo y que el aviso llega (la API pública
   `https://api.github.com/repos/Willyssj3/Jeronimo/releases/latest` responde sin usuario).

## Estado y pendientes

Versión publicada: **0.3.1** (12/10/2026). La 0.3.0 fue la primera portable, con actualización
automática, interfaz en inglés y portugués y la página final «Traducido con Jerónimo» al exportar;
la 0.3.1 suma la corrección de los avisos de calidad con un clic (`qa_fix.py`): lo mecánico (signos
¿ ¡, espacios, guion → raya) al instante y con deshacer, los nombres con dos grafías se unifican
eligiendo cuál queda, y lo demás lo propone la IA para aceptar o descartar (en modo manual, se
copia al chat). La actualización de la 0.3.0 a la 0.3.1 se probó de verdad (la 0.3.0 descargada de
GitHub encontró la 0.3.1, se actualizó y se volvió a abrir sola). El README es la presentación
para compartir (botón de descarga directa `docs/boton-descargar.svg`, capturas de `docs/capturas/`
hechas con `tools/capturas_readme.py`, preguntas frecuentes); el usuario eligió solo el README, sin
página web aparte.

Pendiente, en este orden (lo fijó el usuario el 19/10/2026; una tarjeta de Trello por cada parte):
1. **Versión 0.3.2**: (a) ✅ renombrar proyectos ya creados (cambia el nombre que se ve, no la
   carpeta) y quitarlos de recientes (no borra nada del disco, con «Deshacer»); (b) sugerencias y reporte de
   errores con un **formulario de Google sin cuenta** (las respuestas le llegan al usuario en una
   planilla privada; no GitHub): la app abre el formulario ya completado con el tipo, la
   descripción y, si es un error, datos técnicos opcionales (versión, Windows, último error) sin el
   nombre de usuario en las rutas y con vista previa; nunca texto de libros. El usuario crea el
   formulario y pasa el enlace; (c) «Apoyar el proyecto», discreto, con **GitHub Sponsors,
   Cafecito y PayPal** en la app, el README y `.github/FUNDING.yml`; nunca ventanas que
   interrumpan ni donaciones dentro de los libros exportados. Hecho: enlace al pie del inicio y una
   línea después de exportar (`support.py`), sección en el README y `.github/FUNDING.yml`, con
   Cafecito (https://cafecito.app/willyssj3) y PayPal (https://paypal.me/Willyssj3). Falta GitHub
   Sponsors, pendiente de aprobación (20/10/2026): al aprobarse, cargar
   https://github.com/sponsors/Willyssj3 en `support.LINKS`, `github: [Willyssj3]` en
   `FUNDING.yml` y el enlace en el README. Al publicarla, rehacer las capturas del README (`tools/capturas_readme.py`):
   cambian el resumen («Cambiar nombre») y el inicio (la «×» de recientes).
2. **Versión 0.4.0**: (a) al crear el proyecto, dar ya las instrucciones para armarlo en el chat de
   IA que use, con la guía de estilo y el glosario; (b) revisión final del libro completo, que
   busque errores e incongruencias usando las notas que quedan en cada capítulo; (c) lector de
   audiolibros: voces neuronales gratis que funcionan en la PC (se descargan una vez, pidiendo
   permiso; el texto no sale de la PC), voces distintas por personaje (con IA; sin IA, al menos
   narrador y diálogos) y exportar a MP3.
3. **Versión 0.5.0**: (a) **IA gratis en la PC**: un modelo abierto ya entrenado (no se entrena uno
   desde cero), que se descarga con un clic y permiso, corre dentro de la app sin instalar nada
   aparte y se elige según la memoria y la placa de video (la PC del usuario tiene 4 GB de video);
   sirve para traducir, revisar y corregir, con la guía, el glosario y los ejemplos de siempre;
   (b) **diccionario del perfil** (palabras que no se marcan, traducciones aprendidas de las
   correcciones, viaja con el perfil de autor) y **filtro de falsos positivos** del control de
   calidad con IA, aprendiendo también de lo que el usuario ignora.
4. **Versión 0.6.0**: digitalizar libros en papel con fotos o escaneos: enderezar y recortar
   páginas, separar texto e ilustraciones, unir palabras cortadas, capítulos y notas, OCR de
   Windows o IA que lee imágenes. Segunda parte: fotos desde el celular con un código QR.
5. **Versiones 0.7.0 y 0.8.0**: mangas y cómics. Parte 1: abrir CBZ/CBR/PDF/EPUB, detectar globos,
   leer y traducir, escribir la traducción en globos lisos con fuentes libres de estilo cómic, y
   exportar CBZ y EPUB de página fija (reusa lo de la 0.6.0). Parte 2: texto sobre el dibujo y
   onomatopeyas, manga japonés (vertical, de derecha a izquierda) y ajustes por e-reader, Kindle
   incluido. Elegir piezas con licencias compatibles con MIT (varias herramientas conocidas de
   este tema son GPL).
6. **Programa firmado** (sin el aviso de Windows SmartScreen): en pausa hasta que el usuario lo
   pida. Plan: el usuario solicita el certificado gratuito para proyectos de código abierto de
   SignPath Foundation (verificar los requisitos actuales en signpath.org); después se agrega un
   paso de firma al flujo de GitHub con los secretos que den. Explicarle los pasos de forma simple.
7. **Proponer mejoras** con opciones para elegir. Ideas ya anotadas: los títulos que agrega el
   conversor de PDF a EPUB («Notas», «Índice», «Portada») salen en español aunque el libro esté en
   otro idioma; las reglas de rayas de diálogo y signos de apertura del control de calidad son
   solo para español, pero el libro se puede traducir a otros idiomas (`LANG_NAMES` en `prompts.py`).
