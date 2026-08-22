#!/usr/bin/env python3
"""Run the tuneprog pipeline over the stratified HVSC sample, resumably, in parallel.

Sample and seed are ``run.py``'s (design section 9); one row per tune's default
subtune, appended as it finishes and its artefacts pruned. A rerun skips what
``--out`` holds and exits 2 while work is left, so a sweep is a loop of chunks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import signal
import sys
import time
import traceback
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # keep process_time() a measure of this work only
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position,wrong-import-order
# pylint: disable=broad-exception-caught,global-statement
from deity_informant.tuneprog import pipeline  # noqa: E402
from deity_informant.tuneprog.machine import Refusal  # noqa: E402
from headers import header_row  # noqa: E402
from run import _sample  # noqa: E402

USAGE = """
  tuneprog_sweep.py --hvsc C64Music --results results.csv --out h.jsonl --seconds 30
  tuneprog_sweep.py --hvsc C64Music --results results.csv --out p.jsonl \
--until-period --from h.jsonl
"""
MORE = 2
STAGES = ("trace", "front", "verify", "print")
KEEP_SUB = (
    "ticks",
    "seconds",
    "complete",
    "period",
    "first_repeat",
    "inputs_pinned",
    "divergences",
    "envelope_traps",
    "nmis",
)
KEEP_COST = ("sites", "regions", "ir_statements", "ir_blocks", "ir_procs", "verify_cpu_seconds")
KEEP_HDR = ("magic", "songs", "speed_bits", "speed_any_cia", "play0", "clock", "model", "basic")
_A = None
_CPU = defaultdict(float)


def _quiet(*_a, **_k):
    pass


def _timeout(_signum, _frame):
    raise TimeoutError("tune wall timeout")


def _timed(name):
    """Wrap one pipeline stage so its CPU seconds land in ``_CPU``."""
    fn = getattr(pipeline, "stage_" + name)

    def wrap(*a, **k):
        t = time.process_time()
        try:
            return fn(*a, **k)
        finally:
            _CPU[name] += time.process_time() - t

    return wrap


def worker_limits(rss):
    """Every sweep worker: the pool owns SIGINT, and one tune cannot take the box."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_AS, (rss << 30, resource.RLIM_INFINITY))


def _init_worker():
    worker_limits(_A.rss)
    for name in STAGES:
        setattr(pipeline, "stage_" + name, _timed(name))


def _argv(path, out):
    argv = [str(path), "--out", str(out), "--budget", "%d" % (2 * _A.timeout)]
    if _A.until_period:
        return argv + ["--until-period", "--max-calls", str(_A.max_calls)]
    return argv + ["--seconds", "%g" % _A.seconds]


def _stack(field):
    if field == "eliminated":
        return {"stack": "eliminated"}
    if isinstance(field, dict):
        return {"stack": "residual", "depth": field.get("depth"), "held": field.get("procs")}
    return {"stack": None}


def _certificate(out, row):
    """Fold ``certificate.json`` and ``state.json`` into the row; True when one exists."""
    st = out / "state.json"
    if st.exists():
        s = json.loads(st.read_text())
        row.update(stage=s.get("stage"), procs=s.get("procs"), stmts=s.get("stmts"))
    doc = out / "certificate.json"
    if not doc.exists():
        return False
    c = json.loads(doc.read_text())
    sub = c["subtunes"][0]
    row.update({k: sub.get(k) for k in KEEP_SUB})
    row.update({k: c["cost"].get(k) for k in KEEP_COST})
    row.update(_stack(c.get("stack")))
    e = c.get("entry") or {}
    row.update(entry=e.get("kind"), source=e.get("source"), kernal=e.get("kernal"))
    row["cycles_per_tick"] = e.get("cycles_per_tick")
    cp = c.get("copies") or {}
    row.update(
        copy_families=len(cp.get("families") or ()),
        copy_statements=cp.get("statements"),
        copy_unverified=cp.get("unverified"),
        copy_refused=[r.get("why") if isinstance(r, dict) else r for r in cp.get("refused") or ()],
    )
    row["divergence"] = c.get("divergence")
    row["outcome"] = "diverged" if c.get("divergence") else "certified"
    return True


def _smc(out, row):
    """The SMC facts the certificate does not carry, read straight from the trace."""
    doc = out / "trace.json"
    if not doc.exists():
        return
    t = json.loads(doc.read_text())
    ops = defaultdict(set)
    for pc, opcode, *_rest in t["sites"]:
        ops[pc].add(opcode)
    cells = {pc: v for pc, v in ops.items() if len(v) > 1}
    chip, ram = _planes(t)
    row.update(
        smc_cells=len(t["cells"]),
        smc_play=len(set(t["written_play"]) & set(t.get("code") or ())),
        opcode_cells=len(cells),
        opcode_cells_non_rts=sum(1 for v in cells.values() if 0x60 not in v),
        io_ram_bytes=len(ram),
        two_plane_bytes=len(chip & ram),
        rts_unmatched=sum(r[2] for r in t["rets"]),
    )


def _planes(t):
    """``($D000-$DFFF bytes reached as chip, as the RAM under it)`` over every op."""
    chip_ops = t.get("chip_ops")
    if chip_ops is None:
        return set(), set()
    seen = {tuple(x) for x in chip_ops}
    chip, ram = set(), set()
    for pc, _op, _fixed, _n, _ph, _var, _idx, reads, writes in t["sites"]:
        for i, addrs in reads + writes:
            side = chip if (pc, i) in seen else ram
            side.update(a for a in addrs if 0xD000 <= a <= 0xDFFF)
    return chip, ram


