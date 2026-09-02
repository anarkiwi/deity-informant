#!/usr/bin/env python3
"""lft's Blackbird (*Quintessence*, 2017) as a trackerprog, transliterated by hand.

The seventh family on the universal player of prototype-trackerprog.md sections
4 and 5, and the first whose score does not exist in memory: the tune ships one
LZ-compressed byte stream and the player expands one token per call into three
256-byte ring buffers, so what the sequencer reads is a buffer and never the
file.  Section 6's materialisation rule is what that costs -- the stream, the
buffers and the packed delays are storage, dropped by materialising the decoded
rows over the horizon -- so the tool reads each row's decoded tokens off the
tune's own per-voice cells at the tick its tokenizer finishes them, and the
object carries one event per row and no decompressor.

===========================  ==================================================
the tuneprog says            the trackerprog says
===========================  ==================================================
``phase`` ($E6) by 7         ``meta.tempo`` -- a counter, ``step -7``, the row
                             at ``phase == 0``, reloaded with the tune's own
                             tempo byte
``prepare1``/``2``/``3``     the fetch at ``phase == 21`` and the prelude at
                             ``phase == 14``: one row program, three ticks
                             ahead of the boundary it belongs to
``p_1059`` SR=0, mask $FE    ``Ins.prelude`` -- the hard restart is the
                             pipeline, not a schedule
``execute`` per voice        ``meta.row``: the gate mask, the note, the two
                             restart acts and the envelope act
``T1538[pendfx]``            a ``point`` command on the pitch stream
``T155D`` (fxtable)          the ``pitch`` stream: an offset in quarter
                             semitones, the *next* byte the loop marker
``T15EC`` (wavetable)        the ``wave`` stream: a control byte, a backward
                             jump resolved at build time, a pulse parameter
``v_pwidth`` + ``$1400``     ``Acc(pwidth, const(b) | reload(b<<1), wrap 8)``
                             read through the ``pwprepare`` table
``F[y+24]``, ``F[y+19]+..``  ``pitch``: one u16 row per quarter semitone
``T1559`` (filttable)        ``globals.after`` -- the filter steps after the
                             voices, and commits $D418, $D417, $D416
===========================  ==================================================

Usage::

    tools/trackerprog_blackbird.py Quintessence.sid --out out/tp --certify
"""

import argparse
import hashlib
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

TICKS = 10426  # the whole certified horizon: 2,085 rows of five frames, 208 s

# the tune's own tables, at the addresses its certified program reads them at
FREQ_HI, FREQ_LO = 0x1331, 0x1391  # one 207-byte array, the two halves 15 apart
PWPREPARE = 0x1400  # the page that linearises an 8-bit accumulator into 12-bit pw
INS_AD, INS_SR, INS_WAVE, INS_FILT = 0x14FF, 0x150D, 0x151B, 0x1529  # 1-based
FX_START, FILTTABLE, FXTABLE, WAVETABLE = 0x1537, 0x1559, 0x155D, 0x15EC
FXLEN, WAVELEN = 143, 72
TEMPO_BYTE, GROOVE_BYTE = 0x21F9, 0x21F8  # the stream's own tempo command operand

# the per-voice cells, stride 7 = the SID voice stride
PWIDTH, PENDNOTE, PENDFX, PENDINS, WAVEMASK, TRTIMER = (
    0x12EE,
    0x12F0,
    0x12F1,
    0x12F2,
    0x12F3,
    0x12F4,
)
FXPOS, BASEPITCH, WAVEPOS = 0x1303, 0x1306, 0x1307
MASTER = 0x00E6  # the row timer, in units of 7, and the unpacker's voice index

