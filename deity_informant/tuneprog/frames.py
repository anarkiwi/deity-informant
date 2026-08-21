"""S4 -- the machine stack as frames: which pushes and pops of a program are values.

A stack pointer is its entry value plus the procedure's own pushes and pops, so an
access names a *slot* whose push and the pops it must-defines are one value.
:mod:`.stack` eliminates what this proves; :mod:`.frame` names what it leaves.
"""

from __future__ import annotations

from dataclasses import dataclass

from .graph import preds_of
from .ir import Bin, Call, Const, Let, Phi, Return, STACK_HI, STACK_LO, Store, Var, succs
from .irwalk import apply_stmt, apply_term, call_order, callees, node_loads, single_defs
from .lower import status_expr

SP = "SP"
SPREG = 3
SLOT = "$saved"
UNSET = ()  # no information yet: a value the fixpoint has not reached
ENTRY = "$entry"  # the pseudo-push key of a slot the machine filled before entry
STATUS_SLOT = 1  # 6510 interrupt frame: status at SP+1, return address at SP+2/+3


def contract(prog):
    """``{procedure: {slot: value}}`` of the frame the machine pushed before entry.

    An ``irq`` tick is entered with the interrupt frame its terminating ``RTI``
    pops, and the status byte in it is the entry flags packed: a parameter of the
    tick. Nothing names the pushed return address, so a read of it stays unplaced.
    """
    meta = prog.meta or {}
    tick = meta.get("tick_proc")
    if tick not in prog.procs or (meta.get("entry") or {}).get("kind") != "irq":
        return {}
    if any(tick in callees(p) for p in prog.procs.values()):
        return {}  # entered as a subroutine too: SP+1 is then a return-address byte
    return {tick: {STATUS_SLOT: status_expr()}}


def entry_value(frame, pushes):
    """The contract value a group's pseudo-push names, or ``None`` for real pushes.

    The caller substitutes it at every read, so each read takes its own copy.
    """
    key = next((k for k in pushes if k[0] == ENTRY), None)
    return None if key is None else frame.contract[key[1]]


def _delta(e, off, defs, seen=()):
    """The entry-relative stack offset ``e`` holds, ``None``, or :data:`UNSET`.

    ``seen`` are the names already walked: a definition chain that comes back to
    one of them is a cycle, and cycles hold no offset.
    """
    t = type(e)
    if t is Var:
        if e.n in off:
            return off[e.n]
        if e.n in defs and e.n not in seen:
            return _delta(defs[e.n], off, defs, seen + (e.n,))
        return UNSET if e.n.split("#")[0] == SP else None
    if t is Bin and e.op in ("+", "-") and type(e.b) is Const:
        d = _delta(e.a, off, defs, seen)
        if d is None or d is UNSET:
            return d
        return (d + (e.b.v if e.op == "+" else -e.b.v)) & 0xFF
    return None


def _merge(vals):
    """The one offset a set of them agrees on; ``None`` if they disagree."""
    if len(vals) == 1:
        return next(iter(vals))
    return UNSET if UNSET in vals else None


def _sp_defs(prog, s, off, defs, exits):
    """``(name, offset)`` for every stack-pointer value one statement defines."""
    t = type(s)
    if t is Let and s.n.split("#")[0] == SP:
        yield s.n, _delta(s.e, off, defs)
        return
    if t is Phi and s.n.split("#")[0] == SP:
        yield s.n, _merge({off.get(v, UNSET) for v in s.args.values()})
        return
    q = prog.procs.get(s.proc) if t is Call else None
    if q is None:
        return
    a = next((x for i, x in zip(q.params, s.args) if i == SPREG), None)
    d = exits.get(s.proc)
    base = None if a is None or d is None else _delta(a, off, defs)
    for i, n in zip(q.rets, s.rets):
        if i == SPREG:
            yield n, base if base is None or base is UNSET else (base + d) & 0xFF


def offsets(prog, proc, exits):
    """``{name: entry-relative stack offset}``, ``None`` where a value is not one."""
    off, defs = {SP: 0}, single_defs(proc)
    changed = True
    while changed:
        changed = False
        for b in proc.blocks.values():
            for s in b.stmts:
                for n, v in _sp_defs(prog, s, off, defs, exits):
                    if v is UNSET:
                        continue
                    old = off.get(n, UNSET)
                    if old is not UNSET and old != v:
                        v = None  # two definitions disagree: not one offset
                    if old is UNSET or old != v:
                        off[n], changed = v, True
    return off


def _norm(v):
    return None if v is UNSET else v


