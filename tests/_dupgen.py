"""Region duplication: what copying a callee's region tree to its call sites costs.

A **driver** is a minimal complete tune calling one callee from several static
sites. ``duplicate`` copies the region tree to every site and drops the call
graph; ``pcs_global`` decides in advance whether that copy is exact.
"""

from __future__ import annotations

from functools import lru_cache

import _callgen as C
import _idiomgen as I
from deity_informant import frameproc, frameprog, frameval
from deity_informant import structured as S
from deity_informant.asm6502 import AsmIllegal as Asm

ORG, SID, FRAMES = I.ORG, I.SID, I.FRAMES
ROW, CNT, PTR = I.ROW, I.CNT, I.PTR


# ---- the predicate ---------------------------------------------------------------
_CASE_PCS = frozenset(("swg", "swc"))  # dispatch statements whose case label is a pc


def _arm_bodies(s):
    """A dispatch statement's arm bodies, each less the label restating its own case.

    ``frameval._Code._arms`` keys the arm in a table the statement owns, so the
    ``label`` at the head of an arm restates a pc the dispatch already resolves."""
    at = 1 if s[0] == "swg" else 2
    out = []
    for lbl, body in s[at]:
        pc = int(lbl[1:], 16)
        out.append(body[1:] if body and body[0] == ("label", pc) else body)
    return out


def pcs_global(stmts, out=None):
    """The pcs a copy of ``stmts`` would collide on: its *program-wide* bindings.

    ``goto $PC`` -> ``label $PC`` is the only pair resolved through ``_Code.pcmap``,
    which ``_link`` answers with the first copy. An arm and a static vector's landing
    go through their statement's own table; a ``callb``'s ends read neither."""
    out = [] if out is None else out
    for s in stmts:
        if s[0] == "label":
            out.append(s[1])
        bodies = _arm_bodies(s) if s[0] in _CASE_PCS else frameproc._stmt_bodies(s)
        for b in bodies:
            pcs_global(b, out)
    return sorted(set(out))


def copy_safe(prog):
    """``{callee entry: whether its region tree may be copied to its sites}``."""
    return {e: not pcs_global(s) for e, _p, _r, s in prog.procs if e != prog.play}


# ---- the transform ---------------------------------------------------------------
def _splice(stmts, bodies, params, play):
    """``stmts`` with every ``pcall`` replaced by a copy of its callee's statements.

    Arguments land as assignments to the callee's parameter names (the evaluator's
    locals are program-wide), and the trailing ``ret`` goes as in
    ``frameproc._Ser.splice``."""
    out = []
    for s in stmts:
        if s[0] == "pcall" and s[1] in bodies and s[1] != play:
            out += [("asg", n, a) for n, a in zip(params[s[1]], s[2])]
            body = _splice(bodies[s[1]], bodies, params, play)
            while body and body[-1][0] == "ret":
                body.pop()
            out += body
            continue
        out.append(tuple(_splice(x, bodies, params, play) if isinstance(x, list) else x for x in s))
    return out


def duplicate(prog):
    """``prog`` with the call graph removed: one procedure, every site's body its own.

    Unconditional, so a driver ``pcs_global`` refuses still emits and can be judged
    on the evidence rather than being refused before there is any."""
    bodies = {e: s for e, _p, _r, s in prog.procs}
    params = {e: p for e, p, _r, _s in prog.procs}
    prog.procs = [
        (e, p, r, _splice(s, bodies, params, prog.play))
        for e, p, r, s in prog.procs
        if e == prog.play
    ]
    prog._lines = None
    return prog


# ---- the drivers -----------------------------------------------------------------
def _head(p):
    p.label("init").i("LDA", "imm", 0)
    for c in I.STATE:
        p.i("STA", "zp", c)
    p.i("RTS")
    p.label("play")
    C.bump(p)


def _tail_sites(p, n, callee="dsub"):
    """``n`` static sites on one callee, each in tail position of its own branch."""
    for k in range(n - 1):
        p.i("LDA", "zp", ROW).i("AND", "imm", 2 << k).i("BEQ", "rel", ("L", "s%d" % k))
        p.i("LDA", "imm", 0x10 * (k + 1)).i("STA", "zp", CNT)
        p.i("JSR", "abs", ("L", callee)).i("RTS")
        p.label("s%d" % k)
    p.i("LDA", "imm", 0).i("STA", "zp", CNT)
    p.i("JSR", "abs", ("L", callee)).i("RTS")


