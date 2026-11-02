"""Layout map validation: matching rules, ground-truth scores, consistency, sidreloc agreement."""

import json
import struct
import sys
from pathlib import Path

import pytest

from wholeprog_progs import assemble

from deity_informant import NATIVE

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

# pylint: disable=wrong-import-position,wrong-import-order
import layoutscore as ls  # noqa: E402

native = pytest.mark.skipif(not NATIVE, reason="needs the native extension")

SPLIT = """
init:
  RTS
play:
  LDX $30
  INX
  CPX #3
  BNE ok
  LDX #0
ok:
  STX $30
  LDA tlo,X
  STA $FB
  LDA thi,X
  STA $FC
  LDY #0
  LDA ($FB),Y
  STA $1A00,X
  STA $1A80,X
  RTS
tlo: .byte lo(d0),lo(d1),lo(d2)
thi: .byte hi(d0),hi(d1),hi(d2)
d0: .byte 1
d1: .byte 2
d2: .byte 3
"""


def _psid(code, load, play):
    hdr = b"PSID" + struct.pack(">HHHHHHHI", 2, 0x7C, load, load, play, 1, 1, 0)
    return hdr + b"\0" * 0x60 + struct.pack(">HBBBB", 0x14, 0, 0, 0, 0) + code


def _truth(lab, load, size):
    """The truth record of SPLIT assembled at ``load``, as tools/gtruth.py writes it."""
    d = [lab["d0"], lab["d1"], lab["d2"]]
    tab = {"kind": "addr_table", "labels": ["tlo"], "addr": lab["tlo"], "size": 3, "count": 3}
    tab |= {"hi": lab["thi"], "targets": d}
    data = [
        {"kind": "bytes", "labels": [f"d{i}"], "addr": a, "size": 1, "count": 1}
        | {"target_of": [lab["tlo"]]}
        for i, a in enumerate(d)
    ]
    refs = [{"at": lab["tlo"] + i, "in": "data", "role": "lo", "size": 1} for i in range(3)]
    refs += [{"at": lab["thi"] + i, "in": "data", "role": "hi", "size": 1} for i in range(3)]
    refs += [{"at": lab["play"] + 22, "in": "code", "mode": "indy", "target": 0xFB}]
    return {"load": load, "size": size, "sid": "t.sid", "regions": [tab] + data, "refs": refs}


def _fixture(root, variant, org):
    img, lab = assemble(SPLIT, org)
    code = bytes(img[org : lab["d2"] + 1])
    d = root / variant
    d.mkdir(parents=True)
    (d / "t.sid").write_bytes(_psid(code, org, lab["play"]))
    (d / "t.truth.json").write_text(json.dumps(_truth(lab, org, len(code))), encoding="utf-8")
    return lab


def test_match_rules():
    s = ls._span  # pylint: disable=protected-access
    rec = s(0x100, 0x114, stride=7, offsets={0, 1, 2, 3, 4, 5, 6})
    assert ls.match("records", s(0x100, 0x114, stride=7, offsets={2, 3}), rec)
    assert not ls.match("records", s(0x100, 0x114, stride=1, offsets={0}), rec)
    assert not ls.match("records", s(0x200, 0x214, stride=7, offsets={0}), rec)
    arr = s(0x100, 0x114, stride=7, offsets={0, 5})
    assert ls.match("arrays", s(0x105, 0x113, stride=7), arr)
    assert ls.match("arrays", s(0x100, 0x10E, stride=14), arr)
    assert not ls.match("arrays", s(0x103, 0x111, stride=7), arr)
    assert not ls.match("arrays", s(0x100, 0x114, stride=1), arr)
    tab = s(0x300, 0x302, lo_addr=0x300, hi_addr=0x303)
    assert ls.match("tables", s(0x300, 0x302, lo_addr=0x2FF, hi_addr=0x302), tab)
    assert not ls.match("tables", s(0x300, 0x302, lo_addr=0x300, hi_addr=0x304), tab)
    par = s(0x400, 0x40F, addrs=[0x400, 0x408])
    assert ls.match("parallel", s(0x3FF, 0x40F, addrs=[0x3FF, 0x407, 0x40F]), par)
    assert not ls.match("parallel", s(0x400, 0x40F, addrs=[0x400, 0x409]), par)
    assert ls.match("targets", s(5, 5), s(5, 5)) and not ls.match("targets", s(5, 5), s(6, 6))


def test_items_and_judging():
    m = {
        "regions": [
            {"kind": "address_table", "base": 0x1000, "words": [[0, 1]]}
            | {"fields": [{"offset": 0, "width": 1}, {"offset": 1, "width": 1}]},
            {"kind": "word", "base": 0x40, "size": 2, "fields": [{"offset": 0, "width": 2}]},
            {"kind": "record", "base": 0xD400, "stride": 7, "extent": [0xD400, 0xD414]}
            | {"fields": [{"offset": 0, "width": 1}], "io": True},
            {"kind": "record", "base": 0x1102, "stride": 7, "extent": [0x1100, 0x1114]}
            | {"fields": [{"offset": 0, "width": 1}, {"offset": 3, "width": 1}]},
            {"kind": "pointer", "base": 0xFB, "fields": [{"offset": 0, "width": 2}]}
            | {"targets": [0x1200]},
        ]
    }
    p = ls.predicted_items(m)
    assert p["pointers"] == [ls._span(0x1000, 0x1001), ls._span(0xFB, 0xFC)]  # noqa
    assert p["records"][0]["offsets"] == {(0x1102) % 7, (0x1105) % 7}
    assert p["targets"] == [ls._span(0x1200, 0x1200)]  # pylint: disable=protected-access
    t = {"load": 0x1000, "size": 0x100}
    assert ls.judged(p["words"][0], t) and ls.judged(p["pointers"][0], t)
    assert not ls.judged(ls._span(0x3000, 0x3001), t)  # pylint: disable=protected-access
    assert ls.truth_bytes({"refs": [{"in": "data", "role": "addr", "size": 2, "at": 9}]}) == {
        9: "lo",
        10: "hi",
    }


