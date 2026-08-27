"""S6/S8: per-tick cell histories off the verifier, sparse strides, 16-bit widening."""

import json

import numpy as np
import pytest

from deity_informant.tuneprog import pipeline
from deity_informant.tuneprog.history import cells, history, widen_u16
from deity_informant.tuneprog.ir import Load, Tuneprog
from deity_informant.tuneprog.tracedata import Trace
from deity_informant.tuneprog.verify import certify

from _asm import asm
from _prog import PLAY, counter, tuneprog
from _hvsc import LINUS, tune_file

PERIODIC = ("INC cnt", "LDA cnt", "AND #$03", "STA cnt", "STA $D400")
PTR = (
    "init: LDA #<tab",
    "STA $FB",
    "LDA #>tab",
    "STA $FC",
    "RTS",
    "play: LDY #$00",
    "LDA ($FB),Y",
    "STA $D400",
    "CLC",
    "LDA $FB",
    "ADC #$01",
    "STA $FB",
    "LDA $FC",
    "ADC #$00",
    "STA $FC",
    "RTS",
    "tab: BRK",
)


def _run(code, calls=8, **kw):
    """``(history, verifier, S6 document, program)`` of a snippet."""
    T, prog = tuneprog(code, calls=calls, s4=True)
    doc = pipeline.present(prog)[2].to_dict()
    h, v = history(prog, T, doc, calls=calls, **kw)
    return h, v, doc, prog


def test_a_counter_cell_takes_one_value_per_verified_tick():
    h, v, _doc, _prog = _run(counter("INC cnt", "LDA cnt", "STA $D400"))
    assert v.div is None
    assert [a.tolist() for a in h.values()] == [[1, 2, 3, 4, 5, 6, 7, 8]]


def test_a_periodic_snippet_repeats_at_the_period_the_certificate_claims():
    h, v, _doc, prog = _run(counter(*PERIODIC))
    p = certify(prog, v)["subtunes"][0]["period"]
    assert p == 4
    for a in h.values():
        assert np.array_equal(a[p:], a[:-p])


def test_a_pointer_pair_widens_to_the_address_it_walks():
    h, v, doc, _prog = _run(asm(PLAY, *PTR, *["BRK"] * 7), calls=6)
    assert v.div is None
    words = widen_u16(h, doc)
    assert len(words) == 1
    (base,) = {int(a[0]) for a in words.values()}
    assert list(next(iter(words.values()))) == list(range(base, base + 6))
    assert h.cell(-99, 0) is None  # a pair outside the sampled kinds does not widen


def test_a_sparse_stride_is_sampled_at_the_addresses_the_region_holds():
    """A strided region's bytes are its ``addrs``, not the extent they span."""
    _h, _v, doc, prog = _run(counter("INC cnt", "LDA cnt", "STA $D400"), calls=4)
    rid, name = doc["regions"][0]["id"], doc["regions"][0]["name"]
    rgn = prog.by_id()[rid]
    rgn.size, rgn.stride = 3, 2  # two cells, three bytes of extent
    assert cells(prog, doc) == [(rid, name, rgn.base + i) for i in range(3)]
    sparse = [{"id": rid, "addrs": [rgn.base, rgn.base + 2]}]
    assert cells(prog, doc, regions_doc=sparse) == [
        (rid, name, rgn.base),
        (rid, name, rgn.base + 2),
    ]
    assert not cells(prog, doc, kinds=("const",))


def test_a_divergence_truncates_the_history_at_the_tick_before_it():
    code = asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDY cnt",
        "LDA tab,Y",
        "STA $D400",
        "INC cnt",
        "RTS",
        "cnt: BRK",
        "tab: BRK",
        *["BRK"] * 7,
    )
    T, prog = tuneprog(code, calls=8, s4=True)
    doc = pipeline.present(prog)[2].to_dict()
    for b in prog.procs["tick"].blocks.values():
        for s in b.stmts:
            e = getattr(s, "e", None)
            if type(e) is Load and e.r >= 0 and e.hi - e.lo == 7:
                s.e = Load(e.cls, e.a, e.w, e.lo, e.lo + 3, e.r)  # shrink the table's envelope
    h, v = history(prog, T, doc, calls=8)
    assert v.div["trap"] == "envelope" and v.call == 4
    assert [a.tolist() for a in h.values()] == [[1, 2, 3, 4]]


@pytest.mark.hvsc
def test_the_named_state_of_a_goattracker_tune_replays_without_divergence(tmp_path):
    out = tmp_path / "linus"
    assert pipeline.main([str(tune_file(LINUS)), "--out", str(out), "--calls", "64"]) == 0
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    doc = json.loads((out / "tuneprog.S6.json").read_text())
    regions = json.loads((out / "regions.json").read_text())
    h, v = history(prog, Trace.load(out), doc, calls=64, regions_doc=regions)
    assert v.div is None and v.call == 64
    assert h and all(a.shape[0] == 64 for a in h.values())
    assert {a.ndim for a in h.values()} == {1, 2}
    assert len(h.cells) == sum(a.shape[1] if a.ndim > 1 else 1 for a in h.values())
    assert widen_u16(h, doc)
