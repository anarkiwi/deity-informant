#!/usr/bin/env python3
"""Self-contained C64 "HELLO, WORLD!" demo: illegal opcodes + self-modifying code.

Depends only on ``deity_informant`` (no pysidtracker/HVSC, unlike the other
examples here). A 33-byte program at load address ``$1000`` writes the 13 screen
codes for ``HELLO, WORLD!`` into C64 screen RAM ``$0400..$040C`` using two
load-bearing NMOS illegal opcodes and genuine self-modifying code:

* **LAX $1013,Y** (``$BF``) loads A and X with the next (bit-inverted) message
  byte and sets Z; the ``BEQ`` terminator test rides on LAX's Z flag.
* **ISC $100A** (``$EF``) increments the low byte of the ``STA`` operand -- it
  self-modifies the store target each iteration. Its SBC side effect writes
  garbage to A, immediately overwritten by the next LAX.

The message is stored EOR-``$FF`` (bit-inverted) and decrypted with ``EOR #$FF``.

Run ``python examples/hello_world.py`` -- it prints ``HELLO, WORLD!`` and the
illegal opcodes it executed. The same program is verified through the 6510
SLEIGH spec (Ghidra's engine) in ``tests/test_hello_world.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from deity_informant import ILLEGAL_OPCODES, OPS, PcodeVM, lift, run_sub

ORG = 0x1000

# Raw code+data laid at $1000 (what the VM and Ghidra load). Comment column
# mirrors the assembly listing in docs/hello-world.md.
PROGRAM = bytes.fromhex(
    "A000"  # $1000  LDY #$00
    "BF1310"  # $1002  LAX $1013,Y   ; illegal: A=X=data[Y]; Z=1 at terminator
    "F00B"  # $1005  BEQ $1012      ; done when byte == 0 (rides LAX's Z)
    "49FF"  # $1007  EOR #$FF       ; decrypt (data is bit-inverted)
    "8D0004"  # $1009  STA $0400      ; low operand byte is self-modified
    "EF0A10"  # $100C  ISC $100A      ; illegal: INC store-operand (SMC) + SBC(discarded)
    "C8"  # $100F  INY
    "D0F0"  # $1010  BNE $1002      ; loop
    "60"  # $1012  RTS
    "F7FAF3F3F0D3DFE8F0EDF3FBDE00"  # $1013  "HELLO, WORLD!" EOR $FF, then $00 terminator
)

# Screen codes deposited at $0400.. after the program runs (H,E,L,L,O,",",space,W,O,R,L,D,!).
EXPECTED = bytes([0x08, 0x05, 0x0C, 0x0C, 0x0F, 0x2C, 0x20, 0x17, 0x0F, 0x12, 0x0C, 0x04, 0x21])

STA_PC = 0x1009  # the self-modified store; re-lifts once per distinct operand


def screen_text(codes) -> str:
    """Map C64 screen codes back to ASCII for display."""
    out = []
    for c in codes:
        if 1 <= c <= 26:
            out.append(chr(c + 0x40))  # $01..$1A -> A..Z
        elif c == 0:
            out.append("@")
        else:
            out.append(chr(c))  # $20..$3F match ASCII (space, punctuation, digits)
    return "".join(out)


def build_prg() -> bytes:
    """Return the program with a 2-byte little-endian PRG load-address header.

    For a real C64: ``LOAD"HELLO",8,1`` then ``SYS 4096`` ($1000).
    """
    return bytes([ORG & 0xFF, (ORG >> 8) & 0xFF]) + PROGRAM


def _run_vm():
    """Lay PROGRAM at ORG, execute to the RTS; return (vm, cache)."""
    mem = bytearray(0x10000)
    mem[ORG : ORG + len(PROGRAM)] = PROGRAM
    vm = PcodeVM(mem)
    cache = {}
    run_sub(vm, ORG, cache, lift)
    return vm, cache


def run() -> bytes:
    """Execute the program in the VM; return the 13 screen codes at $0400.."""
    vm, _ = _run_vm()
    return bytes(vm.mem[0x0400 : 0x0400 + len(EXPECTED)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", metavar="PATH", help="dump the raw program bytes to PATH")
    args = ap.parse_args(argv)

    if args.write:
        Path(args.write).write_bytes(PROGRAM)
        print("wrote %d raw bytes to %s (org $%04X)" % (len(PROGRAM), args.write, ORG))
        return 0

    vm, cache = _run_vm()
    codes = bytes(vm.mem[0x0400 : 0x0400 + len(EXPECTED)])
    print(screen_text(codes))
    assert codes == EXPECTED, "VM output %s != expected %s" % (codes.hex(), EXPECTED.hex())

    illegals = sorted(set(k[1] for k in cache) & set(ILLEGAL_OPCODES))
    print("illegal opcodes executed: " + ", ".join("$%02X %s" % (o, OPS[o][0]) for o in illegals))
    relifts = sum(1 for k in cache if k[0] == STA_PC)
    print(
        "self-modifying STA at $%04X re-lifted %d times (one per store target)" % (STA_PC, relifts)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
