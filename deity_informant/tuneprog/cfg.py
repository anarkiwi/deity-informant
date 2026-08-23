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
* both directions of an executed branch are successors; a direction the trace
  never took is marked ``trap`` (the trace-closed product), and a branch with a
  patched offset keeps its condition with a switch on the taken side;
* a pc executed with several opcodes becomes a variant switch on the opcode
  cell, with arms for values a decompiled writer stored but that never executed
  marked ``unverified``.

Nodes are ``(proc entry, pc, opcode)``. Call summaries (registers/flags read
before written, and written) come from the tracer's per-activation masks.

Public API: :func:`build_procs`, :func:`procs_json`, :class:`Proc`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..lifter import OPS
from .ir import REG_NAMES
from .machine import Refusal


def _names(mask):
    return [n for i, n in sorted(REG_NAMES.items()) if mask & (1 << i)]


@dataclass
class Proc:
    """One procedure: an entry, its cloned nodes, and its call summary."""

    name: str
    entry: int
    kind: str  # init | tick | sub -- the first of roles
    roles: tuple = ()
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
            "roles": list(self.roles),
            "nodes": [dict(n, key=list(n["key"])) for n in self.nodes.values()],
            "variant_switch": [{"pc": pc, **v} for pc, v in sorted(self.variant_switch.items())],
            "summary": self.summary,
            "exits": [list(k) for k in self.exits],
        }


def _entry_names(trace):
    """``({addr: name}, {addr: [role]})`` -- one procedure per entry address.

    An address can hold several roles (an ``init`` that is also the play entry, a
    tick entry that is also a JSR target); the first is the procedure's ``kind``.
    The schedule names its entries by what they are, so a second interrupt is
    ``nmi`` (``nmi0``, ``nmi1``, ... where its vector took several addresses).
    """
    init = trace.meta["init"]
    sched = [("nmi" if e["kind"] == "nmi" else "tick", e["addr"]) for e in trace.meta["schedule"]]
    n, seen = Counter(r for r, _a in sched), Counter()
    entries = []
    for role, a in sched:
        seen[role] += 1
        entries.append((a, role, role if n[role] == 1 else "%s%d" % (role, seen[role] - 1)))
    names, roles = {}, {}
    for a, role, name in (
        [(init, "init", "init")]
        + entries
        + [(a, "sub", "p_%04X" % a) for a in sorted(trace.jsr_targets)]
    ):
        if a in roles:
            if role not in roles[a]:
                roles[a].append(role)
            continue
        names[a], roles[a] = name, [role]
    return names, roles


def _site_index(trace):
    out, keys, variants = {}, {}, {}
    for key in trace.sites:
        pc, op = key[0], key[1]
        keys.setdefault((pc, op), []).append(key)
        variants.setdefault(pc, set()).add(op)
    for (f, op, t), (kind, n) in trace.edges.items():
        out.setdefault((f, op), []).append((t, kind, n))
    return out, keys, {pc: sorted(v) for pc, v in variants.items()}


def succ_ref(to, entries=(), tail=False, trap=False):
    """A successor reference: ``tail`` calls the entry and returns, ``trap`` was never taken."""
    return {"to": to, "tail": bool(tail or to in entries), "trap": bool(trap)}


def _switch(expr, targets, entries):
    return {
        "expr": expr,
        "cases": [[t, succ_ref(t, entries)] for t in sorted(targets)],
        "default": "trap",
    }


def build_procs(trace, lifted=None, regions=None):
    """``{name: Proc}`` for ``trace``; ``lifted`` supplies computed-control facts.

    Raises :class:`Refusal` (``recursion``) when the JSR call graph has a cycle.
    """
    names, roles = _entry_names(trace)
    out, keys, variants = _site_index(trace)
    entries = set(names)
    tails = set(trace.jsr_targets)
    procs = {}
    callgraph = {}
    for entry in sorted(entries):
        proc = Proc(name=names[entry], entry=entry, kind=roles[entry][0], roles=tuple(roles[entry]))
        _walk(trace, proc, entry, out, keys, variants, tails, lifted)
        proc.summary = _summary(trace, proc, regions)
        # a tail call grows no frame: no recursion here, but a cycle the IR call graph has
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
        idle = trace.meta.get("init_idle") if proc.kind == "init" else None
        for op in ops:
            node = _cfg_node(trace, pc, op, out, keys, tails, lifted, idle)
            proc.nodes[(pc, op)] = node
            sw = node["switch"]
            refs = list(node["succ"]) + ([c[1] for c in sw["cases"]] if sw else [])
            work.extend(r["to"] for r in refs if not r["tail"] and not r["trap"])


def _writer_variants(trace, pc, ops):
    """Byte values a writer stored into the opcode cell at ``pc`` that never executed.

    Every byte decodes (the lifter covers all 256 NMOS opcodes), so each is an arm;
    they are unverified because no execution of that variant was observed.
    """
    return sorted(v for v in trace.cell_values.get(pc, ()) if v not in ops)


