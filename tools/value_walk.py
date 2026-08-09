"""One value question, asked once: what may this expression hold, and why no less.

Five partial analyses answer it per shape (``addr_bits``/``addr_floor``/``addr_range``
/``span``/``overlaps``, ``_counter_range``, ``_off_page``); this is the domain behind
them -- a strided interval over locals, memory at ⊤, a premise on every widening.
"""

import argparse
import json
import multiprocessing as mp
import signal
import sys
import time
from collections import Counter
from math import gcd
from pathlib import Path

import _sweep
import lift_residue

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/value_walk.py                                  # the whole cache
  python tools/value_walk.py --tunes Hubbard_Rob/Commando
  python tools/value_walk.py --guard 1500                     # + the differential guard"""

STACK_TOP = 0x01FF  # R1/G2's region: a reach bound inside it cannot leave the stack

ORDER = ("dyn", "unclosed", "landing", "loop", "call", "memory", "width")
CLASS = {
    "dyn": "top_dyn",
    "unclosed": "top_edge",
    "landing": "top_edge",
    "loop": "top_edge",
    "call": "top_call",
    "memory": "top_memory",
    "width": "top_width",
}
CLASSES = ("bounded", "top_dyn", "top_edge", "top_call", "top_memory", "top_width")


def _mask(w):
    return (1 << (8 * w)) - 1


class V:
    """``{lo, lo+stride, .., hi}`` over ``w`` bytes, and why it is no narrower.

    Sound by construction: a rule states a superset of the operation's image or falls
    back to the width bound, recording the premise that made it. ``why`` is what a
    query reports where the bound does not fit its region."""

    __slots__ = ("lo", "hi", "stride", "w", "why")

    def __init__(self, lo, hi, stride, w, why=frozenset()):
        self.lo, self.hi, self.stride, self.w, self.why = lo, hi, stride, w, why

    def __repr__(self):
        tail = "" if not self.why else " " + ",".join(sorted(self.why))
        return "V($%X..$%X/%d:%d%s)" % (self.lo, self.hi, self.stride, self.w, tail)

    @property
    def span(self):
        return self.hi - self.lo + 1

    def top(self):
        """The class this value is ⊤ under: the hardest premise that widened it."""
        for r in ORDER:
            if r in self.why:
                return CLASS[r]
        return "top_width"

    def premises(self):
        return "|".join(r for r in ORDER if r in self.why) or "none"

    def inside(self, lo, hi):
        return lo <= self.lo and self.hi <= hi


def full(w, why=("width",)):
    return V(0, _mask(w), 1, w, frozenset(why))


def exact(k, w, why=frozenset()):
    m = _mask(w)
    return V(k & m, k & m, 1, w, why)


def _mk(lo, hi, stride, w, why):
    """Normalise into the width: an exact wrap shifts, anything wider saturates."""
    m = _mask(w)
    if hi - lo >= m:
        return V(0, m, 1, w, why | {"width"})
    q = lo // (m + 1)
    if hi // (m + 1) != q:
        return V(0, m, 1, w, why | {"width"})
    if q:
        lo -= q * (m + 1)
        hi -= q * (m + 1)
        stride = stride if not (m + 1) % stride else 1
    if stride > 1 and (hi - lo) % stride:
        stride = gcd(stride, hi - lo) or 1
    return V(lo, hi, max(1, stride), w, why)


def _step(v):
    """A singleton constrains no congruence, so it contributes nothing to the gcd."""
    return 0 if v.lo == v.hi else v.stride


def join(a, b):
    """The union's hull; a congruence survives only where both sides sit on it."""
    if a is None:
        return b
    if b is None:
        return a
    s = gcd(gcd(_step(a), _step(b)), abs(a.lo - b.lo)) or 1
    return _mk(min(a.lo, b.lo), max(a.hi, b.hi), s, max(a.w, b.w), a.why | b.why)


def cast(v, w):
    """``v`` read at ``w`` bytes: widening keeps the set, narrowing wraps into it."""
    if w == v.w:
        return v
    if w > v.w:
        return V(v.lo, v.hi, v.stride, w, v.why)
    return _mk(v.lo, v.hi, v.stride, w, v.why)


def _min_or(a, b, c, d, m):
    """Warren, *Hacker's Delight* 4.3: exact endpoint bounds for a bitwise op."""
    while m:
        if ~a & c & m and (a | m) & -m <= b:
            a = (a | m) & -m
            break
        if a & ~c & m and (c | m) & -m <= d:
            c = (c | m) & -m
            break
        m >>= 1
    return a | c


