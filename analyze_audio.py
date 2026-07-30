"""
analyze_audio.py — превратить каждый трек в набор измеримых признаков.

Что делает:
  1. Читает data/tracks.json.
  2. Для каждого трека скачивает кусок аудио (по умолчанию 30–150 сек) через yt-dlp.
  3. Считает признаки через librosa и дописывает их в data/features.csv.

Признаки (и как они связаны с "ритмичное / быстрое / меланхоличное"):
  tempo         — темп в BPM                              ← "быстрое"
  key, mode     — тональность и лад (major/minor)         ← "меланхоличное" (минор)
  minor_score   — насколько уверенно это минор (0..1)     ← сила меланхолии
  percussive    — доля ударной энергии (0..1)             ← "ритмичное / драйв"
  energy_rms    — громкость/плотность звука               ← энергия
  brightness    — спектральный центроид, Гц (тембр)       ← светлый/тёмный звук
  onset_rate    — плотность атак в секунду                ← насколько "суетливо"/дробно

Запуск:
  uv run analyze_audio.py            # все треки из tracks.json (с кэшем, можно прерывать)
  uv run analyze_audio.py --limit 30 # только первые 30 (быстрая проба)
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
import yt_dlp

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
DATA = ROOT / "data"
AUDIO = ROOT / "audio"
FEATURES = DATA / "features.csv"

# Профили Крумхансла-Шмуклера — "эталонные" веса ступеней для мажора и минора.
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

FIELDS = ["videoId", "artists", "title", "tempo", "key", "mode", "minor_score",
          "percussive", "energy_rms", "brightness", "onset_rate", "status"]

# ---- аудио: скачивание куска трека -------------------------------------------

# берём 2 минуты из середины интро (0:30–2:30) — репрезентативнее, чем самое начало
SECTION_START = 30.0
SECTION_END = 150.0


def download_clip(video_id: str) -> Path | None:
    """Качает mp3-фрагмент трека в audio/. Возвращает путь или None при ошибке."""
    out_mp3 = AUDIO / f"{video_id}.mp3"
    if out_mp3.exists() and out_mp3.stat().st_size > 0:
        return out_mp3  # кэш

    base = {
        "format": "bestaudio/best",
        "outtmpl": str(AUDIO / f"{video_id}.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    ranged = dict(base, download_ranges=yt_dlp.utils.download_range_func(None, [(SECTION_START, SECTION_END)]))
    url = f"https://music.youtube.com/watch?v={video_id}"

    # сначала пробуем вырезать фрагмент из середины; если не вышло (короткий трек и т.п.)
    # — качаем целиком.
    for attempt, opts in (("фрагмент", ranged), ("целиком", base)):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if out_mp3.exists() and out_mp3.stat().st_size > 0:
                return out_mp3
        except Exception as e:
            print(f"   … попытка «{attempt}» не удалась ({e.__class__.__name__})", file=sys.stderr)
    return None


# ---- признаки ----------------------------------------------------------------

def estimate_key(chroma_mean: np.ndarray) -> tuple[str, str, float]:
    """Оценка тональности и лада по усреднённой хромаграмме (Крумхансл-Шмуклер).

    Возвращает (нота, 'major'|'minor', minor_score 0..1), где minor_score — насколько
    минорная гипотеза сильнее мажорной.
    """
    best = (-2.0, 0, "major")
    corrs_major, corrs_minor = [], []
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


def analyze(path: Path) -> dict:
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    if y.size < sr:  # меньше секунды — бесполезно
        raise ValueError("слишком короткий фрагмент")

    tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0])

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    key, mode, minor_score = estimate_key(chroma.mean(axis=1))

    y_h, y_p = librosa.effects.hpss(y)
    e_h, e_p = float(np.sum(y_h ** 2)), float(np.sum(y_p ** 2))
    percussive = e_p / (e_h + e_p + 1e-9)

    energy_rms = float(np.mean(librosa.feature.rms(y=y)))
    brightness = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    onset_rate = len(onsets) / (len(y) / sr)

    return {
        "tempo": round(tempo, 1),
        "key": key,
        "mode": mode,
        "minor_score": minor_score,
        "percussive": round(percussive, 3),
        "energy_rms": round(energy_rms, 4),
        "brightness": round(brightness, 1),
        "onset_rate": round(onset_rate, 2),
    }


# ---- цикл по треку -----------------------------------------------------------

def load_done() -> set[str]:
    if not FEATURES.exists():
        return set()
    with FEATURES.open(encoding="utf-8") as f:
        return {row["videoId"] for row in csv.DictReader(f) if row.get("status") == "ok"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-seconds", type=int, default=900,
                    help="пропускать треки длиннее (сек); 900=15мин. 0 = без ограничения")
    args = ap.parse_args()

    AUDIO.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)

    tracks_file = DATA / "tracks.json"
    if not tracks_file.exists():
        raise SystemExit("Нет data/tracks.json — сначала: uv run fetch_tracks.py …")
    tracks = json.loads(tracks_file.read_text(encoding="utf-8"))["tracks"]
    if args.limit:
        tracks = tracks[: args.limit]

    done = load_done()
    new_file = not FEATURES.exists()
    with FEATURES.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()

        for i, t in enumerate(tracks, 1):
            vid = t["videoId"]
            label = f"{', '.join(t['artists'])} — {t['title']}"
            if vid in done:
                print(f"[{i}/{len(tracks)}] ⏭  уже есть: {label}", file=sys.stderr)
                continue
            dur = t.get("duration_seconds")
            if args.max_seconds and dur and dur > args.max_seconds:
                print(f"[{i}/{len(tracks)}] ⏭  длинная ({dur//60}м), не песня — пропуск: {label}", file=sys.stderr)
                continue
            print(f"[{i}/{len(tracks)}] ♪ {label}", file=sys.stderr)

            row = {"videoId": vid, "artists": ", ".join(t["artists"]), "title": t["title"], "status": "ok"}
            clip = download_clip(vid)
            if clip is None:
                row["status"] = "download_failed"
                writer.writerow(row); f.flush()
                continue
            try:
                row.update(analyze(clip))
                print(f"       tempo={row['tempo']} {row['key']} {row['mode']} "
                      f"(minor={row['minor_score']}) perc={row['percussive']}", file=sys.stderr)
            except Exception as e:
                print(f"   ✗ анализ не удался: {e}", file=sys.stderr)
                row["status"] = f"analyze_failed:{e.__class__.__name__}"
            writer.writerow(row); f.flush()

    print(f"\n✓ Готово. Признаки в {FEATURES}", file=sys.stderr)


if __name__ == "__main__":
    main()
