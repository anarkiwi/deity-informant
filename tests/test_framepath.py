"""The fold's computed-load guards: a membership fact is control, a pin is data.

Playbook F9: an observed address that varies with the song position forks the
merge per note, so the site's observed set is one shared fact and only a load
naming a frame-local cell keeps its equality pin.
"""

from deity_informant import framepath
from deity_informant import frameprog
from deity_informant import frameval
from deity_informant import structured as S
from deity_informant.asm6502 import Asm

ORG = 0x1000
TBL = 0x1400  # the walked table
SCR = 0x1430  # a frame-local cell inside the table's index range
IDX = 0x1441  # a constant cell holding the aliasing index (a symbolic address)
MAP = 0x1460  # the index table a mixed site walks
CUR = 0x1450  # the song position: read before written, so state
SID = 0xD400
INIT = 0x0F00


def _model(prog, data, frames):
    """A committed model of one synthetic player over ``frames`` invocations."""
    mem = bytearray(0x10000)
    mem[ORG : ORG + len(prog)] = prog
    mem[INIT] = 0x60
    for addr, val in data.items():
        mem[addr] = val
    model, _ev = S.decompile(mem, INIT, ORG, frames)
    return model


def _table():
    return {TBL + k: (k * 7 + 3) & 0xFF for k in range(0x40)}


def _advance(a):
    """The song-position walk: ``CUR = (CUR + 1) & $0F``, Z set on the wrap."""
    a.i("LDA", "abs", CUR).i("CLC").i("ADC", "imm", 1).i("AND", "imm", 0x0F)
    a.i("STA", "abs", CUR)
    return a


def _walker():
    """Reads a never-written table at the walked position; the wrap frame reads CUR."""
    a = _advance(Asm(ORG)).i("BNE", "rel", ("L", "walk"))
    a.i("LDX", "abs", IDX).i("JMP", "abs", ("L", "rd"))
    a.label("walk").i("TAX")
    a.label("rd").i("LDA", "absx", TBL).i("STA", "abs", SID).i("RTS")
    return a.assemble(), {**_table(), CUR: 0, IDX: CUR - TBL}


def _aliaser():
    """The same load site reads a frame-local cell on odd positions, the table on even."""
    a = _advance(Asm(ORG))
    a.i("LDA", "imm", 0xAA).i("STA", "abs", SCR)
    a.i("LDA", "abs", CUR).i("AND", "imm", 1).i("BEQ", "rel", ("L", "walk"))
    a.i("LDX", "abs", IDX).i("JMP", "abs", ("L", "rd"))
    a.label("walk").i("LDX", "abs", CUR)
    a.label("rd").i("LDA", "absx", TBL).i("STA", "abs", SID).i("RTS")
    return a.assemble(), {**_table(), CUR: 0, IDX: SCR - TBL}


def _mixer():
    """One control shape whose single load site lands on the frame-local cell or the table.

    The index comes from a table, so no branch separates the two: the site's
    kind must be site-global and the divergence must be the observation."""
    a = _advance(Asm(ORG))
    a.i("LDA", "imm", 0xAA).i("STA", "abs", SCR)
    a.i("LDY", "abs", CUR).i("LDX", "absy", MAP)
    a.i("LDA", "absx", TBL).i("STA", "abs", SID).i("RTS")
    idx = {MAP + k: (SCR - TBL if k % 4 == 3 else 2 * k) for k in range(0x10)}
    return a.assemble(), {**_table(), **idx, CUR: 0}


def _restager():
    """One control shape whose computed load sometimes lands on a cell it re-staged.

    The landed cell's store version differs between invocations while the
    address form does not, so a version-keyed local splits one load in two."""
    a = _advance(Asm(ORG)).i("TAX")
    a.i("LDA", "imm", 0x55).i("STA", "absx", TBL)
    a.i("LDY", "absx", MAP).i("LDA", "absy", TBL).i("STA", "abs", SID).i("RTS")
    idx = {MAP + k: (k if k % 4 == 3 else (k + 1) & 0x0F) for k in range(0x10)}
    return a.assemble(), {**_table(), **idx, CUR: 0}


def _stmts(stmts):
    for s in stmts:
        yield s
        if s[0] == "if":
            yield from _stmts(s[3])
            yield from _stmts(s[4])
        elif s[0] == "loop":
            yield from _stmts(s[1])


def _term(t):
    """One rendered span's addresses: an equality, or a range with its stride."""
    if t[0] != "op":
        return None
    if t[1] == "INT_EQUAL":
        c = t[2][1]
        return [c[1]] if c[0] == "const" and c[2] == 2 else None
    parts = t[2]
    if t[1] != "INT_AND" or len(parts) < 2 or any(p[1] != "INT_LESSEQUAL" for p in parts[:2]):
        return None
    keep = range(parts[0][2][0][1], parts[1][2][1][1] + 1)
    if len(parts) == 3:
        mask, want = parts[2][2][0][2][1][1], parts[2][2][1][1]
        return [a for a in keep if a & mask == want]
    return list(keep)


