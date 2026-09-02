#!/usr/bin/env python3
"""Martin Galway's *Comic Bakery* (1986) as a trackerprog, transliterated by hand.

The ninth family on the universal player of prototype-trackerprog.md sections 4
and 5, and the last of the anatomy's nine.  Its score is a byte-code program --
2-byte notes and fifteen commands with call/return and for/next over an 8-deep
stack -- and its sound is two piecewise-linear generators per voice whose
segments, gradients and loop policy are all cells the score itself can poke.
That is why the instrument materialises and the generators do not: a ``Moke``
writes the *record* the next note will copy, so section 6 folds every load into
the instrument it makes, while a ``DMoke`` writes the *live* machine, so its
gradients and counters stay cells and the poke is a section 3.6 ``sets``.

===========================  ==================================================
the tuneprog says            the trackerprog says
===========================  ==================================================
``$FA..$FC`` ``DEC``/``BEQ`` ``meta.tempo`` -- a divider, ``step -1``, the row
                             at ``dur == 0``, the row's own length reloading it
``T91B6[cursor]`` per voice  the score: one byte program per voice, blocks of
                             rows over the state ``(pc, transpose)``
``vt0``/``vt1``/``vt2``      ``score.orders`` -- ``Ret``/``Call``/``Jmp``/
                             ``CT``/``JT``/``For``/``Next`` are section 3.6's
                             ``ret``/``call``/``jump``/``mark``/``loop``
``Moke``/``FLoad``/``load*`` nothing: the S record they build *is* the
                             instrument, materialised at the note that copies it
``DMoke``                    a command: one ``sets`` on one engine cell
``$8659`` note-on            ``streams.note_on`` -- five acts, and the ``ctrl``
                             pulse only two of the three copies make
``$87D3`` gate/release       ``streams.gate`` -- the two gate modes and the
                             hard kill, guarded rows over ``vadsc``/``vrc``
``$881B`` pulse ramp         ``accs`` ``pm0``/``pm1`` over ``pcurr``
``$887D`` frequency ramp     ``accs`` ``fm0``..``fm3`` over ``fcurr``, the
                             bend ``fmbend`` and the delay ``fmdelay``
``$88AC`` arpeggio           ``streams.arp`` -- a pitch stream read backwards,
                             its rows the eight offset cells
``$816A``                    ``globals.commit`` -- volume and three filter
                             shadows, written before the voices and never moved
===========================  ==================================================

Usage::

    tools/trackerprog_galway.py Comic_Bakery.sid --song 1 --out out/tp \\
        --source docs/certificates/galway-comic-bakery.json --certify
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

CYCLES = 19656  # PAL, single speed: the player is a bare JSR per frame
SONGS = tuple(range(1, 15))  # 1-3 the sequenced music, 4-6 jingles, 7-14 effects
TICKS = 9450  # 3:09, the HVSC length of the main theme, and the horizon of all three

SREC = (0x8C56, 0x8C8B, 0x8CC0)  # the S records, $35 apart; +$1D.. is the stack
DREC = (0x8D05, 0x8D2C, 0x8D53)  # the D records, $27 apart
ZPC, ZFREE, ZNOTE, ZCLOCK, ZSP = 0xF0, 0xF9, 0xF8, 0xFA, 0xFD
TRANSP, MFL, FILTSH, VOL = 0x8D7A, 0x8D7D, 0x8D7E, 0x8D82
HIFRQ, LOFRQ, IDRT = 0x8D92, 0x8DF1, 0x8CF4
STL, STH, STC = 0x1D, 0x25, 0x2D  # the 8-deep stack, inside the S record
SLEN = 0x1D  # the instrument: S[$00..$1C]

REST, SILENT, RAW, CMD0 = 0x5F, 0x5E, 0x60, 0xC0
# name, length: the fifteen handlers of the vt tables, in dispatch order
CMDS = (
    ("Ret", 1),
    ("Call", 3),
    ("Jmp", 3),
    ("CT", 4),
    ("JT", 4),
    ("Moke", 3),
    ("For", 2),
    ("Next", 1),
    ("FLoad", 5),
    ("load10", 3),
    ("load14", 3),
    ("load5", 3),
    ("DMoke", 3),
    ("Code", 3),
    ("Transp", 2),
)
# the D offsets a DMoke names, as the cells of section 5's vocabulary they are.
# $00-$07 are the four 16-bit FM gradients *and* the eight arpeggio offsets, so
# they are eight byte cells and the arpeggio reads them as a table
DCELL = {
    # D+$0A and D+$0B are FMD2 and FMD3 with bit 3 of the control byte clear and
    # the arpeggio's base note and last index with it set: one byte, two
    # readings, and the object keeps the name the four-segment machine gives it
    0x0A: "fmd2",
    0x0B: "fmd3",
    0x0C: "fmdly",
    0x0D: "fmc",
    0x0E: "pmd0",
    0x0F: "pmd1",
    0x10: "pmdly",
    0x11: "pmc",
    0x1A: "vwfg",
    0x1B: "vadsc",
    0x1C: "vrc",
    0x1F: "fmd0c",
    0x20: "fmd1c",
    0x21: "fmd2c",
    0x22: "fmd3c",
    0x25: "pmd0c",
    0x26: "pmd1c",
}
DCELL.update({k: "g%d" % k for k in range(8)})
DCELL.update({8: "fmd0", 9: "fmd1"})
WIDE = ("pmg0", "pmg1", "pinit", "pcurr", "vfreq", "fcurr")
# a DMoke on one half of a 16-bit cell: the byte store the object states as one
HALF = {
    0x12: ("pmg0", 0),
    0x13: ("pmg0", 1),
    0x14: ("pmg1", 0),
    0x15: ("pmg1", 1),
    0x16: ("pinit", 0),
    0x17: ("pinit", 1),
    0x18: ("vfreq", 0),
    0x19: ("vfreq", 1),
    0x1D: ("fcurr", 0),
    0x1E: ("fcurr", 1),
    0x23: ("pcurr", 0),
    0x24: ("pcurr", 1),
}


class Refused(Exception):
    """A residue the object has no form for (section 8)."""


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


def entries(path):
    d = Path(path).read_bytes()
    return struct.unpack(">H", d[10:12])[0], struct.unpack(">H", d[12:14])[0]


def word(m, a):
    return m[a] | m[a + 1] << 8


def run(path, song, ticks):
    """One pass of the tune's own player: the post-init image and its writes."""
    init, play = entries(path)
    vm, cache = PcodeVM(load(path)), {}
    vm.reg[0] = song - 1
    run_sub(vm, init, cache, lift)
    post = bytes(vm.mem)
    writes = []
    for _ in range(ticks):
        vm.wlog = []
        run_sub(vm, play, cache, lift)
        vm.cycles += CYCLES
        writes.append([(r, v) for _, r, v in vm.wlog])
    return post, writes


