"""Per-frame SID register grids, every write attributed to a frame by its cycle.

A tick's writes are not instantaneous, so a grid by call index is not the grid a
sampler read. The tracer's ``wlog`` and a sidtrace CSV both carry every write's
cycle, and both frame by one rule: the interrupt period the cycle falls in.
"""

from __future__ import annotations

from collections import namedtuple
from pathlib import Path

import numpy as np

SID_REGS = 0x19  # $D400..$D418
SID_BASE = 0xD400
SID_BAND = 0x400  # $D400..$D7FF decodes to the register file, mirrored every 32
PW_HI = (0x03, 0x0A, 0x11)  # the SID ignores the top nibble of pulse-width high
WRAP = 1 << 32  # the write log's cycle column is uint32
INIT_CALL = WRAP - 1  # and its call column holds this for the init phase

CTRL = (0x04, 0x0B, 0x12)
AD = (0x05, 0x0C, 0x13)
SR = (0x06, 0x0D, 0x14)
EDGE = frozenset(CTRL + AD + SR)  # stateful, edge-triggered: every write is kept
# (lo, hi, shift, mask): a register pair as one value, ``(hi << shift) | lo``
PAIRS = (
    (0x00, 0x01, 8, 0xFFFF),
    (0x07, 0x08, 8, 0xFFFF),
    (0x0E, 0x0F, 8, 0xFFFF),
    (0x02, 0x03, 8, 0x0FFF),
    (0x09, 0x0A, 8, 0x0FFF),
    (0x10, 0x11, 8, 0x0FFF),
    (0x15, 0x16, 3, 0x07FF),  # cutoff is 11 bits: 8 high, 3 low
)
LEVEL = (0x17, 0x18)  # res_route, mode_vol


def unwrap(cyc):
    """A monotone cycle column from one that wrapped at ``2**32``."""
    c = np.asarray(cyc, dtype=np.int64)
    if c.size == 0:
        return c
    return c[0] + np.cumsum(np.diff(c, prepend=c[0]) % WRAP)


def frames(cyc, first, cycles_per_frame):
    """Which frame each cycle falls in, counting frame 0 from ``first``."""
    return (np.asarray(cyc, dtype=np.int64) - int(first)) // int(cycles_per_frame)


def grid(frame, reg, val, nframes=None, reg_count=SID_REGS, nibble=PW_HI):
    """Forward-filled ``nframes x reg_count`` grid of writes already framed.

    ``frame`` is each write's frame index (ascending; negative before frame 0, so
    those writes are the baseline), and ``nframes`` defaults to one past the last
    frame written. A row is the register file after every write its frame holds.
    """
    frame, reg, val = (np.asarray(x) for x in (frame, reg, val))
    if nframes is None:
        nframes = int(frame[-1]) + 1 if frame.size else 0
    rows = np.zeros((max(nframes, 0), reg_count), dtype=np.uint8)
    ks = np.arange(nframes)
    for r in range(reg_count):
        m = reg == r
        if not m.any():
            continue
        f, v = frame[m], val[m].astype(np.uint8)
        if r in nibble:
            v = v & 0x0F
        i = np.searchsorted(f, ks, side="right") - 1
        rows[:, r] = np.where(i >= 0, v[i.clip(0)], 0)
    return rows


def sid_writes(trace, reg_count=SID_REGS):
    """``(cycle, register, value, call)`` columns of the log's register-file writes."""
    log = trace.wlog
    addr = np.asarray(log["addr"], dtype=np.int64) - SID_BASE
    keep = (addr >= 0) & (addr < reg_count)
    return (
        unwrap(log["cyc"])[keep],
        addr[keep],
        np.asarray(log["val"])[keep],
        np.asarray(log["call"], dtype=np.int64)[keep],
    )


def trace_grid(trace, nframes=None, reg_count=SID_REGS):
    """The tracer's SID write log as a per-frame grid, by cycle.

    The frame grid is the tracer's own: tick 0 starts where init ended and every
    tick is ``cycles_per_tick`` long, so a tick that overruns lands its late
    writes in the next frame exactly as the hardware would.
    """
    cyc, reg, val, call = sid_writes(trace, reg_count)
    first = trace.meta.get("cycles_init")
    if first is None:  # a trace older than the column: tick 0's own first write
        first = int(cyc[call == 0][0])
    f = frames(cyc, first, trace.meta["entry"]["cycles_per_tick"])
    return grid(f, reg, val, nframes, reg_count)


