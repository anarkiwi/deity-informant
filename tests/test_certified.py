"""Proven run facts (``docs/certified.md``): play certified and verified from each state init
leaves and each code state, and anything unproven refused."""

import numpy as np
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
    """Play's table read takes exactly the four bytes the wrapping counter reaches and every
    instruction of play is a fact; init, never translated, has none; each certificate
    verifies."""
    p, labels = lifted(WRAP)
    fx = p.proof
    tab = labels["tab"]
    assert fx.accesses[(labels["rd"], False)] == frozenset(range(tab, tab + 4))
    assert not any(pc < labels["play"] for pc, _ in fx.accesses)
    assert fx.proven == {labels["play"]: "certificate"}
    assert fx.ran(labels["keep"]) and not fx.ran(labels["init"])
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


def test_translate_reports_its_facts(prefix):
    """``info["certified"]`` names how each entry is proven and what is assumed."""
    img, labels, loaded = T.assemble(WRAP)
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    _, _, info = cgen.translate(
        img, ents, [T.W.IO], (), loaded=loaded, init=0, songs=[0], prefix=prefix, certified=True
    )
    assert info["certified"]["entries"] == {f"{labels['play']:04x}": "certificate"}
    assert info["certified"]["assumed"] == list(certified.ASSUMED)


#: Play counting in a 16-bit counter only where ``on`` is set, which init clears: the songs'
#: runs never count; a code state setting it counts on every call, never repeating.
GATED = """
init:   LDA #$00
        STA on
        RTS
play:   LDA on
        BEQ done
count:  INC lo
        BNE done
        INC hi
done:   RTS
on:     .byte 0
lo:     .byte 0
hi:     .byte 0
end:
"""


def code_state(src, **at):
    """``src``'s image with the bytes at the labels ``at`` set."""
    img, labels, _ = T.assemble(src)
    out = np.frombuffer(bytes(img), np.uint8).copy()
    for name, v in at.items():
        out[labels[name]] = v
    return out


def test_code_states_add_what_play_is_seen_to_do():
    """A code state is run, not certified: play counting from it, which never repeats, adds
    the instructions and accesses it is seen to make, no certificate, and ``info`` counts
    them."""
    p, labels = lifted(GATED)
    assert not p.proof.ran(labels["count"])
    on = code_state(GATED, on=1)
    q, _ = lifted(GATED, code_states=[(on, 8)])
    assert q.proof.ran(labels["count"]) and len(q.proof.certificates) == len(p.proof.certificates)
    assert q.proof.accesses[(labels["count"], True)] == frozenset({labels["lo"]})
    assert q.proof.proven == {labels["play"]: "certificate"}
    added = q.proof.executed - p.proof.executed
    assert (
        q.observed
        == {
            "states": 1,
            "executed": len(added),
            "edges": len(q.proof.edges - p.proof.edges),
        }
        and labels["count"] in added
    )
    img, labels, loaded = T.assemble(GATED)
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    kw = {"loaded": loaded, "init": 0, "certified": True, "code_states": [on]}
    _, _, info = cgen.translate(img, ents, [T.W.IO], **kw)
    assert info["code_states"]["states"] == 1 and info["code_states"]["executed"] >= 1


@pytest.mark.parametrize("op", ["jmp", "lda"], ids=["spins", "volatile"])
def test_a_code_state_that_fails_is_named(op):
    """A code state whose play does not return (it spins), or loads a volatile address, is
    refused, the error naming it."""
    _, labels, _ = T.assemble(GATED)
    bad = code_state(GATED, on=1)
    at = labels["count"]
    word = at if op == "jmp" else 0xC000
    bad[at : at + 3] = [0x4C if op == "jmp" else 0xAD, word & 0xFF, word >> 8]
    with pytest.raises(certified.Unproven, match="code state 0: .*(return|volatile)"):
        lifted(GATED, code_states=[bad])


#: :data:`test_cgen.STRIDED_CALLED` with a load the lift bounds only by a whole byte of index,
#: over the records; called with ``A`` 0, it reads below them.
STRIDED_NEAR = T.STRIDED_CALLED.replace(
    "entry:  LDX #0", "entry:  TAY\n        LDA $2FF0,Y\n        STA $C010\n        LDX #0"
)


def strided_c(prefix, src, certified_):
    """The ``.c`` of ``src``'s entry, its facts certified or not."""
    img, labels, loaded = T.assemble(src)
    c, _, _ = cgen.translate(
        img,
        [(labels["entry"], "entry")],
        [T.W.IO],
        (),
        loaded=loaded,
        prefix=prefix,
        certified=certified_,
    )
    return c


def test_proof_adds_no_division(prefix):
    """Records the lift promotes as fields reached by a division stay fields with the facts;
    records the lift keeps as memory (a load it bounds by a whole byte reaches them) stay
    memory although the facts show that load below them: the facts add no division."""
    assert "/ 7]" in strided_c(prefix, T.STRIDED_CALLED, True)
    assert "/ 7]" not in strided_c(prefix, STRIDED_NEAR, False)
    assert "/ 7]" not in strided_c(prefix, STRIDED_NEAR, True)
