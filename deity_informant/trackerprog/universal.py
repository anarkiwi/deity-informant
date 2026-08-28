"""T3 -- the universal player over the trackerprog document: no program, one procedure.

A tick runs the producer list in order over one memory image: a ``let`` sets a
temp, a ``store`` writes a cell or a register, a ``fetch`` applies its region's
producers to the row it stands on; a loop re-runs its span while a latch holds.
"""

from __future__ import annotations

import hashlib
import json

from ..tuneprog import grid
from ..tuneprog.ir import SID_HI, SID_LO, TrapError
from .document import from_json, text
from .sound import _Unevaluable, evaldata, holds

LOOP_BOUND = 1 << 16


class DataPlayer:
    """Render a trackerprog from its document alone; :attr:`digest` names the text read."""

    def __init__(self, doc):
        self.digest = hashlib.sha256(text(doc).encode()).hexdigest()
        tp = from_json(json.loads(text(doc)))
        self.items = tp["producers"]
        self.loops = tp["loops"]
        self.registers = tp["registers"]
        self.regs = list(self.registers["values"])
        self.m = bytearray(0x10000)
        for span in tp["memory"]:
            b = bytes.fromhex(span["bytes"])
            self.m[span["base"] : span["base"] + len(b)] = b
        for a, v in tp["inputs"].items():
            self.m[int(a)] = v
        self.regions = {f["region"]: f for f in tp["score"]["fetch"]}
        self.fetches = {it["exit"]: it for it in self.items if it["kind"] == "fetch"}
        self.ends = {}
        for n, l in enumerate(self.loops):
            self.ends.setdefault(l["end"], []).append(n)
        self.again = {l["header"]: l["again"] for l in self.loops}
        for it in self.items:  # a guard that is one path temp: read it directly
            g = it.get("guards")
            it["gate"] = (
                g[0][1][1] if g and len(g) == 1 and g[0][1][0] == "tmp" and g[0][2] else None
            )
        self.score = Score(tp["score"]["voices"])
        self.tmps = {}
        self.chained = set()
        self.env = {"tick": 0}
        self.sid = []
        self.obs = []
        self.tick_no = -1

    def ev(self, e, tmps=None):
        return evaldata(e, self.env, self.m, self.tmps if tmps is None else tmps)

    def write(self, it, a, v):
        if not it["lo"] <= a <= it["hi"]:
            raise TrapError("envelope", "$%04X outside [$%04X,$%04X]" % (a, it["lo"], it["hi"]))
        if it["cls"] == "io":
            if SID_LO <= a <= SID_HI:
                self.sid.append((a, v & 0xFF))
            self.m[a] = v & 0xFF
            return
        for i in range(it["w"]):
            self.m[(a + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF

    def fetch(self, it):
        """Apply a region's producers to the row it stands on, all read from the entry state."""
        rgn = self.regions[it["region"]]
        if rgn["refusals"]:
            raise TrapError("fetch not in IR", ", ".join(r["cell"] for r in rgn["refusals"]))
        env, m = self.env, self.m
        local = {}
        for n, e in it["bind"].items():
            try:
                local[n] = self.ev(e)
            except _Unevaluable:
                pass
        c0s, bases, copy = {}, {}, None
        for ch in rgn["chans"]:
            t = ch["table"]
            c0s[t] = evaldata(ch["cursor"], env, m, local)
            if copy is None:
                copy = (evaldata(ch["addr"], env, m, local) - ch["cell"]) // ch["stride"]
            env["byte"] = self.score.probe(copy, self.tick_no, c0s)
            bases[t] = evaldata(ch["base"], env, m, local)
        self.score.begin(copy, self.tick_no, c0s, bases)
        env["byte"] = self.score.lookup(copy)
        fired = []
        for p in rgn["producers"]:
            if not all(holds(g, env, m, local) for g in p["guards"]):
                continue
            if p["kind"] == "store":
                a = evaldata(p["addr"], env, m, local)
                if not p["lo"] <= a <= p["hi"]:
                    raise TrapError(
                        "envelope", "$%04X outside [$%04X,$%04X]" % (a, p["lo"], p["hi"])
                    )
                fired.append((p, a, evaldata(p["expr"], env, m, local)))
            else:
                try:
                    fired.append((p, None, evaldata(p["expr"], env, m, local)))
                except _Unevaluable:
                    pass
        k = next(
            (
                i
                for i, x in enumerate(rgn["exits"])
                if all(holds(g, env, m, local) for g in x["guards"])
            ),
            None,
        )
        if k is None:
            raise TrapError(
                "score out of step",
                "%s leaves by no exit at tick %d" % (it["region"], self.tick_no),
            )
        exit_ = rgn["exits"][k]
        rets = [evaldata(v, env, m, local) for v in exit_["rets"]]
        for p, a, v in fired:
            if p["kind"] == "let":
                self.tmps[it["tmps"][p["name"]]] = v
            else:
                self.write(p, a, v)
        self.tmps[it["exit"]] = k
        if exit_["to"] == "$exit":
            for name, v in zip(it["rets"], rets):
                self.tmps[name] = v
        to = it["chain"].get(str(k))
        if to is not None:
            self.chained.add(to)
            self.fetch(self.fetches[to])

    def one(self, it):
        """Run one item; returns the index to go on at, ``None`` for the next."""
        k = it["kind"]
        try:
            if k == "let" and "guards" not in it:
                v = self.tmps[it["name"]] = self.ev(it["expr"])
                again = self.again.get(it["name"])
                if again is not None:
                    self.tmps[again] = 0
                return None if v else it.get("skip")
            gate = it["gate"]
            if gate is not None:
                on = bool(self.tmps.get(gate))
            else:
                on = all(holds(g, self.env, self.m, self.tmps) for g in it["guards"])
            if k == "let":
                if on:
                    self.tmps[it["name"]] = self.ev(it["expr"])
            elif k == "store":
                if on:
                    self.write(it, self.ev(it["addr"]), self.ev(it["expr"]))
            elif it["exit"] in self.chained:  # a fetch another's exit ran into: done this pass
                self.chained.discard(it["exit"])
            elif on:
                self.fetch(it)
            else:
                self.tmps[it["exit"]] = -1
        except _Unevaluable as e:
            raise TrapError(
                "unevaluable",
                "%s reads %s" % (it.get("name") or it.get("site", {}).get("pc", ""), e),
            ) from e
        return None

    def tick(self):
        self.tick_no += 1
        self.env["tick"] = self.tick_no
        self.sid = []
        self.chained = set()
        tmps = self.tmps
        for i, name in self.registers["in"]:
            tmps[name] = self.regs[i]
        for l in self.loops:
            tmps[l["again"]] = 0
        n, guard = 0, 0
        while n < len(self.items):
            to = self.one(self.items[n])
            if to is not None:
                n = to
                continue
            for k in self.ends.get(n, ()):
                l = self.loops[k]
                if any(
                    tmps.get(name) and all(holds(g, self.env, self.m, tmps) for g in gs)
                    for name, gs in l["latches"]
                ):
                    guard += 1
                    if guard > LOOP_BOUND:
                        raise TrapError("loop bound", l["header"])
                    tmps[l["header"]] = tmps[l["again"]] = 1
                    n = l["first"]
                    break
            n += 1
        for j, e in self.registers["out"]:
            try:
                self.regs[j] = self.ev(e)
            except _Unevaluable:
                pass
        w = [
            (int(r), v)
            for r, (_a, v) in zip(grid.regs([a for a, _v in self.sid]), self.sid)
            if r >= 0
        ]
        self.obs.append(grid.reduce_tick(w, self.obs[-1] if self.obs else None))
        return self.obs[-1]

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
