#!/usr/bin/env python3
"""Martin Walker's *Chameleon* (1990) as a trackerprog, transliterated by hand.

The eighth family on the universal player of prototype-trackerprog.md sections
4 and 5, and the first whose four modulators are one machine unrolled by
*modulator* rather than by voice: pitch, pulse, a second pitch and the filter
are four copies of a "step a triangle by a constant every N calls, turn at the
period" template, the first two indexed by voice and the fourth on the global
channel.  Two of them -- mod1 and mod3 -- sum into **one** frequency offset, so
the turn cannot be a bound on the value, which is neither modulator's: it is a
count of the modulator's own steps, which is the one form section 5 gained.

===========================  ==================================================
the tuneprog says            the trackerprog says
===========================  ==================================================
``$02AF`` against ``$02FF``  ``meta.tempo`` -- a counter, ``step +1``, the row
                             at ``phase == 8``, reloaded with 0
``p_A000`` over two 25-byte  the score: a key is a row, materialised by
key tables                   section 6 -- the token grammar is table
                             membership and the table is not the music
``p_A485`` per block         ``score.orders`` -- one play step per block, and
                             ``score.patterns`` its three tracks of L rows
``row_apply4`` ($A379)       the first row's own ``arm``: the block loads the
                             filter, resets the four modulators and re-arms
                             every voice
``row_apply`` ($A109)        ``Ins.on_note`` -- 30 bytes into five registers
                             and seventeen engine cells, one act
``row_apply3`` ($A2AD)       a drum *is* an instrument: seven bytes, an
                             absolute pitch and a one-shot bend
``p_A0A4`` ($A0A4)           ``gate_lead``/``gate_edge`` -- ``ctrl - 1`` then
                             ``ctrl - 1 + (gate & 1)``, one act each
``p_A60C``/``A692``/``A718`` ``accs`` ``mod1``/``mod2``/``mod3``: one
                             ``reflect`` whose ``amplitude`` is a ``count``
``p_A7B1`` ($A7B1)           the same ``Acc`` on the global channel, stepped by
                             ``globals.after`` once the voices have run
``$AD73..$AD76``             the four ``delta`` constants: RAM the image holds
                             and the player never writes
``$AD00-$AD76`` uncleared    ``state0`` -- init clears page 2 and not the
                             engine, so the residue *is* the initial state
===========================  ==================================================

Usage::

    tools/trackerprog_walker.py Chameleon.sid --out out/tp --certify
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

TICKS = 8052  # the whole certified horizon: 894 rows of nine calls, 80.3 s
CYCLES = 9828  # the CIA period: two play calls a PAL frame
SPEED = 9  # calls to a sequencer row, the tune's own $02FF
RESET = 0x64  # the countdown every modulator reloads on its own fire

# the tune's own tables, at the addresses its certified program reads them at
NOTEKEY, SHIFTKEY, KEYS = 0xAFE7, 0xAFCE, 25  # the two typed keyboards
REST_KEY = 24  # the last row of either keyboard is the space bar
FREQ_LO, FREQ_HI, NOTES = 0xAF0E, 0xAF6E, 96
SONG_LO, SONG_HI = 0xAE64, 0xAE69
BLOCK_LO, BLOCK_HI = 0xAE6E, 0xAE86
DRUM_LO, DRUM_HI, DRUMS = 0xAE9E, 0xAEB6, 24
INS_LO, INS_HI = 0xAECE, 0xAEEE
STEPS = 0xAD73  # mod1, mod2, mod3 and the filter's delta, four bytes of RAM

# the engine block, stride 1 over the three voices, never cleared by init
DELAY, DELAYCTR = 0xAD01, 0xAD04
M1 = (0xAD07, 0xAD0A, 0xAD0D, 0xAD10, 0xAD13, 0xAD16)  # mode rate cd period phase dir
M2 = (0xAD19, 0xAD1C, 0xAD1F, 0xAD22, 0xAD25, 0xAD28)
M3 = (0xAD2B, 0xAD2E, 0xAD31, 0xAD34, 0xAD37, 0xAD3A, 0xAD3D)  # .. and type
M4 = (0xAD40, 0xAD43, 0xAD46, 0xAD4F)  # mode rate cd toggle
PWOFF, FREQOFF = (0xAD59, 0xAD5C), (0xAD5F, 0xAD62)
PWBASE, FREQBASE = (0xAD66, 0xAD69), (0xAD6C, 0xAD6F)
FILT = (0xAD52, 0xAD53, 0xAD54, 0xAD55, 0xAD56, 0xAD57, 0xAD58)
CUTOFF_OFF, CUTBASE = 0xAD65, 0xAD72

# the sequencer's own cells, page 2, cleared by init
CNT = 0x02AF
TRANSPOSE, DETUNE, NOTEMODE, CTRLBYTE = 0x02B2, 0x02BD, 0x02C0, 0x02C3
NEWNOTE, GATESTATE, DRUMACTIVE = 0x02DA, 0x02DD, 0x02F3

# the 30-byte instrument record, by the field the player reads it as
I_AD, I_DEC, I_SUS, I_REL, I_CTRL, I_PW = 0, 1, 2, 3, 4, 5
I_TRANS, I_DETUNE, I_VOL, I_CUT, I_RES = 6, 7, 8, 9, 10
I_M1, I_M2, I_M3, I_M4, I_FILT = 0x0B, 0x0E, 0x11, 0x15, 0x18
I_DELAY, I_MODE = 0x1C, 0x1D
SUSTAIN_MAX = 0x0F  # a full sustain nibble is stored back as $E
DRUM_BASE = 0x80  # where the drum records are numbered as instruments

W16 = 0xFFFF


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


class Tapped(PcodeVM):
    """The oracle, with the one volatile read the anatomy names recorded.

    ``$D41B`` is the family's single stated boundary: a modulator whose period
    is ``$FF`` takes its offset from the chip instead of stepping.  The value
    reaches no guard, only an additive offset, so a pinned stream renders the
    tune -- and the stream is what the object carries (section 6).
    """

    __slots__ = ("taps",)

    def __init__(self, mem_bytes):
        super().__init__(mem_bytes)
        self.taps = []

    def _rd(self, addr, sz):
        val = super()._rd(addr, sz)
        if sz == 1 and addr == 0xD41B:
            self.taps.append(val)
        return val


def run(path, ticks=TICKS):
    """One pass of the tune's own player: its post-init image and its writes."""
    d = Path(path).read_bytes()
    init, play = struct.unpack(">H", d[10:12])[0], struct.unpack(">H", d[12:14])[0]
    vm, cache = Tapped(load(path)), {}
    vm.reg[0] = 0
    run_sub(vm, init, cache, lift)
    m, writes = vm.mem, []
    post0 = bytes(m)
    for _ in range(ticks):
        vm.wlog = []
        run_sub(vm, play, cache, lift)
        vm.cycles += CYCLES
        writes.append([(r, v) for _, r, v in vm.wlog])
    return post0, writes, vm.taps


