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
var gProbe = null, gProbeErr = null;         // resolver self-test report (JSON-able object); see resolver.probe
var _AUTO_SYM = null;                         // cached structurally-discovered automation symbols
var gBlindDiscovery = false;                  // test switch: structural discovery only (no name hints, no fallback)
// Central obfuscated-symbol table. Defaults are the seed (current-build) names; doctor
// resolves + validates them and writes a cache, which init loads to overwrite these. The
// reflection sites read names from here so a re-obfuscated build keeps working once cached.
// Resolved obfuscated-symbol table. NO obfuscated names are hardcoded here: it is populated
// at init from the bootstrap DATA file (symbols_default.json, shipped + installed next to the
// cache) and then overwritten per-build by doctor's validated cache (symbols_cache.json).
// Reflection sites read names from here, so a re-obfuscated build keeps working once doctor
// has refreshed the cache. Numeric op-ids in the data are protocol data, not obfuscated names.
var SYM = { clipCmd: {}, noteCmd: {}, audio: {}, szFilter: {} };
var ACIP_CLASS = "com.bitwig.flt.document.core.iface.clipboard.clip.ArrangerClipInsertionPoint"; // stable, non-obfuscated
var gSymSource = "seed";                       // "seed"|"cache"|"discovered+cached"|"defaults ..."|"UNRESOLVED ..."  (validated iff cache or discovered+cached)
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
    // mark cursor-track volume/pan values so the resolver self-test can read their raw +
    // normalized values (the normalize-fn probe); .get()/.getRaw() throw without this.
    cursorTrack.volume().markInterested(); cursorTrack.volume().value().markInterested();
    cursorTrack.pan().markInterested();    cursorTrack.pan().value().markInterested();
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

    _loadSymbols();              // load obfuscated-name mapping from the data file + per-build cache
    flog("symbol source: " + gSymSource + "  (fingerprint " + _fingerprint() + ")");

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

// doctor is MANDATORY: until the symbol mapping has been VALIDATED for this exact Bitwig
// build, refuse every internals-dependent op. The mapping is validated when a matching
// per-build cache exists (loaded at init) or when doctor validated + cached it this session.
// The only methods allowed before that are the ones `openwig doctor` itself needs to probe
// and validate the build, plus basic connectivity - so doctor can break the chicken-and-egg.
function _symbolsValidated() { return gSymSource === "cache" || gSymSource === "discovered+cached"; }
var _DOCTOR_METHODS = {
    "ping": true, "hello": true, "ops.done": true, "host.version": true, "state.snapshot": true,
    "track.create": true, "track.select": true, "track.delete": true,
    "obj.walk": true, "obj.walk_result": true, "track.insert_audio_clip": true
};
function _gateAllows(method) {
    return _symbolsValidated() || method.indexOf("resolver.") === 0 || _DOCTOR_METHODS[method] === true;
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
    if (!_gateAllows(method)) {
        log("BLOCKED (symbols not validated; run doctor): " + method);
        if (id !== null) sendObj({ jsonrpc: "2.0", id: id, error: { code: -32002,
            message: "openwig: symbols are not validated for this Bitwig build (" + gSymSource +
                     "). Run `openwig doctor` to resolve + cache them, then retry." } });
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
// fj = the document automatable-value base; reach it from a control-surface target. The fj
// class name is resolved into SYM.fj (bootstrap seed, overwritten by doctor's cache and by
// automation discovery), so this is not a hardcoded dependence.
function _fjFrom(obj) {
    var fjC = Java.type(SYM.fj).class;
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
    return _inv0(cwo, SYM.KRt);        // java.util.List<cxz_2>
}
// uo1.mX_() -> descriptor container, routed through the resolved name.
function _mx(uo1) { return _invokeNoArg(uo1, SYM.mX_); }
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
// Bitwig's own serialize filter: the reader includes a descriptor only if it passes. The
// filter singleton (a static SZo field) + its (descriptor, parent)->boolean method come from
// SYM.szFilter (data / cache). Falls back to "include all" (nI_ still gates) if unresolved.
var _SZFILT = null;
function _szPass(d, uo1) {
    if (_SZFILT === null) {
        try { var f = Java.type(SYM.SZo).class.getDeclaredField(SYM.szFilter.field); f.setAccessible(true); _SZFILT = f.get(null); }
        catch (e) { _SZFILT = false; }
    }
    if (!_SZFILT) return true;
    var m = _findMethod(_classOf(_SZFILT), SYM.szFilter.method, 2, SYM.szFilter.param);
    if (m == null) return true;
    try { return !!m.invoke(_SZFILT, d, uo1); } catch (e) { return true; }
}
function _relChildren(d, uo1) {                     // null if d is not a relationship
    var m = _findMethod(_classOf(d), SYM.uEK, 2, "String");
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
    try { cwo = _mx(uo1); } catch (e) { return { _err: "mX_: " + e }; }
    try { o._cls = "" + _inv0(cwo, SYM.bf); } catch (e) { o._cls = "?"; }
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
        try { if (!_inv0(d, SYM.nI_) || (!opts.noFilter && !_szPass(d, uo1))) continue; }
        catch (e) { if (depth === 0 && !o._ferr) o._ferr = "" + e; continue; }
        var pid; try { pid = "" + _inv0(d, SYM.ngq); } catch (e) { pid = "i" + i; }
        budget.n--;
        var kids = null;
        try { kids = _relChildren(d, uo1); } catch (e) { kids = null; }
        if (kids != null) {
            var arr = [], nk = kids.size();
            for (var j = 0; j < nk; j++) {
                if (budget.n <= 0) { o._trunc = true; break; }
                var k = kids.get(j);
                if (depth < maxDepth) arr.push(_walkObj(k, depth + 1, maxDepth, budget, opts));
                else { var rc; try { rc = "" + _inv0(_mx(k), SYM.bf); } catch (e) { rc = "?"; } arr.push({ _ref: rc }); }
            }
            o[pid] = arr;
        } else {
            try { o[pid] = _jval(_inv1(d, SYM.Xzy, uo1)); } catch (e) { o[pid] = "<err>"; }
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

// ── name-free (structural) discovery of the arranger-automation cluster ─────────
// The obfuscated names below are stable for one Bitwig build but get re-obfuscated each
// release. To survive a release, we IDENTIFY the same symbols by SHAPE instead of by name,
// anchored on the stable public-API objects (a track target + a parameter proxy). This is
// the headline path ("write arranger automation, which the official API can't"); the other
// internal paths still use the hardcoded names and are covered by the doctor self-test.
function _dtype(t) { return "" + t.getName(); }
// Robust runtime class of a host object. Inside a long reflection loop on the document
// thread, the JS-side `obj.getClass()` member access intermittently fails with a GraalJS
// "Message not supported" interop error; invoking Object.getClass() REFLECTIVELY avoids it.
var _GETCLASS = null;
function _classOf(o) {
    if (_GETCLASS == null) { _GETCLASS = Java.type("java.lang.Object").class.getMethod("getClass"); _GETCLASS.setAccessible(true); }
    return _GETCLASS.invoke(o);
}
// ── op-id command discovery: resolve obfuscated command-host classes (clip create, note
// insert, ...) by the STABLE numeric op-id the command exposes via ngq(), rather than by
// hardcoded class name. The op-id is wire-protocol data (not obfuscated), so this survives a
// re-obfuscated build. Scans the Bitwig jar's default package (one-time, at doctor).
var _JAR_CLASSES = null, _gCmdScanInstantiated = 0;
function _jarDefaultPkgClasses() {
    if (_JAR_CLASSES) return _JAR_CLASSES;
    var loc = host.getClass().getProtectionDomain().getCodeSource().getLocation();
    var jarFile = new (Java.type("java.io.File"))(loc.toURI());
    var zf = new (Java.type("java.util.zip.ZipFile"))(jarFile), en = zf.entries(), names = [];
    while (en.hasMoreElements()) {
        var n = "" + en.nextElement().getName();
        if (n.length < 7 || n.substring(n.length - 6) !== ".class") continue;
        if (n.indexOf("/") === -1) names.push(n.substring(0, n.length - 6));
    }
    zf.close(); _JAR_CLASSES = names; return names;
}
// First 2-arg method on rt (hierarchy) taking (non-primitive [byU-assignable if given], List).
function _hasExec(rt, byUClass, ListC) {
    var c = rt;
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i]; if (m.getParameterCount() !== 2) continue;
            var ps = m.getParameterTypes();
            if (ps[0].isPrimitive() || !ListC.isAssignableFrom(ps[1])) continue;
            if (byUClass != null && !ps[0].isAssignableFrom(byUClass)) continue;
            return "" + m.getName();
        }
        c = c.getSuperclass();
    }
    return null;
}
// Find command hosts for a SET of op-ids in a single jar pass (early-stops once all are
// found). Returns { opid: {cls, field, factory, exec} }. `byUClass`, if given, requires the
// command's exec to accept it as the first arg (narrows + speeds the clip-create case).
// Does cls (hierarchy) declare a no-arg int/long/short getter? (a command exposes its op-id
// through one). Used to pre-filter command candidates without depending on the reader's ngq.
function _hasNumericNoArg(cls) {
    var c = cls;
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            if (ms[i].getParameterCount() !== 0) continue;
            var rn = "" + ms[i].getReturnType().getName();
            if (rn === "int" || rn === "long" || rn === "short") return true;
        }
        c = c.getSuperclass();
    }
    return false;
}
// Scan a command's no-arg int/long getters; return the first value that is in `want`, else null.
function _cmdMatchOpId(cmd, want) {
    var c = _classOf(cmd), seen = {};
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i]; if (m.getParameterCount() !== 0) continue;
            var rn = "" + m.getReturnType().getName();
            if (rn !== "int" && rn !== "long" && rn !== "short") continue;
            var nm = "" + m.getName(); if (seen[nm]) continue; seen[nm] = 1;
            try { m.setAccessible(true); var id = "" + m.invoke(cmd); if (want[id]) return id; } catch (e) {}
        }
        c = c.getSuperclass();
    }
    return null;
}
function _findCommandsByOpIds(opids, byUClass) {
    var names = _jarDefaultPkgClasses();
    var Class = Java.type("java.lang.Class"), Mod = Java.type("java.lang.reflect.Modifier");
    var ListC = Java.type("java.util.List").class, cl = host.getClass().getClassLoader();
    var want = {}, found = {}, remaining = 0;
    for (var w = 0; w < opids.length; w++) { want["" + opids[w]] = true; remaining++; }
    for (var i = 0; i < names.length && remaining > 0; i++) {
        var cls; try { cls = Class.forName(names[i], false, cl); } catch (e) { continue; }
        var fields; try { fields = cls.getDeclaredFields(); } catch (e) { continue; }
        for (var f = 0; f < fields.length && remaining > 0; f++) {
            if (!Mod.isStatic(fields[f].getModifiers())) continue;
            var T = fields[f].getType(); if (T.isPrimitive() || T.isArray()) continue;
            var tc = T, seenM = {};
            while (tc != null) {
                var tms = tc.getDeclaredMethods();
                for (var m = 0; m < tms.length; m++) {
                    var M = tms[m]; if (M.getParameterCount() !== 0) continue;
                    var mn = "" + M.getName(); if (seenM[mn]) continue; seenM[mn] = 1;
                    var RT = M.getReturnType(); if (RT.isPrimitive() || RT.isArray()) continue;
                    if (!_hasNumericNoArg(RT)) continue;                 // command exposes a numeric op-id
                    var execName = _hasExec(RT, byUClass, ListC); if (execName == null) continue;
                    try {
                        fields[f].setAccessible(true);
                        var val = fields[f].get(null); if (val == null) continue;
                        M.setAccessible(true); var cmd = M.invoke(val); if (cmd == null) continue;
                        _gCmdScanInstantiated++;
                        var id = _cmdMatchOpId(cmd, want);
                        if (id != null && !found[id]) {
                            found[id] = { cls: names[i], field: "" + fields[f].getName(), factory: mn, exec: execName };
                            remaining--;
                        }
                    } catch (e) {}
                }
                tc = tc.getSuperclass();
            }
        }
    }
    return found;
}

