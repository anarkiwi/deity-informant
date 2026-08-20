"""S2/S6 sibling copies: the exact correspondence, the closure, the fold (snippets).

The correspondence is tested as a property: k copies of a random template under a
random per-copy cell layout must come back as one family with exactly that map,
and a stream that only shares a prefix, or whose operands are not one map, as none.
"""

import random
import re

import pytest

from deity_informant.tuneprog import closure, copyfold, jumptab, siblings
from deity_informant.tuneprog.ir import Trap
from deity_informant.tuneprog.verify import verify

from _asm import asm
from _prog import PLAY, closed as _closed, printed as _text, proc_body as _body, tuneprog

SEEDS = range(6)
FORMS = ("LDA", "ADC", "AND", "CMP", "EOR", "ORA", "STA", "INC", "DEC")


def _image(code):
    m = bytearray(0x10000)
    m[PLAY : PLAY + len(code)] = code
    return m


def _label(code, name):
    return code.labels[name]


# ---- a random family of copies -----------------------------------------------
class Family:
    """A generated tune: ``k`` chained copies of one template over per-copy cells."""

    def __init__(self, rng, k=3, fields=4, n=6, share=True, break_map=False, diverge=False):
        self.k, self.fields = k, fields
        names = ["d%d_%d" % (j, i) for j in range(k) for i in range(fields)]
        rng.shuffle(names)  # the cells of one copy lie where the layout put them
        self.cell = {(j, i): names[j * fields + i] for j in range(k) for i in range(fields)}
        body = [(rng.choice(FORMS), rng.randrange(1, fields)) for _ in range(n)]
        self.used = {1} | {i for _mn, i in body}  # the state cell sits on the boundary
        self.arm = [rng.randrange(2) for _ in range(k)]
        self.arm[0] ^= len(set(self.arm)) == 1  # some copy runs the arm another never does
        # a copy that diverges keeps only the template's first instruction
        other = [(rng.choice(FORMS), rng.randrange(1, fields)) for _ in range(n)]
        bodies = [body] + [body[:1] + other[1:] if diverge else body for _ in range(1, k)]
        src = ["init: LDA #$00"] + ["STA %s" % nm for nm in sorted(names) + ["sh", "cnt"]]
        for j in range(k):
            src += ["LDA #$%02X" % self.arm[j], "STA %s" % self.cell[(j, 0)]]
        src += ["RTS", "play:"]
        for j in range(k):
            src += self._copy(j, bodies[j], share, break_map, diverge)
        src += ["after: INC cnt", "RTS"]
        src += ["%s: BRK" % nm for nm in names] + ["sh: BRK", "cnt: BRK"]
        self.code = asm(PLAY, *src)
        self.image = _image(self.code)

    def _copy(self, j, body, share, break_map, diverge):
        """The lines of copy ``j``: a state test, two arms, a shared tail, the chain jump."""
        half = len(body) // 2
        cell = lambda i: self.cell[(j, i)]
        out = ["c%d: LDA %s" % (j, cell(0)), "BNE b%d" % j]
        out += ["%s %s" % (mn, cell(i)) for mn, i in body[:half]]
        out += ["JMP n%d" % j, "b%d: %s %s" % (j, body[half][0], cell(body[half][1]))]
        out += ["%s %s" % (mn, cell(i)) for mn, i in body[half + 1 :]]
        out.append(
            "n%d: %s %s" % (j, "INC" if diverge and j else "LDA", "sh" if share else cell(1))
        )
        # the same cell twice in copy 0, two different ones after it: not one map
        out.append("STA %s" % cell(2 if break_map and j else 1))
        out.append("JMP %s" % ("c%d" % (j + 1) if j + 1 < self.k else "after"))
        return out

    def addr(self, j, i):
        return self.code.labels[self.cell[(j, i)]]

    @property
    def cells(self):
        """Where the tune's cells begin: the map below this is over code, not storage."""
        return min(self.addr(j, i) for j in range(self.k) for i in range(self.fields))

    def pairs(self, j):
        """Every ``(address, address)`` a correct map of copy ``c`` onto ``c + j`` holds."""
        out = {(self.code.labels["sh"], self.code.labels["sh"])}
        for c in range(self.k - j):
            out |= {(self.addr(c, i), self.addr(c + j, i)) for i in range(self.fields)}
        return out

    def found(self, calls=6):
        """The families ``correspond`` finds in the certified program."""
        trace, self.prog = tuneprog(self.code, calls=calls, s4=True)
        band = tuple(trace.meta["load"])
        pcs = {key[0] for key in trace.sites}
        return siblings.correspond(self.prog, trace.image_post_init, pcs, band)