GATE_OFF = 0xFE  # a pendins of $FE clears the gate; $FF, legato, this tune never has
# the hard-restart threshold is a compare *immediate* and not a table byte: the
# exporter sorts the instruments so one compare replaces a per-instrument flag,
# and the player is emitted per tune, so it is read from this build's own three
# operands rather than assumed -- `CMP #imm` at $105C, `CPY #imm` at $1218/$1233
RESTART_OPERANDS = (0x105D, 0x1219, 0x1234)
# the row timer's own five values, and what each frame of a row is for: the three
# tokenizer passes read one class of token each, and the fourth applies them
FETCH_PHASE, EARLY_PHASE, LAST_PASS, ROW_PHASE = 21, 14, 7, 0
ROW_FRAMES, BEFORE_ROW = 5, 3  # a row is five frames and its boundary is the fourth
QUARTER = 4  # a note is four quarter semitones; the pitch table is one per quarter


def restart(m):
    """The instrument number at or above which a note-on hard-restarts.

    One compare against a constant in place of a per-instrument flag, and the
    same constant at all three of its sites -- the two the anatomy calls
    ``INS_RESTART`` and ``INS_RESTART2``.  A build whose two differ would need
    two thresholds, and this one says so by disagreeing.
    """
    ops = {m[a] for a in RESTART_OPERANDS}
    assert len(ops) == 1, "this build's two hard-restart thresholds differ: %r" % (sorted(ops),)
    return ops.pop()


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


def signed(b):
    return b - 256 if b & 0x80 else b


def run(path, ticks=TICKS):
    """One pass of the tune's own player: the oracle's writes, and the decoded rows.

    The score is compressed and the buffers are a ring, so the rows do not exist
    until the player has made them; this reads each voice's finished tokens out
    of the cells the tokenizer leaves them in -- ``pendins``, ``pendfx``,
    ``pendnote`` -- at the tick after its last pass, which is the tick before the
    boundary that applies them.  Section 6's materialisation, measured rather
    than re-implemented.
    """
    d = Path(path).read_bytes()
    init, play = struct.unpack(">H", d[10:12])[0], struct.unpack(">H", d[12:14])[0]
    vm, cache = PcodeVM(load(path)), {}
    vm.reg[0] = 0
    run_sub(vm, init, cache, lift)
    m, writes, events = vm.mem, [], [[], [], []]
    post0 = bytes(m)
    due = [False] * 3
    for _ in range(ticks):
        phase = m[MASTER]
        if phase == FETCH_PHASE:  # the tokenizer's first pass: whose row is due
            due = [m[TRTIMER + 7 * v] == 0xFF for v in range(3)]
        vm.wlog = []
        run_sub(vm, play, cache, lift)
        vm.cycles += 19656
        writes.append([(r, v) for _, r, v in vm.wlog])
        if phase == LAST_PASS:  # the third pass has run: the row the next boundary applies
            for v in range(3):
                events[v].append(_event(m, v) if due[v] else _held())
    return post0, writes, events


def _held():
    """A row the tokenizer did not read: the boundary runs and does nothing."""
    return {
        "dur": 1,
        "sounds": False,
        "tie": False,
        "gate": None,
        "note": None,
        "ins": None,
        "arm": None,
    }


def _event(m, v):
    """One decoded row: what the three passes left for this voice's boundary."""
    ins, fx, note = m[PENDINS + 7 * v], m[PENDFX + 7 * v], m[PENDNOTE + 7 * v]
    sounds = 0 < ins < GATE_OFF
    e = _held()
    e["sounds"] = sounds
    e["gate"] = "on" if sounds else ("off" if ins == GATE_OFF else None)
    e["ins"] = ins if sounds else None
    e["note"] = note * QUARTER if sounds else None
    e["arm"] = "fx%d" % fx if fx else None
    return e


def pitch(m, reach):
    """The tuning, one u16 row per **quarter** semitone, as the player reads it.

    Storage is an idiom: the tune keeps 111 sixteen-bit entries in 207 bytes by
    overlapping the two halves 15 apart, and makes a quarter-semitone pitch by
    summing two entries of that one array at fixed offsets rather than by
    interpolating.  What the object carries is the value read, not the bytes
    stored -- and the sums carry the low half's own carry-in, which is the
    "small consistent error" the author's comment names.
    """
    lo, hi = min(reach), max(reach)
    return {
        "base": lo,
        "tuning": "12-TET, quarter semitones; four sums of one 111-entry array",
        "freq": [_quarter(m, p) for p in range(lo, hi + 1)],
    }


