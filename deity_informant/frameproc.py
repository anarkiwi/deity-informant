"""frameproc: procedural serialization for the frameprog dialect.

Pass 1 lifts registers/temporaries to named locals, pass 2 infers procedure
parameters/returns from register liveness, pass 3 renders counter loops as
for-ranges; all three are emit-side over the committed model's region trees.
"""

from __future__ import annotations

from bisect import bisect_left
from types import MappingProxyType

from . import expr as E
from . import grammar as G
from . import sidprog
from . import structured as C

_REG_LOCAL = {
    0: "a",
    1: "x",
    2: "y",
    3: "sp",
    8: "cflag",
    9: "zflag",
    10: "iflag",
    11: "dflag",
    12: "bflag",
    13: "vflag",
    14: "nflag",
}


def _reg_local(i):
    return _REG_LOCAL.get(i) or "g%d" % i


_ALL_REG_LOCALS = frozenset(_reg_local(i) for i in range(16))
_SP = _REG_LOCAL[3]  # machine state: call/ret move it and stack bytes land through it
_LOCAL_ORDER = {_reg_local(i): i for i in range(16)}


def _by_reg(names):
    return sorted(names, key=_LOCAL_ORDER.get)


# ---- expression helpers (sidprog nodes plus ("loc", name[, width]) leaves) -------
def loc_width(n):
    """Value width of a frameprog expression node: the ONE local-width rule.

    ``("loc", name)`` is one byte and ``("loc", name, w)`` is ``w``; every other
    node states its own width. ``expr.width`` cannot decide a bare local (it reads
    the name), so a loc width is decided here and nowhere else."""
    if n[0] == "loc":
        return n[2] if len(n) > 2 else 1
    return E.width(n)


def _kids(n):
    if n[0] == "mem":
        return (n[1],)
    if n[0] == "op":
        return n[2]
    return ()


def pure(n):
    """True if the expression reads no memory: consts, locals and ops only.

    A pure address re-evaluates with no side effect and consumes no volatile
    input, so it can be probed again within the statement that used it."""
    k = n[0]
    if k in ("const", "loc"):
        return True
    return k == "op" and all(pure(c) for c in n[2])


def _rebuild(n, kids):
    if n[0] == "mem":
        return ("mem", kids[0], n[2])
    if n[0] == "op":
        return ("op", n[1], tuple(kids), n[3])
    return n


def _locset(n):
    out = set()
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "loc":
            out.add(x[1])
        else:
            stack.extend(_kids(x))
    return out


def _reads_mem(n):
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem":
            return True
        stack.extend(_kids(x))
    return False


def _reads_vol(n):
    """True when evaluating ``n`` may read a volatile cell (input order)."""
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "mem" and not sidprog._ld_safe(x[1]):
            return True
        stack.extend(_kids(x))
    return False


def _subst_loc(n, name, repl):
    if n[0] == "loc":
        return repl if n[1] == name else n
    kids = _kids(n)
    if not kids:
        return n
    return _rebuild(n, [_subst_loc(c, name, repl) for c in kids])


_WILD = frozenset(("call", "dcall", "swc", "dbr", "dgoto", "igoto", "label"))


def hidden_defs(s):
    """Names ``s`` may define other than as this list's own ``asg``; None means any.

    A nested body's definition, a call's writes and a label control may enter at
    are invisible to a scan of one list, yet each ends the reign of the definition
    before it."""
    if s[0] in _WILD:
        return None
    out = {s[1]} if s[0] in ("asg", "for") else set(s[3]) if s[0] == "pcall" else set()
    for b in _stmt_bodies(s):
        for s2 in b:
            got = hidden_defs(s2)
            if got is None:
                return None
            out |= got
    return out


class Defs:
    """The local definitions of one statement list, read at the point of the read.

    A statement list is not SSA: ``x0 = x`` captures what ``x`` held there, so
    every lookup carries the reader's position and answers with the definer's. A
    definition the list does not make itself is recorded valueless (``hidden_defs``)."""

    __slots__ = ("defs", "wild", "outer", "cyclic")

    def __init__(self, lst, outer=None, cyclic=False):
        self.defs, self.wild = {}, []
        self.outer, self.cyclic = outer, cyclic
        for k, s in enumerate(lst):
            if s[0] == "asg":
                self.defs.setdefault(s[1], []).append((k, s[2]))
                continue
            killed = hidden_defs(s)
            if killed is None:
                self.wild.append(k)
                continue
            for name in killed:
                self.defs.setdefault(name, []).append((k, None))

    def at(self, name, bound):
        """``(index, value)`` of the definition in force at ``bound``, else None.

        This list only: the index is a position in it, so a caller stepping to that
        index must not be handed one from an enclosing list. A None ``value`` marks
        a definition whose value cannot be read off, and still names which it is."""
        made = self.defs.get(name)
        k = 0 if made is None else bisect_left(made, (bound,))
        got = made[k - 1] if k else None
        w = bisect_left(self.wild, bound)
        if w and (got is None or self.wild[w - 1] > got[0]):
            return (self.wild[w - 1], None)
        return got

    def _lookup(self, name, bound):
        """``(env, index, value)`` of the definition in force, enclosing lists included.

        Reaching here at all means this list defines nothing for ``name`` before
        ``bound`` and lets control in nowhere before it either -- ``at`` answers
        both. Only a back edge can still overwrite it, from a definition or a label
        *after* the read, so a cyclic body must bind neither."""
        got = self.at(name, bound)
        if got is not None:
            return (self, got[0], got[1])
        if self.outer is None or (self.cyclic and (self.wild or name in self.defs)):
            return None
        return self.outer[0]._lookup(name, self.outer[1])

    def resolve(self, n, bound):
        """The value ``n`` names, following definitions out through enclosing lists."""
        env = self
        while n[0] == "loc":
            got = env._lookup(n[1], bound)
            if got is None or got[2] is None:
                return n
            env, bound, n = got
        return n

    def value(self, name, bound):
        """``at``, but None wherever the definition in force has no readable value."""
        got = self.at(name, bound)
        return None if got is None or got[1] is None else got


def _stmt_exprs(s):
    """Statement forms: label/asg/st/if/loop/for/opsw/swg/swc/callb/call/
    pcall/dcall/dbr/dgoto/igoto/goto/unobs/cont/brk/ret; this returns the
    expression operands, ``_stmt_bodies`` the nested statement lists."""
    k = s[0]
    if k == "asg":
        return (s[2],)
    if k == "dgoto":
        return (s[1],)
    if k == "st":
        return (s[1], s[2])
    if k == "if":
        return (s[2],)
    if k == "pcall":
        return tuple(s[2])
    if k == "dcall":
        return (s[1],)
    if k == "dbr":
        return (s[2], s[3])
    if k == "igoto":
        return () if s[2] is None else (s[2],)
    return ()


