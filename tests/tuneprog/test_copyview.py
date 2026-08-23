"""S6 copy view: a per-copy column prints as the operand it stands for (snippets).

The substitution must be exact -- an affine plan evaluated at ``v = j`` is copy
``j``'s own operand and a table plan lists them all -- the loop must print as
``for v in 0..k-1``, and a program with no family must come through untouched.
"""

import random
import re

from deity_informant.tuneprog import copyview, live as L, pipeline, printer, structure, views
from deity_informant.tuneprog.ir import Bin, Const, Load, Let, Rgn, Store, Var
from deity_informant.tuneprog.recover import Names
from deity_informant.tuneprog.irwalk import node_exprs, walk

from _asm import asm
from _prog import PLAY, front, merged, printed as _text, proc_body as _body

VOICE = """
    LDA {st}
    BEQ {v}b
    LDA #$01
    STA {reg}
    JMP {next}
{v}b: LDA cnt
    STA {st}
    STA {reg}
    JMP {next}
"""


def _voice(v, st, reg, nxt):
    src = VOICE.format(st=st, v=v, reg=reg, next=nxt)
    lines = [ln.strip() for ln in src.split("\n") if ln.strip()]
    return [("%s: " % v if i == 0 else "") + ln for i, ln in enumerate(lines)]


def voices():
    """Three chained copies over consecutive cells of one block, one SID voice each."""
    return asm(
        PLAY,
        "init: LDX #$0B",
        "lp: LDA #$00",
        "STA st,X",
        "DEX",
        "BPL lp",
        "STA cnt",
        "RTS",
        "play:",
        *_voice("v0", "st+0", "$D404", "v1"),
        *_voice("v1", "st+1", "$D40B", "v2"),
        *_voice("v2", "st+2", "$D412", "after"),
        "after: INC cnt",
        "RTS",
        "st: BRK",
        *["BRK"] * 11,
        "cnt: BRK",
    )


def _view(code, calls=8):
    """``(certified program, its presentation view before the copy view)``."""
    trace = front(code, calls=calls)[0]
    prog = pipeline.build(trace, "snippet")[0]
    live = L.needed(prog)[0]
    return prog, structure.view(prog, live, L.wants(prog, live))


def _cols(view):
    tabs = {r.id: r for r in view.storage if r.kind == "copymap"}
    return tabs, copyview._collect(view, tabs)[0]


def _stmts(view):
    for p in view.procs.values():
        for b in p.blocks.values():
            yield from list(b.stmts) + [b.term]


def _reads(view, tabs):
    """Every column a load of the view still reads."""
    return {
        copyview._key(x, tabs) for s in _stmts(view) for e in node_exprs(s) for x in walk(e)
    } - {None}


def _eval(e, v):
    """The value of a substituted address expression at copy index ``v``."""
    t = type(e)
    if t is Const:
        return e.v
    if t is Var:
        return v
    a, b = _eval(e.a, v), _eval(e.b, v)
    return {"+": a + b, "-": a - b, "*": a * b}[e.op]


def _col(vals, w, k):
    """A bare column, for the rules that only look at its values."""
    col = copyview.Col.__new__(copyview.Col)
    col.w, col.k, col.vals, col.vars, col.targets = w, k, vals, {"cv0#2"}, {0}
    return col


def test_the_snippet_folds_three_voices_into_one_family():
    prog, _view_ = _view(voices())
    fams = prog.meta["copies"]["families"]
    assert len(fams) == 1 and fams[0]["copies"] == 3 and fams[0]["columns"] == 2


def test_every_affine_substitution_reproduces_each_copy_s_own_operand():
    _prog, view = _view(voices())
    _tabs, cols = _cols(view)
    hits = 0
    for col in cols.values():
        plan, _cells = copyview._col_plan(col, view.by_id())
        if plan is None or plan[0] != "index":
            continue
        e = copyview.step("v", plan[1], plan[2], plan[3])
        assert [_eval(e, j) for j in range(col.k)] == col.vals
        hits += 1
    assert hits


def test_every_group_view_column_names_exactly_the_copies_own_cells():
    _prog, view = _view(voices())
    _tabs, cols = _cols(view)
    hits = 0
    for col in cols.values():
        plan, cells = copyview._col_plan(col, view.by_id())
        if cells is None:
            continue
        # the read stays, so the printed index is the copy the access itself names
        assert [a for _r, a in cells] == col.vals and plan == ("read",)
        hits += 1
    assert hits