def _open_sites(p, n, callee="dsub"):
    """``n`` static sites with work after each, so a copy's exit is not the frame's."""
    for k in range(n):
        p.i("LDA", "imm", 0x10 * k).i("STA", "zp", CNT)
        p.i("JSR", "abs", ("L", callee))
        p.i("LDA", "zp", CNT).i("STA", "abs", SID + 3 + k)
    p.i("RTS")


def _vectors(p, names=("vt0", "vt1")):
    I.tab_data(p)
    p.label("veclo").byte(*[("LOL", n) for n in names])
    p.label("vechi").byte(*[("HIL", n) for n in names])


def _dispatch_body(p, name="dsub"):
    """``JMP (ptr)`` off a declared lo/hi pair: the region that binds the arm pcs."""
    p.label(name).i("LDA", "zp", ROW).i("AND", "imm", 1).i("TAX")
    p.i("LDA", "absx", ("L", "veclo")).i("STA", "zp", PTR)
    p.i("LDA", "absx", ("L", "vechi")).i("STA", "zp", PTR + 1)
    p.i("JMP", "ind", PTR)


def _arm(p, name, table, reg):
    p.label(name).i("LDX", "zp", ROW).i("LDA", "absx", ("L", table))
    p.i("EOR", "zp", CNT).i("STA", "abs", reg).i("RTS")


def _dispatch(n):
    """A computed dispatch inside a callee, at ``n`` sites: the question's own driver."""
    p = Asm(ORG)
    _head(p)
    _tail_sites(p, n)
    _dispatch_body(p)
    _arm(p, "vt0", "tone", SID)
    _arm(p, "vt1", "alt", SID + 1)
    _vectors(p)
    return p


def _nested():
    """The dispatch callee under a callee that is itself duplicated: four copies."""
    p = Asm(ORG)
    _head(p)
    _tail_sites(p, 2, "asub")
    p.label("asub").i("LDA", "zp", ROW).i("AND", "imm", 4).i("BEQ", "rel", ("L", "a1"))
    p.i("JSR", "abs", ("L", "dsub")).i("RTS")
    p.label("a1").i("LDA", "zp", CNT).i("EOR", "imm", 0x0F).i("STA", "zp", CNT)
    p.i("JSR", "abs", ("L", "dsub")).i("RTS")
    _dispatch_body(p)
    _arm(p, "vt0", "tone", SID)
    _arm(p, "vt1", "alt", SID + 1)
    _vectors(p)
    return p


def _arm_calls():
    """The dispatch arms are themselves calls, so a copy carries a call graph inside."""
    p = Asm(ORG)
    _head(p)
    _tail_sites(p, 2)
    _dispatch_body(p)
    for name, off in (("vt0", 0), ("vt1", 1)):
        p.label(name).i("LDA", "imm", off).i("STA", "zp", I.FLG)
        p.i("JSR", "abs", ("L", "leaf")).i("RTS")
    p.label("leaf").i("LDX", "zp", ROW).i("LDA", "absx", ("L", "tone"))
    p.i("EOR", "zp", CNT).i("EOR", "zp", I.FLG).i("STA", "abs", SID).i("RTS")
    _vectors(p)
    return p


def _static_vector():
    """``JMP (const)`` off a vector nothing writes: the landing splices in with no arm.

    The binding inside a region that is the evaluator's alone -- no ``label`` line
    names it, so ``_callgen.pcs_bound`` cannot see it either."""
    p = Asm(ORG)
    _head(p)
    _open_sites(p, 2)
    p.label("dsub").i("LDA", "zp", CNT).i("STA", "abs", SID + 2)
    p.i("JMP", "ind", ("L", "vec"))
    _arm(p, "land", "tone", SID)
    I.tab_data(p)
    p.label("vec").byte(("LOL", "land"), ("HIL", "land"))
    return p


