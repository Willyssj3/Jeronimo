# Traductor de Libros

Aplicación gratuita y de código abierto para **traducir libros EPUB con IA
imitando el estilo de las traducciones oficiales del autor**.

La diferencia con otros traductores de EPUB: le das a la app otros libros del
mismo autor junto con su **traducción oficial** (por ejemplo, el original en
inglés y la edición publicada en español). La app los empareja párrafo a
párrafo y aprende de ellos:

- **Guía de estilo** del traductor oficial: variante del idioma, tuteo o usted,
  rayas de diálogo, cómo trata los nombres, el tono…
- **Glosario oficial**: cómo se tradujeron personajes, lugares y términos.
- **Ejemplos**: en cada fragmento, la IA ve pasajes parecidos ya traducidos
  oficialmente y los imita.

Además lee el libro entero antes de traducir (sinopsis, resumen de cada
capítulo, personajes) para mantener la coherencia, conserva el formato del
EPUB (cursivas, notas al pie, imágenes, índice) y permite revisar y aprobar
**capítulo por capítulo**.

> ⚠️ Usa la app solo con libros que tengas derecho a traducir (obras propias,
> de dominio público o con permiso del autor/editorial). Los EPUB nunca se
> suben a este repositorio.

## Estado

| Parte | Estado |
|---|---|
| Motor (lectura/escritura de EPUB, emparejamiento, glosario, traducción, exportación) | ✅ Listo, con pruebas automáticas |
| Línea de comandos | ✅ Lista |
| App de escritorio para Windows (interfaz gráfica + instalador) | 🚧 En desarrollo |

Ver el plan completo en [docs/PLAN.md](docs/PLAN.md).

## IA compatibles

Cada persona usa su propia IA:

| Proveedor | Coste | Notas |
|---|---|---|
| **Claude** (Anthropic) | Pago por uso | Mejor calidad literaria. Requiere una **clave de API** de [console.anthropic.com](https://console.anthropic.com); la suscripción de claude.ai (Pro/Max) no se puede usar en apps externas. |
| **Google Gemini** | Tiene nivel gratuito | Clave gratis en Google AI Studio, con límites de uso. |
| **OpenAI**, **OpenRouter**, **DeepSeek** | Pago por uso | Cualquier servicio compatible con la API de OpenAI. |
| **Ollama**, **LM Studio** | Gratis | Modelos que funcionan en tu propio PC (calidad menor; requiere buena tarjeta gráfica). |
| **Modo manual** | — | La app genera el texto a pegar en el chat de tu IA y luego pegas la respuesta. |

## Uso (línea de comandos, mientras se termina la app)

Requiere Python 3.11 o superior.

```bash
pip install -e .

# 1. Crear el proyecto con el libro a traducir
traductor nuevo mi-libro "Libro.epub" --destino es

# 2. Añadir libros del mismo autor con su traducción oficial (uno o varios)
traductor referencia mi-libro "Otro_EN.epub" "Otro_ES.epub"

# 3. Elegir la IA (la clave se toma de ANTHROPIC_API_KEY, GEMINI_API_KEY, etc.)
traductor proveedor mi-libro --tipo anthropic --modelo claude-opus-5-5

# 4. Preparar guía de estilo, glosario y análisis del libro (revisables en mi-libro/conocimiento/)
traductor preparar mi-libro

# 5. Traducir capítulo a capítulo, revisar y aprobar
traductor traducir mi-libro
traductor aprobar mi-libro --capitulo 3

# 6. Generar el EPUB traducido (o bilingüe)
traductor exportar mi-libro
traductor exportar mi-libro --bilingue
```

Otros comandos: `traductor estado`, `traductor modelos`, y el modo manual
`traductor prompt` / `traductor importar`.

## Desarrollo

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Estructura del código (`src/traductor/`):

| Módulo | Qué hace |
|---|---|
| `epub.py` | Lee el EPUB, extrae los párrafos traducibles y reconstruye el libro traducido o bilingüe. |
| `inline.py` | Convierte el formato interno de cada párrafo en marcas (`<g1>…</g1>`, `<x2/>`) que la IA respeta, y lo restaura. |
| `align.py` | Empareja un libro con su traducción oficial (Gale-Church + nombres propios como anclas). |
| `retrieval.py` | Busca los pasajes oficiales más parecidos a cada fragmento (BM25). |
| `knowledge.py` | Guía de estilo, glosario oficial, análisis del libro y propuesta de términos. |
| `translate.py` | Traducción por bloques con contexto, reintentos, reanudación y modo manual. |
| `providers/` | Adaptadores de Claude, OpenAI-compatibles y Ollama. |
| `project.py` | Carpeta de proyecto y su estado. |
| `cli.py` | Línea de comandos. |

## Licencia

[MIT](LICENSE).
