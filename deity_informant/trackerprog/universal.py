"""The universal player of prototype-trackerprog.md sections 4 and 5.

One fixed procedure over a trackerprog's data: a pitch table, instruments,
streams, bounded accumulators and a score.  It carries no tune, no family and
no table of its own, dispatching only on the form of a delta, policy or row.
"""

from __future__ import annotations

REG = {"freq_lo": 0, "freq_hi": 1, "pw_lo": 2, "pw_hi": 3, "ctrl": 4, "ad": 5, "sr": 6}
EDGE = ("ctrl", "ad", "sr")  # section 2 rule 1: every write kept, in tick order
GATE_BIT = 1  # ctrl bit 0 is the gate (anatomy:153): a chip fact, like REG
# the ctrl mask a row leaves, gating on and gating off.  The waveform byte
# carries its own gate bit and the row says only whether to keep it, so the two
# masks are the whole byte and the byte with that bit cleared -- there is no
# family's version of this and no tune states one
GATE = (0xFF, 0xFF ^ GATE_BIT)


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
        self.armed = [[] for _ in range(n)]  # the accs the score armed
        self.divider = [
            dict((k, d[i]) for k, d in s0.get("dividers", {}).items()) for i in range(n)
        ]
        self.pw = {
            k: v["pw"][0] | v["pw"][1] << 8 for k, v in obj["instruments"].items() if "pw" in v
        }
        self.flags = {}
        # an accumulator is named by the key it is declared under and nowhere else
        self.accname = {id(a): k for k, a in obj["accs"].items()}
        self.priv, self.subs = {}, []
        for owner in [a.get("beyond") for a in obj["accs"].values()] + [
            i.get("pitch") for i in obj["instruments"].values()
        ]:
            if owner is None:
                continue
            self.priv[id(owner)] = dict(owner["state"])
            self.subs += [(id(owner), x) for x in owner["on"]]
        for owner in [s.get("beyond") for s in obj["streams"].values()]:
            if owner is not None:
                self.priv[id(owner)] = dict(owner["state"])
                self.subs += [(id(owner), x) for x in owner["on"]]
        self.own = None
        self.beyond = None  # the stream stepping, for its own behaviour past the tuning
        self.cur = None  # the modulator stepping, for its own behaviour past the tuning
        sh = m.get("shadow")  # a register file flushed once per tick, in a stated order
        self.shadow = list(s0["shadow"]) if sh else None
        # the flush names the registers the image carries, in the order it writes
        # them: a register the image has no byte for is not in the list at all, and
        # an entry may state the guard the image writes it under -- one build of one
        # family flushes the same 25 registers in either direction, and which one is
        # a byte of the frame being flushed (prototype-jch-trackerprog.md section 4)
        self.flush = [
            (e, []) if isinstance(e, int) else tuple(e) for e in (sh or {}).get("registers", ())
        ]
        self.imaged = {r for r, _ in self.flush}
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
        self.wide = set(m.get("wide", ()))  # the voice cells that are 16 bits
        self.prod, self.edge = [], []
        self.boundary = self.spent = False
        self.tickphase = 0
        self.act = 0  # which of the tick's acts an edge write belongs to
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
        if name == "phase":  # the clock step this tick is, for a phased tick
            return self.tickphase
        if name == "freq_hi":
            return self.c["freq"][v] >> 8
        if name == "freq_lo":
            return self.c["freq"][v] & 0xFF
        if name in ("pw", "pw_lo", "pw_hi") and name not in self.c:
            p = self.pw[str(self.c["ins"][v])]
            return p if name == "pw" else (p & 0xFF if name == "pw_lo" else p >> 8)
        if name not in self.c:  # section 5's own vocabulary, read as an expression
            s, part = self.split_cell(name)
            x = self.whole(s)
            return x if part is None else (x & 0xFF if part == "lo" else (x >> 8) & 0xFF)
        return self.c[name][v] & 0xFFFF

    def command_of(self, e):
        """The commands a row applies, in row order: the ones it holds or carries.

        Whether a command outlives its row is the tune's, not the clock's:
        ``meta.row_command`` says ``held`` where the voice keeps the last one the
        score gave it and re-runs it at every boundary, ``spent`` where it does not.
        """
        if self.o["meta"].get("row_command") == "held":
            c = self.held[self.v]
            return [c] if c is not None else []
        if e is None or e["arm"] is None:
            return []
        a = e["arm"]
        return [self.cmd(x) for x in (a if isinstance(a, list) else [a])]

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
        return self.freq_of(n + off)

    def freq_of(self, n):
        """The frequency of a note: the tuning's, or the modulator's past its top."""
        p = self.o["pitch"]
        top = p["base"] + len(p["freq"])
        return self.tuned(n) if n < top else self.past(n - top)

    def interval(self, n=None):
        """The step to the next semitone above ``n``, the voice's note by default.

        There is none above the top of the tuning, and none at all above a
        sound that is not a pitch: a vibrato over either steps by nothing.  It
        is the bridge from a note interval into a register's own units, which a
        shift then scales -- there is no second form for that.
        """
        if n is None:
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
            return self.interval(None if a is None else self.ev(a, ov))
        if k == "tuned":  # the tuning read as a table, by something that is not a note
            return self.tuned(self.ev(a, ov))
        if k == "transpose":
            return self.transpose(self.ev(a, ov))
        if k == "shr":
            return self.ev(a[0], ov) >> self.ev(a[1], ov)
        if k == "flag":
            return self.flags.get(a, 0)
        if k == "payload":
            return ov[a]
        if k == "ins":
            return self.column(self.instr(), a)
        if k == "insrec":  # a column of the instrument a cell names
            return self.column(self.o["instruments"][str(self.cell(a[0]))], a[1])
        if k == "and":
            return self.ev(a[0], ov) & self.ev(a[1], ov)
        if k == "or":
            return self.ev(a[0], ov) | self.ev(a[1], ov)
        if k == "xor":
            return self.ev(a[0], ov) ^ self.ev(a[1], ov)
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
        if k == "tabcell":  # a named column of a stream row, selected by a live cell
            return self.ev(self.srow(a[0], self.ev(a[1], ov))[a[2]], ov)
        raise KeyError("expression form %r" % (k,))

    @staticmethod
    def column(x, path):
        for part in path.split("."):
            x = x[int(part)] if part.isdigit() else x[part]
        return x

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
            self.w = [(r, self.shadow[r] & 0xFF) for r, when in self.flush if self.guards(when)]
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
        self.channel_commit()
        return self.w

    def prologue(self):
        """The tune's own init call: one command every voice runs, spending its tick."""
        for v in range(self.n):
            self.v = v
            prod, edge = [], []
            self.hold_command(self.o["meta"]["prologue"], prod, edge)
            self.commit(prod, edge)
        self.held = [self.cmd(self.o["state0"].get("held"))] * self.n

    def channel(self):
        """The one global channel's streams, stepped before the voices.

        A stream with ``all`` is its guarded rows, exactly as a voice's is.
        """
        for name in self.o.get("globals", {}).get("streams", ()):
            if self.o["streams"][name].get("all"):
                self.rows(name, [], [])
            else:
                self.stream_step(name, self.gcursor[name], [], [])

    def channel_commit(self):
        """The registers the global channel commits, once the voices have run.

        The image holds the registers the flush names; a commit to a register the
        image does not hold reaches the chip where it is made, on this tick.
        """
        for c in self.o.get("globals", {}).get("commit", ()):
            reg, e = c[0], c[1]
            if len(c) > 2 and not self.guards(c[2]):
                continue
            if self.shadow is None or reg not in self.imaged:
                self.w.append((reg, self.ev(e) & 0xFF))
            else:
                self.shadow[reg] = self.ev(e) & 0xFF

    def voice(self, v):
        """One voice's tick: the phases ``meta.tick`` names, in that order.

        The phases are the fixed four -- ``fetch`` the row the clock runs ahead
        of, ``prelude`` the instrument's early rows, ``row`` the boundary, and
        ``machine`` the streams and armed accumulators -- plus ``{"stream": s}``
        for a stream every path ends on.  Which phases a tune has and in which
        order is data: a fetch that runs ahead of the tick's modulators is the
        list saying so, not a flag.  A row that spends its tick (§3.6's
        ``row_consumes_tick``) skips the phases after it; a stream step still
        runs, being the voice's own write-out and not a modulation.
        """
        self.op, self.spent = False, False
        self.prod, self.edge = [], []
        self.boundary = self.clock(v)
        for step in self.o["meta"]["tick"]:
            if not isinstance(step, str):
                self.rows(step["stream"], self.prod, self.edge)
            elif self.spent:
                continue
            elif self.run_phase(step, v):
                return True
        self.commit(self.prod, self.edge)
        return False

    def run_phase(self, step, v):
        """One phase of the voice's tick.  True where a terminator abandoned it."""
        if step == "fetch":
            if self.fetch_due(v):
                self.fetch(self.prod, self.edge)
        elif step == "prelude":
            if self.stepped and self.early_due(v):
                p = self.instr().get("prelude")
                if p is not None:
                    self.rows(p["stream"], self.prod, self.edge)
        elif step == "machine":
            self.machine(self.prod, self.edge)
        elif step == "commit":  # a group boundary: what the tick has written, written
            self.commit(self.prod, self.edge)
            self.prod, self.edge = [], []
        elif step == "row" and self.boundary:
            self.sequencer_step(self.prod, self.edge)
            if self.stopping:
                return True
            self.spent = self.consumes()
        return False

    def fetch_due(self, v):
        """Where the clock says the fetch runs: a named phase, or the early lead."""
        f = self.tempo.get("fetch")
        if f is not None:
            return self.tickphase == self.ev(f)
        return self.stepped and self.early_due(v)

    def consumes(self):
        """Whether the row just taken spends the voice's tick: always, never, or its guards."""
        r = self.o["meta"]["row_consumes_tick"]
        return r if isinstance(r, bool) else self.guards(r, self.payload)

    # ---- the row clock --------------------------------------------------------
    def clock(self, v):
        """Step the voice's row clock; True on a row boundary.

        Two forms, one meaning: a divider whose phase names the boundary tick and
        a per-row countdown in its steps, or a countdown cell the tick decrements
        and a tempo cell it reloads from.
        """
        self.stepped = True
        if self.tempo.get("form") == "counter":
            k = self.tempo["cell"]
            for r in self.tempo.get("reset", ()):
                if self.guards(r["when"]):
                    for t, e in r["sets"]:
                        self.assign(t, self.ev(e), [], [])
                    break
            self.tickphase = self.c[k][v]
            self.c[k][v] = (self.tickphase + 1) & 0xFF
            return self.tickphase == self.ev(self.tempo.get("boundary", 0))
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
            return self.ev({"tabcell": [f["stream"], t, "value"]})
        return self.c[n][v]

    def early_due(self, v):
        """True where the next row is ``early`` clock steps away."""
        e = self.tempo.get("early")
        if self.tempo.get("form") == "counter":
            return self.guards(e)
        if self.tempo.get("form") != "countdown":
            return self.c["rowsleft"][v] == 0 and not self.tied[v]
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
                if self.o["streams"][x].get("all"):
                    self.rows(x, prod, edge)
                else:
                    self.stream_step(x, self.slot(x), prod, edge)
            elif not self.op:
                self.cur = self.o["accs"][x["acc"]]
                self.step(self.cur, x, prod, edge)

    def slots(self):
        """The stream slots the voice runs: a cursor on a row, and its guards held."""
        out = []
        for k, st in self.o["streams"].items():
            if "rank" not in st or not self.guards(st.get("when")):
                continue
            cur = self.slot(k)
            if st.get("all") or (cur is not None and cur["row"]):
                out.append(k)
        return out

    def slot(self, name):
        """A stream's cursor for the voice being committed, per voice or the tune's."""
        if name in self.o.get("globals", {}).get("streams", ()):
            return None
        if name in self.gcursor:
            return self.gcursor[name]
        return self.cursor[name][self.v] if name in self.cursor else None

    # ---- streams --------------------------------------------------------------
    def stream_step(self, name, cur, prod, edge):
        """One section 3.3 step: what it runs while held, then its sets, op and next."""
        st = self.o["streams"][name]
        y = cur["row"]
        if not y:
            return
        r = st.get("rate")  # section 3.3's divider, kept in a cell the score can set
        if r is not None:
            c = self.c[r["cell"]]
            c[self.v] = (c[self.v] - 1) & 0xFF
            if not c[self.v] & 0x80:
                return
            c[self.v] = self.ev(r["reload"]) & 0xFF
        self.beyond = st.get("beyond")
        row = self.srow(name, y)
        cur["hold"] += 1
        done = cur["hold"] >= self.ev(row.get("hold", 1))
        # a step's counter is read either before or after its own move (#297's
        # epochs), which is what says whether the consuming tick runs too
        if not (done and st.get("epoch") == "entry"):
            for a in row.get("run", ()):  # an acc the step runs on every tick it holds
                self.cur = self.o["accs"][a["acc"]]
                self.step(self.cur, a, prod, edge)
        if not done:
            return
        cur["hold"] = 0
        self.act += 1
        for t, e in row.get("sets", ()):
            self.assign(t, self.ev(e), prod, edge)
        nxt = self.ev(row.get("next", y + 1))
        j = st["rows"][nxt] if nxt < len(st["rows"]) else {}
        cur["row"] = self.ev(j["jump"]) if "jump" in j else nxt
        if "op" in row:
            self.operate(row["op"], prod, edge)

    def operate(self, op, prod, edge):
        """A step's own producer: the accs the score armed stand down for the tick."""
        self.op = True
        if "pitch" in op:
            self.cur = op
            n = self.ev(op["pitch"])
            if op.get("relative"):
                # the step's own bound: a note column of k bits comes back inside itself
                n = (self.c["note"][self.v] + n) & op.get("wrap", 0xFFFF)
            self.take(n, prod)
        elif "acc" in op:
            self.cur = self.o["accs"][op["acc"]]
            self.step(self.cur, op, prod, edge)
        elif "cmd" in op:  # a step may run one of the score's own commands
            self.hold_command(self.cmd(op["cmd"]), prod, edge)

    def take(self, n, prod):
        """Take a note of the tuning: the pitch, the note sounded, what it resets."""
        self.c["lastnote"][self.v] = n
        for a in self.o["meta"].get("pitch_links", ()):
            self.store(self.o["accs"][a], 0)
        f = self.freq_of(n)
        self.assign(self.o["meta"].get("pitch_target", "freq"), f, prod, [])

    def past(self, d):
        """A frequency the tuning has no note for: the modulator says what it is."""
        b = (self.cur or {}).get("beyond") or self.beyond
        who = b.get("id", "the modulator")
        if d >= len(b["words"]):
            raise AssertionError("%s: %d past the tuning is beyond its own bound" % (who, d))
        w = b["words"][d]
        if "trap" in w:
            raise AssertionError("%s, %d past the tuning: %s" % (who, d, w["trap"]))
        return self.private(b, w)

    # ---- writing --------------------------------------------------------------
    def assign(self, t, val, prod, edge):
        """A set's target: a voice cell, a global cell, or a register."""
        if isinstance(t, int):
            self.shadow[t] = val & 0xFF
        elif isinstance(t, str) and t[:1] == "@":
            k = t[1:]
            self.c[k][self.v] = val & (0xFFFF if k in self.wide else 0xFF)
            if k == "wave":  # the voice's waveform moved: the fact a modulator mirrors
                self.publish("sound", self.v, {"wave": self.c[k][self.v]})
        elif isinstance(t, str) and t[:1] == "#":
            self.gl[t[1:]] = val & (0xFFFF if t[1:] in self.wide else 0xFF)
        elif isinstance(t, str) and t[:1] == "!":  # a flag another producer reads
            self.flags[t[1:]] = val
        elif isinstance(t, str) and t[:7] == "shadow.":  # the image, written where it is
            self.store_cell(t, val)
        elif t == "pitch":  # a producer that writes the chip without moving a cell
            prod += [("freq_lo", val & 0xFF), ("freq_hi", (val >> 8) & 0xFF)]
        elif t == "freq":
            self.c["freq"][self.v] = val
            prod += [("freq_lo", val & 0xFF), ("freq_hi", (val >> 8) & 0xFF)]
        elif isinstance(t, str) and t[:4] == "reg.":
            # a register of the tune's one global channel, written by the voice whose
            # write-out sends it and resolved by last-writer (§3.7).  A single-family
            # data form: JCH's write-out sends the cutoff and the volume inside every
            # voice's own group, so the value the tick leaves is the last voice's, not
            # the channel's at the end of the tick (prototype-jch-trackerprog.md §4.4)
            prod.append((int(t[4:]), val))
        elif t in EDGE:  # an edge write belongs to the act of the tick that made it
            edge.append((t, val & 0xFF, self.act))
        else:
            prod.append((t, val & 0xFF))

    def commit(self, prod, edge):
        """One group of the tick's per-voice writes: its producers, then its edges."""
        for t, x in prod:  # 4 the freq/pw producers, in declared order
            self.emit(t, x)
        self.edges(edge)  # 5 every edge write kept, section 2 rule 1

    def edges(self, edge):
        """Every edge write the tick made: its acts in order, each in ``commit_order``.

        A register written twice in one tick is two events (section 2 rule 1), so
        the tick is a sequence of acts and ``commit_order`` orders one act's own.
        A family whose writes go through a shadow makes one act of the tick and
        cannot tell the difference; one that writes as it goes needs the sequence.
        """
        i = 0
        while i < len(edge):
            if len(edge[i]) < 3:  # a producer inside the list: no act to group it with
                self.emit(edge[i][0], edge[i][1])
                i += 1
                continue
            act, one = edge[i][2], {}
            while i < len(edge) and len(edge[i]) > 2 and edge[i][2] == act:
                one[edge[i][0]] = edge[i][1]
                i += 1
            for t in self.commit_order:
                if t in one:
                    self.emit(t, one[t])

    def emit(self, target, val):
        r = target if isinstance(target, int) else 7 * self.v + REG[target]
        if self.shadow is None:
            self.w.append((r, val & 0xFF))
        else:
            self.shadow[r] = val & 0xFF

    def rows(self, name, prod, edge, ov=None):
        """A stream's ``set`` steps, routed by target."""
        for row in self.o["streams"][name]["rows"]:
            if not self.guards(row.get("when"), ov):
                continue
            self.act += 1
            for t, e in row["sets"]:
                self.assign(t, self.ev(e, ov), prod, edge)

    def fetch(self, prod, edge):
        """Read the row the clock runs ahead of, and commit what it stages early."""
        v = self.v
        k = self.o["meta"].get("stage_sounds")
        if self.c["rowsleft"][v] > 0:  # an event of several rows, still spending them
            if k:
                self.c[k][v] = 0
            self.c["rowsleft"][v] -= 1
            self.staged[v] = None
            if self.c["rowsleft"][v] == 0:
                self.advance(v)
            return False
        if k:
            self.c[k][v] = 0
        e = self.next_event()
        if e is None:
            return False
        self.stagedplay[v] = self.play_of(v)
        if e["dur"] > 1:  # the cursor stays where it is until the count runs out
            self.c["rowsleft"][v] = e["dur"] - 1
            self.staged[v] = None
            return False
        self.staged[v] = e
        self.stage(e, prod, edge)
        self.tied[v] = e["tie"] or bool((self.held[v] or {}).get("tie"))
        if k:  # the one field that says a row keys a note, staged with the row
            self.c[k][v] = int(self.keys(e))
        self.advance(v)
        return True

    def advance(self, v):
        """The fetch's own cursor: the next event, and the next order step at a wrap."""
        self.evrow[v] += 1
        if self.evrow[v] == len(self.pattern_of(v)["events"]):
            self.evrow[v] = 0
            self.c["orderpos"][v] += 1
            self.publish("wrap", v)
            self.publish("order", v, {"pos": self.c["orderpos"][v]})

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

    def stage(self, e, prod, edge):
        """What the fetch commits ``early``, before the row it belongs to arrives.

        Three of the fields are **single-family data forms**, each marked with its
        reason (prototype-jch-trackerprog.md §4.2, §4.3, §4.5): ``note`` because a
        family whose commit copies a staged pitch moves the live note on a row
        that does not sound, ``transpose`` because one reads the *untransposed*
        note in a modulator, and ``cmds`` because one spends the row's commands
        where it reads them rather than where the row lands. Each is worth ticks,
        measured over that family's whole horizon; a fourth was struck at zero.
        """
        v = self.v
        for f in self.o["meta"].get("prefetch", ()):
            f, k = (f, f) if isinstance(f, str) else f
            if f == "ins" and e["ins"] is not None:
                self.c[k][v] = e["ins"]
                self.publish("instrument", v, {"ins": e["ins"]})
            elif f == "hrins":  # the instrument the row will play, the prelude's own
                self.c[k][v] = self.c["ins"][v] if e["ins"] is None else e["ins"]
            elif f == "gate" and e["gate"] is not None:
                self.c[k][v] = self.gate_mask(e)
            elif f == "note" and e["note"] is not None:  # the pitch, staged with the row
                self.c[k][v] = e["note"]
            elif f == "transpose":  # the order's own column, staged with the row it plays
                self.c[k][v] = self.stagedplay[v].get("transpose", 0)
            elif f == "arm" and e["arm"] is not None:
                self.held[v] = self.cmd(e["arm"])
            elif f == "cmds" and e["arm"] is not None:  # a row whose commands the fetch spends
                a = e["arm"]
                for c in a if isinstance(a, list) else [a]:
                    self.hold_command(self.cmd(c), prod, edge)

    def sequencer_step(self, prod, edge):
        """Consume the order program's next event and give it to the voice."""
        v = self.v
        if self.tempo.get("form") in ("countdown", "counter"):
            # the fetch already staged the row and the play step it belongs to
            self.apply_row(self.stagedplay[v], self.staged[v], prod, edge)
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
        self.tied[v] = e["tie"]
        self.c["rowsleft"][v] = self.c["dur"][v] = e["dur"]
        self.apply_row(self.play_of(v), e, prod, edge)
        self.evrow[v] += 1
        if self.evrow[v] == len(pat["events"]):
            self.evrow[v] = 0
            self.c["orderpos"][v] += 1
            self.publish("wrap", v)
            self.publish("order", v, {"pos": self.c["orderpos"][v]})

    def keys(self, e):
        """Whether a row starts a sound: the one place the object is asked."""
        return e is not None and e["sounds"] and not self.tied[self.v]

    def gate_mask(self, e):
        """The ctrl mask a row leaves: its own gate statement, else whether it sounds."""
        g = e["gate"]
        return GATE[0 if (e["sounds"] if g is None else g == "on") else 1]

    def apply_row(self, play, e, prod, edge):
        """The row's own program: section 3.6's steps, in the order the object gives.

        One procedure for every family.  A row is a short ordered list of steps
        over the event -- an instrument commit, a guarded stream, the sound
        itself, the row's commands -- and which steps a tune has, and in which
        order, is data.  ``e is None`` is a row a fetch left empty; the steps
        that need an event skip it and the rest still run.
        """
        self.payload = self.row_facts(e)
        for step in self.o["meta"]["row"]:
            if self.guards(step.get("when"), self.payload):
                self.row_step(step, play, e, prod, edge)
        self.publish("row", self.v, self.payload)

    def row_facts(self, e):
        """What the row is, as the values its own steps and streams read.

        ``sounds`` is the row's own field (section 3.6), ``keys`` that field
        against the tie: whether this row starts a sound the player must arm.
        """
        if e is None:
            return {"sounds": 0, "keys": 0, "newins": 0, "field": 0, "gate_stmt": 0, "tie": 0}
        return {
            "sounds": int(e["sounds"]),
            "keys": int(self.keys(e)),
            "newins": int(e["ins"] is not None),
            "field": int(e["ins"] is not None or e["arm"] is not None),
            "gate_stmt": int(e["gate"] is not None),
            "tie": int(self.tied[self.v]),
            "gate": self.gate_mask(e),
        }

    def row_step(self, step, play, e, prod, edge):
        """One step of the row program."""
        if "sets" in step:
            for t, val in step["sets"]:
                self.assign(t, self.ev(val), prod, edge)
        elif "stream" in step:
            self.rows(step["stream"], prod, edge, self.payload)
        elif "commands" in step:
            for c in self.command_of(e):
                self.hold_command(c, prod, edge)
        elif e is None:
            return
        elif "ins" in step:
            if e["ins"] is not None:
                self.c["ins"][self.v] = e["ins"]
                self.publish("instrument", self.v, {"ins": e["ins"]})
        elif "note" in step:
            self.sound(play, e, prod, edge)

    def sound(self, play, e, prod, edge):
        """The row keys a sound: the note it names, and the instrument it arms."""
        v = self.v
        n = e["note"]
        self.c["note"][v] = (
            None if n is None else n + play.get("transpose", 0) + self.instr().get("transpose", 0)
        )
        if n is not None:
            self.publish("note", v, {"note": self.c["note"][v]})
        self.note_on(prod, edge)

    def note_on(self, prod, edge):
        """Arm the instrument: the rows its own note-on emits, and what it rests in.

        One inline stream (section 3.3), the row's facts its guards -- a row a
        tie does not admit carries ``when tie == 0`` and says so, rather than the
        player keeping two lists and a return between them.
        """
        if "rest_arm" in self.o["meta"]:
            self.armed[self.v] = list(self.o["meta"]["rest_arm"])
        self.inline(self.instr().get("on_note", ()), prod, edge)

    def inline(self, rows, prod, edge, ov=None):
        """An inline stream: guarded rows of ``sets`` and ``point``, in order.

        One act (section 2 rule 1): an instrument's note-on and one row command
        are each one thing the tick did, however many guarded rows say it.
        """
        ov = self.payload if ov is None else ov
        self.act += 1
        for row in rows:
            if not self.guards(row.get("when"), ov):
                continue
            for t, e in row.get("sets", ()):
                self.assign(t, self.ev(e, ov), prod, edge)
            self.points(row.get("point", ()), ov)

    def points(self, pts, ov=None):
        """A step's re-points: the slot, the row, whether the hold survives."""
        for pt in pts:
            if len(pt) < 4 or self.guards(pt[3], ov):
                self.point(pt[0], self.ev(pt[1], ov), pt[2] if len(pt) > 2 else False)

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
            self.store(self.o["accs"][a], 0)
        self.inline(cmd.get("rows", ()), prod, edge, cmd)
        for name, e in cmd.get("flags", {}).items():
            self.flags[name] = self.ev(e, cmd)
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
            name = self.accname[id(a)]
            self.divider[v][name] = self.divider[v].get(name, 0) - 1
            if self.divider[v][name] >= 0:
                return
            self.divider[v][name] = k - 1
        pol = a["policy"]
        # the decision the step makes, made once and before anything moves: a gate
        # reports what the step did, not a re-reading of a cell the step moved
        stepped = self.guards(a.get("step_when"), ov)
        val = self.load(a)
        if isinstance(pol, dict) and "reload" in pol and self.guards(pol.get("when"), ov):
            val = self.ev(pol["reload"], ov)
        out = val
        if "delta" in a and self.guards(a.get("delta_when"), ov):
            out = self.apply(a, ov, val, prod, stepped)
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
            for t, e in g["true" if stepped else "false"]:
                self.assign(t, self.ev(e, ov) & 0xFF, prod, edge)

    def apply(self, a, ov, val, prod=None, stepped=True):  # noqa: C901 - one per policy
        """One step of a bounded accumulator: delta, bound, policy, phase."""
        if not stepped:
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
            hi = self.ev(a["amplitude"]["interval"][1], ov)
            if not val & (mask ^ mask >> 1) and val > hi:
                val ^= mask
            return (val + step) & mask
        ph = self.ev(a["phase"], ov) if "phase" in a else 0
        out = (val - step if ph else val + step) & mask
        if pol == "reflect":
            am = a["amplitude"]
            lo, hi = (self.ev(x, ov) for x in am["interval"])
            turn = (out >> am["shift"]) == ((hi if not ph else lo) >> am["shift"])
            if turn:
                c = self.c[a["phase"]["cell"]]
                c[self.v] = (c[self.v] + (-1 if ph else 1)) & 0xFF
                self.publish("turn", self.v, {"phase": c[self.v]}, acc=self.accname[id(a)])
        return out

    def toward(self, a, ov, val, step, prod):
        """``clamp(target)``: move by ``step``, and take the target where it is passed.

        ``edge`` is where the family puts the boundary: the step that lands exactly
        on the target either reaches it or does not, and the object says which.
        """
        b = a["policy"].get("edge", 0)
        d = val - self.ev(a["policy"]["clamp"], ov)
        if (d + step >= b) if d < 0 else (d - step < b):
            self.take(self.c["note"][self.v], prod)
            return None
        step += b
        return (val + step if d < 0 else val - step) & ((1 << a["width"]) - 1)

    @staticmethod
    def split_cell(s):
        """A cell name and the half of it a ``.hi`` or ``.lo`` picks, where it does."""
        return (s[:-3], s[-2:]) if s.endswith((".hi", ".lo")) else (s, None)

    def load(self, a):
        """An accumulator's value.  One vocabulary: the name, its space, its half."""
        s, part = self.split_cell(a["cell"])
        x = self.whole(s)
        return x if part is None else (x & 0xFF if part == "lo" else (x >> 8) & 0xFF)

    def store(self, a, val):
        """Move an accumulator's cell, held to the interval its record declares.

        Section 5's bound is the invariant and not a hint, so the renderer
        asserts it: two constants -- which is what *statically known* means --
        and a move that leaves them stops the render rather than rendering
        something the object does not claim.  The threshold a ``reflect`` turns
        at and a ``reflect-complement`` folds at is the triangle's ``amplitude``
        and lives there; it is the step's own arithmetic, not a claim about the
        cell, and in neither family are the two the same interval.
        """
        b = a.get("bound")
        if b is None or not b["interval"][0] <= val <= b["interval"][1]:
            self.escaped(a, val)
        self.store_cell(a["cell"], val)

    def escaped(self, a, val):
        """A move the object does not claim: section 5's bound, stated and broken."""
        name = self.accname[id(a)]
        b = a.get("bound")
        if b is None:
            raise AssertionError("%s stores with no bound to hold it to" % name)
        raise AssertionError(
            "%s left its %s bound [%d, %d] at %d"
            % (name, b["from"], b["interval"][0], b["interval"][1], val)
        )

    def store_cell(self, name, val):
        """Move one named cell of section 5's vocabulary, or a half of it."""
        s, part = self.split_cell(name)
        if part is not None:  # a half of the cell: the other half is the one it had
            x = self.whole(s)
            val = (x & 0xFF00) | val & 0xFF if part == "lo" else x & 0xFF | (val & 0xFF) << 8
        self.put(s, val)

    def whole(self, s):
        """The whole value of a named cell: the tick's, an instrument's, a voice's."""
        if s == "tick":
            return self.acc
        if s == "ins.pw":
            return self.pw[str(self.c["ins"][self.v])]
        if s[:1] == "#":
            return self.gl[s[1:]]
        if s[:7] == "shadow.":
            return self.shadow_pair(s[7:])
        return self.c[s][self.v]

    def put(self, s, val):
        """Move a named cell to ``val``, whole."""
        if s == "tick":
            self.acc = val
        elif s == "ins.pw":
            self.pw[str(self.c["ins"][self.v])] = val
        elif s[:1] == "#":
            self.gl[s[1:]] = val
        elif s[:7] == "shadow.":
            self.shadow_store(s[7:], val)
        else:
            self.c[s][self.v] = val

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
