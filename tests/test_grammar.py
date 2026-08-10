"""The grammar: the LALR(1) parser, the doc/grammar drift gate, the name and
address bijection it resolves against, version policy and expression laws."""

import os
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from deity_informant import expr as E
from deity_informant import frameproc
from deity_informant import frameprog
from deity_informant import grammar as G
from deity_informant.render import sid_name

DOC = Path(__file__).resolve().parent.parent / "docs" / "grammar.md"
_BEGIN = "<!-- BEGIN GENERATED GRAMMAR: deity_informant/sidprog.lark -->"
_END = "<!-- END GENERATED GRAMMAR -->"


def _C(v):
    return ("const", v, 2)


_FRAME = "frameprog 1\nplay $1000\ninit $0F00\nsub_1000() {\n  ret\n}\n"


def test_generated_grammar_block_matches_the_implementation():
    """A grammar change that is not reflected in the doc fails CI."""
    head, rest = DOC.read_text(encoding="utf-8").split(_BEGIN)
    body, tail = rest.split(_END)
    want = "\n%s\n" % G.doc_block()
    if body != want and os.environ.get("SYNC_GRAMMAR_DOC"):
        DOC.write_text(head + _BEGIN + want + _END + tail, encoding="utf-8")
        return
    assert body == want, "docs/grammar.md is stale: SYNC_GRAMMAR_DOC=1 pytest tests/test_grammar.py"


def test_the_parser_serves_the_one_dialect():
    """The sidprog document dialect is retired: only frameprog parses."""
    assert frameprog.parse(_FRAME).play == 0x1000
    assert G.parse_document(_FRAME).dialect == "frameprog"
    with pytest.raises(ValueError):
        frameprog.parse(_FRAME.replace("frameprog 1", "sidprog 1", 1))


@pytest.mark.parametrize("major", ["7", "0"])
def test_off_major_fails_cleanly(major):
    """3a bumped frameprog to 1: a major-0 artifact predates image/dispatch/evidence."""
    with pytest.raises(G.SidprogVersionError):
        frameprog.parse(_FRAME.replace("frameprog 1", "frameprog " + major, 1))


_HDR = "frameprog 1\nplay $1000\ninit $0F00\n"
_SUB = "sub_1000() {\n  ret\n}\n"


def test_digit_only_byte_rows_are_not_integers():
    """Contextual lexing: packed byte rows never collide with sizes/strides."""
    doc = (
        _HDR + "image {\n $1000: 12345678\n}\n"
        "data {\n table m_2000[8] stride 2:\n  90210042\n  0102FF00\n}\n" + _SUB
    )
    prog = frameprog.loads(doc)
    assert prog.mem0[0x1000:0x1004] == b"\x12\x34\x56\x78"
    assert prog.mem0[0x2000:0x2008] == b"\x90\x21\x00\x42\x01\x02\xff\x00"
    assert prog.data_decls[0]["stride"] == 2
    assert frameprog.dumps(frameprog.loads(frameprog.dumps(prog))) == frameprog.dumps(prog)


def test_mutable_record_offsets_round_trip():
    """``mut`` carries the play-written offsets of a declaration's record."""
    doc = (
        _HDR + "data {\n table m_2000[8] stride 2 mut 0:\n  90210042\n  0102FF00\n"
        " table m_2008[4] mut 1 3:\n  01020304\n}\n" + _SUB
    )
    prog = frameprog.loads(doc)
    assert [d["mut"] for d in prog.data_decls] == [[0], [1, 3]]
    text = frameprog.dumps(prog)
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert "table m_2000[8] stride 2 mut 0:" in text


