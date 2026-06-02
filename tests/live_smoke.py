"""Live smoke test: exercise the documented Song/Track API against a running
Bitwig (OpenwigBridge controller on :7777). Reports pass/fail per method.

    PYTHONPATH=src python tests/live_smoke.py

NOT a pytest (it needs live Bitwig + makes sound). Run manually before release.
"""
import sys
import tempfile
from pathlib import Path

from openwig import Song
import openwig.notes as N
import openwig.curves as C
import openwig.arrangement as A
import openwig.export as E
import openwig.lint as L

PASS, FAIL = [], []


def ok(label):
    PASS.append(label); print(f"  ok   {label}", flush=True)


def bad(label, e):
    FAIL.append((label, f"{type(e).__name__}: {e}"))
    print(f"  FAIL {label} -> {type(e).__name__}: {str(e)[:80]}", flush=True)


def step(label, fn):
    try:
        fn(); ok(label)
    except Exception as e:  # noqa: BLE001
        bad(label, e)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="bsdk_"))
    print("=== Song / Track live smoke ===", flush=True)

    s = Song(tempo=124, bars=4, clean=True)
    ok("Song(clean=True)")

    step("Song.set_tempo", lambda: s.set_tempo(126))
    step("Song.metronome", lambda: s.metronome(False))
    step("Song.panel", lambda: s.panel("ARRANGE"))

    # KICK track + clip via pulse
    kick = s.track("KICK", device="v9 Kick")
    ok("Song.track(device=...)")
    step("Song.pulse", lambda: s.pulse(36, step=1.0))
    step("Track.clip", lambda: kick.clip(s.pulse(36, step=1.0)))
    step("Track.fader", lambda: kick.fader(0.85))
    step("Track.pan", lambda: kick.pan(-0.1))
    step("Track.color", lambda: kick.color(0.8, 0.3, 0.2))
    step("Track.mute", lambda: kick.mute(False))
    step("Track.solo", lambda: kick.solo(False))
    step("Track.arm", lambda: kick.arm(False))
    step("Track.rename", lambda: kick.rename("KICK"))
    step("Track.send", lambda: kick.send(0, 0.0))
    step("Track.select", lambda: kick.select())
    step("Track.describe_clip", lambda: kick.describe_clip())
    step("Track.remote_pages", lambda: kick.remote_pages())

    # BASS track: fx + clips + pump + automate
    bass = s.track("BASS", device="FM-4")
    step("Track.fx", lambda: bass.fx("Filter"))
    step("Track.add_device", lambda: bass.add_device("EQ+"))
    step("Track.clips", lambda: bass.clips([(0.0, 4.0, N.euclidean(33, 5, 8))]))
    step("Track.pump", lambda: bass.pump(hi=0.82))
    step("Track.automate(volume)",
         lambda: bass.automate("volume", C.lfo(4, shape="sine", lo=0.4, hi=0.9)))
    step("Track.transpose_cursor", lambda: bass.transpose_cursor(0))

    # genre template into a fresh track set is heavy; test a pure-into-song helper
    step("arrangement.print_timeline", lambda: A.print_timeline(s))

    # master chain
    step("Song.master", lambda: s.master(["EQ+", "Peak Limiter"]))

    # export / lint (operate on the built Song state)
    step("export.to_dict", lambda: E.to_dict(s))
    step("export.save_json", lambda: E.save_json(s, tmp / "song.json"))
    step("export.export_midi", lambda: E.export_midi(s, tmp / "song.mid"))
    step("lint.lint", lambda: L.lint(s))
    step("lint.print_lint", lambda: L.print_lint(s))
    step("lint.assert_track_count", lambda: L.assert_track_count(s, 2))

    # transport
    step("Song.play", lambda: s.play())
    step("Song.stop", lambda: s.stop())

    print(f"\nLIVE: {len(PASS)} passed, {len(FAIL)} failed")
    for lbl, err in FAIL:
        print(f"   FAIL {lbl}: {err}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