def _stmt_bodies(s):
    k = s[0]
    if k == "if":
        return (s[3], s[4])
    if k == "loop":
        return (s[1],)
    if k == "for":
        return (s[4],)
    if k == "opsw":
        return tuple(b for _l, b in s[2])
    if k == "swg":
        return tuple(b for _l, b in s[1])
    if k == "swc":
        return tuple(b for _l, b in s[2])
    if k == "callb":
        return (s[3],)
    return ()


def _map_exprs(s, f):
    k = s[0]
    if k == "asg":
        return ("asg", s[1], f(s[2]))
    if k == "st":
        return ("st", f(s[1]), f(s[2]))
    if k == "if":
        return ("if", s[1], f(s[2]), s[3], s[4])
    if k == "pcall":
        return ("pcall", s[1], [f(a) for a in s[2]], s[3])
    if k == "dcall":
        return ("dcall", f(s[1]), s[2])
    if k == "dbr":
        return ("dbr", s[1], f(s[2]), f(s[3]), s[4])
    if k == "dgoto":
        return ("dgoto", f(s[1]))
    if k == "igoto" and s[2] is not None:
        return ("igoto", s[1], f(s[2]))
    return s


# ---- block -> sequential local statements (pass 1 groundwork) -------------------
class _Stale(Exception):
    def __init__(self, reg):
        super().__init__(reg)
        self.reg = reg


class _Names:
    """Per-procedure local-name allocator (deterministic counters)."""

    def __init__(self, aliases):
        self.aliases = aliases
        self.counts = {}

    def fresh(self, prefix):
        k = self.counts.get(prefix, 0)
        self.counts[prefix] = k + 1
        return "%s%d" % (prefix, k)

    def role(self, addr):
        """Role prefix from a const cell address's symbol alias, else None."""
        if addr[0] != "const":
            return None
        alias = self.aliases.get(addr[1])
        if alias is None:
            return None
        head = alias.split("_", 1)[0].lower()
        return head if head.isalpha() else None


def _sugar(n):
    """Subtrees that print as name sugar (array index shapes) stay inline."""
    if n[0] == "op" and n[1] == "INT_ZEXT" and n[2][0][0] in ("reg", "slot", "loc"):
        return True
    if n[0] == "op" and n[1] == "INT_ADD" and n[3] == 2 and len(n[2]) == 2:
        idx, base = n[2]
        if base[0] == "const" and base[2] == 2 and base[1] >= 0x100:
            return idx[0] == "op" and idx[1] == "INT_ZEXT"
    return False


