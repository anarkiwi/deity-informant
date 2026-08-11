"""Stage 2's capability checklist: every catalog normal form spelled and executed.

One case per non-unknown ``idioms.FORMS`` entry, enumerated from the catalog so
the checklist cannot undercount: each is emitted, parsed back, accounted to the
row it claims, and run to a checked SID write.
"""

import re
from pathlib import Path

import pytest

from deity_informant import frameproc as P
from deity_informant import frameprog
from deity_informant import frameval
from deity_informant import grammar as G
from deity_informant import idioms
from deity_informant import roles

SID = 0xD400
CELL, ALT, ZP, PTR, ARR, HI = 0x2000, 0x2100, 0x0080, 0x00FB, 0x3000, 0x3100
TABLE = bytes(range(0x0A, 0x0A + 16))


def op(mn, kids, w=1):
    return ("op", mn, tuple(kids), w)


def c(v, w=1):
    return ("const", v, w)


def m(a, w=1):
    return ("mem", ("const", a, 2), w)


def loc(name, w=1):
    return ("loc", name) if w == 1 else ("loc", name, w)


def zext(x):
    return op("INT_ZEXT", (x,), 2)


def pack(lo, hi):
    return op("INT_OR", (op("INT_LEFT", (zext(hi), c(8)), 2), zext(lo)), 2)


def row(base, idx):
    return ("mem", op("INT_ADD", (c(base, 2), zext(idx)), 2), 1)


def image():
    b = bytearray(0x10000)
    b[CELL], b[CELL + 1], b[ALT], b[ZP] = 0x11, 0x22, 0x05, 0x5A
    b[ARR : ARR + 16], b[HI : HI + 16] = TABLE, TABLE[::-1]
    b[PTR], b[PTR + 1] = ARR & 0xFF, ARR >> 8
    return b


def decl(base, size, role=None):
    return {
        "base": base,
        "size": size,
        "kind": "table",
        "stride": 1,
        "mut": [],
        "role": role,
        "cobases": [],
        "targets": None,
        "cmp": [],
        "dispatch": [],
        "observed": True,
        "via": None,
        "data": bytes(image()[base : base + size]),
    }


A, X, Y = loc("a"), loc("x"), loc("y")
DEREF = ("mem", op("INT_ADD", (m(PTR, 2), zext(Y)), 2), 1)
PTR_STATE = (("m_00FB", 2, False, []),)
COLUMNS = [decl(ARR, 16, ("lo", HI)), decl(HI, 16, ("hi", ARR))]


def case(value, writes, state=(), decls=(), resolved=()):
    """One checklist row: the value stored at the SID boundary, and what it writes."""
    return (value, writes, state, decls, resolved)


CASES = {
    "cell-read": case(m(CELL), [(0, 0x11)]),
    "pair-row": case(pack(m(ARR), m(HI)), [(0, 0x0A), (1, 0x19)], decls=COLUMNS),
    "word-pack": case(pack(A, op("INT_ADD", (A, m(CELL)))), [(0, 0x00), (1, 0x11)]),
    "lane-insert": case(
        op("INT_OR", (op("INT_AND", (m(CELL, 2), c(0xFF00, 2)), 2), zext(m(ALT))), 2),
        [(0, 0x05), (1, 0x22)],
    ),
    "hi-byte": case(P.trunc_hi(m(CELL, 2)), [(0, 0x22)]),
    "lo-byte": case(P.trunc_lo(m(CELL, 2)), [(0, 0x11)]),
    "shift-pair": case(
        op(
            "INT_OR",
            (
                op("INT_RIGHT", (m(CELL), c(1))),
                op("INT_LEFT", (op("INT_AND", (m(ALT), c(1))), c(7))),
            ),
        ),
        [(0, 0x88)],
    ),
    "adc-chain": case(
        op(
            "INT_ADD",
            (
                m(CELL),
                m(ALT),
                op(
                    "INT_OR",
                    (
                        op("INT_CARRY", (m(CELL), m(ALT))),
                        op("INT_CARRY", (op("INT_ADD", (m(CELL), m(ALT))), A)),
                    ),
                ),
            ),
        ),
        [(0, 0x16)],
    ),
    "sbc-chain": case(
        op("INT_SUB", (m(CELL), op("INT_ADD", (m(ALT), op("INT_SUB", (c(1), A)))))),
        [(0, 0x0B)],
    ),
    "shift-chain": case(op("INT_LEFT", (op("INT_LEFT", (m(CELL), c(1))), c(1))), [(0, 0x44)]),
    "flag-bit": case(
        op("INT_NOTEQUAL", (op("INT_AND", (op("INT_ADD", (m(CELL), m(ALT))), c(0x10))), c(0))),
        [(0, 0x01)],
    ),
    "table-row": case(row(ARR, Y), [(0, 0x0A)], decls=[decl(ARR, 16)]),
    "zp-row": case(("mem", zext(op("INT_ADD", (X, c(ZP)))), 1), [(0, 0x5A)]),
    "deref-row": case(DEREF, [(0, 0x0A)], state=PTR_STATE, resolved={DEREF[1]: (PTR, Y)}),
    "mask-const": case(op("INT_AND", (m(CELL), c(0x0F))), [(0, 0x01)]),
    "set-const": case(op("INT_OR", (m(CELL), c(0x40))), [(0, 0x51)]),
    "alu-op": case(op("INT_XOR", (A, m(CELL))), [(0, 0x11)]),
    "widen": case(zext(m(CELL)), [(0, 0x11), (1, 0x00)]),
    "const-literal": case(c(0x2A), [(0, 0x2A)]),
    "local-read": case(A, [(0, 0x00)]),
}

