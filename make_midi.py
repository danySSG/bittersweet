"""
make_midi.py — MIDI-стартер для GarageBand по мотивам «s0rrow — unhappy».

Главный файл — midi/unhappy_starter.mid: 3 дорожки СРАЗУ (синты + бас + барабаны),
темп 117, гармония Fm–Db–Ab–Eb. Это и есть «одновременно», как ты нащупал.
Плюс midi/unhappy_chords.mid (только аккорды) и midi/swox_Em.mid (тёмный ламент).

Запуск: uv run make_midi.py
"""

from pathlib import Path

from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo

MIDI = Path(__file__).parent / "midi"
TPB = 480
BAR = TPB * 4
STEP = TPB // 4  # 16-я

# Fm–Db–Ab–Eb: (bass_root, [триада])
UNHAPPY = [(41, [53, 56, 60]), (37, [49, 53, 56]), (44, [56, 60, 63]), (39, [51, 55, 58])]
SWOX = [(40, [52, 55, 59]), (38, [50, 54, 57]), (36, [48, 52, 55]), (35, [47, 50, 54])]

# GM-ноты ударных
KICK, SNARE, CLAP, CHH, OHH = 36, 38, 39, 42, 46


def track_from(events, program, channel, name):
    """events: list of (start_tick, note, dur_tick, vel)."""
    tr = MidiTrack()
    tr.append(MetaMessage("track_name", name=name))
    if program is not None:
        tr.append(Message("program_change", program=program, channel=channel, time=0))
    ev = []
    for st, note, dur, vel in events:
        ev.append((st, 1, note, vel))
        ev.append((st + dur, 0, note, 0))
    ev.sort(key=lambda e: (e[0], e[1]))   # note_off раньше note_on на том же тике
    last = 0
    for tick, on, note, vel in ev:
        d = tick - last; last = tick
        typ = "note_on" if on else "note_off"
        tr.append(Message(typ, note=note, velocity=vel, channel=channel, time=d))
    return tr