class Sequencer:
    """The three byte-code interpreters, simulated over the certified horizon.

    Only the sequencer: the one thing the engine leaves it is the free bit, and
    :meth:`note` asserts that at every note-on rather than carrying it, so the
    walk is exact without the engine.  What it collects is what section 6 asks
    for -- the state each row is read under (:meth:`state`) and the successor of
    every one -- and the duration table is read out of the image band because the
    *song* loads it (``fload`` into ``S2 + $35``, whose entry 0 is voice 2's own
    stack slot 7, so S and the three stacks are one array here as they are there).
    """

    def __init__(self, m):
        self.m = bytearray(m)
        self.pc = [word(self.m, ZPC + 2 * v) for v in range(3)]
        self.clock = [self.m[ZCLOCK + v] for v in range(3)]
        self.tr = [self.m[TRANSP + v] for v in range(3)]
        self.stack = [[] for _ in range(3)]  # the call sites, shadowing the tune's
        self.ins, self.images = {}, []  # the S records the walk interns, section 6
        self.sp = [self.m[ZSP + v] for v in range(3)]
        self.rows = {}  # a state -> the row it reads, resolved
        self.succ = {}  # a state -> the states it goes to
        self.ret = {}  # a call site -> the state its return comes back to
        self.runs = [bool(self.m[ZFREE] >> v & 1) for v in range(3)]
        # the record each voice's engine starts on: init clears neither S nor D,
        # so the image's own residue is instrument 0, 1 and 2 of the object
        self.ins0 = [self.state(v)[3] for v in range(3)]
        self.start = [self.state(v) if self.runs[v] else None for v in range(3)]

    def state(self, v):
        """Where a row is read, and the three things the score left behind for it.

        The sixth family's states carry the note length a block was entered
        with; this one carries the transpose, the instrument record and whether
        the stack under it is empty -- all three being things a block called from
        two places inherits.  Interning the record here is what makes section 6's
        materialisation possible at all: an ``Ins`` is a constant of the object,
        so a row that reads two records is two rows.
        """
        img = bytes(self.m[SREC[v] : SREC[v] + SLEN])
        i = self.ins.get(img)
        if i is None:
            i = self.ins[img] = len(self.images)
            self.images.append(img)
        # the empty flag is what says a ``Ret`` ends this voice's sequencer
        # rather than returning, which is section 3.6's ``stop``
        return (v, self.pc[v], self.tr[v], i, int(self.sp[v] == 7))

    def tick(self):
        for v in (2, 1, 0):
            if not self.m[ZFREE] >> v & 1:  # the run bit: a stopped sequencer runs no clock
                continue
            self.clock[v] = (self.clock[v] - 1) & 0xFF
            if not self.clock[v]:
                self.step(v)

    def step(self, v):
        """Commands back to back until a note or a rest reloads the clock."""
        for _ in range(256):
            s = self.state(v)
            b = self.m[self.pc[v]]
            nxt = self.command(v, b) if b >= CMD0 else self.note(v, b)
            self.succ.setdefault(s, set()).add(self.state(v))
            if nxt:
                return
        raise Refused("voice %d read no note in 256 commands" % v)

    def note(self, v, b):
        """A note or a rest: two bytes, the second the clock's own reload."""
        s, m = self.state(v), self.m
        raw = b >= RAW
        n = b - RAW if raw else b
        dur = m[self.pc[v] + 1] if raw else m[IDRT + m[self.pc[v] + 1]]
        row = {
            "dur": dur or 256,  # a raw 0 counts 256 frames down: the DEC wraps
            "sounds": n != REST,
            "tie": False,
            "gate": None,
            # the row keeps its own note and the play step keeps the transpose
            # (section 3.6): $5E is the silence note, which the source does not
            # transpose and which the tuning answers with zero, so it is section
            # 3.5's sound with no pitch at all and carries no note column
            "note": None if n in (REST, SILENT) else n,
            "ins": None if n == REST else s[3],
            "arm": None,
            "idx": None if n == REST else (SILENT if n == SILENT else (n + self.tr[v]) & 0xFF),
        }
        if row["note"] is not None:
            assert row["idx"] == row["note"] + signed8(self.tr[v]), (
                "the transpose at $%04X wraps the note column" % self.pc[v]
            )
        self.record(s, row)
        if row["sounds"]:
            assert self.m[MFL] >> v & 1, "voice %d: the music does not hold it" % v
            assert self.m[ZFREE] >> (3 + v) & 1, "voice %d: an effect holds the chip" % v
            self.arm(v, row)
        self.clock[v] = dur
        self.pc[v] += 2
        return True

    def arm(self, v, row):
        """The note-on's own copy: S into D, and the two reload primitives."""
        m, d, s = self.m, DREC[v], SREC[v]
        m[d + 0x1A] = m[s + 0x18]
        m[d + 0x18], m[d + 0x19] = m[LOFRQ + row["note"]], m[HIFRQ + row["note"]]
        m[d + 0x11] = m[s + 0x11]
        if m[d + 0x11]:
            m[d + 0x0E : d + 0x18] = m[s + 0x0E : s + 0x18]
            m[d + 0x23 : d + 0x25] = m[d + 0x16 : d + 0x18]  # PCURR = PINIT
            m[d + 0x25], m[d + 0x26] = m[d + 0x0E], m[d + 0x0F]
        m[d : d + 0x0E] = m[s : s + 0x0E]
        if m[s + 0x0D] & 8:
            assert row["note"] is not None, "the silence note keys an arpeggio instrument"
            m[d + 0x0A] = row["idx"]
        else:
            m[d + 0x1D], m[d + 0x1E] = m[d + 0x18], m[d + 0x19]
            m[d + 0x1F : d + 0x23] = m[d + 8 : d + 0x0C]
        m[d + 0x1B], m[d + 0x1C] = m[s + 0x1B], m[s + 0x1C]

    def record(self, s, row):
        was = self.rows.get(s)
        if was is not None and was != row:
            d = [k for k in row if was.get(k) != row.get(k)]
            raise Refused(
                "state v%d $%04X/%d/%d/%d reads two different rows: %s"
                % (s + (", ".join("%s %r/%r" % (k, was.get(k), row[k]) for k in d),))
            )
        self.rows[s] = row

    def command(self, v, b):  # noqa: C901 - one clause per handler, as the vt table is
        """One of the fifteen handlers, and whether it ends the sequencer's walk."""
        k = (b - CMD0) // 2
        if k >= len(CMDS):
            raise Refused("byte $%02X at $%04X is no command" % (b, self.pc[v]))
        name, ln = CMDS[k]
        m, p, s = self.m, self.pc[v], self.state(v)
        op1, op2 = m[p + 1], m[p + 2]
        target = op1 | op2 << 8
        if name in ("Moke", "FLoad", "load10", "load14", "load5"):
            self.record(s, {"kind": name})
            self.instrument_load(v, name, p)
            self.pc[v] += ln
        elif name == "DMoke":
            self.record(s, {"kind": name, "off": op1, "val": op2})
            self.poke(v, op1, op2)
            self.pc[v] += ln
        elif name == "Transp":
            self.record(s, {"kind": name, "tr": op1})
            self.tr[v] = op1
            self.pc[v] += ln
        elif name in ("Call", "CT"):
            self.record(
                s, {"kind": name, "target": target, "tr": m[p + 3] if name == "CT" else None}
            )
            if name == "CT":
                self.tr[v] = m[p + 3]
            self.push(v, p + ln, s)
            self.pc[v] = target
        elif name in ("Jmp", "JT"):
            self.record(
                s, {"kind": name, "target": target, "tr": m[p + 3] if name == "JT" else None}
            )
            if name == "JT":
                self.tr[v] = m[p + 3]
            self.pc[v] = target
        elif name == "Ret":
            self.record(s, {"kind": name, "stop": False})
            if self.pop(v):  # the stack ran out: this voice's sequencer is done
                return True
        elif name == "For":
            self.record(s, {"kind": name, "count": op1})
            m[SREC[v] + STL + self.sp[v]] = (p + 2) & 0xFF
            m[SREC[v] + STH + self.sp[v]] = (p + 2) >> 8
            m[SREC[v] + STC + self.sp[v]] = op1
            self.sp[v] -= 1
            self.pc[v] += ln
        elif name == "Next":
            c = SREC[v] + STC + self.sp[v] + 1
            self.record(s, {"kind": name, "empty_after": int(self.sp[v] + 1 == 7)})
            m[c] = (m[c] - 1) & 0xFF
            if m[c]:
                k = SREC[v] + self.sp[v] + 1
                self.pc[v] = m[k + STL] | m[k + STH] << 8
            else:
                self.sp[v] += 1
                self.pc[v] += ln
        else:
            raise Refused("%s at $%04X: no exemplar reaches it" % (name, p))
        return False

    def push(self, v, back, site):
        self.m[SREC[v] + STL + self.sp[v]] = back & 0xFF
        self.m[SREC[v] + STH + self.sp[v]] = back >> 8
        self.sp[v] -= 1
        self.stack[v].append((back, site))

    def pop(self, v):
        s = self.state(v)
        self.sp[v] += 1
        if self.sp[v] == 8:  # the stack is spent: this voice's sequencer ends
            self.rows[s] = {"kind": "Ret", "stop": True}
            self.m[ZFREE] &= ~(1 << v) & 0xFF
            return True
        self.pc[v] = self.m[SREC[v] + STL + self.sp[v]] | self.m[SREC[v] + STH + self.sp[v]] << 8
        back, site = self.stack[v].pop()
        assert back == self.pc[v], "the shadow stack lost the tune's own return"
        here = self.state(v)
        if self.ret.setdefault(site, here) != here:
            raise Refused("the call at $%04X returns under two states" % site[1])
        return False

    def instrument_load(self, v, name, p):
        """``Moke``/``FLoad``/``load*``: the S record the next note will copy."""
        m, s = self.m, SREC[v]
        if name == "Moke":
            m[s + m[p + 1]] = m[p + 2]
            return
        if name == "FLoad":
            dst, n, src = m[p + 1], m[p + 2] + 1, word(m, p + 3)
            m[s + dst - n + 1 : s + dst + 1] = m[src : src + n]
            return
        n = {"load10": 10, "load14": 14, "load5": 5}[name]
        src = word(m, p + 1)
        off = 0x18 if name == "load5" else 0
        m[s + off : s + off + n] = m[src : src + n]

    def poke(self, v, off, val):
        """``DMoke``: the live machine, one byte."""
        self.m[DREC[v] + off] = val


