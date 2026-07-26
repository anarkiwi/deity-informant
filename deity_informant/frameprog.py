"""frameprog: emit-only frame-level serializer over the committed model.

Prototype for docs/frameprog.md: sidprog region trees with cycle/penalty
annotations stripped (rung (a)), opcode cells as state-variable switches
(spec section 2), declared inputs, and a state/data/symbols header.
"""

from __future__ import annotations

from . import datadecl
from . import sidprog
from . import structured as C

FRAMEPROG_VERSION = 0

_INPUTS = {
    0xD011: "raster_hi",
    0xD012: "raster",
    0xD41B: "osc3",
    0xD41C: "envelope3",
    0xDC0D: "cia_icr",
}
_SID_LO, _SID_HI = 0xD400, 0xD41C

_NOTES = (
    "; generated from the committed sidprog model (the cycle-exact ground truth)",
    "; emit-only prototype: verification is the framelog projection of the",
    ";   model walker; the frameprog parser/evaluator (full Gate FP) is M-FP2",
    "; byte-pair fusion (M-FP3) and per-voice unification (M-FP4) not applied",
)


class _Writer(sidprog._Writer):
    """sidprog writer under the frame-level dialect hooks."""

    boundary_labels = False

    def switch_sel(self, pc):
        return sidprog._addr_name(pc)

    def if_head(self, word, pen, cond):
        return "%s %s" % (word, cond)

    def view_block(self, blk):
        events = [ev for ev in blk.events if ev[0] in ("ld", "st")]
        if len(events) != len(blk.events):
            blk = C.Block(blk.pc, blk.op0, blk.pcs, events, blk.term, blk.regs)
        return sidprog._stmt_view(blk)


def _scan(node, scalars, arrays):
    """Collect const-cell and indexed-base memory references under ``node``."""
    stack = [node]
    while stack:
        x = stack.pop()
        k = x[0]
        if k == "mem":
            a = x[1]
            if a[0] == "const" and a[2] == 2:
                scalars.add(a[1])
            else:
                bi = sidprog._split_index(a)
                if bi is not None:
                    arrays.add(bi[0])
                else:
                    stack.append(a)
        elif k == "op":
            stack.extend(x[2])


def _cells(view):
    """(scalar cells, array bases) referenced by the play-phase blocks."""
    scalars, arrays = set(), set()
    for blk in view.blocks.values():
        for ev in blk.events:
            if ev[0] == "ld":
                _scan(("mem", ev[2], 1), scalars, arrays)
            elif ev[0] == "st":
                _scan(("mem", ev[1], 1), scalars, arrays)
                _scan(ev[2], scalars, arrays)
        for i, r in enumerate(blk.regs):
            if r != ("reg", i):
                _scan(r, scalars, arrays)
        for x in sidprog._term_exprs(blk.term):
            _scan(x, scalars, arrays)
    return scalars, arrays


def _state_lines(view, decls, dispatch):
    """(state section, input names): the record header per spec 1.3/2."""
    scalars, arrays = _cells(view)
    spans = [(d["base"], d["base"] + d["size"]) for d in decls]

    def declared(a):
        return any(lo <= a < hi for lo, hi in spans)

    def hidden(a):
        return a in _INPUTS or _SID_LO <= a <= _SID_HI or declared(a)

    inputs = sorted(_INPUTS[a] for a in scalars & set(_INPUTS))
    out = ["state {"]
    for a in sorted((scalars | set(dispatch)) - arrays):
        if hidden(a):
            continue
        attr = ""
        if a in dispatch:
            attr = " observed " + " ".join("$%02X" % v for v in sorted(dispatch[a]))
        out.append(" %s: u8%s" % (sidprog._addr_name(a), attr))
    for a in sorted(arrays):
        if not hidden(a):
            out.append(" %s: u8[]" % sidprog._addr_name(a))
    out.append("}")
    return out, inputs


def emit(model):
    """frameprog text for a committed block model (emit-only; parser is M-FP2)."""
    decls = getattr(model, "data_decls", None)
    aliases = getattr(model, "symbols", None)
    if decls is None:
        decls, aliases = datadecl.declarations(model)
    trees, labels, view = sidprog._model_trees(model)
    head = ["frameprog %d" % FRAMEPROG_VERSION]
    head.extend(_NOTES)
    head.append("play $%04X" % model.play)
    head.append("init $%04X" % model.init)
    if getattr(model, "subtune", 0):
        head.append("subtune %d" % model.subtune)
    prologue = getattr(model, "prologue", ())
    if prologue:
        head.append("sid-init {")
        head.extend("  $%02X = $%02X" % (r, v) for r, v in prologue)
        head.append("}")
    state, inputs = _state_lines(view, decls, model.dispatch_sets)
    if inputs:
        head.append("inputs { %s }" % " ".join(inputs))
    body = list(state)
    data_out, _cov = sidprog._data_lines(decls, model.mem0)
    body.extend(data_out)
    w = _Writer(labels, set(model.dispatch_sets), view)
    for entry, root in trees:
        w.proc(entry, root)
    body.extend(w.out)
    to_alias, _strip = sidprog._alias_res(aliases)
    if to_alias is not None:
        body = list(map(to_alias, body))
    n = len(state) + len(data_out)
    return "\n".join(head + body[:n] + sidprog._symbol_lines(aliases) + body[n:]) + "\n"
