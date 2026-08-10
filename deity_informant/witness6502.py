"""witness6502: re-emit a frame program as 6502 and replay it under the VM.

Stage 4's round-trip witness, partial: the dialect (``idioms.FORMS``) and the
structured statements become machine code run on ``PcodeVM``, so a checked SID
write stream is witnessed with no evaluator in the trust chain. The computed
transfers resolve their pc through the compiled label table; the rest refuses.
"""

from __future__ import annotations

from . import frameproc as P
from . import grammar as G
from . import idioms
from . import structured as C
from .asm6502 import Asm
from .lifter import lift
from .vm import PcodeVM, run_sub

TEMP_BYTES = 512  # expression scratch; a deeper tree refuses rather than wraps
CTRL_BYTES = 5  # fault flag, saved stack pointer, compare accumulator, computed pc
VOLATILE = frozenset(C._VOL | C._VOL0)  # cycle-derived and constant-0 sources
IO = (0xD000, 0xE000)
LOW = (0x0000, 0x0200)  # page zero and the stack: never ours

_ASSOC = {"INT_ADD": "ADC", "INT_AND": "AND", "INT_OR": "ORA", "INT_XOR": "EOR"}
_UCMP = frozenset(("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL"))
_SCMP = frozenset(("INT_SLESS", "INT_SLESSEQUAL"))
_SIGNED = frozenset(("INT_SCARRY",))

_COMPUTED = frozenset(("dgoto", "igoto", "dcall", "dbr"))  # every statement that resolves a pc
_STMT_REFUSALS = {
    "swg": "the arm table with no computed goto before it (the pair is the dispatch)",
    "swc": "the arm table with no computed call before it (the pair is the dispatch)",
    "call": "the raw machine call (a JSR at a pc, not a serialized procedure call)",
    "callb": "the raw machine call carrying an inline continuation body",
}


class Refusal(NotImplementedError):
    """A construct the partial witness will not spell, named by its mechanism."""


class WitnessFault(RuntimeError):
    """The re-emitted machine reached an unobserved statement."""


def refuse(construct, mechanism):
    """Raise the named refusal: what is not covered, and who owns covering it."""
    raise Refusal(
        "witness6502 refuses %s -- %s; register-model-lift stage 4 owns the completion"
        % (construct, mechanism)
    )


def _pc(lbl):
    """A case label's own pc (or, for ``opsw``, its observed operand byte)."""
    return int(lbl[1:], 16)


def _paired(s, nxt):
    """The arm table belongs to the computed transfer before it, and to no other."""
    return (nxt[0] == "swg" and s[0] in ("dgoto", "igoto")) or (nxt[0] == "swc" and s[0] == "dcall")


def _walk(stmts):
    for s in stmts:
        yield s
        for b in P._stmt_bodies(s):
            yield from _walk(b)


def _loc_reads(n, out):
    if n[0] == "loc":
        out.append((n[1], P.loc_width(n)))
        return
    for c in P._kids(n):
        _loc_reads(c, out)


def _reserved(prog):
    """Every byte the program itself owns: data, state, traced code and I/O."""
    out = set(range(*LOW)) | set(range(*IO))
    for d in prog.data_decls:
        out.update(range(d["base"], d["base"] + d["size"]))
    out.update(idioms.state_cells(prog))
    ev = prog.evidence
    out.update(ev["code"], ev["leaders"], ev["written"], prog.dispatch)
    for addrs in ev["reads"].values():
        out.update(addrs)
    for tgts in ev["targets"].values():
        out.update(tgts)
    out.update((prog.play, prog.init))
    return out


def free_span(prog):
    """The longest run of zeroed image bytes the program owns no part of."""
    mem, taken = prog.mem0, _reserved(prog)
    best, run = (0, 0), 0
    for a in range(0x10000):
        if mem[a] or a in taken:
            if run > best[1]:
                best = (a - run, run)
            run = 0
        else:
            run += 1
    return best if run <= best[1] else (0x10000 - run, run)