def tick_grid(trace, nframes=None, reg_count=SID_REGS):
    """The same writes in the frame of the *tick that issued them*, not their cycle.

    What the per-tick model says the register file holds at each tick boundary.
    It differs from :func:`trace_grid` exactly where a tick outlives its frame.
    """
    _cyc, reg, val, call = sid_writes(trace, reg_count)
    return grid(np.where(call == INIT_CALL, -1, call), reg, val, nframes, reg_count)


def sidtrace_clock(rows):
    """``(first raise, cycles per frame)`` from a sidtrace CSV's own interrupt column.

    One source only -- video where any row carries it, else CIA -- so origin and
    period cannot come from two clocks. Frame 0 is the earliest interrupt a write
    is attributed to: the first play call, since a driver runs init with I off.
    """
    src = "since_video_irq" if any(r.since_video_irq is not None for r in rows) else "since_cia_irq"
    have = [r for r in rows if getattr(r, src) is not None]
    timed = [r for r in rows if r.since_video_irq is not None or r.since_cia_irq is not None]
    if len(have) < len(timed):  # the rest would drop out and leave a self-consistent multiple
        raise ValueError(
            "sidtrace rows split across two interrupt sources (%d of %d on %s)"
            % (len(have), len(timed), src)
        )
    off = [getattr(r, src) for r in have]
    at = sorted({r.cycle - o for r, o in zip(have, off)})
    if len(at) < 2:
        raise ValueError("sidtrace rows carry no interrupt clock (%d raises)" % len(at))
    step = np.diff(at)
    cpf = int(round(np.median(step)))
    # every gap is whole periods (a raise no write fell in); a write further than
    # one period from its raise is a second entry writing while this one is idle
    slip = int(np.abs(step - np.round(step / cpf) * cpf).max()) if cpf > 0 else 0
    if cpf <= 0 or slip > cpf // 100:
        raise ValueError("sidtrace raises do not agree on one period (%d, slip %d)" % (cpf, slip))
    return at[0], cpf


def sidtrace_grid(
    rows, nframes=None, chip=0, reg_count=SID_REGS, first=None, cycles_per_frame=None
):
    """A sidtrace CSV's rows as a per-frame grid on the CSV's own interrupt clock.

    ``first`` and ``cycles_per_frame`` default to :func:`sidtrace_clock`; the
    writes before frame 0 are the baseline the first row fills from.
    """
    rows = [r for r in rows if r.chip == chip and 0 <= r.reg < reg_count]
    if first is None or cycles_per_frame is None:
        at, cpf = sidtrace_clock(rows)
        first = at if first is None else first
        cycles_per_frame = cpf if cycles_per_frame is None else cycles_per_frame
    f = frames([r.cycle for r in rows], first, cycles_per_frame)
    return grid(f, [r.reg for r in rows], [r.value for r in rows], nframes, reg_count)


def oracle_rows(tune_path, oracle_cache, seconds=60, image=None):
    """The sidtrace oracle's rows for ``tune_path``, rendering into the cache once.

    The render length is part of the key, because a trace is only as long as the
    render that made it: one tune asked for at two lengths is two files, and a
    shorter one can never answer a longer request. Keying on the tune alone let
    whichever caller ran first decide, which is a race under ``pytest -n auto``
    and silent whenever the short render wins.
    """
    # pylint: disable=import-outside-toplevel,import-error
    from pysidtracker.oracle import SIDTRACE_IMAGE, read_sidtrace, run_sidtrace

    tune_path, oracle_cache = Path(tune_path), Path(oracle_cache)
    csv = oracle_cache / ("%s-%ds.csv.zst" % (tune_path.stem, seconds))
    if not csv.exists():
        run_sidtrace(tune_path, csv, seconds=seconds, image=image or SIDTRACE_IMAGE)
    return read_sidtrace(csv)


def differing(want, got, nframes=None):
    """The frames where two grids differ, over the frames both hold."""
    n = min(len(want), len(got)) if nframes is None else nframes
    return np.nonzero((np.asarray(want)[:n] != np.asarray(got)[:n]).any(axis=1))[0]


