"""
deep_explainer.py — два «шага под лупой»:
  step_fourier_<vid>.png — как кусочек звука раскладывается на частоты (Фурье) → спектрограмма
  step_key_<vid>.png     — как из 12 нот выбирается тональность (Крумхансл-Шмуклер)

Запуск:  uv run deep_explainer.py --match "veridis"
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import librosa
import librosa.display
import matplotlib.pyplot as plt
import numpy as np

from analyze_audio import KS_MAJOR, KS_MINOR, NOTES, download_clip
from make_explainer import find_track

ROOT = Path(__file__).parent
DATA = ROOT / "data"
AUDIO = ROOT / "audio"
BG = "#0d0d12"


def load(match: str):
    t = find_track(match)
    clip = AUDIO / f"{t['videoId']}.mp3"
    if not clip.exists():
        clip = download_clip(t["videoId"])
    y, sr = librosa.load(str(clip), sr=22050, mono=True)
    return t, y, sr


def fig_fourier(t, y, sr) -> Path:
    n_fft = 4096
    t0 = min(60.0, len(y) / sr / 2)          # берём окно из устойчивой части
    start = int(t0 * sr)
    win = y[start:start + n_fft]
    spec = np.abs(np.fft.rfft(win * np.hanning(len(win))))
    freqs = np.fft.rfftfreq(len(win), 1 / sr)

    plt.style.use("dark_background")
    fig, ax = plt.subplots(4, 1, figsize=(11, 12))
    fig.patch.set_facecolor(BG)
    for a in ax:
        a.set_facecolor(BG)
    fig.suptitle(f"{t['artists']} — {t['title']}\nкак звук становится спектрограммой (Фурье)",
                 color="#e8e8f0", fontsize=13, y=0.995)

    # 1: весь фрагмент + где взяли окошко
    librosa.display.waveshow(y, sr=sr, ax=ax[0], color="#7F77DD")
    ax[0].axvspan(t0, t0 + n_fft / sr, color="#5DCAA5", alpha=0.6)
    ax[0].set_title(f"1. из трека берём крошечное окно ~{n_fft/sr*1000:.0f} мс (зелёное, на {t0:.0f}-й сек)",
                    color="#c9c9de", loc="left", fontsize=11)
    ax[0].set_ylabel("амплитуда")

    # 2: само окошко вблизи
    tw = np.arange(len(win)) / sr * 1000
    ax[1].plot(tw, win, color="#5DCAA5", lw=0.8)
    ax[1].set_title("2. вблизи это сложное колебание — сумма многих чистых волн",
                    color="#c9c9de", loc="left", fontsize=11)
    ax[1].set_xlabel("время, мс"); ax[1].set_ylabel("амплитуда")

    # 3: спектр — Фурье раскладывает на частоты
    m = freqs <= 2000
    ax[2].fill_between(freqs[m], spec[m], color="#D4537E", alpha=0.85)
    for f in freqs[m][spec[m] > spec[m].max() * 0.35]:
        ax[2].axvline(f, color="#f5c4b3", lw=0.5, ls=":")
    ax[2].set_title("3. Фурье говорит: сколько какой частоты внутри (пики = ноты и их обертоны)",
                    color="#c9c9de", loc="left", fontsize=11)
    ax[2].set_xlabel("частота, Гц"); ax[2].set_ylabel("сколько")

    # 4: много таких окон подряд = спектрограмма
    S = librosa.amplitude_to_db(np.abs(librosa.stft(y)), ref=np.max)
    librosa.display.specshow(S, sr=sr, x_axis="time", y_axis="log", ax=ax[3], cmap="magma")
    ax[3].axvline(t0, color="#5DCAA5", lw=1.5)
    ax[3].set_title("4. сдвигаем окно вдоль трека → каждый спектр = столбик → спектрограмма",
                    color="#c9c9de", loc="left", fontsize=11)
    ax[3].set_ylabel("частота, Гц")

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    out = DATA / f"step_fourier_{t['videoId']}.png"
    fig.savefig(out, dpi=110, facecolor=BG)
    plt.close(fig)
    return out


def fig_key(t, y, sr) -> Path:
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)

    labels, scores, kinds = [], [], []
    for i in range(12):
        for prof, name in ((KS_MAJOR, "maj"), (KS_MINOR, "min")):
            labels.append(f"{NOTES[i]} {name}")
            scores.append(float(np.corrcoef(np.roll(prof, i), chroma)[0, 1]))
            kinds.append(name)
    order = np.argsort(scores)
    best = int(np.argmax(scores))

    plt.style.use("dark_background")
    fig, ax = plt.subplots(1, 2, figsize=(12, 7), gridspec_kw={"width_ratios": [1, 1.3]})
    fig.patch.set_facecolor(BG)
    for a in ax:
        a.set_facecolor(BG)
    fig.suptitle(f"{t['artists']} — {t['title']}\nкак выбирается тональность: определён {labels[best]}",
                 color="#e8e8f0", fontsize=13, y=0.99)

    # слева: 12 нот
    colors = ["#5DCAA5" if v == chroma.max() else "#7F77DD" for v in chroma]
    ax[0].bar(NOTES, chroma, color=colors)
    ax[0].set_title("1. средняя сила каждой из 12 нот в треке",
                    color="#c9c9de", loc="left", fontsize=11)
    ax[0].set_ylabel("сила")

    # справа: 24 совпадения с шаблонами
    ys = np.arange(24)
    cols = []
    for idx in order:
        if idx == best:
            cols.append("#5DCAA5")
        elif kinds[idx] == "min":
            cols.append("#7F77DD")
        else:
            cols.append("#534AB7")
    ax[1].barh(ys, [scores[i] for i in order], color=cols)
    ax[1].set_yticks(ys)
    ax[1].set_yticklabels([labels[i] for i in order], fontsize=8)
    ax[1].set_title("2. совпадение с 24 шаблонами (мажор/минор × 12 нот); зелёный = победитель",
                    color="#c9c9de", loc="left", fontsize=11)
    ax[1].set_xlabel("корреляция с эталоном лада")

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    out = DATA / f"step_key_{t['videoId']}.png"
    fig.savefig(out, dpi=110, facecolor=BG)
    plt.close(fig)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", required=True)
    args = ap.parse_args()
    t, y, sr = load(args.match)
    p1 = fig_fourier(t, y, sr)
    p2 = fig_key(t, y, sr)
    print(f"✓ {p1}")
    print(f"✓ {p2}")


if __name__ == "__main__":
    main()
