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
from deity_informant.trackerprog import printer  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.universal import render  # noqa: E402
from deity_informant.vm import PcodeVM, run_sub  # noqa: E402

FREQ_ORIGIN = 0x5428  # pitch index n is the u16 at FREQ_ORIGIN + 2n
INS_BASE, PATPTR_LO, PATPTR_HI = 0x5591, 0x5711, 0x573E
SONGPTR, SPEEDTBL = 0x56FF, 0x5514

# Build-time only, and none of it reaches the object.  The fused region
# commando-floor section 5 documents: the frequency table tunes notes 16..95 at
# $5448..$54E7 and the per-voice state follows it, so an index past 95 names
# state, not tuning.  This decodes *which* state, so the object can carry a
# self-contained generator for that value instead of running off a table.
#
# Each row is (byte width, the generator source for byte i, a name).  ``None``
# is a byte no reachable index names.
FIRST_NOTE, TABLE_END = 16, 96  # the table tunes notes 16..95
OVERFLOW = 12  # a transposition of +12 from the tuning's top twelve notes


def _voice_byte(kind, i):
    """The byte at per-voice cell ``kind[i]``, as a generator's own state."""
    return {"observe": kind, "voice": i}


FUSED = (
    [("sidofs%d" % i, {"sid_base": i}) for i in range(3)]
    + [("voice_base", {"sid_base": "reader"})]
    + [
        ("%s%d" % (k, i), _voice_byte(k, i))
        for k in ("orderpos", "patrow", "rowsleft", "rowbyte", "wave", "note", "ins")
        for i in range(3)
    ]
    + [("scratch%d" % i, None) for i in range(12)]
    + [("%s%d" % (k, i), _voice_byte(k, i)) for k in ("pwdelay", "pwdir") for i in range(3)]
)


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
    """One pattern, materialised: events, and the cursor coordinate each leaves.

    Nothing packed survives.  The row byte's fields are separate columns, and a
    portamento byte becomes ``arm(slide, {delta, phase})`` -- section 3.6's own
    command, the same shape an instrument uses to arm an accumulator.  The
    A pattern is its events and nothing else: a modulator that watches this
    tune's own byte cursor counts it for itself (:func:`_word`).
    """
    base = m[PATPTR_LO + pn] | m[PATPTR_HI + pn] << 8
    out, i = [], 0
    while m[base + i] != 0xFF:
        r = m[base + i]
        e = {
            "dur": r & 0x1F,
            "sounds": not r & 0x40,
            "tie": bool(r & 0x20),
            "gate": None,
            "note": None,
            "ins": None,
            "arm": None,
        }
        i += 1
        if not r & 0x40:
            if r & 0x80:
                x = m[base + i]
                i += 1
                if x < 0x80:
                    e["ins"] = x
                else:
                    e["arm"] = {"arms": [{"acc": "slide", "delta": x & 0x7E, "phase": x & 1}]}
            e["note"] = m[base + i]
            i += 1
        out.append(e)
    return {"events": out}


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
            for e in patterns[str(p)]["events"]:
                cur = e["ins"] if e["ins"] is not None else cur
                if e["note"] is not None:
                    out.add((cur, e["note"]))
    return out


def pitch(m, pairs):
    """The tuning, and the two places a value that is not a pitch belongs.

    A pitch table is a pitch table: a base note and a contiguous run of
    frequencies, notes 16..95.  No note number outside it exists anywhere in
    the object -- not in the score, not in a table, not as an index.

    Two things this tune does are not pitch, and each is private to whatever
    does it:

    * the **arpeggio** transposes past the top of the tuning.  Its bound is the
      tuning and its behaviour at that bound is its own -- twelve words indexed
      by how far past the transposition went, with the private state and the
      subscriptions to feed them.  No other modulator asks for one: the
      vibrato's step above the tuning's last note is measurably never observed,
      so the tuning simply has no interval there;
    * instruments 4 and 7 sound a **drum**, whose frequency is no pitch at all.
      That is a modulator on those instruments -- inline, self-contained, one
      copy each.
    """
    tuning = {
        "base": FIRST_NOTE,
        "freq": [
            m[FREQ_ORIGIN + 2 * n] | m[FREQ_ORIGIN + 2 * n + 1] << 8
            for n in range(FIRST_NOTE, TABLE_END)
        ],
    }
    beyond = _beyond(m)
    drums = {}
    for i, n in sorted(pairs):
        if n < TABLE_END:
            continue
        col = m[INS_BASE + 8 * i : INS_BASE + 8 * i + 8]
        rec = {"state": {}, "on": []}
        rec["value"], why = _word(m, rec, n)
        assert why is None, why
        if col[7] & 4:  # this drum is arpeggiated: its own octave, for the same reason
            rec["octave"], why = _word(m, rec, n + 12)
            assert why is None, why
        drums[i] = rec
    return tuning, beyond, drums


def _beyond(m):
    """The arpeggio's own behaviour past the tuning, by overflow distance."""
    rec = {"index": "how far past it the transposition went", "state": {}, "on": [], "words": []}
    for d in range(OVERFLOW):
        w, why = _word(m, rec, TABLE_END + d)
        rec["words"].append(w if why is None else {"trap": why})
    return rec


