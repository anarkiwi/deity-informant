"""Machine-level witness programs, labelled by the vagrant-moses machine oracle.

Each gadget banks RAM under ROM/IO and fetches through another configuration, or arms a CIA
timer IRQ / CIA2 NMI whose handler patches code or a vector between instructions. They cover
the banking, async-interrupt and port-write scenario families the flat lifter cannot model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vagrant_moses.c64 import poweron_ram

from .asm import assemble
from .machine import MachineOracle

__all__ = ["MachineWitness", "witnesses"]


@dataclass
class MachineWitness:
    """A machine witness: source, entry calls and interrupt setup; labelled lazily."""

    name: str
    source: str
    org: int = 0x1400
    calls: tuple = ((0,),)
    vectors: dict = field(default_factory=dict)
    frames: int = 0
    irq_setup: bool = False

    def image(self):
        img, labels = assemble(self.source, self.org)
        base = bytearray(poweron_ram())
        for addr in range(0x10000):
            if img[addr]:
                base[addr] = img[addr]
        for addr, label in self.vectors.items():
            tgt = labels[label]
            base[addr], base[addr + 1] = tgt & 0xFF, tgt >> 8
        return base, labels

    def run(self):
        base, labels = self.image()
        entry = labels[self.entry]
        oracle = MachineOracle(base)
        calls = [(entry, a if len(c) > 1 else 0) for c in self.calls for a in (c[-1],)]
        return oracle.run(calls, frames=self.frames, irq_setup=self.irq_setup)

    @property
    def entry(self):
        return "play" if self.frames else "entry"


# Bank KERNAL out, write RAM under it, bank it back in, fetch the byte through ROM.
_ROM_VS_RAM = """
entry:  LDA #$35
        STA $01
        LDA #$00
        STA $E475
        LDA #$37
        STA $01
        JSR $E475
        STA $C000
        RTS
"""

# Write code into RAM under the KERNAL (ROM mapped in), bank it out, run the RAM.
_RAM_UNDER_ROM = """
entry:  LDA #$A9
        STA $E47E
        LDA #$42
        STA $E47F
        LDA #$60
        STA $E480
        LDA #$35
        STA $01
        JSR $E47E
        LDX #$37
        STX $01
        STA $C000
        RTS
"""

# A CIA timer IRQ whose handler patches a looping instruction and the CINV vector.
_IRQ_PATCH = """
play:   LDX #$00
loop:   INX
        BNE loop
        RTS
hdlr:   LDA #$99
        STA tgt+1
        LDA #lo(alt)
        STA $0314
        LDA #$01
        STA $DC0D
        LDA #$FF
        STA $D019
        JMP $EA81
alt:    JMP $EA81
tgt:    LDA #$11
"""


def witnesses():
    """The machine witnesses to label with the machine oracle."""
    return [
        MachineWitness("rom_vs_ram", _ROM_VS_RAM, calls=((0,), (0,))),
        MachineWitness("ram_under_rom", _RAM_UNDER_ROM, calls=((0,), (0,))),
        MachineWitness("irq_patch", _IRQ_PATCH, vectors={0x0314: "hdlr"}, frames=3, irq_setup=True),
    ]
