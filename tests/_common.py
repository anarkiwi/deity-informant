"""Shared test helpers: image builders, register load, P-register unpack and one-step runs.

Not collected by pytest (no ``test_`` prefix); imported as ``import _common as H``
(``tests/`` is on ``sys.path`` via the conftest path insert).
"""

import gc
import json
from collections import Counter

from deity_informant import PcodeVM, lift
from deity_informant.lifter import STATUS_BITS, pack_status, unpack_status

PC = 0x0800  # a code address clear of zero page / I/O for single-step fuzzing


def image(prog, org=0x1000, data=None):
    """A zeroed address-space image with ``prog`` at ``org`` and optional ``data`` cells."""
    m = bytearray(0x10000)
    m[org : org + len(prog)] = prog
    for a, v in (data or {}).items():
        m[a] = v
    return m


def flags(p):
    """Unpack a P byte to ``(C, Z, I, D, V, N)``."""
    return tuple(unpack_status(p, {}).values())


def mk_p(rng):
    """Random P byte with D forced 0 (no decimal on the C64) and bit 5 set."""
    reg = dict(zip((i for i, _ in STATUS_BITS), rng.integers(0, 2, 6).tolist()))
    return pack_status(reg | {11: 0}, 0x20)


def load_regs(vm, a=0, x=0, y=0, sp=0xFF, p=0x20):
    """Load A/X/Y/SP and the six flag registers into ``vm`` from a P byte."""
    r = vm.reg
    r[0], r[1], r[2], r[3] = a, x, y, sp
    unpack_status(p, r)


def step(mem, pc, **regs):
    """A non-volatile ``PcodeVM`` over ``mem`` after one instruction at ``pc`` from ``regs``."""
    vm = PcodeVM(mem)
    vm.volatile = False
    load_regs(vm, **regs)
    vm.step(pc, {}, lift)
    return vm


def arch_state(vm):
    """Architectural state ``(A, X, Y, SP, C, Z, I, D, V, N)`` from ``vm``."""
    r = vm.reg
    return (r[0], r[1], r[2], r[3], r[8], r[9], r[10], r[11], r[13], r[14])


def basic_program(*lines, org=0x0801):
    """A tokenised C64 BASIC program at ``org`` from ``(number, tokenised text)`` lines."""
    out = bytearray()
    for number, text in lines:
        body = number.to_bytes(2, "little") + text + b"\0"
        out += (org + len(out) + 2 + len(body)).to_bytes(2, "little") + body
    return bytes(out + b"\0\0")


# 10 POKE 4096,PEEK(780)+1: POKE 54296,15 / 20 GOTO 20: tokens POKE $97, PEEK $C2, + $AA, GOTO $89
SONG_POKE = basic_program((10, b"\x974096,\xc2(780)\xaa1:\x9754296,15"), (20, b"\x8920"))


def live(names):
    """Counts of the live objects whose type is named in ``names``, after a collection."""
    gc.collect()
    return Counter(t for o in gc.get_objects() if (t := type(o).__name__) in names)


def write_rows(path, rows):
    """``rows`` written to ``path`` as JSON lines; the path as a string."""
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)
