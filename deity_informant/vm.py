"""Pure-Python raw-P-Code interpreter (no py65) plus control-flow drivers.

``PcodeVM`` executes the op lists produced by :func:`deity_informant.lifter.lift`
against a flat 64 KiB memory model, capturing ``$D400..`` SID writes. The
``run_sub`` / ``run_irq`` / ``run_irq_driven`` helpers drive playroutine init,
frame play, and interrupt-driven cadence exactly as a real 6510 would.
"""

from __future__ import annotations

# ---- P-Code -> Python source (per-op line; exec'd into a closure) ------------
_BINOP = {
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_AND": "&",
    "INT_OR": "|",
    "INT_XOR": "^",
    "INT_LEFT": "<<",
}
_CMPOP = {"INT_EQUAL": "==", "INT_NOTEQUAL": "!=", "INT_LESS": "<", "INT_LESSEQUAL": "<="}


def _rd_expr(vn):
    sp, off, _sz = vn
    if sp == "c":
        return str(off)
    return ("r[%d]" if sp == "r" else "u[%d]") % off


def _lhs(vn):
    sp, off, _sz = vn
    return ("r[%d]" if sp == "r" else "u[%d]") % off


def _emit_line(mn, out, ins):
    if mn == "STORE":
        return "wr(%s, %s, %d)" % (_rd_expr(ins[0]), _rd_expr(ins[1]), ins[1][2])
    if mn == "LOAD":
        return "%s = rd(%s, %d)" % (_lhs(out), _rd_expr(ins[0]), out[2])
    lhs = _lhs(out)
    if mn in ("COPY", "INT_ZEXT"):
        return "%s = %s" % (lhs, _rd_expr(ins[0]))
    if mn == "INT_RIGHT":
        return "%s = %s >> %s" % (lhs, _rd_expr(ins[0]), _rd_expr(ins[1]))
    if mn in _BINOP:
        mask = (1 << (8 * out[2])) - 1
        return "%s = (%s %s %s) & %d" % (lhs, _rd_expr(ins[0]), _BINOP[mn], _rd_expr(ins[1]), mask)
    if mn in _CMPOP:
        return "%s = 1 if %s %s %s else 0" % (lhs, _rd_expr(ins[0]), _CMPOP[mn], _rd_expr(ins[1]))
    if mn == "INT_CARRY":
        mask0 = (1 << (8 * ins[0][2])) - 1
        return "%s = 1 if (%s + %s) > %d else 0" % (lhs, _rd_expr(ins[0]), _rd_expr(ins[1]), mask0)
    raise NotImplementedError(mn)


