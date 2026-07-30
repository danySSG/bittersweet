"""
cluster.py — найти "музыкальные настроения" в библиотеке (кластеризация).

Разнородные лайки не стоит усреднять в одну точку. Вместо этого разбиваем треки
на группы по звучанию (KMeans), описываем каждую человеческим языком и смотрим,
какого размера твой энергично-меланхоличный кластер относительно остальных.

Читает:  data/features.csv
Пишет:   data/clusters.csv   (трек → кластер + метка)
         data/map.html       (карта, точки раскрашены по кластерам)
Печатает разбор кластеров в консоль.

Запуск:  uv run cluster.py            # авто-выбор числа кластеров
         uv run cluster.py --k 5      # задать вручную
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).parent
DATA = ROOT / "data"

FEATURES = ["tempo", "minor_score", "percussive", "energy_rms", "brightness", "onset_rate"]

# палитра для кластеров (бренд-рампы, средние стопы)
PALETTE = ["#7F77DD", "#1D9E75", "#D85A30", "#D4537E", "#378ADD", "#BA7517", "#639922", "#888780"]


def pct(s: pd.Series) -> pd.Series:
    return s.rank(pct=True)


def choose_k(X: np.ndarray, kmin=3, kmax=7) -> tuple[int, dict]:
    scores = {}
    kmax = min(kmax, len(X) - 1)
    for k in range(kmin, kmax + 1):
        km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(X)
        scores[k] = silhouette_score(X, km.labels_)
    best = max(scores, key=scores.get)
    return best, scores


def label_cluster(row: pd.Series, med: pd.Series) -> str:
    """Человеческая метка кластера из его средних значений относительно медиан библиотеки."""
    # настроение
    if row["minor_score"] >= 0.55:
        mood = "меланхоличный"
    elif row["minor_score"] <= 0.45:
        mood = "светлый"
    else:
        mood = "смешанный"
    # энергия: композит темпа/ударности/дробности
    e = (row["tempo"] > med["tempo"]) + (row["percussive"] > med["percussive"]) + (row["onset_rate"] > med["onset_rate"])
    energy = "энергичный" if e >= 2 else ("спокойный" if e == 0 else "средний")
    # тембр
    timbre = "тёмный" if row["brightness"] < med["brightness"] else "яркий"
    return f"{energy} · {mood} · {timbre}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=None)
    args = ap.parse_args()

    df = pd.read_csv(DATA / "features.csv")
    df = df[df["status"] == "ok"].drop_duplicates("videoId").reset_index(drop=True)
    if len(df) < 8:
        raise SystemExit(f"Слишком мало треков ({len(df)}) для кластеризации. Дождись прогона.")

    X = StandardScaler().fit_transform(df[FEATURES].values)

    if args.k:
        k, scores = args.k, {}
    else:
        k, scores = choose_k(X)
        print("silhouette по k:", {kk: round(v, 3) for kk, v in scores.items()})
    print(f"Кластеров: {k}  ({len(df)} треков)\n")

    km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(X)
    df["cluster"] = km.labels_

    # индексы для карты
    df["energy"] = 100 * (pct(df["tempo"]) + pct(df["energy_rms"]) + pct(df["percussive"]) + pct(df["onset_rate"])) / 4
    df["valence"] = 100 * (pct(1 - df["minor_score"]) + pct(df["brightness"])) / 2
    df["signature"] = (df["energy"] + (100 - df["valence"])) / 2

    med = df[FEATURES].median()
    summary = df.groupby("cluster").agg(
        size=("videoId", "size"),
        tempo=("tempo", "median"),
        minor_score=("minor_score", "mean"),
        percussive=("percussive", "mean"),
        brightness=("brightness", "mean"),
        onset_rate=("onset_rate", "mean"),
        energy=("energy", "mean"),
        valence=("valence", "mean"),
        signature=("signature", "mean"),
    )
    summary["minor_share"] = df.groupby("cluster")["mode"].apply(lambda s: (s == "minor").mean() * 100)
    summary["label"] = summary.apply(lambda r: label_cluster(r, med), axis=1)
    summary = summary.sort_values("signature", ascending=False)

    # твой кластер = максимальный signature (энергия + грусть)
    your = summary.index[0]

    print("=" * 70)
    print("ТВОИ МУЗЫКАЛЬНЫЕ НАСТРОЕНИЯ")
    print("=" * 70)
    for cid, r in summary.iterrows():
        share = r["size"] / len(df) * 100
        star = "  ← ТВОЯ ЗОНА" if cid == your else ""
        print(f"\n● {r['label']}   [{r['size']} треков, {share:.0f}%]{star}")
        print(f"    темп {r['tempo']:.0f} BPM · минор {r['minor_share']:.0f}% · "
              f"ударность {r['percussive']:.2f} · яркость {r['brightness']:.0f}Гц")
        examples = df[df["cluster"] == cid].sort_values("signature", ascending=False).head(3)
        for _, e in examples.iterrows():
            print(f"      · {e['artists']} — {e['title']}")

    df["cluster_label"] = df["cluster"].map(summary["label"])
    df.sort_values(["cluster", "signature"], ascending=[True, False]).to_csv(DATA / "clusters.csv", index=False)

    write_map(df, summary, your)
    print(f"\n✓ Карта: {DATA/'map.html'}")
    print(f"✓ Таблица: {DATA/'clusters.csv'}")

    yr = summary.loc[your]
    print("\n" + "-" * 70)
    print(f"Твой энергично-меланхоличный кластер — {yr['size']} из {len(df)} треков "
          f"({yr['size']/len(df)*100:.0f}% библиотеки).")


def write_map(df: pd.DataFrame, summary: pd.DataFrame, your: int) -> None:
    color_of = {cid: PALETTE[i % len(PALETTE)] for i, cid in enumerate(summary.index)}
    points = [{
        "x": float(r["valence"]), "y": float(r["energy"]),
        "c": color_of[r["cluster"]],
        "label": f"{r['artists']} — {r['title']}",
        "meta": f"{r['tempo']:.0f} BPM · {r['key']} {r['mode']}",
    } for _, r in df.iterrows()]
    legend = [{
        "c": color_of[cid], "label": r["label"],
        "size": int(r["size"]), "you": bool(cid == your),
    } for cid, r in summary.iterrows()]
    html = (_MAP.replace("__POINTS__", json.dumps(points, ensure_ascii=False))
                .replace("__LEGEND__", json.dumps(legend, ensure_ascii=False)))
    (DATA / "map.html").write_text(html, encoding="utf-8")


_MAP = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Карта вкуса</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;background:#0d0d12;color:#e8e8f0;font:15px/1.5 -apple-system,system-ui,sans-serif}
 .wrap{max-width:1000px;margin:0 auto;padding:24px;display:grid;grid-template-columns:1fr 260px;gap:24px}
 @media(max-width:760px){.wrap{grid-template-columns:1fr}}
 h1{font-weight:600;font-size:20px;margin:0 0 4px;grid-column:1/-1}
 p.sub{color:#8a8aa0;margin:0 0 8px;grid-column:1/-1}
 .plot{position:relative;aspect-ratio:1;background:linear-gradient(90deg,#1a1230,#0d0d12 60%);
   border:1px solid #23233a;border-radius:14px;overflow:hidden}
 .zone{position:absolute;left:0;top:0;width:45%;height:45%;
   background:radial-gradient(circle at top left,rgba(190,90,255,.18),transparent 70%);
   border-right:1px dashed #4a2a6a;border-bottom:1px dashed #4a2a6a}
 .axis{position:absolute;color:#6a6a85;font-size:12px}
 .ax-x{bottom:8px;right:12px}.ax-x2{bottom:8px;left:12px}.ax-y{top:10px;left:12px}.ax-y2{bottom:26px;left:12px}
 .dot{position:absolute;width:9px;height:9px;border-radius:50%;transform:translate(-50%,50%);
   cursor:pointer;mix-blend-mode:screen;opacity:.85}
 .dot:hover{outline:2px solid #fff;opacity:1}
 .legend{font-size:13px}.legend h2{font-size:14px;margin:0 0 10px;color:#c9c9de}
 .li{display:flex;gap:8px;align-items:flex-start;margin:0 0 10px}
 .sw{width:11px;height:11px;border-radius:3px;flex:none;margin-top:3px}
 .li .n{color:#8a8aa0;font-size:12px}
 .you{color:#c79bff}
 #tip{position:fixed;pointer-events:none;background:#1b1b28;border:1px solid #35354f;border-radius:8px;
   padding:8px 10px;font-size:13px;opacity:0;transition:.1s;max-width:260px}
 #tip b{color:#fff}#tip span{color:#9a9ab5}
</style></head><body><div class="wrap">
<h1>Карта твоего вкуса</h1>
<p class="sub">Каждая точка — трек, цвет — кластер (настроение). Левее-меланхоличнее, выше-энергичнее.
Подсвечен твой угол: драйв + грусть.</p>
<div class="plot" id="plot">
 <div class="zone"></div>
 <div class="axis ax-y">↑ энергичнее</div><div class="axis ax-y2">спокойнее</div>
 <div class="axis ax-x">радостнее →</div><div class="axis ax-x2">← меланхоличнее</div>
</div>
<div class="legend" id="legend"><h2>настроения</h2></div>
</div><div id="tip"></div>
<script>
const pts=__POINTS__,leg=__LEGEND__;
const plot=document.getElementById('plot'),tip=document.getElementById('tip');
function draw(){const w=plot.clientWidth,h=plot.clientHeight;
 plot.querySelectorAll('.dot').forEach(d=>d.remove());
 for(const p of pts){const d=document.createElement('div');d.className='dot';
  d.style.left=(p.x/100*w)+'px';d.style.bottom=(p.y/100*h)+'px';d.style.background=p.c;
  d.addEventListener('mousemove',e=>{tip.style.opacity=1;tip.style.left=(e.clientX+14)+'px';
   tip.style.top=(e.clientY+14)+'px';tip.innerHTML=`<b>${p.label}</b><br><span>${p.meta}</span>`;});
  d.addEventListener('mouseleave',()=>tip.style.opacity=0);plot.appendChild(d);}}
const lg=document.getElementById('legend');
for(const l of leg){const row=document.createElement('div');row.className='li';
 row.innerHTML=`<span class="sw" style="background:${l.c}"></span><div><div class="${l.you?'you':''}">`+
  `${l.label}${l.you?' ★':''}</div><div class="n">${l.size} треков</div></div>`;lg.appendChild(row);}
draw();addEventListener('resize',draw);
</script></body></html>"""


if __name__ == "__main__":
    main()
