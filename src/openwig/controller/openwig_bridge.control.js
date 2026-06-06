// openwig_bridge.control.js  v1.0
//
// Bitwig controller script that exposes the Controller Scripting API to
// openwig over a TCP socket on 127.0.0.1:7777.
//
// Protocol (newline-delimited JSON, JSON-RPC 2.0 flavored):
//   request  : {"jsonrpc":"2.0","id":N,"method":"transport.play","params":{}}
//   response : {"jsonrpc":"2.0","id":N,"result":...}
//              {"jsonrpc":"2.0","id":N,"error":{"code":-32000,"message":"..."}}
//   notify   : {"jsonrpc":"2.0","method":"connected","params":{...snapshot...}}
//
// Each message is one line: compact JSON + "\n".
//
// Install: `openwig install` copies this file to Bitwig's Controller Scripts dir.
// Enable:  Bitwig > Settings > Controllers > Add > openwig > OpenwigBridge
// (one-time; Bitwig remembers it across launches).

loadAPI(18);

host.defineController(
    "openwig",
    "OpenwigBridge",
    "1.0.0",
    "b17c9000-c0de-4a1d-bb1d-000000000002",
    "openwig"
);

host.defineMidiPorts(0, 0);

// ── config ──────────────────────────────────────────────────────────────────

var PORT        = 7777;  // must match bridge_client.py
var NUM_TRACKS  = 32;
var NUM_SENDS   = 2;
var NUM_CLIPS   = 16;
var NUM_SCENES  = 16;
var NUM_REMOTE  = 8;
var NUM_CUES    = 16;

// ── globals ─────────────────────────────────────────────────────────────────

var transport, trackBank, effectTrackBank, masterTrack, sceneBank;
var cursorTrack, cursorDevice, remoteControlsPage, cursorTrackDeviceBank;
var masterCursorDevice, masterRemotes, masterDeviceBank;
var gSerializeB64 = null, gSerializeErr = null;
var gWalk = null, gWalkErr = null;           // generic descriptor-graph reader result (JSON string)
var gOpsDone = 0;                            // count of finished document-thread ops (completion signal; see ops.done)
var gClipNotes = {}, gNoteScroll = 0, gNoteStepSize = 0.25;
var arranger, cueMarkerBank;
var arrangerClip, launcherClip, gCursorClip;
var application;

var gSocket     = null;
var gConnection = null;
var gState      = null;   // mirror updated by value observers

var Charset     = Java.type("java.nio.charset.StandardCharsets");
var JString     = Java.type("java.lang.String");
var JUUID       = Java.type("java.util.UUID");

// ── logging (file, since the Bitwig controller console is GUI-only) ───────────

// Per-OS log location. Resolved at script load via java.lang.System so it works
// on every customer machine (the dev-machine hardcoded path is gone).
//   Windows : %LOCALAPPDATA%\openwig\openwig_bridge.log
//   macOS   : ~/Library/Logs/openwig/openwig_bridge.log
//   Linux   : ${XDG_STATE_HOME:-~/.local/state}/openwig/openwig_bridge.log
function _resolveLogPath() {
    var JSys  = Java.type("java.lang.System");
    var JFile = Java.type("java.io.File");
    var osRaw = ("" + JSys.getProperty("os.name")).toLowerCase();
    var home  = "" + JSys.getProperty("user.home");
    var dir, sep = "" + JFile.separator;
    if (osRaw.indexOf("win") === 0) {
        var local = JSys.getenv("LOCALAPPDATA");
        dir = (local ? ("" + local) : (home + sep + "AppData" + sep + "Local")) + sep + "openwig";
    } else if (osRaw.indexOf("mac") >= 0 || osRaw.indexOf("darwin") >= 0) {
        dir = home + "/Library/Logs/openwig";
    } else {
        var xdg = JSys.getenv("XDG_STATE_HOME");
        dir = (xdg ? ("" + xdg) : (home + "/.local/state")) + "/openwig";
    }
    try { new JFile(dir).mkdirs(); } catch (e) {}
    return dir + sep + "openwig_bridge.log";
}

var LOG_FILE  = _resolveLogPath();
var logWriter = null;

function openLog() {
    try {
        var FileWriter     = Java.type("java.io.FileWriter");
        var BufferedWriter = Java.type("java.io.BufferedWriter");
        logWriter = new BufferedWriter(new FileWriter(LOG_FILE, false));
    } catch (e) { host.errorln("could not open log at " + LOG_FILE + ": " + e); }
}

function log(msg) {
    host.println(msg);
    flog(msg);
}

// file-only log, safe to call from any thread (no host.* calls)
function flog(msg) {
    if (logWriter) { try { logWriter.write("" + msg); logWriter.newLine(); logWriter.flush(); } catch (e) {} }
}

// ── init ────────────────────────────────────────────────────────────────────

function init() {
    openLog();
    transport   = host.createTransport();
    trackBank   = host.createMainTrackBank(NUM_TRACKS, NUM_SENDS, NUM_CLIPS);
    masterTrack = host.createMasterTrack(0);
    sceneBank   = host.createSceneBank(NUM_SCENES);
    cursorTrack = host.createCursorTrack("openwig-cursor", "Openwig Cursor", NUM_SENDS, NUM_CLIPS, true);
    cursorDevice       = cursorTrack.createCursorDevice();
    remoteControlsPage = cursorDevice.createCursorRemoteControlsPage(NUM_REMOTE);
    // Mark properties needed by device.all_remote_pages - otherwise .get() on
    // pageCount()/selectedPageIndex()/getName() throws "call markInterested()
    // or add at least one observer".
    remoteControlsPage.pageCount().markInterested();
    remoteControlsPage.selectedPageIndex().markInterested();
    remoteControlsPage.getName().markInterested();
    for (var _rcp = 0; _rcp < NUM_REMOTE; _rcp++) {
        var _rcpParam = remoteControlsPage.getParameter(_rcp);
        _rcpParam.exists().markInterested();
        _rcpParam.name().markInterested();
        _rcpParam.value().markInterested();
    }
    masterCursorDevice = masterTrack.createCursorDevice();
    masterRemotes      = masterCursorDevice.createCursorRemoteControlsPage(NUM_REMOTE);
    masterCursorDevice.name().markInterested();
    for (var _mr = 0; _mr < NUM_REMOTE; _mr++) {
        var _mp = masterRemotes.getParameter(_mr);
        _mp.exists().markInterested(); _mp.name().markInterested(); _mp.value().markInterested();
    }
    masterDeviceBank = masterTrack.createDeviceBank(16);   // for clearing the master FX chain
    for (var _md = 0; _md < 16; _md++) {
        masterDeviceBank.getItemAt(_md).exists().markInterested();
        masterDeviceBank.getItemAt(_md).name().markInterested();
    }
    // cursor-track device chain - lets track.device_count report how many devices are
    // loaded, so the SDK can wait for an insert to finish loading instead of a fixed sleep.
    cursorTrackDeviceBank = cursorTrack.createDeviceBank(16);
    for (var _cd = 0; _cd < 16; _cd++) cursorTrackDeviceBank.getItemAt(_cd).exists().markInterested();
    // send/effect (return) tracks live in a SEPARATE bank from the main track bank -> need
    // their own bank so project.clear can delete them too.
    try {
        effectTrackBank = host.createEffectTrackBank(16, NUM_CLIPS);
        for (var _et = 0; _et < 16; _et++) effectTrackBank.getItemAt(_et).exists().markInterested();
    } catch (e) { host.errorln("createEffectTrackBank failed: " + e); }
    arranger      = host.createArranger(-1);
    cueMarkerBank = arranger.createCueMarkerBank(NUM_CUES);
    application   = host.createApplication();

    try { arrangerClip = host.createArrangerCursorClip(NUM_CLIPS, 128); }
    catch (e) { host.errorln("createArrangerCursorClip failed: " + e); }
    try { launcherClip = host.createLauncherCursorClip(NUM_CLIPS, 128); }
    catch (e) { host.errorln("createLauncherCursorClip failed: " + e); }
    gCursorClip = arrangerClip || launcherClip;

    // note-step observer: read MIDI notes of the focused arranger clip (key/start/dur/vel).
    // Records NoteOn steps into gClipNotes keyed by absolute step + key (scroll-aware).
    try {
        var NS_State = Java.type("com.bitwig.extension.controller.api.NoteStep$State");
        if (arrangerClip) {
            arrangerClip.addNoteStepObserver(function (ns) {
                try {
                    if (("" + ns.state()) === ("" + NS_State.NoteOn)) {
                        var absStep = gNoteScroll + ns.x();
                        gClipNotes[absStep + ":" + ns.y()] = {
                            key: ns.y(), channel: ns.channel(),
                            start: absStep * gNoteStepSize, dur: ns.duration(), vel: ns.velocity() };
                    }
                } catch (e) {}
            });
        }
    } catch (e) { host.errorln("noteStepObserver setup: " + e); }

    buildState();
    wireObservers();
    openSocket();

    host.println("Openwig bridge init complete; opening port " + PORT);
}

// ── state mirror ──────────────────────────────────────────────────────────────

function buildState() {
    gState = {
        transport: {
            playing: false, recording: false, loop: false,
            metronome: false, overdub: false, tempo: null, position: null
        },
        arranger: {
            playback_follow: false, cue_markers_visible: false,
            timeline_visible: false, io_visible: false,
            effect_tracks_visible: false, clip_launcher_visible: false,
            double_row: false
        },
        device: { exists: false, name: "", remotes: [] },
        tracks: [],
        effect_tracks: []
    };
    for (var r = 0; r < NUM_REMOTE; r++) {
        gState.device.remotes.push({ index: r, exists: false, name: "", value: 0, disp: "" });
    }
    for (var i = 0; i < NUM_TRACKS; i++) {
        gState.tracks.push({
            index: i, exists: false, name: "",
            volume: 0, volume_db: "", pan: 0.5, pan_disp: "",
            mute: false, solo: false, arm: false
        });
    }
    for (var k = 0; k < 16; k++) {
        gState.effect_tracks.push({
            index: k, exists: false, name: "",
            volume: 0, pan: 0.5, mute: false, solo: false
        });
    }
}

