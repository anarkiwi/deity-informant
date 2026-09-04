"""Section 5's record as the region tree it is: the accumulator procedure written out.

One statement list in the player's own order -- the divider, the decision the
step makes, the reload, the move its policy is, the produce and the gate -- with
a loop, a take or a trap where the row language has no form.
"""

from __future__ import annotations

from .rir import put, read, truth_of

DUE = "$due"  # the divider's own decision, taken before the counter reloads
STEPPED = "$stepped"  # the decision the step makes, before anything moves
HIT = "$hit"  # whether a clamp took its target, which stops the rest of the step
DIR = "$dir"  # the direction a reflect read, before its own turn moves it
MOVES = "$moves"  # whether the step moves, decided before a reload writes the cell


def mask(node, w):
    """A value held to the modulus its record declares."""
    return {"and": [node, (1 << w) - 1]}


def shr(x, k):
    """One value shifted down, where the amplitude states a shift."""
    if not k:
        return x
    return x >> k if isinstance(x, int) else {"shr": [x, k]}


def why(a):
    """Why one section 5 record has no region tree, or ``None`` where it has one."""
    rate = a.get("rate", 1)
    if rate != 1 and not (isinstance(rate, dict) and "cell" in rate and "reload" in rate):
        return "rate: a divider is a counter cell and its reload"
    pol = a.get("policy", "wrap")
    if pol == "reflect" and not isinstance(a.get("phase"), dict):
        return "reflect: the turn moves a direction cell the record does not name"
    if pol in ("reflect", "reflect-complement") and not a.get("amplitude"):
        return "%s: the turn has no amplitude to turn on" % pol
    return None


def produce_sets(a, base):
    """The register writes a record's produce is, each half of the value it emits."""
    return [
        [t, mask(shr(base, 8), 8) if part == "hi" else mask(base, 8)]
        for t, part in a.get("produce", ())
    ]


def divider(a, out, when):
    """The counter a ``rate`` is: one step down, its decision, and its reload."""
    r = a.get("rate", 1)
    if r == 1:
        return when
    cell = r["cell"]
    out.append({"when": when, "sets": [[put(cell), mask({"sub": [read(cell), 1]}, 8)]]})
    out.append({"when": when, "sets": [[put(DUE), {"bit": [read(cell), 7]}]]})
    got = when + [[read(DUE), "!=", 0]]
    out.append({"when": got, "sets": [[put(cell), r["reload"]]]})
    return got


def stepping(a, out, when):
    """``(true arm, false arm)``: the decision the step makes, read where it is made."""
    got = list(a.get("step_when") or [])
    if not (got and a.get("gate")):
        return got, None
    out.append({"when": when, "sets": [[put(STEPPED), truth_of(got)]]})
    return [[read(STEPPED), "!=", 0]], [[read(STEPPED), "==", 0]]


def reads(node, cell):
    """Whether one guard list or value reads the cell a reload is about to write."""
    if isinstance(node, dict):
        return node == read(cell) or any(reads(v, cell) for v in node.values())
    if isinstance(node, list):
        return any(reads(x, cell) for x in node)
    return False


def moving(a, out, when, stept, cell):
    """The step's own guard, decided before a reload writes the cell it reads."""
    got = list(a.get("delta_when") or [])
    pol = a.get("policy", "wrap")
    if not (got and isinstance(pol, dict) and "reload" in pol and reads(got, cell)):
        return when + stept + got
    out.append({"when": when + stept, "sets": [[put(MOVES), truth_of(got)]]})
    return when + stept + [[read(MOVES), "!=", 0]]


def repeat(a, out, gs, base, w):
    """The closed triangle: ``n`` additions of one step, and the carry each leaves."""
    step, n = a["delta"]["repeat"]
    fl = a.get("flag") or {}
    add = {"add": [base, step]}
    if fl:
        out.append({"when": gs, "sets": [["!" + fl["name"], fl["seed"]]]})
    body = [{"sets": [["!" + fl["name"], {"and": [{"shr": [add, w]}, 1]}]]}] if fl else []
    body.append({"sets": [[put(a["cell"]), mask(add, w)]]})
    out.append({"when": gs, "loop": {"trip": n, "body": body}})


def clamp(a, out, gs, base, w):
    """``clamp(target)``: the take where the step passes it, and the move where not."""
    pol = a.get("policy", "wrap")
    tgt, b, d = pol["clamp"], pol.get("edge", 0), a["delta"]
    below, above = [base, "<", tgt], [base, ">=", tgt]
    hi = [{"sub": [{"add": [base, d]}, tgt]}, ">=", b]
    lo = [{"sub": [{"sub": [base, tgt]}, d]}, "<", b]
    out.append({"when": gs + [below, hi], "sets": [[put(HIT), 1]]})
    out.append({"when": gs + [above, lo], "sets": [[put(HIT), 1]]})
    out.append({"when": gs + [[read(HIT), "!=", 0]], "take": read("note")})
    miss, cell = gs + [[read(HIT), "==", 0]], put(a["cell"])
    step = {"add": [d, b]} if b else d
    out.append({"when": miss + [below], "sets": [[cell, mask({"add": [base, step]}, w)]]})
    out.append({"when": miss + [above], "sets": [[cell, mask({"sub": [base, step]}, w)]]})


