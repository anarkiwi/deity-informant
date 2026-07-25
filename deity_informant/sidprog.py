"""sidprog: the canonical structured text for decompiled playroutines.

``emit`` renders a model as region-tree procedures whose nesting IS the flow
(implicit fallthrough; labels only on goto targets); ``parse`` rebuilds the
trees and ``TextModel.run`` walks them. Grammar: docs/sidprog-language.md.
"""

from __future__ import annotations

import re

from . import codec
from . import datadecl
from . import expr as E
from . import structured as C
from .render import _static_preds, sid_name

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


# ---- named machine state (bijection; render.sid_name is the normative table) ---
_SID_NAMES = {a: sid_name(a) for a in range(0xD400, 0xD419)}
_SID_ADDRS = {n: a for a, n in _SID_NAMES.items()}
_CELL_NAME = re.compile(r"(zp|m)_([0-9A-F]+)$")


def _addr_name(v):
    """Canonical cell name for a 2-byte const address (total on 0..$FFFF)."""
    return _SID_NAMES.get(v) or ("zp_%02X" % v if v < 0x100 else "m_%04X" % v)


def _name_addr(name):
    """Address named by a canonical cell name, else None (inverse of _addr_name)."""
    a = _SID_ADDRS.get(name)
    if a is None:
        m = _CELL_NAME.match(name)
        if m:
            a = int(m.group(2), 16)
            if a > 0xFFFF or _addr_name(a) != name:
                return None
    return a


def _split_index(addr):
    """``(base, reg)`` iff ``addr`` is the canonical zext2(reg) + const2 >= $100."""
    if addr[0] == "op" and addr[1] == "INT_ADD" and addr[3] == 2 and len(addr[2]) == 2:
        idx, base = addr[2]
        base_ok = base[0] == "const" and base[2] == 2 and base[1] >= 0x100
        idx_ok = idx[0] == "op" and idx[1] == "INT_ZEXT" and idx[3] == 2
        if base_ok and idx_ok and idx[2][0][0] == "reg":
            return base[1], idx[2][0][1]
    return None


def _mem_body(addr):
    """Named text for a memory reference address, else None (raw mem[] only)."""
    if addr[0] == "const" and addr[2] == 2:
        return _addr_name(addr[1])
    bi = _split_index(addr)
    if bi is not None:
        return "%s[%s]" % (_addr_name(bi[0]), _reg_name(bi[1]))
    return None


def _mem_ref(addr):
    body = _mem_body(addr)
    return body if body is not None else "mem[%s]" % fmt_expr(addr)


def _zext_reg(n):
    return n[0] == "op" and n[1] == "INT_ZEXT" and n[3] == 2 and n[2][0][0] == "reg"


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
        return _mem_ref(n[1])
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


_TOKEN = re.compile(
    r"\$[0-9A-Fa-f]+|u\d+|[A-Za-z]\w*(?:\.\w+)*|<<|>>|<=|==|!=|[-()\[\],:+|^&<=]|\S"
)
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
    addr = _name_addr(t)
    if addr is not None:
        if ts.peek() == "[":
            ts.next()
            r = _reg_index(ts.next())
            ts.expect("]")
            idx = ("op", "INT_ZEXT", (("reg", r),), 2)
            return ("mem", ("op", "INT_ADD", (idx, ("const", addr, 2)), 2), 1)
        return ("mem", ("const", addr, 2), 1)
    return ("reg", _reg_index(t))


def parse_expr(text):
    """Parse one expression; must consume ``text`` entirely."""
    ts = _Toks(text)
    n = _atom(ts)
    if not ts.done():
        raise ValueError("trailing tokens in %r" % text)
    return n


# ---- block payload lines (exact round trip) ------------------------------------
def _term_lines(term):
    """Explicit terminator lines; goto/jmp fallthrough is implicit (structure)
    and a static branch serializes as its if region, not here."""
    if term[0] in ("goto", "jmp"):
        return []
    if term[0] == "br":  # dynamic-target branch escape hatch
        _, pol, _tgt, ft, flag, dyn = term
        word = "if" if pol else "ifnot"
        return ["%s %s goto (%s) else $%04X" % (word, fmt_expr(flag), fmt_expr(dyn), ft)]
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
            out.append("u%d = %s" % (ev[1], _mem_ref(ev[2])))
        elif ev[0] == "st":
            out.append("%s = %s" % (_mem_ref(ev[1]), fmt_expr(ev[2])))
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
    m = re.match(r"u(\d+) = (.*)$", line)
    if m:
        src = parse_expr(m.group(2))
        if src[0] != "mem":
            raise ValueError("load line without a memory source: %r" % line)
        acc.events.append(("ld", int(m.group(1)), src[1]))
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
    name = name.strip()
    if name in _NAME_REGS or (name.startswith("r") and name[1:].isdigit()):
        acc.regs[_reg_index(name)] = parse_expr(rhs)
        return
    dst = parse_expr(name)
    if dst[0] != "mem":
        raise ValueError("store line without a memory target: %r" % line)
    acc.events.append(("st", dst[1], parse_expr(rhs)))


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


# ---- emit-side statement cleanup (single-use load inlining, canonical conds) ---
_VOLS = C._VOL | C._VOL0


def _canon_cond(n):
    """``(a - b) == $00`` -> ``a == b`` and ``(a + k) == $00`` -> ``a == -k``
    (also ``!=``), equal widths only; walker-equivalent direct compares."""
    if n[0] != "op" or n[1] not in ("INT_EQUAL", "INT_NOTEQUAL"):
        return n
    lhs, rhs = n[2]
    if lhs[0] != "op" or rhs != ("const", 0, lhs[3]):
        return n
    sz = lhs[3]
    if lhs[1] == "INT_SUB" and E.width(lhs[2][0]) == sz and E.width(lhs[2][1]) == sz:
        return ("op", n[1], lhs[2], n[3])
    if lhs[1] == "INT_ADD" and len(lhs[2]) == 2:
        a, k = lhs[2]
        if k[0] == "const" and k[2] == sz and E.width(a) == sz and (-k[1]) & E.mask(sz):
            return ("op", n[1], (a, ("const", (-k[1]) & E.mask(sz), sz)), n[3])
    return n