def _max_or(a, b, c, d, m):
    while m:
        if b & d & m:
            if (b - m) | (m - 1) >= a:
                b = (b - m) | (m - 1)
                break
            if (d - m) | (m - 1) >= c:
                d = (d - m) | (m - 1)
                break
        m >>= 1
    return b | d


def _min_and(a, b, c, d, m):
    while m:
        if ~a & ~c & m:
            if (a | m) & -m <= b:
                a = (a | m) & -m
                break
            if (c | m) & -m <= d:
                c = (c | m) & -m
                break
        m >>= 1
    return a & c


def _max_and(a, b, c, d, m):
    while m:
        if b & ~d & m and (b & ~m) | (m - 1) >= a:
            b = (b & ~m) | (m - 1)
            break
        if ~b & d & m and (d & ~m) | (m - 1) >= c:
            d = (d & ~m) | (m - 1)
            break
        m >>= 1
    return b & d


def _bitop(mn, x, y):
    """``x OP y`` over two unsigned intervals; XOR takes OR's upper bound."""
    w = max(x.w, y.w)
    top = 1 << (8 * w - 1)
    a, b, c, d = x.lo, x.hi, y.lo, y.hi
    why = x.why | y.why
    if mn == "INT_OR":
        return _mk(_min_or(a, b, c, d, top), _max_or(a, b, c, d, top), 1, w, why)
    if mn == "INT_AND":
        return _mk(_min_and(a, b, c, d, top), _max_and(a, b, c, d, top), 1, w, why)
    lo = a ^ c if a == b and c == d else 0
    return _mk(lo, _max_or(a, b, c, d, top), 1, w, why)


def _add(vs):
    lo = hi = s = 0
    why = frozenset()
    for v in vs:
        lo, hi, s, why = lo + v.lo, hi + v.hi, gcd(s, _step(v)), why | v.why
    return _mk(lo, hi, s or 1, max(v.w for v in vs), why)


def _sub(x, y):
    return _mk(x.lo - y.hi, x.hi - y.lo, gcd(_step(x), _step(y)) or 1, max(x.w, y.w), x.why | y.why)


def _shift(mn, x, y, w):
    if y.lo != y.hi or y.hi >= 8 * w:
        return full(w, x.why | y.why | {"width"})
    k = y.lo
    if mn == "INT_LEFT":
        return _mk(x.lo << k, x.hi << k, max(1, x.stride << k), w, x.why | y.why)
    s = x.stride >> k if x.stride > 1 and not x.stride % (1 << k) else 1
    return _mk(x.lo >> k, x.hi >> k, max(1, s), w, x.why | y.why)


_BOOL = frozenset(
    (
        "INT_EQUAL",
        "INT_NOTEQUAL",
        "INT_LESS",
        "INT_LESSEQUAL",
        "INT_SLESS",
        "INT_SLESSEQUAL",
        "INT_CARRY",
        "INT_SCARRY",
        "INT_SBORROW",
    )
)


def apply_op(mn, kids, w):
    """The op's image over its operands' sets, or the width bound naming why not."""
    if mn in _BOOL:
        return V(0, 1, 1, w, frozenset().union(*(k.why for k in kids)))
    if mn == "INT_ADD":
        return _add(kids)
    if mn in ("INT_OR", "INT_AND", "INT_XOR"):
        out = cast(kids[0], w)
        for k in kids[1:]:
            out = _bitop(mn, out, cast(k, w))
        return out
    if mn == "INT_SUB" and len(kids) == 2:
        return _sub(kids[0], kids[1])
    if mn in ("INT_LEFT", "INT_RIGHT") and len(kids) == 2:
        return _shift(mn, cast(kids[0], w), kids[1], w)
    if mn in ("INT_ZEXT", "COPY") and len(kids) == 1:
        return cast(kids[0], w)
    return full(w, frozenset().union(*(k.why for k in kids)) | {"width"})


_DYN_ENTER = ("dgoto", "igoto", "dcall", "dbr", "swc")


def _call_targets(s):
    """Pcs a raw call or an enumerated call dispatch may enter, which need not be entries.

    ASL/04's three per-voice passes are ``JSR $1040``/``JSR $103F`` into labels of the
    calling list itself, so a map built from ``goto`` alone misses them."""
    if s[0] in ("call", "callb"):
        return (s[1],)
    return tuple(int(lbl[1:], 16) for lbl in s[1]) if s[0] == "swc" else ()


