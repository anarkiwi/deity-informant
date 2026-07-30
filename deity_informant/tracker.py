"""tracker — the universal tracker layer over a frame program.

One primitive: a triggered generator ``(transfer, trigger, route)``; a tune is a
graph of them. One law: the graph's canonical projection equals frameprog's under
the same input trace. One input: a ``frameprog.FrameProgram``. See docs/tracker.md."""

from collections import namedtuple

import numpy as np

from . import framelog
from . import frameproc
from . import frameval

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_SEMI = 2 ** (1 / 12)
_SID = 0xD400
_FREQ_REGS = (0, 1, 7, 8, 14, 15)

FRAME = ("frame",)

Generator = namedtuple("Generator", "transfer trigger route")
Coverage = namedtuple("Coverage", "interp residual total planes")
Pitch = namedtuple("Pitch", "base words octaves reference endian shift hi", defaults=(None,))
Clock = namedtuple("Clock", "base kind reload role")
Note = namedtuple("Note", "index word name detune")
Tracker = namedtuple("Tracker", "pitch clocks tempo instruments")

_PLANE = {0: "freq", 1: "freq", 2: "pw", 3: "pw", 4: "ctrl", 5: "ad", 6: "sr"}


class TrackerError(ValueError):
    """The graph is not evaluable (unknown transfer, dangling trigger)."""


# ---- 1. the one primitive: a triggered generator ---------------------------------
def div(n, trigger=FRAME):
    """Emit one tick per ``n`` input triggers: a clock. Route is always Fire."""
    return Generator(("DIV", n), trigger, ("fire",))


def lookup(seq, trigger, reg):
    """Emit ``seq[i]`` into a register plane; ``i`` advances per trigger."""
    return Generator(("LOOKUP", tuple(seq)), trigger, ("plane", reg))


def ramp(seed, step, bound, trigger, reg):
    """Emit ``seed + step*count`` into a plane, wrapped at ``bound``."""
    return Generator(("RAMP", seed, step, bound), trigger, ("plane", reg))


def select(table, rows, trigger, reg):
    """Emit ``table[rows[i]]`` into a plane: a declared table read at a recovered row."""
    return Generator(("SELECT", tuple(table), tuple(rows)), trigger, ("plane", reg))


def edge(counts):
    """The trigger floor: fire ``counts[f]`` downstream edges on frame ``f``."""
    return Generator(("EDGE", tuple(counts)), FRAME, ("fire",))


def raw(per_frame):
    """The completeness floor: replay ``per_frame[f]`` writes verbatim, in order."""
    return Generator(("RAW", tuple(tuple(w) for w in per_frame)), FRAME, ("raw",))


class Graph:
    """Generator nodes plus the two distinguished ones every graph carries."""

    def __init__(self, nodes, freq_table=None, cadence=None):
        self.nodes = list(nodes)
        self.freq_table = freq_table
        self.cadence = cadence

    def raw_index(self):
        """Index of the RAW floor node, or None."""
        for i, g in enumerate(self.nodes):
            if g.transfer[0] == "RAW":
                return i
        return None


def _ticks(g, frame):
    """Edges a fired Fire-routed generator emits at ``frame``."""
    kind = g.transfer[0]
    if kind == "DIV":
        n = max(1, g.transfer[1])
        return 1 if (frame + 1) % n == 0 else 0
    if kind == "EDGE":
        seq = g.transfer[1]
        return seq[frame] if frame < len(seq) else 0
    raise TrackerError("transfer %r has no edge emit" % (kind,))


def _fired(nodes, frame):
    """Trigger counts per node for ``frame``: root clocks and their Fire edges."""
    fires = [1 if g.trigger == FRAME else 0 for g in nodes]
    for i, g in enumerate(nodes):
        if g.route[0] != "fire" or not fires[i]:
            continue
        n = _ticks(g, frame)
        for j, h in enumerate(nodes) if n else ():
            if h.trigger == ("event", i):
                fires[j] += n
    return fires


def _emit(g, count):
    """Value a plane-routed generator emits on its ``count``-th trigger."""
    kind = g.transfer[0]
    if kind == "LOOKUP":
        seq = g.transfer[1]
        return seq[(count - 1) % len(seq)] if seq else None
    if kind == "SELECT":
        _k, table, rows = g.transfer
        return table[rows[(count - 1) % len(rows)]] if rows else None
    if kind == "RAMP":
        _k, seed, step, bound = g.transfer
        raw_v = seed + step * (count - 1)
        return raw_v % bound if bound else raw_v
    raise TrackerError("transfer %r has no value emit" % (kind,))


