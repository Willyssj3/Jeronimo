"""Búsqueda de ejemplos en la memoria de traducción (BM25).

Para cada bloque a traducir se buscan pasajes parecidos en las traducciones
oficiales del autor (y en los capítulos ya aprobados del libro actual) y se
dan a la IA como ejemplos del estilo a imitar."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

_WORD_RE = re.compile(r"[a-záéíóúüñàèìòùâêîôûäëïöç']+", re.IGNORECASE)
_STOP = set("""
the a an and but or if then he she it they we you i me my his her its their our your this that
these those there here what when where who whom whose why how which while yes no not was were
will would could should can may might must shall do does did have has had be been being is are
am of on to up with without at by for from in into as so than too very just also only all any
some each every both few more most other such own same again further once out off over under
about above below between through during before after them him us said says say one two
""".split())


def tokenize(text: str) -> list[str]:
    return [w for w in (t.lower().strip("'") for t in _WORD_RE.findall(text)) if len(w) > 2 and w not in _STOP]


@dataclass
class Example:
    src: str
    tgt: str
    origin: str  # nombre del libro de referencia o "este libro"


class ExampleIndex:
    """Índice BM25 sobre el lado original de los pares."""

    def __init__(self, examples: list[Example], *, k1: float = 1.4, b: float = 0.75):
        self.examples = examples
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []
        for i, ex in enumerate(examples):
            toks = tokenize(ex.src)
            self.lengths.append(len(toks))
            for term, tf in Counter(toks).items():
                self.postings[term].append((i, tf))
        n = max(1, len(examples))
        self.avg_len = (sum(self.lengths) / n) if self.lengths else 1.0
        self.idf = {
            t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()
        }

    def __len__(self) -> int:
        return len(self.examples)

    def scores(self, query: str) -> dict[int, float]:
        acc: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, tf in self.postings[term]:
                norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg_len))
                acc[i] += idf * norm
        return acc

    def search(self, query: str, *, k: int = 6, max_chars: int = 5000,
               must_include: list[str] | None = None,
               min_len: int = 40, max_len: int = 1500) -> list[Example]:
        """Mejores ejemplos para ``query``, variados y dentro de un presupuesto
        de caracteres. ``must_include`` son términos del glosario presentes en
        el bloque: se intenta incluir un ejemplo de cómo se tradujo cada uno."""
        if not self.examples:
            return []
        scored = sorted(self.scores(query).items(), key=lambda kv: -kv[1])
        chosen: list[int] = []
        chosen_tokens: list[set[str]] = []
        used = 0

        def fits(i: int) -> bool:
            ex = self.examples[i]
            return min_len <= len(ex.src) <= max_len and used + len(ex.src) + len(ex.tgt) <= max_chars

        def take(i: int) -> None:
            nonlocal used
            ex = self.examples[i]
            chosen.append(i)
            chosen_tokens.append(set(tokenize(ex.src)))
            used += len(ex.src) + len(ex.tgt)

        for term in (must_include or [])[:3]:
            if len(chosen) >= k:
                break
            low = term.lower()
            first = tokenize(term)
            if not first:
                continue
            candidates = sorted(
                (i for i, _tf in self.postings.get(first[0], []) if low in self.examples[i].src.lower()),
                key=lambda i: abs(len(self.examples[i].src) - 300),
            )
            for i in candidates:
                if i not in chosen and fits(i):
                    take(i)
                    break

        for i, _score in scored:
            if len(chosen) >= k:
                break
            if i in chosen or not fits(i):
                continue
            toks = set(tokenize(self.examples[i].src))
            # Evita ejemplos casi iguales entre sí.
            if any(len(toks & other) > 0.6 * max(1, min(len(toks), len(other))) for other in chosen_tokens):
                continue
            take(i)
        return [self.examples[i] for i in chosen]
