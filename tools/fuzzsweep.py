"""Sweep the guard-fuzz generator over many seeds in parallel; print failing seeds.

python tools/fuzzsweep.py --seeds 0 6000 --jobs 36
"""

import argparse
import importlib
import os
import sys
from functools import partial
from multiprocessing import Pool

from tqdm import tqdm

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))


def run(seed, backend="jit", module="test_guard_fuzz"):
    """The first failure of ``seed`` over both cycle modes, or None."""
    from deity_informant import wholeprog  # pylint: disable=C0415

    mod = importlib.import_module(module)
    t = next(v for k, v in vars(mod).items() if k.startswith("test_") and callable(v))

    wholeprog.BACKEND = backend
    for cycles in (False, True):
        try:
            t(seed, cycles)
        except Exception as e:  # pylint: disable=broad-except
            return seed, cycles, repr(e)[:200]
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, nargs=2, default=[0, 6000])
    ap.add_argument("--backend", choices=["jit", "interp"], default="jit")
    ap.add_argument("--module", default="test_guard_fuzz")
    ap.add_argument("--jobs", type=int, default=36)
    a = ap.parse_args(argv)
    with Pool(min(a.jobs, 36)) as p:
        res = list(
            tqdm(
                p.imap_unordered(
                    partial(run, backend=a.backend, module=a.module), range(*a.seeds), chunksize=8
                ),
                total=a.seeds[1] - a.seeds[0],
            )
        )
    bad = sorted(r for r in res if r)
    for r in bad:
        print(r)
    print("failing seeds:", [r[0] for r in bad])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
