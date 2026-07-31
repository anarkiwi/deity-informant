"""Init-phase copy origins: the traced transfer, the refusals, the mutation evidence.

Covers docs/frameprog.md 4.5 -- a cell staged at init out of a declared const table
carries that table cell as its origin, a computed or undeclared or stack-borne write
carries none, and the play phase's own store supersedes what init staged.
"""

import numpy as np
import pytest

from deity_informant import datadecl
from deity_informant import frameprog
from deity_informant import frameval
from deity_informant import initcopy
from deity_informant import structured as S
from deity_informant.lifter import lift
import _fuzzgen as FG

from test_frameprog import _fuzz_model

TBL, CNT, SID, ORG = FG.TBL, FG.CNT, FG.SID, FG.ORG
INIT = 0x0F00


def _rec(ops):
    return {"ops": ops}


def _lifted(code, pc=0x1000):
    mem = bytearray(0x10000)
    mem[pc : pc + len(code)] = bytes(code)
    return lift(mem, pc)


def _player(init_asm, play_asm, data, frames=4, outs=(SID + 2,)):
    return FG.Player(
        "probe",
        ORG,
        play_asm.assemble(),
        set(outs),
        {"indexed"},
        data=dict(data),
        frames=frames,
        init=init_asm.assemble(),
        init_org=INIT,
    )


def _staged(step_from=TBL + 1):
    """Init stages a step byte out of the table the play phase indexes."""
    a = FG.Asm(INIT)
    a.i("LDA", "abs", step_from).i("STA", "abs", CNT + 2).i("RTS")
    p = FG.Asm(ORG)
    p.i("LDX", "abs", CNT + 1).i("LDA", "absx", TBL).i("STA", "abs", SID + 4)
    p.i("LDA", "abs", CNT).i("CLC").i("ADC", "abs", CNT + 2)
    p.i("STA", "abs", CNT).i("STA", "abs", SID + 2)
    p.i("INX").i("TXA").i("AND", "imm", 3).i("STA", "abs", CNT + 1).i("RTS")
    data = {TBL + k: 3 + k for k in range(4)}
    data.update({CNT: 0, CNT + 1: 0, CNT + 2: 0})
    return _player(a, p, data, outs=(SID + 2, SID + 4))


# ---- the static copy transfer ----------------------------------------------------
def test_a_load_then_store_is_a_traced_copy():
    """``LDA abs`` binds A to the load; ``STA abs`` stores that same byte."""
    regs, stores = initcopy.transfer(_lifted([0xAD, 0x00, 0x20]))  # LDA $2000
    assert regs == ((0, ("ld", 0)),) and stores == ()
    regs, stores = initcopy.transfer(_lifted([0x8D, 0x00, 0x30]))  # STA $3000
    assert regs == () and stores == (("in", 0),)


def test_a_register_transfer_carries_the_entry_origin():
    """``TAX`` moves A's origin to X; the transfer is over entry state."""
    assert initcopy.transfer(_lifted([0xAA]))[0] == ((1, ("in", 0)),)  # TAX


def test_arithmetic_is_not_a_copy():
    """``ADC``/``INC`` compute; the destination keeps no origin."""
    assert initcopy.transfer(_lifted([0x6D, 0x00, 0x20]))[0] == ((0, None),)  # ADC $2000
    assert initcopy.transfer(_lifted([0xEE, 0x00, 0x20]))[1] == (None,)  # INC $2000
    assert initcopy.transfer(_lifted([0xA9, 0x07]))[0] == ((0, None),)  # LDA #$07


def test_an_indirect_load_names_the_value_load_not_the_pointer():
    """``LDA (zp),Y`` loads the pointer halves first; the value is the third load."""
    assert initcopy.transfer(_lifted([0xB1, 0x02]))[0] == ((0, ("ld", 2)),)


def test_a_record_that_moves_no_byte_is_skipped():
    """A flag-only record carries no transfer at all, so the tracer never runs it."""
    rec = _lifted([0x18])  # CLC
    assert initcopy.transfer(rec) is False
    assert initcopy.transfer(rec) is False  # cached on the record
    tr = initcopy.Tracer()
    tr.step(rec, 0x1000, [], [])
    assert tr.cells == {} and tr.stores == 0


