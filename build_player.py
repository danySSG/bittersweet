"""build_player.py — плеер с встроенным mp3 (data:URI). Content-only под артефакт.
Запуск: uv run build_player.py <mp3> <out.html>"""

import base64
import sys
from pathlib import Path


def main():
    mp3path, out = Path(sys.argv[1]), Path(sys.argv[2])
    mp3 = base64.b64encode(mp3path.read_bytes()).decode()
    out.write_text(_TPL.replace("__MP3__", mp3), encoding="utf-8")
    print(f"✓ {out} ({out.stat().st_size//1024} KB)")


_TPL = """<title>unhappy · инструментал</title>
<style>
 :root{--bg:#0b0b10;--ink:#ececf4;--muted:#8a8aa0;--line:#20202e;--purple:#7F77DD;--teal:#5DCAA5;--amber:#EF9F27;
   --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;--mono:ui-monospace,"SF Mono",Menlo,monospace}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.55}
 .wrap{max-width:600px;margin:0 auto;padding:52px 22px 80px}
 .eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:0 0 12px}
 h1{font-size:clamp(25px,5vw,33px);margin:0 0 10px;font-weight:600;letter-spacing:-.02em;text-wrap:balance}
 .sub{color:var(--muted);margin:0 0 24px;max-width:54ch}
 .player{border:1px solid var(--line);border-radius:16px;padding:22px;
   background:radial-gradient(130% 130% at 15% 0%,rgba(127,119,221,.12),transparent 55%)}
 audio{width:100%;height:40px;margin:0 0 6px}
 .tby{font-family:var(--mono);font-size:12px;color:var(--muted);margin:0 0 20px}
 .eq{display:flex;gap:8px;margin:0 0 6px}
 .lay{flex:1;border:1px solid var(--line);border-radius:10px;padding:11px 6px;text-align:center}
 .lay .n{font-size:13px;font-weight:500}.lay .r{font-family:var(--mono);font-size:10px;color:var(--muted);margin-top:3px}
 .lay.s{border-color:#3a3314}.lay.b{border-color:#332f56}.lay.d{border-color:#20402f}
 .lay.s .n{color:var(--amber)}.lay.d .n{color:var(--teal)}.lay.b .n{color:var(--purple)}
 .note{color:var(--muted);font-size:12.5px;margin:20px 0 0;border-top:1px solid var(--line);padding-top:16px}
 .note b{color:#c3c3d2}
</style>
<div class="wrap">
 <p class="eyebrow">твоё открытие · инструментал</p>
 <h1>Синты + барабаны, вместе</h1>
 <p class="sub">Настоящий «s0rrow — unhappy» без вокала — только те слои, что ты назвал важными.
 Меланхолия и ритм <b>одновременно</b>. Полная версия, зациклено.</p>

 <div class="player">
  <audio controls preload="metadata" loop src="data:audio/mpeg;base64,__MP3__"></audio>
  <div class="tby">инструментал · без вокала · 1:38</div>

  <div class="eq">
   <div class="lay s"><div class="n">синты</div><div class="r">меланхолия</div></div>
   <div class="lay b"><div class="n">бас</div><div class="r">вес</div></div>
   <div class="lay d"><div class="n">барабаны</div><div class="r">ритм · жизнь</div></div>
  </div>

  <p class="note">Это буквально ответ на твоё первое сообщение: «<b>ритмичная</b> быстрая
  <b>меланхоличная</b>». Меланхолия (синты) + ритм (барабаны) в одном — вот корень, что ты искал.
  Голос убран, а красота осталась: значит она в этом сочетании, а не в вокале.</p>
 </div>
</div>"""


if __name__ == "__main__":
    main()
