"""
generate_track.py — репродукция «SWOX — ...i already died»: тёмно, просторно, минимально.

Реверс: ми-минор, ~117 BPM, атмосферно. Беру нисходящий ламент Em–D–C–Bm
(бас идёт вниз E→D→C→B — классический «печальный спуск»). Тёмные клавиши, много
реверба, без ударных. Резигнация, не биттерсвит.

Выход: data/track.wav   Запуск: uv run generate_track.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, fftconvolve, lfilter

SR = 44100
BPM = 108
BEAT = 60 / BPM
BAR = 4 * BEAT
DATA = Path(__file__).parent / "data"
rng = np.random.default_rng(4)


def m2f(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def lp(x, c):
    b, a = butter(2, min(c / (SR / 2), .99), "low"); return lfilter(b, a, x)


def add(buf, sig, at, g=1.0):
    i = int(max(0, at) * SR); j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i] * g


def keys(freq, dur, dark=1300):
    """Тёмные тёплые клавиши: мягкая атака, приглушённый верх, долгий хвост."""
    n = int(dur * SR); t = np.arange(n) / SR
    s = np.zeros(n)
    for h, a, dec in [(1, 1.0, 1.6), (2, 0.45, 2.4), (3, 0.2, 3.4), (4, 0.08, 5)]:
        s += a * np.sin(2 * np.pi * freq * h * t) * np.exp(-t * dec)
    atk = int(0.012 * SR); s[:atk] *= np.linspace(0, 1, atk)
    return lp(s, dark) * 0.5


def reverb(x, wet=0.38):
    ir_n = int(0.9 * SR)
    ir = lp(rng.standard_normal(ir_n) * np.exp(-np.arange(ir_n) / SR * 3.5), 2800)
    w = fftconvolve(x, ir)[: len(x)]; w /= np.max(np.abs(w)) + 1e-9
    return x * (1 - wet) + w * wet


# Em – D – C – Bm  (i–VII–VI–v, нисходящий бас E→D→C→B); (bass, [арп-тоны])
PROG = [
    (40, [52, 55, 59, 64]),   # Em  (E G B + E)
    (38, [50, 54, 57, 62]),   # D   (D F# A + D)
    (36, [48, 52, 55, 60]),   # C   (C E G + C)
    (35, [47, 50, 54, 59]),   # Bm  (B D F# + B)
]


def jit(t):
    return t + float(rng.normal(0, 0.006))


def main():
    N = 8
    total = int(N * BAR * SR) + SR
    buf = np.zeros(total)
    for bar in range(N):
        bass, arp = PROG[bar % 4]
        t0 = bar * BAR
        # бас — длинный, тёмный
        add(buf, keys(m2f(bass), BAR * 1.15, dark=700), t0, 0.5)
        # редкое, звенящее вполсилы арпеджио: 4 ноты, с воздухом между
        for k, beat in enumerate([0.5, 1.5, 2.0, 3.0]):
            note = arp[k % len(arp)]
            add(buf, keys(m2f(note), BEAT * 3.0, dark=1500), jit(t0 + beat * BEAT), 0.34 * (0.85 + 0.3 * rng.random()))
        # верхняя нота-«капля» в конце такта — искра света
        add(buf, keys(m2f(arp[0] + 12), BEAT * 2.2, dark=2200), jit(t0 + 3.5 * BEAT), 0.16)

    buf = reverb(buf, 0.38)
    buf += rng.standard_normal(total) * 0.002
    buf = np.tanh(buf * 1.05)
    buf *= 0.85 / (np.max(np.abs(buf)) + 1e-9)
    fi, fo = int(0.5 * SR), int(1.6 * SR)
    buf[:fi] *= np.linspace(0, 1, fi); buf[-fo:] *= np.linspace(1, 0, fo)

    DATA.mkdir(exist_ok=True)
    sf.write(str(DATA / "track.wav"), buf.astype(np.float32), SR)
    print(f"✓ data/track.wav  ({len(buf)/SR:.1f} сек · {BPM} BPM · Em–D–C–Bm · тёмно+просторно")


if __name__ == "__main__":
    main()
