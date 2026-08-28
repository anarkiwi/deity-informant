"""T3 -- the fetch as producers: what a region stores, over row bytes and cells.

A region's stores, opened by :mod:`.resolve` over the region's own definitions, are
expressions over the *entry* state and the score bytes read (:class:`Byte`); the
player applies those whose guards hold. What does not open so is a named refusal.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tuneprog.accshape import Ctx, terms
from ..tuneprog.cellref import INDEX_MAX
from ..tuneprog.graph import cfg, rpo
from ..tuneprog.ir import Bin, Call, Const, If, Load, R16, REGVAR, Return, Store, Switch, Var
from ..tuneprog.ir import evalbin, succs
from ..tuneprog.irwalk import addr_split, node_exprs, walk
from .cursors import _cursor
from .refuse import Refusal
from .region import _control
from .resolve import Program, Sel, _renamer, _subst

DEPTH = 16
CUR = "$cur:"
PATH = "$path:"
CMP = ("==", "!=", "<", "<=")


def _and(a, b):
    return b if a == Const(1) else a if b == Const(1) else Bin("and", a, b)


def _or(a, b):
    if a is None:
        return b
    return Const(1) if Const(1) in (a, b) else Bin("or", a, b)


def _truth(c, t):
    """A branch condition as the 0/1 fact its taken (``t``) or untaken edge states."""
    if type(c) is Bin and c.op in CMP:
        return c if t else Bin("==", c, Const(0))
    return Bin("!=" if t else "==", c, Const(0))


@dataclass(frozen=True, slots=True)
class Byte:
    """Channel ``chan``'s score byte at ``cursor + origin``, the cursor as opened at the site."""

    chan: str
    cursor: object
    origin: int


def channels_of(t2, view, names):
    """``{table: {cursor, addr, stride, role, base, lo, hi}}`` over T2's score channels."""
    regs = {names.of(r.id): r for r in view.storage if r.id >= 0}
    out = {}
    for v in t2["score"]:
        for role in ("order", "pattern"):
            for ch in v.get(role, ()):
                t = regs.get(ch["table"])
                if ch["table"] in out or t is None or ch["cursor"].count("@$") != 1:
                    continue
                name, addr = ch["cursor"].split("@$")
                r = regs.get(name)
                stride = 1
                for g in (names.groups or {}).values():
                    if r is not None and (r.id in g.get("members", ()) or g.get("split") == r.id):
                        stride = max(int(g.get("stride", 1)), 1)
                bases = {e["base"] for e in ch.get("events", ())}
                out[ch["table"]] = {
                    "table": ch["table"],
                    "cursor": ch["cursor"],
                    "addr": int(addr, 16),
                    "stride": stride,
                    "role": role,
                    "base": bases.pop() if len(bases) == 1 and ch["depth"] == 0 else None,
                    "lo": t.base,
                    "hi": t.base + t.size - 1,
                }
    return out


def _walk(e):
    """Every node of an opened expression, :class:`Byte` cursors and selection guards included."""
    stack = [e]
    while stack:
        x = stack.pop()
        yield x
        t = type(x)
        if t is Sel:
            for gs, y in x.alts:
                stack.append(y)
                stack.extend(c for c, *_r in gs)
        elif t is Bin:
            stack += [x.a, x.b]
        elif t is Load or t is R16:
            stack.append(x.a)
        elif t is Byte:
            stack.append(x.cursor)


def evaluate(e, F, rd, byte):
    """An entry-relative expression: ``rd(addr, w, cls)`` reads, ``byte(chan, pos)`` a row byte."""
    t = type(e)
    if t is Const:
        return e.v
    if t is Var:
        return F[e.n]
    if t is Bin and e.op in ("and", "or"):
        a = evaluate(e.a, F, rd, byte) != 0
        if a != (e.op == "and"):
            return int(a)
        return int(evaluate(e.b, F, rd, byte) != 0)
    if t is Bin:
        return evalbin(e.op, evaluate(e.a, F, rd, byte), evaluate(e.b, F, rd, byte), e.w)
    if t is Load:
        return rd(evaluate(e.a, F, rd, byte), e.w, e.cls)
    if t is R16:
        return rd(evaluate(e.a, F, rd, byte), 2, "ram")
    if t is Byte:
        return byte(e.chan, evaluate(e.cursor, F, rd, byte) + e.origin)
    if t is Sel:
        for gs, x in reversed(e.alts):
            if all((evaluate(c, F, rd, byte) != 0) == bool(tv) for c, tv, *_w in gs):
                return evaluate(x, F, rd, byte)
    raise KeyError(repr(e))


