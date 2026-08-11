"""ptrcert: is a pointer root a cursor into declared blocks? (register-model-lift 2a, 2b b3).

Block-rooting against ``datadecl``'s registry: every definition reaching a root the
program derefs top-wide is a reload, an advance, a save/restore, a block read or
``other``; b3 adds the extent enumeration. Read-only, so no text moves.
"""

from __future__ import annotations

from collections import Counter

from . import datadecl
from . import expr as E
from . import framefuse as FU
from . import frameproc
from . import frameptr
from . import grammar as G
from . import streams as ST
from .render import name_addr
from .structured import Proof

_CHASE = 12  # definitions followed to inline one local
_ROWS = 512  # reload-table rows read for the target set
_TARGETS = 512  # target words carried per root
_SAVES = 64  # save locations the closure admits
_CAP = 8  # evidence rows a record carries per list; the count is the number
_STACK = 0x01FF  # a reach at or under the stack is not the pointer traffic
_STEP = 0xFF  # an advance step wider than a byte is no in-block step

KINDS = ("reload", "advance", "save_restore", "block_read", "other")
REFUSALS = (
    "ptr_uncertified",
    "ptr_extent_open",
    "extent_mutable",
    "role_entangled",
    "role_opaque",
)
LIFT_REFUSALS = ("web_alias", "def_unliftable", "web_opaque", "low_held")
_SHAPES = ("block_read", "low_held", "opaque", "computed", "held_open")
_LIFTS = ("block_read", "computed")  # residue shapes the 2b spelling still writes


def root_cells(addr):
    """Width-2 named reads inside an address: the pointer roots it walks.

    A plain cell or indexed row read is a root at its base; a lo/hi column pair
    packed in place roots at the lo column. ``fuse_measure`` and
    ``storage_census`` read their pointer roots off this one definition."""
    out, stack = [], [addr]
    while stack:
        n = stack.pop()
        if not isinstance(n, tuple):
            continue
        if n[0] == "mem":
            base, _idx = frameproc.addr_split(n[1])
            if base is not None and n[2] == 2:
                out.append(base)
            stack.append(n[1])
        elif n[0] == "op":
            got = frameproc.packed_cells(n)
            if got is not None:
                out.append(got[0])
            stack.extend(n[2])
    return out


def _stmts(procs):
    """``(entry, env, seat, statement)`` over every statement of every procedure."""
    for entry, _p, _r, stmts in procs:
        for env, k, s in frameproc.envs(stmts):
            yield entry, env, k, s


def _top(addr, at, prog):
    """True where ``addr`` is the top-wide unnamed access the census counts."""
    if frameproc.addr_split(addr)[0] is not None or addr in prog.resolved:
        return False
    return frameproc.addr_bits(addr, at) > _STACK


def sites(prog):
    """``(root cell -> access counts, counts of top-wide accesses with no root)``.

    The population 2b would rewrite, read as ``storage_census`` reads its
    ``ptr_roots``: unnamed, unresolved by rung (f), reach past the stack. An
    access carrying no root is no cursor's to certify, so it is counted apart."""
    per, loose = {}, Counter()

    def record(addr, at, kind):
        if not _top(addr, at, prog):
            return
        roots = sorted(set(root_cells(addr)))
        if not roots:
            loose[kind] += 1
            return
        for c in roots:
            per.setdefault(c, Counter())[kind] += 1

    def scan(n, at):
        stack = [n]
        while stack:
            x = stack.pop()
            if not isinstance(x, tuple):
                continue
            if x[0] == "mem":
                record(x[1], at, "load")
                stack.append(x[1])
            elif x[0] == "op":
                stack.extend(x[2])

    for _entry, env, k, s in _stmts(prog.procs):
        at = frameproc.DefsAt(env, k)
        if s[0] == "st":
            record(s[1], at, "store")
            scan(s[2], at)
            scan(s[1], at)
        elif s[0] == "asg":
            scan(s[2], at)
    return per, dict(loose)


def _defn(env, n, k):
    """``(value, env, seat)`` of the definition in force for a local, else None.

    ``Defs._lookup``'s own chase, which a label, a call and a cyclic body already
    stop; a memory read it carries can only be staled by a store this analysis
    enumerates as a definition of the same web, so no definition's kind moves."""
    while n[0] == "loc":
        got = env._lookup(n[1], k)  # pylint: disable=protected-access
        if got is None or got[2] is None:
            return None
        env, k, n = got
    return n, env, k


def _inline(n, env, k, depth=_CHASE):
    """``n`` with its locals replaced by the definitions in force at ``(env, k)``."""
    if depth <= 0:
        return n
    if n[0] == "loc":
        got = _defn(env, n, k)
        return n if got is None else _inline(got[0], got[1], got[2], depth - 1)
    kids = frameproc._kids(n)
    if not kids:
        return n
    return frameproc._rebuild(n, tuple(_inline(c, env, k, depth - 1) for c in kids))


def _reads_cell(n, cell, width):
    """True where ``n`` is a plain read of ``cell`` at ``width``."""
    return n[0] == "mem" and n[2] == width and n[1][0] == "const" and n[1][1] == cell


def _carry(n):
    """True where ``n`` is a carry flag, or a disjunction of carry flags.

    The hi lane of a byte-wise advance adds exactly this: the flag the lo lane's
    add produced, spelled ``carry(lo,k) | carry(lo+k,0)`` where the fold ran."""
    n = ST._strip_zext(n)
    if frameproc.is_op(n, "INT_CARRY") or frameproc.is_op(n, "INT_SCARRY"):
        return True
    return frameproc.is_op(n, "INT_OR") and all(_carry(c) for c in n[2])


