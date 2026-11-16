"""The loads through zero-page pointers of a PSID's static lift and what bounds each.

``python tools/ptrloads.py FILE... [--songs N] [--json OUT]``: per ``(zp),Y`` and ``(zp,X)``
load its pair, ``Y`` on every run, reach and proven hulls, object and pointer words
(``docs/wholeprog.md``, *Pointer objects*); one with no proven hull reads checked in the C.
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

#: The I/O range the files are lifted with.
VOLATILE = [(0xD000, 0xE000)]
#: The addressing mode of a load by its opcode's low five bits.
MODES = {0x11: "(zp),Y", 0x13: "(zp),Y", 0x01: "(zp,X)", 0x03: "(zp,X)"}


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
        mode = MODES.get(op & 0x1F)
        if mode is None or op & 0xE0 == 0x80:
            continue
        regs = facts.regs.get(pc)
        out.append(
            {
                "pc": pc,
                "mode": mode,
                "zp": image[pc + 1],
                "y": _span(regs[2]) if regs and mode == "(zp),Y" else "-",
                "reach": facts.reach.get(pc),
                "proven": facts.proven.get(pc),
                "object": served.get(pc),
                "words": len(ptr_sets.get(pc, ())),
            }
        )
    return out


def survey(data, songs=None):
    """The rows of the pointer loads of the PSID bytes ``data``, init's lift's last, init
    called with ``A`` each of ``songs`` (default the header's count)."""
    image, entries, loaded, _ = liftsweep.program(data)
    n = songs or int.from_bytes(data[14:16], "big") or 1
    p, _ = cgen.lift(image, entries, VOLATILE, loaded=loaded, init=0, songs=range(n))
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
    args = ap.parse_args(argv)
    report = {}
    for f in tqdm(args.files, desc="lift", unit="file"):
        report[f] = survey(Path(f).read_bytes(), args.songs)
        for r in report[f]:
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
