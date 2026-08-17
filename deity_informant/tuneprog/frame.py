"""S6 -- the machine stack as frames: a push and the pop that reads it are one value.

A procedure's stack pointer is its entry value plus its own pushes and pops, so a
stack access names a *slot*, aliasing is exact, and a callee's frame lies below the
pointer its caller holds. A pointer that is not a constant offset keeps its stack.
"""

from __future__ import annotations

from .ir import Bin, Call, Const, Let, Load, Return, Store, Var, succs
from .irwalk import apply_stmt, apply_term
from .ssa import preds_of

STACK = (0x0100, 0x01FF)
SP = "SP"
SPREG = 3
DEPTH = 6


def _defs(proc):
    """``{name: expression}`` for every name exactly one ``Let`` defines."""
    out = {}
    for b in proc.blocks.values():
        for s in b.stmts:
            if type(s) is Let:
                out.setdefault(s.n, []).append(s.e)
    return {n: v[0] for n, v in out.items() if len(v) == 1}


def _delta(e, off, defs, depth=DEPTH):
    """The entry-relative stack offset ``e`` holds, or ``None``."""
    t = type(e)
    if t is Var:
        if e.n in off:
            return off[e.n]
        return _delta(defs[e.n], off, defs, depth - 1) if depth and e.n in defs else None
    if t is Bin and e.op in ("+", "-") and type(e.b) is Const:
        d = _delta(e.a, off, defs, depth)
        return None if d is None else (d + (e.b.v if e.op == "+" else -e.b.v)) & 0xFF
    return None


def _sp_defs(prog, s, off, defs, exits):
    """``(name, offset)`` for every stack-pointer value one statement defines."""
    if type(s) is Let and s.n.split("#")[0] == SP:
        yield s.n, _delta(s.e, off, defs)
        return
    q = prog.procs.get(s.proc) if type(s) is Call else None
    if q is None:
        return
    a = next((x for i, x in zip(q.params, s.args) if i == SPREG), None)
    d = exits.get(s.proc)
    base = None if a is None or d is None else _delta(a, off, defs)
    for i, n in zip(q.rets, s.rets):
        if i == SPREG:
            yield n, None if base is None else (base + d) & 0xFF


def offsets(prog, proc, exits):
    """``{name: entry-relative stack offset}``, ``None`` where a value is not one."""
    off, defs = {SP: 0}, _defs(proc)
    for _ in range(len(proc.blocks) + 2):
        changed = False
        for b in proc.blocks.values():
            for s in b.stmts:
                for n, v in _sp_defs(prog, s, off, defs, exits):
                    v = None if n in off and off[n] != v else v
                    if n not in off or off[n] != v:
                        off[n], changed = v, True
        if not changed:
            break
    return off


def exit_delta(prog, proc, off, defs, exits):
    """The offset a procedure returns its stack pointer at, when its exits agree."""
    i = proc.rets.index(SPREG) if SPREG in proc.rets else None
    ins, work, seen = {proc.entry: 0}, [proc.entry], set()
    while work:
        lbl = work.pop()
        cur = ins[lbl]
        for s in proc.blocks[lbl].stmts:
            for _n, v in _sp_defs(prog, s, off, defs, exits):
                cur = v
        term = proc.blocks[lbl].term
        if type(term) is Return:
            v = term.vals[i] if i is not None and len(term.vals) > i else None
            seen.add(cur if v is None else _delta(v, off, defs))
        for t in succs(proc.blocks[lbl].term):
            if t not in ins:
                ins[t], _ = cur, work.append(t)
            elif ins[t] != cur and ins[t] is not None:
                ins[t], _ = None, work.append(t)
    return seen.pop() if len(seen) == 1 else None


def _order(prog):
    """Procedure names, callees before callers (the call graph is acyclic)."""
    out, seen = [], set()

    def visit(n):
        if n in seen or n not in prog.procs:
            return
        seen.add(n)
        for b in prog.procs[n].blocks.values():
            for s in b.stmts:
                if type(s) is Call:
                    visit(s.proc)
        out.append(n)

    for n in prog.procs:
        visit(n)
    return out


def _walk(e):
    t = type(e)
    if t is Load:
        yield e
        yield from _walk(e.a)
    elif t is Bin:
        yield from _walk(e.a)
        yield from _walk(e.b)


