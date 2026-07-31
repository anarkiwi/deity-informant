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
Coverage = namedtuple("Coverage", "interp residual total planes classes", defaults=(None,))
Pitch = namedtuple("Pitch", "base words octaves reference endian shift hi", defaults=(None,))
Clock = namedtuple("Clock", "base kind reload role")
Note = namedtuple("Note", "index word name detune")
Tracker = namedtuple("Tracker", "pitch clocks tempo instruments")

_PLANE = {0: "freq", 1: "freq", 2: "pw", 3: "pw", 4: "ctrl", 5: "ad", 6: "sr"}
_VOICE_HI = 0x14
_FILTER_HI = 0x18


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

    def __init__(self, nodes, freq_table=None, cadence=None, classes=None):
        self.nodes = list(nodes)
        self.freq_table = freq_table
        self.cadence = cadence
        self.classes = classes

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

    Refinement removes a *write* from RAW, so a register may be split across RAW and
    a plane-routed node; node order then fixes the interleaving, which the partition
    is constructed and checked against (§5)."""
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
    if reg <= _VOICE_HI:
        return _PLANE[reg % 7]
    return "filter" if reg <= _FILTER_HI else "tail"


def _coverage(interp, rawn, classes=None):
    """The interpreted/residual partition of the emits, split by plane."""
    planes = {}
    for src, gen in ((interp, True), (rawn, False)):
        for reg, n in src.items():
            p = _plane_of(reg)
            it, tot = planes.get(p, (0, 0))
            planes[p] = (it + (n if gen else 0), tot + n)
    ni, nr = sum(interp.values()), sum(rawn.values())
    return Coverage(ni, nr, ni + nr, planes, classes)


def coverage(graph, nframes):
    """Interpreted vs residual emit counts, and the per-plane split."""
    _recs, interp, rawn = _run(graph, nframes)
    return _coverage(interp, rawn, graph.classes)


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


def _proc_stmts(proc):
    """Every statement of one procedure, nested bodies included."""
    stack = [list(proc[3])]
    while stack:
        for s in stack.pop():
            yield s
            stack.extend(list(b) for b in frameproc._stmt_bodies(s))


def _stmts(prog):
    """Every statement of every procedure, nested bodies included."""
    for proc in prog.procs:
        yield from _proc_stmts(proc)


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
        elif s[0] == "st" and _SID <= _base(s[1]) <= _SID + _VOICE_HI:
            if (_base(s[1]) - _SID) % 7 in (4, 5, 6):
                src = _read_base(s[2], env)
                if src >= 0x100:
                    out.add(src)
    return sorted(out)


# ---- 4. instrument lanes: ctrl/AD/SR from a declared bank at a recovered row ------
_CTRL = 4
_SECT = ((0xFF, 0x00), (0xFE, 0x00), (0xFF, 0x01))  # held: byte, gate cleared, gate set
_ORD_SECS = (1, 3, 5)  # the per-voice order-preserved sections of a canonical record


def _banks(prog):
    """Declared const tables as ``(base, size, stride, mut)``, stride at least one.

    ``mut`` is the declaration's play-written record offsets: lanes modulo the
    stride when strided, raw offsets otherwise. Those cells are not const data."""
    return [
        (
            d["base"],
            d["size"],
            max(1, d.get("stride") or 1),
            frozenset(d.get("mut") or ()),
        )
        for d in prog.data_decls
        if d["kind"] == "table"
    ]


def _record(size, stride):
    """Record length a declaration's ``mut`` offsets are taken modulo."""
    return stride if stride > 1 else size


def _decl_of(addr, banks):
    """The declaration containing ``addr``, or None."""
    for b in banks:
        if b[0] <= addr < b[0] + b[1]:
            return b
    return None


def _read_bases(expr, env, origins, depth=4):
    """Const read bases ``expr`` reaches through local definitions and staging cells.

    A local resolves to its definition and a staged byte to the values stored into
    the cell it came from — the origin hop the evaluator makes at runtime, made
    statically off the tree."""
    out, seen, stack = set(), set(), [(expr, depth)]
    while stack:
        x, d = stack.pop()
        if (x, d) in seen:
            continue
        seen.add((x, d))
        if x[0] == "op":
            stack.extend((c, d) for c in x[2])
        elif x[0] == "mem":
            b = _base(x[1])
            out.add(b)
            stack.extend((v, d - 1) for v in origins.get(b, ()) if d)
        elif x[0] == "loc":
            stack.extend((e, d - 1) for e in env.get(x[1], ()) if d)
    return out


