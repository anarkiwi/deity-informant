"""L3 -- typed PNF: every cell typed by the slot lattice, every table by its kind.

Type inference over a finite lattice, by constraints over uses.  A cell that
indexes the tuning is ``note``; one that indexes the instrument table is ``ins``;
the counter the fetch reloads is ``rowsleft`` and the length it reloads it with
is ``dur``; the order's own cursor is ``orderpos``; the pair a record whose
target is the frequency register moves is ``freq``; a cell that indexes a
declared table is a ``cursor``; a cell the fetch writes and a later phase reads
is ``staging``; the image's own halves are ``shadow``; and everything else is
the tune's private state.  Typing renames and states -- it does not move a
value -- so the level renders exactly what the level before it rendered.
"""

from __future__ import annotations

import copy

from ...tuneprog.ir import Store
from ...tuneprog.irwalk import addr_split
from .. import schedule, tables
from ..shape import _order_cursor, _reads
from .ir import Level

SLOTS = ("note", "ins", "rowsleft", "dur", "orderpos", "freq", "wave", "lastnote")
TABLES = ("pitch", "instrument", "stream", "score", "shadow")
KINDS = {(-1, False): "divider", (-1, True): "countdown", (1, True): "counter"}


def clock_of(prog, proc, fetchblocks, t0, order):
    """B6's schedule over the typed tick: the counter, its step and its clauses."""
    got = [l for l in order if l in fetchblocks]
    if not got:
        return schedule.Schedule(proc)
    return schedule.derive(prog, proc, frozenset(fetchblocks), t0, got[0])


def clockkind(sch):
    """Which value of §3.6's one counter a tune's clock is, from its own data.

    A step of ``-1`` with no clause is the divider the row's own length reloads;
    with one it is a countdown against a boundary; a step of ``+1`` is a counter
    the clauses zero.
    """
    if not sch.clock:
        return None
    return KINDS.get((sch.step, bool(sch.resets)), "counter" if sch.step > 0 else "divider")


def cursors(obj):
    """``{cell: table}``: the cells a declared table is read at (§5's ``tabcell``)."""
    out, stack = {}, [obj["streams"], obj["meta"], obj["accs"]]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "tabcell" and isinstance(v[1], dict) and "cell" in v[1]:
                    out[v[1]["cell"]] = v[0]
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def written(obj, streams):
    """The cells the named streams assign, by name."""
    return {
        s[0].lstrip("@#!*")
        for k in streams
        for r in obj["streams"].get(k, {}).get("rows", ())
        for s in r.get("sets", ())
    }


def readby(obj, streams):
    """The cells the named streams read, by name."""
    return {
        n
        for k in streams
        for r in obj["streams"].get(k, {}).get("rows", ())
        for n in _reads(r.get("when", [])) | _reads([s[1] for s in r.get("sets", ())])
    }


def tabletypes(art, obj, sh):
    """Every table of the object typed: the tuning, the records, the score, the image."""
    out = {}
    t2 = art["t2"]
    for name in obj["streams"]:
        out[name] = "stream"
    if t2.get("pitch", {}).get("entries"):
        out["pitch"] = "pitch"
    for s in t2.get("selectors") or ():
        for col in s.get("columns") or ():
            out[str(col.get("table"))] = "instrument"
    for s in t2.get("score") or ():
        for key in ("order", "pattern"):
            for d in s.get(key) or ():
                out[str(d.get("table"))] = "score"
    if sh:
        out["shadow"] = "shadow"
    return out