def _advance(v, cell, width, wide=frozenset()):
    """``(step bound, bounded)`` where ``v`` steps the cell's own value, else None.

    The sufficient form: one term of the add *is* the cell read at this width and
    every other term is a constant, a carry the lo lane produced, or a value
    bounded inside a byte -- an in-block advance, with no address computed."""
    if v[0] != "op" or v[1] not in ("INT_ADD", "INT_SUB"):
        return None
    mine = [i for i, c in enumerate(v[2]) if _reads_cell(c, cell, width)]
    if len(mine) != 1:
        return None
    bound, ok = 0, True
    for i, c in enumerate(v[2]):
        if i == mine[0]:
            continue
        if c[0] == "const":
            bound += c[1]
        elif _carry(c):
            bound += 1
        else:
            hi = frameptr._bound(c, wide)
            bound += hi
            ok = ok and hi <= _STEP and not frameproc._reads_mem(c)
    return bound, ok


def _partner(regions, d):
    """The declared partner table of a role-carrying pointer table, else None."""
    at = regions.at(d["role"][1])
    return None if at is None else at[0]


def _table_targets(d, other, mem0, off, end):
    """The block words the declared lo/hi pair's rows ``off..end`` name."""
    lo, hi = (d, other) if d["role"][0] == "lo" else (other, d)
    lb, hb = lo["base"], hi["base"]
    return {mem0[lb + j] | (mem0[hb + j] << 8) for j in range(off, min(end, off + _ROWS))}


def _reload(v, role, regions, mem0, wide=frozenset()):
    """``(table bases, target words)`` where ``v`` reads a declared pointer row.

    Rung (f)'s premise re-asked per definition and per leg against the registry:
    the byte comes from a table already given the matching role, a declared
    partner and no play-written offset, and the row index bounds the block set."""
    if role == "word":
        got = frameptr._entry(v)
        legs = None if got is None else [("lo", got[0], got[2]), ("hi", got[1], got[2])]
    else:
        got = frameptr._leg(ST._strip_zext(v))
        legs = None if got is None else [(role, got[0], got[1])]
    if legs is None:
        return None
    bases, words = [], set()
    for want, base, idx in legs:
        at = regions.at(base)
        if at is None:
            return None
        d, off = at
        if d["role"] is None or d["role"][0] != want or d["targets"] is None or d["mut"]:
            return None
        other = _partner(regions, d)
        if other is None or other["mut"]:
            return None
        end = min(d["size"], other["size"], off + frameptr._bound(idx, wide) + 1)
        if end <= off:
            return None
        bases.append(d["base"])
        words |= _table_targets(d, other, mem0, off, end)
    return sorted(bases), words


def _const(v):
    """The constant value ``v`` names, else None; packed byte lanes included.

    A pointer set to a literal block arrives as ``zext2($60) | zext2($15) << 8``
    once the lanes fold, so the row is constant even where no ``const`` node is."""
    if v[0] == "const":
        return v[1]
    if v[0] != "op":
        return None
    m = E.mask(frameproc.loc_width(v))
    if v[1] in ("INT_ZEXT", "COPY"):
        got = _const(v[2][0])
        return None if got is None else got & m
    kids = [_const(c) for c in v[2]]
    if any(c is None for c in kids):
        return None
    if v[1] == "INT_OR":
        out = 0
        for c in kids:
            out |= c
    elif v[1] == "INT_ADD":
        out = sum(kids)
    elif v[1] == "INT_AND":
        out = kids[0]
        for c in kids[1:]:
            out &= c
    elif v[1] == "INT_LEFT" and len(kids) == 2:
        out = kids[0] << kids[1]
    else:
        return None
    return out & m


def _held(v, width):
    """``(base, indexed)`` of a plain read that may be a cursor value held as data."""
    if v[0] != "mem" or v[2] != width:
        return None
    base, idx = frameproc.addr_split(v[1])
    return None if base is None else (base, idx is not None)


def _lane(v):
    """The wide value an emitted byte-lane extraction reads, else None."""
    if not frameproc.is_op(v, "COPY", 1):
        return None
    inner = v[2][0]
    hi = frameproc.shifted_hi(inner)
    return hi if hi is not None else (inner if frameproc.loc_width(inner) == 2 else None)


def _addr_const(a):
    """``(the address without its constant term, that constant)``."""
    if frameproc.is_op(a, "INT_ADD"):
        ks = [c[1] for c in a[2] if c[0] == "const"]
        rest = tuple(c for c in a[2] if c[0] != "const")
        if len(ks) == 1 and len(rest) == 1:
            return rest[0], ks[0]
    return a, 0


def _word_read(v):
    """The address of a 16-bit LE read, taken through the fused byte-lane spelling.

    b3's keying note: rung (d) fuses a block operand's two lane reads into one pack,
    so the shape recognizer sees ``computed`` where the machine read a word."""
    if v[0] == "mem" and v[2] == 2:
        return v[1]
    got = FU.unpack(v)
    if got is None:
        return None
    lo, hi = got
    if lo[0] != "mem" or hi[0] != "mem" or lo[2] != 1 or hi[2] != 1:
        return None
    (la, lk), (ha, hk) = _addr_const(lo[1]), _addr_const(hi[1])
    return lo[1] if la == ha and hk == lk + 1 else None


def _pair_legs(v):
    """``[(base, indexed)]`` of the two byte-table reads a packed row entry makes."""
    got = FU.unpack(v)
    if got is None:
        return None
    out = []
    for half in got:
        half = ST._strip_zext(half)
        if half[0] != "mem" or half[2] != 1:
            return None
        base, idx = frameproc.addr_split(half[1])
        if base is None:
            return None
        out.append((base, idx is not None))
    return out


