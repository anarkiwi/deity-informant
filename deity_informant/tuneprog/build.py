"""Front end -> IR: one :class:`~.ir.Proc` per :mod:`.cfg` procedure, one block per node.

Residualised P-Code (:mod:`.lift`) becomes statements one-for-one -- every P-Code
operand is a leaf, so a block is a flat ``let`` sequence -- and every memory op is
resolved against the region (:mod:`.regions`) the trace saw it touch, which fixes
its access class and the envelope it must stay inside. Registers and flags are
procedure-local values: a procedure takes its live-in registers as ``params`` and
returns the ones it (or a callee) defines, so no machine register survives a
call boundary.

Control follows :mod:`.cfg` exactly: variant switches become ``switch(load(cell))``
over the opcode cell, patched jumps/branches and ``JMP (ind)``/RTS-trick returns
become ``switch`` over the computed target with a ``trap`` default, an untaken
branch direction is a ``trap`` block, a tail edge is ``call f; return``.

Public API: :func:`build_ir`, :func:`ops_to_stmts`, :func:`straightline`.
"""

from __future__ import annotations

from .ir import (
    Block,
    Bin,
    Call,
    Const,
    Goto,
    If,
    Let,
    Proc,
    REGVAR,
    Return,
    Rgn,
    Switch,
    Trap,
    Tuneprog,
    Var,
    succs,
)
from .closure import static_resolver
from .copymerge import Plan
from .lower import (
    PH_INIT,
    Storage,
    add_sp,
    column,
    ctrl_expr,
    ops_to_stmts,
    pop_status,
    push16,
    tgt,
)
from .irwalk import callees
from .wire import wire


