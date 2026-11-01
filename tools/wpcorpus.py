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
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import zstandard  # noqa: E402
from pebble import ProcessPool  # noqa: E402
from tqdm import tqdm  # noqa: E402
from vagrant_moses import memmap as mm  # noqa: E402
from vagrant_moses.bus import banking  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deity_informant import c64, trace, wholeprog  # noqa: E402
from deity_informant.smc.classify import (  # noqa: E402
    expected_reads,
    expected_writes,
    insn_reads,
    view,
)
from deity_informant.smc.expect import lift_reason  # noqa: E402
from deity_informant.smc.machine import _ENTRY, MachineOracle, _Mem, _frames  # noqa: E402
from deity_informant.smc.oracle import Run, writer_of  # noqa: E402
import wplift  # noqa: E402 pylint: disable=wrong-import-order
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


def volatile_of(row):
    """The SID windows: $D400-$D7FF plus extra SIDs outside it."""
    return [c64.SID] + [(a, a + 0x20) for a in row["extra_sids"] if not 0xD400 <= a < 0xD800]


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
        reg, cycles = None, 0
        for i in range(n):
            if port is not None:
                mem[1] = port
            o = wholeprog.reference_call(mem, play, reg=reg, volatile=vol, max_cycles=mc, bank=bank)
            reg, cycles = o["reg"], cycles + o["cycles"]
            if o["halt"] != "return":
                return i + 1, cycles, o["halt"]
        return n, cycles, "return"

    return run


def _lifted_run(prog, mc, port=None):
    def run(mem, n):
        reg = None
        for _ in range(n):
            if port is not None:
                mem[1] = port
            reg = prog.call(mem, 0, reg=reg, max_cycles=mc)["reg"]

    return run


def _jennings_run(play, mc):
    def run(mem, n):
        s = trace.Session(mem, mc, False, None)
        for _ in range(n):
            s.call(play)

    return run


class _Records(c64._Host):  # pylint: disable=protected-access
    """A machine run keeping each call's RAM as its change from the previous record."""

    base = None

    def _ram(self):
        cur = self.ram.copy()
        prev, self.base = self.base, cur
        if self.cur != "call" or prev is None:
            return cur.tobytes()
        idx = np.flatnonzero(cur != prev)
        return idx, cur[idx]

    def split(self):
        """``(init records, call records, calls)``, the call records a generator."""
        snaps = self.snaps
        inits = [
            {"halt": h, "reg": r, "log": lg, "ram": m} for p, h, r, lg, m in snaps if p != "call"
        ]

        def calls():
            ram = bytearray(inits[-1]["ram"])
            arr = np.frombuffer(ram, np.uint8)
            for p, h, r, lg, delta in snaps:
                if p == "call":
                    arr[delta[0]] = delta[1]
                    yield {"halt": h, "reg": r, "log": lg, "ram": ram}

        return inits, calls(), sum(s[0] == "call" for s in snaps)


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


def _lifted_row(prog, lift_ms, bad):
    return {
        "lift_ms": lift_ms,
        "stats": prog.stats,
        "exact": bad is None,
        "mismatch": bad,
        "deopts": sum(prog.deopts.values()),
    }


def _banked(row, img, mc, ev):
    """The bank-aware lift of a PSID song: one machine run for facts, records and cells."""
    init, play = row["init"], row["play"]
    ports = {"init": iomap(init), "call": iomap(play)}
    t = time.perf_counter()
    host = _Records(img, ntsc=row["clock"] == "NTSC")
    host.drive([(play, 0)] * row["calls"], [(init, row["song"] - 1)], mc, ports)
    inits, records, n = host.split()
    facts = host.facts()
    row["mode"], row["bank_evidence"] = "banked", ev
    row["facts_ms"] = 1e3 * (time.perf_counter() - t)
    cls = host.cls
    row |= _label_row("calls", Run(cls.cells, cls.unclassified, True, cls.first), n, t)
    last = inits[-1]
    row |= {"init_halt": last["halt"], "calls_run": n}
    if last["halt"] != "return":
        return row | {"skip": "init " + last["halt"]}, None
    t = time.perf_counter()
    prog = wholeprog.lift(last["ram"], [play], [c64.SID], facts, bank=_bank())
    lift_ms = 1e3 * (time.perf_counter() - t)
    t = time.perf_counter()
    bad = c64.check(prog, last["ram"], last["reg"], records, ports["call"], mc)
    row |= _lifted_row(prog, lift_ms, bad) | {"check_ms": 1e3 * (time.perf_counter() - t)}
    row["configs"] = sorted({b for bs in facts.banks.values() for b in bs})
    row["returned"] = n == row["calls"] and host.snaps[-1][1] == "return"
    port, vol = ports["call"], [c64.SID]
    t_ref, (nr, cycles, _) = _timed(
        _ref_run(play, vol, mc, _bank(), port), bytearray(last["ram"]), n
    )
    t_lift, _ = _timed(_lifted_run(prog, mc, port), bytearray(last["ram"]), nr)
    row["timing"] = _timing(nr, cycles, t_ref, t_lift, None)
    return row, {"state": _diff(img, last["ram"]), "reg": list(last["reg"]), "facts": facts}


