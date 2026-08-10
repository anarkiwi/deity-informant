"""The register-model shredder (docs/register-model-lift-impl.md).

Each fixture forces one register-model artifact and stays ``xfail(strict=True)``
until its stage lands; each stage-3 pin also carries the adoption §8 step 4
disposition measured on ``_emit`` -- one of ``_MEASURED``'s three verdicts."""

import re
import sys
from functools import lru_cache
from pathlib import Path

import pytest

import _fuzzgen as G
from test_frameprog import _fuzz_model
from deity_informant import eqlift_mem, frameproc, frameprog, frameval, ptrcert, ptrextent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import value_walk

SID = G.SID
TMP = G.CNT + 0x20  # a RAM cell used only as per-frame scratch
CTR = G.CNT + 0x22  # a 16-bit per-tune counter pair
TGT = G.CNT + 0x24  # a 16-bit threshold pair
POS = G.CNT + 0x26  # a persistent position cell (the pos_54EC shape)
MODE = G.CNT + 0x28  # a per-tune phase toggle
SAV = G.CNT + 0x2A  # cursor save cell pair (the Follin loop-cell shape)
DEPTH = G.CNT + 0x2C  # the Follin per-voice call-stack depth cell (zp_6A shape)
DIV = G.CNT + 0x2E  # where the shift-divide role parks its quotient pair
FTC = G.PTR + 2  # a second zero-page pair: the fetch cursor half stores arrive through
ZTMP = 0x0030  # a zero-page cell used only as per-frame scratch
PAT = G.TBL + 0x100  # sequence data block the zero-page pointer walks
PAT2 = G.TBL + 0x160  # second sequence block (cursor save/restore target)
BLK = G.TBL + 0x180  # RAM block a pointer stores through
STK = G.TBL + 0x1C0  # split return-stack columns, lo at STK, hi at STK+3 (m_6B25 shape)
SPSUB = 0x1300  # a subroutine two call depths reach, so its sp never concretizes
SPMID = 0x1340  # the second depth
HND0 = 0x13C0  # dispatch handler stubs (the SMC-operand jmp's observed targets)
HND1 = 0x13E0

XFAIL = dict(strict=True)

_S3 = "register-model-lift stage 3:"
_MEASURED = (  # the three §8 step 4 dispositions; every stage-3 reason names one
    "unified path: holds today",  # a pre-verified flip: step 4 XPASSes the pin
    "unified path: not an emission property",  # a pre-emission rung verdict step 4 keeps
    "unified path: refuses, and the mechanism is named",  # emission's, with its owner
)


@lru_cache(maxsize=None)
def _build(name):
    """``(model, frames, program)`` before the gate, so a pin may read the verdict."""
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
    _lift_extra[name] = extra
    model = _fuzz_model(player)
    return model, frames, frameprog.program(model)


@lru_cache(maxsize=None)
def _lift(name):
    model, frames, prog = _build(name)
    text = frameprog.dumps(prog)
    assert frameval.gate_fp(model, frames, prog) is None, "the frame oracle must hold"
    assert frameprog.dumps(frameprog.loads(text)) == text
    _lift_prog[name] = prog
    _lift_ctx[name] = (model, frames)
    return text


def _gate(name):
    """Gate FP's verdict, an evaluation fault reported as its own text.

    ``_lift`` asserts the verdict away; the stage-4 family reads it, because a
    continuation the machine never takes faults out of the evaluator rather
    than diverging in the log."""
    model, frames, prog = _build(name)
    try:
        return frameval.gate_fp(model, frames, prog)
    except frameval.FrameFault as exc:
        return str(exc)


@lru_cache(maxsize=None)
def _emit(name):
    """The same model's text from the unified graph: the emitter §8 step 4 cuts over to.

    ``frameprog.program`` runs the structural rungs before emission and ``emit_mem``
    renders raw ``_Builder`` procedures, so a property this refuses may be refused by a
    missing rung rather than a missing rule; the pins say which."""
    text, _second = eqlift_mem.emit(_build(name)[0])
    assert text.startswith("eqlift 0")
    return text


def _emit_body(name):
    return _emit(name)[_emit(name).index("sub_") :]


_lift_prog = {}
_lift_ctx = {}
_lift_extra = {}


def _cert(name):
    """The walked pair's certification record: 2a's authority, read at suite cost."""
    _lift(name)
    (rec,) = [r for r in ptrcert.certify(_lift_prog[name])[0] if r["root"] == "$%04X" % G.PTR]
    return rec


def _sp_classes(name):
    """The ``drop_sp`` refusal classes the program carries: Phase 1's ledger keys."""
    _lift(name)
    proofs = _lift_prog[name].proofs
    return sorted({p.lemma.split(":", 1)[0] for p in proofs if p.kind == "sp"} - {"sp"})


def _observed(name):
    """b0's observed-extent record for the walked pair, from the fixture's own run."""
    _lift(name)
    model, frames = _lift_ctx[name]
    prog = _lift_prog[name]
    trace, _walker = frameprog.iota(model, frames)
    probe = ptrextent.Probe()
    ev = frameval.Evaluator(prog, trace, probe=probe)
    for f in range(frames):
        ev.frame = f
        ev.run_frame()
    (rec,) = [r for r in ptrextent.extents(prog, probe.hits) if r["root"] == "$%04X" % G.PTR]
    return rec


def _walk(name, extents=()):
    """Phase 2.5's verdicts for this fixture: the walker's answer beside the pinned one."""
    _lift(name)
    model, frames = _lift_ctx[name]
    return value_walk.row(model, _lift_prog[name], extents, frames)


def _state_block(text):
    i = text.index("state {")
    return text[i : text.index("}", i)]


def _unnamed_stores(prog):
    """``(address expression, its G1 env)`` for every store whose address names no datum."""
    out = []

    def walk(stmts, outer, cyclic):
        env = frameproc.Defs(stmts, outer, cyclic)
        for k, s in enumerate(stmts):
            for body in frameproc._stmt_bodies(s):
                walk(body, (env, k), s[0] in frameproc._CYCLIC)
            if s[0] == "st" and frameproc.addr_split(s[1])[0] is None:
                if s[1] not in prog.resolved:
                    out.append((s[1], frameproc.DefsAt(env, k)))

    for _e, _p, _r, stmts in prog.procs:
        walk(stmts, None, False)
    return out


def _unnamed_store_bounds(prog):
    """G1-resolved reach bound of every store whose address names no datum."""
    return [frameproc.addr_bits(a, defs) for a, defs in _unnamed_stores(prog)]


def _subterms(e):
    """``e`` and every operand below it."""
    yield e
    for k in e[2] if e[0] == "op" else (e[1],) if e[0] == "mem" else ():
        yield from _subterms(k)


def _ops(e, mn):
    """``mem_rules``' interval for every ``mn`` operand in ``e``, outermost first."""
    return [eqlift_mem._lattice(k) for k in _subterms(e) if k[0] == "op" and k[1] == mn]


def _stores_to(prog, addr):
    """Every value the program stores at ``addr``, at any nesting depth."""
    out = []

    def walk(stmts):
        for s in stmts:
            for body in frameproc._stmt_bodies(s):
                walk(body)
            if s[0] == "st" and s[1][0] == "const" and s[1][1] == addr:
                out.append(s[2])

    for _e, _p, _r, stmts in prog.procs:
        walk(stmts)
    return out


def _scratch():
    """Stage 3: a cell written before read every frame is a local, not state."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x11).i("STA", "abs", CTR)
    a.i("AND", "imm", 0x0F).i("STA", "abs", TMP)
    a.i("LDA", "abs", TMP).i("ORA", "imm", 0x40).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, {CTR: 0}, 8


def _pointer_walk():
    """Stage 3: a reloaded-and-advancing pointer is a cursor into its blocks."""
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
    """Stage 3: a 16-bit compare split into SBC lanes is one wide compare."""
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
    """Stage 3: widening a lone lane half may not read the write-only register."""
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
    """Invariant: a cell a write-through pointer store may clobber stays memory."""
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
    """Invariant (stage 3 init coupling): frame 0 reads what init wrote, so state."""
    ini = G.Asm(0x0F00)
    ini.i("LDA", "imm", 0x21).i("STA", "abs", POS).i("RTS")
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", POS).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", POS).i("EOR", "imm", 0x01).i("STA", "abs", POS)
    a.i("RTS")
    return a, {POS: 0}, 6, {"init": ini.assemble(), "init_org": 0x0F00}


def _mux_pair():
    """Stage 3: one zp pair, pointer role on one path, counter role on the other."""
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
    """Stage 3: the pointer is copied to save cells and restored, Follin-style."""
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
    """Stage 3: a store through a reloaded sequence pointer (the 12-tune class)."""
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
    """Stage 3 (control): a bare in-place advance, no second destination at all."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("RTS")
    return a, _cursor_data(), 8


