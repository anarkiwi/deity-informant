#!/usr/bin/env python3
"""JCH NewPlayer V20 as a trackerprog, transliterated by hand.

Not a lift, a reading: docs/prototype-jch.md and playroutine-anatomy.md section
2 restated in the trackerprog's vocabulary and rendered by the universal player.
docs/prototype-jch-trackerprog.md is the mapping.  V20 is a code template -- the
two builds differ only in their table operands -- so every datum here is located
by the operand of the instruction that reads it, never by an address.
"""

import argparse
import hashlib
import json
import pickle
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
from deity_informant.trackerprog.refuse import Refusal  # noqa: E402
from deity_informant.tuneprog.machine import MachineImage, find_entries, port_bank  # noqa: E402
from deity_informant.vm import PcodeVM, run_sub  # noqa: E402

VOICES = 3
# One signature per block of the player, over the image the tick sees: every
# datum is the operand of the instruction that reads or writes it.  The blocks
# are the V20's own -- the tick, the voice loop, the prefetch, the pattern
# command dispatch, the row commit, the three column programs, the effects and
# the write-out -- and the wrapper's five, which only one build carries.
SIGS = {
    "tick": (
        [
            "A5 .. 48 A5 .. 48 CE .. .. 10 1D AD .. .. 8D .. .. C9 02 B0 13 AC .. .."
            " B9 .. .. 8D .. .. CE .. .. 10 05 A9 01 8D .. .."
        ],
        {"phase": ("w", 7), "speed": ("w", 12)},
    ),
    "voiceloop": (
        [
            "A2 02 BD .. .. D0 03 4C .. .. AD .. .. F0 0C C9 02 D0 0D BD .. .. F0 0E"
            " 4C .. .. DE .. .. 30 03 4C .. .. 4C .. .. BD .. .."
        ],
        {"enable": ("w", 3)},
    ),
    "prefetch": (
        [
            "BD .. .. 85 .. BD .. .. 85 .. A0 00 98 9D .. .. B1 .. 10 0F 0A 9D .. .. FE .. .."
            " D0 03 FE .. .. C8 B1 .. A8 B9 .. .. 85 .. B9 .. .. 85 .. BC .. .. B1 .. 10 03"
            " 4C .. .. F0 29 C9 7E F0 1A 9D .. .. BD .. .. D0 05 A9 00 9D .. .. BD .. .. D0 0B"
            " A9 00 9D .. .. 4C .. .. FE .. .. A9 FF 9D .. .. 4C .. .. FE .. .. BD .. .. C9 FE"
            " F0 13 A9 FE 9D .. .. BC .. .. B9 .. .. D9 .. .. F0 03 9D .. .. FE .. .. BC .. .."
            " B1 .. C9 7F D0 3F A9 00 9D .. .. A8 BD .. .. 18 69 01 9D .. .. 85 .. BD .. .."
            " 69 00 9D .. .. 85 .. B1 .. C9 FF D0 0C BD .. .. 9D .. .. BD .. .. 9D .. .. C9 FE"
            " D0 0E A9 00 9D .. .. BC .. .. 99 .. .. 4C .. .. BD .. .. D0 22 A9 FE 9D .. .."
            " BD .. .. F0 18 BC .. .. AD .. .. 99 .. .. 9D .. .. AD .. .. 99 .. .. 9D .. .."
            " 4C .. .. AD .. .. F0 08 A9 01 9D .. .. 4C .. .. 4C .. .."
        ],
        {
            "optr": ("w", 1),
            "optr_hi": ("w", 6),
            "tie": ("w", 14),
            "s_xpose": ("w", 22),
            "patlo": ("w", 37),
            "pathi": ("w", 42),
            "rowcur": ("w", 47),
            "s_note": ("w", 63),
            "cmd_slide": ("w", 66),
            "s_slide": ("w", 73),
            "cmd_vib": ("w", 76),
            "s_vib": ("w", 83),
            "s_gate": ("w", 94),
            "gatemask": ("w", 103),
            "ins7": ("w", 118),
            "ins6": ("w", 121),
            "wavecur": ("w", 126),
            "ostart": ("w", 174),
            "ostart_hi": ("w", 180),
            "hr_ad": ("w", 222),
            "ad": ("w", 228),
            "hr_sr": ("w", 231),
            "sr": ("w", 237),
            "nofx": ("w", 243),
            "noeff": ("w", 250),
        },
    ),
    "dispatch": (
        [
            "48 29 E0 C9 80 D0 13 68 48 29 10 9D .. .. 68 29 0F 9D .. .. FE .. .. 4C .. .."
            " C9 A0 D0 14 68 0A 0A 0A 9D .. .. A8 B9 .. .. 9D .. .. FE .. .. 4C .. .. 68 29 3F"
            " 0A A8 B9 .. .. 48 29 0F 8D .. .. 68 29 F0 C9 30 B0 1B 29 20 9D .. .. AD .. .."
            " 9D .. .. B9 .. .. 9D .. .. A9 01 9D .. .. 9D .. .. D0 CA C9 60 D0 36 A9 01 9D .. .."
            " 9D .. .. AD .. .. 9D .. .. B9 .. .. 48 4A 4A 4A 4A 9D .. .. 38 E9 01 9D .. .."
            " A9 00 9D .. .. 9D .. .. 9D .. .. 9D .. .. 68 29 0F 9D .. .. 4C .. .. C9 E0 D0 09"
            " B9 .. .. 8D .. .. 4C .. .. C9 F0 D0 09 B9 .. .. 8D .. .. 4C .. .. C9 90 D0 09"
            " B9 .. .. 9D .. .. 4C .. .. B9 .. .. 8D .. .. B9 .. .. 29 1F 0A 0A 0A A8 AD .. .."
            " 99 .. .. 99 .. .. 4C .. .."
        ],
        {
            "s_dur": ("w", 18),
            "s_ins": ("w", 35),
            "ins1": ("w", 39),
            "srover": ("w", 42),
            "cmdtab": ("w", 56),
            "sdir": ("w", 74),
            "sstep_hi": ("w", 80),
            "cmdtab_b": ("w", 83),
            "sstep": ("w", 86),
            "vinc": ("w", 114),
            "vreload": ("w", 125),
            "vtimer": ("w", 131),
            "vdir": ("w", 136),
            "vramp": ("w", 139),
            "vacc": ("w", 142),
            "vacc_hi": ("w", 145),
            "vshift": ("w", 151),
        },
    ),
    "commit": (
        [
            "BD .. .. 9D .. .. BD .. .. 9D .. .. BD .. .. 9D .. .. BD .. .. 9D .. .. BD .. .."
            " 9D .. .. BD .. .. 9D .. .. D0 06 9D .. .. 9D .. .. BD .. .. 9D .. .. BD .. .."
            " F0 14 4C .. .. A9 00 9D .. .. 9D .. .. BC .. .. B9 .. .. 9D .. .. BC .. .."
            " B9 .. .. 9D .. .. B9 .. .. 48 29 80 9D .. .. 68 29 0F 9D .. .. 9D .. .. B9 .. .."
            " 9D .. .. A8 B9 .. .. C9 FF F0 0C 48 29 F0 9D .. .. 68 29 0F 9D .. .. B9 .. .."
            " 48 29 80 9D .. .. 68 29 7F 9D .. .. BC .. .. B9 .. .. 48 29 F0 8D .. .. 68 A0 00"
            " 29 0F F0 20 C9 08 F0 1B 0A 0A 0A 0A 8D .. .. 0D .. .. 8D .. .. C8 AD .. .. 29 0F"
            " 1D .. .. 0D .. .. D0 07 C8 AD .. .. 3D .. .. 8D .. .. 8D .. .. C0 01 D0 1A"
            " BC .. .. B9 .. .. 8D .. .. A8 B9 .. .. C9 FF F0 03 8D .. .. B9 .. .. 8D .. .."
            " BC .. .. B9 .. .. BC .. .. 99 .. .. 9D .. .. BC .. .. B9 .. .. DD .. .. F0 03"
            " BD .. .. BC .. .. 99 .. .. 9D .. .. A9 09 99 .. .. 4C .. .."
        ],
        {
            "livegate": ("w", 4),
            "note": ("w", 10),
            "xpose": ("w", 16),
            "vib_on": ("w", 22),
            "ins": ("w", 28),
            "slide_on": ("w", 34),
            "sacc": ("w", 39),
            "sacc_hi": ("w", 42),
            "dur": ("w", 48),
            "ins2": ("w", 85),
            "hrflag": ("w", 91),
            "wtimer": ("w", 97),
            "wspeed": ("w", 100),
            "ins5": ("w", 103),
            "pcol0": ("w", 110),
            "pulsecur": ("w", 106),
            "pw": ("w", 120),
            "pw_hi": ("w", 126),
            "pcol2": ("w", 129),
            "pwdir": ("w", 135),
            "ptimer": ("w", 141),
            "ins3": ("w", 147),
            "modevol": ("w", 171),
            "volor": ("w", 174),
            "res": ("w", 181),
            "vbit": ("w", 186),
            "vmask": ("w", 198),
            "ins4": ("w", 214),
            "fcur": ("w", 217),
            "fcol0": ("w", 221),
            "cutoff": ("w", 228),
            "fcol2": ("w", 231),
            "ftimer": ("w", 234),
            "ins0": ("w", 240),
            "vmap": ("w", 243),
        },
    ),
    "pulse": (
        [
            "DE .. .. 10 2C BC .. .. B9 .. .. 9D .. .. A8 B9 .. .. 48 29 80 9D .. .. 68 29 7F"
            " 9D .. .. B9 .. .. C9 FF F0 0C 48 29 F0 9D .. .. 68 29 0F 9D .. .. BC .. .."
            " BD .. .. D0 15 BD .. .. 18 79 .. .. 9D .. .. BD .. .. 69 00 9D .. .. 4C .. .."
            " BD .. .. 38 F9 .. .. 9D .. .. BD .. .. E9 00 9D .. .."
        ],
        {"pcol3": ("w", 9), "pcol1": ("w", 62)},
    ),
    "filter": (
        [
            "A0 00 8A D9 .. .. F0 03 4C .. .. CE .. .. 10 1A AC .. .. B9 .. .. 8D .. .. A8"
            " B9 .. .. 8D .. .. B9 .. .. C9 FF F0 03 8D .. .. AC .. .. AD .. .. 18 79 .. .."
            " 8D .. .."
        ],
        {"owner": ("w", 4), "fcol3": ("w", 20), "fcol1": ("w", 50)},
    ),
    "wave": (
        [
            "BC .. .. B9 .. .. 29 40 F0 2A BC .. .. B9 .. .. C9 7E D0 07 DE .. .. 88 4C .. .."
            " C9 7F D0 0A B9 .. .. 9D .. .. A8 B9 .. .. 9D .. .. A9 00 9D .. .. 4C .. .."
            " BC .. .. B9 .. .. 30 1B C9 7E D0 07 DE .. .. 88 4C .. .. C9 7F D0 12 B9 .. .."
            " 9D .. .. A8 B9 .. .. 10 06 0A A0 01 4C .. .. 18 7D .. .. 0A 18 7D .. .. A0 00"
            " 8C .. .. A8 B9 .. .. 18 7D .. .. 9D .. .. B9 .. .. 69 00 9D .. .. BC .. .."
            " B9 .. .. 9D .. .. DE .. .. 10 09 BD .. .. 9D .. .. FE .. .."
        ],
        {
            "wnote": ("w", 14),
            "wctrl": ("w", 32),
            "rawflag": ("w", 105),
            "freq": ("w", 109),
            "fine": ("w", 113),
            "freq_lo": ("w", 116),
            "freq_hi": ("w", 124),
            "wave": ("w", 133),
        },
    ),
    "effects": (
        [
            "BD .. .. F0 49 BD .. .. D0 16 BD .. .. 18 7D .. .. 9D .. .. BD .. .. 7D .. .."
            " 9D .. .. 4C .. .. BD .. .. 38 FD .. .. 9D .. .. BD .. .. FD .. .. 9D .. .."
            " AD .. .. D0 13 BD .. .. 18 7D .. .. 9D .. .. BD .. .. 7D .. .. 9D .. .. 4C .. .."
            " BD .. .. D0 F8 BD .. .. F0 F3 BD .. .. 0A A8 B9 .. .. 38 F9 .. .. 8D .. .."
            " B9 .. .. F9 .. .. 18 7D .. .. 8D .. .. BC .. .. 88 30 09 4E .. .. 6E .. .."
            " 4C .. .. DE .. .. 10 0E BD .. .. 49 01 9D .. .. BD .. .. 9D .. .. BD .. .. D0 16"
            " BD .. .. 18 6D .. .. 9D .. .. BD .. .. 6D .. .. 9D .. .. 4C .. .. BD .. .. 38"
            " ED .. .. 9D .. .. BD .. .. ED .. .. 9D .. .. BD .. .. 18 7D .. .. 9D .. .."
            " BD .. .. 7D .. .. 9D .. .. BD .. .. 18 7D .. .. 9D .. .."
        ],
        {"freq2": ("w", 94), "acc5": ("w", 101)},
    ),
    "init": (
        [
            "0A 0A 0A A8 A2 00 B9 .. .. 9D .. .. 9D .. .. B9 .. .. 9D .. .. 9D .. .. C8 C8 E8"
            " E0 03 D0 E7 B9 .. .. 8D .. .. AD .. .. F0 2B A2 02 B9 .. .. 8D .. .. 3D .. .."
            " 9D .. .. CA 10 F1 2C .. .. 10 15 A2 00 B9 .. .. 9D .. .. B9 .. .. 9D .. .. C8 C8"
            " E8 E0 03 D0 ED A0 00 98 99 .. .. C8 C0 17 D0 F8 A8 99 .. .. C8 C0 0C D0 F8 A0 14"
            " 99 .. .. 88 10 FA A9 01 8D .. .. A9 03 8D .. .. A9 0F 8D .. .. 60"
        ],
        {"rec2": ("w", 7)},
    ),
}
# The wrapper: one build runs the whole player with I/O banked out and flushes
# its own copy, so its 25 registers are memory and the flush is the tick's
# write-out.  A build without it has none of these sites.
WRAP = {
    "count": (
        [
            "A9 .. F0 01 60 20 .. .. 20 .. .. A2 .. CA D0 0B A0 .. 88 D0 03 EE .. .. 8C .. .."
            " 8E .. .. 60"
        ],
        {"c1": ("w", 28), "c2": ("w", 25), "ccell": ("w", 22)},
    ),
    "wrap": (
        [
            "EE .. .. A9 34 85 .. 20 .. .. A2 18 BD .. .. 95 .. CA 10 F8 A9 35 85 .. A9 .. 85 .."
            " A9 .. 85 .. 20 .. .. AD .. .. 20 .. .. 86 .. 85 .. AD .. .. 20 .. .. 86 .. 85 .."
            " AD .. .. 85 .. 60"
        ],
        {
            "ghost": ("w", 13),
            "buf": ("i", 16),
            "vol_ov": ("i", 25),
            "vol_reg": ("i", 27),
            "res_ov": ("i", 29),
            "res_reg": ("i", 31),
            "pw0_lo": ("i", 42),
            "pw0_hi": ("i", 44),
            "pw1_lo": ("i", 52),
            "pw1_hi": ("i", 54),
            "cut_reg": ("i", 59),
        },
    ),
    "rowapply": (
        [
            "A0 00 B1 .. 8D .. .. A0 01 B1 .. 8D .. .. A0 02 B1 .. 8D .. .. A0 03 B1 .. 8D .. .."
            " A5 .. 18 69 04 85 .. 90 02 E6 .. 60"
        ],
        {"dptr": ("i", 3), "d0": ("w", 5), "d1": ("w", 12), "d2": ("w", 19), "d3": ("w", 26)},
    ),
    "flush": (
        ["AD .. .. F0 11 A2 18 B5 .. AC .. .. 88 10 FD 9D .. .. CA 10 F2 60"],
        {"delay": ("w", 1)},
    ),
}


