"""Rung (d0), destacking: the slot proven a temporary, the refusal and the law.

``PHA``/``PLA`` at a known ``sp`` is a store and a load at a constant stack-page
cell; where a store dominates every read with no control transfer between, the
slot is a synthetic local, and every control use of the stack refuses.
"""

import numpy as np
import pytest

from deity_informant import datadecl
from deity_informant import frameprog
from deity_informant import frameproc
from deity_informant import framestack
from deity_informant import frameval
import _fuzzgen as G
import test_frameptr as P

from test_frameprog import _fuzz_model

OUT = 0xD404  # sid.v1.ctrl/attack_decay: observable and not a SID lo/hi pair
LO, HI = G.TBL, G.TBL + 1
SUB, SUB2 = 0x1300, 0x1310
LOSLOT, HISLOT = 0x01FD, 0x01FC  # the two slots a two-byte push at reset ``sp`` takes

_PROT_PIN = pytest.mark.xfail(
    reason="stack removal not landed: a refused slot is exactly a page-one access left "
    "in the text, and frameval._protected refuses to evaluate a program that names "
    "$0100..$01FF. These two shapes are the refusals themselves -- the assertions below "
    "state the residue rung (d0) declines to remove, so _check's gate faults at the very "
    "cell they assert. The removal empties both; nothing here weakens the protection.",
    strict=True,
)


def _check(player):
    """``(model, program, text)`` for a player, gate, fixpoint and locals checked.

    The text is gated as well as the trees: a store the renderer drops as unread is
    only sound where nothing reads it, and a machine transfer is a reader."""
    model = _fuzz_model(player)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert frameval.gate_fp(model, 8, prog) is None
    assert frameval.gate_fp(model, 8, frameprog.loads(text)) is None
    assert frameprog.dumps(frameprog.loads(text)) == text
    frameprog.check_locals(prog.procs)
    return model, prog, text


def _build(name, asm, data=(), frames=1):
    return _check(
        G.Player(
            name,
            G.ORG,
            asm.assemble(),
            {OUT, OUT + 1},
            {"indexed"},
            data=dict(data),
            frames=frames,
        )
    )


def _stack(prog, status=None):
    return [p for p in prog.proofs if p.kind == "stack" and status in (None, p.status)]


# ---- the slot the premise proves a temporary --------------------------------------
def test_a_straight_line_spill_becomes_a_local():
    """One ``PHA`` and the ``PLA`` that reads it back: the slot is never memory."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", LO).i("CLC").i("ADC", "imm", 0x10).i("PHA")
    a.i("LDA", "abs", HI).i("STA", "abs", OUT + 1)
    a.i("PLA").i("STA", "abs", OUT).i("RTS")
    _m, prog, text = _build("spill", a, {LO: 0x22, HI: 0x33})
    (pr,) = _stack(prog)
    assert pr.status == "named" and pr.targets == (LOSLOT,)
    assert pr.lemma.endswith("1 store(s), 1 read(s); data temporary, local s0")
    assert "sid.v1.ctrl = (m_1400 + $10)" in text  # the slot's one use spells its value
    assert "m_01FD" not in text  # not a state field, not a cell


def _two_arms():
    """Both arms push a lo/hi pair, a shared tail pops it (Commando $5262-$52AD)."""
    a = G.Asm(G.ORG)
    a.i("INC", "abs", G.CNT).i("LDA", "abs", G.CNT).i("AND", "imm", 0x01)
    a.i("BEQ", "rel", ("L", "down"))
    a.i("CLC").i("LDA", "abs", LO).i("ADC", "imm", 0x05).i("PHA")
    a.i("LDA", "abs", HI).i("ADC", "imm", 0x00).i("PHA").i("JMP", "abs", ("L", "tail"))
    a.label("down")
    a.i("SEC").i("LDA", "abs", LO).i("SBC", "imm", 0x05).i("PHA")
    a.i("LDA", "abs", HI).i("SBC", "imm", 0x00).i("PHA")
    a.label("tail")
    a.i("PLA").i("STA", "abs", OUT + 1).i("PLA").i("STA", "abs", OUT).i("RTS")
    return _build("twoarm", a, {LO: 0x40, HI: 0x02, G.CNT: 0}, frames=4)


def test_a_slot_written_in_both_arms_and_read_in_the_tail_is_one_local():
    """Two definitions and one use is an ordinary local, not two stack cells."""
    _m, prog, text = _two_arms()
    assert sorted(p.targets[0] for p in _stack(prog, "named")) == [HISLOT, LOSLOT]
    assert all(
        p.lemma.endswith("2 store(s), 1 read(s); data temporary, local s%d" % k)
        for k, p in enumerate(_stack(prog))
    )
    defs = [l.strip().split(" = ")[0] for l in text.splitlines() if " = " in l]
    assert defs.count("d0:2") == 2  # the arms keep only the +/- choice (repolish factoring)
    assert "sid.v1.ctrl = trunc1(q0:2)" in text  # the slot pair is one word local
    assert "sid.v1.attack_decay = trunc1((q0:2 >> $08):2)" in text
    assert "s0" not in text and "s1" not in text  # the slot locals inline clean away
    assert "m_01F" not in text


# ---- the control uses of the stack, refused --------------------------------------
@_PROT_PIN
def test_a_call_between_the_push_and_the_pull_refuses():
    """A ``JSR`` moves ``sp`` and returns through the stack: the slot is not private."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", LO).i("PHA").i("JSR", "abs", SUB)
    a.i("PLA").i("STA", "abs", OUT).i("RTS")
    sub = G.Asm(SUB).i("LDA", "abs", HI).i("STA", "abs", OUT + 1).i("RTS").assemble()
    data = {LO: 0x40, HI: 0x02}
    data.update({SUB + k: b for k, b in enumerate(sub)})
    _m, prog, text = _build("jsr_between", a, data)
    (pr,) = _stack(prog)
    assert pr.status == "refused"
    assert pr.lemma.endswith("a read is not dominated by a store of the slot")
    assert "m_01FD = m_1400" in text and "sid.v1.ctrl = m_01FD" in text  # untouched