def _dual_store_advance():
    """Stage 3: the Ghouls advance - per lane a save copy, then the pair in place."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02)
    a.i("STA", "abs", SAV).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00)
    a.i("STA", "abs", SAV + 1).i("STA", "zp", G.PTR + 1)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _dual_store_pair_first():
    """Stage 3: the same advance with the pair store leading its lane's save copy."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02)
    a.i("STA", "zp", G.PTR).i("STA", "abs", SAV)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00)
    a.i("STA", "zp", G.PTR + 1).i("STA", "abs", SAV + 1)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _dual_store_via_regs():
    """Stage 3: the save copies deferred through X/Y, so the pair stores stay adjacent."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("TAX").i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("TAY").i("STA", "zp", G.PTR + 1)
    a.i("STX", "abs", SAV).i("STY", "abs", SAV + 1)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _dual_store_hi_first():
    """Stage 3: the dual-destination advance with the hi lane stored before the lo."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("TAX")
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00)
    a.i("STA", "abs", SAV + 1).i("STA", "zp", G.PTR + 1)
    a.i("TXA").i("STA", "abs", SAV).i("STA", "zp", G.PTR)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _dual_store_computed():
    """Stage 3: the dual-destination advance stepping by a cell, not an immediate."""
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
    """Stage 3: the save copy taken off the lo lane only, so one lane stays paired."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02)
    a.i("STA", "abs", SAV).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("RTS")
    return a, _cursor_data(SAV), 8


def _dual_store_word_copy():
    """Stage 3: a second destination fed by a plain advance, copied lane by lane."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "zp", G.PTR).i("STA", "abs", SAV)
    a.i("LDA", "zp", G.PTR + 1).i("STA", "abs", SAV + 1)
    a.i("RTS")
    return a, _cursor_data(SAV, SAV + 1), 8


def _stack_spill_cursor():
    """Stage 3 (A): the cursor pushed a lane at a time, so no word form exists at all.

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
    """Stage 3 (B2): an INC/BNE/INC advance whose carry arm the run never enters."""
    a = G.Asm(G.ORG)
    a.i("INC", "zp", G.PTR).i("BNE", "rel", ("L", "skip"))
    a.i("INC", "zp", G.PTR + 1)
    a.label("skip")
    a.i("LDY", "imm", 0x00).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    a.i("RTS")
    return a, _cursor_data(), 8


def _table_spill_cursor():
    """Stage 3 (C): the cursor reloaded from and saved back to a split lo/hi table."""
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
    """Stage 3 (E): a bare INC advance with no carry arm - fusing it would be wrong."""
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
    """Stage 3 (Commando): both halves fetched through another cursor, never read back."""
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


def _follin_jump():
    """2b/b3: the script jump - the cursor's next value read out of the block it walks.

    Ghouls $6AD0 (tools/disasm_tune.py): LDA (21),Y / TAX / INY / LDA (21),Y /
    STA $22 / STX $21 - the block_read def, hi stored first through a register."""
    a = _cursor()
    a.i("CMP", "imm", 0x80).i("BCC", "rel", ("L", "adv"))
    a.i("LDY", "imm", 0x01).i("LDA", "indy", G.PTR).i("TAX")
    a.i("INY").i("LDA", "indy", G.PTR)
    a.i("STA", "zp", G.PTR + 1).i("STX", "zp", G.PTR)
    a.i("JMP", "abs", ("L", "out"))
    a.label("adv")
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.label("out").i("RTS")
    data = _cursor_data()
    data[PAT + 5] = 0x80  # the jump op; its operand word aims the cursor back at PAT
    data[PAT + 6] = PAT & 0xFF
    data[PAT + 7] = PAT >> 8
    return a, data, 8


def _follin_ret_stack():
    """2b/b3: call pushes ptr+3 into split columns via a depth cell; ret pops.

    Ghouls $6ADD/$6B42 (tools/disasm_tune.py): STA $6B25,X / STA $6B28,X columns
    3 apart, INX depth - cursor values as data, on a mutable de-interleaved table."""
    a = _cursor()
    a.i("CMP", "imm", 0x80).i("BCC", "rel", ("L", "ret"))
    a.i("LDX", "abs", DEPTH)
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x03).i("STA", "absx", STK)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "absx", STK + 3)
    a.i("INX").i("STX", "abs", DEPTH)
    a.i("LDA", "imm", PAT2 & 0xFF).i("STA", "zp", G.PTR)
    a.i("LDA", "imm", PAT2 >> 8).i("STA", "zp", G.PTR + 1)
    a.i("JMP", "abs", ("L", "out"))
    a.label("ret")
    a.i("LDX", "abs", DEPTH).i("BEQ", "rel", ("L", "out"))
    a.i("DEX").i("STX", "abs", DEPTH)
    a.i("LDA", "absx", STK).i("STA", "zp", G.PTR)
    a.i("LDA", "absx", STK + 3).i("STA", "zp", G.PTR + 1)
    a.label("out").i("RTS")
    data = _cursor_data(DEPTH)
    data[PAT] = 0x81  # the call op: push ptr+3, enter PAT2
    data.update({PAT2 + k: 0x20 | (k & 0x0F) for k in range(0x10)})
    data.update({STK + k: 0 for k in range(6)})
    return a, data, 8


def _lone_lane_block_read():
    """2b/b3: the hi lane alone is read out of the block; the lo lane steps.

    American $B41A (tools/disasm_tune.py): LDA ($FB),Y / PHA / INY / LDA ($FB),Y /
    STA $FC / PLA / STA $FB - the hi lane is a block read and the lo lane arrives
    from elsewhere, so the two never meet at one seat and no LE word was read."""
    a = _cursor()
    a.i("CMP", "imm", 0x80).i("BCC", "rel", ("L", "adv"))
    a.i("LDY", "imm", 0x01).i("LDA", "indy", G.PTR).i("STA", "zp", G.PTR + 1)
    a.i("JMP", "abs", ("L", "out"))
    a.label("adv")
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "zp", G.PTR)
    a.label("out").i("RTS")
    data = _cursor_data()
    data[PAT + 3] = 0x80  # the jump op; the byte beside it re-selects the same page
    data[PAT + 4] = PAT >> 8
    return a, data, 8


def _low_held_cursor():
    """b1 (iv): the pair held on the machine stack in a sub reached at two depths.

    Angry_Birds $09F1..$0A35 (tools/disasm_tune.py): LDA $FE/PHA/LDA $FF/PHA at
    entry, PLA/STA $FF/PLA/STA $FE at exit - the restore reads page one through sp."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("JSR", "abs", SPSUB).i("JSR", "abs", SPMID)
    a.i("RTS")
    mid = G.Asm(SPMID).i("JSR", "abs", SPSUB).i("RTS")
    sub = G.Asm(SPSUB)
    sub.i("LDA", "zp", G.PTR).i("PHA").i("LDA", "zp", G.PTR + 1).i("PHA")
    sub.i("LDX", "abs", CTR)
    sub.i("LDA", "absx", G.TBL).i("STA", "zp", G.PTR)
    sub.i("LDA", "absx", G.TBL + 2).i("STA", "zp", G.PTR + 1)
    sub.i("LDY", "imm", 0x00).i("LDA", "indy", G.PTR).i("STA", "abs", SID + 4)
    sub.i("PLA").i("STA", "zp", G.PTR + 1).i("PLA").i("STA", "zp", G.PTR)
    sub.i("RTS")
    data = _cursor_data(CTR)
    data.update(
        {
            G.TBL: PAT & 0xFF,
            G.TBL + 1: (PAT + 8) & 0xFF,
            G.TBL + 2: PAT >> 8,
            G.TBL + 3: (PAT + 8) >> 8,
            G.TBL + 4: PAT & 0xFF,
        }
    )
    data.update({SPSUB + k: b for k, b in enumerate(sub.assemble())})
    data.update({SPMID + k: b for k, b in enumerate(mid.assemble())})
    return a, data, 8


def _alias_web():
    """b1 (i): a wrapping zp,X store with unbounded X may reach any zero-page pair.

    ASL/04 $128B (tools/disasm_tune.py --opcode 0x95): STA $FD,X spells
    zext2((x - $03)), an interval the reach analysis cannot keep off the pair."""
    a = _cursor()
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x02).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.i("LDX", "abs", CTR)
    a.i("LDA", "imm", 0x11).i("STA", "zpx", 0x40)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("RTS")
    data = _cursor_data(CTR)
    data.update({0x40 + k: 0 for k in range(0x10)})
    return a, data, 8


