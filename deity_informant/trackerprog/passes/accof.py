"""The section 5 record a region tree expands to: :mod:`.accex`'s own inverse.

The expansion is deterministic, so the reading is its mirror: the markers the
statement list carries -- a divider's decision, the step's, a clamp's take, a
loop, a direction cell -- name the policy, and the rest is read off in order.
"""

from __future__ import annotations

from .accex import DIR, DUE, HIT, MOVES, STEPPED, mask, shr
from .rir import cellof, common, isreg, put, read, selfstep, sets1, untruth


def _isprod(r, base):
    """Whether one statement is a record's produce: each half of the value it emits."""
    got = r.get("sets") or []
    if not got or base is None or not isreg(r):
        return False
    return all(v in (mask(base, 8), mask(shr(base, 8), 8)) for _t, v in got)


def _ishi(v):
    return (
        isinstance(v, dict)
        and "and" in v
        and isinstance(v["and"][0], dict)
        and "shr" in v["and"][0]
    )


def _cell_of(stmts):
    """The cell a run of statements moves: its own step, its loop, or its store."""
    for s in stmts:
        if "loop" in s:
            return cellof(s["loop"]["body"][-1]["sets"][0][0])
        if selfstep(s) is not None:
            return selfstep(s)[0]
    for s in stmts:
        got = s.get("sets") or []
        if len(got) == 1 and got[0][0].startswith(("@", "#")):
            name = cellof(got[0][0])
            if not name.startswith("$"):
                return name
    return _produced(stmts)


def _produced(stmts):
    """The cell a produce reads, where the record moves none of its own."""
    for s in stmts:
        if not isreg(s):
            continue
        v = (
            (s["sets"][0][1] or {}).get("and", [None])[0]
            if isinstance(s["sets"][0][1], dict)
            else None
        )
        v = v.get("shr", [None])[0] if isinstance(v, dict) and "shr" in v else v
        if isinstance(v, dict) and ("cell" in v or "global" in v):
            return v.get("cell") or "#" + v["global"]
    return None


def _divider(stmts, i, guard, a):
    """The counter a ``rate`` was, read back off the three statements it is."""
    if i + 2 >= len(stmts) or not sets1(stmts[i + 1], put(DUE)):
        return i, guard
    cell = cellof(stmts[i]["sets"][0][0])
    a["rate"] = {"cell": cell, "reload": stmts[i + 2]["sets"][0][1]}
    return i + 3, guard + [[read(DUE), "!=", 0]]


def _stepping(stmts, i, a):
    """``(i, true arm, false arm)``: the decision the step made, where it is stated."""
    if i < len(stmts) and sets1(stmts[i], put(STEPPED)):
        got = untruth(stmts[i]["sets"][0][1])
        if got is not None:
            a["step_when"] = got
        return i + 1, [[read(STEPPED), "!=", 0]], [[read(STEPPED), "==", 0]]
    return i, [], None


def _stepguard(s, when, a, drop=None):
    """The guard the step stood under, past the record's own and the arm it picked."""
    got = [t for t in (s.get("when") or []) if t not in when and (drop is None or t[0] != drop)]
    if got:
        a["delta_when"] = got
    return got


def _repeat(loop, rest, a, when):
    """The closed triangle: the step it repeats, its count and the carry it leaves."""
    body = loop["loop"]["body"]
    add = body[-1]["sets"][0][1]
    a["delta"] = {"repeat": [add["and"][0]["add"][1], loop["loop"]["trip"]]}
    a["width"] = add["and"][1].bit_length()
    if len(body) > 1:
        a["flag"] = {"name": rest[0]["sets"][0][0][1:], "seed": rest[0]["sets"][0][1]}
    _stepguard(loop, when, a)


