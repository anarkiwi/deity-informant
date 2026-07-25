"""sidprog: the canonical structured text for decompiled playroutines.

``emit`` renders a model as procedures of nested regions (loop/if/switch over
labeled block leaves); ``parse`` is its exact inverse and re-verifies the
structurer codec. Grammar/laws: docs/sidprog-language.md.
"""

from __future__ import annotations

import re

from . import codec
from . import expr as E
from . import structured as C

SIDPROG_VERSION = 1  # 1: play-phase structured program (spec section 6)

_BIND_BASE = 1 << 20  # tN bindings ride the uni namespace above any real slot
_T_REF = re.compile(r"\bt(\d+)\b")
_U_REF = re.compile(r"\bu(\d+)\b")
_CYC_LINE = re.compile(r"@\d+")
_CYC_FUSED = re.compile(r"@(\d+) (.+)$")


class SidprogVersionError(ValueError):
    """A document's ``sidprog <major>`` header is not this reader's major."""


_REG_NAMES = {
    0: "A",
    1: "X",
    2: "Y",
    3: "SP",
    8: "C",
    9: "Z",
    10: "I",
    11: "D",
    12: "B",
    13: "V",
    14: "N",
}
_NAME_REGS = {v: k for k, v in _REG_NAMES.items()}
_CHAINS = {"INT_OR": "|", "INT_XOR": "^", "INT_AND": "&"}
_BINS = {"INT_LEFT": "<<", "INT_RIGHT": ">>"}
_CMPS = {"INT_EQUAL": "==", "INT_NOTEQUAL": "!=", "INT_LESS": "<", "INT_LESSEQUAL": "<="}


def _reg_name(i):
    return _REG_NAMES.get(i, "r%d" % i)


def _reg_index(name):
    if name in _NAME_REGS:
        return _NAME_REGS[name]
    if name.startswith("r") and name[1:].isdigit():
        return int(name[1:])
    raise ValueError("unknown register %r" % name)


# ---- expression text (exact round trip) ---------------------------------------
def _hex(v, sz):
    return "$%0*X" % (2 * sz, v)


def _wsuf(sz):
    return "" if sz == 1 else ":%d" % sz


def fmt_expr(n):
    """Render an expression node; ``parse_expr`` is its exact inverse."""
    k = n[0]
    if k == "const":
        return _hex(n[1], n[2])
    if k == "reg":
        return _reg_name(n[1])
    if k == "uni":
        return "u%d%s" % (n[1], _wsuf(n[2]))
    if k == "mem":
        return "mem[%s]" % fmt_expr(n[1])
    mn, kids, sz = n[1], n[2], n[3]
    if mn == "INT_ZEXT":
        return "zext%d(%s)" % (sz, fmt_expr(kids[0]))
    if mn == "INT_CARRY":
        return "carry(%s, %s)" % (fmt_expr(kids[0]), fmt_expr(kids[1]))
    if mn == "INT_ADD":
        half = 1 << (8 * sz - 1)
        parts = [fmt_expr(kids[0])]
        for c in kids[1:]:
            if c[0] == "const" and c[1] >= half:
                parts.append("- " + _hex((-c[1]) & E.mask(sz), sz))
            else:
                parts.append("+ " + fmt_expr(c))
        return "(%s)%s" % (" ".join(parts), _wsuf(sz))
    if mn == "INT_SUB":
        return "(%s - %s)%s" % (fmt_expr(kids[0]), fmt_expr(kids[1]), _wsuf(sz))
    if mn in _CHAINS:
        body = (" %s " % _CHAINS[mn]).join(fmt_expr(c) for c in kids)
        return "(%s)%s" % (body, _wsuf(sz))
    if mn in _CMPS:
        return "(%s %s %s)" % (fmt_expr(kids[0]), _CMPS[mn], fmt_expr(kids[1]))
    body = "%s %s %s" % (fmt_expr(kids[0]), _BINS[mn], fmt_expr(kids[1]))
    return "(%s)%s" % (body, _wsuf(sz))