class Fetches:
    """Every region's producers, exits and live-outs, derived once from the program.

    ``out[key]``: ``items`` (stores and live-out lets in apply order, each under its
    region guards), ``exits`` (each edge out under its guards), ``chans`` (per channel
    the cursor's entry value, its cell address and the base, entry-relative), ``refusals``.
    """

    def __init__(self, prog, names, F, chans, namer):
        self.prog, self.rgn, self.F = prog, prog.by_id(), F
        self.chans, self.namer = chans, namer
        self.ctx = Ctx(prog, names)
        self.curval = {}
        self.copyvars = self.indices()
        self.out = {key: self.region(r) for key, r in F.regions.items()}

    def indices(self):
        """The names indexing a channel's cursor cell anywhere in the regions' procs: the copies."""
        addrs = {c["addr"] for c in self.chans.values()}
        stmts = [
            s
            for proc in {r.proc for r in self.F.regions.values()}
            for b in self.prog.procs[proc].blocks.values()
            for s in list(b.stmts) + [b.term]
        ]
        cells = [x.a for s in stmts for e in node_exprs(s) for x in walk(e) if type(x) is Load]
        cells += [s.a for s in stmts if type(s) is Store]
        return {y.n for a in cells if addr_split(a)[0] in addrs for y in walk(a) if type(y) is Var}

    # ---- the region --------------------------------------------------------------
    def region(self, r):
        P = Program(self.ctx, indexed=True, only=(r.proc, r.blocks))
        R = self.resolver(P, r.proc, r.blocks)
        p = self.prog.procs[r.proc]
        D = {"items": [], "exits": [], "chans": {}, "refusals": [], "unset": [], "paths": {}}
        D["res"] = {r.proc: (R, None)}
        params = {REGVAR[i] for i in p.params} | self.copyvars
        self.callees(D, P, R, r)
        heads = {h for l in r.blocks for h in R.inloop.get(l, ()) if h in r.blocks}
        for lbl in (l for l in rpo(p) if l in r.blocks):
            b = p.blocks[lbl]
            site = "%s:%s" % (r.proc, lbl)
            try:
                gs = [(self.cond(D, R, params, lbl), True)]
            except Refusal as x:
                D["refusals"].append(Refusal(x.why, site, site, x.detail))
                continue
            loop = any(h in R.inloop.get(lbl, ()) or h in R.dom[lbl] for h in heads)
            for i, s in enumerate(b.stmts):
                if type(s) is Store:
                    self.store(D, R, r, params, lbl, i, s, gs, loop)
                elif type(s) is Call:
                    self.callee(D, P, R, r, params, lbl, i, s, gs)
            try:
                self.exits(D, R, r, params, lbl, b.term, gs)
            except Refusal as x:
                D["refusals"].append(Refusal(x.why, site, site, x.detail))
        for n in r.liveout:
            self.let(D, R, r, params, n)
        D["order"] = sorted(
            D["chans"],
            key=lambda t: sum(1 for y in _walk(D["chans"][t]["base"]) if type(y) is Byte),
        )
        return D

    def resolver(self, P, proc, blocks):
        """The proc's resolver with each block's guards one marker: its path condition."""
        R = P.of(proc)
        R.ctl = _control(R.proc, cfg(R.proc))
        R.blocks = blocks
        addrs = {c["addr"] for c in self.chans.values()}
        R.keys = {k: v for k, v in R.mem.items() if k[1] in addrs}
        R.mark = lambda x, lbl, i: self.mark(R, lbl, i, x)
        R.sites = {
            l: ((l, Var("%s%s/%s" % (PATH, proc, l)), True, frozenset()),) if l in blocks else ()
            for l in R.proc.blocks
        }
        return R

    def callees(self, D, P, R, r):
        """A proc called inside the region is fetched with it: its resolver, its arguments."""
        p = self.prog.procs[r.proc]
        for q in sorted(r.callees):
            calls = [
                (l, i, s)
                for l in r.blocks
                for i, s in enumerate(p.blocks[l].stmts)
                if type(s) is Call and s.proc == q
            ]
            if len({repr(s.args) for _l, _i, s in calls}) != 1 or q not in self.prog.procs:
                D["refusals"].append(Refusal("fetch not in IR", q, r.proc, "called twice"))
                continue
            l, i, s = calls[0]
            args = {
                REGVAR[k]: R.open(a, l, i, DEPTH) for k, a in zip(self.prog.procs[q].params, s.args)
            }
            D["res"][q] = (
                self.resolver(P, q, frozenset(self.prog.procs[q].blocks)),
                _renamer(args),
            )

    def unmark(self, D, params, x):
        """Every path marker replaced by the block's condition, in its own procedure."""

        def fn(y):
            if type(y) is not Var or not y.n.startswith(PATH):
                return y
            proc, lbl = y.n[len(PATH) :].split("/", 1)
            Rq, fq = D["res"][proc]
            return self.cond(D, Rq, params, lbl, fq)

        return _subst(x, fn)

    def cond(self, D, R, params, lbl, fn=None):
        """Whether the block runs once the region is entered: over its control dependences.

        The disjunction over the edges the block depends on of the decider's own
        condition and the edge's outcome, each opened at the decider's end.
        """
        key = (R.name, lbl)
        if key not in D["paths"]:
            D["paths"][key] = Const(1)
            out = None
            for d, k in sorted(R.ctl.get(lbl, ())):
                if d not in R.blocks:
                    continue
                term = R.proc.blocks[d].term
                if type(term) is If:
                    e = _truth(self.expr(D, R, params, d, None, term.c, fn), k == 0)
                else:
                    e = Bin(
                        "==", self.expr(D, R, params, d, None, term.e, fn), Const(term.cases[k][0])
                    )
                out = _or(out, _and(self.cond(D, R, params, d, fn), e))
            D["paths"][key] = Const(1) if out is None else out
        return D["paths"][key]

    def store(self, D, R, r, params, lbl, i, s, gs, loop, fn=None):
        site = "%s:%s" % (r.proc, lbl)
        try:
            if loop:
                raise Refusal(
                    "fetch not in IR", site, site, "a store inside a loop the fetch walks"
                )
            v = self.expr(D, R, params, lbl, i, s.v, fn)
            a = self.expr(D, R, params, lbl, i, s.a, fn)
        except Refusal as x:
            cell = self.namer.cell(s.lo) if s.cls != "io" else "sid[%d]" % (s.lo - 0xD400)
            D["refusals"].append(Refusal(x.why, cell, site, x.detail))
            return
        D["items"].append(
            {
                "kind": "store",
                "when": gs,
                "addr": a,
                "value": v,
                "cls": s.cls,
                "w": s.w,
                "lo": s.lo,
                "hi": s.hi,
                "site": site,
            }
        )

    def callee(self, D, P, R, r, params, lbl, i, s, gs):
        """The callee's stores at the call, under the call's guards and its own."""
        site = "%s:%s" % (r.proc, lbl)
        del P, R, i
        if s.proc not in D["res"]:
            D["refusals"].append(Refusal("fetch not in IR", site, site, "call to %s" % s.proc))
            return
        q = self.prog.procs[s.proc]
        Rq, fn = D["res"][s.proc]
        for qlbl in rpo(q):
            try:
                qgs = gs + [(self.cond(D, Rq, params, qlbl, fn), True)]
            except Refusal as x:
                D["refusals"].append(Refusal(x.why, site, site, x.detail))
                continue
            loop = bool(Rq.inloop.get(qlbl))
            for j, qs in enumerate(q.blocks[qlbl].stmts):
                if type(qs) is Store:
                    self.store(D, Rq, r, params, qlbl, j, qs, qgs, loop, fn)
                elif type(qs) is Call:
                    D["refusals"].append(
                        Refusal("fetch not in IR", site, site, "nested call to %s" % qs.proc)
                    )

    def exits(self, D, R, r, params, lbl, term, gs):
        """Each edge out of the region from ``lbl``: where it resumes, under its guards."""
        k = type(term)
        if k is Return:
            rets = [self.expr(D, R, params, lbl, None, v) for v in term.vals]
            D["exits"].append({"from": lbl, "to": "$exit", "when": gs, "rets": rets})
            return
        for n, s in enumerate(succs(term)):
            if s in r.blocks or s not in r.exits:
                continue
            when = list(gs)
            if k is If:
                when.append((self.expr(D, R, params, lbl, None, term.c), n == 0))
            elif k is Switch:
                c = Bin("==", self.expr(D, R, params, lbl, None, term.e), Const(term.cases[n][0]))
                when.append((c, True))
            D["exits"].append({"from": lbl, "to": s, "when": when, "rets": []})

    def let(self, D, R, r, params, n):
        """A temp the region leaves live: each definition's value under its block's condition.

        Where several blocks define the name, the one run last on the path is the value.
        """
        order = {l: k for k, l in enumerate(rpo(R.proc))}
        defs = sorted(((l, i) for l, i, _e in R.lets.get(n, ())), key=lambda d: order.get(d[0], -1))
        if n in R.calls:
            defs = [R.calls[n][:2]]
        try:
            alts = [
                (
                    ((self.cond(D, R, params, l), True, frozenset()),),
                    self.expr(D, R, params, l, i + 1, Var(n)),
                )
                for l, i in defs
            ]
        except Refusal as x:  # a temp left unset: named, and a trap only where the tick reads it
            D["unset"].append(Refusal(x.why, n, r.proc, x.detail))
            return
        if len(alts) == 1:
            D["items"].append(
                {"kind": "let", "name": n, "value": alts[0][1], "when": [(alts[0][0][0][0], True)]}
            )
        elif alts:
            D["items"].append(
                {"kind": "let", "name": n, "value": Sel((((), Var(n)),) + tuple(alts)), "when": []}
            )

    # ---- expressions -----------------------------------------------------------
    def expr(self, D, R, params, lbl, i, e, fn=None):
        """``e`` at a site as an entry-relative expression over cells and row bytes."""
        at = len(self.prog.procs[R.name].blocks[lbl].stmts) if i is None else i
        x = R.open(e, lbl, at, DEPTH)
        if fn is not None:
            x = _subst(x, fn)
        x = self.bytes(D, self.unmark(D, params, x))
        for y in _walk(x):
            t = type(y)
            if t is Var and y.n not in params:
                raise Refusal("fetch not in IR", "", "", "name %s is no cell" % y.n)
            if t is Load and y.cls == "io":
                raise Refusal("fetch not in IR", "", "", "reads input $%04X" % y.lo)
            if t is Load and self.isbyte(y):
                raise Refusal("fetch not in IR", "", "", "score read at no cursor")
        return x

    def bytes(self, D, x):
        def fn(y):
            if not self.isbyte(y):
                return y
            y = self.split(y)
            return self.byteref(D, y) if type(y) is Load else self.bytes(D, y)

        return _subst(x, fn)

    @staticmethod
    def split(x):
        """A score read whose address selects: one read per alternative, under its guards."""
        parts = terms(x.a)
        k = next((n for n, (_s, t) in enumerate(parts) if type(t) is Sel), None)
        if k is None:
            return x
        alts = []
        for gs, alt in parts[k][1].alts:
            a = None
            for n, (sign, t) in enumerate(parts):
                t = alt if n == k else t
                a = t if a is None else Bin("+" if sign > 0 else "-", a, t, 2)
            alts.append((gs, Load(x.cls, a, x.w, x.lo, x.hi, x.r)))
        return Sel(tuple(alts))

    def isbyte(self, x):
        return type(x) is Load and any(lo <= x.lo and x.hi <= hi for lo, hi in self.F.tables)

    def mark(self, R, lbl, i, x):
        """A score read's cursor term marked before its address opens: a reset stays a cursor.

        The term reads a channel's cursor cell, or is the name the cursor was just set from.
        """
        if not self.isbyte(x):
            return x
        a = None
        for sign, t in terms(x.a):
            m = self.marker(R, lbl, i, t, R.keys)
            t = t if m is None else m
            a = t if a is None else Bin("+" if sign > 0 else "-", a, t, 2)
        return Load(x.cls, a, x.w, x.lo, x.hi, x.r)

    def marker(self, R, lbl, i, t, keys):
        """A marker name for a cursor term, its entry value and opened value kept beside."""
        cur = _cursor(t, self.rgn)[0]
        raw = (
            t
            if cur is not None and any(cur.addr == c["addr"] for c in self.chans.values())
            else None
        )
        if raw is None and type(t) is Var:
            for key, defs in keys.items():
                for (l, k, _v), (_g, v) in zip(defs, R.alts(defs, lbl, i) if defs else ()):
                    if v == t:
                        st = R.proc.blocks[l].stmts[k]
                        raw = Load("ram", st.a, 1, st.lo, st.hi, key[0])
                        break
                if raw is not None:
                    break
        if raw is None:
            return None
        entry = Load(raw.cls, R.open(raw.a, lbl, i, DEPTH), raw.w, raw.lo, raw.hi, raw.r)
        name = "%s%d" % (CUR, len(self.curval))
        self.curval[name] = (entry, R.open(t, lbl, i, DEPTH))
        return Var(name)

    def byteref(self, D, x):
        """A score load as a :class:`Byte`: the channel its cursor marks, the origin beside.

        Without a cursor term, a read through the channel's known base is at the
        position the rest of its address states -- a jump the score itself named.
        """
        origin, cursor, rest = 0, None, []
        for sign, t in terms(x.a):
            if type(t) is Const:
                origin += sign * t.v
            elif cursor is None and sign > 0 and type(t) is Var and t.n.startswith(CUR):
                cursor = self.curval[t.n]
            elif cursor is None and sign > 0 and _cursor(t, self.rgn)[0] is not None:
                cursor = (t, t)
            else:
                rest.append((sign, t))
        chan = next((c for c in self.chans.values() if c["lo"] <= x.lo and x.hi <= c["hi"]), None)
        if chan is None or any(s < 0 for s, _t in rest):
            return x
        known = D["chans"].get(chan["table"])
        if cursor is None and known is not None:
            bt = [t for _s, t in terms(known["base"])] if known["base"] != Const(0, 2) else []
            if all(any(t == u for _s, u in rest) for t in bt):
                pos = [u for _s, u in rest if not any(u == t for t in bt)]
                position = None
                for t in pos:
                    position = t if position is None else Bin("+", position, t, 2)
                if known["base"] == Const(0, 2):
                    origin -= chan["base"] or 0
                return Byte(chan["table"], self.bytes(D, position or Const(0)), origin)
        if cursor is None:
            return x
        entry, val = cursor
        cur = _cursor(entry, self.rgn)[0]
        if cur is None or cur.addr != chan["addr"]:
            return x
        base = None
        for _s, t in rest:
            base = t if base is None else Bin("+", base, t, 2)
        if base is None and chan["base"] is not None:
            origin -= chan["base"]
        D["chans"].setdefault(
            chan["table"],
            {
                "cursor": entry,
                "addr": entry.a,
                "base": Const(chan["base"] or 0, 2) if base is None else base,
            },
        )
        return Byte(chan["table"], self.bytes(D, val), origin)