def types(l2, sch):  # noqa: C901 - one clause per slot of the lattice
    """``{cell: role}`` over the object's own cells, by the uses that decide them."""
    art, prog, proc = l2.art, l2.prog, l2.proc
    low, cells = l2.facts["reader"], l2.facts["cells"]
    pit = l2.facts["pitch"]
    got = {}

    def put(addr, role):
        if addr is None:
            return
        name = cells.voicecell(addr) if (cells.at(addr) or (None,))[0] == "voice" else None
        if name is not None and name not in got:
            got[name] = role

    if pit is not None:
        put(tables.note_base(low, pit, [prog.procs[proc]]), "note")
    ins = tables.instrument_table(art, art["view"], art["names"])
    if ins:
        put(ins[0], "ins")
    put(_order_cursor(art, art["view"], art["names"]), "orderpos")
    if sch.clock:
        put(sch.clock[3], "rowsleft" if reloaded(l2, sch) else "clock")
    for a in art["t1"].get("accs") or ():
        put(_addr(a["cell"]), "acc")
    for cell, table in cursors(l2.obj).items():
        got.setdefault(cell, "cursor:" + table)
    later = readby(l2.obj, [k for k in l2.obj["streams"] if not k.startswith("row")])
    for name in written(l2.obj, [k for k in l2.obj["streams"] if k.startswith("row")]):
        if name in later and name not in got:
            got[name] = "staging"
    # the image's own halves are no cell of ``state0``: they are the register
    # file, and what names them is the store that lands in it
    for name in written(l2.obj, list(l2.obj["streams"])):
        if name.startswith("shadow."):
            got[name] = "shadow"
    for name in l2.obj["state0"]["cells"]:
        got.setdefault(name, "private")
    for name in l2.obj["state0"].get("globals", {}):
        got.setdefault("#" + name, "private")
    return got


def reloaded(l2, sch):
    """Whether the row's own length reloads the clock: the player's ``rowsleft``."""
    p = l2.prog.procs[l2.proc]
    rows = {l for n, g in l2.facts["segments"] if n == "row" for l in g}
    return any(
        _base(x) == sch.clock[3] for l in rows for x in p.blocks[l].stmts if _base(x) is not None
    )


def _base(s):
    """The constant base one ram store lands at, where it has one."""
    return addr_split(s.a)[0] if type(s) is Store and s.cls == "ram" else None


def _addr(ref):
    return int(str(ref.get("addr", "0")).lstrip("$"), 16)


def rename(node, sub):
    """One object with every cell name the typing settled read by its role."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in ("cell", "global") and isinstance(v, str):
                out[k] = sub.get(v, v)
            elif k == "cell" and isinstance(v, (list, tuple)):
                out[k] = [sub.get(v[0], v[0])] + list(v[1:])
            else:
                out[k] = rename(v, sub)
        return out
    if isinstance(node, list):
        return [rename(x, sub) for x in node]
    if isinstance(node, str):
        return _target(node, sub)
    return node


def _target(name, sub):
    """A ``sets`` target renamed: its prefix kept, its cell read by its role."""
    pre = name[:1] if name[:1] in "@#!*" else ""
    got = sub.get(name[len(pre) :])
    return name if got is None else pre + got


def roles(l2, fetchblocks=None):
    """L2 to L3: the cells typed, and the object read by the roles they are."""
    art, prog, proc = l2.art, l2.prog, l2.proc
    fetchblocks = l2.facts["fetchblocks"] if fetchblocks is None else frozenset(fetchblocks)
    sch = clock_of(prog, proc, fetchblocks, art["t0"], l2.facts["reader"].rpo)
    ty = types(l2, sch)
    taken = {n for n, r in ty.items() if r not in SLOTS}
    sub = {}
    for name, role in sorted(ty.items()):
        if role in SLOTS and role not in sub.values() and role not in taken:
            sub[name] = role
    obj = rename(copy.deepcopy(l2.obj), sub)
    obj["state0"]["cells"] = {sub.get(k, k): v for k, v in obj["state0"]["cells"].items()}
    obj["meta"]["wide"] = sorted({sub.get(n, n) for n in obj["meta"]["wide"]})
    obj["meta"]["tempo"]["cell"] = sub.get(
        obj["meta"]["tempo"]["cell"], obj["meta"]["tempo"]["cell"]
    )
    return Level(
        3,
        art=art,
        prog=prog,
        proc=proc,
        obj=obj,
        facts={
            **l2.facts,
            "types": {sub.get(k, k): v for k, v in ty.items()},
            "renamed": sub,
            "clock": {
                "cell": sub.get(_clockcell(ty), _clockcell(ty)),
                "kind": clockkind(sch),
                "step": sch.step,
                "resets": len(sch.resets),
                "boundary": len(sch.boundary),
            },
            "tables": tabletypes(art, l2.obj, l2.facts["flush"]),
            "schedule": sch,
        },
    )


def _clockcell(ty):
    return next((n for n, r in ty.items() if r in ("rowsleft", "clock")), None)
