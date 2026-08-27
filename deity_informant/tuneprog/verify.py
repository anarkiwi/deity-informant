"""S8 -- differential verification of a tuneprog against its trace, and the certificate.

Runs ``init(song)`` and then tick after tick from the pre-init image, feeding the
recorded input stream, and compares each call's ordered ``(address, value)`` SID
write list -- plus the schedule effects (VIC/CIA stores) and init's own list --
with :class:`~.tracedata.Trace`'s reference log. A mismatch is reported with the
call, the write index, both values and the pc of the IR statement that made the
write. Envelope violations and unverified paths arrive as
:class:`~.ir.TrapError` and count as divergences.

Each call's footprint hash is computed exactly as the tracer computes it (same
address set, same keyed blake2b), so the tuneprog's own periodicity ``(k, p)``
is directly comparable with the trace's -- the second half of the certificate.

Verification is chunked: :meth:`Verifier.run` stops on a CPU budget and
:meth:`Verifier.state` pickles everything needed to resume, so a long
certificate fits inside repeated short invocations.

Public API: :class:`Reference`, :class:`Verifier`, :func:`verify`.
"""

from __future__ import annotations

import time

import numpy as np

from ..lifter import STATUS_BITS
from .emit import PyProgram, certificate
from . import grid
from .interp import Interp, Machine, NmiMachine
from .ir import TrapError
from . import nmi as N
from .machine import STATUS, entry_frame
from .tracevm import REG_IN

PAL, NTSC = 985248, 1022730
INIT_CALL = 0xFFFFFFFF
NMI_ENTRY = {"kind": "nmi"}  # the frame the 6510 pushes taking an NMI
STATE_VERSION = 3  # resume-state layout; an older pickle restarts rather than resumes


def _nmis(log, calls):
    """``(rows, per-call bounds)`` of the traced preemption schedule; a row is
    ``(stores made, handler, SP, status, interrupted pc, A, X, Y)``.

    All but the first two are :data:`~.nmi.REPLAYED`: interrupted state the replay
    takes from the row rather than computing, counted as the schedule entry's
    ``replayed_registers`` in the certificate."""
    if not log or log["call"].size == 0:
        return [], np.zeros(calls + 1, np.int64)
    call = np.asarray(log["call"], dtype=np.int64)
    cols = ("st", "addr", "sp", "p", "pc", "a", "x", "y")
    rows = list(zip(*(log[c].tolist() for c in cols)))
    return rows, np.searchsorted(call, np.arange(calls + 1))


def _packed(log, calls):
    """``(packed writes, per-call bounds, init writes)`` from a trace write log."""
    if not log:
        return np.zeros(0, np.uint32), np.zeros(calls + 1, np.int64), []
    call = log["call"]
    pk = (log["addr"].astype(np.uint32) << 8) | log["val"]
    isinit = call == INIT_CALL
    init = [(int(a), int(v)) for a, v in zip(log["addr"][isinit], log["val"][isinit])]
    play, pk = call[~isinit], pk[~isinit]
    return pk, np.searchsorted(play, np.arange(calls + 1)), init


class Reference:
    """The trace's per-call reference: write lists, hashes, pinned inputs."""

    def __init__(self, trace, calls=None):
        self.calls = int(trace.meta["calls"] if calls is None else calls)
        self.sidpk, self.sidix, self.init_sid = _packed(trace.wlog, self.calls)
        self.iopk, self.ioix, self.init_io = _packed(trace.iolog, self.calls)
        self.state_hash = np.asarray(trace.state_hash)
        self.footprint = np.asarray(trace.footprint_size)
        self.state_hash_free = np.asarray(trace.state_hash_free)
        self.footprint_free = np.asarray(trace.footprint_free)
        self.period_free = trace.meta.get("period_free")
        self.first_repeat_free = trace.meta.get("first_repeat_free")
        nsites = N.sites(trace) if N.entries(trace) else frozenset()
        pinned = [i for i in trace.inputs if i[3] < REG_IN]
        self.inputs = [i for i in pinned if i[1] not in nsites]
        self.nmi_inputs = [i for i in pinned if i[1] in nsites]
        self.regs = {}
        for c, _site, _op, addr, val in trace.inputs:
            if addr >= REG_IN and c >= 0:
                self.regs.setdefault(int(c), []).append((addr - REG_IN, val))
        self.nmipk, self.nmiix = _nmis(trace.nmilog, self.calls)
        self.period = trace.meta.get("period")
        self.first_repeat = trace.meta.get("first_repeat")
        self.entry = trace.meta["entry"]
        self.song = trace.meta["song"]
        self.load = tuple(trace.meta["load"])

    def hashes(self, free):
        """The per-tick ``(footprint sizes, digests)`` of the footprint ``free`` picks."""
        if free:
            return self.footprint_free, self.state_hash_free
        return self.footprint, self.state_hash

    def periodicity(self, free):
        """The ``(period, first repeat)`` witness of that same footprint."""
        if free:
            return self.period_free, self.first_repeat_free
        return self.period, self.first_repeat

    def nmis(self, call):
        """``[(stores made, handler, SP, status, pc, A, X, Y)]`` of this tick's preemptions."""
        return self.nmipk[self.nmiix[call] : self.nmiix[call + 1]]

    def sid(self, call):
        return self.sidpk[self.sidix[call] : self.sidix[call + 1]].tolist()

    def io(self, call):
        return self.iopk[self.ioix[call] : self.ioix[call + 1]].tolist()