@pytest.mark.parametrize("seed", SEEDS)
def test_k_copies_of_one_template_come_back_as_one_family_with_that_map(seed):
    rng = random.Random(seed)
    fam = Family(rng, k=rng.choice((3, 4)))
    got = fam.found()
    assert len(got) == 1 and got[0].k == fam.k, got
    assert siblings.chained(fam.prog.procs[got[0].proc], fam.image, got[0])
    for j in range(1, fam.k):
        m = got[0].addrmap(fam.image, j)
        assert m is not None
        cells = {(a, b) for (_mode, a), b in m.items() if a >= fam.cells}
        assert cells <= fam.pairs(j), (j, sorted(cells - fam.pairs(j)))
        for i in fam.used:  # and it is the whole map, not a corner of it
            assert any((fam.addr(c, i), fam.addr(c + j, i)) in cells for c in range(fam.k - j)), i


@pytest.mark.parametrize("seed", SEEDS)
def test_copies_the_arms_split_still_align_over_the_whole_template(seed):
    rng = random.Random(seed + 100)
    fam = Family(rng, k=3, n=8)
    assert len(set(fam.arm)) > 1  # the copies' coverage differs; the correspondence does not
    got = fam.found()
    assert len(got) == 1 and got[0].k == 3
    rows = got[0].rows
    assert all(len({fam.image[p] for p in row}) == 1 for row in rows)
    assert len(rows) >= 8


@pytest.mark.parametrize("seed", SEEDS)
def test_a_stream_that_only_shares_a_prefix_is_not_a_family(seed):
    fam = Family(random.Random(seed + 200), k=2, n=8, diverge=True)
    assert not fam.found(), [(f.k, [hex(b) for b in f.bases]) for f in fam.found()]


@pytest.mark.parametrize("seed", SEEDS)
def test_copies_whose_operands_are_not_one_map_are_refused_whole(seed):
    fam = Family(random.Random(seed + 300), k=3, share=False, break_map=True)
    assert not fam.found(), [(f.k, [hex(b) for b in f.bases]) for f in fam.found()]


@pytest.mark.parametrize("seed", SEEDS)
def test_the_alignment_takes_a_gap_and_refuses_a_replacement(seed):
    rng = random.Random(seed + 400)
    body = [(rng.choice(FORMS), rng.randrange(1, 4)) for _ in range(6)]
    at = rng.randrange(1, 5)
    lines = ["%s $12%02X" % (mn, i) for mn, i in body]
    one = asm(PLAY, *lines)
    two = asm(PLAY + len(one), *(lines[:at] + ["NOP"] + lines[at:]))
    other = asm(PLAY + len(one), *(lines[:at] + ["TAX"] + lines[at + 1 :]))
    img = _image(asm(PLAY, *lines) + two)
    band, stops = (PLAY, PLAY + len(one) + len(two)), {PLAY + len(one)}
    rows = siblings.align(img, PLAY, PLAY + len(one), stops, band)
    assert len(rows) == len(body) and all(img[a] == img[b] for a, b in rows)
    img = _image(asm(PLAY, *lines) + other)
    assert not siblings.align(img, PLAY, PLAY + len(one), stops, band)


# ---- the parallel dispatch tables --------------------------------------------
def dispatch(k=3):
    """``k`` chained copies, each dispatching its own handlers through its own table.

    Follin's shape in miniature: the handlers sit outside the copies and jump back
    into the copy that dispatched them, and no copy dispatches the last entry.
    """
    src = ["init: LDA #$00", "STA cnt"] + ["STA f%d" % j for j in range(k)] + ["RTS", "play:"]
    for j in range(k):
        nxt = "c%d" % (j + 1) if j + 1 < k else "after"
        src += [
            "c%d: LDA f%d" % (j, j),
            "BEQ z%d" % j,
            "LDA cnt",
            "AND #$01",
            "ASL A",
            "TAX",
            "LDA t%dlo,X" % j,
            "STA j%d+1" % j,
            "LDA t%dhi,X" % j,
            "STA j%d+2" % j,
            "j%d: JMP $0000" % j,
            "r%d: STA f%d" % (j, j),
            "JMP %s" % nxt,
            "z%d: LDA #$01" % j,
            "STA f%d" % j,
            "JMP %s" % nxt,
        ]
    src += ["after: INC cnt", "RTS"]
    for j in range(k):
        src += ["h%d0: LDA #$00" % j, "NOP", "JMP r%d" % j]
        src += ["h%d1: LDA #$01" % j, "NOP", "JMP r%d" % j]
        src += ["h%d2: LDA #$02" % j, "NOP", "JMP r%d" % j]
    for j in range(k):
        src += ["t%dlo: BRK" % j, "BRK", "BRK", "t%dhi: BRK" % j, "BRK", "BRK"]
    src += ["f%d: BRK" % j for j in range(k)] + ["cnt: BRK"]
    code = asm(PLAY, *src)
    data = {}
    for j in range(k):
        for x in range(3):
            h = code.labels["h%d%d" % (j, x)]
            data[code.labels["t%dlo" % j] + x] = h & 0xFF
            data[code.labels["t%dhi" % j] + x] = h >> 8
    return code, data


