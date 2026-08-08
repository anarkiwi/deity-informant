"""Hermetic tests for Phase 2b (b2)'s extent guard (docs/register-model-lift-impl.md).

An access through a web whose declaration names an extent faults where the address it
computes leaves those blocks. The subjects are built here, so the rule is read at the
seats: what is guarded, what costs nothing, the width rule, and b0's probe alongside.
"""

import pytest

from deity_informant import frameprog, frameval, ptrextent
from deity_informant.frameval import FrameFault

PTR = 0x0002  # the pointer pair the extent annotates
OTHER = 0x0040  # a second web, never annotated
LOW, HIGH = 0x1500, 0x1600  # two declared blocks, not adjacent
SIZE = 4

PAIR = ("mem", ("const", PTR, 2), 2)
WALK = ("op", "INT_ADD", (PAIR, ("op", "INT_ZEXT", (("loc", "y", 1),), 2)), 2)
LOOSE = ("op", "INT_ADD", (("mem", ("const", OTHER, 2), 2), ("const", 1, 2)), 2)


def _const_addr(r, m, rd):
    return LOW


def _decl(base, size):
    """One declared datum, as ``datadecl`` shapes them for ``Regions``."""
    return {"base": base, "size": size, "stride": 1, "mut": [], "via": PTR, "kind": "stream"}


def _mem0(word=LOW):
    mem0 = bytearray(0x10000)
    mem0[PTR], mem0[PTR + 1] = word & 0xFF, word >> 8
    for base in (LOW, HIGH):
        for j in range(8):
            mem0[base + j] = (base >> 8) + j
    return mem0


def _prog(stmts, decls=(_decl(LOW, SIZE), _decl(HIGH, SIZE)), extents=None, mem0=None):
    prog = frameprog.FrameProgram(
        0x1000,
        0x0F00,
        decls=list(decls),
        procs=[(0x1000, [], [], list(stmts) + [("ret", False)])],
        mem0=_mem0() if mem0 is None else mem0,
    )
    if extents is not None:
        prog.extents = extents
    return prog


def _reads(y, width=1, extents=None, **kw):
    """A frame whose one deref is ``ptr[y]`` at ``width``, stored to a SID register."""
    stmts = [("asg", "y", ("const", y, 1)), ("st", ("const", 0xD404, 2), ("mem", WALK, width))]
    return _prog(stmts, extents=extents, **kw)


def _writes(y, extents=None, **kw):
    """A frame whose one deref is the write-through store ``ptr[y] = $5A``."""
    stmts = [("asg", "y", ("const", y, 1)), ("st", WALK, ("const", 0x5A, 1))]
    return _prog(stmts, extents=extents, **kw)


def test_a_site_no_annotated_web_spelled_hands_back_its_own_closure():
    """Attribution is per site, so a guard costs nothing where no extent governs."""
    guard = frameval.Extent(_prog([], extents={PTR: (LOW,)}))
    assert guard(LOOSE, _const_addr, 1) is _const_addr
    assert guard(("const", LOW, 2), _const_addr, 1) is _const_addr
    assert guard.sites == 0
    assert guard(WALK, _const_addr, 1) is not _const_addr and guard.sites == 1


def test_a_program_declaring_no_extent_builds_no_guard_at_all():
    """Zero cost is literal: with nothing annotated the seats carry no wrapper."""
    assert frameval._Code(_reads(1)).probe is None
    assert frameval._Code(_reads(1, extents={})).probe is None
    assert frameval._Code(_reads(1, extents={PTR: (LOW,)})).probe is not None


def test_the_guard_does_not_change_the_evaluation():
    """b0's own contract, at the guard: the same image, the same frames, inside."""
    bare = frameval.Evaluator(_reads(1), {})
    guarded = frameval.Evaluator(_reads(1, extents={PTR: (LOW,)}), {})
    assert bare.frames(4) == guarded.frames(4) == [[(4, 0x16)]] * 4
    assert bytes(bare.m) == bytes(guarded.m)


def test_an_access_outside_the_extent_faults_at_evaluation():
    """The fault opens on the token the gate greps for, and names the web."""
    with pytest.raises(FrameFault) as exc:
        frameval.Evaluator(_reads(SIZE, extents={PTR: (LOW,)}), {}).frames(1)
    assert str(exc.value) == "extent $1504 outside zp_02"