def _quarter(m, p):
    """``freq(p)`` for a quarter-semitone index: the sum the player's four arms make."""
    y, q = p >> 2, p & 3
    if q == 0:
        return _f(m, y + 24)
    pairs = {1: (19, 1), 2: (12, 13), 3: (0, 20)}[q]
    return (_f(m, y + pairs[0]) + _f(m, y + pairs[1]) + (q >> 1)) & 0xFFFF


def _f(m, k):
    return m[FREQ_HI + k] << 8 | m[FREQ_LO + k]


def fxstream(m, reach):
    """The pitch programs, as one stream: an offset, and where the next byte sends it.

    ``fxtable[y]`` is a pitch offset in quarter semitones and ``0`` is no pitch
    at all -- the voice's frequency goes to ``$FFFF``.  The *next* byte is the
    loop marker: negative, it is the signed jump added to the cursor, so a
    program needs no terminator row.  A byte offset is its own row: a cursor on
    no row says so, and no index is reserved for it.
    """
    rows = []
    for k in range(FXLEN):
        if k not in reach:
            rows.append({"trap": "fxtable[%d]: the horizon never steps here" % k})
            continue
        jb = m[FXTABLE + k + 1]
        nxt = (k + 1 + (signed(jb) if jb & 0x80 else 0)) & 0xFF
        d = m[FXTABLE + k]
        val = (
            {"const": 0xFFFF}
            if d == 0
            else {"tuned": {"and": [{"add": [{"cell": "note"}, d]}, 0x1FF]}}
        )
        rows.append({"sets": [["pitch", val]], "next": nxt})
    return {"rank": 0, "rows": rows}


def wavestream(m, reach):
    """The wave programs, as one stream: a control byte, and maybe a pulse step.

    A byte below ``$C0`` is written to ``ctrl`` through the voice's gate mask and
    the cursor steps one; a byte at or above it is a relative backward jump the
    read resolves, and is no control byte at all -- so the jump is folded into
    the row that lands on it and never occupies a tick.  Bit 6 says a pulse
    parameter follows, and the step past it is two plus the control byte's own
    sign bit, which is the carry the ``ASL`` that tested bit 6 left behind.
    """
    rows = []
    for k in range(WAVELEN):
        if k not in reach:
            rows.append({"trap": "wavetable[%d]: the horizon never steps here" % k})
            continue
        t = k if m[WAVETABLE + k] < 0xC0 else (k + m[WAVETABLE + k] + 1) & 0xFF
        w = m[WAVETABLE + t]
        assert w < 0xC0, "wavetable[%d] jumps onto another jump at %d" % (k, t)
        row = {"sets": [["ctrl", {"and": [{"const": w}, {"cell": "wavemask"}]}]]}
        if w & 0x40:  # a pulse waveform: the next byte is the accumulator's step
            b = m[WAVETABLE + t + 1]
            row["run"] = [
                {
                    "acc": "pulse",
                    "delta": b,
                    "absolute": (b << 1) & 0xFF,
                    "relative": 0 if b & 0x80 else 1,
                }
            ]
            row["sets"] += [
                [half, {"tabcell": ["pwprepare", {"cell": "pwidth"}, "byte"]}]
                for half in ("pw_lo", "pw_hi")
            ]
            step = t + 2 + (w >> 7)
            # the second inherited carry the certified program keeps -- the one the
            # step's own add leaves -- is zero exactly while this does not wrap, and
            # the table is 72 bytes, so the object states the advance and asserts it
            assert step < 0x100, "the wave cursor wraps at row %d: the step's carry is live" % k
            row["next"] = step
        else:
            row["next"] = t + 1
        rows.append(row)
    return {"rank": 1, "rows": rows}


