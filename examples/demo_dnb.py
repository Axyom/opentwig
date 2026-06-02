#!/usr/bin/env python3
"""demo_dnb.py - 16-bar drum & bass jam built on the SDK + arrangement template."""
from openwig import Song, arrangement as A, curves as C


def main():
    s = Song(tempo=174, bars=16, clean=True)        # DnB tempo

    # Template = instant skeleton (KICK / SNARE / HATS / REESE BASS)
    A.template(s, "dnb", bars=16, root="F", mode="minor")

    # Polish the reese bass: filter sweep + saturation already wired; add a
    # slow LFO on volume for movement
    bass = s.tracks["REESE"]
    bass.fader(0.78)
    bass.automate("volume", C.lfo(float(s.total), shape="tri",
                                  rate=1/8, lo=0.62, hi=0.82))

    # Send hats and snare to a real reverb return for spaciousness
    rev = s.fx_track("REV", device="Reverb")
    rev.fader(0.65)
    s.tracks["HATS"].send(0, 0.35)
    s.tracks["SNARE"].send(0, 0.30)

    # Slight kick punch
    s.tracks["KICK"].fader(0.85)

    s.play(loop=True)
    print(f"playing {s.bars}-bar DnB @ {s.tempo} BPM")
    s.close()


if __name__ == "__main__":
    main()
