"""
fingerprint.py — превратить кластер «быстрый тёмный минор» в формулу.

Берёт реальные 67 треков этого кластера, считает их центр и разброс по каждому
признаку, и定ляет функцию match_score(track) — насколько ЛЮБОЙ трек попадает
в эту зону (0..100). Это и есть «код» твоего настроения.

Запуск:  uv run fingerprint.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"
ZONE_LABEL = "средний · меланхоличный · тёмный"
FEATURES = ["tempo", "minor_score", "percussive", "energy_rms", "brightness", "onset_rate"]


def main() -> None:
    df = pd.read_csv(DATA / "clusters.csv")
    zone = df[df["cluster_label"] == ZONE_LABEL]

    mu = zone[FEATURES].mean()
    sigma = zone[FEATURES].std().replace(0, 1e-6)

    # ---- СИГНАТУРА: центр ± разброс каждого признака ----
    print(f"СИГНАТУРА «быстрый тёмный минор» ({len(zone)} треков)\n")
    print(f"{'признак':<13}{'центр':>9}{'типичный диапазон (±1σ)':>28}")
    for f in FEATURES:
        lo, hi = mu[f] - sigma[f], mu[f] + sigma[f]
        print(f"{f:<13}{mu[f]:>9.2f}   {lo:>8.2f} … {hi:<8.2f}")

    keys = zone.groupby(["key", "mode"]).size().sort_values(ascending=False).head(5)
    print("\nчастые тональности:", ", ".join(f"{k} {m} ({n})" for (k, m), n in keys.items()))

    # ---- ФОРМУЛА принадлежности: гауссова близость к центру ----
    def match_score(row) -> float:
        z = np.array([(row[f] - mu[f]) / sigma[f] for f in FEATURES])
        return float(100 * np.exp(-0.5 * np.mean(z ** 2)))

    df = df.copy()
    df["match"] = df.apply(match_score, axis=1)

    # проверка: топ по формуле должен совпадать с самим кластером
    top = df.sort_values("match", ascending=False).head(15)
    in_zone_top50 = (df.sort_values("match", ascending=False).head(50)["cluster_label"] == ZONE_LABEL).mean()
    print(f"\nточность: из топ-50 по формуле — {in_zone_top50*100:.0f}% реально из этого кластера\n")
    print("ТОП-15 «самых-твоих» по формуле:")
    for _, r in top.iterrows():
        print(f"  {r['match']:>5.1f}  {r['tempo']:>5.0f}bpm {r['key']:>2} {r['mode']:<5} │ {r['artists']} — {r['title']}")

    # ---- сохранить сигнатуру как переносимый JSON ----
    sig = {"label": "fast_dark_minor",
           "center": {f: round(float(mu[f]), 3) for f in FEATURES},
           "spread": {f: round(float(sigma[f]), 3) for f in FEATURES}}
    (DATA / "signature_fast_dark_minor.json").write_text(json.dumps(sig, ensure_ascii=False, indent=2))
    print(f"\n✓ сигнатура сохранена: {DATA/'signature_fast_dark_minor.json'}")


if __name__ == "__main__":
    main()
