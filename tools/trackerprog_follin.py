#!/usr/bin/env python3
"""Tim Follin's Ghouls'n'Ghosts as a trackerprog, transliterated by hand.

Not a lift, a reading: docs/prototype-follin.md and playroutine-anatomy.md
section 3.6 restated in the trackerprog's vocabulary and rendered by the
universal player.  docs/prototype-follin-trackerprog.md is the mapping.

The family has no orderlist/pattern split and no instrument table: one byte
stream per voice is both, and an instrument is the run of commands before a
note.  So the score is a *program* -- ``$8A`` call, ``$8B`` return, ``$82``/
``$81`` counted loop, ``$87`` jump, ``$86`` stop -- and the fetch is a walk
over it that ends at the first row carrying a length.  Every datum is read
off the post-init image, because the rip stub copies a subtune's blocks to
their run addresses inside ``init``.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.lifter import lift  # noqa: E402
from deity_informant.trackerprog import printer  # noqa: E402
from deity_informant.trackerprog.attest import attest  # noqa: E402
from deity_informant.trackerprog.universal import render  # noqa: E402
from deity_informant.tuneprog.machine import MachineImage  # noqa: E402
from deity_informant.vm import PcodeVM, run_sub  # noqa: E402

VOICES = 3
CYCLES_PER_TICK = 19656
NOTES = 97  # the note table's own length; an index past it is no note

# Zero page, from the instruction that reads it (anatomy 3.6.1): a byte cell is
# base + v, a pointer/word pair base + 2v.  Two arrays hold one 16-bit cell
# where the player keeps its halves apart (``freqsh``).
BYTES = {
    "dur": 0x27,
    "wave": 0x2A,
    "gated": 0x36,
    "gatelen": 0x39,
    "gateoff": 0x3C,
    "pwspd": 0x4B,
    "transpose": 0x4E,
    "bliplen": 0x51,
    "blipwave": 0x54,
    "vibdelay": 0x57,
    "tA": 0x5A,
    "tB": 0x5D,
    "trillcnt": 0x60,
    "portaspd": 0x63,
    "note": 0x66,
    "target": 0x6C,
    "vibcnt": 0x7E,
    "vibdir0": 0x81,
    "halfcnt": 0x84,
    "halfper": 0x87,
    "vibdepth": 0x8A,
    "blipcnt": 0x8D,
    "release": 0x90,
    "trilloff": 0x93,
}
WORDS = {"pw": 0x3F, "pwreset": 0x45}  # base + 2v
SPLIT = {"freqsh": (0x75, 0x78)}  # one 16-bit cell the player keeps as two arrays
ACTIVE = 0x7B  # bit 7 set while the voice runs; $86 clears it
TRACKPTR = 0x21  # the three track pointers init leaves, base + 2v

# The immediates the play routine rewrites: a variable that lives inside an
# instruction, one cell per voice (anatomy 3.6.1).
SMC = {
    "pulse_mode": (0x62EE, 0x64DB, 0x66CA),
    "pulse_mode0": (0x63D4, 0x65C1, 0x67B0),
    "vibdir": (0x6269, 0x6456, 0x6645),
    "trillphase": (0x629E, 0x648B, 0x667A),
    "skipxpose": (0x6382, 0x656F, 0x675E),
    "filtdir0": (0x63EB, 0x65DA, 0x67C9),
}
BLIPFREQ = (0x6BDA, 0x6BF5, 0x6C10)  # the blip's frequency, two bytes inside a handler
FIXEDLEN = (0x640F, 0x65FE, 0x67ED)  # $84's cell: it decides a note's byte length
DEFAULT_PW = 0x6A05  # $80's fallback when the reset value it carries is zero
NOTETAB = (0x6D35, 0x6D96)  # 97 entries, lo table then hi table
FILT = {  # the one global channel: zero page, and the bounds $88 patches
    "cutoff": 0x6F,
    "cutreset": 0x71,
    "filtspd": 0x73,
    "owner": 0x74,
}
FILTDIR, FILTMIN, FILTMAX = 0x6800, (0x6819, 0x6813), (0x6833, 0x682D)

# The command byte's argument count.  ``$85`` is the one variable-length class:
# (register, value) pairs until a byte >= $80, which the handler consumes too.
ARGS = {
    0x80: 3,
    0x81: 0,
    0x82: 1,
    0x83: 1,
    0x84: 1,
    0x85: None,
    0x86: 0,
    0x87: 2,
    0x88: 8,
    0x89: 0,
    0x8A: 2,
    0x8B: 0,
    0x8C: 1,
    0x8D: 1,
    0x8E: 4,
    0x8F: 4,
    0x90: 1,
    0x91: 3,
    0x92: 1,
    0x93: 0,
    0x94: 2,
}
CONTROL = frozenset((0x81, 0x82, 0x86, 0x87, 0x8A, 0x8B))  # the order program's own
FIXPOINT = 64  # passes the call summaries settle in; a track that needs more is refused
TURNS = 3  # arms one bounded sweep runs in one frame; the fourth is a stated trap
# the one thing a row tells the row after it: what the row before it did
_FLAGS = ("blipped", "trilling", "retune", "gsilent") + tuple(
    "%s_turn%d" % (c, k) for c in ("pw", "cutoff") for k in range(TURNS)
)


def load(path):
    """The tune's pre-init image."""
    return MachineImage.from_sid(Path(path).read_bytes())


