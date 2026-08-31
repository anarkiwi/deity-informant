"""Section 5's grammar box against the record the tools write and the player reads.

Section 3.1's rule -- a field the object writes and no consumer reads is not a
field, and a field only the print reads is an annotation -- is checkable, so it
is checked here rather than asserted in prose.  Hermetic: the field names come
from the nine tools' own objects via the poison registry's cached builds, and
the readers from the player's source.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

DOC = ROOT / "docs" / "prototype-trackerprog.md"
PLAYER = ROOT / "deity_informant" / "trackerprog" / "universal.py"
PRINTER = ROOT / "deity_informant" / "trackerprog" / "printer.py"

# read by the player, in the box; annotations, marked as such in the box
READ = set(
    "cell width produce delta bound policy rate phase amplitude flag rank"
    " when step_when delta_when gate emit beyond trap".split()
)
ANNOTATIONS = {"target", "scope", "note"}


def box():
    """The field names section 5's ``Acc = { … }`` block declares at its top level.

    A field of the record starts in the column the opening brace sets; anything
    deeper (``bound``'s own ``witness``, ``flag``'s ``seed``) belongs to a field
    and is not one.
    """
    text = DOC.read_text().split("## 5. Effects as bounded accumulators", 1)[1]
    block = text.split("```", 2)[1]
    return {m.group(1) for m in re.finditer(r"^(?:Acc = \{ |      , )([a-z_]+) *:", block, re.M)}


def test_the_box_names_every_field_and_nothing_else():
    assert box() == READ | ANNOTATIONS


def test_every_field_the_box_calls_read_has_a_reader_in_the_player():
    src = PLAYER.read_text()
    for name in READ:
        assert re.search(r'["\']%s["\']' % name, src), name


def test_every_annotation_is_read_by_the_print_and_not_by_the_player():
    """An annotation the player reads would be a field; one nothing reads is dead."""
    printed = PRINTER.read_text()
    for name in ANNOTATIONS:
        assert re.search(r'["\']%s["\']' % name, printed), name
    # `target` and `scope` are named nowhere in the player's own source
    src = PLAYER.read_text()
    for name in ("target", "scope"):
        assert not re.search(r'a\[["\']%s["\']\]|a\.get\(["\']%s["\']' % (name, name), src), name


def test_the_box_carries_no_policy_no_tool_writes():
    """``halt`` was spec-only: no arm in ``apply``, no tool emitting it."""
    text = DOC.read_text().split("## 5. Effects as bounded accumulators", 1)[1]
    assert "halt" not in text.split("```", 2)[1]


@pytest.mark.hvsc
def test_the_nine_families_write_exactly_the_boxs_fields():
    """The union over every family's accumulators is the record, field for field."""
    import trackerprog_poison as TP

    seen = set()
    for module in {b.module for b in TP.BUILDS}:
        b = next(x for x in TP.BUILDS if x.module == module)
        obj = TP.build_object(b.name, str(TP.DEFAULT_CACHE))
        for acc in obj["accs"].values():
            seen |= set(acc)
    assert seen == READ | ANNOTATIONS
