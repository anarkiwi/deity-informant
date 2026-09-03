"""B7 -- the trackerprog as the binding of a certified tune's planes to the player.

There is one player (:mod:`.universal`) with a fixed tick and a fixed state
vector, and a certified tuneprog has the same slots under other names.  This
module binds them: S6's roles and T2's cursors name the player's own cells, T2's
selector is the instrument table, T2's score is the cursor nest, T1's records are
section 5's accumulators and T0's write sites are what produces.  Nothing is
lowered: a value is read at its own site and expressed over a named cell, an
instrument column or a row fact, and a name two paths bind differently splits
the row rather than becoming a cell of the object.
"""

from __future__ import annotations

import itertools

from . import algebra
from ..tuneprog.ir import Bin, Const, Let, Load, Store, Var
from ..tuneprog.irwalk import addr_split, walk
from .lower import Unlowerable

MAXCOMBO = 24
# the event fields section 3.6 lists that a guard may read as a cell of the player
FACTCELL = {"dur": {"cell": "dur"}, "tie": {"cell": "tied"}, "note": {"cell": "note"},
            "ins": {"cell": "ins"}}


def ambiguous(proc):
    """``{name: {block: value}}`` for every SSA name more than one block binds."""
    out = {}
    for lbl, b in proc.blocks.items():
        for s in b.stmts:
            if type(s) is Let:
                out.setdefault(s.n, {})[lbl] = s.e
    return {n: v for n, v in out.items() if len(v) > 1}


def _consistent(guards):
    """Whether guard terms can hold together: no condition under both truths."""
    seen = {}
    for _d, c, t in guards:
        if seen.setdefault(id(c), t) != t:
            return False
    return True


class Rows:
    """One segment read as guarded rows over the object's own cells.

    A block is one row.  Where a name the block reads is bound on more than one
    path the row is split -- one row per binding, under the guard of the block
    that bound it -- which is what keeps an SSA temp out of the object.
    """

    def __init__(self, low, amb):
        self.low, self.amb = low, amb

    def needs(self, lbl, drop=()):
        """The ambiguous names one block's own surviving stores read, after expansion."""
        low = self.low
        low.lbl, low.local, low.turn, low.pick = lbl, {}, None, {}
        out = []
        for s in low.proc.blocks[lbl].stmts:
            if type(s) is not Store or s.src in drop:
                continue
            for e in (s.v, s.a):
                for x in walk(low.expand(e)):
                    if type(x) is Var and x.n in self.amb and x.n not in low.v.vidx:
                        if x.n not in out:
                            out.append(x.n)
        return out

    def bindings(self, lbl, guard, drop=()):
        """``[(extra guard, {name: block})]``: the paths one block's row is read on."""
        names = self.needs(lbl, drop)
        if not names:
            return [((), {})]
        choice = [[(tuple(self.low.eff.get(d, ((), ()))[0]), d) for d in self.amb[n]]
                  for n in names]
        out = []
        for combo in itertools.islice(itertools.product(*choice), MAXCOMBO):
            gs = list(guard)
            for g, _d in combo:
                gs += [t for t in g if t not in gs]
            if not _consistent(gs):
                continue
            extra = tuple(t for t in gs if t not in guard)
            out.append((extra, {n: d for n, (_g, d) in zip(names, combo)}))
        return out

    def when(self, guard):
        """One row's guard: the block's own path terms, each read at its own site."""
        low, out = self.low, []
        for d, c, t in guard:
            if not low.onpath(d, c, t):
                continue
            low.lbl = d
            fact = low.v.terms.get(repr(c)) if low.v.payload else None
            got = ([fact, "!=" if t else "==", 0] if fact is not None
                   else low.term(low.expand(c), t))
            if got not in out:
                out.append(got)
        return out

    def sets(self, lbl, drop):
        """One block's stores, each as a ``sets`` assignment over the object's cells."""
        low, out = self.low, []
        for s in low.proc.blocks[lbl].stmts:
            if type(s) is not Store or s.src in drop:
                continue
            got = low.v.target(low, s)
            if got is None:
                continue
            name = "@" + got[1] if got[0] == "copy" else got[1]
            out.append([name, low.value(low.expand(s.v))])
        return out

    def rows(self, blocks, order, drop=()):
        """One segment as guarded rows in program order, split where a path binds."""
        low, out = self.low, []
        for lbl in [l for l in order if l in blocks]:
            if not any(type(s) is Store for s in low.proc.blocks[lbl].stmts):
                continue
            guard = tuple(low.eff.get(lbl, ((), ()))[0])
            for extra, pick in self.bindings(lbl, guard):
                low.pick = {n: self.amb[n][d] for n, d in pick.items()}
                low.lbl, low.local, low.turn = lbl, {}, None
                try:
                    row = {"when": self.when(guard + extra), "sets": self.sets(lbl, drop)}
                except Unlowerable as x:
                    low.bad.add("%s: %s" % (lbl, x))
                    continue
                if row["sets"]:
                    out.append((lbl, row))
        low.pick = {}
        return out


# ---- the score the fetch regions read, as section 3.6 events -------------------
def _stores(rec):
    """``{address: value}`` and ``{site: value}``: the ram stores one visit made."""
    out, sites = {}, {}
    for cls, a, v, _w, src in rec["cmds"]:
        if cls in ("ram", "chk"):
            out[a] = int(v)
            sites[src] = int(v)
    return out, sites


class Score:
    """T2's cursor nest as events whose fields the fetch's own stores name.

    A visit of a fetch region is one row of one voice: ``dur`` is the value it
    stored into the clock's cell, ``note`` the value it stored into the cell that
    indexes the tuning, ``ins`` the value it stored into the selector's index
    cell, and ``sounds`` whether it stored a note at all (section 3.6).
    """

    def __init__(self, records, vvar, roles, voices, stride, ordpos, top, seed=None):
        self.rows, self.voices, self.top = {v: [] for v in range(voices)}, voices, top
        self.seed = seed or [0] * voices
        self.supplied = {}
        for rec in sorted(records, key=lambda r: r.get("seq", 0)):
            at = rec["env"].get(vvar)
            if at is None or at % stride or at // stride not in self.rows:
                continue
            v, (st, sites) = at // stride, _stores(rec)
            row = {
                "dur": st.get(roles["dur"] + at, 0),
                "note": st.get(roles["note"] + at),
                "ins": st.get(roles["ins"] + at),
                "packed": {n: st.get(a + at) for n, a in roles.get("packed", {}).items()},
                "temps": {n: int(x) for n, x in rec["temps"].items()},
                "st": st,
                "sites": sites,
                "at": at,
                "ends": ordpos is not None and ordpos + at in st,
                "next": st.get(ordpos + at) if ordpos is not None else None,
                "sets": [],
            }
            self.rows[v].append(row)

    def facts(self):
        """``{name: [value per visit]}`` for the fields a guard may be read against."""
        out = {k: [] for k in ("dur", "note", "ins", "sounds", "newins", "wraps", "field")}
        temps = {}
        for v in range(self.voices):
            for r in self.rows[v]:
                out["dur"].append(r["dur"])
                out["note"].append(r["note"])
                out["ins"].append(r["ins"])
                out["sounds"].append(int(r["note"] is not None))
                out["newins"].append(int(r["ins"] is not None))
                out["wraps"].append(int(r["ends"]))
                out["field"].append(int(r["ins"] is not None or bool(r["sets"])))
                for n, x in r["temps"].items():
                    temps.setdefault(n, []).append(x)
        return out, temps

    def events(self, tie):
        """``(orders, patterns)``: the visits as per-voice play lists of events.

        A visit belongs to the step of the order program the tune's own cursor was
        on, so the play list is the score's own list and not the walk the horizon
        took: a second turn of the same step is the same step (§3.6).
        """
        orders, pats = [], {}
        for v in range(self.voices):
            play, cur, at = {}, [], self.seed[v]
            for r in self.rows[v]:
                n = r["note"]
                cur.append(
                    {
                        "dur": r["dur"],
                        "sounds": n is not None,
                        "note": None if n is None or n >= self.top else n,
                        "gate": None,
                        "tie": bool(tie(r)),
                        "ins": r["ins"],
                        "arm": {"rows": [{"sets": r["sets"]}]} if r["sets"] else None,
                    }
                )
                if r["ends"] and cur:
                    _visit(play, pats, cur, at)
                    cur, at = [], r["next"] if r["next"] is not None else at + 1
            if cur:
                _visit(play, pats, cur, at)
            orders.append({"play": [play.get(i, 0) for i in range(max(play, default=-1) + 1)],
                           "end": {"jump": 0}})
        got = sorted(pats.values(), key=lambda x: x[0])
        return orders, {str(k): {"events": rows} for k, rows in got}


def _keyof(e):
    """One event as the tuple two visits are the same pattern by."""
    return (e["dur"], e["sounds"], e["note"], e["tie"], e["ins"], repr(e["arm"]))


def _visit(play, pats, rows, at):
    """One visit of one pattern, kept once and named by what its events decode to."""
    key = tuple(_keyof(e) for e in rows)
    got = pats.get(key)
    if got is None:
        got = pats[key] = (len(pats), rows)
    play.setdefault(at, got[0])


# ---- the fields a guard reads a score byte by ---------------------------------
def masks_of(low):
    """``{(supplied name, mask)}``: every masked field of a score byte the tick reads."""
    out = set()
    for lbl, b in low.proc.blocks.items():
        low.lbl, low.local, low.pick = lbl, {}, {}
        for s in list(b.stmts) + [b.term]:
            for e in (getattr(s, "e", None), getattr(s, "v", None), getattr(s, "c", None)):
                for x in walk(e) if e is not None else ():
                    if type(x) is Bin and x.op == "&" and type(x.b) is Const:
                        got = low.expand(x.a)
                        if type(got) is Var and got.n in low.v.supplied:
                            out.add((got.n, x.b.v))
                    elif type(x) is Var and x.n in low.v.supplied:
                        out.add((x.n, None))
    return out


def _same(a, b):
    """Whether two value lists agree wherever both are stated, and say something."""
    got = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    return len({x for x, _y in got}) > 1 and all(x == y for x, y in got)


