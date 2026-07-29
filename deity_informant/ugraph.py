"""Universal tracker graph and Gate U0 (docs/tracker-unification.md 2-4).

One primitive: a triggered generator ``(transfer, trigger, route)``. A tune is a
graph of them; ``eval_graph``'s canonical projection MUST equal the player's.
Un-refined emits stay in a RAW node, so a graph is complete from the start.
"""

from __future__ import annotations

from collections import namedtuple

from . import framelog

Generator = namedtuple("Generator", "transfer trigger route")
Coverage = namedtuple("Coverage", "interpreted raw total planes")

FRAME = ("frame",)


class UGraphError(ValueError):
    """The graph is not evaluable (unknown transfer, dangling trigger)."""


def div(n, trigger=FRAME):
    """Emit one tick per ``n`` input triggers: a clock. Route is always Fire."""
    return Generator(("DIV", n), trigger, ("fire",))


def lookup(seq, trigger, reg):
    """Emit ``seq[i]`` into a register plane; ``i`` advances per trigger."""
    return Generator(("LOOKUP", tuple(seq)), trigger, ("plane", reg))


def ramp(seed, step, bound, trigger, reg):
    """Emit ``seed + step*count`` into a plane, wrapped at ``bound``."""
    return Generator(("RAMP", seed, step, bound), trigger, ("plane", reg))


def raw(per_frame):
    """The completeness floor: replay ``per_frame[f]`` writes verbatim, in order."""
    return Generator(("RAW", tuple(tuple(w) for w in per_frame)), FRAME, ("raw",))


def _section_of(reg):
    """framelog section index for a SID register offset."""
    if reg <= 0x14:
        v, r = divmod(reg, 7)
        return 2 * v + (1 if r >= 4 else 0)
    return 6 if reg <= 0x18 else 7


class Graph:
    """Generator nodes plus the two distinguished ones every graph carries."""

    def __init__(self, nodes, freq_table=None, cadence=None):
        self.nodes = list(nodes)
        self.freq_table = freq_table
        self.cadence = cadence

    def raw_index(self):
        """Index of the RAW floor node, or None."""
        for i, g in enumerate(self.nodes):
            if g.transfer[0] == "RAW":
                return i
        return None


def _fired(nodes, frame):
    """Trigger counts per node for ``frame``: root clocks and their Fire edges."""
    fires = [1 if g.trigger == FRAME else 0 for g in nodes]
    for i, g in enumerate(nodes):
        if g.transfer[0] != "DIV" or not fires[i]:
            continue
        n = max(1, g.transfer[1])
        if (frame + 1) % n == 0:
            for j, h in enumerate(nodes):
                if h.trigger == ("event", i):
                    fires[j] += 1
    return fires


def _emit(g, count):
    """Value a plane-routed generator emits on its ``count``-th trigger."""
    kind = g.transfer[0]
    if kind == "LOOKUP":
        seq = g.transfer[1]
        return seq[(count - 1) % len(seq)] if seq else None
    if kind == "RAMP":
        _k, seed, step, bound = g.transfer
        raw_v = seed + step * (count - 1)
        return raw_v % bound if bound else raw_v
    raise UGraphError("transfer %r has no value emit" % (kind,))


def _check(nodes):
    for g in nodes:
        if g.trigger != FRAME and g.trigger[0] != "event":
            raise UGraphError("unknown trigger %r" % (g.trigger,))
        if g.trigger[0] == "event" and not 0 <= g.trigger[1] < len(nodes):
            raise UGraphError("dangling trigger %r" % (g.trigger,))
        if g.route[0] not in ("plane", "fire", "raw"):
            raise UGraphError("unknown route %r" % (g.route,))


def eval_graph(graph, nframes):
    """Canonical per-frame records produced by propagating triggers.

    Refinement removes a write from RAW, so RAW and a plane-routed node never
    contend for one register and the interleaving stays well defined."""
    nodes = graph.nodes
    _check(nodes)
    counts = [0] * len(nodes)
    out = []
    for f in range(nframes):
        fires = _fired(nodes, f)
        writes = []
        for i, g in enumerate(nodes):
            if not fires[i]:
                continue
            counts[i] += fires[i]
            if g.transfer[0] == "RAW":
                rows = g.transfer[1]
                writes.extend(rows[f] if f < len(rows) else ())
            elif g.route[0] == "plane":
                v = _emit(g, counts[i])
                if v is not None:
                    writes.append((g.route[1], v & 0xFF))
        out.append(writes)
    return framelog.canonical(out)


def gate_u0(graph, frames):
    """Gate U0 verdict: None when the graph reproduces the player's projection."""
    return framelog.diff(eval_graph(graph, len(frames)), framelog.canonical(frames))


def from_frames(frames):
    """The completeness floor: one RAW node replaying every write, in order."""
    return Graph([raw([list(fr) for fr in frames])])


def coverage(graph, nframes):
    """Interpreted vs RAW emit counts, and the per-plane split."""
    planes = {}
    interp = rawn = 0

    def bump(reg, n, is_interp):
        p = framelog.SECTIONS[_section_of(reg)]
        it, tot = planes.get(p, (0, 0))
        planes[p] = (it + (n if is_interp else 0), tot + n)

    for i, g in enumerate(graph.nodes):
        if g.transfer[0] == "RAW":
            for fr in g.transfer[1][:nframes]:
                for reg, _v in fr:
                    bump(reg, 1, False)
                    rawn += 1
        elif g.route[0] == "plane":
            fires = sum(_fired(graph.nodes, f)[i] for f in range(nframes))
            bump(g.route[1], fires, True)
            interp += fires
    return Coverage(interp, rawn, interp + rawn, planes)
