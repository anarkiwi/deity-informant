"""T3 -- the tick outside the fetch regions as one producer list, the program gone.

A block is a path temp (an edge in taken, or a fetch resumed there), a phi a
selection by the edge taken, a fetch's exit an exit temp its resumed block reads,
a loop the latch temps that re-run its span; copies substituted, dead temps dropped.
"""

from __future__ import annotations

FALSE = [[], ["k", 0]]


def _is(name, k, truth=1):
    return ["cond", ["bin", "==", ["tmp", name], ["k", k], 1], truth]


def _on(name):
    return ["cond", ["tmp", name], 1]


def flatten(items, loops, rets, reads):
    """``(producers, loops, registers)`` over the lowering's own temp names.

    ``reads`` maps a region key to ``{its own name: the program's}`` for what its
    fetch data reads; a fetch item binds each to the temp its call path names.
    """
    fetches = {it["uid"]: it for it in items if it["kind"] == "fetch"}
    path = {it["uid"]: "p%d" % it["uid"] for it in items if it["kind"] in ("block", "fetch")}
    exits = {uid: "x%d" % uid for uid in fetches}
    resumes, froms, chains, leaves = {}, {}, {}, {}
    for f in fetches.values():
        for i, x in enumerate(f["exits"]):
            froms.setdefault(f["tos"].get(x["from"]), []).append(_is(exits[f["uid"]], i))
            to = f["tos"].get(x["to"])
            if x["to"] == "$exit":
                leaves.setdefault(f["uid"], []).append(i)
            elif to in fetches:
                chains.setdefault(f["uid"], {})[str(i)] = exits[to]
            elif to in path:
                resumes.setdefault(to, []).append(_is(exits[f["uid"]], i))
    exec_of = {it["uid"]: dict(it["exec"]) for it in items if it["kind"] in ("block", "fetch")}
    latch = {(l["header"], luid): n for n, l in enumerate(loops) for luid, _g in l["latches"]}

    def edge(uid, p):
        """The alternatives one predecessor edge into ``uid`` holds under."""
        if (uid, p) in latch:
            return [[_on("g%d" % latch[(uid, p)])]]
        if p in path:
            return [[_on(path[p])] + exec_of[uid].get(p, [])]
        return [[g] + exec_of[uid].get(p, []) for g in froms.get(p, ())]

    out, skips = [], []
    for it in items:
        skips = [s for s in skips if it["path"].startswith(s[0])]
        skip = [_is(x, i, 0) for _p, x, i in skips]
        k, uid = it["kind"], it["block"]
        at = {"block": uid, "rank": tuple(it["rank"])}
        if k in ("block", "fetch"):
            alts = [[]] if it.get("entry") else []
            alts += [g for p, _g in it["exec"] for g in edge(uid, p)]
            alts += [[g] for g in resumes.get(uid, ())]
            alts = [[skip + g, ["k", 1]] for g in alts] + [FALSE]
            out.append({"kind": "let", "name": path[uid], "expr": ["sel", alts], **at})
            if k == "block":
                out[-1]["prefix"] = at["rank"][:-1]
            else:
                out.append(
                    {
                        "kind": "fetch",
                        "guards": [_on(path[uid])],
                        "region": it["region"],
                        "bind": {
                            n: ["tmp", it["path"] + old]
                            for n, old in reads.get(it["region"], {}).items()
                        },
                        "tmps": it["tmps"],
                        "rets": it["rets"],
                        "exit": exits[uid],
                        "chain": chains.get(uid, {}),
                        **at,
                    }
                )
                skips += [(it["path"], exits[uid], i) for i in leaves.get(uid, ())]
            continue
        guards = [_on(path[uid])]
        if k == "let":
            out.append(
                {"kind": "let", "name": it["name"], "expr": it["value"], "guards": guards, **at}
            )
        elif k == "phi":
            order = sorted(it["alts"], key=lambda a, u=uid: (u, a[0]) not in latch)
            alts = [[g, v] for p, v in order for g in edge(uid, p)]
            out.append(
                {"kind": "let", "name": it["name"], "expr": ["sel", alts], "guards": guards, **at}
            )
        else:
            out.append(
                {
                    "kind": "store",
                    "guards": guards,
                    "cls": it["cls"],
                    "w": it["w"],
                    "lo": it["lo"],
                    "hi": it["hi"],
                    "addr": it["addr"],
                    "expr": it["value"],
                    "site": {"pc": it["pc"]},
                    **at,
                }
            )
    loops2 = [
        {
            "header": path[l["header"]],
            "again": "g%d" % n,
            "body": sorted(l["body"]),
            "latches": [[path[luid], g] for luid, g in l["latches"]],
        }
        for n, l in enumerate(loops)
    ]
    registers = {
        "in": [[i, n] for i, n in rets["params"]],
        "out": [[j, ["tmp", n]] for j, n in rets["rets"]],
    }
    return out, loops2, registers


