"""frameprog: the frame-level dialect of the sidprog language.

Generation gives annotation-free region trees (rung (a)), state-variable
opcode switches, declared inputs and the procedural surface; ``parse``/
``loads`` read it back over the shared grammar (``sidprog.lark``).
"""

from __future__ import annotations

from . import datadecl
from . import framefuse
from . import framemath
from . import frameproc
from . import frameptr
from . import framestack
from . import grammar as G
from . import initcopy
from . import ptrlift
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
    "; sid.reg[i] is the byte view of the SID register file: a store whose index",
    ";   rung (d) cannot prove asserts one byte at offset i and names no 16-bit",
    ";   register, so a named freq/pulse/cutoff access is always u16",
    "; *ptr[i] (rung f) is a deref whose every definition loads a declared lo/hi",
    ";   pointer table, so the address is a row of one of that table's blocks",
    "; registers/temporaries are procedure locals; parameters, returns and",
    ";   for-ranges are inferred from register liveness (serialization-layer)",
)
_EXTENT_NOTE = (  # emitted only by a program carrying one: the notes are emitted bytes
    "; a state field's `in` clause is that pointer's extent: the declared blocks",
    ";   its derefs were observed to land in, and the only blocks an access may name",
)
_EVIDENCE_NOTE = (
    "; image/dispatch/evidence are the trace channels a block-model rebuild consumes:",
    ";   frameprog.block_model(loads(t)) re-derives the model this text was emitted",
    ";   from, so the artifact is total and the trace need not be repeated",
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


def _field_line(name, width, array, observed, blocks=()):
    if array:
        return " %s: u%d[]" % (name, 8 * width)
    ext = (" in " + ", ".join(blocks)) if blocks else ""
    obs = (" observed " + " ".join("$%02X" % v for v in observed)) if observed else ""
    return " %s: u%d%s%s" % (name, 8 * width, ext, obs)


def _extent_names(extents, symbols):
    """``{field name: block names}``: the extent map spelled as the state text names it."""

    def spell(a):
        return symbols.get(a) or G.addr_name(a)

    return {spell(c): [spell(b) for b in sorted(bs)] for c, bs in extents.items()}


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
        extents=(),
        dispatch=(),
        evidence=None,
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
        self.extents = dict(extents)  # 2b: pointer cell -> the block bases its derefs land in
        self.dispatch = {pc: set(v) for pc, v in dict(dispatch).items()}  # opcode-cell sets
        self.evidence = evidence or G.new_evidence()  # 3a: the block-model rebuild channels


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
    """``(cell -> origin, per-site verdicts, census)``: the init copies (spec 4.5)."""
    tracer = getattr(model, "init_copy", None)
    if tracer is None:
        return {}, {}, {}
    return initcopy.reduce(tracer, datadecl.Regions(decls).const_at, model.written)


_PAGE1 = range(0x100, 0x200)  # always mutable: a rule of ``Model``, never evidence
_SPANS_PER_LINE = 12


def _spans(addrs):
    """Sorted addresses as ``$A`` / ``$A..$B`` run tokens."""
    out, seq, i = [], sorted(addrs), 0
    while i < len(seq):
        j = i
        while j + 1 < len(seq) and seq[j + 1] == seq[j] + 1:
            j += 1
        out.append("$%04X" % seq[i] if j == i else "$%04X..$%04X" % (seq[i], seq[j]))
        i = j + 1
    return out


def _evidence(model, origins, sites, census):
    """The trace channels a block-model rebuild consumes, as the artifact carries them."""
    ev = G.new_evidence()
    ev["code"] = set(getattr(model, "pcs", None) or ())
    ev["leaders"] = set(getattr(model, "leaders", None) or ())
    ev["written"] = set(getattr(model, "written", None) or ()).difference(_PAGE1)
    ev["targets"] = {s: set(t) for s, t in (getattr(model, "ev_targets", None) or {}).items() if t}
    for pc, a in getattr(model, "reads", None) or ():
        ev["reads"].setdefault(pc, set()).add(a)
    clo = getattr(model, "closure", None)
    if clo is not None:
        ev["closure"] = tuple(
            -1 if x is None else x for x in (clo.recur, clo.first, clo.window, clo.cap)
        )
    ev["copies"] = {c: (origins[c], pc) for pc, (cells, _d, _r) in sites.items() for c in cells}
    ev["staged"] = {pc: (d, r) for pc, (_c, d, r) in sites.items() if d or r}
    ev["census"] = dict(census)
    return ev


def _evidence_lines(ev):
    """``evidence { }`` lines, empty for a program carrying no trace channel."""
    body = []
    for key in ("code", "leaders", "written"):
        toks = _spans(ev[key])
        for i in range(0, len(toks), _SPANS_PER_LINE):
            body.append(" %s %s" % (key, " ".join(toks[i : i + _SPANS_PER_LINE])))
    body.extend(
        " targets $%04X: %s" % (s, " ".join("$%04X" % t for t in sorted(ev["targets"][s])))
        for s in sorted(ev["targets"])
    )
    body.extend(
        " reads $%04X: %s" % (pc, " ".join(_spans(ev["reads"][pc]))) for pc in sorted(ev["reads"])
    )
    if ev["closure"] is not None:
        body.append(" closure %d %d %d %d" % ev["closure"])
    body.extend(
        " copy $%04X = $%04X @ $%04X" % ((c,) + ev["copies"][c]) for c in sorted(ev["copies"])
    )
    body.extend(" staged $%04X: %d %d" % ((pc,) + ev["staged"][pc]) for pc in sorted(ev["staged"]))
    body.extend(" census %s %d" % (k, ev["census"][k]) for k in sorted(ev["census"]))
    return ["evidence {"] + body + ["}"] if body else []


def block_model(prog, sound=False):
    """The committed block model ``prog`` was emitted from (3a: the artifact is total).

    Image, dispatch and evidence rebuild the trace channels; ``build_all`` re-derives
    every site table and ``datadecl.declarations`` the data regions, so the model
    walks, re-declares and re-emits identically."""
    ev = prog.evidence
    mem0 = bytes(prog.mem0)
    pcs = {pc: {mem0[pc]} for pc in ev["code"]}
    pcs.update({pc: set(v) for pc, v in prog.dispatch.items()})
    clo = ev["closure"]
    if clo is not None:
        recur, first, window, cap = (None if x < 0 else x for x in clo)
        note = "" if recur is not None else "no recurrence within %d frames" % cap
        clo = structured.Closure(recur, first, window, cap, (), note)
    evidence = structured.Evidence(
        pcs,
        set(ev["leaders"]),
        {s: set(t) for s, t in ev["targets"].items()},
        set(ev["written"]) | set(prog.dispatch),  # an opcode cell is written by definition
        [],
        b"",
        [],
        prog.prologue,
        mem0,
        reads=frozenset((pc, a) for pc, addrs in ev["reads"].items() for a in addrs),
        closure=clo,
        play=prog.play,
        init_copy=initcopy.Reduced(_origins(ev), _sites(ev), ev["census"]),
    )
    return structured.Model(mem0, prog.init, prog.play, evidence, prog.subtune, sound).build_all()


def _origins(ev):
    return {c: o for c, (o, _pc) in ev["copies"].items()}


def _sites(ev):
    """``initcopy.reduce``'s per-site verdicts, rebuilt from the copy and staged lines."""
    staged = {}
    for cell in sorted(ev["copies"]):
        staged.setdefault(ev["copies"][cell][1], []).append(cell)
    counts = ev["staged"]
    return {
        pc: (tuple(staged.get(pc, ())), *counts.get(pc, (0, 0)))
        for pc in sorted(set(staged) | set(counts))
    }


def _decl_pairs(decls):
    """``{lo base: (hi base, size)}`` off the declared roles, the ONE pair registry."""
    return {
        d["base"]: (d["role"][1], d["size"])
        for d in decls
        if d.get("role") and d["role"][0] == "lo"
    }


def _pack_witness(n, bases, cover):
    """``(lo, hi)`` decl bases where ``n`` packs two non-adjacent byte columns.

    An indexed pack witnesses at the bases themselves; a scalar pack witnesses
    through ``cover`` -- the declaration each cell sits in -- when both cells
    sit at one offset into declarations the same distance apart. Cells no
    declaration names witness for themselves, as a pair of one element."""
    got = frameproc.packed_cells(n)
    if got is None:
        return None
    bl, bh, il = got
    if bh == bl + 1:
        return None
    if il is None:
        dl, dh = cover.get(bl), cover.get(bh)
        if dl is None and dh is None:
            return ("cells", bl, bh)
        if dl is None or dh is None:
            return None
        if dl == dh:
            return ("intra", dl, bh - bl)
        if bl - dl != bh - dh:
            return None
        bl, bh = dl, dh
        if bh == bl + 1:
            return None
    return ("pair", bl, bh) if bl in bases and bh in bases else None


def _sole(votes):
    """``{key: its one witness}``, in key order: two witnesses that disagree are none.

    Every tally in the rung -- table pairs, loose cells, intra-decl splits --
    settles on this one law, so a column that two packs read differently is left
    a plain byte rather than shredded on the strength of half its evidence."""
    return {k: next(iter(s)) for k, s in sorted(votes.items()) if len(s) == 1}


def _split_intra(decls, bases, intra, vetoed, words):
    """Split a declaration whose halves the value graph packs (re-striding).

    A scalar pack of ``(D+o, D+K+o)`` inside one plain declaration of size 2K
    is the witness that D is two K-byte columns of one u16 datum; the decl
    splits in place, the data and mut offsets with it, and the roles land so
    the registry sees an ordinary pair."""
    for dbase, k in _sole(intra).items():
        d = bases.get(dbase)
        if d is None or k < 2 or d["size"] != 2 * k:
            continue
        if dbase in vetoed or dbase in words or dbase + k in words:
            continue
        if d["stride"] != 1 or d["cobases"] or d["via"] or d["targets"] or d["cmp"]:
            continue
        hi = dict(d)
        hi["base"], hi["size"] = dbase + k, k
        hi["data"] = d["data"][k:]
        hi["mut"] = [m - k for m in d["mut"] if m >= k]
        hi["dispatch"] = []
        d["size"], d["data"] = k, d["data"][:k]
        d["mut"] = [m for m in d["mut"] if m < k]
        decls.insert(decls.index(d) + 1, hi)
        d["role"], hi["role"] = ("lo", hi["base"]), ("hi", dbase)
        bases.pop(dbase, None)


def _cell_decl(base, role, mem0, mut):
    """A one-element byte-column declaration carved for a loose cell."""
    return {
        "kind": "table",
        "base": base,
        "size": 1,
        "stride": 1,
        "mut": datadecl._mut_offs(base, 1, 1, mut),
        "cobases": [],
        "role": role,
        "via": None,
        "targets": None,
        "cmp": [],
        "dispatch": [],
        "observed": False,
        "data": bytes(mem0[base : base + 1]),
    }


def _declare_cells(decls, cells, vetoed, words, mem0, mut, code):
    """Declare the loose cell pair a scalar pack witnesses (7.9 (a)).

    Two non-adjacent cells no declaration names, packed into one u16 and never
    used as an address, are a 16-bit datum shredded into byte columns of one
    element each -- the pack is the only evidence they will ever have, and it
    is the same evidence a split table's pack carries. They carve out of the
    image as a co-extensive lo/hi pair, so the registry rides the data section
    exactly as a table pair's does, and the roles survive a round trip."""
    covered = {a for d in decls for a in range(d["base"], d["base"] + d["size"])}
    refuse = vetoed | words | covered | code  # named, addressed, or executed: not loose
    taken, new = set(), []
    for lo, hi in _sole(cells).items():
        pair = {lo, hi}
        if len(pair) != 2 or pair & (refuse | taken):
            continue
        if not all(datadecl._LOW <= c < 0xD000 or c >= 0xE000 for c in pair):
            continue
        taken.update(pair)
        new.append(_cell_decl(lo, ("lo", hi), mem0, mut))
        new.append(_cell_decl(hi, ("hi", lo), mem0, mut))
    if new:
        decls.extend(new)
        decls.sort(key=lambda d: d["base"])


def _pair_tables(procs, decls, mem0, mut, code):
    """Declare the split lo/hi table pairs the value graph packs (7.9 (a)).

    A pack over two declared non-adjacent columns at one index is the witness
    that they are one u16 datum; the roles land on the decls, so the registry
    rides the data section and the parser rebuilds it from there."""
    bases = {d["base"]: d for d in decls if d.get("role") is None}
    cover = {}
    for d in decls:
        for a in range(d["base"], d["base"] + d["size"]):
            cover.setdefault(a, d["base"])
    words, veto = set(), set()
    tally = {"pair": {}, "cells": {}, "intra": {}}  # one tally per witness kind

    def walk(n, in_addr=False):
        if n[0] == "mem":
            if n[2] == 2:
                b = frameproc.addr_split(n[1])[0]
                if b is not None:
                    words.add(b)
            walk(n[1], True)
        elif n[0] == "op":
            got = _pack_witness(n, bases, cover)
            if got is not None:
                if in_addr:
                    veto.add(got[1:])  # an address packs them: they are one word, not two
                else:
                    tally[got[0]].setdefault(got[1], set()).add(got[2])
            for c in n[2]:
                walk(c, in_addr)

    for _e, _pa, _r, stmts in procs:
        for stmt in framefuse.stmts_of(stmts):
            for x in frameproc._stmt_exprs(stmt):
                walk(x)
    vetoed = {b for pr in veto for b in pr}
    _split_intra(decls, bases, tally["intra"], vetoed, words)
    _declare_cells(decls, tally["cells"], vetoed, words, mem0, mut, code)
    his = set()
    for lo, hi in _sole(tally["pair"]).items():
        if hi in his or lo in words or hi in words or lo == hi:
            continue
        if lo in vetoed or hi in vetoed:
            continue
        his.add(hi)
        bases[lo]["role"] = ("lo", hi)
        bases[hi]["role"] = ("hi", lo)
    return _decl_pairs(decls)  # roles persist on the decls: emission stays idempotent


def _adjoin_pairs(stmts, pairs, regions):
    """Bring each pair's half stores together so the renderer writes one word.

    The hi store moves up past statements that neither touch its cells nor
    rebind its value's locals; the SID stores crossed keep their own order."""
    for i, stmt in enumerate(stmts):
        for b in frameproc._stmt_bodies(stmt):
            _adjoin_pairs(b, pairs, regions)
    i = 0
    while i < len(stmts):
        got = _half_at(stmts[i], pairs)
        if got is None:
            i += 1
            continue
        _lo, hicell, idx, v = got
        j = _hi_partner(stmts, i, hicell, idx, v, regions)
        if j is None:
            i += 1
            continue
        stmts.insert(i + 1, stmts.pop(j))
        i += 2


def _half_at(s, pairs):
    """``(lo cell, hi cell, index, value)`` where ``s`` stores a pair's lo half."""
    if s[0] != "st" or not frameproc.is_op(s[2], "COPY"):
        return None
    base, idx = frameproc.addr_split(s[1])
    if base is None:
        return None
    got = frameproc.pair_site(pairs, base, idx)
    return None if got is None else (base, got[0], idx, s[2][2][0])


def _hi_partner(stmts, i, hi, idx, v, regions):
    """The movable hi-half store's position, else None (docs/frameprog.md 7.9)."""
    at = (hi, idx, 0, 1, 0)
    locs = frameproc._locset(v)
    want = frameproc.trunc_hi(v)
    for j in range(i + 1, min(i + 5, len(stmts))):
        s = stmts[j]
        if s[0] == "st":
            base, ji = frameproc.addr_split(s[1])
            if base == hi and ji == idx and s[2] == want:
                return j
            if frameproc.overlaps(at, frameproc.store_reach(s, regions)):
                return None
            if frameproc.reads(frameproc._stmt_exprs(s), at, regions):
                return None
            continue
        if s[0] != "asg" or s[1] in locs:
            return None
        if frameproc.reads((s[2],), at, regions):
            return None
    return None


def program(model, extents=None):
    """The frame program of a committed block model (entry translation, rungs a-g).

    ``extents`` is this tune's row of Phase 2b (b0)'s observed-extent artifact, which
    rung (g) reads; without one no web lifts and the text is the one rung (f) left."""
    decls = getattr(model, "data_decls", None)
    aliases = getattr(model, "symbols", None)
    if decls is None:
        decls, aliases = datadecl.declarations(model)
    symbols = dict(aliases or {})
    trees, labels, view = sidprog._model_trees(model)
    state, inputs = _state_fields(view, decls, model.dispatch_sets, symbols)
    procs = frameproc.procedures(trees, labels, view, set(model.dispatch_sets), symbols, model.play)
    stack_proofs = framestack.apply_rung(procs)
    state = framestack.drop_state(state, stack_proofs, symbols, G.addr_name)
    math_proofs = framemath.apply_rung(procs, decls)
    regions = datadecl.Regions(decls)
    frameproc.repolish(procs, model.play, regions)
    state, proofs = framefuse.apply_rung(model, decls, procs, state, symbols, G.addr_name)
    code = set(datadecl._code_bytes(model))
    for _pass in range(4):
        before = repr(procs)
        frameproc.repolish(procs, model.play, regions)
        pairs = _pair_tables(procs, decls, model.mem0, model.written, code)
        regions = datadecl.Regions(decls)  # the rung re-carves decls: containment follows
        for _e2, _pa2, _r2, stmts2 in procs:
            _adjoin_pairs(stmts2, pairs, regions)
        if repr(procs) == before:
            break
    proofs = stack_proofs + math_proofs + proofs + framestack.lift_rts_trick(procs)
    proofs += framestack.drop_sp(procs, model.play, regions)
    resolved, pinned, deref_proofs = frameptr.apply_rung(model.mem0, decls, procs)
    lifted, ext, lift_proofs = ptrlift.apply_rung(
        model.mem0, decls, procs, state, symbols, resolved, extents
    )
    resolved.update(lifted)
    prov0, sites, census = _init_copies(model, decls)
    init_proofs = [_init_proof(pc, *v) for pc, v in sites.items()]
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
        proofs + deref_proofs + lift_proofs + init_proofs,
        resolved,
        pinned,
        prov0,
        census,
        ext,
        model.dispatch_sets,
        _evidence(model, prov0, sites, census),
    )