def word(m, pair, i=0):
    """A 16-bit engine cell: the two halves are three bytes apart, not adjacent."""
    return m[pair[0] + i] | m[pair[1] + i] << 8


def ptr(m, lo, hi, i):
    return m[lo + i] | m[hi + i] << 8


def classify(m, ch):
    """One track byte, as the player's two linear searches read it.

    The score is the keyboard the author typed it on: the note keys first, then
    the shifted keys, and ``$B0`` enters the second search as ``$30``.  Row 24
    of either is the space bar, which is a rest.  The grammar is table
    membership and nothing else, so the tables are storage and the object
    carries the key each byte *is* (section 6).
    """
    if ch != 0xB0:
        for i in range(KEYS):
            if m[NOTEKEY + i] == ch:
                return 0, i
    else:
        ch = 0x30
    for i in range(KEYS):
        if m[SHIFTKEY + i] == ch:
            return 1, i
    raise AssertionError("track byte $%02X is on neither keyboard" % ch)


def blocks_of(m, song):
    """The song's play list, and the block each step names."""
    p = ptr(m, SONG_LO, SONG_HI, song)
    return [m[p + 1 + i] for i in range(m[p])]


def track(m, block, voice, pos):
    """One row of one voice's track.  Position counts from 1; byte 0 is dead."""
    base = ptr(m, BLOCK_LO, BLOCK_HI, block)
    return m[base + 15 + voice * m[base + 1] + pos]


def bend(step, period, typ, width):
    """A one-shot's pre-load: the repeated add the reset spends ``period - 1`` on.

    A loop whose count is a cell and whose body is one addition is a
    multiplication the object states as the value it reaches -- the modulator's
    own amplitude -- and not as the loop.  Type 0 bends up into the note and
    type 1 down; a triangle (type 2) has no pre-load and starts centred.
    """
    n = step * (period - 1)
    return (-n if typ == 0 else n if typ == 1 else 0) & ((1 << width) - 1)


def oneshot(step, period, typ, width):
    """The three cells a one-shot modulator's reset leaves, and where it stops.

    ``dir`` is the player's: 1 subtracts.  Walker's mod3 and its filter copy
    seed their direction from the ``type`` byte, so type 1 is the arm that
    comes down; mod1 and mod2 have no type and always start down.
    """
    return {
        "preload": bend(step, period, typ, width),
        "phase0": period >> 1 if typ not in (0, 1) else 0,
        "dir0": 1 if typ == 1 else 0,
        # a one-shot stops where its phase is one short of its period; a
        # triangle never does, and 0 is a value ``phase + 1`` cannot take
        "halt": 0 if typ == 2 else period,
    }


def held():
    """A row the sequencer no longer reads: the clock turns and nothing happens."""
    return {
        "dur": 1,
        "sounds": False,
        "tie": False,
        "gate": None,
        "note": None,
        "ins": None,
        "arm": None,
    }


def score(m, song, rows):
    """The played score, materialised: one pattern per block per voice.

    Whether a note re-triggers or ties is the ``reload`` flag's, and the flag is
    the score's own: a rest, a drum and every block header set it, and a
    re-triggered note leaves it at ``mode - 1``.  Every block header re-arms all
    three voices, so a block's rows are the same rows every time the order plays
    it -- which is what lets twelve blocks carry thirty-two steps.  It is decided
    here rather than kept as a cell because it is decided by the rows and by
    nothing else, the same reduction section 6 makes of a storage idiom.
    """
    # the song stops when its position reaches the length, which is checked
    # after the row that advances it -- so the last step's block never plays
    play = blocks_of(m, song)[:-1]
    pats, used, drums, notes = {}, set(), set(), set()
    for b in sorted(set(play)):
        _pattern(m, b, pats, used, drums, notes)
    end = ["%d.%d.end" % (play[-1], v) for v in range(3)]
    for v in range(3):  # the song's own end: one step of one block carries it
        rws = [dict(e) for e in pats["%d.%d" % (play[-1], v)]["events"]]
        assert rws[-1]["arm"] is None, "the song ends on a row that starts a block"
        rws[-1] = dict(rws[-1], arm=["songend"])
        pats[end[v]] = {"events": rws}
    order = [["%d.%d" % (b, v) for v in range(3)] for b in play[:-1]] + [end]
    left = rows - sum(len(pats[k]["events"]) for k in (x[0] for x in order))
    assert left >= 0, "the horizon is shorter than the song"
    for v in range(3):  # the rows the clock still turns once the song has stopped
        pats["tail.%d" % v] = {"events": [held() for _ in range(left)]}
    return play, pats, order, sorted(used), sorted(drums), sorted(notes)


