"""
essence.py — match v2: «насколько трек ушёл в СТОРОНУ настроения», а не «расстояние до центра».

Почему v2:
  Гауссова v1 штрафует за любое отклонение от центра — даже за то, что трек ТЕМНЕЕ
  и ПЛОТНЕЕ ядра (хотя это «ещё более твоё»). И жёстко зависит от темпа (октавные ошибки).

Как v2:
  Для зоны определяем НАПРАВЛЕНИЕ каждой оси (минор↑, темнее↑=яркость↓, плотнее↑…).
  Балл = насколько трек ушёл в это направление (tanh, с насыщением), с весами:
  «душа» зоны (лад, тембр, плотность) весит больше; темп — мало и через октаву (×1, ×2, ÷2).

Запуск:  uv run essence.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_audio import analyze

ROOT = Path(__file__).parent
DATA = ROOT / "data"
AUDIO = ROOT / "audio"
ZONE_LABEL = "средний · меланхоличный · тёмный"
FEATURES = ["tempo", "minor_score", "percussive", "energy_rms", "brightness", "onset_rate"]
# «душа» зоны весит больше; темп — капризный, вес маленький
WEIGHTS = {"minor_score": 1.6, "brightness": 1.3, "percussive": 1.1,
           "onset_rate": 1.1, "energy_rms": 0.7, "tempo": 0.6}

KAH = {"videoId": "GKpVPugt0IU", "artists": "егорbez", "title": "kah"}


def ensure_kah(df_features_path: Path) -> None:
    """Добавить трек-находку в features.csv, чтобы он участвовал дальше."""
    rows = list(csv.DictReader(df_features_path.open(encoding="utf-8")))
    if any(r["videoId"] == KAH["videoId"] for r in rows):
        return
    clip = AUDIO / f"{KAH['videoId']}.mp3"
    if not clip.exists():
        return
    feat = analyze(clip)
    row = {"videoId": KAH["videoId"], "artists": KAH["artists"], "title": KAH["title"],
           **feat, "status": "ok"}
    with df_features_path.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=rows[0].keys()).writerow(row)
    print(f"+ добавил в features.csv: {KAH['artists']} — {KAH['title']}")


def build_model(lib: pd.DataFrame):
    med, std = lib[FEATURES].median(), lib[FEATURES].std().replace(0, 1e-6)
    zone = lib[lib["cluster_label"] == ZONE_LABEL]
    direction = {f: float(np.sign(zone[f].mean() - med[f])) for f in FEATURES}
    return med, std, direction


def essence_score(feat, med, std, direction) -> float:
    total = wsum = 0.0
    for f in FEATURES:
        if f == "tempo":
            aligns = [direction[f] * ((c - med[f]) / std[f]) for c in (feat[f], feat[f] * 2, feat[f] / 2)]
            a = max(aligns)                      # выбираем октаву, лучше всего попадающую в «быстро»
        else:
            a = direction[f] * ((feat[f] - med[f]) / std[f])
        total += WEIGHTS[f] * np.tanh(a)          # +1 = сильно в сторону настроения, -1 = против
        wsum += WEIGHTS[f]
    return float(50 * (total / wsum + 1))         # 0..100


def main() -> None:
    ensure_kah(DATA / "features.csv")

    lib = pd.read_csv(DATA / "clusters.csv")
    med, std, direction = build_model(lib)
    print("направления «быстрого тёмного минора»:",
          {f: ("↑" if direction[f] > 0 else "↓") for f in FEATURES})

    def ess(feat):
        return essence_score(feat, med, std, direction)

    # v1 (гаусс) для сравнения
    sig = json.loads((DATA / "signature_fast_dark_minor.json").read_text(encoding="utf-8"))
    c, s = sig["center"], {k: max(v, 1e-6) for k, v in sig["spread"].items()}

    def v1(feat):
        z = np.array([(feat[f] - c[f]) / s[f] for f in FEATURES])
        return float(100 * np.exp(-0.5 * np.mean(z ** 2)))

    # трек-находка
    kah_feat = analyze(AUDIO / f"{KAH['videoId']}.mp3")
    print(f"\n▶ {KAH['artists']} — {KAH['title']}")
    print(f"    v1 (гаусс):  {v1(kah_feat):.1f}   →   v2 (essence):  {ess(kah_feat):.1f}")

    # пересортируем рекомендации по essence
    recs = pd.read_csv(DATA / "recommendations.csv")
    recs["essence"] = recs.apply(lambda r: ess(r), axis=1)
    top = recs.sort_values("essence", ascending=False).head(12)
    print("\nрекомендации, пересчитанные по essence:")
    for _, r in top.iterrows():
        print(f"  {r['essence']:>5.1f}  {r['tempo']:>5.0f}bpm {r['key']:>2} {r['mode']:<5} │ {r['artists']} — {r['title']}")

    # сохранить модель
    (DATA / "essence_model.json").write_text(json.dumps(
        {"label": "fast_dark_minor", "median": {f: round(float(med[f]), 3) for f in FEATURES},
         "std": {f: round(float(std[f]), 3) for f in FEATURES},
         "direction": direction, "weights": WEIGHTS}, ensure_ascii=False, indent=2))
    print(f"\n✓ модель essence сохранена: {DATA/'essence_model.json'}")


if __name__ == "__main__":
    main()
