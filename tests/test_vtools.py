"""tools/vtools.py, sidid.py, sidreloc.py, gtruth.py: parsing and derivation without Docker."""

import io
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

# pylint: disable=wrong-import-position
import gtruth  # noqa: E402
import sidid  # noqa: E402
import sidreloc  # noqa: E402
import vtools  # noqa: E402

BASE = 0x1000
LABELS = {
    "code": 0x1000,
    "sub": 0x100B,
    "jt": 0x100C,
    "lo": 0x100E,
    "hi": 0x1010,
    "f1": 0x1012,
    "f2": 0x1013,
    "f3": 0x1014,
    "a": 0x101B,
    "b": 0x101F,
    "w": 0x1023,
    "c": 0x1025,
    "p0": 0x1027,
    "p1": 0x1029,
    "ptr": 0x102A,
    "r": 0x102C,
}
ATOMS = [
    {"off": 0, "type": 2, "op": 0xBD, "arg": "lo", "len": 3},
    {"off": 3, "type": 4, "op": 0xA9, "arg": ["%", "lo", 256], "len": 2},
    {"off": 5, "type": 4, "op": 0xA2, "arg": ["/", ["+", "lo", 1], 256], "len": 2},
    {"off": 7, "type": 1, "op": 0x85, "arg": "zpv", "len": 2},
    {"off": 9, "type": 4, "op": 0xA9, "arg": 5, "len": 2},
    {"off": 11, "type": 0, "op": 0x60, "len": 1},
    {"off": 12, "type": 11, "exprs": [["%", "code", 256], ["%", "sub", 256]], "len": 2},
    {"off": 14, "type": 11, "exprs": [["%", "p0", 256], ["%", "p1", 256]], "len": 2},
    {"off": 16, "type": 11, "exprs": [["/", "p0", 256], ["/", "p1", 256]], "len": 2},
    {"off": 18, "type": 11, "exprs": [0], "len": 1},
    {"off": 19, "type": 11, "exprs": [1], "len": 1},
    {"off": 20, "type": 11, "exprs": [2], "len": 1},
    {"off": 21, "type": 11, "exprs": [3, 4, 5], "len": 3},
    {"off": 24, "type": 11, "exprs": [6, 7, 8], "len": 3},
    {"off": 27, "type": 11, "exprs": [1, 2, 3, 4], "len": 4},
    {"off": 31, "type": 11, "exprs": [5, 6, 7, 8], "len": 4},
    {"off": 35, "type": 10, "exprs": ["p0"], "len": 2},
    {"off": 37, "type": 10, "exprs": [0x1234], "len": 2},
    {"off": 39, "type": 11, "exprs": [1, 2], "len": 2},
    {"off": 41, "type": 11, "exprs": [3], "len": 1},
    {"off": 42, "type": 11, "exprs": [["%", "p1", 256], ["/", "p1", 256]], "len": 2},
    {"off": 44, "type": 13, "res": 0, "len": 3},
]
BODY = bytes.fromhex(
    "bd0e10 a90e a210 85fc a905 60 000b 2729 1010 000102 030405 060708"
    "01020304 05060708 2710 3412 0102 03 2910 000000"
)


def _listing():
    syms = [{"sym": n, "value": v, "label": 1} for n, v in LABELS.items()]
    syms.append({"sym": "zpv", "value": 0xFC, "label": 0})
    return [json.dumps(x) for x in ATOMS + syms]


def _psid(body, load=BASE, embed=False):
    head = bytearray(0x7C)
    head[:4] = b"PSID"
    struct.pack_into(">HHHHHHH", head, 4, 2, 0x7C, 0 if embed else load, load, load + 3, 1, 1)
    return bytes(head) + (struct.pack("<H", load) if embed else b"") + body


def test_sid_body_header_and_embedded_load():
    assert vtools.sid_body(_psid(b"\x01\x02")) == (BASE, 0x7C, b"\x01\x02")
    assert vtools.sid_body(_psid(b"\x01\x02", 0x2000, embed=True)) == (0x2000, 0x7E, b"\x01\x02")
    with pytest.raises(ValueError):
        vtools.sid_body(b"PRG" + bytes(200))


def test_docker_cmd_mounts_env_and_user():
    cmd = vtools.docker_cmd(["x", "y"], [("/scratch/a", "/in", True)], {"K": "v"}, "img", True)
    assert cmd[:3] == ["docker", "run", "--rm"] and "-i" in cmd
    assert cmd[cmd.index("-v") + 1] == "/scratch/a:/in:ro"
    assert cmd[cmd.index("-e") + 1] == "K=v" and cmd[-3:] == ["img", "x", "y"]


def test_available_without_docker(monkeypatch):
    monkeypatch.setattr(vtools.shutil, "which", lambda _: None)
    assert not vtools.available()
    assert vtools.main(["check", "--image", "none"]) == 1


def test_c_semantics():
    assert gtruth.ev(["/", -7, 2], {}) == -3 and gtruth.ev(["%", -7, 2], {}) == -1
    assert gtruth.ev(["&&", ["<", "a", 3], ["!", 0]], {"a": 1}) == 1
    assert gtruth.ev(["neg", ["||", 0, 0]], {}) == 0