def word(m, a):
    return m[a] | m[a + 1] << 8


class Banked(PcodeVM):
    """The chip answers only where the 6510 port maps it.

    One build runs the whole player with I/O banked out, so its 25 register
    writes a frame are *memory* under the chip and its own wrapper flushes the
    copy; a build that never touches the port writes the chip as it goes
    (prototype-jch.md section 3, the direction byte).
    """

    def _wr(self, addr, val, sz):
        if 0xD400 <= addr <= 0xD418 and port_bank(self.mem) != "io":
            keep, self.wlog = self.wlog, None
            try:
                super()._wr(addr, val, sz)
            finally:
                self.wlog = keep
            return
        super()._wr(addr, val, sz)


def load(path):
    """The tune's pre-init image: power-on RAM under the load band, and the port."""
    img = MachineImage.from_sid(Path(path).read_bytes())
    return bytearray(img.mem), img.lo, img.hi


def entries(path):
    img = MachineImage.from_sid(Path(path).read_bytes())
    return img.init, img.play


def image(path, song=0):
    """The band as the tick sees it: the tune's own init has run."""
    m, lo, hi = load(path)
    vm = Banked(m)
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


def read(m, at, fields, out):
    for f, (kind, off) in fields.items():
        i = at + off
        out[f] = (m[i], i, word(m, i))[{"i": 0, "c": 1, "w": 2}[kind]]