# a transpose ends a block like the two that jump: the play step carries the
# column (section 3.6) and a row after it is read under a different one, so the
# state changes and the block with it
CONTROL = ("Ret", "Call", "Jmp", "CT", "JT", "For", "Next", "Transp")
DROPPED = ("Moke", "FLoad", "load10", "load14", "load5")


def goes(s, row):
    """Where one control byte sends the program, as a state and not an address.

    Every target but a ``Ret``'s is static: the byte carries its own address or
    its own length, and none of the seven touches the record, so the target
    inherits the state's instrument and every transpose but ``CT``/``JT``'s own.
    """
    v, pc, tr, ins, _empty = s
    kind = row["kind"]
    if kind in ("Jmp", "Call"):
        return [(v, row["target"], tr, ins, 0 if kind == "Call" else _empty)]
    if kind in ("JT", "CT"):
        return [(v, row["target"], row["tr"], ins, 0 if kind == "CT" else _empty)]
    if kind == "For":
        return [(v, pc + 2, tr, ins, 0)]
    if kind == "Next":
        return [(v, pc + 1, tr, ins, row["empty_after"])]
    if kind == "Transp":
        return [(v, pc + 2, row["tr"], ins, _empty)]
    return []


def labels(seq):
    """Where a block begins: the three entries, and every control transfer's target."""
    out = {s for s in seq.start if s is not None}
    for s, row in seq.rows.items():
        # the byte that ends a voice's sequencer begins a block of its own, so
        # the stop lands on the boundary after the last row and not on the tick
        # that row was read: the order step a block's last row takes is eager
        if row.get("stop"):
            out.add(s)
        if row.get("kind") in CONTROL:
            out |= set(goes(s, row))
            out |= seq.succ.get(s, set())
    out |= set(seq.ret.values())
    out |= {t for ts in seq.succ.values() for t in ts if t not in seq.rows}
    return out


