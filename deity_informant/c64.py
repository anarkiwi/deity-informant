"""C64 environment helpers for driving playroutines in :class:`PcodeVM`.

``PcodeVM`` already models the C64's VIC/CIA/SID volatile IO; this adds the
surrounding machine facts a host needs to enter a tune faithfully: power-on
RAM, installed interrupt-vector discovery, and the KERNAL IRQ-return stub.
"""

from __future__ import annotations

IRQ_VEC = (0x0314, 0x0315)  # CINV (KERNAL A/X/Y-save ABI)
NMI_VEC = (0x0318, 0x0319)  # NMINV
HW_IRQ_VEC = (0xFFFE, 0xFFFF)  # hardware IRQ/BRK vector

# KERNAL IRQ-return stub for no-ROM CINV handlers: $EA31 -> $EA81 pulls Y/X/A, RTI.
_STUBS = (
    (0xEA31, bytes((0x4C, 0x81, 0xEA))),
    (0xEA81, bytes((0x68, 0xA8, 0x68, 0xAA, 0x68, 0x40))),
)


def poweron_ram():
    """C64 power-on RAM fill (libsidplayfp ``SystemRAMBank::reset``), as bytes.

    Each 16 KiB block alternates 0x00/0xFF with 4-byte stripes of the opposite
    value every 8 bytes from offset 2. Reads of never-written RAM see these on
    hardware; a zero fill diverges from the sidplayfp oracle.
    """
    ram = bytearray(0x10000)
    byte = 0x00
    for j in range(0, 0x10000, 0x4000):
        ram[j : j + 0x4000] = bytes((byte,)) * 0x4000
        byte ^= 0xFF
        stripe = bytes((byte,)) * 4
        for i in range(0x02, 0x4000, 0x08):
            ram[j + i : j + i + 4] = stripe
    return bytes(ram)


def read_vector(mem, lo):
    """Little-endian 16-bit vector at ``lo`` (``lo``/``lo+1``)."""
    return mem[lo] | (mem[(lo + 1) & 0xFFFF] << 8)


def installed_handler(mem, written, img):
    """Installed interrupt handler ``(addr, uses_kernal_cinv)``, or ``None``.

    Prefers a vector actually written (CINV ``$0314`` / NMINV ``$0318`` /
    hardware ``$FFFE``), else a CINV lifted from the load image. ``written`` is
    the observed write-address set; ``img`` the ``(lo, hi)`` load-image bounds.
    """
    for pair, kernal in ((IRQ_VEC, True), (HW_IRQ_VEC, False), (NMI_VEC, False)):
        if pair[0] in written or pair[1] in written:
            return (read_vector(mem, pair[0]), kernal)
    lo, hi = img
    if lo <= IRQ_VEC[0] < hi and lo <= IRQ_VEC[1] < hi:
        civ = read_vector(mem, IRQ_VEC[0])
        return (civ, True) if civ else None
    return None


def install_kernal_irq_stubs(vm):
    """Write the KERNAL IRQ-return stub ($EA31->$EA81, pull Y/X/A, RTI) into ``vm``."""
    for addr, code in _STUBS:
        vm.mem[addr : addr + len(code)] = code