class _Gen:  # pylint: disable=too-many-public-methods
    """Emit one frame program as 6502: a data block at ``org``, then the code.

    One method per dialect form and one per statement form, so the count is the
    vocabulary's."""

    def __init__(self, prog, org):
        self.prog = prog
        self.n = 0
        self.loops = []
        self.entries = {e for e, _pa, _r, _s in prog.procs}
        self.params = {e: tuple(pa) for e, pa, _r, _s in prog.procs}
        stmts = [s for _e, _pa, _r, st in prog.procs for s in _walk(st)]
        self.pcs = {s[1] for s in stmts if s[0] in ("label", "opsw")}
        self.rets = {(s[2] + 1) & 0xFFFF for s in stmts if s[0] == "dcall"} - self.pcs
        self.pcs |= self.rets
        self.computed = any(s[0] in _COMPUTED for s in stmts)
        self.lw = self._local_widths()
        self.fault, self.saved, self.cmp, self.dynv = org, org + 1, org + 2, org + 3
        base = org + CTRL_BYTES
        self.locs = {}
        for name in sorted(self.lw):
            self.locs[name] = base
            base += self.lw[name]
        self.temps = self.tp = base
        self.top = base + TEMP_BYTES
        self.p = Asm(self.top)

    def _local_widths(self):
        """One memory slot per local, at the width the program reads it."""
        w = {}

        def bump(name, width):
            w[name] = max(w.get(name, 1), width)

        def reads(s):
            out = []
            for x in P._stmt_exprs(s):
                _loc_reads(x, out)
            return out

        for _e, params, rets, stmts in self.prog.procs:
            for name in tuple(params) + tuple(rets):
                bump(name, 1)
            for s in _walk(stmts):
                if s[0] == "asg":
                    bump(s[1], P.loc_width(s[2]))
                elif s[0] == "for":
                    bump(s[1], 2 if max(s[2], s[3]) > 0xFF else 1)
                elif s[0] == "pcall":
                    for name in s[3]:
                        bump(name, 1)
                for name, width in reads(s):
                    bump(name, width)
        if P._SP in w:
            refuse("the stack-pointer local %r" % P._SP, "the machine's own SP is not a slot")
        for _e, _pa, _r, stmts in self.prog.procs:
            for s in _walk(stmts):
                for name, width in reads(s):
                    if width < w[name]:
                        refuse(
                            "the narrowing read of local %r (u%d of u%d)"
                            % (name, 8 * width, 8 * w[name]),
                            "the evaluator reads a local unmasked, memory reads it by width",
                        )
        return w

    def nl(self, tag="l"):
        self.n += 1
        return "%s%d" % (tag, self.n)

    def lda(self, a):
        self.p.i("LDA", "abs", a)

    def sta(self, a):
        self.p.i("STA", "abs", a)

    def imm(self, dst, vals):
        prev = None
        for j, b in enumerate(vals):
            if b != prev:
                self.p.i("LDA", "imm", b)
                prev = b
            self.sta(dst + j)

    def move(self, src, sw, dst, dw):
        """``dst`` gets ``src`` zero-extended or truncated to ``dw`` bytes."""
        n = min(sw, dw)
        if src != dst:
            for j in range(n):
                self.lda(src + j)
                self.sta(dst + j)
        if dw > n:
            self.p.i("LDA", "imm", 0)
            for j in range(n, dw):
                self.sta(dst + j)

    def push(self, n):
        a = self.tp
        self.tp += n
        if self.tp > self.top:
            refuse("the expression depth past %d scratch bytes" % TEMP_BYTES, "the temp stack")
        return a

    def flagbyte(self, dst, mn):
        """``dst`` gets 0 where branch ``mn`` takes and 1 where it does not.

        X is pre-zeroed by the caller: ``LDX #0`` writes Z, so it cannot sit
        between the flag-setting chain and its branch."""
        skip = self.nl("f")
        self.p.i(mn, "rel", ("L", skip))
        self.p.i("INX")
        self.p.label(skip)
        self.p.i("STX", "abs", dst)

    def signfix(self):
        """N gets the borrow chain's true sign: overflow flips bit 7, so undo it.

        Runs on the top byte's SBC result in A; the branch that reads N follows, and
        nothing between may write it."""
        skip = self.nl("v")
        self.p.i("BVC", "rel", ("L", skip))
        self.p.i("EOR", "imm", 0x80)
        self.p.label(skip)

    def value(self, n):
        """``(address, width)`` of a temp or local slot holding ``n``'s value."""
        k = n[0]
        if k == "const":
            t = self.push(n[2])
            self.imm(t, [(n[1] >> (8 * j)) & 0xFF for j in range(n[2])])
            return t, n[2]
        if k == "loc":
            return self.locs[n[1]], self.lw[n[1]]
        if k == "mem":
            return self.load(n)
        if k == "op":
            return self.op(n)
        return refuse("the expression node %r" % (k,), "no dialect leaf spells it")

    def at(self, n, w):
        """Address of a ``w``-byte little-endian copy of ``n``'s value."""
        a, aw = self.value(n)
        if aw >= w:
            return a
        t = self.push(w)
        self.move(a, aw, t, w)
        return t

    def load(self, n):
        addr, sz = n[1], n[2]
        if addr[0] == "const" and addr[2] == 2:
            for j in range(sz):
                c = (addr[1] + j) & 0xFFFF
                if c in VOLATILE:
                    refuse(
                        "the volatile input read at $%04X" % c,
                        "the evaluator pins it through iota, the machine reads the hardware",
                    )
            t = self.push(sz)
            for j in range(sz):
                self.lda((addr[1] + j) & 0xFFFF)
                self.sta(t + j)
            return t, sz
        t = self.push(sz)
        mark = self.tp
        lbls = [self.nl("ld") for _ in range(sz)]
        self.patch(self.at(addr, 2), lbls)
        for j, lb in enumerate(lbls):
            self.p.i("LDY", "imm", j)
            self.p.label(lb)
            self.p.i("LDA", "absy", 0)
            self.sta(t + j)
        self.tp = mark
        return t, sz

    def patch(self, a, lbls):
        """Write the computed address into each listed instruction's own operand."""
        for j in (0, 1):
            self.lda(a + j)
            for lb in lbls:
                self.sta(("L", lb, 1 + j))

    def op(self, n):
        mn, kids, w = n[1], n[2], n[3]
        if mn in _SIGNED:
            refuse("the signed operator %s" % mn, "the reference evaluator spells none either")
        if mn in ("COPY", "INT_ZEXT"):
            t = self.push(w)
            mark = self.tp
            a, aw = self.value(kids[0])
            self.move(a, aw, t, w)
            self.tp = mark
            return t, w
        if mn in _UCMP or mn in _SCMP or mn == "INT_CARRY":
            return self.compare(n)
        width = max([w] + [P.loc_width(c) for c in kids])
        t = self.push(width)
        mark = self.tp
        if mn in _ASSOC:
            self.assoc(t, width, mn, kids)
        elif mn == "INT_SUB":
            self.sub(t, width, kids)
        elif mn in ("INT_LEFT", "INT_RIGHT"):
            self.shift(t, width, mn, kids)
        else:
            refuse("the operator %s" % mn, "no dialect form spells it")
        self.tp = mark
        return t, w

    def assoc(self, t, width, mn, kids):
        self.move(self.at(kids[0], width), width, t, width)
        for k in kids[1:]:
            a = self.at(k, width)
            if mn == "INT_ADD":
                self.p.i("CLC")
            for j in range(width):
                self.lda(t + j)
                self.p.i(_ASSOC[mn], "abs", a + j)
                self.sta(t + j)

    def sub(self, t, width, kids):
        self.move(self.at(kids[0], width), width, t, width)
        a = self.at(kids[1], width)
        self.p.i("SEC")
        for j in range(width):
            self.lda(t + j)
            self.p.i("SBC", "abs", a + j)
            self.sta(t + j)

    def shift(self, t, width, mn, kids):
        if kids[1][0] != "const":
            refuse("the variable shift", "the machine shifts one constant place at a time")
        self.move(self.at(kids[0], width), width, t, width)
        k8, rem = divmod(kids[1][1], 8)
        left = mn == "INT_LEFT"
        for j in range(width - 1, -1, -1) if left else range(width):
            src = j - k8 if left else j + k8
            if 0 <= src < width:
                self.lda(t + src)
            else:
                self.p.i("LDA", "imm", 0)
            self.sta(t + j)
        for _ in range(rem if k8 < width else 0):
            lanes = range(width) if left else range(width - 1, -1, -1)
            for k, j in enumerate(lanes):
                head = ("ASL" if left else "LSR") if not k else ("ROL" if left else "ROR")
                self.p.i(head, "abs", t + j)

    def compare(self, n):
        mn, kids, w = n[1], n[2], n[3]
        width = max(P.loc_width(c) for c in kids)
        if mn == "INT_CARRY" and P.loc_width(kids[0]) != width:
            refuse(
                "the carry over operands of unequal width",
                "the threshold is the first operand's mask, which the wider side outgrows",
            )
        if mn in _SCMP and P.loc_width(kids[0]) != P.loc_width(kids[1]):
            refuse(
                "the signed compare over operands of unequal width",
                "the evaluator reads each side at its own width and the machine copy zero-extends",
            )
        t = self.push(w)
        mark = self.tp
        a, b = self.at(kids[0], width), self.at(kids[1], width)
        self.p.i("LDX", "imm", 0)
        if mn in ("INT_EQUAL", "INT_NOTEQUAL"):
            for j in range(width):
                if j:
                    self.sta(self.cmp)
                self.lda(a + j)
                self.p.i("EOR", "abs", b + j)
                if j:
                    self.p.i("ORA", "abs", self.cmp)
            self.flagbyte(t, "BNE" if mn == "INT_EQUAL" else "BEQ")
        else:
            swap = mn in ("INT_LESSEQUAL", "INT_SLESSEQUAL")
            lo, hi = (b, a) if swap else (a, b)
            self.p.i("CLC" if mn == "INT_CARRY" else "SEC")
            for j in range(width):
                self.lda(lo + j)
                self.p.i("ADC" if mn == "INT_CARRY" else "SBC", "abs", hi + j)
            if mn in _SCMP:
                self.signfix()
                self.flagbyte(t, "BPL" if mn == "INT_SLESS" else "BMI")
            else:
                self.flagbyte(t, "BCS" if mn == "INT_LESS" else "BCC")
        self.tp = mark
        self.move(t, 1, t, w)
        return t, w

    def body(self, stmts):
        i = 0
        while i < len(stmts):
            s, nxt = stmts[i], stmts[i + 1] if i + 1 < len(stmts) else None
            mark = self.tp
            if nxt is not None and _paired(s, nxt):
                self.switch(s, nxt)
                i += 1
            else:
                k = s[0]
                if k in _STMT_REFUSALS:
                    refuse("the statement %r" % k, _STMT_REFUSALS[k])
                fn = getattr(self, "_s_" + k, None)
                if fn is None:
                    refuse("the statement %r" % k, "no witnessed statement form spells it")
                fn(s)
            self.tp = mark
            i += 1

    def _s_label(self, s):
        self.p.label("pc_%04X" % s[1])

    def _s_asg(self, s):
        a, aw = self.value(s[2])
        self.move(a, aw, self.locs[s[1]], self.lw[s[1]])

    def _s_ret(self, _s):
        self.p.i("RTS")

    def _s_unobs(self, _s):
        self.p.i("JMP", "abs", ("L", "fault"))

    def _s_goto(self, s):
        self.p.i("JMP", "abs", ("L", self.target(s[1])))

    def target(self, pc):
        if pc in self.pcs:
            return "pc_%04X" % pc
        if pc in self.entries:
            return "sub_%04X" % pc
        return refuse("the transfer to $%04X" % pc, "no label of the compiled program names it")

    def _s_st(self, s):
        """Address first, then value: the order the reference evaluator reads them in."""
        sz = G.store_width(s[2])
        order = tuple(range(sz))[:: -1 if P.hi_first(s) else 1]
        if s[1][0] == "const" and s[1][2] == 2:
            v = self.at(s[2], sz)
            for j in order:
                self.lda(v + j)
                self.sta((s[1][1] + j) & 0xFFFF)
            return
        a = self.at(s[1], 2)
        v = self.at(s[2], sz)
        lbls = [self.nl("st") for _ in range(sz)]
        self.patch(a, lbls)
        for j in order:
            self.p.i("LDY", "imm", j)
            self.lda(v + j)
            self.p.label(lbls[j])
            self.p.i("STA", "absy", 0)

    def test(self, cond, taken):
        """Branch to a fresh label when ``cond``'s truth is ``taken``; return it."""
        mark = self.tp
        c, cw = self.value(cond)
        self.lda(c)
        for j in range(1, cw):
            self.p.i("ORA", "abs", c + j)
        self.tp = mark
        over, tgt = self.nl("o"), self.nl("t")
        self.p.i("BEQ" if taken else "BNE", "rel", ("L", over))
        self.p.i("JMP", "abs", ("L", tgt))
        self.p.label(over)
        return tgt

    def _s_if(self, s):
        _k, word, cond, then, els = s
        tgt = self.test(cond, word == "ifnot")
        self.body(then)
        if els:
            end = self.nl("e")
            self.p.i("JMP", "abs", ("L", end))
            self.p.label(tgt)
            self.body(els)
            self.p.label(end)
        else:
            self.p.label(tgt)

    def _s_loop(self, s):
        head, end = self.nl("h"), self.nl("x")
        self.p.label(head)
        self.loops.append((head, end))
        self.body(s[1])
        self.loops.pop()
        self.p.i("JMP", "abs", ("L", head))
        self.p.label(end)

    def _s_cont(self, _s):
        self.p.i("JMP", "abs", ("L", self.loops[-1][0]))

    def _s_brk(self, _s):
        self.p.i("JMP", "abs", ("L", self.loops[-1][1]))

    def _s_for(self, s):
        _k, name, init, last, body = s
        slot, width = self.locs[name], self.lw[name]
        self.imm(slot, [(init >> (8 * j)) & 0xFF for j in range(width)])
        head, test, end = self.nl("h"), self.nl("c"), self.nl("x")
        self.p.label(head)
        self.loops.append((test, end))
        self.body(body)
        self.loops.pop()
        self.p.label(test)
        for j in range(width):
            if j:
                self.sta(self.cmp)
            self.lda(slot + j)
            self.p.i("EOR", "imm", (last >> (8 * j)) & 0xFF)
            if j:
                self.p.i("ORA", "abs", self.cmp)
        over = self.nl("o")
        self.p.i("BNE", "rel", ("L", over))
        self.p.i("JMP", "abs", ("L", end))
        self.p.label(over)
        up = last >= init
        self.p.i("CLC" if up else "SEC")
        for j in range(width):
            self.lda(slot + j)
            self.p.i("ADC" if up else "SBC", "imm", 1 if j == 0 else 0)
            self.sta(slot + j)
        self.p.i("JMP", "abs", ("L", head))
        self.p.label(end)

    def _s_pcall(self, s):
        params = self.params[s[1]]
        if len(params) != len(s[2]):
            refuse("the call to sub_%04X" % s[1], "the argument list is not the parameter list")
        staged = []
        for arg in s[2]:
            t = self.push(P.loc_width(arg))
            a, aw = self.value(arg)
            self.move(a, aw, t, aw)
            staged.append((t, aw))
        for (t, w), name in zip(staged, params):
            self.move(t, w, self.locs[name], self.lw[name])
        self.p.i("JSR", "abs", ("L", "sub_%04X" % s[1]))

    def vector(self, s):
        """``dynv`` gets the image word an ``igoto`` reads, wrapping inside its own page."""
        if s[2] is None:
            refuse(
                "the static image vector at $%04X" % s[1],
                "its target's body follows the transfer inline, under no label of its own",
            )
        lo, hi = self.nl("v"), self.nl("v")
        self.patch(self.at(s[2], 2), [lo, hi])
        self.p.i("INC", "abs", ("L", hi, 1))  # the 6502 indirect wrap: the page never carries
        for lb, dst in ((lo, self.dynv), (hi, self.dynv + 1)):
            self.p.label(lb)
            self.p.i("LDA", "abs", 0)
            self.sta(dst)

    def dyn(self, s):
        """``dynv`` gets the statement's computed pc: the evaluator's ``dyn``, in a slot."""
        if s[0] == "igoto":
            self.vector(s)
        else:
            self.move(self.at(s[3] if s[0] == "dbr" else s[1], 2), 2, self.dynv, 2)

    def transfer(self, call=False):
        """Resolve ``dynv`` through the whole compiled table: what no arm claimed."""
        self.p.i("JSR" if call else "JMP", "abs", ("L", "resolve"))

    def chain(self, pairs, call=False):
        """Take the arm ``dynv`` names; fall through where the observed set holds none."""
        end = self.nl("d") if call else None
        for pc, lbl in pairs:
            nxt = self.nl("d")
            for j in (0, 1):
                self.lda(self.dynv + j)
                self.p.i("CMP", "imm", (pc >> (8 * j)) & 0xFF)
                self.p.i("BNE", "rel", ("L", nxt))
            self.p.i("JSR" if call else "JMP", "abs", ("L", lbl))
            if call:
                self.p.i("JMP", "abs", ("L", end))
            self.p.label(nxt)
        return end

    def retlabel(self, ret):
        """The JSR continuation a computed transfer may name (the evaluator's contmap)."""
        pc = (ret + 1) & 0xFFFF
        if pc in self.rets:
            self.rets.discard(pc)
            self.p.label("pc_%04X" % pc)

    def switch(self, s, sw):
        """A computed transfer whose observed arms follow it: the arms, then the table."""
        call = sw[0] == "swc"
        self.dyn(s)
        cases = sw[2] if call else sw[1]
        arms = [(_pc(lbl), self.nl("a")) for lbl, _b in cases]
        bare = [(_pc(lbl), self.target(_pc(lbl))) for lbl in (sw[1] if call else ())]
        end = self.chain(arms + bare, call)
        self.transfer(call)
        skip = self.nl("s")
        if call:
            self.p.label(end)
            self.retlabel(s[2])
            self.p.i("JMP", "abs", ("L", skip))
        for (_v, lbl), (_l, arm) in zip(arms, cases):
            self.p.label(lbl)
            self.body(arm)
            if call:
                self.p.i("RTS")
            else:
                self.p.i("JMP", "abs", ("L", "fault"))  # an arm that runs on is the fault
        if call:
            self.p.label(skip)

    def _s_dgoto(self, s):
        self.dyn(s)
        self.transfer()

    _s_igoto = _s_dgoto

    def _s_dbr(self, s):
        tgt = self.test(s[2], s[1] == "ifnot")
        self.dyn(s)
        self.transfer()
        self.p.label(tgt)

    def _s_dcall(self, s):
        self.dyn(s)
        self.transfer(True)
        self.retlabel(s[2])

    def _s_opsw(self, s):
        """The state-variable switch: the dispatch's own operand byte selects the arm."""
        if "pc_%04X" % s[1] not in self.p.labels:  # the switch's own label, where it has one
            self.p.label("pc_%04X" % s[1])
        self.lda(s[1])
        end = self.nl("s")
        arms = [(_pc(lbl), self.nl("a")) for lbl, _b in s[2]]
        for v, lbl in arms:
            nxt = self.nl("d")
            self.p.i("CMP", "imm", v & 0xFF)
            self.p.i("BNE", "rel", ("L", nxt))
            self.p.i("JMP", "abs", ("L", lbl))
            self.p.label(nxt)
        self.p.i("JMP", "abs", ("L", "fault"))
        for (_v, lbl), (_l, arm) in zip(arms, s[2]):
            self.p.label(lbl)
            self.body(arm)
            self.p.i("JMP", "abs", ("L", end))
        self.p.label(end)

    def resolver(self):
        """Every compiled label, keyed by its own pc: the evaluator's ``rmap`` on the machine.

        A computed target is a pc of the serialized program, not an address in the emitted
        image, so the machine resolves it here; a pc outside the observed set lands on the
        fault exactly as the evaluator's out-of-set target raises."""
        self.p.label("resolve")
        self.chain(sorted((pc, self.target(pc)) for pc in self.pcs | self.entries))
        self.p.i("JMP", "abs", ("L", "fault"))

    def build(self):
        """The assembled image: frame entry, fault landing, then the procedures."""
        p = self.p
        p.label("entry")
        p.i("TSX")
        p.i("STX", "abs", self.saved)
        if "a" in self.locs:
            self.imm(self.locs["a"], [0] * self.lw["a"])
        p.i("JMP", "abs", ("L", "sub_%04X" % self.prog.play))
        p.label("fault")
        p.i("INC", "abs", self.fault)
        p.i("LDX", "abs", self.saved)
        p.i("TXS")
        p.i("RTS")
        for entry, _params, _rets, stmts in self.prog.procs:
            p.label("sub_%04X" % entry)
            self.body(stmts)
            p.i("JMP", "abs", ("L", "fault"))
        if self.computed:
            self.resolver()
        return p.assemble()


