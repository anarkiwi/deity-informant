"""T2 -- the pitch table materialised: the values read, not the bytes stored."""

from __future__ import annotations

from ..tuneprog.recover import _freq_layout, _layouts


def regions(prog, names):
    """The ``freq_table`` regions, and every region inside the span they cover."""
    rgn = prog.by_id()
    named = [rgn[r] for r, role in names.role.items() if role == "freq_table" and r in rgn]
    if not named:
        return []
    lo, hi = min(r.base for r in named), max(r.base + r.size for r in named)
    return sorted(r.id for r in prog.storage if r.id >= 0 and r.base < hi and lo < r.base + r.size)


def table(prog, names):
    """``{regions, layout, entries: [u16], below}`` or ``None`` without a freq table."""
    rgn = prog.by_id()
    rids = regions(prog, names)
    named = [r for r in rids if names.role.get(r) == "freq_table"]
    if not named:
        return None
    data = b"".join(bytes(rgn[r].init) for r in sorted(named, key=lambda r: rgn[r].base))
    lay = _freq_layout(data)
    if lay is None:
        return None
    name, n, cut = lay
    lo, hi = next((l, h) for k, l, h in _layouts(data, n) if k == name)
    return {
        "regions": rids,
        "layout": name,
        "entries": [(h << 8) | l for l, h in zip(lo, hi)],
        "below": cut,
        "tuning": "12-TET",
    }


def accessors(accs, prog, names, rids):
    """Each read of the pitch table: its origin relative to the table, its shift, its cursor."""
    rgn = prog.by_id()
    out, seen = [], set()
    for a in accs:
        if a.table not in rids:
            continue
        origin = a.origin - rgn[a.table].base
        cur = None if a.cursor is None else names.of(a.cursor.region)
        key = (a.table, origin, a.shift, cur)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "table": names.of(a.table),
                "origin": origin,
                "shift": a.shift,
                "cursor": cur,
                "site": {"proc": a.site[0], "block": a.site[1]},
            }
        )
    return sorted(out, key=lambda x: (x["table"], x["origin"], x["shift"], x["cursor"] or ""))
