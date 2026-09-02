"""B7 -- the coverage of one lift, counted from the object it emitted.

The store sites the tick has, what the lowering made of each, the leaf forms the
rows carry, and where every accumulator T1 states landed. Nothing here decides
anything: it is the report ``tools/tuneprog_trackerprog.py`` writes, and every
number this layer's documents quote of a lift is one of these.
"""

from __future__ import annotations

from ..tuneprog.ir import Store

# the fields of a section 5 record that carry an expression, and no other
EXPRS = ("policy", "delta", "delta_when", "when", "step_when", "phase", "rate", "gate")


def coverage(low, prog, proc, segs, glob, streams, accs, t1got):
    """B7's numbers: the store sites lowered, recognised and refused, and their leaves."""
    p = prog.procs[proc]
    blocks = sum(segs.values(), []) + list(glob)
    sites = [s for l in blocks for s in p.blocks[l].stmts if type(s) is Store and s.cls != "chk"]
    leaves = {}
    for st in streams:
        for r in st["rows"]:
            _leafkinds([x[1] for x in r["sets"]] + list(r.get("when", [])), leaves)
    for a in accs.values():
        _leafkinds([a.get(k) for k in EXPRS if k in a], leaves)
    return {
        "store_sites": len(sites),
        "rows": sum(len(st["rows"]) for st in streams),
        "sets": sum(len(r["sets"]) for st in streams for r in st["rows"]),
        "accs": len(accs),
        "refused": sorted(low.bad - set(low.v.supplied)),
        "leaves": dict(sorted(leaves.items())),
        "t1_accumulators": t1got,
        "t1_recognised": sum(1 for a in t1got if a["form"] == "acc"),
        "t1_refused": [[a["id"], a["cell"], a["why"]] for a in t1got if a["form"] != "acc"],
    }


def _leafkinds(nodes, out):
    """How many of each section 5 leaf form the lowered rows carry."""
    stack = list(nodes)
    while stack:
        x = stack.pop()
        if isinstance(x, int):
            out["const"] = out.get("const", 0) + 1
        elif isinstance(x, (list, tuple)):
            stack += list(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                if k in ("cell", "global", "ins", "flag"):
                    out[k] = out.get(k, 0) + 1
                elif k in ("transpose", "tuned"):
                    out["pitch"] = out.get("pitch", 0) + 1
                    stack.append(v)
                else:
                    out[k] = out.get(k, 0) + 1
                    stack.append(v)
    return out