class Witness:
    """A re-emitted program: its image, its per-frame entry, and its replay."""

    def __init__(self, prog, mem, entry, fault, code):
        self.prog, self.mem, self.entry, self.fault, self.code = prog, mem, entry, fault, code

    def frames(self, nframes):
        """Per-frame ``(reg, val)`` SID writes of ``nframes`` machine invocations."""
        vm = PcodeVM(bytes(self.mem))
        cache, out = {}, []
        for _f in range(nframes):
            vm.wlog = []
            run_sub(vm, self.entry, cache, lift)
            if vm.mem[self.fault]:
                raise WitnessFault("re-emitted frame reached an unobserved statement")
            out.append([(r, v) for _c, r, v in vm.wlog])
        return out


def emit(prog, org=None):
    """Re-emit ``prog`` as 6502 into its own image; ``Witness`` replays it."""
    if prog.inputs:
        refuse(
            "the declared volatile inputs (%s)" % " ".join(prog.inputs),
            "the evaluator pins them through iota, the machine reads the hardware itself",
        )
    if prog.extents:
        refuse(
            "the extent-guarded deref (%d pointer extent(s))" % len(prog.extents),
            "the `in` clause is a frame fault the witness raises no equivalent of",
        )
    base, size = free_span(prog) if org is None else (org, 0x10000 - org)
    if size < CTRL_BYTES + TEMP_BYTES:
        refuse("the emission into this image", "no free span holds the witness data block")
    gen = _Gen(prog, base)
    code = gen.build()
    if gen.p.org + len(code) > base + size:
        refuse("the emission into this image", "the free span is shorter than the emitted code")
    mem = bytearray(prog.mem0)
    mem[gen.p.org : gen.p.org + len(code)] = code
    mem[base : base + CTRL_BYTES] = bytes(CTRL_BYTES)
    return Witness(prog, mem, gen.p.labels["entry"], gen.fault, code)
