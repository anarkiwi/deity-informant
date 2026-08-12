"""The register-model shredder (docs/register-model-lift-impl.md).

Each fixture forces one register-model artifact and stays ``xfail(strict=True)``
until its stage lands. ``_lift`` is the artifact since the step-4 switch, so a pin
whose goal property held would XPASS here; what each reason still owes is the
measured refusal and the live owner that will move it -- one of ``_OWNERS``."""

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
_OWNERS = (  # the live owner a stage-3 reason names; the disposition is the strict mark
    "owner: rung (f)",  # frameptr._Ptr: the deref premise and the writer set it reads
    "owner: rung (d)",  # framefuse: the pair premise and the SID write-only guard, both landed
    "owner: framestack",  # the sp-relative slot identity a machine-stack hold needs
    "owner: frameproc",  # the bit analyses, their reach, and the promotion they refuse
    "owner: datadecl",  # the registry, and the via: discovery that grows it
    "owner: eqlift_mem",  # render_block's wall: what a call retires of the locals
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
    return [frameproc.addr_reach(a, defs)[1] for a, defs in _unnamed_stores(prog)]


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


def test_scratch_cell_is_a_local_not_state():
    """FLIPPED at stage 4 landing 3: the store retires and its declaration goes with it.

    3d landing 1's read closure retired the store; the ``state { }`` demotion is what
    took the field, off root extraction's own scratch spans and nothing else."""
    text = _lift("scratch")
    assert not re.search(r"\bm_%04X\b" % TMP, text), "scratch cell survives as a named cell"
    procs = text[text.index("sub_") :]
    assert "sid.v1.ctrl = ((t0 & $0F) | $40)" in procs, "the value lost its spelling"
    assert not re.search(r"\bm_%04X\b" % TMP, _state_block(text)), "the field stayed"


@pytest.mark.xfail(
    reason="%s cursor lift; %s -- rung (f) refuses 'another store may write the pointer', "
    "and the stores that hit the pair are the pair's OWN byte-lane reload stores at $0002 "
    "and $0003 (measured: no wild store, no third writer). _hit excepts only an exact word "
    "store and _writers records only width-2 stores as definitions, so the declared lo/hi "
    "reload row is invisible to both" % (_S3, _OWNERS[0]),
    **XFAIL,
)
def test_pointer_walk_names_no_raw_address():
    text = _lift("pointer_walk")
    body = text[text.index("sub_") :]
    assert "mem[" not in body, "the walk still reads through a raw address"
    assert "carry(" not in body, "the pointer advance still carries between lanes"


def test_borrow_chain_is_one_wide_compare():
    """FLIPPED at stage 4 landing 3's switch: the artifact spells the borrow as a compare.

    The pin was raised at stage 3 against ``render_lines``' ``$01 - (zext2(..) <= ..)``;
    the unified graph is what emits now, so the wide compare is an artifact fact."""
    text = _lift("borrow_chain")
    assert "carry(" not in text, "the borrow chain survives as byte-lane carries"
    assert not re.search(r"\$01 - \(zext2", text), "a borrow survives as compare arithmetic"


def test_borrow_chain_is_one_wide_compare_on_the_unified_path():
    """The pin above, verified on both unified texts: ``emit`` today and the cutover's.

    ``_lift`` is the one that decides the disposition, because it renders the
    rung-built statements the cutover hands the graph."""
    for text in (_emit("borrow_chain"), _lift("borrow_chain")):
        assert "carry(" not in text, "the borrow chain survives as byte-lane carries"
        assert not re.search(r"\$01 - \(zext2", text), "a borrow survives as compare arithmetic"
    assert "(ctr0 + $37) < m_%04X" % (TGT,) in _emit("borrow_chain"), "the normal form went"


def test_lone_lane_half_owes_no_register_load():
    """FLIPPED at the widening guard: $D400-$D416 is write-only, so no word is completed.

    Widening a lone half is a read-modify-write of the register, and inside the window
    there is nothing to read; the half stays the byte store it is at the register file."""
    text = _lift("lone_lane")
    assert not re.search(r"= \(+sid\.", text), "a write-only SID register is read back"
    assert "sid.v1.freq_hi = t0" in _body(text), "the lone lane lost its byte-wide store"


def test_lone_lane_half_owes_no_register_load_on_the_unified_path():
    """The two unified texts agree now, and that is what the guard closed.

    ``emit`` renders the bare byte store off a raw ``_Builder`` procedure and the cutover's
    emitter is handed the same byte store, because the widening rung refuses the window."""
    for text in (_emit("lone_lane"), _lift("lone_lane")):
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
    "pointer_walk": "a definition is not a lo/hi partner-table entry read",
    "mux_pair": "a definition is not a lo/hi partner-table entry read",
    "cursor_save": "a definition is not a lo/hi partner-table entry read",
    "writethrough": "a store at an unproven address may write the pointer",
}
_DEREF_WHY = (
    "%s -- rung (f) states the premise %%r. Rung (d)'s per-site premise paid the writer "
    "set: the pair's own byte-lane reload row is a width-2 lane update now, so pointer_walk "
    "and mux_pair moved off the third-writer refusal onto the definition premise, and what "
    "_writers still owes is a definition that is a partner-table entry read (writethrough's "
    "deref store wants frameptr._span off the declared const table's word set)" % _OWNERS[0]
)


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

    LANDED: the premise is per access site, so a lone half is spelled through the
    word -- a store as ``(w & $FF00) | zext2(v)``, a read as that lane's trunc --
    and neither a second destination nor the store order discriminates any more."""
    return re.search(r"ptr_%04X: (?:\w+ )?u16" % G.PTR, _state_block(_lift(name))) is not None


def _lane_read(name):
    """The pair's surviving byte-lane reads are the fused word's own truncs."""
    body = _body(_lift(name))
    return "trunc1(ptr_%04X:2)" % G.PTR in body or "trunc1((ptr_%04X:2 >> $08)" % G.PTR in body


def _lane_update(name, cell):
    """A lone half store is the word's lane update, not a byte store at the cell."""
    return "ptr_%04X:2 = ((ptr_%04X:2 & $" % (cell, cell) in _body(_lift(name))


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
    assert re.search(r"ptr_%04X_lo: (?:\w+ )?u8" % G.PTR, block)
    assert re.search(r"ptr_%04X_hi: (?:\w+ )?u8" % G.PTR, block)


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


