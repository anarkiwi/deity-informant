#!/usr/bin/env python3
"""defMON as a trackerprog, transliterated by hand.

Not a lift, a reading: docs/prototype-automatas.md and playroutine-anatomy.md
section 2 restated in the trackerprog's vocabulary and rendered by the universal
player.  docs/prototype-defmon-trackerprog.md is the mapping.
"""

import argparse
import hashlib
import json
import pickle
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.lifter import lift  # noqa: E402
from deity_informant.trackerprog import printer  # noqa: E402
from deity_informant.trackerprog.attest import COMPARED, DROPPED  # noqa: E402
from deity_informant.trackerprog.universal import Player, render  # noqa: E402
from deity_informant.tuneprog import grid  # noqa: E402
from deity_informant.vm import PcodeVM, run_sub  # noqa: E402

# One signature per datum, over the image the tick sees: the operand of the
# instruction that reads or writes it.  The player's code moves between builds,
# so nothing here is an address.
SIGS = {
    "wr_pw": (["A2 .. A9 .. 8E 02 D4 8D 03 D4"], {"pw_lo": ("c", 1), "pw_hi": ("c", 3)}),
    "wr_freq": (["A2 .. A9 .. 8E 00 D4 8D 01 D4"], {"freq_lo": ("c", 1), "freq_hi": ("c", 3)}),
    "wr_edge": (
        ["A2 .. A0 .. A9 .. 49 .. 8E 06 D4 8C 05 D4 8D 04 D4"],
        {"sr": ("c", 1), "ad": ("c", 3), "ctrl": ("c", 5), "ctrl_eor": ("c", 7)},
    ),
    "wr_glob": (
        ["A9 .. 8D 17 D4 A9 .. 09 0F 8D 18 D4"],
        {"res_route": ("c", 1), "mode_vol": ("c", 6)},
    ),
    "filter": (
        [
            "A9 .. 18 69 .. 8D .. .. A9 .. 69 .. 10 03 AD .. .. 8D .. .. 69 .. 30 04"
            " C9 .. B0 03 AD .. .. .. 8D 16 D4"
        ],
        {
            "flt_acc_lo": ("c", 1),
            "flt_dir": ("c", 3),
            "flt_step_lo": ("c", 4),
            "flt_acc_hi": ("c", 9),
            "flt_step_hi": ("c", 11),
            "flt_base": ("c", 21),
            "flt_floor": ("i", 25),
            "flt_shift": ("i", 31),
        },
    ),
    "advance": (
        ["A9 .. 10 .. 29 0F 8D .. .. 8D .. .. 8D .. .. 8D .. .. A0 .. BE .. .."],
        {
            "rowflag": ("c", 1),
            "timer": ("w", 10),
            "timer_1": ("w", 13),
            "timer_2": ("w", 16),
            "arranger": ("c", 19),
            "col0": ("w", 21),
        },
    ),
    "arranger": (
        [
            "BD .. .. 8D .. .. BD .. .. 8D .. .. BE .. .. BD .. .. 8D .. .. BD .. .. 8D .. .."
            " BE .. .. BD .. .. 8D .. .. BD .. .. 8D .. .."
        ],
        {
            "patlo": ("w", 1),
            "ptr_lo": ("w", 4),
            "pathi": ("w", 7),
            "ptr_hi": ("w", 10),
            "col1": ("w", 13),
            "col2": ("w", 28),
        },
    ),
    "patrow": (
        [
            "A0 01 A9 .. 10 .. B9 .. .. 8D .. .. 8E .. .. C8 A9 .. 10 .. B9 .. .. 8D .. .."
            " 8E .. .. C8 A9 .. 10 .. B9 .. .. 8D .. .. 8D .. .. 8E .. .. 8E .. .. 8E .. .."
        ],
        {
            "has_a": ("c", 3),
            "cur_a": ("w", 10),
            "tim_a": ("w", 13),
            "has_b": ("c", 17),
            "cur_b": ("w", 24),
            "tim_b": ("w", 27),
            "has_note": ("c", 31),
            "notebase": ("w", 38),
            "freq_idx": ("w", 41),
            "acc_lo": ("w", 44),
            "acc_hi": ("w", 47),
            "osc": ("w", 50),
        },
    ),
    "cascade": (
        [
            "A9 .. F0 .. 30 .. CE .. .. 4C .. .. .. .. .. A0 .. B9 .. .. D0 .. B9 .. .. A8"
            " B9 .. .. 85 .. B9 .. .. 8D .. .. B9 .. .. C8 8C .. .. A2 .. 20 .. .."
        ],
        {"rowhi": ("w", 18), "rowlo": ("w", 23), "delay": ("w", 32)},
    ),
    "osc_slide": (
        [
            "BD .. .. F9 .. .. 9D .. .. BD .. .. F9 .. .. 9D .. .. 4C .. .. B9 .. .. 18"
            " 7D .. .. 9D .. .. B9 .. .. 7D .. .."
        ],
        {"sub_lo": ("w", 4), "sub_hi": ("w", 13), "add_lo": ("w", 22), "add_hi": ("w", 32)},
    ),
    "osc_plain": (
        ["BC .. .. B9 .. .. 18 7D .. .. 9D .. .. B9 .. .. 9D .. .. 4C"],
        {"pitch_lo": ("w", 4), "voice_no": ("w", 8), "pitch_hi": ("w", 14)},
    ),
    "osc_interval": (
        ["18 7D .. .. A8 B9 .. .. 38 F9 .. .. 8D .. .. B9 .. .. F9 .. .. 8D .. .."],
        {"iv_hi": ("w", 6), "iv_lo": ("w", 10)},
    ),
    "osc_pw": (
        ["BC .. .. F0 .. 10 .. 98 2B 7F 7D .. .. 9D .. .. 90 .. BD .. .. C9 0F F0 .. FE .. .."],
        {"pwstep": ("w", 1)},
    ),
    "row_head": (
        [
            "85 .. A0 00 B1 .. F0 .. 0A 85 .. 10 .. C8 B1 .. 9D .. .. 90 .. C8 B1 .. 9D .. .."
            " 24 .. 50 .. C8 B1 .. 9D .. .. A5 .. 29 20 F0 .. C8 B1 .. 9D .. .."
        ],
        {"r_ctrl": ("w", 17), "r_ctrl_eor": ("w", 25), "r_ad": ("w", 35), "r_sr": ("w", 47)},
    ),
    "row_note": (
        [
            "A5 .. 29 10 F0 .. C8 B1 .. 18 7D .. .. 9D .. ..",
            "A5 .. 29 10 F0 .. C8 B1 .. 30 .. 18 7D .. .. 29 7F 9D .. ..",
        ],
        [
            {"r_notebase": ("w", 11), "r_freq_idx": ("w", 14)},
            {"r_notebase": ("w", 13), "r_freq_idx": ("w", 18)},
        ],
    ),
    "row_osc": (
        ["A5 .. 29 08 F0 .. C8 B1 .. 9D .. .. A5 .. 29 04 F0 .. C8 B1 .. 9D .. .. 29 F0 9D .. .."],
        {"r_osc": ("w", 10), "r_pw_hi": ("w", 22), "r_pw_lo": ("w", 27)},
    ),
    "row_tail": (
        [
            "C8 B1 .. F0 .. 0A 85 .. 90 .. C8 B1 .. 9D .. .. A5 .. F0 .. 29 80 F0 .. C8 B1 .."
            " F0 .. 29 08 D0 .. B1 .. 4C .. .. AD .. .. 3D .. .. 4C .. .. AD .. .. 29 0F"
            " 11 .. 1D .. .. 8D .. .. A7 .. F0 .. 29 40 F0 .. C8 B1 .. 8D .. .. 8A 29 20"
            " F0 .. C8 B1 .. 8D .. .. 8A 29 10 F0 .. C8 B3 .. C8 B1 .. 30 .. 8E .. .. 8D .. .."
        ],
        {
            "r_pwstep": ("w", 14),
            "r_mask": ("w", 42),
            "r_bit": ("w", 55),
            "r_mode_vol": ("w", 72),
            "r_flt_base": ("w", 83),
        },
    ),
}