def blocks(seq):
    """The score as an order program: blocks of rows, each ending in one step.

    Follin's shape, and for the same reason -- one byte stream is orderlist and
    pattern at once, so a block is a run of rows and the control byte that ends
    it is the step.  The state a block is entered under carries the transpose,
    which is why ``Ret`` needs the fixpoint the sixth family's note lengths
    needed: a procedure returning under two transposes would want two return
    addresses for one pushed one, and :meth:`Sequencer.pop` refuses it.
    """
    lab = labels(seq)
    order, index = [], {}
    for v in range(3):
        mine = sorted(
            (s for s in lab if s[0] == v),
            key=lambda s, e=seq.start[v]: (s != e,) + s[1:],
        )
        for i, s in enumerate(mine):
            index[s] = i  # the order program is the voice's own, and its entry is step 0
        order += mine
    out = []
    for s in order:
        rows, op, cur = [], None, s
        if s not in seq.rows:  # the horizon ends here: the order program has no more
            out.append({"at": s, "rows": [], "op": "stop", "horizon": True})
            continue
        while True:
            row = seq.rows[cur]
            if row.get("kind") in CONTROL:
                op = order_op(seq, cur, row, index)
                break
            rows.append((cur, row))
            nxt = seq.succ[cur]
            if len(nxt) != 1:
                raise Refused("state $%04X has %d successors" % (cur[1], len(nxt)))
            cur = next(iter(nxt))
            if cur in index:
                op = {"jump": index[cur]}
                break
        out.append({"at": s, "rows": rows, "op": op})
    return out, index


def order_op(seq, s, row, index):
    """A control byte as one step of section 3.6's order grammar.

    ``Next`` names only where the loop goes when its count is spent: where it
    goes when the count survives is the mark's, which ``mark`` already said and
    the player's own counted register decides.
    """
    kind = row["kind"]
    if kind == "Ret":
        return "stop" if row.get("stop") else "ret"
    t = index[goes(s, row)[0]]
    if kind in ("Jmp", "JT"):
        return {"jump": t}
    if kind in ("Call", "CT"):
        # a call names where it comes back to, which is a state and not an
        # address: the transpose and the record it returns under are the
        # callee's.  A call the horizon never returns from names none
        back = seq.ret.get(s)
        op = {"call": t}
        if back is not None:
            op["ret"] = index[back]
        return op
    if kind == "For":
        return {"mark": row["count"], "next": t}
    if kind == "Transp":
        return {"jump": t}
    return {"loop": True, "next": t}


def events(blk, commands):
    """One block's rows as section 3.6 events, its loads spent by section 6."""
    out = []
    for _s, row in blk["rows"]:
        kind = row.get("kind")
        if kind in DROPPED:  # the record they build is the instrument, and it is
            continue
        if kind == "DMoke":
            name = poke_name(row["off"], row["val"])
            commands.setdefault(name, poke_command(row["off"], row["val"]))
            out.append(blank(arm=name))
            continue
        e = blank(dur=row["dur"], sounds=row["sounds"])
        if row["sounds"]:
            e["ins"] = row["ins"]
            e["note"] = row["note"]
        out.append(e)
    return out


def blank(dur=0, sounds=False, arm=None):
    return {
        "dur": dur,
        "sounds": sounds,
        "tie": False,
        "gate": None,
        "note": None,
        "ins": None,
        "arm": arm,
    }


