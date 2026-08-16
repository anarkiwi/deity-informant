"""Dynamic-trace one SID tune on deity's PcodeVM and summarise what a
tuneprog decompiler front end must cope with (design survey instrument).

Per tune: header/cadence, executed sites and their instruction-byte variants
(SMC), read/write footprints, index-register domains of indexed sites, volatile
IO reads, stack discipline, SID write sites, per-call cost, state-period
detection, and the executed opcode sequence (engine signature).
"""

from __future__ import annotations

import hashlib
import struct
from collections import Counter, defaultdict

from deity_informant import PcodeVM, lift, c64
from deity_informant.lifter import OPS, MODE_LEN, ILLEGAL_OPCODES

PAL_FRAME = 19656
IDX_MODES = {"absx": 1, "zpx": 1, "indx": 1, "absy": 2, "zpy": 2, "indy": 2}
IO_LO, IO_HI = 0xD000, 0xDFFF
CAP = 24  # per-site value/address set cap
MAX_INSN_PER_CALL = 400_000
VOLATILE_KEYS = (
    ("D011", 0xD011, 0xD011),
    ("D012", 0xD012, 0xD012),
    ("D019", 0xD019, 0xD019),
    ("D41B", 0xD41B, 0xD41B),
    ("D41C", 0xD41C, 0xD41C),
    ("D4xx", 0xD400, 0xD41A),
    ("CIA1", 0xDC00, 0xDC0F),
    ("CIA2", 0xDD00, 0xDD0F),
    ("VICother", 0xD000, 0xD3FF),
)


class Tracer(PcodeVM):
    """PcodeVM that attributes every read/write/exec to the current pc."""

    def __init__(self, mem):
        super().__init__(mem)
        self.cur = -1
        self.phase = "init"
        self.count = Counter()
        self.first_bytes = {}
        self.variants = defaultdict(set)
        self.reads = defaultdict(set)
        self.writes = defaultdict(set)
        self.rd_over = Counter()
        self.wr_over = Counter()
        self.idx = defaultdict(set)
        self.jsr_marks = set()
        self.unbalanced_rts = 0
        self.jsr_depth = 0
        self.max_depth = 0
        self.insns = 0
        self.wlog = []
        self.written_ram = set()
        self.exec_phase = {}
        self.idle_pc = None

    def _rd(self, addr, sz):
        s = self.reads[self.cur]
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if len(s) < CAP:
                s.add(a)
            else:
                self.rd_over[self.cur] += 1
        return super()._rd(addr, sz)

    def _wr(self, addr, val, sz):
        s = self.writes[self.cur]
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if len(s) < CAP:
                s.add(a)
            else:
                self.wr_over[self.cur] += 1
            if a < 0xD000 or a > 0xDFFF:
                self.written_ram.add(a)
            if 0x100 <= a <= 0x1FF:
                self.jsr_marks.discard(a)
        super()._wr(addr, val, sz)

    def step(self, pc, cache, lifter):
        mem = self.mem
        self.cur = pc
        b = (mem[pc], mem[(pc + 1) & 0xFFFF], mem[(pc + 2) & 0xFFFF])
        op = b[0]
        mn, mode = OPS[op]
        n = MODE_LEN[mode]
        bb = b[:n]
        fb = self.first_bytes.get(pc)
        if fb is None:
            self.first_bytes[pc] = bb
            self.exec_phase[pc] = self.phase
        elif fb != bb:
            self.variants[pc].add(bb)
        self.count[pc] += 1
        self.insns += 1
        r = IDX_MODES.get(mode)
        if r is not None:
            s = self.idx[pc]
            if len(s) < CAP:
                s.add(self.reg[r])
        if mn == "JSR":
            self.jsr_depth += 1
            self.max_depth = max(self.max_depth, self.jsr_depth)
            self.jsr_marks.add(0x100 + ((self.reg[3] - 1) & 0xFF))
        elif mn == "RTS":
            lo = 0x100 + ((self.reg[3] + 1) & 0xFF)
            if lo in self.jsr_marks:
                self.jsr_marks.discard(lo)
                self.jsr_depth -= 1
            else:
                self.unbalanced_rts += 1
        return super().step(pc, cache, lifter)


