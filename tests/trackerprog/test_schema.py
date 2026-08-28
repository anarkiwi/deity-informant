"""The schema check: a trackerprog object carrying program residue refuses by name."""

from deity_informant.trackerprog.certify import schema_check

CLEAN = {
    "meta": {"source": {"tune": "x"}, "commit_order": ["ad", "sr", "ctrl"], "note": "$1000"},
    "pitch": [0x0112, 0x0123],
    "instruments": {"kind": "selector", "cursor": "ins_idx@$1020", "rows": {1: {"ad": 9}}},
    "streams": [{"cursor": "wave_idx@$1030", "columns": [{"table": "wave", "bytes": [1, 2]}]}],
    "accs": {"a0": {"id": "a0", "cell": {"name": "vib_phase"}}},
    "producers": [
        {"register": "freq_lo", "when": ["(phase == 2)", "not (hold == 0)"], "accs": ["a0"]},
        {"register": None, "kind": "file", "when": [], "accs": []},
    ],
    "score": {"voices": [{"rows": [{"sets": [["phase", 3, 1], ["sid[0].ctrl", 65, 1]]}]}]},
    "globals": {},
    "inputs": {0xD012: 0},
}


def _with(**kw):
    return {**CLEAN, **kw}


def _details(tp):
    return [r.detail for r in schema_check(tp)]


def test_a_clean_object_passes_with_addresses_only_where_they_are_data():
    assert schema_check(CLEAN) == []


def test_an_ssa_temp_in_any_string_refuses():
    for temp in ("u4_L1268_BD#1", "X#2", "$saved6"):
        bad = _with(producers=[{"register": "ad", "when": ["(%s == 0)" % temp], "accs": []}])
        (r,) = schema_check(bad)
        assert r.why == "program residue" and r.cell == "producers/*/when/*"
        assert r.detail.startswith("temp %s in" % temp)


def test_a_bare_address_outside_the_addressed_sections_refuses():
    bad = _with(score={"voices": [{"rows": [{"sets": [["$10A0", 1, 1], ["sid[$D404]", 65, 1]]}]}]})
    got = _details(bad)
    assert got == ["address $10A0 in '$10A0'", "address $D404 in 'sid[$D404]'"]
    assert schema_check(_with(producers=[{"register": "ad", "site": {"pc": "$1234"}}])) == []
    assert schema_check(_with(meta={"pc": "$1234"}, pitch=["$1234"])) == []
    assert schema_check(_with(accs={"a0": {"id": "a0", "site": {"sites": ["$1234"]}}})) == []
    assert schema_check(_with(producers=[{"register": "ad", "pc": "$1234"}]))


def test_a_program_block_refuses_once_by_kind():
    items = [{"kind": "store", "rank": [0, 1], "pc": "$1000"}, {"kind": "block", "rank": [1]}]
    assert _details(_with(globals={"items": items})) == [
        "program block store",
        "program block block",
    ]


def test_a_producer_refers_to_a_defined_acc_and_names_its_register():
    bad = _with(producers=[{"register": "ad", "accs": ["a9"]}, {"kind": "let", "accs": []}])
    assert _details(bad) == ["acc a9 not in accs", "no register"]


def test_refusals_are_one_per_offending_string_however_many_rows_carry_it():
    rows = [{"sets": [["sid[$D404]", 1, 1]]} for _ in range(50)]
    assert len(schema_check(_with(score={"voices": [{"rows": rows}]}))) == 1
