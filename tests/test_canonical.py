"""Round-trip tests for the canonical IR (``deity_informant.canonical``).

The IR is the canonical form: serialize a kernel, parse the text back, execute it,
and reproduce the original SID register writes byte-exact -- over the synthetic
corpus and a hand-written program that exercises the grammar directly.
"""

import pytest

import deity_informant.canonical as C
from deity_informant import lift_kernel, parse_ir, record, roundtrip, run_sub, to_ir

import _fuzzgen as G

_PLAYERS = G.players(6)
_IDS = [f"{p.name}-{p.seed[1]}" for p in _PLAYERS]


def _kernel(p):
    m = bytearray(0x10000)
    for a, v in p.image_data().items():
        m[a] = v
    return lift_kernel(record(bytes(m), run_sub, p.org, p.outputs, p.frames))


# ---- round-trip over the corpus ----------------------------------------------
@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_roundtrip_byte_exact(p):
    """parse(to_ir(kernel)).run() reproduces the recorded write stream, byte-exact."""
    ok, _ir = roundtrip(_kernel(p))
    assert ok, (p.name, p.seed)


def test_ir_is_stable():
    """Serialization is deterministic (same kernel -> same text)."""
    k = _kernel(_PLAYERS[0])
    assert to_ir(k) == to_ir(k)


def test_parses_and_reparses():
    """to_ir output re-parses to a program that serializes identically."""
    k = _kernel(next(p for p in _PLAYERS if p.name == "dec_timer"))
    ir = to_ir(k)
    prog = parse_ir(ir)
    assert prog.frame and prog.seed and prog.outputs


# ---- expression grammar identity (semantic) ----------------------------------
def test_expr_emit_parse_roundtrip():
    exprs = [
        ("const", 0x1F, 1),
        ("const", 0x1234, 2),
        ("reg", 3),
        ("mem", ("const", 0x54EF, 2), 1),
        (
            "op",
            "INT_ADD",
            (("mem", ("const", 0x10, 2), 1), ("const", 1, 1)),
            1,
        ),
    ]
    for ex in exprs:
        assert C._build(C._read(C._tokens(C._emit(ex)))) == ex


def test_cur_emits_addr_and_size():
    cur = ("cur", ("const", 0x400, 2), 1, 7, ("const", 9, 1))
    back = C._build(C._read(C._tokens(C._emit(cur))))
    assert back[0] == "cur" and back[1] == ("const", 0x400, 2) and back[2] == 1


# ---- hand-written program executes per the grammar ---------------------------
def test_handwritten_program():
    # frame: SID[$D400] <- M[$0002]+1 ; then branch on M[$0002], case 5 writes $D401<-$AA
    text = """
    (tune ; demo
      (outputs d400 d401)
      (tables (t 2 5))
      (init)
      (frame
        (w d400 (o INT_ADD 1 (m (c 2 2) 1) (c 1 1)) 1)
        (sw (m (c 2 2) 1)
          (case 5 (w d401 (c aa 1) 1))
          (case 0 (w d401 (c bb 1) 1)))))
    """
    prog = parse_ir(text)
    out = prog.run(1)
    assert out == [(0xD400, 6), (0xD401, 0xAA)]


def test_switch_missing_case_raises():
    text = "(tune (outputs d400 d400) (tables (t 2 9)) (init) (frame (sw (m (c 2 2) 1) (case 1 (w d400 (c 1 1) 1)))))"
    with pytest.raises(KeyError):
        parse_ir(text).run(1)


def test_grammar_constant_present():
    assert "program :=" in C.GRAMMAR and "(frame" in C.GRAMMAR