def _pair_table(v, role, regions):
    """``[(decl, indexed)]`` where ``v`` reads a row of a play-written lo/hi table pair.

    Exactly the rows ``_reload``'s const premise refuses: the registry names the pair
    and the row, but play writes the columns, so the words ``mem0`` carries are not
    the words the row will hold -- b3 names the def and refuses ``extent_mutable``."""
    legs = None if role != "word" else _pair_legs(v)
    if legs is None:
        return None
    (lob, lix), (hib, hix) = legs
    alo, ahi = regions.at(lob), regions.at(hib)
    if alo is None or ahi is None:
        return None
    (dlo, off), (dhi, ohi) = alo, ahi
    if dlo["role"] != ("lo", dhi["base"]) or dhi["role"] != ("hi", dlo["base"]) or off != ohi:
        return None
    return [(dlo, lix), (dhi, hix)] if dlo["mut"] or dhi["mut"] else None


def _lanes_pair(lanes):
    """Every byte-lane block read has its partner one byte along, so a word was read.

    The enumeration keys on 16-bit LE values: a lone lane names half a pointer, and
    what the other half is came from some other definition entirely."""
    lo, hi = lanes["lo"], lanes["hi"]
    return all((a, k + 1) in hi for a, k in lo) and all((a, k - 1) in lo for a, k in hi)


def _def_refusal(d):
    """The class one definition refuses the 2b lift under, else None: it spells.

    A reload, an advance, a save/restore and a block read spell today; a computed
    def spells where rung (d) fused its lanes at one seat (role ``word``), and a
    hold's own writer answers for a save 2a's closure could not admit."""
    if d["shape"] is None:
        return None
    shape = d["writer"] if d["shape"] == "held_open" else d["shape"]
    if shape == "low_held":
        return "low_held"
    if shape not in _LIFTS:
        return "def_unliftable"
    if shape == "computed" and d["role"] not in ("word", "held"):
        return "def_unliftable"
    return None


def _shape(v):
    """Which residue shape an uncertified definition wears, for the ledger."""
    if v[0] == "loc":
        return "opaque"
    if v[0] == "mem":
        if root_cells(v[1]):
            return "block_read"
        if frameproc.addr_bits(v[1]) <= _STACK:
            return "low_held"
    return "computed"


