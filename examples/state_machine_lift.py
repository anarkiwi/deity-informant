"""End-to-end canonical example for docs/register-model-lift-impl.md.

A hand-written 6502 playroutine (three parallel voices, a RAM SID shadow, hard
restart, vibrato over an ADC carry chain, SMC dispatch, deferred-carry cursors)
is decompiled, e-graph-minimized, Z3-folded to a role-typed u16 state machine."""

from __future__ import annotations

import re
import sys

from deity_informant import PcodeVM, lift, run_sub
from deity_informant import eqlift_mem
from deity_informant import framelog
from deity_informant import render as R
from deity_informant import structured as S
from deity_informant.lifter import OPS, MODE_LEN, ILLEGAL_OPCODES

PAL_CLOCK = 985248
PAL_CYCLES = 19656
SID = 0xD400
INIT, PLAY = 0x0C00, 0x1000
VOICES = 3
ZPV = (0x40, 0x60, 0x80)  # per-voice state block; identical layout, shifted base
PTR, DUR, PHASE, NLO, NHI, DEPTH, WAVE, CTL, AD, SR = 0, 2, 3, 4, 5, 6, 7, 8, 9, 10
ADEF, SRDEF, CLO, CHI, RATE, DLO, DHI = 11, 12, 13, 14, 15, 16, 17
CUT, CUTH, FCTL, FVOL = 18, 19, 20, 21  # voice 3 only: the filter lanes it owns
SHADOW = 0x0340  # 3 x 7-byte SID image; the envelope/control lanes are flushed
FLUSH = (5, 6, 4)  # ADSR before the gate: ctrl is written last
WAVEF = (0x40, 0x20, 0x40)  # pulse lead, saw bass, pulse arpeggio
ADSR = ((0x08, 0xA9), (0x09, 0x68), (0x00, 0x86))
PW = ((0x00, 0x08), (0x00, 0x08), (0x00, 0x03))
PORTA = (0x60, 0xC0, 0xFF)  # per-voice slide rate, in freq units per frame
TEST_BIT, GATE_BIT = 0x08, 0x01
BARS, FRAMES = 768, 800  # 8 bars of 96 frames, plus wrap through the loop command
PH0, TICK, PWL, LOGI, ZX, ZY, SWP, LFO = 0x30, 0x33, 0x34, 0x36, 0x37, 0x38, 0x39, 0x3A
PWH = PWL + 1
ZBUF, ZLO, ZHI = 0x20, 0xF0, 0x0F  # zp row: base $20, index cycles $F0..$0F (8-bit wrap)
ZSPAN = 32
PHSTEP = (0x91, 0x2B, 0x5E)  # 24-bit phase step; the carry-out is the PWM tick
PWSTEP = 0x0140  # per-frame step of the 12-bit pulse-width accumulator
SWEEP = 0x0153  # per-frame step of voice 3's 16-bit filter cutoff accumulator
FILT3, LP, VOL = 0x04, 0x10, 0x0F  # route voice 3, low-pass, master volume

FOLDS = frozenset(
    (
        "pair_store",
        "pair_set",
        "advance",
        "wide16",
        "wide24",
        "wide_cmp",
        "row_read",
        "reroll_guard",
    )
)  # fmt: skip -- every rewrite the example applies, each instance Z3-proved

_ENC = {}
for _op in sorted(OPS):
    if _op not in ILLEGAL_OPCODES:
        _ENC.setdefault(OPS[_op], _op)
_ONE = {"imm", "zp", "zpx", "zpy", "indx", "indy", "rel"}


class Asm:
    """Two-pass label assembler (after tests/_fuzzgen.Asm), legal opcodes only."""

    def __init__(self, org):
        self.org, self.items, self.labels = org, [], {}

    def i(self, mn, mode="impl", operand=None):
        self.items.append(("i", mn, mode, operand))
        return self

    def label(self, name):
        self.items.append(("label", name))
        return self

    def byte(self, *vals):
        for v in vals:
            self.items.append(("byte", v))
        return self

    def _resolve(self, operand):
        if operand is None:
            return 0
        if isinstance(operand, int):
            return operand
        kind, name = operand[0], operand[1]
        base = self.labels[name] + (operand[2] if len(operand) > 2 else 0)
        return {"L": base & 0xFFFF, "LOL": base & 0xFF, "HIL": (base >> 8) & 0xFF}[kind]

    def assemble(self):
        pc = self.org
        for it in self.items:
            if it[0] == "label":
                self.labels[it[1]] = pc
            else:
                pc += 1 if it[0] == "byte" else MODE_LEN[it[2]]
        out, pc = bytearray(), self.org
        for it in self.items:
            if it[0] == "label":
                continue
            if it[0] == "byte":
                out.append(self._resolve(it[1]) & 0xFF)
                pc += 1
                continue
            _, mn, mode, operand = it
            out.append(_ENC[(mn, mode)])
            pc += MODE_LEN[mode]
            if mode == "impl":
                continue
            val = self._resolve(operand)
            if mode == "rel":
                delta = val - pc
                assert -128 <= delta <= 127, "branch out of range: %r" % (operand,)
                out.append(delta & 0xFF)
            elif mode in _ONE:
                out.append(val & 0xFF)
            else:
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
        return bytes(out)


def sid_freq(hz):
    return round(hz * 0x1000000 / PAL_CLOCK)


