"""NMOS dummy reads at interrupt-acknowledge registers: di-pcode's reference vs jennings.

Each case reads ``$DC0D`` (CIA 1 ICR) or ``$D019`` (VIC IRQ status), where a read has a side
effect. Both make the instructions' loads; jennings' ``cpu_gen`` makes none of di-pcode's dummy
reads (indexed page-cross, implied operand, taken-branch next and fixup, ``RTS`` pulled address).
"""

import pytest
from jennings.devices.mpu6502 import NativeMPU
from jennings.memory import NativeMemory

from deity_informant import NATIVE, wholeprog
from deity_informant.smc.asm import assemble

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

ACK = (0xDC0D, 0xD019)


def _case(kind, t):
    """``(source, reads the reference makes at t, whether jennings makes them too)``."""
    page, lo = t & 0xFF00, t & 0xFF
    near = page | 0xF0
    return {
        "load": (f"LDA ${t:04X}\nRTS", [t], True),
        "indexed": (f"LDX #${0x10 + lo:02X}\nLDA ${near:04X},X\nRTS", [t], False),
        "implied": (f"JSR ${t - 1:04X}\nRTS\n.org ${t - 1:04X}\nRTS", [t], False),
        "branch": (f"CLC\nJSR ${t - 2:04X}\nRTS\n.org ${t - 2:04X}\nBCC ${t:04X}\nRTS", [t], False),
        "fixup": (
            f"CLC\nJSR ${near - 2:04X}\nRTS\n.org ${near - 2:04X}\nBCC ${page + 0x100 + lo:04X}"
            f"\n.org ${page + 0x100 + lo:04X}\nRTS",
            [t],
            False,
        ),
        "rts": (
            f"LDA #${t >> 8:02X}\nPHA\nLDA #${lo:02X}\nPHA\nRTS\n.org ${t + 1:04X}\nRTS",
            [t],
            False,
        ),
    }[kind]


def _reference(img, t):
    out = wholeprog.reference_call(bytearray(img), 0x1000, volatile=[(t, t + 1)], log=True)
    assert out["halt"] == "return"
    return [a for a, _, _, w in out["log"] if not w]


def _jennings(img, t):
    mpu = NativeMPU(memory=NativeMemory(bytes(img)))
    mpu.sp = 0xFF
    _, acc = mpu.run_to_rts_accesses(0x1000, 0, 10_000)
    return [a & 0xFFFF for a in acc.tolist() if a & 0xFFFF == t and not a >> 16 & 1]


@pytest.mark.parametrize("t", ACK, ids=lambda t: f"{t:04X}")
@pytest.mark.parametrize("kind", ["load", "indexed", "implied", "branch", "fixup", "rts"])
def test_jennings_makes_no_dummy_reads(kind, t):
    src, reads, shared = _case(kind, t)
    img, _ = assemble(src)
    assert _reference(img, t) == reads
    assert _jennings(img, t) == (reads if shared else [])
