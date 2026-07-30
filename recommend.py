"""
recommend.py — найти НОВЫЕ треки в зоне «быстрый тёмный минор».

  1. Берёт сохранённую сигнатуру (data/signature_fast_dark_minor.json) — центр зоны.
  2. Сиды = твои же эталоны этой зоны (топ по близости к сигнатуре).
  3. Тянет кандидатов из радио YouTube Music по каждому сиду.
  4. Выкидывает всё, что уже в лайках; каждого кандидата прогоняет через анализ
     и оценивает той же гауссовой формулой match (0..100).
  5. Ранжирует — ближайшие и есть «ещё такого же».

Запуск:  uv run recommend.py                    # 8 сидов, до 60 кандидатов
         uv run recommend.py --seeds 6 --cands 40
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from ytmusicapi import YTMusic

from analyze_audio import analyze, download_clip

ROOT = Path(__file__).parent
DATA = ROOT / "data"
AUTH = ROOT / "auth" / "browser.json"
SIG = DATA / "signature_fast_dark_minor.json"
ZONE_LABEL = "средний · меланхоличный · тёмный"
FEATURES = ["tempo", "minor_score", "percussive", "energy_rms", "brightness", "onset_rate"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--cands", type=int, default=60)
    args = ap.parse_args()

    if not AUTH.exists():
        raise SystemExit("Нет auth/browser.json.")
    if not SIG.exists():
        raise SystemExit("Нет сигнатуры — сначала: uv run fingerprint.py")

    sig = json.loads(SIG.read_text(encoding="utf-8"))
    center = sig["center"]
    spread = {f: max(v, 1e-6) for f, v in sig["spread"].items()}

    def match(feat) -> float:
        z = np.array([(feat[f] - center[f]) / spread[f] for f in FEATURES])
        return float(100 * np.exp(-0.5 * np.mean(z ** 2)))

    # сиды = треки зоны с максимальным match
    clusters = pd.read_csv(DATA / "clusters.csv")
    zone = clusters[clusters["cluster_label"] == ZONE_LABEL].copy()
    zone["match"] = zone.apply(lambda r: match(r), axis=1)
    seeds = zone.sort_values("match", ascending=False).head(args.seeds)

    have = {t["videoId"] for t in json.loads((DATA / "tracks.json").read_text(encoding="utf-8"))["tracks"]}

    print("Сиды (эталоны твоей зоны):", file=sys.stderr)
    for _, s in seeds.iterrows():
        print(f"   · {s['artists']} — {s['title']}", file=sys.stderr)

    yt = YTMusic(str(AUTH))
    cands: dict[str, dict] = {}
    for _, s in seeds.iterrows():
        try:
            radio = yt.get_watch_playlist(videoId=s["videoId"], limit=25)
        except Exception as e:
            print(f"   радио не удалось: {e.__class__.__name__}", file=sys.stderr)
            continue
        for t in radio.get("tracks", []):
            vid = t.get("videoId")
            if not vid or vid in have or vid in cands:
                continue
            arts = ", ".join(a["name"] for a in (t.get("artists") or []) if a.get("name"))
            cands[vid] = {"videoId": vid, "artists": arts, "title": t.get("title")}

    cand_list = list(cands.values())[: args.cands]
    print(f"\nНовых кандидатов к анализу: {len(cand_list)}\n", file=sys.stderr)

    scored = []
    for i, c in enumerate(cand_list, 1):
        print(f"[{i}/{len(cand_list)}] ♪ {c['artists']} — {c['title']}", file=sys.stderr)
        clip = download_clip(c["videoId"])
        if clip is None:
            continue
        try:
            feat = analyze(clip)
        except Exception:
            continue
        scored.append({**c, **feat, "match": round(match(feat), 1)})

    scored.sort(key=lambda r: r["match"], reverse=True)
    out = DATA / "recommendations.csv"
    if scored:
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(scored[0].keys()))
            w.writeheader(); w.writerows(scored)

    print("\n" + "=" * 66)
    print("РЕКОМЕНДАЦИИ — ближе всего к «быстрому тёмному минору»:")
    print("=" * 66)
    for r in scored[:20]:
        print(f"  {r['match']:>5.1f}  {r['tempo']:>5.0f}bpm {r['key']:>2} {r['mode']:<5} "
              f"│ {r['artists']} — {r['title']}")
    print(f"\n✓ Полный список: {out}")


if __name__ == "__main__":
    main()
