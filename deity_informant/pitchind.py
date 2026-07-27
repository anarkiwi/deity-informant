"""Output-side pitch recovery: induce an ET lattice from observed SID freqs.

Peels graph-recovered modulation (glide/vibrato) by dwell segmentation, induces
the equal-tempered lattice from the settled base freqs, then inverts every freq
frame to a note index. Complements tracker._pitch (the static-table path)."""

from collections import namedtuple

import numpy as np

from . import framelog
from . import song_model as sm
from . import structured as S
from . import tracker

_SEMI = 2 ** (1 / 12)
_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

Lattice = namedtuple("Lattice", "phi fit bases")
Note = namedtuple("Note", "frame index word name detune")
Lane = namedtuple("Lane", "voice kind notes coverage frames residual distinct span")


def observed(model, nframes):
    """Per-voice ``[(frame, freq_word)]`` from the canonical output projection."""
    frames = framelog.frames_from_walker(S.Walker(model), nframes)
    out = ([], [], [])
    for f, rec in enumerate(framelog.canonical(frames)):
        for v in range(3):
            sec = dict(rec[2 * v])
            b = 7 * v
            if b in sec and b + 1 in sec:
                out[v].append((f, sec[b] | (sec[b + 1] << 8)))
    return out


def peel(words, hold=3, tol=0.25):
    """Plateau centres of a freq series: the settled base freqs, slides dropped.

    A run whose values stay within ``tol`` semitone of a running mean for ``hold``
    frames is a held note or a slide target; its mean survives, transients peel."""
    w = np.asarray([x for x in words if x > 0], dtype=np.float64)
    if len(w) == 0:
        return np.empty(0)
    q = 12.0 * np.log2(w)
    bases, i, n = [], 0, len(q)
    while i < n:
        j = i
        while j + 1 < n and abs(q[j + 1] - np.mean(q[i : j + 1])) < tol:
            j += 1
        if j - i + 1 >= hold:
            bases.append(np.mean(w[i : j + 1]))
        i = j + 1
    return np.asarray(bases, dtype=np.float64)


def _circular_phase(q):
    """Circular mean of the fractional parts of semitone positions ``q`` (mod 1)."""
    ang = 2 * np.pi * (q - np.floor(q))
    return np.angle(np.mean(np.exp(1j * ang))) / (2 * np.pi) % 1.0


def induce(bases, tol=0.15):
    """Induce the ET lattice from base freqs: shared fractional semitone phase.

    On a lattice ``ref*2**(k/12)`` every ``12*log2(base)`` shares one fractional
    part ``phi``; ``fit`` is the share landing within ``tol`` semitone of it."""
    b = np.asarray([x for x in bases if x > 0], dtype=np.float64)
    if len(b) == 0:
        return Lattice(0.0, 0.0, 0)
    q = 12.0 * np.log2(b)
    phi = _circular_phase(q)
    resid = (q - phi + 0.5) % 1.0 - 0.5
    return Lattice(phi, float(np.mean(np.abs(resid) < tol)), len(b))


def _index(word, phi):
    """(semitone index, freq-unit detune) of ``word`` on the ``phi`` lattice."""
    k = int(round(12.0 * np.log2(word) - phi))
    recon = 2.0 ** ((k + phi) / 12.0)
    return k, int(round(word - recon))


def lane(voice, series, lat, tol=0.34):
    """Invert one voice's freq series against lattice ``lat`` into a note lane.

    Each frame within ``tol`` semitone of a lattice node yields a Note; coverage
    is the resolved share, residual the RMS semitone error over resolved frames."""
    notes, errs = [], []
    for f, word in series:
        if word <= 0:
            continue
        q = 12.0 * np.log2(word) - lat.phi
        e = q - round(q)
        if abs(e) < tol:
            k, det = _index(word, lat.phi)
            notes.append(Note(f, k, word, "%s%d" % (_NAMES[k % 12], k // 12), det))
            errs.append(e)
    total = sum(1 for _f, w in series if w > 0)
    rms = float(np.sqrt(np.mean(np.square(errs)))) if errs else 0.0
    cov = len(notes) / total if total else 0.0
    idx = {nt.index for nt in notes}
    span = max(idx) - min(idx) if idx else 0
    return Lane(voice, "freq", tuple(notes), cov, total, rms, len(idx), span)


def _freq_kinds(model):
    """Per-voice-order freq-driver kinds recovered from the graph model."""
    return [d.kind for d in sm.analyze(model).freq]


def recover_lanes(model, nframes, fit_min=0.9, cov_min=0.25, distinct=5, span=7):
    """Output-side note lanes for every voice with an inducible ET lattice.

    Peels each voice's observed freqs, induces a lattice, and inverts; a voice
    below ``fit_min``/``cov_min`` or too few/narrow distinct notes is refused."""
    voices = observed(model, nframes)
    kinds = _freq_kinds(model)
    lanes = []
    for v, series in enumerate(voices):
        bases = peel([w for _f, w in series])
        lat = induce(bases if len(bases) >= 4 else [w for _f, w in series])
        ln = lane(v, series, lat)
        recovered = (
            lat.fit >= fit_min
            and ln.coverage >= cov_min
            and ln.distinct >= distinct
            and ln.span >= span
        )
        lanes.append((ln, lat, recovered))
    return lanes, kinds


def agreement(model, nframes):
    """Induced-vs-static note agreement for a tune tracker._pitch resolves.

    Inverts each observed freq both ways and returns the share of frames whose
    induced index minus static index equals the modal (constant-octave) offset."""
    pitch = tracker._pitch(model)
    if pitch is None:
        return None
    voices = observed(model, nframes)
    diffs = []
    for series in voices:
        bases = peel([w for _f, w in series])
        lat = induce(bases if len(bases) >= 4 else [w for _f, w in series])
        for _f, word in series:
            note = tracker._note_of(pitch, word)
            if note is None:
                continue
            k, _d = _index(word, lat.phi)
            diffs.append(k - note.index)
    if not diffs:
        return None
    _vals, counts = np.unique(diffs, return_counts=True)
    return float(counts.max() / counts.sum())
