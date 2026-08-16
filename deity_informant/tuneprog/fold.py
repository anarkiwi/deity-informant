"""S6 -- outlining: a run of blocks with one role, or shared by two procedures.

A candidate is a single-entry single-exit run of blocks whose statements have one
dominant role; two runs print as one helper when their alpha-renamed form is
equal, so the text is the same program with the copy named once.
"""

from __future__ import annotations

from collections import Counter

import networkx as nx

from .ir import Block, Call, Const, Goto, If, Let, Load, Proc, Return, Store, Switch, Var, succs
from .inline import _natural_loops
from .ssa import defs_of, preds_of, prune, stmt_uses, term_uses
from .word import R16, W16, uses16

EXIT = "$exit"
ORDER = ("cascades", "oscillator", "writeout", "filter", "row_advance")
SID_LO, SID_HI = 0xD400, 0xD418
CUTOFF = (0xD415, 0xD416)
MINSTMT = 5


# ---- regions -----------------------------------------------------------------
def cfg(proc):
    """The control-flow graph with a virtual exit every return reaches."""
    g = nx.DiGraph()
    g.add_nodes_from(proc.blocks)
    g.add_node(EXIT)
    for lbl, b in proc.blocks.items():
        for s in succs(b.term):
            g.add_edge(lbl, s)
        if type(b.term) is Return:
            g.add_edge(lbl, EXIT)
    return g


def chain(g, root, stop):
    """The blocks every path from ``root`` to ``stop`` passes through, in order."""
    if root not in g or stop not in g:
        return []
    idom = nx.immediate_dominators(g, root)
    if stop not in idom:
        return []
    out, cur = [], stop
    while cur != root:
        out.append(cur)
        nxt = idom[cur]
        if nxt == cur:
            return []
        cur = nxt
    return [root] + out[::-1]


def span(proc, h, x):
    """The blocks ``h`` reaches without passing ``x``."""
    out, work = set(), [h]
    while work:
        cur = work.pop()
        if cur in out or cur == x or cur not in proc.blocks:
            continue
        out.add(cur)
        work.extend(succs(proc.blocks[cur].term))
    return out


def sese(proc, preds, blocks, h, x):
    """True when ``blocks`` is entered only at ``h`` and left only at ``x``."""
    for lbl in blocks:
        if lbl != h and any(p not in blocks for p in preds[lbl]):
            return False
        for s in succs(proc.blocks[lbl].term):
            if s not in blocks and s != x:
                return False
        if x == EXIT and lbl != h and type(proc.blocks[lbl].term) is Return:
            continue
    return not (x != EXIT and any(type(proc.blocks[l].term) is Return for l in blocks))


# ---- roles -------------------------------------------------------------------
def written(s):
    t = type(s)
    if t is W16:
        return {s.lo, s.hi}
    return {s.r} if t is Store and s.r >= 0 else set()


def loaded(s):
    out = set()
    for e in (getattr(s, "e", None), getattr(s, "a", None), getattr(s, "v", None)):
        if e is not None:
            for x in _walk(e):
                if type(x) is Load:
                    out.add(x.r)
                elif type(x) is R16:
                    out |= {x.lo, x.hi}
    return out


def _walk(e):
    yield e
    t = type(e)
    if t is Load or t is R16:
        yield from _walk(e.a)
    elif t is not Const and t is not Var and hasattr(e, "b"):
        yield from _walk(e.a)
        yield from _walk(e.b)


class Roles:
    """The role of one statement, from the recovered region roles."""

    def __init__(self, prog, names):
        self.names = names
        self.decoder = {p for p, n in names.procs.items() if n.startswith("row_apply")}
        self.freq = {r for r, k in names.role.items() if k == "freq_table"}
        self.image = {r for r, k in names.role.items() if k == "sid_image"}
        self.step = {r for r, k in names.role.items() if k in ("timer", "cursor", "ptr")}
        self.filt = {r for p, g in names.u16group.items() if g == "filter" for r in p}
        self.prog = prog

    def io(self, s):
        """``writeout``/``filter`` for a SID register store: the runs a block splits at."""
        if type(s) is Store and s.cls == "io" and type(s.a) is Const and SID_LO <= s.a.v <= SID_HI:
            return "filter" if s.a.v in CUTOFF else "writeout"
        return ""

    def of(self, s):
        """The role a statement carries, or ``''``."""
        t = type(s)
        if t is Call:
            return "cascades" if s.proc in self.decoder else ""
        if self.io(s):
            return self.io(s)
        wr, rd = written(s), loaded(s)
        if wr & self.filt or rd & self.filt:
            return "filter"
        if rd & self.freq and wr & self.image:
            return "oscillator"
        return "row_advance" if wr & self.step else ""

    def role(self, proc, blocks):
        """The dominant role of a run of blocks."""
        seen = {self.of(s) for lbl in blocks for s in proc.blocks[lbl].stmts} - {""}
        return next((r for r in ORDER if r in seen), "")


