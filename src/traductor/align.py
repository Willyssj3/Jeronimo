"""Emparejamiento párrafo a párrafo de un libro original con su traducción
oficial (alineación de textos paralelos).

Método: programación dinámica tipo Gale-Church sobre todo el libro, limitada a
una franja alrededor de la diagonal, con dos señales:

* Longitud: un párrafo y su traducción tienen longitudes proporcionales.
* Anclas: nombres propios y números suelen mantenerse en la traducción.

Permite fusiones y divisiones (2-1, 1-2, 2-2, 3-1, 1-3) y saltar párrafos que
solo existen en un lado (prólogos del traductor, notas, publicidad…).
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import asdict, dataclass

# Movimientos (párrafos de origen, párrafos de destino) y su probabilidad a priori.
MOVES: list[tuple[int, int, float]] = [
    (1, 1, 0.89),
    (1, 0, 0.005),
    (0, 1, 0.005),
    (2, 1, 0.045),
    (1, 2, 0.045),
    (2, 2, 0.006),
    (3, 1, 0.002),
    (1, 3, 0.002),
]
MOVE_COST = [(di, dj, -math.log(p)) for di, dj, p in MOVES]

ANCHOR_WEIGHT = 2.5     # premio por compartir nombres/números
MISMATCH_WEIGHT = 1.0   # castigo si ambos tienen nombres pero ninguno coincide
DELTA_CAP = 8.0         # evita que un solo párrafo raro domine el coste

# Palabras que aparecen con mayúscula al empezar una frase (inglés y español)
# y que no sirven como anclas.
_STOP = set("""
the a an and but or if then he she it they we you i me my his her its their our your
this that these those there here what when where who whom whose why how which while
yes no not mr mrs ms dr st sir lady lord miss oh ah well now so as at by for from in
into of on to up with without after before again all also any because both each even
every few just more most never only other over same some still such than too very was
were will would could should can may might must shall do does did done have has had be
been being is are am let let's one two three four five six seven eight nine ten chapter
part book prologue epilogue
el la los las un una unos unas y o pero si entonces él ella ellos ellas nosotros vosotros
usted ustedes yo tú vos mi mis su sus tu tus nuestro nuestra este esta estos estas ese esa
esos esas aquel aquella qué cuándo dónde quién cómo cuál mientras sí no señor señora
señorita don doña ya ahora así como en por para de del al con sin tras antes después también
todo todos toda todas cada otro otra otros otras mismo misma muy más menos nunca solo sólo
era eran fue fueron será sería había habían es son está están estaba estaban hay capítulo
parte libro prólogo epílogo uno dos tres cuatro cinco seis siete ocho nueve diez
""".split())

_CAP_RE = re.compile(r"\b[A-ZÁÉÍÓÚÜÑ][\w’'\-]{2,}")
_NUM_RE = re.compile(r"\d+")


def anchors(text: str) -> frozenset[str]:
    """Nombres propios probables y números de un párrafo."""
    out = {w.lower().strip("’'-") for w in _CAP_RE.findall(text)}
    out = {w for w in out if w not in _STOP and len(w) >= 3}
    out.update(_NUM_RE.findall(text))
    return frozenset(out)


@dataclass
class Pair:
    src: str
    tgt: str
    cost: float
    kind: str  # "1-1", "2-1"…

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AlignStats:
    pairs: int
    src_paragraphs: int
    tgt_paragraphs: int
    one_to_one: float  # fracción de pares 1-1
    coverage: float    # fracción del texto original emparejado
    quality: str       # "buena" | "aceptable" | "baja"


def _letters(s: str) -> int:
    return sum(1 for ch in s if not ch.isspace())


def align(src: list[str], tgt: list[str], *, progress=None) -> tuple[list[Pair], AlignStats]:
    """Empareja dos listas de párrafos (original y traducción)."""
    src = [s for s in src if s.strip()]
    tgt = [t for t in tgt if t.strip()]
    n, m = len(src), len(tgt)
    if not n or not m:
        return [], AlignStats(0, n, m, 0.0, 0.0, "baja")

    ls = [max(1, _letters(s)) for s in src]
    lt = [max(1, _letters(t)) for t in tgt]
    c = sum(lt) / sum(ls)
    a_src = [anchors(s) for s in src]
    a_tgt = [anchors(t) for t in tgt]

    path = _dp(ls, lt, a_src, a_tgt, c, s2=12.0, progress=progress)
    # Segunda pasada con la varianza estimada a partir de la primera.
    s2 = _estimate_variance(path, ls, lt, c)
    if s2 is not None and not (8.0 <= s2 <= 18.0):
        path = _dp(ls, lt, a_src, a_tgt, c, s2=min(max(s2, 4.0), 60.0), progress=progress)

    pairs: list[Pair] = []
    covered = 0
    for i0, i1, j0, j1, cost in path:
        if i1 == i0 or j1 == j0:
            continue
        pairs.append(Pair(
            src=" ".join(src[i0:i1]), tgt=" ".join(tgt[j0:j1]),
            cost=round(cost, 3), kind=f"{i1 - i0}-{j1 - j0}",
        ))
        covered += sum(ls[i0:i1])
    one = sum(1 for p in pairs if p.kind == "1-1") / max(1, len(pairs))
    coverage = covered / sum(ls)
    good = [p for p in pairs if p.cost < 6.0]
    ratio_good = len(good) / max(1, len(pairs))
    quality = "buena" if coverage > 0.85 and ratio_good > 0.8 else "aceptable" if coverage > 0.6 else "baja"
    return pairs, AlignStats(len(pairs), n, m, round(one, 3), round(coverage, 3), quality)


def _estimate_variance(path, ls, lt, c) -> float | None:
    vals = []
    for i0, i1, j0, j1, _cost in path:
        if i1 - i0 == 1 and j1 - j0 == 1:
            l1, l2 = ls[i0], lt[j0]
            mean = (l1 + l2 / c) / 2
            vals.append((l2 - l1 * c) ** 2 / mean)
    if len(vals) < 20:
        return None
    vals.sort()
    # Media recortada: ignora el 10 % más extremo (errores de alineación).
    k = max(1, int(len(vals) * 0.9))
    return sum(vals[:k]) / k


def _dp(ls, lt, a_src, a_tgt, c, *, s2: float, progress=None):
    n, m = len(ls), len(lt)
    # Franja: alrededor del j "esperado" según la longitud acumulada.
    cs = [0]
    for x in ls:
        cs.append(cs[-1] + x)
    ct = [0]
    for x in lt:
        ct.append(ct[-1] + x)
    width = max(50, int(0.05 * max(n, m)) + abs(n - m))
    lo = [0] * (n + 1)
    hi = [0] * (n + 1)
    for i in range(n + 1):
        target = cs[i] / cs[n] * ct[m]
        j = bisect.bisect_left(ct, target)
        lo[i] = max(0, j - width)
        hi[i] = min(m, j + width)
    # Que la franja sea monótona y contenga los extremos.
    lo[0], hi[n] = 0, m
    for i in range(1, n + 1):
        lo[i] = max(lo[i], lo[i - 1]) if lo[i] >= lo[i - 1] else lo[i - 1]
    for i in range(n - 1, -1, -1):
        hi[i] = min(hi[i], hi[i + 1])
    for i in range(n + 1):
        if lo[i] > hi[i]:
            lo[i] = hi[i]

    inf = float("inf")
    cost_rows: list[list[float]] = []
    back_rows: list[bytearray] = []
    sqrt_s2 = math.sqrt(s2)

    def length_cost(l1: int, l2: int) -> float:
        mean = (l1 + l2 / c) / 2
        delta = abs(l2 - l1 * c) / (math.sqrt(mean) * sqrt_s2)
        if delta > DELTA_CAP:
            delta = DELTA_CAP
        # Aproximación de -log(2·(1-Φ(δ))) suficientemente fina para ordenar.
        return 0.5 * delta * delta + (math.log(delta + 1.0) if delta > 1 else 0.0)

    def anchor_cost(sa: frozenset, ta: frozenset) -> float:
        if not sa or not ta:
            return 0.0
        inter = len(sa & ta)
        if inter == 0:
            return MISMATCH_WEIGHT
        return -ANCHOR_WEIGHT * inter / math.sqrt(len(sa) * len(ta))

    for i in range(n + 1):
        row = [inf] * (hi[i] - lo[i] + 1)
        back = bytearray(len(row))
        for j in range(lo[i], hi[i] + 1):
            if i == 0 and j == 0:
                row[0] = 0.0
                continue
            best, best_k = inf, 0
            for k, (di, dj, mc) in enumerate(MOVE_COST):
                pi, pj = i - di, j - dj
                if pi < 0 or pj < 0 or pj < lo[pi] or pj > hi[pi]:
                    continue
                prev = cost_rows[pi][pj - lo[pi]] if pi < i else row[pj - lo[i]]
                if prev == inf:
                    continue
                if di == 0:
                    step = mc + length_cost(0, lt[pj]) * 0.5
                elif dj == 0:
                    step = mc + length_cost(ls[pi], 0) * 0.5
                else:
                    l1 = ls[pi] if di == 1 else sum(ls[pi:i])
                    l2 = lt[pj] if dj == 1 else sum(lt[pj:j])
                    sa = a_src[pi] if di == 1 else frozenset().union(*a_src[pi:i])
                    ta = a_tgt[pj] if dj == 1 else frozenset().union(*a_tgt[pj:j])
                    step = mc + length_cost(l1, l2) + anchor_cost(sa, ta)
                total = prev + step
                if total < best:
                    best, best_k = total, k
            row[j - lo[i]] = best
            back[j - lo[i]] = best_k
        cost_rows.append(row)
        back_rows.append(back)
        if progress and i % 200 == 0:
            progress(i / n)

    # Reconstrucción del camino.
    path = []
    i, j = n, m
    while i > 0 or j > 0:
        k = back_rows[i][j - lo[i]]
        di, dj, _mc = MOVE_COST[k]
        pi, pj = i - di, j - dj
        step = cost_rows[i][j - lo[i]] - cost_rows[pi][pj - lo[pi]]
        path.append((pi, i, pj, j, step))
        i, j = pi, pj
    path.reverse()
    if progress:
        progress(1.0)
    return path
