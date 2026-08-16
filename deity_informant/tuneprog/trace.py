"""S1 -- op-level tracer: sites, edges, procedures' raw material, logs, inputs.

:class:`TraceVM` is a :class:`~deity_informant.vm.PcodeVM` whose generated
straight-line P-Code calls ``rd(a, sz, i)`` / ``wr(a, v, sz, i)`` with the index
``i`` of the P-Code op making the access, so a ``(zp),Y`` pointer fetch and the
stream load it feeds are attributed separately (the exact access relation S3
needs). The base VM is untouched.

Public API:

* ``run_trace(image, entry, calls, ...) -> Trace`` -- init + ``calls`` ticks,
  optionally chunked/resumable through ``resume=path``.
* ``Tracer(image, entry, ...)`` -- the same as an object (``run_init``,
  ``run_calls``, ``trace()``, ``save``/``load``).
* ``Trace`` -- the recorded run; ``save(dir)``/``load(dir)`` as ``trace.json``
  (structure) + ``trace.npz`` (bulk arrays).
* ``site_key(pc, opcode, bytes, cells)`` -- ``(pc, opcode, fixed operand bytes)``
  with play-written SMC cells excluded (``None`` placeholders).

Sites are keyed by :func:`site_key`; ``reads``/``writes`` are keyed by
``(pc, opcode)`` because the P-Code op list's shape depends only on the opcode.
"""

from __future__ import annotations

import pickle
from array import array
from collections import Counter, defaultdict
from hashlib import blake2b
from pathlib import Path

import numpy as np

from ..lifter import OPS, MODE_LEN, STATUS_BITS, lift
from ..vm import PcodeVM, _emit_line, _rd_expr, _lhs
from .. import c64
from .machine import CIA, CIA1_BASE, CIA2_BASE, Refusal, init_runner, port_bank
from .tracedata import Trace

PH_INIT, PH_PLAY = 1, 2
IDX_REG = {"absx": 1, "zpx": 1, "indx": 1, "absy": 2, "zpy": 2, "indy": 2}
SID_LO, SID_HI = 0xD400, 0xD7FF
IO_LO, IO_HI = 0xD000, 0xDFFF
ACKS = (0xD019, 0xDC0D, 0xDD0D)
REG_IN = 0x10000  # synthetic input addresses for live-in A/X/Y
CALL_BUDGET = 400_000
MAX_CELL_VALUES = 16
_KIND = {"jmp": "jmp", "jmpind": "jmpind", "jsr": "jsr", "brk": "brk"}
_FLAGS = sum(1 << i for i, _b in STATUS_BITS)


def input_kind(addr):
    """Input class of ``addr`` (design section 4 ``Input.kind``)."""
    if addr >= REG_IN:
        return "entry_reg"
    if addr in ACKS:
        return "ack"
    if addr == 0xD011 or addr == 0xD012:
        return "raster"
    if SID_LO <= addr <= SID_HI:
        return "sid_readback"
    if 0xDC00 <= addr <= 0xDDFF:
        return "cia"
    if IO_LO <= addr <= IO_HI:
        return "io"
    return "uninit_ram"


def site_key(pc, opcode, insn_bytes, cells):
    """``(pc, opcode, fixed operand bytes)``; operand bytes in ``cells`` drop out."""
    fixed = tuple(
        None if (pc + k) & 0xFFFF in cells else insn_bytes[k] for k in range(1, len(insn_bytes))
    )
    return pc, opcode, fixed


def _emit_attr(mn, out, ins, i):
    """One P-Code op as Python, with the op index threaded into rd/wr."""
    if mn == "STORE":
        return "wr(%s, %s, %d, %d)" % (_rd_expr(ins[0]), _rd_expr(ins[1]), ins[1][2], i)
    if mn == "LOAD":
        return "%s = rd(%s, %d, %d)" % (_lhs(out), _rd_expr(ins[0]), out[2], i)
    return _emit_line(mn, out, ins)