class _Root:
    """One pointer root: every definition reaching it, and what each one proves."""

    def __init__(self, cell, counts):
        self.cell = cell
        self.pair = (cell, cell + 1)
        self.counts = dict(counts)
        self.defs = []
        self.kinds = Counter()
        self.shapes = Counter()
        self.targets = set()
        self.const_lo = set()
        self.const_hi = set()
        self.holds = set()
        self.table = None
        self.alias = []
        self.foreign = []
        self.opaque = []
        self.sources = set()
        self.blocks = []
        self.advance_bounded = True
        self.extent_declared = True
        self.init_declared = True
        self.init_word = 0
        self.notes = []
        self.read_roots = set()  # b3: cursors whose blocks this root reads its value out of
        self.read_blocks = set()  # b3: declared blocks it reads its value out of directly
        self.lanes = {"lo": set(), "hi": set()}  # b3: byte-lane block reads, to pair
        self.lane_open = False
        self.reach = set()  # b3: the static extent, the fixpoint's own answer
        self.mutable = set()  # b3: blocks it reads out of that some store reaches
        self.observed = None
        self.extent_short = []
        self.extent_certified = False

    def add(self, entry, role, kind, why, shape=None, writer=None):
        """Record one definition, the premise that named its kind, and its shape."""
        self.kinds[kind] += 1
        self.defs.append(
            {
                "proc": "$%04X" % entry,
                "role": role,
                "kind": kind,
                "premise": why,
                "shape": shape,
                "writer": writer,
            }
        )

    @property
    def block_rooted(self):
        """No definition falls outside the cursor shapes, and none may alias in."""
        return not self.kinds["other"] and not self.alias

    @property
    def reads_closed(self):
        """No read of the pair outside the web, and none through an unread local."""
        return not self.foreign and not self.opaque

    @property
    def extent_ok(self):
        """Every value the root may hold is a declared datum, under a bounded step."""
        return self.extent_declared and self.advance_bounded and self.init_declared

    def refusals(self):
        """The classes this root refuses under, in the plan's own vocabulary."""
        out = []
        if not self.block_rooted:
            out.append("ptr_uncertified")
        elif not self.extent_ok:
            out.append("ptr_extent_open")
        if self.mutable:
            out.append("extent_mutable")
        if self.foreign:
            out.append("role_entangled")
        elif self.opaque:
            out.append("role_opaque")
        return out

    def close(self, observed, closed):
        """b3's verdict against b0: the fixpoint equals what the run saw, or it closed.

        ``observed`` is this root's b0 block set, so a block the run reached and the
        enumeration did not name is the differential guard firing, ledgered apart."""
        if observed is None:
            return
        self.observed = set(observed)
        if self.refusals():
            return
        self.extent_short = sorted(self.observed - self.reach)
        self.extent_certified = bool(self.observed) and (closed or self.observed == self.reach)

    def lift_defs(self):
        """This web's definitions counted by the class each refuses under."""
        out = Counter()
        for d in self.defs:
            out[_def_refusal(d) or "admits"] += 1
        return out

    def lift_refusals(self):
        """The classes this web refuses 2b's lift under, premises (i)..(iv) in order.

        Separate from ``refusals``: certification is accounting, and everything 2a
        refused for the *extent* claim -- entangled byte reads, an open extent, an
        undeclared post-init value -- stops blocking once the guard checks at the
        deref (docs/register-model-lift-impl.md 2, 2b (b1))."""
        classes = set(self.lift_defs())
        out = ["web_alias"] if self.alias else []
        if "def_unliftable" in classes:
            out.append("def_unliftable")
        if self.opaque:
            out.append("web_opaque")
        if "low_held" in classes:
            out.append("low_held")
        return out

    @property
    def eligible(self):
        """No premise refuses, so rung (g) may rename this web to one u16."""
        return not self.lift_refusals()

    def status(self):
        """The one word for this root: what 2b may spell it as."""
        if not self.block_rooted:
            return "uncertified"
        if not self.reads_closed:
            return "entangled"
        return "block_rooted" if self.extent_ok else "extent_open"

    def lemma(self):
        """The record's own sentence: what held, over how many definitions."""
        per = ", ".join("%s %d" % (k, self.kinds[k]) for k in KINDS if self.kinds[k])
        body = "pointer root %s: %d definition(s) [%s]; %d block(s) %s; %d deref, %d write" % (
            name_addr(self.cell),
            len(self.defs),
            per or "-",
            len(self.blocks),
            "$%04X..$%04X" % (min(self.blocks), max(self.blocks)) if self.blocks else "-",
            self.counts.get("load", 0),
            self.counts.get("store", 0),
        )
        why = []
        if self.alias:
            why.append("%d store(s) may reach the pair unproven" % len(self.alias))
        if self.kinds["other"]:
            why.append("%d definition(s) name no cursor shape" % self.kinds["other"])
        if self.foreign:
            why.append("%d read(s) of the pair outside its web" % len(self.foreign))
        if self.opaque:
            why.append("%d unread local(s) may carry the pair" % len(self.opaque))
        if not self.advance_bounded:
            why.append("an advance steps by an unbounded amount")
        if not self.extent_declared:
            why.append(
                "a byte-lane block read has no partner lane"
                if self.lane_open
                else "a row the root may reload is in no declared datum"
            )
        if not self.init_declared:
            why.append("the post-init value $%04X is in no declared datum" % self.init_word)
        if self.mutable:
            why.append("%d block(s) it reads its value out of are play-written" % len(self.mutable))
        if self.extent_short:
            why.append(
                "%d observed block(s) the enumeration does not name" % len(self.extent_short)
            )
        return "%s; %s" % (body, "; ".join(why) if why else "block-rooted, extent declared")

    def proof(self):
        """The per-root proof record: which premise held, not a bare verdict."""
        status = "certified" if self.status() == "block_rooted" else "refused"
        return Proof(self.cell, "block-root", status, tuple(sorted(self.blocks)), self.lemma())

    def record(self):
        """The serializable proof record the sweep ledgers."""
        return {
            "root": "$%04X" % self.cell,
            "name": name_addr(self.cell),
            "pair": ["$%04X" % c for c in self.pair],
            "status": self.status(),
            "block_rooted": self.block_rooted,
            "reads_closed": self.reads_closed,
            "extent_declared": self.extent_declared,
            "init_declared": self.init_declared,
            "init": "$%04X" % self.init_word,
            "advance_bounded": self.advance_bounded,
            "kinds": {k: self.kinds[k] for k in KINDS},
            "shapes": dict(self.shapes),
            "table": None if self.table is None else "$%04X" % self.table,
            "defs": self.defs[:_CAP],
            "def_count": len(self.defs),
            "alias_stores": self.alias[:_CAP],
            "alias_count": len(self.alias),
            "foreign_reads": self.foreign[:_CAP],
            "foreign_count": len(self.foreign),
            "opaque_count": len(self.opaque),
            "sources": ["$%04X" % c for c in sorted(self.sources)],
            "blocks": ["$%04X" % b for b in sorted(self.blocks)],
            "reach": ["$%04X" % b for b in sorted(self.reach)],
            "read_roots": ["$%04X" % c for c in sorted(self.read_roots)],
            "read_blocks": ["$%04X" % b for b in sorted(self.read_blocks)],
            "mutable_blocks": ["$%04X" % b for b in sorted(self.mutable)],
            "extent_certified": self.extent_certified,
            "extent_short": ["$%04X" % b for b in self.extent_short[:_CAP]],
            "observed_blocks": None if self.observed is None else len(self.observed),
            "holds": ["$%04X" % b for b in sorted(self.holds)],
            "top_loads": self.counts.get("load", 0),
            "top_stores": self.counts.get("store", 0),
            "refusals": self.refusals(),
            "eligible": self.eligible,
            "lift_refusals": self.lift_refusals(),
            "lift_defs": dict(self.lift_defs()),
            "held_writers": dict(
                Counter(d["writer"] for d in self.defs if d["shape"] == "held_open")
            ),
            "lemma": self.lemma(),
            "notes": self.notes,
        }


