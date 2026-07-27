"""tracker (M-T1/T3) — universal tracker layer lifted from the song model.

Derives each voice note lane from the frame projection: multi-octave tables
invert by exact match, one-octave tables (computed-base drivers) by
octave-shift + detune. Gate T checks ``render`` bit-exact. See docs/tracker.md."""

from collections import namedtuple

import numpy as np

from . import eqlift_annotate as ann
from . import framelog
from . import song_model as sm
from . import structured as S

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_SEMI = 2 ** (1 / 12)

Pitch = namedtuple("Pitch", "base words octaves reference endian shift")
Clock = namedtuple("Clock", "base kind reload role")
Note = namedtuple("Note", "index word name detune")
Tracker = namedtuple("Tracker", "pitch clocks tempo instruments")
Coverage = namedtuple("Coverage", "notes residual planes")


def _direct_pitch(model):
    """The confirmed multi-octave ET pitch table (provenance path), or None.

    Tries the interleaved (lo+1) layout then Follin-style separate lo/hi
    blocks, accepting only a reading ``et_check`` confirms as equal-tempered."""
    tr = ann.aggregate(list(ann.model_procs(model)), model)
    decls = ann._decls(model)
    his = sorted(b for b, rs in tr.roles.items() if "freq_hi" in rs)
    for lo, info in sorted(tr.pitch.items()):
        if not info["pitch_table"]:
            continue
        for hi in [lo + 1] + his:
            words = ann._pitch_words(model.mem0, lo, hi, decls)
            if words is not None and ann.et_check(words)["pitch_table"]:
                w = np.asarray(words, dtype=np.int64)
                return Pitch(lo, w, info["octaves"], info["reference"], "<", False)
    return None


def _read_bases(model):
    """Every constant base a memory read indexes, across the pass-1 procedures."""
    bases = set()

    def rec(x):
        if isinstance(x, tuple):
            if x and x[0] == "mem":
                bases.add(ann._const_base(x[1]))
            for e in x:
                rec(e)
        elif isinstance(x, list):
            for e in x:
                rec(e)

    for stmts in ann.model_procs(model):
        rec(stmts)
    return bases


def _octave_et(mem0, base, endian, n=12):
    """True if ``n`` 16-bit words at ``base`` form one equal-tempered octave."""
    w = np.frombuffer(bytes(mem0[base : base + 2 * n]), dtype=endian + "u2").astype(float)[:n]
    return len(w) == n and (w > 0).all() and bool(np.all(np.abs(w[1:] / w[:-1] - _SEMI) < 0.006))


def _sparse_et(words, minspan=24):
    """A gapped semitone-indexed ET table (zeros for unused notes), or None.

    Validates that log2(word) is linear in the array index at 1/12 per step over
    the non-zero entries — the index IS the semitone, so interior rests survive."""
    w = np.asarray(words, dtype=np.float64)
    idx = np.flatnonzero(w > 0)
    if len(idx) < minspan or idx[-1] - idx[0] < minspan:
        return None
    err = 12.0 * np.log2(w[idx] / w[idx[0]]) - (idx - idx[0]).astype(np.float64)
    err -= np.round(np.median(err))
    if np.mean(np.abs(err) < 0.3) < 0.9:
        return None
    return words[: idx[-1] + 1]


def _nz_runs(w):
    """Contiguous (start, end) index runs of strictly-positive entries."""
    runs, i, n = [], 0, len(w)
    while i < n:
        if w[i] <= 0:
            i += 1
            continue
        j = i
        while j + 1 < n and w[j + 1] > 0:
            j += 1
        runs.append((i, j))
        i = j + 1
    return runs


def _segmented_et(words, minseg=8, minsegs=3):
    """A per-octave segmented ET table (chromatic runs split by zero markers), or None.

    Each zero-bounded run must be a chromatic semitone run; octaves restart across
    markers so the global index law breaks but every segment is ET."""
    w = np.asarray(words, dtype=np.float64)
    good = total = last = 0
    for a, b in _nz_runs(w):
        if b - a + 1 < minseg:
            continue
        r = w[a + 1 : b + 1] / w[a:b]
        if np.median(np.abs(r - _SEMI)) < 0.01 and np.mean(np.abs(r - _SEMI) < 0.03) > 0.9:
            good, total, last = good + 1, total + (b - a + 1), b
    return words[: last + 1] if good >= minsegs and total >= 36 else None