# C major over three octaves from C3; scripts index this row by scale degree.
_DEG = (0, 2, 4, 5, 7, 9, 11)
NOTES = [sid_freq(130.81 * 2 ** ((12 * (i // 7) + _DEG[i % 7]) / 12)) for i in range(22)]
C3, F3, G3 = 0, 3, 4
C4, D4, E4, F4, G4 = 7, 8, 9, 10, 11
Q, EI, DQ, H = 24, 12, 36, 48
VIBTAB = (0, 1, 2, 3, 4, 3, 2, 1)
ON, OFF = 0x07, 0x00  # phase mask: 0 pins the table at its zero entry

_PHRASE = [(E4, Q), (E4, Q), (F4, Q), (G4, Q), (G4, Q), (F4, Q), (E4, Q), (D4, Q)]
LEAD = (
    [("raw", (0x10, 0x80, 0x11, 0x01))]
    + _PHRASE
    + [(C4, Q), (C4, Q), (D4, Q), (E4, Q), (E4, DQ), (D4, EI)]
    + [("vib", ON), (D4, H - 4), ("vib", OFF), ("rest", 4)]
    + [("raw", (0x11, 0x02))]
    + _PHRASE
    + [(C4, Q), (C4, Q), (D4, Q), (E4, Q), (D4, DQ), (C4, EI)]
    + [("vib", ON), (C4, H - 8), ("vib", OFF), ("rest", 8)]
)
_ROOTS = (C3, C3, F3, C3, G3, C3, G3, C3, C3, C3, F3, C3, G3, G3, C3, C3)
BASS = (
    [("vib", OFF), ("raw", (0x09, 0x00, 0x0A, 0x04))]
    + [(n, H) for n in _ROOTS[:11]]
    + [("vib", ON)]
    + [(n, H) for n in _ROOTS[11:15]]
    + [("vib", OFF), (_ROOTS[15], H - 8), ("rest", 8)]
)
_ARP = (C4, E4, G4, E4)
ARP = (
    [("vib", OFF), ("raw", (0x10, 0x00, 0x11, 0x08))]
    + [(_ARP[k % 4], EI) for k in range(30)]
    + [("vib", ON), ("raw", ())]
    + [(_ARP[k % 4], EI) for k in range(30, 62)]
    + [("vib", OFF), ("rest", 24)]
)
SCRIPTS = (LEAD, BASS, ARP)


def script_frames(script):
    return sum(n for op, n in script if op == "rest" or isinstance(op, int))


def _cursor(p, cell, tag):
    """Step a zp-row cursor over ``$F0..$0F``: the index arithmetic wraps in 8 bits."""
    p.i("INC", "zp", cell).i("LDA", "zp", cell)
    p.i("CMP", "imm", (ZHI + 1) & 0xFF).i("BNE", "rel", ("L", tag))
    p.i("LDA", "imm", ZLO).i("STA", "zp", cell)
    p.label(tag)


def _global(p):
    """The voice-independent frame head: PWM phase, the log row, the zp row."""
    p.i("LDA", "zp", PH0).i("CLC").i("ADC", "imm", PHSTEP[0]).i("STA", "zp", PH0)
    p.i("LDA", "zp", PH0 + 1).i("ADC", "imm", PHSTEP[1]).i("STA", "zp", PH0 + 1)
    p.i("LDA", "zp", PH0 + 2).i("ADC", "imm", PHSTEP[2]).i("STA", "zp", PH0 + 2)
    p.i("LDA", "imm", 0).i("ADC", "imm", 0).i("STA", "zp", TICK)  # carry outlives its add
    p.i("LDA", "zp", PWL).i("CLC").i("ADC", "imm", PWSTEP & 0xFF).i("STA", "zp", PWL)
    p.i("LDA", "zp", PWH).i("ADC", "imm", PWSTEP >> 8).i("STA", "zp", PWH)
    p.i("LDA", "zp", TICK).i("BEQ", "rel", ("L", "g_join"))
    p.i("LDX", "zp", LOGI).i("LDA", "zp", PWH).i("STA", "absx", ("L", "pwlog"))
    p.i("INC", "zp", LOGI).i("LDA", "zp", LOGI).i("AND", "imm", 0x0F).i("STA", "zp", LOGI)
    p.label("g_join")  # the pw pair crosses the join, disjoint from the row the arm stored
    p.i("LDA", "zp", PWL).i("STA", "abs", SID + 2)
    p.i("LDA", "zp", PWH).i("STA", "abs", SID + 3)
    p.i("LDX", "zp", LOGI).i("LDA", "absx", ("L", "pwlog")).i("STA", "zp", SWP)
    _cursor(p, ZX, "g_zx")
    p.i("LDX", "zp", ZX).i("LDA", "zp", SWP).i("STA", "zpx", ZBUF)
    _cursor(p, ZY, "g_zy")
    p.i("LDX", "zp", ZY).i("LDA", "zpx", ZBUF).i("STA", "zp", LFO)


def _porta(p, v, b):
    """Slide the sounding pitch toward the note: a byte borrow chain, then a
    bounded step whose last stride snaps rather than overshooting."""

    def n(tag):
        return "v%d_%s" % (v, tag)

    p.i("SEC")
    p.i("LDA", "zp", b + NLO).i("SBC", "zp", b + CLO).i("STA", "zp", b + DLO)
    p.i("LDA", "zp", b + NHI).i("SBC", "zp", b + CHI).i("STA", "zp", b + DHI)
    p.i("BCC", "rel", ("L", n("p_dn")))
    p.i("LDA", "zp", b + DHI).i("BNE", "rel", ("L", n("p_upf")))
    p.i("LDA", "zp", b + RATE).i("CMP", "zp", b + DLO).i("BCC", "rel", ("L", n("p_upf")))
    p.i("JMP", "abs", ("L", n("p_snap")))
    p.label(n("p_upf"))
    p.i("LDA", "zp", b + CLO).i("CLC").i("ADC", "zp", b + RATE).i("STA", "zp", b + CLO)
    p.i("LDA", "zp", b + CHI).i("ADC", "imm", 0).i("STA", "zp", b + CHI)
    p.i("JMP", "abs", ("L", n("p_end")))
    p.label(n("p_dn"))
    p.i("LDA", "zp", b + DHI).i("CMP", "imm", 0xFF).i("BNE", "rel", ("L", n("p_dnf")))
    p.i("LDA", "zp", b + DLO).i("CLC").i("ADC", "zp", b + RATE)
    p.i("BCC", "rel", ("L", n("p_dnf")))
    p.i("JMP", "abs", ("L", n("p_snap")))
    p.label(n("p_dnf"))
    p.i("LDA", "zp", b + CLO).i("SEC").i("SBC", "zp", b + RATE).i("STA", "zp", b + CLO)
    p.i("LDA", "zp", b + CHI).i("SBC", "imm", 0).i("STA", "zp", b + CHI)
    p.i("JMP", "abs", ("L", n("p_end")))
    p.label(n("p_snap"))
    p.i("LDA", "zp", b + NLO).i("STA", "zp", b + CLO)
    p.i("LDA", "zp", b + NHI).i("STA", "zp", b + CHI)
    p.label(n("p_end"))


def _filter(p, b):
    """Voice 3's filter block: the 11-bit cutoff lane pair and two flag cells."""
    p.i("LDA", "zp", b + CUT).i("CLC").i("ADC", "imm", SWEEP & 0xFF).i("STA", "zp", b + CUT)
    p.i("LDA", "zp", b + CUTH).i("ADC", "imm", SWEEP >> 8).i("STA", "zp", b + CUTH)
    p.i("LDA", "zp", b + CUT).i("STA", "abs", SID + 0x15)  # $D415 uses bits 0-2 only
    p.i("LDA", "zp", b + CUTH).i("STA", "abs", SID + 0x16)
    p.i("LDA", "zp", b + FCTL).i("AND", "imm", 0x0F).i("STA", "zp", b + FCTL)
    p.i("LDA", "zp", LFO).i("AND", "imm", 0x70).i("ORA", "zp", b + FCTL).i("STA", "zp", b + FCTL)
    p.i("LDA", "zp", b + FCTL).i("STA", "abs", SID + 0x17)
    p.i("LDA", "zp", b + FVOL).i("AND", "imm", 0xF0).i("ORA", "imm", VOL)
    p.i("STA", "zp", b + FVOL).i("STA", "abs", SID + 0x18)


def _voice(p, v):
    """Emit voice ``v``: identical structure, shifted zp/shadow/SID bases."""
    b, sh, sidb = ZPV[v], SHADOW + 7 * v, SID + 7 * v

    def n(tag):
        return "v%d_%s" % (v, tag)

    def lb(tag, off=0):
        return ("L", n(tag), off) if off else ("L", n(tag))

    p.label(n("tick"))
    p.i("DEC", "zp", b + DUR).i("BEQ", "rel", lb("fetch"))
    p.i("LDA", "zp", b + DUR)
    p.i("CMP", "imm", 2).i("BEQ", "rel", lb("kill"))
    p.i("CMP", "imm", 1).i("BEQ", "rel", lb("test"))
    p.i("JMP", "abs", lb("tail"))
    p.label(n("kill"))  # hard restart at -2 frames: zero ADSR, drop the gate
    p.i("LDA", "imm", 0).i("STA", "zp", b + AD).i("STA", "zp", b + SR)
    p.i("LDA", "zp", b + CTL).i("AND", "imm", 0xFF ^ GATE_BIT).i("STA", "zp", b + CTL)
    p.i("JMP", "abs", lb("tail"))
    p.label(n("test"))  # hard restart at -1 frame: TEST resets the oscillator
    p.i("LDA", "zp", b + WAVE).i("ORA", "imm", TEST_BIT).i("STA", "zp", b + CTL)
    p.i("JMP", "abs", lb("tail"))
    p.label(n("fetch")).i("LDY", "imm", 0).i("LDA", "indy", b + PTR)
    p.i("BPL", "rel", lb("note"))
    p.i("AND", "imm", 3).i("TAX")  # Follin: dispatch through paired lo/hi tables
    p.i("LDA", "absx", lb("cmdlo")).i("STA", "abs", lb("jmpv", 1))
    p.i("LDA", "absx", lb("cmdhi")).i("STA", "abs", lb("jmpv", 2))
    p.label(n("jmpv")).i("JMP", "abs", 0)  # SMC operand: the dispatch head
    p.label(n("c_vib"))  # $80 dd: vibrato depth mask (arity 1)
    p.i("INY").i("LDA", "indy", b + PTR).i("STA", "zp", b + DEPTH)
    p.i("LDA", "zp", b + PTR).i("CLC").i("ADC", "imm", 2).i("STA", "zp", b + PTR)
    p.i("BCC", "rel", lb("v_nc")).i("INC", "zp", b + PTR + 1)
    p.label(n("v_nc")).i("JMP", "abs", lb("fetch"))
    p.label(n("c_off"))  # $81 dd: gate off, rest dd frames (arity 1)
    p.i("LDA", "zp", b + WAVE).i("STA", "zp", b + CTL)
    p.i("INY").i("LDA", "indy", b + PTR).i("STA", "zp", b + DUR)
    p.i("LDA", "zp", b + PTR).i("CLC").i("ADC", "imm", 2).i("STA", "zp", b + PTR)
    p.i("BCC", "rel", lb("tail")).i("INC", "zp", b + PTR + 1)
    p.i("JMP", "abs", lb("tail"))
    p.label(n("c_loop"))  # $82 ll hh: rewrite the cursor (control operator)
    p.i("INY").i("LDA", "indy", b + PTR).i("TAX")
    p.i("INY").i("LDA", "indy", b + PTR).i("STA", "zp", b + PTR + 1)
    p.i("STX", "zp", b + PTR).i("JMP", "abs", lb("fetch"))
    p.label(n("c_raw"))  # $83 (reg val)* $FF: arity is the decoded length, not a constant
    p.label(n("r_loop"))
    p.i("INY").i("LDA", "indy", b + PTR).i("BMI", "rel", lb("r_end"))
    p.i("TAX").i("INY").i("LDA", "indy", b + PTR).i("STA", "absx", SID)
    p.i("JMP", "abs", lb("r_loop"))
    p.label(n("r_end"))
    p.i("TYA").i("SEC").i("ADC", "zp", b + PTR).i("STA", "zp", b + PTR)
    p.i("BCC", "rel", lb("r_nc")).i("INC", "zp", b + PTR + 1)
    p.label(n("r_nc")).i("JMP", "abs", lb("fetch"))
    p.label(n("note"))  # nn dd: scale degree + duration; clears TEST, gates on
    p.i("TAX").i("JSR", "abs", ("L", "note_fetch"))
    p.i("STA", "zp", b + NLO).i("STX", "zp", b + NHI)
    p.i("LDA", "zp", b + WAVE).i("ORA", "imm", GATE_BIT).i("STA", "zp", b + CTL)
    p.i("LDA", "zp", b + ADEF).i("STA", "zp", b + AD)
    p.i("LDA", "zp", b + SRDEF).i("STA", "zp", b + SR)
    p.i("LDA", "imm", 0).i("STA", "zp", b + PHASE)
    p.i("INY").i("LDA", "indy", b + PTR).i("STA", "zp", b + DUR)
    p.i("LDA", "zp", b + PTR).i("CLC").i("ADC", "imm", 2).i("STA", "zp", b + PTR)
    p.i("BCC", "rel", lb("tail")).i("INC", "zp", b + PTR + 1)
    p.label(n("tail"))  # vibrato straight to the chip, envelope/control via the shadow
    p.i("INC", "zp", b + PHASE)
    _porta(p, v, b)
    p.i("LDA", "zp", b + PHASE).i("AND", "zp", b + DEPTH).i("TAX")
    p.i("LDA", "absx", ("L", "vibtab"))
    p.i("CLC").i("ADC", "zp", b + CLO).i("STA", "abs", sidb + 0)
    p.i("LDA", "zp", b + CHI).i("ADC", "imm", 0).i("STA", "abs", sidb + 1)  # carry chain -> u16
    p.i("LDA", "zp", b + AD).i("STA", "abs", sh + 5)
    p.i("LDA", "zp", b + SR).i("STA", "abs", sh + 6)
    p.i("LDA", "zp", b + CTL).i("STA", "abs", sh + 4)
    for k in FLUSH:
        p.i("LDA", "abs", sh + k).i("STA", "abs", sidb + k)
    if v == VOICES - 1:
        _filter(p, b)


def first_note(script):
    return next(NOTES[op] for op, _n in script if isinstance(op, int))


def build_image():
    """Assemble play + data, then init against its labels; return (mem, labels)."""
    p = Asm(PLAY)
    _global(p)
    for v in range(VOICES):
        _voice(p, v)
    p.i("RTS")
    p.label("note_fetch")  # shared helper: degree in X, freq lo in A / hi in X
    p.i("LDA", "absx", ("L", "pitchlo")).i("PHA")
    p.i("LDA", "absx", ("L", "pitchhi")).i("TAX")
    p.i("PLA").i("RTS")
    for v in range(VOICES):
        cmds = ("v%d_c_vib" % v, "v%d_c_off" % v, "v%d_c_loop" % v, "v%d_c_raw" % v)
        p.label("v%d_cmdlo" % v).byte(*[("LOL", c) for c in cmds])
        p.label("v%d_cmdhi" % v).byte(*[("HIL", c) for c in cmds])
    p.label("pitchlo").byte(*[f & 0xFF for f in NOTES])
    p.label("pitchhi").byte(*[f >> 8 for f in NOTES])
    p.label("vibtab").byte(*VIBTAB)
    p.label("pwlog").byte(*([0] * 16))
    for v, script in enumerate(SCRIPTS):
        assert script_frames(script) == BARS, "voice %d spans %d frames" % (
            v,
            script_frames(script),
        )
        p.label("script%d" % v)
        for item, arg in script:
            if item == "vib":
                p.byte(0x80, arg)
            elif item == "rest":
                p.byte(0x81, arg)
            elif item == "raw":
                p.byte(0x83, *arg, 0xFF)
            else:
                p.byte(item, arg)
        p.byte(0x82, ("LOL", "script%d" % v), ("HIL", "script%d" % v))
    p.label("imgend")

    a = Asm(INIT)
    a.i("LDA", "imm", 0)
    for k in range(ZSPAN):
        a.i("STA", "zp", (ZBUF + ZLO + k) & 0xFF)
    for off in (PH0, PH0 + 1, PH0 + 2, TICK, PWL, PWH, LOGI, LFO, SWP):
        a.i("STA", "zp", off)
    a.i("LDA", "imm", ZLO).i("STA", "zp", ZX)
    a.i("LDA", "imm", (ZLO + ZSPAN // 4) & 0xFF).i("STA", "zp", ZY)
    for v in range(VOICES):
        b = ZPV[v]
        a.i("LDA", "imm", ("LOL", "script%d" % v)).i("STA", "zp", b + PTR)
        a.i("LDA", "imm", ("HIL", "script%d" % v)).i("STA", "zp", b + PTR + 1)
        a.i("LDA", "imm", 1).i("STA", "zp", b + DUR)
        a.i("LDA", "imm", 0)
        for off in (PHASE, NLO, NHI, DEPTH, CTL, AD, SR, DLO, DHI):
            a.i("STA", "zp", b + off)
        a.i("LDA", "imm", WAVEF[v]).i("STA", "zp", b + WAVE)
        a.i("LDA", "imm", ADSR[v][0]).i("STA", "zp", b + ADEF)
        a.i("LDA", "imm", ADSR[v][1]).i("STA", "zp", b + SRDEF)
        a.i("LDA", "imm", PORTA[v]).i("STA", "zp", b + RATE)
        note = first_note(SCRIPTS[v])
        a.i("LDA", "imm", note & 0xFF).i("STA", "zp", b + CLO)
        a.i("LDA", "imm", note >> 8).i("STA", "zp", b + CHI)
        a.i("LDA", "imm", PW[v][0]).i("STA", "abs", SID + 7 * v + 2)
        a.i("LDA", "imm", PW[v][1]).i("STA", "abs", SID + 7 * v + 3)
    b = ZPV[VOICES - 1]
    a.i("LDA", "imm", 0).i("STA", "zp", b + CUT).i("STA", "zp", b + CUTH)
    a.i("LDA", "imm", FILT3).i("STA", "zp", b + FCTL)
    a.i("LDA", "imm", LP).i("STA", "zp", b + FVOL)
    a.i("LDA", "imm", 0x0F).i("STA", "abs", SID + 0x18)
    a.i("RTS")

    mem = bytearray(0x10000)
    code = p.assemble()
    a.labels.update(p.labels)
    init_code = a.assemble()
    assert INIT + len(init_code) <= PLAY, "init overruns the play routine"
    mem[INIT : INIT + len(init_code)] = init_code
    mem[PLAY : PLAY + len(code)] = code
    return mem, p.labels


def run_vm(mem, frames):
    """Run init+play on the P-Code VM; post-init RAM, per-frame writes, grids."""
    vm = PcodeVM(bytes(mem))
    vm.wlog = []
    cache = {}
    run_sub(vm, INIT, cache, lift)
    init_writes = [(r, v) for _c, r, v in vm.wlog]
    ram0 = bytearray(vm.mem)
    per_frame, grids = [], []
    for _ in range(frames):
        vm.wlog = []
        run_sub(vm, PLAY, cache, lift)
        per_frame.append([(r, v) for _c, r, v in vm.wlog])
        grids.append([vm.mem[SID + i] for i in range(25)])
    return init_writes, ram0, per_frame, grids, bytearray(vm.mem)


_BIN = {
    "|": 1, "^": 2, "&": 3,
    "==": 4, "!=": 4, "<s": 4, ">=s": 4, "<=s": 4, ">s": 4, "<": 4, ">=": 4, "<=": 4, ">": 4,
    "<<": 5, ">>": 5, "+": 6, "-": 6,
}  # fmt: skip


class Tok:
    """Tokenizer for the emitted minimized dialect."""

    RE = re.compile(
        r"\s*(?:(\$[0-9A-Fa-f]+)|(\d+)|([A-Za-z_][\w.]*)"
        r"|(<<|>>|==|!=|<s|>=s|<=s|>s|<=|>=|<|>|[-+&|^!(){}\[\]:=,]))"
    )

    def __init__(self, text):
        self.toks, pos = [], 0
        while pos < len(text):
            m = self.RE.match(text, pos)
            if not m or m.end() == pos:
                raise SyntaxError("lex: %r" % text[pos : pos + 20])
            pos = m.end()
            self.toks.append(m.group(m.lastindex))
        self.k = 0

    def peek(self):
        return self.toks[self.k] if self.k < len(self.toks) else None

    def next(self):
        t = self.peek()
        self.k += 1
        return t

    def expect(self, t):
        got = self.next()
        if got != t:
            raise SyntaxError("expected %r got %r" % (t, got))


def _suffix(tk):
    while tk.peek() == ":":
        tk.next()
        tk.next()


def _atom(tk):
    t = tk.next()
    if t == "(":
        e = _expr(tk, 0)
        tk.expect(")")
        _suffix(tk)
        return e
    if t == "!":
        return ("not", _atom(tk))
    if t == "-":
        return ("neg", _atom(tk))
    if t.startswith("$"):
        return ("num", int(t[1:], 16))
    if t.isdigit():
        return ("num", int(t))
    if tk.peek() == "(":
        tk.next()
        args = [_expr(tk, 0)]
        while tk.peek() == ",":
            tk.next()
            args.append(_expr(tk, 0))
        tk.expect(")")
        _suffix(tk)
        return ("call", t, tuple(args))
    if tk.peek() == "[":
        tk.next()
        idx = _expr(tk, 0)
        _suffix(tk)
        tk.expect("]")
        _suffix(tk)
        return ("index", t, idx)
    return ("name", t)


def _expr(tk, minp):
    lhs = _atom(tk)
    while tk.peek() in _BIN and _BIN[tk.peek()] >= minp:
        op = tk.next()
        rhs = _expr(tk, _BIN[op] + 1)
        lhs = ("bin", op, lhs, rhs)
    return lhs


def parse_expr(text):
    tk = Tok(text)
    e = _expr(tk, 0)
    if tk.peek() is not None:
        raise SyntaxError("trailing %r in %r" % (tk.peek(), text))
    return e


def parse_block(lines, i):
    """Parse the emitted statement dialect into (stmts, index of closing line)."""
    out = []
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line in ("}", "} else {"):
            return out, i
        if re.fullmatch(r"\$[0-9A-Fa-f]+:", line):
            out.append(("label", line[:-1]))
        elif line in ("continue", "break", "ret"):
            out.append((line,))
        elif line.startswith("unobserved"):
            out.append(("unobserved", line.split()[1]))
        elif line.startswith("goto ("):
            out.append(("dgoto", parse_expr(line[5:])))
        elif line.startswith("goto "):
            out.append(("goto", line[5:].strip()))
        elif line.startswith("call $"):
            out.append(("call", int(line[6:10], 16)))
        elif line == "loop {":
            body, i = parse_block(lines, i + 1)
            out.append(("loop", body))
        elif line == "switch {":
            i += 1
            cases = []
            while lines[i].strip() != "}":
                m = re.fullmatch(r"case (\$[0-9A-Fa-f]+): \{", lines[i].strip())
                if not m:
                    raise SyntaxError("switch: %r" % lines[i])
                body, i = parse_block(lines, i + 1)
                cases.append((m.group(1), body))
                i += 1
            out.append(("switch", cases))
        elif line.startswith("if ") and line.endswith("{"):
            then, i = parse_block(lines, i + 1)
            els = []
            if lines[i].strip() == "} else {":
                els, i = parse_block(lines, i + 1)
            out.append(("if", parse_expr(line[3:-1].strip()), then, els))
        else:
            m = re.fullmatch(r"([\w.]+)\[(.*)\](?::\d)? = (.*)", line)
            if m:  # a span store: the row the index names, not a state cell
                out.append(("sto", m.group(1), parse_expr(m.group(2)), parse_expr(m.group(3))))
                i += 1
                continue
            m = re.fullmatch(r"([\w.]+)(?::\d)? = (.*)", line)
            if not m:
                raise SyntaxError("stmt: %r" % line)
            out.append(("asg", m.group(1), parse_expr(m.group(2))))
        i += 1
    raise SyntaxError("unterminated block")


def proc_entries(text):
    """Entry addresses of every procedure the emitter printed, in text order."""
    return [int(m, 16) for m in re.findall(r"^sub_([0-9A-Fa-f]{4}) \{", text, re.M)]


def extract_proc(text, entry):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("sub_%04X {" % entry):
            return parse_block(lines, i + 1)[0]
    raise KeyError("sub_%04X not in emitted text" % entry)


_CELL = re.compile(r"(?:zp_([0-9A-Fa-f]{2})|(?:m|ctr|idx|pos)_([0-9A-Fa-f]{4}))$")
_PAIRRE = re.compile(r"ptr_([0-9A-Fa-f]{4})_(lo|hi)$")
_PAIRBASE = re.compile(r"ptr_([0-9A-Fa-f]{4})(?:_(lo|hi))?$")
_SIDNAME = {R.sid_name(SID + r): r for r in range(25) if R.sid_name(SID + r)}


def cell_addr(name):
    m = _CELL.match(name)
    if m:
        return int(m.group(1) or m.group(2), 16)
    m = _PAIRRE.match(name)
    if m:
        return int(m.group(1), 16) + (m.group(2) == "hi")
    return None


def sid_target(name):
    """SID register index 0-24 for a hardware sink name, else None."""
    return _SIDNAME.get(name)


def is_shadow(name):
    a = cell_addr(name)
    return a is not None and SHADOW <= a < SHADOW + 7 * VOICES


_UCMP = {"<": "ULT", "<=": "ULE", ">": "UGT", ">=": "UGE"}


def _wid(e):
    """Byte width of a term, by the dialect's own rule (as ``Machine._val`` reads it)."""
    k = e[0]
    if k == "num":
        return 2 if e[1] > 0xFF else 1
    if k == "call":
        return 2 if e[1] == "zext2" else 1
    if k == "bin":
        if e[1] == "<<":
            return 2
        if e[1] in _UCMP or e[1] in ("==", "!="):
            return 1
        return max(_wid(e[2]), _wid(e[3]))
    if k == "neg":
        return _wid(e[1])
    return 1


def _z3_expr(e, env, z3, w=16):
    """The emitted byte-domain term as a width-``w`` Z3 bitvector (0/1 for predicates)."""
    k = e[0]
    one, zero = z3.BitVecVal(1, w), z3.BitVecVal(0, w)
    if k == "num":
        return z3.BitVecVal(e[1] & ((1 << w) - 1), w)
    if k == "name":
        return env[e[1]]
    if k == "not":
        return z3.If(_z3_expr(e[1], env, z3, w) == zero, one, zero)
    if k == "call" and e[1] == "zext2":
        return _z3_expr(e[2][0], env, z3, w)
    if k == "call" and e[1] == "carry":
        a, b = (_z3_expr(x, env, z3, w) for x in e[2])
        return z3.If(z3.ULT(z3.BitVecVal(0xFF, w), a + b), one, zero)
    if k == "bin" and e[1] in ("+", "-", "&", "|", "^", "<<", ">>"):
        a, b = _z3_expr(e[2], env, z3, w), _z3_expr(e[3], env, z3, w)
        if e[1] == ">>":
            return z3.LShR(a, b)
        v = {"+": a + b, "-": a - b, "&": a & b, "|": a | b, "^": a ^ b, "<<": a << b}[e[1]]
        if e[1] in ("+", "-", "<<"):
            return v & ((1 << (8 * _wid(e))) - 1)
        return v
    if k == "bin" and e[1] in _UCMP:
        a, b = _z3_expr(e[2], env, z3, w), _z3_expr(e[3], env, z3, w)
        return z3.If(getattr(z3, _UCMP[e[1]])(a, b), one, zero)
    if k == "bin" and e[1] in ("==", "!="):
        a, b = _z3_expr(e[2], env, z3, w), _z3_expr(e[3], env, z3, w)
        return z3.If(a == b if e[1] == "==" else a != b, one, zero)
    raise ValueError("z3: %r" % (e,))


def _names(e, out):
    if not isinstance(e, tuple) or not e:
        return out
    if e[0] == "name":
        out.add(e[1])
        return out
    for kid in e:
        _names(kid, out)
    return out


def _base_split(e):
    """``(base cell, addend)`` for ``b`` or ``b + x`` / ``x + b``, else None."""
    if e[0] == "name":
        return (e[1], None) if cell_addr(e[1]) is not None else None
    if e[0] == "bin" and e[1] == "+":
        for base, other in ((e[2], e[3]), (e[3], e[2])):
            if base[0] == "name" and cell_addr(base[1]) is not None:
                return base[1], other
    return None


def prove_pair(lo, hi, z3):
    """Z3-prove Concat(hi, lo) equals one wide sum; return its decomposition."""
    got_lo, got_hi = _base_split(lo), _base_split(hi)
    if got_lo is None or got_hi is None:
        return None
    (b_lo, addend), b_hi = got_lo, got_hi[0]
    ns = set()
    _names(lo, ns)
    _names(hi, ns)
    env = {n: z3.BitVec(n.replace(".", "_"), 16) for n in ns}
    cons = [z3.ULE(env[n], 0xFF) for n in ns]
    lo_v, hi_v = _z3_expr(lo, env, z3), _z3_expr(hi, env, z3)
    wide = (env[b_hi] << 8 | env[b_lo]) & 0xFFFF
    if addend is not None:
        wide = (wide + _z3_expr(addend, env, z3)) & 0xFFFF
    s = z3.Solver()
    s.add(z3.And(*cons), ((hi_v & 0xFF) << 8 | (lo_v & 0xFF)) != wide)
    if s.check() != z3.unsat:
        return None
    return b_lo, b_hi, addend


_CMPZ = {"<": "ULT", "<=": "ULE", ">": "UGT", ">=": "UGE"}


def _z3_cond(e, env, z3):
    """The emitted guard as a Z3 Bool; raises on anything not modelled."""
    if e[0] == "not":
        return z3.Not(_z3_cond(e[1], env, z3))
    if e[0] == "bin" and e[1] in _CMPZ:
        return getattr(z3, _CMPZ[e[1]])(_z3_expr(e[2], env, z3), _z3_expr(e[3], env, z3))
    if e[0] == "bin" and e[1] in ("==", "!="):
        a, b = _z3_expr(e[2], env, z3), _z3_expr(e[3], env, z3)
        return a == b if e[1] == "==" else a != b
    return _z3_expr(e, env, z3) != 0


def prove_advance(k, carried, cond, p, z3):
    """Z3-prove ``lo = p + k`` under guard ``cond`` is one u16 add on the pair.

    The guard's meaning is proved, not matched, so every spelling the extractor
    picks for the same deferred carry folds through this one rule."""
    lo, hi = z3.BitVec(p.replace(".", "_"), 16), z3.BitVec("hi", 16)
    try:
        nocarry = _z3_cond(cond, {p: lo}, z3)
    except (KeyError, ValueError):
        return False
    t = (lo + k) & 0xFF
    wide = ((hi << 8 | lo) + k) & 0xFFFF
    s = z3.Solver()
    if carried:
        folded = (z3.If(nocarry, hi & 0xFF, (hi + 1) & 0xFF) << 8 | t) & 0xFFFF
        s.add(z3.ULE(lo, 0xFF), z3.ULE(hi, 0xFF), folded != wide)
    else:
        s.add(z3.ULE(lo, 0xFF), z3.ULE(hi, 0xFF), nocarry, ((hi << 8) | t) != wide)
    return s.check() == z3.unsat


def _lane_run(stmts, i):
    """``(addresses, term per address, end)`` for a run storing consecutive byte lanes.

    Locals are inlined as they are bound, so the run is read at whatever width the
    extractor spelled it; a local that reads a lane already stored ends the run,
    which is what makes moving the wide store to the run's end sound."""
    env, lanes, order, stored, j = {}, {}, [], set(), i
    while j < len(stmts) and stmts[j][0] == "asg":
        tgt, raw = stmts[j][1], stmts[j][2]
        rhs = _subst(raw, env)
        ns = set()
        _names(raw, ns)
        if ns & stored:
            break
        a = cell_addr(tgt)
        if a is None:
            if sid_target(tgt) is not None:
                break
            env[tgt] = rhs
        elif (order and a != order[-1] + 1) or a in lanes:
            break
        else:
            lanes[a], stored = rhs, stored | {tgt}
            order.append(a)
        j += 1
    return order, lanes, j


def prove_wide(order, lanes, n, z3, copy_ok):
    """Z3-prove the first ``n`` lanes are one width-``8n`` update; name its operand.

    Candidates come off the cells the lane terms read — the pair itself, any adjacent
    pair, any byte, or the constant the terms reduce to — and each is *proved*, so a
    borrow chain, a deferred-carry step and a straight copy all fold through one rule."""
    lo, w = order[0], 8 * n + 8
    exprs = [lanes[lo + k] for k in range(n)]
    names = set()
    for e in exprs:
        _names(e, names)
    cells = {cell_addr(x): x for x in names if cell_addr(x) is not None}
    if len(cells) != len(names):
        return None
    env = {x: z3.BitVec(x, w) for x in names}
    cons = [z3.ULE(env[x], 0xFF) for x in names]

    def wide(base):
        v = z3.BitVecVal(0, w)
        for k in range(n):
            v = v | (env[cells[base + k]] << (8 * k))
        return v

    got = z3.BitVecVal(0, w)
    try:
        for k, e in enumerate(exprs):
            got = got | ((_z3_expr(e, env, z3, w) & 0xFF) << (8 * k))
    except ValueError:  # a term the byte algebra does not model: no wide reading
        return None
    self_used = all(lo + k in cells for k in range(n))
    srcs = []
    if names <= {cells[lo + k] for k in range(n) if lo + k in cells}:
        k = 0
        for j, e in enumerate(exprs):
            k |= (_z3_eval(e, {x: 0 for x in names}) & 0xFF) << (8 * j)
        srcs.append((("const", k), z3.BitVecVal(k, w)))
    pairs = [a for a in sorted(cells) if all(a + k in cells for k in range(n)) and a != lo]
    srcs += [(("pair", a, n), wide(a)) for a in pairs]
    srcs += [(("byte", a, 1), env[cells[a]]) for a in sorted(cells) if a not in range(lo, lo + n)]
    cands = []
    if self_used:
        me = (("self", lo, n), wide(lo))
        cands += [(op, me, b) for b in srcs for op in "+-"]
    cands += [(op, a, b) for a in srcs for b in srcs if a is not b for op in "+-"]
    if copy_ok:
        cands += [("=", a, None) for a in srcs]
    mask = (1 << (8 * n)) - 1
    for op, a, b in cands:
        cand = a[1] if b is None else (a[1] + b[1] if op == "+" else a[1] - b[1])
        s = z3.Solver()
        s.add(z3.And(*cons), (got & mask) != (cand & mask))
        if s.check() == z3.unsat:
            return op, a[0], None if b is None else b[0]
    return None


def _z3_eval(e, env):
    """Concrete byte-domain evaluation of a term over an all-numeric environment."""
    k = e[0]
    if k == "num":
        return e[1]
    if k == "name":
        return env[e[1]]
    if k == "not":
        return int(_z3_eval(e[1], env) == 0)
    if k == "call" and e[1] == "zext2":
        return _z3_eval(e[2][0], env)
    if k == "call" and e[1] == "carry":
        return int(_z3_eval(e[2][0], env) + _z3_eval(e[2][1], env) > 0xFF)
    a, b = _z3_eval(e[2], env), _z3_eval(e[3], env)
    op = e[1]
    if op in ("+", "-"):
        return (a + b if op == "+" else a - b) & 0xFF
    if op in ("&", "|", "^"):
        return {"&": a & b, "|": a | b, "^": a ^ b}[op]
    return int({"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "==": a == b, "!=": a != b}[op])


_WCMP = {">=": "UGE", "<": "ULT", ">": "UGT", "<=": "ULE"}


def prove_wcmp(cond, z3):
    """Z3-prove a guard is one wide compare of two lane pairs, whatever its spelling.

    The borrow chain the branch tests is a 16-bit relation; proving the guard's
    meaning (not matching its shape) is what lets every spelling of it fold."""
    names = set()
    _names(cond, names)
    cells = {cell_addr(x): x for x in names if cell_addr(x) is not None}
    if len(cells) != len(names) or len(cells) < 4:
        return None
    w = 32
    env = {x: z3.BitVec(x, w) for x in names}
    cons = [z3.ULE(env[x], 0xFF) for x in names]
    try:
        truth = _z3_expr(cond, env, z3, w) != z3.BitVecVal(0, w)
    except ValueError:
        return None

    def wide(a):
        return env[cells[a]] | (env[cells[a + 1]] << 8)

    pairs = [a for a in sorted(cells) if a + 1 in cells]
    for a in pairs:
        for b in pairs:
            for op, fn in _WCMP.items():
                if a == b:
                    continue
                s = z3.Solver()
                s.add(z3.And(*cons), truth != getattr(z3, fn)(wide(a), wide(b)))
                if s.check() == z3.unsat:
                    return op, ("pair", a, 2), ("pair", b, 2)
    return None


def prove_carry_out(expr, order, lanes, n, got, z3):
    """Z3-prove ``expr`` is the carry the width-``8n`` update dropped."""
    op, a, b = got
    lo, w = order[0], 8 * n + 8
    names = set()
    _names(expr, names)
    for k in range(n):
        _names(lanes[lo + k], names)
    cells = {cell_addr(x): x for x in names if cell_addr(x) is not None}
    if len(cells) != len(names) or b is None:
        return False
    env = {x: z3.BitVec(x, w) for x in names}
    cons = [z3.ULE(env[x], 0xFF) for x in names]

    def wide(src):
        if src[0] == "const":
            return z3.BitVecVal(src[1], w)
        v = z3.BitVecVal(0, w)
        for k in range(src[2]):
            if src[1] + k not in cells:
                return None
            v = v | (env[cells[src[1] + k]] << (8 * k))
        return v

    val, base = wide(b), wide(a)
    if val is None or base is None:
        return False
    full = base + val if op == "+" else base - val
    want = z3.LShR(full, 8 * n) & 1
    s = z3.Solver()
    try:
        s.add(z3.And(*cons), _z3_expr(expr, env, z3, w) != want)
    except ValueError:
        return False
    return s.check() == z3.unsat


def _store_addr(name):
    """Concrete address a statement target writes (state cell or SID sink)."""
    a = cell_addr(name)
    if a is not None:
        return a
    t = sid_target(name)
    return None if t is None else SID + t


def _reads(stmts, out):
    for s in stmts:
        for part in s[1:]:
            if isinstance(part, tuple):
                _names(part, out)
        for b in _bodies(s):
            _reads(b, out)
    return out


def drop_dead_shadow(stmts):
    """Drop shadow stores no expression reads: unread and not hardware, so
    unobservable. Iterated, since dropping one can free the store it read."""
    while True:
        pruned = _prune(stmts, _reads(stmts, set()))
        if pruned == stmts:
            return stmts
        stmts = pruned


def drop_dead_locals(stmts):
    """Drop locals no expression in the procedure names: the folds consumed them.

    The call ABI's registers stay live: a callee reads its params and writes its
    returns without either appearing in an expression of the same procedure."""
    while True:
        live = _reads(stmts, set(_REGS))
        pruned = _prune_local(stmts, live)
        if pruned == stmts:
            return stmts
        stmts = pruned


def _prune_local(stmts, live):
    out = []
    for s in stmts:
        if s[0] == "asg" and cell_addr(s[1]) is None and sid_target(s[1]) is None:
            if s[1] not in live:
                continue
        elif s[0] == "if":
            s = ("if", s[1], _prune_local(s[2], live), _prune_local(s[3], live))
        elif s[0] == "loop":
            s = ("loop", _prune_local(s[1], live))
        elif s[0] == "switch":
            s = ("switch", [(l, _prune_local(b, live)) for l, b in s[1]])
        out.append(s)
    return out


def _prune(stmts, live):
    out = []
    for s in stmts:
        if s[0] == "asg" and is_shadow(s[1]) and s[1] not in live:
            continue
        if s[0] == "if":
            s = ("if", s[1], _prune(s[2], live), _prune(s[3], live))
        elif s[0] == "loop":
            s = ("loop", _prune(s[1], live))
        elif s[0] == "switch":
            s = ("switch", [(l, _prune(b, live)) for l, b in s[1]])
        out.append(s)
    return out


_FREQ = re.compile(r"(sid\.v[123]\.(?:freq|pw)|filter\.cutoff)_(lo|hi)$")


def _arm_writes(sl, names, spans):
    """Names a body assigns and the address spans it stores through; ⊤ is the space."""
    for s in sl:
        if s[0] == "asg":
            names.add(s[1])
        elif s[0] == "sto":
            a = _store_addr(s[1])
            spans.append((0, 0xFFFF) if a is None else (a, a + (1 << (8 * _wid(s[2]))) - 1))
        for b in _bodies(s):
            _arm_writes(b, names, spans)
    return names, spans


def _cross_window(s, last, defs):
    """The fold lookback a branch survives: what no body of ``s`` can disturb.

    The span join carries a cell no reachable store can name, so the fold reading that
    cell back must carry the same entries -- and drop every cell a body stores into,
    every span it stores through, and every term naming a local it assigns."""
    names, spans = _arm_writes([s], set(), [])
    keep = {}
    for cell, (pos, stored) in last.items():
        ns = set()
        _names(stored, ns)
        a = cell_addr(cell)
        if cell in names or ns & names or any(lo <= a <= hi for lo, hi in spans):
            continue
        keep[cell] = (pos, stored)
    return keep, {n: p for n, p in defs.items() if n not in names}


def _lane_fold(stmts, i, proofs, z3, targets):
    """Fold a lane run at ``i`` into one wide update (plus its carry-out), if proved.

    A plain lane-by-lane copy is only read as one word where a carry already links
    the lanes: two independent byte moves are not evidence of a wide quantity."""
    order, lanes, end = _lane_run(stmts, i)
    for n in range(len(order), 1, -1):
        got = prove_wide(order, lanes, n, z3, (order[0], n) in targets)
        if got is None:
            continue
        lo, carry, used = order[0], None, end
        if len(order) > n:  # the lane after the run may be the carry the add dropped
            cut = order[n]
            if prove_carry_out(lanes[cut], order, lanes, n, got, z3):
                carry = cut
            else:
                used = None
        if used is None:  # trailing lanes are not ours: refold from the run's start
            continue
        if got[0] != "=":
            targets.add((lo, n))
        proofs.append("wide%d(%s,%s%s)" % (8 * n, _addr_name(lo), got[0], got[1][0]))
        keep = [
            s
            for s in stmts[i:end]
            if cell_addr(s[1]) is None or cell_addr(s[1]) not in order[: n + (carry is not None)]
        ]
        rest = fold(keep, proofs, targets)  # lanes past the carry are their own quantity
        return rest + [("w16", lo, n, got[0], got[1], got[2], carry)], end
    return None


def _addr_name(a):
    if isinstance(a, str):  # a re-rolled slice names the voice, not the address
        return a
    return "zp_%02X" % a if a < 0x100 else "m_%04X" % a


def _lane_src(rhs, last, defs):
    """The cell holding ``rhs`` at this point, so a forwarded store folds like a read."""
    if rhs[0] == "name" and cell_addr(rhs[1]) is not None:
        return rhs
    ns = set()
    _names(rhs, ns)
    for cell, (pos, stored) in last.items():
        if stored == rhs and all(defs.get(x, -1) <= pos for x in ns):
            return ("name", cell)
    return rhs


def fold(stmts, proofs, targets=None):
    """Rewrite byte-lane spellings to wide statements, each instance Z3-proved.

    Two passes: the first finds the lane groups a carry links, the second lets the
    copies onto those groups fold too."""
    import z3  # pylint: disable=import-outside-toplevel

    if targets is None:
        targets = set()
        fold(stmts, [], targets)
        return fold(stmts, proofs, targets)
    out, i, last, defs = [], 0, {}, {}
    while i < len(stmts):
        s = stmts[i]
        if s[0] == "if":
            cond, wc = s[1], prove_wcmp(s[1], z3)
            if wc is not None:
                proofs.append(
                    "wide_cmp(%s%s%s)" % (_addr_name(wc[1][1]), wc[0], _addr_name(wc[2][1]))
                )
                cond = ("wcmp",) + wc
            out.append(("if", cond, fold(s[2], proofs, targets), fold(s[3], proofs, targets)))
            i, (last, defs) = i + 1, _cross_window(s, last, defs)
            continue
        if s[0] == "loop":
            out.append(("loop", fold(s[1], proofs, targets)))
            i, (last, defs) = i + 1, _cross_window(s, last, defs)
            continue
        if s[0] == "switch":
            out.append(("switch", [(lbl, fold(b, proofs, targets)) for lbl, b in s[1]]))
            i, (last, defs) = i + 1, _cross_window(s, last, defs)
            continue
        if s[0] != "asg":
            out.append(s)
            i, last, defs = i + 1, {}, {}
            continue
        nxt = stmts[i + 1] if i + 1 < len(stmts) else None
        voice = _match_freq_pair(s, nxt)
        if voice is not None:
            got = prove_pair(_lane_src(s[2], last, defs), _lane_src(nxt[2], last, defs), z3)
            if got is not None:
                b_lo, b_hi, addend = got
                proofs.append(
                    "pair_store(%s,%s)" % (_addr_name(cell_addr(b_lo)), _addr_name(cell_addr(b_hi)))
                )
                out.append(("st16", voice, b_lo, b_hi, addend))
                i += 2
                continue
        ps = _match_pair_set(s, nxt)
        if ps is not None:
            pair, lanes = ps
            proofs.append("pair_set(%s)" % pair)
            out.append(("set16", pair, lanes["lo"], lanes["hi"]))
            i += 2
            continue
        adv = _match_advance(stmts, i)
        if adv is not None:
            pair, k, carried, cond, p, used = adv
            if prove_advance(k, carried, cond, p, z3):
                proofs.append("advance(%s,+%d,%s)" % (pair, k, "wide" if carried else "nocarry"))
                out.append(("adv16", pair, k, carried))
                i += used
                continue
        got = _lane_fold(stmts, i, proofs, z3, targets)
        if got is not None:
            out.extend(got[0])
            run, i = stmts[i : got[1]], got[1]
            last = {s[1]: (i, s[2]) for s in run if cell_addr(s[1]) is not None}
            defs = {s[1]: i for s in run if cell_addr(s[1]) is None}
            continue
        if cell_addr(s[1]) is not None:
            last[s[1]] = (i, s[2])
        else:
            defs[s[1]] = i
        out.append(s)
        i += 1
    return out


def _match_freq_pair(s, nxt):
    """The multi-byte SID register whose lo/hi sinks these adjacent stores are."""
    if s[0] != "asg" or nxt is None or nxt[0] != "asg":
        return None
    m1, m2 = _FREQ.match(s[1]), _FREQ.match(nxt[1])
    if not m1 or not m2 or m1.group(1) != m2.group(1):
        return None
    return m1.group(1) if (m1.group(2), m2.group(2)) == ("lo", "hi") else None


def _match_pair_set(s, nxt):
    """Match adjacent independent stores to both lanes of one ptr pair."""
    if s[0] != "asg" or nxt is None or nxt[0] != "asg" or s[1] == nxt[1]:
        return None
    m1, m2 = _PAIRRE.match(s[1]), _PAIRRE.match(nxt[1])
    if not m1 or not m2 or m1.group(1) != m2.group(1):
        return None
    ns = set()
    _names(nxt[2], ns)
    if s[1] in ns:
        return None
    return "ptr_%s" % m1.group(1), {m1.group(2): s[2], m2.group(2): nxt[2]}


def _subst(e, env):
    """Inline the window's local bindings so the guard names only the read cell."""
    if e[0] == "name":
        return env.get(e[1], e)
    if e[0] in ("not", "neg"):
        return (e[0], _subst(e[1], env))
    if e[0] == "bin":
        return (e[0], e[1], _subst(e[2], env), _subst(e[3], env))
    if e[0] == "call":
        return (e[0], e[1], tuple(_subst(a, env) for a in e[2]))
    if e[0] == "index":
        return (e[0], e[1], _subst(e[2], env))
    return e


def _add_const(e, p):
    """``k`` if ``e`` is ``p + k`` in either order, else None."""
    if e[0] != "bin" or e[1] != "+":
        return None
    for a, b in ((e[2], e[3]), (e[3], e[2])):
        if a == ("name", p) and b[0] == "num":
            return b[1]
    return None


def _match_advance(stmts, i):
    """Match ``lo = <lo> + k`` under a deferred-carry guard, however it is spelled.

    The window's temporaries are inlined and the guard is handed to Z3 rather than
    compared, so a copy of the cell and the cell read in place are one rule; a read of
    the cell after its own store is the new value and refuses."""
    env, j, k, pair, lo, taken = {}, i, None, None, None, set()
    while j < len(stmts) and stmts[j][0] == "asg" and j - i <= 4:
        tgt, raw = stmts[j][1], stmts[j][2]
        ns = _names(raw, set())
        if lo is not None and lo in ns:
            return None
        rhs, pm = _subst(raw, env), _PAIRRE.match(tgt)
        if pm and pm.group(2) == "lo" and k is None:
            k = _add_const(rhs, tgt)
            if k is None:
                return None
            pair, lo = pm.group(1), tgt
        elif cell_addr(tgt) is not None or sid_target(tgt) is not None:
            return None
        else:
            env[tgt] = rhs
            taken.add(tgt)
        j += 1
    if k is None or j >= len(stmts) or stmts[j][0] != "if" or stmts[j][2]:
        return None
    hi = "ptr_%s_hi" % pair
    els = stmts[j][3]
    if els in (
        [("asg", hi, ("bin", "+", ("name", hi), ("num", 1)))],
        [("asg", hi, ("bin", "+", ("num", 1), ("name", hi)))],
    ):
        carried = True
    elif len(els) == 1 and els[0][0] == "unobserved":
        carried = False
    else:
        return None
    if lo in _names(stmts[j][1], set()) or taken & _reads(stmts[j + 1 :], set()):
        return None  # the guard reads the stored cell, or a temporary outlives the window
    cond = _subst(stmts[j][1], env)
    return (
        ("ptr_%s" % pair, k, carried, cond, lo, j + 1 - i) if _names(cond, set()) == {lo} else None
    )


# ---- the classical passes: call resolution, the row read, guard-aware re-rolling ----


def _leaf_callee(stmts):
    """A callee that is straight-line register arithmetic, else None.

    Its post-state is the composition of its own assignments, so the body substituted
    at a call site is exactly what the call's returns mean in the caller."""
    if len(stmts) < 2 or stmts[-1] != ("ret",):
        return None
    body = stmts[:-1]
    for s in body:
        if s[0] != "asg" or _store_addr(s[1]) is not None:
            return None
    return body


def resolve_calls(procs):
    """Resolve every leaf callee into its call sites; return ``(procs, resolved)``.

    A resolved callee is reached by no root afterwards -- the rule root extraction
    retires an unobservable store by -- so it leaves with the calls."""
    bodies = {e: _leaf_callee(s) for e, s in procs.items() if e != PLAY}
    bodies = {e: b for e, b in bodies.items() if b is not None}
    if not bodies:
        return procs, ()

    def walk(sl):
        out = []
        for s in sl:
            if s[0] == "call" and s[1] in bodies:
                out.extend(bodies[s[1]])
                continue
            if s[0] == "if":
                s = ("if", s[1], walk(s[2]), walk(s[3]))
            elif s[0] == "loop":
                s = ("loop", walk(s[1]))
            elif s[0] == "switch":
                s = ("switch", [(lbl, walk(b)) for lbl, b in s[1]])
            out.append(s)
        return out

    return {e: walk(s) for e, s in procs.items() if e not in bodies}, tuple(sorted(bodies))


def prove_row(lo, z3):
    """Z3-prove over the array theory that two column reads at one index are one row.

    Storing the lo column's byte at the pair's lo cell and the hi column's at the cell
    above leaves the pair holding ``Concat(hi, lo)``; a destination pair that aliases
    refuses, which is what makes the grouping the site's and not the printer's."""
    byte, word = z3.BitVecSort(8), z3.BitVecSort(16)
    idx = z3.BitVec("i", 16)
    t_lo, t_hi = z3.Array("t_lo", word, byte), z3.Array("t_hi", word, byte)
    mem = z3.Array("m", word, byte)
    a_lo, a_hi = z3.BitVecVal(lo, 16), z3.BitVecVal(lo + 1, 16)
    got = z3.Store(z3.Store(mem, a_lo, z3.Select(t_lo, idx)), a_hi, z3.Select(t_hi, idx))
    s = z3.Solver()
    s.add(
        z3.Concat(z3.Select(got, a_hi), z3.Select(got, a_lo))
        != z3.Concat(z3.Select(t_hi, idx), z3.Select(t_lo, idx))
    )
    return s.check() == z3.unsat


def pair_tables(labels):
    """Declared lo/hi column pairs, by the image's own labels: ``pitchlo`` is ``pitch``."""
    out = {}
    for name, addr in labels.items():
        if name.endswith("lo") and name[:-2] + "hi" in labels:
            out[_addr_name(addr)] = name[:-2]
    return out


def _match_row(stmts, i):
    """``r1 = T_lo[e]; r2 = T_hi[e]; c_lo = r1; c_hi = r2``: the pair the site declares."""
    win = stmts[i : i + 4]
    if len(win) != 4 or any(s[0] != "asg" for s in win):
        return None
    r1, ld1, r2, ld2 = win[0][1], win[0][2], win[1][1], win[1][2]
    if r1 == r2 or _store_addr(r1) is not None or _store_addr(r2) is not None:
        return None
    if ld1[0] != "index" or ld2[0] != "index" or ld1[2] != ld2[2] or ld1[1] == ld2[1]:
        return None
    if cell_addr(ld1[1]) is None or cell_addr(ld2[1]) is None:
        return None
    if r1 in _names(ld2, set()):  # the first lane moved the index the second reads
        return None
    lo, hi = _store_addr(win[2][1]), _store_addr(win[3][1])
    if lo is None or hi is None or hi != lo + 1:
        return None
    if (win[2][2], win[3][2]) != (("name", r1), ("name", r2)):
        return None
    return lo, ld1[1], ld2[1], ld1[2]


def row_reads(stmts, proofs, tables):
    """Fold a declared lane pair fed by two column reads into one u16 row read.

    The grouping is the site's -- the declared pair enumerated where the lanes meet --
    and the spelling is the declared table's own name."""
    import z3  # pylint: disable=import-outside-toplevel

    out, i = [], 0
    while i < len(stmts):
        s = stmts[i]
        if s[0] in ("if", "loop", "switch"):
            if s[0] == "if":
                s = ("if", s[1], row_reads(s[2], proofs, tables), row_reads(s[3], proofs, tables))
            elif s[0] == "loop":
                s = ("loop", row_reads(s[1], proofs, tables))
            else:
                s = ("switch", [(lbl, row_reads(b, proofs, tables)) for lbl, b in s[1]])
            out.append(s)
            i += 1
            continue
        got = _match_row(stmts, i)
        if got is not None and got[1] in tables and prove_row(got[0], z3):
            lo, t_lo, t_hi, idx = got
            proofs.append("row_read(%s,%s)" % (t_lo, t_hi))
            out.append(("rd16", lo, t_lo, t_hi, idx, tables[t_lo]))
            i += 4
            continue
        out.append(s)
        i += 1
    return out


class _Hole:
    """One leaf the voices disagree on: the substitution's ``k``-th binding."""

    __slots__ = ("k",)

    def __init__(self, k):
        self.k = k

    def __repr__(self):
        return "<voice %d>" % self.k

    def __eq__(self, other):
        return isinstance(other, _Hole) and other.k == self.k

    def __hash__(self):
        return hash(("hole", self.k))


def prove_guard_unify(k, z3):
    """Z3-prove the guarded advance is the unguarded one wherever its guard holds.

    Voice 1 folds the page cross and voices 2-3 fold ``nocarry``, purely from where
    each script landed; both spellings denote one wide add, so the difference is a
    guard and never a structure difference."""
    lo, hi = z3.BitVec("lo", 16), z3.BitVec("hi", 16)
    s = z3.Solver()
    s.add(
        z3.ULE(lo, 0xFF),
        z3.ULE(hi, 0xFF),
        z3.ULE(lo + k, 0xFF),
        ((hi << 8) | ((lo + k) & 0xFF)) != ((((hi << 8) | lo) + k) & 0xFFFF),
    )
    return s.check() == z3.unsat


_REFUSE = object()  # a leaf may itself be None, so a refusal needs its own sentinel


class _Unify:
    """Anti-unify two voice slices into one template plus its binding table."""

    def __init__(self, z3, proofs):
        self.z3, self.proofs, self.diffs, self.why = z3, proofs, [], None
        self.seen, self.bound, self.arity = {}, {}, 1

    def refuse(self, why):
        self.why = self.why or why
        return _REFUSE

    def guard(self, a, b):
        """One advance the voices observed different page crossings of."""
        if a[2] != b[2] or not prove_guard_unify(a[2], self.z3):
            return self.refuse("advance %r against %r" % (a, b))
        pair = self.walk(a[1], b[1])
        if pair is _REFUSE:
            return _REFUSE
        self.proofs.append("reroll_guard(%s,+%d)" % (a[1], a[2]))
        return ("adv16", pair, a[2], True)

    def walk(self, a, b):
        if isinstance(a, tuple) and isinstance(b, tuple):
            if a[:1] == ("adv16",) and b[:1] == ("adv16",) and a[3] != b[3]:
                return self.guard(a, b)
            if len(a) != len(b):
                return self.refuse("arity %r against %r" % (a[:1], b[:1]))
            got = [self.walk(x, y) for x, y in zip(a, b)]
            return _REFUSE if _REFUSE in got else tuple(got)
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return self.refuse("block of %d against %d" % (len(a), len(b)))
            got = [self.walk(x, y) for x, y in zip(a, b)]
            return _REFUSE if _REFUSE in got else got
        if isinstance(a, _Hole):
            got = self.bound.setdefault(a.k, b)
            if got != b:
                return self.refuse("binding %d is %r and %r" % (a.k, got, b))
            if len(self.diffs[a.k]) == self.arity:
                self.diffs[a.k] += (b,)
            return a
        if type(a) is not type(b):
            return self.refuse("%r against %r" % (a, b))
        if a == b:
            return a
        if isinstance(a, bool) or not isinstance(a, (int, str)):
            return self.refuse("%r against %r" % (a, b))
        if (a, b) not in self.seen:
            self.seen[(a, b)] = _Hole(len(self.diffs))
            self.diffs.append((a,) * self.arity + (b,))
        return self.seen[(a, b)]

    def run(self, a, b):
        self.seen, self.bound, keep = {}, {}, list(self.diffs)
        got = self.walk(a, b)
        if got is _REFUSE:
            self.diffs[:] = keep  # a refused voice binds nothing
            return None
        lhs, rhs = {x for x, _y in self.seen}, {y for _x, y in self.seen}
        if len(lhs) != len(self.seen) or len(rhs) != len(self.seen):
            self.refuse("the substitution is not a bijection")
            self.diffs[:] = keep
            return None
        self.arity += 1
        return got


def _voice_name(tok, var):
    """``(spelling, is a declared parameter)`` for a leaf the voices bind differently.

    A cell or sink the voice owns spells itself; a per-voice temporary is renamed;
    anything else -- a handler label, a split-table base -- is a declared parameter."""
    if isinstance(tok, int):
        tok = _addr_name(tok)
    if tok.startswith("sid.v1."):
        return "sid.%s.%s" % (var, tok[7:]), False
    got = pretty(tok)
    if got.startswith("v1_"):
        return "%s_%s" % (var, got[3:]), False
    if _store_addr(tok) is None and not tok.startswith("$"):
        return None, False  # a local: alpha-renamed, not a voice parameter
    return None, True


def voice_display(diffs, var):
    """``[(spelling, declared)]`` per binding, in the template's own order."""
    out, locals_, params = [], 0, 0
    for got in diffs:
        name, decl = _voice_name(got[0], var)
        if name is None:
            name = "%s_%s%d" % (var, "p" if decl else "t", params if decl else locals_)
            params, locals_ = params + decl, locals_ + (not decl)
        out.append((name, decl))
    return out


def _shown(tree, names):
    if isinstance(tree, _Hole):
        return names[tree.k][0]
    if isinstance(tree, tuple):
        return tuple(_shown(x, names) for x in tree)
    if isinstance(tree, list):
        return [_shown(x, names) for x in tree]
    return tree


def _voices_of(s):
    return {voice_of(n) for n in stmt_names(s)} - {None}


def _find_first(sl, v):
    """``(descents, index)`` of the first statement in tree order that is voice ``v``'s."""
    for i, s in enumerate(sl):
        if _voices_of(s) == {v}:
            return ([], i)
        for bi, b in enumerate(_bodies(s)):
            got = _find_first(b, v)
            if got is not None:
                return ([(i, bi)] + got[0], got[1])
    return None


def _replace_body(s, bi, body):
    if s[0] == "if":
        return ("if", s[1], body, s[3]) if bi == 0 else ("if", s[1], s[2], body)
    if s[0] == "loop":
        return ("loop", body)
    arms = list(s[1])
    arms[bi] = (arms[bi][0], body)
    return ("switch", arms)


def _cut(sl, path, idx, mark):
    """``(context, tail)``: the sub-list at ``path`` cut at ``idx``, ``mark`` in its place."""
    if not path:
        return list(sl[:idx]) + [mark], list(sl[idx:])
    i, bi = path[0]
    head, tail = _cut(_bodies(sl[i])[bi], path[1:], idx, mark)
    return list(sl[:i]) + [_replace_body(sl[i], bi, head)] + list(sl[i + 1 :]), tail


def _bind(tree, diffs, k):
    if isinstance(tree, _Hole):
        return diffs[tree.k][k]
    if isinstance(tree, tuple):
        return tuple(_bind(x, diffs, k) for x in tree)
    if isinstance(tree, list):
        return [_bind(x, diffs, k) for x in tree]
    return tree


def _plug(sl, var, tail):
    """The loop's back edge: ``next <var>`` continues with the following voice's slice."""
    out = []
    for s in sl:
        if s == ("next", var):
            out.extend(tail)
            continue
        if s[0] == "if":
            s = ("if", s[1], _plug(s[2], var, tail), _plug(s[3], var, tail))
        elif s[0] == "loop":
            s = ("loop", _plug(s[1], var, tail))
        elif s[0] == "switch":
            s = ("switch", [(lbl, _plug(b, var, tail)) for lbl, b in s[1]])
        out.append(s)
    return out


def expand(procs):
    """The re-rolled program's own meaning: the body once per voice, in loop order."""

    def walk(sl):
        out = []
        for s in sl:
            if s[0] == "forvoice":
                _kw, var, voices, template, diffs, tail = s
                acc = walk(tail)
                for k in reversed(range(len(voices))):
                    acc = _plug(_bind(template, diffs, k), var, acc)
                out.extend(acc)
                continue
            if s[0] == "if":
                s = ("if", s[1], walk(s[2]), walk(s[3]))
            elif s[0] == "loop":
                s = ("loop", walk(s[1]))
            elif s[0] == "switch":
                s = ("switch", [(lbl, walk(b)) for lbl, b in s[1]])
            out.append(s)
        return out

    return {e: walk(s) for e, s in procs.items()}


VOICE_VAR = "voice"


def reroll(procs, proofs):
    """Unify the per-voice slices into one loop where the isomorphism is total.

    Every path through a voice's slice ends at the next voice's, so the slice is a
    context whose hole is that entry and a total anti-unification of two adjacent
    contexts is a loop over them. Any residual mismatch refuses; the copies stay."""
    import z3  # pylint: disable=import-outside-toplevel

    play = procs[PLAY]
    at = _find_first(play, 0)
    stats = {"voices": VOICES, "unified": [], "refused": None, "bindings": 0, "params": 0}
    if at is None or at[0]:
        stats["refused"] = "no voice slice at the procedure's own level"
        return procs, stats
    head, rest, ctxs = list(play[: at[1]]), list(play[at[1] :]), []
    for v in range(VOICES):
        nxt = _find_first(rest, v + 1) if v + 1 < VOICES else None
        if nxt is None:
            ctxs.append(rest)
            break
        ctx, rest = _cut(rest, nxt[0], nxt[1], ("next", VOICE_VAR))
        ctxs.append(ctx)
    if len(ctxs) < 2:
        stats["refused"] = "one voice slice"
        return procs, stats
    uni = _Unify(z3, proofs)
    template, n = ctxs[0], 1
    while n < len(ctxs):
        got = uni.run(template, ctxs[n])
        if got is None:
            break
        template, n = got, n + 1
    stats["refused"] = uni.why
    if n < 2:
        return procs, stats
    shown = voice_display(uni.diffs, VOICE_VAR)
    stats.update(
        unified=list(range(1, n + 1)),
        bindings=len(uni.diffs),
        params=sum(1 for _n, decl in shown if decl),
    )
    tail = ctxs[n] if n < len(ctxs) else []
    loop = ("forvoice", VOICE_VAR, tuple(range(1, n + 1)), template, uni.diffs, tail)
    return {**procs, PLAY: head + [loop]}, stats


class Flat:
    """Flatten folded statements to ops with resolved labels for execution."""

    def __init__(self, stmts):
        self.ops, self.labels, self.fix = [], {}, []
        self._walk(stmts, [])
        for at, lbl in self.fix:
            self.ops[at] = self.ops[at][:-1] + (self.labels[lbl],)

    def _jmp(self, lbl):
        self.fix.append((len(self.ops), lbl))
        self.ops.append(("jmp", lbl))

    def _walk(self, stmts, loops):
        k = 0
        while k < len(stmts):
            s = stmts[k]
            op = s[0]
            if op == "label":
                self.labels[s[1]] = len(self.ops)
            elif op in ("asg", "sto", "call", "st16", "set16", "adv16", "w16", "rd16"):
                self.ops.append(s)
            elif op == "if":
                l_else, l_end = "@f%d" % len(self.ops), "@e%d" % len(self.ops)
                self.fix.append((len(self.ops), l_else))
                self.ops.append(("bf", s[1], l_else))
                self._walk(s[2], loops)
                self._jmp(l_end)
                self.labels[l_else] = len(self.ops)
                self._walk(s[3], loops)
                self.labels[l_end] = len(self.ops)
            elif op == "loop":
                start, end = "@l%d" % len(self.ops), "@x%d" % len(self.ops)
                self.labels[start] = len(self.ops)
                self._walk(s[1], loops + [(start, end)])
                self._jmp(start)
                self.labels[end] = len(self.ops)
            elif op == "continue":
                self._jmp(loops[-1][0])
            elif op == "break":
                self._jmp(loops[-1][1])
            elif op == "goto":
                self._jmp(s[1])
            elif op == "dgoto":
                nxt = stmts[k + 1]
                assert nxt[0] == "switch", "computed goto without its dispatch"
                table, l_end = {}, "@s%d" % len(self.ops)
                self.ops.append(("dsw", s[1], table))
                for lbl, body in nxt[1]:
                    table[int(lbl[1:], 16)] = len(self.ops)
                    self._walk(body, loops)
                    self._jmp(l_end)
                self.labels[l_end] = len(self.ops)
                k += 1
            elif op == "unobserved":
                self.ops.append(("fault", s[1]))
            elif op == "ret":
                self.ops.append(("ret",))
            else:
                raise ValueError("flatten: %r" % (s,))
            k += 1


_CALL_REGS = ("a", "x", "y")
_REGS = _CALL_REGS + ("cflag", "zflag", "nflag", "vflag")  # the call ABI's own names


class Machine:
    """Execute the flattened, folded procedures over post-init RAM per frame."""

    def __init__(self, flats, ram0):
        self.flats, self.ram, self.out = flats, bytearray(ram0), []

    def _val(self, e, env):
        k = e[0]
        if k == "num":
            return e[1], 2 if e[1] > 0xFF else 1
        if k == "name":
            addr = cell_addr(e[1])
            if addr is not None:
                return self.ram[addr], 1
            return env[e[1]], 1
        if k == "index":
            if e[1] == "mem":
                a, _ = self._val(e[2], env)
                return self.ram[a & 0xFFFF], 1
            base = cell_addr(e[1])
            i, _ = self._val(e[2], env)
            return self.ram[(base + i) & 0xFFFF], 1
        if k == "call" and e[1] == "zext2":
            return self._val(e[2][0], env)[0], 2
        if k == "call" and e[1] == "carry":
            a, _ = self._val(e[2][0], env)
            b, _ = self._val(e[2][1], env)
            return int(a + b > 0xFF), 1
        if k == "wcmp":
            a, b = self._src(e[2]), self._src(e[3])
            return int({">=": a >= b, "<": a < b, ">": a > b, "<=": a <= b}[e[1]]), 1
        if k == "not":
            return int(self._val(e[1], env)[0] == 0), 1
        if k == "neg":
            v, w = self._val(e[1], env)
            return (-v) & (0xFFFF if w == 2 else 0xFF), w
        a, wa = self._val(e[2], env)
        b, wb = self._val(e[3], env)
        w = max(wa, wb)
        op = e[1]
        if op in ("==", "!=", "<", ">=", "<=", ">", "<s", ">=s", "<=s", ">s"):
            if op.endswith("s"):
                a = a - (1 << (8 * wa)) if a >> (8 * wa - 1) else a
                b = b - (1 << (8 * wb)) if b >> (8 * wb - 1) else b
                op = op[:-1]
            res = {"==": a == b, "!=": a != b, "<": a < b, ">=": a >= b, "<=": a <= b, ">": a > b}
            return int(res[op]), 1
        val = {
            "+": a + b,
            "-": a - b,
            "&": a & b,
            "|": a | b,
            "^": a ^ b,
            "<<": a << b,
            ">>": a >> b,
        }[op]
        if op == "<<":
            return val & 0xFFFF, 2
        return val & (0xFFFF if w == 2 else 0xFF), w

    def _src(self, src):
        if src[0] == "const":
            return src[1]
        return int.from_bytes(bytes(self.ram[src[1] : src[1] + src[2]]), "little")

    def _write(self, addr, val):
        """A byte reaching memory; inside the SID window it is also observable."""
        self.ram[addr & 0xFFFF] = val & 0xFF
        if SID <= addr < SID + 25:
            self.out.append((addr - SID, val & 0xFF))

    def frame(self):
        self.out = []
        self._run(PLAY, {"a": 0, "x": 0, "y": 0})  # CPU entry registers, unread here
        return self.out

    def _run(self, entry, env):
        pc, ops, steps = 0, self.flats[entry].ops, 0
        while pc < len(ops):
            steps += 1
            assert steps < 20000, "runaway frame"
            s = ops[pc]
            op = s[0]
            if op == "asg":
                sid = sid_target(s[1])
                v = self._val(s[2], env)[0]
                if sid is not None:
                    self._write(SID + sid, v)
                else:
                    addr = cell_addr(s[1])
                    if addr is not None:
                        self.ram[addr] = v & 0xFF
                    else:
                        env[s[1]] = v & 0xFF
            elif op == "sto":
                base = 0 if s[1] == "mem" else _store_addr(s[1])
                assert base is not None, "span store through an unnamed row: %r" % (s[1],)
                self._write(base + self._val(s[2], env)[0], self._val(s[3], env)[0])
            elif op == "call":
                sub = {k: env[k] for k in _REGS if k in env}
                self._run(s[1], sub)
                env.update(sub)
            elif op == "st16":
                reg = sid_target(s[1] + "_lo")
                wide = (self.ram[cell_addr(s[3])] << 8) | self.ram[cell_addr(s[2])]
                if s[4] is not None:
                    wide = (wide + self._val(s[4], env)[0]) & 0xFFFF
                self._write(SID + reg, wide & 0xFF)
                self._write(SID + reg + 1, wide >> 8)
            elif op == "w16":
                lo, n, wop, a, b, carry = s[1:]
                va = self._src(a)
                full = va if b is None else va + self._src(b) if wop == "+" else va - self._src(b)
                mask = (1 << (8 * n)) - 1
                self.ram[lo : lo + n] = (full & mask).to_bytes(n, "little")
                if carry is not None:
                    self.ram[carry] = (full >> (8 * n)) & 1
            elif op == "rd16":
                i = self._val(s[4], env)[0]
                self.ram[s[1]] = self.ram[(cell_addr(s[2]) + i) & 0xFFFF]
                self.ram[s[1] + 1] = self.ram[(cell_addr(s[3]) + i) & 0xFFFF]
            elif op == "set16":
                base = cell_addr(s[1] + "_lo")
                lo = self._val(s[2], env)[0]
                hi = self._val(s[3], env)[0]
                self.ram[base + 1], self.ram[base] = hi & 0xFF, lo & 0xFF
            elif op == "adv16":
                base = cell_addr(s[1] + "_lo")
                assert s[3] or (self.ram[base] + s[2]) <= 0xFF, "nocarry guard"
                wide = ((self.ram[base + 1] << 8) | self.ram[base]) + s[2]
                self.ram[base] = wide & 0xFF
                self.ram[base + 1] = (wide >> 8) & 0xFF
            elif op == "bf":
                if self._val(s[1], env)[0] == 0:
                    pc = s[2]
                    continue
            elif op == "jmp":
                pc = s[1]
                continue
            elif op == "dsw":
                v = self._val(s[1], env)[0]
                assert v in s[2], "dispatch outside observed set: $%04X" % v
                pc = s[2][v]
                continue
            elif op == "fault":
                raise AssertionError("reached unobserved %s" % s[1])
            elif op == "ret":
                break
            pc += 1


_FIELD = {
    PTR: "pos", DUR: "dur", PHASE: "phase", NLO: "note", NHI: "note_hi",
    DEPTH: "vib", WAVE: "wave", CTL: "ctl", AD: "ad", SR: "sr",
    ADEF: "ad_set", SRDEF: "sr_set", CLO: "pitch", CHI: "pitch_hi", RATE: "slide",
    DLO: "diff", DHI: "diff_hi", CUT: "cut", CUTH: "cut_hi",
    FCTL: "res_route", FVOL: "mode_vol",
}  # fmt: skip
_GLOBAL = {
    PH0: "phase", PH0 + 1: "phase_1", PH0 + 2: "phase_2", TICK: "tick",
    PWL: "pw", PWH: "pw_hi", LOGI: "log_idx",
    ZX: "row_put", ZY: "row_get", SWP: "row_src", LFO: "row_val",
}  # fmt: skip


def pretty(name):
    """Per-voice readable name for a state cell, whatever alias the emitter chose."""
    m = _PAIRBASE.match(name)
    if m:
        addr, lane = int(m.group(1), 16), ("_" + m.group(2) if m.group(2) else "")
    else:
        addr, lane = cell_addr(name), ""
    if addr is None:
        return name
    for v, b in enumerate(ZPV):
        if 0 <= addr - b < 32 and addr - b in _FIELD:
            return "v%d_%s%s" % (v + 1, _FIELD[addr - b], lane)
    return _GLOBAL[addr] + lane if addr in _GLOBAL else name


def _field_update(name, r):
    """A read-modify-write that preserves the bits it does not write (stage 2's ``flags``)."""
    if r[0] != "bin" or r[1] not in ("&", "|", "^"):
        return False
    ns, a = set(), cell_addr(name)
    _names(r, ns)
    if not any(cell_addr(x) == a for x in ns):
        return False
    return any(k[0] == "num" for k in (r[2], r[3])) or any(
        k[0] == "bin" and k[1] in ("&", "|", "^") for k in (r[2], r[3])
    )


def classify_roles(procs):
    """Read each state cell's role off its folded update shapes (the plan's rule).

    Locals are inlined per straight-line run, so a cell updated through a temporary
    is read at the same shape as one updated in place."""
    shapes, read = {}, set()

    def walk(sl):
        env = {}
        for s in sl:
            if s[0] == "adv16":
                shapes.setdefault(s[1], set()).add("advance")
            elif s[0] == "set16":
                shapes.setdefault(s[1], set()).add("rewrite")
            elif s[0] == "w16":
                got = "inc" if s[4][0] == "self" else "set"
                shapes.setdefault(_addr_name(s[1]), set()).add(got)
                if s[6] is not None:
                    shapes.setdefault(_addr_name(s[6]), set()).add("set")
            elif s[0] == "rd16":
                shapes.setdefault(_addr_name(s[1]), set()).add("set")
            elif s[0] == "asg":
                r = _subst(s[2], env)
                _names(r, read)
                if cell_addr(s[1]) is None:
                    env[s[1]] = r
                else:
                    inc = r[0] == "bin" and r[1] in "+-" and ("name", s[1]) in (r[2], r[3])
                    ctr = r[0] == "bin" and r[1] == "-" and r[2][0] == "name" and r[3] == ("num", 1)
                    got = "dec" if ctr else "inc" if inc else "set"
                    key = _addr_name(cell_addr(s[1]))
                    shapes.setdefault(key, set()).add("field" if _field_update(s[1], r) else got)
            else:
                for part in s[1:]:
                    _names(part, read)
                env = {}
            for b in _bodies(s):
                walk(b)
                env = {}

    for stmts in procs.values():
        walk(stmts)
    roles = {}
    for name, got in shapes.items():
        if "advance" in got or "rewrite" in got:
            roles[name] = "cursor"
        elif "dec" in got:
            roles[name] = "counter"
        elif "inc" in got:
            roles[name] = "accumulator"
        elif "field" in got:
            roles[name] = "flags"
        else:
            roles[name] = "parameter"
    for name in read:  # read but never written: set once by init, then a parameter
        a = cell_addr(name)
        if a is not None:
            roles.setdefault(_addr_name(a), "parameter")
    return roles


_SIDV = re.compile(r"sid\.v([123])\.")
_CELLTOK = re.compile(
    r"\b(?:zp_[0-9A-Fa-f]{2}|(?:m|ctr|idx|pos)_[0-9A-Fa-f]{4}"
    r"|ptr_[0-9A-Fa-f]{4}(?:_(?:lo|hi))?)\b"
)
FILTER_CELLS = frozenset((CUT, CUTH, FCTL, FVOL))
_DIALECT = frozenset(("zext2", "carry", "mem", "u8", "u16", "u24", "u32"))


def voice_of(name):
    """The voice whose own *state* ``name`` is, else None.

    Hardware sinks are deliberately not evidence: the pulse-width pair is voice 1's
    register but the accumulator driving it is one global modulation."""
    a = cell_addr(name)
    a = cell_addr(name + "_lo") if a is None else a
    if a is None:
        return None
    for v, b in enumerate(ZPV):
        if 0 <= a - b < 32:
            return v
    if SHADOW <= a < SHADOW + 7 * VOICES:
        return (a - SHADOW) // 7
    return None


def _norm_line(line, v, ids):
    """Normalize one statement to voice ``v``'s frame: offsets, not addresses."""

    def cell(m):
        name = m.group(0)
        a = cell_addr(name)
        a = cell_addr(name + "_lo") if a is None else a
        if a is None:
            return rank(m)
        if a < ZPV[0]:
            return "g%02X" % a
        if voice_of(name) == v:
            b = ZPV[v] if a - ZPV[v] < 32 else SHADOW + 7 * v
            return ("zp+%d" if b == ZPV[v] else "sh+%d") % (a - b)
        return rank(m)

    def rank(m):
        return ids.setdefault(m.group(0), "#%d" % len(ids))

    def local(m):
        return m.group(0) if m.group(0) in _DIALECT else rank(m)

    line = _CELLTOK.sub(cell, line)
    line = _SIDV.sub("sid.", line)
    line = re.sub(r"\$[0-9A-Fa-f]{4,}", rank, line)
    line = re.sub(r"\b[a-z]+\d+\b", local, line)
    return line.split("   ;")[0].strip()


def _addr_srcs(src):
    return () if src is None or src[0] == "const" else (_addr_name(src[1]),)


def stmt_names(s):
    """Every state name a folded statement mentions, wide operands included."""
    ns = set()
    if s[0] == "asg":
        if cell_addr(s[1]) is not None or sid_target(s[1]) is not None:
            ns.add(s[1])  # a local target is defined here, not read
        _names(s[2], ns)
    elif s[0] == "sto":
        ns.add(s[1])
        _names(s[2], ns)
        _names(s[3], ns)
    elif s[0] == "st16":
        ns |= {s[1], s[2], s[3]}
        _names(s[4], ns)
    elif s[0] == "rd16":
        ns |= {_addr_name(s[1]), _addr_name(s[1] + 1)}
        _names(s[4], ns)
    elif s[0] in ("adv16", "set16"):
        ns.add(s[1] + "_lo")
        for part in s[2:]:
            _names(part, ns)
    elif s[0] == "w16":
        ns |= {_addr_name(a) for a in (s[1],) + ((s[6],) if s[6] is not None else ())}
        ns |= set(_addr_srcs(s[4])) | set(_addr_srcs(s[5]))
    elif s[0] == "if":
        if s[1][0] == "wcmp":
            ns |= set(_addr_srcs(s[1][2])) | set(_addr_srcs(s[1][3]))
        else:
            _names(s[1], ns)
    return ns


def voice_skeleton(stmts, v, part="all"):
    """Voice ``v``'s statements, in order, normalized to its base displacement.

    A statement is the voice's when the voice state it names is exactly that one's;
    locals inherit its voice and taint. Guards differing only by an observed page
    cross normalize equal; ``part`` splits off the filter block."""
    out, own, taint = [], {}, set()
    filt = {ZPV[v] + k for k in FILTER_CELLS}

    def line(s):
        buf = []
        _render([s], buf, 0)
        return buf[0]

    def walk(sl):
        for s in sl:
            ns = stmt_names(s)
            got = {own.get(n, voice_of(n)) for n in ns} - {None}
            hot = bool(ns & taint) or any(
                cell_addr(n) in filt or n.startswith("filter.") for n in ns
            )
            if got == {v}:
                out.append((line(s), hot, s))
            if s[0] == "asg" and cell_addr(s[1]) is None and sid_target(s[1]) is None:
                own[s[1]] = list(got)[0] if len(got) == 1 else None
                (taint.add if hot else taint.discard)(s[1])
            for b in _bodies(s):
                walk(b)

    walk(stmts)
    live, keep = set(), []
    for ln, hot, s in reversed(out):  # a definition no later statement reads is not structure
        if s[0] == "asg" and _store_addr(s[1]) is None and s[1] not in live:
            continue
        live |= stmt_names(s)
        keep.append((ln, hot))
    keep.reverse()
    sel = [ln for ln, h in keep if part == "all" or h == (part == "filter")]
    ids = {}
    return [_norm_line(ln, v, ids) for ln in sel]


def sid_pairs():
    """Every multi-byte SID register, by lo/hi lane naming: the width law's subjects."""
    out = []
    for r in range(25):
        lo, hi = R.sid_name(SID + r), R.sid_name(SID + r + 1)
        if lo and hi and lo.endswith("_lo") and hi == lo[:-3] + "_hi":
            out.append(lo[:-3])
    return tuple(out)


def wide_spans(procs):
    """``addr -> width`` for every quantity the folds recovered as one word."""
    out = {}
    for name, n in wide_cells(procs).items():
        a = cell_addr(name)
        a = cell_addr(name + "_lo") if a is None else a
        out[a] = n
    return out


def lane_updates(procs):
    """Byte-lane writes to a cell some wide quantity spans: the width law's residue."""
    spans, out = wide_spans(procs), []

    def walk(sl):
        for s in sl:
            if s[0] == "asg":
                a = cell_addr(s[1])
                for base, n in spans.items():
                    if a is not None and base <= a < base + n:
                        out.append((pretty(_addr_name(base)), a))
            for b in _bodies(s):
                walk(b)

    for stmts in procs.values():
        walk(stmts)
    return sorted(set(out))


def _op_use(s):
    ns = set()
    if s[0] in ("call", "ret"):  # the ABI passes registers, never the flags
        return set(_CALL_REGS)
    for part in s[1:]:
        _names(part, ns)
    if s[0] == "asg" and _store_addr(s[1]) is not None:
        ns.add(s[1])
    return ns


def dead_local_defs(stmts):
    """Local definitions no path reads, by liveness over the flattened statements."""
    flat = Flat(stmts)
    ops = flat.ops
    succ = []
    for i, s in enumerate(ops):
        if s[0] == "bf":
            succ.append([i + 1, s[2]])
        elif s[0] == "jmp":
            succ.append([s[1]])
        elif s[0] == "dsw":
            succ.append(sorted(s[2].values()))
        elif s[0] in ("ret", "fault"):
            succ.append([])
        else:
            succ.append([i + 1])
    use = [_op_use(s) for s in ops]
    dfn = [s[1] if s[0] == "asg" and _store_addr(s[1]) is None else None for s in ops]
    dfn = [None if s[0] == "call" else d for s, d in zip(ops, dfn)]
    live = [set() for _ in ops]
    changed = True
    while changed:
        changed = False
        for i in range(len(ops) - 1, -1, -1):
            out = set().union(*[live[j] for j in succ[i] if j < len(ops)]) if succ[i] else set()
            got = (out - ({dfn[i]} if dfn[i] else set())) | use[i]
            if got != live[i]:
                live[i], changed = got, True
    dead = []
    for i, s in enumerate(ops):
        if dfn[i] is None:
            continue
        out = set().union(*[live[j] for j in succ[i] if j < len(ops)]) if succ[i] else set()
        if dfn[i] not in out:
            dead.append((dfn[i], s[2]))
    return dead


def _bodies(s):
    if s[0] == "if":
        return [s[2], s[3]]
    if s[0] == "loop":
        return [s[1]]
    if s[0] == "switch":
        return [b for _l, b in s[1]]
    return []


def _fmt(e):
    k = e[0]
    if k == "wcmp":
        return "(%s %s %s)" % (_src_fmt(e[2]), e[1], _src_fmt(e[3]))
    if k == "num":
        if isinstance(e[1], str):
            return e[1]
        return "$%02X" % e[1] if e[1] <= 0xFF else "$%04X" % e[1]
    if k == "name":
        return e[1]
    if k == "index":
        return "%s[%s]" % (e[1], _fmt(e[2]))
    if k == "call":
        return "%s(%s)" % (e[1], ", ".join(_fmt(a) for a in e[2]))
    if k == "not":
        return "!%s" % _fmt(e[1])
    if k == "neg":
        return "-%s" % _fmt(e[1])
    return "(%s %s %s)" % (_fmt(e[2]), e[1], _fmt(e[3]))


_ORDER = {"cursor": 0, "counter": 1, "accumulator": 2, "flags": 3, "parameter": 4}


def wide_cells(procs):
    """``name -> byte width`` for every quantity the folds recovered as one word."""
    out = {}

    def walk(sl):
        for s in sl:
            if s[0] == "w16":
                out[_addr_name(s[1])] = s[2]
            elif s[0] == "rd16":
                out[_addr_name(s[1])] = 2
            elif s[0] in ("adv16", "set16"):
                out[s[1]] = 2
            for b in _bodies(s):
                walk(b)

    for stmts in procs.values():
        walk(stmts)
    return out


def voice_record(procs):
    """The loop's per-voice bindings that no systematic name covers: §4(e)'s record."""
    out = []
    for stmts in procs.values():
        for s in stmts:
            if s[0] != "forvoice":
                continue
            _kw, var, voices, _tpl, diffs, _tail = s
            rows = [
                "  %s = %s" % (n, ", ".join(_bound(v) for v in got))
                for (n, decl), got in zip(voice_display(diffs, var), diffs)
                if decl
            ]
            if rows:
                out.append("voices %s {" % ", ".join("v%d" % v for v in voices))
                out.extend(rows)
                out.append("}")
    return out


def _bound(tok):
    return "$%04X" % tok if isinstance(tok, int) else tok


def declared_roles(procs, roles):
    """The state block's own names: a lane a wide quantity spans is not a field."""
    wide, covered = wide_cells(procs), set()
    for n, m in wide.items():
        a = cell_addr(n) if cell_addr(n) is not None else cell_addr(n + "_lo")
        covered |= set(range(a, a + m))
    return {n: r for n, r in roles.items() if n in wide or cell_addr(n) not in covered}


def render(procs, roles):
    """Print the folded program as the role-typed state machine.

    The field line is the dialect's own (``name: <role> uN``, sidprog.lark
    ``statedef``), so what this prints is what stage 4 emits."""
    flat = expand(procs)
    lines, wide = ["state {"], wide_cells(flat)
    show = declared_roles(flat, roles)
    for n in sorted(show, key=lambda n: (_ORDER[roles[n]], pretty(n))):
        lane = _PAIRBASE.match(n) and not _PAIRRE.match(n)
        w = "u%d" % (8 * wide.get(n, 2 if lane else 1))
        lines.append("  %s: %s %s" % (pretty(n), roles[n], w))
    lines.append("}")
    lines.append("pitch: u16[%d] = %s" % (len(NOTES), " ".join("$%04X" % f for f in NOTES)))
    lines.extend(voice_record(procs))
    for entry, stmts in sorted(procs.items()):
        lines.append("play {" if entry == PLAY else "sub_%04X {" % entry)
        _render(stmts, lines, 1)
        lines.append("}")
    return "\n".join(_rename_line(ln) for ln in lines)


def _rename_line(ln):
    return re.sub(
        r"\b(?:ptr|zp|ctr|idx|pos|m)_[0-9A-Fa-f]+(?:_(?:lo|hi))?\b",
        lambda m: pretty(m.group(0)),
        ln,
    )


def _src_fmt(src):
    if src[0] == "const":
        return "$%0*X" % (2 * max(1, (src[1].bit_length() + 7) // 8), src[1])
    return "%s:u%d" % (_addr_name(src[1]), 8 * src[2])


def _render(sl, lines, d):
    pad = "  " * d
    for s in sl:
        op = s[0]
        if op == "asg":
            lines.append("%s%s = %s" % (pad, s[1], _fmt(s[2])))
        elif op == "sto":
            lines.append("%s%s[%s] = %s" % (pad, s[1], _fmt(s[2]), _fmt(s[3])))
        elif op == "call":
            lines.append("%scall sub_%04X" % (pad, s[1]))
        elif op == "st16":
            rhs = "%s:%s as u16" % (s[3], s[2])
            if s[4] is not None:
                rhs += " + zext2(%s)" % _fmt(s[4])
            lines.append("%s%s:u16 = %s" % (pad, s[1], rhs))
        elif op == "rd16":
            lines.append("%s%s = %s[%s]" % (pad, _addr_name(s[1]), s[5], _fmt(s[4])))
        elif op == "forvoice":
            _kw, var, voices, template, diffs, tail = s
            heads = ", ".join("v%d" % v for v in voices)
            lines.append("%sfor %s in %s {" % (pad, var, heads))
            _render(_shown(template, voice_display(diffs, var)), lines, d + 1)
            lines.append("%s}" % pad)
            _render(tail, lines, d)
        elif op == "next":
            lines.append("%snext %s" % (pad, s[1]))
        elif op == "set16":
            lines.append("%s%s:u16 = %s:%s" % (pad, s[1], _fmt(s[3]), _fmt(s[2])))
        elif op == "adv16":
            guard = "" if s[3] else "   ; guard: no page cross observed"
            lines.append("%s%s:u16 += %d%s" % (pad, s[1], s[2], guard))
        elif op == "w16":
            lo, n, wop, a, b, cy = s[1:]
            tgt = "%s:u%d" % (_addr_name(lo), 8 * n)
            if b is None:
                rhs = "%s = %s" % (tgt, _src_fmt(a))
            elif a[0] == "self":
                rhs = "%s %s= %s" % (tgt, wop, _src_fmt(b))
            else:
                rhs = "%s = %s %s %s" % (tgt, _src_fmt(a), wop, _src_fmt(b))
            lines.append(pad + rhs + ("" if cy is None else "   ; carry -> %s" % _addr_name(cy)))
        elif op == "if":
            lines.append("%sif %s {" % (pad, _fmt(s[1])))
            _render(s[2], lines, d + 1)
            if s[3]:
                lines.append("%s} else {" % pad)
                _render(s[3], lines, d + 1)
            lines.append("%s}" % pad)
        elif op == "loop":
            lines.append("%sloop {" % pad)
            _render(s[1], lines, d + 1)
            lines.append("%s}" % pad)
        elif op == "switch":
            lines.append("%sdispatch {" % pad)
            for lbl, b in s[1]:
                lines.append("%s  op %s: {" % (pad, lbl))
                _render(b, lines, d + 2)
                lines.append("%s  }" % pad)
            lines.append("%s}" % pad)
        elif op == "dgoto":
            continue
        elif op == "label":
            lines.append("%s%s:" % ("  " * max(d - 1, 0), s[1]))
        elif op == "goto":
            lines.append("%sgoto %s" % (pad, s[1]))
        elif op == "unobserved":
            lines.append("%sunobserved %s" % (pad, s[1]))
        else:
            lines.append("%s%s" % (pad, op))


def to_psid(mem, end):
    from pysidtracker import write_psid  # pylint: disable=import-outside-toplevel

    body = bytes((INIT & 0xFF, INIT >> 8)) + bytes(mem[INIT:end])
    return write_psid(load=0, init=INIT, play=PLAY, image=body, kind="PSID")


def image_end(labels):
    return labels["imgend"]


def grids_from_writes(init_writes, per_frame):
    state = [0] * 25
    for r, v in init_writes:
        state[r] = v
    out = []
    for frame in per_frame:
        for r, v in frame:
            state[r] = v
        out.append(list(state))
    return out


def change_stream(init_writes, per_frame, volume=None):
    state, out = [0] * 25, []  # power-on zeros: a write of 0 is not a change
    if volume is not None:  # the PSID environment seeds $D418 before init runs
        state[24] = volume
    for writes in [list(init_writes)] + list(per_frame):
        for r, v in writes:
            if state[r] != v:
                state[r] = v
                out.append((r, v))
    return out


def _docker(args):
    import subprocess  # pylint: disable=import-outside-toplevel

    return subprocess.run(
        ["docker", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )


def _in_container(entrypoint, argv, tune, gets):
    """Run ``entrypoint argv`` on ``tune`` in the sidtrace image; copy ``gets`` out."""
    from pysidtracker.oracle import SIDTRACE_IMAGE  # pylint: disable=import-outside-toplevel

    create = ["create", "-w", "/work", "--entrypoint", entrypoint, SIDTRACE_IMAGE, *argv]
    cid = _docker(create).stdout.decode().strip()
    try:
        _docker(["cp", str(tune), "%s:/work/e2e.sid" % cid])
        _docker(["start", "-a", cid])
        for src, dst in gets:
            _docker(["cp", "%s:/work/%s" % (cid, src), str(dst)])
    finally:
        _docker(["rm", "-f", cid])


def sidtrace_stream(mem, labels):
    import tempfile  # pylint: disable=import-outside-toplevel
    from pathlib import Path  # pylint: disable=import-outside-toplevel
    from pysidtracker.oracle import read_sidtrace  # pylint: disable=import-outside-toplevel

    with tempfile.TemporaryDirectory() as td:
        tune = Path(td) / "e2e.sid"
        tune.write_bytes(to_psid(mem, image_end(labels)))
        out = Path(td) / "t.csv.zst"
        _in_container("sidtrace", ["t.csv.zst", "e2e.sid", "-t17"], tune, [("t.csv.zst", out)])
        rows = read_sidtrace(out)
    return [(row.reg, row.value) for row in rows if row.chip == 0 and 0 <= row.reg < 25]


WAV_SECONDS = 15  # whole seconds: sidplayfp's -t takes integer seconds
FRAME_S = PAL_CYCLES / PAL_CLOCK


def wav_frames(art, seconds=WAV_SECONDS):
    """Frames both renders cover: ``seconds`` of program, capped by what it has."""
    return min(round(seconds / FRAME_S), len(art["min_frames"]) + 1)


def wav_span(art, seconds=WAV_SECONDS):
    """That frame count in seconds, which is what both renders are pinned to."""
    return wav_frames(art, seconds) * FRAME_S


def sidplayfp_wav(mem, labels, dst, seconds=WAV_SECONDS):
    """Render the tune to WAV with the dockerized sidplayfp; return ``dst``."""
    import tempfile  # pylint: disable=import-outside-toplevel
    from pathlib import Path  # pylint: disable=import-outside-toplevel

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tune = Path(td) / "e2e.sid"
        tune.write_bytes(to_psid(mem, image_end(labels)))
        argv = ["-wout.wav", "-t%d" % seconds, "-q", "e2e.sid"]
        _in_container("sidplayfp", argv, tune, [("out.wav", dst)])
    return dst


def minimized_wav(art, dst, seconds=WAV_SECONDS, model="8580"):
    """Render the MINIMIZED program's own write stream on an emulated SID."""
    from pathlib import Path  # pylint: disable=import-outside-toplevel
    from pysidtracker.audio import render_wav  # pylint: disable=import-outside-toplevel

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    stream = [list(art["init_writes"])] + art["min_frames"][: wav_frames(art, seconds) - 1]
    return render_wav(
        stream,
        dst,
        model=model,
        cycles_per_frame=PAL_CYCLES,
        clock_frequency=float(PAL_CLOCK),
    )


def note_starts(grids, voice):
    """Frames on which ``voice``'s gate goes 0 -> 1 (a note attack)."""
    reg, out, prev = 7 * voice + 4, [], 0
    for i, g in enumerate(grids):
        if g[reg] & GATE_BIT and not prev & GATE_BIT:
            out.append(i)
        prev = g[reg]
    return out


def oscillator_reset_frames(grids, voice):
    """Frames on which ``voice`` holds the TEST bit (the oscillator reset)."""
    reg = 7 * voice + 4
    return [i for i, g in enumerate(grids) if g[reg] & TEST_BIT]


def restart_shape(grids, voice):
    """Per note attack, the (ad, sr, ctrl) of the two frames that precede it."""
    b = 7 * voice
    return [
        tuple((grids[i][b + 5], grids[i][b + 6], grids[i][b + 4]) for i in (f - 2, f - 1))
        for f in note_starts(grids, voice)
        if f >= 2
    ]


def adsr_before_gate(per_frame):
    """True if every frame writes each voice's AD/SR strictly before its ctrl."""
    for writes in per_frame:
        for v in range(VOICES):
            b, seen = 7 * v, {}
            for k, (r, _val) in enumerate(writes):
                if r - b in (4, 5, 6):
                    seen.setdefault(r - b, k)
            if 4 in seen and not all(seen.get(r, -1) < seen[4] for r in (5, 6)):
                return False
    return True


def observed_extents(model, frames):
    """Phase 2b (b0)'s observed extents for this model: ``{pointer cell: block bases}``.

    The run resolves every deref concretely, so where each web's derefs landed is an
    observation; stage 3d's read closure bounds a deref with it, and never with more."""
    from deity_informant import frameprog, frameval, ptrextent  # pylint: disable=C0415

    prog = frameprog.program(model)
    trace, _walker = frameprog.iota(model, frames)
    probe = ptrextent.Probe()
    ev = frameval.Evaluator(prog, trace, probe=probe)
    for f in range(frames):
        ev.frame = f
        ev.run_frame()
    return ptrextent.mapped_blocks(ptrextent.extents(prog, probe.hits))


def boundary(model):
    """``entry -> (params, returns)``: the decompiled model's own pass-2 summary."""
    from deity_informant import datadecl, frameproc, sidprog  # pylint: disable=C0415

    decls = getattr(model, "data_decls", None)
    aliases = getattr(model, "symbols", None)
    if decls is None:
        decls, aliases = datadecl.declarations(model)
    trees, labels, view = sidprog._model_trees(model)  # pylint: disable=protected-access
    procs = frameproc.procedures(
        trees, labels, view, set(model.dispatch_sets), dict(aliases or {}), model.play
    )
    return {e: (tuple(params), tuple(rets)) for e, params, rets, _s in procs}


def pipeline(frames=FRAMES):
    """Build, verify, decompile, minimize, fold, execute; return the artifacts."""
    mem, labels = build_image()
    init_writes, ram0, orig_frames, orig_grids, ram_end = run_vm(mem, frames)
    model, ev = S.decompile(bytearray(mem), INIT, PLAY, frames)
    assert S.Walker(model).run(frames) == ev.wlog, "walker replay is not bit-exact"
    text, _ = eqlift_mem.emit(model, extents=observed_extents(model, frames))
    proofs, folded = [], {}
    for entry in proc_entries(text):
        ast = extract_proc(text, entry)
        folded[entry] = fold(drop_dead_shadow(ast), proofs)
    folded, resolved = resolve_calls(folded)
    tables = pair_tables(labels)
    folded = {e: drop_dead_locals(row_reads(s, proofs, tables)) for e, s in folded.items()}
    rolled, unify = reroll(folded, proofs)
    machine = Machine({e: Flat(s) for e, s in expand(rolled).items()}, ram0)
    min_frames = [machine.frame() for _ in range(frames)]
    return {
        "mem": mem,
        "labels": labels,
        "init_writes": init_writes,
        "orig_frames": orig_frames,
        "orig_grids": orig_grids,
        "ram_end": ram_end,
        "eqlift_text": text,
        "folded": folded,
        "rolled": rolled,
        "resolved": resolved,
        "unify": unify,
        "boundary": boundary(model),
        "proofs": proofs,
        "min_frames": min_frames,
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    art = pipeline()
    if "--dump" in argv:
        print(art["eqlift_text"])
        return 0
    kinds = {p.split("(")[0] for p in art["proofs"]}
    assert kinds == FOLDS, kinds
    assert framelog.canonical(art["min_frames"]) == framelog.canonical(
        art["orig_frames"]
    ), "minimized program diverges from the VM frame projection"
    roles = classify_roles(art["folded"])
    text = render(art["rolled"], roles)
    print(text)
    print()
    assert not re.search(r"m_03[0-9A-Fa-f]{2}", text), "shadow survives on the SID path"
    print("folds proved by Z3: %s" % ", ".join(sorted(kinds)))
    got = art["unify"]
    print(
        "re-rolling: %d of %d voices unified over %d bindings; refused: %s"
        % (len(got["unified"]), got["voices"], got["params"], got["refused"] or "nothing")
    )
    print("frame projection: minimized == VM over %d frames" % FRAMES)

    min_grids = grids_from_writes(art["init_writes"], art["min_frames"])
    assert min_grids == art["orig_grids"], "write-application grid diverges from VM grid"
    assert adsr_before_gate(art["min_frames"]), "minimized frame gates before writing the ADSR"
    for v in range(VOICES):
        shapes = restart_shape(art["orig_grids"], v)
        want = ((0, 0, WAVEF[v]), (0, 0, WAVEF[v] | TEST_BIT))
        assert shapes and all(s == want for s in shapes), "voice %d hard restart shape" % v
    print("hard restart: %d voices, ADSR-zero then TEST, ADSR written before the gate" % VOICES)

    try:
        from pysidtracker.oracle import register_grid  # pylint: disable=import-outside-toplevel

        psid = to_psid(art["mem"], image_end(art["labels"]))
        oracle = [g[:25] for g in register_grid(psid, FRAMES)]
        assert oracle == art["orig_grids"], "pysidtracker oracle grid diverges"
        print("oracle grid: pysidtracker == VM == minimized over %d frames" % FRAMES)
    except ImportError:
        print("oracle grid: pysidtracker not installed, skipped")

    if "--sidtrace" in argv:
        stream = sidtrace_stream(art["mem"], art["labels"])
        if stream and stream[0] == (24, 0x0F):
            stream = stream[1:]
        mine = change_stream(art["init_writes"], art["min_frames"], volume=0x0F)
        n = min(len(stream), len(mine))
        assert n and mine[:n] == stream[:n], "sidtrace oracle diverges"
        print("sidplayfp/sidtrace oracle: %d register changes match (minimized side)" % n)
    if "--wav" in argv:
        print("wav (minimized program, reSID): %s" % minimized_wav(art, "out/minimized.wav"))
        print("wav (sidplayfp): %s" % sidplayfp_wav(art["mem"], art["labels"], "out/tune.wav"))
        print("both renders span %d frames = %.3fs" % (wav_frames(art), wav_span(art)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
