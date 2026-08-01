"""tracker — the universal tracker layer over a frame program.

One primitive: a triggered generator ``(transfer, trigger, route)``; a tune is a
graph of them. One law: the graph's canonical projection equals frameprog's under
the same input trace. One input: a ``frameprog.FrameProgram``. See docs/tracker.md."""

import bisect
from collections import Counter, namedtuple

import numpy as np

from . import framelog
from . import frameproc
from . import frameptr
from . import frameval

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_SEMI = 2 ** (1 / 12)
_SID = 0xD400
_FREQ_REGS = (0, 1, 7, 8, 14, 15)

FRAME = ("frame",)

Generator = namedtuple("Generator", "transfer trigger route")
Coverage = namedtuple("Coverage", "interp residual total planes classes triggers")
Pitch = namedtuple("Pitch", "base words octaves reference endian shift hi", defaults=(None,))
Clock = namedtuple("Clock", "base kind reload role")
Note = namedtuple("Note", "index word name detune")
Region = namedtuple("Region", "base size stride cursors")
Pairs = namedtuple("Pairs", "regions cursors", defaults=((), None))
Tracker = namedtuple("Tracker", "pitch clocks tempo instruments divisors")

_PLANE = {0: "freq", 1: "freq", 2: "pw", 3: "pw", 4: "ctrl", 5: "ad", 6: "sr"}
_VOICE_HI = 0x14
_FILTER_HI = 0x18
_FULL = 0xFF  # the mask of a route that owns a register's whole byte
_CLASSES = ("lane", "gate", "imm", "ramp", "seed", "mask", "rel", "arr")
_REL = {"ADD": lambda b, d: b + d, "SUB": lambda b, d: b - d, "XOR": lambda b, d: b ^ d}


class TrackerError(ValueError):
    """The graph is not evaluable (unknown transfer, dangling trigger)."""


# ---- 1. the one primitive: a triggered generator ---------------------------------
def plane(reg, mask=_FULL):
    """A SID register plane route, over the whole byte or the bits ``mask`` names."""
    return ("plane", reg) if mask == _FULL else ("plane", reg, mask)


def relative(reg, op, base, mask=_FULL):
    """A relative plane route: the emit is a delta ``op`` combines with ``base``.

    ``base`` is ``("prev",)`` (the plane's own previously emitted value), ``("node", i)``
    (generator ``i``'s current value) or ``("const", c)`` (a declared base byte)."""
    return ("rel", reg, mask, op, base)


INDEX = ("index",)  # a value-carrying edge: the emit is another generator's row index


def _mask_of(route):
    """The bits a plane route owns; a route that names none owns the byte."""
    if route[0] == "rel":
        return route[2]
    return route[2] if len(route) > 2 else _FULL


def _is_plane(route):
    """Does this route write a SID register plane, absolutely or relatively?"""
    return route[0] in ("plane", "rel")


def div(n, trigger=FRAME, phase=None):
    """Emit one tick per ``n`` input triggers: a clock. Route is always Fire.

    ``phase`` is the frame the first tick lands on modulo ``n``. It belongs to the
    arrangement, not to the divider (§8): the post-init counter byte says where the
    count runs out, and an unstaged cell's zero yields the default ``n-1``."""
    return Generator(
        ("DIV", n, (n - 1 if phase is None else phase) % max(1, n)), trigger, ("fire",)
    )


def lookup(seq, trigger, reg, mask=_FULL):
    """Emit ``seq[i]`` into a plane: a ``SELECT`` that recovered no row."""
    return Generator(("SELECT", tuple(seq), ()), trigger, plane(reg, mask))


def ramp(seed, step, bound, trigger, reg, mask=_FULL):
    """Emit ``seed + step*count`` into a plane, wrapped at ``bound``."""
    return Generator(("RAMP", seed, step, bound), trigger, plane(reg, mask))


def select(table, rows, trigger, reg, mask=_FULL):
    """Emit ``table[rows[i]]`` into a plane: a declared table read at a row.

    ``rows`` is a recovered run of row indices, or ``("node", j)`` — generator ``j``'s
    emit, so the row is produced rather than observed (§7.4)."""
    return Generator(("SELECT", tuple(table), tuple(rows)), trigger, plane(reg, mask))


def indexer(transfer, trigger):
    """A generator whose emit is another's row index, writing no register itself.

    The value counterpart of a Fire edge: ``Fire`` says *when* a downstream table
    advances, this says *which row* it reads."""
    return Generator(transfer, trigger, INDEX)


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
        p = g.transfer[2] if len(g.transfer) > 2 else n - 1
        return 1 if frame % n == p % n else 0
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


def _generated(rows):
    """Is this row source another generator's emit rather than a recovered run?"""
    return bool(rows) and rows[0] in ("node", "rel")


def _sources(rows):
    """Node indices a generated row source reads, in evaluation order."""
    if not _generated(rows):
        return ()
    return (rows[1],) if rows[0] == "node" else tuple(s[1] for s in rows[2:] if s[0] == "node")


def _named(src, cur, prev):
    """The value a named delta or base holds: a constant, a node's emit, the plane's own."""
    if src[0] == "const":
        return src[1]
    return cur.get(src[1]) if src[0] == "node" else prev


def _row(rows, count, cur):
    """The row a ``SELECT`` reads: recovered at its own tick, or another's emit.

    A generated row is what the named generator holds, optionally combined with a
    named base — a transpose shifts the row, not the byte. A source that has not
    emitted yet supplies nothing, so the read is dropped rather than guessed."""
    if not _generated(rows):
        return rows[(count - 1) % len(rows)]
    if rows[0] == "node":
        return cur.get(rows[1])
    _k, op, delta, base = rows
    d, v = _named(delta, cur, None), _named(base, cur, None)
    return None if d is None or v is None else _REL[op](v, d)


def _emit(g, count, cur=()):
    """Value a plane-routed generator emits on its ``count``-th trigger."""
    kind = g.transfer[0]
    if kind == "SELECT":
        _k, table, rows = g.transfer
        if not table:
            return None
        i = _row(rows, count, cur or {}) if rows else (count - 1) % len(table)
        return table[i] if i is not None and 0 <= i < len(table) else None
    if kind == "RAMP":
        _k, seed, step, bound = g.transfer
        raw_v = seed + step * (count - 1)
        return raw_v % bound if bound else raw_v
    raise TrackerError("transfer %r has no value emit" % (kind,))


def _field_of(route):
    """The field a route settles absolutely: a plane's masked byte, an index, or nothing."""
    return ("plane", route[1], _mask_of(route)) if route[0] == "plane" else route


def _rel_ok(nodes, i, op, srcs, field):
    """Refuse a relative form the graph cannot settle — one rule, both domains.

    Absolutes settle a field and relatives apply to it in node order, so every named
    source must be settled first: a byte ``Const``, an earlier absolute generator of the
    same ``field``, or the plane's own ``Prev``, which a row index has no meaning for."""
    if op is not None and op not in _REL:
        raise TrackerError("unknown relative operation %r" % (op,))
    for base in srcs:
        if base[0] == "const":
            if not 0 <= base[1] <= _FULL:
                raise TrackerError("relative base %r is not a byte" % (base,))
        elif base[0] == "prev":
            if field == INDEX or not any(
                g.route[0] == "raw" or (g.route[0] == "plane" and g.route[1] == field[1])
                for g in nodes[:i]
            ):
                raise TrackerError("relative base %r has no previous value" % (base,))
        elif base[0] != "node" or not 0 <= base[1] < i:
            raise TrackerError("relative base %r is not an earlier node" % (base,))
        elif _field_of(nodes[base[1]].route) != field:
            raise TrackerError("relative base %r drives another field" % (base,))


def _index_ok(nodes, i, g):
    """Refuse a generated row index the graph cannot supply before it is read.

    The sources must be earlier ``index``-routed nodes, so the value edge runs the same
    way node order already runs and no cycle can form. An ``index`` route with no reader
    is refused too: a generator that neither writes nor is read is dead."""
    rows = g.transfer[2] if g.transfer[0] == "SELECT" else ()
    if _generated(rows):
        rel = rows[0] == "rel"
        _rel_ok(nodes, i, rows[1] if rel else None, rows[2:] if rel else (rows,), INDEX)
    if g.route == INDEX and not any(
        h.transfer[0] == "SELECT" and i in _sources(h.transfer[2]) for h in nodes
    ):
        raise TrackerError("index route on node %d has no reader" % (i,))


def _check(nodes):
    """Refuse a graph that is not evaluable, masks and relative bases included.

    Two generators sharing a register must own the same bits or disjoint ones: a
    partial overlap is two owners of one bit, which no order resolves. A relative
    route must further name a base an earlier generator settles (`_rel_ok`)."""
    owned = {}
    for i, g in enumerate(nodes):
        if g.trigger != FRAME and g.trigger[0] != "event":
            raise TrackerError("unknown trigger %r" % (g.trigger,))
        if g.trigger[0] == "event" and not 0 <= g.trigger[1] < len(nodes):
            raise TrackerError("dangling trigger %r" % (g.trigger,))
        if g.route[0] not in ("plane", "fire", "raw", "rel", "index"):
            raise TrackerError("unknown route %r" % (g.route,))
        _index_ok(nodes, i, g)
        if not _is_plane(g.route):
            continue
        m = _mask_of(g.route)
        if not 0 < m <= _FULL:
            raise TrackerError("route mask %r owns no bit" % (g.route,))
        for other in owned.setdefault(g.route[1], set()):
            if other != m and other & m:
                raise TrackerError(
                    "routes $%02X and $%02X overlap on $%02X" % (other, m, g.route[1])
                )
        owned[g.route[1]].add(m)
        if g.route[0] == "rel":
            _rel_ok(nodes, i, g.route[3], (g.route[4],), ("plane", g.route[1], m))


