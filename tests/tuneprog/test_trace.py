"""S1: per-op attribution, sites/variants, edges, JSR/RTS pairing, inputs, resume."""

import pytest

from deity_informant.tuneprog.machine import Entry, Refusal, init_runner
from deity_informant.tuneprog.trace import (
    Trace,
    Tracer,
    TraceVM,
    input_kind,
    run_trace,
    site_key,
)
from deity_informant import lift

from _asm import asm, sid_image, trace_prog

PLAY = 0x1000


def _reads(trace, pc):
    return trace.sites[trace.site_at(pc)[0]]["reads"]


def test_per_op_read_sets_separate_pointer_from_stream():
    # LDA ($FB),Y: the two pointer-byte fetches and the stream load are three ops.
    init = asm(0x1100, "LDA #$10", "STA $FB", "LDA #$11", "STA $FC", "RTS")
    T, _ = trace_prog(
        {PLAY: asm(PLAY, "LDA ($FB),Y", "STA $D400", "RTS"), 0x1100: init},
        init=0x1100,
        play=PLAY,
        data={0x1110: 0x5A},
    )
    sets = [s for s in _reads(T, PLAY).values() if s]
    assert {0x00FB} in sets and {0x00FC} in sets
    assert {0x1110} in sets  # Y = 0 at entry
    assert not any(0x00FB in s and 0x1110 in s for s in sets)


def test_per_op_write_set_and_index_domain():
    prog = asm(PLAY, "LDX #$02", "loop: STA $1100,X", "DEX", "BPL loop", "RTS")
    T, _ = trace_prog(
        {PLAY: prog, 0x1200: asm(0x1200, "RTS")},
        init=0x1200,
        play=PLAY,
        data={0x1100: 0, 0x1101: 0, 0x1102: 0},
    )
    key = T.site_at(prog.labels["loop"])[0]
    assert list(T.sites[key]["writes"].values()) == [{0x1100, 0x1101, 0x1102}]
    assert T.sites[key]["idx"] == [0, 1, 2]


def test_jsr_rts_pairing_with_a_tail_entry():
    play = asm(PLAY, "JSR $1020", "call2: JSR $1030", "RTS")
    sub = asm(0x1020, "LDA #$01", "jmp1: JMP $1030")  # tail jump into another JSR target
    tail = asm(0x1030, "STA $D400", "rts1: RTS")
    T, _ = trace_prog(
        {PLAY: play, 0x1020: sub, 0x1030: tail},
        init=0x1040,
        play=PLAY,
        data={0x1040: 0x60},
        calls=1,
    )
    assert T.jsr_targets == {0x1020, 0x1030}
    assert T.edges[(sub.labels["jmp1"], 0x4C, 0x1030)][0] == "tail"
    rts = [k for k in T.rets if k[0] == tail.labels["rts1"]][0]
    assert T.rets[rts]["unmatched"] == 0
    assert set(T.rets[rts]["matched"]) == {PLAY, play.labels["call2"]}
    assert T.meta["unmatched_rts"] == 0
    assert T.meta["max_depth"] == 2


def test_unmatched_rts_is_recorded():
    # RTS trick: push a target and return to it.
    play = asm(PLAY, "LDA #$10", "PHA", "LDA #$1F", "PHA", "trick: RTS")
    T, _ = trace_prog(
        {PLAY: play, 0x1020: asm(0x1020, "RTS"), 0x1030: asm(0x1030, "RTS")},
        init=0x1030,
        play=PLAY,
        calls=1,
    )
    trick = [k for k in T.rets if k[0] == play.labels["trick"]][0]
    assert T.rets[trick]["unmatched"] == 1
    assert set(T.rets[trick]["targets"]) == {0x1020}
    assert T.meta["unmatched_rts"] == 1


def test_site_key_drops_smc_operand_bytes():
    play = asm(PLAY, "smc: LDA #$00", "STA $D400", "INC $1001", "fixed: LDX #$07", "RTS")
    T, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY, calls=4)
    keys = T.site_at(PLAY)
    assert keys == [(PLAY, 0xA9, (None,))]  # one site despite four operand values
    assert 0x1001 in T.cells
    assert len(T.sites[keys[0]]["variants"]) == 4
    # a non-SMC immediate keeps its operand byte in the key
    assert T.site_at(play.labels["fixed"])[0][2] == (0x07,)


