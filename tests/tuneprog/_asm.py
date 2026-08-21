"""Illegal-aware hand-assembly helper for the tuneprog tests.

``jennings.assembler.Assembler`` drives off an mpu's ``disassemble`` table; the
stub here feeds it ``jennings.opcodes.OPCODES`` (all 256 NMOS opcodes) so tests
can write ``LAX ($FB),Y`` or ``SBX #$31`` in source form.
"""

import re

from jennings.assembler import Assembler
from jennings.opcodes import OPCODES

_MODE = {
    "zp": "zpg",
    "zpx": "zpx",
    "zpy": "zpy",
    "abs": "abs",
    "absx": "abx",
    "absy": "aby",
    "indx": "inx",
    "indy": "iny",
    "ind": "ind",
    "imm": "imm",
    "impl": "imp",
    "acc": "acc",
    "rel": "rel",
}


class _Mpu:
    byteMask = 0xFF
    BYTE_WIDTH = 8
    ADDR_WIDTH = 16
    BYTE_FORMAT = "%02x"
    ADDR_FORMAT = "%04x"
    disassemble = [(OPCODES[i][0], _MODE[OPCODES[i][1]]) for i in range(256)]


_ASM = Assembler(_Mpu())


_LABEL = re.compile(r"^([A-Za-z_]\w*):\s*(.*)$")
_WORD = re.compile(r"(?<![$\w])([<>]?)([A-Za-z_]\w*)(\s*[+-]\s*\d+)?")


class Code(bytes):
    """Assembled bytes plus the ``labels`` the source defined."""

    labels = {}


def _subst(operand, resolve):
    def rep(m):
        half, name, off = m.groups()
        if name.upper() in ("A", "X", "Y") and not half:
            return m.group(0)
        v = resolve(name) + (int(off.replace(" ", "")) if off else 0)
        if half:
            return "$%02X" % ((v & 0xFF) if half == "<" else (v >> 8))
        return "$%04X" % v

    return _WORD.sub(rep, operand)


def asm(org, *lines):
    """Assemble ``lines`` at ``org``. ``name:`` defines a label; a bare word is one.

    Two passes, so forward references work: ``asm(0x1000, "loop: DEX", "BNE loop")``.
    ``label+N``/``label-N`` and ``#<label``/``#>label`` (low/high byte) are understood;
    labels named A, X or Y are not (they read as registers).
    """
    labels = {}
    out = b""
    for final in (False, True):
        out = bytearray()
        pc = org
        for ln in lines:
            while True:
                m = _LABEL.match(ln)
                if not m:
                    break
                labels[m.group(1)] = pc
                ln = m.group(2)
            if not ln.strip():
                continue
            parts = ln.split(None, 1)
            if len(parts) > 1:
                resolve = labels.__getitem__ if final else (lambda n: 0xFFF0)
                ln = parts[0] + " " + _subst(parts[1], resolve)
            b = _ASM.assemble(ln, pc)
            out += bytes(b)
            pc += len(b)
    code = Code(out)
    code.labels = labels
    return code


def image(blocks, data=None):
    """A 64 KiB image: ``blocks`` is ``{org: bytes}``, ``data`` is ``{addr: byte}``."""
    m = bytearray(0x10000)
    for org, code in blocks.items():
        m[org : org + len(code)] = code
    for a, v in (data or {}).items():
        m[a] = v
    return m


def psid(blocks, init, play, data=None, load=0x1000, songs=1, speed=0, magic=b"PSID", clock=1):
    """A minimal PSID/RSID file wrapping ``blocks`` so ``MachineImage.from_sid`` loads it.

    ``speed`` is the header's subtune bitfield and ``clock`` its flags video
    standard (1 = PAL, 2 = NTSC).
    """
    m = image(blocks, data)
    hi = max(org + len(code) for org, code in blocks.items())
    hi = max([hi] + [a + 1 for a in (data or {}) if a >= load])
    body = bytes(m[load:hi])
    head = bytearray(0x7C)
    head[0:4] = magic
    head[4:6] = (2).to_bytes(2, "big")
    head[6:8] = (0x7C).to_bytes(2, "big")
    head[8:10] = load.to_bytes(2, "big")
    head[10:12] = init.to_bytes(2, "big")
    head[12:14] = play.to_bytes(2, "big")
    head[14:16] = songs.to_bytes(2, "big")
    head[16:18] = (1).to_bytes(2, "big")
    head[18:22] = speed.to_bytes(4, "big")
    head[0x76:0x78] = (clock << 2).to_bytes(2, "big")
    return bytes(head) + body


def sid_image(blocks, init, play, data=None, load=0x1000):
    """``MachineImage`` of a synthetic tune (see :func:`psid`)."""
    from deity_informant.tuneprog.machine import MachineImage

    return MachineImage.from_sid(psid(blocks, init, play, data, load))


def banked_out(img):
    """The same image with the KERNAL banked out ($01 HIRAM clear): a raw-vector machine.

    A raw ``$FFFE`` entry is only reachable on such a machine -- with the ROM
    mapped the 6510 takes the KERNAL's own vector and the CINV path instead.
    """
    from dataclasses import replace

    mem = bytearray(img.mem)
    mem[1] = 0x35
    return replace(img, mem=bytes(mem))


def trace_prog(
    blocks,
    init,
    play,
    calls=2,
    data=None,
    load=0x1000,
    cycles=19656,
    kind="sub",
    kernal=False,
    **kw,
):
    """Trace a synthetic tune; returns ``(Trace, Tracer)``."""
    from deity_informant.tuneprog.machine import Entry
    from deity_informant.tuneprog.trace import Tracer

    img = sid_image(blocks, init, play, data, load)
    if kind == "irq" and not kernal:
        img = banked_out(img)
    t = Tracer(img, Entry(kind, play, cycles, "test", kernal), **kw)
    t.run_init()
    t.run_calls(calls)
    return t.trace(), t