def _assemble(g, v, held, writes):
    """The byte one emit writes: its own, or the fields its register's masks hold.

    A masked generator latches the bits it owns; the byte is written by the last of
    them to fire, so a register several generators drive takes one write."""
    mask = _mask_of(g.route)
    if v is None or mask == _FULL:
        return v
    held[mask] = v & mask
    if not writes:
        return None
    out = 0
    for b in held.values():
        out |= b
    return out


def _masked(nodes):
    """``{reg: [node index]}`` for the registers a masked route drives."""
    out = {}
    for i, g in enumerate(nodes):
        if _is_plane(g.route) and _mask_of(g.route) != _FULL:
            out.setdefault(g.route[1], []).append(i)
    return out


def _combine(route, delta, prev, cur):
    """The byte a relative route writes: its delta, combined with the named base.

    A base the graph has not settled yet emits nothing, so a mis-built relative stream
    drops a write and the law says so rather than inventing a base."""
    _k, reg, mask, op, base = route
    val = _named(base, cur, prev.get(reg))
    return None if delta is None or val is None else _REL[op](val & mask, delta) & 0xFF


def _run(graph, nframes):
    """``(canonical records, interpreted emits, raw emits)`` per register.

    Refinement removes a *write* from RAW, so node order fixes the interleaving of a
    split register (§5); a masked generator latches the bits it owns and the last of
    a register's masked generators to fire writes the byte (§4e)."""
    nodes = graph.nodes
    _check(nodes)
    counts = [0] * len(nodes)
    interp, rawn, trig = {}, {}, {}
    parts = _masked(nodes)
    held = {reg: {} for reg in parts}
    prev, cur = {}, {}
    out = []
    for f in range(nframes):
        fires = _fired(nodes, f)
        eaten = {
            g.route[4][1]
            for i, g in enumerate(nodes)
            if fires[i] and g.route[0] == "rel" and g.route[4][0] == "node"
        }
        last = {
            r: max((i for i in ns if fires[i] and i not in eaten), default=None)
            for r, ns in parts.items()
        }
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
                    prev[reg] = val
            elif g.route == INDEX:  # a value edge: it indexes, it does not write
                for _t in range(fires[i]):
                    counts[i] += 1
                    cur[i] = _emit(g, counts[i], cur)
            elif _is_plane(g.route):
                reg = g.route[1]
                for _t in range(fires[i]):  # one emit per trigger, in order
                    counts[i] += 1
                    cur[i] = v = _emit(g, counts[i], cur)
                    if g.route[0] == "rel":
                        v = _combine(g.route, v, prev, cur)
                    if i in eaten:  # a base generator supplies a value, it does not write
                        continue
                    v = _assemble(g, v, held.get(reg), i == last.get(reg))
                    if v is None:
                        continue
                    interp[reg] = interp.get(reg, 0) + 1
                    prev[reg] = v & 0xFF
                    writes.append((reg, v & 0xFF))
            else:
                counts[i] += fires[i]
                if g.route[0] == "fire":  # the trigger domain's own census
                    k = g.transfer[0]
                    trig[k] = trig.get(k, 0) + _ticks(g, f) * fires[i]
        out.append(writes)
    return framelog.canonical(out), interp, rawn, trig


def eval_graph(graph, nframes):
    """Canonical per-frame records produced by propagating triggers."""
    return _run(graph, nframes)[0]


def _generates(counts, n, phase):
    """Does ``DIV(n)`` at ``phase`` fire exactly where ``counts`` fires, and nowhere else?"""
    return all(c == (1 if f % n == phase else 0) for f, c in enumerate(counts))


def _plane_of(reg):
    """Canonical plane class for a SID register offset."""
    if reg <= _VOICE_HI:
        return _PLANE[reg % 7]
    return "filter" if reg <= _FILTER_HI else "tail"


def _coverage(interp, rawn, classes=None, trig=None):
    """The interpreted/residual partition of the emits, split by plane.

    ``triggers`` is the *other* domain's partition: ``(generated, all)`` fires, a
    generated fire being a ``DIV`` tick over a declared divisor and the rest the
    ``EDGE`` floor. Two domains, two numbers, never summed."""
    planes = {}
    for src, gen in ((interp, True), (rawn, False)):
        for reg, n in src.items():
            p = _plane_of(reg)
            it, tot = planes.get(p, (0, 0))
            planes[p] = (it + (n if gen else 0), tot + n)
    ni, nr = sum(interp.values()), sum(rawn.values())
    fired = sum((trig or {}).values())
    return Coverage(ni, nr, ni + nr, planes, classes, (fired - (trig or {}).get("EDGE", 0), fired))


def coverage(graph, nframes):
    """Interpreted vs residual emit counts, the per-plane split, and the trigger census."""
    _recs, interp, rawn, trig = _run(graph, nframes)
    return _coverage(interp, rawn, graph.classes, trig)


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


def _unmask(root, env):
    """``(expression, wrap modulus)`` peeling one ``AND``-immediate off a stored value."""
    if isinstance(root, tuple) and root[0] == "op" and root[1] == "INT_AND" and len(root[2]) == 2:
        cs = [k for k in root[2] if isinstance(k, tuple) and k[0] == "const"]
        vs = [k for k in root[2] if not (isinstance(k, tuple) and k[0] == "const")]
        if len(cs) == 1 and (cs[0][1] & 0xFF) + 1 & (cs[0][1] & 0xFF) == 0:
            return _resolve(vs[0], env), (cs[0][1] & 0xFF) + 1
    return root, 0x100


def _walk_of(s, env, cell):
    """``("step", d, wrap)`` or ``("set", c, wrap)`` where the text fully determines a store.

    A 6502 counter wraps with an ``AND``-immediate, so the modulus is program text too;
    anything else about the value disqualifies the cell."""
    root, wrap = _unmask(_resolve(s[2], env), env)
    if not isinstance(root, tuple):
        return None
    if root[0] == "const":
        return ("set", root[1] % wrap, wrap)
    if root[0] == "op" and root[1] in ("INT_ADD", "INT_SUB"):
        imm = [k for k in root[2] if isinstance(k, tuple) and k[0] == "const"]
        var = [k for k in root[2] if not (isinstance(k, tuple) and k[0] == "const")]
        if len(imm) == 1 and len(var) == 1 and _read_base(var[0], env) == cell:
            d = imm[0][1] & 0xFF
            return ("step", (-d if root[1] == "INT_SUB" else d) & 0xFF, wrap)
    return None


def _prog_env(prog):
    """A program-wide ``{local: definition}`` map, the reading every other rule here uses."""
    env = {}
    for s in _stmts(prog):
        if s[0] == "asg":
            env[s[1]] = s[2]
    return env


