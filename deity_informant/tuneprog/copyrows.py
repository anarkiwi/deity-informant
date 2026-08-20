"""What folds: the rows of one sibling family, and what each copy holds.

A row folds when every copy holds the same instruction under one lift shape; an
operand the copies disagree on becomes a column of the family's table, and a
successor that crosses copies is the chain or a refusal.

Public API: :func:`family`, :func:`own`, :class:`Fam`, :class:`MNode`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from jennings.opcodes import MODE_LEN, OPCODES as OPS

from .lift import lift_site
from .siblings import ilen

SPACE = 0x10000  # a column address is an ordinary 16-bit address


@dataclass
class Fam:
    """One merged family: the copies' bases, the rows that fold, the per-copy columns."""

    proc: str
    bases: tuple
    idx: int = 0
    rows: tuple = ()
    cols: tuple = ()  # ((width, values per copy), ...)
    own: dict = field(default_factory=dict)  # address -> the copy that holds it
    base: int = 0  # where the columns live (assigned by the front end)
    rid: int = -1
    hoist: bool = True  # one entry dominates the body, so the columns are read there

    @property
    def k(self):
        return len(self.bases)

    @property
    def entry(self):
        return self.bases[0]

    @property
    def var(self):
        return "cv%d" % self.idx

    @property
    def size(self):
        return sum(w * self.k for w, _v in self.cols)

    def col(self, c):
        return "cx%d_%d" % (self.idx, c)

    def offset(self, c):
        return sum(w * self.k for w, _v in self.cols[:c])

    def column(self, pc):
        """The copy ``pc`` sits in, or ``None`` when no copy holds that address."""
        return self.own.get(pc)

    def bytes(self):
        """The columns as bytes: column ``c``'s copy ``j`` at ``offset(c) + j*w``."""
        out = bytearray()
        for w, vals in self.cols:
            for v in vals:
                out += int(v).to_bytes(w, "little")
        return bytes(out)

    def to_dict(self):
        return {
            "proc": self.proc,
            "bases": ["$%04X" % b for b in self.bases],
            "copies": self.k,
            "rows": len(self.rows),
            "columns": len(self.cols),
            "table": "$%04X" % self.base,
        }


@dataclass
class MNode:
    """One merged node: the template's cfg node, its per-copy keys, counts and lift."""

    fam: Fam
    node: dict
    keys: tuple
    counts: tuple
    ls: object
    per: tuple = ()  # per-copy lifts, for a terminator that stays per copy


def _insn(image, pc, op):
    n = MODE_LEN[OPS[op][1]]
    return bytes([op]) + bytes(image[(pc + i) & 0xFFFF] for i in range(1, n))


def _lift(image, pc, op, cells):
    """The residualised lift of the instruction the image holds at ``pc``."""
    return lift_site(image, {"pc": pc, "opcode": op, "variants": [_insn(image, pc, op)]}, cells)


def _same_ops(a, b):
    """True when two lifts differ only in constant varnodes.

    A control target is not part of the shape: where the copies jump is the
    successor map's business, but the flag and polarity a branch tests are.
    """
    if a.src_map != b.src_map or len(a.ops) != len(b.ops) or a.ctrl[0] != b.ctrl[0]:
        return False
    if a.ctrl[0] == "br" and a.ctrl[1:3] != b.ctrl[1:3]:
        return False
    for (m1, o1, i1), (m2, o2, i2) in zip(a.ops, b.ops):
        if m1 != m2 or o1 != o2 or len(i1) != len(i2):
            return False
        for x, y in zip(i1, i2):
            if x[0] != y[0] or x[2] != y[2] or (x[0] != "c" and x != y):
                return False
    return True


