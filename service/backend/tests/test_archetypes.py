"""Архетипы кластеров (SPEC v0.3 §A): таблица правил покрыта таблично + дедуп имён."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.portrait import (
    ARCHETYPE_FALLBACK,
    ARCHETYPE_NAMES,
    ARCHETYPE_RULES,
    _archetype,
    build_portrait,
)

# медианы «библиотеки», относительно которых считаются правила
MED = pd.Series({
    "tempo": 120.0, "minor_score": 0.5, "bittersweet": 0.5,
    "percussive": 0.3, "energy_rms": 0.2, "brightness": 1800.0, "onset_rate": 3.0,
})

# нейтральный кластер: не попадает ни в одно правило -> fallback «между строк»
NEUTRAL = {
    "tempo": 100.0, "minor_score": 0.5, "bittersweet": 0.5,
    "percussive": 0.1, "energy_rms": 0.5, "brightness": 2500.0, "onset_rate": 1.0,
}

# (переопределения относительно NEUTRAL, ожидаемое имя) — по строке на правило спеки
CASES = [
    ({"bittersweet": 0.85}, "биттерсвит"),
    ({"minor_score": 0.7, "tempo": 150.0, "percussive": 0.5}, "грустный бэнгер"),  # перкуссия
    ({"minor_score": 0.7, "tempo": 150.0, "onset_rate": 5.0}, "грустный бэнгер"),  # onset
    ({"minor_score": 0.7, "percussive": 0.1, "energy_rms": 0.1}, "тихая грусть"),
    ({"minor_score": 0.3, "brightness": 2500.0}, "светлая сторона"),
    ({"percussive": 0.5, "onset_rate": 5.0}, "грув"),
    ({"brightness": 1000.0}, "тёмная материя"),
    ({}, "между строк"),
]


@pytest.mark.parametrize("overrides, expected", CASES)
def test_archetype_rules_table(overrides, expected):
    means = pd.Series({**NEUTRAL, **overrides})
    result = _archetype(means, MED)
    assert result["name"] == expected
    assert result["emoji"]


def test_first_matching_rule_wins():
    """Правила проверяются по порядку: биттерсвит бьёт грустный бэнгер."""
    means = pd.Series({
        **NEUTRAL,
        "bittersweet": 0.9, "minor_score": 0.7, "tempo": 160.0, "percussive": 0.6,
    })
    assert _archetype(means, MED)["name"] == "биттерсвит"


def test_archetype_vocabulary_covers_rules_and_fallback():
    assert len(ARCHETYPE_NAMES) == len(ARCHETYPE_RULES) + 1
    assert ARCHETYPE_NAMES[-1] == ARCHETYPE_FALLBACK[0]
    assert len(set(ARCHETYPE_NAMES)) == len(ARCHETYPE_NAMES)


def _all_bittersweet_df(n: int = 24) -> pd.DataFrame:
    """Две тempo-группы, но обе с bittersweet >= 0.9 — оба кластера «биттерсвит»."""
    rng = np.random.default_rng(7)
    rows = []
    for i in range(n):
        slow = i < n // 2
        rows.append({
            "videoId": f"v{i:03d}", "artists": f"A{i}", "title": f"T{i}",
            "tempo": float(rng.uniform(65, 75) if slow else rng.uniform(160, 170)),
            "key": "A", "mode": "minor", "minor_score": 0.5,
            "bittersweet": float(rng.uniform(0.9, 1.0)),
            "percussive": float(rng.uniform(0.1, 0.5)),
            "energy_rms": float(rng.uniform(0.1, 0.3)),
            "brightness": float(rng.uniform(1500, 2100)),
            "onset_rate": float(rng.uniform(1, 5)),
            "status": "ok",
        })
    return pd.DataFrame(rows)


def test_archetype_dedup_suffix():
    """Второй одноимённый архетип получает суффикс различия «· ~N bpm»."""
    portrait = build_portrait(_all_bittersweet_df(), k=2)
    names = [c["archetype"]["name"] for c in portrait["clusters"]]
    assert len(names) == 2
    assert len(set(names)) == len(names)  # дубли недопустимы
    assert all(n.startswith("биттерсвит") for n in names)
    assert any("bpm" in n for n in names)  # суффикс различия темпом


def test_cluster_contract_has_archetype():
    portrait = build_portrait(_all_bittersweet_df(), k=2)
    for c in portrait["clusters"]:
        assert set(c["archetype"]) == {"name", "emoji"}
        assert c["label"]  # техническое имя остаётся в label
