"""Per-idiom convergence over the unified graph (register-model-lift stage 3c).

The catalog's rows are the idioms; a parametric generator spells each one the ways a
6502 lift legitimately spells it, and the test asserts the spellings merge into ONE
e-class whose extracted representative is the row's normal form."""

import pytest

pytest.importorskip("egglog")
pytest.importorskip("z3")

from egglog import EGraph
from egglog import eq as egg_eq

from deity_informant import eqlift as E
from deity_informant import eqlift_mem as mem
from deity_informant import idioms


def L(n):
    return ("loc", n)


def N(v, w=1):
    return ("const", v, w)


def O(mn, kids, w):
    return ("op", mn, tuple(kids), w)


def Z(x):
    return O("INT_ZEXT", (x,), 2)


def M(a, w=1):
    return ("mem", a, w)


def PK(h, l):
    """``h<<8 | l``, the word two byte lanes make: eqlift's own ``_pk``."""
    return O("INT_OR", (O("INT_LEFT", (Z(h), N(8, 1)), 2), Z(l)), 2)


_ROUNDS = 4  # every row's fixpoint: round-capped, so convergence is the rule set's
# property and not the clock's -- the borrow family reaches no fixpoint at all
_SPILL = [("st", N(0x40, 1), L("x"))]
_PUSH = O("INT_OR", (Z(L("sp")), N(0x0100, 2)), 2)
_ROW = O("INT_ADD", (N(0x5428, 2), Z(L("y"))), 2)
_PTR = O("INT_ADD", (L("p"), Z(L("y"))), 2)

PACK_ADD = O("INT_ADD", (O("INT_LEFT", (Z(L("h")), N(8, 1)), 2), Z(L("l"))), 2)

CASES = {  # row id -> (setup statements, spellings, the normal form extraction returns)
    "word-pack": (
        (),
        [
            PK(L("h"), L("l")),
            O("INT_OR", (Z(L("l")), O("INT_LEFT", (Z(L("h")), N(8, 1)), 2)), 2),
        ],
        "((zext2(h) << $08):2 | zext2(l)):2",
    ),
    "pair-row": (
        (),
        [
            PK(M(O("INT_ADD", (N(0x5429, 2), Z(L("y"))), 2)), M(_ROW)),
            M(_ROW, 2),
        ],
        "mem[($5428 + zext2(y)):2]:2",
    ),
    "lane-insert": (
        (),
        [
            O("INT_OR", (O("INT_AND", (L("w"), N(0xF0)), 1), L("v")), 1),
            O("INT_OR", (L("v"), O("INT_AND", (N(0xF0), L("w")), 1)), 1),
        ],
        "((w & $F0) | v)",
    ),
    "hi-byte": (
        (),
        [O("INT_RIGHT", (PK(L("h"), L("l")), N(8, 1)), 2), Z(L("h"))],
        "zext2(h)",
    ),
    "lo-byte": (
        (),
        [O("INT_AND", (PK(L("h"), L("l")), N(0xFF, 2)), 2), Z(L("l"))],
        "zext2(l)",
    ),
    "adc-chain": (
        (),
        [
            PK(
                O("INT_ADD", (L("ah"), L("bh"), O("INT_CARRY", (L("al"), L("bl")), 1)), 1),
                O("INT_ADD", (L("al"), L("bl")), 1),
            ),
            O("INT_ADD", (PK(L("ah"), L("al")), PK(L("bh"), L("bl"))), 2),
        ],
        "(((zext2((ah + bh)) << $08):2 | zext2(al)):2 + zext2(bl)):2",
    ),
    "sbc-chain": (
        (),
        [
            O("INT_SUB", (L("a"), O("INT_ADD", (L("b"), O("INT_SUB", (N(1), N(1)), 1)), 1)), 1),
            O("INT_SUB", (L("a"), L("b")), 1),
        ],
        "(a - b)",
    ),
    "shift-chain": (
        (),
        [
            O("INT_LEFT", (O("INT_LEFT", (L("v"), N(1)), 1), N(1)), 1),
            O("INT_LEFT", (L("v"), N(2)), 1),
        ],
        "(v << $02)",
    ),
    "flag-bit": (
        (),
        [
            O("INT_NOTEQUAL", (O("INT_AND", (L("v"), N(0x80)), 1), N(0)), 1),
            O("INT_NOTEQUAL", (N(0), O("INT_AND", (N(0x80), L("v")), 1)), 1),
        ],
        "(v <s $00)",
    ),
    "table-row": (
        (),
        [M(_ROW), M(O("INT_ADD", (Z(L("y")), N(0x5428, 2)), 2))],
        "m_5428[y]",
    ),
    "zp-row": (
        (),
        [M(Z(O("INT_ADD", (L("x"), N(0x20)), 1))), M(Z(O("INT_ADD", (N(0x20), L("x")), 1)))],
        "mem[zext2((x + $20))]",
    ),
    "cell-read": (_SPILL, [M(N(0x40, 1)), L("x")], "x"),
    "stack-slot": ([("st", _PUSH, L("a"))], [M(_PUSH), L("a")], "a"),
    "deref-row": (
        (),
        [M(_PTR), M(O("INT_ADD", (Z(L("y")), L("p")), 2))],
        "mem[(p + zext2(y)):2]",
    ),
    "mask-const": (
        (),
        [O("INT_AND", (L("v"), N(0x0F)), 1), O("INT_AND", (N(0x0F), L("v")), 1)],
        "(v & $0F)",
    ),
    "set-const": (
        (),
        [O("INT_OR", (L("v"), N(0x08)), 1), O("INT_OR", (N(0x08), L("v")), 1)],
        "(v | $08)",
    ),
    "alu-op": (
        (),
        [O("INT_SUB", (L("a"), N(3)), 1), O("INT_ADD", (L("a"), N(0xFD)), 1)],
        "(a - $03)",
    ),
    "widen": ((), [Z(L("v")), O("INT_AND", (Z(L("v")), N(0xFF, 2)), 2)], "zext2(v)"),
    "carry-value": (
        (),
        [
            O("INT_CARRY", (L("a"), L("b")), 1),
            O("INT_CARRY", (L("b"), L("a")), 1),
            O("INT_LESS", (O("INT_ADD", (L("a"), L("b")), 1), L("a")), 1),
        ],
        "((a + b) < a)",
    ),
    "compare-value": (
        (),
        [
            O("INT_NOTEQUAL", (L("a"), L("b")), 1),
            O("INT_NOTEQUAL", (L("b"), L("a")), 1),
            O("INT_NOTEQUAL", (O("INT_SUB", (L("a"), L("b")), 1), N(0)), 1),
        ],
        "(a != b)",
    ),
}