def _call_returned_row():
    """b1 (ii): the row crosses a call boundary - the callee returns it in A/X."""
    a = _cursor()
    a.i("CMP", "imm", 0x5F).i("BNE", "rel", ("L", "adv"))
    a.i("JSR", "abs", SPSUB)
    a.i("STA", "zp", G.PTR).i("STX", "zp", G.PTR + 1)
    a.i("JMP", "abs", ("L", "out"))
    a.label("adv")
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.label("out").i("RTS")
    sub = G.Asm(SPSUB)
    sub.i("LDX", "abs", CTR)
    sub.i("LDA", "absx", G.TBL)
    sub.i("PHA")
    sub.i("LDA", "abs", CTR).i("EOR", "imm", 0x01).i("STA", "abs", CTR)
    sub.i("LDX", "imm", PAT >> 8)
    sub.i("PLA")
    sub.i("RTS")
    data = _cursor_data(CTR)
    data.update({G.TBL: PAT & 0xFF, G.TBL + 1: (PAT + 8) & 0xFF})
    data[PAT + 2] = 0x5F
    data[PAT + 10] = 0x5F
    data.update({SPSUB + k: b for k, b in enumerate(sub.assemble())})
    return a, data, 12


def _computed_rows():
    """b0: the reload row is arithmetic, aimed where the registry declares nothing.

    The Galway/goto80 shape behind extent_unmappable: via: discovery anchors the
    walked stream, but a computed row breaks the chain and lands off the registry."""
    a = _cursor()
    a.i("CMP", "imm", 0x5F).i("BNE", "rel", ("L", "adv"))
    a.i("LDA", "abs", CTR).i("AND", "imm", 0x01)
    a.i("ASL", "acc").i("ASL", "acc").i("ASL", "acc")
    a.i("ORA", "imm", 0x80).i("STA", "zp", G.PTR)
    a.i("LDA", "imm", G.ORG >> 8).i("STA", "zp", G.PTR + 1)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("JMP", "abs", ("L", "out"))
    a.label("adv")
    a.i("LDA", "zp", G.PTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "zp", G.PTR)
    a.i("LDA", "zp", G.PTR + 1).i("ADC", "imm", 0x00).i("STA", "zp", G.PTR + 1)
    a.label("out").i("RTS")
    data = {CTR: 0, G.PTR: PAT & 0xFF, G.PTR + 1: PAT >> 8}
    data.update({PAT + k: 0x40 | (k & 0x1E) for k in range(0x40)})
    data[PAT + 2] = 0x5F
    data.update({G.ORG + 0x80 + k: 0x20 | (k & 0x0F) for k in range(0x20)})
    return a, data, 12


def _sp_fix_balance():
    """2c: an interior label at nonzero displacement, entry-balanced on every path."""
    sub = G.Asm(SPSUB)
    sub.i("PHA").i("LDA", "abs", CTR).i("AND", "imm", 0x01).i("BNE", "rel", ("L", "odd"))
    sub.i("LDA", "abs", CTR).i("AND", "imm", 0x0F).i("STA", "abs", TMP)
    sub.i("PLA").i("EOR", "imm", 0x02).i("RTS")
    sub.label("odd")
    sub.i("PLA").i("EOR", "imm", 0x04).i("RTS")
    return _sp_body(sub)


def _sp_scratch_floor():
    """Stage 3: zero-page scratch beside kept sp fabric still promotes.

    Without frameproc.addr_floor the kept push (zext2(sp)|$0100) reaches an
    interval from zero and spuriously threatens every zero-page cell."""
    a, data, frames = _sp_body(_sp_loop_sub())
    a2 = G.Asm(G.ORG)
    a2.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a2.i("AND", "imm", 0x0F).i("STA", "zp", ZTMP)
    a2.i("JSR", "abs", SPSUB).i("JSR", "abs", SPMID).i("JSR", "abs", SPMID)
    a2.i("LDA", "zp", ZTMP).i("ORA", "imm", 0x21).i("STA", "abs", SID + 4).i("RTS")
    data[ZTMP] = 0
    return a2, data, frames


def _sp_loop_sub():
    """A loop between the push and the pull, so its back edge carries a displacement."""
    sub = G.Asm(SPSUB)
    sub.i("PHA").i("LDX", "imm", 0x03)
    sub.label("lp").i("DEX").i("BNE", "rel", ("L", "lp"))
    sub.i("PLA").i("EOR", "imm", 0x02).i("RTS")
    return sub


def _phase_split_reload():
    """Stage 3: the pair's halves reloaded in different frames by a phase machine.

    Air_on_a_Rasterline $0C1A/$0D05 (tools/disasm_tune.py): one play phase writes
    zp_FC from m_1145, a later phase writes zp_FB from m_118A - no frame holds a
    pair store for rung (d2) to pair, yet each store is a plain lane replacement."""
    a = _cursor()
    a.i("LDA", "abs", MODE).i("CLC").i("ADC", "imm", 0x01).i("AND", "imm", 0x03)
    a.i("STA", "abs", MODE).i("TAY")
    a.i("CPY", "imm", 0x01).i("BNE", "rel", ("L", "hi"))
    a.i("LDA", "absy", G.TBL).i("STA", "zp", G.PTR)
    a.i("JMP", "abs", ("L", "out"))
    a.label("hi")
    a.i("CPY", "imm", 0x02).i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", PAT >> 8).i("STA", "zp", G.PTR + 1)
    a.label("out").i("RTS")
    data = _cursor_data(MODE)
    data.update({G.TBL + k: (PAT + 2 * k) & 0xFF for k in range(4)})
    return a, data, 8


def _shift_divide():
    """Stage 3: the pair as a divide accumulator - (T2[y]-T1[y]) >> n, n from a cell.

    Cool_Air $1447..$145D (tools/disasm_tune.py): SEC/SBC lanes build the 16-bit
    interval, then an LSR A / ROR $FB loop rotates it right n times - the 6502
    has no divide, so a power-of-two divide is a loop-carried lane rotate."""
    a = _cursor()
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("AND", "imm", 0x01).i("BEQ", "rel", ("L", "out"))
    a.i("LDA", "abs", CTR).i("AND", "imm", 0x03).i("ORA", "imm", 0x01).i("TAX")
    a.i("LDA", "abs", CTR).i("AND", "imm", 0x03).i("TAY")
    a.i("LDA", "absy", PAT2 + 16).i("SEC").i("SBC", "absy", PAT2).i("STA", "zp", G.PTR)
    a.i("LDA", "absy", PAT2 + 24).i("SBC", "absy", PAT2 + 8)
    a.label("lp").i("LSR", "acc").i("ROR", "zp", G.PTR)
    a.i("DEX").i("BNE", "rel", ("L", "lp"))
    a.i("STA", "zp", G.PTR + 1)
    a.i("LDA", "zp", G.PTR).i("STA", "abs", DIV)
    a.i("LDA", "zp", G.PTR + 1).i("STA", "abs", DIV + 1)
    a.i("LDA", "imm", PAT & 0xFF).i("STA", "zp", G.PTR)
    a.i("LDA", "imm", PAT >> 8).i("STA", "zp", G.PTR + 1)
    a.label("out").i("RTS")
    data = _cursor_data(CTR, DIV, DIV + 1)
    data.update({PAT2 + k: (0x30 + 3 * k) for k in range(4)})
    data.update({PAT2 + 8 + k: 0x01 for k in range(4)})
    data.update({PAT2 + 16 + k: (0x20 + 5 * k) for k in range(4)})
    data.update({PAT2 + 24 + k: 0x1F for k in range(4)})
    return a, data, 8


def _dispatch_scratch():
    """Stage 3: scratch written before an SMC-operand dispatch, read by handlers.

    Ghouls $6360..$6374 in miniature (docs/follin-dispatch-study.md section 1):
    paired table rows patch a jmp operand - a switch goto join, not a wall."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x11).i("STA", "abs", CTR)
    a.i("AND", "imm", 0x0F).i("STA", "abs", TMP)
    a.i("LDA", "abs", CTR).i("AND", "imm", 0x01).i("TAX")
    a.i("LDA", "absx", G.TBL).i("STA", "abs", ("L", "site", 1))
    a.i("LDA", "absx", G.TBL + 2).i("STA", "abs", ("L", "site", 2))
    a.label("site").i("JMP", "abs", 0x0000)
    data = {CTR: 0, TMP: 0}
    data.update({G.TBL: HND0 & 0xFF, G.TBL + 1: HND1 & 0xFF})
    data.update({G.TBL + 2: HND0 >> 8, G.TBL + 3: HND1 >> 8})
    for base, orv in ((HND0, 0x20), (HND1, 0x40)):
        h = G.Asm(base)
        h.i("LDA", "abs", TMP).i("ORA", "imm", orv).i("STA", "abs", SID + 4).i("RTS")
        data.update({base + k: b for k, b in enumerate(h.assemble())})
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


def _sp_loop_edge():
    """Invariant: a loop edge standing at a displacement keeps sp -- 2c's own bound."""
    return _sp_body(_sp_loop_sub())