def test_a_wide_varnode_carries_no_byte_origin():
    """Only a one-byte value is a copied byte; a word move drops the origin."""
    load2 = _rec(
        [["LOAD", ["u", 0, 2], [["c", 0x20, 2]]], ["STORE", None, [["c", 4, 2], ["u", 0, 2]]]]
    )
    assert initcopy.transfer(load2) == ((), (None,))
    copy2 = _rec(
        [["COPY", ["u", 0, 2], [["r", 0, 2]]], ["STORE", None, [["c", 4, 2], ["u", 0, 1]]]]
    )
    assert initcopy.transfer(copy2) == ((), (None,))


def test_a_unique_read_before_any_binding_has_no_origin():
    """A unique the record never wrote carries nothing (registers carry their entry)."""
    assert initcopy.transfer(_rec([["STORE", None, [["c", 4, 2], ["u", 9, 1]]]]))[1] == (None,)


# ---- the tracer: last write wins, computed and stack writes refuse ----------------
def test_the_tracer_chains_and_refuses():
    """A copy of a copy path-compresses; a computed or stack write drops the cell."""
    tr = initcopy.Tracer()
    tr.bind(0x0400, 0x2000, 0x1000)
    assert tr.cells == {0x0400: 0x2000}
    tr.step(_lifted([0xAD, 0x00, 0x04]), 0x1002, [0x0400], [])  # LDA $0400
    tr.step(_lifted([0x8D, 0x01, 0x04]), 0x1005, [], [0x0401])  # STA $0401
    assert tr.cells[0x0401] == 0x2000  # path-compressed to the source, not to $0400
    tr.bind(0x0401, None, 0x1005)
    assert 0x0401 not in tr.cells and tr.refused[0x1005] == 1
    tr.bind(0x0180, 0x2000, 0x1008)
    tr.bind(0x0402, 0x0180, 0x100A)
    assert 0x0180 not in tr.cells and 0x0402 not in tr.cells


def test_a_cell_staged_twice_keeps_the_last_write_and_counts_the_conflict():
    """Last write wins -- that byte is what ``mem0`` holds -- and the clash is reported."""
    tr = initcopy.Tracer()
    tr.bind(0x0400, 0x2000, 0x1000)
    tr.bind(0x0400, 0x2001, 0x1002)
    assert tr.cells[0x0400] == 0x2001 and tr.conflict == {0x0400}


# ---- the declared reduction ------------------------------------------------------
def _decl(base, size, stride=1, mut=()):
    return {"base": base, "size": size, "stride": stride, "mut": list(mut), "kind": "table"}


def test_only_a_declared_byte_at_a_non_mut_offset_is_an_origin():
    """The origin must be const data: outside a declaration, or a `mut` lane, refuses."""
    tr = initcopy.Tracer()
    tr.bind(0x0400, 0x2000, 0x1000)  # declared, lane 0
    tr.bind(0x0401, 0x2001, 0x1002)  # declared, lane 1 -- play-written
    tr.bind(0x0402, 0x2100, 0x1004)  # past the declaration
    tr.bind(0x0403, 0x1000, 0x1006)  # below every declaration
    decls = [_decl(0x2000, 0x80, stride=2, mut=(1,)), _decl(0x0000, 0, stride=1)]
    out, sites, cens = initcopy.reduce(tr, datadecl.Regions(decls).const_at, played={0x0400})
    assert out == {0x0400: 0x2000}
    assert cens["origins"] == 1 and cens["undeclared"] == 3 and cens["play_written"] == 1
    assert cens["cells"] == 4 and cens["computed"] == 0
    assert sites[0x1000] == ((0x0400,), 0, 0) and sites[0x1004] == ((), 1, 0)


def test_a_flat_region_takes_its_mut_offsets_as_cells():
    """Snapshot soundness is per record offset; a flat region's record is the region."""
    tr = initcopy.Tracer()
    tr.bind(0x0400, 0x3005, 0x1000)
    out, _s, cens = initcopy.reduce(tr, datadecl.Regions([_decl(0x3000, 0x10, mut=(5,))]).const_at)
    assert not out and cens["undeclared"] == 1