def test_the_rts_trick_is_the_goto_it_always_was():
    """``PHA``/``PHA``/``RTS`` dispatch: the slot's own value decides what the ret is.

    ``machine_reads`` names the cell pair the ``RTS`` reads, rung (d0r) spells it as
    the goto's operand, rung (d0) proves the procedure's own stores dominate it, and
    the two pushes become locals the value folds away."""
    _m, prog, text = _check(G.t_rts_trick(np.random.default_rng(5)))
    assert [p.targets[0] for p in _stack(prog, "named")] == [HISLOT, LOSLOT]
    assert "goto ($1320)" in text
    assert "m_01FD = " not in text and "m_01FC = " not in text and "sp = " not in text
    (rts,) = [p for p in prog.proofs if p.kind == "rts"]
    assert rts.status == "resolved" and rts.targets == (HISLOT,)
    assert "the procedure's own stores reach it" in rts.lemma
    (sp,) = [p for p in prog.proofs if p.kind == "sp"]
    assert sp.status == "resolved" and "no reader" in sp.lemma


def _rts_dispatch():
    """Two arms push two targets, so no adjacency window spans the pair."""
    a = G.Asm(G.ORG)
    a.i("INC", "abs", G.CNT).i("LDA", "abs", G.CNT).i("AND", "imm", 0x01)
    a.i("BEQ", "rel", ("L", "down"))
    a.i("LDA", "imm", (SUB - 1) >> 8).i("PHA").i("LDA", "imm", (SUB - 1) & 0xFF).i("PHA")
    a.i("JMP", "abs", ("L", "tail"))
    a.label("down")
    a.i("LDA", "imm", (SUB2 - 1) >> 8).i("PHA").i("LDA", "imm", (SUB2 - 1) & 0xFF).i("PHA")
    a.label("tail").i("RTS")
    data = {G.CNT: 0, LO: 0x40}
    for base, out in ((SUB, OUT), (SUB2, OUT + 1)):
        code = G.Asm(base).i("LDA", "abs", LO).i("STA", "abs", out).i("RTS").assemble()
        data.update({base + k: b for k, b in enumerate(code)})
    return _build("rtsdispatch", a, data, frames=4)


def test_a_two_arm_rts_dispatch_is_one_computed_goto():
    """The trick no adjacency window could lift: the pushes are in the branch arms.

    The value question does not care where the stores are, only that they dominate
    the read, so the arms' two definitions are one local with two definitions and the
    dispatch is the goto on it -- no page-one cell and no ``sp`` survive."""
    _m, prog, text = _rts_dispatch()
    (rts,) = [p for p in prog.proofs if p.kind == "rts"]
    assert rts.status == "resolved" and rts.targets == (HISLOT,)
    assert all(p.status == "named" and "2 store(s), 1 read(s)" in p.lemma for p in _stack(prog))
    assert "m_01F" not in text and "sp" not in text[text.index("sub_") :]
    assert "goto ((((zext2(s1) << $08):2 | zext2(s0)):2 + $0001):2)" in text


def test_a_tsx_save_txs_restore_bracket_dissolves():
    """The context-save idiom: sp spilled, moved, restored -- all of it fabric.

    The bracket cell is saved once and read only to restore, the symbolic walk
    returns to the entry state whatever ran between, and the datum that rode
    the stack arrives as the one word it was (docs/frameprog.md 7.9)."""
    a = G.Asm(G.ORG)
    a.i("TSX").i("STX", "abs", G.CNT + 0x10)
    a.i("LDA", "abs", LO).i("PHA")
    a.i("LDA", "abs", HI).i("PHA")
    a.i("PLA").i("STA", "abs", OUT + 1)
    a.i("PLA").i("STA", "abs", OUT)
    a.i("LDX", "abs", G.CNT + 0x10).i("TXS").i("RTS")
    _m, prog, text = _build("spsave", a, {LO: 0x40, HI: 0x02})
    body = text[text.index("sub_") :]
    assert "sp" not in body and "m_01" not in body
    assert "sid.v1.ctrl = m_1400" in body and "sid.v1.attack_decay = m_1401" in body
    (sp,) = [p for p in prog.proofs if p.kind == "sp"]
    assert sp.status == "resolved"


def test_a_txs_stack_switch_with_restore_dissolves():
    """A constant TXS opens a new stack; restored before ret, it is still fabric."""
    a = G.Asm(G.ORG)
    a.i("TSX").i("STX", "abs", G.CNT + 0x10)
    a.i("LDX", "imm", 0x80).i("TXS")
    a.i("LDA", "abs", LO).i("PHA")
    a.i("LDA", "abs", HI).i("PHA")
    a.i("PLA").i("STA", "abs", OUT + 1)
    a.i("PLA").i("STA", "abs", OUT)
    a.i("LDX", "abs", G.CNT + 0x10).i("TXS").i("RTS")
    _m, prog, text = _build("spswitch", a, {LO: 0x41, HI: 0x03})
    body = text[text.index("sub_") :]
    assert "sp" not in body and "m_01" not in body and "m_0080" not in body
    assert "sid.v1.ctrl = m_1400" in body and "sid.v1.attack_decay = m_1401" in body
    (sp,) = [p for p in prog.proofs if p.kind == "sp"]
    assert sp.status == "resolved"


# ---- the premise, stated ----------------------------------------------------------
CELL = 0x01FD


