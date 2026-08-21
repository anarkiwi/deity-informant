"""The end-to-end driver: trace -> lift -> regions -> procs -> IR -> S4 -> verify -> text.

Every stage's artefacts land in one output directory and the long stages are
chunked against a CPU budget, so a long certificate is a handful of short runs
(:func:`run` returns ``MORE`` while work remains). ``tools/tuneprog_certify.py``
and ``deity-informant tuneprog`` are both thin wrappers around it.
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

from . import (
    closure,
    copymerge,
    copyview,
    emit,
    fold,
    frame,
    ghidra_facts,
    ir,
    jumptab,
    live as L,
    printer,
    recover,
    siblings,
    ssa,
    stack,
    structure,
    tails,
    texture,
    unroll,
    verify as V,
    views,
    word,
)
from .build import build_ir
from .cfg import build_procs, procs_json
from .idioms import rewrite
from .lift import lift_trace
from .machine import find_entries
from .regions import build_regions
from .resume import build_opts, horizon, state
from .trace import Tracer
from .tracedata import Trace, merge

MODEL_D41B = {"6581": 0x00, "8580": 0x01}
MORE = 2


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
    ap.add_argument(
        "--no-merge", action="store_true", help="do not fold sibling copies onto one body"
    )
    ap.add_argument(
        "--closure",
        choices=("trace", "static"),
        default="trace",
        help="also close the untaken branch directions the image states (unverified code)",
    )
    ap.add_argument(
        "--ghidra-facts", action="store_true", help="also write OUT/ghidra for headless Ghidra"
    )
    ap.add_argument("--budget", type=float, default=45.0, help="CPU seconds per invocation")
    ap.add_argument("--chunk", type=int, default=4000, help="ticks per progress step")
    ap.add_argument("--prefix", type=int, default=2000, help="calls to re-run on the interpreter")
    return ap


def parser(prog="tuneprog"):
    """The command line both entry points share."""
    return add_args(argparse.ArgumentParser(prog=prog, description=__doc__.splitlines()[0]))


def _subdir(out, song):
    return out / ("s%02d" % song)


def _stop(args, tr, target, free=True):
    """Why this subtune stopped: a state repeat, the tick horizon, or not yet (budget)."""
    if args.until_period and tr.witness(free) is not None:
        return "period"
    return "horizon" if tr.calls_done >= target else None


def _free(st):
    """True while a page-free repeat may end the trace: S4 has not said otherwise."""
    return st.get("stack") != "residual"


def _certified(args, witness, traced):
    """The ticks a run certifies: one past the repeat it stopped on, else what it traced.

    A chunk overshoots the witness by up to ``--chunk`` ticks. Those ticks stay in
    the trace the program is built from -- and add nothing to it, since a witness
    is a repeated state with no input consumed, so they replay sites, edges and
    accesses the certified prefix already carries.
    """
    return witness + 1 if args.until_period and witness is not None else traced


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
    keep = (args.resume or "stack" in st) and resume.exists()
    tr = Tracer.load(resume) if keep else None
    if tr is None:
        override = {0xD41B: MODEL_D41B[args.sid_model]} if args.sid_model else None
        tr = Tracer(img, entry, song=args.song - 1 if args.song else None, override=override)
        tr.run_init()
    target, free = _target(args, entry), _free(st)
    while tr.calls_done < target and not (args.until_period and tr.witness(free) is not None):
        tr.run_calls(min(args.chunk, target - tr.calls_done))
        st["calls"] = tr.calls_done
        hit = tr.witness(free) is not None
        log(
            "  traced %d calls (%.0fs cpu)%s"
            % (tr.calls_done, time.process_time() - t0, " period!" if hit else "")
        )
        if time.process_time() - t0 > args.budget:
            break
    done = tr.calls_done >= target or (args.until_period and tr.witness(free) is not None)
    tr.save(resume)
    if not done:
        return False
    trace = tr.trace()
    st["calls"] = _certified(args, tr.witness(free), tr.calls_done)
    trace.save(out)
    st.update(period=tr.period, first_repeat=tr.first_repeat, stage="front")
    return True


def trace_all(args, out, st, t0, log=print):
    """Trace every subtune to its own directory, then merge them into one trace.

    Each subtune keeps its trace (verification runs against it) and its own record
    -- ticks, stop reason, horizon -- so subtunes that stop for different reasons
    resume independently. The merged trace is what the front end decompiles.
    """
    img, schedule = find_entries(Path(args.sid).read_bytes())
    entry = schedule[0]
    songs = st.setdefault("songs", list(range(1, img.songs + 1)))
    done = st.setdefault("traced", {})
    target, free = _target(args, entry), _free(st)
    for song in songs:
        rec = done.get(str(song))
        if rec and rec["stop"]:
            continue
        resume = out / ("tracer%02d.pkl" % song)
        keep = rec and resume.exists() and (args.resume or "stack" in st)
        tr = Tracer.load(resume) if keep else None
        if tr is None:
            override = {0xD41B: MODEL_D41B[args.sid_model]} if args.sid_model else None
            tr = Tracer(img, entry, song=song - 1, override=override)
            tr.run_init()
        while tr.calls_done < target and not (args.until_period and tr.witness(free) is not None):
            tr.run_calls(min(args.chunk, target - tr.calls_done))
            if time.process_time() - t0 > args.budget:
                break
        stop = _stop(args, tr, target, free)
        calls = _certified(args, tr.witness(free), tr.calls_done)
        full = tr.witness(False) is not None
        done[str(song)] = {"calls": calls, "stop": stop, "horizon": horizon(args), "full": full}
        st["calls"] = calls
        if stop is None:
            tr.save(resume)
            log("  song %d: %d calls (%.0fs cpu)" % (song, calls, time.process_time() - t0))
            return False
        tr.trace().save(_subdir(out, song))
        if full or stop != "period":
            resume.unlink(missing_ok=True)
        else:
            tr.save(resume)
        log(
            "  song %d traced: %d calls, %s (%.0fs cpu)"
            % (song, calls, stop, time.process_time() - t0)
        )
        if time.process_time() - t0 > args.budget:
            return False
    merge([Trace.load(_subdir(out, n)) for n in songs]).save(out)
    st.update(stage="front", calls=None)
    return True


def _s4(trace, lifted, regions, procs, meta, union, plan=None):
    """One S4 program from the front-end products: SSA, stack, static jump closure."""
    prog = build_ir(trace, lifted, regions, procs, meta=meta, plan=plan)
    folds = None if union else ssa.Folds(trace.image_post_init, trace.cells, trace.written_play)
    ssa.simplify(prog, rewrite, folds=folds)
    prog.meta["stack"] = stack.eliminate(prog)
    code = {a for k, l in lifted.items() for a in range(k[0], k[0] + l.length)}
    jumptab.enumerate_targets(prog, code, {r.id: r.addrs for r in regions})
    return prog


def _front(trace, kind, unite=()):
    """Lift, type storage, build procedures: the front-end products of one trace."""
    lifted = lift_trace(trace)
    regions = build_regions(trace, lifted, init_kind=kind, unite=unite)
    return lifted, regions, build_procs(trace, lifted, regions)


def build(trace, name=None, sid_model=None, union=False, copies=True, static=False, log=None):
    """Front end -> IR -> S4: the certified program, plus its front-end products.

    ``union`` is the ``--songs all`` build, whose regions are ``state``. Copies are
    discovered on whichever program the build makes: :mod:`.siblings` reads the
    image, so the static closure neither takes a family away nor adds one.
    """
    kind = "state" if union else "init_constant"
    meta = {"name": name, "sid_model": sid_model}
    if static:
        closure.close_static(trace)
    lifted, regions, procs = _front(trace, kind)
    prog = _s4(trace, lifted, regions, procs, meta, union)
    band = tuple(trace.meta["load"])
    fams = siblings.correspond(prog, trace.image_post_init, band, procs) if copies else []
    if not copies:
        return prog, regions, procs
    plan = copymerge.plan(procs, trace, lifted, fams, regions, log)
    if not plan:
        if plan.refused:
            prog.meta["copies"] = plan.to_dict()
        return prog, regions, procs
    lifted, regions, procs = _front(trace, kind, unite=plan.unions)
    return _s4(trace, lifted, regions, procs, meta, union, plan), regions, procs


def stage_front(args, out, st):
    """Lift, type storage, build procedures, build the IR, run S4, emit Python."""
    trace = Trace.load(out)
    prog, regions, procs = build(
        trace,
        Path(args.sid).name,
        args.sid_model,
        union=args.songs == "all",
        copies=not args.no_merge,
        static=args.closure == "static",
    )
    (out / "regions.json").write_text(json.dumps([r.to_dict() for r in regions]))
    (out / "procs.json").write_text(json.dumps(procs_json(procs)))
    prog.save(out / "tuneprog.S4.json")
    (out / "tuneprog.py").write_text(emit.emit_python(prog))
    stage = _horizon_stage(args, st, trace, prog)
    st.update(
        build=build_opts(args),
        sites=len(trace.sites),
        regions=len(regions),
        procs=len(procs),
        stmts=sum(len(b.stmts) for p in prog.procs.values() for b in p.blocks.values()),
        stage=stage,
    )
    return prog


def _horizon_stage(args, st, trace, prog):
    """``"trace"`` where S4's verdict moves the horizon this run stopped on.

    ``--until-period`` stops at the earliest repeat of either footprint, and a
    residual program may claim only the page-inclusive one: it traces on. S4
    decides once, so a run that has already traced on does not do it again.
    """
    first = st.get("stack") is None
    st["stack"] = "eliminated" if prog.meta.get("stack") == "eliminated" else "residual"
    if not (first and st["stack"] == "residual" and args.until_period):
        return "verify"
    if args.songs != "all":
        if trace.witness(False) is not None:
            return "verify"
    else:
        rec = st.get("traced", {})
        stale = {k for k, r in rec.items() if r["stop"] == "period" and not r.get("full")}
        if not stale:
            return "verify"
        st["traced"] = {k: (dict(r, stop=None) if k in stale else r) for k, r in rec.items()}
        st["subtunes"] = [x for x in st.get("subtunes", ()) if str(x["song"]) not in stale]
    st.pop("divergence", None)
    return "trace"


def verify_all(args, out, st, t0, prog, log=print):
    """Verify the union program against every subtune's own trace, in chunks."""
    src = (out / "tuneprog.py").read_text()
    subs = st.setdefault("subtunes", [])
    resume = out / "verify.pkl"
    saved = pickle.loads(resume.read_bytes()) if args.resume and resume.exists() else {}
    for song in st["songs"]:
        if any(x["song"] == song for x in subs):
            continue
        sub = Trace.load(_subdir(out, song))
        ref = V.Reference(sub, _certified(args, sub.witness(_free(st)), sub.meta["calls"]))
        v = V.Verifier(prog, ref, src=src)
        if saved.get("song") == song and saved.get("calls") == ref.calls:
            v.restore(saved["state"])
        while v.call < ref.calls and v.div is None:
            v.run(ref.calls, budget=v.seconds + max(1.0, args.budget - (time.process_time() - t0)))
            if time.process_time() - t0 > args.budget:
                break
        if v.call < ref.calls and v.div is None:
            resume.write_bytes(pickle.dumps({"song": song, "calls": ref.calls, "state": v.state()}))
            log("  song %d: verified %d/%d calls" % (song, v.call, ref.calls))
            return False
        resume.unlink(missing_ok=True)
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

    Structuring, texture removal, 16-bit views and outlining; the argument is
    never touched. Sibling copies are already one body (:mod:`.copymerge`).
    """
    live = L.needed(prog)[0]
    keep = L.wants(prog, live)
    view = structure.view(prog, live, keep)
    copies = copyview.expand(view)
    texture.clean(view, frame.deltas(prog))
    structure.inline(view, L.needed(view)[0], keep)
    texture.tidy(view)
    facts = copyview.naming_facts(view)
    names = recover.recover(view, structure.structure(view), facts)
    views.decorate(view, names, facts=facts)
    word.fold16(view, names)
    fold.outline(view, names, *L.needed(view))
    tails.promote_tails(view, names)
    views.decorate(view, names)
    live, params = L.needed(view)
    st = structure.structure(view, L.wants(view, live))
    _n, groups = unroll.unroll(st, live, fold.livearg(view, params), rgn=view.by_id())
    views.decorate(view, names, groups)
    copyview.mark(st, copies)
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
    names.copies = copymerge.report(prog)
    (out / "tuneprog.S5.json").write_text(json.dumps(structure_json(view, st, names)))
    (out / "tuneprog.S6.json").write_text(json.dumps(names.to_dict(), indent=1))
    (out / "tuneprog.md").write_text(printer.render(view, st, names, doc))
    return view, st, names


def run(args, log=print):
    """Drive the stages under ``args``; returns the process exit code."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    st = state(out, args)
    t0 = time.process_time()
    prog = None
    try:
        while st["stage"] in ("trace", "front"):
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
            if getattr(args, "ghidra_facts", False):
                log("  ghidra facts -> %s" % ghidra_facts.export(out))
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
