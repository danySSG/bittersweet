"""
dashboard.py — собрать гранд-дашборд «Портрет вкуса» из data/analysis.json.
Content-only HTML (под артефакт). Запуск: uv run dashboard.py <out.html>
"""

import json
import sys
from pathlib import Path

DATA = Path(__file__).parent / "data"


def main():
    out = Path(sys.argv[1])
    A = json.loads((DATA / "analysis.json").read_text(encoding="utf-8"))

    # нормализуем UMAP-координаты в 0..100
    xs = [p["x"] for p in A["points"]]; ys = [p["y"] for p in A["points"]]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    def nx(v): return round((v - x0) / (x1 - x0 + 1e-9) * 96 + 2, 2)
    def ny(v): return round((v - y0) / (y1 - y0 + 1e-9) * 96 + 2, 2)
    pts = [{"x": nx(p["x"]), "y": ny(p["y"]), "c": p["c"], "l": p["l"], "m": p["m"]} for p in A["points"]]

    # карточки настроений
    mood_cards = "\n".join(f'''<div class="card">
      <div class="chip"><span class="sw" style="background:{b['color']}"></span>{b['label']}</div>
      <div class="pctbar"><i style="width:{b['size']/A['n']*100:.0f}%;background:{b['color']}"></i></div>
      <div class="meta">{b['size']} треков · {b['size']/A['n']*100:.0f}% · темп {b['medians']['tempo']:.0f} · минор {b['minor_share']}% · bs {b['medians']['bittersweet']}</div>
      <div class="ex">{' · '.join(b['examples'][:3])}</div>
    </div>''' for b in A["mood_clusters"])

    sound_cards = "\n".join(f'''<div class="scard">
      <div class="sname">{b['label']}</div><div class="ssize">{b['size']} треков</div>
      <div class="ex">{' · '.join(b['examples'][:2])}</div></div>''' for b in A["sound_clusters"])

    bs = A["bittersweet"]
    bs_top = "".join(f"<li>{t}</li>" for t in bs["top"][:8])

    bridges = "".join(f'<li><b>{b["track"]}</b><span class="br">{b["a"]} ↔ {b["b"]} · {int(b["w"]*100)}%</span></li>'
                      for b in A["bridges"][:6])

    drift_html = ""
    if A["drift"]:
        d = A["drift"]
        rows = "".join(f'''<tr><td class="per">{nm}</td><td>{v['tempo']}</td><td>{v['minor']}</td>
          <td>{v['bittersweet']}</td><td>{v['brightness']}</td></tr>''' for nm, v in d.items())
        drift_html = f'''<section><p class="eyebrow" style="color:#EF9F27">06 · дрейф во времени</p>
          <h2>как менялся вкус</h2>
          <p class="lead">лайки от свежих к старым, поделены на три периода. видно небольшую дугу.</p>
          <table class="drift"><thead><tr><th>период</th><th>темп</th><th>минор</th><th>bs</th><th>яркость</th></tr></thead>
          <tbody>{rows}</tbody></table></section>'''

    html = _TPL
    for key, val in {
        "__N__": str(A["n"]), "__K__": str(A["k"]),
        "__PTS__": json.dumps(pts, ensure_ascii=False),
        "__LEGEND__": json.dumps([{"c": b["color"], "t": b["label"], "s": b["size"]} for b in A["mood_clusters"]], ensure_ascii=False),
        "__MOODS__": mood_cards, "__SOUND__": sound_cards,
        "__BSCOUNT__": str(bs["count"]), "__BSSHARE__": str(bs["share"]), "__BSTOP__": bs_top,
        "__BRIDGES__": bridges, "__DRIFT__": drift_html,
    }.items():
        html = html.replace(key, val)
    out.write_text(html, encoding="utf-8")
    print(f"✓ {out} ({out.stat().st_size//1024} KB)")