def _bytev(n):
    """True iff ``n`` provably evaluates to one byte under ``compile_block``."""
    while n[0] == "op" and n[1] == "INT_ZEXT":
        n = n[2][0]
    if n[0] != "op":
        return E.width(n) == 1
    if n[1] in ("INT_ADD", "INT_SUB", "INT_LEFT"):
        return n[3] == 1  # masked to their width at evaluation
    if n[1] == "INT_RIGHT":
        return _bytev(n[2][0])
    if n[1] == "INT_CARRY" or n[1] in _CMPS:
        return True
    return all(_bytev(k) for k in n[2])  # AND/OR/XOR: closed over byte operands


def _ld_safe(addr):
    """True iff every run-time address of this load avoids the volatile cells."""
    if addr[0] == "const":
        return addr[1] & 0xFFFF not in _VOLS
    if addr[0] == "op" and addr[1] == "INT_ADD" and addr[3] == 2 and len(addr[2]) == 2:
        a, b = addr[2]
        base, idx = (a, b) if a[0] == "const" else (b, a)
        if base[0] == "const" and idx[0] != "const" and _bytev(idx):
            return all((v - base[1]) & 0xFFFF > 0xFF for v in _VOLS)
    return False


def _uni_refs(n, memo):
    """slot -> [paths, wide-ref] reference multiplicities over the DAG of ``n``."""
    if id(n) not in memo:
        stack = [(n, False)]
        while stack:
            x, done = stack.pop()
            if id(x) in memo:
                continue
            if x[0] == "uni":
                memo[id(x)] = {x[1]: [1, x[2] != 1]}
            elif not done:
                stack.append((x, True))
                stack.extend((k, False) for k in _kids(x))
            else:
                acc = {}
                for k in _kids(x):
                    for s, (c, w) in memo[id(k)].items():
                        cur = acc.setdefault(s, [0, False])
                        cur[0] += c
                        cur[1] |= w
                memo[id(x)] = acc
    return memo[id(n)]


def _pos_roots(events, term, regs):
    out = []
    for i, ev in enumerate(events):
        if ev[0] == "ld":
            out.append((i, ev[2]))
        elif ev[0] == "st":
            out.extend(((i, ev[1]), (i, ev[2])))
        elif ev[0] == "pen":
            out.extend(((i, ev[2]), (i, ev[3])))
    end = len(events)
    out.extend((end, r) for i, r in enumerate(regs) if r != ("reg", i))
    out.extend((end, x) for x in _term_exprs(term))
    return out


def _inline_pick(events, term, regs):
    """(index, slot) of the first single-use load safe to inline, else None."""
    defs = {}
    for i, ev in enumerate(events):
        if ev[0] == "ld":
            defs[ev[1]] = None if ev[1] in defs else i
    uses = {}
    memo = {}
    for pos, x in _pos_roots(events, term, regs):
        for s, (c, w) in _uni_refs(x, memo).items():
            u = uses.setdefault(s, [0, -1, False])
            u[0] += c
            u[1] = pos
            u[2] |= w
    for i, ev in enumerate(events):
        if ev[0] != "ld" or defs[ev[1]] != i:
            continue
        u = uses.get(ev[1])
        if u is None or u[0] != 1 or u[2] or u[1] <= i or not _ld_safe(ev[2]):
            continue
        span = events[i + 1 : u[1]]
        if any(e[0] == "st" for e in span):
            continue  # the moved read must not cross any store
        moved = set(_uni_refs(ev[2], memo))
        if any(e[0] == "ld" and e[1] in moved for e in span):
            continue  # nor a redefinition of a slot its address reads
        return i, ev[1]
    return None


def _subst_slot(n, slot, repl, memo):
    stack = [n]
    while stack:
        x = stack[-1]
        if id(x) in memo:
            stack.pop()
            continue
        if x == ("uni", slot, 1):
            memo[id(x)] = repl
            stack.pop()
            continue
        todo = [k for k in _kids(x) if id(k) not in memo]
        if todo:
            stack.extend(todo)
            continue
        stack.pop()
        memo[id(x)] = _rebuild(x, [memo[id(k)] for k in _kids(x)])
    return memo[id(n)]


def _apply_inline(events, term, regs, i):
    repl = ("mem", events[i][2], 1)
    slot = events[i][1]
    memo = {}

    def sub(x):
        return _subst_slot(x, slot, repl, memo)

    del events[i]
    for j, ev in enumerate(events):
        if ev[0] == "ld":
            events[j] = ("ld", ev[1], sub(ev[2]))
        elif ev[0] == "st":
            events[j] = ("st", sub(ev[1]), sub(ev[2]))
        elif ev[0] == "pen":
            events[j] = ("pen", ev[1], sub(ev[2]), sub(ev[3]))
    return events, _map_term(term, sub), [sub(r) for r in regs]


def _stmt_view(blk):
    """Emit-side copy of a block with canonical branch conditions and every
    single-use non-volatile load inlined at its use site (docs section on
    statement sugar); the model block is never mutated."""
    term = blk.term
    if term[0] == "br":
        cond = _canon_cond(term[4])
        if cond is not term[4]:
            term = term[:4] + (cond, term[5])
    events, regs = list(blk.events), list(blk.regs)
    changed = False
    pick = _inline_pick(events, term, regs)
    while pick is not None:
        events, term, regs = _apply_inline(events, term, regs, pick[0])
        changed = True
        pick = _inline_pick(events, term, regs)
    if not changed and term is blk.term:
        return blk
    return C.Block(blk.pc, blk.op0, blk.pcs, events, term, regs)


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
        """Bind iff the tN definition + references print shorter than inlining;
        named cells/arrays and their address shapes stay inline (they ARE names)."""
        n = self._rep[nid]
        kids = self._kid[nid]
        if not kids:
            self._len[nid] = len(fmt_expr(n))
            return False
        body = _mem_body(n[1]) if n[0] == "mem" else None
        if body is not None:
            self._len[nid] = len(body)
            return False
        base = {"INT_ZEXT": 7, "INT_CARRY": 9}.get(n[1], 3 * len(kids) + 1) if n[0] == "op" else 5
        plen = base + sum(3 if k in self._name else self._len[k] for k in kids)
        self._len[nid] = plen
        if _split_index(n) is not None or _zext_reg(n):
            return False
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
def _image_lines(mem0, cov):
    """``image { .. }``: runs of nonzero bytes outside the declared data
    regions (``cov`` marks carved addresses), 16 per row, packed hex pairs."""
    out = ["image {"]
    row = []
    for a in range(0x10000):
        if mem0[a] and not cov[a]:
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