def test_dual_store_advance_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_advance")


def test_dual_store_pair_first_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_pair_first")


def test_dual_store_via_regs_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_via_regs")


def test_dual_store_hi_first_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_hi_first")


def test_dual_store_computed_fuses_its_cursor_pair():
    assert _fused_cursor("dual_store_computed")


def test_dual_store_lo_only_fuses_its_cursor_pair():
    """One lane's copy is enough: the surviving lo read is the word's own trunc.

    ``ptrcert._def_refusal`` reads the role, so rung (g) still refuses the hi lane as
    ``computed`` -- a rule gap the corpus never exhibits (0 webs) and no part of this
    pair's declaration, which is what the premise decides."""
    assert _fused_cursor("dual_store_lo_only")
    assert _lane_read("dual_store_lo_only"), "the surviving lo read kept its byte cell"


def test_stack_spill_cursor_fuses_its_cursor_pair():
    """The largest group (15 webs): a spill has no word form and needs none.

    Corpus-wide the 164 lone-half reads are 130 bare copies to 33 ``INT_ADD``, sinking to
    ``asg`` (96) and ``st`` (70) and to no ``if`` at all: spill and carry residue, each
    of which the word's own trunc spells without a word form to appeal to."""
    assert _fused_cursor("stack_spill_cursor")
    assert _lane_read("stack_spill_cursor"), "the spilled halves kept their byte cells"


def test_deferred_carry_cursor_fuses_its_cursor_pair():
    """The hi lane is in the code but not the text, so the lo store is the lane update.

    The unobserved carry arm is the wall that leaves the hi lane open, which is what
    parts this fixture from ``inpage_advance``'s proven page selector below."""
    assert _fused_cursor("deferred_carry_cursor")
    assert _lane_update("deferred_carry_cursor", G.PTR), "the lone lo store stayed byte-wide"


def test_table_spill_cursor_fuses_its_cursor_pair():
    """The advance is already one u16 store, and the de-interleaved save-back becomes one.

    The two lane stores meet at the partner table's own row once the pair is a word, so
    the fixture that had no pairing seat ends with no lane spelling left at all."""
    assert _fused_cursor("table_spill_cursor")
    assert "m_%04X[x]:2 = ptr_%04X:2" % (G.TBL, G.PTR) in _body(_lift("table_spill_cursor"))


def test_unpaired_half_store_fuses_its_cursor_pair():
    """The one rung (d) class no other fixture reaches: no lane read, the stores unpaired.

    Neither half meets a partner at a seat, so each is its own lane update and the pair
    is one ``u16`` all the same."""
    assert _fused_cursor("unpaired_half_store")
    body = _body(_lift("unpaired_half_store"))
    assert body.count("ptr_%04X:2 = ((ptr_%04X:2 & $" % (G.PTR, G.PTR)) == 2


