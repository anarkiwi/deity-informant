"""T3 -- the universal player over the sound as data: no program, one procedure.

A tick runs the producer list in rank order over one memory image: a ``block``
runs when a forward edge in is taken or a fetch resumed there, a ``let`` sets a
temp, a ``phi`` picks by the predecessor that ran last, a ``store`` writes a cell
or a register, a ``fetch`` applies its region's producers to the row it stands on.
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
        self.score = Score(tp["score"]["voices"])
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
        """Apply a region's producers to the row it stands on, all read from the entry state."""
        if item.get("refused"):
            raise TrapError("fetch not in IR", ", ".join(item["refused"]))
        env, m, tmps = self.env, self.m, self.tmps
        c0s, bases, copy = {}, {}, None
        for ch in item["chans"]:
            t = ch["table"]
            c0s[t] = evaldata(ch["cursor"], env, m, tmps)
            if copy is None:
                copy = (evaldata(ch["addr"], env, m, tmps) - ch["cell"]) // ch["stride"]
            env["byte"] = self.score.probe(copy, self.tick_no, c0s)
            bases[t] = evaldata(ch["base"], env, m, tmps)
        self.score.begin(copy, self.tick_no, c0s, bases)
        env["byte"] = self.score.lookup(copy)
        fired = []
        for it in item["items"]:
            if not all(holds(g, env, m, tmps) for g in it["when"]):
                continue
            if it["kind"] == "store":
                a = evaldata(it["addr"], env, m, tmps)
                if not it["lo"] <= a <= it["hi"]:
                    raise TrapError(
                        "envelope", "$%04X outside [$%04X,$%04X]" % (a, it["lo"], it["hi"])
                    )
                fired.append((it, a, evaldata(it["value"], env, m, tmps)))
            else:
                try:
                    fired.append((it, None, evaldata(it["value"], env, m, tmps)))
                except _Unevaluable:
                    pass
        exit_ = next(
            (x for x in item["exits"] if all(holds(g, env, m, tmps) for g in x["when"])), None
        )
        if exit_ is None:
            raise TrapError(
                "score out of step",
                "%s leaves by no exit at tick %d" % (item["region"], self.tick_no),
            )
        rets = [evaldata(v, env, m, tmps) for v in exit_["rets"]]
        for it, a, v in fired:
            if it["kind"] == "let":
                tmps[it["name"]] = v
            elif it["cls"] == "io":
                if SID_LO <= a <= SID_HI:
                    self.sid.append((a, v & 0xFF))
                m[a] = v & 0xFF
            else:
                for k in range(it["w"]):
                    m[(a + k) & 0xFFFF] = (v >> (8 * k)) & 0xFF
        uid = item["tos"].get(exit_["from"])
        if uid is not None:
            self.taken[uid] = self.step
        to = item["tos"].get(exit_["to"])
        if to in self.entries:  # a fetch that ran straight into a region: its fetch, now
            self.taken[to] = self.step
            self.fetch(self.entries[to])
        elif to is not None:
            self.resumed[to] = self.step
        if exit_["to"] == "$exit":
            self.skip = item["path"]
            names = item["rets"] if item["path"] else [n for _i, n in self.rets["rets"]]
            for name, v in zip(names, rets):
                tmps[name] = v

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


class Score:
    """The rows per copy, consumed in order: one per fetch tick, one more per base change."""

    def __init__(self, voices):
        self.rows = {v["copy"]: v["rows"] for v in voices}
        self.starts = {}
        for v in voices:
            t, got = v["start"], []
            for r in v["rows"]:
                got.append(t)
                t += r["dur"]
            self.starts[v["copy"]] = got
        self.at, self.tick, self.c0, self.base = {}, {}, {}, {}

    def _byte(self, copy, i, c0, t, pos):
        rows = self.rows.get(copy) or ()
        if i >= len(rows) or t not in c0:
            return None
        row = rows[i]
        k = pos - c0[t] - (row.get("at") or {}).get(t, 0)
        b = row["bytes"].get(t) or ()
        return b[k] if 0 <= k < len(b) else None

    def probe(self, copy, tick, c0s):
        """A byte lookup before the row is settled: the current row, else the next."""
        i = self.at.get(copy)
        here = dict(self.c0.get(copy) or {})
        here.update({t: c for t, c in c0s.items() if t not in here})
        cands = [] if i is None or self.tick.get(copy) != tick else [(i, here)]
        cands.append((0 if i is None else i + 1, c0s))

        def byte(t, pos):
            for j, c0 in cands:
                if j >= len(self.rows.get(copy) or ()):
                    raise TrapError("score exhausted", "copy %s at tick %d" % (copy, tick))
                v = self._byte(copy, j, c0, t, pos)
                if v is not None:
                    return v
            raise TrapError(
                "score out of step", "%s at %d off copy %s's rows, tick %d" % (t, pos, copy, tick)
            )

        return byte

    def lookup(self, copy):
        i, c0 = self.at[copy], self.c0[copy]

        def byte(t, pos):
            v = self._byte(copy, i, c0, t, pos)
            if v is None:
                raise TrapError(
                    "score out of step", "%s at %d off copy %s's row %d" % (t, pos, copy, i)
                )
            return v

        return byte

    def begin(self, copy, tick, c0s, bases):
        """Settle the row a fetch stands on: the current, or the next if the tick or base moved."""
        i = self.at.get(copy)
        base = self.base.get(copy) or {}
        if (
            i is None
            or self.tick[copy] != tick
            or any(t in base and base[t] != b for t, b in bases.items())
        ):
            i = 0 if i is None else i + 1
            rows = self.rows.get(copy) or ()
            if i >= len(rows):
                raise TrapError("score exhausted", "copy %s at tick %d" % (copy, tick))
            if self.starts[copy][i] != tick:
                raise TrapError(
                    "score out of step",
                    "copy %s: row of tick %d at tick %d" % (copy, self.starts[copy][i], tick),
                )
            self.at[copy], self.tick[copy], self.c0[copy], self.base[copy] = i, tick, {}, {}
        for t in c0s:
            self.c0[copy].setdefault(t, c0s[t])
            self.base[copy].setdefault(t, bases[t])
