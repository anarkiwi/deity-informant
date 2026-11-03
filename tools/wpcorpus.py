"""Run every song of an HVSC tree once, for its full length, and record what lifting it shows.

``python tools/wpcorpus.py ROOT --out DIR [--sample N --seed S --jobs J --timeout S
--call-timeout S --max-cycles C --default-length S]``; see ``docs/corpus.md``.
"""

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

# pylint: disable=wrong-import-position
import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import struct  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402
from concurrent.futures import TimeoutError as FutureTimeout  # noqa: E402
from concurrent.futures import as_completed  # noqa: E402
from functools import lru_cache  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import zstandard  # noqa: E402
from jennings.opcodes import MEM_MODES, OPCODES  # noqa: E402
from pebble import ProcessPool  # noqa: E402
from tqdm import tqdm  # noqa: E402
from vagrant_moses import memmap as mm  # noqa: E402
from vagrant_moses.bus import banking  # noqa: E402

try:
    from vagrant_moses.smc import Scan
except ImportError:  # a vagrant-moses without the scan
    Scan = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deity_informant import c64, frames, trace, wholeprog  # noqa: E402
from deity_informant.smc.classify import (  # noqa: E402
    expected_reads,
    expected_writes,
    insn_reads,
    view,
)
from deity_informant.smc.expect import lift_reason  # noqa: E402
from deity_informant.smc.machine import _ENTRY, MachineOracle, Queue, _Mem, _frames  # noqa: E402
from deity_informant.smc.oracle import writer_of  # noqa: E402
from smclabel import iomap, sidfile  # noqa: E402 pylint: disable=wrong-import-order

CLOCKS = ("unknown", "PAL", "NTSC", "PAL/NTSC")
#: Init accesses logged to read the CPU port and CIA 1 timer A latch writes.
PROBE = [(0x00, 0x02), (mm.CIA1_TIMER_A_LO, mm.CIA1_TIMER_A_HI + 1)]
_LENGTHS = {}


def songlengths(path):
    """``{md5: [seconds per song]}`` of an HVSC ``Songlengths.md5``."""
    out = {}
    for line in Path(path).read_text(encoding="latin-1").splitlines():
        key, eq, val = line.partition("=")
        if eq and not line.startswith(";"):
            out[key.strip()] = [
                int(m) * 60 + float(s) for m, _, s in (t.partition(":") for t in val.split())
            ]
    return out


def header(data):
    """The PSID/RSID header fields, or None."""
    if data[:4] not in (b"PSID", b"RSID") or len(data) < 0x76:
        return None
    ver, _, load, init, play, songs, start, speed = struct.unpack(">HHHHHHHI", data[4:0x16])
    flags = int.from_bytes(data[0x76:0x78], "big") if ver >= 2 else 0
    extra = [data[0x7A] if ver >= 3 else 0, data[0x7B] if ver >= 4 else 0]
    return {
        "kind": data[:4].decode(),
        "version": ver,
        "load": load,
        "init": init,
        "play": play,
        "songs": max(songs, 1),
        "start": start,
        "flags": flags,
        "clock": CLOCKS[(flags >> 2) & 3],
        "sid_models": [(flags >> s) & 3 for s in (4, 6, 8)],
        "extra_sids": [0xD000 | b << 4 for b in extra if b],
        "speed_word": speed,
    }


def latch_of(log, ntsc):
    """CIA 1 timer A latch after an init access log (the KERNAL's for unwritten bytes), and
    whether init wrote it."""
    k = mm.video(ntsc).kernal_latch
    lohi = [k & 0xFF, k >> 8]
    wrote = False
    for addr, v, _, w in log:
        if w and mm.CIA1_TIMER_A_LO <= addr <= mm.CIA1_TIMER_A_HI:
            lohi[addr - mm.CIA1_TIMER_A_LO], wrote = v, True
    return lohi[0] | lohi[1] << 8, wrote


def sid_model(row):
    """The SID model a song's header asks for: ``8580`` when only it, else ``6581``."""
    return "8580" if row.get("sid_models", [0])[0] == 2 else "6581"


def volatile_of(row):
    """The device window $D000-$DFFF plus extra SIDs outside it."""
    lo, hi = c64.DEVICES
    return [c64.DEVICES] + [(a, a + 0x20) for a in row["extra_sids"] if not lo <= a < hi]


def flat_init(img, row, mc):
    """Init on the reference: ``(state, result)``, the result's log covering the probes."""
    mem = bytearray(img)
    vol = volatile_of(row) + PROBE
    r = wholeprog.reference_call(
        mem, row["init"], a=row["song"] - 1, volatile=vol, log=True, max_cycles=mc
    )
    return mem, r