// True if cls (or any superclass/interface) declares a no-arg method named `name`, including
// non-public ones (getMethods() would miss those). Used by jar-scan structural fingerprints.
function _hasNoArgNamed(cls, name) {
    var c = cls;
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++)
            if (ms[i].getParameterCount() === 0 && ("" + ms[i].getName()) === name) return true;
        var ifs = c.getInterfaces();
        for (var k = 0; k < ifs.length; k++) if (_hasNoArgNamed(ifs[k], name)) return true;
        c = c.getSuperclass();
    }
    return false;
}

// The breakpoint-insert method has a very specific 8-arg shape:
//   (valueRef, double time, double value, double curvature, boolean, boolean, Enum interp, Object)
// Find it on a class hierarchy by that shape alone (no name). Returns {method, avr, interp}.
function _findInsertShape(cls) {
    var c = cls;
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i]; if (m.getParameterCount() !== 8) continue;
            var p = m.getParameterTypes();
            if (_dtype(p[1]) !== "double" || _dtype(p[2]) !== "double" || _dtype(p[3]) !== "double") continue;
            if (_dtype(p[4]) !== "boolean" || _dtype(p[5]) !== "boolean") continue;
            if (!p[6].isEnum()) continue;
            if (p[0].isPrimitive() || p[0].isEnum() || p[0].isArray()) continue;
            m.setAccessible(true);
            return { method: m, avr: p[0], interp: p[6] };
        }
        c = c.getSuperclass();
    }
    return null;
}
// The value-ref identity factory: a static 1-arg method on the avr class returning an avr.
// Its single parameter type IS the value base (fj). Returns {factory, fjClass}.
function _findAvrFactory(avr) {
    var Mod = Java.type("java.lang.reflect.Modifier");
    var ms = avr.getDeclaredMethods();
    for (var i = 0; i < ms.length; i++) {
        var m = ms[i];
        if (!Mod.isStatic(m.getModifiers()) || m.getParameterCount() !== 1) continue;
        if (!avr.isAssignableFrom(m.getReturnType())) continue;
        var pt = m.getParameterTypes()[0];
        if (pt.isPrimitive() || pt.isEnum() || pt.isArray()) continue;
        m.setAccessible(true);
        return { factory: m, fjClass: pt };
    }
    return null;
}
// An instance of fjClass reachable from obj (itself, or via one of its no-arg getters).
function _reachInstance(obj, fjClass) {
    if (obj == null) return null;
    if (fjClass.isInstance(obj)) return obj;
    var c = _classOf(obj);
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i];
            if (m.getParameterCount() !== 0) continue;
            var rt = m.getReturnType(); if (rt.isPrimitive() || rt === java.lang.Void.TYPE) continue;
            var nm = "" + m.getName();
            try { var r = _invokeNoArg(obj, nm); if (r != null && fjClass.isInstance(r)) return r; } catch (e) {}
        }
        c = c.getSuperclass();
    }
    return null;
}
// Pick LINEAR/HOLD interpolation constants. The interp enum keeps SEMANTIC constant names
// ("LINEAR", "HOLD"/"STEP") - not obfuscated symbols - so we match those directly (robust
// across releases, and legitimate even in blind mode). Falls back to the first constant if
// names are unrecognized; point insertion is unaffected by interpolation type.
function _pickInterp(interpEnum) {
    var consts = interpEnum.getEnumConstants();
    var LIN = null, HOLD = null;
    for (var k = 0; k < consts.length; k++) {
        var nm = ("" + consts[k].name()).toUpperCase();
        if (nm.indexOf("LIN") === 0) LIN = consts[k];
        else if (nm.indexOf("HOLD") === 0 || nm.indexOf("STEP") === 0) HOLD = consts[k];
    }
    var resolved = (LIN != null && HOLD != null);
    if (LIN == null) LIN = consts.length ? consts[0] : null;
    if (HOLD == null) HOLD = LIN;
    return { LINEAR: LIN, HOLD: HOLD, resolved: resolved };
}
// Every no-arg getter on byU whose returned object's concrete class carries the insert
// shape. The shape is NOT unique (several lane containers match) - only one is the real
// track automation_lanes, so the caller validates by execution. Getters are invoked BY NAME
// via _invokeNoArg: invoking the reflected Method object directly returns a lazy GraalJS
// value that fails ("Message not supported") when forced for some accessors. `accessor` is
// the method NAME. When not blind, the known accessor name leads.
function _autoCandidates(byU) {
    var out = [], seen = {}, c = _classOf(byU);
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i];
            if (m.getParameterCount() !== 0) continue;
            var nm = "" + m.getName(); if (seen[nm]) continue; seen[nm] = 1;
            var rt = m.getReturnType();
            if (rt.isPrimitive() || rt === java.lang.Void.TYPE || rt.isArray()) continue;
            var obj; try { obj = _invokeNoArg(byU, nm); } catch (e) { continue; }
            if (obj == null) continue;
            var sh; try { sh = _findInsertShape(_classOf(obj)); } catch (e) { continue; }
            if (sh) out.push({ accessor: nm, shape: sh, name: nm });
        }
        c = c.getSuperclass();
    }
    var hint = SYM.autoLanes;   // known-good lanes accessor (from data); try it first, not blind
    if (!gBlindDiscovery && hint) out.sort(function (a, b) { return (b.name === hint) - (a.name === hint); });
    return out;
}
// Insert points with a resolved symbol set S. Throws if the lane is not a valid target
// (wrong candidates throw here - that is how the caller tells them apart). Returns count.
function _doInsert(S, byU, pp, pts) {
    var al = _invokeNoArg(byU, S.alAccessor);
    var fjInst = _reachInstance(pp.getDeepestTarget(), S.fjClass);
    if (fjInst == null) throw "value base (fj) instance not reachable from param";
    var avr = S.factory.invoke(null, fjInst);
    var Dbl = Java.type("java.lang.Double"), Bl = Java.type("java.lang.Boolean");
    var n = 0;
    for (var i = 0; i < pts.length; i++) {
        var pt = pts[i];
        var hasCurv = (pt.length > 2 && pt[2] != null);
        var curv = hasCurv ? pt[2] : 0.0;
        var interp = (pt.length > 3 && ("" + pt[3]) === "hold") ? S.HOLD : S.LINEAR;
        S.insert.invoke(al, avr, Dbl.valueOf(pt[0]), Dbl.valueOf(pt[1]), Dbl.valueOf(curv),
                        Bl.valueOf(hasCurv), Bl.FALSE, interp, null);
        n++;
    }
    return n;
}
// Insert via structurally-discovered symbols (no obfuscated names). Tries each shape-matching
// candidate and keeps the first whose insert actually succeeds, caching it for the session.
// Throws if none validate.
function _insertAutoDiscovered(byU, pp, pts) {
    if (_AUTO_SYM) return _doInsert(_AUTO_SYM, byU, pp, pts);   // validated this session
    var cands = _autoCandidates(byU);
    if (!cands.length) throw "no automation_lanes candidate (insert shape) found";
    var lastErr = "no candidate validated";
    for (var ci = 0; ci < cands.length; ci++) {
        var sh = cands[ci].shape;
        var fac = _findAvrFactory(sh.avr); if (!fac) { lastErr = "no value-ref factory"; continue; }
        var ip = _pickInterp(sh.interp);
        var cand = { alAccessor: cands[ci].accessor, insert: sh.method, avr: sh.avr,
                     factory: fac.factory, fjClass: fac.fjClass, interp: sh.interp,
                     LINEAR: ip.LINEAR, HOLD: ip.HOLD, interpResolved: ip.resolved };
        try { var n = _doInsert(cand, byU, pp, pts); _AUTO_SYM = cand; return n; }   // cache AFTER success
        catch (e) { lastErr = "" + e; }
    }
    throw lastErr;
}
// Core arranger-automation insert. MUST run on the document-edit thread. Resolves the
// automation cluster purely by name-free structural discovery (validated by execution).
// Returns { inserted, param, via }.
var gAutoVia = "?";
function _insertAutomationPoints(byU, paramProxy, which, pts) {
    var pp = paramProxy();
    var n = _insertAutoDiscovered(byU, pp, pts);
    gAutoVia = "discovered";
    return { inserted: n, param: which, via: "discovered" };
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
        return _insertAutomationPoints(byU, paramProxy, which, pts);
    });
    return { queued: pts.length, param: which, note: "async; outcome in openwig_bridge.log [auto]" };
}

