"""B7 -- T2's cursor nest as section 3.6's own events, and what a score byte is.

A visit of a fetch region is one row of one voice, and its fields are the values
it stored into the player's own cells: ``dur`` into the clock's, ``note`` into
the cell that indexes the tuning, ``ins`` into the selector's index.  A masked
byte a guard reads is the field the horizon's own visits say it is; the field
list is closed, so a candidate no field explains is refused rather than named.
"""

from __future__ import annotations

import operator

from ..tuneprog.ir import Bin, Const, Store, Var, evalbin
from ..tuneprog.irwalk import addr_split, walk
from .read import Unlowerable


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

    def __init__(self, records, vvar, roles, voices, stride, ordpos, top, seed=None, own=()):
        self.rows, self.voices, self.top = {v: [] for v in range(voices)}, voices, top
        self.seed = seed or [0] * voices
        own = set(own)
        self.supplied = {}
        for rec in sorted(records, key=lambda r: r.get("seq", 0)):
            at = rec["env"].get(vvar)
            if at is None or at % stride or at // stride not in self.rows:
                continue
            v, (st, sites) = at // stride, _stores(rec)

            # a role the tune keeps in one cell for the whole tune is read there,
            # and one it keeps per voice at the copy the visit's own index names
            def _at(key, at=at):
                return roles[key] + (at if roles[key] in own else 0)

            row = {
                "dur": st.get(_at("dur"), 0),
                "note": st.get(_at("note")),
                "ins": st.get(_at("ins")),
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
            orders.append(
                {
                    "play": [play.get(i, 0) for i in range(max(play, default=-1) + 1)],
                    "end": {"jump": 0},
                }
            )
        got = sorted(pats.values(), key=operator.itemgetter(0))
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


def _mask(low, x):
    """``(supplied name, mask)`` where one node is a masked field of a score byte."""
    if type(x) is Bin and x.op == "&" and type(x.b) is Const:
        got = low.expand(x.a)
        return (got.n, x.b.v) if type(got) is Var and got.n in low.v.supplied else None
    return (x.n, None) if type(x) is Var and x.n in low.v.supplied else None


def masks_of(low):
    """``{(supplied name, mask)}``: every masked field of a score byte the tick reads."""
    out = set()
    for lbl, b in low.proc.blocks.items():
        low.lbl, low.local, low.pick = lbl, {}, {}
        for s in list(b.stmts) + [b.term]:
            for e in (getattr(s, "e", None), getattr(s, "v", None), getattr(s, "c", None)):
                out |= {
                    got
                    for x in (walk(e) if e is not None else ())
                    for got in (_mask(low, x),)
                    if got is not None
                }
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
        for key, node in (
            ("dur", {"cell": "dur"}),
            ("note", {"cell": "note"}),
            ("ins", {"cell": "ins"}),
        ):
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


def _ev(e, env):
    """One condition over a byte the score supplied, evaluated; ``None`` where it reads more."""
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
