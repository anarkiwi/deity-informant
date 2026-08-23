#!/usr/bin/env python3
"""Reproduce every certificate in ``docs/certificates/`` and diff it field for field.

A certificate records its own run (tune, subtune, horizon, SID model, stage), so
the set replays through the pipeline in ``--budget`` chunks; each invocation
prints the table and exits :data:`MORE` while work is left -- 3, not the
pipeline's 2, so a caller's loop cannot mistake argparse's usage exit for another
chunk. Timestamp and timings excepted.
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
from deity_informant.tuneprog import ghidra_compare, ghidra_facts, pipeline, tunes  # noqa: E402

MORE = 3  # "invoke me again": distinct from argparse's and the pipeline's exit 2
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


def plan(doc, out, path, budget):
    """The pipeline command line one certificate records."""
    subs = doc["subtunes"]
    argv = [str(path), "--out", str(out), "--resume", "--budget", "%.1f" % budget]
    argv += ["--songs", "all"] if len(subs) > 1 else ["--song", str(subs[0]["song"])]
    argv += horizon(subs)
    if doc["sid_model"]:
        argv += ["--sid-model", doc["sid_model"]]
    if doc.get("stage") != "S6":
        argv.append("--no-text")
    return argv


def facts(out):
    """Export ``OUT/ghidra`` when it is missing or older than the trace it comes from.

    The oracle runs over whatever this leaves, so it cannot depend on the pipeline
    having reached its print stage in *this* invocation: a resumed certificate is
    already finished and prints nothing.
    """
    src, dst = out / "trace.npz", out / "ghidra" / "ghidra_facts.json"
    if src.is_file() and (not dst.is_file() or dst.stat().st_mtime < src.stat().st_mtime):
        ghidra_facts.export(out)


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
        argv = plan(doc, out, path, min(left, args.chunk))
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


def _flag(name, entry, detail, known):
    """One oracle failure, unless ``CERT:ENTRY`` is a recorded row (``--known``)."""
    return [] if "%s:%s" % (name, entry) in known else ["%s %s" % (entry, detail)]


def oracle_row(name, out, gdir, tol, known=()):
    """The three Ghidra oracles for one certificate, joined and localised.

    A certificate with no export is a failure of the oracle run, not a blank row:
    the whole set having run is what the summary counts.
    """
    if not (gdir / "stats.json").is_file():
        return {
            "name": name,
            "export": False,
            "note": "no export",
            "flags": _flag(name, "export", "missing", known),
        }
    doc = ghidra_compare.compare(out, gdir, tol)
    (gdir / "comparison.json").write_text(json.dumps(doc, indent=1))
    cov, emu = doc.get("coverage") or {}, doc.get("emulate") or {}
    # an emulator that errored made no comparison: that is its own verdict, and
    # its string is the note -- sid_mismatches is empty in exactly that case
    err = emu.get("error")
    bigger = [f for r in doc["flags"] for f in _flag(name, r["entry"], r["detail"], known)]
    return {
        "name": name,
        "export": True,
        "bigger": len(bigger),
        "flags": bigger + (_flag(name, "emulate", err, known) if err else []),
        "uncovered": cov.get("uncovered_sites", "-"),
        "merged": len(doc["alignment"]["merged"]),
        "partial": sum(1 for r in doc["procs"] if r["verdict"] == "ghidra_partial"),
        "agree": None if err else emu.get("agree"),
        "error": err,
        "note": err
        or (str(emu.get("sid_mismatches", [])[:1]) if emu.get("agree") is False else ""),
    }


def oracles(certs, args):
    """Every certificate's oracle row, then the count of what has to be zero."""
    known = set(args.known or ())
    rows = [
        oracle_row(n, Path(args.out) / n, Path(args.ghidra_dir) / n, args.tol, known)
        for n, _d in certs
    ]
    head = OCOLS % ("certificate", "uncovered", "partial", "merged", "emulate", "flags")
    out = [head, "-" * len(head)]
    for r in rows:
        emulate = (
            "ERROR"
            if r.get("error")
            else {True: "agree", False: "DIFFER", None: "-"}[r.get("agree")]
        )
        out.append(
            OCOLS
            % (
                r["name"],
                r.get("uncovered", "-"),
                r.get("partial", "-"),
                r.get("merged", "-"),
                "-" if not r["export"] else emulate,
                ", ".join(r["flags"]) or r.get("note") or "0",
            )
        )
    flags = sum(len(r["flags"]) for r in rows)
    out += [
        "",
        "%d/%d with a Ghidra export, ours_bigger %d, emulate disagreements %d, errors %d,"
        " flagged %d"
        % (
            sum(1 for r in rows if r["export"]),
            len(rows),
            sum(r.get("bigger", 0) for r in rows),
            sum(1 for r in rows if r.get("agree") is False),
            sum(1 for r in rows if r.get("error")),
            flags,
        ),
    ]
    print("\n".join(out))
    return flags


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
        "--ghidra-facts", action="store_true", help="export OUT/CERT/ghidra as it replays"
    )
    ap.add_argument(
        "--ghidra-dir",
        help="headless Ghidra exports per certificate; exports the facts and runs the oracles"
        " over what the headless run left there",
    )
    ap.add_argument("--tol", type=float, default=ghidra_compare.TOL, help="complexity tolerance")
    ap.add_argument(
        "--known",
        action="append",
        help="CERT:ENTRY whose oracle flag is a recorded row (ENTRY is a procedure,"
        " 'export' for a certificate with no export, or 'emulate' for an emulator error)",
    )
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
    want_facts = args.ghidra_facts or args.ghidra_dir
    for name, doc in certs:
        if name in state:
            if want_facts:
                facts(out / name)
            continue
        got, ds = replay(name, doc, args, t0)
        if ds is None:
            break
        if want_facts and got is not None:
            facts(out / name)
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
        return MORE
    bad = any(state[n]["diff"] for n, _d in certs)
    if args.ghidra_dir:
        bad = oracles(certs, args) > 0 or bad
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
