"""
build_stems_player.py — интерактивный микшер стемов (Web Audio): play/solo/mute по слоям, синхронно.
Читает <dir>/{vocals,drums,bass,other,mix}.mp3, встраивает как data:URI. Content-only под артефакт.

Запуск: uv run build_stems_player.py <stems_dir> <out.html>
"""

import base64
import sys
from pathlib import Path


def b64(p):
    return "data:audio/mpeg;base64," + base64.b64encode(Path(p).read_bytes()).decode()


STEMS = [
    ("mix", "весь трек", "то, что ты слышишь целиком", "#8a8aa0"),
    ("guitar", "гитара", "ВОТ настоящий гармонический слой (38%) — это не синт!", "#EF9F27"),
    ("bass", "бас", "мощный низкий фундамент — 42%", "#7F77DD"),
    ("drums", "барабаны", "ритм — 60%", "#5DCAA5"),
    ("piano", "пианино", "почти нет (3%) — фоновая деталь", "#378ADD"),
]


def main():
    sdir = Path(sys.argv[1]); out = Path(sys.argv[2])
    data = {name: b64(sdir / f"{name}.mp3") for name, *_ in STEMS}
    rows = "\n".join(f'''<div class="stem" data-k="{k}">
      <span class="dot" style="background:{c}"></span>
      <div class="info"><div class="nm">{title}</div><div class="ds">{desc}</div></div>
      <div class="btns"><button class="solo" data-k="{k}">solo</button>
      <button class="mute" data-k="{k}">mute</button></div>
      <div class="mtr"><i></i></div></div>''' for (k, title, desc, c) in STEMS)

    html = _TPL.replace("__ROWS__", rows)
    for k, b in data.items():
        html = html.replace(f"__{k.upper()}__", b)
    out.write_text(html, encoding="utf-8")
    print(f"✓ {out} ({out.stat().st_size//1024} KB)")