# ---- data + symbols sections (song-data declarations, role aliases) -------------
def _decl_attrs(d):
    parts = []
    if d["stride"] > 1:
        parts.append("stride %d" % d["stride"])
    parts.extend("+%s" % _addr_name(b) for b in d["cobases"])
    if d["role"] is not None:
        parts.append("%s %s" % (d["role"][0], _addr_name(d["role"][1])))
    if d["via"] is not None:
        parts.append("via %s" % _addr_name(d["via"]))
    if d["targets"] is not None:
        parts.append("-> $%04X..$%04X" % d["targets"])
    if d["cmp"]:
        parts.append("cmp " + " ".join("$%02X" % v for v in d["cmp"]))
    if d["dispatch"]:
        parts.append("dispatch " + " ".join("$%04X" % v for v in d["dispatch"]))
    if d["observed"]:
        parts.append("observed")
    return "".join(" " + p for p in parts)


def _data_lines(decls, mem0):
    """``data { .. }`` plus the carved-address mask; declarations partition
    their regions out of the image (asserted disjoint, bytes attached)."""
    cov = bytearray(0x10000)
    if not decls:
        return [], cov
    out = ["data {"]
    end = 0
    for d in decls:
        assert end <= d["base"] and len(d["data"]) == d["size"] > 0
        end = d["base"] + d["size"]
        assert bytes(mem0[d["base"] : end]) == d["data"]
        cov[d["base"] : end] = b"\x01" * d["size"]
        out.append(" %s %s[%d]%s:" % (d["kind"], _addr_name(d["base"]), d["size"], _decl_attrs(d)))
        out.extend("  " + d["data"][k : k + 16].hex().upper() for k in range(0, d["size"], 16))
    out.append("}")
    return out, cov


_DECL_HEAD = re.compile(r"(table|stream) (\S+)\[(\d+)\]( [^:]*)?:$")
_TARGETS = re.compile(r"\$([0-9A-F]{4})\.\.\$([0-9A-F]{4})$")


def _req_name(name):
    addr = _name_addr(name)
    if addr is None:
        raise ValueError("not a canonical cell name: %r" % name)
    return addr


def _parse_decl(line):
    m = _DECL_HEAD.match(line)
    if not m:
        raise ValueError("bad data declaration %r" % line)
    d = {
        "kind": m.group(1),
        "base": _req_name(m.group(2)),
        "size": int(m.group(3)),
        "stride": 1,
        "cobases": [],
        "role": None,
        "via": None,
        "targets": None,
        "cmp": [],
        "dispatch": [],
        "observed": False,
        "data": b"",
    }
    toks = (m.group(4) or "").split()
    j = 0
    while j < len(toks):
        t = toks[j]
        if t == "stride":
            d["stride"] = int(toks[j + 1])
            j += 2
        elif t.startswith("+"):
            d["cobases"].append(_req_name(t[1:]))
            j += 1
        elif t in ("lo", "hi"):
            d["role"] = (t, _req_name(toks[j + 1]))
            j += 2
        elif t == "via":
            d["via"] = _req_name(toks[j + 1])
            j += 2
        elif t == "->":
            tm = _TARGETS.match(toks[j + 1])
            if not tm:
                raise ValueError("bad target span %r" % toks[j + 1])
            d["targets"] = (int(tm.group(1), 16), int(tm.group(2), 16))
            j += 2
        elif t in ("cmp", "dispatch"):
            j += 1
            vals = []
            while j < len(toks) and toks[j].startswith("$"):
                vals.append(int(toks[j][1:], 16))
                j += 1
            d[t] = vals
        elif t == "observed":
            d["observed"] = True
            j += 1
        else:
            raise ValueError("unknown data attribute %r" % t)
    return d


_ALIAS_LINE = re.compile(r"alias ([A-Za-z]\w*) = (\S+)$")
_RESERVED = re.compile(r"[ut]\d+$|r\d+$|mem$|carry$|zext[12]$|goto$|if$|ifnot$|loop$")


def _check_alias(name):
    if _name_addr(name) is not None or name in _NAME_REGS or _RESERVED.match(name):
        raise ValueError("alias %r shadows a reserved name" % name)
    return name


def _symbol_lines(aliases):
    if not aliases:
        return []
    out = ["symbols {"]
    out.extend(" alias %s = %s" % (aliases[c], _addr_name(c)) for c in sorted(aliases))
    out.append("}")
    return out


def _alias_res(aliases):
    """``(apply, strip)`` body-line substitutions for the {cell: alias} table;
    the table is the only mapping (strict bijection, checked here)."""
    if not aliases:
        return None, None
    fwd = {}
    rev = {}
    for cell, name in aliases.items():
        _check_alias(name)
        fwd[_addr_name(cell)] = name
        rev[name] = _addr_name(cell)
    if len(rev) != len(fwd):
        raise ValueError("alias table is not a bijection")
    fpat = re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, fwd)))
    rpat = re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, rev)))
    return (
        lambda s: fpat.sub(lambda m: fwd[m.group(0)], s),
        lambda s: rpat.sub(lambda m: rev[m.group(0)], s),
    )


def _items(region):
    return region.a if region.kind == "seq" else [region]


def _br_pen(term):
    """Static taken penalty of a branch: 1 + page-cross(target, fallthrough)."""
    return 1 + ((term[2] & 0xFF00) != (term[3] & 0xFF00))


class _SortedView:
    """Model facade with canonical (sorted) variant order for structuring;
    ``hidden`` pcs (already serialized by an earlier proc) become goto labels."""

    def __init__(self, model):
        self.blocks = model.blocks
        self.mem0 = model.mem0
        self.play = getattr(model, "play", None)
        self.dyn_targets = model.dyn_targets
        self.dispatch_pcs = set(model.dispatch_sets)
        self.hidden = set()
        by_pc = {}
        for key in sorted(model.blocks):
            by_pc.setdefault(key[0], []).append(key)
        self._by_pc = by_pc

    def variants(self, pc):
        return () if pc in self.hidden else self._by_pc.get(pc, ())

    def all_variants(self, pc):
        return self._by_pc.get(pc, ())


