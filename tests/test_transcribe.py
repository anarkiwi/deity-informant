"""Tests for the readable transcription layer (``deity_informant.transcribe``).

Over the synthetic corpus: register naming, indexed-table (``T[...]``) rendering,
never emitting table byte values, and shared-subexpression ``let`` factoring.
"""

import sys

import deity_informant.expr as E
import deity_informant.transcribe  # noqa: F401  (submodule; shadowed by the re-exported fn)
from deity_informant import lift_kernel, record, run_sub, sid_name, transcribe

import _fuzzgen as G

_T = sys.modules["deity_informant.transcribe"]
_PLAYERS = G.players(4)


class _Kern:
    def __init__(self, state=(), tables=()):
        self.state = set(state)
        self.tables = set(tables)


def _kernel(name):
    p = next(pl for pl in _PLAYERS if pl.name == name)
    m = bytearray(0x10000)
    for a, v in p.image_data().items():
        m[a] = v
    return lift_kernel(record(bytes(m), run_sub, p.org, p.outputs, p.frames))


# ---- register naming ---------------------------------------------------------
def test_sid_name():
    assert sid_name(0xD400) == "V1.FREQ_LO"
    assert sid_name(0xD404) == "V1.CTRL"
    assert sid_name(0xD407) == "V2.FREQ_LO"
    assert sid_name(0xD40E) == "V3.FREQ_LO"
    assert sid_name(0xD415) == "FILT_CUT_LO"
    assert sid_name(0xD418) == "MODE_VOL"
    assert sid_name(0x1234) == "$1234"


# ---- readable rendering over the corpus --------------------------------------
def test_smc_operand_readable():
    text = transcribe(_kernel("smc_operand"), "smc_operand")
    assert "V2.CTRL      $D40B <- $4B" in text
    assert "S[$1003] <- (S[$1003] + $1)" in text


def test_dec_timer_two_paths_indexed():
    text = transcribe(_kernel("dec_timer"), "dec_timer")
    assert "paths=2" in text
    assert "T[(" in text  # indexed constant-table read
    assert "ZEXT" not in text  # width extension rendered transparently


def test_no_table_bytes_emitted():
    text = transcribe(_kernel("table_index"), "table_index")
    rhs = [ln.split("<-", 1)[1].strip() for ln in text.splitlines() if "<-" in ln and "$D4" in ln]
    assert rhs and all(r.startswith("T[") for r in rhs)  # reads, never folded byte values


def test_every_player_transcribes():
    for p in _PLAYERS:
        m = bytearray(0x10000)
        for a, v in p.image_data().items():
            m[a] = v
        text = transcribe(
            lift_kernel(record(bytes(m), run_sub, p.org, p.outputs, p.frames)), p.name
        )
        assert "SID WRITES" in text and "STATE UPDATE" in text


def test_determinism():
    assert transcribe(_kernel("dec_timer"), "x") == transcribe(_kernel("dec_timer"), "x")


# ---- printer unit tests (crafted exprs, no tune data) ------------------------
def test_printer_table_vs_state():
    pr = _T._Printer(_Kern(state={0x40}, tables={0x2000}))
    assert pr.render(E.mem(E.konst(0x40, 2))) == "S[$0040]"
    assert pr.render(E.mem(E.konst(0x2000, 2))) == "T[$2000]"


def test_printer_zext_transparent_and_shifts():
    pr = _T._Printer(_Kern(tables={0x10}))
    n = E.op("INT_LEFT", [E.op("INT_ZEXT", [E.mem(E.konst(0x10, 2))], 2), E.konst(1, 1)], 2)
    assert pr.render(n) == "(T[$0010] << $1)"


def test_printer_word():
    pr = _T._Printer(_Kern(tables={0x10, 0x11}))
    lo = E.op("INT_ZEXT", [E.mem(E.konst(0x10, 2))], 2)
    hi = E.op("INT_LEFT", [E.op("INT_ZEXT", [E.mem(E.konst(0x11, 2))], 2), E.konst(8, 1)], 2)
    assert pr.render(E.op("INT_OR", [lo, hi], 2)) == "(T[$0011]:T[$0010])"


def test_printer_cse_factors_shared_subexpr():
    pr = _T._Printer(_Kern(tables={0x2000}))
    d = E.op(
        "INT_ADD",
        [E.op("INT_AND", [E.mem(E.konst(0x2000, 2)), E.konst(0x1F, 1)], 1), E.konst(1, 1)],
        1,
    )
    pr.name_common([d, d, d])
    assert d in pr.names
    assert pr.render(d) == pr.names[d]  # reference at use site
    assert pr.render(d, top=True) != pr.render(d)  # definition expands one level
    assert any("&" in pr.render(n, top=True) for n in pr.names)  # AND factored into a binding
