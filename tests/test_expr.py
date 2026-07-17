"""Expression-algebra unit tests: flat associative folding + depth guard."""

import pytest

import deity_informant.expr as E


def _depth(n):
    """Max node depth over the DAG (iterative; safe on shared subtrees)."""
    order, seen, stack = [], set(), [n]
    while stack:
        x = stack.pop()
        if id(x) in seen:
            continue
        seen.add(id(x))
        order.append(x)
        stack.extend(E._children(x))  # pylint: disable=protected-access
    dep = {}
    for x in reversed(order):
        kids = E._children(x)  # pylint: disable=protected-access
        dep[id(x)] = 1 + max((dep[id(k)] for k in kids), default=0)
    return dep[id(n)]


@pytest.mark.parametrize("mn", ["INT_ADD", "INT_OR", "INT_XOR", "INT_AND"])
def test_associative_accumulator_stays_flat(mn):
    E.clear_simplify_cache()
    acc = E.mem(E.konst(0x1000, 2), 1)
    for i in range(500):
        acc = E.op(mn, [acc, E.mem(E.konst(0x2000 + i, 2), 1)], 1)
    assert acc[0] == "op" and acc[1] == mn
    assert len(acc[2]) == 501
    assert _depth(acc) == 3


def test_flat_node_evaluates_like_nested():
    E.clear_simplify_cache()
    terms = [E.konst(i, 1) for i in (3, 40, 5, 200)]
    flat = E.op("INT_ADD", terms, 1)
    assert E.evaluate(flat, b"", [], b"", {}) == (3 + 40 + 5 + 200) & 0xFF


def test_constants_fold_and_identity_drops():
    E.clear_simplify_cache()
    x = E.mem(E.konst(0x10, 2), 1)
    assert E.op("INT_ADD", [x, E.konst(0, 1)], 1) == x
    assert E.op("INT_OR", [x, E.konst(0, 1)], 1) == x
    folded = E.op("INT_ADD", [x, E.konst(5, 1), E.konst(9, 1)], 1)
    assert folded == ("op", "INT_ADD", (x, E.konst(14, 1)), 1)


def test_sub_by_const_folds_into_sum():
    E.clear_simplify_cache()
    x = E.mem(E.konst(0x10, 2), 1)
    node = E.op("INT_SUB", [E.op("INT_ADD", [x, E.konst(10, 1)], 1), E.konst(3, 1)], 1)
    assert node == ("op", "INT_ADD", (x, E.konst(7, 1)), 1)


def test_depth_guard_raises_on_runaway():
    E.clear_simplify_cache()
    acc = E.mem(E.konst(0x1000, 2), 1)
    with pytest.raises(E.ExprTooComplex):
        for i in range(E.MAX_DEPTH + 50):
            acc = E.op("INT_SUB", [acc, E.mem(E.konst(0x2000 + i, 2), 1)], 1)
