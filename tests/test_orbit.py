"""The orbit (``deity_informant.orbit``): play called with no input from init's state repeats,
so the addresses its accesses take and the branch arms it runs over the states up to the repeat
are every run's, the check of a lift's bounds and decisions (no part of the lift)."""

import test_cgen as T
from deity_informant import orbit

pytestmark = T.pytestmark

#: A position stepped by 3 modulo 9 picks a row of ``rows`` through a pair; a row byte of
#: `$80` or more (none is) stores through another pair, which nothing bounds.
STEP = """
init:   LDA #0
        STA pos
        RTS
play:   LDA pos
        CLC
        ADC #3
        CMP #9
        BCC keep
        SBC #9
keep:   STA pos
        ADC #lo(rows)
        STA $FB
        LDA #hi(rows)
        ADC #0
        STA $FC
        LDY #1
rd:     LDA ($FB),Y
        CMP #$80
        BCS dead
        STA $C000
        RTS
dead:   LDY $C001
st:     STA ($FD),Y
        RTS
pos:    .byte 0
rows:   .byte 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
end:
"""


def test_orbit_addresses_and_arms():
    """The orbit closes once init's state (the pair unset) and the three positions it steps
    through have run, its read takes the pair and the rows past them (the wrap adds its
    carry), the compare's branch runs one way; a read of a device, or too few calls, has none."""
    img, labels, _ = T.assemble(STEP)
    state = bytearray(img)
    state[labels["pos"]] = 0
    got = orbit.orbit(bytes(state), labels["play"], [T.W.IO], 50)
    assert got is not None and got[0] == 4
    rows = labels["rows"]
    assert got[1][(labels["rd"], False)] == [0xFB, 0xFC, rows + 2, rows + 4, rows + 7]
    bcs = labels["rd"] + 4
    assert got[2][bcs] == [bcs + 2]
    assert orbit.orbit(bytes(state), labels["play"], [T.W.IO], 2) is None
    assert orbit.orbit(bytes(state), labels["dead"], [T.W.IO], 50) is None


#: A read past a three-entry table whose byte indexes another table.
PAST = """
play:   LDX #5
rd:     LDA tab,X
        TAX
use:    LDA other,X
        STA $C000
        RTS
tab:    .byte 0, 1, 2, 3, 4, 7
other:  .byte 0, 0, 0, 0, 0, 0, 0, 0
end:
"""


#: :data:`PAST` with the index a position stepping 1..7 and 0: its reads pass ``tab + 4``
#: first in the fifth call.
LATE = """
play:   LDX pos
        INX
        CPX #8
        BCC keep
        LDX #0
keep:   STX pos
rd:     LDA tab,X
        TAX
use:    LDA other,X
        STA $C000
        RTS
pos:    .byte 0
tab:    .byte 0, 1, 2, 3, 4, 5, 6, 7
other:  .byte 0, 0, 0, 0, 0, 0, 0, 0
end:
"""


def test_orbit_under_the_overrun_rule():
    """Under a site of the last-entry rule the read past the table reads its last entry, so
    the read it indexes takes that entry's address, not the byte past the table's."""
    img, labels, _ = T.assemble(PAST)
    tab = labels["tab"]
    site = {
        "pc": labels["rd"],
        "addr": [tab, tab + 0xFF],
        "ok": [[tab, tab + 2]],
        "last": [[tab + 2, 1]],
    }
    src = orbit.sources(site)
    assert src[tab + 5] == tab + 2 and src[tab + 1] == -1 and src[tab + 0x100] == -1
    key = (labels["use"], False)
    assert orbit.orbit(bytes(img), labels["play"], [T.W.IO], 5)[1][key] == [labels["other"] + 7]
    got = orbit.orbit(bytes(img), labels["play"], [T.W.IO], 5, [site])
    assert got[1][key] == [labels["other"] + 2]
    assert orbit.sources({**site, "last": None})[tab + 5] == -2


