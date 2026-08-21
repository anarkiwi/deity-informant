"""One canonical tune map: every certificate resolves through it, nothing else holds a path."""

import ast
import json
from pathlib import Path

from deity_informant.tuneprog import tunes

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (ROOT / "deity_informant", ROOT / "tests", ROOT / "tools")
MAP = Path(tunes.__file__).resolve()


def _literals():
    """Every ``*.sid`` string literal in the sources, by file (the map excepted)."""
    out = {}
    for root in SOURCES:
        for f in sorted(root.rglob("*.py")):
            if f.resolve() == MAP:
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            hits = {
                n.value
                for n in ast.walk(tree)
                if isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and n.value.endswith(".sid")
            }
            if hits:
                out[f.relative_to(ROOT)] = hits
    return out


def _certificates():
    return [
        json.loads(p.read_text()) for p in sorted((ROOT / "docs" / "certificates").glob("*.json"))
    ]


def test_every_certificates_tune_resolves_through_the_map():
    named = {doc["tune"] for doc in _certificates()}
    assert named and named <= set(tunes.HVSC)
    assert all(tunes.path(n).endswith("/" + n) for n in tunes.HVSC)


def test_no_source_outside_the_map_holds_an_hvsc_path():
    """A tune is named by basename everywhere else, so adding one is one line."""
    bad = {f: sorted(h for h in hits if "/" in h) for f, hits in _literals().items()}
    assert not {f: h for f, h in bad.items() if h}


def test_the_map_holds_no_duplicate_and_no_dead_entry():
    paths = list(tunes.HVSC.values())
    assert len(set(paths)) == len(paths)
    used = {doc["tune"] for doc in _certificates()}
    used |= {h for hits in _literals().values() for h in hits}
    assert set(tunes.HVSC) <= used