def _sp_unbalanced():
    """Invariant: a procedure that discards its own return address keeps sp.

    720_Degrees $C31D (tools/disasm_tune.py): PLA/PLA/RTS returns one level out,
    so the machine reads page one for a target the entry displacement does not
    name -- the one shape in the corpus the balance fixpoint must still refuse."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("JSR", "abs", SPMID)
    a.i("LDA", "abs", TMP).i("ORA", "imm", 0x21).i("STA", "abs", SID + 4).i("RTS")
    mid = G.Asm(SPMID)
    mid.i("JSR", "abs", SPSUB).i("LDA", "imm", 0x0C).i("STA", "abs", TMP).i("RTS")
    sub = G.Asm(SPSUB)
    sub.i("LDA", "abs", CTR).i("AND", "imm", 0x01).i("BEQ", "rel", ("L", "back"))
    sub.i("PLA").i("PLA")
    sub.i("LDA", "abs", CTR).i("AND", "imm", 0x0F).i("STA", "abs", TMP).i("RTS")
    sub.label("back").i("LDA", "imm", 0x03).i("STA", "abs", TMP).i("RTS")
    data = {CTR: 0, TMP: 0}
    data.update({SPSUB + k: b for k, b in enumerate(sub.assemble())})
    data.update({SPMID + k: b for k, b in enumerate(mid.assemble())})
    return a, data, 8


def _raw_call_body(a, sub):
    """A player whose ``JSR SPSUB`` stays a raw call: a tail ``JMP`` blocks the pcall.

    ``frameproc`` will not give a procedure a register interface once another
    procedure jumps into it, so the machine, not the text, threads this call."""
    a.label("alt").i("JMP", "abs", SPSUB)
    data = {CTR: 0, TMP: 0}
    data.update({SPSUB + k: b for k, b in enumerate(sub.assemble())})
    return a, data, 8


def _sp_call_at_entry():
    """2c: a raw call standing at the entry displacement drops its linkage.

    The machine writes the return bytes exactly where it always did, so nothing
    the program keeps can tell that the displacement went with ``sp``."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("AND", "imm", 0x01).i("BNE", "rel", ("L", "alt"))
    a.i("JSR", "abs", SPSUB)
    a.i("ORA", "imm", 0x21).i("STA", "abs", SID + 4).i("RTS")
    sub = G.Asm(SPSUB)
    sub.i("PHA").i("LDA", "abs", CTR).i("AND", "imm", 0x0F).i("STA", "abs", TMP)
    sub.i("PLA").i("EOR", "imm", 0x02).i("RTS")
    return _raw_call_body(a, sub)


def _sp_call_displaced():
    """Invariant (2c): a raw call at a nonzero displacement keeps its linkage.

    The caller pushes before the call, so dropping its displacement would move
    where the machine writes the return bytes this call pushes."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("STA", "abs", CTR)
    a.i("AND", "imm", 0x01).i("BNE", "rel", ("L", "alt"))
    a.i("LDA", "abs", CTR).i("PHA")
    a.i("JSR", "abs", SPSUB)
    a.i("PLA").i("ORA", "imm", 0x21).i("STA", "abs", SID + 4).i("RTS")
    sub = G.Asm(SPSUB)
    sub.i("LDA", "abs", CTR).i("AND", "imm", 0x0F).i("STA", "abs", TMP).i("RTS")
    return _raw_call_body(a, sub)


def _g2_store():
    """Stage 3 (G2 shape, 7.10.3): a (zext2(y) + $NN) store is bounded under $01FF, not top."""
    a = G.Asm(G.ORG)
    a.i("LDY", "abs", CTR)
    a.i("LDA", "abs", CTR).i("ORA", "imm", 0x40).i("STA", "absy", 0x00A5)
    a.i("LDA", "zp", 0xA8).i("AND", "imm", 0x0F).i("ORA", "imm", 0x20).i("STA", "abs", SID + 4)
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("AND", "imm", 0x07).i("STA", "abs", CTR)
    a.i("RTS")
    data = {CTR: 0}
    data.update({0x00A5 + k: 0 for k in range(8)})
    return a, data, 8


def _skip_sub(n=None):
    """The CyberTracker ``$4921`` callee (docs/frameprog.md section 5, #155).

    It pops its own return address into a pointer, copies the inline bytes that
    follow the ``JSR`` through it, pushes the address back advanced past them
    and returns, so the machine lands on the byte after the data. ``n`` fixed is
    the corpus's own shape; ``None`` reads the count out of the first inline
    byte, which makes the skip a property of the site and not of the callee."""
    sub = G.Asm(SPSUB)
    sub.i("PLA").i("STA", "zp", FTC)
    sub.i("PLA").i("STA", "zp", FTC + 1)
    if n is None:
        sub.i("LDY", "imm", 0x01).i("LDA", "indy", FTC).i("TAX").i("STA", "abs", TMP)
        sub.label("cp").i("INY").i("LDA", "indy", FTC).i("STA", "absy", BLK - 2)
        sub.i("DEX").i("BNE", "rel", ("L", "cp"))
        sub.i("SEC").i("LDA", "zp", FTC).i("ADC", "abs", TMP).i("STA", "zp", FTC)
    else:
        sub.i("LDY", "imm", 0x01)
        sub.label("cp").i("LDA", "indy", FTC).i("STA", "absy", BLK - 1)
        sub.i("INY").i("CPY", "imm", n + 1).i("BNE", "rel", ("L", "cp"))
        sub.i("LDA", "zp", FTC).i("CLC").i("ADC", "imm", n).i("STA", "zp", FTC)
    sub.i("LDA", "zp", FTC + 1).i("ADC", "imm", 0x00).i("STA", "zp", FTC + 1)
    sub.i("PHA").i("LDA", "zp", FTC).i("PHA")
    sub.i("RTS")
    return sub


def _skip_site(a, tag, payload, orv):
    """One inline-data call site: the call, its bytes, and a tail the output needs.

    The tail reads what the callee copied, so the bytes are observed reads and a
    continuation that misses the skip is a different SID write, not a no-op."""
    a.i("JSR", "abs", SPSUB).label(tag)
    for b in payload:
        a.byte(b)
    a.label(tag + "c")
    a.i("LDX", "abs", CTR).i("LDA", "absx", BLK).i("ORA", "imm", orv)
    a.i("STA", "abs", SID + 4)


def _skip_spans(*pairs):
    """``[(first inline byte, first byte past the data)]`` per site, in code order."""
    out = []
    for a, tags in pairs:
        a.assemble()
        out += [(a.labels[t], a.labels[t + "c"]) for t in tags]
    return out


def _skip_data(n, sub):
    """The seed a skip fixture runs on: the copy target, the pointer and the callee."""
    data = {CTR: 0, MODE: 0, TMP: 0, FTC: 0, FTC + 1: 0}
    data.update({BLK + k: 0 for k in range(n)})
    data.update({SPSUB + k: b for k, b in enumerate(sub.assemble())})
    return data


def _skip_head(a, mask=0x03):
    a.i("LDA", "abs", CTR).i("CLC").i("ADC", "imm", 0x01).i("AND", "imm", mask)
    a.i("STA", "abs", CTR)


def _skip_tail(a):
    a.i("LDA", "abs", MODE).i("EOR", "imm", 0x01).i("STA", "abs", MODE).i("RTS")


def _jsr_inline_skip():
    """Stage 4 (control): the trick at one call site, and it lifts today.

    The lone raw call writes the return slot at a concrete stack cell, so the
    callee's rewrite of that cell is a constant push pair and ``lift_rts_trick``
    turns it into the goto it is - the continuation the machine takes."""
    a = G.Asm(G.ORG)
    _skip_head(a)
    _skip_site(a, "s0", (0x01, 0x02, 0x03, 0x04), 0x20)
    a.i("RTS")
    return a, _skip_data(4, _skip_sub(4)), 8, {"skip": _skip_spans((a, ("s0",)))}


def _jsr_inline_skip_two_sites():
    """Stage 4: the corpus's two sites of one callee ($4ED4 and $4D7A)."""
    a = G.Asm(G.ORG)
    _skip_head(a)
    a.i("LDA", "abs", MODE).i("AND", "imm", 0x01).i("BNE", "rel", ("L", "two"))
    _skip_site(a, "s0", (0x01, 0x02, 0x03, 0x04), 0x20)
    a.i("JMP", "abs", ("L", "out"))
    a.label("two")
    _skip_site(a, "s1", (0x05, 0x06, 0x07, 0x08), 0x40)
    a.label("out")
    _skip_tail(a)
    return a, _skip_data(4, _skip_sub(4)), 8, {"skip": _skip_spans((a, ("s0", "s1")))}


def _jsr_inline_skip_two_depths():
    """Stage 4: the same callee entered at two stack depths, the corpus spelling.

    ``sp`` never concretizes, so the pops render ``mem[(sp + $01) | $0100]`` and
    the push pair is no constant rts trick: the callee ends ``ret`` and the text
    resumes at the byte after the call."""
    a = G.Asm(G.ORG)
    _skip_head(a)
    a.i("LDA", "abs", MODE).i("AND", "imm", 0x01).i("BNE", "rel", ("L", "two"))
    _skip_site(a, "s0", (0x01, 0x02, 0x03, 0x04), 0x20)
    a.i("JMP", "abs", ("L", "out"))
    a.label("two").i("JSR", "abs", SPMID)
    a.label("out")
    _skip_tail(a)
    mid = G.Asm(SPMID)
    _skip_site(mid, "s1", (0x05, 0x06, 0x07, 0x08), 0x40)
    mid.i("RTS")
    data = _skip_data(4, _skip_sub(4))
    data.update({SPMID + k: b for k, b in enumerate(mid.assemble())})
    return a, data, 8, {"skip": _skip_spans((a, ("s0",)), (mid, ("s1",)))}


def _jsr_inline_skip_varlen():
    """Stage 4: one callee, two sites, the skip length in the first inline byte.

    Three payload bytes at one site and five at the other, so no continuation
    taken from the call is right for both: the skip is the site's, not the
    callee's."""
    a = G.Asm(G.ORG)
    _skip_head(a, 0x01)
    a.i("LDA", "abs", MODE).i("AND", "imm", 0x01).i("BNE", "rel", ("L", "two"))
    _skip_site(a, "s0", (0x03, 0x11, 0x12, 0x13), 0x20)
    a.i("JMP", "abs", ("L", "out"))
    a.label("two")
    _skip_site(a, "s1", (0x05, 0x21, 0x22, 0x23, 0x24, 0x25), 0x40)
    a.label("out")
    _skip_tail(a)
    return a, _skip_data(5, _skip_sub()), 8, {"skip": _skip_spans((a, ("s0", "s1")))}


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
    "follin_jump": _follin_jump,
    "follin_ret_stack": _follin_ret_stack,
    "lone_lane_block_read": _lone_lane_block_read,
    "low_held_cursor": _low_held_cursor,
    "alias_web": _alias_web,
    "call_returned_row": _call_returned_row,
    "computed_rows": _computed_rows,
    "phase_split_reload": _phase_split_reload,
    "shift_divide": _shift_divide,
    "dispatch_scratch": _dispatch_scratch,
    "g2_store": _g2_store,
    "sp_spill": _sp_spill,
    "sp_unbalanced": _sp_unbalanced,
    "sp_loop_edge": _sp_loop_edge,
    "sp_call_at_entry": _sp_call_at_entry,
    "sp_call_displaced": _sp_call_displaced,
    "sp_fix_balance": _sp_fix_balance,
    "sp_scratch_floor": _sp_scratch_floor,
    "jsr_inline_skip": _jsr_inline_skip,
    "jsr_inline_skip_two_sites": _jsr_inline_skip_two_sites,
    "jsr_inline_skip_two_depths": _jsr_inline_skip_two_depths,
    "jsr_inline_skip_varlen": _jsr_inline_skip_varlen,
}

