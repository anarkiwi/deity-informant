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
COMPILER = ROOT / "deity_informant" / "trackerprog" / "compiler.py"

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


# the policy values and the flag's own fields, which B8 measured down to what the
# families write: `take` is a clamp whose step reaches at once, and the carry the
# delta did not make is `step_when` with `gate.false`
POLICIES = {"wrap", "reflect", "reflect-complement", "clamp", "reload"}
FLAG = {"name", "seed"}
# a form B8 struck: no tool writes it, no line of the player reads it
STRUCK = ("take", "unguarded", "epoch", "links")


def policies():
    """The policy values section 5's ``policy :`` entry names: a word, or the
    first key of a record form."""
    text = DOC.read_text().split("## 5. Effects as bounded accumulators", 1)[1]
    block = text.split("```", 2)[1]
    line = block.split(", policy :", 1)[1].split("\n      ,", 1)[0].split("#")[0]
    return {re.findall(r"[a-z-]+", x)[0] for x in line.split("|")}


def test_the_box_names_the_policy_values_and_the_flags_own_fields():
    assert policies() == POLICIES
    text = DOC.read_text().split("## 5. Effects as bounded accumulators", 1)[1]
    flag = text.split("```", 2)[1].split(", flag   :", 1)[1].split("#")[0]
    assert {m.rstrip("?") for m in re.findall(r"[a-z]+\??", flag)} == FLAG


def test_no_form_b8_struck_survives_in_the_player_or_the_print():
    """A value the schema dropped is a value nothing renders (section 3.1)."""
    src = PLAYER.read_text() + PRINTER.read_text() + COMPILER.read_text()
    for name in STRUCK:
        assert '"%s"' % name not in src, name
    cmd = DOC.read_text().split("### 3.6 score", 1)[1].split("```", 2)[1]
    assert "links" not in cmd.split("Cmd     = {", 1)[1].split("Order", 1)[0]


@pytest.mark.hvsc
def test_the_nine_families_write_the_policy_values_and_flag_fields_the_box_names():
    import trackerprog_poison as TP

    pol, flag = set(), set()
    for module in {b.module for b in TP.BUILDS}:
        b = next(x for x in TP.BUILDS if x.module == module)
        for acc in TP.build_object(b.name, str(TP.DEFAULT_CACHE))["accs"].values():
            p = acc["policy"]
            pol |= {p} if isinstance(p, str) else set(p) - {"when", "edge"}
            flag |= set(acc.get("flag", ()))
    assert pol == POLICIES and flag == FLAG


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


# section 3.5's instrument record: the names the player reads, and the two the
# print knows as columns where a family has them (section 3.1: an annotation)
INS_READ = set("prelude on_note accs pitch transpose pw".split())
INS_PRINTED = {"adsr", "wave"}


def insbox():
    """The field names section 3.5's ``Ins = { … }`` block declares."""
    text = DOC.read_text().split("### 3.5 instruments", 1)[1]
    block = text.split("```", 2)[1]
    return {m.group(1) for m in re.finditer(r"^(?:Ins = \{ |      , )([a-z_]+) *:", block, re.M)}


def test_the_instrument_box_names_what_the_player_reads_and_nothing_else():
    assert insbox() == INS_READ


def test_every_name_in_the_instrument_box_has_a_reader_in_the_player():
    src = PLAYER.read_text()
    for name in INS_READ:
        assert re.search(r'["\']%s["\']' % name, src), name


def test_the_two_instrument_columns_the_print_knows_are_not_player_names():
    """A name only the print reads is an annotation and not a field (section 3.1)."""
    printed = PRINTER.read_text()
    for name in INS_PRINTED:
        assert re.search(r'ins\[["\']%s["\']\]|ins\.get\(["\']%s["\']' % (name, name), printed)
    assert "adsr" not in PLAYER.read_text()  # `wave` is also a cell name, so it is not asked


@pytest.mark.hvsc
def test_no_instrument_column_is_read_by_nothing():
    """Every key an instrument carries past the box is the family's own column,
    so the object's own expressions read it: grep every ``ins``/``insrec`` path.
    """
    import trackerprog_poison as TP

    for module in sorted({b.module for b in TP.BUILDS}):
        b = next(x for x in TP.BUILDS if x.module == module)
        obj = TP.build_object(b.name, str(TP.DEFAULT_CACHE))
        read, keys = set(), set()
        _paths(obj, read)
        for one in obj["instruments"].values():
            keys |= set(one) | set(obj["meta"].get("instrument", {}))
        assert not keys - INS_READ - INS_PRINTED - read, module


def _paths(x, out):
    """Every instrument column the object reads, by the name its path starts with."""
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "ins" and isinstance(v, str):
                out.add(v.split(".", maxsplit=1)[0])
            elif k == "insrec" and isinstance(v, list):
                out.add(str(v[1]).split(".", maxsplit=1)[0])
            _paths(v, out)
    elif isinstance(x, list):
        for y in x:
            _paths(y, out)


