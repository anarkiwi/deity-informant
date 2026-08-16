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
    Load,
    Proc,
    REGIDX,
    REGVAR,
    Return,
    Rgn,
    Store,
    Switch,
    Trap,
    Tuneprog,
    Var,
    succs,
)
from .regions import index_regions
from .ssa import liveness

BINOP = {
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_AND": "&",
    "INT_OR": "|",
    "INT_XOR": "^",
    "INT_LEFT": "<<",
    "INT_RIGHT": ">>",
    "INT_EQUAL": "==",
    "INT_NOTEQUAL": "!=",
    "INT_LESS": "<",
    "INT_LESSEQUAL": "<=",
    "INT_CARRY": "carry",
}
PH_INIT = 1
STACK = (0x0100, 0x01FF)


def _vn(v, blk):
    """A varnode as an IR expression (registers by name, uniques per block)."""
    if v[0] == "c":
        return Const(v[1], v[2])
    if v[0] == "r":
        return Var(REGVAR[v[1]], v[2])
    return Var("u%d_%s" % (v[1], blk), v[2])


def _name(v, blk):
    return REGVAR[v[1]] if v[0] == "r" else "u%d_%s" % (v[1], blk)


def ops_to_stmts(ops, resolve=None, blk="0", src=0, src_map=None):
    """Residualised P-Code ``ops`` as IR statements.

    ``resolve(op index, size, is_store) -> (cls, lo, hi, region id)`` types each
    memory access; the default is untyped RAM over the whole address space.
    """
    out = []
    for i, (mn, res, ins) in enumerate(ops):
        j = src_map[i] if src_map is not None else i
        if mn == "STORE":
            cls, lo, hi, rid = resolve(j, ins[1][2], True) if resolve else ("ram", 0, 0xFFFF, -1)
            out.append(Store(cls, _vn(ins[0], blk), _vn(ins[1], blk), ins[1][2], lo, hi, rid, src))
        elif mn == "LOAD":
            cls, lo, hi, rid = resolve(j, res[2], False) if resolve else ("ram", 0, 0xFFFF, -1)
            out.append(Let(_name(res, blk), Load(cls, _vn(ins[0], blk), res[2], lo, hi, rid)))
        elif mn in ("COPY", "INT_ZEXT"):
            out.append(Let(_name(res, blk), _vn(ins[0], blk)))
        else:
            w = ins[0][2] if mn == "INT_CARRY" else res[2]
            out.append(Let(_name(res, blk), Bin(BINOP[mn], _vn(ins[0], blk), _vn(ins[1], blk), w)))
    return out


def straightline(ops, name="f", resolve=None):
    """A one-block :class:`~.ir.Proc` over every register (the fuzz-test shape)."""
    regs = tuple(range(16))
    blocks = {
        "b0": Block("b0", ops_to_stmts(ops, resolve), Return(tuple(Var(REGVAR[i]) for i in regs)))
    }
    return Proc(name, regs, regs, blocks, "b0", "sub")


class _Storage:
    """Resolves an access to (class, envelope, region) from the trace's access relation."""

    def __init__(self, trace, regions):
        self.by_addr = index_regions(regions)
        self.acc = {}
        for r in regions:
            for a in r.accessors:
                self.acc.setdefault((tuple(a["site"]), a["op"]), (a["extent"], r))
        lo, hi = trace.meta["load"]
        self.k0 = bytearray(0x10000)
        self.k0[lo:hi] = b"\1" * (hi - lo)
        self.k0[STACK[0] : STACK[1] + 1] = b"\1" * 0x100
        self.k1 = bytearray(self.k0)
        for a in trace.written_init:
            self.k1[a] = 1

    def cls(self, lo, hi, kind, init_phase):
        if kind == "io":
            return "io"
        k = self.k0 if init_phase else self.k1
        return "ram" if all(k[a] for a in range(lo, hi + 1)) else "chk"

    def at(self, addr, size, init_phase):
        """Type an access at a known constant address (control cells, stack)."""
        r = self.by_addr.get(addr)
        lo, hi = (addr, addr + size - 1) if r is None else (r.base, r.base + r.size - 1)
        kind = "io" if r is None and 0xD000 <= addr <= 0xDFFF else (r.kind if r else "state")
        return self.cls(addr, addr + size - 1, kind, init_phase), lo, hi, (r.id if r else -1)

    def resolver(self, key, init_phase):
        def resolve(i, size, _store):
            hit = self.acc.get((key, i))
            if hit is None:
                return "chk", 0, 0xFFFF, -1
            (lo, hi), r = hit
            return self.cls(lo, hi, r.kind, init_phase), lo, max(hi, lo + size - 1), r.id

        return resolve


SP = REGVAR[3]
STATUS_BITS = ((8, 0), (9, 1), (10, 2), (11, 3), (13, 6), (14, 7))


def _spaddr(out, blk, tag):
    """``$0100 | SP`` as an expression (the stack pointer's current byte)."""
    n = "sp%s_%s" % (tag, blk)
    out.append(Let(n, Bin("|", Const(0x100, 2), Var(SP), 2)))
    return Var(n, 2)