_SKIP_FAMILY = (
    "jsr_inline_skip",
    "jsr_inline_skip_two_sites",
    "jsr_inline_skip_two_depths",
    "jsr_inline_skip_varlen",
)


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_fixture_builds_and_gates(name):
    """Not xfail: the fixtures themselves must stay valid while the lift lands.

    The stage-4 skip family is included since landing 1: a callee that consumes
    its own return slot keeps its raw call and the ret reads the slot back."""
    assert _lift(name).startswith("frameprog 1")


@pytest.mark.xfail(
    reason="%s scratch promotion; %s -- 3d landing 1's read closure already retires the "
    "store, so what stands is the state { } declaration, which frameprog._state_lines "
    "derives from _cells(view) and not from the stores extraction kept" % (_S3, _MEASURED[2]),
    **XFAIL,
)
def test_scratch_cell_is_a_local_not_state():
    text = _lift("scratch")
    assert not re.search(r"\bm_%04X\b" % TMP, text), "scratch cell survives as a named cell"


def test_the_scratch_store_demotes_on_the_unified_path_and_its_declaration_stays():
    """Measured after 3d landing 1: the store is gone from the body, the field is not.

    frameprog re-reads the cell at the SID write and keeps the store; the unified path
    spells the value and retires it, so all that is left of the pin is ``state { }``."""
    assert "sid.v1.ctrl = (m_%04X | $40)" % TMP in _lift("scratch")
    assert "sid.v1.ctrl = ((t0 & $0F) | $40)" in _emit_body("scratch")
    assert not re.search(r"\bm_%04X\b" % TMP, _emit_body("scratch")), "the store survived"
    assert re.search(r"\bm_%04X\b" % TMP, _state_block(_emit("scratch"))), "the field went"


@pytest.mark.xfail(
    reason="%s cursor lift; %s -- rung (f)/(g) mints *ptr[i] inside frameprog.program "
    "and refuses here ('another store may write the pointer'); emit runs no such rung, "
    "so mem[ stands on both paths for the one premise" % (_S3, _MEASURED[1]),
    **XFAIL,
)
def test_pointer_walk_names_no_raw_address():
    text = _lift("pointer_walk")
    body = text[text.index("sub_") :]
    assert "mem[" not in body, "the walk still reads through a raw address"
    assert "carry(" not in body, "the pointer advance still carries between lanes"


@pytest.mark.xfail(
    reason="%s wide compare; %s -- the unified graph spells the borrow as the compare "
    "it is ((ctr0 + $37) < m_1464) where frameprog spells $01 - (zext2(..) <= zext2(..)), "
    "so step 4 XPASSes this pin" % (_S3, _MEASURED[0]),
    **XFAIL,
)
def test_borrow_chain_is_one_wide_compare():
    text = _lift("borrow_chain")
    assert "carry(" not in text, "the borrow chain survives as byte-lane carries"
    assert not re.search(r"\$01 - \(zext2", text), "a borrow survives as compare arithmetic"


def test_borrow_chain_is_one_wide_compare_on_the_unified_path():
    """The pin above, pre-verified: its two assertions hold on ``emit`` today."""
    text = _emit("borrow_chain")
    assert "carry(" not in text, "the borrow chain survives as byte-lane carries"
    assert not re.search(r"\$01 - \(zext2", text), "a borrow survives as compare arithmetic"
    assert "(ctr0 + $37) < m_%04X" % (TGT,) in text, "the sbc-chain normal form went"


@pytest.mark.xfail(
    reason="%s sinks are write-only, no read-back survives; %s -- the unified graph "
    "emits sid.v1.freq_hi = t0 where frameprog widens the lone half to a read-modify-"
    "write of the u16 register, so step 4 XPASSes this pin" % (_S3, _MEASURED[0]),
    **XFAIL,
)
def test_lone_lane_half_owes_no_register_load():
    text = _lift("lone_lane")
    assert not re.search(r"= \(+sid\.", text), "a write-only SID register is read back"


def test_lone_lane_half_owes_no_register_load_on_the_unified_path():
    """The pin above, pre-verified: the write-only price leaves the lane store bare."""
    text = _emit("lone_lane")
    assert not re.search(r"= \(+sid\.", text), "a write-only SID register is read back"
    assert "sid.v1.freq_hi = t0" in text, "the lone lane lost its byte-wide store"


def _body(text):
    return text[text.index("play $") :]


def test_sp_relative_spill_leaves_no_stack_pointer():
    """Phase 1 (LANDED): the spill is a local and no procedure threads sp."""
    assert not re.search(r"\bsp\b", _body(_lift("sp_spill"))), "the stack pointer survived"


def test_an_unbalanced_procedure_keeps_its_stack_pointer():
    """Invariant: an unproven stack effect keeps sp; the fixpoint may not touch it.

    The ret of such a procedure reads page one for its target, so a promoted
    slot would delete the byte the machine returns through."""
    assert re.search(r"\bsp\b", _body(_lift("sp_unbalanced"))), "an unbalanced procedure lost sp"
    assert _sp_classes("sp_unbalanced") == ["sp_unbalanced"]


def test_a_loop_edge_at_a_displacement_keeps_the_stack_pointer():
    """Invariant, and 2c's own measured bound: an interior edge is not relaxable.

    The procedure is stack-balanced (PHA .. PLA) and refused because its back edge
    stands at the displacement. Relaxing that is what 2c withdrew: a dispatch arm
    or a label may be entered by a jump no list enumerates (8 tunes diverged)."""
    text = _lift("sp_loop_edge")
    assert re.search(r"\bsp\b", _body(text)), "the kept spill lost sp"
    assert "$0100" in text, "the spill lost its stack-page identity"
    assert _sp_classes("sp_loop_edge") == ["sp_unbalanced"]


