#!/usr/bin/env python3
"""Commando (Hubbard, 1985) as a trackerprog, transliterated by hand.

The oracle reference tune for prototype-trackerprog.md: not a lift, a *reading*.
Every structure below is docs/prototype-commando-floor.md section 4's factored
form -- the certified tuneprog's own text -- restated in the trackerprog's
vocabulary, one layer up:

===========================  ==================================================
the tuneprog says            the trackerprog says
===========================  ==================================================
``FREQ[n]`` (u16 at          ``pitch[n]``; where the read leaves the const table
``$5428 + 2n``)              the entry names two state cells (section 6)
``INS[i]`` 8 columns         ``Ins{adsr, wave, pw, prelude, accs}``
``TRACK[v]``, ``PAT[p]``     ``score.orders``, ``score.patterns`` of events
``speedctr``/``speed``       ``meta.tempo`` -- a divider, ``rate = speed + 1``
``row & $1F``                the event's ``dur`` in row ticks
``row & $20``                the event's ``tie``: it disarms the prelude
``row & $40``                the event's ``gate: off`` -- a keyoff
``ins.vib``                  ``Acc(freq, repeat(tablestep(pitch, note, vib+1),
                             phase(counter)), policy reload)``
``ins.fx & 8``               ``Acc(pw_lo, const(pspeed) + carry(vibrato))``
``ins.pspeed``               ``Acc(pw, const(pspeed & $E0), policy reflect,
                             bound [$800,$EFF], rate (pspeed & $1F) + 1)``
``voice.porta``              ``Acc(freq, field(porta, $7E), phase bit(porta,0))``
``ins.fx & 1``               ``Acc(freq_hi, const(-1), emit entry)`` + gate rows
``ins.fx & 4``               ``Acc(freq, policy reload(pitch[note + [0,12][c]]))``
``ins.fx & 2``               the arm the horizon never takes: ``trap``
``$518B`` hard cut           the instrument's prelude, ``early = 1`` row tick
===========================  ==================================================

Usage::

    tools/trackerprog_commando.py Commando.sid --song 0 --out out/tp
"""

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.lifter import lift  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.universal import render  # noqa: E402
from deity_informant.vm import PcodeVM, run_sub  # noqa: E402

FREQ_ORIGIN = 0x5428  # pitch index n is the u16 at FREQ_ORIGIN + 2n
INS_BASE, PATPTR_LO, PATPTR_HI = 0x5591, 0x5711, 0x573E
SONGPTR, SPEEDTBL = 0x56FF, 0x5514

# The cells the pitch table's tail overlaps (commando-floor section 5, "const is
# refuted by the tune"): each is state the universal player already holds.
CELLS = {
    0x54EB: "voice_base",
    0x54EC: ("orderpos", 0),
    0x54ED: ("orderpos", 1),
    0x54EE: ("orderpos", 2),
    0x54EF: ("patrow", 0),
    0x54F0: ("patrow", 1),
    0x54F1: ("patrow", 2),
    0x54F2: ("rowsleft", 0),
    0x54F3: ("rowsleft", 1),
    0x54F4: ("rowsleft", 2),
    0x54F5: ("dur", 0),
    0x54F6: ("dur", 1),
    0x54F7: ("dur", 2),
    0x54F8: ("wave", 0),
    0x54F9: ("wave", 1),
    0x54FA: ("wave", 2),
    0x54FB: ("note", 0),
    0x54FC: ("note", 1),
    0x54FD: ("note", 2),
    0x54FE: ("ins", 0),
    0x54FF: ("ins", 1),
    0x5500: ("ins", 2),
    0x5510: ("pwdir", 0),
    0x5511: ("pwdir", 1),
    0x5512: ("pwdir", 2),
}


def load(path):
    """The tune's load band, PSID header stripped."""
    d = Path(path).read_bytes()
    off, org = struct.unpack(">H", d[6:8])[0], struct.unpack(">H", d[8:10])[0]
    body = d[off:]
    if org == 0:
        org, body = body[0] | body[1] << 8, body[2:]
    m = bytearray(0x10000)
    m[org : org + len(body)] = body
    return m


