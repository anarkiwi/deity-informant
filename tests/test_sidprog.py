"""sidprog structured language: walk equality on the fuzz corpus, round-trip
laws (canonical fixpoint, structural identity) over generated models, version
policy, and full-length real-tune acceptance with the CSE size gate."""

from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from deity_informant import expr as E
from deity_informant import sidprog
from deity_informant import structured as S
from deity_informant.c64 import load_psid
from deity_informant.cli import format_insn
from deity_informant.structured import Block

import _fuzzgen as G

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"

_PLAYERS = G.players(3)
_IDS = [f"{p.name}-{p.seed[1]}" for p in _PLAYERS]


def _image(p):
    m = bytearray(0x10000)
    for a, v in p.image_data().items():
        m[a] = v
    if p.init is None:
        m[0x0F00] = 0x60  # RTS: empty init
    return m


def _init(p):
    return p.init_org if p.init is not None else 0x0F00


def _verify(mem, init, play, frames, subtune=0):
    model, ev = S.decompile(mem, init, play, frames, subtune)
    assert model.prologue == ev.prologue
    w = S.Walker(model)
    assert w.run(frames) == ev.wlog
    assert bytes(w.m) == ev.end_mem
    text = sidprog.emit(model)
    tm = sidprog.parse(text)
    assert sidprog.emit(tm) == text  # canonical text is a parse/emit fixpoint
    assert tm.prologue == model.prologue
    tw = sidprog.TreeWalker(tm)  # tree-driven executor over the parsed structure
    assert tw.run(frames) == ev.wlog  # standalone text replay, cycle-stamped
    assert bytes(tw.m) == ev.end_mem
    return model, text


@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_fuzz_walker_bit_exact(p):
    """Every idiom class replays bit-exact from the model and from parsed text."""
    _verify(_image(p), _init(p), p.org, p.frames)


# ---- real tunes (full Songlengths duration; skip when the cache is absent) -----
def _tunes():
    return [
        pytest.param(path, sub, secs, id=f"{path.parent.name}-{path.stem}")
        for path, sub, secs in corpus_params(HVSC)
    ]


def _disasm_size(mem):
    nz = [a for a in range(0x10000) if mem[a]]
    lo, hi = min(nz), max(nz)
    total = 0
    pc = lo
    while pc <= hi:
        try:
            length, text = format_insn(mem, pc)
        except Exception:  # pylint: disable=broad-except
            length, text = 1, "$%04X: .byte" % pc
        total += len(text) + 1
        pc += length
    return total


