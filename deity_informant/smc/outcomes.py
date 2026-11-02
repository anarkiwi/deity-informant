"""Classify a witness program's whole-program lift outcome against the reference.

A flat CPU program is lifted from facts over its calls and run differentially against
``wholeprog.reference_call``; the outcome is ``exact_nodeopt``, ``exact_deopt``, ``inexact`` or
``not_runnable``. A machine program whose cells the lifter models (banking) is lifted through
the C64 bank map from machine facts and checked call by call against the machine; any other
machine program's outcome is its cells' lift reason.
"""

from __future__ import annotations

from deity_informant import c64, trace, wholeprog

from .expect import lift_reason
from .space import Cell

IO = (0xC000, 0xC100)

__all__ = ["classify", "OUTCOMES"]

OUTCOMES = ("exact_nodeopt", "exact_deopt", "inexact", "not_runnable")


def _facts(prog):
    calls = [(prog.pcs[e], a) for e, a in prog.calls]
    init = [(prog.labels.get(p, p), a) for p, a in prog.init]

    def driver(s):
        for pc, a in init:
            s.call(pc, a)
        return [s.call(pc, a) for pc, a in calls]

    return trace.collect(prog.image, driver)


def _machine_outcome(prog, max_cycles):
    reasons = {lift_reason(Cell(*_parse(sid))) for sid in prog.sids}
    reasons.discard(None)
    if reasons:
        return next(r for r in ("lift-no-dma", "lift-no-interrupts") if r in reasons), 0
    wit = prog.wit
    image, labels = wit.image()
    entry = labels[wit.entry]
    calls = [(entry, c[-1] if len(c) > 1 else 0) for c in wit.calls]
    res = c64.lift_check(image, entry, calls, calls, max_cycles=max_cycles)
    deopts = sum(res.prog.deopts.values())
    if res.bad is not None:
        out = {"halted": "not_runnable", "interrupt": "lift-no-interrupts"}
        return out.get(res.bad[1], "inexact"), deopts
    return ("exact_deopt" if deopts else "exact_nodeopt"), deopts


def _parse(sid):
    writer, channel, role, timing, mapping = sid.split(".")
    rmw = channel.endswith("_rmw")
    return writer, channel[:-4] if rmw else channel, rmw, role, timing, mapping


def classify(prog, max_cycles=2_000_000):
    """``(outcome, deopts)`` for ``prog``."""
    if getattr(prog, "oracle", "cpu") == "machine":
        return _machine_outcome(prog, max_cycles)
    image = bytearray(prog.image)
    for p, a in prog.init:
        wholeprog.reference_call(image, prog.labels.get(p, p), a=a, volatile=[IO])
    facts = _facts(prog)
    lifted = wholeprog.lift(image, prog.pcs, [IO], facts)
    m1, m2 = bytearray(image), bytearray(image)
    s1 = s2 = {"reg": None, "cycles": 0}
    for e, a in prog.calls:
        kw = {"log": True, "synth": True, "max_cycles": max_cycles}
        r = wholeprog.reference_call(m1, prog.pcs[e], a=a, volatile=[IO], clocked=False, **kw, **s1)
        if r["halt"] != "return":
            return "not_runnable", sum(lifted.deopts.values())
        o = lifted.call(m2, e, a=a, **kw, **s2)
        for k in ("log", "reg", "pc", "halt"):
            if r[k] != o[k]:
                return "inexact", sum(lifted.deopts.values())
        sp = r["reg"][3]
        if any(m1[i] != m2[i] and not 0x100 <= i <= 0x100 + sp for i in range(0x10000)):
            return "inexact", sum(lifted.deopts.values())
        s1 = {"reg": list(r["reg"]), "cycles": r["cycles"]}
        s2 = {"reg": list(o["reg"]), "cycles": r["cycles"]}
    deopts = sum(lifted.deopts.values())
    return ("exact_deopt" if deopts else "exact_nodeopt"), deopts
