"""Proven run facts (``docs/certified.md``): play certified and verified from each state init
leaves, init proven by its runs, and anything unproven refused."""

from types import SimpleNamespace

import pytest

import test_cgen as T
from test_cgen import fixture_prefix  # noqa: F401  pylint: disable=unused-import
from deity_informant import certified, cgen

pytestmark = T.pytestmark

#: Play reads a counter-indexed table; the counter wraps at 4, so play repeats every 4 calls.
WRAP = """
init:   LDA #$00
        STA cur
        RTS
play:   LDX cur
rd:     LDA tab,X
        STA $C010
        INX
        CPX #$04
        BNE keep
        LDX #$00
keep:   STX cur
        RTS
cur:    .byte 0
tab:    .byte 1,2,3,4
end:
"""
#: Play reads an I/O register: an input the certificate cannot cover.
VOLATILE = """
play:   LDA $C000
        STA $C010
        RTS
end:
"""
#: A 16-bit counter: its state repeats only after 65536 calls.
COUNTER = """
play:   INC lo
        BNE done
        INC hi
done:   RTS
lo:     .byte 0
hi:     .byte 0
end:
"""
#: Play jams the processor.
JAM = """
play:   .byte $02
end:
"""


def lifted(src, entries=("init", "play"), **kw):
    """``(Program, labels)`` of ``src`` lifted with its run facts certified."""
    img, labels, loaded = T.assemble(src)
    ents = [(labels[n], n) for n in entries]
    init = {"init": 0, "songs": [0]} if "init" in entries else {}
    p, _ = cgen.lift(img, ents, [T.W.IO], loaded=loaded, certified=True, **init, **kw)
    return p, labels


def test_play_accesses_are_certified():
    """Play's table read takes exactly the four bytes the wrapping counter reaches; init's
    store and every instruction of both entries are facts; each certificate verifies."""
    p, labels = lifted(WRAP)
    fx = p.proof
    tab = labels["tab"]
    assert fx.accesses[(labels["rd"], False)] == frozenset(range(tab, tab + 4))
    assert fx.accesses[(labels["init"] + 2, True)] == frozenset({labels["cur"]})
    assert fx.proven == {labels["init"]: "runs", labels["play"]: "certificate"}
    assert fx.ran(labels["keep"]) and fx.ran(labels["init"])
    assert (labels["rd"], labels["rd"] + 3) in fx.edges
    assert fx.addresses([labels["rd"], labels["keep"]]) == set(range(tab, tab + 4)) | {
        labels["cur"]
    }
    summary = fx.summary()
    assert [c["ok"] for c in summary["certificates"]] == [True] * len(fx.certificates)
    assert summary["entries"][f"{labels['play']:04x}"] == "certificate"


def test_no_init_certifies_play_from_the_image():
    """Without init, play is certified from the image itself."""
    p, labels = lifted(COUNTER.replace("INC hi", "LDA #0\n        STA lo"), entries=("play",))
    assert p.proof.proven == {labels["play"]: "certificate"}


@pytest.mark.parametrize("src", [VOLATILE, JAM])
def test_unprovable_play_is_refused(src):
    """A play reading an input or not returning has no certificate: the lift refuses it."""
    with pytest.raises(certified.Unproven):
        lifted(src, entries=("play",))


def test_play_with_no_cover_point_is_refused():
    """A play whose state does not repeat within the limit is unproven."""
    img, labels, _ = T.assemble(COUNTER)
    with pytest.raises(certified.Unproven, match="no cover point"):
        certified.play([bytes(img)], labels["play"], [T.W.IO], limit=100)


def test_certified_facts_need_the_trap_rule():
    """The facts are of the machine's runs: the zero rule's reads differ from them."""
    with pytest.raises(ValueError):
        lifted(WRAP, overrun="zero")


def test_init_without_exact_runs_has_no_facts():
    """Init's facts need runs for every declared value, each exact."""
    run = SimpleNamespace(exact=True, touched={(1, False): {2}}, ran={1}, edges=set())
    assert certified.init([None], 1) is None
    assert certified.init([[SimpleNamespace(**{**vars(run), "exact": False})]], 1) is None
    got = certified.init([[run]], 1)
    assert got.accesses == {(1, False): frozenset({2})} and got.proven == {1: "runs"}


def test_translate_reports_its_facts(prefix):
    """``info["certified"]`` names how each entry is proven and what is assumed."""
    img, labels, loaded = T.assemble(WRAP)
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    _, _, info = cgen.translate(
        img, ents, [T.W.IO], (), loaded=loaded, init=0, songs=[0], prefix=prefix, certified=True
    )
    assert info["certified"]["entries"] == {
        f"{labels['init']:04x}": "runs",
        f"{labels['play']:04x}": "certificate",
    }
    assert info["certified"]["assumed"] == list(certified.ASSUMED)
