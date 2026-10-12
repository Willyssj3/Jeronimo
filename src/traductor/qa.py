"""Control de calidad de la traducción, sin IA.

Revisa los párrafos traducidos y señala lo que suele fallar: términos del
glosario no respetados, párrafos que quedaron igual que el original o mucho
más cortos (posible omisión), palabras que quedaron en el idioma original,
números distintos, signos de apertura (¿ ¡) que faltan, guiones en lugar de
rayas de diálogo, espacios mal puestos y nombres escritos de varias formas.

Son avisos para revisar, no errores seguros: cada uno se puede ignorar."""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from .inline import strip_marks

# clave → (nombre para mostrar, nivel)
KINDS: dict[str, tuple[str, str]] = {
    "glosario": ("Glosario no respetado", "error"),
    "igual": ("Igual que el original", "error"),
    "restos": ("Palabras sin traducir", "error"),
    "signos": ("Signos de apertura", "error"),
    "numeros": ("Números distintos", "aviso"),
    "longitud": ("Posible omisión o añadido", "aviso"),
    "dialogo": ("Rayas de diálogo", "aviso"),
    "espacios": ("Espacios y puntuación", "aviso"),
    "nombres": ("Nombres con varias grafías", "aviso"),
    "formato": ("Formato", "aviso"),
}

# Palabras muy frecuentes del inglés que no existen en las lenguas de destino
# habituales: si aparecen en la traducción, probablemente quedaron sin traducir.
ENGLISH_LEFTOVERS = frozenset("""
the and with that this these those was were would could should have has had from which their
there they them what when where your you she her his him about into been being said just like
because though through before after then than only very much some any other will shall might
must don't didn't can't isn't wasn't it's i'm i've i'll you're we're they're won't couldn't wouldn't
""".split())
# Las que sí son palabras en algún idioma de destino.
LEFTOVER_EXCEPTIONS = {"de": {"was", "will", "had"}, "nl": {"was", "had", "will"}}

DEFAULT_RATIO = {("en", "es"): 1.15, ("en", "pt"): 1.12, ("en", "fr"): 1.15, ("en", "it"): 1.1}
WORD_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
NUMBER_RE = re.compile(r"\d+(?:[.,\u00a0\u202f]\d+)*")
SENTENCE_START = set(".!?¿¡—–-«“\"'‘…:;([")


@dataclass
class Issue:
    doc: int
    n: int
    tipo: str
    mensaje: str
    clave: str = ""  # para ignorar avisos de todo el libro (nombres)

    @property
    def nivel(self) -> str:
        return KINDS[self.tipo][1]


