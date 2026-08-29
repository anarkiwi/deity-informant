"""The trackerprog certificate of prototype-trackerprog.md section 2.

One comparison, over the whole certified horizon: the universal player's SID
writes against the reference's, both reduced by :func:`~..tuneprog.grid.reduce_tick`
-- per-voice ctrl/AD/SR writes kept in tick order, freq/pw/cutoff and
res_route/mode_vol as the one value the tick left.  What the reduction drops --
order between register classes inside a tick, order between voices, the cycle
position -- the certificate names, so the boundary is stated and not hidden.
"""

from __future__ import annotations

from ..tuneprog import grid
from .universal import render

COMPARED = (
    "per-voice ctrl/AD/SR write order",
    "freq/pw/cutoff tick values",
    "res_route/mode_vol tick values",
)
DROPPED = (
    "order between registers of different classes inside a tick",
    "order between voices inside a tick",
    "cycle position inside a tick",
)


def attest(obj, reference, ticks=None):
    """Render ``obj`` and compare it with ``reference``, a per-tick write list."""
    n = len(reference) if ticks is None else min(ticks, len(reference))
    got = render(obj, n)
    out = {
        "compared": list(COMPARED),
        "dropped": list(DROPPED),
        "ticks": n,
        "divergence": None,
        "writes": sum(len(w) for w in got),
        "permuted_ticks": 0,
        "identical_ticks": 0,
        "same_per_register_order": subsequences_agree(reference[:n], got),
    }
    for t in range(n):
        want = [tuple(x) for x in reference[t]]
        mine = [tuple(x) for x in got[t]]
        if want == mine:
            out["identical_ticks"] += 1
        elif sorted(want) == sorted(mine):
            out["permuted_ticks"] += 1
        a, b = grid.reduce_tick(want), grid.reduce_tick(mine)
        if a != b and out["divergence"] is None:
            out["divergence"] = _where(t, a, b, want, mine)
    return out


def subsequences_agree(reference, got):
    """True where, per register, the values written per tick agree in order.

    Stronger than section 2 and free here: it says the two sides differ only by
    the interleave of registers inside a tick, never by a value or a count.
    """
    for want, mine in zip(reference, got):
        want = [tuple(x) for x in want]
        mine = [tuple(x) for x in mine]
        for r in {q for q, _ in want} | {q for q, _ in mine}:
            if [v for q, v in want if q == r] != [v for q, v in mine if q == r]:
                return False
    return True


def _where(t, a, b, want, mine):
    d = {"tick": t, "expected": _fmt(want), "got": _fmt(mine)}
    if a.edges != b.edges:
        d["edges"] = {"expected": list(a.edges), "got": list(b.edges)}
    for i, (x, y) in enumerate(zip(a.values, b.values)):
        if x != y:
            d.setdefault("values", []).append({"column": i, "expected": x, "got": y})
    return d


def _fmt(w):
    return " ".join("%02X=%02X" % rv for rv in w)
