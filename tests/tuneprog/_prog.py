"""Snippet -> tuneprog helpers shared by the IR, SSA, idiom, codegen and print tests."""

from deity_informant.tuneprog import copymerge, pipeline, printer
from deity_informant.tuneprog.build import build_ir
from deity_informant.tuneprog.cfg import build_procs
from deity_informant.tuneprog.idioms import rewrite
from deity_informant.tuneprog.lift import lift_trace
from deity_informant.tuneprog.ir import STACK_HI, STACK_LO, Store
from deity_informant.tuneprog.irwalk import node_loads
from deity_informant.tuneprog.regions import build_regions
from deity_informant.tuneprog.ssa import Folds, simplify
from deity_informant.tuneprog.stack import eliminate

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


def tuneprog(code, calls=4, s4=False, stack=True, **kw):
    """``(Trace, Tuneprog)`` for a snippet, optionally after the S4 passes.

    ``stack=False`` keeps the machine stack the S4 elimination would remove, which
    is what the differential tests compare against.
    """
    T, _tr, L, R, P = front(code, calls=calls, **kw)
    prog = build_ir(T, L, R, P, meta={"name": "snippet"})
    if s4:
        simplify(prog, rewrite, folds=Folds(T.image_post_init, T.cells, T.written_play))
        if stack:
            prog.meta["stack"] = eliminate(prog)
    return T, prog


def stmts(prog):
    return [s for p in prog.procs.values() for b in p.blocks.values() for s in b.stmts]


def counter(*lines, cnt=True):
    """A tune whose play routine is ``lines``, with an ``init`` that zeroes ``cnt``."""
    src = ["init: LDA #$00", "STA cnt", "RTS", "play:"] + list(lines) + ["RTS"]
    if cnt:
        src += ["cnt: BRK"]
    return asm(PLAY, *src)


def printed(code, calls=6, **kw):
    """The printed tuneprog of a snippet, through the whole presentation stack."""
    _T, prog = tuneprog(code, calls=calls, s4=True, **kw)
    before = prog.to_json()
    view, st, names = pipeline.present(prog)
    assert prog.to_json() == before  # S5/S6 annotate; the certified program is untouched
    return printer.render(view, st, names, pcs=False)


def merged(code, calls=6, **kw):
    """A snippet with its sibling copies folded: ``(text, copies, view, program, trace)``."""
    T, _tr, _L, _R, _P = front(code, calls=calls, **kw)
    prog = pipeline.build(T, "snippet")[0]
    view, st, names = pipeline.present(prog)
    names.copies = copymerge.report(prog)
    return printer.render(view, st, names, pcs=False), names.copies, view, prog, T


def proc_body(doc, name):
    """The lines of one printed procedure."""
    out, on = [], False
    for line in doc.splitlines():
        if line.startswith("%s(" % name):
            on = True
            continue
        if on and (line.startswith("```") or (line and not line.startswith(" "))):
            break
        if on:
            out.append(line)
    return out


def stack_access(prog):
    """Every load and store a program still makes on the stack page."""
    out = []
    for p in prog.procs.values():
        for b in p.blocks.values():
            for s in list(b.stmts) + [b.term]:
                out += [x for x in node_loads(s) if x.lo <= STACK_HI and x.hi >= STACK_LO]
                if type(s) is Store and s.lo <= STACK_HI and s.hi >= STACK_LO:
                    out.append(s)
    return out