def _reg_masks(rec):
    """``(read-before-write, written)`` register-file bitmasks of one instruction."""
    rd = wr = 0
    for _mn, out, ins in rec["ops"]:
        for vn in ins:
            if vn[0] == "r":
                b = 1 << vn[1]
                if not wr & b:
                    rd |= b
        if out is not None and out[0] == "r":
            wr |= 1 << out[1]
    stk = rec["stk"]
    if stk is not None:
        wr |= 1 << 3
        if stk in ("rts", "rti"):
            rd |= 1 << 3
        if stk == "rti":  # step() pops the status byte into the flag registers
            wr |= _FLAGS
        elif stk == "brk":  # step() pushes the status byte and sets I
            rd |= _FLAGS & ~wr
            wr |= _FLAGS | (1 << 10)
    return rd, wr


class TraceVM(PcodeVM):
    """PcodeVM with per-P-Code-op access attribution, sites, edges and logs."""

    def __init__(self, mem, image, policy="record", inputs=None, override=None):
        super().__init__(mem)
        self.image = image
        self.policy = policy
        self.override = override or {}
        self.replay = iter(inputs or ())
        self.phase = PH_INIT
        self.call = -1
        self.bank = port_bank(mem)
        self.cia = (CIA(CIA1_BASE), CIA(CIA2_BASE))
        self.known = bytearray(0x10000)
        self.known[image.lo : image.hi] = b"\1" * (image.hi - image.lo)
        self.known[0x100:0x200] = b"\1" * 0x100
        self.code = bytearray(0x10000)
        self.inband = bytearray(0x10000)
        self.inband[image.lo : image.hi] = b"\1" * (image.hi - image.lo)
        self.count = Counter()
        self.sitephase = {}
        self.reads = defaultdict(dict)
        self.writes = defaultdict(dict)
        self.idx = defaultdict(set)
        self.edges = {}
        self.calls = {}
        self.rets = {}
        self.summaries = {}
        self.shadow = []
        self.unmatched_rts = 0
        self.max_depth = 0
        self.insns = 0
        self.written_init = set()
        self.written_play = set()
        self.wr_values = defaultdict(set)
        self.init_writes = []
        self.inputs = []
        self.input_sites = {}
        # column arrays (call, addr, val, cycle); the base VM's own wlog hook is
        # unused because _wr is fully overridden.
        self.sidlog = tuple(array(t) for t in "IHBI")
        self.iolog = tuple(array(t) for t in "IHBI")
        self.tick_rd = self.tick_wr = 0
        self._rs = {}
        self._ws = {}
        self._pc = self._op = 0
        self._r0 = (0, 0, 0)

    # ---- per-op attributed memory ------------------------------------------
    def compile_record(self, rec):
        f = rec.get("_f")
        if f is None:
            lines = [_emit_attr(mn, out, ins, i) for i, (mn, out, ins) in enumerate(rec["ops"])]
            src = "def _f(r,u,rd,wr):\n    " + ("\n    ".join(lines) or "pass") + "\n"
            ns = {}
            exec(src, ns)  # noqa: S102 - generated straight-line P-Code
            f = rec["_f"] = ns["_f"]
        return f

    def _rd(self, addr, sz, i=0):
        s = self._rs.get(i)
        if s is None:
            s = self._rs[i] = set()
        if sz == 1:
            s.add(addr)
            return self._rd1(addr, i)
        v = 0
        for k in range(sz):
            a = (addr + k) & 0xFFFF
            s.add(a)
            v |= self._rd1(a, i) << (8 * k)
        return v

    def _rd1(self, a, i):
        if IO_LO <= a <= IO_HI:
            if self.bank != "io":
                return self.mem[a]
            v = self.override.get(a)
            if v is None:
                v = self.cia[0].read(a, self.cycles)
            if v is None:
                v = self.cia[1].read(a, self.cycles)
            if v is None:
                v = PcodeVM._rd(self, a, 1)
            kind = input_kind(a)
            return v if kind == "ack" else self._input(a, v, i, kind)
        v = self.mem[a]
        if not self.known[a]:
            v = self._input(a, v, i, "uninit_ram")
        return v

    def _input(self, addr, value, i, kind):
        site = self._pc
        rec = self.input_sites.get((site, addr))
        if rec is None:
            rec = self.input_sites[(site, addr)] = {"kind": kind, "count": 0, "phase": 0}
        rec["count"] += 1
        rec["phase"] |= self.phase
        if self.policy == "replay":
            nxt = next(self.replay, None)
            if nxt is None or nxt[3] != addr:
                raise Refusal("input replay mismatch", "at $%04X call %d" % (addr, self.call))
            return nxt[4]
        self.inputs.append((self.call, site, i, addr, value))
        return value

    def _wr(self, addr, val, sz, i=0):
        s = self._ws.get(i)
        if s is None:
            s = self._ws[i] = set()
        mem = self.mem
        for k in range(sz):
            a = (addr + k) & 0xFFFF
            b = (val >> (8 * k)) & 0xFF
            s.add(a)
            if IO_LO <= a <= IO_HI and self.bank == "io":
                self._io_write(a, b)
            else:
                self.known[a] = 1
                (self.written_init if self.phase == PH_INIT else self.written_play).add(a)
                if self.inband[a]:
                    vs = self.wr_values[a]
                    if len(vs) < MAX_CELL_VALUES:
                        vs.add(b)
            mem[a] = b
        if addr <= 1:
            self.bank = port_bank(mem)

    def _io_write(self, a, b):
        log = self.sidlog if SID_LO <= a <= SID_HI else self.iolog
        log[0].append(self.call & 0xFFFFFFFF)
        log[1].append(a)
        log[2].append(b)
        log[3].append(self.cycles & 0xFFFFFFFF)
        if self.phase == PH_INIT:
            self.init_writes.append((a, b, self.cycles))
        if a == 0xD019:
            self.vicirq &= ~b & 0x7F
        elif 0xDC00 <= a <= 0xDDFF:
            self.cia[(a >> 8) & 1].write(a, b, self.cycles)

    # ---- sites, edges, frames ----------------------------------------------
    def push_frame(self, site, ret, target):
        """Push a shadow frame (``site`` is ``None`` for a driver's dummy return)."""
        self.shadow.append([site, ret, target, 0, 0])
        self.max_depth = max(self.max_depth, len(self.shadow))

    def step(self, pc, cache, lifter):
        mem = self.mem
        b0 = mem[pc]
        key = (pc, b0, mem[(pc + 1) & 0xFFFF], mem[(pc + 2) & 0xFFFF])
        mode = OPS[b0][1]
        bb = key[1 : 1 + MODE_LEN[mode]]
        sk = (pc, bb)
        self._pc = pc
        self._op = b0
        n = self.count[sk]
        self.count[sk] = n + 1
        if not n:  # first execution of these exact bytes: they are instruction bytes
            for k in range(len(bb)):
                self.code[(pc + k) & 0xFFFF] = 1
        self.sitephase[sk] = self.sitephase.get(sk, 0) | self.phase
        r = IDX_REG.get(mode)
        if r is not None:
            self.idx[sk].add(self.reg[r])
        self._rs = self.reads[sk]
        self._ws = self.writes[sk]
        if self.tick_rd & 7 != 7:
            self._r0 = (self.reg[0], self.reg[1], self.reg[2])
        self.insns += 1
        nxt = super().step(pc, cache, lifter)
        rec = cache[key]
        rw = rec.get("_rw")
        if rw is None:
            rw = rec["_rw"] = _reg_masks(rec)
        self._account(pc, rec, nxt, rw)
        return nxt

    def _account(self, pc, rec, nxt, rw):
        rd, wr = rw
        live = rd & ~self.tick_wr
        if live & 7:
            for j in range(3):
                if live & (1 << j) and not self.tick_rd & (1 << j):
                    self._input(REG_IN + j, self._r0[j], 0, "entry_reg")
        self.tick_rd |= rd
        self.tick_wr |= wr
        if self.shadow:
            f = self.shadow[-1]
            f[3] |= rd & ~f[4]
            f[4] |= wr
        sk = (pc, self._op)
        kind = rec["ctrl"][0]
        if kind == "next":
            self._edge(sk, nxt, "fall")
        elif kind == "br":
            self._edge(sk, nxt, "br_taken" if nxt == rec["ctrl"][3] else "br_not")
        elif kind == "jsr":
            self._edge(sk, nxt, "jsr")
            ret = (pc + rec["len"]) & 0xFFFF
            c = self.calls.get(sk)
            if c is None:
                c = self.calls[sk] = {"targets": Counter(), "ret_pc": ret, "count": 0}
            c["targets"][nxt] += 1
            c["count"] += 1
            self.push_frame(pc, ret, nxt)
        elif kind in ("rts", "rti"):
            self._return(sk, nxt)
        else:
            self._edge(sk, nxt, _KIND[kind])

    def _edge(self, sk, t, kind):
        e = self.edges.get((sk[0], sk[1], t))
        if e is None:
            self.edges[(sk[0], sk[1], t)] = [kind, 1]
        else:
            e[1] += 1

    def _return(self, sk, nxt):
        r = self.rets.get(sk)
        if r is None:
            r = self.rets[sk] = {
                "matched": Counter(),
                "unmatched": 0,
                "targets": Counter(),
                "loose": Counter(),
            }
        r["targets"][nxt] += 1
        if self.shadow and self.shadow[-1][1] == nxt:
            site, _ret, target, frd, fwr = self.shadow.pop()
            r["matched"][site if site is not None else -1] += 1
            if self.shadow:
                p = self.shadow[-1]
                p[3] |= frd & ~p[4]
                p[4] |= fwr
            s = self.summaries.get(target)
            if s is None:
                s = self.summaries[target] = {"rd": 0, "wr": 0, "count": 0}
            s["rd"] |= frd
            s["wr"] |= fwr
            s["count"] += 1
        else:
            r["unmatched"] += 1
            r["loose"][nxt] += 1
            self.unmatched_rts += 1