COPIES = {"cascade": 6, "patrow": 3}  # the routines the player wrote out per copy
VOICES, STRIDE = 3, 0x31  # three voices; the player's own record stride
CASCADES = 6  # two sidTAB programs a voice, the A set then the B set
WIDE = ("acc",)  # the voice cells that are 16 bits
CELLS = ("osc", "pwstep", "ctrl", "ctrl_eor", "freq_idx", "note")
DEAD = {
    "res_route.literal": "no sidTAB row of either tune writes the routing byte outright",
    "cascade.chain": "no sidTAB jump of either tune lands on another jump",
    "osc.interval": "no oscillator of either tune detunes a note by an interval of the tuning",
    "note.absolute": "no sidTAB row of the build that has the column takes a note outright",
}
# the image the tick writes out, in the order the tune's own write-out runs
FLUSH = [7 * v + r for v in range(3) for r in (2, 3, 0, 1, 6, 5, 4)] + [23, 24]
OSC = {"cell": "osc"}
PWS = {"cell": "pwstep"}
FI = {"cell": "freq_idx"}
PW = {"cell": "shadow.pw"}


def word(m, a):
    return m[a] | m[a + 1] << 8


def load(path):
    """The tune's load band, PSID header stripped, with the band's own extent."""
    d = Path(path).read_bytes()
    off, org = struct.unpack(">H", d[6:8])[0], struct.unpack(">H", d[8:10])[0]
    body = d[off:]
    if org == 0:
        org, body = body[0] | body[1] << 8, body[2:]
    m = bytearray(0x10000)
    m[org : org + len(body)] = body
    return m, org, org + len(body)


def entries(path):
    d = Path(path).read_bytes()
    return struct.unpack(">H", d[10:12])[0], struct.unpack(">H", d[12:14])[0]


def image(path, song=0):
    """The band as the tick sees it: the tune's own init has run."""
    m, lo, hi = load(path)
    vm = PcodeVM(m)
    vm.reg[0] = song
    run_sub(vm, entries(path)[0], {}, lift)
    return bytearray(vm.mem), lo, hi


def sites(m, lo, hi, pat):
    """Every offset where a wildcarded opcode pattern holds."""
    want = [None if b == ".." else int(b, 16) for b in pat.split()]
    return [
        i
        for i in range(lo, hi - len(want))
        if all(w is None or m[i + j] == w for j, w in enumerate(want))
    ]


