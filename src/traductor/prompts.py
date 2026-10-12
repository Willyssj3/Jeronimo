"""Instrucciones que se envían a la IA y lectura de sus respuestas.

Las instrucciones van en inglés (los modelos las siguen con más precisión);
las notas para el usuario se piden en español."""

from __future__ import annotations

import json
import re

LANG_NAMES = {
    "en": ("English", "inglés"),
    "es": ("Spanish", "español"),
    "pt": ("Portuguese", "portugués"),
    "fr": ("French", "francés"),
    "it": ("Italian", "italiano"),
    "de": ("German", "alemán"),
    "ca": ("Catalan", "catalán"),
    "gl": ("Galician", "gallego"),
    "eu": ("Basque", "euskera"),
    "nl": ("Dutch", "neerlandés"),
    "ja": ("Japanese", "japonés"),
    "zh": ("Chinese", "chino"),
    "ru": ("Russian", "ruso"),
}

VARIANTS = {
    "auto": "",
    "es-419": "Use neutral Latin American Spanish (ustedes for plural address; avoid strong regionalisms).",
    "es-AR": "Use Rioplatense Spanish (Argentina/Uruguay), with voseo (vos tenés, vos podés) where the second person singular is informal.",
    "es-MX": "Use Mexican Spanish.",
    "es-ES": "Use Peninsular Spanish (Spain), with vosotros for informal plural address.",
    "pt-BR": "Use Brazilian Portuguese.",
    "pt-PT": "Use European Portuguese.",
    "fr-FR": "Use French from France.",
    "fr-CA": "Use Canadian French.",
}


def lang_name(code: str, *, spanish: bool = False) -> str:
    base = (code or "").split("-")[0].lower()
    names = LANG_NAMES.get(base)
    if not names:
        return code
    return names[1] if spanish else names[0]


def variant_line(variant: str, target: str) -> str:
    if variant and variant != "auto":
        return VARIANTS.get(variant, f"Use the {variant} variety of {lang_name(target)}.")
    return (f"Use the same variety of {lang_name(target)} as the author's official translations "
            "(country/region, forms of address), as described in the style guide.")


# ---------------------------------------------------------------------------
# Traducción

TRANSLATE_SYSTEM = """\
You are an expert literary translator. You are translating the book "{title}"{author_part} from {src} into {tgt}.
{variant}
Your translation must read as if it had been written by the official translator of this author's other books into {tgt}: imitate the voice, register, rhythm, vocabulary and conventions described in the style guide and shown in the reference examples. Fidelity comes first: do not omit, summarize, soften, censor or add content; keep the author's meaning, tone, humor and ambiguity.

Format rules:
- The text comes as segments: <seg id="N">…</seg>. Answer with exactly one <seg id="N">…</seg> per input segment, with the same ids and in the same order, and nothing else between them.
- <gN>…</gN> wrap formatted words (italics, bold, links). Keep each pair exactly once around the corresponding words of your translation; move it within the segment if the word order changes.
- <xN/> are fixed elements (images, footnote marks, line breaks). Keep each one exactly once, in the corresponding position.
- &amp; &lt; &gt; are escaped characters: keep them escaped.
- Never merge or split segments, even when a sentence continues in the next segment.
- Apply the glossary exactly. Names that are not in the glossary keep their original form unless the style guide says otherwise.
- Follow the punctuation and typography of the style guide (for example, dialogue dashes and quotation marks).
- If something is genuinely doubtful (wordplay, ambiguous reference, cultural allusion, possible error in the original), translate it as well as you can and explain the doubt briefly, in Spanish, in a final block: <notes><note id="N">…</note></notes>. Omit the block when there are no doubts.

# Style guide
{style_guide}

# About the book
{synopsis}
"""

NO_STYLE_GUIDE = "(Not available: write natural, literary {tgt}, faithful to the original.)"
NO_SYNOPSIS = "(No summary available.)"


def translate_system(*, title: str, author: str, src: str, tgt: str, variant: str,
                     style_guide: str, synopsis: str) -> str:
    return TRANSLATE_SYSTEM.format(
        title=title,
        author_part=f" by {author}" if author else "",
        src=lang_name(src),
        tgt=lang_name(tgt),
        variant=variant_line(variant, tgt),
        style_guide=style_guide.strip() or NO_STYLE_GUIDE.format(tgt=lang_name(tgt)),
        synopsis=synopsis.strip() or NO_SYNOPSIS,
    )


