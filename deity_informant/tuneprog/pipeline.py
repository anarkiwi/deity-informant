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

from . import (
    emit,
    fold,
    frame,
    ir,
    jumptab,
    printer,
    recover,
    ssa,
    structure,
    tails,
    texture,
    unroll,
    verify as V,
    word,
)
from .build import build_ir
from .cfg import build_procs, procs_json
from .idioms import rewrite
from .lift import lift_trace
from .machine import find_entries
from .regions import build_regions
from .trace import Tracer
from .tracedata import Trace, merge

MODEL_D41B = {"6581": 0x00, "8580": 0x01}
MORE = 2
STAGES = ("trace", "front", "verify", "print", "done")


def add_args(ap):
    """The tuneprog options, shared by the tool and the ``deity-informant`` subcommand."""
    ap.add_argument("sid")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--song", type=int, help="1-based subtune (default: the header's)")
    ap.add_argument("--songs", choices=("all",), help="one tuneprog over every subtune's trace")
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


def _subdir(out, song):
    return out / ("s%02d" % song)


def _target(args, entry):
    if args.calls:
        return args.calls
    if args.seconds:
        clock = V.NTSC if "ntsc" in entry.source else V.PAL
        return int(args.seconds * clock / entry.cycles_per_tick)
    return args.max_calls


def stage_trace(args, out, st, t0, log=print):
    """Trace in chunks; True when the horizon (or a state repeat) is reached."""
    if args.songs == "all":
        return trace_all(args, out, st, t0, log)
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


def trace_all(args, out, st, t0, log=print):
    """Trace every subtune to its own directory, then merge them into one trace.

    Each subtune keeps its trace (verification runs against it); the merged trace
    is the union program the front end decompiles.
    """
    img, schedule = find_entries(Path(args.sid).read_bytes())
    entry = schedule[0]
    songs = st.setdefault("songs", list(range(1, img.songs + 1)))
    done = st.setdefault("traced", [])
    target = _target(args, entry)
    for song in songs:
        if song in done:
            continue
        resume = out / ("tracer%02d.pkl" % song)
        if args.resume and resume.exists():
            tr = Tracer.load(resume)
        else:
            override = {0xD41B: MODEL_D41B[args.sid_model]} if args.sid_model else None
            tr = Tracer(img, entry, song=song - 1, override=override)
            tr.run_init()
        while tr.calls_done < target and not (args.until_period and tr.period is not None):
            tr.run_calls(min(args.chunk, target - tr.calls_done))
            if time.process_time() - t0 > args.budget:
                break
        if tr.calls_done < target and not (args.until_period and tr.period is not None):
            tr.save(resume)
            log("  song %d: %d calls (%.0fs cpu)" % (song, tr.calls_done, time.process_time() - t0))
            return False
        tr.trace().save(_subdir(out, song))
        resume.unlink(missing_ok=True)
        done.append(song)
        log(
            "  song %d traced: %d calls (%.0fs cpu)"
            % (song, tr.calls_done, time.process_time() - t0)
        )
        if time.process_time() - t0 > args.budget:
            return False
    merge([Trace.load(_subdir(out, n)) for n in songs]).save(out)
    st.update(stage="front", calls=None)
    return True


def build(trace, name=None, sid_model=None, union=False):
    """Front end -> IR -> S4: the certified program, plus its front-end products.

    ``union`` is the ``--songs all`` build: what init writes is per-subtune state,
    so its regions are typed ``state`` and no cell folds to a constant.
    """
    lifted = lift_trace(trace)
    regions = build_regions(trace, lifted, init_kind="state" if union else "init_constant")
    procs = build_procs(trace, lifted, regions)
    prog = build_ir(trace, lifted, regions, procs, meta={"name": name, "sid_model": sid_model})
    folds = None if union else ssa.Folds(trace.image_post_init, trace.cells, trace.written_play)
    ssa.simplify(prog, rewrite, folds=folds)
    jumptab.enumerate_targets(prog)
    return prog, regions, procs


def stage_front(args, out, st):
    """Lift, type storage, build procedures, build the IR, run S4, emit Python."""
    trace = Trace.load(out)
    prog, regions, procs = build(
        trace, Path(args.sid).name, args.sid_model, union=args.songs == "all"
    )
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


def verify_all(args, out, st, t0, prog, log=print):
    """Verify the union program against every subtune's own trace, in chunks."""
    src = (out / "tuneprog.py").read_text()
    subs = st.setdefault("subtunes", [])
    resume = out / "verify.pkl"
    saved = pickle.loads(resume.read_bytes()) if args.resume and resume.exists() else {}
    for song in st["songs"]:
        if any(x["song"] == song for x in subs):
            continue
        ref = V.Reference(Trace.load(_subdir(out, song)))
        v = V.Verifier(prog, ref, src=src)
        if saved.get("song") == song:
            v.restore(saved["state"])
        while v.call < ref.calls and v.div is None:
            v.run(ref.calls, budget=v.seconds + max(1.0, args.budget - (time.process_time() - t0)))
            if time.process_time() - t0 > args.budget:
                break
        if v.call < ref.calls and v.div is None:
            resume.write_bytes(pickle.dumps({"song": song, "state": v.state()}))
            log("  song %d: verified %d/%d calls" % (song, v.call, ref.calls))
            return False
        subs.append(dict(v.subtune(), interp_prefix=0))
        log("  song %d verified (%d calls, %.0fs cpu)" % (song, v.call, time.process_time() - t0))
        if v.div is not None:
            st["divergence"] = v.div
            break
    cost = {
        "trace_calls": sum(x["ticks"] for x in subs),
        "sites": st.get("sites"),
        "regions": st.get("regions"),
        "ir_statements": sum(len(b.stmts) for p in prog.procs.values() for b in p.blocks.values()),
        "ir_blocks": sum(len(p.blocks) for p in prog.procs.values()),
        "ir_procs": len(prog.procs),
    }
    emit.write_certificate(
        out / "certificate.json",
        emit.certificate(prog, subs, cost, divergence=st.get("divergence")),
    )
    st["stage"] = "print"
    return True


def stage_verify(args, out, st, t0, prog=None, log=print):
    """Verify in chunks against the trace; writes ``certificate.json`` when finished."""
    prog = prog or ir.Tuneprog.load(out / "tuneprog.S4.json")
    if args.songs == "all":
        return verify_all(args, out, st, t0, prog, log)
    trace = Trace.load(out)
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
    texture.clean(view, frame.deltas(prog))
    structure.inline(view, printer.needed(view)[0])
    texture.tidy(view)
    names = recover.recover(view, structure.structure(view))
    word.fold16(view, names)
    fold.outline(view, names, *printer.needed(view))
    tails.promote_tails(view, names)
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
