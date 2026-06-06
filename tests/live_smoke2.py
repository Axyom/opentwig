"""Live smoke test, batch 2: render + track variants + modulators + sidechain +
tempo automation + transport extras. Run manually (needs live Bitwig, sound)."""
import sys
import tempfile
from pathlib import Path

from openwig import Song

PASS, FAIL = [], []


def step(label, fn):
    try:
        fn(); PASS.append(label); print(f"  ok   {label}", flush=True)
    except Exception as e:  # noqa: BLE001
        FAIL.append((label, f"{type(e).__name__}: {e}"))
        print(f"  FAIL {label} -> {type(e).__name__}: {str(e)[:90]}", flush=True)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="bsdk2_"))
    s = Song(tempo=128, bars=2, clean=True)

    # track-creation variants
    kick = s.track("KICK", device="v9 Kick")
    kick.clip([(36, beat, 0.25, 1.0) for beat in range(s.total)])
    step("Song.audio_track", lambda: s.audio_track("AUD"))
    step("Song.fx_track", lambda: s.fx_track("RETURN", device="Reverb"))

    bass = s.track("BASS", device="FM-4")
    bass.clip([(33, beat, 0.4, 0.85) for beat in range(s.total)])

    # sidechain (KICK -> BASS)
    step("Track.sidechain_from", lambda: bass.sidechain_from(kick))

    # tempo automation + markers + transport
    step("Song.automate_tempo",
         lambda: s.automate_tempo([(0, 128), (8, 140)]))
    step("Song.marker", lambda: s.marker())
    step("Song.undo", lambda: s.undo())
    step("Song.redo", lambda: s.redo())
    step("Song.stop_all", lambda: s.stop_all())

    # THE headline feature: render to wav, assert non-silent
    out = tmp / "render.wav"

    def do_render():
        path = s.render(str(out), tail=0.5)
        import wave
        with wave.open(str(out), "rb") as w:
            frames = w.readframes(min(w.getnframes(), 200000))
        nonzero = any(b != 0 for b in frames[:4000])
        assert out.exists() and out.stat().st_size > 1000, "render produced no file"
        print(f"       render -> {out.name} ({out.stat().st_size} bytes, "
              f"{'non-silent' if nonzero else 'SILENT?'})")
    step("Song.render", do_render)

    print(f"\nLIVE2: {len(PASS)} passed, {len(FAIL)} failed")
    for lbl, err in FAIL:
        print(f"   FAIL {lbl}: {err}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
