"""The two refusals docs/denotation-solve.md 8.4 names in advance, settled.

Class 1: a non-tail self-call carries a stack residue exactly when the value it
holds across its own call is not re-derivable from restored state AND page one is
its only store. Class 2: the target set's closure decides nothing.
"""

import re

import pytest

import _callgen as G
import _stackgen as K
from deity_informant import frameval

CLEAN = (
    ("deep-recursion", "const/after"),
    ("deep-recursion", "const/around"),
    ("deep-recursion", "row/after"),
    ("deep-recursion", "row/around"),
    ("mutual-recursion", "const/a"),
    ("mutual-recursion", "const/b"),
    ("mutual-recursion", "row/a"),
    ("mutual-recursion", "row/b"),
    ("saved-recursion", "zp/const"),
    ("saved-recursion", "zp/row"),
    ("input-recursion", "1/after"),
    ("input-recursion", "1/around"),
    ("input-recursion", "3/after"),
    ("input-recursion", "3/around"),
    ("carry-recursion", "volatile/zp"),
    ("carry-recursion", "overwrite/zp"),
    ("carry-recursion", "lossy/zp"),
    ("carry-recursion", "multi/zp"),
)
CONTROL = (("saved-recursion", "pha/const"), ("saved-recursion", "pha/row"))
RESIDUE = tuple(
    ("carry-recursion", "%s/pha" % w) for w in ("volatile", "overwrite", "lossy", "multi")
)

OPEN = ("closed", "partial", "input", "wide", "computed", "roundtrip")
ARMS = {"closed": 4, "partial": 2, "input": 2, "wide": 4, "computed": 2, "roundtrip": 2}

_PUSH = re.compile(r"^\s*mem\[\(\$0100.*?\):2\] = (.+)$", re.M)
_RESTORED = re.compile(r"^m_[0-9A-F]{4}\[ctr_00F5\]$")

M_CARRIED_VALUE = (
    "a recursive routine carrying a value across its own call that restored state "
    "cannot re-derive, with page one as its only store: the byte is `mem[$0100 | sp]`, "
    "sp becomes a parameter and a return, and the descent writes the SID so no sound "
    "removal may re-run it to recompute the byte. The removal would have to declare an "
    "array the image does not contain, which is machinery added; refuse it"
)
_ARITH = re.compile(r"^\s*goto \(.*\(\(m_D41B & \$01\) << \$04\).*\)$", re.M)


@pytest.mark.parametrize("row,label", CLEAN, ids=["%s:%s" % v for v in CLEAN])
def test_a_non_tail_self_call_alone_leaves_no_stack_behind(row, label):
    """Class 1's base case: the call survives, the machine stack does not.

    Every finding is a call-graph class: the recursive routine stays a ``sub`` under a
    ``pcall``, and ``mutual-recursion``'s second routine is spliced into it."""
    entry = G.parsed(row, label).procs[1][0]
    want = ["pcall: $%04X" % entry, "procedures: 2"]
    assert not K.page_one(row, label)
    assert G.violations(row, label) == tuple(sorted(want))


@pytest.mark.parametrize(
    "row,label", CONTROL + RESIDUE, ids=["%s:%s" % v for v in CONTROL + RESIDUE]
)
def test_a_value_parked_in_page_one_keeps_sp_and_faults(row, label):
    """Page one as the value stack: sp threads the recursion and the protection fires."""
    assert K.page_one(row, label) == (0x0100,)
    entry = G.parsed(row, label).procs[1][0]
    got = G.violations(row, label)
    assert "sp: parameter of sub_$%04X" % entry in got
    assert "fault: store into the stack page $01FB" in got


@pytest.mark.parametrize("row,label", CONTROL, ids=["%s:%s" % v for v in CONTROL])
def test_the_control_carries_a_value_the_restored_counter_re_derives(row, label):
    """Not a residue: the pushed byte is a table read indexed by state the callee restores."""
    rhs = _PUSH.findall(K.bodies(row, label))
    assert len(rhs) == 1 and _RESTORED.match(rhs[0].strip())
    assert "ctr_00F5 = (ctr_00F5 + $01)" in K.bodies(row, label)


@pytest.mark.parametrize("row,label", RESIDUE, ids=["%s:%s" % v for v in RESIDUE])
def test_the_residue_carries_a_value_no_restored_state_re_derives(row, label):
    """The real refusal: the pushed byte is volatile, overwritten, or reduced by an AND."""
    body, kind = K.bodies(row, label), label.split("/")[0]
    rhs = [x.strip() for x in _PUSH.findall(body)]
    assert rhs and not any(_RESTORED.match(x) for x in rhs)
    if kind in ("volatile", "multi"):
        assert rhs == ["m_D41B"] * len(rhs)
        assert "inputs { osc3 }" in G.text(row, label)
    elif kind == "overwrite":
        assert rhs == ["zp_F7"] and body.index("zp_F7 = w0") > body.index("= zp_F7")
    else:
        assert rhs == ["zp_F6"] and "zp_F6 = (w0 & w1)" in body
    assert "re-derive" in M_CARRIED_VALUE


@pytest.mark.parametrize("what", ("volatile", "overwrite", "lossy", "multi"))
def test_the_page_the_author_chose_is_the_whole_difference(what):
    """The same irreducible value in page zero costs nothing: the array is in the image."""
    assert not K.page_one("carry-recursion", "%s/zp" % what)
    assert K.page_one("carry-recursion", "%s/pha" % what) == (0x0100,)
    assert K.activations("carry-recursion", "%s/zp" % what, "crec") == K.activations(
        "carry-recursion", "%s/pha" % what, "crec"
    )