def _run(vm, pc, cache, budget, init=False):
    """Run a subroutine (dummy-return convention) with an instruction budget.

    With ``init`` a ``JMP *`` idle loop (an IRQ-installing init that never
    returns) ends the run normally.
    """
    reg = vm.reg
    start = reg[3]
    vm._push(0x00)
    vm._push(0x01)
    vm.jsr_marks.add(0x100 + ((reg[3] + 1) & 0xFF))
    n0 = vm.insns
    mem = vm.mem
    while reg[3] < start:
        if (
            init
            and mem[pc] == 0x4C
            and mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8) == pc
        ):
            vm.idle_pc = pc
            return
        pc = vm.step(pc, cache, lift)
        if vm.insns - n0 > budget:
            raise RuntimeError("runaway")


def _run_irq(vm, handler, cache, budget):
    reg = vm.reg
    start = reg[3]
    vm._push(0x00)
    vm._push(0x00)
    vm._push_status()
    vm.jsr_marks.add(0x100 + ((reg[3] + 2) & 0xFF))
    reg[10] = 1
    pc = handler
    n0 = vm.insns
    while reg[3] < start:
        pc = vm.step(pc, cache, lift)
        if vm.insns - n0 > budget:
            raise RuntimeError("runaway")


def _header(data):
    magic = data[:4].decode("ascii", "replace")
    version, off, load, init, play, songs, start, speed = struct.unpack(">HHHHHHHI", data[4:22])
    flags = struct.unpack(">H", data[118:120])[0] if version >= 2 else 0
    return {
        "magic": magic,
        "version": version,
        "load": load,
        "init": init,
        "play": play,
        "songs": songs,
        "start": start,
        "speed": speed,
        "flags": flags,
        "clock": ("?", "PAL", "NTSC", "PAL+NTSC")[(flags >> 2) & 3],
        "model": ("?", "6581", "8580", "6581+8580")[(flags >> 4) & 3],
        "size": len(data),
    }


def _cadence(data):
    """(cycles_per_call, source, handler, kernal) from an init trace, or defaults."""
    try:
        from pysidtracker.image import SidImage
        from pysidtracker.trace import trace_init
        from pysidtracker.cadence import playroutine_cadence
    except ImportError:  # pragma: no cover
        return PAL_FRAME, "assumed_pal", None, False, None, {}
    img = SidImage.from_bytes(data)
    trace = trace_init(img, play_calls=0)
    cad = playroutine_cadence(img)
    handler = trace.irq_vector or trace.hw_irq_vector or trace.nmi_vector
    kernal = trace.irq_vector is not None
    topo = {
        "irq_vector": trace.irq_vector,
        "hw_irq_vector": trace.hw_irq_vector,
        "nmi_vector": trace.nmi_vector,
        "cia1_latch": trace.cia1_timer_latch,
        "cia2_latch": trace.cia2_timer_latch,
        "vic_raster": trace.vic_raster,
    }
    return cad.cycles_per_call, cad.source.value, handler, kernal, cad.dynamic, topo


