#!/usr/bin/env python3
"""demo_sidechain.py - real cross-track sidechain via Compressor+.

KICK on 4-on-floor, BASS holds a sustained note. Compressor+ on BASS is
sidechained to KICK's signal -> BASS should duck on every kick.
"""
from openwig import Song, Note


def main():
    s = Song(tempo=128, bars=4, clean=True)

    kick = s.track("KICK", device="v9 Kick")
    kick.clip([Note(36, b, dur=0.25) for b in range(int(s.total))])
    kick.fader(0.85)

    bass = s.track("BASS", device="FM-4")
    bass.fx("Compressor+")
    # tune the compressor for obvious ducking
    bass._set_remote("Threshold", 0.20)
    bass._set_remote("Ratio", 0.80)
    bass._set_remote("Attack", 0.05)
    bass._set_remote("Release", 0.30)
    bass.clip([Note(33, 0, dur=float(s.total), vel=0.85)])

    # Wire BASS's Compressor+ sidechain input from KICK's signal
    bass.sidechain_from(kick, sink_device_index=1)
    print("sidechain wired: BASS Compressor+ <- KICK signal")

    s.play(loop=True)
    print(f"playing 4-bar sidechain test @ {s.tempo} BPM (kick should duck bass)")
    s.close()


if __name__ == "__main__":
    main()