class _Builder:
    """One IR procedure per :class:`~.cfg.Proc`."""

    def __init__(self, trace, lifted, store, procs, plan=None):
        self.trace = trace
        self.lifted = lifted
        self.store = store
        self.procs = procs
        self.plan = plan if plan is not None else Plan()
        self.extra = {}
        self.byentry = {p.entry: p.name for p in procs.values()}
        self.keys = {}
        for key in trace.sites:
            self.keys.setdefault((key[0], key[1]), []).append(key)

    def key_of(self, pc, op, phase):
        """The site key to lift for this pc under ``phase``.

        One (pc, opcode) has several keys only when an operand byte that play
        never writes differs between phases (an init-only patch). Play wins, since
        the tick code is what a certificate is about; a procedure that runs in
        both phases with such an operand would need per-phase cloning.
        """
        ks = self.keys[(pc, op)]
        for want in (phase & ~PH_INIT, phase):
            hit = next((k for k in ks if self.trace.sites[k]["phases"] & want), None)
            if hit is not None:
                return hit
        return ks[0]

    def phase_of(self, cp):
        """The phases the trace ran this procedure in (init, play, or both)."""
        m = 0
        for pc, op in cp.nodes:
            for k in self.keys.get((pc, op), ()):
                m |= self.trace.sites[k]["phases"]
        return m or 2

    def label(self, cp, pc):
        """The label control enters ``pc`` at: the variant dispatch, else the node."""
        pc = self.plan.tmpl(cp.name, pc)
        if pc in cp.variant_switch:
            return "V%04X" % pc
        op = self.plan.op_at(cp.name, pc)
        ops = [op] if op is not None else [o for (p, o) in cp.nodes if p == pc]
        return "L%04X_%02X" % (pc, ops[0]) if ops else "X%04X" % pc

    def enter(self, cp, pc, src):
        """The label an edge from ``src`` really enters.

        An edge from outside a family enters the copy that holds its target, and
        the prologue it goes through says which copy that is.
        """
        fam = self.plan.owner(cp.name, pc)
        if fam is None or (src is not None and fam.column(src) is not None):
            return self.label(cp, pc)
        if pc == fam.entry:
            return "Z%04X" % fam.entry
        lbl = "Q%04X" % pc
        if lbl not in self.extra:
            head = self.header(cp, fam, pc)
            self.extra[lbl] = Block(lbl, [Let(fam.var, Const(fam.column(pc)))], Goto(head), pc)
        return lbl

    def header(self, cp, fam, pc):
        """The block that reads copy ``v``'s columns and enters the body at ``pc``."""
        if pc == fam.entry:
            return "H%04X" % fam.entry
        if not fam.hoist:
            return self.label(cp, pc)
        lbl = "M%04X" % pc
        if lbl not in self.extra:
            self.extra[lbl] = Block(lbl, self.columns(fam), Goto(self.label(cp, pc)), pc)
        return lbl

    def columns(self, fam):
        """The per-copy columns, read once where one header dominates their uses."""
        if not fam.hoist:
            return []
        return [Let(fam.col(c), column(fam, c, w)) for c, (w, _v) in enumerate(fam.cols)]

    def fam_blocks(self, cp, fam):
        """The loop the copy index runs: ``v = 0``, the header its columns load in."""
        head, zero = "H%04X" % fam.entry, "Z%04X" % fam.entry
        mn = self.plan.node(cp.name, fam.entry, self.plan.op_at(cp.name, fam.entry))
        cover = mn.counts if mn is not None else ()
        n = sum(cover)
        return [
            Block(head, self.columns(fam), Goto(self.label(cp, fam.entry)), fam.entry, n, cover),
            Block(zero, [Let(fam.var, Const(0))], Goto(head), fam.entry, n),
        ]

    def build_proc(self, cp):
        phase = self.phase_of(cp)
        init_phase = bool(phase & PH_INIT)
        blocks = {}
        for pc, vs in cp.variant_switch.items():
            arms, sub = [], []
            for a in vs["arms"]:
                if a["unverified"] or (pc, a["opcode"]) not in cp.nodes:
                    sub.append(Block("V%04X_%02X" % (pc, a["opcode"]), [], Trap("unverified"), pc))
                    arms.append((a["opcode"], sub[-1].label))
                else:
                    arms.append((a["opcode"], "L%04X_%02X" % (pc, a["opcode"])))
            out = []
            e = tgt(self.store, vs["cell"], 1, init_phase, "V%04X" % pc, out)
            blocks["V%04X" % pc] = Block("V%04X" % pc, out, Switch(e, tuple(arms), ""), pc)
            for b in sub:
                blocks[b.label] = b
        built = set()
        for (pc, op), node in cp.nodes.items():
            if (cp.name, pc, op) in self.plan.absorbed:
                continue
            mn = self.plan.node(cp.name, pc, op)
            built.add((pc, op))
            for b in self.node_blocks(cp, pc, op, node if mn is None else mn.node, phase, mn):
                blocks[b.label] = b
        for pc, op, mn in self.plan.nodes_of(cp.name):  # a row copy 0 never ran
            if (pc, op) not in built:
                for b in self.node_blocks(cp, pc, op, mn.node, phase, mn):
                    blocks[b.label] = b
        for fam in self.plan.fams_of(cp.name):
            for b in self.fam_blocks(cp, fam):
                blocks[b.label] = b
        entry = self.enter(cp, cp.entry, None)
        blocks.update(self.extra)
        self.extra = {}
        return Proc(cp.name, (), (), blocks, entry, cp.kind)

    def node_blocks(self, cp, pc, op, node, phase, mn=None):
        lbl = "L%04X_%02X" % (pc, op)
        init_phase = bool(phase & PH_INIT)
        if mn is None:
            key = self.key_of(pc, op, phase)
            ls, resolve = self.lifted.get(key), self.store.resolver(key, init_phase)
        else:
            ls = mn.ls
            resolve = self.store.resolver_many(mn.keys, init_phase)
        # A copy that ran the row covers it: closed means no copy of it ran at all.
        closed = "static" if node.get("closed") and not (mn and any(mn.counts)) else ""
        if closed and ls is not None:
            resolve = static_resolver(ls, mn and mn.fam)
        fam = None if mn is None else mn.fam
        stmts = ops_to_stmts(ls.ops, resolve, lbl, pc, ls.src_map, fam) if ls is not None else []
        # A closed block has no copy coverage: it is nothing any copy failed to run.
        blk = Block(
            lbl,
            stmts,
            Trap("unstated" if closed else "unreached"),
            pc,
            node["count"],
            () if mn is None or closed else mn.counts,
            closed,
        )
        extra = []
        term = node["term"]
        if node["mnemonic"] in ("BRK", "JAM"):
            blk.term = Trap(node["mnemonic"].lower())
        elif term == "return":
            if node["mnemonic"] == "RTI":
                pop_status(blk.stmts, lbl)
            add_sp(blk.stmts, 2)
            blk.term = Return()
        elif term == "call":
            self._call(cp, blk, node, extra, init_phase, mn)
        elif term == "tail":
            blk.stmts.append(Call(self.byentry[node["call"][0]]))
            blk.term = Return()
        elif term == "branch":
            f = ls.ctrl[1] if ls is not None else ["r", 14, 1]
            cond = Bin("==", Var(REGVAR[f[1]]), Const(ls.ctrl[2] if ls is not None else 1), 1)
            arms = [self._succ(cp, blk, r, extra, i, mn) for i, r in enumerate(node["succ"])]
            if node["switch"] is not None:  # patched offset: the taken side is computed
                arms[0] = self._branch_switch(cp, blk, node, ls, extra, init_phase, mn)
            blk.term = If(cond, arms[0], arms[1])
        elif term == "switch":
            blk.term = self._switch(cp, blk, node, ls, extra, init_phase, mn)
        elif term == "goto":
            blk.term = Goto(self._succ(cp, blk, node["succ"][0], extra, 0, mn))
        out = [blk] + extra
        for b in out:
            b.closed = closed
        return out

    def _switch(self, cp, blk, node, ls, extra, init_phase, mn=None):
        """A computed jump: one switch, or one per copy under a switch on ``v``."""
        if mn is not None and "per" in node["switch"]:
            return self._percopy(cp, blk, node, extra, init_phase, mn)
        e = ctrl_expr(node, ls, self.store, node["pc"], init_phase, blk.label, blk.stmts)
        cases = tuple(
            (v, self._succ(cp, blk, r, extra, i, mn))
            for i, (v, r) in enumerate(node["switch"]["cases"])
        )
        return Switch(e, cases, "")

    def _percopy(self, cp, blk, node, extra, init_phase, mn):
        """``switch (v)`` into the dispatch each copy really has, arms folded.

        The cell holds the copy's own target, so the copies key on their own
        values; what pairs them is the arm body, which is one row of the family.
        """
        cases = []
        for j, sw in enumerate(node["switch"]["per"]):
            if sw is None:
                continue
            b = Block("P%s_%d" % (blk.label, j), [], None, blk.src, 0, blk.cover)
            ls = mn.per[j]
            nj = dict(node, switch=sw, pc=ls.pc)
            e = ctrl_expr(nj, ls, self.store, ls.pc, init_phase, b.label, b.stmts)
            arms = tuple(
                (v, self._succ(cp, b, r, extra, i, mn)) for i, (v, r) in enumerate(sw["cases"])
            )
            b.term = Switch(e, arms, "")
            extra.append(b)
            cases.append((j, b.label))
        return Switch(Var(mn.fam.var), tuple(cases), "")

    def _branch_switch(self, cp, blk, node, ls, extra, init_phase, mn=None):
        """The taken side of a branch whose offset is an SMC cell: a computed goto."""
        b = Block("S%s" % blk.label, [], None, blk.src, 0, blk.cover)
        b.term = self._switch(cp, b, node, ls, extra, init_phase, mn)
        extra.append(b)
        return b.label

    def _call(self, cp, blk, node, extra, init_phase, mn=None):
        ret = self._succ(cp, blk, node["succ"][0], extra, 0, mn)
        ls = self.lifted.get(node["key"])
        push16(
            blk.stmts, blk.label, (node["pc"] + (ls.length if ls else 3) - 1) & 0xFFFF, node["pc"]
        )
        if node["switch"] is None:
            blk.stmts.append(Call(self.byentry[node["call"][0]]))
            blk.term = Goto(ret)
            return
        cases = []
        for t in node["call"]:
            b = Block(
                "C%04X_%04X" % (node["pc"], t), [Call(self.byentry[t])], Goto(ret), node["pc"]
            )
            extra.append(b)
            cases.append((t, b.label))
        e = ctrl_expr(
            node,
            self.lifted.get(node["key"]),
            self.store,
            node["pc"],
            init_phase,
            blk.label,
            blk.stmts,
        )
        blk.term = Switch(e, tuple(cases), "")

    def _succ(self, cp, blk, ref, extra, i, mn=None):
        """A successor reference as a label: trap block, tail-call block, or the node."""
        if "per" in ref:
            return self._split(cp, blk, ref["per"], extra, i, mn)
        if "chain" in ref:
            return self._next_copy(cp, blk, ref["chain"], extra, i, mn.fam, ref["to"])
        fam = None if ref["trap"] or ref["tail"] else self.plan.chain(cp.name, blk.src, ref["to"])
        if fam is not None:
            out = {"to": 0, "tail": False, "trap": True}
            return self._next_copy(cp, blk, out, extra, i, fam, self.plan.tmpl(cp.name, ref["to"]))
        if ref["trap"]:
            b = Block("X%s_%d" % (blk.label, i), [], Trap("untaken"), blk.src)
        elif ref["tail"]:
            b = Block("T%s_%d" % (blk.label, i), [Call(self.byentry[ref["to"]])], Return(), blk.src)
        else:
            return self.enter(cp, ref["to"], blk.src)
        extra.append(b)
        return b.label

    def _split(self, cp, blk, refs, extra, i, mn):
        """``switch (v)``: the successor each copy really has, where they differ."""
        b = Block("P%s_%d" % (blk.label, i), [], None, blk.src, 0, blk.cover)
        extra.append(b)
        cases = tuple((j, self._succ(cp, b, r, extra, j, mn)) for j, r in enumerate(refs))
        b.term = Switch(Var(mn.fam.var), cases, "")
        return b.label

    def _next_copy(self, cp, blk, out, extra, i, fam, to):
        """The chain edge: ``v + 1``, into the next copy while one is left.

        Only the arm that advances takes the step, so an edge that leaves the family
        leaves ``v`` naming the copy it left; the step is the index's own statement
        and no copy's row, so it carries no coverage.
        """
        b = Block("I%s_%d" % (blk.label, i), [], None, blk.src, 0, blk.cover)
        extra.append(b)
        nxt = "%s_%s_%d" % (fam.var, blk.label, i)
        b.stmts.append(Let(nxt, Bin("+", Var(fam.var), Const(1), 1)))
        step = Block(
            "N%s_%d" % (blk.label, i),
            [Let(fam.var, Var(nxt, 1))],
            Goto(self.header(cp, fam, to)),
            blk.src,
        )
        extra.append(step)
        cond = Bin("<", Var(nxt, 1), Const(fam.k), 1)
        b.term = If(cond, step.label, self._succ(cp, b, out, extra, 0, None))
        return b.label


