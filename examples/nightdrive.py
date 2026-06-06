"""Nightdrive - a full demo song.

A 12-track progressive house / techno sketch: drum grids, a tiled bass cell,
repeated stab and lead phrases, device chains, and arranger automation. The
repetitive parts are written as generators (pulse / tile / roll) instead of
note-by-note, with stock factory devices so it runs anywhere.

Run it against a scratch project - it opens with clean=True, which clears the
currently-open Bitwig project:

    python examples/nightdrive.py
"""

from openwig import Song


def pulse(key, n, *, step=1.0, dur=0.24, vel=0.8, t0=0.0):
    """n notes of `key` on a regular grid (i*step), e.g. four-on-the-floor."""
    return [(key, round(t0 + i * step, 4), dur, vel) for i in range(n)]


def tile(cell, times, period, *, t0=0.0):
    """Repeat a (key, t, dur, vel) note `cell` `times`, each shifted `period` beats."""
    return [(k, round(t0 + r * period + t, 4), d, v)
            for r in range(times) for (k, t, d, v) in cell]


def roll(key, t0, *, n=8, step=0.25, dur=0.1, v0=0.4, v1=0.85):
    """A rising 16th-note fill of `n` hits - a snare build before a drop."""
    return [(key, round(t0 + i * step, 4), dur, round(v0 + (v1 - v0) * i / (n - 1), 4))
            for i in range(n)]


s = Song(tempo=128, bars=32, clean=True)

# KICK - four on the floor
t_00_kick = s.track('KICK', device='v9 Kick')
t_00_kick.fader(0.7937)
t_00_kick.fx('Saturator')
t_00_kick.select_device(0)
t_00_kick.set_remote_values(0, {1: 0.23})   # Decay
t_00_kick.clips([(16, 96, pulse(36, 96, vel=0.82))])

# HATS - straight eighths
t_01_hats = s.track('HATS', device='v9 Hat Closed')
t_01_hats.fader(0.7937)
t_01_hats.clips([(64, 64, pulse(42, 128, step=0.5, dur=0.12, vel=0.4))])

# v1 Hat - straight 16ths + a rising decay-envelope automation
t_02_v1_hat = s.track('v1 Hat', device='v1 Hat')
t_02_v1_hat.fader(0.7937)
t_02_v1_hat.fx('Tool')
t_02_v1_hat.select_device(0)
t_02_v1_hat.set_remote_values(0, {1: 0.4083})   # Decay
t_02_v1_hat.clips([(64, 64, pulse(60, 256, step=0.25, dur=0.25, vel=0.7874))])
# Decay - a rising envelope that resets every 16 beats (bars 20-32)
_hat_rise = [(0.0, 0.408), (0.19, 0.428), (0.70, 0.438), (1.01, 0.463), (1.44, 0.523),
             (1.83, 0.553), (1.97, 0.588), (2.40, 0.633), (2.75, 0.708), (3.08, 0.818), (3.08, 0.408)]
_hat_decay = [(32, 0.408)] + [(base + dt, v) for base in (76.78, 92.78, 108.78, 124.78)
                              for dt, v in _hat_rise]
t_02_v1_hat.automate('remote', _hat_decay, remote_index=1)

# CLAP - offbeat
t_03_clap = s.track('CLAP', device='v9 Clap')
t_03_clap.fader(0.7937)
t_03_clap.fx('Reverb')
t_03_clap.clips([(32, 96, pulse(39, 48, step=2, dur=0.2, vel=0.88, t0=1))])

# RIDE - quarters in the last 8 bars
t_04_v9_ride = s.track('v9 Ride', device='v9 Ride')
t_04_v9_ride.fader(0.7937)
t_04_v9_ride.clips([(96, 32, pulse(59, 32, dur=1.0, vel=0.7874))])

# CRASH - on each section start
t_05_v9_crash = s.track('v9 Crash', device='v9 Crash')
t_05_v9_crash.fader(0.7937)
t_05_v9_crash.fx('Reverb')
t_05_v9_crash.fx('Delay+')
t_05_v9_crash.fx('EQ+')
t_05_v9_crash.select_device(0)
t_05_v9_crash.set_remote_values(0, {0: 0.645, 1: 0.915, 2: 0.24, 3: 1, 4: 1})   # Tune, Decay, Impact, Density, Width
t_05_v9_crash.select_device(3)   # EQ+
t_05_v9_crash.set_remote_values(0, {0: 0.4152})   # 1 Gain
t_05_v9_crash.set_remote_values(1, {0: 0.8952})   # 1 Freq
for _b in (32, 64, 96):
    t_05_v9_crash.clips([(_b, 4, [(60, 0, 4, 0.7874)])])

# SHAKER - a 3-hit cell every bar
t_06_shaker = s.track('SHAKER', device='v9 Hat Closed')
t_06_shaker.fader(0.7937)
_shaker = [(70, 0.25, 0.1, 0.32), (70, 1.5, 0.1, 0.45), (70, 2.25, 0.1, 0.44)]
t_06_shaker.clips([(64, 64, tile(_shaker, 16, 4))])

# SNARE - rising 16th fills before each drop
t_07_snare = s.track('SNARE', device='v9 Snare')
t_07_snare.fader(0.7937)
t_07_snare.fx('Reverb')
t_07_snare.select_device(0)
t_07_snare.set_remote_values(0, {2: 0.665, 3: 0.795, 4: 0.7, 6: 0.985, 7: 0.8943})   # Snappy, Drive, Tone, Vel Sens., Output
t_07_snare.select_device(1)   # Reverb
t_07_snare.set_remote_values(0, {3: 0.135, 7: 0.335})   # R. Time, Mix
for _b in (32, 64, 96, 128):
    t_07_snare.clips([(_b - 2, 2, roll(38, 0))])