#: :data:`STEP` with a 24-bit count the calls add one to and write out: memory as a whole
#: repeats only after 2**24 calls, the position feeding every address and branch after three.
DRIFT = STEP.replace(
    """play:   LDA pos""",
    """play:   LDA cnt
        CLC
        ADC #1
        STA cnt
        LDA cnt+1
        ADC #0
        STA cnt+1
        LDA cnt+2
        ADC #0
        STA cnt+2
        STA $C002
        LDA pos""",
).replace("pos:    .byte 0", "pos:    .byte 0\ncnt:    .byte 0, 0, 0")


def test_cover_stops_where_the_feed_repeats():
    """Memory never repeats within the limit, so there is no orbit; the position, the only cell
    feeding an address or a branch, repeats after three calls, which cover every access and arm
    the orbit of :data:`STEP` has."""
    img, labels, _ = T.assemble(DRIFT)
    play = labels["play"]
    assert orbit.orbit(bytes(img), play, [T.W.IO], 5000) is None
    calls, got, succ = orbit.cover(bytes(img), play, [T.W.IO], 5000)
    rows = labels["rows"]
    assert calls == 3
    assert got[(labels["rd"], False)] == [0xFB, 0xFC, rows + 2, rows + 4, rows + 7]
    bcs = labels["rd"] + 4
    assert succ[bcs] == [bcs + 2]
    assert orbit.cover(bytes(img), play, [T.W.IO], 2) is None
    assert orbit.cover(bytes(img), labels["dead"], [T.W.IO], 5000) is None


def test_runs_reuse_where_the_sites_change_no_access():
    """A run is made again only where new sites redirect an address the last one accessed,
    taken on from the last one's census at the first call that accesses one: it is the cover
    run anew."""
    img, labels, _ = T.assemble(PAST)
    tab = labels["tab"]
    site = {"pc": labels["rd"], "addr": [tab, tab + 0xFF], "ok": [[tab, tab + 2]]}
    runs = orbit.Runs(labels["play"], [T.W.IO], "cover")
    plain = runs(img)
    assert runs(img, [{**site, "ok": [[tab, tab + 5]]}]) == plain and runs.ran == 1
    redirected = runs(img, [{**site, "last": [[tab + 2, 1]]}])
    assert runs.ran == 2 and redirected[1][(labels["use"], False)] == [labels["other"] + 2]
    img, labels, _ = T.assemble(LATE)
    tab = labels["tab"]
    sites = [{"pc": labels["rd"], "addr": [tab, tab + 0xFF], "ok": [[tab, tab + 4]]}]
    sites[0]["last"] = [[tab + 4, 1]]
    runs = orbit.Runs(labels["play"], [T.W.IO], "cover")
    plain = runs(img)
    ((old, ran),) = runs.census[bytes(img)]
    assert orbit._diverges(ran, old, orbit._tables(sites, runs.vol)) == 4
    redirected = runs(img, sites)
    assert redirected != plain and runs.ran == 2
    assert redirected == orbit.cover(bytes(img), labels["play"], [T.W.IO], sites=sites)
    closing = orbit.Runs(labels["play"], [T.W.IO], 5)
    assert closing(img) == orbit.orbit(img, labels["play"], [T.W.IO], 5)


def test_runs_reuse_where_a_site_spans_the_pointer():
    """A site over all of memory at a read through a pair leaves the pair's bytes as they are,
    so the run of :data:`STEP`, whose reads stay inside the rows, is not made again."""
    img, labels, _ = T.assemble(STEP)
    rows = labels["rows"]
    site = {"pc": labels["rd"], "addr": [0, 0xFFFF], "ok": [[rows, rows + 10]], "last": None}
    runs = orbit.Runs(labels["play"], [T.W.IO], "cover")
    plain = runs(img)
    assert runs(img, [site]) == plain and runs.ran == 1
