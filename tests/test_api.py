"""API contract tests - verify Song/Track/Note emit the right bridge requests
WITHOUT a live Bitwig.

A `FakeBridge` records every outgoing (method, params) and returns scripted
snapshots, so we can assert the SDK's half of the contract: correct handler
names, parameter keys, value normalization, and the Note->payload mapping.
What these CANNOT check is what Bitwig does with the request (does a track
actually appear, does it make sound) - that needs a live instance and lives in
the manual `live_smoke*.py` scripts.
"""
import pytest

from openwig import Song, Track, Note
import openwig.song as song_mod
import openwig.wire.automation as auto_mod


class FakeBridge:
    """Records outgoing requests; answers the few read calls the API makes."""

    def __init__(self):
        self.calls = []                 # [(method, params), ...] in order
        self.tracks = []                # main-bank tracks (grow on track.create)
        self.effect_tracks = []
        self.device = {"remotes": [
            {"exists": True, "name": "Drive", "index": 0},
            {"exists": True, "name": "Mix", "index": 1},
        ]}

    def request(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "state.snapshot":
            return {"tracks": list(self.tracks),
                    "effect_tracks": list(self.effect_tracks),
                    "device": self.device}
        if method == "track.create":
            bank = self.effect_tracks if params.get("type") == "effect" else self.tracks
            bank.append({"name": params.get("name"), "index": len(bank)})
        if method == "master.remotes":
            return []
        return {}

    def request_op(self, method, params=None, **_kw):
        """Async-op fire+wait collapses to a plain recorded request in tests."""
        return self.request(method, params)

    def request_insert(self, method, params=None, **_kw):
        """Device-insert fire+wait collapses to a plain recorded request in tests."""
        return self.request(method, params)

    # --- assertion helpers ---
    def methods(self):
        return [m for m, _ in self.calls]

    def last(self, method):
        for m, p in reversed(self.calls):
            if m == method:
                return p
        raise AssertionError(f"no call to {method!r}; saw {self.methods()}")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The API sprinkles time.sleep() as a sync mechanism; stub it so tests fly."""
    monkeypatch.setattr(song_mod.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(auto_mod.time, "sleep", lambda *a, **k: None)


@pytest.fixture
def song():
    return Song(bridge=FakeBridge(), check_version=False)


# ── Note ──────────────────────────────────────────────────────────────────────

def test_note_defaults():
    n = Note(36, 2.0)
    assert (n.dur, n.vel, n.channel) == (0.5, 1.0, 0)


def test_note_is_a_plain_tuple():
    n = Note(36, 2.0, dur=0.25, vel=0.8)
    assert isinstance(n, tuple)
    assert tuple(n) == (36, 2.0, 0.25, 0.8, 0)
    assert (n[0], n[1], n[2], n[3]) == (36, 2.0, 0.25, 0.8)


# ── Song construction ──────────────────────────────────────────────────────────

def test_song_injected_bridge_skips_handshake(song):
    # check_version=False + injected bridge => no ensure_compatible / connect
    assert "transport.stop" in song.b.methods()


def test_clean_calls_project_clear():
    b = FakeBridge()
    Song(bridge=b, check_version=False, clean=True)
    assert "project.clear" in b.methods()


def test_not_clean_skips_project_clear():
    b = FakeBridge()
    Song(bridge=b, check_version=False, clean=False)
    assert "project.clear" not in b.methods()


# ── tracks ──────────────────────────────────────────────────────────────────────

def test_track_create_params_and_index(song):
    t = song.track("KICK")
    assert ("track.create", {"type": "instrument", "name": "KICK", "index": -1}) in song.b.calls
    assert t.idx == 0


def test_track_with_device_inserts_file(song):
    song.track("KICK", device="v9 Kick")
    assert song.b.last("device.insert_file")["path"].endswith("v9 Kick.bwdevice")


def test_fx_track_uses_effect_bank(song):
    fx = song.fx_track("REV")
    assert fx.bank == "effect"
    assert song.b.last("track.create")["type"] == "effect"


# ── clips + Note payload ─────────────────────────────────────────────────────────

def test_clip_note_payload_is_channel_first_floats(song):
    t = song.track("BASS")
    t.clip([Note(33, 1.0, dur=0.4, vel=0.85)])
    p = song.b.last("clip.create_arranger_with_notes")
    assert p["notes"] == [[0, 33, 1.0, 0.4, 0.85]]
    assert p["start"] == 0.0
    assert p["duration"] == float(song.total)


def test_clip_accepts_raw_tuple(song):
    t = song.track("BASS")
    t.clip([(33, 0.0, 0.5, 1.0)])
    assert song.b.last("clip.create_arranger_with_notes")["notes"] == [[0, 33, 0.0, 0.5, 1.0]]


def test_clip_preserves_explicit_channel(song):
    t = song.track("BASS")
    t.clip([Note(33, 0.0, dur=0.5, vel=1.0, channel=3)])
    assert song.b.last("clip.create_arranger_with_notes")["notes"][0][0] == 3


def test_short_note_raises_valueerror(song):
    t = song.track("BASS")
    with pytest.raises(ValueError):
        t.clip([(33, 0.0)])


def test_clips_emits_one_request_per_segment(song):
    t = song.track("BASS")
    t.clips([(0, 4, [Note(33, 0)]), (8, 4, [Note(33, 0)])])
    starts = [p["start"] for m, p in song.b.calls if m == "clip.create_arranger_with_notes"]
    assert starts == [0.0, 8.0]


# ── value normalization ──────────────────────────────────────────────────────────

def test_pan_normalizes_minus1_to_1_into_0_to_1(song):
    t = song.track("T")
    t.pan(-1.0); assert song.b.last("track.set_pan")["value"] == 0.0
    t.pan(0.0);  assert song.b.last("track.set_pan")["value"] == 0.5
    t.pan(1.0);  assert song.b.last("track.set_pan")["value"] == 1.0


def test_fader_clamps_to_unit_range(song):
    t = song.track("T")
    t.fader(2.0);  assert song.b.last("track.set_volume")["value"] == 1.0
    t.fader(-1.0); assert song.b.last("track.set_volume")["value"] == 0.0


# ── fx / remotes ──────────────────────────────────────────────────────────────────

def test_fx_inserts_device_and_sets_matched_remote(song):
    t = song.track("T")
    t.fx("Saturator", Drive=0.25)
    assert song.b.last("device.insert_file")["path"].endswith("Saturator.bwdevice")
    assert song.b.last("device.set_remote") == {"index": 0, "value": 0.25}


def test_unmatched_remote_warns_and_sets_nothing(song):
    t = song.track("T")
    before = song.b.methods().count("device.set_remote")
    with pytest.warns(UserWarning):
        t._set_remote("Nonexistent", 0.5)
    assert song.b.methods().count("device.set_remote") == before


# ── effect-track guards (loud, not silent) ───────────────────────────────────────

def test_send_on_effect_track_warns_and_noops(song):
    fx = song.fx_track("REV")
    with pytest.warns(UserWarning):
        fx.send(0, 0.5)
    assert "track.set_send" not in song.b.methods()


def test_arm_on_effect_track_warns(song):
    fx = song.fx_track("REV")
    with pytest.warns(UserWarning):
        fx.arm(True)


# ── automation ────────────────────────────────────────────────────────────────────

def test_automate_emits_write_offline(song):
    t = song.track("BASS")
    t.automate("volume", [(0, 0.30), (1, 0.80)])
    p = song.b.last("automation.write_offline")
    assert p["param"] == "volume"
    assert p["remote_index"] == 0
    assert p["points"] == [[0, 0.30], [1, 0.80]]


# ── scene (launcher) velocity contract ───────────────────────────────────────────

def test_scene_scales_velocity_to_0_127(song):
    t = song.track("LEAD")
    t.scene(0, [Note(60, 0.0, dur=0.5, vel=1.0)])
    assert song.b.last("clip.set_step")["velocity"] == 127


# ── misc ──────────────────────────────────────────────────────────────────────────

def test_marker_emits_cue_add(song):
    song.marker()
    assert "cue.add" in song.b.methods()


def test_master_inserts_each_device_on_master(song):
    song.master(["EQ+", "Compressor+", "Peak Limiter"])
    paths = [p["path"] for m, p in song.b.calls if m == "device.insert_file_on_master"]
    assert [pp.split("/")[-1] for pp in paths] == [
        "EQ+.bwdevice", "Compressor+.bwdevice", "Peak Limiter.bwdevice"]