def plan(path, rel, default, mc):
    """The rows' identity, length, rate and planned calls for every song of one file."""
    data = Path(path).read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    hdr = header(data)
    if hdr is None:
        return [{"path": rel, "md5": md5, "song": 0, "calls": 0, "skip": "not a sid file"}]
    tune = sidfile(data)
    lengths = _LENGTHS.get(md5, [])
    rows = []
    for song in range(1, hdr["songs"] + 1):
        row = {"path": rel, "md5": md5, **hdr, "song": song}
        if tune is not None:
            row["init"] = tune[2]
        row["speed"] = (hdr["speed_word"] >> min(song - 1, 31)) & 1 if hdr["kind"] == "PSID" else 0
        row["length_default"] = song > len(lengths)
        row["length_s"] = default if row["length_default"] else lengths[song - 1]
        v = mm.video(row["clock"] == "NTSC")
        rate, src = v.clock_hz / v.cycles_per_frame, "vbi"
        if tune is None:
            row["skip"] = "not sid-callable"
        elif row["speed"] and hdr["play"] and hdr["kind"] == "PSID":
            latch, wrote = latch_of(flat_init(tune[1], row, mc)[1]["log"], row["clock"] == "NTSC")
            rate, src = v.clock_hz / (latch + 1), "cia-init" if wrote else "cia-kernal"
        row |= {"rate_hz": rate, "rate_src": src, "calls": max(1, round(row["length_s"] * rate))}
        rows.append(row)
    return rows


def _diff(base, ram):
    """``[[addr, hex], ...]`` runs where ``ram`` differs from ``base``."""
    a, b = np.frombuffer(bytes(base), np.uint8), np.frombuffer(bytes(ram), np.uint8)
    d = np.flatnonzero(a != b)
    runs = np.split(d, np.flatnonzero(np.diff(d) > 1) + 1) if d.size else []
    return [[int(g[0]), b[g[0] : g[-1] + 1].tobytes().hex()] for g in runs]


def bank_evidence(phases):
    """Why ``[(image, facts, $01 value)]`` needs the bank map: CPU port stores, and code bytes
    or vectors the executed starts fetch from a window that is not RAM under that value."""
    ev = set()
    for img, facts, port in phases:
        if 0 in facts.writes or 1 in facts.writes:
            ev.add("port-write")
        for pc in facts.starts:
            for addr, role in insn_reads(img, pc, 0, 0xFF):
                v = view(port & 7, addr)
                if role != "ret_addr" and v != "ram":
                    ev.add(f"{role}-in-{v}")
    return sorted(ev)


def _flat_view(addr):
    """What the flat model sees at ``addr``: the device window, else RAM."""
    lo, hi = c64.DEVICES
    return "io" if lo <= addr < hi and addr >= 2 else "ram"


def unfaithful(port, store=False):
    """The ``range`` objects where the CPU under $01 value ``port`` does not see what the flat
    model does (:func:`_flat_view`): the port registers and the pages banked otherwise;
    for a ``store``, only those it does not reach as the flat model does (RAM under ROM is
    reached as RAM)."""
    pages = (range(p << 12, (p + 1) << 12) for p in range(16))
    seen = ((r, view(port & 7, r[2]), _flat_view(r[2])) for r in pages)
    return [range(2)] + [r for r, v, f in seen if v != f and not (store and f == "ram")]


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def flat_evidence(last, records, n, play, port, vol, mc):
    """The bank evidence of the flat model's first ``n`` play calls from the machine's post-init
    record ``last``, each replaying its machine record's device reads: the data loads
    (``data-in-*``) and stores (``store-in-*``) it makes where the CPU under $01 value ``port``
    does not see what the flat model does (:func:`unfaithful`). Until the first, the flat model
    runs as the machine does, so a flat lift that differs from the machine without one is
    itself wrong."""
    s = _regs(trace.Session(last["ram"], mc, False, None), last["reg"])
    feed, seen = [], set()
    s.mem.subscribe_to_read([a for lo, hi in vol for a in range(lo, hi)],
                            lambda _: feed.pop() if feed else 0)  # fmt: skip
    s.mem.observe_data_access(
        on_read=lambda a: seen.add(f"data-in-{view(port & 7, a)}"),
        on_write=lambda a, _: seen.add(f"store-in-{view(port & 7, a)}"),
        reads=unfaithful(port),
        writes=unfaithful(port, store=True),
    )
    for _, rec in zip(range(n), records):
        feed[:] = c64.reads(rec)[::-1]
        s.call(play)
        if seen:
            break
    return sorted(seen)


def _regs(s, reg):
    """Jennings session ``s`` from the ``Program.call`` register file ``reg``."""
    m = s.mpu
    m.a, m.x, m.y, m.sp = reg[:4]
    m.p = sum(reg[i] << bit for i, bit in c64._FLAGS) | 0x30  # pylint: disable=protected-access
    return s


def _cells(cells):
    return sorted(c.sid for c in cells), sorted({lift_reason(c) or "lift-exact" for c in cells})


def _timed(fn, mem, n):
    """Seconds for ``fn(mem, n)`` after one warm call on a copy, and its result."""
    fn(bytearray(mem), 1)
    t = time.perf_counter()
    out = fn(mem, n)
    return time.perf_counter() - t, out


def _ref_run(play, vol, mc, bank=None, port=None):
    def run(mem, n):
        return wholeprog.reference_calls(
            mem, play, n, volatile=vol, max_cycles=mc, bank=bank, port=port
        )

    return run


def _lifted_run(prog, mc, port=None):
    def run(mem, n):
        s = prog.session(mem, mc)
        for _ in range(n):
            if port is not None:
                mem[1] = port
            s.call()

    return run


def _jennings_run(play, mc):
    def run(mem, n):
        s = trace.Session(mem, mc, False, None)
        for _ in range(n):
            s.call(play)

    return run


_BANK = []


def _bank():
    if not _BANK:
        _BANK.append(c64.bank_map())
    return _BANK[0]