def test_an_inpage_advance_is_never_fused():
    """Invariant: a bare INC lane with no carry arm stays two bytes (8 of 74 webs).

    The premise is now the fact the docstring used to assume: ``_page_fixed`` proves no
    path changes the hi lane -- every store writes the byte it already holds and no wall
    hides one -- so a lo lane advanced in place is an index into one page, not a word."""
    assert not _fused_cursor("inpage_advance"), "a byte-wise pair was widened to u16"
    (pr,) = [p for p in _lift_prog["inpage_advance"].proofs if p.kind == "pointer"]
    assert "in-place lane advance(s) under a hi lane no path changes" in pr.lemma


def test_g2_bounds_the_zext_add_store():
    """FLIPPED at the reach reading: the tighter of the two bounds is the store's.

    ``frameproc.lattice`` is the rule half and ``addr_floor``/``addr_bits`` the bit half;
    each is sound alone, so a store no base/index form names is bounded by the min."""
    _lift("g2_store")
    bad = [b for b in _unnamed_store_bounds(_lift_prog["g2_store"]) if b > 0x01FF]
    assert not bad, "a (zext2(y) + $NN) store is still counted top-wide"


def test_the_g2_store_bound_is_the_lattice_s_and_not_the_bits():
    """Why ``addr_bits`` may not take the min itself: it recurses through ``INT_OR``.

    A magnitude bound is unsound under that recursion (the mask is what composes), so the
    two readings stay apart and only ``reach`` joins them."""
    _lift("g2_store")
    (addr, defs), *rest = _unnamed_stores(_lift_prog["g2_store"])
    assert not rest and frameproc.addr_bits(addr, defs) > 0x01FF
    assert frameproc.lattice(addr) == (0x00A5, 0x01A4) == eqlift_mem._lattice(addr)
    assert frameproc.addr_reach(addr, defs) == (0x00A5, 0x01A4)


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


def test_a_stack_held_cursor_names_its_slot_and_keeps_its_store():
    """Invariant (b1 iv): the identity is sp-relative, and sp survives with the store.

    The deref between push and pull reads an address no analysis resolves, so it may
    not move the slot's value; what it forbids is dropping the store, which is why the
    cell stays and the pulls read the local the push defined -- as the one hi-first word
    store rung (d) merges the two restores into."""
    body = _body(_lift("low_held_cursor"))
    assert re.search(r"\bsp\b", body), "the hold lost its sp spelling"
    assert "mem[($0100 | zext2(sp)):2] = s0" in body, "the held slot dropped its store"
    assert "hi-first ptr_%04X:2 = ((zext2(s1) << $08):2 | zext2(s0)):2" % G.PTR in body


def test_a_stack_held_cursor_lifts_once_the_slot_is_named():
    """FLIPPED at the slot identity: the restore is the push, so no page-one def stands.

    3d predicted the deref span and that is not the premise. An sp-relative slot the
    push and the pull name at one entry-relative offset is, with the machine's own
    return-address push a writer by construction -- the below-sp premise at every call."""
    rec = _cert("low_held_cursor")
    assert rec["eligible"] and rec["lift_refusals"] == []


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
    reason="%s an arithmetic row lands off the registry; %s -- extent_unmappable fires "
    "because the rows the run observes are in no declared datum, and an interval for the "
    "row is the input to that and not the mechanism: INT_OR is in no interval rule (the "
    "sound pair is hi(a|b) <= next_pow2(max(hi a, hi b)) - 1, lo(a|b) >= max(lo a, lo b)), "
    "but the consumer that turns a span into a declaration is via: discovery"
    "" % (_S3, _OWNERS[4]),
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


def test_dispatch_scratch_promotes():
    """FLIPPED: the arm table is the transfer's successor set, so the arms keep its memory.

    The predicted in-edge-join extension was not the blocker -- the label never reset.
    The two measured causes were the ``dgoto``'s own havoc, which the arm table right
    after it makes unnecessary, and a zero extension that dropped its operand's bound."""
    assert not re.search(r"\bm_%04X\b" % TMP, _lift("dispatch_scratch"))


def test_the_dispatch_arms_read_the_value_and_not_the_cell():
    """The flip's own mechanism, stated where the pin cannot: both arms spell the value.

    Each handler is entered with the memory standing at the computed transfer, so the
    store is forwarded rather than read back, and with no reader left the store demotes."""
    body = _body(_lift("dispatch_scratch"))
    assert body.count("sid.v1.ctrl = ((t0 & $0F) | $") == 2, "an arm read the cell back"
    assert "m_%04X" % TMP not in _state_block(_lift("dispatch_scratch"))


