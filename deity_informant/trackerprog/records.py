"""B7 -- T1's accumulator records, rendered into section 5's, over the object's cells.

Each field of the record is read where the tune's own site reads it: the delta
over a named cell or an instrument column, the reload where the reload stands,
the produce from the T0 writes the record's own cells reach, and the guard split
into what the record runs under and what its delta alone applies under.
"""

from __future__ import annotations

from ..tuneprog.ir import Bin, Const, Load, Store
from ..tuneprog.irwalk import addr_split, walk
from .rows import Rows, _carried
from .shape import _reads, _u16name

MASK8 = 0xFF


def _addr(ref):
    return None if ref is None else int(ref["addr"][1:], 16)


def _load(low, addr, w=1):
    """One cell read as the tick reads it: the byte at a constant address."""
    return low.expand(Load("ram", Const(addr, 2), w, addr, addr, -1))


def _halving(s, bases):
    """Whether one store halves the word a pair of bases holds."""
    if type(s) is not Store or addr_split(s.a)[0] not in bases:
        return False
    return any(
        type(x) is Bin and x.op == ">>" and type(x.b) is Const and x.b.v == 1 for x in walk(s.v)
    )


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
        self.t1 = list(art["t1"].get("accs") or [])
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
        return [
            l
            for l, b in self.low.proc.blocks.items()
            if any(type(s) is Store and s.src in want for s in b.stmts)
        ]

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
            for addr in (
                [lo]
                if a["width"] <= 8
                else [
                    lo,
                    next(
                        (
                            self.view.by_id()[r].base
                            for r in a["regions"]
                            if r != a["cell"]["region"]
                        ),
                        lo + 1,
                    ),
                ]
            ):
                hit = got.get(addr)
                if hit is None:
                    halves = []
                    break
                low.lbl, low.local, low.pick = hit[0], {}, {}
                halves.append(low.value(low.expand(hit[1])))
            if len(halves) == 1:
                return {"reload": halves[0]}
            if halves:
                return {"reload": _unsplit(*halves) or {"or": [halves[0], {"shl": [halves[1], 8]}]}}
        low.lbl, low.local, low.pick = blk, {}, {}
        if a["width"] <= 8:
            return {"reload": low.value(low.expand(_reload(low, lo)))}
        hi = next(
            (self.view.by_id()[r].base for r in a["regions"] if r != a["cell"]["region"]), lo + 1
        )
        halves = [low.value(low.expand(_reload(low, x))) for x in (lo, hi)]
        return {"reload": _unsplit(*halves) or {"or": [halves[0], {"shl": [halves[1], 8]}]}}

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
        blocks = sorted(self.siteblocks(a), key=self.low.rpo.index)
        blk = blocks[0]
        produce, psites, pblocks = self.produce(a, sitewhen)
        # a term the step stands under and the produce does not is `delta_when`:
        # the record still produces on a tick its own delta does not apply (§5)
        keep = (
            set.intersection(*[set(self.eff.get(l, ((), ()))[0]) for l in pblocks])
            if pblocks
            else set(sitewhen)
        )
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


def _unsplit(lo, hi):
    """``(M8(E), M8(E >> 8))`` as ``E``: one word stated once and not twice."""
    if not (isinstance(lo, dict) and "and" in lo and lo["and"][1] == MASK8):
        return None
    if not (isinstance(hi, dict) and "and" in hi and hi["and"][1] == MASK8):
        return None
    a, b = lo["and"][0], hi["and"][0]
    return a if isinstance(b, dict) and b.get("shr") == [a, 8] else None


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