function wireObservers() {
    // transport
    transport.isPlaying().markInterested();
    transport.isPlaying().addValueObserver(function (v) { gState.transport.playing = v; });
    transport.isArrangerRecordEnabled().markInterested();
    transport.isArrangerRecordEnabled().addValueObserver(function (v) { gState.transport.recording = v; });
    transport.isArrangerLoopEnabled().markInterested();
    transport.isArrangerLoopEnabled().addValueObserver(function (v) { gState.transport.loop = v; });
    transport.isMetronomeEnabled().markInterested();
    transport.isMetronomeEnabled().addValueObserver(function (v) { gState.transport.metronome = v; });
    transport.isArrangerOverdubEnabled().markInterested();
    transport.isArrangerOverdubEnabled().addValueObserver(function (v) { gState.transport.overdub = v; });
    // tempo + position change too often to mirror via observers; markInterested
    // so .getRaw()/.get() return live values, then read them in snapshot().
    transport.tempo().markInterested();
    transport.tempo().value().markInterested();
    transport.playPosition().markInterested();

    // arranger panels
    arranger.isPlaybackFollowEnabled().markInterested();
    arranger.isPlaybackFollowEnabled().addValueObserver(function (v) { gState.arranger.playback_follow = v; });
    arranger.areCueMarkersVisible().markInterested();
    arranger.areCueMarkersVisible().addValueObserver(function (v) { gState.arranger.cue_markers_visible = v; });
    arranger.isTimelineVisible().markInterested();
    arranger.isTimelineVisible().addValueObserver(function (v) { gState.arranger.timeline_visible = v; });
    arranger.isIoSectionVisible().markInterested();
    arranger.isIoSectionVisible().addValueObserver(function (v) { gState.arranger.io_visible = v; });
    arranger.areEffectTracksVisible().markInterested();
    arranger.areEffectTracksVisible().addValueObserver(function (v) { gState.arranger.effect_tracks_visible = v; });
    arranger.isClipLauncherVisible().markInterested();
    arranger.isClipLauncherVisible().addValueObserver(function (v) { gState.arranger.clip_launcher_visible = v; });
    arranger.hasDoubleRowTrackHeight().markInterested();
    arranger.hasDoubleRowTrackHeight().addValueObserver(function (v) { gState.arranger.double_row = v; });

    // cursor device + remote controls (for plugin-param work)
    cursorDevice.exists().markInterested();
    cursorDevice.exists().addValueObserver(function (v) { gState.device.exists = v; });
    cursorDevice.name().markInterested();
    cursorDevice.name().addValueObserver(function (v) { gState.device.name = "" + v; });
    for (var r = 0; r < NUM_REMOTE; r++) {
        (function (ri) {
            var p = remoteControlsPage.getParameter(ri);
            p.exists().markInterested();
            p.exists().addValueObserver(function (v) { gState.device.remotes[ri].exists = v; });
            p.name().markInterested();
            p.name().addValueObserver(function (v) { gState.device.remotes[ri].name = "" + v; });
            p.value().markInterested();
            p.value().addValueObserver(function (v) { gState.device.remotes[ri].value = v; });
            p.displayedValue().markInterested();
            p.displayedValue().addValueObserver(function (v) { gState.device.remotes[ri].disp = "" + v; });
        })(r);
    }

    // per-track
    for (var i = 0; i < NUM_TRACKS; i++) {
        (function (idx) {
            var t = trackBank.getItemAt(idx);
            t.exists().markInterested();
            t.exists().addValueObserver(function (v) { gState.tracks[idx].exists = v; });
            t.name().markInterested();
            t.name().addValueObserver(function (v) { gState.tracks[idx].name = v; });
            t.volume().markInterested();
            t.volume().value().addValueObserver(function (v) { gState.tracks[idx].volume = v; });
            t.volume().displayedValue().markInterested();
            t.volume().displayedValue().addValueObserver(function (v) { gState.tracks[idx].volume_db = "" + v; });
            t.pan().markInterested();
            t.pan().value().addValueObserver(function (v) { gState.tracks[idx].pan = v; });
            t.pan().displayedValue().markInterested();
            t.pan().displayedValue().addValueObserver(function (v) { gState.tracks[idx].pan_disp = "" + v; });
            t.mute().markInterested();
            t.mute().addValueObserver(function (v) { gState.tracks[idx].mute = v; });
            t.solo().markInterested();
            t.solo().addValueObserver(function (v) { gState.tracks[idx].solo = v; });
            t.arm().markInterested();
            t.arm().addValueObserver(function (v) { gState.tracks[idx].arm = v; });
        })(i);
    }
    // effect / return tracks (separate bank)
    if (effectTrackBank) {
        for (var ei = 0; ei < 16; ei++) {
            (function (idx) {
                var t = effectTrackBank.getItemAt(idx);
                t.exists().markInterested();
                t.exists().addValueObserver(function (v) { gState.effect_tracks[idx].exists = v; });
                t.name().markInterested();
                t.name().addValueObserver(function (v) { gState.effect_tracks[idx].name = "" + v; });
                t.volume().markInterested();
                t.volume().value().addValueObserver(function (v) { gState.effect_tracks[idx].volume = v; });
                t.pan().markInterested();
                t.pan().value().addValueObserver(function (v) { gState.effect_tracks[idx].pan = v; });
                t.mute().markInterested();
                t.mute().addValueObserver(function (v) { gState.effect_tracks[idx].mute = v; });
                t.solo().markInterested();
                t.solo().addValueObserver(function (v) { gState.effect_tracks[idx].solo = v; });
            })(ei);
        }
    }
}

function snapshot() {
    // gState is kept current by observers; return a plain copy.
    var tracks = [];
    for (var i = 0; i < gState.tracks.length; i++) {
        var t = gState.tracks[i];
        if (!t.exists) continue;
        tracks.push({
            index: t.index, name: t.name, volume: t.volume, volume_db: t.volume_db,
            pan: t.pan, pan_disp: t.pan_disp, mute: t.mute, solo: t.solo, arm: t.arm
        });
    }
    var tempo = null, position = null;
    try { tempo = transport.tempo().value().getRaw(); } catch (e) {}
    try { position = transport.playPosition().get(); } catch (e) {}
    var hostVersion = null;
    try { hostVersion = "" + host.getHostVersion(); } catch (e) {}
    return {
        host_version: hostVersion,
        transport: {
            playing: gState.transport.playing,
            recording: gState.transport.recording,
            loop: gState.transport.loop,
            metronome: gState.transport.metronome,
            overdub: gState.transport.overdub,
            tempo: tempo,
            position: position
        },
        arranger: gState.arranger,
        device: gState.device,
        tracks: tracks,
        effect_tracks: gState.effect_tracks.filter(function (t) { return t.exists; }),
        cursor_clip: { which: (gCursorClip === arrangerClip) ? "arranger" : "launcher" }
    };
}

// ── socket ────────────────────────────────────────────────────────────────────

function openSocket() {
    gSocket = host.createRemoteConnection("openwig", PORT);
    gSocket.setClientConnectCallback(function (connection) {
        gConnection = connection;
        log("Openwig client connected on port " + gSocket.getPort());
        connection.setDisconnectCallback(function () {
            log("Openwig client disconnected");
            gConnection = null;
        });
        // Bitwig's RemoteSocket reads a 4-byte big-endian length prefix off the
        // wire and fires this callback with exactly one complete de-framed
        // message body. So each invocation == one full message; parse directly.
        connection.setReceiveCallback(function (data) {
            try {
                handleLine("" + new JString(data, Charset.UTF_8));
            } catch (e) {
                flog("RX cb error: " + e);
            }
        });
        // proactively push a snapshot so the server can seed its cache
        try { sendObj({ jsonrpc: "2.0", method: "connected", params: snapshot() }); }
        catch (e) { log("snapshot on connect failed: " + e); }
    });
    log("Openwig bridge listening on port " + gSocket.getPort());
}

function sendObj(obj) {
    if (!gConnection) return;
    var s = JSON.stringify(obj) + "\n";
    var bytes = new JString(s).getBytes(Charset.UTF_8);
    gConnection.send(bytes);
}

function handleLine(line) {
    var msg;
    try { msg = JSON.parse(line); }
    catch (e) { log("bad JSON: " + line); return; }

    var id     = (msg.id === undefined) ? null : msg.id;
    var method = msg.method;
    var params = msg.params || {};
    log("RECV id=" + id + " method=" + method);

    var fn = HANDLERS[method];
    if (!fn) {
        log("unknown method: " + method);
        if (id !== null) sendObj({ jsonrpc: "2.0", id: id, error: { code: -32601, message: "unknown method: " + method } });
        return;
    }
    try {
        var result = fn(params);
        if (id !== null) sendObj({ jsonrpc: "2.0", id: id, result: (result === undefined ? true : result) });
        log("OK " + method);
    } catch (e) {
        log("handler error [" + method + "]: " + e);
        if (id !== null) sendObj({ jsonrpc: "2.0", id: id, error: { code: -32000, message: "" + e } });
    }
}

// ── handlers ──────────────────────────────────────────────────────────────────

function trk(i) { return trackBank.getItemAt(i); }
// Bank-aware track resolver. bank: "main" (default) | "effect" | "master".
// Used by fxtrack.* + future routing/cross-bank ops; existing trk()-based handlers
// stay main-only for backward compat.
function trkB(bank, i) {
    if (bank === "effect") return effectTrackBank.getItemAt(i);
    if (bank === "master") return masterTrack;
    return trackBank.getItemAt(i);
}
function _existing(bank, n) {            // bank items whose exists() is true (must be markInterested)
    var out = [];
    for (var i = 0; i < n; i++) { var it = bank.getItemAt(i); if (it.exists().get()) out.push(it); }
    return out;
}
function _deleteAll(items) {             // atomic multi-delete (no bank-reindex hazard)
    if (!items || !items.length) return 0;
    host.deleteObjects(Java.to(items, "com.bitwig.extension.controller.api.DeleteableObject[]"));
    return items.length;
}
function bget(p, k, d) { return (p[k] === undefined) ? d : p[k]; }

// ── internal-access helpers (the controller as an in-process "agent") ──────────
// Bitwig's GraalJS grants full host access, so we can load internal classes, unwrap
// the API proxies to their document objects, and call internal methods by reflection.
function _invokeNoArg(obj, name) {
    var c = obj.getClass();
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++)
            if (("" + ms[i].getName()) === name && ms[i].getParameterCount() === 0) { ms[i].setAccessible(true); return ms[i].invoke(obj); }
        c = c.getSuperclass();
    }
    throw "no no-arg method " + name;
}
// fj = the document automatable-value base; reach it from a control-surface target
function _fjFrom(obj) {
    var fjC = Java.type("fj").class;
    if (obj != null && fjC.isInstance(obj)) return obj;
    var c = obj.getClass();
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i];
            if (m.getParameterCount() === 0 && !m.getReturnType().isPrimitive() && m.getReturnType() !== java.lang.Void.TYPE) {
                try { m.setAccessible(true); var r = m.invoke(obj); if (r != null && fjC.isInstance(r)) return r; } catch (e) {}
            }
        }
        c = c.getSuperclass();
    }
    return null;
}
// the arranger automation-event insert: r3B(a1x, time, value, curvature, hasCurv, adjPrev, oJk, bT1)
function _findAutomationInsert(cls) {
    var c = cls;
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i]; if (("" + m.getName()) !== "r3B") continue;
            var ps = m.getParameterTypes(); if (ps.length !== 8) continue;
            if (("" + ps[0].getSimpleName()) === "a1x" && ("" + ps[1].getName()) === "double" &&
                ("" + ps[6].getSimpleName()) === "oJk") { m.setAccessible(true); return m; }
        }
        c = c.getSuperclass();
    }
    throw "automation insert method not found";
}

