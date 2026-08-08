"""The register-model shredder (docs/register-model-lift-impl.md).

Each fixture forces one 6502 register-model artifact the plan promises to lift
and stays ``xfail(strict=True)`` until its phase lands: the fixture must build
and gate today, and its canonicality assert must not pass yet."""

import re
from functools import lru_cache

import pytest

import _fuzzgen as G
from test_frameprog import _fuzz_model
from deity_informant import frameproc, frameprog, frameval

SID = G.SID
TMP = G.CNT + 0x20  # a RAM cell used only as per-frame scratch
CTR = G.CNT + 0x22  # a 16-bit per-tune counter pair
TGT = G.CNT + 0x24  # a 16-bit threshold pair
POS = G.CNT + 0x26  # a persistent position cell (the pos_54EC shape)
MODE = G.CNT + 0x28  # a per-tune phase toggle
SAV = G.CNT + 0x2A  # cursor save cell pair (the Follin loop-cell shape)
FTC = G.PTR + 2  # a second zero-page pair: the fetch cursor half stores arrive through
PAT = G.TBL + 0x100  # sequence data block the zero-page pointer walks
PAT2 = G.TBL + 0x160  # second sequence block (cursor save/restore target)
BLK = G.TBL + 0x180  # RAM block a pointer stores through
SPSUB = 0x1300  # a subroutine two call depths reach, so its sp never concretizes
SPMID = 0x1340  # the second depth

XFAIL = dict(strict=True)


@lru_cache(maxsize=None)
def _lift(name):
    got = _FIXTURES[name]()
    a, data, frames = got[:3]
    extra = got[3] if len(got) > 3 else {}
    outs = set(extra.get("outs", {SID + 4}))
    player = G.Player(
        name,
        G.ORG,
        a.assemble(),
        outs,
        {"regmodel"},
        data=data,
        frames=frames,
        init=extra.get("init"),
        init_org=extra.get("init_org"),
    )
    model = _fuzz_model(player)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert frameval.gate_fp(model, frames, prog) is None, "the frame oracle must hold"
    assert frameprog.dumps(frameprog.loads(text)) == text
    _lift_prog[name] = prog
    return text


_lift_prog = {}


def _state_block(text):
    i = text.index("state {")
    return text[i : text.index("}", i)]


def _unnamed_store_bounds(prog):
    """G1-resolved reach bound of every store whose address names no datum."""
    out = []

    def walk(stmts, outer, cyclic):
        env = frameproc.Defs(stmts, outer, cyclic)
        for k, s in enumerate(stmts):
            for body in frameproc._stmt_bodies(s):
                walk(body, (env, k), s[0] in frameproc._CYCLIC)
            if s[0] == "st" and frameproc.addr_split(s[1])[0] is None:
                if s[1] not in prog.resolved:
                    out.append(frameproc.addr_bits(s[1], frameproc.DefsAt(env, k)))

    for _e, _p, _r, stmts in prog.procs:
        walk(stmts, None, False)
    return out


def _scratch():
    """Phase 3: a cell written before read every frame is a local, not state."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x11).i("STA", "abs", CTR)
    a.i("AND", "imm", 0x0F).i("STA", "abs", TMP)
    a.i("LDA", "abs", TMP).i("ORA", "imm", 0x40).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, {CTR: 0}, 8


def _pointer_walk():
    """Phase 2: a reloaded-and-advancing pointer is a cursor into its blocks."""
    a = G.Asm(G.ORG)
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x03).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("LDY", "imm", 0x01).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("LDA", "zp", G.PTR).i("AND", "imm", 0x18).i("CMP", "imm", 0x18)
    a.i("BNE", "rel", ("L", "out"))
    a.i("LDX", "abs", CTR).i("LDA", "absx", G.TBL).i("STA", "zp", G.PTR)
    a.i("LDA", "absx", G.TBL + 2).i("STA", "zp", G.PTR + 1)
    a.label("out").i("RTS")
    data = {G.PTR: PAT & 0xFF, G.PTR + 1: PAT >> 8, CTR: 0}
    data.update(
        {G.TBL: PAT & 0xFF, G.TBL + 1: PAT & 0xFF, G.TBL + 2: PAT >> 8, G.TBL + 3: PAT >> 8}
    )
    data.update({PAT + k: (0x40 | (k & 0x1F)) for k in range(0x40)})
    return a, data, 8


def _borrow_chain():
    """Phase 4: a 16-bit compare split into SBC lanes is one wide compare."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x37).i("STA", "abs", CTR)
    a.i("LDA", "abs", CTR + 1).i("ADC", "imm", 0x00).i("STA", "abs", CTR + 1)
    a.i("SEC")
    a.i("LDA", "abs", CTR).i("SBC", "abs", TGT)
    a.i("LDA", "abs", CTR + 1).i("SBC", "abs", TGT + 1)
    a.i("BCC", "rel", ("L", "under"))
    a.i("LDA", "imm", 0x00).i("STA", "abs", CTR).i("STA", "abs", CTR + 1)
    a.label("under")
    a.i("LDA", "abs", CTR).i("AND", "imm", 0x0F).i("ORA", "imm", 0x20)
    a.i("STA", "abs", SID + 4).i("RTS")
    return a, {CTR: 0, CTR + 1: 0, TGT: 0x40, TGT + 1: 0x01}, 8


