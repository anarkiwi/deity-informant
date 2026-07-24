"""SIDL acceptance: byte-exact replay over the fuzz corpus, exact text
round-trip, generalization past the recorded window, and loud dispatch faults."""

import pytest

from deity_informant import sidl

import _fuzzgen as G

_PLAYERS = G.players(3)
_IDS = [f"{p.name}-{p.seed[1]}" for p in _PLAYERS]


def _image(cells):
    m = bytearray(0x10000)
    for a, v in cells.items():
        m[a] = v
    return m


def _build(p, frames=None):
    return sidl.build(
        _image(p.image_data()),
        p.org,
        frames if frames is not None else p.frames,
        init=p.init_org if p.init is not None else None,
        outputs=p.outputs,
    )


def _reference(p, frames=None):
    return sidl.reference_log(
        _image(p.image_data()),
        p.org,
        frames if frames is not None else p.frames,
        init=p.init_org if p.init is not None else None,
        outputs=p.outputs,
    )


@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_lossless_and_roundtrip(p):
    """dumps -> loads -> run reproduces the VM's ordered write stream, and the
    canonical text is a fixpoint of loads/dumps."""
    prog = _build(p)
    text = sidl.dumps(prog)
    prog2 = sidl.loads(text)
    assert sidl.dumps(prog2) == text
    assert prog2.run() == _reference(p), (p.name, p.seed)


@pytest.mark.parametrize("name", ["dec_timer", "multispeed", "table_index"])
def test_generalizes_past_recorded_window(name):
    """Once the path vocabulary is covered, dispatch-by-guards replays frames
    the recorder never saw."""
    p = next(q for q in _PLAYERS if q.name == name)
    prog = sidl.loads(sidl.dumps(_build(p)))
    total = p.frames * 3
    assert prog.run(total) == _reference(p, total)


def test_windowed_build_identical_to_monolithic():
    """Parallel windowed recording is a pure CPU-budget device: same text."""
    p = next(q for q in _PLAYERS if q.name == "dec_timer")
    mono = _build(p)
    windowed = sidl.build(
        _image(p.image_data()),
        p.org,
        p.frames,
        init=p.init_org,
        outputs=p.outputs,
        window=max(1, p.frames // 3),
    )
    assert sidl.dumps(windowed) == sidl.dumps(mono)


def test_volatile_carries_uni_rows():
    p = next(q for q in _PLAYERS if q.name == "volatile")
    prog = _build(p)
    assert prog.uni is not None and len(prog.uni) == p.frames
    assert sidl.loads(sidl.dumps(prog)).run() == _reference(p)


def test_data_bounded_player_faults_past_stream():
    """A finite-stream player faults loudly (never guesses) once frames read
    data bytes no recorded frame touched."""
    p = next(q for q in _PLAYERS if q.name == "varlen_row")
    prog = _build(p)
    assert prog.run() == _reference(p)
    with pytest.raises(sidl.DispatchError):
        prog.run(p.frames * 3)


def test_trie_dispatch_equals_linear_first_match():
    """Decision-trie dispatch selects the same template a linear first-match
    scan would, frame by frame."""
    p = next(q for q in _PLAYERS if q.name == "dec_timer")
    prog = _build(p)
    cells = dict(prog.cells)
    regs = list(prog.regs0)
    trie = sidl._build_trie(prog.templates)
    for _ in range(p.frames * 2):
        linear = next(
            r for r in (prog._try(t, cells, regs, {}) for t in prog.templates) if r is not None
        )
        via_trie = prog._dispatch(trie, cells, regs, {})
        assert via_trie == linear
        overlay, _writes, regs = via_trie
        cells.update(overlay)


def test_dispatch_fault_is_loud():
    p = next(q for q in _PLAYERS if q.name == "smc_operand")
    prog = _build(p, frames=2)
    prog.templates = prog.templates[:0]
    with pytest.raises(sidl.DispatchError):
        prog.run(1)


def test_missing_cell_fault_is_loud():
    p = next(q for q in _PLAYERS if q.name == "table_index")
    prog = _build(p)
    prog.cells = {a: v for a, v in prog.cells.items() if a < 0x1400 or a > 0x14FF}
    with pytest.raises(sidl.DispatchError):
        prog.run(1)


def test_expr_text_is_bijective():
    cases = [
        ("const", 0x1234, 2),
        ("reg", 3),
        ("uni", 7, 2),
        ("mem", ("const", 0x1400, 2), 1),
        (
            "cur",
            ("op", "INT_ADD", (("op", "INT_ZEXT", (("reg", 1),), 2), ("const", 0x1400, 2)), 2),
            1,
        ),
        ("op", "INT_ADD", (("reg", 0), ("const", 0xFF, 1)), 1),
        ("op", "INT_SUB", (("reg", 3), ("reg", 1)), 1),
        ("op", "INT_AND", (("reg", 0), ("reg", 1), ("const", 0x7F, 1)), 1),
        ("op", "INT_EQUAL", (("mem", ("const", 0x40, 2), 1), ("const", 0, 1)), 1),
        ("op", "INT_RIGHT", (("reg", 0), ("const", 7, 1)), 1),
        ("op", "INT_CARRY", (("reg", 0), ("uni", 0, 1)), 1),
    ]
    for n in cases:
        assert sidl.parse_expr(sidl.fmt_expr(n)) == n, n


def test_loads_rejects_garbage():
    with pytest.raises(ValueError):
        sidl.loads("not a program\n")
    with pytest.raises(ValueError):
        sidl.loads("sidl 0\nbogus $1000\n")
    with pytest.raises(ValueError):
        sidl.parse_expr("(A ? X)")
