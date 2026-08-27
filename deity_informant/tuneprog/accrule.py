"""T1 -- the rules that read one recurrence as a section 5 ``Acc``.

Which cells are accumulators and which are the counters that pace them, which
guard bounds a value and which only picks an arm, and the phase, policy, rate and
scope those guards leave.
"""

from __future__ import annotations

from .accdelta import _cellref
from .accshape import canon, lowbits, maskof, reads, selfread, sext_split
from .facts import SID_VOICES
from .ir import Bin, Const, Load
from .irwalk import walk

SENTINEL = 0xFF


# ---- direction, bound, policy, rate --------------------------------------------
def _ontarget(base, c):
    """``(operand, other side, op)`` when a comparison reads the accumulator's cell."""
    if type(c) is not Bin or c.op not in ("==", "!=", "<", "<="):
        return None
    for x, y, flip in ((c.a, c.b, False), (c.b, c.a, True)):
        if any(base(z) for z in walk(x)):
            return x, y, ("<=" if c.op == "<" else "<") if flip and c.op in ("<", "<=") else c.op
    return None


def _cmpcell(ctx, cells, c):
    """The one cell a comparison reads, with the bit it masks, or ``None``."""
    for x in walk(c):
        v, mask = maskof(x)
        ref = _cellref(ctx, cells, v)
        if ref is not None:
            return ref, mask
    return None, None


def phase_of(ctx, cells, tgt, steps, byacc, counters):
    """The five-way ``phase``: which bit, cell, counter or other Acc picks a sign."""
    up = {repr(g): t for c in steps if c.sign > 0 for g, t, _w in c.guards}
    down = {repr(g): t for c in steps if c.sign < 0 for g, t, _w in c.guards}
    pick = None
    for c in steps:
        for g, t, _w in c.guards:
            k = repr(g)
            if k in up and k in down and up[k] != down[k]:
                pick = g
    if pick is None:
        return {"kind": "none", "cell": None, "bit": None}
    ref, mask = _cmpcell(ctx, cells, pick)
    if ref is None:
        return {"kind": "unnamed", "cell": None, "bit": None, "test": repr(pick)}
    key = (ref["region"], int(ref["addr"][1:], 16))
    k = (
        None
        if mask is None
        else (mask.bit_length() - 1 if mask and not mask & (mask - 1) else None)
    )
    if key in byacc:
        return {"kind": "acc", "acc": byacc[key], "cell": ref, "bit": k}
    if key in counters:
        return {"kind": "counter", "cell": ref, "bit": k}
    own = selfread(tgt)
    if any(own(x) for x in walk(pick)):
        return {"kind": "self", "cell": ref, "bit": k}
    return {"kind": "bit" if k is not None else "cell", "cell": ref, "bit": k}


def _const(e):
    return e.v if type(e) is Const else None


def bound_of(ctx, cells, tgt, width, event, hold, mask, split, target, complete, period):
    """The intervals a guard, the store's own mask and the horizon each claim.

    In preference order; :func:`~.acchist.verify` takes the first one the certified
    horizon does not escape, so a guard that selects an arm without bounding the
    cell is not mistaken for one that bounds it.
    """
    lo, hi, cell, why = 0, (1 << width) - 1, None, None
    base, unit = selfread(tgt), 1 << (split[0] if split else 0)
    out, proj = [], False
    for g, t, _w in tuple(event) + tuple((c, not v, w) for c, v, w in hold):
        got = _ontarget(base, g)
        if got is None:
            continue
        x, other, op = got
        v = _const(other)
        ref = None if v is not None else _cellref(ctx, cells, other)
        if v is None and ref is None:
            continue
        top = _hiunit(base, x, unit)
        masked = maskof(x)[1] is not None
        if op in ("<", "<=") and not t:
            hi, cell, why, proj = (v * top + top - 1 if v is not None else hi), ref, repr(g), masked
        elif op == "==" and not t and v is not None:
            hi, why, proj = max(hi if cell is None else 0, v * top + top - 1), repr(g), masked
        elif op in ("<", "<=") and t and v is not None:
            lo, why, proj = v * top, repr(g), masked
    if why is not None:
        got = "projected" if proj else "proved"
        out.append({"interval": [lo, hi if cell is None else cell], "from": got, "witness": why})
    if mask is not None and mask < (1 << width) - 1:
        out.append({"interval": [0, mask], "from": "projected", "witness": "mask $%02X" % mask})
    if complete and target is not None and target.size:
        out.append(
            {
                "interval": [int(target.min()), int(target.max())],
                "from": "observed",
                "witness": "period %s" % period,
            }
        )
    out.append({"interval": [0, (1 << width) - 1], "from": "projected", "witness": "width"})
    return out


def _hiunit(base, x, unit):
    """The units a comparison speaks in: the split's high half counts whole lows."""
    return unit if unit > 1 and any(base(z) for z in walk(x)) else 1


def policy_of(steps, actions, phase, bound, dirstore):
    """``wrap``/``reflect``/``reflect-complement``/``clamp``/``halt``/``reload``."""
    if any(c.comp for c in steps):
        return "reflect-complement", None
    seen = {
        x.r
        for c in list(steps) + list(actions)
        for g, _t, _w in c.guards
        for x in reads(g)
        if type(x) is Load
    }
    for c in actions:
        if any(_sentinel(g) for g, _t, _w in c.guards):
            return "reload", repr(c.value)
        if seen & {x.r for x in reads(c.value) if type(x) is Load}:
            return "clamp", repr(c.value)
    if dirstore:
        return "reflect", None
    if bound["from"] == "proved" and not actions:
        return "halt", None
    return "wrap", None


