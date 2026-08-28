"""T3 -- the universal player: one fixed interpreter over a trackerprog's data.

The trackerprog carries the certified tick with its fetch regions cut out
(:mod:`.region`) and, in their place, the score as data: one *fetch* per entry of
a region -- the cells and registers it set, the temps it left, the block it
resumed at. The player runs the tick from the post-init image and, at a region's
entry, applies the next fetch instead of reading the score tables. Lifting is the
same run with the regions executed and their effects recorded; certification is
the replay compared with :attr:`~.tuneprog.verify.Verifier.obs` tick for tick.
"""

from __future__ import annotations

from ..lifter import STATUS_BITS
from ..tuneprog import grid
from ..tuneprog.graph import EXIT
from ..tuneprog.ir import (
    Bin,
    Call,
    Const,
    Goto,
    If,
    Let,
    Phi,
    REGVAR,
    Return,
    SID_HI,
    SID_LO,
    Store,
    Switch,
    TrapError,
    Var,
    copymap_bands,
    evalbin,
    hits_band,
)
from ..tuneprog.machine import STATUS, entry_frame

DEFAULT_ORDER = ("ad", "sr", "ctrl")


def _status(regs):
    return sum(regs[i] << s for i, s in STATUS_BITS) | 0x20


class Player:
    """Render a tuneprog whose score is data.

    ``fetches`` is ``None`` to *record* (the regions run and each entry is logged)
    or ``{region key: [fetch, ...]}`` to *replay* them.
    """

    def __init__(self, prog, fetch, inputs=None, fetches=None, envvars=None, watch=None):
        self.prog, self.fetch = prog, fetch
        self.envvars = envvars or {}
        self.watch = watch  # {proc: the name whose value says the voice}; logs block runs
        self.log = []  # (tick, proc, label, voice value, branch outcome)
        self.voice = -1
        self.m = bytearray(prog.image())
        lo, hi = prog.meta.get("load") or (0, 0)
        self.k = bytearray(0x10000)
        self.k[lo:hi] = b"\1" * (hi - lo)
        self.k[0x100:0x200] = b"\1" * 0x100
        self.k[0xD000:0xE000] = b"\1" * 0x1000
        self.regs = [0] * 16
        self.regs[3] = 0xFF
        self.bank = 2
        self.inputs = dict(inputs or {})
        self.ro = copymap_bands(prog.storage)
        self.record = fetches is None
        self.fetches = {} if self.record else fetches
        self.pos = {}
        self.rec = None
        self.depth = 0
        self.sid = []
        self.obs = []
        self.tick_no = -1
        self.steps = 0
        self.setbank()

    # ---- the machine ----------------------------------------------------------
    def setbank(self):
        p = (self.m[1] | ~self.m[0]) & 7
        self.bank = 0 if not p & 3 else (1 if not p & 4 else 2)

    def rd(self, a, w, cls):
        v = 0
        for i in range(w):
            b = (a + i) & 0xFFFF
            if cls == "ram" or (cls != "io" and self.k[b]):
                x = self.m[b]
            elif cls == "io" and self.bank != 2:
                x = self.m[b]
            elif b in self.inputs:
                x = self.inputs[b]
            else:
                raise TrapError("external input", "$%04X" % b)
            v |= x << (8 * i)
        return v

    def wr(self, a, v, w):
        for i in range(w):
            b = (a + i) & 0xFFFF
            self.m[b] = (v >> (8 * i)) & 0xFF
            self.k[b] = 1
        if a <= 1:
            self.setbank()

    def iostore(self, a, v, src):
        if self.bank == 2 and SID_LO <= a <= SID_HI:
            self.sid.append((a, v))
        else:
            self.k[a] = 1
        self.m[a] = v
        del src

    def push(self, v):
        self.m[0x100 + self.regs[3]] = v & 0xFF
        self.regs[3] = (self.regs[3] - 1) & 0xFF

    def enter(self, entry=None):
        frame = entry_frame(entry or {"kind": "sub"})
        ret = 0x0000 if frame else 0x0001
        self.push(ret >> 8)
        self.push(ret & 0xFF)
        for what in frame:
            self.push(_status(self.regs) if what is STATUS else self.regs[what])

    # ---- the interpreter ------------------------------------------------------
    def ev(self, e, F):
        t = type(e)
        if t is Var:
            return F[e.n]
        if t is Const:
            return e.v
        if t is Bin:
            return evalbin(e.op, self.ev(e.a, F), self.ev(e.b, F), e.w)
        a = self.ev(e.a, F)
        if not e.lo <= a <= e.hi or a + e.w - 1 > e.hi:
            raise TrapError("envelope", "$%04X outside [$%04X,$%04X]" % (a, e.lo, e.hi))
        if self.rec is not None and any(lo <= e.lo and e.hi <= hi for lo, hi in self.fetch.tables):
            self.rec["reads"].append(a)
        return self.rd(a, e.w, e.cls)

    def _store(self, s, F):
        a = self.ev(s.a, F)
        if not s.lo <= a <= s.hi or a + s.w - 1 > s.hi:
            raise TrapError("envelope", "$%04X outside [$%04X,$%04X]" % (a, s.lo, s.hi))
        v = self.ev(s.v, F)
        if self.ro and hits_band(self.ro, a, s.w):
            raise TrapError("copymap", "$%04X at $%04X" % (a, s.src))
        if self.rec is not None:
            self.rec["cmds"].append([s.cls, a, v & ((1 << (8 * s.w)) - 1), s.w, s.src])
        if s.cls == "io":
            self.iostore(a, v & 0xFF, s.src)
        elif s.cls == "raw":
            self.m[a] = v & 0xFF
        else:
            self.wr(a, v, s.w)

    def apply(self, key, F):
        """Replay the next fetch of a region: its effects, its temps, where it resumed."""
        got = self.fetches.get(key) or ()
        i = self.pos.get(key, 0)
        if i >= len(got):
            raise TrapError("score exhausted", "%s:%s at tick %d" % (key[0], key[1], self.tick_no))
        self.pos[key] = i + 1
        f = got[i]
        for cls, a, v, w, src in f["cmds"]:
            if cls == "io":
                self.iostore(a, v & 0xFF, src)
            elif cls == "raw":
                self.m[a] = v & 0xFF
            else:
                self.wr(a, v, w)
        F.update(f["temps"])
        return f

    def _begin(self, key, region, F):
        self.rec = {
            "tick": self.tick_no,
            "key": key,
            "env": {n: F[n] for n in self.envvars.get(key, ()) if n in F},
            "cmds": [],
            "reads": [],
            "temps": {},
            "from": None,
            "to": None,
            "depth": self.depth,
            "region": region,
        }

    def _end(self, F, prev, to, rets=None):
        r = self.rec
        r["temps"] = {n: F[n] for n in r["region"].liveout if n in F}
        r["from"], r["to"] = prev, to
        if rets is not None:
            r["rets"] = list(rets)
        del r["depth"], r["region"]
        self.fetches.setdefault(r["key"], []).append(r)
        self.rec = None

    def run(self, name, args=()):
        """Run procedure ``name``; returns the values of its ``rets``."""
        proc = self.prog.procs[name]
        F = dict(zip((REGVAR[i] for i in proc.params), args))
        lbl, prev = proc.entry, None
        self.depth += 1
        regions = self.fetch.regions
        try:
            while True:
                key = (name, lbl)
                region = regions.get(key)
                if region is not None:
                    if self.record:
                        # a region entered straight from another ends that fetch here;
                        # one entered inside a fetch's callee is that fetch's own
                        if self.rec is None:
                            self._begin(key, region, F)
                        elif self.rec["depth"] == self.depth:
                            self._end(F, prev, lbl)
                            self._begin(key, region, F)
                    else:
                        f = self.apply(key, F)
                        if f["to"] == EXIT:
                            return tuple(f["rets"])
                        lbl, prev = f["to"], f["from"]
                        continue
                blk = proc.blocks[lbl]
                self.steps += 1
                if self.watch is not None:
                    n = self.watch.get(name)
                    if n is not None and n in F:
                        self.voice = (name, F[n])
                    self.log.append([self.tick_no, name, lbl, self.voice, -1])
                for s in blk.stmts:
                    t = type(s)
                    if t is Let:
                        F[s.n] = self.ev(s.e, F)
                    elif t is Store:
                        self._store(s, F)
                    elif t is Call:
                        vals = self.run(s.proc, [self.ev(a, F) for a in s.args])
                        F.update(zip(s.rets, vals))
                    elif t is Phi:
                        F[s.n] = F[s.args[prev]]
                    elif not self.ev(s.e, F):
                        raise TrapError(s.why, blk.label)
                term = blk.term
                k = type(term)
                prev = lbl
                if k is Goto:
                    lbl = term.to
                elif k is If:
                    taken = self.ev(term.c, F)
                    lbl = term.t if taken else term.f
                    if self.watch is not None:
                        self.log[-1][4] = 1 if taken else 0
                elif k is Switch:
                    v = self.ev(term.e, F)
                    if self.watch is not None:
                        self.log[-1][4] = v
                    lbl = next((l for c, l in term.cases if c == v), None)
                    if lbl is None:
                        raise TrapError("switch", "$%04X value %d" % (blk.src, v))
                else:
                    if k is not Return:
                        raise TrapError(term.why, "$%04X %s" % (blk.src, blk.label))
                    vals = tuple(self.ev(v, F) for v in term.vals)
                    if self.rec is not None and self.rec["depth"] == self.depth:
                        self._end(F, prev, EXIT, vals)
                    return vals
                if self.rec is not None and self.rec["depth"] == self.depth:
                    if lbl in self.rec["region"].exits:
                        self._end(F, prev, lbl)
        finally:
            self.depth -= 1

    # ---- ticks -----------------------------------------------------------------
    def _call(self, name):
        proc = self.prog.procs[name]
        vals = self.run(name, [self.regs[i] for i in proc.params])
        for i, v in zip(proc.rets, vals):
            self.regs[i] = v

    def run_init(self):
        meta = self.prog.meta
        self.regs[0] = int(meta.get("song") or 0)
        self.enter()
        self._call(meta["init_proc"])
        self.sid = []
        return self

    def tick(self):
        """One tick: its :class:`~.tuneprog.grid.TickObs`, appended to :attr:`obs`."""
        self.tick_no += 1
        self.sid = []
        self.enter(self.prog.meta["entry"])
        self._call(self.prog.meta["tick_proc"])
        w = [
            (int(r), v)
            for r, (_a, v) in zip(grid.regs([a for a, _v in self.sid]), self.sid)
            if r >= 0
        ]
        self.obs.append(grid.reduce_tick(w, self.obs[-1] if self.obs else None))
        return self.obs[-1]

    def render(self, ticks):
        """``(obs, trap)``: the observable over ``ticks``, and what stopped it, if anything."""
        try:
            for _ in range(ticks):
                self.tick()
        except TrapError as e:
            return self.obs, {"tick": self.tick_no, "trap": e.why, "detail": e.detail}
        return self.obs, None