def layout(m, lo, hi):
    """Every base and cell, each from the instruction that reads it."""
    out = {}
    for name, (pats, fields) in SIGS.items():
        at = [a for p in pats for a in sites(m, lo, hi, p)]
        assert len(at) == 1, "%s: %d sites, not 1" % (name, len(at))
        read(m, at[0], fields, out)
    out["wrapper"] = all(sites(m, lo, hi, p) for _, (pats, _) in WRAP.items() for p in pats)
    if out["wrapper"]:  # which shape a build has is itself a datum
        for name, (pats, fields) in WRAP.items():
            at = [a for p in pats for a in sites(m, lo, hi, p)]
            assert len(at) == 1, "%s: %d sites, not 1" % (name, len(at))
            read(m, at[0], fields, out)
    for a, b, why in (
        ("freq", "freq_lo", "the tuning is read through one base"),
        ("cutoff", "fcol0", "the filter's own accumulator"),
        ("pw", "pcol0", "the pulse pair the program initialises"),
    ):
        assert out[a] is not None and out[b] is not None, why
    assert out["freq2"] == out["freq"] + 2, "the interval is a difference of neighbours"
    assert out["fcol1"] == out["fcol0"] + 1, "the filter program is four columns"
    assert out["fcol2"] == out["fcol0"] + 2, "the filter program is four columns"
    assert out["fcol3"] == out["fcol0"] + 3, "the filter program is four columns"
    assert out["pcol1"] == out["pcol0"] + 1, "the pulse program is four columns"
    assert out["pcol2"] == out["pcol0"] + 2, "the pulse program is four columns"
    assert out["pcol3"] == out["pcol0"] + 3, "the pulse program is four columns"
    # the track the filter runs on is column 3 of the program's own reserved record 0
    assert out["owner"] == out["fcol3"], "the filter's track is its reserved record"
    assert out["hr_sr"] == out["hr_ad"] + 1, "the hard restart is one record of the commands"
    assert out["cmdtab"] == out["hr_ad"], "the commands' own table starts at that record"
    assert out["cmdtab_b"] == out["cmdtab"] + 1, "a command is two bytes"
    for k in range(1, 8):
        assert out["ins%d" % k] == out["ins0"] + k, "the instrument is eight columns"
    out["notes"] = (out["acc5"] - out["freq"]) // 2  # where the stored tuning ends
    out["wave_rows"] = out["wctrl"] - out["wnote"]  # two parallel columns, back to back
    assert 0 < out["wave_rows"] < 0x100, "the wave table is two columns of one page"
    return out


DEAD = {
    "wave.step_back": "no wave row of either tune steps its cursor back",
    "wave.raw16": "no instrument of either tune takes the wave column as a raw frequency",
    "order.stop": "no order list of either tune stops a track",
    "cmd.speed": "no pattern command of either tune sets the row clock's own rate",
    "cmd.sr": "no pattern command of either tune overrides the instrument's sustain",
    "cmd.wave": "no pattern command of either tune patches an instrument's wave pointer",
    "ins.release": "no instrument of either tune releases to a wave row of its own",
    "funk": "the speed is two or more, so no funk tempo is read",
}
# every voice cell of the object, and the layout field the tune's own init fills it from
CELLS = {
    "phase": None,
    "speed": None,
    "gatemask": "livegate",
    "pend_gate": "s_gate",
    "xpose": "xpose",
    "pend_xpose": "s_xpose",
    "note": "note",
    "pend_note": "s_note",
    "vib_on": "vib_on",
    "slide_on": "slide_on",
    "pend_vib": "s_vib",
    "pend_slide": "s_slide",
    "cmd_vib": "cmd_vib",
    "cmd_slide": "cmd_slide",
    "wavecur": "wavecur",
    "wavetimer": "wtimer",
    "wavespeed": "wspeed",
    "wave": "wave",
    "pwdir": "pwdir",
    "sdir": "sdir",
    "vdir": "vdir",
    "vramp": "vramp",
    "vinc": "vinc",
    "vshift": "vshift",
    "vtimer": "vtimer",
    "vreload": "vreload",
    "ad": "ad",
    "sr": "sr",
    "hrflag": "hrflag",
    "fine": "fine",
}
WIDE = ("pw", "sacc", "sstep", "vacc", "freq")  # the voice cells that are 16 bits
RANK = {  # the order the voice's own tick runs its streams and accumulators in
    "pulse": 0,
    "pulse.step": 1,
    "filter": 2,
    "filter.step": 3,
    "wave": 4,
    "wave.step": 5,
    "slide": 6,
    "vibrato.turn": 7,
    "vibrato": 8,
    "pitch": 9,
    "vibrato.ramp": 10,
    "writeout": 11,
}
NOTE_END, HOLD, JUMP, STEP_BACK = 0x7F, 0x7E, 0x7F, 0x7E


class Refused(Exception):
    """A tune the layer will not emit a trackerprog for: the refusal, with its cell."""

    def __init__(self, refusal):
        super().__init__(
            "%s: %s at %s -- %s" % (refusal.why, refusal.cell, refusal.site or "?", refusal.detail)
        )
        self.refusal = refusal


def scan(path):
    """Refuse before reading: the tick must be the play call the family's tick is.

    The V20 sample builds run their mixer on a CIA interrupt of their own and
    stream the volume nibble from it; a nibble stream is not a score (section 8),
    and the refusal names the register it lands in.
    """
    ents = find_entries(Path(path).read_bytes())[1]
    if [e.kind for e in ents] != ["sub"]:
        raise Refused(
            Refusal(
                "sample stream",
                "mode_vol",
                "$%04X" % ents[0].addr,
                "the tick is an entry of kind %s on %s, not the player's own play call"
                % (ents[0].kind, ents[0].source),
            )
        )