class _Conv:
    """One block to statements: loads and shared values become named locals,
    register out-exprs become sequenced assignments (pre-copies on conflict)."""

    def __init__(self, names):
        self.names = names

    def run(self, blk):
        items, kouts, term = self._keyform(blk)
        bound, first, order, impure = self._bindings(items, kouts, term)
        precopy = set()
        while True:
            snap = dict(self.names.counts)
            try:
                return self._emit(items, kouts, term, bound, first, order, impure, precopy)
            except _Stale as e:
                self.names.counts = snap
                if e.reg in precopy:
                    raise ValueError("unresolvable register dependency") from e
                precopy.add(e.reg)

    @staticmethod
    def _keyform(blk):
        memo = {}

        def key(n):
            r = memo.get(id(n))
            if r is None:
                k = n[0]
                if k == "uni":
                    r = ("slot", n[1])
                elif k == "mem":
                    r = ("mem", key(n[1]), n[2])
                elif k == "op":
                    r = ("op", n[1], tuple(key(c) for c in n[2]), n[3])
                else:
                    r = n
                memo[id(n)] = r
            return r

        items = []
        for ev in blk.events:
            if ev[0] == "ld":
                items.append(("ld", ev[1], key(ev[2])))
            else:
                items.append(("st", key(ev[1]), key(ev[2])))
        kouts = {i: key(x) for i, x in enumerate(blk.regs) if x != ("reg", i)}
        t = blk.term
        k = t[0]
        if k == "br":
            term = ("br", t[1], t[2], t[3], key(t[4]), None if t[5] is None else key(t[5]))
        elif k == "jmpd":
            term = ("jmpd", key(t[1]))
        elif k == "jmpind":
            term = ("jmpind", t[1], None if t[2] is None else key(t[2]))
        elif k == "jsr":
            term = ("jsr", t[1], t[2], None if t[3] is None else key(t[3]))
        else:
            term = t
        return items, kouts, term

    @staticmethod
    def _roots(items, kouts, term):
        roots = []
        for item in items:
            roots.extend(item[2:] if item[0] == "ld" else item[1:])
        roots.extend(kouts[i] for i in sorted(kouts))
        k = term[0]
        if k == "br":
            roots.append(term[4])
            if term[5] is not None:
                roots.append(term[5])
        elif k == "jmpd":
            roots.append(term[1])
        elif k in ("jmpind", "jsr") and term[-1] is not None:
            roots.append(term[-1])
        return roots

    def _bindings(self, items, kouts, term):
        """(bound set, first-use position, postorder, impure set) for the
        pure multi-use subtrees worth naming (the _Cse economics)."""
        refs = {}
        order = []
        stack = [(r, False) for r in reversed(self._roots(items, kouts, term))]
        while stack:
            n, done = stack.pop()
            if done:
                order.append(n)
                continue
            if n in refs:
                refs[n] += 1
                continue
            refs[n] = 1
            stack.append((n, True))
            stack.extend((c, False) for c in _kids(n))
        plen = {}
        impure = set()
        bound = set()
        for n in order:
            kids = _kids(n)
            if n[0] == "mem" or any(c in impure for c in kids):
                impure.add(n)
            if not kids:
                plen[n] = 2 * n[2] + 1 if n[0] == "const" else 3
                continue
            if n[0] == "mem":
                base = 5
            else:
                base = {"INT_ZEXT": 7, "INT_CARRY": 9}.get(n[1], 3 * len(kids) + 1)
            plen[n] = base + sum(3 if c in bound else plen[c] for c in kids)
            if n[0] != "op" or n in impure or _sugar(n):
                continue
            if refs[n] > 1 and (refs[n] - 1) * plen[n] > 12 + 3 * refs[n]:
                bound.add(n)
        first = {}
        roots = [(p, r) for p, item in enumerate(items) for r in item[1:] if isinstance(r, tuple)]
        roots += [(len(items), r) for r in self._roots([], kouts, term)]
        for pos, root in roots:
            stack = [root]
            while stack:
                x = stack.pop()
                if x in bound and x not in first:
                    first[x] = pos
                stack.extend(_kids(x))
        return bound, first, order, impure

    @staticmethod
    def _reg_last(items, kouts, term):
        last = {}
        roots = [(p, r) for p, item in enumerate(items) for r in item[1:] if isinstance(r, tuple)]
        roots += [(len(items), r) for r in _Conv._roots([], kouts, term)]
        for pos, root in roots:
            stack = [root]
            while stack:
                x = stack.pop()
                if x[0] == "reg":
                    last[x[1]] = max(last.get(x[1], -1), pos)
                stack.extend(_kids(x))
        return last

    def _emit(self, items, kouts, term, bound, first, order, impure, precopy):
        avail = {("reg", i): _reg_local(i) for i in range(16)}
        held = {}
        for i in range(16):
            held.setdefault(_reg_local(i), set()).add(("reg", i))
        out = []
        pend = dict(kouts)
        reg_last = self._reg_last(items, kouts, term)
        slot_regs = {}
        for i in sorted(kouts):
            stack = [kouts[i]]
            while stack:
                x = stack.pop()
                if x[0] == "slot":
                    slot_regs.setdefault(x[1], []).append(i)
                stack.extend(_kids(x))

        def render(n):
            name = avail.get(n)
            if name is not None:
                return ("loc", name)
            k = n[0]
            if k == "const":
                return n
            if k in ("reg", "slot"):
                raise _Stale(n[1])
            return _rebuild(n, [render(c) for c in _kids(n)])

        def bind(name, node):
            avail[node] = name
            held.setdefault(name, set()).add(node)

        def overwrite(name, i, rendered, node):
            if i in precopy and avail.get(("reg", i)) == name:
                tmp = self.names.fresh(name)
                out.append(("asg", tmp, ("loc", name)))
                bind(tmp, ("reg", i))
            out.append(("asg", name, rendered))
            for n2 in held.pop(name, ()):
                if avail.get(n2) == name:
                    del avail[n2]
            if node is not None and node not in impure and node[0] != "const":
                bind(name, node)

        matq = [n for n in order if n in bound]
        end = len(items)

        def mat(pos):
            for n in matq:
                if first.get(n) != pos or n in avail:
                    continue
                if pos == end and n in pend.values():
                    continue  # the register assignment itself binds the value
                di = None
                if pos < end:
                    for i in sorted(pend):
                        if pend[i] == n and reg_last.get(i, -1) < pos:
                            di = i
                            break
                if di is not None:
                    overwrite(_reg_local(di), di, render(n), pend[di])
                    del pend[di]
                else:
                    rendered = render(n)
                    name = self.names.fresh("t")
                    out.append(("asg", name, rendered))
                    bind(name, n)

        for pos, item in enumerate(items):
            mat(pos)
            if item[0] == "ld":
                _l, s, addr = item
                rendered = ("mem", render(addr), 1)
                di = None
                for i in sorted(pend):
                    if pend[i] == ("slot", s) and reg_last.get(i, -1) <= pos:
                        di = i
                        break
                if di is not None:
                    overwrite(_reg_local(di), di, rendered, None)
                    bind(_reg_local(di), ("slot", s))
                    del pend[di]
                else:
                    vregs = [i for i in slot_regs.get(s, ()) if i < 4]
                    prefix = self.names.role(addr) or (_reg_local(min(vregs)) if vregs else "w")
                    name = self.names.fresh(prefix)
                    out.append(("asg", name, rendered))
                    bind(name, ("slot", s))
            else:
                out.append(("st", render(item[1]), render(item[2])))
        mat(len(items))
        while pend:
            done = None
            for i in sorted(pend):
                try:
                    rendered = render(pend[i])
                except _Stale:
                    continue
                overwrite(_reg_local(i), i, rendered, pend[i])
                done = i
                break
            if done is None:
                render(pend[sorted(pend)[0]])
                raise AssertionError("stale register not raised")
            del pend[done]
        k = term[0]
        if k == "br":
            term = term[:4] + (render(term[4]), None if term[5] is None else render(term[5]))
        elif k == "jmpd":
            term = ("jmpd", render(term[1]))
        elif k == "jmpind" and term[2] is not None:
            term = ("jmpind", term[1], render(term[2]))
        elif k == "jsr" and term[3] is not None:
            term = ("jsr", term[1], term[2], render(term[3]))
        return out, term


# ---- region trees -> statement trees --------------------------------------------
def _view_block(blk):
    events = [ev for ev in blk.events if ev[0] in ("ld", "st")]
    if len(events) != len(blk.events):
        blk = C.Block(blk.pc, blk.op0, blk.pcs, events, blk.term, blk.regs)
    return sidprog._stmt_view(blk)


