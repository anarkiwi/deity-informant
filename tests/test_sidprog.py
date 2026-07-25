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


# expected data/symbols declaration fragments per tune id (ground-truth studies)
_DECL_TRUTH = {
    "Hubbard_Rob-Commando": [
        "table m_5428[192] stride 2 +m_5429 +m_542A +m_542B observed:",
        "table m_56F9[3] lo m_56FC -> $576B..$57EC observed:",
        "table m_56FC[3] hi m_56F9 -> $576B..$57EC observed:",
        " stride 8 ",  # instrument records at m_5591
        "stream m_576B[",
        "via zp_5D cmp $FE $FF observed:",
        "alias ptr_005D_lo = zp_5D",
        "alias ptr_005D_hi = zp_5E",
        "alias pos_54EC = m_54EC",
        "alias pos_54ED = m_54ED",
        "alias pos_54EE = m_54EE",
    ],
    "Cadaver-Aces_High": [
        "table m_155C[52] lo m_1590",
        "stream m_15C4[",
        "via zp_FB cmp $00 $FE $FF observed:",
        "alias ptr_00FB_lo = zp_FB",
    ],
    "Follin_Tim-Ghouls_n_Ghosts": [
        "stream m_7338[",
        "stream m_75F7[",
        "stream m_77A8[",
        "via zp_21 ",
        "via zp_23 ",
        "via zp_25 ",
        "alias ptr_0021_lo = zp_21",
    ],
}


def _check_partition(model, text, tm):
    """Data regions and image rows partition mem0 exactly (no byte in both)."""
    assert tm.mem0 == model.mem0
    cov = set()
    for d in tm.data_decls:
        span = set(range(d["base"], d["base"] + d["size"]))
        assert not cov & span
        cov |= span
    lines = text.splitlines()
    for row in lines[lines.index("image {") + 1 :]:
        if row == "}":
            break
        addr, run = row.split(":", 1)
        a = int(addr.strip().lstrip("$"), 16)
        n = len(run.strip()) // 2
        assert not cov & set(range(a, a + n))


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
    _check_partition(model, text, sidprog.parse(text))
    for frag in _DECL_TRUTH.get("%s-%s" % (sid.parent.name, sid.stem), ()):
        assert frag in text, frag


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

# zero-compare conditions: the emit-side canonicalization must stay a fixpoint
_byte_atom = st.one_of(
    st.integers(0, 15).map(lambda i: ("reg", i)),
    st.integers(0, 31).map(lambda n: ("uni", n, 1)),
    _named_mem,
)
_zero_cmp = st.builds(
    lambda mn, lhs: ("op", mn, (lhs, ("const", 0, 1)), 1),
    st.sampled_from(("INT_EQUAL", "INT_NOTEQUAL")),
    st.one_of(
        st.tuples(_byte_atom, _byte_atom).map(lambda t: ("op", "INT_SUB", t, 1)),
        st.tuples(_byte_atom, st.integers(0, 255)).map(
            lambda t: ("op", "INT_ADD", (t[0], ("const", t[1], 1)), 1)
        ),
    ),
)
_cond = st.one_of(_expr, _zero_cmp)