def test_keywords_come_from_the_grammar():
    """Keywords are grammar terminals; no layer keeps its own word list."""
    kw = G.keywords()
    assert {"if", "ifnot", "loop", "for", "in", "switch", "case", "goto", "igoto"} <= kw
    assert {"mem", "carry", "zext1", "zext2", "unobserved", "state", "inputs"} <= kw
    assert {"@1", "@t1", "@x"}.isdisjoint(kw)  # the cycle annotations went with sidprog
    assert not hasattr(frameprog, "_LINT_WORDS")
    for bad in ("loop", "carry", "state", "u3", "t0", "r5", "A", "m_1234"):
        with pytest.raises(ValueError, match="shadow"):
            G.check_alias(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "()",  # redundant parentheses: a chain needs an operator
        "($01 + $02",  # unbalanced
        "($01 + $02 & $03)",  # mixed operators
        "$01 $02",  # trailing tokens
        "nosuch[x]",  # an index base is a canonical cell name, not a local
        "*nosuch[x]",  # so is a deref base
    ],
)
def test_bad_expressions_rejected(bad):
    with pytest.raises(ValueError):
        G.parse_expression(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "frameprog 1\nplay $1000\nsub_1000() {\n  ret\n}\n",  # no init
        "frameprog 1\nplay $1000\ninit $0F00\nsymbols {\n alias a = nosuch\n}\n",
        "frameprog 1\nplay $1000\ninit $0F00\nsymbols {\n alias p = m_1000\n alias q = m_1000\n}\n",
        "frameprog 1\nplay $1000\ninit $0F00\nsub_XXXX() {\n  ret\n}\n",  # not a sub name
        "frameprog 1\nplay $1000\ninit $0F00\nstate {\n m_1000: u24\n}\n",  # unknown type
        "frameprog 1\nplay $1000\ninit $0F00\nsub_1000() {\n m_1000:2 = $01\n ret\n}\n",  # width
        "sidprog 1\nplay $1000\ninit $0F00\nproc $1000 {\n ret\n}\n",  # the retired dialect
        "frameprog 1\nplay $1000\ninit $0F00\nproc $1000 {\n ret\n}\n",  # a sidprog proc
        "frameprog 1\nplay $1000\ninit $0F00\nsub_1000() {\n @2 A = $00\n ret\n}\n",  # a cycle
    ],
)
def test_bad_documents_rejected(bad):
    with pytest.raises(ValueError):
        frameprog.parse(bad)


@pytest.mark.parametrize(
    "text,base,idx",
    [
        ("m_1500[x]", 0x1500, ("loc", "x")),
        ("m_1500[(x + $01)]", 0x1500, ("op", "INT_ADD", (("loc", "x"), ("const", 1, 1)), 1)),
        ("m_1500[t3]", 0x1500, ("loc", "t3")),
        ("m_1500[m_1600]", 0x1500, ("mem", ("const", 0x1600, 2), 1)),
        ("sid.v1.freq_hi[x]", 0xD401, ("loc", "x")),
    ],
)
def test_indexed_access_carries_any_index_expression(text, base, idx):
    """``base[index]`` reads ``base + zext2(index)`` for an arbitrary index."""
    n = G.parse_expression(text)
    assert G.addr_name(base) == text.split("[", 1)[0]
    assert n == (
        "mem",
        ("op", "INT_ADD", (("op", "INT_ZEXT", (idx,), 2), ("const", base, 2)), 2),
        1,
    )


def test_indexed_store_target_carries_an_index_expression():
    """The same production serves the lvalue: a computed store names its base."""
    doc = "frameprog 1\nplay $1000\ninit $0F00\nsub_1000() {\n  m_1500[(X + $01)] = $07\n  ret\n}\n"
    prog = frameprog.parse(doc)
    st = prog.procs[0][3][0]
    assert st[0] == "st" and st[1][2][1] == ("const", 0x1500, 2)
    assert frameprog.dumps(frameprog.loads(frameprog.dumps(prog))) == frameprog.dumps(prog)


# ---- named machine state: the bijection and the indexed sugar ------------------
def test_cell_naming_is_a_total_bijection():
    """Every 16-bit address has exactly one canonical name and back."""
    seen = set()
    for a in range(0x10000):
        name = G.addr_name(a)
        assert name not in seen
        seen.add(name)
        assert G.name_addr(name) == a
        if 0xD400 <= a <= 0xD418:
            assert name == sid_name(a)


@pytest.mark.parametrize("bad", ["m_D400", "m_00FB", "zp_5", "zp_fb", "m_12345", "sid.v4.ctrl"])
def test_non_canonical_names_rejected(bad):
    assert G.name_addr(bad) is None


@pytest.mark.parametrize(
    "e,text",
    [
        (("mem", ("const", 0xD404, 2), 1), "sid.v1.ctrl"),
        (("mem", ("const", 0xFB, 2), 1), "zp_FB"),
        (("mem", ("const", 0xFB, 2), 2), "zp_FB:2"),
        (("mem", ("const", 0xFB, 1), 1), "mem[$FB]"),  # width-1 const: raw
        (
            ("mem", ("op", "INT_ADD", (("op", "INT_ZEXT", (("loc", "x"),), 2), _C(0x1234)), 2), 1),
            "m_1234[x]",
        ),
        (("mem", ("op", "INT_SUB", (_C(0x1234), ("loc", "x", 2)), 2), 1), "mem[($1234 - x:2):2]"),
    ],
)
def test_named_and_indexed_forms(e, text):
    assert frameproc._fmt(e) == text
    assert G.parse_expression(text) == e


# ---- the expression round trip, over generated expressions ---------------------
_SZ = st.sampled_from((1, 2))
_CMP = st.sampled_from(("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL"))
_SHIFT = st.sampled_from(("INT_LEFT", "INT_RIGHT"))
_LOGIC = st.sampled_from(("INT_OR", "INT_XOR", "INT_AND"))

