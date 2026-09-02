#!/usr/bin/env python3
"""SID Wizard as a trackerprog, transliterated by hand.

Not a lift, a reading: docs/prototype-sidwizard.md and playroutine-anatomy.md
section 3.4 restated in the trackerprog's vocabulary and rendered by the
universal player.  docs/prototype-sidwizard-trackerprog.md is the mapping.

Refused: the three table-pointer commands.  ``stream.wave``, ``stream.pulse``
and ``stream.filter`` carry a *byte offset* into the instrument's own record,
and ``row_of`` -- a build-time table per instrument -- is the only map from one
to a stream row, so a command, which names no instrument, cannot state the row.
Neither certified tune emits one; the build stops rather than compute it.
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

# The player's own state, five bunches of three voices of seven bytes plus the
# constants bunch: one anchor gives every cell, as GoatTracker 2's ghost does.
BUNCH = (
    ("freq", None, "pw", None, "wave", "gate", "pweepcnt"),
    (None, "spdcnt", None, None, "wftpos", "pwtpos", "arpscnt"),
    (None, None, "note", None, "ins", None, None),
    ("slidevib", "freqmod", None, "videlcnt", "vibfrequ", "vibracnt", None),
    ("tmpptr", "tmppos", "arpsped", "pkbdtrk", "curchord", "chordpos", None),
)
CONST = (None, None, None, None, None, None, "detuner")  # the constants bunch
CELLS = (
    "wave",
    "gate",
    "spdcnt",
    "arpscnt",
    "note",
    "ins",
    "slidevib",
    "videlcnt",
    "vibfrequ",
    "vibracnt",
    "tmpptr",
    "tmppos",
    "arpsped",
    "pkbdtrk",
    "curchord",
    "chordpos",
    "detuner",
)
WIDE = ("freq", "pw", "freqmod", "cutoff")  # the cells that are 16 bits, not 8

SIGS = {
    "dotrack": (
        ["BC .. .. BD .. .. C8 38 F9 .. .. F0 .. 50 .. BC .. .. A9 00 9D .. .. 98 9D .. .."],
        {"spdcnt": ("w", 4), "tempo_m1": ("w", 9)},
    ),
    "tick0": (
        ["BC .. .. B9 .. .. 18 6D .. .. 85 .. B9 .. .. 6D .. .. 85 .. A9 00 9D .. .. 9D .. .."],
        {"pptrlo": ("w", 4), "swp": ("w", 8), "pptrhi": ("w", 13)},
    ),
    "insptr": (
        ["BC .. .. B9 .. .. 18 6D .. .. 85 .. B9 .. .. 6D .. .. 85 .. 4C"],
        {"insptlo": ("w", 4), "inspthi": ("w", 13)},
    ),
    "tick2": (["A0 FF BD .. .. F0 .. C9 .. 10 .. 9D .. .. A0 3F 8C .. .."], {"maxins": ("i", 8)}),
    "hradsr": (
        [
            "A9 FE 9D .. .. 3D .. .. 9D .. .. C8 B1 .. 9D 05 D4 C8 B1 .. 9D 06 D4",
            "A9 FE 9D .. .. 3D .. .. 9D .. .. A0 02 B1 .. 9D 06 D4 88 B1 .. 9D 05 D4",
        ],
        {},
    ),
    "noteadsr": (
        [
            "A0 03 B1 .. 8D .. .. 4A 4A 4A 4A A8 B9 .. .. 18 69 .. A8 B9 .. .. 29 F0 69 .."
            " 48 29 0F A8 B9 .. .. 69 .. A8 68 79 .. ..",
            "A0 04 B1 .. 9D 06 D4 88 B1 .. 9D 05 D4",
        ],
        [{"adsr_offs": ("w", 13), "expoff": ("w", 20), "adsr_exptb": ("w", 38)}, {}],
    ),
    "freqhi": (
        [
            "BC .. .. BD .. .. 9D 01 D4 A0 0F B1 .. 9D .. ..",
            "BC .. .. B9 .. .. 9D 01 D4 A0 0F B1 .. 9D .. ..",
        ],
        {"freqtbh": ("w", 4)},
    ),
    "wftpos": (
        ["A9 10 9D .. .. A9 FF 9D .. .. A0 07", "A9 10 9D .. .. A9 FF 9D .. .. 9D .. .. A0 07"],
        {},
    ),
    "chordsel": (["A0 08 B1 .. 9D .. .. A8 B9 .. .. 9D .. .."], {"chdptrlo": ("w", 9)}),
    "route": (
        ["A0 0B B1 .. A8 B1 .. F0 .. C9 FF F0 .. 8E .. .. 8C .. .."],
        {"flswtbl": ("w", 20)},
    ),
    "ownercheck": (["EC .. .. D0 .. 8C .. .. BD .. .. 2D .. .. 8D .. .."], {}),
    "filthead": (
        ["E0 .. D0 .. A0 .. B1 .. 30 .. C8 C9 .. F0 .. EE .. .. 18 B1 .. 10 .."],
        {"fltctrl": ("c", 1), "fltposi": ("c", 5), "cwepcnt": ("c", 12)},
    ),
    "sweepneg": (["09 F8 6D .. .. 08 29 07 8D .. .."], {"ctflgho": ("w", 3)}),
    "sweepjoin": (["28 6D .. .. 8D .. .."], {"ctfhgho": ("w", 2)}),
    "pwwrite": (
        ["18 BD .. .. F0 .. 7D .. .. A8 B9 .. .. F9 .. .. 7D .. .. 9D 03 D4 BD .. .. 9D 02 D4"],
        {"exptabh": ("w", 11)},
    ),
    "wfpitch": (
        [
            "18 7D .. .. 29 7F 9D .. .. A8 B9 .. .. 9D .. .. B9 .. .. 9D .. ..",
            "18 7D .. .. 29 7F A8 B9 .. .. 9D .. .. B9 .. .. 9D .. ..",
        ],
        [{"freqtbl": ("w", 11)}, {"freqtbl": ("w", 8)}],
    ),
    "chordstep": (["B1 .. 9D .. .. BC .. .. B9 .. .. C9 7E D0 .."], {"chords": ("w", 9)}),
    "commonregs": (
        ["A9 .. 09 .. 8D 17 D4 A9 .. 09 .. 8D 18 D4 18 A9 .. F0 .."],
        {
            "fswitch": ("c", 1),
            "resonib": ("c", 3),
            "mainvol": ("c", 8),
            "fltband": ("c", 10),
            "ckbdtrk": ("c", 16),
        },
    ),
    "cutoffwrite": (["8D 16 D4 A9 .. 8D 15 D4"], {"ctflgho_i": ("c", 4)}),
    "cutoffcalc": (
        ["AE .. .. 7D .. .. A8 B9 .. .. 69 .. 69 ..", "AE .. .. 7D .. .. A8 B9 .. .. 69 .. A0 .."],
        {"ctfhgho_i": ("c", 11)},
    ),
    "flshift": (["69 .. 69 .. 8D 16 D4"], {"flshift": ("c", 3)}),
    "seqsub": (
        ["E0 07 F0 .. 10 .. B9 .. .. 60 B9 .. .. 60 B9 .. .. 60"],
        {"o0": ("w", 7), "o1": ("w", 11), "o2": ("w", 15)},
    ),
    "traktmp": (["09 80 BC .. .. 99 .. .. 98 9D .. .. 9D .. .."], {"tempotbl": ("w", 6)}),
    "temptrlo": (["F0 .. A8 B9 .. .. 4C"], {"temptrlo": ("w", 4)}),
    "slowdown": (
        ["4E .. .. D0 .. AC .. .. B9 .. .. 8D .. .. 90 .."],
        {"slowdcnt": ("w", 1), "slowdownv": ("w", 6), "dither": ("w", 9)},
    ),
    "wrpitch": (
        [
            "BD .. .. 8D .. .. BD .. .. 8D .. .. BD .. .. A8 38 E9 ..",
            "BD .. .. 7D .. .. 9D 00 D4 BD .. .. 69 .. 9D 01 D4 BD .. .. 9D 04 D4",
        ],
        {},
    ),
    "bigfxtbl": (["0A A8 B9 .. .. 8D .. .. B9 .. .. 8D .. .. BD .. .. 4C"], {"bigfx": ("w", 3)}),
    "smallfxtbl": (
        ["4A 4A 4A 4A A8 B9 .. .. 8D .. .. 68 29 0F 8D .. .. 18 90 .."],
        {"smallfx": ("w", 6), "smalljmp": ("c", 20)},
    ),
    "notefxtbl": (
        ["A8 B9 .. .. 8D .. .. BD .. .. 18 90 .."],
        {"notefx": ("w", 2), "notejmp": ("c", 13)},
    ),
    "transpose": (["BC .. .. BD .. .. 9D .. .. BD .. .. D0 .. C8 B1 .. C9 FF"], {}),
    "seqfx": (["C9 A0 B0 .. E9 8F 9D .. .."], {}),
    "fxpos": (["20 .. .. A0 0A 71 .. 9D .. .. A9 00 9D .. .."], {}),
}
OPTIONAL = ("slowdown", "transpose", "seqfx", "flshift", "ownercheck", "fxpos")

# What each dispatched effect does.  The score names a command by that and never
# by the index the player's three jump tables give it.
NOTEFX = ("porta.note", "sync.on", "sync.off", "ring.on", "ring.off", "gate.on", "gate.off")
SMALLFX = (
    "attack",
    "decay",
    "wave.high",
    "sustain",
    "release",
    "chord",
    "vibrato.depth",
    "vibrato.rate",
    "volume",
    "filter.band",
    "arpeggio.speed",
    "detune.coarse",
    "wave.low",
    "filter.resonance",
)
BIGFX = (
    "slide.up",
    "slide.down",
    "portamento",
    "wave",
    "ad",
    "sr",
    "chord",
    "vibrato",
    "stream.wave",
    "stream.pulse",
    "stream.filter",
    "arpeggio.speed",
    "detune",
    "pulse.high",
    "filter.cutoff",
    "tempo",
    "tempo.funk",
    "tempo.program",
    "tempo.track",
    "tempo.track.funk",
    "tempo.track.program",
    "vibrato.type",
    "filter.shift",
    "filter.shift",
    "filter.shift",
    "filter.shift",
    "filter.shift",
    "filter.shift",
    "nop",
    "nop",
    "filter.route",
)
DEAD = {
    "wave.jump": "no waveform row of either tune jumps",
    "filter.jump": "no filter row of either tune jumps",
    "pulse.jump": "no pulse jump of either tune lands on a row that takes a width",
    "chord.return": "no chord of either tune returns to the waveform row",
    "hr.mute": "no instrument of either tune hard-restarts with the test bit",
    "gate.pointer": "no instrument of either tune carries a gate-off pointer",
    "amount.clamp": "the modulation amount never reaches the top of its table",
    "fx.pointer": "the parameter is a byte offset of the instrument's own record and"
    " row_of, the map from one to a stream row, is that instrument's alone",
}
# the commands that map cannot be stated for, refused rather than computed
FXPOINT = ("stream.wave", "stream.pulse", "stream.filter")


def signed(b):
    return b - 0x100 if b & 0x80 else b


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
    """The band as the tick sees it: init has relocated every table operand.

    The music blob is position-independent, so before ``init`` runs the operands
    hold offsets and no signature over them means anything.
    """
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


def read(m, lo, hi):
    """Each signature's operands, from the sites in the code that agree on them.

    A signature may offer alternatives -- the two builds are one player under
    different flags -- and exactly one must match.  Which one is itself a datum.
    """
    out = {}
    for name, (pats, fields) in SIGS.items():
        hits = [(k, a) for k, a in ((k, sites(m, lo, hi, p)) for k, p in enumerate(pats)) if a]
        if not hits:
            assert name in OPTIONAL, "%s matches no alternative" % name
            out[name] = None
            continue
        assert len(hits) == 1, "%s: %d alternatives match" % (name, len(hits))
        k, at = hits[0]
        out[name] = k
        for f, (kind, off) in (fields[k] if isinstance(fields, list) else fields).items():
            v = {
                (m[i], i, m[i] | m[i + 1] << 8)[{"i": 0, "c": 1, "w": 2}[kind]]
                for i in (s + off for s in at)
            }
            assert len(v) == 1, "%s.%s has %d readings" % (name, f, len(v))
            out[f] = v.pop()
    return out


def layout(m, lo, hi):
    """Every base, cell and build difference, each from its own anchor."""
    a = read(m, lo, hi)
    var = a["spdcnt"] - 22
    o = {n: var + 21 * b + i for b, row in enumerate(BUNCH) for i, n in enumerate(row) if n}
    o.update(
        var=var,
        tempotbl=a["tempo_m1"] + 1,
        swp=a["swp"],
        pptrlo=a["pptrlo"],
        pptrhi=a["pptrhi"],
        insptlo=a["insptlo"],
        inspthi=a["inspthi"],
        freqtbl=a["freqtbl"],
        freqtbh=a["freqtbh"],
        exptabh=a["exptabh"],
        chords=a["chords"],
        chdptrlo=a["chdptrlo"],
        temptrlo=a["temptrlo"],
        flswtbl=a["flswtbl"],
        orderlist=(a["o0"], a["o1"], a["o2"]),
        fswitch=a["fswitch"],
        resonib=a["resonib"],
        mainvol=a["mainvol"],
        fltband=a["fltband"],
        ckbdtrk=a["ckbdtrk"],
        ctfhgho=a["ctfhgho_i"],
        ctflgho=a["ctflgho_i"],
        fltctrl=a["fltctrl"],
        fltposi=a["fltposi"],
        cwepcnt=a["cwepcnt"],
        flshift=a.get("flshift"),
        maxins=a["maxins"],
        hibug=a["freqhi"] == 0,
        arpscnt_reset=a["wftpos"] == 1,
        detune=a["wrpitch"] == 1,
        transpose=a["transpose"] is not None,
        seqfx=a["seqfx"] is not None,
        ownercheck=a["ownercheck"] is not None,
        slowdown=a["slowdcnt"] if a["slowdown"] is not None else None,
        slowdownv=a.get("slowdownv") if a["slowdown"] is not None else None,
        commit_order=["sr", "ad", "ctrl"] if a["hradsr"] else ["ad", "sr", "ctrl"],
    )
    assert a["hradsr"] == a["noteadsr"], "one build writes AD and SR in one order"
    assert o["freqtbh"] + 107 == o["exptabh"] + 118, "the exponent table is the tuning's own"
    o.update({n: a["flswtbl"] + i for i, n in enumerate(CONST) if n})
    o["cvar"] = a["flswtbl"]
    o["bigfx"], o["smallfx"], o["notefx"] = a["bigfx"], a["smallfx"], a["notefx"] + 0x78
    o["smalljmp"], o["notejmp"] = a["smalljmp"], a["notejmp"]
    for k in ("adsr_offs", "expoff", "adsr_exptb"):
        o[k] = a.get(k)
    return o


NOTE = {"cell": "note"}
ALIVE = {"or": [{"cell": "pending"}, {"cell": "ins"}]}  # a note starting, or an instrument
LIVE = [[ALIVE, "!=", 0]]  # the voice sounds at all
RUN = [[{"cell": "pending"}, "==", 0], [{"cell": "ins"}, "!=", 0]]  # its modulators run
OWNS = [[{"cell": "voice_index"}, "==", {"global": "fltctrl"}]]
CHORD = {"tabcell": ["chords", {"cell": "chordpos"}, "value"]}
CHORD_RAW = {"tabcell": ["chords", {"cell": "chordpos"}, "raw"]}
TEMPO = {"tabcell": ["tempo", {"cell": "tmppos"}, "value"]}


def carry(e):
    """The carry an eight-bit add leaves, which another producer reads as a flag."""
    return {"carry_out": [e, 8]}


def borrow(e):
    """The carry an eight-bit subtraction leaves: the 6502's C, 1 where it did not borrow."""
    return {"borrow_out": [e, 8]}


