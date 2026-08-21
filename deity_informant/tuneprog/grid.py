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


def unwrap(cyc):
    """A monotone cycle column from one that wrapped at ``2**32``."""
    c = np.asarray(cyc, dtype=np.int64)
    if c.size == 0:
        return c
    return c[0] + np.cumsum(np.diff(c, prepend=c[0]) % WRAP)


def frames(cyc, first, cycles_per_frame):
    """Which frame each cycle falls in, counting frame 0 from ``first``."""
    return (np.asarray(cyc, dtype=np.int64) - int(first)) // int(cycles_per_frame)


def grid(frame, reg, val, nframes, reg_count=SID_REGS, nibble=PW_HI):
    """Forward-filled ``nframes x reg_count`` grid of writes already framed.

    ``frame`` is each write's frame index (ascending; negative before frame 0, so
    those writes are the baseline). A row is the register file after every write
    its frame holds, which is what a sampler reading once a frame sees.
    """
    rows = np.zeros((nframes, reg_count), dtype=np.uint8)
    ks = np.arange(nframes)
    frame, reg, val = (np.asarray(x) for x in (frame, reg, val))
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


def trace_grid(trace, nframes=None, reg_count=SID_REGS):
    """The tracer's SID write log as a per-frame grid.

    The frame grid is the tracer's own: tick 0 starts where init ended and every
    tick is ``cycles_per_tick`` long, so a tick that overruns lands its late
    writes in the next frame exactly as the hardware would.
    """
    log = trace.wlog
    cyc = unwrap(log["cyc"])
    addr = np.asarray(log["addr"], dtype=np.int64) - SID_BASE
    keep = (addr >= 0) & (addr < reg_count)
    first = trace.meta.get("cycles_init")
    if first is None:  # a trace older than the column: tick 0's own first write
        first = int(cyc[keep & (np.asarray(log["call"], dtype=np.int64) == 0)][0])
    n = trace.meta["calls"] if nframes is None else nframes
    return grid(
        frames(cyc[keep], first, trace.meta["entry"]["cycles_per_tick"]),
        addr[keep],
        np.asarray(log["val"])[keep],
        n,
        reg_count,
    )


def sidtrace_grid(rows, nframes=None, chip=0, reg_count=SID_REGS):
    """A sidtrace CSV's rows as a per-frame grid on the CSV's own interrupt clock.

    Each row carries the cycles since its frame's interrupt was raised, so
    ``cycle - offset`` is that raise and the earliest is frame 0 -- the first play
    call, with the init writes before it as the baseline.
    """
    # pylint: disable=import-outside-toplevel,import-error
    from pysidtracker.oracle import sidtrace_cadence

    rows = [r for r in rows if r.chip == chip and 0 <= r.reg < reg_count]
    raised = [
        r.cycle - (r.since_video_irq if r.since_video_irq is not None else r.since_cia_irq)
        for r in rows
        if r.since_video_irq is not None or r.since_cia_irq is not None
    ]
    f = frames([r.cycle for r in rows], min(raised), sidtrace_cadence(rows, chip=chip))
    n = int(f[-1]) + 1 if nframes is None else nframes
    return grid(f, [r.reg for r in rows], [r.value for r in rows], n, reg_count)


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
