"""The denotation solve: what each value denotes, to a fixpoint over the webs.

docs/denotation-solve.md §3, as one monotone analysis whose unknown is a def-use
web or a persistent cell rather than a machine location. Nothing here is emitted;
the lattice, the join and every transfer rule are documented in docs/denotation.md.
"""

from __future__ import annotations

from . import datadecl
from . import frameproc
from . import grammar as G

CHANNELS = 3  # the SID's voice count: the domain a ``lane`` denotation is affine over

BOT = ("bot",)
TOP = ("top",)

T_ENTRY = "entry"  # the web is live where the procedure begins
T_OPAQUE = "opaque"  # an opaque call or an unenumerable transfer defines it
T_CALL = "call"  # a call return: the callee's own denotation is not solved
T_MIXED = "mixed"  # the facts join across constructors the lattice does not cross
T_OP = "op"  # an operator with no transfer rule
T_ADDR = "addr"  # a memory site whose address names no declared base
T_CELL = "cell"  # it reads a cell or table whose own denotation is ⊤
T_NOFACT = "nofact"  # no site states anything about it
T_ROUNDS = "rounds"  # the fact set had not settled at the round cap
CAUSES = (T_ENTRY, T_OPAQUE, T_CALL, T_MIXED, T_OP, T_ADDR, T_CELL, T_NOFACT, T_ROUNDS)

KINDS = ("const", "lane", "idx", "row", "addr", "byte")
RULES = (  # the transfer rules of docs/denotation.md §4, tallied so the census reads per rule
    "lane-table",
    "table-row",
    "pair-row",
    "deref-row",
    "cell-read",
    "staged-init",
    "field-select",
    "addr-row",
    "cursor-step",
    "affine-const",
    "index-use",
)

_ROUNDS = 24  # the fixpoint cap every other sweep in this package runs under
_BITS = ("INT_AND", "INT_OR", "INT_XOR", "INT_LEFT", "INT_RIGHT", "INT_SRIGHT")


def kind(d):
    """The lattice constructor of ``d``: one of ``KINDS``, ``bot`` or ``top``."""
    return d[0]


def _extent(d):
    """The row count a role denotation bounds its index by, or None where open."""
    return d[2] if d[0] in ("idx", "row") else None


def _bound(a, b):
    """Join of two row bounds: an unknown bound absorbs, else the weaker one."""
    return None if a is None or b is None else max(a, b)


def _in_lane(c, d):
    """Whether constant ``c`` is one of the channel values ``lane(a,b)`` takes."""
    v, r = divmod(c - d[1], d[2])
    return r == 0 and 0 <= v < CHANNELS


def _legal_row(c, e):
    """Whether constant ``c`` is a row of an ``e``-bounded index (0 always is)."""
    return c == 0 or (e is not None and 0 <= c < e)


def join(a, b):  # pylint: disable=too-many-return-statements
    """The §3.1 join: pointwise, with the block set of an ``addr`` allowed to grow.

    ``const ⊔ const`` is the loop's affine image and belongs to ``_consts``, which
    sees the whole constant set; the remaining crossings are subsumption -- a byte
    of a declared source read as a row of a declared table is that index."""
    if a == BOT:
        return b
    if b == BOT:
        return a
    if a == b:
        return a
    if a == TOP or b == TOP:
        return TOP
    ka, kb = a[0], b[0]
    if ka == kb:
        if ka == "byte":
            return ("byte", a[1] | b[1])
        if ka in ("idx", "row"):
            return (ka, a[1] | b[1], _bound(a[2], b[2]))
        if ka == "addr":
            return ("addr", a[1] | b[1], join(a[2], b[2]))
        return TOP
    lo, hi = (a, b) if KINDS.index(ka) < KINDS.index(kb) else (b, a)
    if lo[0] == "const" and hi[0] == "lane":
        return hi if _in_lane(lo[1], hi) else TOP
    if lo[0] == "const" and hi[0] in ("idx", "row"):
        return hi if _legal_row(lo[1], _extent(hi)) else TOP
    if lo[0] in ("idx", "row") and hi[0] == "byte":
        return lo
    return TOP