def build_starter(path, prog, bpm=117, bars=8, half_time=False, chord_program=81, name="synths"):
    mid = MidiFile(ticks_per_beat=TPB)
    tempo = MidiTrack(); mid.tracks.append(tempo)
    tempo.append(MetaMessage("set_tempo", tempo=bpm2tempo(bpm)))
    tempo.append(MetaMessage("track_name", name="tempo"))

    chords, bass, drums = [], [], []
    for bar in range(bars):
        root, tri = prog[bar % 4]
        t0 = bar * BAR
        # аккорды — держим весь такт
        for n in tri:
            chords.append((t0, n, BAR - 20, 62))
        # бас — 8-ми на корне, движение
        for i in range(8):
            bass.append((t0 + i * (TPB // 2), root, TPB // 2 - 20, 80 if half_time else 74))
        if half_time:
            # тяжело и сдержанно (SWOX): снер на 3-ю долю, хэты 8-ми
            for s in (0, 10):
                drums.append((t0 + s * STEP, KICK, STEP, 104))
            drums.append((t0 + 8 * STEP, SNARE, STEP, 98))
            for s in (0, 2, 4, 6, 8, 10, 12, 14):
                drums.append((t0 + s * STEP, CHH, STEP, 74 if s % 4 == 0 else 48))
        else:
            for s in (0, 6, 10):
                drums.append((t0 + s * STEP, KICK, STEP, 104))
            for s in (4, 12):
                drums.append((t0 + s * STEP, SNARE, STEP, 96))
                drums.append((t0 + s * STEP, CLAP, STEP, 60))
            for s in range(16):
                if s == 14:
                    drums.append((t0 + s * STEP, OHH, STEP * 2, 78))
                else:
                    drums.append((t0 + s * STEP, CHH, STEP, 82 if s % 4 == 0 else 56))

    mid.tracks.append(track_from(chords, program=chord_program, channel=0, name=name))
    mid.tracks.append(track_from(bass, program=38, channel=1, name="bass"))                     # 38=synth bass
    mid.tracks.append(track_from(drums, program=None, channel=9, name="drums"))             # ch10=ударные
    mid.save(path)
    return path


def build_chords(path, prog, bpm, program=4, bars=4, loops=2):
    mid = MidiFile(ticks_per_beat=TPB)
    tr = MidiTrack(); mid.tracks.append(tr)
    tr.append(MetaMessage("set_tempo", tempo=bpm2tempo(bpm)))
    tr.append(Message("program_change", program=program, time=0))
    ev = []
    for _ in range(loops):
        base = len([e for e in ev])  # dummy
    # проще: собрать события
    events = []
    for L in range(loops):
        for i, (root, tri) in enumerate(prog):
            t0 = (L * len(prog) + i) * BAR
            events.append((t0, root, BAR - 20, 70))
            for n in tri:
                events.append((t0, n, BAR - 20, 58))
    body = track_from(events, program=program, channel=0, name="chords")
    # перенесём tempo в начало этой дорожки
    mid.tracks = [MidiTrack([MetaMessage("set_tempo", tempo=bpm2tempo(bpm))]), body]
    mid.save(path)
    return path


def build_rich_unhappy(path, bpm=117, bars=8):
    """Богатый стартер: пэд + арпеджио + лид отдельными дорожками (как реальный стек синтов)."""
    ARP = {0: [65, 68, 72, 77], 1: [61, 65, 68, 73], 2: [68, 72, 75, 80], 3: [63, 67, 70, 75]}
    # лид: (доля в 4-тактовой фразе, midi, длит. в долях)
    LEAD = [(0, 68, 1), (1, 72, 1), (2, 77, 2), (4, 65, 2), (6, 68, 2),
            (8, 75, 1), (9, 72, 1), (10, 68, 2), (12, 67, 1), (13, 70, 1), (14, 75, 2)]
    pad, arp, lead, bass, drums = [], [], [], [], []
    for bar in range(bars):
        root, tri = UNHAPPY[bar % 4]; t0 = bar * BAR
        for n in tri:
            pad.append((t0, n, BAR - 20, 56))
        for i in range(16):
            note = ARP[bar % 4][i % 4] + (12 if i % 8 >= 4 else 0)
            arp.append((t0 + i * STEP, note, STEP * 2, 72 if i % 4 == 0 else 52))
        for i in range(8):
            bass.append((t0 + i * (TPB // 2), root, TPB // 2 - 20, 74))
        for s in (0, 6, 10):
            drums.append((t0 + s * STEP, KICK, STEP, 104))
        for s in (4, 12):
            drums.append((t0 + s * STEP, SNARE, STEP, 96)); drums.append((t0 + s * STEP, CLAP, STEP, 58))
        for s in range(16):
            drums.append((t0 + s * STEP, OHH if s == 14 else CHH, STEP * (2 if s == 14 else 1), 80 if s % 4 == 0 else 54))
    for phrase_bar in range(0, bars, 4):
        for (b, note, dl) in LEAD:
            lead.append((phrase_bar * BAR + int(b * TPB), note, int(dl * TPB) - 20, 74))

    mid = MidiFile(ticks_per_beat=TPB)
    mid.tracks.append(MidiTrack([MetaMessage("set_tempo", tempo=bpm2tempo(bpm)), MetaMessage("track_name", name="tempo")]))
    mid.tracks.append(track_from(pad, program=89, channel=0, name="1 pad (chords)"))    # 89 warm pad
    mid.tracks.append(track_from(arp, program=11, channel=2, name="2 arp (sparkle)"))   # 11 vibraphone/bell
    mid.tracks.append(track_from(lead, program=81, channel=3, name="3 lead (melody)"))  # 81 synth lead
    mid.tracks.append(track_from(bass, program=38, channel=1, name="bass"))
    mid.tracks.append(track_from(drums, program=None, channel=9, name="drums"))
    mid.save(path)
    return path


def build_rich_swox(path, bpm=117, bars=8):
    """Богатый стартер SWOX: пэд + тёмный арпеджио (8-ми) + сдержанный лид, half-time."""
    ARP = {0: [52, 55, 59, 64], 1: [50, 54, 57, 62], 2: [48, 52, 55, 60], 3: [47, 50, 54, 59]}  # Em D C Bm
    LEAD = [(0, 71, 2), (2, 67, 2), (4, 69, 2), (6, 66, 2),
            (8, 67, 2), (10, 64, 2), (12, 66, 2), (14, 62, 2)]  # нисходящая, разреженная
    pad, arp, lead, bass, drums = [], [], [], [], []
    for bar in range(bars):
        root, tri = SWOX[bar % 4]; t0 = bar * BAR
        for n in tri:
            pad.append((t0, n, BAR - 20, 54))
        for i in range(8):                                    # арп 8-ми — спокойнее, темнее
            arp.append((t0 + i * (TPB // 2), ARP[bar % 4][i % 4], TPB // 2, 60 if i % 2 == 0 else 44))
        for i in range(8):
            bass.append((t0 + i * (TPB // 2), root, TPB // 2 - 20, 82))
        for s in (0, 10):                                     # half-time
            drums.append((t0 + s * STEP, KICK, STEP, 104))
        drums.append((t0 + 8 * STEP, SNARE, STEP, 98))
        for s in (0, 2, 4, 6, 8, 10, 12, 14):
            drums.append((t0 + s * STEP, CHH, STEP, 74 if s % 4 == 0 else 48))
    for phrase_bar in range(0, bars, 4):
        for (b, note, dl) in LEAD:
            lead.append((phrase_bar * BAR + int(b * TPB), note, int(dl * TPB) - 20, 68))

    mid = MidiFile(ticks_per_beat=TPB)
    mid.tracks.append(MidiTrack([MetaMessage("set_tempo", tempo=bpm2tempo(bpm)), MetaMessage("track_name", name="tempo")]))
    mid.tracks.append(track_from(pad, program=89, channel=0, name="1 pad (chords)"))
    mid.tracks.append(track_from(arp, program=11, channel=2, name="2 arp (dark)"))
    mid.tracks.append(track_from(lead, program=82, channel=3, name="3 lead (melody)"))
    mid.tracks.append(track_from(bass, program=38, channel=1, name="bass"))
    mid.tracks.append(track_from(drums, program=None, channel=9, name="drums"))
    mid.save(path)
    return path


def main():
    MIDI.mkdir(exist_ok=True)
    f = build_rich_swox(MIDI / "swox_rich.mid")
    print(f"✓ {f}")
    e = build_rich_unhappy(MIDI / "unhappy_rich.mid")
    print(f"✓ {e}")
    a = build_starter(MIDI / "unhappy_starter.mid", UNHAPPY, bpm=117, name="synths (Fm-Db-Ab-Eb)")
    d = build_starter(MIDI / "swox_starter.mid", SWOX, bpm=117, half_time=True,
                      chord_program=89, name="synths (Em-D-C-Bm dark)")   # 89 = warm pad
    b = build_chords(MIDI / "unhappy_chords.mid", UNHAPPY, bpm=104)
    c = build_chords(MIDI / "swox_Em.mid", SWOX, bpm=100)
    for p in (a, d, b, c):
        print(f"✓ {p}")


if __name__ == "__main__":
    main()