def nibble(base, hi, v):
    """One nibble of a byte the score sets and the other nibble keeps."""
    return {"or": [{"and": [base, 0x0F if hi else 0xF0]}, (v << 4) & 0xF0 if hi else v]}


class Tune:
    """One SID Wizard tune's data, read through its own player's operands."""

    def __init__(self, path, song=0):
        self.path, self.song = path, song
        self.m, lo, hi = image(path, song)
        self.L = layout(self.m, lo, hi)
        self.cmds = {}
        self.rows = {
            k: [{"trap": "no stream"}, {"trap": "past the instrument's own rows"}]
            for k in ("wave", "pulse", "filter")
        }
        self.base = {}

    # ---- the tune's tables ----------------------------------------------------
    def pitch(self):
        """A base note and a contiguous run of frequencies: the tune's whole tuning."""
        m, L = self.m, self.L
        return {
            "base": 0,
            "tuning": "12-TET",
            "freq": [m[L["freqtbl"] + n] | m[L["freqtbh"] + n] << 8 for n in range(96)],
        }

    def beyond(self):
        """What the waveform stream's pitch step does past the top of the tuning.

        The tune's note column masks to seven bits, so a step may name a note the
        tuning has not got; what it plays there is the stream's own, indexed by
        how far past the tuning it went and never by a note.
        """
        m, L = self.m, self.L
        return {
            "index": "how far past the tuning the step's own seven bits went",
            "state": {},
            "on": [],
            "words": [
                {"u16": [m[L["freqtbl"] + 96 + d], m[L["freqtbh"] + 96 + d]]} for d in range(32)
            ],
        }

    def exp_table(self):
        """The exponent table: eleven zeros and the tuning's high bytes.

        It shares its bytes with the tuning (it is read eleven entries below it),
        which is storage; section 3.2 materialises the values, not the bytes.
        """
        m, L = self.m, self.L
        return {
            "rank": 0,
            "rows": [{"value": m[L["exptabh"] + i]} for i in range(107)],
        }

    def tempo_stream(self):
        """The tempo program: eight rows the score's own commands write."""
        return {
            "rank": 0,
            "rows": [{"value": {"global": "tempo%d" % i}} for i in range(8)],
        }

    def chord_stream(self):
        """The chords: signed semitone steps, and the row each one goes on to.

        A loop byte goes back to the start of the chord the *voice* is playing,
        which no row knows, so the row reads it off the chord it names.
        """
        m, L = self.m, self.L
        n = L["tempotbl"] - L["chords"]
        start = {"tabcell": ["chordstart", {"cell": "curchord"}, "value"]}
        rows = []
        for k in range(n):
            b = m[L["chords"] + k]
            if b == 0x7E:
                rows.append({"raw": b, "value": {"trap": DEAD["chord.return"]}, "next": k})
            elif b == 0x7F:
                rows.append(
                    {
                        "raw": {"tabcell": ["chords", start, "raw"]},
                        "value": {"tabcell": ["chords", start, "value"]},
                        "next": {"add": [start, 1]},
                    }
                )
            else:
                rows.append({"raw": b, "value": signed(b), "next": k + 1})
        return {"rank": 0, "rows": rows}

    def chordstart_stream(self):
        """Where each chord begins: the tune's own table of chord starts."""
        m, L = self.m, self.L
        n = L["insptlo"] - L["chdptrlo"]
        return {
            "rank": 0,
            "rows": [{"value": m[L["chdptrlo"] + i]} for i in range(n)],
        }

    # ---- the instruments, and the three streams their records are -------------
    def ins_at(self, i):
        m, L = self.m, self.L
        return (m[L["insptlo"] + i] | m[L["inspthi"] + i] << 8) + (
            m[L["swp"]] | m[L["swp"] + 1] << 8
        )

    def ins_len(self, i):
        """One instrument's record: as far as the next record starts."""
        here = self.ins_at(i)
        n = self.L["inspthi"] - self.L["insptlo"]
        after = [x for x in (self.ins_at(j) for j in range(n)) if x > here]
        return (min(after) if after else self.L["chords"]) - here

    def rec(self, i, k):
        return self.m[self.ins_at(i) + k]

    def block(self, i, start, n):
        """One table of an instrument: three-byte rows to its own terminator."""
        out = []
        k = start
        while k < n:
            out.append(k)
            if self.rec(i, k) == 0xFF:
                break
            k += 3
        return out

    def build_streams(self, used, head):
        """Every instrument's tables, read three ways: waveform, pulse and filter.

        The rows are the instrument's own three-byte rows; ``head`` is where a
        voice's cursor already stands before any note has started one.
        """
        for i in sorted(used):
            at = self.ins_at(i)
            if at in self.base:
                self.base[i] = self.base[at]
                continue
            n = self.ins_len(i)
            offs = {
                "wave": [k for k in head.get(i, ()) if k < 0x10] + self.block(i, 0x10, n),
                "pulse": self.block(i, self.rec(i, 0x0A), n),
                "filter": self.block(i, self.rec(i, 0x0B), n),
            }
            self.base[at] = self.base[i] = {
                t: {k: len(self.rows[t]) + j for j, k in enumerate(o)} for t, o in offs.items()
            }
            for t, o in offs.items():
                for k in o:
                    self.rows[t].append(
                        getattr(self, t + "_row")(i, k, [self.rec(i, k + j) for j in range(3)])
                    )

    def row_of(self, i, slot, k):
        """The stream row one byte offset of an instrument is; else the table's end."""
        return self.base[i][slot].get(k, 1)

    def wave_row(self, i, k, b):  # noqa: C901 - one clause per row type
        """A waveform row: a waveform or a repeat count, then what it plays."""
        r = self.row_of(i, "wave", k)
        if b[0] == 0xFF:
            return {"next": r, "sets": [["!C", 1]]}
        if b[0] == 0xFE:  # the row the jump would take, and the trap that says it does not
            return {
                "next": self.row_of(i, "wave", b[1]),
                "detune": b[2],
                "sets": [["!C", 1], ["!dead", {"trap": DEAD["wave.jump"]}]],
            }
        sets = (
            [["@arpscnt", b[0]]] if b[0] < 0x10 else [["@wave", {"and": [b[0], {"cell": "gate"}]}]]
        )
        if b[1] == 0x7F:  # a chord step: the row plays again until the chord ends
            return {
                "next": r,
                "detune": b[2],
                "sets": sets
                + [
                    ["@detuner", {"tabcell": ["wave", r, "detune"]}],
                    ["@chordval", {"field": [{"add": [NOTE, CHORD]}, 0x7F]}],
                    ["!C", carry({"add": [NOTE, CHORD_RAW]})],
                    ["@chordpos", {"tabcell": ["chords", {"cell": "chordpos"}, "next"]}],
                ],
                "op": {"pitch": {"cell": "chordval"}},
            }
        if self.L["detune"] and b[2] != 0xFF:
            sets.append(["@detuner", {"tabcell": ["wave", r, "detune"]}])
        nxt = self.row_of(i, "wave", k + 3)
        if b[1] == 0x80:  # the row keeps the pitch it found
            return {"next": nxt, "detune": b[2], "sets": sets + [["!C", 1]]}
        if b[1] < 0x80:
            sets.append(["!C", carry({"add": [b[1], NOTE]})])
            op = {"pitch": b[1], "relative": True, "wrap": 0x7F}
        elif b[1] < 0xE0:
            sets.append(["!C", 0])
            op = {"pitch": b[1] & 0x7F}
        else:
            sets.append(["!C", carry({"add": [b[1], NOTE]})])
            op = {"pitch": b[1] - 0x100, "relative": True, "wrap": 0x7F}
        return {"next": nxt, "detune": b[2], "sets": sets, "op": op}

    def pulse_row(self, i, k, b):
        """A pulse row: a width to take, a sweep to run, a jump or a hold."""
        r = self.row_of(i, "pulse", k)
        nxt = self.row_of(i, "pulse", k + 3)
        tail = [["@pkbdtrk", b[2]]]
        if b[0] == 0xFF:
            return {"next": r}
        if b[0] == 0xFE:
            row = {"next": self.row_of(i, "pulse", b[1]), "track": b[2]}
            if b[1] != k and self.rec(i, b[1]) & 0x80:
                row["sets"] = [["!dead", {"trap": DEAD["pulse.jump"]}]]
            return row
        if b[0] < 0x80:
            return {
                "hold": b[0] + 1,
                "run": [{"acc": "pulse_step", "delta": signed(b[1])}],
                "sets": tail,
                "next": nxt,
            }
        return {"sets": [["@pw", ((b[0] & 0x7F) << 8) | b[1]]] + tail, "next": nxt}

    def filter_row(self, i, k, b):
        """A filter row: a band and a cutoff to take, a sweep to run, or a hold."""
        r = self.row_of(i, "filter", k)
        nxt = self.row_of(i, "filter", k + 3)
        tail = (
            [["#fswitch", b[2] & 0x0F], ["#ckbdtrk", 0]]
            if 0x80 <= b[2] < 0x90
            else [["#ckbdtrk", b[2]]]
        )
        if b[0] == 0xFF:
            return {"next": r}
        if b[0] == 0xFE:
            return {
                "next": self.row_of(i, "filter", b[1]),
                "track": b[2],
                "sets": [["!dead", {"trap": DEAD["filter.jump"]}]],
            }
        if b[0] < 0x80:
            return {
                "hold": b[0] + 1,
                "run": [{"acc": "cutoff_step", "delta": signed(b[1])}],
                "sets": tail,
                "next": nxt,
            }
        sets = [["#fltband", b[0] & 0x70], ["#resonib", (b[0] << 4) & 0xFF], ["#cutoff", b[1] << 3]]
        return {"sets": sets + tail, "next": nxt}

    # ---- the instruments ------------------------------------------------------
    def instruments(self, used):
        """Sixteen columns, read as adsr, a prelude, three stream entries and cells."""
        out = {}
        for i in sorted(used):
            ctrl, vib = self.rec(i, 0), self.rec(i, 5)
            typ = ctrl & 0x30
            out[str(i)] = {
                "ctrl": ctrl,
                "hr": [self.rec(i, 1), self.rec(i, 2)],
                "adsr": [self.adsr(self.rec(i, 3), 5), self.adsr(self.rec(i, 4), 6)],
                "vib": vib,
                "vibdelay": self.rec(i, 6),
                "arpsped": self.rec(i, 7),
                "chord": self.rec(i, 8),
                "transpose": signed(self.rec(i, 9)),
                "pw_index": self.rec(i, 0x0A),
                "flt_index": self.rec(i, 0x0B),
                "gate_off": [self.rec(i, k) for k in (0x0C, 0x0D, 0x0E)],
                "wave": self.rec(i, 0x0F),
                "vibracnt": 0 if typ == 0x30 else ((vib & 0x0F) >> (0 if typ == 0x20 else 1)),
                "prelude": {"stream": "hard_restart"},
                "on_note": inline(self.note_sets(i), UNTIED)
                + [{"when": UNTIED, "point": self.points(i)}],
                "accs": arms(),
            }
        return out

    def adsr(self, v, reg):
        """The envelope byte the chip is given; the slowdown remap, measured to be none."""
        m, L = self.m, self.L
        if L["adsr_offs"] is None:
            return v
        y = m[L["slowdownv"]]
        if reg == 6:
            out = v + m[L["adsr_exptb"] + ((m[L["adsr_offs"] + (v & 0x0F)] + y) & 0xFF)]
        else:
            hi = (m[L["expoff"] + ((m[L["adsr_offs"] + (v >> 4)] + y) & 0xFF)] & 0xF0) + v
            c, hi = hi > 0xFF, hi & 0xFF
            out = hi + m[L["adsr_exptb"] + ((m[L["adsr_offs"] + (hi & 0x0F)] + y + c) & 0xFF)]
        assert out & 0xFF == v, "the envelope remap is the identity at this slowdown"
        return v

    def new_instrument(self, i, bit):
        """A row that names an instrument clears the instrument's own keep bits."""
        return [] if not self.rec(i, 0) & bit else [["newins", "!=", 0]]

    def note_sets(self, i):  # noqa: C901 - one column of the instrument per clause
        """What a note start emits and moves, in the order the routine writes it."""
        m, L = self.m, self.L
        ctrl, vib = self.rec(i, 0), self.rec(i, 5)
        s = [["@slidevib", ctrl & 0x30]]
        if ctrl & 0x08:  # the first frame's waveform, sounded against the test bit
            hi = {"sid_base": "reader"} if L["hibug"] else NOTE
            s += [["freq_hi", {"shr": [{"tuned": hi}, 8]}], ["@wave", self.rec(i, 0x0F)]]
        s.append(["@gate", 0xFF])
        if L["arpscnt_reset"]:
            s.append(["@arpscnt", 0xFF])
        s += [
            ["@arpsped", self.rec(i, 7)],
            ["@videlcnt", self.rec(i, 6)],
            ["@vibfrequ", ((vib & 0x0F) << 1) & 0xFF],
            ["@vibracnt", {"ins": "vibracnt"}],
        ]
        s += self.amount((vib & 0xF0) >> 1)
        s += [
            ["@curchord", self.rec(i, 8)],
            ["@chordpos", m[L["chdptrlo"] + self.rec(i, 8)]],
        ]
        s += self.route(i)
        for reg, k in ((5, 3), (6, 4)) if L["commit_order"][0] == "ad" else ((6, 4), (5, 3)):
            s.append([{5: "ad", 6: "sr"}[reg], self.adsr(self.rec(i, k), reg)])
        if any(self.rec(i, k) for k in (0x0C, 0x0D, 0x0E)):
            s.append(["!dead", {"trap": DEAD["gate.pointer"]}])
        return s

    def amount(self, a):
        """The modulation amount: pitch-proportional, through the exponent table."""
        if a == 0:
            return [["@freqmod", 0]]
        t = {"add": [(a >> 1) + (a & 1), NOTE]}
        if self.L["slowdownv"] is not None:  # the slowdown build takes the borrow off
            y = {"field": [{"sub": [t, {"sub": [1, carry(t)]}]}, 0xFF]}
            return [
                ["@freqmod", {"tabcell": ["exp", y, "value"]}, [[y, "<", 0x6B]]],
                ["@freqmod", {"tuned": {"sub": [y, 0x6B]}}, [[y, ">=", 0x6B], [y, "<", 0x80]]],
                ["@freqmod", 0, [[y, ">=", 0x80]]],
            ]
        y = {"field": [t, 0xFF]}
        return [
            ["@freqmod", {"tabcell": ["exp", y, "value"]}, [[y, "<", 0x6B]]],
            ["@freqmod", {"tuned": {"sub": [y, 0x6B]}}, [[y, ">=", 0x6B], [y, "<", 0xCB]]],
            ["@freqmod", {"trap": DEAD["amount.clamp"]}, [[y, ">=", 0xCB]]],
        ]

    def route(self, i):
        """Which voice owns the filter, and which voices the filter is over."""
        when = self.new_instrument(i, 0x80)
        a = self.rec(i, self.rec(i, 0x0B))
        bit = {"tabcell": ["voice_bit", {"cell": "voice_index"}, "value"]}
        mask = {"tabcell": ["voice_bit", {"cell": "voice_index"}, "mask"]}
        if a == 0xFF:
            return [["#fswitch", {"and": [{"global": "fswitch"}, mask]}, when]]
        s = [["#fswitch", {"or": [{"global": "fswitch"}, bit]}, when]]
        if a:  # a filter row of its own: this voice takes the filter
            s.insert(0, ["#fltctrl", {"cell": "voice_index"}, when])
        return s

    def points(self, i):
        """The three streams a note start re-points, where the instrument resets them."""
        p = [["wave", self.row_of(i, "wave", 0x10), False]]
        p.append(
            [
                "pulse",
                self.row_of(i, "pulse", self.rec(i, 0x0A)),
                False,
                self.new_instrument(i, 0x40),
            ]
        )
        b = self.rec(i, 0x0B)
        a = self.rec(i, b)
        when = self.new_instrument(i, 0x80)
        if a not in (0, 0xFF):
            p.append(["filter", self.row_of(i, "filter", b), True, when])
        elif a == 0xFF and self.L["ownercheck"]:
            p.append(["filter", self.row_of(i, "filter", b), True, when + OWNS])
        return p

    # ---- the score ------------------------------------------------------------
    def order(self, v):
        """One voice's order program: the pattern steps, and the columns beside them."""
        m, L = self.m, self.L
        base = L["orderlist"][v]
        play, at, col = [], {}, {"transpose": 0}
        y = 0
        while True:
            at[y] = len(play)
            b = m[base + y]
            if b == 0xFF:
                return {"play": play, "end": {"jump": at[m[base + y + 1]]}}
            if b == 0xFE:
                return {"play": play, "end": "stop"}
            y += 1
            if b >= 0x80:
                if b < 0xA0:
                    col["transpose"] = b - 0x90
                elif b < 0xB0:
                    col["vol"] = b & 0x0F
                elif b < 0xF0:
                    col["tempo"] = b - 0xB0
                continue
            play.append(dict(col, pattern=b))

    def pattern(self, n):  # noqa: C901 - one clause per token of the note column
        """One pattern, materialised: no packed byte and no byte cursor survives."""
        m, L = self.m, self.L
        base = (m[L["pptrlo"] + n] | m[L["pptrhi"] + n] << 8) + (m[L["swp"]] | m[L["swp"] + 1] << 8)
        out, y = [], 0
        while m[base + y] != 0xFF:
            e = {
                "dur": 1,
                "sounds": False,
                "tie": False,
                "gate": None,
                "note": None,
                "ins": None,
                "arm": None,
            }
            cmds = []
            b = m[base + y]
            y += 1
            note = b & 0x7F
            if not b & 0x80 and 0x70 <= b < 0x78:
                e["dur"] = b - 0x6E
            elif not note:
                pass
            elif note < 0x60:
                e["note"], e["sounds"] = note, True
            elif note < 0x78:  # the note column's own vibrato depth
                cmds.append(self.name("smallfx", 8, note & 0x0F))
            elif note < 0x7D:
                cmds.append(self.name("notefx", note - 0x78, None))
            else:
                e["gate"] = "on" if note == 0x7D else "off"
            fx, tail = None, []
            if b & 0x80:
                ib = m[base + y]
                y += 1
                ins = ib & 0x7F
                if ins == 0x3F:
                    e["tie"] = True
                elif ins >= 0x40:
                    tail.append(self.name("smallfx", ins >> 4, ins & 0x0F))
                elif ins:
                    e["ins"] = ins
                if ib & 0x80:
                    fx = m[base + y]
                    y += 1
                    if fx >= 0x20:
                        tail.append(self.name("smallfx", fx >> 4, fx & 0x0F))
                    else:
                        if fx == 3:  # a portamento re-targets the note, it does not retrigger
                            e["tie"] = True
                        tail.append(self.name("bigfx", fx, m[base + y]))
                        y += 1
                if ins == 0x3F and fx != 3 and e["sounds"]:
                    cmds.append(self.name("legato", 0, None))
            e["arm"] = cmds + tail or None
            out.append(e)
        return {"events": out}

    def score(self):
        """The three order programs and the patterns they reach."""
        orders = [self.order(v) for v in range(3)]
        used = {p["pattern"] for o in orders for p in o["play"]}
        pats = {str(p): self.pattern(p) for p in sorted(used)}
        return {"orders": orders, "patterns": pats, "commands": {}}

    def target(self, kind, idx):
        """Where the tune's own dispatch sends this effect; a bare return is none."""
        m, L = self.m, self.L
        if kind == "bigfx":
            return m[L["bigfx"] + 2 * idx] | m[L["bigfx"] + 2 * idx + 1] << 8
        base, at = (
            (L["smalljmp"], L["smallfx"] + idx)
            if kind == "smallfx"
            else (L["notejmp"], L["notefx"] + idx)
        )
        return base + signed(m[at])

    def name(self, kind, idx, val):
        """One row command, interned under what it does; the score names it."""
        if kind == "legato":
            what = "legato"
        else:
            what = {"notefx": NOTEFX, "smallfx": SMALLFX, "bigfx": BIGFX}[kind][
                idx - {"notefx": 0, "smallfx": 2, "bigfx": 1}[kind]
            ]
            if self.m[self.target(kind, idx)] == 0x60:  # this build compiles the effect out
                what, val = "nop", (idx << 4 | val if kind == "smallfx" else val)
        key = what if val is None else "%s:%02X" % (what, val)
        self.cmds.setdefault(key, rows_of(self.command(what, val)))
        return key

    def command(self, what, v):  # noqa: C901 - one clause per command
        """One row command, unpacked into section 3.6's own fields."""
        m, L = self.m, self.L
        wave, pw, cut = {"cell": "wave"}, {"cell": "pw"}, {"global": "cutoff"}
        adsr = {
            "attack": ("ad", "adsr.0", True),
            "decay": ("ad", "adsr.0", False),
            "sustain": ("sr", "adsr.1", True),
            "release": ("sr", "adsr.1", False),
        }
        bit = {
            "sync.on": (0x02, True),
            "sync.off": (0xFD, False),
            "ring.on": (0x04, True),
            "ring.off": (0xFB, False),
        }
        if what == "nop":
            return {}
        if what in adsr:
            reg, col, hi = adsr[what]
            return {"sets": [[reg, nibble({"ins": col}, hi, v)]]}
        if what in bit:
            k, on = bit[what]
            return {"sets": [["@wave", {"or" if on else "and": [wave, k]}]]}
        one = {
            "wave.high": lambda: [["@wave", nibble(wave, True, v)]],
            "wave.low": lambda: [["@wave", nibble(wave, False, v)]],
            "wave": lambda: [["@wave", v]],
            "chord": lambda: [["@curchord", v], ["@chordpos", m[L["chdptrlo"] + v]]],
            "vibrato.rate": lambda: [["@vibfrequ", (v << 1) & 0xFF]],
            "vibrato.type": lambda: [["@slidevib", v & 0x30]],
            "volume": lambda: [["#mainvol", v]],
            "filter.band": lambda: [["#fltband", (v << 4) & 0xFF]],
            "filter.resonance": lambda: [["#resonib", (v << 4) & 0xFF]],
            "filter.route": lambda: [["#fswitch", v & 0x0F], ["#resonib", v & 0xF0]],
            "filter.shift": lambda: [["#flshift", v]],
            "filter.cutoff": lambda: [["#cutoff", {"or": [{"and": [cut, 7]}, (v << 3) & 0x7FF]}]],
            "arpeggio.speed": lambda: [["@arpsped", v], ["@arpscnt", 0xFF]],
            "detune": lambda: [["@detuner", v]],
            "detune.coarse": lambda: [["@detuner", (v << 3) & 0xFF]],
            "pulse.high": lambda: [["@pw", {"or": [{"and": [pw, 0xFF]}, (v & 0x0F) << 8]}]],
            "legato": lambda: [
                ["@freqmod", {"or": [{"and": [{"cell": "freqmod"}, 0xFF]}, 0x7F00]}],
                ["@slidevib", 0x83],
            ],
            "ad": lambda: [["ad", v]],
            "sr": lambda: [["sr", v]],
            "slide.up": lambda: [["@slidevib", 0x81]] + self.amount(v),
            "slide.down": lambda: [["@slidevib", 0x82]] + self.amount(v),
            "portamento": lambda: [["@slidevib", 0x83]] + self.amount(v),
            "porta.note": lambda: [["@slidevib", 0xFF]] + self.amount(0x6E),
            "vibrato.depth": lambda: self.setvib(0, v << 3, depth=True),
            "vibrato": lambda: self.setvib(v, (v & 0xF0) >> 1),
        }
        if what in one:
            return {"sets": one[what]()}
        if what in FXPOINT:
            raise AssertionError("command residue: %s: %s" % (what, DEAD["fx.pointer"]))
        if what == "tempo":
            return {"sets": [["#tempo0", v | 0x80]], "all": [["@tmppos", 0], ["@tmpptr", 0]]}
        if what == "tempo.funk":
            return {
                "sets": [["#tempo1", (v & 0x0F) | 0x80], ["#tempo0", v >> 4]],
                "all": [["@tmppos", 0], ["@tmpptr", 0]],
            }
        if what == "tempo.program":
            t = m[L["temptrlo"] + v]
            return {} if not v else {"all": [["@tmppos", t], ["@tmpptr", t]]}
        raise AssertionError("command residue: %s is no command of section 3.6" % what)

    def setvib(self, a, amp, depth=False):
        """A vibrato the score sets: its rate, its phase and its depth."""
        rate = (
            {
                "field": [
                    {"add": [{"and": [{"ins": "vib"}, 0x0F]}, {"and": [{"ins": "vib"}, 0x0F]}]},
                    0xFF,
                ]
            }
            if depth
            else ((a & 0x0F) << 1) & 0xFF
        )
        cnt = (
            [["@vibracnt", {"ins": "vibracnt"}]]
            if depth
            else [
                ["@vibracnt", 0, [[{"and": [{"ins": "ctrl"}, 0x30]}, "==", 0x30]]],
                ["@vibracnt", a & 0x0F, [[{"and": [{"ins": "ctrl"}, 0x30]}, "==", 0x20]]],
                ["@vibracnt", (a & 0x0F) >> 1, [[{"and": [{"ins": "ctrl"}, 0x30]}, "<", 0x20]]],
            ]
        )
        return (
            [["@slidevib", {"and": [{"ins": "ctrl"}, 0x30]}], ["@vibfrequ", rate]]
            + cnt
            + self.amount(amp)
        )

    # ---- the streams the player itself runs -----------------------------------
    def prelude_stream(self):
        """The hard restart: two clock steps of the row the fetch staged."""
        ctrl = {"insrec": ["hrins", "ctrl"]}
        pend = [{"cell": "pending"}, "!=", 0]
        rows = [
            {
                "when": [pend, [{"cell": "phase"}, "==", ph], [{"and": [ctrl, bit]}, "!=", 0]],
                "sets": [["@gate", 0xFE], ["@wave", {"and": [{"cell": "wave"}, 0xFE]}]]
                + [
                    [t, {"insrec": ["hrins", "hr.%d" % {"ad": 0, "sr": 1}[t]]}]
                    for t in self.L["commit_order"][:2]
                ],
            }
            for ph, bit in ((0, 2), (1, 1))
        ]
        rows.append(
            {
                "when": [pend, [{"cell": "phase"}, "<", 2], [{"and": [ctrl, 4]}, "!=", 0]],
                "sets": [["!dead", {"trap": DEAD["hr.mute"]}]],
            }
        )
        return {"rank": 0, "rows": rows}

    def fixed(self):
        """The rows the voice emits itself: its gate, its pulse and its pitch."""
        m, L = self.m, self.L
        pw, gate = {"cell": "pw"}, {"payload": "gate"}
        i = {"add": [{"cell": "pkbdtrk"}, NOTE]}
        j = {"field": [i, 0xFF]}
        d = {
            "sub": [
                {
                    "sub": [
                        {"tabcell": ["exp", j, "value"]},
                        {"tabcell": ["exp", {"field": [{"sub": [j, 1]}, 0xFF]}, "value"]},
                    ]
                },
                {"sub": [1, carry(i)]},
            ]
        }
        hi = {
            "add": [
                {"add": [{"field": [d, 0xFF]}, {"shr": [pw, 8]}]},
                borrow(d),
            ]
        }
        out = {
            "exit": {
                "rows": [
                    {"when": LIVE, "sets": [["ctrl", {"cell": "wave"}]]},
                    {
                        "when": [[ALIVE, "==", 0], [{"cell": "phase"}, "==", 2]],
                        "sets": [["ctrl", {"cell": "wave"}]],
                    },
                ],
            },
            "pitch_row": {
                "rows": [{"when": OWNS, "sets": [["#ownerpitch", NOTE]]}],
            },
            "gate_row": {
                "rows": [
                    {
                        "sets": [
                            ["@gate", gate],
                            [
                                "@wave",
                                {"or": [{"and": [{"cell": "wave"}, gate]}, {"and": [gate, 1]}]},
                            ],
                        ]
                    }
                ],
            },
            "pw_out": {
                "rank": 15,
                "all": True,
                "when": RUN,
                "rows": [
                    {
                        "when": [[{"cell": "pkbdtrk"}, "==", 0]],
                        "sets": [
                            ["pw_hi", {"shr": [pw, 8]}],
                            ["pw_lo", {"field": [pw, 0xFF]}],
                            ["!C", 0],
                        ],
                    },
                    {
                        "when": [[{"cell": "pkbdtrk"}, "!=", 0]],
                        "sets": [
                            ["pw_hi", hi],
                            ["pw_lo", {"field": [pw, 0xFF]}],
                            ["!C", carry(hi)],
                        ],
                    },
                ],
            },
            "pitch_out": {
                "rank": 25,
                "all": True,
                "when": LIVE,
                "rows": [{"sets": [["pitch", self.pitch_out()]]}],
            },
            "voice_bit": {
                "rows": [
                    {"value": m[L["cvar"] + 7 * v], "mask": m[L["cvar"] + 1 + 7 * v]}
                    for v in range(3)
                ],
            },
            "hard_restart": self.prelude_stream(),
            "exp": self.exp_table(),
            "chords": self.chord_stream(),
            "chordstart": self.chordstart_stream(),
            "tempo": self.tempo_stream(),
        }
        return out

    def pitch_out(self):
        """What the voice writes to its frequency: its cell, and what detunes it."""
        if not self.L["detune"]:
            return {"cell": "freq"}
        return {"add": [{"add": [{"cell": "freq"}, {"cell": "detuner"}]}, {"flag": "C"}]}

    def globals(self):
        """The one global channel: the filter and the master volume it commits."""
        cut = {"shr": [{"global": "cutoff"}, 3]}
        if self.L["flshift"] is not None:
            cut = {"add": [cut, {"global": "flshift"}]}
        i = {"add": [{"global": "ckbdtrk"}, {"global": "ownerpitch"}]}
        track = {
            "add": [
                {"add": [{"tabcell": ["exp", {"field": [i, 0xFF]}, "value"]}, carry(i)]},
                cut,
            ]
        }
        return {
            "streams": [],
            "flags": {"C": {"default": 0}},
            "stop_writes": [],
            "commit": [
                [23, {"or": [{"global": "fswitch"}, {"global": "resonib"}]}],
                [24, {"or": [{"global": "mainvol"}, {"global": "fltband"}]}],
                [22, cut, [[{"global": "ckbdtrk"}, "==", 0]]],
                [22, track, [[{"global": "ckbdtrk"}, "!=", 0]]],
                [21, {"and": [{"global": "cutoff"}, 7]}],
            ],
        }

    # ---- the whole object -----------------------------------------------------
    def build(self):
        """Section 3's seven sections, and the state the tune's init leaves."""
        m, L = self.m, self.L
        score = self.score()
        used = {0} | {
            e["ins"]
            for p in score["patterns"].values()
            for e in p["events"]
            if e["ins"] is not None
        }
        head = {}
        for v in range(3):
            i, k = m[L["ins"] + 7 * v], m[L["wftpos"] + 7 * v]
            head.setdefault(i, set()).update(range(k, 0x10, 3))
        self.build_streams(used, {i: sorted(k) for i, k in head.items()})
        streams = self.fixed()
        streams["wave"] = {
            "rank": 20,
            "when": LIVE,
            "rows": self.rows["wave"],
            "rate": {"cell": "arpscnt", "reload": {"and": [{"cell": "arpsped"}, 0x3F]}},
            "beyond": self.beyond(),
        }
        streams["pulse"] = {
            "rank": 10,
            "when": RUN,
            "epoch": "entry",
            "rows": self.rows["pulse"],
        }
        streams["filter"] = {
            "rank": 5,
            "when": RUN + OWNS,
            "epoch": "entry",
            "rows": self.rows["filter"],
        }
        score["commands"] = dict(sorted(self.cmds.items()))
        return {
            "$trackerprog": 1,
            "meta": self.meta(),
            "pitch": self.pitch(),
            "streams": streams,
            "accs": accs(),
            "instruments": self.instruments(used),
            "score": score,
            "globals": self.globals(),
            "state0": self.state0(),
        }

    def state0(self):
        """What init leaves: the cells, the cursors and the global channel."""
        m, L = self.m, self.L
        cells = {k: [m[L[k] + 7 * v] for v in range(3)] for k in CELLS}
        for k in ("freq", "pw", "freqmod"):
            cells[k] = [m[L[k] + 7 * v] | m[L[k] + 1 + 7 * v] << 8 for v in range(3)]
        cells["pending"] = [0] * 3
        cells["hrins"] = list(cells["ins"])
        cells["chordval"] = [0] * 3
        assert not any(m[L["wftpos"] + 7 * v] for v in range(3)), "the tables start at the head"
        return {
            "cells": cells,
            "ins": cells["ins"],
            "wave": cells["wave"],
            "globals": {
                "fswitch": m[L["fswitch"]],
                "resonib": m[L["resonib"]],
                "mainvol": m[L["mainvol"]],
                "fltband": m[L["fltband"]],
                "ckbdtrk": m[L["ckbdtrk"]],
                "cutoff": m[L["ctfhgho"]] << 3 | (m[L["ctflgho"]] & 7),
                "fltctrl": 0x0F,
                "ownerpitch": 0,
                "flshift": 0 if L["flshift"] is None else m[L["flshift"]],
                **{"tempo%d" % i: m[L["tempotbl"] + i] for i in range(8)},
            },
            "cursors": {
                "wave": [
                    {"row": self.row_of(cells["ins"][v], "wave", m[L["wftpos"] + 7 * v]), "hold": 0}
                    for v in range(3)
                ],
                "pulse": [{"row": 0, "hold": m[L["pweepcnt"] + 7 * v]} for v in range(3)],
                "pitch_out": [{"row": 1, "hold": 0} for _ in range(3)],
                "pw_out": [{"row": 1, "hold": 0} for _ in range(3)],
            },
            "gcursors": {"filter": {"row": 0, "hold": m[L["cwepcnt"]]}},
        }

    def meta(self):
        """Section 3.1, and the data a family's whole tick shape reduces to."""
        L = self.L
        return {
            "tune": Path(self.path).name,
            "family": "SID Wizard",
            "song": self.song,
            "cycles_per_tick": 19656,
            "voices": 3,
            "voice_order": [2, 1, 0],
            "commit_order": L["commit_order"],
            "wide": list(WIDE),
            "tempo": {
                "cell": "spdcnt",
                "step": 1,
                "boundary": [[{"cell": "phase"}, "==", 2]],
                "fetch": [[{"cell": "phase"}, "==", 0]],
                "early": [[{"cell": "phase"}, "<", 2]],
                "reset": [
                    {
                        "when": [
                            [{"bit": [TEMPO, 7]}, "==", 0],
                            [{"cell": "spdcnt"}, "==", {"field": [TEMPO, 0x7F]}],
                        ],
                        "sets": [["@spdcnt", 0], ["@tmppos", {"add": [{"cell": "tmppos"}, 1]}]],
                    },
                    {
                        "when": [
                            [{"bit": [TEMPO, 7]}, "!=", 0],
                            [{"cell": "spdcnt"}, ">=", {"field": [TEMPO, 0x7F]}],
                        ],
                        "sets": [["@spdcnt", 0], ["@tmppos", {"cell": "tmpptr"}]],
                    },
                ],
            },
            "tick": ["fetch", "prelude", "commit", "row", "commit", "machine", {"stream": "exit"}],
            "row_consumes_tick": [["sounds", "!=", 0]],
            "row_command": "spent",
            "stage": [{"sets": [["@hrins", {"payload": "ins"}]]}],
            "stage_sounds": "pending",
            "row": [
                {"sets": [["@pending", 0]]},
                {"ins": True},
                {"stream": "gate_row", "when": [["gate_stmt", "!=", 0]]},
                {"note": True, "when": [["sounds", "!=", 0]]},
                {"stream": "pitch_row", "when": [["sounds", "!=", 0]]},
                {"commands": True},
            ],
            "pitch_target": "@freq",
            **({"prologue": {"rows": []}} if L["slowdown"] is not None else {}),
        }


