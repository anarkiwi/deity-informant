"""frameprog: the frame-level dialect of the sidprog language.

Generation gives annotation-free region trees (rung (a)), state-variable
opcode switches, declared inputs and the procedural surface; ``parse``/
``loads`` read it back over the shared grammar (``sidprog.lark``).
"""

from __future__ import annotations

from . import datadecl
from . import frameproc
from . import grammar as G
from . import sidprog
from .grammar import FRAMEPROG_VERSION

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
    "; the text is grammar-defined (deity_informant/sidprog.lark) and a canonical",
    ";   fixpoint dumps(loads(t)) == t; the reference evaluator (Gate FP) is M-FP2",
    "; byte-pair fusion (M-FP3) and per-voice unification (M-FP4) not applied",
    "; registers/temporaries are procedure locals; parameters, returns and",
    ";   for-ranges are inferred from register liveness (serialization-layer)",
)


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


def _state_fields(view, decls, dispatch, aliases=None):
    """(state fields, input names): the record header per spec 1.3/2."""
    scalars, arrays = _cells(view)
    spans = [(d["base"], d["base"] + d["size"]) for d in decls]
    names = dict(aliases or {})

    def hidden(a):
        return a in _INPUTS or _SID_LO <= a <= _SID_HI or any(lo <= a < hi for lo, hi in spans)

    def name(a):
        return names.get(a) or sidprog._addr_name(a)

    inputs = sorted(_INPUTS[a] for a in scalars & set(_INPUTS))
    fields = [
        (name(a), False, sorted(dispatch.get(a, ())))
        for a in sorted((scalars | set(dispatch)) - arrays)
        if not hidden(a)
    ]
    fields += [(name(a), True, []) for a in sorted(arrays) if not hidden(a)]
    return fields, inputs


def _field_line(name, array, observed):
    if array:
        return " %s: u8[]" % name
    obs = (" observed " + " ".join("$%02X" % v for v in observed)) if observed else ""
    return " %s: u8%s" % (name, obs)


def _state_lines(view, decls, dispatch):
    """``state { }`` section lines plus the declared input names."""
    fields, inputs = _state_fields(view, decls, dispatch)
    return ["state {"] + [_field_line(*f) for f in fields] + ["}"], inputs


def check_locals(procs):
    """Assert every local in every procedure is defined before use."""
    for entry, params, _rets, stmts in procs:
        _defined(stmts, set(params), entry)


def _defined(stmts, live, entry):
    for s in stmts:
        k = s[0]
        if k != "for":
            for x in frameproc._stmt_exprs(s):
                for name in sorted(frameproc._locset(x) - live):
                    raise ValueError("sub_%04X: local %r used before definition" % (entry, name))
        if k in ("asg", "for"):
            live.add(s[1])
        elif k == "pcall":
            live.update(s[3])
        for body in frameproc._stmt_bodies(s):
            _defined(body, live, entry)


class FrameProgram:
    """A frame program: header, state/inputs, const data, symbols, procedures."""

    def __init__(
        self,
        play,
        init,
        subtune=0,
        prologue=(),
        inputs=(),
        state=(),
        decls=(),
        symbols=None,
        procs=(),
        mem0=None,
    ):
        self.play = play
        self.init = init
        self.subtune = subtune
        self.prologue = list(prologue)
        self.inputs = list(inputs)
        self.state = list(state)
        self.data_decls = list(decls)
        self.symbols = dict(symbols or {})
        self.procs = list(procs)
        self.mem0 = bytearray(0x10000) if mem0 is None else mem0


def program(model):
    """The frame program of a committed block model (entry translation, rungs a-c)."""
    decls = getattr(model, "data_decls", None)
    aliases = getattr(model, "symbols", None)
    if decls is None:
        decls, aliases = datadecl.declarations(model)
    trees, labels, view = sidprog._model_trees(model)
    state, inputs = _state_fields(view, decls, model.dispatch_sets, aliases)
    procs = frameproc.procedures(trees, labels, view, set(model.dispatch_sets), aliases, model.play)
    return FrameProgram(
        model.play,
        model.init,
        getattr(model, "subtune", 0),
        getattr(model, "prologue", ()),
        inputs,
        state,
        decls,
        aliases,
        procs,
        model.mem0,
    )


def dumps(prog):
    """Canonical frameprog text; ``dumps(loads(t)) == t`` for canonical ``t``."""
    if not isinstance(prog, FrameProgram):
        prog = program(prog)
    head = ["frameprog %d" % FRAMEPROG_VERSION]
    head.extend(_NOTES)
    head.append("play $%04X" % prog.play)
    head.append("init $%04X" % prog.init)
    if prog.subtune:
        head.append("subtune %d" % prog.subtune)
    if prog.prologue:
        head.append("sid-init {")
        head.extend("  $%02X = $%02X" % (r, v) for r, v in prog.prologue)
        head.append("}")
    if prog.inputs:
        head.append("inputs { %s }" % " ".join(prog.inputs))
    body = ["state {"] + [_field_line(*f) for f in prog.state] + ["}"]
    data_out, _cov = sidprog._data_lines(prog.data_decls, prog.mem0)
    body.extend(data_out)
    n = len(body)
    body.extend(frameproc.render_lines(prog.procs))
    to_alias = sidprog._alias_sub(prog.symbols)
    if to_alias is not None:
        body = list(map(to_alias, body))
    return "\n".join(head + body[:n] + sidprog._symbol_lines(prog.symbols) + body[n:]) + "\n"


def parse(text):
    """Parse canonical frameprog text into a ``FrameProgram``."""
    doc = G.parse_document(text, "frameprog")
    if doc.init is None or doc.play is None:
        raise ValueError("missing init/play header")
    return FrameProgram(
        doc.play,
        doc.init,
        doc.subtune,
        doc.prologue,
        doc.inputs,
        doc.state,
        doc.data_decls,
        doc.symbols,
        doc.subs,
        doc.mem0,
    )


def lint(text):
    """Assert every local in every emitted procedure is defined before use."""
    check_locals(parse(text).procs)


def emit(model):
    """frameprog text for a committed block model."""
    prog = program(model)
    check_locals(prog.procs)
    return dumps(prog)


loads = parse