def test_a_raw_call_at_the_entry_displacement_drops_its_linkage():
    """Landed 2c: the machine's pushed return does not move, so sp leaves."""
    body = _body(_lift("sp_call_at_entry"))
    assert "call $" in body, "the fixture lost the raw call it pins"
    assert not re.search(r"\bsp\b", body), "the stack pointer survived"


def test_a_displaced_raw_call_keeps_the_stack_pointer():
    """Invariant (2c): dropping the displacement would move the pushed return."""
    body = _body(_lift("sp_call_displaced"))
    assert "call $" in body, "the fixture lost the raw call it pins"
    assert re.search(r"\bsp\b", body), "a displaced raw call lost sp"
    assert _sp_classes("sp_call_displaced") == ["sp_linked"]


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


_DEREF_REFUSAL = {  # fixture -> the rung (f) premise its *ptr[i] lift states
    "pointer_walk": "another store may write the pointer",
    "mux_pair": "another store may write the pointer",
    "cursor_save": "a definition is not a lo/hi partner-table entry read",
    "writethrough": "a store at an unproven address may write the pointer",
}
_DEREF_WHY = "%s -- rung (f)'s premise, %%r, and emit mints no *ptr[i] at all" % _MEASURED[1]


@pytest.mark.xfail(
    reason="%s multiplexed pair splits per role; %s"
    % (_S3, _DEREF_WHY % _DEREF_REFUSAL["mux_pair"]),
    **XFAIL,
)
def test_mux_pair_certifies_the_pointer_role():
    text = _lift("mux_pair")
    body = text[text.index("sub_") :]
    assert "mem[" not in body, "the pointer role still reads through a raw address"


@pytest.mark.xfail(
    reason="%s cursor values as data; %s" % (_S3, _DEREF_WHY % _DEREF_REFUSAL["cursor_save"]),
    **XFAIL,
)
def test_cursor_save_restore_lifts_to_cursor_values():
    text = _lift("cursor_save")
    body = text[text.index("sub_") :]
    assert "mem[" not in body, "the saved/restored cursor still derefs a raw address"


@pytest.mark.xfail(
    reason="%s write-through becomes a table write; %s"
    % (_S3, _DEREF_WHY % _DEREF_REFUSAL["writethrough"]),
    **XFAIL,
)
def test_writethrough_store_becomes_a_bounded_table_write():
    text = _lift("writethrough")
    body = text[text.index("sub_") :]
    assert not re.search(r"^\s*mem\[.*\] = ", body, re.M), "the store still writes through top"


@pytest.mark.parametrize("name", sorted(_DEREF_REFUSAL))
def test_a_refused_deref_keeps_its_raw_address_on_the_unified_path(name):
    """The four ``mem[`` pins measured together: the refusal is the rung's, on both paths.

    Each names the premise rung (f) states; the unified emitter has no deref rung, so its
    text carries the same raw address and the cutover cannot move any of them."""
    _lift(name)
    why = [p.lemma for p in _lift_prog[name].proofs if p.kind == "deref"]
    assert any(_DEREF_REFUSAL[name] in w for w in why), why
    assert "mem[" in _emit_body(name), "the unified path already named the row"


def _fused_cursor(name):
    """True where rung (d) declared the walked pair one ``u16`` field, not two lanes.

    ``framefuse.refusal()`` reads one surviving byte-lane read of the pair as refusing
    its whole tune-wide declaration, even where an equal word read stands beside it: it
    is that read, not the second destination or the store order, that discriminates."""
    return "ptr_%04X: u16" % G.PTR in _state_block(_lift(name))


_FUSE_WHY = (
    "%s -- rung (d)/framefuse decides the tune-wide declaration on the statements before "
    "any emitter runs, and emit reuses frameprog._state_lines' unfused block, so the green "
    "plain_advance control reads byte-wise there too" % _MEASURED[1]
)


def _fuse_pin(why):
    """A rung-(d) pin's mark: its own refusal class beside the measured step 4 verdict."""
    return pytest.mark.xfail(reason="%s %s; %s" % (_S3, why, _FUSE_WHY), **XFAIL)


def test_a_plain_advance_fuses_its_cursor_pair():
    """Invariant: the baseline the dual-destination family is measured against."""
    assert _fused_cursor("plain_advance"), "a bare in-place advance left the pair byte-wise"


def test_the_unified_emitter_carries_no_rung_built_pair_declaration():
    """The fused-cursor family's disposition, measured on the control it is measured against.

    ``plain_advance`` fuses under rung (d) and ``emit`` still spells its lanes, so every
    ``_fused_cursor`` pin reads False there for want of framefuse and not of a rule; step
    4's splice keeps the rungs as the substrate builder."""
    assert _fused_cursor("plain_advance")
    block = _state_block(_emit("plain_advance"))
    assert "ptr_%04X: u16" % G.PTR not in block
    assert "ptr_%04X_lo: u8" % G.PTR in block and "ptr_%04X_hi: u8" % G.PTR in block


def test_the_rung_built_statements_convert_for_the_unified_emitter():
    """Cutover order (LANDED): rung (d2)'s narrowing ``COPY`` is a term the converter has.

    ``dual_store_lo_only``'s lo-lane save copy is ``COPY`` of the fused u16 local at width
    one; ``eqlift.trunc`` is ``zext``'s dual, so splicing frameprog's own statements into
    ``render_proc`` reaches the rules instead of faulting."""
    _lift("dual_store_lo_only")
    (stmts,) = [s for _e, _p, _r, s in _lift_prog["dual_store_lo_only"].procs]
    assert eqlift_mem.render_proc(stmts), "the spliced statements rendered nothing"


def test_a_word_copy_of_the_advanced_cursor_leaves_it_fused():
    """Invariant: a second destination is no refusal where every lane read folds to a word.

    The save copy reads the pair after the advance, so rung (d2) merges both the
    advance and the copy; no byte-lane read of the pair survives to refuse it."""
    assert _fused_cursor("dual_store_word_copy"), "a word-wide save copy refused the pair"


@_fuse_pin("a dual-destination advance keeps the pair byte-wise")
def test_dual_store_advance_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_advance")


@_fuse_pin("store order within a lane does not free the pair")
def test_dual_store_pair_first_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_pair_first")


@_fuse_pin("uninterleaved pair stores still keep it byte-wise")
def test_dual_store_via_regs_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_via_regs")


@_fuse_pin("a hi-first dual-destination advance keeps the pair byte-wise")
def test_dual_store_hi_first_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_hi_first")


@_fuse_pin("a cell-stepped dual store keeps it byte-wise")
def test_dual_store_computed_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_computed")


@_fuse_pin("a lo-only dual store refuses certification, not just naming")
def test_dual_store_lo_only_fuses_its_cursor_pair():
    """One lane's copy is enough: rung (g) then refuses ``def_unliftable``, not the spelling.

    ``ptrcert._def_refusal`` reads the role, so the hi lane refuses as ``computed`` though
    both lanes extract one named word -- a rule gap the corpus never exhibits (0 webs; the
    nearest 2 are lo-lane advances spelled through the word), so it is no lift to recover."""
    assert _fused_cursor("dual_store_lo_only")


@_fuse_pin("a stack-spilled cursor has no word form to appeal to")
def test_stack_spill_cursor_fuses_its_cursor_pair():
    """The largest group (15 webs), and the one no proven-word-form fix can reach.

    Corpus-wide the 164 lone-half reads are 130 bare copies to 33 ``INT_ADD``, sinking to
    ``asg`` (96) and ``st`` (70) and to no ``if`` at all: not one is a page-alignment test
    or an end-of-block compare, so the byte-wise evidence is spill and carry residue."""
    assert _fused_cursor("stack_spill_cursor")


@_fuse_pin("an unobserved carry arm leaves the hi lane unpaired")
def test_deferred_carry_cursor_fuses_its_cursor_pair():
    """The hi lane is in the code but not the text, so no store pairs with the lo one."""
    assert _fused_cursor("deferred_carry_cursor")


@_fuse_pin("a split lo/hi save-back destination cannot pair")
def test_table_spill_cursor_fuses_its_cursor_pair():
    """The advance is already one u16 store; the de-interleaved save-back is what refuses."""
    assert _fused_cursor("table_spill_cursor")


@_fuse_pin("interleaved half stores never pair, so the pair refuses")
def test_unpaired_half_store_fuses_its_cursor_pair():
    """The one rung (d) class no other fixture reaches: no lane read, the stores unpaired.

    ``framefuse.refusal()`` reports the first failing premise only, testing ``lone``
    before ``unpaired``, so a fixture's stated class is the first that fails and not
    necessarily the only one - the classes are ordered, not disjoint."""
    assert _fused_cursor("unpaired_half_store")


