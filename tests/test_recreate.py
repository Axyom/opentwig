"""Unit tests for openwig.recreate.to_script - the live read -> script generator.
Pure function (no Bitwig), so it runs in CI."""
from openwig.recreate import to_script

SAMPLE = {
    "tempo": 128,
    "tracks": [
        {
            "index": 0, "name": "KICK", "volume": 0.79, "pan": 0.5,
            "mute": False, "solo": False, "arm": True,
            "devices": [
                {"name": "v9 Kick", "remotes": []},
                {"name": "Saturator", "remotes": [{"name": "Drive", "value": 0.2}]},
            ],
            "clips": [{
                "clip_start": 16, "clip_duration": 48, "note_count": 2,
                "notes": [
                    {"key": 36, "start": 0, "duration": 0.25, "velocity": 1.0, "channel": 0},
                    {"key": 36, "start": 1, "duration": 0.25, "velocity": 0.9, "channel": 0},
                ],
            }],
            "automation": [{
                "param": "volume_value_atom",
                "breakpoints": [{"time": 0, "value": 0.0}, {"time": 1, "value": 1.0}],
            }],
        },
        {
            "index": 1, "name": "BASS", "volume": 0.8, "pan": 0.2,
            "devices": [{"name": "FM-4", "remotes": []}], "clips": [], "automation": [],
        },
    ],
    "effect_tracks": [{"index": 0, "name": "REV", "volume": 0.6, "pan": 0.5}],
}


def test_script_is_valid_python():
    compile(to_script(SAMPLE, project_label="t"), "<gen>", "exec")


def test_song_open():
    s = to_script(SAMPLE)
    assert "from openwig import Song" in s
    assert "Song(tempo=128" in s
    assert "bars=16" in s          # max clip end 16+48=64 beats -> 16 bars


def test_track_device_and_mix():
    s = to_script(SAMPLE)
    assert "s.track('KICK', device='v9 Kick')" in s
    assert ".fader(0.79)" in s
    assert ".arm(True)" in s
    assert ".fx('Saturator', Drive=0.2)" in s


def test_pan_normalized_to_signed():
    # snapshot pan 0.2 -> signed (0.2-0.5)*2 = -0.6
    assert ".pan(-0.6)" in to_script(SAMPLE)


def test_notes_are_clip_relative():
    s = to_script(SAMPLE)
    assert "(16, 48, [" in s                 # clip placed at start 16, dur 48
    assert "(36, 0, 0.25, 1)" in s           # note start RELATIVE to the clip
    assert "(36, 1, 0.25, 0.9)" in s


def test_automation_volume_normalized():
    # volume breakpoints are raw (native): raw 1.0 -> normalized 1/1.2599 = 0.7937
    s = to_script(SAMPLE)
    assert ".automate('volume', [(0, 0), (1, 0.7937)])" in s


def test_automation_pan_normalized():
    # pan raw -1..1 -> normalized (raw+1)/2: -1 -> 0, 1 -> 1
    data = {"tempo": 120, "tracks": [{
        "index": 0, "name": "T", "volume": 0.8,
        "devices": [{"name": "FM-4", "remotes": []}], "clips": [],
        "automation": [{"param": "pan_value_atom",
                        "breakpoints": [{"time": 0, "value": -1.0}, {"time": 4, "value": 1.0}]}],
    }], "effect_tracks": []}
    assert ".automate('pan', [(0, 0), (4, 1)])" in to_script(data)


def test_automation_remote_normalized():
    # a normalized remote target: each breakpoint carries its precomputed `nvalue`
    # (Bitwig's normalize fn), so recreate emits those directly
    data = {"tempo": 120, "tracks": [{
        "index": 0, "name": "T", "volume": 0.8,
        "devices": [{"name": "Polysynth", "remotes": []}, {"name": "Delay-1", "remotes": []}],
        "clips": [],
        "automation": [{"param": "decimal_value_atom",
                        "breakpoints": [{"time": 0, "value": 15.0, "nvalue": 0.0},
                                        {"time": 4, "value": 100.0, "nvalue": 1.0}],
                        "target": {"kind": "remote", "device_index": 1, "remote_index": 4,
                                   "device": "Delay-1", "param": "Low", "normalized": True}}],
    }], "effect_tracks": []}
    s = to_script(data)
    assert ".automate('remote', [(0, 0), (4, 1)], remote_index=4)" in s


def test_automation_remote_uncalibrated_keeps_raw():
    # resolved target but no calibration -> raw values + a warning comment
    data = {"tempo": 120, "tracks": [{
        "index": 0, "name": "T", "volume": 0.8,
        "devices": [{"name": "Polysynth", "remotes": []}], "clips": [],
        "automation": [{"param": "decimal_value_atom",
                        "breakpoints": [{"time": 0, "value": 51.0}, {"time": 4, "value": 135.0}],
                        "target": {"kind": "remote", "device_index": 0, "remote_index": 5,
                                   "device": "Polysynth", "param": "X"}}],
    }], "effect_tracks": []}
    s = to_script(data)
    assert "uncalibrated" in s
    assert ".automate('remote', [(0, 51), (4, 135)], remote_index=5)" in s


def test_effect_track_emitted():
    assert "s.fx_track('REV')" in to_script(SAMPLE)
