"""Hermetic tests for the sweep harness's decompile cache (Phase 3a).

The cache serves the sweeps' models from the frameprog artifact instead of a
second trace, so its whole contract is that a warm run is the cold run: same
model, same program text, same write log, and a key no source edit survives."""

import sys
from pathlib import Path

import pytest

import _fuzzgen as G

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import _sweep  # pylint: disable=wrong-import-position

from deity_informant import frameprog  # pylint: disable=wrong-import-position
from deity_informant import structured as S  # pylint: disable=wrong-import-position

FRAMES = 12


def _tables(m):
    """The committed site tables and block set: what ``build_all`` must re-derive."""
    return m.proofs, m.dyn_targets, m.dispatch_sets, sorted(m.blocks)


def _player():
    """A synthetic tune's image and entry points, as a sweep loads one."""
    p = G.t_table_index(G.np.random.default_rng(11))
    mem = bytearray(0x10000)
    for a, v in p.image_data().items():
        mem[a] = v
    init = p.init_org if p.init is not None else 0x0F00
    if p.init is None:
        mem[0x0F00] = 0x60
    return mem, init, p.org


@pytest.fixture(name="cache")
def _cache(tmp_path, monkeypatch):
    monkeypatch.setattr(_sweep, "ARTIFACTS", tmp_path / "art")
    monkeypatch.delenv(_sweep.CACHE_ENV, raising=False)
    return tmp_path / "art"


@pytest.mark.parametrize("kw", [{}, {"close": True, "close_cap": 64}], ids=["plain", "closed"])
def test_warm_run_is_the_cold_run(cache, kw):
    """Cold then warm: the model, the program text and the write log all agree.

    ``close`` is parametrised because the recurrence record is the one channel
    the artifact abbreviates, and it decides every guard's certification."""
    mem, init, play = _player()
    m0, p0, e0 = _sweep.build(mem, init, play, FRAMES, **kw)
    assert not e0.cached and len(list(cache.glob("*.fp.gz"))) == 1
    m1, p1, e1 = _sweep.build(mem, init, play, FRAMES, **kw)
    assert e1.cached and (e1.wlog_sha, e1.wlog_len) == (e0.wlog_sha, e0.wlog_len)
    assert frameprog.dumps(p1) == frameprog.dumps(p0)
    assert S.Walker(m1).run(FRAMES) == S.Walker(m0).run(FRAMES)
    assert _sweep.wlog_matches(e1, e0.wlog)
    assert bool(kw) == (m0.closure is not None)  # the closed case really scanned
    assert _tables(m1) == _tables(m0)


def test_a_source_edit_invalidates_every_key(cache, monkeypatch):
    """The key is the package's content, so no edit can be served a stale artifact."""
    mem, init, play = _player()
    _sweep.build(mem, init, play, FRAMES)
    monkeypatch.setattr(_sweep, "_FINGERPRINT", None)
    monkeypatch.setattr(_sweep, "PKG", Path(__file__).resolve().parent)
    _, _, ev = _sweep.build(mem, init, play, FRAMES)
    assert not ev.cached and len(list(cache.glob("*.fp.gz"))) == 2


@pytest.mark.parametrize("change", ["mem", "frames", "kw"])
def test_every_build_input_is_in_the_key(cache, change):
    """A mutated image, a different length and a build flag each miss, never merge."""
    mem, init, play = _player()
    _sweep.build(mem, init, play, FRAMES)
    if change == "mem":
        mem[0xD418] = 0x0F
        _sweep.build(mem, init, play, FRAMES)
    elif change == "frames":
        _sweep.build(mem, init, play, FRAMES + 1)
    else:
        _sweep.build(mem, init, play, FRAMES, close=True, close_cap=64)
    assert len(list(cache.glob("*.fp.gz"))) == 2


def test_the_bypass_neither_reads_nor_writes(cache, monkeypatch):
    """``DI_SWEEP_CACHE=0`` is the escape hatch a divergence is localized with."""
    mem, init, play = _player()
    monkeypatch.setenv(_sweep.CACHE_ENV, "0")
    _, _, ev = _sweep.build(mem, init, play, FRAMES)
    assert not ev.cached and not cache.exists()


def test_refresh_rewrites_the_artifact_it_hit(cache, monkeypatch):
    """``=refresh`` recomputes in place: one key, one file, freshly traced."""
    mem, init, play = _player()
    _sweep.build(mem, init, play, FRAMES)
    (one,) = cache.glob("*.fp.gz")
    one.write_bytes(b"")  # a corrupt artifact must not survive a refresh
    monkeypatch.setenv(_sweep.CACHE_ENV, "refresh")
    _, _, ev = _sweep.build(mem, init, play, FRAMES)
    assert not ev.cached and one.read_bytes()