def translate_user(*, segments: list[tuple[int, str]], glossary: list[dict],
                   examples: list, chapter_summary: str, previous: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    if glossary:
        lines = []
        for g in glossary:
            note = f" ({g['nota']})" if g.get("nota") else ""
            lines.append(f"- {g['termino']} → {g['traduccion']}{note}")
        parts.append("# Glossary (mandatory renderings)\n" + "\n".join(lines))
    if examples:
        ex_lines = []
        for ex in examples:
            attr = ' kind="this-book"' if ex.origin == "este libro" else ""
            ex_lines.append(f"<example{attr}>\n<source>{ex.src}</source>\n"
                            f"<translation>{ex.tgt}</translation>\n</example>")
        parts.append("# Reference examples\nPassages from the official translations of this author "
                     "(and passages already approved in this book). Imitate their choices.\n" + "\n".join(ex_lines))
    if chapter_summary:
        parts.append("# Chapter context\n" + chapter_summary.strip())
    if previous:
        prev = "\n".join(f"<source>{s}</source>\n<translation>{t}</translation>" for s, t in previous)
        parts.append("# Previous text (already translated; for continuity only, do not translate it again)\n" + prev)
    segs = "\n".join(f'<seg id="{n}">{text}</seg>' for n, text in segments)
    parts.append("# Segments to translate\n" + segs)
    return "\n\n".join(parts)


# En el modo manual el texto se pega en cualquier chat de IA (Claude, ChatGPT,
# Gemini, Copilot, DeepSeek, Le Chat, un modelo local…) y la respuesta se copia
# de vuelta: dentro de un bloque de código se conserva tal cual y los chats
# ofrecen un botón para copiarla entera.
MANUAL_OUTPUT = """

# Output format
Write your whole answer inside ONE single code block (```), containing only the <seg> elements and, if needed, the <notes> block. Do not write anything before or after the code block.

# Before answering, check that
- every input segment appears exactly once in your answer, with the same id, completely translated (no summaries, no omissions, nothing left in the original language);
- every <gN>…</gN> and <xN/> tag is kept;
- you followed the glossary, the style guide and the dialogue punctuation.
If the whole answer does not fit in one message, stop right after a complete </seg>: the missing segments will be sent again."""

MANUAL_ANSWER = """

# Output format
Write your whole answer inside ONE single code block (```) so it can be copied exactly. Do not write anything before or after the code block."""


# Paquete para Proyectos (claude.ai, proyectos de ChatGPT, Gems de Gemini…): las
# instrucciones y el conocimiento fijo se suben una vez y cada bloque va liviano.
PROJECT_NOTE = """
# How this project works
Each message in this project contains one block of segments to translate, together with the glossary entries that appear in it, the chapter context and the previous text. Translate every block following all the rules above. The project files contain the complete glossary, the chapter summaries and more passages from the official translations: imitate their choices."""

LIGHT_BLOCK_HEADER = """\
Translate the segments below into {tgt}, following the instructions, the style guide, the glossary and the reference passages of this project.
Reminder: answer with exactly one <seg id="N">…</seg> per input segment, keep every <gN>…</gN> and <xN/> tag, keep &amp; &lt; &gt; escaped and apply the glossary exactly."""


def strip_code_fences(text: str) -> str:
    """Contenido del bloque de código más largo de una respuesta de chat (o el texto tal cual)."""
    blocks = re.findall(r"```[^\n`]*\n(.*?)```", text or "", re.DOTALL)
    if blocks:
        return max(blocks, key=len).strip()
    return (text or "").strip()


_SEG_RE = re.compile(r'<seg\s+id\s*=\s*["\']?(\d+)["\']?\s*>(.*?)</seg\s*>', re.DOTALL | re.IGNORECASE)
_NOTE_RE = re.compile(r'<note\s+id\s*=\s*["\']?(\d+)["\']?\s*>(.*?)</note\s*>', re.DOTALL | re.IGNORECASE)
_NL_RE = re.compile(r"\s*\n\s*")


def parse_segments(text: str) -> tuple[dict[int, str], dict[int, str]]:
    """Extrae las traducciones ``{id: texto}`` y las notas ``{id: nota}``."""
    segs: dict[int, str] = {}
    for m in _SEG_RE.finditer(text or ""):
        n = int(m.group(1))
        if n not in segs:
            segs[n] = _NL_RE.sub(" ", m.group(2)).strip()
    notes = {int(m.group(1)): _NL_RE.sub(" ", m.group(2)).strip() for m in _NOTE_RE.finditer(text or "")}
    return segs, notes


# ---------------------------------------------------------------------------
# Segunda pasada: revisión de la traducción por una IA

REVIEW_SYSTEM = """\
You are a meticulous literary editor. You are reviewing the translation of the book "{title}"{author_part} from {src} into {tgt}.
{variant}
Compare each translated segment with its original and propose a correction only when there is a real problem:
- meaning changed, mistranslations, omitted or added content;
- the glossary is not followed, or names and terms are inconsistent;
- wrong register or forms of address according to the style guide (for example tú/usted);
- clumsy or unnatural phrasing in {tgt}, or calques from {src};
- spelling, grammar, punctuation and typography (dialogue dashes, quotation marks, opening ¿ and ¡ in Spanish).
Do not rewrite for personal taste, do not simplify or modernize the author's style, and keep the voice of the official translator described in the style guide. If a segment is fine, leave it alone.

Format rules:
- The input comes as <seg id="N"><source>…</source><translation>…</translation></seg>.
- For each segment that needs changes, answer <fix id="N"><text>the complete corrected translation of that segment</text><why>a very short reason, in Spanish</why></fix>.
- In <text>, keep every <gN>…</gN> and <xN/> tag of the translation exactly once (move <gN> pairs with their words if needed) and keep &amp; &lt; &gt; escaped.
- Write nothing for segments that are fine. If no segment needs changes, answer only <nofixes/>.

# Style guide
{style_guide}

# About the book
{synopsis}
"""

REVIEW_MANUAL_OUTPUT = """

# Output format
Write your whole answer inside ONE single code block (```), containing only the <fix> elements (or <nofixes/>). Do not write anything before or after the code block. If the answer does not fit in one message, stop right after a complete </fix>."""


def review_system(*, title: str, author: str, src: str, tgt: str, variant: str,
                  style_guide: str, synopsis: str) -> str:
    return REVIEW_SYSTEM.format(
        title=title,
        author_part=f" by {author}" if author else "",
        src=lang_name(src),
        tgt=lang_name(tgt),
        variant=variant_line(variant, tgt),
        style_guide=style_guide.strip() or NO_STYLE_GUIDE.format(tgt=lang_name(tgt)),
        synopsis=synopsis.strip() or NO_SYNOPSIS,
    )


def review_user(*, segments: list[tuple[int, str, str]], glossary: list[dict], chapter_summary: str) -> str:
    parts: list[str] = []
    if glossary:
        lines = [f"- {g['termino']} → {g['traduccion']}" + (f" ({g['nota']})" if g.get("nota") else "") for g in glossary]
        parts.append("# Glossary (mandatory renderings)\n" + "\n".join(lines))
    if chapter_summary:
        parts.append("# Chapter context\n" + chapter_summary.strip())
    segs = "\n".join(f'<seg id="{n}"><source>{src}</source><translation>{tgt}</translation></seg>'
                     for n, src, tgt in segments)
    parts.append("# Segments to review\n" + segs)
    return "\n\n".join(parts)


_FIX_RE = re.compile(r'<fix\s+id\s*=\s*["\']?(\d+)["\']?\s*>(.*?)</fix\s*>', re.DOTALL | re.IGNORECASE)
_TEXT_RE = re.compile(r"<text>(.*?)</text\s*>", re.DOTALL | re.IGNORECASE)
_WHY_RE = re.compile(r"<why>(.*?)</why\s*>", re.DOTALL | re.IGNORECASE)


def parse_fixes(text: str) -> dict[int, tuple[str, str]]:
    """Correcciones propuestas ``{id: (texto, motivo)}``."""
    fixes: dict[int, tuple[str, str]] = {}
    for m in _FIX_RE.finditer(text or ""):
        body = m.group(2)
        t = _TEXT_RE.search(body)
        if not t:
            continue
        why = _WHY_RE.search(body)
        fixes.setdefault(int(m.group(1)), (_NL_RE.sub(" ", t.group(1)).strip(),
                                           _NL_RE.sub(" ", why.group(1)).strip() if why else ""))
    return fixes


def looks_like_review(text: str) -> bool:
    return bool(_FIX_RE.search(text or "")) or "<nofixes" in (text or "").lower()


# ---------------------------------------------------------------------------
# Conocimiento: guía de estilo, glosario y análisis del libro

ANALYST_SYSTEM = "You are an expert in literary translation and translation analysis. Follow the requested output format exactly."

STYLE_GUIDE_USER = """\
Below are aligned passages from books by {author}: the original {src} text and the official, published {tgt} translation.
Study the translator's choices and write a practical style guide, in Spanish, that would allow another translator to produce translations indistinguishable from this translator's work. Cover, when the passages show it:
1. Variety of {tgt} (country/region) and forms of address (tú/usted/vos; vosotros/ustedes), including how characters address each other.
2. Dialogue and typography: dialogue dashes or quotation marks, type of quotes, punctuation of dialogue tags, italics, numbers, measurements.
3. Proper names, nicknames, titles, places and invented terms: kept, adapted or translated? How are honorifics (Mr., Mrs., Sir, Lady…) handled?
4. Register and voice: formality, rhythm, sentence length, vocabulary, idioms, slang and swearing, humor.
5. Other notable recurring decisions.
Illustrate with brief examples quoting only a few words at a time; do not copy whole passages. Length: 300–700 words, in Markdown, starting directly with the guide.

{passages}
"""

GLOSSARY_USER = """\
Below are candidate terms (names, places, titles, invented words) from books by {author}, each with aligned passages from the original {src} text and the official {tgt} translation.
For each term, state how the official translation renders it. Reply with one JSON object per line and nothing else:
{{"term": "...", "translation": "...", "type": "persona|lugar|organización|objeto|título|otro", "note": "short note in Spanish"}}
If the term is kept unchanged, repeat it as the translation. Skip terms that are ordinary capitalized words or whose rendering the passages don't show.

{terms}
"""

ANALYZE_USER = """\
This is part {part} of {parts} of the book "{title}"{author_part}. Chapters are marked with [[CHAPTER id: title]].
Reply with JSON only, no other text, with this structure:
{{"summary": "summary of this part, in Spanish, 5-10 sentences",
 "chapters": [{{"id": "chapter id", "summary": "2-4 sentences in Spanish: what happens, who appears, who speaks to whom and in which tone"}}],
 "terms": [{{"term": "...", "type": "persona|lugar|organización|objeto|título|otro", "note": "short description in Spanish (e.g. gender, role, relationships)"}}]}}
In "terms", list the characters, places, organizations, titles, invented words and special expressions that a translator must keep consistent.

{text}
"""

SYNOPSIS_MERGE_USER = """\
These are summaries of consecutive parts of the book "{title}". Combine them into a single synopsis in Spanish of 200–350 words that a translator can use as context: main characters (with gender and relationships), setting, tone and plot.

{summaries}
"""

PROPOSE_USER = """\
These terms appear in the book "{title}", which is being translated from {src} into {tgt}. Following the style guide and the official glossary below, decide how to render each term. Keep proper names unchanged unless the style guide or the official glossary shows that this translator adapts them (for example meaningful names, titles, invented words).
Reply with one JSON object per line and nothing else:
{{"term": "...", "translation": "...", "note": "short note in Spanish"}}

# Style guide
{style_guide}

# Official glossary (already decided; do not repeat these)
{official}

# Terms to decide
{terms}
"""


def passages_block(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"<passage>\n<original>{s}</original>\n<translation>{t}</translation>\n</passage>"
                     for s, t in pairs)


_OBJ_RE = re.compile(r"\{[^{}]*\}")


def parse_json_lines(text: str) -> list[dict]:
    """Objetos JSON (uno por línea, o sueltos dentro del texto)."""
    out = []
    for m in _OBJ_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def parse_json_object(text: str) -> dict | None:
    """El objeto JSON principal de una respuesta (tolera texto alrededor)."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
