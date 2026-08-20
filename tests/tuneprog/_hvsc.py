"""HVSC exemplar fixtures: tune resolution, tracing, decompiling, printed bodies.

Every ``hvsc``-marked module shares these, so one tune is traced and decompiled
once per worker however many tests read it. Skips cleanly where the tune is not
reachable (no HVSC tree, no cache, offline).
"""

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("pysidtracker")

from pysidtracker.testing import resolve_tune  # noqa: E402

from deity_informant.tuneprog import copymerge, pipeline, printer  # noqa: E402
from deity_informant.tuneprog.cfg import build_procs  # noqa: E402
from deity_informant.tuneprog.ir import Const, Let, Load, Switch, Var  # noqa: E402
from deity_informant.tuneprog.irwalk import loads, node_exprs, walk  # noqa: E402
from deity_informant.tuneprog.lift import lift_trace  # noqa: E402
from deity_informant.tuneprog.machine import find_entries  # noqa: E402
from deity_informant.tuneprog.regions import build_regions  # noqa: E402
from deity_informant.tuneprog.trace import Tracer  # noqa: E402
from deity_informant.tuneprog.verify import certify, verify  # noqa: E402

from _prog import proc_body as body  # noqa: E402

CACHE = Path(os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache")) / "hvsc"
PAL_CLOCK = 985248
AUTOMATAS = "MUSICIANS/G/Goto80/Automatas.sid"
COMMANDO = "MUSICIANS/H/Hubbard_Rob/Commando.sid"
GNG = "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid"
EMOMYST = "MUSICIANS/H/Hermit/Emomyst.sid"
EOTW = "MUSICIANS/H/Hermit/End_of_the_World.sid"
LINUS = "MUSICIANS/L/Linus/Je_suis_Linus_le_salaud.sid"
DIA = "MUSICIANS/L/Linus/Do_It_Again.sid"


def tune_file(relpath):
    """The tune's path, skipping the test when it cannot be resolved."""
    path = resolve_tune(relpath, cache_dir=CACHE)
    if path is None:
        pytest.skip("%s unavailable (no HVSC tree, no cache, offline)" % relpath)
    return Path(path)


def tune(relpath):
    return tune_file(relpath).read_bytes()


def traced(relpath, seconds, song=None, override=None, until_period=False, chunk=256):
    """Trace ``seconds`` of music (or up to a state repeat): ``(entry, trace)``."""
    img, schedule = find_entries(tune(relpath))
    entry = schedule[0]
    calls = int(seconds * PAL_CLOCK / entry.cycles_per_tick)
    tr = Tracer(img, entry, song=song, override=override)
    tr.run_init()
    while tr.calls_done < calls and not (until_period and tr.period is not None):
        tr.run_calls(min(chunk, calls - tr.calls_done) if until_period else calls)
    return entry, tr.trace()


def front_end(relpath, seconds, song=None, override=None):
    """S1-S3 only: ``(entry, calls, trace, lifted, regions, procs)``."""
    entry, trace = traced(relpath, seconds, song=song, override=override)
    lifted = lift_trace(trace)
    regions = build_regions(trace, lifted)
    return entry, trace.meta["calls"], trace, lifted, regions, build_procs(trace, lifted, regions)


@dataclass
class Run:
    """One decompiled exemplar: the certified program, its verifier, its text."""

    trace: object
    prog: object
    regions: list
    v: object
    cert: dict
    calls: int
    text: str = ""
    names: object = None
    entry: object = None
    before: object = None
    fold: tuple = None


_DONE = {}


def decompiled(relpath, seconds, song=None, prefix=200, until_period=False, text=True):
    """Trace, decompile, verify and (unless ``text=False``) print one tune, once."""
    key = (relpath, seconds, song, prefix, until_period)
    run = _DONE.get(key)
    if run is None:
        entry, trace = traced(relpath, seconds, song=song, until_period=until_period)
        prog, regions, _procs = pipeline.build(trace, Path(relpath).name)
        calls = trace.meta["calls"]
        v = verify(prog, trace, calls=calls, prefix=prefix)
        cert = certify(prog, v, prefix=prefix)
        run = _DONE[key] = Run(
            trace, prog, regions, v, cert, calls, entry=entry, before=prog.to_json()
        )
    if text and run.names is None:
        view, st, names = pipeline.present(run.prog)
        run.text, run.names = printer.render(view, st, names), names
        # S5/S6 annotate the certified program; they never edit it
        assert run.prog.to_json() == run.before
    return run


def folded(relpath, seconds, song=None, until_period=False, prefix=200):
    """``(text, names, view, program)`` of one tune whose sibling copies folded."""
    run = decompiled(relpath, seconds, song=song, until_period=until_period, prefix=prefix)
    if run.fold is None:
        view, st, names = pipeline.present(run.prog)
        names.copies = copymerge.report(run.prog)
        assert run.prog.to_json() == run.before  # S5/S6 annotate; they never edit
        run.fold = (printer.render(view, st, names), names, view, run.prog)
    return run.fold


def load_addrs(prog, procs=None):
    """Every constant address the given procedures load a byte from."""
    out = set()
    for n, p in prog.procs.items():
        if procs is not None and n not in procs:
            continue
        for b in p.blocks.values():
            for s in list(b.stmts) + [b.term]:
                for e in node_exprs(s):
                    for x in loads(e):
                        if type(x.a) is Const:
                            out.update(range(x.a.v, x.a.v + x.w))
    return out


def switches(prog, width=None):
    """``[(cell address, width, terminator)]`` for every switch over a loaded cell."""
    out = []
    for p in prog.procs.values():
        defs = {s.n: s.e for b in p.blocks.values() for s in b.stmts if type(s) is Let}
        for b in p.blocks.values():
            if type(b.term) is not Switch:
                continue
            for x in walk(b.term.e):
                x = defs.get(x.n, x) if type(x) is Var else x
                if type(x) is Load and type(x.a) is Const and width in (None, x.w):
                    out.append((x.a.v, x.w, b.term))
    return out