UNTIED = [["tie", "==", 0]]  # a row a tie does not admit


def rows_of(c):
    """A command's writes as an inline stream: one guard shape, section 3.3's."""
    out = inline(c.pop("sets", ()), [])
    if "point" in c:
        out = out or [{"when": []}]
        out[-1]["point"] = c.pop("point")
    return dict(c, rows=out) if out else c


def inline(sets, when):
    """Sets as an inline stream: one row per run of them sharing a guard (section 3.3).

    A set carries its guard beside it in the routine's own text; a stream row
    carries one guard for its whole row, so a run of sets under one guard is one
    row and the schema keeps a single shape for a guard.
    """
    out = []
    for t, *rest in sets:
        g = when + (rest[1] if len(rest) > 1 else [])
        if not out or out[-1]["when"] != g:
            out.append({"when": g, "sets": []})
        out[-1]["sets"].append([t, rest[0]])
    return out


SV = {"cell": "slidevib"}
DELAY = [[SV, "!=", 0], [{"and": [SV, 0x80]}, "==", 0]]
LATE = [[{"and": [{"cell": "videlcnt"}, 0x80]}, "!=", 0]]


def arms():
    """The modulations a note-on arms: one arm per value of the tune's own selector."""
    return [
        {"acc": "freqmod_step", "when": RUN + [[SV, "==", 0]]},
        {"acc": "vib_phase", "when": RUN + [[SV, "==", 0]]},
        {"acc": "vibrato", "when": RUN + [[SV, "==", 0]]},
        {
            "acc": "vib_delay",
            "when": RUN + DELAY + [[{"and": [{"cell": "videlcnt"}, 0x80]}, "==", 0]],
        },
        {"acc": "vib_phase", "when": RUN + DELAY + LATE},
        {"acc": "vibrato", "when": RUN + DELAY + LATE},
        {"acc": "slide_up", "when": RUN + [[SV, "==", 0x81]]},
        {"acc": "slide_down", "when": RUN + [[SV, "==", 0x82]]},
        {"acc": "toneporta", "when": RUN + [[SV, ">=", 0x83]]},
    ]


