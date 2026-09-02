"""T3 -- the certificate: the universal player's observable against the verifier's.

Section 2's comparison, tick for tick over the whole certified horizon: per voice
the ordered ctrl/AD/SR edges, and the pair and level values; voice order and
cross-class order inside a tick are dropped and said so.
"""

from __future__ import annotations

import hashlib
import json

from ..tuneprog.facts import SID_VOICE, SID_VOICES

COMPARED = [
    "per-voice ctrl/AD/SR write order",
    "freq/pw/cutoff tick values",
    "res_route/mode_vol tick values",
]
DROPPED = [
    "order between registers of different classes inside a tick",
    "the interleave between voices of one tick's writes",
    "cycle position inside a tick",
]


def _byvoice(edges):
    out = {v: [] for v in range(SID_VOICES)}
    for r, val in edges:
        out[r // SID_VOICE].append((int(r), int(val)))
    return out


def divergence(want, got):
    """The first tick two observable runs differ on, as the certificate states it."""
    for t, (a, b) in enumerate(zip(want, got)):
        wa, ga = _byvoice(a.edges), _byvoice(b.edges)
        for v in range(SID_VOICES):
            if wa[v] != ga[v]:
                return {
                    "tick": t,
                    "register": "voice %d edges" % v,
                    "expected": wa[v],
                    "got": ga[v],
                }
        if tuple(a.values) != tuple(b.values):
            i = next(i for i, (x, y) in enumerate(zip(a.values, b.values)) if x != y)
            return {
                "tick": t,
                "register": "value %d" % i,
                "expected": a.values[i],
                "got": b.values[i],
            }
    if len(want) != len(got):
        return {
            "tick": min(len(want), len(got)),
            "register": "horizon",
            "expected": len(want),
            "got": len(got),
        }
    return None


def equal_ticks(want, got):
    """How many leading ticks agree."""
    d = divergence(want, got)
    return len(want) if d is None else d["tick"]


def certificate(tune, cert, want, got, refusals, end, trap=None):
    """``scoreprog.certificate.json`` (section 2), with the loop claim re-checked.

    Emitted only with no refusal, no divergence and no trap: a render that
    differs from the source is not a scoreprog, however it is described.
    """
    sub = ((cert or {}).get("subtunes") or [{}])[0]
    digest = (
        hashlib.sha256(json.dumps(cert, sort_keys=True).encode()).hexdigest()[:16] if cert else None
    )
    loop = None
    if sub.get("complete") and (sub.get("period") or 0) > 1:
        p, f = sub["period"], sub.get("first_repeat")
        loop = {"period": p, "first_repeat": f, "rechecked": None}
        if (
            f is not None and f >= p and f + p <= len(got)
        ):  # the render repeats where the source did
            loop["rechecked"] = got[f - p : f] == got[f : f + p]
    div = divergence(want, got)
    return {
        "source": {"tune": tune, "certificate_digest": digest},
        "compared": COMPARED,
        "dropped": DROPPED,
        "ticks": len(want),
        "divergence": div,
        "trap": trap,
        "rendered": {"ticks_equal": equal_ticks(want, got), "divergence": div},
        "refusals": [r.to_dict() if hasattr(r, "to_dict") else r for r in refusals],
        "emitted": not refusals and div is None and trap is None,
        "loop": loop,
        "end": end,
    }