def image(path, song=0):
    """The band as the tick sees it: the rip stub has run and the blocks are placed."""
    img = load(path)
    vm = PcodeVM(bytearray(img.mem))
    vm.reg[0] = song
    run_sub(vm, img.init, {}, lift)
    return bytearray(vm.mem)


def word(m, a):
    return m[a] | m[a + 1] << 8


class Refused(Exception):
    """A subtune the layer will not emit a trackerprog for, with its reason."""


# ---------------------------------------------------------------------------
# the score: one byte program per voice


def token(m, p, fixed):
    """One token of a track: its kind, its byte, its length and the length it leaves.

    ``fixed`` is ``$84``'s cell, which decides whether a note carries a length
    byte -- so the grammar is not context free and the walk carries it.
    """
    b = m[p]
    if b < 0x80:
        return ("note" if b else "rest", b, 1 + (0 if fixed else 1), fixed)
    if b == 0x85:
        n = 1
        while m[p + n] < 0x80:
            n += 2
        return ("cmd", b, n + 1, fixed)  # the terminator is the handler's too
    if b not in ARGS:
        raise Refused("$%02X at $%04X is not in the grammar" % (b, p))
    return ("cmd", b, 1 + ARGS[b], m[p + 1] if b == 0x84 else fixed)


def procedure(m, entry, fx0, summary, calls):
    """One procedure of a track: its states from the entry to its returns.

    A state is a byte *and* the note length ``$84`` left, because that cell
    decides whether a note carries a length byte -- so a byte the walk reaches
    under two of them is two states, and the grammar is not context free.
    """
    st, rets, todo = set(), set(), [(entry, fx0)]
    while todo:
        s = todo.pop()
        while s not in st:
            st.add(s)
            p, fx = s
            _kind, b, n, nf = token(m, p, fx)
            if b == 0x87:
                s = (word(m, p + 1), nf)
                continue
            if b == 0x8A:
                t = (word(m, p + 1), nf)
                calls.add(t)
                if summary.get(t) is not None:
                    todo.append((p + n, summary[t]))
                break
            if b == 0x8B:
                rets.add(nf)
                break
            if b == 0x86:
                break
            s = (p + n, nf)
    return st, rets


def analyse(m, start):
    """Every state one track reaches, and the length each of its calls comes back with.

    Call and return are the score's own, so the walk is one procedure at a time
    with a summary per call target: a procedure that returned under two note
    lengths would need two return addresses for one pushed one, and is refused.
    """
    summary, procs, seen, states = {}, [(start, 0)], {(start, 0)}, {}
    for _ in range(FIXPOINT):
        changed = False
        for proc in list(procs):
            calls = set()
            st, rets = procedure(m, proc[0], proc[1], summary, calls)
            states[proc] = st
            if len(rets) > 1:
                raise Refused("$%04X returns under %d note lengths" % (proc[0], len(rets)))
            for c in calls:
                if c not in seen:
                    seen.add(c)
                    procs.append(c)
                    changed = True
            r = next(iter(rets), None)
            if proc not in summary or summary[proc] != r:
                summary[proc] = r
                changed = True
        if not changed:
            return set().union(*states.values()), summary
    raise Refused("the track's note lengths reach no fixpoint")


def blocks(m, start):
    """The track as an order program: blocks of rows, each ending in one step.

    A control byte ends a block and names the step it takes; a block is entered
    only at a label, so every step names its target outright and the list's own
    order carries nothing.
    """
    states, summary = analyse(m, start)
    ends, labels = set(), {(start, 0)}
    for s in sorted(states):
        p, fx = s
        _kind, b, n, nf = token(m, p, fx)
        if b not in CONTROL:
            continue
        ends.add(s)
        if b == 0x87:
            labels.add((word(m, p + 1), nf))
        elif b == 0x8A:
            t = (word(m, p + 1), nf)
            labels.add(t)
            if summary.get(t) is not None:
                labels.add((p + n, summary[t]))
        elif b not in (0x86, 0x8B):
            labels.add((p + n, nf))
    order = sorted(labels & states)
    index = {s: i for i, s in enumerate(order)}
    out = []
    for s in order:
        rows, op, cur = [], None, s
        while cur in states:
            p, fx = cur
            kind, b, n, nf = token(m, p, fx)
            if cur in ends:
                op = order_op(m, b, p, n, nf, index, summary)
                break
            rows.append((kind, b, p, fx))
            cur = (p + n, nf)
            if cur in index:
                op = {"jump": index[cur]}
                break
        if op is None:
            raise Refused("the block at $%04X runs off the track" % s[0])
        out.append({"rows": rows, "op": op, "at": s})
    return out, index


