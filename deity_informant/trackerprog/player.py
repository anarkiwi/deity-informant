"""T3 -- the universal player: prototype-trackerprog section 4, tick for tick.

One fixed procedure over one data object. Per tick, per voice: the row clock,
the sequencer step that consumes an event, the armed streams and accumulators,
then ``commit`` -- the voice's edge list in the order the schema states, and its
level values. The output is one :class:`~.tuneprog.grid.TickObs` per tick, the
same reduction the certificate compares.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..tuneprog.facts import GLOBAL_REG, SID_REG_LO, SID_VOICE, VOICE_REG
from ..tuneprog import grid

PAIR = {"freq": 0, "pw": 1, "cutoff": 2}  # 16-bit targets, per voice or global
DEFAULT_ORDER = ("ad", "sr", "ctrl")


def regindex(name, voice):
    """The SID register index of a per-voice or global register name."""
    if name in VOICE_REG:
        return SID_VOICE * voice + VOICE_REG.index(name)
    return next(a - SID_REG_LO for a, g in GLOBAL_REG.items() if g == name)


@dataclass
class Acc:
    """A bounded accumulator (section 5), the forms the player steps itself."""

    target: str
    width: int
    delta: int
    lo: int
    hi: int
    policy: str
    rate: int = 1
    value: int = 0
    phase: int = 0
    direction: int = 1

    def step(self):
        self.phase += 1
        if self.phase % self.rate:
            return
        m = (1 << self.width) - 1
        v = self.value + self.delta * self.direction
        if self.policy == "wrap":
            v &= m
        elif self.policy == "clamp":
            v = min(max(v, self.lo), self.hi)
        elif self.policy == "halt":
            v = self.value if not self.lo <= v <= self.hi else v
        elif self.policy == "reflect":
            if not self.lo <= v <= self.hi:
                self.direction = -self.direction
                v = self.value + self.delta * self.direction
        self.value = v & m


@dataclass
class Voice:
    order: list
    patterns: dict
    pos: int = -1
    row: int = 0
    hold: int = 0
    note: int | None = None
    ins: int | None = None
    sets: dict = field(default_factory=dict)
    ended: bool = False
    accs: list = field(default_factory=list)

    def pattern(self):
        return (
            self.patterns[self.order[self.pos]["pattern"]]
            if 0 <= self.pos < len(self.order)
            else None
        )

    def advance(self, end):
        """Move to the next row, across patterns, honouring the order's terminator."""
        pat = self.pattern()
        if pat is None or self.row >= len(pat):
            self.pos += 1
            self.row = 0
            if self.pos >= len(self.order):
                if end.get("kind") == "loop":
                    self.pos = int(end.get("row", 0))
                else:
                    self.ended = True
                    return None
        pat = self.pattern()
        ev = pat[self.row]
        self.row += 1
        return ev


class Player:
    """Render a trackerprog: :meth:`tick` returns the tick's :class:`~.tuneprog.grid.TickObs`."""

    def __init__(self, tp):
        self.tp = tp
        self.pitch = list(tp["pitch"] or [])
        self.order = tuple(tp["meta"].get("commit_order") or DEFAULT_ORDER)
        self.voices = [
            Voice(v["order"], v["patterns"], accs=[self._acc(a) for a in v.get("accs", ())])
            for v in tp["score"]["voices"]
        ]
        self.end = tp["score"].get("end") or {}
        self.prev = None
        self.t = 0

    def _acc(self, a):
        return Acc(
            a["target"],
            a["width"],
            a["delta"],
            a["bound"][0],
            a["bound"][1],
            a["policy"],
            a.get("rate", 1),
        )

    def sequencer_step(self, v, vs):
        ev = vs.advance(self.end)
        if ev is None:
            vs.hold = 1 << 30
            return
        vs.hold = int(ev["dur"])
        if ev.get("note") is not None:
            vs.note = int(ev["note"])
            for a in vs.accs:
                a.value, a.phase, a.direction = 0, 0, 1
        if ev.get("ins") is not None:
            vs.ins = int(ev["ins"])
        for cmd in ev.get("cmds", ()):
            kind, reg, val = cmd
            if kind == "set":
                vs.sets[reg] = int(val)

    def commit(self, v, vs, edges, levels):
        for reg, val in list(vs.sets.items()):
            if reg not in self.order and reg in VOICE_REG:
                levels[regindex(reg, v)] = val
        if vs.note is not None and 0 <= vs.note < len(self.pitch):
            f = self.pitch[vs.note]
            for a in vs.accs:
                if a.target == "freq":
                    f += a.value if a.value < (1 << (a.width - 1)) else a.value - (1 << a.width)
            f &= 0xFFFF
            levels[regindex("freq_lo", v)] = f & 0xFF
            levels[regindex("freq_hi", v)] = f >> 8
        for reg in self.order:
            if reg in vs.sets:
                edges.append((regindex(reg, v), vs.sets.pop(reg)))
        vs.sets = {k: val for k, val in vs.sets.items() if k in self.order}

    def tick(self):
        edges, levels = [], {}
        for v, vs in enumerate(self.voices):
            vs.hold -= 1
            if vs.hold <= 0 and not vs.ended:
                self.sequencer_step(v, vs)
            for a in vs.accs:
                a.step()
            self.commit(v, vs, edges, levels)
        writes = edges + sorted(levels.items())
        self.prev = grid.reduce_tick(writes, self.prev)
        self.t += 1
        return self.prev

    def render(self, ticks):
        return [self.tick() for _ in range(ticks)]