def test_an_inpage_advance_is_never_fused():
    """Invariant: a bare INC lane with no carry arm never fuses to the wide add (8 of 74).

    lo = (lo+k) mod 256 with hi untouched diverges from +:2 at every lane wrap
    ($14FF -> $1400, not $1500). No operator is missing: hi is a constant page
    selector, so the honest lift is a u8 offset into the page block, never a u16."""
    assert not _fused_cursor("inpage_advance"), "a byte-wise pair was widened to u16"


@pytest.mark.xfail(
    reason="%s INT_ADD store bound via intervals; %s -- the bound exists in the graph "
    "(mem_rules' lattice states ($00A5, $01A4) here) and the pin reads frameproc."
    "addr_bits, which states top: addr_interval seeds the graph FROM the bit analysis "
    "and never back" % (_S3, _MEASURED[1]),
    **XFAIL,
)
def test_g2_bounds_the_zext_add_store():
    _lift("g2_store")
    bad = [b for b in _unnamed_store_bounds(_lift_prog["g2_store"]) if b > 0x01FF]
    assert not bad, "a (zext2(y) + $NN) store is still counted top-wide"


def test_the_g2_store_is_bounded_by_the_unified_interval():
    """The pin's goal measured on the unified path: ``mem_rules`` states the interval.

    Both sides stand here, so what the pin still pins is the direction the bridge is
    missing and not an absent bound."""
    _lift("g2_store")
    (addr, defs), *rest = _unnamed_stores(_lift_prog["g2_store"])
    assert not rest and frameproc.addr_bits(addr, defs) > 0x01FF
    assert eqlift_mem._lattice(addr) == (0x00A5, 0x01A4)


def test_the_script_jump_is_fused_and_lift_eligible():
    """Control: Follin's jump op is no lift refusal - the pair fuses, (ii) admits.

    The fused def classifies ``computed`` (the block_read shape is a byte-lane
    spelling), so b3's enumeration must key on the fused form too."""
    assert _fused_cursor("follin_jump"), "the script-jump pair went byte-wise"
    rec = _cert("follin_jump")
    assert rec["eligible"] and not rec["lift_refusals"]


def test_the_script_jump_certifies():
    """2b (b3) LANDED: the jump operand is a block read, and the fixpoint closes on PAT.

    E0 is the post-init block; the only word in it that the registry declares is the
    operand aiming the cursor back at PAT, so one round reaches the fixpoint."""
    rec = _cert("follin_jump")
    assert not rec["refusals"], rec["lemma"]
    assert rec["kinds"]["block_read"] == 1 and rec["reach"] == ["$%04X" % PAT]
    assert not rec["mutable_blocks"] and rec["read_roots"] == ["$%04X" % G.PTR]


def test_the_enumeration_keys_on_the_fused_block_read():
    """b3's keying note, executable: rung (d)'s pack is what the shape recognizer sees.

    Ghouls $6AD0 fuses, so the def wears shape ``computed`` and kind ``block_read`` --
    an enumeration keyed on the ``block_read`` *shape* alone would never see it."""
    (d,) = [d for d in _cert("follin_jump")["defs"] if d["kind"] == "block_read"]
    assert d["shape"] == "computed" and d["role"] == "word"


def test_a_lone_lane_block_read_has_no_word_to_enumerate():
    """b3: the hi lane is read out of the block while the lo lane steps on its own.

    The enumeration keys on 16-bit LE values, so a lane whose partner is not the byte
    beside it names half a pointer: the defs close and the extent stays open."""
    rec = _cert("lone_lane_block_read")
    assert rec["kinds"]["block_read"] == 1 and rec["block_rooted"]
    assert rec["refusals"] == ["ptr_extent_open"], rec["lemma"]
    assert any("adjacent partner" in n for n in rec["notes"]), rec["notes"]


def test_the_ret_stack_is_fused_and_lift_eligible():
    """Control: the depth-indexed split-column call stack is no lift refusal."""
    assert _fused_cursor("follin_ret_stack"), "the ret-stack pair went byte-wise"
    rec = _cert("follin_ret_stack")
    assert rec["eligible"] and not rec["lift_refusals"]


def test_the_ret_stack_certifies():
    """2b (b3) LANDED: the split call-stack columns are a declared pair play writes.

    Ghouls $6ADD/$6B42: the row is a cursor value stored as data, so the web closes
    over the columns -- and because play writes them, ``mem0``'s words are not their
    rows, which is the whole content of ``extent_mutable``."""
    rec = _cert("follin_ret_stack")
    assert set(rec["refusals"]) <= {"extent_mutable"}
    assert rec["mutable_blocks"] == ["$%04X" % STK, "$%04X" % (STK + 3)]
    assert rec["kinds"]["other"] == 0 and rec["block_rooted"]


def test_the_extent_guard_compares_the_fixpoint_with_what_the_run_saw():
    """b3 against b0, the divergence guard: equality certifies and a gap is ledgered.

    ``--close`` is the second licence: a run that reached recurrence stands for the
    infinite one, so its observed extent is the extent whatever the fixpoint named."""
    _lift("follin_jump")
    prog, root = _lift_prog["follin_jump"], "$%04X" % G.PTR

    def one(observed, closed=False):
        (rec,) = [r for r in ptrcert.certify(prog, observed, closed)[0] if r["root"] == root]
        return rec

    same = one({G.PTR: {PAT}})
    assert same["extent_certified"] and not same["extent_short"]
    more = one({G.PTR: {PAT, PAT2}})
    assert not more["extent_certified"] and more["extent_short"] == ["$%04X" % PAT2]
    assert one({G.PTR: {PAT, PAT2}}, closed=True)["extent_certified"]
    assert not one(None)["extent_certified"], "no observation is no certificate"


def test_a_play_written_source_block_stops_the_certification_only():
    """Invariant (b3): ``extent_mutable`` is accounting -- the lift and the guard stand."""
    rec = _cert("follin_ret_stack")
    assert rec["eligible"] and not rec["lift_refusals"]


def test_a_stack_held_cursor_refuses_low_held():
    """Invariant (b1 iv): the restore reads page one through sp, and sp survives."""
    rec = _cert("low_held_cursor")
    assert rec["lift_refusals"] == ["low_held"] and not rec["eligible"]
    assert re.search(r"\bsp\b", _body(_lift("low_held_cursor"))), "the hold lost its sp spelling"


@pytest.mark.xfail(
    reason="%s the deref bound the certification cannot give; %s -- ptrcert reads the "
    "program, and the unified path declines the same forward (the pull still spells "
    "mem[t0]), so the deref-span quantity stage 3d landing 1's read closure computes is "
    "the missing premise and its consumer is a rung" % (_S3, _MEASURED[1]),
    **XFAIL,
)
def test_a_stack_held_cursor_lifts_once_the_deref_is_bounded():
    """2c measured the blocker and it is neither of 2c's rules (§2 2c correction 5).

    The linkage drops and the balance is proven; rung (d0s) still refuses both
    slots because the `(ptr),y` deref between push and pull may alias them, and
    the pair's own extent is refused because it is held through page one."""
    assert _cert("low_held_cursor")["eligible"]


def test_an_unresolvable_store_refuses_the_web_and_keeps_the_spelling():
    """Invariant (b1 i): the one soundness premise; renaming under it would be wrong."""
    rec = _cert("alias_web")
    assert rec["lift_refusals"] == ["web_alias"] and not rec["eligible"]
    assert "mem[" in _body(_lift("alias_web")), "the refused web lost its machine spelling"
    assert _walk("alias_web")["web_alias"] == {"top_memory": 1}, "2.5: the index is a cell"


def test_a_call_returned_row_is_no_lift_refusal():
    """Control: a def crossing a call boundary refuses certification, never the lift."""
    assert _fused_cursor("call_returned_row")
    rec = _cert("call_returned_row")
    assert rec["eligible"] and not rec["lift_refusals"]
    assert rec["refusals"] == ["ptr_uncertified"]


def test_computed_rows_walk_off_the_registry():
    """Invariant (b0): b1-eligible, and the observed rows land in no declared datum."""
    rec = _cert("computed_rows")
    assert rec["eligible"] and not rec["lift_refusals"]
    row = _observed("computed_rows")
    assert row["refusals"] == ["extent_unmappable"] and row["unmappable_foreign"]
    assert _walk("computed_rows", [row])["extent_foreign"] == {"top_memory": 1}, "2.5: P6 stands"


@pytest.mark.xfail(
    reason="%s an arithmetic row stays guarded; %s -- the memory sort was the other "
    "branch of this reason and it does not resolve the row: mem_rules' lattice states "
    "nothing for ((ctr & $01) << $03) | $80 (INT_OR is in no interval rule), and the "
    "consumer is ptrextent either way" % (_S3, _MEASURED[1]),
    **XFAIL,
)
def test_computed_rows_map():
    """b3 measured this and cannot reach it: the row is arithmetic, not a registry read.

    The fixpoint walks declared data for 16-bit LE words; a row built as
    ``((ctr & 1) << 3) | $80`` is in no block, so only a value-set walker derives it."""
    assert not _observed("computed_rows")["refusals"]