class InEdges:
    """Every site that may reach a label, across lists and procedures.

    2c's withdrawal is the specification. Exactly two edges are unenumerable: a raw
    dynamic transfer (R8's ``wall``), which opens every label at once, and an
    RTS-trick landing, which opens the one pc it lands on."""

    __slots__ = ("srcs", "labels", "arms", "calls_in", "gotos", "landings", "wall", "calls")

    def __init__(self, prog, model=None):
        from deity_informant import framefuse
        from deity_informant import frameproc

        self.srcs, self.labels, self.arms, self.calls_in = {}, {}, 0, 0
        self.gotos = set()
        self.landings = frozenset() if model is None else frozenset(framefuse._landings(model))
        dyn, _swg = lift_residue.dyn_counts(prog.procs)
        self.calls = frameproc.Calls(prog.procs, prog.play, self.landings)
        self.wall = "dyn" if dyn or self.calls.open_flow else None
        for entry, _pa, _r, stmts in prog.procs:
            for env, k, s in frameproc.envs(stmts):
                site = (entry, env, k)
                if s[0] == "goto":
                    self.srcs.setdefault(s[1], []).append(site)
                    self.gotos.add(s[1])
                elif s[0] == "label":
                    self.labels.setdefault(s[1], []).append(site)
                else:
                    for pc in _call_targets(s):
                        self.srcs.setdefault(pc, []).append(site)
                        self.calls_in += 1
                    if s[0] in ("swg", "swc", "opsw"):
                        self.arms += len(frameproc._stmt_bodies(s))

    def closed(self, pc):
        """Whether every edge into ``pc`` is one this map names."""
        return self.wall is None and pc not in self.landings

    def reason(self, pc):
        return self.wall or ("landing" if pc in self.landings else None)

    def report(self):
        """The join structure stage 3 consumes: does the map close, and where not."""
        pcs = sorted(set(self.labels) | self.gotos)
        cross = sum(
            1
            for pc, srcs in self.srcs.items()
            for e, _env, _k in srcs
            if any(e2 != e for e2, _e2, _k2 in self.labels.get(pc, ()))
        )
        return {
            "labels": len(pcs),
            "closed": sum(1 for pc in pcs if self.closed(pc)),
            "arms": self.arms,
            "goto_edges": sum(len(v) for v in self.srcs.values()) - self.calls_in,
            "call_edges": self.calls_in,
            "cross_proc_edges": cross,
            "landings": len(self.landings),
            "wall": self.wall,
            "map_closes": self.wall is None and not (self.landings & set(pcs)),
        }


