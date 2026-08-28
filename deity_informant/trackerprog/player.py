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
    """One armed stream: the step due next, the ticks left on it, the cycle it is in."""

    steps: list
    at: int = 0
    left: int = 0
    cycle: list | None = None
    phase: int = 0
    loop: int | None = None

    def step(self):
        """The sets due this tick: a held step's on its tick, a cycle's each tick round."""
        if self.left > 0:
            self.left -= 1
            if self.cycle is not None:
                self.phase += 1
                return self.cycle[self.phase % len(self.cycle)]
            return None
        if self.at >= len(self.steps):
            if self.loop is None:
                return None
            self.at = self.loop
        s = self.steps[self.at]
        self.at += 1
        if "cycle" in s:
            self.cycle, self.phase = s["cycle"], 0
            self.left = len(s["cycle"]) * int(s["times"]) - 1
            return s["cycle"][0]
        self.cycle = None
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
    index: int | None = None
    freq_off: int | None = 0
    freq: int = 0
    levels: dict = field(default_factory=dict)
    streams: dict = field(default_factory=dict)
    loop: dict | None = None
    ended: bool = False

    def pattern(self):
        if 0 <= self.pos < len(self.order):
            return self.patterns[self.order[self.pos]["pattern"]]
        return None

    def advance(self, end):
        """The next row, across patterns, honouring the voice's loop or the terminator."""
        pat = self.pattern()
        if pat is None or self.row >= len(pat):
            self.pos += 1
            self.row = 0
            if self.pos >= len(self.order):
                if end.get("kind") == "loop" and self.loop:
                    self.pos, self.row = int(self.loop["pos"]), int(self.loop["row"])
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
        self.instruments = tp["instruments"]
        self.voices = [
            Voice(v["order"], v["patterns"], loop=v.get("loop")) for v in tp["score"]["voices"]
        ]
        self.end = tp["score"].get("end") or {}
        g = tp["score"].get("global")
        self.glob = Voice(g["order"], g["patterns"], loop=g.get("loop")) if g else None
        self.levels = {}
        self.prev = None
        self.transpose = 0
        self.order = tuple(tp["meta"].get("commit_order") or DEFAULT_ORDER)

    def sequencer_step(self, vs):
        ev = vs.advance(self.end)
        self.transpose = vs.order[vs.pos].get("transpose", 0) if ev is not None else 0
        if ev is None:
            vs.hold = 1 << 30
            return
        vs.hold = int(ev["dur"])
        if ev.get("note") is not None:
            vs.note = int(ev["note"]) + int(self.transpose)
        for reg, val in ev.get("enter", ()):  # the levels a loop re-enters with, silently
            if reg == "freq":
                vs.freq_off, vs.freq = None, int(val)
            elif reg in ("cutoff", "res_route", "mode_vol"):
                self.levels[reg] = int(val)
            else:
                vs.levels[reg] = int(val)
        ref = self.instruments.get(ev.get("ins")) or {}
        vs.streams = {
            lane: self.cursor(sid) for lane, sid in ref.items() if not lane.startswith("pre_")
        }

    def lanes(self, vs):
        """A voice's lanes in the order the edges commit: ``meta.commit_order`` first."""
        first = [l for l in self.order if l in vs.streams]
        return first + [l for l in vs.streams if l not in first]

    def cursor(self, sid):
        st = self.streams[sid]
        return Cursor(st["steps"], loop=st.get("loop"))

    def apply(self, v, vs, sets, edges):
        """One step's sets: edges in their own order, level and pitch operands as state."""
        for reg, val, *rest in sets:
            if reg == "note_abs":
                vs.note, vs.index, vs.freq_off = int(val), int(val), 0
            elif reg == "note_off":
                vs.index, vs.freq_off = (vs.note or 0) + int(val), 0
            elif reg == "freq":
                vs.freq_off, vs.freq = None, int(val)
            elif reg == "freq_delta":
                vs.freq_off, vs.freq = None, (self.freq_of(vs) + int(val)) & 0xFFFF
            elif reg == "freq_ts":
                m, shift = int(val), int(rest[0])
                i = vs.index if vs.index is not None else vs.note or 0
                semi = (self.pitch[i + 1] - self.pitch[i]) >> shift
                vs.freq_off, vs.freq = None, (self.freq_of(vs) + m * semi) & 0xFFFF
            elif reg == "pw_delta":
                vs.levels["pw"] = (vs.levels.get("pw", 0) + int(val)) & 0xFFF
            elif reg in ("cutoff_delta", "res_route_delta", "mode_vol_delta"):
                base_ = reg[: -len("_delta")]
                m = 0x7FF if base_ == "cutoff" else 0xFF
                self.levels[base_] = (self.levels.get(base_, 0) + int(val)) & m
            elif reg in grid_edges():
                edges.append((regindex(reg, v), int(val)))
            elif reg in ("cutoff", "res_route", "mode_vol"):
                self.levels[reg] = int(val)
            else:
                vs.levels[reg] = int(val)

    def freq_of(self, vs):
        """The voice's frequency: its pitch entry at the note and offset, or the value set."""
        if vs.freq_off is None:
            return vs.freq
        n = vs.index if vs.index is not None else vs.note or 0
        return self.pitch[n] if 0 <= n < len(self.pitch) else 0

    def commit(self, v, vs, levels):
        if vs.note is not None:
            f = self.freq_of(vs)
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
            for lane in self.lanes(vs):
                sets = vs.streams[lane].step()
                if sets:
                    self.apply(v, vs, sets, edges)
            self.commit(v, vs, levels)
        if self.glob is not None:
            self.glob.hold -= 1
            if self.glob.hold <= 0 and not self.glob.ended:
                self.sequencer_step(self.glob)
            for cur in self.glob.streams.values():
                sets = cur.step()
                if sets:
                    self.apply(0, self.glob, sets, edges)
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
