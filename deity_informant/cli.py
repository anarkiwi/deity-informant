"""Command-line interface for deity-informant.

Subcommands:
  disasm       linear-lift a code region and print mnemonics (illegals included)
  pcode        dump the raw P-Code op list for one instruction
  run          drive a playroutine through PcodeVM and print the $D400.. grid
  emit-sleigh  build the 6510 Ghidra/pypcode SLEIGH module (delegates to build.py)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .lifter import OPS, MODE_LEN, ILLEGAL_OPCODES, lift
from .vm import PcodeVM, run_sub


# operand rendering per addressing mode (mnemonic already comes from OPS)
def _operand(mode, mem, pc):
    lo = mem[(pc + 1) & 0xFFFF]
    hi = mem[(pc + 2) & 0xFFFF]
    word = lo | (hi << 8)
    return {
        "impl": "",
        "acc": "A",
        "imm": "#$%02X" % lo,
        "zp": "$%02X" % lo,
        "zpx": "$%02X,X" % lo,
        "zpy": "$%02X,Y" % lo,
        "abs": "$%04X" % word,
        "absx": "$%04X,X" % word,
        "absy": "$%04X,Y" % word,
        "indx": "($%02X,X)" % lo,
        "indy": "($%02X),Y" % lo,
        "ind": "($%04X)" % word,
        "rel": "$%04X" % ((pc + 2 + (lo - 256 if lo & 0x80 else lo)) & 0xFFFF),
    }[mode]


def format_insn(mem, pc):
    """Return ``(length, text)`` for the instruction at ``pc``."""
    op = mem[pc]
    mn, mode = OPS[op]
    text = "%-4s %s" % (mn, _operand(mode, mem, pc))
    tag = "  ; illegal" if op in ILLEGAL_OPCODES else ""
    return MODE_LEN[mode], "$%04X: %02X  %s%s" % (pc, op, text.rstrip(), tag)


def _load(path, org):
    data = Path(path).read_bytes()
    mem = bytearray(0x10000)
    mem[org : org + len(data)] = data[: 0x10000 - org]
    return mem, len(data)


def cmd_disasm(args):
    mem, n = _load(args.file, args.org)
    pc = args.start if args.start is not None else args.org
    end = pc + args.count if args.count else args.org + n
    while pc < end:
        length, text = format_insn(mem, pc)
        print(text)
        pc += length
    return 0


def cmd_pcode(args):
    mem, _ = _load(args.file, args.org)
    rec = lift(mem, args.at)
    _, text = format_insn(mem, args.at)
    print(text)
    for mn, out, ins in rec["ops"]:
        o = "" if out is None else "%s = " % (out,)
        print("    %s%s %s" % (o, mn, ins))
    print("  len=%d cyc=%d pen=%s ctrl=%s" % (rec["len"], rec["cyc"], rec["pen"], rec["ctrl"]))
    return 0


def cmd_run(args):
    mem, _ = _load(args.file, args.org)
    vm = PcodeVM(mem)
    vm.mem[0xD418] = 0x0F
    cache = {}
    run_sub(vm, args.init, cache, lift)
    if args.play is None:
        print("init done; $D400.. = " + " ".join("%02X" % vm.mem[0xD400 + i] for i in range(25)))
        return 0
    for f in range(args.frames):
        run_sub(vm, args.play, cache, lift)
        row = " ".join("%02X" % vm.mem[0xD400 + i] for i in range(25))
        print("frame %4d: %s" % (f, row))
    return 0


def cmd_emit_sleigh(args):
    build = Path(__file__).resolve().parent.parent / "ghidra" / "6510" / "build.py"
    if not build.is_file():
        sys.stderr.write("ghidra/6510/build.py not found (run from a source checkout)\n")
        return 1
    cmd = [sys.executable, str(build)]
    if args.out:
        cmd += ["--install", args.out]
    if args.magic:
        cmd += ["--magic", args.magic]
    return subprocess.call(cmd)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="deity-informant",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def org(p):
        p.add_argument("file")
        p.add_argument(
            "--org",
            type=lambda x: int(x, 0),
            default=0x1000,
            help="load address of FILE (default 0x1000)",
        )

    p = sub.add_parser("disasm", help="linear-lift a region and print mnemonics")
    org(p)
    p.add_argument("--start", type=lambda x: int(x, 0), default=None)
    p.add_argument("--count", type=int, default=0, help="bytes to disassemble (default whole file)")
    p.set_defaults(fn=cmd_disasm)

    p = sub.add_parser("pcode", help="dump raw P-Code for one instruction")
    org(p)
    p.add_argument("--at", type=lambda x: int(x, 0), required=True)
    p.set_defaults(fn=cmd_pcode)

    p = sub.add_parser("run", help="drive a playroutine through PcodeVM")
    org(p)
    p.add_argument("--init", type=lambda x: int(x, 0), required=True)
    p.add_argument("--play", type=lambda x: int(x, 0), default=None)
    p.add_argument("--frames", type=int, default=1)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("emit-sleigh", help="build the 6510 SLEIGH module")
    p.add_argument("-o", "--out", help="languages dir to install the built module into")
    p.add_argument("--magic", help="override the ANE/LXA magic constant, e.g. 0x00")
    p.set_defaults(fn=cmd_emit_sleigh)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
