"""Diagnostics tests - verify run_selftest / _delete_all_named / _print_selftest
WITHOUT a live Bitwig.

A `FakeBridge` (same idea as tests/test_api.py) records every outgoing
(method, params) and answers the read calls the resolver self-test makes:
``resolver.classes``, ``state.snapshot``, ``track.create`` / ``track.delete``,
``track.select``, ``resolver.probe`` and ``resolver.result``. That lets us assert
the resolver's half of the contract (the probe track is created and always
deleted, the report shape, the not-connected short-circuit) and the doctor's
capability-matrix printout, none of which needs a real bridge.
"""
import pytest

import openwig.diagnostics as diag
from openwig.diagnostics import (
    PROBE_TRACK,
    run_selftest,
    _delete_all_named,
    _find_track_index,
)
from openwig.cli.install import _print_selftest


# ── a scriptable fake bridge ────────────────────────────────────────────────────

GOOD_REPORT = {
    "ok": True,
    "capabilities": {
        "automation_write": {"ok": True, "detail": "wrote 2 points", "via": "offline"},
        "clip_create": {"ok": True, "detail": "created clip"},
        "descriptor_read": {"ok": True, "detail": "read back"},
        "serialize": {"ok": True, "detail": "serialized doc", "via": "WZK"},
        "normalize": {"ok": True, "detail": "normalized value", "via": "fj"},
    },
    "discovered": {"al_accessor": "aL", "insert": "ins", "value_base": "vB"},
}