def _store(val=("const", 7, 1)):
    return ("st", ("const", CELL, 2), val)


def _read(name="w0"):
    return ("asg", name, ("mem", ("const", CELL, 2), 1))


def _indexed(base, name="x"):
    return ("op", "INT_ADD", (("const", base, 2), ("op", "INT_ZEXT", (("loc", name),), 2)), 2)


def _clean():
    return [_store(), _read()]


def _read_first():
    return [_read(), _store()]


def _loop_between():
    """The landed rule: a region between the two ends carries the definition."""
    return [_store(), ("loop", [("asg", "t1", ("const", 0, 1)), ("brk", None)]), _read()]


def _call_between():
    """The machine's own push is the one page-one write no operand names."""
    return [_store(), ("call", 0x2000, 0x1234), _read()]


def _label_between():
    """A join the list does not enumerate: a ``goto`` elsewhere may reach it."""
    return [_store(), ("label", 0x1234), _read()]


def _one_arm_only():
    return [("if", 0, ("loc", "cflag"), [_store()], []), _read()]


def _stack_peek():
    return [_store(), ("st", _indexed(0x0100), ("const", 0, 1)), _read()]


def _blind_store():
    return [_store(), ("st", ("loc", "t0", 2), ("const", 0, 1)), _read()]


def _blind_store_before():
    return [("st", ("loc", "t0", 2), ("const", 0, 1))] + _clean()


def _blind_load():
    return [_store(), ("asg", "t1", ("mem", ("loc", "t0", 2), 1)), _read()]


def _word_store():
    return [("st", ("const", CELL, 2), ("mem", ("const", 0x1400, 2), 2)), _read()]


@pytest.mark.parametrize(
    "build,want",
    [
        (_clean, None),
        (_read_first, "a read is not dominated by a store of the slot"),
        (_loop_between, None),
        (_call_between, "a read is not dominated by a store of the slot"),
        (_label_between, "a read is not dominated by a store of the slot"),
        (_one_arm_only, "a read is not dominated by a store of the slot"),
        (_stack_peek, "another resolvable access may touch the slot"),
        (_blind_store, "an unresolvable store may alias the live slot"),
        (_blind_store_before, None),
        (_blind_load, None),  # a load moves no value; page one is protected at evaluation
        (_word_store, "another resolvable access may touch the slot"),
    ],
)
def test_the_refusal_diagnostic_names_the_premise_that_failed(build, want):
    slot = framestack._Slot(CELL).run(build(), set())
    assert slot.why == want
    pr = slot.proof("s0")
    assert pr.kind == "stack" and pr.status == ("named" if want is None else "refused")
    assert pr.targets == (CELL,) and pr.lemma.endswith(want or "data temporary, local s0")


def test_a_slot_a_second_procedure_may_touch_is_not_private():
    """The rewrite is per procedure, so a slot two of them address stays memory."""
    slot = framestack._Slot(CELL).run(_clean(), {CELL})
    assert slot.why == "another procedure may touch the slot"


def test_the_synthetic_names_collide_with_nothing_the_procedures_bind():
    """``s0`` is taken by the player, so the slot takes the next free ``s<k>``."""
    procs = [(0x1000, [], [], [("asg", "s0", ("const", 1, 1))] + _clean())]
    (pr,) = framestack.apply_rung(procs)
    assert pr.status == "named" and pr.lemma.endswith("local s1")
    assert procs[0][3][1:] == [("asg", "s1", ("const", 7, 1)), ("asg", "w0", ("loc", "s1"))]


# ---- the read the rewrite used to walk past ---------------------------------------
def test_a_slot_read_inside_another_address_becomes_the_local_too():
    """``m_CA02[(m_01FA & $07)]`` is a read: the store may not go without it.

    ``_count`` walks into a ``mem`` address and scored this read, so the slot
    was named while the rewrite left the load behind and ``drop_state`` deleted
    the cell under it -- 720_Degrees' Class B divergence (frameprog 7.10.14)."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", LO).i("AND", "imm", 0x03).i("PHA")
    a.i("LDX", "abs", LOSLOT).i("LDA", "absx", G.TBL + 4).i("STA", "abs", OUT + 1)
    a.i("PLA").i("ORA", "imm", 0x20).i("STA", "abs", OUT).i("RTS")
    data = {LO: 0x02, HI: 0x33}
    data.update({G.TBL + 4 + k: 0x50 + k for k in range(4)})
    _m, prog, text = _build("nested", a, data)
    (pr,) = _stack(prog)
    assert pr.status == "named" and pr.lemma.endswith(
        "1 store(s), 2 read(s); data temporary, local s0"
    )
    assert "m_01FD" not in text  # the cell is gone, so no read of it may survive
    assert "s0" in text


# ---- rung (d0s): the slot named through sp ---------------------------------------
SPN = "sp"
_spaddr = frameproc.sp_addr  # the ONE spelling of a page-one address through ``sp``


def _spstore(k=0, val=("const", 7, 1)):
    return ("st", _spaddr(k), val)


def _spread(k=0, name="w0"):
    return ("asg", name, ("mem", _spaddr(k), 1))


def _spmove(d):
    return ("asg", SPN, ("op", "INT_ADD", (("loc", SPN), ("const", d & 0xFF, 1)), 1))


def _run_spslots(stmts, balanced=True):
    """Every candidate slot of the list, walked, in the rung's own order."""
    marks = framestack._Marks()
    framestack._sp_scan(stmts, marks, SPN)
    keys = sorted(framestack._sp_candidates(stmts, marks, SPN))
    return [framestack._SpSlot(k).run(stmts, marks, SPN, balanced) for k in keys]


def _sp_clean():
    return [_spstore(), _spmove(-1), _spread(1), _spmove(1)]


