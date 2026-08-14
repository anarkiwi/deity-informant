"""C64 environment helpers for driving playroutines in :class:`PcodeVM`.

``PcodeVM`` already models the C64's VIC/CIA/SID volatile IO; this adds the
machine facts a host needs to enter a tune: power-on RAM, installed
interrupt-vector discovery, and the ROM-free KERNAL IRQ epilogue.
"""

from __future__ import annotations

import hashlib
import re
import struct

IRQ_VEC = (0x0314, 0x0315)  # CINV (KERNAL A/X/Y-save ABI)
NMI_VEC = (0x0318, 0x0319)  # NMINV
HW_IRQ_VEC = (0xFFFE, 0xFFFF)  # hardware IRQ/BRK vector

# KERNAL IRQ epilogue for no-ROM handlers: PLA;TAY;PLA;TAX;PLA;RTI ($EA81, NMI $FEBC).
_EPILOGUE = bytes((0x68, 0xA8, 0x68, 0xAA, 0x68, 0x40))
_STUBS = (
    (0xEA31, b"\xea" * (0xEA81 - 0xEA31)),  # handler body: any exit entry NOPs to $EA81
    (0xEA81, _EPILOGUE),
    (0xFEBC, _EPILOGUE),
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


def installed_vector(mem, written, img):
    """Installed interrupt vector ``(vector addr, uses_kernal_cinv)``, or ``None``.

    Prefers a vector actually written (CINV ``$0314`` / NMINV ``$0318`` /
    hardware ``$FFFE``), else a CINV lifted from the load image. ``written`` is
    the observed write-address set; ``img`` the ``(lo, hi)`` load-image bounds.
    """
    for pair, kernal in ((IRQ_VEC, True), (HW_IRQ_VEC, False), (NMI_VEC, False)):
        if pair[0] in written or pair[1] in written:
            return (pair[0], kernal)
    lo, hi = img
    if lo <= IRQ_VEC[0] < hi and lo <= IRQ_VEC[1] < hi and read_vector(mem, IRQ_VEC[0]):
        return (IRQ_VEC[0], True)
    return None


def installed_handler(mem, written, img):
    """Installed interrupt handler ``(addr, uses_kernal_cinv)``, or ``None``."""
    found = installed_vector(mem, written, img)
    return None if found is None else (read_vector(mem, found[0]), found[1])


def install_kernal_irq_stubs(vm, img=(0, 0)):
    """Write the KERNAL IRQ epilogue ($EA31 body NOPs to $EA81: pull Y/X/A, RTI).

    Raises ``ValueError`` when the load image ``img`` claims one: the tune's own
    bytes live there and the host cannot fake the machine under them.
    """
    lo, hi = img
    for addr, code in _STUBS:
        if lo < addr + len(code) and addr < hi:
            raise ValueError(
                "load image $%04X-$%04X covers the KERNAL IRQ epilogue at $%04X" % (lo, hi, addr)
            )
        vm.mem[addr : addr + len(code)] = code


def _psid_body(data):
    """``(load, body)`` of a PSID/RSID file, honouring an in-body load address."""
    if data[:4] not in (b"PSID", b"RSID"):
        raise ValueError("not a SID file")
    off, load = struct.unpack(">H", data[6:8])[0], struct.unpack(">H", data[8:10])[0]
    body = data[off:]
    if load == 0:
        load, body = body[0] | (body[1] << 8), body[2:]
    return load, body[: 0x10000 - load]


def load_psid(data):
    """Parse a PSID/RSID image: ``(mem64k, load, init, play)``.

    Subtune count/default live in the header; see :func:`psid_songs`.
    """
    load, body = _psid_body(data)
    init, play = struct.unpack(">HH", data[10:14])
    mem = bytearray(0x10000)
    mem[load : load + len(body)] = body
    return mem, load, init, play


def psid_image(data):
    """``(lo, hi)`` bounds of the cells a PSID/RSID file loads."""
    load, body = _psid_body(data)
    return load, load + len(body)


def psid_songs(data):
    """``(songs, startsong)`` from the PSID header (both 1-based)."""
    songs, startsong = struct.unpack(">HH", data[0x0E:0x12])
    return songs, startsong


def song_lengths(text):
    """``{md5: [seconds per subtune]}`` parsed from HVSC ``Songlengths.md5``."""
    out = {}
    for line in text.splitlines():
        if "=" not in line or not re.match(r"^[0-9a-f]{32}=", line):
            continue
        md5, times = line.split("=", 1)
        secs = []
        for t in times.split():
            mm, _, ss = t.partition(":")
            secs.append(int(mm) * 60 + int(float(ss)))
        out[md5] = secs
    return out


def song_seconds(data, lengths, subtune=None):
    """Full ``Songlengths`` duration of one subtune (0-based; default startsong).

    ``lengths`` is :func:`song_lengths` output; None when the tune is unknown.
    Short evidence windows under-trace playroutines -- always use full length.
    """
    secs = lengths.get(hashlib.md5(data).hexdigest())
    if not secs:
        return None
    if subtune is None:
        subtune = psid_songs(data)[1] - 1
    return secs[subtune] if 0 <= subtune < len(secs) else secs[0]
