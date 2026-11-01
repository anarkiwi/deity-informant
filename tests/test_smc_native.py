"""The machine oracle's native backend (vagrant-moses' SMC recorder) against the Python one."""

import struct
import sys
from pathlib import Path

import pytest

from deity_informant.smc import machine
from deity_informant.smc.machine import MachineOracle, native_cells
from deity_informant.smc.oracle import Run
from deity_informant.smc.space import Cell

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import smcequiv  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order

needs_recorder = pytest.mark.skipif(
    not (machine.core() and machine.SMC_NATIVE), reason="needs vagrant-moses' SMC recorder"
)
CASES = smcequiv.corpus_cases() if machine.core() else []


@needs_recorder
@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_native_labels_the_corpus_as_the_classifier(case):
    row = smcequiv.compare(case)
    assert row["python"] == row["native"], row["name"]


def _sid(code, init, play, magic=b"PSID", flags=0x14):
    hdr = magic + struct.pack(">HHHHHHHI", 2, 0x7C, 0x1000, init, play, 1, 1, 0)
    return hdr + b"\0" * 0x60 + struct.pack(">HBBBB", flags, 0, 0, 0, 0) + code


@needs_recorder
def test_a_song_case_agrees_and_is_costed(tmp_path):
    # play ($1001): INC the operand of its own LDA #, fetched later in the call
    path = tmp_path / "t.sid"
    path.write_bytes(_sid(bytes.fromhex("60ee0510a90060"), 0x1000, 0x1001))
    row = smcequiv._job((path, 5, 100_000))  # pylint: disable=protected-access
    assert row["equal"] and row["units"] == 5
    assert row["native"]["first"] == {"cpu.abs_rmw.operand.same_call_earlier.flat": 0}
    rsid = tmp_path / "r.sid"
    rsid.write_bytes(_sid(b"\x60", 0x1000, 0, magic=b"RSID", flags=0x16))
    assert smcequiv._job((rsid, 5, 100_000))["skip"]  # pylint: disable=protected-access
    out = smcequiv.summary([row, {"name": "x", "skip": True}])
    assert out["equal"] == out["compared"] == 1 and out["skipped"] == 1
    assert out["native_ms_per_unit"] > 0 and not out["mismatches"]


def test_native_events_map_to_cells_with_the_earliest_index():
    events = [
        ("cpu", 0x20, "ret_addr", "same_call_earlier", "flat", 0),  # JSR: a call frame
        ("cpu", 0x8D, "operand", "earlier_call", "flat", 2),
        ("cpu", 0x8D, "operand", "earlier_call", "flat", 1),
        ("cpu_port", None, "bank_reg", "pre_lift", "flat", 3),
        ("cpu_port", 0x85, "opcode", "same_call_earlier", "rom_vs_ram", 3),
        ("cpu_port", 0x20, "opcode", "same_call_earlier", "rom_vs_ram", 3),
        ("irq_push", None, "ret_addr", "async", "flat", 4),
        ("reu_dma", None, "opcode", "earlier_call", "flat", 5),
        ("cpu", 0xEA, "opcode", "same_insn", "flat", 6),
    ]
    cells, first, bad = native_cells(events)
    assert first == {
        Cell("cpu", "abs", False, "operand", "earlier_call", "flat"): 2,
        Cell("cpu_port", "reset", False, "bank_reg", "pre_lift", "flat"): 3,
        Cell("cpu_port", "zp", False, "opcode", "same_call_earlier", "rom_vs_ram"): 3,
        Cell("irq_push", "sp_rel", False, "ret_addr", "async", "flat"): 4,
        Cell("reu_dma", "dma", False, "opcode", "earlier_call", "flat"): 5,
    }
    assert cells == set(first)
    assert [b[1] for b in bad] == [0x20, 0xEA]


def test_run_counts_a_sampled_unclassified_list():
    assert Run(unclassified=[1, 2]).n_unclassified == 2
    assert Run(unclassified=[1], count=7).n_unclassified == 7


@pytest.mark.skipif(not machine.core(), reason="needs vagrant-moses' native machine")
def test_the_backend_is_picked_by_availability_hook_and_devices():
    class Hooked(MachineOracle):
        decodes = 0

        def _decode(self, mc):
            self.decodes += 1
            super()._decode(mc)

    class Dma:
        def attach(self, host):
            self.host = host  # pylint: disable=attribute-defined-outside-init

        def read(self, _cycle, _reg):
            return 0

        def write(self, _cycle, _reg, _value):
            pass

    img = bytes(0x10000)
    assert MachineOracle(img).native == machine.SMC_NATIVE
    assert not Hooked(img).native
    assert not MachineOracle(img, devices={0xD400: Dma()}).native
    assert not MachineOracle(img, backend="python").native
    with pytest.raises(ValueError):
        MachineOracle(img, backend="other")


@needs_recorder
def test_frame_indices_and_timeouts_agree():
    # init points $0314 at $1010: INC $1017; LDA $DC0D; LDA #$00; JMP $EA81 (patches its operand)
    init = bytes.fromhex("78a9108d1403a9108d15035860")
    hdlr = bytes.fromhex("ee1710ad0ddca9004c81ea")
    img = bytearray(0x10000)
    img[0x1000 : 0x1000 + len(init)] = init
    img[0x1010 : 0x1010 + len(hdlr)] = hdlr
    hang = bytearray(0x10000)
    hang[0x2000:0x2003] = bytes.fromhex("4c0020")
    runs = {}
    for b in ("python", "native"):
        o = MachineOracle(img, backend=b)
        r = o.run([], init=[(0x1000, 0)], frames=4, irq_setup=True, index_frames=True)
        loop = MachineOracle(hang, backend=b)
        stuck = loop.run([(0x2000, 0)], call_cycles=5_000)
        runs[b] = (r.first, r.returned, stuck.returned, loop.timeout)
    assert runs["python"] == runs["native"]
    first, returned, stuck, timeout = runs["native"]
    assert first[Cell("cpu", "abs", True, "operand", "async", "flat")] == 0
    assert set(first.values()) <= set(range(4))
    assert returned and not stuck and timeout