class _Builder:
    """Serializes region trees into statement lists (frameprog dialect)."""

    def __init__(self, labels, dispatch, view, conv):
        self.labels = labels
        self.dispatch = dispatch
        self.view = view
        self.conv = conv
        self.arm = 0

    def proc(self, root):
        return self.capture(sidprog._items(root))

    def capture(self, items):
        out = []
        self.seq(items, out)
        return out

    def seq(self, items, out):
        i = 0
        while i < len(items):
            r = items[i]
            k = r.kind
            if k == "block":
                i += self.block(r, items[i + 1] if i + 1 < len(items) else None, out)
                continue
            if k == "seq":
                self.seq(r.a, out)
            elif k == "loop":
                out.append(("loop", self.capture(sidprog._items(r.a))))
            elif k == "switch":
                if r.b and r.b[0] in self.labels:
                    out.append(("label", r.b[0]))
                cases = [(lbl, self.capture(sidprog._items(body))) for lbl, body in r.a[1]]
                out.append(("opsw", r.b[0], cases))
            elif k == "goto":
                out.append(("goto", r.a))
            elif k == "frontier":
                out.append(("unobs", r.a))
            elif k in ("cont", "brk"):
                out.append((k,))
            else:
                raise ValueError("unexpected %s region in sequence" % k)
            i += 1

    def block(self, r, nxt, out):
        blk = r.a
        if self.view is not None and r.b is not None and blk.pc in self.dispatch:
            if r.b in self.labels:
                out.append(("label", r.b))
            inner = []
            consumed = self._leaf(r, nxt, inner, False)
            out.append(("opsw", blk.pc, [("$%02X" % blk.op0, inner)]))
            return consumed
        return self._leaf(r, nxt, out, True)

    def _leaf(self, r, nxt, out, labelled):
        blk = _view_block(r.a)
        stmts, term = self.conv.run(blk)
        if labelled and r.b is not None and r.b in self.labels:
            out.append(("label", r.b))
        out.extend(stmts)
        return self._flow(term, blk.pcs[-1], nxt, out)

    def _flow(self, term, site, nxt, out):
        k = term[0]
        if nxt is not None and nxt.kind == "if":
            if k != "br" or term[5] is not None:
                raise ValueError("if region without a static branch terminator")
            word = "if" if term[1] else "ifnot"
            then = self.capture(sidprog._items(nxt.b))
            els = self.capture(sidprog._items(nxt.c)) if nxt.c is not None else []
            out.append(("if", word, term[4], then, els))
            return 2
        if nxt is not None and nxt.kind == "call":
            if k != "jsr" or term[3] is not None:
                raise ValueError("call body without a static call terminator")
            self.arm += 1
            body = self.capture(sidprog._items(nxt.b))
            self.arm -= 1
            out.append(("callb", term[1], term[2], body))
            return 2
        if nxt is not None and nxt.kind == "switch" and not nxt.b:
            self._termlines(term, out)
            sel, cases = nxt.a
            vector = k == "jmpind" and term[1] is not None
            if vector and self.view is not None and not self.view.dyn_targets.get(site):
                for _lbl, arm in cases:
                    self.seq(sidprog._items(arm), out)
            elif sel == "call":
                bare = [lbl for lbl, arm in cases if arm is None or arm.b is None]
                bodied = []
                for lbl, arm in cases:
                    if arm is not None and arm.b is not None:
                        self.arm += 1
                        bodied.append((lbl, self.capture(sidprog._items(arm.b))))
                        self.arm -= 1
                out.append(("swc", bare, bodied))
            else:
                arms = [(lbl, self.capture(sidprog._items(arm))) for lbl, arm in cases]
                out.append(("swg", arms))
            return 2
        if k == "br" and term[5] is None:
            raise ValueError("static branch terminator without an if region")
        self._termlines(term, out)
        return 2 if nxt is not None and nxt.kind == "exit" else 1

    def _termlines(self, term, out):
        k = term[0]
        if k in ("goto", "jmp"):
            return
        if k == "br":
            out.append(("dbr", "if" if term[1] else "ifnot", term[4], term[5], term[3]))
        elif k == "jmpd":
            out.append(("dgoto", term[1]))
        elif k == "jmpind":
            out.append(("igoto", term[1], term[2]))
        elif k == "jsr":
            if term[3] is not None:
                out.append(("dcall", term[3], term[2]))
            else:
                out.append(("call", term[1], term[2]))
        else:
            out.append(("ret", self.arm > 0))


# ---- pass 2: interprocedural register liveness -----------------------------------
class _Info:
    """Program-wide summaries: live-in, may/must-define, returns, callability."""

    def __init__(self, procs, play):
        self.procs = dict(procs)
        self.order = [e for e, _s in procs]
        self.play = play
        self.livein = {e: set() for e in self.order}
        self.returns = {e: set() for e in self.order}
        self.may = {e: set() for e in self.order}
        self.must = {e: set() for e in self.order}
        self.labmap = {e: {} for e in self.order}
        self.own_labels = {e: set() for e in self.order}
        self.call_labels = {e: set() for e in self.order}
        self.G = set()
        self.open_flow = False
        self.callable_ = {}
        self.params = {}
        self.rets = {}
        self._scan()

    def _scan(self):
        blocked = set()
        for e in self.order:
            gotos, calls = set(), set()
            self._scan_list(self.procs[e], self.own_labels[e], gotos, blocked, calls)
            blocked |= {pc for pc in gotos if pc == e or pc not in self.own_labels[e]}
            self.call_labels[e] = calls & self.own_labels[e]
        for e in self.order:
            self.callable_[e] = not self.open_flow and e not in blocked

    def _scan_list(self, stmts, labels, gotos, blocked, calls):
        for j, s in enumerate(stmts):
            k = s[0]
            nxt = stmts[j + 1][0] if j + 1 < len(stmts) else None
            if k == "dbr":
                self.open_flow = True
            elif k == "dgoto" and nxt != "swg":
                self.open_flow = True
            elif k == "igoto" and s[2] is not None and nxt != "swg":
                self.open_flow = True
            elif k == "dcall" and nxt != "swc":
                self.open_flow = True
            elif k == "swc":
                blocked |= {int(lbl[1:], 16) for lbl in s[1]}
            elif k == "label":
                labels.add(s[1])
            elif k == "goto":
                gotos.add(s[1])
            elif k in ("call", "callb"):
                calls.add(s[1])
            for x in _stmt_exprs(s):
                self.G |= _locset(x) & _ALL_REG_LOCALS
            for b in _stmt_bodies(s):
                self._scan_list(b, labels, gotos, blocked, calls)

    def ret_live(self, e):
        out = set(self.returns[e])
        if e == self.play:
            out |= self.livein[e]
        if not self.callable_[e]:
            out |= self.G
        return out

    def summarize(self):
        for _round in range(24):
            changed = False
            for e in self.order:
                may = self._may(self.procs[e])
                if may != self.may[e]:
                    self.may[e] = may
                    changed = True
                must = self._must(self.procs[e])[0]
                if must != self.must[e]:
                    self.must[e] = must
                    changed = True
                before = (len(self.livein[e]), len(self.returns[e]))
                self.livein[e] |= _Flow(self, e).run() & _ALL_REG_LOCALS
                if self.callable_[e]:
                    self.livein[e] |= self.returns[e] - self.must[e]
                if (len(self.livein[e]), len(self.returns[e])) != before:
                    changed = True
            if not changed:
                break
        for e in self.order:
            self.params[e] = _by_reg(self.livein[e])
            self.rets[e] = _by_reg(self.returns[e]) if self.callable_[e] else []

    def _may(self, stmts):
        out = set()
        for s in stmts:
            k = s[0]
            if k in ("asg", "for"):
                out.add(s[1])
            elif k == "pcall":
                out |= set(s[3]) | self.may.get(s[1], set())
            elif k == "call":
                out |= self.may.get(s[1], self.G)
            elif k in ("dcall", "dbr", "dgoto"):
                out |= self.G
            elif k == "swc":
                for lbl in s[1]:
                    out |= self.may.get(int(lbl[1:], 16), self.G)
            for b in _stmt_bodies(s):
                out |= self._may(b)
        return out

    def _must(self, stmts):
        """(surely-defined names, fell-through) under conservative branching."""
        out = set()
        for s in stmts:
            k = s[0]
            if k in ("ret", "goto", "cont", "brk", "unobs", "dbr", "dgoto", "igoto"):
                return out, False
            if k == "asg":
                out.add(s[1])
            elif k == "pcall":
                out |= set(s[3]) | self.must.get(s[1], set())
            elif k == "call":
                out |= self.must.get(s[1], set())
            elif k == "callb":
                out |= self._must(s[3])[0]
            elif k in ("if", "opsw", "swg"):
                arms = [self._arm_must(b) for b in _stmt_bodies(s)]
                live_arms = [d for d in arms if d is not None]
                if not live_arms:
                    return out, False
                if len(live_arms) == len(arms):
                    out |= set.intersection(*live_arms)
                elif k == "if" and len(live_arms) == 1:
                    out |= live_arms[0]
                if k == "swg":
                    return out, False
        return out, True

    def _arm_must(self, body):
        if body and all(x[0] == "unobs" for x in body):
            return None
        return self._must(body)[0]