def test_touched_runs():
    runs = [[0x10, 0x12], [0x20, 0x20]]
    hit = ls._touched  # pylint: disable=protected-access
    assert hit(runs, 0x12, 0x15) and hit(runs, 0x18, 0x20) and hit(runs, 0, 0x10)
    assert not hit(runs, 0x13, 0x1F) and not hit(runs, 0x21, 0x30) and not hit([], 0, 9)


def test_operand_and_code_bytes():
    img, lab = assemble(SPLIT)
    im = (0x1000, bytes(img[0x1000 : lab["d2"] + 1]))
    assert ls.operand(im, lab["ok"] + 2) == lab["tlo"]
    assert ls.operand(im, lab["play"]) == 0x30 and ls.operand(im, lab["ok"] - 2) is None
    assert ls.operand(im, 0x3000) is None
    assert ls.code_bytes(im, [lab["ok"]]) == {lab["ok"], lab["ok"] + 1}


def test_agree():
    m = {
        "regions": [
            {"kind": "pointer", "base": 0x1010, "size": 2, "fields": []},
            {"kind": "pointer", "base": 0x1020, "size": 2, "fields": []},
            {"kind": "pointer", "base": 0x1030, "size": 2, "fields": []},
            {"kind": "pointer", "base": 0xFB, "size": 2, "fields": []},
        ],
        "starts": [0x1000],
    }
    rep = {"roles": {"hi": [0x1002, 0x1011, 0x1040], "static": [0x1021], "unused": []}}
    got = ls.agree(m, (0x1000, b"\xad" + bytes(0xFF)), rep)
    assert got == {"layout": 3, "sidreloc": 2, "both": 1, "static": 1, "undecided": 1}


def test_player_groups(tmp_path):
    names = {}
    for name, hdr, ids in (
        ("a", (0x1000, 0x1003), ["GoatTracker_V2.x"]),
        ("b", (0x1000, 0x1003), ["JCH_NewPlayer", "(JCH_NewPlayer_V20)"]),
        ("c", (0x1000, 0), ["GoatTracker_V2.x"]),
        ("d", (0x1000, 0x1003), ["Other"]),
    ):
        p = tmp_path / f"{name}.sid"
        p.write_bytes(_psid(b"\x60" * 4, *hdr))
        names[str(p)] = ids
    (tmp_path / "ids.json").write_text(json.dumps(names), encoding="utf-8")
    got = ls.player_groups(tmp_path / "ids.json", ["GoatTracker_V2", "JCH_NewPlayer"])
    assert got == {
        "GoatTracker_V2.x": [(tmp_path / "a.sid", 1)],
        "JCH_NewPlayer_V20": [(tmp_path / "b.sid", 1)],
    }


def _fake_reloc(path, _frames):
    """A sidreloc report naming the table's high bytes and a code operand's."""
    _, lab = assemble(SPLIT, ls.vtools.sid_body(Path(path).read_bytes())[0])
    hi = [lab["thi"], lab["thi"] + 1, lab["thi"] + 2, lab["ok"] + 4]
    return {"error": "ok", "roles": {"hi": hi, "static": [], "zp": []}}


@native
def test_truth_consistency_sidreloc(tmp_path, monkeypatch, capsys):
    """Two variants of one program: exact scores, full correspondence, sidreloc agreement."""
    gt, cache = tmp_path / "gt", tmp_path / "cache"
    _fixture(gt, "base", 0x1000)
    _fixture(gt, "w2000", 0x2000)
    (tmp_path / "bad.sid").write_bytes(b"nope")
    bad = ls.maps([(tmp_path / "bad.sid", 1)], cache, 20)
    assert "error" in bad[tmp_path / "bad.sid", 1]
    common = ["--gtruth", str(gt), "--cache", str(cache), "--calls", "20", "--jobs", "2"]
    out = tmp_path / "truth.json"
    assert ls.main(["truth", *common, "--json", str(out)]) == 0
    res = json.loads(out.read_text(encoding="utf-8"))
    for v in ("base", "w2000"):
        tot = res[v]["total"]
        for k in ("tables", "pointers", "arrays", "targets", "bytes_lo", "bytes_hi"):
            assert tot[k]["precision"] == tot[k]["recall"] == 1.0, (v, k)
        assert tot["arrays"]["truth"] == 2 and tot["targets"]["recalled_reached"] == 3
        assert tot["parallel"] == ls._ratios(  # pylint: disable=protected-access
            {"pred": 1, "tp": 0, "truth": 0, "recalled": 0, "reached": 0, "recalled_reached": 0}
        )
    capsys.readouterr()
    assert ls.main(["consistency", *common]) == 0
    c = json.loads(capsys.readouterr().out)["gtruth variants"]
    assert c["pairs"] == c["mapped"] == 1
    assert c["regions"] == c["corresponding"] == c["same_shape"] > 0
    assert c["placed"] == c["same_place"] > 0

    monkeypatch.setattr(ls, "_reloc", _fake_reloc)
    out = tmp_path / "reloc.json"
    assert ls.main(["sidreloc", *common, "--json", str(out)]) == 0
    tot = json.loads(out.read_text(encoding="utf-8"))["total"]
    assert tot == {"layout": 6, "sidreloc": 6, "both": 6, "static": 0, "undecided": 0} | {
        "precision": 1.0,
        "recall": 1.0,
    }
