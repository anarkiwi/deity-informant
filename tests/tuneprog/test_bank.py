"""The 6510 port: what $D000-$DFFF is, per access, and the RAM under it as storage."""

import json

from deity_informant.tuneprog import pipeline, printer
from deity_informant.tuneprog.ir import Load, Store
from deity_informant.tuneprog.machine import MachineImage, port_bank
from deity_informant.tuneprog.verify import verify

from _asm import asm, psid
from _prog import PLAY, front, tuneprog

CALLS = 6
SHADOW = 0xD400
REAL = 0xD401

BANKED = asm(  # stage a byte under the SID with I/O out, then write the register file
    PLAY,
    "init: LDA #$00",
    "STA cnt",
    "RTS",
    "play: LDA #$34",
    "STA $01",
    "LDA $D400",
    "STA $02",
    "INC cnt",
    "LDA cnt",
    "STA $D400",
    "LDA #$35",
    "STA $01",
    "LDA $02",
    "STA $D401",
    "RTS",
    "cnt: BRK",
)
PLAIN = asm(
    PLAY,
    "init: LDA #$00",
    "STA cnt",
    "RTS",
    "play: INC cnt",
    "LDA cnt",
    "STA $D400",
    "RTS",
    "cnt: BRK",
)


def sid_writes(trace):
    return [(int(a), int(v)) for a, v in zip(trace.wlog["addr"], trace.wlog["val"])]


def accesses(prog, addr):
    """``{(node type, access class)}`` of every typed access whose region is at ``addr``."""
    return {
        (type(x).__name__, x.cls)
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in b.stmts
        for x in (s, getattr(s, "e", None))
        if type(x) in (Store, Load) and x.r >= 0 and x.lo == addr
    }


def test_the_pre_init_port_is_the_one_a_kernal_initialised_host_leaves():
    img = MachineImage.from_sid(psid({PLAY: PLAIN}, PLAIN.labels["init"], PLAIN.labels["play"]))
    assert (img.mem[0], img.mem[1]) == (0x2F, 0x37) and port_bank(img.mem) == "io"
    mem = bytearray(img.mem)
    mem[1] = 0x34
    assert port_bank(mem) == "ram"  # with the direction byte an input it would not be


def test_a_store_with_io_banked_out_writes_memory_and_no_register():
    trace = front(BANKED, calls=CALLS)[0]
    assert {a for a, _v in sid_writes(trace)} == {REAL}
    assert SHADOW in trace.written_play
    assert trace.chip_ops and all(pc != PLAY for pc, _i in trace.chip_ops)


def test_the_ram_under_the_register_file_is_a_region_and_its_accesses_are_memory():
    trace, prog = tuneprog(BANKED, calls=CALLS, s4=True)
    rgn = {r.base: r.kind for r in prog.storage if r.id >= 0}
    assert rgn[SHADOW] == "state" and rgn[REAL] == "io"
    assert accesses(prog, SHADOW) == {("Store", "ram"), ("Load", "ram")}
    assert accesses(prog, REAL) == {("Store", "io")}
    assert verify(prog, trace, calls=CALLS, prefix=CALLS).div is None


def test_without_banking_the_same_store_is_the_register_it_names():
    _trace, prog = tuneprog(PLAIN, calls=CALLS, s4=True)
    assert [r.kind for r in prog.storage if r.id >= 0 and r.base == SHADOW] == ["io"]


def test_the_shadow_prints_as_the_register_file_it_lies_under():
    _trace, prog = tuneprog(BANKED, calls=CALLS, s4=True)
    view, st, names = pipeline.present(prog)
    text = printer.render(view, st, names, pcs=False)
    assert "input(" not in text, text
    assert "ghost[0].freq_lo" in text and "sid[0].freq_hi" in text, text


def test_the_program_carries_the_port_bytes_the_bank_is_decided_by():
    _trace, prog = tuneprog(BANKED, calls=CALLS, s4=True)
    assert bytes(prog.image()[0:2]) == b"\x2f\x37"
    assert [r.name for r in prog.storage if r.base == 0 and r.id < 0] == ["image_port"]


def test_a_certificate_pins_no_input_for_the_ram_under_io(tmp_path):
    out = tmp_path / "o"
    sid = tmp_path / "banked.sid"
    sid.write_bytes(psid({PLAY: BANKED}, BANKED.labels["init"], BANKED.labels["play"]))
    assert pipeline.main([str(sid), "--out", str(out), "--calls", str(CALLS)]) == 0
    sub = json.loads((out / "certificate.json").read_text())["subtunes"][0]
    assert sub["divergences"] == 0 and sub["inputs_pinned"] == 0