def order_op(m, b, p, n, nf, index, summary):
    """A control byte as one step of the order program (section 3.6's grammar)."""
    if b == 0x86:
        return "stop"
    if b == 0x8B:
        return "ret"
    if b == 0x82:
        return {"mark": m[p + 1], "next": index[(p + n, nf)]}
    if b == 0x81:
        return {"loop": True, "next": index[(p + n, nf)]}
    t = (word(m, p + 1), nf)
    if t not in index:
        raise Refused("$%02X at $%04X leaves the track" % (b, p))
    if b == 0x87:
        return {"jump": index[t]}
    back = summary.get(t)
    op = {"call": index[t]}
    if back is not None:
        op["ret"] = index[(p + n, back)]
    return op


def events(m, rows):
    """One block's rows as events: a command row, or a note or rest with a length.

    A row that carries a length spends the voice's tick; a command row does
    not, and the fetch walks straight through it (``meta.row_ends_fetch``).
    """
    out = []
    for kind, b, p, fixed in rows:
        e = {
            "dur": 0,
            "sounds": False,
            "tie": False,
            "gate": None,
            "note": None,
            "ins": None,
            "arm": None,
        }
        if kind == "cmd":
            e["arm"] = command(m, b, p)
        else:
            e["dur"] = fixed or m[p + 1]
            e["sounds"] = kind == "note"
            e["note"] = b if e["sounds"] else None
        out.append(e)
    return out


def command(m, b, p):
    """One command byte as a section 3.6 command: the cells its bytes name."""
    a = [m[p + 1 + i] for i in range(4)]
    if b == 0x85:
        pairs, i = [], 1
        while m[p + i] < 0x80:
            pairs.append(["reg.%d" % m[p + i], {"const": m[p + i + 1]}])
            i += 2
        return {"rows": [{"sets": pairs}], "why": "$85 raw registers"}
    if b == 0x80:
        pw = a[1] | a[2] << 8
        return _sets(
            [
                ["@pwspd", a[0]],
                ["@pwreset", pw],
                ["@pw", pw if pw else word(m, DEFAULT_PW)],
            ]
        )
    if b == 0x83:
        return _sets([["@gated", 1], ["@gatelen", a[0]]])
    if b == 0x84:
        return {"rows": [], "why": "$84 fixed length: the parse spent it"}
    if b == 0x88:
        return {
            "rows": [
                {
                    "sets": [
                        ["#filtspd", {"const": a[0]}],
                        ["@filtdir0", {"const": a[1]}],
                        ["#cutoff", {"const": a[2] | a[3] << 8}],
                        ["#cutreset", {"const": a[2] | a[3] << 8}],
                        ["#filtmin", {"const": m[p + 5] | m[p + 6] << 8}],
                        ["#filtmax", {"const": m[p + 7] | m[p + 8] << 8}],
                    ]
                }
            ]
        }
    if b == 0x89:
        return {"rows": [{"sets": [["#owner", {"cell": "voice_index"}]]}]}
    if b == 0x8C:
        return _sets([["@transpose", a[0]]])
    if b == 0x8D:
        return {
            "rows": [
                {
                    "sets": [
                        ["ctrl", {"const": a[0]}],
                        ["@wave", {"const": a[0]}],
                        ["@pulse_mode0", {"const": 0xFF if a[0] & 0x40 else 1}],
                        ["@pulse_mode", {"const": 0xFF if a[0] & 0x40 else 1}],
                    ]
                }
            ]
        }
    if b == 0x8E:
        return _sets(
            [["@vibdelay", a[0]], ["@vibdepth", a[1]], ["@halfper", a[2]], ["@vibdir0", a[3]]]
        )
    if b == 0x8F:
        return _sets([["@bliplen", a[0]], ["@blipwave", a[1]], ["@blipfreq", a[2] | a[3] << 8]])
    if b == 0x90:
        return _sets([["@release", a[0]]])
    if b == 0x91:
        return _sets([["@trilloff", a[0]], ["@tA", a[1]], ["@tB", a[2]]])
    if b == 0x92:
        return _sets([["@portaspd", a[0]]])
    if b == 0x93:
        return _sets([["@skipxpose", 0xFF]])
    raise Refused("command $%02X is not in the grammar" % b)