class _Flow:
    """One backward register-liveness sweep over a procedure's statements."""

    def __init__(self, info, entry):
        self.info = info
        self.entry = entry
        self.labmap = info.labmap[entry]
        self.brk = []
        self.cont = []
        self.armret = []
        self.loop_after = None
        self.loop_head = None
        self.liveout = None

    def run(self):
        """Backward sweep to a fixpoint over the procedure's label live-sets.

        One pass is unsound: a ``goto`` reaching a label the sweep has not yet
        visited (a forward branch into a later arm) reads an empty live-set and
        makes live locals look dead, so seed each round with the last."""
        seed = self.info.labmap[self.entry]
        out = set()
        for _ in range(24):
            self.labmap = {k: set(v) for k, v in seed.items()}
            out = self.seq(self.info.procs[self.entry], set())
            grew = any(not v <= seed.get(k, set()) for k, v in self.labmap.items())
            for k, v in self.labmap.items():
                seed[k] = seed.get(k, set()) | v
            if not grew:
                break
        self.labmap = seed
        return out

    def _call_body(self, stmts):
        """First index of an own-label some ``call`` enters, else None.

        Such a body returns to its call sites and may be entered again, so its
        exit carries the machine set: textual fall-through under-approximates."""
        labs = self.info.call_labels.get(self.entry)
        if not labs:
            return None
        for i, s in enumerate(stmts):
            if s[0] == "label" and s[1] in labs:
                return i
        return None

    def seq(self, stmts, live):
        nxt = None
        cb = self._call_body(stmts)
        for j in range(len(stmts) - 1, -1, -1):
            s = stmts[j]
            if cb is not None and j >= cb:
                live = live | self.info.G
            if self.liveout is not None:
                self.liveout[id(s)] = set(live)
            live = self.stmt(s, live, nxt)
            nxt = s[0]
        return live

    def _uses(self, s):
        out = set()
        for x in _stmt_exprs(s):
            out |= _locset(x)
        return out

    def _loop_head(self, s, live):
        body = s[1] if s[0] == "loop" else s[4]
        head = set()
        for _i in range(24):
            f = _Flow(self.info, self.entry)
            f.armret = list(self.armret)
            f.brk = self.brk + [set(live)]
            f.cont = self.cont + [head]
            h = f.seq(body, set(head))
            if s[0] == "for":
                h.discard(s[1])
            if h <= head:
                break
            head |= h
        return head

    def _call_live(self, tgt, live, kills, extra):
        info = self.info
        if tgt in info.procs:
            if info.callable_[tgt]:
                info.returns[tgt] |= live & info.may[tgt]
            return (live - kills) | info.livein[tgt] | extra
        return live | info.G | extra

    def stmt(self, s, live, nxt):
        k = s[0]
        info = self.info
        if k == "asg":
            if s[1] != _SP and s[1] not in live and not _reads_vol(s[2]):
                return live  # faint: a dead target's operand reads are not uses
            live = set(live)
            live.discard(s[1])
            return live | _locset(s[2])
        if k == "st":
            return live | self._uses(s)
        if k == "if":
            return self.seq(s[3], set(live)) | self.seq(s[4], set(live)) | _locset(s[2])
        if k in ("loop", "for"):
            head = self._loop_head(s, live)
            if k == "loop" and self.loop_after is not None:
                self.loop_after[id(s)] = set(live)
            if self.loop_head is not None:
                self.loop_head[id(s)] = set(head)
            self.brk.append(set(live))
            self.cont.append(head)
            out = self.seq(s[1] if k == "loop" else s[4], set(head))
            self.brk.pop()
            self.cont.pop()
            if k == "for":
                out.discard(s[1])
            return out | head
        if k == "label":
            self.labmap[s[1]] = self.labmap.get(s[1], set()) | live
            return live
        if k == "goto":
            pc = s[1]
            if pc in info.own_labels[self.entry]:
                return set(self.labmap.get(pc, set()))
            if pc in info.procs:
                return set(info.livein[pc])
            return set(info.G)
        if k == "cont":
            return set(self.cont[-1]) if self.cont else set(info.G)
        if k == "brk":
            return set(self.brk[-1]) if self.brk else set(info.G)
        if k == "unobs":
            return set()
        if k == "ret":
            if s[1]:
                return set(self.armret[-1]) if self.armret else set(info.G)
            return set(info.ret_live(self.entry))
        if k == "call":
            return self._call_live(s[1], live, info.must.get(s[1], set()), set())
        if k == "pcall":
            kills = set(s[3]) | info.must.get(s[1], set())
            return self._call_live(s[1], live, kills, self._uses(s))
        if k == "dcall":
            if nxt == "swc":
                return live | _locset(s[1])
            return set(info.G) | _locset(s[1])
        if k == "swc":
            out = set()
            for _lbl, body in s[2]:
                self.armret.append(set(live))
                out |= self.seq(body, set())
                self.armret.pop()
            for lbl in s[1]:
                out |= self._call_live(int(lbl[1:], 16), set(live), set(), set())
            return out
        if k in ("opsw", "swg"):
            out = set()
            for body in _stmt_bodies(s):
                out |= self.seq(body, set(live))
            return out
        if k == "callb":
            self.armret.append(set(live))
            out = self.seq(s[3], set(live))
            self.armret.pop()
            return out
        if k == "dbr":
            return set(info.G) | self._uses(s)
        if k == "dgoto":
            if nxt == "swg":
                return live | _locset(s[1])
            return set(info.G) | _locset(s[1])
        if k == "igoto":
            used = _locset(s[2]) if s[2] is not None else set()
            if nxt == "swg" or s[2] is None:
                return live | used
            return set(info.G) | used
        raise ValueError("unexpected statement %r" % (k,))


# ---- pass 1 cleanup: dead-definition pruning + single-use inlining ---------------
class _Prune(_Flow):
    """Liveness sweep that drops assignments to locals dead at their site."""

    changed = False

    def seq(self, stmts, live):
        keep = []
        nxt = None
        cb = self._call_body(stmts)
        for j in range(len(stmts) - 1, -1, -1):
            s = stmts[j]
            if cb is not None and j >= cb:
                live = live | self.info.G
            if s[0] == "asg" and s[1] != _SP and s[1] not in live and not _reads_vol(s[2]):
                self.changed = True
                continue
            live = self.stmt(s, live, nxt)
            keep.append(s)
            nxt = s[0]
        keep.reverse()
        stmts[:] = keep
        return live