def filterstream(m):
    """The one filter program, global: volume and routing, then the cutoff.

    The cutoff byte's sign bit picks absolute from relative; this tune's one row
    is absolute and its next row's first byte is ``$FF``, so the cursor holds it
    for ever and the three registers are rewritten with the same values every
    frame.  ``m_cutoff`` is an immediate operand of the player's own ``ADC``,
    which is a cell like any other here.
    """
    c = m[FILTTABLE + 2]
    assert c & 0x80, "the horizon takes only the absolute arm of the cutoff"
    return {
        "rows": [
            {
                "sets": [
                    ["#mode_vol", {"const": m[FILTTABLE]}],
                    ["#res_route", {"const": m[FILTTABLE + 1]}],
                    ["#cutoff", {"const": (c << 1) & 0xFF}],
                    ["#cutoff_hi", {"xor": [{"global": "cutoff"}, 0x80]}],
                ],
                "next": 0,
            },
        ]
    }


def instruments(m, used, ins_restart):
    """The four 1-based columns the note-on reads, plus the threshold that sorts them.

    The exporter sorts the table so that one compare against a constant replaces
    a per-instrument hard-restart flag; ``restart`` is that compare, and the
    instruments it holds for are the ones that carry a prelude.
    """
    out = {
        "0": {
            "adsr": [0, 0],
            "restart": 0,
            "wavepos": 0,
            "accs": [],
            "on_note": [],
            "note": "the cell's post-init value; no row plays it",
        }
    }
    for i in sorted(used):
        assert not m[INS_FILT + i], "instrument %d re-points the filter" % i
        rec = {
            "adsr": [m[INS_AD + i], m[INS_SR + i]],
            "restart": int(i >= ins_restart),
            "wavepos": m[INS_WAVE + i],
            "accs": [],
            "on_note": [{"point": [["wave", {"ins": "wavepos"}, False]]}],
        }
        if i >= ins_restart:
            rec["prelude"] = {"stream": "hard_restart"}
        out[str(i)] = rec
    return out