def split_roles(prog, names):
    """Split a block where its statements change role, so a run can take one."""
    roles, n = Roles(prog, names), 0
    for proc in prog.procs.values():
        for lbl in list(proc.blocks):
            b = proc.blocks[lbl]
            marks = _marks([roles.io(s) for s in b.stmts])
            cuts = [i for i in range(1, len(marks)) if marks[i] != marks[i - 1]]
            if marks and marks[-1] != "$tail" and type(b.term) in (If, Switch):
                cuts.append(len(marks))
            for k, i in enumerate(reversed(cuts)):
                tail = "%s$r%d" % (lbl, len(cuts) - k)
                proc.blocks[tail] = Block(tail, b.stmts[i:], b.term, b.src, b.count)
                b = Block(lbl, b.stmts[:i], Goto(tail), b.src, b.count)
                n += 1
            proc.blocks[lbl] = b
    return n


def _marks(rs):
    """The role each statement belongs to; the role-free tail is its own run."""
    out, cur = [], ""
    for r in rs:
        cur = r or cur
        out.append(cur)
    last = max((i for i, r in enumerate(rs) if r), default=-1)
    return out[: last + 1] + ["$tail"] * (len(rs) - last - 1)


# ---- candidates --------------------------------------------------------------
class Cand:
    """One outlining candidate: a run of blocks in one procedure."""

    __slots__ = ("proc", "role", "entry", "exit", "blocks", "skip", "depth")

    def __init__(self, proc, role, entry, exit_lbl, blocks, depth):
        self.proc = proc
        self.role = role
        self.entry = entry
        self.exit = exit_lbl
        self.blocks = blocks
        self.skip = 0
        self.depth = depth

    def stmts(self, prog):
        p = prog.procs[self.proc]
        return sum(len(p.blocks[l].stmts) for l in self.blocks) - self.skip


def candidates(prog, names):
    """Every role-named run in the program, deepest first."""
    roles, out = Roles(prog, names), []
    hot = _hot(prog)
    for pname, proc in prog.procs.items():
        if pname not in hot:
            continue
        g, preds = cfg(proc), preds_of(proc)
        graph = (g, preds, set(), _loops(proc, g, preds))
        _chains(prog, roles, pname, proc, (proc.entry, EXIT), graph, 0, out)
    return out


def _hot(prog):
    """The procedures a tick reaches: init-only code is not worth a helper."""
    seen, work = set(), [prog.meta.get("tick_proc")]
    while work:
        n = work.pop()
        if n in seen or n not in prog.procs:
            continue
        seen.add(n)
        work += [s.proc for b in prog.procs[n].blocks.values() for s in b.stmts if type(s) is Call]
    return seen


def _loops(proc, g, preds):
    """Every block inside a natural loop: a chain never cuts one in half."""
    g = g.subgraph([n for n in g if n != EXIT])
    idom = nx.immediate_dominators(g, proc.entry)
    out = set()
    for body, _latches in _natural_loops(g, idom, preds).values():
        out |= body
    return out


def _chains(prog, roles, pname, proc, ends, graph, depth, out):
    """Collect candidates on the chain ``root``..``stop``, its nested chains first."""
    root, stop = ends
    g, preds, seen, loops = graph
    if ends in seen or depth > 24:
        return
    seen.add(ends)
    seq = [c for c in chain(g, root, stop) if c not in loops or c in (root, stop)]
    atoms = [(a, b, span(proc, a, b)) for a, b in zip(seq, seq[1:])]
    for a, b, blocks in atoms:
        for lbl in sorted(blocks):
            kids = succs(proc.blocks[lbl].term)
            for k in kids if len(kids) > 1 else ():
                if k in blocks and preds[k] == [lbl] and k != root:
                    _chains(prog, roles, pname, proc, (k, b), graph, depth + 1, out)
    run = []
    for a, b, blocks in atoms + [(None, None, None)]:
        ok = blocks is not None and sese(proc, preds, blocks, a, b)
        role = roles.role(proc, blocks) if ok else ""
        if not ok or (role and run and run[0][0] != role):
            _emit(prog, pname, run, depth, out)
            run = []
        if ok and (role or run):
            run.append((role, a, b, blocks))
    _emit(prog, pname, run, depth, out)