# ---- the two encodings -------------------------------------------------------------
def todata(e, path=""):
    """An entry-relative expression as the universal player's data."""
    t = type(e)
    if t is Const:
        return ["k", e.v]
    if t is Var:
        return ["tmp", path + e.n]
    if (
        t is Bin and e.op == "and"
    ):  # a selection, so the second conjunct is read only when the first holds
        both = [["cond", todata(e.a, path), 1], ["cond", todata(e.b, path), 1]]
        return ["sel", [[both, ["k", 1]], [[], ["k", 0]]]]
    if t is Bin and e.op == "or":
        alts = [[[["cond", todata(x, path), 1]], ["k", 1]] for x in (e.a, e.b)]
        return ["sel", alts + [[[], ["k", 0]]]]
    if t is Bin:
        return ["bin", e.op, todata(e.a, path), todata(e.b, path), e.w or 1]
    if t is Load:
        return ["mem", todata(e.a, path), e.w]
    if t is R16:
        return ["mem", todata(e.a, path), 2]
    if t is Byte:
        return ["byte", e.chan, todata(e.cursor, path), e.origin]
    if t is Sel:
        alts = [[guarddata([g[:2] for g in gs], path), todata(x, path)] for gs, x in e.alts]
        return ["sel", alts[::-1]]
    raise TypeError(t.__name__)


