"""
discover.py — найти новые треки по ВСЕМУ вкусу (не одна зона), разнообразно.

  1. Сиды — по 2 представителя из каждого настроения (кластера).
  2. Радио YouTube Music по каждому сиду → пул кандидатов.
  3. Выкидываем то, что уже в лайках; не больше 2 треков на артиста.
  4. Каждого анализируем и оцениваем близость к твоей библиотеке (k-NN в пространстве признаков).
  5. Ранжируем; помечаем, к какому твоему настроению трек ближе.

Выход: data/discoveries.csv   Запуск: uv run discover.py [--per-seed N] [--cap M]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from ytmusicapi import YTMusic

from analyze_audio import analyze, download_clip

ROOT = Path(__file__).parent
DATA = ROOT / "data"
AUTH = ROOT / "auth" / "browser.json"
FEATS = ["tempo", "minor_score", "bittersweet", "percussive", "brightness", "onset_rate"]

# короткие человеческие имена настроений
NICE = {
    "средний · биттерсвит · яркий": "биттерсвит",
    "средний · меланхоличный · яркий": "меланхолия (яркая)",
    "средний · смешанный · яркий": "грув/смешанное",
    "средний · светлый · яркий": "светлое",
    "спокойный · смешанный · тёмный": "спокойное тёмное",
    "средний · меланхоличный · тёмный": "тёмный минор",
}


def bs(minor):
    return 1 - 2 * abs(minor - 0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-seed", type=int, default=16)
    ap.add_argument("--cap", type=int, default=60)
    args = ap.parse_args()
    if not AUTH.exists():
        raise SystemExit("Нет auth/browser.json.")

    lib = pd.read_csv(DATA / "clusters_full.csv")
    for f in FEATS:
        lib[f] = pd.to_numeric(lib[f], errors="coerce")
    lib = lib.dropna(subset=FEATS)

    scaler = StandardScaler().fit(lib[FEATS].values)
    Xlib = scaler.transform(lib[FEATS].values)
    nn = NearestNeighbors(n_neighbors=6).fit(Xlib)
    # центроиды настроений для пометки
    cents = {lbl: scaler.transform([g[FEATS].mean().values])[0] for lbl, g in lib.groupby("cluster_label")}

    # сиды: по 2 из каждого настроения
    seeds = []
    for lbl, g in lib.groupby("cluster_label"):
        for _, r in g.head(2).iterrows():
            seeds.append({"videoId": r["videoId"], "artists": r["artists"], "title": r["title"]})
    have = {t["videoId"] for t in json.loads((DATA / "tracks.json").read_text(encoding="utf-8"))["tracks"]}

    print(f"сидов: {len(seeds)} (по 2 на настроение)", file=sys.stderr)
    yt = YTMusic(str(AUTH))
    cands, art_count = {}, Counter()
    for s in seeds:
        try:
            radio = yt.get_watch_playlist(videoId=s["videoId"], limit=args.per_seed)
        except Exception:
            continue
        for t in radio.get("tracks", []):
            vid = t.get("videoId")
            if not vid or vid in have or vid in cands:
                continue
            arts = ", ".join(a["name"] for a in (t.get("artists") or []) if a.get("name"))
            if art_count[arts] >= 2:              # не больше 2 на артиста
                continue
            art_count[arts] += 1
            cands[vid] = {"videoId": vid, "artists": arts, "title": t.get("title")}
    cand_list = list(cands.values())[: args.cap]
    print(f"кандидатов к анализу: {len(cand_list)}\n", file=sys.stderr)

    scored = []
    for i, c in enumerate(cand_list, 1):
        print(f"[{i}/{len(cand_list)}] {c['artists']} — {c['title']}", file=sys.stderr)
        clip = download_clip(c["videoId"])
        if clip is None:
            continue
        try:
            feat = analyze(clip)
        except Exception:
            continue
        feat["bittersweet"] = bs(feat["minor_score"])
        x = scaler.transform([[feat[f] for f in FEATS]])
        dist = float(nn.kneighbors(x)[0].mean())          # ближе = меньше
        mood = min(cents, key=lambda l: np.linalg.norm(x[0] - cents[l]))
        scored.append({**c, **feat, "closeness": round(100 * np.exp(-dist), 1),
                       "mood": NICE.get(mood, mood)})

    scored.sort(key=lambda r: -r["closeness"])
    if scored:
        with (DATA / "discoveries.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(scored[0].keys())); w.writeheader(); w.writerows(scored)

    print("\n" + "=" * 70)
    print("ОТКРЫТИЯ — новое под твой вкус (разнообразно):")
    print("=" * 70)
    for r in scored[:24]:
        print(f"  {r['closeness']:>5.0f}  [{r['mood']:<18}] {r['tempo']:>5.0f}bpm {r['key']:>2} {r['mode']:<5} │ {r['artists']} — {r['title']}")
    print(f"\n✓ data/discoveries.csv")


if __name__ == "__main__":
    main()
