"""Section 9.1's measurement, hermetic: the object against the tune's load band.

No tune and no HVSC: a PSID file assembled here, so the band is bytes this test
states, and the coverage rule -- a ratio over a fraction of a tune's subtunes is
not a comparison -- is checked on a file that declares more subtunes than the
objects cover.
"""

import json
import struct

from deity_informant.trackerprog import sizes


def psid(body, load=0x1000, songs=1):
    """A minimal PSID v2 file whose data offset is its header length."""
    head = bytearray(0x7C)
    head[0:4] = b"PSID"
    struct.pack_into(">HHHHHH", head, 4, 2, 0x7C, load, load, load, songs)
    struct.pack_into(">H", head, 0x10, 1)  # startsong
    return bytes(head) + body


def test_the_band_is_the_bytes_the_file_loads_and_not_its_header():
    body = bytes(range(256)) * 4
    raw, packed = sizes.band(psid(body))
    assert raw == len(body) and packed == sizes.xz(body)


def test_an_in_body_load_address_is_not_part_of_the_band():
    """``load = 0`` puts the address in the first two bytes, which do not load."""
    body = bytes(500)
    assert sizes.band(psid(b"\x00\x20" + body, load=0))[0] == len(body)


def test_the_row_states_its_coverage_and_its_ratio():
    obj = {"score": {"orders": [1, 2, 3]}, "meta": {"tune": "x"}, "pitch": [1, 2]}
    data = psid(bytes(range(256)) * 8, songs=4)
    row = sizes.tune_row(data, [obj, obj], "t.sid")
    assert (row["songs"], row["certified"]) == (4, 2)
    assert row["ratio"] == row["object_xz"] / row["band_xz"]
    assert "(2 of 4)" in sizes.line(row)


def test_a_tune_certified_whole_says_so_by_saying_nothing():
    obj = {"score": {}, "meta": {}}
    row = sizes.tune_row(psid(bytes(300), songs=1), [obj], "t.sid")
    assert "of" not in sizes.line(row).split("x", 1)[1]


def test_the_halves_split_the_object_and_nothing_falls_between_them():
    obj = {"score": {"orders": [[1, 2], [3]]}, "meta": {"a": 1}, "accs": {"v": {"rank": 0}}}
    h = sizes.halves(obj)
    assert json.loads(sizes.compact(obj)) == obj  # compact is the object, not a print of it
    assert h["score_raw"] == len(sizes.compact(obj["score"]))
    assert h["rest_raw"] == len(sizes.compact({"meta": obj["meta"], "accs": obj["accs"]}))


def test_the_summed_form_is_never_smaller_than_the_concatenated_one():
    """Two objects share bytes, so xz of both together is the honest numerator."""
    obj = {"score": {"orders": list(range(200))}, "meta": {"tune": "x"}}
    row = sizes.tune_row(psid(bytes(400)), [obj, obj], "t.sid")
    assert row["object_xz"] < row["summed_xz"]


def test_section_9_1_quotes_the_tune_set_the_registry_has():
    """The table's rows are the registry's tunes, so neither can drift alone."""
    import re
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(root / "tools"))
    import trackerprog_poison as TP  # noqa: E402

    text = (root / "docs" / "prototype-trackerprog.md").read_text()
    section = text.split("### 9.1 The object against the load band", 1)[1]
    table = section.split("**The claim does not hold", 1)[0]
    quoted = re.findall(r"^\| \*(.+?)\* \((.+?)\) \| (\d+) \| \*{0,2}(\d+)\*{0,2} \|", table, re.M)
    assert len(quoted) == len({b.tune for b in TP.BUILDS})
    # every row's "certified" count is how many builds the registry has for that tune
    per = {}
    for b in TP.BUILDS:
        per[b.tune] = per.get(b.tune, 0) + 1
    assert sorted(int(c) for _, _, _, c in quoted) == sorted(per.values())
