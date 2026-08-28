"""S6 -- T1: the bounded accumulators a certified tune moves its registers with.

A state cell that updates from itself and reaches a SID write site (T0) is one
``Acc`` of the prototype's section 5 schema, checked twice against :mod:`.history`
-- the interval it claims and its ``step`` replayed exactly (:mod:`.accstep`). What
neither the grammar nor the replay accepts is a stated refusal, never an approximation.
"""

from __future__ import annotations

from collections import namedtuple

from .accguard import unpin
from .acchist import Cells
from .accstep import COPY, Inexact, Stepper, prove
from .accdelta import _cellref, cellname, delta_of, tablestep_exprs, tablestep_sources, unscratch
from .accrule import _const, _dirstore, _groups, _merge, _splitpair, bound_of, candidates
from .accrule import copies_of, counters_of, fn_phase, phase_of, policy_of, rate_of, scope_of
from . import accreg
from .accshape import Ctx, arms, complemented, enclosing, external, key_of, lowbits
from .accshape import onepass, selfread
from .accshape import maskof, sites, step, terms
from .facts import Facts
from .ir import Bin, Const, Load, R16, Store, Var, W16
from .loops import repeats
from .irwalk import walk

WHY = "unclassified update"
INEXACT = "inexact recurrence"
DIVERGES = "divergent recurrence"
WHYS = (WHY, INEXACT, DIVERGES)
CLAUSES = ("delta", "phase", "bound", "rate", "replay")
POLICIES = ("wrap", "reflect", "reflect-complement", "clamp", "halt", "reload")
FIELDS = (
    "kind guards sign delta carry comp value mask addr site rank proc block at exact chain"
    " dexact cexact shift times"
)
Clause = namedtuple("Clause", FIELDS, defaults=((), None, (), None, None, 0, None))


# ---- one store, read as a clause ----------------------------------------------
def clauses(ctx, tgt, ss, extra=()):
    """Every arm of every store into ``tgt`` as a step or an action."""
    base = selfread(tgt)

    def isself(e):
        return base(e) or complemented(e, base)

    out = []
    for s in list(ss) + list(extra):
        raw = s.stmt.e if type(s.stmt) is W16 else s.stmt.v
        skip = frozenset(x.n for x in walk(s.stmt.a) if type(x) is Var)
        arms_ = arms(ctx, s.proc, s.block, raw, s.stmt.a, skip)
        for arm, rank, at in ctx.ranked(s.proc, s.block, s.at, arms_):
            v, mask = maskof(arm.value)
            got = step(v, isself)
            comp = any(complemented(x, base) for _g, x in terms(v))
            kind = "step" if got else ("action" if not _reads(arm.value, base) else "opaque")
            sign, delta, carry, borrow = got or (0, None, None, False)
            if borrow and carry is not None:
                carry = Bin("-", carry, Const(1, 1), 1)
            dexact, cexact = _exactstep(arm.exact, isself, borrow) if got else (None, None)
            out.append(
                Clause(
                    kind,
                    arm.guards,
                    sign,
                    delta,
                    carry,
                    comp,
                    arm.value,
                    mask,
                    arm.addr,
                    s,
                    rank,
                    arm.proc,
                    arm.block,
                    at,
                    arm.exact,
                    ctx.chain(s.proc, arm.path),
                    dexact,
                    cexact,
                )
            )
    return sorted(
        out,
        key=lambda c: (c.rank, c.site.stmt.src, c.proc, c.block, repr(c.value), repr(c.guards)),
    )


def _exactstep(exact, isself, borrow):
    """The delta and carry of the site-pinned value, where it spells the same step."""
    got = step(unpin(exact, isself), isself)
    if got is None or bool(got[3]) != borrow:
        return None, None
    carry = got[2]
    if borrow and carry is not None:
        carry = Bin("-", carry, Const(1, 1), 1)
    return got[1], carry


def _reads(e, base):
    """True when a value reads the accumulator's own cell anywhere."""
    return any(base(x) for x in walk(e))


def registers(t0_doc):
    """``{region: (register, voices)}``: the SID register every named cell reaches."""
    out = {}
    for w in t0_doc.get("writes") or ():
        if not w.get("register"):
            continue
        for c in w["cells"]:
            hit = out.get(c["region"])
            if hit is None or (w["self_update"] and not hit[2]):
                out[c["region"]] = (w["register"], list(w["voices"]), w["self_update"])
    return out