def _truthy(a, b):
    """Whether two lists have the same truth throughout, and it is not one value.

    A field that never changes over the horizon is matched by every other that
    never changes, so a constant is no evidence and the match is refused.
    """
    got = [(x, y) for x, y in zip(a, b) if x is not None]
    return len({bool(x) for x, _y in got}) > 1 and all(bool(x) == bool(y) for x, y in got)


def fields_of(uses, facts, temps):
    """``{(name, mask): node}``: what a masked score byte is, of section 3.6's fields.

    The field list is closed, so each candidate is decided by what the horizon's
    own visits say: a value that is the row's length is ``dur``, one whose truth
    is whether the row keys a sound is ``sounds``, and the one field left that a
    guard still reads is the row's ``tie``.
    """
    out, left = {}, []
    for name, mask in sorted(uses, key=lambda x: (x[0], -1 if x[1] is None else x[1])):
        vals = temps.get(name)
        if vals is None:
            continue
        vals = [None if v is None else (v if mask is None else v & mask) for v in vals]
        for key, node in (("dur", {"cell": "dur"}), ("note", {"cell": "note"}),
                          ("ins", {"cell": "ins"})):
            if _same(vals, facts[key]):
                out[(name, mask)] = node
                break
        else:
            if _truthy(vals, facts["sounds"]):
                out[(name, mask)] = "sounds"
            elif _truthy(vals, [1 - x for x in facts["sounds"]]):
                out[(name, mask)] = {"xor": ["sounds", 1]}
            elif _truthy(vals, facts["newins"]):
                out[(name, mask)] = "newins"
            elif _truthy(vals, facts["field"]):
                out[(name, mask)] = "field"
            elif _truthy(vals, [1 - x for x in facts["field"]]):
                out[(name, mask)] = {"xor": ["field", 1]}
            elif mask is not None:
                left.append((name, mask, vals))
    return out, left


def tie_of(out, left):
    """Section 3.6's ``tie``: the one field of the row a guard still reads.

    A row that re-targets without re-triggering is what disarms an instrument's
    prelude, and the field list has no other name for a bit of the row the tick
    tests and nothing else explains.
    """
    own = {n for (n, _m), node in out.items() if node == {"cell": "dur"}}
    got = sorted({(n, m) for n, m, _v in left if not own or n in own})
    if len(got) != 1:
        return None, dict(out)
    out = dict(out)
    out[got[0]] = {"cell": "tied"}
    return got[0], out


# ---- T1's records, rendered into section 5's ------------------------------------
def _addr(ref):
    return None if ref is None else int(ref["addr"][1:], 16)


def _load(low, addr, w=1):
    """One cell read as the tick reads it: the byte at a constant address."""
    return low.expand(Load("ram", Const(addr, 2), w, addr, addr, -1))


def _halving(s, bases):
    """Whether one store halves the word a pair of bases holds."""
    if type(s) is not Store or addr_split(s.a)[0] not in bases:
        return False
    return any(type(x) is Bin and x.op == ">>" and type(x.b) is Const and x.b.v == 1
               for x in walk(s.v))


def shift_of(low, addr, bases):
    """How far a table difference is shifted down: the loop's own count.

    A loop that halves the word once a turn shifts it by the count its counter
    enters with, and once more where the test follows the body: the head's own
    statements run before the exit is decided, so the loop makes one more pass.
    """
    body, head = frozenset(), None
    for h, (blocks, _lat) in sorted(low.loops.items()):
        if not any(_halving(s, bases) for l in blocks for s in low.proc.blocks[l].stmts):
            continue
        if not body or len(blocks) < len(body):
            body, head = blocks, h
    if head is None or addr is None:
        return 0
    k = 1 if any(_halving(s, bases) for s in low.proc.blocks[head].stmts) else 0
    pre = next((q for q in sorted(low.preds.get(head, ())) if q not in body), head)
    low.lbl, low.local, low.pick = pre, {}, {}
    got = low.value(_load(low, addr))
    return got if not k else {"add": [got, k]}


class Accs:
    """T1's records over the object's own cells: section 5, field for field."""

    def __init__(self, low, art, names, view):
        self.low, self.names, self.view = low, names, view
        self.t1 = [a for a in (art["t1"].get("accs") or [])]
        self.t0 = art["t0"].get("writes") or []
        self.eff = low.eff
        self.blocks = {}
        for w in self.t0:
            self.blocks.setdefault(w["site"]["block"], []).append(w)

    def base_of(self, name):
        """The address the region S6 names holds, for a name a record states."""
        for r in self.view.storage:
            if r.id >= 0 and self.names.of(r.id) == name:
                return r.base
        return None

    def siteblocks(self, a):
        """The blocks one record's own sites stand in."""
        want = {int(s[1:], 16) for s in a["sites"]}
        return [l for l, b in self.low.proc.blocks.items()
                if any(type(s) is Store and s.src in want for s in b.stmts)]

    def when(self, a):
        """A record's own ``when``: the terms every one of its sites stands under.

        A term the record's own loop carries is not its guard: the ``repeat`` of
        section 5 states that loop, so a term over a name the loop rebinds is
        dropped rather than read as a cell.
        """
        blocks = self.siteblocks(a)
        got = [set(self.eff.get(l, ((), ()))[0]) for l in blocks]
        if not got:
            return ()
        keep = set.intersection(*got)
        out = []
        for d, c, t in self.eff.get(blocks[0], ((), ()))[0]:
            if (d, c, t) not in keep or _carried(self.low, c):
                continue
            out.append((d, c, t))
        return tuple(out)

    def under(self, lbl, when):
        """Whether a block and a record run under one guard, either way about.

        A record's own produce may stand where its step's loop has closed, so the
        block's path is the record's where one is a prefix of the other.
        """
        if lbl not in self.low.proc.blocks:
            return False
        got = set(self.eff.get(lbl, ((), ()))[0])
        return set(when) <= got or got <= set(when)

    def cellname(self, a, addr):
        """The object's own name for a record's value cell: a role, ins.pw or a global."""
        low = self.low
        got = low.cells.at(addr)
        if got is not None and got[0] == "inspw":
            return "ins.pw." + got[1][0]
        if int(a["cell"]["copies"]) <= 1:
            nm = _u16name(self.names, a["cell"]["region"]) or self.names.of(a["cell"]["region"])
            low.cells.widths["#" + nm] = 2 if a["width"] == 16 else 1
            return low.cells.declare("#" + nm, addr)
        nm = low.cells.voicecell(addr)
        return nm[:-3] if nm.endswith((".lo", ".hi")) else nm

    def produce(self, a, when):
        """Where a record's value goes: the T0 sites its own cells reach (§5)."""
        lo, regions = a["cell"]["region"], set(a["regions"])
        out, sites, blocks = [], set(), set()
        for w in self.t0:
            if not w.get("register") or not self.under(w["site"]["block"], when):
                continue
            hit = {c["region"] for c in w.get("cells") or ()} & regions
            if not hit:
                continue
            part = "byte" if a["width"] <= 8 else ("lo" if lo in hit else "hi")
            reg = w["register"]
            sites |= self.regsites(w["site"]["block"], reg)
            blocks.add(w["site"]["block"])
            if reg == "freq":  # a 16-bit write of the pair the chip reads as one
                out += [("freq_lo", "lo"), ("freq_hi", "hi")]
            else:
                out.append((reg, part))
        return [list(x) for x in dict.fromkeys(out)], sites, sorted(blocks)

    def regsites(self, lbl, reg):
        """The stores one block makes to the registers a 16-bit produce sends.

        A pair the chip reads as one value is two stores of the machine and one
        write of T0, so the record's produce states both.
        """
        want = {"freq": ("freq_lo", "freq_hi"), "pw": ("pw_lo", "pw_hi")}.get(reg, (reg,))
        out = set()
        if lbl not in self.low.proc.blocks:
            return out
        for s in self.low.proc.blocks[lbl].stmts:
            if type(s) is not Store or s.cls != "io":
                continue
            base = addr_split(s.a)[0]
            if base is not None and self.low.v.regs.get(base - 0xD400) in want:
                out.add(s.src)
        return out

    def delta(self, a, blk):
        """T1's delta over the object's cells: section 5's own four forms."""
        low, d = self.low, a["delta"] or {}
        low.lbl, low.local, low.pick = blk, {}, {}
        kind = d.get("kind")
        if kind == "const":
            got = int(d["value"])
        elif kind == "field":
            got = self.cellvalue(_addr(d["cell"]), d["cell"].get("width") or 1)
            if int(d.get("mask", 0xFF)) not in (0xFF, 0xFFFF):
                got = {"and": [got, int(d["mask"])]}
        elif kind == "tabcell":
            rid = d["cell"]["region"]
            if rid not in low.v.inscol:
                return None
            got = {"ins": low.v.inscol[rid]}
        elif kind == "repeat":
            step = self.tablestep(d["step"], blk)
            if step is None:
                return None
            got = {"repeat": [step, self.cellvalue(_addr(d["n"]), d["n"].get("width") or 1)]}
        else:
            return None
        carry = (d.get("carry") or {}).get("flag")
        return {"add": [got, {"flag": _flagname(carry)}]} if carry else got

    def cellvalue(self, addr, w):
        """One cell a record reads: what the tick left in it, or the cell itself.

        A fold that leaves a name more than one block of the tick binds is no
        reading of the record's own input, so the cell is read as the cell.
        """
        low = self.low
        got = low.value(_load(low, addr, w))
        if _clean(low, got):
            return got
        return low.value(Load("ram", Const(addr, 2), w, addr, addr, -1))

    def tablestep(self, step, blk):
        """A table difference shifted down: section 5's ``interval``, and by how far."""
        idx = _addr(step.get("index"))
        if idx is None or idx != self.low.v.notebase or int(step.get("span") or 0) != 2:
            return None
        bases = {_addr(step["cell"]), _addr(step["cell"]) + 1}
        k = shift_of(self.low, self.base_of(step.get("shift")), bases)
        self.low.lbl, self.low.local, self.low.pick = blk, {}, {}
        return {"interval": None} if k == 0 else {"shr": [{"interval": None}, k]}

    def reloads(self, a):
        """``{address: (block, value)}``: the stores that reload a record's own cell."""
        want, out = set(self.siteblocks(a)), {}
        when = self.when(a)
        for lbl in self.low.proc.blocks:
            if lbl in want or not self.under(lbl, when):
                continue
            for s in self.low.proc.blocks[lbl].stmts:
                if type(s) is Store and s.r in set(a["regions"]):
                    out[addr_split(s.a)[0]] = (lbl, s.v)
        return out

    def policy(self, a, blk):
        """T1's policy, its reload read where the record's own reload stands."""
        low = self.low
        if a["policy"] != "reload":
            return a["policy"]
        got = self.reloads(a)
        lo = _addr(a["cell"])
        if got:
            halves = []
            for addr in ([lo] if a["width"] <= 8 else [lo, next(
                    (self.view.by_id()[r].base for r in a["regions"]
                     if r != a["cell"]["region"]), lo + 1)]):
                hit = got.get(addr)
                if hit is None:
                    halves = []
                    break
                low.lbl, low.local, low.pick = hit[0], {}, {}
                halves.append(low.value(low.expand(hit[1])))
            if len(halves) == 1:
                return {"reload": halves[0]}
            if halves:
                return {"reload": algebra.unsplit(*halves)
                        or {"or": [halves[0], {"shl": [halves[1], 8]}]}}
        low.lbl, low.local, low.pick = blk, {}, {}
        if a["width"] <= 8:
            return {"reload": low.value(low.expand(_reload(low, lo)))}
        hi = next((self.view.by_id()[r].base for r in a["regions"]
                   if r != a["cell"]["region"]), lo + 1)
        halves = [low.value(low.expand(_reload(low, x))) for x in (lo, hi)]
        return {"reload": algebra.unsplit(*halves) or {"or": [halves[0], {"shl": [halves[1], 8]}]}}

    def phase(self, a, blk):
        """T1's phase over the object's cells: a bit of a live cell, or none."""
        ph = a.get("phase") or {}
        if ph.get("kind") != "bit" or ph.get("cell") is None:
            return None
        self.low.lbl, self.low.local, self.low.pick = blk, {}, {}
        return {"bit": [self.low.value(_load(self.low, _addr(ph["cell"]))), int(ph["bit"])]}

    def order(self, rpo_):
        """T1's records in the order the tick's own program runs them."""
        at = {l: i for i, l in enumerate(rpo_)}
        return sorted(self.t1, key=lambda a: min(at.get(l, 0) for l in self.siteblocks(a)))

    def record(self, a, rank):
        """One T1 accumulator as section 5's record, and the stores it states."""
        sitewhen = self.when(a)
        blocks = sorted(self.siteblocks(a), key=lambda l: self.low.rpo.index(l))
        blk = blocks[0]
        produce, psites, pblocks = self.produce(a, sitewhen)
        # a term the step stands under and the produce does not is `delta_when`:
        # the record still produces on a tick its own delta does not apply (§5)
        keep = set.intersection(*[set(self.eff.get(l, ((), ()))[0]) for l in pblocks]) \
            if pblocks else set(sitewhen)
        when = tuple(t for t in sitewhen if t in keep)
        dwhen = tuple(t for t in sitewhen if t not in keep)
        delta = self.delta(a, blk)
        if delta is None:
            return None, set(), "T1's delta is no section 5 form"
        rec = {
            "rank": rank,
            "cell": self.cellname(a, _addr(a["cell"])),
            "target": a["target"]["register"],
            "width": a["width"],
            "delta": delta,
            "policy": self.policy(a, blk),
            "bound": {
                "from": "projected",
                "interval": [0, (1 << a["width"]) - 1],
                "witness": "the %d-bit store" % a["width"],
            },
            "rate": 1,
            "scope": a["scope"],
            "produce": produce,
        }
        ph = self.phase(a, blk)
        if ph is not None:
            rec["phase"] = ph
        rec["when"] = Rows(self.low, {}).when(when)
        if dwhen:
            rec["delta_when"] = Rows(self.low, {}).when(dwhen)
        drop = set(psites) | {int(s[1:], 16) for s in a["sites"]}
        for lbl in self.low.proc.blocks:
            if not self.under(lbl, sitewhen):
                continue
            for s in self.low.proc.blocks[lbl].stmts:
                if type(s) is Store and s.cls == "ram" and s.r in set(a["regions"]):
                    drop.add(s.src)
        return rec, drop, None






