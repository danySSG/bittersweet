"""
make_explainer.py — показать, ЧТО анализатор "видит" в треке.

Строит 4 панели для одного трека и подписывает, откуда берётся каждый признак:
  1. волна            — звук как колебание во времени
  2. спектрограмма    — частоты во времени; линия = яркость (спектральный центроид)
  3. хромаграмма      — какие из 12 нот звучат → лад (минор/мажор)
  4. пульс + биты     — сила атак; отсюда темп

Запуск:  uv run make_explainer.py --match "veridis"
Выход:   data/explain_<videoId>.png
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import librosa
import librosa.display
import matplotlib.pyplot as plt
import numpy as np

from analyze_audio import NOTES, analyze, download_clip, estimate_key

ROOT = Path(__file__).parent
DATA = ROOT / "data"
AUDIO = ROOT / "audio"


def find_track(match: str) -> dict:
    match = match.lower()
    with (DATA / "features.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["status"] == "ok" and (match in (r["title"] or "").lower()
                                        or match in (r["artists"] or "").lower()):
                return r
    raise SystemExit(f"Не нашёл трек по '{match}' в features.csv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", required=True, help="часть названия/артиста")
    args = ap.parse_args()

    t = find_track(args.match)
    vid = t["videoId"]
    clip = AUDIO / f"{vid}.mp3"
    if not clip.exists():
        clip = download_clip(vid)
    y, sr = librosa.load(str(clip), sr=22050, mono=True)

    # --- признаки ---
    tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0])
    _, beats = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beats, sr=sr)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    key, mode, minor_score = estimate_key(chroma.mean(axis=1))
    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    cent_t = librosa.times_like(cent, sr=sr)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_t = librosa.times_like(onset_env, sr=sr)
    y_h, y_p = librosa.effects.hpss(y)
    perc = float(np.sum(y_p ** 2) / (np.sum(y_h ** 2) + np.sum(y_p ** 2) + 1e-9))
    brightness = float(np.mean(cent))

    # --- рисуем ---
    plt.style.use("dark_background")
    fig, ax = plt.subplots(4, 1, figsize=(11, 12))
    fig.patch.set_facecolor("#0d0d12")
    for a in ax:
        a.set_facecolor("#0d0d12")

    title = f"{t['artists']} — {t['title']}"
    fig.suptitle(
        f"{title}\nтемп {tempo:.0f} BPM   ·   {key} {mode} (minor={minor_score})   ·   "
        f"ярк. {brightness:.0f} Гц   ·   ударность {perc:.2f}",
        color="#e8e8f0", fontsize=13, y=0.995,
    )

    # 1. волна
    librosa.display.waveshow(y, sr=sr, ax=ax[0], color="#7F77DD")
    ax[0].set_title("1. волна — колебание звука во времени (громкость = энергия)",
                    color="#c9c9de", loc="left", fontsize=11)
    ax[0].set_ylabel("амплитуда")

    # 2. спектрограмма + яркость
    S = librosa.amplitude_to_db(np.abs(librosa.stft(y)), ref=np.max)
    img = librosa.display.specshow(S, sr=sr, x_axis="time", y_axis="log", ax=ax[1], cmap="magma")
    ax[1].plot(cent_t, cent, color="#5DCAA5", lw=1.3, label="яркость (центроид)")
    ax[1].legend(loc="upper right", fontsize=9, facecolor="#1b1b28")
    ax[1].set_title("2. спектрограмма — какие частоты и когда; зелёная линия = «центр тяжести» = тембр",
                    color="#c9c9de", loc="left", fontsize=11)
    ax[1].set_ylabel("частота, Гц")

    # 3. хромаграмма
    librosa.display.specshow(chroma, sr=sr, x_axis="time", y_axis="chroma", ax=ax[2], cmap="magma")
    ax[2].set_title(f"3. хромаграмма — сила каждой из 12 нот → лад: определён {key} {mode}",
                    color="#c9c9de", loc="left", fontsize=11)

    # 4. пульс + биты
    ax[3].plot(onset_t, onset_env, color="#D4537E", lw=1, label="сила атак")
    ax[3].vlines(beat_times, 0, onset_env.max(), color="#8a8aa0", ls="--", lw=0.7, label="биты")
    ax[3].legend(loc="upper right", fontsize=9, facecolor="#1b1b28")
    ax[3].set_title(f"4. пульс — сила ударов/атак; расстояние между битами → темп {tempo:.0f} BPM",
                    color="#c9c9de", loc="left", fontsize=11)
    ax[3].set_xlabel("время, сек"); ax[3].set_ylabel("сила")

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    out = DATA / f"explain_{vid}.png"
    fig.savefig(out, dpi=110, facecolor="#0d0d12")
    print(f"✓ {out}")
    print(f"  {title}: {tempo:.0f} BPM, {key} {mode}, ярк {brightness:.0f}Гц, удар {perc:.2f}")


if __name__ == "__main__":
    main()