def _status(regs):
    return sum(regs[i] << s for i, s in STATUS_BITS) | 0x20


def nmi_procs(prog):
    """``{handler address: procedure}`` for every NMI entry of the schedule."""
    return {p.blocks[p.entry].src: name for name, p in prog.procs.items() if p.kind == "nmi"}


def page_free(prog):
    """True when S4 proved ``prog`` stack-free: its footprint leaves the page out.

    A residual program keeps its pushes and must claim periodicity on the whole
    write set, so the witness it may stop and certify on is the page-inclusive one.
    """
    return prog.meta.get("stack") == "eliminated"


class Verifier:
    """Runs a tuneprog against a :class:`Reference`, chunked and resumable."""

    def __init__(self, prog, ref, backend="py", src=None, obs=False):
        self.prog = prog
        self.ref = ref
        self.free = page_free(prog)
        self.backend = backend
        self.nmi = nmi_procs(prog)
        self.queue = []
        cls = NmiMachine if self.nmi else Machine
        self.M = cls(prog.image(), ref.load, inputs=ref.inputs)
        if self.nmi:
            self.M.alt = list(ref.nmi_inputs)
        self.exe = Interp(prog, self.M) if backend == "interp" else PyProgram(prog, self.M, src=src)
        self.tickproc = prog.procs[prog.meta["tick_proc"]]
        self.init = prog.procs[prog.meta["init_proc"]]
        self.call = -1
        self.hashes = {}
        self.period = None
        self.first_repeat = None
        self.div = None
        self.nreg = 0
        self.seconds = 0.0
        # opt-in: the trackerprog observable per verified tick, off the same writes
        # the raw comparison just passed (:func:`~.grid.reduce_tick`)
        self.obs = [] if obs else None

    # ---- state (chunked runs) ----------------------------------------------
    def state(self):
        return {
            "v": STATE_VERSION,
            "M": self.M,
            "call": self.call,
            "hashes": self.hashes,
            "period": self.period,
            "first_repeat": self.first_repeat,
            "div": self.div,
            "nreg": self.nreg,
            "seconds": self.seconds,
            "obs": self.obs,
        }

    def restore(self, st):
        """Resume from :meth:`state`; a state an older layout wrote starts over."""
        if st.get("v") != STATE_VERSION:
            return self
        self.M = st["M"]
        self.exe.M = self.M
        for k in ("call", "hashes", "period", "first_repeat", "div", "nreg", "seconds", "obs"):
            setattr(self, k, st[k])
        return self

    # ---- the machine's side of a tick --------------------------------------
    def _enter(self, entry=None, ret=None, status=None):
        """Push the frame the machine pushes: a JSR return, or the interrupt frame.

        :func:`~.machine.entry_frame` is that frame -- the status byte, and A/X/Y
        too where the KERNAL dispatched. The interrupt disable is the tick's own
        first statement (:func:`~.build._irq_entry`), so the entry flags are it;
        a preemption replays the return address and status the schedule recorded.
        """
        M = self.M
        frame = entry_frame(entry or {"kind": "sub"})
        if ret is None:
            ret = 0x0000 if frame else 0x0001
        M.push(ret >> 8)
        M.push(ret & 0xFF)
        for what in frame:
            if what is not STATUS:
                M.push(M.regs[what])
            else:
                M.push(_status(M.regs) if status is None else status)

    def _run_nmi(self, row):
        """Enter one NMI handler on the frame the 6510 pushes taking it.

        The schedule carries the stack pointer the machine was at, because the
        tick's own frame is an IR value the register file does not follow.
        """
        _st, addr, sp, status, pc, a, x, y = row
        name = self.nmi.get(addr)
        if name is None:
            raise TrapError("nmi handler", "$%04X is no entry of the schedule" % addr)
        M = self.M
        M.hook = None  # a handler's own stores are not preemption points
        held = list(M.regs)
        M.regs[0], M.regs[1], M.regs[2], M.regs[3] = a, x, y, sp
        M.swap()
        self._enter(NMI_ENTRY, ret=pc, status=status)
        self._call_proc(self.prog.procs[name])
        M.swap()
        M.regs[:] = held  # an NMI leaves the interrupted program's registers alone
        M.hook = self._preempt

    def _preempt(self):
        """Run every NMI the traced schedule places before the store about to happen."""
        M, q = self.M, self.queue
        while q and q[0][0] <= M.stores:
            self._run_nmi(q.pop(0))

    def _drain(self):
        """The NMIs of this tick the host took after the play routine returned."""
        while self.queue:
            self._run_nmi(self.queue.pop(0))
        self.M.hook = None

    def _call_proc(self, proc):
        M = self.M
        vals = self.exe.run(proc.name, [M.regs[i] for i in proc.params])
        for i, v in zip(proc.rets, vals):
            M.regs[i] = v

    def run_init(self, song=None):
        """Run ``init(song)`` and compare its write list with the trace's."""
        M = self.M
        M.regs[0] = self.ref.song if song is None else song
        M.sid.clear()
        M.io.clear()
        M.src.clear()
        self._enter()  # init is always entered as a subroutine (machine.init_runner)
        t0 = time.process_time()
        try:
            self._call_proc(self.init)
        except TrapError as e:
            self.div = {"tick": -1, "index": -1, "trap": e.why, "detail": e.detail}
        self.seconds += time.process_time() - t0
        if self.div is None:
            self._compare(-1, self.ref.init_sid, self.ref.init_io)
        M.play_phase()
        self.call = 0
        return self

    def _compare(self, call, want_sid, want_io):
        M = self.M
        got = [(a << 8) | v for a, v in M.sid]
        want = [(a << 8) | v for a, v in want_sid] if call < 0 else want_sid
        if got != want:
            self.div = self._diff(call, got, want, "sid")
            return False
        gio = [(a << 8) | v for a, v in M.io]
        wio = [(a << 8) | v for a, v in want_io] if call < 0 else want_io
        if gio != wio:
            self.div = self._diff(call, gio, wio, "io")
            return False
        return True

    def _observe(self):
        """Append this tick's :class:`~.grid.TickObs`, mirrors folded to registers."""
        M = self.M
        w = [(int(r), v) for r, (_a, v) in zip(grid.regs([a for a, _ in M.sid]), M.sid) if r >= 0]
        self.obs.append(grid.reduce_tick(w, self.obs[-1] if self.obs else None))

    def _diff(self, call, got, want, what):
        i = next(
            (j for j in range(max(len(got), len(want))) if got[j : j + 1] != want[j : j + 1]), 0
        )
        g = got[i] if i < len(got) else None
        w = want[i] if i < len(want) else None
        return {
            "tick": call,
            "index": i,
            "compared": what,
            "expected": None if w is None else ["$%04X" % (w >> 8), w & 0xFF],
            "got": None if g is None else ["$%04X" % (g >> 8), g & 0xFF],
            "site": "$%04X" % self.M.src[i] if what == "sid" and i < len(self.M.src) else None,
        }

    def run(self, calls=None, budget=None, chunk=256):
        """Verify up to ``calls`` ticks; stops early on ``budget`` CPU seconds."""
        if self.call < 0:
            self.run_init()
        ref = self.ref
        end = ref.calls if calls is None else min(calls, ref.calls)
        t0 = time.process_time()
        while self.call < end and self.div is None:
            for _ in range(min(chunk, end - self.call)):
                if not self.tick():
                    break
            self.seconds += time.process_time() - t0
            t0 = time.process_time()
            if budget is not None and self.seconds > budget:
                break
        return self.call >= end or self.div is not None

    def tick(self):
        """Run, compare and hash one tick; falsy on a divergence, which ``div`` states."""
        M, c = self.M, self.call
        for j, v in self.ref.regs.get(c, ()):
            self.nreg += 1
            if M.regs[j] != v:
                self.div = {"tick": c, "index": -1, "compared": "entry register", "reg": j}
                return False
        M.sid.clear()
        M.io.clear()
        M.src.clear()
        self._enter(self.ref.entry)
        try:
            if self.nmi:
                self.queue = list(self.ref.nmis(c))
                M.stores, M.hook = 0, self._preempt
            self._call_proc(self.tickproc)
            if self.nmi:
                self._drain()
        except TrapError as e:
            if self.nmi:
                M.hook = None
            self.div = {"tick": c, "index": -1, "trap": e.why, "detail": e.detail}
            return False
        if not self._compare(c, self.ref.sid(c), self.ref.io(c)):
            return False
        if self.obs is not None:
            self._observe()
        n, h = M.hash(self.free)
        sizes, digests = self.ref.hashes(self.free)
        if n != sizes[c] or h != digests[c]:
            self.div = {"tick": c, "index": -1, "compared": "state hash"}
            return False
        if self.period is None:
            ninp = M.icur + self.nreg
            prev = self.hashes.get((n, h))
            if prev is None:
                self.hashes[(n, h)] = (c, ninp)
            elif prev[1] == ninp:
                self.period = c - prev[0]
                self.first_repeat = c
        self.call = c + 1
        return True

    _one = tick

    # ---- certificate --------------------------------------------------------
    def subtune(self):
        e = self.ref.entry
        tperiod, tfirst = self.ref.periodicity(self.free)
        clock = NTSC if "ntsc" in e["source"] else PAL
        done = self.call
        out = {
            "song": self.ref.song + 1,
            "ticks": done,
            "seconds": round(done * e["cycles_per_tick"] / clock, 2),
            "cycles_per_tick": e["cycles_per_tick"],
            "inputs_pinned": self.M.icur + self.nreg + getattr(self.M, "acur", 0),
            "period": self.period,
            "first_repeat": self.first_repeat,
            "trace_period": tperiod,
            "trace_first_repeat": tfirst,
            "complete": bool(
                self.period is not None
                and self.first_repeat is not None
                and self.div is None
                and done > self.first_repeat
                and self.period == tperiod
                and self.first_repeat == tfirst
            ),
            "closure": (
                "static" if (self.prog.meta.get("static_closure") or {}).get("closed") else "trace"
            ),
            "envelope_traps": int(self.div is not None and self.div.get("trap") == "envelope"),
            "divergences": int(self.div is not None),
        }
        if self.nmi:
            out["nmis"] = len(self.ref.nmipk)
            out["nmi_entries"] = sorted(self.nmi.values())
        return out