// OFFLINE arranger-clip create + fill, in-process. Calls the same GUI commands the wire
// op 0x1cb6 (track.insert_instrument_clip_on_arranger_command, prop 7350) and op 0x1cb5
// (instrument_note_clip_event.insert_note_command, prop 7349) would dispatch - reached via
// cxu_2.r3B(UO1, List) (cxu_2.java:82 -> cxr_22.Xzy((XDd)uO1, this.ngq(), list)). Replaces
// the wire path (mitm_daemon + port 7880) end-to-end. Cursor track must be selected first.
//   p: { start: beats, duration: beats, notes: [[channel, key, start, dur, vel], ...] }
// Core arranger-clip create + note fill. MUST run on the document-edit thread. Calls the
// same GUI commands the wire ops would dispatch, reached via internal command objects.
// Extracted so the public handler and the resolver self-test share one path.
// Returns { created, notes, start, duration }.
// Resolve a command spec {cls, field, factory, exec} into { cmd, exec(Method) }. The field
// is a static singleton, factory() yields the command, exec(target, List) dispatches it.
function _findField(cls, name) {
    var c = cls; while (c != null) { try { return c.getDeclaredField(name); } catch (e) {} c = c.getSuperclass(); }
    return null;
}
function _cmdResolve(spec) {
    var clsObj = Java.type(spec.cls).class;
    var fld = _findField(clsObj, spec.field); if (fld == null) throw "command field " + spec.cls + "." + spec.field + " not found";
    fld.setAccessible(true);
    var holder = fld.get(null); if (holder == null) throw "command field " + spec.field + " is null";
    var cmd = _invokeNoArg(holder, spec.factory);
    var em = _findMethod(_classOf(cmd), spec.exec, 2, null);
    if (em == null) throw "command exec " + spec.exec + " not found";
    return { cmd: cmd, exec: em };
}
function _insertClip(byU, start, dur, notes) {
    var ArrayList = Java.type("java.util.ArrayList");
    var Dbl = Java.type("java.lang.Double"), Int = Java.type("java.lang.Integer");
    var args1 = new ArrayList();
    args1.add(Dbl.valueOf(start)); args1.add(Dbl.valueOf(dur));
    var cc = _cmdResolve(SYM.clipCmd);                    // op-id-resolved (seed default = X2S)
    var clipDoc = cc.exec.invoke(cc.cmd, byU, args1);
    if (clipDoc == null) throw "clip create returned null";
    var nc = _cmdResolve(SYM.noteCmd);                    // op-id-resolved (seed default = alU)
    for (var i = 0; i < notes.length; i++) {
        var n = notes[i];
        var args2 = new ArrayList();
        args2.add(Int.valueOf(n[0] | 0));            // channel
        args2.add(Int.valueOf(n[1] | 0));            // key
        args2.add(Dbl.valueOf(Number(n[2])));        // start_in_clip
        args2.add(Dbl.valueOf(Number(n[3])));        // duration
        args2.add(Dbl.valueOf(Number(n[4])));        // velocity
        nc.exec.invoke(nc.cmd, clipDoc, args2);
    }
    return { created: 1, notes: notes.length, start: start, duration: dur };
}