# ---- the table ----------------------------------------------------------------------
def _names(e, out):
    """Every temp an expression reads."""
    if isinstance(e, list):
        if e and e[0] == "tmp":
            out.add(e[1])
        else:
            for x in e:
                _names(x, out)
    return out


def _subst(e, by):
    """An expression with every temp in ``by`` replaced, transitively."""
    if isinstance(e, list):
        if e and e[0] == "tmp":
            got = by.get(e[1])
            return e if got is None else _subst(got, by)
        return [_subst(x, by) for x in e]
    return e


def _slots(it):
    """The expressions one item reads, as ``(holder, key)``."""
    slots = [(it, "guards")]
    if it["kind"] in ("let", "store"):
        slots.append((it, "expr"))
    if it["kind"] == "store":
        slots.append((it, "addr"))
    if it["kind"] == "fetch":
        slots += [(it["bind"], n) for n in it["bind"]]
    return slots


def _apply(items, loops, registers, fn):
    for it in items:
        for holder, key in _slots(it):
            if key in holder:
                holder[key] = _subst(holder[key], fn)
    registers["out"] = [[j, _subst(e, fn)] for j, e in registers["out"]]
    for l in loops:
        l["latches"] = [[name, _subst(g, fn)] for name, g in l["latches"]]


def _defs(items, registers):
    """How many times each temp is assigned."""
    n = {}
    for it in items:
        names = [it["name"]] if it["kind"] == "let" else []
        if it["kind"] == "fetch":
            names = list(it["tmps"].values()) + it["rets"] + [it["exit"]]
        for name in names:
            n[name] = n.get(name, 0) + 1
    for _i, name in registers["in"]:
        n[name] = n.get(name, 0) + 1
    return n


def _live(items, loops, registers):
    got = set()
    for it in items:
        for holder, key in _slots(it):
            _names(holder.get(key, []), got)
    for l in loops:
        got.add(l["header"])
        for name, g in l["latches"]:
            got.add(name)
            _names(g, got)
    for _j, e in registers["out"]:
        _names(e, got)
    return got


def reduce(items, loops, registers):
    """Substitute every single-assignment copy, then drop what nothing reads, to a fixpoint."""
    defs = _defs(items, registers)
    by = {}
    for it in items:
        e = it.get("expr")
        if it["kind"] == "let" and defs[it["name"]] == 1 and e[0] in ("tmp", "k"):
            if e != ["tmp", it["name"]]:
                by[it["name"]] = e
    _apply(items, loops, registers, by)
    items = [it for it in items if not (it["kind"] == "let" and it["name"] in by)]
    while True:
        live = _live(items, loops, registers)
        keep = [it for it in items if it["kind"] != "let" or it["name"] in live]
        if len(keep) == len(items):
            return items
        items = keep


def rename(items, loops, registers):
    """Every temp by its place: ``t<k>`` a value; ``p``/``x``/``g`` a path, an exit, a loop."""
    names = {}

    def new(n):
        if n not in names:
            names[n] = n if n[0] in "pxg" and n[1:].isdigit() else "t%d" % len(names)
        return names[n]

    for it in items:
        if it["kind"] == "let":
            it["name"] = new(it["name"])
        elif it["kind"] == "fetch":
            it["tmps"] = {n: new(g) for n, g in it["tmps"].items()}
            it["rets"] = [new(n) for n in it["rets"]]
            it["exit"] = new(it["exit"])
    registers["in"] = [[i, new(n)] for i, n in registers["in"]]
    for l in loops:
        l["header"], l["again"] = new(l["header"]), new(l["again"])
        l["latches"] = [[new(name), g] for name, g in l["latches"]]
    for n in sorted(_live(items, loops, registers)):
        new(n)  # a temp nothing sets (a refused live-out) is read by its place too
    _apply(items, loops, registers, {n: ["tmp", g] for n, g in names.items() if n != g})
    return items


def index(items, loops):
    """Loops by item index (``first`` the header's path let, ``end`` the body's last item).

    A block's path let gets ``skip``: the first item past everything ranked under
    the block, where a path of 0 resumes.
    """
    for l in loops:
        body = set(l.pop("body"))
        l["first"] = next(
            n for n, it in enumerate(items) if it["kind"] == "let" and it["name"] == l["header"]
        )
        l["end"] = max(n for n, it in enumerate(items) if it.get("block") in body)
    for n, it in enumerate(items):
        pre = it.pop("prefix", None)
        if pre is None:
            continue
        m = n + 1
        while m < len(items) and items[m]["rank"][: len(pre)] == pre:
            m += 1
        if m > n + 1:
            it["skip"] = m
    for it in items:
        it.pop("block", None)
        it.pop("rank", None)
    return sorted(loops, key=lambda l: l["end"] - l["first"])