def _addrs(cond):
    """The addresses a place guard's condition admits, or None where it is not one."""
    got = [_term(t) for t in (cond[2] if cond[1] == "INT_OR" else (cond,))]
    return None if any(g is None for g in got) else tuple(sorted(a for g in got for a in g))


def _conds(stmts):
    """Every rendered place guard's condition, in statement order."""
    return [s[2] for s in _stmts(stmts) if s[0] == "if" and _addrs(s[2]) is not None]


def _guards(stmts):
    """Every rendered place guard's address set, in statement order."""
    return [_addrs(c) for c in _conds(stmts)]


def _pieces(cond):
    """How many spans the set compressed to, and the conjuncts of the first."""
    terms = cond[2] if cond[1] == "INT_OR" else (cond,)
    return len(terms), len(terms[0][2]) if terms[0][1] == "INT_AND" else 1


def _unobs(stmts):
    return [s[1] for s in _stmts(stmts) if s[0] == "unobs"]


def _gates(model, frames):
    """The folded program's verdict, taken again off the artifact it serialises to."""
    prog = framepath.program(model, frames)
    text = frameprog.dumps(prog)
    return prog, [frameval.gate_fp(model, frames, p) for p in (prog, frameprog.loads(text))]


def test_a_walked_table_read_guards_by_its_observed_set():
    """The site's addresses are one shared membership fact, not a fork per position."""
    prog, data = _walker()
    model = _model(prog, data, 40)
    stmts, _params, _uni = framepath.fold(model, 40)
    observed = tuple(range(TBL + 1, TBL + 0x10)) + (CUR,)
    assert _guards(stmts) == [observed, observed]
    assert [_pieces(c) for c in _conds(stmts)] == [(2, 2), (2, 2)]  # a run and one address
    assert len(set(_unobs(stmts))) == 1
    _fprog, verdicts = _gates(model, 40)
    assert verdicts == [None, None]


def test_the_walked_fold_does_not_grow_with_the_trace():
    """Twice the frames is the same program: the fold is of the code, not the song."""
    prog, data = _walker()
    short = framepath.fold(_model(prog, data, 40), 40)
    assert short == framepath.fold(_model(prog, data, 80), 80)
    assert len(list(_stmts(short[0]))) == 13  # one body per arm, not one per position


def test_a_frame_local_alias_guards_by_its_own_singleton():
    """The emission names that cell's local, so the guard admits that cell alone (F9)."""
    prog, data = _aliaser()
    model = _model(prog, data, 40)
    stmts, _params, _uni = framepath.fold(model, 40)
    walked = tuple(range(TBL, TBL + 0x10, 2))
    assert sorted(_guards(stmts)) == sorted([(SCR,), walked])
    assert sorted(_pieces(c) for c in _conds(stmts)) == [(1, 1), (1, 3)]  # stride 2 + congruence
    forwarded = [s for s in _stmts(stmts) if s[0] == "asg" and s[2] == ("loc", "s_%04X" % SCR)]
    assert len(forwarded) == 1
    _fprog, verdicts = _gates(model, 40)
    assert verdicts == [None, None]


def test_the_alias_cell_is_neither_guarded_nor_declared():
    """Scratch is subtracted from the observed set and never reaches the artifact."""
    prog, data = _aliaser()
    model = _model(prog, data, 40)
    fprog, _verdicts = _gates(model, 40)
    assert SCR not in max(_guards(fprog.procs[0][3]), key=len)
    assert "m_%04X" % SCR not in {f[0] for f in fprog.state}


def test_a_re_staged_cell_does_not_split_one_load_in_two():
    """The local's name is the prefix's occurrence, never the landed cell's version."""
    prog, data = _restager()
    model = _model(prog, data, 40)
    stmts, _params, _uni = framepath.fold(model, 40)
    read = {TBL + (k if k % 4 == 3 else k + 1) for k in range(0x10)}
    assert _guards(stmts) == [tuple(sorted(read))]
    assert [_pieces(c) for c in _conds(stmts)] == [(4, 2)]  # four stride-1 runs of three
    assert len(_unobs(stmts)) == 1 and len(list(_stmts(stmts))) == 9
    _fprog, verdicts = _gates(model, 40)
    assert verdicts == [None, None]


def test_a_mixed_site_forks_on_the_observation_not_the_kind():
    """One control shape, both outcomes: the fam is shared and the obs sets divide it."""
    prog, data = _mixer()
    model = _model(prog, data, 40)
    stmts, _params, _uni = framepath.fold(model, 40)
    walked = tuple(TBL + 2 * k for k in range(0x10) if k % 4 != 3)
    assert _guards(stmts) == [walked, (SCR,)]
    assert len(_unobs(stmts)) == 1
    forwarded = [s for s in _stmts(stmts) if s[0] == "asg" and s[2] == ("loc", "s_%04X" % SCR)]
    assert len(forwarded) == 1
    _fprog, verdicts = _gates(model, 40)
    assert verdicts == [None, None]
