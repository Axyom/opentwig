"""Diagnostics tests - verify run_selftest / _occupied / _delete_index /
_print_selftest WITHOUT a live Bitwig.

A `FakeBridge` (same idea as tests/test_api.py) records every outgoing
(method, params) and answers the read calls the resolver self-test makes:
``resolver.classes``, ``state.snapshot``, ``track.create`` / ``track.delete``,
``track.select``, ``resolver.probe`` and ``resolver.result``. That lets us assert
the resolver's half of the contract (the probe track is created and always
deleted by INDEX-DIFF, the report shape, the not-connected short-circuit) and the
doctor's capability-matrix printout, none of which needs a real bridge.
"""
import pytest

import openwig.diagnostics as diag
from openwig.bridge import BridgeError
from openwig.diagnostics import (
    PROBE_TRACK,
    run_selftest,
    _occupied,
    _delete_index,
)
from openwig.cli.install import _print_selftest


# -- a scriptable fake bridge ------------------------------------------------------

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
    appends one (with ``create_name`` if given, else the requested name) once the
    snapshot has been polled ``appear_after`` times; track.delete removes by index,
    so we can assert the probe slot is gone at the end. ``classes`` / ``probe_report``
    / ``probe_error`` are scripted per test. ``appear_after=None`` means the new slot
    never appears (the probe-never-shows case). ``create_name`` lets a test simulate a
    FAILED RENAME: the created track lands under a name other than PROBE_TRACK.
    """

    def __init__(self, *, connected=True, classes=None, probe_report=None,
                 probe_error=None, appear_after=0, seed_tracks=None,
                 create_name=None, delete_raises=False):
        self.calls = []                       # [(method, params), ...] in order
        self.connected = connected
        self.classes = classes if classes is not None else {
            "bitwig": "5.2", "classes": {"Foo": True, "Bar": True}}
        self.probe_report = probe_report
        self.probe_error = probe_error
        self.appear_after = appear_after      # snapshot polls before the slot shows
        self.tracks = list(seed_tracks or [])
        self.create_name = create_name        # if set, the appended track uses this name
        self.delete_raises = delete_raises    # track.delete raises BridgeError
        self._pending_name = None             # name to append once it "appears"
        self._snap_polls = 0                  # counts post-create snapshot reads
        self._next_index = (max((t["index"] for t in self.tracks), default=-1) + 1)

    # --- the bridge surface run_selftest uses ---
    def wait_connected(self, _timeout):
        return self.connected

    def request(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "resolver.classes":
            return dict(self.classes)
        if method == "state.snapshot":
            if self._pending_name is not None and self.appear_after is not None:
                if self._snap_polls >= self.appear_after:
                    self.tracks.append(
                        {"name": self._pending_name, "index": self._next_index})
                    self._next_index += 1
                    self._pending_name = None
                self._snap_polls += 1
            return {"tracks": [dict(t) for t in self.tracks]}
        if method == "track.create":
            self._pending_name = self.create_name or params.get("name")
            return {}
        if method == "track.delete":
            if self.delete_raises:
                raise BridgeError("cannot delete")
            idx = params.get("index")
            self.tracks = [t for t in self.tracks if t["index"] != idx]
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

    def track_names(self):
        return [t["name"] for t in self.tracks]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """run_selftest polls with time.sleep(); stub it so the loop does not wait."""
    monkeypatch.setattr(diag.time, "sleep", lambda *a, **k: None)


# -- run_selftest: happy path ------------------------------------------------------

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
    # seed a pre-existing track so we can assert the bank returns to this exact state
    pre = [{"name": "Drums", "index": 0}]
    b = FakeBridge(probe_report=dict(GOOD_REPORT), seed_tracks=[dict(t) for t in pre])
    run_selftest(b)
    # the probe track was created ...
    assert PROBE_TRACK in b.created_names()
    # ... and cleaned up by the finally block (none left in the bank)
    assert b.probe_track_count() == 0
    assert "track.delete" in b.methods()
    # the track list returns to its pre-probe state
    assert b.track_names() == ["Drums"]


def test_selftest_happy_path_selects_probe_then_probes():
    b = FakeBridge(probe_report=dict(GOOD_REPORT))
    run_selftest(b)
    ms = b.methods()
    # ordering contract: create -> select -> probe -> result
    assert ms.index("track.create") < ms.index("track.select")
    assert ms.index("track.select") < ms.index("resolver.probe")
    assert ms.index("resolver.probe") < ms.index("resolver.result")


def test_selftest_picks_only_the_new_slot_by_index_diff():
    # two pre-existing tracks; the probe slot must be the NEW (max new) index
    b = FakeBridge(probe_report=dict(GOOD_REPORT), seed_tracks=[
        {"name": "Drums", "index": 0}, {"name": "Bass", "index": 1}])
    run_selftest(b)
    # the select hit the new slot (index 2), not a pre-existing one
    selected = [p.get("index") for m, p in b.calls if m == "track.select"]
    assert selected == [2]
    # cleanup left the two originals intact
    assert b.track_names() == ["Drums", "Bass"]


# -- run_selftest: index-diff cleanup of a FAILED RENAME ---------------------------

def test_selftest_cleans_up_failed_rename_slot():
    # the post-create rename silently fails: the new track lands as "Inst 2",
    # NOT PROBE_TRACK. run_selftest must still delete it by index-diff.
    pre = [{"name": "Drums", "index": 0}]
    b = FakeBridge(probe_report=dict(GOOD_REPORT),
                   seed_tracks=[dict(t) for t in pre],
                   create_name="Inst 2")
    rep = run_selftest(b)
    assert rep["connected"] is True
    # the differently-named new slot was removed in the finally
    assert "Inst 2" not in b.track_names()
    assert b.track_names() == ["Drums"]
    assert "track.delete" in b.methods()


# -- run_selftest: not connected ---------------------------------------------------

def test_selftest_not_connected_short_circuits():
    b = FakeBridge(connected=False)
    rep = run_selftest(b)
    assert rep == {"connected": False}
    # no probe track was created and nothing else was requested
    assert "track.create" not in b.methods()
    assert "resolver.classes" not in b.methods()


# -- run_selftest: probe track never appears ---------------------------------------

def test_selftest_probe_never_appears_returns_classes_and_error():
    b = FakeBridge(appear_after=None, probe_report=dict(GOOD_REPORT))
    rep = run_selftest(b)
    assert rep["connected"] is True
    assert rep["classes"] == {"Foo": True, "Bar": True}
    assert rep["bitwig"] == "5.2"
    assert "probe track did not appear" in rep["error"]
    # never selected / probed because the slot was not found
    assert "resolver.probe" not in b.methods()
    assert "track.select" not in b.methods()
    # finally still ran cleanup (nothing leaked)
    assert b.probe_track_count() == 0


# -- run_selftest: self-heal leftover probe track ----------------------------------

def test_selftest_self_heals_leftover_probe_track():
    # a leftover probe track from a prior run is in `before`, so it is NOT in the
    # index-diff; it should stay (run_selftest only removes slots it created).
    leftover = [{"name": PROBE_TRACK, "index": 0}]
    b = FakeBridge(probe_report=dict(GOOD_REPORT), seed_tracks=leftover)
    rep = run_selftest(b)
    assert rep["connected"] is True
    # the slot it actually created during the probe is cleaned up; track list
    # returns to the pre-probe state (the leftover is untouched).
    assert b.track_names() == [PROBE_TRACK]


# -- run_selftest: report falls back to base when result has no report --------------

def test_selftest_no_report_falls_back_to_base_with_error():
    b = FakeBridge(probe_report=None, probe_error="resolver blew up")
    rep = run_selftest(b)
    assert rep["connected"] is True
    assert rep["classes"] == {"Foo": True, "Bar": True}
    assert rep["error"] == "resolver blew up"


# -- _occupied ---------------------------------------------------------------------

def test_occupied_returns_only_named_indices():
    b = FakeBridge(seed_tracks=[
        {"name": "Drums", "index": 0},
        {"name": "", "index": 1},          # empty name -> not occupied
        {"name": None, "index": 2},        # no name -> not occupied
        {"name": "Bass", "index": 3},
    ])
    assert _occupied(b) == {0, 3}


def test_delete_index_swallows_bridge_error():
    b = FakeBridge(seed_tracks=[{"name": "Drums", "index": 0}], delete_raises=True)
    # must not raise even though the bridge rejects the delete
    _delete_index(b, 0)
    assert "track.delete" in b.methods()


# -- _print_selftest ---------------------------------------------------------------

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


# -- _print_selftest: NEW optional report fields (reader / cache / symbol_source) ---

def test_print_selftest_prints_reader_cache_written_and_symbol_source(capsys):
    rep = _rep_all_ok()
    rep["reader"] = {"mX_": "mX_", "KRt": "KRt", "bf": "bf", "ngq": "ngq",
                     "nI_": "nI_", "Xzy": "Xzy", "uEK": "uEK"}
    rep["cache"] = {"written": True, "path": "/home/u/.openwig/symbols.json"}
    rep["symbol_source"] = "resolved live (cached)"
    rc = _print_selftest(rep)
    out = capsys.readouterr().out
    assert rc == 0
    # reader line names the resolved reader symbols
    assert "reader" in out
    assert "mX_" in out
    assert "ngq" in out
    assert "uEK" in out
    # cache line shows the written path
    assert "cache" in out
    assert "/home/u/.openwig/symbols.json" in out
    # symbol source line
    assert "symbol source" in out
    assert "resolved live (cached)" in out


def test_print_selftest_prints_cache_not_written_reason(capsys):
    rep = _rep_all_ok()
    rep["cache"] = {"written": False, "reason": "read-only filesystem"}
    rc = _print_selftest(rep)
    out = capsys.readouterr().out
    assert rc == 0
    assert "not written" in out
    assert "read-only filesystem" in out


# -- _print_selftest: NEW optional "commands" report field --------------------------

def _commands_block(resolved=True):
    return {
        "clipCmd": {"cls": "X2S", "field": "fiU", "factory": "qgm",
                    "exec": "r3B", "opid": 7350},
        "noteCmd": {"cls": "alU", "field": "r3B", "factory": "XaN",
                    "exec": "r3B", "opid": 7349},
        "resolved": resolved,
        "instantiated": 12569,
    }


def test_print_selftest_prints_commands_by_opid_when_resolved(capsys):
    rep = _rep_all_ok()
    rep["commands"] = _commands_block(resolved=True)
    rc = _print_selftest(rep)
    out = capsys.readouterr().out
    assert rc == 0
    # the commands line is printed with the "by op-id" tag ...
    assert "commands" in out
    assert "by op-id" in out
    assert "SEED (op-id lookup failed)" not in out
    # ... and names clip / note as <cls>.<factory>
    assert "X2S.qgm" in out
    assert "alU.XaN" in out


def test_print_selftest_prints_commands_seed_when_not_resolved(capsys):
    rep = _rep_all_ok()
    rep["commands"] = _commands_block(resolved=False)
    rc = _print_selftest(rep)
    out = capsys.readouterr().out
    assert rc == 0
    # the seed/fallback tag replaces the "by op-id" tag
    assert "SEED (op-id lookup failed)" in out
    assert "by op-id" not in out
    # the clip / note class.factory are still printed
    assert "X2S.qgm" in out
    assert "alU.XaN" in out


def test_print_selftest_no_commands_key_prints_no_commands_line(capsys):
    rep = _rep_all_ok()
    assert "commands" not in rep
    rc = _print_selftest(rep)
    out = capsys.readouterr().out
    assert rc == 0
    # no commands line is printed and nothing crashes
    assert "by op-id" not in out
    assert "SEED (op-id lookup failed)" not in out
    assert "commands     :" not in out
