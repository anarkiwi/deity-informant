"""Block arguments computed again where their block starts (``cgen.joins``): played
against the reference, with and without the rewrite."""

import pytest

from test_cgen import body_of, check, entry_calls, fixture_prefix  # pylint: disable=unused-import
from deity_informant.cgen import printer

#: ``A`` at ``join`` is ``tab[X]`` on both edges: the edge past ``top``'s load reloads it
#: (``top`` then only branches).
RELOAD = """
entry:  TAX
        AND #1
        BNE top
        LDA {tab},X
        {between}
        BNE join
        LDX #3
top:    LDA {tab},X
join:   STA $C000
        STX $C001
        RTS
.org $1100
tab:    .byte 0,1,2,0,4,5,6,7,0,9
.org $1200
end:
"""

#: ``A`` at ``out`` is ``cell`` on both edges: just stored on one, loaded on the other.
STORED = """
entry:  TAX
        AND #1
        BEQ other
        LDA cell
        CLC
        ADC #3
        STA cell
        {between}
        DEC cnt
        BNE out
        LDA #9
        STA $C001
        RTS
other:  LDA cell
out:    STA $C000
        {call}
        RTS
bump:   INC {target}
        RTS
cell:   .byte 5
cnt:    .byte 2
other2: .byte 0
end:
"""


def rewrites(prefix, tmp_path, monkeypatch, src, calls):
    """``(rewritten, c, c without the rewrite)``: whether a block argument was computed
    again, and the entry's text with and without it, each played against the reference."""
    seen = []
    real = printer.joins.rematerialise
    monkeypatch.setattr(
        printer.joins, "rematerialise", lambda *a: seen.append(real(*a)) or seen[-1]
    )
    c = body_of(check(prefix, tmp_path, src, calls)[0], "entry")
    monkeypatch.setattr(printer.joins, "rematerialise", lambda *a: False)
    plain = body_of(check(prefix, tmp_path, src, calls)[0], "entry")
    return any(seen), c, plain


@pytest.mark.parametrize(
    "tab,between,kept",
    [
        ("tab", "", False),
        ("tab", "STY tab+7", True),
        ("tab", "LDY $C080", False),
        ("$C080", "", True),
    ],
    ids=["reload", "store", "io-between", "io"],
)
def test_reload_past_a_load_joins_its_block(
    prefix, tmp_path, monkeypatch, tab, between, kept
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """An edge past a load of what it already holds reaches the load's block, which goes: a
    store that may write the table between keeps it, an I/O read between does not, and an
    I/O read is never read again."""
    src = RELOAD.format(tab=tab, between=between)
    done, c, plain = rewrites(prefix, tmp_path, monkeypatch, src, entry_calls(range(0, 256, 3)))
    assert done != kept
    assert (c == plain) == kept
    assert c.count(";") < plain.count(";") or kept


@pytest.mark.parametrize(
    "between,call,kept",
    [
        ("", "", False),
        ("INC cell", "", True),
        ("JSR bump", "JSR bump", True),
        ("", "JSR bump", False),
    ],
    ids=["stored", "store", "call", "after"],
)
def test_value_stored_to_a_cell_is_read_back(
    prefix, tmp_path, monkeypatch, between, call, kept
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """A value just stored to a cell, or loaded from it, is the cell where the paths meet; a
    store of the cell or a call writing it between keeps the value."""
    src = STORED.format(between=between, call=call, target="cell")
    done, c, plain = rewrites(prefix, tmp_path, monkeypatch, src, entry_calls(range(4), 3))
    assert done != kept
    assert (c == plain) == kept


def test_call_writing_another_cell_keeps_the_rewrite(prefix, tmp_path, monkeypatch):
    """A call between that writes only another cell leaves the cell's value."""
    src = STORED.format(between="JSR bump", call="JSR bump", target="other2")
    done, c, plain = rewrites(prefix, tmp_path, monkeypatch, src, entry_calls(range(4), 3))
    assert done and c != plain