class Tune:
    """One JCH V20 tune's data, read through its own player's operands."""

    def __init__(self, path, song=0, cycles=None, ticks=None, late=False):
        scan(path)
        self.path, self.song, self.late = path, song, late
        self.m, lo, hi = image(path, song)
        self.L = layout(self.m, lo, hi)
        self.cycles, self.ticks = cycles, ticks
        self.cmds, self.notes, self.xposes = {}, set(), set()
        self.pairs = set()  # the (instrument, note) pairs the tuning is read at
        self.prog = {}  # a column program's rows, per table base
        self.decoded = {}  # one pattern per (number, entry duration, entry instrument)
        self.orders, self.pats = [], {}
        self.used = set()  # the instruments the score reaches

    # ---- the four-column table programs ---------------------------------------
    def record(self, base, k):
        """One record of a column program: its four columns, at a byte cursor."""
        m = self.m
        return tuple(m[base + k + j] for j in range(4))

    def program(self, base, entries_, sets):
        """A column program as a stream: each record acts, then holds its frames.

        A record initialises, then steps for as many frames as its own column
        says, then the cursor takes its ``next`` link and the record there acts
        on that same tick -- so a record is two rows, an act and a wait, and a
        record with no frames of its own is the act alone (defmon-trackerprog
        section 8's own split, and the reason the note-on points past the act it
        has already made).
        """
        if base in self.prog:
            return self.prog[base]
        seen, work = {}, list(entries_)
        while work:  # every record the cursor can reach, and only those
            k = work.pop()
            if k in seen:
                continue
            seen[k] = None
            work.append(self.record(base, k)[3])
        rows, act = [{"trap": "no program runs here"}], {}
        for k in sorted(seen):
            act[k] = len(rows)
            rows.append(None)
            if self.frames(base, k):
                rows.append(None)
        for k in sorted(seen):
            r, nxt = act[k], self.record(base, k)[3]
            rows[r] = {"sets": sets(self.record(base, k)), "hold": 1}
            rows[r]["next"] = r + 1 if self.frames(base, k) else act[nxt]
            if self.frames(base, k):
                rows[r + 1] = {"hold": self.frames(base, k), "next": act[nxt]}
        self.prog[base] = (rows, act)
        return rows, act

    def frames(self, base, k):
        """How long a record holds: the pulse program's own column, less its direction."""
        f = self.record(base, k)[2]
        return f & 0x7F if base == self.L["pcol0"] else f

    def enter(self, base, k, timer):
        """The cursor state a record and its frame countdown are: a row and a hold."""
        act, n = self.prog[base][1], self.frames(base, k)
        if timer == 0:  # the next tick takes the link and acts on the record it names
            return {"row": act[self.record(base, k)[3]], "hold": 0}
        assert 0 < timer <= n, "a record's countdown outlasts its own frames"
        return {"row": act[k] + 1, "hold": n - timer}

    @staticmethod
    def pulse_sets(rec):
        """A pulse record: the pair it may reload, its direction and its step."""
        out = []
        if rec[0] != 0xFF:  # $FF keeps the width the sweep has reached
            out.append(["@pw", (rec[0] & 0xF0) | (rec[0] & 0x0F) << 8])
        return out + [["@pwdir", rec[2] & 0x80], ["@pwstep", rec[1]]]

    @staticmethod
    def filter_sets(rec):
        """A filter record: the cutoff it may reload, and its step."""
        out = []
        if rec[0] != 0xFF:
            out.append(["#cutoff", rec[0]])
        return out + [["#fstep", rec[1]]]

    # ---- the score ------------------------------------------------------------
    def command(self, byte):
        """One pattern command, named by what it does and never by its own index.

        The two bytes of the command's own record are a slide's direction and
        16-bit step, or a vibrato's speed, shift and depth ramp; the arms no
        certified horizon takes carry a trap with the reason attached.
        """
        m, L = self.m, self.L
        k = byte & 0x3F
        a, b = m[L["cmdtab"] + 2 * k], m[L["cmdtab_b"] + 2 * k]
        hi, lo = a & 0xF0, a & 0x0F
        if hi < 0x30:
            name = "slide.%s:%02X%02X" % ("down" if a & 0x20 else "up", lo, b)
            rows = [
                {
                    "when": [],
                    "sets": [
                        ["@sdir", a & 0x20],
                        ["@sstep", lo << 8 | b],
                        ["@pend_slide", 1],
                        ["@cmd_slide", 1],
                    ],
                }
            ]
        elif hi == 0x60:
            name = "vibrato:%X%02X" % (lo, b)
            rows = [
                {
                    "when": [],
                    "sets": [
                        ["@pend_vib", 1],
                        ["@cmd_vib", 1],
                        ["@vinc", lo],
                        ["@vreload", b >> 4],
                        ["@vtimer", (b >> 4) - 1 & 0xFF],
                        ["@vdir", 0],
                        ["@vramp", 0],
                        ["@vacc", 0],
                        ["@vshift", b & 0x0F],
                    ],
                }
            ]
        elif hi == 0xF0:
            name, rows = "volume:%02X" % b, [{"when": [], "sets": [["#vol_or", b]]}]
        else:
            why = {0xE0: "cmd.speed", 0x90: "cmd.sr"}.get(hi, "cmd.wave")
            name = "unreached:%02X%02X" % (a, b)
            rows = [{"when": [], "sets": [["!dead", {"trap": DEAD[why]}]]}]
        self.cmds[name] = {"bytes": [a, b], "index": k, "rows": rows}
        return name

    def pattern(self, at, dur, ins):
        """One pattern, materialised: the byte cursor and the packed prefixes go.

        A row's duration byte says how many further row steps it holds, so the
        row is one event and the steps it holds are one more (section 6's
        materialisation); the duration and the instrument are both sticky, so a
        pattern is decoded under the state the order step arrives in.
        """
        m, out, y = self.m, [], 0
        e, arm = self.blank(), []
        while True:
            b = m[at + y]
            y += 1
            if b >= 0x80:
                if b & 0xE0 == 0x80:
                    dur, e["tie"] = b & 0x0F, bool(b & 0x10)
                elif b & 0xE0 == 0xA0:
                    ins = e["ins"] = b & 0x1F
                    self.used.add(ins)
                else:
                    arm.append(self.command(b))
                continue
            if b == NOTE_END:
                return out, dur, ins, y
            if b == 0:
                e["gate"], e["tie"] = "off", True
            elif b == HOLD:
                e["gate"], e["tie"] = "on", True
            else:
                e["note"], e["sounds"], e["gate"] = b, True, "on"
                self.notes.add(b)
            e["arm"] = arm or None
            out.append(e)
            if dur:  # the row steps the event holds: one event that does nothing
                out.append(dict(self.blank(), dur=dur, tie=True))
            e, arm = self.blank(), []

    @staticmethod
    def blank():
        return {
            "dur": 1,
            "sounds": False,
            "tie": False,
            "gate": None,
            "note": None,
            "ins": None,
            "arm": None,
        }

    def pattern_at(self, n):
        m, L = self.m, self.L
        return m[L["patlo"] + n] | m[L["pathi"] + n] << 8

    def order(self, v):
        """One voice's order program: ``[transpose] pattern`` to its own terminator.

        The transpose column is sticky, and so are the duration and instrument
        the patterns leave, so the walk carries them: a pattern reached under two
        different states is two materialised patterns.
        """
        m, L = self.m, self.L
        at = m[L["optr"] + v] | m[L["optr_hi"] + v] << 8
        start = m[L["ostart"] + v] | m[L["ostart_hi"] + v] << 8
        assert at == start, "the order starts where its own restart points"
        play, steps, dur, ins, xpose = [], 0, 0, 0, 0
        while True:
            b = m[at]
            # the terminator is read where a pattern leaves the pointer, which is
            # the step's own first byte: a transpose is only the byte before a pattern
            if b == 0xFF:
                return {"play": play, "end": {"jump": 0}}
            assert b != 0xFE, DEAD["order.stop"]
            if b >= 0x80:
                xpose, at = b & 0x7F, at + 1
                b = m[at]
            self.xposes.add(xpose)
            entry = (b, dur, ins)
            evs, dur, ins = self.decode(b, dur, ins)
            play.append({"pattern": entry, "transpose": xpose})
            steps += sum(e["dur"] for e in evs)
            at += 1
            if self.ticks is not None and steps * (self.speed() + 1) > self.ticks:
                return {"play": play, "end": "horizon"}

    def decode(self, n, dur, ins):
        """One pattern under the state it is entered in, decoded once and kept."""
        key = (n, dur, ins)
        if key not in self.decoded:
            self.decoded[key] = self.pattern(self.pattern_at(n), dur, ins)[:3]
        return self.decoded[key]

    def speed(self):
        return self.m[self.L["speed"]]

    def score(self):
        """Every voice's order program, and the patterns they reach, deduplicated."""
        orders, pats, keys = [], {}, {}
        m, L = self.m, self.L
        self.used |= {m[L["ins"] + v] >> 3 for v in range(VOICES)}  # what init leaves live
        for v in range(VOICES):
            o = self.order(v)
            for step in o["play"]:
                key = step["pattern"]
                if key not in keys:
                    keys[key] = len(pats)
                    pats[str(len(pats))] = {"events": self.decoded[key][0]}
                step["pattern"] = keys[key]
            orders.append(o)
        self.orders, self.pats = orders, pats
        self.walk()
        return {"orders": orders, "patterns": pats, "commands": self.commands()}

    def commands(self):
        """Each command's own rows: what the pattern byte does, in the tick it does it."""
        return {k: {"rows": v["rows"]} for k, v in sorted(self.cmds.items())}

    # ---- the tuning -----------------------------------------------------------
    def wave_reach(self, start):
        """Every wave row an instrument's own cursor can reach: the run, and its jumps."""
        m, L = self.m, self.L
        seen, work = set(), [start]
        while work:
            j = work.pop() & 0xFF
            assert j < L["wave_rows"], "a wave cursor past the table's own two columns"
            if j in seen:
                continue
            seen.add(j)
            b = m[L["wnote"] + j]
            work.append(m[L["wctrl"] + j] if b == JUMP else j + 1)
        return seen

    def walk(self):
        """The live instrument and note of every row step, in the order the tune plays it.

        The pair is what the tuning is read at: a wave row's own offset against
        the note the row left live, which the order's transpose has already moved.
        """
        m, L = self.m, self.L
        for v in range(VOICES):
            ins, raw, xpose = m[L["ins"] + v] >> 3, m[L["s_note"] + v], 0
            self.pairs.add((ins, m[L["note"] + v], m[L["note"] + v]))  # what init leaves live
            for step in self.orders[v]["play"]:
                xpose = step["transpose"]
                for e in self.pats[str(step["pattern"])]["events"]:
                    ins = ins if e["ins"] is None else e["ins"]
                    raw = raw if e["note"] is None else e["note"]
                    self.pairs.add((ins, (raw + xpose) & 0x7F, raw))

    def span(self):
        """The lowest and highest entry of the tuning the tune's own reads reach.

        Three reads: the wave row's own offset against the note the row left
        live, a wave row that is a note outright, and the vibrato's interval,
        which is a difference of neighbours at the *untransposed* note.
        """
        m, L = self.m, self.L
        idx = set()
        for i, note, raw in self.pairs:
            idx |= {raw, raw + 1}
            for j in self.wave_reach(m[L["ins6"] + 8 * i]):
                b = m[L["wnote"] + j]
                if b in (JUMP, STEP_BACK):  # a link, not a note: the row it reaches is one
                    continue
                idx.add(b & 0x7F if b >= 0x80 else (b + note) & 0x7F)
        return min(idx), max(idx)

    def pitch(self):
        """The frequency table as the values the tune's own reads take out of it."""
        m, L = self.m, self.L
        lo, hi = self.span()
        assert hi < L["notes"], "a note past the stored tuning: %d" % hi
        return {
            "base": lo,
            "tuning": "12-TET",
            "note_count": L["notes"],
            "freq": [word(m, L["freq"] + 2 * n) for n in range(lo, hi + 1)],
        }

    # ---- the voice's own machine ----------------------------------------------
    def streams(self):
        """The two column programs, the wave table and its reader, and the write-out."""
        m, L = self.m, self.L
        p_rows = self.program(L["pcol0"], self.pulse_entries(), self.pulse_sets)[0]
        f_rows = self.program(L["fcol0"], self.filter_entries(), self.filter_sets)[0]
        out = {
            "pulse": {"rank": RANK["pulse"], "when": self.fx_guard(), "rows": p_rows},
            "filter": {
                "rank": RANK["filter"],
                "when": self.fx_guard() + [[{"cell": "voice_index"}, "==", m[L["owner"]]]],
                "rows": f_rows,
            },
            "wavetab": {
                "rows": [
                    {"note": m[L["wnote"] + i], "ctrl": m[L["wctrl"] + i]}
                    for i in range(L["wave_rows"])
                ]
            },
            "voicebits": {
                "rows": [
                    {"mask": m[L["vmask"] + v], "bit": m[L["vbit"] + v]} for v in range(VOICES)
                ]
            },
            "wave": {
                "rank": RANK["wave"],
                "all": True,
                "when": self.fx_guard(),
                "rows": self.wave_rows(),
            },
            "pitch": {
                "rank": RANK["pitch"],
                "all": True,
                "when": self.fx_guard(),
                "rows": self.pitch_rows(),
            },
            "writeout": {"rank": RANK["writeout"], "all": True, "rows": self.writeout_rows()},
            "prelude": {"rows": self.prelude_rows()},
            "notestage": {"rows": self.notestage_rows()},
        }
        return out

    def fx_guard(self, _deep=False):
        """What the tick's own skip leaves running: the write-out, and nothing else.

        The prefetch's hard restart jumps the whole effects block.  The build
        byte beside it (``$17CA``, jch.md:363-366's ``$1766``) leaves the pulse,
        the filter and the vibrato out of the row step the fetch reads as well,
        and that one is worth nothing: over the whole horizon of the only build
        that sets it, rendering the step diverges on 0 of 8,577 ticks, so the
        object states it nowhere (prototype-jch-trackerprog.md section 4).
        """
        return [[{"cell": "fx"}, "==", 0]]

    def pulse_entries(self):
        """Every record the pulse cursor can start on: an instrument's own, or the state."""
        m, L = self.m, self.L
        return {m[L["ins5"] + 8 * i] for i in self.used} | {
            m[L["pulsecur"] + v] for v in range(VOICES)
        }

    def filter_entries(self):
        m, L = self.m, self.L
        return {m[L["ins4"] + 8 * i] for i in self.used} | {m[L["fcur"]]}

    def wave_rows(self):
        """The wave row the cursor is on, read every tick: a jump, a note, an offset."""
        note = {"tabcell": ["wavetab", {"cell": "wavecur"}, "note"]}
        ctrl = {"tabcell": ["wavetab", {"cell": "wavecur"}, "ctrl"]}
        return [
            {"when": [[note, "==", JUMP]], "sets": [["@wavecur", ctrl]]},
            {
                "when": [[note, "==", STEP_BACK]],
                "sets": [["!dead", {"trap": DEAD["wave.step_back"]}]],
            },
            {
                "when": [[note, ">=", 0x80]],
                "sets": [["@freq_idx", {"and": [note, 0x7F]}], ["!raw", 1]],
            },
            {
                "when": [[note, "<", 0x80]],
                "sets": [
                    ["@freq_idx", {"and": [{"add": [note, {"cell": "note"}]}, 0x7F]}],
                    ["!raw", 0],
                ],
            },
            {"when": [], "sets": [["@wave", ctrl]]},
        ]

    def pitch_rows(self):
        """The voice's frequency: the tuning at its own index, detuned, then modulated."""
        base = {"add": [{"tuned": {"cell": "freq_idx"}}, {"cell": "fine"}]}
        return [
            {"when": [], "sets": [["@freq", base]]},
            {
                "when": [[{"cell": "slide_on"}, "!=", 0], [{"flag": "raw"}, "==", 0]],
                "sets": [["@freq", {"add": [{"cell": "freq"}, {"cell": "sacc"}]}]],
            },
            {
                "when": [[{"cell": "slide_on"}, "==", 0], [{"cell": "vib_on"}, "!=", 0]],
                "sets": [["@freq", {"add": [{"cell": "freq"}, {"cell": "vacc"}]}]],
            },
        ]

    def writeout_rows(self):
        """The voice's own write-out, and the three flags it spends on its way out."""
        return [
            {
                "when": [],
                "sets": [
                    ["pw_lo", {"cell": "pw.lo"}],
                    ["pw_hi", {"cell": "pw.hi"}],
                    ["reg.22", {"global": "cutoff"}],
                    ["pitch", {"cell": "freq"}],
                    ["ad", {"cell": "ad"}],
                    ["sr", {"cell": "sr"}],
                    ["ctrl", {"and": [{"cell": "wave"}, {"cell": "gatemask"}]}],
                    ["reg.24", {"or": [{"global": "mode_vol"}, {"global": "vol_or"}]}],
                ],
            },
            {"when": [], "sets": [["@fx", 0]]},
        ]

    def prelude_rows(self):
        """Two frames early: the gate goes off, and the instrument may restart hard."""
        m, L = self.m, self.L
        keyed = [[{"cell": "keyed"}, "!=", 0]]
        return [
            {"when": keyed, "sets": [["@gatemask", 0xFE]]},
            {
                "when": keyed + [[{"cell": "hrflag"}, "!=", 0]],
                "sets": [
                    ["ad", m[L["hr_ad"]]],
                    ["@ad", m[L["hr_ad"]]],
                    ["sr", m[L["hr_sr"]]],
                    ["@sr", m[L["hr_sr"]]],
                    ["@fx", 1],
                ],
            },
        ]

    @staticmethod
    def notestage_rows():
        """A row that sounds ends the modulation no command of its own restated."""
        return [
            {"when": [[{"cell": "cmd_slide"}, "==", 0]], "sets": [["@pend_slide", 0]]},
            {"when": [[{"cell": "cmd_vib"}, "==", 0]], "sets": [["@pend_vib", 0]]},
        ]

    # ---- the accumulators -----------------------------------------------------
    def accs(self):
        """Section 5's records: the two sweeps, the wave clock, the slide, the vibrato."""
        vt, wt = {"cell": "vtimer"}, {"cell": "wavetimer"}
        iv = {"interval": {"sub": [{"cell": "note"}, {"cell": "xpose"}]}}
        return {
            "pulse.step": self.acc(
                "pulse.step",
                "pw",
                16,
                {"cell": "pwstep"},
                phase={"cell": "pwdir"},
                witness="the sixteen-bit pair the write-out sends the chip twelve bits of",
            ),
            "filter.step": self.acc(
                "filter.step",
                "#cutoff",
                8,
                {"global": "fstep"},
                scope="global",
                witness="the eight bits of cutoff the program's own record steps",
            ),
            "wave.step": self.countdown("wave.step", "wavetimer", "wavespeed", wt, "wavecur"),
            "slide": self.acc(
                "slide",
                "sacc",
                16,
                {"cell": "sstep"},
                phase={"cell": "sdir"},
                witness="the sixteen-bit store the voice's own frequency reads",
            ),
            "vibrato.turn": self.countdown("vibrato.turn", "vtimer", "vreload", vt, None),
            "vibrato": self.acc(
                "vibrato",
                "vacc",
                16,
                {
                    "shr": [
                        {"and": [{"add": [iv, {"u16": [0, {"cell": "vramp"}]}]}, 0xFFFF]},
                        {"cell": "vshift"},
                    ]
                },
                phase={"cell": "vdir"},
                witness="the sixteen-bit store the voice's own frequency reads",
            ),
            "vibrato.ramp": self.acc(
                "vibrato.ramp",
                "vramp",
                8,
                {"cell": "vinc"},
                witness="the byte the vibrato's own depth is carried in",
            ),
        }

    def acc(self, name, cell, width, delta, phase=None, scope="voice", witness=""):
        out = {
            "id": name,
            "cell": cell,
            "target": "freq" if "vib" in name or name == "slide" else "pw",
            "width": width,
            "delta": delta,
            "policy": "wrap",
            "rate": 1,
            "scope": scope,
            "produce": [],
            "rank": RANK[name],
            "bound": {
                "from": "projected",
                "interval": [0, (1 << width) - 1],
                "witness": witness,
            },
        }
        if name == "filter.step":
            out["target"] = "cutoff"
        if phase is not None:
            out["phase"] = phase
        return out

    def countdown(self, name, cell, reload_, count, cursor):
        """A frame countdown that turns something at its end: the wave cursor, or a phase."""
        gate = (
            [["@" + cursor, {"and": [{"add": [{"cell": cursor}, 1]}, 0xFF]}]]
            if cursor
            else [["@vdir", {"xor": [{"cell": "vdir"}, 1]}]]
        )
        return {
            "id": name,
            "cell": cell,
            "target": "note" if cursor else "freq",
            "width": 8,
            "delta": {"const": 1},
            "phase": {"const": 1},
            # the step is the count coming down; the frame it does not come down on
            # is the frame the record reloads and the thing it clocks turns
            "step_when": [[count, "!=", 0]],
            "policy": {"reload": {"cell": reload_}, "when": [[count, "==", 0]]},
            "gate": {"true": [], "false": gate},
            "rate": 1,
            "scope": "voice",
            "produce": [],
            "rank": RANK[name],
            "bound": {
                "from": "proved",
                "interval": [0, 0xFF],
                "witness": "the frame count the record it belongs to reloads",
            },
        }

    def arms(self):
        """One arm per accumulator, guarded by what the tick's own skips leave running."""
        run = self.fx_guard()
        vib = run + [[{"cell": "slide_on"}, "==", 0], [{"cell": "vib_on"}, "!=", 0]]
        owner = [[{"cell": "voice_index"}, "==", self.m[self.L["owner"]]]]
        return [
            {"acc": "pulse.step", "when": run},
            {"acc": "filter.step", "when": run + owner},
            {"acc": "wave.step", "when": run},
            {"acc": "slide", "when": run + [[{"cell": "slide_on"}, "!=", 0]]},
            {"acc": "vibrato.turn", "when": vib},
            {"acc": "vibrato", "when": vib},
            {"acc": "vibrato.ramp", "when": vib},
        ]

    # ---- the instruments ------------------------------------------------------
    def instruments(self):
        """One record per instrument the score reaches: eight columns, and a note-on."""
        return {str(i): self.instrument(i) for i in sorted(self.used)}

    def instrument(self, i):  # noqa: C901 - one clause per column of the record
        m, L = self.m, self.L
        col = [m[L["ins0"] + 8 * i + k] for k in range(8)]
        assert col[6] == col[7], DEAD["ins.release"]
        assert not col[2] & 0x40, DEAD["wave.raw16"]
        tie0 = [["tie", "==", 0]]
        p_rows, p_act = self.prog[L["pcol0"]]
        rows = [
            {
                "when": tie0,
                "sets": [
                    ["@wavecur", col[6]],
                    ["@hrflag", col[2] & 0x80],
                    ["@wavetimer", col[2] & 0x0F],
                    ["@wavespeed", col[2] & 0x0F],
                ],
                "point": [["pulse", p_rows[p_act[col[5]]]["next"]]],
            },
            {"when": tie0, "sets": self.pulse_sets(self.record(L["pcol0"], col[5]))},
        ]
        rows.append({"when": tie0, "sets": self.route(col[3])})
        if col[3] & 0x0F:  # the filter program is loaded where the record names one
            f_rows, f_act = self.prog[L["fcol0"]]
            rows.append(
                {
                    "when": tie0,
                    "sets": self.filter_sets(self.record(L["fcol0"], col[4])),
                    "point": [["filter", f_rows[f_act[col[4]]]["next"]]],
                }
            )
        rows.append(
            {
                "when": tie0,
                "sets": [
                    ["ad", {"ins": "adsr.0"}],
                    ["@ad", {"ins": "adsr.0"}],
                    ["sr", {"ins": "adsr.1"}],
                    ["@sr", {"ins": "adsr.1"}],
                    ["ctrl", 9],
                ],
            }
        )
        return {
            "adsr": [col[0], col[1]],
            "flags": col[2],
            "vol": col[3],
            "filter": col[4],
            "pulse": col[5],
            "wave": col[6],
            "prelude": {"stream": "prelude", "early": 2},
            "on_note": rows,
            "accs": self.arms(),
        }

    def route(self, vol):
        """The filter routing byte: this voice's bit cleared, or set beside a resonance."""
        res = {"global": "res"}
        mask = {"tabcell": ["voicebits", {"cell": "voice_index"}, "mask"]}
        bit = {"tabcell": ["voicebits", {"cell": "voice_index"}, "bit"]}
        if vol & 0x0F in (0, 8):
            return [["#res", {"and": [res, mask]}], ["reg.23", {"global": "res"}]]
        return [
            ["#mode_vol", (vol & 0x0F) << 4],
            ["reg.24", {"or": [(vol & 0x0F) << 4, {"global": "vol_or"}]}],
            ["#res", {"or": [{"or": [{"and": [res, 0x0F]}, bit]}, vol & 0xF0]}],
            ["reg.23", {"global": "res"}],
        ]

    # ---- the tune's one global channel ----------------------------------------
    def globals_(self):
        """The filter, the routing, the master volume -- and the wrapper, where there is one."""
        out = {
            "streams": ["channel"],
            "flags": {"raw": {"default": 0}},
            "stop_writes": [],
            "commit": [],  # the voices send the channel's own registers themselves
        }
        if self.L["wrapper"]:
            out["commit"] += self.overrides()
        return out

    def channel_rows(self):
        """The head of the tick: what the voices leave for the commit, and the wrapper."""
        rows = [{"when": [], "sets": [["#stepped", 0]]}]
        if not self.L["wrapper"]:
            return rows
        c1, c2 = {"global": "c1"}, {"global": "c2"}
        return rows + [
            {
                "when": [[{"global": "playing"}, "!=", 0]],
                "sets": [["#dptr", {"add": [{"global": "dptr"}, 1]}]],
            },
            {"when": [[c1, "==", 1], [c2, "==", 1]], "sets": [["#playing", 0]]},
            {
                "when": [[c1, "==", 1]],
                "sets": [
                    ["#c1", 0],
                    ["#c2", {"and": [{"sub": [c2, 1]}, 0xFF]}],
                    ["#stepped", 1],
                ],
            },
            {
                "when": [[c1, "!=", 1], [{"global": "stepped"}, "==", 0]],
                "sets": [["#c1", {"and": [{"sub": [c1, 1]}, 0xFF]}]],
            },
        ]

    def wrapdata(self):
        """The wrapper's own per-frame record: two pulse widths, a cutoff and a delay.

        Four bytes a frame, read through a pointer the wrapper walks, and flushed
        by the *next* frame -- so row 0 is the record the tune's init leaves and
        row k the one frame k reads.
        """
        m, L = self.m, self.L
        at = word(m, L["dptr"])
        rows = [self.wraprow([m[L["d%d" % j]] for j in range(4)])]
        for k in range(min(self.ticks, self.playticks())):
            rows.append(self.wraprow([m[at + 4 * k + j] for j in (0, 1, 2, 3)]))
        return {"rows": rows}

    def playticks(self):
        """How many frames the wrapper's own two-byte countdown lets the player run."""
        m, L = self.m, self.L
        return m[L["c1"]] + 0x100 * (m[L["c2"]] - 1)

    @staticmethod
    def wraprow(b):
        """One record: a byte a nibble shift opens into each twelve-bit pair."""
        return {
            "pw0_lo": (b[0] & 0x0F) << 4,
            "pw0_hi": b[0] >> 4,
            "pw1_lo": (b[1] & 0x0F) << 4,
            "pw1_hi": b[1] >> 4,
            "cut": b[2],
            "delay": b[3],
        }

    def wrapcell(self, col):
        return {"tabcell": ["wrapdata", {"global": "dptr"}, col]}

    def overrides(self):
        """What the wrapper writes over the player's own image, every frame."""
        L, b = self.L, self.L["buf"]
        return [
            [L["pw0_lo"] - b, self.wrapcell("pw0_lo")],
            [L["pw0_hi"] - b, self.wrapcell("pw0_hi")],
            [L["pw1_lo"] - b, self.wrapcell("pw1_lo")],
            [L["pw1_hi"] - b, self.wrapcell("pw1_hi")],
            [L["cut_reg"] - b, self.wrapcell("cut")],
            [L["res_reg"] - b, L["res_ov"]],
            [L["vol_reg"] - b, L["vol_ov"]],
        ]

    def flush(self):
        """The image's own write-out, in the order the frame's delay byte selects.

        One build flushes its 25 registers low to high when the frame carries no
        delay and high to low when it does, so the order is a property of the
        record being flushed and every entry states the guard it writes under.
        """
        run = [[{"global": "playing"}, "!=", 0]]
        d = self.wrapcell("delay")
        return [[r, run + [[d, "==", 0]]] for r in range(25)] + [
            [r, run + [[d, "!=", 0]]] for r in range(24, -1, -1)
        ]

    # ---- the state the tune's own init leaves ---------------------------------
    def state0(self):
        """What init leaves: the cells, the cursors, the channel and, maybe, the image."""
        m, L = self.m, self.L
        cells = {k: [m[L[f] + v] for v in range(VOICES)] for k, f in CELLS.items() if f}
        cells["phase"] = [m[L["phase"]]] * VOICES
        cells["speed"] = [m[L["speed"]]] * VOICES
        cells["xpose"] = [x >> 1 for x in cells["xpose"]]
        cells["pend_xpose"] = [x >> 1 for x in cells["pend_xpose"]]
        cells["note"] = [n + t for n, t in zip(cells["note"], cells["xpose"])]
        cells["ins"] = [m[L["ins"] + v] >> 3 for v in range(VOICES)]
        for k, (a, b) in (
            ("pw", ("pw", "pw_hi")),
            ("sacc", ("sacc", "sacc_hi")),
            ("sstep", ("sstep", "sstep_hi")),
            ("vacc", ("vacc", "vacc_hi")),
            ("freq", ("freq_lo", "freq_hi")),
        ):
            cells[k] = [m[L[a] + v] | m[L[b] + v] << 8 for v in range(VOICES)]
        cells["pwstep"] = [m[L["pcol1"] + m[L["pulsecur"] + v]] for v in range(VOICES)]
        for k in ("freq_idx", "keyed", "fx", "rowsleft"):
            cells[k] = [0] * VOICES
        gl = {
            "cutoff": m[L["cutoff"]],
            "fstep": m[L["fcol1"] + m[L["fcur"]]],
            "res": m[L["res"]],
            "mode_vol": m[L["modevol"]],
            "vol_or": m[L["volor"]],
            "stepped": 0,
        }
        out = {
            "cells": cells,
            "ins": cells["ins"],
            "globals": gl,
            "cursors": {
                "pulse": [
                    self.enter(L["pcol0"], m[L["pulsecur"] + v], m[L["ptimer"] + v])
                    for v in range(VOICES)
                ]
            },
            "gcursors": {"filter": self.enter(L["fcol0"], m[L["fcur"]], m[L["ftimer"]])},
        }
        if L["wrapper"]:
            gl.update(
                {
                    "dptr": 0,
                    "playing": 1,
                    "c1": m[L["c1"]],
                    "c2": m[L["c2"]],
                }
            )
            out["shadow"] = [m[L["buf"] + r] for r in range(25)]
            assert not m[L["ccell"]], "the wrapper's own play gate is open at the first frame"
        return out

    def meta(self):
        """Section 3.1, and the data a family's whole tick shape reduces to."""
        gate = [["gate_stmt", "!=", 0]]
        pre = [
            ["gate", "pend_gate"],
            ["transpose", "pend_xpose"],
            ["note", "pend_note"],
        ]
        row = [{"stream": "notestage", "when": [["sounds", "!=", 0]]}]
        if self.late:  # the row's commands at the boundary, which is what they cost
            row.insert(0, {"commands": True, "when": gate})
        else:
            pre.append(["cmds", "cmds"])
        out = {
            "tune": Path(self.path).name,
            "family": "JCH V20",
            "song": self.song,
            "cycles_per_tick": self.cycles or 19656,
            "voices": VOICES,
            "voice_order": [2, 1, 0],
            "commit_order": ["ad", "sr", "ctrl"],
            "wide": list(WIDE) + ["dptr"],
            "tempo": {
                "form": "countdown",
                "cell": "phase",
                "boundary": 0,
                "reload": "speed",
                "early": 2,
            },
            "tick": ["fetch", "prelude", "row", "machine"],
            "row_consumes_tick": [["keys", "!=", 0]],
            "row_command": "spent",
            "prefetch": pre,
            "stage_sounds": "keyed",
            "row": row
            + [
                {"sets": [["@cmd_slide", 0], ["@cmd_vib", 0]], "when": gate},
                {
                    "sets": [
                        ["@gatemask", {"cell": "pend_gate"}],
                        ["@xpose", {"cell": "pend_xpose"}],
                        ["@vib_on", {"cell": "pend_vib"}],
                        ["@slide_on", {"cell": "pend_slide"}],
                    ],
                    "when": gate,
                },
                {"sets": [["@sacc", 0]], "when": gate + [[{"cell": "pend_slide"}, "==", 0]]},
                # the note the fetch staged, for the rows the note step does not reach:
                # a row that sounds takes its own, and a row that holds or lets go
                # takes the one the fetch left, which is what the commit copies
                {
                    "sets": [["@note", {"add": [{"cell": "pend_note"}, {"cell": "xpose"}]}]],
                    "when": gate + [["sounds", "==", 0]],
                },
                # the instrument the row names, and no staging: the player's commit
                # copies a staged byte, and rendering the row's own diverges on 0 ticks
                # of either horizon (prototype-jch-trackerprog.md section 4)
                {"ins": True, "when": gate},
                {"note": True, "when": [["sounds", "!=", 0]]},
            ],
            "player": "prototype-trackerprog.md sections 4 and 5",
        }
        if self.L["wrapper"]:
            out["shadow"] = {"registers": self.flush()}
        return out

    # ---- the whole object -----------------------------------------------------
    def build(self):
        """Section 3's seven sections, in the order the extraction settles them."""
        assert self.speed() >= 2, DEAD["funk"]
        score = self.score()
        streams = self.streams()
        if self.L["wrapper"]:
            streams["wrapdata"] = self.wrapdata()
        streams["channel"] = {"all": True, "rows": self.channel_rows()}
        return {
            "$trackerprog": 1,
            "meta": self.meta(),
            "pitch": self.pitch(),
            "streams": streams,
            "accs": self.accs(),
            "instruments": self.instruments(),
            "score": score,
            "globals": self.globals_(),
            "state0": self.state0(),
        }