def _variant_arms(trace, pc, ops):
    """Arms of the opcode-cell switch; an ``unverified`` arm has no node -- trap it."""
    arms = [{"opcode": op, "unverified": False} for op in ops]
    arms += [{"opcode": v, "unverified": True} for v in _writer_variants(trace, pc, ops)]
    return {"cell": pc, "arms": arms}


def _cfg_node(trace, pc, op, out, keys, tails, lifted, idle=None):
    # One (pc, opcode) can carry several site keys when an operand byte is patched
    # by init only (constant, but a different constant per phase); the first is
    # representative because the control behaviour is the opcode's.
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
        "closed": bool(site.get("closed")),
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
        node["succ"] = [succ_ref(calls["ret_pc"], tails)]
        if len(targets) > 1 or node["computed"]:
            node["switch"] = _switch(_dispatch_on(ls, "cell", site), targets, tails)
        return node
    if rets is not None:
        if rets["unmatched"]:
            node["term"] = "switch"
            sw = node["switch"] = _switch({"kind": "stack"}, rets["loose"], tails)
            node["succ"] = [c[1] for c in sw["cases"]]
        else:
            node["term"] = "return"
        return node
    flow = [(t, k) for t, k, _n in edges]
    arms = branch_arms(ls, site, pc, op)
    if arms is not None:
        # Both directions of an executed branch are nodes; a direction the trace
        # never took is a trap (the trace-closed product of architecture section 2). A
        # branch whose offset byte is an SMC cell keeps its condition and dispatches
        # on the targets its observed offsets name and the trace reached.
        seen = {t for t, _k in flow}
        node["term"] = "branch"
        node["taken"] = arms[0]
        if not node["computed"]:
            node["succ"] = [succ_ref(a, tails, trap=a not in seen) for a in arms]
            return node
        taken = sorted(seen & _rel_targets(site, arms[1]))
        node["succ"] = [
            succ_ref(taken[0] if taken else arms[0], tails, trap=not taken),
            succ_ref(arms[1], tails, trap=arms[1] not in seen),
        ]
        if taken:
            node["switch"] = _switch(_dispatch_on(ls, "cell", site), taken, tails)
        return node
    if not flow:
        # an init that ends in a ``JMP *`` has returned as far as the schedule is
        # concerned: the machine sits there until the next interrupt. It is init's
        # own address: any other procedure falling onto it is a path never traced.
        if idle is not None and idle == (pc + (ls.length if ls else 1)) & 0xFFFF:
            node["term"] = "return"
        return node
    if len(flow) == 1:
        t, k = flow[0]
        if k == "tail":
            node["term"] = "tail"
            node["tail_call"] = True
            node["call"] = [t]
        elif k == "jmpind" or node["computed"]:
            node["term"] = "switch"
            sw = _switch(_dispatch_on(ls, "jmpind" if k == "jmpind" else "cell", site), [t], tails)
            node["switch"] = sw
            node["succ"] = [c[1] for c in sw["cases"]]
        else:
            node["term"] = "goto"
            node["succ"] = [succ_ref(t, tails)]
        return node
    kinds = {k for _t, k in flow}
    node["term"] = "switch"
    sw = _switch(
        _dispatch_on(ls, "jmpind" if "jmpind" in kinds else "cell", site),
        [t for t, _k in flow],
        tails,
    )
    node["switch"] = sw
    node["succ"] = [c[1] for c in sw["cases"]]
    return node


def branch_arms(ls, site, pc, op):
    """``(taken, fall-through)`` of a relative branch, or ``None`` if not one."""
    if ls is not None:
        return (ls.ctrl[3], ls.ctrl[4]) if ls.ctrl[0] == "br" else None
    if OPS[op][1] != "rel":
        return None
    return ((pc + 2 + sext(site["variants"][0][1])) & 0xFFFF, (pc + 2) & 0xFFFF)


def sext(b):
    """A branch offset byte as the signed displacement the 6502 adds."""
    return b - 256 if b & 0x80 else b


def _rel_targets(site, fall):
    """The addresses the offset bytes the trace executed at this site name."""
    return {(fall + sext(b[1])) & 0xFFFF for b in site["variants"]}


def _ptrs(site):
    """The pointers the operand words the trace executed at this site name."""
    return sorted({b[1] | (b[2] << 8) for b in site["variants"]})


def _dispatch_on(ls, default, site=None):
    """What a computed terminator dispatches on.

    ``JMP (ind)`` dispatches on the word its pointer holds, so a patched operand is
    the pointer and not the target: it is one load short of the address jumped to.
    """
    if ls is not None and ls.ctrl[0] == "jmpind":
        e = {"kind": "jmpind", "ptr": ls.ctrl[1]}
        if ls.ctrl_cell is not None and site is not None:
            e["cell"], e["ptrs"] = [ls.ctrl_cell[0], ls.ctrl_cell[1]], _ptrs(site)
        return e
    if ls is not None and ls.ctrl_cell:
        return {"kind": "cell", "addr": ls.ctrl_cell[0], "size": ls.ctrl_cell[1]}
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