def test_the_residue_scales_with_the_values_live_at_one_activation():
    """Not a one-slot special case: two carried bytes are two page-one cells per level."""
    body = K.bodies("carry-recursion", "multi/pha")
    assert len(_PUSH.findall(body)) == 2
    assert "sp = (sp - $02)" in body and "sp = (sp + $02)" in body
    assert body.count("mem[zext2((ctr_00F5") == 0


@pytest.mark.parametrize(
    "row,label", CLEAN[8:10] + CLEAN[14:], ids=["%s:%s" % v for v in CLEAN[8:10] + CLEAN[14:]]
)
def test_the_page_zero_slot_store_is_read_back_so_liveness_keeps_it(row, label):
    """Not dead: the store's cell is the load's, and the load reaches the SID write."""
    body = K.bodies(row, label)
    assert re.search(r"mem\[zext2\(\(\w+ - \$[23]0\)\)\] = ", body)
    assert re.search(r"= mem\[zext2\(\(ctr_00F5 - \$[23]0\)\)\]", body)
    assert "sid.v1.freq_lo = a" in body


def test_a_bounded_unroll_needs_a_depth_the_artifact_does_not_carry():
    """A trace-fixed depth is a fact about the trace; a volatile one is not even that."""
    assert set(K.activations("deep-recursion", "const/after", "rec")) == {4}
    assert len(set(K.activations("input-recursion", "3/around", "irec"))) > 1
    body = K.bodies("input-recursion", "3/around")
    assert "ctr_00F5 = (m_D41B & $03)" in body
    assert body.count("if (") == 1 and "!= $00" in body


@pytest.mark.parametrize("label", ("const/a", "const/b", "row/a", "row/b"))
def test_mutual_recursion_is_the_self_call_after_the_sole_site_inline(label):
    """No second mechanism: B has one static site, so A ends up calling itself."""
    prog = G.parsed("mutual-recursion", label)
    assert len(prog.procs) == 2
    assert "sub_%04X()" % prog.procs[1][0] in K.bodies("mutual-recursion", label)


@pytest.mark.parametrize("label", OPEN)
def test_an_rts_dispatch_the_trace_did_not_close_is_still_a_resolved_goto(label):
    """Class 2 is empty as a stack residue: the push goes, closed or not, table or not."""
    assert not K.page_one("open-dispatch", label)
    assert G.violations("open-dispatch", label) == ("procedures: %d" % (ARMS[label] + 1),)
    assert "goto (" in K.bodies("open-dispatch", label)


@pytest.mark.parametrize("label", OPEN)
def test_the_guard_is_the_observed_arm_set_the_evidence_channel_states(label):
    """Guarded, never claimed: the arms the trace ran, and a faulting default."""
    prog = G.parsed("open-dispatch", label)
    seen = K.targets("open-dispatch", label)
    arms = seen[min(pc for pc in seen if pc > prog.procs[0][0])]
    assert len(arms) == ARMS[label]
    assert set(arms) == {p[0] for p in prog.procs[1:]}


def test_a_partially_observed_arm_set_narrows_its_own_table():
    """The unobserved arms are in the image and in neither the table nor a procedure."""
    lab = K.labels("open-dispatch", "partial")
    assert lab["sa1"] - lab["sa0"] == K.STRIDE
    assert "stride 2" in G.text("open-dispatch", "partial")
    seats = {p[0] for p in G.parsed("open-dispatch", "partial").procs}
    assert not {lab["sa1"], lab["sa3"]} & seats


@pytest.mark.parametrize("label", ("computed", "roundtrip"))
def test_an_arithmetic_push_is_the_goto_its_own_operand_names(label):
    """The pushed byte's form decides nothing: rung (d0r) asks the slot, not the shape.

    The window this replaced matched a push pair of const, loc or mem alone, so an
    arithmetic target left both cells, the displacement and the ret standing."""
    body = K.bodies("open-dispatch", label)
    assert _ARITH.search(body), "the arithmetic is not the goto's operand"
    assert not re.search(r"\bm_01[0-9A-F]{2}\b|\bsp\b", body)
    assert not K.page_one("open-dispatch", label)


def test_the_arms_are_one_page_and_one_stride_so_the_arithmetic_is_a_target():
    """The `computed` driver is a real dispatch, not a driver bug."""
    lab = K.labels("open-dispatch", "computed")
    pcs = [lab["sa%d" % k] for k in range(4)]
    assert [b - a for a, b in zip(pcs, pcs[1:])] == [K.STRIDE] * 3
    assert len({(pc - 1) >> 8 for pc in pcs}) == 1


@pytest.mark.parametrize("row,label", K.ALL, ids=["%s:%s" % v for v in K.ALL])
def test_every_driver_reproduces_its_own_write_log(row, label):
    """The lift is correct today, so no verdict above rests on a lifter bug."""
    model, prog = G.built(row, label)
    if (row, label) in CONTROL + RESIDUE:
        with pytest.raises(frameval.FrameFault, match="the stack page"):
            frameval.gate_fp(model, G.FRAMES, prog)
        return
    assert frameval.gate_fp(model, G.FRAMES, prog) is None
