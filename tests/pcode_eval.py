"""A small evaluator for pypcode translations: one 6510 instruction over many machine states.

Each byte of a space holds one value per lane (a numpy array); a lane runs the ops its own
branches take. Branches within an instruction go forward, so one pass over the ops in order
runs every lane, each op writing only the lanes standing at it.
"""

from collections import defaultdict

import numpy as np


def MASK(size):
    return np.uint64((1 << (8 * size)) - 1)


def signed(x, size):
    """``x`` (``size`` bytes) as a signed int64 array."""
    x = np.asarray(x, np.uint64)
    top = np.uint64(1 << (8 * size - 1))
    return (x ^ top).astype(np.int64) - np.int64(top)


def _bin(name, x, y, insize):
    sx, sy = signed(x, insize), signed(y, insize)
    top = 1 << (8 * insize)
    shift = np.minimum(y, np.uint64(63))
    ops = {
        "INT_ADD": lambda: x + y,
        "INT_SUB": lambda: x - y,
        "INT_AND": lambda: x & y,
        "INT_OR": lambda: x | y,
        "INT_XOR": lambda: x ^ y,
        "INT_LEFT": lambda: np.where(y < 64, x << shift, 0),
        "INT_RIGHT": lambda: np.where(y < 64, x >> shift, 0),
        "INT_SRIGHT": lambda: sx >> shift.astype(np.int64),
        "INT_EQUAL": lambda: x == y,
        "INT_NOTEQUAL": lambda: x != y,
        "INT_LESS": lambda: x < y,
        "INT_LESSEQUAL": lambda: x <= y,
        "INT_SLESS": lambda: sx < sy,
        "INT_SLESSEQUAL": lambda: sx <= sy,
        "INT_CARRY": lambda: x + y >= top,
        "INT_SCARRY": lambda: ~((-top // 2 <= sx + sy) & (sx + sy < top // 2)),
        "INT_SBORROW": lambda: ~((-top // 2 <= sx - sy) & (sx - sy < top // 2)),
        "BOOL_AND": lambda: x & y,
        "BOOL_OR": lambda: x | y,
        "BOOL_XOR": lambda: x ^ y,
        "INT_MULT": lambda: x * y,
    }
    return np.asarray(ops[name](), np.uint64)


class Machine:
    """Byte-addressed spaces of ``lanes`` states; ``run`` executes the ops of one translated
    instruction on each."""

    def __init__(self, lanes):
        self.lanes = lanes
        self.spaces = defaultdict(lambda: defaultdict(lambda: np.zeros(lanes, np.uint64)))

    def read(self, vn):
        space, off, size = vn.space.name, vn.offset, vn.size
        if size >= 8:
            raise NotImplementedError("varnodes of 8 bytes or more")
        if space == "const":
            return np.full(self.lanes, np.uint64(off) & MASK(size))
        mem = self.spaces[space]
        out = np.zeros(self.lanes, np.uint64)
        for i in range(size):
            out |= np.asarray(mem[off + i], np.uint64) << np.uint64(8 * i)
        return out

    def write(self, vn, value, act):
        mem = self.spaces[vn.space.name]
        value = np.asarray(value, np.uint64)
        for i in range(vn.size):
            byte = value >> np.uint64(8 * i) & np.uint64(0xFF)
            mem[vn.offset + i] = np.where(act, byte, mem[vn.offset + i])

    def run(self, ops):
        at = np.zeros(self.lanes, np.int64)
        for i, op in enumerate(ops):
            act = at == i
            if not act.any():
                continue
            name = op.opcode.name
            ins = op.inputs
            at[act] = i + 1
            if name in ("BRANCH", "CBRANCH"):
                to = i + int(signed(ins[0].offset, 4))
                if to <= i:
                    raise NotImplementedError("a backward branch")
                at[act & (name == "BRANCH" or self.read(ins[1]) != 0)] = to
                continue
            if name == "IMARK":
                continue
            out = op.output
            if name in ("COPY", "INT_ZEXT"):
                v = self.read(ins[0])
            elif name == "INT_SEXT":
                v = signed(self.read(ins[0]), ins[0].size).astype(np.uint64)
            elif name == "BOOL_NEGATE":
                v = self.read(ins[0]) ^ np.uint64(1)
            elif name == "INT_NEGATE":
                v = ~self.read(ins[0])
            elif name == "INT_2COMP":
                v = np.uint64(0) - self.read(ins[0])
            elif name == "SUBPIECE":
                v = self.read(ins[0]) >> (np.uint64(8) * self.read(ins[1]))
            else:
                v = _bin(name, self.read(ins[0]), self.read(ins[1]), ins[0].size)
            self.write(out, v & MASK(out.size), act)
        return at
