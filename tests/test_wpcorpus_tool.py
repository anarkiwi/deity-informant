"""tools/wpcorpus.py: planning, flat/banked/IRQ rows, resumption and the summary."""

import hashlib
import json
import struct
import sys
import time
from pathlib import Path

import pytest

from deity_informant import NATIVE
from deity_informant.smc.machine import core

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wpcorpus  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order
import _common as H  # noqa: E402 pylint: disable=wrong-import-position,wrong-import-order

pytestmark = pytest.mark.skipif(not (NATIVE and core()), reason="needs the native machines")

# init $1000 sets the CIA 1 timer A latch to $0040; play $100B: PHA; JSR $1013; PLA; RTS
FLAT = bytes.fromhex("a9408d04dca9008d05dc60" "4820131068606060" "8d00d460")
BANKED = bytes.fromhex("60" "a9358501a9378501" "8d00d460")  # play stores $01 twice
# init points $0314 at $1010 (LDA $DC0D; STA $D400; KERNAL-style register pulls; RTI)
IRQ = bytes.fromhex("78a9108d1403a9108d150358" "60eaeaea" "ad0ddc8d00d468a868aa6840")


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def sid(code, play, init=0x1000, magic=b"PSID", songs=1, speed=0, flags=0x14, load=0x1000):
    hdr = magic + struct.pack(">HHHHHHHI", 2, 0x7C, load, init, play, songs, 1, speed)
    hdr += b"\0" * 0x60 + struct.pack(">HBBBB", flags, 0, 0, 0, 0)
    return hdr + code


def tree(tmp_path, basic=False):
    files = {
        "flat.sid": sid(FLAT, 0x100B, songs=2, speed=3),
        "banked.sid": sid(BANKED, 0x1001),
        "irq.sid": sid(IRQ, 0, speed=1),
        "bad.sid": b"PSID",
    }
    if basic:
        files["basic.sid"] = sid(H.SONG_POKE, 0, 0, b"RSID", songs=2, flags=0x16, load=0)
    lines = ["[Database]"]
    tmp_path.mkdir()
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        if name != "banked.sid":
            lines.append(f"; /{name}\n{hashlib.md5(data).hexdigest()}=0:01 0:02")
    (tmp_path / "DOCUMENTS").mkdir()
    (tmp_path / "DOCUMENTS" / "Songlengths.md5").write_text("\n".join(lines) + "\n")
    return tmp_path


def rows_of(out):
    return {
        (Path(r["path"]).name, r["song"]): r
        for r in map(json.loads, (out / "rows.jsonl").read_text().splitlines())
    }


def facts_of(out):
    lines = wpcorpus._facts_lines(out / "facts.jsonl.zst")  # pylint: disable=protected-access
    return [json.loads(x) for _, x in lines]


