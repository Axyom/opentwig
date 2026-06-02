#!/usr/bin/env python3
"""verify_sidechain.py - record the sidechain demo + analyze for kick-rhythm ducking.

Procedure:
1. Render the playing arrangement to .wav
2. Compute RMS in 50ms windows
3. Check that the RMS pattern shows periodic dips matching the kick rhythm
   (one dip per beat at 128 BPM = every ~0.47s = every 9.4 windows of 50ms)
"""
import time, wave, struct
from pathlib import Path

HERE = Path(__file__).resolve()
import numpy as np
from openwig.bridge import BridgeClient
from openwig.wire.render import render_to_wav


def main():
    b = BridgeClient(); b.start(); assert b.wait_connected(8)

    out = HERE.parent / "data" / "temp" / "sidechain_test.wav"
    print(f"recording 4 bars at 128 BPM ({4*4*60/128:.1f}s)...")
    res = render_to_wav(b, str(out), beats=4*4, tempo=128.0)
    print(f"  -> {res}")

    with wave.open(str(out), "rb") as w:
        ch = w.getnchannels(); sr = w.getframerate(); n = w.getnframes()
        raw = w.readframes(n)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2: audio = audio.reshape(-1, 2).mean(axis=1)

    # RMS in 50ms windows
    win = int(0.05 * sr)
    n_win = len(audio) // win
    rms = np.array([np.sqrt(np.mean(audio[i*win:(i+1)*win]**2)) for i in range(n_win)])

    # Beat positions (128 BPM = 0.469s/beat = 9.4 windows). Expect ducking ON beats.
    samples_per_beat = sr * 60 / 128.0
    windows_per_beat = samples_per_beat / win
    print(f"  rms range:  {rms.min():.4f} .. {rms.max():.4f}")
    print(f"  rms mean:   {rms.mean():.4f}")
    print(f"  beats covered: {n_win / windows_per_beat:.2f}")

    # Each beat: look at RMS at the beat (window k = round(i * windows_per_beat))
    # vs the RMS at midpoint between beats. If sidechain working, beat-aligned
    # should be LOWER than midpoint.
    print()
    print(f"  beat-aligned vs midpoint RMS (sidechain pump signature):")
    print(f"  {'beat':>4} {'on-beat':>9} {'off-beat':>9} {'ratio':>8}")
    n_beats = int(n_win / windows_per_beat) - 1
    ratios = []
    for k in range(n_beats):
        on_idx  = int(round(k * windows_per_beat))
        off_idx = int(round((k + 0.5) * windows_per_beat))
        if off_idx >= len(rms): break
        on_rms  = rms[on_idx]; off_rms = rms[off_idx]
        ratio = (on_rms / off_rms) if off_rms > 1e-6 else float("inf")
        ratios.append(ratio)
        print(f"  {k:>4} {on_rms:>9.4f} {off_rms:>9.4f} {ratio:>8.3f}")

    # NB: this is BASS+KICK summed. If sidechain is wired, the BASS amplitude
    # is ducked when kick fires -- so the COMBINED amplitude pattern should
    # show: very high spike on beat (kick), much lower off-beat (only ducked bass).
    # That's the OPPOSITE of what we'd predict naively.
    if ratios:
        avg = sum(ratios) / len(ratios)
        print(f"\n  avg on/off ratio = {avg:.3f}")
        if avg > 1.5:
            print("  SIDECHAIN LIKELY ACTIVE -- on-beats much louder than off-beats")
            print("                            (kick transient dominates, bass ducked)")
        else:
            print(f"  Inconclusive from RMS alone (on/off too close)")

    b.stop()


if __name__ == "__main__":
    main()