def _longest_run(words, minrun=24, tol=0.02):
    """The longest maximal chromatic semitone run in the window, or None.

    Scans all start points so a leading near-anchor or trailing garbage in an
    undeclared window does not truncate a real interior ET run."""
    w = np.asarray(words, dtype=np.float64)
    best_a = best_b = a = 0
    for k in range(1, len(w)):
        if w[k] > 0 and w[k - 1] > 0 and abs(w[k] / w[k - 1] - _SEMI) < tol:
            if k - a > best_b - best_a:
                best_a, best_b = a, k
        else:
            a = k
    return words[best_a : best_b + 1] if best_b - best_a + 1 >= minrun else None


def _lattice_et(words, minspan=24, mindist=12, tol=0.15):
    """The leading monotone run whose values lie on the chromatic ET lattice, or None.

    Values ``ref*2**(k/12)`` (freq up, period down, or diatonic subset) make
    ``12*log2(v/ref)`` round to a note index; monotonicity rejects arpeggio streams,
    span/distinct floors and whole-window purity reject short or noisy decoys."""
    w = np.asarray(words, dtype=np.float64)
    pos = np.flatnonzero(w > 0)
    if len(pos) < mindist:
        return None
    vals = w[pos]
    bi = bj = 0
    for sgn in (1.0, -1.0):
        i = 0
        for k in range(1, len(vals)):
            if sgn * (vals[k] - vals[k - 1]) < 0:
                i = k
            if k - i > bj - bi:
                bi, bj = i, k
    if bj - bi + 1 < mindist:
        return None
    seg = vals[: bj + 1]
    q = 12.0 * np.log2(seg / seg.min())
    if np.mean(np.abs(q - np.round(q)) < tol) < 0.9:
        return None
    if int(round(q.max() - q.min())) < minspan or len(set(np.round(q).astype(int))) < mindist:
        return None
    return words[: pos[bj] + 1]


def _et_words(words, minrun=24, bounded=True):
    """The pitch table within a window, or None.

    Prefers the leading semitone-ratio run; a decl-``bounded`` window also accepts
    a whole-window ``et_check``. Falls back to gapped, segmented, longest interior
    run, then the monotone ET-lattice run. Graph-anchored."""
    i = 0
    while i < len(words) and words[i] == 0:
        i += 1
    j = i
    while j + 1 < len(words) and words[j + 1] > 0 and abs(words[j + 1] / words[j] - _SEMI) < 0.02:
        j += 1
    if j - i + 1 >= minrun:
        return words[: j + 1]
    if bounded and len(words) >= 24 and ann.et_check(words.astype(float))["pitch_table"]:
        return words
    for fb in (
        _sparse_et(words),
        _segmented_et(words),
        _longest_run(words, minrun),
        _lattice_et(words),
    ):
        if fb is not None:
            return fb
    return None


def _octave_pitch(model):
    """A one-octave ET table read from the graph (computed-base drivers), or None."""
    for base in sorted(_read_bases(model)):
        for endian in ("<", ">"):
            if _octave_et(model.mem0, base, endian):
                w = np.frombuffer(bytes(model.mem0[base : base + 24]), dtype=endian + "u2")
                return Pitch(base, w.astype(np.int64), 1, int(w[0]), endian, True)
    return None


def _coindexed_bases(model):
    """Groups of table bases read with the same index expression (the graph pairing)."""
    groups = {}

    def index_of(addr):
        if isinstance(addr, tuple) and addr[0] == "op" and addr[1] in ("INT_ADD", "INT_SUB"):
            var = [k for k in addr[2] if not (isinstance(k, tuple) and k[0] == "const")]
            return repr(var[0]) if len(var) == 1 else None
        return None

    def rec(x):
        if isinstance(x, tuple):
            if x and x[0] == "mem":
                base, key = ann._const_base(x[1]), index_of(x[1])
                if ann._is_table(base) and key is not None:
                    groups.setdefault(key, set()).add(base)
            for e in x:
                rec(e)
        elif isinstance(x, list):
            for e in x:
                rec(e)

    for stmts in ann.model_procs(model):
        rec(stmts)
    return [g for g in groups.values() if len(g) >= 2]