def pattern(m, pn):
    """One pattern as events -- what the fetch grammar decodes, materialised."""
    base = m[PATPTR_LO + pn] | m[PATPTR_HI + pn] << 8
    out, i = [], 0
    while m[base + i] != 0xFF:
        start, r = i, m[base + i]
        e = {
            "dur": r & 0x1F,
            "tie": bool(r & 0x20),
            "gate": "off" if r & 0x40 else "on",
            "note": None,
            "ins": None,
            "porta": None,
        }
        i += 1
        if not r & 0x40:
            if r & 0x80:
                x = m[base + i]
                i += 1
                e["ins" if x < 0x80 else "porta"] = x
            e["note"] = m[base + i]
            i += 1
        e["bytes"] = i - start
        out.append(e)
    return out


def score(m, song):
    """The three order programs and the patterns they reach."""
    orders, used = [], set()
    for k in range(3):
        p = m[SONGPTR + 6 * song + k] | m[SONGPTR + 6 * song + 3 + k] << 8
        seq = []
        while m[p] not in (0xFF, 0xFE):
            seq.append(m[p])
            used.add(m[p])
            p += 1
        orders.append({"play": seq, "end": "jump" if m[p] == 0xFF else "stop"})
    return orders, {str(p): pattern(m, p) for p in sorted(used)}


def reached(m, orders, patterns):
    """The (instrument, note) pairs the horizon plays."""
    out = set()
    for v, o in enumerate(orders):
        cur = m[0x54FE + v]
        for p in o["play"]:
            for e in patterns[str(p)]:
                cur = e["ins"] if e["ins"] is not None else cur
                if e["note"] is not None:
                    out.add((cur, e["note"]))
    return out


def pitch(m, pairs):
    """The tuning, materialised: a const where the table is const, else two cells."""
    need = set()
    for i, n in pairs:
        col = m[INS_BASE + 8 * i : INS_BASE + 8 * i + 8]
        need |= {n, n + 1} if col[5] else {n}
        need |= {n + 12} if col[7] & 4 else set()
    out = {}
    for n in sorted(need):
        a = FREQ_ORIGIN + 2 * n
        if a in CELLS or a + 1 in CELLS:
            out[str(n)] = {"cells": [_ref(m, a), _ref(m, a + 1)]}
        else:
            out[str(n)] = {"const": m[a] | m[a + 1] << 8}
    return out


def _ref(m, a):
    c = CELLS.get(a)
    if c is None:
        return {"const": m[a]}
    return {"cell": c} if isinstance(c, str) else {"cell": c[0], "voice": c[1]}