def trace_tune(data, subtune=None, seconds=60.0, budget=MAX_INSN_PER_CALL, max_calls=40000):
    """Trace ``seconds`` of play calls of ``data`` (a .sid file); returns a summary dict."""
    hdr = _header(data)
    mem, _load, init, play = c64.load_psid(data)
    lo, hi = c64.psid_image(data)
    song = (hdr["start"] - 1) if subtune is None else subtune
    out = {"hdr": hdr, "song": song, "img": [lo, hi]}
    try:
        cpc, source, handler, kernal, dynamic, topo = _cadence(data)
    except Exception as e:  # noqa: BLE001 - survey: record, don't crash
        cpc, source, handler, kernal, dynamic, topo = (
            PAL_FRAME,
            "cadence_error:%s" % type(e).__name__,
            None,
            False,
            None,
            {},
        )
    out["cadence"] = {"cycles_per_call": cpc, "source": source, "dynamic": dynamic}
    out["topo"] = topo
    vm = Tracer(bytes(mem))
    vm.mem[0xD418] = 0x0F
    cache = {}
    vm.reg[0] = song
    vm.reg[1] = 0
    vm.reg[2] = 0
    try:
        _run(vm, init, cache, 2_000_000, init=True)
    except Exception as e:  # noqa: BLE001
        out["error"] = "init:%s" % e
        return _finish(out, vm, 0)
    out["init_idle"] = vm.idle_pc
    calls = min(max_calls, int(seconds * 985248 / cpc))
    out["calls_planned"] = calls
    init_sites = set(vm.count)
    init_written = set(vm.written_ram)
    vm.written_ram = set()
    entry = None
    if play:
        entry = ("sub", play)
    else:
        if handler is None:
            found = c64.installed_handler(vm.mem, init_written, (lo, hi))
            if found:
                handler, kernal = found
        if handler is None:
            out["error"] = "no play entry"
            return _finish(out, vm, 0)
        if kernal:
            try:
                c64.install_kernal_irq_stubs(vm)
            except Exception:  # noqa: BLE001
                pass
        entry = ("irq", handler)
    out["entry"] = {"kind": entry[0], "addr": entry[1]}
    vm.phase = "play"
    per_call = []
    wl_prev = 0
    hashes = {}
    period = None
    first_repeat = None
    done = 0
    for k in range(calls):
        n0, c0 = vm.insns, vm.cycles
        try:
            if entry[0] == "sub":
                _run(vm, entry[1], cache, budget)
            else:
                _run_irq(vm, entry[1], cache, budget)
        except Exception as e:  # noqa: BLE001
            out["error"] = "play@%d:%s" % (k, e)
            break
        done = k + 1
        per_call.append((vm.insns - n0, vm.cycles - c0, len(vm.wlog) - wl_prev))
        wl_prev = len(vm.wlog)
        # advance the clock to the next call as a real IRQ cadence would
        if vm.cycles - c0 < cpc:
            vm.cycles = c0 + cpc
        if period is None:
            fp = sorted(vm.written_ram)
            h = hashlib.blake2b(bytes(vm.mem[a] for a in fp), digest_size=8).digest()
            key = (len(fp), h)
            prev = hashes.get(key)
            if prev is not None:
                period = k - prev
                first_repeat = k
            else:
                hashes[key] = k
    out["calls"] = done
    out["init_sites"] = len(init_sites)
    out["init_written"] = len(init_written)
    out["init_image_writes"] = sum(1 for a in init_written if lo <= a < hi)
    out["period"] = period
    out["first_repeat"] = first_repeat
    out["per_call"] = _stats(per_call)
    return _finish(out, vm, done)


def _stats(per_call):
    if not per_call:
        return {}
    ins = [p[0] for p in per_call]
    cyc = [p[1] for p in per_call]
    wr = [p[2] for p in per_call]
    return {
        "insn_mean": sum(ins) / len(ins),
        "insn_max": max(ins),
        "cyc_mean": sum(cyc) / len(cyc),
        "cyc_max": max(cyc),
        "sidw_mean": sum(wr) / len(wr),
        "sidw_max": max(wr),
    }


