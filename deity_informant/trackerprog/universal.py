"""The universal player of prototype-trackerprog.md sections 4 and 5.

One fixed procedure over a trackerprog's data.  The object carries a pitch
table, instruments, streams, bounded accumulators and a score; this module
carries no tune, no family and no table of its own.  What a family's player
does -- register offsets, voice loops, scratch pairs, an inherited carry flag,
a pattern cursor counted in bytes -- is either spent by the lift or stated as
one datum in the object.

The tick is section 4's, verbatim::

    for v in meta.voice_order:
        tempo.step(); if row_boundary(v): sequencer_step(v)
        streams(v); accs(v); commit(v)

and ``commit`` emits, per voice: the prelude rows due this tick, the other
streams' ``set`` steps, the event's ``set_register`` writes, the freq/pw
producers in declared order, then ad/sr/ctrl in ``meta.commit_order``.

An accumulator is section 5's record -- ``target``, ``width``, ``delta``,
``bound``, ``policy``, ``rate``, ``phase``, ``links``, ``scope`` -- read as
data, so the evaluator below dispatches on the *form* of a delta and never on
the name of an effect.

Two invariants the object must keep, and this module enforces:

* **the pitch table is bounded.**  ``pitch.freq`` is indexed by a row of the
  note space and by nothing else; there is no arithmetic on a pitch index, no
  stride, and no read past the end.  A note the score plays that is not a row
  of the space is an error, not a value.
* **nothing reads another voice's state.**  An expression reads the cells of
  the voice being committed, and no others.  Where a tune's value genuinely
  depends on another voice, a **generator** (``obj["generators"]``) carries it:
  private state with declared initial values, updated only by the events this
  player publishes, and a value over that state alone.  Two generators
  mirroring the same fact keep two copies; nothing is shared, so nothing
  aliases.
"""

from __future__ import annotations

REG = {"freq_lo": 0, "freq_hi": 1, "pw_lo": 2, "pw_hi": 3, "ctrl": 4, "ad": 5, "sr": 6}
EDGE = ("ctrl", "ad", "sr")  # section 2 rule 1: every write kept, in tick order


