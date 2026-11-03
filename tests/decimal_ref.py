"""NMOS 6502 ADC, SBC and ARR as Bruce Clark's "Decimal Mode" tutorial states them.

Each returns ``(result, N, V, Z, C)``. The binary and decimal forms agree with every
SingleStepTests 6502 case of $69, $E9 and $6B (checked by vagrant-moses).
"""


def _signed(x):
    return x - 256 if x & 0x80 else x


def adc(a, b, c, d):
    total = a + b + c
    z = int(total & 0xFF == 0)
    if not d:
        r = total & 0xFF
        return r, r >> 7, int(((a ^ r) & (b ^ r) & 0x80) != 0), z, total >> 8
    al = (a & 0xF) + (b & 0xF) + c
    if al >= 0xA:
        al = ((al + 6) & 0xF) + 0x10
    s = (a & 0xF0) + (b & 0xF0) + al
    signed = _signed(a & 0xF0) + _signed(b & 0xF0) + al
    n, v = (s >> 7) & 1, int(not -128 <= signed <= 127)
    if s >= 0xA0:
        s += 0x60
    return s & 0xFF, n, v, z, int(s >= 0x100)


def sbc(a, b, c, d):
    diff = a - b - (1 - c)
    r = diff & 0xFF
    flags = (r >> 7, int(((a ^ b) & (a ^ r) & 0x80) != 0), int(r == 0), int(diff >= 0))
    if not d:
        return (r, *flags)
    al = (a & 0xF) - (b & 0xF) + c - 1
    if al < 0:
        al = ((al - 6) & 0xF) - 0x10
    s = (a & 0xF0) - (b & 0xF0) + al
    if s < 0:
        s -= 0x60
    return (s & 0xFF, *flags)


def arr(a, imm, c, d):
    t = a & imm
    r = (t >> 1) | (c << 7)
    n, z, v = r >> 7, int(r == 0), ((t ^ r) >> 6) & 1
    if not d:
        return r, n, v, z, (r >> 6) & 1
    if (t & 0xF) + (t & 1) > 5:
        r = (r & 0xF0) | ((r + 6) & 0xF)
    carry = int((t & 0xF0) + (t & 0x10) > 0x50)
    if carry:
        r = (r & 0xF) | ((r + 0x60) & 0xF0)
    return r, n, v, z, carry


def rra(a, m, c, d):
    """``(A, N, V, Z, C, memory)`` of ROR memory then ADC."""
    rotated = (m >> 1) | (c << 7)
    return (*adc(a, rotated, m & 1, d), rotated)


def isc(a, m, c, d):
    """``(A, N, V, Z, C, memory)`` of INC memory then SBC."""
    inc = (m + 1) & 0xFF
    return (*sbc(a, inc, c, d), inc)