def _pattern(m, b, pats, used, drums, notes):
    """One block's three tracks, as three patterns of L rows."""
    base = ptr(m, BLOCK_LO, BLOCK_HI, b)
    length, hins = m[base + 1], [m[base + 3 + v] for v in range(3)]
    keys = ["%d.%d" % (b, v) for v in range(3)]
    reload = [1, 1, 1]
    for v in range(3):
        pats[keys[v]] = {"events": []}
    for pos in range(1, length + 1):
        for v in range(3):
            cls, idx = classify(m, track(m, b, v, pos))
            rec = ptr(m, INS_LO, INS_HI, hins[v])
            e = held()
            e["arm"] = _arm(pos, v, b)
            if idx == REST_KEY:
                e["gate"] = "off"
                reload[v] = 1
            elif cls:  # a shifted key: a drum record, absolute and one-shot
                drums.add(idx)
                e.update(sounds=True, gate="off", ins=DRUM_BASE + idx)
                reload[v] = 1
            elif reload[v]:
                used.add(hins[v])
                e.update(sounds=True, gate="on", note=idx, ins=hins[v])
                reload[v] = m[rec + I_MODE] - 1
                notes.add(idx + m[rec + I_TRANS])
            else:  # the tie: the pitch moves and the envelope does not
                e.update(sounds=True, tie=True, note=idx)
                notes.add(idx + m[rec + I_TRANS])
            pats[keys[v]]["events"].append(e)


def _arm(pos, v, block):
    """What the first row of a block carries: the header, as the row's commands."""
    if pos != 1:
        return None
    return ["filter.%d" % block, "reblock", "refilter"] if v == 0 else ["reblock"]


def cell(name):
    return {"cell": name}


def glob(name):
    return {"global": name}


def flag(name):
    return {"flag": name}


def masked(e, m=0xFF):
    return {"and": [e, m]}


def dec(name):
    return masked({"sub": [cell(name), 1]})


def inc(name):
    return masked({"add": [cell(name), 1]})


def modclock(k):
    """One modulator's own clock: the countdown, and the two ways it comes due.

    ``rate`` is an inverted target -- the countdown reloads to 100 and fires
    where it *reaches* the rate, so one byte gives every period from 1 to 100
    calls and 0 is off.  A note-on skips the count and fires, which is what
    phase-locks every modulator to the note.
    """
    on = [[flag("run"), "!=", 0], [cell(k + "rate"), "!=", 0]]
    return [
        {"when": on + [[cell("newnote"), "==", 0]], "sets": [["@" + k + "cd", dec(k + "cd")]]},
        {
            "when": on + [[cell("newnote"), "!=", 0]],
            "sets": [["!" + k + "fire", 1], ["@" + k + "cd", RESET]],
        },
        {
            "when": on + [[cell(k + "cd"), "==", cell(k + "rate")]],
            "sets": [["!" + k + "fire", 1], ["@" + k + "cd", RESET]],
        },
    ]


def modreset(k, off, extra=()):
    """What a note-on does to a modulator: centre the triangle and re-arm it.

    The reset zeroes the offset cell the modulator drives, restarts its
    countdown and puts its phase at half a period, which is what makes the
    triangle centred on the note.  ``mode 1`` is the free-running arm: it keeps
    running across notes and is not reset.
    """
    when = [
        [flag(k + "fire"), "!=", 0],
        [cell("newnote"), "!=", 0],
        [cell(k + "mode"), "!=", 1],
    ]
    sets = [
        ["@" + k + "dir", 1],
        ["@" + off, 0],
        ["@" + k + "cd", RESET],
        ["@" + k + "phase", {"shr": [cell(k + "period"), 1]}],
    ]
    return [{"when": when, "sets": list(extra) + sets}]


def triangle(rank, value, target, dirc, phase, period, fire, step, width, halt=None):
    """One modulator: a triangle stepped by a constant, turning on a count.

    The three per-voice copies and the filter's are one record with four sets of
    operands.  ``phase`` is the direction cell (1 subtracts) and
    ``amplitude.count`` is the period: the turn is the modulator's own step
    count and not a bound on the cell, because mod1 and mod3 sum into the one
    frequency offset and the value there is neither modulator's.  ``delta_when``
    is where a one-shot stops -- one short of its period, the arm ``halt`` names.
    """
    return {
        "rank": rank,
        "cell": value,
        "target": target,
        "width": width,
        "delta": {"const": step},
        "phase": {"cell": dirc},
        "policy": "reflect",
        "amplitude": {"count": cell(period), "cell": phase},
        "bound": {
            "from": "projected",
            "interval": [0, (1 << width) - 1],
            "witness": "the %d-bit cell the write-out adds to its base" % width,
        },
        "rate": 1,
        "scope": "global" if value[:1] == "#" else "voice",
        "produce": [],
        "when": [[flag(fire), "!=", 0]],
        **({"delta_when": [[{"add": [cell(phase), 1]}, "!=", cell(halt)]]} if halt else {}),
    }


def modcols(rec, base):
    """One modulator's three bytes, wherever in a record they sit."""
    return {"mode": rec[base], "rate": rec[base + 1], "period": rec[base + 2]}


def filtercols(m, i):
    """The filter half of a 30-byte record: a block's, not a sound's (``_loadfilt``)."""
    p = ptr(m, INS_LO, INS_HI, i)
    rec = [m[(p + k) & W16] for k in range(30)]
    return {
        "mode_vol": (rec[I_VOL] << 4) + 0x0F,
        "cutoff": rec[I_CUT],
        "res": rec[I_RES] << 4,
        "mod": dict(modcols(rec, I_FILT), type=rec[I_FILT + 3]),
    }


