"""Address masks: boolean arrays over the 64K address space and the runs of True in them."""

from __future__ import annotations

import numpy as np

#: The size of the 6502's address space.
SPACE = 0x10000


def runs(mask):
    """``[(start, length)]`` of the runs of True in ``mask``."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], np.asarray(mask, np.int8), [0]))))
    return [(int(a), int(b - a)) for a, b in zip(edges[::2], edges[1::2])]
