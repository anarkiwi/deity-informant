"""L4's specialiser over cursor kinds: a cursor a row walks becomes the stream it is.

A cursor is a cell that steps over a table, so the code that reads the table row
is specialised into the fields the row sets and the step it takes -- the hold, the
next, the jump -- which is the fetch's own specialisation over a second cursor.
"""

from __future__ import annotations

BINOP = {
    "and": lambda a, b: a & b,
    "or": lambda a, b: a | b,
    "xor": lambda a, b: a ^ b,
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "shr": lambda a, b: a >> b,
    "shl": lambda a, b: a << b,
}


def value(node, obj, cell, i):
    """One value with the cursor on row ``i``, worth what it is, or ``None``."""
    if isinstance(node, int):
        return node
    if not isinstance(node, dict) or len(node) != 1:
        return None
    k, a = next(iter(node.items()))
    if k == "cell":
        return i if a == cell else None
    if k == "const":
        return a if isinstance(a, int) else None
    if k == "tabcell":
        y = value(a[1], obj, cell, i)
        rows = (obj["streams"].get(a[0]) or {}).get("rows") or []
        if y is None or not 0 <= y < len(rows) or a[2] not in rows[y]:
            return None
        return value(rows[y][a[2]], obj, cell, i)
    if k in BINOP:
        x, y = value(a[0], obj, cell, i), value(a[1], obj, cell, i)
        return None if x is None or y is None else BINOP[k](x, y)
    return None


def reads(node, cell, table=None):
    """Whether one value reads the cursor, or the table it walks, at all."""
    if isinstance(node, dict):
        if node.get("cell") == cell:
            return True
        if table is not None and node.get("tabcell", [None])[0] == table:
            return True
        return any(reads(v, cell, table) for v in node.values())
    if isinstance(node, list):
        return any(reads(x, cell, table) for x in node)
    return False


def steps(r, cell):
    """Whether one statement is the cursor's own step: it moves the cursor and nothing else."""
    got = r.get("sets") or []
    return len(got) == 1 and got[0][0] == "@" + cell


def touches(r, cell, table):
    """Whether one statement names the cursor or its table anywhere."""
    return reads(r, cell, table) or any(s[0].lstrip("@#!*") == cell for s in r.get("sets") or ())


def at(node, obj, cell, i):
    """One value with every read of the cursor's own row spent, or ``None``."""
    got = value(node, obj, cell, i)
    if got is not None:
        return got
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            got = at(v, obj, cell, i) if not isinstance(v, str) else v
            if got is None:
                return None
            out[k] = got
        return out
    if isinstance(node, list):
        out = [at(x, obj, cell, i) for x in node]
        return None if any(x is None for x in out) else out
    return node


def rowsof(obj, rows, cell, table, read, step):
    """The stepped stream a cursor walk is: one row of the table, one step of the stream."""
    out = []
    for i in range(len(obj["streams"][table]["rows"])):
        sets = []
        for k in read:
            for t, v in rows[k]["sets"]:
                got = at(v, obj, cell, i)
                if got is None:
                    return None
                sets.append([t, got])
        nxt = value(rows[step]["sets"][0][1], obj, cell, i)
        if nxt is None:
            return None
        out.append({"sets": sets, **({"next": nxt} if nxt != i + 1 else {})})
    return out


def walk(obj, name, cell, table):
    """``(rows, read, step)`` where one phase's statements walk a cursor, else ``None``."""
    rows = obj["streams"][name]["rows"]
    if any(not isinstance(r, dict) or "sets" not in r for r in rows):
        return None
    move = [i for i, r in enumerate(rows) if steps(r, cell)]
    read = [i for i, r in enumerate(rows) if reads(r.get("sets"), cell, table) and i not in move]
    if not read or len(move) != 1 or move[0] < max(read):
        return None
    if any(rows[i].get("when") for i in read + move):
        return None
    if any(touches(r, cell, table) for i, r in enumerate(rows) if i not in read + move):
        return None
    got = rowsof(obj, rows, cell, table, read, move[0])
    return None if got is None else (got, read, move[0])


def phase(obj):
    """The one ``{stream}`` phase of the tick a cursor's own stream can be ranked in."""
    tick = obj["meta"]["tick"]
    got = [e["stream"] for e in tick if not isinstance(e, str)]
    return got[0] if len(got) == 1 and "machine" not in tick else None


def place(obj, name, table, rows, read, step):
    """The machine's rank order: what stood before the walk, the stream, what stood after."""
    old = obj["streams"].pop(name)
    keep = {k: v for k, v in old.items() if k not in ("rows", "rank")}
    spent = set(read) | {step}
    runs = [
        [r for i, r in enumerate(old["rows"]) if i < min(spent) and i not in spent],
        [r for i, r in enumerate(old["rows"]) if i > max(spent) and i not in spent],
    ]
    obj["meta"]["tick"] = [
        "machine" if not isinstance(e, str) and e["stream"] == name else e
        for e in obj["meta"]["tick"]
    ]
    if runs[0]:
        obj["streams"]["%s0" % name] = {**keep, "rows": runs[0], "rank": 0}
    obj["streams"][table] = {"rows": rows, "rank": 1}
    # a machine phase runs the record every voice enters holding, so an object
    # whose score names no instrument still states the one
    obj["instruments"] = obj["instruments"] or {"0": {}}
    if runs[1]:
        obj["streams"]["%s2" % name] = {**keep, "rows": runs[1], "rank": 2}
    return obj


def specialise(obj, cursors, seeds):
    """Every cursor a phase walks over a declared table, specialised into its stream."""
    got = {}
    name = phase(obj)
    if name is None:
        return obj, got
    for cell, table in sorted(cursors.items()):
        if name not in obj["streams"] or table not in obj["streams"]:
            continue
        found = walk(obj, name, cell, table)
        if found is None:
            continue
        place(obj, name, table, *found)
        obj["state0"].setdefault("cursors", {})[table] = [
            {"row": int(x), "hold": 0} for x in seeds.get(cell, [0])
        ]
        got[cell] = table
        break
    return obj, got


def left(obj, cursors, done):
    """The cursors this level did not specialise, each with the stream it walks."""
    del obj
    return {c: t for c, t in cursors.items() if c not in done}