// ── arranger audio-clip insert ──────────────────────────────────────────────────
// ArrangerClipInsertionPoint (ACIP) is a STABLE class. Resolve its 5-arg constructor, its
// (File, mode, hook)->ok dispatch method (the only inherited method whose first arg is a
// File), the mode class (dispatch param[1]) and the mode singleton (the mode class's only
// self-typed static field). The track-as-HrV accessor name comes from SYM.audio.hrv (data /
// cache / validated by doctor). All structural except the HrV accessor.
function _acipParts() {
    var ACIP = Java.type(ACIP_CLASS).class, FileC = Java.type("java.io.File").class;
    var ctors = ACIP.getDeclaredConstructors(), ctor = null;
    for (var i = 0; i < ctors.length; i++) if (ctors[i].getParameterCount() === 5) { ctor = ctors[i]; break; }
    if (ctor == null) throw "ACIP 5-arg constructor not found";
    ctor.setAccessible(true);
    var dispatch = null, c = ACIP;
    while (c != null && dispatch == null) {
        var ms = c.getDeclaredMethods();
        for (var j = 0; j < ms.length; j++) {
            var m = ms[j], ps = m.getParameterTypes();
            if (ps.length === 3 && FileC.isAssignableFrom(ps[0])) { m.setAccessible(true); dispatch = m; break; }
        }
        c = c.getSuperclass();
    }
    if (dispatch == null) throw "ACIP file-dispatch method not found";
    var zjsType = dispatch.getParameterTypes()[1];
    var Mod = Java.type("java.lang.reflect.Modifier"), mode = null, zfs = zjsType.getDeclaredFields();
    for (var k = 0; k < zfs.length; k++) {
        if (Mod.isStatic(zfs[k].getModifiers()) && zjsType.isAssignableFrom(zfs[k].getType())) {
            try { zfs[k].setAccessible(true); mode = zfs[k].get(null); } catch (e) {}
            if (mode != null) break;
        }
    }
    if (mode == null) throw "audio-insert mode constant not found";
    return { ctor: ctor, dispatch: dispatch, mode: mode, hrvType: ctor.getParameterTypes()[0] };
}
function _insertAudioClip(byU, path, start, dur, hrvName) {
    var P = _acipParts(), File = Java.type("java.io.File"), Dbl = Java.type("java.lang.Double");
    var hn = hrvName || SYM.audio.hrv;
    var hrv = _invokeNoArg(byU, hn);
    if (hrv == null) throw "track-as-HrV accessor '" + hn + "' returned null";
    var aip = P.ctor.newInstance(hrv, Dbl.valueOf(start), Dbl.valueOf(dur), null, null);
    var ok = P.dispatch.invoke(aip, new File(path), P.mode, null);
    return { dispatched: !!ok };
}

function _clipCreateAndFill(p) {
    var start = Number(p.start || 0), dur = Number(p.duration || 4);
    var notes = p.notes || [];
    _runOnDocumentThread(cursorTrack, function () {
        var byU = cursorTrack.getDeepestTarget();
        if (byU == null) throw "no track target (select a track first)";
        return _insertClip(byU, start, dur, notes);
    });
    return { queued: notes.length, start: start, duration: dur, note: "async; outcome in openwig_bridge.log [auto]" };
}