def _clamp(rest, a, when):
    """``clamp(target)`` read back: its target, its edge and the delta it moves by."""
    first = rest[0]
    a["policy"] = {"clamp": first["when"][-2][2]}
    b = first["when"][-1][2]
    if b:
        a["policy"]["edge"] = b
    _cell, _op, d, w = selfstep(next(s for s in rest if selfstep(s) is not None))
    a["delta"] = d["add"][0] if b else d
    a["width"] = w
    _stepguard(next(s for s in rest if "take" in s), when, a, read(HIT))


def _amplitude(rest, a, pc):
    """The bound a reflect turns at: an interval of the value, or a count of its steps."""
    if rest and cellof(rest[0]["sets"][0][0]) != pc:
        a["amplitude"] = {"cell": cellof(rest[0]["sets"][0][0]), "count": rest[1]["when"][-2][2]}
        return
    at = [s["when"][-1] for s in rest[:2]]
    lhs = at[0][0]
    k = lhs["shr"][1] if isinstance(lhs, dict) and "shr" in lhs else 0
    got = {"interval": [_unshr(at[0][2], k), _unshr(at[1][2], k)]}
    a["amplitude"] = {**got, **({"shift": k} if k else {})}


def _reflect(rest, a, when):
    """The triangle that turns: its direction cell, its delta and its amplitude."""
    a["policy"] = "reflect"
    a["phase"] = rest[0]["sets"][0][1]
    _cell, _op, d, w = selfstep(rest[2])
    a["delta"], a["width"] = d, w
    _amplitude(rest[3:], a, a["phase"].get("cell"))
    _stepguard(rest[0], when, a)


def _unshr(x, k):
    return x << k if isinstance(x, int) and k else x


def _isxor(s, base, nxt=None):
    """Whether one statement is the fold a ``reflect-complement`` takes above its bound."""
    got = s.get("sets") or []
    if len(got) != 1 or not isinstance(got[0][1], dict) or got[0][1].get("xor", [None])[0] != base:
        return False
    step = selfstep(nxt) if nxt else None
    return step is not None and got[0][1]["xor"][1] == (1 << step[3]) - 1


def _wrapped(rest, a, when):
    """The plain step: the delta it moves by, and the phase its two arms pick between."""
    steps = [s for s in rest if selfstep(s) is not None]
    if not steps:
        return
    _cell, _op, d, w = selfstep(steps[-1])
    a["delta"], a["width"] = d, w
    ph = None
    if len(steps) > 1:
        diff = [t for t in (steps[0].get("when") or []) if t not in (steps[-1].get("when") or [])]
        if len(diff) == 1:
            ph = a["phase"] = diff[0][0]
    _stepguard(steps[-1], when, a, ph)


def _isturn(s, pc):
    return selfstep(s) is not None and selfstep(s)[0] == pc


def _span(body, cell, base):
    """``(start, end)`` of the statements the record's own move is, or ``None``."""
    for j, s in enumerate(body):
        if "loop" in s:
            got = (body[j - 1].get("sets") or [["", 0]])[0][0] if j else ""
            return (j - 1 if got.startswith("!") else j), j + 1
        if "take" in s:
            return j - 2, j + 3
        if sets1(s, put(DIR)):
            pc = (s["sets"][0][1] or {}).get("cell")
            return j, j + 3 + (2 if j + 3 < len(body) and _isturn(body[j + 3], pc) else 4)
        if _isxor(s, base, body[j + 1] if j + 1 < len(body) else None):
            return j, j + 2
    if cell is None:
        return None
    steps = [j for j, s in enumerate(body) if (selfstep(s) or (None,))[0] == cell]
    return (steps[0], steps[-1] + 1) if steps else None


def _isstore(s):
    """Whether one statement is a single assignment to a cell the record could name."""
    got = s.get("sets") or []
    if len(got) != 1 or not got[0][0].startswith(("@", "#")):
        return False
    return not cellof(got[0][0]).startswith("$")


def _split(body):
    """``(head, tail)`` where no move stands between them: the reload, then the rest."""
    return (body[:1], body[1:]) if body and _isstore(body[0]) else ([], body)