def test_parallel_dispatch_arms_pair_by_their_index_in_the_table():
    code, data = dispatch()
    trace, prog = tuneprog(code, calls=8, s4=True, data=data)
    img, band = trace.image_post_init, tuple(trace.meta["load"])
    assert jumptab.enumerate_targets(prog) == 3  # the entry no copy dispatched, per copy
    fams = siblings.correspond(prog, img, {k[0] for k in trace.sites}, band)
    assert len(fams) == 1 and fams[0].k == 3, fams
    rows = {r[0]: (r[1], r[2]) for r in fams[0].rows}
    for x in range(3):  # every handler pairs, the entry no copy ever dispatched too
        want = tuple(_label(code, "h%d%d" % (j, x)) for j in (1, 2))
        assert rows.get(_label(code, "h0%d" % x)) == want, x
    m = fams[0].addrmap(img, 1)
    assert m and {(a, b) for (_md, a), b in m.items()} >= {
        (_label(code, "f0"), _label(code, "f1")),
        (_label(code, "t0lo"), _label(code, "t1lo")),
    }


# ---- the closure and the fold ------------------------------------------------
VOICE = """
    LDA {st}
    {cmp}
    {extra}
    BNE {v}b
    LDA #$01
    STA {reg}
    JMP {next}
{v}b: LDA #$02
    STA {reg}
    LDA cnt
    STA {v}b+1
    JMP {next}
"""


def _voice(v, st, cmp_, reg, nxt, extra=""):
    src = VOICE.format(st=st, cmp=cmp_, v=v, reg=reg, next=nxt, extra=extra)
    lines = [ln.strip() for ln in src.split("\n") if ln.strip()]
    return [("%s: " % v if i == 0 else "") + ln for i, ln in enumerate(lines)]


def voices(extra2="NOP"):
    """A tune whose play routine is three chained copies of one voice interpreter.

    Follin's shape in miniature: copy 0 tests its state byte with the load's own
    Z flag where the others compare, copy 2 carries one byte more still, and each
    copy runs the arm the others never reach.
    """
    return asm(
        PLAY,
        "init: LDX #$0B",
        "lp: LDA #$00",
        "STA st,X",
        "DEX",
        "BPL lp",
        "STA cnt",
        "RTS",
        "play:",
        *_voice("v0", "st", "", "$D404", "v1"),
        *_voice("v1", "st+1", "CMP #$01", "$D40B", "v2"),
        *_voice("v2", "st+2", "CMP #$02", "$D412", "after", extra2),
        "after: INC cnt",
        "RTS",
        "st: BRK",
        *["BRK"] * 11,
        "cnt: BRK",
    )


def test_three_copies_that_ran_different_arms_close_and_fold():
    text, stats, view, _prog, _trace = _closed(voices())
    assert stats["families"] == 1 and stats["sites_added"] > 0
    body = "\n".join(_body(text, "tick"))
    assert "for v in 0, 1, 2:" in body, body
    assert re.search(r"voice\[v\]\.\w+", body), body
    assert "sid[v].ctrl" in body and "sid[1]" not in body, body
    cells = [c for g in view.meta["folds"].values() for c in g["slots"].values()]
    gaps = [tuple(b[1] - a[1] for a, b in zip(c, c[1:])) for c in cells]
    assert any(len(set(g)) > 1 for g in gaps), gaps  # no stride describes the cells


def test_the_closure_adds_only_arms_no_execution_reached():
    text, stats, _view, prog, trace = _closed(voices())
    added = set(stats.get("pcs", ()))
    assert added and not added & {k[0] for k in trace.sites}
    for p in prog.procs.values():
        for b in p.blocks.values():
            assert b.count == 0 or b.src not in added
    assert 0 < stats["unverified"] < stats["statements"], stats
    assert text