def _tree_tables(prog, banks):
    """``{register class: declarations the program text reads into it}``.

    The store statement's value expression *names* the declaration the byte comes
    from: identification from the artifact, not a search for some bank that happens
    to hold a provenance cell with an agreeing byte."""
    origins, out = {}, {}
    for s in _stmts(prog):
        if s[0] == "st" and _sid_class(s[1]) is None:
            origins.setdefault(_base(s[1]), []).append(s[2])
    for proc in prog.procs:
        env, stores = {}, []
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                env.setdefault(s[1], []).append(s[2])
            elif s[0] == "st" and _sid_class(s[1]) is not None:
                stores.append((_sid_class(s[1]), s[2]))
        for cls, val in stores:
            for b in _read_bases(val, env, origins):
                d = _decl_of(b, banks)
                if d is not None:
                    out.setdefault(cls, set()).add(d)
    return {c: tuple(sorted(v)) for c, v in out.items()}


def _class_of(reg):
    """Register class of a SID offset: ``reg % 7`` per voice, the register itself for filter.

    $15-$18 are one global filter, not a voice, so they take a class of their own —
    ``reg % 7`` would alias them onto freq/pw."""
    return reg % 7 if reg <= _VOICE_HI else reg


def _sid_class(addr):
    """Register class of a SID store address, or None."""
    reg = _base(addr) - _SID
    return _class_of(reg) if 0 <= reg <= _FILTER_HI else None


def _const_flow(body, env, out):
    """``{class: {value}}`` for SID stores in one body; returns the exit ``env``.

    ``env[name]`` is the constant set a local may hold. A nested body inherits the
    environment and merges back, so a constant a branch loads reaches the store
    that follows it — the shape a gate-off or hard-restart write takes."""
    for s in body:
        if s[0] == "asg":
            env[s[1]] = {s[2][1] & 0xFF} if s[2][0] == "const" else set()
        elif s[0] == "st":
            cls, val = _sid_class(s[1]), s[2]
            if cls is not None and val[0] in ("const", "loc"):
                got = {val[1] & 0xFF} if val[0] == "const" else env.get(val[1], ())
                out.setdefault(cls, set()).update(got)
        for sub in [_const_flow(list(b), dict(env), out) for b in frameproc._stmt_bodies(s)]:
            for name, vals in sub.items():
                env[name] = env.get(name, set()) | vals
    return env


def _immediates(prog):
    """``{register class: {value}}``: constants a store site writes to a SID register.

    Keyed by ``reg % 7``: one voice-generic store site serves all three voices
    behind a dynamic offset, so the class is what the program text fixes."""
    out = {}
    for proc in prog.procs:
        _const_flow(list(proc[3]), {}, out)
    return out