def _timing(n, cycles, t_ref, t_lift, t_jen):
    return {
        "calls": n,
        "cycles": cycles,
        "reference_s": t_ref,
        "lifted_s": t_lift,
        "jennings_s": t_jen,
    }


def _compiled(prog):
    """Compile ``prog``'s native code now: milliseconds, or None on the interpreter."""
    s = prog.compile() if prog.backend == "jit" else None
    return None if s is None else 1e3 * s


def _lifted_row(prog, lift_ms, bad):
    """The lift's row fields; a :func:`c64.check` result ``bad`` is its ``outcome`` and either
    its ``mismatch`` or the ``stop`` the lift agrees with (:data:`c64.AGREE`)."""
    out = c64.outcome(bad)
    return {
        "lift_ms": lift_ms,
        "backend": prog.backend,
        "stats": prog.stats,
        "exact": bad is None,
        "outcome": out,
        "mismatch": bad if out == "mismatch" else None,
        "stop": bad if out in c64.AGREE else None,
        "deopts": sum(prog.deopts.values()),
    }


def psid_song(row, img, mc, cap, defer=False):
    """A PSID song with a play routine: ``(row, facts record or None)``. One machine run of
    init and every call gives the facts, the call records the lift is checked against and the
    cells; its bank evidence picks the flat or the bank-aware lift. ``defer`` returns a banked
    song unlifted (``deferred``) so a later resumed run lifts it."""
    init, play, a = row["init"], row["play"], row["song"] - 1
    _, r = flat_init(img, row, mc)
    phases = [(img, trace.collect(img, lambda s: s.call(init, a), mc), iomap(init))]
    row |= {"init_halt": r["halt"], "init_cycles": r["cycles"]}
    ev = bank_evidence(phases) if r["halt"] != "return" else None
    if ev == []:
        return row | {"mode": "flat", "bank_evidence": ev, "skip": "init " + r["halt"]}, None
    if ev and defer:
        return row | {"deferred": True}, None
    ports = {"init": iomap(init), "call": iomap(play)}
    t = time.perf_counter()
    host = c64._Host(img, ntsc=row["clock"] == "NTSC", model=sid_model(row))
    host.drive(Queue(runs=[((play, 0), row["calls"])]), [(init, a)], mc, ports)
    inits, records, n = host.split()
    last = inits[-1]
    facts = host.facts() if last["halt"] == "return" else None
    if ev is None and facts is not None:
        ev = bank_evidence(phases + [(last["ram"], facts, ports["call"])])
    if ev and defer:
        return row | {"deferred": True}, None
    banked = bool(ev)
    row |= {
        "mode": "banked" if banked else "flat",
        "bank_evidence": ev or [],
        "facts_ms": 1e3 * (time.perf_counter() - t),
        "init_halt": last["halt"],
        "calls_run": n,
    }
    if banked or not cap or cap >= n:
        row |= _label_row("calls", host.result, n, t)
    else:
        row |= label("calls", img, row, cap, mc)
    if facts is None:
        return row | {"skip": "init " + last["halt"]}, None
    port = ports["call"]

    def lifted(banked):
        t = time.perf_counter()
        bank, vol = (_bank(), [c64.DEVICES]) if banked else (None, volatile_of(row))
        prog = wholeprog.lift(last["ram"], c64.entries_of(play, facts), vol, facts, bank=bank)
        lift_ms = 1e3 * (time.perf_counter() - t)
        row["compile_ms"] = _compiled(prog)
        t = time.perf_counter()
        ret = host.sentinel - 1
        bad = c64.check(prog, last["ram"], last["reg"], records, port, mc, ret=ret)
        row.update(_lifted_row(prog, lift_ms, bad), check_ms=1e3 * (time.perf_counter() - t))
        return prog, bank, vol, bad

    prog, bank, vol, bad = lifted(banked)
    if not banked and c64.outcome(bad) == "mismatch":
        ev = flat_evidence(last, records, bad[0] + 1, play, port, vol, mc)
        if ev:
            row |= {"mode": "banked", "bank_evidence": ev}
            banked = True
            prog, bank, vol, bad = lifted(banked)
    row["returned"] = n == row["calls"] and host.last_halt == "return"
    if banked:
        row["configs"] = sorted({b for bs in facts.banks.values() for b in bs})
    else:
        port = None
    t_ref, (nr, cycles, _) = _timed(_ref_run(play, vol, mc, bank, port), bytearray(last["ram"]), n)
    t_lift, _ = _timed(_lifted_run(prog, mc, port), bytearray(last["ram"]), nr)
    t_jen = None if banked else _timed(_jennings_run(play, mc), bytearray(last["ram"]), nr)[0]
    row["timing"] = _timing(nr, cycles, t_ref, t_lift, t_jen)
    return row, {"state": _diff(img, last["ram"]), "reg": list(last["reg"]), "facts": facts}


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def label(oracle, img, row, n, mc, boot=None):
    """The scenario-cell row fields of a song's first ``n`` play calls (``calls``: play called
    as sidplay does) or interrupt-driven frames (``frames``), on the vagrant-moses machine;
    ``boot`` (:class:`c64.Boot`) replaces ``img`` and the row's init."""
    t = time.perf_counter()
    o = MachineOracle(img if boot is None else boot.image, ntsc=row["clock"] == "NTSC",
                      roms=None if boot is None else boot.roms)  # fmt: skip
    if oracle == "calls":
        ports = {"init": iomap(row["init"]), "call": iomap(row["play"])}
        run = o.run([(row["play"], 0)] * n, init=[(row["init"], row["song"] - 1)],
                    call_cycles=mc, ports=ports)  # fmt: skip
    else:
        run = o.run([], frames=n, call_cycles=mc, index_frames=True, **boot.run_kw())
    return _label_row(oracle, run, n, t)