def melodic(m, i, steps):
    """One 30-byte instrument as a record: five registers and its four modulators."""
    p = ptr(m, INS_LO, INS_HI, i)
    rec = [m[(p + k) & W16] for k in range(30)]
    sus = SUSTAIN_MAX - 1 if rec[I_SUS] == SUSTAIN_MAX else rec[I_SUS]
    pw = ((rec[I_PW] << 4) & 0xFF, rec[I_PW] >> 4)
    m3 = modcols(rec, I_M3)
    m3["type"] = rec[I_M3 + 3]
    m3.update(oneshot(steps[2], m3["period"], m3["type"], 16))
    return {
        "adsr": [(rec[I_AD] << 4) + rec[I_DEC], (sus << 4) + rec[I_REL]],
        "ctrl": rec[I_CTRL],
        "pw": list(pw),
        "pwbase": pw[0] | pw[1] << 8,
        "trans": rec[I_TRANS],
        "detune": rec[I_DETUNE],
        "notemode": rec[I_MODE],
        "delay": rec[I_DELAY],
        "drum": 0,
        "m1": modcols(rec, I_M1),
        "m2": modcols(rec, I_M2),
        "m3": m3,
        "m4": {"mode": rec[I_M4], "rate": rec[I_M4 + 1]},
    }


def drum(m, k, steps):
    """One 7-byte drum as an instrument: an absolute pitch and a one-shot bend."""
    p = ptr(m, DRUM_LO, DRUM_HI, k)
    rec = [m[(p + j) & W16] for j in range(7)]
    pw = ((rec[6] << 4) & 0xFF, rec[6] >> 4)
    m3 = {"mode": 2, "rate": rec[0], "period": rec[1], "type": 1}
    m3.update(oneshot(steps[2], m3["period"], m3["type"], 16))
    return {
        "adsr": [rec[5], rec[5] & 0x0F],  # one byte is both: SR is AD's low nibble
        "ctrl": rec[4],
        "pw": list(pw),
        "pwbase": pw[0] | pw[1] << 8,
        "freq": rec[2] | rec[3] << 8,
        "notemode": 2,
        "delay": 0,
        "drum": 1,
        "m3": m3,
        "on_note": "drum_on",
    }


def shared():
    """What every instrument of this family carries: the three modulators the
    engine arms at every note-on, and the note-on itself, stated once (§3.5)."""
    return {"accs": [{"acc": "mod1"}, {"acc": "mod2"}, {"acc": "mod3"}], "on_note": "note_on"}


def _loads():
    """``loadins``: the seventeen engine cells one instrument record fills."""
    out = [
        ["ad", {"ins": "adsr.0"}],
        ["sr", {"ins": "adsr.1"}],
        ["@ctrlbyte", {"ins": "ctrl"}],
        ["ctrl", masked({"sub": [{"ins": "ctrl"}, 1]})],
        ["pw_lo", {"ins": "pw.0"}],
        ["pw_hi", {"ins": "pw.1"}],
        ["@pwbase", {"ins": "pwbase"}],
        ["@transpose", {"ins": "trans"}],
        ["@detune", {"ins": "detune"}],
        ["@notemode", {"ins": "notemode"}],
        ["@delay", {"ins": "delay"}],
    ]
    for k in ("m1", "m2", "m3"):
        out += [["@" + k + c, {"ins": "%s.%s" % (k, c)}] for c in ("mode", "rate", "period")]
    out += [["@m3" + c, {"ins": "m3." + c}] for c in ("type", "preload", "phase0", "dir0", "halt")]
    out += [["@m4mode", {"ins": "m4.mode"}], ["@m4rate", {"ins": "m4.rate"}]]
    # the note's own mod3 reset, which the handler runs whatever the rate is
    out += [["@newnote", masked({"sub": [{"ins": "notemode"}, 1]})]]
    out += [["@delayctr", 0], ["@m3dir", 0], ["@freqoff", 0], ["@m3cd", RESET]]
    return out


KEYED = [["tie", "==", 0]]
OWNS = KEYED + [[cell("voice_index"), "==", glob("owner")]]
NOTE_ROWS = [
    {"when": KEYED, "sets": _loads()},
    {"when": KEYED + [[{"ins": "notemode"}, "!=", 1]], "sets": [["#anynew", 1]]},
    {"when": OWNS + [[{"ins": "notemode"}, "!=", 1]], "sets": [["#ownernew", 1]]},
    {
        "when": KEYED + [[{"ins": "m3.rate"}, "!=", 0]],
        "sets": [
            ["@m3phase", {"ins": "m3.phase0"}],
            ["@m3dir", {"ins": "m3.dir0"}],
            ["@freqoff", {"ins": "m3.preload"}],
        ],
    },
]
DRUM_ROWS = [
    {
        "when": KEYED,
        "sets": [
            # the drum's presets: three modulators off, no delay, one-shot down
            ["@m1rate", 0],
            ["@m2rate", 0],
            ["@m4rate", 0],
            ["@delay", 0],
            ["@m3mode", 2],
            ["@m3type", 1],
            ["@notemode", 2],
            ["@m3rate", {"ins": "m3.rate"}],
            ["@m3period", {"ins": "m3.period"}],
            ["@m3preload", {"ins": "m3.preload"}],
            ["@m3phase0", {"ins": "m3.phase0"}],
            ["@m3dir0", {"ins": "m3.dir0"}],
            ["@m3halt", {"ins": "m3.halt"}],
            ["@ctrlbyte", {"ins": "ctrl"}],
            ["ad", {"ins": "adsr.0"}],
            ["sr", {"ins": "adsr.1"}],
            ["pw_lo", {"ins": "pw.0"}],
            ["pw_hi", {"ins": "pw.1"}],
            ["@pwbase", {"ins": "pwbase"}],
            ["@drumactive", 1],
        ],
    }
]


TUNED = {"tuned": {"add": [cell("note"), cell("transpose")]}}
GATE_ON = [[{"payload": "gate"}, "!=", 0xFE]]
M3RES = [[flag("m3fire"), "!=", 0], [cell("newnote"), "!=", 0], [cell("m3mode"), "!=", 1]]
RAND = "a modulator whose period is $FF takes $D41B: the horizon never asks this one"