def poke_name(off, val):
    """A command named by what it does, never by the offset the score gives it."""
    if off in HALF:
        cell, half = HALF[off]
        return "%s.%s:%02X" % (cell, "hi" if half else "lo", val)
    if off not in DCELL:
        raise Refused("DMoke $%02X names no cell of the engine" % off)
    return "%s:%02X" % (DCELL[off], val)


def poke_command(off, val):
    """One ``sets`` on one engine cell -- or on one half of a 16-bit one."""
    if off in HALF:
        cell, half = HALF[off]
        keep = {"and": [{"cell": cell}, 0x00FF if half else 0xFF00]}
        return {"rows": [{"sets": [["@" + cell, {"or": [keep, val << 8 if half else val]}]]}]}
    return {"rows": [{"sets": [["@" + DCELL[off], {"const": val}]]}]}


def C(n):
    return {"cell": n}


def K(n, k):
    return {"and": [C(n), k]}


def dec(n):
    return ["@" + n, {"sub": [C(n), 1]}]


def I(n):
    return {"ins": n}


def instruments(images, silence):
    """The S records the horizon copies, interned: one ``Ins`` per distinct image.

    A ``Moke``, an ``FLoad`` and the three ``load`` commands all write this
    record and never the chip, so section 6 spends them here -- the instrument
    *is* what the score built by the time a note copied it.  Every record
    carries the same accumulators because the engine is the voice's and not the
    instrument's: what an instrument does is decide the cells it starts from.
    """
    out = {}
    for i, s in enumerate(images):
        wave = s[0x18]
        out[str(i)] = {
            "adsr": [s[0x19], s[0x1A]],
            "sr": s[0x1A],
            "ad": s[0x19],
            "wave": wave,
            "wave_test": wave | 8,  # the TEST-bit pulse two of the three copies make
            "wave_gate": wave & 0xF7,  # bit 3 is the player's own flag and never the chip's
            "pw": [s[0x16], s[0x17]],
            "arp": int(bool(s[0x0D] & 8)),
            "vadsc": s[0x1B],
            "vrc": s[0x1C],
            "pmc": s[0x11],
            "pmd0": s[0x0E],
            "pmd1": s[0x0F],
            "pmdly": s[0x10],
            "pmg0": s[0x12] | s[0x13] << 8,
            "pmg1": s[0x14] | s[0x15] << 8,
            "pinit": s[0x16] | s[0x17] << 8,
            "fmd": list(s[8:0x0C]),
            "fmdly": s[0x0C],
            "fmc": s[0x0D],
            "g": list(s[0:8]),
            "on_note": [{"point": [["arp", s[0x0C] + 1, False]]}] if s[0x0D] & 8 else [],
            "accs": ENGINE,
            # section 3.5's sound with no pitch at all: note $5E keys the
            # instrument and takes the tuning's own entry for it, read and not assumed
            "pitch": {"value": {"const": silence}},
        }
    return out


def note_on():
    """The note-on, as the five acts the tune makes of it.

    The register writes run ``SR AD [wave|8] wave pw_hi pw_lo``, and the bracket
    is the family's one asymmetry: voice 0 and voice 2 send ``wave|8`` to their
    own ``ctrl`` and voice 1 sends it to its own ``pw_lo``, because that copy's
    store names the voice's base where the other two name base+2.  The cell
    ``testpulse`` is that fact as data.
    """
    return {
        "rows": [
            {"sets": [["sr", I("sr")], ["ad", I("ad")]]},
            {"sets": [["ctrl", I("wave_test")]], "when": [[C("testpulse"), "!=", 0]]},
            {"sets": [["pw_lo", I("wave_test")]], "when": [[C("testpulse"), "==", 0]]},
            {"sets": [["ctrl", I("wave_gate")]]},
            {"sets": [["pw_hi", I("pw.1")], ["pw_lo", I("pw.0")]]},
            {
                "sets": [
                    ["@vwfg", I("wave")],
                    ["@vfreq", {"notefreq": None}],
                    ["pitch", {"notefreq": None}],
                    ["@pmc", I("pmc")],
                ]
            },
            {  # the pulse block, copied only where the record has one
                "when": [[I("pmc"), "!=", 0]],
                "sets": [
                    ["@pmd0", I("pmd0")],
                    ["@pmd1", I("pmd1")],
                    ["@pmdly", I("pmdly")],
                    ["@pmg0", I("pmg0")],
                    ["@pmg1", I("pmg1")],
                    ["@pinit", I("pinit")],
                    ["@pcurr", I("pinit")],
                    ["@pmd0c", I("pmd0")],
                    ["@pmd1c", I("pmd1")],
                ],
            },
            {  # the frequency block: fourteen bytes, whatever the record says
                "sets": [["@g%d" % k, I("g.%d" % k)] for k in range(8)]
                + [["@fmd%d" % k, I("fmd.%d" % k)] for k in range(4)]
                + [["@fmdly", I("fmdly")], ["@fmc", I("fmc")]]
            },
            {"when": [[I("arp"), "!=", 0]], "sets": [["@fmd2", C("note")]]},
            {
                "when": [[I("arp"), "==", 0]],
                "sets": [["@fcurr", C("vfreq")]]
                + [["@fmd%dc" % k, C("fmd%d" % k)] for k in range(4)],
            },
            {"sets": [["@vadsc", I("vadsc")], ["@vrc", I("vrc")]]},
        ]
    }