def regs(addrs):
    """Register indices of absolute addresses, mirrors folded; -1 outside the file.

    The band is ``$D400..$D7FF`` (:data:`~.ir.SID_LO`/``SID_HI``), which decodes
    every 32 bytes; ``$D419..$D41F`` are read-only and have no index here.
    """
    a = np.asarray(addrs, dtype=np.int64) - SID_BASE
    r = a & 0x1F
    return np.where((a >= 0) & (a < SID_BAND) & (r < SID_REGS), r, -1)


def changes(reg, val, seed):
    """Mask of the writes that changed the register file, ``seed`` being it before.

    A write whose value the register already holds reaches no chip state, so the
    two sides of a comparison must drop it by the same rule. The file after any
    prefix holds each register's last written value, so the test is against the
    previous write to the same register.
    """
    reg, val = np.asarray(reg, np.int64), np.asarray(val, np.int64)
    keep = np.zeros(reg.shape, dtype=bool)
    for r in np.unique(reg):
        m = reg == r
        v = val[m]
        keep[m] = np.concatenate(([v[0] != seed[r]], v[1:] != v[:-1]))
    return keep


TickObs = namedtuple("TickObs", "edges values")


def reduce_tick(writes, prev=None):
    """One tick's ``(register, value)`` writes as the trackerprog observable.

    Three rules (prototype-trackerprog §2): every write to a ctrl/AD/SR register
    is kept in tick order, because the envelope generator is edge-triggered; each
    :data:`PAIRS` register pair reduces to the one 16-bit value the tick left; each
    :data:`LEVEL` register reduces to its last value the same way. A pair or level
    the tick did not write carries ``prev``'s, or is ``None`` with no ``prev``.

    Two boundaries: order *between* registers is dropped by rules 2 and 3, and
    ``writes`` are already register indices, so mirrors are folded (:func:`regs`).
    """
    edges = tuple((r, v) for r, v in writes if r in EDGE)
    last = dict(writes)
    values = []
    for i, (lo, hi, shift, mask) in enumerate(PAIRS):
        p = None if prev is None else prev.values[i]
        if lo not in last and hi not in last:
            values.append(p)
            continue
        base = 0 if p is None else p
        v = last.get(lo, base & ((1 << shift) - 1))
        h = last.get(hi, base >> shift)
        values.append(((h << shift) | (v & ((1 << shift) - 1))) & mask)
    for i, r in enumerate(LEVEL):
        values.append(last.get(r, None if prev is None else prev.values[len(PAIRS) + i]))
    return TickObs(edges, tuple(values))


def value_index(reg):
    """The :func:`reduce_tick` value column a register index folds into, or ``None``."""
    for i, (lo, hi, _shift, _mask) in enumerate(PAIRS):
        if reg in (lo, hi):
            return i
    return len(PAIRS) + LEVEL.index(reg) if reg in LEVEL else None


def reduce_run(frame, reg, val, nframes=None):
    """The same reduction over a whole run of already-framed writes.

    ``(levels, edges)``: ``levels`` is ``nframes x (len(PAIRS) + len(LEVEL))``, the
    forward-filled :func:`grid` composed pair by pair (an unwritten register is 0,
    where :func:`reduce_tick` with no ``prev`` has ``None``); ``edges`` is one
    ordered ``(register, value)`` tuple per frame.
    """
    frame, reg, val = (np.asarray(x) for x in (frame, reg, val))
    if nframes is None:
        nframes = int(frame[-1]) + 1 if frame.size else 0
    nframes = max(nframes, 0)
    rows = grid(frame, reg, val, nframes)
    levels = np.zeros((nframes, len(PAIRS) + len(LEVEL)), dtype=np.uint16)
    for i, (lo, hi, shift, mask) in enumerate(PAIRS):
        lov = rows[:, lo].astype(np.uint16) & ((1 << shift) - 1)
        levels[:, i] = ((rows[:, hi].astype(np.uint16) << shift) | lov) & mask
    levels[:, len(PAIRS) :] = rows[:, LEVEL]
    m = np.isin(reg, np.fromiter(EDGE, np.int64))
    f, r, v = frame[m], reg[m].tolist(), val[m].tolist()
    at = np.searchsorted(f, np.arange(nframes + 1))
    return levels, [tuple(zip(r[a:b], v[a:b])) for a, b in zip(at[:-1], at[1:])]