CHECKLIST = sorted(r.id for r in idioms.ROWS if r.form != idioms.UNKNOWN)


def test_the_checklist_is_enumerated_from_the_catalog_and_cannot_undercount():
    """Every non-unknown normal form owes a case; a named-unknown adds no vocabulary."""
    assert sorted(CASES) == CHECKLIST
    assert len(CHECKLIST) == 20
    assert not {r.id for r in idioms.ROWS if r.form == idioms.UNKNOWN} & set(CASES)


def _prog(value, state, decls, resolved):
    stmts = [("st", c(SID, 2), value), ("ret", False)]
    return frameprog.FrameProgram(
        0x1000,
        0x0F00,
        state=state,
        decls=decls,
        procs=[(0x1000, [], [], stmts)],
        mem0=image(),
        resolved=resolved,
    )


@pytest.mark.parametrize("rid", CHECKLIST)
def test_every_normal_form_is_spellable_and_executable(rid):
    """The form survives emission, parses back, accounts to its row, and runs."""
    value, writes, state, decls, resolved = CASES[rid]
    text = frameprog.dumps(_prog(value, state, decls, resolved))
    prog = frameprog.loads(text)
    assert frameprog.dumps(prog) == text

    sid, _upd = idioms.obligations(prog)
    assert len(sid) == 1
    counts, gaps = idioms.cover(sid[0].value)
    assert not gaps, [P._fmt(g) for g in gaps]
    assert counts[rid], (rid, sorted(counts))

    assert frameval.Evaluator(prog, {}).frames(1) == [writes]


_STATE = (
    ("ptr_00FB", 2, False, []),
    ("m_2000", 1, False, [0x01, 0x02]),
    ("m_3000", 1, True, []),
)


def _roled(assigned):
    stmts = [("st", c(SID, 2), m(CELL)), ("ret", False)]
    return frameprog.FrameProgram(
        0x1000,
        0x0F00,
        state=_STATE,
        procs=[(0x1000, [], [], stmts)],
        mem0=image(),
        extents={PTR: (ARR,)},
        decls=[decl(ARR, 16)],
        symbols={PTR: "ptr_00FB"},
        roles=assigned,
    )


@pytest.mark.parametrize("role", roles.ROLES)
def test_every_role_is_spellable_on_a_state_field_and_changes_no_execution(role):
    """A role qualifies the type; the program it annotates runs identically."""
    text = frameprog.dumps(_roled({n: role for n, *_r in _STATE}))
    assert " ptr_00FB: %s u16 in m_3000" % role in text
    assert " m_2000: %s u8 observed $01 $02" % role in text
    assert " m_3000: %s u8[]" % role in text
    prog = frameprog.loads(text)
    assert frameprog.dumps(prog) == text
    assert prog.roles == {n: role for n, *_r in _STATE}
    assert frameval.Evaluator(prog, {}).frames(1) == frameval.Evaluator(
        frameprog.loads(frameprog.dumps(_roled({}))), {}
    ).frames(1)


def test_the_role_keywords_are_the_census_s_roles_and_nothing_else():
    """The grammar's ``srole`` alternatives are ``roles.ROLES``, not a second list."""
    lark = (Path(G.__file__).parent / "sidprog.lark").read_text(encoding="utf-8")
    spelled = re.findall(r'"([a-z]+)"', re.search(r"^!srole:(.*)$", lark, re.M).group(1))
    assert spelled == list(roles.ROLES)
    assert set(roles.ROLES) <= G.keywords()  # so no symbol alias may shadow one


