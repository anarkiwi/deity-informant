#!/usr/bin/env python3
"""Reproduce every certificate in ``docs/certificates/`` and diff it field for field.

A certificate records its own run (tune, subtune, horizon, SID model, stage), so
the set replays through the pipeline in ``--budget`` chunks; each invocation
prints the table and exits 2 while work is left. Timestamp and timings excepted.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # keep process_time() a measure of this work only
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
from deity_informant.tuneprog import ghidra_compare, pipeline, tunes  # noqa: E402

CERTS = ROOT / "docs" / "certificates"
IGNORE = (("generated",), ("cost", "verify_cpu_seconds"), ("cost", "calls_per_second"))
COLS = "%-22s %-26s %-9s %9s %9s %-8s %s"
OCOLS = "%-22s %9s %9s %9s %9s  %s"


def tune_path(name, hvsc=None):
    """The tune's file, resolved through the one canonical map (:mod:`.tunes`)."""
    try:
        return tunes.resolve(name, hvsc=hvsc)
    except ImportError:  # pragma: no cover - pysidtracker is an optional extra
        return None


def horizon(subs):
    """The horizon options the certificate's tick counts imply.

    A subtune that stopped one tick past its first repeat was traced with
    ``--until-period``; a ``--songs all`` run shares one tick target across every
    subtune, so it carries both.
    """
    s = subs[0]
    if len(subs) > 1:
        return ["--until-period", "--calls", str(max(x["ticks"] for x in subs))]
    if s["first_repeat"] is not None and s["ticks"] == s["first_repeat"] + 1:
        return ["--until-period"]
    return ["--calls", str(s["ticks"])]


def plan(doc, out, path, budget, ghidra=False):
    """The pipeline command line one certificate records."""
    subs = doc["subtunes"]
    argv = [str(path), "--out", str(out), "--resume", "--budget", "%.1f" % budget]
    argv += ["--songs", "all"] if len(subs) > 1 else ["--song", str(subs[0]["song"])]
    argv += horizon(subs)
    if doc["sid_model"]:
        argv += ["--sid-model", doc["sid_model"]]
    if doc.get("stage") != "S6":
        argv.append("--no-text")
    if ghidra:
        argv.append("--ghidra-facts")
    return argv


def diff(want, got, path=()):
    """Every field where the two documents disagree, as ``path: want -> got`` lines."""
    if path in IGNORE:
        return []
    if isinstance(want, dict) and isinstance(got, dict):
        out = []
        for k in sorted(set(want) | set(got)):
            out += diff(want.get(k, "<missing>"), got.get(k, "<missing>"), path + (k,))
        return out
    if isinstance(want, list) and isinstance(got, list) and len(want) == len(got):
        return [d for i, (a, b) in enumerate(zip(want, got)) for d in diff(a, b, path + (str(i),))]
    if want == got:
        return []
    return ["%s: %r -> %r" % (".".join(path) or "<doc>", want, got)]


def _quiet(*_a, **_k):
    """Swallow the pipeline's per-chunk progress; the table is the report."""


def replay(name, doc, args, t0):
    """Run one certificate's pipeline to the end, or to the budget: ``(doc, diff)``."""
    path = tune_path(doc["tune"], args.hvsc)
    if path is None:
        return None, ["%s unavailable (no HVSC tree, no cache)" % doc["tune"]]
    out = Path(args.out) / name
    out.mkdir(parents=True, exist_ok=True)
    rc = pipeline.MORE
    while rc == pipeline.MORE:
        left = args.budget - (time.process_time() - t0)
        if left <= 1.0:
            return None, None
        argv = plan(doc, out, path, min(left, args.chunk), bool(args.ghidra_dir))
        rc = pipeline.run(pipeline.parser("tuneprog_recert.py").parse_args(argv), log=_quiet)
    made = out / "certificate.json"
    if not made.is_file():
        return None, ["no certificate written (exit %d)" % rc]
    got = json.loads(made.read_text())
    return got, diff(doc, got)


def patch(want, got, path=()):
    """``want`` with every field ``got`` really changed: the ignored ones stay put."""
    if path in IGNORE:
        return want
    if isinstance(want, dict) and isinstance(got, dict):
        out = {k: patch(want[k], got[k], path + (k,)) for k in want if k in got}
        out.update({k: v for k, v in got.items() if k not in want})
        return out
    if isinstance(want, list) and isinstance(got, list) and len(want) == len(got):
        return [patch(a, b, path + (str(i),)) for i, (a, b) in enumerate(zip(want, got))]
    return got


def row(name, doc, state):
    """One table row: what was replayed, what came back, and whether it matches."""
    subs = doc["subtunes"]
    s = subs[0]
    st = state.get(name)
    status = "pending" if st is None else ("DIFF %d" % len(st["diff"]) if st["diff"] else "ok")
    return COLS % (
        name,
        doc["tune"],
        "all(%d)" % len(subs) if len(subs) > 1 else "song %d" % s["song"],
        s["ticks"],
        s["period"] if s["period"] is not None else "-",
        "complete" if s["complete"] else "horizon",
        status,
    )


