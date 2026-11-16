"""Value sets of read-only memory carried through the main analysis (``docs/wholeprog.md``,
*Value sets*): register sets from constant tables, pointer pairs holding sets of words walked
with `Y`, and the address sets they give stores, loads and computed jumps."""

import pytest

import wholeprog_progs as W
from test_wholeprog import differential
from deity_informant import NATIVE, wholeprog

ProcessPool = pytest.importorskip("pebble").ProcessPool

pytestmark = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

LIMIT = 60

# Records (selector, datum) through a pointer from word tables; the selector patches a store.
WALK = """
entry:  AND #$01
        TAX
        LDA plo,X
        STA $FB
        LDA phi,X
        STA $FC
        LDY #0
loop:   LDA ($FB),Y
        BMI done
        LSR A
        TAX
sel:    LDA dlo,X
        STA dst+1
        LDA dhi,X
        STA dst+2
        INY
        LDA ($FB),Y
dst:    STA $3000
        INY
        JMP loop
done:   RTS
plo:    .byte lo(r0), lo(r1)
phi:    .byte hi(r0), hi(r1)
r0:     .byte 0, $33, 4, $7D, $80
r1:     .byte 2, $15, 0, $61, 4, $0B, $FF
dlo:    .byte lo($3000), lo($3010), lo($3020)
dhi:    .byte hi($3000), hi($3010), hi($3020)
end:
"""

# A selector table of even offsets into a jump table of three words.
SELECT = """
entry:  AND #$03
        TAX
        LDA sel,X
        TAX
        LDA jt,X
        STA $FB
        LDA jt+1,X
        STA $FC
        JMP ($FB)
t0:     LDA #1
        STA $C000
        RTS
t1:     LDA #2
        STA $C000
        RTS
t2:     LDA #3
        STA $C000
        RTS
sel:    .byte 0, 2, 4, 2
jt:     .byte lo(t0), hi(t0), lo(t1), hi(t1), lo(t2), hi(t2)
end:
"""

# More words than a pointer pair's set holds.
WIDE = (
    """
entry:  AND #$7F
        TAX
        LDA plo,X
        STA $FB
        LDA phi,X
        STA $FC
        LDY #0
        LDA ($FB),Y
        STA $C000
        RTS
plo:    .byte """
    + ", ".join(f"lo(d+{i})" for i in range(128))
    + "\nphi:    .byte "
    + ", ".join(f"hi(d+{i})" for i in range(128))
    + "\nd:      .byte "
    + ", ".join(str(i) for i in range(128))
    + "\nend:\n"
)

# An index permuted by a table and patched into an immediate, a pointer stepped by three.
GROW = """
entry:  LDX #0
        LDY #0
        LDA plo
        STA $FB
        LDA phi
        STA $FC
loop:   LDA perm,X
        TAX
        STA imm+1
imm:    LDA #0
        STA $C000
        LDA ($FB),Y
        STA $C001
        INY
        INY
        INY
        LDA $C002
        BNE loop
        RTS
plo:    .byte lo(perm)
phi:    .byte hi(perm)
perm:   .byte """ + ", ".join(str(i * 7 + 3 & 0xFF) for i in range(256)) + "\nend:\n"


# A table of addresses ending in a zero high byte walked two bytes a step, each entry's second
# byte incremented through the pointer it makes: the stores' addresses come from the table
# they may write.
ADDRS = """
entry:  LDX #0
loop:   LDA tab+1,X
        BEQ done
        STA $FC
        LDA tab,X
        STA $FB
        LDY #1
        LDA ($FB),Y
        CLC
        ADC #1
put:    STA ($FB),Y
        INX
        INX
        BNE loop
done:   RTS
tab:    .byte lo(d0), hi(d0), lo(d1), hi(d1), lo(d2), hi(d2), 0, 0
d0:     .byte 0, 0
d1:     .byte 0, 0
d2:     .byte 0, 0
end:
"""


def spread(n):
    """An index from ``n`` entries of a table spread over the byte, reading a second table."""
    vals = ", ".join(str(i * 37 % 256) for i in range(n))
    return f"""
entry:  AND #${n - 1:02X}
        TAX
        LDA t,X
        TAY
pick:   LDA u,Y
        STA $C000
        RTS
t:      .byte {vals}
u:      .byte {", ".join("0" for _ in range(256))}
end:
"""


def lift(src):
    """``(image, labels, program)``: the static lift of ``src``'s ``entry``."""
    img, labels = W.assemble(src)
    loaded = (0x1000, labels["end"])
    prog = wholeprog.lift(img, [labels["entry"]], [W.IO], entry_sp=0xFD, loaded=loaded, fold=True)
    return img, labels, prog


def runs(addrs):
    """``addr_sets`` runs ``[(lo, hi)]`` of the addresses ``addrs``."""
    out = []
    for a in sorted(addrs):
        if out and out[-1][1] == a:
            out[-1] = (out[-1][0], a + 1)
        else:
            out.append((a, a + 1))
    return out


def calls(values, n=1):
    """The entry called with each ``A`` of ``values``, ``n`` times over."""
    return [(0, a) for a in values] * n