def _reads(node):
    """Every ``Load`` a statement or a terminator evaluates."""
    parts = (getattr(node, "e", None), getattr(node, "a", None), getattr(node, "v", None))
    parts += (getattr(node, "c", None),) + tuple(getattr(node, "args", ()))
    parts += tuple(getattr(node, "vals", ()))
    for e in parts:
        if e is not None:
            yield from _walk(e)


def _touches(x):
    return x.lo <= STACK[1] and x.hi >= STACK[0]


def _slot(e, off, defs, depth=DEPTH):
    """The frame slot an address expression names, or ``None``."""
    while type(e) is Var and depth and e.n in defs:
        e, depth = defs[e.n], depth - 1
    if type(e) is not Bin or e.op not in ("|", "+"):
        return None
    for k, x in ((e.a, e.b), (e.b, e.a)):
        if type(k) is Const and k.v == STACK[0]:
            d = _delta(x, off, defs)
            return None if d is None else d & 0xFF
    return None


def events(proc, off, defs):
    """``{block: [(index, kind, slot, node)]}``, or ``None`` when a slot is not one."""
    out = {}
    for lbl, b in proc.blocks.items():
        evs = []
        for i, node in list(enumerate(b.stmts)) + [(len(b.stmts), b.term)]:
            for x in _reads(node):
                if not _touches(x):
                    continue
                slot = _slot(x.a, off, defs)
                if slot is None:
                    return None
                evs.append((i, "raw" if x.cls == "raw" else "load", slot, x))
            if type(node) is Store and _touches(node):
                slot = _slot(node.a, off, defs)
                if slot is None:
                    return None
                evs.append((i, "store", slot, node))
        out[lbl] = evs
    return out


def _reaching(proc, evs):
    """``{(block, index, node id): (slot, the stores that reach it)}``.

    An inlined load is one shared node in several statements, so a use is keyed by
    where it is read, not by the value alone.
    """
    last = {lbl: {s: {(lbl, i)} for i, k, s, _n in e if k == "store"} for lbl, e in evs.items()}
    preds, ins = preds_of(proc), {lbl: {} for lbl in proc.blocks}
    for _ in range(len(proc.blocks) + 1):
        changed = False
        for lbl in proc.blocks:
            cur = {}
            for p in preds[lbl]:
                for slot, keys in {**ins[p], **last[p]}.items():
                    cur.setdefault(slot, set()).update(keys)
            if cur != ins[lbl]:
                ins[lbl], changed = cur, True
        if not changed:
            break
    out = {}
    for lbl, e in evs.items():
        cur = {k: set(v) for k, v in ins[lbl].items()}
        for i, kind, slot, node in e:
            if kind == "store":
                cur[slot] = {(lbl, i)}
            else:
                out[(lbl, i, id(node))] = (slot, frozenset(cur.get(slot, ())))
    return out


def _plan(proc, evs, reach):
    """``[(pushes, loads)]``: the pushes and pops of one value, in one frame slot.

    Two pushes a load can both read are one value (the phi a branch left), so the
    groups are the connected components of the reaches relation.
    """
    loads = {}
    for lbl, e in evs.items():
        for i, kind, slot, node in e:
            if kind != "store":
                loads.setdefault(slot, []).append(((lbl, i, id(node)), kind))
    out = []
    for _slot, ls in sorted(loads.items()):
        par = {}
        for key, _t in ls:
            for k in reach[key][1]:
                par.setdefault(k, k)
        for key, _t in ls:
            for k in sorted(reach[key][1])[1:]:
                par[_find(par, k)] = _find(par, sorted(reach[key][1])[0])
        groups = {}
        for key, kind in ls:
            keys = reach[key][1]
            root = _find(par, next(iter(keys))) if keys else None
            g = groups.setdefault(root, ([], [], True))
            g[1].append(key)
            if kind == "raw" or not keys:
                groups[root] = (g[0], g[1], False)
        for root, (_p, keys, ok) in groups.items():
            pushes = sorted(k for k in par if _find(par, k) == root)
            if ok and root is not None and all(_pushes(proc, k) for k in pushes):
                out.append((pushes, keys))
    return out


def _find(par, x):
    while par[x] != x:
        par[x] = par[par[x]]
        x = par[x]
    return x