def _prune(info):
    changed = False
    for e in info.order:
        p = _Prune(info, e)
        p.run()
        changed |= p.changed
    return changed


def _defs_in(s, info):
    """Textual-semantics definitions of ``s`` (a pcall writes only its rets)."""
    k = s[0]
    out = set()
    if k in ("asg", "for"):
        out.add(s[1])
    elif k == "pcall":
        out |= set(s[3])
    elif k == "call":
        out |= info.may.get(s[1], info.G)
    elif k in ("dcall", "dbr", "dgoto", "igoto", "swc"):
        out |= info.G
    for b in _stmt_bodies(s):
        for s2 in b:
            out |= _defs_in(s2, info)
    return out


def _invis_name(s, name, info, entry):
    """Whether ``s`` may consume ``name`` through a non-textual use."""
    k = s[0]
    if k == "call":
        hit = name in info.livein.get(s[1], info.G)
    elif k in ("dcall", "dbr", "dgoto", "igoto", "swc"):
        hit = name in info.G
    elif k == "ret":
        hit = True if s[1] else name in info.ret_live(entry)
    elif k == "goto":
        if s[1] in info.own_labels[entry]:
            hit = name in info.labmap[entry].get(s[1], info.G)  # the target consumes it
        else:
            hit = name in info.livein.get(s[1], info.G)
    else:
        hit = False
    return hit or any(_invis_name(s2, name, info, entry) for b in _stmt_bodies(s) for s2 in b)


def _labels_in(s):
    if s[0] == "label":
        return True
    return any(_labels_in(s2) for b in _stmt_bodies(s) for s2 in b)


def _use_count_expr(x, name):
    n = 0
    stack = [x]
    while stack:
        y = stack.pop()
        if y == ("loc", name):
            n += 1
        stack.extend(_kids(y))
    return n


def _use_count(s, name):
    n = sum(_use_count_expr(x, name) for x in _stmt_exprs(s))
    for b in _stmt_bodies(s):
        for s2 in b:
            n += _use_count(s2, name)
    return n


def _escapes(s, out):
    """Collect cont/brk statements of ``s`` bound to the enclosing loop."""
    k = s[0]
    if k in ("cont", "brk"):
        out.add(k)
    elif k not in ("loop", "for"):
        for b in _stmt_bodies(s):
            for s2 in b:
                _escapes(s2, out)
    return out


def _esc_hit(s, name, head, after):
    esc = _escapes(s, set())
    if "cont" in esc and (head is None or name in head):
        return True
    return "brk" in esc and (after is None or name in after)


class _InlineCtx:
    """Per-list inlining context: liveness annotations plus loop scope."""

    __slots__ = ("info", "entry", "liveout", "loop_head", "head", "after")

    def __init__(self, info, entry, liveout, loop_head, head=None, after=None):
        self.info = info
        self.entry = entry
        self.liveout = liveout
        self.loop_head = loop_head
        self.head = head
        self.after = after

    def enter(self, s):
        if s[0] in ("loop", "for"):
            return _InlineCtx(
                self.info,
                self.entry,
                self.liveout,
                self.loop_head,
                self.loop_head.get(id(s)),
                self.liveout.get(id(s)),
            )
        return self


def _find_use(items, start, name, e, ctx):
    """Index of the sole safe use site of ``name = e`` in ``items``, else None.

    Sound for multi-def names: the def dominates the same-list use (no label
    in between), so every path to the use passes the def."""
    info, entry, liveout = ctx.info, ctx.entry, ctx.liveout
    head, after = ctx.head, ctx.after
    elocs = _locset(e)
    has_mem = _reads_mem(e)
    for j in range(start, len(items)):
        s = items[j]
        if _labels_in(s):
            return None
        cnt = _use_count(s, name)
        dd = _defs_in(s, info)
        if cnt:
            targets = {s[1]} if s[0] == "asg" else set(s[3]) if s[0] == "pcall" else set()
            if cnt > 1 or (dd - targets) & (elocs | {name}) or _invis_name(s, name, info, entry):
                return None  # targets assign after operand evaluation: not a conflict
            top = sum(_use_count_expr(x, name) for x in _stmt_exprs(s))
            if has_mem and top != cnt:
                return None
            if _esc_hit(s, name, head, after):
                return None
            if name not in targets and name in liveout.get(id(s), (name,)):
                return None  # a later (possibly non-textual) consumer remains
            return j
        if name in dd or dd & elocs or _invis_name(s, name, info, entry):
            return None
        if _esc_hit(s, name, head, after):
            return None
        if has_mem and s[0] != "asg":
            return None
    return None


def _subst_stmt(s, name, e):
    s = _map_exprs(s, lambda x: _subst_loc(x, name, e))
    for b in _stmt_bodies(s):
        for i, s2 in enumerate(b):
            if _use_count(s2, name):
                b[i] = _subst_stmt(s2, name, e)
    return s


def _inline_list(items, ctx):
    changed = False
    i = 0
    while i < len(items):
        s = items[i]
        sub = ctx.enter(s)
        for b in _stmt_bodies(s):
            changed |= _inline_list(b, sub)
        if s[0] == "asg" and s[1] != _SP and not _reads_vol(s[2]):
            j = _find_use(items, i + 1, s[1], s[2], ctx)
            if j is not None:
                items[j] = _subst_stmt(items[j], s[1], s[2])
                del items[i]
                changed = True
                continue
        i += 1
    return changed


def _inline(info):
    changed = False
    for e in info.order:
        flow = _Flow(info, e)
        flow.liveout = {}
        flow.loop_head = {}
        flow.run()
        changed |= _inline_list(info.procs[e], _InlineCtx(info, e, flow.liveout, flow.loop_head))
    return changed


def _rewrite_calls(info):
    def walk(stmts):
        for i, s in enumerate(stmts):
            if s[0] == "call" and info.callable_.get(s[1]):
                args = [("loc", p) for p in info.params[s[1]]]
                stmts[i] = ("pcall", s[1], args, list(info.rets[s[1]]))
            for b in _stmt_bodies(stmts[i]):
                walk(b)

    for e in info.order:
        walk(info.procs[e])


# ---- pass 3: counter loops as for-ranges -----------------------------------------
def _mentions(s, name):
    if s[0] in ("asg", "for") and s[1] == name:
        return True
    if s[0] == "pcall" and name in s[3]:
        return True
    if any(_use_count_expr(x, name) for x in _stmt_exprs(s)):
        return True
    return any(_mentions(s2, name) for b in _stmt_bodies(s) for s2 in b)