def _label_row(oracle, run, n, t0):
    sids, reasons = _cells(run.cells)
    return {
        "oracle": oracle,
        "label_calls": n,
        "cells": sids,
        "reasons": reasons,
        "cells_first": {c.sid: int(i) for c, i in run.first.items()},
        "unclassified": run.n_unclassified,
        "label_backend": "python" if run.count is None else "native",
        "label_ms": 1e3 * (time.perf_counter() - t0),
    }


@lru_cache(maxsize=None)
def _scan_table():
    """``vagrant_moses.smc.Scan``'s opcode table: the address form, a read and a CPU store of
    it, as :meth:`_IrqScan._decode` derives them."""
    t = np.zeros(256, "<u2")
    for op in range(256):
        (mn, mode), w = OPCODES[op], writer_of(op)
        store = w is not None and w[0] == "cpu"
        read = mode in MEM_MODES and mn not in ("JSR", "JMP") and not store
        t[op] = c64.FORMS.get(mode, 0) | read << 4 | store << 5
    return t.tobytes()


class _IrqScan(MachineOracle):
    """An interrupt-driven run recording, after init, the IRQ path and interrupts taken, and
    whether the main context ever stores outside page 1 or touches I/O. ``backend`` ``native``
    scans in vagrant-moses' recorder; ``python`` (the reference) drops its decode hook once the
    main context has, interrupts then being counted from the machine's events."""

    def __init__(self, image, ntsc=False, backend=None, roms=None):
        backend = backend or ("native" if c64.TRACE_NATIVE else "python")
        super().__init__(image, ntsc=ntsc, roms=roms, backend=backend)
        self.irqs, self.vector, self.ctx, self.result = 0, None, [], None
        self.counting = False
        self.main = Counter()
        self.cpf, self.t0 = mm.video(ntsc).cycles_per_frame, None
        self.sc = Scan(self.rec, _scan_table(), self.sentinel) if self.native else None

    def _frames_phase(self, index_frames):
        if self.native:
            self.m.clear_events()  # pylint: disable=no-member
            self.sc.start(self.cpf)
            self.counting = True
        super()._frames_phase(index_frames)

    def _window(self):
        if self.native:
            if self.counting:
                self.irqs += self._entries(self.m.events)
            self.m.clear_events()  # pylint: disable=no-member

    def _native_run(self):
        self.irqs += self._entries(self.m.events)
        vector, self.main["insns"], busy = self.sc.result()
        if vector is not None:
            bits, kv, hv = vector
            self.vector = {
                "path": "kernal" if banking(bits)[1] else "hardware",
                "port_bits": bits,
                "kernal_vector": kv,
                "hw_vector": hv,
            }
        if busy is not None:
            self.main["first_busy_frame"] = busy
        return super()._native_run()

    def _entries(self, events):
        return sum(_ENTRY.get(n) in ("irq", "nmi") for _, n in events)

    def _decode(self, mc):
        r, ram, events = mc.mpu, self.ram, mc.events
        if events:
            mc.clear_events()
        ctx = self.ctx
        while ctx and r.sp > ctx[-1][1]:
            ctx.pop()
        for kind, fsp in _frames([n for _, n in events], r.sp):
            ctx.append((kind if kind in ("irq", "nmi") else ctx[-1][0] if ctx else "main", fsp))
        if not (self.irq_driven and self.call_cycles == 0):
            if self.call_cycles and mc.cycle - self.call_start > self.call_cycles:
                self.timeout = mc.stop = True
            return
        if self.vector is None:
            self.t0 = mc.cycle
            self.vector = {
                "path": "kernal" if banking(mc.bits)[1] else "hardware",
                "port_bits": int(mc.bits),
                "kernal_vector": int(ram[0x314]) | int(ram[0x315]) << 8,
                "hw_vector": int(ram[0xFFFE]) | int(ram[0xFFFF]) << 8,
            }
        self.irqs += self._entries(events)
        if (ctx and ctx[-1][0] != "main") or r.pc == self.sentinel:
            return
        pc, bits = r.pc, mc.bits
        fetched = [self._read(bits, (pc + k) & 0xFFFF)[0] for k in range(3)]
        mem = _Mem(pc, fetched, ram)
        w = writer_of(fetched[0])
        store = w is not None and w[0] == "cpu"
        writes = expected_writes(mem, pc, r.x, r.y, r.sp) if store else set()
        self.main["insns"] += 1
        busy = any(not 0x100 <= a < 0x200 for a in writes) or any(
            view(bits, a) == "io" for a in writes | expected_reads(mem, pc, r.x, r.y, r.sp, store)
        )
        if busy:
            self.main["first_busy_frame"] = (mc.cycle - self.t0) // self.cpf
            mc.set_decode_hook(None)  # pylint: disable=no-member

    def scan(self, boot, nframes, mc, cells=False):
        """Run ``boot`` (:class:`c64.Boot`) then ``nframes`` interrupt-driven frames; the ``irq``
        row field. With ``cells`` the run's SMC labelling, its cells indexed by frame, is kept
        as :attr:`result`."""
        self.result = self.run(
            [], frames=nframes, call_cycles=mc, index_frames=cells, **boot.run_kw()
        )
        self.irqs += self._entries(self.m.events)
        return {
            **(self.vector or {}),
            "irqs": self.irqs,
            "init_returned": not self.timeout and not self.m.jammed,
            "jammed": bool(self.m.jammed),
            "main_insns": self.main["insns"],
            "idle": "first_busy_frame" not in self.main,
            "first_busy_frame": self.main.get("first_busy_frame"),
        }