def _emit(prog, pname, run, depth, out):
    """One candidate from a run of atoms, trailing role-free ones trimmed."""
    while run and not run[-1][0]:
        run.pop()
    if not run:
        return
    blocks = set().union(*[b for _r, _a, _x, b in run])
    cand = Cand(pname, run[0][0], run[0][1], run[-1][2], blocks, depth)
    if cand.stmts(prog) >= MINSTMT:
        out.append(cand)


# ---- structural identity -----------------------------------------------------
def canon(proc, cand, params=None):
    """A fingerprint of a candidate: block order, statements, names alpha-renamed.

    The machine's own frame pushes are left out: they differ by the call depth and
    the printer never shows them.
    """
    order, ren = _order(proc, cand), {}
    idx = {l: i for i, l in enumerate(order)}
    out = []
    for i, lbl in enumerate(order):
        b = proc.blocks[lbl]
        for s in b.stmts[cand.skip if i == 0 else 0 :]:
            if type(s) is not Store or s.cls != "raw":
                out.append(_cstmt(s, ren, params or {}))
        out.append(_cterm(b.term, ren, idx, cand))
    return tuple(out)


def _order(proc, cand):
    out, work = [], [cand.entry]
    while work:
        lbl = work.pop()
        if lbl in out or lbl not in cand.blocks:
            continue
        out.append(lbl)
        work.extend(reversed(succs(proc.blocks[lbl].term)))
    return out


def _name(n, ren):
    return ren.setdefault(n, "$%d" % len(ren))


def _cexpr(e, ren):
    t = type(e)
    if t is Const:
        return ("k", e.v)
    if t is Var:
        return ("v", _name(e.n, ren))
    if t is Load:
        return ("l", e.cls, e.r, e.w, _cexpr(e.a, ren))
    if t is R16:
        return ("r16", e.lo, e.hi, _cexpr(e.a, ren))
    return ("b", e.op, e.w, _cexpr(e.a, ren), _cexpr(e.b, ren))


def _cstmt(s, ren, params):
    t = type(s)
    if t is Let:
        return ("let", _name(s.n, ren), _cexpr(s.e, ren))
    if t is Store:
        return ("st", s.cls, s.r, s.w, s.src, _cexpr(s.a, ren), _cexpr(s.v, ren))
    if t is W16:
        return ("w16", s.lo, s.hi, s.src, _cexpr(s.a, ren), _cexpr(s.e, ren))
    if t is Call:
        keep = params.get(s.proc)
        args = s.args if keep is None else [a for a, i in zip(s.args, keep) if i]
        return ("call", s.proc, tuple(_cexpr(a, ren) for a in args), len(s.rets))
    return (type(s).__name__,)


def _cterm(t, ren, idx, cand):
    def lbl(l):
        return idx.get(l, "out" if l == cand.exit else l)

    k = type(t)
    if k is Goto:
        return ("goto", lbl(t.to))
    if k is Return:
        return ("ret",)
    if k is If:
        return ("if", _cexpr(t.c, ren), lbl(t.t), lbl(t.f))
    if k is Switch:
        return ("sw", _cexpr(t.e, ren), tuple((v, lbl(l)) for v, l in t.cases))
    return ("trap", t.why)


# ---- data flow ---------------------------------------------------------------
def crossing(prog, cand, live):
    """The live values that enter or leave the run; a helper needs them empty."""
    proc = prog.procs[cand.proc]
    inside, used_in, outside = set(), set(), set()
    for lbl, b in proc.blocks.items():
        first = cand.skip if lbl == cand.entry else 0
        for i, s in enumerate(b.stmts):
            here = lbl in cand.blocks and i >= first
            (inside if here else outside).update(defs_of(s))
            (used_in if here else outside).update(_uses(s, set()))
        if lbl in cand.blocks:
            term_uses(b.term, used_in)
        else:
            term_uses(b.term, outside)
    keep = {n for n in live.get(cand.proc, set()) if not n.startswith("$")}
    return ((inside & outside) | (used_in - inside)) & keep


def _uses(s, out):
    if type(s) is W16:
        uses16(s.a, out)
        uses16(s.e, out)
        return out
    return stmt_uses(s, out)