def _left_entry(view, left):
    """Next leftover-fragment entry: prefer a chain head (no unserialized pred)."""
    gp = _static_preds(view)
    for key in left:
        if all(q in view.hidden for q in gp.get(key[0], ())):
            return key[0]
    return left[0][0]


def _need_pcs(model):
    """Pcs dynamic control can land on at run time (must stay resolvable)."""
    need = set()
    rets = {(b.term[2] + 1) & 0xFFFF for b in model.blocks.values() if b.term[0] == "jsr"}
    ev_tg = getattr(model, "ev_targets", {})
    for blk in model.blocks.values():
        t = blk.term
        site = blk.pcs[-1]
        if t[0] == "jsr" and t[1] is not None:
            need.add(t[1])
        elif t[0] in ("jsr", "jmpd", "jmpind") or (t[0] == "br" and t[5] is not None):
            need.update(model.dyn_targets.get(site, ()))
            need.update(ev_tg.get(site, ()))
        elif t[0] == "rts":  # observed RTS-trick landings (call returns excluded)
            need.update(set(ev_tg.get(site, ())) - rets)
    return need


def _tree_keys(root, dups=None):
    """Primary block keys a region tree serializes (dispatch and call arms
    included); duplicate-copy keys collect into ``dups`` instead."""
    keys = set()
    stack = [root]
    while stack:
        r = stack.pop()
        k = r.kind
        if k == "block":
            if r.b is None and r.c is not None:
                if dups is not None:
                    dups.add((r.a.pc, r.a.op0))
            else:
                keys.add((r.a.pc, r.a.op0))
        elif k == "seq":
            stack.extend(r.a)
        elif k == "loop":
            stack.append(r.a)
        elif k == "if":
            stack.extend(c for c in (r.b, r.c) if c is not None)
        elif k == "switch":
            for _lbl, body in r.a[1]:
                if body is None:
                    continue
                if body.kind == "call":
                    if body.b is not None:
                        stack.append(body.b)
                else:
                    stack.append(body)
    return keys


class _Writer:
    """Serializes region trees; the nesting carries the flow (no goto soup).
    ``view`` is the model facade when emitting from a block model, else None."""

    def __init__(self, labels, dispatch, view):
        self.labels = labels
        self.dispatch = dispatch
        self.view = view
        self.out = []
        self.open = False  # last emitted line is another block's payload

    def line(self, text, d):
        self.out.append(" " * d + text)
        self.open = False

    def payline(self, text, d):
        self.out.append(" " * d + text)
        self.open = True

    def proc(self, entry, root):
        self.line("proc $%04X {" % entry, 0)
        self.seq(_items(root), 1)
        self.line("}", 0)

    def capture(self, items, d):
        keep, flag = self.out, self.open
        self.out, self.open = [], False
        self.seq(items, d)
        buf, self.out, self.open = self.out, keep, flag
        return buf

    def seq(self, items, d):
        i = 0
        while i < len(items):
            r = items[i]
            k = r.kind
            if k == "block":
                i += self.block(r, items[i + 1] if i + 1 < len(items) else None, d)
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
                self.line("goto $%04X" % r.a, d)
            elif k in ("cont", "brk"):
                self.line("continue" if k == "cont" else "break", d)
            else:
                raise ValueError("unexpected %s region in sequence" % k)
            i += 1

    def block(self, r, nxt, d):
        blk = r.a
        if self.view is not None and r.b is not None and blk.pc in self.dispatch:
            if r.b in self.labels:
                self.line("$%04X:" % r.b, d)
            self.line("switch code[$%04X] {" % blk.pc, d)
            self.line("case $%02X: {" % blk.op0, d + 1)
            consumed = self._leaf(r, nxt, d + 2, False)
            self.line("}", d + 1)
            self.line("}", d)
            return consumed
        return self._leaf(r, nxt, d, True)

    def _leaf(self, r, nxt, d, labelled):
        blk = _stmt_view(r.a)
        cse = _Cse(_roots(blk))
        shadow = _shadow(blk, cse)
        lines = cse.binding_lines()
        pend = None
        for l in _block_lines(shadow):
            l = _rename(l)
            if _CYC_LINE.fullmatch(l):
                pend = l
                continue
            lines.append(pend + " " + l if pend else l)
            pend = None
        if pend:
            lines.append(pend)
        lbl = r.b if labelled and r.b is not None and r.b in self.labels else None
        boundary = r.b if r.b is not None else r.c
        if lbl is None and labelled and self.open and lines and boundary is not None:
            lbl = boundary  # boundary label: separates adjacent fallthrough payloads
        if lbl is not None:
            self.line("$%04X:" % lbl, d)
        for l in lines:
            self.payline(l, d + 1)
        return self._flow(blk, shadow.term, nxt, d, bool(lines))

    def _flow(self, blk, term, nxt, d, has_payload):
        k = term[0]
        if nxt is not None and nxt.kind == "if":
            if k != "br" or term[5] is not None:
                raise ValueError("if region without a static branch terminator")
            pen = nxt.a if isinstance(nxt.a, int) else _br_pen(blk.term)
            head = "if" if term[1] else "ifnot"
            self.line("%s @t%d %s {" % (head, pen, _rename(fmt_expr(term[4]))), d)
            self.seq(_items(nxt.b), d + 1)
            els = self.capture(_items(nxt.c), d + 1) if nxt.c is not None else []
            if els:
                self.line("} else {", d)
                self.out.extend(els)
            self.line("}", d)
            return 2
        if nxt is not None and nxt.kind == "switch" and not nxt.b:
            for l in _term_lines(term):
                self.line(_rename(l), d + 1)
            sel, cases = nxt.a
            vector = k == "jmpind" and term[1] is not None
            if vector and self.view is not None and not self.view.dyn_targets.get(blk.pcs[-1]):
                for _lbl, arm in cases:  # image-derived target: never recorded
                    self.seq(_items(arm), d)
            elif sel == "call":
                self.call_switch(cases, d)
            else:
                self.line("switch goto {", d)
                for lbl, arm in cases:
                    self.line("case %s: {" % lbl, d + 1)
                    self.seq(_items(arm), d + 2)
                    self.line("}", d + 1)
                self.line("}", d)
            return 2
        if k == "br" and term[5] is None:
            raise ValueError("static branch terminator without an if region")
        printed = _term_lines(term)
        for l in printed:
            self.line(_rename(l), d + 1)
        if not printed and has_payload:
            self.open = True  # implicit fallthrough: payload stays the last line
        return 2 if nxt is not None and nxt.kind == "exit" else 1

    def call_switch(self, cases, d):
        """``switch call``: bare-target list plus case arms for inlined handlers."""
        bare = [lbl for lbl, arm in cases if arm is None or arm.b is None]
        bodied = [(lbl, arm.b) for lbl, arm in cases if arm is not None and arm.b is not None]
        if not bodied:
            body = " ".join(bare)
            self.line("switch call { %s }" % body if body else "switch call { }", d)
            return
        self.line("switch call {", d)
        if bare:
            self.line(" ".join(bare), d + 1)
        for lbl, body in bodied:
            self.line("case %s: {" % lbl, d + 1)
            self.seq(_items(body), d + 2)
            self.line("}", d + 1)
        self.line("}", d)

    def opcode_switch(self, r, d):
        sel, cases = r.a
        if r.b and r.b[0] in self.labels:
            self.line("$%04X:" % r.b[0], d)
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


