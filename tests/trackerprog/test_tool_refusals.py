"""A tool arm no certified tune reaches: refused by name rather than computed.

Hermetic.  The refusal is stated before the command reads anything of the tune,
so it is one call on an instance with no tune behind it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

import trackerprog_sidwizard as TS  # noqa: E402


def test_the_table_pointer_commands_are_refused_by_name():
    """Their parameter is a byte offset and only ``row_of`` maps one to a row."""
    tune = object.__new__(TS.Tune)
    tune.m, tune.L = b"", {}
    for what in TS.FXPOINT:
        with pytest.raises(AssertionError, match="row_of"):
            tune.command(what, 0)


def test_a_command_the_grammar_has_no_form_for_is_still_a_residue():
    """The refusal above is one arm of the tool's own command residue, not a new one."""
    tune = object.__new__(TS.Tune)
    tune.m, tune.L = b"", {}
    with pytest.raises(AssertionError, match="command residue"):
        tune.command("no.such.command", 0)
