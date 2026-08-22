"""Per-frame SID register grids, every write attributed to a frame by its cycle.

A tick's writes are not instantaneous, so a grid by call index is not the grid a
sampler read. The tracer's ``wlog`` and a sidtrace CSV both carry every write's
cycle, and both frame by one rule: the interrupt period the cycle falls in.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SID_REGS = 0x19  # $D400..$D418
SID_BASE = 0xD400
PW_HI = (0x03, 0x0A, 0x11)  # the SID ignores the top nibble of pulse-width high
WRAP = 1 << 32  # the write log's cycle column is uint32
INIT_CALL = WRAP - 1  # and its call column holds this for the init phase


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
    """The sidtrace oracle's rows for ``tune_path``, rendering into the cache once."""
    # pylint: disable=import-outside-toplevel,import-error
    from pysidtracker.oracle import SIDTRACE_IMAGE, read_sidtrace, run_sidtrace

    tune_path, oracle_cache = Path(tune_path), Path(oracle_cache)
    csv = oracle_cache / (tune_path.stem + ".csv.zst")
    if not csv.exists():
        run_sidtrace(tune_path, csv, seconds=seconds, image=image or SIDTRACE_IMAGE)
    return read_sidtrace(csv)


def differing(want, got, nframes=None):
    """The frames where two grids differ, over the frames both hold."""
    n = min(len(want), len(got)) if nframes is None else nframes
    return np.nonzero((np.asarray(want)[:n] != np.asarray(got)[:n]).any(axis=1))[0]