def _sets(pairs):
    return {"rows": [{"sets": [[t, {"const": v}] for t, v in pairs]}]}


# ---------------------------------------------------------------------------
# section 5 shorthands: the object is data, and this is how it is spelled


def C(n):
    return {"cell": n}


def G(n):
    return {"global": n}


def K(v):
    return {"const": v}


def F(n):
    return {"flag": n}


def hi(e):
    return {"and": [{"shr": [e, 8]}, 0xFF]}


def lo(e):
    return {"and": [e, 0xFF]}


def dec(n):
    return {"and": [{"sub": [C(n), 1]}, 0xFF]}


def wrap(e, m=0xFF):
    return {"and": [e, m]}


def streams():
    """The five blocks of the voice's tick, and the two the row program runs.

    Each is section 3.3's stream in its degenerate form -- one pass of guarded
    rows, no cursor -- because this player's modulators are not tables walked by
    a cursor but a fixed sequence of guarded assignments over the voice's own
    cells, which is what the anatomy's pseudocode for section 3.6 is.
    """
    return {
        # 1. the attack blip ends: the note's own frequency and waveform arrive
        "blip": {
            "all": True,
            "rows": [
                {
                    "when": [[C("blipcnt"), "!=", 0]],
                    "sets": [["@blipcnt", dec("blipcnt")], ["!blipped", K(1)]],
                },
                {
                    "when": [[F("blipped"), "!=", 0], [C("blipcnt"), "==", 0]],
                    "sets": [["pitch", C("freqsh")]],
                },
                {
                    "when": [
                        [F("blipped"), "!=", 0],
                        [C("blipcnt"), "==", 0],
                        [C("gated"), "!=", 0],
                    ],
                    "sets": [["ctrl", {"or": [C("wave"), 1]}]],
                },
            ],
        },
        # 2. the vibrato: a signed step on the frequency shadow, its direction a
        # cell the half-period counter complements -- and a half period of zero
        # never turns, which is this family's slide
        "vibrato": {
            "all": True,
            "rows": [
                {
                    "when": [[C("vibdelay"), "!=", 0], [C("vibcnt"), "!=", 0]],
                    "sets": [["@vibcnt", dec("vibcnt")]],
                },
                {
                    "when": [
                        [C("vibdelay"), "!=", 0],
                        [C("vibcnt"), "==", 0],
                        [C("vibdir"), "!=", 0],
                    ],
                    "sets": [
                        ["@freqsh", wrap({"add": [C("freqsh"), C("vibdepth")]}, 0xFFFF)],
                        ["pitch", C("freqsh")],
                    ],
                },
                {
                    "when": [
                        [C("vibdelay"), "!=", 0],
                        [C("vibcnt"), "==", 0],
                        [C("vibdir"), "==", 0],
                    ],
                    "sets": [
                        ["@freqsh", wrap({"sub": [C("freqsh"), C("vibdepth")]}, 0xFFFF)],
                        ["pitch", C("freqsh")],
                    ],
                },
                {
                    "when": [[C("vibdelay"), "!=", 0], [C("vibcnt"), "==", 0]],
                    "sets": [["@halfcnt", dec("halfcnt")]],
                },
                {
                    "when": [
                        [C("vibdelay"), "!=", 0],
                        [C("vibcnt"), "==", 0],
                        [C("halfcnt"), "==", 0],
                        [C("halfper"), "!=", 0],
                    ],
                    "sets": [
                        ["@halfcnt", wrap({"add": [C("halfper"), C("halfper")]})],
                        ["@vibdir", {"xor": [C("vibdir"), 0xFF]}],
                    ],
                },
            ],
        },
        # 3. the trill, else the portamento: two modulators of the note index
        # itself, sharing one tail -- the tuning, read at the note they left
        "pitchmod": {
            "all": True,
            "rows": [
                {
                    "when": [[C("trillcnt"), "!=", 0]],
                    "sets": [["@trillcnt", dec("trillcnt")], ["!trilling", K(1)]],
                },
                {
                    "when": [[F("trilling"), "!=", 0], [C("trillcnt"), "==", 0]],
                    "sets": [
                        ["@trillphase", {"xor": [C("trillphase"), 0xFF]}],
                        ["!retune", K(1)],
                    ],
                },
                {
                    "when": [[F("retune"), "!=", 0], [C("trillphase"), "!=", 0]],
                    "sets": [
                        ["@trillcnt", C("tA")],
                        ["@note", wrap({"add": [C("note"), C("trilloff")]})],
                    ],
                },
                {
                    "when": [[F("retune"), "!=", 0], [C("trillphase"), "==", 0]],
                    "sets": [
                        ["@trillcnt", C("tB")],
                        ["@note", wrap({"sub": [C("note"), C("trilloff")]})],
                    ],
                },
            ]
            + _porta()
            + [
                {
                    "when": [[F("retune"), "!=", 0]],
                    "sets": [["@freqsh", {"transpose": K(0)}], ["pitch", C("freqsh")]],
                },
            ],
        },
        # 4. the pulse sweep: one 16-bit accumulator between two bounds, and a
        # turn that re-applies its own step in the other direction, so the frame
        # a bound is crossed leaves the width where it was
        "pulse": {"all": True, "rows": _sweep()},
        # 5. the note's own two clocks: the release point and the gate-off
        # countdown, either of which silences the voice, and neither of which
        # spends the other's step
        "gate": {
            "all": True,
            "rows": [
                {
                    "when": [[C("release"), "==", C("dur")]],
                    "sets": [["ctrl", {"and": [C("wave"), 0xFE]}], ["!gsilent", K(1)]],
                },
                {
                    "when": [[C("release"), "!=", C("dur")], [C("gateoff"), "==", 0]],
                    "sets": [["ctrl", {"and": [C("wave"), 0xFE]}], ["!gsilent", K(1)]],
                },
                {
                    "when": [[F("gsilent"), "==", 0]],
                    "sets": [["@gateoff", dec("gateoff")]],
                },
            ],
        },
        "noteon": {"all": True, "rows": _noteon()},
        "rest": {"all": True, "rows": [{"sets": [["@gateoff", C("gatelen")]]}]},
        "filter": {"all": True, "rows": _filter()},
    }