def _fuse(lifts, cols):
    """``lifts[0]`` with ``["h", column, width]`` where the copies name different constants.

    Columns are shared, so one address the copies displace is one column however
    many rows name it.
    """
    a = lifts[0]
    if not all(_same_ops(a, b) for b in lifts[1:]):
        return None
    ops = [[mn, out, list(ins)] for mn, out, ins in a.ops]
    for i, (_mn, _out, ins) in enumerate(ops):
        for j, vn in enumerate(ins):
            vs = tuple(l.ops[i][2][j][1] for l in lifts)
            if len(set(vs)) == 1:
                continue
            ins[j] = ["h", cols.setdefault((vn[2], vs), len(cols)), vn[2]]
    return replace(a, ops=ops)


def _agree(nodes, key):
    return len({str(n[key]) for n in nodes.values()}) == 1


def _own(bases, rows, image):
    """``{address: copy}``: what each copy holds, from its rows and the chain.

    Two rows of one copy own everything between them; the chained run owns the
    gaps its copies' bases delimit. An arm body sits where its siblings' do, so
    address order alone does not say whose it is.
    """
    out, k = {}, len(bases)
    for j in range(k - 1):
        out.update(dict.fromkeys(range(bases[j], bases[j + 1]), j))
    at = sorted((p, j) for r in rows for j, p in enumerate(r))
    # the last copy is bounded by its rows, not by a base after it, but what lies
    # between its base and its first row is the stream the alignment stepped over
    out.update(dict.fromkeys(range(bases[-1], min(p for p, j in at if j == k - 1)), k - 1))
    for (p, j), (q, c) in zip(at, at[1:]):
        out.update(dict.fromkeys(range(p, q if c == j else p + ilen(image, p)), j))
    p, j = at[-1]
    out.update(dict.fromkeys(range(p, p + ilen(image, p)), j))
    return out


class _Rows:
    """The rows of one family, with what every copy's cfg says about each."""

    def __init__(self, cp, sib, image):
        self.cp = cp
        self.at = {}
        for (pc, op), n in cp.nodes.items():
            self.at.setdefault(pc, {})[op] = n
        self.own = _own(sib.bases, sib.rows, image)
        self.byrow = {r[0]: r for r in sib.rows}

    def ops(self, row):
        """The one opcode every executed copy of ``row`` ran, or ``None``."""
        seen = {o for p in row for o in self.at.get(p, {})}
        if len(seen) != 1 or any(len(self.at.get(p, {})) > 1 for p in row):
            return None
        return seen.pop()


def _rowok(rows, row, op, ctx, cols):
    """The merged node's ingredients when ``row`` folds to one node, else ``None``.

    Every copy holds the same instruction, none dispatches on its own opcode byte,
    every lift has one shape, and what the trace lifted for a copy that ran the
    row is what the image says it is.
    """
    trace, lifted, image = ctx
    if any(p in rows.cp.variant_switch or image[p] != op for p in row):
        return None
    got = {j: rows.at.get(p, {}).get(op) for j, p in enumerate(row)}
    live = {j: n for j, n in got.items() if n is not None}
    if not live or not all(_agree(live, f) for f in ("term", "mnemonic", "computed", "call")):
        return None
    lifts = [_lift(image, p, op, trace.cells) for p in row]
    for j, n in live.items():
        if not _same_ops(lifts[j], lifted[n["key"]]):
            return None
        if [o[2] for o in lifts[j].ops] != [o[2] for o in lifted[n["key"]].ops]:
            return None
    ls = _fuse(lifts, cols)
    if ls is None:
        return None
    keys = tuple(n["key"] if n is not None else None for n in got.values())
    counts = tuple(n["count"] if n is not None else 0 for n in got.values())
    return live, keys, counts, ls, tuple(lifts)


def _mapped(fam, tmpl, j, to):
    """Where copy ``j``'s successor ``to`` lands after the fold, or ``None``.

    ``("t", pc)`` is the template row it folds onto, ``("p", pc)`` a block that
    stays, ``("chain",)`` the edge into the next copy; anything else crosses
    copies, which ``v`` cannot name.
    """
    c = fam.column(to)
    if c is None:
        return ("p", to)
    if c == j:
        return ("t", tmpl[to]) if to in tmpl else ("p", to)
    if c == j + 1 and to == fam.bases[j + 1] and to in tmpl:
        return ("chain", tmpl[to])
    return None


