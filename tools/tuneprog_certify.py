#!/usr/bin/env python3
"""Certify a SID tune: trace -> lift -> regions -> procedures -> IR -> S4 -> Python -> verify.

Every stage's artefacts land in ``--out DIR`` (``trace.json``/``trace.npz``,
``regions.json``, ``procs.json``, ``tuneprog.S4.json``, ``tuneprog.py``,
``certificate.json``) and the long stages (tracing and verification) are
chunked: each invocation works for ``--budget`` CPU seconds, pickles its state
and exits 2 when there is more to do, so a 149k-call certificate is a handful of
short runs::

    until python3 tools/tuneprog_certify.py TUNE.sid --out out/tune --until-period --resume
    do :; done

``--sid-model`` pins ``$D41B`` bit 0 (the register a tune reads at init to tell a
6581 from an 8580), which certifies the tune under either model.
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
from deity_informant.tuneprog import emit, ir, ssa, verify as V  # noqa: E402
from deity_informant.tuneprog.build import build_ir  # noqa: E402
from deity_informant.tuneprog.cfg import build_procs, procs_json  # noqa: E402
from deity_informant.tuneprog.idioms import rewrite  # noqa: E402
from deity_informant.tuneprog.lift import lift_trace  # noqa: E402
from deity_informant.tuneprog.machine import find_entries  # noqa: E402
from deity_informant.tuneprog.regions import build_regions  # noqa: E402
from deity_informant.tuneprog.trace import Tracer  # noqa: E402
from deity_informant.tuneprog.tracedata import Trace  # noqa: E402

MODEL_D41B = {"6581": 0x00, "8580": 0x01}
MORE = 2  # exit code: work remains, invoke again with --resume


def _state(out):
    p = out / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"stage": "trace", "calls": 0}


def _save_state(out, st):
    (out / "state.json").write_text(json.dumps(st, indent=1, sort_keys=True))


def _target(args, entry):
    if args.calls:
        return args.calls
    if args.seconds:
        clock = V.NTSC if "ntsc" in entry.source else V.PAL
        return int(args.seconds * clock / entry.cycles_per_tick)
    return args.max_calls


def stage_trace(args, out, st, t0):
    """Trace in chunks; returns True when the horizon (or a state repeat) is reached."""
    data = Path(args.sid).read_bytes()
    img, schedule = find_entries(data)
    entry = schedule[0]
    resume = out / "tracer.pkl"
    if args.resume and resume.exists():
        tr = Tracer.load(resume)
    else:
        override = {0xD41B: MODEL_D41B[args.sid_model]} if args.sid_model else None
        tr = Tracer(img, entry, song=args.song - 1 if args.song else None, override=override)
        tr.run_init()
    target = _target(args, entry)
    while tr.calls_done < target and not (args.until_period and tr.period is not None):
        tr.run_calls(min(args.chunk, target - tr.calls_done))
        st["calls"] = tr.calls_done
        print(
            "  traced %d calls (%.0fs cpu)%s"
            % (tr.calls_done, time.process_time() - t0, "" if tr.period is None else " period!"),
            flush=True,
        )
        if time.process_time() - t0 > args.budget:
            break
    done = tr.calls_done >= target or (args.until_period and tr.period is not None)
    tr.save(resume)
    if not done:
        return False
    trace = tr.trace()
    if args.until_period and tr.period is not None:
        st["calls"] = tr.first_repeat + 1
    else:
        st["calls"] = tr.calls_done
    trace.save(out)
    st["period"] = tr.period
    st["first_repeat"] = tr.first_repeat
    st["stage"] = "front"
    return True


def stage_front(args, out, st):
    """Lift, type storage, build procedures, build the IR, run S4, emit Python."""
    trace = Trace.load(out)
    lifted = lift_trace(trace)
    regions = build_regions(trace, lifted)
    procs = build_procs(trace, lifted, regions)
    (out / "regions.json").write_text(json.dumps([r.to_dict() for r in regions]))
    (out / "procs.json").write_text(json.dumps(procs_json(procs)))
    prog = build_ir(
        trace,
        lifted,
        regions,
        procs,
        meta={"name": Path(args.sid).name, "sid_model": args.sid_model},
    )
    prog.save(out / "tuneprog.S2.json")
    ssa.simplify(prog, rewrite)
    prog.save(out / "tuneprog.S4.json")
    (out / "tuneprog.py").write_text(emit.emit_python(prog))
    st["sites"] = len(trace.sites)
    st["regions"] = len(regions)
    st["procs"] = len(procs)
    st["stmts"] = sum(len(b.stmts) for p in prog.procs.values() for b in p.blocks.values())
    st["stage"] = "verify"
    return prog


def stage_verify(args, out, st, t0, prog=None):
    """Verify in chunks against the trace; writes ``certificate.json`` when finished."""
    trace = Trace.load(out)
    prog = prog or ir.Tuneprog.load(out / "tuneprog.S4.json")
    src = (out / "tuneprog.py").read_text()
    ref = V.Reference(trace, st["calls"])
    v = V.Verifier(prog, ref, src=src)
    resume = out / "verify.pkl"
    if args.resume and resume.exists():
        v.restore(pickle.loads(resume.read_bytes()))
    while v.call < ref.calls and v.div is None:
        v.run(ref.calls, budget=v.seconds + max(1.0, args.budget - (time.process_time() - t0)))
        print("  verified %d/%d calls (%.0fs cpu)" % (v.call, ref.calls, v.seconds), flush=True)
        if time.process_time() - t0 > args.budget:
            break
    resume.write_bytes(pickle.dumps(v.state(), protocol=pickle.HIGHEST_PROTOCOL))
    if v.call < ref.calls and v.div is None:
        return False
    if v.div is None and args.prefix:
        p = V.prefix_check(prog, ref, min(args.prefix, v.call))
        if p.div is not None:
            v.div = dict(p.div, executor="interp")
    cost = {"trace_calls": st["calls"], "sites": st.get("sites"), "regions": st.get("regions")}
    cert = V.certify(prog, v, prefix=args.prefix, extra=cost)
    emit.write_certificate(out / "certificate.json", cert)
    st["stage"] = "done"
    st["divergence"] = v.div
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sid")
    ap.add_argument("--out", required=True)
    ap.add_argument("--song", type=int, help="1-based subtune (default: the header's)")
    ap.add_argument("--calls", type=int, help="horizon in ticks")
    ap.add_argument("--seconds", type=float, help="horizon in seconds of music")
    ap.add_argument("--until-period", action="store_true", help="trace to the first state repeat")
    ap.add_argument("--max-calls", type=int, default=400_000)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--sid-model", choices=sorted(MODEL_D41B), help="pin $D41B bit 0")
    ap.add_argument("--budget", type=float, default=45.0, help="CPU seconds per invocation")
    ap.add_argument("--chunk", type=int, default=4000, help="ticks per progress step")
    ap.add_argument("--prefix", type=int, default=2000, help="calls to re-run on the interpreter")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    st = _state(out) if args.resume else {"stage": "trace", "calls": 0}
    t0 = time.process_time()
    prog = None
    try:
        if st["stage"] == "trace" and not stage_trace(args, out, st, t0):
            return _finish(out, st, t0, MORE)
        if st["stage"] == "front":
            prog = stage_front(args, out, st)
        if st["stage"] == "verify" and not stage_verify(args, out, st, t0, prog):
            return _finish(out, st, t0, MORE)
    finally:
        _save_state(out, st)
    print(json.dumps(json.loads((out / "certificate.json").read_text())["subtunes"][0], indent=1))
    return 1 if st.get("divergence") else 0


def _finish(out, st, t0, code):
    print(
        "  stage %s incomplete after %.0fs cpu; rerun with --resume"
        % (st["stage"], time.process_time() - t0)
    )
    _save_state(out, st)
    return code


if __name__ == "__main__":
    sys.exit(main())