def accs():
    """Section 5's records: every one a row of section 5's own table."""
    freq = {
        "cell": "freq",
        "target": "freq",
        "width": 16,
        "delta": {"cell": "freqmod"},
        "policy": "wrap",
        "rate": 1,
        "scope": "voice",
        "produce": [],
        "bound": {
            "from": "projected",
            "interval": [0, 0xFFFF],
            "witness": "the sixteen-bit store; the modulation has no target",
        },
    }
    vc, vf = {"cell": "vibracnt"}, {"cell": "vibfrequ"}
    return {
        "freqmod_step": {
            "rank": 0,
            "cell": "freqmod",
            "target": "note",
            "width": 16,
            "delta": {"cell": "videlcnt"},
            "policy": "wrap",
            "rate": 1,
            "scope": "voice",
            "produce": [],
            "bound": {
                "from": "projected",
                "interval": [0, 0xFFFF],
                "witness": "the sixteen-bit store",
            },
        },
        "vib_phase": {
            "rank": 1,
            "cell": "vibracnt",
            "target": "note",
            "width": 8,
            "delta": {"const": -1},
            "policy": {"reload": vf, "when": [[vc, "==", 0]]},
            "rate": 1,
            "scope": "voice",
            "produce": [],
            "bound": {
                "from": "proved",
                "interval": [0, 0xFF],
                "witness": "the reload at zero, from the vibrato's own period",
            },
        },
        "vib_delay": {
            "rank": 3,  # the delay's own step is read at its entry, so it runs last
            "cell": "videlcnt",
            "target": "note",
            "width": 8,
            "delta": {"const": -1},
            "policy": "wrap",
            "rate": 1,
            "scope": "voice",
            "produce": [],
            "bound": {"from": "proved", "interval": [0, 0xFF], "witness": "the arm's own guard"},
        },
        "vibrato": dict(
            freq,
            rank=2,
            phase=borrow({"sub": [{"field": [{"add": [vc, vc]}, 0xFF]}, vf]}),
        ),
        "slide_up": dict(freq, rank=2, phase={"const": 0}),
        "slide_down": dict(freq, rank=2, phase={"const": 1}),
        "toneporta": dict(
            freq,
            rank=2,
            policy={"clamp": {"notefreq": None}, "edge": 1},
            bound={
                "from": "proved",
                "interval": [0, 0xFFFF],
                "witness": "the tuning at the voice's note; the step cannot pass it",
            },
        ),
        "pulse_step": {
            "rank": 10,
            "cell": "pw",
            "target": "pw",
            "width": 16,
            "delta": {"const": "delta"},
            "policy": "wrap",
            "rate": 1,
            "scope": "voice",
            "produce": [],
            "bound": {
                "from": "projected",
                "interval": [0, 0xFFFF],
                "witness": "the store is 16-bit; the chip sees 12",
            },
        },
        "cutoff_step": {
            "rank": 5,
            "cell": "#cutoff",
            "target": "split(3, 8)",
            "width": 11,
            "delta": {"const": "delta"},
            "policy": "wrap",
            "rate": 1,
            "scope": "global",
            "produce": [],
            "bound": {
                "from": "projected",
                "interval": [0, 0x7FF],
                "witness": "the chip's own 3+8 split of the cutoff",
            },
        },
    }


def build(path, song=0):
    """The trackerprog object for one SID Wizard tune."""
    return Tune(path, song).build()


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
    """Re-verify the inherited claim on the render: the horizon replays itself."""
    n, p = loop["first_repeat"], loop["period"]
    w = render(obj, n + p)
    return w[n - p : n] == w[n : n + p]


def reference(path, song, ticks):
    """The oracle: the tune's own player on the PcodeVM, per-tick SID writes."""
    init, play = entries(path)
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
    ap.add_argument("--ticks", type=int, default=8084)
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
