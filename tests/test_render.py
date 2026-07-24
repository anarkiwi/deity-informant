"""Structured readable view (plan P5): faithfulness (every reachable block
emitted exactly once), real control recovery, named state, cross-tune structure."""

from pathlib import Path

import pytest

from deity_informant import render, structured as S
from deity_informant.c64 import load_psid, psid_songs

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def _driver_tunes():
    if not HVSC.is_dir():
        return []
    out = []
    for sid in sorted(HVSC.rglob("*.sid")):
        _mem, _load, _init, play = load_psid(sid.read_bytes())
        if play:  # per-frame driver; play==0 (RSID) is P9 scope
            out.append(sid)
    return out


_CORPUS = _driver_tunes()


def _emitted(root):
    out = []

    def walk(r):
        if r.kind == "seq":
            for x in r.a:
                walk(x)
        elif r.kind == "block":
            if r.b is not None:  # variant blocks (switch cases) carry pc via the switch
                out.append(r.b)
        elif r.kind == "loop":
            walk(r.a)
        elif r.kind == "if":
            walk(r.b)
            if r.c is not None:
                walk(r.c)
        elif r.kind == "switch":
            if r.b is not None:
                out.append(r.b)
            for _lbl, body in r.a[1]:
                walk(body)

    walk(root)
    return out


@pytest.mark.parametrize("sid", _CORPUS, ids=[s.stem for s in _CORPUS])
def test_structured_view_is_faithful(sid):
    """Every block reachable in each procedure is emitted exactly once — the
    readable view is a lossless re-nesting of the model, not a lossy sketch."""
    data = sid.read_bytes()
    mem, _load, init, play = load_psid(data)
    _songs, start = psid_songs(data)
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, 300, start - 1)
    for _name, pc in render._procedures(model):
        root, _labels = render._structure(model, pc)
        emitted = _emitted(root)
        reach = set(render._proc_cfg(model, pc)[0])
        assert len(emitted) == len(set(emitted)), "%s $%04X: block emitted twice" % (sid.stem, pc)
        assert set(emitted) == reach, "%s $%04X: %d blocks dropped" % (
            sid.stem,
            pc,
            len(reach - set(emitted)),
        )


def test_recovers_control_and_names_sid():
    """The play routine renders real loops/ifs and names SID registers rather
    than transliterating raw stores."""
    sid = next((s for s in _CORPUS if s.stem == "Commando"), None)
    if sid is None:
        pytest.skip("corpus tune absent")
    mem, _l, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, 400)
    txt = render.render(model)
    assert "loop {" in txt and "if " in txt
    assert "sid.v1.ctrl" in txt  # SID voice registers named
    assert "[X]" in txt or "[Y]" in txt  # indexed voice state as arrays
    # most blocks land nested inside structure, not flat at proc top level
    root, _ = render._structure(model, play)
    nested = top = 0

    def walk(r, d):
        nonlocal nested, top
        if r.kind == "seq":
            for x in r.a:
                walk(x, d)
        elif r.kind == "block":
            nested += d > 0
            top += d == 0
        elif r.kind == "loop":
            walk(r.a, d + 1)
        elif r.kind == "if":
            walk(r.b, d + 1)
            if r.c is not None:
                walk(r.c, d + 1)

    walk(root, 0)
    assert nested > top  # structural, not a flat block list


def test_reused_player_yields_matching_structure():
    """Two tunes built on Hubbard's reused engine recover the same high-level
    shape from different machine code -- structure capture, not transliteration."""
    got = {}
    for name in ("Commando", "Monty_on_the_Run"):
        sid = next((s for s in _CORPUS if s.stem == name), None)
        if sid is None:
            pytest.skip("corpus tune absent")
        mem, _l, init, play = load_psid(sid.read_bytes())
        mem[0xD418] = 0x0F
        model, _ev = S.decompile(mem, init, play, 400)
        root, _ = render._structure(model, play)
        # skeleton: the sequence of construct kinds at the top two levels
        skel = []

        def walk(r, d):
            if d > 2:
                return
            if r.kind == "seq":
                for x in r.a:
                    walk(x, d)
            elif r.kind in ("loop", "if"):
                skel.append(r.kind)
                walk(r.a if r.kind == "loop" else r.b, d + 1)
                if r.kind == "if" and r.c is not None:
                    walk(r.c, d + 1)

        walk(root, 0)
        got[name] = tuple(skel[:8])
    assert got["Commando"] == got["Monty_on_the_Run"]