def _lane(key, mem0):
    """The declared bytes of a bank lane, one per row."""
    _k, _reg, base, size, stride, off = key
    return tuple(mem0[base + off + stride * i] for i in range((size - off + stride - 1) // stride))


def _lane_key(w, banks, mem0):
    """``(stream key, row)`` for the first source cell reading a declared lane, else None.

    The byte must equal the declared image byte, and the offset must not be one the
    play phase writes: ``mut`` says that cell is not const data, so agreement with the
    snapshot is coincidence rather than a const read."""
    reg, val, srcs = w
    for src in srcs:
        for base, size, stride, mut in banks:
            if not base <= src < base + size or mem0[src] != val:
                continue
            if (src - base) % _record(size, stride) in mut:
                continue
            row, off = divmod(src - base, stride)
            return ("lane", reg, base, size, stride, off), row
    return None


def _classify(w, tabs, banks, imm, mem0, held):
    """``(stream key, row)`` for one write: a lane byte, its gate image, or a constant.

    The declarations the program text reads into this register class come first, so
    the table is named by the tree; the search over every bank is the fallback for a
    value the tree cannot express. A ctrl write carrying no cell of its own is the
    gate bit over the lane byte at the voice's held row: only bit 0 moves."""
    reg, val, _srcs = w
    for pool in (tabs.get(reg % 7, ()), banks):
        got = _lane_key(w, pool, mem0)
        if got is not None:
            held[reg] = got
            return got
    if reg % 7 == _CTRL and reg in held:
        key, row = held[reg]
        lane = _lane(key, mem0)
        for sec, (amask, omask) in enumerate(_SECT):
            if (lane[row] & amask) | omask == val:
                return key, row + (1 + sec) * len(lane)
    if val in imm.get(reg % 7, ()):
        return ("imm", reg, val), 0
    return None


def _key_table(key, mem0):
    """A stream key's emitted table: one declared lane of a bank, or one constant.

    A ctrl lane is followed by the three held readings of it, so the row says both
    which byte and how the gate reached it, and every byte emitted is declared."""
    if key[0] == "imm":
        return (key[2],)
    lane = _lane(key, mem0)
    if key[1] % 7 != _CTRL:
        return lane
    return lane + tuple((b & a) | o for a, o in _SECT for b in lane)


def _mean_pos(obs, key):
    """Mean position of a key's writes inside the refined block."""
    pos = [i for row in obs for i, (k, _r, _v) in enumerate(row) if k == key]
    return sum(pos) / len(pos) if pos else 0.0


_RES = ("raw",)  # the residual bucket: the writes of this voice that stay in RAW


def _precede(obs, nodes):
    """Reachability of the bucket digraph adjacent writes in a frame fix.

    Adjacent pairs suffice: the rendered section concatenates whole buckets in node
    order, so it equals the observed one exactly when every frame's bucket sequence
    is non-decreasing in that order."""
    idx = {n: i for i, n in enumerate(nodes)}
    m = np.zeros((len(nodes),) * 2, bool)
    for row in obs:
        for a, b in zip(row, row[1:]):
            if a[0] != b[0]:
                m[idx[a[0]], idx[b[0]]] = True
    for _ in range(len(nodes).bit_length()):
        m |= m @ m
    return idx, m


def _rank(n, obs, m, idx):
    """Sort key placing a bucket after its ancestors; an unconstrained key goes post."""
    return int(m[:, idx[n]].sum()), n != _RES, _mean_pos(obs, n), n


def _buckets(obs, weight):
    """``(obs, pre, post, live)``: a bucket order, demoting keys until one exists.

    A key on a cycle sits neither wholly before nor wholly after the residual, so it
    is demoted into it, lightest first. The acyclic remainder orders by ancestor
    count, a linear extension of the closure."""
    live = set(weight)
    while True:
        obs = [[(k if k in live else _RES, r, v) for k, r, v in row] for row in obs]
        nodes = sorted(live) + [_RES]
        idx, m = _precede(obs, nodes)
        loop = [n for n in nodes if n != _RES and m[idx[n], idx[n]]]
        if not loop:
            order = sorted(nodes, key=lambda n: _rank(n, obs, m, idx))
            cut = order.index(_RES)
            return obs, order[:cut], order[cut + 1 :], live
        live.discard(min(loop, key=lambda k: (weight[k], k)))


def _stream(key, rows, table):
    """One ``(per-frame counts, transfer, register)`` stream for a bucket's emits."""
    flat = tuple(r for fr in rows for r in fr)
    t = ("LOOKUP", table) if key[0] == "imm" else ("SELECT", table, flat)
    return tuple(len(fr) for fr in rows), t, key[1]


def _refine_voice(seq, tabs, banks, imm, mem0):
    """``(pre, post, residual)`` splitting one voice's ctrl/AD/SR writes, or None.

    Every write ``_classify`` explains is a candidate emit and the rest stay in RAW,
    so a register is split rather than forfeited; the split is then checked by
    rebuilding the order-preserved section from it, values included."""
    rows, obs, held, weight = {}, [], {}, {}
    for f, ws in enumerate(seq):
        row = []
        for w in ws:
            got = _classify(w, tabs, banks, imm, mem0, held)
            if got is not None:
                rows.setdefault(got[0], [[] for _g in seq])[f].append(got[1])
                weight[got[0]] = weight.get(got[0], 0) + 1
            row.append((_RES if got is None else got[0], w[0], w[1]))
        obs.append(row)
    obs, pre, post, live = _buckets(obs, weight)
    tables = {k: _key_table(k, mem0) for k in live}
    resid = []
    for f, row in enumerate(obs):
        mid = [e[1:] for e in row if e[0] == _RES]
        got = [(k[1], tables[k][r]) for k in pre for r in rows[k][f]]
        got += mid + [(k[1], tables[k][r]) for k in post for r in rows[k][f]]
        if got != [e[1:] for e in row]:
            return None
        resid.append(tuple(mid))
    return (
        [_stream(k, rows[k], tables[k]) for k in pre],
        [_stream(k, rows[k], tables[k]) for k in post],
        resid,
    )


def _classes(streams):
    """``{plane: {lane, gate, imm, ramp, seed}}``: refined emits by their evidence.

    ``lane`` is a declared bank byte at a row that emit's own provenance recovered
    and ``gate`` the same lane at the row the voice holds — both strong; ``ramp`` is
    an emit the accumulator's declared step generates. ``imm`` is a program constant
    and ``seed`` the one observed byte a ramp starts from: both pass the law without
    explaining a byte, and neither is ever folded into a strong figure."""
    out = {}
    for counts, t, reg in streams:
        cls = out.setdefault(
            _plane_of(reg), dict.fromkeys(("lane", "gate", "imm", "ramp", "seed"), 0)
        )
        if t[0] == "RAMP":
            cls["seed"] += 1
            cls["ramp"] += sum(counts) - 1
        elif t[0] == "LOOKUP":
            cls["imm"] += sum(counts)
        elif reg % 7 == _CTRL:
            n = len(t[1]) // (1 + len(_SECT))
            cls["lane"] += sum(r < n for r in t[2])
            cls["gate"] += sum(r >= n for r in t[2])
        else:
            cls["lane"] += len(t[2])
    return out


def _instr_streams(prog, ords, tabs, banks):
    """``(pre, post, residual)``: instrument streams, and the writes still replayed.

    ``pre``/``post`` place each voice's streams either side of the RAW node, as the
    order-preserved section requires; a voice whose section cannot be rebuilt keeps
    every write."""
    imm, mem0 = _immediates(prog), prog.mem0
    pre, post, resid = [], [], []
    for seq in ords:
        got = _refine_voice(seq, tabs, banks, imm, mem0)
        if got is None:
            resid.append([tuple(w[:2] for w in ws) for ws in seq])
            continue
        pre.extend(got[0])
        post.extend(got[1])
        resid.append(got[2])
    return pre, post, resid


# ---- 4b. the last-write-wins planes: freq/pw/filter off the store statement -------
def _lww_streams(lww, tabs, mem0):
    """``(streams, explained)``: declared-lane SELECT nodes for the freq/pw/filter planes.

    The store statement names the declaration and the read cell recovers the row, so
    the emitted byte is declared data at the index the play code used. The plane is
    last-write-wins, so only the frames the declaration explains fire."""
    streams, explained = {}, [set() for _f in lww]
    for f, wr in enumerate(lww):
        for reg in sorted(wr):
            val, srcs = wr[reg]
            got = _lane_key((reg, val, srcs), tabs.get(_class_of(reg), ()), mem0)
            if got is None:
                continue
            key, row = got
            counts, rows = streams.setdefault(key, ([0] * len(lww), []))
            counts[f] += 1
            rows.append(row)
            explained[f].add(reg)
    out = [
        (tuple(counts), ("SELECT", _key_table(k, mem0), tuple(rows)), k[1])
        for k, (counts, rows) in streams.items()
    ]
    return out, explained


# ---- 4c. the pulse sweep: a RAMP whose step is a declared byte --------------------
def _step_site(s, env, envl, banks, origins):
    """``(site, sign)`` if this store steps its own cell by one declared byte.

    ``("fix", cell)`` is a declared scalar and ``("lane", decl, off)`` a lane read at
    the row the voice holds; a step the tree names no other way is refused, and so is
    one it names two ways."""
    addr = _base(s[1])
    root = _resolve(s[2], env)
    if not (isinstance(root, tuple) and root[0] == "op" and root[1] in ("INT_ADD", "INT_SUB")):
        return None
    held = [t for t in root[2] if _read_base(t, env) == addr]
    if not held or (root[1] == "INT_SUB" and root[2][0] not in held):
        return None
    sites = set()
    for t in root[2]:
        for b in () if t in held else _read_bases(t, envl, origins):
            d = _decl_of(b, banks)
            if d is None or (b - d[0]) % _record(d[1], d[2]) in d[3]:
                continue
            sites.add(("lane", d, (b - d[0]) % d[2]) if d[2] > 1 else ("fix", b))
    return (sites.pop(), -1 if root[1] == "INT_SUB" else 1) if len(sites) == 1 else None


def _accumulators(prog, banks):
    """``{register class: (site, sign)}``: the pw accumulators the store statement names.

    The pw store reads a cell the play code steps by a declared byte: that cell is the
    accumulator and the byte is its step. Two accumulators reaching one store refuse it."""
    origins, out = {}, {}
    for s in _stmts(prog):
        if s[0] == "st" and _sid_class(s[1]) is None:
            origins.setdefault(_base(s[1]), []).append(s[2])
    for proc in prog.procs:
        env, envl, steps, reads = {}, {}, {}, []
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                env[s[1]] = s[2]
                envl.setdefault(s[1], []).append(s[2])
            elif s[0] == "st":
                cls = _sid_class(s[1])
                if cls is None:
                    got = _step_site(s, env, envl, banks, origins)
                    if got is not None:
                        steps[_base(s[1])] = got
                elif _PLANE.get(cls) == "pw":
                    reads.append((cls, _read_bases(s[2], envl, origins)))
        for cls, bases in reads:
            hit = {b: steps[b] for b in bases if b in steps}
            if len(hit) == 1:
                out.setdefault(cls, hit.popitem()[1])
    return out


def _hold_rows(ords, lww, banks, nframes):
    """``[frame][voice][declaration base] -> row``, from the source cell of every write."""
    cur, out = [{} for _v in range(3)], []
    for f in range(nframes):
        for v in range(3):
            for w in ords[v][f]:
                _hold(cur[v], w[2], banks)
        for reg, (_val, srcs) in lww[f].items():
            if reg <= _VOICE_HI:  # the filter is global: it holds no voice's row
                _hold(cur[reg // 7], srcs, banks)
        out.append([dict(c) for c in cur])
    return out


def _hold(cur, srcs, banks):
    """Record the row each source cell recovers in its declaration."""
    for c in srcs:
        d = _decl_of(c, banks)
        if d is not None:
            cur[d[0]] = (c - d[0]) // d[2]


def _step_byte(site, mem0, held):
    """``(step cell, declared byte)`` for a step site, or None without a row."""
    if site[0] == "fix":
        return site[1], mem0[site[1]]
    _k, (base, size, stride, _mut), off = site
    row = held.get(base)
    if row is None:
        return None
    cell = base + stride * row + off
    return (cell, mem0[cell]) if base <= cell < base + size else None


def _acc_streams(acc, banks, lww, mem0, ords):
    """``(streams, explained)``: the pw sweep as a RAMP over its declared step.

    A pw write the evaluator reports no source cell for is the accumulator store; its
    step is the declared byte the tree named, at the row the voice holds. One run per
    step cell, kept only if the ramp regenerates every byte of it from its first."""
    rows = _hold_rows(ords, lww, banks, len(lww))
    runs, seen = {}, {}
    for f, wr in enumerate(lww):
        for reg in sorted(wr):
            val, srcs = wr[reg]
            if srcs or reg > _VOICE_HI or reg % 7 not in acc:
                continue
            site, sign = acc[reg % 7]
            got = _step_byte(site, mem0, rows[f][reg // 7])
            if got is None:
                continue
            cell, byte = got
            if seen.get(reg) != cell:
                seen[reg] = cell
                seen[reg, "n"] = seen.get((reg, "n"), 0) + 1
            runs.setdefault((reg, cell, seen[reg, "n"]), []).append((f, val, sign * byte))
    streams, explained = [], [set() for _f in lww]
    for (reg, _cell, _n), run in runs.items():
        seed, step = run[0][1], run[0][2]
        if step % 0x100 == 0 or len(run) < 2:
            continue
        if any((seed + step * i) & 0xFF != v for i, (_f, v, _s) in enumerate(run)):
            continue
        counts = [0] * len(lww)
        for f, _v, _s in run:
            counts[f] = 1
            explained[f].add(reg)
        streams.append((tuple(counts), ("RAMP", seed, step, 0x100), reg))
    return streams, explained


# ---- 5. the law: the graph's projection is frameprog's ---------------------------
def oracle(prog, trace, nframes):
    """The frame projection the tracker must reproduce (frameprog, Gate FP-verified)."""
    return frameval.eval_fp(prog, trace, nframes)


def _observe(prog, trace, nframes):
    """``(canonical records, order-preserved writes, last-write-wins writes)``, with provenance.

    One machine run: the projection ``oracle`` defines, plus the cell each write
    loaded its byte from (``frameval.eval_src``) — the index the store statement's
    table read used, which the tree names but cannot evaluate."""
    frames, srcs = frameval.eval_src(prog, trace, nframes)
    ords = [[[] for _f in range(nframes)] for _v in range(3)]
    lww = [{} for _f in range(nframes)]
    for f, (fr, sr) in enumerate(zip(frames, srcs)):
        for (reg, val), src in zip(fr, sr):
            if reg > _FILTER_HI:
                continue
            if reg <= _VOICE_HI and reg % 7 >= 4:
                ords[reg // 7][f].append((reg, val, src))
            else:
                lww[f][reg] = (val, src)
    return framelog.canonical(frames), ords, lww


def lift(prog, frames=()):
    """Lift the tune-independent engine parameters from a frame program."""
    clocks = _clocks(prog)
    return Tracker(_pitch(prog, _freq_words(frames)), clocks, _tempo(clocks), _instruments(prog))


def _graph(prog, pitch, frames, ords, lww):
    """``(graph, lanes)``: declared lanes and notes as generators, the rest RAW.

    A declared lane the store statement names outranks the note reading of the same
    byte; a detuned frame counts only as vibrato on the current note, or as a fresh
    exact anchor, and an excursion to an unrelated note stays residual."""
    banks = _banks(prog)
    tabs = _tree_tables(prog, banks)
    lanes = [[], [], []]
    anchor = [None, None, None]
    seqs = {r: [] for r in _FREQ_REGS}
    residual = []
    pre, post, ires = _instr_streams(prog, ords, tabs, banks)
    lwws, declared = _lww_streams(lww, tabs, prog.mem0)
    ramps, swept = _acc_streams(_accumulators(prog, banks), banks, lww, prog.mem0, ords)
    for f, rec in enumerate(frames):
        gen, done = {}, declared[f] | swept[f]
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
            seq.append(None if r in done else gen.get(r))
        keep = set(gen) | done
        secs = [
            ires[i // 2][f] if i in _ORD_SECS else [e for e in s if e[0] not in keep]
            for i, s in enumerate(rec)
        ]
        residual.append([e for sec in secs for e in sec])
    streams = pre + post + lwws + ramps
    edges = {}
    for counts, _t, _r in streams:
        edges.setdefault(counts, len(edges))
    nodes = [edge(c) for c in edges]
    fired = [Generator(t, ("event", edges[c]), ("plane", r)) for c, t, r in pre]
    nodes += fired + [raw(residual)]
    nodes += [Generator(t, ("event", edges[c]), ("plane", r)) for c, t, r in post + lwws + ramps]
    nodes += [lookup(seqs[r], FRAME, r) for r in _FREQ_REGS if any(v is not None for v in seqs[r])]
    return Graph(nodes, freq_table=pitch, classes=_classes(streams)), lanes


def render(prog, trace, nframes):
    """``(rendered, oracle, Coverage, lanes)`` for the frame program's projection.

    Accepted-note freq entries and explained ADSR writes are interpreted
    generators; everything else is an explicit RAW residual, so a ``gate`` PASS
    certifies the partition is complete."""
    gt, ords, lww = _observe(prog, trace, nframes)
    graph, lanes = _graph(prog, _pitch(prog, _freq_words(gt)), gt, ords, lww)
    recs, interp, rawn = _run(graph, nframes)
    return recs, gt, _coverage(interp, rawn, graph.classes), lanes


def gate(prog, trace, nframes):
    """Gate verdict: None if the generator graph reproduces frameprog's projection."""
    rendered, gt, _cov, _lanes = render(prog, trace, nframes)
    return framelog.diff(rendered, gt)
