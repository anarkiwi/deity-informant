"""tools/profsum.py, corpusequiv.py and factsequiv.py: profile summaries and run equivalence."""

import json
import sys
from pathlib import Path

import pytest
import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
# pylint: disable=wrong-import-position,import-error
import corpusequiv  # noqa: E402
import profsum  # noqa: E402

from deity_informant import NATIVE, trace  # noqa: E402


def test_profsum(tmp_path, capsys):
    p = tmp_path / "prof.txt"
    p.write_text(
        "main (a.py:1);run (b.py:3);step (c.so) 6\n"
        "main (a.py:1);run (b.py:4);snap (c.so) 3\n"
        "main (a.py:1);load (d.py:9) 1\n"
        "garbage\n"
    )
    assert profsum.main([str(p), "--top", "2", "--match", "run (", "--under", "run ("]) == 0
    out = capsys.readouterr().out
    assert "samples 10" in out and " 60.0% self  step (c.so)" in out and "snap" in out
    assert " 90.0% incl  run (" in out and " 60.0% under step (c.so)" in out
    assert profsum.main([str(p), "--within", "load", "--top", "1"]) == 0
    assert "100.0% self  load (d.py)" in capsys.readouterr().out


def _run(path, rows, facts=()):
    path.mkdir()
    (path / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if facts:
        z = zstandard.ZstdCompressor()
        (path / "facts.jsonl.zst").write_bytes(
            b"".join(z.compress((json.dumps(f) + "\n").encode()) for f in facts)
        )
    return str(path)


def test_corpusequiv(tmp_path, capsys):
    row = {"path": "a", "song": 1, "mode": "flat", "exact": True, "timing": {"calls": 9}}
    fact = {"path": "a", "song": 1, "facts": {"starts": [1]}}
    before = _run(
        tmp_path / "b",
        [
            row | {"song_ms": 40.0, "timing": {"calls": 9, "lifted_s": 1.0}},
            {"path": "i", "song": 1, "irq": {}, "song_ms": 30.0, "cells": [1]},
        ],
        [fact],
    )
    same = _run(
        tmp_path / "s",
        [
            row | {"song_ms": 10.0, "facts_ms": 3.0, "timing": {"calls": 9, "lifted_s": 0.1}},
            {"path": "i", "song": 1, "irq": {}, "song_ms": 10.0, "cells": [1]},
            {"path": "new", "song": 1},
        ],
        [fact],
    )
    assert corpusequiv.main([before, same]) == 0
    out = capsys.readouterr().out
    assert "compared 2 songs, 0 differ" in out and "flat: n=1 speedup p10 4.00" in out
    assert "irq: n=1 speedup p10 3.00" in out
    other = _run(
        tmp_path / "o",
        [row | {"exact": False}, {"path": "i", "song": 1, "irq": {}, "cells": [2]}],
        [fact | {"facts": {"starts": [2]}}],
    )
    assert corpusequiv.main([before, other]) == 1
    out = capsys.readouterr().out
    assert "2 differ" in out and "a #1: exact, facts" in out and "i #1: cells" in out


@pytest.mark.skipif(not NATIVE, reason="needs the native extension")
def test_factsequiv(tmp_path, capsys):
    import factsequiv  # pylint: disable=import-outside-toplevel

    code = bytes.fromhex("a9018d00d4a9028d01d460")  # LDA #1; STA $D400; LDA #2; STA $D401; RTS
    hdr = b"PSID" + (2).to_bytes(2, "big") + (0x7C).to_bytes(2, "big") + bytes(2)
    hdr += b"".join(v.to_bytes(2, "big") for v in (0x1000, 0x1000, 1, 1)) + bytes(4)
    data = hdr + bytes(0x7C - len(hdr)) + (0x1000).to_bytes(2, "little") + code
    (tmp_path / "t.sid").write_bytes(data)
    img = factsequiv.wpcorpus.sidfile(data)[1]
    facts = trace.collect(img, lambda s: s.call(0x1000), 10_000).to_dict()
    row = {"path": "t.sid", "mode": "flat", "play": 0x1000, "extra_sids": []}
    rec = {"path": "t.sid", "state": [], "facts": facts}
    variants = {
        "same": rec,
        "state": rec | {"state": [[0x1005, "60"]]},
        "facts": rec | {"facts": facts | {"writes": {0x1001: [5]}}},
    }
    rows, recs = [], []
    for song, (cause, after) in enumerate(variants.items(), 1):
        got, diff = factsequiv.attribute(img, row, rec, after)
        assert got == cause and diff == ([0x1005] if cause == "state" else [])
        rows.append(row | {"song": song})
        recs.append(after | {"song": song})
    before = _run(tmp_path / "b", rows, [rec | {"song": r["song"]} for r in rows])
    after = _run(tmp_path / "a", rows, recs)
    assert factsequiv.main([str(tmp_path), before, after, "--list"]) == 0
    out = capsys.readouterr().out
    assert "3 flat songs: facts 1, same 1, state 1" in out and "t.sid #2: state" in out
