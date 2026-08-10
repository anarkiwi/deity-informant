"""The eqlift measurement over the exemplar set, off dumped frameprog texts.

A package edit costs one cold decompile sweep, so the lift is measured from texts
dumped once: ``dump`` writes them, ``run`` lifts and proves each tune, ``report``
rolls the artifact up into the review's gate. The transitional liveness path is
deleted (adoption §5), so a landing is measured against the **recorded baseline**
artifact rather than against a second code path.
"""

import argparse
import hashlib
import json
import multiprocessing as mp
import resource
import signal
import sys
import time
from pathlib import Path

import _sweep
import exemplars

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

MODELS = ROOT / "out" / "eqlift-models"

USAGE = """\
  python tools/eqlift_measure.py dump                          # the exemplars' frameprog texts
  python tools/eqlift_measure.py run --prove                   # every tune, all sites proved
  python tools/eqlift_measure.py run --tunes Alioth             # one tune
  python tools/eqlift_measure.py report out/eqlift_measure.json --baseline out/prev.json"""

_FIELDS = ("lines", "stores", "d_lines", "d_stores", "extracted", "changed", "proved")


def model_name(tune):
    """The dumped text's file stem: a tune id with its separators flattened."""
    return tune.replace("/", "~")


def stores(text):
    """Emitted store statements: a destination that indexes or names memory."""
    return sum(1 for ln in text.splitlines() if "=" in ln and "[" in ln.split("=")[0])


def dump(entry, models):
    """One exemplar's frameprog text, written off the artifact cache."""
    from deity_informant import frameprog
    from deity_informant.c64 import load_psid

    try:
        signal.alarm(_sweep.CAP_S)
        sid, sub, secs = entry
        mem, _load, init, play = load_psid(Path(sid).read_bytes())
        mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
        model, _ev = _sweep.decompile(mem, init, play, int(secs * 50), sub)
        text = frameprog.dumps(frameprog.program(model))
        path = Path(models) / (model_name(_sweep.tune_id(sid)) + ".fp")
        path.write_text(text, encoding="utf-8")
        return {**_sweep.row_head(entry), "bytes": len(text)}
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def lift(path, prove, extents=None):
    """One tune: text, size, extraction fallbacks, and the §6 proofs.

    ``extents`` is this tune's 2b row, which stage 3d's read closure bounds a deref
    with. A proof that refuses is recorded as this row's named refusal -- the
    anti-vacuity guard and a failed equivalence are results the review reads."""
    from deity_informant import eqlift_mem
    from deity_informant import frameprog

    tune = Path(path).stem.replace("~", "/")
    row = {"tune": tune, "name": tune.rsplit("/", 1)[-1]}
    try:
        signal.alarm(_sweep.CAP_S)
        model = frameprog.block_model(frameprog.parse(Path(path).read_text(encoding="utf-8")))
        proofs, stats = ([] if prove else None), {}
        t0 = time.monotonic()
        text, _ = eqlift_mem.emit(model, proofs=proofs, stats=stats, extents=extents)
        row.update(
            wall_s=round(time.monotonic() - t0, 1),
            lines=len(text.splitlines()),
            bytes=len(text),
            stores=stores(text),
            sha=hashlib.sha256(text.encode()).hexdigest(),
            sites=stats.get("sites", 0),
            fallback=stats.get("extract_fallback", 0),
            scratch=stats.get("scratch", 0),
            in_join=stats.get("in_join", 0),
            label_reset=stats.get("label_reset", 0),
            text=text,
        )
        if prove:
            t1 = time.monotonic()
            try:
                row["proved"] = sum(eqlift_mem.verify_sites(p) for p in proofs)
            except AssertionError as exc:
                row["refused"] = str(exc)
            row["changed"] = sum(1 for p in proofs for a, b in p.get("pairs", ()) if a != b)
            row["proof_s"] = round(time.monotonic() - t1, 1)
        return row
    except Exception as exc:  # pylint: disable=broad-except
        return {**row, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _lift(job):
    return lift(*job)


def _arm(cap_gb):
    """Pool initialiser: the build cap, plus an address-space cap so a saturation that
    outruns its budget is one worker's ``MemoryError`` row and not the whole run."""
    _sweep.arm()
    if cap_gb:
        resource.setrlimit(resource.RLIMIT_AS, (cap_gb << 30, cap_gb << 30))


def baseline_rows(path):
    """``{tune: row}`` of a previously recorded run artifact, or {} without one."""
    if not path:
        return {}
    art = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r["tune"]: r for r in art["rows"] if "error" not in r}


def rollup(rows, base=None):
    """Gate one run: no fault, no refusal, no unproved change, no growth on baseline.

    Without a baseline the deltas are zero and only the faults gate; with one, a tune
    emitting more lines or stores than the recorded run is a regression, and the tunes
    whose sha moved are what the §4 review reads."""
    base = base or {}
    recs, faults = [], []
    for r in sorted(rows, key=lambda r: r["tune"]):
        if "error" in r:
            faults.append(r["tune"])
            continue
        was = base.get(r["tune"])
        recs.append(
            {
                "tune": r["tune"],
                "lines": r["lines"],
                "stores": r["stores"],
                "d_lines": 0 if was is None else r["lines"] - was["lines"],
                "d_stores": 0 if was is None else r["stores"] - was["stores"],
                "identical": was is not None and was["sha"] == r["sha"],
                "extracted": r.get("sites", 0),
                "changed": r.get("changed", 0),
                "proved": r.get("proved", 0),
                "fallback": r.get("fallback", 0),
                "wall_s": r["wall_s"],
            }
        )
    out = {
        "tunes": len(recs),
        "faults": faults,
        "refused": sorted(r["tune"] for r in rows if "refused" in r),
        "regressed": sorted(r["tune"] for r in recs if r["d_lines"] > 0 or r["d_stores"] > 0),
        "unproved": sorted(r["tune"] for r in recs if r["changed"] and not r["proved"]),
        "identical": sum(1 for r in recs if r["identical"]),
        "fallback_tunes": sorted(r["tune"] for r in recs if r["fallback"]),
        "totals": {f: sum(r[f] for r in recs) for f in _FIELDS},
        "rows": recs,
        "baseline": bool(base),
    }
    out["clean"] = not (out["faults"] or out["refused"] or out["regressed"] or out["unproved"])
    return out