def build(path, ticks=TICKS):
    """The trackerprog object for *Quintessence*, and the oracle it renders against."""
    m, writes, events = run(path, ticks)
    used = {e["ins"] for v in events for e in v if e["ins"] is not None}
    ins_restart = restart(m)
    fxcmds = sorted({e["arm"] for v in events for e in v if e["arm"] is not None})
    fxreach, wavereach, notes = _reach(m, events, ticks)
    obj = {
        "$trackerprog": 1,
        "meta": {
            "tune": Path(path).name,
            "family": "Blackbird (lft)",
            "song": 0,
            "cycles_per_tick": 19656,
            "voices": 3,
            "voice_order": [2, 1, 0],
            # one act's edges: SR alone, then AD before CTRL, then AD before SR
            "commit_order": ["ad", "sr", "ctrl"],
            "tempo": {
                "cell": "master",
                "step": -7,
                "boundary": [[{"cell": "phase"}, "==", ROW_PHASE]],
                "reset": [
                    {
                        "when": [[{"cell": "phase"}, "==", ROW_PHASE]],
                        "sets": [["@master", {"const": m[TEMPO_BYTE]}]],
                    }
                ],
                "fetch": [[{"cell": "phase"}, "==", FETCH_PHASE]],
                "early": [
                    [{"cell": "phase"}, "==", EARLY_PHASE],
                    [{"cell": "willsound"}, "!=", 0],
                ],
                "note": "the stream's groove mask is $%02X: one reload, not two" % m[GROOVE_BYTE],
            },
            "tick": ["fetch", "prelude", "row", "machine"],
            "row_consumes_tick": False,
            "row_command": "spent",
            "stage": [{"ins": True}, {"sets": [["@willsound", {"payload": "keys"}]]}],
            "row": [
                {"sets": [["@wavemask", {"payload": "gate"}]], "when": [["gate_stmt", "!=", 0]]},
                {
                    "stream": "restart_sr",
                    "when": [["sounds", "!=", 0], [{"ins": "restart"}, "!=", 0]],
                },
                {"note": True, "when": [["sounds", "!=", 0]]},
                {
                    "stream": "restart_gate",
                    "when": [["sounds", "!=", 0], [{"ins": "restart"}, "!=", 0]],
                },
                {"stream": "envelope", "when": [["sounds", "!=", 0]]},
                {"commands": True},
            ],
        },
        "globals": {
            "after": ["filter"],
            "commit": [
                [24, {"global": "mode_vol"}],
                [23, {"global": "res_route"}],
                [22, {"global": "cutoff_hi"}],
            ],
        },
        "pitch": pitch(m, notes),
        "streams": {
            "pitch": fxstream(m, fxreach),
            "wave": wavestream(m, wavereach),
            "filter": filterstream(m),
            "pwprepare": {
                "note": "the page that makes an 8-bit accumulator a 12-bit pulse triangle",
                "rows": [{"byte": {"const": m[PWPREPARE + i]}} for i in range(256)],
            },
            "hard_restart": {
                "rows": [{"sets": [["sr", {"const": 0}], ["@wavemask", {"const": 0xFE}]]}]
            },
            "restart_sr": {"rows": [{"sets": [["sr", {"const": 0x0F}]]}]},
            "restart_gate": {"rows": [{"sets": [["ad", {"const": 0}], ["ctrl", {"const": 0x01}]]}]},
            "envelope": {
                "rows": [{"sets": [["ad", {"ins": "adsr.0"}], ["sr", {"ins": "adsr.1"}]]}]
            },
        },
        "accs": {
            "pulse": {
                "rank": 1,
                "cell": "pwidth",
                "target": "pw",
                "width": 8,
                "delta": {"const": "delta"},
                "delta_when": [[{"const": "relative"}, "!=", 0]],
                "policy": {
                    "reload": {"const": "absolute"},
                    "when": [[{"const": "relative"}, "==", 0]],
                },
                "bound": {
                    "from": "projected",
                    "interval": [0, 0xFF],
                    "witness": "the 8-bit store into v_pwidth; the chip sees 12 through pwprepare",
                },
                "rate": 1,
                "scope": "voice",
                "produce": [],
            }
        },
        "instruments": instruments(m, used, ins_restart),
        "score": {
            "patterns": {str(v): {"events": events[v]} for v in range(3)},
            "orders": [{"play": [v], "end": "horizon"} for v in range(3)],
            "commands": {
                name: {"rows": [{"point": [["pitch", m[FX_START + int(name[2:])], False]]}]}
                for name in fxcmds
            },
        },
        "state0": {
            "ins": [0, 0, 0],
            "cells": {
                "master": [m[MASTER]] * 3,
                "wavemask": [m[WAVEMASK + 7 * v] for v in range(3)],
                "pwidth": [m[PWIDTH + 7 * v] for v in range(3)],
                "note": [m[BASEPITCH + 7 * v] for v in range(3)],
                "willsound": [0, 0, 0],
            },
            "globals": {"mode_vol": 0, "res_route": 0, "cutoff": 0, "cutoff_hi": 0},
            "cursors": {
                "pitch": [{"row": m[FXPOS + 7 * v], "hold": 0} for v in range(3)],
                "wave": [{"row": m[WAVEPOS + 7 * v], "hold": 0} for v in range(3)],
            },
            "gcursors": {"filter": {"row": 0, "hold": 0}},
        },
    }
    return obj, writes