def _own_conts(stmts):
    n = 0
    for s in stmts:
        if s[0] == "cont":
            n += 1
        elif s[0] not in ("loop", "for"):
            for b in _stmt_bodies(s):
                n += _own_conts(b)
    return n


def _defs_name(s, name):
    if s[0] in ("asg", "for") and s[1] == name:
        return True
    if s[0] == "pcall" and name in s[3]:
        return True
    if s[0] in ("call", "dcall", "swc"):
        return True
    return any(_defs_name(s2, name) for b in _stmt_bodies(s) for s2 in b)


def _step_of(upd, name):
    if upd == ("op", "INT_ADD", (("loc", name), ("const", 1, 1)), 1):
        return 1
    if upd == ("op", "INT_ADD", (("loc", name), ("const", 0xFF, 1)), 1):
        return -1
    if upd == ("op", "INT_SUB", (("loc", name), ("const", 1, 1)), 1):
        return -1
    return None


def _cond_of(cond, name):
    """("sign", None) or ("eq"/"ne", K) when ``cond`` tests the counter."""
    if cond[0] != "op":
        return None
    if cond[1] == "INT_NOTEQUAL" and cond[3] == 1:
        lhs, rhs = cond[2]
        sign = (
            rhs == ("const", 0, 1)
            and lhs[0] == "op"
            and lhs[1] == "INT_AND"
            and set(lhs[2]) == {("loc", name), ("const", 0x80, 1)}
        )
        if sign:
            return ("sign", None)
    if cond[1] in ("INT_EQUAL", "INT_NOTEQUAL"):
        lhs, rhs = cond[2]
        if lhs == ("loc", name) and rhs[0] == "const" and rhs[2] == 1:
            return ("eq" if cond[1] == "INT_EQUAL" else "ne", rhs[1])
    return None


def _match_for(body):
    """(counter, step, test shape, continue-if-true) from an update/test tail."""
    if len(body) < 3 or body[-1] != ("brk",):
        return None
    upd, test = body[-3], body[-2]
    if upd[0] != "asg" or test[0] != "if":
        return None
    name = upd[1]
    step = _step_of(upd[2], name)
    if step is None:
        return None
    _k, word, cond, then, els = test
    if then == [("cont",)] and els == []:
        cont_if_true = word == "if"
    elif then == [] and els == [("cont",)]:
        cont_if_true = word == "ifnot"
    else:
        return None
    shape = _cond_of(cond, name)
    if shape is None:
        return None
    return name, step, shape, cont_if_true


def _for_last(step, shape, cont_if_true, init):
    kind, kval = shape
    if kind == "sign" and step == -1 and not cont_if_true and init < 0x80:
        return 0
    cont_ne = (kind == "ne" and cont_if_true) or (kind == "eq" and not cont_if_true)
    if cont_ne and step == 1 and init < kval:
        return kval - 1
    if cont_ne and step == -1 and init > kval:
        return kval + 1
    return None


def _for_lists(items, after):
    for s in items:
        for b in _stmt_bodies(s):
            _for_lists(b, after)
    idx = 0
    while idx < len(items):
        s = items[idx]
        match = _match_for(s[1]) if s[0] == "loop" and id(s) in after else None
        if match is None:
            idx += 1
            continue
        name, step, shape, cont_if_true = match
        body = s[1][:-3]
        bad = (
            name in after[id(s)]
            or any(_labels_in(s2) for s2 in s[1])
            or _own_conts(body)
            or any(_defs_name(s2, name) for s2 in body)
        )
        j = idx - 1
        while j >= 0 and items[j][0] != "label" and not _mentions(items[j], name):
            j -= 1
        init_ok = j >= 0 and items[j][:2] == ("asg", name) and items[j][2][0] == "const"
        if bad or not init_ok:
            idx += 1
            continue
        init = items[j][2][1]
        last = _for_last(step, shape, cont_if_true, init)
        if last is None:
            idx += 1
            continue
        items[idx] = ("for", name, init, last, body)
        del items[j]
    return items


def _forloops(info):
    for e in info.order:
        f = _Flow(info, e)
        f.loop_after = {}
        f.run()
        _for_lists(info.procs[e], f.loop_after)


# ---- printing --------------------------------------------------------------------
def _index_of(addr):
    """``(base, index expression)`` for a ``const + index`` address, else None.

    The index is whatever the address adds to the base; ``zext2`` is the reader's
    own widening (grammar ``_index_addr``) and is stripped so the text round trips."""
    if addr[0] != "op" or addr[1] != "INT_ADD" or addr[3] != 2 or len(addr[2]) != 2:
        return None
    at = [i for i, c in enumerate(addr[2]) if c[0] == "const" and c[2] == 2 and c[1] >= 0x100]
    if len(at) != 1:
        return None
    base, idx = addr[2][at[0]], addr[2][1 - at[0]]
    if idx[0] == "const":
        return None
    if idx[0] == "op" and idx[1] == "INT_ZEXT" and idx[3] == 2:
        idx = idx[2][0]
    return base[1], idx


_NORES = MappingProxyType({})  # rung (f): deref address -> (pointer cell, index or None)


def _membody(addr, res=_NORES):
    if addr[0] == "const" and addr[2] == 2:
        return sidprog._addr_name(addr[1])
    got = res.get(addr)
    if got is not None:
        name = "*" + sidprog._addr_name(got[0])
        return name if got[1] is None else "%s[%s]" % (name, _fmt(got[1], res))
    got = _index_of(addr)
    if got is not None:
        return "%s[%s]" % (sidprog._addr_name(got[0]), _fmt(got[1], res))
    return None


def _memref(addr, sz=1, res=_NORES):
    body = _membody(addr, res)
    if body is None:
        body = "mem[%s]" % _fmt(addr, res)
    return body + sidprog._wsuf(sz)


def _fmt(n, res=_NORES):
    k = n[0]
    if k == "const":
        return sidprog._hex(n[1], n[2])
    if k == "loc":
        return n[1] + sidprog._wsuf(loc_width(n))
    if k == "mem":
        return _memref(n[1], n[2], res)
    mn, kids, sz = n[1], n[2], n[3]
    if mn == "INT_ZEXT":
        return "zext%d(%s)" % (sz, _fmt(kids[0], res))
    if mn == "COPY":
        return "trunc%d(%s)" % (sz, _fmt(kids[0], res))
    if mn == "INT_CARRY":
        return "carry(%s, %s)" % (_fmt(kids[0], res), _fmt(kids[1], res))
    if mn == "INT_ADD":
        half = 1 << (8 * sz - 1)
        parts = [_fmt(kids[0], res)]
        for c in kids[1:]:
            if c[0] == "const" and c[1] >= half:
                parts.append("- " + sidprog._hex((-c[1]) & ((1 << (8 * sz)) - 1), sz))
            else:
                parts.append("+ " + _fmt(c, res))
        return "(%s)%s" % (" ".join(parts), sidprog._wsuf(sz))
    if mn == "INT_SUB":
        return "(%s - %s)%s" % (_fmt(kids[0], res), _fmt(kids[1], res), sidprog._wsuf(sz))
    if mn in sidprog._CHAINS:
        body = (" %s " % sidprog._CHAINS[mn]).join(_fmt(c, res) for c in kids)
        return "(%s)%s" % (body, sidprog._wsuf(sz))
    if mn in sidprog._CMPS:
        return "(%s %s %s)" % (_fmt(kids[0], res), sidprog._CMPS[mn], _fmt(kids[1], res))
    body = "%s %s %s" % (_fmt(kids[0], res), sidprog._BINS[mn], _fmt(kids[1], res))
    return "(%s)%s" % (body, sidprog._wsuf(sz))