def _check(nodes):
    for g in nodes:
        if g.trigger != FRAME and g.trigger[0] != "event":
            raise TrackerError("unknown trigger %r" % (g.trigger,))
        if g.trigger[0] == "event" and not 0 <= g.trigger[1] < len(nodes):
            raise TrackerError("dangling trigger %r" % (g.trigger,))
        if g.route[0] not in ("plane", "fire", "raw"):
            raise TrackerError("unknown route %r" % (g.route,))


def _run(graph, nframes):
    """``(canonical records, interpreted emits, raw emits)`` per register.

    Refinement removes a write from RAW, so RAW and a plane-routed node never
    contend for one register and the interleaving stays well defined."""
    nodes = graph.nodes
    _check(nodes)
    counts = [0] * len(nodes)
    interp, rawn = {}, {}
    out = []
    for f in range(nframes):
        fires = _fired(nodes, f)
        writes = []
        for i, g in enumerate(nodes):
            if not fires[i]:
                continue
            if g.transfer[0] == "RAW":
                counts[i] += fires[i]
                rows = g.transfer[1]
                for reg, val in rows[f] if f < len(rows) else ():
                    rawn[reg] = rawn.get(reg, 0) + 1
                    writes.append((reg, val))
            elif g.route[0] == "plane":
                reg = g.route[1]
                for _t in range(fires[i]):  # one emit per trigger, in order
                    counts[i] += 1
                    v = _emit(g, counts[i])
                    if v is not None:
                        interp[reg] = interp.get(reg, 0) + 1
                        writes.append((reg, v & 0xFF))
            else:
                counts[i] += fires[i]
        out.append(writes)
    return framelog.canonical(out), interp, rawn


def eval_graph(graph, nframes):
    """Canonical per-frame records produced by propagating triggers."""
    return _run(graph, nframes)[0]


def _plane_of(reg):
    """Canonical plane class for a SID register offset."""
    if reg <= 0x14:
        return _PLANE[reg % 7]
    return "filter" if reg <= 0x18 else "tail"


def _coverage(interp, rawn):
    """The interpreted/residual partition of the emits, split by plane."""
    planes = {}
    for src, gen in ((interp, True), (rawn, False)):
        for reg, n in src.items():
            p = _plane_of(reg)
            it, tot = planes.get(p, (0, 0))
            planes[p] = (it + (n if gen else 0), tot + n)
    ni, nr = sum(interp.values()), sum(rawn.values())
    return Coverage(ni, nr, ni + nr, planes)


def coverage(graph, nframes):
    """Interpreted vs residual emit counts, and the per-plane split."""
    _recs, interp, rawn = _run(graph, nframes)
    return _coverage(interp, rawn)


def from_frames(frames):
    """The completeness floor: one RAW node replaying every write, in order."""
    return Graph([raw([list(fr) for fr in frames])])


