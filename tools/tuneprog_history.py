#!/usr/bin/env python3
"""Per-tick histories of every named cell of a decompiled tune, from its output directory.

Replays the certified program against its own trace
(:func:`deity_informant.tuneprog.history.history`), writes one array per name to
``tuneprog.history.npz`` -- the S6 ``u16`` pairs widened alongside their bytes --
and reports how many distinct values each name took. Nothing here is a pipeline
artefact: the tuneprog files are read, never written.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # keep process_time() a measure of this work only
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
import numpy as np  # noqa: E402
from deity_informant.tuneprog.history import history, widen_u16  # noqa: E402
from deity_informant.tuneprog.ir import Tuneprog  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402


def distinct(a):
    """How many distinct values a name took: rows, for a multi-byte region."""
    return len(np.unique(a, axis=0) if a.ndim > 1 else np.unique(a))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tuneprog_history.py", description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="a pipeline output directory")
    ap.add_argument("--calls", type=int, help="ticks to replay (default: the trace's)")
    ap.add_argument("--kinds", default="state", help="comma-separated region kinds")
    ap.add_argument("--backend", default="interp", choices=("interp", "py"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    names = json.loads((out / "tuneprog.S6.json").read_text())
    regions = json.loads((out / "regions.json").read_text())
    t0 = time.process_time()
    hist, v = history(
        prog,
        Trace.load(out),
        names,
        calls=args.calls,
        backend=args.backend,
        kinds=tuple(args.kinds.split(",")),
        regions_doc=regions,
    )
    words = {n + "_u16": a for n, a in widen_u16(hist, names).items()}
    secs = time.process_time() - t0
    np.savez_compressed(out / "tuneprog.history.npz", **{**hist, **words})
    for n, a in sorted(hist.items()):
        cells = a.shape[1] if a.ndim > 1 else 1
        print("  %-24s %2d cell(s) %6d distinct" % (n, cells, distinct(a)))
    for n, a in sorted(words.items()):
        print("  %-24s %2s        %6d distinct" % (n, "16", distinct(a)))
    print(
        "%s: %d ticks, %d cells, %d names, %.1f s, divergence %s"
        % (out.name, v.call, len(hist.cells), len(hist) + len(words), secs, json.dumps(v.div))
    )
    return 1 if v.div is not None else 0


if __name__ == "__main__":
    sys.exit(main())