def _ref(to, tail=False, trap=False):
    return {"to": to, "tail": bool(tail), "trap": bool(trap)}


def _succs(fam, tmpl, live, idx):
    """The merged form of successor ``idx``: one ref, a chain, or one ref per copy."""
    per, hit = {}, {}
    for j, n in live.items():
        if idx >= len(n["succ"]):
            continue
        r = n["succ"][idx]
        m = ("p", r["to"]) if r["tail"] else _mapped(fam, tmpl, j, r["to"])
        if m is None:
            return None
        per[j], hit[j] = (m, r["trap"], r["tail"]), m
    return _one(fam, per, hit) if hit else None


def _one(fam, per, hit):
    """One ref where every copy agrees, a chain where the last copy leaves, else a split.

    A target inside the family that no row folds is that copy's own block, so a
    copy that never ran the edge takes nobody else's word for it.
    """
    shapes = set(hit.values())
    m = next(iter(shapes)) if len(shapes) == 1 else None
    if m is not None and m[0] != "chain":
        local = m[0] == "p" and fam.column(m[1]) is not None
        if len(hit) == fam.k or not local:
            return _ref(
                m[1],
                tail=any(t for _m, _x, t in per.values()),
                trap=all(x for _m, x, _t in per.values()),
            )
    chain = {j for j, m in hit.items() if m[0] == "chain"}
    rest = set(hit) - chain
    one = {hit[j][1] for j in chain}
    if len(one) == 1 and chain == {j for j in hit if j < fam.k - 1} and rest <= {fam.k - 1}:
        out = per.get(fam.k - 1)
        exit_ref = _ref(0, trap=True) if out is None else _ref(out[0][1], out[2], out[1])
        return {"chain": exit_ref, "to": one.pop()}
    out = [_perref(per.get(j)) for j in range(fam.k)]
    if all(r.get("trap") for r in out):
        return _ref(0, trap=True)
    return {"per": out}


def _perref(got):
    if got is None:
        return _ref(0, trap=True)
    m, trap, tail = got
    if m[0] == "chain":
        return {"chain": _ref(0, trap=True), "to": m[1]}
    return _ref(m[1], tail, trap)


def _cases(fam, tmpl, live):
    """The merged switch: one over a shared expression, or one switch per copy."""
    per = {}
    for j, n in live.items():
        got = {}
        for v, r in n["switch"]["cases"]:
            m = ("p", r["to"]) if r["tail"] else _mapped(fam, tmpl, j, r["to"])
            if m is None:
                return None
            got[v] = _ref(m[1], r["tail"], r["trap"])
        per[j] = {"expr": n["switch"]["expr"], "cases": sorted(got.items())}
    one = per[min(per)]
    if len(per) == fam.k and all(str(p) == str(one) for p in per.values()):
        return {"expr": one["expr"], "cases": [[v, r] for v, r in one["cases"]]}
    return {"per": [per.get(j) for j in range(fam.k)]}


def _merge(fam, tmpl, live, ls):
    """The template's cfg node with merged successors, or ``None`` when a copy crosses."""
    first = live[min(live)]
    n = dict(first, pc=ls.pc, count=sum(x["count"] for x in live.values()))
    arity = 0 if first["term"] == "switch" else max(len(x["succ"]) for x in live.values())
    succ = [_succs(fam, tmpl, live, i) for i in range(arity)]
    if any(s is None for s in succ):
        return None
    n["succ"] = succ
    if first["switch"] is None:
        return n
    if any(x["switch"] is None for x in live.values()):
        return None
    sw = _cases(fam, tmpl, live)
    if sw is None:
        return None
    n["switch"] = sw
    if first["term"] == "switch" and "per" not in sw:
        n["succ"] = [r for _v, r in sw["cases"]]
    return n