def _fault(exc):
    """``(outcome, reason, detail, site)`` of an exception the pipeline let out."""
    tb = traceback.extract_tb(exc.__traceback__)
    site = "%s:%s" % (Path(tb[-1].filename).name, tb[-1].name) if tb else "?"
    if isinstance(exc, Refusal):
        return "refused", exc.reason, exc.detail[:160], site
    if isinstance(exc, TimeoutError):
        return "timeout", "wall timeout", "", site
    if isinstance(exc, MemoryError):
        return "oom", "address space", "", site
    return "crashed", type(exc).__name__, str(exc)[:160], site


def _one(item):
    """One tune through the pipeline: its row, and no artefacts left behind."""
    rel, family = item
    path = Path(_A.hvsc) / rel
    out = Path(_A.work) / hashlib.sha1(rel.encode()).hexdigest()[:16]
    shutil.rmtree(out, ignore_errors=True)
    row = {"path": rel, "family": family, "outcome": "incomplete"}
    _CPU.clear()
    t0, c0 = time.time(), time.process_time()
    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(_A.timeout)
    try:
        data = path.read_bytes()
        row.update({k: header_row(rel, data)[k] for k in KEEP_HDR})
        pipeline.run(pipeline.parser().parse_args(_argv(path, out)), log=_quiet)
    except BaseException as exc:  # a sweep records faults, never raises
        row["outcome"], row["fault"], row["detail"], row["site"] = _fault(exc)
    finally:
        signal.alarm(0)
    fault = row.get("fault")
    signal.alarm(30)
    try:
        _smc(out, row)
    except BaseException:  # the trace is an extra, never the answer
        pass
    finally:
        signal.alarm(0)
    if _certificate(out, row) and fault:
        row["fault_after"] = "certificate"  # the certificate stands; S5/S6 did not finish
    row["wall"] = round(time.time() - t0, 2)
    row["cpu"] = round(time.process_time() - c0, 2)
    row.update({"cpu_" + k: round(_CPU[k], 2) for k in STAGES})
    shutil.rmtree(out, ignore_errors=True)
    return row


def _todo(args):
    """``(whole sample, rows still to run)``: on disk, not already in ``--out``."""
    done = set()
    if Path(args.out).exists():
        with open(args.out, encoding="utf-8") as f:
            done = {json.loads(line)["path"] for line in f if line.strip()}
    keep = None
    if args.from_:
        with open(args.from_, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        keep = {r["path"] for r in rows if r["outcome"] == "certified"}
    items = _sample(args.results, args.cap, args.seed, False)
    elig = [
        (p, fam)
        for p, fam in items
        if (keep is None or p in keep) and (Path(args.hvsc) / p).is_file()
    ]
    if args.only:
        want = {x.strip() for x in Path(args.only).read_text(encoding="utf-8").split("\n") if x.strip()}
        elig = [x for x in elig if x[0] in want]
        missing = sorted(want - {p for p, _f in elig})
        if missing:
            print(
                "--only: %d of %d not in the sample:" % (len(missing), len(want)), file=sys.stderr
            )
            for p in missing:
                print("  " + p, file=sys.stderr)
    if args.per_family:
        seen = Counter()
        elig = [(p, f) for p, f in elig if (seen.update([f]) or seen[f]) <= args.per_family]
    return items, [x for x in elig if x[0] not in done]


def parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog=USAGE)
    ap.add_argument("--hvsc", required=True)
    ap.add_argument("--results", required=True, help="SIDId family CSV (path,player)")
    ap.add_argument("--out", required=True, help="JSONL of result rows, appended and resumed")
    ap.add_argument("--work", default="out/sweep/work", help="scratch for per-tune artefacts")
    ap.add_argument("--from", dest="from_", help="only tunes certified in this JSONL")
    ap.add_argument("--cap", type=int, default=30, help="tunes per family (design section 9)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=30.0, help="horizon in seconds of music")
    ap.add_argument("--until-period", action="store_true")
    ap.add_argument("--max-calls", type=int, default=400_000)
    ap.add_argument("--timeout", type=int, default=120, help="per-tune wall seconds")
    ap.add_argument("--rss", type=int, default=8, help="per-worker address space, GiB")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 8) - 8))
    ap.add_argument("--budget", type=float, default=1800.0, help="wall seconds per invocation")
    ap.add_argument("--per-family", type=int, default=0, help="keep the first k per family")
    ap.add_argument("--only", help="file of HVSC-relative paths: run only these")
    ap.add_argument("--limit", type=int, default=0)
    return ap


def main(argv=None):
    global _A
    _A = args = parser().parse_args(argv)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.work).mkdir(parents=True, exist_ok=True)
    items, todo = _todo(args)
    if args.limit:
        todo = todo[: args.limit]
    print("sample %d, todo %d, jobs %d" % (len(items), len(todo), args.jobs), file=sys.stderr)
    t0, n = time.time(), 0
    with (
        open(args.out, "a", encoding="utf-8") as f,
        Pool(args.jobs, initializer=_init_worker, maxtasksperchild=16) as pool,
    ):
        for row in pool.imap_unordered(_one, todo, chunksize=1):
            f.write(json.dumps(row) + "\n")
            n += 1
            if n % 100 == 0:
                f.flush()
                print("%d/%d %.0fs" % (n, len(todo), time.time() - t0), file=sys.stderr)
            if time.time() - t0 > args.budget:
                pool.terminate()
                break
    left = len(todo) - n
    print("wrote %d rows in %.0fs, %d left" % (n, time.time() - t0, left), file=sys.stderr)
    return MORE if left > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
