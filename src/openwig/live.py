"""bitwig_live.py - live (real-time) helpers.

LiveScheduler: queue (track_idx, key, velocity, start_beat, duration) events and
play them via track.start_note / track.stop_note while the transport runs.
Uses Python threading + bridge calls (latency ~5-20 ms per call). Best for
interactive/algorithmic playback, not tight performance.
"""
from __future__ import annotations
import heapq, threading, time


class LiveScheduler:
    """A beat-accurate note dispatcher driven by the system clock + a known tempo.
    Start by setting `t0` (wall-clock at beat 0) and tempo; queued events fire when
    their beat is reached. Stop with .stop()."""

    def __init__(self, bridge, *, tempo: float = 120.0):
        self.b = bridge; self.tempo = tempo
        self.t0 = None
        self._q: list[tuple[float, int, int, int, float]] = []  # (abs_beat, track, key, vel127, dur_beats)
        self._counter = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self, t0: float | None = None):
        """Begin dispatching. `t0` = wall-clock when beat 0 happened (default: now)."""
        if t0 is None: t0 = time.time()
        self.t0 = t0
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None: self._thread.join(timeout=2.0)
        return self

    def play(self, track_idx: int, key: int, beat: float, *, dur: float = 0.5,
             vel: float = 0.85):
        """Queue a note at `beat` (absolute), velocity 0..1, duration in beats."""
        heapq.heappush(self._q, (float(beat), int(track_idx), int(key),
                                 max(1, min(127, int(127 * float(vel)))), float(dur)))
        return self

    def chord(self, track_idx: int, keys, beat: float, *, dur: float = 1.0, vel: float = 0.7):
        for k in keys:
            self.play(track_idx, k, beat, dur=dur, vel=vel)
        return self

    def _beat_to_wall(self, beat: float) -> float:
        return self.t0 + beat * 60.0 / self.tempo

    def _loop(self):
        while not self._stop.is_set():
            if not self._q:
                time.sleep(0.005); continue
            next_beat, track, key, vel, dur = self._q[0]
            now = time.time()
            target = self._beat_to_wall(next_beat)
            if now < target:
                time.sleep(min(0.020, target - now)); continue
            heapq.heappop(self._q)
            self.b.request("track.start_note", {"track": track, "key": key, "velocity": vel})
            # off-event: spawn a tiny thread that sleeps the duration then stops the note
            off_wall = target + dur * 60.0 / self.tempo
            threading.Thread(target=self._fire_off,
                             args=(track, key, off_wall), daemon=True).start()

    def _fire_off(self, track, key, off_wall):
        wait = max(0.0, off_wall - time.time())
        if wait: time.sleep(wait)
        try: self.b.request("track.stop_note", {"track": track, "key": key})
        except Exception: pass
