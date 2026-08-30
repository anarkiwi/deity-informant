#!/usr/bin/env python3
"""GoatTracker 2 as a trackerprog, transliterated by hand.

Not a lift, a reading: docs/prototype-goattracker.md and playroutine-anatomy.md
section 3.3 restated in the trackerprog's vocabulary and rendered by the
universal player.  docs/prototype-goattracker-trackerprog.md is the mapping.
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

SIGS = {
    "flush": ("A2 .. BD .. .. 9D 00 D4 CA 10 ..", {"regs": ("imm", 1), "ghost": ("word", 3)}),
    "wavenote": (
        "9D .. .. A8 A9 00 9D .. .. B9 .. .. 9D .. .. B9 .. .. 9D .. ..",
        {"freqlo": ("word", 10), "freqhi": ("word", 16)},
    ),
    "fetch": (
        "BC .. .. B9 .. .. 85 .. B9 .. .. 85 .. BC .. .. B1 .. C9 40",
        {"pattlo": ("word", 4), "patthi": ("word", 9)},
    ),
    "sequencer": (
        "BC .. .. B9 .. .. 85 .. B9 .. .. 85 .. BC .. .. B1 .. C9 FF",
        {"songlo": ("word", 4), "songhi": ("word", 9)},
    ),
    "notebase": ("38 E9 .. 9D .. .. A9 00 9D .. ..", {"notecode": ("imm", 2)}),
    "insload": (
        "B9 .. .. 9D .. .. B9 .. .. 9D .. .. B9 .. .. 9D .. .. BD .. .. 20",
        {"waveptr": ("word", 1), "sr": ("word", 7), "ad": ("word", 13)},
    ),
    "wavestep": (
        "BC .. .. F0 .. B9 .. .. C9 10 B0 .. DD .. .. F0 .. FE .. .. D0",
        {"wave": ("word", 6)},
    ),
    "wavejump": (
        "B9 .. .. C9 FF C8 98 90 .. B9 .. .. 9D .. .. A9 00 9D .. ..",
        {"note": ("word", 10)},
    ),
    "pulsehead": ("BC .. .. F0 .. BD .. .. D0 .. B9 .. .. 10 .. 9D .. ..", {"time": ("word", 11)}),
    "pulseset": ("B9 .. .. 10 .. 9D .. .. B9 .. .. 9D .. .. 4C", {"spd": ("word", 9)}),
    "setcutoff": ("B9 .. .. 8D .. .. 4C", {"spd": ("word", 1), "cutoff": ("word", 4)}),
    "speed": (
        "B9 .. .. 30 .. 85 .. B9 .. .. 85 .. 4C",
        {"left": ("word", 1), "right": ("word", 8)},
    ),
    "filthead": (
        "A0 .. F0 .. A9 .. D0 .. B9 .. .. F0 .. 10 .. 0A 8D .. ..",
        {"step": ("cell", 1), "time": ("cell", 5), "left": ("word", 9), "type": ("word", 17)},
    ),
    "filtflush": (
        "A9 .. 8D .. .. A9 .. 8D .. .. A9 .. 09 .. 8D .. ..",
        {"ctrl": ("cell", 6), "fader": ("cell", 13)},
    ),
    "execchn": (
        "DE .. .. F0 .. 10 .. BD .. .. C9 02 B0 .. A8 49 01 9D .. .. B9 .. .. E9 00",
        {"funk": ("word", 21)},
    ),
    "hardrestart": (
        "BD .. .. C9 .. B0 .. A9 .. 9D .. .. A9 .. 9D .. .. A9 FE 9D .. ..",
        {"nohr": ("imm", 4), "srparam": ("imm", 8), "adparam": ("imm", 13)},
    ),
    "initchn": ("A9 .. 9D .. .. A9 01 9D .. .. 9D .. .. 4C", {"tempo": ("imm", 1)}),
}

BLOCKS = {
    "songptr": (0, 0),
    "trans": (0, 1),
    "repeat": (0, 2),
    "pattptr": (0, 3),
    "packedrest": (0, 4),
    "newfx": (0, 5),
    "newparam": (0, 6),
    "fx": (1, 0),
    "param": (1, 1),
    "newnote": (1, 2),
    "waveptr": (1, 3),
    "wave": (1, 4),
    "pulseptr": (1, 5),
    "pulsetime": (1, 6),
    "songnum": (2, 0),
    "pattnum": (2, 1),
    "tempo": (2, 2),
    "rowclock": (2, 3),
    "note": (2, 4),
    "instr": (2, 5),
    "gate": (2, 6),
    "vibtime": (3, 0),
    "vibdelay": (3, 1),
    "wavetime": (3, 2),
    "gatetimer": (4, 5),
    "lastnote": (4, 6),
}
COLUMNS = (
    "ad",
    "sr",
    "waveptr",
    "pulseptr",
    "filtptr",
    "vibparam",
    "vibdelay",
    "gatetimer",
    "firstwave",
)
# the cells the object still needs; the rest of blocks A-E are cursors the layer spent
LIVE = (
    "rowclock",
    "tempo",
    "instr",
    "gate",
    "wave",
    "param",
    "vibtime",
    "vibdelay",
    "note",
    "lastnote",
)
NO_SPEED = "the 1-based table's null: index 0 names no speed, so it carries none"
# what each row command does -- the score names commands by that, never by the
# nibble the player's jump table indexes them with
COMMANDS = (
    "vibrato.instrument",
    "slide.up",
    "slide.down",
    "portamento",
    "vibrato",
    "ad",
    "sr",
    "wave",
    "stream.wave",
    "stream.pulse",
    "stream.filter",
    "filter.route",
    "filter.cutoff",
    "volume",
    "tempo.swing",
    "tempo",
)


def _semitones(right):
    """A relative note column: its low seven bits, read as a signed semitone count."""
    k = right & 0x7F
    return k - 0x80 if k & 0x40 else k


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


def sites(m, lo, hi, pat):
    """Every offset where a wildcarded opcode pattern holds."""
    want = [None if b == ".." else int(b, 16) for b in pat.split()]
    return [
        i
        for i in range(lo, hi - len(want))
        if all(w is None or m[i + j] == w for j, w in enumerate(want))
    ]


def read(m, lo, hi):
    """Each signature's operands, from the one site in the code that matches it."""
    out = {}
    for name, (pat, fields) in SIGS.items():
        at = sites(m, lo, hi, pat)
        assert len(at) == 1, "%s matches %d sites" % (name, len(at))
        for f, (kind, off) in fields.items():
            i = at[0] + off
            out["%s.%s" % (name, f)] = {"imm": m[i], "cell": i, "word": m[i] | m[i + 1] << 8}[kind]
    return out