class Walk:
    """The value of any expression at any seat, over locals, with memory at ⊤.

    ``Defs``' backward walk re-asked as a join: at a label the map's in-edges are
    joined rather than required to agree, at an ``if`` the arms are joined rather than
    abandoned, at a procedure entry the call sites are. Every wall names its premise."""

    def __init__(self, prog, model=None, edges=None):
        from deity_informant import frameproc

        self.prog = prog
        self.P = frameproc
        self.edges = edges if edges is not None else InEdges(prog, model)
        self.busy, self.memo, self.scopes = set(), {}, {}

    def _env(self, env, k, body):
        key = (id(env.lst), k, id(body))
        got = self.scopes.get(key)
        if got is None:
            got = self.P.Defs(body, (env, k), env.lst[k][0] in self.P._CYCLIC)
            self.scopes[key] = got
        return got

    def value(self, n, site):
        """What ``n`` may evaluate to at ``site`` = ``(procedure entry, env, seat)``."""
        k = n[0]
        if k == "const":
            return exact(n[1], n[2])
        if k == "loc":
            return self.name(n[1], self.P.loc_width(n), site)
        if k == "mem":
            return full(n[2], ("memory",))
        if k == "op":
            return apply_op(n[1], [self.value(c, site) for c in n[2]], n[3])
        return full(self.P.loc_width(n), ("width",))  # a raw machine reg or a uni input

    def name(self, name, w, site):
        """What local ``name`` may hold at ``site``, joined over every edge into it."""
        entry, env, k = site
        key = (id(env.lst), k, name, w)
        got = self.memo.get(key)
        if got is not None:
            return got
        if key in self.busy:  # a back edge or a recursive call: the only sound answer
            return full(w, ("loop",))
        self.busy.add(key)
        try:
            out = self._scan(name, w, entry, env, k)
        finally:
            self.busy.discard(key)
        self.memo[key] = out
        return out

    def _scan(self, name, w, entry, env, k):
        """Backward over the list, joining every edge that may enter it before ``k``."""
        acc = None
        for j in range(k - 1, -1, -1):
            s = env.lst[j]
            if s[0] == "asg" and s[1] == name:
                return join(acc, cast(self.value(s[2], (entry, env, j)), w))
            got = self.P._label_defs(s)
            if got is None:
                return join(acc, full(w, ("dyn" if s[0] in _DYN_ENTER else "call",)))
            for pc in got[1]:
                acc = join(acc, self._at_label(pc, name, w))
            if name in got[0]:
                return join(acc, self._bound_in(s, name, w, entry, env, j))
        return join(acc, self._above(name, w, entry, env))

    def _above(self, name, w, entry, env):
        """Control arriving from outside this list: the enclosing seat, or the entry."""
        if env.outer is None:
            return self._entry_value(entry, name, w)
        if env.cyclic and self._dirty(env, name):
            return full(w, ("loop",))  # the back edge re-enters with the body's own value
        oenv, ok = env.outer
        s = oenv.lst[ok]
        if s[0] == "for" and s[1] == name:
            return self._counter(s, w)
        return self.name(name, w, (entry, oenv, ok))

    def _counter(self, s, w):
        """A ``for`` counter's may-set: its bounds, with no premise about early exit."""
        return cast(_mk(min(s[2], s[3]), max(s[2], s[3]), 1, 1, frozenset()), w)

    def _dirty(self, env, name):
        """The cyclic body may rebind ``name``, or bind it unseen, anywhere in itself."""
        for s in env.lst:
            got = self.P._label_defs(s)
            if got is None or name in got[0]:
                return True
        return False

    def _bound_in(self, s, name, w, entry, env, j):
        """``s`` binds ``name`` in a nested body: join the arms, and the fall-through."""
        if s[0] == "for" and s[1] == name:
            return self._counter(s, w)
        if s[0] == "pcall" and name in s[3]:
            return full(w, ("call",))
        acc = None
        bodies = self.P._stmt_bodies(s)
        for b in bodies:
            acc = join(acc, self.name(name, w, (entry, self._env(env, j, b), len(b))))
        if not self._must_bind(s, bodies, name):
            acc = join(acc, self.name(name, w, (entry, env, j)))
        return acc if acc is not None else full(w, ("loop",))

    def _must_bind(self, s, bodies, name):
        """No path through ``s`` skips a binding: only a two-armed ``if`` proves it."""
        if s[0] != "if" or len(bodies) != 2 or not all(bodies):
            return False
        for b in bodies:
            got = [self.P._label_defs(x) for x in b]
            if any(g is None for g in got) or not any(name in g[0] for g in got):
                return False
        return True

    def _at_label(self, pc, name, w):
        """The value every enumerated edge into ``pc`` may carry, else ⊤."""
        if not self.edges.closed(pc):
            return full(w, (self.edges.reason(pc),))
        acc = None
        for entry, env, k in self.edges.srcs.get(pc, ()):
            acc = join(acc, self.name(name, w, (entry, env, k)))
        return acc  # None where no goto names the label: it is entered by fall-through

    def _entry_value(self, entry, name, w):
        """A procedure's entry value: what its call sites pass, where the graph closes."""
        args = self.edges.calls.args(entry, name)
        if args is None:
            return full(w, ("call",))
        acc = None
        for caller, env, i, arg in args:
            acc = join(acc, cast(self.value(arg, (caller, env, i)), w))
        return acc if acc is not None else full(w, ("call",))

    def reach(self, s, site):
        """The byte interval a store writes: its address bound plus its own width."""
        from deity_informant import grammar as G

        v = self.value(s[1], site)
        width = G.store_width(s[2])
        return _mk(v.lo, v.hi + width - 1, 1, 2, v.why) if width > 1 else v


def sites(prog):
    """``(entry, env, seat, statement)`` over every statement of every procedure."""
    from deity_informant import frameproc

    for entry, _pa, _r, stmts in prog.procs:
        for env, k, s in frameproc.envs(stmts):
            yield entry, env, k, s


