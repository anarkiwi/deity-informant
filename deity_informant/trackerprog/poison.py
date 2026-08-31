"""The poison harness: render two forms of one object and count differing ticks.

Section 7's method as a procedure.  A mutation is a stated edit to an object; a
strike renders both forms over the whole horizon and counts the ticks whose
write lists differ, with the sites the mutation matched and the first divergence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from .universal import Player

DIGEST = 16  # bytes of blake2b per tick; 30 builds of 332,358 ticks is 5 MB
CACHE_ENV = "DEITY_POISON_CACHE"
# what a poisoned object does to the player: a missing key, a bad type, an assert
REFUSALS = (
    ArithmeticError,
    AssertionError,
    AttributeError,
    LookupError,
    RecursionError,
    TypeError,
    ValueError,
)


def keys(container, seg):
    """The keys of ``container`` one path segment names, in document order.

    ``*`` is every key of a mapping or every index of a list, a decimal segment
    indexes a list, anything else is a mapping key.  A segment naming nothing
    yields nothing, which is how a mutation counts its sites.
    """
    if isinstance(container, list):
        if seg == "*":
            return list(range(len(container)))
        if seg.lstrip("-").isdigit() and -len(container) <= int(seg) < len(container):
            return [int(seg) % len(container)]  # normalised, so a drop sorts by depth
        return []
    if isinstance(container, dict):
        return list(container) if seg == "*" else ([seg] if seg in container else [])
    return []


def sites(obj, path):
    """Every ``(container, key)`` a dotted path names, deepest segment last."""
    cur = [obj]
    segs = path.split(".")
    for seg in segs[:-1]:
        cur = [c[k] for c in cur for k in keys(c, seg)]
    return [(c, k) for c in cur for k in keys(c, segs[-1])]


class Mutation:
    """A stated edit to an object: ``drop PATH``, or ``set PATH=JSON``."""

    def __init__(self, op, path, value=None):
        if op not in ("drop", "set"):
            raise ValueError("mutation is drop or set, not %r" % op)
        self.op, self.path, self.value = op, path, value

    @classmethod
    def parse(cls, spec):
        """``"drop meta.tempo.reset"`` or ``"set meta.tempo.step=1"``."""
        op, _, rest = spec.strip().partition(" ")
        if op == "set":
            path, sep, value = rest.partition("=")
            if not sep:
                raise ValueError("set wants PATH=JSON, got %r" % rest)
            return cls(op, path.strip(), json.loads(value))
        return cls(op, rest.strip())

    def __str__(self):
        tail = "=" + json.dumps(self.value) if self.op == "set" else ""
        return "%s %s%s" % (self.op, self.path, tail)

    def apply(self, obj):
        """``(mutant, sites)`` -- a copy with the edit applied at every match."""
        out = json.loads(json.dumps(obj))
        found = sites(out, self.path)
        if self.op == "set":
            for container, key in found:
                container[key] = json.loads(json.dumps(self.value))
        else:  # a list's keys are indices, so the deepest index goes first
            for container, key in sorted(found, key=_deepest):
                del container[key]
        return out, len(found)


class Poison:
    """A named mutation: the stated edits, applied together and counted together."""

    def __init__(self, name, specs):
        self.name = name
        self.edits = [Mutation.parse(s) for s in specs]

    def __str__(self):
        return "%s: %s" % (self.name, "; ".join(str(e) for e in self.edits) or "no edit")

    def apply(self, obj):
        found = 0
        for edit in self.edits:
            obj, n = edit.apply(obj)
            found += n
        return (obj if self.edits else json.loads(json.dumps(obj))), found


def _deepest(site):
    return -site[1] if isinstance(site[0], list) else 0


def fingerprint(obj, ticks):
    """The cache key of one render: the object's canonical form and its horizon."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return "%s-%d" % (hashlib.sha256(text.encode()).hexdigest()[:16], ticks)


def digests(obj, ticks, player=Player):
    """One 16-byte digest per tick of the render, as a ``(ticks, 16)`` array."""
    p = player(obj)
    out = np.empty((ticks, DIGEST), np.uint8)
    for t in range(ticks):
        buf = bytearray()
        for write in p.tick():
            buf.extend(write)
        out[t] = np.frombuffer(hashlib.blake2b(buf, digest_size=DIGEST).digest(), np.uint8)
    return out


def cache_dir(cache=None):
    """Where renders are kept, or ``None`` for no cache."""
    cache = cache if cache is not None else os.environ.get(CACHE_ENV)
    return Path(cache) if cache else None


def render_digests(obj, ticks, cache=None, player=Player):
    """:func:`digests`, read from and written to the cache when there is one."""
    d = cache_dir(cache)
    if d is None:
        return digests(obj, ticks, player)
    path = d / (fingerprint(obj, ticks) + ".npy")
    if path.is_file():
        return np.load(path)
    out = digests(obj, ticks, player)
    d.mkdir(parents=True, exist_ok=True)
    np.save(path, out)
    return out


def differ(a, b):
    """``(differing ticks, first)`` between two digest runs; ``first`` is None at 0."""
    n = min(len(a), len(b))
    d = np.any(a[:n] != b[:n], axis=1)
    count = int(d.sum()) + abs(len(a) - len(b))
    if d.any():
        return count, int(d.argmax())
    return count, (n if count else None)


def strike(obj, mutation, ticks, cache=None, player=Player):
    """Render ``obj`` and its mutant over ``ticks`` and count the ticks that differ."""
    mutant, found = mutation.apply(obj)
    base = render_digests(obj, ticks, cache, player)
    row = {"mutation": str(mutation), "sites": found, "ticks": ticks, "refused": None}
    if fingerprint(mutant, ticks) == fingerprint(obj, ticks):
        return dict(row, differing=0, first=None)
    try:
        alt = render_digests(mutant, ticks, cache, player)
    except REFUSALS as exc:  # a poison the renderer refuses is an asserted invariant
        return dict(row, differing=None, first=None, refused="%s: %s" % (type(exc).__name__, exc))
    count, first = differ(base, alt)
    return dict(row, differing=count, first=first)


def against(obj, stored, ticks, cache=None, player=Player):
    """The same count against a digest run stored by another checkout of the tree."""
    count, first = differ(render_digests(obj, ticks, cache, player), stored)
    return {
        "mutation": "stored render",
        "sites": None,
        "ticks": ticks,
        "differing": count,
        "first": first,
        "refused": None,
    }


def line(name, row):
    """The one line a document row quotes per build."""
    tag = "%2s site%s" % (
        "-" if row["sites"] is None else row["sites"],
        "" if row["sites"] == 1 else "s",
    )
    if row.get("refused"):
        return "%-24s %7s of %-7d refused   %s  %s" % (name, "-", row["ticks"], tag, row["refused"])
    return "%-24s %7d of %-7d differing  %s%s" % (
        name,
        row["differing"],
        row["ticks"],
        tag,
        "" if row["first"] is None else "  first at %d" % row["first"],
    )


def total(rows):
    """The sweep's own totals: builds, ticks, differing ticks and sites."""
    return {
        "builds": len(rows),
        "ticks": sum(r["ticks"] for r in rows.values()),
        "differing": sum(r["differing"] or 0 for r in rows.values()),
        "sites": sum(r["sites"] or 0 for r in rows.values()),
        "refused": sorted(n for n, r in rows.items() if r.get("refused")),
        "untouched": sorted(n for n, r in rows.items() if not r["sites"]),
    }