def _lone_lane():
    """Phase 5: widening a lone lane half may not read the write-only register."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x05).i("STA", "abs", CTR)
    a.i("STA", "abs", SID + 1)
    a.i("LDA", "imm", 0x21).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, {CTR: 0}, 8


def _sweep_blit():
    """Invariant (7.10.11): a covering blit over the register file is no lane half."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 0x18).label("lp")
    a.i("LDA", "absx", G.TBL).i("STA", "absx", SID)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {G.TBL + k: 0x10 + k for k in range(0x19)}
    return a, data, 4, {"outs": {SID + k for k in range(0x19)}}


def _hi_first_pair():
    """Invariant (7.10.10): a hi-then-lo pair merges carrying its own write order."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x03).i("STA", "abs", CTR)
    a.i("STA", "abs", SID + 1).i("STA", "abs", SID)
    a.i("LDA", "imm", 0x41).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, {CTR: 0}, 8, {"outs": {SID, SID + 1, SID + 4}}


def _path_persist():
    """Invariant (7.10.13): a cell rewritten on most paths but not all stays state."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("AND", "imm", 0x03)
    a.i("STA", "abs", CTR).i("BEQ", "rel", ("L", "keep"))
    a.i("ORA", "imm", 0x10).i("STA", "abs", POS)
    a.label("keep")
    a.i("LDA", "abs", POS).i("AND", "imm", 0x0F).i("ORA", "imm", 0x20).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, {CTR: 0, POS: 0}, 8


def _alias_state():
    """Invariant (R1): a cell a write-through pointer store may clobber stays memory."""
    a = G.Asm(G.ORG)
    a.i("LDA", "imm", 0x33).i("STA", "abs", POS)
    a.i("LDX", "abs", CTR)
    a.i("LDA", "absx", G.TBL).i("STA", "zp", G.PTR)
    a.i("LDA", "absx", G.TBL + 2).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "abs", CTR).i("ORA", "imm", 0x40)
    a.i("LDY", "imm", 0x00).i("STA", "indy", G.PTR)
    a.i("LDA", "abs", POS).i("AND", "imm", 0x0F).i("ORA", "imm", 0x20).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", CTR).i("EOR", "imm", 0x01).i("STA", "abs", CTR)
    a.i("RTS")
    data = {CTR: 0, POS: 0, G.PTR: POS & 0xFF, G.PTR + 1: POS >> 8}
    data.update({G.TBL: POS & 0xFF, G.TBL + 1: (POS + 1) & 0xFF})
    data.update({G.TBL + 2: POS >> 8, G.TBL + 3: (POS + 1) >> 8})
    return a, data, 8


def _init_livein():
    """Invariant (Phase 3 init coupling): frame 0 reads what init wrote, so state."""
    ini = G.Asm(0x0F00)
    ini.i("LDA", "imm", 0x21).i("STA", "abs", POS).i("RTS")
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", POS).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", POS).i("EOR", "imm", 0x01).i("STA", "abs", POS)
    a.i("RTS")
    return a, {POS: 0}, 6, {"init": ini.assemble(), "init_org": 0x0F00}


