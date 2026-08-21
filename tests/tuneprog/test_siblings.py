"""S2c sibling copies: the exact correspondence over snippets, as a property.

k copies of a random template under a random per-copy cell layout must come back
as one family with exactly that map, and a stream that only shares a prefix, or
whose operands are not one map, as none. The fold itself is ``test_copymerge``.
"""

import random
import re

import pytest

from deity_informant.tuneprog import jumptab, pipeline, siblings

from _asm import asm
from _prog import PLAY, front, printed as _text, proc_body as _body, tuneprog

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
        # a copy that diverges keeps only the template's first instruction, and no
        # two of them agree either: chained code that is not copied code
        other = [
            [(rng.choice(FORMS), rng.randrange(1, fields)) for _ in range(n)] for _ in range(k)
        ]
        bodies = [body] + [body[:1] + other[j][1:] if diverge else body for j in range(1, k)]
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

    def found(self, calls=6, static=False):
        """The families ``correspond`` finds in the program a build of the tune makes."""
        trace = front(self.code, calls=calls)[0]
        self.prog, _rgn, self.procs = pipeline.build(trace, "snippet", copies=False, static=static)
        band = tuple(trace.meta["load"])
        return siblings.correspond(self.prog, trace.image_post_init, band, self.procs)

    def bases(self):
        """Where the copies really begin."""
        return tuple(self.code.labels["c%d" % j] for j in range(self.k))


@pytest.mark.parametrize("seed", SEEDS)
def test_k_copies_of_one_template_come_back_as_one_family_with_that_map(seed):
    rng = random.Random(seed)
    fam = Family(rng, k=rng.choice((3, 4)))
    got = fam.found()
    assert len(got) == 1 and got[0].k == fam.k, got
    assert siblings.chained(fam.procs[got[0].proc].nodes, fam.image, got[0])
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


def smc():
    """A tune whose init overwrites an instruction *after* running it.

    The post-init image decodes ``$AD`` (three bytes) where an ``$A8`` (one) ran,
    so any walk of the image past that byte invents instructions no execution
    reached: what S2b's nodes say, and the image does not.
    """
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "JSR t0",
        "LDA #$AD",
        "STA t0",
        "RTS",
        "play: INC cnt",
        "RTS",
        "t0: TAY",
        "RTS",
        "cnt: BRK",
        "BRK",
        "BRK",
    )


def test_the_instructions_a_code_holds_are_the_ones_an_execution_reached():
    """``Code.pcs`` is the trace's own answer, not a walk of the image's decode."""
    for code in (smc(), cascade(), voices_snippet()):
        trace = front(code, calls=6)[0]
        prog, _rgn, procs = pipeline.build(trace, "snippet", copies=False)
        ran = {x["pc"] for x in trace.sites.values() if x["count"]}
        band = tuple(trace.meta["load"])
        for name in prog.procs:
            got = siblings._code(procs[name].nodes, trace.image_post_init, band)
            assert set(got.pcs) <= ran, (name, ["%04X" % p for p in set(got.pcs) - ran])
            assert all(got.ran(p) > 0 for p in got.pcs)
    # and the image really does decode past the patched byte, so the guard bites
    code = smc()
    t0 = code.labels["t0"]
    img = _image(code)
    img[t0] = 0xAD
    assert siblings.stream(img, t0, t0 + 6)[:2] == [t0, t0 + 3]


def voices_snippet():
    """Three chained copies, as the boundary-invariance generator makes them."""
    return Family(random.Random(0), k=3, n=8).code


def cascade(k=3, silent=False):
    """Automatas' shape: a copy opens on a branch the run only ever takes one way.

    Nothing jumps to a copy: each falls into the next, on the ``N`` the copy before
    it left. The trace-closed build gives the untaken arm a block whose ``src`` is
    the copy's entry, and the static closure deletes it -- which is what P2's
    discovery was seeded by.
    """
    src = ["init: LDA #$00", "STA cnt"] + ["STA d%d" % j for j in range(k)]
    src += ["RTS", "play: LDA d0"]
    for j in range(k):
        src += [
            "c%d: BMI a%d" % (j, j),
            "LDA d%d" % j,
            "CLC",
            "ADC #$01",
            "STA d%d" % j,
            "LDA d%d" % ((j + 1) % k),
        ]
    src = _silence(src, k) if silent else src
    src += ["after: INC cnt", "RTS"]
    for j in range(k):
        src += ["a%d: LDA #$FF" % j, "STA d%d" % j, "JMP after"]
    return asm(PLAY, *src, *["d%d: BRK" % j for j in range(k)], "cnt: BRK")


