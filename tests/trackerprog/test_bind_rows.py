"""B7's segment reader: the split a name two paths bind makes, and the staged order."""

from _bound import C, GLOB, SWEEP, V, binder, other, ram, reader, store, tick, VOICES
from _procs import Holder, diamond, halver
from deity_informant.trackerprog import rows
from deity_informant.tuneprog.ir import Bin, Block, Let, Proc, Return, Store


def oddstore():
    """A store at an index no voice names: a target the object has no name for."""
    s = Store(
        "ram",
        Bin("+", C(SWEEP, 2), V("j"), 2),
        Bin("+", ram(SWEEP, 10), C(1)),
        1,
        SWEEP,
        SWEEP + 2,
        10,
        0x7000,
    )
    return Proc("tick", entry="a", blocks={"a": Block("a", [s], Return(vals=[]), src=0x7000)})


# ---- rows.py: the split, the epoch and the staged order --------------------------
def test_a_name_more_than_one_block_binds_is_the_name_a_row_splits_on():
    assert rows.ambiguous(tick()) == {"x": {"top": C(2), "back": Bin("-", V("x"), C(1))}}
    assert sorted(rows.ambiguous(diamond())) == ["w", "z"]
    assert rows.ambiguous(halver()) == {}


def test_guard_terms_hold_together_only_where_no_condition_takes_both_truths():
    c = Bin("==", V("y"), C(0))
    assert rows._consistent((("a", c, True), ("b", c, True)))
    assert not rows._consistent((("a", c, True), ("b", c, False)))


def test_a_row_is_one_row_a_path_the_names_it_reads_are_bound_on():
    low = other(diamond())
    low.planall([list(low.proc.blocks)])
    R = rows.Rows(low, rows.ambiguous(low.proc))
    assert R.needs("e") == ["z", "w"]
    assert not R.needs("e", drop={0x2010})
    assert not R.needs("b")
    got = R.bindings("e", ())
    assert [pick for _extra, pick in got] == [{"z": "b", "w": "b"}, {"z": "d", "w": "c"}]
    assert len(got[0][0]) == 1 and len(got[1][0]) == 2  # the two paths that hold together
    assert R.bindings("b", ()) == [((), {})]
    assert R.when(got[1][0]) == [[1, "==", 0], [1, "!=", 0]]
    got = R.sets("e", ())
    assert got == [["#scratch", {"and": [{"add": [{"cell": "tz"}, {"cell": "tw"}]}, 0xFF]}]]
    assert not R.sets("e", (0x2010,))


def test_a_store_whose_value_was_read_first_stands_before_the_store_that_moved_it():
    stmts = [
        Let("v", ram(GLOB, 11, size=1)),
        store(GLOB, 11, C(5), src=0x6000, size=1),
        store(SWEEP, 10, V("v"), src=0x6004),
    ]
    pos = {"v": 0}
    assert rows._deps(stmts, 2, pos) == {GLOB: 0}
    assert rows._before(stmts, 2, 1, pos)
    assert not rows._before(stmts, 1, 2, pos)
    assert not rows._before(stmts, 2, 0, pos)
    got = [(1, "set", None, None), (2, "set", None, None)]
    assert [i for i, _k, _t, _s in rows._epoch(stmts, got)] == [2, 1]


def test_a_guard_over_a_name_the_tick_rebinds_is_carried_and_is_no_cell():
    low, _voc = reader()
    assert rows._carried(low, Bin("!=", V("elsewhere"), C(0)))
    assert not rows._carried(low, Bin("!=", V("t0"), C(0)))


def test_one_value_every_copy_takes_is_one_write_every_voice_makes():
    low, _voc = reader()
    same = [(k, "set", (("wave", k), {"const": 1}), None) for k in range(VOICES)]
    got = rows._copies(low, same)
    assert [x[2] for x in got] == [["*wave", {"const": 1}]]
    part = [(0, "set", (("wave", 0), {"const": 1}), ("k", {"cell": "q"}))]
    part += [(1, "set", (("wave", 1), {"const": 2}), None)]
    got = rows._copies(low, part)
    assert [x[2] for x in got] == [["@wave", {"const": 1}]]
    assert got[0][3] == ("k", {"cell": "wave"}) and "wave" in low.bad
    assert rows._copies(low, [(0, "set", None, None)]) == [(0, "set", None, None)]


def test_a_row_whose_guard_reads_a_cell_an_earlier_row_writes_stands_before_it():
    order = ["a", "b"]
    first = ("set", (), [["@counter", 1]], "b", [None])
    second = ("set", (("a", C(1), True),), [["ctrl", 2]], "b", [None])

    def facts(step):
        return ({"counter"}, frozenset({"a"})) if step is second else (set(), frozenset())

    assert rows._staged([first, second], order, facts) == [second, first]
    assert rows._staged([second, first], order, facts) == [second, first]


# ---- rows.py: the guards a staged segment reads --------------------------------
def test_a_value_a_block_stored_is_the_cell_a_later_guard_reads_it_in():
    b = binder()
    assert not rows.stored(b, None) and not rows.stored(b, "nosuchblock")
    assert not rows.stored(b, "keyon")  # no store of a value that is its own cell's
    got = rows.stored(b, "mach")
    assert list(got.values()) == [{"cell": "sweep"}]
    assert not rows.stored(Holder(other(halver())), "h")  # a copy, and no cell
    assert not rows.stored(Holder(other(oddstore())), "a")  # a target with no name


def test_a_step_whose_guard_the_object_cannot_read_states_no_cell_and_no_block():
    b = binder()
    step = ("set", (("head", Bin("!=", V("elsewhere"), C(0)), True),), None, "mach", [None])
    assert rows.guardfacts(b, step) == (set(), frozenset({"head"}))
    b.low.stated, b.low.scope = frozenset({id(step[1][0][1])}), frozenset()
    assert rows.guardfacts(b, step) == (set(), frozenset())


def test_a_block_the_object_has_no_reading_of_states_its_refusal_and_no_row():
    low = other(oddstore())
    assert not rows.blockrows(Holder(low), {"a"}, ["a"], set(), {})
    assert low.bad == {"a: $2412[..]"}


def test_a_store_the_schedule_already_states_is_no_assignment_of_a_row():
    low, voc = reader()
    low.planall([list(low.proc.blocks)])
    R = rows.Rows(low, {})
    assert [s[0] for s in R.sets("keyon", ())] == ["@note", "@ins", "@cmd", "@gate", "ctrl"]
    voc.dropstores = {0x1030, 0x1034}
    assert [s[0] for s in R.sets("keyon", ())] == ["@cmd", "@gate", "ctrl"]
    d, c, t = low.eff["fetch"][0][0]
    assert R.when(((d, c, t),))
    low.gate = frozenset({(id(c), t)})
    assert not R.when(((d, c, t),))


def test_a_store_at_an_address_no_constant_names_orders_nothing():
    stmts = [Store("ram", V("p", 2), C(1), 1, 0, 0xFFFF, -1, 0x8000)]
    assert not rows._before(stmts, 0, 0, {})