class Player:
    """Render one trackerprog.  ``tick()`` returns the tick's SID writes."""

    def __init__(self, obj):
        self.o = obj
        m = obj["meta"]
        self.order = m["voice_order"]
        self.rate, self.phase = m["tempo"]["rate"], m["tempo"]["phase"]
        self.commit_order = m["commit_order"]
        s0 = obj["state0"]
        n = m["voices"]
        self.c = {
            "ins": list(s0["ins"]),
            "wave": list(s0["wave"]),
            "pwdir": list(s0["pwdir"]),
            "orderpos": [0] * n,
            "rowsleft": [0] * n,
            "dur": [0] * n,
            "freq": [0] * n,
        }
        self.noteref = [0] * n
        self.cur = None  # the accumulator stepping, for its own per-note table
        self.evrow = [0] * n
        self.tie = [False] * n
        self.armed = [[] for _ in range(n)]  # the accs the score armed
        self.divider = [dict((k, d[i]) for k, d in s0["dividers"].items()) for i in range(n)]
        self.pw = {k: v["pw"][0] | v["pw"][1] << 8 for k, v in obj["instruments"].items()}
        self.flags = {}
        self.gen = {k: dict(g["state"]) for k, g in obj.get("generators", {}).items()}
        self.own = None
        self.tick_no = -1
        self.stopping = 0
        self.v = 0
        self.acc = 0  # the tick-scratch accumulator an acc with scope "tick" uses
        self.w = []

    # ---- reading the object ---------------------------------------------------
    def cell(self, name):
        """A cell of the voice being committed.  There is no other-voice form."""
        v = self.v
        if name == "voice_index":
            return v
        if name == "counter":
            return self.tick_no
        if name == "freq_hi":
            return self.c["freq"][v] >> 8
        if name == "freq_lo":
            return self.c["freq"][v] & 0xFF
        if name in ("pw", "pw_lo", "pw_hi"):
            p = self.pw[str(self.c["ins"][v])]
            return p if name == "pw" else (p & 0xFF if name == "pw_lo" else p >> 8)
        return self.c[name][v] & 0xFFFF

    # ---- the tuning, and the tables the modulators keep over it ----------------
    def noteval(self, field):
        """``field`` of the voice's current note: the tuning, or a modulator's table.

        A pitch table is a pitch table -- ``freq`` and nothing else.  Any other
        field belongs to the accumulator asking for it, over the same rows.  A
        note the tuning does not have is the instrument's seed, which carries
        the same fields for itself.
        """
        ref = self.noteref[self.v]
        if ref == "seed":
            e = self.instr()["seed"][field]
        elif field == "freq":
            e = self.o["pitch"]["freq"][ref]
        else:
            t = self.cur.get(field)
            e = None if t is None or not 0 <= ref < len(t) else t[ref]
        if e is None:
            raise AssertionError("no %s for this note: it is never modulated that way" % field)
        if isinstance(e, int):
            return e & 0xFFFF
        if "row" in e:
            return self.o["pitch"]["freq"][e["row"]]
        return self.ev(e) & 0xFFFF

    def ev(self, e, ov=None):
        """Evaluate one section 5 expression node."""
        if isinstance(e, int):
            return e
        if isinstance(e, str):
            return (ov or {})[e]
        k, a = next(iter(e.items()))
        if k == "const":
            return (ov or {})[a] if isinstance(a, str) else a
        if k == "cell":
            return self.cell(a)
        if k == "own":
            return self.own[a]
        if k == "sid_base":
            return 7 * (self.v if a == "reader" else a)
        if k == "u16":
            return (self.ev(a[0], ov) & 0xFF) | (self.ev(a[1], ov) & 0xFF) << 8
        if k == "gen":
            g = self.o["generators"][a]
            keep, self.own = self.own, self.gen[a]
            try:
                return self.ev(g["value"], ov)
            finally:
                self.own = keep
        if k == "note":
            return self.noteval(a if isinstance(a, str) else self.ev(a, ov))
        if k == "shr":
            return self.ev(a[0], ov) >> self.ev(a[1], ov)
        if k == "flag":
            return self.flags.get(a, 0)
        if k == "payload":
            return ov[a]
        if k == "ins":
            x = self.instr()
            for part in a.split("."):
                x = x[int(part)] if part.isdigit() else x[part]
            return x
        if k == "and":
            return self.ev(a[0], ov) & self.ev(a[1], ov)
        if k == "add":
            return self.ev(a[0], ov) + self.ev(a[1], ov)
        if k == "sub":
            return self.ev(a[0], ov) - self.ev(a[1], ov)

        if k == "field":
            return self.ev(a[0], ov) & a[1]
        if k == "bit":
            return (self.ev(a[0], ov) >> a[1]) & 1
        if k == "fold":  # the triangle a free counter's low bits already are
            x = self.ev(a[0], ov) & a[1]
            return x ^ a[1] if x > a[1] >> 1 else x
        if k == "stream":
            return self.o["streams"][a[0]]["rows"][self.ev(a[1], ov)]
        raise KeyError("expression form %r" % (k,))

    def publish(self, event, voice, payload=None, acc=None):
        """One musical fact, offered to every generator that subscribes to it."""
        for name, g in self.o.get("generators", {}).items():
            for sub in g["on"]:
                if sub["event"] != event or sub["voice"] != voice or sub.get("acc") != acc:
                    continue
                own = self.gen[name]
                for k, e in sub["set"].items():  # a generator mirrors; it never counts
                    own[k] = self.ev(e, payload) & 0xFF

    def notenumber(self, v):
        """The number the voice's note carries in the tune's own numbering."""
        ref = self.noteref[v]
        return self.instr(v)["seed"]["number"] if ref == "seed" else self.o["pitch"]["notes"][ref]

    def instr(self, v=None):
        return self.o["instruments"][str(self.c["ins"][self.v if v is None else v])]

    def guards(self, gs, ov=None):
        for lhs, op, rhs in gs or ():
            x, y = self.ev(lhs, ov), self.ev(rhs, ov)
            if not {">=": x >= y, "<": x < y, "!=": x != y, "==": x == y}[op]:
                return False
        return True

    # ---- the tick -------------------------------------------------------------
    def tick(self):
        self.tick_no += 1
        self.w = []
        if self.stopping:
            if self.stopping == 1:
                self.stopping = 2
                self.w = [tuple(x) for x in self.o["globals"]["stop_writes"]]
            return self.w
        boundary = self.tick_no % self.rate == self.phase
        for v in self.order:
            self.v = v
            pre, prod, edge = [], [], []
            if boundary:
                self.c["rowsleft"][v] -= 1
                if self.c["rowsleft"][v] < 0:
                    self.sequencer_step(prod, edge)
                    if self.stopping:
                        return self.w  # the stop terminator abandons the tick
                    self.commit(pre, prod, edge)
                    if self.o["meta"]["row_consumes_tick"]:
                        continue
                if self.c["rowsleft"][v] == 0 and not self.tie[v]:
                    self.rows(self.instr()["prelude"]["stream"], pre, pre)
            self.accs(prod, edge)
            self.commit(pre, prod, edge)
        return self.w

    def commit(self, pre, prod, edge):
        for t, x in pre:  # 1 the prelude rows due this tick
            self.emit(t, x)
        for t, x in prod:  # 4 the freq/pw producers, in declared order
            self.emit(t, x)
        d = dict(edge)
        for t in self.commit_order:  # 5 ad, sr, ctrl in meta.commit_order
            if t in d:
                self.emit(t, d[t])

    def emit(self, target, val):
        self.w.append((7 * self.v + REG[target], val & 0xFF))

    def rows(self, name, prod, edge, ov=None):
        """A stream's ``set`` steps, routed by register class."""
        for row in self.o["streams"][name]["rows"]:
            for t, e in row["sets"]:
                (edge if t in EDGE else prod).append((t, self.ev(e, ov) & 0xFF))

    # ---- the sequencer --------------------------------------------------------
    def sequencer_step(self, prod, edge):
        v = self.v
        o = self.o["score"]["orders"][v]
        if self.c["orderpos"][v] >= len(o["play"]):
            if o["end"] != "jump":
                self.stopping = 1
                return
            self.c["orderpos"][v] = self.evrow[v] = 0
            self.c["rowsleft"][v] = 0
            self.publish("order", v, {"pos": 0})
        pat = self.o["score"]["patterns"][str(o["play"][self.c["orderpos"][v]])]
        e = pat["events"][self.evrow[v]]
        self.armed[v] = []
        self.c["rowsleft"][v] = self.c["dur"][v] = e["dur"]
        self.tie[v] = e["tie"]
        gate = 0xFE if e["gate"] == "off" else 0xFF
        if e["gate"] == "on":
            if e["ins"] is not None:
                self.c["ins"][v] = e["ins"]
                self.publish("instrument", v, {"ins": e["ins"]})
            if e["arm"] is not None:
                self.armed[v].append(e["arm"])
            ref = e["note"]
            if isinstance(ref, int) and not 0 <= ref < len(self.o["pitch"]["freq"]):
                raise AssertionError("row %d is outside the tuning" % ref)
            self.noteref[v] = ref
            self.publish("note", v, {"note": self.notenumber(v)})
            f = self.noteval("freq")
            self.c["freq"][v] = f
            prod += [("freq_hi", f >> 8), ("freq_lo", f & 0xFF)]
        self.c["wave"][v] = self.instr()["wave"]
        self.publish("sound", v, {"wave": self.c["wave"][v]})
        self.rows(self.o["meta"]["note_row"], prod, edge, {"gate": gate})
        if "cursor" in pat:  # a cursor publishes its position, not an increment
            self.publish("row", v, {"pos": pat["cursor"][self.evrow[v]]})
        self.evrow[v] += 1
        if self.evrow[v] == len(pat["events"]):
            self.evrow[v] = 0
            self.c["orderpos"][v] += 1
            self.publish("order", v, {"pos": self.c["orderpos"][v]})

    # ---- the accumulators -----------------------------------------------------
    def accs(self, prod, edge):
        v = self.v
        arms = list(self.instr()["accs"]) + list(self.armed[v])
        for name, d in self.o["globals"]["flags"].items():
            self.flags[name] = self.ev(d["default"])
        for arm in sorted(arms, key=lambda a: self.o["accs"][a["acc"]]["rank"]):
            self.cur = self.o["accs"][arm["acc"]]
            self.step(self.cur, arm, prod, edge)

    def step(self, a, ov, prod, edge):
        v = self.v
        if not self.guards(a.get("when"), ov):
            return
        if a.get("trap"):
            raise AssertionError("the arm the certified horizon never takes")
        k = self.ev(a.get("rate", 1), ov)
        if k > 1:  # section 3.3's divider, the one meaning of rate
            self.divider[v][a["id"]] = self.divider[v].get(a["id"], 0) - 1
            if self.divider[v][a["id"]] >= 0:
                return
            self.divider[v][a["id"]] = k - 1
        pol = a["policy"]
        val = self.load(a)
        if isinstance(pol, dict) and "reload" in pol:
            val = self.ev(pol["reload"], ov)
        out = val
        if "delta" in a and self.guards(a.get("delta_when"), ov):
            out = self.apply(a, ov, val)
        elif "flag" in a:
            self.flags[a["flag"]["name"]] = a["flag"]["unguarded"]
        emitted = val if a.get("emit") == "entry" else out
        self.store(a, out)
        for target, part in a["produce"]:
            self.emit_part(prod, target, emitted, part)
        g = a.get("gate")
        if g:
            key = "true" if self.guards(a.get("step_when"), ov) else "false"
            for t, e in g[key]:
                (edge if t in EDGE else prod).append((t, self.ev(e, ov) & 0xFF))

    def apply(self, a, ov, val):
        """One step of a bounded accumulator: delta, bound, policy, phase."""
        if not self.guards(a.get("step_when"), ov):
            return val
        d = a["delta"]
        mask = (1 << a["width"]) - 1
        if "repeat" in d:  # the closed triangle: n additions, and the carry they leave
            step, n = self.ev(d["repeat"][0], ov), self.ev(d["repeat"][1], ov)
            f = a.get("flag")
            if f:
                self.flags[f["name"]] = f["seed"]
            for _ in range(n):
                s = val + step
                if f:
                    self.flags[f["name"]] = 1 if s > mask else 0
                val = s & mask
            return val
        step = self.ev(d, ov)
        ph = self.ev(a["phase"], ov) if "phase" in a else 0
        out = (val - step if ph else val + step) & mask
        b = a.get("bound")
        if b and a["policy"] == "reflect":
            lo, hi = b["interval"]
            turn = (out >> b["shift"]) == ((hi if not ph else lo) >> b["shift"])
            if turn:
                c = self.c[a["phase"]["cell"]]
                c[self.v] = (c[self.v] + (-1 if ph else 1)) & 0xFF
                self.publish("turn", self.v, {"phase": c[self.v]}, acc=a["id"])
        return out

    def load(self, a):
        s, i = a["cell"], str(self.c["ins"][self.v])
        if s == "tick":
            return self.acc
        if s == "ins.pw":
            return self.pw[i]
        if s == "ins.pw.lo":
            return self.pw[i] & 0xFF
        if s == "voice.freq":
            return self.c["freq"][self.v]
        return self.c["freq"][self.v] >> 8  # voice.freq.hi

    def store(self, a, val):
        s, i, v = a["cell"], str(self.c["ins"][self.v]), self.v
        if s == "tick":
            self.acc = val
        elif s == "ins.pw":
            self.pw[i] = val
        elif s == "ins.pw.lo":
            self.pw[i] = (self.pw[i] & 0xFF00) | (val & 0xFF)
        elif s == "voice.freq":
            self.c["freq"][v] = val
        else:
            self.c["freq"][v] = (self.c["freq"][v] & 0xFF) | (val & 0xFF) << 8

    def emit_part(self, prod, target, val, part):
        prod.append((target, val & 0xFF if part != "hi" else (val >> 8) & 0xFF))


def render(obj, ticks):
    """The whole horizon as a list of per-tick ``(register, value)`` write lists."""
    p = Player(obj)
    return [p.tick() for _ in range(ticks)]
