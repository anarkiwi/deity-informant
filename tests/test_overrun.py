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
#: The overrun rules.
MODES = ("trap", "zero")
#: Arguments reading the table, the program past it and memory past the program.
ARGS = [0, 1, 3, 4, 5, 9, 17, 30, 200, 255, 1, 0, 7, 8]


def padded(src, fill):
    """``src`` assembled, the 0x200 bytes past its ``end`` label ``fill``."""
    img, labels, loaded = T.assemble(src)
    img[labels["end"] : labels["end"] + 0x200] = fill
    return img, labels, loaded


def pushread():
    """:data:`PUSHREAD` assembled, the bytes past the program not 0."""
    return padded(PUSHREAD, b"\xea" * 0x200)


def rule(prefix, img, labels, loaded, mode, entries=("entry",), **kw):
    """``(c, h, info)`` of the program translated with ``overrun=mode``."""
    ents = [(labels[n], n) for n in entries]
    return cgen.translate(
        img, ents, [T.W.IO], (), loaded=loaded, header="t.h", prefix=prefix, overrun=mode, **kw
    )


def zero(prefix, img, labels, loaded, entries=("entry",), **kw):
    """``(c, h, info)`` of the program translated with ``overrun="zero"``."""
    return rule(prefix, img, labels, loaded, "zero", entries, **kw)