def test_corpus(tmp_path):
    root, out = tree(tmp_path / "hvsc"), tmp_path / "out"
    args = [str(root), "--out", str(out), "--jobs", "2", "--default-length", "0.5"]
    assert wpcorpus.main(args + ["--label-calls", "10"]) == 0
    rows = rows_of(out)
    assert len(rows) == 5
    f1, f2 = rows["flat.sid", 1], rows["flat.sid", 2]
    assert f1["rate_src"] == "cia-init" and f1["rate_hz"] == pytest.approx(985248 / 0x41)
    assert f1["calls"] == f1["calls_run"] == round(985248 / 0x41) and f2["calls"] == round(
        2 * 985248 / 0x41
    )
    assert f1["mode"] == "flat" and f1["exact"] and f1["returned"] and not f1["deopts"]
    assert f1["oracle"] == "calls" and f1["reasons"] == [] and f1["stats"]["stack_private"]
    assert f1["label_calls"] == 10 and set(f1["cells_first"]) == set(f1["cells"])
    assert f1["clock"] == "PAL" and f1["sid_models"] == [1, 0, 0] and f1["speed"] == 1
    assert set(f1["timing"]) >= {"jennings_s", "lifted_s", "reference_s", "cycles"}
    b = rows["banked.sid", 1]
    assert b["length_default"] and b["calls"] == 25 and b["rate_src"] == "vbi"
    assert b["mode"] == "banked" and b["bank_evidence"] == ["port-write"] and b["exact"]
    assert b["oracle"] == "calls" and b["configs"] and b["timing"]["jennings_s"] is None
    i = rows["irq.sid", 1]
    assert i["skip"] == "irq-driven" and i["irq"]["path"] == "kernal" and i["irq"]["idle"]
    assert i["irq"]["kernal_vector"] == 0x1010 and i["irq"]["irqs"] > 0 and i["calls_run"] > 0
    assert i["oracle"] == "frames" and i["label_calls"] == 10 and i["irq"]["init_returned"]
    assert set(i["cells_first"]) == set(i["cells"]) and i["cells"]
    assert rows["bad.sid", 0]["skip"] == "not a sid file"
    recs = facts_of(out)
    assert {(Path(r["path"]).name, r["song"], r["mode"]) for r in recs} == {
        ("flat.sid", 1, "flat"),
        ("flat.sid", 2, "flat"),
        ("banked.sid", 1, "banked"),
    }
    assert all(r["facts"]["starts"] and r["state"] for r in recs)
    s = json.loads((out / "summary.json").read_text())
    assert s["rows"] == 5 and s["by_kind"]["PSID"]["lifted"] == 3
    assert s["by_kind"]["PSID"]["exact"] == 3
    assert s["by_kind"]["PSID"]["skipped"] == {"irq-driven": 1}
    assert s["irq"]["songs"] == 1 and s["speed"]["lifted_calls_s"]["n"] == 3 and not s["failures"]
    assert s["first_seen"]["capped_songs"] == 3 and s["first_seen"]["indices"]["n"] > 0
    assert {"reference", "lifted", "jennings", "check", "cpu_label", "irq_scan"} <= set(
        s["cost_us"]
    )
    assert "## Stage costs" in (out / "summary.md").read_text()

    size = (out / "rows.jsonl").stat().st_size
    assert wpcorpus.main(args) == 0 and (out / "rows.jsonl").stat().st_size == size
    (out / "facts.jsonl.zst").write_bytes((out / "facts.jsonl.zst").read_bytes()[:-5])
    (out / "rows.jsonl").write_text((out / "rows.jsonl").read_text()[:-10])
    assert wpcorpus.main(args) == 0
    assert len(rows_of(out)) == 5 and len(facts_of(out)) == 3


def test_timeout_and_errors(tmp_path, monkeypatch):
    root, out = tree(tmp_path / "hvsc"), tmp_path / "out"
    monkeypatch.setattr(wpcorpus, "run_song", _sleep)
    args = [str(root), "--out", str(out), "--jobs", "2", "--timeout", "0.5", "--call-timeout", "0"]
    assert wpcorpus.main(args) == 0
    assert rows_of(out)["banked.sid", 1]["error"] == "timeout"
    s = json.loads((out / "summary.json").read_text())
    assert s["by_kind"]["PSID"]["timeouts"] == 4 and len(s["failures"]) >= 4
    monkeypatch.undo()
    assert "error" in wpcorpus.run_song(str(root / "missing.sid"), {"path": "m", "song": 1}, 9)[0]


def test_parsers(tmp_path):
    db = tmp_path / "s.md5"
    db.write_text("[Database]\n; /x.sid\nabc=1:02.5 0:03\n")
    assert wpcorpus.songlengths(db) == {"abc": [62.5, 3.0]}
    h = wpcorpus.header(sid(FLAT, 0x100B))
    assert (h["kind"], h["load"], h["play"], h["songs"], h["extra_sids"]) == (
        "PSID",
        0x1000,
        0x100B,
        1,
        [],
    )
    assert wpcorpus.header(b"XXXX" + bytes(0x80)) is None
    assert wpcorpus.latch_of([(0xDC05, 0x12, 0, True)], False) == ((0x12 << 8) | 0x25, True)
    assert wpcorpus.latch_of([], False) == (0x4025, False)
    assert wpcorpus._diff(bytes(4), bytes([0, 1, 2, 0])) == [
        [1, "0102"]
    ]  # pylint: disable=protected-access
    assert wpcorpus.summary([])["rows"] == 0