# ---- pure-Python P-Code interpreter (no py65) --------------------------------
class PcodeVM:
    __slots__ = ("mem", "reg", "uniq", "cycles", "volatile", "vicirq", "ciaicr", "wlog")

    def __init__(self, mem_bytes):
        self.mem = bytearray(mem_bytes)
        self.reg = [0] * 16
        self.reg[3] = 0xFF
        self.uniq = {}
        self.cycles = 0
        self.volatile = True  # apply the SID-replay volatile-read model
        # Interrupt-source flags an IRQ handler polls to find who fired: $D019
        # (VIC, write-acked) and $DC0D (CIA, read-cleared). run_irq_driven raises
        # them. wlog, when a list, records (cycle, reg, val) SID writes.
        self.vicirq = 0
        self.ciaicr = 0
        self.wlog = None

    def _rd(self, addr, sz):
        mem = self.mem
        if sz == 1:
            if self.volatile and addr == 0xD019:
                return self.vicirq
            if self.volatile and addr == 0xDC0D:
                v = self.ciaicr
                self.ciaicr = 0
                return v
            if not (self.volatile and 0xD011 <= addr <= 0xD41C):
                return mem[addr]
        val = 0
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if self.volatile and a == 0xD012:
                b = (self.cycles // 63) % 312 & 0xFF
            elif self.volatile and a == 0xD011:
                b = (mem[0xD011] & 0x7F) | ((((self.cycles // 63) % 312 >> 8) & 1) << 7)
            elif self.volatile and (a == 0xD41B or a == 0xD41C):
                b = (self.cycles >> 3) & 0xFF
            else:
                b = mem[a]
            val |= b << (8 * i)
        return val

    def _wr(self, addr, val, sz):
        mem = self.mem
        if self.volatile and addr == 0xD019:  # writing 1s to $D019 acks VIC IRQ
            self.vicirq &= ~val & 0x7F
        if self.wlog is not None and 0xD400 <= addr <= 0xD418:
            self.wlog.append((self.cycles, addr - 0xD400, val & 0xFF))
        for i in range(sz):
            mem[(addr + i) & 0xFFFF] = (val >> (8 * i)) & 0xFF

    def compile_record(self, rec):
        f = rec.get("_f")
        if f is not None:
            return f
        lines = [_emit_line(mn, out, ins) for mn, out, ins in rec["ops"]]
        src = "def _f(r,u,rd,wr):\n    " + ("\n    ".join(lines) or "pass") + "\n"
        ns = {}
        exec(src, ns)  # noqa: S102 - generated straight-line P-Code, no user input
        f = ns["_f"]
        rec["_f"] = f
        return f

    def run_record(self, rec, pc):
        (rec.get("_f") or self.compile_record(rec))(self.reg, self.uniq, self._rd, self._wr)
        cyc = rec["cyc"]
        ctrl = rec["ctrl"]
        nxt = None
        if ctrl[0] == "br":
            _k, flag, pol, tgt, ft = ctrl
            if self.reg[flag[1]] == pol:
                cyc += 1
                if (ft & 0xFF00) != (tgt & 0xFF00):
                    cyc += 1
                nxt = tgt
            else:
                nxt = ft
        else:
            pen = rec["pen"]
            if pen is not None:
                k = pen[0]
                base = pen[1]
                if k == "iy":
                    base = self.mem[base] | (self.mem[(base + 1) & 0xFF] << 8)
                    idx = self.reg[2]
                elif k == "ax":
                    idx = self.reg[1]
                else:  # ay
                    idx = self.reg[2]
                if k != "branch" and (base & 0xFF00) != ((base + idx) & 0xFF00):
                    cyc += 1
        self.cycles += cyc
        return ctrl, nxt

    # ---- one 6502 instruction: run its P-Code and resolve the next PC --------
    def step(self, pc, cache, lifter):
        mem = self.mem
        k = (pc, mem[pc], mem[(pc + 1) & 0xFFFF], mem[(pc + 2) & 0xFFFF])
        rec = cache.get(k)
        if rec is None:
            if lifter is None:
                raise KeyError("cache miss at %04X (SMC needs a lifter)" % pc)
            rec = lifter(mem, pc)
            cache[k] = rec
        ctrl, nxt = self.run_record(rec, pc)
        t = ctrl[0]
        reg = self.reg
        if t == "next":
            return (pc + rec["len"]) & 0xFFFF
        if t == "br":
            return nxt
        if t == "jmp":
            return ctrl[1]
        if t == "jmpind":
            ptr = ctrl[1]
            lo = mem[ptr]
            hi = mem[(ptr & 0xFF00) | ((ptr + 1) & 0xFF)]
            return lo | (hi << 8)
        if t == "jsr":
            ret = (pc + rec["len"] - 1) & 0xFFFF
            mem[0x100 + reg[3]] = ret >> 8
            reg[3] = (reg[3] - 1) & 0xFF
            mem[0x100 + reg[3]] = ret & 0xFF
            reg[3] = (reg[3] - 1) & 0xFF
            return ctrl[1]
        if t == "rts":
            reg[3] = (reg[3] + 1) & 0xFF
            lo = mem[0x100 + reg[3]]
            reg[3] = (reg[3] + 1) & 0xFF
            hi = mem[0x100 + reg[3]]
            return ((hi << 8) | lo) + 1 & 0xFFFF
        if t == "rti":
            reg[3] = (reg[3] + 1) & 0xFF
            self._set_flags(mem[0x100 + reg[3]])
            reg[3] = (reg[3] + 1) & 0xFF
            lo = mem[0x100 + reg[3]]
            reg[3] = (reg[3] + 1) & 0xFF
            hi = mem[0x100 + reg[3]]
            return (hi << 8) | lo
        if t == "brk":
            ret = (pc + 2) & 0xFFFF
            mem[0x100 + reg[3]] = ret >> 8
            reg[3] = (reg[3] - 1) & 0xFF
            mem[0x100 + reg[3]] = ret & 0xFF
            reg[3] = (reg[3] - 1) & 0xFF
            mem[0x100 + reg[3]] = self._status(brk=1)
            reg[3] = (reg[3] - 1) & 0xFF
            reg[10] = 1
            return mem[0xFFFE] | (mem[0xFFFF] << 8)
        raise RuntimeError("JAM at %04X" % pc)

    def _status(self, brk=0):
        r = self.reg
        return (
            r[8]
            | (r[9] << 1)
            | (r[10] << 2)
            | (r[11] << 3)
            | 0x20
            | ((brk or 0) << 4)
            | (r[13] << 6)
            | (r[14] << 7)
        )

    def _set_flags(self, p):
        r = self.reg
        r[8] = p & 1
        r[9] = (p >> 1) & 1
        r[10] = (p >> 2) & 1
        r[11] = (p >> 3) & 1
        r[13] = (p >> 6) & 1
        r[14] = (p >> 7) & 1


# ---- control-flow drivers ----------------------------------------------------
_GUARD = 8_000_000


def run_sub(vm, pc, cache, lifter):
    """Run a subroutine to its balancing RTS (dummy-return convention)."""
    reg = vm.reg
    start = reg[3]
    vm.mem[0x100 + reg[3]] = 0x00
    reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = 0x01
    reg[3] = (reg[3] - 1) & 0xFF
    n = 0
    while reg[3] < start:
        pc = vm.step(pc, cache, lifter)
        n += 1
        if n > _GUARD:
            raise RuntimeError("runaway subroutine at %04X" % pc)
    return pc


def run_irq(vm, handler, cache, lifter):
    """Enter ``handler`` like a hardware IRQ; run until its RTI unwinds the frame.

    Pushes the CPU interrupt frame (return address then status), sets I, and runs
    from ``handler`` until the balancing RTI climbs the stack back.
    """
    reg = vm.reg
    start = reg[3]
    vm.mem[0x100 + reg[3]] = 0x00  # sentinel return hi
    reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = 0x00  # sentinel return lo
    reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = vm._status()  # pushed status
    reg[3] = (reg[3] - 1) & 0xFF
    reg[10] = 1  # I set on IRQ entry
    pc = handler
    n = 0
    while reg[3] < start:
        pc = vm.step(pc, cache, lifter)
        n += 1
        if n > _GUARD:
            raise RuntimeError("runaway IRQ at %04X" % pc)
    return pc


def _take_irq(vm, handler, ret_pc, enter):
    reg = vm.reg
    vm.mem[0x100 + reg[3]] = (ret_pc >> 8) & 0xFF
    reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = ret_pc & 0xFF
    reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = vm._status()
    reg[3] = (reg[3] - 1) & 0xFF
    reg[10] = 1  # I set on IRQ entry
    enter(vm)
    return handler


def run_irq_driven(vm, handler, total, sources, cache, lifter, idle_pc=0):
    """Interrupt-driven executor: multiple sources, hardware-faithful nesting.

    ``sources`` is a list of dicts ``{"period", "next", "enter"}``; ``enter(vm)``
    raises that source's flag ($D019/$DC0D). Between handlers the CPU idles (skip
    to the next fire). While a handler runs with I clear, a due source nests --
    modelling defMON's ``CLI`` mid-play, so a raster split delays the CIA play's
    later SID writes exactly as on hardware. Advances until ``total`` cycles.
    """
    reg = vm.reg
    while True:
        nxt = min(s["next"] for s in sources)
        if nxt >= total:
            return
        if vm.cycles < nxt:
            vm.cycles = nxt
        idle_sp = reg[3]
        due = min((s for s in sources if s["next"] <= vm.cycles), key=lambda s: s["next"])
        pc = _take_irq(vm, handler, idle_pc, due["enter"])
        due["next"] += due["period"]
        n = 0
        while reg[3] < idle_sp:
            if reg[10] == 0:
                ready = [s for s in sources if s["next"] <= vm.cycles]
                if ready:
                    d = min(ready, key=lambda s: s["next"])
                    pc = _take_irq(vm, handler, pc, d["enter"])
                    d["next"] += d["period"]
                    continue
            pc = vm.step(pc, cache, lifter)
            n += 1
            if n > _GUARD:
                raise RuntimeError("runaway IRQ-driven at %04X" % pc)