def layout(m, lo, hi, songs=1, voices=3):
    """Every base and count of the tune's data, each from its own anchor.

    A parallel pair (lo/hi, left/right) gives its own length: the two columns are
    adjacent and equal, so the second base less the first is the row count, and
    the packer's spare bytes between tables are never counted as rows.
    """
    a = read(m, lo, hi)
    g = a["flush.ghost"]
    o = {
        "ghost": g,
        "regs": a["flush.regs"] + 1,
        "notecode": a["notebase.notecode"],
        "voices": voices,
        "songs": songs,
    }
    for name, (blk, k) in BLOCKS.items():
        o[name] = g - 7 * voices * (5 - blk) + k
    o["freq_lo"], o["freq_hi"] = a["wavenote.freqlo"], a["wavenote.freqhi"]
    assert o["freq_lo"] == g + o["regs"], "the tuning follows the ghost image"
    o["notes"] = o["freq_hi"] - o["freq_lo"]
    o["songtbl"], o["songtbl_hi"] = a["sequencer.songlo"], a["sequencer.songhi"]
    o["patttbl"], o["patttbl_hi"] = a["fetch.pattlo"], a["fetch.patthi"]
    o["patterns"] = o["patttbl_hi"] - o["patttbl"]
    o["ins"] = a["insload.ad"] + 1
    o["instruments"] = a["insload.sr"] - a["insload.ad"]
    assert a["insload.waveptr"] - a["insload.sr"] == o["instruments"], "one stride, nine columns"
    assert o["ins"] == o["patttbl_hi"] + o["patterns"], "the columns follow the pattern table"
    o["wave"], o["wave_r"] = a["wavestep.wave"] + 1, a["wavejump.note"] + 1
    o["waverows"] = o["wave_r"] - o["wave"]
    assert o["wave"] == o["ins"] + len(COLUMNS) * o["instruments"], "the wavetable follows them"
    o["pulse"], o["pulse_r"] = a["pulsehead.time"] + 1, a["pulseset.spd"] + 1
    o["pulserows"] = o["pulse_r"] - o["pulse"]
    o["filt"], o["filt_r"] = a["filthead.left"] + 1, a["setcutoff.spd"] + 1
    o["filtrows"] = o["filt_r"] - o["filt"]
    o["speed"], o["speed_r"] = a["speed.left"] + 1, a["speed.right"] + 1
    o["speedrows"] = o["speed_r"] - o["speed"]
    o["funk"] = a["execchn.funk"]
    o["filtstep"], o["filttime"] = a["filthead.step"], a["filthead.time"]
    o["filtcutoff"], o["filtctrl"] = a["setcutoff.cutoff"], a["filtflush.ctrl"]
    o["filttype"], o["fader"] = a["filthead.type"], a["filtflush.fader"]
    o["nohr"] = a["hardrestart.nohr"]
    o["adparam"], o["srparam"] = a["hardrestart.adparam"], a["hardrestart.srparam"]
    o["deftempo"] = a["initchn.tempo"]
    return o