// ── name-free (structural) discovery of the descriptor-reader cluster ───────────
// The reader walks the document graph via obfuscated methods: uo1.mX_() -> container,
// container.KRt() -> descriptor list, container.bf() -> class name, and per descriptor
// ngq() (numeric prop id), nI_() (is-serialized), Xzy(uo1) (scalar value), uEK(uo1,String)
// (child relationship). We rediscover these by SHAPE + BEHAVIOUR, anchored on a known
// document object (a track target), so the read path survives re-obfuscation.
var _JLIST = null;
function _listClass() { if (_JLIST == null) _JLIST = Java.type("java.util.List").class; return _JLIST; }
function _isNumericStr(s) {
    if (s == null) return false; s = "" + s; if (!s.length) return false;
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c < 48 || c > 57) return false; }
    return true;
}
// The shape isn't unique, so discovery gathers CANDIDATE name-sets and the caller validates
// each by execution (walk, look for known sentinels). A candidate carries several Xzy
// (value-getter) options because the right one can only be told apart behaviourally.
function _descriptorShape(d0) {
    var dCls; try { dCls = _classOf(d0); } catch (e) { return null; }
    var numOpts = [], boolNoArg = [], xzyOpts = [], uEK = null, c = dCls, seen = {};
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i], nm = "" + m.getName(); if (seen[nm]) continue; seen[nm] = 1;
            var pc = m.getParameterCount(), rt = m.getReturnType(), rtn = "" + rt.getName();
            if (pc === 0) {
                if (rtn === "boolean") boolNoArg.push(nm);
                else if (rtn === "java.lang.String") numOpts.push({ name: nm, str: true });
                else if (rtn === "int" || rtn === "long" || rtn === "short") numOpts.push({ name: nm, str: false });
            } else if (pc === 1 && !rt.isPrimitive() && rt !== java.lang.Void.TYPE && !m.getParameterTypes()[0].isPrimitive()) xzyOpts.push(nm);
            else if (pc === 2 && ("" + m.getParameterTypes()[1].getSimpleName()) === "String") uEK = nm;
        }
        c = c.getSuperclass();
    }
    if (uEK == null || !xzyOpts.length) return null;   // ngq not required to validate the skeleton
    // ngq (prop id) candidates: no-arg methods returning a numeric value (String-numeric or
    // int/long/short). The exact one is chosen later by value-uniqueness across the container.
    var ngqOpts = [];
    for (var k = 0; k < numOpts.length; k++) {
        try {
            var v = _invokeNoArg(d0, numOpts[k].name);
            if (numOpts[k].str) { if (_isNumericStr(v)) ngqOpts.push(numOpts[k].name); }
            else if (v != null) ngqOpts.push(numOpts[k].name);
        } catch (e) {}
    }
    var nI_ = null;                                   // no-arg boolean (is-serialized): true on serialized props
    for (var b = 0; b < boolNoArg.length; b++)
        try { if (_invokeNoArg(d0, boolNoArg[b]) === true) { nI_ = boolNoArg[b]; break; } } catch (e) {}
    if (nI_ == null && boolNoArg.length) nI_ = boolNoArg[0];
    return { ngqOpts: ngqOpts, nI_: nI_, uEK: uEK, xzyOpts: xzyOpts };
}
// All candidate reader name-sets reachable from uo1 (each carries xzyOpts to be validated).
function _readerCandidates(uo1) {
    var out = [], c = _classOf(uo1), seenG = {};
    while (c != null) {
        var ms = c.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
            var g = ms[i]; if (g.getParameterCount() !== 0) continue;
            var gn = "" + g.getName(); if (seenG[gn]) continue; seenG[gn] = 1;
            var rt = g.getReturnType(); if (rt.isPrimitive() || rt === java.lang.Void.TYPE || rt.isArray()) continue;
            var cwo; try { cwo = _invokeNoArg(uo1, gn); } catch (e) { continue; }
            if (cwo == null) continue;
            var cwoCls; try { cwoCls = _classOf(cwo); } catch (e) { continue; }
            var listM = [], strM = [], cc = cwoCls, seenM = {};
            while (cc != null) {
                var ms2 = cc.getDeclaredMethods();
                for (var j = 0; j < ms2.length; j++) {
                    var m = ms2[j]; if (m.getParameterCount() !== 0) continue;
                    var nm = "" + m.getName(); if (seenM[nm]) continue; seenM[nm] = 1;
                    var r = m.getReturnType();
                    if (_listClass().isAssignableFrom(r)) listM.push(nm);
                    else if (("" + r.getName()) === "java.lang.String") strM.push(nm);
                }
                cc = cc.getSuperclass();
            }
            for (var l = 0; l < listM.length; l++) {
                var lst; try { lst = _invokeNoArg(cwo, listM[l]); } catch (e) { continue; }
                if (lst == null) continue;
                var sz; try { sz = lst.size(); } catch (e) { continue; }
                if (sz === 0) continue;
                // the descriptor list is heterogeneous: sample several elements to find a
                // RICH descriptor (numeric ngq + uEK relationship) that yields the full shape.
                var sh = null, lim = Math.min(sz, 60);
                for (var di = 0; di < lim && !sh; di++) {
                    var dx; try { dx = lst.get(di); } catch (e) { continue; }
                    if (dx != null) sh = _descriptorShape(dx);
                }
                if (!sh) continue;
                var bf = null;
                for (var s = 0; s < strM.length; s++)
                    try { var v = _invokeNoArg(cwo, strM[s]); if (v != null && ("" + v).length && !_isNumericStr(v)) { bf = strM[s]; break; } } catch (e) {}
                out.push({ mX_: gn, KRt: listM[l], bf: bf, nI_: sh.nI_, uEK: sh.uEK, xzyOpts: sh.xzyOpts, ngqOpts: sh.ngqOpts });
            }
        }
        c = c.getSuperclass();
    }
    return out;
}
// Bounded recursive walk using a candidate name-set N; pushes scalar values (as strings)
// into `sink`. Mirrors _walkObj's logic: relationship (uEK non-null) -> recurse; else read Xzy.
function _walkWith(uo1, N, xzy, depth, maxDepth, budget, sink, seen) {
    if (budget.n <= 0 || uo1 == null || depth > maxDepth) return;
    if (_IHC == null) _IHC = Java.type("java.lang.System");
    var id; try { id = _IHC.identityHashCode(uo1); } catch (e) { id = 0; }
    if (seen[id]) return; seen[id] = 1;                 // dedup: the document graph has cycles
    var cwo; try { cwo = _invokeNoArg(uo1, N.mX_); } catch (e) { return; }
    if (cwo == null) return;
    var lst; try { lst = _invokeNoArg(cwo, N.KRt); } catch (e) { return; }
    if (lst == null) return;
    var sz; try { sz = lst.size(); } catch (e) { return; }
    for (var i = 0; i < sz && budget.n > 0; i++) {
        var d; try { d = lst.get(i); } catch (e) { continue; }
        budget.n--;
        var kids = null;                                 // relationship children: uEK(uo1, String)
        try { var km = _findMethod(_classOf(d), N.uEK, 2, "String"); if (km) kids = km.invoke(d, uo1, null); } catch (e) { kids = null; }
        if (kids != null) {
            var nk; try { nk = kids.size(); } catch (e) { nk = 0; }
            for (var k = 0; k < nk && budget.n > 0; k++) {
                var ch; try { ch = kids.get(k); } catch (e) { continue; }
                _walkWith(ch, N, xzy, depth + 1, maxDepth, budget, sink, seen);
            }
        } else {
            try { var v = _inv1(d, xzy, uo1); if (v != null) sink.push("" + v); } catch (e) {}
        }
    }
}
// The prop-id getter (ngq): of the numeric no-arg candidates, the one whose values across
// the container's descriptors are the most DISTINCT (each property carries a unique id).
function _selectNgq(cwo, KRt, ngqOpts) {
    if (!ngqOpts || !ngqOpts.length) return null;
    var lst; try { lst = _invokeNoArg(cwo, KRt); } catch (e) { return ngqOpts[0]; }
    if (lst == null) return ngqOpts[0];
    var sz; try { sz = lst.size(); } catch (e) { return ngqOpts[0]; }
    var best = ngqOpts[0], bestDistinct = -1;
    for (var o = 0; o < ngqOpts.length; o++) {
        var vals = {}, distinct = 0;
        for (var i = 0; i < sz; i++) {
            var d; try { d = lst.get(i); } catch (e) { continue; }
            try { var v = _invokeNoArg(d, ngqOpts[o]); if (v != null && !vals["" + v]) { vals["" + v] = 1; distinct++; } } catch (e) {}
        }
        if (distinct > bestDistinct) { bestDistinct = distinct; best = ngqOpts[o]; }
    }
    return best;
}
// Resolve the reader. Without `forceStructural`: TRUST the SYM mapping (loaded from the
// validated cache or the shipped data file) - it is the canonical reader on a supported build,
// and the caller validates it via the descriptor-read sentinel check (re-resolving structurally
// only if that fails). With forceStructural (or blind mode): pure structural discovery, picking
// the candidate whose walk surfaces the most sentinels. Returns the reader name-set or null.
function _discoverReader(uo1, sentinels, forceStructural) {
    if (!forceStructural && !gBlindDiscovery && SYM.mX_ && SYM.KRt && SYM.uEK && SYM.Xzy) {
        return { mX_: SYM.mX_, KRt: SYM.KRt, bf: SYM.bf, ngq: SYM.ngq, nI_: SYM.nI_, Xzy: SYM.Xzy, uEK: SYM.uEK };
    }
    // structural (blind, no mapping, or the trusted mapping failed validation): among the
    // candidates whose walk surfaces a sentinel, pick the RICHEST (most scalars). The richest
    // walk is the most COMPLETE reader (reaches notes + automation), matching the canonical
    // reader instead of a partial alias.
    var cands = _readerCandidates(uo1), _bestN = null, _bestXzy = null, _bestScalars = -1, _bestHits = -1;
    for (var ci = 0; ci < cands.length; ci++) {
        var N = cands[ci];
        for (var xi = 0; xi < N.xzyOpts.length; xi++) {
            var xzy = N.xzyOpts[xi], sink = [], budget = { n: 9000 };
            try { _walkWith(uo1, N, xzy, 0, 16, budget, sink, {}); } catch (e) { continue; }
            var blob = sink.join(""), nhit = 0;
            for (var s = 0; s < sentinels.length; s++) if (blob.indexOf(sentinels[s]) >= 0) nhit++;
            if (nhit > 0 && (nhit > _bestHits || (nhit === _bestHits && sink.length > _bestScalars))) {
                _bestHits = nhit; _bestScalars = sink.length; _bestN = N; _bestXzy = xzy;
            }
        }
    }
    if (_bestN == null) return null;
    var cwoN; try { cwoN = _invokeNoArg(uo1, _bestN.mX_); } catch (e) { cwoN = null; }
    var ngq = cwoN ? _selectNgq(cwoN, _bestN.KRt, _bestN.ngqOpts) : (_bestN.ngqOpts[0] || null);
    return { mX_: _bestN.mX_, KRt: _bestN.KRt, bf: _bestN.bf, ngq: ngq, nI_: _bestN.nI_, Xzy: _bestXzy, uEK: _bestN.uEK };
}