def _word(m, rec, n):
    """The word at index ``n`` of the tune's byte array, over ``rec``'s own state."""
    seed, halves = _seed(m), []
    for j in (0, 1):
        label, src = FUSED[2 * (n - TABLE_END) + j]
        if src is None:
            return None, "a cell the tick recomputes; nothing carries it between ticks"
        v = _static(src)
        if v is not None:
            halves.append({"const": v})
            continue
        if "sid_base" in src:
            halves.append(dict(src))
            continue
        kind, i = src["observe"], src["voice"]
        if kind not in EVENTS and kind != "patrow":
            return None, "no event publishes %s" % kind
        rec["state"][label] = seed(kind, i)
        if kind == "patrow":
            # this tune's cursor counts bytes, and a row is one, plus one where it
            # sounds and one where it carries an instrument or an arm.  That is the
            # modulator's own model of the cell it mirrors; the score keeps events.
            subs = [
                {
                    "event": "row",
                    "voice": i,
                    "add": {
                        label: {
                            "add": [
                                {"const": 1},
                                {"add": [{"payload": "sounds"}, {"payload": "field"}]},
                            ]
                        }
                    },
                },
                {"event": "wrap", "voice": i, "set": {label: {"const": 0}}},
            ]
        else:
            name, payload = EVENTS[kind]
            sub = {"event": name, "voice": i, "set": {label: {"payload": payload}}}
            if name == "turn":
                sub["acc"] = "pulse_bounce"
            subs = [sub]
        for sub in subs:
            if sub not in rec["on"]:
                rec["on"].append(sub)
        halves.append({"own": label})
    return {"u16": halves}, None


def _static(src):
    """The value of a source that depends on nothing live, else ``None``."""
    v = src.get("sid_base")
    return 7 * v if isinstance(v, int) else None


def _seed(m):
    """A mirrored cell's value in the post-init image."""
    base = {
        "wave": 0x54F8,
        "note": 0x54FB,
        "ins": 0x54FE,
        "orderpos": 0x54EC,
        "patrow": 0x54EF,
        "pwdir": 0x5510,
    }
    return lambda kind, v: m[base[kind] + v]


EVENTS = {
    "wave": ("sound", "wave"),
    "note": ("note", "note"),
    "ins": ("instrument", "ins"),
    "orderpos": ("order", "pos"),
    "pwdir": ("turn", "phase"),
}


def _flag_default(instruments):
    """The carry no producer leaves: the residue of the index's own three shifts.

    ``bit(ins, 5)`` over the declared instruments, constant-folded where every
    declared id proves it, so the object never reads an index as if it were data.
    """
    if {int(k) >> 5 & 1 for k in instruments} == {0}:
        return {"default": {"const": 0}, "proof": "no declared instrument id has bit 5 set"}
    return {"default": {"bit": [{"cell": "ins"}, 5]}}


def accs():
    """Section 5's records.  Seven forms; six are section 5's own rows."""
    return {
        "vibrato": {
            "id": "vibrato",
            "rank": 0,
            "cell": "tick",
            "target": "freq",
            "width": 16,
            "policy": {"reload": {"notefreq": None}},
            "delta": {
                "repeat": [
                    {"shr": [{"interval": None}, "shift"]},
                    {"fold": [{"cell": "counter"}, 7]},
                ]
            },
            "overflow": "beyond_tuning",
            "delta_when": [[{"cell": "dur"}, ">=", 6]],
            "flag": {"name": "C", "seed": 1, "unguarded": 0},
            "bound": {
                "from": "proved",
                "interval": [0, 3],
                "witness": "the fold's own range; the tuning has no interval above its top",
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
            "delta": {"const": "delta"},
            "phase": {"const": "phase"},
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
                "reload": {"transpose": {"stream": ["arp", {"and": [{"cell": "counter"}, 1]}]}}
            },
            "overflow": "beyond_tuning",
            "bound": {
                "from": "proved",
                "interval": [0, 12],
                "witness": "the arp stream; past the tuning, beyond",
            },
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
    tuning, beyond, drums = pitch(m, pairs)
    for pat in patterns.values():  # an index outside the tuning is no note
        for e in pat["events"]:
            if e["note"] is not None and e["note"] >= TABLE_END:
                e["note"] = None
    acc = accs()
    acc["arpeggio"]["beyond"] = beyond
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
            "on_note": [{"sets": [["freq", {"notefreq": None}]]}],
            "accs": arms,
            **({"pitch": drums[i]} if i in drums else {}),
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
            "row_command": "spent",
            "row": [
                {"ins": True},
                {"note": True, "when": [["sounds", "!=", 0]]},
                {"sets": [["@wave", {"ins": "wave"}]]},
                {"stream": "note_on"},
                {"commands": True},
            ],
            "player": "prototype-trackerprog.md sections 4 and 5",
        },
        "globals": {
            "mode_vol": 0x0F,
            "flags": {"C": _flag_default(instruments)},
            "init_writes": [[4, 0], [11, 0], [4, 0], [11, 0], [18, 0], [24, 0x0F]],
            "stop_writes": [[4, 0], [11, 0], [18, 0], [24, 0x0F]],
        },
        "pitch": tuning,
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
        "accs": acc,
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
        text = printer.render(obj)
        (d / "trackerprog.md").write_text(text)
        print("print: " + "  ".join("%s %s" % kv for kv in printer.numbers(text).items()))
    print(
        "instruments %d  patterns %d  events %d  tuning %d  drums %d  accs %d"
        % (
            len(obj["instruments"]),
            len(obj["score"]["patterns"]),
            sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
            len(obj["pitch"]["freq"]),
            sum(1 for i in obj["instruments"].values() if "pitch" in i),
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