def replay(tmp_path, c, h, img, labels, calls, overruns=None):
    """:func:`cgenplay.replay` of ``calls`` on a fresh build of ``c``/``h``."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")
    pcs = {n: labels[n] for n, _ in calls}
    return cgenplay.replay(lib, img, pcs, calls, [T.W.IO], overruns=overruns)


def sites(info):
    """``{pc: (ok runs, addr hull)}`` of ``info["overruns"]`` (or a lift's ``(pc, ok, addr)``)."""
    rows = [
        o if isinstance(o, dict) else dict(zip(("pc", "ok", "addr"), o)) for o in info["overruns"]
    ]
    return {o["pc"]: ([tuple(r) for r in o["ok"]], tuple(o["addr"])) for o in rows}


def test_store_and_read_outside_their_objects(prefix, tmp_path):
    """The push past its array stores nothing and the read past the program reads 0, each a
    helper's call with no check, branch or trap of its own in the entry; the rule's reference
    replays, the plain one (writing the code, reading past the program) does not."""
    img, labels, loaded = pushread()
    c, h, info = zero(prefix, img, labels, loaded)
    body = T.body_of(c, "entry")
    calls = [("entry", a) for a in ARGS]
    assert replay(tmp_path / "zero", c, h, img, labels, calls, info["overruns"]) == len(calls)
    with pytest.raises(AssertionError):
        replay(tmp_path / "plain", c, h, img, labels, calls)
    assert re.search(r"\bR(\d+)_obj\d+_wr\(R\d+\[0\], a\);", body)
    assert re.search(r"\bio_write\(0xC010, R\d+_obj\d+\(1 \+ a\)\);", body)
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
    c2, _, info2 = rule(prefix, img, labels, loaded, "trap")
    assert c == c2 and info["overruns"] == info2["overruns"]
    assert c != zero(prefix, img, labels, loaded)[0]
    guards = {
        g["at"]: [tuple(r) for r in g["ok"]]
        for t in info["trap_info"].values()
        for g in t["guards"]
        if g["kind"] == "objchk" and g["def"] == "machine"
    }
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
    assert replay(tmp_path, c, h, img, labels, calls, info["overruns"]) == len(calls)
    ok, (lo, hi) = sites(info)[labels["rd"]]
    assert all(b < labels["end"] for _, b in ok) and lo < labels["end"] <= hi


def test_lifted_program_reads_zero_and_skips_the_store():
    """The lift alone (its interpreter) follows the rule: each call's writes are the rule's
    reference's, no deopt taking the plain reference's."""
    img, labels, loaded = pushread()
    kw = {"entry_sp": cgen.emit.ENTRY_REGS[3], "loaded": loaded, "fold": True}
    prog = wholeprog.lift(img, [labels["entry"]], [T.W.IO], overrun="zero", **kw)
    rules = [{"pc": p, "ok": ok, "addr": a} for p, ok, a, _ in prog.overruns]
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
    assert re.search(r"\bR\d+_obj\d+_wr\(", T.body_of((tmp_path / "p.c").read_text(), "entry"))
    calls = ",".join(f"entry:{a}" for a in ARGS)
    assert cgenplay.main([*args, "--calls", calls, "-o", str(tmp_path / "play")]) == 0


#: A table read the memory map bounds between a store of ``A`` and its reload, twice.
REREAD = """
entry:  STA cell
        TAX
rd:     LDA tab,X
        STA $C010
        LDA cell
        STA $C011
again:  LDA tab,X
        STA $C012
        RTS
cell:   .byte 0
tab:    .byte 1,2,3,4
end:
"""


def test_skipped_access_prints_as_the_trap_modes_access(prefix, tmp_path):
    """Each skipped access is its array's helper taking the subscript the trap mode's element
    has, defined as that element where the address lies in the object, and its value folds
    into its use as the element does; it is one access in the lift, so the reload of the cell
    stored before it is forwarded and the second read of the same address is the first's,
    as in the trap mode."""
    img, labels, loaded = padded(REREAD, b"\x07" * 0x200)
    trap = T.body_of(T.translate(prefix, img, labels, loaded)[0], "entry")
    c, h, info = zero(prefix, img, labels, loaded)
    body = T.body_of(c, "entry")
    ((k, sub),) = set(re.findall(r"\bR(\d+)\[(\w+ \+ a)\]", trap))
    (j,) = set(re.findall(rf"\bR{k}_obj(\d+)\({re.escape(sub)}\)", body))
    helper = re.search(rf"static uint8_t R{k}_obj{j}\(uint16_t i\) \{{(.*?)\n\}}", c, re.S)
    assert (
        helper and f"return R{k}[i];" in helper.group(1) and f"CGEN_OBJ{j}_ORG" in helper.group(1)
    )
    assert "io_write(0xC011, a);" in trap and "io_write(0xC011, a);" in body
    assert body.count(f"R{k}_obj{j}(") == trap.count(f"R{k}[{sub}]") == 1
    calls = [("entry", a) for a in ARGS]
    assert replay(tmp_path, c, h, img, labels, calls, info["overruns"]) == len(calls)


def test_lift_flags_each_skipped_access_with_its_object():
    """The lift emits each access the memory map bounds as one load or store flagged with its
    object (``(j, volatile)``), no branch around it: the zero lift has fewer blocks than the
    trap lift, which branches to its deopts there."""
    img, labels, loaded = pushread()
    kw = {"entry_sp": cgen.emit.ENTRY_REGS[3], "loaded": loaded, "fold": True}
    lifts = {m: wholeprog.lift(img, [labels["entry"]], [T.W.IO], overrun=m, **kw) for m in MODES}
    ops = {
        m: [o for f in p.functions for b in f["blocks"] for o in b["ops"]] for m, p in lifts.items()
    }
    flagged = {o[4]: o[3] for o in ops["zero"] if isinstance(o[3], tuple)}
    assert set(flagged) == set(sites({"overruns": lifts["zero"].overruns}))
    assert all(x[1] is False and x[0] >= 0 for x in flagged.values())
    assert not any(isinstance(o[3], tuple) for o in ops["trap"])
    blocks = {m: sum(len(f["blocks"]) for f in p.functions) for m, p in lifts.items()}
    assert blocks["zero"] < blocks["trap"]


def test_volatile_skipped_access_branches_on_its_object(prefix):
    """A skipped access that may be volatile is its ``io_read`` or ``io_write`` behind a
    branch on its ``objchk``, a load's value 0 on the other edge, and the MLIR verifies; every
    other skipped access is the ``memref`` access marked with its object."""
    img, labels, loaded = pushread()
    kw = {"entry_sp": cgen.emit.ENTRY_REGS[3], "loaded": loaded, "fold": True}
    funcs = wholeprog.lift(img, [labels["entry"]], [T.W.IO], overrun="zero", **kw).functions
    ents = [(labels["entry"], "entry")]
    plain = cgen.emit.emit(funcs, ents, False, 0)[0]
    zeroed = [o for f in funcs for b in f["blocks"] for o in b["ops"] if isinstance(o[3], tuple)]
    assert len(re.findall(r"memref\.(?:load|store) .*\{zero = \d+ : i16\}", plain)) == len(zeroed)
    for o in zeroed:
        o[3] = (o[3][0], True)
    text = cgen.emit.emit(funcs, ents, False, 0)[0]
    assert "{zero" not in text and text.count("@objchk(") >= len(zeroed)
    loads = sum(o[0] == "LOAD" for o in zeroed)
    assert len(re.findall(r"cf\.cond_br %\w+, \^\w+j\(%\w+ : i8\), \^\w+\b", text)) == loads
    assert "callee = @io_read" in cgen.mlir.optimize(text, prefix)


@pytest.mark.parametrize("aim", [None, "play"])
def test_block_reader_and_element_writer_helpers(prefix, tmp_path, aim):
    """A song pointer's read through the data block's reader is that reader's helper, taking
    the base and index the trap mode's checked reader takes, and its store back the block
    array's element writer taking the subscript; aimed into play's code, the reads past the
    pattern read 0 and the stores there do nothing, as the rule's reference does."""
    src = T.SONGBLOCK.replace("note:   STA add+1", "note:   STA ($FB),Y\n        STA add+1")
    img, labels, loaded = T.assemble(src)
    if aim:
        off = (labels[aim] - labels["pats"]) & 0xFFFF
        img[labels["ordlo"] + 1], img[labels["ordhi"] + 1] = off & 0xFF, off >> 8
    trap = T.body_of(T.translate(prefix, img, labels, loaded, ("init", "play"), init=0)[0], "play")
    c, h, info = zero(prefix, img, labels, loaded, ("init", "play"), init=0)
    body = T.body_of(c, "play")
    ((b, k, base, idx),) = re.findall(r"\bB(\d+)c(\d+)\((.+), (\w+), \d+\)", trap)
    assert f"B{b}r_obj{k}({base}, {idx})" in body
    assert re.search(
        rf"uint8_t B{b}r_obj{k}\(uint16_t p, uint8_t y\) \{{.*?return B{b}r\(p, y\);", c, re.S
    )
    assert re.search(rf"\bB{b}_obj{k}_wr\(\(uint16_t\)\(.+ - B{b}_ORG\),\s+v\d+\);", body)
    assert re.search(rf"void B{b}_obj{k}_wr\(uint16_t i, uint8_t v\) \{{.*?B{b}\[i\] = v;", c, re.S)
    assert "trap(" not in body
    calls = [("init", 0)] + [("play", 0)] * 200
    assert replay(tmp_path, c, h, img, labels, calls, info["overruns"]) == len(calls)


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


#: Tables read by runtime indexes: a split record (``lo``, ``hi`` at ``X``), a difference of
#: adjacent entries (``vib``, ``vib-1`` at ``Y``), a lookup at a constant byte (``hi+1,Y``) and
#: a table ending ``hi`` (``tail``, at ``X`` changed).
TABLES = """
entry:  TAX
        LDY konst
cst:    LDA hi+1,Y
        STA $C013
lo_rd:  LDA lo,X
        STA $C010
hi_rd:  LDA hi,X
        STA $C011
        TXA
        LSR A
        TAY
vib_rd: LDA vib,Y
        SEC
inner:  SBC vib-1,Y
        STA $C012
        INX
tl_rd:  LDA tail,X
        STA $C014
        RTS
konst:  .byte 1
vib:    .byte 10,20,30
lo:     .byte 1,2,3,4,5
hi:     .byte 6,7,8
tail:   .byte 9,9
end:
"""


def last(prefix, img, labels, loaded):
    """``(c, h, info)`` of the program translated with ``overrun="last"``."""
    return rule(prefix, img, labels, loaded, "last")


def test_last_entry_tables(prefix, tmp_path):
    """Each table is its own extent: the split record's columns take the least entries, of
    two bases one entry apart neither of which is read alone the lower is the table's, a
    lookup at a constant byte ends no table; a read past one reads its last entry in the C,
    in the lift's interpreter and in the rule's reference alike, which the plain reference
    does not."""
    img, labels, loaded = padded(TABLES, bytes(range(0x80, 0x100)) * 4)
    c, h, info = last(prefix, img, labels, loaded)
    got = {o["pc"]: (o["ok"], o["last"]) for o in info["overruns"]}
    lo, hi, vib, tail = (labels[n] for n in ("lo", "hi", "vib", "tail"))
    assert got[labels["lo_rd"]] == ([[lo, lo + 2]], [[lo + 2, 1]])
    assert got[labels["hi_rd"]] == ([[hi, hi + 2]], [[hi + 2, 1]])
    assert got[labels["vib_rd"]] == got[labels["inner"]] == ([[vib - 1, lo - 1]], [[lo - 1, 1]])
    assert got[labels["tl_rd"]][0] == [[tail, labels["end"] - 1]]
    assert labels["cst"] not in got and not info["unbounded"]
    assert {t[0] for t in info["tables"]} >= set(got)
    calls = [("entry", a) for a in (0, 1, 2, 3, 4, 7, 40, 200, 255)]
    assert replay(tmp_path / "last", c, h, img, labels, calls, info["overruns"]) == len(calls)
    with pytest.raises(AssertionError):
        replay(
            tmp_path / "zero",
            c,
            h,
            img,
            labels,
            calls,
            [{**o, "last": None} for o in info["overruns"]],
        )
    kw = {"entry_sp": cgen.emit.ENTRY_REGS[3], "loaded": loaded, "fold": True}
    prog = wholeprog.lift(img, [labels["entry"]], [T.W.IO], overrun="last", **kw)
    rules = [{"pc": p, "ok": ok, "addr": a, "last": e} for p, ok, a, e in prog.overruns]
    ref, mem = cgenplay.Overruns(img, rules, [T.W.IO]), bytearray(img)
    for _, a in calls:
        o = prog.call(mem, 0, a=a, reg=cgenplay.entry_regs(a), log=True)
        want = [(x, v, w) for x, v, w in ref.call(labels["entry"], a) if w]
        assert [(x, v, int(w)) for x, v, _c, w in o["log"] if w] == want, a
        assert not o["deopt"], a


#: :data:`PUSHREAD` with its push index fixed: called with ``A`` 0, the table read stays in it.
PUSHFIXED = PUSHREAD.replace("        INC depth\n", "")
#: A table read through an index wrapping at 8 over a 4-byte table: the run leaves it.
WRAPREAD = """
entry:  LDX idx
rd:     LDA tab,X
        STA $C010
        INX
        TXA
        AND #7
        STA idx
        RTS
idx:    .byte 0
tab:    .byte 1,2,3,4
end:
"""


def test_proven_addresses_inside_their_objects_need_no_check(prefix, tmp_path):
    """With every run's addresses certified (play called with ``A`` 0), the table read's all
    lie inside its object: the read has no object check and is no unbounded access; the C
    still plays the reference."""
    img, labels, loaded = padded(PUSHFIXED, b"\xea" * 0x200)
    c, h, info = rule(prefix, img, labels, loaded, "trap", certified=True)
    c0, _, plain = rule(prefix, img, labels, loaded, "trap")
    rd = labels["rd"]
    assert "CGEN_OBJ" in T.body_of(c0, "entry") and "CGEN_OBJ" not in T.body_of(c, "entry")
    assert rd in info["traced_bounds"] and plain["traced_bounds"] == []
    assert rd in {u[0] for u in plain["unbounded"]}
    assert rd not in {u[0] for u in info["unbounded"]}
    calls = [("entry", 0)] * 3
    assert replay(tmp_path, c, h, img, labels, calls) == len(calls)


def test_proven_address_outside_its_object_keeps_the_check(prefix):
    """A read whose certified addresses leave its object keeps its check."""
    img, labels, loaded = padded(WRAPREAD, b"\xea" * 0x200)
    c, _, info = rule(prefix, img, labels, loaded, "trap", certified=True)
    rd = labels["rd"]
    assert "CGEN_OBJ" in T.body_of(c, "entry") and rd not in info["traced_bounds"]
    assert rd in {u[0] for u in info["unbounded"]}