def layout(m, lo, hi):
    """Every base and cell, each from the instruction that reads it."""
    out = {}
    for name, (pats, fields) in SIGS.items():
        hits = [
            (k, a) for k, a in ((k, sorted(sites(m, lo, hi, p))) for k, p in enumerate(pats)) if a
        ]
        assert len(hits) == 1, "%s: %d alternatives match" % (name, len(hits))
        k, at = hits[0]
        out[name] = k  # which shape a build has is itself a datum
        copies = COPIES.get(name, 1)  # a routine the player unrolled once per copy
        assert len(at) == copies, "%s: %d sites, not %d" % (name, len(at), copies)
        if name == "cascade":
            out["cascade_at"] = at
        for f, (kind, off) in (fields[k] if isinstance(fields, list) else fields).items():
            i = at[0] + off
            out[f] = (m[i], i, word(m, i))[{"i": 0, "c": 1, "w": 2}[kind]]
    base = out["cascade_at"][0]
    out["cas_timer"] = [base + 1 + STRIDE * k for k in range(CASCADES)]
    out["cas_cursor"] = [base + 16 + STRIDE * k for k in range(CASCADES)]
    out["cas_voice"] = [m[base + 45 + STRIDE * k] // STRIDE for k in range(CASCADES)]
    assert out["cas_voice"] == [0, 1, 2, 0, 1, 2], "the six cascades are two sets of three"
    assert out["timer_1"] - out["timer"] == out["timer_2"] - out["timer_1"], "one voice stride"
    out["vstride"] = out["timer_1"] - out["timer"]
    out["notes"] = out["pitch_hi"] - out["pitch_lo"]  # the tuning's own length
    out["base"] = out["add_lo"] - out["pitch_lo"]  # where the slide table starts, in notes
    assert out["sub_lo"] - out["pitch_lo"] == out["base"] - 0x80, "one table, two windows"
    assert out["iv_lo"] + 1 == out["iv_hi"], "the interval is a difference of neighbours"
    assert out["pitch_hi"] - out["pitch_lo"] == out["add_hi"] - out["add_lo"], "one tuning"
    return out


class Tune:
    """One defMON tune's data, read through its own player's operands."""

    def __init__(self, path, song=0, cycles=None, ticks=None):
        self.path, self.song = path, song
        self.m, lo, hi = image(path, song)
        self.L = layout(self.m, lo, hi)
        self.cycles, self.ticks = cycles, ticks
        self.rate = 1 if cycles is None else round(19656 / cycles) or 1
        self.cmds = {}
        self.act, self.jump, self.rows = {}, {}, [{"trap": "no cascade runs here"}]
        self.notes, self.offs, self.oscs = set(), set(), set()  # what the tuning must reach

    # ---- the tuning -----------------------------------------------------------
    def pitch(self):
        """One frequency table, read through two windows: the notes and the slide.

        Materialised over the run the tune's own reads reach, which is wider than
        the table is stored: a read past its declared size extends the tuning with
        the values read (section 3.2), and a read below it is the slide's window.
        ``note_count`` is where the stored table ends and the extension begins.
        """
        m, L = self.m, self.L
        lo, hi = self.span()
        return {
            "base": lo,
            "tuning": "12-TET",
            "note_count": L["notes"],
            "freq": [m[L["pitch_lo"] + n] | m[L["pitch_hi"] + n] << 8 for n in range(lo, hi + 1)],
        }

    def span(self):
        """The lowest and highest entry of the tuning the tune's own reads reach."""
        L = self.L
        idx = set(self.notes) | {(n + o) & 0xFF for n in self.notes for o in self.offs}
        idx |= {self.m[L["freq_idx"] + STRIDE * v] for v in range(VOICES)}
        top = max(idx)
        for o in self.oscs:
            assert not 0 < o < 0x80, DEAD["osc.interval"]
            if o:  # the slide window, twice the step below the tuning
                top = max(top, 2 * (o & 0x3F) + L["base"])
        return L["base"], top

    # ---- the sidTAB: the one stream form --------------------------------------
    def isjump(self, i):
        return self.m[self.L["rowhi"] + i] == 0

    def delay(self, i):
        return self.m[self.L["delay"] + i]

    def addr(self, i):
        return self.m[self.L["rowlo"] + i] | self.m[self.L["rowhi"] + i] << 8

    def enter(self, i):
        """Where a sidcall byte really lands: a jump row is resolved on arrival."""
        if not self.isjump(i):
            return i
        t = self.m[self.L["rowlo"] + i]
        assert not self.isjump(t), DEAD["cascade.chain"]
        return t

    def reach(self, entries_):
        """Every sidTAB row a cascade can reach, and the jump rows between them."""
        work, seen = [self.enter(i) for i in entries_], set()
        while work:
            i = work.pop()
            if i in seen:
                continue
            seen.add(i)
            if self.delay(i) & 0x80:  # the delay's own bit 7 halts the cascade
                continue
            k = (i + 1) & 0xFF
            if self.isjump(k):
                self.jump[k] = None
                work.append(self.enter(k))
            else:
                work.append(k)
        return sorted(seen)

    def build_rows(self, entries_):
        """Two object rows a sidTAB row: what it does, and how long it then holds."""
        acts = self.reach(entries_)
        for i in acts:
            self.act[i] = len(self.rows)
            self.rows.append(None)
            if self.delay(i) & 0x7F:  # the row's own hold, whether it halts after or not
                self.rows.append(None)
        for k in sorted(self.jump):
            self.jump[k] = len(self.rows)
            self.rows.append(None)
        for i in acts:
            r, d = self.act[i], self.delay(i)
            end = 0 if d & 0x80 else self.step(i + 1)  # the delay's bit 7 is the terminator
            self.rows[r] = dict(self.record(i), hold=1, next=r + 1 if d & 0x7F else end)
            if d & 0x7F:
                self.rows[r + 1] = {"hold": d & 0x7F, "next": end}
        for k, r in self.jump.items():
            self.rows[r] = {"jump": self.act[self.enter(k)]}

    def step(self, k):
        """The object row the cursor lands on after a sidTAB row: a row or a jump."""
        k &= 0xFF
        return self.jump[k] if k in self.jump else self.act[k]

    def record(self, i):  # noqa: C901 - one clause per column of the record
        """One sidTAB row: a variable-length record of register columns."""
        m, p, sets = self.m, self.addr(i), []
        m0, y = m[p], 0

        def take():
            nonlocal y
            y += 1
            return m[p + y]

        ctrl = None
        if m0 & 0x40:
            ctrl = ["@ctrl", take()]
        if m0 & 0x80:
            sets.append(["@ctrl_eor", take()])
        if ctrl:
            sets.insert(0, ctrl)
        if ctrl or m0 & 0x80:
            sets.append(["ctrl", {"xor": [{"cell": "ctrl"}, {"cell": "ctrl_eor"}]}])
        if m0 & 0x20:
            sets.append(["ad", take()])
        if m0 & 0x10:
            sets.append(["sr", take()])
        if m0 & 0x08:
            sets += self.offset(take())
        if m0 & 0x04:
            b = take()
            self.oscs.add(b)
            sets.append(["@osc", b])
        if m0 & 0x02:
            b = take()
            assert b < 0x10, "a pulse width the chip's own nibble cannot hold"
            sets += [["shadow.pw.hi", b], ["shadow.pw.lo", b & 0xF0]]
        m1 = take()
        if m1:
            if m1 & 0x80:
                sets.append(["@pwstep", take()])
            if (m1 << 1) & 0xFF:
                if m1 & 0x40:
                    sets += self.route(take())
                if m1 & 0x20:
                    sets.append(["#mode_vol", take()])
                if m1 & 0x10:
                    sets.append(["#flt_base", take()])
                if m1 & 0x08:
                    sets += self.filter_set(take(), take())
        return {"sets": sets} if sets else {}

    def offset(self, b):
        """The note column of a sidTAB row: an offset from the row's own note.

        One build masks the sum into seven bits and takes a byte with bit 7 set
        as an absolute note instead; the other adds and keeps eight.
        """
        self.offs.add(b)
        if self.L["row_note"] == 0:
            return [["@freq_idx", {"field": [{"add": [b, {"cell": "note"}]}, 0xFF]}]]
        if b & 0x80:
            return [["!dead", {"trap": DEAD["note.absolute"]}]]
        return [["@freq_idx", {"field": [{"add": [b, {"cell": "note"}]}, 0x7F]}]]

    def route(self, b):
        """The routing byte: this voice's bit cleared, or set beside a resonance."""
        rr = {"global": "res_route"}
        mask = {"tabcell": ["voice_bit", {"cell": "voice_index"}, "mask"]}
        bit = {"tabcell": ["voice_bit", {"cell": "voice_index"}, "value"]}
        if b == 0:
            return [["#res_route", {"and": [rr, mask]}]]
        if b & 8:
            return [["#res_route", {"or": [{"or": [{"and": [rr, 0x0F]}, b]}, bit]}]]
        return [["!dead", {"trap": DEAD["res_route.literal"]}]]

    @staticmethod
    def filter_set(lo, hi):
        """Two bytes: a signed sweep rate, or the cutoff accumulator itself."""
        if hi & 0x80:
            return [
                ["#flt_step", lo | (hi & 0x3F) << 8],
                ["#flt_dir", 1 if hi & 0x40 else 0],
            ]
        return [["#flt_acc", lo | hi << 8], ["#flt_step", 0]]

    # ---- the score ------------------------------------------------------------
    def patrows(self, at):
        """One pattern, decoded: a flag byte and the columns it says are there."""
        out, seen = [], set()
        while at not in seen:
            seen.add(at)
            f, y = self.m[at], 0
            row = {"flag": f, "a": None, "b": None, "note": None}
            for k, c in ((0x40, "a"), (0x20, "b"), (0x10, "note")):
                if f & k:
                    y += 1
                    row[c] = self.m[at + y]
            out.append(row)
            if f & 0x80:
                return out
            at += y + 1
        raise AssertionError("pattern at $%04X does not end" % at)

    def pattern_at(self, n):
        m, L = self.m, self.L
        return m[L["patlo"] + n] | m[L["pathi"] + n] << 8

    def arranger(self):
        """The order's own rows: three pattern numbers, to the terminator."""
        m, L = self.m, self.L
        cols = (L["col0"], L["col1"], L["col2"])
        rows, y = [], 0
        while not m[cols[0] + y] & 0x80:
            rows.append(tuple(m[c + y] for c in cols))
            y += 1
        return rows, m[cols[1] + y]

    def score(self):
        """Every voice's play steps, materialised: the first pattern to end ends them all."""
        rows, loop = self.arranger()
        pats, plays, frame, entries_ = {}, [[] for _ in range(VOICES)], 1, set()
        horizon = None if self.ticks is None else self.ticks / self.rate
        for step, cols in enumerate(rows):
            decoded = [self.patrows(self.pattern_at(n)) for n in cols]
            cut = min(self.ends(d) for d in decoded)
            held = self.last(decoded, cut) & 0x0F
            for v, d in enumerate(decoded):
                ev = self.events(d, cut, held, entries_)
                key = json.dumps(ev, sort_keys=True)
                pats.setdefault(key, (len(pats), {"events": ev}))
                plays[v].append({"pattern": pats[key][0]})
            frame += cut + held + 2
            if horizon is not None and frame > horizon:
                return (
                    {
                        "orders": [{"play": p, "end": "horizon"} for p in plays],
                        "patterns": {str(i): p for i, p in sorted(pats.values())},
                        "commands": {},
                    },
                    entries_,
                    step + 1,
                )
        return (
            {
                "orders": [{"play": p, "end": {"jump": loop}} for p in plays],
                "patterns": {str(i): p for i, p in sorted(pats.values())},
                "commands": {},
            },
            entries_,
            len(rows),
        )

    @staticmethod
    def ends(decoded):
        """The frame, inside the arranger step, the pattern's own last row applies."""
        return sum(r["flag"] & 0x0F for r in decoded[:-1]) + 2 * (len(decoded) - 1)

    @staticmethod
    def last(decoded, cut):
        """The flag that holds the step's own last row: the last voice to end it.

        Every voice's row runs on the frame the step ends; the one whose own
        pattern ends there leaves the count, and the last such voice leaves it.
        """
        out = None
        for d in decoded:
            at = 0
            for r in d:
                if at == cut and r["flag"] & 0x80:
                    out = r["flag"]
                at += (r["flag"] & 0x0F) + 2
        assert out is not None, "no pattern ends the arranger step"
        return out

    def events(self, decoded, cut, held, entries_):
        """One voice's rows of an arranger step, cut where the step itself ends.

        The step ends on the frame the first pattern's own end row runs, whatever
        the others were in the middle of, so the row a voice is holding then is
        the row it holds until the step's own count runs out.
        """
        out, at, spans = [], 0, []
        for r in decoded:
            if at > cut:
                break
            e = {
                "dur": (r["flag"] & 0x0F) + 1,
                "sounds": r["note"] is not None,
                "tie": False,
                "gate": None,
                "note": r["note"],
                "ins": None,
                "arm": None,
            }
            if r["note"] is not None:
                self.notes.add(r["note"])
            arm = []
            for slot in ("a", "b"):
                if r[slot] is not None:
                    entries_.add(r[slot])
                    arm.append(self.command(slot, r[slot]))
            e["arm"] = arm or None
            out.append(e)
            spans.append(at)
            at += (r["flag"] & 0x0F) + 2
        out[-1]["dur"] = cut + held + 1 - spans[-1]
        assert out[-1]["dur"] >= 1, "a row of the step outlasts the step"
        return out

    def command(self, slot, byte):
        """One row command: the cascade it starts, named by what it starts."""
        key = "cascade.%s:%02X" % (slot, byte)
        self.cmds[key] = {"byte": byte, "slot": slot}
        return key

    def commands(self):
        """Each command's own re-point, once the sidTAB rows have their numbers."""
        return {
            k: {
                "rows": [
                    {"when": [], "point": [["cas" + c["slot"], self.act[self.enter(c["byte"])]]]}
                ]
            }
            for k, c in sorted(self.cmds.items())
        }

    # ---- the voice's own machine ----------------------------------------------
    def streams(self):
        """The two cascades, the oscillator's producers and the filter channel."""
        m, L = self.m, self.L
        return {
            "casa": {"rank": 0, "rows": self.rows},
            "casb": {"rank": 1, "rows": self.rows},
            "voice_bit": {
                "rows": [
                    {"value": m[L["r_bit"] + STRIDE * v], "mask": m[L["r_mask"] + STRIDE * v]}
                    for v in range(VOICES)
                ],
            },
            "pitch_out": {
                "rank": 3,
                "all": True,
                "rows": self.pitch_rows(),
            },
            "filter": {"all": True, "rows": self.filter_rows()},
        }

    def pitch_rows(self):
        """The voice's frequency: its note, its note detuned, or its slide."""
        lo = {"field": [{"tuned": FI}, 0xFF]}
        hi = {"shr": [{"tuned": FI}, 8]}
        plain = {"add": [lo, {"cell": "voice_no"}]}
        slid = {"add": [{"cell": "acc"}, {"tuned": FI}]}
        return [
            {
                "when": [[OSC, "==", 0]],
                "sets": [
                    ["freq_lo", plain],
                    ["freq_hi", hi],
                    ["!C", {"carry_out": [plain, 8]}],
                ],
            },
            {
                "when": [[OSC, "!=", 0], [OSC, "<", 0x80]],
                # the arm the horizon never takes: what it would add is `det`
                "sets": [["freq_lo", {"trap": DEAD["osc.interval"]}]],
            },
            {
                "when": [[OSC, ">=", 0x80]],
                "sets": [
                    ["freq_lo", {"field": [slid, 0xFF]}],
                    ["freq_hi", {"shr": [{"field": [slid, 0xFFFF]}, 8]}],
                    ["!C", {"carry_out": [slid, 16]}],
                ],
            },
        ]

    def filter_rows(self):
        """The global cutoff channel: one accumulator, its floor and its own shift."""
        acc, step = {"global": "flt_acc"}, {"global": "flt_step"}
        up = {"add": [acc, step]}
        down = {"sub": [acc, {"add": [step, 1]}]}
        floor = self.L["flt_floor"]
        cut = {
            "add": [
                {"add": [{"shr": [acc, 8]}, {"global": "flt_base"}]},
                {"global": "flt_carry"},
            ]
        }
        out = [
            {
                "when": [[{"global": "flt_dir"}, "==", 0]],
                "sets": [
                    ["#flt_carry", {"carry_out": [up, 16]}],
                    ["#flt_acc", {"field": [up, 0xFFFF]}],
                ],
            },
            {
                "when": [[{"global": "flt_dir"}, "!=", 0]],
                "sets": [
                    ["#flt_carry", {"borrow_out": [down, 16]}],
                    ["#flt_acc", {"field": [down, 0xFFFF]}],
                ],
            },
            {
                "when": [[{"bit": [acc, 15]}, "!=", 0]],
                "sets": [["#flt_acc", {"or": [{"and": [acc, 0xFF]}, floor << 8]}]],
            },
            {"when": [], "sets": [["#cutoff", {"field": [cut, 0xFF]}]]},
            {
                "when": [[{"bit": [cut, 7]}, "!=", 0]],
                "sets": [["#cutoff", floor]],
            },
            {
                "when": [[{"bit": [cut, 7]}, "==", 0], [{"field": [cut, 0xFF]}, "<", floor]],
                "sets": [["#cutoff", floor]],
            },
        ]
        if self.L["flt_shift"] == 0x0A:  # the build's own ASL, patched by the model probe
            out.append(
                {
                    "when": [],
                    "sets": [
                        [
                            "#cutoff",
                            {
                                "field": [
                                    {"add": [{"global": "cutoff"}, {"global": "cutoff"}]},
                                    0xFF,
                                ]
                            },
                        ]
                    ],
                }
            )
        else:
            assert self.L["flt_shift"] == 0xEA, "the cutoff is shifted once or not at all"
        return out

    def accs(self):
        """Section 5's records: the slide, and the pulse width's own bounce."""
        o63 = {"and": [OSC, 0x3F]}
        slide = {
            "cell": "acc",
            "target": "freq",
            "width": 16,
            "delta": {"tuned": {"sub": [{"add": [o63, o63]}, -self.L["base"]]}},
            "policy": "wrap",
            "rate": 1,
            "scope": "voice",
            "produce": [],
            "rank": 2,
            "bound": {
                "from": "projected",
                "interval": [0, 0xFFFF],
                "witness": "the sixteen-bit store the voice's own frequency reads",
            },
        }
        dn = {"sub": [{"add": [PWS, 1]}, {"flag": "C"}]}
        up = {"field": [PWS, 0x7F]}
        pw = {
            "cell": "shadow.pw",
            "target": "pw",
            "width": 16,
            "policy": "wrap",
            "rate": 1,
            "scope": "voice",
            "produce": [],
            "rank": 4,
            "bound": {
                "from": "projected",
                "interval": [0, 0xFFFF],
                "witness": "the chip's own twelve bits of the pair the sweep walks",
            },
        }
        bounce = {"name": "bounce", "unguarded": 1}
        return {
            "slide_up": dict(slide, phase={"const": 0}),
            "slide_down": dict(slide, phase={"const": 1}),
            "pw_down": dict(
                pw,
                delta=dn,
                phase={"const": 1},
                delta_when=[[PW, ">=", dn]],
                policy={"reload": 1, "when": [[PW, "<", dn]]},
                flag=bounce,
            ),
            "pw_up": dict(
                pw,
                delta=up,
                phase={"const": 0},
                delta_when=[[{"add": [PW, up]}, "<", 0x1000]],
                policy={"reload": 0xFF8, "when": [[{"add": [PW, up]}, ">=", 0x1000]]},
                flag=bounce,
            ),
            "pw_turn": {
                "rank": 5,
                "cell": "pwstep",
                "target": "pw",
                "width": 8,
                "policy": {"reload": {"xor": [PWS, 0x80]}, "when": []},
                "rate": 1,
                "scope": "voice",
                "produce": [],
                "when": [[{"flag": "bounce"}, "!=", 0]],
                "bound": {
                    "from": "proved",
                    "interval": [0, 0xFF],
                    "witness": "the bounce the sweep leaves at either end of the pulse width",
                },
            },
        }

    @staticmethod
    def arms():
        """One arm per value of the oscillator's own selector, and of the sweep's."""
        return [
            {"acc": "slide_up", "when": [[OSC, ">=", 0x80], [{"bit": [OSC, 6]}, "==", 0]]},
            {"acc": "slide_down", "when": [[OSC, ">=", 0x80], [{"bit": [OSC, 6]}, "!=", 0]]},
            {"acc": "pw_down", "when": [[PWS, "!=", 0], [{"bit": [PWS, 7]}, "==", 0]]},
            {"acc": "pw_up", "when": [[{"bit": [PWS, 7]}, "!=", 0]]},
            {"acc": "pw_turn"},
        ]

    # ---- the whole object -----------------------------------------------------
    def build(self):
        """Section 3's seven sections, and the state the tune's init leaves."""
        score, entries_, steps = self.score()
        self.build_rows(sorted(entries_))
        score["commands"] = self.commands()
        return {
            "$trackerprog": 1,
            "meta": self.meta(steps),
            "pitch": self.pitch(),
            "streams": self.streams(),
            "accs": self.accs(),
            "instruments": {
                "0": {
                    "on_note": [
                        {
                            "when": [],
                            "sets": [["@freq_idx", {"cell": "note"}], ["@acc", 0], ["@osc", 0]],
                        }
                    ],
                    "accs": self.arms(),
                }
            },
            "score": score,
            "globals": self.globals(),
            "state0": self.state0(),
        }

    def globals(self):
        """The one global channel: the filter, the routing and the master volume."""
        return {
            "streams": ["filter"],
            "flags": {"C": {"default": 0}, "bounce": {"default": 0}},
            "stop_writes": [],
            "commit": [
                [22, {"global": "cutoff"}],
                [23, {"global": "res_route"}],
                [24, {"or": [{"global": "mode_vol"}, 0x0F]}],
            ],
        }

    def state0(self):
        """What init leaves: the image, the cells, the cursors and the channel."""
        m, L = self.m, self.L
        shadow = [0] * 25
        for v in range(VOICES):
            b = STRIDE * v
            for r, k in (
                (0, "freq_lo"),
                (1, "freq_hi"),
                (2, "pw_lo"),
                (3, "pw_hi"),
                (5, "ad"),
                (6, "sr"),
            ):
                shadow[7 * v + r] = m[L[k] + b]
            shadow[7 * v + 4] = m[L["ctrl"] + b] ^ m[L["ctrl_eor"] + b]
        gl = {
            "res_route": m[L["res_route"]],
            "mode_vol": m[L["mode_vol"]],
            "flt_acc": m[L["flt_acc_lo"]] | m[L["flt_acc_hi"]] << 8,
            "flt_step": m[L["flt_step_lo"]] | m[L["flt_step_hi"]] << 8,
            "flt_dir": 1 if m[L["flt_dir"]] == 0xE9 else 0,
            "flt_base": m[L["flt_base"]],
            "flt_carry": 0,
            "cutoff": 0,
        }
        shadow[23], shadow[24] = gl["res_route"], gl["mode_vol"] | 0x0F
        cells = {
            k: [m[L[k] + STRIDE * v] for v in range(VOICES)] for k in ("osc", "ctrl", "ctrl_eor")
        }
        cells["pwstep"] = [m[L["pwstep"] + STRIDE * v] for v in range(VOICES)]
        cells["freq_idx"] = [m[L["freq_idx"] + STRIDE * v] for v in range(VOICES)]
        cells["note"] = [m[L["notebase"] + STRIDE * v] for v in range(VOICES)]
        cells["acc"] = [
            m[L["acc_lo"] + STRIDE * v] | m[L["acc_hi"] + STRIDE * v] << 8 for v in range(VOICES)
        ]
        cells["voice_no"] = [m[L["voice_no"] + STRIDE * v] for v in range(VOICES)]
        cells["rowsleft"] = [1] * VOICES  # the frame the tune's own init spends
        assert cells["voice_no"] == list(range(VOICES)), "the detune is the voice's own index"
        return {
            "shadow": shadow,
            "cells": cells,
            "ins": [0] * VOICES,
            "globals": gl,
            "cursors": {
                k: [{"row": 0, "hold": 0} for _ in range(VOICES)] for k in ("casa", "casb")
            },
            "gcursors": {},
        }

    def meta(self, steps):
        """Section 3.1, and the data a family's whole tick shape reduces to."""
        return {
            "tune": Path(self.path).name,
            "family": "defMON",
            "song": self.song,
            "cycles_per_tick": self.cycles or 19656,
            "voices": VOICES,
            "voice_order": [0, 1, 2],
            "commit_order": ["sr", "ad", "ctrl"],
            "shadow": {"registers": FLUSH},
            "wide": list(WIDE) + ["flt_acc", "flt_step"],
            "tempo": {
                "cell": "rowsleft",
                "step": -1,
                "rate": self.rate,
                "phase": 0,
                "boundary": [[{"cell": "rowsleft"}, ">=", 0x80]],
            },
            "tick": ["row", "machine"],
            "row_consumes_tick": False,
            "row_command": "spent",
            "row": [{"commands": True}, {"note": True, "when": [["sounds", "!=", 0]]}],
        }


def build(path, song=0, cycles=None, ticks=None):
    """The trackerprog object for one defMON tune."""
    return Tune(path, song, cycles, ticks).build()


def claim(path, song):
    """What the source tuneprog's certificate claims, and the binding to it."""
    d = Path(path).read_bytes()
    c = json.loads(d)
    s = next(x for x in c["subtunes"] if x["song"] == song + 1)
    loop = (
        None if s["period"] is None else {"period": s["period"], "first_repeat": s["first_repeat"]}
    )
    return loop, s["ticks"], s["cycles_per_tick"], hashlib.sha256(d).hexdigest()[:16]


def loop_holds(obj, loop):
    """Re-verify the inherited claim on the render: the horizon replays itself.

    The claim names the call the state repeats *after*, and the write-out emits
    the image the call before it left, so the replay starts one tick later: the
    period after ``first_repeat`` is the period before it, write for write.
    """
    n, p = loop["first_repeat"] + 1, loop["period"]
    w = render(obj, n + p)
    return w[n - p : n] == w[n : n + p]


class Reference:
    """The oracle: the tune's own player on the PcodeVM, one tick at a time."""

    def __init__(self, path, song, cycles):
        self.vm, self.cache = PcodeVM(load(path)[0]), {}
        self.vm.reg[0] = song
        self.play, self.cycles = entries(path)[1], cycles
        run_sub(self.vm, entries(path)[0], self.cache, lift)

    def tick(self):
        self.vm.wlog = []
        run_sub(self.vm, self.play, self.cache, lift)
        self.vm.cycles += self.cycles
        return [(r, v) for _, r, v in self.vm.wlog]

    def __getstate__(self):
        return {"vm": self.vm, "play": self.play, "cycles": self.cycles}

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.cache = {}


def compare(obj, ref, doc, ticks, budget):
    """Section 2's comparison, as far as the budget reaches; True when it finished."""
    player = doc.pop("_player", None) or Player(obj)
    t0 = time.process_time()
    while doc["ticks"] < ticks:
        want = [tuple(x) for x in ref.tick()]
        mine = [tuple(x) for x in player.tick()]
        t = doc["ticks"]
        doc["ticks"] += 1
        doc["writes"] += len(mine)
        if want == mine:
            doc["identical_ticks"] += 1
        elif sorted(want) == sorted(mine):
            doc["permuted_ticks"] += 1
        a, b = grid.reduce_tick(want), grid.reduce_tick(mine)
        if a != b and doc["divergence"] is None:
            doc["divergence"] = {"tick": t, "expected": _fmt(want), "got": _fmt(mine)}
            return True
        if budget and time.process_time() - t0 > budget:
            doc["_player"] = player
            return False
    return True


def _fmt(w):
    return " ".join("%02X=%02X" % rv for rv in w)


def certify(path, song, obj, ticks, cycles, budget, state):
    """Run the comparison to the end, or to the budget, resuming where it left off."""
    if state and Path(state).is_file():
        doc, ref = pickle.loads(Path(state).read_bytes())
    else:
        ref = Reference(path, song, cycles)
        doc = {
            "compared": list(COMPARED),
            "dropped": list(DROPPED),
            "ticks": 0,
            "divergence": None,
            "writes": 0,
            "permuted_ticks": 0,
            "identical_ticks": 0,
        }
    done = compare(obj, ref, doc, ticks, budget)
    if state and not done:
        Path(state).write_bytes(pickle.dumps((doc, ref)))
    doc.pop("_player", None)
    return doc, done


def main(argv=None):  # noqa: C901 - argument plumbing
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("sid")
    ap.add_argument("--song", type=int, default=0)
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--out", default=None, help="directory for trackerprog.json")
    ap.add_argument("--source", default=None, help="the source tuneprog's certificate.json")
    ap.add_argument("--certify", action="store_true", help="compare with the PcodeVM")
    ap.add_argument("--budget", type=float, default=0.0, help="CPU seconds per invocation")
    ap.add_argument("--resume", default=None, help="a file the comparison resumes from")
    a = ap.parse_args(argv)
    loop, ticks, cycles, digest = (None, a.ticks or 1000, None, None)
    if a.source:
        loop, ticks, cycles, digest = claim(a.source, a.song)
    ticks = a.ticks or ticks
    obj = build(a.sid, a.song, cycles, None if loop else ticks)
    if a.out:
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "trackerprog.json").write_text(json.dumps(obj, indent=1))
        text = printer.render(obj)
        (d / "trackerprog.md").write_text(text)
        print("print: " + "  ".join("%s %s" % kv for kv in printer.numbers(text).items()))
    print(
        "patterns %d  events %d  tuning %d  sidtab rows %d  commands %d  accs %d"
        % (
            len(obj["score"]["patterns"]),
            sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
            len(obj["pitch"]["freq"]),
            len(obj["streams"]["casa"]["rows"]),
            len(obj["score"]["commands"]),
            len(obj["accs"]),
        )
    )
    if not a.certify:
        render(obj, min(ticks, 2000))
        return 0
    doc, done = certify(a.sid, a.song, obj, ticks, cycles or 19656, a.budget, a.resume)
    if not done:
        print("certify: %d of %d ticks, resuming" % (doc["ticks"], ticks))
        return 2
    doc["source"] = {
        "tune": obj["meta"]["tune"],
        "song": a.song,
        "oracle": "deity_informant.PcodeVM",
        "certificate_digest": digest,
    }
    doc["loop"] = loop and dict(loop, verified=loop_holds(obj, loop))
    doc["end"] = {"tick": doc["ticks"] - 1, "kind": "loop" if loop else "horizon"}
    print(json.dumps({k: v for k, v in doc.items() if k != "dropped"}, indent=1))
    if a.out:
        (Path(a.out) / "trackerprog.certificate.json").write_text(json.dumps(doc, indent=1))
    return 0 if doc["divergence"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