def test_the_closed_program_verifies_against_the_same_trace():
    _text, _stats, _view, prog, trace = _closed(voices(), calls=8)
    v = verify(prog, trace, calls=trace.meta["calls"], prefix=0)
    assert v.div is None and v.call == trace.meta["calls"]


def test_the_certified_program_keeps_its_traps_and_its_bytes():
    trace, prog = tuneprog(voices(), calls=6, s4=True)
    before = prog.to_json()
    fams = siblings.correspond(
        prog, trace.image_post_init, {k[0] for k in trace.sites}, tuple(trace.meta["load"])
    )
    ctrace, stats = closure.close(trace, fams)
    assert prog.to_json() == before and ctrace is not trace
    assert stats["sites_added"] and len(ctrace.sites) > len(trace.sites)
    traps = [b for p in prog.procs.values() for b in p.blocks.values() if type(b.term) is Trap]
    assert traps  # the certified program is still the trace-closed one


def test_copies_that_really_differ_do_not_fold():
    text, _stats, _view, _prog, _trace = _closed(voices(extra2="INC cnt"))
    assert "for v in 0, 1, 2:" not in "\n".join(_body(text, "tick"))


# ---- the fold's own proof ----------------------------------------------------
def test_a_hole_the_copies_disagree_on_must_map_or_step():
    holes = [[("r", 1), ("k@0", 0x2000)], [("r", 2), ("k@0", 0x2010)], [("r", 3), ("k@0", 0x2030)]]
    plan, slots = copyfold.plan(holes)
    assert plan == [("keep",), ("keep",)]
    assert slots == {(1, 0x2000): ((1, 0x2000), (2, 0x2010), (3, 0x2030))}
    assert copyfold.plan([[("r", 1), ("r", 1)], [("r", 2), ("r", 3)]]) == (None, None)
    assert copyfold.plan([[("k", 1)], [("k", 2)], [("k", 9)]]) == (None, None)
    assert copyfold.plan([[("k", 1)], [("k", 3)], [("k", 5)]])[0] == [("affine", 2)]


# ---- group views over a mapping, and over a play-time stride ------------------
def relocated(skew=0):
    """Two copies of one block; ``skew`` moves one cell out of the relocation."""
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA a0",
        "CLC",
        "ADC #$01",
        "STA a0",
        "STA $D404",
        "LDA b0",
        "CLC",
        "ADC #$01",
        "STA b0",
        "STA $D405",
        "LDA a1",
        "CLC",
        "ADC #$01",
        "STA a1",
        "STA $D404",
        "LDA b1",
        "CLC",
        "ADC #$01",
        "STA b1",
        "STA $D405",
        "INC cnt",
        "RTS",
        "a0: BRK",
        "b0: BRK",
        *["BRK"] * 8,
        "a1: BRK",
        *["BRK"] * skew,
        "b1: BRK",
        "cnt: BRK",
    )


def test_two_runs_one_relocation_apart_fold_over_a_per_copy_table():
    text = _text(relocated())
    body = "\n".join(_body(text, "tick"))
    assert "for v in 0, 1:" in body, body
    assert re.search(r"copy\[v\]\.\w+", body), body
    assert "per-copy cells, 2 fields" in text, text


def test_two_runs_whose_cells_are_not_one_relocation_do_not_fold():
    # b's copy sits one byte further on than a's: two mappings, not one
    assert "for v in 0, 1:" not in "\n".join(_body(_text(relocated(skew=1)), "tick"))


def blocks():
    """A block init clears with one loop, walked at stride 7 by the tick."""
    return asm(
        PLAY,
        "init: LDX #$14",
        "lp: LDA #$00",
        "STA blk,X",
        "DEX",
        "BPL lp",
        "STA cnt",
        "RTS",
        "play: LDX #$00",
        "lp2: LDA blk,X",
        "STA sh,X",
        "LDA blk+3,X",
        "STA $D404",
        "TXA",
        "CLC",
        "ADC #$07",
        "TAX",
        "CMP #$15",
        "BNE lp2",
        "INC cnt",
        "RTS",
        "blk: BRK",
        *["BRK"] * 20,
        "sh: BRK",
        *["BRK"] * 20,
        "cnt: BRK",
    )


def test_a_block_one_init_loop_made_one_region_splits_into_its_play_time_records():
    text = _text(blocks())
    assert re.search(r"\w+\[3\]  \$\w+ 21 bytes, stride 7, 2 fields", text), text
    body = "\n".join(_body(text, "tick"))
    assert re.search(r"voice\[[vx][/7]*\]\.f0\d", body), body
    assert not re.search(r"b10\w\w\[[^]]*x", body), body  # its records, not its address
