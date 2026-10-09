import random

from traductor.align import align, anchors
from traductor.retrieval import Example, ExampleIndex

NAMES = ["Arlen", "Mirela", "Tobias", "Quintra", "Velasco", "Harrow", "Ysolde", "Brannock"]
WORDS = "wind river stone light house road field night morning letter door window table voice hand".split()


def _para(rng):
    words = [rng.choice(WORDS) for _ in range(rng.randint(8, 80))]
    for _ in range(rng.randint(0, 2)):
        words.insert(rng.randrange(len(words)), rng.choice(NAMES))
    return " ".join(words).capitalize() + "."


def _fake_translation(p, rng):
    out = []
    for w in p.split():
        out.append(w if w.strip(".") in NAMES else w[::-1] + ("o" if rng.random() < 0.3 else ""))
    return " ".join(out)


def test_anchors_ignore_common_words():
    a = anchors("The night Arlen came to Velasco in 1890, he said nothing.")
    assert {"arlen", "velasco", "1890"} <= a
    assert "the" not in a


def test_alignment_with_merges_splits_and_extra_material():
    rng = random.Random(3)
    src = [_para(rng) for _ in range(300)]
    tgt = [f"Nota del traductor número {i} sobre la edición." for i in range(5)]
    gold = []
    i = 0
    while i < len(src):
        r = rng.random()
        if r < 0.05 and i + 1 < len(src):
            gold.append((src[i] + " " + src[i + 1], len(tgt)))
            tgt.append(_fake_translation(src[i], rng) + " " + _fake_translation(src[i + 1], rng))
            i += 2
        else:
            gold.append((src[i], len(tgt)))
            tgt.append(_fake_translation(src[i], rng))
            i += 1
    pairs, stats = align(src, tgt)
    expected = {(s, tgt[j]) for s, j in gold}
    correct = sum(1 for p in pairs if (p.src, p.tgt) in expected)
    assert correct / len(gold) > 0.95
    assert stats.coverage > 0.95
    assert stats.quality == "buena"


def test_empty_inputs():
    pairs, stats = align([], ["a"])
    assert pairs == [] and stats.quality == "baja"


def test_retrieval_prefers_similar_and_glossary_terms():
    examples = [
        Example("The lighthouse keeper climbed the stairs at night.", "El farero subió la escalera de noche.", "A"),
        Example("Mirela sold bread in the market every morning.", "Mirela vendía pan en el mercado cada mañana.", "A"),
        Example("The storm broke the windows of the old house.", "La tormenta rompió las ventanas de la casa vieja.", "A"),
    ]
    index = ExampleIndex(examples)
    found = index.search("At night the keeper of the lighthouse watched the sea.", k=1)
    assert found[0].tgt.startswith("El farero")
    found = index.search("Nothing else happened that day.", k=2, must_include=["Mirela"])
    assert found and found[0].src.startswith("Mirela")