_TOKEN = re.compile(r"\$[0-9A-Fa-f]+|u\d+|[A-Za-z]\w*|<<|>>|<=|==|!=|[-()\[\],:+|^&<=]|\S")
_ADDSUB = frozenset("+-")
_CHAINOPS = {"|": "INT_OR", "^": "INT_XOR", "&": "INT_AND"}
_BINOPS = {"<<": "INT_LEFT", ">>": "INT_RIGHT"}
_CMPOPS = {"==": "INT_EQUAL", "!=": "INT_NOTEQUAL", "<": "INT_LESS", "<=": "INT_LESSEQUAL"}


class _Toks:
    def __init__(self, text):
        self.toks = _TOKEN.findall(text)
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self):
        t = self.peek()
        if t is None:
            raise ValueError("unexpected end of expression")
        self.i += 1
        return t

    def expect(self, tok):
        t = self.next()
        if t != tok:
            raise ValueError("expected %r, got %r" % (tok, t))

    def done(self):
        return self.i >= len(self.toks)


def _const_tok(t):
    digits = t[1:]
    return ("const", int(digits, 16), max(1, len(digits) // 2))


def _suffix(ts):
    if ts.peek() == ":":
        ts.next()
        return int(ts.next())
    return 1


def _chain(ts):
    operands = [_atom(ts)]
    ops = []
    while ts.peek() != ")":
        ops.append(ts.next())
        operands.append(_atom(ts))
    ts.expect(")")
    sz = _suffix(ts)
    if not ops:
        raise ValueError("redundant parentheses")
    if set(ops) <= _ADDSUB:
        if ops == ["-"] and operands[1][0] != "const":
            return ("op", "INT_SUB", (operands[0], operands[1]), sz)
        kids = [operands[0]]
        for op, o in zip(ops, operands[1:]):
            if op == "-":
                if o[0] != "const":
                    return ("op", "INT_SUB", (operands[0], operands[1]), sz)
                o = ("const", (-o[1]) & E.mask(sz), sz)
            kids.append(o)
        return ("op", "INT_ADD", tuple(kids), sz)
    if len(ops) == 1 and ops[0] in _CMPOPS:
        return ("op", _CMPOPS[ops[0]], (operands[0], operands[1]), sz)
    if len(ops) == 1 and ops[0] in _BINOPS:
        return ("op", _BINOPS[ops[0]], (operands[0], operands[1]), sz)
    mns = {_CHAINOPS.get(op) for op in ops}
    if len(mns) == 1 and None not in mns:
        return ("op", mns.pop(), tuple(operands), sz)
    raise ValueError("mixed operators in %r" % ops)


def _atom(ts):
    t = ts.next()
    if t.startswith("$"):
        return _const_tok(t)
    if t == "(":
        return _chain(ts)
    if t == "mem":
        ts.expect("[")
        addr = _atom(ts)
        ts.expect("]")
        return ("mem", addr, 1)
    if t in ("zext1", "zext2"):
        ts.expect("(")
        a = _atom(ts)
        ts.expect(")")
        return ("op", "INT_ZEXT", (a,), int(t[4:]))
    if t == "carry":
        ts.expect("(")
        a = _atom(ts)
        ts.expect(",")
        b = _atom(ts)
        ts.expect(")")
        return ("op", "INT_CARRY", (a, b), 1)
    if re.fullmatch(r"u\d+", t):
        return ("uni", int(t[1:]), _suffix(ts))
    return ("reg", _reg_index(t))


def parse_expr(text):
    """Parse one expression; must consume ``text`` entirely."""
    ts = _Toks(text)
    n = _atom(ts)
    if not ts.done():
        raise ValueError("trailing tokens in %r" % text)
    return n


# ---- block payload lines (exact round trip) ------------------------------------
def _label(key, dispatch):
    pc, op0 = key
    return "$%04X/$%02X" % (pc, op0) if pc in dispatch else "$%04X" % pc


def _term_lines(term, next_pc):
    if term[0] in ("goto", "jmp"):
        return [] if term[1] == next_pc else ["goto $%04X" % term[1]]
    if term[0] == "br":
        _, pol, tgt, ft, flag, dyn = term
        word = "if" if pol else "ifnot"
        dst = "(%s)" % fmt_expr(dyn) if dyn is not None else "$%04X" % tgt
        line = "%s %s goto %s" % (word, fmt_expr(flag), dst)
        if ft != next_pc:
            line += " else $%04X" % ft
        return [line]
    if term[0] == "jmpd":
        return ["goto (%s)" % fmt_expr(term[1])]
    if term[0] == "jmpind":
        ptr = "(%s)" % fmt_expr(term[2]) if term[2] is not None else "$%04X" % term[1]
        return ["igoto %s" % ptr]
    if term[0] == "jsr":
        _, tgt, ret, dyn = term
        dst = "(%s)" % fmt_expr(dyn) if dyn is not None else "$%04X" % tgt
        return ["call %s ret $%04X" % (dst, ret)]
    return ["ret"]


def _block_lines(blk):
    out = []
    cyc = 0
    for ev in blk.events:
        if ev[0] == "cyc":
            cyc += ev[1]
            continue
        if cyc:
            out.append("@%d" % cyc)
            cyc = 0
        if ev[0] == "ld":
            out.append("u%d = mem[%s]" % (ev[1], fmt_expr(ev[2])))
        elif ev[0] == "st":
            out.append("mem[%s] = %s" % (fmt_expr(ev[1]), fmt_expr(ev[2])))
        else:
            _, kind, aux, idx = ev
            tag = "@xi" if kind == "iy" else "@x"
            out.append("%s(%s, %s)" % (tag, fmt_expr(aux), fmt_expr(idx)))
    if cyc:
        out.append("@%d" % cyc)
    for i in range(16):
        if blk.regs[i] != ("reg", i):
            out.append("%s = %s" % (_reg_name(i), fmt_expr(blk.regs[i])))
    return out


def _parse_two(text):
    ts = _Toks(text)
    ts.expect("(")
    a = _atom(ts)
    ts.expect(",")
    b = _atom(ts)
    ts.expect(")")
    if not ts.done():
        raise ValueError("trailing tokens in %r" % text)
    return a, b


def _parse_target(tok):
    if tok.startswith("("):
        return None, parse_expr(tok[1:-1])
    return int(tok.lstrip("$"), 16), None


def _parse_line(acc, line):
    if line.startswith("@"):
        if line.startswith(("@x(", "@xi(")):
            kind = "iy" if line.startswith("@xi(") else "ax"
            aux, idx = _parse_two(line[len("@xi") if kind == "iy" else len("@x") :])
            acc.events.append(("pen", kind, aux, idx))
        else:
            acc.events.append(("cyc", int(line[1:])))
        return
    if line.startswith("mem["):
        lhs, rhs = line.split(" = ", 1)
        acc.events.append(("st", parse_expr(lhs[4:-1]), parse_expr(rhs)))
        return
    m = re.match(r"u(\d+) = mem\[(.*)\]$", line)
    if m:
        acc.events.append(("ld", int(m.group(1)), parse_expr(m.group(2))))
        return
    if line.startswith(("if ", "ifnot ")):
        pol = 0 if line.startswith("ifnot ") else 1
        body = line.split(" ", 1)[1]
        cond, rest = body.rsplit(" goto ", 1)
        if " else " in rest:
            dst, ft = rest.split(" else ")
            ft = int(ft.lstrip("$"), 16)
        else:
            dst, ft = rest, None
        tgt, dyn = _parse_target(dst)
        acc.term = ("br", pol, tgt, ft, parse_expr(cond), dyn)
        return
    if line.startswith("igoto "):
        ptr, dyn = _parse_target(line[6:])
        acc.term = ("jmpind", ptr, dyn)
        return
    if line.startswith("goto "):
        tgt, dyn = _parse_target(line[5:])
        acc.term = ("jmpd", dyn) if dyn is not None else ("goto", tgt)
        return
    if line.startswith("call "):
        body, ret = line[5:].rsplit(" ret ", 1)
        tgt, dyn = _parse_target(body)
        acc.term = ("jsr", tgt, int(ret.lstrip("$"), 16), dyn)
        return
    if line == "ret":
        acc.term = ("rts",)
        return
    name, rhs = line.split(" = ", 1)
    acc.regs[_reg_index(name.strip())] = parse_expr(rhs)


# ---- per-block common-subexpression bindings (textual only) --------------------
def _kids(n):
    if n[0] == "mem":
        return (n[1],)
    if n[0] == "op":
        return n[2]
    return ()


def _rebuild(n, kids):
    if n[0] == "mem":
        return ("mem", kids[0], n[2])
    if n[0] == "op":
        return ("op", n[1], tuple(kids), n[3])
    return n


def _term_exprs(term):
    k = term[0]
    if k == "br":
        return [term[4]] if term[5] is None else [term[4], term[5]]
    if k == "jmpd":
        return [term[1]]
    if k == "jmpind":
        return [] if term[2] is None else [term[2]]
    if k == "jsr":
        return [] if term[3] is None else [term[3]]
    return []


def _roots(blk):
    out = []
    for ev in blk.events:
        if ev[0] == "ld":
            out.append(ev[2])
        elif ev[0] == "st":
            out.extend((ev[1], ev[2]))
        elif ev[0] == "pen":
            out.extend((ev[2], ev[3]))
    for i in range(16):
        if blk.regs[i] != ("reg", i):
            out.append(blk.regs[i])
    out.extend(_term_exprs(blk.term))
    return out


def _map_term(term, f):
    k = term[0]
    if k == "br":
        return term[:4] + (f(term[4]), None if term[5] is None else f(term[5]))
    if k == "jmpd":
        return ("jmpd", f(term[1]))
    if k == "jmpind" and term[2] is not None:
        return ("jmpind", term[1], f(term[2]))
    if k == "jsr" and term[3] is not None:
        return ("jsr", term[1], term[2], f(term[3]))
    return term


def _rename(line):
    def sub(m):
        n = int(m.group(1))
        return "t%d" % (n - _BIND_BASE) if n >= _BIND_BASE else m.group(0)

    return _U_REF.sub(sub, line)


class _Cse:
    """Hash-consed sharing over one block's expressions: any op/mem subtree
    referenced more than once binds to ``tN``, making emission O(DAG size)."""

    def __init__(self, roots):
        self._oid = {}  # id(node) -> nid
        self._key = {}  # structural key -> nid
        self._rep = []  # nid -> representative node
        self._ref = []  # nid -> DAG reference count
        self._kid = []  # nid -> child nids
        self._out = {}  # id(node) -> reference form (bound subtrees -> tN)
        for r in roots:
            self._ref[self._intern(r)] += 1
        self.order = []
        self._name = {}
        self._len = {}
        seen = set()
        for r in roots:
            self._post(self._oid[id(r)], seen)

    def _intern(self, root):
        stack = [root]
        while stack:
            n = stack[-1]
            if id(n) in self._oid:
                stack.pop()
                continue
            todo = [k for k in _kids(n) if id(k) not in self._oid]
            if todo:
                stack.extend(todo)
                continue
            stack.pop()
            kid_ids = tuple(self._oid[id(k)] for k in _kids(n))
            if n[0] == "op":
                key = ("op", n[1], kid_ids, n[3])
            elif n[0] == "mem":
                key = ("mem", kid_ids[0], n[2])
            else:
                key = n
            nid = self._key.get(key)
            if nid is None:
                nid = len(self._rep)
                self._key[key] = nid
                self._rep.append(n)
                self._ref.append(0)
                self._kid.append(kid_ids)
                for k in kid_ids:
                    self._ref[k] += 1
            self._oid[id(n)] = nid
        return self._oid[id(root)]

    def _post(self, top, seen):
        stack = [(top, False)]
        while stack:
            nid, done = stack.pop()
            if done:
                if self._bind(nid):
                    self.order.append(nid)
                continue
            if nid in seen:
                continue
            seen.add(nid)
            stack.append((nid, True))
            stack.extend((k, False) for k in self._kid[nid])

    def _bind(self, nid):
        """Bind iff the tN definition + references print shorter than inlining."""
        n = self._rep[nid]
        kids = self._kid[nid]
        if not kids:
            self._len[nid] = len(fmt_expr(n))
            return False
        base = {"INT_ZEXT": 7, "INT_CARRY": 9}.get(n[1], 3 * len(kids) + 1) if n[0] == "op" else 5
        plen = base + sum(3 if k in self._name else self._len[k] for k in kids)
        self._len[nid] = plen
        refs = self._ref[nid]
        if refs > 1 and (refs - 1) * plen > 12 + 3 * refs:
            self._name[nid] = len(self._name)
            return True
        return False

    def form(self, n):
        out = self._out
        stack = [n]
        while stack:
            x = stack[-1]
            if id(x) in out:
                stack.pop()
                continue
            name = self._name.get(self._oid[id(x)])
            if name is not None:
                out[id(x)] = ("uni", _BIND_BASE + name, 1)
                stack.pop()
                continue
            todo = [k for k in _kids(x) if id(k) not in out]
            if todo:
                stack.extend(todo)
                continue
            stack.pop()
            out[id(x)] = _rebuild(x, [out[id(k)] for k in _kids(x)])
        return out[id(n)]

    def binding_lines(self):
        out = []
        for i, nid in enumerate(self.order):
            rep = self._rep[nid]
            body = _rebuild(rep, [self.form(k) for k in _kids(rep)])
            out.append("t%d = %s" % (i, _rename(fmt_expr(body))))
        return out


def _shadow(blk, cse):
    """The block with every bound subtree replaced by its tN placeholder."""
    events = []
    for ev in blk.events:
        if ev[0] == "ld":
            events.append(("ld", ev[1], cse.form(ev[2])))
        elif ev[0] == "st":
            events.append(("st", cse.form(ev[1]), cse.form(ev[2])))
        elif ev[0] == "pen":
            events.append(("pen", ev[1], cse.form(ev[2]), cse.form(ev[3])))
        else:
            events.append(ev)
    regs = [r if r == ("reg", i) else cse.form(r) for i, r in enumerate(blk.regs)]
    return C.Block(blk.pc, blk.op0, blk.pcs, events, _map_term(blk.term, cse.form), regs)


# ---- emission -------------------------------------------------------------------
def _image_lines(mem0):
    """``image { .. }``: runs of nonzero bytes, 16 per row, packed hex pairs."""
    out = ["image {"]
    row = []
    for a in range(0x10000):
        if mem0[a]:
            if row and (a != row[0] + len(row) - 1 or len(row) - 1 >= 16):
                out.append(" $%04X: %s" % (row[0], "".join(row[1:])))
                row = []
            if not row:
                row = [a]
            row.append("%02X" % mem0[a])
    if row:
        out.append(" $%04X: %s" % (row[0], "".join(row[1:])))
    out.append("}")
    return out


def _items(region):
    return region.a if region.kind == "seq" else [region]


class _SortedView:
    """Model facade with canonical (sorted) variant order for structuring;
    ``hidden`` pcs (already serialized by an earlier proc) become goto labels."""

    def __init__(self, model):
        self.blocks = model.blocks
        self.mem0 = model.mem0
        self.dyn_targets = model.dyn_targets
        self.dispatch_pcs = set(model.dispatch_sets)
        self.hidden = set()
        by_pc = {}
        for key in sorted(model.blocks):
            by_pc.setdefault(key[0], []).append(key)
        self._by_pc = by_pc

    def variants(self, pc):
        return () if pc in self.hidden else self._by_pc.get(pc, ())


class _Writer:
    """Serializes each procedure's region tree; term lines carry the flow."""

    def __init__(self, model):
        self.model = _SortedView(model)
        self.dispatch = set(model.dispatch_sets)
        self.out = []
        self.done = set()

    def line(self, text, d):
        self.out.append(" " * d + text)

    def proc(self, entry):
        root, _labels = codec.structure(self.model, entry)
        seen = set(self.done)
        self.out.append("proc $%04X {" % entry)
        for r in _items(root):
            self.seq(_items(r), 1)
        self.out.append("}")
        self.model.hidden.update(k[0] for k in self.done - seen)

    def capture(self, items, d):
        keep, self.out = self.out, []
        self.seq(items, d)
        buf, self.out = self.out, keep
        return buf

    def seq(self, items, d):
        i = 0
        while i < len(items):
            r = items[i]
            k = r.kind
            if k == "block":
                i += self.block(r.a, items[i + 1] if i + 1 < len(items) else None, d)
                continue
            if k == "seq":
                self.seq(r.a, d)
            elif k == "loop":
                self.line("loop {", d)
                self.seq(_items(r.a), d + 1)
                self.line("}", d)
            elif k == "switch":
                self.opcode_switch(r, d)
            elif k == "goto":
                echo = "goto $%04X" % r.a
                if not self.out or self.out[-1].lstrip("\x00 ") != echo:
                    self.line(echo, d)
            elif k in ("cont", "brk"):
                self.line("continue" if k == "cont" else "break", d)
            else:
                raise ValueError("unexpected %s region in sequence" % k)
            i += 1

    def block(self, blk, nxt, d):
        self.done.add((blk.pc, blk.op0))
        cse = _Cse(_roots(blk))
        self.line(_label((blk.pc, blk.op0), self.dispatch) + ":", d)
        shadow = _shadow(blk, cse)
        for l in cse.binding_lines():
            self.line(l, d + 1)
        pend = None
        for l in _block_lines(shadow):
            l = _rename(l)
            if _CYC_LINE.fullmatch(l):
                pend = l
                continue
            self.line(pend + " " + l if pend else l, d + 1)
            pend = None
        if pend:
            self.line(pend, d + 1)
        term = [_rename(l) for l in _term_lines(shadow.term, None)]
        if nxt is not None and nxt.kind == "if":
            self.line(term[0] + " {", d)
            self.seq(_items(nxt.b), d + 1)
            els = self.capture(_items(nxt.c), d + 1)
            if els:
                self.line("} else {", d)
                self.out.extend(els)
            self.line("}", d)
            return 2
        if nxt is not None and nxt.kind == "switch" and not nxt.b:
            for l in term:
                self.line(l, d + 1)
            sel, cases = nxt.a
            t = blk.term
            vector = t[0] == "jmpind" and t[1] is not None
            if vector and not self.model.dyn_targets.get(blk.pcs[-1]):
                for _lbl, arm in cases:  # image-derived target: never recorded
                    self.seq(_items(arm), d)
            elif sel == "call":
                body = " ".join(lbl for lbl, _r in cases)
                self.line("switch call { %s }" % body if body else "switch call { }", d)
            else:
                self.line("switch goto {", d)
                for lbl, arm in cases:
                    self.line("case %s: {" % lbl, d + 1)
                    self.seq(_items(arm), d + 2)
                    self.line("}", d + 1)
                self.line("}", d)
            return 2
        if blk.term[0] in ("goto", "jmp"):
            self.out.append("\x00" + " " * (d + 1) + term[0])  # elidable fallthrough
        else:
            for l in term:
                self.line(l, d + 1)
        return 2 if nxt is not None and nxt.kind == "exit" else 1

    def opcode_switch(self, r, d):
        sel, cases = r.a
        self.line("switch %s {" % sel, d)
        for lbl, body in cases:
            self.line("case %s: {" % lbl, d + 1)
            self.seq(_items(body), d + 2)
            self.line("}", d + 1)
        self.line("}", d)


def _entries(model):
    """Procedure entries: play, then every static or proven-dynamic call target."""
    extra = set()
    for blk in model.blocks.values():
        if blk.term[0] == "jsr":
            if blk.term[1] is not None:
                extra.add(blk.term[1])
            else:
                extra.update(model.dyn_targets.get(blk.pcs[-1], ()))
    return [model.play] + sorted(extra - {model.play})


def emit(model):
    """Canonical sidprog text (``parse`` is its exact inverse)."""
    out = ["sidprog %d" % SIDPROG_VERSION, "play $%04X" % model.play, "init $%04X" % model.init]
    if getattr(model, "subtune", 0):
        out.append("subtune %d" % model.subtune)
    prologue = getattr(model, "prologue", ())
    if prologue:
        out.append("sid-init {")
        out.extend("  $%02X = $%02X" % (r, v) for r, v in prologue)
        out.append("}")
    for pc in sorted(model.dispatch_sets):
        out.append(
            "dispatch $%04X: %s"
            % (pc, " ".join("$%02X" % v for v in sorted(model.dispatch_sets[pc])))
        )
    out.extend(_image_lines(model.mem0))
    w = _Writer(model)
    for entry in _entries(model):
        w.proc(entry)
    left = sorted(k for k in model.blocks if k not in w.done)
    while left:
        w.proc(left[0][0])
        still = sorted(k for k in model.blocks if k not in w.done)
        if len(still) == len(left):
            raise ValueError("blocks unreachable from any procedure: %s" % still[:4])
        left = still
    out.extend(_resolve_fallthrough(w.out))
    return "\n".join(out) + "\n"


def _resolve_fallthrough(lines):
    """Drop an elidable ``goto`` whose target label is the very next line."""
    out = []
    for i, l in enumerate(lines):
        if not l.startswith("\x00"):
            out.append(l)
            continue
        tgt = l.rsplit("$", 1)[1]
        nxt = lines[i + 1].lstrip() if i + 1 < len(lines) else ""
        if nxt.startswith("$%s" % tgt) and nxt.endswith(":") and nxt[len(tgt) + 1] in ":/":
            continue
        out.append(l[1:])
    return out


# ---- parsing ----------------------------------------------------------------------
class TextModel:
    """Parsed sidprog program; duck-types ``structured.Model`` for walker+codec."""

    def __init__(self, mem0, init, play, blocks, dispatch, subtune=0, prologue=(), dyn=None):
        self.mem0 = bytes(mem0)
        self.init = init
        self.play = play
        self.subtune = subtune
        self.prologue = list(prologue)
        self.blocks = blocks
        self.dispatch_sets = dispatch
        self.dispatch_pcs = set(dispatch)
        self.written = set(dispatch)
        self.pcs = {pc: {op} for pc, op in blocks if pc not in dispatch}
        self.dyn_targets = dict(dyn or {})
        by_pc = {}
        for key in sorted(blocks):
            by_pc.setdefault(key[0], []).append(key)
        self._by_pc = by_pc

    def variants(self, pc):
        return self._by_pc.get(pc, ())

    def lookup(self, pc, m):
        """The block keyed ``(pc, opcode)``, compiled on first use."""
        if pc in self.written:
            key = (pc, m[pc])
        else:
            ops = self.pcs.get(pc)
            if ops is None:
                raise C.WalkError("pc $%04X outside program" % pc)
            key = (pc, next(iter(ops)))
        blk = self.blocks.get(key)
        if blk is None:
            raise C.WalkError("opcode $%02X at $%04X outside proven set" % (key[1], pc))
        if blk.fn is None:
            blk.fn = C.compile_block(blk)
        return blk


class _Acc:
    def __init__(self, pc, op0):
        self.key = (pc, op0)
        self.events = []
        self.regs = [E.reg(i) for i in range(16)]
        self.term = None
        self.bind = {}


def _t2u(line):
    return _T_REF.sub(lambda m: "u%d" % (int(m.group(1)) + _BIND_BASE), line)


def _expand(n, bind, memo):
    stack = [n]
    while stack:
        x = stack[-1]
        if id(x) in memo:
            stack.pop()
            continue
        if x[0] == "uni" and x[1] >= _BIND_BASE:
            memo[id(x)] = bind[x[1] - _BIND_BASE]
            stack.pop()
            continue
        todo = [k for k in _kids(x) if id(k) not in memo]
        if todo:
            stack.extend(todo)
            continue
        stack.pop()
        memo[id(x)] = _rebuild(x, [memo[id(k)] for k in _kids(x)])
    return memo[id(n)]


def _finish(acc, blocks):
    memo = {}

    def x(n):
        return _expand(n, acc.bind, memo)

    events = []
    for ev in acc.events:
        if ev[0] == "ld":
            events.append(("ld", ev[1], x(ev[2])))
        elif ev[0] == "st":
            events.append(("st", x(ev[1]), x(ev[2])))
        elif ev[0] == "pen":
            events.append(("pen", ev[1], x(ev[2]), x(ev[3])))
        else:
            events.append(ev)
    regs = [r if r == ("reg", i) else x(r) for i, r in enumerate(acc.regs)]
    if acc.term is None:
        raise ValueError("block $%04X has no terminator" % acc.key[0])
    term = _map_term(acc.term, x)
    if term[0] == "br" and term[3] is None:
        raise ValueError("block $%04X falls through nowhere" % acc.key[0])
    pc, op0 = acc.key
    blocks[acc.key] = C.Block(pc, op0, [pc], events, term, regs)


_LABEL = re.compile(r"\$([0-9A-Fa-f]{1,4})(?:/\$([0-9A-Fa-f]{1,2}))?:$")
_BINDING = re.compile(r"t(\d+) = (.*)$")
_CASE = re.compile(r"case \$([0-9A-Fa-f]+): \{$")
_SW_CODE = re.compile(r"switch code\[\$[0-9A-Fa-f]{4}\] \{$")
_SW_CALL = re.compile(r"switch call \{ ?(.*?) ?\}$")


def parse(text):
    """Parse canonical sidprog text into a walkable, codec-verified model."""
    lines = []
    for raw in text.splitlines():
        s = raw.split(";", 1)[0].strip()
        if s:
            lines.append(s)
    head = lines.pop(0).split() if lines else []
    if len(head) != 2 or head[0] != "sidprog" or not head[1].isdigit():
        raise ValueError("not a sidprog document")
    if int(head[1]) != SIDPROG_VERSION:
        raise SidprogVersionError(
            "sidprog major %s: this reader speaks major %d" % (head[1], SIDPROG_VERSION)
        )
    init = play = None
    subtune = 0
    mem0 = bytearray(0x10000)
    dispatch_sets = {}
    blocks = {}
    prologue = []
    dyn_targets = {}
    accs = []
    cur = None
    fall = None  # acc that may adopt an immediately following label as fallthrough
    stack = []  # open braces; a list value collects a goto-switch's case targets
    i = 0
    while i < len(lines):
        line = lines[i]
        adjacent, fall = fall, None
        if line.startswith("play "):
            play = int(line.split()[1].lstrip("$"), 16)
        elif line.startswith("init "):
            init = int(line.split()[1].lstrip("$"), 16)
        elif line.startswith("subtune "):
            subtune = int(line.split()[1])
        elif line.startswith("dispatch "):
            site, vals = line[9:].split(":")
            dispatch_sets[int(site.strip().lstrip("$"), 16)] = {
                int(v.lstrip("$"), 16) for v in vals.split()
            }
        elif line == "sid-init {":
            i += 1
            while lines[i] != "}":
                reg, val = lines[i].split("=")
                prologue.append(
                    (int(reg.strip().lstrip("$"), 16), int(val.strip().lstrip("$"), 16))
                )
                i += 1
        elif line == "image {":
            i += 1
            while lines[i] != "}":
                addr, bytestr = lines[i].split(":", 1)
                a = int(addr.strip().lstrip("$"), 16)
                run = bytestr.strip()
                for k in range(0, len(run), 2):
                    mem0[a + k // 2] = int(run[k : k + 2], 16)
                i += 1
        elif line.startswith("proc ") or line == "loop {":
            stack.append(None)
        elif line in ("}", "} else {"):
            if stack:
                stack.pop()
            if line == "} else {":
                stack.append(None)
        elif line in ("continue", "break"):
            pass
        elif line == "switch goto {":
            dyn_targets[cur.key[0]] = tgts = []
            stack.append(tgts)
        elif _SW_CODE.match(line):
            stack.append(None)
        else:
            m = _SW_CALL.match(line)
            if m:
                dyn_targets[cur.key[0]] = [int(t.lstrip("$"), 16) for t in m.group(1).split()]
                i += 1
                continue
            m = _CASE.match(line)
            if m:
                if stack and isinstance(stack[-1], list):
                    stack[-1].append(int(m.group(1), 16))
                stack.append(None)
                i += 1
                continue
            m = _LABEL.match(line)
            if m:
                pc = int(m.group(1), 16)
                op0 = int(m.group(2), 16) if m.group(2) else mem0[pc]
                if adjacent is not None and adjacent.term is None:
                    adjacent.term = ("goto", pc)  # elided fallthrough goto
                cur = _Acc(pc, op0)
                accs.append(cur)
                fall = cur
                i += 1
                continue
            m = _BINDING.match(line)
            if m:
                node = parse_expr(_t2u(m.group(2)))
                cur.bind[int(m.group(1))] = _expand(node, cur.bind, {})
                fall = cur
            elif line.endswith(" {"):  # if/ifnot header: the block's terminator line
                _parse_line(cur, _t2u(line[:-2]))
                stack.append(None)
            elif cur is not None and cur.term is not None and line.startswith("goto "):
                pass  # region-flow echo; the terminator line already recorded it
            else:
                m = _CYC_FUSED.match(line)
                if m:
                    _parse_line(cur, "@" + m.group(1))
                    line = m.group(2)
                _parse_line(cur, _t2u(line))
                fall = cur
        i += 1
    for acc in accs:
        _finish(acc, blocks)
    if init is None or play is None:
        raise ValueError("missing init/play header")
    tm = TextModel(mem0, init, play, blocks, dispatch_sets, subtune, prologue, dyn_targets)
    codec.verify(tm)
    return tm


dumps = emit  # spec vocabulary (section 6); loads/dumps are inverses
loads = parse
