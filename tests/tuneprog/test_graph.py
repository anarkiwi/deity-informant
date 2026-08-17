"""The shared control-flow graph: predecessors, dominators, natural loops."""

from deity_informant.tuneprog import graph as G
from deity_informant.tuneprog.ir import Block, Const, Goto, If, Proc, Return, Switch, Trap, Var


def _proc(*blocks, entry="b0"):
    return Proc("f", (), (), {b.label: b for b in blocks}, entry)


def _diamond():
    return _proc(
        Block("b0", [], If(Var("C"), "t", "f")),
        Block("t", [], Goto("j")),
        Block("f", [], Goto("j")),
        Block("j", [], Return()),
    )


def _loop():
    """``b0 -> h``; ``h`` tests and either latches back or leaves to ``x``."""
    return _proc(
        Block("b0", [], Goto("h")),
        Block("h", [], If(Var("C"), "body", "x")),
        Block("body", [], Goto("h")),
        Block("x", [], Return()),
    )


def test_preds_of_lists_every_edge_into_a_block():
    preds = G.preds_of(_diamond())
    assert preds == {"b0": [], "t": ["b0"], "f": ["b0"], "j": ["t", "f"]}


def test_cfg_carries_every_successor_and_optionally_a_virtual_exit():
    g = G.cfg(_diamond())
    assert set(g.edges) == {("b0", "t"), ("b0", "f"), ("t", "j"), ("f", "j")}
    assert G.EXIT not in g
    ge = G.cfg(_diamond(), G.EXIT)
    assert ("j", G.EXIT) in ge.edges and G.EXIT in ge


def test_a_switch_edges_to_every_arm_and_to_its_default():
    proc = _proc(
        Block("b0", [], Switch(Const(0), ((0, "a"), (1, "b")), "d")),
        Block("a", [], Return()),
        Block("b", [], Return()),
        Block("d", [], Trap("switch")),
    )
    assert set(G.cfg(proc).edges) == {("b0", "a"), ("b0", "b"), ("b0", "d")}


def test_idoms_and_postdoms_name_the_join_of_a_diamond():
    proc = _diamond()
    idom = G.idoms(proc)
    assert idom["t"] == "b0" and idom["f"] == "b0" and idom["j"] == "b0"
    ipdom = G.postdoms(G.cfg(proc), proc)
    assert ipdom["t"] == "j" and ipdom["f"] == "j" and ipdom["b0"] == "j"


def test_postdoms_are_empty_when_nothing_returns():
    proc = _proc(Block("b0", [], Goto("t")), Block("t", [], Trap("unreached")))
    assert G.postdoms(G.cfg(proc), proc) == {}


def test_natural_loops_find_the_body_and_the_latch_of_a_back_edge():
    proc = _loop()
    loops = G.loops_of(proc)
    assert set(loops) == {"h"}
    body, latches = loops["h"]
    assert body == {"h", "body"} and latches == {"body"}


def test_a_graph_without_a_back_edge_has_no_loop():
    assert G.loops_of(_diamond()) == {}
