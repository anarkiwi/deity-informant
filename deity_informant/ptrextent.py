"""ptrextent: what a pointer web's derefs touched (register-model-lift 2b, b0).

The census sweep resolves every deref concretely, so a web's extent is an
observation, not a proof: per web the declared data its derefs landed in, through
``datadecl``'s own ``via:`` discovery. Every record carries the horizon it was read at.
"""

from __future__ import annotations

import bisect

from . import datadecl
from . import ptrcert
from .render import name_addr

REFUSALS = ("extent_unmappable",)
_CAP = 8  # sample addresses a record carries per list; the count is the number


class Probe:
    """The ``frameval`` address observer: which web each computed deref belongs to.

    Attribution is static and per site (``ptrcert.root_cells`` of the compiled
    address), so an address is charged to the web whose text spelled it, never
    guessed from the value a pair held. A site with no root costs nothing."""

    __slots__ = ("hits", "sites")

    def __init__(self):
        self.hits = {}
        self.sites = 0

    def __call__(self, addr, f, width):
        roots = sorted(set(ptrcert.root_cells(addr)))
        if not roots:
            return f
        self.sites += 1
        sinks = [self.hits.setdefault(c, set()) for c in roots]
        if width == 1:

            def observe(r, m, rd):
                a = f(r, m, rd)
                for s in sinks:
                    s.add(a)
                return a

            return observe

        span = tuple(range(width))

        def observe_wide(r, m, rd):
            a = f(r, m, rd)
            for s in sinks:
                for j in span:
                    s.add((a + j) & 0xFFFF)
            return a

        return observe_wide


def _via(d, cell):
    """True where the registry itself says this pointer walks this block."""
    return d["via"] is not None and d["via"] in (cell, cell + 1)


def _below(regions, a):
    """The declaration nearest below ``a``, else None: what it ran off the end of."""
    j = bisect.bisect_right(regions.bases, a) - 1
    return None if j < 0 else regions.decls[j]


def web(cell, addrs, regions):
    """One web's observed-extent record: the declared data its derefs touched.

    An address in no declared datum splits two ways, because the two are different
    work: ``short`` ran off the end of a block this web itself walks, ``foreign``
    landed where the registry declares nothing at all."""
    blocks, via, loose = {}, [], []
    for a in sorted(addrs):
        at = regions.at(a)
        if at is None:
            loose.append(a)
            continue
        d = at[0]
        blocks[d["base"]] = blocks.get(d["base"], 0) + 1
        if _via(d, cell) and d["base"] not in via:
            via.append(d["base"])
    short, over = [], 0
    for a in loose:
        d = _below(regions, a)
        if d is not None and d["base"] in blocks:
            short.append(a)
            over = max(over, a - d["base"] - d["size"] + 1)
    return {
        "root": "$%04X" % cell,
        "name": name_addr(cell),
        "addrs": len(addrs),
        "blocks": ["$%04X" % b for b in sorted(blocks)],
        "block_hits": {"$%04X" % b: n for b, n in sorted(blocks.items())},
        "via_blocks": ["$%04X" % b for b in sorted(via)],
        "unmappable": len(loose),
        "unmappable_short": len(short),
        "unmappable_foreign": len(loose) - len(short),
        "overshoot": over,
        "unmappable_addrs": ["$%04X" % a for a in loose[:_CAP]],
        "refusals": ["extent_unmappable"] if loose else [],
        "observed": bool(addrs),
    }


def extents(prog, hits):
    """Every pointer web's observed extent, keyed as ``ptrcert`` keys its roots.

    The population is 2a's rather than a second one, so the extent column and the
    certification column are the same rows."""
    regions = datadecl.Regions(prog.data_decls)
    per, _loose = ptrcert.sites(prog)
    return [web(c, hits.get(c, ()), regions) for c in sorted(per)]


def summary(recs, frames):
    """One tune's extent row: the horizon it was read at, and what it covers."""
    mapped = [r for r in recs if r["observed"] and not r["unmappable"]]
    return {
        "horizon": frames,
        "webs": len(recs),
        "webs_observed": sum(1 for r in recs if r["observed"]),
        "webs_mapped": len(mapped),
        "webs_via": sum(1 for r in recs if r["via_blocks"]),
        "blocks": sum(len(r["blocks"]) for r in recs),
        "addrs": sum(r["addrs"] for r in recs),
        "unmappable_addrs": sum(r["unmappable"] for r in recs),
        "unmappable_short": sum(r["unmappable_short"] for r in recs),
        "webs_short": sum(1 for r in recs if r["unmappable"] and not r["unmappable_foreign"]),
        "overshoot": max((r["overshoot"] for r in recs), default=0),
        "refusals": {"extent_unmappable": len(recs) - len(mapped)},
        "records": recs,
    }


def mapped_cells(recs):
    """The web cells whose observed extent is wholly inside the declared registry.

    A web the horizon never saw deref has no extent for an annotation to name, so it
    is no more a target than one that left the registry."""
    return {int(r["root"][1:], 16) for r in recs if r["observed"] and not r["unmappable"]}


def horizons(rows):
    """``{tune: horizon}`` from artifact rows: what each web's extent was read at."""
    return {r["tune"]: r["extents"]["horizon"] for r in rows if "extents" in r}


def records(rows):
    """``{tune: [web record]}`` from artifact rows: rung (g)'s own build-time input."""
    return {r["tune"]: r["extents"]["records"] for r in rows if "extents" in r}


def outran(art, runs):
    """Rows of a run that outran the artifact's horizon: b0's MUST, as a check.

    Inside the horizon an ``extent`` fault is an instrument defect and stops the line;
    past it the fault is the claim boundary, which is ``unobserved``'s own discipline.
    A tune the artifact does not name is past it."""
    bad = []
    for tune in sorted(runs):
        n, h = runs[tune], art.get(tune)
        if h is None or n > h:
            bad.append({"tune": tune, "frames": n, "horizon": h})
    return bad