def prefix_check(prog, ref, calls, backend="interp"):
    """Run ``calls`` ticks on the other executor (E12): interpreter vs generated code."""
    v = Verifier(prog, ref, backend=backend)
    v.run(calls)
    return v


def verify(prog, trace, calls=None, prefix=0, src=None, state=None, budget=None):
    """Verify ``prog`` against ``trace``; returns the :class:`Verifier`."""
    ref = Reference(trace, calls)
    v = Verifier(prog, ref, src=src)
    if state is not None:
        v.restore(state)
    v.run(calls, budget=budget)
    if prefix and v.div is None:
        p = prefix_check(prog, ref, min(prefix, v.call))
        if p.div is not None:
            v.div = dict(p.div, executor="interp")
    return v


def certify(prog, verifier, prefix=0, stage="S4", extra=None):
    """The certificate document for a finished :class:`Verifier`."""
    cost = {
        "verify_cpu_seconds": round(verifier.seconds, 1),
        "calls_per_second": round(verifier.call / max(verifier.seconds, 1e-9)),
        "ir_statements": sum(len(b.stmts) for p in prog.procs.values() for b in p.blocks.values()),
        "ir_blocks": sum(len(p.blocks) for p in prog.procs.values()),
        "ir_procs": len(prog.procs),
    }
    cost.update(extra or {})
    sub = verifier.subtune()
    sub["interp_prefix"] = prefix
    return certificate(prog, [sub], cost, divergence=verifier.div, stage=stage)
