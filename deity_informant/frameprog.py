"""frameprog: the frame-level dialect of the sidprog language.

Generation gives annotation-free region trees (rung (a)), state-variable
opcode switches, declared inputs and the procedural surface; ``parse``/
``loads`` read it back over the shared grammar (``sidprog.lark``).
"""

from __future__ import annotations

from . import datadecl
from . import framefuse
from . import frameproc
from . import frameptr
from . import grammar as G
from . import initcopy
from . import sidprog
from . import structured
from .grammar import FRAMEPROG_VERSION

_NAMES = {0xD011: "raster_hi", 0xD012: "raster", 0xD41B: "osc3", 0xD41C: "envelope3"}
_INPUTS = {a: _NAMES[a] for a in structured._VOL}  # cycle-derived: what iota pins
_ZERO = structured._VOL0  # constant-0 sources (spec 1.3): neither input nor state
_SID_LO, _SID_HI = 0xD400, 0xD41C

_NOTES = (
    "; generated from the committed sidprog model (the cycle-exact ground truth)",
    "; the text is grammar-defined (deity_informant/sidprog.lark) and a canonical",
    ";   fixpoint dumps(loads(t)) == t; frameval.gate_fp is the reference evaluator",
    "; 16-bit fusion (rung d) makes a proven lo/hi pair one u16 state field and an",
    ";   adjacent freq/pulse/cutoff store pair one u16 store; a lone-half access",
    ";   refuses that pair; per-voice unification is not applied",
    "; *ptr[i] (rung f) is a deref whose every definition loads a declared lo/hi",
    ";   pointer table, so the address is a row of one of that table's blocks",
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
        if a in _INPUTS or a in _ZERO or _SID_LO <= a <= _SID_HI:
            return True
        return any(lo <= a < hi for lo, hi in spans)

    def name(a):
        return names.get(a) or sidprog._addr_name(a)

    inputs = sorted(_INPUTS[a] for a in scalars & set(_INPUTS))
    fields = [
        (name(a), 1, False, sorted(dispatch.get(a, ())))
        for a in sorted((scalars | set(dispatch)) - arrays)
        if not hidden(a)
    ]
    fields += [(name(a), 1, True, []) for a in sorted(arrays) if not hidden(a)]
    return fields, inputs


def _field_line(name, width, array, observed):
    if array:
        return " %s: u%d[]" % (name, 8 * width)
    obs = (" observed " + " ".join("$%02X" % v for v in observed)) if observed else ""
    return " %s: u%d%s" % (name, 8 * width, obs)


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
        proofs=(),
        resolved=(),
        pinned=(),
        prov0=(),
        init_census=None,
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
        self.proofs = list(proofs)  # rung (d) pair records, rung (f) deref-site records
        self.resolved = dict(resolved)  # rung (f): deref address -> (pointer cell, index)
        self.pinned = dict(pinned)  # spec 4.6: deref address -> the address the proof names
        self.prov0 = dict(prov0)  # init-staged cell -> the declared byte it was copied from
        self.init_census = dict(init_census or {})


def _init_proof(pc, cells, undeclared, computed):
    """One record per init store site: the cells it staged, and why the rest refuse."""
    return structured.Proof(
        pc,
        "init-copy",
        "resolved" if cells and not (undeclared or computed) else "refused",
        cells,
        "init copy: %d cell(s) staged from a declared const byte, %d from a cell no"
        " declaration names, %d computed (no traced load)" % (len(cells), undeclared, computed),
    )


def _init_copies(model, decls):
    """``(cell -> origin, proofs, census)``: the init phase's copies, named (spec 4.5)."""
    tracer = getattr(model, "init_copy", None)
    if tracer is None:
        return {}, [], {}
    origins, sites, census = initcopy.reduce(
        tracer, datadecl.Regions(decls).const_at, model.written
    )
    return origins, [_init_proof(pc, *v) for pc, v in sites.items()], census


def program(model):
    """The frame program of a committed block model (entry translation, rungs a-f)."""
    decls = getattr(model, "data_decls", None)
    aliases = getattr(model, "symbols", None)
    if decls is None:
        decls, aliases = datadecl.declarations(model)
    symbols = dict(aliases or {})
    trees, labels, view = sidprog._model_trees(model)
    state, inputs = _state_fields(view, decls, model.dispatch_sets, symbols)
    procs = frameproc.procedures(trees, labels, view, set(model.dispatch_sets), symbols, model.play)
    state, proofs = framefuse.apply_rung(model, decls, procs, state, symbols, G.addr_name)
    resolved, pinned, deref_proofs = frameptr.apply_rung(model.mem0, decls, procs)
    prov0, init_proofs, census = _init_copies(model, decls)
    return FrameProgram(
        model.play,
        model.init,
        getattr(model, "subtune", 0),
        getattr(model, "prologue", ()),
        inputs,
        state,
        decls,
        symbols,
        procs,
        model.mem0,
        proofs + deref_proofs + init_proofs,
        resolved,
        pinned,
        prov0,
        census,
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
    body.extend(frameproc.render_lines(prog.procs, prog.resolved))
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
        (),
        doc.resolved,
    )


class _IotaWalker(structured.Walker):
    """Walker that pins every declared volatile read as ``iota(f, input, k)``."""

    def __init__(self, model):
        super().__init__(model)
        self.frame = 0
        self.trace = {}
        self._k = {}
        self.vol_read = self._vol
        self.dyn_read = self._dyn

    def _pin(self, a, v):
        name = _INPUTS.get(a)
        if name is not None:
            key = (self.frame, name)
            k = self._k.get(key, 0)
            self._k[key] = k + 1
            self.trace[(self.frame, name, k)] = v
        return v

    def _vol(self, m, a, c):
        return self._pin(a, structured.volatile_read(m, a, c))

    def _dyn(self, m, a, c):
        return self._pin(a, structured._dyn_read(m, a, c))


def iota(model, nframes):
    """``({(frame, input, k): value}, frames)`` from one walker run (spec 1.3).

    Both sides of Gate FP consume this trace, so the law is well-defined by
    construction: frameprog never re-derives cycle positions."""
    w = _IotaWalker(model)
    frames = []
    for f in range(nframes):
        w.frame = f
        start = len(w.wlog)
        w.run(1)
        frames.append([(reg, val) for _c, reg, val in w.wlog[start:]])
    return w.trace, frames


def declared_inputs(trace):
    """Input names the trace actually records (spec 4(b) compares to ``inputs``)."""
    return sorted({name for _f, name, _k in trace})


def lint(text):
    """Assert every local in every emitted procedure is defined before use."""
    check_locals(parse(text).procs)


def emit(model):
    """frameprog text for a committed block model."""
    prog = program(model)
    check_locals(prog.procs)
    return dumps(prog)


loads = parse