def psid_song(row, img, mc, cap, defer=False):
    """A PSID song with a play routine: ``(row, facts record or None)``; ``defer`` returns a
    banked song unrun (``deferred``) so a later resumed run lifts it."""
    init, play, a = row["init"], row["play"], row["song"] - 1
    vol = volatile_of(row)
    mem, r = flat_init(img, row, mc)
    phases = [(img, trace.collect(img, lambda s: s.call(init, a), mc), iomap(init))]
    row |= {"init_halt": r["halt"], "init_cycles": r["cycles"]}
    if r["halt"] != "return":
        ev = bank_evidence(phases)
        if ev:
            return (row | {"deferred": True}, None) if defer else _banked(row, img, mc, ev)
        return row | {"mode": "flat", "bank_evidence": ev, "skip": "init " + r["halt"]}, None
    t_ref, (n, cycles, halt) = _timed(_ref_run(play, vol, mc), bytearray(mem), row["calls"])
    t = time.perf_counter()
    facts = trace.collect(mem, lambda s: [s.call(play) for _ in range(n)], mc)
    facts_ms = 1e3 * (time.perf_counter() - t)
    phases.append((mem, facts, iomap(play)))
    ev = bank_evidence(phases)
    if ev:
        return (row | {"deferred": True}, None) if defer else _banked(row, img, mc, ev)
    row |= {
        "mode": "flat",
        "bank_evidence": ev,
        "calls_run": n,
        "returned": halt == "return",
        "facts_ms": facts_ms,
    }
    t = time.perf_counter()
    prog = wholeprog.lift(mem, [play], vol, facts)
    lift_ms = 1e3 * (time.perf_counter() - t)
    t = time.perf_counter()
    bad = wplift.check(mem, prog, [(play, 0)] * n, vol, progress=False)
    row |= _lifted_row(prog, lift_ms, bad) | {"check_ms": 1e3 * (time.perf_counter() - t)}
    t_lift, _ = _timed(_lifted_run(prog, mc), bytearray(mem), n)
    t_jen, _ = _timed(_jennings_run(play, mc), bytearray(mem), n)
    row["timing"] = _timing(n, cycles, t_ref, t_lift, t_jen)
    row |= label("calls", img, row, min(n, cap or n), mc)
    return row, {"state": _diff(img, mem), "reg": list(r["reg"]), "facts": facts}


def label(oracle, img, row, n, mc):
    """The scenario-cell row fields of a song's first ``n`` play calls (``calls``: play called
    as sidplay does) or interrupt-driven frames (``frames``), on the vagrant-moses machine."""
    t = time.perf_counter()
    init = (row["init"], row["song"] - 1)
    o = MachineOracle(img, ntsc=row["clock"] == "NTSC")
    if oracle == "calls":
        ports = {"init": iomap(row["init"]), "call": iomap(row["play"])}
        run = o.run([(row["play"], 0)] * n, init=[init], call_cycles=mc, ports=ports)
    else:
        run = o.run([], init=[init], frames=n, irq_setup=True, call_cycles=mc, index_frames=True)
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


