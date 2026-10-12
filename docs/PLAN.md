# Plan del proyecto

## Objetivo

App de escritorio (Windows primero), gratuita y de código abierto, para
traducir libros EPUB con IA manteniendo el estilo de las traducciones oficiales
del mismo autor. Pensada para personas que no programan.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Sistema | Windows (instalador `.exe`); el motor es multiplataforma |
| Licencia | Código abierto (MIT), gratis |
| IA | Cada usuario usa la suya: Claude (clave de API), Gemini, OpenAI, OpenRouter, DeepSeek, Ollama/LM Studio (locales y gratis) o modo manual |
| Estilo | Variante y estilo detectados de las traducciones oficiales |
| Revisión | Capítulo por capítulo (también se puede traducir todo de corrido) |
| Idiomas | Español primero; el motor admite cualquier idioma de destino |

## Arquitectura

```
EPUB original ──► epub.py ──► segmentos (párrafos con marcas de formato)
                                   │
Referencias (original + oficial) ──► align.py ──► pares ──► retrieval.py (ejemplos)
                                   │                  └──► knowledge.py (guía de estilo, glosario)
Libro completo ──► knowledge.py (sinopsis, resúmenes por capítulo, términos)
                                   │
                         translate.py (por bloques)
     instrucciones + guía + sinopsis (cacheado) │ glosario relevante + ejemplos +
                                                │ resumen del capítulo + párrafos previos
                                   ▼
                     providers/ (Claude, OpenAI-compatibles, Ollama)
                                   ▼
               revisión y aprobación por capítulo ──► epub.py ──► EPUB traducido / bilingüe
```

### Cómo se conserva el formato

Cada párrafo se convierte en texto con marcas numeradas: `<g1>…</g1>` para
formato que se traduce (cursiva, negrita, enlaces) y `<x2/>` para elementos
fijos (imágenes, llamadas a notas, saltos de línea). La respuesta se valida y,
si la IA rompió alguna marca, se repara automáticamente y el párrafo queda
marcado para revisión. El EPUB se reconstruye sobre el original, así que CSS,
imágenes, fuentes, índice y metadatos se mantienen.

### Cómo se imita el estilo oficial

1. **Emparejamiento** de cada libro de referencia con su traducción oficial,
   párrafo a párrafo, aunque el traductor haya unido o dividido párrafos o
   añadido prólogos.
2. **Guía de estilo** redactada por la IA a partir de una muestra variada de
   pares (diálogo y narración de todo el libro). Editable por el usuario.
3. **Glosario oficial**: nombres y términos frecuentes y cómo se tradujeron.
4. **Ejemplos por bloque**: los pasajes oficiales más parecidos a lo que se
   está traduciendo (y los capítulos ya aprobados de este libro).

### Coste y caché

Las instrucciones, la guía de estilo y la sinopsis son idénticas en todas las
peticiones de un libro, por lo que Claude (y otros proveedores con caché
automática) las cobran a precio reducido a partir de la segunda petición.
El consumo de tokens se registra por modelo en `proyecto.json` y la app muestra
el coste estimado.

## Hoja de ruta

1. ✅ **Motor**: EPUB, marcas de formato, emparejamiento, búsqueda de ejemplos,
   conocimiento, traducción por bloques, proveedores, exportación, línea de
   comandos y pruebas automáticas.
2. ✅ **App de escritorio** (pywebview + interfaz HTML en español, con modo
   navegador de respaldo):
   - Nuevo proyecto / abrir / recientes.
   - Referencias con calidad del emparejamiento.
   - Configuración de IA (lista de modelos, clave en el almacén seguro,
     prueba de conexión, coste estimado).
   - Estilo y glosario editables; generación con IA.
   - Capítulos: traducir siguiente / todo, omitir documentos.
   - Revisión lado a lado con formato visible, edición, retraducción de un
     párrafo, notas y avisos de la IA, aprobación, modo manual.
   - Exportación a EPUB traducido o bilingüe.
   - Diseño propio con la estética del logo (azul tinta, dorado, papel
     crema y EB Garamond), con modo oscuro.
3. ✅ **Instalador de Windows** generado con GitHub Actions (PyInstaller +
   Inno Setup) y publicado en *Releases* al crear una etiqueta `v*`. Desde la 0.3.0, en su
   lugar, una **versión portable** (`Jeronimo-Windows.zip`) que se actualiza sola.
4. **Mejoras pendientes**:
   - Probar el instalador en equipos reales. ✅ Nombre (Jerónimo) y logo propios.
   - Emparejamiento con *embeddings* multilingües (opcional, vía Ollama o API)
     para libros con estructura muy distinta.
   - ✅ Pasada de revisión por una segunda IA (consistencia y glosario).
   - ✅ Modo manual con cualquier chat de IA, también para preparar la guía
     de estilo, el glosario y el análisis del libro.
   - Traducción del texto alternativo de imágenes.
   - ✅ Conversión de PDF con texto a EPUB (`pdf2epub.py`).
   - ✅ OCR para PDF escaneados; más formatos de entrada (DOCX, TXT, FB2, MOBI/AZW3) y salida
     (DOCX, PDF).
   - ✅ Interfaz en inglés y portugués.
   - ✅ Corregir los avisos del control de calidad con un clic: lo mecánico al instante y con
     deshacer, lo demás con una propuesta de la IA (`qa_fix.py`, versión 0.3.1).
   - Para la 0.4.0 (elegido por el usuario): instrucciones para preparar el proyecto en la IA
     apenas se crea; revisión final del libro completo con las notas de cada capítulo; lector de
     audiolibros con voces locales gratuitas, una voz por personaje y exportación a MP3.
