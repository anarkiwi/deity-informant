"""The ONE grammar of the sidprog language and its frameprog dialect.

``sidprog.lark`` is normative and LALR(1); this module owns the name/address
bijection it resolves against and the reader that turns a parse into region
trees (sidprog) or statement trees (frameprog).
"""

from __future__ import annotations

import re
from pathlib import Path

import lark

from . import expr as E
from . import structured as C
from .render import sid_name

SIDPROG_VERSION = 1  # 1: play-phase structured program (spec section 6)
FRAMEPROG_VERSION = 0

GRAMMAR_PATH = Path(__file__).with_name("sidprog.lark")
GRAMMAR = GRAMMAR_PATH.read_text(encoding="utf-8")

BIND_BASE = 1 << 20  # tN bindings ride the uni namespace above any real slot


class SidprogVersionError(ValueError):
    """A document's ``<dialect> <major>`` header is not this reader's major."""


# ---- registers and the named-machine-state bijection ---------------------------
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
_SID_NAMES = {a: sid_name(a) for a in range(0xD400, 0xD419)}
_SID_ADDRS = {n: a for a, n in _SID_NAMES.items()}
_CELL_NAME = re.compile(r"(zp|m)_([0-9A-F]+)$")
_SLOT_NAME = re.compile(r"[utr]\d+$")
_T_NAME = re.compile(r"t(\d+)$")
_U_NAME = re.compile(r"u(\d+)$")
_R_NAME = re.compile(r"r\d+$")
_SUB_NAME = re.compile(r"sub_([0-9A-F]{4})$")


def reg_name(i):
    return _REG_NAMES.get(i, "r%d" % i)


def reg_index(name):
    if name in _NAME_REGS:
        return _NAME_REGS[name]
    if _R_NAME.match(name):
        return int(name[1:])
    raise ValueError("unknown register %r" % name)


def addr_name(v):
    """Canonical cell name for a 2-byte const address (total on 0..$FFFF)."""
    return _SID_NAMES.get(v) or ("zp_%02X" % v if v < 0x100 else "m_%04X" % v)


def name_addr(name):
    """Address named by a canonical cell name, else None (inverse of addr_name)."""
    a = _SID_ADDRS.get(name)
    if a is None:
        m = _CELL_NAME.match(name)
        if m:
            a = int(m.group(2), 16)
            if a > 0xFFFF or addr_name(a) != name:
                return None
    return a


def req_name(name):
    addr = name_addr(name)
    if addr is None:
        raise ValueError("not a canonical cell name: %r" % name)
    return addr


_KEYWORDS = None


def keywords():
    """Reserved words of the grammar (its literal identifier terminals)."""
    global _KEYWORDS  # pylint: disable=global-statement
    if _KEYWORDS is None:
        _KEYWORDS = frozenset(
            t.pattern.value
            for t in _parser().terminals
            if isinstance(t.pattern, lark.lexer.PatternStr) and t.pattern.value.isidentifier()
        )
    return _KEYWORDS


def check_alias(name):
    """An alias may shadow no keyword, canonical cell name, register or slot."""
    if name_addr(name) is not None or name in _NAME_REGS or _SLOT_NAME.match(name):
        raise ValueError("alias %r shadows a reserved name" % name)
    if name in keywords():
        raise ValueError("alias %r shadows a grammar keyword" % name)
    return name


# ---- expression-tree utilities -------------------------------------------------
def kids(n):
    if n[0] == "mem":
        return (n[1],)
    if n[0] == "op":
        return n[2]
    return ()


def rebuild(n, ks):
    if n[0] == "mem":
        return ("mem", ks[0], n[2])
    if n[0] == "op":
        return ("op", n[1], tuple(ks), n[3])
    return n


def map_term(term, f):
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