def _paired_pitch(model, minsize=36, cap=0x100):
    """Split lo/hi pitch table: two co-indexed tables (graph), sized by their decl.

    Pairing comes from reads sharing an index; the extent is the declared table
    size, or — for an undeclared pair — the ``_et_words`` run within a capped
    window (still graph-anchored); ``et_check`` only confirms, no byte scan."""
    m0 = model.mem0
    sizes = {d["base"]: d["size"] for d in ann._decls(model) if d["kind"] == "table"}
    best_n, best = 0, None
    for group in _coindexed_bases(model):
        bases = sorted(group)
        for i, a in enumerate(bases):
            for b in bases[i + 1 :]:
                decl = a in sizes and b in sizes
                n = max(sizes.get(a, 0), sizes.get(b, 0)) if decl else cap
                if decl and n < minsize:
                    continue
                for lo, hi in ((a, b), (b, a)):
                    k = min(n, len(m0) - lo, len(m0) - hi)
                    lob = np.frombuffer(bytes(m0[lo : lo + k]), dtype="u1").astype(np.int64)
                    hib = np.frombuffer(bytes(m0[hi : hi + k]), dtype="u1").astype(np.int64)
                    et = _et_words(lob | (hib << 8), bounded=decl)
                    if et is None or len(et) <= best_n:
                        continue
                    if not decl and (len(et) < minsize or abs(lo - hi) < len(et) or len(et) > 108):
                        continue
                    best_n, best = len(et), (lo, et)
    if best is None:
        return None
    lo, words = best
    return Pitch(lo, words, best_n // 12, int(words[words > 0][0]), "split", False)


def _freq_bases(model):
    """Read bases carrying a freq_lo/freq_hi provenance role (graph-labelled)."""
    tr = ann.aggregate(list(ann.model_procs(model)), model)
    return {b for b, rs in tr.roles.items() if "freq_lo" in rs or "freq_hi" in rs}


def _split_octave(m0, lo, hi, n=12, tol=0.008):
    """12 words from split lo/hi bytes if they form one ET octave, else None."""
    lob = np.frombuffer(bytes(m0[lo : lo + n]), dtype="u1").astype(np.int64)
    hib = np.frombuffer(bytes(m0[hi : hi + n]), dtype="u1").astype(np.int64)
    w = lob | (hib << 8)
    if len(w) == n and (w > 0).all() and bool(np.all(np.abs(w[1:] / w[:-1] - _SEMI) < tol)):
        return w
    return None


def _octave_split_pitch(model):
    """A one-octave ET table stored as co-indexed split lo/hi byte blocks, or None.

    Pairing is the graph's shared index; the octave-ratio test over 12 entries
    confirms. Inverted by octave-shift like the contiguous one-octave table."""
    m0 = model.mem0
    for group in _coindexed_bases(model):
        bases = sorted(group)
        for a in bases:
            for b in bases:
                if a == b:
                    continue
                w = _split_octave(m0, a, b)
                if w is not None:
                    return Pitch(a, w, 1, int(w[0]), "split", True)
    return None


def _role_split_pitch(model, minrun=30, cap=0x100):
    """Split lo/hi pitch table paired by freq role when reads are not co-indexed.

    A freq_lo-role table and a freq_hi-role table (declared, equal-sized) whose
    byte blocks a gapped ET check (``_sparse_et``) confirms; extent from the run."""
    m0 = model.mem0
    sizes = {d["base"]: d["size"] for d in ann._decls(model) if d["kind"] == "table"}
    tr = ann.aggregate(list(ann.model_procs(model)), model)
    los = sorted(b for b, rs in tr.roles.items() if "freq_lo" in rs)
    his = sorted(b for b, rs in tr.roles.items() if "freq_hi" in rs)
    best_n, best = 0, None
    for lo in los:
        for hi in his:
            if lo == hi or sizes.get(lo) != sizes.get(hi) or sizes.get(lo, 0) < minrun:
                continue
            n = min(sizes[lo], cap, len(m0) - lo, len(m0) - hi)
            lob = np.frombuffer(bytes(m0[lo : lo + n]), dtype="u1").astype(np.int64)
            hib = np.frombuffer(bytes(m0[hi : hi + n]), dtype="u1").astype(np.int64)
            et = _sparse_et(lob | (hib << 8), minspan=minrun)
            if et is not None and len(et) > best_n:
                best_n, best = len(et), (lo, np.asarray(et, dtype=np.int64))
    if best is None:
        return None
    lo, words = best
    return Pitch(lo, words, best_n // 12, int(words[words > 0][0]), "split", False)


def _interleaved_pitch(model, cap=0x100):
    """A multi-octave interleaved 16-bit ET table, or None.

    A declared read table sized by its decl, or an undeclared **freq-role** read
    base within a capped window (extent from ``_et_words``); ``et_check`` confirms."""
    m0 = model.mem0
    sizes = {
        d["base"]: d["size"] for d in ann._decls(model) if d["kind"] == "table" and d.get("size")
    }
    freq = _freq_bases(model)
    bases = (set(sizes) | freq) & _read_bases(model)
    best_len, best = 0, None
    for b in sorted(bases):
        decl = b in sizes and (sizes[b] >= 48 or b not in freq)
        nw = sizes[b] // 2 if decl else cap // 2
        if decl and nw < 24:
            continue
        for endian in ("<", ">"):
            k = min(2 * nw, len(m0) - b)
            w = np.frombuffer(bytes(m0[b : b + k]), dtype=endian + "u2").astype(np.int64)
            et = _et_words(w, bounded=decl)
            need = 12 if decl else 48
            if et is not None and need <= len(et) <= 108 and len(et) > best_len:
                best_len, best = len(et), (b, et, endian)
    if best is None:
        return None
    b, w, endian = best
    return Pitch(b, w, len(w) // 12, int(w[w > 0][0]), endian, False)


def _pitch(model):
    """The pitch table: provenance, one-octave shift, split lo/hi, else interleaved."""
    return (
        _direct_pitch(model)
        or _octave_pitch(model)
        or _paired_pitch(model)
        or _interleaved_pitch(model)
        or _octave_split_pitch(model)
        or _role_split_pitch(model)
    )


def _note_direct(pitch, word):
    """Nearest multi-octave table note + detune, if within half a semitone."""
    idx = int(np.argmin(np.abs(pitch.words - word)))
    cand = int(pitch.words[idx])
    d = word - cand
    if 2 * abs(d) >= cand * (_SEMI - 1):
        return None
    return Note(idx, int(word), "%s%d" % (_NOTE_NAMES[idx % 12], idx // 12), int(d))


def _note_shift(pitch, word):
    """One-octave note `words[semitone] >> octave` + detune, if unambiguous."""
    best, best_ad = None, 0
    for sem, wsem in enumerate(pitch.words):
        b = int(wsem)
        oc = 0
        while b >> oc:
            cand = b >> oc
            d = word - cand
            if best is None or abs(d) < best_ad:
                best, best_ad = (sem, oc, cand, d), abs(d)
            oc += 1
    if best is None:
        return None
    sem, oc, cand, d = best
    if 2 * abs(d) >= cand * (_SEMI - 1):
        return None
    return Note(sem - 12 * oc, int(word), "%s%d" % (_NOTE_NAMES[sem], 8 - oc), int(d))


def _note_of(pitch, word):
    """Recover the note for a freq word under the table's inversion mode."""
    return _note_shift(pitch, word) if pitch.shift else _note_direct(pitch, word)


def _tempo(counters):
    """frames_per_tick: the decrementing counter that reloads from a cell."""
    for c in counters:
        if c.kind == "dec" and c.reload is not None:
            return c.reload
    return None


def lift(model):
    """Lift the tune-independent engine parameters from a committed model."""
    m = sm.analyze(model)
    clocks = [
        Clock(c.base, c.kind, c.reload, "lfo" if c.kind == "inc" else "divider") for c in m.counters
    ]
    insts = sorted({t.source for t in m.control.transitions if t.source})
    return Tracker(_pitch(model), clocks, _tempo(m.counters), insts)


def _frame_notes(pitch, rec):
    """Per-voice Note (or None) for one canonical frame record ``rec``."""
    notes = []
    for v in range(3):
        sec = dict(rec[2 * v])
        b = 7 * v
        word = sec[b] | (sec[b + 1] << 8) if b in sec and b + 1 in sec else None
        notes.append(_note_of(pitch, word) if word is not None else None)
    return notes


def render(model, nframes):
    """Regenerate the canonical projection and score note interpretation.

    Emits the pitch-table lookup for interpreted notes and observed bytes
    elsewhere. Returns (rendered, ground_truth, Coverage, lanes)."""
    frames = framelog.frames_from_walker(S.Walker(model), nframes)
    gt = framelog.canonical(frames)
    pitch = _pitch(model)
    lanes = [[], [], []]
    anchor = [None, None, None]
    interp = total = 0
    rendered = []
    for f, (frame, rec) in enumerate(zip(frames, gt)):
        note_regs = {}
        notes = _frame_notes(pitch, rec) if pitch else (None, None, None)
        for v, note in enumerate(notes):
            b = 7 * v
            if b not in dict(rec[2 * v]):
                continue
            total += 1
            # a detuned frame counts only as vibrato on the current note, or a fresh exact anchor
            if note is not None and (note.detune == 0 or note.index == anchor[v]):
                anchor[v] = note.index
                interp += 1
                lanes[v].append((f, note))
                note_regs[b], note_regs[b + 1] = note.word & 0xFF, (note.word >> 8) & 0xFF
        rendered.append([(reg, note_regs.get(reg, val)) for reg, val in frame])
    return rendered, gt, Coverage(interp, total - interp, total), lanes


def gate_t(model, nframes):
    """Gate T verdict: None (bit-exact) or the first framelog.Divergence."""
    rendered, gt, _cov, _lanes = render(model, nframes)
    return framelog.diff(framelog.canonical(rendered), gt)
