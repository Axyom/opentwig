#!/usr/bin/env python3
"""demo_modulators.py - real modulator INSERT + MAP, fully programmatic.

Adds an LFO modulator to a Polysynth and maps it to the Polysynth's first remote
parameter at depth 0.8. Plays a 4-bar held note so the modulation is audible.
"""
import time
from openwig import Song


def main():
    s = Song(tempo=120, bars=4, clean=True)
    lead = s.track("LEAD", device="Polysynth")
    print(f"Polysynth ships with {len(lead.list_modulators())} built-in modulation sources")

    # ADD a Beat LFO modulator - works via ModulatorGridInsertionPoint
    lead.add_modulator("Beat LFO")
    n = len(lead.list_modulators())
    print(f"After add_modulator('Beat LFO'): {n} sources")

    # The newly-inserted Beat LFO is now in the list. Find its index (last entry).
    sources = lead.list_modulators()
    new_idx = sources[-1]["index"]
    print(f"  newly-inserted Beat LFO source is at index {new_idx}")

    # Map the Beat LFO -> Polysynth's remote 0 (typically Volume / first knob)
    res = lead.map_modulator(new_idx, dest="remote", remote_index=0, amount=0.8)
    print(f"  map -> {res}")

    # Hold a long note across the whole song so the modulation is audible
    lead.clip([(60, 0, float(s.total), 0.85)])

    s.play(loop=True)
    print(f"playing {s.bars}-bar drone with Beat LFO -> remote 0 (depth 0.8)")
    s.close()


if __name__ == "__main__":
    main()