def _porta():
    """The portamento: a free ramp of the note index toward a target it takes.

    Not ``clamp(target)`` as section 5 spells it -- the step is in note-index
    units and the target is a note, so the ramp is over the *index* and the
    tuning is read after it, which is what makes an overshoot land exactly on
    the target rather than one step past a frequency.
    """
    off = [[F("retune"), "==", 0], [C("portaspd"), "!=", 0]]
    down, up = off + [[C("note"), ">", C("target")]], off + [[C("note"), "<", C("target")]]
    return [
        {"when": down, "sets": [["@pnew", wrap({"sub": [C("note"), C("portaspd")]})]]},
        {
            "when": down + [[C("pnew"), ">=", C("target")]],
            "sets": [["@note", C("pnew")], ["!retune", K(1)]],
        },
        {
            "when": down + [[C("pnew"), "<", C("target")]],
            "sets": [["@note", C("target")], ["!retune", K(1)]],
        },
        {"when": up, "sets": [["@pnew", wrap({"add": [C("note"), C("portaspd")]})]]},
        {
            "when": up + [[C("pnew"), "<", C("target")]],
            "sets": [["@note", C("pnew")], ["!retune", K(1)]],
        },
        {
            "when": up + [[C("pnew"), ">=", C("target")]],
            "sets": [["@note", C("target")], ["!retune", K(1)]],
        },
    ]


def _bounce(cell, mode, speed, bounds, put, read, passes):
    """A bounded sweep whose turn steps the other way inside the same frame.

    The player tests the bound on the stepped value and, where it crossed,
    complements the direction and *runs the other arm* -- so the frame a bound
    is met leaves the accumulator where it found it, and can meet the far bound
    on the way back.  That is a loop over two arms, and a fixed row list is it
    unrolled: each pass after the first two is guarded by the turn before it,
    and the pass after the last is a trap saying how far the object claims.
    """
    rows, guard, low, high = [], None, bounds[0], bounds[1]
    for k in range(passes):
        down = k % 2 == 0
        if k == 0:
            on = [[read(mode), "==", 0]]
        elif k == 1:
            on = [[read(mode), ">=", 0x80]]
        else:
            on = [[F(guard), "!=", 0]]
        op = "sub" if down else "add"
        rows.append(
            {"when": on, "sets": [[put(cell), wrap({op: [read(cell), read(speed)]}, 0xFFFF)]]}
        )
        guard = "%s_turn%d" % (cell, k)
        for g in (low if down else high)(read(cell)):
            sets = [[put(mode), K(0xFF if down else 0)]]
            if k:
                sets.append(["!" + guard, K(1)])
            rows.append({"when": on + g, "sets": sets})
    rows.append(
        {
            "when": [[F(guard), "!=", 0]],
            "sets": [[put(cell), {"trap": "%s turns %d times inside one frame" % (cell, passes)}]],
        }
    )
    return rows