def _sleep(*_):
    time.sleep(30)


def test_defer_banked(tmp_path):
    """Deferred songs write no row, so a later run without the flag runs them."""
    root, out = tree(tmp_path / "hvsc"), tmp_path / "out"
    args = [str(root), "--out", str(out), "--jobs", "2", "--default-length", "0.5"]
    assert wpcorpus.main(args + ["--defer", "banked,irq"]) == 0
    first = rows_of(out)
    assert ("banked.sid", 1) not in first and first["flat.sid", 1]["mode"] == "flat"
    assert not any("irq" in r for r in first.values())
    assert wpcorpus.main(args) == 0
    rows = rows_of(out)
    assert rows["banked.sid", 1]["mode"] == "banked" and any("irq" in r for r in rows.values())


# init banks the KERNAL out, points RAM $FFFE at $1030 (LDA $DC0D; RTI) and never returns: the
# main context counts in page 1 until it stores to $D020
BUSY = bytes.fromhex(
    "78a9358501a9308dfeffa9108dffff58" "ee5001d0fbee5101ad5101c940d0f1" "8d20d04c1010"
).ljust(0x30, b"\xea") + bytes.fromhex("ad0ddc40")


@pytest.mark.skipif(not wpcorpus.c64.TRACE_NATIVE, reason="needs vagrant-moses' native scan")
@pytest.mark.parametrize(
    "code,frames,mc",
    [(IRQ, 120, 2_000_000), (BUSY, 60, 5000), (BUSY, 2, 5000), (b"\x02", 10, 5000), (FLAT, 5, 300)],
)
def test_irq_scan_backends_agree(code, frames, mc):
    img = wpcorpus.sidfile(sid(code, 0))[1]
    boot = wpcorpus.c64.boot(img, 0x1000, 0x1000 + len(code), 0x1000, 1, kind="PSID")
    scans = [
        wpcorpus._IrqScan(boot.image, backend=b).scan(boot, frames, mc)  # pylint: disable=W0212
        for b in ("python", "native")
    ]
    assert scans[0] == scans[1]


def test_basic_rsid_redo(tmp_path, real):  # pylint: disable=unused-argument
    """A BASIC RSID runs on the real ROMs; --redo replaces rows skipped for a reason in place."""
    root, out = tree(tmp_path / "hvsc", basic=True), tmp_path / "out"
    args = [str(root), "--out", str(out), "--jobs", "2", "--default-length", "0.5"]
    assert wpcorpus.main(args + ["--label-calls", "10"]) == 0
    rows = rows_of(out)
    for song in (1, 2):
        b = rows["basic.sid", song]
        assert b["basic"] and b["skip"] == "irq-driven" and b["oracle"] == "frames"
        assert b["irq"]["path"] == "kernal" and b["irq"]["kernal_vector"] == 0xEA31
        assert b["irq"]["irqs"] > 0 and not b["irq"]["jammed"] and b["cells"]
    lines = (out / "rows.jsonl").read_text().splitlines()
    old = [json.loads(x) for x in lines]
    skipped = "needs the C64 BASIC ROM"
    keep = ("path", "md5", "song", "kind", "flags", "clock", "calls", "length_s", "rate_hz")
    old = [r if "basic" not in r else {k: r[k] for k in keep} | {"skip": skipped} for r in old]
    (out / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in old))
    assert wpcorpus.main(args) == 0 and rows_of(out)["basic.sid", 1]["skip"] == skipped
    assert wpcorpus.main(args + ["--redo", skipped, "--redo", "other"]) == 0
    lines = (out / "rows.jsonl").read_text().splitlines()
    assert len(lines) == len(old) == len(rows_of(out))
    assert rows_of(out)["basic.sid", 2]["irq"] == rows["basic.sid", 2]["irq"]