def expand(n, bind, memo):
    """Inline every ``tN`` binding reference in ``n`` (bindings are sugar)."""
    stack = [n]
    while stack:
        x = stack[-1]
        if id(x) in memo:
            stack.pop()
            continue
        if x[0] == "uni" and x[1] >= BIND_BASE:
            memo[id(x)] = bind[x[1] - BIND_BASE]
            stack.pop()
            continue
        todo = [k for k in kids(x) if id(k) not in memo]
        if todo:
            stack.extend(todo)
            continue
        stack.pop()
        memo[id(x)] = rebuild(x, [memo[id(k)] for k in kids(x)])
    return memo[id(n)]


class Region:
    """Parsed region node, duck-typing the structurer's Region (kind/a/b/c)."""

    __slots__ = ("kind", "a", "b", "c")

    def __init__(self, kind, a=None, b=None, c=None):
        self.kind = kind
        self.a = a
        self.b = b
        self.c = c


class _Acc:
    """One block under construction: events, register out-exprs, bindings."""

    __slots__ = ("label", "events", "regs", "term", "bind")

    def __init__(self, label):
        self.label = label
        self.events = []
        self.regs = [E.reg(i) for i in range(16)]
        self.term = None
        self.bind = {}


def acc_block(acc):
    """Compiled block payload with every ``tN`` binding inlined."""
    memo = {}

    def x(n):
        return expand(n, acc.bind, memo)

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
    term = map_term(acc.term if acc.term is not None else ("goto", None), x)
    pc = acc.label if acc.label is not None else 0
    return C.Block(pc, 0, [pc], events, term, regs)


# ---- parse-side operator tables -------------------------------------------------
_ADDSUB = frozenset("+-")
_CHAINOPS = {"|": "INT_OR", "^": "INT_XOR", "&": "INT_AND"}
_BINOPS = {"<<": "INT_LEFT", ">>": "INT_RIGHT"}
_CMPOPS = {"==": "INT_EQUAL", "!=": "INT_NOTEQUAL", "<": "INT_LESS", "<=": "INT_LESSEQUAL"}


def _chain_node(parts, sz):
    """One parenthesised operator chain; operators must be homogeneous."""
    operands, ops = parts[0::2], parts[1::2]
    if set(ops) <= _ADDSUB:
        if ops == ["-"] and operands[1][0] != "const":
            return ("op", "INT_SUB", (operands[0], operands[1]), sz)
        out = [operands[0]]
        for op, o in zip(ops, operands[1:]):
            if op == "-":
                if o[0] != "const":
                    return ("op", "INT_SUB", (operands[0], operands[1]), sz)
                o = ("const", (-o[1]) & E.mask(sz), sz)
            out.append(o)
        return ("op", "INT_ADD", tuple(out), sz)
    if len(ops) == 1 and ops[0] in _CMPOPS:
        return ("op", _CMPOPS[ops[0]], (operands[0], operands[1]), sz)
    if len(ops) == 1 and ops[0] in _BINOPS:
        return ("op", _BINOPS[ops[0]], (operands[0], operands[1]), sz)
    mns = {_CHAINOPS.get(op) for op in ops}
    if len(mns) == 1 and None not in mns:
        return ("op", mns.pop(), tuple(operands), sz)
    raise ValueError("mixed operators in %r" % ops)


def _hexval(tok):
    return int(str(tok)[1:], 16)


