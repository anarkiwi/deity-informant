"""The universal player of prototype-trackerprog.md sections 4 and 5.

One fixed procedure over a trackerprog's data: a pitch table, instruments,
streams, bounded accumulators and a score.  It carries no tune, no family and
no table of its own, dispatching only on the form of a delta, policy or row.
"""

from __future__ import annotations

import operator

REG = {"freq_lo": 0, "freq_hi": 1, "pw_lo": 2, "pw_hi": 3, "ctrl": 4, "ad": 5, "sr": 6}
EDGE = ("ctrl", "ad", "sr")  # section 2 rule 1: every write kept, in tick order
GATE_BIT = 1  # ctrl bit 0 is the gate (anatomy:153): a chip fact, like REG
# the ctrl mask a row leaves, gating on and gating off.  The waveform byte
# carries its own gate bit and the row says only whether to keep it, so the two
# masks are the whole byte and the byte with that bit cleared -- there is no
# family's version of this and no tune states one
GATE = (0xFF, 0xFF ^ GATE_BIT)
# a fetch is a walk, so it is bounded: the two limits are the render's own
# refusal to loop, and nothing about a tune is meant to reach them
ROWS_PER_TICK, ORDER_STEPS = 256, 256

# the compiler's dispatch, spent once per node instead of once per evaluation
_CMP = {">=": operator.ge, "<": operator.lt, "!=": operator.ne, "==": operator.eq, ">": operator.gt}
_BINOP = {
    "and": operator.and_,
    "or": operator.or_,
    "xor": operator.xor,
    "add": operator.add,
    "sub": operator.sub,
    "shr": operator.rshift,
}
_RANK = operator.itemgetter(0)
# what a stored player leaves behind: the object compiled, which is read again
_DERIVED = (
    "accname heard code tests kept rowplans plans puts steps armwhen columns ranked flagdefs"
    " clockplan earlycode fetchcode endcode spends phases flushcode commits cursors priv subs"
).split()
_UNARY = {  # a node whose argument is a name, not an expression
    "cell": lambda p, a: p.cellcode(a),
    "global": lambda p, a: (lambda ov: p.gl[a] & 0xFFFF),
    "own": lambda p, a: (lambda ov: p.own[a]),
    "flag": lambda p, a: (lambda ov: p.flags.get(a, 0)),
    "payload": lambda p, a: (lambda ov: ov[a]),
    "ins": lambda p, a: (lambda ov: p.column(p.instr(), a)),
    "insrec": lambda p, a: (lambda ov: p.column(p.o["instruments"][str(p.cell(a[0]))], a[1])),
    "sid_base": lambda p, a: (lambda ov: 7 * (p.v if a == "reader" else a)),
    "notefreq": lambda p, a: (lambda ov: p.pitchof()),
    "tuned": lambda p, a: (lambda ov, x=None: p.tuned(p.code_of(a)(ov))),
    "transpose": lambda p, a: (lambda ov: p.transpose(p.code_of(a)(ov))),
}


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
        # the order program's own state: a voice the score stopped, its return
        # stack and where its one counted loop returns to (section 3.6)
        self.stopped = list(s0.get("stopped", [False] * n))
        self.callstack = [list(x) for x in s0.get("callstack", [[]] * n)]
        # the counted loops nest: a `mark` opens one and the `loop` that spends
        # it closes it, so what a voice carries is a stack and not a register
        self.loopstack = [[list(y) for y in x] for x in s0.get("loopstack", [[]] * n)]
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
        for owner in self.owners():
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
        self.prefetched = "fetch" in m["tick"]  # a tick that reads its row ahead
        # what the score's own stop stops: the whole voice, or its sequencer alone
        self.stopsafter = m.get("stop", "voice") == "voice"
        self.act = 0  # which of the tick's acts an edge write belongs to
        self.tick_no = -1
        self.stopping = 0
        self.v = 0
        self.acc = 0  # the tick-scratch accumulator an acc with scope "tick" uses
        self.w = []
        self.compile()

    def compile(self):
        """The object, compiled: nothing here is a fact, and all of it is derived.

        Every expression node, guard list, ``sets`` target, accumulator, stream
        row and stream column of a trackerprog is fixed for the render, so each
        is compiled to a closure -- here where it is the tick's own shape, and on
        first reading where the tick chooses it.  Section 4 is unchanged: this is
        the same procedure over the same object, said once instead of walked per
        tick.
        """
        o, t = self.o, self.tempo
        # an accumulator is named by the key it is declared under and nowhere else
        self.accname = {id(a): k for k, a in o["accs"].items()}
        self.heard = {}  # the subscriptions one published fact reaches
        for key, sub in self.subs:
            self.heard.setdefault((sub["event"], sub["voice"], sub.get("acc")), []).append(
                (key, sub)
            )
        # the memos a reading fills: a closure per node, a predicate per guard list,
        # a setter per target, a plan per accumulator, stream, arm and column
        self.code, self.tests, self.kept = {}, {}, []
        self.rowplans, self.plans, self.puts, self.steps, self.armwhen = {}, {}, {}, {}, {}
        self.columns = {}
        self.ranked = sorted(  # the streams a voice's machine runs, in rank order
            (
                (st["rank"], k, st, self.guardcode(st.get("when")))
                for k, st in o["streams"].items()
                if "rank" in st
            ),
            key=_RANK,
        )
        self.flagdefs = [  # the flags a voice's machine resets before its rank order
            (k, self.code_of(d["default"])) for k, d in o["globals"].get("flags", {}).items()
        ]
        self.clockplan = (  # the row clock: its cell, its step, its boundary, its resets
            self.c[t["cell"]],
            t["step"],
            self.guardcode(t["boundary"]),
            [(self.guardcode(r["when"]), self.setcode(r["sets"])) for r in t.get("reset", ())],
        )
        self.earlycode = self.guardcode(t.get("early"))
        # where the fetch stops: a row that ends it, for a fetch that is a walk.
        # Absent, every row ends it and the walk is one step, which is what a
        # family whose row is its own boundary has
        self.endcode = self.guardcode(o["meta"].get("row_ends_fetch"))
        self.fetchcode = self.guardcode(t["fetch"] if "fetch" in t else t.get("early"))
        rc = o["meta"]["row_consumes_tick"]
        self.spends = self.guardcode(None if isinstance(rc, bool) else rc)
        self.phases = [  # meta.tick, resolved: a phase is the procedure that runs it
            (None, e["stream"]) if not isinstance(e, str) else (e, getattr(self, "phase_" + e))
            for e in o["meta"]["tick"]
        ]
        self.flushcode = [(r, self.guardcode(w)) for r, w in self.flush]
        self.commits = [  # the global channel's own registers, their guards and values
            (c[0], self.code_of(c[1]), self.guardcode(c[2] if len(c) > 2 else None))
            for c in o.get("globals", {}).get("commit", ())
        ]
        g = o.get("globals", {})  # where each cursor lives: the channel's, or a voice's
        glob = set(g.get("streams", ())) | set(g.get("after", ()))
        self.cursors = {k: d for k, d in self.gcursor.items() if k not in glob}
        self.cursors.update(
            (k, d) for k, d in self.cursor.items() if k not in glob and k not in self.gcursor
        )

    def owners(self):
        """Every modulator with private state, in the one order the player keeps them.

        The order is the enumeration itself, which is what lets a stored player
        come back: ``id`` keys nothing across a pickle, so the state is carried
        by position and re-keyed against the object it is read with.
        """
        o = self.o
        seq = [a.get("beyond") for a in o["accs"].values()]
        seq += [i.get("pitch") for i in o["instruments"].values()]
        seq += [s.get("beyond") for s in o["streams"].values()]
        return [x for x in seq if x is not None]

    def __getstate__(self):
        """A player without its derived form: the compiled object, and the id keys."""
        d = {k: v for k, v in self.__dict__.items() if k not in _DERIVED}
        d["_own"] = [self.priv[id(x)] for x in self.owners()]
        return d

    def __setstate__(self, d):
        """A stored player, read back: compile the object again and re-key the state."""
        own = d.pop("_own")
        self.__dict__.update(d)
        self.priv, self.subs = {}, []
        for x, state in zip(self.owners(), own):
            self.priv[id(x)] = state
            self.subs += [(id(x), y) for y in x["on"]]
        self.compile()

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
        if name == "tied":  # whether the row the clock is running out re-targets
            return int(self.tied[v])
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

    def ev(self, e, ov=None):
        """Evaluate one section 5 expression node, through the closure it compiles to."""
        f = self.code.get(id(e))
        return (f or self.compiled(e))(ov)

    def compiled(self, e):
        """The closure a node evaluates through, compiled once and kept with it.

        The object is fixed for the player's life, so every expression in it is
        compiled the first time it is reached and never dispatched on again.  The
        memo holds the node beside its closure, which is what keeps ``id(e)`` a
        key: nothing the memo names can be collected while the memo lives.
        """
        f = self.build(e)
        self.code[id(e)] = f
        self.kept.append(e)
        return f

    def build(self, e):  # noqa: C901 - one clause per section 5 expression form
        """Compile one section 5 expression node to a closure over the payload."""
        if isinstance(e, int):
            return lambda ov: e
        if isinstance(e, str):
            return lambda ov: (ov or {})[e]
        k, a = next(iter(e.items()))
        if k in _BINOP:
            op, x, y = _BINOP[k], self.code_of(a[0]), self.code_of(a[1])
            return lambda ov: op(x(ov), y(ov))
        if k in _UNARY:
            return _UNARY[k](self, a)
        if k == "const":
            if not isinstance(a, str):
                return lambda ov: a
            return lambda ov: self.const(a, ov)
        if k == "u16":
            x, y = self.code_of(a[0]), self.code_of(a[1])
            return lambda ov: (x(ov) & 0xFF) | (y(ov) & 0xFF) << 8
        if k == "interval":
            if a is None:
                return lambda ov: self.interval(None)
            x = self.code_of(a)
            return lambda ov: self.interval(x(ov))
        if k == "field":
            x, m = self.code_of(a[0]), a[1]
            return lambda ov: x(ov) & m
        if k in ("bit", "carry_out"):
            x, n = self.code_of(a[0]), a[1]
            return lambda ov: (x(ov) >> n) & 1
        if k == "borrow_out":  # a subtraction's own: the 6502's C, 1 where it did not borrow
            x, n = self.code_of(a[0]), a[1]
            return lambda ov: 1 - ((x(ov) >> n) & 1)
        if k == "fold":  # the triangle a free counter's low bits already are
            x, m = self.code_of(a[0]), a[1]
            return lambda ov: self.folded(x(ov) & m, m)
        if k == "trap":
            return lambda ov: self.sprung(a)
        if k == "stream":
            rows, y = self.o["streams"][a[0]]["rows"], self.code_of(a[1])
            return lambda ov: rows[y(ov)]
        if k == "tabcell":  # a named column of a stream row, selected by a live cell
            name, y, col = a[0], self.code_of(a[1]), a[2]
            cols = self.column_of(name, col)
            return lambda ov: (cols[y(ov)] or self.missing(name, col, y(ov)))(ov)
        raise KeyError("expression form %r" % (k,))

    def column_of(self, name, col):
        """One named column of a stream's rows, compiled: a closure per row.

        A row the column is not in, and a row the object marks as no row at all,
        are both ``None`` here and both answered by ``missing`` at the read --
        the second by ``srow``'s own refusal, which is where it belongs.
        """
        out = self.columns.get((name, col))
        if out is None:
            rows = self.o["streams"][name]["rows"]
            # the list goes into the memo before it is filled: a column may read
            # its own stream at another row, and that is the object's, not a loop
            out = self.columns[(name, col)] = [None] * len(rows)
            for i, r in enumerate(rows):
                if isinstance(r, dict) and col in r and "trap" not in r:
                    out[i] = self.code_of(r[col])
        return out

    def missing(self, name, col, i):
        """A column a compiled row does not carry: read it out, or refuse the row."""
        return self.code_of(self.srow(name, i)[col])

    def setcode(self, sets):
        """One ``sets`` list, compiled: each target's own setter and its value."""
        return [(self.put_to(t), self.code_of(e)) for t, e in sets]

    def code_of(self, e):
        """The closure a sub-expression evaluates through, compiled now."""
        return self.code.get(id(e)) or self.compiled(e)

    def const(self, a, ov):
        """A named constant an arm or a command binds: a number, or an expression."""
        x = (ov or {})[a]
        return x if isinstance(x, int) else self.ev(x, ov)

    @staticmethod
    def folded(x, m):
        return x ^ m if x > m >> 1 else x

    @staticmethod
    def sprung(why):
        raise AssertionError(why)

    def cellcode(self, name):
        """A cell read, compiled: a plain voice cell is its own list and index."""
        if name in self.c and name not in ("freq_hi", "freq_lo"):
            d = self.c[name]
            return lambda ov: d[self.v] & 0xFFFF
        return lambda ov: self.cell(name)

    def guardcode(self, gs):
        """One guard list, compiled to a predicate: one comparison, no dict."""
        if not gs:
            return lambda ov: True
        t = [(self.code_of(x), _CMP[op], self.code_of(y)) for x, op, y in gs]
        if len(t) == 1:
            ((x, op, y),) = t
            return lambda ov: op(x(ov), y(ov))
        if len(t) == 2:
            (x, op, y), (x2, op2, y2) = t
            return lambda ov: op(x(ov), y(ov)) and op2(x2(ov), y2(ov))
        return lambda ov: all(op(x(ov), y(ov)) for x, op, y in t)

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
        for key, sub in self.heard.get((event, voice, acc), ()):
            own = self.priv[key]
            for k, e in sub.get("set", {}).items():
                own[k] = self.ev(e, payload) & 0xFF
            for k, e in sub.get("add", {}).items():  # a cursor counts for itself
                own[k] = (own[k] + self.ev(e, payload)) & 0xFF

    def instr(self, v=None):
        return self.o["instruments"][str(self.c["ins"][self.v if v is None else v])]

    def guards(self, gs, ov=None):
        """Whether a guard list holds, through the predicate it compiles to."""
        f = self.tests.get(id(gs))
        if f is None:
            f = self.tests[id(gs)] = self.guardcode(gs)
            self.kept.append(gs)
        return f(ov)

    # ---- the tick -------------------------------------------------------------
    def tick(self):
        """One tick: the flush, the global channel, then each voice in order."""
        self.tick_no += 1
        self.w = []
        if self.shadow is not None:  # this tick emits the image the last tick left
            sh = self.shadow
            self.w = [(r, sh[r] & 0xFF) for r, when in self.flushcode if when(None)]
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
        self.channel_after()
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

    def channel_after(self):
        """The global channel's streams that run after the voices, not before.

        A channel a voice feeds -- one whose note-on reloads the cell the
        channel sweeps -- steps once the voices have written it, and one that
        feeds the voices steps before them.  Which of the two a tune has is
        the list it declares (``globals.streams`` and ``globals.after``).
        """
        for name in self.o.get("globals", {}).get("after", ()):
            if self.o["streams"][name].get("all"):
                self.rows(name, [], [])
            else:
                self.stream_step(name, self.gcursor[name], [], [])

    def channel_commit(self):
        """The registers the global channel commits, once the voices have run.

        The image holds the registers the flush names; a commit to a register the
        image does not hold reaches the chip where it is made, on this tick.
        """
        for reg, f, when in self.commits:
            if not when(None):
                continue
            if self.shadow is None or reg not in self.imaged:
                self.w.append((reg, f(None) & 0xFF))
            else:
                self.shadow[reg] = f(None) & 0xFF

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
        halted = self.stopped[v]
        if halted and self.stopsafter:  # the score stopped the voice itself
            return False
        self.op, self.spent = False, False
        self.prod, self.edge = [], []
        # a score that stops the *sequencer* leaves the sound running: the clock
        # is the sequencer's, so a halted voice does not step it, and every
        # other phase is the voice's own and runs (prototype-galway-trackerprog.md
        # section 4).  Which of the two a family's stop is, is one datum
        self.boundary = False if halted else self.clock(v)
        for name, run in self.phases:
            # the tick a score ends a voice's *sequencer* on is that voice's
            # last: the source leaves the voice's routine where it clears the
            # run bit, so the phases after the row do not run on it
            if not self.stopsafter and self.stopped[v] and not halted:
                break
            if name is None:
                self.rows(run, self.prod, self.edge)
            elif self.spent:
                continue
            elif run(v):
                return True
        self.commit(self.prod, self.edge)
        return False

    def phase_fetch(self, v):
        if self.fetch_due(v):
            self.fetch(self.prod, self.edge)
        return False

    def phase_prelude(self, v):
        if self.stepped and self.early_due(v):
            p = self.instr().get("prelude")
            if p is not None:
                self.rows(p["stream"], self.prod, self.edge)
        return False

    def phase_machine(self, v):
        self.machine(self.prod, self.edge)
        return False

    def phase_commit(self, v):  # a group boundary: what the tick has written, written
        self.commit(self.prod, self.edge)
        self.prod, self.edge = [], []
        return False

    def phase_row(self, v):
        """The row boundary.  True where the order program's own end abandoned it."""
        if not self.boundary:
            return False
        self.sequencer_step(self.prod, self.edge)
        if self.stopping:
            return True
        self.spent = self.consumes()
        return False

    def consumes(self):
        """Whether the row just taken spends the voice's tick: always, never, or its guards."""
        r = self.o["meta"]["row_consumes_tick"]
        return r if isinstance(r, bool) else self.spends(self.payload)

    # ---- the row clock --------------------------------------------------------
    def clock(self, v):
        """Step the voice's row clock; True on a row boundary.

        One form, and every family's is a value of it.  A counter ``cell`` the
        tick moves by ``step``, on the ticks ``rate`` and ``phase`` name; a
        ``boundary`` guard saying which of its steps the row lands on; and
        guarded ``reset`` clauses -- what the clock does at its end, the first
        that holds and no more.  A divider is the rate with a step of -1 and no
        reset, the row's own length reloaded by the sequencer; a countdown is a
        step of -1 and a reset that reloads; a counter is a step of +1 and a
        reset that zeroes.  The step this tick is is ``phase``, which any guard
        may read (sidwizard-trackerprog.md section 4.1).
        """
        t = self.tempo
        self.stepped = self.tick_no % self.rate == self.phase
        if not self.stepped:
            return False
        k = t["cell"]
        self.tickphase = self.c[k][v]
        self.c[k][v] = (self.tickphase + t["step"]) & 0xFF
        hit = self.guards(t["boundary"])
        for r in t.get("reset", ()):
            if self.guards(r["when"]):
                for target, e in r["sets"]:
                    self.assign(target, self.ev(e), [], [])
                break
        return hit

    def early_due(self, v):
        """True where the next row is ``early`` clock steps away."""
        return self.earlycode(None)

    def fetch_due(self, v):
        """Where the clock says the fetch runs: its own guard, else the early lead."""
        return self.stepped and self.fetchcode(None)

    # ---- the accumulators and the streams, in one rank order -------------------
    def machine(self, prod, edge):
        """The voice's streams and armed accumulators, in the rank the object gives."""
        v = self.v
        for name, f in self.flagdefs:
            self.flags[name] = f(None)
        work = self.slots()
        accs, n = self.o["accs"], len(work)
        for a in self.instr().get("accs", ()):
            work.append((accs[a["acc"]]["rank"], n, None, a))
            n += 1
        for a in self.armed[v]:
            work.append((accs[a["acc"]]["rank"], n, None, a))
            n += 1
        work.sort()  # the rank, and the object's own order breaking a tie
        for _, _i, st, x in work:
            if st is None:
                if not self.op:
                    self.cur = self.o["accs"][x["acc"]]
                    self.step(self.cur, x, prod, edge)
            elif st.get("all"):
                self.rows(x, prod, edge)
            else:
                self.stream_step(x, self.slot(x), prod, edge)

    def slots(self):
        """The stream slots the voice runs: a cursor on a row, and its guards held."""
        out = []
        for rank, k, st, when in self.ranked:
            if not when(None):
                continue
            cur = self.slot(k)
            if st.get("all") or (cur is not None and cur["row"]):
                out.append((rank, len(out), st, k))
        return out

    def slot(self, name):
        """A stream's cursor for the voice being committed, per voice or the tune's."""
        d = self.cursors.get(name)
        return None if d is None else (d[self.v] if isinstance(d, list) else d)

    # ---- streams --------------------------------------------------------------
    def steprows(self, name):
        """A stream's rows, compiled: each one's hold, its sets and where it goes."""
        st = self.o["streams"][name]
        plan = self.steps[name] = [
            (
                None
                if "trap" in r
                else (
                    self.code_of(r.get("hold", 1)),
                    self.setcode(r.get("sets", ())),
                    self.code_of(r["next"]) if "next" in r else None,
                    r.get("run", ()),
                    r.get("op"),
                )
            )
            for r in st["rows"]
        ]
        return plan

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
        plan = self.steps.get(name) or self.steprows(name)
        if plan[y] is None:
            self.srow(name, y)  # the row the object marks as no row at all, refused by name
        hold, sets, nxt, run, op = plan[y]
        cur["hold"] += 1
        done = cur["hold"] >= hold(None)
        # a step's counter is read either before or after its own move (#297's
        # epochs), which is what says whether the consuming tick runs too
        if not (done and st.get("epoch") == "entry"):
            for a in run:  # an acc the step runs on every tick it holds
                self.cur = self.o["accs"][a["acc"]]
                self.step(self.cur, a, prod, edge)
        if not done:
            return
        cur["hold"] = 0
        self.act += 1
        for put, f in sets:
            put(f(None), prod, edge)
        nxt = y + 1 if nxt is None else nxt(None)
        j = st["rows"][nxt] if nxt < len(st["rows"]) else {}
        cur["row"] = self.ev(j["jump"]) if "jump" in j else nxt
        if op is not None:
            self.operate(op, prod, edge)

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
    def put_to(self, t):
        """A set's target, compiled and kept: the one place that value goes."""
        key = t if isinstance(t, int) else "s" + t
        f = self.puts.get(key)
        if f is None:
            f = self.puts[key] = self.putcode(t)
        return f

    def putcode(self, t):  # noqa: C901 - one clause per section 5 target form
        """``assign``'s dispatch, made once for a target instead of once per write."""
        if isinstance(t, int):
            return lambda val, prod, edge: self.shadow.__setitem__(t, val & 0xFF)
        if t[:1] == "@":
            k = t[1:]
            d, m = self.c[k], 0xFFFF if k in self.wide else 0xFF
            if k == "wave":  # the voice's waveform moved: the fact a modulator mirrors
                return lambda val, prod, edge: self.sounded(d, val & m)
            return lambda val, prod, edge: d.__setitem__(self.v, val & m)
        if t[:1] == "#":
            k, m = t[1:], 0xFFFF if t[1:] in self.wide else 0xFF
            return lambda val, prod, edge: self.gl.__setitem__(k, val & m)
        if t[:1] == "!":  # a flag another producer reads
            k = t[1:]
            return lambda val, prod, edge: self.flags.__setitem__(k, val)
        if t[:7] == "shadow.":  # the image, written where it is
            return lambda val, prod, edge: self.store_cell(t, val)
        if t == "pitch":  # a producer that writes the chip without moving a cell
            return lambda val, prod, edge: prod.extend(
                (("freq_lo", val & 0xFF), ("freq_hi", (val >> 8) & 0xFF))
            )
        if t == "freq":
            d = self.c["freq"]
            return lambda val, prod, edge: self.pitched(d, val, prod)
        if t[:4] == "reg.":
            # a register of the tune's one global channel, written by the voice whose
            # write-out sends it and resolved by last-writer (§3.7).  A single-family
            # data form: JCH's write-out sends the cutoff and the volume inside every
            # voice's own group, so the value the tick leaves is the last voice's, not
            # the channel's at the end of the tick (prototype-jch-trackerprog.md §4.4)
            r = int(t[4:])
            return lambda val, prod, edge: prod.append((r, val))
        if t in EDGE:  # an edge write belongs to the act of the tick that made it
            return lambda val, prod, edge: edge.append((t, val & 0xFF, self.act))
        return lambda val, prod, edge: prod.append((t, val & 0xFF))

    def pitched(self, d, val, prod):
        """The voice's frequency cell, and the pair the commit sends with it."""
        d[self.v] = val
        prod.extend((("freq_lo", val & 0xFF), ("freq_hi", (val >> 8) & 0xFF)))

    def sounded(self, d, val):
        """A voice cell whose move is a fact of its own: the waveform."""
        d[self.v] = val
        self.publish("sound", self.v, {"wave": val})

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
        # its own behaviour past the tuning, for a producer one of its rows makes
        self.beyond = self.o["streams"][name].get("beyond")
        plan = self.rowplans.get(name)
        if plan is None:
            plan = self.rowplans[name] = [
                (self.guardcode(r.get("when")), self.setcode(r["sets"]))
                for r in self.o["streams"][name]["rows"]
            ]
        for when, sets in plan:
            if not when(ov):
                continue
            self.act += 1
            for put, f in sets:
                put(f(ov), prod, edge)

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

        ``meta.stage`` is a row program (§3.6) over the row the clock ran ahead
        to, and `row_step` runs it -- the same steps in the same order under the
        same guards as ``meta.row``, over a payload that carries two more
        facts a staging reads: the instrument the row will play, and the
        transpose of the play step it belongs to.
        """
        self.payload = self.stage_facts(e)
        for step in self.o["meta"].get("stage", ()):
            if self.guards(step.get("when"), self.payload):
                self.row_step(step, self.stagedplay[self.v], e, prod, edge)

    def stage_facts(self, e):
        """The row the fetch read, as the values its own steps read.

        §3.6's facts, plus the two a staging copies rather than tests: ``ins``
        the instrument the row will play (its own, else the one the voice holds),
        and ``transpose``, the play step's own column -- which one family reads
        *untransposed* in a modulator, so the fetch stages it.  The row's pitch
        was a third until a row's own note became a fact of the row itself.
        """
        f = self.row_facts(e)
        f["ins"] = self.c["ins"][self.v] if e["ins"] is None else e["ins"]
        f["transpose"] = self.stagedplay[self.v].get("transpose", 0)
        return f

    def sequencer_step(self, prod, edge):
        """Consume the order program until a row spends the voice's tick.

        A family whose row *is* the boundary consumes exactly one row and
        leaves the loop on the first pass; one whose fetch is a walk over its
        own byte stream -- commands and notes in one program, the control flow
        between them the score's -- consumes every command it meets on the way
        to the note.  The group is flushed *between* two rows and never after
        the last, so a family that takes one row is one act (section 2 rule 1)
        and a family that takes six is six.
        """
        v = self.v
        if self.prefetched:
            # the fetch already staged the row and the play step it belongs to
            self.apply_row(self.stagedplay[v], self.staged[v], prod, edge)
            return
        for i in range(ROWS_PER_TICK):
            if i:  # the group the row before it left, sent before this one adds to it
                self.commit(self.prod, self.edge)
                prod = self.prod = []
                edge = self.edge = []
            e = self.next_row(v)
            if e is None:
                return
            self.armed[v] = []
            self.tied[v] = e["tie"]
            self.c["rowsleft"][v] = self.c["dur"][v] = e["dur"]
            self.apply_row(self.play_of(v), e, prod, edge)
            self.evrow[v] += 1
            if self.evrow[v] == len(self.pattern_of(v)["events"]):
                self.evrow[v] = 0
                self.order_step(v)
            if self.endcode(self.payload):
                return
        raise AssertionError("the order program made no row in %d steps" % ROWS_PER_TICK)

    def next_row(self, v):
        """The row the order program is on, its control steps taken to reach it.

        A step with no rows -- two control bytes in a row -- is not a row, so
        the program runs on until one has one, or until it stops.
        """
        for _ in range(ORDER_STEPS):
            if self.stopped[v] or self.stopping:
                return None
            if self.c["orderpos"][v] >= len(self.order_of(v)["play"]):
                self.order_end(v)
                continue
            pat = self.pattern_of(v)
            if self.evrow[v] < len(pat["events"]):
                return pat["events"][self.evrow[v]]
            self.evrow[v] = 0
            self.order_step(v)
        raise AssertionError("the order program reached no row in %d steps" % ORDER_STEPS)

    def order_end(self, v):
        """The end of the play list: the terminator the order declares."""
        o = self.order_of(v)
        if not (o["end"] == "jump" or isinstance(o["end"], dict)):
            self.stopping = 1
            return
        j = o["end"]
        self.c["orderpos"][v] = j["jump"] if isinstance(j, dict) else 0
        self.evrow[v] = self.c["rowsleft"][v] = 0
        self.publish("wrap", v)
        self.publish("order", v, {"pos": self.c["orderpos"][v]})

    def order_step(self, v):
        """One step of the order program: its own ``op``, else the next step.

        Section 3.6's order grammar, matched by the one family that emits it:
        ``call``/``ret`` over a per-voice return stack, ``mark``/``loop`` over a
        per-voice loop stack, ``jump``, and ``stop`` -- which stops this voice
        and not the tune, because the score stops each of them by itself.  The
        loop stack is the ninth family's: Galway pushes a counted loop's start
        and its count onto the same 8-deep stack its calls use, and six of the
        main theme's loops open while another is still live.
        """
        op = self.play_of(v).get("op")
        pos = self.c["orderpos"][v]
        if op is None:
            self.c["orderpos"][v] = pos + 1
        elif op == "stop":
            self.stopped[v] = True
        elif op == "ret":
            self.c["orderpos"][v] = self.callstack[v].pop()
        elif "jump" in op:
            self.c["orderpos"][v] = op["jump"]
        elif "call" in op:  # a call names where it goes and where it comes back
            self.callstack[v].append(op.get("ret", pos + 1))
            self.c["orderpos"][v] = op["call"]
        elif "mark" in op:  # the counted loop opens: its count, and where it returns
            self.loopstack[v].append([op["mark"] & 0xFF, op.get("next", pos + 1)])
            self.c["orderpos"][v] = op.get("next", pos + 1)
        else:  # "loop": the count spent, or the step back to the mark
            top = self.loopstack[v][-1]
            top[0] = n = (top[0] - 1) & 0xFF
            self.c["orderpos"][v] = top[1] if n else op.get("next", pos + 1)
            if not n:
                self.loopstack[v].pop()
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
            return {
                "sounds": 0,
                "keys": 0,
                "newins": 0,
                "field": 0,
                "gate_stmt": 0,
                "tie": 0,
                "dur": 0,
                "note": 0,
            }
        return {
            "dur": e["dur"],  # a row's own length, and the note it names
            "note": 0 if e["note"] is None else e["note"],
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
                self.assign(t, self.ev(val, self.payload), prod, edge)
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
        elif "hold" in step and e["arm"] is not None:  # the command the voice keeps
            self.held[self.v] = self.cmd(e["arm"])

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
        plan = self.rowplans.get(id(rows))
        if plan is None:
            plan = self.rowplans[id(rows)] = [
                (self.guardcode(r.get("when")), self.setcode(r.get("sets", ())), r.get("point", ()))
                for r in rows
            ]
            self.kept.append(rows)
        for when, sets, pts in plan:
            if not when(ov):
                continue
            for put, f in sets:
                put(f(ov), prod, edge)
            self.points(pts, ov)

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
    def plan(self, a):
        """One accumulator's own guards and expressions, compiled once.

        The record is fixed, so what its step asks of it -- whether it runs, its
        divider, whether it steps, whether it reloads and from what, and what its
        gate writes either way -- is compiled on the first arm and kept.
        """
        pol = a["policy"]
        d = isinstance(pol, dict)
        g = a.get("gate") or {}
        out = {
            "when": self.guardcode(a.get("when")),
            "rate": self.code_of(a.get("rate", 1)),
            "step_when": self.guardcode(a.get("step_when")),
            "delta_when": self.guardcode(a.get("delta_when")),
            "reload_when": self.guardcode(pol.get("when")) if d and "reload" in pol else None,
            "reload": self.code_of(pol["reload"]) if d and "reload" in pol else None,
            "gate": {arm: self.setcode(g[arm]) for arm in g if arm in ("true", "false")},
        }
        self.plans[id(a)] = out
        self.kept.append(a)
        return out

    def step(self, a, ov, prod, edge):
        v = self.v
        pair = self.armwhen.get(id(ov))
        if pair is None:
            pair = self.armwhen[id(ov)] = (
                self.plans.get(id(a)) or self.plan(a),
                self.guardcode(ov.get("when")),
            )
            self.kept.append(ov)
        p, w = pair
        if not p["when"](ov) or not w(ov):
            return
        if a["policy"] == "take":  # the degenerate clamp: already at its target
            self.take(self.c["note"][self.v], prod)
            return
        if a.get("trap"):
            raise AssertionError("the arm the certified horizon never takes")
        k = p["rate"](ov)
        if k > 1:  # section 3.3's divider, the one meaning of rate
            name = self.accname[id(a)]
            self.divider[v][name] = self.divider[v].get(name, 0) - 1
            if self.divider[v][name] >= 0:
                return
            self.divider[v][name] = k - 1
        # the decision the step makes, made once and before anything moves: a gate
        # reports what the step did, not a re-reading of a cell the step moved
        stepped = p["step_when"](ov)
        val = self.load(a)
        if p["reload"] is not None and p["reload_when"](ov):
            val = p["reload"](ov)
        out = val
        if "delta" in a and p["delta_when"](ov):
            out = self.apply(a, ov, val, prod, stepped)
            if out is None:
                return  # the policy took the value to its bound, and said so itself
        elif "unguarded" in a.get("flag", ()):  # a carry the block that makes it did not make
            self.flags[a["flag"]["name"]] = a["flag"]["unguarded"]
        emitted = val if a.get("emit") == "entry" else out
        self.store(a, out)
        for target, part in a["produce"]:
            self.emit_part(prod, target, emitted, part)
        for put, f in p["gate"].get("true" if stepped else "false", ()):
            put(f(ov) & 0xFF, prod, edge)

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
            if self.turned(a["amplitude"], out, ph, ov):
                k = a["phase"]["cell"]
                self.put(k, (self.whole(k) + (-1 if ph else 1)) & 0xFF)
                self.publish("turn", self.v, {"phase": self.whole(k)}, acc=self.accname[id(a)])
        return out

    def turned(self, am, out, ph, ov):
        """Whether the triangle turns on this step: at its bound, or on a count.

        A bound is the turn where the accumulator's value is the modulator's own;
        where two modulators sum into one cell the value is neither's, so the turn
        is a counter of the modulator's own steps against its period, which is
        what ``count`` names.  The counter is a cell of section 5's vocabulary,
        so a modulator on the global channel counts in a global cell.
        """
        if "count" not in am:
            lo, hi = (self.ev(x, ov) for x in am["interval"])
            return (out >> am["shift"]) == ((hi if not ph else lo) >> am["shift"])
        n = (self.whole(am["cell"]) + 1) & 0xFF
        turn = n == self.ev(am["count"], ov)
        self.put(am["cell"], 0 if turn else n)
        return turn

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
