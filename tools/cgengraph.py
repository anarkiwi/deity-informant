"""Dump cgen's structuring graph of the C functions holding given instructions.

``python tools/cgengraph.py FILE.sid --pc 0x62EF ...`` translates a PSID over the C64 power-on
RAM and prints each matching function's nodes (first instruction, branch instruction, edges by
target instruction, ``*`` a back edge), then the printed gotos and loop heads.
"""

import argparse
import sys
from pathlib import Path

from deity_informant.cgen import printer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cgenplay import psid  # noqa: E402  pylint: disable=wrong-import-position


def pcs_of(cf, n):
    """The instruction addresses graph node ``n`` of C function ``cf`` holds."""
    return {o.pc for b in cf.blocks(n) for o in b.ops if o.pc}


def dump(cf, out=print, verbose=False):
    """Print C function ``cf``'s structured graph by instruction address."""
    g = cf.g
    out(f"== {cf.f.name} entry {cf.first_pc(g.entry) or 0:04X}")
    for n in sorted(g.items, key=lambda n: (cf.first_pc(n) or 0, n)):
        es = " ".join(
            f"{'*' if (n, t) in g.back else ''}{cf.first_pc(t) or 0:04X}[{t}]" for t, _ in g.succ[n]
        )
        kinds = ",".join(sorted({it[0] for it in g.items[n]}))
        out(f"  [{n}] {cf.first_pc(n) or 0:04X}..{cf.source_pc(n) or 0:04X} {kinds:12} -> {es}")
        for it in g.items[n]:
            if it[0] == "ops" and verbose:
                out("        " + "; ".join(o.name.split(".")[-1] + ("@" + o.prop("callee")[1:] if o.name == "func.call" else "") for o in it[1].ops))
            if it[0] == "copy":
                out("        " + ", ".join(f"{d} := {v}" for d, v, _ in it[1]))


def main(argv=None):
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sid")
    ap.add_argument("--pc", action="append", type=lambda s: int(s, 0), default=[])
    ap.add_argument("--ops", action="store_true", help="print each block's operations")
    args = ap.parse_args(argv)
    from vagrant_moses.c64 import poweron_ram  # pylint: disable=import-outside-toplevel
    from vagrant_moses.cgen import translate_c64  # pylint: disable=import-outside-toplevel

    load, init, play, _, body = psid(Path(args.sid).read_bytes())
    ram = bytearray(poweron_ram())
    ram[load : load + len(body)] = body
    want, structure = set(args.pc), printer.CFunc.structure

    def hook(cf):
        hit = not want or any(pcs_of(cf, n) & want for n in cf.g.items)
        structure(cf)
        if hit:
            dump(cf, verbose=args.ops)

    printer.CFunc.structure = hook
    try:
        _, _, info = translate_c64(ram, init, play, loaded=(load, load + len(body)))
    finally:
        printer.CFunc.structure = structure
    gotos = sorted({f"{a:04X}->{b:04X}" for a, b in info["provenance"]["gotos"]})
    print("gotos", len(gotos), " ".join(gotos))
    print("loops", " ".join(f"{h:04X}" for h in info["provenance"]["loops"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
