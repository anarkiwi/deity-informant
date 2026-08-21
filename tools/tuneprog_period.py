#!/usr/bin/env python3
"""Classify why a subtune's state does not repeat: counter, accumulator, or aperiodic.

Traces one subtune in ``--budget`` CPU-second chunks (exit 2 while work is left),
sampling every footprint cell and every SID write per tick, then reports
:func:`deity_informant.tuneprog.period.classify`; ``--resume`` continues a run.
"""

import argparse
import json
import os
import pickle
import sys
import time
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # keep process_time() a measure of this work only
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.tuneprog.machine import find_entries  # noqa: E402
from deity_informant.tuneprog.period import Samples, classify  # noqa: E402
from deity_informant.tuneprog.trace import Tracer  # noqa: E402

MORE = 2


def sample(path, calls, song, state, budget):
    """Trace to ``calls`` ticks, sampling each; ``(samples, done)``."""
    if state.exists():
        tr, s = pickle.loads(state.read_bytes())
    else:
        img, schedule = find_entries(Path(path).read_bytes(), song=song - 1)
        tr = Tracer(img, schedule[0], song=song - 1)
        tr.run_init()
        s = Samples(tr.vm)
    t0 = time.process_time()
    while s.n < calls and time.process_time() - t0 < budget:
        tr.run_calls(1)
        s.add(tr.vm)
    state.write_bytes(pickle.dumps((tr, s), protocol=pickle.HIGHEST_PROTOCOL))
    return s, s.n >= calls


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tuneprog_period.py", description=__doc__.splitlines()[0])
    ap.add_argument("sid")
    ap.add_argument("--song", type=int, default=1, help="1-based subtune")
    ap.add_argument("--calls", type=int, default=60_000, help="ticks to sample")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--resume", action="store_true", help="continue a chunked run")
    ap.add_argument("--budget", type=float, default=45.0, help="CPU seconds per invocation")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    state = out / ("period%02d.pkl" % args.song)
    if not args.resume:
        state.unlink(missing_ok=True)
    s, done = sample(args.sid, args.calls, args.song, state, args.budget)
    if not done:
        print("  sampled %d/%d ticks; rerun with --resume" % (s.n, args.calls))
        return MORE
    doc = dict(classify(s), tune=Path(args.sid).name, song=args.song)
    (out / ("period%02d.json" % args.song)).write_text(json.dumps(doc, indent=1, sort_keys=True))
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