_TPL = """<title>Портрет вкуса</title>
<style>
 :root{--bg:#0b0b10;--ink:#ececf4;--muted:#8a8aa0;--line:#1f1f2c;--purple:#7F77DD;
   --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
   --mono:ui-monospace,"SF Mono",Menlo,monospace}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.6}
 .wrap{max-width:940px;margin:0 auto;padding:52px 22px 90px}
 .eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:0 0 12px}
 h1{font-size:clamp(30px,5.5vw,46px);margin:0 0 10px;font-weight:600;letter-spacing:-.02em}
 h2{font-size:21px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
 .hero-sub{color:var(--muted);font-size:18px;max-width:60ch;margin:0 0 6px}
 section{margin:52px 0 0;border-top:1px solid var(--line);padding-top:30px}
 section.hero{border:0;padding:0;margin:0}
 .lead{color:var(--muted);max-width:64ch;margin:0 0 22px}
 /* map */
 .maprow{display:grid;grid-template-columns:1fr 250px;gap:24px}
 @media(max-width:760px){.maprow{grid-template-columns:1fr}}
 .plot{position:relative;aspect-ratio:1.15;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#0d0d13}
 .dot{position:absolute;width:8px;height:8px;border-radius:50%;transform:translate(-50%,50%);cursor:pointer;mix-blend-mode:screen;opacity:.8}
 .dot:hover{outline:2px solid #fff;opacity:1}
 .leg h3{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:0 0 12px;font-weight:500}
 .li{display:flex;gap:8px;align-items:flex-start;margin:0 0 11px;font-size:13px;line-height:1.35}
 .sw{width:11px;height:11px;border-radius:3px;flex:none;margin-top:3px}
 .li .n{color:var(--muted);font-family:var(--mono);font-size:11px}
 /* mood cards */
 .cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}
 @media(max-width:640px){.cards{grid-template-columns:1fr}}
 .card{border:1px solid var(--line);border-radius:12px;padding:15px}
 .chip{display:flex;gap:8px;align-items:center;font-size:14.5px;font-weight:500;margin:0 0 9px}
 .pctbar{height:6px;background:#181824;border-radius:4px;overflow:hidden;margin:0 0 9px}
 .pctbar i{display:block;height:100%;border-radius:4px}
 .meta{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin:0 0 7px;letter-spacing:.02em}
 .ex{font-size:12.5px;color:#b7b7c8}
 /* bittersweet spotlight */
 .spot{border:1px solid #3a2f14;background:linear-gradient(120deg,rgba(239,159,39,.08),transparent 60%);border-radius:14px;padding:22px}
 .spot h2{color:#EF9F27}
 .spot .big{font-size:38px;font-weight:600;font-family:var(--mono)}
 .spot ul{columns:2;gap:24px;margin:14px 0 0;padding:0 0 0 18px;font-size:13px;color:#c7c7d6}
 @media(max-width:560px){.spot ul{columns:1}}
 /* sound + bridges */
 .scards{display:grid;grid-template-columns:1fr 1fr;gap:14px}
 @media(max-width:640px){.scards{grid-template-columns:1fr}}
 .scard{border:1px solid var(--line);border-radius:10px;padding:13px}
 .sname{font-size:14px;font-weight:500;margin:0 0 3px}.ssize{font-family:var(--mono);font-size:11px;color:var(--muted);margin:0 0 7px}
 ul.bridges{list-style:none;padding:0;margin:0}
 ul.bridges li{padding:9px 0;border-bottom:1px solid var(--line);font-size:14px}
 ul.bridges .br{display:block;font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:2px}
 table.drift{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
 table.drift th{text-align:left;font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:500;padding:6px 10px 6px 0;border-bottom:1px solid var(--line)}
 table.drift td{padding:9px 10px 9px 0;border-bottom:1px solid var(--line)}
 table.drift .per{color:var(--purple)}
 footer{margin-top:56px;color:var(--muted);font-family:var(--mono);font-size:12px;border-top:1px solid var(--line);padding-top:20px;line-height:1.7}
 #tip{position:fixed;pointer-events:none;background:#16161f;border:1px solid #2c2c40;border-radius:8px;padding:7px 10px;font-size:12.5px;opacity:0;transition:.08s;max-width:250px;z-index:9}
 #tip b{color:#fff}#tip span{color:#9a9ab5;font-family:var(--mono);font-size:11px}
</style>
<div class="wrap">
 <section class="hero">
  <p class="eyebrow">ночной разбор вкуса · тотальный анализ</p>
  <h1>Портрет вкуса</h1>
  <p class="hero-sub">__N__ песен из твоих лайков, разобранных по 21 признаку и разложенных
  на __K__ настроений. Ниже — карта, настроения, звучание и как вкус менялся во времени.</p>
 </section>

 <section>
  <p class="eyebrow" style="color:var(--purple)">01 · карта</p>
  <h2>честная карта (UMAP)</h2>
  <p class="lead">Проекция 19-мерного пространства в 2D: близкие точки = похоже звучат.
  Цвет — настроение. Наводи на точки.</p>
  <div class="maprow">
   <div class="plot" id="plot"></div>
   <div class="leg" id="leg"><h3>настроения</h3></div>
  </div>
 </section>

 <section>
  <p class="eyebrow" style="color:var(--purple)">02 · настроения</p>
  <h2>__K__ музыкальных «я»</h2>
  <div class="cards">__MOODS__</div>
 </section>

 <section>
  <p class="eyebrow" style="color:#EF9F27">03 · выделенная категория</p>
  <h2>биттерсвит</h2>
  <p class="lead">Треки на самой грани мажора и минора — «грустно, но со светлыми нотами».
  Ты просил выделить это особо.</p>
  <div class="spot">
   <div><span class="big">__BSCOUNT__</span> треков · __BSSHARE__% библиотеки на грани 0.5</div>
   <ul>__BSTOP__</ul>
  </div>
 </section>

 <section>
  <p class="eyebrow" style="color:#5DCAA5">04 · звучание</p>
  <h2>другой разрез — по продакшену</h2>
  <p class="lead">Те же треки, но сгруппированы по тому, как они ЗВУЧАТ (тембр, грязь, динамика),
  а не по настроению.</p>
  <div class="scards">__SOUND__</div>
 </section>

 <section>
  <p class="eyebrow" style="color:#D4537E">05 · мосты</p>
  <h2>треки между настроениями</h2>
  <p class="lead">Не в одной коробке, а между двумя — «перешейки» твоего вкуса.</p>
  <ul class="bridges">__BRIDGES__</ul>
 </section>

 __DRIFT__

 <footer>375+ песен · librosa (21 признак на трек) · UMAP + KMeans · биттерсвит = 1−2·|минор−0.5|<br>
 длинные записи (doomer/ambient, 67 шт) вынесены отдельно · собрано ночью</footer>
</div>
<div id="tip"></div>
<script>
const P=__PTS__,L=__LEGEND__,plot=document.getElementById('plot'),tip=document.getElementById('tip');
function draw(){const w=plot.clientWidth,h=plot.clientHeight;plot.querySelectorAll('.dot').forEach(d=>d.remove());
 for(const p of P){const d=document.createElement('div');d.className='dot';
  d.style.left=(p.x/100*w)+'px';d.style.bottom=(p.y/100*h)+'px';d.style.background=p.c;
  d.addEventListener('mousemove',e=>{tip.style.opacity=1;tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY+14)+'px';
   tip.innerHTML='<b>'+p.l+'</b><br><span>'+p.m+'</span>';});
  d.addEventListener('mouseleave',()=>tip.style.opacity=0);plot.appendChild(d);}}
const lg=document.getElementById('leg');
for(const l of L){const r=document.createElement('div');r.className='li';
 r.innerHTML='<span class="sw" style="background:'+l.c+'"></span><div>'+l.t+'<div class="n">'+l.s+' треков</div></div>';lg.appendChild(r);}
draw();addEventListener('resize',draw);
</script>"""


if __name__ == "__main__":
    main()