def gate():
    """The gate and release timers, and the hard kill that frees the voice.

    Two modes and one datum chooses: the instrument's wave byte carries bit 3,
    which never reaches the chip, and says whether the release is counted from
    the note (``vadsc`` frames, absolute) or from the *end of the step* (fewer
    than ``vadsc`` frames left on the row clock).  The flags are the two edges
    the source reads off its own ``DEC``: a counter that reached zero this tick
    is not one that was zero already.
    """
    on = [C("vrc"), "!=", 0]
    rel = [K("vwfg", 8), "!=", 0]
    abso = [K("vwfg", 8), "==", 0]
    fresh = [{"flag": "gatedone"}, "==", 0]
    masked = {"and": [C("vwfg"), 0xF6]}
    # the row clock is a byte, so the compare reads one: a raw duration of 0 is a
    # row of 256 frames and the object says 256, which is the length -- the
    # player's own counter holds the 0 its DEC wraps, and this is that read
    clock = {"and": [C("dur"), 0xFF]}
    return {
        "rows": [
            {
                "sets": [
                    ["!gatetick", {"const": 0}],
                    ["!vrctick", {"const": 0}],
                    ["!gatedone", {"const": 0}],
                ]
            },
            {  # the relative release leaves the block: clearing bit 3 of the wave
                # byte is what puts the *next* tick on the absolute path, not this one
                "when": [on, rel, [C("vadsc"), ">", clock]],
                "sets": [
                    ["@vadsc", {"const": 0}],
                    ["ctrl", masked],
                    ["@vwfg", masked],
                    ["!gatedone", {"const": 1}],
                ],
            },
            {
                "when": [on, rel, [C("vadsc"), ">", clock], [C("vwfg"), "==", 0]],
                "sets": [["!dead", {"trap": "a relative gate whose masked wave is zero"}]],
            },
            {
                "when": [on, fresh, abso, [C("vadsc"), "!=", 0]],
                "sets": [dec("vadsc"), ["!gatetick", {"const": 1}]],
            },
            {
                "when": [on, fresh, abso, [{"flag": "gatetick"}, "!=", 0], [C("vadsc"), "==", 0]],
                "sets": [["ctrl", masked]],
            },
            {
                "when": [on, fresh, abso, [{"flag": "gatetick"}, "==", 0], [C("vadsc"), "==", 0]],
                "sets": [dec("vrc"), ["!vrctick", {"const": 1}]],
            },
            {  # the seven registers the release writes, in the order it writes them
                "when": [[{"flag": "vrctick"}, "!=", 0], [C("vrc"), "==", 0]],
                "sets": [
                    ["sr", {"const": 0}],
                    ["ad", {"const": 0}],
                    ["ctrl", {"const": 0}],
                    ["pw_hi", {"const": 0}],
                    ["pw_lo", {"const": 0}],
                    ["pitch", {"const": 0}],
                ],
            },
        ]
    }


def arp():
    """The arpeggio: a pitch stream over the eight offset cells, read backwards.

    The same eight bytes are the four sixteen-bit frequency gradients when bit 3
    of the control byte is clear, so the object keeps them as eight cells and
    each row of this stream names one -- the index is the row and not a read.
    Row 0 is the player's own empty cursor, so offset ``k`` is row ``k + 1``.
    """
    rows = [{"trap": "row 0 is the player's own empty cursor"}]
    for x in range(8):
        rows.append(
            {
                "op": {"pitch": {"and": [{"add": [C("fmd2"), C("g%d" % x)]}, 0xFF]}},
                "next": {"add": [C("fmd3"), 1]} if x == 0 else x,
            }
        )
    return {
        "rank": 30,
        "when": [[C("vrc"), "!=", 0], [K("fmc", 8), "!=", 0]],
        "rows": rows,
    }


PW = [["pw_lo", "lo"], ["pw_hi", "hi"]]
FQ = [["freq_lo", "lo"], ["freq_hi", "hi"]]
BOUND16 = {
    "from": "projected",
    "interval": [0, 0xFFFF],
    "witness": "the two 8-bit stores the ramp leaves in X and Y: the value is the pair",
}


def ramp(rank, cell, target, produce, when, delta=None, gate_sets=()):
    """One arm of a piecewise-linear generator: a bounded accumulator, section 5."""
    a = {
        "rank": rank,
        "cell": cell,
        "target": target,
        "width": 16,
        "policy": "wrap",
        "bound": BOUND16,
        "rate": 1,
        "scope": "voice",
        "when": list(when),
        "produce": list(produce),
    }
    if delta is not None:
        a["delta"] = delta
    if gate_sets:
        a["gate"] = {"true": list(gate_sets)}
    return a


