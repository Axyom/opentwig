# Render to `.wav`

The SDK records Bitwig's master output to a `.wav` file via WASAPI loopback
(Windows). It's a *real recording* of what you'd hear, not an offline render-
plug-in latencies, audio-rate sidechain, modulator non-determinism all
behave the same as live playback.

## The one-liner

```python
print(s.render("song.wav"))
```

That stops the transport, repositions to beat 0, opens a loopback capture on
Bitwig's audio endpoint, plays the song once, and writes the file. The return
value is the absolute path to the `.wav`.

## What `render` actually does

1. **Stops + rewinds**. `transport.stop` + `transport.set_position(0)`. The
   render begins from beat 0 unless you pass `start_beat`.
2. **Configures the loopback**. Opens the WASAPI loopback endpoint that Bitwig
   is routed to. The SDK uses
   [`PyAudioWPatch`](https://github.com/s0d3s/PyAudioWPatch) for this - it's a
   Windows-only fork of PyAudio with native WASAPI loopback. The endpoint is
   auto-detected.
3. **Plays the song once**. `transport.play` for the song's full beat-length
   plus a small tail.
4. **Stops + writes the `.wav`**. Captured samples are written out at the
   endpoint's native sample rate. The header is fixed up to match.

## Gotchas

### Loopback is per-endpoint, not per-app

WASAPI loopback captures the *endpoint*, not Bitwig specifically. If you have
Bitwig routed to one device (your audio interface) and Chrome playing a
YouTube video on a different device, you'll only get Bitwig. If they're on
the *same* device, the YouTube sound will end up in the `.wav` too. Mute
other apps before rendering, or route Bitwig to a virtual "renders only"
device.

### Bitwig must produce audio in real time

This is a real-time capture. If Bitwig drops samples (CPU spikes, plug-in
glitches, voice-stealing) those drops are baked into the file. Watch the
Bitwig load meter during long renders.

### Long renders take real time

A 4-minute song takes 4 minutes to render. There is no offline "faster than
realtime" mode - Bitwig's API doesn't expose one. For long batches,
parallelism doesn't help (one Bitwig instance, one loopback).

## Rendering a section

```python
from openwig.export import render_section

render_section(s, "drop.wav", start_beat=64.0, end_beat=128.0)
```

`render_section` renders only the requested beat range - useful for stems,
previews, A/B comparisons of edits.

## Rendering stems (per-track)

```python
from openwig.export import render_stems

render_stems(s, out_dir="stems/")
```

For each track, the SDK solos it, renders to `stems/<track_name>.wav`,
un-solos. Time-aligned by construction - they line up when imported back into
a DAW.

## Verifying a render

Use numpy on the resulting wav. `examples/verify_sidechain.py` does this
end-to-end:

```python
import numpy as np, wave
with wave.open("song.wav") as w:
    n = w.getnframes()
    pcm = np.frombuffer(w.readframes(n), dtype=np.int16)
    pcm = pcm.reshape(-1, w.getnchannels()).mean(axis=1)

# RMS in 50 ms windows
win = int(w.getframerate() * 0.050)
rms = np.array([np.sqrt(np.mean(pcm[i:i+win].astype(float)**2))
                for i in range(0, len(pcm) - win, win)])

print(f"frames {n}, rms mean {rms.mean():.0f}, rms max {rms.max():.0f}")
```

Use this in CI / regression tests: render, compute, assert on properties of
the RMS curve (peaks at expected beats, dips between, etc.).

## macOS / Linux

WASAPI loopback is Windows-only. On macOS or Linux you'll need to route
Bitwig's master to a system loopback device (BlackHole, JACK loopback) and
record externally. The SDK's `render` method will raise on non-Windows.

## Next

- [Cookbook](../cookbook.md)
- [API: `Song.render` / `openwig.export`](../reference.md)