class Tune:
    """One GoatTracker 2 tune's data, read through its own player's operands."""

    def __init__(self, path):
        self.path = path
        self.m, lo, hi = load(path)
        self.L = layout(self.m, lo, hi)
        self.cmds = {}  # the tune's distinct row commands; the score names them

    def t(self, base, i, right=False):
        """A 1-based table entry; ``right`` selects the parallel second column."""
        return self.m[self.L[base] - 1 + i + (self.L[base + "rows"] if right else 0)]

    def col(self, name, i):
        """One instrument column, nine of them at one stride."""
        k = COLUMNS.index(name) * self.L["instruments"]
        return self.m[self.L["ins"] + k - 1 + i]

    def pitch(self):
        """A base note and a contiguous run of frequencies: the tune's whole tuning."""
        return {
            "base": 0,
            "tuning": "12-TET",
            "freq": [
                self.m[self.L["freq_lo"] + n] | self.m[self.L["freq_hi"] + n] << 8
                for n in range(self.L["notes"])
            ],
        }

    # ---- the score ------------------------------------------------------------
    def order(self, v):
        """One voice's order program: ``play(pattern, transpose)`` and its terminator."""
        m, L = self.m, self.L
        s = m[L["songnum"] + 7 * v]
        base = m[L["songtbl"] + s] | m[L["songtbl_hi"] + s] << 8
        play, at, trans, y = [], {}, 0, 0
        while True:
            at[y] = len(play)
            b = m[base + y]
            if b == 0xFF:
                return {"play": play, "end": {"jump": at[m[base + y + 1]]}}
            assert b < 0xD0 or b >= 0xE0, "an orderlist REPEAT is no play step yet"
            if b >= 0xE0:
                trans = b - 0xF0
                y += 1
                b = m[base + y]
            play.append({"pattern": b, "transpose": trans})
            y += 1

    def pattern(self, n):
        """One pattern, materialised: no packed byte and no byte cursor survives."""
        m = self.m
        base = m[self.L["patttbl"] + n] | m[self.L["patttbl_hi"] + n] << 8
        out, y = [], 0
        while m[base + y] != 0:
            e = {
                "dur": 1,
                "sounds": False,
                "tie": False,
                "gate": None,
                "note": None,
                "ins": None,
                "arm": None,
            }
            b = m[base + y]
            if b < 0x40:
                e["ins"] = b
                y += 1
                b = m[base + y]
            if b < 0x60:
                fx, param = b & 0x0F, None
                if fx:
                    y += 1
                    param = m[base + y]
                e["arm"] = self.name(fx, param)
                if b >= 0x50:  # FXONLY: the row carries no note
                    out.append(e)
                    y += 1
                    continue
                y += 1
                b = m[base + y]
            if b >= 0xC0:
                e["dur"] = 0x100 - b
            elif b > 0xBD:
                e["gate"] = "off" if b == 0xBE else "on"
            elif b < 0xBD:
                e["note"], e["sounds"] = b - self.L["notecode"], True
            out.append(e)
            y += 1
        return {"events": out}

    def score(self):
        """The three order programs and the patterns they reach."""
        orders = [self.order(v) for v in range(3)]
        used = {p["pattern"] for o in orders for p in o["play"]}
        pats = {str(p): self.pattern(p) for p in sorted(used)}
        return {"orders": orders, "patterns": pats, "commands": {}}

    def name(self, fx, param):
        """One row command, interned under what it does; the score names it."""
        key = COMMANDS[fx] + ("" if param is None else ":%02X" % param)
        self.cmds.setdefault(key, self.command(fx, param))
        return key

    def command(self, fx, param):  # noqa: C901 - one clause per command number
        """One of the fifteen row commands, unpacked into section 3.6's own fields."""
        c = {}
        if fx < 5:
            c["sets"] = [["@param", {"ins": "vibparam"} if fx == 0 else param]]
            c["arms"] = [SNAP] if fx == 3 and param == 0 else ARMS[fx]
            if fx == 3:  # a tone portamento re-targets the note; it does not retrigger
                c["tie"] = True
            if fx in (1, 2):
                c["links"] = ["vib_phase"]
        elif fx == 5:
            c["sets"] = [["ad", param]]
        elif fx == 6:
            c["sets"] = [["sr", param]]
        elif fx == 7:
            c["sets"] = [["@wave", param]]
        elif fx == 8:
            c["point"] = [["wave", param]]
        elif fx == 9:
            c["point"] = [["pulse", param]]
        elif fx == 0xA:
            c["point"] = [["filter", param]]
        elif fx == 0xB:
            c["sets"] = [["#filtctrl", param]]
            if param == 0:
                c["point"] = [["filter", 0]]
        elif fx == 0xC:
            c["sets"] = [["#cutoff", param]]
        elif fx == 0xD:
            assert param < 0x10, "a master volume above $0F is a timing mark, not a volume"
            c["sets"] = [["#fader", param]]
        elif fx == 0xE:
            c["sets"] = [
                ["#funk0", self.t("speed", param)],
                ["#funk1", self.t("speed", param, True)],
            ]
            c["all"] = [["@tempo", 0]]
        elif fx == 0xF:
            if param & 0x80:
                c["sets"] = [["@tempo", param & 0x7F]]
            else:
                c["all"] = [["@tempo", param]]
        return c

    # ---- the streams ----------------------------------------------------------
    def wave_stream(self):
        """The wavetable: a waveform, a hold, a pitch of the tuning, or a command."""
        rows = [{"trap": "a 1-based table has no row zero"}]
        for y in range(1, self.L["waverows"] + 1):
            left, right = self.t("wave", y), self.t("wave", y, True)
            if left == 0xFF:
                rows.append({"jump": right})
                continue
            row = {}
            if left < 0x10:
                row["hold"] = left + 1
            elif (left - 0x10) & 0xFF < 0xE0:
                row["sets"] = [["@wave", (left - 0x10) & 0xFF]]
            if left >= 0xE0:
                row["op"] = self.wave_command(left & 0x0F, right)
            elif right:
                row["op"] = (
                    {"pitch": right}
                    if right < 0x80
                    else {"pitch": _semitones(right), "relative": True}
                )
            rows.append(row)
        rows.append({"trap": "past the last row of the table"})
        return {"rank": 0, "rows": rows, "term": "jump"}

    def wave_command(self, fx, param):
        """A wavetable command row: this tick's own effect, or a row command."""
        if fx >= 5:
            return {"cmd": self.name(fx, param)}
        if fx == 3 and param == 0:
            return dict(SNAP)
        arms = ARMS[fx]
        assert len(arms) == 1, "a wavetable row runs one accumulator, not a group"
        return dict(arms[0], row=param)

    def pulse_stream(self):
        """The pulse table: set the width, or step it for a count of ticks."""
        rows = [{"trap": "a 1-based table has no row zero"}]
        for y in range(1, self.L["pulserows"] + 1):
            left, right = self.t("pulse", y), self.t("pulse", y, True)
            if left == 0xFF:
                rows.append({"jump": right})
            elif left & 0x80:
                rows.append({"sets": [["pw_hi", left], ["pw_lo", right]]})
            else:
                rows.append(
                    {
                        "hold": left,
                        "run": [{"acc": "pulse_step", "delta": right - (right & 0x80) * 2}],
                    }
                )
        rows.append({"trap": "past the last row of the table"})
        return {"rank": 6, "rows": rows, "term": "jump"}

    def filter_stream(self):
        """The filter table: set the cutoff, set the mode, or sweep for a count."""
        rows, fused = [{"trap": "a 1-based table has no row zero"}], set()
        for y in range(1, self.L["filtrows"] + 1):
            left, right = self.t("filt", y), self.t("filt", y, True)
            if left == 0xFF:
                rows.append({"jump": right})
            elif left == 0:
                rows.append({"sets": [["#cutoff", right]]})
            elif left < 0x80:
                rows.append({"hold": left, "run": [{"acc": "cutoff_step", "delta": right}]})
            else:
                sets = [["#filttype", (left << 1) & 0xFF], ["#filtctrl", right]]
                row = {"sets": sets}
                if self.t("filt", y + 1) == 0:  # a mode row takes the cutoff row with it
                    sets.append(["#cutoff", self.t("filt", y + 1, True)])
                    row["next"] = y + 2
                    fused.add(y + 1)
                rows.append(row)
        rows.append({"trap": "past the last row of the table"})
        for y in fused:
            rows[y] = {"trap": "the mode row above consumed this cutoff row with it"}
        return {"rank": 0, "rows": rows, "term": "jump", "scope": "global"}

    def speed_stream(self):
        """The speed table, unpacked: what an arm binds on the accumulator it names."""
        rows = [{"zero": 1, "cmp": 0, "delta": {"trap": NO_SPEED}, "depth": {"trap": NO_SPEED}}]
        for y in range(1, self.L["speedrows"] + 1):
            left, right = self.t("speed", y), self.t("speed", y, True)
            if left & 0x80:  # calculated: a fraction of the semitone above the note sounded
                step = {"tablestep": ["pitch", {"cell": "lastnote"}, right]}
                rows.append({"zero": 0, "cmp": left & 0x7F, "delta": step, "depth": step})
            else:
                rows.append(
                    {
                        "zero": int(right == 0),
                        "cmp": left & 0x7F,
                        "delta": (left << 8) | right,
                        "depth": right,
                    }
                )
        return {"rank": 0, "rows": rows, "term": "halt", "kind": "arm"}

    def instruments(self, used):
        """Nine columns, read as adsr, a prelude, four stream entries and cells."""
        out = {}
        for i in sorted(used):
            fw = self.col("firstwave", i)
            pul, flt = self.col("pulseptr", i), self.col("filtptr", i)
            note_sets = [["@gate", 0xFF if fw < 0xFE else fw]]
            if fw < 0xFE:
                note_sets.insert(0, ["@wave", fw])
            points = [["wave", self.col("waveptr", i), True]]
            points += [["pulse", pul, False]] if pul else []
            points += [["filter", flt, False]] if flt else []
            out[str(i)] = {
                "adsr": [self.col("ad", i), self.col("sr", i)],
                "wave": fw,
                "vibparam": self.col("vibparam", i),
                "vibdelay": self.col("vibdelay", i),
                "sets": [["@vibdelay", {"ins": "vibdelay"}], ["@param", {"ins": "vibparam"}]],
                "note_sets": note_sets,
                "points": points,
                "prelude": None if i >= self.L["nohr"] else {"stream": "hard_restart"},
                "accs": [],
            }
        return out

    def build(self, song=0):
        """The whole object: section 3's seven sections, and the state init leaves."""
        m, L = self.m, self.L
        score = self.score()
        used = {1} | {
            e["ins"]
            for p in score["patterns"].values()
            for e in p["events"]
            if e["ins"] is not None
        }
        gates = {self.col("gatetimer", i) for i in used}
        assert len(gates) == 1, "the fetch is early by one number, not by the instrument"
        cells = {k: [m[L[k] + 7 * v] for v in range(3)] for k in LIVE}
        streams = {
            "note_on": {
                "rank": 0,
                "term": "halt",
                "rows": [{"sets": [["sr", {"ins": "adsr.1"}], ["ad", {"ins": "adsr.0"}]]}],
            },
            "hard_restart": {
                "rank": 0,
                "term": "halt",
                "rows": [{"sets": [["sr", L["srparam"]], ["ad", L["adparam"]], ["@gate", 0xFE]]}],
            },
            "exit": {"rank": 0, "term": "halt", "rows": [{"sets": [["ctrl", GATED]]}]},
            "funktempo": {
                "rank": 0,
                "term": "jump",
                "rows": [{"value": {"global": "funk0"}}, {"value": {"global": "funk1"}}],
            },
            "wave": self.wave_stream(),
            "pulse": self.pulse_stream(),
            "filter": self.filter_stream(),
            "speed": self.speed_stream(),
        }
        score["commands"] = dict(sorted(self.cmds.items()))  # every command, once, named
        return {
            "$trackerprog": 1,
            "meta": self.meta(song, gates.pop()),
            "pitch": self.pitch(),
            "streams": streams,
            "accs": accs(),
            "instruments": self.instruments(used),
            "score": score,
            "globals": {
                "streams": ["filter"],
                "commit": [
                    [22, {"global": "cutoff"}],
                    [23, {"global": "filtctrl"}],
                    [24, {"or": [{"global": "filttype"}, {"global": "fader"}]}],
                ],
                "flags": {},
                "stop_writes": [],
            },
            "state0": {
                "shadow": list(m[L["ghost"] : L["ghost"] + L["regs"]]),
                "cells": cells,
                "ins": cells["instr"],
                "globals": {
                    "cutoff": m[L["filtcutoff"]],
                    "filtctrl": m[L["filtctrl"]],
                    "filttype": m[L["filttype"]],
                    "fader": m[L["fader"]],
                    "funk0": m[L["funk"]],
                    "funk1": m[L["funk"] + 1],
                },
                "cursors": {
                    k: [{"row": m[L[c] + 7 * v], "hold": m[L[h] + 7 * v]} for v in range(3)]
                    for k, c, h in (
                        ("wave", "waveptr", "wavetime"),
                        ("pulse", "pulseptr", "pulsetime"),
                    )
                },
                "gcursors": {"filter": {"row": m[L["filtstep"]], "hold": m[L["filttime"]]}},
                "held": self.name(0, None),
            },
        }

    def meta(self, song, early):
        """Section 3.1, and the data a family's whole tick shape reduces to."""
        L = self.L
        return {
            "tune": Path(self.path).name,
            "family": "GoatTracker 2",
            "song": song,
            "cycles_per_tick": 19656,
            "voices": 3,
            "voice_order": [0, 1, 2],
            "commit_order": ["sr", "ad", "ctrl"],
            "shadow": {"registers": L["regs"], "order": "descending"},
            "tempo": {
                "form": "countdown",
                "cell": "rowclock",
                "reload": "tempo",
                "boundary": 0,
                "early": early,
                "alternate": {"stream": "funktempo", "when": [[{"cell": "tempo"}, "<", 2]]},
            },
            "row_consumes_tick": [["sounds", "!=", 0]],
            "row_command": "held",
            "prefetch": ["ins", "gate", "arm"],
            "rest_arm": ARMS[0],
            "note_row": "note_on",
            "voice_exit": "exit",
            "pitch_links": ["vib_phase"],
            "prologue": {
                "id": "init",
                "sets": [
                    ["@wave", 0],
                    ["@param", 0],
                    ["@tempo", L["deftempo"]],
                    ["@rowclock", 1],
                    ["@instr", 1],
                    ["#filtctrl", 0],
                    [21, 0],
                    ["ctrl", GATED],
                ],
                "point": [["wave", 0], ["pulse", 0], ["filter", 0]],
            },
            "player": "prototype-trackerprog.md sections 4 and 5",
        }


