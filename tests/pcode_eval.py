"""A small evaluator for pypcode translations: enough to run one 6510 instruction."""

from collections import defaultdict


def MASK(size):
    return (1 << (8 * size)) - 1


def signed(x, size):
    return x - (1 << (8 * size)) if x >> (8 * size - 1) else x


def _bin(name, x, y, size, insize):
    sx, sy = signed(x, insize), signed(y, insize)
    top = 1 << (8 * insize)
    return {
        "INT_ADD": x + y,
        "INT_SUB": x - y,
        "INT_AND": x & y,
        "INT_OR": x | y,
        "INT_XOR": x ^ y,
        "INT_LEFT": x << y if y < 64 else 0,
        "INT_RIGHT": x >> y if y < 64 else 0,
        "INT_SRIGHT": sx >> min(y, 63),
        "INT_EQUAL": x == y,
        "INT_NOTEQUAL": x != y,
        "INT_LESS": x < y,
        "INT_LESSEQUAL": x <= y,
        "INT_SLESS": sx < sy,
        "INT_SLESSEQUAL": sx <= sy,
        "INT_CARRY": x + y >= top,
        "INT_SCARRY": not -top // 2 <= sx + sy < top // 2,
        "INT_SBORROW": not -top // 2 <= sx - sy < top // 2,
        "BOOL_AND": x & y,
        "BOOL_OR": x | y,
        "BOOL_XOR": x ^ y,
        "INT_MULT": x * y,
    }[name]


class Machine:
    """Byte-addressed spaces; ``run`` executes the ops of one translated instruction."""

    def __init__(self):
        self.spaces = defaultdict(lambda: defaultdict(int))

    def read(self, vn):
        space, off, size = vn.space.name, vn.offset, vn.size
        if space == "const":
            return off & MASK(size)
        mem = self.spaces[space]
        return sum(mem[off + i] << (8 * i) for i in range(size))

    def write(self, vn, value):
        mem = self.spaces[vn.space.name]
        for i in range(vn.size):
            mem[vn.offset + i] = value >> (8 * i) & 0xFF

    def run(self, ops):
        i = 0
        while i < len(ops):
            op = ops[i]
            name = op.opcode.name
            ins = op.inputs
            i += 1
            if name == "IMARK":
                continue
            if name == "BRANCH":
                i += signed(ins[0].offset, 4) - 1
                continue
            if name == "CBRANCH":
                if self.read(ins[1]):
                    i += signed(ins[0].offset, 4) - 1
                continue
            out = op.output
            if name == "COPY":
                self.write(out, self.read(ins[0]))
            elif name == "INT_ZEXT":
                self.write(out, self.read(ins[0]))
            elif name == "INT_SEXT":
                self.write(out, signed(self.read(ins[0]), ins[0].size) & MASK(out.size))
            elif name in ("BOOL_NEGATE", "INT_NEGATE", "INT_2COMP"):
                x = self.read(ins[0])
                self.write(
                    out,
                    {"BOOL_NEGATE": x ^ 1, "INT_NEGATE": ~x, "INT_2COMP": -x}[name]
                    & MASK(out.size),
                )
            elif name == "SUBPIECE":
                self.write(out, self.read(ins[0]) >> (8 * self.read(ins[1])))
            else:
                x, y = self.read(ins[0]), self.read(ins[1])
                self.write(out, int(_bin(name, x, y, out.size, ins[0].size)) & MASK(out.size))