def _reach(m, events, ticks):
    """The rows and the pitches the horizon reaches, walked over the decoded score.

    A row the walk never steps on is a ``trap`` and not a row (section 3.3), and
    the tuning is the span the walk asks for -- both are the horizon's, not the
    table's.  The walk keeps the row's own phasing: the audio engine runs on all
    five frames of a row and the boundary that re-points its two cursors is the
    fourth, so three frames of every row run on the cursors the row before it
    left.  Getting that wrong claims rows the tune never steps on; the render is
    what says it is right, because a row marked ``trap`` and then reached stops it.
    """
    fx, wave, notes = set(), set(), set()
    cur = [[m[FXPOS + 7 * v], m[WAVEPOS + 7 * v], m[BASEPITCH + 7 * v]] for v in range(3)]
    left = ticks
    for k in range(len(events[0])):
        for lead in (BEFORE_ROW, ROW_FRAMES - BEFORE_ROW):
            for _ in range(min(lead, left)):
                for v in range(3):
                    _frame(m, cur[v], fx, wave, notes)
                left -= 1
            if lead == BEFORE_ROW:  # the boundary, between the two runs
                for v in range(3):
                    e = events[v][k]
                    if e["arm"] is not None:
                        cur[v][0] = m[FX_START + int(e["arm"][2:])]
                    if e["sounds"]:
                        cur[v][1] = m[INS_WAVE + e["ins"]]
                        cur[v][2] = e["note"]
    return fx, wave, notes


def _frame(m, cur, fx, wave, notes):
    """One frame of one voice: the two cursors it reads, and where each one goes."""
    y, w = cur[0], cur[1]
    fx.add(y)
    wave.add(w)
    if m[FXTABLE + y]:
        notes.add((m[FXTABLE + y] + cur[2]) & 0x1FF)
    jb = m[FXTABLE + y + 1]
    cur[0] = (y + 1 + (signed(jb) if jb & 0x80 else 0)) & 0xFF
    t = w if m[WAVETABLE + w] < 0xC0 else (w + m[WAVETABLE + w] + 1) & 0xFF
    b = m[WAVETABLE + t]
    cur[1] = (t + (2 + (b >> 7) if b & 0x40 else 1)) & 0xFF


def claim(path):
    """What the source tuneprog's certificate claims, and the binding to it.

    The horizon is the certificate's and never the tool's: this tune is
    ``horizon``-terminated -- 208 seconds of music whose state never repeats, so
    ``period`` is null and there is no inherited loop to re-verify.
    """
    d = Path(path).read_bytes()
    s = json.loads(d)["subtunes"][0]
    assert not s["complete"] and s["period"] is None, "Quintessence's certificate is a horizon"
    return s["ticks"], hashlib.sha256(d).hexdigest()[:16]


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("sid")
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--out", default=None, help="directory for trackerprog.json")
    ap.add_argument("--source", default=None, help="the source tuneprog's certificate.json")
    ap.add_argument("--certify", action="store_true", help="compare with the PcodeVM")
    a = ap.parse_args(argv)
    ticks, source = TICKS, None
    if a.source:
        ticks, source = claim(a.source)
    obj, writes = build(a.sid, a.ticks or ticks)
    if a.out:
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "trackerprog.json").write_text(json.dumps(obj, indent=1))
        text = printer.render(obj)
        (d / "trackerprog.md").write_text(text)
        print("print: " + "  ".join("%s %s" % kv for kv in printer.numbers(text).items()))
    print(
        "instruments %d  rows %d  tuning %d  pitch rows %d  wave rows %d  commands %d"
        % (
            len(obj["instruments"]) - 1,
            sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
            len(obj["pitch"]["freq"]),
            sum(1 for r in obj["streams"]["pitch"]["rows"] if "trap" not in r),
            sum(1 for r in obj["streams"]["wave"]["rows"] if "trap" not in r),
            len(obj["score"]["commands"]),
        )
    )
    if a.certify:
        c = attest(obj, writes)
        c["source"] = {
            "tune": obj["meta"]["tune"],
            "song": 0,
            "oracle": "deity_informant.PcodeVM",
            "certificate_digest": source,
            "rendered_from": digest(obj),
        }
        c["loop"] = None  # the source is a horizon: no repeat to inherit
        c["end"] = {"tick": c["ticks"] - 1, "kind": "horizon"}
        print(json.dumps({k: v for k, v in c.items() if k != "dropped"}, indent=1))
        if a.out:
            (Path(a.out) / "trackerprog.certificate.json").write_text(json.dumps(c, indent=1))
        return 0 if c["divergence"] is None else 1
    render(obj, a.ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
