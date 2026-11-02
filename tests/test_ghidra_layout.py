"""tools/ghidra_layout.py: the plan of each region kind, check() against hand-built dumps, and
the selftest under Ghidra headless (skipped unless the ``di-ghidra`` image exists)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

# pylint: disable=wrong-import-position
import ghidra_layout as gl  # noqa: E402
import vtools  # noqa: E402


def _plan(*regions, image=bytes(0x80), load=0x1000):
    return gl.plan({"regions": list(regions)}, image, load)


def _at(pl, addr):
    return [d for d in pl["data"] if d["addr"] == addr]


def _table(**kw):
    return {
        "kind": "address_table",
        "base": 0x1000,
        "stride": 1,
        "count": 3,
        "extent": [0x1000, 0x1005],
        "words": [[0, 3]],
        "fields": [{"offset": 0, "width": 1}, {"offset": 3, "width": 1}],
    } | kw


def test_split_table_refs_follow_the_image():
    img = bytes([0x34, 0x00, 0xFF, 0x12, 0x20, 0x00]) + bytes(0x7A)
    pl = _plan(_table(), image=img)
    want = {(0x1000, 0x1234), (0x1003, 0x1234), (0x1001, 0x2000), (0x1004, 0x2000)}
    assert {(r["from"], r["to"]) for r in pl["refs"]} == want | {(0x1002, 0xFF), (0x1005, 0xFF)}
    assert [(d["addr"], d["type"], d["count"]) for d in pl["data"]] == [
        (0x1000, "byte", 3),
        (0x1003, "byte", 3),
    ]
    assert {(x["addr"], x["name"]) for x in pl["labels"]} == {
        (0x1000, "tab_1000_lo"),
        (0x1003, "tab_1000_hi"),
    }


def test_table_refs_need_the_image_and_unwritten_entries():
    assert not _plan(_table(written=True))["refs"]
    assert not _plan(_table(), image=bytes(5))["refs"]
    assert not _plan(_table(io=True), image=bytes(5))["data"]


def test_split_table_with_stride_is_a_struct_array():
    pl = _plan(_table(stride=2, count=2, extent=[0x1000, 0x1005]))
    assert pl["types"][0] == {
        "name": "tab_1000_lo_t",
        "size": 2,
        "fields": [{"offset": 0, "type": "byte", "count": 1, "name": "f0"}],
    }
    assert [(d["addr"], d["type"], d["count"]) for d in pl["data"]] == [
        (0x1000, "tab_1000_lo_t", 2),
        (0x1003, "tab_1000_hi_t", 2),
    ]


def test_interleaved_tables():
    img = bytes([0x00, 0x20, 0x10, 0x30]) + bytes(0x7C)
    t = _table(stride=2, count=2, extent=[0x1000, 0x1003], words=[[0, 1]])
    pl = _plan(t, image=img)
    assert [(d["type"], d["count"]) for d in pl["data"]] == [("pointer", 2)]
    assert {(r["from"], r["to"]) for r in pl["refs"]} == {
        (0x1000, 0x2000),
        (0x1001, 0x2000),
        (0x1002, 0x3010),
        (0x1003, 0x3010),
    }
    pl = _plan(t | {"stride": 4, "extent": [0x1000, 0x1005]})
    assert pl["types"][0]["size"] == 4 and pl["types"][0]["fields"][0]["type"] == "pointer"
    assert not _plan(t | {"stride": 1})["data"]


def test_pointer_targets_and_ranges():
    p = {"kind": "pointer", "base": 0xFB, "size": 3}
    p |= {"fields": [{"offset": 0, "width": 2}, {"offset": 2, "width": 1}]}
    pl = _plan(p | {"targets": [0x1010, 0x1020]})
    assert [(d["addr"], d["type"]) for d in pl["data"]] == [(0xFB, "pointer"), (0xFD, "byte")]
    assert {(r["from"], r["to"]) for r in pl["refs"]} == {(0xFB, 0x1010), (0xFB, 0x1020)}
    assert not _plan(p | {"target_range": [0x1000, 0x2000]})["refs"]


def test_word_atoms():
    w = {"kind": "word", "base": 0x1040, "size": 5}
    w |= {"fields": [{"offset": 0, "width": 2}, {"offset": 2, "width": 3}]}
    pl = _plan(w)
    assert [(d["addr"], d["type"], d["count"]) for d in pl["data"]] == [
        (0x1040, "word", 1),
        (0x1042, "byte", 3),
    ]
    assert not _plan(w | {"io": True})["data"]


def test_record_struct_geometry():
    r = {"kind": "record", "base": 0x1050, "stride": 5, "count": 3, "extent": [0x1055, 0x1063]}
    r |= {"fields": [{"offset": 0, "width": 1}, {"offset": 2, "width": 2}]}
    pl = _plan(r)
    assert len(pl["types"]) == 1
    t = pl["types"][0]
    assert (t["name"], t["size"]) == ("rec_1050_t", 5)
    assert [(f["offset"], f["type"], f["name"]) for f in t["fields"]] == [
        (0, "byte", "f0"),
        (2, "word", "f2"),
    ]
    assert _at(pl, 0x1055) == [
        {"addr": 0x1055, "type": "rec_1050_t", "count": 3, "name": "rec_1050"}
    ]
    assert pl["labels"] == [{"addr": 0x1050, "name": "rec_1050"}]


def test_arrays_and_unique_struct_names():
    a = {"kind": "array", "base": 0x1060, "count": 4, "extent": [0x1060, 0x1067]}
    pl = _plan(
        a | {"stride": 2, "fields": [{"offset": 0, "width": 2}]},
        a | {"stride": 3, "fields": [{"offset": 0, "width": 1}]},
        a | {"stride": 4, "fields": [{"offset": 0, "width": 1}]},
    )
    assert [(d["type"], d["count"]) for d in pl["data"]] == [
        ("word", 4),
        ("arr_1060_t", 4),
        ("arr_1060_2_t", 4),
    ]
    assert [t["size"] for t in pl["types"]] == [3, 4]


def test_order_and_parallel_comment():
    img, m = gl.fixture()
    pl = gl.plan(m, img, gl.FIXTURE_LOAD)
    kinds = [d["name"].split("_")[0] for d in pl["data"]]
    assert kinds == sorted(kinds, key=["tab", "ptr", "word", "rec", "arr"].index)
    assert pl["comments"] == [{"addr": 0x1078, "text": "parallel array: $1078, $107C"}]
    assert (0xD400, "rec_D400") in {(x["addr"], x["name"]) for x in pl["labels"]}
    assert not _at(pl, 0xD400)


def _dump():
    """The dump Ghidra gives for :func:`_small` applied without conflicts."""
    return {
        "types": [
            {
                "name": "rec_1050_t",
                "size": 3,
                "fields": [
                    {"offset": 0, "name": "f0", "type": "byte", "count": 1, "length": 1},
                    {"offset": 1, "name": "f1", "type": "word", "count": 1, "length": 2},
                ],
            }
        ],
        "data": [
            {"addr": 0xFB, "type": "pointer16", "count": 1, "length": 2},
            {"addr": 0x1050, "type": "rec_1050_t", "count": 2, "length": 6},
        ],
        "labels": [{"addr": 0xFB, "name": "ptr_00FB"}, {"addr": 0x1050, "name": "rec_1050"}],
        "refs": [{"from": 0xFB, "to": 0x1010, "source": "USER_DEFINED"}],
        "comments": [],
        "skipped": [],
    }


def _small():
    p = {"kind": "pointer", "base": 0xFB, "size": 2, "fields": [{"offset": 0, "width": 2}]}
    r = {"kind": "record", "base": 0x1050, "stride": 3, "count": 2, "extent": [0x1050, 0x1055]}
    r |= {"fields": [{"offset": 0, "width": 1}, {"offset": 1, "width": 2}]}
    return _plan(p | {"targets": [0x1010]}, r)


def test_check_accepts_the_matching_dump():
    assert not gl.check(_small(), _dump())


def test_check_reports_each_difference():
    d = _dump()
    d["refs"], d["labels"] = [], d["labels"][:1]
    d["data"][1]["count"] = 1
    d["types"][0]["fields"][1]["type"] = "byte"
    bad = gl.check(_small(), d)
    assert any(b.startswith("ref 00FB->1010") for b in bad)
    assert any(b.startswith("label rec_1050@1050") for b in bad)
    assert any(b.startswith("data rec_1050@1050") for b in bad)
    assert any("field (1, 'f1', 'word', 1) missing" in b for b in bad)
    assert any("field (1, 'f1', 'byte', 1) unplanned" in b for b in bad)
    d = _dump()
    d["types"][0]["size"] = 4
    assert gl.check(_small(), d) == ["type rec_1050_t: want size 3, got 4"]


def test_check_discounts_skips():
    d = _dump()
    d["data"] = d["data"][:1]
    d["types"][0]["fields"] = d["types"][0]["fields"][:1]
    d["skipped"] = [
        {"what": "data", "key": 0x1050, "reason": "conflict"},
        {"what": "field", "key": ["rec_1050_t", 1], "reason": "overlap"},
    ]
    assert not gl.check(_small(), d)
    d["skipped"] = d["skipped"][:1]
    assert gl.check(_small(), d) == ["type rec_1050_t: field (1, 'f1', 'word', 1) missing"]


def test_expectation_rejects_a_changed_dump():
    d = {"types": [], "data": [], "labels": [], "refs": [], "comments": [], "skipped": []}
    assert len(gl.expect_mismatches(d)) == len(gl.EXPECT["types"]) + 5


def test_cli_needs_files():
    with pytest.raises(SystemExit):
        gl.main([])


@pytest.mark.skipif(not vtools.available(gl.IMAGE), reason="di-ghidra image not built")
def test_selftest_under_ghidra(capsys):
    assert gl.main(["--selftest"]) == 0
    assert gl.PASS in capsys.readouterr().out