# ---- the map at the frame program, and what it buys the query --------------------
def test_the_staged_step_carries_the_declared_table_cell_it_was_copied_from():
    """The staged RAM cell's origin is the table cell init loaded, with a proof record."""
    prog = frameprog.program(_fuzz_model(_staged()))
    assert prog.prov0 == {CNT + 2: TBL + 1}
    proofs = [p for p in prog.proofs if p.kind == "init-copy"]
    assert [p.status for p in proofs] == ["resolved"]
    assert proofs[0].targets == (CNT + 2,) and "declared const byte" in proofs[0].lemma
    assert prog.init_census["origins"] == 1 and prog.init_census["cells"] == 1


def test_the_query_reports_the_origin_of_a_cell_no_sid_store_reads():
    """``eval_watch`` puts the table cell ahead of the staged cell (spec 1.4)."""
    model = _fuzz_model(_staged())
    prog = frameprog.program(model)
    trace, _w = frameprog.iota(model, 4)
    watch = [s for proc in prog.procs for s in proc[3] if s[0] == "st" and s[1][1] == CNT]
    _f, _s, wat = frameval.eval_watch(prog, trace, 4, watch)
    assert wat[0] and wat[0][0][1] == CNT
    assert wat[0][0][2][0] == TBL + 1  # the origin, ahead of the cell that read it


def test_the_annotation_moves_no_record():
    """Seeding the map cannot change a value, a write or a canonical record."""
    model = _fuzz_model(_staged())
    prog = frameprog.program(model)
    trace, _w = frameprog.iota(model, 6)
    with_map = frameval.eval_fp(prog, trace, 6)
    prog.prov0 = {}
    assert frameval.eval_fp(prog, trace, 6) == with_map


@pytest.mark.parametrize("seed", [0, 3, 11])
def test_gate_fp_holds_on_the_init_staged_shape(seed):
    """The fuzz class passes Gate FP and the canonical fixpoint."""
    p = FG.t_init_param(np.random.default_rng(seed))
    model = _fuzz_model(p)
    assert frameval.gate_fp(model, p.frames) is None
    text = frameprog.emit(model)
    assert frameprog.dumps(frameprog.loads(text)) == text


# ---- refusals that must stay refusals --------------------------------------------
def test_a_computed_init_value_gets_no_origin():
    """An immediate stored at init is not a copy; the cell stays unnamed."""
    a = FG.Asm(INIT)
    a.i("LDA", "imm", 5).i("STA", "abs", CNT + 2).i("RTS")
    p = FG.Asm(ORG)
    p.i("LDX", "abs", CNT + 1).i("LDA", "absx", TBL).i("STA", "abs", SID + 4)
    p.i("LDA", "abs", CNT).i("CLC").i("ADC", "abs", CNT + 2)
    p.i("STA", "abs", CNT).i("STA", "abs", SID + 2).i("RTS")
    data = {TBL + k: 3 + k for k in range(4)}
    data.update({CNT: 0, CNT + 1: 0, CNT + 2: 0})
    prog = frameprog.program(_fuzz_model(_player(a, p, data, outs=(SID + 2, SID + 4))))
    assert not prog.prov0 and prog.init_census["computed"] == 1


def test_an_undeclared_source_is_refused_and_counted():
    """A byte staged out of a cell no declaration names carries no origin."""
    prog = frameprog.program(_fuzz_model(_staged(step_from=TBL + 0x200)))
    assert not prog.prov0 and prog.init_census["undeclared"] == 1


def test_the_play_phase_supersedes_what_init_staged():
    """A play store to the staged cell rebinds it by the one-contributor rule."""
    a = FG.Asm(INIT)
    a.i("LDA", "abs", TBL + 1).i("STA", "abs", CNT + 2).i("RTS")
    p = FG.Asm(ORG)
    p.i("LDX", "abs", CNT + 1).i("LDA", "absx", TBL).i("STA", "abs", SID + 4)
    p.i("LDA", "abs", CNT + 2).i("STA", "abs", SID + 2)
    p.i("LDA", "imm", 0x77).i("STA", "abs", CNT + 2).i("RTS")
    data = {TBL + k: 3 + k for k in range(4)}
    data.update({CNT + 1: 0, CNT + 2: 0})
    model = _fuzz_model(_player(a, p, data, frames=3, outs=(SID + 2, SID + 4)))
    prog = frameprog.program(model)
    assert prog.prov0 == {CNT + 2: TBL + 1}
    trace, _w = frameprog.iota(model, 3)
    ev = frameval.Evaluator(prog, trace, sources=True)
    ev.frames(3)
    assert CNT + 2 not in ev.prov  # the computed play write dropped it