// ── symbol cache: doctor resolves + validates the reader names, persists them keyed by a
// build fingerprint; the bridge loads them at init. The reader is the one path that cannot
// self-validate during a plain read (no sentinel to check against), so it relies on the
// cache; automation/clip resolve + validate live on every write.
var _CACHE_SCHEMA = 3;   // bump invalidates old caches (added clipCmd/noteCmd)
function _fingerprint() {
    var parts = [];
    try { parts.push("v=" + host.getHostVersion()); } catch (e) {}
    try {
        var loc = host.getClass().getProtectionDomain().getCodeSource().getLocation();
        var f = new (Java.type("java.io.File"))(loc.toURI());
        parts.push("len=" + f.length()); parts.push("mt=" + f.lastModified());
    } catch (e) { parts.push("nojar"); }
    parts.push("schema=" + _CACHE_SCHEMA);
    return parts.join("|");
}
function _cachePath() { return ("" + LOG_FILE).replace(/openwig_bridge\.log$/, "symbols_cache.json"); }
function _writeCache(obj) {
    try {
        var BW = Java.type("java.io.BufferedWriter"), FW = Java.type("java.io.FileWriter");
        var w = new BW(new FW(_cachePath(), false));
        w.write(JSON.stringify(obj)); w.newLine(); w.flush(); w.close();
        return true;
    } catch (e) { flog("symbol cache write failed: " + e); return false; }
}
function _readCache() { return _readJson(_cachePath()); }
// Apply a validated reader name-set into SYM (called from a mapping and from the probe).
function _applyReaderNames(rd) {
    if (!rd) return;
    if (rd.mX_) SYM.mX_ = rd.mX_; if (rd.KRt) SYM.KRt = rd.KRt; if (rd.bf) SYM.bf = rd.bf;
    if (rd.ngq) SYM.ngq = rd.ngq; if (rd.nI_) SYM.nI_ = rd.nI_; if (rd.Xzy) SYM.Xzy = rd.Xzy;
    if (rd.uEK) SYM.uEK = rd.uEK;
}
// Apply a command spec {cls,field,factory,exec} into SYM (preserving the opid).
function _applyCommandSpec(target, spec) {
    if (!spec || !spec.cls) return;
    target.cls = spec.cls; target.field = spec.field; target.factory = spec.factory; target.exec = spec.exec;
}
// Merge a symbol mapping (from the bootstrap data file OR the per-build cache) into SYM.
function _applyMapping(d) {
    if (!d) return;
    _applyReaderNames(d.reader);
    if (d.fj) SYM.fj = d.fj;
    if (d.SZo) SYM.SZo = d.SZo;
    if (d.autoLanes) SYM.autoLanes = d.autoLanes;
    if (d.szFilter) SYM.szFilter = d.szFilter;
    if (d.clipCmd) _applyCommandSpec(SYM.clipCmd, d.clipCmd), (SYM.clipCmd.opid = d.clipCmd.opid);
    if (d.noteCmd) _applyCommandSpec(SYM.noteCmd, d.noteCmd), (SYM.noteCmd.opid = d.noteCmd.opid);
    if (d.audio && d.audio.hrv) SYM.audio = d.audio;
}
function _readJson(path) {
    try {
        var F = Java.type("java.io.File"), f = new F(path);
        if (!f.exists()) return null;
        var bytes = Java.type("java.nio.file.Files").readAllBytes(f.toPath());
        return JSON.parse("" + new JString(bytes, Charset.UTF_8));
    } catch (e) { flog("read json failed (" + path + "): " + e); return null; }
}
function _defaultsPath() { return ("" + LOG_FILE).replace(/openwig_bridge\.log$/, "symbols_default.json"); }
// init-time: load the bootstrap DATA mapping, then override with doctor's per-build cache
// (only when its fingerprint matches THIS build). No obfuscated names live in code.
function _loadSymbols() {
    var hadDefaults = false;
    var d = _readJson(_defaultsPath());
    if (d) { _applyMapping(d); hadDefaults = true; }
    var c = _readCache();
    if (c && c.fingerprint === _fingerprint() && c.reader) {
        _applyMapping(c);
        gSymSource = "cache";
    } else if (hadDefaults) {
        gSymSource = c ? "defaults (cache stale; re-run doctor)" : "defaults (run doctor to validate + cache)";
    } else {
        gSymSource = "UNRESOLVED (run openwig install + doctor)";
    }
}

// ── resolver / self-test (run by `openwig doctor`) ──────────────────────────────
// The reflection paths above hardcode Bitwig's obfuscated class/method names. Those names
// are stable for a given Bitwig build but are re-obfuscated each release, so they can move
// from one version to the next. This self-test VERIFIES, on a throwaway track, that the
// paths still work on the LIVE build: it round-trips an arranger automation write, an
// arranger clip+note, and a descriptor read, checking that each landed (distinctive
// sentinel values must reappear in the descriptor walk). It fails SAFE - a broken path is
// reported, never silently corrupting a project - and reports which obfuscated classes
// still load, so an unsupported build yields an actionable report instead of a crash.

function _hostInfo() {
    var out = {};
    try { out.product = "" + host.getHostProduct(); } catch (e) {}
    try { out.vendor  = "" + host.getHostVendor();  } catch (e) {}
    try { out.version = "" + host.getHostVersion(); } catch (e) {}
    try { out.api_version = host.getHostApiVersion(); } catch (e) {}
    return out;
}

// Verify the obfuscated classes the bridge actually depends on still load - derived from the
// RESOLVED symbols (SYM + the discovered automation cluster), not a hardcoded list, so this
// reflects whatever was discovered/cached for this build. A renamed-and-unresolved class
// shows up here as failing to load.
function _resolverClasses() {
    var out = {}, names = [SYM.fj, SYM.SZo];
    if (SYM.clipCmd && SYM.clipCmd.cls) names.push(SYM.clipCmd.cls);
    if (SYM.noteCmd && SYM.noteCmd.cls) names.push(SYM.noteCmd.cls);
    if (_AUTO_SYM) { try { names.push("" + _AUTO_SYM.avr.getName()); } catch (e) {}
                     try { names.push("" + _AUTO_SYM.interp.getName()); } catch (e) {} }
    for (var i = 0; i < names.length; i++) {
        var nm = names[i]; if (nm == null || out[nm] !== undefined) continue;
        try { Java.type(nm); out[nm] = true; } catch (e) { out[nm] = false; }
    }
    return out;
}

// sentinels: distinctive constants we write, then look for in the descriptor-walk JSON.
// NB: automation TIMES (beat positions) and the note START are stored as TIMES we can search
// for; an automation VALUE and a note velocity are stored normalized and are NOT searchable -
// hence we verify by the distinctive TIME. Bitwig stores these times as 32-bit floats, so the
// double we write is widened back from float32 (e.g. 1.6180339 -> 1.6180343627929688). We
// therefore search for the float32 form (Math.fround), trimmed to a stable leading prefix, so
// the match survives that rounding instead of depending on the exact decimal we sent.
var _SENT_AUTO_T = 1.4142135, _SENT_AUTO_T2 = 2.2360679;        // sqrt2, sqrt5 (beat positions)
var _SENT_NOTE_START = 1.6180339, _SENT_NOTE_VEL = 0.6789, _SENT_NOTE_KEY = 61;  // golden ratio
// float32-safe searchable form: nearest-float32 string, first 7 chars ("N.NNNNN") - distinctive
// (irrational constants) yet robust to the last-digit rounding the float storage introduces.
function _sentStr(x) { return ("" + Math.fround(x)).slice(0, 7); }
var _SENT_AUTO_S = _sentStr(_SENT_AUTO_T), _SENT_AUTO_S2 = _sentStr(_SENT_AUTO_T2);
var _SENT_NOTE_S = _sentStr(_SENT_NOTE_START);

