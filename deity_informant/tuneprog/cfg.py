"""S2b -- procedures from observed edges: clone-per-entry, tail calls, switches.

:func:`build_procs` takes the entries (tick entries, ``init`` and every JSR
target) and walks the traced edge relation from each, cloning every node it
reaches, so a shared tail or a routine entered from two places exists once per
caller and the CFG has no cross-procedure fall-through:

* a ``jsr`` edge is a call statement that continues at the return pc;
* an edge into a JSR-target entry that is not a ``jsr`` is a **tail call**
  (``call f; return``);
* a matched ``rts``/``rti`` returns the frame's current procedure; an unmatched
  one, a ``JMP (ind)``, and a jump whose operand is an SMC cell become a
  ``switch`` over the observed targets with a ``trap`` default;
* a pc executed with several opcodes becomes a variant switch on the opcode
  cell, with arms for values a decompiled writer stored but that never executed
  marked ``unverified``.

Nodes are ``(proc entry, pc, opcode)``. Call summaries (registers/flags read
before written, and written) come from the tracer's per-activation masks.

Public API: :func:`build_procs`, :func:`procs_json`, :class:`Proc`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..lifter import OPS
from .machine import Refusal

REG_NAMES = {0: "A", 1: "X", 2: "Y", 3: "SP", 8: "C", 9: "Z", 10: "I", 11: "D", 13: "V", 14: "N"}
FLOW = ("fall", "br_taken", "br_not", "jmp")


def _names(mask):
    return [n for i, n in sorted(REG_NAMES.items()) if mask & (1 << i)]


@dataclass
class Proc:
    """One procedure: an entry, its cloned nodes, and its call summary."""

    name: str
    entry: int
    kind: str  # init | tick | sub
    nodes: dict = field(default_factory=dict)  # (pc, opcode) -> node dict
    variant_switch: dict = field(default_factory=dict)  # pc -> {"cell", "arms"}
    summary: dict = field(default_factory=dict)

    @property
    def exits(self):
        return [k for k, n in self.nodes.items() if n["term"] in ("return", "tail", "trap")]

    def to_dict(self):
        return {
            "name": self.name,
            "entry": self.entry,
            "kind": self.kind,
            "nodes": [dict(n, key=list(n["key"])) for n in self.nodes.values()],
            "variant_switch": [{"pc": pc, **v} for pc, v in sorted(self.variant_switch.items())],
            "summary": self.summary,
            "exits": [list(k) for k in self.exits],
        }


def _entry_names(trace):
    init = trace.meta["init"]
    ticks = [e["addr"] for e in trace.meta["schedule"]]
    names, kinds = {}, {}
    names[init], kinds[init] = "init", "init"
    for i, a in enumerate(ticks):
        if a not in names:
            names[a] = "tick" if len(ticks) == 1 else "tick%d" % i
            kinds[a] = "tick"
    for a in sorted(trace.jsr_targets):
        if a not in names:
            names[a] = "p_%04X" % a
            kinds[a] = "sub"
    return names, kinds


def _index(trace):
    out, keys, variants = {}, {}, {}
    for key in trace.sites:
        pc, op = key[0], key[1]
        keys.setdefault((pc, op), []).append(key)
        variants.setdefault(pc, set()).add(op)
    for (f, op, t), (kind, n) in trace.edges.items():
        out.setdefault((f, op), []).append((t, kind, n))
    return out, keys, {pc: sorted(v) for pc, v in variants.items()}


def _ref(to, entries, tail=False):
    return {"to": to, "tail": bool(tail or to in entries)}


def _switch(expr, targets, entries):
    return {
        "expr": expr,
        "cases": [[t, _ref(t, entries)] for t in sorted(targets)],
        "default": "trap",
    }


def build_procs(trace, lifted=None, regions=None):
    """``{name: Proc}`` for ``trace``; ``lifted`` supplies computed-control facts.

    Raises :class:`Refusal` (``recursion``) when the JSR call graph has a cycle.
    """
    names, kinds = _entry_names(trace)
    out, keys, variants = _index(trace)
    entries = set(names)
    tails = set(trace.jsr_targets)
    procs = {}
    callgraph = {}
    for entry in sorted(entries):
        proc = Proc(name=names[entry], entry=entry, kind=kinds[entry])
        _walk(trace, proc, entry, out, keys, variants, tails, lifted)
        proc.summary = _summary(trace, proc, regions)
        callgraph[entry] = {t for n in proc.nodes.values() for t in n["call"] if not n["tail_call"]}
        procs[proc.name] = proc
    _no_recursion(callgraph, names)
    return procs


def _walk(trace, proc, entry, out, keys, variants, tails, lifted):
    seen, work = set(), [entry]
    while work:
        pc = work.pop()
        if pc in seen or pc not in variants:
            continue
        seen.add(pc)
        ops = variants[pc]
        if len(ops) > 1 or _writer_variants(trace, pc, ops):
            proc.variant_switch[pc] = _variant_arms(trace, pc, ops)
        for op in ops:
            node = _node(trace, pc, op, out, keys, tails, lifted)
            proc.nodes[(pc, op)] = node
            work.extend(r["to"] for r in node["succ"] if not r["tail"])


def _writer_variants(trace, pc, ops):
    """Opcodes a decompiled writer stored into the cell at ``pc`` but never executed."""
    return sorted(v for v in trace.cell_values.get(pc, ()) if v not in ops and v in OPS)


def _variant_arms(trace, pc, ops):
    arms = [{"opcode": op, "unverified": False} for op in ops]
    arms += [{"opcode": v, "unverified": True} for v in _writer_variants(trace, pc, ops)]
    return {"cell": pc, "arms": arms}


def _node(trace, pc, op, out, keys, tails, lifted):
    key = keys[(pc, op)][0]
    site = trace.sites[key]
    ls = (lifted or {}).get(key)
    edges = out.get((pc, op), [])
    rets = trace.rets.get((pc, op))
    calls = trace.calls.get((pc, op))
    node = {
        "pc": pc,
        "opcode": op,
        "key": key,
        "mnemonic": OPS[op][0],
        "count": site["count"],
        "call": [],
        "tail_call": False,
        "computed": bool(ls is not None and ls.computed_control),
        "succ": [],
        "switch": None,
        "term": "trap",
    }
    if calls is not None:
        targets = sorted(calls["targets"])
        node["call"] = targets
        node["term"] = "call"
        node["succ"] = [_ref(calls["ret_pc"], tails)]
        if len(targets) > 1 or node["computed"]:
            node["switch"] = _switch(_expr(ls, "cell"), targets, tails)
        return node
    if rets is not None:
        if rets["unmatched"]:
            node["term"] = "switch"
            sw = node["switch"] = _switch({"kind": "stack"}, rets["targets"], tails)
            node["succ"] = [c[1] for c in sw["cases"]]
        else:
            node["term"] = "return"
        return node
    flow = [(t, k) for t, k, _n in edges]
    if not flow:
        return node
    if len(flow) == 1:
        t, k = flow[0]
        if k == "tail":
            node["term"] = "tail"
            node["tail_call"] = True
            node["call"] = [t]
        elif k == "jmpind" or node["computed"]:
            node["term"] = "switch"
            sw = _switch(_expr(ls, "jmpind" if k == "jmpind" else "cell"), [t], tails)
            node["switch"] = sw
            node["succ"] = [c[1] for c in sw["cases"]]
        else:
            node["term"] = "goto"
            node["succ"] = [_ref(t, tails)]
        return node
    kinds = {k for _t, k in flow}
    if kinds <= {"br_taken", "br_not", "tail"} and len(flow) == 2 and not node["computed"]:
        taken = next(
            t for t, k in flow if k in ("br_taken", "tail") and _is_taken(trace, pc, op, t)
        )
        node["term"] = "branch"
        node["succ"] = [_ref(t, tails) for t, _k in sorted(flow, key=lambda x: x[0] != taken)]
        node["taken"] = taken
        return node
    node["term"] = "switch"
    sw = _switch(_expr(ls, "jmpind" if "jmpind" in kinds else "cell"), [t for t, _k in flow], tails)
    node["switch"] = sw
    node["succ"] = [c[1] for c in sw["cases"]]
    return node


def _is_taken(trace, pc, op, t):
    e = trace.edges.get((pc, op, t))
    return e is not None and e[0] in ("br_taken", "tail", "jmp")


def _expr(ls, default):
    if ls is not None and ls.ctrl_cell:
        return {"kind": "cell", "addr": ls.ctrl_cell[0], "size": ls.ctrl_cell[1]}
    if default == "jmpind" and ls is not None and ls.ctrl[0] == "jmpind":
        return {"kind": "jmpind", "ptr": ls.ctrl[1]}
    return {"kind": default}


def _summary(trace, proc, regions):
    s = trace.summaries.get(proc.entry, {"rd": 0, "wr": 0, "count": 0})
    d = {
        "live_in": _names(s["rd"]),
        "defs": _names(s["wr"]),
        "activations": s["count"],
        "calls": sorted({t for n in proc.nodes.values() for t in n["call"]}),
    }
    if regions is not None:
        keys = {n["key"] for n in proc.nodes.values()}
        d["regions"] = sorted(
            {r.id for r in regions for a in r.accessors if tuple(a["site"]) in keys}
        )
    return d


def _no_recursion(callgraph, names):
    colour = {}

    def visit(a, stack):
        if colour.get(a) == 1:
            raise Refusal("recursion", " -> ".join("$%04X" % x for x in stack + [a]))
        if colour.get(a) == 2:
            return
        colour[a] = 1
        for t in callgraph.get(a, ()):
            if t in callgraph:
                visit(t, stack + [a])
        colour[a] = 2

    for a in names:
        visit(a, [])


def procs_json(procs):
    """Serialisable form of ``{name: Proc}``."""
    return {"procs": [p.to_dict() for p in procs.values()]}