def rowstreams():
    """The row's own program, as the streams ``meta.row`` names in order.

    Each firing row of a stream is one act (section 2 rule 1), which is what
    puts four separate ``ctrl`` writes on a drum and two on a note: the player's
    gate writes ``ctrl - 1`` and then ``ctrl - 1 + (gate & 1)``, and a drum does
    it twice -- off, clear the offsets, on.
    """
    lead = [{"sets": [["ctrl", dec("ctrlbyte")]]}]
    keyed = [
        {"when": [[cell("gatestate"), "==", 0]], "sets": [["@newnote", 1], ["#anynew", 1]]},
        {
            "when": [[cell("gatestate"), "==", 0], [cell("voice_index"), "==", glob("owner")]],
            "sets": [["#ownernew", 1]],
        },
        {"sets": [["@gatestate", cell("ctrlbyte")]]},
    ]
    return {
        "undrum": {
            "rows": [
                {"when": [[cell("drumactive"), "!=", 0]], "sets": [["@freqoff", 0], ["@pwoff", 0]]},
                {"sets": [["@drumactive", 0]]},
            ]
        },
        "retune": {
            "rows": [
                {"when": [[cell("voice_index"), "==", 0]], "sets": [["@freqbase", TUNED]]},
                {
                    "when": [[cell("voice_index"), "==", 1]],
                    "sets": [["@freqbase", masked({"sub": [TUNED, cell("detune")]}, W16)]],
                },
                {
                    "when": [[cell("voice_index"), "==", 2]],
                    "sets": [["@freqbase", masked({"add": [TUNED, cell("detune")]}, W16)]],
                },
                {"sets": [["pitch", cell("freqbase")]]},
            ]
        },
        "drumtune": {
            "rows": [{"sets": [["@freqbase", {"ins": "freq"}], ["pitch", {"ins": "freq"}]]}]
        },
        "gate_lead": {"rows": lead},
        "gate_edge": {
            "rows": [{"when": GATE_ON + r.get("when", []), "sets": r["sets"]} for r in keyed]
            + [
                {"when": [[{"payload": "gate"}, "==", 0xFE]], "sets": [["@gatestate", 0]]},
                {
                    "sets": [
                        [
                            "ctrl",
                            masked(
                                {
                                    "add": [
                                        {"sub": [cell("ctrlbyte"), 1]},
                                        {"and": [{"payload": "gate"}, 1]},
                                    ]
                                }
                            ),
                        ]
                    ]
                },
            ]
        },
        "drumclear": {"rows": [{"sets": [["@freqoff", 0], ["@pwoff", 0]]}]},
        "gate_lead2": {"rows": lead},
        "gate_edge2": {"rows": keyed + [{"sets": [["ctrl", masked(cell("ctrlbyte"))]]}]},
        "stop_gate": {
            "rows": [
                {"when": [[cell("notemode"), "!=", 1]], "sets": [["ctrl", dec("ctrlbyte")]]},
                {
                    "sets": [
                        ["@gatestate", 0],
                        ["ctrl", dec("ctrlbyte")],
                        ["@songend", 0],
                    ]
                },
            ]
        },
    }


def machinestreams():
    """The engine, in the rank order the tick runs it: clocks, resets, modulators.

    ``voicemod`` holds the whole block off for ``delay`` calls after a note-on,
    which is one guard the tick evaluates once and every later rank reads --
    the flag channel section 7 gave the object for exactly this.
    """
    clocks = [
        {"when": [[cell("newnote"), "!=", 0]], "sets": [["!run", 1]]},
        {"when": [[cell("delayctr"), "==", cell("delay")]], "sets": [["!run", 1]]},
        {"when": [[flag("run"), "==", 0]], "sets": [["@delayctr", inc("delayctr")]]},
    ]
    for k in ("m1", "m2", "m3", "m4"):
        clocks += modclock(k)
    clocks += [
        {
            "when": [[flag("m3fire"), "!=", 0], [g, "==", v], [cell("m3period"), "!=", 0xFF]],
            "sets": [["!m3step", 1]],
        }
        for g, v in ((cell("newnote"), 0), (cell("m3mode"), 1))
    ]
    clocks += [  # the two arms the certified horizon never takes
        {
            "when": [[flag(k + "fire"), "!=", 0], [cell(k + "period"), "==", 0xFF]],
            "sets": [["@freqoff" if k == "m1" else "@pwoff", {"trap": RAND}]],
        }
        for k in ("m1", "m2")
    ]
    return {
        "clocks": {"rank": 0, "all": True, "rows": clocks},
        "m1reset": {"rank": 1, "all": True, "rows": modreset("m1", "freqoff", [["@delayctr", 0]])},
        "m2reset": {"rank": 3, "all": True, "rows": modreset("m2", "pwoff")},
        "m3reset": {
            "rank": 5,
            "all": True,
            "rows": [
                {
                    "when": M3RES,
                    "sets": [
                        ["@delayctr", 0],
                        ["@m3dir", 0],
                        ["@freqoff", 0],
                        ["@m3cd", RESET],
                    ],
                },
                {
                    "when": M3RES + [[cell("m3rate"), "!=", 0]],
                    "sets": [
                        ["@m3phase", cell("m3phase0")],
                        ["@m3dir", cell("m3dir0")],
                        ["@freqoff", cell("m3preload")],
                    ],
                },
                {
                    "when": [
                        [flag("m3fire"), "!=", 0],
                        [cell("newnote"), "==", 0],
                        [cell("m3period"), "==", 0xFF],
                    ],
                    "sets": [
                        ["@freqoff", {"tabcell": ["noise", cell("noisepos"), "word"]}],
                        ["@noisepos", inc("noisepos")],
                    ],
                },
            ],
        },
        "mod4": {
            "rank": 7,
            "all": True,
            "rows": [
                {
                    "when": [
                        [flag("m4fire"), "!=", 0],
                        [cell("newnote"), "!=", 0],
                        [cell("m4mode"), "!=", 1],
                    ],
                    "sets": [["@m4toggle", 1], ["@m4cd", RESET]],
                },
                {
                    "when": [[flag("m4fire"), "!=", 0]],
                    "sets": [
                        [
                            "ctrl",
                            masked({"add": [{"sub": [cell("ctrlbyte"), 1]}, cell("m4toggle")]}),
                        ]
                    ],
                },
                {
                    "when": [[flag("m4fire"), "!=", 0]],
                    "sets": [["@m4toggle", {"and": [{"add": [cell("m4toggle"), 1]}, 1]}]],
                },
            ],
        },
        "writeout": {
            "rank": 8,
            "all": True,
            "rows": [
                {
                    "when": [[flag("run"), "!=", 0]],
                    "sets": [
                        ["pitch", masked({"add": [cell("freqbase"), cell("freqoff")]}, W16)],
                        ["pw_lo", masked({"add": [cell("pwbase"), cell("pwoff")]})],
                        [
                            "pw_hi",
                            masked(
                                {
                                    "shr": [
                                        masked({"add": [cell("pwbase"), cell("pwoff")]}, W16),
                                        8,
                                    ]
                                }
                            ),
                        ],
                    ],
                },
                {"sets": [["@newnote", 0]]},
            ],
        },
    }