def load_span(data):
    """``(load, end)``: the C64 address range a PSID/RSID file's data loads to."""
    off, load = struct.unpack(">HH", data[6:10])
    if load == 0:
        load, off = data[off] | data[off + 1] << 8, off + 2
    return load, load + len(data) - off


_real_roms = lru_cache(maxsize=1)(c64.real_roms)


def irq_boot(row, data):
    """The :class:`c64.Boot` of an interrupt-driven song of the file ``data`` (an RSID, on the
    real ROMs, or a PSID without a play address)."""
    kind = "BASIC" if row["kind"] == "RSID" and row["flags"] & 2 else row["kind"]
    roms = _real_roms() if row["kind"] == "RSID" else None
    return c64.boot(sidfile(data)[1], *load_span(data), row["init"], row["song"],
                    row["clock"] == "NTSC", kind, bool(row["speed"]), roms)  # fmt: skip


def irq_skip(irq):
    """Why an interrupt-driven song's scan (the ``irq`` field) leaves it unlifted, or None: its
    init jams, its main context does real work beside the interrupts (``async main``) or no
    interrupt is taken."""
    if irq["jammed"]:
        return "init jam"
    if not irq["idle"]:
        return "async main"
    return None if irq["irqs"] else "no interrupts"


def frame_song(row, boot, mc):
    """``(row, facts record or None)`` of an idle-main interrupt-driven song.

    One machine run records its frames (IRQ or NMI entry to RTI, the interrupts entered inside
    one with it), they are lifted banked through the song's ROMs and checked and timed frame by
    frame; a run with an interrupt outside a frame is skipped. The facts record has the machine's
    RAM at the first frame over the song's image and the lift's entries. A run that reaches a
    budget limit stops there: a lift exact over the frames recorded is ``halted`` (stop ``budget``
    for a song limit).
    """
    t = time.perf_counter()
    host = frames.FrameHost(boot.image, roms=boot.roms, ntsc=row["clock"] == "NTSC",
                            model=sid_model(row))  # fmt: skip
    host.drive_frames(boot, row["calls"], mc)
    n = len(host.stream)
    row |= {"facts_ms": 1e3 * (time.perf_counter() - t), "calls_run": n, "budget": host.stop}
    if host.foreign:
        return row | {"skip": "interrupt outside a frame: " + ",".join(sorted(host.foreign))}, None
    if host.dirty:
        return row | {"skip": "async main"}, None
    if not n:
        return row | {"skip": "no frames"}, None
    t = time.perf_counter()
    facts = host.facts()
    prog = frames.lift(host, boot.roms, facts)
    lift_ms = 1e3 * (time.perf_counter() - t)
    row["compile_ms"] = _compiled(prog)
    t = time.perf_counter()
    bad = frames.check(prog, host, mc)
    if host.stop.startswith("song") and bad is None:
        bad = [n, "halted", "budget"]
    row |= {"mode": "banked", "bank_evidence": [], "returned": host.last_halt == "return",
            "configs": sorted({b for bs in facts.banks.values() for b in bs}),
            "interrupts_inside": host.ninner, **_lifted_row(prog, lift_ms, bad)}  # fmt: skip
    row["check_ms"] = 1e3 * (time.perf_counter() - t)
    t_ref, t_lift, cycles, timed, hit = frames.timed(prog, host, c64.bank_map(boot.roms), mc)
    rec = {"state": _diff(boot.image, host.image), "entries": list(prog.entries), "facts": facts}
    return row | {"timing": _timing(timed, cycles, t_ref, t_lift, None) | {"interrupted": hit}}, rec


def irq_song(row, boot, mc, cap):
    """An interrupt-driven song on the machine from ``boot`` (:func:`irq_boot`): its IRQ facts
    over all ``calls`` frames, its cells over the first ``cap`` (0: all); an idle-main song is
    then lifted and checked by :func:`frame_song`, any other skipped with its reason."""
    if row["kind"] == "RSID" and row["flags"] & 2:
        row["basic"] = True
    t = time.perf_counter()
    o = _IrqScan(boot.image, ntsc=row["clock"] == "NTSC", roms=boot.roms)
    full = not cap or cap >= row["calls"]
    row["irq"] = o.scan(boot, row["calls"], mc, full)
    row |= {"calls_run": o.irqs, "facts_ms": 1e3 * (time.perf_counter() - t)}
    if not full:
        row |= label("frames", boot.image, row, cap, mc, boot)
    else:
        row |= _label_row("frames", o.result, row["calls"], t)
    if why := irq_skip(row["irq"]):
        return row | {"skip": why}, None
    return frame_song(row, boot, mc)