def _const(tok):
    d = str(tok)[1:]
    return ("const", int(d, 16), max(1, len(d) // 2))


def _flat(seq):
    out = []
    for x in seq:
        out.extend(x)
    return out


class Document:
    """A parsed document: header, sections, and the dialect's procedure trees."""

    def __init__(self):
        self.dialect = None
        self.version = None
        self.play = None
        self.init = None
        self.subtune = 0
        self.prologue = []
        self.dispatch_sets = {}
        self.mem0 = bytearray(0x10000)
        self.data_decls = []
        self.symbols = {}
        self.state = []
        self.inputs = []
        self.labels = set()
        self.procs = []  # sidprog: [(entry, seq Region)]
        self.subs = []  # frameprog: [(entry, params, rets, statements)]


class _Reader(lark.Transformer):  # pylint: disable=too-many-public-methods
    """Grammar callbacks for both dialects; ``reset`` starts a document."""

    def __init__(self):
        super().__init__()
        self.doc = Document()
        self.want = None
        self.rev = {}
        self.subrets = []

    def reset(self, want):
        self.doc = Document()
        self.want = want
        self.rev = {}
        self.subrets = []
        return self.doc

    def _frame(self):
        return self.doc.dialect == "frameprog"

    # -- header ----------------------------------------------------------------
    def sphead(self, c):
        self._version("sidprog", SIDPROG_VERSION, c[0])

    def fphead(self, c):
        self._version("frameprog", FRAMEPROG_VERSION, c[0])

    def _version(self, dialect, current, tok):
        if self.want is not None and dialect != self.want:
            raise ValueError("not a %s document" % self.want)
        major = int(tok)
        if major != current:
            raise SidprogVersionError(
                "%s major %d: this reader speaks major %d" % (dialect, major, current)
            )
        self.doc.dialect = dialect
        self.doc.version = major

    def play(self, c):
        self.doc.play = _hexval(c[0])

    def init(self, c):
        self.doc.init = _hexval(c[0])

    def subtune(self, c):
        self.doc.subtune = int(c[0])

    def sidwrite(self, c):
        return (_hexval(c[0]), _hexval(c[1]))

    def sidinit(self, c):
        self.doc.prologue.extend(c)

    def dispatch_set(self, c):
        self.doc.dispatch_sets[_hexval(c[0])] = {_hexval(t) for t in c[1:]}

    def inputs_sec(self, c):
        self.doc.inputs = [str(t) for t in c]

    # -- image / data / symbols / state ----------------------------------------
    def imgrow(self, c):
        a, run = _hexval(c[0]), bytes.fromhex(str(c[1]))
        self.doc.mem0[a : a + len(run)] = run

    def image_sec(self, c):
        return None

    def data_sec(self, c):
        return None

    def symbols_sec(self, c):
        return None

    def state_sec(self, c):
        return None

    def k_table(self, c):
        return "table"

    def k_stream(self, c):
        return "stream"

    def at_stride(self, c):
        return ("stride", int(c[0]))

    def at_cobase(self, c):
        return ("cobases", str(c[0]))

    def at_lo(self, c):
        return ("role", ("lo", str(c[0])))

    def at_hi(self, c):
        return ("role", ("hi", str(c[0])))

    def at_via(self, c):
        return ("via", str(c[0]))

    def at_targets(self, c):
        return ("targets", (_hexval(c[0]), _hexval(c[1])))

    def at_cmp(self, c):
        return ("cmp", [_hexval(t) for t in c])

    def at_dispatch(self, c):
        return ("dispatch", [_hexval(t) for t in c])

    def at_observed(self, c):
        return ("observed", True)

    def datarow(self, c):
        return bytes.fromhex(str(c[0]))

    def decl(self, c):
        d = {
            "kind": c[0],
            "base": str(c[1]),
            "size": int(c[2]),
            "stride": 1,
            "cobases": [],
            "role": None,
            "via": None,
            "targets": None,
            "cmp": [],
            "dispatch": [],
            "observed": False,
            "data": b"".join(x for x in c[3:] if isinstance(x, bytes)),
        }
        for key, val in (x for x in c[3:] if isinstance(x, tuple)):
            if key == "cobases":
                d["cobases"].append(val)
            else:
                d[key] = val
        self.doc.data_decls.append(d)

    def aliasdef(self, c):
        alias, cell = str(c[0]), str(c[1])
        addr = req_name(cell)
        if addr in self.doc.symbols:
            raise ValueError("duplicate alias for %s" % cell)
        self.doc.symbols[addr] = check_alias(alias)
        self.rev[alias] = cell

    def array(self, c):
        return True

    def statobs(self, c):
        return [_hexval(t) for t in c]

    def statedef(self, c):
        if str(c[1]) != "u8":
            raise ValueError("unknown state type %r" % str(c[1]))
        self.doc.state.append((str(c[0]), c[2] is not None, c[3] or []))

    # -- expressions -----------------------------------------------------------
    def e_hex(self, c):
        return _const(c[0])

    def wsuf(self, c):
        return int(c[0])

    def e_name(self, c):
        return self._nameref(str(c[0]), c[1] or 1)

    def e_index(self, c):
        return ("mem", self._index_addr(str(c[0]), str(c[1])), 1)

    def e_mem(self, c):
        return ("mem", c[0], 1)

    def z1(self, c):
        return 1

    def z2(self, c):
        return 2

    def e_zext(self, c):
        return ("op", "INT_ZEXT", (c[1],), c[0])

    def e_carry(self, c):
        return ("op", "INT_CARRY", (c[0], c[1]), 1)

    def e_group(self, c):
        return _chain_node(c[0], c[1] or 1)

    def chain(self, c):
        return c

    def o_add(self, c):
        return "+"

    def o_sub(self, c):
        return "-"

    def o_shl(self, c):
        return "<<"

    def o_shr(self, c):
        return ">>"

    def o_eq(self, c):
        return "=="

    def o_ne(self, c):
        return "!="

    def o_le(self, c):
        return "<="

    def o_lt(self, c):
        return "<"

    def o_or(self, c):
        return "|"

    def o_xor(self, c):
        return "^"

    def o_and(self, c):
        return "&"

    def _nameref(self, name, sz):
        name = self.rev.get(name, name)
        if self._frame():
            addr = name_addr(name)
            return ("mem", ("const", addr, 2), 1) if addr is not None else ("loc", name)
        m = _T_NAME.match(name)
        if m:
            return ("uni", BIND_BASE + int(m.group(1)), 1)
        m = _U_NAME.match(name)
        if m:
            return ("uni", int(m.group(1)), sz)
        addr = name_addr(name)
        if addr is not None:
            return ("mem", ("const", addr, 2), 1)
        return ("reg", reg_index(name))

    def _index_addr(self, base, idx):
        addr = req_name(self.rev.get(base, base))
        node = ("loc", idx) if self._frame() else ("reg", reg_index(idx))
        return ("op", "INT_ADD", (("op", "INT_ZEXT", (node,), 2), ("const", addr, 2)), 2)

    # -- statements ------------------------------------------------------------
    def lv_name(self, c):
        return ("name", str(c[0]))

    def lv_index(self, c):
        return ("index", str(c[0]), str(c[1]))

    def lv_mem(self, c):
        return ("mem", c[0])

    def asg(self, c):
        return (c[0], c[1])

    def pen(self, c):
        return ("pen", "iy" if str(c[0]) == "@xi" else "ax", c[1], c[2])

    def s_cyc(self, c):
        return ("stmt", int(str(c[0])[1:]), None)

    def s_pen(self, c):
        return ("stmt", 0 if c[0] is None else int(str(c[0])[1:]), c[1])

    def s_asg(self, c):
        return ("stmt", 0 if c[0] is None else int(str(c[0])[1:]), ("asg",) + c[1])

    def f_asg(self, c):
        return ("stmt", 0, ("asg",) + c[0])

    def pcall(self, c):
        m = _SUB_NAME.match(str(c[0]))
        if not m:
            raise ValueError("not a procedure name: %r" % str(c[0]))
        return (int(m.group(1), 16), list(c[1:]))

    def f_pcall_void(self, c):
        return ("pcall", [], c[0])

    def f_pcall_ret(self, c):
        if c[0][0] != "name":
            raise ValueError("procedure results must be plain locals")
        return ("pcall", [c[0][1]] + [str(t) for t in c[1:-1]], c[-1])

    # -- terminators and flow --------------------------------------------------
    def w_if(self, c):
        return 1

    def w_ifnot(self, c):
        return 0

    def dynbr(self, c):
        return ("term", ("br", c[0], None, _hexval(c[3]), c[1], c[2]))

    def cgoto(self, c):
        return ("term", ("jmpd", c[0]))

    def igoto_static(self, c):
        return ("term", ("jmpind", _hexval(c[0]), None))

    def igoto_dyn(self, c):
        return ("term", ("jmpind", None, c[0]))

    def tgt_static(self, c):
        return (_hexval(c[0]), None)

    def tgt_dyn(self, c):
        return (None, c[0])

    def call_flat(self, c):
        return ("term", ("jsr", c[0][0], _hexval(c[1]), c[0][1]))

    def call_deep(self, c):
        return ("callb", ("jsr", c[0][0], _hexval(c[1]), c[0][1]), _flat(c[2:]))

    def retline(self, c):
        return ("term", ("rts",))

    def fretline(self, c):
        return ("fret", [str(t) for t in c])

    def fl_goto(self, c):
        return ("flow", "goto", _hexval(c[0]))

    def fl_unobs(self, c):
        return ("flow", "unobs", _hexval(c[0]))

    def fl_cont(self, c):
        return ("flow", "cont", None)

    def fl_brk(self, c):
        return ("flow", "brk", None)

    def els_none(self, c):
        return ("else", None)

    def els_body(self, c):
        return ("else", _flat(c))

    def els_unobs(self, c):
        return ("elsunobs", _hexval(c[0]))

    def sif_body(self, c):
        return ("if", int(str(c[1])[2:]), c[0], c[2], _flat(c[3:-1]), c[-1])

    def sif_front(self, c):
        return ("iffront", int(str(c[1])[2:]), c[0], c[2], _hexval(c[3]))

    def fif_body(self, c):
        return ("if", None, c[0], c[1], _flat(c[2:-1]), c[-1])

    def fif_front(self, c):
        return ("iffront", None, c[0], c[1], _hexval(c[2]))

    # -- regions ---------------------------------------------------------------
    def label(self, c):
        pc = _hexval(c[0])
        self.doc.labels.add(pc)
        return ("label", pc)

    def loop(self, c):
        body = _flat(c)
        return [("loop", body)] if self._frame() else [Region("loop", Region("seq", body))]

    def case(self, c):
        return (_hexval(c[0]), _flat(c[1:]))

    def _arm(self, body):
        return body if self._frame() else Region("seq", body)

    def swgoto(self, c):
        cases = [("$%04X" % pc, self._arm(body)) for pc, body in c]
        return [("swg", cases)] if self._frame() else [Region("switch", ("goto", cases), [])]

    def _swcall(self, bare, bodied):
        if self._frame():
            return [("swc", ["$%04X" % t for t in bare], [("$%04X" % pc, b) for pc, b in bodied])]
        cases = [("$%04X" % t, Region("call", t)) for t in bare]
        cases += [("$%04X" % pc, Region("call", pc, Region("seq", b))) for pc, b in bodied]
        return [Region("switch", ("call", cases), [])]

    def swcall_flat(self, c):
        return self._swcall([_hexval(t) for t in c], [])

    def pclist(self, c):
        return [_hexval(t) for t in c]

    def swcall_deep(self, c):
        return self._swcall(c[0] or [], c[1:])

    def opsw_code(self, c):
        pc = _hexval(c[1])
        cases = [("$%02X" % op, Region("seq", body)) for op, body in c[2:]]
        return [Region("switch", ("code[$%04X]" % pc, cases), [pc])]

    def opsw_cell(self, c):
        addr = req_name(self.rev.get(str(c[1]), str(c[1])))
        out = [("label", c[0][1])] if c[0] is not None else []
        return out + [("opsw", addr, [("$%02X" % op, body) for op, body in c[2:]])]

    def forloop(self, c):
        return [("for", str(c[0]), _hexval(c[1]), _hexval(c[2]), _flat(c[3:]))]

    # -- blocks ----------------------------------------------------------------
    def sblock(self, c):
        """Payload lines plus the closers that end them: the block region and
        whatever sibling regions its closers introduce (the tree is the flow)."""
        labelled = bool(c) and c[0][0] == "label"
        acc = _Acc(c[0][1]) if labelled else None
        out = []
        for x in c[1:] if labelled else c:
            tag = x[0]
            if tag == "flow":  # a flow item ends the block without joining it
                if acc is not None:
                    out.append(Region("block", acc_block(acc), acc.label))
                    acc = None
                out.append(_FLOW_REGION[x[1]](x[2]))
                continue
            if acc is None:
                acc = _Acc(None)
            if tag == "stmt":
                self._sstmt(acc, x[1], x[2])
                continue
            acc.term = x[1] if tag in ("term", "callb") else ("br", x[2], None, None, x[3], None)
            out.append(Region("block", acc_block(acc), acc.label))
            acc = None
            if tag == "callb":
                out.append(Region("call", x[1][1], Region("seq", x[2])))
            elif tag == "if":
                out.append(Region("if", x[1], Region("seq", x[4]), self._else(x[5])))
            elif tag == "iffront":
                arm = Region("seq", [Region("frontier", x[4])])
                out.append(Region("if", x[1], arm, None))
        if acc is not None:
            out.append(Region("block", acc_block(acc), acc.label))
        return out

    @staticmethod
    def _else(spec):
        if spec[0] == "elsunobs":
            return Region("seq", [Region("frontier", spec[1])])
        return None if spec[1] is None else Region("seq", spec[1])

    def _sstmt(self, acc, cyc, payload):
        if cyc:
            acc.events.append(("cyc", cyc))
        if payload is None:
            return
        if payload[0] == "pen":
            acc.events.append(payload)
            return
        lv, rhs = payload[1], payload[2]
        if lv[0] == "index":
            acc.events.append(("st", self._index_addr(lv[1], lv[2]), rhs))
            return
        if lv[0] == "mem":
            acc.events.append(("st", lv[1], rhs))
            return
        name = self.rev.get(lv[1], lv[1])
        m = _T_NAME.match(name)
        if m:
            acc.bind[int(m.group(1))] = expand(rhs, acc.bind, {})
            return
        m = _U_NAME.match(name)
        if m:
            if rhs[0] != "mem":
                raise ValueError("load line without a memory source: %r" % name)
            acc.events.append(("ld", int(m.group(1)), rhs[1]))
            return
        if name in _NAME_REGS or _R_NAME.match(name):
            acc.regs[reg_index(name)] = rhs
            return
        acc.events.append(("st", ("const", req_name(name), 2), rhs))

    def fblock(self, c):
        out = []
        for x in c:
            tag = x[0]
            if tag == "label":
                out.append(x)
            elif tag == "stmt":
                out.append(self._fasg(x[2]))
            elif tag == "pcall":
                out.append(("pcall", x[2][0], x[2][1], x[1]))
            elif tag == "term":
                out.append(_fterm(x[1]))
            elif tag == "callb":
                out.append(("callb", x[1][1], x[1][2], x[2]))
            elif tag == "fret":
                out.append(("ret", not x[1] and bool(self.subrets)))
            elif tag == "if":
                els = [("unobs", x[5][1])] if x[5][0] == "elsunobs" else (x[5][1] or [])
                out.append(("if", _WORD[x[2]], x[3], x[4], els))
            elif tag == "iffront":
                out.append(("if", _WORD[x[2]], x[3], [("unobs", x[4])], []))
            else:
                out.append((x[1],) if x[2] is None else (x[1], x[2]))
        return out

    def _fasg(self, payload):
        lv, rhs = payload[1], payload[2]
        if lv[0] == "index":
            return ("st", self._index_addr(lv[1], lv[2]), rhs)
        if lv[0] == "mem":
            return ("st", lv[1], rhs)
        name = self.rev.get(lv[1], lv[1])
        addr = name_addr(name)
        return ("st", ("const", addr, 2), rhs) if addr is not None else ("asg", name, rhs)

    # -- procedures and document -----------------------------------------------
    def proc(self, c):
        self.doc.procs.append((_hexval(c[0]), Region("seq", _flat(c[1:]))))

    def params(self, c):
        self.subrets = []
        return [str(t) for t in c]

    def rets(self, c):
        self.subrets = [str(t) for t in c]
        return self.subrets

    def sub(self, c):
        m = _SUB_NAME.match(str(c[0]))
        if not m:
            raise ValueError("not a procedure name: %r" % str(c[0]))
        self.doc.subs.append((int(m.group(1), 16), c[1], c[2] or [], _flat(c[3:])))

    def _finish(self):
        doc = self.doc
        for d in doc.data_decls:
            d["base"] = req_name(self.rev.get(d["base"], d["base"]))
            d["cobases"] = [req_name(self.rev.get(n, n)) for n in d["cobases"]]
            if d["role"] is not None:
                d["role"] = (d["role"][0], req_name(self.rev.get(d["role"][1], d["role"][1])))
            if d["via"] is not None:
                d["via"] = req_name(self.rev.get(d["via"], d["via"]))
            if len(d["data"]) != d["size"]:
                raise ValueError(
                    "data region %s[%d] carries %d bytes"
                    % (addr_name(d["base"]), d["size"], len(d["data"]))
                )
            doc.mem0[d["base"] : d["base"] + d["size"]] = d["data"]
        return doc

    def sidprog_doc(self, c):
        return self._finish()

    def frameprog_doc(self, c):
        return self._finish()

    def start(self, c):
        return c[0]


def _fterm(term):
    """A block terminator as a frameprog statement (no cycle penalties)."""
    k = term[0]
    if k == "br":
        return ("dbr", _WORD[term[1]], term[4], term[5], term[3])
    if k == "jmpd":
        return ("dgoto", term[1])
    if k == "jmpind":
        return ("igoto", term[1], term[2])
    if term[3] is not None:
        return ("dcall", term[3], term[2])
    return ("call", term[1], term[2])


_WORD = {1: "if", 0: "ifnot"}
_FLOW_REGION = {
    "goto": lambda pc: Region("goto", pc),
    "unobs": lambda pc: Region("frontier", pc),
    "cont": lambda _pc: Region("cont"),
    "brk": lambda _pc: Region("brk"),
}

_READER = _Reader()
_LARK = None


def _parser():
    global _LARK  # pylint: disable=global-statement
    if _LARK is None:
        _LARK = lark.Lark(
            GRAMMAR,
            parser="lalr",
            start=["start", "expr"],
            transformer=_READER,
            propagate_positions=False,
        )
    return _LARK


def _run(text, start, want, preset=False):
    doc = _READER.reset(want)
    if preset:
        doc.dialect = want
    try:
        return _parser().parse(text, start=start)
    except lark.exceptions.VisitError as e:
        raise e.orig_exc from None
    except lark.exceptions.LarkError as e:
        raise ValueError(str(e)) from None


def parse_document(text, want=None):
    """Parse a document of the given dialect (any when ``want`` is None)."""
    return _run(text if text.endswith("\n") else text + "\n", "start", want)


def parse_expression(text, want="sidprog"):
    """Parse one expression in the given dialect; must consume ``text`` entirely."""
    return _run(text, "expr", want, preset=True)


def doc_block():
    """The grammar as a fenced markdown block; the docs embed this verbatim."""
    return "```lark\n%s```" % GRAMMAR