def verdict(v, lo, hi):
    return "bounded" if v.inside(lo, hi) else v.top()


def _harder(a, b):
    """The verdict a phase must break through first; ``bounded`` loses to every ⊤."""
    if a == "bounded" or b == "bounded":
        return b if a == "bounded" else a
    ranks = {c: i for i, c in enumerate(CLASSES)}
    return a if ranks[a] <= ranks[b] else b


def wide_stores(walk, prog):
    """R1/G2: every store whose G1 reach bound leaves the stack, priced per class.

    ``fuse_measure``'s own population and classification; the verdict is whether the
    interval keeps the store inside the stack, which is the bound ``g2_boundable`` is
    named for and no committed analysis proves."""
    import fuse_measure
    from deity_informant import frameproc

    out, prem = {}, Counter()
    for entry, env, k, s in sites(prog):
        if s[0] != "st":
            continue
        base, _idx = frameproc.addr_split(s[1])
        if base is not None or s[1] in prog.resolved:
            continue
        if frameproc.addr_bits(s[1], frameproc.DefsAt(env, k)) <= STACK_TOP:
            continue
        shape = fuse_measure.wide_class(s[1])
        v = walk.reach(s, (entry, env, k))
        got = verdict(v, 0, STACK_TOP)
        out.setdefault(shape, Counter())[got] += 1
        if got != "bounded":
            prem[v.premises()] += 1
    return {k: dict(v) for k, v in out.items()}, dict(prem)


def _modular(addr, at):
    """``(base, index, modulus)`` where an access wraps its page, a bound temp followed.

    ``zp,X`` arrives inline where the address is used once and bound to a temporary
    where it is not, so a rule keyed on the inline spelling is a rule about the
    emitter -- which is what ``lift_residue``'s ``mod_addr`` signature is."""
    from deity_informant import frameproc

    for n in (addr, at.defn(addr) if addr[0] == "loc" else None):
        if n is None:
            continue
        got = frameproc._index_of(n)
        if got is not None and got[2] and frameproc.addr_split(n)[0] is None:
            return got
    return None


def _mod_sites(n, s, at):
    """Every modular address under ``n``, tagged ``store`` where it is the destination."""
    out, stack, seen = [], [n], []
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            seen.append((x[1], "load"))
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    if s[0] == "st" and n is s[1]:
        seen.append((n, "store"))
    for addr, kind in seen:
        got = _modular(addr, at)
        if got is not None:
            out.append((addr, got[2], kind))
    return out


def mod_addrs(walk, prog):
    """Stage 3 threat input: every modular (``zp,X``) access, and its page reach.

    The cell-threat test is an interval one, so the verdict is whether the address is
    strictly narrower than its own wrap; the store half is the threat test's own, and
    ``mod_addr`` counts neither it nor the temp-bound spelling."""
    from deity_informant import frameproc

    out = {"store": Counter(), "load": Counter()}
    prem, reach = Counter(), 0
    for entry, env, k, s in sites(prog):
        at = frameproc.DefsAt(env, k)
        for n in frameproc._stmt_exprs(s):
            for addr, mod, kind in _mod_sites(n, s, at):
                v = walk.value(addr, (entry, env, k))
                span = min(v.span, mod)
                if span < mod:
                    out[kind]["bounded"] += 1
                else:
                    out[kind][v.top()] += 1
                    prem[v.premises()] += 1
                reach += span
    return {k: dict(v) for k, v in out.items()}, dict(prem), reach


def alias_webs(walk, prog):
    """Per web refusing ``web_alias``: whether the walker rules every store out (stage 3 pricing).

    ``ptrcert``'s own alias scan with the interval substituted only where 2a had no
    bound of its own; a web's verdict is the hardest of its stores', since one store
    it cannot place keeps the refusal."""
    from deity_informant import frameproc
    from deity_informant import ptrcert

    cert = ptrcert._Cert(prog).run()
    out, prem = Counter(), Counter()
    for cell, root in sorted(cert.roots.items()):
        if not root.alias:
            continue
        best, why = "bounded", frozenset()
        pair = (cell, frameproc.NOIDX, 0, 2, 0)
        for entry, env, k, s, base, indexed, width in cert.stores:
            through = set(ptrcert.root_cells(s[1]))
            if cell in through or cert._role(root, base, indexed, width) is not None:
                continue
            got = cert._span(through) if through else None
            if got is not None:  # certification bounds this store by its own blocks
                span = (got[0], frameproc.UNRES, max(0, got[1] - got[0] - 1), width, 0)
                if frameproc.overlaps(span, pair):
                    best, why = _harder(best, "top_memory"), why | {"memory"}
                continue
            v = walk.reach(s, (entry, env, k))
            if frameproc.overlaps((v.lo, frameproc.UNRES, v.span - 1, 1, 0), pair):
                best = _harder(best, v.top())
                why |= v.why
        out[best] += 1
        if best != "bounded":
            prem["|".join(r for r in ORDER if r in why)] += 1
    return dict(out), dict(prem)


