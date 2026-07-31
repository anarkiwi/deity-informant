"""Declaration extents: observation floors a region, the sound ceiling bounds it."""

from deity_informant import datadecl as D
from deity_informant import expr as E

_UNPROVEN = E.reg(0)  # an 8-bit register index: no bound below the width mask
_PROVEN = ("op", "INT_AND", (E.reg(0), ("const", 3, 1)), 1)


def _grp(base, reads=(), stride=1):
    r = sorted(reads)
    return {
        "base": base,
        "fields": [base],
        "stride": stride,
        "reads": r,
        "top": r[-1] if r else -1,
    }


def _sites(*bases, proven=False):
    return {b: [_PROVEN if proven else _UNPROVEN] for b in bases}


def _regions(groups, code=()):
    """``_regions`` over groups whose reads are pinned by one site pc each."""
    rd_pcs = {("t", g["base"]): {i} for i, g in enumerate(groups)}
    by_pc = {i: g["reads"] for i, g in enumerate(groups)}
    sites = _sites(*(g["base"] for g in groups))
    return D._regions(
        [_grp(g["base"], (), g["stride"]) for g in groups], sites, rd_pcs, by_pc, code
    )


def _ext(base, reads, sites=None, stride=1, **kw):
    g = _grp(base, reads, stride)
    return D._extent(g, sites or _sites(base), kw.pop("bounds", []), kw.pop("code", []), **kw)


def test_observed_run_is_a_floor_not_the_extent():
    """A prefix-indexing run declares the whole region up to the next boundary."""
    assert _ext(0x2000, [0x2000, 0x2020], bounds=[0x2000, 0x2080]) == (0x80, [], True)
    assert _ext(0x2000, [0x2000, 0x2020]) == (0x100, [], True)  # capped by the 8-bit index
    assert _ext(0x2000, [0x2000], bounds=[0x2000], code=[0x2040]) == (0x40, [], True)
    assert _ext(0x2000, []) == (0, [], True)  # nothing read: nothing declared


def test_proven_index_domain_sizes_exactly():
    assert _ext(0x2000, [0x2000], sites=_sites(0x2000, proven=True)) == (4, [], False)


def test_extension_stops_at_a_play_written_cell():
    """The run past the floor is const only while the play phase never writes."""
    assert _ext(0x2000, [0x2000, 0x2010], mut=frozenset({0x2040})) == (0x40, [], True)
    assert _ext(0x2000, [0x2000, 0x2010], mut=frozenset({0x2011})) == (0x11, [], True)


def test_flat_region_excludes_the_cells_the_play_phase_writes():
    """A flat region is one record, so a written offset names that cell alone."""
    reads, bounds = range(0x2000, 0x2040), [0x2000, 0x2040]
    assert _ext(0x2000, reads, bounds=bounds, mut=frozenset({0x2010})) == (0x40, [0x10], True)
    mut = frozenset({0x2000, 0x203F})
    assert _ext(0x2000, reads, bounds=bounds, mut=mut) == (0x40, [0, 0x3F], True)
    assert _ext(0x2000, [0x2000, 0x2001], pairtabs={0x2000}, mut=frozenset({0x2001})) == (
        2,
        [1],
        True,
    )


def test_strided_block_keeps_the_lanes_the_play_phase_never_writes():
    """A record block is const per lane: a written row names its lane, not the block."""
    reads, bounds = range(0x2000, 0x2040), [0x2000, 0x2040]
    assert _ext(0x2000, reads, stride=8, bounds=bounds, mut=frozenset({0x2010})) == (
        0x40,
        [0],
        True,
    )
    mut = frozenset({0x2013, 0x2028})
    assert _ext(0x2000, reads, stride=8, bounds=bounds, mut=mut) == (0x40, [0, 3], True)
    every = frozenset(range(0x2010, 0x2018))  # a whole record: every lane is mutable
    assert _ext(0x2000, reads, stride=8, bounds=bounds, mut=every) == (0x40, list(range(8)), True)


def test_pointer_reload_table_keeps_the_observed_floor():
    """Its composed words prove exactly the reloaded entries, so it never runs on."""
    assert _ext(0x2000, [0x2000, 0x2001], pairtabs=frozenset({0x2000})) == (2, [], True)


def test_alias_base_is_absorbed_into_the_region_it_indexes():
    """Parallel runs overlapping by more than half are one region at two bases."""
    lo = _grp(0x2000, range(0x2000, 0x2061))
    alias = _grp(0x2001, range(0x2001, 0x2062))
    block = _grp(0x2061, range(0x2061, 0x20C2))
    out = _regions([lo, alias, block])
    assert [g["base"] for g in out] == [0x2000, 0x2061]
    assert out[0]["fields"] == [0x2000, 0x2001]
    assert not D._alias(lo, block)  # the next block is not a field of the first


def test_unwitnessed_base_neither_declares_nor_bounds():
    """A base with no observed read must not truncate its neighbour to nothing."""
    out = _regions([_grp(0x2000, range(0x2000, 0x2060)), _grp(0x2001)])
    assert [g["base"] for g in out] == [0x2000]
    assert _ext(0x2000, range(0x2000, 0x2060), bounds=[0x2000]) == (0x100, [], True)


def test_avail_spans_the_containing_region():
    tables = {0x2000: {"base": 0x2000, "size": 0x40}}
    assert D._avail(tables, 0x2000) == 0x40 and D._avail(tables, 0x2010) == 0x30
    assert D._avail(tables, 0x2040) == 0 and D._avail(tables, 0x1000) == 0