def _consts(vals):
    """§3.1's affine-image rule, set-wise: ``{0,7,14}`` over three channels is ``lane(0,7)``.

    It fires at exactly the channel count and a non-degenerate step; the caller's
    channel evidence gates it further, since three affine constants name no loop."""
    cs = sorted({c for _k, c in vals})
    if len(cs) == 1:
        return ("const", cs[0])
    if len(cs) == CHANNELS:
        step = cs[1] - cs[0]
        if step and cs[2] - cs[1] == step:
            return ("lane", cs[0], step)
    return TOP


def _affine(data):
    """``(a, b)`` where ``data`` is the non-degenerate affine image ``a + b·v``."""
    if len(data) != CHANNELS:
        return None
    step = data[1] - data[0]
    return (data[0], step) if step and data[2] - data[1] == step else None


class Record:
    """One unknown's solution and why: the rules that fired and what they read."""

    __slots__ = ("key", "den", "cause", "rules", "sel", "spell")

    def __init__(self, key, spell):
        self.key = key
        self.spell = spell
        self.den = BOT
        self.cause = T_NOFACT
        self.rules = set()
        self.sel = set()

    def __repr__(self):
        return "Record(%s=%s, %s, %d sel)" % (
            self.spell,
            self.den[0] if self.den != TOP else "top/" + self.cause,
            "|".join(sorted(self.rules)) or "-",
            len(self.sel),
        )


class Decls:
    """The declared data as the transfer rules ask about it, read from ``mem0``.

    Extents, ``lo``/``hi`` roles and element bytes come from the declarations the
    artifact carries and the image they were carved from, never from the trace."""

    def __init__(self, prog):
        self.decls = {d["base"]: d for d in prog.data_decls}
        self.regions = datadecl.Regions(prog.data_decls)
        self.pairs = datadecl.decl_pairs(prog.data_decls)
        self.mem0 = prog.mem0
        self._blocks = {}

    def at(self, base):
        """The declaration based exactly at ``base``, else None."""
        return self.decls.get(base)

    def covers(self, addr):
        """The declaration containing ``addr``, else None."""
        got = self.regions.at(addr)
        return None if got is None else got[0]

    def column(self, base):
        """``(declaration, bytes it holds from ``base`` on)`` for a table access base.

        A record's columns are declared as cobases of one declaration, so ``m_5598``
        is the eighth column of ``m_5591[263] stride 8`` and its extent is what the
        declaration still holds at that offset."""
        d = self.at(base) or self.covers(base)
        return None if d is None else (d, d["base"] + d["size"] - base)

    @staticmethod
    def const_table(d):
        """Whether every offset of ``d`` is a declared constant byte."""
        return not d["mut"]

    def blocks(self, lo):
        """§3.1's ``S``: the word set a declared ``lo``/``hi`` pair's rows name."""
        got = self._blocks.get(lo)
        if got is None:
            hi, size = self.pairs[lo]
            got = self._blocks[lo] = frozenset(
                self.mem0[lo + i] | (self.mem0[hi + i] << 8) for i in range(size)
            )
        return got

    def span(self, blocks):
        """The largest declared size over ``blocks``, or None where one is undeclared."""
        out = 0
        for b in blocks:
            d = self.covers(b)
            if d is None:
                return None
            out = max(out, d["base"] + d["size"] - b)
        return out


def _paths(stmts, out, path=()):
    """``statement path -> statement`` over one procedure's forest."""
    for i, s in enumerate(stmts):
        out[path + (i,)] = s
        for bi, b in enumerate(frameproc._stmt_bodies(s)):
            _paths(b, out, path + (i, bi))
    return out


def _index_local(idx):
    """The bare local an index expression names, through the reader's own widening."""
    if frameproc.is_op(idx, "INT_ZEXT", 2):
        idx = idx[2][0]
    return idx[1] if idx[0] == "loc" else None


