"""C as pycparser ``c_ast`` nodes: builders, provenance carried on each node's ``coord``
(:class:`Prov`), structural keys, and the printer (wayward-eagle's generator) that reads the
provenance back as marker records in text order and, on request, ``#line`` directives."""

from __future__ import annotations

from dataclasses import dataclass

from pycparser import c_ast
from pycparser.c_parser import Coord
from wayward_eagle.c import Generator, format_c
from wayward_eagle.c.printer import STYLE

#: Bits of each integer type the printer spells.
BITS = {"uint8_t": 8, "int8_t": 8, "uint16_t": 16, "int16_t": 16}
#: The layout of ``.c`` text carrying ``#line`` directives: each line as the generator ends it.
LINES_STYLE = (
    "{BasedOnStyle: LLVM, MaxEmptyLinesToKeep: 0, RemoveParentheses: MultipleParentheses,"
    " ColumnLimit: 0}"
)
#: Records whose construct ends with its node, the enclosing record resuming after it.
WRAPPED = ("check", "arm")


@dataclass(slots=True)
class Prov(Coord):
    """A node's provenance: ``rec`` the marker record it prints (:attr:`marks.Marks.recs`),
    ``pure`` an assignment of a value with no effect, droppable when nothing reads it,
    ``edges`` a ``goto``'s machine-code edges, ``neg`` an ``if``'s condition negated,
    ``inner`` the record of the node ``rec`` was put around."""

    rec: int | None = None
    pure: bool = False
    edges: list | None = None
    neg: c_ast.Node | None = None
    inner: int | None = None


def prov(node):
    """``node``'s :class:`Prov`, made on first use."""
    if not isinstance(node.coord, Prov):
        node.coord = Prov("", 0)
    return node.coord


def rec_of(node):
    """The marker record ``node`` prints, or None."""
    return node.coord.rec if isinstance(node, c_ast.Node) and isinstance(node.coord, Prov) else None


def marked(node, rec):
    """A shallow copy of expression ``node`` printing marker record ``rec``."""
    out = type(node).__new__(type(node))
    for k in node.__slots__[:-1]:
        setattr(out, k, getattr(node, k))
    out.coord = Prov("", 0, rec=rec, inner=rec_of(node))
    return out


def spell(v, uns=False):
    """A literal's spelling: decimal below 10, else hex, ``u`` when ``uns``."""
    text = str(v) if v < 10 else f"0x{v:X}"
    return text + ("u" if uns else "")


def const(v, uns=False):
    """The integer literal ``v``."""
    return c_ast.Constant("int", spell(v, uns))


def hexes(vals, width=2):
    """``vals`` as the hex literals of an initializer list macro, ``width`` digits at least."""
    return ", ".join(f"0x{v:0{width}X}" for v in vals)


def ident(name):
    """The identifier ``name``."""
    return c_ast.ID(name)


def type_decl(ctype, declname=None):
    """The type tree of ``ctype`` (``"uint8_t"``, ``"uint8_t *"``, ``"const uint8_t *const"``)
    declaring ``declname``."""
    words = ctype.replace("*", " * ").split()
    star = words.index("*") if "*" in words else len(words)
    base = words[:star]
    quals = ["const"] if "const" in base else []
    t = c_ast.TypeDecl(
        declname, quals, None, c_ast.IdentifierType([w for w in base if w != "const"])
    )
    return c_ast.PtrDecl(words[star + 1 :], t) if star < len(words) else t


def typename(ctype):
    """The type name ``ctype`` as a cast writes it."""
    return c_ast.Typename(None, [], None, type_decl(ctype))


def cast(ctype, node):
    """``(ctype)node``."""
    return c_ast.Cast(typename(ctype), node)


def cast_type(node):
    """The integer type an integer cast ``node`` converts to, else None."""
    if not isinstance(node, c_ast.Cast) or not isinstance(node.to_type.type, c_ast.TypeDecl):
        return None
    names = node.to_type.type.type.names
    return names[0] if len(names) == 1 and names[0] in BITS else None