def _sp_read_first():
    return [_spread(0), _spstore()]


def _sp_control_between():
    return [_spstore(), _spmove(-1), ("label", 0x1234), _spread(1), _spmove(1)]


def _sp_page_peek():
    return [_spstore(), ("st", ("const", 0x01F0, 2), ("const", 0, 1)), _spmove(-1), _spread(1)]


def _sp_blind_store():
    return [_spstore(), ("st", ("loc", "t0", 2), ("const", 0, 1)), _spmove(-1), _spread(1)]


def _sp_word_store():
    return [_spstore(), ("st", _spaddr(0), ("mem", ("const", 0x1400, 2), 2)), _spread(0)]


def _sp_other_slot():
    return [_spstore(), _spstore(0xFF, ("const", 9, 1)), _spmove(-1), _spread(1)]


@pytest.mark.parametrize(
    "build,want",
    [
        (_sp_clean, None),
        (_sp_other_slot, None),
        (_sp_read_first, "a read is not dominated by a store of the slot"),
        (_sp_control_between, "the slot is not both stored and read in the procedure"),
        (_sp_page_peek, "another resolvable access may touch the slot"),
        (_sp_blind_store, "an unresolvable address may alias the live slot"),
        (_sp_word_store, "another resolvable access may touch the slot"),
    ],
)
def test_the_sp_slot_diagnostic_names_the_premise_that_failed(build, want):
    slots = _run_spslots(build())
    assert slots and slots[0].why == want
    pr = slots[0].proof("s0")
    assert pr.kind == "spslot" and pr.status == ("named" if want is None else "refused")
    assert pr.targets == () and pr.lemma.endswith(want or "data temporary, local s0")


def test_an_unbalanced_procedure_keeps_its_sp_slot():
    """The ret reads page one for its target where sp moved, so the store stays."""
    (slot,) = _run_spslots(_sp_clean(), balanced=False)
    assert slot.why == "the procedure's stack effect is not zero"


def test_a_store_above_the_live_stack_top_is_no_candidate():
    """``sp + 1`` is the caller's live stack -- the return address, not a spill."""
    stmts = [_spstore(1), _spread(1)]
    marks = framestack._Marks()
    framestack._sp_scan(stmts, marks, SPN)
    assert not framestack._sp_candidates(stmts, marks, SPN)


def test_two_arms_that_push_and_a_tail_that_pulls_are_one_sp_local():
    """The sp state joins where both arms leave it equal, so the tail reads one slot."""
    arm = [_spstore(0, ("loc", "a")), _spmove(-1)]
    stmts = [("if", "if", ("loc", "cflag"), list(arm), list(arm)), _spread(1), _spmove(1)]
    (slot,) = _run_spslots(stmts)
    assert slot.why is None and (slot.stores, slot.reads) == (2, 1)


def test_arms_that_leave_sp_at_different_depths_share_no_slot():
    """One arm pushes and the other does not: the tail names neither arm's cell."""
    stmts = [
        ("if", "if", ("loc", "cflag"), [_spstore(0, ("loc", "a")), _spmove(-1)], []),
        _spread(1),
    ]
    for slot in _run_spslots(stmts):
        assert slot.why == "the slot is not both stored and read in the procedure"


# ---- the slot identity: what a reader may take, and where a call stands ------------
def _pcall(entry=SUB):
    return ("pcall", entry, [], [])


def _sp_blind_read():
    return [_spstore(), _spmove(-1), ("asg", "a", ("mem", ("loc", "t0", 2), 1)), _spread(1)]


def _sp_page_read():
    return [_spstore(), _spmove(-1), ("asg", "a", ("mem", ("const", 0x01F0, 2), 1)), _spread(1)]


@pytest.mark.parametrize("build", [_sp_blind_read, _sp_page_read])
def test_a_reader_holds_the_slot_and_refuses_only_its_store(build):
    """A read moves no value: the identity stands and the cell stays behind it.

    #189's correction is the other half -- a *writer* is what refuses the slot, and
    the machine's own return-address push is one, so it is priced at the call."""
    (slot,) = _run_spslots(build())
    assert slot.why is None and slot.status() == "held"
    assert slot.proof("s0").lemma.endswith("value held in s0, store kept -- %s" % slot.impure)


def test_no_slot_is_live_across_a_call_however_it_stands():
    """The machine's own return-address push is priced by refusing to span a call.

    A callee that names no page-one cell of its own was measured as a spanning premise
    and refused on the corpus (see the decision log), so the epoch still closes here."""
    stmts = [_spstore(), _spmove(-1), _pcall(), _spread(1), _spmove(1)]
    for slot in _run_spslots(stmts):
        assert slot.why == "the slot is not both stored and read in the procedure"


# ---- a label no transfer names is not a point control enters ----------------------
def _proc(stmts, entry=SUB):
    return [(entry, [SPN], [], stmts)]


def _sentinel(interior):
    """Automatas minimised: a spill, a loop carrying ``interior``, and the pull.

    The displacement inside the loop is -1, so anything the walk reads as an entry
    there refuses the balance and ``sp`` survives the whole rung."""
    return [
        _spstore(0, ("loc", "a")),
        _spmove(-1),
        ("loop", list(interior) + [("brk", None)]),
        _spread(1),
        _spmove(1),
    ]


def _balanced(procs):
    sp = frameproc._SP
    saves = {e: framestack._saves(st, sp) for e, _pa, _r, st in procs}
    return framestack._balances(procs, sp, saves)[0]