def test_the_view_keeps_the_column_reads_a_group_view_names_and_no_others():
    _prog, view = _view(voices())
    tabs, cols = _cols(view)
    plans = {k: copyview._col_plan(c, view.by_id())[0] for k, c in cols.items()}
    kept = {k for k, p in plans.items() if p is None or p[0] == "read"}
    assert kept and any(p is not None and p[0] == "index" for p in plans.values())
    copyview.expand(view)
    assert _reads(view, tabs) == kept
    names = {s.n for s in _stmts(view) if type(s) is Let}
    assert not [n for n in names if n.startswith("cx")]


def test_the_folded_loop_prints_as_a_for_over_the_copy_index():
    body = "\n".join(_body(merged(voices(), calls=8)[0], "tick"))
    assert "for v in 0, 1, 2:" in body, body
    assert "sid[v].ctrl" in body, body  # the SID column is the voice, by its stride
    assert "voice[v]." in body, body  # the block's cells are a group view
    assert "copies_" not in body, body


def test_the_state_header_lists_the_per_copy_addresses_once():
    head = merged(voices(), calls=8)[0].split("## state")[1].split("## data")[0]
    rows = [ln for ln in head.splitlines() if ln.startswith("voice[3]")]
    assert rows and "per-copy cells" in rows[0], head
    cells = [ln for ln in head.splitlines() if ln.startswith("  .")]
    assert cells and all(ln.count("$") == 3 for ln in cells), head


def test_a_program_with_no_family_is_untouched():
    code = asm(PLAY, "init: RTS", "play: LDA #$01", "STA $D404", "RTS")
    _prog, view = _view(code, calls=4)
    before = view.to_json()
    assert copyview.expand(view) == [] and view.to_json() == before


def test_a_column_whose_copies_name_different_fields_keeps_its_table_read():
    r = Rgn(0, "rec", 0x1000, 300, "state", 30, b"", ())
    apart = _col([0x1000, 0x1020, 0x104C], 2, 3)  # offsets 0, 2, 12 of three records
    assert copyview._col_plan(apart, {0: r}) == (None, None)
    same = _col([0x1000, 0x101E, 0x103C], 2, 3)  # one offset, three records
    assert copyview._col_plan(same, {0: r})[1] is not None


def test_random_columns_either_reproduce_their_values_or_keep_the_read():
    rng = random.Random(7)
    small = Rgn(0, "rec", 0x0100, 6, "state", 1, b"", ())
    block = Rgn(0, "blk", 0x0100, 64, "state", 1, b"", ())
    seen = set()
    for _ in range(300):
        k = rng.randrange(2, 6)
        vals = sorted(rng.sample(range(0x0100, 0x0140), k))
        plan, cells = copyview._col_plan(_col(vals, 1, k), {0: rng.choice([small, block])})
        seen.add(plan and plan[0])
        if plan is not None and plan[0] == "index":
            e = copyview.step("v", plan[1], plan[2], plan[3])
            assert [_eval(e, j) for j in range(k)] == vals
        elif cells is not None:
            assert [a for _r, a in cells] == vals
    assert seen == {"index", "read"}  # both rules fire over the sample


def test_a_column_read_left_in_place_still_loads_from_its_table():
    _prog, view = _view(voices())
    tabs, _cols_ = _cols(view)
    copyview.expand(view)
    for s in _stmts(view):
        for e in node_exprs(s):
            for x in walk(e):
                if type(x) is Load and x.r in tabs:
                    assert copyview._key(x, tabs) is not None


def test_a_stride_view_the_copy_index_selects_joins_the_fold_s_group():
    _prog, view = _view(voices())
    copies = copyview.expand(view)
    f = next(x for x in copies if x["slots"])
    names = Names(groups={"voice": {"stride": 6, "n": f["n"], "members": [7, 8]}})
    names.view = {7: ("voice", "a"), 8: ("voice", "b")}
    f["views"], f["named"] = {7, 8}, False
    views.copy_groups(view, names, [])
    g = names.groups["voice"]
    assert g["members"] == [7, 8] and g["cells"] and g["stride"] == 6
    assert list(names.groups) == ["voice"]  # one view, not two