def test_a_write_through_store_is_guarded_at_its_own_seat():
    """The store address is a deref too, so both seats carry the check."""
    ev = frameval.Evaluator(_writes(1, extents={PTR: (LOW,)}), {})
    assert ev.frames(1) == [[]] and ev.m[LOW + 1] == 0x5A
    with pytest.raises(FrameFault, match=r"^extent \$1504 outside zp_02$"):
        frameval.Evaluator(_writes(SIZE, extents={PTR: (LOW,)}), {}).frames(1)


def test_a_word_at_the_last_cell_of_a_block_reads_one_past_it_and_faults():
    """Every byte the access touches is checked, which is what b0 observed."""
    assert frameval.Evaluator(_reads(SIZE - 1, extents={PTR: (LOW,)}), {}).frames(1) == [
        [(4, 0x18)]
    ]
    with pytest.raises(FrameFault, match=r"^extent \$1504 outside zp_02$"):
        frameval.Evaluator(_reads(SIZE - 1, 2, extents={PTR: (LOW,)}), {}).frames(1)


def test_two_blocks_of_one_extent_admit_a_byte_each_but_no_word_between_them():
    """The union is per byte, so a word may not bridge the gap between blocks."""
    both = {PTR: (LOW, HIGH)}
    assert frameval.Evaluator(_reads(1, extents=both), {}).frames(1) == [[(4, 0x16)]]
    far = _reads(1, extents=both, mem0=_mem0(HIGH))
    assert frameval.Evaluator(far, {}).frames(1) == [[(4, 0x17)]]
    with pytest.raises(FrameFault, match=r"^extent \$1504 outside zp_02$"):
        frameval.Evaluator(_reads(SIZE - 1, 2, extents=both), {}).frames(1)


def test_two_adjacent_blocks_of_one_extent_are_one_region():
    """A word across the seam is inside: the extent's bytes are what it admits."""
    decls = (_decl(LOW, SIZE), _decl(LOW + SIZE, SIZE))
    prog = _reads(SIZE - 1, 2, decls=decls, extents={PTR: (LOW, LOW + SIZE)})
    assert frameval.Evaluator(prog, {}).frames(1) == [[(4, 0x18), (5, 0x19)]]


def test_a_block_the_registry_does_not_declare_admits_no_byte():
    """An extent naming an undeclared base guards nothing in, rather than everything."""
    with pytest.raises(FrameFault, match=r"^extent \$1501 outside zp_02$"):
        frameval.Evaluator(_reads(1, extents={PTR: (0x2000,)}), {}).frames(1)


def test_the_check_is_at_the_use_and_never_at_rest():
    """A pair holds junk between roles (5.2 M2), so only a deref's address is checked."""
    stmts = [
        ("st", ("const", 0x00C0, 2), PAIR),
        ("st", ("const", 0xD405, 2), ("op", "INT_AND", (PAIR, ("const", 0xFF, 2)), 1)),
        ("st", ("const", PTR, 2), ("const", LOW & 0xFF, 1)),
        ("st", ("const", PTR + 1, 2), ("const", LOW >> 8, 1)),
        ("asg", "y", ("const", 1, 1)),
        ("st", ("const", 0xD404, 2), ("mem", WALK, 1)),
    ]
    ev = frameval.Evaluator(_prog(stmts, extents={PTR: (LOW,)}, mem0=_mem0(0x9999)), {})
    assert ev.frames(1) == [[(5, 0x99), (4, 0x16)]]
    assert ev.m[0x00C0] == 0x99 and ev.m[0x00C1] == 0x99


def test_the_probe_observes_the_address_the_guard_then_refuses():
    """Ordering at the shared seat: b0's observer runs first, so the gap is named."""
    probe = ptrextent.Probe()
    with pytest.raises(FrameFault, match=r"^extent \$1504 outside zp_02$"):
        frameval.Evaluator(_reads(SIZE, extents={PTR: (LOW,)}), {}, probe=probe).frames(1)
    assert probe.sites == 1 and probe.hits[PTR] == {0x1504}


def test_the_probe_and_the_guard_compose_without_changing_the_evaluation():
    """A census run may carry both instruments; inside the extent neither is visible."""
    bare = frameval.Evaluator(_reads(1), {})
    probe = ptrextent.Probe()
    both = frameval.Evaluator(_reads(1, extents={PTR: (LOW,)}), {}, probe=probe)
    assert bare.frames(4) == both.frames(4)
    assert bytes(bare.m) == bytes(both.m)
    assert probe.hits[PTR] == {0x1501}
