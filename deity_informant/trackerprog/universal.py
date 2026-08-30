"""The universal player of prototype-trackerprog.md sections 4 and 5.

One fixed procedure over a trackerprog's data: a pitch table, instruments,
streams, bounded accumulators and a score.  It carries no tune, no family and
no table of its own, dispatching only on the form of a delta, policy or row.
"""

from __future__ import annotations

REG = {"freq_lo": 0, "freq_hi": 1, "pw_lo": 2, "pw_hi": 3, "ctrl": 4, "ad": 5, "sr": 6}
EDGE = ("ctrl", "ad", "sr")  # section 2 rule 1: every write kept, in tick order


class Player:
    """Render one trackerprog.  ``tick()`` returns the tick's SID writes."""

    def __init__(self, obj):  # noqa: C901 - one clause per declared section
        self.o = obj
        m = obj["meta"]
        self.order = m["voice_order"]
        self.tempo = m["tempo"]
        self.rate, self.phase = self.tempo.get("rate", 1), self.tempo.get("phase", 0)
        self.commit_order = m["commit_order"]
        s0 = obj["state0"]
        n = self.n = m["voices"]
        self.c = {
            "ins": list(s0.get("ins", [0] * n)),
            "wave": list(s0.get("wave", [0] * n)),
            "pwdir": list(s0.get("pwdir", [0] * n)),
            "orderpos": [0] * n,
            "rowsleft": [0] * n,
            "dur": [0] * n,
            "freq": [0] * n,
            "note": [0] * n,
            "lastnote": [0] * n,
        }
        for k, d in s0.get("cells", {}).items():
            self.c[k] = list(d)
        self.evrow = [0] * n
        self.tie = [False] * n
        self.armed = [[] for _ in range(n)]  # the accs the score armed
        self.divider = [
            dict((k, d[i]) for k, d in s0.get("dividers", {}).items()) for i in range(n)
        ]
        self.pw = {
            k: v["pw"][0] | v["pw"][1] << 8 for k, v in obj["instruments"].items() if "pw" in v
        }
        self.flags = {}
        self.priv, self.subs = {}, []
        for owner in [a.get("beyond") for a in obj["accs"].values()] + [
            i.get("pitch") for i in obj["instruments"].values()
        ]:
            if owner is None:
                continue
            self.priv[id(owner)] = dict(owner["state"])
            self.subs += [(id(owner), x) for x in owner["on"]]
        self.own = None
        self.cur = None  # the modulator stepping, for its own behaviour past the tuning
        sh = m.get("shadow")  # a register file flushed once per tick, in a stated order
        self.shadow = list(s0["shadow"]) if sh else None
        self.flush = list(range(sh["registers"] - 1, -1, -1)) if sh else []
        if sh and sh.get("order") == "ascending":
            self.flush.reverse()
        self.gl = dict(s0.get("globals", {}))  # the tune's one global channel
        self.cursor = {k: [dict(x) for x in d] for k, d in s0.get("cursors", {}).items()}
        self.gcursor = {k: dict(d) for k, d in s0.get("gcursors", {}).items()}
        self.held = [self.cmd(s0.get("held"))] * n  # the command a voice holds at the start
        self.staged = [None] * n  # the event a fetch left for the row boundary to take
        self.tied = [False] * n  # whether that event re-targets without re-triggering
        self.stagedplay = [{}] * n
        self.op = False  # a stream step produced this tick, so the armed accs stand down
        self.stepped = False  # whether the row clock advanced on the tick being rendered
        self.payload = {}
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
        if name in ("pw", "pw_lo", "pw_hi") and name not in self.c:
            p = self.pw[str(self.c["ins"][v])]
            return p if name == "pw" else (p & 0xFF if name == "pw_lo" else p >> 8)
        return self.c[name][v] & 0xFFFF

    def command_of(self, e):
        """The command a row applies: the one the voice holds, or the one it carries.

        Whether a command outlives its row is the tune's, not the clock's:
        ``meta.row_command`` says ``held`` where the voice keeps the last one the
        score gave it and re-runs it at every boundary, ``spent`` where it does not.
        """
        if self.o["meta"].get("row_command") == "held":
            return self.held[self.v]
        return None if e is None or e["arm"] is None else self.cmd(e["arm"])

    def cmd(self, c):
        """A row command: the record, or the name the score gives it."""
        return self.o["score"]["commands"][c] if isinstance(c, str) else c

    def gcell(self, name):
        """A cell of the tune's one global channel."""
        return self.gl[name] & 0xFFFF

    # ---- the tuning, and what is not a pitch ----------------------------------
    def tuned(self, n):
        """The tuning at note ``n``.  It is defined over the tuning and nowhere else."""
        p = self.o["pitch"]
        k = n - p["base"]
        if not 0 <= k < len(p["freq"]):
            raise AssertionError("note %d is outside the tuning" % n)
        return p["freq"][k]

    def private(self, owner, e):
        """Evaluate ``e`` over ``owner``'s own private state."""
        keep, self.own = self.own, self.priv[id(owner)]
        try:
            return self.ev(e) & 0xFFFF
        finally:
            self.own = keep

    def unpitched(self):
        """The instrument's own pitch modulator, where its sound is no pitch."""
        return self.instr().get("pitch")

    def pitchof(self):
        """The voice's frequency: its note in the tuning, or the instrument's own."""
        n = self.c["note"][self.v]
        if n is not None:
            return self.tuned(n)
        p = self.unpitched()
        return self.private(p, p["value"])

    def transpose(self, off):
        """This voice's pitch moved by ``off`` semitones -- the arpeggio's question.

        Past the top of the tuning there is no pitch, so the answer is the
        modulator's own, indexed by how far past it went.  Where the sound has
        no pitch at all the instrument answers instead.
        """
        n = self.c["note"][self.v]
        if n is None:
            p = self.unpitched()
            return self.private(p, p["octave" if off else "value"])
        p = self.o["pitch"]
        top = p["base"] + len(p["freq"])
        if n + off < top:
            return self.tuned(n + off)
        b, d = self.cur["beyond"], n + off - top
        if d >= len(b["words"]):
            raise AssertionError(
                "%s: %d past the tuning is beyond its own bound" % (self.cur["id"], d)
            )
        w = b["words"][d]
        if "trap" in w:
            raise AssertionError("%s, %d past the tuning: %s" % (self.cur["id"], d, w["trap"]))
        return self.private(b, w)

    def interval(self):
        """The step to the next semitone above.

        There is none above the top of the tuning, and none at all above a
        sound that is not a pitch: a vibrato over either steps by nothing.
        """
        n = self.c["note"][self.v]
        if n is None:
            return 0
        p = self.o["pitch"]
        if n + 1 >= p["base"] + len(p["freq"]):
            return 0
        return (self.tuned(n + 1) - self.tuned(n)) & 0xFFFF

    def ev(self, e, ov=None):  # noqa: C901 - one clause per section 5 expression form
        """Evaluate one section 5 expression node."""
        if isinstance(e, int):
            return e
        if isinstance(e, str):
            return (ov or {})[e]
        k, a = next(iter(e.items()))
        if k == "const":
            if not isinstance(a, str):
                return a
            x = (ov or {})[a]
            return x if isinstance(x, int) else self.ev(x, ov)
        if k == "cell":
            return self.cell(a)
        if k == "global":
            return self.gcell(a)
        if k == "own":
            return self.own[a]
        if k == "sid_base":
            return 7 * (self.v if a == "reader" else a)
        if k == "u16":
            return (self.ev(a[0], ov) & 0xFF) | (self.ev(a[1], ov) & 0xFF) << 8
        if k == "notefreq":
            return self.pitchof()
        if k == "interval":
            return self.interval()
        if k == "transpose":
            return self.transpose(self.ev(a, ov))
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
        if k == "or":
            return self.ev(a[0], ov) | self.ev(a[1], ov)
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
        if k == "trap":
            raise AssertionError(a)
        if k == "stream":
            return self.o["streams"][a[0]]["rows"][self.ev(a[1], ov)]
        if k == "tablestep":  # the bridge from a note interval into register units
            n = self.ev(a[1], ov)
            return ((self.tuned(n + 1) - self.tuned(n)) & 0xFFFF) >> self.ev(a[2], ov)
        if k == "tabcell":  # a named column of a stream row, selected by a live cell
            return self.ev(self.srow(a[0], self.ev(a[1], ov))[a[2]], ov)
        raise KeyError("expression form %r" % (k,))

    def srow(self, name, y):
        """One row of a stream, refusing a row the object marks as no row at all."""
        row = self.o["streams"][name]["rows"][y]
        if "trap" in row:
            raise AssertionError("%s row %d: %s" % (name, y, row["trap"]))
        return row

    def publish(self, event, voice, payload=None, acc=None):
        """One musical fact, offered to every modulator that subscribes to it."""
        for key, sub in self.subs:
            if sub["event"] != event or sub["voice"] != voice or sub.get("acc") != acc:
                continue
            own = self.priv[key]
            for k, e in sub.get("set", {}).items():
                own[k] = self.ev(e, payload) & 0xFF
            for k, e in sub.get("add", {}).items():  # a cursor counts for itself
                own[k] = (own[k] + self.ev(e, payload)) & 0xFF

    def instr(self, v=None):
        return self.o["instruments"][str(self.c["ins"][self.v if v is None else v])]

    def guards(self, gs, ov=None):
        for lhs, op, rhs in gs or ():
            x, y = self.ev(lhs, ov), self.ev(rhs, ov)
            if not {">=": x >= y, "<": x < y, "!=": x != y, "==": x == y, ">": x > y}[op]:
                return False
        return True

    # ---- the tick -------------------------------------------------------------
    def tick(self):
        """One tick: the flush, the global channel, then each voice in order."""
        self.tick_no += 1
        self.w = []
        if self.shadow is not None:  # this tick emits the image the last tick left
            self.w = [(r, self.shadow[r] & 0xFF) for r in self.flush]
        if self.stopping:
            if self.stopping == 1:
                self.stopping = 2
                self.w += [tuple(x) for x in self.o["globals"]["stop_writes"]]
            return self.w
        if self.tick_no == 0 and "prologue" in self.o["meta"]:
            self.prologue()
            return self.w
        self.channel()
        for v in self.order:
            self.v = v
            if self.voice(v):
                return self.w
        return self.w

    def prologue(self):
        """The tune's own init call: one command every voice runs, spending its tick."""
        for v in range(self.n):
            self.v = v
            prod, edge = [], []
            self.hold_command(self.o["meta"]["prologue"], prod, edge)
            self.commit([], prod, edge)
        self.held = [self.cmd(self.o["state0"].get("held"))] * self.n

    def channel(self):
        """The one global channel: its streams, then the registers it commits."""
        g = self.o.get("globals", {})
        for name in g.get("streams", ()):
            self.stream_step(name, self.gcursor[name], [], [])
        for reg, e in g.get("commit", ()):
            self.shadow[reg] = self.ev(e) & 0xFF

    def voice(self, v):
        """One voice's tick.  True where a terminator abandoned the whole tick."""
        pre, prod, edge = [], [], []
        self.op = False
        if self.clock(v):
            self.sequencer_step(prod, edge)
            if self.stopping:
                return True
            if self.guards(self.consumes(), self.payload):
                self.exit_rows(prod, edge)
                self.commit(pre, prod, edge)
                return False
            self.commit(pre, prod, edge)
            pre, prod, edge = [], [], []
        self.machine(prod, edge)
        if self.stepped and self.early_due(v):
            self.prelude(pre, prod, edge)
        self.exit_rows(prod, edge)
        self.commit(pre, prod, edge)
        return False

    def consumes(self):
        """Whether the row just taken spends the voice's tick, as a guard list."""
        r = self.o["meta"]["row_consumes_tick"]
        return [] if r is True else (None if r is False else r)

    # ---- the row clock --------------------------------------------------------
    def clock(self, v):
        """Step the voice's row clock; True on a row boundary.

        Two forms, one meaning: a divider whose phase names the boundary tick and
        a per-row countdown in its steps, or a countdown cell the tick decrements
        and a tempo cell it reloads from.
        """
        self.stepped = True
        if self.tempo.get("form") == "countdown":
            k = self.tempo["cell"]
            self.c[k][v] = (self.c[k][v] - 1) & 0xFF
            if self.c[k][v] == self.tempo.get("boundary", 0):
                return True
            if self.c[k][v] & 0x80:  # went past the boundary: reload the row's length
                self.c[k][v] = self.reload(v, self.tempo["reload"])
            return False
        self.stepped = self.tick_no % self.rate == self.phase
        if not self.stepped:
            return False
        self.c["rowsleft"][v] -= 1
        return self.c["rowsleft"][v] < 0

    def reload(self, v, n):
        """The row's length in clock steps, and the alternation a funk tempo makes."""
        f = self.tempo.get("alternate")
        if f and self.guards(f["when"]):
            t = self.c[n][v]
            self.c[n][v] = t ^ 1
            return (self.ev({"tabcell": [f["stream"], t, "value"]}) - 1) & 0xFF
        return self.c[n][v]

    def early_due(self, v):
        """True where the next row is ``early`` clock steps away."""
        if self.tempo.get("form") != "countdown":
            return self.c["rowsleft"][v] == 0 and not self.tie[v]
        e = self.tempo.get("early")
        return e is not None and self.c[self.tempo["cell"]][v] == self.ev(e)

    # ---- the accumulators and the streams, in one rank order -------------------
    def machine(self, prod, edge):
        """The voice's streams and armed accumulators, in the rank the object gives."""
        v = self.v
        for name, d in self.o["globals"].get("flags", {}).items():
            self.flags[name] = self.ev(d["default"])
        work = [(self.o["streams"][s]["rank"], "s", s) for s in self.slots()]
        arms = list(self.instr().get("accs", ())) + list(self.armed[v])
        work += [(self.o["accs"][a["acc"]]["rank"], "a", a) for a in arms]
        for _, kind, x in sorted(work, key=lambda t: t[0]):
            if kind == "s":
                self.stream_step(x, self.cursor[x][v], prod, edge)
            elif not self.op:
                self.cur = self.o["accs"][x["acc"]]
                self.step(self.cur, x, prod, edge)

    def slots(self):
        """The per-voice stream slots whose cursor is on a row of its own."""
        return [k for k in self.cursor if self.cursor[k][self.v]["row"]]

    # ---- streams --------------------------------------------------------------
    def stream_step(self, name, cur, prod, edge):
        """One section 3.3 step: what it runs while held, then its sets, op and next."""
        st = self.o["streams"][name]
        y = cur["row"]
        if not y:
            return
        row = self.srow(name, y)
        for a in row.get("run", ()):  # an acc the step runs on every tick it holds
            self.cur = self.o["accs"][a["acc"]]
            self.step(self.cur, a, prod, edge)
        cur["hold"] += 1
        if cur["hold"] < row.get("hold", 1):
            return
        cur["hold"] = 0
        for t, e in row.get("sets", ()):
            self.assign(t, self.ev(e) & 0xFF, prod, edge)
        nxt = row.get("next", y + 1)
        j = st["rows"][nxt] if nxt < len(st["rows"]) else {}
        cur["row"] = j["jump"] if "jump" in j else nxt
        if "op" in row:
            self.operate(row["op"], prod, edge)

    def operate(self, op, prod, edge):
        """A step's own producer: the accs the score armed stand down for the tick."""
        self.op = True
        if "pitch" in op:
            n = self.ev(op["pitch"])
            self.take(self.c["note"][self.v] + n if op.get("relative") else n, prod)
        elif "acc" in op:
            self.cur = self.o["accs"][op["acc"]]
            self.step(self.cur, op, prod, edge)
        elif "cmd" in op:  # a step may run one of the score's own commands
            self.hold_command(self.cmd(op["cmd"]), prod, edge)

    def take(self, n, prod):
        """Take a note of the tuning: the pitch, the note sounded, what it resets."""
        self.c["lastnote"][self.v] = n
        for a in self.o["meta"].get("pitch_links", ()):
            self.c[self.o["accs"][a]["cell"]][self.v] = 0
        self.assign("freq", self.tuned(n), prod, [])

    # ---- writing --------------------------------------------------------------
    def assign(self, t, val, prod, edge):
        """A set's target: a voice cell, a global cell, or a register."""
        if isinstance(t, int):
            self.shadow[t] = val & 0xFF
        elif isinstance(t, str) and t[:1] == "@":
            self.c[t[1:]][self.v] = val & 0xFF
        elif isinstance(t, str) and t[:1] == "#":
            self.gl[t[1:]] = val & 0xFF
        elif t == "freq":
            self.c["freq"][self.v] = val
            prod += [("freq_lo", val & 0xFF), ("freq_hi", (val >> 8) & 0xFF)]
        else:
            (edge if t in EDGE else prod).append((t, val & 0xFF))

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
        r = 7 * self.v + REG[target]
        if self.shadow is None:
            self.w.append((r, val & 0xFF))
        else:
            self.shadow[r] = val & 0xFF

    def rows(self, name, prod, edge, ov=None):
        """A stream's ``set`` steps, routed by target."""
        for row in self.o["streams"][name]["rows"]:
            for t, e in row["sets"]:
                self.assign(t, self.ev(e, ov) & 0xFF, prod, edge)

    def exit_rows(self, prod, edge):
        """The rows every voice path ends on, where a tune has such an exit."""
        name = self.o["meta"].get("voice_exit")
        if name:
            self.rows(name, prod, edge)

    def prelude(self, pre, prod, edge):
        """The instrument's early rows, and the row a fetch stages along with them."""
        if self.tempo.get("form") != "countdown":
            self.rows(self.instr()["prelude"]["stream"], pre, pre)
            return
        v = self.v
        if self.c["rowsleft"][v] > 0:  # an event of several rows, still spending them
            self.c["rowsleft"][v] -= 1
            self.staged[v] = None
            if self.c["rowsleft"][v] == 0:
                self.advance(v)
            return
        e = self.next_event()
        if e is None:
            return
        self.stagedplay[v] = self.play_of(v)
        if e["dur"] > 1:  # the cursor stays where it is until the count runs out
            self.c["rowsleft"][v] = e["dur"] - 1
            self.staged[v] = None
            return
        self.staged[v] = e
        self.stage(e)
        self.tied[v] = e["tie"] or bool((self.held[v] or {}).get("tie"))
        p = self.instr()["prelude"]
        if e["sounds"] and not self.tied[v] and p is not None:
            self.rows(p["stream"], prod, edge)
        self.advance(v)

    def advance(self, v):
        """The fetch's own cursor: the next event, and the next order step at a wrap."""
        self.evrow[v] += 1
        if self.evrow[v] == len(self.pattern_of(v)["events"]):
            self.evrow[v] = 0
            self.c["orderpos"][v] += 1
            self.publish("wrap", v)
            self.publish("order", v, {"pos": self.c["orderpos"][v]})

    def take_row(self, prod, edge):
        """A row boundary of a tune whose fetch runs ahead of it: take what it left."""
        v = self.v
        e = self.staged[v]
        sounds = e is not None and e["sounds"] and not self.tied[v]
        self.payload = {"sounds": int(sounds)}
        c = self.command_of(e)
        if e is None:
            if c is not None:
                self.hold_command(c, prod, edge)
            return
        self.row(self.stagedplay[v], e, prod, edge)

    # ---- the sequencer --------------------------------------------------------
    def order_of(self, v):
        return self.o["score"]["orders"][v]

    def play_of(self, v):
        """One ``play`` step; a bare pattern number is the step with no columns."""
        p = self.order_of(v)["play"][self.c["orderpos"][v]]
        return p if isinstance(p, dict) else {"pattern": p}

    def pattern_of(self, v):
        return self.o["score"]["patterns"][str(self.play_of(v)["pattern"])]

    def next_event(self):
        """The event the fetch is about to read, the order program's jump taken first."""
        v = self.v
        o = self.order_of(v)
        if self.c["orderpos"][v] >= len(o["play"]):
            if not isinstance(o["end"], dict):
                return None
            self.c["orderpos"][v] = o["end"]["jump"]
            self.evrow[v] = 0
            self.publish("order", v, {"pos": self.c["orderpos"][v]})
        return self.pattern_of(v)["events"][self.evrow[v]]

    def stage(self, e):
        """What the fetch commits ``early``, before the row it belongs to arrives."""
        v = self.v
        for f in self.o["meta"].get("prefetch", ()):
            if f == "ins" and e["ins"] is not None:
                self.c["ins"][v] = e["ins"]
                self.publish("instrument", v, {"ins": e["ins"]})
            elif f == "gate" and e["gate"] is not None:
                self.c["gate"][v] = 0xFF if e["gate"] == "on" else 0xFE
            elif f == "arm" and e["arm"] is not None:
                self.held[v] = self.cmd(e["arm"])

    def sequencer_step(self, prod, edge):
        """Consume the order program's next event and give it to the voice."""
        v = self.v
        if self.tempo.get("form") == "countdown":
            self.take_row(prod, edge)
            return
        o = self.order_of(v)
        if self.c["orderpos"][v] >= len(o["play"]):
            if not (o["end"] == "jump" or isinstance(o["end"], dict)):
                self.stopping = 1
                return
            j = o["end"]
            self.c["orderpos"][v] = j["jump"] if isinstance(j, dict) else 0
            self.evrow[v] = self.c["rowsleft"][v] = 0
            self.publish("wrap", v)
            self.publish("order", v, {"pos": self.c["orderpos"][v]})
        pat = self.pattern_of(v)
        e = pat["events"][self.evrow[v]]
        self.armed[v] = []
        self.tie[v] = e["tie"]
        self.c["rowsleft"][v] = self.c["dur"][v] = e["dur"]
        self.latch(e, prod, edge)
        self.evrow[v] += 1
        if self.evrow[v] == len(pat["events"]):
            self.evrow[v] = 0
            self.c["orderpos"][v] += 1
            self.publish("wrap", v)
            self.publish("order", v, {"pos": self.c["orderpos"][v]})

    def gate_mask(self, e):
        """The ctrl mask a row leaves: its own gate statement, else whether it sounds."""
        g = e["gate"]
        if g is None:
            return 0xFF if e["sounds"] else 0xFE
        return 0xFF if g == "on" else 0xFE

    def latch(self, e, prod, edge):
        """A row whose whole effect lands on its own tick."""
        v = self.v
        gate = self.gate_mask(e)
        if e["sounds"]:
            if e["ins"] is not None:
                self.c["ins"][v] = e["ins"]
                self.publish("instrument", v, {"ins": e["ins"]})
            c = self.command_of(e)
            if c is not None:
                self.hold_command(c, prod, edge)
            self.c["note"][v] = e["note"]
            if e["note"] is not None:
                self.publish("note", v, {"note": e["note"]})
            f = self.pitchof()
            self.c["freq"][v] = f
            prod += [("freq_hi", f >> 8), ("freq_lo", f & 0xFF)]
        self.c["wave"][v] = self.instr()["wave"]
        self.publish("sound", v, {"wave": self.c["wave"][v]})
        self.rows(self.o["meta"]["note_row"], prod, edge, {"gate": gate})
        self.payload = {
            "sounds": int(e["sounds"]),
            "field": int(e["ins"] is not None or e["arm"] is not None),
        }
        self.publish("row", v, self.payload)

    def row(self, play, e, prod, edge):
        """A row a fetch already staged: its note, its note on, and its command."""
        v = self.v
        for t, val in self.o["meta"].get("row_sets", ()):
            self.assign(t, self.ev(val), prod, edge)
        if e["sounds"]:
            if e["note"] is not None:
                self.c["note"][v] = e["note"] + play.get("transpose", 0)
                self.publish("note", v, {"note": self.c["note"][v]})
            self.note_on(self.tied[v], prod, edge)
        c = self.command_of(e)
        if c is not None:
            self.hold_command(c, prod, edge)

    def note_on(self, tied, prod, edge):
        """Arm the instrument: its cells, its streams and the rows it emits."""
        v, ins = self.v, self.instr()
        for t, val in ins.get("sets", ()):
            self.assign(t, self.ev(val), prod, edge)
        if "rest_arm" in self.o["meta"]:
            self.armed[v] = list(self.o["meta"]["rest_arm"])
        if tied:
            return
        for t, val in ins.get("note_sets", ()):
            self.assign(t, self.ev(val), prod, edge)
        for slot, r, keep in ins.get("points", ()):
            self.point(slot, r, keep)
        self.rows(self.o["meta"]["note_row"], prod, edge)
        self.publish("sound", v, {"wave": self.cell("wave")})

    def point(self, slot, r, keep=False):
        """Re-point a stream and reset the hold it was counting (section 3.6)."""
        cur = self.gcursor[slot] if slot in self.gcursor else self.cursor[slot][self.v]
        cur["row"] = r
        if not keep:
            cur["hold"] = 0

    def hold_command(self, cmd, prod, edge):
        """Apply one section 3.6 command: what it arms, sets, re-points and resets."""
        if "arms" in cmd:
            self.armed[self.v] = list(cmd["arms"])
        for a in cmd.get("links", ()):
            self.c[self.o["accs"][a]["cell"]][self.v] = 0
        for t, e in cmd.get("sets", ()):
            self.assign(t, self.ev(e, cmd), prod, edge)
        for slot, e in cmd.get("point", ()):
            self.point(slot, self.ev(e, cmd))
        for t, e in cmd.get("all", ()):  # section 3.6's global tempo: every voice
            for u in range(self.n):
                self.c[t[1:]][u] = self.ev(e, cmd) & 0xFF

    # ---- the accumulators -----------------------------------------------------
    def step(self, a, ov, prod, edge):
        v = self.v
        if not self.guards(a.get("when"), ov) or not self.guards(ov.get("when"), ov):
            return
        if a["policy"] == "take":  # the degenerate clamp: already at its target
            self.take(self.c["note"][self.v], prod)
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
            out = self.apply(a, ov, val, prod)
            if out is None:
                return  # the policy took the value to its bound, and said so itself
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
                self.assign(t, self.ev(e, ov) & 0xFF, prod, edge)

    def apply(self, a, ov, val, prod=None):  # noqa: C901 - one clause per policy
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
        step, pol = self.ev(d, ov), a["policy"]
        if isinstance(pol, dict) and "clamp" in pol:
            return self.toward(a, ov, val, step, prod)
        if pol == "reflect-complement":  # the triangle one complement folds
            hi = self.ev(a["bound"]["interval"][1], ov)
            if not val & (mask ^ mask >> 1) and val > hi:
                val ^= mask
            return (val + step) & mask
        ph = self.ev(a["phase"], ov) if "phase" in a else 0
        out = (val - step if ph else val + step) & mask
        b = a.get("bound")
        if b and pol == "reflect":
            lo, hi = b["interval"]
            turn = (out >> b["shift"]) == ((hi if not ph else lo) >> b["shift"])
            if turn:
                c = self.c[a["phase"]["cell"]]
                c[self.v] = (c[self.v] + (-1 if ph else 1)) & 0xFF
                self.publish("turn", self.v, {"phase": c[self.v]}, acc=a["id"])
        return out

    def toward(self, a, ov, val, step, prod):
        """``clamp(target)``: move by ``step``, and take the target where it is passed."""
        d = val - self.ev(a["policy"]["clamp"], ov)
        if (d + step >= 0) if d < 0 else (d - step < 0):
            self.take(self.c["note"][self.v], prod)
            return None
        return (val + step if d < 0 else val - step) & ((1 << a["width"]) - 1)

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
        if s == "voice.freq.hi":
            return self.c["freq"][self.v] >> 8
        if s[:1] == "#":
            return self.gl[s[1:]]
        if s[:1] == "@":
            return self.shadow_pair(s[1:])
        return self.c[s][self.v]

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
        elif s == "voice.freq.hi":
            self.c["freq"][v] = (self.c["freq"][v] & 0xFF) | (val & 0xFF) << 8
        elif s[:1] == "#":
            self.gl[s[1:]] = val
        elif s[:1] == "@":
            self.shadow_store(s[1:], val)
        else:
            self.c[s][v] = val & 0xFF

    def shadow_pair(self, name):
        """A register pair read back out of the shadow the tune writes through."""
        r = 7 * self.v + REG[name + "_lo"]
        return self.shadow[r] | self.shadow[r + 1] << 8

    def shadow_store(self, name, val):
        r = 7 * self.v + REG[name + "_lo"]
        self.shadow[r], self.shadow[r + 1] = val & 0xFF, (val >> 8) & 0xFF

    def emit_part(self, prod, target, val, part):
        prod.append((target, val & 0xFF if part != "hi" else (val >> 8) & 0xFF))


def render(obj, ticks):
    """The whole horizon as a list of per-tick ``(register, value)`` write lists."""
    p = Player(obj)
    return [p.tick() for _ in range(ticks)]
