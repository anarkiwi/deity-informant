"""The two families whose dispatch the trace states indirectly (marked ``hvsc``).

Virtuoso patches the operand of its own ``JMP (ind)``, so the target is the word
that operand points at; Ben Daglish's engine patches a branch offset that is
sometimes zero, so the taken arm lands where the untaken one does.
"""

import pytest

from deity_informant.tuneprog import jumptab
from deity_informant.tuneprog.ir import Const, Load, Switch, Var

from _hvsc import DEFLEKTOR, ZETA, decompiled

pytestmark = pytest.mark.hvsc

BOTH = ((ZETA, 30), (DEFLEKTOR, 30))


def test_both_certify_over_their_horizon_with_no_trap():
    for rel, secs in BOTH:
        run = decompiled(rel, seconds=secs, text=False)
        assert run.v.div is None and run.v.call == run.calls
        sub = run.cert["subtunes"][0]
        assert sub["divergences"] == 0 and sub["envelope_traps"] == 0
        assert run.prog.meta["stack"] == "eliminated"


def _dispatches(prog):
    """``(defs, block)`` of every computed dispatch with more than one arm."""
    for p in prog.procs.values():
        defs = jumptab._defs(p)
        for b in p.blocks.values():
            if type(b.term) is Switch and len(b.term.cases) > 1:
                yield defs, b


def _any(e, defs, pred, seen=()):
    """True where ``pred`` holds anywhere in ``e``, through the names the block bound."""
    if type(e) is Var:
        if e.n in seen:
            return False
        seen, e = seen + (e.n,), jumptab._resolve(e, defs, seen)
    kids = [y for y in (getattr(e, "a", None), getattr(e, "b", None)) if y is not None]
    return pred(e, defs, seen) or any(_any(y, defs, pred, seen) for y in kids)


def _is_load(x, _defs, _seen):
    return type(x) is Load


def _through_a_load(x, defs, seen):
    """A load whose address is not a constant and is itself produced by a load."""
    return type(x) is Load and type(x.a) is not Const and _any(x.a, defs, _is_load, seen)


def test_the_virtuoso_dispatch_reads_through_the_operand_it_patches():
    """The patched operand is the pointer, so the target is one load further on."""
    run = decompiled(ZETA, seconds=30, text=False)
    assert [b.label for defs, b in _dispatches(run.prog) if _any(b.term.e, defs, _through_a_load)]


def test_the_daglish_dispatch_reaches_the_address_after_the_branch():
    """A zero offset in the cell names the address the untaken arm falls to."""
    run = decompiled(DEFLEKTOR, seconds=30, text=False)
    hit = []
    for defs, b in _dispatches(run.prog):
        got = jumptab._cell(b.term, defs)
        if got and got[2] is not None and got[2] in {v for v, _l in b.term.cases}:
            hit.append(b.label)
    assert hit


def test_experiment_zeta_closes_on_its_own_period():
    run = decompiled(ZETA, seconds=130, until_period=True, text=False)
    sub = run.cert["subtunes"][0]
    assert sub["complete"] and sub["period"] == sub["trace_period"] > 0
    assert sub["first_repeat"] == sub["trace_first_repeat"] == run.trace.meta["first_repeat"]
