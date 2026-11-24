"""Overrun-zero (``docs/cgen.md``, *Memory*): an access the memory map bounds reads 0 or stores
nothing where its address leaves its object, never trapping; the C plays the reference that
follows the same rule (``tools/cgenplay.py``'s ``Overruns``)."""

import re

import pytest

import test_cgen as T
from test_cgen import fixture_prefix  # noqa: F401  pylint: disable=unused-import
from deity_informant import cgen, wholeprog

cgenplay = T.cgenplay
pytestmark = T.pytestmark

#: A push index growing past its array (into the code), table reads past the program.
PUSHREAD = """
stack:  .byte 0,0,0
entry:  LDX depth
put:    STA stack,X
        INC depth
        TAX
rd:     LDA tab,X
        STA $C010
cmp:    LDA tab+1,X
        CMP #$03
        BNE no
        STA $C013
no:     LDA stack
        STA $C011
        LDA stack+2
        STA $C012
        RTS
depth:  .byte 0
tab:    .byte 1,2,3,4
end:
"""
#: Arguments reading the table, the program past it and memory past the program.
ARGS = [0, 1, 3, 4, 5, 9, 17, 30, 200, 255, 1, 0, 7, 8]


def pushread():
    """:data:`PUSHREAD` assembled, the bytes past the program not 0."""
    img, labels, loaded = T.assemble(PUSHREAD)
    img[labels["end"] : labels["end"] + 0x200] = b"\xea" * 0x200
    return img, labels, loaded


def zero(prefix, img, labels, loaded, entries=("entry",), **kw):
    """``(c, h, info)`` of the program translated with ``overrun="zero"``."""
    ents = [(labels[n], n) for n in entries]
    return cgen.translate(
        img, ents, [T.W.IO], (), loaded=loaded, header="t.h", prefix=prefix, overrun="zero", **kw
    )


def sites(info):
    """``{pc: (ok runs, addr hull)}`` of ``info["overruns"]``."""
    return {o["pc"]: (T.as_runs(o["ok"]), tuple(o["addr"])) for o in info["overruns"]}


def test_store_and_read_outside_their_objects(prefix, tmp_path):
    """The push past its array stores nothing and the read past the program reads 0, each a
    helper's call with no check, branch or trap of its own in the entry; the rule's reference
    replays, the plain one (writing the code, reading past the program) does not."""
    img, labels, loaded = pushread()
    c, h, info = zero(prefix, img, labels, loaded)
    body = T.body_of(c, "entry")
    calls = [("entry", a) for a in ARGS]
    n = T.play(tmp_path / "zero", c, h, img, labels, calls, overruns=info["overruns"])
    assert n == len(calls)
    with pytest.raises(AssertionError):
        T.play(tmp_path / "plain", c, h, img, labels, calls)
    assert re.search(r"\bR\d+zw\d+\(R\d+_ORG \+ R\d+\[0\], a\);", body)
    assert re.search(r"= \w+z\d+\(R\d+_ORG \+ 1 \+ a\);", body)
    assert "CGEN_OBJ" not in body and "?" not in body
    assert {t["kind"] for t in info["trap_info"].values()} == {"index"}
    got = sites(info)
    assert set(got) == {labels["put"], labels["rd"], labels["cmp"]}
    assert got[labels["put"]][0] == [(labels["stack"], labels["entry"] - 1)]
    assert got[labels["rd"]][1] == (labels["tab"], labels["tab"] + 255)
    checks = info["emission"]["checks"]
    assert {x["at"] for x in checks if x["kind"] == "overrun"} == set(got)


def test_trap_mode_keeps_its_guards(prefix):
    """``overrun="trap"`` is the default: the same C, the accesses trapping at checks whose
    runs are the sites' own."""
    img, labels, loaded = pushread()
    ents = [(labels["entry"], "entry")]
    kw = {"loaded": loaded, "header": "t.h", "prefix": prefix}
    c, _, info = cgen.translate(img, ents, [T.W.IO], (), **kw)
    c2, _, info2 = cgen.translate(img, ents, [T.W.IO], (), overrun="trap", **kw)
    assert c == c2 and info["overruns"] == info2["overruns"]
    assert c != zero(prefix, img, labels, loaded)[0]
    guards = {g["at"]: T.as_runs(g["ok"]) for _, g in T.trap_guards(info, "objchk", "machine")}
    assert guards == {pc: ok for pc, (ok, _) in sites(info).items()}
    with pytest.raises(ValueError):
        cgen.translate(img, ents, [T.W.IO], (), overrun="wrap", **kw)


