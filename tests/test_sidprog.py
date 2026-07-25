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
    tm = sidprog.parse(text)  # parse re-runs codec.verify on the rebuilt model
    assert sidprog.emit(tm) == text  # canonical text is a parse/emit fixpoint
    assert tm.prologue == model.prologue
    tw = S.Walker(tm)
    assert tw.run(frames) == ev.wlog  # standalone text replay, cycle-stamped
    assert bytes(tw.m) == ev.end_mem
    return model, text


@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_fuzz_walker_bit_exact(p):
    """Every idiom class replays bit-exact from the model and from parsed text."""
    _verify(_image(p), _init(p), p.org, p.frames)


# ---- real tunes (full Songlengths duration; skip when the cache is absent) -----
_STEMS = {"Commando", "Automatas", "Krakout", "Ghouls_n_Ghosts"}


def _tunes():
    return [
        pytest.param(path, sub, secs, id=f"{path.parent.name}-{path.stem}")
        for path, sub, secs in corpus_params(HVSC)
        if path.stem in _STEMS
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
    """Full-length (cycle, reg, value) log bit-exact from model and parsed text;
    Ghouls_n_Ghosts additionally proves CSE beats the disassembly listing."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    frames = int(secs * 50)
    model, text = _verify(mem, init, play, frames, subtune)
    assert model.dispatch_sets is not None
    if sid.stem == "Ghouls_n_Ghosts":
        assert len(text) < _disasm_size(model.mem0)


# ---- property-based round-trip laws over generated models ----------------------
_SZ = st.sampled_from((1, 2))
_CMP = st.sampled_from(("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL"))
_SHIFT = st.sampled_from(("INT_LEFT", "INT_RIGHT"))
_LOGIC = st.sampled_from(("INT_OR", "INT_XOR", "INT_AND"))

_const = st.one_of(
    st.integers(0, 0xFF).map(lambda v: ("const", v, 1)),
    st.integers(0, 0xFFFF).map(lambda v: ("const", v, 2)),
)
_atom = st.one_of(
    _const,
    st.integers(0, 15).map(lambda i: ("reg", i)),
    st.tuples(st.integers(0, 31), _SZ).map(lambda t: ("uni", t[0], t[1])),
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


def _key(m):
    return (
        m.init,
        m.play,
        m.subtune,
        tuple(m.prologue),
        tuple((a, m.mem0[a]) for a in range(0x10000) if m.mem0[a]),
        {pc: frozenset(s) for pc, s in m.dispatch_sets.items()},
        {k: (tuple(b.events), b.term, tuple(b.regs)) for k, b in m.blocks.items()},
    )


@settings(max_examples=100, deadline=None)
@given(_model())
def test_roundtrip_identity_and_fixpoint(m):
    """loads(dumps(m)) == m structurally, and dumps is a canonical fixpoint."""
    text = sidprog.dumps(m)
    back = sidprog.loads(text)
    assert _key(back) == _key(m)
    assert sidprog.dumps(back) == text


def test_dumps_loads_are_emit_parse():
    assert sidprog.dumps is sidprog.emit and sidprog.loads is sidprog.parse


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