def _split(s, name, keep):
    """A push as the value it pushed, keeping the write while a frame may be foreign."""
    out = [Let(name, s.v)]
    if keep:
        out.append(Store(s.cls, s.a, Var(name), s.w, s.lo, s.hi, s.r, s.src))
    return out


def _callees(prog, name):
    """Every procedure ``name`` can reach, itself excluded."""
    out, work = set(), [name]
    while work:
        n = work.pop()
        for b in prog.procs[n].blocks.values():
            for s in b.stmts:
                if type(s) is Call and s.proc not in out and s.proc in prog.procs:
                    out.add(s.proc)
                    work.append(s.proc)
    return out


def _foreign(evs, reach):
    """True when a procedure can read a frame that is not its own.

    Its stack pointer is not a slot everywhere, or it reads a slot above its entry
    -- which is where its caller's values are, so no caller may drop its pushes.
    """
    if evs is None:
        return True
    for _lbl, e in evs.items():
        for i, kind, slot, node in e:
            key = (_lbl, i, id(node))
            if kind != "store" and 0 < slot < 0x80 and not reach[key][1]:
                return True
    return False


def _pushes(proc, key):
    """True when the store at ``key`` is a push of a value, not a machine frame."""
    lbl, i = key
    s = proc.blocks[lbl].stmts[i]
    return type(s) is Store and s.cls != "raw"


def fresh(prog):
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
            name = "$saved" if n[0] == 1 else "$saved%d" % n[0]
            if name not in taken:
                taken.add(name)
                return name

    return make


def deltas(prog):
    """``({procedure: exit offset}, {procedure: {value: offset}})``, callees first.

    Measured on the certified program: the presentation copy drops the stack
    arithmetic nothing reads, and its uses would then have no definition left.
    """
    exits, offs = {}, {}
    for name in _order(prog):
        proc = prog.procs[name]
        offs[name] = offsets(prog, proc, exits)
        exits[name] = exit_delta(prog, proc, offs[name], _defs(proc), exits)
    return exits, offs


def frames(prog, info=None, make=None):
    """Forward every frame slot a procedure pushes and pops; returns the slot count."""
    make = make or fresh(prog)
    exits, offs = info or deltas(prog)
    plans, foreign, out = {}, {}, 0
    for name in _order(prog):
        proc = prog.procs[name]
        defs = _defs(proc)
        off = offs.get(name) or offsets(prog, proc, exits)
        evs = events(proc, off, defs)
        reach = {} if evs is None else _reaching(proc, evs)
        foreign[name] = _foreign(evs, reach)
        plans[name] = () if evs is None else _plan(proc, evs, reach)
    for name, plan in plans.items():
        proc = prog.procs[name]
        keep = any(foreign[c] for c in _callees(prog, name))
        edits, sub = {}, {}
        for pushes, keys in plan:
            var = make()
            for lbl, i in pushes:
                edits[(lbl, i)] = _split(proc.blocks[lbl].stmts[i], var, keep)
            sub.update({k: Var(var) for k in keys})
            out += 1
        _apply(proc, sub)
        for lbl in {l for l, _i in edits}:
            b = proc.blocks[lbl]
            b.stmts = [x for i, s in enumerate(b.stmts) for x in edits.get((lbl, i), [s])]
    _drop(prog)
    return out


def _apply(proc, sub):
    """Replace every read the plan forwarded, at the statement that reads it."""
    for lbl, b in proc.blocks.items():
        for i, s in list(enumerate(b.stmts)) + [(len(b.stmts), b.term)]:
            fn = _reader(sub, lbl, i)
            (apply_stmt if i < len(b.stmts) else apply_term)(s, fn)


def _reader(sub, lbl, i):
    def fn(e):
        return sub.get((lbl, i, id(e)), e)

    return fn


def _drop(prog):
    """Drop the stack regions the forwarding left without an access."""
    live = set()
    for p in prog.procs.values():
        for b in p.blocks.values():
            for s in list(b.stmts) + [b.term]:
                live.update(x.r for x in _reads(s))
                if type(s) is Store:
                    live.add(s.r)
    prog.storage = [r for r in prog.storage if r.id in live or not STACK[0] <= r.base <= STACK[1]]
    return prog