def _mux_pair():
    """Phase 2: one zp pair, pointer role on one path, counter role on the other."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", MODE).i("AND", "imm", 0x01).i("BNE", "rel", ("L", "ctr"))
    a.i("LDX", "abs", CTR)
    a.i("LDA", "absx", G.TBL).i("STA", "zp", G.PTR)
    a.i("LDA", "absx", G.TBL + 2).i("STA", "zp", G.PTR + 1)
    a.i("LDY", "imm", 0x01).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("INX").i("TXA").i("AND", "imm", 0x01).i("STA", "abs", CTR)
    a.i("JMP", "abs", ("L", "out"))
    a.label("ctr")
    a.i("INC", "zp", G.PTR).i("INC", "zp", G.PTR + 1)
    a.i("LDA", "zp", G.PTR).i("AND", "imm", 0x07).i("ORA", "imm", 0x30).i("STA", "abs", SID + 4)
    a.label("out")
    a.i("LDA", "abs", MODE).i("EOR", "imm", 0x01).i("STA", "abs", MODE)
    a.i("RTS")
    data = {MODE: 0, CTR: 0, G.PTR: PAT & 0xFF, G.PTR + 1: PAT >> 8}
    data.update({G.TBL: PAT & 0xFF, G.TBL + 1: (PAT + 8) & 0xFF})
    data.update({G.TBL + 2: PAT >> 8, G.TBL + 3: (PAT + 8) >> 8})
    data.update({PAT + k: 0x40 | (k & 0x1F) for k in range(0x40)})
    return a, data, 8


def _cursor_save():
    """Phase 2: the pointer is copied to save cells and restored, Follin-style."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", MODE).i("AND", "imm", 0x01).i("BNE", "rel", ("L", "res"))
    a.i("LDA", "zp", G.PTR).i("STA", "abs", SAV)
    a.i("LDA", "zp", G.PTR + 1).i("STA", "abs", SAV + 1)
    a.i("LDA", "imm", PAT2 & 0xFF).i("STA", "zp", G.PTR)
    a.i("LDA", "imm", PAT2 >> 8).i("STA", "zp", G.PTR + 1)
    a.i("JMP", "abs", ("L", "de"))
    a.label("res")
    a.i("LDA", "abs", SAV).i("STA", "zp", G.PTR)
    a.i("LDA", "abs", SAV + 1).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.label("de")
    a.i("LDY", "imm", 0x00).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", MODE).i("EOR", "imm", 0x01).i("STA", "abs", MODE)
    a.i("RTS")
    data = {MODE: 0, SAV: 0, SAV + 1: 0, G.PTR: PAT & 0xFF, G.PTR + 1: PAT >> 8}
    data.update({PAT + k: 0x40 | (k & 0x1F) for k in range(0x20)})
    data.update({PAT2 + k: 0x60 | (k & 0x1F) for k in range(0x20)})
    return a, data, 8


def _writethrough():
    """Phase 2: a store through a reloaded sequence pointer (the 12-tune class)."""
    a = G.Asm(G.ORG)
    a.i("LDX", "abs", CTR)
    a.i("LDA", "absx", G.TBL).i("STA", "zp", G.PTR)
    a.i("LDA", "absx", G.TBL + 2).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "abs", CTR).i("ORA", "imm", 0x41)
    a.i("LDY", "imm", 0x02).i("STA", "indy", G.PTR)
    a.i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", CTR).i("EOR", "imm", 0x01).i("STA", "abs", CTR)
    a.i("RTS")
    data = {CTR: 0, G.PTR: BLK & 0xFF, G.PTR + 1: BLK >> 8}
    data.update({G.TBL: BLK & 0xFF, G.TBL + 1: (BLK + 8) & 0xFF})
    data.update({G.TBL + 2: BLK >> 8, G.TBL + 3: (BLK + 8) >> 8})
    data.update({BLK + k: 0 for k in range(0x10)})
    return a, data, 8


def _cursor():
    """A cursor fixture's opening: deref the pair to the SID, so the web is genuine.

    Rung (d2) lifts one site per lane pair per statement list, so the word reaches
    exactly one of a dual-destination advance's two stores; the loser keeps two byte
    stores whose lane values are then named byte-wise. Which one loses is immaterial."""
    a = G.Asm(G.ORG)
    a.i("LDY", "imm", 0x00).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    return a


