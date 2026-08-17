"""Snippet -> tuneprog helpers shared by the IR, SSA, idiom, codegen and verify tests."""

from deity_informant.tuneprog.build import build_ir
from deity_informant.tuneprog.cfg import build_procs
from deity_informant.tuneprog.idioms import rewrite
from deity_informant.tuneprog.lift import lift_trace
from deity_informant.tuneprog.regions import build_regions
from deity_informant.tuneprog.ssa import Folds, simplify

from _asm import asm, trace_prog

PLAY = 0x1000


def front(code, calls=4, data=None, blocks=None, play=None, init=None, **kw):
    """Trace a snippet tune and return ``(Trace, Tracer, lifted, regions, procs)``."""
    b = dict(blocks or {})
    b[PLAY] = code
    T, tr = trace_prog(
        b,
        init=init if init is not None else code.labels["init"],
        play=play if play is not None else code.labels["play"],
        calls=calls,
        data=data,
        **kw,
    )
    L = lift_trace(T)
    R = build_regions(T, L)
    return T, tr, L, R, build_procs(T, L, R)


def tuneprog(code, calls=4, s4=False, **kw):
    """``(Trace, Tuneprog)`` for a snippet, optionally after the S4 passes."""
    T, _tr, L, R, P = front(code, calls=calls, **kw)
    prog = build_ir(T, L, R, P, meta={"name": "snippet"})
    if s4:
        simplify(prog, rewrite, folds=Folds(T.image_post_init, T.cells, T.written_play))
    return T, prog


def stmts(prog):
    return [s for p in prog.procs.values() for b in p.blocks.values() for s in b.stmts]


def counter(*lines, cnt=True):
    """A tune whose play routine is ``lines``, with an ``init`` that zeroes ``cnt``."""
    src = ["init: LDA #$00", "STA cnt", "RTS", "play:"] + list(lines) + ["RTS"]
    if cnt:
        src += ["cnt: BRK"]
    return asm(PLAY, *src)