@pytest.mark.parametrize("mode", [*T.MODES, "zpx"])
def test_pointer_from_song_data_reads_zero_past_the_program(prefix, tmp_path, mode):
    """A pointer from the song's data whose pattern runs off the program: where the trap mode
    stops at the memory map's check, every play reads 0 past the program, as the rule's
    reference does; the site's runs end inside the program, its addresses past it."""
    img, labels, loaded = T.assemble(T.machine_src(mode, p2="6, 7, 8, 9"))
    img[labels["end"] : labels["end"] + 0x400] = b"\x05" * 0x400
    c, h, info = zero(prefix, img, labels, loaded, ("init", "play"), init=0)
    assert not T.machine_guards(info) and "CGEN_OBJ" not in T.body_of(c, "play")
    calls = [("init", 0)] + [("play", 0)] * 40
    assert T.play(tmp_path, c, h, img, labels, calls, overruns=info["overruns"]) == len(calls)
    ok, (lo, hi) = sites(info)[labels["rd"]]
    assert all(b < labels["end"] for _, b in ok) and lo < labels["end"] <= hi


def test_lifted_program_reads_zero_and_skips_the_store():
    """The lift alone (its interpreter) follows the rule: each call's writes are the rule's
    reference's, no deopt taking the plain reference's."""
    img, labels, loaded = pushread()
    kw = {"entry_sp": cgen.emit.ENTRY_REGS[3], "loaded": loaded, "fold": True}
    prog = wholeprog.lift(img, [labels["entry"]], [T.W.IO], overrun="zero", **kw)
    rules = [{"pc": p, "ok": ok, "addr": a} for p, ok, a in prog.overruns]
    assert {o["pc"] for o in rules} == {labels["put"], labels["rd"], labels["cmp"]}
    ref, mem = cgenplay.Overruns(img, rules, [T.W.IO]), bytearray(img)
    for a in ARGS:
        o = prog.call(mem, 0, a=a, reg=cgenplay.entry_regs(a), log=True)
        want = [(x, v, w) for x, v, w in ref.call(labels["entry"], a) if w]
        assert [(x, v, int(w)) for x, v, _c, w in o["log"] if w] == want, a
        assert not o["deopt"] and o["halt"] == "return", (a, o["halt"])
    with pytest.raises(ValueError):
        wholeprog.lift(img, [labels["entry"]], [T.W.IO], overrun="wrap", **kw)


def test_command_lines_take_the_rule(prefix, tmp_path):
    """``python -m deity_informant.cgen --overrun zero`` prints the helpers, and
    ``tools/cgenplay.py --overrun zero`` replays the C against the rule's reference."""
    from deity_informant.cgen.__main__ import main  # pylint: disable=import-outside-toplevel

    img, labels, loaded = pushread()
    assert prefix is not None
    (tmp_path / "p.bin").write_bytes(bytes(img[loaded[0] : loaded[1]]))
    args = [str(tmp_path / "p.bin"), "--org", hex(loaded[0]), "--volatile", "0xC000-0xC100"]
    args += ["--entry", f"{labels['entry']}:entry", "--overrun", "zero"]
    main([*args, "-o", str(tmp_path / "p.c")])
    assert re.search(r"\bR\d+zw\d+\(", T.body_of((tmp_path / "p.c").read_text(), "entry"))
    calls = ",".join(f"entry:{a}" for a in ARGS)
    assert cgenplay.main([*args, "--calls", calls, "-o", str(tmp_path / "play")]) == 0


def test_unbounded_lists_the_checked_accesses(prefix):
    """``info["unbounded"]`` holds each access the memory map checks, by instruction start,
    as checked with its object; a program whose accesses have exact addresses has none."""
    img, labels, loaded = pushread()
    _, _, info = zero(prefix, img, labels, loaded)
    got = {pc: (why, obj) for pc, why, obj in info["unbounded"]}
    assert {labels["put"], labels["rd"], labels["cmp"]} <= set(got)
    assert set(sites(info)) <= {pc for pc, (why, _) in got.items() if why == "checked"}
    assert all(obj is not None for why, obj in got.values() if why == "checked")
    exact = "entry: LDX #2\n LDA tab,X\n STA $C010\n RTS\ntab: .byte 1,2,3\nend:\n"
    img, labels, loaded = T.assemble(exact)
    assert zero(prefix, img, labels, loaded)[2]["unbounded"] == []