class _Cert:
    """The program's pointer roots, certified against the declared block registry."""

    def __init__(self, prog):
        self.prog = prog
        self.mem0 = prog.mem0
        self.regions = datadecl.Regions(prog.data_decls)
        self.wide = frameptr._assigns(prog.procs)[0]
        self.per, self.loose = sites(prog)
        self.roots = {c: _Root(c, n) for c, n in self.per.items()}
        self.cells = {c + o: c for c in self.roots for o in (0, 1)}
        self.stores = []
        self.saves = {}
        self.edges = None

    def scan(self):
        """Index every store as ``(entry, env, seat, stmt, base, indexed, width)``."""
        for entry, env, k, s in _stmts(self.prog.procs):
            if s[0] != "st":
                continue
            base, idx = frameproc.addr_split(s[1])
            self.stores.append((entry, env, k, s, base, idx is not None, G.store_width(s[2])))
        return self

    def _table_root(self, cell):
        """``(decl, partner, offset)`` where a root cell names a declared table.

        A row of a declared lo/hi pointer table used as an address is rung (f)'s
        own object: the registry states the block set, and const data has no
        definition to classify -- the root is block-rooted by declaration."""
        at = self.regions.at(cell)
        if at is None:
            return None
        d, off = at
        if d["role"] is None or d["targets"] is None or d["mut"]:
            return None
        other = _partner(self.regions, d)
        return None if other is None or other["mut"] else (d, other, off)

    def _seed_tables(self):
        """Give every table root the registry's own statement as its definition."""
        for root in self.roots.values():
            got = self._table_root(root.cell)
            if got is None:
                continue
            d, other, off = got
            root.table = d["base"]
            root.targets |= _table_targets(d, other, self.mem0, off, d["size"])
            root.kinds["reload"] += 1
            root.defs.append(
                {
                    "proc": "-",
                    "role": "table",
                    "kind": "reload",
                    "premise": "declared lo/hi pointer table %s over %d row(s)"
                    % (name_addr(d["base"]), d["size"] - off),
                    "shape": None,
                    "writer": None,
                }
            )
        return self

    def _role(self, root, base, indexed, width):
        """Which leg of the pair a plain store writes, else None."""
        if indexed or base is None:
            return None
        if base == root.cell:
            return "word" if width == 2 else "lo"
        return "hi" if width == 1 and base == root.cell + 1 else None

    def _classify(self, root, role, v):
        """``(kind, premise, shape)`` of one definition, extent contribution absorbed.

        Rung (d) spells a lone half as the word's lane update, so a word definition
        that is one reads as the lane it replaced -- ``framefuse.lane_of``."""
        v = FU.unlane(v, root.cell)
        got = FU.lane_of(v, root.cell) if role == "word" else None
        if got is not None:
            return self._classify(root, got[0], got[1])
        width = 2 if role == "word" else 1
        got = _reload(v, role, self.regions, self.mem0, self.wide)
        if got is not None:
            root.targets |= got[1]
            names = ", ".join(name_addr(b) for b in got[0])
            return "reload", "row of declared pointer table(s) %s" % names, None
        k = _const(v)
        if k is not None:
            if role == "word":
                root.targets.add(k & 0xFFFF)
            elif role == "lo":
                root.const_lo.add(k & 0xFF)
            else:
                root.const_hi.add(k & 0xFF)
            return "reload", "constant row $%0*X" % (2 * width, k), None
        adv = _advance(v, root.cell + (1 if role == "hi" else 0), width, self.wide)
        if adv is not None:
            root.advance_bounded = root.advance_bounded and adv[1]
            open_ = "" if adv[1] else " (unbounded)"
            why = "in-block advance of the %s lane, step bound $%X%s" % (role, adv[0], open_)
            return "advance", why, None
        held = _held(v, width)
        if held is not None:
            base, indexed = held
            if base in self.cells:
                root.sources.add(self.cells[base])
                return "save_restore", "restored from cursor %s" % name_addr(base), None
            root.holds.add(base)
            self.saves.setdefault((base, indexed, width), set()).add(root.cell)
            at = self.regions.at(base)
            if at is not None:
                root.read_blocks.add(at[0]["base"])
            row = "[]" if indexed else ""
            why = "restored from held value %s%s" % (name_addr(base), row)
            return "save_restore", why, None
        shape = _shape(v)
        got = _pair_table(v, role, self.regions)
        if got is not None:
            root.shapes[shape] += 1
            for d, indexed in got:
                root.holds.add(d["base"])
                self.saves.setdefault((d["base"], indexed, 1), set()).add(root.cell)
                if d["mut"]:
                    root.mutable.add(d["base"])
            why = "row of declared pointer table %s, play-written" % name_addr(got[0][0]["base"])
            return "save_restore", why, shape
        got = self._block_read(role, v)
        if got is not None:
            root.shapes[shape] += 1
            through, blocks, addr = got
            root.read_roots |= set(through)
            root.read_blocks |= set(blocks)
            if role != "word":
                root.lanes[role].add(_addr_const(addr))
            named = [name_addr(c) for c in sorted(through)] + [name_addr(b) for b in sorted(blocks)]
            lane = "word" if role == "word" else "%s lane" % role
            return "block_read", "%s read out of %s" % (lane, ", ".join(named)), shape
        root.shapes[shape] += 1
        return "other", "%s: %s" % (shape, frameproc._fmt(v)), shape

    def _block_read(self, role, v):
        """``(cursor roots, declared blocks, address)`` a def reads its next value from.

        2a named this shape and left it uncertified. The word arrives fused (rung
        (d)'s pack, which the shape recognizer reads as ``computed``) or as the two
        byte lanes the machine wrote, so b3 keys on both."""
        if role == "word":
            addr = _word_read(v)
        else:
            addr = v[1] if v[0] == "mem" and v[2] == 1 else None
        if addr is None:
            return None
        through = sorted(set(root_cells(addr)) & set(self.roots))
        if through:
            return through, [], addr
        base, _idx = frameproc.addr_split(addr)
        at = None if base is None else self.regions.at(base)
        return None if at is None else ([], [at[0]["base"]], addr)

    def _block_edges(self):
        """``block base -> the declared datums a 16-bit LE word inside it names``.

        b3's fixpoint step, computed once per program: the registry is finite and so
        is this relation, which is what makes the least fixpoint terminate."""
        if self.edges is None:
            self.edges = {}
            for d in self.regions.decls:
                base, size, out = d["base"], d["size"], set()
                for a in range(base, base + max(0, size - 1)):
                    at = self.regions.at(self.mem0[a] | (self.mem0[a + 1] << 8))
                    if at is not None:
                        out.add(at[0]["base"])
                self.edges[base] = out
        return self.edges

    def enumerate(self):
        """b3: the static extent of every root, as a least fixpoint over the registry.

        E0 is 2a's own answer (reload rows plus the post-init block); each round adds
        every declared datum holding a word readable in the blocks the root reads out
        of. Monotone over a finite registry, so it terminates."""
        edges = self._block_edges()
        for root in self.roots.values():
            root.reach = set(root.blocks)
        grew = True
        while grew:
            grew = False
            for root in self.roots.values():
                add = set()
                for b in self._read_span(root):
                    add |= edges.get(b, ())
                if add - root.reach:
                    root.reach |= add
                    grew = True
        for root in self.roots.values():
            for b in self._read_span(root):
                at = self.regions.at(b)
                if at is not None and at[0]["mut"]:
                    root.mutable.add(b)
            if not _lanes_pair(root.lanes):
                root.extent_declared = False
                root.lane_open = True
                root.notes.append("a byte-lane block read has no adjacent partner lane")
        return self

    def _read_span(self, root):
        """The declared blocks a root's block reads take their word out of."""
        out = set(root.read_blocks)
        for c in root.read_roots:
            other = self.roots.get(c)
            if other is not None:
                out |= other.reach
        return out

    def _reach(self, s, at):
        """The range a store writes, floored by the bits its address must set."""
        got = frameproc.store_ref(s, self.regions)
        if got is not None:
            return got
        bits = frameproc.addr_bits(s[1], at)
        floor = frameproc.addr_floor(s[1], at)
        return (floor, frameproc.UNRES, max(0, bits - floor), G.store_width(s[2]), 0)

    def definitions(self):
        """Classify every store that may reach a root's pair."""
        for entry, env, k, s, base, indexed, width in self.stores:
            through = set(root_cells(s[1]))
            for root in self.roots.values():
                if root.cell in through:
                    continue
                role = self._role(root, base, indexed, width)
                if role is not None:
                    kind, why, shape = self._classify(root, role, _inline(s[2], env, k))
                    root.add(entry, role, kind, why, shape)
        return self

    def _span(self, cells):
        """The declared block span the certified roots of an address cover, else None."""
        lo, hi = 0x10000, 0
        for c in cells:
            root = self.roots.get(c)
            if root is None or not root.blocks or not root.extent_ok:
                return None
            for b in root.blocks:
                at = self.regions.at(b)
                if at is None:
                    return None
                lo, hi = min(lo, at[0]["base"]), max(hi, at[0]["base"] + at[0]["size"])
        return None if lo > hi else (lo, hi)

    def aliases(self):
        """Ledger every store this analysis cannot rule out of a root's pair.

        A store through a certified root is bounded by that root's own blocks --
        the certification spending itself, which is what makes the 12
        write-through players certifiable at all rather than each other's alias."""
        for entry, env, k, s, base, indexed, width in self.stores:
            at = frameproc.DefsAt(env, k)
            through = set(root_cells(s[1]))
            got = self._span(through) if through else None
            if got is not None:
                reach = (got[0], frameproc.UNRES, max(0, got[1] - got[0] - 1), width, 0)
            else:
                reach = self._reach(s, at)
            for root in self.roots.values():
                if root.cell in through or self._role(root, base, indexed, width) is not None:
                    continue
                if frameproc.overlaps(reach, (root.cell, frameproc.NOIDX, 0, 2, 0)):
                    root.alias.append({"proc": "$%04X" % entry, "addr": frameproc._fmt(s[1])})
        return self

    def _stores_into(self, base, indexed, width):
        """Every store whose destination is the held location, index unread."""
        for entry, env, k, s, sbase, sidx, swidth in self.stores:
            if sbase is None or swidth != width or sidx != indexed:
                continue
            if sbase == base if indexed else sbase <= base < sbase + swidth:
                yield entry, env, k, s

    def _cursor_value(self, v, width):
        """True where ``v`` is a value a certified cursor may legitimately hold.

        The web's own alphabet: a constant row, a read of a cursor or of another
        held location, an advance of one, or a declared reload row -- taken
        through the byte-lane extraction a saved word is spelled with."""
        core = _lane(v) or v
        if core[0] == "const":
            return True
        w = frameproc.loc_width(core)
        got = _held(core, w)
        if got is not None and (got[0] in self.cells or (got[0], got[1], w) in self.saves):
            return True
        if any(_advance(core, c, w, self.wide) is not None for c in self.cells):
            return True
        role = "word" if w == 2 else "lo"
        if _reload(core, role, self.regions, self.mem0, self.wide) is not None:
            return True
        return _reload(core, "hi", self.regions, self.mem0, self.wide) is not None

    def _carried(self, core):
        """Which roots a cursor-shaped value is read or stepped from."""
        w = frameproc.loc_width(core)
        hit = (c for c in self.cells if _reads_cell(core, c, w) or _advance(core, c, w, self.wide))
        return {self.cells[c] for c in hit}

    def closure(self):
        """Every held value a root restores is itself only written from a cursor.

        R9's design point as a check: Follin's loop cells and its 3-deep return
        stack are cursor values stored as data, so the web closes over them
        transitively, and a location it cannot admit refuses its roots' defs."""
        pending, seen = list(self.saves), set()
        while pending:
            loc = pending.pop()
            if loc in seen:
                continue
            seen.add(loc)
            base, indexed, width = loc
            owners = sorted(self.saves[loc])
            for entry, env, k, s in self._stores_into(base, indexed, width):
                v = _inline(s[2], env, k)
                core = _lane(v) or v
                if self._cursor_value(v, width):
                    for o in owners:
                        self.roots[o].sources |= self._carried(core)
                    held = _held(core, width)
                    nxt = None if held is None else (held[0], held[1], width)
                    if nxt is not None and held[0] not in self.cells and len(seen) < _SAVES:
                        self.saves.setdefault(nxt, set()).update(owners)
                        pending.append(nxt)
                    continue
                why = "held value %s is written from %s, not a cursor" % (
                    name_addr(base),
                    frameproc._fmt(v),
                )
                for c in owners:
                    root = self.roots[c]
                    root.shapes["held_open"] += 1
                    root.add(entry, "held", "other", why, "held_open", _shape(core))
        return self

    def _web_dest(self, base, indexed, width):
        """True where a store's destination is a cell of the certified web."""
        if base is None:
            return False
        if not indexed and any(base + o in self.cells for o in range(width)):
            return True
        return any(b == base and ix == indexed for b, ix, _w in self.saves)

    def _scan(self, n, cells, names):
        """Named cell reads and local reads under ``n``, deref addresses skipped."""
        stack = [n]
        while stack:
            x = stack.pop()
            if not isinstance(x, tuple):
                continue
            if x[0] == "loc":
                names.add(x[1])
            elif x[0] == "mem":
                if frameptr.deref(x[1]) is not None or root_cells(x[1]):
                    continue
                base, idx = frameproc.addr_split(x[1])
                if base is not None and idx is None:
                    cells.update(base + o for o in range(x[2]))
                stack.append(x[1])
            elif x[0] == "op":
                stack.extend(x[2])

    def _observed(self, s):
        """The expressions of ``s`` a rewrite must keep meaning, else None.

        A store into the web keeps only its address; an ``asg`` observes nothing
        itself, since its value reaches the statements that read the name."""
        if s[0] == "asg":
            return None
        if s[0] != "st":
            return list(frameproc._stmt_exprs(s))
        base, idx = frameproc.addr_split(s[1])
        if self._web_dest(base, idx is not None, G.store_width(s[2])):
            return []
        return [s[2]] if frameptr.deref(s[1]) is not None else [s[2], s[1]]

    def reads(self):
        """Ledger every read of a pair's bytes a statement outside the web observes.

        Taint over the procedure's local names, position-blind on purpose: a name
        a definition of the pair ever reaches carries the pair everywhere it is
        read, so the over-approximation falls on the refusing side."""
        for _e, _p, _r, stmts in self.prog.procs:
            per = list(frameproc.envs(stmts))
            taint = {}
            for _round in range(len(per) + 1):
                grew = False
                for env, k, s in per:
                    if s[0] != "asg":
                        continue
                    direct, opaque = self._roots_of(_inline(s[2], env, k), taint)
                    got = direct | opaque
                    if got - taint.get(s[1], set()):
                        taint.setdefault(s[1], set()).update(got)
                        grew = True
                if not grew:
                    break
            for env, k, s in per:
                for x in self._observed(s) or ():
                    direct, opaque = self._roots_of(_inline(x, env, k), taint)
                    for c in sorted(direct):
                        self.roots[c].foreign.append({"stmt": s[0], "via": "cell"})
                    for c in sorted(opaque - direct):
                        self.roots[c].opaque.append({"stmt": s[0], "via": "local"})
        return self

    def _roots_of(self, n, taint):
        """``(roots read directly, roots an unreadable local may carry)``.

        Inlining answers the common read; a local it could not read through is an
        entry value or a call's, and the name-keyed taint covers it position-blind
        -- coarse, so it is ledgered as its own class rather than as a read."""
        cells, names = set(), set()
        self._scan(n, cells, names)
        direct = {self.cells[a] for a in cells & set(self.cells)}
        opaque = set()
        for name in names:
            opaque |= taint.get(name, set())
        return direct, opaque

    def extents(self):
        """Every word a root may hold must land inside a declared datum.

        A root restored from another cursor holds that cursor's blocks, so the
        target sets close over the copy edges before the premise is asked."""
        for root in self.roots.values():
            root.init_word = self.mem0[root.cell] | (self.mem0[root.cell + 1] << 8)
            for lo in sorted(root.const_lo)[:16]:
                for hi in sorted(root.const_hi)[:16]:
                    root.targets.add(lo | (hi << 8))
            if root.const_lo and not root.const_hi:
                root.extent_declared = False
                root.notes.append("a constant lo byte has no constant hi partner")
        for _round in range(len(self.roots) + 1):
            grew = False
            for root in self.roots.values():
                for c in root.sources:
                    if self.roots[c].targets - root.targets:
                        root.targets |= self.roots[c].targets
                        grew = True
            if not grew:
                break
        for root in self.roots.values():
            if root.table is not None:
                root.init_word = root.table
            at = self.regions.at(root.init_word)
            root.init_declared = at is not None or root.table is not None
            if at is not None and root.table is None:
                root.blocks.append(at[0]["base"])
            if len(root.targets) > _TARGETS:
                root.extent_declared = False
                root.notes.append("target set exceeds %d words" % _TARGETS)
                continue
            for w in sorted(root.targets):
                at = self.regions.at(w)
                if at is None:
                    root.extent_declared = False
                    root.notes.append("no declared datum holds row $%04X" % w)
                    continue
                d = at[0]
                root.blocks.append(d["base"])
                if d["base"] <= root.cell + 1 and root.cell < d["base"] + d["size"]:
                    root.extent_declared = False
                    root.notes.append("block %s holds the root itself" % name_addr(d["base"]))
            root.blocks = sorted(set(root.blocks))
        return self

    def run(self, observed=None, closed=False):
        """Discharge every premise, in the order each one's inputs are ready.

        ``aliases`` reads 2a's own extent, never b3's enumeration: b3 is accounting,
        so the alias premise the lift rides on is left exactly where 2b measured it."""
        cert = self.scan()._seed_tables().definitions().closure().extents().enumerate()
        cert = cert.aliases().reads()
        for c, root in cert.roots.items():
            root.close(None if observed is None else observed.get(c, ()), closed)
        return cert