def _voice_cell(n):
    return "@" + n


def _global_cell(n):
    return "#" + n


def _pulse_low(e):
    """``$62FE``: the borrow out of the high byte, or the low bound as two bytes."""
    return [[[hi(e), ">=", 0x80]], [[hi(e), "==", 0], [lo(e), "<", 0x64]]]


def _pulse_high(e):
    """``$631A``: the high byte over $0F, or equal to it with the low byte at $9B."""
    return [[[hi(e), ">", 0x0F]], [[hi(e), "==", 0x0F], [lo(e), ">=", 0x9B]]]


def _cut_low(e):
    """``$6810``: the borrow, or the high byte *equal* to the bound and the low under it."""
    return [
        [[hi(e), ">=", 0x80]],
        [[hi(e), "==", hi(G("filtmin"))], [lo(e), "<", lo(G("filtmin"))]],
    ]


def _cut_high(e):
    """``$682A``: both bytes at or over the bound -- one test, not two arms."""
    return [[[hi(e), ">=", hi(G("filtmax"))], [lo(e), ">=", lo(G("filtmax"))]]]


def _sweep():
    """The pulse width, and the register pair the frame leaves it in."""
    return _bounce(
        "pw", "pulse_mode", "pwspd", (_pulse_low, _pulse_high), _voice_cell, C, TURNS
    ) + [
        {
            "when": [[C("pulse_mode"), "!=", 1]],
            "sets": [["pw_lo", lo(C("pw"))], ["pw_hi", hi(C("pw"))]],
        },
    ]


def _filter():
    """The one global channel: the same sweep over the cutoff, between $88's bounds."""
    return _bounce("cutoff", "filtdir", "filtspd", (_cut_low, _cut_high), _global_cell, G, TURNS)


def _noteon():
    """The note row: the index the transpose makes, and everything it re-arms."""
    return [
        {
            "when": [[C("skipxpose"), "==", 0]],
            "sets": [["@pnew", wrap({"add": [{"payload": "note"}, C("transpose")]})]],
        },
        {
            "when": [[C("skipxpose"), "!=", 0]],
            "sets": [["@pnew", {"payload": "note"}], ["@skipxpose", K(0)]],
        },
        {"when": [[C("portaspd"), "!=", 0]], "sets": [["@target", C("pnew")]]},
        {"when": [[C("portaspd"), "==", 0]], "sets": [["@note", C("pnew")]]},
        {"sets": [["@freqsh", {"transpose": K(0)}], ["pitch", C("freqsh")]]},
        {"sets": [["@trillcnt", C("tA")]]},
        {
            "when": [[C("vibdelay"), "!=", 0]],
            "sets": [
                ["@vibcnt", C("vibdelay")],
                ["@vibdir", C("vibdir0")],
                ["@halfcnt", C("halfper")],
            ],
        },
        {"sets": [["@trillphase", K(0)], ["@gateoff", C("gatelen")]]},
        {
            "when": [[C("pwreset"), "!=", 0]],
            "sets": [["@pw", C("pwreset")], ["@pulse_mode", C("pulse_mode0")]],
        },
        {
            "when": [[C("voice_index"), "==", G("owner")], [G("cutreset"), "!=", 0]],
            "sets": [["#cutoff", G("cutreset")], ["#filtdir", C("filtdir0")]],
        },
        {
            "when": [[C("gated"), "!=", 0], [C("bliplen"), "==", 0]],
            "sets": [["ctrl", {"or": [C("wave"), 1]}]],
        },
        {
            "when": [[C("gated"), "!=", 0], [C("bliplen"), "!=", 0]],
            "sets": [
                ["@blipcnt", C("bliplen")],
                ["pitch", C("blipfreq")],
                ["ctrl", {"or": [C("blipwave"), 1]}],
            ],
        },
    ]


# ---------------------------------------------------------------------------
# the object


def state0(m, starts, index, stopped):
    """Every cell of the object, each read off the image the tick sees."""
    cells = {k: [m[a + v] for v in range(VOICES)] for k, a in BYTES.items()}
    cells.update({k: [word(m, a + 2 * v) for v in range(VOICES)] for k, a in WORDS.items()})
    cells.update(
        {k: [m[a + v] | m[b + v] << 8 for v in range(VOICES)] for k, (a, b) in SPLIT.items()}
    )
    cells.update({k: [m[a] for a in addrs] for k, addrs in SMC.items()})
    cells["blipfreq"] = [word(m, a) for a in BLIPFREQ]
    cells["pnew"] = [0] * VOICES
    cells["orderpos"] = [ix.get((s, 0), 0) for s, ix in zip(starts, index)]
    return {
        "ins": [0] * VOICES,
        "cells": cells,
        "stopped": list(stopped),
        "globals": {
            "cutoff": word(m, FILT["cutoff"]),
            "cutreset": word(m, FILT["cutreset"]),
            "filtspd": m[FILT["filtspd"]],
            "owner": m[FILT["owner"]],
            "filtdir": m[FILTDIR],
            "filtmin": m[FILTMIN[0]] | m[FILTMIN[1]] << 8,
            "filtmax": m[FILTMAX[0]] | m[FILTMAX[1]] << 8,
        },
    }