def filterstreams():
    """The fourth copy of the modulator, on the tune's one global channel.

    It runs once the three voices have, so its own note-on is any voice's --
    and its reset is the *owner* voice's, the one whose instrument programmed
    it.  ``globals.after`` is where a channel a voice feeds steps (section 4.4).
    """
    due = [[glob("frate"), "!=", 0]]
    fired = [[flag("ffire"), "!=", 0]]
    return {
        "filterclock": {
            "all": True,
            "rows": [
                {"sets": [["!ffire", 0], ["!freset", 0]]},
                {
                    "when": due + [[glob("anynew"), "!=", 0]],
                    "sets": [["!ffire", 1], ["#fcd", RESET]],
                },
                {
                    "when": due + [[glob("anynew"), "==", 0]],
                    "sets": [["#fcd", masked({"sub": [glob("fcd"), 1]})]],
                },
                {
                    "when": due + [[glob("anynew"), "==", 0], [glob("fcd"), "==", glob("frate")]],
                    "sets": [["!ffire", 1], ["#fcd", RESET]],
                },
                {
                    "when": fired + [[glob("ownernew"), "!=", 0], [glob("fmode"), "!=", 1]],
                    "sets": [["!freset", 1], ["!ffire", 0]],
                },
                {
                    "when": [[flag("freset"), "!=", 0]],
                    "sets": [["#fdir", 0], ["#cutoff_off", 0], ["#fcd", RESET]],
                },
                {
                    "when": [[flag("freset"), "!=", 0]] + due,
                    "sets": [
                        ["#fdir", glob("fdir0")],
                        ["#fphase", glob("fphase0")],
                        ["#cutoff_off", glob("fpreload")],
                    ],
                },
                {
                    "when": fired + [[glob("fperiod"), "==", 0xFF]],
                    "sets": [["#cutoff_off", {"trap": RAND}]],
                },
                {"sets": [["#anynew", 0], ["#ownernew", 0]]},
            ],
        },
        "filtermod": {"rows": [{"run": [{"acc": "filter"}], "next": 0}]},
    }


def commands(m, play, steps):
    """The block header, as the commands its first row carries.

    A block is the tune's mixer: it names an instrument, a filter routing and a
    filter owner per voice, loads that owner's instrument into the three filter
    registers, and resets every modulator.  The reset reads the engine cells and
    not the record, because ``loadins`` has not run: the block's own first note
    is what loads it.
    """
    out = {"songend": {"rows": [{"sets": [["@songend", 1]]}]}}
    out["reblock"] = {
        "rows": [
            {  # $A88B, mod1 -- and the modulation delay, which is mod1's to clear
                "sets": [
                    ["@delayctr", 0],
                    ["@m1dir", 1],
                    ["@freqoff", 0],
                    ["@m1cd", RESET],
                    ["@m1phase", {"shr": [cell("m1period"), 1]}],
                ]
            },
            {  # $A8AB, mod2
                "sets": [
                    ["@m2dir", 1],
                    ["@pwoff", 0],
                    ["@m2cd", RESET],
                    ["@m2phase", {"shr": [cell("m2period"), 1]}],
                ]
            },
            {"sets": [["@m4toggle", 1], ["@m4cd", RESET]]},  # $A8C6, mod4
            {"sets": [["@m3dir", 0], ["@freqoff", 0], ["@m3cd", RESET]]},  # $A8D6, mod3
            {
                "when": [[cell("m3rate"), "!=", 0]],
                "sets": [
                    ["@m3phase", cell("m3phase0")],
                    ["@m3dir", cell("m3dir0")],
                    ["@freqoff", cell("m3preload")],
                ],
            },
        ]
    }
    out["refilter"] = {  # $A958
        "rows": [
            {"sets": [["#fdir", 0], ["#cutoff_off", 0], ["#fcd", RESET]]},
            {
                "when": [[glob("frate"), "!=", 0]],
                "sets": [
                    ["#fdir", glob("fdir0")],
                    ["#fphase", glob("fphase0")],
                    ["#cutoff_off", glob("fpreload")],
                ],
            },
        ]
    }
    for b in sorted(set(play)):
        out["filter.%d" % b] = {"rows": [{"sets": _loadfilt(m, b, steps)}]}
    return out