# section 5's cell vocabulary, section 3.6's row program and section 4.1's phases:
# three boxes naming what the player itself knows, machine-checked against it
DECLARED = "ins wave orderpos rowsleft dur freq note lastnote".split()
RESERVED = "voice_index counter phase tied freq_hi freq_lo pw pw_lo pw_hi".split()
ROW_STEPS = {"sets", "ins", "stream", "note", "commands", "hold"}
PHASES = {"fetch", "prelude", "row", "machine", "commit"}


def _table(head, marker):
    """The backticked names of the table row whose first column holds ``marker``."""
    text = DOC.read_text().split(head, 1)[1]
    row = next(x for x in text.splitlines() if x.startswith("|") and marker in x)
    return re.findall(r"`([a-z_.<>#]+)`", row.split("|")[2])


def test_the_cell_box_names_the_players_own_state_vector():
    """The eight cells ``Player.__init__`` declares, in the order it declares them."""
    src = PLAYER.read_text().split("self.c = {", 1)[1].split("}", 1)[0]
    assert re.findall(r'"([a-z_]+)":', src) == DECLARED
    assert _table("## 5. Effects as bounded accumulators", "Player.__init__") == DECLARED


def test_the_cell_box_names_what_cell_answers_itself():
    """Every name ``cell()`` special-cases before reading the vector, and no other."""
    body = PLAYER.read_text().split("    def cell(self, name):", 1)[1]
    body = body.split("    def command_of", 1)[0]
    seen = re.findall(r'name (?:==|in \()\s*"([a-z_]+)"(?:, "([a-z_]+)", "([a-z_]+)")?', body)
    got = list(dict.fromkeys(n for group in seen for n in group if n))
    assert got == RESERVED
    assert _table("## 5. Effects as bounded accumulators", "answers itself") == RESERVED


def test_the_row_program_box_names_every_step_row_step_runs():
    """Section 3.6's six steps are ``Player.row_step``'s own branches."""
    body = PLAYER.read_text().split("    def row_step(self", 1)[1].split("    def ties(", 1)[0]
    assert set(re.findall(r'"([a-z]+)" in step', body)) == ROW_STEPS
    text = DOC.read_text().split("### 3.6 score", 1)[1].split("Which steps a tune has", 1)[0]
    assert {m for m in re.findall(r"^\| `\{([a-z]+)\}`", text, re.M)} == ROW_STEPS


def test_the_phase_table_names_every_phase_the_player_has_and_the_stream_phase():
    """Section 4.1's list is ``meta.tick``'s vocabulary: the five named phases the
    player resolves to a ``phase_`` method, and the ``{stream: s}`` entry."""
    assert set(re.findall(r"def phase_([a-z]+)\(", PLAYER.read_text())) == PHASES
    text = DOC.read_text().split("**§4.1 The voice's tick is a declared order.**", 1)[1]
    table = text.split("Which phases a tune has", 1)[0]
    named = re.findall(r"^\| `([a-z]+)` \|", table, re.M)
    assert set(named) == PHASES and "| `{stream: s}` |" in table


def test_section_4s_pseudocode_names_every_procedure_one_tick_runs():
    """A reader holding the code against section 4 holds it against the code: the
    block names the six procedures ``tick`` reaches and nothing it does not."""
    text = DOC.read_text().split("## 4. The universal player", 1)[1]
    block = text.split("```", 2)[1]
    called = {m.group(1) for m in re.finditer(r"^([a-z_]+)\(", block, re.M)}
    assert called == {"tick", "voice", "clock", "channel", "channel_commit", "commit"}
    src = PLAYER.read_text()
    for name in called:
        assert "    def %s(self" % name in src, name


# section 3.3's two readers: what a cursor-stepped row has, and what a guarded one
# has.  The grammar is one; the readers are not, so the box says which reads which
CURSOR_ONLY = {"hold", "next", "jump", "op", "run", "trap"}
ROWS_ONLY = {"when", "point"}


def test_the_step_table_gives_each_field_the_reader_the_player_gives_it():
    """``steprows`` compiles a cursor-stepped row and ``rowplan`` a guarded one."""
    src = PLAYER.read_text()
    cursor = src.split("    def steprows(self", 1)[1].split("    def stream_step(", 1)[0]
    rows = src.split("    def rowplan(self", 1)[1].split("    def runstream(", 1)[0]
    got = set(re.findall(r'r(?:\.get)?[(\[]"([a-z]+)"', cursor))
    got |= set(re.findall(r'"([a-z]+)" (?:not )?in r\b', cursor))
    assert got == CURSOR_ONLY | {"sets"}
    assert set(re.findall(r'r\.get\("([a-z]+)"', rows)) == ROWS_ONLY | {"sets"}
    text = DOC.read_text().split("### 3.3 streams", 1)[1].split("A stream may be reached", 1)[0]
    table = {
        m.group(1): (m.group(2).strip(), m.group(3).strip())
        for m in re.finditer(r"^\| `([a-z]+)`[^|]*\| ([^|]+)\|([^|]+)\|", text, re.M)
    }
    assert set(table) == CURSOR_ONLY | ROWS_ONLY | {"sets"}
    for name, (bycursor, byrows) in table.items():
        assert bycursor.startswith("—") == (name not in CURSOR_ONLY | {"sets"}), name
        assert byrows.startswith("—") == (name not in ROWS_ONLY | {"sets"}), name