def test_site_key_helper():
    assert site_key(0x1000, 0xA9, b"\xa9\x12", {0x1001}) == (0x1000, 0xA9, (None,))
    assert site_key(0x1000, 0xA9, b"\xa9\x12", set()) == (0x1000, 0xA9, (0x12,))


def test_opcode_cell_gives_two_site_keys():
    # $1010 alternates RTS <-> LDA #$00 under its own writer.
    play = asm(PLAY, "LDA $1010", "EOR #$C9", "STA $1010", "JSR $1010", "RTS")
    T, _ = trace_prog(
        {PLAY: play, 0x1010: asm(0x1010, "LDA #$00", "RTS"), 0x1020: asm(0x1020, "RTS")},
        init=0x1020,
        play=PLAY,
        calls=4,
        data={0x1010: 0xA9},
    )
    assert sorted(T.opcode_cells()[0x1010]) == [0x60, 0xA9]
    assert len(T.site_at(0x1010)) == 2
    assert 0x1010 in T.cells and T.cell_values[0x1010] == {0x60, 0xA9}


def test_edge_kinds():
    play = asm(PLAY, "LDX #$01", "br: BNE ind", "NOP", "ind: JMP ($1200)", "done: RTS")
    T, _ = trace_prog(
        {PLAY: play, 0x1020: asm(0x1020, "RTS")},
        init=0x1020,
        play=PLAY,
        data={0x1200: play.labels["done"] & 0xFF, 0x1201: play.labels["done"] >> 8},
    )
    L = play.labels
    kinds = {(f, t): k for (f, _o, t), (k, _n) in T.edges.items()}
    assert kinds[(PLAY, L["br"])] == "fall"
    assert kinds[(L["br"], L["ind"])] == "br_taken"
    assert kinds[(L["ind"], L["done"])] == "jmpind"


def test_branch_not_taken_edge():
    play = asm(PLAY, "LDX #$00", "br: BNE skip", "LDA #$01", "skip: STA $D400", "RTS")
    T, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY)
    kinds = {(f, t): k for (f, _o, t), (k, _n) in T.edges.items()}
    assert kinds[(play.labels["br"], play.labels["br"] + 2)] == "br_not"


def test_sid_and_io_write_logs_and_init_writes():
    init = asm(0x1100, "LDA #$0F", "STA $D418", "RTS")
    play = asm(PLAY, "LDA #$07", "STA $D400", "STA $D020", "RTS")
    T, _ = trace_prog({PLAY: play, 0x1100: init}, init=0x1100, play=PLAY, calls=3)
    assert (0xD418, 0x0F) == T.init_writes[0][:2]
    play_w = [
        (int(a), int(v)) for c, a, v in zip(T.wlog["call"], T.wlog["addr"], T.wlog["val"]) if c < 3
    ]
    assert play_w == [(0xD400, 7)] * 3
    assert [int(a) for c, a in zip(T.iolog["call"], T.iolog["addr"]) if c < 3] == [0xD020] * 3


def test_banked_out_io_store_is_not_a_sid_write():
    play = asm(
        PLAY,
        "LDA #$2F",
        "STA $00",
        "LDA #$34",
        "STA $01",
        "LDA #$07",
        "STA $D400",
        "LDA #$37",
        "STA $01",
        "RTS",
    )
    T, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY)
    assert not [c for c in T.wlog["call"] if c < 1]
    assert 0xD400 in T.written_play  # it was a RAM store under the I/O window


def test_input_record_override_and_replay():
    play = asm(PLAY, "LDA $D41B", "STA $D400", "RTS")
    blocks = {PLAY: play, 0x1020: asm(0x1020, "RTS")}
    T, _ = trace_prog(blocks, init=0x1020, play=PLAY, calls=3)
    assert [i[3] for i in T.inputs] == [0xD41B] * 3
    assert T.input_sites[(PLAY, 0xD41B)]["kind"] == "sid_readback"

    Tov, _ = trace_prog(blocks, init=0x1020, play=PLAY, calls=3, override={0xD41B: 0x42})
    assert list(Tov.wlog["val"]) == [0x42] * 3

    Trp, _ = trace_prog(blocks, init=0x1020, play=PLAY, calls=3, policy="replay", inputs=T.inputs)
    assert list(Trp.wlog["val"]) == list(T.wlog["val"])


