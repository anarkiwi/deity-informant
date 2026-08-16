"""End-to-end front end on two HVSC exemplars (marked ``hvsc``; short horizon).

Automatas (defMON, anatomy 3.7) checks the facts of
``docs/prototype-automatas.md`` section 2 that hold at a 20 s horizon; Commando
(Hubbard, anatomy 3.1) is the genericity smoke test and also pins the tracer's
per-call SID write list to the plain :class:`~deity_informant.vm.PcodeVM`.
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("pysidtracker")

from pysidtracker.testing import resolve_tune  # noqa: E402

from deity_informant import PcodeVM, c64, lift, run_sub  # noqa: E402
from deity_informant.lifter import ILLEGAL_OPCODES  # noqa: E402
from deity_informant.tuneprog.cfg import build_procs, procs_json  # noqa: E402
from deity_informant.tuneprog.lift import lift_trace  # noqa: E402
from deity_informant.tuneprog.machine import find_entries  # noqa: E402
from deity_informant.tuneprog.regions import build_regions, index_regions  # noqa: E402
from deity_informant.tuneprog.trace import Tracer  # noqa: E402

pytestmark = pytest.mark.hvsc

_CACHE = Path(os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache")) / "hvsc"
PAL_CLOCK = 985248
AUTOMATAS = "MUSICIANS/G/Goto80/Automatas.sid"
COMMANDO = "MUSICIANS/H/Hubbard_Rob/Commando.sid"


def _tune(relpath):
    path = resolve_tune(relpath, cache_dir=_CACHE)
    if path is None:
        pytest.skip("%s unavailable (no HVSC tree, no cache, offline)" % relpath)
    return Path(path).read_bytes()


def _front_end(data, seconds):
    """Trace ``seconds`` of music, then lift, type storage and build procedures."""
    img, schedule = find_entries(data)
    entry = schedule[0]
    calls = int(seconds * PAL_CLOCK / entry.cycles_per_tick)
    tracer = Tracer(img, entry)
    tracer.run_init()
    tracer.run_calls(calls)
    trace = tracer.trace()
    lifted = lift_trace(trace)
    regions = build_regions(trace, lifted)
    return entry, calls, trace, lifted, regions, build_procs(trace, lifted, regions)


def _reference_writes(data, calls, cycles_per_tick):
    """Per-call ``(addr, val)`` lists from an unmodified PcodeVM run."""
    band, _load, init, play = c64.load_psid(data)
    lo, hi = c64.psid_image(data)
    mem = bytearray(c64.poweron_ram())
    mem[lo:hi] = band[lo:hi]
    mem[0xD418] = 0x0F
    vm = PcodeVM(bytes(mem))
    vm.reg[0] = 0
    cache = {}
    run_sub(vm, init, cache, lift)
    out = []
    vm.wlog = []
    for _ in range(calls):
        c0 = vm.cycles
        run_sub(vm, play, cache, lift)
        if vm.cycles - c0 < cycles_per_tick:
            vm.cycles = c0 + cycles_per_tick
        out.append([(0xD400 + r, v) for _c, r, v in vm.wlog])
        vm.wlog = []
    return out


def _per_call(trace, calls):
    out = [[] for _ in range(calls)]
    w = trace.wlog
    for c, a, v in zip(w["call"], w["addr"], w["val"]):
        if c < calls:
            out[int(c)].append((int(a), int(v)))
    return out


def test_automatas_front_end():
    data = _tune(AUTOMATAS)
    entry, calls, trace, lifted, regions, procs = _front_end(data, seconds=20)

    # schedule (doc section 2: CIA-1 TA = $0998 -> 2457 cycles, 8 ticks per frame)
    assert (entry.kind, entry.addr, entry.cycles_per_tick, entry.source) == (
        "sub",
        0x0FE3,
        2457,
        "cia_timer",
    )
    assert trace.meta["calls"] == calls

    # inputs: exactly two sites, both in init ($D012 busy-wait, one $D41B read)
    assert sorted(trace.input_sites) == [(0x14CE, 0xD012), (0x14E3, 0xD41B)]
    kinds = {k[1]: v for k, v in trace.input_sites.items()}
    assert kinds[0xD012]["kind"] == "raster" and kinds[0xD012]["phase"] == 1
    assert kinds[0xD41B]["kind"] == "sid_readback" and kinds[0xD41B]["count"] == 1

    # SMC: play-written operand cells including the wrapper's call counter
    assert len(trace.cells) >= 70 and 0x0FE4 in trace.cells
    assert set(trace.opcode_cells()) == {0x10B8, 0x10BF, 0x10D8}
    assert trace.variants_at(0x10D8) == [0x60, 0xA9]  # RTS / LDA #
    assert trace.variants_at(0x10B8) == trace.variants_at(0x10BF) == [0x69, 0xE9]  # ADC / SBC
    assert {lifted[k].mnemonic for k in trace.site_at(0x10D8)} == {"RTS", "LDA"}
    assert lifted[trace.site_at(0x0FE3)[0]].cell_loads == [(0x0FE4, 1)]  # LDA #cnt -> load

    # six illegal-opcode kinds on the hot path
    play_ops = {k[1] for k, s in trace.sites.items() if s["phases"] & 2}
    assert len(play_ops & set(ILLEGAL_OPCODES)) == 6

    # calls and returns: no unmatched RTS, the two play-time JSR targets
    assert trace.meta["unmatched_rts"] == 0
    assert {0x1022, 0x168C} <= trace.jsr_targets
    assert not any(k[1] == 0x6C for k in trace.sites)  # no JMP (ind)

    # procedures: the JMP-entered main, the JSR-entered sub that calls it
    entries = {p.entry: p for p in procs.values()}
    assert {0x0FD0, 0x0FE3, 0x1003, 0x1006, 0x1022, 0x168C} <= set(entries)
    tail = list(entries[0x1003].nodes.values())
    assert len(tail) == 1 and tail[0]["term"] == "tail" and tail[0]["call"] == [0x1022]
    assert 0x1022 in entries[0x1006].summary["calls"]
    main = entries[0x1022]
    assert sorted(a["opcode"] for a in main.variant_switch[0x10D8]["arms"]) == [0x60, 0xA9]
    assert main.nodes[(0x10D8, 0x60)]["term"] == "return"  # the sub-tick gate returns
    assert main.nodes[(0x10D8, 0xA9)]["term"] in ("goto", "branch")
    assert procs_json(procs)["procs"]

    # regions: struct-of-code at stride 49 in the SID write band
    band = [
        r for r in regions if r.stride == 49 and len(r.addrs) == 3 and 0x1022 <= r.base <= 0x10A8
    ]
    assert len(band) >= 8
    assert all(r.kind == "state" for r in band)
    # the (zp),Y pointer is its own region, not part of the sidTAB rows it reads
    by_addr = index_regions(regions)
    ptr = by_addr[0x00FB]
    assert ptr.addrs == (0x00FB, 0x00FC) and ptr.kind == "state"
    rows = by_addr[0x2C8F]
    assert rows is not ptr and rows.kind == "const" and rows.size > 100

    # 24 SID writes per call (7 registers x 3 voices + $D417 + $D418 + $D416)
    assert len([c for c in trace.wlog["call"] if c < calls]) == 24 * calls

    # the instrumented VM stays byte-exact against the plain one on the hard tune
    assert _per_call(trace, calls) == _reference_writes(data, calls, entry.cycles_per_tick)


def test_commando_front_end_smoke():
    data = _tune(COMMANDO)
    entry, calls, trace, _lifted, regions, procs = _front_end(data, seconds=10)
    assert (entry.kind, entry.addr, entry.source) == ("sub", 0x5012, "pal_video")
    assert trace.meta["unmatched_rts"] == 0 and trace.sites and procs
    assert {p.kind for p in procs.values()} == {"init", "tick", "sub"}

    # the per-voice state is struct-of-arrays at stride 1 (anatomy 3.1.1)
    triples = [
        r for r in regions if r.stride == 1 and len(r.addrs) == 3 and 0x54E8 <= r.base <= 0x5522
    ]
    assert len(triples) >= 8
    assert {r.base for r in triples} >= {0x54EC, 0x54F2, 0x54F5, 0x54FB, 0x54FE, 0x551A, 0x551D}

    # the instrumented VM stays byte-exact against the plain one
    assert _per_call(trace, calls) == _reference_writes(data, calls, entry.cycles_per_tick)
