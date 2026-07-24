"""SIDC: canonical structured text for decompiled playroutines (plan P5-P6).

``emit`` renders a decompiled model as procedures of labeled blocks over an
initial image; ``parse`` is its exact inverse; the parsed model drives the
same compiled-block walker, standalone and cycle-exact.
"""

from __future__ import annotations

import re

from . import expr as E
from . import structured as C

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


# ---- emission -----------------------------------------------------------------
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


def _static_succ(term):
    if term[0] in ("goto", "jmp"):
        return [term[1]]
    if term[0] == "br":
        return ([term[2]] if term[2] is not None else []) + [term[3]]
    if term[0] == "jsr":
        return [(term[2] + 1) & 0xFFFF]
    return []


def _proc_layout(model):
    """Assign blocks to procedures (init, play, jsr targets, leftovers) and
    lay each out depth-first for fallthrough elision."""
    entries = [model.init, model.play]
    targets = {
        blk.term[1]
        for blk in model.blocks.values()
        if blk.term[0] == "jsr" and blk.term[1] is not None
    }
    entries.extend(pc for pc in sorted(targets) if pc not in entries)
    assigned = set()
    procs = []
    for entry in entries:
        order = []
        stack = sorted(model.variants(entry), reverse=True)
        while stack:
            key = stack.pop()
            if key in assigned or key not in model.blocks:
                continue
            assigned.add(key)
            order.append(key)
            succs = []
            for pc in _static_succ(model.blocks[key].term):
                succs.extend(model.variants(pc))
            stack.extend(sorted(succs, reverse=True))
        if order:
            procs.append((entry, order))
    rest = sorted(k for k in model.blocks if k not in assigned)
    if rest:
        procs.append((rest[0][0], rest))
    return procs


def emit(model):
    """Canonical SIDC text (``parse`` is its exact inverse)."""
    out = ["sidc 0", "init $%04X" % model.init, "play $%04X" % model.play]
    for pc in sorted(model.dispatch_sets):
        out.append(
            "dispatch $%04X: %s"
            % (pc, " ".join("$%02X" % v for v in sorted(model.dispatch_sets[pc])))
        )
    out.append("image {")
    row = []
    mem0 = model.mem0
    for a in range(0x10000):
        if mem0[a]:
            if row and (a != row[0] + len(row) - 1 or len(row) - 1 >= 16):
                out.append("  $%04X: %s" % (row[0], " ".join(row[1:])))
                row = []
            if not row:
                row = [a]
            row.append("%02X" % mem0[a])
    if row:
        out.append("  $%04X: %s" % (row[0], " ".join(row[1:])))
    out.append("}")
    dispatch = set(model.dispatch_sets)
    for entry, order in _proc_layout(model):
        out.append("proc $%04X {" % entry)
        for i, key in enumerate(order):
            blk = model.blocks[key]
            nxt = order[i + 1][0] if i + 1 < len(order) else None
            out.append("%s:" % _label(key, dispatch))
            out.extend("  " + l for l in _block_lines(blk))
            out.extend("  " + l for l in _term_lines(blk.term, nxt))
        out.append("}")
    return "\n".join(out) + "\n"


# ---- parsing ------------------------------------------------------------------
class TextModel:
    """Parsed SIDC program; duck-types ``structured.Model`` for the Walker."""

    def __init__(self, mem0, init, play, blocks, dispatch_sets):
        self.mem0 = bytes(mem0)
        self.init = init
        self.play = play
        self.blocks = blocks
        self.dispatch_sets = dispatch_sets
        self.written = set(dispatch_sets)
        self.pcs = {pc: {op} for pc, op in blocks if pc not in dispatch_sets}

    def variants(self, pc):
        return [key for key in self.blocks if key[0] == pc]

    def lookup(self, pc, m):
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


class _BlockAccum:
    def __init__(self, pc, op0):
        self.key = (pc, op0)
        self.events = []
        self.regs = [E.reg(i) for i in range(16)]
        self.term = None
        self.nuni = 0

    def finish(self, next_pc):
        term = self.term
        if term is None:
            if next_pc is None:
                raise ValueError("block $%04X falls through nowhere" % self.key[0])
            term = ("goto", next_pc)
        elif term[0] == "br" and term[3] is None:
            if next_pc is None:
                raise ValueError("block $%04X falls through nowhere" % self.key[0])
            term = term[:3] + (next_pc,) + term[4:]
        return C.Block(self.key[0], self.key[1], [self.key[0]], self.events, term, self.regs)


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
        acc.nuni = max(acc.nuni, int(m.group(1)) + 1)
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


def parse(text):
    """Parse canonical SIDC text back into a walkable :class:`TextModel`."""
    lines = []
    for raw in text.splitlines():
        s = raw.split(";", 1)[0].strip()
        if s:
            lines.append(s)
    if not lines or lines.pop(0) != "sidc 0":
        raise ValueError("not a sidc 0 document")
    init = play = None
    mem0 = bytearray(0x10000)
    dispatch_sets = {}
    blocks = {}
    pending = []  # (accumulator, terminator-pending) in order, resolved per proc

    def close_proc():
        for i, acc in enumerate(pending):
            nxt = pending[i + 1].key[0] if i + 1 < len(pending) else None
            blk = acc.finish(nxt)
            blocks[blk.pc, blk.op0] = blk
        pending.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("init "):
            init = int(line.split()[1].lstrip("$"), 16)
        elif line.startswith("play "):
            play = int(line.split()[1].lstrip("$"), 16)
        elif line.startswith("dispatch "):
            head, vals = line[9:].split(":")
            dispatch_sets[int(head.strip().lstrip("$"), 16)] = {
                int(v.lstrip("$"), 16) for v in vals.split()
            }
        elif line == "image {":
            i += 1
            while lines[i] != "}":
                addr, bytestr = lines[i].split(":", 1)
                a = int(addr.strip().lstrip("$"), 16)
                for k, tok in enumerate(bytestr.split()):
                    mem0[a + k] = int(tok, 16)
                i += 1
        elif line.startswith("proc "):
            pass
        elif line == "}":
            close_proc()
        elif line.endswith(":"):
            lab = line[:-1]
            if "/" in lab:
                pcs, ops = lab.split("/")
                pc, op0 = int(pcs.lstrip("$"), 16), int(ops.lstrip("$"), 16)
            else:
                pc = int(lab.lstrip("$"), 16)
                op0 = mem0[pc]
            pending.append(_BlockAccum(pc, op0))
        else:
            _parse_line(pending[-1], line)
        i += 1
    close_proc()
    if init is None or play is None:
        raise ValueError("missing init/play header")
    return TextModel(mem0, init, play, blocks, dispatch_sets)