def accs():
    """Section 5's records.  Seven forms; six are section 5's own rows."""
    return {
        "vibrato": {
            "id": "vibrato",
            "rank": 0,
            "cell": "tick",
            "target": "freq",
            "width": 16,
            "policy": {"reload": {"pitch": {"cell": "note"}}},
            "delta": {
                "repeat": [
                    {"tablestep": [{"cell": "note"}, "shift"]},
                    {"fold": [{"cell": "counter"}, 7]},
                ]
            },
            "delta_when": [[{"cell": "dur"}, ">=", 6]],
            "flag": {"name": "C", "seed": 1, "unguarded": 0},
            "bound": {
                "from": "proved",
                "interval": [0, 3],
                "witness": "the fold's own range, 0..3 semitone steps",
            },
            "rate": 1,
            "scope": "voice",
            "phase": {"fold": [{"cell": "counter"}, 7]},
            "produce": [["freq_lo", "lo"], ["freq_hi", "hi"]],
        },
        "pulse_run": {
            "id": "pulse_run",
            "rank": 1,
            "cell": "ins.pw.lo",
            "target": "pw",
            "width": 8,
            "delta": {"add": [{"const": "delta"}, {"flag": "C"}]},
            "policy": "wrap",
            "bound": {
                "from": "projected",
                "interval": [0, 0xFF],
                "witness": "the store is 8-bit; the chip sees 12",
            },
            "rate": 1,
            "scope": "instrument",
            "produce": [["pw_lo", "byte"]],
        },
        "pulse_bounce": {
            "id": "pulse_bounce",
            "rank": 1,
            "cell": "ins.pw",
            "target": "pw",
            "width": 12,
            "delta": {"const": "delta"},
            "policy": "reflect",
            "bound": {
                "from": "projected",
                "interval": [0x800, 0xEFF],
                "shift": 8,
                "witness": "pw_hi == $E going up, == $8 going down",
            },
            "rate": "rate",
            "phase": {"cell": "pwdir"},
            "scope": "instrument",
            "produce": [["pw_hi", "hi"], ["pw_lo", "lo"]],
        },
        "slide": {
            "id": "slide",
            "rank": 2,
            "cell": "voice.freq",
            "target": "freq",
            "width": 16,
            "delta": {"field": [{"cell": "porta"}, 0x7E]},
            "phase": {"bit": [{"cell": "porta"}, 0]},
            "policy": "wrap",
            "bound": {
                "from": "projected",
                "interval": [0, 0xFFFF],
                "witness": "the 16-bit store; a free slide, no target",
            },
            "rate": 1,
            "scope": "voice",
            "armed_by": "score",
            "produce": [["freq_lo", "lo"], ["freq_hi", "hi"]],
        },
        "drum": {
            "id": "drum",
            "rank": 3,
            "cell": "voice.freq.hi",
            "target": "freq",
            "width": 8,
            "delta": {"const": -1},
            "policy": "wrap",
            "emit": "entry",
            "bound": {"from": "proved", "interval": [1, 0xFF], "witness": "the guard freq_hi != 0"},
            "when": [[{"cell": "freq_hi"}, "!=", 0], [{"cell": "rowsleft"}, "!=", 0]],
            "step_when": [
                [{"and": [{"sub": [{"cell": "dur"}, 1]}, 0xFF]}, ">=", {"cell": "rowsleft"}]
            ],
            "rate": 1,
            "scope": "voice",
            "produce": [["freq_hi", "byte"]],
            "gate": {
                "false": [["ctrl", {"const": 0x80}]],
                "true": [["ctrl", {"and": [{"cell": "wave"}, 0xFE]}]],
            },
        },
        "skydive": {
            "id": "skydive",
            "rank": 4,
            "cell": "voice.freq.hi",
            "target": "freq",
            "width": 8,
            "delta": {"const": 2},
            "policy": "wrap",
            "emit": "entry",
            "rate": 1,
            "scope": "voice",
            "produce": [["freq_hi", "byte"]],
            "when": [[{"cell": "dur"}, ">=", 3]],
            "trap": True,
            "note": "dead in this family: fx & 2 and dur >= 3 never hold together",
        },
        "arpeggio": {
            "id": "arpeggio",
            "rank": 5,
            "cell": "tick",
            "target": "freq",
            "width": 16,
            "policy": {
                "reload": {
                    "pitch": {
                        "add": [
                            {"cell": "note"},
                            {"stream": ["arp", {"and": [{"cell": "counter"}, 1]}]},
                        ]
                    }
                }
            },
            "bound": {"from": "proved", "interval": [0, 12], "witness": "the arp stream"},
            "rate": 1,
            "scope": "voice",
            "phase": {"and": [{"cell": "counter"}, 1]},
            "produce": [["freq_hi", "hi"], ["freq_lo", "lo"]],
        },
    }