class FakeBridge:
    """Records outgoing requests; answers the read calls run_selftest makes.

    Tracks live in ``self.tracks`` (list of {"name", "index"} dicts). track.create
    appends one, track.delete removes by index and re-numbers, so we can assert the
    probe track is gone at the end. ``classes`` / ``probe_report`` / ``probe_error``
    are scripted per test. ``appear_after`` controls how many snapshot polls happen
    before the freshly created probe track becomes visible (None = never appears).
    """

    def __init__(self, *, connected=True, classes=None, probe_report=None,
                 probe_error=None, appear_after=0, seed_tracks=None):
        self.calls = []                       # [(method, params), ...] in order
        self.connected = connected
        self.classes = classes if classes is not None else {
            "bitwig": "5.2", "classes": {"Foo": True, "Bar": True}}
        self.probe_report = probe_report
        self.probe_error = probe_error
        self.appear_after = appear_after      # snapshot polls before probe shows
        self.tracks = list(seed_tracks or [])
        self._snap_polls = 0                   # counts post-create snapshot reads
        self._created_probe = False

    # --- the bridge surface run_selftest uses ---
    def wait_connected(self, _timeout):
        return self.connected

    def request(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "resolver.classes":
            return dict(self.classes)
        if method == "state.snapshot":
            if self._created_probe and self.appear_after is not None:
                if self._snap_polls >= self.appear_after:
                    if not any(t["name"] == PROBE_TRACK for t in self.tracks):
                        self.tracks.append(
                            {"name": PROBE_TRACK, "index": len(self.tracks)})
                self._snap_polls += 1
            return {"tracks": list(self.tracks)}
        if method == "track.create":
            if params.get("name") == PROBE_TRACK:
                self._created_probe = True
            return {}
        if method == "track.delete":
            idx = params.get("index")
            self.tracks = [t for t in self.tracks if t["index"] != idx]
            for i, t in enumerate(self.tracks):   # re-number like a real bank
                t["index"] = i
            return {}
        if method == "track.select":
            return {}
        if method == "resolver.result":
            return {"report": self.probe_report, "error": self.probe_error}
        return {}

    def request_op(self, method, params=None, **_kw):
        return self.request(method, params)

    def request_insert(self, method, params=None, **_kw):
        return self.request(method, params)

    # --- assertion helpers ---
    def methods(self):
        return [m for m, _ in self.calls]

    def created_names(self):
        return [p.get("name") for m, p in self.calls if m == "track.create"]

    def probe_track_count(self):
        return sum(1 for t in self.tracks if t["name"] == PROBE_TRACK)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """run_selftest polls with time.sleep(); stub it so the loop does not wait."""
    monkeypatch.setattr(diag.time, "sleep", lambda *a, **k: None)


# ── run_selftest: happy path ─────────────────────────────────────────────────────

def test_selftest_happy_path_returns_report_connected():
    b = FakeBridge(probe_report=dict(GOOD_REPORT))
    rep = run_selftest(b)
    assert rep["connected"] is True
    assert rep["ok"] is True
    assert rep["capabilities"]["automation_write"]["ok"] is True
    assert rep["capabilities"]["clip_create"]["ok"] is True
    assert rep["capabilities"]["descriptor_read"]["ok"] is True
    assert rep["capabilities"]["serialize"]["ok"] is True
    assert rep["capabilities"]["normalize"]["ok"] is True


def test_selftest_happy_path_creates_and_deletes_probe_track():
    b = FakeBridge(probe_report=dict(GOOD_REPORT))
    run_selftest(b)
    # the probe track was created ...
    assert PROBE_TRACK in b.created_names()
    # ... and cleaned up by the finally block (none left in the bank)
    assert b.probe_track_count() == 0
    assert "track.delete" in b.methods()


def test_selftest_happy_path_selects_probe_then_probes():
    b = FakeBridge(probe_report=dict(GOOD_REPORT))
    run_selftest(b)
    ms = b.methods()
    # ordering contract: create -> select -> probe -> result
    assert ms.index("track.create") < ms.index("track.select")
    assert ms.index("track.select") < ms.index("resolver.probe")
    assert ms.index("resolver.probe") < ms.index("resolver.result")


# ── run_selftest: not connected ──────────────────────────────────────────────────

def test_selftest_not_connected_short_circuits():
    b = FakeBridge(connected=False)
    rep = run_selftest(b)
    assert rep == {"connected": False}
    # no probe track was created and nothing else was requested
    assert "track.create" not in b.methods()
    assert "resolver.classes" not in b.methods()


# ── run_selftest: probe track never appears ──────────────────────────────────────

def test_selftest_probe_never_appears_returns_classes_and_error():
    b = FakeBridge(appear_after=None, probe_report=dict(GOOD_REPORT))
    rep = run_selftest(b)
    assert rep["connected"] is True
    assert rep["classes"] == {"Foo": True, "Bar": True}
    assert "probe track did not appear" in rep["error"]
    # never selected / probed because the track was not found
    assert "resolver.probe" not in b.methods()
    # finally still ran cleanup
    assert b.probe_track_count() == 0


# ── run_selftest: self-heal leftover probe track ─────────────────────────────────

def test_selftest_self_heals_leftover_probe_track():
    leftover = [{"name": PROBE_TRACK, "index": 0}]
    b = FakeBridge(probe_report=dict(GOOD_REPORT), seed_tracks=leftover)
    rep = run_selftest(b)
    assert rep["connected"] is True
    # the pre-existing leftover was cleared and nothing is left behind
    assert b.probe_track_count() == 0


# ── run_selftest: report falls back to base when result has no report ─────────────

def test_selftest_no_report_falls_back_to_base_with_error():
    b = FakeBridge(probe_report=None, probe_error="resolver blew up")
    rep = run_selftest(b)
    assert rep["connected"] is True
    assert rep["classes"] == {"Foo": True, "Bar": True}
    assert rep["error"] == "resolver blew up"


# ── _find_track_index ────────────────────────────────────────────────────────────

def test_find_track_index_by_name():
    b = FakeBridge(seed_tracks=[
        {"name": "Drums", "index": 0}, {"name": PROBE_TRACK, "index": 1}])
    assert _find_track_index(b, PROBE_TRACK) == 1
    assert _find_track_index(b, "nope") is None


# ── _delete_all_named ────────────────────────────────────────────────────────────

def test_delete_all_named_removes_every_duplicate():
    b = FakeBridge(seed_tracks=[
        {"name": PROBE_TRACK, "index": 0},
        {"name": "keep", "index": 1},
        {"name": PROBE_TRACK, "index": 2},
        {"name": PROBE_TRACK, "index": 3},
    ])
    _delete_all_named(b, PROBE_TRACK)
    assert b.probe_track_count() == 0
    assert [t["name"] for t in b.tracks] == ["keep"]


def test_delete_all_named_noop_when_absent():
    b = FakeBridge(seed_tracks=[{"name": "keep", "index": 0}])
    _delete_all_named(b, PROBE_TRACK)
    assert "track.delete" not in b.methods()


def test_delete_all_named_respects_limit():
    # five duplicates but a limit of 2 -> only two deletes attempted
    b = FakeBridge(seed_tracks=[
        {"name": PROBE_TRACK, "index": i} for i in range(5)])
    _delete_all_named(b, PROBE_TRACK, limit=2)
    assert b.methods().count("track.delete") == 2
    assert b.probe_track_count() == 3


# ── _print_selftest ──────────────────────────────────────────────────────────────

# the resolver class-load map: all nine internal classes present on a good build
_ALL_CLASSES = {
    "fj": True, "oJk": True, "a1x": True, "X2S": True, "alU": True,
    "SZo": True, "ZjS": True, "BOg": True, "WZK": True,
}


def _rep_all_ok():
    return {
        "connected": True,
        "ok": True,
        "classes": dict(_ALL_CLASSES),
        "capabilities": {
            "automation_write": {"ok": True, "detail": "wrote points"},
            "clip_create": {"ok": True, "detail": "made clip"},
            "descriptor_read": {"ok": True, "detail": "read back"},
            "serialize": {"ok": True, "detail": "serialized doc"},
            "normalize": {"ok": True, "detail": "normalized value"},
        },
        "discovered": {"al_accessor": "aL", "insert": "ins", "value_base": "vB"},
    }


def test_print_selftest_all_ok_returns_zero(capsys):
    rc = _print_selftest(_rep_all_ok())
    out = capsys.readouterr().out
    assert rc == 0
    assert "9/9 internal classes load" in out
    assert "automation" in out
    assert "clip create" in out
    assert "descriptor" in out
    assert out.count("OK  ") == 5
    assert "all reflection paths verified" in out


def test_print_selftest_prints_all_five_capability_lines(capsys):
    rc = _print_selftest(_rep_all_ok())
    out = capsys.readouterr().out
    assert rc == 0
    # every one of the five capability labels appears on its own printed line
    for label in ("automation", "clip create", "descriptor", "serialize", "normalize"):
        assert label in out


@pytest.mark.parametrize("cap", ["serialize", "normalize"])
def test_print_selftest_failed_new_capability_returns_3(capsys, cap):
    rep = _rep_all_ok()
    rep["ok"] = False
    rep["capabilities"][cap] = {"ok": False, "detail": "broke"}
    rc = _print_selftest(rep)
    out = capsys.readouterr().out
    assert rc == 3
    # the FAIL line is printed for the failing NEW capability, with its detail
    assert "FAIL" in out
    assert "broke" in out
    assert "SOME paths failed" in out


def test_print_selftest_failed_capability_returns_3(capsys):
    rep = _rep_all_ok()
    rep["ok"] = False
    rep["capabilities"]["clip_create"] = {"ok": False, "detail": "no clip"}
    rc = _print_selftest(rep)
    out = capsys.readouterr().out
    assert rc >= 3
    assert "FAIL" in out
    assert "SOME paths failed" in out


def test_print_selftest_not_connected_returns_2(capsys):
    rc = _print_selftest({"connected": False})
    out = capsys.readouterr().out
    assert rc == 2
    assert "bridge dropped" in out


def test_print_selftest_no_capabilities_returns_3(capsys):
    rep = {"connected": True, "classes": {"Foo": True},
           "error": "probe track did not appear"}
    rc = _print_selftest(rep)
    out = capsys.readouterr().out
    assert rc == 3
    assert "NOT RUN" in out
    assert "probe track did not appear" in out


def test_print_selftest_reports_missing_classes(capsys):
    rep = _rep_all_ok()
    # two of the nine internal classes failed to load on this build
    rep["classes"] = dict(_ALL_CLASSES)
    rep["classes"]["BOg"] = False
    rep["classes"]["WZK"] = False
    _print_selftest(rep)
    out = capsys.readouterr().out
    assert "7/9 internal classes load" in out
    assert "MISSING: " in out
    assert "BOg" in out
    assert "WZK" in out