def _cursor_data(*cells):
    """The seed a cursor fixture walks: the pair aimed at ``PAT``, ``cells`` at zero."""
    data = {G.PTR: PAT & 0xFF, G.PTR + 1: PAT >> 8}
    data.update({PAT + k: 0x40 | (k & 0x1F) for k in range(0x40)})
    data.update({c: 0 for c in cells})
    return data


def _plain_advance():
    """Phase 2 (control): a bare in-place advance, no second destination at all."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("RTS")
    return a, _cursor_data(), 8


def _dual_store_advance():
    """Phase 2: the Ghouls advance - per lane a save copy, then the pair in place."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02)
    a.i("STA", "abs", SAV).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00)
    a.i("STA", "abs", SAV + 1).i("STA", "zp", G.PTR + 1)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _dual_store_pair_first():
    """Phase 2: the same advance with the pair store leading its lane's save copy."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02)
    a.i("STA", "zp", G.PTR).i("STA", "abs", SAV)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00)
    a.i("STA", "zp", G.PTR + 1).i("STA", "abs", SAV + 1)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _dual_store_via_regs():
    """Phase 2: the save copies deferred through X/Y, so the pair stores stay adjacent."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("TAX").i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("TAY").i("STA", "zp", G.PTR + 1)
    a.i("STX", "abs", SAV).i("STY", "abs", SAV + 1)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _dual_store_hi_first():
    """Phase 2: the dual-destination advance with the hi lane stored before the lo."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("TAX")
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00)
    a.i("STA", "abs", SAV + 1).i("STA", "zp", G.PTR + 1)
    a.i("TXA").i("STA", "abs", SAV).i("STA", "zp", G.PTR)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _dual_store_computed():
    """Phase 2: the dual-destination advance stepping by a cell, not an immediate."""
    a = _cursor()
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("AND", "imm", 0x03)
    a.i("STA", "abs", CTR)
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "abs", CTR)
    a.i("STA", "abs", SAV).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00)
    a.i("STA", "abs", SAV + 1).i("STA", "zp", G.PTR + 1)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1, CTR), 8


def _dual_store_lo_only():
    """Phase 2: the save copy taken off the lo lane only, so one lane stays paired."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02)
    a.i("STA", "abs", SAV).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("RTS")
    return a, _cursor_data(SAV), 8