def _loadfilt(m, b, steps):
    """``loadfilt`` ($A230): the owner instrument's three registers and its modulator.

    The routing bits are the block's and the rest is the instrument's, so the
    one ``$D417`` byte is the two joined -- which is why a filter is a property
    of a block and not of a sound.
    """
    base = ptr(m, BLOCK_LO, BLOCK_HI, b)
    hdr = [m[base + i] for i in range(16)]
    owner = hdr[12] - 1
    f = filtercols(m, hdr[3 + owner])
    mod = f["mod"]
    route = hdr[9] | hdr[10] << 1 | hdr[11] << 2
    seed = oneshot(steps[3], mod["period"], mod["type"], 8)
    return [
        ["mode_vol", f["mode_vol"]],
        ["cutoff_hi", f["cutoff"]],
        ["#cutbase", f["cutoff"]],
        ["res_route", f["res"] | route],
        ["#owner", owner],
        ["#fmode", mod["mode"]],
        ["#frate", mod["rate"]],
        ["#fperiod", mod["period"]],
        ["#ftype", mod["type"]],
        ["#fpreload", seed["preload"]],
        ["#fphase0", seed["phase0"]],
        ["#fdir0", seed["dir0"]],
        ["#fhalt", seed["halt"]],
    ]


def rowprogram():
    """``meta.row``: the steps a row runs, in the order the handlers run them."""
    note = [["sounds", "!=", 0]]
    keyed = [["keys", "!=", 0]]
    isdrum = [[{"ins": "drum"}, "!=", 0]]
    nodrum = [[{"ins": "drum"}, "==", 0]]
    return [
        {"commands": True},  # the block's own header, where this row starts one
        {"ins": True},
        {"stream": "undrum", "when": note + nodrum},
        {"note": True, "when": note},
        {"stream": "retune", "when": note + nodrum},
        {"stream": "drumtune", "when": keyed + isdrum},
        {"stream": "gate_lead", "when": [["gate_stmt", "!=", 0], [cell("notemode"), "!=", 1]]},
        {"stream": "gate_edge", "when": [["gate_stmt", "!=", 0]]},
        {"stream": "drumclear", "when": keyed + isdrum},
        {"stream": "gate_lead2", "when": keyed + isdrum + [[cell("notemode"), "!=", 1]]},
        {"stream": "gate_edge2", "when": keyed + isdrum},
        {"stream": "stop_gate", "when": [[cell("songend"), "!=", 0]]},
    ]


VOICE_CELLS = (
    ("newnote", NEWNOTE),
    ("gatestate", GATESTATE),
    ("ctrlbyte", CTRLBYTE),
    ("notemode", NOTEMODE),
    ("transpose", TRANSPOSE),
    ("detune", DETUNE),
    ("drumactive", DRUMACTIVE),
    ("delay", DELAY),
    ("delayctr", DELAYCTR),
    ("m1mode", M1[0]),
    ("m1rate", M1[1]),
    ("m1cd", M1[2]),
    ("m1period", M1[3]),
    ("m1phase", M1[4]),
    ("m2mode", M2[0]),
    ("m2rate", M2[1]),
    ("m2cd", M2[2]),
    ("m2period", M2[3]),
    ("m2phase", M2[4]),
    ("m3mode", M3[0]),
    ("m3rate", M3[1]),
    ("m3cd", M3[2]),
    ("m3period", M3[3]),
    ("m3phase", M3[4]),
    ("m3type", M3[6]),
    ("m4mode", M4[0]),
    ("m4rate", M4[1]),
    ("m4cd", M4[2]),
    ("m4toggle", M4[3]),
)


def state0(m, steps):
    """The state the tune starts in: page 2 cleared, the engine block not.

    ``init`` writes 25 zeroes to the chip and 89 to page 2 and stops; the
    engine's 119 bytes are whatever the file loaded there, and the first eight
    calls render them.  So the residue *is* section 5's ``state0`` -- including
    the four ``delta`` constants, which the player never writes at all.
    """
    c = {name: [m[a + v] for v in range(3)] for name, a in VOICE_CELLS}
    c["cnt"] = [m[CNT]] * 3
    c["songend"] = [0] * 3
    c["noisepos"] = [0] * 3
    c["freqbase"] = [word(m, FREQBASE, v) for v in range(3)]
    c["freqoff"] = [word(m, FREQOFF, v) for v in range(3)]
    c["pwbase"] = [word(m, PWBASE, v) for v in range(3)]
    c["pwoff"] = [word(m, PWOFF, v) for v in range(3)]
    # the player's direction is "1 subtracts"; mod1 and mod2 subtract on 0 and
    # mod3 and the filter on anything else, which is the type byte they seed from
    c["m1dir"] = [int(not m[M1[5] + v]) for v in range(3)]
    c["m2dir"] = [int(not m[M2[5] + v]) for v in range(3)]
    c["m3dir"] = [int(bool(m[M3[5] + v])) for v in range(3)]
    for v in range(3):
        seed = oneshot(steps[2], m[M3[3] + v], m[M3[6] + v], 16)
        for k, x in seed.items():
            c.setdefault("m3" + k, [0, 0, 0])[v] = x
    fseed = oneshot(steps[3], m[FILT[3]], m[FILT[6]], 8)
    g = {
        "cutbase": m[CUTBASE],
        "cutoff_off": m[CUTOFF_OFF],
        "fmode": m[FILT[0]],
        "frate": m[FILT[1]],
        "fcd": m[FILT[2]],
        "fperiod": m[FILT[3]],
        "fphase": m[FILT[4]],
        "fdir": int(bool(m[FILT[5]])),
        "ftype": m[FILT[6]],
        "anynew": 0,
        "ownernew": 0,
        "owner": (m[0x02D9] - 1) & 0xFF,
    }
    g.update(("f" + k, x) for k, x in fseed.items())
    return c, g