// Find a parameter value's NORMALIZE function on its document fj: the 1-arg(double)->
// double method mapping native units to 0..1 (== the inverse of what automate() writes,
// and what arranger breakpoints must be converted through). Names are obfuscated and
// class-specific, so identify by behaviour: the unique such method, or - if several -
// the one that maps the current native value to the current normalized value.
function _findNormalizeFn(fj, curNative, curNorm) {
    var cands = [], seen = {}, c = fj.getClass();
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i];
            if (m.getParameterCount() !== 1) continue;
            if (("" + m.getReturnType().getName()) !== "double") continue;
            if (("" + m.getParameterTypes()[0].getName()) !== "double") continue;
            var nm = "" + m.getName(); if (seen[nm]) continue; seen[nm] = 1;
            m.setAccessible(true); cands.push(m);
        }
        c = c.getSuperclass();
    }
    if (cands.length === 0) return null;
    if (cands.length === 1) return cands[0];
    for (var j = 0; j < cands.length; j++) {
        try {
            var got = cands[j].invoke(fj, java.lang.Double.valueOf(curNative));
            if (got != null && Math.abs(got - curNorm) < 1e-3 && got >= -0.01 && got <= 1.01) return cands[j];
        } catch (e) {}
    }
    return null;
}

// ── generic descriptor-graph reader (the in-process structured reader) ──────────
// Walk the live document object graph EXACTLY the way Bitwig's own serializer does:
// for each property descriptor that passes (nI_() && SZo.uEK filter) emit its VALUE
// (scalars/strings directly via cxz_2.Xzy(UO1)) and recurse into relationships
// (cxb_3.uEK(UO1,String) -> child document objects). This yields real notes +
// automation as structured JSON, sidestepping the wire/serialization format.
// cwo_3.KRt() = Arrays.asList(this.azd): the public, collision-free way to get the
// serialized-property descriptor list (empty if the class wasn't realized yet).
function _descriptors(cwo) {
    return _inv0(cwo, "KRt");          // java.util.List<cxz_2>
}
var _MCACHE = {};
function _findMethod(cls, name, pcount, p1simple) {
    var key = cls.getName() + "#" + name + "/" + pcount + "/" + (p1simple || "");
    if (_MCACHE[key] !== undefined) return _MCACHE[key];
    var c = cls, found = null;
    while (c != null && found == null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i];
            if (("" + m.getName()) !== name || m.getParameterCount() !== pcount) continue;
            if (p1simple) { var pt = m.getParameterTypes(); if (("" + pt[1].getSimpleName()) !== p1simple) continue; }
            m.setAccessible(true); found = m; break;
        }
        c = c.getSuperclass();
    }
    _MCACHE[key] = found; return found;
}
function _inv0(obj, name) {                         // cached reflected no-arg call
    var m = _findMethod(obj.getClass(), name, 0, null);
    return (m == null) ? null : m.invoke(obj);
}
function _inv1(obj, name, a) {                      // cached reflected 1-arg call
    var m = _findMethod(obj.getClass(), name, 1, null);
    return (m == null) ? null : m.invoke(obj, a);
}
var _SZUEK = null;
function _szPass(d, uo1) {                          // Bitwig's own serialize filter (uEK)
    if (_SZUEK == null) _SZUEK = Java.type("com.bitwig.ramona.serial.SZo").uEK;
    var m = _findMethod(_SZUEK.getClass(), "r3B", 2, "cxz_2");
    if (m == null) return true;
    return !!m.invoke(_SZUEK, d, uo1);
}
function _relChildren(d, uo1) {                     // null if d is not a relationship
    var m = _findMethod(d.getClass(), "uEK", 2, "String");
    if (m == null) return null;
    return m.invoke(d, uo1, null);
}
function _jval(v) {
    if (v == null) return null;
    var cn;
    try { cn = "" + v.getClass().getName(); } catch (e) { return "" + v; }
    if (cn === "java.lang.Double" || cn === "java.lang.Float") return v.doubleValue();
    if (cn === "java.lang.Integer" || cn === "java.lang.Long" || cn === "java.lang.Short" || cn === "java.lang.Byte") return v.longValue();
    if (cn === "java.lang.Boolean") return v.booleanValue();
    return "" + v;
}
var _IHC = null;
function _walkObj(uo1, depth, maxDepth, budget, opts) {
    var o = {}, cwo;
    try { cwo = uo1.mX_(); } catch (e) { return { _err: "mX_: " + e }; }
    try { o._cls = "" + _inv0(cwo, "bf"); } catch (e) { o._cls = "?"; }
    if (_IHC == null) _IHC = Java.type("java.lang.System");
    var ihc = _IHC.identityHashCode(uo1);
    o._id = ihc;                                        // object identity (for cross-reference matching)
    if (opts.prune[o._cls]) { o._pruned = true; return o; }
    if (opts.seen[ihc] && !(opts.noDedup && opts.noDedup[o._cls])) { o._dup = true; return o; }
    opts.seen[ihc] = true;
    var azd = _descriptors(cwo);
    var alen = (azd == null) ? 0 : azd.size();
    if (alen === 0) { o._noazd = true; return o; }
    for (var i = 0; i < alen; i++) {
        if (budget.n <= 0) { o._trunc = true; break; }
        var d = azd.get(i);
        try { if (!_inv0(d, "nI_") || (!opts.noFilter && !_szPass(d, uo1))) continue; }
        catch (e) { if (depth === 0 && !o._ferr) o._ferr = "" + e; continue; }
        var pid; try { pid = "" + _inv0(d, "ngq"); } catch (e) { pid = "i" + i; }
        budget.n--;
        var kids = null;
        try { kids = _relChildren(d, uo1); } catch (e) { kids = null; }
        if (kids != null) {
            var arr = [], nk = kids.size();
            for (var j = 0; j < nk; j++) {
                if (budget.n <= 0) { o._trunc = true; break; }
                var k = kids.get(j);
                if (depth < maxDepth) arr.push(_walkObj(k, depth + 1, maxDepth, budget, opts));
                else { var rc; try { rc = "" + _inv0(k.mX_(), "bf"); } catch (e) { rc = "?"; } arr.push({ _ref: rc }); }
            }
            o[pid] = arr;
        } else {
            try { o[pid] = _jval(_inv1(d, "Xzy", uo1)); } catch (e) { o[pid] = "<err>"; }
        }
    }
    return o;
}

// run a JS task on the document-edit thread/context (where mutations are valid).
// Fire-and-forget: exec() posts to the controller's own event queue, which is processed
// AFTER this handler returns - so we can't block on a result (that would deadlock). The
// task logs its outcome to openwig_bridge.log ([auto] ...).
function _runOnDocumentThread(proxy, jsRun) {
    var execM = null, c = proxy.getClass();
    while (c != null) { try { execM = c.getDeclaredMethod("exec", Java.type("java.lang.Runnable")); break; } catch (e) {} c = c.getSuperclass(); }
    if (execM == null) throw "exec(Runnable) not found";
    execM.setAccessible(true);
    var task = new (Java.extend(Java.type("java.lang.Runnable")))({ run: function () {
        try { flog("[auto] " + JSON.stringify(jsRun())); } catch (e) { flog("[auto] ERR: " + e); }
        gOpsDone++;          // signal completion: the op actually finished on the edit thread
        // PUSH the completion to the client (this runs on the doc thread, same as the op, and
        // the client waits passively - so no JS runs concurrently on the receive thread, which
        // would crash GraalJS). The client must NOT poll while an op is in flight.
        try { sendObj({ jsonrpc: "2.0", method: "op_done", params: { done: gOpsDone } }); } catch (e) {}
    }});
    execM.invoke(proxy, task);
}

function automationWriteOffline(p) {
    var pts = p.points; if (!pts || !pts.length) return { error: "no points" };
    var which = "" + bget(p, "param", "volume");
    function paramProxy() {
        if (which === "pan") return cursorTrack.pan();
        if (which === "remote") return remoteControlsPage.getParameter(bget(p, "remote_index", 0) | 0);
        return cursorTrack.volume();
    }
    _runOnDocumentThread(cursorTrack, function () {
        var byU = cursorTrack.getDeepestTarget();
        if (byU == null) throw "no track target (select a track first)";
        var al = _invokeNoArg(byU, "zer");                       // automation_lanes (udG)
        var pp = paramProxy();
        var fj = _fjFrom(_invokeNoArg(pp.getDeepestTarget(), "getAtom"));
        if (fj == null) fj = _fjFrom(pp.getDeepestTarget());
        if (fj == null) throw "could not resolve fj for param '" + which + "'";
        var a1x = Java.type("com.bitwig.flt.document.core.master.a1x").r3B(fj);
        var oJk = Java.type("oJk"), LINEAR = oJk.Xzy, HOLD = oJk.r3B;
        var Dbl = Java.type("java.lang.Double"), Bl = Java.type("java.lang.Boolean");
        var m = _findAutomationInsert(al.getClass());
        var n = 0;
        for (var i = 0; i < pts.length; i++) {
            var pt = pts[i];
            var hasCurv = (pt.length > 2 && pt[2] != null);
            var curv = hasCurv ? pt[2] : 0.0;
            var interp = (pt.length > 3 && ("" + pt[3]) === "hold") ? HOLD : LINEAR;
            m.invoke(al, a1x, Dbl.valueOf(pt[0]), Dbl.valueOf(pt[1]), Dbl.valueOf(curv),
                     Bl.valueOf(hasCurv), Bl.FALSE, interp, null);
            n++;
        }
        return { inserted: n, param: which };
    });
    return { queued: pts.length, param: which, note: "async; outcome in openwig_bridge.log [auto]" };
}