def _dual_store_word_copy():
    """Phase 2: a second destination fed by a plain advance, copied lane by lane."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "zp", G.PTR).i("STA", "abs", SAV)
    a.i("LDA", "zp", G.PTR + 1).i("STA", "abs", SAV + 1)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _stack_spill_cursor():
    """Phase 2 (A): the cursor pushed a lane at a time, so no word form exists at all.

    The 6502 has no 16-bit push and the stack descends, so the hi half lands at the
    lower address: nothing packs. The hi-first restore pair still merges."""
    a = G.Asm(G.ORG)
    a.i("LDA", "zp", G.PTR).i("PHA").i("LDA", "zp", G.PTR + 1).i("PHA")
    a.i("LDX", "abs", CTR)
    a.i("LDA", "absx", G.TBL).i("STA", "zp", G.PTR)
    a.i("LDA", "absx", G.TBL + 2).i("STA", "zp", G.PTR + 1)
    a.i("LDY", "imm", 0x01).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("PLA").i("STA", "zp", G.PTR + 1).i("PLA").i("STA", "zp", G.PTR)
    a.i("LDA", "abs", CTR).i("EOR", "imm", 0x01).i("STA", "abs", CTR)
    a.i("RTS")
    data = _cursor_data(CTR)
    data.update({G.TBL: PAT & 0xFF, G.TBL + 1: (PAT + 8) & 0xFF})
    data.update({G.TBL + 2: PAT >> 8, G.TBL + 3: (PAT + 8) >> 8})
    return a, data, 8


def _deferred_carry_cursor():
    """Phase 2 (B2): an INC/BNE/INC advance whose carry arm the run never enters."""
    a = G.Asm(G.ORG)
    a.i("INC", "zp", G.PTR).i("BNE", "rel", ("L", "skip"))
    a.i("INC", "zp", G.PTR + 1)
    a.label("skip")
    a.i("LDY", "imm", 0x00).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, _cursor_data(), 8


def _table_spill_cursor():
    """Phase 2 (C): the cursor reloaded from and saved back to a split lo/hi table."""
    a = G.Asm(G.ORG)
    a.i("LDX", "abs", CTR)
    a.i("LDA", "absx", G.TBL).i("STA", "zp", G.PTR)
    a.i("LDA", "absx", G.TBL + 2).i("STA", "zp", G.PTR + 1)
    a.i("LDY", "imm", 0x00).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "zp", G.PTR).i("STA", "absx", G.TBL)
    a.i("LDA", "zp", G.PTR + 1).i("STA", "absx", G.TBL + 2)
    a.i("LDA", "abs", CTR).i("EOR", "imm", 0x01).i("STA", "abs", CTR)
    a.i("RTS")
    data = _cursor_data(CTR)
    data.update({G.TBL: PAT & 0xFF, G.TBL + 1: (PAT + 0x10) & 0xFF})
    data.update({G.TBL + 2: PAT >> 8, G.TBL + 3: (PAT + 0x10) >> 8})
    return a, data, 8


def _inpage_advance():
    """Phase 2 (E): a bare INC advance with no carry arm - fusing it would be wrong."""
    a = _cursor()
    a.i("INC", "zp", G.PTR).i("INC", "zp", G.PTR).i("INC", "zp", G.PTR)
    a.i("LDA", "abs", MODE).i("AND", "imm", 0x01).i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", PAT >> 8).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "imm", PAT & 0xFF).i("STA", "zp", G.PTR)
    a.label("out")
    a.i("LDA", "abs", MODE).i("EOR", "imm", 0x01).i("STA", "abs", MODE)
    a.i("RTS")
    return a, _cursor_data(MODE), 8


def _unpaired_half_store():
    """Phase 2 (Commando): both halves fetched through another cursor, never read back."""
    a = G.Asm(G.ORG)
    a.i("LDY", "imm", 0x00).i("LDA", "indy", FTC).i("STA", "zp", G.PTR)
    a.i("INY").i("LDA", "indy", FTC).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "zp", FTC).i("CLC").i("ADC", "imm", 0x02).i("STA", "zp", FTC)
    a.i("LDA", "zp", FTC + 1).i("ADC", "imm", 0x00).i("STA", "zp", FTC + 1)
    a.i("LDY", "imm", 0x00).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("RTS")
    data = _cursor_data()
    data.update({FTC: PAT2 & 0xFF, FTC + 1: PAT2 >> 8})
    data.update({PAT2 + 2 * k: (PAT + 2 * k) & 0xFF for k in range(8)})
    data.update({PAT2 + 2 * k + 1: (PAT + 2 * k) >> 8 for k in range(8)})
    return a, data, 8


def _sp_body(sub):
    """A player calling ``sub`` at two stack depths, so its spill stays sp-relative.

    ``structured.sp_flow`` joins the two depths to bot, so ``concretize_stack``
    folds no cell and the push keeps the machine spelling rung (d0s) reads."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("JSR", "abs", SPSUB).i("JSR", "abs", SPMID).i("JSR", "abs", SPMID)
    a.i("ORA", "imm", 0x21).i("STA", "abs", SID + 4).i("RTS")
    mid = G.Asm(SPMID).i("JSR", "abs", SPSUB).i("RTS")
    data = {CTR: 0}
    data.update({SPSUB + k: b for k, b in enumerate(sub.assemble())})
    data.update({SPMID + k: b for k, b in enumerate(mid.assemble())})
    return a, data, 8


def _sp_spill():
    """Phase 1: a balanced sp-relative spill is a local, and sp leaves entirely."""
    sub = G.Asm(SPSUB)
    sub.i("PHA").i("LDA", "abs", CTR).i("AND", "imm", 0x0F).i("STA", "abs", TMP)
    sub.i("PLA").i("EOR", "imm", 0x02).i("RTS")
    return _sp_body(sub)


