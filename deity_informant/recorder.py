"""Symbolic window recorder: per-invocation partial evaluation of the 6510 VM.

``record`` runs a driver ``invocations`` times, bit-identically to
:class:`~deity_informant.vm.PcodeVM`, residualising data flow over the entry
state and recording folds as facts. See docs/symbolic-recorder.md.
"""

from __future__ import annotations

from . import expr as E
from .lifter import lift
from .vm import PcodeVM

_VOL = frozenset({0xD019, 0xDC0D})
_SIG_MASK = (1 << 64) - 1


def _volatile(addr):
    return addr in _VOL or 0xD011 <= addr <= 0xD41C


class Recording:
    """Per-invocation artifacts (index ``i`` selects the invocation)."""

    def __init__(self, outputs):
        self.outputs = set(outputs)
        self.F = []
        self.facts = []
        self.slog = []
        self.out_seq = []
        self.entry = []
        self._uni = []

    def replay(self, i):
        """Reconstruct invocation ``i``'s observable write sequence from ``slog``
        + the entry snapshot (evaluates the recorded templates)."""
        emem, ereg = self.entry[i]
        uni = self._uni[i]
        image = bytearray(emem)
        out = []
        for _pos, addr, ex, sz in self.slog[i]:
            v = E.evaluate(ex, emem, ereg, image, uni)
            for k in range(sz):
                image[(addr + k) & 0xFFFF] = (v >> (8 * k)) & 0xFF
            if addr in self.outputs:
                out.append((addr, v))
        return out


