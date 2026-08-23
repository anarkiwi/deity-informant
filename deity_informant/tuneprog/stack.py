"""S4 -- eliminating the machine stack: the frames :mod:`.frames` proved become values.

A push whose pops all read it in its own frame is one value, a return-address push
is the continuation the ``Call`` carries, and the page is then dead storage. One
access the analysis cannot place leaves the whole stack where it was, since such a
read can see any byte of the page.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

from .frames import (
    SLOT,
    SP,
    SPREG,
    analyse,
    apply_reads,
    drop_regions,
    entry_value,
    fresh,
    touches,
)
from .ir import Call, Const, Let, Phi, REGIDX, Return, Store, Var
from .irwalk import apply_stmt, apply_term, call_order, defs_of, pure, renamer, stmt_uses, term_uses
from .ssa import canonical, dce, merge_chains


def _value(proc, pushes, defs):
    """The value a single push holds under a name of its own, or ``None``."""
    if len(pushes) != 1:
        return None
    lbl, i = pushes[0]
    v = proc.blocks[lbl].stmts[i].v
    if type(v) is Const or (type(v) is Var and defs[v.n] <= 1):
        return v
    return None


def _defcounts(proc):
    return Counter(n for b in proc.blocks.values() for s in b.stmts for n in defs_of(s))


def _rename_slot(proc, old, new):
    """Give the value ``old`` the slot's name, definition and uses alike."""
    for b in proc.blocks.values():
        for s in b.stmts:
            if type(s) is Let and s.n == old:
                s.n = new
    fn = renamer({old: Var(new)})
    for b in proc.blocks.values():
        for s in b.stmts:
            apply_stmt(s, fn)
        apply_term(b.term, fn)


def _popname(proc, keys, defs):
    """``(name, pop)`` when one pop takes the whole slot: the pop's own name serves."""
    if len(keys) != 1:
        return None, None
    lbl, i, nid = keys[0]
    s = proc.blocks[lbl].stmts[i] if i < len(proc.blocks[lbl].stmts) else None
    if type(s) is Let and id(s.e) == nid and defs[s.n] == 1:
        return s.n, (lbl, i)
    return None, None


def _slotname(proc, name, make):
    """The slot's name: a machine unique takes it, a register keeps its own letter."""
    if name.split("#")[0] in REGIDX or name.startswith(SLOT):
        return name
    new = make()
    _rename_slot(proc, name, new)
    return new


def _slotted(proc, val, defs, make):
    """A pushed value under the slot's own name."""
    if type(val) is not Var or defs[val.n] != 1:
        return val
    return Var(_slotname(proc, val.n, make), val.w)


def _forward_pushes(proc, frame, make):
    """The plan's pushes as values; every other stack store is the machine's own."""
    defs, sub, edits, gone = _defcounts(proc), {}, {}, set()
    for pushes, keys in frame.plan:
        val = entry_value(frame, pushes)
        if val is not None:  # the machine's own entry frame: a value, never a store
            sub.update({k: deepcopy(val) for k in keys})
            continue
        val = _value(proc, pushes, defs)
        if val is None:
            name, pop = _popname(proc, keys, defs)
            val = Var(_slotname(proc, name, make) if name else make())
            edits.update({k: val.n for k in pushes})
            gone.update({pop} - {None})
        else:
            val = _slotted(proc, val, defs, make)
        sub.update({k: val for k in keys})
    apply_reads(proc, sub)
    for lbl, b in proc.blocks.items():
        out = []
        for i, s in enumerate(b.stmts):
            if (lbl, i) in edits:
                out.append(Let(edits[(lbl, i)], s.v))
            elif not ((type(s) is Store and touches(s)) or (lbl, i) in gone):
                out.append(s)
        b.stmts = out
    return len(frame.plan)


def _copies(proc):
    """Forward ``let x = y`` where both names are defined at most once: sound anywhere."""
    defs = _defcounts(proc)
    sub = {}
    for b in proc.blocks.values():
        for s in b.stmts:
            if type(s) is Let and type(s.e) is Var and defs[s.n] == 1 and defs[s.e.n] <= 1:
                sub[s.n] = s.e
    for k, v in list(sub.items()):
        seen = {k}
        while type(v) is Var and v.n in sub and v.n not in seen:
            seen.add(v.n)
            v = sub[v.n]
        sub[k] = v
    if sub:
        fn = renamer(sub)
        for b in proc.blocks.values():
            for s in b.stmts:
                apply_stmt(s, fn)
            apply_term(b.term, fn)
    return proc


def _isspdef(s):
    """True when the statement defines a stack-pointer value and can simply go."""
    t = type(s)
    if t is Phi:
        return s.n.split("#")[0] == SP
    return t is Let and s.n.split("#")[0] == SP and pure(s.e)


def _drop_sp(prog):
    """Remove the stack pointer from every procedure's interface and arithmetic."""
    params = {n: p.params for n, p in prog.procs.items()}
    rets = {n: p.rets for n, p in prog.procs.items()}
    for name, p in prog.procs.items():
        keep = [i for i, r in enumerate(rets[name]) if r != SPREG]
        for b in p.blocks.values():
            for s in b.stmts:
                if type(s) is Call and s.proc in params:
                    s.args = tuple(a for i, a in zip(params[s.proc], s.args) if i != SPREG)
                    s.rets = tuple(r for i, r in zip(rets[s.proc], s.rets) if i != SPREG)
            if type(b.term) is Return and len(b.term.vals) == len(rets[name]):
                b.term.vals = tuple(b.term.vals[i] for i in keep)
            b.stmts = [s for s in b.stmts if not _isspdef(s)]
        p.params = tuple(i for i in params[name] if i != SPREG)
        p.rets = tuple(i for i in rets[name] if i != SPREG)
    return prog


def _holds_sp(proc):
    """True when a stack-pointer value survives here, read or defined: it is data."""
    names = set()
    for b in proc.blocks.values():
        for s in b.stmts:
            stmt_uses(s, names)
            names.update(defs_of(s))
        term_uses(b.term, names)
    return any(n.split("#")[0] == SP for n in names)


def _depth(prog, info):
    """The stack depth a residual program is proven to use, or ``"unknown"``."""
    out = {}
    for name in call_order(prog):
        own = info[name].depth
        if own is None:
            return "unknown"
        below = [
            out[s.proc]
            for b in prog.procs[name].blocks.values()
            for s in b.stmts
            if type(s) is Call and s.proc in out
        ]
        out[name] = own + (max(below) + 2 if below else 0)
    return max(out.values()) if out else 0


def eliminate(prog):
    """Remove the machine stack from ``prog``; returns the certificate's ``stack`` field.

    Sound because every stack load is must-defined by pushes of its own frame: no
    load is left on the page, so every store to it is dead and the pointer that
    addressed them is read by nothing.
    """
    out = deepcopy(prog)  # the pointer may still be data: commit only if it goes
    info = analyse(out)
    bad = sorted(n for n, f in info.items() if f.opaque)
    if not bad:
        make = fresh(out)
        for name, f in info.items():
            _forward_pushes(out.procs[name], f, make)
        _drop_sp(out)
        for p in out.procs.values():
            dce(p)
            _copies(p)
            dce(p)
        bad = sorted(n for n, p in out.procs.items() if _holds_sp(p))
        if not bad:
            for p in out.procs.values():
                merge_chains(p)
                canonical(p)
            prog.procs, prog.storage = out.procs, drop_regions(out).storage
            return "eliminated"
    return {"depth": _depth(out, info), "procs": bad}
