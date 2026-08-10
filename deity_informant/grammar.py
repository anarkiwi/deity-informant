"""The ONE grammar of the frameprog language.

``sidprog.lark`` is normative and LALR(1); this module owns the name/address
bijection it resolves against and the reader that turns a parse into the
statement trees frameprog reads back.
"""

from __future__ import annotations

import re
from pathlib import Path

import lark

from . import expr as E
from .render import sid_name

FRAMEPROG_VERSION = 1  # 1: image + dispatch + evidence sections (the total artifact)

GRAMMAR_PATH = Path(__file__).with_name("sidprog.lark")
GRAMMAR = GRAMMAR_PATH.read_text(encoding="utf-8")


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
VIEW = "sid.reg"  # the byte view of the register file (docs/frameprog.md 7.7 (5))
_CELL_NAME = re.compile(r"(zp|m)_([0-9A-F]+)$")
_SLOT_NAME = re.compile(r"[utr]\d+$")
_SUB_NAME = re.compile(r"sub_([0-9A-F]{4})$")


def _z2(n):
    return ("op", "INT_ZEXT", (n,), 2)


def addr_name(v):
    """Canonical cell name for a 2-byte const address (total on 0..$FFFF)."""
    return _SID_NAMES.get(v) or ("zp_%02X" % v if v < 0x100 else "m_%04X" % v)


def sid_base(base):
    """The lo register of the SID lo/hi pair ``base`` belongs to, else None."""
    reg = base - 0xD400
    if not 0 <= reg <= 0x18:
        return None
    if reg > 0x14:
        return 0xD415 if reg in (0x15, 0x16) else None
    r = reg % 7
    return base - r if r <= 1 else base - (r - 2) if r <= 3 else None


def name_addr(name):
    """Address named by a canonical cell name, else None (inverse of addr_name)."""
    if name == VIEW:
        return 0xD400
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


def store_width(val):
    """Byte width of a store whose value expression is ``val`` (2 once fused).

    ``op``/``mem`` and a word local state their width (``frameproc.loc_width``
    is the same rule, unreachable here); every other value is one byte."""
    if val[0] == "op":
        return val[3]
    if val[0] == "mem":
        return val[2]
    return val[2] if val[0] == "loc" and len(val) > 2 else 1


def _check_store(lv, rhs):
    """A store's lvalue width must be the width of the value stored."""
    if lv[-1] != store_width(rhs):
        raise ValueError("lvalue width %d does not match the stored value" % lv[-1])
    return True


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
        self.resolved = {}  # rung (f): deref address -> (pointer cell, index or None)
        self.extents = {}  # 2b: pointer cell -> the declared block bases its derefs land in
        self.roles = {}  # stage 2: state field name -> the role its updates name
        self.labels = set()
        self.subs = []  # frameprog: [(entry, params, rets, statements)]
        self.evidence = new_evidence()  # frameprog: the block-model rebuild channels