def _add_sp(out, delta):
    out.append(Let(SP, Bin("+" if delta > 0 else "-", Var(SP), Const(abs(delta)), 1)))


def _push16(out, blk, val, src):
    """The JSR frame: return address high byte then low, SP down by two."""
    for tag, e in (("h", Const((val >> 8) & 0xFF)), ("l", Const(val & 0xFF))):
        a = _spaddr(out, blk, tag)
        out.append(Store("raw", a, e, 1, 0x100, 0x1FF, -1, src))
        _add_sp(out, -1)


def _pop_status(out, blk):
    """The RTI frame: status byte back into the six flag registers, then the pc."""
    _add_sp(out, 1)
    a = _spaddr(out, blk, "p")
    out.append(Let("pstat_%s" % blk, Load("ram", a, 1, 0x100, 0x1FF, -1)))
    for idx, sh in STATUS_BITS:
        src = Var("pstat_%s" % blk, 1)
        out.append(
            Let(
                REGVAR[idx],
                Bin("&", src if not sh else Bin(">>", src, Const(sh), 1), Const(1), 1),
            )
        )


def _tgt(store, addr, size, init_phase, blk, out):
    """Emit the load of a computed-control cell; returns its expression."""
    cls, lo, hi, rid = store.at(addr, size, init_phase)
    n = "t_%s" % blk
    out.append(Let(n, Load(cls, Const(addr, 2), size, lo, hi, rid)))
    return Var(n, size)


def _ctrl_expr(node, ls, store, pc, init_phase, blk, out):
    """The switch expression of a computed jump/branch/return, with its loads."""
    ex = node["switch"]["expr"]
    if ex["kind"] == "stack":
        for half in ("lo", "hi"):
            _add_sp(out, 1)
            a = _spaddr(out, blk, half)
            out.append(Let("p_%s_%s" % (half, blk), Load("ram", a, 1, 0x100, 0x1FF, -1)))
        w = Bin("|", Var("p_lo_%s" % blk, 1), Bin("<<", Var("p_hi_%s" % blk, 1), Const(8), 2), 2)
        return Bin("+", w, Const(1, 2), 2)
    if ex["kind"] == "jmpind":
        ptr = ex["ptr"]
        lo8 = _tgt(store, ptr, 1, init_phase, blk + "l", out)
        hi8 = _tgt(store, (ptr & 0xFF00) | ((ptr + 1) & 0xFF), 1, init_phase, blk + "h", out)
        return Bin("|", lo8, Bin("<<", hi8, Const(8), 2), 2)
    cell = _tgt(store, ex["addr"], ex["size"], init_phase, blk, out)
    if ex["size"] == 2 or ls is None or ls.ctrl[0] != "br":
        return cell
    base = Bin("+", Const((pc + 2) & 0xFFFF, 2), cell, 2)
    return Bin("-", base, Bin("<<", Bin("&", cell, Const(0x80), 1), Const(1), 2), 2)


