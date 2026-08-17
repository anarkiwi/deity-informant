"""The shared IR traversal: sub-expressions, node reads, names, the call graph."""

from deity_informant.tuneprog import irwalk as W
from deity_informant.tuneprog.ir import (
    Assert,
    Bin,
    Block,
    Call,
    Const,
    Goto,
    If,
    Let,
    Load,
    Phi,
    Proc,
    R16,
    Return,
    Store,
    Switch,
    Trap,
    Tuneprog,
    Var,
    W16,
)

LD = Load("ram", Bin("+", Const(0x1000, 2), Var("Y"), 2), 1, 0x1000, 0x10FF, 3)
IO = Load("io", Const(0xD012, 2), 1, 0xD012, 0xD012, -1)


def _proc(name, blocks, entry="b0", **kw):
    return Proc(name, (), (), {b.label: b for b in blocks}, entry, **kw)


# ---- expressions -------------------------------------------------------------
def test_walk_descends_bin_load_and_the_word_view():
    e = Bin("|", LD, R16(1, 2, Const(0x20, 2)), 2)
    kinds = [type(x).__name__ for x in W.walk(e)]
    assert kinds == ["Bin", "Load", "Bin", "Const", "Var", "R16", "Const"]
    assert W.loads(e) == [LD]


def test_node_exprs_names_what_each_node_evaluates():
    st = Store("ram", Const(1, 2), Const(2), 1, 0, 0xFFFF, 4)
    cases = [
        (Let("t", LD), (LD,)),
        (Assert(LD, "why"), (LD,)),
        (st, (st.a, st.v)),
        (Call("f", (LD, IO), ("A",)), (LD, IO)),
        (W16(1, 2, Const(3, 2), LD), (Const(3, 2), LD)),
        (If(LD, "t", "f"), (LD,)),
        (Switch(LD, ((0, "a"),), ""), (LD,)),
        (Return((LD, IO)), (LD, IO)),
        (Goto("x"), ()),
        (Trap("no"), ()),
        (Phi("A", {"b0": "A#1"}), ()),
    ]
    for node, want in cases:
        assert tuple(W.node_exprs(node)) == want, node
    assert list(W.node_loads(Call("f", (LD, IO), ()))) == [LD, IO]


def test_sub_expr_rebuilds_through_loads_and_the_word_view():
    fn = W.renamer({"Y": Const(7)})
    assert W.sub_expr(LD, fn).a.b == Const(7)
    assert W.sub_expr(R16(1, 2, Var("Y")), fn) == R16(1, 2, Const(7))
    assert W.sub_expr(Const(1), fn) is not None


def test_pure_loadfree_and_addr_split():
    assert W.pure(LD) and not W.pure(IO)
    assert not W.loadfree(LD) and W.loadfree(Bin("+", Var("A"), Const(1), 1))
    assert W.addr_split(Const(0x1234, 2)) == (0x1234, None)
    assert W.addr_split(Bin("+", Var("X"), Const(0x40, 2), 2)) == (0x40, Var("X"))
    assert W.addr_split(Var("X")) == (None, Var("X"))


def test_expand_substitutes_names_to_a_bounded_depth():
    defs = {"a": Bin("+", Var("b"), Const(1), 1), "b": Const(2)}
    assert W.expand(Var("a"), defs, 4) == Bin("+", Const(2), Const(1), 1)
    assert W.expand(Var("a"), defs, 1) == Bin("+", Var("b"), Const(1), 1)
    assert W.expand(Load("ram", Var("a"), 1), defs, 2).a.a == Const(2)


def test_any_load_stops_at_the_first_hit():
    seen = []

    def pred(x):
        seen.append(x.r)
        return x.r == 3

    assert W.any_load(Bin("|", LD, LD, 1), pred) and seen == [3]
    assert not W.any_load(Bin("+", Var("A"), Const(1), 1), pred)
    assert W.any_load(R16(1, 2, LD), lambda x: x.r == 3)


# ---- statements --------------------------------------------------------------
def test_apply_stmt_rewrites_both_halves_of_a_word_assignment():
    s = W16(1, 2, Var("Y"), Bin("+", R16(1, 2, Var("Y")), Const(1, 2), 2))
    W.apply_stmt(s, W.renamer({"Y": Const(4)}))
    assert s.a == Const(4) and s.e.a.a == Const(4)


def test_apply_stmt_leaves_a_phi_alone():
    s = Phi("A", {"b0": "A#1"})
    W.apply_stmt(s, W.renamer({"A#1": Const(0)}))
    assert s.args == {"b0": "A#1"}


def test_uses_and_defs_of_every_node_kind():
    assert W.defs_of(Let("t", LD)) == ("t",)
    assert W.defs_of(Phi("A", {})) == ("A",)
    assert W.defs_of(Call("f", (), ("A", "X"))) == ("A", "X")
    assert W.defs_of(Store("ram", Const(0, 2), Const(0))) == ()
    assert W.stmt_uses(Let("t", LD), set()) == {"Y"}
    assert W.stmt_uses(W16(1, 2, Var("Z"), R16(1, 2, Var("Q"))), set()) == {"Z", "Q"}
    assert W.stmt_uses(Phi("A", {"b0": "A#1"}), set()) == {"A#1"}
    assert W.stmt_uses(Phi("A", {"b0": "A#1"}), set(), True) == set()
    assert W.term_uses(Return((Var("A"), Var("t1"))), set(), True) == {"A"}


def test_use_counts_and_single_defs():
    proc = _proc(
        "f",
        [Block("b0", [Let("t", LD), Let("u", Var("t")), Let("t", Const(0))], Return((Var("t"),)))],
    )
    counts = W.use_counts(proc)
    assert counts["t"] == 2 and counts["Y"] == 1
    assert set(W.single_defs(proc)) == {"u"}  # t has two definitions


def test_unique_name_suffixes_until_it_is_free():
    assert W.unique_name("a", {"b"}) == "a"
    assert W.unique_name("a", {"a", "a_2"}) == "a_3"
    assert W.unique_name("role", {"role"}, sep="") == "role2"


# ---- the call graph ----------------------------------------------------------
def _prog():
    tick = _proc("tick", [Block("b0", [Call("mid"), Call("leaf")], Return())])
    mid = _proc("mid", [Block("b0", [Call("leaf")], Return())])
    leaf = _proc("leaf", [Block("b0", [], Return())])
    lone = _proc("lone", [Block("b0", [Call("gone")], Return())])
    return Tuneprog(procs={p.name: p for p in (tick, mid, leaf, lone)})


def test_callees_keep_program_order_and_call_order_puts_callees_first():
    prog = _prog()
    assert W.callees(prog.procs["tick"]) == ["mid", "leaf"]
    order = W.call_order(prog)
    assert order.index("leaf") < order.index("mid") < order.index("tick")
    assert set(order) == set(prog.procs)


def test_reachable_and_forwarder():
    prog = _prog()
    assert W.reachable(prog, "tick") == {"tick", "mid", "leaf"}
    assert W.reachable(prog, "leaf") == {"leaf"}
    assert W.reachable(prog, None) == set()
    assert W.reachable(prog, "lone") == {"lone"}  # the missing callee is skipped
    assert W.forwarder(prog.procs["mid"]) == "leaf"
    assert W.forwarder(prog.procs["tick"]) is None
    assert W.forwarder(prog.procs["leaf"]) is None