def _finish(out, vm, done):
    play_sites = sorted(pc for pc, ph in vm.exec_phase.items() if ph == "play")
    all_sites = sorted(vm.exec_phase)
    ops = {pc: OPS[vm.first_bytes[pc][0]] for pc in all_sites}
    exec_bytes = set()
    for pc in all_sites:
        for i in range(MODE_LEN[ops[pc][1]]):
            exec_bytes.add((pc + i) & 0xFFFF)
    # SMC: variants (bytes differed between executions) and writers into executed bytes
    opcode_cells = [
        pc for pc, vs in vm.variants.items() if any(v[0] != vm.first_bytes[pc][0] for v in vs)
    ]
    operand_cells = [pc for pc, vs in vm.variants.items() if pc not in opcode_cells]
    writer_sites = {}
    for pc, ws in vm.writes.items():
        hit = ws & exec_bytes
        if hit:
            writer_sites[pc] = len(hit)
    play_writer_sites = [pc for pc in writer_sites if vm.exec_phase.get(pc) == "play"]
    # volatile reads by play-phase sites
    vol = Counter()
    vol_sites = Counter()
    for pc in play_sites:
        rs = vm.reads.get(pc, ())
        for name, a0, a1 in VOLATILE_KEYS:
            n = sum(1 for a in rs if a0 <= a <= a1)
            if n:
                vol[name] += n
                vol_sites[name] += 1
    modes = Counter(ops[pc][1] for pc in play_sites)
    mnem = Counter(ops[pc][0] for pc in play_sites)
    illegal = sorted(
        {vm.first_bytes[pc][0] for pc in play_sites if vm.first_bytes[pc][0] in ILLEGAL_OPCODES}
    )
    sid_sites = [
        pc for pc in play_sites if any(0xD400 <= a <= 0xD7FF for a in vm.writes.get(pc, ()))
    ]
    sid_regs = sorted(
        {(a - 0xD400) & 0x1F for pc in sid_sites for a in vm.writes[pc] if 0xD400 <= a <= 0xD7FF}
    )
    lo, hi = out.get("img", (0, 0))
    written_any = set(vm.written_ram)
    for pc, ws in vm.writes.items():
        written_any |= ws
    bank = sorted({v for pc in vm.writes if 1 in vm.writes[pc] for v in (vm.mem[1],)})
    bank_sites = {"init": 0, "play": 0}
    for pc, ws in vm.writes.items():
        if 1 in ws:
            bank_sites[vm.exec_phase.get(pc, "init")] += 1
    uninit = 0
    io_writes = Counter()
    for pc in play_sites:
        for a in vm.reads.get(pc, ()):
            if a < 0xD000 and not (lo <= a < hi) and a not in written_any and a > 0x1FF:
                uninit += 1
        for a in vm.writes.get(pc, ()):
            if 0xD000 <= a <= 0xD3FF:
                io_writes["VIC"] += 1
            elif 0xDC00 <= a <= 0xDDFF:
                io_writes["CIA"] += 1
    out["bank01"] = {"values": bank, "sites": bank_sites}
    out["uninit_reads"] = uninit
    out["io_writes"] = dict(io_writes)
    # index domains for indexed sites in play
    idx = {}
    for pc in play_sites:
        if pc in vm.idx:
            idx["%04X" % pc] = [ops[pc][1], sorted(vm.idx[pc])[:CAP]]
    # executed opcode sequence in pc order (engine signature)
    opseq = bytes(vm.first_bytes[pc][0] for pc in play_sites)
    out.update(
        {
            "sites": len(play_sites),
            "sites_all": len(all_sites),
            "code_bytes": len(exec_bytes),
            "code_range": [play_sites[0], play_sites[-1]] if play_sites else None,
            "insns": vm.insns,
            "modes": dict(modes),
            "mnem_top": dict(mnem.most_common(12)),
            "illegal": illegal,
            "smc": {
                "opcode_cells": len(opcode_cells),
                "operand_cells": len(operand_cells),
                "writer_sites": len(writer_sites),
                "play_writer_sites": len(play_writer_sites),
                "cells": sum(writer_sites.values()),
            },
            "volatile": dict(vol),
            "volatile_sites": dict(vol_sites),
            "stack": {"max_jsr_depth": vm.max_depth, "unbalanced_rts": vm.unbalanced_rts},
            "sid_sites": len(sid_sites),
            "sid_regs": sid_regs,
            "sid_writes": len(vm.wlog),
            "footprint": len(vm.written_ram),
            "idx": idx,
            "opseq": opseq.hex(),
            "opseq_sha": hashlib.sha1(opseq).hexdigest()[:12],
        }
    )
    return out