def _early_ret():
    """A sole-site callee with an early return: one ``callb``, copied to both sites.

    The block call is where the splice used to mint a continuation label; its entry
    is an op index and its ``ret`` pops its own frame, so the copy is exact."""
    p = Asm(ORG)
    _head(p)
    _open_sites(p, 2, "bsub")
    p.label("bsub").i("JSR", "abs", ("L", "leaf"))
    p.i("LDA", "zp", CNT).i("STA", "abs", SID + 2).i("RTS")
    p.label("leaf").i("LDX", "zp", ROW).i("LDA", "absx", ("L", "tone"))
    p.i("CMP", "imm", 0x40).i("BCC", "rel", ("L", "lo")).i("RTS")
    p.label("lo").i("EOR", "zp", CNT).i("STA", "abs", SID).i("RTS")
    I.tab_data(p)
    return p


def _irreducible():
    """A callee entered at two points of one loop, so the structurer emits the join.

    Neither entry dominates the other, ``label $PC`` stays in the callee's own region
    and a ``goto`` in it reaches -- the binding a copy collides on."""
    p = Asm(ORG)
    _head(p)
    _open_sites(p, 2, "isub")
    p.label("isub").i("LDA", "zp", ROW).i("AND", "imm", 1).i("BEQ", "rel", ("L", "e0"))
    p.i("JMP", "abs", ("L", "mid"))
    p.label("e0").i("LDA", "zp", CNT)
    p.label("top").i("EOR", "imm", 0x0F).i("STA", "abs", SID)
    p.label("mid").i("CLC").i("ADC", "imm", 1).i("STA", "zp", CNT)
    p.i("DEC", "zp", I.TMP).i("BNE", "rel", ("L", "top")).i("RTS")
    I.tab_data(p)
    return p


DRIVERS = {
    "dispatch/2-site": lambda: _dispatch(2),
    "dispatch/3-site": lambda: _dispatch(3),
    "dispatch/nested": _nested,
    "dispatch/arm-calls": _arm_calls,
    "static-vector": _static_vector,
    "early-ret": _early_ret,
    "irreducible": _irreducible,
}

REFUSED = ("irreducible",)  # drivers whose callee binds a pc no dispatch statement scopes


@lru_cache(maxsize=None)
def built(name):
    """``(model, base program, duplicated program)``, both programs read back canonical."""
    p = DRIVERS[name]()
    code = p.assemble()
    mem = bytearray(0x10000)
    mem[ORG : ORG + len(code)] = code
    model, _ev = S.decompile(
        mem, p.labels["init"], p.labels["play"], FRAMES, img=(ORG, ORG + len(code))
    )
    base = frameprog.loads(frameprog.dumps(frameprog.program(model)))
    dup = frameprog.loads(frameprog.dumps(duplicate(base)))
    return model, frameprog.loads(frameprog.dumps(frameprog.program(model))), dup


def text(name, which="dup"):
    """The emitted text of one of a driver's two programs."""
    return frameprog.dumps(built(name)[1 if which == "base" else 2])


def gate(name, which="dup"):
    """Gate FP's verdict on one of a driver's two programs; the fault where it faults."""
    model, base, dup = built(name)
    try:
        return frameval.gate_fp(model, FRAMES, base if which == "base" else dup)
    except frameval.FrameFault as exc:
        return "fault: %s" % exc


def violations(name, which="dup"):
    """``_callgen``'s 8.4 invariant, read off one of the driver's texts."""
    model, base, dup = built(name)
    prog = base if which == "base" else dup
    trace, _walker = frameprog.iota(model, FRAMES)
    out = []
    try:
        frameval.eval_fp(prog, trace, FRAMES)
    except frameval.FrameFault as exc:
        out.append("fault: %s" % exc)
    if len(prog.procs) != 1:
        out.append("procedures: %d" % len(prog.procs))
    for _e, params, rets, stmts in prog.procs:
        if C.SP in set(params) | set(rets):
            out.append("sp: parameter")
        out += C._walk(stmts, stmts)
    return tuple(sorted(set(out)))


def kinds(name, which="dup"):
    """The classes of a driver's violations: each finding stripped of its detail."""
    return frozenset(f.split(":")[0] for f in violations(name, which))