def test_a_role_rides_with_the_extent_and_the_observed_set_on_one_field():
    text = frameprog.dumps(_roled({"ptr_00FB": "cursor", "m_2000": "flags"}))
    assert " ptr_00FB: cursor u16 in m_3000" in text
    assert " m_2000: flags u8 observed $01 $02" in text
    assert re.search(r"^ m_3000: (?:\w+ )?u8\[\]", text, re.M)  # un-roled stays legal
    assert frameprog.dumps(frameprog.loads(text)) == text


def test_an_un_roled_program_emits_exactly_the_text_it_always_did():
    """Stage 2 is capability with zero use: no role, no token in the artifact."""
    text = frameprog.dumps(_roled({}))
    assert not any(" %s u" % r in text for r in roles.ROLES)
    assert re.search(r"^ ptr_00FB: (?:\w+ )?u16 in m_3000", text, re.M)
    assert re.search(r"^ m_2000: (?:\w+ )?u8 observed \$01 \$02", text, re.M)


def test_a_role_the_census_never_names_is_not_a_type():
    """``shifter`` is the residue's shape, not a role; the grammar refuses it."""
    text = frameprog.dumps(_roled({})).replace(" m_2000: u8", " m_2000: shifter u8", 1)
    with pytest.raises(ValueError, match="Unexpected token"):
        frameprog.loads(text)


_SIGNED = {
    "<s": (op("INT_SLESS", (op("INT_ADD", (m(CELL), c(0x80))), c(0))), 1),
    "<=s": (op("INT_SLESSEQUAL", (c(0), m(CELL))), 1),
}


@pytest.mark.parametrize("spelling", sorted(_SIGNED))
def test_the_signed_compare_is_spellable_and_executable(spelling):
    """The dialect's signed pair: spelled, parsed back, accounted and run.

    ``eqlift``'s admitted ``sign_ne``/``sign_eq`` rewrite ``(x & $80) != $00`` to a
    signed compare, so the unified emitter's operator set needs the dialect to carry
    it before step 4's cutover may use one -- stage 2's capability precondition."""
    value, want = _SIGNED[spelling]
    text = frameprog.dumps(_prog(value, (), (), ()))
    assert " %s " % spelling in text
    prog = frameprog.loads(text)
    assert frameprog.dumps(prog) == text

    sid, _upd = idioms.obligations(prog)
    counts, gaps = idioms.cover(sid[0].value)
    assert not gaps, [P._fmt(g) for g in gaps]
    assert counts["compare-value"], sorted(counts)

    assert frameval.Evaluator(prog, {}).frames(1) == [[(0, want)]]


def test_the_signed_compare_reads_the_sign_and_the_unsigned_one_does_not():
    """The operators differ where the operand's top bit is set, and nowhere else."""
    hi = op("INT_ADD", (m(CELL), c(0x80)))  # $11 + $80 = $91: negative, and above $00
    signed = frameval.Evaluator(
        frameprog.loads(frameprog.dumps(_prog(_SIGNED["<s"][0], (), (), ()))), {}
    )
    unsigned = frameval.Evaluator(
        frameprog.loads(frameprog.dumps(_prog(op("INT_LESS", (hi, c(0))), (), (), ()))), {}
    )
    assert signed.frames(1) == [[(0, 1)]] and unsigned.frames(1) == [[(0, 0)]]


def test_a_named_unknown_carries_no_spelling_obligation():
    """``stack-slot``/``carry-value``/``compare-value`` inventory a shape, not a form."""
    unknown = {r.id for r in idioms.ROWS if r.form == idioms.UNKNOWN}
    assert unknown == {"stack-slot", "carry-value", "compare-value"}
    assert len(unknown) + len(CHECKLIST) == len(idioms.ROWS)


def test_a_word_constant_is_spellable_as_a_word_store_s_value():
    """A folded word literal had no spelling: ``grammar.store_width`` read every
    ``const`` as one byte, so ``filter.cutoff_lo:2 = $0000`` was unparseable and
    ``framefuse._pack`` kept two constant halves apart to work around it. A literal's
    width is its digit count, which is the same rule every other value states."""
    text = frameprog.dumps(_prog(c(0x0102, 2), (), (), ()))
    assert " sid.v1.freq_lo:2 = $0102" in text
    prog = frameprog.loads(text)
    assert frameprog.dumps(prog) == text
    assert G.store_width(c(0x0102, 2)) == 2 and G.store_width(c(0x02, 1)) == 1
    assert frameval.Evaluator(prog, {}).frames(1) == [[(0, 0x02), (1, 0x01)]]
