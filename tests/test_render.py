"""Structured readable view (plan P5): faithfulness (every reachable block
emitted exactly once), real control recovery, named state, cross-tune structure.

Real-tune tests need the (uncommitted, copyrighted) HVSC cache; the synthetic
``_fuzzgen`` corpus runs everywhere and carries the CI coverage of render/P4."""

from pathlib import Path

import pytest

from deity_informant import render, structured as S
from deity_informant.c64 import load_psid, psid_songs

import _fuzzgen as G

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"

_FUZZ = G.players(2)
_FUZZ_IDS = [f"{p.name}-{p.seed[1]}" for p in _FUZZ]


def _image(cells):
    m = bytearray(0x10000)
    for a, v in cells.items():
        m[a] = v
    return m


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
            out.extend(pc for pc in (r.b or []) if pc is not None)
            for _lbl, body in r.a[1]:
                walk(body)

    walk(root)
    return out


def _faithful(model, note):
    """Every reachable block in every procedure emitted exactly once."""
    for _name, pc in render._procedures(model):
        root, _labels = render._structure(model, pc)
        root = render._switchify(root)
        emitted = _emitted(root)
        reach = set(render._proc_cfg(model, pc)[0])
        assert len(emitted) == len(set(emitted)), "%s $%04X: block emitted twice" % (note, pc)
        assert set(emitted) == reach, "%s $%04X: %d blocks dropped" % (
            note,
            pc,
            len(reach - set(emitted)),
        )


@pytest.mark.parametrize("p", _FUZZ, ids=_FUZZ_IDS)
def test_fuzz_render_faithful_and_structured(p):
    """Every idiom class renders faithfully with real control constructs; the
    dispatch idioms (opcode SMC, jump table, indirect) exercise P4 + switch
    recovery in CI, where the HVSC corpus is absent."""
    init = p.init_org if p.init is not None else None
    mem = _image(p.image_data())
    if init is None:
        mem[0x0F00] = 0x60  # RTS: empty init
        init = 0x0F00
    model, _ev = S.decompile(mem, init, p.org, max(p.frames, 2))
    _faithful(model, p.name)
    txt = render.render(model)
    assert txt.startswith("; structured view")
    assert render._switchify(render._structure(model, p.org)[0]) is not None


def test_comparison_chain_renders_as_value_switch():
    """A CMP #c / BEQ chain over a command byte collapses to a value switch,
    exercising the normalise-and-collapse path (P5) without the HVSC corpus."""
    a = G.Asm(0x1000)
    a.i("LDA", "abs", 0x1400)  # command byte
    for cmd, reg in ((0x01, 0), (0x02, 1), (0x03, 2)):
        a.i("CMP", "imm", cmd).i("BNE", "rel", ("L", "n%d" % cmd))
        a.i("LDA", "imm", 0x80 + reg).i("STA", "abs", 0xD400 + reg).i("RTS")
        a.label("n%d" % cmd)
    a.i("RTS")
    mem = _image({0x1400: 0x02})
    mem[0x1000 : 0x1000 + len(a.assemble())] = a.assemble()
    mem[0x0F00] = 0x60
    model, _ev = S.decompile(mem, 0x0F00, 0x1000, 2)
    _faithful(model, "cmp_chain")
    txt = render.render(model)
    assert "switch A {" in txt and "case $" in txt


def test_indexed_jump_table_renders_dispatch():
    """A JMP (vector) dispatch whose vector is written from a selector-indexed
    table resolves its target set (P4 closure) and structures the handlers."""
    a = G.Asm(0x1000)
    a.i("LDX", "abs", 0x1400)  # selector: 0 or 2
    a.i("LDA", "absx", 0x1420).i("STA", "zp", 0x02)  # vector lo from table[X]
    a.i("LDA", "absx", 0x1421).i("STA", "zp", 0x03)  # vector hi
    a.i("JMP", "ind", 0x02)
    data = {0x1400: 0x02, 0x1420: 0x00, 0x1421: 0x13, 0x1422: 0x20, 0x1423: 0x13}
    mem = _image(data)
    prog = a.assemble()
    mem[0x1000 : 0x1000 + len(prog)] = prog
    for base, reg, val in ((0x1300, 3, 0x11), (0x1320, 4, 0x22)):
        h = G.Asm(base).i("LDA", "imm", val).i("STA", "abs", 0xD400 + reg).i("RTS").assemble()
        mem[base : base + len(h)] = h
    mem[0x0F00] = 0x60
    model, _ev = S.decompile(mem, 0x0F00, 0x1000, 2)
    _faithful(model, "jump_table")
    txt = render.render(model)
    assert "sub_" in txt or "goto L_" in txt or "switch" in txt


def test_fuzz_dispatch_idioms_render_switches():
    """The generated dispatch idioms surface as switch / call-one-of / jump
    tables in the readable view (P5 dispatcher recovery, HVSC-free)."""
    kinds = {}
    for p in G.players(2):
        init = p.init_org if p.init is not None else None
        mem = _image(p.image_data())
        if init is None:
            mem[0x0F00] = 0x60
            init = 0x0F00
        model, _ev = S.decompile(mem, init, p.org, max(p.frames, 2))
        txt = render.render(model)
        if "switch code[" in txt:
            kinds["opcode"] = True
        if "call one of" in txt or "switch (computed goto)" in txt:
            kinds["computed"] = True
    assert "opcode" in kinds, "smc_opcode player should yield an opcode switch"
    assert "computed" in kinds, "dispatch players should yield a computed jump/call"


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
        root = render._switchify(root)  # command-chain collapse must stay faithful
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
