"""Nightdrive - the demo track we iterate on. Progressive house/techno, ~3 min.

Run it (Bitwig open, OpenwigBridge enabled):
    python examples/nightdrive.py
It wipes the open project, builds the song, and LOOPS it live in Bitwig so you
hear changes immediately. To capture a .wav instead, swap the last line for:
    print(s.render("nightdrive.wav"))
"""
import math, random
from openwig import Song, Note

RND = random.Random(7)   # seeded -> reproducible variation
def hum(v, amt=0.08):    # humanize velocity
    return max(0.05, min(1.0, v + RND.uniform(-amt, amt)))

# ── pattern helpers (ordinary Python) ─────────────────────────────────────────
def kick(bars, *, vel=1.0, offbeat=False, fills=True):
    out = []
    for bar in range(bars):
        base = bar * 4
        for beat in range(4):
            out.append(Note(36, base + beat, dur=0.24, vel=hum(vel * (1.0 if beat == 0 else 0.93))))
        if offbeat:
            out.append(Note(36, base + 3.5, dur=0.18, vel=hum(vel * 0.7)))
        if fills and bar % 8 == 7:
            out.append(Note(36, base + 2.75, dur=0.12, vel=hum(vel * 0.6)))
            out.append(Note(36, base + 3.5, dur=0.12, vel=hum(vel * 0.78)))
    return out

def hats(bars, *, div=0.5, vel=0.5):
    out = []
    n = int(round(bars * 4 / div))
    for i in range(n):
        if RND.random() < 0.06:
            continue
        accent = 1.12 if i % 8 == 7 else (0.7 if i % 2 else 1.0)
        out.append(Note(42, i * div, dur=min(0.12, div * 0.8), vel=hum(vel * accent)))
    return out

def clap(bars, *, vel=0.9, roll_last=False):
    out = [Note(39, bar * 4 + beat, dur=0.2, vel=hum(vel)) for bar in range(bars) for beat in (1, 3)]
    for bar in range(bars):
        if RND.random() < 0.18:
            out.append(Note(39, bar * 4 + 2.75, dur=0.1, vel=hum(0.4)))
    if roll_last:
        b = (bars - 1) * 4
        out += [Note(39, b + i * 0.25, dur=0.1, vel=hum(0.45 + 0.03 * i)) for i in range(16)]
    return out

def shaker(bars, *, vel=0.4):
    return [Note(70, b + 0.25, dur=0.1, vel=hum(vel)) for b in range(bars * 4)]

def bassline(bars, root, *, vel=0.85, pattern="offbeat"):
    out = []
    for bar in range(bars):
        base = bar * 4
        if pattern == "rolling":
            for i in range(8):
                if RND.random() < 0.05:
                    continue
                k = root + (12 if i % 4 == 3 else 0)
                if bar % 4 == 3 and i >= 6:
                    k = root + 12
                out.append(Note(k, base + i * 0.5, dur=0.3, vel=hum(vel)))
        else:
            for beat in range(4):
                k = root + (12 if (bar % 8 == 7 and beat == 3) else 0)
                out.append(Note(k, base + beat + 0.5, dur=0.4, vel=hum(vel)))
            if RND.random() < 0.25:
                out.append(Note(root, base + 2.25, dur=0.15, vel=hum(0.4)))
    return out

def stabs(bars, roots, *, vel=0.5):
    out = []
    for bar in range(bars):
        if RND.random() < 0.1:
            continue
        r = roots[bar % len(roots)]
        voicing = (0, 3, 7, 12) if bar % 4 == 3 else (0, 3, 7)
        accent = 1.0 if bar % 4 == 0 else 0.9
        out += [Note(r + iv, bar * 4 + 0.5, dur=0.4, vel=hum(vel * accent)) for iv in voicing]
        if RND.random() < 0.22:
            out.append(Note(r + 7, bar * 4 + 2.5, dur=0.2, vel=hum(vel * 0.65)))
    return out

def held(bars, root, *, vel=0.3, voicing=(0, 7, 12)):
    return [Note(root + iv, 0.0, dur=bars * 4.0, vel=vel) for iv in voicing]

def arp(bars, root, *, vel=0.45, step=0.5, shape=(0, 7, 12, 7, 3, 7)):
    out = []
    n = int(round(bars * 4 / step))
    for i in range(n):
        if RND.random() < 0.08:
            continue
        out.append(Note(root + shape[i % len(shape)], i * step, dur=step * 0.9, vel=hum(vel)))
    return out

def lead(bars, keys, *, vel=0.6):
    out = []
    for bar in range(bars):
        k = keys[bar % len(keys)]
        out += [Note(k, bar * 4, dur=1.5, vel=hum(vel)),
                Note(k + 12, bar * 4 + 2.5, dur=0.5, vel=hum(vel * 0.7)),
                Note(k + 7, bar * 4 + 3.0, dur=0.5, vel=hum(vel * 0.6))]
        if bar % 2 == 1:
            out.append(Note(k + 10, bar * 4 + 1.75, dur=0.25, vel=hum(vel * 0.5)))
    return out

