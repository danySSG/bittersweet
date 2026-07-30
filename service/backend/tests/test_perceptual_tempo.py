"""Перцептивный темп (SPEC v0.13 §A) — БЕЗ сети и без аудиофайлов.

A1: pick_perceptual_tempo — чистая функция на синтетических кликах
(numpy-огибающие атак; клики кладутся на точную кадровую сетку, чтобы
пик автокорреляции не размазывался округлением).
A2: fold_cached_tempo — табличный тест страховки старого кэша + интеграция
в build_portrait (октавный Эйнауди больше не «самый быстрый» с >180 bpm).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.features import TEMPO_MAX, TEMPO_MIN, pick_perceptual_tempo
from app.engine.portrait import build_portrait, fold_cached_tempo

# кадровая частота огибающей в тестах: 40 кадров/сек -> клики 60/120/240 bpm
# ложатся ровно на кадры (интервалы 40/20/10 кадров), пики АК не дрожат
FRAME_RATE = 40.0
DURATION = 30.0  # секунд — как 30-сек превью


def _click_env(
    clicks_per_sec: float,
    accent_every: int = 0,
    accent: float = 2.5,
    duration: float = DURATION,
    frame_rate: float = FRAME_RATE,
) -> np.ndarray:
    """Синтетическая огибающая атак: импульсы clicks_per_sec раз в секунду.

    accent_every=k > 0 — каждый k-й клик сильнее (акцентная доля, как
    сильная доля такта поверх 16-х); 0 — все клики равные (изохронный бит).
    """
    n = int(duration * frame_rate)
    env = np.zeros(n)
    step = frame_rate / clicks_per_sec
    k = 0
    while round(k * step) < n:
        env[round(k * step)] = accent if accent_every and k % accent_every == 0 else 1.0
        k += 1
    return env


# ------------------------------------------------------- A1: выбор кандидата

def test_sixteenths_fold_to_perceived_60():
    """Клик-трек 60 bpm с 16-ми (4 атаки/сек, акцент на долю): beat_track
    цепляется за 16-е и даёт 240 — перцептивный выбор возвращает 60."""
    env = _click_env(clicks_per_sec=4.0, accent_every=4)
    assert pick_perceptual_tempo(240.0, env, FRAME_RATE) == pytest.approx(60.0)


def test_honest_120_stays_120():
    """Изохронный бит 120 bpm (2 атаки/сек, без акцентов) не фолдится:
    сила пика субгармоники не выше — побеждает сырой t."""
    env = _click_env(clicks_per_sec=2.0)
    assert pick_perceptual_tempo(120.0, env, FRAME_RATE) == pytest.approx(120.0)


def test_honest_fast_beat_stays_when_peaks_equal():
    """Честные быстрые 160 bpm без акцентов: субгармоника 80 не сильнее —
    остаётся 160 (ничья решается в пользу сырого t)."""
    env = _click_env(clicks_per_sec=160.0 / 60.0)
    assert pick_perceptual_tempo(160.0, env, FRAME_RATE) == pytest.approx(160.0)


def test_edge_t50_folds_up_via_double_candidate():
    """Edge из спеки: t=50 -> фолд вверх (кандидат 2t=100 — единственный
    в [55, 180])."""
    env = _click_env(clicks_per_sec=100.0 / 60.0)  # реальный пульс 100
    assert pick_perceptual_tempo(50.0, env, FRAME_RATE) == pytest.approx(100.0)


def test_empty_candidates_fold_down_into_range():
    """t=800: все кандидаты вне [55, 180] -> фолд вдвое вниз до диапазона."""
    env = _click_env(clicks_per_sec=2.0)
    got = pick_perceptual_tempo(800.0, env, FRAME_RATE)
    assert got == pytest.approx(100.0)  # 800 -> 400 -> 200 -> 100
    assert TEMPO_MIN <= got <= TEMPO_MAX


def test_empty_candidates_fold_up_into_range():
    """t=20: кандидаты 20/10/5/40 вне диапазона -> фолд вдвое вверх."""
    env = _click_env(clicks_per_sec=2.0)
    got = pick_perceptual_tempo(20.0, env, FRAME_RATE)
    assert got == pytest.approx(80.0)  # 20 -> 40 -> 80
    assert TEMPO_MIN <= got <= TEMPO_MAX


def test_degenerate_tempo_zero_returned_as_is():
    """t<=0 (вырожденное аудио) возвращается как есть — без вечного цикла."""
    env = np.zeros(100)
    assert pick_perceptual_tempo(0.0, env, FRAME_RATE) == 0.0


def test_einaudi_like_quarters_over_sixteenths():
    """Октавная ошибка вдвое-вчетверо: сетка 16-х (235*4/60... упрощённо
    ~4 атаки/сек) с акцентом раз в 4 клика; сырой t=235 вне диапазона,
    из кандидатов {117.5, 58.75} побеждает доля с сильным пиком ~59."""
    # клики 235 bpm (тиковая сетка детектора), акцент на каждый 4-й ->
    # перцептивная доля 58.75 bpm
    env = _click_env(clicks_per_sec=235.0 / 60.0, accent_every=4, accent=3.0)
    got = pick_perceptual_tempo(235.0, env, FRAME_RATE)
    assert got == pytest.approx(58.75)
    assert 55 <= got <= 70


# ------------------------------------------------- A2: фолд старого кэша

@pytest.mark.parametrize("tempo,onset_rate,expected", [
    (235.0, 2.1, 117.5),    # Эйнауди из старого кэша: атак мало -> октава вниз
    (162.0, 5.0, 162.0),    # честный быстрый дарквейв — НЕ трогается
    (172.0, 9.8, 172.0),    # плотный быстрый — атак много, честный
    (165.0, 1.0, 82.5),     # ровно на пороге и редкие атаки -> фолд
    (164.9, 0.1, 164.9),    # ниже порога — не трогаем даже при тишине
    (330.0, 1.0, 82.5),     # двойной фолд: 330 -> 165 -> 82.5
    (258.4, 3.05, 129.2),   # Бах/Руссо: арпеджио-прелюдия, октавный уровень нот
    (240.0, 2.3, 120.0),    # 2.3 < 240/60*0.75=3.0 -> фолд; 120 < 165 -> стоп
    (240.0, 3.2, 240.0),    # 3.2 > 3.0 — атак достаточно, не трогаем
    (90.0, 0.5, 90.0),      # обычный темп — вне зоны действия
])
def test_fold_cached_tempo_table(tempo, onset_rate, expected):
    assert fold_cached_tempo(tempo, onset_rate) == pytest.approx(expected)


def _row(i: int, tempo: float, onset_rate: float) -> dict:
    """Строка признаков для build_portrait; две группы для KMeans."""
    rng = np.random.default_rng(500 + i)
    calm = i % 2 == 0
    minor_score = float(rng.uniform(0.68, 0.85) if calm else rng.uniform(0.15, 0.32))
    return {
        "videoId": f"vid{i:03d}",
        "artists": f"Artist {i}",
        "title": f"Track {i}",
        "tempo": tempo,
        "minor_score": round(minor_score, 3),
        "bittersweet": round(1 - 2 * abs(minor_score - 0.5), 3),
        "percussive": float(rng.uniform(0.05, 0.15) if calm else rng.uniform(0.45, 0.6)),
        "energy_rms": float(rng.uniform(0.05, 0.1) if calm else rng.uniform(0.25, 0.35)),
        "brightness": float(rng.uniform(900, 1300) if calm else rng.uniform(2600, 3400)),
        "onset_rate": onset_rate,
        "status": "ok",
    }


def test_build_portrait_folds_octave_error_from_old_cache(tmp_path, monkeypatch):
    """DoD §C2: синтетика с (235, 2.1) — «самый быстрый» больше не > 180.

    Октавный трек фолдится до 117.5 и перестаёт быть «самым быстрым»:
    хайлайт достаётся честному дарквейву 162 bpm (onset_rate 5 — не тронут).
    """
    from app.config import settings
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")

    rows = [
        _row(i, tempo=float(70 + i if i % 2 == 0 else 150 + i),
             onset_rate=float(1.0 if i % 2 == 0 else 5.0))
        for i in range(14)
    ]
    rows.append(_row(14, tempo=235.0, onset_rate=2.1))   # октавный Эйнауди
    rows.append(_row(15, tempo=162.0, onset_rate=5.0))   # честный дарквейв

    portrait = build_portrait(pd.DataFrame(rows))

    fastest = next(h for h in portrait["highlights"] if h["kind"] == "fastest")
    assert "bpm" in fastest["value"]
    assert float(fastest["value"].split()[0]) <= 180

    # октавный трек в точках несёт уже фолднутый темп
    einaudi = next(p for p in portrait["points"] if p["label"].endswith("Track 14"))
    assert einaudi["features"]["tempo"] == pytest.approx(117.5)
    # честный дарквейв не тронут
    darkwave = next(p for p in portrait["points"] if p["label"].endswith("Track 15"))
    assert darkwave["features"]["tempo"] == pytest.approx(162.0)
    # медианы и fingerprint считаются по фолднутым значениям
    assert portrait["fingerprint"]["tempo_median"] <= 180
