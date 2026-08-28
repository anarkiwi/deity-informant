"""T3 -- the certificate: the universal player's observable against the verifier's.

Section 2's comparison, tick for tick over the whole certified horizon: per voice
the ordered ctrl/AD/SR edges, and the pair and level values; voice order and
cross-class order inside a tick are dropped and said so.
"""

from __future__ import annotations

import hashlib
import json
import re

from ..tuneprog.facts import SID_VOICE, SID_VOICES
from .refuse import Refusal

COMPARED = [
    "per-voice ctrl/AD/SR write order",
    "freq/pw/cutoff tick values",
    "res_route/mode_vol tick values",
]
DROPPED = [
    "order between registers of different classes inside a tick",
    "order between voices inside a tick",
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


TEMP = re.compile(r"u\d+_L[0-9A-F]{4}_[0-9A-F]{2}#\d+|(?<![\w$])[A-Z]#\d+|\$saved\d*")
ADDR = re.compile(r"\$[0-9A-F]{4}(?![0-9A-Za-z])")
PROGRAM = ("block", "fetch", "let", "phi", "store")
ADDRESSED = ("meta", "pitch")
PROVENANCE = ("meta", "site")  # keys holding where a datum came from: not the datum


def _exempt(path):
    """Where a bare address is data: meta, pitch, and a selector's or channel's cursor label."""
    if path[0] in ADDRESSED:
        return True
    return path[-1] == "cursor" and (
        path[0] in ("instruments", "streams") or path[:2] == ("score", "channels")
    )


def schema_check(tp):
    """The refusals a trackerprog object carries by its shape: no program residue.

    A string holding an SSA temp or a bare address outside the addressed
    sections, an item of the lowered tick, and a producer whose accumulator the
    document does not define each refuse by name; ``meta`` and a ``site`` are
    provenance, read past.
    """
    out = {}

    def bad(path, detail):
        cell = "/".join("*" if isinstance(k, int) else k for k in path)
        out.setdefault((cell, detail), Refusal("program residue", cell, "", detail))

    def walk(x, path):
        if isinstance(x, dict):
            if x.get("kind") in PROGRAM and "rank" in x:
                bad(path, "program block %s" % x["kind"])
                return
            for k, v in x.items():
                if k in PROVENANCE:
                    continue
                walk(k, path + (k,))
                walk(v, path + (k,))
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                walk(v, path + (i,))
        elif isinstance(x, str):
            m = TEMP.search(x)
            if m:
                bad(path, "temp %s in %r" % (m.group(0), x))
            elif not _exempt(path):
                m = ADDR.search(x)
                if m:
                    bad(path, "address %s in %r" % (m.group(0), x))

    walk(tp, ())
    accs = tp.get("accs") or {}
    for i, p in enumerate(tp.get("producers") or ()):
        for a in p.get("accs") or ():
            if a not in accs:
                bad(("producers", i, "accs"), "acc %s not in accs" % a)
        if not p.get("register") and p.get("kind") != "file":
            bad(("producers", i, "register"), "no register")
    return list(out.values())


def certificate(tune, cert, want, got, refusals, end, trap=None, tp=None):
    """``trackerprog.certificate.json`` (section 2), with the loop claim re-checked.

    Emitted only with no refusal, no divergence, no trap and, given ``tp``, a
    clean :func:`schema_check`: a render that differs from the source, or an
    object carrying program residue, is not a trackerprog however it is described.
    """
    refusals = list(refusals) + (schema_check(tp) if tp is not None else [])
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