def build(path, song=0, cycles=None, ticks=None, late=False):
    """The trackerprog object for one JCH V20 tune."""
    return Tune(path, song, cycles, ticks, late).build()


def claim(path, song):
    """What the source tuneprog's certificate claims, and the binding to it."""
    d = Path(path).read_bytes()
    c = json.loads(d)
    s = next(x for x in c["subtunes"] if x["song"] == song + 1)
    loop = (
        None
        if s["period"] is None or s["period"] == 1
        else {"period": s["period"], "first_repeat": s["first_repeat"]}
    )
    end = "loop" if loop else ("fixed_point" if s["period"] == 1 else "horizon")
    return loop, s["ticks"], s["cycles_per_tick"], hashlib.sha256(d).hexdigest()[:16], end


def loop_holds(obj, loop):
    """Re-verify the inherited claim on the render: the horizon replays itself."""
    n, p = loop["first_repeat"] + 1, loop["period"]
    w = render(obj, n + p)
    return w[n - p : n] == w[n : n + p]


def fixed_point(obj, ticks):
    """A period of one is a fixed point: the tick the tune ends on writes nothing more."""
    w = render(obj, ticks)
    return w[-1] == [] and w[-2] != []


class Reference:
    """The oracle: the tune's own player on the PcodeVM, one tick at a time."""

    def __init__(self, path, song, cycles):
        self.vm, self.cache = Banked(load(path)[0]), {}
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
        if a != b:  # the whole horizon is counted, and the first one is named
            doc["diverged"] += 1
            if doc["divergence"] is None:
                doc["divergence"] = {"tick": t, "expected": _fmt(want), "got": _fmt(mine)}
        if budget and time.process_time() - t0 > budget:
            doc["_player"] = player
            return False
    return True