def _inlined_arms(trees):
    """Call targets whose handler tree is inlined as a switch-call case arm."""
    out = set()
    stack = [root for _e, root in trees]
    while stack:
        r = stack.pop()
        k = r.kind
        if k == "seq":
            stack.extend(r.a)
        elif k == "loop":
            stack.append(r.a)
        elif k == "if":
            stack.extend(c for c in (r.b, r.c) if c is not None)
        elif k == "switch":
            for _lbl, body in r.a[1]:
                if body is None:
                    continue
                if body.kind == "call":
                    if body.b is not None:
                        out.add(body.a)
                        stack.append(body.b)
                else:
                    stack.append(body)
    return out


def _model_trees(model):
    """``(trees, labels, view)``: region trees for every procedure and the full
    label set (goto + dynamic-branch targets; proc entries need no label)."""
    view = _SortedView(model)
    trees = []
    labels = set()
    done = set()
    dupped = set()
    owners = set()  # proc entries whose own tree serializes their entry block
    need = _need_pcs(model)  # dynamic landings must resolve at run time

    def build(entry):
        root, labs = codec.structure(view, entry)
        labels.update(labs)
        keys = _tree_keys(root, dupped)
        if any(k[0] == entry for k in keys):
            owners.add(entry)
        done.update(keys)
        trees.append((entry, root))
        view.hidden.update(k[0] for k in keys)

    def left_keys():
        return sorted(
            k
            for k in model.blocks
            if k not in done and not (k in dupped and k[0] not in need and k[0] not in labels)
        )

    for entry in _entries(model):
        build(entry)
    left = left_keys()
    while left:
        build(_left_entry(view, left))
        still = left_keys()
        if len(still) == len(left):
            raise ValueError("blocks unreachable from any procedure: %s" % still[:4])
        left = still
    labels |= need - _inlined_arms(trees)
    return trees, labels - owners, view


def emit(model):
    """Canonical sidprog text (``emit(parse(emit(m))) == emit(m)``)."""
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
    decls = getattr(model, "data_decls", None)
    aliases = getattr(model, "symbols", None)
    if decls is None:
        decls, aliases = datadecl.declarations(model)
    data_out, cov = _data_lines(decls, model.mem0)
    out.extend(_image_lines(model.mem0, cov))
    out.extend(data_out)
    out.extend(_symbol_lines(aliases))
    procs = getattr(model, "procs", None)
    if procs is not None:
        w = _Writer(set(model.labels), set(model.dispatch_sets), None)
        for entry, root in procs:
            w.proc(entry, root)
    else:
        trees, labels, view = _model_trees(model)
        w = _Writer(labels, set(model.dispatch_sets), view)
        for entry, root in trees:
            w.proc(entry, root)
    to_alias, _strip = _alias_res(aliases)
    out.extend(w.out if to_alias is None else map(to_alias, w.out))
    return "\n".join(out) + "\n"


def metrics(model):
    """Structuring metrics: {blocks, nested_blocks, structured_pct, goto_count,
    labels, dup_blocks} over the codec region trees (block leaves at nesting
    depth > 0 are structured; gotos, labels and duplicate copies are counted,
    not judged)."""
    view = _SortedView(model)
    done = set()
    dupped = set()
    labels = set()
    counts = {"blocks": 0, "nested": 0, "gotos": 0, "dups": 0}
    need = _need_pcs(model)

    def proc(entry):
        root, labs = codec.structure(view, entry)
        labels.update(labs)
        seen = set(done)
        stack = [(root, 0)]
        while stack:
            r, d = stack.pop()
            k = r.kind
            if k == "seq":
                stack.extend((c, d) for c in r.a)
            elif k == "block":
                if r.b is None and r.c is not None:
                    counts["dups"] += 1
                    dupped.add((r.a.pc, r.a.op0))
                else:
                    counts["blocks"] += 1
                    counts["nested"] += 1 if d else 0
                    done.add((r.a.pc, r.a.op0))
            elif k == "loop":
                stack.append((r.a, d + 1))
            elif k == "if":
                stack.extend((c, d + 1) for c in (r.b, r.c) if c is not None)
            elif k == "switch":
                stack.extend((body, d + 1) for _lbl, body in r.a[1])
            elif k == "call" and r.b is not None:
                stack.append((r.b, d))
            elif k == "goto":
                counts["gotos"] += 1
        view.hidden.update(key[0] for key in done - seen)

    def left_keys():
        return sorted(
            k
            for k in model.blocks
            if k not in done and not (k in dupped and k[0] not in need and k[0] not in labels)
        )

    for entry in _entries(model):
        proc(entry)
    left = left_keys()
    while left:
        proc(_left_entry(view, left))
        still = left_keys()
        if len(still) == len(left):
            break
        left = still
    blocks, nested = counts["blocks"], counts["nested"]
    return {
        "blocks": blocks,
        "nested_blocks": nested,
        "structured_pct": 100.0 * nested / blocks if blocks else 100.0,
        "goto_count": counts["gotos"],
        "labels": len(labels),
        "dup_blocks": counts["dups"],
    }


