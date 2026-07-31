"""The shared grammar: one LALR(1) parser for both dialects, the doc/grammar
drift gate, dialect rejection, version policy and expression error paths."""

import os
from pathlib import Path

import pytest

from deity_informant import frameprog
from deity_informant import grammar as G
from deity_informant import sidprog

DOC = Path(__file__).resolve().parent.parent / "docs" / "grammar.md"
_BEGIN = "<!-- BEGIN GENERATED GRAMMAR: deity_informant/sidprog.lark -->"
_END = "<!-- END GENERATED GRAMMAR -->"

_SID = "sidprog 1\nplay $1000\ninit $0F00\nimage {\n $1000: 60\n}\nproc $1000 {\n ret\n}\n"
_FRAME = "frameprog 0\nplay $1000\ninit $0F00\nsub_1000() {\n  ret\n}\n"


def test_generated_grammar_block_matches_the_implementation():
    """A grammar change that is not reflected in the doc fails CI."""
    head, rest = DOC.read_text(encoding="utf-8").split(_BEGIN)
    body, tail = rest.split(_END)
    want = "\n%s\n" % G.doc_block()
    if body != want and os.environ.get("SYNC_GRAMMAR_DOC"):
        DOC.write_text(head + _BEGIN + want + _END + tail, encoding="utf-8")
        return
    assert body == want, "docs/grammar.md is stale: SYNC_GRAMMAR_DOC=1 pytest tests/test_grammar.py"


def test_one_parser_serves_both_dialects():
    assert sidprog.parse(_SID).play == 0x1000
    assert frameprog.parse(_FRAME).play == 0x1000
    assert G.parse_document(_SID).dialect == "sidprog"
    assert G.parse_document(_FRAME).dialect == "frameprog"


@pytest.mark.parametrize("reader,text", [(sidprog.parse, _FRAME), (frameprog.parse, _SID)])
def test_wrong_dialect_rejected(reader, text):
    with pytest.raises(ValueError, match="not a .*prog document"):
        reader(text)


@pytest.mark.parametrize(
    "reader,text",
    [
        (sidprog.parse, _SID.replace("sidprog 1", "sidprog 7", 1)),
        (frameprog.parse, _FRAME.replace("frameprog 0", "frameprog 7", 1)),
    ],
)
def test_future_major_fails_cleanly_in_both_dialects(reader, text):
    with pytest.raises(sidprog.SidprogVersionError):
        reader(text)


def test_digit_only_byte_rows_are_not_integers():
    """Contextual lexing: packed byte rows never collide with sizes/strides."""
    doc = (
        "sidprog 1\nplay $1000\ninit $0F00\nimage {\n $1000: 12345678\n}\n"
        "data {\n table m_2000[8] stride 2:\n  90210042\n  0102FF00\n}\n"
    )
    tm = sidprog.parse(doc)
    assert tm.mem0[0x1000:0x1004] == b"\x12\x34\x56\x78"
    assert tm.mem0[0x2000:0x2008] == b"\x90\x21\x00\x42\x01\x02\xff\x00"
    assert tm.data_decls[0]["stride"] == 2
    assert sidprog.dumps(sidprog.loads(sidprog.dumps(tm))) == sidprog.dumps(tm)


def test_mutable_record_offsets_round_trip():
    """``mut`` carries the play-written offsets of a declaration's record."""
    doc = (
        "sidprog 1\nplay $1000\ninit $0F00\n"
        "data {\n table m_2000[8] stride 2 mut 0:\n  90210042\n  0102FF00\n"
        " table m_2008[4] mut 1 3:\n  01020304\n}\n"
    )
    tm = sidprog.parse(doc)
    assert [d["mut"] for d in tm.data_decls] == [[0], [1, 3]]
    assert sidprog.dumps(sidprog.loads(sidprog.dumps(tm))) == sidprog.dumps(tm)
    assert "table m_2000[8] stride 2 mut 0:" in sidprog.dumps(tm)


def test_keywords_come_from_the_grammar():
    """Keywords are grammar terminals; no layer keeps its own word list."""
    kw = G.keywords()
    assert {"if", "ifnot", "loop", "for", "in", "switch", "case", "goto", "igoto"} <= kw
    assert {"mem", "carry", "zext1", "zext2", "unobserved", "state", "inputs"} <= kw
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
        "m_1234[Q]",  # not a register
        "nosuch[X]",  # not a canonical cell name
        "nosuch",
    ],
)
def test_bad_expressions_rejected(bad):
    with pytest.raises(ValueError):
        sidprog.parse_expr(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "sidprog 1\nplay $1000\ninit $0F00\nproc $1000 {\n u0 = $01\n}\n",  # load, no memref
        "sidprog 1\nplay $1000\ninit $0F00\nproc $1000 {\n nosuch = $01\n}\n",  # unknown lvalue
        "sidprog 1\nplay $1000\nproc $1000 {\n ret\n}\n",  # no init
        "sidprog 1\nplay $1000\ninit $0F00\nsymbols {\n alias a = nosuch\n}\n",
        "sidprog 1\nplay $1000\ninit $0F00\nsymbols {\n alias p = m_1000\n alias q = m_1000\n}\n",
        "frameprog 0\nplay $1000\ninit $0F00\nsub_XXXX() {\n  ret\n}\n",  # not a sub name
        "frameprog 0\nplay $1000\ninit $0F00\nstate {\n m_1000: u16\n}\n",  # unknown type
    ],
)
def test_bad_documents_rejected(bad):
    reader = sidprog.parse if bad.startswith("sidprog") else frameprog.parse
    with pytest.raises(ValueError):
        reader(bad)


@pytest.mark.parametrize(
    "text,base,idx",
    [
        ("m_1500[X]", 0x1500, ("reg", 1)),
        ("m_1500[(X + $01)]", 0x1500, ("op", "INT_ADD", (("reg", 1), ("const", 1, 1)), 1)),
        ("m_1500[u3]", 0x1500, ("uni", 3, 1)),
        ("m_1500[m_1600]", 0x1500, ("mem", ("const", 0x1600, 2), 1)),
        ("sid.v1.freq_hi[X]", 0xD401, ("reg", 1)),
    ],
)
def test_indexed_access_carries_any_index_expression(text, base, idx):
    """``base[index]`` reads ``base + zext2(index)`` for an arbitrary index."""
    n = sidprog.parse_expr(text)
    assert G.addr_name(base) == text.split("[", 1)[0]
    assert n == (
        "mem",
        ("op", "INT_ADD", (("op", "INT_ZEXT", (idx,), 2), ("const", base, 2)), 2),
        1,
    )


def test_indexed_store_target_carries_an_index_expression():
    """The same production serves the lvalue: a computed store names its base."""
    doc = "frameprog 0\nplay $1000\ninit $0F00\nsub_1000() {\n  m_1500[(X + $01)] = $07\n  ret\n}\n"
    prog = frameprog.parse(doc)
    st = prog.procs[0][3][0]
    assert st[0] == "st" and st[1][2][1] == ("const", 0x1500, 2)
    assert frameprog.dumps(frameprog.loads(frameprog.dumps(prog))) == frameprog.dumps(prog)
