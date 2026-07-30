"""Извлечение акустических признаков из аудиофайла.

Самодостаточная адаптация корневых analyze_audio.py / analyze_full.py
(НЕ импорт из корня репозитория): профили Крумхансла-Шмуклера для тональности,
librosa для остальных признаков.

analyze(path) -> dict:
  tempo         — перцептивный темп, BPM (SPEC v0.13 §A1)  ← «быстрое»
  tempo_raw     — сырой темп beat_track (до выбора октавы)
  key, mode     — тональность и лад (major/minor)          ← «меланхоличное»
  minor_score   — уверенность в миноре, 0..1               ← сила меланхолии
  bittersweet   — близость к грани мажор/минор, 0..1 (1 = ровно на грани)
  percussive    — доля ударной энергии, 0..1               ← «ритмичное / драйв»
  energy_rms    — громкость/плотность звука                ← энергия
  brightness    — спектральный центроид, Гц                ← светлый/тёмный тембр
  onset_rate    — плотность атак в секунду                 ← «суетливость»
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# --- Перцептивный темп (SPEC v0.13 §A1) ---
# Октавные ошибки beat_track: Эйнауди «Nobody Knows» — 235 bpm при ощущаемых
# ~57-59 (детектор цепляется за 16-е). Кандидаты t, t/2, t/4, 2t сверяются
# с автокорреляцией огибающей атак: у перцептивного темпа пик сильнее.
TEMPO_MIN, TEMPO_MAX = 55.0, 180.0
# окрестность пика: ±2 бина вокруг лага кандидата (сетка кадров дискретна,
# реальный пик огибающей размазан на несколько кадров)
_PEAK_NEIGHBORHOOD = 2
# hop_length огибающей атак (librosa-умолчание onset_strength/beat_track)
_HOP_LENGTH = 512


def pick_perceptual_tempo(
    tempo_raw: float, onset_env, frame_rate: float
) -> float:
    """Перцептивный выбор темпа из октавных кандидатов (SPEC v0.13 §A1).

    Кандидаты C = {t, t/2, t/4, 2t} ∩ [55, 180] — в этом порядке. Для каждого
    считается сила пика автокорреляции огибающей атак (onset_env, кадровая
    частота frame_rate кадров/сек) на лаге кандидата: максимум в окрестности
    ±2 бина, без интерполяции. Побеждает кандидат с максимальной силой;
    при равенстве — более ранний в списке (сырой t предпочтительнее его
    субгармоник: честный быстрый бит не фолдится «за компанию»).

    Пустой C (t < 27.5 или t > 720) — фолд t вдвое (вверх или вниз)
    до попадания в [55, 180]; t <= 0 возвращается как есть.

    Чистая функция: numpy-only, юнит-тестируется на синтетических кликах.
    Демо-эталон: превью «Ludovico Einaudi — Nobody Knows» даёт ~57-59
    вместо 235 (проверено вручную 17.07 — сильнейший пик диапазона на 59).
    """
    t = float(tempo_raw)
    if t <= 0:
        return t
    candidates = [c for c in (t, t / 2, t / 4, t * 2) if TEMPO_MIN <= c <= TEMPO_MAX]
    if not candidates:
        while t > TEMPO_MAX:
            t /= 2
        while t < TEMPO_MIN:
            t *= 2
        return t

    env = np.asarray(onset_env, dtype=float)
    env = env - env.mean()
    n = env.size
    # автокорреляция без пер-лагового нормирования: у короткого лага чуть
    # больше слагаемых — на точных субгармониках (изохронные клики) это
    # разводит «ничью» в пользу сырого t, а не его половины
    ac = np.correlate(env, env, mode="full")[n - 1:]
    norm = float(ac[0]) if ac[0] > 0 else 1.0

    strengths = []
    for c in candidates:
        lag = round(60.0 / c * frame_rate)
        lo = max(1, lag - _PEAK_NEIGHBORHOOD)
        hi = min(n - 1, lag + _PEAK_NEIGHBORHOOD)
        if lag >= n or lo > hi:
            strengths.append(-np.inf)  # лаг за пределами огибающей
        else:
            strengths.append(float(ac[lo:hi + 1].max()) / norm)
    return float(candidates[int(np.argmax(strengths))])


# Профили Крумхансла-Шмуклера — эталонные веса ступеней для мажора и минора.
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def estimate_key(chroma_mean: np.ndarray) -> tuple[str, str, float]:
    """Оценка тональности и лада по усреднённой хромаграмме (Крумхансл-Шмуклер).

    Возвращает (нота, 'major'|'minor', minor_score 0..1), где minor_score —
    насколько минорная гипотеза сильнее мажорной (0.5 = поровну).
    """
    best = (-2.0, 0, "major")
    corrs_major: list[float] = []
    corrs_minor: list[float] = []
    for i in range(12):
        cm = float(np.corrcoef(np.roll(KS_MAJOR, i), chroma_mean)[0, 1])
        cn = float(np.corrcoef(np.roll(KS_MINOR, i), chroma_mean)[0, 1])
        corrs_major.append(cm)
        corrs_minor.append(cn)
        if cm > best[0]:
            best = (cm, i, "major")
        if cn > best[0]:
            best = (cn, i, "minor")
    best_major = max(corrs_major)
    best_minor = max(corrs_minor)
    # 0.5 = поровну, >0.5 = скорее минор. Сжимаем разницу в 0..1.
    minor_score = float(0.5 + 0.5 * np.tanh((best_minor - best_major) * 3))
    return NOTES[best[1]], best[2], round(minor_score, 3)


def analyze(path: Path | str) -> dict:
    """Считает признаки по аудиофайлу (30-сек превью достаточно)."""
    # librosa импортируется лениво: тяжёлый импорт (~секунды) не нужен
    # ни demo-режиму, ни тестам движка портрета.
    import librosa

    y, sr = librosa.load(str(path), sr=22050, mono=True)
    if y.size < sr:  # меньше секунды — бесполезно
        raise ValueError("слишком короткий фрагмент")

    # SPEC v0.13 §A1: сырой beat_track + перцептивный выбор октавы по
    # автокорреляции огибающей атак (октавная ошибка: 235 vs ощущаемых ~59)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=_HOP_LENGTH)
    tempo_raw = float(np.atleast_1d(
        librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=_HOP_LENGTH)[0]
    )[0])
    tempo = pick_perceptual_tempo(tempo_raw, onset_env, frame_rate=sr / _HOP_LENGTH)

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    key, mode, minor_score = estimate_key(chroma.mean(axis=1))

    y_h, y_p = librosa.effects.hpss(y)
    e_h, e_p = float(np.sum(y_h**2)), float(np.sum(y_p**2))
    percussive = e_p / (e_h + e_p + 1e-9)

    energy_rms = float(np.mean(librosa.feature.rms(y=y)))
    brightness = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    onset_rate = len(onsets) / (len(y) / sr)

    return {
        "tempo": round(tempo, 1),
        # сырой t — рядом, просто полем результата (SPEC v0.13 §A1);
        # в кэш признаков уходит этот же dict — отдельной колонки нет
        "tempo_raw": round(tempo_raw, 1),
        "key": key,
        "mode": mode,
        "minor_score": minor_score,
        # 1 = ровно на грани мажор/минор (адаптация analyze_full.py)
        "bittersweet": round(1 - 2 * abs(minor_score - 0.5), 3),
        "percussive": round(percussive, 3),
        "energy_rms": round(energy_rms, 4),
        "brightness": round(brightness, 1),
        "onset_rate": round(onset_rate, 2),
    }