# ---- parsed model, linker, tree walker ------------------------------------------
class _R:
    """Parsed region node, duck-typing the structurer's Region (kind/a/b/c)."""

    __slots__ = ("kind", "a", "b", "c")

    def __init__(self, kind, a=None, b=None, c=None):
        self.kind = kind
        self.a = a
        self.b = b
        self.c = c


class TextModel:
    """Parsed sidprog program: region-tree procedures over compiled block
    payloads. Also constructible from pc-keyed blocks (``emit`` restructures)."""

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
        self.procs = None  # [(entry, seq region)] when parsed from text
        self.labels = set()
        self.data_decls = []  # data { } declarations (bytes carved from mem0)
        self.symbols = {}  # {cell: alias} role-alias bijection
        self.prog = None  # linked flat program: [block-or-None, ctrl] rows
        self.pcmap = None  # serialized pc -> prog index (nodes that ARE the pc)
        self.contmap = None  # call-return pc -> continuation index (flow nodes)
        by_pc = {}
        for key in sorted(blocks):
            by_pc.setdefault(key[0], []).append(key)
        self._by_pc = by_pc

    def variants(self, pc):
        return self._by_pc.get(pc, ())

    def link(self):
        """Resolve the region trees to a flat control program (idempotent)."""
        if self.prog is not None:
            return self
        if self.procs is None:
            raise ValueError("not a tree model: construct via parse()")
        self.prog = []
        self.pcmap = {}
        self.contmap = {}
        gotos = []
        for entry, root in self.procs:
            idx = self._lseq(_items(root), None, [], gotos)
            if idx is not None:
                self.pcmap.setdefault(entry, idx)
        for j, pc in gotos:  # authoritative nodes only: a flow node never aliases a pc
            self.prog[j][1] = ("next", self.pcmap.get(pc))
        return self

    def resolve_pc(self, pc):
        """Program index a run-time pc lands on, else None (loud-fault guard)."""
        idx = self.pcmap.get(pc)
        return self.contmap.get(pc) if idx is None else idx

    def node_at(self, pc):
        """Linked program index of a serialized pc; WalkError outside the program."""
        self.link()
        idx = self.resolve_pc(pc)
        if idx is None:
            raise C.WalkError("pc $%04X outside program" % pc)
        return idx

    def run(self, frames):
        """Execute the tree program standalone; the (cycle, reg, value) SID log."""
        return TreeWalker(self).run(frames)

    def _lseq(self, items, follow, loops, gotos):
        prog = self.prog
        nxt = follow
        pend = None
        for r in reversed(items):
            k = r.kind
            if k == "block":
                nxt = self._lblock(r, pend, nxt, loops, gotos)
                pend = None
                continue
            if pend is not None:
                raise ValueError("flow region %s not attached to a block" % pend.kind)
            if k == "if" or (k == "switch" and not r.b):
                pend = r
            elif k == "switch":
                omap = {
                    int(lbl[1:], 16): self._lseq(_items(arm), nxt, loops, gotos)
                    for lbl, arm in r.a[1]
                }
                prog.append([None, ("op", r.b[0], omap)])
                nxt = len(prog) - 1
                self.pcmap.setdefault(r.b[0], nxt)
            elif k == "seq":
                nxt = self._lseq(r.a, nxt, loops, gotos)
            elif k == "loop":
                prog.append([None, None])
                ti = len(prog) - 1
                body = self._lseq(_items(r.a), None, loops + [(ti, nxt)], gotos)
                prog[ti][1] = ("next", body)
                nxt = ti
            elif k == "goto":
                prog.append([None, None])
                gotos.append((len(prog) - 1, r.a))
                nxt = len(prog) - 1
            elif k == "cont":
                nxt = loops[-1][0]
            elif k == "brk":
                nxt = loops[-1][1]
            elif k != "exit":
                raise ValueError("unexpected %s region" % k)
        if pend is not None:
            raise ValueError("flow region %s not attached to a block" % pend.kind)
        return nxt

    def _lblock(self, r, pend, nxt, loops, gotos):
        blk = r.a
        term = blk.term
        k = term[0]
        if pend is not None:
            ctrl = self._lpend(term, pend, nxt, loops, gotos)
        elif k == "rts":
            ctrl = ("ret",)
        elif k == "jsr":
            ctrl = ("call", term[1], term[2], nxt)
            if nxt is not None:  # ret pc is serialized: rts resolves through it
                self.contmap.setdefault((term[2] + 1) & 0xFFFF, nxt)
        elif k == "br":
            if term[5] is None:
                raise ValueError("static branch without an if region")
            ctrl = ("dbr", term[1], term[3], nxt)
        elif k == "jmpd":
            ctrl = ("jmpd", {})
        elif k == "jmpind":  # inline arm: the vector's image value falls through
            ptr = term[1]
            v = None
            if ptr is not None and term[2] is None:
                v = self.mem0[ptr] | (self.mem0[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8)
            ctrl = ("vec", ptr, term[2] is not None, {} if v is None else {v: nxt})
        else:  # goto fallthrough
            ctrl = ("next", nxt)
        self.prog.append([blk, ctrl])
        idx = len(self.prog) - 1
        if r.b is not None:
            self.pcmap.setdefault(r.b, idx)
        return idx

    def _lpend(self, term, pend, nxt, loops, gotos):
        k = term[0]
        if pend.kind == "if":
            if k != "br" or term[5] is not None:
                raise ValueError("if region without a static branch terminator")
            ti = self._lseq(_items(pend.b), nxt, loops, gotos)
            ei = self._lseq(_items(pend.c), nxt, loops, gotos) if pend.c is not None else nxt
            return ("br", term[1], pend.a, ti, ei)
        sel, cases = pend.a
        if sel == "call":
            if k != "jsr":
                raise ValueError("switch call without a call terminator")
            for lbl, arm in cases:
                body = arm.b if arm is not None and arm.kind == "call" else None
                if body is not None:  # inlined handler: the callee's own tree
                    ai = self._lseq(_items(body), None, [], gotos)
                    if ai is not None:
                        self.pcmap.setdefault(int(lbl[1:], 16), ai)
            if nxt is not None:
                self.contmap.setdefault((term[2] + 1) & 0xFFFF, nxt)
            return ("call", None, term[2], nxt)
        cmap = {}
        for lbl, arm in cases:  # arm entries are flow, not the pc's block: no pcmap
            cmap[int(lbl[1:], 16)] = self._lseq(_items(arm), None, loops, gotos)
        if k == "jmpd":
            return ("jmpd", cmap)
        if k == "jmpind":
            return ("vec", term[1], term[2] is not None, cmap)
        raise ValueError("switch goto without a computed-jump terminator")


class TreeWalker:
    """Executes a parsed tree program from its initial image alone, reproducing
    ``structured.Walker`` semantics (cycle-stamped SID write log) bit-exactly."""

    def __init__(self, tm):
        self.tm = tm.link()
        self.m = bytearray(tm.mem0)
        self.r = [0] * 16
        self.r[3] = 0xFF
        self.c = 0
        self.wlog = []

    def _push(self, val):
        self.m[0x100 + self.r[3]] = val & 0xFF
        self.r[3] = (self.r[3] - 1) & 0xFF

    def run_frame(self):
        self._entry(self.tm.play)

    def run(self, frames):
        for _ in range(frames):
            self._entry(self.tm.play)
        return self.wlog

    def _entry(self, entry, acc=0):
        prog = self.tm.prog
        m = self.m
        start = self.r[3]
        self.r[0] = acc & 0xFF
        self._push(0x00)
        self._push(0x01)
        idx = self.tm.pcmap.get(entry)
        n = 0
        while self.r[3] < start:
            if idx is None:
                raise C.WalkError("control left the serialized program")
            blk, ctrl = prog[idx]
            x = None
            if blk is not None:
                if blk.fn is None:
                    blk.fn = C.compile_block(blk)
                self.c, self.r, x = blk.fn(
                    m, self.r, self.c, self.wlog, C.volatile_read, C._dyn_read, C._sid_log
                )
            idx = self._step(ctrl, x)
            n += 1
            if n > C._GUARD:
                raise C.WalkError("runaway tree walk")

    def _step(self, ctrl, x):
        op = ctrl[0]
        if op == "next":
            return ctrl[1]
        if op == "br":
            if x[0] == ctrl[1]:
                self.c += ctrl[2]
                return ctrl[3]
            return ctrl[4]
        if op == "ret":  # the return address is real memory; the pc maps are the truth
            m = self.m
            sp = (self.r[3] + 1) & 0xFF
            lo = m[0x100 + sp]
            sp = (sp + 1) & 0xFF
            hi = m[0x100 + sp]
            self.r[3] = sp
            return self.tm.resolve_pc((((hi << 8) | lo) + 1) & 0xFFFF)
        if op == "call":
            _o, tgt, ret, _cont = ctrl
            if tgt is None:
                tgt = x
            self._push(ret >> 8)
            self._push(ret & 0xFF)
            idx = self.tm.resolve_pc(tgt)
            if idx is None:
                raise C.WalkError("call target $%04X outside program" % tgt)
            return idx
        if op == "op":
            b = self.m[ctrl[1]]
            idx = ctrl[2].get(b)
            if idx is None:
                raise C.WalkError("opcode $%02X at $%04X outside proven set" % (b, ctrl[1]))
            return idx
        if op == "jmpd":
            idx = ctrl[1].get(x)
            if idx is None:
                idx = self.tm.resolve_pc(x)
            if idx is None:
                raise C.WalkError("goto target $%04X outside program" % x)
            return idx
        if op == "vec":
            m = self.m
            ptr = x if ctrl[2] else ctrl[1]
            tgt = m[ptr] | (m[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)] << 8)
            idx = ctrl[3].get(tgt)
            if idx is None:
                idx = self.tm.resolve_pc(tgt)
            if idx is None:
                raise C.WalkError("vector target $%04X outside program" % tgt)
            return idx
        flag, tgt = x  # dbr: dynamic-target branch escape hatch
        if flag != ctrl[1]:
            return ctrl[3]
        self.c += 1 + ((ctrl[2] & 0xFF00) != (tgt & 0xFF00))
        idx = self.tm.resolve_pc(tgt)
        if idx is None:
            raise C.WalkError("branch target $%04X outside program" % tgt)
        return idx


