#!/usr/bin/env python3
"""Export headless-Ghidra facts: from a tuneprog output dir, or a synthetic demo.

``python3 tools/tuneprog_ghidra.py OUTDIR [--dst DIR]`` writes ``ghidra_facts.json``
+ ``image_post_init.bin``; ``--demo NAME DIR`` writes the same for one of the
demos :data:`DEMOS` names. Feed the directory to ``ghidra/6510/headless/run.sh``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.tuneprog import ghidra_compare, ghidra_facts  # noqa: E402
from examples.hello_world import EXPECTED, ORG, PROGRAM, STA_PC  # noqa: E402

HELLO_END = 0x1013  # first data byte; $1000..$1012 is code
RTS_PC = HELLO_END - 1
RTI = 0x40
# LDA #$34 STA $01 (I/O out) LDA #$3F STA $D400 (RAM) LDA #$37 STA $01 (I/O in)
# LDA #$21 STA $D400 (the chip) RTS -- the banking gate in nine instructions
BANK = bytes.fromhex("A934" "8501" "A93F" "8D00D4" "A937" "8501" "A921" "8D00D4" "60")
BANK_PCS = [0x1000, 0x1002, 0x1004, 0x1006, 0x1009, 0x100B, 0x100D, 0x100F, 0x1012]


def _entry(kind):
    """The schedule's first entry, in :meth:`~.machine.Entry.to_dict` shape."""
    e = {"kind": kind, "addr": ORG, "cycles_per_tick": 19656, "source": "cia_timer"}
    return e if kind == "sub" else dict(e, kernal=False)


def _write(dst, doc, image):
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "image_post_init.bin").write_bytes(bytes(image))
    (dst / "ghidra_facts.json").write_text(json.dumps(doc, indent=1))
    return dst


def bank_facts(dst):
    """Facts for the banking demo: only the I/O-mapped store reaches the chip.

    Both stores land on ``$D400``; the first is RAM under the chip, so a memory
    diff sees two register changes where the machine made one.
    """
    image = bytearray(0x10000)
    image[ORG : ORG + len(BANK)] = BANK
    image[0], image[1] = 0x2F, 0x37  # the port init leaves: I/O mapped
    doc = {
        "language": ghidra_facts.LANGUAGE,
        "meta": {
            "init": ORG,
            "play": ORG,
            "load": [ORG, ORG + len(BANK)],
            "schedule": [_entry("sub")],
        },
        "image": "image_post_init.bin",
        "entries": [{"addr": ORG, "name": "bank", "kind": "tick", "roles": ["tick"]}],
        "insn_addrs": BANK_PCS,
        "smc_cells": [],
        "computed_jumps": [],
        "tail_calls": [],
        "regions": [],
        "inputs": [],
        "emulate": {
            "calls": 1,
            "entry": "tick",
            "sid_base": 0xD400,
            "sid_len": 0x19,
            "writes": [[[0, 0x21]]],
            "pins": [],
            "reads": [[]],
            "unpinned_inputs": [],
        },
    }
    return _write(dst, doc, image)


def hello_facts(dst, irq=False):
    """Facts for the 33-byte SMC demo: one entry, one addr cell at the STA.

    ``irq`` swaps the closing ``RTS`` for an ``RTI``, which is the whole of the
    difference an installed-handler tick makes to this oracle: the frame the
    machine pushes entering it, and the balanced stack it ends on.
    """
    image = bytearray(0x10000)
    image[ORG : ORG + len(PROGRAM)] = PROGRAM
    if irq:
        image[RTS_PC] = RTI
    doc = {
        "language": ghidra_facts.LANGUAGE,
        "meta": {
            "init": ORG,
            "play": ORG,
            "load": [ORG, ORG + len(PROGRAM)],
            "schedule": [_entry("irq" if irq else "sub")],
        },
        "image": "image_post_init.bin",
        "entries": [{"addr": ORG, "name": "hello", "kind": "tick", "roles": ["tick"]}],
        "insn_addrs": [0x1000, 0x1002, 0x1005, 0x1007, 0x1009, 0x100C, 0x100F, 0x1010, 0x1012],
        "smc_cells": [
            {
                "pc": STA_PC,
                "len": 3,
                "kinds": ["addr"],
                "cells": [STA_PC + 1],
                "variants": [],
                "mnemonic": "STA",
                "mode": "abs",
                "context": ["smc_addr"],
            }
        ],
        "computed_jumps": [],
        "tail_calls": [],
        "regions": [
            {
                "id": 0,
                "name": "screen",
                "base": 0x0400,
                "size": 13,
                "kind": "state",
                "stride": 1,
                "origin": 0x0400,
                "fields": [0],
                "count": 13,
            },
            {
                "id": 1,
                "name": "message",
                "base": HELLO_END,
                "size": len(PROGRAM) + ORG - HELLO_END,
                "kind": "const",
                "stride": 1,
                "origin": HELLO_END,
                "fields": [0],
                "count": len(PROGRAM) + ORG - HELLO_END,
            },
        ],
        "inputs": [],
        "emulate": {
            "calls": 1,
            "entry": "tick",
            "sid_base": 0x0400,
            "sid_len": len(EXPECTED),
            "writes": [[[i, v] for i, v in enumerate(EXPECTED)]],
            "pins": [],
            "reads": [[]],
            "unpinned_inputs": [],
        },
    }
    return _write(dst, doc, image)


DEMOS = {
    "hello": hello_facts,
    "irq": lambda dst: hello_facts(dst, irq=True),
    "bank": bank_facts,
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("outdir", nargs="?", help="a finished tuneprog output directory")
    ap.add_argument("--dst", help="where to write the facts (default: OUTDIR/ghidra)")
    ap.add_argument(
        "--demo",
        nargs=2,
        metavar=("NAME", "DIR"),
        help="write a synthetic demo's facts (%s) into DIR" % "|".join(DEMOS),
    )
    ap.add_argument("--compare", metavar="DIR", help="join OUTDIR with a headless export in DIR")
    ap.add_argument("--tol", type=float, default=ghidra_compare.TOL, help="complexity tolerance")
    args = ap.parse_args(argv)
    if args.compare:
        doc = ghidra_compare.compare(args.outdir, args.compare, args.tol)
        Path(args.compare, "comparison.json").write_text(json.dumps(doc, indent=1))
        Path(args.compare, "comparison.md").write_text(ghidra_compare.markdown(doc) + "\n")
        print(ghidra_compare.markdown(doc))
        print("\nflags: %s" % ([f["entry"] for f in doc["flags"]] or "none"))
        return 0
    if args.demo:
        name, into = args.demo
        if name not in DEMOS:
            ap.error("unknown demo %s (have %s)" % (name, ", ".join(DEMOS)))
        dst = DEMOS[name](into)
    elif args.outdir:
        dst = ghidra_facts.export(args.outdir, args.dst)
    else:
        ap.error("give an output directory or --demo NAME DIR")
    doc = json.loads((dst / "ghidra_facts.json").read_text())
    print(
        "%s: %d entries, %d SMC cell sites, %d computed jumps, %d regions"
        % (
            dst,
            len(doc["entries"]),
            len(doc["smc_cells"]),
            len(doc["computed_jumps"]),
            len(doc["regions"]),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