def turn(a, out, gs, base, up, down):
    """The direction cell a reflect moves, on the bound it turns at or on a count."""
    am, pc = a["amplitude"], a["phase"]["cell"]
    down_step, up_step = {"sub": [read(pc), 1]}, {"add": [read(pc), 1]}
    if "count" in am:
        cc = am["cell"]
        out.append({"when": gs, "sets": [[put(cc), mask({"add": [read(cc), 1]}, 8)]]})
        at = gs + [[read(cc), "==", am["count"]]]
        out.append({"when": at + down, "sets": [[put(pc), mask(down_step, 8)]]})
        out.append({"when": at + up, "sets": [[put(pc), mask(up_step, 8)]]})
        out.append({"when": at, "sets": [[put(cc), 0]]})
        return
    lo, hi, k = am["interval"][0], am["interval"][1], am.get("shift", 0)
    for arm, bound, s in ((down, lo, down_step), (up, hi, up_step)):
        at = [shr(base, k), "==", shr(bound, k)]
        out.append({"when": gs + arm + [at], "sets": [[put(pc), mask(s, 8)]]})


def reflect(a, out, gs, base, w):
    """The triangle that turns: the arm its direction picks, and the turn it takes."""
    out.append({"when": gs, "sets": [[put(DIR), a["phase"]]]})
    up, down = [[read(DIR), "==", 0]], [[read(DIR), "!=", 0]]
    cell, d = put(a["cell"]), a["delta"]
    out.append({"when": gs + down, "sets": [[cell, mask({"sub": [base, d]}, w)]]})
    out.append({"when": gs + up, "sets": [[cell, mask({"add": [base, d]}, w)]]})
    turn(a, out, gs, base, up, down)


def complement(a, out, gs, base, w):
    """The triangle one complement folds: the fold above its amplitude, then the step."""
    m = (1 << w) - 1
    hi, cell = a["amplitude"]["interval"][1], put(a["cell"])
    fold = [[{"and": [base, m ^ m >> 1]}, "==", 0], [base, ">", hi]]
    out.append({"when": gs + fold, "sets": [[cell, {"xor": [base, m]}]]})
    out.append({"when": gs, "sets": [[cell, mask({"add": [base, a["delta"]]}, w)]]})


def wrapped(a, out, gs, base, w):
    """The plain step: one arm, or the two a phase picks between."""
    ph, cell = a.get("phase"), put(a["cell"])
    arms = [("add", [])] if ph is None else [("sub", [[ph, "!=", 0]]), ("add", [[ph, "==", 0]])]
    for op, extra in arms:
        out.append({"when": gs + extra, "sets": [[cell, mask({op: [base, a["delta"]]}, w)]]})


def step_of(a, out, gs, base, w):
    """The move one record's policy is, and the guard a clamp leaves over the rest."""
    pol, d = a.get("policy", "wrap"), a.get("delta")
    if isinstance(d, dict) and "repeat" in d:
        repeat(a, out, gs, base, w)
    elif isinstance(pol, dict) and "clamp" in pol:
        clamp(a, out, gs, base, w)
        return [[read(HIT), "==", 0]]
    elif pol == "reflect":
        reflect(a, out, gs, base, w)
    elif pol == "reflect-complement":
        complement(a, out, gs, base, w)
    else:
        wrapped(a, out, gs, base, w)
    return []


def rows(a):
    """The region tree section 4's accumulator procedure is, for one section 5 record."""
    if why(a) is not None:
        return None
    when, cell, w = list(a.get("when") or []), a.get("cell"), a.get("width", 8)
    if a.get("trap"):
        return [{"when": when, "trap": "the arm the certified horizon never takes"}]
    out, base = [], read(cell) if cell else None
    guard = divider(a, out, when)
    stept, unstept = stepping(a, out, guard)
    gs = moving(a, out, guard, stept, cell) if cell else guard
    pol = a.get("policy", "wrap")
    prod = produce_sets(a, base) if base is not None else []
    if isinstance(pol, dict) and "clamp" in pol:
        out.append({"when": guard, "sets": [[put(HIT), 0]]})
    if isinstance(pol, dict) and "reload" in pol:
        got = guard + list(pol.get("when") or [])
        out.append({"when": got, "sets": [[put(cell), pol["reload"]]]})
    if a.get("emit") == "entry" and prod:
        out.append({"when": guard, "sets": prod})
    hit = []
    if a.get("delta") is not None and base is not None:
        hit = step_of(a, out, gs, base, w)
    if a.get("emit") != "entry" and prod:
        out.append({"when": guard + hit, "sets": prod})
    gate = a.get("gate") or {}
    for arm, extra in (("true", stept if unstept else []), ("false", unstept)):
        got = gate.get(arm)
        if got and extra is not None:
            out.append({"when": guard + extra + hit, "sets": [list(x) for x in got]})
    for r in out:
        if not r.get("when"):
            r.pop("when", None)
    return [{"region": out, "beyond": a["beyond"]}] if a.get("beyond") else out
