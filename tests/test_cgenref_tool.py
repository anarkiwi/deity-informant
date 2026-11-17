"""tools/cgenref.py: generated-output references, their diff and the translate dump."""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import cgenref  # noqa: E402 pylint: disable=wrong-import-position,import-error

import wholeprog_progs as W  # noqa: E402 pylint: disable=wrong-import-position
from deity_informant import cgen  # noqa: E402 pylint: disable=wrong-import-position


def _hex(obj):
    h = hashlib.sha256()
    cgenref.digest(h, obj)
    return h.hexdigest()


def test_canonical_is_json_of_the_values():
    info = {(1, "a"): {3, 1, 2}, "b": np.arange(3), "c": b"\x01\xff", "d": np.int64(7)}
    got = cgenref.canonical(info)
    assert got == {"(1, 'a')": [1, 2, 3], "b": [0, 1, 2], "c": "01ff", "d": 7}
    assert json.loads(json.dumps(got)) == got
    assert cgenref.canonical(range(2)) == [0, 1] and cgenref.canonical(len) == repr(len)


def test_digest_is_by_value():
    img = bytearray(16)
    assert _hex((img, {"a": 1, "b": {2, 3}})) == _hex((bytes(16), {"b": {3, 2}, "a": 1}))
    assert _hex((img,)) != _hex((bytes(15),))
    assert _hex(np.zeros(4, np.uint8)) != _hex(np.zeros(4, np.int8))
    assert _hex([len]) == _hex([len]) != _hex([print])


def _tree(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def test_diff_counts_changed_and_missing_cases(tmp_path, capsys):
    ref = _tree(tmp_path / "ref", {"gi/a.c": "x\n", "gi/b.h": "y\n", "tests/k.c": "t\n"})
    same = _tree(tmp_path / "same", {"gi/a.c": "x\n", "gi/b.h": "y\n", "tests/j.c": "u\n"})
    assert cgenref.diff(ref, same) == 0
    assert "0 differing of 2 common files" in capsys.readouterr().out
    new = _tree(tmp_path / "new", {"gi/a.c": "z\n", "tests/k.c": "t\n"})
    assert cgenref.diff(ref, new) == 2
    out = capsys.readouterr().out
    assert "-x" in out and "+z" in out and "gi/b.h: only in" in out
    assert cgenref.main(["diff", str(ref), str(new)]) == 1


def test_plugin_dumps_each_translation_by_its_arguments(tmp_path, monkeypatch):
    def fake(image, entries, volatile, **kw):
        return f"c{len(image)}{entries}", f"h{kw}", {"times": {"lift": 1.0}, "traps": {1: 2}}

    monkeypatch.setenv(cgenref.DUMP, str(tmp_path))
    monkeypatch.setattr(cgen, "translate", fake)
    cleanups = []
    cgenref.pytest_configure(SimpleNamespace(add_cleanup=cleanups.append))
    for _ in range(2):
        out = cgen.translate(bytearray(4), [(1, "e")], [W.IO], header="t.h")
    cgen.translate(bytes(5), [(1, "e")], [W.IO])
    for f in cleanups:
        f()
    assert cgen.translate is fake
    names = sorted({p.stem for p in tmp_path.iterdir()})
    assert len(names) == 2
    (name,) = [n for n in names if (tmp_path / f"{n}.c").read_text() == out[0]]
    assert (tmp_path / f"{name}.h").read_text() == out[1]
    assert json.loads((tmp_path / f"{name}.json").read_text()) == {"traps": {"1": 2}}


def test_plugin_is_inert_without_a_dump(monkeypatch):
    monkeypatch.delenv(cgenref.DUMP, raising=False)
    inner = cgen.translate
    cgenref.pytest_configure(None)
    assert cgen.translate is inner