def exit_delta(prog, proc, off, defs, exits):
    """The offset a procedure returns its stack pointer at, when its exits agree."""
    i = proc.rets.index(SPREG) if SPREG in proc.rets else None
    ins, work, seen = {proc.entry: 0}, [proc.entry], set()
    while work:
        lbl = work.pop()
        cur = ins[lbl]
        for s in proc.blocks[lbl].stmts:
            for _n, v in _sp_defs(prog, s, off, defs, exits):
                cur = _norm(v)
        term = proc.blocks[lbl].term
        if type(term) is Return:
            v = term.vals[i] if i is not None and len(term.vals) > i else None
            seen.add(cur if v is None else _norm(_delta(v, off, defs)))
        for t in succs(proc.blocks[lbl].term):
            if t not in ins:
                ins[t], _ = cur, work.append(t)
            elif ins[t] != cur and ins[t] is not None:
                ins[t], _ = None, work.append(t)
    return seen.pop() if len(seen) == 1 else None


def deltas(prog):
    """``({procedure: exit offset}, {procedure: {value: offset}})``, callees first."""
    exits, offs = {}, {}
    for name in call_order(prog):
        proc = prog.procs[name]
        offs[name] = offsets(prog, proc, exits)
        exits[name] = exit_delta(prog, proc, offs[name], single_defs(proc), exits)
    return exits, offs


# ---- the accesses ------------------------------------------------------------
def touches(x):
    return x.lo <= STACK_HI and x.hi >= STACK_LO


def _slot(e, off, defs):
    """The frame slot an address expression names, or ``None``."""
    seen = set()
    while type(e) is Var and e.n in defs and e.n not in seen:
        seen.add(e.n)
        e = defs[e.n]
    if type(e) is not Bin or e.op not in ("|", "+"):
        return None
    for k, x in ((e.a, e.b), (e.b, e.a)):
        if type(k) is Const and k.v == STACK_LO:
            d = _norm(_delta(x, off, defs))
            return None if d is None else d & 0xFF
    return None


def events(proc, off, defs):
    """``{block: [(index, kind, slot, node)]}``, or ``None`` when a slot is not one."""
    out = {}
    for lbl, b in proc.blocks.items():
        evs = []
        for i, node in list(enumerate(b.stmts)) + [(len(b.stmts), b.term)]:
            for x in node_loads(node):
                if not touches(x):
                    continue
                slot = _slot(x.a, off, defs)
                if slot is None:
                    return None
                evs.append((i, "raw" if x.cls == "raw" else "load", slot, x))
            if type(node) is Store and touches(node):
                slot = _slot(node.a, off, defs)
                if slot is None:
                    return None
                evs.append((i, "store", slot, node))
        out[lbl] = evs
    return out


def _reaching(proc, evs, con=()):
    """``{(block, index, node id): (slot, the stores that reach it)}``.

    The entry carries one pseudo-definition per slot -- the machine's own frame
    where ``con`` names one, else ``None``, which is no value of this frame. An
    inlined load is one shared node, so a use is keyed by where it is read.
    """
    slots = {s for e in evs.values() for _i, _k, s, _n in e}
    last = {lbl: {s: {(lbl, i)} for i, k, s, _n in e if k == "store"} for lbl, e in evs.items()}
    seed = {s: {(ENTRY, s) if s in con else None} for s in slots}
    preds, ins = preds_of(proc), {lbl: {} for lbl in proc.blocks}
    ins[proc.entry] = {s: set(v) for s, v in seed.items()}
    changed = True
    while changed:
        changed = False
        for lbl in proc.blocks:
            cur = {s: set(v) for s, v in seed.items()} if lbl == proc.entry else {}
            for p in preds[lbl]:
                for slot, keys in {**ins[p], **last[p]}.items():
                    cur.setdefault(slot, set()).update(keys)
            if cur != ins[lbl]:
                ins[lbl], changed = cur, True
    out = {}
    for lbl, e in evs.items():
        cur = {k: set(v) for k, v in ins[lbl].items()}
        for i, kind, slot, node in e:
            if kind == "store":
                cur[slot] = {(lbl, i)}
            else:
                out[(lbl, i, id(node))] = (slot, frozenset(cur.get(slot, ())))
    return out


def _resolved(keys):
    """True when every path to the read passes a push of this frame."""
    return bool(keys) and None not in keys


def _pushes(proc, key):
    """True when ``key`` puts a value in its slot: a non-frame store, or the entry."""
    lbl, i = key
    if lbl == ENTRY:
        return True
    s = proc.blocks[lbl].stmts[i]
    return type(s) is Store and s.cls != "raw"


def _find(par, x):
    while par[x] != x:
        par[x] = par[par[x]]
        x = par[x]
    return x


