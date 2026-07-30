"""
scatter_artifact.py — самодостаточная HTML-карта 375 треков, раскрашенных по кластерам.
Content-only (без doctype/html/head/body) — под рендер артефактом.

Запуск:  uv run scatter_artifact.py <output.html>
"""

import json
import sys
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"

# метка кластера (energy·mood·timbre из cluster.py) -> человеческое имя + цвет + порядок
NAME_MAP = {
    "средний · меланхоличный · тёмный":  ("быстрый тёмный минор · дарквейв, фонк", "#7F77DD", True),
    "средний · меланхоличный · яркий":   ("дрим-поп · witch-синти, мечтательное", "#D4537E", False),
    "средний · смешанный · яркий":       ("грувовое ритмичное · фанк, lo-fi", "#5DCAA5", False),
    "спокойный · меланхоличный · тёмный": ("спокойное меланхоличное · атмосфера", "#EF9F27", False),
    "средний · смешанный · тёмный":      ("тихое пиано · эмбиент, без ритма", "#378ADD", False),
    "средний · светлый · яркий":         ("светлое мажорное · инди, поп", "#D85A30", False),
}
FALLBACK = ("прочее", "#888780", False)


def main() -> None:
    out = Path(sys.argv[1])
    df = pd.read_csv(DATA / "clusters.csv")

    def info(lbl):
        return NAME_MAP.get(lbl, FALLBACK)

    points = [{
        "x": round(float(r["valence"]), 1), "y": round(float(r["energy"]), 1),
        "c": info(r["cluster_label"])[1],
        "l": f"{r['artists']} — {r['title']}",
        "m": f"{r['tempo']:.0f} BPM · {r['key']} {r['mode']}",
    } for _, r in df.iterrows()]

    sizes = df["cluster_label"].value_counts().to_dict()
    seen, legend = set(), []
    # порядок легенды: твоя зона первой, дальше по размеру
    for lbl in sorted(sizes, key=lambda k: (not info(k)[2], -sizes[k])):
        name, color, you = info(lbl)
        if name in seen:
            continue
        seen.add(name)
        legend.append({"name": name, "c": color, "size": int(sizes[lbl]), "you": you})

    html = _TPL.replace("__PTS__", json.dumps(points, ensure_ascii=False)) \
               .replace("__LEG__", json.dumps(legend, ensure_ascii=False)) \
               .replace("__N__", str(len(df)))
    out.write_text(html, encoding="utf-8")
    print(f"✓ {out}  ({out.stat().st_size//1024} KB, {len(df)} точек)")


_TPL = """<title>Карта вкуса — 375 треков</title>
<style>
 :root{--bg:#0b0b10;--ink:#ececf4;--muted:#8a8aa0;--line:#20202e;
   --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
   --mono:ui-monospace,"SF Mono",Menlo,monospace}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.6}
 .wrap{max-width:1000px;margin:0 auto;padding:48px 24px 72px}
 .eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.18em;text-transform:uppercase;
   color:var(--muted);margin:0 0 12px}
 h1{font-size:clamp(26px,4.5vw,36px);margin:0 0 12px;font-weight:600;letter-spacing:-.01em}
 .intro{color:var(--muted);max-width:62ch;margin:0 0 32px}
 .grid{display:grid;grid-template-columns:1fr 300px;gap:28px}
 @media(max-width:780px){.grid{grid-template-columns:1fr}}
 .plot{position:relative;aspect-ratio:1;border:1px solid var(--line);border-radius:14px;overflow:hidden;
   background:linear-gradient(105deg,rgba(127,119,221,.10),transparent 55%)}
 .zone{position:absolute;left:0;top:0;width:45%;height:45%;
   background:radial-gradient(circle at top left,rgba(127,119,221,.16),transparent 72%);
   border-right:1px dashed #33294f;border-bottom:1px dashed #33294f}
 .ax{position:absolute;color:var(--muted);font-size:11px;font-family:var(--mono);letter-spacing:.04em}
 .ax.tl{top:10px;left:12px}.ax.bl{bottom:8px;left:12px}.ax.br{bottom:8px;right:12px}
 .dot{position:absolute;width:8px;height:8px;border-radius:50%;transform:translate(-50%,50%);
   cursor:pointer;mix-blend-mode:screen;opacity:.82}
 .dot:hover{outline:2px solid #fff;opacity:1}
 .legend h2{font-size:13px;font-family:var(--mono);letter-spacing:.1em;text-transform:uppercase;
   color:var(--muted);margin:0 0 14px;font-weight:500}
 .li{display:flex;gap:9px;align-items:flex-start;margin:0 0 13px}
 .sw{width:11px;height:11px;border-radius:3px;flex:none;margin-top:4px}
 .li .nm{font-size:13.5px;line-height:1.35}
 .li .n{color:var(--muted);font-family:var(--mono);font-size:11.5px}
 .li .star{color:#b9a8ff}
 #tip{position:fixed;pointer-events:none;background:#16161f;border:1px solid #2c2c40;border-radius:8px;
   padding:7px 10px;font-size:12.5px;opacity:0;transition:.08s;max-width:250px;z-index:9}
 #tip b{color:#fff}#tip span{color:#9a9ab5;font-family:var(--mono);font-size:11px}
</style>
<div class="wrap">
 <p class="eyebrow">ночной разбор вкуса · 6 настроений</p>
 <h1>Карта твоего вкуса</h1>
 <p class="intro">__N__ песен из лайков. Каждая точка — трек; цвет — настроение (кластер).
 По горизонтали настроение (левее — меланхоличнее), по вертикали энергия.
 Подсвечен угол драйва и грусти — твоя зона.</p>
 <div class="grid">
  <div class="plot" id="plot">
   <div class="zone"></div>
   <div class="ax tl">↑ энергичнее</div>
   <div class="ax bl">← меланхоличнее</div>
   <div class="ax br">радостнее →</div>
  </div>
  <div class="legend" id="legend"><h2>настроения</h2></div>
 </div>
</div>
<div id="tip"></div>
<script>
const P=__PTS__,L=__LEG__,plot=document.getElementById('plot'),tip=document.getElementById('tip');
function draw(){const w=plot.clientWidth,h=plot.clientHeight;
 plot.querySelectorAll('.dot').forEach(d=>d.remove());
 for(const p of P){const d=document.createElement('div');d.className='dot';
  d.style.left=(p.x/100*w)+'px';d.style.bottom=(p.y/100*h)+'px';d.style.background=p.c;
  d.addEventListener('mousemove',e=>{tip.style.opacity=1;tip.style.left=(e.clientX+14)+'px';
   tip.style.top=(e.clientY+14)+'px';tip.innerHTML='<b>'+p.l+'</b><br><span>'+p.m+'</span>';});
  d.addEventListener('mouseleave',()=>tip.style.opacity=0);plot.appendChild(d);}}
const lg=document.getElementById('legend');
for(const l of L){const row=document.createElement('div');row.className='li';
 row.innerHTML='<span class="sw" style="background:'+l.c+'"></span><div>'+
  '<div class="nm'+(l.you?' star':'')+'">'+l.name+(l.you?' ★':'')+'</div>'+
  '<div class="n">'+l.size+' треков</div></div>';lg.appendChild(row);}
draw();addEventListener('resize',draw);
</script>"""


if __name__ == "__main__":
    main()