def _policy(rest, a, base, when):
    """The move the statements are, and the fields the record states it with."""
    loop = next((s for s in rest if "loop" in s), None)
    if loop is not None:
        return _repeat(loop, rest, a, when)
    if any("take" in s for s in rest):
        return _clamp(rest, a, when)
    if rest and sets1(rest[0], put(DIR)):
        return _reflect(rest, a, when)
    fold = next((s for s in rest if _isxor(s, base, rest[-1])), None)
    if fold is not None:
        a["policy"] = "reflect-complement"
        a["amplitude"] = {"interval": [0, fold["when"][-1][2]]}
    return _wrapped([s for s in rest if s is not fold], a, when)


def _reload(rest, a, cell, guard):
    """The policy a store into the record's own cell that reads no delta is."""
    if not (rest and sets1(rest[0], put(cell)) and selfstep(rest[0]) is None):
        return rest
    extra = [t for t in (rest[0].get("when") or []) if t not in guard]
    a["policy"] = {"reload": rest[0]["sets"][0][1], **({"when": extra} if extra else {})}
    return rest[1:]


def record(stmts):
    """The section 5 record one region tree is the expansion of, or ``None``."""
    if not stmts:
        return None
    stmts, beyond = list(stmts), None
    if len(stmts) == 1 and "region" in stmts[0]:
        beyond, stmts = stmts[0].get("beyond"), list(stmts[0]["region"])
    if len(stmts) == 1 and "trap" in stmts[0]:
        got = {"trap": True}
        return {**got, "when": stmts[0]["when"]} if stmts[0].get("when") else got
    when = common(stmts)
    a, i = {"policy": "wrap", "produce": [], "width": 8}, 0
    i, guard = _divider(stmts, i, list(when), a)
    i, stept, unstept = _stepping(stmts, i, a)
    moves = []
    if i < len(stmts) and sets1(stmts[i], put(MOVES)):
        got = untruth(stmts[i]["sets"][0][1])
        if got is not None:
            a["delta_when"] = got
        moves, i = [[read(MOVES), "!=", 0]], i + 1
    if i < len(stmts) and sets1(stmts[i], put(HIT), 0):
        i += 1
    cell = _cell_of(stmts[i:])
    base = read(cell) if cell else None
    if cell:
        a["cell"] = cell
    body = stmts[i:]
    span = _span(body, cell, base)
    head, tail = (body[: span[0]], body[span[1] :]) if span else _split(body)
    head = _reload(head, a, cell, guard) if cell else head
    if head and _isprod(head[0], base):
        a["produce"], a["emit"] = _produce(head[0]), "entry"
    prod = [s for s in tail if _isprod(s, base)]
    if prod and "emit" not in a:
        a["produce"] = _produce(prod[0])
    gate = _gatearms([s for s in tail if s not in prod], unstept)
    _policy(body[span[0] : span[1]] if span else [], a, base, guard + stept + moves)
    if gate:
        a["gate"] = _gate(gate, unstept)
    if when:
        a["when"] = when
    if beyond:
        a["beyond"] = beyond
    a["bound"] = {
        "interval": [0, (1 << a["width"]) - 1],
        "from": "projected",
        "witness": "the mask the statement writes through",
    }
    return a


def _gatearms(tail, unstept):
    """The record's own gate: where no decision picked the arm, a step is another's."""
    if unstept is not None:
        return tail
    out = []
    for s in tail:
        if selfstep(s) is not None:
            break
        out.append(s)
    return out


def _gate(gate, unstept):
    """The arms a record's gate is, each read off the decision its guard names."""
    out = {}
    for s in gate:
        false = unstept is not None and unstept[0] in (s.get("when") or [])
        out["false" if false else "true"] = [list(x) for x in s["sets"]]
    return out


def _produce(s):
    """The produce a statement is: the register each half of the value goes to."""
    return [[t, "hi" if _ishi(v) else "lo"] for t, v in s["sets"]]