# ---- outlining ---------------------------------------------------------------
def select(cands):
    """The outermost candidates: one whose inside disagrees on the role is dropped."""
    out = []
    for c in sorted(cands, key=lambda c: (c.depth, -len(c.blocks), c.proc, c.entry)):
        inner = [d for d in cands if d is not c and d.blocks < c.blocks]
        if {d.role for d in inner} - {c.role}:
            continue
        if any(c.blocks & s.blocks for s in out if s.proc == c.proc):
            continue
        out.append(c)
    return out


def livearg(prog, params):
    """``{proc: [is this argument printed]}`` -- the rest is machine plumbing."""
    return {n: [i in params.get(n, ()) for i in p.params] for n, p in prog.procs.items()}


def outline(prog, names, live, params=None):
    """Replace every accepted candidate by a call to a helper procedure."""
    split_roles(prog, names)
    keep = livearg(prog, params or {})
    groups = {}
    for c in select(candidates(prog, names)):
        if crossing(prog, c, live):
            continue
        key = _match(prog, c, groups, keep)
        if key not in groups and _whole(prog, c):
            continue
        groups.setdefault(key, []).append(c)
    out, count = {}, Counter(c.proc for g in groups.values() for c in g)
    worth = [g for g in groups.values() if len(g) > 1 or count[g[0].proc] > 1]
    for group in sorted(worth, key=lambda g: g[0].role):
        group.sort(key=lambda c: (c.skip, c.proc))
        name = _unique(prog, group[0].role)
        for c in group:
            _extract(prog, c, name, name in out)
            out[name] = group
        names.procs[name] = name
    return out


def _match(prog, c, groups, keep):
    """The group of an equal run, aligning whichever entry block has a prologue."""
    for key, group in list(groups.items()):
        other = group[0]
        if other.role != c.role or other.proc == c.proc:
            continue
        d = _entrylen(prog, c) - _entrylen(prog, other)
        if d >= 0:
            c.skip = d
            if canon(prog.procs[c.proc], c, keep) == key:
                return key
            c.skip = 0
        elif len(group) == 1:
            other.skip = -d
            got = canon(prog.procs[other.proc], other, keep)
            if canon(prog.procs[c.proc], c, keep) == got:
                groups[got] = groups.pop(key)
                return got
            other.skip = 0
    return canon(prog.procs[c.proc], c, keep)


def _whole(prog, c):
    """A run that is its whole procedure: naming it again buys the reader nothing."""
    p = prog.procs[c.proc]
    return c.entry == p.entry and not c.skip and len(c.blocks) == len(p.blocks)


def _entrylen(prog, c):
    return len(prog.procs[c.proc].blocks[c.entry].stmts) - c.skip


def _unique(prog, want):
    out, i = want, 2
    while out in prog.procs:
        out, i = "%s%d" % (want, i), i + 1
    return out


def _extract(prog, cand, name, exists):
    """Move the run into ``name`` (once) and leave a call behind."""
    proc = prog.procs[cand.proc]
    entry = proc.blocks[cand.entry]
    if not exists:
        blocks = {l: proc.blocks[l] for l in cand.blocks}
        blocks[cand.entry] = Block(
            cand.entry, entry.stmts[cand.skip :], entry.term, entry.src, entry.count
        )
        prog.procs[name] = Proc(name, (), (), blocks, cand.entry, "helper")
        _returns(prog.procs[name], cand.exit)
    for lbl in cand.blocks:
        if lbl != cand.entry:
            del proc.blocks[lbl]
    term = Return() if cand.exit == EXIT else Goto(cand.exit)
    proc.blocks[cand.entry] = Block(
        cand.entry, entry.stmts[: cand.skip] + [Call(name)], term, entry.src, entry.count
    )
    prune(proc)


def _returns(proc, exit_lbl):
    """Every edge that left the run returns from the helper instead."""
    for b in list(proc.blocks.values()):
        if type(b.term) is Goto and b.term.to == exit_lbl:
            b.term = Return()
        elif exit_lbl in succs(b.term):
            proc.blocks.setdefault(EXIT, Block(EXIT, [], Return()))
            b.term = _retarget(b.term, exit_lbl, EXIT)


def _retarget(term, old, new):
    k = type(term)
    if k is If:
        return If(term.c, new if term.t == old else term.t, new if term.f == old else term.f)
    if k is Switch:
        return Switch(
            term.e,
            tuple((v, new if l == old else l) for v, l in term.cases),
            new if term.default == old else term.default,
        )
    return term
