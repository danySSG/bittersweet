"""
deep.py — тотальный анализ по data/features_full.csv.

Урок прошлого прогона: РАЗНЫМ вопросам — разные признаки.
  · НАСТРОЕНИЕ кластеризуем по фокусным «настроенческим» осям (иначе тембр размывает).
  · ЗВУЧАНИЕ  — по богатым тембровым осям.
  · UMAP-карта — по всему пространству (честная форма).

Пишет data/analysis.json (+ data/clusters_full.csv):
  UMAP-карта, кластеры-настроения (+ мягкая принадлежность и треки-мосты),
  сигнатуры, биттерсвит-категория, кластеры по звучанию, дрейф вкуса во времени.

Запуск:  uv run deep.py [--k N]
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
DATA = ROOT / "data"

# все числовые (для UMAP и per-track)
NUM = ["tempo", "minor_score", "bittersweet", "key_strength", "percussive", "energy_rms",
       "dynamics", "brightness", "rolloff", "bandwidth", "flatness", "zcr", "contrast",
       "onset_rate", "pulse_clarity", "mfcc1", "mfcc2", "mfcc3", "mfcc4"]
# фокусные оси НАСТРОЕНИЯ
MOODF = ["tempo", "minor_score", "bittersweet", "percussive", "brightness", "onset_rate", "energy_rms"]
# оси ЗВУЧАНИЯ / продакшна
TIMBRE = ["brightness", "rolloff", "bandwidth", "flatness", "zcr", "contrast",
          "dynamics", "mfcc1", "mfcc2", "mfcc3", "mfcc4"]
PALETTE = ["#7F77DD", "#5DCAA5", "#D85A30", "#D4537E", "#378ADD", "#EF9F27", "#9F7BEA", "#63992D"]


def pct(s):
    return s.rank(pct=True)


def choose_k(X, lo=4, hi=7):
    hi = min(hi, len(X) - 1)
    sc = {k: silhouette_score(X, KMeans(k, n_init=10, random_state=0).fit_predict(X)) for k in range(lo, hi + 1)}
    return max(sc, key=sc.get), sc


def mood_label(row, med):
    if row["minor_score"] >= 0.60:
        mood = "меланхоличный"
    elif row["minor_score"] <= 0.42:
        mood = "светлый"
    elif row["bittersweet"] >= 0.86:
        mood = "биттерсвит"
    else:
        mood = "смешанный"
    e = (row["tempo"] > med["tempo"]) + (row["percussive"] > med["percussive"]) + (row["onset_rate"] > med["onset_rate"])
    energy = "энергичный" if e >= 2 else ("спокойный" if e == 0 else "средний")
    timbre = "тёмный" if row["brightness"] < med["brightness"] else "яркий"
    return f"{energy} · {mood} · {timbre}"


def sound_label(row, med):
    a = "грязный/дисторшн" if row["flatness"] > med["flatness"] * 1.3 else "чистый"
    b = "тёмный" if row["brightness"] < med["brightness"] else "яркий"
    c = "дышит" if row["dynamics"] > med["dynamics"] else "плотный"
    return f"{a} · {b} · {c}"


def cluster(df, feats, k, med, labeler):
    """Возвращает (labels, {id: block})."""
    X = StandardScaler().fit_transform(df[feats].values)
    lab = KMeans(k, n_init=10, random_state=0).fit_predict(X)
    by_id = {}
    for c in range(k):
        idx = np.where(lab == c)[0]
        sub = df.iloc[idx]
        cen = X[idx].mean(axis=0)
        ex = sub.iloc[np.argsort(np.linalg.norm(X[idx] - cen, axis=1))[:4]]
        by_id[c] = {
            "id": int(c), "size": int(len(idx)),
            "label": labeler(sub[NUM].mean(), med),
            "minor_share": round(float((sub["mode"] == "minor").mean() * 100)),
            "medians": {f: round(float(sub[f].median()), 2)
                        for f in ["tempo", "minor_score", "bittersweet", "percussive", "brightness", "flatness"]},
            "examples": [f"{r['artists']} — {r['title']}" for _, r in ex.iterrows()],
        }
    return lab, by_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=None)
    args = ap.parse_args()

    df = pd.read_csv(DATA / "features_full.csv")
    df = df[df["status"] == "ok"].drop_duplicates("videoId").reset_index(drop=True)
    for f in NUM:
        df[f] = pd.to_numeric(df[f], errors="coerce")
    df = df.dropna(subset=NUM).reset_index(drop=True)
    med = df[NUM].median()
    print(f"треков: {len(df)}")

    # ---- НАСТРОЕНИЕ (фокусные оси) ----
    Xmood = StandardScaler().fit_transform(df[MOODF].values)
    if args.k:
        k = args.k
    else:
        k, sc = choose_k(Xmood)
        print("silhouette (mood):", {kk: round(v, 3) for kk, v in sc.items()})
    print(f"k = {k}")
    labels, mood_by_id = cluster(df, MOODF, k, med, mood_label)
    df["cluster"] = labels

    order_by_size = sorted(mood_by_id, key=lambda c: -mood_by_id[c]["size"])
    color_of = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(order_by_size)}
    for c in mood_by_id:
        mood_by_id[c]["color"] = color_of[c]
    df["cluster_label"] = df["cluster"].map({c: b["label"] for c, b in mood_by_id.items()})
    mood_blocks = [mood_by_id[c] for c in order_by_size]

    # ---- мягкая принадлежность + мосты (softmax по центроидам в mood-пространстве) ----
    cens = np.vstack([Xmood[labels == c].mean(axis=0) for c in range(k)])
    dist = np.linalg.norm(Xmood[:, None, :] - cens[None, :, :], axis=2)
    w = np.exp(-dist); w /= w.sum(axis=1, keepdims=True)
    o2 = np.argsort(-w, axis=1)
    lab_txt = {c: mood_by_id[c]["label"] for c in range(k)}
    df["mix1"] = [lab_txt[int(o2[i, 0])] for i in range(len(df))]
    df["mix2"] = [lab_txt[int(o2[i, 1])] for i in range(len(df))]
    df["mix2_w"] = [round(float(w[i, o2[i, 1]]), 2) for i in range(len(df))]
    br = df[(df["mix2_w"] >= 0.32) & (df["mix1"] != df["mix2"])].sort_values("mix2_w", ascending=False)
    bridges = [{"track": f"{r['artists']} — {r['title']}", "a": r["mix1"], "b": r["mix2"], "w": float(r["mix2_w"])}
               for _, r in br.head(8).iterrows()]

    # ---- UMAP (по всему пространству) ----
    import umap
    emb = umap.UMAP(n_neighbors=15, min_dist=0.12, random_state=42).fit_transform(
        StandardScaler().fit_transform(df[NUM].values))
    df["ux"], df["uy"] = emb[:, 0], emb[:, 1]

    # ---- биттерсвит ----
    bs = df[(df["bittersweet"] >= 0.86) & (df["brightness"] > med["brightness"])].sort_values("bittersweet", ascending=False)
    bittersweet = {"count": int(len(bs)), "share": round(len(bs) / len(df) * 100),
                   "top": [f"{r['artists']} — {r['title']}" for _, r in bs.head(8).iterrows()]}

    # ---- ЗВУЧАНИЕ ----
    _, sound_by_id = cluster(df, TIMBRE, 4, med, sound_label)
    sound_blocks = sorted(sound_by_id.values(), key=lambda b: -b["size"])

    # ---- дрейф вкуса ----
    order = {t["videoId"]: i for i, t in enumerate(json.loads((DATA / "tracks.json").read_text(encoding="utf-8"))["tracks"])}
    df["pos"] = df["videoId"].map(order)
    dd = df.dropna(subset=["pos"]).sort_values("pos")
    drift = None
    if len(dd) >= 60:
        n3 = len(dd) // 3
        parts = {"свежие": dd.iloc[:n3], "средние": dd.iloc[n3:2 * n3], "старые": dd.iloc[2 * n3:]}
        drift = {nm: {"tempo": round(float(g["tempo"].median())), "minor": round(float(g["minor_score"].mean()), 2),
                      "bittersweet": round(float(g["bittersweet"].mean()), 2),
                      "brightness": round(float(g["brightness"].mean())), "energy": round(float(g["energy_rms"].mean()), 3)}
                 for nm, g in parts.items()}

    points = [{"x": round(float(r["ux"]), 2), "y": round(float(r["uy"]), 2), "c": color_of[int(r["cluster"])],
               "l": f"{r['artists']} — {r['title']}",
               "m": f"{r['tempo']:.0f}bpm · {r['key']} {r['mode']} · bs{r['bittersweet']:.2f}"}
              for _, r in df.iterrows()]

    analysis = {"n": len(df), "k": k, "mood_clusters": mood_blocks, "bittersweet": bittersweet,
                "sound_clusters": sound_blocks, "bridges": bridges, "drift": drift, "points": points}
    (DATA / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2))
    df.to_csv(DATA / "clusters_full.csv", index=False)

    # ---- сводка ----
    print("\n=== НАСТРОЕНИЯ ===")
    for b in mood_blocks:
        print(f"● {b['label']}  [{b['size']}, {b['size']/len(df)*100:.0f}%]  "
              f"темп {b['medians']['tempo']:.0f} · минор {b['minor_share']}% · bs {b['medians']['bittersweet']}")
        print("    " + " · ".join(b["examples"][:3]))
    print(f"\n=== БИТТЕРСВИТ === {bittersweet['count']} ({bittersweet['share']}%): " + " · ".join(bittersweet["top"][:5]))
    print("\n=== МОСТЫ ===")
    for b in bridges[:5]:
        print(f"  {b['track']}  ({b['a']} ↔ {b['b']}, {int(b['w']*100)}%)")
    print("\n=== ЗВУЧАНИЕ ===")
    for b in sound_blocks:
        print(f"● {b['label']}  [{b['size']}]   " + " · ".join(b["examples"][:2]))
    if drift:
        print("\n=== ДРЕЙФ (свежие→старые) ===")
        for nm, d in drift.items():
            print(f"  {nm:<8} темп {d['tempo']} · минор {d['minor']} · bs {d['bittersweet']} · ярк {d['brightness']}")
    print("\n✓ data/analysis.json + data/clusters_full.csv")


if __name__ == "__main__":
    main()