def guarddata(when, path=""):
    return [["cond", todata(c, path), 1 if t else 0] for c, t in when]


def _plus(i, k):
    return i if not k else "%s %s %d" % (i, "+" if k > 0 else "-", abs(k))


def _copy(idx, stride):
    """An index over a group of ``stride``-byte copies as the copy number."""
    if stride == 1:
        return idx
    return "v" if idx == "v*%d" % stride else "%s/%d" % (idx, stride)


class Printer:
    """An entry-relative expression under the presentation view's names."""

    def __init__(self, namer, chans, copyvars=frozenset()):
        self.namer, self.chans = namer, chans
        self.copyvars = copyvars if isinstance(copyvars, dict) else dict.fromkeys(copyvars, 1)

    def var(self, n):
        """The copy index as the voice, scaled by the stride it steps by."""
        k = self.copyvars.get(n)
        if k is not None:
            return "v" if k == 1 else "v*%d" % k
        return n.lower() if n in REGVAR.values() else n

    def ref(self, base, idx, lo, field=None):
        """``group[idx].field`` for a cell of a per-copy group, else ``name[idx]``.

        The copy index steps by the group's stride, so as ``v`` it is the copy itself;
        ``field`` names a pair over the low half's cell.
        """
        namer = self.namer
        r = next((r for r in namer.rgn if r.base == base), None) or namer.region(base)
        r = r or namer.region(lo)
        if r is None:
            return "mem[$%04X + %s]" % (base, idx)
        if not r.base - INDEX_MAX <= base < r.base + r.size:  # a pointer plus an offset
            return "%s[%s]" % (namer.names.of(r.id), _plus(idx, base))
        off = base - r.base
        slot = namer.names.slots.get((r.id, base))
        if slot:
            return "%s[%s].%s" % (slot[0][0], idx, field or slot[0][1])
        if r.id in namer.split:
            g, d = namer.split[r.id]
            fields = {int(k): f for k, f in d["fields"].items()}
            f = max((k for k in fields if k <= off), default=None)
            if f is not None:
                stride = max(int(d["stride"]), 1)
                i = _copy(idx, stride)
                copy = (off - f) // stride
                fld = field or fields[f]
                return "%s[%s].%s" % (g, _plus(i, copy), fld)
        hit = namer.names.view.get(r.id)
        if hit is not None and int((namer.names.groups.get(hit[0]) or {}).get("n", 1)) > 1:
            stride = max(int(namer.names.groups[hit[0]].get("stride", 1)), 1)
            copy, rem = divmod(off, stride)
            if not rem:
                return "%s[%s].%s" % (hit[0], _plus(_copy(idx, stride), copy), field or hit[1])
        return "%s[%s]" % (field or namer.names.of(r.id), _plus(idx, base - r.zero))

    def pair(self, e):
        """A 16-bit view by the pair's own name, indexed like its low half."""
        name = self.namer.names.u16.get((tuple(e.lo), tuple(e.hi)))
        idx = addr_split(e.a)[1]
        if idx is None:
            return name or self.namer.cell(e.lo[1])
        return self.ref(e.lo[1], self.expr(idx), e.lo[1], name)

    def expr(self, e):
        t = type(e)
        if t is Const:
            return "$%X" % e.v if e.v > 9 else str(e.v)
        if t is Var:
            return self.var(e.n)
        if t is Bin and e.op in ("and", "or"):
            return "(%s)" % (" %s " % e.op).join(self.chain(e, e.op))
        if t is Bin and e.op == "==" and e.b == Const(0) and type(e.a) is Bin and e.a.op in CMP:
            return "not %s" % self.expr(e.a)
        if t is Bin:
            return "(%s %s %s)" % (self.expr(e.a), e.op, self.expr(e.b))
        if t is Byte:
            chan = self.chans[e.chan]
            name = "byte" if chan["role"] == "pattern" else chan["table"]
            bare = type(e.cursor) is Load and addr_split(e.cursor.a)[0] == chan["addr"]
            if bare or e.cursor == Const(0):
                k = str(e.origin)
            else:
                k = self.expr(e.cursor) + ("" if not e.origin else " + %d" % e.origin)
            return "%s[%s]" % (name, k)
        if t is Load:
            base, idx = addr_split(e.a)
            if base is None:
                r = self.namer.region(e.lo)
                name = "mem" if r is None else self.namer.names.of(r.id)
                return "%s[%s]" % (name, self.expr(e.a))
            if idx is None:
                return self.namer.cell(base) if e.w == 1 else self.namer.expr(e)
            return self.ref(base, self.expr(idx), e.lo)
        if t is R16:
            return self.pair(e)
        if t is Sel:
            out = self.expr(e.alts[0][1])
            for gs, x in e.alts[1:]:
                out = "(%s if %s else %s)" % (self.expr(x), self.guards(gs), out)
            return out
        return self.namer.expr(e)

    def chain(self, e, op):
        if type(e) is Bin and e.op == op:
            return self.chain(e.a, op) + self.chain(e.b, op)
        return [self.expr(e)]

    def guards(self, gs):
        return " and ".join(self.guard(c, t) for c, t, *_w in gs)

    def guard(self, c, t):
        s = self.expr(c)
        if t:
            return s
        return s[4:] if s.startswith("not ") else "not " + s

    def store(self, it):
        cell = self.expr(Load("ram", it["addr"], it["w"], it["lo"], it["hi"], -1))
        if it["cls"] == "io":
            base, idx = addr_split(it["addr"])
            reg = base - 0xD400
            cell = "sid.reg[%s]" % (reg if idx is None else "%d + %s" % (reg, self.expr(idx)))
        when = [self.guards([(c, t)]) for c, t in it["when"] if c != Const(1)]
        when = [
            w[1:-1] if w.startswith("(") and w.endswith(")") and w.count("(") == 1 else w
            for w in when
        ]
        line = "%s = %s" % (cell, self.expr(it["value"]))
        return {
            "cell": cell,
            "print": line + ((" if " + " and ".join(when)) if when else ""),
            "when": when,
            "bytes": sorted({self.expr(y) for y in _walk(it["value"]) if type(y) is Byte}),
        }


