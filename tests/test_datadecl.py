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


def test_wholly_written_run_declares_its_extent_with_an_empty_const_claim():
    """A per-voice state array is a datum: ``mut`` carries constness, the size stands.

    Its extent is what bounds an index (``avail``), so suppressing the declaration
    would leave every access spanning the whole register range."""
    mut = frozenset(range(0x2000, 0x2100))
    size, moffs, _obs = _ext(0x2000, [0x2000, 0x2001, 0x2002], bounds=[0x2000, 0x2100], mut=mut)
    assert (size, moffs) == (3, [0, 1, 2])
    r = D.Regions([_reg(0x2000, size, mut=moffs)])
    assert r.avail(0x2000) == 3 and not any(r.const_at(a) for a in range(0x2000, 0x2003))


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


def test_a_traversed_run_carries_the_bases_inside_it():
    """Grid_Runner's SID blit: 25 cells read at one site are one datum, not three.

    Its base carved one row because the next base truncated it, so the four latches
    at the far end of the run declared as loose state."""
    blit = _grp(0x2000, range(0x2000, 0x2019))
    out = _regions([blit, _grp(0x2001, [0x2001]), _grp(0x2002, range(0x2002, 0x2004))])
    assert [g["base"] for g in out] == [0x2000]
    assert out[0]["fields"] == [0x2000, 0x2001, 0x2002]
    mut = frozenset(range(0x2000, 0x2019))
    sites = _sites(0x2000, 0x2001, 0x2002)
    assert D._extent(out[0], sites, [0x2000, 0x2019], [], mut)[0] == 0x19


def test_a_sparse_read_map_swallows_no_neighbour():
    """Puke's note table overruns onto a declared neighbour; it does not own it.

    An index that leaves its declaration reads a cell of the next one, which is the
    aliasing the read map reports -- not evidence of a wider extent."""
    over = _grp(0x2000, [0x2000, 0x2001, 0x2041, 0x2060])
    out = _regions([over, _grp(0x2040, range(0x2040, 0x2060))])
    assert [g["base"] for g in out] == [0x2000, 0x2040]
    assert D._extent(out[0], _sites(0x2000), [0x2000, 0x2040], [])[0] == 0x40


def test_a_run_stops_at_the_first_cell_it_was_not_seen_to_read():
    """The run is contiguity from the base, so a gap ends what the traversal proves."""
    reach = [*range(0x2000, 0x2004), 0x2010]
    out = _regions([_grp(0x2000, reach), _grp(0x2002, [0x2002]), _grp(0x2010, [0x2010])])
    assert [g["base"] for g in out] == [0x2000, 0x2010]


def test_unwitnessed_base_neither_declares_nor_bounds():
    """A base with no observed read must not truncate its neighbour to nothing."""
    out = _regions([_grp(0x2000, range(0x2000, 0x2060)), _grp(0x2001)])
    assert [g["base"] for g in out] == [0x2000]
    assert _ext(0x2000, range(0x2000, 0x2060), bounds=[0x2000]) == (0x100, [], True)


def test_a_base_on_the_code_image_is_not_carved_as_data():
    """A declaration carries its base byte, so one there would print code as data.

    The extent claim is not separable from that: the next code byte bounds the
    region, so all it can claim is the instruction byte itself. Spec 2 reads a
    code cell as the state variable it is."""
    lo = _grp(0x2000, range(0x2000, 0x2040))
    on_code = _grp(0x2040, range(0x2040, 0x2080))
    assert [g["base"] for g in _regions([lo, on_code])] == [0x2000, 0x2040]
    assert [g["base"] for g in _regions([lo, on_code], code=[0x2040])] == [0x2000]
    assert _ext(0x2040, [0x2040, 0x2050], code=[0x2040, 0x2041]) == (1, [], True)


def _reg(base, size, stride=1, mut=()):
    return {"base": base, "size": size, "stride": stride, "mut": list(mut)}


def test_regions_answer_containment_offset_and_the_bytes_that_follow():
    """The one declaration index: which datum holds a byte, and where in it."""
    r = D.Regions([_reg(0x2000, 0x40), _reg(0x1000, 0)])
    assert r.at(0x1000) is None and r.at(0x0FFF) is None  # a sizeless region holds nothing
    assert r.at(0x2040) is None and r.at(0x2010)[1] == 0x10
    assert r.avail(0x2000) == 0x40 and r.avail(0x2010) == 0x30
    assert r.avail(0x2040) == 0 and r.avail(0x1000) == 0


def test_regions_read_mut_as_a_lane_when_strided_and_a_cell_when_flat():
    """The #61 const claim: ``mut`` names record offsets, so the record must be right."""
    r = D.Regions([_reg(0x2000, 0x80, stride=2, mut=(1,)), _reg(0x3000, 0x10, mut=(5,))])
    assert r.const_at(0x2000) and not r.const_at(0x2001) and not r.const_at(0x2003)
    assert r.const_at(0x3004) and not r.const_at(0x3005) and r.const_at(0x300F)
    assert not r.const_at(0x2100) and not r.const_at(0x1FFF)


def test_the_lo_hi_partnership_is_a_co_index_claim_not_an_address_order():
    """Two columns of one datum are read at one row or at no row at all.

    The zip by sorted base address asserted a partnership from nothing but the order
    the two tables happen to sit in; the index the two reload reads share is the
    evidence, and a lo column no hi column meets at one index takes no role."""
    y, w = ("op", "INT_ZEXT", (("reg", 2),), 2), ("op", "INT_ZEXT", (("reg", 3),), 2)
    lrows = [(0x1493, y), (0x1499, w), (0x1678, y)]
    hrows = [(0x1496, y), (0x14CB, w), (0x1675, ("op", "INT_ZEXT", (("reg", 9),), 2))]
    assert D._co_indexed(lrows, hrows) == [(0x1493, 0x1496), (0x1499, 0x14CB)]
    assert D._co_indexed([(0x1000, y)], [(0x2000, w)]) == []
    assert D._co_indexed([(0x1000, y)], [(0x1000, y)]) == [], "a column is no partner"
