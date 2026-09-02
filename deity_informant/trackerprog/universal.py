"""The universal player of prototype-trackerprog.md sections 4 and 5.

One fixed procedure over a trackerprog's data: a pitch table, instruments,
streams, bounded accumulators and a score.  It carries no tune, no family and
no table of its own, dispatching only on the form of a delta, policy or row.
"""

from __future__ import annotations

import operator

from .compiler import Plan, PlayerMixin, _always, _both

REG = {"freq_lo": 0, "freq_hi": 1, "pw_lo": 2, "pw_hi": 3, "ctrl": 4, "ad": 5, "sr": 6}
STRIDE, VOICES = 7, 3  # the chip's own shape: seven registers to a voice, three voices
# every register the object may name outright: the four the chip has one of, named
# as tuneprog/grid.py names the observable's columns, and a voice's seven named on
# the voice (`v1.pw_lo`); a bare per-voice name is the committing voice's own
CHIP = {"cutoff_lo": 21, "cutoff_hi": 22, "res_route": 23, "mode_vol": 24}
CHIP.update(("v%d.%s" % (v, k), STRIDE * v + r) for v in range(VOICES) for k, r in REG.items())
REGNAME = {r: k for k, r in CHIP.items()}  # what a number is called, for what reads numbers
EDGE = ("ctrl", "ad", "sr")  # section 2 rule 1: every write kept, in tick order
GATE_BIT = 1  # ctrl bit 0 is the gate (anatomy:153): a chip fact, like REG
# the ctrl mask a row leaves, gating on and off: the waveform byte carries its own
# gate bit and the row says only whether to keep it (§3.6), so no tune states one
GATE = (0xFF, 0xFF ^ GATE_BIT)
# a fetch is a walk, so it is bounded: the two limits are the render's own
# refusal to loop, and nothing about a tune is meant to reach them
ROWS_PER_TICK, ORDER_STEPS = 256, 256
# section 3.6's row facts, which a row the fetch did not read carries at zero
_FACTS = ("sounds", "keys", "newins", "field", "gate_stmt", "tie", "dur", "note", "wraps")

_RANK = operator.itemgetter(0)
# what a stored player leaves behind: the object compiled, which is read again --
# a closure is not state and does not pickle, so the resume rebuilds it
_DERIVED = (
    "puts columns stores spaces armplans cmdplans insplans machines rowsof stepsof"
    " rowprog stageprog pitchput ranked regs flagdefs clockplan earlycode endcode"
    " fetchcode spends phases flushcode commits cursors channels links restarm"
).split()


# a value a defect of the source makes, named as the defect and never as a musical
# one: SID Wizard 1.6 reads `freq_hi`'s note at the voice's own register base
_BUGS = {"voice_base": lambda p: STRIDE * p.v}


def _slotcheck(when, cur):
    """A stream slot's own question, asked of every slot before the order runs:
    does its guard hold, and is its cursor on a row (§3.3)?
    """
    if cur is None:
        return None if when is _always else (lambda: when(None))
    return lambda: when(None) and cur["row"] is not None


def _norow(ov):
    """Where a step that stops its stream goes: no row at all (§3.3)."""
    return None


def chipreg(name, v=None):
    """One register of the chip, by name: the only place a name becomes a number.

    A register named outright is the chip's own; a bare per-voice name is the
    register of the voice ``v`` being committed, which only a write knows.
    """
    r = CHIP.get(name)
    if r is None and v is None:
        raise AssertionError("%r names no register of the chip" % (name,))
    return STRIDE * v + REG[name] if r is None else r