def call(name, *args):
    """``name(args...)``."""
    return c_ast.FuncCall(ident(name), c_ast.ExprList(list(args)) if args else None)


def binop(op, a, b):
    """``a op b``."""
    return c_ast.BinaryOp(op, a, b)


def index(base, sub):
    """``base[sub]``."""
    return c_ast.ArrayRef(ident(base) if isinstance(base, str) else base, sub)


def assign(lvalue, rvalue, pure=False):
    """``lvalue = rvalue;`` (``pure``: droppable when nothing reads ``lvalue``)."""
    st = c_ast.Assignment("=", ident(lvalue) if isinstance(lvalue, str) else lvalue, rvalue)
    if pure:
        prov(st).pure = True
    return st


def when(cond, items):
    """``if (cond) { items }``."""
    return c_ast.If(cond, c_ast.Compound(list(items)), None)


def void(node):
    """``(void)node;``."""
    return cast("void", node)


def ret(node=None):
    """``return node;``."""
    return c_ast.Return(node)


def fallthrough():
    """``__attribute__((fallthrough));``."""
    attr = c_ast.AttributeSpecifier([c_ast.Attribute("fallthrough", None, None)])
    return c_ast.AttributedStatement([attr], c_ast.EmptyStatement())


def decl(name, ctype, init=None, storage=(), attrs=(), dim=False):
    """The declaration of ``name`` of type ``ctype``; with ``dim`` an array of it (True:
    unsized, else its size expression)."""
    t = type_decl(ctype, name)
    if dim is not False:
        t = c_ast.ArrayDecl(t, None if dim is True else dim, [])
    spec = [c_ast.AttributeSpecifier([c_ast.Attribute(a, None, None) for a in attrs])]
    return c_ast.Decl(
        name, [], None, list(storage), [], t, init, None, attributes=spec if attrs else []
    )


def function(name, ret_type, params, storage=(), attrs=()):
    """The declaration of function ``name`` returning ``ret_type`` taking ``[(name, ctype)]``."""
    ps = [decl(n, t) for n, t in params] or [typename("void")]
    d = decl(name, ret_type, storage=storage, attrs=attrs)
    d.type = c_ast.FuncDecl(c_ast.ParamList(ps), d.type)
    return d


def define(head, items, frame=None):
    """The definition of function declaration ``head`` holding statements ``items``;
    ``frame``: the records its head and its closing brace print."""
    body = c_ast.Compound(list(items))
    fd = c_ast.FuncDef(head, None, body)
    if frame:
        prov(fd).rec, prov(body).rec = frame
    return fd


def inits(*names):
    """``{a, b}`` of names (macros of initializer lists)."""
    return c_ast.InitList([ident(n) for n in names])


def key(node, names=None):
    """A structural key of ``node``: equal for equal trees whatever their provenance, each
    name renamed by ``names``."""
    if isinstance(node, list):
        return tuple(key(x, names) for x in node)
    if not isinstance(node, c_ast.Node):
        return node
    attrs = []
    for k in node.__slots__[:-2]:
        v = getattr(node, k)
        if k in ("name", "declname") and isinstance(v, str) and names:
            v = names.get(v, v)
        attrs.append(key(v, names))
    return (type(node).__name__, *attrs)


def walk(node):
    """Every node under ``node``, ``node`` first."""
    yield node
    for _, child in node.children():
        yield from walk(child)


def rename(nodes, names):
    """Rename each identifier and declarator of ``nodes`` named in ``names``, in place."""
    seen = set()
    for n in (x for top in nodes for x in walk(top)):
        if id(n) in seen:
            continue
        seen.add(id(n))
        if isinstance(n, (c_ast.ID, c_ast.Decl)) and n.name in names:
            n.name = names[n.name]
        elif isinstance(n, c_ast.TypeDecl) and n.declname in names:
            n.declname = names[n.declname]