def _fmt(w):
    return " ".join("%02X=%02X" % rv for rv in w)


def certify(path, song, obj, ticks, cycles, budget=0.0, state=None):
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
            "diverged": 0,
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
    ap.add_argument("--late", action="store_true", help="run a row's commands at its boundary")
    ap.add_argument("--budget", type=float, default=0.0, help="CPU seconds per invocation")
    ap.add_argument("--resume", default=None, help="a file the comparison resumes from")
    a = ap.parse_args(argv)
    loop, ticks, cycles, digest, end = (None, a.ticks or 1000, None, None, "horizon")
    if a.source:
        loop, ticks, cycles, digest, end = claim(a.source, a.song)
    ticks = a.ticks or ticks
    try:
        obj = build(a.sid, a.song, cycles, None if loop else ticks, a.late)
    except Refused as r:  # fail closed: nothing is emitted, and the cell is named
        print(json.dumps({"emitted": False, "refusals": [r.refusal.to_dict()]}, indent=1))
        return 3
    if a.out:
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "trackerprog.json").write_text(json.dumps(obj, indent=1))
        text = printer.render(obj)
        (d / "trackerprog.md").write_text(text)
        print("print: " + "  ".join("%s %s" % kv for kv in printer.numbers(text).items()))
    print(
        "patterns %d  events %d  tuning %d  pulse %d  filter %d  wave %d  commands %d  ins %d"
        % (
            len(obj["score"]["patterns"]),
            sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
            len(obj["pitch"]["freq"]),
            len(obj["streams"]["pulse"]["rows"]),
            len(obj["streams"]["filter"]["rows"]),
            len(obj["streams"]["wavetab"]["rows"]),
            len(obj["score"]["commands"]),
            len(obj["instruments"]),
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
    doc["end"] = {"tick": doc["ticks"] - 1, "kind": end}
    if end == "fixed_point":
        doc["end"]["verified"] = fixed_point(obj, doc["ticks"])
    print(json.dumps({k: v for k, v in doc.items() if k != "dropped"}, indent=1))
    if a.out:
        (Path(a.out) / "trackerprog.certificate.json").write_text(json.dumps(doc, indent=1))
    return 0 if doc["divergence"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