def _sentinel(g):
    """A test of a stream byte against ``$FF``: the terminator that reloads a segment."""
    if type(g) is not Bin or g.op not in ("==", "!="):
        return False
    return any(type(x) is Const and x.v == SENTINEL for x in (g.a, g.b))


def rate_of(ctx, cells, steps, actions, counters):
    """The divider that paces an update, and the reload that sets it."""
    for cs, every in ((steps, None), (actions, 1)):
        for c in cs:
            for g, _t, _w in c.guards:
                ref, _m = _cmpcell(ctx, cells, g)
                if ref is None:
                    continue
                key = (ref["region"], int(ref["addr"][1:], 16))
                if key not in counters:
                    continue
                kind, reload = counters[key]
                k = _const(reload) if reload is not None else None
                return {
                    "every": every if every is not None else (None if k is None else k + 1),
                    "counter": ref["name"],
                    "cell": ref,
                    "kind": kind,
                    "reload": None if reload is None else repr(reload),
                }
    return {"every": 1, "counter": None, "cell": None, "kind": "none", "reload": None}


def scope_of(cells, rid, copies):
    """``voice``/``instrument``/``global``, read off the region the value cell lives in.

    The SID has three voices, so a cell of a wider record is one a cursor selects
    and a cell with one copy is the tune's own.
    """
    n = cells.group[rid][2] if rid in cells.group else copies
    if max(n, copies) <= 1:
        return "global"
    return "voice" if max(n, copies) <= SID_VOICES else "instrument"


# ---- the candidates, and the records they become -------------------------------
def counters_of(byname):
    """``{cell: (countdown|countup, reload)}`` for every cell whose steps are one."""
    out = {}
    for tgt, cs in sorted(byname.items()):
        steps = [c for c in cs if c.kind == "step"]
        ks = {c.sign * (_const(c.delta) or 0) for c in steps}
        if tgt.kind != "byte" or not steps or ks - {1, -1}:
            continue
        loads = [c.value for c in cs if c.kind == "action"]
        out[tgt.cells[0]] = ("countdown" if -1 in ks else "countup", loads[0] if loads else None)
    return out


def _merge(byname):
    """A pair's clauses gain the stores of its halves; one block's two halves are one.

    A snap writes the pair as two byte stores, which is the same convention
    :func:`~.word.fold16` reads elsewhere; a half no block pairs is a write the
    record cannot state, and the replay takes it as an unnamed producer.
    """
    out = {}
    for tgt, cs in sorted(byname.items()):
        if tgt.kind != "pair":
            out[tgt] = cs
            continue
        halves = {}
        for other, ocs in sorted(byname.items()):
            if other.kind == "byte" and other.cells[0] in tgt.cells:
                for c in (x for x in ocs if x.kind == "action"):
                    halves.setdefault((c.site.proc, c.site.block), {})[other.cells[0]] = c
        extra = [x for _k, g in sorted(halves.items()) for x in _halves(tgt, g)]
        out[tgt] = sorted(cs + extra, key=lambda c: (c.rank, c.site.stmt.src, repr(c.value)))
    return out


def _halves(tgt, got):
    """One block's writes of a pair: both halves as one assignment, or an unnamed one."""
    lo, hi = got.get(tgt.cells[0]), got.get(tgt.cells[1])
    if lo is not None and hi is not None:
        return [lo._replace(value=Bin("|", lo.value, Bin("<<", hi.value, Const(8, 1), 2), 2))]
    return [c._replace(kind="half") for c in got.values()]


def candidates(byname, sources):
    """The targets whose recurrence is an accumulator's, not a counter's or a step's."""
    out = {}
    for tgt, cs in sorted(byname.items()):
        steps = [c for c in cs if c.kind == "step"]
        key = tgt.cells if tgt.kind == "pair" else tgt.cells[0]
        ks = {c.sign * (_const(c.delta) or 0) for c in steps}
        if not steps or key in sources or not ks - {1, -1}:
            continue
        out[tgt] = cs
    return out


def _groups(steps):
    """Step clauses grouped by their delta's shape and the producer they root in."""
    out = {}
    for c in steps:
        out.setdefault((repr(canon(c.delta)), c.proc), []).append(c)
    return out


def _dirstore(ctx, cells, phase, byname):
    """True when the cell a phase reads is itself stored under a test of the target."""
    ref = phase.get("cell")
    if ref is None:
        return False
    key = (ref["region"], int(ref["addr"][1:], 16))
    for tgt, cs in byname.items():
        if tgt.kind == "byte" and tgt.cells[0] == key:
            return any(c.kind == "step" or c.kind == "action" for c in cs)
    return False


def _splitpair(lo, hi):
    """``(low bits, high bits, the byte both halves read)`` of a carry-joined pair."""
    base = selfread(lo["target_cells"])
    if not any(any(base(z) for z in walk(c.carry)) for c in hi["steps"] if c.carry is not None):
        return None
    k = lowbits(lo["mask"]) or 8
    got = {
        sext_split(a.delta, b.delta, k)
        for a in lo["steps"]
        for b in hi["steps"]
        if sext_split(a.delta, b.delta, k) is not None
    }
    return k, lowbits(hi["mask"]) or 8, got.pop() if len(got) == 1 else None