def dumps(prog):
    """Canonical frameprog text; ``dumps(loads(t)) == t`` for canonical ``t``."""
    if not isinstance(prog, FrameProgram):
        prog = program(prog)
    head = ["frameprog %d" % FRAMEPROG_VERSION]
    head.extend(_NOTES)
    if prog.extents:
        head.extend(_EXTENT_NOTE)
    head.extend(_EVIDENCE_NOTE)
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
    head.extend(
        "dispatch $%04X: %s" % (pc, " ".join("$%02X" % v for v in sorted(prog.dispatch[pc])))
        for pc in sorted(prog.dispatch)
    )
    ext = _extent_names(prog.extents, prog.symbols)
    body = ["state {"] + [_field_line(*f, ext.get(f[0], ())) for f in prog.state] + ["}"]
    data_out, cov = sidprog._data_lines(prog.data_decls, prog.mem0)
    body.extend(data_out)
    n = len(body)
    body.extend(frameproc.render_lines(prog.procs, prog.resolved, _decl_pairs(prog.data_decls)))
    to_alias = sidprog._alias_sub(prog.symbols)
    if to_alias is not None:
        body = list(map(to_alias, body))
    mid = sidprog._symbol_lines(prog.symbols) + _evidence_lines(prog.evidence)
    head.extend(sidprog._image_lines(prog.mem0, cov))
    return "\n".join(head + body[:n] + mid + body[n:]) + "\n"


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
        prov0=_origins(doc.evidence),
        init_census=doc.evidence["census"],
        extents=doc.extents,
        dispatch=doc.dispatch_sets,
        evidence=doc.evidence,
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