def _sp_unbalanced():
    """Invariant (Phase 1): a procedure whose stack effect is unproven keeps sp."""
    sub = G.Asm(SPSUB)
    sub.i("PHA").i("LDX", "imm", 0x03)
    sub.label("lp").i("DEX").i("BNE", "rel", ("L", "lp"))
    sub.i("PLA").i("EOR", "imm", 0x02).i("RTS")
    return _sp_body(sub)


def _g2_store():
    """G2 (7.10.3): a (zext2(y) + $NN) store is bounded under $01FF, not top."""
    a = G.Asm(G.ORG)
    a.i("LDY", "abs", CTR)
    a.i("LDA", "abs", CTR).i("ORA", "imm", 0x40).i("STA", "absy", 0x00A5)
    a.i("LDA", "zp", 0xA8).i("AND", "imm", 0x0F).i("ORA", "imm", 0x20).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("AND", "imm", 0x07).i("STA", "abs", CTR)
    a.i("RTS")
    data = {CTR: 0}
    data.update({0x00A5 + k: 0 for k in range(8)})
    return a, data, 8


_FIXTURES = {
    "scratch": _scratch,
    "pointer_walk": _pointer_walk,
    "borrow_chain": _borrow_chain,
    "lone_lane": _lone_lane,
    "sweep_blit": _sweep_blit,
    "hi_first_pair": _hi_first_pair,
    "path_persist": _path_persist,
    "alias_state": _alias_state,
    "init_livein": _init_livein,
    "mux_pair": _mux_pair,
    "cursor_save": _cursor_save,
    "writethrough": _writethrough,
    "plain_advance": _plain_advance,
    "dual_store_advance": _dual_store_advance,
    "dual_store_pair_first": _dual_store_pair_first,
    "dual_store_via_regs": _dual_store_via_regs,
    "dual_store_hi_first": _dual_store_hi_first,
    "dual_store_computed": _dual_store_computed,
    "dual_store_lo_only": _dual_store_lo_only,
    "dual_store_word_copy": _dual_store_word_copy,
    "stack_spill_cursor": _stack_spill_cursor,
    "deferred_carry_cursor": _deferred_carry_cursor,
    "table_spill_cursor": _table_spill_cursor,
    "inpage_advance": _inpage_advance,
    "unpaired_half_store": _unpaired_half_store,
    "g2_store": _g2_store,
    "sp_spill": _sp_spill,
    "sp_unbalanced": _sp_unbalanced,
}


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_fixture_builds_and_gates(name):
    """Not xfail: the fixtures themselves must stay valid while the lift lands."""
    assert _lift(name).startswith("frameprog 0")


@pytest.mark.xfail(reason="register-model-lift Phase 3: scratch promotion", **XFAIL)
def test_scratch_cell_is_a_local_not_state():
    text = _lift("scratch")
    assert not re.search(r"\bm_%04X\b" % TMP, text), "scratch cell survives as a named cell"


@pytest.mark.xfail(reason="register-model-lift Phase 2: cursor lift", **XFAIL)
def test_pointer_walk_names_no_raw_address():
    text = _lift("pointer_walk")
    body = text[text.index("sub_") :]
    assert "mem[" not in body, "the walk still reads through a raw address"
    assert "carry(" not in body, "the pointer advance still carries between lanes"


@pytest.mark.xfail(reason="register-model-lift Phase 4: wide compare", **XFAIL)
def test_borrow_chain_is_one_wide_compare():
    text = _lift("borrow_chain")
    assert "carry(" not in text, "the borrow chain survives as byte-lane carries"
    assert not re.search(r"\$01 - \(zext2", text), "a borrow survives as compare arithmetic"


@pytest.mark.xfail(reason="register-model-lift Phase 5: boundary shadow", **XFAIL)
def test_lone_lane_half_owes_no_register_load():
    text = _lift("lone_lane")
    assert not re.search(r"= \(+sid\.", text), "a write-only SID register is read back"


def _body(text):
    return text[text.index("play $") :]


def test_sp_relative_spill_leaves_no_stack_pointer():
    """Phase 1 (LANDED): the spill is a local and no procedure threads sp."""
    assert not re.search(r"\bsp\b", _body(_lift("sp_spill"))), "the stack pointer survived"