def build(path, song=0):
    """The trackerprog object for one Commando subtune."""
    m = load(path)
    orders, patterns = score(m, song)
    pairs = reached(m, orders, patterns)
    instruments = {}
    for i in sorted({i for i, _ in pairs}):
        pw_lo, pw_hi, wave, ad, sr, vib, pspeed, fx = m[INS_BASE + 8 * i : INS_BASE + 8 * i + 8]
        arms = []
        if vib:
            arms.append({"acc": "vibrato", "shift": vib + 1})
        if fx & 8:
            arms.append({"acc": "pulse_run", "delta": pspeed})
        elif pspeed:
            arms.append(
                {"acc": "pulse_bounce", "delta": pspeed & 0xE0, "rate": (pspeed & 0x1F) + 1}
            )
        if fx & 1:
            arms.append({"acc": "drum"})
        if fx & 2:
            arms.append({"acc": "skydive"})
        if fx & 4:
            arms.append({"acc": "arpeggio"})
        instruments[str(i)] = {
            "adsr": [ad, sr],
            "wave": wave,
            "pw": [pw_lo, pw_hi],
            "prelude": {"stream": "note_off", "early": 1},
            "accs": arms,
        }
    return {
        "$trackerprog": 1,
        "meta": {
            "tune": Path(path).name,
            "family": "Hubbard",
            "song": song,
            "cycles_per_tick": 19656,
            "voices": 3,
            "voice_order": [2, 1, 0],
            "commit_order": ["ctrl", "ad", "sr"],
            "tempo": {"rate": m[SPEEDTBL + song] + 1, "phase": 0},
            "row_consumes_tick": True,
            "note_row": "note_on",
            "score_acc": "slide",
            "player": "prototype-trackerprog.md sections 4 and 5",
        },
        "globals": {
            "mode_vol": 0x0F,
            "flags": {"C": {"default": {"bit": [{"cell": "ins"}, 5]}}},
            "init_writes": [[4, 0], [11, 0], [4, 0], [11, 0], [18, 0], [24, 0x0F]],
            "stop_writes": [[4, 0], [11, 0], [18, 0], [24, 0x0F]],
        },
        "pitch": pitch(m, pairs),
        "streams": {
            "note_on": {
                "rows": [
                    {
                        "sets": [
                            ["ctrl", {"and": [{"ins": "wave"}, "gate"]}],
                            ["pw_lo", {"cell": "pw_lo"}],
                            ["pw_hi", {"cell": "pw_hi"}],
                            ["ad", {"ins": "adsr.0"}],
                            ["sr", {"ins": "adsr.1"}],
                        ]
                    }
                ],
                "term": "halt",
            },
            "note_off": {
                "rows": [
                    {
                        "sets": [
                            ["ctrl", {"and": [{"cell": "wave"}, 0xFE]}],
                            ["ad", {"const": 0}],
                            ["sr", {"const": 0}],
                        ]
                    }
                ],
                "term": "halt",
            },
            "arp": {"rows": [0, 12], "term": "jump", "kind": "pitch"},
        },
        "accs": accs(),
        "instruments": instruments,
        "score": {"patterns": patterns, "orders": orders},
        "state0": {
            "ins": [m[0x54FE + v] for v in range(3)],
            "wave": [m[0x54F8 + v] for v in range(3)],
            "pwdir": [m[0x5510 + v] for v in range(3)],
            "dividers": {"pulse_bounce": [m[0x550D + v] for v in range(3)]},
        },
    }


def reference(path, song, ticks):
    """The oracle: the tune's own player on the PcodeVM, per-tick SID writes.

    ``sidplayfp <= PcodeVM <= tuneprog <= trackerprog`` -- this is the second
    link, the same interpreter the tuneprog certificate is verified against.
    """
    d = Path(path).read_bytes()
    init, play = struct.unpack(">H", d[10:12])[0], struct.unpack(">H", d[12:14])[0]
    vm, cache = PcodeVM(load(path)), {}
    vm.reg[0] = song
    run_sub(vm, init, cache, lift)
    out = []
    for _ in range(ticks):
        vm.wlog = []
        run_sub(vm, play, cache, lift)
        out.append([(r, v) for _, r, v in vm.wlog])
        vm.cycles += 19656
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("sid")
    ap.add_argument("--song", type=int, default=0, help="subtune index (0, 1 or 2)")
    ap.add_argument("--ticks", type=int, default=11780)
    ap.add_argument("--out", default=None, help="directory for trackerprog.json")
    ap.add_argument("--certify", action="store_true", help="compare with the PcodeVM")
    a = ap.parse_args(argv)
    obj = build(a.sid, a.song)
    if a.out:
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "trackerprog.json").write_text(json.dumps(obj, indent=1))
    print(
        "instruments %d  patterns %d  events %d  pitch %d  accs %d"
        % (
            len(obj["instruments"]),
            len(obj["score"]["patterns"]),
            sum(len(p) for p in obj["score"]["patterns"].values()),
            len(obj["pitch"]),
            len(obj["accs"]),
        )
    )
    if a.certify:
        c = attest(obj, reference(a.sid, a.song, a.ticks))
        c["source"] = {
            "tune": obj["meta"]["tune"],
            "song": a.song,
            "oracle": "deity_informant.PcodeVM",
        }
        print(json.dumps(c, indent=1))
        if a.out:
            (Path(a.out) / "trackerprog.certificate.json").write_text(json.dumps(c, indent=1))
        return 0 if c["divergence"] is None else 1
    render(obj, a.ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