class _Builder:
    """One IR procedure per :class:`~.cfg.Proc`."""

    def __init__(self, trace, lifted, store, procs):
        self.trace = trace
        self.lifted = lifted
        self.store = store
        self.procs = procs
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
        if pc in cp.variant_switch:
            return "V%04X" % pc
        ops = [op for (p, op) in cp.nodes if p == pc]
        return "L%04X_%02X" % (pc, ops[0]) if ops else "X%04X" % pc

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
            e = _tgt(self.store, vs["cell"], 1, init_phase, "V%04X" % pc, out)
            blocks["V%04X" % pc] = Block("V%04X" % pc, out, Switch(e, tuple(arms), ""), pc)
            for b in sub:
                blocks[b.label] = b
        for (pc, op), node in cp.nodes.items():
            for b in self.node_blocks(cp, pc, op, node, phase):
                blocks[b.label] = b
        proc = Proc(cp.name, (), (), blocks, self.label(cp, cp.entry), cp.kind)
        return proc

    def node_blocks(self, cp, pc, op, node, phase):
        lbl = "L%04X_%02X" % (pc, op)
        key = self.key_of(pc, op, phase)
        ls = self.lifted.get(key)
        init_phase = bool(phase & PH_INIT)
        stmts = (
            ops_to_stmts(ls.ops, self.store.resolver(key, init_phase), lbl, pc, ls.src_map)
            if ls is not None
            else []
        )
        blk = Block(lbl, stmts, Trap("unreached"), pc, node["count"])
        extra = []
        term = node["term"]
        if node["mnemonic"] in ("BRK", "JAM"):
            blk.term = Trap(node["mnemonic"].lower())
        elif term == "return":
            if node["mnemonic"] == "RTI":
                _pop_status(blk.stmts, lbl)
            _add_sp(blk.stmts, 2)
            blk.term = Return()
        elif term == "call":
            self._call(cp, blk, node, extra, init_phase)
        elif term == "tail":
            blk.stmts.append(Call(self.byentry[node["call"][0]]))
            blk.term = Return()
        elif term == "branch":
            f = ls.ctrl[1] if ls is not None else ["r", 14, 1]
            cond = Bin("==", Var(REGVAR[f[1]]), Const(ls.ctrl[2] if ls is not None else 1), 1)
            arms = [self._succ(cp, blk, r, extra, i) for i, r in enumerate(node["succ"])]
            blk.term = If(cond, arms[0], arms[1])
        elif term == "switch":
            e = _ctrl_expr(node, ls, self.store, pc, init_phase, lbl, blk.stmts)
            cases = tuple(
                (v, self._succ(cp, blk, r, extra, i))
                for i, (v, r) in enumerate(node["switch"]["cases"])
            )
            blk.term = Switch(e, cases, "")
        elif term == "goto":
            blk.term = Goto(self._succ(cp, blk, node["succ"][0], extra, 0))
        return [blk] + extra

    def _call(self, cp, blk, node, extra, init_phase):
        ret = self._succ(cp, blk, node["succ"][0], extra, 0)
        ls = self.lifted.get(node["key"])
        _push16(
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
        e = _ctrl_expr(
            node,
            self.lifted.get(node["key"]),
            self.store,
            node["pc"],
            init_phase,
            blk.label,
            blk.stmts,
        )
        blk.term = Switch(e, tuple(cases), "")

    def _succ(self, cp, blk, ref, extra, i):
        """A successor reference as a label: trap block, tail-call block, or the node."""
        if ref["trap"]:
            b = Block("X%s_%d" % (blk.label, i), [], Trap("untaken"), blk.src)
        elif ref["tail"]:
            b = Block("T%s_%d" % (blk.label, i), [Call(self.byentry[ref["to"]])], Return(), blk.src)
        else:
            return self.label(cp, ref["to"])
        extra.append(b)
        return b.label


def _seal(proc):
    """Every successor a block names must exist: an unbuilt one is an unreached trap."""
    for lbl in list(proc.blocks):
        for t in succs(proc.blocks[lbl].term):
            if t not in proc.blocks:
                proc.blocks[t] = Block(t, [], Trap("unreached"), proc.blocks[lbl].src)
    return proc


def _wire(procs):
    """Fill in params/rets/args by liveness over the (acyclic) call graph."""
    order, seen = [], set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for c in _callees(procs[n]):
            visit(c)
        order.append(n)

    for n in procs:
        visit(n)
    for n in order:
        p = procs[n]
        rets = {REGIDX[s.n] for b in p.blocks.values() for s in b.stmts if _isreg(s)}
        for c in _callees(p):
            rets |= set(procs[c].rets)
        p.rets = tuple(sorted(rets))
        vals = tuple(Var(REGVAR[i]) for i in p.rets)
        for b in p.blocks.values():
            if type(b.term) is Return:
                b.term = Return(vals)
            for s in b.stmts:
                if type(s) is Call:
                    q = procs[s.proc]
                    s.args = tuple(Var(REGVAR[i]) for i in q.params)
                    s.rets = tuple(REGVAR[i] for i in q.rets)
        p.params = tuple(sorted({REGIDX[n] for n in liveness(p)[p.entry]} | set(p.rets)))
    return procs


def _isreg(s):
    return type(s) is Let and s.n in REGIDX


def _callees(p):
    return {s.proc for b in p.blocks.values() for s in b.stmts if type(s) is Call}


def _machine_image(trace):
    """Pre-init contents of the bands a tuneprog may read without an access relation.

    The load image, the stack page and the RAM under I/O are ``known`` to the
    machine from the start (the tracer pins no input for them), so a tuneprog that
    rebuilds its memory from storage alone needs their bytes even where no traced
    op touched them.
    """
    lo, hi = trace.meta["load"]
    pre = trace.image_pre
    spans = (("image_band", lo, hi), ("image_stack", 0x100, 0x200), ("image_io", 0xD000, 0xE000))
    return [
        Rgn(-1 - i, n, a, b - a, "image", 1, bytes(pre[a:b]), ())
        for i, (n, a, b) in enumerate(spans)
    ]


def build_ir(trace, lifted, regions, procs, meta=None):
    """The S2/S3 front-end result as a :class:`~.ir.Tuneprog` (design section 4)."""
    store = _Storage(trace, regions)
    b = _Builder(trace, lifted, store, procs)
    out = {name: b.build_proc(cp) for name, cp in procs.items()}
    for p in out.values():
        _seal(p)
    _wire(out)
    tick = [p.name for p in procs.values() if p.kind == "tick"]
    m = dict(trace.meta)
    m.update(meta or {})
    m["tick_proc"] = tick[0] if tick else None
    m["init_proc"] = next((p.name for p in procs.values() if p.kind == "init"), None)
    m["stage"] = "S2"
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
        + _machine_image(trace),
        inputs=[
            [k[0], k[1], v["kind"], v["count"], v["phase"]] for k, v in trace.input_sites.items()
        ],
        procs=out,
    )
