"""Address masks: boolean arrays over the 64K address space and the runs of True in them."""

from __future__ import annotations

import numpy as np

#: The size of the 6502's address space.
SPACE = 0x10000


def runs(bits):
    """``[(start, length)]`` of the runs of True in ``bits``."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], np.asarray(bits, np.int8), [0]))))
    return [(int(a), int(b - a)) for a, b in zip(edges[::2], edges[1::2])]


def mask(spans=(), closed=False, at=(), out=None):
    """``out`` (a new mask when None) with the bytes of ``spans`` (``[(lo, hi)]``, ``hi``
    exclusive, inclusive when ``closed``) and the addresses ``at`` set."""
    out = np.zeros(SPACE, dtype=bool) if out is None else out
    for lo, hi in spans:
        out[lo : hi + closed] = True
    out[at if isinstance(at, np.ndarray) else np.fromiter(at, np.int64)] = True
    return out
