"""
analyze_full.py — РАСШИРЕННЫЙ анализ: ~20 признаков на трек (для тотального разбора).

К базовым 6 добавлены: биттерсвит, чёткость тональности, динамика, «грязь» (дисторшн),
спектральные форма/ширина, шумность, тембровая текстура (MFCC), чёткость пульса.

Читает песни из data/tracks.json (+ трек-находку kah), пропускает длинные (>15 мин).
Аудио берёт из кэша audio/ (почти всё уже скачано ранее) — потому быстро.

Выход: data/features_full.csv   (не трогает старый features.csv)
Запуск: uv run analyze_full.py   (кэш + пропуск готовых, прерываемо)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import warnings
from pathlib import Path

import librosa
import numpy as np

from analyze_audio import KS_MAJOR, KS_MINOR, NOTES, download_clip, estimate_key

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
DATA = ROOT / "data"
AUDIO = ROOT / "audio"
OUT = DATA / "features_full.csv"

# трек-находка пользователя, чтобы участвовал наравне
EXTRA = [{"videoId": "GKpVPugt0IU", "artists": ["егорbez"], "title": "kah", "duration_seconds": 120}]

FIELDS = ["videoId", "artists", "title",
          "tempo", "key", "mode", "minor_score", "bittersweet", "key_strength",
          "percussive", "energy_rms", "dynamics", "brightness", "rolloff", "bandwidth",
          "flatness", "zcr", "contrast", "onset_rate", "pulse_clarity",
          "mfcc1", "mfcc2", "mfcc3", "mfcc4", "status"]


def key_strength(chroma_mean: np.ndarray) -> float:
    """Насколько уверенно вообще определяется тональность (макс корреляция с 24 шаблонами)."""
    best = -1.0
    for i in range(12):
        best = max(best, float(np.corrcoef(np.roll(KS_MAJOR, i), chroma_mean)[0, 1]),
                   float(np.corrcoef(np.roll(KS_MINOR, i), chroma_mean)[0, 1]))
    return round(best, 3)


def analyze_full(path: Path) -> dict:
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    if y.size < sr:
        raise ValueError("слишком короткий фрагмент")

    tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0])
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    cm = chroma.mean(axis=1)
    key, mode, minor_score = estimate_key(cm)

    y_h, y_p = librosa.effects.hpss(y)
    percussive = float(np.sum(y_p ** 2) / (np.sum(y_h ** 2) + np.sum(y_p ** 2) + 1e-9))

    rms = librosa.feature.rms(y=y)[0]
    energy_rms = float(np.mean(rms))
    dynamics = float(np.std(rms) / (np.mean(rms) + 1e-9))            # динамика: дышит vs стена звука

    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr)))
    bandwidth = float(np.mean(librosa.feature.spectral_bandwidth(y=y, sr=sr)))
    flatness = float(np.mean(librosa.feature.spectral_flatness(y=y)))  # шумность/«грязь»
    zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
    contrast = float(np.mean(librosa.feature.spectral_contrast(y=y, sr=sr)))
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=5).mean(axis=1)     # текстура тембра

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    onset_rate = len(onsets) / (len(y) / sr)
    ac = librosa.autocorrelate(onset_env)
    pulse_clarity = float(ac[1:].max() / (ac[0] + 1e-9)) if ac.size > 1 else 0.0

    return {
        "tempo": round(tempo, 1), "key": key, "mode": mode,
        "minor_score": minor_score,
        "bittersweet": round(1 - 2 * abs(minor_score - 0.5), 3),   # 1 = ровно на грани маж/мин
        "key_strength": key_strength(cm),
        "percussive": round(percussive, 3), "energy_rms": round(energy_rms, 4),
        "dynamics": round(dynamics, 3), "brightness": round(float(np.mean(cent)), 1),
        "rolloff": round(rolloff, 1), "bandwidth": round(bandwidth, 1),
        "flatness": round(flatness, 4), "zcr": round(zcr, 4),
        "contrast": round(contrast, 3), "onset_rate": round(onset_rate, 2),
        "pulse_clarity": round(pulse_clarity, 3),
        "mfcc1": round(float(mfcc[1]), 2), "mfcc2": round(float(mfcc[2]), 2),
        "mfcc3": round(float(mfcc[3]), 2), "mfcc4": round(float(mfcc[4]), 2),
    }


def load_done() -> set[str]:
    if not OUT.exists():
        return set()
    with OUT.open(encoding="utf-8") as f:
        return {r["videoId"] for r in csv.DictReader(f) if r.get("status") == "ok"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-seconds", type=int, default=900)
    args = ap.parse_args()
    AUDIO.mkdir(exist_ok=True); DATA.mkdir(exist_ok=True)

    tracks = json.loads((DATA / "tracks.json").read_text(encoding="utf-8"))["tracks"] + EXTRA
    tracks = [t for t in tracks if not t.get("duration_seconds") or t["duration_seconds"] <= args.max_seconds]

    done = load_done()
    new = not OUT.exists()
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for i, t in enumerate(tracks, 1):
            vid = t["videoId"]
            label = f"{', '.join(t['artists'])} — {t['title']}"
            if vid in done:
                continue
            print(f"[{i}/{len(tracks)}] ♪ {label}", file=sys.stderr)
            row = {"videoId": vid, "artists": ", ".join(t["artists"]), "title": t["title"], "status": "ok"}
            clip = AUDIO / f"{vid}.mp3"
            if not clip.exists():
                clip = download_clip(vid)
            if clip is None:
                row["status"] = "download_failed"; w.writerow(row); f.flush(); continue
            try:
                row.update(analyze_full(clip))
            except Exception as e:
                row["status"] = f"analyze_failed:{e.__class__.__name__}"
            w.writerow(row); f.flush()
    print(f"\n✓ {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