def _unions(keys, trace, lifted):
    """One address per copy for every access the fold makes one: its region is their union."""
    out = []
    live = [k for k in keys if k is not None]
    ops = {i for k in live for i in (set(trace.sites[k]["reads"]) | set(trace.sites[k]["writes"]))}
    for i in sorted(ops):
        addrs = set()
        for k in live:
            s = trace.sites[k]
            got = s["reads"].get(i) or s["writes"].get(i)
            if got:
                addrs.add(min(got))
        if len(addrs) > 1:
            out.append(tuple(sorted(addrs)))
    cells = [tuple(a for a, _w in lifted[k].cell_loads) for k in live]
    if len(cells) > 1 and len({len(c) for c in cells}) == 1:
        for grp in zip(*cells):
            if len(set(grp)) > 1:
                out.append(tuple(sorted(set(grp))))
    return out


def _sound(cp, fam, tmpl):
    """``v`` names the copy: no edge crosses from one copy into another.

    An edge from outside the family enters the copy that holds its target, which
    the front end reaches through a prologue that sets ``v``; only the chain edge
    may leave one copy for the next.
    """
    for (pc, _op), n in cp.nodes.items():
        src = fam.column(pc)
        if src is None:
            continue
        refs = list(n["succ"]) + ([r for _v, r in n["switch"]["cases"]] if n["switch"] else [])
        for r in refs:
            to = r["to"]
            if r["tail"] or fam.column(to) is None or fam.column(to) == src:
                continue
            if not (src + 1 < fam.k and to == fam.bases[src + 1] and to in tmpl):
                return "an edge from copy %d enters copy %s" % (src, fam.column(to))
    return None


def _entries(cp, fam):
    """The addresses an edge from outside the family enters it at.

    The columns are read once at the loop header only where that header dominates
    the body, which is exactly the family nothing enters but its own entry.
    """
    out = {cp.entry} if fam.column(cp.entry) is not None else set()
    for (pc, _op), n in cp.nodes.items():
        if fam.column(pc) is not None:
            continue
        refs = list(n["succ"]) + ([r for _v, r in n["switch"]["cases"]] if n["switch"] else [])
        out |= {r["to"] for r in refs if not r["tail"] and fam.column(r["to"]) is not None}
    return out


def family(cp, sib, idx, ctx):
    """``(Fam, nodes, unions, template map)`` for one sibling family, or a refusal."""
    trace, lifted, image = ctx
    rows = _Rows(cp, sib, image)
    fam = Fam(sib.proc, tuple(sib.bases), idx, own=rows.own)
    cols, keep = {}, {}
    for t0, row in sorted(rows.byrow.items()):
        op = rows.ops(row)
        got = None if op is None else _rowok(rows, row, op, ctx, cols)
        if got is not None:
            keep[t0] = (row, op) + got
    if fam.entry not in keep:
        return "the entry row does not fold"
    while True:
        tmpl = {p: t0 for t0, v in keep.items() for p in v[0]}
        bad, nodes, unions = set(), {}, []
        for t0, (_row, op, live, keys, counts, ls, per) in sorted(keep.items()):
            got = _merge(fam, tmpl, live, ls)
            if got is None:
                bad.add(t0)
                continue
            nodes[(t0, op)] = MNode(fam, got, keys, counts, ls, per)
            unions += _unions(keys, trace, lifted)
        if not bad:
            break
        if fam.entry in bad:
            return "the entry row's successors cross copies"
        for t0 in bad:
            del keep[t0]
    why = _sound(cp, fam, tmpl)
    if why:
        return why
    fam.hoist = _entries(cp, fam) <= {fam.entry}
    fam.rows = tuple(keep[t0][0] for t0 in sorted(keep))
    fam.cols = tuple(w_vals for w_vals, _c in sorted(cols.items(), key=lambda kv: kv[1]))
    return fam, nodes, unions, tmpl