def snare_fill(end_beat):
    return [Note(38, end_beat - 2 + i * 0.25, dur=0.1, vel=hum(0.4 + 0.05 * i)) for i in range(8)]

def ramp(b0, b1, v0, v1, *, n=16):
    return [(b0 + (b1 - b0) * i / n, v0 + (v1 - v0) * i / n) for i in range(n + 1)]


s = Song(tempo=128, bars=96, clean=True)   # 6 blocks x 16 bars = 384 beats = 3:00
A1 = 33
PROG_A = [57, 53, 48, 55]      # Am F C G
PROG_B = [50, 46, 53, 48]      # Dm Bb F C

# KICK - phrase-end pushes; out in the breakdown; syncopated in drop 2
kick_t = s.track("KICK", device="v9 Kick").fx("Saturator", Drive=0.2)
kick_t.clips([
    (16, 48, kick(12, vel=0.9)),
    (64, 192, kick(48)),
    (312, 8, kick(2, fills=False)),
    (320, 64, kick(16, offbeat=True)),
])

# HATS - humanized, dropped hits, density rises into the drops
hats_t = s.track("HATS", device="v9 Hat Closed")
hats_t.clips([
    (32, 32, hats(8, vel=0.42)),
    (64, 64, hats(16, vel=0.5)),
    (128, 64, hats(16, div=0.25, vel=0.5)),
    (192, 64, hats(16, vel=0.5)),
    (256, 64, hats(16, vel=0.3)),
    (320, 64, hats(16, div=0.25, vel=0.55)),
])

clap_t = s.track("CLAP", device="v9 Clap").fx("Reverb", Mix=0.22)
clap_t.clips([(128, 64, clap(16)), (192, 64, clap(16, roll_last=True)), (320, 64, clap(16))])

shk = s.track("SHAKER", device="v9 Hat Closed")
shk.clips([(192, 64, shaker(16)), (320, 64, shaker(16))])

# SNARE - a roll fill into every section change
snare_t = s.track("SNARE", device="v9 Snare").fx("Reverb", Mix=0.25)
snare_t.clip([n for end in (64, 128, 192, 256, 320) for n in snare_fill(end)])

# BASS - computed filter LFO + build sweep + sidechain duck + ghost notes
bass_t = s.track("BASS", device="FM-4").fx("Filter")
fcut = []
for beat in range(64, 384):
    base = 0.25 + 0.45 * min(1.0, (beat - 64) / 64.0)
    fcut.append((beat, max(0.05, min(1.0, base + 0.12 * math.sin(beat * 0.8)))))
bass_t.automate("remote", fcut, remote_index=0)
bass_t.clips([
    (64, 64, bassline(16, A1, vel=0.7)),
    (128, 64, bassline(16, A1, pattern="rolling")),
    (192, 64, bassline(16, A1)),
    (320, 64, bassline(16, A1, pattern="rolling")),
])
duck = []
for beat in list(range(128, 192)) + list(range(320, 384)):
    duck += [(beat, 0.0), (beat + 0.85, 1.0)]
bass_t.automate("volume", duck)

# STAB - progression changes in the variation block; spaced + accented
stab_t = s.track("STAB", device="Polysynth").fx("Delay+", Mix=0.2)
stab_t.clips([
    (128, 64, stabs(16, PROG_A)),
    (192, 64, stabs(16, PROG_B)),
    (320, 64, stabs(16, PROG_A)),
])

# PAD - long chords + auto-pan
pad_t = s.track("PAD", device="Polysynth").fx("Reverb", Mix=0.5).fx("Delay+", Mix=0.25)
pad_t.clips([(0, 64, held(16, 45)), (256, 64, held(16, 41, voicing=(0, 5, 7, 12)))])
pan = [(b * 0.5, 0.5 + 0.4 * math.sin(b * 0.4)) for b in range(128)]
pan += [(256 + b * 0.5, 0.5 + 0.4 * math.sin(b * 0.4)) for b in range(128)]
pad_t.automate("pan", pan)

# LEAD - arp in the breakdown, evolving melody + grace notes in variation + drop 2
lead_t = s.track("LEAD", device="Polysynth").fx("Reverb", Mix=0.3).fx("Delay+", Mix=0.3)
lead_t.clips([
    (192, 64, lead(16, [69, 72, 76, 74])),
    (256, 64, arp(16, 57)),
    (320, 64, lead(16, [69, 72, 67, 71])),
])
lead_t.automate("volume", ramp(312, 320, 0.2, 0.9))

s.master(["EQ+", "Compressor+", "Peak Limiter"])
s.play(loop=True)      # loop it live in Bitwig; swap for s.render("nightdrive.wav") to capture
print("playing Nightdrive - loop running in Bitwig")