# BASS - syncopated root note
t_08_bass = s.track('BASS', device='Polysynth')
t_08_bass.fader(0.7937)
t_08_bass.fx('Filter')
t_08_bass.fx('Filter')
t_08_bass.select_device(0)
t_08_bass.set_remote_values(3, {0: 0.6, 5: 0.39, 7: 0.5})   # Filt Freq, FEG D, FEG Amount
t_08_bass.set_remote_values(4, {2: 0.5})   # FEG Amt
t_08_bass.select_device(1)   # Filter
t_08_bass.set_remote_values(0, {0: 0, 2: 0.5714})   # Freq, Mode
_bass = [(33, 0.25, 0.5, 0.7874), (33, 0.75, 0.25, 0.7874), (33, 1.0, 0.5, 0.7874), (33, 1.5, 0.5, 0.7874)]
t_08_bass.clips([(32, 96, tile(_bass, 48, 2))])
t_08_bass.select_device(1)   # Filter: Freq
t_08_bass.automate('remote', [(0, 0), (96, 0), (128, 1)], remote_index=0)

# STAB - a 4-note chord-stab motif every 16 beats
t_09_stab = s.track('STAB', device='Polysynth')
t_09_stab.fader(0.7937)
t_09_stab.fx('Delay+')
t_09_stab.select_device(0)
t_09_stab.set_remote_values(5, {7: 0.38})   # AEG R
_stab = [(57, 0.5, 0.4, 0.45), (53, 4.5, 0.4, 0.42), (48, 8.5, 0.4, 0.5), (55, 12.5, 0.4, 0.5)]
t_09_stab.clips([(32, 96, tile(_stab, 6, 16))])
t_09_stab.select_device(0)   # AEG R
t_09_stab.automate('remote', [(96.7, 0.38), (124.3, 0.88), (128, 0.497), (392, 0.38)], remote_index=7, page=5)

# PAD - a held chord with a filter sweep
t_10_pad = s.track('PAD', device='Polysynth')
t_10_pad.fader(0.7937)
t_10_pad.pan(-0.5196)
t_10_pad.fx('Reverb')
t_10_pad.fx('Filter')
t_10_pad.fx('Chorus+')
t_10_pad.fx('Delay+')
t_10_pad.select_device(0)
t_10_pad.set_remote_values(0, {2: 0.2305})   # Uni1
t_10_pad.set_remote_values(3, {0: 0.877})   # Filt Freq
t_10_pad.select_device(2)   # Filter
t_10_pad.set_remote_values(0, {2: 0.5714})   # Mode
t_10_pad.clips([(0, 32, [(45, 0, 64, 0.3), (52, 0, 64, 0.3), (57, 0, 64, 0.3)])])
t_10_pad.select_device(2)   # Filter: Freq
t_10_pad.automate('remote', [(0.28, 1.0), (13.3, 0.0), (15.17, 0.0)], remote_index=0)
t_10_pad.select_device(0)   # Filt Freq
t_10_pad.automate('remote', [(0.77, 0.877), (8.5, 0.52), (23.7, 0.89), (36.7, 0.0), (44.5, 0.0)], remote_index=0, page=3)

# LEAD - a 16-beat phrase, repeated
t_11_lead = s.track('LEAD', device='Polysynth')
t_11_lead.fader(0.7937)
t_11_lead.arm(True)
t_11_lead.fx('Reverb')
t_11_lead.fx('Delay+')
t_11_lead.select_device(0)
t_11_lead.set_remote_values(3, {0: 0.5477})   # Filt Freq
_lead = [(69, 0, 1.5, 0.59), (81, 2.5, 0.5, 0.35), (76, 3, 0.5, 0.38), (72, 4, 1.5, 0.65),
         (82, 5.75, 0.25, 0.35), (84, 6.5, 0.5, 0.39), (79, 7, 0.5, 0.38), (76, 8, 1.5, 0.65),
         (88, 10.5, 0.5, 0.34), (83, 11, 0.5, 0.36), (74, 12, 1.5, 0.52), (84, 13.75, 0.25, 0.29),
         (86, 14.5, 0.5, 0.4), (81, 15, 0.5, 0.4)]
t_11_lead.clips([(64, 64, tile(_lead, 4, 16))])
t_11_lead.select_device(1)   # Reverb: Mix
t_11_lead.automate('remote', [(0, 0.405), (100, 0.405), (126, 0.8693), (126, 0.905)], remote_index=7)
t_11_lead.select_device(0)   # Filt Freq
t_11_lead.automate('remote', [(64, 0.5477), (68, 0.5477), (96, 0.6529), (100, 0.5477), (126, 0.6454), (126, 0.6529)], remote_index=0, page=3)
t_11_lead.select_device(1)   # Reverb: R. Time
t_11_lead.automate('remote', [(0, 0.49), (64, 0.49), (80, 0.49), (96, 0.8037), (112, 0.49), (126, 0.8), (126, 0.8443)], remote_index=3)

s.master([
    'Distortion',
    'Saturator',
    'Over',
])

# s.play(loop=True)        # uncomment to hear it
# print(s.render('out.wav'))