def run_song(path, row, mc, cap=0, defer=()):
    """``(row, compressed facts line or None)`` of one planned song."""
    row, t0 = dict(row), time.perf_counter()
    try:
        if "skip" in row:
            return row, None
        data = Path(path).read_bytes()
        if row["kind"] == "RSID" or not row["play"]:
            if "irq" in defer:
                return row | {"deferred": True}, None
            row, rec = irq_song(row, irq_boot(row, data), mc, cap)
        else:
            row, rec = psid_song(row, sidfile(data)[1], mc, cap, "banked" in defer)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {**row, "error": repr(e)[:300]}, None
    row["song_ms"] = 1e3 * (time.perf_counter() - t0)
    if rec is None:
        return row, None
    rec = {"path": row["path"], "song": row["song"], "mode": row["mode"], **rec}
    rec["facts"] = rec["facts"].to_dict()
    row["has_facts"] = True
    return row, zstandard.ZstdCompressor().compress((json.dumps(rec) + "\n").encode())


def _worker(lengths_path, backend="jit"):
    wholeprog.BACKEND = backend
    if Path(lengths_path).exists():
        _LENGTHS.update(songlengths(lengths_path))


def _read_rows(path):
    """The rows of a rows file, the last per (path, song); a torn last line is cut off."""
    if not path.exists():
        return {}
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        data = data[: data.rfind(b"\n") + 1]
        path.write_bytes(data)
    rows = (json.loads(line) for line in data.decode().splitlines() if line)
    return {(r["path"], r["song"]): r for r in rows}


def _facts_lines(path):
    """``(end offset, line)`` per complete zstd frame (one facts line each) of a facts file."""
    dctx = zstandard.ZstdDecompressor()
    d, parts, off = dctx.decompressobj(), [], 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 24):
            while chunk:
                parts.append(d.decompress(chunk))
                if not d.eof:
                    off += len(chunk)
                    break
                rest = d.unused_data
                off += len(chunk) - len(rest)
                yield off, b"".join(parts).decode()
                d, parts, chunk = dctx.decompressobj(), [], rest


def _fact_keys(path):
    """The (path, song) keys of a facts file, cut back to its last complete frame."""
    keys, end = set(), 0
    if not path.exists():
        return keys
    for end, line in _facts_lines(path):
        r = json.loads(line)
        keys.add((r["path"], r["song"]))
    if end != path.stat().st_size:
        with open(path, "r+b") as f:
            f.truncate(end)
    return keys


def _q(xs):
    """Count, p10, median and p90."""
    if not xs:
        return None
    p10, med, p90 = np.percentile(np.asarray(xs, float), [10, 50, 90])
    return {"n": len(xs), "p10": float(p10), "median": float(med), "p90": float(p90)}


def _failure(r):
    if "error" in r:
        return r["error"]
    if r.get("mismatch"):
        return "inexact " + json.dumps(r["mismatch"])[:120]
    return None


def _kind(rs):
    ok = [r for r in rs if "exact" in r]
    return {
        "rows": len(rs),
        "lifted": len(ok),
        "flat": sum(r["mode"] == "flat" for r in ok),
        "banked": sum(r["mode"] == "banked" for r in ok),
        "exact": sum(r["exact"] for r in ok),
        "deopting": sum(r["deopts"] > 0 for r in ok),
        "inexact": sum(_failure(r) is not None for r in ok),
        **{k: sum(r.get("outcome") == k for r in ok) for k in c64.AGREE},
        "timeouts": sum(r.get("error") == "timeout" for r in rs),
        "errors": sum("error" in r and r["error"] != "timeout" for r in rs),
        "skipped": dict(Counter(r["skip"] for r in rs if "skip" in r).most_common()),
    }


def _speeds(ok):
    sp = [r["timing"] for r in ok if r.get("timing") and r["timing"]["lifted_s"]]
    jen = [s for s in sp if s["jennings_s"]]
    return {
        "lifted_vs_jennings": _q([s["jennings_s"] / s["lifted_s"] for s in jen]),
        "lifted_vs_reference": _q([s["reference_s"] / s["lifted_s"] for s in sp]),
        "lifted_calls_s": _q([s["calls"] / s["lifted_s"] for s in sp]),
        "lifted_cycles_s": _q([s["cycles"] / s["lifted_s"] for s in sp]),
        "jennings_calls_s": _q([s["calls"] / s["jennings_s"] for s in jen]),
        "reference_calls_s": _q([s["calls"] / s["reference_s"] for s in sp]),
        "compile_ms": _q([r["compile_ms"] for r in ok if r.get("compile_ms") is not None]),
    }


def _costs(rows):
    """Per stage, microseconds per call (per frame for interrupt-driven runs) and, where the
    song's reference cycles per call are known, nanoseconds per cycle."""
    st = {}
    for r in rows:
        t = r.get("timing") or {}
        cyc = t["cycles"] / t["calls"] if t.get("calls") and t["cycles"] else None
        flat = r.get("mode") == "flat"
        stages = [
            ("irq_scan" if "irq" in r else ("flat_facts" if flat else "machine_run"),
             r.get("facts_ms", 0) / 1e3, r["calls"] if "irq" in r else r.get("calls_run")),
            ("check" if flat else "banked_check", r.get("check_ms", 0) / 1e3, r.get("calls_run")),
            ("cpu_label" if flat else "irq_label" if "irq" in r else None,
             r.get("label_ms", 0) / 1e3, r.get("label_calls")),
            ("reference" if flat else "banked_reference", t.get("reference_s"), t.get("calls")),
            ("lifted" if flat else "banked_lifted", t.get("lifted_s"), t.get("calls")),
            ("jennings", t.get("jennings_s"), t.get("calls")),
        ]  # fmt: skip
        for name, sec, n in stages:
            if name and sec and n and ("exact" in r or "irq" in r):
                st.setdefault(name, []).append(
                    (1e6 * sec / n, 1e9 * sec / (n * cyc) if cyc else None)
                )
    return {
        k: {"us_per_call": _q([u for u, _ in v]), "ns_per_cycle": _q([c for _, c in v if c])}
        for k, v in sorted(st.items())
    }