def test_the_memory_sort_states_no_interval_for_the_computed_row():
    """The guarded refusal's own alternative, measured: ``INT_OR`` is in no interval rule.

    Phase 2.5's strided-interval walker stays the named mechanism, as the green
    ``extent_foreign`` row beside this one already records."""
    _lift("computed_rows")
    (row,) = [r for r in _stores_to(_lift_prog["computed_rows"], G.PTR) if r[1] == "INT_OR"]
    assert _ops(row, "INT_OR") == [None, None], "INT_OR gained an interval rule"
    assert all(_ops(row, "INT_LEFT")), "the lane's shift chain lost its bound"


def test_the_computed_row_is_no_block_read():
    """b3's own boundary, stated: the arithmetic row names no source block to enumerate."""
    rec = _cert("computed_rows")
    assert rec["kinds"]["block_read"] == 0 and not rec["read_blocks"] and not rec["read_roots"]
    assert rec["refusals"] == ["ptr_uncertified"], rec["lemma"]


def test_the_smc_operand_dispatch_is_a_join_not_a_wall():
    """Control (R8): the Follin dispatch shape emits switch goto, no raw dyn form."""
    body = _body(_lift("dispatch_scratch"))
    assert "switch goto" in body, "the dispatch fell out of the observed-target closure"
    assert "dgoto" not in body and "igoto" not in body


def test_the_unified_emitter_spells_the_dispatch_header_the_dialect_spells():
    """Cutover order, MOVED: the header names its dispatch kind on both paths.

    It read ``switch {`` for every kind until the step-4 splice measured it against the
    dialect; the arms and the ``goto`` over the patched operand always stood on both."""
    body = _emit_body("dispatch_scratch")
    assert "switch goto {" in body
    assert "dgoto" not in body and "igoto" not in body
    assert body.count("case $%04X" % HND0) == 1 and body.count("case $%04X" % HND1) == 1


@pytest.mark.xfail(
    reason="%s written-before-read joins over a dispatch; %s -- the arms are swc labels, "
    "which 3d landing 1's in-edge join excludes by design, so each handler reads the cell "
    "where the straight-line scratch fixture now reads the value" % (_S3, _MEASURED[2]),
    **XFAIL,
)
def test_dispatch_scratch_promotes():
    assert not re.search(r"\bm_%04X\b" % TMP, _lift("dispatch_scratch"))


def test_the_dispatch_arms_do_not_join_the_scratch_write():
    """The dispatch pin's mechanism, measured: an ``swc`` label resets where a goto joins.

    Both handlers read the cell on the unified path, so extending the in-edge join to the
    dispatch's own arms is the whole of what this pin waits on."""
    body = _emit_body("dispatch_scratch")
    assert "m_%04X = (t0 & $0F)" % TMP in body, "the dispatch store already demoted"
    assert body.count("sid.v1.ctrl = (m_%04X | $" % TMP) == 2, "an arm forwarded the value"


@_fuse_pin("a cross-frame lane reload is a masked word update")
def test_a_phase_split_reload_fuses_its_cursor_pair():
    """Each half store is a lane replacement - (ptr & $FF00) | zext2(row) - so no
    carry and no new operator is involved; only the lane-update spelling is missing."""
    assert _fused_cursor("phase_split_reload")


@_fuse_pin("a loop-carried LSR/ROR pair is one wide variable shift")
def test_a_shift_divide_lifts_to_a_wide_shift():
    """The dialect has >>; what is missing is the loop-to-expression rule, not an operator.

    Unlike ``inpage_advance`` this fusion is semantically permitted: the loop is a
    power-of-two divide of one 16-bit value, so the strict xfail is the whole pin -
    it fails while the pair is byte-wise and XPASSes the day a loop-level rule lands."""
    assert _fused_cursor("shift_divide")


def test_an_entry_balanced_procedure_destacks():
    """Landed 2c: the interior nonzero displacement is a label edge, not an imbalance."""
    assert not re.search(r"\bsp\b", _body(_lift("sp_fix_balance")))


@pytest.mark.xfail(
    reason="%s addr_floor keeps the kept push off zero page; %s -- emit's raw _Builder "
    "procedures keep the raw call frameprog promotes to a register interface, and the "
    "chain havocs at its landing label, so the cell survives for want of the promotion "
    "and not for want of the floor" % (_S3, _MEASURED[2]),
    **XFAIL,
)
def test_scratch_beside_kept_sp_fabric_promotes():
    assert not re.search(r"\bzp_%02X\b" % ZTMP, _lift("sp_scratch_floor"))


def test_a_raw_call_holds_the_scratch_cell_the_promoted_call_would_free():
    """Cutover order: what the unified path lacks here is the substrate, not the floor.

    The same fixture's cell survives beside a raw ``call`` where frameprog threads a
    procedure with a register interface, so step 4's splice order decides this one."""
    assert "sub_%04X(" % SPSUB in _body(_lift("sp_scratch_floor")), "the pcall went"
    body = _emit_body("sp_scratch_floor")
    assert "call $%04X" % SPSUB in body, "the raw path promoted the call after all"
    assert "sid.v1.ctrl = (zp_%02X | $21)" % ZTMP in body, "the read forwarded after all"


def test_every_stage_three_pin_carries_its_measured_disposition():
    """Stage 3's convergence bullet, enforced: no pin may assume it flips at the cutover.

    A stage-3 xfail added later fails here until its goal property has been evaluated
    against ``_emit`` and one of the three verdicts put on its reason."""
    pins = {
        n: m.kwargs["reason"]
        for n, f in sorted(globals().items())
        for m in getattr(f, "pytestmark", ())
        if m.name == "xfail" and m.kwargs.get("reason", "").startswith(_S3)
    }
    assert len(pins) == 24, sorted(pins)
    assert not [n for n, r in pins.items() if sum(v in r for v in _MEASURED) != 1]


@pytest.mark.parametrize("name", _SKIP_FAMILY)
def test_the_machine_skips_the_inline_data(name):
    """The machine's half of the trick, and the model already has it right.

    Every inline byte is an observed read of the callee's copy and none is a
    code seat; the run resumes past the data. That is why the fault below is a
    lift defect and not the claim boundary."""
    model, _frames, _prog = _build(name)
    seats, read = set(model.pcs), {a for _pc, a in model.reads}
    for lo, hi in _lift_extra[name]["skip"]:
        span = set(range(lo, hi))
        assert span and not span & seats, "an inline data byte was decoded as code"
        assert span <= read, "an inline data byte was never read as data"
        assert hi in seats, "the byte past the data is no code seat"


def test_a_single_site_inline_skip_lifts_through_its_return_slot():
    """Control (LANDED): at one call site the trick already lifts and gates.

    The lone raw ``call`` writes the return slot at a concrete stack cell, so
    the callee's rewrite of it is the constant push pair 7.9 reads and
    ``framestack.lift_rts_trick`` gives it the goto the machine takes."""
    assert _gate("jsr_inline_skip") is None


def test_a_shared_inline_data_callee_evaluates_through_the_skip():
    """#155's shape with the callee shared, which is what makes it a procedure.

    The promotion is refused (``frameproc.slot_reader``), so the raw call writes
    the return slot the callee steps and ``lift_rts_trick`` reads the goto off it."""
    assert _gate("jsr_inline_skip_two_sites") is None
    body = _body(_lift("jsr_inline_skip_two_sites"))
    assert not re.search(r"\n\s+sub_%04X\(" % SPSUB, body), "the slot reader promoted"
    assert body.count("call $%04X ret " % SPSUB) == 2, "the raw call carries no return slot"
    assert "goto ((((zext2(t1) << $08):2 | zext2(ptr_0004_lo)):2 + $0001):2)" in body


def test_a_two_depth_inline_data_callee_evaluates_through_the_skip():
    """The corpus spelling exactly, and the corpus fix exactly.

    Entered at two depths ``sp`` never concretizes and no constant trick lifts, so
    the ``ret`` is what carries it: the slot the callee rewrote, not the successor."""
    assert _gate("jsr_inline_skip_two_depths") is None
    body = _body(_lift("jsr_inline_skip_two_depths"))
    assert not re.search(r"\n\s+sub_%04X\(" % SPSUB, body), "the slot reader promoted"
    assert "mem[(zext2((sp - $01)) | $0100):2] = a" in body, "the slot rewrite went"
    assert body.count("call $%04X ret " % SPSUB) == 2, "the raw call carries no return slot"


def test_a_per_site_inline_data_length_evaluates_through_the_skip():
    """The variation: the skip length is the site's, so no per-callee answer exists.

    Three payload bytes at one site and five at the other, the count in the inline
    byte before them; the slot the callee wrote is the only continuation that fits."""
    assert _gate("jsr_inline_skip_varlen") is None