# calls stay dynamic: static jsr targets would become codec-verified entries
_TERM = st.one_of(
    _ADDR.map(lambda a: ("goto", a)),
    st.tuples(st.integers(0, 1), _ADDR, _ADDR, _cond).map(
        lambda t: ("br", t[0], t[1], t[2], t[3], None)
    ),
    st.tuples(st.integers(0, 1), _ADDR, _cond, _expr).map(
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
    """A block whose payload uses every memref form survives dumps/loads;
    the single-use load inlines, the store-crossing load keeps its line."""
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
    assert "sid.v1.ctrl = zp_FB" in text  # single use, no store between: inlined
    assert "u1 = m_1500[X]" in text  # crosses the SID store: keeps its line
    assert "m_1500[X] = u1" in text
    assert sidprog.dumps(sidprog.loads(text)) == text


def _one_block_text(events, term=("rts",), regs=None):
    blk = Block(0x1000, 0xA9, [0x1000], events, term, regs or [E.reg(i) for i in range(16)])
    mem0 = bytearray(0x10000)
    mem0[0x1000] = 0xA9
    m = sidprog.TextModel(mem0, 0x0F00, 0x1000, {(0x1000, 0xA9): blk}, {})
    text = sidprog.dumps(m)
    assert sidprog.dumps(sidprog.loads(text)) == text
    return text


def _indexed(base, reg):
    zx = ("op", "INT_ZEXT", (("reg", reg),), 2)
    return ("op", "INT_ADD", (zx, ("const", base, 2)), 2)


def test_single_use_load_inlines_across_cyc_and_pen():
    """The load line vanishes; its cycle stamp coalesces into the consumer's."""
    idx = _indexed(0x5591, 2)
    text = _one_block_text(
        [
            ("cyc", 2),
            ("ld", 0, idx),
            ("cyc", 4),
            ("pen", "ax", ("const", 0x5591, 2), ("reg", 2)),
            ("cyc", 4),
            ("st", ("const", 0x01FD, 2), ("op", "INT_SUB", (("uni", 0, 1), ("reg", 0)), 1)),
        ]
    )
    assert "u0" not in text
    assert "@6 @x($5591, Y)" in text
    assert "@4 m_01FD = (m_5591[Y] - A)" in text


def test_volatile_and_near_volatile_loads_keep_their_lines():
    t1 = _one_block_text(
        [("ld", 0, ("const", 0xD012, 2)), ("st", ("const", 0x00FB, 2), ("uni", 0, 1))]
    )
    assert "u0 = m_D012" in t1  # volatile cell
    t2 = _one_block_text(
        [("ld", 0, _indexed(0xD400, 1)), ("st", ("const", 0x00FB, 2), ("uni", 0, 1))]
    )
    assert "u0 = sid.v1.freq_lo[X]" in t2  # index window reaches $D41B/$D41C


def test_multi_use_load_keeps_its_line():
    u0 = ("uni", 0, 1)
    text = _one_block_text(
        [("ld", 0, _indexed(0x1500, 1)), ("st", ("const", 0xFB, 2), u0)],
        regs=[u0] + [E.reg(i) for i in range(1, 16)],
    )
    assert "u0 = m_1500[X]" in text


def test_condition_canonicalizes_to_direct_compare():
    """Sub/add compare-to-zero in condition position becomes a direct compare."""
    a, b = ("mem", ("const", 0xFB, 2), 1), ("reg", 0)
    sub0 = ("op", "INT_EQUAL", (("op", "INT_SUB", (a, b), 1), ("const", 0, 1)), 1)
    add0 = (
        "op",
        "INT_NOTEQUAL",
        (("op", "INT_ADD", (b, ("const", 0xF8, 1)), 1), ("const", 0, 1)),
        1,
    )
    t1 = _one_block_text([], term=("br", 1, None, 0x1005, sub0, ("const", 0x1000, 2)))
    assert "if (zp_FB == A) goto ($1000) else $1005" in t1
    t2 = _one_block_text([], term=("br", 0, None, 0x1005, add0, ("const", 0x1000, 2)))
    assert "ifnot (A != $08) goto ($1000) else $1005" in t2


def test_condition_width_mismatch_not_canonicalized():
    wide = ("op", "INT_SUB", (("uni", 0, 2), ("uni", 1, 2)), 2)
    cond = ("op", "INT_EQUAL", (wide, ("const", 0, 1)), 1)
    text = _one_block_text([], term=("br", 1, None, 0x1005, cond, ("const", 0x1000, 2)))
    assert "((u0:2 - u1:2):2 == $00)" in text


def test_metrics_reporting():
    mem0 = bytearray(0x10000)
    mem0[0x1000] = 0xA9
    blk = Block(0x1000, 0xA9, [0x1000], [], ("rts",), [E.reg(i) for i in range(16)])
    m = sidprog.TextModel(mem0, 0x0F00, 0x1000, {(0x1000, 0xA9): blk}, {})
    mt = sidprog.metrics(m)
    assert set(mt) == {
        "blocks",
        "nested_blocks",
        "structured_pct",
        "goto_count",
        "labels",
        "dup_blocks",
    }
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


# ---- data { } declarations + symbols { } aliases (serialization only) ----------
_D_ORG, _D_TBL, _D_WTBL, _D_CNT, _D_PTR = 0x1000, 0x1400, 0x1480, 0x1440, 0x60
_D_PLO, _D_PHI, _D_PATA, _D_PATB = 0x1500, 0x1508, 0x1520, 0x1530


def _decl_player():
    """Counter-indexed tables (proven + record stride), a reloaded pointer
    pair walking command streams, and role-classified state cells."""
    a = G.Asm(_D_ORG)
    a.i("LDX", "abs", _D_CNT)
    a.i("LDA", "absx", _D_PLO).i("STA", "zp", _D_PTR)
    a.i("LDA", "absx", _D_PHI).i("STA", "zp", _D_PTR + 1)
    a.i("LDY", "abs", _D_CNT + 1)
    a.i("LDA", "indy", _D_PTR).i("CMP", "imm", 1).i("BNE", "rel", ("L", "n1"))
    a.i("LDA", "imm", 0x41)
    a.label("n1").i("STA", "abs", G.SID + 4)
    a.i("LDA", "abs", _D_CNT + 2).i("CLC").i("ADC", "imm", 1).i("STA", "abs", _D_CNT + 2)
    a.i("AND", "imm", 3).i("TAX")
    a.i("LDA", "absx", _D_TBL).i("STA", "abs", G.SID)
    a.i("LDA", "abs", _D_CNT + 2).i("AND", "imm", 3).i("ASL", "acc").i("TAX")
    a.i("LDA", "absx", _D_WTBL).i("STA", "abs", G.SID + 2)
    a.i("LDA", "absx", _D_WTBL + 1).i("STA", "abs", G.SID + 3)
    a.i("INC", "abs", _D_CNT + 1).i("LDA", "abs", _D_CNT + 1).i("CMP", "imm", 3)
    a.i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", 0).i("STA", "abs", _D_CNT + 1)
    a.i("INC", "abs", _D_CNT).i("LDA", "abs", _D_CNT).i("CMP", "imm", 2)
    a.i("BNE", "rel", ("L", "out"))
    a.i("LDA", "imm", 0).i("STA", "abs", _D_CNT)
    a.label("out").i("RTS")
    data = {
        _D_PLO: _D_PATA & 0xFF,
        _D_PLO + 1: _D_PATB & 0xFF,
        _D_PHI: _D_PATA >> 8,
        _D_PHI + 1: _D_PATB >> 8,
    }
    data.update({_D_PATA + k: v for k, v in enumerate((1, 2, 1))})
    data.update({_D_PATB + k: 0x11 + k for k in range(3)})
    data.update({_D_TBL + k: 0x30 + k for k in range(4)})
    data.update({_D_WTBL + k: 0x50 + k for k in range(8)})
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60  # init: RTS
    for k, b in enumerate(a.assemble()):
        mem[_D_ORG + k] = b
    for addr, v in data.items():
        mem[addr] = v
    return S.decompile(mem, 0x0F00, _D_ORG, 14)


def test_data_declarations_and_aliases():
    """Typed declarations carve the song data (extent-honest) and the alias
    bijection renames classified state; all serialization laws hold."""
    model, ev = _decl_player()
    text = sidprog.emit(model)
    assert "table m_1400[4]:" in text  # proven index domain: no observed marker
    assert "table m_1480[8] stride 2 +m_1481:" in text
    assert "table m_1500[2] lo m_1508 -> $1520..$1530 observed:" in text
    assert "table m_1508[2] hi m_1500 -> $1520..$1530 observed:" in text
    assert "stream m_1520[3] via zp_60 cmp $01 observed:" in text
    assert "alias ptr_0060_lo = zp_60" in text and "alias ptr_0060_hi = zp_61" in text
    assert "alias pos_1441 = m_1441" in text  # row position of the deref index
    assert "ptr_0060_lo = " in text and "zp_60 = " not in text  # body uses aliases
    tm = sidprog.parse(text)
    assert sidprog.emit(tm) == text
    _check_partition(model, text, tm)
    assert sidprog.TreeWalker(tm).run(14) == ev.wlog  # declarations move bytes only


def test_data_symbols_handwritten_roundtrip():
    """A handwritten data/symbols document parses, reconstructs mem0 from the
    declared bytes, and re-emits as a fixpoint (dispatch/cmp attrs included)."""
    doc = "\n".join(
        (
            "sidprog 1",
            "play $1000",
            "init $0F00",
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
            "proc $1000 {",
            "  @2 A = $00",
            "  ctr_1440 = $05",
            "  ret",
            "}",
        )
    )
    tm = sidprog.loads(doc)
    assert tm.mem0[0x2000:0x2004] == b"\x01\x02\xff\x00"
    assert tm.symbols == {0x1440: "ctr_1440"}
    (d,) = tm.data_decls
    assert d["cmp"] == [1, 2] and d["dispatch"] == [0x1234] and d["via"] == 0x60
    text = sidprog.dumps(tm)
    assert sidprog.dumps(sidprog.loads(text)) == text
    assert "ctr_1440 = $05" in text


def test_alias_shadowing_rejected():
    doc = "sidprog 1\nplay $1000\ninit $0F00\nsymbols {\n alias m_1234 = m_1240\n}\n"
    with pytest.raises(ValueError):
        sidprog.loads(doc)


def test_data_byte_count_mismatch_rejected():
    doc = "sidprog 1\nplay $1000\ninit $0F00\ndata {\n table m_2000[4]:\n  0102\n}\n"
    with pytest.raises(ValueError):
        sidprog.loads(doc)
