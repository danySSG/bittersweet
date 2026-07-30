"""
score_track.py — оценить ЛЮБОЙ трек по ссылке/ID: признаки, match к твоей сигнатуре
и ближайшее из 6 настроений.

Запуск:  uv run score_track.py "https://music.youtube.com/watch?v=..."
         uv run score_track.py VIDEOID
"""

from __future__ import annotations

import json
import sys
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
from ytmusicapi import YTMusic

from analyze_audio import analyze, download_clip
from pathlib import Path

DATA = Path(__file__).parent / "data"
FEATURES = ["tempo", "minor_score", "percussive", "energy_rms", "brightness", "onset_rate"]


def parse_id(s: str) -> str:
    if s.startswith("http"):
        q = parse_qs(urlparse(s).query)
        return q["v"][0] if "v" in q else s.rstrip("/").split("/")[-1]
    return s


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Укажи ссылку или videoId.")
    vid = parse_id(sys.argv[1])

    # метаданные
    title, author = vid, "?"
    try:
        d = YTMusic().get_song(vid)["videoDetails"]
        title, author = d.get("title", vid), d.get("author", "?")
    except Exception:
        pass
    print(f"\n▶ {author} — {title}\n")

    clip = download_clip(vid)
    if clip is None:
        raise SystemExit("Не удалось скачать трек.")
    feat = analyze(clip)

    # match к сигнатуре
    sig = json.loads((DATA / "signature_fast_dark_minor.json").read_text(encoding="utf-8"))
    center, spread = sig["center"], {f: max(v, 1e-6) for f, v in sig["spread"].items()}
    z = np.array([(feat[f] - center[f]) / spread[f] for f in FEATURES])
    match = float(100 * np.exp(-0.5 * np.mean(z ** 2)))

    # ближайшее из 6 настроений (по стандартизованному расстоянию до центроида)
    lib = pd.read_csv(DATA / "clusters.csv")
    mu, sd = lib[FEATURES].mean(), lib[FEATURES].std().replace(0, 1e-6)
    xz = np.array([(feat[f] - mu[f]) / sd[f] for f in FEATURES])
    best_lbl, best_d = None, 1e9
    for lbl, g in lib.groupby("cluster_label"):
        cz = np.array([(g[f].mean() - mu[f]) / sd[f] for f in FEATURES])
        d = float(np.linalg.norm(xz - cz))
        if d < best_d:
            best_lbl, best_d = lbl, d

    print(f"признаки:  {feat['tempo']:.0f} BPM · {feat['key']} {feat['mode']} "
          f"(minor={feat['minor_score']}) · ударность {feat['percussive']} · "
          f"ярк {feat['brightness']:.0f}Гц · атак/с {feat['onset_rate']}")
    print(f"\nmatch к «быстрому тёмному минору»:  {match:.1f} / 100")
    print(f"ближайшее настроение:  {best_lbl}")


if __name__ == "__main__":
    main()