def test_an_unreferenced_interior_label_is_dropped_and_the_loop_balances():
    """The Automatas sentinel: three dispatch pcs, and no goto or arm names one."""
    procs = _proc(_sentinel([("label", 0x10B8), ("st", ("const", OUT, 2), ("loc", "a"))]))
    assert frameproc.entered_pcs(procs) == {SUB}
    assert not _balanced(procs)
    assert frameproc.drop_dead_labels(procs)
    assert not [s for s in framestack.FF.stmts_of(procs[0][3]) if s[0] == "label"]
    assert _balanced(procs) == {SUB: True}


@pytest.mark.parametrize(
    "namer",
    [
        lambda pc: ("goto", pc),
        lambda pc: ("swg", [("$%04X" % pc, [("brk", None)])]),
        lambda pc: ("swc", ["$%04X" % pc], []),
        lambda pc: ("call", pc, 0x1234),
    ],
    ids=["goto", "swg-arm", "swc-label", "call"],
)
def test_a_label_a_transfer_does_name_is_kept_and_the_balance_still_refuses(namer):
    """The negative: an enumerated entry is an entry, whatever spells it."""
    procs = _proc(_sentinel([("label", 0x10B8)]) + [namer(0x10B8)])
    assert 0x10B8 in frameproc.entered_pcs(procs)
    assert not frameproc.drop_dead_labels(procs)
    assert not _balanced(procs)


def test_a_computed_transfer_with_no_arm_table_refuses_the_whole_reading():
    """A dispatch no list enumerates may land on any label, so none is dropped."""
    procs = _proc(_sentinel([("label", 0x10B8)]) + [("dgoto", ("mem", ("const", 2, 2), 2))])
    assert frameproc.entered_pcs(procs) is None
    assert not frameproc.drop_dead_labels(procs)


def test_a_computed_transfer_beside_its_arm_table_lands_on_the_arms():
    """``dgoto``/``swg`` is the enumerated dispatch: the arms, and nothing else."""
    arms = ("swg", [("$10B8", [("brk", None)])])
    procs = _proc(_sentinel([("label", 0x10B8), ("label", 0x10BF)]) + [("dgoto", None), arms])
    assert frameproc.entered_pcs(procs) == {SUB, 0x10B8}
    assert frameproc.drop_dead_labels(procs)
    assert [s[1] for s in framestack.FF.stmts_of(procs[0][3]) if s[0] == "label"] == [0x10B8]


def test_a_landing_no_transfer_spells_keeps_its_label():
    """An RTS-trick landing is entered by a manufactured return, not by a transfer."""
    procs = _proc(_sentinel([("label", 0x10B8)]))
    assert not frameproc.drop_dead_labels(procs, entered=(0x10B8,))
    assert frameproc.drop_dead_labels(procs)


def test_a_foreign_goto_is_a_transfer_the_reading_carries():
    """The procedures are read together, so another one's goto names the label."""
    procs = _proc(_sentinel([("label", 0x10B8)])) + [(SUB2, [], [], [("goto", 0x10B8)])]
    assert not frameproc.drop_dead_labels(procs)


# ---- the fault is the bottom of the displacement lattice --------------------------
def test_an_unobserved_arm_carries_no_displacement_and_the_balance_holds():
    """Reaching ``unobserved`` is a fault (``frameval._s_unobs``): nothing continues.

    Blueprint's shape -- a guard between the push and the pull -- so the walk that
    reads the arm as an edge standing at the entry refuses a balance that holds."""
    guard = ("if", "if", ("loc", "cflag"), [("unobs", 0x847A)], [])
    assert _balanced(_proc(_sentinel([guard]))) == {SUB: True}
    assert _balanced(_proc([_spstore(), _spmove(-1), guard, _spread(1), _spmove(1)])) == {SUB: True}


def test_a_procedure_no_path_leaves_stands_where_it_entered():
    """Every path faults, so no edge states a displacement and the balance is vacuous."""
    assert _balanced(_proc([_spmove(-1), ("unobs", 0x847A)])) == {SUB: True}


_GUARD = ("if", "if", ("loc", "cflag"), [("unobs", 0x847A)], [])


def test_a_faulted_arm_opens_no_epoch_so_the_pull_names_its_own_push():
    """The same bottom, read by the epoch walk: a guard rejoins nothing, so it ends
    no epoch and the pull behind it is a read of the slot the push made."""
    (slot,) = _run_spslots([_spstore(), _spmove(-1), _GUARD, _spread(1), _spmove(1)])
    assert slot.why is None and (slot.stores, slot.reads) == (1, 1)


def test_a_pull_the_epoch_walk_loses_still_holds_its_own_push():
    """Arcade_Hustlers' ``$01F9``: two spills at one displacement, the second's pull
    behind a guard. An epoch the fault opened hid that pull, and the slot the two
    stores shared read as stored-and-read on the first pull alone -- so the rewrite
    dropped a store whose reader stayed, and the text read a cell it never wrote."""
    stmts = [_spstore(), _spmove(-1), _spread(1), _spmove(1)]
    stmts += [_spstore(), _spmove(-1), _GUARD, _spread(1), _spmove(1)]
    (slot,) = _run_spslots(stmts)
    assert slot.why is None and (slot.stores, slot.reads) == (2, 2)


def test_a_real_edge_at_a_displacement_still_refuses_the_balance():
    """The negative: a ``goto`` is a transfer control takes, and it must stand."""
    assert not _balanced(_proc([_spstore(), _spmove(-1), ("goto", 0x10B8), _spread(1)]))


# ---- the same bottom in the splice's own reading (``frameproc.sp_balanced``) ------
def test_an_arm_that_returns_carries_its_displacement_to_no_fall_through():
    """Both paths pull what the push made; the arm that leaves by ``ret`` reaches
    the tail's ``ret`` on no edge, so its displacement joins nothing there."""
    arm = [_spmove(1), ("ret", False)]
    body = [_spstore(), _spmove(-1), ("if", "if", ("loc", "cflag"), arm, [])]
    assert frameproc.sp_balanced(body + [_spmove(1), ("ret", False)])