_const = st.one_of(
    st.integers(0, 0xFF).map(lambda v: ("const", v, 1)),
    st.integers(0, 0xFFFF).map(lambda v: ("const", v, 2)),
)
# slots stand in for locals: E.simplify normalises the tree, then they become names
_slot = st.tuples(st.integers(0, 7), _SZ).map(lambda t: ("uni", t[0], t[1]))
_named_mem = st.tuples(st.integers(0, 0xFFFF), _SZ).map(lambda t: ("mem", _C(t[0]), t[1]))
_indexed_mem = st.tuples(st.integers(0x100, 0xCFFF), st.integers(0, 7)).map(
    lambda t: (
        "mem",
        ("op", "INT_ADD", (("op", "INT_ZEXT", (("uni", t[1], 1),), 2), _C(t[0])), 2),
        1,
    )
)
_atom = st.one_of(_const, _slot, _named_mem, _indexed_mem)


def _extend(kids):
    nonconst = kids.filter(lambda n: n[0] != "const")
    return st.one_of(
        kids.map(lambda a: ("mem", a, 1)),
        st.tuples(kids, _SZ).map(lambda t: ("op", "INT_ZEXT", (t[0],), t[1])),
        st.tuples(kids, _SZ).map(lambda t: ("op", "COPY", (t[0],), t[1])),
        st.tuples(kids, kids).map(lambda t: ("op", "INT_CARRY", t, 1)),
        st.tuples(st.lists(kids, min_size=2, max_size=3), _SZ).map(
            lambda t: ("op", "INT_ADD", tuple(t[0]), t[1])
        ),
        st.tuples(kids, nonconst, _SZ).map(lambda t: ("op", "INT_SUB", (t[0], t[1]), t[2])),
        st.tuples(kids, kids, _SZ, _SHIFT).map(lambda t: ("op", t[3], (t[0], t[1]), t[2])),
        st.tuples(kids, kids, _CMP).map(lambda t: ("op", t[2], (t[0], t[1]), 1)),
        st.tuples(st.lists(kids, min_size=2, max_size=3), _SZ, _LOGIC).map(
            lambda t: ("op", t[2], tuple(t[0]), t[1])
        ),
    )


def _to_loc(n):
    """Every slot becomes the named local of its width; nothing else moves."""
    if n[0] == "uni":
        return ("loc", "t%d" % n[1]) if n[2] == 1 else ("loc", "t%d" % n[1], 2)
    if n[0] == "mem":
        return ("mem", _to_loc(n[1]), n[2])
    if n[0] == "op":
        return ("op", n[1], tuple(_to_loc(k) for k in n[2]), n[3])
    return n


@settings(max_examples=200, deadline=None)
@given(st.recursive(_atom, _extend, max_leaves=6).map(E.simplify).map(_to_loc))
def test_expression_text_is_a_canonical_fixpoint(e):
    """Expression text re-prints byte-identically and re-parses to one tree.

    The printer normalises (a byte local under an index widens, either operand
    order names the base), so the law is the artifact's: the text is the fixpoint
    and the parse of it is stable."""
    text = frameproc._fmt(e)
    back = G.parse_expression(text)
    assert frameproc._fmt(back) == text
    assert G.parse_expression(frameproc._fmt(back)) == back


# ---- data { } declarations and symbols { } aliases (document laws) -------------
def test_data_symbols_handwritten_roundtrip():
    """A handwritten data/symbols document parses, reconstructs mem0 from the
    declared bytes, and re-emits as a fixpoint (dispatch/cmp attrs included)."""
    doc = _HDR + "\n".join(
        (
            "image {",
            " $1000: A900",
            "}",
            "data {",
            " stream m_2000[4] via zp_60 cmp $01 $02 dispatch $1234 observed:",
            "  0102FF00",
            "}",
            "symbols {",
            " alias ctr_1440 = m_1440",
            "}",
            "sub_1000() {",
            "  ctr_1440 = $05",
            "  ret",
            "}",
            "",
        )
    )
    prog = frameprog.loads(doc)
    assert prog.mem0[0x2000:0x2004] == b"\x01\x02\xff\x00"
    assert prog.symbols == {0x1440: "ctr_1440"}
    (d,) = prog.data_decls
    assert d["cmp"] == [1, 2] and d["dispatch"] == [0x1234] and d["via"] == 0x60
    text = frameprog.dumps(prog)
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert "ctr_1440 = $05" in text


def test_data_byte_count_mismatch_rejected():
    """A declaration's extent is its byte count: a short row is not a declaration."""
    with pytest.raises(ValueError):
        frameprog.loads(_HDR + "data {\n table m_2000[4]:\n  0102\n}\n" + _SUB)
