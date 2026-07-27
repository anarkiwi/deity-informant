"""Output-side pitch recovery: peel modulation, induce an ET lattice, invert."""

from pathlib import Path

import numpy as np
import pytest

from deity_informant import pitchind as P
from deity_informant import structured as S
from deity_informant.c64 import load_psid

from _corpus import corpus_params

_SEMI = 2 ** (1 / 12)
HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def _tune(stem, parent):
    return [
        pytest.param(path, sub, id="%s-%s" % (parent, stem))
        for path, sub, _secs in corpus_params(HVSC)
        if path.stem == stem and path.parent.name == parent
    ]


def _model(sid, subtune, frames=200):
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, frames, subtune)
    return model


def _modulated(notes, ref=1793.0, glide=4, hold=8, vib=0.15):
    """A freq series that glides between notes then holds with small vibrato."""
    series, f = [], 0
    for n in notes:
        target = ref * _SEMI**n
        prev = series[-1][1] if series else target
        for t in np.linspace(prev, target, glide):
            series.append((f, int(round(t))))
            f += 1
        for k in range(hold):
            series.append((f, int(round(target * _SEMI ** (vib * np.sin(k * 0.9))))))
            f += 1
    return series


def test_empty_inputs_and_zero_words():
    """Degenerate inputs return empty bases, a null lattice, and skip zero words."""
    assert len(P.peel([])) == 0 and len(P.peel([0, 0])) == 0
    lat = P.induce([])
    assert lat == P.Lattice(0.0, 0.0, 0)
    good = P.induce([1793.0 * _SEMI**k for k in range(6)])
    ln = P.lane(0, [(0, 0), (1, int(1793.0 * _SEMI**7))], good)  # a zero freq is skipped
    assert ln.frames == 1 and len(ln.notes) == 1


def test_peel_drops_slides_keeps_plateaus():
    """A held note survives peeling; a pure monotone ramp yields no base."""
    ref = 1793.0
    held = [(i, int(ref * _SEMI**7)) for i in range(10)]
    assert len(P.peel([w for _f, w in held])) == 1
    ramp = [(i, int(1000 + 60 * i)) for i in range(20)]
    assert len(P.peel([w for _f, w in ramp])) == 0


def test_induce_recovers_lattice_under_modulation():
    """A glide+vibrato melody peels to a pure lattice and inverts to notes."""
    notes = [0, 4, 7, 12, 7, 4, 0, 5, 9, -3, -5]
    series = _modulated(notes)
    lat = P.induce(P.peel([w for _f, w in series]))
    assert lat.fit == 1.0
    ln = P.lane(0, series, lat)
    assert ln.coverage > 0.9 and ln.residual < 0.2
    idx = sorted({nt.index for nt in ln.notes})
    assert idx[-1] - idx[0] == max(notes) - min(notes)


def test_induce_is_slope_invariant():
    """Descending period tables share the lattice phase with ascending freqs."""
    ref = 1793.0
    freqs = [ref * _SEMI**k for k in (0, 3, 5, 7, 10, 12)]
    periods = [1e7 / v for v in freqs]
    assert P.induce(freqs).fit == 1.0 and P.induce(periods).fit == 1.0


def test_atonal_signal_is_refused():
    """A random atonal series does not fit the lattice at the recovery threshold."""
    rng = np.random.default_rng(0)
    noise = list(enumerate(int(v) for v in rng.integers(500, 8000, 400)))
    bases = P.peel([w for _f, w in noise])
    lat = P.induce(bases if len(bases) >= 4 else [w for _f, w in noise])
    assert lat.fit < 0.9


def test_index_inversion_roundtrips():
    """Lattice inversion reconstructs freqs at the right relative semitone interval."""
    lat = P.induce([1793.0 * _SEMI**k for k in range(12)])
    lo, det = P._index(int(round(1793.0 * _SEMI**7)), lat.phi)
    hi, _ = P._index(int(round(1793.0 * _SEMI**19)), lat.phi)
    assert hi - lo == 12 and abs(det) < 1793.0 * (_SEMI - 1) / 2


@pytest.mark.parametrize("sid,subtune", _tune("Commando", "Hubbard_Rob"))
def test_output_side_agrees_with_static_table(sid, subtune):
    """On a static-table tune the induced lattice agrees and a note lane recovers."""
    model = _model(sid, subtune)
    assert P.agreement(model, 200) > 0.95
    lanes, kinds = P.recover_lanes(model, 200)
    assert any(rec for _ln, _lat, rec in lanes)
    assert isinstance(kinds, list)