def _plan(proc, evs, reach):
    """``([(pushes, loads)], unresolved loads)``: the pushes and pops of one value.

    Two pushes a load can both read are one value (the phi a branch left), so the
    groups are the connected components of the reaches relation; a group an
    unresolvable read joins is not a value at all.
    """
    loads = {}
    for lbl, e in evs.items():
        for i, kind, slot, node in e:
            if kind != "store":
                loads.setdefault(slot, []).append(((lbl, i, id(node)), kind))
    out, bad = [], []
    for _slot, ls in sorted(loads.items()):
        par = {}
        for key, _t in ls:
            for k in reach[key][1]:
                if k is not None:
                    par.setdefault(k, k)
        for key, _t in ls:
            ks = sorted(k for k in reach[key][1] if k is not None)
            for k in ks[1:]:
                par[_find(par, k)] = _find(par, ks[0])
        groups = {}
        for key, kind in ls:
            keys = reach[key][1]
            ok = kind != "raw" and _resolved(keys)
            root = _find(par, next(iter(keys))) if ok else None
            g = groups.setdefault(root, ([], [], True))
            g[1].append(key)
            if not ok:
                groups[root] = (g[0], g[1], False)
        for root, (_p, keys, ok) in groups.items():
            pushes = sorted(k for k in par if _find(par, k) == root)
            ok = ok and not (len(pushes) > 1 and any(k[0] == ENTRY for k in pushes))
            if ok and root is not None and all(_pushes(proc, k) for k in pushes):
                out.append((pushes, keys))
            else:
                bad.extend(keys)
    return out, bad


def _foreign(evs, reach):
    """True when a procedure can read a frame that is not its own.

    A slot above its entry is where its caller's values are, so no caller of a
    foreign procedure may drop its pushes.
    """
    if evs is None:
        return True
    for lbl, e in evs.items():
        for i, kind, slot, node in e:
            key = (lbl, i, id(node))
            if kind != "store" and 0 < slot < 0x80 and not _resolved(reach[key][1]):
                return True
    return False


@dataclass(slots=True)
class Frame:
    """One procedure's frame: its offsets, its accesses, and the slots that are values."""

    off: dict
    events: dict
    reach: dict
    plan: list
    unresolved: list
    foreign: bool
    contract: dict

    @property
    def opaque(self):
        """True when this procedure's stack is not covered by its own pushes."""
        return self.events is None or bool(self.unresolved)

    @property
    def depth(self):
        """Bytes below its entry pointer the procedure provably touches, or ``None``.

        Every placed access counts, read as well as written; a slot above the entry
        is the caller's frame and is no depth of this one.
        """
        if self.events is None:
            return None
        slots = [s for e in self.events.values() for _i, _k, s, _n in e]
        return max([1 - (s - 0x100 if s >= 0x80 else s) for s in slots] + [0])


def analyse(prog, info=None):
    """``{procedure: Frame}`` over the whole program, callees first."""
    exits, offs = info or deltas(prog)
    cons, out = contract(prog), {}
    for name in call_order(prog):
        proc = prog.procs[name]
        defs = single_defs(proc)
        off = offs.get(name) or offsets(prog, proc, exits)
        evs = events(proc, off, defs)
        con = cons.get(name, {})
        if evs is None:
            out[name] = Frame(off, None, {}, [], [None], True, con)
            continue
        reach = _reaching(proc, evs, con)
        plan, bad = _plan(proc, evs, reach)
        out[name] = Frame(off, evs, reach, plan, bad, _foreign(evs, reach), con)
    return out


# ---- the rewrite both the elimination and the view share ---------------------
def fresh(prog, want=SLOT):
    """A generator of forwarded-slot names, unique across ``prog``."""
    taken = {
        s.n
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in b.stmts
        if type(s) is Let
    }
    n = [0]

    def make():
        while True:
            n[0] += 1
            name = want if n[0] == 1 else "%s%d" % (want, n[0])
            if name not in taken:
                taken.add(name)
                return name

    return make


def _reader(sub, lbl, i):
    def fn(e):
        return sub.get((lbl, i, id(e)), e)

    return fn


def apply_reads(proc, sub):
    """Replace every read a plan forwarded, at the statement that reads it."""
    for lbl, b in proc.blocks.items():
        for i, s in list(enumerate(b.stmts)) + [(len(b.stmts), b.term)]:
            fn = _reader(sub, lbl, i)
            (apply_stmt if i < len(b.stmts) else apply_term)(s, fn)


def drop_regions(prog):
    """Drop the stack regions no access is left on."""
    live = set()
    for p in prog.procs.values():
        for b in p.blocks.values():
            for s in list(b.stmts) + [b.term]:
                live.update(x.r for x in node_loads(s))
                if type(s) is Store:
                    live.add(s.r)
    prog.storage = [r for r in prog.storage if r.id in live or not STACK_LO <= r.base <= STACK_HI]
    return prog