def test_a_levelled_break_lands_past_the_loop_it_counts_out_of():
    """Atmosphere_II's shape: the pull is in a nested loop a ``break 2`` leaves, so
    the loop's exit is where its breaks stand and never its body's fall-through."""
    inner = [
        ("if", "if", ("loc", "cflag"), [("brk",)], []),
        _spmove(1),
        ("brk", 2),
    ]
    body = [("loop", [_spstore(), _spmove(-1), ("loop", inner), _spmove(1), ("brk",)])]
    assert frameproc.sp_balanced(body + [("ret", False)])


def test_a_break_that_leaves_the_loop_at_another_depth_still_refuses():
    """The negative: two exits at two displacements name no one state past the loop."""
    inner = [("if", "if", ("loc", "cflag"), [("brk",)], []), _spmove(1), ("brk",)]
    body = [("loop", [_spstore(), _spmove(-1), ("loop", inner), ("brk",)])]
    assert not frameproc.sp_balanced(body + [("ret", False)])


# ---- rung (d0'): the drop moves every pushed return word, threaded or not ---------
def _page_datum(stmts):
    """Blueprint minimised: a page-one datum written, spanned by a call, read back."""
    return _proc([_store(("loc", "a"))] + list(stmts) + [_read()]) + [(SUB2, [], [], [])]


def _sp_proofs(procs):
    return sorted(p.lemma.split(":", 1)[0] for p in framestack.drop_sp(procs, SUB))


def test_a_text_threaded_call_at_a_displacement_keeps_the_stack_pointer():
    """``frameval.run_frame`` pushes a return word at a ``pcall`` too, so the drop
    moves it onto the datum the artifact parked below the stack top."""
    procs = _page_datum([_spmove(-1), _pcall(SUB2), _spmove(1)])
    assert _sp_proofs(procs) == ["sp_linked"]
    assert framestack.SP_CLASSES["sp_linked"].startswith("a surviving call")


def test_a_text_threaded_call_at_the_entry_displacement_drops_its_linkage():
    """The positive: the push does not move, so the datum stands and ``sp`` goes."""
    procs = _page_datum([_pcall(SUB2)])
    assert _sp_proofs(procs) == ["sp"]
    assert not [s for s in framestack.FF.stmts_of(procs[0][3]) if s[0] == "asg" and s[1] == SPN]


def test_a_displaced_call_over_no_page_one_datum_still_drops_its_linkage():
    """The other disjunct: nothing surviving names page one, so nothing moved."""
    procs = _proc([_spmove(-1), _pcall(SUB2), _spmove(1)]) + [(SUB2, [], [], [])]
    assert _sp_proofs(procs) == ["sp"]


# ---- the held store, re-asked where rung (f) has an answer ------------------------
def _ptr_procs(extra=()):
    """A pointer reloaded from a declared lo/hi pair and dereferenced at a row."""
    stmts = [
        ("asg", "y", ("mem", ("const", 0x1600, 2), 1)),
        P._st(P.PTR, P._load(0x1500, 0x1502, ("loc", "y"))),
        *extra,
        ("st", ("const", OUT, 2), ("mem", P._deref(P.PTR, ("loc", "y")), 1)),
    ]
    return [(SUB, [], [], stmts)], P._pair(0x1500, 0x1502, 2)


def _defs_at(stmts, i=0):
    return frameproc.DefsAt(frameproc.Defs(stmts, None, False), i)


def test_a_deref_rung_f_bounds_names_its_target_words_and_leaves_the_page():
    """The ordering gap: the pointer's value set is rung (f)'s and needs rung (d).

    Unbounded, the address may be any cell and the aliasing question must refuse;
    bounded, the two declared blocks are where it lands and page one is not one."""
    procs, decls = _ptr_procs()
    addr = P._deref(P.PTR, ("loc", "y"))
    bounds = framestack.deref_bounds(P._mem0(P._TAB), decls, procs)
    assert bounds[addr] == ((0x0000, 0x1400, 0x1440), 0xFF)  # the rows, the image's own
    at = _defs_at(procs[0][3])
    assert not framestack._off_page(addr, 1, at, None, None)
    assert framestack._off_page(addr, 1, at, None, bounds)


def test_a_deref_whose_targets_reach_page_one_is_not_off_the_page():
    """The negative: the bound is read, not assumed -- a word in page one counts."""
    procs, decls = _ptr_procs()
    addr = P._deref(P.PTR, ("loc", "y"))
    cells = {**P._TAB, P.PTR: 0xF0, P.PTR + 1: 0x01}  # the image's own word is $01F0
    bounds = framestack.deref_bounds(P._mem0(cells), decls, procs)
    assert bounds[addr] == ((0x01F0, 0x1400, 0x1440), 0xFF)
    assert not framestack._off_page(addr, 1, _defs_at(procs[0][3]), None, bounds)


def _held(tail):
    """Automatas minimised: the value parked below the live top, and ``tail`` after."""
    return _proc([("st", _spaddr(0), ("loc", "a"))] + list(tail))


SP_UNBALANCED = framestack.SP_CLASSES["sp_unbalanced"]


def _spill_lemma(procs, bounds=None):
    (pr,) = framestack.drop_spills(procs, SUB, None, bounds)
    return pr.status, pr.lemma


def test_a_held_store_no_read_may_name_is_dead():
    """Page one is the machine's, so a store the artifact cannot read back is not one."""
    procs = _held([("st", ("const", OUT, 2), ("loc", "a")), ("ret", None)])
    assert _spill_lemma(procs) == (
        "resolved",
        "spill: 1 held store(s) dropped; " "page one has no reader",
    )
    assert [s[0] for s in procs[0][3]] == ["st", "ret"]