def _first_seen(labelled):
    """First-seen call/frame indices of every labelled cell, and the cells first seen in the
    last 10% of a capped (song longer than the cap) labelling."""
    capped = [r for r in labelled if r["label_calls"] < r.get("calls_run", r["calls"])]
    late = [(r["path"], r["song"], c, i) for r in capped for c, i in r["cells_first"].items()
            if i >= 0.9 * r["label_calls"]]  # fmt: skip
    return {
        "indices": _q([i for r in labelled for i in r["cells_first"].values()]),
        "capped_songs": len(capped),
        "capped_cells": sum(len(r["cells_first"]) for r in capped),
        "late_cells": len(late),
        "late": late[:100],
    }


def summary(rows):
    """Coverage, scenario cells, speed and time distributions, and the failure list."""
    ok = [r for r in rows if "exact" in r]
    labelled = [r for r in rows if "cells" in r]
    irq = [r["irq"] for r in rows if "irq" in r]
    kinds = sorted({r.get("kind", "none") for r in rows})
    return {
        "rows": len(rows),
        "tunes": len({r["path"] for r in rows}),
        "calls_planned": sum(r.get("calls", 0) for r in rows),
        "calls_run": sum(r.get("calls_run", 0) for r in rows),
        "length_default": sum(r.get("length_default", False) for r in rows),
        "rate_src": dict(Counter(r["rate_src"] for r in rows if "rate_src" in r)),
        "by_kind": {k: _kind([r for r in rows if r.get("kind", "none") == k]) for k in kinds},
        "bank_evidence": dict(Counter(e for r in ok for e in r.get("bank_evidence", ()))),
        "stack_private": sum(bool(r["stats"]["stack_private"]) for r in ok),
        "kept_bytes": {
            "nonzero": sum(r["stats"]["kept_bytes"] > 0 for r in ok),
            **(_q([r["stats"]["kept_bytes"] for r in ok]) or {}),
        },
        "labelled": len(labelled),
        "per_reason": dict(Counter(x for r in labelled for x in r["reasons"]).most_common()),
        "top_cells": Counter(c for r in labelled for c in r["cells"]).most_common(30),
        "first_seen": _first_seen(labelled),
        "irq": {
            "songs": len(irq),
            "path": dict(Counter(i.get("path", "none") for i in irq)),
            "idle": sum(i["idle"] for i in irq),
            "init_returned": sum(i["init_returned"] for i in irq),
        },
        "speed": _speeds(ok),
        "lift_ms": _q([r["lift_ms"] for r in ok]),
        "facts_ms": _q([r["facts_ms"] for r in rows if "facts_ms" in r]),
        "check_ms": _q([r["check_ms"] for r in rows if "check_ms" in r]),
        "label_ms": _q([r["label_ms"] for r in rows if "label_ms" in r]),
        "cost_us": _costs(rows),
        "failures": [[r["path"], r["song"], f] for r in rows if (f := _failure(r))],
    }


def markdown(s):
    """A compact markdown rendering of a summary."""
    head = next(iter(s["by_kind"].values()), {})
    out = [
        f"# HVSC corpus run\n\n{s['rows']} songs of {s['tunes']} files; {s['calls_run']} of "
        f"{s['calls_planned']} planned calls/frames run; {s['length_default']} songs at the "
        f"default length; rate sources {s['rate_src']}; wall {s.get('wall_s', 0):.0f} s.\n",
        "| kind | " + " | ".join(head) + " |",
        "|---" * (len(head) + 1) + "|",
    ]
    out += [f"| {k} | " + " | ".join(map(str, v.values())) + " |" for k, v in s["by_kind"].items()]
    out.append(f"\nBank evidence: {s['bank_evidence']}. Stack private: {s['stack_private']}. "
               f"kept_bytes: {s['kept_bytes']}. IRQ-driven: {s['irq']}.\n")  # fmt: skip
    out.append(f"## Gap reasons ({s['labelled']} songs labelled)\n")
    out += [f"- {k}: {v}" for k, v in s["per_reason"].items()]
    out.append("\n## Top scenario cells (songs)\n")
    out += [f"- `{c}`: {n}" for c, n in s["top_cells"]]
    out.append(
        f"\nFirst seen: {json.dumps({k: v for k, v in s['first_seen'].items() if k != 'late'})}.\n"
    )
    out.append("\n## Distributions (p10 / median / p90)\n")
    times = [(k, s[k]) for k in ("lift_ms", "facts_ms", "check_ms", "label_ms")]
    for name, q in [*s["speed"].items(), *times]:
        if q:
            out.append(
                f"- {name}: {q['p10']:.4g} / {q['median']:.4g} / {q['p90']:.4g} (n={q['n']})"
            )
    out.append("\n## Stage costs (p10 / median / p90)\n")
    for name, c in s["cost_us"].items():
        u, n = c["us_per_call"], c["ns_per_cycle"]
        cyc = f"; ns/cycle {n['p10']:.3g} / {n['median']:.3g} / {n['p90']:.3g}" if n else ""
        out.append(
            f"- {name}: us/call {u['p10']:.3g} / {u['median']:.3g} / {u['p90']:.3g}{cyc}"
            f" (n={u['n']})"
        )
    out.append(f"\n## Failures ({len(s['failures'])})\n")
    out += [f"- {p} #{n}: {f}" for p, n, f in s["failures"][:200]]
    return "\n".join(out) + "\n"