def test_a_phase_split_reload_fuses_its_cursor_pair():
    """Each half store is a lane replacement - (ptr & $FF00) | zext2(row) - so no
    carry and no new operator is involved, and the two arms never have to meet.

    The hi lane is proven fixed here and the pair fuses anyway: what refuses a page
    selector is a lo lane advanced *in place*, which a table row replacement is not."""
    assert _fused_cursor("phase_split_reload")
    body = _body(_lift("phase_split_reload"))
    assert body.count("ptr_%04X:2 = ((ptr_%04X:2 & $" % (G.PTR, G.PTR)) == 2


def test_a_shift_divide_lifts_to_a_wide_shift():
    """The dialect has >>; the loop-carried rotate is the word's lanes, spelled as such.

    Unlike ``inpage_advance`` this fusion is semantically permitted: the loop is a
    power-of-two divide of one 16-bit value, and the loop-to-expression rule that would
    collapse the rotate to ``ptr >> n`` is rung (d2)'s, not this declaration's."""
    assert _fused_cursor("shift_divide")
    assert _lane_read("shift_divide"), "the rotated lo lane kept its byte cell"


def test_an_entry_balanced_procedure_destacks():
    """Landed 2c: the interior nonzero displacement is a label edge, not an imbalance."""
    assert not re.search(r"\bsp\b", _body(_lift("sp_fix_balance")))


def test_scratch_beside_kept_sp_fabric_promotes():
    """Landed: a call's wall retires what its callee may define, not every local.

    The chain already kept $0030 across all three ``pcall``s; what held the cell was the
    wall retiring every local, so the stored value named a version no site could spell.
    Bounded to ``frameproc._Info.may``, it crosses the calls and the cell's reader goes."""
    assert not re.search(r"\bzp_%02X\b" % ZTMP, _lift("sp_scratch_floor"))


def test_a_raw_call_holds_the_scratch_cell_the_promoted_call_would_free():
    """Control: the raw path keeps the cell for a second reason, and it is not the floor.

    The same fixture's cell survives beside a raw ``call`` where frameprog threads a
    procedure with a register interface, so a callee's may-set cannot bound the havoc
    there whatever the wall does -- that is the raw call's own boundary."""
    assert "sub_%04X(" % SPSUB in _body(_lift("sp_scratch_floor")), "the pcall went"
    body = _emit_body("sp_scratch_floor")
    assert "call $%04X" % SPSUB in body, "the raw path promoted the call after all"
    assert "sid.v1.ctrl = (zp_%02X | $21)" % ZTMP in body, "the read forwarded after all"


def _stage_three_pins():
    """``{name: reason}`` for every stage-3 pin, read off its own xfail mark."""
    return {
        n: m.kwargs["reason"]
        for n, f in sorted(globals().items())
        for m in getattr(f, "pytestmark", ())
        if m.name == "xfail" and m.kwargs.get("reason", "").startswith(_S3)
    }


def test_every_stage_three_pin_names_a_live_owner():
    """The deferral discipline, enforced: no pin may stand without someone to move it.

    The re-measurement the cutover owed is now structural -- ``_lift`` is the artifact,
    so a pin whose goal property held would XPASS -- and what a reason still owes is the
    refusal it was measured at and exactly one live owner from ``_OWNERS``."""
    pins = _stage_three_pins()
    assert len(pins) == 5, sorted(pins)
    assert not [n for n, r in pins.items() if sum(v in r for v in _OWNERS) != 1]


def test_the_owners_partition_the_family_as_the_ledger_records_it():
    """The ledger, executable: rung (d) owns none, and the rest are named apart.

    The twelve pair-premise pins landed with the per-site premise and the thirteenth
    with the write-only guard; a pin moving owners moves here too."""
    per = {o: sorted(n for n, r in _stage_three_pins().items() if o in r) for o in _OWNERS}
    assert per["owner: rung (d)"] == [], "the widening guard landed; rung (d) owns none"
    assert len(per["owner: rung (f)"]) == 4, per["owner: rung (f)"]
    assert per["owner: framestack"] == [], "the slot identity landed; framestack owns none"
    assert per["owner: datadecl"] == ["test_computed_rows_map"]
    assert per["owner: frameproc"] == [], "the reach reading landed; frameproc owns none"
    assert per["owner: eqlift_mem"] == [], "the wall's bound landed; eqlift_mem owns none"


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
    assert "goto ((ptr_%04X:2 + $0001):2)" % FTC in body


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
