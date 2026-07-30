"""
build_map.py — превратить признаки треков в "карту вкуса" и отпечаток.

Идея: у каждого признака смотрим не абсолютное значение, а ПЕРЦЕНТИЛЬ внутри
ТВОЕЙ библиотеки ("этот трек энергичнее 80% твоих"). Так карта показывает
структуру именно твоего вкуса, а не абстрактную норму.

Две оси:
  energy    (0..100) — темп + громкость + ударность + плотность атак
  valence   (0..100) — "настроение": мажорность + яркость тембра
                       низкий valence = меланхолия

Твоя зона — правый нижний угол: высокая energy + низкий valence
("грустный бэнгер"). Скрипт считает для каждого трека signature-score
(насколько он воплощает этот угол) и печатает топ.

Выход:
  data/taste.csv          — треки с индексами energy/valence/signature
  data/map.html           — интерактивная карта (открыть в браузере)
  печатает отпечаток библиотеки в консоль

Запуск:  uv run build_map.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"


def pct(series: pd.Series) -> pd.Series:
    """Перцентиль-ранг 0..1 (устойчив к выбросам и малой выборке)."""
    return series.rank(pct=True)


def main() -> None:
    fpath = DATA / "features.csv"
    if not fpath.exists():
        raise SystemExit("Нет data/features.csv — сначала: uv run analyze_audio.py")

    df = pd.read_csv(fpath)
    df = df[df["status"] == "ok"].copy()
    if len(df) < 3:
        print(f"⚠ Пока всего {len(df)} проанализированных треков — карта будет условной. "
              "Прогони analyze_audio.py на большем числе.")

    majorness = 1 - df["minor_score"]  # выше = мажорнее = "радостнее"

    # energy: темп + громкость + ударность + плотность атак
    df["energy"] = 100 * (pct(df["tempo"]) + pct(df["energy_rms"])
                          + pct(df["percussive"]) + pct(df["onset_rate"])) / 4
    # valence: мажорность + яркость тембра (низкий = меланхолия)
    df["valence"] = 100 * (pct(majorness) + pct(df["brightness"])) / 2
    # signature: воплощение "энергично + грустно" = высокий energy + низкий valence
    df["signature"] = (df["energy"] + (100 - df["valence"])) / 2

    df = df.round({"energy": 1, "valence": 1, "signature": 1})
    df_sorted = df.sort_values("signature", ascending=False)
    df_sorted.to_csv(DATA / "taste.csv", index=False)

    # ---- текстовый отпечаток --------------------------------------------------
    n = len(df)
    minor_share = (df["mode"] == "minor").mean() * 100
    print("\n" + "=" * 56)
    print(f"ОТПЕЧАТОК ТВОЕЙ БИБЛИОТЕКИ  ({n} треков)")
    print("=" * 56)
    print(f"  Темп (медиана):       {df['tempo'].median():.0f} BPM"
          f"   [диапазон {df['tempo'].quantile(.25):.0f}–{df['tempo'].quantile(.75):.0f}]")
    print(f"  Доля минорных:        {minor_share:.0f}%")
    print(f"  Средняя ударность:    {df['percussive'].mean():.2f}   (доля ритма в звуке)")
    print(f"  Средняя яркость:      {df['brightness'].mean():.0f} Гц")
    top_keys = df.groupby(["key", "mode"]).size().sort_values(ascending=False).head(3)
    print("  Частые тональности:   " + ", ".join(f"{k} {m}" for (k, m) in top_keys.index))

    print("\n  ТОП-10 «энергично + меланхолично» (сердце твоего паттерна):")
    for _, r in df_sorted.head(10).iterrows():
        print(f"    {r['signature']:>5.1f}  {r['tempo']:>5.0f}bpm {r['key']:>2} {r['mode']:<5} "
              f"│ {r['artists']} — {r['title']}")

    # ---- интерактивная карта (самодостаточный HTML, без внешних CDN) ----------
    write_map_html(df)
    print(f"\n✓ Карта: {DATA / 'map.html'}   (открой в браузере)")
    print(f"✓ Таблица: {DATA / 'taste.csv'}")


def write_map_html(df: pd.DataFrame) -> None:
    points = [
        {
            "x": float(r["valence"]), "y": float(r["energy"]),
            "sig": float(r["signature"]),
            "label": f"{r['artists']} — {r['title']}",
            "meta": f"{r['tempo']:.0f} BPM · {r['key']} {r['mode']}",
        }
        for _, r in df.iterrows()
    ]
    html = _MAP_TEMPLATE.replace("__POINTS__", json.dumps(points, ensure_ascii=False))
    (DATA / "map.html").write_text(html, encoding="utf-8")


_MAP_TEMPLATE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Карта вкуса</title>
<style>
  :root{color-scheme:dark}
  body{margin:0;background:#0d0d12;color:#e8e8f0;font:15px/1.5 -apple-system,system-ui,sans-serif}
  .wrap{max-width:900px;margin:0 auto;padding:24px}
  h1{font-weight:600;font-size:20px;margin:0 0 4px}
  p.sub{color:#8a8aa0;margin:0 0 20px}
  .plot{position:relative;aspect-ratio:1;background:
     linear-gradient(90deg,#1a1230,#0d0d12 60%);
     border:1px solid #23233a;border-radius:14px;overflow:hidden}
  .axis{position:absolute;color:#6a6a85;font-size:12px}
  .ax-x{bottom:8px;right:12px}.ax-x2{bottom:8px;left:12px}
  .ax-y{top:10px;left:12px}.ax-y2{bottom:26px;left:12px}
  .zone{position:absolute;left:0;top:0;width:45%;height:45%;
     background:radial-gradient(circle at top left,rgba(190,90,255,.22),transparent 70%);
     border-right:1px dashed #4a2a6a;border-bottom:1px dashed #4a2a6a}
  .zlabel{position:absolute;left:12px;top:10px;color:#c79bff;font-size:12px;text-align:left}
  .dot{position:absolute;border-radius:50%;transform:translate(-50%,50%);cursor:pointer;
     transition:.15s;mix-blend-mode:screen}
  .dot:hover{outline:2px solid #fff}
  #tip{position:fixed;pointer-events:none;background:#1b1b28;border:1px solid #35354f;
     border-radius:8px;padding:8px 10px;font-size:13px;opacity:0;transition:.1s;max-width:260px}
  #tip b{color:#fff}#tip span{color:#9a9ab5}
</style></head><body><div class="wrap">
<h1>Карта твоего вкуса</h1>
<p class="sub">Каждая точка — трек. Правее — «радостнее», выше — энергичнее.
Твоя зона подсвечена: <b style="color:#c79bff">энергично + меланхолично</b>. Наведи на точку.</p>
<div class="plot" id="plot">
  <div class="zone"></div><div class="zlabel">твой угол:<br>драйв + грусть</div>
  <div class="axis ax-y">↑ энергичнее</div>
  <div class="axis ax-y2">спокойнее</div>
  <div class="axis ax-x">радостнее →</div>
  <div class="axis ax-x2">← меланхоличнее</div>
</div>
</div><div id="tip"></div>
<script>
const pts=__POINTS__;const plot=document.getElementById('plot');const tip=document.getElementById('tip');
function draw(){const w=plot.clientWidth,h=plot.clientHeight;
 for(const p of pts){const d=document.createElement('div');d.className='dot';
  const s=6+p.sig/100*16;d.style.width=d.style.height=s+'px';
  d.style.left=(p.x/100*w)+'px';d.style.bottom=(p.y/100*h)+'px';
  const hue=280-(p.x/100*80);d.style.background=`hsla(${hue},80%,60%,.85)`;
  d.addEventListener('mousemove',e=>{tip.style.opacity=1;tip.style.left=(e.clientX+14)+'px';
    tip.style.top=(e.clientY+14)+'px';tip.innerHTML=`<b>${p.label}</b><br><span>${p.meta} · signature ${p.sig.toFixed(0)}</span>`;});
  d.addEventListener('mouseleave',()=>tip.style.opacity=0);plot.appendChild(d);}}
draw();window.addEventListener('resize',()=>{plot.querySelectorAll('.dot').forEach(d=>d.remove());draw();});
</script></body></html>"""


if __name__ == "__main__":
    main()