def _index_cell(idx, decls):
    """The persistent unknown an index expression reads, else None."""
    if frameproc.is_op(idx, "INT_ZEXT", 2):
        idx = idx[2][0]
    if idx[0] != "mem":
        return None
    base, inner = frameproc.addr_split(idx[1])
    if base is None:
        return None
    if inner is None:
        return ("c", base)
    return ("t", base) if decls.column(base) is not None else None


def _defined(s):
    """The locals one statement defines, in the order the printer spells them."""
    k = s[0]
    if k in ("asg", "for"):
        return (s[1],)
    return tuple(s[3]) if k == "pcall" else ()


def _read(s):
    """The locals one statement reads, its own expressions only."""
    out = {}
    for x in frameproc._stmt_exprs(s):
        frameproc._uses_of(x, out)
    return out


class Solve:
    """One program's denotation fixpoint, its evidence records and its census.

    A definition says what a value is built from and an index use says what it
    selects, so each unknown carries a fact set; the rounds re-evaluate it, since a
    role fact at a deref needs the pointer's own block set to exist first."""

    def __init__(self, prog):
        self.prog = prog
        self.decls = Decls(prog)
        self.pws = prog.webs()
        self.res = prog.resolved
        self.val = {}
        self.rec = {}
        self.facts = {}
        self.rounds = 0
        self._trees = {e: _paths(stmts, {}) for e, _p, _r, stmts in prog.procs}
        self._chan = set()
        self._sites = []
        self._width = {}
        self._seed()
        self._run()

    def _key_web(self, entry, web):
        key = ("w", entry, web.root)
        if key not in self.rec:
            self.rec[key] = Record(key, "sub_%04X:%s" % (entry, web.name))
            self.val[key] = BOT
        return key

    def _key(self, key, spell):
        if key not in self.rec:
            self.rec[key] = Record(key, spell)
            self.val[key] = BOT
        return key

    def _fact(self, key, fact):
        got = self.facts.setdefault(key, [])
        if fact in got:
            return False
        got.append(fact)
        return True

    def _top(self, key, cause):
        self._fact(key, ("top", cause))

    def _seed(self):
        for entry, _pa, _rets, stmts in self.prog.procs:
            pw = self.pws[entry]
            for web in pw.webs:
                key = self._key_web(entry, web)
                for cls in web.refusals:
                    self._top(key, T_ENTRY if cls == frameproc.WEB_ENTRY else T_OPAQUE)
            for path, s in self._trees[entry].items():
                self._seed_stmt(entry, path, s, pw)
            self._seed_uses(entry, stmts, ())
        self._seed_image()

    def _seed_image(self):
        """What init left in a persistent unknown: the image's own bytes are a definition."""
        for key in [k for k in self.rec if k[0] in ("c", "t")]:
            base = key[1]
            if key[0] == "t":
                self._fact(key, ("lit", ("byte", frozenset([base]))))
                continue  # a byte of a declared source is one whatever store put it there
            w = self._width.get(base, 1)
            self._fact(key, ("init", int.from_bytes(self.decls.mem0[base : base + w], "little")))

    def _seed_stmt(self, entry, path, s, pw):
        k = s[0]
        if k == "asg":
            web = pw.at_def(path, s[1])
            if web is not None:
                self._fact(self._key_web(entry, web), ("def", entry, path, s[2]))
        elif k == "for":
            web = pw.at_def(path, s[1])
            if web is not None:
                self._for_facts(self._key_web(entry, web), s)
        elif k in ("pcall", "call"):
            names = s[3] if k == "pcall" else sorted(frameproc._ALL_REG_LOCALS)
            for n in names:
                web = pw.at_def(path, n)
                if web is not None:
                    self._top(self._key_web(entry, web), T_CALL)
        elif k == "st":
            self._store(entry, path, s)

    def _for_facts(self, key, s):
        """A ``for`` counter's constants: its own header range, where it is short."""
        step = 1 if s[3] >= s[2] else -1
        vals = list(range(s[2], s[3] + step, step))
        if len(vals) != CHANNELS:
            self._top(key, T_OP)
        for v in vals if len(vals) == CHANNELS else ():
            self._fact(key, ("const", v))

    def _store(self, entry, path, s):
        """A store's target unknown takes the stored value as a definition fact."""
        base, idx = frameproc.addr_split(s[1])
        if base is None:
            return
        if idx is None:
            w = G.store_width(s[2])
            self._width[base] = max(self._width.get(base, 1), w)
            self._fact(self._key(("c", base), G.addr_name(base)), ("def", entry, path, s[2]))
        elif self.decls.column(base) is not None:
            self._key(("t", base), G.addr_name(base))  # the role a use puts on its rows

    def _seed_uses(self, entry, stmts, path):
        """Every memory access of the forest: the deref population and its roles."""
        for i, s in enumerate(stmts):
            here = path + (i,)
            if s[0] == "st":
                self._site(entry, here, ("mem", s[1], G.store_width(s[2])))
            for x in frameproc._stmt_exprs(s):
                stack = [x]
                while stack:
                    n = stack.pop()
                    if n[0] == "mem":
                        self._site(entry, here, n)
                    stack.extend(frameproc._kids(n))
            for bi, b in enumerate(frameproc._stmt_bodies(s)):
                self._seed_uses(entry, b, here + (bi,))

    def _site(self, entry, path, n):
        """Record one memory access and the role its index carries."""
        addr = n[1]
        if not (addr[0] == "const" and addr[2] == 2):
            self._sites.append((entry, path, n))
        if addr in self.res:
            return
        base, idx = frameproc.addr_split(addr)
        if base is None or idx is None:
            return
        got = self.decls.column(base)
        if got is None or self._pair_word(base, n[2]):
            return
        d, size = got
        self._role(entry, path, idx, ("idx", frozenset([base]), size))
        if size == CHANNELS and set(d["mut"]) >= set(range(CHANNELS)):
            self._chan.add(self._index_key(entry, path, idx))

    def _pair_word(self, base, width):
        """Whether this access is the word read of a declared ``lo``/``hi`` pair."""
        return width == 2 and base in self.decls.pairs

    def _index_key(self, entry, path, idx):
        name = _index_local(idx)
        if name is not None:
            web = self.pws[entry].at_use(path, name)
            return None if web is None else self._key_web(entry, web)
        got = _index_cell(idx, self.decls)
        return None if got is None else self._key(got, G.addr_name(got[1]))

    def _role(self, entry, path, idx, role):
        key = self._index_key(entry, path, idx)
        return False if key is None else self._fact(key, ("role",) + role)

    def _run(self):
        for rnd in range(_ROUNDS):
            self.rounds = rnd + 1
            grew = self._derive()
            vals = dict(self.val)  # a rule may key an unknown no site had named yet
            for key in list(self.rec):
                got = join(self.val.get(key, BOT), self._resolve(key))
                rec = self.rec[key]
                rec.den = got
                rec.cause = rec.cause or (T_MIXED if got == TOP else None)
                vals[key] = got
            moved = vals != self.val or len(self.rec) != len(vals)
            self.val = vals
            if not (grew or moved):
                return
        for key, rec in self.rec.items():
            if self.val[key] != TOP:
                self.val[key], rec.den, rec.cause = TOP, TOP, T_ROUNDS

    def _derive(self):
        """Role facts a resolved pointer makes statable: ``*ptr[i]`` bounds ``i``."""
        grew = False
        for entry, path, n in self._sites:
            got = self.res.get(n[1])
            if got is None or got[1] is None:
                continue
            den = self.val.get(("c", got[0]), BOT)
            if den[0] != "addr":
                continue
            grew |= self._role(entry, path, got[1], ("row", den[1], self.decls.span(den[1])))
        return grew

    def _resolve(self, key):
        """One unknown's denotation: the value facts joined, then the role's refinement."""
        rec = self.rec[key]
        rec.rules, rec.sel, rec.cause = set(), set(), None
        facts = self.facts.get(key, ())
        if not facts:
            rec.den, rec.cause = TOP, T_NOFACT
            return TOP
        role = BOT
        for f in facts:
            if f[0] == "role":
                role = join(role, (f[1], f[2], f[3]))
        cause, consts, value, init, defs = None, [], BOT, None, False
        for f in facts:
            if f[0] == "role":
                continue
            if f[0] == "init":
                init = f[1]
            elif f[0] == "top":
                cause, value = cause or f[1], TOP
            elif f[0] == "const":
                consts.append(f)
            elif f[0] == "lit":
                defs = True
                value = join(value, self._refine(f[1], role, rec))
            else:
                defs = True
                got = self._ev(f[1], f[2], f[3], rec)
                value = join(value, self._refine(got, role, rec))
        if consts:
            got = _consts(consts)
            if got[0] == "lane" and key not in self._chan:
                got = TOP
            else:
                rec.rules.add("affine-const")
            value = join(value, self._refine(got, role, rec))
        if init is not None:
            value = self._staged(value, init, role, rec, defs or bool(consts))
        rec.den = value
        rec.cause = cause or rec.cause or (T_MIXED if value == TOP else None)
        return value

    def _refine(self, value, role, rec):
        """A role a use states is the reading of the value where the two cross."""
        if role == BOT:
            return value
        got = join(value, role)
        if got == role:
            rec.rules.add("index-use")
        return got

    def _staged(self, value, init, role, rec, defs):
        """What init left in a cell: the declared initial, which the state row carries.

        A cell no play store reaches denotes exactly that word; a cell the play
        stores do reach denotes what they put there, except that a ``const`` claim
        must agree with the image, since nothing else states the pre-store value."""
        if not defs:
            rec.rules.add("staged-init")
            return self._refine(("const", init), role, rec)
        if value[0] == "const" and value[1] != init:
            rec.cause = rec.cause or T_MIXED
            return TOP
        return value

    def _ev(self, entry, path, x, rec):
        k = x[0]
        if k == "const":
            return ("const", x[1])
        if k == "loc":
            return self._ev_loc(entry, path, x, rec)
        if k == "mem":
            return self._ev_mem(entry, path, x, rec)
        if k == "op":
            return self._ev_op(entry, path, x, rec)
        rec.cause = rec.cause or T_OP
        return TOP

    def _ev_loc(self, entry, path, x, rec):
        web = self.pws[entry].at_use(path, x[1])
        if web is None:
            return TOP
        key = self._key_web(entry, web)
        rec.sel.add(key)
        return self.val.get(key, BOT)

    def _ev_mem(self, entry, path, x, rec):
        """A load: the deref rule, then the declared-table rules of §3.3."""
        addr, width = x[1], x[2]
        got = self.res.get(addr)
        if got is not None:
            den = self.val.get(("c", got[0]), BOT)
            if den[0] == "addr":
                rec.rules.add("deref-row")
                return ("byte", den[1])
            if den == BOT:
                return BOT  # the pointer has no denotation yet: this round says nothing
            rec.cause = rec.cause or T_CELL
            return TOP
        base, idx = frameproc.addr_split(addr)
        if base is None:
            rec.cause = rec.cause or T_ADDR
            return TOP
        if idx is None:
            got = self._through(("c", base), rec) if ("c", base) in self.rec else TOP
            return got if got != TOP else self._declared(base, width, rec)
        return self._ev_row(entry, path, base, idx, width, rec)

    def _ev_row(self, entry, path, base, idx, width, rec):
        got = self.decls.column(base)
        if got is None:
            rec.cause = rec.cause or T_ADDR
            return TOP
        d = got[0]
        if self._pair_word(base, width):
            rec.rules.add("pair-row")
            got = self._index_key(entry, path, idx)
            if got is not None:
                rec.sel.add(got)
            return ("addr", self.decls.blocks(base), BOT)
        if self.decls.const_table(d) and got[1] == CHANNELS and width == 1:
            aff = _affine(list(d["data"]))
            if aff is not None:
                rec.rules.add("lane-table")
                return ("lane",) + aff
        rec.rules.add("table-row")
        key = ("t", base)
        got = self.val.get(key, BOT) if key in self.rec else BOT
        if got[0] in ("idx", "row"):
            rec.sel.add(key)
            return got
        return ("byte", frozenset([base]))

    def _through(self, key, rec):
        """Read one persistent unknown; a ⊤ there is a ⊤ here, and says so."""
        rec.sel.add(key)
        got = self.val.get(key, BOT)
        if got == TOP:
            rec.cause = rec.cause or T_CELL
        return got

    def _declared(self, base, width, rec):
        """A read of a declared datum at a constant address: the declaration is the fact."""
        d = self.decls.covers(base)
        if d is None:
            rec.cause = rec.cause or T_CELL
            return TOP
        rec.rules.add("cell-read")
        if self.decls.const_table(d):
            return ("const", int.from_bytes(self.decls.mem0[base : base + width], "little"))
        return ("byte", frozenset([d["base"]]))

    def _ev_op(self, entry, path, x, rec):
        """``addr + row`` is ``addr``; a cursor's step is a row with the bound open."""
        got = self._ev_pack(entry, path, x, rec)
        if got is not None:
            return got
        mn, kids = x[1], x[2]
        if mn in ("INT_ZEXT", "COPY"):
            return self._ev(entry, path, kids[0], rec)
        if mn in ("INT_ADD", "INT_SUB"):
            return self._ev_add(entry, path, kids, rec)
        if mn in _BITS and kids[-1][0] == "const":
            return self._ev_field(entry, path, kids, rec)
        rec.cause = rec.cause or T_OP
        return TOP

    def _ev_pack(self, entry, path, x, rec):
        """The catalog's ``word-pack`` of a declared ``lo``/``hi`` row: §3.3's pair rule.

        The two columns the printer spells ``T[i]:2`` are two byte loads until the
        renderer joins them, so the pointer's own definition is this shape."""
        got = frameproc.packed_cells(x)
        if got is None or got[2] is None:
            return None
        pair = frameproc.pair_site(self.decls.pairs, got[0], got[2])
        if pair is None or pair[0] != got[1]:
            return None
        rec.rules.add("pair-row")
        sel = self._index_key(entry, path, got[2])
        if sel is not None:
            rec.sel.add(sel)
        return ("addr", self.decls.blocks(got[0]), BOT)

    def _ev_field(self, entry, path, kids, rec):
        """A mask, set or shift against a literal keeps the datum's declared source."""
        base = self._ev(entry, path, kids[0], rec)
        if base == BOT:
            return BOT
        if base[0] == "byte":
            rec.rules.add("field-select")
            return base
        rec.cause = rec.cause or T_OP
        return TOP

    def _ev_add(self, entry, path, kids, rec):
        parts = [self._ev(entry, path, c, rec) for c in kids]
        if BOT in parts:
            return BOT
        at = [i for i, p in enumerate(parts) if p[0] in ("addr", "row", "idx")]
        if len(at) == 1 and all(p[0] == "const" for i, p in enumerate(parts) if i != at[0]):
            base = parts[at[0]]
            if base[0] == "addr":
                rec.rules.add("addr-row")
                return base
            rec.rules.add("cursor-step")
            return (base[0], base[1], None)
        rec.cause = rec.cause or T_OP
        return TOP

    def denote_use(self, entry, path, name):
        """The denotation of one read of ``name``, ``⊤`` where the web refuses."""
        web = self.pws[entry].at_use(path, name)
        return TOP if web is None else self.val.get(self._key_web(entry, web), TOP)

    def denote_def(self, entry, path, name):
        """The denotation of one definition of ``name``, ``⊤`` where the web refuses."""
        web = self.pws[entry].at_def(path, name)
        return TOP if web is None else self.val.get(self._key_web(entry, web), TOP)

    def value_sites(self):
        """``(denotation, cause)`` per local occurrence: the §5 ``⊤-sites`` population."""
        out = []
        for entry, _pa, _rets, _stmts in self.prog.procs:
            pw = self.pws[entry]
            for path, s in self._trees[entry].items():
                for name in _defined(s):
                    out.append(self._site_den(pw.at_def(path, name), entry))
                for name in sorted(_read(s)):
                    out.append(self._site_den(pw.at_use(path, name), entry))
        return out

    def _site_den(self, web, entry):
        if web is None:
            return (TOP, T_OPAQUE)
        key = self._key_web(entry, web)
        return (self.val.get(key, TOP), self.rec[key].cause)

    def deref_sites(self):
        """``(denotation, cause, pointer-rooted)`` per non-constant memory access.

        A deref site owes a denotation for *where it lands*, so it is typed by the
        address's ``addr(S,·)`` and not by the byte the access yields."""
        out = []
        for entry, path, n in self._sites:
            rec = Record(("s", entry, path), "site")
            rec.cause = None
            den = self._addr_den(entry, path, n, rec)
            out.append((den, rec.cause, n[1] in self.res))
        return out

    def _addr_den(self, entry, path, n, rec):
        addr = n[1]
        got = self.res.get(addr)
        if got is not None:
            den = self.val.get(("c", got[0]), BOT)
            if den[0] != "addr":
                rec.cause = T_CELL
                return TOP
            return ("addr", den[1], BOT if got[1] is None else self._index_den(entry, path, got[1]))
        base, idx = frameproc.addr_split(addr)
        if base is None:
            rec.cause = T_ADDR
            return TOP
        if 0xD400 <= base <= 0xD418:
            row = self._index_den(entry, path, idx)
            if row == TOP:
                rec.cause = T_MIXED
                return TOP
            return ("addr", frozenset([0xD400]), row)
        if self.decls.column(base) is None:
            rec.cause = T_ADDR
            return TOP
        return ("addr", frozenset([base]), self._index_den(entry, path, idx))

    def _index_den(self, entry, path, idx):
        if idx is None:
            return BOT
        key = self._index_key(entry, path, idx)
        if key is not None:
            return self.val.get(key, TOP)
        if frameproc.is_op(idx, "INT_ZEXT", 2):
            idx = idx[2][0]
        return ("const", idx[1]) if idx[0] == "const" else TOP

    def census(self):
        """The plan's §5 census over one program: sites, constructors and ⊤ by cause."""
        out = {"procs": len(self.prog.procs), "unknowns": len(self.rec)}
        out.update({"value_sites": 0, "deref_sites": 0, "deref_ptr": 0, "deref_ptr_typed": 0})
        out.update({"v_" + k: 0 for k in KINDS})
        out.update({"d_" + k: 0 for k in KINDS})
        out.update({"vtop_" + c: 0 for c in CAUSES})
        out.update({"dtop_" + c: 0 for c in CAUSES})
        out.update({"u_" + k: 0 for k in ("w", "c", "t")})
        out.update({"utyped_" + k: 0 for k in ("w", "c", "t")})
        out.update({"rule_" + r: 0 for r in RULES})
        for key, rec in self.rec.items():
            out["u_" + key[0]] += 1
            out["utyped_" + key[0]] += rec.den not in (TOP, BOT)
            for r in rec.rules:
                out["rule_" + r] += 1
        for den, cause in self.value_sites():
            out["value_sites"] += 1
            if den in (TOP, BOT):
                out["vtop_" + (cause if den == TOP else T_NOFACT)] += 1
            else:
                out["v_" + den[0]] += 1
        for den, cause, ptr in self.deref_sites():
            out["deref_sites"] += 1
            out["deref_ptr"] += ptr
            if den in (TOP, BOT):
                out["dtop_" + (cause or T_NOFACT)] += 1
            else:
                out["d_" + den[0]] += 1
                out["deref_ptr_typed"] += ptr
        return out


def solve(prog):
    """The denotation fixpoint of one frame program (docs/denotation-solve.md §3)."""
    return Solve(prog)
