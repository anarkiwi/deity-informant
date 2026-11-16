"""Translate PSID files to C, each under a wall-clock limit, and replay the C against the reference.

``python tools/liftsweep.py FILE... [--timeout S] [-n CALLS] [--image RAM] [--json OUT]``: per
file the translation time and whether the C replays exactly, traps at a deopt or differs
(``docs/wholeprog.md``, *Termination*).
"""

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

# pylint: disable=wrong-import-position
import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from concurrent.futures import TimeoutError as FutureTimeout  # noqa: E402
from pathlib import Path  # noqa: E402

from pebble import ProcessPool  # noqa: E402
from tqdm import tqdm  # noqa: E402

import cgenplay  # noqa: E402
from deity_informant import cgen, wholeprog  # noqa: E402

NAMES = ("init", "run")
#: The harness flags less ``-Werror``: a program that never returns compiles to C that warns.
CFLAGS = tuple(f for f in cgenplay.CFLAGS if f != "-Werror")
MAX_CYCLES = 10_000_000


def program(data, base=None):
    """``(image, entries, loaded, start)`` of the PSID bytes ``data`` loaded over ``base``
    (64K, default zeros): entries ``[(pc, name)]``, init first."""
    load, init, run, start, body = cgenplay.psid(data)
    image = bytearray(base or bytes(0x10000))
    image[load : load + len(body)] = body
    return image, list(zip((init, run), NAMES)), (load, load + len(body)), start


def translate(data, volatile, base=None):
    """``(seconds, c, h, traps)`` of the translation of the PSID bytes ``data``."""
    image, entries, loaded, start = program(data, base)
    a = max(start - 1, 0)
    t = time.perf_counter()
    c, h, info = cgen.translate(
        image, entries, volatile, (), loaded=loaded, header="t.h", init=0, songs=range(a, a + 1)
    )
    return time.perf_counter() - t, c, h, len(info["traps"])


def returning(image, pcs, calls, volatile, max_cycles=MAX_CYCLES):
    """The leading calls of ``calls`` the reference returns from, each within ``max_cycles``."""
    mem = bytearray(image)
    for i, (name, a) in enumerate(calls):
        r = wholeprog.reference_call(
            mem,
            pcs[name],
            a=a,
            reg=cgenplay.entry_regs(a),
            volatile=volatile,
            max_cycles=max_cycles,
        )
        if r["halt"] != "return":
            return calls[:i]
    return calls


def replay(data, c, h, volatile, n, base=None):
    """``(result, detail)`` of the C ``c``/``h`` of ``data`` replayed: init once with ``A`` the
    start value less one, then the other entry ``n`` times, up to a call the reference does
    not return from."""
    image, entries, _, start = program(data, base)
    pcs = {name: pc for pc, name in entries}
    want = [(NAMES[0], max(start - 1, 0))] + [(NAMES[1], 0)] * n
    calls = returning(image, pcs, want, volatile)
    note = "" if calls == want else f"the reference returns from {len(calls)} of {len(want)}"
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "t.c").write_text(c, encoding="utf-8")
        (Path(d) / "t.h").write_text(h, encoding="utf-8")
        lib = cgenplay.build(Path(d) / "t.c", Path(d) / "t.so", CFLAGS)
        try:
            cgenplay.replay(lib, image, pcs, calls, volatile)
        except AssertionError as e:
            why = e.args[0] if e.args else ()
            kind = "deopt" if len(why) > 2 and why[2] == "trap" else "differs"
            return kind, " ".join(filter(None, [f"call {why[0]}" if why else "", note]))
    return "exact", note


def _wait(fut):
    """The result of ``fut``, the exception it raised, or ``"timeout"`` past its limit."""
    try:
        return fut.result()
    except FutureTimeout:
        return "timeout"
    except Exception as e:  # pylint: disable=broad-exception-caught
        return e


def _failed(res, stage):
    """``(result, detail)`` of a stage that did not finish."""
    return ("timeout", stage) if res == "timeout" else ("error", f"{stage}: {res!r}"[:200])


def sweep(files, volatile, opts):
    """Per file ``{"file", "seconds", "traps", "result", "detail"}``, with progress."""
    base = Path(opts.image).read_bytes() if opts.image else None
    rows = {str(f): {"file": str(f), "seconds": None, "traps": None} for f in files}
    data = {f: Path(f).read_bytes() for f in rows}
    with ProcessPool(max_workers=opts.jobs) as pool:
        futs = {
            f: pool.schedule(translate, (d, volatile, base), timeout=opts.timeout)
            for f, d in data.items()
        }
        played = {}
        for f, fut in tqdm(futs.items(), desc="translate", unit="file"):
            res = _wait(fut)
            if isinstance(res, tuple):
                rows[f]["seconds"], c, h, rows[f]["traps"] = res
                args = (data[f], c, h, volatile, opts.n, base)
                played[f] = pool.schedule(replay, args, timeout=opts.replay_timeout)
            else:
                rows[f]["result"], rows[f]["detail"] = _failed(res, "translate")
        for f, fut in tqdm(played.items(), desc="replay", unit="file"):
            res = _wait(fut)
            rows[f]["result"], rows[f]["detail"] = (
                res if isinstance(res, tuple) else _failed(res, "replay")
            )
    return list(rows.values())


def _range(text):
    lo, hi = (int(x, 0) for x in text.split("-"))
    return (lo, hi)


def main(argv=None):
    """Command line: a row per file; exit 1 when a translation did not finish."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--timeout", type=float, default=120, help="seconds per translation")
    ap.add_argument("--replay-timeout", type=float, default=120, help="seconds per replay")
    ap.add_argument("-n", type=int, default=200, help="calls of the second entry")
    ap.add_argument("--jobs", type=int, default=min(12, os.cpu_count() or 1))
    ap.add_argument("--volatile", action="append", type=_range, help="LO-HI (default D000-E000)")
    ap.add_argument("--image", help="64K memory the files are loaded over (default zeros)")
    ap.add_argument("--json", help="write the rows here")
    a = ap.parse_args(argv)
    rows = sweep(a.files, a.volatile or [(0xD000, 0xE000)], a)
    for r in rows:
        secs = "-" if r["seconds"] is None else f"{r['seconds']:.2f}"
        print(f"{r['file']}\t{secs}\t{r['traps']}\t{r['result']}\t{r['detail']}")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return int(any(r["seconds"] is None for r in rows))


if __name__ == "__main__":
    sys.exit(main())