class Tracer:
    """Drives init and ``n`` ticks of one entry under :class:`TraceVM`."""

    def __init__(self, image, entry, song=None, policy="record", inputs=None, override=None):
        self.image = image
        self.entry = entry
        self.song = image.startsong - 1 if song is None else song
        self.vm = TraceVM(image.mem, image, policy=policy, inputs=inputs, override=override)
        self.cache = {}
        self.image_post_init = None
        self.calls_done = 0
        self.hashes = {}
        self.period = None
        self.first_repeat = None
        self.state_hash = array("Q")
        self.footprint = array("I")
        self._fp = ()

    def run_init(self, budget=None):
        vm = self.vm
        vm.reg[0], vm.reg[1], vm.reg[2] = self.song, 0, 0
        vm.push_frame(None, 0x0002, self.image.init)
        kw = {} if budget is None else {"budget": budget}
        init_runner(vm, self.image.init, self.cache, lift, **kw)
        vm.shadow.clear()
        vm.phase = PH_PLAY
        if self.entry.kind == "irq" and not any(
            self.image.lo <= a < self.image.hi for a in (0xEA31, 0xEA81, 0xFEBC)
        ):
            c64.install_kernal_irq_stubs(vm)
        self.image_post_init = bytes(vm.mem)
        return self

    def run_calls(self, n, budget=CALL_BUDGET):
        for _ in range(n):
            self._one_call(budget)
        return self

    def _one_call(self, budget):
        vm = self.vm
        reg = vm.reg
        vm.call = self.calls_done
        vm.tick_rd = vm.tick_wr = 0
        start = reg[3]
        c0 = vm.cycles
        vm._push(0x00)
        if self.entry.kind == "sub":
            vm._push(0x01)
            vm.push_frame(None, 0x0002, self.entry.addr)
        else:
            vm._push(0x00)
            vm._push_status()
            vm.push_frame(None, 0x0000, self.entry.addr)
            reg[10] = 1
            if "video" in self.entry.source:
                vm.vicirq = 0x81  # a raster IRQ has fired: handlers poll $D019
        pc = self.entry.addr
        n = 0
        while reg[3] < start:
            pc = vm.step(pc, self.cache, lift)
            n += 1
            if n > budget:
                raise Refusal("play runaway", "call %d at $%04X" % (self.calls_done, pc))
        vm.shadow.clear()
        if vm.cycles - c0 < self.entry.cycles_per_tick:
            vm.cycles = c0 + self.entry.cycles_per_tick
        self._hash()
        self.calls_done += 1

    def _hash(self):
        vm = self.vm
        if len(self._fp) != len(vm.written_play):
            self._fp = tuple(sorted(vm.written_play))
        mem = vm.mem
        n = len(self._fp)
        h = blake2b(
            bytes(map(mem.__getitem__, self._fp)), digest_size=8, key=n.to_bytes(4, "little")
        ).digest()
        v = int.from_bytes(h, "little")
        self.state_hash.append(v)
        self.footprint.append(n)
        if self.period is None:
            ninp = len(vm.inputs)
            prev = self.hashes.get((n, v))
            if prev is None:
                self.hashes[(n, v)] = (self.calls_done, ninp)
            elif prev[1] == ninp:
                # design S1: a repeat is a witness only with no inputs consumed
                # between the two calls (otherwise the next tick may differ).
                self.period = self.calls_done - prev[0]
                self.first_repeat = self.calls_done

    # ---- resume ------------------------------------------------------------
    def save(self, path):
        Path(path).write_bytes(pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL))
        return path

    @staticmethod
    def load(path):
        return pickle.loads(Path(path).read_bytes())

    def __getstate__(self):
        d = dict(self.__dict__)
        d["cache"] = None
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.cache = {}

    # ---- result ------------------------------------------------------------
    def trace(self):
        vm = self.vm
        cells = {a for a in vm.written_play if vm.code[a]}
        sites = {}
        for sk, count in vm.count.items():
            pc, bb = sk
            key = site_key(pc, bb[0], bb, cells)
            s = sites.get(key)
            if s is None:
                s = sites[key] = {
                    "pc": pc,
                    "opcode": bb[0],
                    "count": 0,
                    "phases": 0,
                    "variants": [],
                    "idx": set(),
                    "reads": {},
                    "writes": {},
                }
            s["count"] += count
            s["phases"] |= vm.sitephase[sk]
            s["variants"].append(bytes(bb))
            s["idx"] |= vm.idx.get(sk, set())
            for name, src in (("reads", vm.reads), ("writes", vm.writes)):
                for i, a in src.get(sk, {}).items():
                    s[name].setdefault(i, set()).update(a)
        for s in sites.values():
            s["idx"] = sorted(s["idx"])
            s["variants"].sort()
        jsr_targets = {t for c in vm.calls.values() for t in c["targets"]}
        edges = {k: list(v) for k, v in vm.edges.items()}
        for (_f, _o, t), e in edges.items():
            if t in jsr_targets and e[0] in ("fall", "br_taken", "br_not", "jmp"):
                e[0] = "tail"
        meta = {
            "entry": self.entry.to_dict(),
            "schedule": [self.entry.to_dict()],
            "song": self.song,
            "calls": self.calls_done,
            "insns": vm.insns,
            "cycles": vm.cycles,
            "period": self.period,
            "first_repeat": self.first_repeat,
            "unmatched_rts": vm.unmatched_rts,
            "max_depth": vm.max_depth,
            **self.image.meta(),
        }
        return Trace(
            meta=meta,
            image_pre=self.image.mem,
            image_post_init=self.image_post_init or self.image.mem,
            sites=sites,
            edges=edges,
            calls={k: dict(v) for k, v in vm.calls.items()},
            rets={k: dict(v) for k, v in vm.rets.items()},
            summaries=dict(vm.summaries),
            inputs=vm.inputs,
            input_sites=dict(vm.input_sites),
            init_writes=vm.init_writes,
            written_init=set(vm.written_init),
            written_play=set(vm.written_play),
            cells=cells,
            cell_values={a: set(v) for a, v in vm.wr_values.items() if a in cells},
            jsr_targets=jsr_targets,
            wlog=_arrays(vm.sidlog),
            iolog=_arrays(vm.iolog),
            state_hash=np.frombuffer(self.state_hash, dtype=np.uint64).copy(),
            footprint_size=np.frombuffer(self.footprint, dtype=np.uint32).copy(),
        )


def _arrays(cols):
    names = ("call", "addr", "val", "cyc")
    types = (np.uint32, np.uint16, np.uint8, np.uint32)
    return {n: np.frombuffer(c, dtype=t).copy() for n, c, t in zip(names, cols, types)}


def run_trace(
    image,
    entry,
    calls,
    song=None,
    policy="record",
    inputs=None,
    override=None,
    resume=None,
    budget=CALL_BUDGET,
):
    """Trace ``calls`` ticks of ``entry`` (init first); returns a :class:`Trace`.

    With ``resume=path`` the tracer state is pickled after each invocation and
    reloaded by the next, so a long run splits into chunks that each stay inside
    a CPU budget; ``calls`` is always the total number of ticks from the start.
    """
    p = Path(resume) if resume else None
    if p is not None and p.exists():
        t = Tracer.load(p)
    else:
        t = Tracer(image, entry, song=song, policy=policy, inputs=inputs, override=override)
        t.run_init()
    t.run_calls(max(0, calls - t.calls_done), budget=budget)
    if p is not None:
        t.save(p)
    return t.trace()