def accs():
    """The two generators as section 5 accumulators: pulse over two segments, frequency over four.

    Each arm is the source's own: a guard saying which segment the tick is in, a
    sixteen-bit gradient read out of the cells the note-on filled, and a ``gate``
    spending that segment's counter.  Two facts of the source's control flow are
    flags rather than guards on the cells, because a cell an earlier arm moved is
    not the cell the arm was chosen on: **an arm that steps ends the generator**
    (the source leaves by ``JMP``, so the next segment does not also run on the
    tick this one spent its counter), and **a reload does not** -- which is the
    ``while`` the source writes as one loop, and why the reload arms rank below
    the segments and above nothing.
    """
    live = [C("vrc"), "!=", 0]

    def arm(tag, rank, cell, target, produce, when, delta=None, gate_sets=()):
        """An arm that steps: it ends its generator, so it raises the flag."""
        return ramp(
            rank,
            cell,
            target,
            produce,
            [[{"flag": tag + "done"}, "==", 0]] + list(when),
            delta=delta,
            gate_sets=list(gate_sets) + [["!" + tag + "done", {"const": 1}]],
        )

    def hold(tag, rank, cell, target, when, gate_sets, reload=None):
        """A reload: it moves counters and lets the segment after it run.

        The counters are bytes and go through the ``gate``; the *value* is
        sixteen bits and goes through ``policy.reload``, which is where a
        sixteen-bit move belongs -- a gate write is an edge and one byte wide.
        """
        a = ramp(
            rank,
            cell,
            target,
            [],
            [[{"flag": tag + "done"}, "==", 0]] + list(when),
            gate_sets=gate_sets,
        )
        if reload is not None:
            a["policy"] = {"reload": reload}
        return a

    pmon = [live, [C("pmc"), "!=", 0]]
    pm = pmon + [[C("pmdly"), "==", 0]]
    pmspent = pm + [[C("pmd%dc" % k), "==", 0] for k in range(2)]
    fmon = [live, [C("fmc"), "!=", 0], [K("fmc", 8), "==", 0], [C("fcurr"), "!=", 0]]
    fm = fmon + [[C("fmdly"), "==", 0]]
    fmspent = fm + [[C("fmd%dc" % k), "==", 0] for k in range(4)]
    out = {
        "pmdelay": arm(
            "pm", 10, "pcurr", "pw", [], pmon + [[C("pmdly"), "!=", 0]], gate_sets=[dec("pmdly")]
        ),
        "pmreload": hold(
            "pm",
            11,
            "pcurr",
            "pw",
            pmspent + [[K("pmc", 0x81), "!=", 0], [K("pmc", 0x80), "==", 0]],
            [["@pmd0c", C("pmd0")], ["@pmd1c", C("pmd1")]],
        ),
        # bit 7 of the control byte reloads the value with the note's own as
        # well as the counters: the loop restarts the whole ramp and not its list
        "pmreload_all": hold(
            "pm",
            11,
            "pcurr",
            "pw",
            pmspent + [[K("pmc", 0x80), "!=", 0]],
            [["@pmd0c", C("pmd0")], ["@pmd1c", C("pmd1")]],
            reload=C("pinit"),
        ),
        "pmnoop": arm("pm", 14, "pcurr", "pw", PW, pmspent + [[K("pmc", 0x81), "==", 0]]),
        "fmdelay": arm(
            "fm",
            20,
            "fcurr",
            "freq",
            [],
            fmon + [[C("fmdly"), "!=", 0], [K("fmc", 2), "==", 0]],
            gate_sets=[dec("fmdly")],
        ),
        "fmbend": arm(
            "fm",
            20,
            "fcurr",
            "freq",
            FQ,
            fmon + [[C("fmdly"), "!=", 0], [K("fmc", 2), "!=", 0]],
            delta={"u16": [C("g6"), C("g7")]},
            gate_sets=[dec("fmdly")],
        ),
        "fmreload": hold(
            "fm",
            21,
            "fcurr",
            "freq",
            fmspent + [[K("fmc", 0x81), "!=", 0], [K("fmc", 0x80), "==", 0]],
            [["@fmd%dc" % k, C("fmd%d" % k)] for k in range(4)],
        ),
        "fmreload_all": hold(
            "fm",
            21,
            "fcurr",
            "freq",
            fmspent + [[K("fmc", 0x80), "!=", 0]],
            [["@fmd%dc" % k, C("fmd%d" % k)] for k in range(4)],
            reload=C("vfreq"),
        ),
        "fmnoop": arm("fm", 26, "fcurr", "freq", FQ, fmspent + [[K("fmc", 0x81), "==", 0]]),
    }
    for k in range(2):
        out["pm%d" % k] = arm(
            "pm",
            12 + k,
            "pcurr",
            "pw",
            PW,
            pm + [[C("pmd%dc" % j), "==", 0] for j in range(k)] + [[C("pmd%dc" % k), "!=", 0]],
            delta={"field": [C("pmg%d" % k), 0xFFFF]},
            gate_sets=[dec("pmd%dc" % k)],
        )
    for k in range(4):
        out["fm%d" % k] = arm(
            "fm",
            22 + k,
            "fcurr",
            "freq",
            FQ,
            fm + [[C("fmd%dc" % j), "==", 0] for j in range(k)] + [[C("fmd%dc" % k), "!=", 0]],
            delta={"u16": [C("g%d" % (2 * k)), C("g%d" % (2 * k + 1))]},
            gate_sets=[dec("fmd%dc" % k)],
        )
    return out


ENGINE = [
    {"acc": k}
    for k in (
        "pmdelay",
        "pmreload",
        "pmreload_all",
        "pm0",
        "pm1",
        "pmnoop",
        "fmdelay",
        "fmbend",
        "fmreload",
        "fmreload_all",
        "fm0",
        "fm1",
        "fm2",
        "fm3",
        "fmnoop",
    )
]


def word_at(m, n):
    """One entry of the tuning, out of the two byte tables the player reads."""
    return m[HIFRQ + n] << 8 | m[LOFRQ + n]


def pitch(m, notes):
    """The tuning as the player reads it: two byte tables, one u16 row per note."""
    lo, hi = min(notes), max(notes)
    return {
        "base": lo,
        "tuning": "12-TET, NTSC 1 MHz; entry $5E is 0, the silence note",
        "resolution": "semitone",
        "freq": [word_at(m, n) for n in range(lo, hi + 1)],
    }


def cells(m):
    """The engine's own state, read off the post-init image and never zeroed.

    ``init`` writes the chip and four zero-page bytes per voice and leaves the S,
    D and duration records exactly as the file loaded them, so the residue *is*
    the initial state -- inert only because ``vrc`` is zero, which one ``DMoke``
    in the main theme's intro undoes over the residual record.
    """
    out = {"dur": [m[ZCLOCK + v] for v in range(3)], "testpulse": [1, 0, 1]}
    for off, name in sorted(DCELL.items()):
        out[name] = [m[DREC[v] + off] for v in range(3)]
    for name, off in (
        ("pmg0", 0x12),
        ("pmg1", 0x14),
        ("pinit", 0x16),
        ("vfreq", 0x18),
        ("fcurr", 0x1D),
        ("pcurr", 0x23),
    ):
        out[name] = [word(m, DREC[v] + off) for v in range(3)]
    return out