def _walked(prog):
    """``{base: (rule, ...)}`` for cells the play code only steps or sets by its own text.

    Such a cell's value is the post-init byte plus the updates the text names, in the
    order the machine ran them — a walk, not an observation. One writer the text does
    not determine disqualifies the cell outright."""
    rules, bad, env = {}, set(), {}
    for s in _stmts(prog):
        if s[0] == "asg":
            env[s[1]] = s[2]
        elif s[0] == "st" and 2 <= _base(s[1]) < _SID:
            cell = _base(s[1])
            got = _walk_of(s, env, cell)
            if got is None:
                bad.add(cell)
            else:
                rules.setdefault(cell, []).append(got)
    return {c: tuple(v) for c, v in rules.items() if c not in bad}


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
    base, size, stride, off = key[2:6]
    return tuple(mem0[base + off + stride * i] for i in range((size - off + stride - 1) // stride))


def _pair_at(objs, curs, f):
    """The pair context one frame reads: the regions, and the states its cursors passed."""
    return Pairs(objs, curs[f] if f < len(curs) else None)


def _decl_cells(reg, srcs, banks, mem0, pairs=Pairs()):
    """``[(stream key, row, declared byte)]`` for the source cells reading a declared lane.

    A region the text indexes at a named cursor keys the read first, so the node is the
    editor's object; the whole declaration is the fallback where no base names the cell.
    The offset must not be one the play phase writes: ``mut`` says that cell is not const
    data, so agreement with the snapshot is coincidence rather than a const read."""
    out = []
    for src in srcs:
        for base, size, stride, mut in banks:
            if not base <= src < base + size:
                continue
            if (src - base) % _record(size, stride) in mut:
                continue
            o = _object_at(pairs.regions, src)
            if o is not None and base <= o.base:
                key = ("lane", reg, o.base, o.size, o.stride, 0, o.cursors)
                out.append((key, (src - o.base) // o.stride, mem0[src]))
                continue
            row, off = divmod(src - base, stride)
            out.append((("lane", reg, base, size, stride, off, None), row, mem0[src]))
    return out


def _lane_key(w, banks, mem0, pairs=Pairs(), diag=None):
    """``(stream key, row)`` for the first source cell reading a declared lane, else None.

    The declared byte must equal the byte the register took, which is what makes the
    emit a const read rather than a cell that merely happens to be indexed."""
    reg, val, srcs = w
    for key, row, byte in _decl_cells(reg, srcs, banks, mem0, pairs):
        if byte == val:
            _pair_verify(key, row, pairs, diag)
            return key, row
    return None


def _classify(w, tabs, banks, imm, mem0, held, pairs=Pairs(), diag=None):
    """``(stream key, row)`` for one write: a lane byte, its gate image, or a constant.

    The declarations the program text reads into this register class come first, so
    the table is named by the tree; the search over every bank is the fallback for a
    value the tree cannot express. A ctrl write carrying no cell of its own is the
    gate bit over the lane byte at the voice's held row: only bit 0 moves."""
    reg, val, _srcs = w
    for pool in (tabs.get(reg % 7, ()), banks):
        got = _lane_key(w, pool, mem0, pairs, diag)
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


def _select(key, rows, mem0):
    """The ``SELECT`` a stream key emits: its declared lane at ``rows``, or its constant.

    An ``imm`` key recovers no row at all — the one-byte table is read straight through."""
    return ("SELECT", _key_table(key, mem0), () if key[0] == "imm" else tuple(rows))


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
    """One ``(counts, transfer, register, evidence, key)`` stream: an ``imm`` recovers no row."""
    imm = key[0] == "imm"
    flat = () if imm else tuple(r for fr in rows for r in fr)
    ev = "imm" if imm else "lane"
    return tuple(len(fr) for fr in rows), ("SELECT", table, flat), key[1], ev, key


def _refine_voice(seq, tabs, banks, imm, mem0, objs=(), curs=(), diag=None):
    """``(pre, post, residual)`` splitting one voice's ctrl/AD/SR writes, or None.

    Every write ``_classify`` explains is a candidate emit and the rest stay in RAW,
    so a register is split rather than forfeited; the split is then checked by
    rebuilding the order-preserved section from it, values included."""
    rows, obs, held, weight = {}, [], {}, {}
    for f, ws in enumerate(seq):
        row, pairs = [], _pair_at(objs, curs, f)
        for w in ws:
            got = _classify(w, tabs, banks, imm, mem0, held, pairs, diag)
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
    _pair_census(((k, sum(len(fr) for fr in rows[k])) for k in pre + post), diag)
    return (
        [_stream(k, rows[k], tables[k]) for k in pre],
        [_stream(k, rows[k], tables[k]) for k in post],
        resid,
    )


def _classes(streams, groups=(), rels=(), pairs=()):
    """``{plane: {lane, gate, imm, ramp, seed, mask}}``: refined emits by their evidence.

    ``lane`` is a declared bank byte at a row that emit's own provenance recovered
    and ``gate`` the same lane at the row the voice holds — both strong; ``ramp`` is
    an emit the accumulator's declared step generates. ``imm`` is a program constant,
    ``seed`` the one observed byte a ramp starts from, ``mask`` a byte several
    generators assemble field by field and ``rel`` a declared delta over a live base:
    none of the last four is ever folded into a strong figure."""
    out = {}
    for counts, _parts, reg in groups:
        out.setdefault(_plane_of(reg), dict.fromkeys(_CLASSES, 0))["mask"] += sum(counts)
    for counts, _t, reg, _op, _base in rels:
        out.setdefault(_plane_of(reg), dict.fromkeys(_CLASSES, 0))["rel"] += sum(counts)
    for counts, _r, _s, reg in pairs:
        out.setdefault(_plane_of(reg), dict.fromkeys(_CLASSES, 0))["arr"] += sum(counts)
    for counts, t, reg, ev, _key in streams:
        cls = out.setdefault(_plane_of(reg), dict.fromkeys(_CLASSES, 0))
        if ev == "ramp":
            cls["seed"] += 1
            cls["ramp"] += sum(counts) - 1
        elif ev == "imm":
            cls["imm"] += sum(counts)
        elif reg % 7 == _CTRL:
            n = len(t[1]) // (1 + len(_SECT))
            cls["lane"] += sum(r < n for r in t[2])
            cls["gate"] += sum(r >= n for r in t[2])
        else:
            cls["lane"] += len(t[2])
    return out


def _instr_streams(prog, ords, tabs, banks, objs=(), curs=(), diag=None):
    """``(pre, post, residual)``: instrument streams, and the writes still replayed.

    ``pre``/``post`` place each voice's streams either side of the RAW node, as the
    order-preserved section requires; a voice whose section cannot be rebuilt keeps
    every write."""
    imm, mem0 = _immediates(prog), prog.mem0
    pre, post, resid = [], [], []
    for seq in ords:
        got = _refine_voice(seq, tabs, banks, imm, mem0, objs, curs, diag)
        if got is None:
            resid.append([tuple(w[:2] for w in ws) for ws in seq])
            continue
        pre.extend(got[0])
        post.extend(got[1])
        resid.append(got[2])
    return pre, post, resid


# ---- 4b. the last-write-wins planes: freq/pw/filter off the store statement -------
def _lww_streams(lww, tabs, mem0, objs=(), curs=(), diag=None):
    """``(streams, explained)``: declared-lane SELECT nodes for the freq/pw/filter planes.

    The store statement names the declaration and the read cell recovers the row, so
    the emitted byte is declared data at the index the play code used. The plane is
    last-write-wins, so only the frames the declaration explains fire."""
    streams, explained = {}, [set() for _f in lww]
    for f, wr in enumerate(lww):
        pairs = _pair_at(objs, curs, f)
        for reg in sorted(wr):
            val, srcs = wr[reg]
            got = _lane_key((reg, val, srcs), tabs.get(_class_of(reg), ()), mem0, pairs, diag)
            if got is None:
                continue
            key, row = got
            counts, rows = streams.setdefault(key, ([0] * len(lww), []))
            counts[f] += 1
            rows.append(row)
            explained[f].add(reg)
    _pair_census(((k, len(rows)) for k, (_c, rows) in streams.items()), diag)
    out = [
        (tuple(counts), _select(k, rows, mem0), k[1], "lane", k)
        for k, (counts, rows) in streams.items()
    ]
    return out, explained


# ---- 4c. the pulse sweep: a RAMP whose step the origin map names ------------------
_CUTOFF = (0x15, 0x16)


def _def_stmt(expr, env, defs, fallback):
    """The statement whose value expression ``expr`` resolves to through the locals."""
    seen, out = set(), fallback
    while isinstance(expr, tuple) and expr[0] == "loc" and expr[1] not in seen:
        seen.add(expr[1])
        if expr[1] not in env:
            break
        out, expr = defs[expr[1]], env[expr[1]]
    return out


def _steps_own(s, env):
    """``1``/``-1`` if the store adds a term to a read of its own cell, else None."""
    root = _resolve(s[2], env)
    if not (isinstance(root, tuple) and root[0] == "op" and root[1] in ("INT_ADD", "INT_SUB")):
        return None
    held = [t for t in root[2] if _read_base(t, env) == _base(s[1])]
    if not held or (root[1] == "INT_SUB" and root[2][0] not in held):
        return None
    return -1 if root[1] == "INT_SUB" else 1


def _acc_sites(prog):
    """``([statement to watch], [cells per statement], {cell: sign})``: the accumulators.

    Watched is the statement the arithmetic happens in — the store, or the assignment
    its value resolves through, since a store of a bare local carries no origin at all.
    Watching is by identity, so one assignment two stores share answers for both."""
    watch, cells, at, signs = [], [], {}, {}
    for proc in prog.procs:
        env, defs = {}, {}
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                env[s[1]], defs[s[1]] = s[2], s
                continue
            if s[0] != "st" or _sid_class(s[1]) is not None:
                continue
            sign = _steps_own(s, env)
            if sign is None:
                continue
            w = _def_stmt(s[2], env, defs, s)
            i = at.setdefault(id(w), len(watch))
            if i == len(watch):
                watch.append(w)
                cells.append(set())
            cells[i].add(_base(s[1]))
            signs.setdefault(_base(s[1]), sign)
    return watch, cells, signs


def _acc_pools(cells, watched):
    """``[frame][accumulator cell] -> candidate step origins``, one entry per execution.

    An execution reports the cells its byte derives from with each origin ahead of it;
    the cell it wrote is the accumulator, so what is left is where the step came from.
    Per execution: one statement serves three voices and re-stages mid-run."""
    out = [{} for _f in watched]
    for f, ws in enumerate(watched):
        for i, cell, srcs in ws:
            if i >= len(cells):  # the arrangement's own watches share this one run
                continue
            pool = [x for x in srcs if x != cell]
            for c in cells[i]:
                out[f].setdefault(c, []).extend(pool)
    return out


def _accumulators(prog, signs):
    """``{register class: (accumulator cell, sign)}``: the accumulator a store reads.

    A pw or cutoff store whose value reaches exactly one stepped cell is that
    accumulator's output; two accumulators reaching one store refuse it."""
    origins, out, stepped = {}, {}, set(signs)
    for s in _stmts(prog):
        if s[0] == "st" and _sid_class(s[1]) is None:
            origins.setdefault(_base(s[1]), []).append(s[2])
    for proc in prog.procs:
        envl = {}
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                envl.setdefault(s[1], []).append(s[2])
                continue
            cls = _sid_class(s[1]) if s[0] == "st" else None
            if cls is None or not (_PLANE.get(cls) == "pw" or cls in _CUTOFF):
                continue
            hit = _read_bases(s[2], envl, origins) & stepped
            if len(hit) == 1:
                cell = hit.pop()
                out.setdefault(cls, (cell, signs[cell]))
    return out


def _runs(vals):
    """``[(start, emits, delta)]``: maximal constant-nonzero-delta runs of two or more."""
    out, i = [], 0
    d = [(vals[j + 1] - vals[j]) & 0xFF for j in range(len(vals) - 1)]
    while i < len(d):
        j = i
        while j < len(d) and d[j] == d[i]:
            j += 1
        if d[i]:
            out.append((i, j - i + 1, d[i]))
        i = j
    return out


def _acc_streams(acc, pools, banks, tabs, lww, mem0):
    """``(streams, explained)``: the sweep as a RAMP whose step the origin map names.

    A maximal constant-delta run of the register's own emits is the candidate; every
    stepped emit's accumulator execution must report an origin that is a declared byte
    at a non-``mut`` offset equal to that step, so a fitted step is refused."""
    seqs = {}
    for f, wr in enumerate(lww):
        for reg, (val, srcs) in wr.items():
            cls = _class_of(reg)
            if cls in acc and _lane_key((reg, val, srcs), tabs.get(cls, ()), mem0) is None:
                seqs.setdefault(reg, []).append((f, val))
    streams, explained = [], [set() for _f in lww]
    for reg, seq in sorted(seqs.items()):
        cell, sign = acc[_class_of(reg)]
        claimed = set()
        for at, n, delta in _runs([v for _f, v in seq]):
            if seq[at][0] in claimed:  # adjacent runs share their boundary emit
                at, n = at + 1, n - 1
            step = delta if sign > 0 else (-delta) & 0xFF
            if n < 2 or not all(
                _lane_key((reg, step, pools[f].get(cell, ())), banks, mem0) is not None
                for f, _v in seq[at + 1 : at + n]
            ):
                continue
            counts = [0] * len(lww)
            for f, _v in seq[at : at + n]:
                counts[f] = 1
                claimed.add(f)
                explained[f].add(reg)
            streams.append((tuple(counts), ("RAMP", seq[at][1], delta, 0x100), reg, "ramp", None))
    return streams, explained


# ---- 4d. the trigger domain: a DIV whose divisor is a declared reload -------------
def _reloads(prog, banks):
    """``{divider cell: {divisor}}``: what the play code reloads into a cell it steps down.

    A recovered divider (``_clocks``) is reloaded either with a program immediate or
    from a declared byte at a non-``mut`` offset; nothing else is a divisor. A reload
    of one divides nothing — that is the root frame clock — and is refused."""
    clocks = [c for c in _clocks(prog) if c.role == "divider"]
    out, env = {c.base: set() for c in clocks}, {}
    for c in clocks:
        d = None if c.reload is None else _decl_of(c.reload, banks)
        if d is not None and (c.reload - d[0]) % _record(d[1], d[2]) not in d[3]:
            out[c.base].add(int(prog.mem0[c.reload]))
    for s in _stmts(prog):
        if s[0] == "asg":
            env[s[1]] = s[2]
        elif s[0] == "st" and _base(s[1]) in out:
            root = _resolve(s[2], env)
            if isinstance(root, tuple) and root[0] == "const":
                out[_base(s[1])].add(root[1] & 0xFF)
    return {c: {n for n in ns if n > 1} for c, ns in out.items()}


def _divisors(prog, banks):
    """Every divisor the play code declares, over all its dividers (§4d)."""
    return tuple(sorted({n for ns in _reloads(prog, banks).values() for n in ns}))


def _clock_node(counts, ticks):
    """A ``DIV`` where a declared tick generates this edge stream, else the floor.

    Divisor and phase are both program text — the reload, and the counter's own
    post-init byte — and the whole stream is checked against them in both directions;
    a period or a phase read off the fire pattern would explain nothing (§4c)."""
    for n, phase in ticks:
        if _generates(counts, n, phase):
            return div(n, phase=phase)
    return edge(counts)


# ---- 4e. one plane, two generators: the bit partition the store statement names ---
def _term(expr, env):
    """``(is a constant, the bits it can set, its value)`` for one OR term.

    The mask is the program text's: a constant owns its own bits, an AND-immediate
    owns its mask, a shift moves that mask. Anything else names none."""
    r = _resolve(expr, env)
    if not isinstance(r, tuple):
        return None
    if r[0] == "const":
        return (True, r[1] & 0xFF, r[1] & 0xFF)
    if r[0] == "op" and len(r[2]) == 2 and r[1] in ("INT_AND", "INT_LEFT", "INT_RIGHT"):
        cs = [k for k in r[2] if isinstance(k, tuple) and k[0] == "const"]
        if len(cs) == 1 and r[1] == "INT_AND":
            return (False, cs[0][1] & 0xFF, None)
        if len(cs) == 1 and r[2][1] is cs[0]:
            sub = _term(r[2][0], env)
            if sub is not None and sub[1] is not None:
                m = sub[1] << cs[0][1] if r[1] == "INT_LEFT" else sub[1] >> cs[0][1]
                return (False, m & 0xFF, None)
    return (False, None, None)


def _partition(expr, env):
    """The bit partition a store's value expression names, or None.

    An OR of terms partitions the byte where the text names every term's bits but
    one, which takes the rest; overlapping or uncovered bits are not a partition."""
    r = _resolve(expr, env)
    if not (isinstance(r, tuple) and r[0] == "op" and r[1] == "INT_OR"):
        return None
    terms = [_term(t, env) for t in r[2]]
    if any(t is None for t in terms):
        return None
    unk = [i for i, t in enumerate(terms) if t[1] is None]
    known = 0
    for i, t in enumerate(terms):
        if i not in unk:
            if known & t[1]:
                return None
            known |= t[1]
    if len(unk) > 1 or (not unk and known != _FULL):
        return None
    if unk:
        if known == _FULL:
            return None
        terms[unk[0]] = (False, _FULL & ~known, None)
    return tuple(terms)


def _partitions(prog):
    """``{register class: [partition]}``: the bit partitions the program text names."""
    out = {}
    for proc in prog.procs:
        env = {}
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                env[s[1]] = s[2]
                continue
            cls = _sid_class(s[1]) if s[0] == "st" else None
            got = None if cls is None else _partition(s[2], env)
            if got is not None and got not in out.setdefault(cls, []):
                out[cls].append(got)
    return out


def _decompose(w, parts, pool, mem0):
    """``[(mask, key, row)]`` assembling one write out of disjoint fields, or None.

    Each field is the store statement's own constant over the bits it names, or a
    declared byte at the row the read cell recovers over the bits left to it."""
    reg, val, srcs = w
    for part in parts:
        got = []
        for isconst, mask, c in part:
            if isconst:
                got.append((mask, ("imm", reg, c), 0) if c == val & mask else None)
            else:
                k = _lane_key((reg, val & mask, srcs), pool, mem0)
                got.append(None if k is None else (mask, k[0], k[1]))
            if got[-1] is None:
                break
        if len(got) == len(part) and None not in got:
            return got
    return None


def _mask_streams(lww, parts, tabs, mem0, done):
    """``(groups, explained)``: one register's byte assembled from several generators.

    A group's parts fire together and own disjoint fields, so the write is the byte
    they assemble; the keys a register's masks take are fixed at its first explained
    frame, since one field has one owner."""
    counts, rows, keys, explained = {}, {}, {}, [set() for _f in lww]
    for f, wr in enumerate(lww):
        for reg in sorted(wr):
            cls = _class_of(reg)
            val, srcs = wr[reg]
            if reg in done[f]:
                continue
            got = _decompose((reg, val, srcs), parts.get(cls, ()), tabs.get(cls, ()), mem0)
            if got is None:
                continue
            fix = {m: k for m, k, _r in got}
            if keys.setdefault(reg, fix) != fix:  # one field, one owner, for the whole tune
                continue
            counts.setdefault(reg, [0] * len(lww))[f] = 1
            for m, _k, r in got:
                rows.setdefault((reg, m), []).append(r)
            explained[f].add(reg)
    groups = [
        (
            tuple(cnt),
            [(_select(keys[reg][m], rows[(reg, m)], mem0), m) for m in sorted(keys[reg])],
            reg,
        )
        for reg, cnt in sorted(counts.items())
    ]
    return groups, explained


# ---- 4f. the relative route: a declared delta over a base the statement names ------
_REL_OPS = {"INT_ADD": "ADD", "INT_SUB": "SUB", "INT_XOR": "XOR"}
Site = namedtuple("Site", "op base pool bpool")


def _mirrors(prog):
    """``{cell: {register class}}``: cells the text stores a register's own value into.

    A non-SID store whose value expression is one a SID store of that class also writes
    makes its cell that plane's mirror, so a later read of it *is* the previous emit."""
    out = {}
    for proc in prog.procs:
        sid, cells = {}, {}
        for s in _proc_stmts(proc):
            if s[0] != "st":
                continue
            cls = _sid_class(s[1])
            if cls is None:
                cells.setdefault(s[2], set()).add(_base(s[1]))
            else:
                sid.setdefault(s[2], set()).add(cls)
        for expr, clss in sid.items():
            for cell in cells.get(expr, ()):
                out.setdefault(cell, set()).update(clss)
    return out


def _term_role(term, env, envl, origins, banks):
    """``("const", c)``, ``("decl", decls)``, ``("cell", bases)`` or ``("computed", ())``."""
    root = _resolve(term, env)
    if isinstance(root, tuple) and root[0] == "const":
        return ("const", root[1] & _FULL)
    got = _read_bases(term, envl, origins)
    lanes = tuple(sorted({d for b in got if (d := _decl_of(b, banks)) is not None}))
    if lanes:
        return ("decl", lanes)
    return ("cell", tuple(sorted(got))) if got else ("computed", ())


def _is_mirror(term, env, cls, mirrors):
    """Does ``term`` read, directly, a cell the text mirrors this register class into?"""
    root = _resolve(term, env)
    return isinstance(root, tuple) and root[0] == "mem" and cls in mirrors.get(_base(root[1]), ())


def _rel_site(cls, root, roles, ctx, diag):
    """The `Site` one binary-op store names, or None with the refusal it costs.

    The base is the term the text names — a program constant, this plane's own mirror
    cell, or a second declared lane — and the delta is the declared byte beside it."""
    env, mirrors = ctx
    op, (ka, va), (kb, vb) = _REL_OPS[root[1]], roles[0], roles[1]
    if ka == "decl" and kb == "const":  # lane - c is lane + (-c): the base is the constant
        neg = (-vb if op == "SUB" else vb) & _FULL
        return Site("ADD" if op == "SUB" else op, ("const", neg), va, ())
    if ka == "const" and kb == "decl":
        return Site(op, ("const", va), vb, ())
    mirror = [_is_mirror(t, env, cls, mirrors) for t in root[2]]
    if mirror[0] and kb == "decl":
        return Site(op, ("prev",), vb, ())
    if mirror[1] and ka == "decl":
        if op == "SUB":  # a declared lane minus the plane's own value is not base-op-delta
            diag["rel_site_sub_order"] += 1
            return None
        return Site(op, ("prev",), va, ())
    if ka == "decl" and kb == "decl":
        return Site(op, ("gen",), vb, va)
    diag["rel_site_unnamed_base" if "decl" in (ka, kb) else "rel_site_no_declared_term"] += 1
    return None


def _rel_sites(prog, banks, diag):
    """``{register class: [Site]}``: the relative stores the program text names."""
    mirrors, origins, out = _mirrors(prog), {}, {}
    for s in _stmts(prog):
        if s[0] == "st" and _sid_class(s[1]) is None:
            origins.setdefault(_base(s[1]), []).append(s[2])
    for proc in prog.procs:
        env, envl = {}, {}
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                env[s[1]] = s[2]
                envl.setdefault(s[1], []).append(s[2])
                continue
            cls = _sid_class(s[1]) if s[0] == "st" else None
            if cls is None:
                continue
            root = _resolve(s[2], env)
            if not (isinstance(root, tuple) and root[0] == "op" and root[1] in _REL_OPS):
                continue
            if len(root[2]) != 2:
                diag["rel_site_not_binary"] += 1
                continue
            roles = [_term_role(t, env, envl, origins, banks) for t in root[2]]
            got = _rel_site(cls, root, roles, (env, mirrors), diag)
            if got is not None and got not in out.setdefault(cls, []):
                out[cls].append(got)
    return out


def _relate(w, sites, mem0, prev, diag, pairs=Pairs()):
    """``(site, delta cell, base cell)`` predicting this write, else None.

    The delta is the declared byte at the cell the machine read and the base is the
    named one; the write is claimed only where combining them *predicts* the byte the
    register took. Nothing is solved for: a delta read back off the output is refused."""
    reg, val, srcs = w
    for site in sites:
        if site.base[0] == "gen":
            bases = _decl_cells(reg, srcs, site.bpool, mem0, pairs)
        else:
            got = site.base[1] if site.base[0] == "const" else prev
            bases = () if got is None else ((None, 0, got),)
        if not bases:
            diag["rel_no_base"] += 1
            continue
        for cell in _decl_cells(reg, srcs, site.pool, mem0, pairs):
            if not cell[2]:  # a zero delta predicts nothing, as DIV(1) and RAMP(0) do not
                diag["rel_zero_delta"] += 1
                continue
            for base in bases:
                if base[:2] != cell[:2] and _REL[site.op](base[2], cell[2]) & _FULL == val:
                    return site, cell, base
        diag["rel_unpredicted"] += 1
    return None


def _rel_streams(lww, sites, mem0, done, diag, objs=(), curs=()):
    """``(streams, explained)``: the relative route, one stream per delta lane.

    Each stream is a declared lane the store statement names, routed relatively over the
    base that statement names; every emit is predicted rather than fitted, so a write the
    combination does not reproduce stays residual."""
    streams, explained, prev = {}, [set() for _f in lww], {}
    for f, wr in enumerate(lww):
        pairs = _pair_at(objs, curs, f)
        for reg in sorted(wr):
            val, srcs = wr[reg]
            got = (
                None
                if reg in done[f]
                else _relate(
                    (reg, val, srcs),
                    sites.get(_class_of(reg), ()),
                    mem0,
                    prev.get(reg),
                    diag,
                    pairs,
                )
            )
            prev[reg] = val
            if got is None:
                continue
            site, cell, base = got
            diag["rel_over_" + site.base[0]] += 1
            counts, rows, brows = streams.setdefault(
                (reg, site, cell[0], base[0]), ([0] * len(lww), [], [])
            )
            counts[f] += 1
            rows.append(cell[1])
            brows.append(base[1])
            explained[f].add(reg)
    out = [
        (
            tuple(counts),
            _select(key, rows, mem0),
            reg,
            site.op,
            (site.base if bkey is None else ("gen", _select(bkey, brows, mem0))),
        )
        for (reg, site, key, bkey), (counts, rows, brows) in streams.items()
    ]
    return out, explained


def _rel_cost(lww, sites, banks, mem0, done, diag):
    """Count what a delta read back off the output would take, and what refuses it.

    ``rel_fitted`` is every unexplained write in a relative class a back-computed
    ``val - prev`` would "explain"; ``rel_mut`` the ones a `mut` offset alone refuses."""
    prev = {}
    for f, wr in enumerate(lww):
        for reg in sorted(wr):
            val, srcs = wr[reg]
            was, prev[reg] = prev.get(reg), val
            if reg in done[f] or _class_of(reg) not in sites:
                continue
            diag["rel_fitted"] += int(was is not None and (val - was) & 0xFF != 0)
            for base, size, stride, mut in banks:
                if any(
                    base <= s < base + size and (s - base) % _record(size, stride) in mut
                    for s in srcs
                ):
                    diag["rel_mut"] += 1
                    break


# ---- 4g. the arrangement: a declared pattern at a row the program text walks ------
Arr = namedtuple("Arr", "cell lo hi row step wrap init")


def _arr_rule(rules):
    """``(step, wrap)`` where a walked cell's rules name one step and one modulus."""
    steps = {r for r in rules if r[0] == "step"}
    wraps = {r[2] for r in rules}
    if len(steps) != 1 or len(wraps) != 1:
        return None
    step, wrap = steps.pop()[1], wraps.pop()
    return (step, wrap) if step % wrap and wrap > 1 else ()


def _arr_sites(prog, env, walk, diag):
    """``({pointer cell: Arr}, {address: pointer})`` for derefs read at a walked row.

    Rung (f) proves the address is row ``i`` of block ``T[k]``; what it does not give is
    ``i``, so the row must be a cell whose every writer the program text names."""
    out, addrs, bad = {}, {}, set()
    for s in frameptr.analyse(prog.mem0, prog.data_decls, prog.procs):
        if s.why is not None or not s.ptr.tables:
            continue
        cell = 0 if s.idx is None else _read_base(s.idx, env)
        rule = _arr_rule(walk[cell]) if cell in walk else None
        addrs[s.addr] = s.ptr.cell
        if not rule:  # a walk that stands still predicts no row, as DIV(1) predicts no tick
            diag["arrange_row_not_walked" if rule is None else "arrange_walk_stands_still"] += 1
            bad.add(s.ptr.cell)
            continue
        got = Arr(s.ptr.cell, s.ptr.tables[0][0], s.ptr.tables[0][1], cell, *rule, s.ptr.init)
        if out.setdefault(s.ptr.cell, got) != got:
            diag["arrange_two_rows"] += 1
            bad.add(s.ptr.cell)
    return {c: a for c, a in out.items() if c not in bad}, addrs


def _arr_reads(expr, envl, addrs, depth=4):
    """Pointer cells a value expression reaches through a proven deref read."""
    out, seen, stack = set(), set(), [(expr, depth)]
    while stack:
        x, d = stack.pop()
        if (x, d) in seen:
            continue
        seen.add((x, d))
        if x[0] == "op":
            stack.extend((c, d) for c in x[2])
        elif x[0] == "mem":
            if x[1] in addrs:
                out.add(addrs[x[1]])
            stack.append((x[1], d))
        elif x[0] == "loc":
            stack.extend((e, d - 1) for e in envl.get(x[1], ()) if d)
    return out


def _arr_classes(prog, addrs):
    """``{register class: {pointer cell}}``: SID stores whose text names a proven deref.

    The deref address is impure, so ``frameval`` reports the pointer's own cells and
    never the target; what names the pattern is therefore the statement tree, as §4b."""
    out = {}
    for proc in prog.procs:
        envl, stores = {}, []
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                envl.setdefault(s[1], []).append(s[2])
            elif s[0] == "st" and _sid_class(s[1]) is not None:
                stores.append((_sid_class(s[1]), s[2]))
        for cls, val in stores:
            for c in _arr_reads(val, envl, addrs):
                out.setdefault(cls, set()).add(c)
    return out


def _arr_watch(prog, sites, at):
    """``([statement], {watch index: tag})``: each pointer's reload and each row cell's walk."""
    keys = {a.cell: ("ptr", a) for a in sites.values()}
    keys.update({a.row: ("row", a) for a in sites.values()})
    out, tags, seen = [], {}, set()
    for s in _stmts(prog):
        got = keys.get(_base(s[1])) if s[0] == "st" else None
        if got is not None and id(s) not in seen:
            seen.add(id(s))
            tags[at + len(out)] = got
            out.append(s)
    return out, tags


def _arr_states(sites, tags, wat, mem0, nframes):
    """``[frame][(pointer, block, row)]``: every state the arrangement's walk passes.

    The block comes off the machine's own address bus and the row off the post-init byte
    stepped by the text's rule, in the order the machine ran the writers."""
    block = {c: a.init for c, a in sites.items()}
    row = {c: mem0[a.row] for c, a in sites.items()}
    out = []
    for f in range(nframes):
        seen = [(c, block[c], row[c]) for c in sites]
        for i, cell, srcs in wat[f] if f < len(wat) else ():
            kind, a = tags.get(i, (None, None))
            if kind == "row" and cell == a.row:
                row[a.cell] = (row[a.cell] + a.step) % a.wrap
            elif kind == "ptr" and cell == a.cell:
                ks = {c - a.lo for c in srcs if 0 <= c - a.lo < 0x100}
                if len(ks) != 1:
                    continue
                k = ks.pop()
                block[a.cell] = mem0[a.lo + k] | (mem0[a.hi + k] << 8)
            else:
                continue
            seen.append((a.cell, block[a.cell], row[a.cell]))
        out.append(seen)
    return out


def _arr_table(block, wrap, banks, mem0):
    """The declared bytes of one pattern block, or None where no declaration holds them."""
    d = _decl_of(block, banks)
    if d is None:
        return None
    off, rec = block - d[0], _record(d[1], d[2])
    n = min(wrap, d[1] - off)
    if n < 1 or any((off + i) % rec in d[3] for i in range(n)):
        return None
    return tuple(mem0[block + i] for i in range(n))


def _arr_claim(lww, classes, states, tabs, diag):
    """``{(reg, pointer, block): [(frame, row)]}``: writes the predicted address explains.

    A write is a candidate only where the store statement names that pointer's deref, and
    is claimed only where the declared byte at the predicted address is the byte the
    register took — the ``mem0[src] == val`` pair every other lane emit passes."""
    out = {}
    for f, wr in enumerate(lww):
        for reg in sorted(wr):
            val, _srcs = wr[reg]
            for c in sorted(classes.get(_class_of(reg), ())):
                if c not in tabs:  # a pointer whose row the program text does not walk
                    continue
                tab = tabs[c]
                at = [(blk, r) for p, blk, r in states[f] if p == c]
                if not any(tab.get(blk) for blk, _r in at):
                    diag["arrange_block_undeclared"] += 1
                    continue
                hits = {
                    (blk, r)
                    for blk, r in at
                    if tab.get(blk) and r < len(tab[blk]) and tab[blk][r] == val
                }
                if not hits:
                    diag["arrange_unpredicted"] += 1
                elif len({b + r for b, r in hits}) > 1:
                    diag["arrange_ambiguous"] += 1
                else:
                    blk, row = hits.pop()
                    out.setdefault((reg, c, blk), []).append((f, row))
    return out


def _arr_pairs(lww, arr, states, banks, mem0, done, diag):
    """``(pairs, explained)``: a fed pattern ``SELECT`` and the ``RAMP`` that rows it.

    One block is one pattern node, shared by every song step that revisits it, and the row
    ``RAMP`` wraps at the modulus the text names — the pattern's own loop. A row stream
    one ramp does not reproduce refuses its block whole, as a sweep run does."""
    sites, classes = arr
    tabs = {}
    for st in states:
        for p, blk, _r in st:
            tabs.setdefault(p, {}).setdefault(blk, _arr_table(blk, sites[p].wrap, banks, mem0))
    claims = _arr_claim(
        [{r: v for r, v in wr.items() if r not in dn} for wr, dn in zip(lww, done)],
        classes,
        states,
        tabs,
        diag,
    )
    pairs, explained = [], [set() for _f in lww]
    for (reg, ptr, blk), got in sorted(claims.items()):
        a = sites[ptr]
        for run in _arr_runs(got, a.step, a.wrap):
            if len(run) < 2:  # one row predicts no second one, as DIV(1) predicts no tick
                diag["arrange_short_run"] += len(run)
                continue
            counts = [0] * len(lww)
            for f, _r in run:
                counts[f] += 1
                explained[f].add(reg)
            pairs.append((tuple(counts), ("RAMP", run[0][1], a.step, a.wrap), tabs[ptr][blk], reg))
    return pairs, explained


def _arr_cost(lww, ords, classes, told, arranged, diag):
    """Price the refusals: the order-preserved section, and a row taken off the output.

    ``arrange_fitted`` is every write a *segmentation of the observed row stream* would
    claim — a store the text points at a proven pattern, whose row no walk supplies."""
    for f, wr in enumerate(lww):
        for reg in wr:
            if reg not in told[f] and reg not in arranged[f] and _class_of(reg) in classes:
                diag["arrange_fitted"] += 1
    for v in range(3):
        for ws in ords[v]:
            diag["arrange_ord_section"] += sum(1 for w in ws if _class_of(w[0]) in classes)


def _arr_runs(got, step, wrap):
    """Maximal runs of one block's emits over which the row walks by the text's own step."""
    out = []
    for f, r in got:
        if out and r == (out[-1][-1][1] + step) % wrap:
            out[-1].append((f, r))
        else:
            out.append([(f, r)])
    return out


# ---- 4h. the node identity: a declared region at a cursor the program text names --
def _cursors(idx, env, depth=4):
    """Cursor cells an index expression reads, through the locals defined above it.

    Program text only, never an observed value: the index is followed through its
    locals and through any table it is itself read out of (tools/node_partition.py §2)."""
    out, stack, seen = set(), [(idx, depth)], set()
    while stack:
        x, k = stack.pop()
        if not isinstance(x, tuple) or (x, k) in seen:
            continue
        seen.add((x, k))
        if x[0] == "op":
            stack += [(c, k) for c in x[2]]
        elif x[0] == "mem":
            got = frameproc._index_of(x[1])
            out.add(_base(x[1]))
            if got is not None and k:
                stack.append((got[1], k - 1))
        elif x[0] == "loc" and k and x[1] in env:
            stack.append((env[x[1]], k - 1))
    return {c for c in out if c >= 0x100}


def _loads(expr, env, out):
    """Collect one expression's ``base[index]`` loads into ``{load base: {cursor}}``."""
    stack = [expr]
    while stack:
        x = stack.pop()
        if not isinstance(x, tuple):
            continue
        if x[0] == "mem":
            got = frameproc._index_of(x[1])
            if got is not None:
                out.setdefault(got[0], set()).update(_cursors(got[1], env))
            stack.append(x[1])
        elif x[0] == "op":
            stack += list(x[2])


def _load_walk(stmts, env, out):
    """Statements in order, each load resolved against the locals live at that point."""
    for s in stmts:
        frameproc._map_exprs(s, lambda e: _loads(e, env, out) or e)
        for body in frameproc._stmt_bodies(s):
            _load_walk(list(body), dict(env), out)
        if s[0] == "asg":
            env[s[1]] = s[2]


def _pairs(prog):
    """``{load base: (cursor cell, ...)}``: every indexed load the program text writes."""
    out = {}
    for proc in prog.procs:
        _load_walk(list(proc[3]), {}, out)
    return {b: tuple(sorted(cs)) for b, cs in out.items()}


def _objects(prog, banks, diag):
    """``[Region]`` in address order: a declared region split at the bases the text indexes.

    A declaration tiles a whole data block, so containment resolves the block and not the
    table (docs/node-partition.md §2.1); the text's own load bases resolve it, each running
    to the next named base or to the end of const data. A base the text names no cursor
    for is **refused**: without one the read has no identity to partition by."""
    named = []
    for b, curs in sorted(_pairs(prog).items()):
        d = _decl_of(b, banks)
        if d is None:
            diag["pair_base_undeclared"] += 1
        elif not curs:
            diag["pair_no_cursor"] += 1
        else:
            named.append((b, d, curs))
            diag["pair_multi_cursor"] += len(curs) > 1
    out = []
    for i, (b, d, curs) in enumerate(named):
        nxt = named[i + 1][0] if i + 1 < len(named) else 0x10000
        end = min(d[0] + d[1], nxt if d[2] == 1 else 0x10000)
        end = _const_end(b, end, d)
        if end > b:
            out.append(Region(b, end - b, d[2], curs))
        else:
            diag["pair_region_mut"] += 1
    diag["pair_objects"] += len(out)
    return out


def _const_end(b, end, d):
    """Where const data stops above ``b``: the first offset the declaration names ``mut``.

    A strided declaration is a record array, so a base inside it names one lane and the
    ``mut`` reading is per record offset; a flat one is tiled by the objects themselves."""
    rec = _record(d[1], d[2])
    if d[2] > 1:
        return end if (b - d[0]) % rec not in d[3] else b
    for off in range(b - d[0], end - d[0]):
        if off % rec in d[3]:
            return d[0] + off
    return end


def _cur_watch(prog, objs, at):
    """``([statement], {watch index: (cursor, walk rule)})``: the play stores to a cursor cell.

    The cursor rides the one ``eval_watch`` run everything else rides, so watching what
    the text already named costs no second pass."""
    cells = {c for o in objs for c in o.cursors}
    env, out, tags, seen = _prog_env(prog), [], {}, set()
    for s in _stmts(prog):
        cell = _base(s[1]) if s[0] == "st" else None
        if cell not in cells or id(s) in seen:
            continue
        seen.add(id(s))
        tags[at + len(out)] = (cell, _walk_of(s, env, cell))
        out.append(s)
    return out, tags


def _cur_value(rule, was, srcs, banks, mem0, objs):
    """``(value, source region)`` a cursor store leaves, or None where nothing names it.

    The text's own rule answers where it determines the store; otherwise the byte is the
    declared one at the cell the machine copied from — §4g's reading of the address bus."""
    if rule is not None and rule[0] == "set":
        return (rule[1], -1)
    if rule is not None:
        return None if was is None else ((was[0] + rule[1]) % rule[2], was[1])
    got = set()
    for s in srcs:
        d = _decl_of(s, banks)
        if d is not None and (s - d[0]) % _record(d[1], d[2]) not in d[3]:
            o = _object_at(objs, s)
            got.add((mem0[s], o.base if o is not None else d[0]))
    return got.pop() if len(got) == 1 else None


def _cur_states(tags, wat, objs, banks, mem0, nframes):
    """``[frame][cursor] -> {(value, source region)}``: the states each cursor passes.

    A cursor the text writes through an indexed address is a family of cells, so the
    states of the whole family stand for it and the read's own address picks."""
    fam = {c: {c} for o in objs for c in o.cursors}
    val = {c: (mem0[c], -1) for c in fam}
    out = []
    for f in range(nframes):
        seen = {c: {val[x] for x in xs if val.get(x) is not None} for c, xs in fam.items()}
        for i, cell, srcs in wat[f] if f < len(wat) else ():
            got = tags.get(i)
            if got is None or cell is None:
                continue
            c, rule = got
            fam[c].add(cell)
            val[cell] = st = _cur_value(rule, val.get(cell), srcs, banks, mem0, objs)
            if st is not None:
                seen.setdefault(c, set()).add(st)
        out.append(seen)
    return out


def _pair_verify(key, row, pairs, diag):
    """Price the split the cursor's own observed value would make on top of the pair.

    A verified read is one whose named cursor was holding the index the machine read at;
    the partition is **not** keyed on it, because §6 measures what that split costs."""
    if diag is None or not _paired(key):
        return
    cur, index = pairs.cursors or {}, row * key[4]
    hits = {g for c in key[6] for v, g in cur.get(c, ()) if v == index}
    diag["pair_cursor_" + ("verified" if hits else "unverified")] += 1
    diag["pair_cursor_two_sources"] += len(hits) > 1


def _pair_cost(lww, tabs, objs, mem0, told, diag):
    """Price the refusal the whole rule rests on: a row chosen to fit the byte.

    ``pair_fitted`` is every unclaimed write in a class the tree names a declaration for
    whose byte some named region holds *somewhere* — the population a search over the
    region's rows would claim, and which the machine's own read index refuses."""
    held = {}
    for cls, decls in tabs.items():
        held[cls] = {
            mem0[o.base + i]
            for o in objs
            for i in range(o.size)
            if any(d[0] <= o.base < d[0] + d[1] for d in decls)
        }
    for f, wr in enumerate(lww):
        for reg, (val, _srcs) in wr.items():
            if reg not in told[f] and val in held.get(_class_of(reg), ()):
                diag["pair_fitted"] += 1


def _object_at(objs, src):
    """The nearest region below ``src`` that holds it at one of its own rows, or None.

    The lanes of a record array overlap, so containment alone names the wrong one: the
    region must reach ``src`` at a whole number of its own records."""
    i = bisect.bisect_right(objs, src, key=lambda o: o.base)
    for o in reversed(objs[:i]):
        if src < o.base + o.size and (src - o.base) % o.stride == 0:
            return o
    return None


def _paired(key):
    """Is this stream key a region the program text names, rather than a whole declaration?"""
    return key is not None and len(key) > 6 and key[6] is not None


def _pair_census(items, diag):
    """Count the nodes and emits the pair partition splits out, against the ones it does not."""
    for key, n in items if diag is not None else ():
        if key[0] != "lane":
            continue
        tag = "pair" if key[6] else ("pair_unverified" if _paired(key) else "pair_unnamed")
        diag[tag + "_nodes"] += 1
        diag[tag + "_emits"] += n


# ---- 4i. the sequencer: a tick clock, a row cursor, and the table it rows ---------
Seq = namedtuple("Seq", "ticks cursors")


def _sequencer(prog, banks):
    """``Seq``: the tick clock and the row cursors, both read off the program text.

    A tick is a declared divisor (§4d) at the phase its own counter's post-init byte
    fixes; a cursor is a cell whose every writer the text names (``_walked``), seeded
    by the post-init image and stepped and wrapped by the text's own rule."""
    walk = _walked(prog)
    ticks = {
        (n, (int(prog.mem0[c]) - 1) % n) for c, ns in _reloads(prog, banks).items() for n in ns
    }
    curs = {}
    for cell, rules in walk.items():
        got = _arr_rule(rules)
        if got:
            curs[cell] = (int(prog.mem0[cell]) % got[1], got[0], got[1])
    return Seq(tuple(sorted(ticks)), curs)


def _beats(tags, wat, nframes):
    """``{cursor cell: counts}``: the frames the text's own step rule advanced a cursor.

    A cursor some writer reloads is refused outright — a ``RAMP`` walks and never
    resets — so what is left is a cell the text only steps, at the frames it stepped it."""
    out, bad = {}, set()
    for f in range(nframes):
        for i, cell, _srcs in wat[f] if f < len(wat) else ():
            got = tags.get(i)
            if got is None or cell is None or cell != got[0]:
                continue
            if got[1] is None or got[1][0] != "step":
                bad.add(cell)
            else:
                out.setdefault(cell, [0] * nframes)[f] += 1
    return {c: tuple(n) for c, n in out.items() if c not in bad}


def _rows_at(cur, beats, counts):
    """The rows a cursor stepped by ``beats`` holds at the reads ``counts`` names.

    The cursor emits once per step, so a read sees the value that frame's steps left;
    a read before the cursor has stepped at all sees nothing and refuses the stream."""
    seed, step, wrap = cur
    out, n = [], 0
    for f, c in enumerate(counts):
        n += beats[f] if f < len(beats) else 0
        if c and not n:
            return None
        out += [(seed + step * (n - 1)) % wrap] * c
    return out


def _chain_cell(key, counts, rows, ctx, diag):
    """``(cell, transfer, trigger)`` for the walked cursor whose steps are this row stream.

    The region names its cursors and the text names each cursor's seed, step and modulus,
    so the rows are *predicted* off the cursor's own beats; a stream the walk does not
    reproduce keeps its recovered run, as a sweep run whose step is unnamed does."""
    seq, beats = ctx
    cells = [c for c in key[6] if c in seq.cursors] if _paired(key) else []
    if not cells:
        diag["chain_cursor_not_walked"] += 1
        return None
    for cell in cells:
        if cell not in beats:
            diag["chain_cursor_reset"] += 1
        elif _rows_at(seq.cursors[cell], beats[cell], counts) == list(rows):
            diag["chain_rows_generated"] += len(rows)
            return (cell, ("RAMP",) + seq.cursors[cell], beats[cell])
    diag["chain_rows_unwalked"] += len(rows)
    return None


def _chain(streams, ctx, diag):
    """``{stream index: (cursor cell, transfer, trigger)}``: streams read at a generated row.

    Links 2 and 3 of the chain: the row a table is read at stops being the run the
    observation yielded and becomes a ``RAMP`` the program text seeds, steps and wraps,
    beaten by the cursor's own step statement rather than by the read."""
    out = {}
    for i, (counts, t, _r, ev, key) in enumerate(streams):
        if ev != "lane" or t[0] != "SELECT" or not t[2] or _generated(t[2]):
            continue
        got = _chain_cell(key, counts, t[2], ctx, diag)
        if got is not None:
            out[i] = got
    diag["chain_cursor_nodes"] += len(set(out.values()))
    return out


def _rowers(chained, edges, nodes):
    """Append one cursor ``RAMP`` per distinct chain and return where each node sits.

    A cursor several registers read is one node, as the editor's own graph has it: the
    beats are the cursor's own, so every reader of one cursor shares one row generator."""
    rowed = {}
    for key in chained.values():
        if rowed.setdefault(key, len(nodes)) == len(nodes):
            nodes.append(indexer(key[1], ("event", edges[key[2]])))
    return rowed


def _planed(i, st, chained, rowed, edges):
    """One stream's plane generator, read at its recovered run or at its cursor's node."""
    counts, t, reg, _ev, _key = st
    if i in chained:
        t = (t[0], t[1], ("node", rowed[chained[i]]))
    return Generator(t, ("event", edges[counts]), ("plane", reg))


def _attrition(seq, streams, chained, pairs, nodes, diag):
    """Per-tune presence of each link of the chain, so the binding one is named."""
    diag["chain_link1_tick"] = int(bool(seq.ticks))
    diag["chain_link2_cursor"] = int(bool(seq.cursors))
    diag["chain_link3_region"] = int(
        any(_paired(k) and any(c in seq.cursors for c in k[6]) for *_r, k in streams)
    )
    diag["chain_link4_rowed"] = int(bool(chained) or bool(pairs))
    diag["chain_link5_ticked"] = int(any(g.transfer[0] == "DIV" for g in nodes))
    diag["chain_whole"] = int(
        bool(seq.ticks)
        and any(
            g.transfer[0] == "DIV"
            and any(h.trigger == ("event", i) and h.route == INDEX for h in nodes)
            for i, g in enumerate(nodes)
        )
    )


# ---- 5. the law: the graph's projection is frameprog's ---------------------------
def oracle(prog, trace, nframes):
    """The frame projection the tracker must reproduce (frameprog, Gate FP-verified)."""
    return frameval.eval_fp(prog, trace, nframes)


def _observe(prog, trace, nframes, diag=None):
    """``(records, order-preserved writes, lww writes, state)``, with provenance.

    ONE machine run supplies everything the recovery reads: the projection ``oracle``
    defines, the cell each write loaded its byte from, the origin map at the accumulator
    statements (§4c), the arrangement's own reload and row walks (§4g) and every cursor
    the program text indexes a declared region at (§4h)."""
    diag = Counter() if diag is None else diag
    watch, cells, signs = _acc_sites(prog)
    sites, addrs = _arr_sites(prog, _prog_env(prog), _walked(prog), diag)
    astmts, tags = _arr_watch(prog, sites, len(watch))
    banks = _banks(prog)
    objs = _objects(prog, banks, diag)
    cstmts, ctags = _cur_watch(prog, objs, len(watch) + len(astmts))
    frames, srcs, wat = frameval.eval_watch(prog, trace, nframes, watch + astmts + cstmts)
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
    states = _arr_states(sites, tags, wat, prog.mem0, nframes)
    curs = _cur_states(ctags, wat, objs, banks, prog.mem0, nframes)
    return (
        framelog.canonical(frames),
        ords,
        lww,
        (
            _acc_pools(cells, wat),
            signs,
            (sites, _arr_classes(prog, addrs)),
            states,
            (objs, curs, _beats(ctags, wat, nframes)),
        ),
    )  # the arrangement and the cursors ride the same run: their walks are watched too


def lift(prog, frames=()):
    """Lift the tune-independent engine parameters from a frame program."""
    clocks = _clocks(prog)
    return Tracker(
        _pitch(prog, _freq_words(frames)),
        clocks,
        _tempo(clocks),
        _instruments(prog),
        _divisors(prog, _banks(prog)),
    )


def _graph(prog, pitch, frames, ords, lww, acc, diag=None):
    """``(graph, lanes)``: declared lanes and notes as generators, the rest RAW.

    A declared lane the store statement names outranks the note reading of the same
    byte; a detuned frame counts only as vibrato on the current note, or as a fresh
    exact anchor, and an excursion to an unrelated note stays residual."""
    banks = _banks(prog)
    tabs = _tree_tables(prog, banks)
    diag = Counter() if diag is None else diag
    pools, signs, arrs, states, (objs, curs, beats) = acc
    lanes = [[], [], []]
    anchor = [None, None, None]
    seqs = {r: [] for r in _FREQ_REGS}
    residual = []
    pre, post, ires = _instr_streams(prog, ords, tabs, banks, objs, curs, diag)
    lwws, declared = _lww_streams(lww, tabs, prog.mem0, objs, curs, diag)
    ramps, swept = _acc_streams(_accumulators(prog, signs), pools, banks, tabs, lww, prog.mem0)
    fields = [d | s for d, s in zip(declared, swept)]
    groups, assembled = _mask_streams(lww, _partitions(prog), tabs, prog.mem0, fields)
    claimed = [d | a for d, a in zip(fields, assembled)]
    sites = _rel_sites(prog, banks, diag)
    rels, related = _rel_streams(lww, sites, prog.mem0, claimed, diag, objs, curs)
    told = [c | r for c, r in zip(claimed, related)]
    _rel_cost(lww, sites, banks, prog.mem0, told, diag)
    _pair_cost(lww, tabs, objs, prog.mem0, told, diag)
    pairs, arranged = _arr_pairs(lww, arrs, states, banks, prog.mem0, told, diag)
    _arr_cost(lww, ords, arrs[1], told, arranged, diag)
    for f, rec in enumerate(frames):
        gen, done = {}, told[f] | arranged[f]
        diag["rel_ord_section"] += sum(
            1 for v in range(3) for w in ires[v][f] if _class_of(w[0]) in sites
        )
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
    seq = _sequencer(prog, banks)
    chained = _chain(streams, (seq, beats), diag)
    edges = {}
    for counts, *_rest in streams + groups + rels + pairs:
        edges.setdefault(counts, len(edges))
    for key in chained.values():
        edges.setdefault(key[2], len(edges))
    nodes = [_clock_node(c, seq.ticks) for c in edges]
    rowed = _rowers(chained, edges, nodes)
    fired = [_planed(i, s, chained, rowed, edges) for i, s in enumerate(streams[: len(pre)])]
    nodes += fired + [raw(residual)]
    nodes += [
        _planed(i + len(pre), s, chained, rowed, edges) for i, s in enumerate(streams[len(pre) :])
    ]
    nodes += [Generator(t, ("event", edges[c]), plane(r, m)) for c, ps, r in groups for t, m in ps]
    nodes += [lookup(seqs[r], FRAME, r) for r in _FREQ_REGS if any(v is not None for v in seqs[r])]
    for counts, t, reg, op, base in rels:  # absolutes settle a register, relatives follow
        if base[0] == "gen":
            nodes.append(Generator(base[1], ("event", edges[counts]), plane(reg)))
            base = ("node", len(nodes) - 1)
        nodes.append(Generator(t, ("event", edges[counts]), relative(reg, op, base)))
    for counts, walk, table, reg in pairs:  # the row generator, then the pattern it rows
        nodes.append(indexer(walk, ("event", edges[counts])))
        nodes.append(select(table, ("node", len(nodes) - 1), ("event", edges[counts]), reg))
    _attrition(seq, streams, chained, pairs, nodes, diag)
    return Graph(nodes, freq_table=pitch, classes=_classes(streams, groups, rels, pairs)), lanes


def render(prog, trace, nframes, diag=None):
    """``(rendered, oracle, Coverage, lanes)`` for the frame program's projection.

    Accepted-note freq entries and explained ADSR writes are interpreted
    generators; everything else is an explicit RAW residual, so a ``gate`` PASS
    certifies the partition is complete. ``diag`` collects the refusal histogram."""
    diag = Counter() if diag is None else diag
    gt, ords, lww, acc = _observe(prog, trace, nframes, diag)
    graph, lanes = _graph(prog, _pitch(prog, _freq_words(gt)), gt, ords, lww, acc, diag)
    recs, interp, rawn, trig = _run(graph, nframes)
    return recs, gt, _coverage(interp, rawn, graph.classes, trig), lanes


def gate(prog, trace, nframes):
    """Gate verdict: None if the generator graph reproduces frameprog's projection."""
    rendered, gt, _cov, _lanes = render(prog, trace, nframes)
    return framelog.diff(rendered, gt)
