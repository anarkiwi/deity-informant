"""Translation cost: what the printed ``.c`` holds per original instruction and per check
(``info["emission"]``, *Translation cost*), from the markers that survive printing."""

from __future__ import annotations

from collections import Counter

from . import marks

#: Guards a deopt tests with no check call of its own, of an address: a check at its trap.
TRAP_CHECKS = ("index", "window", "zone", "stack")


def _lift_of(p, mod):
    """The lift (0: the entries', 1: init's) an MLIR function's operations come from."""
    scope = getattr(mod, "scope", {}) if p.lifted is not None else {}
    return lambda fn: int(fn in scope)


def _defs(p):
    """``([[loc, at, byte, by, lift]], per lift (offset, {pc: ids} reaching, {pc: ids} past))``."""
    table, lifts = [], []
    for k, (defs, at, past) in enumerate(p.pdefs):
        lifts.append((len(table), defs, at, past))
        table += [[*d, k] for d in defs]
    return table, lifts


def _pick(lift, where, pcs, loc=None):
    """The global ids of the definitions of lift ``lift`` reaching (``where`` ``"at"``) or
    past (``"past"``) the instructions ``pcs``, of locations ``loc`` (``"w"`` and a word, or a
    register name) or every one."""
    base, defs, at, past = lift
    got = set()
    for pc in pcs:
        for d in (at if where == "at" else past).get(pc, ()):
            kind, q = defs[d][0], defs[d][1]
            if loc is None or loc == kind or loc == ("w", q):
                got.add(base + d)
    return sorted(got)


def _guard_defs(lift, g):
    """The definitions a lift guard ``g`` (``trap_info``) checks."""
    pcs = [g["at"], *[c for c in g.get("copies") or () if c]]
    if g.get("def") == "entry":
        base, defs = lift[0], lift[1]
        return [base + i for i, d in enumerate(defs) if d[:2] == ("w", g["pair"]) and d[3] is None]
    if g["kind"] == "index":
        return _pick(lift, "at", pcs, g["reg"])
    loc = None if g.get("pair") is None else ("w", g["pair"])
    where = "past" if g.get("def") in ("store", "walk") or g["kind"] == "zone" else "at"
    return _pick(lift, where, pcs, loc)


def emission(p, mod, order, trap_info):
    """``(info["emission"], line)`` of a printed ``.c``: ``order`` the marker records
    surviving in text order; ``line(record)`` the ``(number, file)`` of its ``#line``."""
    recs, lift_of = mod.marks.recs, _lift_of(p, mod)
    table, lifts = _defs(p)

    def pcs_of(fn, pc):
        return [q for q in mod.copies_of(fn, pc) if q]

    checks, index, traps = [], {}, Counter()
    with_op = {recs[j][4] for j in order if recs[j][0] == "check"}
    for j in order:
        rec = recs[j]
        if rec[0] == "trap":
            traps[rec[1]] += 1
            meta = trap_info.get(rec[1]) or {}
            for g in (g for g in meta.get("guards", ()) if g["kind"] in TRAP_CHECKS):
                if rec[1] not in with_op:
                    index.setdefault(j, len(checks))
                    lift = lifts[lift_of(rec[2])] if lifts else None
                    defs = _guard_defs(lift, g) if lift else []
                    checks.append(
                        {
                            "kind": g["kind"],
                            "at": g["at"],
                            "fn": rec[2],
                            "defs": defs,
                            "trap": rec[1],
                        }
                    )
        if rec[0] != "check":
            continue
        _, kind, o, fn, k = rec
        index[j] = len(checks)
        lift = lifts[lift_of(fn)] if lifts else None
        guards = [g for g in (trap_info.get(k) or {}).get("guards", ()) if g["kind"] == "objchk"]
        if lift is None:
            defs = []
        elif kind == "objchk" and guards:
            defs = sorted({d for g in guards for d in _guard_defs(lift, g)})
        else:
            defs = _pick(lift, "at", pcs_of(fn, o.pc))
        checks.append({"kind": kind, "at": o.pc, "fn": fn, "defs": defs, "trap": k})
    calls = _callees(mod)
    unproven = {}
    for c in (c for c in mod.entries if c.f.name in calls):
        reach = _reached(calls, c.f.name)
        unproven[c.f.name] = sorted({d for x in checks if x["fn"] in reach for d in x["defs"]})
    for x in checks:
        del x["fn"]
    info = {
        "copies": marks.copies(recs, order, pcs_of),
        "checks": checks,
        "traps": dict(sorted(traps.items())),
        "defs": table,
        "unproven": unproven,
    }

    def line(j):
        rec = recs[j]
        if rec[0] == "pc":
            return next(o.pc for o in rec[4] if o.pc), "6502"
        if rec[0] == "check":
            return index[j] + 1, "check"
        if rec[0] == "arm":
            return line(rec[1])
        if rec[0] == "trap":
            return rec[1] + 1, "trap"
        return 1, rec[1]

    return info, line


def redundant(info):
    """The indices of the checks of ``info["emission"]`` past the least the code needs: each
    whose definitions (some) earlier checks (in text order) already check."""
    done, out = set(), []
    for i, c in enumerate(info["checks"]):
        if c["defs"] and done.issuperset(c["defs"]):
            out.append(i)
        done.update(c["defs"])
    return out


def definition(info, d):
    """Definition ``d`` of ``info["emission"]`` as text: its location and writers."""
    loc, at, byte, by, _ = info["defs"][d]
    what = f"{loc.upper()} in ${at:04X}" if loc != "w" else f"${at + byte:04X}"
    return f"{what} by {'entry' if by is None else f'${by:04X}'}"


def sites(info):
    """``[(kind, pc, detail)]``: the cost sites of ``info["emission"]`` (*Translation cost*),
    ``E1`` per printed copy of an instruction past its first, ``E2`` per :func:`redundant`
    check and per trap path past one per check."""
    out = [
        ("E1", pc, f"copy {i} of {n}") for pc, n in info["copies"].items() for i in range(2, n + 1)
    ]
    for i in redundant(info):
        c = info["checks"][i]
        defs = ", ".join(definition(info, d) for d in c["defs"])
        out.append(("E2", c["at"], f"{c['kind']} of {defs}"))
    per, at = Counter(c["trap"] for c in info["checks"]), {}
    for c in info["checks"]:
        at.setdefault(c["trap"], c["at"])
    for k, n in info["traps"].items():
        for i in range(per[k] + 1, n + 1) if per[k] else ():
            out.append(("E2", at[k], f"trap({k}) path {i} of {n}"))
    return out


def _callees(mod):
    """``{MLIR function: {functions it calls}}``."""
    names = {f.name for f in mod.funcs}
    return {
        f.name: {
            o.prop("callee")[1:]
            for b in f.blocks
            for o in b.ops
            if o.name == "func.call" and (o.prop("callee") or "")[1:] in names
        }
        for f in mod.funcs
    }


def _reached(calls, root):
    """The functions ``root`` calls, through calls, and itself."""
    seen, work = {root}, [root]
    while work:
        for t in calls.get(work.pop(), ()):
            if t not in seen:
                seen.add(t)
                work.append(t)
    return seen
