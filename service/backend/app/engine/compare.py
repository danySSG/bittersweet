"""Сравнение вкусов двух портретов — «совместимость по звуку» (SPEC v0.3 §C).

score 0..100 — косинусная близость векторов-отпечатков (fingerprint_vector),
отрицательный косинус клипуется в 0. Плюс общие архетипы, facets для
столбиков-сравнений и словесный verdict.
"""

from __future__ import annotations

import numpy as np

from app.engine.portrait import ARCHETYPE_NAMES

# Фиксированные (центр, масштаб) стандартизации скалярных граней отпечатка.
# НЕ по статистике сравниваемой пары (по двум точкам score прыгал бы от запроса
# к запросу), а по разумным диапазонам популярной музыки — score стабилен.
FACET_SCALES: dict[str, tuple[float, float]] = {
    "tempo_median": (120.0, 40.0),       # bpm: типичные библиотеки ~80..160
    "minor_share": (50.0, 30.0),         # %: от «весь мажор» до «весь минор»
    "brightness_mean": (1800.0, 800.0),  # Гц: тёмное ~1000, яркое ~2600+
    "bittersweet_share": (12.0, 10.0),   # %: типичная доля биттерсвита 0..25
}

# facets ответа: (имя грани, ключ fingerprint | None = bittersweet.share, unit, масштаб)
_FACETS: list[tuple[str, str | None, str, float]] = [
    ("темп", "tempo_median", "bpm", FACET_SCALES["tempo_median"][1]),
    ("минор", "minor_share", "%", FACET_SCALES["minor_share"][1]),
    ("яркость", "brightness_mean", "Гц", FACET_SCALES["brightness_mean"][1]),
    ("биттерсвит", None, "%", FACET_SCALES["bittersweet_share"][1]),
]

# (минимальный score, вердикт) — сверху вниз
VERDICTS: list[tuple[int, str]] = [
    (80, "музыкальные близнецы"),
    (60, "звучите в унисон"),
    (40, "есть общие волны"),
    (0, "противоположности"),
]


def archetype_base(name: str) -> str:
    """Имя архетипа без дедуп-суффикса: «грустный бэнгер · ~162 bpm» -> «грустный бэнгер»."""
    return name.split(" · ")[0]


def archetype_shares(portrait: dict) -> dict[str, float]:
    """Доли архетипов 0..1 по фиксированному словарю ARCHETYPE_NAMES.

    Доля архетипа = суммарная доля библиотеки в кластерах с этим (базовым) именем.
    """
    shares = dict.fromkeys(ARCHETYPE_NAMES, 0.0)
    for c in portrait.get("clusters", []):
        base = archetype_base(c.get("archetype", {}).get("name", ""))
        if base in shares:
            shares[base] += float(c.get("share", 0)) / 100
    return shares


def fingerprint_vector(portrait: dict) -> np.ndarray:
    """Вектор-отпечаток портрета для косинусной близости.

    Компоненты (в этом порядке):
      [0] tempo_median      — (bpm - 120) / 40
      [1] minor_share       — (% - 50) / 30
      [2] brightness_mean   — (Гц - 1800) / 800
      [3] bittersweet.share — (% - 12) / 10
      [4:] доли архетипов   — по фиксированному порядку ARCHETYPE_NAMES
           («биттерсвит», «грустный бэнгер», «тихая грусть», «светлая сторона»,
            «грув», «тёмная материя», «между строк»), каждая 0..1.

    Стандартизация скаляров — по фиксированным масштабам FACET_SCALES, не по
    статистике сравниваемой пары: идентичные портреты дают косинус 1
    (score 100), а score пары не зависит от того, с кем ещё сравнивали.
    """
    fp = portrait.get("fingerprint", {})
    bs_share = float(portrait.get("bittersweet", {}).get("share", 0))
    scalars = [
        (float(fp.get("tempo_median", 0)), FACET_SCALES["tempo_median"]),
        (float(fp.get("minor_share", 0)), FACET_SCALES["minor_share"]),
        (float(fp.get("brightness_mean", 0)), FACET_SCALES["brightness_mean"]),
        (bs_share, FACET_SCALES["bittersweet_share"]),
    ]
    head = [(value - center) / scale for value, (center, scale) in scalars]
    tail = list(archetype_shares(portrait).values())
    return np.array(head + tail, dtype=float)


def similarity_score(a: dict, b: dict) -> int:
    """Косинус отпечатков, отмасштабированный в 0..100 (отрицательный -> 0)."""
    va, vb = fingerprint_vector(a), fingerprint_vector(b)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:  # вырожденный отпечаток — на практике не встречается
        return 100 if np.allclose(va, vb) else 0
    cos = float(np.dot(va, vb) / (na * nb))
    return int(round(100 * min(max(cos, 0.0), 1.0)))


def _facets(pa: dict, pb: dict) -> list[dict]:
    def value(portrait: dict, key: str | None) -> float | int:
        if key is None:
            return portrait.get("bittersweet", {}).get("share", 0)
        return portrait.get("fingerprint", {}).get(key, 0)

    return [
        {"name": name, "a": value(pa, key), "b": value(pb, key), "unit": unit}
        for name, key, unit, _ in _FACETS
    ]


def _verdict(score: int, facets: list[dict]) -> str:
    """Вердикт по диапазону score + предложение о самой близкой/далёкой грани."""
    base = next(text for lo, text in VERDICTS if score >= lo)
    scales = {name: scale for name, _, _, scale in _FACETS}
    diffs = {f["name"]: abs(float(f["a"]) - float(f["b"])) / scales[f["name"]] for f in facets}
    closest = min(diffs, key=diffs.get)
    farthest = max(diffs, key=diffs.get)
    if diffs[farthest] < 0.05:  # всё практически совпало
        return f"{base} — вы совпадаете по всем граням звука"
    return (f"{base} — ближе всего вы по грани «{closest}», "
            f"заметнее всего расходитесь по грани «{farthest}»")


def compare_portraits(a: dict, b: dict) -> dict:
    """Ответ /api/compare; a и b — dict'ы из store.get_portrait."""
    pa, pb = a["portrait"], b["portrait"]
    score = similarity_score(pa, pb)
    shares_a, shares_b = archetype_shares(pa), archetype_shares(pb)
    facets = _facets(pa, pb)
    return {
        "score": score,
        "common_archetypes": [n for n in ARCHETYPE_NAMES if shares_a[n] > 0 and shares_b[n] > 0],
        "facets": facets,
        "verdict": _verdict(score, facets),
        "a": {"id": a["id"], "source_label": a["source_label"], "created_at": a["created_at"]},
        "b": {"id": b["id"], "source_label": b["source_label"], "created_at": b["created_at"]},
    }