def new_evidence():
    """An empty ``evidence { }`` record (frameprog 1)."""
    return {
        "code": set(),  # executed pcs
        "leaders": set(),  # block leaders
        "written": set(),  # play-written cells, evidence half (page one is a rule)
        "targets": {},  # transfer site -> observed successors
        "reads": {},  # read site -> addresses read there (datadecl.declarations)
        "closure": None,  # (recur, first, window, cap); -1 spells None
        "copies": {},  # init-staged cell -> (origin, proving store pc)
        "staged": {},  # store pc -> (undeclared origins, refused stores)
        "census": {},  # initcopy.reduce's census counters
    }


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

    # -- header ----------------------------------------------------------------
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

    def at_mut(self, c):
        return ("mut", [int(t) for t in c])

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
            "mut": [],
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

    # -- evidence (frameprog 1: the block-model rebuild channels) --------------
    def span(self, c):
        a = _hexval(c[0])
        return range(a, (a if c[1] is None else _hexval(c[1])) + 1)

    def evidence_sec(self, c):
        return None

    def _ev(self, key, spans):
        self.doc.evidence[key].update(a for r in spans for a in r)

    def ev_code(self, c):
        self._ev("code", c)

    def ev_leaders(self, c):
        self._ev("leaders", c)

    def ev_written(self, c):
        self._ev("written", c)

    def ev_targets(self, c):
        self.doc.evidence["targets"][_hexval(c[0])] = {_hexval(t) for t in c[1:]}

    def ev_reads(self, c):
        self.doc.evidence["reads"].setdefault(_hexval(c[0]), set()).update(
            a for r in c[1:] for a in r
        )

    def ev_closure(self, c):
        self.doc.evidence["closure"] = tuple(int(t) for t in c)

    def ev_copy(self, c):
        self.doc.evidence["copies"][_hexval(c[0])] = (_hexval(c[1]), _hexval(c[2]))

    def ev_staged(self, c):
        self.doc.evidence["staged"][_hexval(c[0])] = (int(c[1]), int(c[2]))

    def ev_census(self, c):
        self.doc.evidence["census"][str(c[0])] = int(c[1])

    def aliasdef(self, c):
        alias, cell = str(c[0]), str(c[1])
        addr = req_name(cell)
        if addr in self.doc.symbols:
            raise ValueError("duplicate alias for %s" % cell)
        self.doc.symbols[addr] = check_alias(alias)
        self.rev[alias] = cell

    def array(self, c):
        return True

    def statext(self, c):
        return [str(t) for t in c]

    def statobs(self, c):
        return [_hexval(t) for t in c]

    def srole(self, c):
        return str(c[0])

    def statedef(self, c):
        """A block extent is a pointer's, so it is a scalar u16 field's alone."""
        name, kind = str(c[0]), str(c[2])
        if kind not in ("u8", "u16"):
            raise ValueError("unknown state type %r" % kind)
        if c[4] and (kind != "u16" or c[3] is not None):
            raise ValueError("state field %s: a block extent is a u16 field's" % name)
        self.doc.state.append((name, int(kind[1:]) // 8, c[3] is not None, c[5] or []))
        if c[1]:
            self.doc.roles[name] = c[1]
        if c[4]:
            self.doc.extents[name] = c[4]

    # -- expressions -----------------------------------------------------------
    def e_hex(self, c):
        return _const(c[0])

    def wsuf(self, c):
        return int(c[0])

    def e_name(self, c):
        return self._nameref(str(c[0]), c[1] or 1)

    def e_index(self, c):
        if (c[2] or 1) == 2:
            got = self._pair_addrs(str(c[0]), c[1])
            if got is not None:
                la, ha = got
                shl = ("op", "INT_LEFT", (_z2(("mem", ha, 1)), ("const", 8, 1)), 2)
                return ("op", "INT_OR", (shl, _z2(("mem", la, 1))), 2)
        return ("mem", self._index_addr(str(c[0]), c[1]), c[2] or 1)

    def e_deref(self, c):
        return ("mem", self._deref_addr(str(c[0]), c[1]), c[2] or 1)

    def e_deref_bare(self, c):
        return ("mem", self._deref_addr(str(c[0]), None), c[1] or 1)

    def e_mem(self, c):
        return ("mem", c[0], c[1] or 1)

    def z1(self, c):
        return 1

    def z2(self, c):
        return 2

    def e_zext(self, c):
        return ("op", "INT_ZEXT", (c[1],), c[0])

    def t1(self, c):
        return 1

    def t2(self, c):
        return 2

    def e_trunc(self, c):
        return ("op", "COPY", (c[1],), c[0])

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
        addr = name_addr(name)
        if addr is not None:
            return ("mem", ("const", addr, 2), sz)
        return ("loc", name) if sz == 1 else ("loc", name, sz)

    def _cell(self, name):
        """The cell a section names, an alias resolving to the cell it stands for."""
        return req_name(self.rev.get(name, name))

    def _index_addr(self, base, idx):
        """``base + zext2(idx)``: the declared-base indexed access, any index expression.

        A byte index widens; one already a word -- the ``sid.reg`` view carries
        its offset inside the index at word width -- rides as written."""
        addr = self._cell(base)
        if not (idx[0] == "op" and idx[3] == 2) and not (
            idx[0] in ("mem", "const") and idx[2] == 2
        ):
            idx = ("op", "INT_ZEXT", (idx,), 2)
        return ("op", "INT_ADD", (idx, ("const", addr, 2)), 2)

    def _pair_hi(self, base):
        """Hi partner address where ``base`` names a declared lo-role table."""
        name = self.rev.get(base, base)
        for d in self.doc.data_decls:
            if d["base"] == name and d["role"] and d["role"][0] == "lo":
                return self._cell(d["role"][1])
        return None

    def _pair_addrs(self, base, idx):
        """``(lo addr, hi addr)`` of a paired-table access, one widened index."""
        hi = self._pair_hi(base)
        if hi is None:
            return None
        la = self._index_addr(base, idx)
        return la, ("op", "INT_ADD", (la[2][0], ("const", hi, 2)), 2)

    def _deref_addr(self, base, idx):
        """``ptr [+ zext2(idx)]``: rung (f)'s resolved deref, the pointer read as a word."""
        cell = self._cell(base)
        word = ("mem", ("const", cell, 2), 2)
        addr = word if idx is None else ("op", "INT_ADD", (word, ("op", "INT_ZEXT", (idx,), 2)), 2)
        self.doc.resolved[addr] = (cell, idx)
        return addr

    # -- statements ------------------------------------------------------------
    def lv_name(self, c):
        return ("name", str(c[0]), c[1] or 1)

    def lv_index(self, c):
        return ("index", str(c[0]), c[1], c[2] or 1)

    def lv_deref(self, c):
        return ("deref", str(c[0]), c[1], c[2] or 1)

    def lv_deref_bare(self, c):
        return ("deref", str(c[0]), None, c[1] or 1)

    def lv_mem(self, c):
        return ("mem", c[0], c[1] or 1)

    def asg(self, c):
        return (c[0], c[1])

    def f_asg(self, c):
        return ("stmt", 0, ("asg",) + c[0])

    def f_asg_hifirst(self, c):
        return ("stmt", 0, ("asg",) + c[0] + (True,))

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
        return [("loop", _flat(c))]

    def case(self, c):
        return (_hexval(c[0]), _flat(c[1:]))

    def swgoto(self, c):
        return [("swg", [("$%04X" % pc, body) for pc, body in c])]

    def _swcall(self, bare, bodied):
        return [("swc", ["$%04X" % t for t in bare], [("$%04X" % pc, b) for pc, b in bodied])]

    def swcall_flat(self, c):
        return self._swcall([_hexval(t) for t in c], [])

    def pclist(self, c):
        return [_hexval(t) for t in c]

    def swcall_deep(self, c):
        return self._swcall(c[0] or [], c[1:])

    def opsw_cell(self, c):
        addr = self._cell(str(c[1]))
        out = [("label", c[0][1])] if c[0] is not None else []
        return out + [("opsw", addr, [("$%02X" % op, body) for op, body in c[2:]])]

    def forloop(self, c):
        return [("for", str(c[0]), _hexval(c[1]), _hexval(c[2]), _flat(c[3:]))]

    # -- blocks ----------------------------------------------------------------
    def fblock(self, c):
        out = []
        for x in c:
            tag = x[0]
            if tag == "label":
                out.append(x)
            elif tag == "stmt":
                got = self._fasg(x[2])
                (out.extend if isinstance(got, list) else out.append)(got)
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
        got = self._fasg_body(payload)
        if len(payload) < 4:
            return got
        if not isinstance(got, tuple) or got[0] != "st" or store_width(payload[2]) != 2:
            raise ValueError("hi-first states a word store's byte order")
        return got + (True,)

    def _fasg_body(self, payload):
        lv, rhs = payload[1], payload[2]
        if lv[0] == "index" and _check_store(lv, rhs):
            if lv[3] == 2:
                got = self._pair_addrs(lv[1], lv[2])
                if got is not None:
                    la, ha = got
                    hishift = ("op", "INT_RIGHT", (rhs, ("const", 8, 1)), 2)
                    return [
                        ("st", la, ("op", "COPY", (rhs,), 1)),
                        ("st", ha, ("op", "COPY", (hishift,), 1)),
                    ]
            return ("st", self._index_addr(lv[1], lv[2]), rhs)
        if lv[0] == "deref" and _check_store(lv, rhs):
            return ("st", self._deref_addr(lv[1], lv[2]), rhs)
        if lv[0] == "mem" and _check_store(lv, rhs):
            return ("st", lv[1], rhs)
        name = self.rev.get(lv[1], lv[1])
        addr = name_addr(name)
        if addr is None:
            return ("asg", name, rhs)
        _check_store(lv, rhs)
        return ("st", ("const", addr, 2), rhs)

    # -- procedures and document -----------------------------------------------
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
        doc.extents = {
            self._cell(n): tuple(sorted(self._cell(b) for b in bs)) for n, bs in doc.extents.items()
        }
        for d in doc.data_decls:
            d["base"] = self._cell(d["base"])
            d["cobases"] = [self._cell(n) for n in d["cobases"]]
            if d["role"] is not None:
                d["role"] = (d["role"][0], self._cell(d["role"][1]))
            if d["via"] is not None:
                d["via"] = self._cell(d["via"])
            if len(d["data"]) != d["size"]:
                raise ValueError(
                    "data region %s[%d] carries %d bytes"
                    % (addr_name(d["base"]), d["size"], len(d["data"]))
                )
            doc.mem0[d["base"] : d["base"] + d["size"]] = d["data"]
        return doc

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


def parse_expression(text, want="frameprog"):
    """Parse one expression in the given dialect; must consume ``text`` entirely."""
    return _run(text, "expr", want, preset=True)


def doc_block():
    """The grammar as a fenced markdown block; the docs embed this verbatim."""
    return "```lark\n%s```" % GRAMMAR