GATED = {"and": [{"cell": "wave"}, {"cell": "gate"}]}
SPEED = {"tabcell": ["speed", {"const": "row"}, "delta"]}
DEPTH = {"tabcell": ["speed", {"const": "row"}, "depth"]}
QUIET = [[{"tabcell": ["speed", {"const": "row"}, "zero"]}, "==", 0]]
ROW = {"cell": "param"}
SNAP = {"acc": "toneporta_snap"}
ARMS = {
    0: [
        {"acc": "vib_delay", "row": ROW, "when": QUIET + [[{"cell": "vibdelay"}, "!=", 0]]},
        {"acc": "vib_phase", "row": ROW, "when": QUIET + [[{"cell": "vibdelay"}, "==", 0]]},
        {"acc": "vibrato", "row": ROW, "when": QUIET + [[{"cell": "vibdelay"}, "==", 0]]},
    ],
    1: [{"acc": "porta_up", "row": ROW}],
    2: [{"acc": "porta_down", "row": ROW}],
    3: [{"acc": "toneporta", "row": ROW}],
    4: [{"acc": "vib_phase", "row": ROW}, {"acc": "vibrato", "row": ROW}],
}


def accs():
    """Section 5's records: eight forms, every one a row of section 5's own table."""
    slide = {
        "cell": "voice.freq",
        "target": "freq",
        "width": 16,
        "delta": SPEED,
        "policy": "wrap",
        "rate": 1,
        "scope": "voice",
        "produce": [["freq_lo", "lo"], ["freq_hi", "hi"]],
        "bound": {
            "from": "projected",
            "interval": [0, 0xFFFF],
            "witness": "the 16-bit store; a free slide has no target",
        },
    }
    return {
        "vib_delay": {
            "id": "vib_delay",
            "rank": 3,
            "cell": "vibdelay",
            "target": "note",
            "width": 8,
            "delta": {"const": -1},
            "policy": "wrap",
            "rate": 1,
            "scope": "voice",
            "bound": {
                "from": "proved",
                "interval": [0, 0xFF],
                "witness": "the arm's own guard, vibdelay != 0",
            },
            "produce": [],
        },
        "vib_phase": {
            "id": "vib_phase",
            "rank": 1,
            "cell": "vibtime",
            "target": "note",
            "width": 8,
            "delta": {"const": 2},
            "policy": "reflect-complement",
            "rate": 1,
            "scope": "voice",
            "bound": {
                "from": "proved",
                "interval": [0, {"tabcell": ["speed", {"const": "row"}, "cmp"]}],
                "witness": "the speed row's own compare",
            },
            "produce": [],
        },
        "vibrato": {
            "id": "vibrato",
            "rank": 2,
            "cell": "voice.freq",
            "target": "freq",
            "width": 16,
            "delta": DEPTH,
            "policy": "wrap",
            "rate": 1,
            "scope": "voice",
            "phase": {"bit": [{"cell": "vibtime"}, 0]},
            "bound": {"from": "projected", "interval": [0, 0xFFFF], "witness": "the 16-bit store"},
            "produce": [["freq_lo", "lo"], ["freq_hi", "hi"]],
        },
        "porta_up": dict(slide, id="porta_up", rank=2, phase={"const": 0}),
        "porta_down": dict(slide, id="porta_down", rank=2, phase={"const": 1}),
        "toneporta": {
            "id": "toneporta",
            "rank": 2,
            "cell": "voice.freq",
            "target": "freq",
            "width": 16,
            "delta": SPEED,
            "policy": {"clamp": {"notefreq": None}},
            "rate": 1,
            "scope": "voice",
            "links": [{"reset": "vib_phase"}],
            "bound": {
                "from": "proved",
                "interval": [0, 0xFFFF],
                "witness": "the tuning at the voice's note; the step cannot pass it",
            },
            "produce": [["freq_lo", "lo"], ["freq_hi", "hi"]],
        },
        "toneporta_snap": {
            "id": "toneporta_snap",
            "rank": 2,
            "cell": "voice.freq",
            "target": "freq",
            "width": 16,
            "policy": "take",
            "rate": 1,
            "scope": "voice",
            "bound": {
                "from": "proved",
                "interval": [0, 0xFFFF],
                "witness": "no step at all: the voice is at its target already",
            },
            "produce": [],
        },
        "pulse_step": {
            "id": "pulse_step",
            "rank": 6,
            "cell": "@pw",
            "target": "pw",
            "width": 16,
            "delta": {"const": "delta"},
            "policy": "wrap",
            "rate": 1,
            "scope": "voice",
            "bound": {
                "from": "projected",
                "interval": [0, 0xFFFF],
                "witness": "the store is 16-bit; the chip sees 12",
            },
            "produce": [],
        },
        "cutoff_step": {
            "id": "cutoff_step",
            "rank": 0,
            "cell": "#cutoff",
            "target": "cutoff",
            "width": 8,
            "delta": {"const": "delta"},
            "policy": "wrap",
            "rate": 1,
            "scope": "global",
            "bound": {
                "from": "projected",
                "interval": [0, 0xFF],
                "witness": "the store is 8-bit; the chip sees the high 8 of 11",
            },
            "produce": [],
        },
    }