# ---- parsing ----------------------------------------------------------------------
class _Acc:
    def __init__(self, label):
        self.label = label
        self.events = []
        self.regs = [E.reg(i) for i in range(16)]
        self.term = None
        self.bind = {}

    def empty(self):
        return not (self.events or self.bind or self.term) and all(
            r == ("reg", i) for i, r in enumerate(self.regs)
        )


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


def _acc_block(acc):
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
    term = _map_term(acc.term if acc.term is not None else ("goto", None), x)
    pc = acc.label if acc.label is not None else 0
    return C.Block(pc, 0, [pc], events, term, regs)


_LABEL = re.compile(r"\$([0-9A-Fa-f]{1,4}):$")
_BINDING = re.compile(r"t(\d+) = (.*)$")
_CASE = re.compile(r"case \$([0-9A-Fa-f]+): \{$")
_IF_HDR = re.compile(r"(if|ifnot) @t(\d+) (.*) \{$")
_SW_CODE = re.compile(r"switch code\[\$([0-9A-Fa-f]{4})\] \{$")
_SW_CALL = re.compile(r"switch call \{ ?(.*?) ?\}$")
_PC_LIST = re.compile(r"\$[0-9A-Fa-f]{1,4}( \$[0-9A-Fa-f]{1,4})*$")