class RecVM(PcodeVM):
    """PcodeVM that also builds symbolic templates while executing concretely."""

    def __init__(self, mem_bytes):
        super().__init__(mem_bytes)
        self.outputs = set()
        self.mutable = set()
        self.alias_sites = set()
        self.collect = False
        self.emit = True
        self.assertion = True
        self.sig = 0
        self.reset_invocation()

    def reset_invocation(self):
        self.entry_mem = bytes(self.mem)
        self.entry_reg = list(self.reg)
        self.sreg = [E.reg(i) for i in range(16)]
        self.suni = {}
        self.cell_expr = {}
        self.cell_ver = {}
        self.written = set()
        self.events = []
        self.pos = 0
        self.out_seq = []
        self.uni_vals = {}
        self.F = {}
        self.sig = 0xCBF29CE484222325

    def _mix(self, x):
        """Fold a control-flow/address quantity into the per-invocation signature.

        The signature abstracts the concrete run to (executed instruction
        identities + effective addresses of every memory access). Two invocations
        with equal signatures took the identical control-flow path over the
        identical addresses, so they residualise to the byte-identical symbolic
        template -- only the entry snapshot and volatile reads differ.
        """
        self.sig = ((self.sig ^ (x & _SIG_MASK)) * 0x100000001B3) & _SIG_MASK

    # ---- symbolic memory leaves --------------------------------------------
    def _byte(self, addr):
        addr &= 0xFFFF
        if addr in self.written:
            return E.cur(E.konst(addr, 2), 1, self.cell_ver[addr], self.cell_expr[addr])
        return E.mem(E.konst(addr, 2), 1)

    def _word(self, lo, hi):
        return E.op(
            "INT_OR",
            [
                E.op("INT_ZEXT", [lo], 2),
                E.op("INT_LEFT", [E.op("INT_ZEXT", [hi], 2), E.konst(8, 1)], 2),
            ],
            2,
        )

    def _residual(self, srcs, fn, pc, sz):
        if fn == "word":
            return self._word(self._byte(pc + srcs[0]), self._byte(pc + srcs[1]))
        leaf = self._byte(pc + srcs[0])
        if fn == "hi1":
            leaf = E.op("INT_ADD", [leaf, E.konst(1, 1)], 1)
        return E.op("INT_ZEXT", [leaf], sz) if sz > 1 else leaf

    # ---- fact / store logging ----------------------------------------------
    def _fact(self, site, kind, ex, observed):
        entry = E.simplify(E.to_entry(ex))
        evolved = E.simplify(E.to_evolved(ex, self.cell_ver))
        self.events.append(("fact", self.pos, site, kind, entry, evolved, observed))
        self.pos += 1

    def _store(self, addr, saddr, sval, sz, cval):
        addr &= 0xFFFF
        entry = E.simplify(E.to_entry(sval))
        evolved = E.simplify(E.to_evolved(sval, self.cell_ver))
        if not E.is_const(saddr):
            self._fact(addr, "place", saddr, addr)
        self.events.append(("store", self.pos, addr, evolved, sz, cval & E.mask(sz)))
        self.pos += 1
        if addr in self.outputs:
            self.out_seq.append((addr, evolved))
        self.cell_expr[addr] = entry
        self.cell_ver[addr] = self.cell_ver.get(addr, 0) + 1
        self.written.add(addr)

    # ---- op-list interpreter (concrete + symbolic) -------------------------
    def _cval(self, vn):
        sp = vn[0]
        if sp == "c":
            return vn[1]
        return (self.reg if sp == "r" else self.uniq)[vn[1]]

    def _sval(self, vn, i, j, pmap, pc):
        sp = vn[0]
        if sp == "r":
            return self.sreg[vn[1]]
        if sp == "u":
            return self.suni[vn[1]]
        p = pmap.get((i, j))
        if p is not None and any((pc + off) & 0xFFFF in self.mutable for off in p[0]):
            return self._residual(p[0], p[1], pc, vn[2])
        return E.konst(vn[1], vn[2])

    def _setout(self, out, cval, sexpr):
        cval &= E.mask(out[2])
        (self.reg if out[0] == "r" else self.uniq)[out[1]] = cval
        if self.emit:
            (self.sreg if out[0] == "r" else self.suni)[out[1]] = sexpr

    def _exec(self, rec, pc):
        prov = rec["prov"]
        pmap = prov["ops"]
        emit = self.emit
        if emit:
            self._justify(pc, prov)
        for i, (mn, out, ins) in enumerate(rec["ops"]):
            cvals = [self._cval(vn) for vn in ins]
            if mn == "STORE":
                sz = ins[1][2]
                self._mix(0x51E0000 ^ (cvals[0] & 0xFFFF))
                self._wr(cvals[0], cvals[1], sz)
                if self.collect:
                    self._mark(cvals[0] & 0xFFFF, sz)
                elif emit:
                    self._store(
                        cvals[0] & 0xFFFF,
                        self._sval(ins[0], i, 0, pmap, pc),
                        self._sval(ins[1], i, 1, pmap, pc),
                        sz,
                        cvals[1],
                    )
                continue
            if mn == "LOAD":
                addr, sz = cvals[0] & 0xFFFF, out[2]
                self._mix(0x10AD0000 ^ addr)
                cval = self._rd(cvals[0], sz)
                if self.collect:
                    if self._aliases(addr, sz):
                        self.alias_sites.add((pc, i))
                    sexpr = None
                elif emit:
                    saddr = self._sval(ins[0], i, 0, pmap, pc)
                    sexpr = self._loadsym((pc, i), addr, saddr, sz, cval)
                else:  # repeat frame: mirror the emit path's uni allocation order
                    if sz != 1 or (self.volatile and _volatile(addr)):
                        self._newuni(cval, sz)
                    sexpr = None
                self._setout(out, cval, sexpr)
                continue
            cval = E._apply(mn, cvals, [vn[2] for vn in ins], out[2])
            if not emit:
                self._setout(out, cval, None)
                continue
            svals = [self._sval(vn, i, j, pmap, pc) for j, vn in enumerate(ins)]
            sexpr = svals[0] if mn == "COPY" else E.op(mn, svals, out[2])
            self._setout(out, cval, sexpr)

    def _mark(self, addr, sz):
        for k in range(sz):
            self.written.add((addr + k) & 0xFFFF)

    def _aliases(self, addr, sz):
        return sz == 1 and not (self.volatile and _volatile(addr)) and addr in self.written

    def _loadsym(self, site, addr, saddr, sz, cval):
        if sz != 1:
            return E.uni(self._newuni(cval, sz), sz)
        if self.volatile and _volatile(addr):
            return E.uni(self._newuni(cval, 1), 1)
        if addr in self.written:
            if not E.is_const(saddr):
                self._fact(site[0], "place", saddr, addr)
            return E.cur(E.konst(addr, 2), 1, self.cell_ver[addr], self.cell_expr[addr])
        if site in self.alias_sites and not E.is_const(saddr):
            self._fact(site[0], "place", saddr, addr)
        return E.mem(saddr, 1)

    def _newuni(self, cval, sz):
        n = len(self.uni_vals)
        self.uni_vals[n] = cval & E.mask(sz)
        return n

    def _justify(self, pc, prov):
        if pc in self.mutable:
            self._fact(pc, "opcode", self._byte(pc), prov["op0"])
        ctrlp = prov["ctrl"]
        if ctrlp is not None:
            srcs, fn, val = ctrlp
            if any((pc + off) & 0xFFFF in self.mutable for off in srcs):
                self._fact(pc, "target", self._ctrl_expr(srcs, fn, pc), val)

    def _ctrl_expr(self, srcs, fn, pc):
        if fn == "word":
            return self._word(self._byte(pc + srcs[0]), self._byte(pc + srcs[1]))
        b = self._byte(pc + srcs[0])
        msb = E.op("INT_AND", [E.op("INT_RIGHT", [b, E.konst(7, 1)], 1), E.konst(1, 1)], 1)
        corr = E.op("INT_LEFT", [E.op("INT_ZEXT", [msb], 2), E.konst(8, 1)], 2)
        base = E.op("INT_ADD", [E.konst((pc + 2) & 0xFFFF, 2), E.op("INT_ZEXT", [b], 2)], 2)
        return E.op("INT_SUB", [base, corr], 2)

    # ---- control transfer with symbolic stack traffic ----------------------
    def step(self, pc, cache, lifter):
        mem = self.mem
        k = (pc, mem[pc], mem[(pc + 1) & 0xFFFF], mem[(pc + 2) & 0xFFFF])
        self._mix((pc << 24) ^ (k[1] << 16) ^ (k[2] << 8) ^ k[3])
        rec = cache.get(k)
        if rec is None:
            rec = lifter(mem, pc)
            cache[k] = rec
        ctrl, nxt = self.run_record(rec, pc)
        t = ctrl[0]
        if t == "next":
            return (pc + rec["len"]) & 0xFFFF
        if t == "br":
            if self.emit:
                self._branch(pc, ctrl[1][1], ctrl[2])
            return nxt
        if t == "jmp":
            return ctrl[1]
        if t == "jmpind":
            return self._jmpind(pc, ctrl[1])
        if t == "jsr":
            return self._jsr(pc, rec, ctrl[1])
        if t == "rts":
            return self._rts(pc)
        if t == "rti":
            return self._rti(pc)
        if t == "brk":
            return self._brk(pc)
        raise RuntimeError("JAM at %04X" % pc)

    def _branch(self, pc, fidx, pol):
        pred = E.op("INT_EQUAL", [self.sreg[fidx], E.konst(pol, 1)], 1)
        self._fact(pc, "branch", pred, 1 if self.reg[fidx] == pol else 0)

    def _pushb(self, cval, sexpr):
        addr = 0x100 + self.reg[3]
        self.mem[addr] = cval & 0xFF
        if self.collect:
            self.written.add(addr & 0xFFFF)
        elif self.emit:
            self._store(addr, E.konst(addr, 2), sexpr, 1, cval)
        self.reg[3] = (self.reg[3] - 1) & 0xFF
        if self.emit:
            self.sreg[3] = E.op("INT_SUB", [self.sreg[3], E.konst(1, 1)], 1)

    def _push(self, val):
        self._pushb(val, E.konst(val, 1))

    def _push_status(self):
        self._pushb(self._status(), self._status_expr(0) if self.emit else None)

    def _pull(self):
        self.reg[3] = (self.reg[3] + 1) & 0xFF
        if self.emit:
            self.sreg[3] = E.op("INT_ADD", [self.sreg[3], E.konst(1, 1)], 1)
        addr = 0x100 + self.reg[3]
        return self.mem[addr], (self._byte(addr) if self.emit else None)

    def _jsr(self, pc, rec, target):
        ret = (pc + rec["len"] - 1) & 0xFFFF
        self._pushb((ret >> 8) & 0xFF, E.konst((ret >> 8) & 0xFF, 1))
        self._pushb(ret & 0xFF, E.konst(ret & 0xFF, 1))
        return target

    def _rts(self, pc):
        lo_c, lo_e = self._pull()
        hi_c, hi_e = self._pull()
        target = (((hi_c << 8) | lo_c) + 1) & 0xFFFF
        if self.emit:
            tex = E.op("INT_ADD", [self._word(lo_e, hi_e), E.konst(1, 2)], 2)
            self._fact(pc, "target", tex, target)
        return target

    def _rti(self, pc):
        st_c, st_e = self._pull()
        lo_c, lo_e = self._pull()
        hi_c, hi_e = self._pull()
        self._set_flags(st_c)
        if self.emit:
            self._restore_flags(st_e)
            self._fact(pc, "target", self._word(lo_e, hi_e), ((hi_c << 8) | lo_c) & 0xFFFF)
        return ((hi_c << 8) | lo_c) & 0xFFFF

    def _brk(self, pc):
        ret = (pc + 2) & 0xFFFF
        self._pushb((ret >> 8) & 0xFF, E.konst((ret >> 8) & 0xFF, 1))
        self._pushb(ret & 0xFF, E.konst(ret & 0xFF, 1))
        self._pushb(self._status(brk=1), None if self.collect else self._status_expr(1))
        self.reg[10] = 1
        if self.emit:
            self.sreg[10] = E.konst(1, 1)
        target = self.mem[0xFFFE] | (self.mem[0xFFFF] << 8)
        if self.emit and (0xFFFE in self.mutable or 0xFFFF in self.mutable):
            vex = self._word(self._byte(0xFFFE), self._byte(0xFFFF))
            self._fact(pc, "target", vex, target)
        return target

    def _jmpind(self, pc, ptr):
        a_lo = ptr
        a_hi = (ptr & 0xFF00) | ((ptr + 1) & 0xFF)
        target = self.mem[a_lo] | (self.mem[a_hi] << 8)
        if self.emit and (a_lo in self.mutable or a_hi in self.mutable):
            vex = self._word(self._byte(a_lo), self._byte(a_hi))
            self._fact(pc, "target", vex, target)
        return target

    def _restore_flags(self, st):
        self.sreg[8] = E.op("INT_AND", [st, E.konst(1, 1)], 1)
        for idx, sh in ((9, 1), (10, 2), (11, 3), (13, 6), (14, 7)):
            self.sreg[idx] = E.op(
                "INT_AND", [E.op("INT_RIGHT", [st, E.konst(sh, 1)], 1), E.konst(1, 1)], 1
            )

    def _status_expr(self, brk):
        s = self.sreg
        parts = [
            s[8],
            E.op("INT_LEFT", [s[9], E.konst(1, 1)], 1),
            E.op("INT_LEFT", [s[10], E.konst(2, 1)], 1),
            E.op("INT_LEFT", [s[11], E.konst(3, 1)], 1),
            E.konst(0x20 | (brk << 4), 1),
            E.op("INT_LEFT", [s[13], E.konst(6, 1)], 1),
            E.op("INT_LEFT", [s[14], E.konst(7, 1)], 1),
        ]
        node = parts[0]
        for p in parts[1:]:
            node = E.op("INT_OR", [node, p], 1)
        return node

    # ---- finalise + assertion ----------------------------------------------
    def _finalize(self):
        for addr in self.written:
            self.F[addr] = (self.cell_expr[addr], 1)
        if self.assertion:
            self._check()

    def _check(self):
        image = bytearray(self.entry_mem)
        for ev in self.events:
            if ev[0] == "store":
                _, _pos, addr, evolved, sz, cval = ev
                v = E.evaluate(evolved, self.entry_mem, self.entry_reg, image, self.uni_vals)
                assert v == cval, ("store", hex(addr), v, cval, evolved)
                for k in range(sz):
                    image[(addr + k) & 0xFFFF] = (v >> (8 * k)) & 0xFF
            else:
                _, _pos, site, kind, _entry, evolved, obs = ev
                v = E.evaluate(evolved, self.entry_mem, self.entry_reg, image, self.uni_vals)
                assert v == obs, ("fact", hex(site), kind, v, obs, evolved)
        for addr, (fe, _sz) in self.F.items():
            v = E.evaluate(fe, self.entry_mem, self.entry_reg, image, self.uni_vals)
            assert v == self.mem[addr], ("F", hex(addr), v, self.mem[addr])

    def public_facts(self):
        return [(e[2], e[3], e[5], e[6]) for e in self.events if e[0] == "fact"]

    def public_slog(self):
        return [(e[1], e[2], e[3], e[4]) for e in self.events if e[0] == "store"]


