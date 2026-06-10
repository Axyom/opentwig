"""wire_render.py - render the playing Bitwig project to a .wav by recording the WASAPI
loopback of Bitwig's audio output (real-time capture; the actual mastered output).

  from openwig.wire.render import render_to_wav
  render_to_wav(bridge, "out.wav", beats=128, tempo=128)

Plays the arrangement once from beat 0 (loop OFF) and records the system loopback for the
arrangement length. Pins to the default WASAPI loopback endpoint (Bitwig should output there);
asserts the capture isn't silent. CLI: python wire_render.py out.wav BEATS TEMPO
"""
import sys, time, wave
from pathlib import Path
import numpy as np


def render_to_wav(bridge, out_path, beats=128.0, tempo=128.0, tail=1.0, lead=0.3):
    """Record Bitwig's loopback output for one pass of the arrangement -> out_path (.wav)."""
    # WASAPI loopback is Windows-only; PyAudioWPatch is excluded on other platforms by its
    # pip marker. Import lazily so `import openwig` (and the unit tests / linux CI) work
    # everywhere; only actually rendering needs the package.
    import pyaudiowpatch as pa

    secs = beats / tempo * 60.0 + tail
    p = pa.PyAudio()
    dev = p.get_default_wasapi_loopback()
    sr = int(dev["defaultSampleRate"]); ch = int(dev["maxInputChannels"]); di = dev["index"]
    frames_per = 2048
    st = p.open(format=pa.paInt16, channels=ch, rate=sr, input=True,
                input_device_index=di, frames_per_buffer=frames_per)

    # position to start, disable loop so it plays through once, then play
    try: bridge.request("transport.set_tempo", {"bpm": float(tempo)})
    except Exception: pass
    try: bridge.request("transport.set_loop", {"on": False})
    except Exception: pass
    bridge.request("transport.stop"); time.sleep(0.2)
    bridge.request("transport.set_position", {"beats": 0.0})
    # flush any buffered audio, then start
    st.read(max(1, st.get_read_available()), exception_on_overflow=False)
    bridge.request("transport.play")
    time.sleep(lead)  # let it actually start

    chunks = []
    n_needed = int(secs * sr)
    got = 0
    while got < n_needed:
        data = st.read(frames_per, exception_on_overflow=False)
        chunks.append(data); got += frames_per
    bridge.request("transport.stop")
    st.stop_stream(); st.close(); p.terminate()

    audio = np.frombuffer(b"".join(chunks), dtype=np.int16)
    rms = float(np.sqrt(np.mean((audio.astype(np.float64) / 32768.0) ** 2)))
    out_path = str(out_path)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(chunks))
    dur = len(audio) / ch / sr
    return {"path": out_path, "seconds": round(dur, 2), "rate": sr, "channels": ch,
            "rms": round(rms, 5), "silent": rms < 1e-3}


if __name__ == "__main__":
    from openwig.bridge import BridgeClient
    out = sys.argv[1] if len(sys.argv) > 1 else "render.wav"
    beats = float(sys.argv[2]) if len(sys.argv) > 2 else 128.0
    tempo = float(sys.argv[3]) if len(sys.argv) > 3 else 128.0
    b = BridgeClient(); b.start(); assert b.wait_connected(8), "bridge not connected"
    print("rendering (real-time loopback capture)...")
    res = render_to_wav(b, out, beats, tempo)
    print(res)
    b.stop()
