"""Graph-provenance table-role labelling over eqlift lifts: SID-register roles by
dataflow, and equal-temperament confirmation of the pitch table."""

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("egglog")
pytest.importorskip("z3")

from deity_informant import eqlift_annotate as ann
from deity_informant import eqlift_mem
from deity_informant import structured as S
from deity_informant.c64 import load_psid

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"
_SEMI = 2.0 ** (1.0 / 12.0)


def _tune(stem, parent):
    return [
        pytest.param(path, sub, secs, id="%s-%s" % (parent, stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == stem and path.parent.name == parent
    ]


def _model(sid, subtune, secs):
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, int(secs * 50), subtune)
    return model


def test_role_and_base_helpers():
    assert ann._role_of(0xD400) == "freq_lo" and ann._role_of(0xD401) == "freq_hi"
    assert ann._role_of(0xD418) == "mode_vol" and ann._role_of(0x5428) is None
    addr = ("op", "INT_ADD", (("const", 0x5428, 2), ("op", "INT_ZEXT", (("loc", "note"),), 2)), 2)
    assert ann._const_base(addr) == 0x5428
    assert ann._is_table(0x5428) and not ann._is_table(0xD400) and not ann._is_table(0x40)


def test_et_check_synthetic():
    words = np.array([278 * _SEMI**n for n in range(96)])
    info = ann.et_check(words)
    assert info["pitch_table"] and info["octaves"] == 8 and info["reference"] == 278
    assert not ann.et_check(np.arange(1, 97, dtype=float))["pitch_table"]
    assert not ann.et_check(np.array([1.0, 2.0]))["pitch_table"]


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_pitch_table_by_provenance(sid, subtune, secs):
    """The $5428 note table is reached from freq_lo stores and is equal-tempered."""
    model = _model(sid, subtune, secs)
    text, _ = eqlift_mem.emit(model)
    tr = ann.aggregate(ann.model_procs(model), model)
    assert "freq_lo" in tr.roles[0x5428]
    info = tr.pitch[0x5428]
    assert info["pitch_table"] and info["octaves"] == 8 and info["reference"] == 278
    header = next(ln for ln in text.splitlines() if ln.strip().startswith("table m_5428["))
    assert "role freq_lo" in header and "pitch table: 8 octaves, equal-tempered" in header
    assert eqlift_mem.emit(model)[0] == text


@pytest.mark.parametrize("sid,subtune,secs", _tune("Ghouls_n_Ghosts", "Follin_Tim"))
def test_ghouls_pitch_table_equal_tempered(sid, subtune, secs):
    """Follin's separate lo/hi freq byte tables pair into an equal-tempered scale."""
    text, _ = eqlift_mem.emit(model := _model(sid, subtune, secs))
    tr = ann.aggregate(ann.model_procs(model), model)
    pitched = [info for info in tr.pitch.values() if info["pitch_table"]]
    assert pitched and any(info["octaves"] == 8 for info in pitched)
    assert "pitch table:" in text and "equal-tempered" in text


@pytest.mark.parametrize("sid,subtune,secs", _tune("Krakout", "Daglish_Ben"))
def test_krakout_freq_table_found(sid, subtune, secs):
    """Daglish's freq buffer is reached by provenance (its pitch ROM is behind a
    computed pointer the constant-base backtrace cannot follow)."""
    model = _model(sid, subtune, secs)
    tr = ann.aggregate(ann.model_procs(model), model)
    assert any(set(rs) & ann._FREQ_ROLES for rs in tr.roles.values())