// OFFLINE arranger-clip create + fill, in-process. Calls the same GUI commands the wire
// op 0x1cb6 (track.insert_instrument_clip_on_arranger_command, prop 7350) and op 0x1cb5
// (instrument_note_clip_event.insert_note_command, prop 7349) would dispatch - reached via
// cxu_2.r3B(UO1, List) (cxu_2.java:82 -> cxr_22.Xzy((XDd)uO1, this.ngq(), list)). Replaces
// the wire path (mitm_daemon + port 7880) end-to-end. Cursor track must be selected first.
//   p: { start: beats, duration: beats, notes: [[channel, key, start, dur, vel], ...] }
function _clipCreateAndFill(p) {
    var start = Number(p.start || 0), dur = Number(p.duration || 4);
    var notes = p.notes || [];
    _runOnDocumentThread(cursorTrack, function () {
        var byU = cursorTrack.getDeepestTarget();
        if (byU == null) throw "no track target (select a track first)";
        // alU = real class (CFR renamed source file to alu_1.java to avoid case-insensitive
        // FS collision with sibling classes alu / aLu / aLU - all four exist in default pkg).
        var X2S = Java.type("X2S"), alu1 = Java.type("alU");
        var ArrayList = Java.type("java.util.ArrayList");
        var Dbl = Java.type("java.lang.Double"), Int = Java.type("java.lang.Integer");
        var args1 = new ArrayList();
        args1.add(Dbl.valueOf(start)); args1.add(Dbl.valueOf(dur));
        var clipDoc = X2S.fiU.qgm().r3B(byU, args1);
        if (clipDoc == null) throw "clip create returned null";
        var cmdNote = alu1.r3B.XaN();
        for (var i = 0; i < notes.length; i++) {
            var n = notes[i];
            var args2 = new ArrayList();
            args2.add(Int.valueOf(n[0] | 0));            // channel
            args2.add(Int.valueOf(n[1] | 0));            // key
            args2.add(Dbl.valueOf(Number(n[2])));        // start_in_clip
            args2.add(Dbl.valueOf(Number(n[3])));        // duration
            args2.add(Dbl.valueOf(Number(n[4])));        // velocity
            cmdNote.r3B(clipDoc, args2);
        }
        return { created: 1, notes: notes.length, start: start, duration: dur };
    });
    return { queued: notes.length, start: start, duration: dur, note: "async; outcome in openwig_bridge.log [auto]" };
}