def _silence(src, k):
    """Put the last copy behind a jump, so no execution ever enters it."""
    at = src.index("c%d: BMI a%d" % (k - 1, k - 1))
    return src[:at] + ["JMP after"] + src[at:]


def test_a_copy_no_execution_entered_is_no_copy():
    """The documented boundary: a chain runs every copy of it as often."""
    code = cascade(silent=True)
    want = tuple(code.labels["c%d" % j] for j in range(3))
    for static in (False, True):
        trace = front(code, calls=6)[0]
        prog, _rgn, procs = pipeline.build(trace, "snippet", copies=False, static=static)
        got = siblings.correspond(prog, trace.image_post_init, tuple(trace.meta["load"]), procs)
        assert len(got) == 1 and got[0].bases == want[:2], (static, got)
        assert want[2] not in got[0].bases  # nothing entered it, so it is not a copy


def test_a_copy_entered_by_falling_in_is_found_whatever_the_untaken_arm_left():
    """The P2 diagnosis as a test: the block the trap made is not what seeds a base."""
    code = cascade()
    want = tuple(code.labels["c%d" % j] for j in range(3))
    seen = []
    for static in (False, True):
        trace = front(code, calls=6)[0]
        prog, _rgn, procs = pipeline.build(trace, "snippet", copies=False, static=static)
        got = siblings.correspond(prog, trace.image_post_init, tuple(trace.meta["load"]), procs)
        assert len(got) == 1 and got[0].bases == want, (static, got)
        assert len(got[0].rows) == 6
        seen.append(set(want) <= {b.src for p in prog.procs.values() for b in p.blocks.values()})
    assert seen == [True, False]  # the trap carried the entries; closing them took them away


SHAPES = [(calls, static) for calls in (6, 9) for static in (False, True)]


def _srcs(prog):
    """Every address the built blocks begin at."""
    return {b.src for p in prog.procs.values() for b in p.blocks.values()}


@pytest.mark.parametrize("seed", SEEDS)
def test_the_family_is_the_same_under_every_block_shape(seed):
    """A copy's entry is a property of the image, not of the blocks a build makes.

    Each copy runs an arm another never reaches, the horizon decides which, and the
    static closure joins the arms none ran or leaves them ``trap 'untaken'``: four
    block shapes over one image, and one family under all of them.
    """
    got, merged = set(), False
    for calls, static in SHAPES:
        fam = Family(random.Random(seed + 500), k=3, n=8)
        out = fam.found(calls=calls, static=static)
        assert len(out) == 1 and out[0].k == 3, (calls, static, out)
        assert out[0].rows[0] == fam.bases(), (calls, static, out[0].rows[0])
        got.add((out[0].bases, out[0].rows))
        merged = merged or not set(fam.bases()) <= _srcs(fam.prog)
    assert len(got) == 1, sorted(got)
    assert merged  # S2b glued a copy entry into the block before it, and it held


@pytest.mark.parametrize("seed", SEEDS)
def test_unrelated_chained_code_that_ran_as_often_is_no_family(seed):
    """Chained is not copied: segments that do not align hold no correspondence."""
    fam = Family(random.Random(seed + 600), k=3, n=8, diverge=True)
    got = [f for f in fam.found() if len(f.rows) > 1]
    assert not got, [(f.k, [hex(b) for b in f.bases]) for f in got]


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
    procs = front(code, calls=8, data=data)[4]  # S2b's own count per instruction
    img, band = trace.image_post_init, tuple(trace.meta["load"])
    assert jumptab.enumerate_targets(prog) == 3  # the entry no copy dispatched, per copy
    fams = siblings.correspond(prog, img, band, procs)
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
