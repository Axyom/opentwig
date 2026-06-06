"""Live smoke batch 3: remaining testable documented methods (launcher scenes,
audio clips/samples, device selection, clip props, routing). GUI-dialog methods
(save, save_as_dialog, open_dialog, new_project) are documented but inherently
pop a Bitwig dialog, so they're listed not run."""
import sys
import tempfile
import wave
import struct
import math
from pathlib import Path

from openwig import Song

PASS, FAIL = [], []


def step(label, fn):
    try:
        fn(); PASS.append(label); print(f"  ok   {label}", flush=True)
    except Exception as e:  # noqa: BLE001
        FAIL.append((label, f"{type(e).__name__}: {e}"))
        print(f"  FAIL {label} -> {type(e).__name__}: {str(e)[:90]}", flush=True)


def _make_wav(path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * i / 44100)))
            for i in range(22050)))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="bsdk3_"))
    sample = tmp / "tone.wav"; _make_wav(sample)

    s = Song(tempo=128, bars=2, clean=True)
    lead = s.track("LEAD", device="Polysynth")
    lead.clip([(60, i * 0.5, 0.4, 0.8) for i in range(s.total * 2)])

    # launcher scene + launch
    step("Track.scene", lambda: lead.scene(0, [(60, 0.0, 0.5, 0.8), (64, 0.5, 0.5, 0.8)]))
    step("Track.launch", lambda: lead.launch(0))
    step("Song.scene_launch", lambda: s.scene_launch(0))
    s.stop_all()

    # device selection + clip props + automate variants
    step("Track.fx (for select)", lambda: lead.fx("Reverb"))
    step("Track.select_device", lambda: lead.select_device(0))
    step("Track.preset", lambda: lead.preset("Reverb"))
    step("Track.automate(pan)", lambda: lead.automate("pan", [(0.0, 0.3), (4.0, 0.7)]))
    step("Track.set_clip_prop", lambda: lead.select() or lead.set_clip_prop("loop_length", 4.0))
    step("Track.monitor", lambda: lead.monitor("OFF"))
    step("Track.routing_info", lambda: lead.routing_info())

    # audio track + sample + audio_clip
    aud = s.audio_track("AUD")
    step("Track.sample", lambda: aud.sample(str(sample), 0))
    step("Track.audio_clip", lambda: aud.audio_clip(str(sample), start=0.0, duration=2.0))
    step("Song.verbose", lambda: s.verbose(False))

    print(f"\nLIVE3: {len(PASS)} passed, {len(FAIL)} failed")
    for lbl, err in FAIL:
        print(f"   FAIL {lbl}: {err}")
    print("\nGUI-dialog (documented, not auto-run): save, save_as_dialog, "
          "open_dialog, new_project")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