def _pool_map(pool, fn, jobs, timeouts, desc, on_result):
    futs = {pool.schedule(fn, args=a, timeout=t): a for a, t in zip(jobs, timeouts)}
    for f in tqdm(as_completed(futs), total=len(futs), desc=desc, unit="job", smoothing=0.02):
        try:
            res = f.result()
        except FutureTimeout:
            res = None
        except Exception as e:  # pylint: disable=broad-exception-caught
            res = e
        on_result(futs[f], res)


def main(argv=None):  # pylint: disable=too-many-locals
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--songlengths", help="default: ROOT/DOCUMENTS/Songlengths.md5")
    ap.add_argument("--default-length", type=float, default=180.0, help="seconds, if unlisted")
    ap.add_argument("--max-cycles", type=int, default=2_000_000, help="per init or call")
    ap.add_argument("--timeout", type=float, default=60, help="seconds per song, plus")
    ap.add_argument(
        "--call-timeout", type=float, help="seconds per call/frame (default by backend)"
    )
    ap.add_argument("--label-calls", type=int, default=1000, help="labelled calls/frames (0: all)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--sample", type=int, default=0, help="random tunes (0: all)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--backend", choices=("jit", "interp"), default="jit", help="lifted engine")
    ap.add_argument("--paths", help="file of tune paths relative to ROOT, one per line")
    ap.add_argument(
        "--defer",
        type=lambda v: tuple(v.split(",")),
        default=(),
        help="banked,irq: leave those songs for a later run",
    )
    ap.add_argument(
        "--redo",
        action="append",
        default=[],
        metavar="REASON",
        help="re-run the rows skipped for REASON, replacing them (repeatable)",
    )
    args = ap.parse_args(argv)
    root, out = Path(args.root), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows_path, facts_path = out / "rows.jsonl", out / "facts.jsonl.zst"
    lengths = args.songlengths or root / "DOCUMENTS" / "Songlengths.md5"
    if args.paths:
        lines = Path(args.paths).read_text(encoding="utf-8").split()
        paths = sorted({root / p for p in lines})
    else:
        paths = sorted(root.rglob("*.sid"))
    if args.sample:
        paths = random.Random(args.seed).sample(paths, min(args.sample, len(paths)))
    t0 = time.perf_counter()
    rows = _read_rows(rows_path)
    if args.redo:
        rows = {k: r for k, r in rows.items() if r.get("skip") not in args.redo}
        tmp = rows_path.with_suffix(".redo")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows.values()), encoding="utf-8")
        os.replace(tmp, rows_path)
    fkeys = _fact_keys(facts_path)
    done = {k for k, r in rows.items() if not r.get("has_facts") or k in fkeys}
    plans = []

    def planned(a, res):
        if not isinstance(res, list):
            err = "timeout" if res is None else repr(res)[:300]
            res = [{"path": a[1], "song": 0, "calls": 0, "error": "plan " + err}]
        plans.extend((a[0], r) for r in res)

    with ProcessPool(
        max_workers=args.jobs, initializer=_worker, initargs=(str(lengths), args.backend)
    ) as pool:
        jobs = [(str(p), str(p.relative_to(root)), args.default_length, args.max_cycles)
                for p in paths]  # fmt: skip
        _pool_map(pool, plan, jobs, [args.timeout] * len(jobs), "plan", planned)
        todo = sorted(
            ((p, r) for p, r in plans if (r["path"], r["song"]) not in done),
            key=lambda pr: -pr[1]["calls"],
        )
        with open(rows_path, "a", encoding="utf-8") as fr, open(facts_path, "ab") as ff:

            def ran(a, res):
                if not isinstance(res, tuple):
                    res = ({**a[1], "error": "timeout" if res is None else repr(res)[:300]}, None)
                row, rec = res
                if row.get("deferred"):
                    return
                if rec is not None:
                    ff.write(rec)
                    ff.flush()
                fr.write(json.dumps(row) + "\n")
                fr.flush()
                rows[(row["path"], row["song"])] = row

            for p, r in todo:
                if "error" in r:
                    ran((p, r), (r, None))
            todo = [(p, r) for p, r in todo if "error" not in r]
            per_call = args.call_timeout
            if per_call is None:
                per_call = 0.02 if c64.TRACE_NATIVE else 0.5
            timeouts = [args.timeout + per_call * r["calls"] for _, r in todo]
            jobs = [(p, r, args.max_cycles, args.label_calls, args.defer) for p, r in todo]
            _pool_map(pool, run_song, jobs, timeouts, "corpus", ran)
    s = summary(list(rows.values()))
    s["wall_s"] = time.perf_counter() - t0
    (out / "summary.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    (out / "summary.md").write_text(markdown(s), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