WIDTH = {"freq": 16, "pw": 12, "cutoff": 11}


def _repeat(ctx, cells, c, delta):
    """``repeat(delta, n)`` where a counted loop applies the same step ``n`` times.

    Hubbard's triangle is the closed form every other family accumulates: the same
    step added ``phase`` times inside one loop (``$520B``). ``times`` is the passes
    the loop runs, one more than its bound where the test follows the body.
    """
    if delta is None:
        return None
    for header, body, latches in enclosing(ctx, c.site.proc, c.site.block):
        got = repeats(ctx.prog.procs[c.site.proc], header, body, latches)
        ref = None if got is None else _cellref(ctx, cells, got[1])
        if ref is not None:
            k = Const(0 if onepass(ctx, c.site.proc, c.site.block, c.guards) else 1, 1)
            return {"kind": "repeat", "step": delta, "n": ref, "times": Bin("+", got[1], k, 2)}
    return delta


def _idx(c):
    """The names one clause's own store addresses with: the copy it reaches."""
    return {x.n for x in walk(c.addr) if type(x) is Var}


def _withcarry(delta, steps):
    """Section 5's ``delta + carry(site)``: a live bit another block of the tick made.

    The carry a call returns is a flag in SSA, so the record names the site that
    defines it, not a cell -- the one thing the tick's own dataflow can say.
    """
    got = [(c, _flag(c.carry)) for c in steps if _flag(c.carry) is not None]
    if delta is None or not got:
        return delta
    if any(external(f) for _c, f in got):
        return None  # the tick was given this bit; section 8 refuses an external input
    c, flag = got[0]
    return dict(delta, carry={"site": "$%04X" % c.site.stmt.src, "flag": flag.n})


def _flag(e):
    """The SSA flag a carry names, where the carry is one: ``C``, or its borrow.

    A carry another block computes in place is part of the step's own arithmetic; a
    carry that is only a *name* is a bit some other block of the tick left, which is
    what section 5's ``+ carry(site)`` states and what ``ssa`` calls a flag.
    """
    if type(e) is Bin and e.op == "-" and type(e.b) is Const and e.b.v == 1:
        e = e.a
    return e if type(e) is Var else None


def _read(tgt, c):
    """The accumulator's own value at its own address: the base plus the copy's displacement."""
    s = c.site.stmt
    addr = Bin("+", Const(tgt.cells[0][1], 2), Var(COPY, 1), 2)
    if tgt.kind == "pair":
        return R16(tgt.cells[0], tgt.cells[1], addr)
    return Load("ram", addr, 1, s.lo, s.hi, tgt.cells[0][0])


def _one(ctx, cells, tgt, steps, cs, env):
    """One provisional Acc: everything but the identifiers other Accs are known by."""
    rid = tgt.cells[0][0]
    scratch = tgt.cells[0] in ctx.scratch
    actions = [c for c in cs if c.kind == "action"]
    event = next((c.guards for c in steps if c.comp), ()) or next((c.guards for c in actions), ())
    mask = next((c.mask for c in steps if c.mask is not None), None)
    reg = env["regs"].get(rid) or (None, [], False)
    own = [c for c in cs if c.kind not in ("opaque", "half")]
    index, scale, copies = copies_of(cells, own or steps)
    index = sorted(set(index) | {n for c in cs if c.kind == "half" for n in _idx(c)})
    width = 16 if tgt.kind == "pair" else (lowbits(mask) or 8)
    delta = _repeat(ctx, cells, steps[0], delta_of(ctx, cells, steps[0].delta, env["sources"]))
    delta = _withcarry(delta, steps)
    phase = phase_of(ctx, cells, tgt, steps, env["byacc"], env["counters"])
    if phase["kind"] == "none":
        phase = fn_phase(ctx, cells, delta, env["counters"], env["all"]) or phase
    read = _read(tgt, steps[0])
    tcol = cells.value(read, {n: 0 for n in index + [COPY]})
    hold = steps[0].guards if len(steps) == 1 else ()
    bounds = bound_of(
        ctx, cells, tgt, width, event, hold, mask, None, tcol, env["complete"], env["period"]
    )
    policy, value = policy_of(
        steps,
        actions,
        phase,
        bounds[0],
        _dirstore(ctx, cells, phase, env["all"], sorted({r for r, _a in tgt.cells})),
        scratch,
    )
    return {
        "target": {"register": reg[0], "voices": reg[1], "kind": "register", "split": None},
        "cell": {
            "region": rid,
            "name": cellname(ctx, cells, rid),
            "addr": "$%04X" % tgt.cells[0][1],
            "copies": copies,
            "role": ctx.names.role.get(rid, ""),
        },
        "width": WIDTH.get(reg[0], width) if tgt.kind == "pair" else width,
        "delta": delta,
        "bound": bounds[0],
        "bounds": bounds,
        "policy": policy,
        "policy_value": value,
        "rate": rate_of(ctx, cells, steps, actions, env["counters"]),
        "phase": phase,
        "links": [],
        "scope": scope_of(cells, rid, len(reg[1]) if scratch and reg[1] else copies),
        "sites": sorted({"$%04X" % c.site.stmt.src for c in steps}),
        "index": index,
        "scale": scale,
        "read": read,
        "regions": sorted({r for r, _a in tgt.cells}),
        "complete": env["complete"],
        "period": env["period"],
        "target_cells": tgt,
        "steps": steps,
        "actions": actions,
        "clauses": [c for c in cs if c.kind != "opaque"],
        "mask": mask,
        "scratch": scratch,
    }