class Player(PlayerMixin):
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
        # the order program's own state: a stopped voice, and its return stack (§3.6)
        self.stopped = list(s0.get("stopped", [False] * n))
        self.callstack = [list(x) for x in s0.get("callstack", [[]] * n)]
        # the counted loops nest: a `mark` opens one and the `loop` that spends
        # it closes it, so what a voice carries is a stack and not a register
        self.loopstack = [[list(y) for y in x] for x in s0.get("loopstack", [[]] * n)]
        self.armed = [()] * n  # the accs the score armed, as the object lists them
        # the record every instrument extends, and what each states of its own (§3.5)
        self.ins = {k: {**m.get("instrument", {}), **v} for k, v in obj["instruments"].items()}
        self.pw = {k: v["pw"][0] | v["pw"][1] << 8 for k, v in self.ins.items() if "pw" in v}
        self.flags = {}
        self.beyond = None  # the stream stepping, for its own behaviour past the tuning
        self.cur = None  # the modulator stepping, for its own behaviour past the tuning
        sh = m.get("shadow")  # a register file flushed once per tick, in a stated order
        self.shadow = list(s0["shadow"]) if sh else None
        # the flush names the registers the image carries, in the order it writes
        # them, and an entry may state the guard the image writes it under: one
        # build flushes the same 25 either way, by a byte of the frame (§3.1)
        self.flush = [
            (chipreg(e), []) if isinstance(e, str) else (chipreg(e[0]), e[1])
            for e in (sh or {}).get("registers", ())
        ]
        self.imaged = {r for r, _ in self.flush}
        self.gl = dict(s0.get("globals", {}))  # the tune's one global channel
        self.cursor = {k: [dict(x) for x in d] for k, d in s0.get("cursors", {}).items()}
        self.gcursor = {k: dict(d) for k, d in s0.get("gcursors", {}).items()}
        self.held = [self.cmd(s0.get("held"))] * n  # the command a voice holds at the start
        # the tune's init call: a command run on a tick of its own, before the first
        self.entry = self.cmd(s0["prologue"]) if "prologue" in s0 else None
        self.staged = [None] * n  # the event a fetch left for the row boundary to take
        self.tied = [False] * n  # whether that event re-targets without re-triggering
        self.stagedplay = [{}] * n
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
        self.v = 0
        self.acc = [0]  # the tick-scratch accumulator an acc with scope "tick" uses
        self.w = []
        self.compile()

    def compile(self):
        """The object, compiled: nothing here is a fact, and all of it is derived.

        The whole object is spent here, top down: every expression node, guard
        list, ``sets`` target, row program, stream and accumulator becomes a
        closure bound to its children's closures, so a tick calls and never
        dispatches.  Four memos hold what the tick reaches through data it
        selects -- an instrument, a stream slot, an arm, a command -- and each
        holds the record it is keyed on, so no identity it keys can be reused.
        Section 4 is unchanged: the same procedure over the same object, said
        once instead of walked per tick.
        """
        o, t = self.o, self.tempo
        self.puts, self.columns, self.stores, self.spaces = {}, {}, {}, {}
        self.armplans, self.cmdplans, self.insplans, self.machines = {}, {}, {}, {}
        self.rowsof, self.stepsof = {}, {}
        g = o.get("globals", {})  # where each cursor lives: the channel's, or a voice's
        glob = set(g.get("streams", ())) | set(g.get("after", ()))
        self.cursors = {k: d for k, d in self.gcursor.items() if k not in glob}
        self.cursors.update(
            (k, d) for k, d in self.cursor.items() if k not in glob and k not in self.gcursor
        )
        # §3.6's two row programs and the note's own target, compiled with the rest
        self.rowprog = self.rowcode(o["meta"]["row"])
        self.stageprog = self.rowcode(o["meta"].get("stage", ()))
        self.pitchput = self.put_to(o["meta"].get("pitch_target", "freq"))
        self.ranked = sorted(  # the streams a voice's machine runs, in rank order
            (
                (st["rank"], k, st, self.guardcode(st.get("when")))
                for k, st in o["streams"].items()
                if "rank" in st
            ),
            key=_RANK,
        )
        self.regs = [{k: chipreg(k, v) for k in REG} for v in range(self.n)]  # each voice's own
        self.restarm = o["meta"].get("rest_arm")  # what a note-on rests the machine in
        self.links = [self.storeof(a) for a in o["meta"].get("pitch_links", ())]
        self.flagdefs = [  # the flags a voice's machine resets before its rank order
            (k, self.code_of(d["default"])) for k, d in g.get("flags", {}).items()
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
        rc = o["meta"]["row_consumes_tick"]  # always, never, or the row's own guards
        self.spends = (lambda ov, b=rc: b) if isinstance(rc, bool) else self.guardcode(rc)
        self.phases = [  # meta.tick, resolved: a phase is the procedure that runs it
            (
                (None, self.streamrows(e["stream"]))
                if not isinstance(e, str)
                else (e, getattr(self, "phase_" + e))
            )
            for e in o["meta"]["tick"]
        ]
        self.flushcode = [(r, self.guardcode(w)) for r, w in self.flush]
        self.commits = [  # the global channel's own registers, their guards and values
            (chipreg(c[0]), self.code_of(c[1]), self.guardcode(c[2] if len(c) > 2 else None))
            for c in g.get("commit", ())
        ]
        self.channels = {  # the one global channel's own streams, before and after
            k: [
                (
                    (self.runstream, self.streamrows(name))
                    if o["streams"][name].get("all")
                    else (self.stream_step, self.streamstep(name, None))
                )
                for name in g.get(k, ())
            ]
            for k in ("streams", "after")
        }

    def __getstate__(self):
        """A player without its derived form: the cells and cursors, and no closures."""
        return {k: v for k, v in self.__dict__.items() if k not in _DERIVED}

    def __setstate__(self, d):
        """A stored player, read back: the object compiled again from the object."""
        self.__dict__.update(d)
        self.compile()

    # ---- reading the object ---------------------------------------------------
    def cell(self, name):
        """One named cell of §5's vocabulary, read on the voice being committed."""
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
            return self.part_of(name)
        return self.c[name][v] & 0xFFFF

    def command_of(self, e):
        """The commands a row applies, in row order: the ones it holds or carries.

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

    # ---- the tuning, and what is not a pitch ----------------------------------
    def tuned(self, n):
        """The tuning at note ``n``.  It is defined over the tuning and nowhere else."""
        p = self.o["pitch"]
        k = n - p["base"]
        if not 0 <= k < len(p["freq"]):
            raise AssertionError("note %d is outside the tuning" % n)
        return p["freq"][k]

    def unpitched(self):
        """The instrument's own pitch modulator, compiled, where its sound is no pitch."""
        return self.insplan(str(self.c["ins"][self.v]))

    def pitchof(self):
        """The voice's frequency: its note in the tuning, or the instrument's own."""
        n = self.c["note"][self.v]
        return self.tuned(n) if n is not None else self.unpitched().value(None)

    def transpose(self, off):
        """This voice's pitch moved by ``off`` semitones -- the arpeggio's question.

        Past the top of the tuning there is no pitch, so the answer is the
        modulator's own, indexed by how far past it went.  Where the sound has
        no pitch at all the instrument answers instead.
        """
        n = self.c["note"][self.v]
        if n is None:
            p = self.unpitched()
            return (p.octave if off else p.value)(None)
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

    # ---- the compiler ---------------------------------------------------------
    def rowcode(self, prog):
        """One row program (§3.6), compiled: each step's guard and what it runs."""
        return [(self.guardcode(s.get("when")), s, self.stepcode(s)) for s in prog]

    def stepcode(self, s):
        """What one row program step carries: its own ``sets``, or the stream it runs."""
        if "sets" in s:
            return self.setcode(s["sets"])
        return self.streamrows(s["stream"]) if "stream" in s else None

    def on_voice(self, name, u):
        """One cell of the voice ``u``, read as that voice reads it."""
        keep, self.v = self.v, u
        try:
            return self.cell(name)
        finally:
            self.v = keep

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

    def bugcode(self, name):
        """A defect of the source, compiled: one value, named as the defect it is."""
        f = _BUGS[name]
        return lambda ov: f(self)

    def instr(self, v=None):
        return self.ins[str(self.c["ins"][self.v if v is None else v])]

    def insplan(self, k):
        """One instrument's compiled half: its note-on rows, its prelude, its arms."""
        p = self.insplans.get(k)
        if p is None:
            i = self.ins[k]
            pre, un = i.get("prelude"), i.get("pitch") or {}
            p = self.insplans[k] = Plan(
                on_note=self.rowsource(i.get("on_note", ())),
                prelude=self.rowsource(pre) if pre else None,
                arms=[self.armof(x) for x in i.get("accs", ())],
                # what an instrument whose sound is no pitch answers with instead
                value=self.code_of(un["value"]) if "value" in un else None,
                octave=self.code_of(un["octave"]) if "octave" in un else None,
            )
        return p

    # ---- the tick -------------------------------------------------------------
    def tick(self):
        """One tick: the flush, the global channel, then each voice in order."""
        self.tick_no += 1
        self.w = []
        if self.shadow is not None:  # this tick emits the image the last tick left
            sh = self.shadow
            self.w = [(r, sh[r] & 0xFF) for r, when in self.flushcode if when(None)]
        if self.entry is not None:
            return self.enter()
        self.channel("streams")
        for v in self.order:
            self.v = v
            self.voice(v)
        self.channel("after")
        self.channel_commit()
        return self.w

    def enter(self):
        """A command the tune runs on a tick of its own: the init call the entry
        state names (``state0.prologue``), or the one an order's ``end`` leaves.
        Every voice runs it, and nothing else runs on that tick.
        """
        cmd, self.entry = self.entry, None
        for v in range(self.n):
            self.v = v
            prod, edge = [], []
            self.hold_command(cmd, prod, edge)
            self.commit(prod, edge)
        return self.w

    def channel(self, key):
        """The one global channel's streams, stepped where the channel declares.

        A stream with ``all`` is its guarded rows, exactly as a voice's is.  A
        channel the voices feed steps after them and one that feeds them steps
        before: the two lists are ``globals.streams`` and ``globals.after``.
        """
        for run, sr in self.channels[key]:
            run(sr, [], [])

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

        Nothing abandons a tick: a voice the score stopped runs no phase at all,
        and the tune's own end is that voice stopped like any other (§3.6).

        Which phases a tune has and in which order is data (§4.1), not a flag.  A
        row that spends its tick (§3.6's ``row_consumes_tick``) skips the phases
        after it; a stream step still runs, being the write-out and not a
        modulation.
        """
        halted = self.stopped[v]
        if halted and self.stopsafter:  # the score stopped the voice itself
            return
        self.spent = False
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
                self.runstream(run, self.prod, self.edge)
            elif not self.spent:
                run(v)
        self.commit(self.prod, self.edge)

    def phase_fetch(self, v):
        if self.stepped and self.fetchcode(None):  # the step the clock reads its row at
            self.fetch(self.prod, self.edge)

    def phase_prelude(self, v):
        if self.stepped and self.earlycode(None):  # the next row is `early` steps away
            p = self.insplan(str(self.c["ins"][v])).prelude
            if p is not None:
                self.runstream(p, self.prod, self.edge)

    def phase_machine(self, v):
        self.machine(self.prod, self.edge)

    def phase_commit(self, v):  # a group boundary: what the tick has written, written
        self.commit(self.prod, self.edge)
        self.prod, self.edge = [], []

    def phase_row(self, v):
        """The row boundary: the order program consumed, and the tick it spends."""
        if self.boundary:
            self.sequencer_step(self.prod, self.edge)
            self.spent = self.spends(self.payload)

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
        cell, step, boundary, resets = self.clockplan
        self.stepped = self.tick_no % self.rate == self.phase
        if not self.stepped:
            return False
        self.tickphase = cell[v]
        cell[v] = (self.tickphase + step) & 0xFF
        hit = boundary(None)
        for when, sets in resets:  # the first that holds, and no more
            if when(None):
                for put, f in sets:
                    put(f(None), [], [])
                break
        return hit

    # ---- the accumulators and the streams, in one rank order -------------------
    def machine(self, prod, edge):
        """The voice's streams and armed accumulators, in the rank the object gives.

        The order is the object's and fixed; what the tick decides is which
        stream's guard holds and which cursor sits on a row, and both are asked
        of every slot before any of them runs, as the rank order is one list.
        """
        for name, f in self.flagdefs:
            self.flags[name] = f(None)
        v = self.v
        order = self.machine_order(v, self.armed[v])
        for _, run, x in [e for e in order if e[0] is None or e[0]()]:
            run(x, prod, edge)

    def machine_order(self, v, arms):
        """A voice's machine in rank order, merged and sorted once for the pair its
        instrument and its armed set make -- both fixed, and the ranks with them.

        The memo holds the arm list it is keyed on, so that identity cannot be reused.
        """
        key = (v, self.c["ins"][v], id(arms))
        got = self.machines.get(key)
        if got is not None:
            return got[1]
        work = []
        for i, (rank, name, st, when) in enumerate(self.ranked):
            if st.get("all"):
                rows = self.streamrows(name)
                work.append(((rank, 0, i), _slotcheck(when, None), self.runstream, rows))
                continue
            sr = self.streamstep(name, v)
            if sr.cur is not None:  # a stream the voice has no cursor for never runs
                work.append(((rank, 0, i), _slotcheck(when, sr.cur), self.stream_step, sr))
        accs = self.o["accs"]
        pool = self.insplan(str(key[1])).arms + [self.armof(x) for x in arms]
        for i, p in enumerate(pool):
            work.append(((accs[p.name]["rank"], 1, i), None, self.step, p))
        work.sort(key=_RANK)
        out = [(chk, run, x) for _, chk, run, x in work]
        self.machines[key] = (arms, out)
        return out

    def slot(self, name, v):
        """A stream's cursor for one voice: its own, or the tune's."""
        d = self.cursors.get(name)
        return None if d is None else (d[v] if isinstance(d, list) else d)

    # ---- streams --------------------------------------------------------------
    def streamrows(self, name):
        """A stream run as its guarded rows: the rows, and its words past the tuning."""
        sr = self.rowsof.get(name)
        if sr is None:
            st = self.o["streams"][name]
            sr = self.rowsof[name] = Plan(
                name=name, plan=self.rowplan(st["rows"]), beyond=st.get("beyond")
            )
        return sr

    def rowsource(self, x, pay=None):
        """A §3.3 stream where the grammar puts one: a declared stream's name, or
        the rows -- one shape for a prelude, a note-on and a command's rows."""
        if isinstance(x, str):
            return self.streamrows(x)
        return Plan(name=None, plan=self.rowplan(x or (), pay), beyond=None)

    def streamstep(self, name, v):
        """A stream a cursor steps: its cursor, its divider, its rows and their landings."""
        sr = self.stepsof.get((name, v))
        if sr is None:
            st = self.o["streams"][name]
            sr = self.stepsof[(name, v)] = Plan(
                name=name,
                cur=self.gcursor.get(name) if v is None else self.slot(name, v),
                due=self.dividercode(st.get("rate")),
                beyond=st.get("beyond"),
                land=self.landing(st),
                rows=self.steprows(st),
            )
        return sr

    def steprows(self, st):
        """A stream's rows, compiled: each one's hold, its sets, its arms and its next."""
        return [
            (
                None
                if "trap" in r
                else (
                    self.code_of(r.get("hold", 1)),
                    self.setcode(r.get("sets", ())),
                    None if "next" not in r else self.landrow(r["next"]),
                    [self.armof(x) for x in r.get("run", ())],
                    self.opcode(r["op"]) if "op" in r else None,
                )
            )
            for r in st["rows"]
        ]

    def landrow(self, y):
        """Where a row sends a cursor; a ``next`` or a ``jump`` of null is no row (§3.3)."""
        return _norow if y is None else self.code_of(y)

    def landing(self, st):
        """Where a step that arrives at each row leaves its cursor: the row itself,
        or the jump that row carries, taken here because a jump occupies no tick.
        """
        return [None if "jump" not in r else self.landrow(r["jump"]) for r in st["rows"]]

    def stream_step(self, sr, prod, edge):
        """One section 3.3 step: what it runs while held, then its sets, op and next."""
        cur = sr.cur
        y = cur["row"]
        if y is None:  # a cursor that is on no row: the stream does not run (§3.3)
            return
        if sr.due is not None and not sr.due(None):
            return
        self.beyond = sr.beyond
        row = sr.rows[y]
        if row is None:
            self.srow(sr.name, y)  # the row the object marks as no row at all, refused by name
        hold, sets, nxt, run, op = row
        cur["hold"] += 1
        done = cur["hold"] >= hold(None)
        for arm in run:  # an acc the step runs on every tick it holds
            self.step(arm, prod, edge)
        if not done:
            return
        cur["hold"] = 0
        self.act += 1
        for put, f in sets:
            put(f(None), prod, edge)
        cur["row"] = self.lands(sr.land, y + 1 if nxt is None else nxt(None))
        if op is not None:
            self.operate(op, prod, edge)

    @staticmethod
    def lands(land, nxt):
        """The landing a row carries, where the step arrives at one; null is no row."""
        if nxt is None or nxt >= len(land):
            return nxt
        f = land[nxt]
        return nxt if f is None else f(None)

    def opcode(self, op):
        """A step's own producer, compiled: a pitch, an accumulator, or a command."""
        return Plan(
            pitch=self.code_of(op["pitch"]) if "pitch" in op else None,
            relative=op.get("relative"),
            wrap=op.get("wrap", 0xFFFF),
            beyond=op.get("beyond"),
            arm=self.armof(op) if "acc" in op else None,
            cmd=op.get("cmd"),
        )

    def operate(self, op, prod, edge):
        """A step's own producer: a pitch of the tuning, an accumulator, or a command.

        A family whose armed accumulators stand down for a tick one of its steps
        produced on says so in the object: the row leaves a flag and the arm's
        ``when`` reads it (§5's producer flags), which is one family's precedence
        stated where the family is.
        """
        if op.pitch is not None:
            self.cur = op.beyond
            n = op.pitch(None)
            if op.relative:
                # the step's own bound: a note column of k bits comes back inside itself
                n = (self.c["note"][self.v] + n) & op.wrap
            self.take(n, prod)
        elif op.arm is not None:
            self.step(op.arm, prod, edge)
        else:  # a step may run one of the score's own commands
            self.hold_command(self.cmd(op.cmd), prod, edge)

    def take(self, n, prod):
        """Take a note of the tuning: the pitch, the note sounded, what it resets."""
        self.c["lastnote"][self.v] = n
        for put in self.links:
            put(0)
        self.pitchput(self.freq_of(n), prod, [])

    def past(self, d):
        """A frequency the tuning has no note for: the modulator says what it is."""
        b = self.cur or self.beyond
        who = b.get("id", "the modulator")
        if d >= len(b["words"]):
            raise AssertionError("%s: %d past the tuning is beyond its own bound" % (who, d))
        w = b["words"][d]
        if "trap" in w:
            raise AssertionError("%s, %d past the tuning: %s" % (who, d, w["trap"]))
        return self.ev(w)

    # ---- writing --------------------------------------------------------------
    def put_to(self, t):
        """A set's target, compiled and kept: the one place that value goes."""
        f = self.puts.get(t)
        if f is None:
            f = self.puts[t] = self.putcode(t)
        return f

    def putcode(self, t):  # noqa: C901 - one clause per section 5 target form
        """A ``sets`` target's dispatch, made once for the target and not per write."""
        if t in CHIP:
            # a register the write names outright, sent by the voice whose write-out
            # sends it and resolved by last-writer, not by the channel (§3.7)
            r = chipreg(t)
            return lambda val, prod, edge: prod.append((r, val))
        if t[:1] == "@":
            k = t[1:]
            d, m = self.c[k], 0xFFFF if k in self.wide else 0xFF
            return lambda val, prod, edge: d.__setitem__(self.v, val & m)
        if t[:1] == "#":
            k, m = t[1:], 0xFFFF if t[1:] in self.wide else 0xFF
            d = self.gl
            return lambda val, prod, edge: d.__setitem__(k, val & m)
        if t[:1] == "!":  # a flag another producer reads
            k, d = t[1:], self.flags
            return lambda val, prod, edge: d.__setitem__(k, val)
        if t[:7] == "shadow.":  # the image, written where it is
            put = self.cellput(t)
            return lambda val, prod, edge: put(val)
        if t in ("pitch", "freq"):  # the frequency: `pitch` writes the chip alone
            d = self.c["freq"] if t == "freq" else None
            return lambda val, prod, edge: self.pitched(d, val, prod)
        if t in EDGE:  # an edge write belongs to the act of the tick that made it
            return lambda val, prod, edge: edge.append((t, val & 0xFF, self.act))
        return lambda val, prod, edge: prod.append((t, val & 0xFF))

    def pitched(self, d, val, prod):
        """The pair the commit sends for a frequency, and the cell where it has one."""
        if d is not None:
            d[self.v] = val
        prod.extend((("freq_lo", val & 0xFF), ("freq_hi", (val >> 8) & 0xFF)))

    def commit(self, prod, edge):
        """One group of the tick's per-voice writes: its producers, then its edges."""
        for t, x in prod:  # 4 the freq/pw producers, in declared order
            self.emit(t, x)
        self.edges(edge)  # 5 every edge write kept, section 2 rule 1

    def edges(self, edge):
        """Every edge write the tick made: its acts in order, each in ``commit_order``.

        A register written twice in one tick is two events (section 2 rule 1), so
        the tick is a sequence of acts and ``commit_order`` orders one act's own.
        Inside one act a register keeps its **last** value: an act is one thing
        the tick did and ``commit_order`` is a permutation, so it has one slot per
        register and no exemplar writes the same one twice in a row (§3.1).
        """
        i = 0
        while i < len(edge):
            act, one = edge[i][2], {}
            while i < len(edge) and edge[i][2] == act:
                one[edge[i][0]] = edge[i][1]
                i += 1
            for t in self.commit_order:
                if t in one:
                    self.emit(t, one[t])

    def emit(self, target, val):
        r = target if isinstance(target, int) else self.regs[self.v][target]
        if self.shadow is None:
            self.w.append((r, val & 0xFF))
        else:
            self.shadow[r] = val & 0xFF

    def rowplan(self, rows, pay=None):
        """One guarded row list, compiled: each row's guard, its sets and its re-points."""
        return [
            (
                self.guardcode(r.get("when"), pay),
                self.setcode(r.get("sets", ()), pay),
                self.pointcode(r.get("point", ()), pay),
            )
            for r in rows
        ]

    def runstream(self, sr, prod, edge, ov=None):
        """A §3.3 stream's guarded rows of ``sets`` and ``point``, in order.

        The one procedure for the four places the grammar puts a stream -- a
        declared stream, a note-on, a prelude, a command's rows -- named or inline.
        **One act per matching row** (section 2 rule 1): the act is the row and not
        the call site, which is the measurement -- 2,943 ticks of seven builds.
        """
        self.beyond = sr.beyond
        for when, sets, pts in sr.plan:
            if not when(ov):
                continue
            self.act += 1
            for put, f in sets:
                put(f(ov), prod, edge)
            if pts:
                self.points(pts, ov)

    def pointcode(self, pts, pay=None):
        """A step's re-points, compiled: the slot, the row, the hold and the guard."""
        return [
            (
                pt[0],
                None if pt[1] is None else self.code_of(pt[1], pay),
                pt[2] if len(pt) > 2 else False,
                self.guardcode(pt[3], pay) if len(pt) > 3 else None,
            )
            for pt in pts
        ]

    def points(self, pts, ov):
        """A step's re-points: the slot, the row, whether the hold survives."""
        for slot, r, keep, when in pts:
            if when is None or when(ov):
                self.point(slot, None if r is None else r(ov), keep)

    def point(self, slot, r, keep=False):
        """Re-point a stream and reset the hold it was counting (section 3.6).

        ``None`` is a cursor on no row -- what a command that stops a stream
        points it to.  A cursor is off by saying so and never by its index: row 0
        is a row like any other.
        """
        cur = self.gcursor[slot] if slot in self.gcursor else self.cursor[slot][self.v]
        cur["row"] = r
        if not keep:
            cur["hold"] = 0

    # ---- the fetch and the sequencer -------------------------------------------
    def fetch(self, prod, edge):
        """Read the row the clock runs ahead of, and run ``meta.stage`` over it.

        A fetch tick that stages no row -- an event of several rows still
        spending them, a row the count has not reached, a play list that ended --
        runs the same program over the empty facts (``row_facts(None)``), so what
        the fetch leaves for the row is one program under one guard list.
        """
        v = self.v
        e = self.staging(v)
        self.row_program(self.stageprog, self.stage_facts(e), self.stagedplay[v], e, prod, edge)
        if e is not None:
            self.advance(v)

    def staging(self, v):
        """The row the fetch stages, and the cursor it leaves; ``None`` where it stages none."""
        if self.c["rowsleft"][v] > 0:  # an event of several rows, still spending them
            self.c["rowsleft"][v] -= 1
            self.staged[v] = None
            if self.c["rowsleft"][v] == 0:
                self.advance(v)
            return None
        e = self.next_event()
        if e is None:
            return None
        self.stagedplay[v] = self.play_of(v)
        if e["dur"] > 1:  # the cursor stays where it is until the count runs out
            self.c["rowsleft"][v] = e["dur"] - 1
            self.staged[v] = None
            return None
        self.staged[v] = e
        # the row's own tie, settled before its program runs over it -- a command
        # the row hands the voice adds its own at the step that takes it, so
        # `keys` is one fact at the staging and at the boundary
        self.tied[v] = bool(e["tie"])
        return e

    def advance(self, v):
        """The fetch's own cursor: the next event, and the next order step at a wrap.

        The step is ``order_step``'s, not an increment of its own: *when* the row
        is read and *what shape the sequencer is* are two properties, and a
        prefetching family with a called or counted score walks past ``call``,
        ``mark`` and ``loop`` as though each were ``play`` if the wrap here does
        not run the order program.
        """
        self.evrow[v] += 1
        if self.evrow[v] == len(self.pattern_of(v)["events"]):
            self.evrow[v] = 0
            self.order_step(v)

    def order_of(self, v):
        return self.o["score"]["orders"][v]

    def play_of(self, v):
        """One ``play`` step; a bare pattern number is the step with no columns."""
        p = self.order_of(v)["play"][self.c["orderpos"][v]]
        return p if isinstance(p, dict) else {"pattern": p}

    def pattern_of(self, v):
        return self.o["score"]["patterns"][str(self.play_of(v)["pattern"])]

    def next_event(self):
        """The event the fetch is about to read, the play list's own end taken first."""
        v = self.v
        if self.c["orderpos"][v] >= len(self.order_of(v)["play"]) and not self.order_end(v):
            return None
        ev = self.pattern_of(v)["events"]
        return ev[self.evrow[v]] if self.evrow[v] < len(ev) else None

    def stage_facts(self, e):
        """§3.6's facts of the row the fetch read, plus the two a staging copies
        rather than tests: ``ins``, the instrument the row will play (its own, else
        the one the voice holds), and ``transpose``, the play step's own column,
        which one family reads *untransposed* in a modulator.
        """
        f = self.row_facts(e)
        f["ins"] = self.c["ins"][self.v] if e is None or e["ins"] is None else e["ins"]
        f["transpose"] = self.stagedplay[self.v].get("transpose", 0)
        return f

    def sequencer_step(self, prod, edge):
        """Consume the order program until a row spends the voice's tick.

        A family whose row *is* the boundary consumes exactly one row; one whose
        fetch is a walk over its own byte stream consumes every command it meets
        on the way to the note.  The group is flushed *between* two rows and never
        after the last, so a family that takes six rows is six acts (§2 rule 1).
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
            self.armed[v] = ()
            self.tied[v] = e["tie"]
            self.c["rowsleft"][v] = self.c["dur"][v] = e["dur"]
            self.apply_row(self.play_of(v), e, prod, edge)
            self.advance(v)
            if self.endcode(self.payload):
                return
        raise AssertionError("the order program made no row in %d steps" % ROWS_PER_TICK)

    def next_row(self, v):
        """The row the order program is on, its control steps taken to reach it.

        A step with no rows -- two control bytes in a row -- is not a row, so
        the program runs on until one has one, or until it stops.
        """
        for _ in range(ORDER_STEPS):
            if self.stopped[v]:
                return None
            if self.c["orderpos"][v] >= len(self.order_of(v)["play"]):
                if not self.order_end(v):
                    return None
                continue
            pat = self.pattern_of(v)
            if self.evrow[v] < len(pat["events"]):
                return pat["events"][self.evrow[v]]
            self.evrow[v] = 0
            self.order_step(v)
        raise AssertionError("the order program reached no row in %d steps" % ORDER_STEPS)

    def order_end(self, v):
        """The end of the play list: the terminator the order declares; False where it stops.

        One answer for both positions that ask it -- the fetch reading ahead and
        the walk stepping -- because what the play list does when it runs out is
        the order's own datum and not the caller's.  A list that jumps goes on; any
        other end stops that voice, exactly as the ``stop`` step of the order
        program does, and may name the command the tune runs after its last row
        (``end: {"stop": name}``), which ``enter`` spends a tick on.
        """
        j = self.order_of(v)["end"]
        if not (isinstance(j, dict) and "jump" in j):
            self.stopped[v] = True
            if isinstance(j, dict):
                self.entry = self.cmd(j["stop"])
            return False
        self.c["orderpos"][v] = j["jump"]
        self.evrow[v] = self.c["rowsleft"][v] = 0
        return True

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

    def keys(self, e):
        """Whether a row starts a sound: the one place the object is asked."""
        return e is not None and e["sounds"] and not self.tied[self.v]

    def wrapping(self):
        """Whether the cursor leaves its pattern after this row -- a byte cursor's end."""
        v = self.v
        play = self.order_of(v)["play"]
        n = len(self.pattern_of(v)["events"]) if self.c["orderpos"][v] < len(play) else 0
        return int(self.evrow[v] + 1 >= n)

    def gate_mask(self, e):
        """The ctrl mask a row leaves: its own gate statement, else whether it sounds."""
        g = e["gate"]
        return GATE[0 if (e["sounds"] if g is None else g == "on") else 1]

    def apply_row(self, play, e, prod, edge):
        """The boundary's own program, over the row the score gave the voice."""
        self.row_program(self.rowprog, self.row_facts(e), play, e, prod, edge)

    def row_program(self, prog, facts, play, e, prod, edge):
        """§3.6's row program: its steps over the row's facts, in the object's order.

        One procedure for every family and for both positions the program runs at
        -- ``meta.row`` at the boundary and ``meta.stage`` at the fetch, whose
        facts carry two values it copies rather than tests.  A row is a short
        ordered list of steps over the event: an instrument commit, a guarded
        stream, the sound itself, the row's commands.  ``e is None`` is a row a
        fetch left empty; the steps that need an event skip it and the rest run.
        """
        self.payload = facts
        for when, step, x in prog:
            if when(facts):
                self.row_step(step, x, play, e, prod, edge)

    def row_facts(self, e):
        """What the row is, as the values its own steps and streams read.

        ``sounds`` is the row's own field (section 3.6), ``keys`` that field
        against the tie: whether this row starts a sound the player must arm.
        """
        if e is None:
            return dict.fromkeys(_FACTS, 0)
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
            # where a byte cursor the score no longer packs ends: the row program
            # keeps such a cursor itself, and this is where it starts over (§3.6)
            "wraps": self.wrapping(),
        }

    def row_step(self, step, x, play, e, prod, edge):
        """One step of the row program, over what it compiled to."""
        if "sets" in step:
            ov = self.payload
            for put, f in x:
                put(f(ov), prod, edge)
        elif "stream" in step:
            self.runstream(x, prod, edge, self.payload)
        elif "commands" in step:
            for c in self.command_of(e):
                self.hold_command(c, prod, edge)
        elif e is None:
            return
        elif "ins" in step:
            if e["ins"] is not None:
                self.c["ins"][self.v] = e["ins"]
        elif "note" in step:
            self.sound(play, e, prod, edge)
        elif "hold" in step:  # the command the voice keeps, and the tie it carries
            if e["arm"] is not None:
                self.held[self.v] = self.cmd(e["arm"])
            self.ties(e)

    def ties(self, e):
        """Settle the row's tie: its own field, and the command it hands the voice.

        A command may re-target without re-triggering (§3.6's ``Cmd.tie``), so the
        step that takes it is where the tie is known; the facts derived from it
        move with it, and the steps after this one read them.
        """
        self.tied[self.v] = bool(e["tie"]) or bool((self.held[self.v] or {}).get("tie"))
        self.payload["tie"] = int(self.tied[self.v])
        self.payload["keys"] = int(self.keys(e))

    def sound(self, play, e, prod, edge):
        """The row keys a sound: the note it names, and the instrument it arms."""
        v = self.v
        n = e["note"]
        self.c["note"][v] = (
            None if n is None else n + play.get("transpose", 0) + self.instr().get("transpose", 0)
        )
        self.note_on(prod, edge)

    def note_on(self, prod, edge):
        """Arm the instrument: the rows its own note-on emits, and what it rests in.

        One inline stream (section 3.3), the row's facts its guards -- a row a
        tie does not admit carries ``when tie == 0`` and says so, rather than the
        player keeping two lists and a return between them.
        """
        if self.restarm is not None:
            self.armed[self.v] = self.restarm
        p = self.insplan(str(self.c["ins"][self.v]))
        self.runstream(p.on_note, prod, edge, self.payload)

    # ---- the commands ---------------------------------------------------------
    def cmdplan(self, cmd):
        """One §3.6 command, compiled; the memo holds the record it is keyed on."""
        p = self.cmdplans.get(id(cmd))
        if p is None:
            p = self.cmdplans[id(cmd)] = Plan(
                arms=cmd.get("arms"),
                rows=self.rowsource(cmd.get("rows", ()), cmd),
                flags=[(k, self.code_of(e, cmd)) for k, e in cmd.get("flags", {}).items()],
                every=self.setcode(cmd["all"], cmd) if "all" in cmd else None,
            )
        return p

    def hold_command(self, cmd, prod, edge):
        """Apply one section 3.6 command: what it arms, what it sets and re-points."""
        p = self.cmdplan(cmd)
        if p.arms is not None:
            self.armed[self.v] = p.arms
        self.runstream(p.rows, prod, edge, cmd)
        for name, f in p.flags:
            self.flags[name] = f(cmd)
        if p.every is not None:  # section 3.6's global tempo: one set, every voice
            keep = self.v
            for put, f in p.every:
                val = f(cmd)
                for u in range(self.n):
                    self.v = u
                    put(val, prod, edge)
            self.v = keep

    # ---- the accumulators -----------------------------------------------------
    def armof(self, ov):
        """One armed accumulator, compiled under the arm's own numbers (§5).

        The memo holds the arm it is keyed on, so that identity cannot be reused.
        """
        p = self.armplans.get(id(ov))
        if p is None:
            p = self.armplans[id(ov)] = self.accplan(ov["acc"], ov)
        return p

    def accplan(self, name, ov):
        """§5's record, spent once: what its step asks of it -- whether it runs, its
        divider, whether it steps, whether it reloads and from what, how far it
        moves, where it stops and what its gate writes either way -- compiled
        against the arm, whose own numbers the object states beside it.
        """
        a = self.o["accs"][name]
        pol = a["policy"]
        d = isinstance(pol, dict)
        g, fl, am = a.get("gate") or {}, a.get("flag") or {}, a.get("amplitude") or {}
        delta, rep = a.get("delta"), None
        if isinstance(delta, dict) and "repeat" in delta:
            rep = (self.code_of(delta["repeat"][0], ov), self.code_of(delta["repeat"][1], ov))
            delta = None
        ph = a.get("phase")
        return Plan(
            name=name,
            ov=ov,
            beyond=a.get("beyond"),
            when=_both(self.guardcode(a.get("when"), ov), self.guardcode(ov.get("when"), ov)),
            trap=bool(a.get("trap")),
            divider=self.dividercode(a.get("rate"), ov),
            step_when=self.guardcode(a.get("step_when"), ov),
            delta_when=self.guardcode(a.get("delta_when"), ov),
            reload_when=self.guardcode(pol.get("when"), ov) if d and "reload" in pol else None,
            reload=self.code_of(pol["reload"], ov) if d and "reload" in pol else None,
            gate=tuple(self.setcode(g.get(k, ()), ov) for k in ("false", "true")),
            read=self.cellget(a["cell"]),
            store=self.storeof(name),
            entry=a.get("emit") == "entry",
            produce=[(t, part == "hi") for t, part in a["produce"]],
            flag=fl.get("name"),
            seed=fl["seed"] if fl and rep is not None else None,
            moves="delta" in a,
            mask=(1 << a["width"]) - 1,
            delta=None if delta is None else self.code_of(delta, ov),
            repeat=rep,
            clamp=self.code_of(pol["clamp"], ov) if d and "clamp" in pol else None,
            edge=pol.get("edge", 0) if d else 0,
            complement=pol == "reflect-complement",
            reflect=pol == "reflect",
            phase=None if ph is None else self.code_of(ph, ov),
            turn=self.space(ph["cell"]) if pol == "reflect" else None,
            amp=self.ampcode(am, ov) if am else None,
        )

    def ampcode(self, am, ov):
        """A triangle's turn (§5): its own bound, or a count of the steps it takes."""
        lo, hi = am.get("interval", (None, None))
        cell = self.space(am["cell"]) if "count" in am else (None, None)
        return (
            None if lo is None else self.code_of(lo, ov),
            None if hi is None else self.code_of(hi, ov),
            am.get("shift"),
            self.code_of(am["count"], ov) if "count" in am else None,
            cell[0],
            cell[1],
        )

    def step(self, p, prod, edge):
        """One armed accumulator's step, over the plan its record compiled to."""
        ov = p.ov
        self.cur = p.beyond
        if not p.when(ov):
            return
        if p.trap:
            raise AssertionError("the arm the certified horizon never takes")
        if p.divider is not None and not p.divider(ov):
            return
        # the decision the step makes, made once and before anything moves: a gate
        # reports what the step did, not a re-reading of a cell the step moved
        stepped = p.step_when(ov)
        val = p.read()
        if p.reload is not None and p.reload_when(ov):
            val = p.reload(ov)
        out = val
        if p.moves and p.delta_when(ov):
            out = self.apply(p, val, prod, stepped)
            if out is None:
                return  # the policy took the value to its bound, and said so itself
        emitted = val if p.entry else out
        p.store(out)
        for target, hi in p.produce:
            prod.append((target, (emitted >> 8) & 0xFF if hi else emitted & 0xFF))
        for put, f in p.gate[stepped]:
            put(f(ov) & 0xFF, prod, edge)

    def apply(self, p, val, prod, stepped):
        """One step of a bounded accumulator: delta, bound, policy, phase."""
        if not stepped:
            return val
        ov, m = p.ov, p.mask
        if p.repeat is not None:  # the closed triangle: n additions, and the carry they leave
            step, n, f = p.repeat[0](ov), p.repeat[1](ov), p.flag
            if f is not None:
                self.flags[f] = p.seed
            for _ in range(n):
                s = val + step
                if f is not None:
                    self.flags[f] = 1 if s > m else 0
                val = s & m
            return val
        step = p.delta(ov)
        if p.clamp is not None:
            return self.toward(p, val, step, prod)
        if p.complement:  # the triangle one complement folds
            hi = p.amp[1](ov)
            if not val & (m ^ m >> 1) and val > hi:
                val ^= m
            return (val + step) & m
        ph = p.phase(ov) if p.phase is not None else 0
        out = (val - step if ph else val + step) & m
        if p.reflect and self.turned(p, out, ph):
            get, put = p.turn
            put((get() + (-1 if ph else 1)) & 0xFF)
        return out

    def turned(self, p, out, ph):
        """Whether the triangle turns on this step: at its bound, or on a count.

        A bound is the turn where the accumulator's value is the modulator's own;
        where two modulators sum into one cell the value is neither's, so the turn
        is a counter of the modulator's own steps against its period, which is
        what ``count`` names.  The counter is a cell of section 5's vocabulary,
        so a modulator on the global channel counts in a global cell.
        """
        lo, hi, shift, count, get, put = p.amp
        if count is None:
            a, b = lo(p.ov), hi(p.ov)
            return (out >> shift) == ((b if not ph else a) >> shift)
        n = (get() + 1) & 0xFF
        turn = n == count(p.ov)
        put(0 if turn else n)
        return turn

    def toward(self, p, val, step, prod):
        """``clamp(target)``: move by ``step``, and take the target where it is passed.

        ``edge`` is where the family puts the boundary: the step that lands exactly
        on the target either reaches it or does not, and the object says which.
        """
        b = p.edge
        d = val - p.clamp(p.ov)
        if (d + step >= b) if d < 0 else (d - step < b):
            self.take(self.c["note"][self.v], prod)
            return None
        step += b
        return (val + step if d < 0 else val - step) & p.mask

    # ---- section 5's cells ----------------------------------------------------
    def part_of(self, name):
        """One named cell, read: its space, and the half a ``.hi``/``.lo`` picks (§5)."""
        s, part = self.split_cell(name)
        x = self.space(s)[0]()
        return x if part is None else (x & 0xFF if part == "lo" else (x >> 8) & 0xFF)

    def storeof(self, name):
        """An accumulator's own move, compiled once for the accumulator it names."""
        f = self.stores.get(name)
        if f is None:
            f = self.stores[name] = self.storecode(name, self.o["accs"][name])
        return f

    def storecode(self, name, a):
        """Move an accumulator's cell, held to the interval its record declares.

        Section 5's bound is the invariant and not a hint, so the renderer
        asserts it: two constants -- which is what *statically known* means --
        and a move that leaves them stops the render rather than rendering
        something the object does not claim.  The threshold a ``reflect`` turns
        at and a ``reflect-complement`` folds at is the triangle's ``amplitude``
        and lives there; it is the step's own arithmetic, not a claim about the
        cell, and in neither family are the two the same interval.
        """
        b, put = a.get("bound"), self.cellput(a["cell"])
        if b is None:
            return lambda val: self.sprung("%s stores with no bound to hold it to" % name)
        lo, hi = b["interval"]
        why = "%s left its %s bound [%d, %d] at %%d" % (name, b["from"], lo, hi)
        return lambda val: put(val) if lo <= val <= hi else self.sprung(why % val)

    def shadow_pair(self, name):
        """A register pair read back out of the shadow the tune writes through."""
        r = chipreg(name + "_lo", self.v)
        return self.shadow[r] | self.shadow[r + 1] << 8

    def shadow_store(self, name, val):
        r = chipreg(name + "_lo", self.v)
        self.shadow[r], self.shadow[r + 1] = val & 0xFF, (val >> 8) & 0xFF


def render(obj, ticks):
    """The whole horizon as a list of per-tick ``(register, value)`` write lists."""
    p = Player(obj)
    return [p.tick() for _ in range(ticks)]