def table(certs, state):
    """The report: one row per certificate, then every mismatch in full."""
    head = COLS % ("certificate", "tune", "subtune", "ticks", "period", "closure", "reproduced")
    out = [head, "-" * len(head)] + [row(n, d, state) for n, d in certs]
    bad = [(n, state[n]["diff"]) for n, _d in certs if state.get(n) and state[n]["diff"]]
    for n, ds in bad:
        out += ["", "%s:" % n] + ["  " + d for d in ds]
    left = [n for n, _d in certs if n not in state]
    out += [
        "",
        "%d/%d reproduced, %d mismatched, %d pending"
        % (len(certs) - len(bad) - len(left), len(certs), len(bad), len(left)),
    ]
    return "\n".join(out)


def oracle_row(name, out, gdir, tol):
    """The three Ghidra oracles for one certificate, joined and localised."""
    if not (gdir / "stats.json").is_file():
        return {"name": name, "flags": [], "note": "no export"}
    doc = ghidra_compare.compare(out, gdir, tol)
    (gdir / "comparison.json").write_text(json.dumps(doc, indent=1))
    cov, emu = doc.get("coverage") or {}, doc.get("emulate") or {}
    return {
        "name": name,
        "flags": [f["entry"] + " " + f["detail"] for f in doc["flags"]],
        "uncovered": cov.get("uncovered_sites", "-"),
        "merged": len(doc["alignment"]["merged"]),
        "partial": sum(1 for r in doc["procs"] if r["verdict"] == "ghidra_partial"),
        "agree": emu.get("agree"),
        "note": "" if emu.get("agree") is not False else str(emu.get("sid_mismatches", [])[:1]),
    }


def oracles(certs, args):
    """Every certificate's oracle row, then the ``ours_bigger`` verdict."""
    rows = [
        oracle_row(n, Path(args.out) / n, Path(args.ghidra_dir) / n, args.tol) for n, _d in certs
    ]
    head = OCOLS % ("certificate", "uncovered", "partial", "merged", "emulate", "ours_bigger")
    out = [head, "-" * len(head)]
    for r in rows:
        out.append(
            OCOLS
            % (
                r["name"],
                r.get("uncovered", "-"),
                r.get("partial", "-"),
                r.get("merged", "-"),
                {True: "agree", False: "DIFFER", None: "-"}[r.get("agree")],
                ", ".join(r["flags"]) or r["note"] or "0",
            )
        )
    bigger = sum(len(r["flags"]) for r in rows)
    out += [
        "",
        "%d/%d with a Ghidra export, ours_bigger %d, emulate disagreements %d"
        % (
            sum(1 for r in rows if r["note"] != "no export"),
            len(rows),
            bigger,
            sum(1 for r in rows if r.get("agree") is False),
        ),
    ]
    print("\n".join(out))
    return bigger


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tuneprog_recert.py", description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="out/recert", help="working directory (default out/recert)")
    ap.add_argument("--certs", default=str(CERTS), help="directory of committed certificates")
    ap.add_argument("--only", action="append", help="reproduce only these certificates")
    ap.add_argument("--hvsc", help="HVSC root (default $HVSC, then the tune cache)")
    ap.add_argument("--resume", action="store_true", help="keep what earlier invocations did")
    ap.add_argument("--update", action="store_true", help="write what was reproduced back")
    ap.add_argument("--budget", type=float, default=45.0, help="CPU seconds per invocation")
    ap.add_argument("--chunk", type=float, default=20.0, help="CPU seconds per pipeline call")
    ap.add_argument("--shard", help="I/N: reproduce every Nth certificate, offset I")
    ap.add_argument(
        "--ghidra-dir", help="headless Ghidra exports per certificate; runs the oracles"
    )
    ap.add_argument("--tol", type=float, default=ghidra_compare.TOL, help="complexity tolerance")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    certs = [
        (p.stem, json.loads(p.read_text()))
        for p in sorted(Path(args.certs).glob("*.json"))
        if not args.only or p.stem in args.only
    ]
    if args.shard:
        i, n = (int(x) for x in args.shard.split("/"))
        certs = certs[i::n]
    statefile = out / "recert.json"
    state = json.loads(statefile.read_text()) if args.resume and statefile.is_file() else {}
    t0 = time.process_time()
    for name, doc in certs:
        if name in state:
            continue
        got, ds = replay(name, doc, args, t0)
        if ds is None:
            break
        note = []
        if args.update and got is not None and ds:
            (Path(args.certs) / (name + ".json")).write_text(
                json.dumps(patch(doc, got), indent=1, sort_keys=True)
            )
            ds, note = [], ds
        state[name] = {
            "diff": ds,
            "updated": note,
            "ticks": None if got is None else got["subtunes"][0]["ticks"],
        }
        statefile.write_text(json.dumps(state, indent=1, sort_keys=True))
    statefile.write_text(json.dumps(state, indent=1, sort_keys=True))
    print(table(certs, state))
    if any(n not in state for n, _d in certs):
        return pipeline.MORE
    bad = any(state[n]["diff"] for n, _d in certs)
    if args.ghidra_dir:
        bad = oracles(certs, args) > 0 or bad
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