def _deref_addrs(n):
    out, stack = [], [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            out.append(x[1])
            stack.append(x[1])
        elif x[0] == "op":
            stack.extend(x[2])
    return out


def foreign_webs(walk, prog, rec):
    """The ``foreign`` half of ``extent_unmappable``, priced per web (stage 3 pricing).

    A static extent exists exactly where the walker bounds the web's own deref
    addresses, so this is where a locals-only domain is asked to prove it is enough."""
    from deity_informant import frameproc
    from deity_informant import ptrcert

    want = {int(r["root"][1:], 16) for r in rec if r.get("unmappable_foreign")}
    out = Counter()
    if not want:
        return {}, {}
    per = dict.fromkeys(want)
    for entry, env, k, s in sites(prog):
        for n in frameproc._stmt_exprs(s):
            for addr in _deref_addrs(n):
                for c in set(ptrcert.root_cells(addr)) & want:
                    per[c] = join(per[c], walk.value(addr, (entry, env, k)))
    prem = Counter()
    for c in sorted(want):
        v = per[c]
        got = "top_memory" if v is None else verdict(v, 0, 0xFFFE)
        out[got] += 1
        if got != "bounded":
            prem["memory" if v is None else v.premises()] += 1
    return dict(out), dict(prem)


class Observer:
    """Every concrete address a seat resolved, keyed by the expression that spelled it.

    §3's guard: a value the run reaches outside a claimed static bound is an analysis
    bug found before any phase leans on it."""

    __slots__ = ("hits",)

    def __init__(self):
        self.hits = {}

    def __call__(self, addr, f, width):
        if addr[0] == "const":
            return f
        sink = self.hits.setdefault(addr, set())

        def observe(r, m, rd):
            a = f(r, m, rd)
            sink.add(a)
            return a

        return observe


def _addr_exprs(s):
    from deity_informant import frameproc

    out = []
    for n in frameproc._stmt_exprs(s):
        out.extend(_deref_addrs(n))
    if s[0] == "st":
        out.append(s[1])
    return out


def guard(walk, prog, model, frames):
    """The static bounds against the run: a contradiction stops the line, slack counts."""
    from deity_informant import frameprog
    from deity_informant import frameval

    obs = Observer()
    trace, _walker = frameprog.iota(model, frames)
    ev = frameval.Evaluator(prog, trace, probe=obs)
    ran = 0
    for f in range(frames):
        ev.frame = f
        try:
            ev.run_frame()
        except frameval.FrameFault:
            break
        ran = f + 1
    hull = {}
    for entry, env, k, s in sites(prog):
        for n in _addr_exprs(s):
            if n in obs.hits:
                hull[n] = join(hull.get(n), walk.value(n, (entry, env, k)))
    bad, slack, checked = [], 0, 0
    for n, seen in obs.hits.items():
        v = hull.get(n)
        if v is None:
            continue
        checked += 1
        outside = sorted(a for a in seen if not v.lo <= a <= v.hi)
        if outside:
            bad.append({"addr": ["$%04X" % a for a in outside[:4]], "bound": repr(v)})
        elif v.span > len(seen):
            slack += 1
    return {"frames": ran, "checked": checked, "contradictions": bad, "over_refused": slack}


def row(model, prog, extent_rec=(), frames=0):
    """One tune's verdicts: the testable core, with no corpus around it."""
    edges = InEdges(prog, model)
    walk = Walk(prog, model, edges)
    mods, mod_prem, reach = mod_addrs(walk, prog)
    wide, wide_prem = wide_stores(walk, prog)
    alias, alias_prem = alias_webs(walk, prog)
    foreign, foreign_prem = foreign_webs(walk, prog, extent_rec)
    out = {
        "edges": edges.report(),
        "wide": wide,
        "mod_addr": mods,
        "mod_reach": reach,
        "web_alias": alias,
        "extent_foreign": foreign,
        "premises": {
            "wide": wide_prem,
            "mod_addr": mod_prem,
            "web_alias": alias_prem,
            "extent_foreign": foreign_prem,
        },
    }
    if frames:
        out["guard"] = guard(walk, prog, model, frames)
    return out


def one(entry, extents=None, frames=0):
    """One tune's row, or the exception that stopped it."""
    try:
        signal.alarm(_sweep.CAP_S)
        return _one(entry, extents, frames)
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _one(entry, extents, frames):
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F
    full_frames = int(secs * 50)
    t0 = time.monotonic()
    model, prog, _ev = _sweep.build(mem, init, play, full_frames, sub)
    got = row(model, prog, extents or (), min(frames, full_frames) if frames else 0)
    return {**_sweep.row_head(entry), "walk_s": round(time.monotonic() - t0, 1), **got}


def _sum(done, key):
    out = Counter()
    for r in done:
        out.update(r[key])
    return dict(out)


def totals(done):
    """Corpus totals: the in-edge closure, then one verdict histogram per customer."""
    edges = [r["edges"] for r in done]
    wide, per_shape = Counter(), {}
    for r in done:
        for shape, per in r["wide"].items():
            per_shape.setdefault(shape, Counter()).update(per)
            wide.update(per)
    out = {
        "tunes": len(done),
        "in_edges": {
            "labels": sum(e["labels"] for e in edges),
            "closed": sum(e["closed"] for e in edges),
            "arms": sum(e["arms"] for e in edges),
            "goto_edges": sum(e["goto_edges"] for e in edges),
            "call_edges": sum(e["call_edges"] for e in edges),
            "cross_proc_edges": sum(e["cross_proc_edges"] for e in edges),
            "landings": sum(e["landings"] for e in edges),
            "tunes_closing": sum(1 for e in edges if e["map_closes"]),
            "tunes_walled": sum(1 for e in edges if e["wall"]),
        },
        "wide_stores": dict(wide),
        "wide_by_shape": {k: dict(v) for k, v in sorted(per_shape.items())},
        "mod_addr": {
            k: dict(sum((Counter(r["mod_addr"][k]) for r in done), Counter()))
            for k in ("store", "load")
        },
        "mod_reach": sum(r["mod_reach"] for r in done),
        "web_alias": _sum(done, "web_alias"),
        "extent_foreign": _sum(done, "extent_foreign"),
        "premises": {
            k: dict(sum((Counter(r["premises"][k]) for r in done), Counter()))
            for k in ("wide", "mod_addr", "web_alias", "extent_foreign")
        },
    }
    got = [r["guard"] for r in done if "guard" in r]
    if got:
        out["guard"] = {
            "tunes": len(got),
            "checked": sum(x["checked"] for x in got),
            "contradictions": sum(len(x["contradictions"]) for x in got),
            "over_refused": sum(x["over_refused"] for x in got),
            "tunes_contradicting": sum(1 for x in got if x["contradictions"]),
        }
    return out


def _extent_rows(path):
    """b0's per-web records per tune, empty where the artifact is not in tree."""
    p = Path(path)
    if not p.exists():
        return {}
    art = json.loads(p.read_text(encoding="utf-8"))
    return {r["tune"]: r["extents"]["records"] for r in art["rows"] if "extents" in r}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "value_walk.json"))
    ap.add_argument(
        "--extents",
        default=str(ROOT / "out" / "ptr_extents.json"),
        help="Phase 2b (b0) artifact: names the webs whose observed extent is foreign",
    )
    ap.add_argument(
        "--guard",
        type=int,
        default=0,
        help="run the divergence guard against a run of this many frames per tune",
    )
    args = ap.parse_args()

    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    art = _extent_rows(args.extents)
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        jobs = [(e, art.get(_sweep.tune_id(e[0]), ()), args.guard) for e in tunes]
        rows = _sweep.check_rows(pool.starmap(one, jobs))
    done = [r for r in rows if "error" not in r]
    out = {
        "refused": [r for r in rows if "error" in r],
        "wall_s": round(time.monotonic() - t0, 1),
        **totals(done),
        "rows": rows,
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
    return 1 if out.get("guard", {}).get("contradictions") else 0


if __name__ == "__main__":
    sys.exit(main())