def test_roles():
    labels, sym = {"t"}, {"t": 0x1234, "K": 7}
    assert gtruth.role(["%", ["+", "t", 2], 256], sym, labels) == ("lo", 0x1236, "t")
    assert gtruth.role(["/", "t", 256], sym, labels) == ("hi", 0x1234, "t")
    assert gtruth.role(["/", "K", 256], sym, labels) == ("const", None, None)
    assert gtruth.role("t", sym, labels) == ("addr", 0x1234, "t")


def test_layout_kinds_and_refs():
    atoms, sym = gtruth.load_listing(_listing())
    regions, refs = gtruth.layout(atoms, sym, BASE)
    by = {r["addr"]: r for r in regions if r["kind"] != "parallel"}
    assert by[0x100C]["kind"] == "addr_lo" and by[0x100C]["targets"] == [0x1000, 0x100B]
    t = by[0x100E]
    assert t["kind"] == "addr_table" and t["hi"] == 0x1010 and t["targets"] == [0x1027, 0x1029]
    assert 0x1010 not in by
    rec = by[0x1012]
    assert (rec["kind"], rec["stride"], rec["count"], rec["size"]) == ("records", 3, 3, 9)
    assert [f["labels"] for f in rec["fields"]] == [["f1"], ["f2"], ["f3"]]
    assert (by[0x1023]["kind"], by[0x1025]["kind"], by[0x102A]["kind"]) == (
        "pointers",
        "words",
        "pointers",
    )
    assert by[0x102A]["targets"] == [0x1029]
    assert by[0x1027]["target_of"] == [0x100E, 0x1023]
    assert by[0x1029]["target_of"] == [0x100E, 0x102A]
    assert (by[0x102C]["kind"], by[0x102C]["size"]) == ("bytes", 3)
    par = [r for r in regions if r["kind"] == "parallel"]
    assert par == [
        {
            "kind": "parallel",
            "labels": [["a"], ["b"]],
            "addrs": [0x101B, 0x101F],
            "addr": 0x101B,
            "size": 8,
            "count": 4,
        }
    ]
    code = {r["at"]: r for r in refs if r["in"] == "code"}
    assert code[0x1001]["mode"] == "absx" and code[0x1001]["mn"] == "LDA"
    assert (code[0x1001]["target"], code[0x1001]["sym"], code[0x1001]["size"]) == (0x100E, "lo", 2)
    assert (code[0x1004]["role"], code[0x1006]["role"], code[0x1006]["offset"]) == ("lo", "hi", 1)
    assert (code[0x1008]["mode"], code[0x1008]["target"], code[0x1008]["sym"]) == (
        "zp",
        0xFC,
        "zpv",
    )
    assert 0x100A not in code
    data = sorted((r["at"], r["role"]) for r in refs if r["in"] == "data")
    assert data == [
        (0x100C, "lo"),
        (0x100D, "lo"),
        (0x100E, "lo"),
        (0x100F, "lo"),
        (0x1010, "hi"),
        (0x1011, "hi"),
        (0x1023, "addr"),
        (0x102A, "lo"),
        (0x102B, "hi"),
    ]


def test_check_and_truth(tmp_path):
    atoms, sym = gtruth.load_listing(_listing())
    assert not gtruth.check(atoms, sym, BASE, BODY)
    bad = bytearray(BODY)
    bad[2] ^= 1
    bad[36] ^= 1
    bad[45] = 9
    assert gtruth.check(atoms, sym, BASE, bad) == [0x1000, 0x1023, 0x102C]
    (tmp_path / "t.sid").write_bytes(_psid(BODY))
    rec = gtruth.truth(_listing(), tmp_path / "t.sid")
    assert (rec["load"], rec["size"], rec["mismatches"]) == (BASE, len(BODY), [])
    assert rec["symbols"]["zpv"] == {"value": 0xFC, "label": False}
    assert gtruth.summarize(rec)["regions"]["addr_table"] == 1


def test_no_records_without_rows():
    segs = [(["x"], [(0, 1, "const", None, None, 0)]), (["y"], [(1, 1, "const", None, None, 1)])]
    assert gtruth.records(segs, 0) is None
    assert gtruth.records([([], segs[0][1])] + segs[1:], 0) is None


def test_pack_and_produce(monkeypatch, tmp_path):
    def run(args, mounts, env, image, timeout):
        assert args[0] == "gt2reloc" and env["GT2_LISTING"] == "/out/song.jsonl"
        out = Path(mounts[0][0])
        (out / "song.jsonl").write_text("\n".join(_listing()))
        (out / "song.sid").write_bytes(_psid(BODY))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gtruth.vtools, "run", run)
    summary = gtruth.produce(tmp_path, ["/x/song.sng"], {"v": ["-W20"]})
    assert summary["v/song"]["mismatches"] == 0
    rec = json.loads((tmp_path / "v" / "song.truth.json").read_text())
    assert (rec["variant"], rec["options"], rec["song"]) == ("v", ["-W20"], "song.sng")
    monkeypatch.setattr(
        gtruth.vtools, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "bad", "")
    )
    with pytest.raises(RuntimeError):
        gtruth.pack("/x/other.sng", tmp_path)