def render(got):
    """The per-tune table plus the gate line, as the review records them."""
    head = ("tune", "lines", "stores", "dlin", "dsto", "extr", "chg", "proved", "fb", "wall")
    lines = ["%-44s %6s %6s %5s %5s %6s %6s %6s %4s %6s" % head]
    for r in got["rows"]:
        lines.append(
            "%-44s %6d %6d %5d %5d %6d %6d %6d %4d %6.1f %s"
            % (
                r["tune"][-44:],
                r["lines"],
                r["stores"],
                r["d_lines"],
                r["d_stores"],
                r["extracted"],
                r["changed"],
                r["proved"],
                r["fallback"],
                r["wall_s"],
                "identical" if r["identical"] else "MOVED",
            )
        )
    t = got["totals"]
    lines.append(
        "%-44s %6d %6d %5d %5d %6d %6d %6d"
        % (
            "TOTAL (%d tunes, %d identical)" % (got["tunes"], got["identical"]),
            *(t[f] for f in _FIELDS),
        )
    )
    lines.append(json.dumps({k: v for k, v in got.items() if k not in ("rows", "totals")}))
    return "\n".join(lines)


def _dump_main(args):
    models = Path(args.models)
    models.mkdir(parents=True, exist_ok=True)
    names = [t.split(exemplars.M)[1] for t in exemplars.EXEMPLARS]
    ents = _sweep.entries(args.tunes.split(",") if args.tunes else names)
    if not ents:
        sys.exit("no cached tune matched")
    with mp.Pool(min(len(ents), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.starmap(dump, [(e, str(models)) for e in ents]))
    bad = [r for r in rows if "error" in r]
    print(json.dumps({"dumped": len(rows) - len(bad), "into": str(models), "refused": bad}))
    return 1 if bad else 0


def extent_rows(path):
    """``{tune: {pointer cell: block bases}}`` from a 2b artifact, or {} without one."""
    from deity_informant import ptrextent

    if not path:
        return {}
    art = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r["tune"]: ptrextent.mapped_blocks(r["extents"]["records"]) for r in art["rows"]}


def _run_main(args):
    paths = sorted(Path(args.models).glob("*.fp"))
    if args.tunes:
        want = args.tunes.split(",")
        paths = [p for p in paths if any(w in p.stem for w in want)]
    if not paths:
        sys.exit("no dumped model matched; run `dump` first")
    ext = extent_rows(args.extents)
    jobs = [(str(p), args.prove, ext.get(p.stem.replace("~", "/"))) for p in paths]
    t0 = time.monotonic()
    rows = []
    with mp.Pool(min(len(jobs), args.procs), _arm, (args.cap_gb,)) as pool:
        for r in pool.imap_unordered(_lift, jobs):
            rows.append(r)
            print(
                "%3d/%d %-46s %s"
                % (
                    len(rows),
                    len(jobs),
                    r["name"],
                    r.get("error") or r.get("refused") or r["wall_s"],
                ),
                file=sys.stderr,
                flush=True,
            )
    if args.texts:
        out = Path(args.texts)
        out.mkdir(parents=True, exist_ok=True)
        for r in rows:
            if "text" in r:
                (out / (model_name(r["tune"]) + ".txt")).write_text(r["text"], encoding="utf-8")
    for r in rows:
        r.pop("text", None)
    got = rollup(rows, baseline_rows(args.baseline))
    art = {"wall_s": round(time.monotonic() - t0, 1), "rows": rows, "rollup": got}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(art, indent=1), encoding="utf-8")
    print(render(got))
    return 0 if got["clean"] else 1


def _report_main(args):
    rows = json.loads(Path(args.artifact).read_text(encoding="utf-8"))["rows"]
    got = rollup(rows, baseline_rows(args.baseline))
    print(render(got))
    return 0 if got["clean"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="write the exemplars' frameprog texts")
    d.add_argument("--tunes", help="comma-separated tune ids or stems; default the exemplar set")
    r = sub.add_parser("run", help="lift every dumped text")
    r.add_argument("--tunes", help="substring filter over the dumped stems")
    r.add_argument("--prove", action="store_true", help="run the adoption §6 site proofs")
    r.add_argument("--texts", help="directory to write every emitted text into")
    r.add_argument("-o", "--out", default=str(ROOT / "out" / "eqlift_measure.json"))
    r.add_argument("--cap-gb", type=int, default=6, help="per-worker address-space cap; 0 off")
    r.add_argument("--extents", help="2b observed-extent artifact the read closure reads")
    p = sub.add_parser("report", help="roll a run artifact up")
    p.add_argument("artifact")
    for q in (r, p):
        q.add_argument("--baseline", help="a previously recorded run artifact to diff against")
    for q in (d, r):
        q.add_argument("-j", "--procs", type=int, default=24)
        q.add_argument("--models", default=str(MODELS))
    args = ap.parse_args(argv)
    return {"dump": _dump_main, "run": _run_main, "report": _report_main}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
