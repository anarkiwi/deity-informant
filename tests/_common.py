"""Shared test helpers: image builders, register load, and P-register unpack.

Not collected by pytest (no ``test_`` prefix); imported as ``import _common as H``
(``tests/`` is on ``sys.path`` via the conftest path insert).
"""

PC = 0x0800  # a code address clear of zero page / I/O for single-step fuzzing


def image(prog, org=0x1000, data=None):
    """A zeroed 64 KiB image with ``prog`` at ``org`` and optional ``data`` cells."""
    m = bytearray(0x10000)
    m[org : org + len(prog)] = prog
    for a, v in (data or {}).items():
        m[a] = v
    return m


def flags(p):
    """Unpack a P byte to ``(C, Z, I, D, V, N)``."""
    return (p & 1, (p >> 1) & 1, (p >> 2) & 1, (p >> 3) & 1, (p >> 6) & 1, (p >> 7) & 1)


def mk_p(rng):
    """Random P byte with D forced 0 (no decimal on the C64) and bit 5 set."""
    b = rng.integers(0, 2, 6)
    return int(b[0] | (b[1] << 1) | (b[2] << 2) | 0x20 | (b[4] << 6) | (b[5] << 7))


def load_regs(vm, a=0, x=0, y=0, sp=0xFF, p=0x20):
    """Load A/X/Y/SP and the six flag registers into ``vm`` from a P byte."""
    r = vm.reg
    r[0], r[1], r[2], r[3] = a, x, y, sp
    r[8], r[9], r[10], r[11], r[13], r[14] = flags(p)


def arch_state(vm):
    """Architectural state ``(A, X, Y, SP, C, Z, I, D, V, N)`` from ``vm``."""
    r = vm.reg
    return (r[0], r[1], r[2], r[3], r[8], r[9], r[10], r[11], r[13], r[14])
