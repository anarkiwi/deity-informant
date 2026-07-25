"""EXPERIMENTAL (musical-structure study): frequency -> note inversion.

Log-driven: nearest equal-tempered semitone + cent detune against the PAL
clock (freq = f_hz * 2**24 / 985248). Program-driven: exact lookup in the
tune's own freq table located in the post-init image (pysidtracker locator).
"""

import math

from pysidtracker.notefreq import PAL_FREQ, locate_note_freq
from pysidtracker.image import SidImage

NAMES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")


def nearest_et(freq):
    """(note_index, cents) nearest the PAL equal-tempered table; freq > 0."""
    n = min(range(len(PAL_FREQ)), key=lambda i: abs(math.log2(freq / PAL_FREQ[i])))
    cents = 1200.0 * math.log2(freq / PAL_FREQ[n])
    return n, cents


def note_name(n):
    """Tracker spelling; index 0 = C-0 per the pysidtracker table convention."""
    return "%s%d" % (NAMES[n % 12], n // 12)


def _ramp16(vals):
    """Octave-ramp score: entries whose +12 neighbour is ~2x (16-bit)."""
    return sum(1 for i in range(len(vals) - 12) if vals[i] and abs(vals[i + 12] - 2 * vals[i]) <= 2)


def locate_freq_table(mem0, lengths=(96, 95, 89, 64)):
    """Best (kind, addr, values) freq table in a post-init snapshot, or None.

    Generalises pysidtracker's locator: candidate bases are abs,X / abs,Y
    indexed-load operands; layouts tried are split hi/lo, split lo/hi and
    lo/hi-interleaved word tables; best 16-bit octave-ramp score wins.
    """
    targets = set()
    for pc in range(0x0800, 0xFFFD):
        if mem0[pc] in (0xBD, 0xB9):
            targets.add(mem0[pc + 1] | (mem0[pc + 2] << 8))
    best = None
    for a in sorted(targets):
        for L in lengths:
            for lo_a, hi_a, kind in ((a + L, a, "split-hi-lo"), (a, a + L, "split-lo-hi")):
                if a + L in targets and max(lo_a, hi_a) + L <= 0x10000:
                    vals = [(mem0[hi_a + i] << 8) | mem0[lo_a + i] for i in range(L)]
                    r = _ramp16(vals)
                    if best is None or r > best[0]:
                        best = (r, kind, a, vals)
            if a + 2 * L <= 0x10000:
                vals = [(mem0[a + 1 + 2 * i] << 8) | mem0[a + 2 * i] for i in range(L)]
                r = _ramp16(vals)
                if best is None or r > best[0]:
                    best = (r, "interleaved", a, vals)
    if best is None or best[0] < 30:
        return None
    return best[1], best[2], best[3]


def tune_table(sid_path):
    """The tune's own note-freq table located in its image, or None."""
    img = SidImage.from_sid(open(sid_path, "rb").read())
    return locate_note_freq(img)


def invert(freq, table=None):
    """(note, cents, exact) -- exact iff freq matches a table entry verbatim."""
    if table is not None:
        for i in range(len(table)):
            if table.freq(i) == freq:
                n, cents = nearest_et(freq)
                return i, cents, True
    n, cents = nearest_et(freq)
    return n, cents, False