def _sext(e):
    """``e`` as a signed byte: the ``$100`` bit 7 takes off, :func:`~.idioms.sext_of`."""
    return Bin("-", e, Bin("<<", Bin("&", e, Const(0x80, 1), 1), Const(1, 1), 2), 2)


def _join(lo, hi):
    """Fold a carry-joined high half into the low one: one value across two registers."""
    got = _splitpair(lo, hi)
    if got is None:
        return False
    k, kh, byte = got
    steps = hi["steps"] if byte is not None else lo["steps"]
    if byte is None and any(_const(c.delta) != 0 for c in hi["steps"]):
        return False
    lo["target"] = dict(
        lo["target"], kind="split", split=[k, kh], register=hi["target"]["register"]
    )
    lo["width"] = k + kh
    lo["hi"] = dict(hi["cell"])
    lo["regions"] = sorted(set(lo["regions"]) | set(hi["regions"]))
    lo["read"] = Bin("|", lo["read"], Bin("<<", hi["read"], Const(k, 1), 2), 2)
    lo["steps"] = steps
    lo["actions"] = hi["actions"] if byte is not None else lo["actions"]
    lo["clauses"] = hi["clauses"] if byte is not None else lo["clauses"]
    lo["shift"] = k if byte is not None else 0
    lo["delta"] = (
        dict(delta_of(hi["ctx"], hi["cells"], byte, hi["sources"]), signed=k + kh)
        if byte is not None
        else lo["delta"]
    )
    lo["joined"] = byte
    lo["sites"] = sorted(set(lo["sites"]) | set(hi["sites"]))
    lo["bounds"] = _rebound(lo)
    return True


def _rebound(a):
    """A joined target's intervals: a guard on it still holds, its halves' masks do not."""
    cells, ok, wide = a["cells"], a["complete"], (1 << a["width"]) - 1
    col = cells.value(a["read"], {n: 0 for n in a["index"] + [COPY]})
    out = [b for b in a["bounds"] if b["from"] == "proved"]
    if ok and col is not None and col.size:
        out.append(
            {
                "interval": [int(col.min()), int(col.max())],
                "from": "observed",
                "witness": "period %s" % a["period"],
            }
        )
    return out + [{"interval": [0, wide], "from": "projected", "witness": "width"}]


def _pairs(accs):
    """Every ``split(k, 8)`` join, the folded high halves dropped."""
    gone = set()
    for lo in accs:
        for hi in accs:
            if lo is hi or id(hi) in gone or lo["target"]["kind"] == "split":
                continue
            if _join(lo, hi):
                gone.add(id(hi))
    return [a for a in accs if id(a) not in gone]


