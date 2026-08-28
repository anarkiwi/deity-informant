"""T3 -- the universal player over the sound as data: no program, one procedure.

A tick runs the producer list in rank order over one memory image. A ``block``
item sets its flag when one of its forward edges is taken -- each edge a branch
condition over memory and temps -- or a fetch resumed
there; every other item runs when its block's flag is set. A ``let`` sets a
temp, a ``phi`` picks by the predecessor that ran last, a ``store`` writes a
cell or a register, a ``fetch`` applies the next recorded fetch of its region,
a ``file`` copies the image into the register file; a loop is re-entered while
its latch edge holds.
"""

from __future__ import annotations

from ..tuneprog import grid
from ..tuneprog.ir import SID_HI, SID_LO, TrapError
from .sound import _Unevaluable, evaldata, holds


class DataPlayer:
    """Render a trackerprog over its lowered tick ``snd`` (:mod:`.sound`)."""

    def __init__(self, tp, snd):
        self.items = sorted(snd["items"], key=lambda x: tuple(x["rank"]))
        self.loops = snd["loops"]
        self.voicevars = set(snd["voicevars"])
        self.rets = snd["rets"]
        self.regs = list(snd["regs"])
        self.m = bytearray.fromhex(snd["image"])
        for a, v in tp["inputs"].items():
            self.m[int(a)] = v
        self.fetches = dict(tp["score"]["fetches"])
        self.pos = {}
        self.tmps = {}
        self.taken = {}  # block uid -> step it last ran
        self.resumed = {}
        self.skip = None
        self.pending = None
        self.pass_step = 0
        self.step = 0
        self.sid = []
        self.obs = []
        self.tick_no = -1
        self.env = {"voice": 0, "tick": 0}
        self.first = {}
        self.entries = {it["uid"]: it for it in self.items if it["kind"] == "fetch"}
        for n, it in enumerate(self.items):
            self.first.setdefault(it["block"], n)
        # a block not entered skips its whole extent: every item ranked under it
        self.extent = {}
        ranks = [tuple(it["rank"]) for it in self.items]
        for n, it in enumerate(self.items):
            if it["kind"] in ("block", "fetch"):
                pre = ranks[n][:-1]
                m = n + 1
                while m < len(ranks) and ranks[m][: len(pre)] == pre:
                    m += 1
                self.extent[n] = m

    def ev(self, e):
        return evaldata(e, self.env, self.m, self.tmps)

    def store(self, item):
        a = self.ev(item["addr"])
        if not item["lo"] <= a <= item["hi"]:
            raise TrapError("envelope", "$%04X outside [$%04X,$%04X]" % (a, item["lo"], item["hi"]))
        v = self.ev(item["value"])
        if item["cls"] == "io":
            if SID_LO <= a <= SID_HI:
                self.sid.append((a, v & 0xFF))
            self.m[a] = v & 0xFF
            return
        for i in range(item["w"]):
            self.m[(a + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF

    def fetch(self, item):
        key = item["region"]
        got = self.fetches.get(key) or ()
        i = self.pos.get(key, 0)
        if i >= len(got):
            raise TrapError("score exhausted", "%s at tick %d" % (key, self.tick_no))
        f = got[i]
        if f["tick"] != self.tick_no:
            raise TrapError(
                "score out of step",
                "%s: row of tick %d at tick %d" % (key, f["tick"], self.tick_no),
            )
        self.pos[key] = i + 1
        for cls, a, v, w, _src in f["cmds"]:
            if cls == "io":
                if SID_LO <= a <= SID_HI:
                    self.sid.append((a, v & 0xFF))
                self.m[a] = v & 0xFF
            else:
                for k in range(w):
                    self.m[(a + k) & 0xFFFF] = (v >> (8 * k)) & 0xFF
        for raw, name in item["tmps"].items():
            if raw in f["temps"]:
                self.tmps[name] = f["temps"][raw]
        uid = item["froms"].get(f["from"])
        if uid is not None:
            self.taken[uid] = self.step
        to = item["tos"].get(f["to"])
        if to in self.entries:  # a fetch that ran straight into a region: its fetch, now
            self.taken[to] = self.step
            self.fetch(self.entries[to])
        elif to is not None:
            self.resumed[to] = self.step
        if f["to"] == "$exit":
            self.skip = item["path"]
            if item["path"]:
                for name, v in zip(item["rets"], f.get("rets", ())):
                    self.tmps[name] = v
            else:
                for j, (_i, name) in enumerate(self.rets["rets"]):
                    if j < len(f.get("rets", ())):
                        self.tmps[name] = f["rets"][j]

    def entered(self, item):
        """Whether one of the block's edges in was taken this pass, or a fetch resumed here."""
        if item.get("entry"):
            return True
        for puid, guards in item["exec"]:
            if self.taken.get(puid, -1) >= self.pass_step and all(
                holds(g, self.env, self.m, self.tmps) for g in guards
            ):
                return True
        return self.resumed.pop(item["uid"], -1) >= self.pass_step

    def one(self, item):
        """Run one item; returns False for a block not entered, whose extent is skipped."""
        self.step += 1
        if self.skip is not None and item["path"].startswith(self.skip):
            return True
        self.skip = None
        k = item["kind"]
        try:
            if k in ("block", "fetch"):
                if self.pending == item["uid"] or self.entered(item):
                    self.pending = None
                    self.taken[item["uid"]] = self.step
                    if k == "fetch":
                        self.fetch(item)
                    return True
                self.taken.pop(item["uid"], None)
                return False
            if self.taken.get(item["block"], -1) < self.pass_step:
                return True
            if k == "let":
                v = self.ev(item["value"])
                self.tmps[item["name"]] = v
                if item["name"] in self.voicevars:
                    self.env["voice"] = v
            elif k == "phi":
                best = max(item["alts"], key=lambda alt: self.taken.get(alt[0], -1))
                if self.taken.get(best[0], -1) < 0:
                    raise TrapError("join without a predecessor", item["name"])
                self.tmps[item["name"]] = self.ev(best[1])
            elif k == "store":
                self.store(item)
        except _Unevaluable as e:
            raise TrapError(
                "unevaluable", "%s reads %s" % (item.get("pc") or item.get("name", ""), e)
            ) from e
        return True

    def tick(self):
        self.tick_no += 1
        self.env["tick"] = self.tick_no
        self.sid = []
        self.skip = None
        self.pass_step = self.step + 1
        for i, name in self.rets["params"]:
            self.tmps[name] = self.regs[i]
        n, guard = 0, 0
        while n < len(self.items):
            it = self.items[n]
            if self.one(it) is False:
                m = self.extent[n]
                if not any(n <= loop["end"] < m for loop in self.loops):
                    n = m
                    continue
            for loop in self.loops:
                if loop["end"] != n:
                    continue
                back = None
                for l, e in loop["latches"]:
                    if self.taken.get(l, -1) >= self.pass_step and self.latch(l, e):
                        back = l
                if back is not None:
                    guard += 1
                    if guard > 1 << 16:
                        raise TrapError("loop bound", str(loop["header"]))
                    for b in loop["body"]:
                        if b != back:
                            self.taken.pop(b, None)
                    self.step += 1
                    self.pending = loop["header"]
                    n = self.first[loop["header"]]
                    break
            else:
                n += 1
        for j, name in self.rets["rets"]:
            if name in self.tmps:
                self.regs[j] = self.tmps[name]
        w = [
            (int(r), v)
            for r, (_a, v) in zip(grid.regs([a for a, _v in self.sid]), self.sid)
            if r >= 0
        ]
        self.obs.append(grid.reduce_tick(w, self.obs[-1] if self.obs else None))
        return self.obs[-1]

    def latch(self, _uid, guards):
        return all(holds(g, self.env, self.m, self.tmps) for g in guards)

    def render(self, ticks):
        try:
            for _ in range(ticks):
                self.tick()
        except TrapError as e:
            return self.obs, {"tick": self.tick_no, "trap": e.why, "detail": e.detail}
        return self.obs, None
