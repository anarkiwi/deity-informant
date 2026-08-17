"""S7 text form: the certified tuneprog as anatomy-style pseudocode (``tuneprog.md``).

Prints the S5 node tree with the S6 names: ``meta``, ``state`` (regions, roles and
struct views), ``const`` tables, ``inputs``, then one procedure each. Machine
plumbing (stack frames, register copies nothing reads) is dropped for print only.
"""

from __future__ import annotations

from .irwalk import call_order, forwarder
from .machine import PAL_FRAME
from .pseudocode import Body, num

PHASES = {1: "init", 2: "tick", 3: "init+tick"}


def _meta(prog, names, cert):
    """The ``meta`` block: entry, cadence, subtunes, model, certificate."""
    m = prog.meta
    e = m.get("entry", {})
    rate = PAL_FRAME / e.get("cycles_per_tick", PAL_FRAME)
    out = [
        "entry     %s $%04X every %d cycles (%.1f calls/frame, %s)"
        % (e.get("kind"), e.get("addr", 0), e.get("cycles_per_tick", 0), rate, e.get("source")),
        "subtunes  %d, playing song %d; sid model %s"
        % (m.get("songs", 1), (m.get("song") or 0) + 1, m.get("sid_model") or "as traced"),
        "program   %d procedures, %d blocks, %d statements, %d regions"
        % (
            len(prog.procs),
            sum(len(p.blocks) for p in prog.procs.values()),
            sum(len(b.stmts) for p in prog.procs.values() for b in p.blocks.values()),
            len([r for r in prog.storage if r.id >= 0]),
        ),
    ]
    if names.phase is not None:
        out.append("phase     %s selects the rate" % names.region.get(names.phase[0], "?"))
    if cert:
        s = cert["subtunes"][0]
        out.append(
            "certified %s calls, %s divergences, period %s, first repeat at call %s (%s), stage %s"
            % (
                num(s["ticks"]),
                s["divergences"],
                num(s["period"]) if s["period"] else "none",
                num(s["first_repeat"]) if s["first_repeat"] is not None else "-",
                "complete" if s["complete"] else "horizon",
                cert.get("stage"),
            )
        )
    return out


def _row(r, names, extra=""):
    return "%-16s $%04X %-14s %-10s %s" % (
        names.region.get(r.id, "r%d" % r.id),
        r.base,
        "%d bytes%s" % (r.size, "" if r.stride < 2 else " stride %d" % r.stride),
        names.role.get(r.id, ""),
        extra or names.notes.get(r.id, ""),
    )


def _state(prog, names):
    """The ``state`` block: struct views first, then the scalars."""
    out = []
    for g, d in sorted(names.groups.items()):
        fields = {}
        for rid in sorted(set(d["members"]), key=lambda i: _base(prog, i)):
            fields.setdefault(names.view[rid][1], []).append(rid)
        out.append("%s[%d]  stride %d, %d fields" % (g, d["n"], d["stride"], len(fields)))
        for fname, rids in fields.items():
            out.append(
                "  .%-14s %-24s %-10s %s"
                % (
                    fname,
                    " ".join("$%04X" % _base(prog, i) for i in rids),
                    names.role.get(rids[0], ""),
                    names.notes.get(rids[0], ""),
                )
            )
    half = {r for p in names.u16 for r in p}
    for (lo, hi), name in sorted(names.u16.items(), key=lambda kv: _base(prog, kv[0][0])):
        if lo in names.view:
            continue
        out.append(
            "%-16s $%04X %-14s %-10s %s"
            % (
                name,
                _base(prog, lo),
                "u16",
                names.role.get(lo, ""),
                "lo|hi $%04X" % _base(prog, hi),
            )
        )
    for r in sorted(prog.storage, key=lambda x: x.base):
        if r.id < 0 or r.id in names.view or r.kind not in ("state", "init_constant"):
            continue
        if r.id not in half:
            out.append(_row(r, names, "init-only" if r.kind == "init_constant" else ""))
    return out


def _const(prog, names):
    return [
        _row(r, names)
        for r in sorted(prog.storage, key=lambda x: x.base)
        if r.id >= 0 and r.kind in ("const", "image") and r.id not in names.view
    ]


def _inputs(prog):
    return [
        "$%04X %-14s at $%04X, %d reads (%s)" % (addr, kind, pc, count, PHASES.get(phase, phase))
        for pc, addr, kind, count, phase in prog.inputs
    ]


def _rgn(prog, rid):
    return next(r for r in prog.storage if r.id == rid)


def _base(prog, rid):
    return _rgn(prog, rid).base


def render(prog, structured, names, cert=None, pcs=True):
    """``tuneprog.md``: meta, state, const, inputs, then every procedure."""
    body = Body(prog, names, pcs)
    out = ["# tuneprog: %s" % prog.meta.get("name", "?"), ""]
    for title, lines in (
        ("meta", _meta(prog, names, cert)),
        ("state", _state(prog, names)),
        ("const", _const(prog, names)),
        ("inputs", _inputs(prog)),
    ):
        out += ["## %s" % title, "", "```"] + (lines or ["(none)"]) + ["```", ""]
    out += ["## program", ""]
    for name in _procs_order(prog):
        out += ["```"] + body.render(name, structured[name]) + ["```", ""]
    return "\n".join(out) + "\n"


def _procs_order(prog):
    """The tick first, then what it calls, then init and the rest; forwarders elided."""
    tick, init = prog.meta.get("tick_proc"), prog.meta.get("init_proc")
    hot = [n for n in reversed(call_order(prog)) if n != init]
    order = [n for n in (tick,) if n in hot] + [n for n in hot if n != tick]
    order += [n for n in prog.procs if n not in order]
    return [n for n in order if forwarder(prog.procs[n]) is None]