def build(path, song=0):
    """The trackerprog object for one GoatTracker 2 tune."""
    return Tune(path).build(song)


def claim(path, song):
    """The loop the source tuneprog's certificate claims, and the binding to it."""
    d = Path(path).read_bytes()
    c = json.loads(d)
    s = next(x for x in c["subtunes"] if x["song"] == song + 1)
    return (
        {"period": s["period"], "first_repeat": s["first_repeat"]},
        s["ticks"],
        hashlib.sha256(d).hexdigest()[:16],
    )


def loop_holds(obj, loop):
    """Re-verify the inherited claim on the render: the horizon replays itself.

    The claim names the call the state repeats *after*, and a ghost flush emits
    the image the call before it left, so the replay starts one tick later: the
    period after ``first_repeat`` is the period before it, write for write.
    """
    n, p = loop["first_repeat"] + 1, loop["period"]
    w = render(obj, n + p)
    return w[n - p : n] == w[n : n + p]


def reference(path, song, ticks):
    """The oracle: the tune's own player on the PcodeVM, per-tick SID writes."""
    d = Path(path).read_bytes()
    init, play = struct.unpack(">H", d[10:12])[0], struct.unpack(">H", d[12:14])[0]
    vm, cache = PcodeVM(load(path)[0]), {}
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
    ap.add_argument("--song", type=int, default=0)
    ap.add_argument("--ticks", type=int, default=8236)
    ap.add_argument("--out", default=None, help="directory for trackerprog.json")
    ap.add_argument("--source", default=None, help="the source tuneprog's certificate.json")
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
        "instruments %d  patterns %d  events %d  tuning %d  streams %d  accs %d"
        % (
            len(obj["instruments"]),
            len(obj["score"]["patterns"]),
            sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
            len(obj["pitch"]["freq"]),
            len(obj["streams"]),
            len(obj["accs"]),
        )
    )
    if a.certify:
        loop, ticks, digest = claim(a.source, a.song) if a.source else (None, a.ticks, None)
        c = attest(obj, reference(a.sid, a.song, ticks))
        c["source"] = {
            "tune": obj["meta"]["tune"],
            "song": a.song,
            "oracle": "deity_informant.PcodeVM",
            "certificate_digest": digest,
        }
        c["loop"] = loop and dict(loop, verified=loop_holds(obj, loop))
        c["end"] = {"tick": ticks - 1, "kind": "loop" if loop else "horizon"}
        print(json.dumps({k: v for k, v in c.items() if k != "dropped"}, indent=1))
        if a.out:
            (Path(a.out) / "trackerprog.certificate.json").write_text(json.dumps(c, indent=1))
        return 0 if c["divergence"] is None else 1
    render(obj, a.ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