class _Parser:
    """Stack-based reader of proc bodies into region trees + block payloads."""

    def __init__(self):
        self.labels = set()
        self.procs = []
        self.stack = []
        self.cur = None

    def top_items(self):
        return self.stack[-1][-1]

    def ensure(self):
        if self.cur is None:
            self.cur = _Acc(None)
        return self.cur

    def flush(self):
        if self.cur is not None:
            self.top_items().append(_R("block", _acc_block(self.cur), self.cur.label))
            self.cur = None

    def close(self):
        self.flush()
        f = self.stack.pop()
        k = f[0]
        if k == "proc":
            self.procs.append((f[1], _R("seq", f[2])))
        elif k == "loop":
            self.top_items().append(_R("loop", _R("seq", f[1])))
        elif k == "then":
            self.top_items().append(_R("if", f[1], _R("seq", f[2]), None))
        elif k == "else":
            self.top_items().append(_R("if", f[1], f[2], _R("seq", f[3])))
        elif k == "case":
            self.stack[-1][-1].append((f[1], _R("seq", f[2])))
        elif k == "swg":
            self.top_items().append(_R("switch", ("goto", f[1]), []))
        elif k == "swcl":
            cases = [(lbl, _R("call", int(lbl[1:], 16))) for lbl in f[1]]
            cases += [(lbl, _R("call", int(lbl[1:], 16), arm)) for lbl, arm in f[2]]
            self.top_items().append(_R("switch", ("call", cases), []))
        else:  # swc
            self.top_items().append(_R("switch", ("code[$%04X]" % f[1], f[2]), [f[1]]))

    def else_arm(self):
        self.flush()
        f = self.stack.pop()
        if f[0] != "then":
            raise ValueError("'} else {' outside an if region")
        self.stack.append(["else", f[1], _R("seq", f[2]), []])

    def structural(self, line):
        """Handle one flow-structure line; False when it is block payload."""
        if self.stack[-1][0] == "swcl" and line != "}" and not line.startswith("case "):
            if not _PC_LIST.match(line):
                raise ValueError("bad switch call target list %r" % line)
            self.stack[-1][1].extend(line.split())
            return True
        if line == "}":
            self.close()
        elif line == "} else {":
            self.else_arm()
        elif line == "loop {":
            self.flush()
            self.stack.append(["loop", []])
        elif line in ("continue", "break"):
            self.flush()
            self.top_items().append(_R("cont" if line == "continue" else "brk"))
        elif line == "switch goto {":
            self.flush()
            self.stack.append(["swg", []])
        elif line == "switch call {":
            self.flush()
            self.stack.append(["swcl", [], []])
        else:
            return self._structural2(line)
        return True

    def _structural2(self, line):
        m = _LABEL.match(line)
        if m:
            self.flush()
            pc = int(m.group(1), 16)
            self.labels.add(pc)
            self.cur = _Acc(pc)
            return True
        m = _SW_CODE.match(line)
        if m:
            if self.cur is not None and self.cur.empty():
                self.cur = None  # a label line here names the switch subject
            self.flush()
            self.stack.append(["swc", int(m.group(1), 16), []])
            return True
        m = _SW_CALL.match(line)
        if m:
            self.flush()
            tgts = [int(v.lstrip("$"), 16) for v in m.group(1).split()]
            cases = [("$%04X" % t, _R("call", t)) for t in tgts]
            self.top_items().append(_R("switch", ("call", cases), []))
            return True
        m = _CASE.match(line)
        if m:
            wide = self.stack[-1][0] in ("swg", "swcl")
            self.stack.append(["case", ("$%04X" if wide else "$%02X") % int(m.group(1), 16), []])
            return True
        m = _IF_HDR.match(line)
        if m:
            acc = self.ensure()
            pol = 1 if m.group(1) == "if" else 0
            acc.term = ("br", pol, None, None, parse_expr(_t2u(m.group(3))), None)
            self.flush()
            self.stack.append(["then", int(m.group(2)), []])
            return True
        if line.startswith("goto $"):
            self.flush()
            self.top_items().append(_R("goto", int(line[6:], 16)))
            return True
        return False

    def terminator(self, line):
        """Handle an explicit terminator line; False when it is not one."""
        if line == "ret" or line.startswith(("if ", "ifnot ", "call ", "igoto ", "goto (")):
            _parse_line(self.ensure(), _t2u(line))
            self.flush()
            return True
        return False

    def payload(self, line):
        acc = self.ensure()
        m = _BINDING.match(line)
        if m:
            node = parse_expr(_t2u(m.group(2)))
            acc.bind[int(m.group(1))] = _expand(node, acc.bind, {})
            return
        m = _CYC_FUSED.match(line)
        if m:
            _parse_line(acc, "@" + m.group(1))
            line = m.group(2)
        _parse_line(acc, _t2u(line))


def parse(text):
    """Parse canonical sidprog text into a tree-walkable ``TextModel``."""
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
    prologue = []
    data_decls = []
    symbols = {}
    unalias = None
    p = _Parser()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if p.stack:
            if unalias is None:
                unalias = _alias_res(symbols)[1] or str
            line = unalias(line)
            if not (p.structural(line) or p.terminator(line)):
                p.payload(line)
            continue
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
            while lines[i] != "}":
                reg, val = lines[i].split("=")
                prologue.append(
                    (int(reg.strip().lstrip("$"), 16), int(val.strip().lstrip("$"), 16))
                )
                i += 1
            i += 1
        elif line == "image {":
            while lines[i] != "}":
                addr, bytestr = lines[i].split(":", 1)
                a = int(addr.strip().lstrip("$"), 16)
                run = bytestr.strip()
                for k in range(0, len(run), 2):
                    mem0[a + k // 2] = int(run[k : k + 2], 16)
                i += 1
            i += 1
        elif line == "data {":
            while lines[i] != "}":
                if _DECL_HEAD.match(lines[i]):
                    data_decls.append(_parse_decl(lines[i]))
                else:
                    data_decls[-1]["data"] += bytes.fromhex(lines[i])
                i += 1
            i += 1
            for d in data_decls:
                if len(d["data"]) != d["size"]:
                    raise ValueError(
                        "data region %s[%d] carries %d bytes"
                        % (_addr_name(d["base"]), d["size"], len(d["data"]))
                    )
                mem0[d["base"] : d["base"] + d["size"]] = d["data"]
        elif line == "symbols {":
            while lines[i] != "}":
                m = _ALIAS_LINE.match(lines[i])
                if not m:
                    raise ValueError("bad alias line %r" % lines[i])
                cell = _req_name(m.group(2))
                if cell in symbols:
                    raise ValueError("duplicate alias for %s" % m.group(2))
                symbols[cell] = _check_alias(m.group(1))
                i += 1
            i += 1
        elif line.startswith("proc "):
            p.stack.append(["proc", int(line.split()[1].lstrip("$"), 16), []])
        else:
            raise ValueError("unexpected top-level line %r" % line)
    if p.stack or p.cur is not None:
        raise ValueError("unbalanced braces at end of document")
    if init is None or play is None:
        raise ValueError("missing init/play header")
    tm = TextModel(mem0, init, play, {}, dispatch_sets, subtune, prologue)
    tm.procs = p.procs
    tm.labels = p.labels
    tm.data_decls = data_decls
    tm.symbols = symbols
    return tm


dumps = emit  # spec vocabulary (section 6); loads/dumps are inverses
loads = parse