function _walkTrackJSON(byU) {
    var budget = { n: 9000 };
    var opts = { prune: {}, noFilter: false, seen: {}, noDedup: {} };
    return JSON.stringify(_walkObj(byU, 0, 16, budget, opts));
}

// Runs on the document-edit thread (inside resolver.probe's exec). Mutates ONLY the
// currently-selected track, which the caller has created as a throwaway and deletes after.
function _runResolverProbe() {
    var report = {
        bitwig: _hostInfo(),
        classes: _resolverClasses(),
        capabilities: {
            automation_write: { ok: false, detail: "" },
            clip_create:      { ok: false, detail: "" },
            descriptor_read:  { ok: false, detail: "" },
            serialize:        { ok: false, detail: "" },
            normalize:        { ok: false, detail: "" }
        },
        ok: false
    };
    var byU = cursorTrack.getDeepestTarget();
    if (byU == null) { report.error = "no track target (probe track not selected)"; return report; }

    // 1. arranger automation write (self-discovered, name-free).
    try {
        var r = _insertAutomationPoints(byU, function () { return cursorTrack.volume(); }, "volume",
            [[_SENT_AUTO_T, 0.3], [_SENT_AUTO_T2, 0.7]]);
        report.capabilities.automation_write.detail = "inserted " + r.inserted + " (" + r.via + ")";
        report.capabilities.automation_write.via = r.via;
    } catch (e) { report.capabilities.automation_write.detail = "insert failed: " + e; }

    // 1.5 resolve the clip-create + note-insert command hosts by stable op-id (jar scan,
    //     doctor-only, self-contained: reads each command's numeric op-id directly, no reader
    //     dependency). Skipped in blind mode. On success SYM.clipCmd/noteCmd are resolved.
    if (!gBlindDiscovery) {
        try {
            var got = _findCommandsByOpIds([SYM.clipCmd.opid, SYM.noteCmd.opid], null);
            var gc = got["" + SYM.clipCmd.opid], gn = got["" + SYM.noteCmd.opid];
            if (gc) _applyCommandSpec(SYM.clipCmd, gc);
            if (gn) _applyCommandSpec(SYM.noteCmd, gn);
            report.commands = { clipCmd: SYM.clipCmd, noteCmd: SYM.noteCmd, resolved: !!(gc && gn),
                                instantiated: _gCmdScanInstantiated };
        } catch (e) { report.commands_err = "" + e; }
    }

    // 2. arranger clip + one note (via the resolved command hosts).
    try {
        var c = _insertClip(byU, 0.0, 4.0, [[0, _SENT_NOTE_KEY, _SENT_NOTE_START, 0.5, _SENT_NOTE_VEL]]);
        report.capabilities.clip_create.detail = "created clip, " + c.notes + " note(s)";
    } catch (e) { report.capabilities.clip_create.detail = "create failed: " + e; }

    // 2.5 reader: trust the SYM mapping (validated cache / shipped data); the descriptor read
    //     below validates it against the automation + note sentinels just written.
    var SENT = [_SENT_AUTO_S, _SENT_AUTO_S2, _SENT_NOTE_S];
    var rd = null;
    try { rd = _discoverReader(byU, SENT, false); } catch (e) { report.reader_err = "" + e; }
    if (rd) { _applyReaderNames(rd); report.reader = rd; }

    // 3. descriptor read-back + sentinel verification. If the trusted reader does NOT surface
    //    both sentinels (a build where the mapping moved), re-resolve the reader STRUCTURALLY
    //    and walk again. autoFound = automation point; noteFound = clip note.
    var json = null, autoFound = false, noteFound = false;
    var walkCheck = function () {
        try { json = _walkTrackJSON(byU); } catch (e) { json = null; report.capabilities.descriptor_read.detail = "walk failed: " + e; return; }
        autoFound = json.indexOf(_SENT_AUTO_S) >= 0 || json.indexOf(_SENT_AUTO_S2) >= 0;
        noteFound = json.indexOf(_SENT_NOTE_S) >= 0;
    };
    walkCheck();
    if (json != null && !(autoFound && noteFound) && !gBlindDiscovery) {
        var rd2 = null; try { rd2 = _discoverReader(byU, SENT, true); } catch (e) {}
        if (rd2) { _applyReaderNames(rd2); report.reader = rd2; walkCheck(); }
    }
    if (json != null) {
        report.capabilities.descriptor_read.ok = (json.length > 2);
        report.capabilities.descriptor_read.detail = "walk " + json.length + " chars";
        if (report.capabilities.automation_write.detail.indexOf("inserted") === 0)
            report.capabilities.automation_write.ok = autoFound;
        report.capabilities.automation_write.detail += autoFound ? " | verified" : " | sentinel NOT found";
        if (report.capabilities.clip_create.detail.indexOf("created") === 0)
            report.capabilities.clip_create.ok = noteFound;
        report.capabilities.clip_create.detail += noteFound ? " | verified" : " | sentinel NOT found";
    }

    // 4. serialize filter: the SZo filter the descriptor reader uses to decide which properties
    //    are serialized. Confirm the SZo class loads and the filter (SYM.szFilter) classifies a
    //    real descriptor (returns a boolean for a true property, false for the dedup sentinel).
    try {
        Java.type(SYM.SZo);                          // class must load
        _SZFILT = null;                              // force re-resolve from SYM.szFilter
        var cwoS = _mx(byU), dl = _descriptors(cwoS);
        var classified = false;
        if (dl && dl.size() > 0) { _szPass(dl.get(0), byU); classified = (_SZFILT !== null && _SZFILT !== false); }
        report.capabilities.serialize.ok = classified;
        report.capabilities.serialize.detail = classified ? ("filter ok (" + SYM.szFilter.method + ")") : "filter not resolved";
    } catch (e) { report.capabilities.serialize.detail = "failed: " + e; }

    // 5. normalize: resolve a parameter's native->0..1 normalize fn (structural, behavioural).
    //    Underpins device.remote_normalize and exact automation/breakpoint value conversion.
    try {
        var vp = cursorTrack.volume();
        var vdt = vp.getDeepestTarget();
        var vfj = _fjFrom(_invokeNoArg(vdt, "getAtom")); if (vfj == null) vfj = _fjFrom(vdt);
        if (vfj == null) throw "no fj for volume";
        var curN = vp.value().getRaw(), curNorm = vp.value().get();
        var nf = _findNormalizeFn(vfj, curN, curNorm);
        report.capabilities.normalize.ok = (nf != null);
        report.capabilities.normalize.detail = nf ? ("resolved " + nf.getName() + "()") : "no normalize fn found";
    } catch (e) { report.capabilities.normalize.detail = "failed: " + e; }

    // structurally-discovered automation symbols (for transparency + crowdsourced maps):
    // these names should match the hardcoded ones on a supported build.
    if (_AUTO_SYM) {
        report.discovered = {
            al_accessor: "" + _AUTO_SYM.alAccessor,
            insert: "" + _AUTO_SYM.insert.getName(),
            value_ref: "" + _AUTO_SYM.avr.getName(),
            factory: "" + _AUTO_SYM.factory.getName(),
            value_base: "" + _AUTO_SYM.fjClass.getName(),
            interp_enum: "" + _AUTO_SYM.interp.getName(),
            interp_resolved: _AUTO_SYM.interpResolved
        };
    }

    var caps = report.capabilities;
    report.ok = caps.automation_write.ok && caps.clip_create.ok && caps.descriptor_read.ok &&
                caps.serialize.ok && caps.normalize.ok;

    // persist the validated reader names so the bridge can use them at read time (no cache in
    // blind/test mode; only when the reader actually validated via the descriptor read).
    if (!gBlindDiscovery && rd && caps.descriptor_read.ok) {
        var cacheObj = {
            schema: _CACHE_SCHEMA, fingerprint: _fingerprint(), bitwig: report.bitwig,
            reader: rd, SZo: SYM.SZo, clipCmd: SYM.clipCmd, noteCmd: SYM.noteCmd, audio: SYM.audio,
            verdicts: { automation: caps.automation_write.ok, clip: caps.clip_create.ok,
                        descriptor: caps.descriptor_read.ok, serialize: caps.serialize.ok, normalize: caps.normalize.ok }
        };
        var wrote = _writeCache(cacheObj);
        gSymSource = wrote ? "discovered+cached" : "discovered";
        report.cache = { written: wrote, path: _cachePath(), fingerprint: cacheObj.fingerprint };
    } else {
        report.cache = { written: false, reason: gBlindDiscovery ? "blind mode" : (rd ? "descriptor_read failed" : "reader not discovered") };
    }
    report.symbol_source = gSymSource;
    return report;
}