# ---- M-FP mutation evidence: each weakening moves a reported record ---------------
def _sources(prog, trace, nframes, reg):
    _f, srcs = frameval.eval_src(prog, trace, nframes)
    out = []
    for fr, sr in zip(_f, srcs):
        out += [s for (r, _v), s in zip(fr, sr) if r == reg]
    return out


def test_mutation_an_origin_from_a_value_match_moves_the_record():
    """A cell merely holding the same byte is a different origin, so the record moves."""
    model = _fuzz_model(_staged())
    prog = frameprog.program(model)
    trace, _w = frameprog.iota(model, 4)
    watch = [s for proc in prog.procs for s in proc[3] if s[0] == "st" and s[1][1] == CNT]
    _f, _s, traced = frameval.eval_watch(prog, trace, 4, watch)
    prog.mem0 = bytearray(prog.mem0)
    prog.mem0[TBL + 0x20] = prog.mem0[TBL + 1]  # an equal byte elsewhere
    prog.prov0 = {CNT + 2: TBL + 0x20}
    _f, _s, fitted = frameval.eval_watch(prog, trace, 4, watch)
    assert fitted != traced and fitted[0][0][2][0] == TBL + 0x20


def test_mutation_keeping_an_origin_across_a_play_write_moves_the_record():
    """Re-seeding a staged cell the play phase overwrote changes what a store reports."""
    a = FG.Asm(INIT)
    a.i("LDA", "abs", TBL + 1).i("STA", "abs", CNT + 2).i("RTS")
    p = FG.Asm(ORG)
    p.i("LDX", "abs", CNT + 1).i("LDA", "absx", TBL).i("STA", "abs", SID + 4)
    p.i("LDA", "abs", CNT + 2).i("STA", "abs", SID + 2)
    p.i("LDA", "imm", 0x77).i("STA", "abs", CNT + 2).i("RTS")
    data = {TBL + k: 3 + k for k in range(4)}
    data.update({CNT + 1: 0, CNT + 2: 0})
    model = _fuzz_model(_player(a, p, data, frames=3, outs=(SID + 2, SID + 4)))
    prog = frameprog.program(model)
    trace, _w = frameprog.iota(model, 3)
    ev = frameval.Evaluator(prog, trace, sources=True)
    ev.frames(1)
    assert any(TBL + 1 in s for s in ev.srcs[0])  # frame 0 still reads what init staged
    ev.frames(1)
    assert not any(TBL + 1 in s for s in ev.srcs[1])  # the play write superseded it
    ev.prov[CNT + 2] = TBL + 1  # the mutation: keep it across the play write
    ev.frames(1)
    assert ev.srcs[2] != ev.srcs[1] and any(TBL + 1 in s for s in ev.srcs[2])


def test_mutation_giving_a_computed_cell_an_origin_moves_the_record():
    """The computed staging cell reports itself; naming a table cell changes the tuple."""
    a = FG.Asm(INIT)
    a.i("LDA", "imm", 4).i("STA", "abs", CNT + 2).i("RTS")
    p = FG.Asm(ORG)
    p.i("LDX", "abs", CNT + 1).i("LDA", "absx", TBL).i("STA", "abs", SID + 4)
    p.i("LDA", "abs", CNT + 2).i("STA", "abs", SID + 2).i("RTS")
    data = {TBL + k: 3 + k for k in range(4)}
    data.update({CNT + 1: 0, CNT + 2: 0})
    model = _fuzz_model(_player(a, p, data, frames=3, outs=(SID + 2, SID + 4)))
    prog = frameprog.program(model)
    trace, _w = frameprog.iota(model, 3)
    assert not prog.prov0
    sound = _sources(prog, trace, 3, 2)
    prog.prov0 = {CNT + 2: TBL + 1}
    assert _sources(prog, trace, 3, 2) != sound


def test_the_evidence_carries_the_tracer_and_the_stack_page_is_never_an_origin():
    """``decompile`` hands the tracer on; jsr/rts stack traffic names no origin."""
    model = _fuzz_model(_staged())
    tr = model.init_copy
    assert isinstance(tr, initcopy.Tracer) and tr.stores >= 1
    assert not any(0x0100 <= c <= 0x01FF for c in tr.cells)
    assert S.trace.__doc__ and model.written  # the play-written set the reduction consumes