def test_an_unbalanced_procedure_keeps_its_stack_pointer():
    """Invariant: unproven stack effect means the spill stays memory, sp and all.

    The ret of such a procedure reads page one for its target, so a promoted
    slot would delete the byte the machine returns through."""
    text = _lift("sp_unbalanced")
    assert re.search(r"\bsp\b", _body(text)), "an unbalanced procedure lost sp"
    assert "$0100" in text, "the spill lost its stack-page identity"


def test_covering_sweep_stays_byte_wide():
    """Invariant: the $CA6E argument (7.10.11) - a covering blit is never widened."""
    text = _lift("sweep_blit")
    body = text[text.index("sub_") :]
    assert "sid.reg[" in body, "the blit must keep the byte view of the register file"
    assert not re.search(r"sid\.reg\[[^\]]*\]:2", body), "a swept byte store was widened"


def test_hi_first_pair_keeps_its_order():
    """Invariant: the merged pair states hi-first (7.10.10); no phase may drop it."""
    assert "hi-first" in _lift("hi_first_pair"), "the store no longer states its write order"


def test_scratch_fixture_keeps_the_true_counter():
    """Invariant: the read-before-write counter next to the scratch cell stays state."""
    assert re.search(r"_%04X\b" % CTR, _state_block(_lift("scratch")))


def test_path_dependent_persistent_cell_stays_state():
    """Invariant: rewritten on most paths, surviving on one - the pos_54EC shape."""
    assert re.search(r"_%04X\b" % POS, _state_block(_lift("path_persist")))


def test_pointer_aliased_cell_is_not_promoted():
    """Invariant: a write-through pointer may clobber it between write and read.

    Text-wide on purpose: state field or mut data block both keep the memory
    identity; only promotion to a local (the name vanishing) is the defect."""
    text = _lift("alias_state")
    assert re.search(r"_%04X\b" % POS, text), "the aliased cell lost its memory identity"


def test_init_written_livein_cell_stays_state():
    """Invariant: frame 0 reads the init-written value, so the cell is live-in."""
    assert re.search(r"_%04X\b" % POS, _state_block(_lift("init_livein")))


@pytest.mark.xfail(reason="register-model-lift Phase 2: multiplexed pair splits per role", **XFAIL)
def test_mux_pair_certifies_the_pointer_role():
    text = _lift("mux_pair")
    body = text[text.index("sub_") :]
    assert "mem[" not in body, "the pointer role still reads through a raw address"


@pytest.mark.xfail(reason="register-model-lift Phase 2: cursor values as data", **XFAIL)
def test_cursor_save_restore_lifts_to_cursor_values():
    text = _lift("cursor_save")
    body = text[text.index("sub_") :]
    assert "mem[" not in body, "the saved/restored cursor still derefs a raw address"


@pytest.mark.xfail(
    reason="register-model-lift Phase 2: write-through becomes a table write", **XFAIL
)
def test_writethrough_store_becomes_a_bounded_table_write():
    text = _lift("writethrough")
    body = text[text.index("sub_") :]
    assert not re.search(r"^\s*mem\[.*\] = ", body, re.M), "the store still writes through top"


def _fused_cursor(name):
    """True where rung (d) declared the walked pair one ``u16`` field, not two lanes.

    ``framefuse.refusal()`` reads one surviving byte-lane read of the pair as refusing
    its whole tune-wide declaration, even where an equal word read stands beside it: it
    is that read, not the second destination or the store order, that discriminates."""
    return "ptr_%04X: u16" % G.PTR in _state_block(_lift(name))


def test_a_plain_advance_fuses_its_cursor_pair():
    """Invariant: the baseline the dual-destination family is measured against."""
    assert _fused_cursor("plain_advance"), "a bare in-place advance left the pair byte-wise"


def test_a_word_copy_of_the_advanced_cursor_leaves_it_fused():
    """Invariant: a second destination is no refusal where every lane read folds to a word.

    The save copy reads the pair after the advance, so rung (d2) merges both the
    advance and the copy; no byte-lane read of the pair survives to refuse it."""
    assert _fused_cursor("dual_store_word_copy"), "a word-wide save copy refused the pair"


@pytest.mark.xfail(
    reason="register-model-lift 2b: a dual-destination advance keeps the pair byte-wise", **XFAIL
)
def test_dual_store_advance_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_advance")