_TPL = """<title>Стемы «unhappy»</title>
<style>
 :root{--bg:#0b0b10;--panel:#111119;--ink:#ececf4;--muted:#8a8aa0;--line:#22222f;--teal:#5DCAA5;--minor:#7F77DD;
   --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;--mono:ui-monospace,"SF Mono",Menlo,monospace}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.5}
 .wrap{max-width:640px;margin:0 auto;padding:44px 20px 70px}
 .eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
 h1{font-size:clamp(24px,5vw,32px);margin:0 0 8px;font-weight:600;letter-spacing:-.02em}
 .sub{color:var(--muted);margin:0 0 22px;max-width:60ch}
 .top{display:flex;align-items:center;gap:14px;margin:0 0 20px}
 .play{width:56px;height:56px;border-radius:50%;border:none;background:var(--teal);color:#04120d;font-size:22px;cursor:pointer;flex:none}
 .play.on{background:var(--minor);color:#fff}
 .status{font-family:var(--mono);font-size:12px;color:var(--muted)}
 .stem{display:grid;grid-template-columns:14px 1fr auto;grid-template-rows:auto auto;gap:4px 12px;
   align-items:center;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--panel);margin:0 0 10px}
 .dot{width:12px;height:12px;border-radius:50%;grid-row:1/2}
 .info .nm{font-size:15px;font-weight:500}.info .ds{font-size:12px;color:var(--muted)}
 .btns{display:flex;gap:6px;grid-row:1/2;grid-column:3}
 .btns button{font-family:var(--mono);font-size:12px;padding:6px 12px;border-radius:7px;border:1px solid var(--line);
   background:transparent;color:var(--muted);cursor:pointer}
 .btns .solo.on{border-color:var(--teal);color:var(--teal);background:rgba(93,202,165,.12)}
 .btns .mute.on{border-color:#c0554f;color:#e08079;background:rgba(192,85,79,.12)}
 .mtr{grid-column:1/4;grid-row:2;height:4px;background:#191922;border-radius:3px;overflow:hidden}
 .mtr i{display:block;height:100%;width:0;background:var(--teal);transition:width .06s}
 .hint{font-family:var(--mono);font-size:11px;color:#66667d;margin:16px 0 0}
</style>
<div class="wrap">
 <p class="eyebrow">разбор на слои · AI-разделение (Demucs)</p>
 <h1>SWOX — истинные инструменты</h1>
 <p class="sub">Модель на 6 семейств вскрыла правду: гармония — это <b>живая гитара</b>, а не синт.
 Жми ▶, потом <b>solo «гитара»</b> — и услышишь сам. <b>mute</b> — убрать слой.</p>

 <div class="top"><button class="play" id="play">▶</button><span class="status" id="status">нажми play</span></div>
 <div id="stems">__ROWS__</div>
 <p class="hint">solo «гитара» → вот он гармонический слой, живая гитара (а ты думал синт) · solo «бас» → низ · mute барабаны → уходит движение · «пианино» почти пустое</p>
</div>
<script>
const SRC={mix:"__MIX__",vocals:"__VOCALS__",other:"__OTHER__",bass:"__BASS__",drums:"__DRUMS__"};
let ctx,master,buffers={},sources={},gains={},analysers={},playing=false,ready=false;
const state={};Object.keys(SRC).forEach(k=>state[k]={solo:false,mute:false});
function b64buf(d){const s=atob(d.split(',')[1]);const u=new Uint8Array(s.length);for(let i=0;i<s.length;i++)u[i]=s.charCodeAt(i);return u.buffer;}
async function load(){
 ctx=new (window.AudioContext||window.webkitAudioContext)();master=ctx.createGain();master.gain.value=0.9;master.connect(ctx.destination);
 document.getElementById('status').textContent='загрузка слоёв…';
 for(const k of Object.keys(SRC)){
  const buf=await ctx.decodeAudioData(b64buf(SRC[k]));buffers[k]=buf;
  const g=ctx.createGain();const a=ctx.createAnalyser();a.fftSize=256;g.connect(a);a.connect(master);gains[k]=g;analysers[k]=a;}
 ready=true;document.getElementById('status').textContent='готово · ▶';
}
function applyGains(){
 const anySolo=Object.values(state).some(s=>s.solo);
 for(const k of Object.keys(SRC)){
  let v=1;const s=state[k];
  if(s.mute)v=0; else if(anySolo&&!s.solo)v=0;
  // mix и стемы: если играет mix соло — остальные молчат, и наоборот. по умолчанию mix заглушим, если что-то ещё солировано.
  if(gains[k])gains[k].gain.setTargetAtTime(v,ctx.currentTime,0.02);}
}
function start(){
 for(const k of Object.keys(SRC)){const src=ctx.createBufferSource();src.buffer=buffers[k];src.loop=true;src.connect(gains[k]);src.start(0);sources[k]=src;}
 applyGains();meter();
}
function stop(){for(const k in sources){try{sources[k].stop()}catch(e){}}sources={};}
async function toggle(){
 if(!ready){await load();}
 if(ctx.state==='suspended')await ctx.resume();
 const btn=document.getElementById('play');
 if(playing){playing=false;stop();btn.classList.remove('on');btn.textContent='▶';document.getElementById('status').textContent='пауза';}
 else{playing=true;start();btn.classList.add('on');btn.textContent='■';document.getElementById('status').textContent='играет · зациклено';}
}
function meter(){
 if(!playing)return;
 for(const k of Object.keys(SRC)){const a=analysers[k];if(!a)continue;const d=new Uint8Array(a.frequencyBinCount);a.getByteFrequencyData(d);
  let s=0;for(const x of d)s+=x;const lvl=Math.min(100,s/d.length/1.2);
  const bar=document.querySelector('.stem[data-k="'+k+'"] .mtr i');if(bar)bar.style.width=lvl+'%';}
 requestAnimationFrame(meter);
}
document.getElementById('play').onclick=toggle;
// по умолчанию замьютим mix, чтобы стемы играли; клик по mix-solo слушает оригинал
state.mix.mute=true;
document.querySelectorAll('.stem[data-k="mix"] .mute').forEach(b=>b.classList.add('on'));
document.querySelectorAll('.solo').forEach(b=>b.onclick=()=>{const k=b.dataset.k;state[k].solo=!state[k].solo;b.classList.toggle('on');
 if(state[k].solo){state[k].mute=false;document.querySelector('.stem[data-k="'+k+'"] .mute').classList.remove('on');}applyGains();});
document.querySelectorAll('.mute').forEach(b=>b.onclick=()=>{const k=b.dataset.k;state[k].mute=!state[k].mute;b.classList.toggle('on');
 if(state[k].mute){state[k].solo=false;document.querySelector('.stem[data-k="'+k+'"] .solo').classList.remove('on');}applyGains();});
</script>"""


if __name__ == "__main__":
    main()
