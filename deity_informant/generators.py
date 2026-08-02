"""Parametric generator vocabulary over recovered song models.

Lifts the raw graph sweeps/counters of ``song_model`` into a small operator set
(Clock, TableGen, Accumulator, Trigger), canonicalises each operator's parameters
by equality saturation, and forward-evaluates accumulators against the projection."""

from collections import namedtuple

from . import eqlift as E
from . import eqlift_mem as M
from . import framelog
from . import song_model as sm
from . import structured as S

Clock = namedtuple("Clock", "cell kind reload role")
TableGen = namedtuple("TableGen", "plane table pitch phase")
Accumulator = namedtuple("Accumulator", "plane acc step direction rate bounds")
Trigger = namedtuple("Trigger", "kind source guard")
Generators = namedtuple("Generators", "clocks pw filter freq triggers static")
Coverage = namedtuple("Coverage", "plane target interpreted total runs")

_PR = E._Printer({})


def canon(ir):
    """Simplest form of a pass-1 expression under equality saturation, rendered."""
    if ir is None:
        return None
    return _PR.fmt(M.to_ir(M.extract(E._egg_of(E.to_egg(ir), {}))))


def raw(ir):
    """Rendered raw (pre-saturation) form of a pass-1 expression."""
    return None if ir is None else _PR.fmt(M.to_ir(str(E._egg_of(E.to_egg(ir), {}))))


def _clock(counter):
    """A cadence counter as a Clock: divider (dec+reload), LFO (inc), or counter."""
    if counter.kind == "dec" and counter.reload is not None:
        role = "tick_divider"
    elif counter.kind == "inc":
        role = "lfo_phase"
    else:
        role = "counter"
    return Clock(counter.base, counter.kind, counter.reload, role)


def _accumulator(sweep):
    """A recovered Sweep as an Accumulator with e-graph-canonicalised steps."""
    steps = tuple((d, canon(st)) for d, st in sweep.updates)
    dirs = {d for d, _ in sweep.updates}
    direction = "triangle" if len(dirs) > 1 else (next(iter(dirs)) if dirs else "?")
    return Accumulator(sweep.plane, sweep.acc, steps, direction, sweep.rate, sweep.bounds)


def _freq_ops(freq, model):
    """Freq drivers as TableGen (note lookup) or Accumulator (slide) operators."""
    tr = sm.ann.aggregate(list(sm.ann.model_procs(model)), model)
    ops = []
    for d in freq:
        if d.kind == "note":
            pitch = tr.pitch.get(d.source, {}).get("pitch_table", False)
            ops.append(TableGen("freq", d.source, pitch, None))
        elif d.kind == "slide":
            ops.append(Accumulator("freq", d.source, (), "slide", None, ()))
    return ops


def _triggers(control):
    """Note-lifecycle triggers from the control automaton's guarded edges."""
    out = []
    for t in control.transitions:
        if t.action == "gate_on":
            out.append(Trigger("note_on", t.source, t.guards))
        elif t.action == "gate_off":
            out.append(Trigger("gate_off", t.source, t.guards))
        elif any("&" in g for g in t.guards):
            out.append(Trigger("flag_bit", t.source, t.guards))
    return out


def recover(model):
    """Per-plane generator operators recovered from ``model``'s lifted graph."""
    m = sm.analyze(model)
    return Generators(
        clocks=[_clock(c) for c in m.counters],
        pw=[_accumulator(w) for w in m.pw],
        filter=[_accumulator(w) for w in m.filter.cutoff],
        freq=_freq_ops(m.freq, model),
        triggers=_triggers(m.control),
        static=m.filter.static,
    )


def _series(canonical, section_idx, reg):
    """Values written to one register over the canonical projection, in order."""
    return [dict(rec[section_idx])[reg] for rec in canonical if reg in dict(rec[section_idx])]


def verify_series(vals):
    """Forward-evaluate a constant-step accumulator over a write series (mod 256).

    Each run seeds (value, step) from a consecutive pair then predicts the rest;
    a mispredict starts a new run (a note reseed). Returns (interpreted, total,
    runs) with interpreted counting writes regenerated bit-exactly."""
    total = len(vals)
    interp = runs = 0
    step = None
    for i, v in enumerate(vals):
        if i and step is not None and (vals[i - 1] + step) & 0xFF == v:
            interp += 1
            continue
        step = (v - vals[i - 1]) & 0xFF if i else None
        runs += 1
    return interp, total, runs


def regenerate(model, nframes):
    """Verify recovered accumulators against the observed plane projection.

    Walks ``nframes`` frames, then forward-evaluates every recovered pw/cutoff
    accumulator per target register and reports the bit-exact coverage."""
    gens = recover(model)
    frames = framelog.frames_from_walker(S.Walker(model), nframes)
    can = framelog.canonical(frames)
    out = []
    for acc in gens.pw:
        off = 2 if acc.plane == "pw_lo" else 3
        for voice in range(3):
            vals = _series(can, 2 * voice, 7 * voice + off)
            if len(vals) >= 3:
                out.append(Coverage(acc.plane, "v%d" % voice, *verify_series(vals)))
    for acc in gens.filter:
        reg = 0x15 if acc.plane == "cutoff_lo" else 0x16
        vals = _series(can, 6, reg)
        if len(vals) >= 3:
            out.append(Coverage(acc.plane, "global", *verify_series(vals)))
    return gens, out