@pytest.mark.parametrize(
    "tail,want",
    [
        ([_spread(0)], "no entry-epoch spill store"),
        ([("asg", "w0", ("mem", ("const", 0x01F0, 2), 1))], "no entry-epoch spill store"),
        ([("asg", "w0", ("mem", ("loc", "t0", 2), 1))], "no entry-epoch spill store"),
        ([("st", ("const", 0x01F0, 2), ("const", 0, 1))], "no entry-epoch spill store"),
        ([("igoto", 0x01F5, None)], "the machine names a page-one cell of its own"),
        ([_spmove(-1)], SP_UNBALANCED),
    ],
    ids=["slot-read", "page-read", "blind-read", "foreign-store", "machine-read", "unbalanced"],
)
def test_a_held_store_a_reader_may_name_stays(tail, want):
    """Each refusal names the reader: the store is only dead where none can see it."""
    status, lemma = _spill_lemma(_held(list(tail) + [("ret", None)]))
    assert status == "refused" and want in lemma


def test_a_push_a_loop_carries_is_not_an_entry_slot():
    """The RTS trick (``rts-trick:loop``): the push a loop body carries stands at no
    displacement the entry names, so the ``ret`` reading it back is not excluded."""
    procs = _proc([("loop", [("st", _spaddr(0), ("loc", "a")), _spmove(-1), ("brk", None)])])
    assert _spill_lemma(procs) == (
        "refused",
        "spill: a page-one access is no entry-epoch " "spill store",
    )


def test_the_bound_is_what_releases_the_blind_reader():
    """The whole ordering point: the same text, refused unbounded and dead bounded."""
    blind = ("mem", ("loc", "t0", 2), 1)
    tail = [("st", ("const", OUT, 2), blind), ("ret", None)]
    assert _spill_lemma(_held(tail))[0] == "refused"
    assert _spill_lemma(_held(tail), {blind[1]: ((0x1400,), 0)})[0] == "resolved"


# ---- rung (d1): scratch leaves the state (docs/denotation-solve.md 9.1) -----------
SCELL = 0x13CF  # Grid_Runner's first row-fetch latch: written and read in one frame
_SFIELD = ("m_13CF", 1, False, [])


def _sstore(val=("const", 7, 1), cell=SCELL):
    return ("st", ("const", cell, 2), val)


def _sread(name="w0", cell=SCELL):
    return ("asg", name, ("mem", ("const", cell, 2), 1))


def _sproc(stmts):
    return [(SUB, [], [], list(stmts))]


def _classify(stmts, state=(_SFIELD,), reach=(), regions=None):
    """``(state after the rung, the one proof it wrote)``."""
    procs = _sproc(stmts)
    cells = framestack.state_cells(list(state), {}, frameproc.G.addr_name)
    proofs = framestack.apply_scratch(procs, cells, regions, None, reach)
    return framestack.drop_scratch(list(state), proofs, {}, frameproc.G.addr_name), proofs, procs


def test_a_cell_every_read_of_which_a_same_frame_store_dominates_leaves_the_state():
    """The wire: the store is an assignment, the read a local, the field is gone."""
    state, proofs, procs = _classify([_sstore(), _sread()])
    assert state == []
    assert [(p.kind, p.status) for p in proofs] == [("scratch", "named")]
    assert procs[0][3][0][0] == "asg" and procs[0][3][1][2][0] == "loc"


def _first_frame_read():
    return [_sread(), _sstore()]


def _loop_reads_first():
    return [("loop", [_sread(), _sstore(), ("brk", None)])]


def _one_arm_stores():
    return [("if", "if", ("loc", "cflag"), [_sstore()], []), _sread()]


def _indexed_neighbour():
    return [_sstore(), ("st", _indexed(SCELL - 4), ("const", 0, 1)), _sread()]


def _never_read():
    return [_sstore()]


def _blind_store_while_live():
    return [_sstore(), ("st", ("loc", "t0", 2), ("const", 0, 1)), _sread()]


@pytest.mark.parametrize(
    "build,want",
    [
        (_first_frame_read, "a read is not dominated by a store of the slot"),
        (_loop_reads_first, "a read is not dominated by a store of the slot"),
        (_one_arm_stores, "a read is not dominated by a store of the slot"),
        (_indexed_neighbour, "another resolvable access may touch the slot"),
        (_blind_store_while_live, "an unresolvable store may alias the live slot"),
        (_never_read, "the slot is not both stored and read in the procedure"),
    ],
)
def test_the_scratch_refusal_names_the_premise_that_failed(build, want):
    """Each refusal keeps the field: 9.1's persistent and open-access classes."""
    state, proofs, _procs = _classify(build())
    assert state == [_SFIELD] and proofs[0].status == "refused"
    assert proofs[0].lemma.endswith(want)


BLIND = ("loc", "t0", 2)


def test_a_deref_rung_f_bounds_onto_the_cell_is_an_access_that_touches_it():
    """The span rule over a base-less address: bounded away it is no reader, bounded
    onto the cell it is the resolvable access the walk refuses (9.1's open class)."""
    stmts = [_sstore(), ("asg", "w0", ("mem", BLIND, 1)), _sread()]
    cells = framestack.state_cells([_SFIELD], {}, frameproc.G.addr_name)
    (hit,) = framestack.apply_scratch(_sproc(stmts), cells, None, {BLIND: ((SCELL,), 0)})
    assert hit.status == "refused"
    assert hit.lemma.endswith("another resolvable access may touch the slot")
    (miss,) = framestack.apply_scratch(_sproc(stmts), cells, None, {BLIND: ((0x1400,), 0)})
    assert miss.status == "named"