def _lead(st):
    """The expression a statement's line starts with: an ``if``'s, ``while``'s or
    ``switch``'s condition, an assignment's value, an initializer, a returned value."""
    if isinstance(st, (c_ast.If, c_ast.While, c_ast.Switch)):
        return st.cond
    if isinstance(st, c_ast.Assignment):
        return st.rvalue
    if isinstance(st, c_ast.Decl):
        return st.init
    if isinstance(st, c_ast.Return):
        return st.expr
    return None


class Printer(Generator):
    """wayward-eagle's generator reading provenance back: :attr:`order` the marker records in
    text order; with ``line`` (a record's ``(number, file)``) a ``#line`` directive before
    each line naming the record in force, and around each wrapped one (:data:`WRAPPED`)."""

    def __init__(self, recs, line=None):
        super().__init__()
        self.recs, self.line, self.order, self.active, self.open = recs, line, [], None, []

    def directive(self, rec):
        """The ``#line`` directive of record ``rec`` on its own line, or nothing."""
        if self.line is None or rec is None:
            return ""
        n, f = self.line(rec)
        return f'\n#line {n} "{f}"\n'

    def enter(self, rec, inline=True):
        """Start record ``rec`` where it is new, the directive it needs mid-line (``inline``)."""
        if rec is None or rec == self.active or rec in self.open:
            return ""
        self.order.append(rec)
        self.active = rec
        return self.directive(rec) if inline else ""

    def _sticky(self, node):
        rec = rec_of(node)
        return rec if rec is not None and self.recs[rec][0] not in WRAPPED else None

    def visit(self, node):
        own = rec_of(node) if not isinstance(node, c_ast.Compound) else None
        recs = [own, node.coord.inner] if own is not None else []
        return self._within(node, [r for r in recs if r is not None])

    def _within(self, node, recs):
        """``node`` printed inside records ``recs``, outermost first."""
        if not recs:
            return super().visit(node)
        rec, rest = recs[0], recs[1:]
        if rec == self.active or rec in self.open:
            return self._within(node, rest)
        if self.recs[rec][0] not in WRAPPED:
            return self.enter(rec) + self._within(node, rest)
        saved = self.active
        text = self.enter(rec)
        self.open.append(rec)
        text += self._within(node, rest)
        self.open.pop()
        self.active = saved
        return text + self.directive(saved)

    def _make_indent(self):
        return self.directive(self.active).lstrip("\n") + super()._make_indent()

    def _generate_stmt(self, n, add_indent=False):
        lead = _lead(n)
        rec = self._sticky(n) if rec_of(n) is not None else self._sticky(lead)
        self.enter(rec, inline=False)
        return super()._generate_stmt(n, add_indent)

    def visit_Compound(self, n):  # pylint: disable=invalid-name
        """``{ ... }``, the closing brace printing the block's own record."""
        s = self._make_indent() + "{\n"
        self.indent_level += 2
        s += "".join(self._generate_stmt(st) for st in n.block_items or ())
        self.indent_level -= 2
        self.enter(self._sticky(n), inline=False)
        return s + self._make_indent() + "}\n"

    def visit_DoWhile(self, n):  # pylint: disable=invalid-name
        """``do { ... } while (c);``, its last line printing the condition's record."""
        s = "do\n" + self._generate_stmt(n.stmt, add_indent=True)
        self.enter(self._sticky(n.cond), inline=False)
        return (
            s + self._make_indent() + "while (" + self._visit_expr(n.cond, allow_comma=True) + ");"
        )


def render(parts, recs, line=None):
    """``(text, order)``: the formatted C of ``parts`` (``c_ast`` declarations and
    definitions, or ``(record, text)`` verbatim C printing ``record``) and the marker records
    in text order; ``line``: with ``#line`` directives."""
    gen, out = Printer(recs, line), []
    for p in parts:
        if isinstance(p, tuple):
            gen.active = None
            out.append(gen.enter(p[0]).lstrip("\n") + p[1])
        else:
            out.append(gen.visit(p).lstrip("\n") + ("" if isinstance(p, c_ast.FuncDef) else ";"))
    return format_c("\n".join(out) + "\n", STYLE if line is None else LINES_STYLE), gen.order
