"""
build_gallery.py — собрать самодостаточную HTML-страницу с картинками разбора.
Картинки встраиваются как data:URI (base64), чтобы страница ни от чего не зависела.

Запуск:  uv run build_gallery.py <output.html>
"""

import base64
import sys
from pathlib import Path

DATA = Path(__file__).parent / "data"

PLATES = [
    ("01 · сырьё", "#7F77DD", "что «видит» анализатор",
     "Четыре вопроса к одному треку — громкость, частоты, ноты, пульс. "
     "Из этих панелей и вытаскиваются все числа.",
     "explain_qe8Q7mjxjig.jpg"),
    ("02 · Фурье", "#5DCAA5", "как рождается спектрограмма",
     "Крохотное окно звука раскладывается на чистые частоты. "
     "Много таких окон подряд — и получается спектрограмма (нижняя панель).",
     "step_fourier_qe8Q7mjxjig.jpg"),
    ("03 · лад", "#D4537E", "как выбирается тональность",
     "12-нотный отпечаток трека сверяется с 24 эталонами тональностей. "
     "Зелёный — победитель: A minor. Так «грусть» становится числом.",
     "step_key_qe8Q7mjxjig.jpg"),
]


def data_uri(name: str) -> str:
    b = (DATA / name).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(b).decode()


def main() -> None:
    out = Path(sys.argv[1])
    plates_html = "\n".join(
        f'''<section class="plate">
  <p class="eyebrow" style="color:{color}">{num}</p>
  <h2>{title}</h2>
  <p class="lead">{lead}</p>
  <figure><img src="{data_uri(img)}" alt="{title}" loading="lazy"></figure>
</section>'''
        for (num, color, title, lead, img) in PLATES
    )

    html = f'''<title>Как анализатор слышит музыку</title>
<style>
  :root{{
    --bg:#0b0b10; --ink:#ececf4; --muted:#8a8aa0; --line:#20202e;
    --purple:#7F77DD; --teal:#5DCAA5; --pink:#D4537E;
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
    line-height:1.6;-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:880px;margin:0 auto;padding:56px 24px 80px}}
  .top-eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.18em;
    text-transform:uppercase;color:var(--muted);margin:0 0 14px}}
  h1{{font-size:clamp(28px,5vw,40px);line-height:1.1;margin:0 0 16px;
    font-weight:600;text-wrap:balance;letter-spacing:-.01em}}
  .intro{{color:var(--muted);font-size:17px;max-width:60ch;margin:0 0 8px}}
  .rule{{height:1px;background:var(--line);margin:48px 0}}
  .plate{{margin:0 0 12px}}
  .eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.14em;
    text-transform:uppercase;margin:0 0 10px}}
  h2{{font-size:22px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}}
  .lead{{color:var(--muted);max-width:64ch;margin:0 0 20px}}
  figure{{margin:0 0 44px;border:1px solid var(--line);border-radius:14px;
    overflow:hidden;background:#0d0d12}}
  img{{display:block;width:100%;height:auto}}
  footer{{color:var(--muted);font-family:var(--mono);font-size:12.5px;
    letter-spacing:.03em;border-top:1px solid var(--line);padding-top:20px}}
</style>
<div class="wrap">
  <p class="top-eyebrow">ночной разбор вкуса · music-taste</p>
  <h1>Как анализатор слышит музыку</h1>
  <p class="intro">Каждый трек превращается в числа тремя шагами. Вот что стоит
  за словами «темп», «лад» и «яркость» — на примере <em>Daft Punk — Veridis Quo</em>.</p>
  <div class="rule"></div>
  {plates_html}
  <footer>сгенерировано из твоих 375 песен · librosa + Krumhansl-Schmuckler</footer>
</div>'''

    out.write_text(html, encoding="utf-8")
    print(f"✓ {out}  ({out.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
