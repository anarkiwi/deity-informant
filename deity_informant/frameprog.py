"""frameprog: the frame-level artifact, and the one text the project emits.

Generation gives annotation-free region trees (rung (a)), state-variable opcode
switches, declared inputs and the procedural surface; ``parse``/``loads`` read
it back over the grammar (``sidprog.lark``)."""

from __future__ import annotations

import re

from . import datadecl
from . import eqlift_mem
from . import framefuse
from . import framemath
from . import frameproc
from . import frameptr
from . import framestack
from . import grammar as G
from . import idioms
from . import initcopy
from . import opdispatch
from . import ptrlift
from . import sidprog
from . import structured
from .grammar import FRAMEPROG_VERSION

_NOTES = (
    "; generated from the committed sidprog model (the cycle-exact ground truth)",
    "; the text is grammar-defined (deity_informant/sidprog.lark) and a canonical",
    ";   fixpoint dumps(loads(t)) == t; frameval.gate_fp is the reference evaluator",
    "; 16-bit fusion (rung d) makes a proven lo/hi pair one u16 state field and an",
    ";   adjacent freq/pulse/cutoff store pair one u16 store; a lone half is spelled",
    ";   through that word; per-voice unification is not applied",
    "; sid.reg[i] is the byte view of the SID register file: a store whose index",
    ";   rung (d) cannot prove, or a lone half of the write-only $D400-$D416, asserts",
    ";   one byte at offset i and names no 16-bit register",
    "; *ptr[i] (rung f) is a deref of a pointer web: every definition is a declared",
    ";   lo/hi table row, a constant or the web's own maintenance, and no other store",
    ";   may reach it; a web the maintenance opens names no target block set",
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


_IDENT = re.compile(r"[A-Za-z_]\w*")


def render_lines(prog):
    """The pre-switch projection: ``frameproc.render_lines`` over the program's procs.

    What a parsed program renders through, and the switch's own control -- patched over
    ``FrameProgram.lines`` it gives the text the emitter shipped before §8 step 4."""
    return frameproc.render_lines(prog.procs, prog.resolved, datadecl.decl_pairs(prog.data_decls))


def check_locals(procs):
    """Assert every local in every procedure is defined before use.

    A call that declares no returns still defines what its callee must define -- the
    machine runs it -- so the definitions are the procedures' own must-sets."""
    musts = frameproc.must_defines(procs)
    for entry, params, _rets, stmts in procs:
        _defined(stmts, set(params), entry, musts)


def _defined(stmts, live, entry, musts):
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
        elif k in ("call", "callb"):
            live.update(musts.get(s[1], ()))
        for body in frameproc._stmt_bodies(s):
            _defined(body, live, entry, musts)


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
        proved=None,
        pinned=(),
        prov0=(),
        init_census=None,
        extents=(),
        dispatch=(),
        evidence=None,
        roles=(),
        bounds=(),
        operators=(),
        landings=None,
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
        # rung (f)'s block-proved subset: 2b's top population is what this leaves
        self.proved = dict(self.resolved if proved is None else proved)
        self.pinned = dict(pinned)  # spec 4.6: deref address -> the address the proof names
        self.prov0 = dict(prov0)  # init-staged cell -> the declared byte it was copied from
        self.init_census = dict(init_census or {})
        self.extents = dict(extents)  # 2b: pointer cell -> the block bases its derefs land in
        self.roles = dict(roles)  # stage 2: state field name -> the role its updates name
        self.bounds = dict(bounds)  # stage 4: field name -> ("mask", k) / ("bound", lo, hi)
        self.operators = dict(operators)  # stage 4: opcode -> (name, arity, repeat, writes)
        self.dispatch = {pc: set(v) for pc, v in dict(dispatch).items()}  # opcode-cell sets
        self.evidence = evidence or G.new_evidence()  # 3a: the block-model rebuild channels
        self.landings = None if landings is None else frozenset(landings)  # None: parsed
        self._lines = None
        self._webs = None
        self.demoted = set()  # root extraction's scratch spans, filled by ``lines``

    def webs(self):
        """``entry -> frameproc.ProcWebs``: the analysis unit, computed once.

        A machine name carries one web per quantity it happens to hold, so the web
        and not the name is what a denotation can be attached to; a web the
        analysis refuses keeps its spelling and is counted, never merged away."""
        if self._webs is None:
            self._webs = frameproc.webs(self.procs)
        return self._webs

    def lines(self):
        """The artifact's procedure lines, rendered once (§8 step 4's unified graph).

        An analysed program renders through ``eqlift_mem``; a parsed one carries no
        landings and renders through ``frameproc.render_lines``, which is what makes
        ``dumps(loads(t)) == t`` a gate on the unified emitter rather than an accident."""
        if self._lines is None:
            if self.landings is None:
                self._lines = render_lines(self)
            else:
                self._lines = eqlift_mem.artifact_lines(self, demoted=self.demoted)
        return self._lines


def _aliased(proofs, symbols):
    """Every proof lemma spelled as the document spells its cells.

    A rung names cells canonically, the artifact declares the aliases, and a record
    that says ``*zp_FE`` joins to no ``state { }`` row: it is the body substitution."""
    sub = sidprog._alias_sub(symbols)
    return proofs if sub is None else [p._replace(lemma=sub(p.lemma)) for p in proofs]


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
    return datadecl.decl_pairs(decls)  # roles persist on the decls: emission stays idempotent


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
    state, inputs = sidprog._state_fields(view, decls, model.dispatch_sets, symbols)
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
    state = sidprog._drop_declared(state, decls, symbols)
    proofs = stack_proofs + math_proofs + proofs + framestack.lift_rts_trick(procs)
    proofs += framestack.drop_sp(procs, model.play, regions)
    resolved, blocked, pinned, deref_proofs = frameptr.apply_rung(model.mem0, decls, procs)
    lifted, ext, lift_proofs = ptrlift.apply_rung(
        model.mem0, decls, procs, state, symbols, blocked, extents
    )
    resolved.update(lifted)
    blocked.update(lifted)  # 2b spent its extent on the web: it leaves the top population
    frameproc.resign(procs, model.play)  # the bodies have settled: the headers are theirs now
    prov0, sites, census = _init_copies(model, decls)
    init_proofs = [_init_proof(pc, *v) for pc, v in sites.items()]
    state = _drop_transfer_operands(state, procs, symbols)
    prog = FrameProgram(
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
        _aliased(proofs + deref_proofs + lift_proofs + init_proofs, symbols),
        resolved,
        blocked,
        pinned,
        prov0,
        census,
        ext,
        model.dispatch_sets,
        _evidence(model, prov0, sites, census),
        landings=framefuse._landings(model),
    )
    prog.roles, prog.bounds = _roles(prog)
    prog.operators = _operators(model)
    return prog


_TARGET_AT = {"dgoto": 1, "dcall": 1, "igoto": 2, "dbr": 3}  # the transfer's own operand


def _read_bases(n, out):
    """Const bases of every memory read inside ``n``, at any width."""
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            base, _idx = frameproc.addr_split(x[1])
            if base is not None:
                out.add(base)
        stack.extend(frameproc._kids(x))


def _drop_transfer_operands(state, procs, symbols):
    """``state`` less every field read only to decide where the machine jumps.

    An SMC dispatch's cell holds no datum another statement observes: it is the
    transfer, which the artifact spells itself -- the arm table as a ``switch``,
    its domain in the ``dispatch`` header -- so a ``state`` row besides is
    machinery declared as data."""
    ctrl, data = set(), set()
    for _e, _p, _r, stmts in procs:
        for s in framefuse.stmts_of(stmts):
            at = _TARGET_AT.get(s[0])
            tgt = None if at is None or s[at] is None else s[at]
            for x in frameproc._stmt_exprs(s):
                _read_bases(x, ctrl if x is tgt else data)
            if s[0] == "opsw":
                ctrl.add(s[1])  # the switch subject is read to dispatch and nowhere else
    rev, named = {v: k for k, v in symbols.items()}, {}
    for f in state:
        base = rev.get(f[0], G.name_addr(f[0]))
        if base is not None:
            for a in range(base, base + (1 if f[2] else f[1])):
                named[a] = f[0]
    gone = {named[a] for a in ctrl if a in named} - {named[a] for a in data if a in named}
    return [f for f in state if f[0] not in gone]


def _operators(model):
    """``{opcode: (name, arity, repeat, writes)}``: the script VM's own operator set.

    A play routine that dispatches through an SMC operand has an operator set,
    and it is recovered rather than transcribed (``opdispatch``): a driver with
    no such dispatch declares none, which is most of the corpus."""
    return {op: tuple(rec) for op, rec in opdispatch.operator_set(model).items()}


def _kept_state(prog, procs, to_alias):
    """``prog.state`` less every field root extraction demoted to per-frame scratch.

    A field goes only where the emitter retired its stores as unobservable *and* no
    emitted line names the cell, so a declaration the text still uses always stays and
    a parsed program (which demotes nothing) re-derives the same block."""
    if not prog.demoted:
        return prog.state
    named = set(_IDENT.findall("\n".join(procs)))
    addr = {}
    for a, nm in prog.symbols.items():
        addr[nm] = a
    out = []
    for f in prog.state:
        nm = f[0] if to_alias is None else to_alias(f[0])
        a = addr.get(f[0], G.name_addr(f[0]))
        gone = a is not None and any(lo <= a <= hi for lo, hi in prog.demoted)
        if not (gone and nm not in named):
            out.append(f)
    return out


def _roles(prog):
    """``({field: role}, {field: ("mask", k)})``: stage 2's reading of each cell.

    Recognition licenses nothing -- an un-roled field stays a legal ``uN`` -- so a cell
    with no witnessed update, or one carrying an unshaped update, is simply absent. The
    mask is the bound the cell's own steps are taken under, carried as its evidence."""
    from . import roles  # pylint: disable=import-outside-toplevel  # ``roles`` is a field name

    cells = idioms.state_cells(prog)
    got, _shapes, _residue, bounds = roles.census(prog)
    out, spelled = {}, {}
    for a, role in got.items():
        name = cells.get(a)
        if role is not None and name is not None:
            out[name] = role
            if a in bounds:
                spelled[name] = ("mask", bounds[a])
    return out, spelled


def _initial(mem0, rev, name, width, array):
    """The value the init phase leaves in a state field, off the flat image itself.

    ``decompile`` keeps init's image and not its trace, so the cell's own bytes are
    the reading (spec 4.5's ``prov0`` names where each came from, never what it is)."""
    if array:
        return None
    addr = rev[name] if name in rev else G.name_addr(name)
    if addr is None or addr + width > len(mem0):
        return None
    return int.from_bytes(mem0[addr : addr + width], "little")


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
    ext = sidprog._extent_names(prog.extents, prog.symbols)
    to_alias = sidprog._alias_sub(prog.symbols)
    procs = prog.lines() if to_alias is None else list(map(to_alias, prog.lines()))
    state = _kept_state(prog, procs, to_alias)
    rev = {nm: a for a, nm in prog.symbols.items()}
    fields = [
        sidprog._field_line(
            *f,
            prog.roles.get(f[0]),
            ext.get(f[0], ()),
            prog.bounds.get(f[0]),
            _initial(prog.mem0, rev, *f[:3]),
        )
        for f in state
    ]
    body = ["state {"] + fields + ["}"] + sidprog._operator_lines(prog.operators)
    data_out, cov = sidprog._data_lines(prog.data_decls, prog.mem0)
    body.extend(data_out)
    n = len(body)
    if to_alias is not None:
        body = list(map(to_alias, body))
    body.extend(procs)
    mid = sidprog._symbol_lines(prog.symbols) + _evidence_lines(prog.evidence)
    head.extend(sidprog._image_lines(prog.mem0, cov))
    return "\n".join(head + body[:n] + mid + body[n:]) + "\n"


def parse(text):
    """Parse canonical frameprog text into a ``FrameProgram``."""
    doc = G.parse_document(text, "frameprog")
    if doc.init is None or doc.play is None:
        raise ValueError("missing init/play header")
    rev = {nm: a for a, nm in doc.symbols.items()}
    for name, width, array, _obs in doc.state:
        got = doc.initial.get(name)
        if got is not None and got != _initial(doc.mem0, rev, name, width, array):
            raise ValueError("state field %s: initial value is not the image's" % name)
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
        roles=doc.roles,
        bounds=doc.bounds,
        operators=doc.operators,
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
        name = sidprog._INPUTS.get(a)
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