def score(seq, m):
    """The three byte programs as section 3.6 orders, patterns and commands."""
    blks, index = blocks(seq)
    patterns, commands = {}, {}
    plays = [[] for _ in range(3)]
    for i, b in enumerate(blks):
        patterns[str(i)] = {"events": events(b, commands)}
        plays[b["at"][0]].append(
            {"pattern": str(i), "op": b["op"], "transpose": signed8(b["at"][2])}
        )
    orders = [{"play": plays[v], "end": "stop"} for v in range(3)]
    return blks, index, patterns, orders, commands, seq.images


def signed8(x):
    return x - 256 if x & 0x80 else x


def build(path, song=1, ticks=TICKS):
    """The trackerprog object for one subtune, and the oracle it renders against."""
    post, writes = run(path, song, ticks)
    m = bytearray(post)
    seq = Sequencer(m)
    for _ in range(ticks):
        seq.tick()
    _blks, _index, patterns, orders, commands, images = score(seq, m)
    notes = {r["idx"] for r in seq.rows.values() if r.get("sounds")} | {SILENT}
    obj = {
        "$trackerprog": 1,
        "meta": {
            "tune": Path(path).name,
            "family": "Galway (Comic Bakery)",
            "song": song - 1,  # meta.song is the subtune index; --song is the certificate's
            "cycles_per_tick": CYCLES,
            "voices": 3,
            "voice_order": [2, 1, 0],
            "wide": list(WIDE),
            # one act's edges: the note-on sends SR, AD, then the two control bytes
            "commit_order": ["sr", "ad", "ctrl"],
            "tempo": {
                "cell": "dur",
                "step": -1,
                "rate": 1,
                "phase": 0,
                "boundary": [[C("dur"), "==", 0]],
            },
            "tick": ["row", "commit", {"stream": "gate"}, "machine"],
            "row_consumes_tick": False,
            "row_ends_fetch": [["dur", "!=", 0]],
            "row_command": "spent",
            # what the score's own stop stops: this family ends a voice's
            # *sequencer* -- the eight-deep stack runs out and the run bit
            # clears -- and its engine plays the note out and frees the chip
            "stop": "sequencer",
            "row": [
                {"commands": True},
                {"ins": True},
                {"note": True, "when": [["sounds", "!=", 0]]},
                {"stream": "note_on", "when": [["sounds", "!=", 0]]},
            ],
        },
        "globals": {
            "commit": [
                [24, {"const": m[VOL] | m[FILTSH + 3]}],
                [21, {"const": m[FILTSH]}],
                [22, {"const": m[FILTSH + 1]}],
                [23, {"const": m[FILTSH + 2]}],
            ],
            "flags": {
                "pmdone": {"default": {"const": 0}},
                "fmdone": {"default": {"const": 0}},
            },
            "stop_writes": [],
        },
        "pitch": pitch(m, notes),
        "streams": {"note_on": note_on(), "gate": gate(), "arp": arp()},
        "accs": accs(),
        "instruments": instruments(images, word_at(m, SILENT)),
        "score": {"patterns": patterns, "orders": orders, "commands": commands},
        "state0": {
            "ins": seq.ins0,
            "stopped": [not r for r in seq.runs],
            "cells": cells(m),
            "cursors": {"arp": [{"row": m[DREC[v] + 0x0C] + 1, "hold": 0} for v in range(3)]},
            "globals": {},
        },
    }
    return obj, writes


def claim(path, song):
    """What the source tuneprog's certificate claims for this subtune."""
    d = Path(path).read_bytes()
    for s in json.loads(d)["subtunes"]:
        if s["song"] == song:
            return s["ticks"], hashlib.sha256(d).hexdigest()[:16]
    raise SystemExit("no subtune %d in %s" % (song, path))


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def counts(obj):
    ev = sum(len(p["events"]) for p in obj["score"]["patterns"].values())
    return "instruments %d  blocks %d  rows %d  tuning %d  commands %d" % (
        len(obj["instruments"]),
        len(obj["score"]["patterns"]),
        ev,
        len(obj["pitch"]["freq"]),
        len(obj["score"]["commands"]),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("sid")
    ap.add_argument("--song", type=int, default=1, help="1-based subtune (default 1)")
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--out", default=None, help="directory for trackerprog.json")
    ap.add_argument("--source", default=None, help="the source tuneprog's certificate.json")
    ap.add_argument("--certify", action="store_true", help="compare with the PcodeVM")
    a = ap.parse_args(argv)
    ticks, source = TICKS, None
    if a.source:
        ticks, source = claim(a.source, a.song)
    obj, writes = build(a.sid, a.song, a.ticks or ticks)
    if a.out:
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "trackerprog.json").write_text(json.dumps(obj, indent=1))
        text = printer.render(obj)
        (d / "trackerprog.md").write_text(text)
        print("print: " + "  ".join("%s %s" % kv for kv in printer.numbers(text).items()))
    print(counts(obj))
    if a.certify:
        c = attest(obj, writes)
        c["source"] = {
            "tune": obj["meta"]["tune"],
            "song": a.song,
            "oracle": "deity_informant.PcodeVM",
            "certificate_digest": source,
            "rendered_from": digest(obj),
        }
        c["loop"] = None
        c["end"] = {"tick": c["ticks"] - 1, "kind": "horizon"}
        print(json.dumps({k: v for k, v in c.items() if k != "dropped"}, indent=1))
        if a.out:
            (Path(a.out) / "trackerprog.certificate.json").write_text(json.dumps(c, indent=1))
        return 0 if c["divergence"] is None else 1
    render(obj, a.ticks or ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