var HANDLERS = {

    // ── meta ──
    "ping": function () { return "pong"; },
    // ── resolver / self-test (driven by `openwig doctor`) ──
    // Cheap, synchronous: which obfuscated classes still load on this build (no doc thread).
    "resolver.classes": function () { return { bitwig: _hostInfo(), classes: _resolverClasses() }; },
    // arranger audio-clip insert. SYM.audio.hrv (data/cache/validated) names the track-as-HrV
    // accessor; p.hrv overrides it (used by doctor to validate candidates). dispatch/ZjS/mode
    // resolve structurally from the stable ArrangerClipInsertionPoint.
    "track.insert_audio_clip": function (p) {
        var trackIdx = bget(p, "track", 0) | 0;
        var path = "" + bget(p, "path", "");
        var start = Number(bget(p, "start", 0)), dur = Number(bget(p, "duration", 4));
        var hrvOverride = p.hrv ? ("" + p.hrv) : null;
        if (!path) return { error: "no path" };
        _runOnDocumentThread(cursorTrack, function () {
            var trackDoc = trackBank.getItemAt(trackIdx).getDeepestTarget();
            if (trackDoc == null) throw "track " + trackIdx + " has no document target";
            return _insertAudioClip(trackDoc, path, start, dur, hrvOverride);
        });
        return { queued: true, path: path, note: "async; outcome in openwig_bridge.log [auto]" };
    },
    // doctor: candidate track-as-HrV accessor names for the SELECTED track (+ resolved parts),
    // so the SDK can validate each by inserting a test wav. Select a track first.
    "resolver.audio_candidates": function () {
        var byU = cursorTrack.getDeepestTarget();
        if (byU == null) return { error: "select a track first" };
        var P; try { P = _acipParts(); } catch (e) { return { error: "" + e }; }
        var cands = [], c = _classOf(byU), seen = {};
        while (c != null) {
            var ms = c.getDeclaredMethods();
            for (var a = 0; a < ms.length; a++) {
                var am = ms[a]; if (am.getParameterCount() !== 0) continue;
                var an = "" + am.getName(); if (seen[an]) continue; seen[an] = 1;
                if (P.hrvType.isAssignableFrom(am.getReturnType())) cands.push(an);
            }
            c = c.getSuperclass();
        }
        return { hrv_candidates: cands, dispatch: "" + P.dispatch.getName(),
                 zjs: "" + P.dispatch.getParameterTypes()[1].getName() };
    },
    // doctor: record the validated audio HrV accessor + refresh the cache with it.
    "resolver.set_audio_hrv": function (p) {
        SYM.audio.hrv = "" + bget(p, "hrv", SYM.audio.hrv);
        var c = _readCache();
        if (c) { c.audio = SYM.audio; _writeCache(c); }
        return { ok: true, hrv: SYM.audio.hrv };
    },
    // Where SYM's reader names came from this session (cache / seed) + the build fingerprint.
    "resolver.status": function () {
        var c = _readCache();
        return { symbol_source: gSymSource, fingerprint: _fingerprint(),
                 cache_exists: (c != null), cache_matches: (c != null && c.fingerprint === _fingerprint()),
                 reader: { mX_: SYM.mX_, KRt: SYM.KRt, bf: SYM.bf, ngq: SYM.ngq, nI_: SYM.nI_, Xzy: SYM.Xzy, uEK: SYM.uEK } };
    },
    // Full round-trip probe on the document thread; fetch the report with resolver.result.
    // The caller MUST have created + selected a throwaway track first (this writes to it).
    // params: { blind: bool } - blind forces name-free structural discovery only (no name
    // hints, no hardcoded fallback), simulating a build where the obfuscated names changed.
    "resolver.probe": function (p) {
        gProbe = null; gProbeErr = null;
        var blind = !!bget(p, "blind", false);
        _runOnDocumentThread(cursorTrack, function () {
            var prevBlind = gBlindDiscovery;
            gBlindDiscovery = blind;
            if (blind) _AUTO_SYM = null;            // force a fresh structural-only resolution
            try { gProbe = _runResolverProbe(); gProbe.blind = blind; return { ok: gProbe.ok }; }
            catch (e) { gProbeErr = "" + e; return { error: gProbeErr }; }
            finally { gBlindDiscovery = prevBlind; if (blind) _AUTO_SYM = null; }   // don't leak the blind cache
        });
        return { queued: true, note: "fetch with resolver.result" };
    },
    "resolver.result": function () { return { report: gProbe, error: gProbeErr, ready: (gProbe != null || gProbeErr != null) }; },
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
            var cwo = _mx(gCursorClip);
            if (cwo == null) return { error: "cursor clip has no mX_ target" };
            // shallow walk: just enumerate this object's descriptors (no recursion)
            var descrs = _descriptors(cwo);
            var n = (descrs == null) ? 0 : descrs.size();
            var out = { _cls: (function () {
                try { return "" + _inv0(cwo, SYM.bf); } catch (e) { return "?"; }
            })(), props: [] };
            for (var i = 0; i < n; i++) {
                var d = descrs.get(i);
                var pid; try { pid = "" + _inv0(d, SYM.ngq); } catch (e) { pid = "i" + i; }
                var val = null;
                try { val = _jval(_inv1(d, SYM.Xzy, cwo)); } catch (e) { val = "<err>"; }
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
                var cwo = _mx(gCursorClip);
                if (cwo == null) throw "cursor clip has no mX_ target";
                var descrs = _descriptors(cwo);
                var n = (descrs == null) ? 0 : descrs.size();
                var d = null;
                for (var i = 0; i < n; i++) {
                    var dd = descrs.get(i);
                    if (("" + _inv0(dd, SYM.ngq)) === prop) { d = dd; break; }
                }
                if (d == null) throw "prop " + prop + " not on cursor clip";
                // Try the standard setter: uEK(uo1, value)
                var m = _findMethod(_classOf(d), SYM.uEK, 2, null);
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

    "tempo.write_offline": function (p) {
        var pts = p.points; if (!pts || !pts.length) return { error: "no points" };
        _runOnDocumentThread(cursorTrack, function () {
            // tempo's atom lives on the transport float_document. fj IS that document; its
            // automation_lanes accessor + the value-ref factory + insert are the SAME cluster
            // as track automation, so we reuse the structurally-discovered automation symbols
            // (no obfuscated names): fj is byU (its lanes accessor yields the transport lanes).
            var tt = transport.tempo();
            var pp = tt.value ? tt.value() : tt;
            var fj = _fjFrom(_invokeNoArg(pp.getDeepestTarget(), "getAtom"));
            if (fj == null) fj = _fjFrom(pp.getDeepestTarget());
            if (fj == null) throw "could not resolve fj for tempo";
            var n = _insertAutoDiscovered(fj, pp, pts);
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
