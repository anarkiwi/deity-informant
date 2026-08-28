"""S6 over S8 -- the per-tick history of every named cell, sampled from the verifier.

The certified program is the oracle: :class:`~.verify.Verifier` runs the same
ticks the certificate covers, and after each verified tick the machine's flat
image is read at a fixed index -- one column per byte of every named region of the
requested kinds. No tracer change and no new artefact: the arrays are what a
recurrence replay (accumulator bounds, cursor successors) is checked against.

Public API: :func:`cells`, :func:`history`, :func:`widen_u16`, :class:`History`.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from .verify import Reference, Verifier


def cells(prog, names_doc, kinds=("state",), regions_doc=None):
    """``[(region id, name, address)]`` for every byte of every named region of ``kinds``.

    ``regions_doc`` is S3's ``regions.json``: its ``addrs`` is the exact address
    set, which a strided region does not fill.
    """
    by, out = prog.by_id(), []
    exact = {r["id"]: tuple(r["addrs"]) for r in regions_doc or ()}
    for r in names_doc["regions"]:
        rgn = by.get(r["id"])
        if rgn is None or rgn.kind not in kinds:
            continue
        for a in exact.get(r["id"]) or range(rgn.base, rgn.base + rgn.size):
            out.append((r["id"], r["name"], a))
    return out


class History(dict):
    """``{name: per-tick values}``: ``(ticks,)`` for one byte, ``(ticks, n)`` above.

    ``cells`` is the sampled order and ``at`` maps one byte to its column, so a
    16-bit view (:func:`widen_u16`) resolves an S6 ``(region id, address)`` pair.

    ``by`` maps the address alone: the presentation view splits a region into the
    fields its accessors reach, and those ids are the naming plane's, not the ones
    the sampled program carries. Regions do not overlap, so one byte is one column
    however the plane that asks for it names the region around it.
    """

    def __init__(self, arrays, order):
        super().__init__(arrays)
        self.cells = order
        n, seen = Counter(r for r, _n, _a in order), Counter()
        self.at, self.by = {}, {}
        for rid, name, a in order:
            self.at[(rid, a)] = self.by[a] = (name, seen[rid] if n[rid] > 1 else None)
            seen[rid] += 1

    def cell(self, rid, addr):
        """The per-tick values of one byte, or ``None`` where it was not sampled."""
        hit = self.at.get((rid, addr)) or self.by.get(addr)
        if hit is None:
            return None
        name, col = hit
        return self[name] if col is None else self[name][:, col]


def _split(out, order):
    """The flat ``(ticks, cells)`` sample as one array per region name."""
    cols = {}
    for i, (_r, name, _a) in enumerate(order):
        cols.setdefault(name, []).append(i)
    return History(
        {n: (out[:, c[0]] if len(c) == 1 else out[:, c]).copy() for n, c in cols.items()}, order
    )


def history(
    prog,
    trace,
    names_doc,
    calls=None,
    backend="interp",
    kinds=("state",),
    regions_doc=None,
    obs=False,
):
    """``(History, Verifier)``: post-tick values of every named cell, tick by tick.

    Truncated at a divergence, which the returned verifier's ``div`` states. ``obs``
    accumulates the trackerprog observable of the same ticks on the verifier
    (:class:`~.grid.TickObs`), which is what a producer no cell column can carry is
    checked against.
    """
    order = cells(prog, names_doc, kinds, regions_doc)
    idx = np.array([a for _r, _n, a in order], np.intp)
    ref = Reference(trace, calls)
    v = Verifier(prog, ref, backend=backend, obs=obs)
    v.run_init()
    n = ref.calls if calls is None else min(int(calls), ref.calls)
    out = np.zeros((n if v.div is None else 0, idx.size), np.uint8)
    m = np.frombuffer(v.M.m, np.uint8)
    done = 0
    while done < len(out) and v.tick():
        out[done] = m[idx]
        done += 1
    return _split(out[:done], order), v


def widen_u16(hist, names_doc):
    """``{name: uint16 values}`` for every S6 ``u16`` pair whose two bytes were sampled."""
    out = {}
    for w in names_doc.get("u16") or ():
        lo, hi = hist.cell(*w["lo"]), hist.cell(*w["hi"])
        if lo is not None and hi is not None:
            out[w["name"]] = lo.astype(np.uint16) | (hi.astype(np.uint16) << 8)
    return out
