"""The loads through zero-page pointers of a PSID's static lift and what bounds each.

``python tools/ptrloads.py FILE... [--songs N] [--json OUT] [--all]``: per ``(zp),Y`` and
``(zp,X)`` load its pair, ``Y`` on every run, reach and proven hulls, object and pointer words
(``docs/wholeprog.md``, *Pointer objects*); one with no proven hull reads checked in the C.
``--all`` lists every access of no one address instead, with its base hull and why the memory
map (*Machine model*) does or does not hold it to the program.
"""

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

# pylint: disable=wrong-import-position
import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from tqdm import tqdm  # noqa: E402

import liftsweep  # noqa: E402
from deity_informant import cgen  # noqa: E402
from jennings import isa  # noqa: E402
from pysidtracker import parse_sid_header  # noqa: E402

#: The I/O range the files are lifted with.
VOLATILE = [(0xD000, 0xE000)]
#: The pointer loads' addressing modes as the rows name them.
MODES = {"indy": "(zp),Y", "indx": "(zp,X)"}


def _span(vals):
    """``lo-hi`` of a register's values (ascending ``bytes``), or ``-``."""
    return f"{vals[0]}-{vals[-1]}" if vals else "-"


def rows(image, starts, facts, objects, ptr_sets):
    """Per pointer load among ``starts`` of ``image``, a row of what its lift's ``facts``
    (:func:`deity_informant.cgen._facts`), ``objects`` and ``ptr_sets`` give it."""
    served = {pc: j for j, o in enumerate(objects) for pc in o[2]}
    out = []
    for pc in sorted(starts):
        op = image[pc]
        mode = MODES.get(isa.mode(op))
        if mode is None or isa.is_store(op):
            continue
        regs = facts.regs.get(pc)
        out.append(
            {
                "pc": pc,
                "mode": mode,
                "zp": image[(pc + 1) & 0xFFFF],
                "y": _span(regs[2]) if regs and mode == "(zp),Y" else "-",
                "reach": facts.reach.get(pc),
                "proven": facts.proven.get(pc),
                "object": served.get(pc),
                "words": len(ptr_sets.get(pc, ())),
            }
        )
    return out


def verdict(base, proven, checked, held):
    """Why an access of base hull ``base`` and proven hull ``proven`` reads plain or checked:
    ``checked`` whether the memory map checks it, ``held`` the program's bytes ``(lo, hi)``."""
    inside = proven is not None and held[0] <= proven[0] and proven[1] < held[1]
    if inside:
        return "plain, checked by the memory map" if checked else "plain"
    if base is None:
        return "no base"
    if base[0] == 0 and base[1] >= 0xFFFF:
        return "base unknown"
    if any(lo < base[1] + 1 and base[0] < hi for lo, hi in VOLATILE):
        return "base in I/O"
    if base[0] < held[0] or base[1] >= held[1]:
        return "base outside the program"
    return "entry words or least address outside the program"


def accesses(image, starts, facts, machine, held):
    """Per access of no one address among ``starts`` of ``image`` but pushes and pulls, a row
    of its base and the memory map's verdict (:func:`verdict`)."""
    out = []
    for pc in sorted(set(facts.bases) & set(starts)):
        _, _, blo, bhi = facts.bases[pc]
        proven = facts.proven.get(pc)
        mn, mode = isa.mnemonic(image[pc]), isa.mode(image[pc])
        if mode == "impl":
            continue
        out.append(
            {
                "pc": pc,
                "mode": f"{mn} {mode}",
                "base": (blo, bhi),
                "proven": proven,
                "verdict": verdict((blo, bhi), proven, pc in machine, held),
            }
        )
    return out


def survey(data, songs=None, every=False):
    """The rows of the pointer loads of the PSID bytes ``data``, init's lift's last, init
    called with ``A`` each of ``songs`` (default the header's count); with ``every`` those of
    every access of no one address of the other entries' lift (:func:`accesses`)."""
    image, entries, loaded, _ = liftsweep.program(data)
    n = songs or parse_sid_header(data).songs or 1
    p, _ = cgen.lift(image, entries, VOLATILE, loaded=loaded, init=0, songs=range(n))
    if every:
        held = (loaded[0], max([loaded[1], *(b for a, b in p.written if a >= loaded[1])]))
        return accesses(p.image, p.starts, p, p.machine, held)
    out = rows(p.image, p.starts, p, p.objects, p.ptr_sets)
    if p.lifted:
        _, starts, facts = p.lifted
        out += rows(p.start, starts - p.starts, facts, p.objects, p.ptr_sets)
    return out


def _hex(v):
    """Words in hex, or ``-``."""
    return "-" if v is None else " ".join(f"{x:04X}" for x in v)


def main(argv=None):
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--songs", type=int)
    ap.add_argument("--json")
    ap.add_argument("--all", action="store_true", help="every access of no one address")
    args = ap.parse_args(argv)
    report = {}
    for f in tqdm(args.files, desc="lift", unit="file"):
        report[f] = survey(Path(f).read_bytes(), args.songs, args.all)
        for r in report[f]:
            if args.all:
                print(
                    f"{f}: ${r['pc']:04X} {r['mode']} base {_hex(r['base'])}"
                    f" proven {_hex(r['proven'])}: {r['verdict']}"
                )
                continue
            print(
                f"{f}: ${r['pc']:04X} {r['mode']} ${r['zp']:02X} y {r['y']}"
                f" reach {_hex(r['reach'])} proven {_hex(r['proven'])}"
                f" obj {r['object']} words {r['words']}"
            )
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