def build(path, ticks=TICKS, song=1):
    """The trackerprog object for *Chameleon*, and the oracle it renders against."""
    m, writes, taps = run(path, ticks)
    steps = [m[STEPS + i] for i in range(4)]
    rows = (ticks - SPEED) // SPEED + 1 if ticks >= SPEED else 0
    play, pats, order, used, drums, notes = score(m, song, rows)
    cells, gl = state0(m, steps)
    ins = {str(i): melodic(m, i, steps) for i in used}
    ins.update((str(DRUM_BASE + k), drum(m, k, steps)) for k in drums)
    ins.setdefault("0", melodic(m, 0, steps))
    obj = {
        "$trackerprog": 1,
        "meta": {
            "tune": Path(path).name,
            "family": "Walker (Chameleon)",
            "song": song,
            "cycles_per_tick": CYCLES,
            "voices": 3,
            "voice_order": [0, 1, 2],
            # one act's edges: an instrument load sends AD, SR and then the
            # gate-off control byte; every later act sends one control byte
            "commit_order": ["ad", "sr", "ctrl"],
            "wide": ["freqbase", "freqoff", "pwbase", "pwoff", "m3preload"],
            "tempo": {
                "cell": "cnt",
                "step": 1,
                "boundary": [[cell("phase"), "==", SPEED - 1]],
                "reset": [
                    {
                        "when": [[cell("phase"), "==", SPEED - 1]],
                        "sets": [["@cnt", 0]],
                    }
                ],
            },
            "tick": ["row", "machine"],
            "row_consumes_tick": False,
            "row_command": "spent",
            "instrument": shared(),
            "row": rowprogram(),
        },
        "globals": {
            "after": ["filterclock", "filtermod"],
            "flags": {
                k: {"default": 0}
                for k in (
                    "run",
                    "m1fire",
                    "m2fire",
                    "m3fire",
                    "m3step",
                    "m4fire",
                    "ffire",
                    "freset",
                )
            },
            "commit": [["cutoff_hi", masked({"add": [glob("cutbase"), glob("cutoff_off")]})]],
        },
        "pitch": {
            "base": notes[0],
            "tuning": "12-TET, PAL; 96 semitones from C-0, the key plus the instrument's transpose",
            "freq": [m[FREQ_LO + k] | m[FREQ_HI + k] << 8 for k in range(notes[0], notes[-1] + 1)],
        },
        "streams": dict(
            rowstreams(),
            note_on={"rows": NOTE_ROWS},
            drum_on={"rows": DRUM_ROWS},
            **machinestreams(),
            **filterstreams(),
            noise={
                "note": RAND,
                "rows": [{"word": b | b << 8} for b in taps],
            },
        ),
        "accs": {
            "mod1": triangle(
                2, "freqoff", "freq", "m1dir", "m1phase", "m1period", "m1fire", steps[0], 16
            ),
            "mod2": triangle(
                4, "pwoff", "pw", "m2dir", "m2phase", "m2period", "m2fire", steps[1], 16
            ),
            "mod3": triangle(
                6,
                "freqoff",
                "freq",
                "m3dir",
                "m3phase",
                "m3period",
                "m3step",
                steps[2],
                16,
                "m3halt",
            ),
            "filter": triangle(
                9,
                "#cutoff_off",
                "cutoff",
                "#fdir",
                "#fphase",
                "#fperiod",
                "ffire",
                steps[3],
                8,
                "#fhalt",
            ),
        },
        "instruments": ins,
        "score": {
            "patterns": pats,
            "orders": [
                {
                    "play": [{"pattern": k[v]} for k in order] + [{"pattern": "tail.%d" % v}],
                    "end": "stop",
                }
                for v in range(3)
            ],
            "commands": commands(m, play[: len(order)], steps),
        },
        "state0": {
            "ins": [0, 0, 0],
            "cells": cells,
            "globals": gl,
            "gcursors": {"filtermod": {"row": 0, "hold": 0}},
        },
    }
    return obj, writes


def claim(path):
    """What the source tuneprog's certificate claims, and the binding to it.

    *Chameleon* is the family's whole tune: 8,052 calls, its state repeating at
    8,051 with a period of 72, so the certificate is ``complete`` and the
    trackerprog inherits the repeat rather than a horizon.
    """
    d = Path(path).read_bytes()
    s = json.loads(d)["subtunes"][0]
    assert s["complete"], "Chameleon's certificate is complete"
    return s["ticks"], s["first_repeat"], s["period"], hashlib.sha256(d).hexdigest()[:16]


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("sid")
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--song", type=int, default=1)
    ap.add_argument("--out", default=None, help="directory for trackerprog.json")
    ap.add_argument("--source", default=None, help="the source tuneprog's certificate.json")
    ap.add_argument("--certify", action="store_true", help="compare with the PcodeVM")
    a = ap.parse_args(argv)
    ticks, repeat, period, source = TICKS, None, None, None
    if a.source:
        ticks, repeat, period, source = claim(a.source)
    obj, writes = build(a.sid, a.ticks or ticks, a.song)
    if a.out:
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "trackerprog.json").write_text(json.dumps(obj, indent=1))
        text = printer.render(obj)
        (d / "trackerprog.md").write_text(text)
        print("print: " + "  ".join("%s %s" % kv for kv in printer.numbers(text).items()))
    print(
        "instruments %d  drums %d  blocks %d  rows %d  tuning %d  pinned inputs %d"
        % (
            sum(1 for i in obj["instruments"].values() if not i["drum"]),
            sum(1 for i in obj["instruments"].values() if i["drum"]),
            len(obj["score"]["orders"][0]["play"]) - 1,
            sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
            len(obj["pitch"]["freq"]),
            len(obj["streams"]["noise"]["rows"]),
        )
    )
    if a.certify:
        c = attest(obj, writes)
        c["source"] = {
            "tune": obj["meta"]["tune"],
            "song": a.song,
            "oracle": "deity_informant.PcodeVM",
            "certificate_digest": source,
            "rendered_from": digest(obj),
        }
        c["loop"] = None if repeat is None else {"tick": repeat, "period": period}
        c["end"] = {"tick": c["ticks"] - 1, "kind": "loop" if repeat else "horizon"}
        print(json.dumps({k: v for k, v in c.items() if k != "dropped"}, indent=1))
        if a.out:
            (Path(a.out) / "trackerprog.certificate.json").write_text(json.dumps(c, indent=1))
        return 0 if c["divergence"] is None else 1
    render(obj, a.ticks or ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