class _IrqScan(MachineOracle):
    """An interrupt-driven run recording, after init, the IRQ path and interrupts taken, and
    whether the main context ever stores outside page 1 or touches I/O; the decode hook is
    dropped once it has, interrupts then being counted from the machine's events."""

    def __init__(self, image, ntsc=False):
        super().__init__(image, ntsc=ntsc)
        self.irqs, self.vector, self.ctx = 0, None, []
        self.main = Counter()
        self.cpf, self.t0 = mm.video(ntsc).cycles_per_frame, None

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

    def scan(self, init, frames, mc):
        """Run ``init`` then ``frames`` interrupt-driven frames; the ``irq`` row field."""
        self.run([], init=[init], frames=frames, irq_setup=True, call_cycles=mc)
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


def irq_song(row, img, mc, cap):
    """An interrupt-driven song on the machine: its IRQ facts over all ``calls`` frames, its
    cells over the first ``cap`` (0: all)."""
    row["skip"] = "irq-driven"
    if row["kind"] == "RSID" and row["flags"] & 2:
        return row | {"skip": "needs the C64 BASIC ROM"}, None
    t = time.perf_counter()
    o = _IrqScan(img, ntsc=row["clock"] == "NTSC")
    row["irq"] = o.scan((row["init"], row["song"] - 1), row["calls"], mc)
    row |= {"calls_run": o.irqs, "facts_ms": 1e3 * (time.perf_counter() - t)}
    return row | label("frames", img, row, min(row["calls"], cap or row["calls"]), mc), None


def run_song(path, row, mc, cap=0, defer=()):
    """``(row, compressed facts line or None)`` of one planned song."""
    row = dict(row)
    try:
        if "skip" in row:
            return row, None
        img = sidfile(Path(path).read_bytes())[1]
        if row["kind"] == "RSID" or not row["play"]:
            if "irq" in defer:
                return row | {"deferred": True}, None
            row, rec = irq_song(row, img, mc, cap)
        else:
            row, rec = psid_song(row, img, mc, cap, "banked" in defer)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {**row, "error": repr(e)[:300]}, None
    if rec is None:
        return row, None
    rec = {"path": row["path"], "song": row["song"], "mode": row["mode"], **rec}
    rec["facts"] = rec["facts"].to_dict()
    row["has_facts"] = True
    return row, zstandard.ZstdCompressor().compress((json.dumps(rec) + "\n").encode())


def _worker(lengths_path):
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
    if r.get("exact") is False and r["mismatch"][1] != "no-return":
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
        "no_return": sum(r["mismatch"] is not None and r["mismatch"][1] == "no-return" for r in ok),
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
    ap.add_argument("--call-timeout", type=float, default=0.5, help="seconds per call/frame")
    ap.add_argument("--label-calls", type=int, default=1000, help="labelled calls/frames (0: all)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--sample", type=int, default=0, help="random tunes (0: all)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--defer",
        type=lambda v: tuple(v.split(",")),
        default=(),
        help="banked,irq: leave those songs for a later run",
    )
    args = ap.parse_args(argv)
    root, out = Path(args.root), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows_path, facts_path = out / "rows.jsonl", out / "facts.jsonl.zst"
    lengths = args.songlengths or root / "DOCUMENTS" / "Songlengths.md5"
    paths = sorted(root.rglob("*.sid"))
    if args.sample:
        paths = random.Random(args.seed).sample(paths, min(args.sample, len(paths)))
    t0 = time.perf_counter()
    rows = _read_rows(rows_path)
    fkeys = _fact_keys(facts_path)
    done = {k for k, r in rows.items() if not r.get("has_facts") or k in fkeys}
    plans = []

    def planned(a, res):
        if not isinstance(res, list):
            err = "timeout" if res is None else repr(res)[:300]
            res = [{"path": a[1], "song": 0, "calls": 0, "error": "plan " + err}]
        plans.extend((a[0], r) for r in res)

    with ProcessPool(max_workers=args.jobs, initializer=_worker, initargs=(str(lengths),)) as pool:
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
            timeouts = [args.timeout + args.call_timeout * r["calls"] for _, r in todo]
            jobs = [(p, r, args.max_cycles, args.label_calls, args.defer) for p, r in todo]
            _pool_map(pool, run_song, jobs, timeouts, "corpus", ran)
    s = summary(list(rows.values()))
    s["wall_s"] = time.perf_counter() - t0
    (out / "summary.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    (out / "summary.md").write_text(markdown(s), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