def _plan(acc):
    """Every clause of the value cell, in the split's units where the target is one.

    One Acc is one producer, and a cell has more than one: the plane's claim is that
    the clauses of the cell together make every move it makes.
    """
    out, k, joined = [], acc.get("shift") or 0, acc.get("joined")
    times = (
        (acc["delta"] or {}).get("times") if (acc["delta"] or {}).get("kind") == "repeat" else None
    )
    tab = acc["cells"].tabstep if acc["scratch"] else {}
    for c in acc["clauses"]:
        if c.kind == "step":
            d = _sext(joined) if joined is not None else c.delta
            dx = _sext(joined) if joined is not None else c.dexact
            out.append(
                c._replace(
                    delta=unscratch(d, tab),
                    dexact=None if dx is None else unscratch(dx, tab),
                    carry=None if k else c.carry,
                    cexact=None if k else c.cexact,
                    times=times,
                )
            )
        else:
            v = Bin("<<", c.value, Const(k, 1), 2) if k else c.value
            x = c.exact if c.exact is not None else c.value
            x = Bin("<<", x, Const(k, 1), 2) if k else x
            out.append(c._replace(value=unscratch(v, tab), exact=unscratch(x, tab)))
    return out


def _identify(ctx, cells, accs):
    """Give every Acc its id, then name the Accs its phase and its links reach."""
    byacc = {}
    for i, a in enumerate(accs):
        a["id"] = "acc%d" % i
        byacc[(a["cell"]["region"], int(a["cell"]["addr"][1:], 16))] = a["id"]
    for a in accs:
        for other in (byacc.get(k) for k in _linked(a)):
            hit = next((x for x in accs if x["id"] == other), None)
            if hit is not None and not hit["target"]["register"]:
                hit["target"] = dict(a["target"], kind=hit["target"]["kind"])
        ref = a["phase"].get("cell")
        key = ref and (ref["region"], int(ref["addr"][1:], 16))
        if key in byacc and byacc[key] != a["id"]:
            a["phase"] = dict(a["phase"], kind="acc", acc=byacc[key])
        a["links"] = sorted(
            {byacc[k] for k in _resets(ctx, a["actions"]) if k in byacc and byacc[k] != a["id"]}
        )
        a["links"] = [{"reset": x} for x in a["links"]]
    return byacc


def _linked(a):
    """The cells one Acc's phase and its actions' own block reach."""
    ref = a["phase"].get("cell")
    return [(ref["region"], int(ref["addr"][1:], 16))] if ref else []


def _resets(ctx, actions):
    """The cells an action's own block sets to a constant: what its event zeroes."""
    out = set()
    for c in actions:
        for s in ctx.prog.procs[c.site.proc].blocks[c.site.block].stmts:
            if type(s) is not Store or s.r < 0 or type(s.v) is not Const:
                continue
            k = key_of(s)
            if k is not None:
                out.add(k.cells[0])
    return out


def _refuse(ctx, cells, tgt, cs, clause, why=WHY):
    """One stated refusal: the cell, the site and the clause of section 5 it failed.

    ``scratch`` marks a value cell a copy loop rewrites: one column and one value
    per copy in a tick, which no once-a-tick history can take apart.
    """
    rid, addr = tgt.cells[0]
    return {
        "why": why,
        "cell": cellname(ctx, cells, rid),
        "region": rid,
        "addr": "$%04X" % addr,
        "site": sorted({"$%04X" % c.site.stmt.src for c in cs})[:4],
        "clause": clause,
        "scratch": tgt.cells[0] in ctx.scratch,
    }


def _reach(accs, regs):
    """The Accs a SID write site reaches, and the Accs those reach through a phase."""
    keep = {a["id"] for a in accs if a["cell"]["region"] in regs}
    while True:
        more = set(keep)
        for a in accs:
            if a["id"] not in keep:
                continue
            more |= {l["reset"] for l in a["links"]}
            if a["phase"].get("kind") == "acc":
                more.add(a["phase"]["acc"])
        if more == keep:
            return keep
        keep = more