IRQ_ENTRY = "entry_irq"


def _irq_entry(procs, trace):
    """The machine's own entry action, ahead of an ``irq`` tick: the interrupt disable.

    The frame it pushed with it is the tick's contract, not a store of the program
    (:func:`~.frames.contract`), so only the flag the machine sets is a statement --
    and only where the tick has no caller of its own to enter it another way.
    """
    tick = next((n for n, p in procs.items() if p.kind == "tick"), None)
    if tick is None or trace.meta["entry"]["kind"] != "irq":
        return procs
    if any(tick in callees(p) for p in procs.values()):
        return procs
    proc = procs[tick]
    head = proc.blocks[proc.entry]
    proc.blocks[IRQ_ENTRY] = Block(
        IRQ_ENTRY, [Let(REGVAR[10], Const(1, 1))], Goto(proc.entry), head.src, head.count
    )
    proc.entry = IRQ_ENTRY
    return procs


def _seal(proc):
    """Every successor a block names must exist: an unbuilt one is an unreached trap."""
    for lbl in list(proc.blocks):
        for t in succs(proc.blocks[lbl].term):
            if t not in proc.blocks:
                proc.blocks[t] = Block(t, [], Trap("unreached"), proc.blocks[lbl].src)
    return proc


def _machine_image(trace):
    """Pre-init contents of the bands a tuneprog may read without an access relation.

    The load image, the stack page and the RAM under I/O are ``known`` to the
    machine from the start (the tracer pins no input for them), so a tuneprog that
    rebuilds its memory from storage alone needs their bytes even where no traced
    op touched them. The 6510 port ($00/$01) is machine state too: it decides
    whether $D000-$DFFF is I/O or RAM, and a tune that banks I/O out writes only
    the port byte, leaving the direction byte in no region at all.
    """
    lo, hi = trace.meta["load"]
    pre = trace.image_pre
    spans = (
        ("image_band", lo, hi),
        ("image_port", 0x0000, 0x0002),
        ("image_stack", 0x100, 0x200),
        ("image_io", 0xD000, 0xE000),
    )
    return [
        Rgn(-1 - i, n, a, b - a, "image", 1, bytes(pre[a:b]), ())
        for i, (n, a, b) in enumerate(spans)
    ]