@pytest.mark.xfail(
    reason="register-model-lift 2b: store order within a lane does not free the pair", **XFAIL
)
def test_dual_store_pair_first_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_pair_first")


@pytest.mark.xfail(
    reason="register-model-lift 2b: uninterleaved pair stores still keep it byte-wise", **XFAIL
)
def test_dual_store_via_regs_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_via_regs")


@pytest.mark.xfail(
    reason="register-model-lift 2b: a hi-first dual-destination advance keeps the pair byte-wise",
    **XFAIL,
)
def test_dual_store_hi_first_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_hi_first")


@pytest.mark.xfail(
    reason="register-model-lift 2b: a cell-stepped dual store keeps it byte-wise", **XFAIL
)
def test_dual_store_computed_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_computed")


@pytest.mark.xfail(
    reason="register-model-lift 2b: a lo-only dual store refuses certification, not just naming",
    **XFAIL,
)
def test_dual_store_lo_only_fuses_its_cursor_pair():
    """One lane's copy is enough: rung (g) then refuses ``def_unliftable``, not the spelling.

    ``ptrcert._def_refusal`` reads the role, so the hi lane refuses as ``computed`` though
    both lanes extract one named word -- a rule gap the corpus never exhibits (0 webs; the
    nearest 2 are lo-lane advances spelled through the word), so it is no lift to recover."""
    assert _fused_cursor("dual_store_lo_only")


@pytest.mark.xfail(
    reason="register-model-lift 2b: a stack-spilled cursor has no word form to appeal to", **XFAIL
)
def test_stack_spill_cursor_fuses_its_cursor_pair():
    """The largest group (15 webs), and the one no proven-word-form fix can reach.

    Corpus-wide the 164 lone-half reads are 130 bare copies to 33 ``INT_ADD``, sinking to
    ``asg`` (96) and ``st`` (70) and to no ``if`` at all: not one is a page-alignment test
    or an end-of-block compare, so the byte-wise evidence is spill and carry residue."""
    assert _fused_cursor("stack_spill_cursor")


@pytest.mark.xfail(
    reason="register-model-lift 2b: an unobserved carry arm leaves the hi lane unpaired", **XFAIL
)
def test_deferred_carry_cursor_fuses_its_cursor_pair():
    """The hi lane is in the code but not the text, so no store pairs with the lo one."""
    assert _fused_cursor("deferred_carry_cursor")


@pytest.mark.xfail(
    reason="register-model-lift 2b: a split lo/hi save-back destination cannot pair", **XFAIL
)
def test_table_spill_cursor_fuses_its_cursor_pair():
    """The advance is already one u16 store; the de-interleaved save-back is what refuses."""
    assert _fused_cursor("table_spill_cursor")


@pytest.mark.xfail(
    reason="register-model-lift 2b: interleaved half stores never pair, so the pair refuses",
    **XFAIL,
)
def test_unpaired_half_store_fuses_its_cursor_pair():
    """The one rung (d) class no other fixture reaches: no lane read, the stores unpaired.

    ``framefuse.refusal()`` reports the first failing premise only, testing ``lone``
    before ``unpaired``, so a fixture's stated class is the first that fails and not
    necessarily the only one - the classes are ordered, not disjoint."""
    assert _fused_cursor("unpaired_half_store")


def test_an_inpage_advance_is_never_fused():
    """Invariant: a bare INC lane with no carry arm is genuinely byte-wise (8 of the 74).

    Fusing would carry on a lane wrap the machine does not, so no rung (d) fix may take it.
    A fix keyed on the word form existing elsewhere clears at most 45 of 68 refusals; one
    keyed on the merge premises reaches 66, and these 8 must stay refused for good."""
    assert not _fused_cursor("inpage_advance"), "a byte-wise pair was widened to u16"


@pytest.mark.xfail(reason="register-model-lift G2: INT_ADD bound in addr_bits", **XFAIL)
def test_g2_bounds_the_zext_add_store():
    _lift("g2_store")
    bad = [b for b in _unnamed_store_bounds(_lift_prog["g2_store"]) if b > 0x01FF]
    assert not bad, "a (zext2(y) + $NN) store is still counted top-wide"