class _Printer:
    """Statement trees to frameprog text lines."""

    def __init__(self, res=_NORES):
        self.out = []
        self.rets = []
        self.res = res

    def e(self, n):
        """Expression text, resolved deref addresses named (rung (f))."""
        return _fmt(n, self.res)

    def line(self, text, d):
        self.out.append(" " * d + text)

    def proc(self, entry, stmts, params, rets):
        self.rets = rets
        sig = "sub_%04X(%s)" % (entry, ", ".join(params))
        if rets:
            sig += " -> %s" % ", ".join(rets)
        self.line(sig + " {", 0)
        self.seq(stmts, 1)
        self.line("}", 0)

    def seq(self, stmts, d):
        for s in stmts:
            self.stmt(s, d)

    def _cases(self, head, cases, d):
        self.line(head, d)
        for lbl, body in cases:
            self.line("case %s: {" % lbl, d + 1)
            self.seq(body, d + 2)
            self.line("}", d + 1)
        self.line("}", d)

    def _if(self, s, d):
        _k, word, cond, then, els = s
        if len(then) == 1 and then[0][0] == "unobs":
            self.line("%s %s unobserved $%04X" % (word, self.e(cond), then[0][1]), d)
            self.seq(els, d)
            return
        self.line("%s %s {" % (word, self.e(cond)), d)
        self.seq(then, d + 1)
        if len(els) == 1 and els[0][0] == "unobs":
            self.line("} else unobserved $%04X" % els[0][1], d)
            return
        if els:
            self.line("} else {", d)
            self.seq(els, d + 1)
        self.line("}", d)

    def _swc(self, s, d):
        if not s[2]:
            body = " ".join(s[1])
            self.line("switch call { %s }" % body if body else "switch call { }", d)
            return
        self.line("switch call {", d)
        if s[1]:
            self.line(" ".join(s[1]), d + 1)
        for lbl, body in s[2]:
            self.line("case %s: {" % lbl, d + 1)
            self.seq(body, d + 2)
            self.line("}", d + 1)
        self.line("}", d)

    def stmt(self, s, d):
        k = s[0]
        if k == "label":
            self.line("$%04X:" % s[1], d)
        elif k == "asg":
            lv = s[1] + sidprog._wsuf(G.store_width(s[2]))
            self.line("%s = %s" % (lv, self.e(s[2])), d + 1)
        elif k == "st":
            self.line(
                "%s = %s" % (_memref(s[1], G.store_width(s[2]), self.res), self.e(s[2])), d + 1
            )
        elif k == "if":
            self._if(s, d)
        elif k == "loop":
            self.line("loop {", d)
            self.seq(s[1], d + 1)
            self.line("}", d)
        elif k == "for":
            self.line("for %s in $%02X..$%02X {" % (s[1], s[2], s[3]), d)
            self.seq(s[4], d + 1)
            self.line("}", d)
        elif k == "opsw":
            self._cases("switch %s {" % sidprog._addr_name(s[1]), s[2], d)
        elif k == "swg":
            self._cases("switch goto {", s[1], d)
        elif k == "swc":
            self._swc(s, d)
        elif k == "callb":
            self.line("call $%04X ret $%04X {" % (s[1], s[2]), d + 1)
            self.seq(s[3], d + 2)
            self.line("}", d + 1)
        elif k == "call":
            self.line("call $%04X ret $%04X" % (s[1], s[2]), d + 1)
        elif k == "pcall":
            text = "sub_%04X(%s)" % (s[1], ", ".join(self.e(a) for a in s[2]))
            if s[3]:
                text = "%s = %s" % (", ".join(s[3]), text)
            self.line(text, d + 1)
        elif k == "dcall":
            self.line("call (%s) ret $%04X" % (self.e(s[1]), s[2]), d + 1)
        elif k == "dbr":
            self.line(
                "%s %s goto (%s) else $%04X" % (s[1], self.e(s[2]), self.e(s[3]), s[4]), d + 1
            )
        elif k == "dgoto":
            self.line("goto (%s)" % self.e(s[1]), d + 1)
        elif k == "igoto":
            ptr = "(%s)" % self.e(s[2]) if s[2] is not None else "$%04X" % s[1]
            self.line("igoto %s" % ptr, d + 1)
        elif k == "goto":
            self.line("goto $%04X" % s[1], d)
        elif k == "unobs":
            self.line("unobserved $%04X" % s[1], d)
        elif k == "cont":
            self.line("continue", d)
        elif k == "brk":
            self.line("break", d)
        elif k == "ret":
            if not s[1] and self.rets:
                self.line("ret %s" % ", ".join(self.rets), d + 1)
            else:
                self.line("ret", d + 1)
        else:
            raise ValueError("unexpected statement %r" % (k,))


# ---- driver ------------------------------------------------------------------------
def procedures(trees, labels, view, dispatch, aliases, play):
    """``(entry, params, rets, statements)`` per procedure under passes 1-3."""
    cell_aliases = dict(aliases or {})
    procs = []
    for entry, root in trees:
        builder = _Builder(labels, dispatch, view, _Conv(_Names(cell_aliases)))
        procs.append((entry, builder.proc(root)))
    info = _Info(procs, play)
    info.summarize()
    for _round in range(3):
        before = ({e: list(v) for e, v in info.params.items()}, dict(info.rets))
        info.summarize()
        if before == (info.params, info.rets):
            break
    _rewrite_calls(info)
    for _round in range(16):
        pruned = _prune(info)
        inlined = _inline(info)
        if not (pruned or inlined):
            break
    _forloops(info)
    return [(e, info.params[e], info.rets[e], stmts) for e, stmts in procs]


def render_lines(procs, resolved=_NORES):
    """frameprog text lines for analysed (or parsed) procedures."""
    printer = _Printer(resolved)
    for entry, params, rets, stmts in procs:
        printer.proc(entry, stmts, params, rets)
    return printer.out
