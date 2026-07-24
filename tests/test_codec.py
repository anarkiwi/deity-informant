"""Structurer codec: flatten(structure(model)) == model CFG, and the check
fails loudly on any tampered tree or diverging terminator."""

import pytest

from deity_informant import codec
from deity_informant import structured as S

import _fuzzgen as G


def _model(p, frames=8):
    m = bytearray(0x10000)
    for a, v in p.image_data().items():
        m[a] = v
    if p.init is None:
        m[0x0F00] = 0x60
    init = p.init_org if p.init is not None else 0x0F00
    model, _ev = S.decompile(m, init, p.org, frames)
    return model


_PLAYERS = G.players(2)


@pytest.mark.parametrize("p", _PLAYERS, ids=[f"{p.name}-{p.seed[1]}" for p in _PLAYERS])
def test_verify_holds_on_every_idiom(p):
    """decompile() already runs the codec check; the explicit call agrees."""
    assert codec.verify(_model(p)) is not None


def _first_leaf(region, path=()):
    if region.kind == "block":
        return path
    if region.kind == "seq":
        for i, r in enumerate(region.a):
            found = _first_leaf(r, path + (i,))
            if found is not None:
                return found
    return None


def test_dropped_leaf_is_caught():
    model = _model(_PLAYERS[0])
    entry = codec.procedures(model)[0]
    root, _labels = codec.structure(model, entry)
    path = _first_leaf(root)
    assert path is not None
    seq = root
    for i in path[:-1]:
        seq = seq.a[i]
    del seq.a[path[-1]]
    with pytest.raises(codec.CodecError):
        codec.flatten(model, entry, root)


def test_diverging_terminator_is_caught():
    model = _model(_PLAYERS[0])
    entry = codec.procedures(model)[0]
    root, _labels = codec.structure(model, entry)
    for blk in model.blocks.values():
        if blk.term[0] in ("goto", "jmp"):
            blk.term = (blk.term[0], (blk.term[1] + 2) & 0xFFFF)
            break
        if blk.term[0] == "br" and blk.term[3] is not None:
            blk.term = blk.term[:3] + (((blk.term[3] + 2) & 0xFFFF),) + blk.term[4:]
            break
    else:
        pytest.skip("no static-target terminator to tamper")
    with pytest.raises(codec.CodecError):
        codec.flatten(model, entry, root)


def test_decompile_surfaces_codec_failure(monkeypatch):
    def boom(_model):
        raise codec.CodecError("synthetic")

    monkeypatch.setattr(codec, "verify", boom)
    p = _PLAYERS[0]
    m = bytearray(0x10000)
    for a, v in p.image_data().items():
        m[a] = v
    if p.init is None:
        m[0x0F00] = 0x60
    init = p.init_org if p.init is not None else 0x0F00
    with pytest.raises(S.DecompileError, match="structurer codec"):
        S.decompile(m, init, p.org, 4)