NO_VARIANTS = {  # a row that spells one way proves nothing by a generator; each says why
    "const-literal": "a literal has one spelling",
    "local-read": "a local has one spelling",
    "shift-pair": "the rotate's lanes are one shape; its spelling variants are shift-chain's",
}


def _terms(setup, variants):
    """One straight-line lift over a shared store chain; the variants' egg terms."""
    st = mem.Straight()
    st.run(list(setup) + [("asg", "v%d" % i, e) for i, e in enumerate(variants)])
    return [st.env["v%d" % i] for i in range(len(variants))]


def _order(ir):
    """``pick_ir``'s own price: a memory spelling loses to every value one."""
    return mem._has_mem(ir), E._cost(ir), repr(ir)


def _pick(eg, h):
    cands = [mem.to_ir(str(x)) for x in eg.extract_multiple(h, 12)]
    return E._Printer({}).fmt(min(cands, key=_order))


@pytest.fixture(scope="module", name="rules")
def _rules():
    return mem.unified_rules()[0]


def test_every_catalog_row_is_gated():
    """Enumerated from the catalog, so a row added there fails until it has a case."""
    assert set(CASES) | set(NO_VARIANTS) == set(idioms.FORMS)
    assert not set(CASES) & set(NO_VARIANTS)


def test_the_cost_names_the_or_built_pack_the_normal_form():
    """§4's cost change, admitted with the rule: the ADC spelling is priced above the
    ORA one, so the merge cannot leave which pack the artifact carries to ``repr``."""
    lo, hi = ("cell", 0x40, 1, 0), ("cell", 0x41, 1, 0)
    ora = ("bor", ("shl", ("zext", hi), ("num", 8, 1), 2), ("zext", lo), 2)
    adc = ("add",) + ora[1:]
    assert E._packed(adc) and not E._packed(ora)
    assert E._cost(adc) == E._cost(ora) + 1


def test_the_adc_built_pack_converges_on_the_ora_built_one(rules):
    """The pack the 6502 builds with ADC rather than ORA: one class, one normal form."""
    eg = EGraph()
    hs = [eg.let("h%d" % i, t) for i, t in enumerate(_terms((), [PK(L("h"), L("l")), PACK_ADD]))]
    mem.saturate(eg, rules, iters=_ROUNDS, budget=60.0)
    assert eg.check_bool(egg_eq(hs[0]).to(hs[1]))


@pytest.mark.parametrize("row", sorted(CASES))
def test_the_spellings_of_one_idiom_merge_into_one_class(row, rules):
    """Convergence: every spelling of the idiom is one e-class, and the representative
    extraction returns from each of them is the catalog's normal form."""
    setup, variants, want = CASES[row]
    assert len(variants) > 1, row
    eg = EGraph()
    hs = [eg.let("h%d" % i, t) for i, t in enumerate(_terms(setup, variants))]
    mem.saturate(eg, rules, iters=_ROUNDS, budget=60.0)
    for i, h in enumerate(hs[1:], 1):
        assert eg.check_bool(egg_eq(hs[0]).to(h)), "%s: variant %d did not merge" % (row, i)
    assert {_pick(eg, h) for h in hs} == {want}, row