def _flagname(x):
    return "".join(c for c in (x or "C") if c.isalnum() or c == "_")


def _reload(low, addr):
    """The value a record's own cell is reloaded with, where its block reloads it."""
    vs = low.reach.get(low.lbl, {}).get(addr)
    if vs and len(vs) == 1:
        return next(iter(vs))
    return Load("ram", Const(addr, 2), 1, addr, addr, -1)


def _clean(low, node):
    """Whether an expression reads no name the object has no cell of its own for."""
    return not (_reads(node) & {c.lstrip("#") for c in low.temps.values()})
# ---- the lift ------------------------------------------------------------------
def _rename(cells, roles):
    """The player's own slots, bound to the addresses S6 and T2 name (§5)."""
    for name, addr in roles.items():
        if addr is not None:
            cells.rename[addr] = name


def _seed(obj, cells, img, keep):
    """``state0``: the post-init image at the cells the object still reads."""
    out = {}
    for name, base in sorted(cells.vcells.items()):
        if base is None or name not in keep or "." in name:
            continue
        n = 1 if name in cells.bcast else cells.voices
        vals = [int(img[base + (0 if name in cells.bcast else i * cells.stride)])
                for i in range(cells.voices)]
        del n
        out[name] = vals
    del obj
    return out


def _u16name(names, rid):
    """The name S6 gives the word a region is the low half of, where it names one."""
    for (lo, _hi), name in (names.u16 or {}).items():
        if lo[0] == rid:
            return name
    return None


def _scorecells(low, blocks, supplied):
    """``{site: (cell, address)}``: the row segment's stores of a byte the score read."""
    out = {}
    for lbl in blocks:
        low.lbl, low.local, low.pick = lbl, {}, {}
        for s in low.proc.blocks[lbl].stmts:
            if type(s) is not Store or s.cls != "ram":
                continue
            v = low.expand(s.v)
            if type(v) is not Var or v.n not in supplied:
                continue
            base, idx = addr_split(s.a)
            if base is None:
                continue
            try:
                got = low.v.target(low, s)
            except Unlowerable:
                continue
            if got is None or got[0] not in ("cell", "copy"):
                continue
            out[s.src] = (got[1] if got[0] == "cell" else "@" + got[1], base, v.n)
            del idx
    return out


