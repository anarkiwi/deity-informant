"""Disassemble a cached tune, so a residue claim can be read against the machine.

Every "unliftable"/"refuses" claim in docs/register-model-lift-impl.md must cite the
instructions behind it; this is the tool that reads them. It resolves a tune the way
every sweep does, loads the PSID image, and prints a range -- or every site of an opcode.
"""

import argparse
import sys

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/disasm_tune.py Angry_Birds --start 0x09F1 --end 0x0A36
  python tools/disasm_tune.py Ghouls_n_Ghosts --start 0x6AD0 --end 0x6B64
  python tools/disasm_tune.py ASL/04 --opcode 0x95      # every STA $zz,X site"""


def load(raw):
    """``(path, mem, load, init, play)`` for the one cached tune ``raw`` names."""
    from deity_informant.c64 import load_psid

    paths = sorted(_sweep.HVSC.rglob("*.sid"))
    ident = _sweep.resolve({_sweep.tune_id(p) for p in paths}, raw)
    path = next(p for p in paths if _sweep.tune_id(p) == ident)
    mem, loadaddr, init, play = load_psid(path.read_bytes())
    return path, mem, loadaddr, init, play


def lines(mem, start, end):
    """Linear disassembly rows of ``mem[start:end]``; data regions will desync."""
    from deity_informant.cli import format_insn

    pc = start
    while pc < end:
        length, text = format_insn(mem, pc)
        yield text
        pc += length


def sites(mem, opcode, lo, hi):
    """Every address in ``[lo, hi)`` holding ``opcode``, disassembled at that seat.

    A linear walk desyncs over data, so a byte-anchored scan is how an idiom a
    census record names (``STA $zz,X``, ``TSX``) is located before a range is read."""
    from deity_informant.cli import format_insn

    for pc in range(lo, hi):
        if mem[pc] == opcode:
            yield format_insn(mem, pc)[1]


def _hexint(x):
    """``argparse`` type for an address in any base, as ``cli.py`` spells it."""
    return int(x, 0)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("tune", help="tune id or stem, resolved like every sweep resolves it")
    hexint = _hexint
    ap.add_argument("--start", type=hexint, help="first address (default: the play entry)")
    ap.add_argument("--end", type=hexint, help="one past the last address (default: start+0x40)")
    ap.add_argument("--opcode", type=hexint, help="scan the image for this opcode instead")
    args = ap.parse_args()

    path, mem, loadaddr, init, play = load(args.tune)
    print("; %s load=$%04X init=$%04X play=$%04X" % (path.name, loadaddr, init, play))
    if args.opcode is not None:
        lo = args.start if args.start is not None else loadaddr
        hi = args.end if args.end is not None else 0x10000
        for text in sites(mem, args.opcode, lo, hi):
            print(text)
        return 0
    start = args.start if args.start is not None else play
    for text in lines(mem, start, args.end if args.end is not None else start + 0x40):
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