def _copy_tables(plan):
    """One read-only region per distinct set of merged columns: copy by copy."""
    out = {}
    for f in plan.fams:
        if f.size:
            out.setdefault(
                f.rid,
                Rgn(f.rid, "copies_%04X" % f.entry, f.base, f.size, "copymap", 1, f.bytes(), ()),
            )
    return list(out.values())


def build_ir(trace, lifted, regions, procs, meta=None, plan=None):
    """The S2/S3 front-end result as a :class:`~.ir.Tuneprog` (design section 4)."""
    store = Storage(trace, regions)
    b = _Builder(trace, lifted, store, procs, plan)
    out = _irq_entry({name: b.build_proc(cp) for name, cp in procs.items()}, trace)
    for p in out.values():
        _seal(p)
    wire(out)
    tick = [p.name for p in procs.values() if p.kind == "tick"]
    m = dict(trace.meta)
    m.update(meta or {})
    m["tick_proc"] = tick[0] if tick else None
    m["init_proc"] = next((p.name for p in procs.values() if p.kind == "init"), None)
    m["stage"] = "S2"
    if b.plan.fams or b.plan.refused:
        m["copies"] = b.plan.to_dict()
    return Tuneprog(
        meta=m,
        storage=[
            Rgn(
                r.id,
                r.name,
                r.base,
                r.size,
                r.kind,
                r.stride,
                r.init_bytes,
                tuple(r.fields),
                r.origin,
            )
            for r in regions
        ]
        + _machine_image(trace)
        + _copy_tables(b.plan),
        inputs=[
            [k[0], k[1], v["kind"], v["count"], v["phase"]] for k, v in trace.input_sites.items()
        ],
        procs=out,
    )
