"""B7 -- the shapes the binding assembles into, and what it drops on the way.

The blocks a segment is, the cursor a score channel steps, the record one
instrument is, and the liveness that takes out every assignment no record, no
register and no word past the tuning reads.
"""

from __future__ import annotations

from ..tuneprog.graph import cfg, idoms, natural_loops, preds_of
from . import emit, region, schedule, tables
from .refuse import Refusal, Refused
from .tree import body, kept, stmts

TRAP = "Trap"
KEEP = (
    "rowsleft",
    "dur",
    "note",
    "ins",
    "freq",
    "orderpos",
    "tied",
    "phase",
    "counter",
    "voice_index",
    "lastnote",
    "wave",
)


def _live(prog, proc, blocks):
    """The blocks of a set the program can reach: a trap is no block of a phase."""
    p = prog.procs[proc]
    return [l for l in blocks if type(p.blocks[l].term).__name__ != TRAP]


def _rowblocks(prog, proc, rowr):
    """The blocks the ``row`` segment is: the fetch regions, and where they rejoin.

    A region's exit is the row's where the fetch alone reaches it, and the
    machine's where the voice loop closes on it -- a latch runs on every turn.
    """
    got = {l for r in rowr for l in r.blocks}
    latches = schedule.voice_loop(prog, proc, frozenset(got))[1][1]
    return got | ({r.exit for r in rowr} - set(latches))


def _channels(prog, proc, fetch, pattables):
    """The fetch regions the ``row`` is: those a pattern table is read in.

    T2 names the table each channel of the score reads, so a region that reads no
    pattern table is a walk of the order list, which the object states as a table
    read at a cursor (section 3.3) like any other.
    """
    got = region.score_loads(prog.procs[proc], pattables) if pattables else set()
    rowr = [r for r in fetch.regions.values() if r.proc == proc and r.blocks & got]
    return rowr or [r for r in fetch.regions.values() if r.proc == proc]


def _order_cursor(art, view, names):
    """The cell the fetch steps at a pattern's end: T2's own order cursor."""
    regs = emit.by_name(view, names)
    for v in art["t2"]["score"]:
        for ch in v.get("order", ()):
            name, _at, addr = ch["cursor"].partition("@$")
            if addr:
                return int(addr, 16)
            r = regs.get(name)
            if r is not None:
                return r.base
    return None


def _latches(prog, proc, sch):
    """The blocks that close the voice loop: where its index is rebound."""
    p = prog.procs[proc]
    g = cfg(p)
    return natural_loops(g, idoms(p, g), preds_of(p)).get(sch.head, (set(), set()))[1]


def _resets(low, cell, sch):
    """Section 3.6's ``reset`` clauses: what the tick does to the counter at its end."""
    out = [
        {
            "when": [low.guard(c, t) for c, t in guard],
            "sets": [["@" + cell, low.guard_value(st.v)]],
        }
        for st, guard in sch.resets
    ]
    return {"reset": out} if out else {}


def _instruments(art, view, names, ins, pwcols, img, accs):
    """One record per entry of T2's selector: its columns, and its pulse pair.

    A record is named by what the cell that selects it holds, which T2 states.
    """
    addr, cols, stride, entries, keys = ins
    org = {rid: tables._origin_of(view, rid) for rid in set(cols) | set(pwcols)}
    # a record stands where the selecting cell's own value puts it: the value is
    # the record's number in one family and the offset it already is in another
    offsets = all(k == j * stride for j, k in enumerate(keys))
    out = {}
    for i in range(entries):
        at = keys[i] if offsets else keys[i] * stride
        rec = {name: int(img[org[rid] + at]) for rid, name in cols.items()}
        pw = [0, 0]
        for rid, part in pwcols.items():
            pw[0 if part == "lo" else 1] = int(img[org[rid] + at])
        rec["pw"] = pw
        rec["accs"] = [{"acc": k} for k in accs]
        out[str(keys[i])] = rec
    del art, names, addr, view
    return out


def _need(got, why, cell, detail):
    """A datum the binding cannot do without: a refusal by name, and no object."""
    if got in (None, (), []):
        raise Refused([Refusal(why, cell, "", detail)])
    return got


# the event fields section 3.6 lists that a guard may read as a cell of the player
FACTCELL = {
    "dur": {"cell": "dur"},
    "tie": {"cell": "tied"},
    "note": {"cell": "note"},
    "ins": {"cell": "ins"},
}


def _rename(cells, roles):
    """The player's own slots, bound to the addresses S6 and T2 name (§5)."""
    for name, addr in roles.items():
        if addr is not None:
            cells.rename[addr] = name


def _u16name(names, rid):
    """The name S6 gives the word a region is the low half of, where it names one."""
    for (lo, _hi), name in (names.u16 or {}).items():
        if lo[0] == rid:
            return name
    return None


