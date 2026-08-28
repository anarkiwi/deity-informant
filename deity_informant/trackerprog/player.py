"""T3 -- the universal player: prototype-trackerprog section 4, tick for tick.

One fixed procedure over one data object. Per tick, per voice: the row clock,
the sequencer step that consumes an event and arms its stream, the armed
stream's step whose hold elapsed, then ``commit`` -- the step's edge writes in
their own order and the voice's level values. The global channel is one stream
of its own. The output is one :class:`~.tuneprog.grid.TickObs` per tick, the
reduction the certificate compares.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..tuneprog import grid
from ..tuneprog.facts import GLOBAL_REG, SID_REG_LO, SID_VOICE, VOICE_REG

DEFAULT_ORDER = ("ad", "sr", "ctrl")
LEVEL = ("pw", "cutoff", "res_route", "mode_vol")


def regindex(name, voice):
    """The SID register index of a per-voice or global register name."""
    if name in VOICE_REG:
        return SID_VOICE * voice + VOICE_REG.index(name)
    return next(a - SID_REG_LO for a, g in GLOBAL_REG.items() if g == name)


@dataclass
class Cursor:
    """One armed stream: its steps, the step due next and the ticks left on the current one."""

    steps: list
    at: int = 0
    left: int = 0

    def step(self):
        """The sets due this tick, or ``None`` while the current step's hold runs."""
        if self.left > 0:
            self.left -= 1
            return None
        if self.at >= len(self.steps):
            return None
        s = self.steps[self.at]
        self.at += 1
        self.left = int(s["hold"]) - 1
        return s["sets"]


@dataclass
class Voice:
    order: list
    patterns: dict
    pos: int = -1
    row: int = 0
    hold: int = 0
    note: int | None = None
    off: int = 0
    freq_off: int | None = 0
    freq: int = 0
    levels: dict = field(default_factory=dict)
    stream: Cursor | None = None
    ended: bool = False

    def pattern(self):
        if 0 <= self.pos < len(self.order):
            return self.patterns[self.order[self.pos]["pattern"]]
        return None

    def advance(self, end):
        """The next row, across patterns, honouring the order's terminator."""
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
        ev = self.pattern()[self.row]
        self.row += 1
        return ev


class Player:
    """Render a trackerprog: :meth:`tick` returns the tick's :class:`~.tuneprog.grid.TickObs`."""

    def __init__(self, tp):
        self.tp = tp
        self.pitch = list(tp["pitch"] or [])
        self.streams = tp["streams"]
        self.voices = [Voice(v["order"], v["patterns"]) for v in tp["score"]["voices"]]
        self.end = tp["score"].get("end") or {}
        g = tp["globals"].get("stream")
        self.glob = Cursor(self.streams[g]["steps"]) if g else None
        self.levels = {}
        self.prev = None

    def sequencer_step(self, vs):
        ev = vs.advance(self.end)
        if ev is None:
            vs.hold = 1 << 30
            return
        vs.hold = int(ev["dur"])
        if ev.get("note") is not None:
            vs.note = int(ev["note"])
        vs.off, vs.freq_off = 0, 0
        vs.stream = Cursor(self.streams[ev["ins"]]["steps"]) if ev.get("ins") else None

    def apply(self, v, vs, sets, edges):
        """One step's sets: edges in their own order, level and pitch operands as state."""
        for reg, val in sets:
            if reg == "note_abs":
                vs.note, vs.off, vs.freq_off = int(val), 0, 0
            elif reg == "note_off":
                vs.off, vs.freq_off = int(val), 0
            elif reg == "freq":
                vs.freq_off = None
                vs.freq = int(val)
            elif reg in grid_edges():
                edges.append((regindex(reg, v), int(val)))
            elif reg in ("cutoff", "res_route", "mode_vol"):
                self.levels[reg] = int(val)
            else:
                vs.levels[reg] = int(val)

    def commit(self, v, vs, levels):
        if vs.note is not None:
            n = vs.note + vs.off
            f = vs.freq if vs.freq_off is None else self.pitch[n] if 0 <= n < len(self.pitch) else 0
            levels[regindex("freq_lo", v)] = f & 0xFF
            levels[regindex("freq_hi", v)] = (f >> 8) & 0xFF
        if "pw" in vs.levels:
            levels[regindex("pw_lo", v)] = vs.levels["pw"] & 0xFF
            levels[regindex("pw_hi", v)] = (vs.levels["pw"] >> 8) & 0x0F

    def tick(self):
        edges, levels = [], {}
        for v, vs in enumerate(self.voices):
            vs.hold -= 1
            if vs.hold <= 0 and not vs.ended:
                self.sequencer_step(vs)
            sets = vs.stream.step() if vs.stream else None
            if sets:
                self.apply(v, vs, sets, edges)
            self.commit(v, vs, levels)
        if self.glob:
            sets = self.glob.step()
            if sets:
                self.apply(0, self.voices[0], sets, edges)
        if "cutoff" in self.levels:
            levels[regindex("cutoff_lo", 0)] = self.levels["cutoff"] & 7
            levels[regindex("cutoff_hi", 0)] = self.levels["cutoff"] >> 3
        for reg in ("res_route", "mode_vol"):
            if reg in self.levels:
                levels[regindex(reg, 0)] = self.levels[reg]
        self.prev = grid.reduce_tick(edges + sorted(levels.items()), self.prev)
        return self.prev

    def render(self, ticks):
        return [self.tick() for _ in range(ticks)]


def grid_edges():
    """The register names whose writes are edges (ctrl, ad, sr)."""
    return ("ctrl", "ad", "sr")
