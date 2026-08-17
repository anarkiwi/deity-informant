"""S2 -- sibling closure: every copy of a template gets the arms its siblings ran.

The trace-closed product makes k copies of one routine k different programs. This
lifts each executed instruction into the copies that never reached it, under that
copy's own operands, so the front end decompiles one shape k times.
"""

from __future__ import annotations

from jennings.opcodes import MODE_LEN, OPCODES as OPS

from .siblings import operand
from .tracedata import Trace, rekey, site_key

REFUSE = ("JSR", "RTS", "RTI", "BRK")  # needs call/return records the trace has not
NEAR = 0x100  # how far an indexed access may sit from its operand


def _target(image, pc, kind, cells):
    """The successor of ``pc`` on an edge of ``kind``, when the instruction says it.

    A patched operand says nothing -- the target is the cell's value, so it comes
    from the sibling row instead.
    """
    mn, mode = OPS[image[pc]]
    ln = MODE_LEN[mode]
    patched = any((pc + k) & 0xFFFF in cells for k in range(1, ln))
    if mode == "rel":
        if kind != "br_taken":
            return (pc + 2) & 0xFFFF
        rel = image[(pc + 1) & 0xFFFF]
        return None if patched else (pc + 2 + (rel - 256 if rel & 0x80 else rel)) & 0xFFFF
    if mn == "JMP" and mode == "abs":
        return None if patched else operand(image, pc)
    if mode == "ind" or mn in REFUSE:
        return None
    return (pc + ln) & 0xFFFF


def _mapset(addrs, base, delta):
    """One op's address set, moved to the copy whose operand sits ``delta`` on.

    An access based at the instruction's own operand moves with it; one that is
    not (the stream a ``(zp),Y`` pointer reaches) is the same object in every
    copy and stays.
    """
    if not addrs or delta is None or base is None:
        return set(addrs)
    lo = min(addrs)
    if not base <= lo <= base + NEAR:
        return set(addrs)
    return {(a + delta) & 0xFFFF for a in addrs}


def _synth(trace, image, pc, key):
    """The site ``key`` would be at ``pc``, or ``None`` when it cannot be moved."""
    src = trace.sites[key]
    op = image[pc]
    if op != src["opcode"] or OPS[op][0] in REFUSE:
        return None
    ln = MODE_LEN[OPS[op][1]]
    base, want = operand(image, src["pc"]), operand(image, pc)
    delta = None if base is None or want is None else (want - base) & 0xFFFF
    return {
        "pc": pc,
        "opcode": op,
        "count": 0,
        "phases": src["phases"],
        "variants": [bytes(image[pc : pc + ln])],
        "idx": list(src["idx"]),
        "reads": {i: _mapset(a, base, delta) for i, a in src["reads"].items()},
        "writes": {i: _mapset(a, base, delta) for i, a in src["writes"].items()},
    }


def _donors(trace, fams):
    """``[(pc, donor pc, donor site key)]`` -- every row a copy has not run, once."""
    at = {}
    for k in trace.sites:
        at.setdefault(k[0], []).append(k)
    out, seen = [], set()
    for fam in fams:
        for row in fam.rows:
            keys = [at.get(p, ()) for p in row]
            if not any(keys) or all(keys):
                continue
            i = next(j for j, k in enumerate(keys) if k)
            for j, k in enumerate(keys):
                if not k and row[j] not in seen:
                    seen.add(row[j])
                    out.append((row[j], row[i], at[row[i]][0]))
    return out


def _edges(trace, image, fams, have):
    """Every edge a copy's row lacks and a sibling's has, retargeted to that copy."""
    byfrom = {}
    for (f, o, t), (kind, _n) in trace.edges.items():
        byfrom.setdefault(f, []).append((o, t, kind))
    out = {}
    for fam in fams:
        for i, j in [(i, j) for i in range(fam.k) for j in range(fam.k) if i != j]:
            xlate = {r[i]: r[j] for r in fam.rows}
            for row in fam.rows:
                _row_edges(image, trace, byfrom, (row[i], row[j]), (xlate, have), out)
    return out


def _row_edges(image, trace, byfrom, pair, ctx, out):
    """The edges one row of a sibling copy inherits from the row that ran."""
    src, pc = pair
    xlate, have = ctx
    for o, t, kind in byfrom.get(src, ()):
        if image[pc] != o:
            continue
        want = _target(image, pc, kind, trace.cells)
        want = xlate.get(t) if want is None else want
        if want is not None and want in have:
            out[(pc, o, want)] = [kind, 0]


def close(trace, fams):
    """``(closed trace, stats)`` -- ``trace`` plus the arms the siblings ran.

    Nothing observed changes: the added sites have count 0 and are reachable only
    through edges that were a ``trap`` before.
    """
    image = trace.image_post_init
    sites = {}
    for pc, _src, key in _donors(trace, fams):
        s = _synth(trace, image, pc, key)
        if s is not None:
            sites[pc] = s
    have = {k[0] for k in trace.sites} | set(sites)
    code = set(trace.code)
    written = set(trace.written_play)
    for pc, s in sites.items():
        code |= set(range(pc, pc + len(s["variants"][0])))
        written |= {a for w in s["writes"].values() for a in w}
    cells = code & (written | trace.written_init)
    out = rekey(trace, cells, {})
    for s in out.values():
        s["idx"] = sorted(s["idx"])
        s["variants"] = sorted(set(s["variants"]))
    for s in sites.values():
        key = site_key(s["pc"], s["opcode"], s["variants"][0], cells)
        if key not in out:
            out[key] = dict(s, idx=sorted(s["idx"]))
    edges = {k: list(v) for k, v in trace.edges.items()}
    added = 0
    for k, v in _edges(trace, image, fams, have).items():
        added += k not in edges
        edges.setdefault(k, v)
    closed = Trace(
        **{
            **{f: getattr(trace, f) for f in trace.__dataclass_fields__},
            "meta": dict(trace.meta, closure="siblings"),
            "sites": out,
            "edges": edges,
            "written_play": written,
            "cells": cells,
            "code": code,
        }
    )
    stats = {
        "families": len(fams),
        "copies": [f.k for f in fams],
        "rows": sum(len(f.rows) for f in fams),
        "sites_added": len(sites),
        "edges_added": added,
        "pcs": sorted(sites),
    }
    return closed, stats