SIDID_OUT = """Using configfile /usr/local/share/sidid/sidid.cfg

000000.sid                                               GoatTracker_V2.x
000001.sid                                               JCH_NewPlayer
                                                         (JCH_NewPlayer_V17)
000002.sid                                               *Unidentified*

Detected players:
GoatTracker_V2.x         1

Statistics:
Identified               2
"""


def test_sidid_parse():
    assert sidid.parse(SIDID_OUT.splitlines()) == {
        0: ["GoatTracker_V2.x"],
        1: ["JCH_NewPlayer", "(JCH_NewPlayer_V17)"],
        2: [],
    }


def test_sidid_identify(monkeypatch, tmp_path):
    files = [tmp_path / "a" / f"{n}.sid" for n in "xyz"] + [tmp_path / "b" / "w.sid"]
    for f in files:
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(b"")
    seen = {}

    class Proc:
        def __init__(self, cmd, **_kw):
            seen["cmd"] = cmd
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(SIDID_OUT)
            self.returncode = 0
            seen["stdin"] = self.stdin
            self.stdin.close = lambda: seen.setdefault("text", self.stdin.getvalue())

        def wait(self, timeout):
            return self.returncode

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(sidid.subprocess, "Popen", Proc)
    got = sidid.identify(sidid.expand([tmp_path / "a", files[3]]), progress=False)
    assert seen["text"].splitlines() == ["a/x.sid", "a/y.sid", "a/z.sid", "b/w.sid"]
    assert f"{tmp_path.resolve()}:/in:ro" in seen["cmd"]
    assert list(got.values()) == [
        ["GoatTracker_V2.x"],
        ["JCH_NewPlayer", "(JCH_NewPlayer_V17)"],
        [],
        [],
    ]
    assert sidid.identify([]) == {}
    out = tmp_path / "r.json"
    assert sidid.main([str(files[0]), "--json", str(out)]) == 0
    assert json.loads(out.read_text()) == {str(files[0].resolve()): ["GoatTracker_V2.x"]}
    Proc.wait = lambda self, timeout: 3
    with pytest.raises(RuntimeError):
        sidid.identify(files[:1], progress=False)


RELOC_ERR = """Relocating from $1000-$13ff to $1100-$14ff
Program map:
1000, 1100:  =R=Z?.
Old zero-page addresses:   fc fd
New zero-page addresses:   80 81
Relocation successful.
"""


def test_sidreloc_parsers():
    assert sidreloc.parse_map(RELOC_ERR) == {
        0x1000: "static",
        0x1001: "hi",
        0x1002: "static",
        0x1003: "zp",
        0x1004: "undetermined",
        0x1005: "unused",
    }
    assert sidreloc.parse_zp(RELOC_ERR) == {0xFC: 0x80, 0xFD: 0x81}
    assert sidreloc.status(0) == ("ok", []) and sidreloc.status(8) == ("no solution", [])
    assert sidreloc.status(96) == ("ok", ["writes outside load range", "pitch/pulse mismatches"])
    assert sidreloc.status(30)[0] == "exit 30"


def test_sidreloc_report():
    old = _psid(bytes.fromhex("a91000fc00ea"))
    new = _psid(bytes.fromhex("a91100800000"), 0x1100)
    rep = sidreloc.report(old, new, RELOC_ERR, 1)
    assert rep["roles"]["hi"] == [0x1001] and rep["roles"]["zp"] == [0x1003]
    assert rep["changed"] == {"4097": [0x10, 0x11], "4099": [0xFC, 0x80], "4101": [0xEA, 0]}
    assert rep["disagree"] == [0x1005]
    same = sidreloc.report(old, old, RELOC_ERR, 1)
    assert same["disagree"] == [0x1001]
    with pytest.raises(ValueError):
        sidreloc.diff(old, old + b"\x00")


def test_sidreloc_relocate(monkeypatch, tmp_path):
    sid = tmp_path / "t.sid"
    sid.write_bytes(_psid(bytes.fromhex("a910fc0000ea")))

    def run(args, mounts, image, timeout):
        assert args[:4] == ["sidreloc", "-v", "-p", "11"] and "-k" in args
        (Path(mounts[1][0]) / "r.sid").write_bytes(_psid(bytes.fromhex("a911fc0000ea"), 0x1100))
        return subprocess.CompletedProcess(args, 32, "", RELOC_ERR)

    monkeypatch.setattr(sidreloc.vtools, "run", run)
    rep = sidreloc.relocate(sid)
    assert (rep["error"], rep["warnings"], rep["disagree"]) == (
        "ok",
        ["writes outside load range"],
        [],
    )
    assert sidreloc.main([str(sid), "--out", str(tmp_path / "o")]) == 0
    assert json.loads((tmp_path / "o" / "t.reloc.json").read_text())["roles"]["hi"] == [0x1001]
    with pytest.raises(ValueError):
        sidreloc.relocate(sid, pages=-0x10)