@pytest.mark.parametrize("sid,subtune,secs", _tunes())
def test_real_tune_full_length_cycle_exact(sid, subtune, secs):
    """Acceptance: full-length (cycle, reg, value) log bit-exact from model and
    from parsed standalone text; text smaller than the disassembly listing."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    frames = int(secs * 50)
    model, text = _verify(mem, init, play, frames, subtune)
    assert model.dispatch_sets is not None
    assert len(text) < _disasm_size(model.mem0)
    mt = sidprog.metrics(model)  # reporting only: no structuring threshold yet
    assert mt["blocks"] > 0 and 0.0 <= mt["structured_pct"] <= 100.0


# ---- property-based round-trip laws over generated models ----------------------
_SZ = st.sampled_from((1, 2))
_CMP = st.sampled_from(("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL"))
_SHIFT = st.sampled_from(("INT_LEFT", "INT_RIGHT"))
_LOGIC = st.sampled_from(("INT_OR", "INT_XOR", "INT_AND"))

_const = st.one_of(
    st.integers(0, 0xFF).map(lambda v: ("const", v, 1)),
    st.integers(0, 0xFFFF).map(lambda v: ("const", v, 2)),
)
# named cell (mem[const:2]) and indexed array (mem[zext2(reg) + const:2 >= $100]) forms
_named_mem = st.integers(0, 0xFFFF).map(lambda a: ("mem", ("const", a, 2), 1))
_indexed_mem = st.tuples(st.integers(0x100, 0xFFFF), st.integers(0, 15)).map(
    lambda t: (
        "mem",
        ("op", "INT_ADD", (("op", "INT_ZEXT", (("reg", t[1]),), 2), ("const", t[0], 2)), 2),
        1,
    )
)
_atom = st.one_of(
    _const,
    st.integers(0, 15).map(lambda i: ("reg", i)),
    st.tuples(st.integers(0, 31), _SZ).map(lambda t: ("uni", t[0], t[1])),
    _named_mem,
    _indexed_mem,
)


def _extend(kids):
    nonconst = kids.filter(lambda n: n[0] != "const")
    return st.one_of(
        kids.map(lambda a: ("mem", a, 1)),
        st.tuples(kids, _SZ).map(lambda t: ("op", "INT_ZEXT", (t[0],), t[1])),
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


_expr = st.recursive(_atom, _extend, max_leaves=6).map(E.simplify)
_ADDR = st.integers(0x0800, 0xBFFF)
_ENTRY = st.integers(0, 0x07FF)  # outside the block pool: header provenance only


def _canon_events(raw):
    out = []
    for ev in raw:
        if ev[0] == "cyc" and out and out[-1][0] == "cyc":
            out[-1] = ("cyc", out[-1][1] + ev[1])
        else:
            out.append(ev)
    return [ev for ev in out if ev[0] != "cyc" or ev[1]]


_event = st.one_of(
    st.integers(1, 8).map(lambda k: ("cyc", k)),
    st.tuples(st.integers(0, 31), _expr).map(lambda t: ("ld", t[0], t[1])),
    st.tuples(_expr, _expr).map(lambda t: ("st", t[0], t[1])),
    st.tuples(st.sampled_from(("ax", "iy")), _expr, _expr).map(lambda t: ("pen", t[0], t[1], t[2])),
)

# calls stay dynamic: static jsr targets would become codec-verified entries
_TERM = st.one_of(
    _ADDR.map(lambda a: ("goto", a)),
    st.tuples(st.integers(0, 1), _ADDR, _ADDR, _expr).map(
        lambda t: ("br", t[0], t[1], t[2], t[3], None)
    ),
    st.tuples(st.integers(0, 1), _ADDR, _expr, _expr).map(
        lambda t: ("br", t[0], None, t[1], t[2], t[3])
    ),
    _expr.map(lambda e: ("jmpd", e)),
    _ADDR.map(lambda a: ("jmpind", a, None)),
    _expr.map(lambda e: ("jmpind", None, e)),
    st.tuples(_ADDR, _expr).map(lambda t: ("jsr", None, t[0], t[1])),
    st.just(("rts",)),
)


@st.composite
def _block(draw, pc, op0):
    events = _canon_events(draw(st.lists(_event, max_size=4)))
    term = draw(_TERM)
    regs = [draw(st.one_of(st.just(("reg", i)), _expr)) for i in range(16)]
    return Block(pc, op0, [pc], events, term, regs)


@st.composite
def _model(draw):
    n_plain = draw(st.integers(0, 5))
    n_disp = draw(st.integers(0, 2))
    total = n_plain + n_disp
    addrs = draw(st.lists(_ADDR, min_size=total, max_size=total, unique=True))
    plain, disp = addrs[:n_plain], addrs[n_plain:]
    mem0 = bytearray(0x10000)
    blocks = {}
    dispatch_sets = {}
    for pc in plain:
        op0 = draw(st.integers(0, 255))
        mem0[pc] = op0
        blocks[(pc, op0)] = draw(_block(pc, op0))
    for pc in disp:
        opset = draw(st.sets(st.integers(0, 255), min_size=1, max_size=4))
        dispatch_sets[pc] = set(opset)
        for op0 in draw(st.lists(st.sampled_from(sorted(opset)), max_size=4, unique=True)):
            blocks[(pc, op0)] = draw(_block(pc, op0))
    for addr, val in draw(st.lists(st.tuples(_ADDR, st.integers(1, 255)), max_size=8)):
        if addr not in plain:
            mem0[addr] = val
    prologue = draw(st.lists(st.tuples(st.integers(0, 0x1F), st.integers(0, 255)), max_size=4))
    return sidprog.TextModel(
        mem0,
        draw(_ENTRY),
        draw(_ENTRY),
        blocks,
        dispatch_sets,
        draw(st.integers(0, 255)),
        prologue,
    )


@settings(max_examples=100, deadline=None)
@given(_model())
def test_roundtrip_fixpoint(m):
    """dumps is a canonical fixpoint: loads(dumps(m)) re-emits byte-identically,
    and the parsed tree model preserves every header field."""
    text = sidprog.dumps(m)
    back = sidprog.loads(text)
    assert sidprog.dumps(back) == text
    assert (back.init, back.play, back.subtune) == (m.init, m.play, m.subtune)
    assert back.prologue == m.prologue and back.mem0 == bytes(m.mem0)
    assert back.dispatch_sets == m.dispatch_sets
    back.link()  # every parsed tree resolves to a flat control program


@settings(max_examples=100, deadline=None)
@given(_expr)
def test_expr_roundtrip(e):
    assert sidprog.parse_expr(sidprog.fmt_expr(e)) == e


def test_dumps_loads_are_emit_parse():
    assert sidprog.dumps is sidprog.emit and sidprog.loads is sidprog.parse


# ---- named machine state: bijection and indexed sugar --------------------------
def test_cell_naming_is_a_total_bijection():
    """Every 16-bit address has exactly one canonical name and back."""
    from deity_informant.render import sid_name

    seen = set()
    for a in range(0x10000):
        name = sidprog._addr_name(a)
        assert name not in seen
        seen.add(name)
        assert sidprog._name_addr(name) == a
        if 0xD400 <= a <= 0xD418:
            assert name == sid_name(a)


def test_non_canonical_names_rejected():
    for bad in ("m_D400", "m_00FB", "zp_5", "zp_fb", "m_12345", "sid.v4.ctrl"):
        assert sidprog._name_addr(bad) is None


@pytest.mark.parametrize(
    "e,text",
    [
        (("mem", ("const", 0xD404, 2), 1), "sid.v1.ctrl"),
        (("mem", ("const", 0xFB, 2), 1), "zp_FB"),
        (("mem", ("const", 0xFB, 1), 1), "mem[$FB]"),  # width-1 const: raw
        (
            (
                "mem",
                ("op", "INT_ADD", (("op", "INT_ZEXT", (("reg", 1),), 2), ("const", 0x1234, 2)), 2),
                1,
            ),
            "m_1234[X]",
        ),
        (  # const-first operand order is not canonical: raw fallback
            (
                "mem",
                ("op", "INT_ADD", (("const", 0x1234, 2), ("op", "INT_ZEXT", (("reg", 1),), 2)), 2),
                1,
            ),
            "mem[($1234 + zext2(X)):2]",
        ),
    ],
)
def test_named_and_indexed_forms(e, text):
    assert sidprog.fmt_expr(e) == text
    assert sidprog.parse_expr(text) == e


def test_named_store_and_load_lines_roundtrip():
    """A block whose payload uses every memref form survives dumps/loads."""
    zx = ("op", "INT_ZEXT", (("reg", 1),), 2)
    idx = ("op", "INT_ADD", (zx, ("const", 0x1500, 2)), 2)
    blk = Block(
        0x1000,
        0xA9,
        [0x1000],
        [
            ("ld", 0, ("const", 0xFB, 2)),
            ("ld", 1, idx),
            ("st", ("const", 0xD404, 2), ("uni", 0, 1)),
            ("st", idx, ("uni", 1, 1)),
        ],
        ("rts",),
        [E.reg(i) for i in range(16)],
    )
    mem0 = bytearray(0x10000)
    mem0[0x1000] = 0xA9
    m = sidprog.TextModel(mem0, 0x0F00, 0x1000, {(0x1000, 0xA9): blk}, {})
    text = sidprog.dumps(m)
    assert "u0 = zp_FB" in text
    assert "u1 = m_1500[X]" in text
    assert "sid.v1.ctrl = u0" in text
    assert "m_1500[X] = u1" in text
    assert sidprog.dumps(sidprog.loads(text)) == text


def test_metrics_reporting():
    mem0 = bytearray(0x10000)
    mem0[0x1000] = 0xA9
    blk = Block(0x1000, 0xA9, [0x1000], [], ("rts",), [E.reg(i) for i in range(16)])
    m = sidprog.TextModel(mem0, 0x0F00, 0x1000, {(0x1000, 0xA9): blk}, {})
    mt = sidprog.metrics(m)
    assert set(mt) == {"blocks", "nested_blocks", "structured_pct", "goto_count", "labels"}
    assert mt["blocks"] == 1 and mt["goto_count"] == 0
    assert 0.0 <= mt["structured_pct"] <= 100.0


def test_header_carries_current_version():
    m = sidprog.TextModel(bytearray(0x10000), 0x1000, 0x1003, {}, {})
    assert sidprog.dumps(m).startswith("sidprog %d\n" % sidprog.SIDPROG_VERSION)


def test_future_major_fails_cleanly():
    m = sidprog.TextModel(bytearray(0x10000), 0x1000, 0x1003, {}, {})
    future = sidprog.dumps(m).replace("sidprog %d" % sidprog.SIDPROG_VERSION, "sidprog 999", 1)
    with pytest.raises(sidprog.SidprogVersionError):
        sidprog.loads(future)


@pytest.mark.parametrize("bad", ["", "not a sidprog doc\n", "sidprog\n", "sidprog x\nplay $1000\n"])
def test_non_sidprog_document_rejected(bad):
    with pytest.raises(ValueError):
        sidprog.loads(bad)