class Binder:
    """One certified tune's planes, bound to the player's own slots (§4, §5)."""

    def __init__(self, art, ticks=None):
        from . import assemble, build, emit, record, region, schedule, tables
        from .cells import Cells
        from .lower import Lower
        from .vocab import Vocab
        from ..tuneprog.graph import rpo

        self.art, self.refusals = art, []
        view, names, t0 = art["view"], art["names"], art["t0"]
        prog, proc = art["prog"], art["prog"].meta["tick_proc"]
        p = prog.procs[proc]
        self.view, self.names, self.t0 = view, names, t0
        self.prog, self.proc, self.p = prog, proc, p
        fetch, self.refusals = region.fetch(prog, emit.tables_of(art["t2"], view, names))
        rowr = assemble._channels(prog, proc, fetch,
                                  emit.tables_of(art["t2"], view, names, ("pattern",)))
        self.rowfb = assemble._rowblocks(prog, proc, rowr)
        order = [l for l in rpo(p) if l in self.rowfb]
        assemble._need(order, "score not cursor-shaped", proc, "the tick reaches no fetch region")
        self.sch = sch = schedule.derive(prog, proc, self.rowfb, t0, order[0])
        assemble._need(sch.clock, "unclassified update", proc, "no row clock steps the voice loop")
        self.pit = tables.pitch_of(art, view, names)
        self.ins = tables.instrument_table(art, view, names)
        self.pwcols = tables.pw_columns(art, view, names)
        assemble._need(self.pit, "unclassified update", "pitch", "T2 materialised no tuning")
        assemble._need(self.ins, "command residue", "instruments",
                       "T2 found no instrument selector")
        pit = self.pit
        entry0 = tuple(b + pit.step * pit.base for b in pit.obases)
        self.cells = Cells(view, names, pitch=(pit.rids, entry0, pit.step, pit.n),
                           inspw=self.pwcols, words=tables.word_widths(prog, proc))
        self.voc = Vocab(self.cells, prog.reads(), build.registers(), sch.vidx)
        self.voc.pitch, self.voc.inspw = (pit.rids, pit.obases, pit.step, pit.n), self.pwcols
        self.voc.insbase, self.voc.inscol, self.voc.insstride = self.ins[0], self.ins[1], \
            self.ins[2]
        self.low = Lower(prog, proc, self.cells, self.voc)
        self.voc.notebase = tables.note_base(self.low, pit, [p])
        assemble._need(self.voc.notebase, "unclassified update", "note",
                       "no cell indexes the tuning")
        self.img = record.interp.Player(prog, region.Fetch()).run_init().m
        self.voc.img = self.img
        self.ticks = ticks or art["t2"]["horizon"]["ticks"]
        self.segs = {n: list(b) for n, b in sch.segments}

    def freqpair(self):
        """The per-voice pair a frequency accumulator moves: the player's ``freq``."""
        n = self.cells.voices
        for a in self.art["t1"].get("accs") or ():
            if a["width"] != 16 or int(a["cell"]["copies"]) != n:
                continue
            if (a["target"] or {}).get("register") not in ("freq", "freq_lo", "freq_hi"):
                continue
            lo = _addr(a["cell"])
            hi = next((r for r in a["regions"] if r != a["cell"]["region"]), None)
            if hi is None:
                continue
            return lo, self.view.by_id()[hi].base
        return None, None

    def copied(self, addr):
        """The per-voice cell a scalar the tick reads a role off is copied from.

        A family that stages its row moves the byte into a scratch the machine
        reads, so the cell the player's slot names is the copy's own source.
        """
        low, got = self.low, []
        for lbl, blk in low.proc.blocks.items():
            for s in blk.stmts:
                if type(s) is Store and s.cls == "ram" and addr_split(s.a)[0] == addr:
                    got.append((lbl, s))
        if len(got) != 1 or self.cells.at(addr) is not None:
            return addr
        low.lbl, low.local, low.pick, low.sub = got[0][0], {}, {}, {}
        e = low.expand(got[0][1].v)
        base, idx = addr_split(e.a) if type(e) is Load else (None, None)
        if base is not None and idx is not None and low.isvoice(idx):
            return base
        return addr

    def roles(self):
        """The player's own slots, bound to the cells S6, T1 and T2 name (§4, §5)."""
        from . import assemble

        sch, voc = self.sch, self.voc
        lo, hi = self.freqpair()
        self.orderbase = assemble._order_cursor(self.art, self.view, self.names)
        self.clockbase = sch.clock[3]
        voc.notebase = self.copied(voc.notebase)
        voc.insbase = self.copied(voc.insbase)
        # the clock is the player's ``rowsleft`` only where the row's own length
        # reloads it: a tune whose clock the tick keeps has no ``dur`` field
        rows = set(self.segs["row"])
        if not any(type(x) is Store and addr_split(x.a)[0] == self.clockbase
                   for l in rows for x in self.p.blocks[l].stmts):
            self.clockbase = None
        got = {"note": voc.notebase, "ins": voc.insbase, "rowsleft": self.clockbase,
               "orderpos": self.orderbase, "freq.lo": lo, "freq.hi": hi}
        _rename(self.cells, got)
        self.clockcell = ("rowsleft" if self.clockbase else
                          (self.cells.voicecell(sch.clock[3]) if sch.inloop
                           else self.cells.scalarcell(sch.clock[3])))
        self.slots = {k: v for k, v in got.items() if v is not None}
        voc.subst = {sch.clock[1].n: {"cell": "phase"}}
        drop = {sch.clock[2].src} | {st.src for st, _g in sch.resets}
        own = {v for k, v in self.slots.items() if k != "freq.lo" and k != "freq.hi"}
        for lbl in self.segs["row"]:
            for s in self.p.blocks[lbl].stmts:
                if type(s) is Store and addr_split(s.a)[0] in own:
                    drop.add(s.src)
        voc.dropstores = drop
        self.low.stated = frozenset(id(c) for c in sch.spent)

    def supplied(self):
        """The names no cell of the tune holds: the bytes a fetch read (the score's)."""
        from . import build
        from .lower import Lower

        blocks = sum(self.segs.values(), [])
        self.low.gate, self.low.scope, self.low.local = frozenset(), frozenset(), {}
        got = build._supplied(self.low, blocks)
        self.low = Lower(self.prog, self.proc, self.cells, self.voc)
        self.low.stated = frozenset(id(c) for c in self.sch.spent)
        self.voc.supplied = {n for n in got if n in self.low.defs or n in self.low.assigned}
        return self.voc.supplied

    def visits(self):
        """The horizon recorded over the fetch regions: one visit a row of a voice."""
        from . import build, record
        from ..tuneprog.graph import succs

        rowblocks = self.segs["row"]
        exits = sorted({s for l in rowblocks for s in succs(self.p.blocks[l].term)
                        if s not in rowblocks})
        exits = [e for e in exits if type(self.p.blocks[e].term).__name__ != "Trap"]
        inputs, bad = build.pinned_inputs(self.prog, self.img)
        vnames = sorted(self.sch.vidx)
        groups = [(rowblocks[0], rowblocks, exits)]
        R, fetches, trap, _obs = record.run(
            self.prog, self.proc, groups, self.ticks, inputs=inputs,
            envvars={(self.proc, rowblocks[0]): vnames})
        self.trips, self.inputs, self.badinputs, self.trap = dict(R.trips), inputs, bad, trap
        recs = fetches[(self.proc, rowblocks[0])]
        self.vvar = record.voice_name(recs, vnames, self.cells.voices, self.cells.stride)
        return recs

    def bind_fields(self, recs):
        """Section 3.6's event fields, and what a masked score byte is of them."""
        top = self.pit.base + self.pit.n
        roles = {"dur": self.clockbase or -1, "note": self.voc.notebase,
                 "ins": self.voc.insbase}
        seed = ([int(self.img[self.orderbase + v * self.cells.stride])
                 for v in range(self.cells.voices)] if self.orderbase else None)
        self.score = Score(recs, self.vvar, roles, self.cells.voices, self.cells.stride,
                           self.orderbase, top, seed)
        own = {self.clockbase, self.voc.notebase, self.voc.insbase, self.orderbase}
        self.sc = _scorecells(self.low, self.segs["row"], self.voc.supplied)
        # the byte the row's own fields are read off is the row and not a command:
        # a cell it lands in carries no datum the event's fields do not (§3.6)
        base, temps0 = self.score.facts()
        packed = {n for n, m in masks_of(self.low)
                  if n in temps0 and _same([None if v is None else v & (m or 0xFF)
                                            for v in temps0[n]], base["dur"])}
        self.packed = packed
        self.armcells = {k: v for k, v in self.sc.items()
                         if v[1] not in own and v[2] not in packed}
        for v in range(self.cells.voices):
            for r in self.score.rows[v]:
                r["sets"] = [[cell, r["sites"][src]]
                             for src, (cell, _base, _n) in sorted(self.armcells.items())
                             if src in r["sites"]]
        facts, temps = self.score.facts()
        got, left = fields_of(masks_of(self.low), facts, temps)
        self.tiemask, self.voc.fields = tie_of(got, left)
        rows = [r for v in range(self.cells.voices) for r in self.score.rows[v]]
        pairs = {(lbl, id(c)): (lbl, c) for lbl, gs in self.low.guards.items()
                 for _d, c, _t, _w in gs}
        self.voc.terms = terms_of(self.low, sorted(pairs.values(), key=lambda x: x[0]),
                                  facts, rows)
        self.left = [(n, m) for n, m, _v in left if (n, m) != self.tiemask]
        return self.voc.fields

    def tie(self, row):
        """One row's ``tie``: the field of the packed byte no other field explains."""
        if self.tiemask is None:
            return False
        return bool(row["temps"].get(self.tiemask[0], 0) & self.tiemask[1])

    def plan(self, order=()):
        """One guard plan over the segments: a join folds, or its paths raise a cell."""
        body = set(self.sch.body)
        groups = [self.segs.get("prelude", []), self.segs["row"], self.segs.get("machine", []),
                  [l for l in order if l not in body]]
        flags = self.low.planall(groups)
        if flags:
            from .refuse import Refusal

            self.refusals.append(Refusal("unclassified update", ",".join(flags), "",
                                         "a join no path folds"))
        return flags

    def steps(self, lbl, drop, roles, guard, extra, split=False):
        """One block as ordered steps: its role stores, its cells, its registers.

        The order is the block's own, with one exception the schema has no other
        channel for: an assignment whose value was *read* before a later store
        moved the cell it reads stands before that store, since a row's ``sets``
        run in the order they are written.  A value the row has since stored is
        read as the cell it left it in, for the same reason.
        """
        low, out, keep = self.low, [], []
        stmts = low.proc.blocks[lbl].stmts
        for i, s in enumerate(stmts):
            if type(s) is not Store:
                continue
            role = roles.get(s.src)
            if role is not None:
                keep.append((i, role, None, None))
                continue
            if s.src in drop:
                continue
            tgt = low.v.target(low, s)
            if tgt is not None:
                keep.append((i, "reg" if tgt[0] == "reg" else "set", tgt, s))
        put = {("@" if t[0] in ("copy", "acc") else "") + str(t[1]).lstrip("@")
               for _i, _k, t, _s in keep if t is not None}
        put = {x.lstrip("@#!*") for x in put}
        sub, got = {}, []
        for i, kind, tgt, s in _epoch(stmts, keep):
            if tgt is None:
                got.append((i, kind, None, None))
                continue
            low.sub = dict(sub)
            val = low.value(low.expand(s.v))
            low.sub = {}
            name = tgt[1] if tgt[0] not in ("copy", "acc") else "@" + str(tgt[1])
            hit = None
            if s.cls == "ram" and tgt[0] in ("cell", "acc"):
                nm = str(name).lstrip("@")
                node = {"global": nm[1:]} if nm[:1] == "#" else {"cell": nm}
                if _reads(val) & put:
                    sub[repr(low.expand(s.v))] = node
                    hit = (repr(low.expand(s.v)), node)
            got.append((i, kind, [name, val], hit))
        for _i, kind, pair, hit in _copies(low, got):
            key = kind if split or pair is None else "set"
            if out and out[-1][0] == key and key == "set" and pair is not None:
                out[-1][2].append(pair)
                out[-1][4].append(hit)
            else:
                out.append([key, guard + extra, None if pair is None else [pair], lbl, [hit]])
        return out

    def blockrows(self, blocks, order, drop, roles, split=False):
        """One segment as ordered steps in program order, split where a path binds."""
        low, R, out = self.low, Rows(self.low, self.amb), []
        for lbl in [l for l in order if l in blocks]:
            if not any(type(s) is Store for s in low.proc.blocks[lbl].stmts):
                continue
            guard = tuple(low.eff.get(lbl, ((), ()))[0])
            for extra, pick in R.bindings(lbl, guard, drop):
                low.pick = {n: self.amb[n][d] for n, d in pick.items()}
                low.lbl, low.local, low.turn, low.sub = lbl, {}, None, {}
                try:
                    got = self.steps(lbl, drop, roles, guard, extra, split)
                except Unlowerable as x:
                    low.bad.add("%s: %s" % (lbl, x))
                    continue
                out += got
        low.pick = {}
        return out

    def guards(self, steps, order):
        """Each step's guard, read where the staged order puts the row that carries it.

        A value an earlier row of the segment has since stored is that cell where
        the guard reads it, which is what keeps a row's own guard exact without a
        cell for the epoch.
        """
        low, out, sub = self.low, [], {}
        for kind, guard, pairs, lbl, subs in _staged(steps, order, self.guardfacts):
            low.sub = {}
            when, dec = [], set()
            R = Rows(low, {})
            for d, c, t in guard:
                if not low.onpath(d, c, t):
                    continue
                low.lbl, low.sub = d, dict(sub, **self.stored(d))
                fact = low.v.terms.get(repr(c))
                got = ([fact, "!=" if t else "==", 0] if fact is not None
                       else low.term(low.expand(c), t))
                if got not in when:
                    when.append(got)
                    dec.add(d)
            del R
            out.append((lbl, kind, when, pairs, frozenset(dec)))
            # a value the row itself takes the inputs of away: the cell it left it
            # in is where a later guard reads it, and no other value moved
            put = {x[0].lstrip("@#!*") for x in (pairs or ())}
            for x, pair in zip(subs, pairs or []):
                if x is not None and _reads(pair[1]) & put:
                    sub[x[0]] = x[1]
        low.sub = {}
        return out

    def stored(self, lbl):
        """``{a value the block stored: the cell it left it in}``, as the row reads it."""
        low, out = self.low, {}
        if lbl is None or lbl not in low.proc.blocks:
            return out
        low.lbl, low.sub = lbl, {}
        for s in low.proc.blocks[lbl].stmts:
            if type(s) is not Store or s.cls != "ram" or s.src in low.v.dropstores:
                continue
            base = addr_split(s.a)[0]
            # a counter alone: a value that is its own cell's is what a later row
            # has no older epoch of, and a copy is readable where it was copied from
            if base is None or not low.selfread(s.v, base):
                continue
            try:
                tgt = low.v.target(low, s)
            except Unlowerable:
                continue
            if tgt is None or tgt[0] != "cell":
                continue
            name = tgt[1]
            node = {"global": name[1:]} if name[:1] == "#" else {"cell": name.lstrip("@")}
            out[repr(low.expand(s.v))] = node
        return out

    def guardfacts(self, step):
        """``(the cells one step's guard reads, the blocks that decide it)``."""
        low, reads, dec = self.low, set(), set()
        for d, c, t in step[1]:
            if not low.onpath(d, c, t):
                continue
            low.lbl, low.sub = d, self.stored(d)
            try:
                reads |= _reads(low.term(low.expand(c), t))
            except Unlowerable:
                pass
            dec.add(d)
        low.sub = {}
        return reads, frozenset(dec)

    def run(self):  # noqa: C901 - one clause per section of the object
        """The bound object, and the report of what each plane supplied."""
        from . import assemble, build, record, tables
        from .refuse import Refusal

        self.roles()
        self.supplied()
        recs = self.visits()
        self.bind_fields(recs)
        self.amb = ambiguous(self.p)
        pro = record.firstonly(self.prog, self.proc, self.inputs)
        self.pro = pro if pro and not pro & set(self.sch.body) else frozenset()
        order = self.low.rpo
        self.plan(order)
        A = Accs(self.low, self.art, self.names, self.view)
        accs, drop, accat = {}, set(), {}
        for a in A.order(order):
            rec, d, why = A.record(a, 0)
            if rec is None:
                self.refusals.append(Refusal("unclassified update", a["cell"]["name"],
                                             ",".join(a["sites"]), why))
                continue
            nm = _u16name(self.names, a["cell"]["region"]) or a["id"]
            if rec["cell"].lstrip("#").startswith("c"):
                rec["cell"] = ("#" if rec["cell"][:1] == "#" else "") + nm
            accs[a["id"]] = rec
            accat[a["id"]] = min(order.index(l) for l in A.siteblocks(a))
            drop |= d
        self.accs, self.accat = accs, accat
        sc = self.sc
        roles = {}
        for lbl in self.segs["row"]:
            for s in self.p.blocks[lbl].stmts:
                if type(s) is not Store:
                    continue
                base = addr_split(s.a)[0]
                if base == self.voc.notebase:
                    roles[s.src] = "note"
                elif base == self.voc.insbase:
                    roles[s.src] = "ins"
                elif base in (self.clockbase, self.orderbase):
                    drop.add(s.src)
                elif s.src in sc:
                    roles[s.src] = "arm"

        return self.assemble(order, drop, roles, tables, build, assemble)

    def assemble(self, order, drop, roles, tables, build, asm):  # noqa: C901
        """The object: the phases, the row program, the score and the records."""
        segs, out = self.segs, _Out()
        low, sch = self.low, self.sch
        tick, pre = [], []
        low.gate, low.scope, low.v.payload = frozenset(), set(segs.get("prelude", [])), False
        for i, r in enumerate(_rows_of(self.guards(
                self.blockrows(set(segs.get("prelude", [])), order, drop, roles), order),
                ("set", "reg"))):
            pre.append(out.stream("prelude%d" % i, [r]))
        low.gate = frozenset((id(c), t) for c, t in sch.boundary)
        low.scope, low.v.payload = set(segs["row"]), True
        rowprog, ncmd, nst = [], 0, 0
        for _lbl, kind, when, sets, _d in self.guards(
                self.blockrows(set(segs["row"]), order, drop, roles, True), order):
            if kind == "note":
                rowprog.append({"note": True, **({"when": when} if when else {})})
            elif kind == "ins":
                rowprog.append({"ins": True})
            elif kind == "arm":
                if not ncmd:
                    rowprog.append({"commands": True})
                ncmd += 1
            elif kind == "reg":
                nm = out.stream("note_on%d" % nst,
                                [{"when": when, "sets": [list(x) for x in sets]}])
                rowprog.append({"stream": nm})
                nst += 1
            else:
                rowprog.append({"sets": [list(x) for x in sets],
                                **({"when": when} if when else {})})
        body = set(self.sch.body)
        glob = [l for l in order if l not in body and l not in self.pro]
        low.gate, low.scope, low.v.payload = frozenset(), set(glob), False
        low.gate, low.scope, low.v.payload = frozenset(), set(self.pro), False
        prol = _rows_of(self.guards(self.blockrows(set(self.pro), order, drop, roles), order),
                        ("set", "reg"))
        low.gate, low.scope, low.v.payload = frozenset(), set(glob), False
        gl = [out.stream("global%d" % i, [r]) for i, r in enumerate(
            _rows_of(self.guards(self.blockrows(set(glob), order, drop, roles), order),
                     ("set", "reg")))]
        low.gate, low.scope = frozenset(), set(segs.get("machine", []))
        low.v.payload = False
        items, rows = [], []
        for lbl, kind, when, sets, _d in self.guards(
                self.blockrows(set(segs.get("machine", [])), order, drop, roles), order):
            if kind in ("set", "reg"):
                rows.append((order.index(lbl), {"when": when,
                                                "sets": [list(x) for x in sets]}))
        accat = sorted(self.accat.items(), key=lambda kv: kv[1])
        rank, run, nm = 0, [], 0
        for at, row in rows:
            while accat and accat[0][1] <= at:
                if run:
                    out.stream("machine%d" % nm, run, rank)
                    nm, rank, run = nm + 1, rank + 1, []
                self.accs[accat[0][0]]["rank"] = rank
                rank, accat = rank + 1, accat[1:]
            run.append(row)
        if run:
            out.stream("machine%d" % nm, run, rank)
            rank += 1
        for key, _at in accat:
            self.accs[key]["rank"] = rank
            rank += 1
        return self.object(out, pre, rowprog, gl, prol, tables, build, asm)

    def object(self, out, pre, rowprog, gl, prol, tables, build, asm):  # noqa: C901
        """The sections of the object, each the plane that supplied it."""
        low, sch, art = self.low, self.sch, self.art
        rate = build.divider_rate(sch.divider[1], low, self.img) if sch.divider else 1
        phase = (build.divider_phase(self.img, sch.divider[0], rate - 1, rate)
                 if sch.divider and rate > 1 else 0)
        tick = []
        for name in sch.tick:
            tick += [{"stream": nm} for nm in pre] if name == "prelude" else [name]
        orders, pats = self.score.events(self.tie)
        self.armsets(pats, out)
        # the words a transposition of the object's own can reach, and the whole
        # region an instrument whose sound is no pitch reads its own from (§3.2)
        whole = self.trapped(tables.beyond_words(
            self.cells, low, self.pit, tables.beyond_limit(self.cells, low, self.pit)))
        words = whole[:max(_transposed(out.streams), 1)]
        for st in out.streams.values():
            st["beyond"] = {"id": "the fused tuning", "words": words}
        instruments = asm._instruments(art, self.view, self.names, self.ins, self.pwcols,
                                       self.img, self.accs)
        # a record no cell of the tune ever selects is no record of the object; a
        # score whose row states no instrument selects them all
        got = {r["ins"] for v in range(self.cells.voices) for r in self.score.rows[v]}
        if got - {None}:
            got |= {int(x) for x in self.img[self.voc.insbase:
                                             self.voc.insbase + self.cells.voices]}
            instruments = {k: v for k, v in instruments.items() if int(k) in got}
        self.pitched(instruments, whole)
        cellseed, globseed = self.cells.seed(self.img)
        obj = {
            "$trackerprog": 1,
            "meta": {
                "tune": self.prog.meta.get("name"),
                "song": self.prog.meta.get("song"),
                "family": "bound",
                "cycles_per_tick": self.prog.meta["entry"]["cycles_per_tick"],
                "voices": self.cells.voices,
                "horizon": self.ticks,
                "voice_order": build.voice_order(self.p, sch.head,
                                                 asm._latches(self.prog, self.proc, sch),
                                                 sch.vidx, self.cells.voices, self.cells.stride),
                "commit_order": list(sch.commit_order),
                "instrument": {},
                "tempo": {
                    "cell": self.clockcell,
                    "step": sch.step,
                    "rate": rate,
                    "phase": phase,
                    "boundary": [low.guard(c, t) for c, t in sch.boundary],
                    **asm._resets(low, self.clockcell, sch),
                },
                "tick": tick,
                "row_consumes_tick": sch.row_consumes_tick,
                "row_command": "spent",
                "row": rowprog,
                "wide": sorted(set(low.wide) | self.wide()),
            },
            "pitch": {"base": self.pit.base, "freq": list(art["t2"]["pitch"]["entries"])},
            "streams": {**out.streams, **build.table_streams(self.voc, self.img)},
            "accs": {k: v for k, v in sorted(self.accs.items(), key=lambda kv: kv[1]["rank"])},
            "instruments": instruments,
            "score": {"patterns": pats, "orders": orders},
            "globals": {**self.flags(), **({"streams": gl} if gl else {})},
            "state0": {"cells": cellseed, "globals": globseed,
                       **({"prologue": {"rows": prol}} if prol else {})},
        }
        _merge_halves(obj)
        _dce(obj)
        build.prune(obj)
        return obj

    def coverage(self, obj):
        """What each plane supplied, counted from the object the binding emitted."""
        rows = [r for st in obj["streams"].values() for r in st["rows"] if "sets" in r]
        sets = sum(len(r["sets"]) for r in rows)
        sets += sum(len(s.get("sets", ())) for s in obj["meta"]["row"])
        t1 = self.art["t1"].get("accs") or []
        return {
            "store_sites": sum(1 for b in self.p.blocks.values() for x in b.stmts
                               if type(x) is Store),
            "streams": len(obj["streams"]),
            "rows": len(rows) + len(obj["meta"]["row"]),
            "sets": sets,
            "accs": len(obj["accs"]),
            "t1_accumulators": len(t1),
            "t1_recognised": len(obj["accs"]),
            "cells": len(obj["state0"]["cells"]) + len(obj["state0"].get("globals", {})),
            "patterns": len(obj["score"]["patterns"]),
            "events": sum(len(p["events"]) for p in obj["score"]["patterns"].values()),
            "instruments": len(obj["instruments"]),
            "refused": sorted(self.low.bad),
        }

    def wide(self):
        """The voice cells the object reads as sixteen bits."""
        got = {a["cell"].lstrip("#") for a in self.accs.values() if a["width"] == 16}
        return {n for n in got if not n.startswith("ins.")}

    def flags(self):
        """Section 5's producer flags: the carry a repeated addition leaves."""
        out = {}
        for a in self.accs.values():
            for x in _flags(a.get("delta")):
                out[x] = {"default": {"const": 0}}
        for a in self.accs.values():
            if out and isinstance(a.get("delta"), dict) and "repeat" in a["delta"]:
                a["flag"] = {"name": sorted(out)[0], "seed": 0}
        return {"flags": out} if out else {}

    def armsets(self, pats, out):
        """The cells the score's own bytes reach: one command a row, over named cells."""
        live = set()
        for st in out.streams.values():
            for r in st["rows"]:
                live |= _reads(r.get("when", [])) | _reads([x[1] for x in r["sets"]])
        for a in self.accs.values():
            live |= _reads(list(a.values()))
        for pat in pats.values():
            for e in pat["events"]:
                if e["arm"] is None:
                    continue
                got = [s for s in e["arm"]["rows"][0]["sets"] if s[0].lstrip("@#") in live]
                e["arm"] = {"rows": [{"sets": got}]} if got else None

    def trapped(self, words):
        """A word past the tuning the score's own byte holds: no cell of the object.

        The packed row byte is the event's own fields (§3.6), so the object has no
        cell for it and the word that would read one is a ``trap``.
        """
        names = {self.sc[src][0].lstrip("@#") for src in self.sc
                 if self.sc[src][2] in getattr(self, "packed", ())}
        out = []
        for w in words:
            hit = [h for h in w.get("u16", ()) if isinstance(h, dict)
                   and (h.get("cell") or [""])[0] in names]
            out.append({"trap": "the packed row byte, which the score keeps as an "
                                "event's own fields"} if hit else w)
        return out

    def pitched(self, instruments, words):
        """An instrument whose sound the tuning has no note for: its own pitch (§3.5)."""
        top = self.pit.base + self.pit.n
        cur, want = {}, set()
        for v in range(self.cells.voices):
            for r in self.score.rows[v]:
                if r["ins"] is not None:
                    cur[v] = r["ins"]
                if r["note"] is not None and r["note"] >= top and v in cur:
                    want.add((cur[v], r["note"] - top))
        for key, d in sorted(want):
            rec = instruments.get(str(key))
            if rec is None or d >= len(words) or "trap" in words[d]:
                continue
            rec["pitch"] = {"value": words[d]}
            if d + 12 < len(words) and "trap" not in words[d + 12]:
                rec["pitch"]["octave"] = words[d + 12]


