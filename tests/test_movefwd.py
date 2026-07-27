"""Redundant data-move pass: SID-shadow detection and provenance lift."""

from pathlib import Path

import pytest

from deity_informant import movefwd
from deity_informant import song_model as sm
from deity_informant import structured as S
from deity_informant.c64 import load_psid

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def _tune(stem, parent):
    return [
        pytest.param(path, sub, id="%s-%s" % (parent, stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == stem and path.parent.name == parent
    ]


def _model(sid, subtune):
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    return S.decompile(mem, init, play, 200, subtune)[0]


def _freq_kinds(procs, model):
    kinds = set()
    for stmts in procs:
        kinds.update(d.kind for d in sm.recover(stmts, model).freq)
    return kinds


def _idx(base, i="i"):
    return ("op", "INT_ADD", (("const", base, 2), ("loc", i)), 2)


def test_walk_stores_flow_sensitive_over_bodies():
    """asg feeds env, stores in if/loop bodies are visited, fn sees each store."""
    stmts = [
        ("asg", "x", ("const", 0x11, 1)),
        ("st", ("const", 0x0400, 2), ("loc", "x")),
        ("if", "if", ("loc", "c"), [("st", ("const", 0x0500, 2), ("const", 1, 1))], []),
        ("loop", [("st", ("const", 0x0600, 2), ("const", 2, 1))]),
    ]
    seen = []
    movefwd._walk_stores(stmts, {}, lambda a, v, e: seen.append((movefwd.ann._const_base(a), e)))
    bases = [b for b, _e in seen]
    assert bases == [0x0400, 0x0500, 0x0600]
    assert seen[0][1] == {"x": ("const", 0x11, 1)}  # env carried the asg


def test_resolve_follows_loc_chain_and_stops():
    env = {"a": ("loc", "b"), "b": ("mem", ("const", 0x0700, 2), 1), "c": ("loc", "c")}
    assert movefwd._resolve(("loc", "a"), env) == ("mem", ("const", 0x0700, 2), 1)
    assert movefwd._resolve(("loc", "c"), env) == ("loc", "c")  # self-cycle breaks
    assert movefwd._resolve(("loc", "z"), env) == ("loc", "z")  # unbound stays


def test_index_extracts_varying_part():
    assert movefwd._index(("const", 5, 1)) is None
    assert movefwd._index(_idx(0xD400)) == ("loc", "i")
    two = ("op", "INT_ADD", (("loc", "i"), ("loc", "j")), 2)
    assert movefwd._index(two) == ("op", "INT_ADD", (("loc", "i"), ("loc", "j")), 2)
    assert movefwd._index(("op", "INT_ADD", (("const", 1, 1), ("const", 2, 1)), 2)) is None
    assert movefwd._index(("mem", ("const", 0x10, 2), 1)) == ("mem", ("const", 0x10, 2), 1)


def test_sid_shadows_relabels_flushed_buffer():
    """A buffer flushed by sid[$D400+i] = B[i] with a matching index is a shadow."""
    procs = [
        [
            ("st", _idx(0xE686), ("const", 0x99, 1)),  # buffer is writable
            ("st", _idx(0xD400), ("mem", _idx(0xE686), 1)),  # parallel flush onto SID
        ]
    ]
    assert movefwd.sid_shadows(procs) == {0xE686: 0xD400}
    assert movefwd._store_bases(procs) == {0xE686}
    mismatched = [[("st", _idx(0xD400), ("mem", _idx(0xE686, "j"), 1))]]
    assert movefwd.sid_shadows(mismatched) == {}  # index does not match, not a flush


def test_rebase_rewrites_const_base():
    shadows = {0xE686: 0xD400}
    expr = ("op", "INT_ADD", (("const", 0xE686, 2), ("mem", ("const", 0xE686, 2), 1)), 2)
    out = movefwd._rebase(expr, shadows)
    assert out == ("op", "INT_ADD", (("const", 0xD400, 2), ("mem", ("const", 0xD400, 2), 1)), 2)
    assert movefwd._rebase(("loc", "k"), shadows) == ("loc", "k")
    assert movefwd._rebase(7, shadows) == 7


def test_map_tree_relabels_stores_in_every_structure():
    shadows = {0xE686: 0xD400}
    body = [("st", _idx(0xE686), ("const", 1, 1))]
    stmts = [
        ("st", _idx(0xE686), ("const", 0, 1)),
        ("if", "if", ("loc", "c"), body, body),
        ("loop", body),
        ("for", "i", ("const", 0, 1), ("const", 4, 1), body),
        ("callb", "p", [], body),
        ("opsw", ("loc", "s"), [("L0", body)]),
        ("swg", [("L1", body)]),
    ]
    out = movefwd._map_tree(stmts, shadows)
    assert movefwd.ann._const_base(out[0][1]) == 0xD400  # top store rebased
    assert movefwd.ann._const_base(out[1][3][0][1]) == 0xD400  # then-branch
    assert movefwd.ann._const_base(out[2][1][0][1]) == 0xD400  # loop body
    assert movefwd.ann._const_base(out[3][4][0][1]) == 0xD400  # for body
    assert movefwd.ann._const_base(out[4][3][0][1]) == 0xD400  # callb body
    assert movefwd.ann._const_base(out[5][2][0][1][0][1]) == 0xD400  # opsw case
    assert movefwd.ann._const_base(out[6][1][0][1][0][1]) == 0xD400  # swg case


def test_list_and_lifted_procs(monkeypatch):
    procs = [
        [
            ("st", _idx(0xE686), ("const", 0x99, 1)),
            ("st", _idx(0xD400), ("mem", _idx(0xE686), 1)),
        ]
    ]
    monkeypatch.setattr(movefwd.ann, "model_procs", lambda m: procs)
    assert movefwd.list_procs(None) == procs
    lifted, shadows = movefwd.lifted_procs(None)
    assert shadows == {0xE686: 0xD400}
    assert movefwd.ann._const_base(lifted[0][1][1]) == 0xD400  # flushed store rebased
    monkeypatch.setattr(movefwd.ann, "model_procs", lambda m: [[("asg", "x", ("const", 1, 1))]])
    lifted2, shadows2 = movefwd.lifted_procs(None)
    assert shadows2 == {} and lifted2 == [[("asg", "x", ("const", 1, 1))]]  # no shadow, unchanged


@pytest.mark.parametrize("sid,subtune", _tune("Krakout", "Daglish_Ben"))
def test_krakout_shadow_lifts_provenance(sid, subtune):
    """The $E686 shadow flush is detected and freq provenance leaves `other`."""
    model = _model(sid, subtune)
    lifted, shadows = movefwd.lifted_procs(model)
    assert shadows == {0xE686: 0xD400}
    base = _freq_kinds(movefwd.list_procs(model), model)
    assert base == {"other"} and "slide" in _freq_kinds(lifted, model)


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_commando_has_no_shadow(sid, subtune):
    """Read-only pitch tables and scalar per-voice cells are not shadows."""
    model = _model(sid, subtune)
    lifted, shadows = movefwd.lifted_procs(model)
    assert shadows == {}
    assert "note" in _freq_kinds(lifted, model)