def norm(text: str) -> str:
    """Minúsculas, sin tildes y con apóstrofos y guiones uniformes."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower().replace("’", "'").replace("‐", "-").replace("‑", "-")


def _name_key(word: str) -> str:
    key = re.sub(r"['\-]", "", norm(word))
    return re.sub(r"(.)\1+", r"\1", key)


def _numbers(text: str) -> Counter:
    return Counter(re.sub(r"\D", "", m) for m in NUMBER_RE.findall(text))


class _GlossaryEntry:
    def __init__(self, entry: dict):
        self.term = entry["termino"].strip()
        self.translation = entry["traduccion"].strip()
        flags = 0 if any(c.isupper() for c in self.term) else re.IGNORECASE
        self.source_re = re.compile(r"(?<!\w)" + re.escape(self.term) + r"(?!\w)", flags)
        self.alternatives = [a.strip() for a in re.split(r"\s*[/;|]\s*", self.translation) if a.strip()]

    def respected(self, target: str) -> bool:
        tnorm = norm(target)
        words = WORD_RE.findall(tnorm)
        for alt in self.alternatives:
            if alt[:1].isupper() or not alt[:1].isalpha():
                # Nombre propio: tal cual (sin contar tildes ni mayúsculas).
                if re.search(r"(?<!\w)" + re.escape(norm(alt)) + r"(?!\w)", tnorm):
                    return True
                continue
            # Nombre común: basta la raíz de cada palabra (admite plural y género).
            needed = [w for w in WORD_RE.findall(norm(alt)) if len(w) >= 3]
            stems = [w if len(w) <= 4 else w[:max(4, math.ceil(len(w) * 0.7))] for w in needed]
            if stems and all(any(t.startswith(s) for t in words) for s in stems):
                return True
        return False


class Checker:
    """Revisa un libro. Se crea con todos los capítulos para calcular lo que
    depende del libro entero (proporción de longitud, estilo de diálogos)."""

    def __init__(self, chapters: list[dict], glossary: list[dict], *, source_lang: str = "en",
                 target_lang: str = "es", ignored_names: list[str] | None = None):
        self.chapters = [c for c in chapters if not c.get("omitir")]
        self.source_lang = (source_lang or "en").split("-")[0].lower()
        self.target_lang = (target_lang or "es").split("-")[0].lower()
        self.spanish = self.target_lang == "es"
        self.glossary = [_GlossaryEntry(e) for e in glossary
                         if (e.get("termino") or "").strip() and (e.get("traduccion") or "").strip()]
        self.ignored_names = set(ignored_names or [])
        self.leftovers = (ENGLISH_LEFTOVERS - LEFTOVER_EXCEPTIONS.get(self.target_lang, set())
                          if self.source_lang == "en" and self.target_lang != "en" else frozenset())
        self.ratio = self._typical_ratio()
        self.dialogue_style = self._dialogue_style()

    # -- estadísticas del libro -------------------------------------------------

    def _translated(self):
        for ch in self.chapters:
            for seg in ch.get("segmentos", []):
                if seg.get("tgt"):
                    yield ch, seg, strip_marks(seg["src"]), strip_marks(seg["tgt"])

    def _typical_ratio(self) -> float:
        ratios = [len(t) / len(s) for _c, _s, s, t in self._translated() if len(s) >= 40 and t.strip()]
        if len(ratios) >= 20:
            return statistics.median(ratios)
        return DEFAULT_RATIO.get((self.source_lang, self.target_lang), 1.1)

    def _dialogue_style(self) -> str | None:
        """«raya» o «comillas», según lo que use la mayoría de los diálogos."""
        dash = quotes = 0
        for _c, _s, src, tgt in self._translated():
            if src.lstrip()[:1] in "“\"‘'«—":
                first = tgt.lstrip()[:1]
                dash += first == "—"
                quotes += first in "“\"«‘'"
        if dash >= 3 and dash >= 3 * quotes:
            return "raya"
        if quotes >= 3 and quotes >= 3 * dash:
            return "comillas"
        return None

    # -- revisión -------------------------------------------------------------------

    def segment_issues(self, doc: int, seg: dict) -> list[Issue]:
        if not seg.get("tgt"):
            return []
        n = seg["n"]
        src, tgt = strip_marks(seg["src"]), strip_marks(seg["tgt"])
        found: list[Issue] = []

        def add(kind: str, message: str) -> None:
            found.append(Issue(doc, n, kind, message))

        for entry in self.glossary:
            if entry.source_re.search(src) and not entry.respected(tgt):
                left = " (quedó sin traducir)" if entry.term != entry.translation and entry.source_re.search(tgt) else ""
                add("glosario", f"«{entry.term}» debería traducirse «{entry.translation}»{left}.")

        src_words = WORD_RE.findall(src)
        if len(src_words) >= 4 and norm(src).strip() == norm(tgt).strip():
            add("igual", "La traducción es igual que el original.")

        if self.leftovers:
            left = sorted({w for w in WORD_RE.findall(tgt) if w in self.leftovers})
            if left:
                add("restos", "Posibles palabras en inglés: " + ", ".join(f"«{w}»" for w in left[:6]) + ".")

        if self.spanish:
            for close, opening in (("?", "¿"), ("!", "¡")):
                if tgt.count(close) > tgt.count(opening):
                    add("signos", f"Falta el signo de apertura «{opening}».")

        src_nums, tgt_nums = _numbers(src), _numbers(tgt)
        extra = [k for k in tgt_nums if k not in src_nums]
        if tgt_nums and (extra or any(k not in tgt_nums for k in src_nums)):
            shown = extra or [k for k in src_nums if k not in tgt_nums]
            add("numeros", "Los números no coinciden con el original" + (f" ({', '.join(shown[:4])})" if shown else "") + ".")

        if len(src) >= 80 and tgt.strip():
            ratio = len(tgt) / len(src)
            if ratio < 0.55 * self.ratio:
                add("longitud", "Mucho más corto que el original: puede faltar texto.")
            elif ratio > 1.8 * self.ratio:
                add("longitud", "Mucho más largo que el original: revisa si hay añadidos.")

        if self.spanish:
            stripped = tgt.lstrip()
            if re.match(r"[-–]\s*[^\W\d_¿¡]|[-–]\s*[¿¡]", stripped):
                add("dialogo", "Diálogo con guion: en español se usa la raya (—).")
            elif re.search(r"[^\W\d_][.,!?…]?\s[-–]\s?[^\W\d_]", tgt) and src.lstrip()[:1] in "“\"‘'«—":
                add("dialogo", "Inciso del narrador con guion: usa la raya (—).")
        if self.dialogue_style and src.lstrip()[:1] in "“\"‘'«":
            first = tgt.lstrip()[:1]
            if self.dialogue_style == "raya" and first in "“\"«‘'":
                add("dialogo", "Este diálogo va entre comillas; el resto del libro usa raya.")
            elif self.dialogue_style == "comillas" and first == "—":
                add("dialogo", "Este diálogo va con raya; el resto del libro usa comillas.")

        spacing = []
        if re.search(r"\S {2,}\S", tgt):
            spacing.append("espacios dobles")
        if re.search(r"\s[,;:]|\s\.(?!\.)", tgt):
            spacing.append("espacio antes de un signo")
        if re.search(r"[¿¡«]\s", tgt) or re.search(r"\s[?!»]", tgt):
            spacing.append("espacio junto a ¿ ? ¡ ! « »")
        if spacing:
            add("espacios", "Revisa: " + ", ".join(spacing) + ".")

        for warning in seg.get("avisos") or []:
            add("formato", warning)

        ignored = set(seg.get("qa_ignorar") or [])
        return [i for i in found if i.tipo not in ignored]

    def name_issues(self) -> list[Issue]:
        """Nombres propios que aparecen escritos de varias formas en el libro."""
        forms: dict[str, Counter] = defaultdict(Counter)
        first_seen: dict[str, tuple[int, int]] = {}
        for ch, seg, _src, tgt in self._translated():
            for m in WORD_RE.finditer(tgt):
                word = m.group(0)
                if len(word) < 4 or not word[0].isupper() or word.isupper():
                    continue
                before = tgt[:m.start()].rstrip()
                if not before or before[-1] in SENTENCE_START:
                    continue  # inicio de frase: la mayúscula no indica un nombre
                key = _name_key(word)
                forms[key][word] += 1
                first_seen.setdefault(word, (ch["doc"], seg["n"]))
        found = []
        for key, counter in forms.items():
            if len(counter) < 2 or key in self.ignored_names:
                continue
            ranked = counter.most_common()
            listing = " y ".join(f"«{w}» ({c})" for w, c in ranked[:4])
            for word, _count in ranked[1:]:
                doc, n = first_seen[word]
                found.append(Issue(doc, n, "nombres", f"Mismo nombre escrito de varias formas: {listing}.", key))
        return found

    def run(self, only: int | None = None) -> list[Issue]:
        issues: list[Issue] = []
        for ch in self.chapters:
            if only is not None and ch["doc"] != only:
                continue
            for seg in ch.get("segmentos", []):
                issues.extend(self.segment_issues(ch["doc"], seg))
        issues.extend(i for i in self.name_issues() if only is None or i.doc == only)
        order = list(KINDS)
        return sorted(issues, key=lambda i: (i.doc, i.n, order.index(i.tipo)))


def check_project(project, only: int | None = None) -> dict:
    """Control de calidad de un proyecto (o de un solo capítulo)."""
    chapters = [project.load_chapter(i) for i in project.chapter_indices()]
    checker = Checker(chapters, project.glossary(), source_lang=project.source_lang,
                      target_lang=project.target_lang,
                      ignored_names=project.config.get("qa_nombres_ignorados", []))
    issues = checker.run(only)
    titles = {c["doc"]: c.get("titulo") or f"Documento {c['doc']}" for c in chapters}
    texts = {(c["doc"], s["n"]): strip_marks(s.get("tgt") or "") for c in chapters for s in c["segmentos"]}
    checked = sum(1 for c in checker.chapters if only is None or c["doc"] == only
                  for s in c["segmentos"] if s.get("tgt"))
    rows = []
    for issue in issues:
        row = asdict(issue)
        row["nivel"] = issue.nivel
        row["titulo"] = titles.get(issue.doc, "")
        text = texts.get((issue.doc, issue.n), "")
        row["fragmento"] = text if len(text) <= 160 else text[:157].rsplit(" ", 1)[0] + "…"
        rows.append(row)
    return {"problemas": rows, "resumen": dict(Counter(i.tipo for i in issues)), "parrafos": checked,
            "tipos": {k: {"nombre": v[0], "nivel": v[1]} for k, v in KINDS.items()}}


def ignore(project, doc: int, n: int, kind: str, key: str = "") -> None:
    """No volver a avisar de este tipo de problema en ese párrafo (o de ese nombre)."""
    if kind not in KINDS:
        raise ValueError(f"Tipo de aviso desconocido: {kind}")
    if kind == "nombres" and key:
        names = project.config.setdefault("qa_nombres_ignorados", [])
        if key not in names:
            names.append(key)
            project.save_config()
        return
    data = project.load_chapter(doc)
    seg = data["segmentos"][n]
    ignored = seg.setdefault("qa_ignorar", [])
    if kind not in ignored:
        ignored.append(kind)
    project.save_chapter(data)