def _flags(node):
    """The flag names one expression reads: section 5's one carry channel."""
    out, stack = set(), [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "flag":
                    out.add(v)
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _merge_halves(obj):
    """A cell the object names by its halves, seeded as the one word it is."""
    got = obj["state0"]["cells"]
    for name in [n for n in got if n.endswith(".lo")]:
        base = name[:-3]
        hi = got.pop(base + ".hi", None)
        lo = got.pop(name)
        got[base] = [a | (b << 8 if hi else 0) for a, b in zip(lo, hi or lo)]
    return obj


def _reads(node):
    """Every cell one expression reads, by name."""
    out, stack = set(), [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "cell":
                    out.add(v if isinstance(v, str) else v[0])
                elif k == "global":
                    out.add(v)
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _ordered(sets):
    """One row's assignments, a read of a cell the row writes kept before the write."""
    out = list(sets)
    for _ in range(len(out)):
        moved = False
        for i, (t, _v) in enumerate(out):
            name = t.lstrip("@#!*")
            for j in range(i + 1, len(out)):
                if name in _reads(out[j][1]) or name.split(".")[0] in _reads(out[j][1]):
                    out.insert(i, out.pop(j))
                    moved = True
                    break
            if moved:
                break
        if not moved:
            break
    return out


class _Out:
    """The object under construction: its streams, its accumulators and their ranks."""

    def __init__(self):
        self.streams, self.accs, self.items = {}, {}, []

    def stream(self, name, rows, rank=None):
        st = {"rows": rows, "all": True}
        if rank is not None:
            st["rank"] = rank
        self.streams[name] = st
        return name


def _rows_of(steps, kinds):
    """Consecutive steps of one kind as guarded rows of one stream."""
    out = []
    for _lbl, kind, when, sets, _d in steps:
        if kind not in kinds:
            continue
        out.append({"when": when, "sets": [list(x) for x in sets]})
    return out

def _epoch(stmts, got):
    """One block's stores in an order a row's own ``sets`` can be run in.

    A store whose value was *read* before a later store moved the cell it reads
    stands before that store: the IR names the read where it happened, and a row
    has no channel for a value read one statement earlier.
    """
    pos = {s.n: i for i, s in enumerate(stmts) if type(s) is Let}
    out = list(got)
    for _ in range(len(out) * len(out) + 1):
        for a, x in enumerate(out):
            i = x[0]
            b = next((k for k in range(a) if _before(stmts, i, out[k][0], pos)), None)
            if b is not None:
                out.insert(b, out.pop(a))
                break
        else:
            return out
    return out


def _deps(stmts, i, pos):
    """``{address: the statement its value was read at}`` for one store's value."""
    out, seen, stack = {}, set(), [(stmts[i].v, i)]
    while stack:
        e, at = stack.pop()
        for x in walk(e):
            if type(x) is Load:
                b = addr_split(x.a)[0]
                if b is not None:
                    out[b] = min(out.get(b, at), at)
            elif type(x) is Var and x.n in pos and x.n not in seen:
                seen.add(x.n)
                stack.append((stmts[pos[x.n]].e, pos[x.n]))
    return out


def _before(stmts, i, j, pos):
    """Whether store ``i`` must stand before store ``j`` in one row's ``sets``."""
    if type(stmts[j]) is not Store or stmts[j].cls == "io":
        return False
    base = addr_split(stmts[j].a)[0]
    if base is None:
        return False
    got = _deps(stmts, i, pos).get(base)
    return got is not None and got < j


def _carried(low, c):
    """Whether a guard term reads a name more than one block of the tick binds."""
    low.lbl, low.local, low.pick = None, {}, {}
    return any(type(x) is Var and x.n not in low.defs and x.n not in low.v.vidx
               for x in walk(low.expand(c)))


def _copies(low, got):
    """Fold the copies of one per-voice cell a block writes at constant addresses.

    A value every copy takes is one write every voice makes (§3.6's ``all``); a
    copy that is neither the committing voice's nor one of a full set is no cell.
    """
    at = {}
    for k, x in enumerate(got):
        pair = x[2]
        if pair is not None and isinstance(pair[0], tuple):
            at.setdefault(pair[0][0], []).append(k)
    out, drop = list(got), set()
    for name, ks in at.items():
        vals = {got[k][2][0][1]: repr(got[k][2][1]) for k in ks}
        full = len(vals) == low.cells.voices and len(set(vals.values())) == 1
        for j, k in enumerate(ks):
            put = ("*" if full else "@") + name
            if full and j:
                drop.add(k)
                continue
            if not full and got[k][2][0][1]:
                drop.add(k)
                low.bad.add(name)
                continue
            sub = got[k][3]
            node = {"cell": name}
            out[k] = (got[k][0], got[k][1], [put, got[k][2][1]],
                      None if sub is None else (sub[0], node))
    return [x for k, x in enumerate(out) if k not in drop]


KEEP = ("rowsleft", "dur", "note", "ins", "freq", "orderpos", "tied", "phase", "counter",
        "voice_index", "lastnote", "wave")


def _tables(obj):
    """The declared streams a ``tabcell`` reads: one row a byte of a const table."""
    out, stack = set(), [obj["streams"], obj["accs"], obj["meta"], obj["instruments"]]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "tabcell":
                    out.add(v[0])
                stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _needed(target):
    """Whether one ``sets`` target is a register the chip has, and not a cell."""
    return target[:1] not in "@#!*"


def _dce(obj):
    """Drop the assignments whose cell nothing the object states reads.

    Liveness and not a count of readers: a cell two dead rows pass between them
    is dead, so the live set grows from the roots -- the registers, the records,
    the score and the words past the tuning -- through the rows that write them.
    """
    rows = [r for st in obj["streams"].values() for r in st["rows"] if "sets" in r]
    rows += [s for s in obj["meta"]["row"] if "sets" in s]
    nodes = [(r, k) for r in rows for k in range(len(r["sets"]))]
    live = set(KEEP) | {a["cell"].lstrip("#").split(".")[0] for a in obj["accs"].values()}
    for part in ("accs", "score", "globals", "instruments"):
        live |= _reads(obj[part])
    live |= _reads(obj["meta"]["tempo"]) | _reads([s.get("when") for s in obj["meta"]["row"]])
    live |= _reads([st.get("beyond") for st in obj["streams"].values()])
    keep = set()
    for _ in range(len(nodes) + 1):
        more = set()
        for r, k in nodes:
            if (id(r), k) in keep:
                continue
            t = r["sets"][k][0]
            if _needed(t) or t.lstrip("@#!*").split(".")[0] in live:
                more.add((id(r), k))
        if not more:
            break
        keep |= more
        for r, k in nodes:
            if (id(r), k) in keep:
                live |= _reads(r["sets"][k][1]) | _reads(r.get("when", []))
    for r in rows:
        r["sets"] = [x for k, x in enumerate(r["sets"]) if (id(r), k) in keep]
    for st in obj["streams"].values():
        st["rows"] = [r for r in st["rows"] if r.get("sets") or "sets" not in r]
    obj["meta"]["row"] = [s for s in obj["meta"]["row"] if "sets" not in s or s["sets"]]
    named = {s["stream"] for s in obj["meta"]["row"] if "stream" in s}
    named |= {e["stream"] for e in obj["meta"]["tick"] if not isinstance(e, str)}
    named |= set(obj["globals"].get("streams", ()))
    named |= {n for n in obj["streams"] if _tables(obj) & {n}}
    obj["streams"] = {k: v for k, v in obj["streams"].items()
                      if v["rows"] and (k in named or "rank" in v)}
    obj["meta"]["row"] = [s for s in obj["meta"]["row"]
                          if "stream" not in s or s["stream"] in obj["streams"]]
    obj["meta"]["tick"] = [e for e in obj["meta"]["tick"]
                           if isinstance(e, str) or e["stream"] in obj["streams"]]
    obj["globals"]["streams"] = [k for k in obj["globals"].get("streams", ())
                                 if k in obj["streams"]]
    if not obj["globals"]["streams"]:
        del obj["globals"]["streams"]
    return obj


def _transposed(streams):
    """How far past the tuning a transposition of the object's own can reach (§3.2)."""
    got = [0]
    for st in streams.values():
        for r in st["rows"]:
            stack = [r.get("when", []), [x[1] for x in r["sets"]]]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    for k, v in x.items():
                        got.append(v) if k == "transpose" and isinstance(v, int) else stack.append(v)
                elif isinstance(x, (list, tuple)):
                    stack += list(x)
    return max(got)


def _ev(e, env):
    """One condition over a byte the score supplied, evaluated; ``None`` where it reads more."""
    from ..tuneprog.ir import evalbin

    t = type(e)
    if t is Const:
        return e.v
    if t is Var:
        return env.get(e.n)
    if t is Bin:
        a, b = _ev(e.a, env), _ev(e.b, env)
        return None if a is None or b is None else evalbin(e.op, a, b, e.w or 1)
    return None


def terms_of(low, guards, facts, rows):
    """``{a guard the score's own byte decides: the row fact it is}`` (§3.6).

    A term whose only input is a byte a fetch read is a fact of the row, and
    which fact is decided by what the horizon's own visits say -- not by a
    reading of what the byte means.
    """
    out = {}
    for lbl, c in guards:
        low.lbl, low.local, low.pick, low.sub = lbl, {}, {}, {}
        e = low.expand(c)
        names = {x.n for x in walk(e) if type(x) is Var}
        if len(names) != 1 or not names <= set(low.v.supplied):
            continue
        got = [_ev(e, r["temps"]) for r in rows]
        if any(v is None for v in got):
            continue
        for key in ("wraps", "sounds", "newins", "field"):
            if _truthy(got, facts[key]):
                out[repr(c)] = key
                break
            if _truthy(got, [1 - x for x in facts[key]]):
                out[repr(c)] = {"xor": [key, 1]}
                break
    return out


def _staged(steps, order, facts):
    """One segment's rows in an order their own guards can be read in.

    A guard the tick decided before a store of the segment is read at the row it
    guards, so a row whose guard reads a cell an earlier row writes -- and whose
    guard was decided before that row's own block -- stands before it.
    """
    at = {l: i for i, l in enumerate(order)}
    out = list(steps)
    for _ in range(len(out) * len(out) + 1):
        for j, step in enumerate(out):
            reads, dec = facts(step)
            if not reads or not dec:
                continue
            i = next((i for i in range(j)
                      if out[i][2] and reads & {x[0].lstrip("@#!*") for x in out[i][2]}
                      and out[i][3] not in dec
                      and max(at.get(d, 0) for d in dec) < at.get(out[i][3], 0)
                      and at.get(out[i][3], 0) <= at.get(step[3], 0)), None)
            if i is not None:
                out.insert(i, out.pop(j))
                break
        else:
            return out
    return out


def _epoch(stmts, got):
    """One block's stores in an order a row's own ``sets`` can be run in.

    A store whose value was *read* before a later store moved the cell it reads
    stands before that store: the IR names the read where it happened, and a row
    has no channel for a value read one statement earlier.
    """
    pos = {s.n: i for i, s in enumerate(stmts) if type(s) is Let}
    out = list(got)
    for _ in range(len(out) * len(out) + 1):
        for a, x in enumerate(out):
            i = x[0]
            b = next((k for k in range(a) if _before(stmts, i, out[k][0], pos)), None)
            if b is not None:
                out.insert(b, out.pop(a))
                break
        else:
            return out
    return out


def _deps(stmts, i, pos):
    """``{address: the statement its value was read at}`` for one store's value."""
    out, seen, stack = {}, set(), [(stmts[i].v, i)]
    while stack:
        e, at = stack.pop()
        for x in walk(e):
            if type(x) is Load:
                b = addr_split(x.a)[0]
                if b is not None:
                    out[b] = min(out.get(b, at), at)
            elif type(x) is Var and x.n in pos and x.n not in seen:
                seen.add(x.n)
                stack.append((stmts[pos[x.n]].e, pos[x.n]))
    return out


def _before(stmts, i, j, pos):
    """Whether store ``i`` must stand before store ``j`` in one row's ``sets``."""
    if type(stmts[j]) is not Store or stmts[j].cls == "io":
        return False
    base = addr_split(stmts[j].a)[0]
    if base is None:
        return False
    got = _deps(stmts, i, pos).get(base)
    return got is not None and got < j


def _carried(low, c):
    """Whether a guard term reads a name more than one block of the tick binds."""
    low.lbl, low.local, low.pick = None, {}, {}
    return any(type(x) is Var and x.n not in low.defs and x.n not in low.v.vidx
               for x in walk(low.expand(c)))


def _copies(low, got):
    """Fold the copies of one per-voice cell a block writes at constant addresses.

    A value every copy takes is one write every voice makes (§3.6's ``all``); a
    copy that is neither the committing voice's nor one of a full set is no cell.
    """
    at = {}
    for k, x in enumerate(got):
        pair = x[2]
        if pair is not None and isinstance(pair[0], tuple):
            at.setdefault(pair[0][0], []).append(k)
    out, drop = list(got), set()
    for name, ks in at.items():
        vals = {got[k][2][0][1]: repr(got[k][2][1]) for k in ks}
        full = len(vals) == low.cells.voices and len(set(vals.values())) == 1
        for j, k in enumerate(ks):
            put = ("*" if full else "@") + name
            if full and j:
                drop.add(k)
                continue
            if not full and got[k][2][0][1]:
                drop.add(k)
                low.bad.add(name)
                continue
            sub = got[k][3]
            node = {"cell": name}
            out[k] = (got[k][0], got[k][1], [put, got[k][2][1]],
                      None if sub is None else (sub[0], node))
    return [x for k, x in enumerate(out) if k not in drop]


KEEP = ("rowsleft", "dur", "note", "ins", "freq", "orderpos", "tied", "phase", "counter",
        "voice_index", "lastnote", "wave")


def _tables(obj):
    """The declared streams a ``tabcell`` reads: one row a byte of a const table."""
    out, stack = set(), [obj["streams"], obj["accs"], obj["meta"], obj["instruments"]]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "tabcell":
                    out.add(v[0])
                stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _needed(target):
    """Whether one ``sets`` target is a register the chip has, and not a cell."""
    return target[:1] not in "@#!*"


def _dce(obj):
    """Drop the assignments whose cell nothing the object states reads.

    Liveness and not a count of readers: a cell two dead rows pass between them
    is dead, so the live set grows from the roots -- the registers, the records,
    the score and the words past the tuning -- through the rows that write them.
    """
    rows = [r for st in obj["streams"].values() for r in st["rows"] if "sets" in r]
    rows += [s for s in obj["meta"]["row"] if "sets" in s]
    nodes = [(r, k) for r in rows for k in range(len(r["sets"]))]
    live = set(KEEP) | {a["cell"].lstrip("#").split(".")[0] for a in obj["accs"].values()}
    for part in ("accs", "score", "globals", "instruments"):
        live |= _reads(obj[part])
    live |= _reads(obj["meta"]["tempo"]) | _reads([s.get("when") for s in obj["meta"]["row"]])
    live |= _reads([st.get("beyond") for st in obj["streams"].values()])
    keep = set()
    for _ in range(len(nodes) + 1):
        more = set()
        for r, k in nodes:
            if (id(r), k) in keep:
                continue
            t = r["sets"][k][0]
            if _needed(t) or t.lstrip("@#!*").split(".")[0] in live:
                more.add((id(r), k))
        if not more:
            break
        keep |= more
        for r, k in nodes:
            if (id(r), k) in keep:
                live |= _reads(r["sets"][k][1]) | _reads(r.get("when", []))
    for r in rows:
        r["sets"] = [x for k, x in enumerate(r["sets"]) if (id(r), k) in keep]
    for st in obj["streams"].values():
        st["rows"] = [r for r in st["rows"] if r.get("sets") or "sets" not in r]
    obj["meta"]["row"] = [s for s in obj["meta"]["row"] if "sets" not in s or s["sets"]]
    named = {s["stream"] for s in obj["meta"]["row"] if "stream" in s}
    named |= {e["stream"] for e in obj["meta"]["tick"] if not isinstance(e, str)}
    named |= set(obj["globals"].get("streams", ()))
    named |= {n for n in obj["streams"] if _tables(obj) & {n}}
    obj["streams"] = {k: v for k, v in obj["streams"].items()
                      if v["rows"] and (k in named or "rank" in v)}
    obj["meta"]["row"] = [s for s in obj["meta"]["row"]
                          if "stream" not in s or s["stream"] in obj["streams"]]
    obj["meta"]["tick"] = [e for e in obj["meta"]["tick"]
                           if isinstance(e, str) or e["stream"] in obj["streams"]]
    obj["globals"]["streams"] = [k for k in obj["globals"].get("streams", ())
                                 if k in obj["streams"]]
    if not obj["globals"]["streams"]:
        del obj["globals"]["streams"]
    return obj


def _transposed(streams):
    """How far past the tuning a transposition of the object's own can reach (§3.2)."""
    got = [0]
    for st in streams.values():
        for r in st["rows"]:
            stack = [r.get("when", []), [x[1] for x in r["sets"]]]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    for k, v in x.items():
                        got.append(v) if k == "transpose" and isinstance(v, int) else stack.append(v)
                elif isinstance(x, (list, tuple)):
                    stack += list(x)
    return max(got)


def _ev(e, env):
    """One condition over a byte the score supplied, evaluated; ``None`` where it reads more."""
    from ..tuneprog.ir import evalbin

    t = type(e)
    if t is Const:
        return e.v
    if t is Var:
        return env.get(e.n)
    if t is Bin:
        a, b = _ev(e.a, env), _ev(e.b, env)
        return None if a is None or b is None else evalbin(e.op, a, b, e.w or 1)
    return None


def terms_of(low, guards, facts, rows):
    """``{a guard the score's own byte decides: the row fact it is}`` (§3.6).

    A term whose only input is a byte a fetch read is a fact of the row, and
    which fact is decided by what the horizon's own visits say -- not by a
    reading of what the byte means.
    """
    out = {}
    for lbl, c in guards:
        low.lbl, low.local, low.pick, low.sub = lbl, {}, {}, {}
        e = low.expand(c)
        names = {x.n for x in walk(e) if type(x) is Var}
        if len(names) != 1 or not names <= set(low.v.supplied):
            continue
        got = [_ev(e, r["temps"]) for r in rows]
        if any(v is None for v in got):
            continue
        for key in ("wraps", "sounds", "newins", "field"):
            if _truthy(got, facts[key]):
                out[repr(c)] = key
                break
            if _truthy(got, [1 - x for x in facts[key]]):
                out[repr(c)] = {"xor": [key, 1]}
                break
    return out


def _stale(out, j, dec, at):
    """The row a guard of row ``j`` would be read one store too late after."""
    step = out[j]
    reads = _reads([c for _d, c, _t in step[1]])
    del reads
    return None


def _epoch(stmts, got):
    """One block's stores in an order a row's own ``sets`` can be run in.

    A store whose value was *read* before a later store moved the cell it reads
    stands before that store: the IR names the read where it happened, and a row
    has no channel for a value read one statement earlier.
    """
    pos = {s.n: i for i, s in enumerate(stmts) if type(s) is Let}
    out = list(got)
    for _ in range(len(out) * len(out) + 1):
        for a, x in enumerate(out):
            i = x[0]
            b = next((k for k in range(a) if _before(stmts, i, out[k][0], pos)), None)
            if b is not None:
                out.insert(b, out.pop(a))
                break
        else:
            return out
    return out


def _deps(stmts, i, pos):
    """``{address: the statement its value was read at}`` for one store's value."""
    out, seen, stack = {}, set(), [(stmts[i].v, i)]
    while stack:
        e, at = stack.pop()
        for x in walk(e):
            if type(x) is Load:
                b = addr_split(x.a)[0]
                if b is not None:
                    out[b] = min(out.get(b, at), at)
            elif type(x) is Var and x.n in pos and x.n not in seen:
                seen.add(x.n)
                stack.append((stmts[pos[x.n]].e, pos[x.n]))
    return out


def _before(stmts, i, j, pos):
    """Whether store ``i`` must stand before store ``j`` in one row's ``sets``."""
    if type(stmts[j]) is not Store or stmts[j].cls == "io":
        return False
    base = addr_split(stmts[j].a)[0]
    if base is None:
        return False
    got = _deps(stmts, i, pos).get(base)
    return got is not None and got < j


def _carried(low, c):
    """Whether a guard term reads a name more than one block of the tick binds."""
    low.lbl, low.local, low.pick = None, {}, {}
    return any(type(x) is Var and x.n not in low.defs and x.n not in low.v.vidx
               for x in walk(low.expand(c)))


def _copies(low, got):
    """Fold the copies of one per-voice cell a block writes at constant addresses.

    A value every copy takes is one write every voice makes (§3.6's ``all``); a
    copy that is neither the committing voice's nor one of a full set is no cell.
    """
    at = {}
    for k, x in enumerate(got):
        pair = x[2]
        if pair is not None and isinstance(pair[0], tuple):
            at.setdefault(pair[0][0], []).append(k)
    out, drop = list(got), set()
    for name, ks in at.items():
        vals = {got[k][2][0][1]: repr(got[k][2][1]) for k in ks}
        full = len(vals) == low.cells.voices and len(set(vals.values())) == 1
        for j, k in enumerate(ks):
            put = ("*" if full else "@") + name
            if full and j:
                drop.add(k)
                continue
            if not full and got[k][2][0][1]:
                drop.add(k)
                low.bad.add(name)
                continue
            sub = got[k][3]
            node = {"cell": name}
            out[k] = (got[k][0], got[k][1], [put, got[k][2][1]],
                      None if sub is None else (sub[0], node))
    return [x for k, x in enumerate(out) if k not in drop]


KEEP = ("rowsleft", "dur", "note", "ins", "freq", "orderpos", "tied", "phase", "counter",
        "voice_index", "lastnote", "wave")


def _tables(obj):
    """The declared streams a ``tabcell`` reads: one row a byte of a const table."""
    out, stack = set(), [obj["streams"], obj["accs"], obj["meta"], obj["instruments"]]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "tabcell":
                    out.add(v[0])
                stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _needed(target):
    """Whether one ``sets`` target is a register the chip has, and not a cell."""
    return target[:1] not in "@#!*"


def _dce(obj):
    """Drop the assignments whose cell nothing the object states reads.

    Liveness and not a count of readers: a cell two dead rows pass between them
    is dead, so the live set grows from the roots -- the registers, the records,
    the score and the words past the tuning -- through the rows that write them.
    """
    rows = [r for st in obj["streams"].values() for r in st["rows"] if "sets" in r]
    rows += [s for s in obj["meta"]["row"] if "sets" in s]
    nodes = [(r, k) for r in rows for k in range(len(r["sets"]))]
    live = set(KEEP) | {a["cell"].lstrip("#").split(".")[0] for a in obj["accs"].values()}
    for part in ("accs", "score", "globals", "instruments"):
        live |= _reads(obj[part])
    live |= _reads(obj["meta"]["tempo"]) | _reads([s.get("when") for s in obj["meta"]["row"]])
    live |= _reads([st.get("beyond") for st in obj["streams"].values()])
    keep = set()
    for _ in range(len(nodes) + 1):
        more = set()
        for r, k in nodes:
            if (id(r), k) in keep:
                continue
            t = r["sets"][k][0]
            if _needed(t) or t.lstrip("@#!*").split(".")[0] in live:
                more.add((id(r), k))
        if not more:
            break
        keep |= more
        for r, k in nodes:
            if (id(r), k) in keep:
                live |= _reads(r["sets"][k][1]) | _reads(r.get("when", []))
    for r in rows:
        r["sets"] = [x for k, x in enumerate(r["sets"]) if (id(r), k) in keep]
    for st in obj["streams"].values():
        st["rows"] = [r for r in st["rows"] if r.get("sets") or "sets" not in r]
    obj["meta"]["row"] = [s for s in obj["meta"]["row"] if "sets" not in s or s["sets"]]
    named = {s["stream"] for s in obj["meta"]["row"] if "stream" in s}
    named |= {e["stream"] for e in obj["meta"]["tick"] if not isinstance(e, str)}
    named |= set(obj["globals"].get("streams", ()))
    named |= {n for n in obj["streams"] if _tables(obj) & {n}}
    obj["streams"] = {k: v for k, v in obj["streams"].items()
                      if v["rows"] and (k in named or "rank" in v)}
    obj["meta"]["row"] = [s for s in obj["meta"]["row"]
                          if "stream" not in s or s["stream"] in obj["streams"]]
    obj["meta"]["tick"] = [e for e in obj["meta"]["tick"]
                           if isinstance(e, str) or e["stream"] in obj["streams"]]
    obj["globals"]["streams"] = [k for k in obj["globals"].get("streams", ())
                                 if k in obj["streams"]]
    if not obj["globals"]["streams"]:
        del obj["globals"]["streams"]
    return obj


def _transposed(streams):
    """How far past the tuning a transposition of the object's own can reach (§3.2)."""
    got = [0]
    for st in streams.values():
        for r in st["rows"]:
            stack = [r.get("when", []), [x[1] for x in r["sets"]]]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    for k, v in x.items():
                        got.append(v) if k == "transpose" and isinstance(v, int) else stack.append(v)
                elif isinstance(x, (list, tuple)):
                    stack += list(x)
    return max(got)


def _ev(e, env):
    """One condition over a byte the score supplied, evaluated; ``None`` where it reads more."""
    from ..tuneprog.ir import evalbin

    t = type(e)
    if t is Const:
        return e.v
    if t is Var:
        return env.get(e.n)
    if t is Bin:
        a, b = _ev(e.a, env), _ev(e.b, env)
        return None if a is None or b is None else evalbin(e.op, a, b, e.w or 1)
    return None


def terms_of(low, guards, facts, rows):
    """``{a guard the score's own byte decides: the row fact it is}`` (§3.6).

    A term whose only input is a byte a fetch read is a fact of the row, and
    which fact is decided by what the horizon's own visits say -- not by a
    reading of what the byte means.
    """
    out = {}
    for lbl, c in guards:
        low.lbl, low.local, low.pick, low.sub = lbl, {}, {}, {}
        e = low.expand(c)
        names = {x.n for x in walk(e) if type(x) is Var}
        if len(names) != 1 or not names <= set(low.v.supplied):
            continue
        got = [_ev(e, r["temps"]) for r in rows]
        if any(v is None for v in got):
            continue
        for key in ("wraps", "sounds", "newins", "field"):
            if _truthy(got, facts[key]):
                out[repr(c)] = key
                break
            if _truthy(got, [1 - x for x in facts[key]]):
                out[repr(c)] = {"xor": [key, 1]}
                break
    return out



def lift(art, ticks=None, hints=None):
    """``(object, report)``: one certified tune's planes, bound to the player."""
    b = Binder(art, ticks)
    obj = b.run()
    for k, v in (hints or {}).items():
        node, parts = obj, k.split(".")
        for x in parts[:-1]:
            node = node[x]
        node[parts[-1]] = v
    report = {
        "schedule": b.sch.datums(),
        "refusals": [r.to_dict() for r in b.refusals],
        "coverage": b.coverage(obj),
        "trips": b.trips,
        "rows": sum(len(s["rows"]) for s in obj["streams"].values()),
        "accs": len(obj["accs"]),
        "patterns": len(obj["score"]["patterns"]),
    }
    return obj, report