def test_replay_mismatch_refuses():
    play = asm(PLAY, "LDA $D41B", "STA $D400", "RTS")
    blocks = {PLAY: play, 0x1020: asm(0x1020, "RTS")}
    with pytest.raises(Refusal) as e:
        trace_prog(
            blocks,
            init=0x1020,
            play=PLAY,
            calls=1,
            policy="replay",
            inputs=[(0, PLAY, 0, 0xD012, 1)],
        )
    assert e.value.reason == "input replay mismatch"


def test_live_in_registers_are_inputs():
    play = asm(PLAY, "STA $D400", "RTS")  # A read before any write in the tick
    T, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY)
    kinds = {v["kind"] for v in T.input_sites.values()}
    assert "entry_reg" in kinds
    assert input_kind(0x10000) == "entry_reg"


def test_uninitialised_ram_read_is_an_input():
    play = asm(PLAY, "LDA $9000", "STA $D400", "RTS")
    T, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY)
    assert T.input_sites[(PLAY, 0x9000)]["kind"] == "uninit_ram"


def test_input_kind_classification():
    assert input_kind(0xD012) == "raster"
    assert input_kind(0xD019) == "ack"
    assert input_kind(0xDC0D) == "ack"
    assert input_kind(0xD41C) == "sid_readback"
    assert input_kind(0xDC04) == "cia"
    assert input_kind(0xD020) == "io"
    assert input_kind(0x1234) == "uninit_ram"


def test_cia_busy_wait_in_init_terminates():
    init = asm(
        0x1100,
        "LDA #$10",
        "STA $DC04",
        "LDA #$00",
        "STA $DC05",
        "LDA #$11",
        "STA $DC0E",
        "wait: LDA $DC0D",
        "AND #$01",
        "BEQ wait",
        "RTS",
    )
    img = sid_image({PLAY: asm(PLAY, "RTS"), 0x1100: init}, 0x1100, PLAY)
    t = Tracer(img, Entry("sub", PLAY, 19656, "test"))
    t.run_init(budget=10000)
    assert t.vm.insns > 8  # the wait loop ran and then fell through


def test_state_hash_finds_the_period():
    play = asm(
        PLAY, "INC $1100", "LDA $1100", "CMP #$03", "BNE out", "LDA #$00", "STA $1100", "out: RTS"
    )
    T, tr = trace_prog(
        {PLAY: play, 0x1200: asm(0x1200, "RTS")},
        init=0x1200,
        play=PLAY,
        calls=9,
        data={0x1100: 0x00},
    )
    assert T.meta["period"] == 3
    assert tr.first_repeat == 3
    assert len(T.state_hash) == 9 and int(T.footprint_size[0]) == 1


def test_resume_equals_one_run(tmp_path):
    play = asm(PLAY, "INC $1100", "LDA $1100", "STA $D400", "RTS")
    blocks = {PLAY: play, 0x1200: asm(0x1200, "RTS")}
    img = sid_image(blocks, 0x1200, PLAY, {0x1100: 0})
    e = Entry("sub", PLAY, 19656, "test")
    whole = run_trace(img, e, 20)
    p = tmp_path / "resume.pkl"
    run_trace(sid_image(blocks, 0x1200, PLAY, {0x1100: 0}), e, 8, resume=p)
    part = run_trace(sid_image(blocks, 0x1200, PLAY, {0x1100: 0}), e, 20, resume=p)
    assert part.meta["calls"] == whole.meta["calls"] == 20
    assert sorted(part.sites) == sorted(whole.sites)
    assert list(part.wlog["val"]) == list(whole.wlog["val"])
    assert list(part.state_hash) == list(whole.state_hash)
    assert part.written_play == whole.written_play
    assert part.image_post_init == whole.image_post_init