def record(vm_or_mem, driver, entry, outputs, invocations, lifter=lift, assertion=True):
    """Record ``invocations`` runs of ``driver`` from ``entry`` into artifacts.

    ``driver(vm, entry, cache, lifter)`` is any VM driver; ``outputs`` is the
    observable address set. A concrete pre-pass fixes the exact mutable-cell set
    before the recording pass residualises against it.
    """
    E.clear_simplify_cache()
    E.clear_form_caches()
    if isinstance(vm_or_mem, (bytes, bytearray)):
        init_mem, init_reg = bytes(vm_or_mem), None
    else:
        init_mem, init_reg = bytes(vm_or_mem.mem), list(vm_or_mem.reg)
    outset = set(outputs)

    pre = RecVM(init_mem)
    if init_reg is not None:
        pre.reg = list(init_reg)
    pre.outputs, pre.collect, pre.emit, pre.assertion = outset, True, False, False
    mutable = set()
    sigs = []
    cache = {}
    for _ in range(invocations):
        pre.reset_invocation()
        driver(pre, entry, cache, lifter)
        mutable |= pre.written
        sigs.append(pre.sig)

    vm = RecVM(init_mem)
    if init_reg is not None:
        vm.reg = list(init_reg)
    vm.outputs, vm.mutable, vm.collect, vm.assertion = outset, mutable, False, assertion
    vm.alias_sites = set(pre.alias_sites)
    res = Recording(outset)
    cache = {}
    tmpl = {}
    for idx in range(invocations):
        cached = tmpl.get(sigs[idx])
        vm.emit = cached is None
        vm.reset_invocation()
        driver(vm, entry, cache, lifter)
        if cached is None:
            vm._finalize()
            cached = (dict(vm.F), vm.public_facts(), vm.public_slog(), list(vm.out_seq))
            tmpl[sigs[idx]] = cached
        res.F.append(cached[0])
        res.facts.append(cached[1])
        res.slog.append(cached[2])
        res.out_seq.append(cached[3])
        res.entry.append((vm.entry_mem, tuple(vm.entry_reg)))
        res._uni.append(dict(vm.uni_vals))
    return res