def test_a_stride_view_the_index_does_not_select_keeps_its_own_name():
    _prog, view = _view(voices())
    copies = copyview.expand(view)
    f = next(x for x in copies if x["slots"])
    names = Names(groups={"voice": {"stride": 6, "n": f["n"], "members": [7, 8]}})
    names.view = {7: ("voice", "a"), 8: ("voice", "b")}
    f["views"], f["named"] = {7}, False
    views.copy_groups(view, names, [])
    assert sorted(names.groups) == ["voice", "voice_2"]


def test_a_constant_of_the_merged_body_never_borrows_the_loop_index():
    """A family's cell names the copy it belongs to wherever the address is a constant.

    Only the column read is indexed by ``v``; an operand every copy agrees on is
    copy *j*'s own byte, and printing it as ``g[v].field`` would be a lie.
    """
    _prog, view = _view(voices())
    copies = copyview.expand(view)
    names = Names()
    views.copy_groups(view, names, copies)
    hits = [h for hs in names.slots.values() for h in hs]
    assert hits and all(not local for _g, _n, _j, local in hits)
    p = printer.Body(view, names, pcs=False)
    p.fvars = {hits[0][0]: "v"}
    rid, addr = next(k for k, hs in names.slots.items() if hs[0][2] == 1)
    assert p.slot(names.slots[(rid, addr)], rid, addr, None).endswith("[1].%s" % hits[0][1])


def test_a_store_from_another_column_is_not_a_compound_assignment():
    """``a = b + k`` over two columns of one region must not print as ``a += k``.

    Both addresses are column reads, so neither carries a literal the printer can
    compare, and only the same expression names the same cell.
    """
    _prog, view = _view(voices())
    rid = next(r.id for r in view.storage if r.kind == "state")
    names = Names(column={(-4, 0x200): ("voice", "a", rid), (-4, 0x206): ("voice", "b", rid)})
    p = printer.Body(view, names, pcs=False)
    ca = Load("ram", Const(0x200), 2, 0x200, 0x205, -4)
    cb = Load("ram", Const(0x206), 2, 0x206, 0x20B, -4)
    other = Bin("+", Load("ram", cb, 1, 0, 0xFFFF, rid), Const(2), 1)
    own = Bin("+", Load("ram", ca, 1, 0, 0xFFFF, rid), Const(2), 1)
    assert p.store(Store("ram", ca, other, 1, 0, 0xFFFF, rid)) == "voice[0].a = (voice[0].b + 2)"
    assert p.store(Store("ram", ca, own, 1, 0, 0xFFFF, rid)) == "voice[0].a += 2"


def test_same_cell_refuses_two_computed_addresses_that_differ():
    _prog, view = _view(voices())
    p = printer.Body(view, Names(), pcs=False)
    lo = Load("ram", Const(0x200), 2, 0x200, 0x205, -4)
    hi = Load("ram", Const(0x206), 2, 0x206, 0x20B, -4)
    assert p.same_cell(lo, lo, 0, (None, lo)) and not p.same_cell(lo, hi, 0, (None, hi))


TABLE = """
init: LDA #$00
    STA cnt
    RTS
play: LDA cnt
    AND #$03
    TAY
    LDA tab,Y
    CLC
    ADC #$01
    {step}
    STA tab,Y
    INC cnt
    RTS
tab: BRK
"""


def _table(step):
    src = TABLE.format(step=step)
    return asm(PLAY, *[l.strip() for l in src.split("\n") if l.strip()], *["BRK"] * 7, "cnt: BRK")


def test_one_element_on_is_a_different_cell_not_a_compound():
    """``tab[i + 1] = tab[i] + 1`` must not print as ``tab[i + 1] += 1``.

    The literal base is the same for both accesses, so only the index tells the
    two cells apart -- a compound that ignores it asserts a read the program
    never makes.
    """
    shifted = "\n".join(_body(_text(_table("INY"), calls=6), "tick"))
    assert not [l for l in shifted.splitlines() if "+= 1" in l and "[" in l], shifted
    assert [l for l in shifted.splitlines() if re.search(r"\+ 1\] = \(t\d+ \+ 1\)", l)], shifted
    # the control: same index on both sides is a real compound
    same = "\n".join(_body(_text(_table("NOP"), calls=6), "tick"))
    assert [l for l in same.splitlines() if re.search(r"\[.*\] \+= 1", l)], same
