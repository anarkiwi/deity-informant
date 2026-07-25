"""EXPERIMENTAL (musical-structure study): musical event/row/pattern recovery.

Log-driven pipeline over the canonical frame log: per-voice note/gate events,
speed (frames per row) inference, row quantisation, instrument fingerprints,
and repetition measurement (repeated-phrase cover + fixed-length blocking).
"""

from collections import Counter

GATE = 0x01
NAMES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")


def note_name(n):
    return "%s%d" % (NAMES[n % 12], n // 12)


def voice_events(grid, canon_frames, voice):
    """Per-frame voice view -> [(frame, kind, data)] events.

    kinds: on (gate 0->1: freq16, wave, ad, sr), off (gate 1->0), note
    (freq16 change while gated), wave (ctrl change w/o gate edge).
    """
    b = voice * 7
    ev = []
    prev_ctrl = 0
    prev_freq = None
    for f, state in enumerate(grid):
        ctrl = state[b + 4]
        freq = state[b] | (state[b + 1] << 8)
        wrote_ctrl = any(r == b + 4 for r, _ in canon_frames[f])
        if wrote_ctrl and (ctrl & GATE) and not (prev_ctrl & GATE):
            ev.append((f, "on", (freq, ctrl >> 4, state[b + 5], state[b + 6])))
        elif wrote_ctrl and not (ctrl & GATE) and (prev_ctrl & GATE):
            ev.append((f, "off", ctrl >> 4))
        elif (ctrl & GATE) and prev_freq is not None and freq != prev_freq:
            ev.append((f, "note", freq))
        elif wrote_ctrl and ctrl != prev_ctrl:
            ev.append((f, "wave", ctrl >> 4))
        prev_ctrl, prev_freq = ctrl, freq
    return ev


def infer_speed(all_onsets, cands=range(2, 17)):
    """Frames-per-row: the largest divisor aligning >=90% of onset deltas."""
    deltas = [b - a for a, b in zip(all_onsets, all_onsets[1:]) if b > a]
    if not deltas:
        return 1, 0.0
    best = (1, 0.0)
    for s in cands:
        frac = sum(1 for d in deltas if d % s == 0) / len(deltas)
        if frac >= best[1] + 0.005 or (frac > best[1] - 0.005 and s > best[0]):
            if frac >= best[1] - 0.005:
                best = (s, max(frac, best[1]))
    return best


def row_stream(events, speed, nrows, table_freqs):
    """Quantise one voice's events to rows: list of (note|None, instr|None).

    note: exact table index when freq matches, else nearest-ET-style marker
    ('~%d' slide steps are ignored here -- only note-on rows carry notes).
    instr: (wave, ad, sr) fingerprint at note-on; 'off' rows carry gate-off.
    """
    rows = [None] * nrows
    for f, kind, data in events:
        r = f // speed
        if r >= nrows:
            break
        if kind == "on":
            freq, wave, ad, sr = data
            note = table_freqs.get(freq)
            rows[r] = ("on", note if note is not None else -freq, (wave, ad, sr))
        elif kind == "off" and rows[r] is None:
            rows[r] = ("off",)
    return rows


def tokens(rows):
    """Row stream -> hashable token tuple ('.' = empty row)."""
    out = []
    for r in rows:
        if r is None:
            out.append(".")
        elif r[0] == "off":
            out.append("off")
        else:
            out.append((r[1], r[2]))
    return tuple(out)


def phrase_cover(seq, min_len=8):
    """Greedy left-to-right cover by earlier-occurring phrases.

    Returns (covered_fraction, phrases) where phrases maps (start,len) of each
    reused source span to its use count -- an upper-bound-ish measure of how
    much of the stream is literal repetition of earlier material.
    """
    n = len(seq)
    covered = 0
    uses = Counter()
    i = 0
    occ = {}
    for j in range(n):  # index of first occurrence of each min_len-gram
        g = seq[j : j + min_len]
        occ.setdefault(g, []).append(j)
    while i < n:
        g = seq[i : i + min_len]
        best = 0
        src = None
        for j in occ.get(g, ()):
            if j >= i:
                break
            L = 0
            while i + L < n and seq[j + L] == seq[i + L] and j + L < i:
                L += 1
            if L > best:
                best, src = L, j
        if best >= min_len:
            covered += best
            uses[(src, best)] += 1
            i += best
        else:
            i += 1
    return covered / max(1, n), uses


def block_stats(seq, L):
    """Fixed-L blocking: (total blocks, distinct blocks, order list)."""
    blocks = [seq[i : i + L] for i in range(0, len(seq) - L + 1, L)]
    index = {}
    order = []
    for blk in blocks:
        if blk not in index:
            index[blk] = len(index)
        order.append(index[blk])
    return len(blocks), len(index), order, index


def best_block_len(seq, cands=(16, 24, 32, 48, 64, 96, 128)):
    """Block length maximising total/distinct compression."""
    best = (1, 1.0, [], {})
    for L in cands:
        if L * 2 > len(seq):
            continue
        tot, dis, order, index = block_stats(seq, L)
        ratio = tot / dis
        if ratio > best[1]:
            best = (L, ratio, order, index)
    return best
