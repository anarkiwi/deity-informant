"""The hermetic snippet the expansions are checked against, and the arm's own numbers.

A construct is rendered by the player over a one-phase object that runs it and
nothing else; its expansion is rendered over the same object with the rows in
its place.  Where a record names its numbers rather than stating them, the arm
that names them is the object's own, and the expansion carries it.
"""

import copy


def armfor(obj, name):
    """The arm the object gives one record: an instrument's, a command's, or none."""
    for rec in obj.get("instruments", {}).values():
        for x in rec.get("accs", ()) or ():
            if isinstance(x, dict) and x.get("acc") == name:
                return dict(x)
    for cmd in _commands(obj):
        for x in cmd.get("arms", ()) or ():
            if isinstance(x, dict) and x.get("acc") == name:
                return dict(x)
    for rec in obj.get("instruments", {}).values():
        for x in rec.get("accs", ()) or ():
            if x == name:
                return {"acc": name}
    return {"acc": name}


def _commands(obj):
    """Every §3.6 command the object states: the score's own, and the rows' inline."""
    out = list((obj.get("score", {}).get("commands") or {}).values())
    for pat in (obj.get("score", {}).get("patterns") or {}).values():
        for e in pat.get("events", ()):
            got = e.get("arm")
            for c in got if isinstance(got, list) else [got]:
                if isinstance(c, dict):
                    out.append(c)
    return out


def bind(node, ov):
    """One expansion with the arm's own numbers where the record names them."""
    if isinstance(node, dict):
        if len(node) == 1 and isinstance(node.get("const"), str) and node["const"] in ov:
            return bind(ov[node["const"]], ov)
        if "sets" in node:
            got = [[t, bind(v, ov)] for t, v in node["sets"]]
            return {**{k: bind(v, ov) for k, v in node.items() if k != "sets"}, "sets": got}
        return {k: bind(v, ov) for k, v in node.items()}
    if isinstance(node, list):
        return [bind(x, ov) for x in node]
    if isinstance(node, str) and node in ov and not isinstance(ov[node], str):
        return bind(ov[node], ov)
    return node


def armed(rows, arm):
    """One expansion under the arm's own guard, which the player takes before the step."""
    when = list((arm or {}).get("when") or [])
    if not when:
        return rows
    return [{**r, "when": when + [t for t in (r.get("when") or []) if t not in when]} for r in rows]


def _wide(rows):
    """The cells a run of rows writes as more than a byte, by the mask they use."""
    out = set()
    for r in rows or ():
        for t, v in r.get("sets", ()) or ():
            m = v.get("and", [None, 0])[1] if isinstance(v, dict) else 0
            if t.startswith("@") and isinstance(m, int) and m > 0xFF:
                out.add(t[1:])
    return out


def snippet(obj, ticks, name=None, rows=None, arm=None):  # noqa: C901 - one clause a section
    """A hermetic object that runs one construct, and nothing else, for ``ticks``.

    The tick is the machine phase and its commit; the score, the row program and
    the clock are stripped, so what the render shows is the construct's own moves.
    """
    o = copy.deepcopy(obj)
    m, n = o["meta"], o["meta"]["voices"]
    m["tick"] = ["machine", "commit"]
    m["row"], m["row_consumes_tick"] = [], False
    m.pop("stage", None)
    m["tempo"] = {"cell": "$phase", "step": 0, "rate": 1, "phase": 0, "boundary": [[0, "!=", 0]]}
    m["horizon"] = ticks
    o["score"] = {"patterns": {}, "orders": [{"play": [], "end": "stop"} for _ in range(n)]}
    o["globals"] = {k: v for k, v in o.get("globals", {}).items() if k in ("flags", "commit")}
    for st in o["streams"].values():
        st.pop("rank", None)
    o["state0"] = {k: v for k, v in o["state0"].items() if k not in ("prologue", "stopped")}
    o["state0"].setdefault("cells", {})["$phase"] = [0] * n
    if name is not None:
        o["accs"] = {name: {**obj["accs"][name], "rank": 0}}
        arms = [arm or {"acc": name}]
    else:
        o["accs"] = {}
        o["streams"]["$expansion"] = {"rows": rows, "all": True, "rank": 0}
        # a record's own modulus is its width; a row states it by the mask it
        # writes through, and the object carries the cell's width in ``meta.wide``
        m["wide"] = sorted(set(m.get("wide", ())) | _wide(rows))
        arms = []
    o["instruments"] = {
        k: {**v, "accs": arms, "prelude": None} for k, v in o.get("instruments", {}).items()
    }
    o["instruments"].setdefault("0", {"accs": arms})
    m["instrument"] = {**m.get("instrument", {}), "accs": arms, "prelude": None}
    k = sorted(o["instruments"], key=lambda x: int(x))[0]
    o["state0"]["ins"] = [int(k)] * n
    return o