def document(fetches, chans):
    """``score.fetch``: per region the producers as printed, and what refused."""
    out = []
    for key, D in fetches.out.items():
        pr = Printer(fetches.namer, chans, fetches.copyvars)
        out.append(
            {
                "region": "%s:%s" % key,
                "producers": [pr.store(it) for it in D["items"] if it["kind"] == "store"],
                "refusals": [{"cell": r.cell, "detail": r.detail} for r in D["refusals"]],
            }
        )
    return out


def data(D, path, chans):
    """One region's derivation as the universal player's fetch item fields."""
    items = []
    for it in D["items"]:
        got = {"kind": it["kind"], "when": guarddata(it["when"], path)}
        if it["kind"] == "store":
            got.update(cls=it["cls"], w=it["w"], lo=it["lo"], hi=it["hi"])
            got.update(addr=todata(it["addr"], path), value=todata(it["value"], path))
        else:
            got.update(name=path + it["name"], value=todata(it["value"], path))
        items.append(got)

    return {
        "items": items,
        "exits": [
            {
                "from": x["from"],
                "to": x["to"],
                "when": guarddata(x["when"], path),
                "rets": [todata(v, path) for v in x["rets"]],
            }
            for x in D["exits"]
        ],
        "chans": [
            {
                "table": t,
                "cursor": todata(D["chans"][t]["cursor"], path),
                "addr": todata(D["chans"][t]["addr"], path),
                "base": todata(D["chans"][t]["base"], path),
                "cell": chans[t]["addr"],
                "stride": chans[t]["stride"],
            }
            for t in D["order"]
        ],
        "refused": [r.cell for r in D["refusals"]],
    }