var HANDLERS = {

    // ── meta ──
    "ping": function () { return "pong"; },
    // Completion counter for async document-thread ops (clip create, automation write, ...).
    // The SDK reads it before firing an op and polls until it advances - replaces fixed
    // sleeps with "wait until the op actually finished" (faster + race-free).
    "ops.done": function () { return { done: gOpsDone }; },
    // Number of loaded devices on the CURSOR track. A device insert finishes loading
    // asynchronously (not a document-thread op, so no op_done) - the SDK polls this until it
    // increases instead of sleeping a fixed second. Polling here is safe: loading runs in
    // Bitwig's engine, not the GraalJS controller, so there's no concurrent JS.
    "track.device_count": function () {
        var n = 0;
        for (var i = 0; i < 16; i++) { try { if (cursorTrackDeviceBank.getItemAt(i).exists().get()) n++; } catch (e) {} }
        return { count: n };
    },
    "hello": function (p) { return { version: 1, num_tracks: NUM_TRACKS, snapshot: snapshot() }; },
    "state.snapshot": function () { return snapshot(); },
    // ── host introspection (Bitwig version handshake) ──
    "host.version": function () {
        var out = {};
        try { out.product = "" + host.getHostProduct(); } catch (e) {}
        try { out.vendor  = "" + host.getHostVendor();  } catch (e) {}
        try { out.version = "" + host.getHostVersion(); } catch (e) {}
        try { out.api_version = host.getHostApiVersion();} catch (e) {}
        return out;
    },

    // ── OFFLINE automation: insert arranger breakpoints DIRECTLY (no playback/recording) ──
    // Reaches Bitwig internals from this in-process controller: unwrap the parameter proxy to
    // its document fj, build the a1x identity, and call the GUI's own automation_lanes insert
    // inside the document-edit context via exec(Runnable).
    //   p: { param: "volume"|"pan"|"remote", remote_index: N,
    //        points: [ [beat, value0..1, curvature?, "linear"|"hold"?], ... ] }
    // Select the target track first (cursorTrack follows it).
    "automation.write_offline": automationWriteOffline,

    // ── OFFLINE arranger clip + notes (daemon-free; calls insert_instrument_clip_on_arranger
    //    + insert_note via cxu_2 dispatch - see _clipCreateAndFill above). Select track first.
    "clip.create_arranger_with_notes": _clipCreateAndFill,

    // ── transport ──
    "transport.play":      function () { transport.play(); },
    "transport.stop":      function () { transport.stop(); },
    "transport.continue":  function () { transport.continuePlayback(); },
    "transport.record":    function () { transport.record(); },
    "transport.toggle_record": function (p) { transport.isArrangerRecordEnabled().set(!!bget(p, "on", true)); },
    "transport.set_automation_write": function (p) { transport.isArrangerAutomationWriteEnabled().set(!!bget(p, "on", true)); },
    "transport.set_position":  function (p) { transport.setPosition(bget(p, "beats", 0.0)); },
    "transport.set_loop":      function (p) { transport.isArrangerLoopEnabled().set(!!bget(p, "on", true)); },
    "transport.set_loop_region": function (p) {
        transport.arrangerLoopStart().set(bget(p, "start", 0.0));
        transport.arrangerLoopDuration().set(bget(p, "length", 4.0));
    },
    "transport.set_punch": function (p) {
        if (p.in  !== undefined) transport.isPunchInEnabled().set(!!p.in);
        if (p.out !== undefined) transport.isPunchOutEnabled().set(!!p.out);
    },
    "transport.set_metronome": function (p) { transport.isMetronomeEnabled().set(!!bget(p, "on", true)); },
    "transport.set_overdub":   function (p) { transport.isArrangerOverdubEnabled().set(!!bget(p, "on", true)); },
    "transport.set_tempo":     function (p) { transport.tempo().value().setRaw(bget(p, "bpm", 120.0)); },
    "transport.add_cue_marker":function () { transport.addCueMarkerAtPlaybackPosition(); },
    "transport.return_to_arrangement": function () { transport.returnToArrangement(); },

    // ── tracks ──
    "track.create": function (p) {
        var type = bget(p, "type", "instrument");
        var pos  = bget(p, "index", -1);
        if (type === "audio")       application.createAudioTrack(pos);
        else if (type === "effect") application.createEffectTrack(pos);
        else                        application.createInstrumentTrack(pos);
        var nm = p.name;
        if (nm) host.scheduleTask(function () {
            try { cursorTrack.name().set("" + nm); } catch (e) { host.errorln("rename new track failed: " + e); }
        }, 200);
    },
    "track.delete":  function (p) { host.deleteObjects(trk(bget(p, "index", 0))); },

    // ── effect-track (return-track) addressing (separate bank) ──
    "fxtrack.set_volume": function (p) { effectTrackBank.getItemAt(bget(p, "index", 0)).volume().value().set(bget(p, "value", 0.0)); },
    "fxtrack.set_pan":    function (p) { effectTrackBank.getItemAt(bget(p, "index", 0)).pan().value().set(bget(p, "value", 0.5)); },
    "fxtrack.set_mute":   function (p) { effectTrackBank.getItemAt(bget(p, "index", 0)).mute().set(!!bget(p, "on", true)); },
    "fxtrack.set_solo":   function (p) { effectTrackBank.getItemAt(bget(p, "index", 0)).solo().set(!!bget(p, "on", true)); },
    "fxtrack.set_color":  function (p) { effectTrackBank.getItemAt(bget(p, "index", 0)).color().set(bget(p, "r", 0.5), bget(p, "g", 0.5), bget(p, "b", 0.5)); },
    "fxtrack.rename":     function (p) { effectTrackBank.getItemAt(bget(p, "index", 0)).name().set("" + bget(p, "name", "")); },
    "fxtrack.delete":     function (p) { host.deleteObjects(effectTrackBank.getItemAt(bget(p, "index", 0))); },
    "fxtrack.select":     function (p) { effectTrackBank.getItemAt(bget(p, "index", 0)).selectInMixer(); },
    "fxtrack.insert_file":function (p) {
        effectTrackBank.getItemAt(bget(p, "index", 0)).selectInMixer();
        // wait one tick for cursorTrack to follow, then insert
        cursorTrack.endOfDeviceChainInsertionPoint().insertFile("" + bget(p, "path", ""));
    },
    // delete ALL tracks (main + send/effect tracks; everything except master) in one atomic
    // call (avoids the bank-reindex hazard of deleting one-by-one). Returns how many removed.
    "track.delete_all": function () {
        var items = _existing(trackBank, NUM_TRACKS);
        if (effectTrackBank) items = items.concat(_existing(effectTrackBank, 16));
        return { deleted: _deleteAll(items) };
    },
    // remove every device from the MASTER chain (the missing "clean slate" piece).
    "master.clear": function () { return { deleted: _deleteAll(_existing(masterDeviceBank, 16)) }; },
    "master.device_count": function () { return { count: _existing(masterDeviceBank, 16).length }; },
    // full clean slate: all tracks (main + send/effect) + the master FX chain.
    "project.clear": function () {
        var items = _existing(trackBank, NUM_TRACKS);
        if (effectTrackBank) items = items.concat(_existing(effectTrackBank, 16));
        return { tracks_deleted: _deleteAll(items),
                 master_devices_deleted: _deleteAll(_existing(masterDeviceBank, 16)) };
    },
    "track.rename":  function (p) { trk(bget(p, "index", 0)).name().set("" + bget(p, "name", "")); },
    "track.set_color": function (p) {
        trk(bget(p, "index", 0)).color().set(bget(p, "r", 0.5), bget(p, "g", 0.5), bget(p, "b", 0.5));
    },
    "track.group_selected": function () {
        var a = application.getAction("group_tracks");
        if (!a) a = application.getAction("group_selected_tracks");
        if (!a) throw "no grouping action available in this Bitwig version";
        a.invoke();
    },
    "track.set_volume": function (p) { trk(bget(p, "index", 0)).volume().value().set(bget(p, "value", 0.0)); },
    "track.set_pan":    function (p) { trk(bget(p, "index", 0)).pan().value().set(bget(p, "value", 0.5)); },
    "track.set_mute":   function (p) { trk(bget(p, "index", 0)).mute().set(!!bget(p, "on", true)); },
    "track.set_solo":   function (p) { trk(bget(p, "index", 0)).solo().set(!!bget(p, "on", true)); },
    "track.set_arm":    function (p) { trk(bget(p, "index", 0)).arm().set(!!bget(p, "on", true)); },
    "track.set_monitor":function (p) { trk(bget(p, "index", 0)).monitorMode().set("" + bget(p, "mode", "AUTO")); },
    "track.set_send":   function (p) {
        trk(bget(p, "index", 0)).sendBank().getItemAt(bget(p, "send", 0)).value().set(bget(p, "value", 0.0));
    },
    "track.stop":   function (p) { trk(bget(p, "index", 0)).stop(); },
    "track.select": function (p) { trk(bget(p, "index", 0)).selectInMixer(); },

    // ── instrument / clip authoring (for making sound) ──
    "device.insert_bitwig": function (p) {
        // caller should track.select the target first (cursorTrack follows selection)
        cursorTrack.endOfDeviceChainInsertionPoint().insertBitwigDevice(JUUID.fromString("" + p.uuid));
    },
    "device.insert_file": function (p) {
        // load a device/preset from a .bwdevice/.bwpreset file path (factory devices live in
        // <install>/Library/devices/<Name>.bwdevice). Select the target track first.
        // VSTs (.vst3) may or may not be accepted by insertFile - wrap so a failure
        // here doesn't crash the bridge; caller can fall back to device.insert_vst3.
        try {
            cursorTrack.endOfDeviceChainInsertionPoint().insertFile("" + p.path);
            return { ok: true, path: "" + p.path };
        } catch (e) {
            return { error: "" + e, path: "" + p.path };
        }
    },
    "device.insert_file_on_master": function (p) {
        try {
            masterTrack.endOfDeviceChainInsertionPoint().insertFile("" + p.path);
            return { ok: true };
        } catch (e) {
            return { error: "" + e };
        }
    },
    // VST3 insertion fallback for the case where insertFile rejects .vst3 paths.
    // Uses InsertionPoint.insertVst3Device(name, uid) via reflection - present on
    // Bitwig 5.x. uid is the 32-char hex Class ID extracted from moduleinfo.json
    // or the .vst3 binary (see walls.vst3_uuid in the Python layer).
    "device.insert_vst3": function (p) {
        var uid  = "" + bget(p, "uid", "");
        var name = "" + bget(p, "name", "");
        if (!uid) return { error: "uid required" };
        try {
            var ip = cursorTrack.endOfDeviceChainInsertionPoint();
            // Look up the method by name (signature may vary across versions)
            var ms = ip.getClass().getMethods();
            for (var i = 0; i < ms.length; i++) {
                var mn = "" + ms[i].getName();
                if (mn === "insertVst3Device" || mn === "insertVST3Device") {
                    ms[i].invoke(ip, name, uid);
                    return { ok: true, via: mn, uid: uid };
                }
            }
            return { error: "no insertVst3Device method found on InsertionPoint",
                     candidates: ms.length };
        } catch (e) {
            return { error: "" + e };
        }
    },
    // serialize the SELECTED track's internal object to bytes (Bitwig's own serializer, run in
    // the document-edit context) -> stashed as base64; fetch with track.serialize_result. The
    // parent reads it with ramona_serial to decode clips/notes (engine_playback_events) +
    // automation. mode: "uEK" (default) or "SWC".
    "track.serialize": function (p) {
        gSerializeB64 = null; gSerializeErr = null;
        var obj = cursorTrack.getDeepestTarget();
        if (obj == null) return { error: "no track target (select a track first)" };
        var mode = "" + (p.mode || "uEK");
        _runOnDocumentThread(cursorTrack, function () {
            try {
                var SZo = Java.type("com.bitwig.ramona.serial.SZo");
                var bytes = obj.SWC(mode === "SWC" ? SZo.SWC : SZo.uEK);
                gSerializeB64 = "" + Java.type("java.util.Base64").getEncoder().encodeToString(bytes);
                return { len: bytes.length };
            } catch (e) { gSerializeErr = "" + e; return { error: "" + e }; }
        });
        return { queued: true, note: "fetch with track.serialize_result" };
    },
    "track.serialize_result": function (p) {
        return { b64: gSerializeB64, error: gSerializeErr, ready: (gSerializeB64 != null || gSerializeErr != null) };
    },
    // generic in-process structured read of the SELECTED track's document graph (notes +
    // automation as real values). params: max_depth (default 12), max_nodes (default 6000).
    // async -> fetch JSON with obj.walk_result.
    "obj.walk": function (p) {
        gWalk = null; gWalkErr = null;
        var maxDepth = Math.min(bget(p, "max_depth", 12) | 0, 16);     // hard caps: heavy walks crash GraalJS
        var maxNodes = Math.min(bget(p, "max_nodes", 6000) | 0, 9000);
        var pruneArr = bget(p, "prune", []), prune = {};
        for (var pi = 0; pi < pruneArr.length; pi++) prune["" + pruneArr[pi]] = true;
        var noDedupArr = bget(p, "no_dedup", []), noDedup = {};
        for (var ni = 0; ni < noDedupArr.length; ni++) noDedup["" + noDedupArr[ni]] = true;
        var opts = { prune: prune, noFilter: !!bget(p, "no_filter", false), seen: {}, noDedup: noDedup };
        _runOnDocumentThread(cursorTrack, function () {
            try {
                var root = cursorTrack.getDeepestTarget();
                if (root == null) { gWalkErr = "no track target (select a track first)"; return { error: gWalkErr }; }
                var budget = { n: maxNodes };
                gWalk = JSON.stringify(_walkObj(root, 0, maxDepth, budget, opts));
                return { len: gWalk.length, used: maxNodes - budget.n };
            } catch (e) { gWalkErr = "" + e; return { error: gWalkErr }; }
        });
        return { queued: true, note: "fetch with obj.walk_result" };
    },
    "obj.walk_result": function (p) {
        return { json: gWalk, error: gWalkErr, ready: (gWalk != null || gWalkErr != null) };
    },
    // Like obj.walk but rooted at the CURRENT cursor DEVICE - to read one device's document
    // subtree (real parameter atoms + values, with no remote-macro indirection and no
    // observer lag). Same safe descriptor walk; fetch with obj.walk_result.
    "obj.walk_device": function (p) {
        gWalk = null; gWalkErr = null;
        var maxDepth = Math.min(bget(p, "max_depth", 12) | 0, 16);
        var maxNodes = Math.min(bget(p, "max_nodes", 6000) | 0, 9000);
        var pruneArr = bget(p, "prune", []), prune = {};
        for (var pi = 0; pi < pruneArr.length; pi++) prune["" + pruneArr[pi]] = true;
        var noDedupArr = bget(p, "no_dedup", []), noDedup = {};
        for (var ni = 0; ni < noDedupArr.length; ni++) noDedup["" + noDedupArr[ni]] = true;
        var opts = { prune: prune, noFilter: !!bget(p, "no_filter", false), seen: {}, noDedup: noDedup };
        _runOnDocumentThread(cursorTrack, function () {
            try {
                var root = cursorDevice.getDeepestTarget();
                if (root == null) { gWalkErr = "no device target (select a device first)"; return { error: gWalkErr }; }
                var budget = { n: maxNodes };
                gWalk = JSON.stringify(_walkObj(root, 0, maxDepth, budget, opts));
                return { len: gWalk.length, used: maxNodes - budget.n };
            } catch (e) { gWalkErr = "" + e; return { error: gWalkErr }; }
        });
        return { queued: true, note: "fetch with obj.walk_result" };
    },
    // CURRENT cursor device's remote-page params + the identity of each param's
    // document atom(s). Navigate devices from Python (select_previous/next) and
    // call this per device; match the ids against obj.walk automation-lane targets
    // (_id) to resolve which remote a device-param automation lane drives.
    "device.remote_atom_ids": function (p) {
        if (_IHC == null) _IHC = Java.type("java.lang.System");
        var params = [];
        for (var ri = 0; ri < NUM_REMOTE; ri++) {
            var pr = remoteControlsPage.getParameter(ri);
            var ex; try { ex = !!pr.exists().get(); } catch (e) { ex = false; }
            if (!ex) continue;
            var nm; try { nm = "" + pr.name().get(); } catch (e) { nm = ""; }
            var ids = [];
            try {
                var dt = pr.getDeepestTarget();
                if (dt != null) {
                    ids.push(_IHC.identityHashCode(dt));
                    try { var at = _invokeNoArg(dt, "getAtom"); if (at != null) ids.push(_IHC.identityHashCode(at)); } catch (e) {}
                    try { var fj = _fjFrom(_invokeNoArg(dt, "getAtom")); if (fj != null) ids.push(_IHC.identityHashCode(fj)); } catch (e) {}
                }
            } catch (e) {}
            params.push({ remote_index: ri, name: nm, atom_ids: ids });
        }
        return { params: params };
    },
    // Convert arranger-automation breakpoint values (native/raw units, decimal_value_event
    // 655) to automate()'s normalized 0..1 for the given remote param, using Bitwig's OWN
    // normalize function on the param's document fj. This is exact for any mapping - including
    // remotes whose macro covers only a sub-range of the param, and nonlinear (log) params -
    // because it operates on the underlying parameter's full range, not the remote macro.
    // Non-destructive: reads only. p: { remote_index, values: [native, ...] }.
    "device.remote_normalize": function (p) {
        var ri = bget(p, "remote_index", 0) | 0;
        var vals = p.values || [];
        var pp = remoteControlsPage.getParameter(ri);
        var dt = null, atom = null, fj = null;
        try { dt = pp.getDeepestTarget(); } catch (e) {}
        try { atom = _invokeNoArg(dt, "getAtom"); } catch (e) {}
        fj = _fjFrom(atom); if (fj == null) fj = _fjFrom(dt);
        if (fj == null) return { error: "no fj" };
        var curNative, curNorm;
        try { curNative = pp.value().getRaw(); } catch (e) { return { error: "no getRaw" }; }
        try { curNorm = pp.value().get(); } catch (e) { return { error: "no value" }; }
        var nf = _findNormalizeFn(fj, curNative, curNorm);
        if (nf == null) return { error: "no normalize fn", cur_native: curNative, cur_norm: curNorm };
        if (!vals.length) return { normalized: [], cur_native: curNative, cur_norm: curNorm };
        var out = [];
        for (var i = 0; i < vals.length; i++) {
            try {
                var n = nf.invoke(fj, java.lang.Double.valueOf(vals[i]));
                out.push(n < 0 ? 0 : (n > 1 ? 1 : n));
            } catch (e) { out.push(null); }
        }
        return { normalized: out };
    },
    // read the focused arranger clip's MIDI notes via the note-step grid (scroll-aware).
    // protocol: notes_setup -> notes_scroll(step) per window -> notes_get. grid is 16 steps wide.
    "clip.notes_setup": function (p) {
        gClipNotes = {}; gNoteScroll = 0;
        gNoteStepSize = bget(p, "step", 0.25);
        if (arrangerClip) arrangerClip.setStepSize(gNoteStepSize);
        return { grid_width: NUM_CLIPS, step: gNoteStepSize };
    },
    "clip.notes_scroll": function (p) {
        gNoteScroll = bget(p, "step", 0) | 0;
        if (arrangerClip) arrangerClip.scrollToStep(gNoteScroll);
        return { scroll: gNoteScroll };
    },
    "clip.notes_get": function (p) {
        var out = [];
        for (var k in gClipNotes) out.push(gClipNotes[k]);
        out.sort(function (a, b) { return a.start - b.start || a.key - b.key; });
        return out;
    },
    // Master-track device chain names (for s.master([...])). Read straight from the bank.
    "master.devices": function () {
        var out = [];
        for (var i = 0; i < 16; i++) {
            var d = masterDeviceBank.getItemAt(i);
            if (!d.exists().get()) break;
            out.push({ index: i, name: "" + d.name().get() });
        }
        return { devices: out };
    },
    "master.set_remote": function (p) { masterRemotes.getParameter(bget(p, "index", 0)).value().set(bget(p, "value", 0.0)); },
    "master.remotes": function () {
        var out = [];
        for (var i = 0; i < NUM_REMOTE; i++) {
            var mp = masterRemotes.getParameter(i);
            out.push({ index: i, exists: !!mp.exists().get(), name: "" + mp.name().get(), value: mp.value().get() });
        }
        return out;
    },
    "track.start_note": function (p) { trk(bget(p, "track", 0)).startNote(bget(p, "key", 60), bget(p, "velocity", 100)); },
    "track.stop_note":  function (p) { trk(bget(p, "track", 0)).stopNote(bget(p, "key", 60), bget(p, "velocity", 0)); },
    "track.create_clip": function (p) { trk(bget(p, "track", 0)).createNewLauncherClip(bget(p, "slot", 0)); },
    "track.select_slot": function (p) { trk(bget(p, "track", 0)).selectSlot(bget(p, "slot", 0)); },

    // ── master ──
    "master.set_volume": function (p) { masterTrack.volume().value().set(bget(p, "value", 0.0)); },

    // ── arranger ──
    "arranger.set_panel": function (p) {
        var on = !!bget(p, "on", true);
        switch (bget(p, "panel", "")) {
            case "follow":         arranger.isPlaybackFollowEnabled().set(on); break;
            case "double_row":     arranger.hasDoubleRowTrackHeight().set(on); break;
            case "cue_markers":    arranger.areCueMarkersVisible().set(on); break;
            case "clip_launcher":  arranger.isClipLauncherVisible().set(on); break;
            case "timeline":       arranger.isTimelineVisible().set(on); break;
            case "io":             arranger.isIoSectionVisible().set(on); break;
            case "effect_tracks":  arranger.areEffectTracksVisible().set(on); break;
            default: throw "unknown panel: " + p.panel;
        }
    },
    "arranger.zoom": function (p) {
        switch (bget(p, "mode", "fit")) {
            case "in":          arranger.zoomIn(); break;
            case "out":         arranger.zoomOut(); break;
            case "fit":         arranger.zoomToFit(); break;
            case "selection":   arranger.zoomToSelection(); break;
            case "fit_or_all":  arranger.zoomToFitSelectionOrAll(); break;
            default: throw "unknown zoom mode: " + p.mode;
        }
    },
    "arranger.lane_zoom": function (p) {
        var dir = bget(p, "direction", "in");
        var sc  = bget(p, "scope", "all");
        if (sc === "selected") {
            if (dir === "out") arranger.zoomOutLaneHeightsSelected(); else arranger.zoomInLaneHeightsSelected();
        } else {
            if (dir === "out") arranger.zoomOutLaneHeightsAll(); else arranger.zoomInLaneHeightsAll();
        }
    },

    // ── cursor clip / note editing ──
    "clip.select_arranger": function () { if (arrangerClip) gCursorClip = arrangerClip; },
    "clip.select_launcher": function () { if (launcherClip) gCursorClip = launcherClip; },
    "clip.set_step":   function (p) { gCursorClip.setStep(bget(p, "x", 0), bget(p, "y", 60), bget(p, "velocity", 100), bget(p, "duration", 0.25)); },
    "clip.toggle_step":function (p) { gCursorClip.toggleStep(bget(p, "x", 0), bget(p, "y", 60), bget(p, "velocity", 100)); },
    "clip.clear_step": function (p) { gCursorClip.clearStep(bget(p, "x", 0), bget(p, "y", 60)); },
    "clip.clear_all_steps": function () { gCursorClip.clearSteps(); },
    "clip.set_step_size":   function (p) { gCursorClip.setStepSize(bget(p, "size", 0.25)); },
    "clip.quantize":   function (p) { gCursorClip.quantize(bget(p, "amount", 1.0)); },
    "clip.transpose":  function (p) { gCursorClip.transpose(bget(p, "semitones", 0)); },
    "clip.set_loop":   function (p) {
        if (p.on !== undefined)     gCursorClip.isLoopEnabled().set(!!p.on);
        if (p.start !== undefined)  gCursorClip.getLoopStart().set(p.start);
        if (p.length !== undefined) gCursorClip.getLoopLength().set(p.length);
    },
    "clip.duplicate":  function () { gCursorClip.duplicate(); },
    "clip.set_name":   function (p) { gCursorClip.setName("" + bget(p, "name", "")); },

    // ── cue markers ──
    "cue.add":    function () { transport.addCueMarkerAtPlaybackPosition(); },
    "cue.delete": function (p) { cueMarkerBank.getItemAt(bget(p, "index", 0)).delete(); },
    "cue.launch": function (p) { cueMarkerBank.getItemAt(bget(p, "index", 0)).launch(!!bget(p, "quantized", false)); },

    // ── device ──
    "device.set_enabled":  function (p) { cursorDevice.isEnabled().set(!!bget(p, "on", true)); },
    "device.set_expanded": function (p) { cursorDevice.isExpanded().set(!!bget(p, "on", true)); },
    "device.select_next":     function () { cursorDevice.selectNext(); },
    "device.select_previous": function () { cursorDevice.selectPrevious(); },
    "device.set_remote": function (p) { remoteControlsPage.getParameter(bget(p, "index", 0)).value().set(bget(p, "value", 0.0)); },
    // select a remote-controls PAGE on the cursor device (each page exposes 8 params) so the
    // automation path can reach >8 device parameters. Async -> let it flush before reading remotes.
    "device.select_remote_page": function (p) { remoteControlsPage.selectedPageIndex().set(bget(p, "page", 0) | 0); return { page: bget(p, "page", 0) | 0 }; },
    "device.remote_page": function (p) {
        if (bget(p, "direction", "next") === "previous") remoteControlsPage.selectPreviousPage(true);
        else remoteControlsPage.selectNextPage(true);
    },

    // ── session ──
    "slot.launch": function (p) { trk(bget(p, "track", 0)).clipLauncherSlotBank().getItemAt(bget(p, "slot", 0)).launch(); },
    "slot.stop":   function (p) { trk(bget(p, "track", 0)).stop(); },
    "scene.launch":function (p) { sceneBank.getItemAt(bget(p, "scene", 0)).launch(); },

    // ── application ──
    "app.undo": function () { application.undo(); },
    "app.redo": function () { application.redo(); },
    "app.set_panel_layout": function (p) { application.setPanelLayout("" + bget(p, "layout", "ARRANGE")); },

    // ── project save/load (via the Action API - ApplicationProxy doesn't expose
    //    saveProject/openProject directly). Action IDs are the GUI action names;
    //    if Bitwig renames them this will silently no-op. Save-as / open-by-path
    //    aren't action-addressable (they open a file dialog) - handled in GUI.
    "project.save":    function () {
        var a = application.getAction("Save");
        if (a != null) a.invoke();
        return {ok: a != null, action: "Save"};
    },
    "project.save_as": function () {
        // Cannot pass a path through the action system - pops the OS file dialog.
        var a = application.getAction("Save As...");
        if (a != null) a.invoke();
        return {ok: a != null, note: "Action triggers GUI file dialog"};
    },
    "project.open_dialog": function () {
        var a = application.getAction("Open Project...");
        if (a != null) a.invoke();
        return {ok: a != null};
    },
    "project.new":     function () {
        var a = application.getAction("New");
        if (a != null) a.invoke();
        return {ok: a != null};
    },
    "app.list_actions": function () {
        var acts = application.getActions();
        var out = [];
        for (var i = 0; i < Math.min(acts.length, 500); i++) {
            out.push({id: "" + acts[i].getId(), name: "" + acts[i].getName()});
        }
        return out;
    },

    // ── device chain manipulation (cursorDevice - select track first) ──
    "device.delete":        function () { cursorDevice.deleteObject(); return {ok: true}; },
    "device.move_up":       function () { cursorDevice.beforeDeviceInsertionPoint().moveDevices(cursorDevice); return {ok: true}; },
    "device.move_down":     function () { cursorDevice.afterDeviceInsertionPoint().moveDevices(cursorDevice); return {ok: true}; },
    "device.select_first":  function () { cursorDevice.selectFirst(); },
    "device.select_last":   function () { cursorDevice.selectLast(); },
    "device.select_index":  function (p) {
        // walk to index from first
        cursorDevice.selectFirst();
        var n = bget(p, "index", 0) | 0;
        for (var i = 0; i < n; i++) cursorDevice.selectNext();
        return {index: n};
    },

    // ── all device-remote pages (not just active) ──
    "device.all_remote_pages": function () {
        var n = remoteControlsPage.pageCount().get() | 0;
        var pages = [], saved = remoteControlsPage.selectedPageIndex().get() | 0;
        for (var i = 0; i < n; i++) {
            remoteControlsPage.selectedPageIndex().set(i);
            var page = {index: i, name: ("" + remoteControlsPage.getName().get()), params: []};
            for (var k = 0; k < NUM_REMOTE; k++) {
                var rc = remoteControlsPage.getParameter(k);
                if (rc.exists().get()) {
                    page.params.push({index: k, name: ("" + rc.name().get()),
                                      value: rc.value().get()});
                }
            }
            pages.push(page);
        }
        remoteControlsPage.selectedPageIndex().set(saved);
        return pages;
    },

    // ── audio-clip descriptor introspection + timestretch ──
    // p: { depth?: 1 } -> walks gCursorClip's descriptor map (property IDs + values),
    // for discovering which descriptor IDs hold stretch mode/factor for audio clips.
    "clip.describe": function (p) {
        var depth = bget(p, "depth", 1) | 0;
        if (gCursorClip == null) return { error: "no cursor clip" };
        try {
            var cwo = gCursorClip.mX_();
            if (cwo == null) return { error: "cursor clip has no mX_ target" };
            // shallow walk: just enumerate this object's descriptors (no recursion)
            var descrs = _descriptors(cwo);
            var n = (descrs == null) ? 0 : descrs.size();
            var out = { _cls: (function () {
                try { return "" + _inv0(cwo, "bf"); } catch (e) { return "?"; }
            })(), props: [] };
            for (var i = 0; i < n; i++) {
                var d = descrs.get(i);
                var pid; try { pid = "" + _inv0(d, "ngq"); } catch (e) { pid = "i" + i; }
                var val = null;
                try { val = _jval(_inv1(d, "Xzy", cwo)); } catch (e) { val = "<err>"; }
                out.props.push({ id: pid, value: val });
            }
            return out;
        } catch (e) { return { error: "" + e }; }
    },

    // Try-set a descriptor by property ID. Used for stretch discovery + tweaking.
    // p: { prop_id: "6234", value: <num/bool/str> }. Returns ok/err.
    "clip.set_prop": function (p) {
        var prop = "" + bget(p, "prop_id", "");
        var val  = bget(p, "value", null);
        if (!prop) return { error: "prop_id required" };
        if (gCursorClip == null) return { error: "no cursor clip" };
        _runOnDocumentThread(cursorTrack, function () {
            try {
                var cwo = gCursorClip.mX_();
                if (cwo == null) throw "cursor clip has no mX_ target";
                var descrs = _descriptors(cwo);
                var n = (descrs == null) ? 0 : descrs.size();
                var d = null;
                for (var i = 0; i < n; i++) {
                    var dd = descrs.get(i);
                    if (("" + _inv0(dd, "ngq")) === prop) { d = dd; break; }
                }
                if (d == null) throw "prop " + prop + " not on cursor clip";
                // Try the standard setter: uEK(uo1, value)
                var m = _findMethod(d.getClass(), "uEK", 2, null);
                if (m == null) throw "no setter on prop " + prop;
                m.invoke(d, cwo, val);
                return { ok: true, prop_id: prop, value: val };
            } catch (e) { return { error: "" + e }; }
        });
        return { queued: true };
    },

    // ── per-step NoteStep attributes (extends clip.set_step with rich properties) ──
    // p: { x, y, attr: "duration"|"velocity"|"release"|"chance"|"pressure"|"timbre"|"pan"|"transpose"|"gain", value }
    "clip.set_step_attr": function (p) {
        var x = bget(p, "x", 0) | 0, y = bget(p, "y", 60) | 0;
        var attr = "" + bget(p, "attr", "velocity");
        var v = Number(bget(p, "value", 0));
        var s = gCursorClip.getStep(0, x, y);
        if (s == null || ("" + s.state()) === "Empty") return {error: "no step at (" + x + "," + y + ")"};
        if (attr === "duration")   s.setDuration(v);
        else if (attr === "velocity") s.setVelocity(v);
        else if (attr === "release")  s.setReleaseVelocity(v);
        else if (attr === "chance")   s.setChance(v);
        else if (attr === "pressure") s.setPressure(v);
        else if (attr === "timbre")   s.setTimbre(v);
        else if (attr === "pan")      s.setPan(v);
        else if (attr === "transpose")s.setTranspose(v);
        else if (attr === "gain")     s.setGain(v);
        else return {error: "unknown attr " + attr};
        return {ok: true, attr: attr, value: v};
    },

    // ── tempo automation (offline, inserts on master tempo) ──
    // Mirrors automation.write_offline but targets transport.tempo() directly.
    // ── audio clips: load a .wav into a launcher slot ──
    // p: { track, slot, path }. Uses replaceInsertionPoint().insertFile() which loads
    // the sample into the slot's audio clip. For arranger placement, launch the slot
    // then return-to-arrangement-via Copy, or duplicate the clip in-place.
    "slot.insert_audio_file": function (p) {
        var t = trk(bget(p, "track", 0) | 0);
        var slot = t.clipLauncherSlotBank().getItemAt(bget(p, "slot", 0) | 0);
        slot.replaceInsertionPoint().insertFile("" + bget(p, "path", ""));
        return { ok: true };
    },

    // ── load a .bwpreset file (same insertion-point machinery as .bwdevice) ──
    "device.insert_preset": function (p) {
        cursorTrack.endOfDeviceChainInsertionPoint().insertFile("" + bget(p, "path", ""));
        return { ok: true };
    },

    // ── modulator mapping (cxu_2 set_default_modulation_mapping op 3630 on
    //    modulation_source_atom / class 766). Reads modulation_sources (prop 5438) off
    //    the cursor device, picks one by index, then maps to a remote-control param.
    //    NOTE: this MAPS an existing modulator source -- it does NOT insert a modulator.
    //    Modulator insertion is API-blocked + cxu_2 only exposes preview-session commit,
    //    which would need multi-step orchestration. Use the GUI / "Insert Modulator..."
    //    action to add the modulator device first; then map it from here.
    // Trigger Bitwig's "Insert Modulator..." GUI dialog so the user can pick one.
    // Programmatic insert isn't reachable through any exposed handler today.
    "modulator.open_browser": function () {
        var a = application.getAction("Insert Modulator...")
                || application.getAction("Browse Modulators");
        if (a != null) { a.invoke(); return { ok: true, action: "" + a.getName() }; }
        return { ok: false, error: "no insert-modulator action found" };
    },

    // INSERT an audio clip from a .wav file onto the arranger at `start` (beats),
    // duration `dur` beats. Same playbook as modulator.insert:
    //   ArrangerClipInsertionPoint(HrV track, double startTime, double duration,
    //                              FSY=null, clk_2=null)
    //   .r3B(new File(path), ZjS.r3B, null)
    // (HrV is the track-document interface; byU implements it. byi_2.r3B(File, ZjS, hha)
    // at byi_2.java:130 is the base file dispatcher; both FSY and hha are nullable.)
    // p: { track: N, path: "...wav", start: beat, duration?: beat }
    "track.insert_audio_clip": function (p) {
        var trackIdx = bget(p, "track", 0) | 0;
        var path = "" + bget(p, "path", "");
        var start = Number(bget(p, "start", 0));
        var dur = Number(bget(p, "duration", 4));
        if (!path) return { error: "no path" };
        _runOnDocumentThread(cursorTrack, function () {
            var t = trackBank.getItemAt(trackIdx);
            var trackDoc = t.getDeepestTarget();
            if (trackDoc == null) throw "track " + trackIdx + " has no document target";
            // byU implements OkP() -> HrV (synthetic); the real accessor is TD() which
            // returns a `miV` that IS-A HrV. Use it.
            var trackAsHrV = _inv0(trackDoc, "TD");
            if (trackAsHrV == null) throw "trackDoc.TD() returned null";
            var ACIP = Java.type("com.bitwig.flt.document.core.iface.clipboard.clip.ArrangerClipInsertionPoint");
            // pick the 5-arg constructor: (HrV, double, double, FSY, clk_2)
            var ctors = ACIP.class.getDeclaredConstructors();
            var ctor = null;
            for (var i = 0; i < ctors.length; i++) {
                if (ctors[i].getParameterCount() === 5) { ctor = ctors[i]; break; }
            }
            if (ctor == null) throw "ArrangerClipInsertionPoint 5-arg ctor not found";
            ctor.setAccessible(true);
            var Dbl = Java.type("java.lang.Double");
            var aip = ctor.newInstance(trackAsHrV, Dbl.valueOf(start), Dbl.valueOf(dur), null, null);
            var File = Java.type("java.io.File");
            var ZjS = Java.type("ZjS");
            var ok = aip.r3B(new File(path), ZjS.r3B, null);
            return { dispatched: !!ok, path: path, start: start, duration: dur };
        });
        return { queued: true, path: path, start: start, duration: dur,
                 note: "async; outcome in openwig_bridge.log [auto]" };
    },

    // INSERT a modulator from a .bwmodulator file path. Discovered via
    // TestModulatorGrid.java:60-61 -- ModulatorGridInsertionPoint accepts
    // (peb_0 modulator_grid, int gridX, int gridY, PKE pke=null); then
    // byi_2.r3B(File, ZjS, hha) (line 130 of byi_2.java) loads the file +
    // dispatches the insert (hha callback nullable). Bypasses preview-session
    // orchestration entirely.
    // p: { path: "C:/.../LFO.bwmodulator", x?: 0, y?: 0 }
    "modulator.insert": function (p) {
        var path = "" + bget(p, "path", "");
        if (!path) return { error: "no path" };
        var gx = bget(p, "x", 0) | 0;
        var gy = bget(p, "y", 0) | 0;
        _runOnDocumentThread(cursorTrack, function () {
            var byU = cursorDevice.getDeepestTarget();
            if (byU == null) throw "no cursor device target";
            // walk to modulator_grid (cxt_2 prop 6727 on device)
            var descrs = _descriptors(byU.mX_());
            var mg = null;
            for (var i = 0; i < descrs.size(); i++) {
                var d = descrs.get(i);
                try {
                    if (("" + _inv0(d, "ngq")) === "6727") {
                        try { mg = _inv1(d, "uEK", byU); } catch (e) {}
                        break;
                    }
                } catch (e) {}
            }
            if (mg == null) throw "modulator_grid not reachable on cursor device";

            // construct ModulatorGridInsertionPoint(peb_0, int, int, PKE=null)
            var MGIP = Java.type("com.bitwig.flt.document.core.iface.clipboard.modulator.ModulatorGridInsertionPoint");
            var ctors = MGIP.class.getDeclaredConstructors();
            var ctor = null;
            for (var c = 0; c < ctors.length; c++) {
                if (ctors[c].getParameterCount() === 4) { ctor = ctors[c]; break; }
            }
            if (ctor == null) throw "ModulatorGridInsertionPoint 4-arg constructor not found";
            ctor.setAccessible(true);
            var Int = Java.type("java.lang.Integer");
            var mgip = ctor.newInstance(mg, Int.valueOf(gx), Int.valueOf(gy), null);

            // dispatch file insert: byi_2.r3B(File, ZjS, hha=null)
            var File = Java.type("java.io.File");
            var ZjS = Java.type("ZjS");
            var ok = mgip.r3B(new File(path), ZjS.r3B, null);
            return { dispatched: !!ok, path: path, x: gx, y: gy };
        });
        return { queued: true, path: path, note: "async; outcome in openwig_bridge.log [auto]" };
    },

    // SIDECHAIN: wire cursorDevice's sidechain input from another track's signal.
    //   p: { source_track: N }
    // Path discovered via ModuleGraphTests.java::sidechainPushingLatency:
    //   - sink device has a `tkG` component (extends BOg/bog_3) in its components list
    //   - tkG.fCq() returns the source-selector (dML/dml_2)
    //   - dML.r3B(QcL) sets the source. QcL = a device's audio output via GVZ.jB1().
    // The source-track's FIRST device's audio output is used as the source signal
    // (chain it back to read post-FX by picking last device instead).
    "device.set_sidechain_source": function (p) {
        var srcIdx = bget(p, "source_track", 0) | 0;
        var srcDevIdx = bget(p, "source_device_index", 0) | 0;
        _runOnDocumentThread(cursorTrack, function () {
            var sink = cursorDevice.getDeepestTarget();
            if (sink == null) throw "no cursor device target (select sink device first)";
            // 1. find the BOg (sidechain routing) component on the sink device
            var lxh = _inv0(sink, "kGL");
            if (lxh == null) throw "device has no kGL()";
            var ayb = _findMethod(lxh.getClass(), "AYB", 0, null);
            ayb.setAccessible(true);
            var components = ayb.invoke(lxh);
            var BOg = Java.type("BOg");
            var bog = null;
            for (var i = 0; i < components.size(); i++) {
                var ci = components.get(i);
                if (BOg.class.isAssignableFrom(ci.getClass())) { bog = ci; break; }
            }
            if (bog == null) throw "sink device has no BOg/sidechain routing component";
            // 2. get its source selector
            var selector = _inv0(bog, "fCq");
            if (selector == null) throw "BOg.fCq() returned null";

            // 3. obtain a source QcL = source track's specific device's audio output.
            //    createDeviceBank() is init-only, so reach the source device document
            //    obj directly. trackBank.getItemAt(srcIdx).getDeepestTarget() gives us
            //    the source track's runtime object.
            var srcTrack = trackBank.getItemAt(srcIdx);
            var srcTrackDoc = srcTrack.getDeepestTarget();
            if (srcTrackDoc == null) throw "source track document not reachable";
            // walk track -> device_chain (cxt_2 atom child) -> devices (cxs_2 list) -> [srcDevIdx]
            function _atomChild(uo1, propId) {
                var dl = _descriptors(uo1.mX_());
                for (var i = 0; i < dl.size(); i++) {
                    var d = dl.get(i);
                    try { if (("" + _inv0(d, "ngq")) === ("" + propId)) {
                        try { return _inv1(d, "uEK", uo1); } catch (e) {} } } catch (e) {}
                }
                return null;
            }
            function _listChildren(uo1, propId) {
                var dl = _descriptors(uo1.mX_());
                for (var i = 0; i < dl.size(); i++) {
                    var d = dl.get(i);
                    try { if (("" + _inv0(d, "ngq")) === ("" + propId)) {
                        return _relChildren(d, uo1); } } catch (e) {}
                }
                return null;
            }
            // track has device_chain at prop 356 (confirmed via debug_source_walk)
            var dchain = _atomChild(srcTrackDoc, 356);
            if (dchain == null) throw "source track has no device_chain (prop 356)";
            // walk dchain children for a cxs_2 list of devices; scan ALL list-relationships
            // and pick the first whose first element has a jB1() method (= a device)
            var devList = null, srcDevObj = null;
            var dchainDescr = _descriptors(dchain.mX_());
            for (var jj = 0; jj < dchainDescr.size(); jj++) {
                var dd = dchainDescr.get(jj);
                try {
                    var kids = _relChildren(dd, dchain);
                    if (kids != null && kids.size() > srcDevIdx) {
                        var candidate = kids.get(srcDevIdx);
                        // does it have jB1()?
                        var hasJB1 = _findMethod(candidate.getClass(), "jB1", 0, null);
                        if (hasJB1 != null) {
                            devList = kids; srcDevObj = candidate; break;
                        }
                    }
                } catch (e) {}
            }
            if (srcDevObj == null) throw "no device with jB1() at index " + srcDevIdx + " on source track";
            var srcQcL = _inv0(srcDevObj, "jB1");
            if (srcQcL == null) throw "source device jB1() returned null (no audio output)";

            // 4. wire selector -> source. dml_2 has multiple r3B overloads (QcL, Object,
            // boolean...). Pick the one whose parameter type is assignable from srcQcL's
            // class.
            var setM = null;
            var ms = selector.getClass().getMethods();
            for (var k = 0; k < ms.length; k++) {
                if (("" + ms[k].getName()) === "r3B" && ms[k].getParameterCount() === 1) {
                    var pt = ms[k].getParameterTypes()[0];
                    if (pt.isAssignableFrom(srcQcL.getClass())) { setM = ms[k]; break; }
                }
            }
            if (setM == null) throw "no compatible r3B setter on selector";
            setM.setAccessible(true);
            setM.invoke(selector, srcQcL);
            return { wired: true, source_track: srcIdx, source_device: srcDevIdx };
        });
        return { queued: true, note: "async; outcome in openwig_bridge.log [auto]" };
    },

    "modulator.list_sources": function () {
        // Read the device's modulation_sources list via internals (prop 5438).
        // Returns one entry per source with its display name (via hf() - found
        // by enumerating String-returning methods on the source instance).
        var byU = cursorDevice.getDeepestTarget();
        if (byU == null) return { error: "no cursor device target" };
        var cwo = byU.mX_();
        var descrs = _descriptors(cwo);
        var modList = null;
        for (var i = 0; i < descrs.size(); i++) {
            var d = descrs.get(i);
            try {
                if (("" + _inv0(d, "ngq")) === "5438") { modList = _relChildren(d, byU); break; }
            } catch (e) {}
        }
        if (modList == null) return [];
        var out = [];
        for (var j = 0; j < modList.size(); j++) {
            var s = modList.get(j);
            var info = { index: j, name: "?", id: "?" };
            try { info.name = "" + _inv0(s, "hf"); } catch (e) {}     // display name
            try { info.id   = "" + _inv0(s, "cB_"); } catch (e) {}    // internal code
            // fallback: blank display name -> use the id (e.g. "LFO", "RANDOM")
            if (!info.name || info.name === "null" || info.name === "" || info.name === "?") {
                info.name = info.id;
            }
            out.push(info);
        }
        return out;
    },

    // ── routing introspection (SETTING routing is API-blocked + cxu_2 has no
    //    setter, only `find_first_sidechain_source_command` getter; per-device
    //    sidechain inputs are not exposed at schema level). This handler is
    //    READ-ONLY: returns what the SourceSelector reports.
    "track.routing_info": function (p) {
        var t = trk(bget(p, "index", 0));
        var ss = t.sourceSelector();
        var info = { has_note_input: null, has_audio_input: null };
        try { ss.hasNoteInputSelected().markInterested(); info.has_note_input  = ss.hasNoteInputSelected().get(); } catch (e) {}
        try { ss.hasAudioInputSelected().markInterested(); info.has_audio_input = ss.hasAudioInputSelected().get(); } catch (e) {}
        try { info.can_hold_audio = t.canHoldAudioData().get(); } catch (e) {}
        try { info.can_hold_note  = t.canHoldNoteData().get();  } catch (e) {}
        return info;
    },

    // p: { source_index: N, dest: "remote"|"volume"|"pan", remote_index: M, amount: -1..1 }
    "modulator.map": function (p) {
        var srcIdx = bget(p, "source_index", 0) | 0;
        var dest = "" + bget(p, "dest", "remote");
        var amount = Number(bget(p, "amount", 0.5));
        _runOnDocumentThread(cursorTrack, function () {
            var byU = cursorDevice.getDeepestTarget();
            if (byU == null) throw "no cursor device target";
            // find modulation_sources list (prop 5438) and pick the Nth source
            var cwo = byU.mX_();
            var descrs = _descriptors(cwo);
            var modList = null;
            for (var i = 0; i < descrs.size(); i++) {
                var d = descrs.get(i);
                if (("" + _inv0(d, "ngq")) === "5438") { modList = _relChildren(d, byU); break; }
            }
            if (modList == null || modList.size() <= srcIdx) throw "source_index out of range";
            var src = modList.get(srcIdx);

            // resolve destination to the canonical float-param atom (fj) - the SAME
            // resolution the automation path uses. Passing the raw target of a remote
            // control here is a proxy atom the audio engine can't modulate, so the
            // mapping syncs to the engine malformed and crashes the native host.
            // _fjFrom() unwraps the proxy down to the real modulatable atom; for
            // volume/pan the atom already IS an fj, so they are unaffected.
            var pp;
            if (dest === "volume") pp = cursorTrack.volume();
            else if (dest === "pan") pp = cursorTrack.pan();
            else pp = remoteControlsPage.getParameter(bget(p, "remote_index", 0) | 0);
            var destAtom = _fjFrom(_invokeNoArg(pp.getDeepestTarget(), "getAtom"));
            if (destAtom == null) destAtom = _fjFrom(pp.getDeepestTarget());
            if (destAtom == null) throw "could not resolve target fj for dest '" + dest + "'";

            // build the cxu_2 set_default_modulation_mapping (op 3630, on class 766)
            // and call its executor: cxu_2.r3B(UO1, List)
            var WZK = Java.type("com.bitwig.flt.document.core.master.device.WZK");
            var cmd = WZK.SWC.Mhm();    // returns the cxu_2 (per WZK.java:140)
            var ArrayList = Java.type("java.util.ArrayList");
            var Dbl = Java.type("java.lang.Double");
            var args = new ArrayList();
            args.add(destAtom);
            args.add(Dbl.valueOf(amount));
            cmd.r3B(src, args);
            return { mapped: true, source_index: srcIdx, dest: dest, amount: amount };
        });
        return { queued: true, note: "async; outcome in openwig_bridge.log [auto]" };
    },

    "tempo.write_offline": function (p) {
        var pts = p.points; if (!pts || !pts.length) return { error: "no points" };
        _runOnDocumentThread(cursorTrack, function () {
            // tempo's atom lives on the transport / float_document - we resolve fj from
            // the tempo parameter proxy.
            var tt = transport.tempo();
            var pp = tt.value ? tt.value() : tt;
            var fj = _fjFrom(_invokeNoArg(pp.getDeepestTarget(), "getAtom"));
            if (fj == null) fj = _fjFrom(pp.getDeepestTarget());
            if (fj == null) throw "could not resolve fj for tempo";
            // fj IS the float_document that owns the tempo atom; its zer() returns
            // the transport-level automation_lanes (same as automationWriteOffline
            // does for track params, but sourced from the transport document, not
            // the cursor track).
            var al = _invokeNoArg(fj, "zer");
            var a1x = Java.type("com.bitwig.flt.document.core.master.a1x").r3B(fj);
            var oJk = Java.type("oJk"), LINEAR = oJk.Xzy, HOLD = oJk.r3B;
            var Dbl = Java.type("java.lang.Double"), Bl = Java.type("java.lang.Boolean");
            var m = _findAutomationInsert(al.getClass());
            var n = 0;
            for (var i = 0; i < pts.length; i++) {
                var pt = pts[i];
                var hasCurv = (pt.length > 2 && pt[2] != null);
                var curv = hasCurv ? pt[2] : 0.0;
                var interp = (pt.length > 3 && ("" + pt[3]) === "hold") ? HOLD : LINEAR;
                m.invoke(al, a1x, Dbl.valueOf(pt[0]), Dbl.valueOf(pt[1]), Dbl.valueOf(curv),
                         Bl.valueOf(hasCurv), Bl.FALSE, interp, null);
                n++;
            }
            return { inserted: n, param: "tempo" };
        });
        return { queued: pts.length, note: "async; outcome in openwig_bridge.log [auto]" };
    }
};

// ── required stubs ────────────────────────────────────────────────────────────

function flush() {}
function exit() {
    try { if (gConnection) gConnection.disconnect(); } catch (e) {}
}