def _flags(node):
    """The flag names one expression reads: section 5's one carry channel."""
    out, stack = set(), [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "flag":
                    out.add(v)
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _merge_halves(obj):
    """A cell the object names by its halves, seeded as the one word it is."""
    got = obj["state0"]["cells"]
    for name in [n for n in list(got) if n.endswith(".lo")]:
        base = name[:-3]
        hi = got.pop(base + ".hi", None)
        lo = got.pop(name)
        got[base] = [a | (b << 8 if hi else 0) for a, b in zip(lo, hi or lo)]
    return obj


def _reads(node):
    """Every cell one expression reads, by name."""
    out, stack = set(), [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "cell":
                    out.add(v if isinstance(v, str) else v[0])
                elif k == "global":
                    out.add(v)
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


class _Out:
    """The object under construction: its streams, its accumulators and their ranks."""

    def __init__(self):
        self.streams, self.accs, self.items = {}, {}, []

    def stream(self, name, rows, rank=None):
        st = {"rows": rows, "all": True}
        if rank is not None:
            st["rank"] = rank
        self.streams[name] = st
        return name


def _rows_of(steps, kinds):
    """Consecutive steps of one kind as guarded rows of one stream."""
    out = []
    for _lbl, kind, when, sets, _d in steps:
        if kind not in kinds:
            continue
        out.append({"when": when, "sets": [list(x) for x in sets]})
    return out


def _tables(obj):
    """The declared streams a ``tabcell`` reads: one row a byte of a const table."""
    out, stack = set(), [obj["streams"], obj["accs"], obj["meta"], obj["instruments"]]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "tabcell":
                    out.add(v[0])
                stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _needed(target):
    """Whether one ``sets`` target is a register the chip has, and not a cell."""
    return target[:1] not in "@#!*"


def _control(s):
    """What a statement no assignment of it can make dead reads: its own control."""
    if "sets" in s and body(s) is None and "take" not in s:
        return None
    return [(s.get("loop") or {}).get("trip"), s.get("take"), s.get("when")]


def _dce(obj):
    """Drop the assignments whose cell nothing the object states reads.

    Liveness and not a count of readers: a cell two dead rows pass between them
    is dead, so the live set grows from the roots -- the registers, the records,
    the score and the words past the tuning -- through the rows that write them.
    """
    rows = [r for st in obj["streams"].values() for r in stmts(st["rows"]) if "sets" in r]
    rows += [s for s in obj["meta"]["row"] if "sets" in s]
    nodes = [(r, k) for r in rows for k in range(len(r["sets"]))]
    live = set(KEEP) | {a["cell"].lstrip("#").split(".")[0] for a in obj["accs"].values()}
    for part in ("accs", "score", "globals", "instruments"):
        live |= _reads(obj[part])
    live |= _reads(obj["meta"]["tempo"]) | _reads([s.get("when") for s in obj["meta"]["row"]])
    live |= _reads([st.get("beyond") for st in obj["streams"].values()])
    live |= _reads([_control(s) for st in obj["streams"].values() for s in stmts(st["rows"])])
    keep = set()
    for _ in range(len(nodes) + 1):
        more = set()
        for r, k in nodes:
            if (id(r), k) in keep:
                continue
            t = r["sets"][k][0]
            if _needed(t) or t.lstrip("@#!*").split(".")[0] in live:
                more.add((id(r), k))
        if not more:
            break
        keep |= more
        for r, k in nodes:
            if (id(r), k) in keep:
                live |= _reads(r["sets"][k][1]) | _reads(r.get("when", []))
    for r in rows:
        r["sets"] = [x for k, x in enumerate(r["sets"]) if (id(r), k) in keep]
    for st in obj["streams"].values():
        st["rows"] = kept(st["rows"], lambda r: r.get("sets") or "sets" not in r)
    obj["meta"]["row"] = [s for s in obj["meta"]["row"] if "sets" not in s or s["sets"]]
    named = {s["stream"] for s in obj["meta"]["row"] if "stream" in s}
    named |= {e["stream"] for e in obj["meta"]["tick"] if not isinstance(e, str)}
    named |= set(obj["globals"].get("streams", ()))
    named |= {n for n in obj["streams"] if _tables(obj) & {n}}
    obj["streams"] = {
        k: v for k, v in obj["streams"].items() if v["rows"] and (k in named or "rank" in v)
    }
    obj["meta"]["row"] = [
        s for s in obj["meta"]["row"] if "stream" not in s or s["stream"] in obj["streams"]
    ]
    obj["meta"]["tick"] = [
        e for e in obj["meta"]["tick"] if isinstance(e, str) or e["stream"] in obj["streams"]
    ]
    obj["globals"]["streams"] = [
        k for k in obj["globals"].get("streams", ()) if k in obj["streams"]
    ]
    if not obj["globals"]["streams"]:
        del obj["globals"]["streams"]
    return obj


def _offsets(node, got):
    """Every constant transposition one part of the object states (§3.2)."""
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "transpose" and isinstance(v, int):
                    got.append(v)
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return got


def _transposed(streams):
    """How far past the tuning a transposition of the object's own can reach (§3.2)."""
    got = [0]
    for st in streams.values():
        for r in st["rows"]:
            _offsets([r.get("when", []), [x[1] for x in r["sets"]]], got)
    return max(got)
