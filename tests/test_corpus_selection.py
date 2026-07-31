"""The corpus selection covers every tune a test names, whatever the cap.

A capped run drops the tail of ``CORPUS``, so a test naming a tune beyond the cap
gets no parameters, does not run, and first fails on the push to main. These derive
the named set from the sources and hold the pin list to it.
"""

import ast
from pathlib import PurePath

from _corpus import CORPUS, PINNED, selection

TESTS = PurePath(__file__).parent


def _named(tree):
    """``{(stem, composer or None)}`` a module selects an individual tune by."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            args = [a.value for a in node.args if isinstance(a, ast.Constant)]
            if node.func.id == "_tune" and len(args) == 2:
                out.add((args[0], args[1]))
        if isinstance(node, ast.Compare) and isinstance(node.comparators[0], ast.Constant):
            left, val = node.left, node.comparators[0].value
            if isinstance(left, ast.Attribute) and left.attr == "stem" and isinstance(val, str):
                out.add((val, None))
    return out


def _selectors():
    """Every named-tune selector across the test sources."""
    import pathlib

    out = set()
    for path in sorted(pathlib.Path(TESTS).glob("test_*.py")):
        out |= _named(ast.parse(path.read_text(encoding="utf-8")))
    return out


def _resolves(stem, composer, rels):
    """Does some relpath in ``rels`` satisfy this selector?"""
    for rel in rels:
        p = PurePath(rel)
        if p.stem == stem and (composer is None or p.parent.name == composer):
            return True
    return False


def test_every_named_tune_is_pinned():
    """A test naming a tune must have it pinned, or its assertions never run."""
    missing = sorted(
        "%s/%s" % (composer or "*", stem)
        for stem, composer in _selectors()
        if not _resolves(stem, composer, PINNED)
    )
    assert not missing, "named by a test but not in _corpus.PINNED: %s" % missing


def test_a_capped_selection_still_covers_every_named_tune():
    """The cap bounds the sweep; it must not be able to drop a pinned tune."""
    for limit in (1, 4, 12):
        rels = selection(limit)
        assert len(rels) == len(PINNED) + limit
        for stem, composer in _selectors():
            assert _resolves(stem, composer, rels), (stem, composer, limit)


def test_a_capped_sweep_spans_composers():
    """A prefix samples across the corpus, not the first composer alphabetically."""
    sweep = [r for r in selection(12) if r not in set(PINNED)]
    assert len({PurePath(r).parent.name for r in sweep}) == len(sweep)


def test_the_uncapped_selection_is_the_whole_corpus():
    """No tune is lost or duplicated by pinning."""
    assert sorted(selection(0)) == sorted(CORPUS)
    assert len(set(selection(0))) == len(CORPUS)


def test_pinned_tunes_are_corpus_members():
    """A pin outside CORPUS would never be fetched."""
    assert not set(PINNED) - set(CORPUS)