def score(m, stopped):
    """The three order programs and the blocks of rows they step through.

    A voice the entry never started has no program: a sound effect starts one
    to three of them and leaves the others where ``sidclear`` left them, which
    is a pointer into page zero and no track at all.
    """
    orders, patterns, starts, index = [], {}, [], []
    for v in range(VOICES):
        s = word(m, TRACKPTR + 2 * v)
        starts.append(s)
        if stopped[v]:
            orders.append({"play": [], "end": "stop"})
            index.append({})
            continue
        bl, ix = blocks(m, s)
        index.append(ix)
        play = []
        for b in bl:
            key = str(len(patterns))
            patterns[key] = {"events": events(m, b["rows"])}
            play.append({"pattern": key, "op": b["op"]})
        orders.append({"play": play, "end": "stop"})
    return orders, patterns, starts, index


def pitch(m):
    """The tuning: 97 entries, read at the index a note byte and the transpose make."""
    return {"base": 0, "freq": [word_split(m, n) for n in range(NOTES)]}


def word_split(m, n):
    """Entry ``n`` of the note table, whose two halves are two byte arrays."""
    return m[NOTETAB[0] + n] | m[NOTETAB[1] + n] << 8


def beyond(m, why):
    """What this player reads past the top of its tuning: the bytes that follow it.

    The index is one byte -- every producer of the note cell is an eight-bit
    add or subtract -- so the two tables it reads are 256 entries long and the
    tuning is their first 97.  Past that the read is no pitch: it is the byte
    the image holds there, which for the low table is the high table's own
    start and then the sound-effect pointers and lists.  The bound is the
    index's own width, and every word it admits is stated.
    """
    return {
        "id": why,
        "index": "how far past the tuning the index went",
        "words": [K(word_split(m, n)) for n in range(NOTES, 0x100)],
    }


def build(path, song=0):
    """The trackerprog object for one Ghouls'n'Ghosts subtune."""
    m = image(path, song)
    stopped = [not m[ACTIVE + v] & 0x80 for v in range(VOICES)]
    orders, patterns, starts, index = score(m, stopped)
    return {
        "$trackerprog": 1,
        "meta": {
            "tune": Path(path).name,
            "family": "Follin",
            "song": song,
            "cycles_per_tick": CYCLES_PER_TICK,
            "voices": VOICES,
            "voice_order": list(range(VOICES)),
            "commit_order": ["ctrl", "ad", "sr"],
            "wide": [
                "pw",
                "pwreset",
                "freqsh",
                "blipfreq",
                "cutoff",
                "cutreset",
                "filtmin",
                "filtmax",
            ],
            # no tempo and no speed counter: a note's own frame count is the clock
            "tempo": {
                "cell": "dur",
                "step": -1,
                "rate": 1,
                "phase": 0,
                "boundary": [[C("dur"), "==", 0]],
            },
            # every block of the voice's tick is its own group: this player has
            # no shadow and writes the chip as it goes, so what one block left
            # is on the chip before the next block runs (section 2 rule 1)
            "tick": [
                "machine",
                {"stream": "blip"},
                "commit",
                {"stream": "vibrato"},
                "commit",
                {"stream": "pitchmod"},
                "commit",
                {"stream": "pulse"},
                "commit",
                {"stream": "gate"},
                "commit",
                "row",
            ],
            "row_consumes_tick": True,
            # the fetch is a walk: it takes commands until one row carries a length
            "row_ends_fetch": [["dur", "!=", 0]],
            "row_command": "spent",
            "row": [
                {"commands": True},
                {"stream": "noteon", "when": [["sounds", "!=", 0]]},
                {"stream": "rest", "when": [["dur", "!=", 0], ["sounds", "==", 0]]},
            ],
        },
        "globals": {
            "flags": {k: {"default": K(0)} for k in _FLAGS},
            "streams": [],
            "after": ["filter"],
            # the cutoff is 11 bits over two registers the chip splits 8 and 3
            "commit": [[0x15, lo(G("cutoff"))], [0x16, lo({"shr": [G("cutoff"), 3]})]],
            "stop_writes": [],
        },
        "pitch": pitch(m),
        "streams": _past(streams(), m),
        "accs": {},
        "instruments": {"0": {"accs": []}},
        "score": {"patterns": patterns, "orders": orders, "commands": {}},
        "state0": state0(m, starts, index, stopped),
    }