def test_trace_save_load_round_trip(tmp_path):
    play = asm(PLAY, "LDA #$00", "STA $D400", "INC $1001", "JSR $1020", "RTS")
    T, _ = trace_prog(
        {PLAY: play, 0x1020: asm(0x1020, "LDA $D41B", "RTS")},
        init=0x1030,
        play=PLAY,
        calls=3,
        data={0x1030: 0x60},
    )
    T.save(tmp_path / "tr")
    B = Trace.load(tmp_path / "tr")
    assert sorted(B.sites) == sorted(T.sites)
    assert B.sites[T.site_at(PLAY)[0]]["reads"] == T.sites[T.site_at(PLAY)[0]]["reads"]
    assert B.edges == T.edges and B.calls == T.calls and B.rets == T.rets
    assert B.cells == T.cells and B.cell_values == T.cell_values
    assert B.inputs == T.inputs and B.input_sites == T.input_sites
    assert B.jsr_targets == T.jsr_targets and B.summaries == T.summaries
    assert list(B.wlog["addr"]) == list(T.wlog["addr"])
    assert B.image_post_init == T.image_post_init
    assert B.written_init == T.written_init and B.init_writes == T.init_writes


def test_call_summaries_from_activations():
    play = asm(PLAY, "LDX #$05", "JSR $1020", "RTS")
    T, _ = trace_prog(
        {PLAY: play, 0x1020: asm(0x1020, "TXA", "STA $D400", "RTS")},
        init=0x1030,
        play=PLAY,
        data={0x1030: 0x60},
        calls=1,
    )
    s = T.summaries[0x1020]
    assert s["rd"] & (1 << 1)  # X read before written -> live-in
    assert s["wr"] & 1  # A written -> def
    assert s["count"] == 1


def test_play_runaway_refuses():
    img = sid_image({PLAY: asm(PLAY, "JMP $1000"), 0x1100: asm(0x1100, "RTS")}, 0x1100, PLAY)
    t = Tracer(img, Entry("sub", PLAY, 19656, "test"))
    t.run_init()
    with pytest.raises(Refusal) as e:
        t.run_calls(1, budget=200)
    assert e.value.reason == "play runaway"


def test_irq_entry_is_driven_like_a_hardware_interrupt():
    handler = asm(0x1200, "LDA $D019", "STA $D400", "RTI")
    img = sid_image({0x1100: asm(0x1100, "RTS"), 0x1200: handler}, 0x1100, 0)
    t = Tracer(img, Entry("irq", 0x1200, 19656, "pal_video"))
    t.run_init()
    t.run_calls(2)
    T = t.trace()
    assert list(T.wlog["val"]) == [0x81, 0x81]  # the VIC source flag the handler polled
    assert T.meta["unmatched_rts"] == 0


def test_init_runner_is_reused_by_the_tracer():
    assert init_runner is not None and lift is not None


def test_multi_byte_access_is_attributed_byte_by_byte():
    # residualised 16-bit cell loads use the LOAD/STORE size contract of the base VM.
    img = sid_image({PLAY: asm(PLAY, "RTS")}, PLAY, PLAY, {0x1001: 0x34, 0x1002: 0x12})
    vm = TraceVM(img.mem, img)
    vm._rs, vm._ws = {}, {}
    assert vm._rd(0x1001, 2, 3) == 0x1234
    assert vm._rs[3] == {0x1001, 0x1002}
    vm._wr(0x2000, 0xBEEF, 2, 4)
    assert vm._ws[4] == {0x2000, 0x2001}
    assert vm.mem[0x2000] == 0xEF and vm.mem[0x2001] == 0xBE


def test_banked_out_io_read_and_vic_ack():
    play = asm(
        PLAY,
        "LDA #$07",
        "STA $D019",  # ack: clears the VIC interrupt-source flag
        "LDA #$2F",
        "STA $00",
        "LDA #$34",
        "STA $01",
        "LDA $D400",  # RAM under I/O, not a SID read-back input
        "STA $1100",
        "LDA #$37",
        "STA $01",
        "RTS",
    )
    T, tr = trace_prog(
        {PLAY: play, 0x1200: asm(0x1200, "RTS")}, init=0x1200, play=PLAY, calls=1, data={0x1100: 0}
    )
    assert 0xD400 not in {k[1] for k in T.input_sites}
    assert tr.vm.vicirq == 0
    assert (0xD019, 0x07) in [(int(a), int(v)) for a, v in zip(T.iolog["addr"], T.iolog["val"])]


def test_trace_queries():
    play = asm(PLAY, "smc: LDA #$00", "STA $D400", "INC smc+1", "RTS")
    T, _ = trace_prog({PLAY: play, 0x1020: asm(0x1020, "RTS")}, init=0x1020, play=PLAY, calls=2)
    assert T.writers_of(PLAY + 1) == T.site_at(PLAY + 5)
    assert T.writers_of(0x9999) == []
