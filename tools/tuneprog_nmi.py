#!/usr/bin/env python3
"""Classify the CIA #2 NMI schedules of a tune population, and report the classes.

    tuneprog_nmi.py scan --hvsc C64Music --results results.csv --out nmi.jsonl
    tuneprog_nmi.py report --rows nmi.jsonl --results results.csv --hvsc C64Music

One row per tune: whether a CIA #2 source can fire once its own init has run, what
the handler does, and what it shares with the play routine. Rates are raw over the
sample and re-weighted to HVSC by SIDId family size, as ``tools/survey/report.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import resource
import signal
import sys
import time
import traceback
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "survey"))

# pylint: disable=wrong-import-position,wrong-import-order
# pylint: disable=broad-exception-caught,global-statement,import-error
from deity_informant.tuneprog import nmi as N  # noqa: E402
from deity_informant.tuneprog.ir import SID_HI, SID_LO  # noqa: E402
from deity_informant.tuneprog.cia import CIA2_BASE  # noqa: E402
from deity_informant.tuneprog.machine import (  # noqa: E402
    Entry,
    MachineImage,
    Refusal,
    find_entries,
)
from deity_informant.tuneprog.trace import IDLE_INDEX, Tracer  # noqa: E402
from report import Rates  # noqa: E402
from run import _sample  # noqa: E402

CIA2_HI = CIA2_BASE + 0xFF
PAL_FRAME = 19656  # the provisional tick of a tune with no dispatchable entry
D418 = 0xD418  # the master-volume register a sample mixer owns
_A = None


def accesses(trace, pcs):
    """``(read addresses, written addresses)`` of every site at one of ``pcs``."""
    rd, wr = set(), set()
    for (pc, _op, _f), s in trace.sites.items():
        if pc in pcs:
            for a in s["reads"].values():
                rd |= a
            for a in s["writes"].values():
                wr |= a
    return rd, wr


def _band(addrs, lo, hi):
    return {a for a in addrs if lo <= a <= hi}


def _ram(addrs):
    return {a for a in addrs if a < 0xD000 or a > 0xDFFF}


def facts(trace, entry, nmi):
    """What the two entries do and what they share, from the trace alone."""
    hp = N.sites(trace)
    tp = N.reach(trace, entry["addr"])
    hrd, hwr = accesses(trace, hp)
    trd, twr = accesses(trace, tp)
    log = trace.nmilog
    calls = int(trace.meta["calls"]) or 1
    idle = int((log["insn"] == IDLE_INDEX).sum()) if len(log.get("insn", ())) else 0
    hsid, tsid = _band(hwr, SID_LO, SID_HI), _band(twr, SID_LO, SID_HI)
    return {
        "handler_addrs": sorted({int(a) for a in log.get("addr", ())}),
        "handler_pcs": len(hp),
        "handler_shared_code": len(hp & tp),
        "handler_smc": sum(1 for k in trace.sites if k[0] in hp and None in k[2]),
        "handler_writes_code": len(hwr & trace.cells),
        "handler_acks": int(bool(_band(hrd, CIA2_BASE, CIA2_HI))),
        "handler_writes_cia2": sorted("$%04X" % a for a in _band(hwr, CIA2_BASE, CIA2_HI)),
        "handler_sid": sorted("$%04X" % a for a in hsid),
        "handler_d418_only": bool(hsid) and hsid == {D418},
        "handler_ram_writes": len(_ram(hwr)),
        "handler_ram_reads": len(_ram(hrd)),
        "play_sid": len(tsid),
        "play_writes_sid": bool(tsid),
        "shared_nmi_writes_play_reads": len(_ram(hwr) & _ram(trd)),
        "shared_play_writes_nmi_reads": len(_ram(twr) & _ram(hrd)),
        "shared_both_write": len(_ram(hwr) & _ram(twr)),
        "nmis": len(log.get("call", ())),
        "nmis_per_tick": round(len(log.get("call", ())) / calls, 2),
        "nmis_in_idle": idle,
        "unmatched_rts": trace.meta["unmatched_rts"],
        "max_depth": trace.meta["max_depth"],
        "insns": trace.meta["insns"],
    }


def klass(r):
    """The class the measured facts put this tune in."""
    if not r.get("nmi_addr"):
        return "no nmi"
    if not r["handler_sid"]:
        return "no SID write" if r["handler_ram_writes"] else "acknowledge only"
    if not r["play_writes_sid"]:
        return "sample player, silent play"
    if r["handler_d418_only"]:
        return "sample mixer ($D418 only)"
    return "handler writes the register file"


def _entry(data):
    """``(image, provisional play entry, the gate that refused one)``.

    A tune with no dispatchable IRQ vector still has its init run here, so the
    CIA #2 state it leaves is measured rather than shadowed by that refusal.
    """
    img = MachineImage.from_sid(data)
    try:
        return img, find_entries(data)[1][0], None
    except Refusal as exc:
        return img, Entry("sub", img.init, PAL_FRAME, "pal_video"), exc.reason


def one(item):
    """One tune: trace ``--calls`` ticks and classify what its NMI did."""
    rel, family = item
    row = {"path": rel, "family": family}
    signal.signal(signal.SIGALRM, lambda *_a: (_ for _ in ()).throw(TimeoutError("wall")))
    signal.alarm(_A.timeout)
    t0 = time.process_time()
    try:
        data = (Path(_A.hvsc) / rel).read_bytes()
        img, entry, gate = _entry(data)
        row["gate"] = gate
        tr = Tracer(img, entry).run_init()
        cia = tr.vm.cia[1]
        row.update(
            entry=tr.entry.kind,
            source=tr.entry.source,
            cycles_per_tick=tr.entry.cycles_per_tick,
            play=img.play,
            icr=cia.icr,
            cra=cia.cra,
            crb=cia.crb,
            latch=cia.latch,
            latch_b=cia.latch_b,
            sources=cia.sources(),
            nmi_source=N.sources(cia) or None,
            nmi_period=N.period(cia),
            nmi_addr=tr.nmi.addr if tr.nmi else None,
            nmi_vector="$%04X" % N.vector(tr.vm.mem)[0],
        )
        if gate is not None:
            row["outcome"] = "refused"
            row["fault"] = gate if tr.nmi is None else "%s (nmi armed)" % gate
        elif tr.nmi is None:
            row["outcome"] = "no nmi"
        else:
            row["outcome"] = "traced"
            try:
                tr.run_calls(_A.calls)
            except Refusal as exc:  # classify what the ticks before the refusal showed
                row.update(outcome="refused", fault=exc.reason, detail=exc.detail[:120])
            if tr.calls_done:
                trace = tr.trace()
                row.update(facts(trace, trace.meta["entry"], trace.meta["schedule"][1]))
    except Refusal as exc:
        row.update(outcome="refused", fault=exc.reason, detail=exc.detail[:120])
    except BaseException as exc:
        tb = traceback.extract_tb(exc.__traceback__)
        row.update(
            outcome="crashed",
            fault=type(exc).__name__,
            detail=str(exc)[:120],
            site="%s:%s" % (Path(tb[-1].filename).name, tb[-1].name) if tb else "?",
        )
    finally:
        signal.alarm(0)
    row["class"] = klass(row) if row["outcome"] == "traced" else row.get("fault", row["outcome"])
    row["cpu"] = round(time.process_time() - t0, 2)
    return row


def _init_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_AS, (_A.rss << 30, resource.RLIM_INFINITY))


def scan(args):
    """Run the classifier over the sample (or ``--only``), appending rows to ``--out``."""
    out = Path(args.out)
    done = set()
    if out.exists():
        done = {
            json.loads(x)["path"] for x in out.read_text(encoding="utf-8").splitlines() if x.strip()
        }
    items = _sample(args.results, args.cap, args.seed, False)
    todo = [(p, f) for p, f in items if (Path(args.hvsc) / p).is_file() and p not in done]
    if args.only:
        want = {
            x.strip() for x in Path(args.only).read_text(encoding="utf-8").split("\n") if x.strip()
        }
        todo = [x for x in todo if x[0] in want]
    print("todo %d, jobs %d" % (len(todo), args.jobs), file=sys.stderr)
    t0 = time.time()
    with open(out, "a", encoding="utf-8") as f, Pool(args.jobs, initializer=_init_worker) as pool:
        for i, row in enumerate(pool.imap_unordered(one, todo, chunksize=4)):
            f.write(json.dumps(row) + "\n")
            if i % 500 == 0:
                f.flush()
                print("%d/%d %.0fs" % (i, len(todo), time.time() - t0), file=sys.stderr)
    print("wrote %d rows in %.0fs" % (len(todo), time.time() - t0), file=sys.stderr)
    return 0


def population(results, hvsc):
    """``{family: tunes present on disk}`` over the whole catalogue."""
    pop, root = Counter(), Path(hvsc)
    with open(results, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (root / r["path"]).is_file():
                pop[r["player"]] += 1
    return pop


def report(args):
    """The markdown tables the prototype record carries."""
    rows = [
        json.loads(x) for x in Path(args.rows).read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    rates = Rates(rows, population(args.results, args.hvsc))
    out = ["| class | tunes | raw | HVSC-weighted |", "|---|---|---|---|"]
    for name, _n in Counter(r["class"] for r in rows).most_common():
        out.append(rates.row("`%s`" % name, lambda r, k=name: r["class"] == k))
    out.append("")
    traced = [r for r in rows if r["outcome"] == "traced"]
    out.append(
        "| property of the %d traced schedules | tunes | raw | HVSC-weighted |" % len(traced)
    )
    out.append("|---|---|---|---|")
    props = (
        ("Timer A", lambda r: r["sources"] & 1),
        ("Timer B", lambda r: r["sources"] & 2),
        ("vector `$0318` (KERNAL mapped)", lambda r: r["nmi_vector"] == "$0318"),
        ("vector `$FFFA` (KERNAL banked out)", lambda r: r["nmi_vector"] == "$FFFA"),
        ("handler acknowledges the ICR", lambda r: r["handler_acks"]),
        ("handler rewrites a CIA #2 register", lambda r: r["handler_writes_cia2"]),
        ("handler self-modifies", lambda r: r["handler_smc"]),
        ("handler shares code with the play routine", lambda r: r["handler_shared_code"]),
        ("shared RAM: NMI writes, play reads", lambda r: r["shared_nmi_writes_play_reads"]),
        ("shared RAM: play writes, NMI reads", lambda r: r["shared_play_writes_nmi_reads"]),
        ("shared RAM: both write", lambda r: r["shared_both_write"]),
        ("the play routine writes no SID register", lambda r: not r["play_writes_sid"]),
        ("more than one NMI per tick", lambda r: r["nmis_per_tick"] > 1),
        ("every NMI ran in the idle time", lambda r: r["nmis"] and r["nmis_in_idle"] == r["nmis"]),
        ("the RTI frames balance", lambda r: not r["unmatched_rts"]),
    )
    for name, pred in props:
        out.append(rates.row(name, lambda r, p=pred: bool(p(r)), subset=traced))
    print("\n".join(out))
    return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--hvsc", required=True)
    s.add_argument("--results", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--only", help="file of HVSC-relative paths: run only these")
    s.add_argument("--calls", type=int, default=200, help="ticks to trace")
    s.add_argument("--cap", type=int, default=30)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--timeout", type=int, default=180)
    s.add_argument("--rss", type=int, default=8)
    s.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 8) - 8))
    r = sub.add_parser("report")
    r.add_argument("--rows", required=True)
    r.add_argument("--results", required=True)
    r.add_argument("--hvsc", required=True)
    return ap


def main(argv=None):
    global _A
    _A = args = parser().parse_args(argv)
    return scan(args) if args.cmd == "scan" else report(args)


if __name__ == "__main__":
    sys.exit(main())