# ---- 2. pitch: equal-tempered tables read from the declarations -------------------
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

    Scans all start points so a leading near-anchor or a trailing garbage tail in a
    declared window does not truncate a real interior ET run."""
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

    Values ``ref*2**(k/12)`` (freq up, period down, or a diatonic subset) make
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


def _median_et(words, minrun=24):
    """The whole window when its median semitone and octave ratios are ET, else None.

    The declared extent is the table: a run of 24+ words whose median step is a
    semitone and median 12-step an octave is equal-tempered however gapped."""
    w = np.asarray(words, dtype=np.float64)
    if len(w) < minrun:
        return None
    nz, oz = w[:-1] > 0, w[:-12] > 0
    if not nz.any() or not oz.any():
        return None
    step = np.median(w[1:][nz] / w[:-1][nz])
    octr = np.median(w[12:][oz] / w[:-12][oz])
    return words if abs(step - _SEMI) <= 0.01 and abs(octr - 2.0) <= 0.05 else None


def _leading_run(words, minrun=24):
    """The leading chromatic semitone run of a window, or None."""
    i = 0
    while i < len(words) and words[i] == 0:
        i += 1
    j = i
    while j + 1 < len(words) and words[j + 1] > 0 and abs(words[j + 1] / words[j] - _SEMI) < 0.02:
        j += 1
    return words[: j + 1] if j - i + 1 >= minrun else None


_VALIDATORS = (_median_et, _leading_run, _sparse_et, _segmented_et, _longest_run, _lattice_et)


def _et_words(words):
    """``[(tier, table)]``: every ET reading of a window, strongest evidence first.

    The tier is the validator's rank — a leading chromatic run is the strongest
    evidence, the monotone ET lattice the weakest; distinct extents are all kept."""
    out, seen = [], set()
    if len(words) < 12:
        return out
    for tier, check in enumerate(_VALIDATORS):
        et = check(words)
        if et is not None and len(et) not in seen:
            seen.add(len(et))
            out.append((len(_VALIDATORS) - tier, np.asarray(et, dtype=np.int64)))
    return out


def _octave_words(words, n=12, tol=0.008):
    """The first ``n`` words if they form exactly one equal-tempered octave."""
    w = np.asarray(words[:n], dtype=np.int64)
    if len(w) < n or not (w > 0).all():
        return None
    r = w[1:].astype(float) / w[:-1]
    return w if bool(np.all(np.abs(r - _SEMI) < tol)) else None


def _avail(prog):
    """``base -> declared const bytes from base``, per declared table and cobase.

    Adjacent declarations are one contiguous const run: the boundary between them
    marks another read base, not another data class, so a table may span it."""
    tabs = sorted(
        (d["base"], d["size"], list(d.get("cobases", ())))
        for d in prog.data_decls
        if d["kind"] == "table"
    )
    out, end = {}, {}
    above = (None, None)
    for base, size, _co in reversed(tabs):
        stop = above[1] if base + size == above[0] else base + size
        end[base] = stop
        above = (base, stop)
    for base, _size, cobases in tabs:
        for b in [base] + cobases:
            out[b] = max(out.get(b, 0), end[base] - b)
    return out


def _words_at(mem0, base, endian, nbytes):
    """The 16-bit words of an interleaved table at ``base``, within the image."""
    k = max(0, min(nbytes, len(mem0) - base))
    return np.frombuffer(bytes(mem0[base : base + k - k % 2]), dtype=endian + "u2").astype(np.int64)


def _split_words(mem0, lo, hi, n):
    """The 16-bit words of a lo/hi split table, within the image."""
    n = max(0, min(n, len(mem0) - lo, len(mem0) - hi))
    lob = np.frombuffer(bytes(mem0[lo : lo + n]), dtype="u1").astype(np.int64)
    hib = np.frombuffer(bytes(mem0[hi : hi + n]), dtype="u1").astype(np.int64)
    return lob | (hib << 8)


def _pitch_of(base, words, endian, hi):
    """A multi-octave Pitch over a recovered ET word run."""
    return Pitch(base, words, len(words) // 12, int(words[words > 0][0]), endian, False, hi)


def _candidates(prog, cap=0x100):
    """Every ET reading of the declared tables: interleaved, split, one-octave.

    Base, pairing and extent all come from the declarations — nothing is scanned
    out of the image, and the ET validators only confirm."""
    mem0, avail = prog.mem0, _avail(prog)
    out = []
    for b, n in sorted(avail.items()):
        for endian in ("<", ">"):
            w = _words_at(mem0, b, endian, min(n, 2 * cap))
            out += [(t, _pitch_of(b, ws, endian, None)) for t, ws in _et_words(w)]
            oc = _octave_words(w)
            if oc is not None:
                out.append((1, Pitch(b, oc, 1, int(oc[0]), endian, True)))
    for lo, nlo in sorted(avail.items()):
        for hi, nhi in sorted(avail.items()):
            n = min(nlo, nhi, cap, abs(hi - lo))
            if lo == hi or n < 12:
                continue
            w = _split_words(mem0, lo, hi, n)
            out += [(t, _pitch_of(lo, ws, "split", hi)) for t, ws in _et_words(w)]
            oc = _octave_words(w)
            if oc is not None:
                out.append((1, Pitch(lo, oc, 1, int(oc[0]), "split", True, hi)))
    return out


def _reach(p):
    """Every freq word the table can produce, octave shifts included."""
    w = p.words[p.words > 0]
    if p.shift:
        w = np.concatenate([w >> oc for oc in range(16)])
    return np.unique(w[w > 0])


def _explains(p, freqs):
    """Share of the observed freq words the table produces exactly, per frame.

    Exactness, not proximity: a dense decoy window is within half a semitone of
    anything, but only the real table holds the words the player wrote. Counted
    per frame, so the words a tune actually plays outweigh its rarities."""
    cand = _reach(p)
    if len(freqs) == 0 or len(cand) == 0:
        return 0.0
    return float(np.mean(np.isin(freqs, cand)))


def _pitch(prog, freqs=()):
    """The pitch table best explaining the observed freq words, or None.

    Ranked by explanatory power over the projection, then by ET evidence tier,
    then by extent — a decoy window holds none of the words the player wrote."""
    f = np.asarray(freqs, dtype=np.int64)
    best, key = None, ()
    for tier, p in _candidates(prog):
        k = (round(_explains(p, f), 2), tier, len(p.words))
        if best is None or k > key:
            best, key = p, k
    return best


def _freq_words(frames):
    """Every 16-bit freq word the projection writes, per voice per frame."""
    out = []
    for rec in frames:
        for v in range(3):
            sec, b = dict(rec[2 * v]), 7 * v
            if b in sec and b + 1 in sec:
                out.append(sec[b] | (sec[b + 1] << 8))
    return out


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


# ---- 3. the engine: clocks and instrument banks, read off the frameprog IR --------
def _base(addr):
    """Constant base of an address expression, 16-bit wrapped (SUB subtracts)."""
    k = addr[0]
    if k == "const":
        return addr[1]
    if k == "op" and addr[1] in ("INT_ADD", "INT_SUB"):
        kids = [_base(a) for a in addr[2] if isinstance(a, tuple)]
        if not kids:
            return 0
        base = sum(kids) if addr[1] == "INT_ADD" else kids[0] - sum(kids[1:])
        return base & 0xFFFF
    return 0


def _stmts(prog):
    """Every statement of every procedure, nested bodies included."""
    stack = [list(p[3]) for p in prog.procs]
    while stack:
        body = stack.pop()
        for s in body:
            yield s
            stack.extend(list(b) for b in frameproc._stmt_bodies(s))


def _resolve(expr, env):
    """Follow ``loc`` defs through ``env`` to the first non-``loc`` expression."""
    seen = set()
    while isinstance(expr, tuple) and expr[0] == "loc" and expr[1] not in seen:
        seen.add(expr[1])
        nxt = env.get(expr[1])
        if nxt is None:
            break
        expr = nxt
    return expr


def _read_base(expr, env):
    """Const base of the memory read ``expr`` resolves to, else 0."""
    root = _resolve(expr, env)
    return _base(root[1]) if isinstance(root, tuple) and root[0] == "mem" else 0


def _step(expr, env, cell):
    """``"inc"``/``"dec"`` if ``expr`` steps ``cell`` by one, else None."""
    root = _resolve(expr, env)
    if not (isinstance(root, tuple) and root[0] == "op" and root[1] in ("INT_ADD", "INT_SUB")):
        return None
    imm = [k for k in root[2] if isinstance(k, tuple) and k[0] == "const"]
    var = [k for k in root[2] if not (isinstance(k, tuple) and k[0] == "const")]
    if len(imm) != 1 or not var or _read_base(var[0], env) != cell:
        return None
    v = imm[0][1]
    if v == 0xFF or (root[1] == "INT_SUB" and v == 1):
        return "dec"
    return "inc" if root[1] == "INT_ADD" and v == 1 else None


def _clocks(prog):
    """Cells the play code steps by one, with the source their reload reads.

    ``dec`` + reload is a divider (tempo, note length); a free ``inc`` is an LFO
    phase. Read off the frameprog procedures, no second dataflow."""
    steps, reloads, env = {}, {}, {}
    for s in _stmts(prog):
        if s[0] == "asg":
            env[s[1]] = s[2]
        elif s[0] == "st" and 2 <= _base(s[1]) < _SID:
            cell = _base(s[1])
            kind = _step(s[2], env, cell)
            if kind is not None:
                steps.setdefault(cell, kind)
            else:
                src = _read_base(s[2], env)
                if src >= 0x100:
                    reloads.setdefault(cell, src)
    return [
        Clock(c, k, reloads.get(c), "lfo" if k == "inc" else "divider")
        for c, k in sorted(steps.items())
    ]


def _tempo(clocks):
    """frames_per_tick: the decrementing counter that reloads from a cell."""
    for c in clocks:
        if c.kind == "dec" and c.reload is not None:
            return c.reload
    return None


def _instruments(prog):
    """Const table bases feeding a ctrl/AD/SR store: the instrument banks."""
    out, env = set(), {}
    for s in _stmts(prog):
        if s[0] == "asg":
            env[s[1]] = s[2]
        elif s[0] == "st" and _SID <= _base(s[1]) <= _SID + 0x14:
            if (_base(s[1]) - _SID) % 7 in (4, 5, 6):
                src = _read_base(s[2], env)
                if src >= 0x100:
                    out.add(src)
    return sorted(out)


# ---- 4. instrument lanes: ADSR from a declared bank at a recovered row ------------
_ADSR = (5, 6)


def _banks(prog):
    """Declared const tables as ``(base, size, stride)``, stride at least one."""
    return [
        (d["base"], d["size"], max(1, d.get("stride") or 1))
        for d in prog.data_decls
        if d["kind"] == "table"
    ]


def _immediates(prog):
    """``{register class: {value}}``: constants a store site writes to a SID register.

    Keyed by ``reg % 7``: one voice-generic store site serves all three voices
    behind a dynamic offset, so the class is what the program text fixes."""
    out = {}
    for s in _stmts(prog):
        if s[0] == "st" and s[2][0] == "const":
            reg = _base(s[1]) - _SID
            if 0 <= reg <= 0x14:
                out.setdefault(reg % 7, set()).add(s[2][1] & 0xFF)
    return out


def _classify(w, banks, imm, mem0):
    """``(stream key, row)`` for one write: a declared lane byte, or an immediate.

    A lane read must agree with the declared image byte, so a cell the play phase
    mutated is never taken for constant data. None when neither reading holds."""
    reg, val, src = w
    if src is None:
        return (("imm", reg, val), 0) if val in imm.get(reg % 7, ()) else None
    for base, size, stride in banks:
        if base <= src < base + size and mem0[src] == val:
            row, off = divmod(src - base, stride)
            return ("lane", reg, base, size, stride, off), row
    return None


def _key_table(key, mem0):
    """A stream key's emitted table: one declared lane of a bank, or one constant."""
    if key[0] == "imm":
        return (key[2],)
    _k, _reg, base, size, stride, off = key
    return tuple(mem0[base + off + stride * i] for i in range((size - off + stride - 1) // stride))


def _mean_pos(obs, key):
    """Mean position of a key's writes inside the refined block."""
    pos = [i for row in obs for i, (k, _r, _v) in enumerate(row) if k == key]
    return sum(pos) / len(pos) if pos else 0.0


def _refine_voice(seq, targets, banks, imm, mem0):
    """``(relation, streams)`` refining one voice's ``targets`` registers, or None.

    Refuses unless every targeted write is explained, they sit at one end of the
    order-preserved section in every frame (so one placement against the residual
    holds), and the node order by mean position reproduces the observed order."""
    rel = {"pre", "post"}
    keys, rows, obs = [], {}, []
    for f, ws in enumerate(seq):
        at = [i for i, w in enumerate(ws) if w[0] in targets]
        if at:
            if at != list(range(len(at))):
                rel.discard("pre")
            if at != list(range(len(ws) - len(at), len(ws))):
                rel.discard("post")
            if not rel:
                return None
        row = []
        for i in at:
            got = _classify(ws[i], banks, imm, mem0)
            if got is None:
                return None
            key, r = got
            if key not in rows:
                rows[key] = [[] for _g in seq]
                keys.append(key)
            rows[key][f].append(r)
            row.append((key, ws[i][0], ws[i][1]))
        obs.append(row)
    tables = {k: _key_table(k, mem0) for k in keys}
    keys.sort(key=lambda k: _mean_pos(obs, k))
    for f, row in enumerate(obs):
        if [(k[1], tables[k][r]) for k in keys for r in rows[k][f]] != [e[1:] for e in row]:
            return None
    streams = []
    for k in keys:
        flat = tuple(r for fr in rows[k] for r in fr)
        t = ("LOOKUP", tables[k]) if k[0] == "imm" else ("SELECT", tables[k], flat)
        streams.append((tuple(len(fr) for fr in rows[k]), t, k[1]))
    return ("post" if "post" in rel else "pre"), streams


def _instr_streams(prog, ords):
    """``(pre, post, refined)``: ADSR streams, and the registers they take from RAW.

    Per voice the widest explainable register set wins; ``pre``/``post`` place a
    voice's streams before or after the residual, as its write order requires."""
    banks, imm, mem0 = _banks(prog), _immediates(prog), prog.mem0
    pre, post, refined = [], [], set()
    for v, seq in enumerate(ords):
        for regs in (_ADSR, (5,), (6,)):
            targets = {7 * v + r for r in regs}
            if not any(w[0] in targets for ws in seq for w in ws):
                continue
            got = _refine_voice(seq, targets, banks, imm, mem0)
            if got is not None:
                rel, streams = got
                (pre if rel == "pre" else post).extend(streams)
                refined |= targets
                break
    return pre, post, refined


# ---- 5. the law: the graph's projection is frameprog's ---------------------------
def oracle(prog, trace, nframes):
    """The frame projection the tracker must reproduce (frameprog, Gate FP-verified)."""
    return frameval.eval_fp(prog, trace, nframes)


def _observe(prog, trace, nframes):
    """``(canonical records, per-voice order-preserved writes with provenance)``.

    One machine run: the projection ``oracle`` defines, plus the cell each
    order-preserved write loaded its byte from (``frameval.eval_src``)."""
    frames, srcs = frameval.eval_src(prog, trace, nframes)
    ords = [[[] for _f in range(nframes)] for _v in range(3)]
    for f, (fr, sr) in enumerate(zip(frames, srcs)):
        for (reg, val), src in zip(fr, sr):
            if reg < 0x15 and reg % 7 >= 4:
                ords[reg // 7][f].append((reg, val, src))
    return framelog.canonical(frames), ords


def lift(prog, frames=()):
    """Lift the tune-independent engine parameters from a frame program."""
    clocks = _clocks(prog)
    return Tracker(_pitch(prog, _freq_words(frames)), clocks, _tempo(clocks), _instruments(prog))


def _graph(prog, pitch, frames, ords):
    """``(graph, lanes)``: notes and instrument lanes as generators, the rest RAW.

    A detuned frame counts only as vibrato on the current note, or as a fresh exact
    anchor; an excursion to an unrelated note stays residual."""
    lanes = [[], [], []]
    anchor = [None, None, None]
    seqs = {r: [] for r in _FREQ_REGS}
    residual = []
    pre, post, refined = _instr_streams(prog, ords)
    for f, rec in enumerate(frames):
        gen = {}
        for v in range(3):
            sec, b = dict(rec[2 * v]), 7 * v
            if b not in sec or b + 1 not in sec:
                continue
            word = sec[b] | (sec[b + 1] << 8)
            note = _note_of(pitch, word) if pitch else None
            if note is not None and (note.detune == 0 or note.index == anchor[v]):
                anchor[v] = note.index
                gen[b], gen[b + 1] = note.word & 0xFF, (note.word >> 8) & 0xFF
                lanes[v].append((f, note))
        for r, seq in seqs.items():
            seq.append(gen.get(r))
        residual.append([e for sec in rec for e in sec if e[0] not in gen and e[0] not in refined])
    edges = {}
    for counts, _t, _r in pre + post:
        edges.setdefault(counts, len(edges))
    nodes = [edge(c) for c in edges]
    fired = [Generator(t, ("event", edges[c]), ("plane", r)) for c, t, r in pre]
    nodes += fired + [raw(residual)]
    nodes += [Generator(t, ("event", edges[c]), ("plane", r)) for c, t, r in post]
    nodes += [lookup(seqs[r], FRAME, r) for r in _FREQ_REGS if any(v is not None for v in seqs[r])]
    return Graph(nodes, freq_table=pitch), lanes


def render(prog, trace, nframes):
    """``(rendered, oracle, Coverage, lanes)`` for the frame program's projection.

    Accepted-note freq entries and explained ADSR writes are interpreted
    generators; everything else is an explicit RAW residual, so a ``gate`` PASS
    certifies the partition is complete."""
    gt, ords = _observe(prog, trace, nframes)
    graph, lanes = _graph(prog, _pitch(prog, _freq_words(gt)), gt, ords)
    recs, interp, rawn = _run(graph, nframes)
    return recs, gt, _coverage(interp, rawn), lanes


def gate(prog, trace, nframes):
    """Gate verdict: None if the generator graph reproduces frameprog's projection."""
    rendered, gt, _cov, _lanes = render(prog, trace, nframes)
    return framelog.diff(rendered, gt)