def certify(prog, observed=None, closed=False):
    """``([proof record], top-wide accesses with no root)`` for ``prog``.

    ``observed`` is b0's per-root block set for this run and ``closed`` says the run
    reached recurrence: together they decide b3's ``extent_certified`` column."""
    cert = _Cert(prog).run(observed, closed)
    return [cert.roots[c].record() for c in sorted(cert.roots)], dict(cert.loose)


def proofs(prog):
    """The same certification as ``Proof`` records, for the phases that consume it."""
    cert = _Cert(prog).run()
    return [cert.roots[c].proof() for c in sorted(cert.roots)]


def cert_cells(recs):
    """The root cells a certification covers, both legs of each pair."""
    return {int(r["root"][1:], 16) + o for r in recs for o in (0, 1)}


def observed_blocks(recs):
    """``{root cell: {block base}}`` from b0's extent records: b3's own comparison."""
    return {int(r["root"][1:], 16): {int(b[1:], 16) for b in r["blocks"]} for r in recs}


def summary(prog, cls=None, observed=None, closed=False):
    """One tune's certification row: coverage, the refusal ledger, the records.

    ``cls`` is ``streams.classify``'s cell table where the caller has it, so a
    root the finder names and the program no longer derefs top-wide is visible as
    rung (f)'s work rather than as a root nobody looked at."""
    recs, loose = certify(prog, observed, closed)
    ledger, lift, defs, held = Counter(), Counter(), Counter(), Counter()
    for r in recs:
        ledger.update(r["refusals"])
        lift.update(r["lift_refusals"])
        defs.update(r["lift_defs"])
        held.update(r["held_writers"])
    rooted = [r for r in recs if r["block_rooted"]]
    ready = [r for r in recs if r["status"] == "block_rooted"]
    fit = [r for r in recs if r["eligible"]]
    premises = {
        "defs_closed": sum(1 for r in recs if r["block_rooted"]),
        "reads_closed": sum(1 for r in recs if r["reads_closed"]),
        "rows_declared": sum(1 for r in recs if r["extent_declared"]),
        "init_declared": sum(1 for r in recs if r["init_declared"]),
        "advance_bounded": sum(1 for r in recs if r["advance_bounded"]),
    }
    out = {
        "roots": len(recs),
        "block_rooted": len(rooted),
        "cursor_ready": len(ready),
        "premises": premises,
        "extent": {
            "enumerated": sum(1 for r in recs if r["reach"]),
            "block_reads": sum(r["kinds"]["block_read"] for r in recs),
            "read_roots": sum(1 for r in recs if r["read_roots"] or r["read_blocks"]),
            "reach_blocks": sum(len(r["reach"]) for r in recs),
            "mutable": sum(1 for r in recs if r["mutable_blocks"]),
            "certified": sum(1 for r in recs if r["extent_certified"]),
            "short": sum(1 for r in recs if r["extent_short"]),
            "compared": sum(1 for r in recs if r["observed_blocks"] is not None),
        },
        "shapes": {k: sum(r["shapes"].get(k, 0) for r in recs) for k in _SHAPES},
        "top_loads": sum(r["top_loads"] for r in recs),
        "top_stores": sum(r["top_stores"] for r in recs),
        "rooted_loads": sum(r["top_loads"] for r in rooted),
        "rooted_stores": sum(r["top_stores"] for r in rooted),
        "ready_loads": sum(r["top_loads"] for r in ready),
        "ready_stores": sum(r["top_stores"] for r in ready),
        "unrooted": loose,
        "def_kinds": {k: sum(r["kinds"][k] for r in recs) for k in KINDS},
        "refusals": dict(ledger),
        "eligible": len(fit),
        "eligible_loads": sum(r["top_loads"] for r in fit),
        "eligible_stores": sum(r["top_stores"] for r in fit),
        "eligible_roots": [r["root"] for r in fit],
        "lift_refusals": dict(lift),
        "lift_alone": dict(
            Counter(r["lift_refusals"][0] for r in recs if len(r["lift_refusals"]) == 1)
        ),
        "lift_premises": {
            "web_closed": sum(1 for r in recs if "web_alias" not in r["lift_refusals"]),
            "defs_expressible": sum(1 for r in recs if "def_unliftable" not in r["lift_refusals"]),
            "uses_expressible": sum(1 for r in recs if "web_opaque" not in r["lift_refusals"]),
            "holds_off_stack": sum(1 for r in recs if "low_held" not in r["lift_refusals"]),
        },
        "lift_defs": dict(defs),
        "held_writers": dict(held),
        "records": recs,
    }
    if cls is not None:
        found = {c for c, r in cls.items() if r["class"] == "pointer"}
        out["finder_cells"] = len(found)
        out["finder_only"] = len(found - cert_cells(recs))
    return out