def accumulators(prog, names, t0_doc, hist, facts=None, complete=False, period=None, obs=None):
    """``(accs, refusals)``: the section 5 records of one certified tune, both verified."""
    facts = facts or Facts(prog)
    ctx, cells = Ctx(prog, names), Cells(prog, names, hist, facts)
    raw = {t: clauses(ctx, t, ss) for t, ss in sites(prog, facts, ctx.rank).items()}
    src = tablestep_sources(ctx, cells, raw)
    regs = registers(t0_doc)
    stepper = Stepper(ctx, cells, raw)
    cells.scratch = ctx.scratch
    cells.tabstep = tablestep_exprs(ctx, raw)
    cells.obs = accreg.observable(obs) if obs else None
    cells.counters = counters_of(raw, cells)
    env = {
        "regs": regs,
        "sources": src,
        "counters": cells.counters,
        "byacc": {},
        "all": raw,
        "complete": complete,
        "period": period,
    }
    accs, refusals = [], []
    merged = _merge(raw)
    cands = candidates(merged, src)
    halves = {c for t in raw if t.kind == "pair" for c in t.cells}
    stepped = {k[0][0] if isinstance(k[0], tuple) else k[0] for k in src}
    for tgt, cs in sorted(merged.items(), key=lambda kv: kv[0].cells):
        opaque = [c for c in cs if c.kind == "opaque"]
        key = tgt.cells if tgt.kind == "pair" else tgt.cells[0]
        if key in src or tgt.cells[0][0] in stepped or tgt.cells[0] in halves:
            continue  # a delta producer and a pair's own half are not accumulators
        if opaque and (tgt in cands or any(r in regs for r, _a in tgt.cells)):
            refusals.append(_refuse(ctx, cells, tgt, opaque, "delta"))
    for tgt, cs in sorted(cands.items(), key=lambda kv: kv[0].cells):
        if any(c.kind == "opaque" for c in cs):
            continue
        for _k, steps in sorted(_groups([c for c in cs if c.kind == "step"]).items()):
            a = _one(ctx, cells, tgt, steps, cs, env)
            a.update(ctx=ctx, cells=cells, sources=src)
            accs.append(a)
    accs = _pairs(accs)
    for a in [x for x in accs if x["delta"] is None]:
        refusals.append(_refuse(ctx, cells, a["target_cells"], a["steps"], "delta"))
    accs = [a for a in accs if a["delta"] not in (None, {"kind": "const", "value": 0})]
    _identify(ctx, cells, accs)
    keep = _reach(accs, regs)
    out = []
    for a in (x for x in accs if x["id"] in keep):
        plan = _plan(a)
        try:
            per = accreg.series(cells, a, plan, ctx, t0_doc, stepper) if a["scratch"] else None
        except Inexact as x:
            per, a["verify"] = None, {"why": x.why, "site": x.site, "divergences": 0}
        if per:
            a["bounds"] = accreg.bound(per, complete, period, cells.ticks) + a["bounds"]
        if "verify" not in a:
            a["bound"], a["verify"], a["step"] = prove(cells, a, plan, a["bounds"], stepper, per)
        rec = {k: v for k, v in a.items() if k in RECORD}
        rec["delta"] = {k: v for k, v in (rec["delta"] or {}).items() if k not in DROP} or None
        v = a["verify"]
        if v.get("why") or v["divergences"] or v.get("escapes"):
            why = DIVERGES if v.get("why") is None else (WHY if v["why"] == "read" else INEXACT)
            refusals.append(
                dict(
                    _refuse(ctx, cells, a["target_cells"], a["steps"], "replay", why),
                    acc=a["id"],
                    detail=v.get("why") or "diverges at tick %s" % v.get("tick"),
                    at=v.get("site"),
                    tick=v.get("tick"),
                    verify=v,
                )
            )
            continue
        out.append(rec)
    return out, refusals


DROP = ("times",)


RECORD = (
    "id target cell width delta bound policy policy_value rate phase links scope"
    " sites hi verify index scale regions step"
).split()


def document(prog, names, t0_doc, hist, cert=None, facts=None, obs=None):
    """``tuneprog.T1.json``: the accumulator plane, its refusals and its horizon."""
    sub = ((cert or {}).get("subtunes") or [{}])[0]
    accs, refusals = accumulators(
        prog, names, t0_doc, hist, facts, bool(sub.get("complete")), sub.get("period"), obs
    )
    return {
        "plane": "S6-view",
        "horizon": {
            "ticks": min((a.shape[0] for a in hist.values()), default=0),
            "complete": bool(sub.get("complete")),
            "period": sub.get("period"),
        },
        "accs": accs,
        "refusals": refusals,
    }
