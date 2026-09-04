"""The trackerprog certificate of prototype-trackerprog.md section 2.

One comparison, over the whole certified horizon: the universal player's SID
writes against the reference's, both reduced by :func:`~..tuneprog.grid.reduce_tick`
-- per-voice ctrl/AD/SR writes kept in tick order, freq/pw/cutoff and
res_route/mode_vol as the one value the tick left.  What the reduction drops --
order between register classes inside a tick, the *interleave* between voices of
one tick's writes, the cycle position -- the certificate names, so the boundary
is stated and not hidden.  The order the voices *run* in is not dropped:
``meta.voice_order`` decides the render wherever two voices share a cell or a
register (section 2).
"""

from __future__ import annotations

from ..tuneprog import grid
from ..tuneprog.facts import SID_VOICE
from .universal import render

COMPARED = (
    "per-voice ctrl/AD/SR write order",
    "freq/pw/cutoff tick values",
    "res_route/mode_vol tick values",
)
DROPPED = (
    "order between registers of different classes inside a tick",
    "the interleave between voices of one tick's writes",
    "cycle position inside a tick",
)


def attest(obj, reference, ticks=None, renderer=None):
    """Render ``obj`` and compare it with ``reference``, a per-tick write list."""
    n = len(reference) if ticks is None else min(ticks, len(reference))
    got = (renderer or render)(obj, n)
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
        if _voiced(a) != _voiced(b) and out["divergence"] is None:
            out["divergence"] = _where(t, a, b, want, mine)
    return out


def _voiced(obs):
    """One tick's observable as section 2 compares it: the edges *per voice*.

    Rule 1 keeps every ctrl/AD/SR write in tick order, and it is a rule about one
    voice's own envelope generator; order between voices is on the certificate's
    ``dropped`` list, so the comparison drops it too -- which is what
    :func:`~.certify.divergence` already does on the certificate's own side.  A
    family whose tick runs one pass over all three voices and then another
    interleaves its writes differently from one that finishes a voice at a time,
    and neither ordering is audible.
    """
    out = {}
    for r, v in obs.edges:
        out.setdefault(r // SID_VOICE, []).append((r, v))
    return out, obs.values


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
    wa, ga = _voiced(a)[0], _voiced(b)[0]
    for v in sorted(set(wa) | set(ga)):
        if wa.get(v, []) != ga.get(v, []):
            d["edges"] = {"voice": v, "expected": wa.get(v, []), "got": ga.get(v, [])}
            break
    for i, (x, y) in enumerate(zip(a.values, b.values)):
        if x != y:
            d.setdefault("values", []).append({"column": i, "expected": x, "got": y})
    return d


def _fmt(w):
    return " ".join("%02X=%02X" % rv for rv in w)