def test_a_read_the_next_frame_makes_the_cell_state_not_scratch():
    """The frame is the procedure, so the first-trip read is last frame's value."""
    state, _p, _procs = _classify([("loop", [_sread(), _sstore(), ("cont", None)])])
    assert state == [_SFIELD]


def test_a_declared_array_and_a_dispatch_cell_are_no_candidates():
    """The web of an indexed field splits per offset, and a dispatch cell is control."""
    arr, disp = ("m_13CF", 1, True, []), ("m_13CF", 1, False, [0x69, 0xE9])
    for f in (arr, disp):
        assert framestack.state_cells([f], {}, frameproc.G.addr_name) == {}


def test_a_cell_a_second_procedure_may_touch_stays_state():
    """Privacy is the premise: the other procedure's footprint refuses the cell."""
    procs = _sproc([_sstore(), _sread()]) + [(SUB2, [], [], [_sread("w1")])]
    cells = framestack.state_cells([_SFIELD], {}, frameproc.G.addr_name)
    proofs = framestack.apply_scratch(procs, cells)
    assert [p.status for p in proofs] == ["refused"]
    assert proofs[0].lemma.endswith("another procedure may touch the slot")


def test_a_state_cell_no_store_may_reach_is_a_constant():
    """Grid_Runner's ``m_040B``/``m_0414``/``m_045D``: initialised, never written."""
    regions = datadecl.Regions(())
    procs = _sproc([_sread(), _sstore(cell=0x1400)])
    assert framestack.unwritten(procs, {SCELL, 0x1400}, regions) == {SCELL}


def test_an_indexed_store_that_may_reach_the_cell_is_a_write():
    """``unwritten`` asks ``Defs._hits``, so a span that covers the cell counts."""
    regions = datadecl.Regions(())
    procs = _sproc([("st", _indexed(SCELL - 4), ("const", 0, 1))])
    assert framestack.unwritten(procs, {SCELL}, regions) == set()


def _demote(stmts, state=(_SFIELD,)):
    """``(state after the demotion, the declarations it carved)``."""
    decls, mem0 = [], bytearray(0x10000)
    mem0[SCELL] = 0x2A
    state = frameprog._declare_constants(
        decls, _sproc(stmts), list(state), {}, datadecl.Regions(()), mem0, set(), set()
    )
    return state, decls


def test_a_state_cell_no_store_reaches_declares_as_data_and_not_as_a_role():
    """Grid_Runner's ``$040B``/``$0414``/``$045D``: a constant is not an update shape."""
    state, decls = _demote([_sread()])
    assert state == []
    assert [(d["kind"], d["base"], d["size"], d["data"], d["role"]) for d in decls] == [
        ("table", SCELL, 1, b"\x2a", None)
    ]


def test_a_state_cell_a_store_reaches_stays_declared_as_state():
    """The demotion is ``unwritten``'s verdict, so one store holds the field."""
    assert _demote([_sread(), _sstore()]) == ([_SFIELD], [])


# ---- the read set an index carries (docs/denotation-solve.md 9.2) -----------------
def test_an_access_naming_one_address_is_the_exact_load_and_no_wider_reader():
    """``read_reach``: a plain load is the read the rewrite redirects, so it is not one."""
    assert framestack.read_reach({(0x15AC, SCELL), (0x15E9, SCELL)}) == set()


def test_an_access_seen_at_two_addresses_reads_every_one_of_them():
    """The blit and the overrunning table row: one site, every cell it was seen at."""
    blit = {(0x10A9, c) for c in range(0x13BA, SCELL + 1)}
    assert framestack.read_reach(blit) == {c for c in range(0x13BA, SCELL + 1)}


def _carved(base, size):
    return datadecl.Regions(({"base": base, "size": size},))


def test_a_table_row_the_index_overruns_onto_the_cell_holds_its_store():
    """Puke's ``$171F``: three stores, a dominated read, and ``m_16A7[a]`` reaching it.

    The carve bounds the index to its own declaration and the row leaves it, so the
    span rule alone names a wire the machine still reads (9.2's Gate FP verdict)."""
    row = ("asg", "w1", ("mem", _indexed(SCELL - 0x78, "a"), 1))
    stmts, regions = [_sstore(), _sread(), row], _carved(SCELL - 0x78, 8)
    state, proofs, _procs = _classify(stmts, regions=regions)
    assert state == [] and proofs[0].status == "named"
    state, proofs, procs = _classify(stmts, reach={SCELL}, regions=regions)
    assert state == [_SFIELD] and proofs[0].status == "refused"
    assert proofs[0].lemma.endswith("an indexed access was seen to name the slot")
    assert procs[0][3][0] == _sstore()


def test_a_covering_blit_is_a_read_of_every_latch_it_covers():
    """Grid_Runner's ``sid.reg[x] = m_13BA[x]``: the four latches are read, not dead."""
    blit = ("st", _indexed(0xD400), ("mem", _indexed(0x13BA), 1))
    stmts, regions = [_sstore(), _sread(), blit], _carved(0x13BA, 1)
    state, proofs, _procs = _classify(stmts, reach={SCELL}, regions=regions)
    assert state == [_SFIELD] and proofs[0].status == "refused"
    assert proofs[0].lemma.endswith("an indexed access was seen to name the slot")


def test_an_unresolvable_read_may_name_the_cell_and_holds_it():
    """9.1's open class: a deref rung (f) does not bound reaches every private cell."""
    stmts = [_sstore(), ("asg", "w0", ("mem", BLIND, 1)), _sread()]
    state, proofs, _procs = _classify(stmts)
    assert state == [_SFIELD] and proofs[0].status == "refused"
    assert proofs[0].lemma.endswith("an unresolvable read may name the slot")
