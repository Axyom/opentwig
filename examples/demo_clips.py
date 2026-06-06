#!/usr/bin/env python3
"""demo_clips.py - a 16-bar ARRANGEMENT: several clips per track with gaps between them.

Structure (beats, 16 bars @ 122 BPM): intro -> drop -> break -> drop. Each track has 2 clips
with gaps (the silences between sections), instead of one clip spanning the song.

  CHORDS  [0-16]   [24-64]        Polysynth + Reverb
  BASS         [8-32]   [40-64]   FM-4 + Filter
  KICK              [16-32] [40-64]   v9 Kick + Saturator   (silent intro + break)
  CLAP              [16-32]   [44-64] v9 Clap + Reverb
  HATS                [20-32] [40-64] v9 Hat Closed

Clears the project first. No render; plays a loop at the end.
"""
from openwig import Song, Note

PROG = [([57, 60, 64], 33),   # Am
        ([53, 57, 60], 29),   # F
        ([60, 64, 67], 36),   # C
        ([55, 59, 62], 31)]   # G


def chord_at(absb):
    return PROG[(int(absb) // 4) % 4]


def gen_chords(abss, length):
    out, seg = [], 0
    while seg < length:
        tri, _ = chord_at(abss + seg)
        out += [Note(n, seg, dur=min(4, length - seg), vel=0.5) for n in tri]
        seg += 4
    return out


def gen_bass(abss, length):
    out = []
    for b in range(int(length)):
        _, root = chord_at(abss + b)
        out.append(Note(root, b + 0.5, dur=0.4, vel=0.85))   # offbeat root
    return out


def kick(length):  return [Note(36, b, dur=0.25) for b in range(int(length))]
def clap(length):  return [Note(39, b, dur=0.20, vel=0.9) for b in range(1, int(length), 2)]   # backbeat
def hats(length):  return [Note(42, b + 0.5, dur=0.20, vel=0.6) for b in range(int(length))]   # offbeat


def main():
    s = Song(tempo=122, bars=16, clean=True)   # 64 beats; clean slate

    tr = s.track("CHORDS", device="Polysynth")
    tr.fx("Reverb", Mix=0.30)
    tr.clips([(0, 16, gen_chords(0, 16)), (24, 40, gen_chords(24, 40))])

    tr = s.track("BASS", device="FM-4")
    tr.fx("Filter")
    tr.clips([(8, 24, gen_bass(8, 24)), (40, 24, gen_bass(40, 24))])

    tr = s.track("KICK", device="v9 Kick")
    tr.fx("Saturator", Drive=0.20)
    tr.clips([(16, 16, kick(16)), (40, 24, kick(24))])

    tr = s.track("CLAP", device="v9 Clap")
    tr.fx("Reverb", Mix=0.30)
    tr.clips([(16, 16, clap(16)), (44, 20, clap(20))])

    tr = s.track("HATS", device="v9 Hat Closed")
    tr.clips([(20, 12, hats(12)), (40, 24, hats(24))])

    s.play(loop=True)
    print("playing 16-bar arrangement (multiple clips per track, with gaps).")
    s.close()


if __name__ == "__main__":
    main()