def test_record_walk_bounds_a_patched_store():
    """The selectors the walk reads (not the data beside them) bound the patched store."""
    img, labels, prog = lift(WALK)
    sets = dict(prog.addr_sets)
    assert sets[(labels["dst"], True)] == runs([0x3000, 0x3010, 0x3020])
    assert dict(prog.ptr_sets)[labels["loop"]] == sorted([labels["r0"], labels["r1"]])
    assert list(dict(prog.sets)[labels["dst"] + 1]) == [0x00, 0x10, 0x20]
    selectors = [labels["r0"] + i for i in (0, 2, 4)] + [labels["r1"] + i for i in (0, 2, 4, 6)]
    assert sets[(labels["loop"], False)] == runs(selectors)
    assert sets[(labels["sel"], False)] == runs(labels["dlo"] + i for i in range(3))
    differential(img, prog, [labels["entry"]], calls(range(4), 2))
    assert not dict(prog.deopts)


def test_stored_records_do_not_narrow():
    """A store into a record leaves its bytes out of the constant set: the selector bounds
    no index."""
    src = WALK.replace("done:   RTS", "done:   LDA $C001\n        STA r1+2\n        RTS")
    img, labels, prog = lift(src)
    assert dict(prog.addr_sets)[(labels["sel"], False)] == runs(
        labels["dlo"] + i for i in range(64)
    )
    differential(img, prog, [labels["entry"]], calls(range(4), 3))


def test_unknown_store_over_records_is_checked():
    """A store through a pointer computed from I/O is checked against the records the sets
    read: a run in which it hits one leaves the lifted code there."""
    flip = "        EOR #$FF\n        EOR #$FF\n"
    src = WALK.replace(
        "done:   RTS",
        f"done:   LDA $C001\n{flip}        STA $F0\n        LDA $C002\n{flip}        STA $F1\n"
        "        LDY #0\n        LDA #$22\n        STA ($F0),Y\n        RTS",
    )
    img, labels, prog = lift(src)
    assert dict(prog.addr_sets)[(labels["dst"], True)] == runs([0x3000, 0x3010, 0x3020])
    differential(img, prog, [labels["entry"]], calls(range(4), 2))
    mem = bytearray(img)
    mem[0xC001], mem[0xC002] = labels["r1"] & 0xFF, labels["r1"] >> 8
    differential(img, prog, [labels["entry"]], calls([1]), synth=False, mem=mem)
    assert dict(prog.deopts)


def test_selector_table_bounds_a_computed_jump():
    """`JMP (ind)` through a word table at offsets from a selector table resolves without
    facts to the three words."""
    img, labels, prog = lift(SELECT)
    targets = [t for t in dict(prog.jumps).values() if t]
    assert [sorted(t) for t in targets] == [sorted(labels[n] for n in ("t0", "t1", "t2"))]
    differential(img, prog, [labels["entry"]], calls(range(8)))
    assert not dict(prog.deopts)


def test_word_set_past_its_cap_keeps_intervals():
    """A pointer from 128 words holds no set; its loads keep their intervals and replay."""
    img, labels, prog = lift(WIDE)
    assert not dict(prog.ptr_sets)
    differential(img, prog, [labels["entry"]], calls(range(0, 256, 5)))


@pytest.mark.parametrize("n", [32, 128])
def test_register_set_past_its_cap_keeps_its_interval(n):
    """An index of 32 table values addresses them alone; of 128, more than a set holds, its
    interval: every byte of the second table."""
    img, labels, prog = lift(spread(n))
    got = dict(prog.addr_sets).get((labels["pick"], False))
    want = runs(labels["u"] + i * 37 % 256 for i in range(n)) if n <= 64 else None
    assert got == want
    differential(img, prog, [labels["entry"]], calls(range(0, 256, 3)))


def lift_and_replay(src, plan):
    """The deopts of the calls ``plan`` replayed on the static lift of ``src``."""
    img, labels, prog = lift(src)
    differential(img, prog, [labels["entry"]], plan)
    return dict(prog.deopts)


def test_growing_sets_end():
    """Sets growing around a loop and through a patched immediate end within the limit."""
    with ProcessPool(max_workers=1) as pool:
        pool.schedule(lift_and_replay, (GROW, calls([0], 4)), timeout=LIMIT).result()


def test_table_of_addresses_stores_resolve():
    """The table is constant under the assumption the stores through its words then verify:
    each store's address set is the bytes its entries name, none of them the table's."""
    img, labels, prog = lift(ADDRS)
    tab = range(labels["tab"], labels["d0"])
    assert dict(prog.addr_sets)[(labels["put"], True)] == runs(
        labels[d] + 1 for d in ("d0", "d1", "d2")
    )
    assert set(tab) <= set(prog.code)
    differential(img, prog, [labels["entry"]], calls([0], 3))
    assert not dict(prog.deopts)


def test_table_entry_naming_the_table_is_checked():
    """An entry whose word names a table byte makes a store into the table: the store's set
    meets the bytes assumed constant, so it has none, and its runtime check leaves the lifted
    code on the call it hits the table, the lift replaying exactly."""
    src = ADDRS.replace("lo(d1), hi(d1)", "lo(tab+3), hi(tab+3)")
    img, labels, prog = lift(src)
    assert (labels["put"], True) not in dict(prog.addr_sets)
    differential(img, prog, [labels["entry"]], calls([0], 4))
    assert labels["put"] + 2 in dict(prog.deopts)