def _past(sts, m):
    """The two streams that read the tuning carry what lies past it, each its own."""
    for name in ("pitchmod", "noteon"):
        sts[name]["beyond"] = beyond(m, name)
    return sts


# ---------------------------------------------------------------------------
# the oracle


def reference(path, song, ticks):
    """The tune's own player on the PcodeVM, one tick at a time."""
    img = load(path)
    vm = PcodeVM(bytearray(img.mem))
    vm.reg[0] = song
    cache = {}
    run_sub(vm, img.init, cache, lift)
    out = []
    for _ in range(ticks):
        vm.wlog = []
        run_sub(vm, img.play, cache, lift)
        out.append([(r, v) for _, r, v in vm.wlog])
        vm.cycles += CYCLES_PER_TICK
    return out


def claim(path, song):
    """What the source tuneprog's certificate claims, and the binding to it."""
    d = Path(path).read_bytes()
    s = next(x for x in json.loads(d)["subtunes"] if x["song"] == song + 1)
    loop = (
        None
        if s["period"] in (None, 1)
        else {"period": s["period"], "first_repeat": s["first_repeat"]}
    )
    end = "loop" if loop else ("fixed_point" if s["period"] == 1 else "horizon")
    return loop, s["ticks"], hashlib.sha256(d).hexdigest()[:16], end


def loop_holds(obj, loop):
    """Re-verify the inherited claim on the render: the horizon replays itself."""
    n, p = loop["first_repeat"] + 1, loop["period"]
    w = render(obj, n + p)
    return w[n - p : n] == w[n : n + p]


def fixed_point(obj, ticks):
    """A period of one: the last tick writes what the tick before it wrote.

    Not silence, which is how the one other family with this end spells it: the
    filter commits its two registers every frame whatever the voices do, and a
    stopped voice stops its own clock rather than the tune.  What settles is
    the write list, and the claim is that it settles with period one.
    """
    w = render(obj, ticks + 1)  # the tick after the horizon: the first repeat
    return w[-1] == w[-2] != []


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def counts(obj):
    ev = sum(len(p["events"]) for p in obj["score"]["patterns"].values())
    rows = sum(len(s["rows"]) for s in obj["streams"].values())
    return "blocks %d  events %d  stream rows %d  tuning %d  cells %d" % (
        len(obj["score"]["patterns"]),
        ev,
        rows,
        len(obj["pitch"]["freq"]),
        len(obj["state0"]["cells"]),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("sid")
    ap.add_argument("--song", type=int, default=0, help="subtune index, 0-based")
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--out", default=None, help="directory for trackerprog.json")
    ap.add_argument("--source", default=None, help="the source tuneprog's certificate.json")
    ap.add_argument("--certify", action="store_true", help="compare with the PcodeVM")
    a = ap.parse_args(argv)
    loop, ticks, source, end = None, a.ticks or 1000, None, "horizon"
    if a.source:
        loop, ticks, source, end = claim(a.source, a.song)
    ticks = a.ticks or ticks
    try:
        obj = build(a.sid, a.song)
    except Refused as r:  # fail closed: nothing is emitted, and the byte is named
        print(json.dumps({"emitted": False, "refusal": str(r)}, indent=1))
        return 3
    print(counts(obj))
    if a.out:
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "trackerprog.json").write_text(json.dumps(obj, indent=1))
        text = printer.render(obj)
        (d / "trackerprog.md").write_text(text)
        print("print: " + "  ".join("%s %s" % kv for kv in printer.numbers(text).items()))
    if not a.certify:
        render(obj, min(ticks, 2000))
        return 0
    doc = attest(obj, reference(a.sid, a.song, ticks))
    doc["source"] = {
        "tune": obj["meta"]["tune"],
        "song": a.song,
        "oracle": "deity_informant.PcodeVM",
        "certificate_digest": source,
        "rendered_from": digest(obj),
    }
    doc["loop"] = loop and dict(loop, verified=loop_holds(obj, loop))
    doc["end"] = {"tick": doc["ticks"] - 1, "kind": end}
    if end == "fixed_point":
        doc["end"]["verified"] = fixed_point(obj, doc["ticks"])
    print(json.dumps({k: v for k, v in doc.items() if k != "dropped"}, indent=1))
    if a.out:
        (Path(a.out) / "trackerprog.certificate.json").write_text(json.dumps(doc, indent=1))
    return 0 if doc["divergence"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
