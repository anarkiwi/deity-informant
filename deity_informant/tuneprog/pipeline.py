"""The end-to-end driver: trace -> lift -> regions -> procs -> IR -> S4 -> verify -> text.

Every stage's artefacts land in one output directory and the long stages are
chunked against a CPU budget, so a 149k-call certificate is a handful of short
runs (:func:`main` returns ``MORE`` while work remains). ``tools/tuneprog_certify.py``
and ``deity-informant tuneprog`` are both thin wrappers around it.
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

from . import emit, fold, ir, printer, recover, ssa, structure, texture, unroll, verify as V, word
from .build import build_ir
from .cfg import build_procs, procs_json
from .idioms import rewrite
from .lift import lift_trace
from .machine import find_entries
from .regions import build_regions
from .trace import Tracer
from .tracedata import Trace

MODEL_D41B = {"6581": 0x00, "8580": 0x01}
MORE = 2
STAGES = ("trace", "front", "verify", "print", "done")


def add_args(ap):
    """The tuneprog options, shared by the tool and the ``deity-informant`` subcommand."""
    ap.add_argument("sid")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--song", type=int, help="1-based subtune (default: the header's)")
    ap.add_argument("--calls", type=int, help="horizon in ticks")
    ap.add_argument("--seconds", type=float, help="horizon in seconds of music")
    ap.add_argument("--until-period", action="store_true", help="trace to the first state repeat")
    ap.add_argument("--max-calls", type=int, default=400_000)
    ap.add_argument("--resume", action="store_true", help="continue a chunked run")
    ap.add_argument("--sid-model", choices=sorted(MODEL_D41B), help="pin $D41B bit 0")
    ap.add_argument("--no-verify", action="store_true", help="skip S8 (no certificate)")
    ap.add_argument("--no-text", action="store_true", help="skip S5/S6 and tuneprog.md")
    ap.add_argument("--budget", type=float, default=45.0, help="CPU seconds per invocation")
    ap.add_argument("--chunk", type=int, default=4000, help="ticks per progress step")
    ap.add_argument("--prefix", type=int, default=2000, help="calls to re-run on the interpreter")
    return ap


def parser(prog="tuneprog"):
    """The command line both entry points share."""
    return add_args(argparse.ArgumentParser(prog=prog, description=__doc__.splitlines()[0]))


def _state(out, resume):
    p = out / "state.json"
    return json.loads(p.read_text()) if resume and p.exists() else {"stage": "trace", "calls": 0}


def _target(args, entry):
    if args.calls:
        return args.calls
    if args.seconds:
        clock = V.NTSC if "ntsc" in entry.source else V.PAL
        return int(args.seconds * clock / entry.cycles_per_tick)
    return args.max_calls


def stage_trace(args, out, st, t0, log=print):
    """Trace in chunks; True when the horizon (or a state repeat) is reached."""
    img, schedule = find_entries(Path(args.sid).read_bytes())
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
        log(
            "  traced %d calls (%.0fs cpu)%s"
            % (tr.calls_done, time.process_time() - t0, "" if tr.period is None else " period!")
        )
        if time.process_time() - t0 > args.budget:
            break
    done = tr.calls_done >= target or (args.until_period and tr.period is not None)
    tr.save(resume)
    if not done:
        return False
    trace = tr.trace()
    st["calls"] = tr.first_repeat + 1 if args.until_period and tr.period else tr.calls_done
    trace.save(out)
    st.update(period=tr.period, first_repeat=tr.first_repeat, stage="front")
    return True


def build(trace, name=None, sid_model=None):
    """Front end -> IR -> S4: the certified program, plus its front-end products."""
    lifted = lift_trace(trace)
    regions = build_regions(trace, lifted)
    procs = build_procs(trace, lifted, regions)
    prog = build_ir(trace, lifted, regions, procs, meta={"name": name, "sid_model": sid_model})
    ssa.simplify(
        prog, rewrite, folds=ssa.Folds(trace.image_post_init, trace.cells, trace.written_play)
    )
    return prog, regions, procs


def stage_front(args, out, st):
    """Lift, type storage, build procedures, build the IR, run S4, emit Python."""
    trace = Trace.load(out)
    prog, regions, procs = build(trace, Path(args.sid).name, args.sid_model)
    (out / "regions.json").write_text(json.dumps([r.to_dict() for r in regions]))
    (out / "procs.json").write_text(json.dumps(procs_json(procs)))
    prog.save(out / "tuneprog.S4.json")
    (out / "tuneprog.py").write_text(emit.emit_python(prog))
    st.update(
        sites=len(trace.sites),
        regions=len(regions),
        procs=len(procs),
        stmts=sum(len(b.stmts) for p in prog.procs.values() for b in p.blocks.values()),
        stage="verify",
    )
    return prog


def stage_verify(args, out, st, t0, prog=None, log=print):
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
        log("  verified %d/%d calls (%.0fs cpu)" % (v.call, ref.calls, v.seconds))
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
    emit.write_certificate(
        out / "certificate.json", V.certify(prog, v, prefix=args.prefix, extra=cost)
    )
    st.update(stage="print", divergence=v.div)
    return True


def structure_json(prog, structured, names):
    """The S5 annotation: the structured shape of every procedure."""
    return {
        "procs": {n: [_node(x) for x in body] for n, body in structured.items()},
        "phase": None if names.phase is None else {"region": names.phase[0]},
    }


def _node(n):
    """One structured node as data (children included, expressions elided)."""
    k = type(n).__name__.lower()
    d = {"kind": k}
    for f in ("label", "src", "count", "var", "kind", "values", "scale"):
        if hasattr(n, f) and f != "kind":
            d[f] = list(n.values) if f == "values" else getattr(n, f)
    if k == "jump" or k == "exit":
        d["kind"] = "%s:%s" % (k, n.kind)
    for f in ("then", "els", "body"):
        if hasattr(n, f):
            d[f] = [_node(x) for x in getattr(n, f)]
    if k == "case":
        d["cases"] = [[v, [_node(x) for x in b]] for v, b in n.cases]
    if k == "blk":
        d["stmts"] = len(n.stmts)
    return d


def present(prog):
    """S5 + S6 over a copy of the certified IR: ``(view, structured, names)``.

    Structuring, texture removal, 16-bit views, outlining and copy folding; the
    argument is never touched.
    """
    view = structure.view(prog, printer.needed(prog)[0])
    texture.clean(view)
    structure.inline(view, printer.needed(view)[0])
    texture.tidy(view)
    names = recover.recover(view, structure.structure(view))
    word.fold16(view, names)
    fold.outline(view, names, *printer.needed(view))
    st = structure.structure(view)
    live, params = printer.needed(view)
    unroll.unroll(st, live, fold.livearg(view, params))
    return view, st, names


def stage_print(args, out, prog=None):
    """S5 + S6 over the certified IR, then ``tuneprog.md`` (design section 4 text form)."""
    prog = prog or ir.Tuneprog.load(out / "tuneprog.S4.json")
    cert = out / "certificate.json"
    doc = json.loads(cert.read_text()) if cert.exists() else None
    if doc is not None and doc.get("stage") == "S4":
        doc["stage"] = "S6"
        doc["presentation"] = "S5/S6 annotate the certified S4 IR; the program is unchanged"
        emit.write_certificate(cert, doc)
    view, st, names = present(prog)
    (out / "tuneprog.S5.json").write_text(json.dumps(structure_json(view, st, names)))
    (out / "tuneprog.S6.json").write_text(json.dumps(names.to_dict(), indent=1))
    (out / "tuneprog.md").write_text(printer.render(view, st, names, doc))
    return view, st, names


def run(args, log=print):
    """Drive the stages under ``args``; returns the process exit code."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    st = _state(out, args.resume)
    t0 = time.process_time()
    prog = None
    try:
        if st["stage"] == "trace" and not stage_trace(args, out, st, t0, log):
            return _more(st, t0, log)
        if st["stage"] == "front":
            prog = stage_front(args, out, st)
        if args.no_verify and st["stage"] == "verify":
            st["stage"] = "print"
        if st["stage"] == "verify" and not stage_verify(args, out, st, t0, prog, log):
            return _more(st, t0, log)
        if st["stage"] == "print":
            if not args.no_text:
                stage_print(args, out, prog)
            st["stage"] = "done"
    finally:
        (out / "state.json").write_text(json.dumps(st, indent=1, sort_keys=True))
    doc = out / "certificate.json"
    if doc.exists():
        log(json.dumps(json.loads(doc.read_text())["subtunes"][0], indent=1))
    return 1 if st.get("divergence") else 0


def _more(st, t0, log):
    log(
        "  stage %s incomplete after %.0fs cpu; rerun with --resume"
        % (st["stage"], time.process_time() - t0)
    )
    return MORE


def main(argv=None):
    return run(parser().parse_args(argv))
