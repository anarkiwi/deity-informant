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
_SPAN = 0x100  # what one 6502 index register reaches from the base a load names
_CLASSES = ("lane", "gate", "imm", "ramp", "seed", "mask", "rel", "arr")
_REL = {"ADD": lambda b, d: b + d, "SUB": lambda b, d: b - d, "XOR": lambda b, d: b ^ d}


class TrackerError(ValueError):
    """The graph is not evaluable (unknown transfer, dangling trigger)."""


# ---- 1. the one primitive: a triggered generator ---------------------------------
def plane(reg, mask=_FULL, at=None):
    """A SID register plane route, over the whole byte or the bits ``mask`` names.

    ``at`` is ``("node", k)`` where the register the emit lands in is ``reg`` plus the
    offset generator ``k`` holds — a driver whose store is ``sta $d402,y`` writes one
    object's value to whichever voice ``y`` names, and the offsets are a declared table."""
    if at is not None:
        return ("plane", reg, mask, at)
    return ("plane", reg) if mask == _FULL else ("plane", reg, mask)


def relative(reg, op, base, mask=_FULL):
    """A relative plane route: the emit is a delta ``op`` combines with ``base``.

    ``base`` is ``("prev",)`` (the plane's own previously emitted value), ``("node", i)``
    (generator ``i``'s current value) or ``("const", c)`` (a declared base byte)."""
    return ("rel", reg, mask, op, base)


def pair(reg_lo, reg_hi, mask_hi=_FULL, at=None):
    """A register-pair plane route: one emit writes its low byte and its high byte.

    The one route whose value is wider than a register — a 16-bit accumulator's, which
    all three editors keep. ``mask_hi`` names the bits the high register takes, and
    ``at`` offsets both halves by the value a generator holds."""
    return ("pair", reg_lo, reg_hi, mask_hi, at)


INDEX = ("index",)  # a value-carrying edge: the emit is another generator's row index


def hold(j, seed=None, at=0):
    """Emit what generator ``j`` held after its ``at``-th emit of this frame, else ``seed``.

    What a persistent object needs — one generator carries the state and every reader
    takes what it held where the machine's own order placed that read."""
    return ("HOLD", j, seed, at)


def _at_of(route):
    """The generator that offsets this route's register, or None where none does."""
    if route[0] == "pair":
        return route[4] if len(route) > 4 else None
    return route[3] if route[0] == "plane" and len(route) > 3 else None


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
    count runs out, and an unstaged cell's zero yields the default ``n-1``.

    ``n`` may be ``("node", j)`` — a divisor generator ``j`` emits, reloaded at every
    tick. A period a constant cannot name is what a tracker row's duration field is, and
    ``phase`` is then the ticks the first row still owes."""
    if isinstance(n, tuple):
        return Generator(("DIV", n, phase or 0), trigger, ("fire",))
    return Generator(
        ("DIV", n, (n - 1 if phase is None else phase) % max(1, n)), trigger, ("fire",)
    )


def lookup(seq, trigger, reg, mask=_FULL):
    """Emit ``seq[i]`` into a plane: a ``SELECT`` that recovered no row."""
    return Generator(("SELECT", tuple(seq), ()), trigger, plane(reg, mask))


def ramp(seed, step, bound, trigger, reg, mask=_FULL, turn=()):
    """Emit ``seed + step*count`` into a plane, wrapped at ``bound``, turned at ``turn``.

    ``turn`` is ``()`` for a ramp that only wraps, else ``(low, high)`` in high-register
    units: the step goes negative where the new emit's high register reaches ``high`` and
    positive where it reaches ``low``, which is the one 6502 sweep a wrap cannot say.
    ``seed`` may be ``("node", j)``: the value ``j`` holds, taken afresh every time ``j``
    emits, which is what a note-on reloading an accumulator does (§4m)."""
    return Generator(("RAMP", seed, step, bound, turn), trigger, plane(reg, mask))


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
    """Generator nodes, the distinguished pitch table, the song, and the evidence classes.

    ``charts`` is the arrangement (§4j): the terminator-bounded regions a proven pointer
    names, their rows walked by the program text's own cursor steps. It is declared data
    at program-text offsets, so it carries no observation at all."""

    def __init__(self, nodes, freq_table=None, classes=None, charts=()):
        self.nodes = list(nodes)
        self.freq_table = freq_table
        self.classes = classes
        self.charts = tuple(charts)

    def raw_index(self):
        """Index of the RAW floor node, or None."""
        for i, g in enumerate(self.nodes):
            if g.transfer[0] == "RAW":
                return i
        return None


class _Held:
    """What each node holds at the ``t``-th edge of a frame: its own ``t``-th emit, else its last.

    ``_run`` settles a frame's generators in dependency order — index nodes are earlier
    than the reads they feed (``_index_ok``) — but a driver can cut several rows inside one
    frame, and a node-major pass would then show every read of that frame the *last* row.
    Pairing an emit with the edge that produced it is what makes the row generated rather
    than observed; the last value stands where a node did not fire that edge, which is what
    a cell the machine did not rewrite holds."""

    __slots__ = ("last", "seq", "t", "start")

    def __init__(self):
        self.last, self.seq, self.t, self.start = {}, {}, 0, {}

    def put(self, i, v):
        """Record node ``i``'s emit for this edge."""
        self.last[i] = v
        self.seq.setdefault(i, []).append(v)

    def get(self, i, default=None):
        """Node ``i``'s value as of edge ``t``."""
        got = self.seq.get(i)
        return got[self.t] if got is not None and self.t < len(got) else self.last.get(i, default)

    def edge(self, t):
        """Read the frame at its ``t``-th edge."""
        self.t = t
        return self

    def frame(self):
        """Start a frame: this frame's emits are not the last one's, and the last still stands."""
        self.seq.clear()
        self.start = dict(self.last)
        self.t = 0

    def after(self, i, k, default=None):
        """What node ``i`` held after its ``k``-th emit of this frame: an object's own state."""
        got = self.seq.get(i, ())
        if not k:
            return self.start.get(i, default)
        return got[k - 1] if k <= len(got) else self.last.get(i, default)


class _Fires:
    """Trigger propagation with per-node input ordinals, one instance per run.

    A ``DIV`` emits one tick per ``n`` *input* triggers (§2): clocked by the frame it
    divides the frame number, clocked by an event it divides the ticks it has received,
    which is what lets one divider clock another. The consumer index keeps a frame linear.

    A divisor may itself be a generator's emit (``Node(j)``) — the trigger domain's
    counterpart of the value domain's generated row. A period no constant can name is
    exactly what a tracker row's own duration field is, and the divider reads the value
    node ``j`` was holding when it last ran out, which is the byte the machine reloaded."""

    def __init__(self, nodes, held=None):
        self.nodes = nodes
        self.held = held
        self.seen = [0] * len(nodes)  # input ticks a node has received so far
        self.left = {}  # ticks still owed, for a divider whose divisor is an emit
        self.cons = {}
        for j, h in enumerate(nodes):
            if h.trigger != FRAME:
                self.cons.setdefault(h.trigger[1], []).append(j)

    def _period(self, spec):
        """The divisor a ``DIV`` divides by: a constant, or the value a generator holds."""
        if not isinstance(spec, tuple):
            return max(1, spec)
        got = None if self.held is None else self.held.last.get(spec[1])
        return None if got is None or got < 1 else int(got)

    def _counted(self, i, g, got):
        """Ticks a generated-divisor ``DIV`` emits over ``got`` input triggers.

        The divisor is not a modulus here — it is reloaded at every tick, so the divider
        is a countdown and a row that lasts one tick and one that lasts sixteen are the
        same generator with a different reload. The reload is the value the tick's own
        reader emits, which the frame settles after the triggers, so a tick whose reload
        is not in yet takes it at the next input trigger: that is the player's own order,
        the counter running out first and the row it then reads supplying the next one."""
        left = self.left.get(i, (g.transfer[2] or 0) + 1)
        out = 0
        for _x in range(got):
            if left is None:
                left = self._period(g.transfer[1])
                if left is None:
                    break
            left -= 1
            if left <= 0:
                out += 1
                left = None
        self.left[i] = left
        return out

    def _ticks(self, i, g, frame, got):
        """Edges fire-routed node ``i`` emits at ``frame``, given ``got`` input ticks."""
        kind = g.transfer[0]
        if kind == "EDGE":
            seq = g.transfer[1]
            return seq[frame] if frame < len(seq) else 0
        if kind == "DIV":
            if isinstance(g.transfer[1], tuple):
                return self._counted(i, g, got)
            n = max(1, g.transfer[1])
            p = (g.transfer[2] if len(g.transfer) > 2 else n - 1) % n
            if g.trigger == FRAME:
                return 1 if frame % n == p else 0
            t = self.seen[i]
            self.seen[i] = t + got
            return sum(1 for x in range(t, t + got) if x % n == p)
        raise TrackerError("transfer %r has no edge emit" % (kind,))

    def step(self, frame):
        """``(fires, ticks)``: triggers per node, and edges each fire node emitted."""
        nodes = self.nodes
        fires = [1 if g.trigger == FRAME else 0 for g in nodes]
        ticks = [0] * len(nodes)
        for i, g in enumerate(nodes):
            if g.route[0] != "fire" or not fires[i]:
                continue
            ticks[i] = n = self._ticks(i, g, frame, fires[i])
            for j in self.cons.get(i, ()) if n else ():
                fires[j] += n
        return fires, ticks


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


def _turned(transfer, count, state=None):
    """``(count, value, direction)`` of a turning ``RAMP``, stepped on from ``state``.

    The direction turns where the new emit's high register meets a bound, exactly as
    the 6502 compares it. Carrying the state keeps a run linear in its own length: a
    turn is history-dependent, so recomputing from the seed each fire is quadratic."""
    _k, seed, step, bound, turn = transfer
    low, high = turn
    mag = abs(step)
    if state is None or state[0] > count:
        state = (1, seed % bound, 1 if step >= 0 else -1)
    n, v, d = state
    while n < count:
        v = (v + d * mag) % bound
        top = v >> 8
        if d > 0 and top == high:
            d = -1
        elif d < 0 and top == low:
            d = 1
        n += 1
    return (n, v, d)


def _emit(g, count, cur=()):
    """Value a plane-routed generator emits on its ``count``-th trigger."""
    kind = g.transfer[0]
    if kind == "HOLD":
        return cur.after(g.transfer[1], g.transfer[3], g.transfer[2]) if cur else g.transfer[2]
    if kind == "SELECT":
        _k, table, rows = g.transfer
        if not table:
            return None
        i = _row(rows, count, cur or {}) if rows else (count - 1) % len(table)
        return table[i] if i is not None and 0 <= i < len(table) else None
    if kind == "RAMP":
        _k, seed, step, bound, turn = g.transfer
        if turn:
            return _turned(g.transfer, count)[1]
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


def _divisor_ok(nodes, i, g):
    """Refuse a generated divisor no ``index`` node supplies.

    The divisor is a value, so it comes from an ``index`` node exactly as a row does; the
    divider reads what that node last emitted, which is the byte the machine reloaded, so
    the source may be *any* index node and not only an earlier one — a row's duration and
    the divider that counts it name each other, as a player's counter and its reload do."""
    n = g.transfer[1]
    if g.transfer[0] != "DIV" or not isinstance(n, tuple):
        return ()
    if n[0] != "node" or not 0 <= n[1] < len(nodes) or nodes[n[1]].route != INDEX:
        raise TrackerError("divisor %r on node %d is not an index node" % (n, i))
    return (n[1],)


def _seed_ok(nodes, i, g):
    """Refuse a generated ramp seed no earlier ``index`` node supplies.

    The seed is a value, so it comes from an ``index`` node as a row does; it must be
    *earlier*, because the ramp re-seeds on the edge that reloads it and node order is
    what settles a frame."""
    s = g.transfer[1] if g.transfer[0] == "RAMP" else None
    if not isinstance(s, tuple):
        return ()
    if s[0] != "node" or not 0 <= s[1] < i or nodes[s[1]].route != INDEX:
        raise TrackerError("ramp seed %r on node %d is not an earlier index node" % (s, i))
    if g.transfer[4]:
        raise TrackerError("ramp seed %r on node %d turns: a reload has no direction" % (s, i))
    return (s[1],)


def _offsets(nodes, at):
    """The register offsets an ``at`` generator can hold: the rows of its own table."""
    g = nodes[at[1]]
    return sorted(set(g.transfer[1])) if g.transfer[0] == "SELECT" else (0,)


def _at_ok(nodes, i, g):
    """Refuse a register offset no earlier ``index`` node settles, and return its own."""
    at = _at_of(g.route)
    if at is None:
        return ()
    if at[0] != "node" or not 0 <= at[1] < i or nodes[at[1]].route != INDEX:
        raise TrackerError("route offset %r on node %d is not an earlier index node" % (at, i))
    return (at[1],)


def _index_ok(nodes, i, g):
    """Refuse a generated row index the graph cannot supply before it is read.

    The sources must be earlier ``index``-routed nodes, so the value edge runs the same
    way node order already runs and no cycle can form. An ``index`` route with no reader
    is refused too: a generator that neither writes nor is read is dead."""
    rows = g.transfer[2] if g.transfer[0] == "SELECT" else ()
    if _generated(rows):
        rel = rows[0] == "rel"
        _rel_ok(nodes, i, rows[1] if rel else None, rows[2:] if rel else (rows,), INDEX)
    if g.transfer[0] == "HOLD" and not 0 <= g.transfer[1] < i:
        raise TrackerError("held value %r on node %d is not an earlier node" % (g.transfer, i))
    _divisor_ok(nodes, i, g)
    _seed_ok(nodes, i, g)
    _at_ok(nodes, i, g)
    if g.route == INDEX and not any(
        (h.transfer[0] == "SELECT" and i in _sources(h.transfer[2]))
        or (h.transfer[0] == "HOLD" and h.transfer[1] == i)
        or i in _divisor_ok(nodes, j, h)
        or i in _seed_ok(nodes, j, h)
        or i in _at_ok(nodes, j, h)
        for j, h in enumerate(nodes)
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
        if g.route[0] not in ("plane", "fire", "raw", "rel", "index", "pair"):
            raise TrackerError("unknown route %r" % (g.route,))
        if g.transfer[0] == "RAMP" and (
            len(g.transfer) != 5
            or (g.transfer[4] and (len(g.transfer[4]) != 2 or not g.transfer[3]))
        ):
            raise TrackerError("ramp %r names no turn and no wrap to turn in" % (g.transfer,))
        _index_ok(nodes, i, g)
        at = _at_of(g.route)
        offs = _offsets(nodes, at) if at is not None else (0,)
        if g.route[0] == "pair":  # each half owns its whole byte
            if not 0 < g.route[3] <= _FULL:
                raise TrackerError("route mask %r owns no bit" % (g.route,))
            for reg in [r + o for r in g.route[1:3] for o in offs]:
                for other in owned.setdefault(reg, set()):
                    if other != _FULL:
                        raise TrackerError("routes $%02X and $FF overlap on $%02X" % (other, reg))
                owned[reg].add(_FULL)
            continue
        if not _is_plane(g.route):
            continue
        m = _mask_of(g.route)
        if not 0 < m <= _FULL:
            raise TrackerError("route mask %r owns no bit" % (g.route,))
        for reg in [g.route[1] + o for o in offs]:
            for other in owned.setdefault(reg, set()):
                if other != m and other & m:
                    raise TrackerError("routes $%02X and $%02X overlap on $%02X" % (other, m, reg))
            owned[reg].add(m)
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


def _writes_of(route):
    """Register writes one emit of this route makes: a pair writes both halves, a plane one."""
    return 2 if route[0] == "pair" else 1


def _pair_writes(route, v, off=0):
    """The two byte writes one pair-routed emit makes: its low half, its masked high."""
    return [(route[1] + off, v & 0xFF), (route[2] + off, (v >> 8) & route[3])]


def _combine(route, delta, prev, cur):
    """The byte a relative route writes: its delta, combined with the named base.

    A base the graph has not settled yet emits nothing, so a mis-built relative stream
    drops a write and the law says so rather than inventing a base."""
    _k, reg, mask, op, base = route
    val = _named(base, cur, prev.get(reg))
    return None if delta is None or val is None else _REL[op](val & mask, delta) & 0xFF


def _reseeded(g, i, count, cur, state, counts):
    """A ramp whose seed is generator ``j``'s value: it restarts every time ``j`` emits.

    ``j``'s emit count *at this edge* is the epoch, so the reload is an event and not a
    frame number, and a ramp whose seed has not arrived yet emits nothing rather than a
    guess."""
    _k, seed, step, bound, _turn = g.transfer
    j = seed[1]
    got = cur.seq.get(j, ()) if cur else ()
    epoch = counts[j] - len(got) + min(cur.t + 1, len(got)) if cur else 0
    was = state.get(i)
    if was is None or was[0] != epoch:
        base = cur.get(j) if cur else None
        if base is None:
            return None
        was = (epoch, base, count)
    state[i] = was
    v = was[1] + step * (count - was[2])
    return v % bound if bound else v


def _value(g, i, count, cur, state, counts=()):
    """One generator's emit, carrying a turning or reloaded ``RAMP``'s own state forward.

    Trigger counts are monotonic within a run, so one step per fire replaces the
    recomputation from the seed a history-dependent transfer would otherwise need."""
    if g.transfer[0] == "RAMP":
        if isinstance(g.transfer[1], tuple):
            return _reseeded(g, i, count, cur, state, counts)
        if g.transfer[4]:
            state[i] = st = _turned(g.transfer, count, state.get(i))
            return st[1]
    return _emit(g, count, cur)


def _run(graph, nframes, trace=None):
    """``(canonical records, interpreted emits, raw emits, fire census)``.

    Refinement removes a *write* from RAW, so node order fixes the interleaving of a
    split register (§5); a masked generator latches its bits and the last to fire writes
    the byte (§4e). ``trace`` is ``(per-node acts, per-frame writes)``, filled if given."""
    nodes = graph.nodes
    _check(nodes)
    acts = None if trace is None else trace[0]
    counts = [0] * len(nodes)
    interp, rawn, trig = {}, {}, {}
    parts = _masked(nodes)
    held = {reg: {} for reg in parts}
    prev, cur, state = {}, _Held(), {}
    firing = _Fires(nodes, cur)
    out = []
    for f in range(nframes):
        cur.frame()
        fires, ticks = firing.step(f)
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
                    if acts is not None:
                        acts[i].append((f, reg, val))
            elif g.route == INDEX:  # a value edge: it indexes, it does not write
                for t in range(fires[i]):
                    counts[i] += 1
                    v = _value(g, i, counts[i], cur.edge(t), state, counts)
                    cur.put(i, v)
                    if acts is not None:
                        acts[i].append((f, v))
            elif g.route[0] == "pair":  # one emit, two registers: a 16-bit value
                at = _at_of(g.route)
                for t in range(fires[i]):
                    counts[i] += 1
                    v = _value(g, i, counts[i], cur.edge(t), state, counts)
                    cur.put(i, v)
                    off = 0 if at is None else cur.edge(t).get(at[1])
                    got = () if v is None or off is None else _pair_writes(g.route, v, off)
                    writes += got
                    for reg, b in got:
                        interp[reg] = interp.get(reg, 0) + 1
                        prev[reg] = b
                    if acts is not None:
                        acts[i].extend((f, reg, b) for reg, b in got)
            elif _is_plane(g.route):
                at = _at_of(g.route)
                for t in range(fires[i]):  # one emit per trigger, in order
                    counts[i] += 1
                    v = _value(g, i, counts[i], cur.edge(t), state, counts)
                    cur.put(i, v)
                    if g.route[0] == "rel":
                        v = _combine(g.route, v, prev, cur)
                    if i in eaten:  # a base generator supplies a value, it does not write
                        continue
                    off = 0 if at is None else cur.edge(t).get(at[1])
                    if off is None:
                        continue
                    reg = g.route[1] + off
                    v = _assemble(g, v, held.get(reg), i == last.get(reg))
                    if v is None:
                        continue
                    interp[reg] = interp.get(reg, 0) + 1
                    prev[reg] = v & 0xFF
                    writes.append((reg, v & 0xFF))
                    if acts is not None:
                        acts[i].append((f, reg, v & 0xFF))
            else:
                counts[i] += fires[i]
                if g.route[0] == "fire":  # the trigger domain's own census
                    k = g.transfer[0]
                    trig[k] = trig.get(k, 0) + ticks[i]
                    if acts is not None and ticks[i]:
                        acts[i].append((f, ticks[i]))
        out.append(writes)
        if trace is not None:
            trace[1].append(writes)
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


def _spilled(w, tabs, banks, mem0, pairs=Pairs(), diag=None):
    """``(stream key, row)`` for a read that ran past its own table's declared end.

    The store statement names the base and one 6502 index reaches ``_SPAN`` bytes from
    it, so a row past the declared extent is that statement's own read still and the byte
    is the declaration the address landed in. A cell no named base reaches is no source."""
    reg, val, srcs = w
    bases = [b[0] for b in tabs.get(_class_of(reg), ())]
    hit = tuple(s for s in srcs if any(b < s < b + _SPAN for b in bases))
    return _lane_key((reg, val, hit), banks, mem0, pairs, diag) if hit else None


def _declared_at(w, tabs, banks, mem0, pairs=Pairs(), diag=None):
    """``(stream key, row)`` for a write whose read cell a declaration the text names holds.

    The declarations the store statement names come first, then the one an index past
    their end reaches; one reading, so every stage claims the same writes."""
    got = _lane_key(w, tabs.get(_class_of(w[0]), ()), mem0, pairs, diag)
    return got if got is not None else _spilled(w, tabs, banks, mem0, pairs, diag)


def _classify(w, tabs, banks, imm, mem0, held, pairs=Pairs(), diag=None):
    """``(stream key, row)`` for one write: a lane byte, its gate image, or a constant.

    The declarations the tree names come first and every bank is the fallback for a value
    the tree cannot express. A ctrl write carrying no cell of its own is the gate bit over
    the lane byte at the voice's held row: only bit 0 moves."""
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
                lane = rows.get(got[0])
                if lane is None:  # built on first use: the default is one row per frame
                    lane = rows[got[0]] = [[] for _g in seq]
                lane[f].append(got[1])
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


def _classes(streams, groups=(), rels=(), pairs=(), sweeps=(), songs=(), held=(), notes=()):
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
    for grp in held:  # an object's emits: the step's own, and the reads of what it holds
        for node in ([grp.step] if grp.step else []) + list(grp.reads):
            cls = out.setdefault(_plane_of(node.lo), dict.fromkeys(_CLASSES, 0))
            cls[node.ev] += sum(node.counts) * (2 if node.hi is not None else 1)
    for obj in notes:  # an object the note reloads: declared seed, declared walk (§4m)
        for counts, _at, reg in obj.holds:
            out.setdefault(_plane_of(reg), dict.fromkeys(_CLASSES, 0))["ramp"] += sum(counts)
    for counts, _t, route, reg in sweeps:  # one pair emit is two register writes
        cls = out.setdefault(_plane_of(reg), dict.fromkeys(_CLASSES, 0))
        cls["seed"] += _writes_of(route)
        cls["ramp"] += _writes_of(route) * (sum(counts) - 1)
    for i, (counts, t, reg, ev, _key) in enumerate(streams):
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
            cls["arr" if i in songs else "lane"] += len(t[2])
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
def _lww_streams(lww, tabs, mem0, objs=(), curs=(), diag=None, done=(), banks=()):
    """``(streams, explained)``: declared-lane SELECT nodes for the freq/pw/filter planes.

    The store statement names the declaration and the read cell recovers the row, so
    the emitted byte is declared data at the index the play code used; the search over
    every bank is `_classify`'s own fallback, for a row that ran past the end of the named
    table and landed in another declaration. The plane is last-write-wins."""
    streams, explained = {}, [set() for _f in lww]
    for f, wr in enumerate(lww):
        pairs = _pair_at(objs, curs, f)
        for reg in sorted(wr):
            if f < len(done) and reg in done[f]:
                continue  # an object owns every read of its own cell, steps included (§4l)
            val, srcs = wr[reg]
            got = _declared_at((reg, val, srcs), tabs, banks, mem0, pairs, diag)
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
_LOW = (2, 0x15)  # the low half of the two 16-bit SID planes an accumulator drives
Acc = namedtuple("Acc", "cells wraps turn masks signs")


def _def_stmt(expr, env, defs, fallback):
    """The statement whose value expression ``expr`` resolves to through the locals."""
    seen, out = set(), fallback
    while isinstance(expr, tuple) and expr[0] == "loc" and expr[1] not in seen:
        seen.add(expr[1])
        if expr[1] not in env:
            break
        out, expr = defs[expr[1]], env[expr[1]]
    return out


def _staged(prog):
    """``{cell: [statement]}``: every non-SID store, by the cell it writes."""
    out = {}
    for s in _stmts(prog):
        if s[0] == "st" and _sid_class(s[1]) is None:
            out.setdefault(_base(s[1]), []).append(s)
    return out


def _and_imm(root):
    """The one ``AND``-immediate a value expression applies to a term, else None."""
    if isinstance(root, tuple) and root[0] == "op" and root[1] == "INT_AND" and len(root[2]) == 2:
        cs = [k for k in root[2] if isinstance(k, tuple) and k[0] == "const"]
        if len(cs) == 1:
            return cs[0][1] & 0xFF
    return None


def _steps_own(s, env, wide=None, staged=()):
    """``[(sign, root, core, wrap, statement, held)]``: every way a store steps its own cell.

    The value is followed through the locals it names *and* through the staging cells it
    is copied from, so an accumulator whose arithmetic happens in a scratch byte is one
    too; the statement returned is where that arithmetic is, which is what the origin map
    answers for. A staged store's ``AND``-immediate is that byte's own width — the one
    place a 6502 names how wide an accumulator's high half is."""
    cell, out, seen = _base(s[1]), [], set()
    stack = [(_resolve(s[2], env), s, env, False)]
    while stack:
        root, at, e, hop = stack.pop()
        if not isinstance(root, tuple):
            continue
        if root[0] == "mem":
            c = _base(root[1])
            if wide is not None and c not in seen:
                seen.add(c)
                stack += [(_resolve(t[2], wide), t, wide, True) for t in staged.get(c, ())]
            continue
        core, wrap = _unmask(root, e) if hop else (root, 0x100)
        if not (isinstance(core, tuple) and core[0] == "op" and core[1] in ("INT_ADD", "INT_SUB")):
            continue
        held = [t for t in core[2] if _read_base(t, e) == cell]
        if not held or (core[1] == "INT_SUB" and core[2][0] not in held):
            continue
        out.append((-1 if core[1] == "INT_SUB" else 1, root, core, wrap, at, tuple(held)))
    return out


def _acc_sites(prog):
    """``([statement to watch], [cells per statement], {cell: [root]})``: the accumulators.

    Watched is the statement the arithmetic happens in — the store, the assignment its
    value resolves through, or the staging cell it is copied from, since a store of a bare
    local or of a scratch byte carries no origin at all. Watching is by identity, so one
    statement two stores share answers for both, and a cell stepped both ways keeps both
    signs: one store adds and the other subtracts, and the first seen fixes neither."""
    watch, cells, at, roots, arms = [], [], {}, {}, {}
    wide, staged = _prog_env(prog), _staged(prog)
    for proc in prog.procs:
        env, defs = {}, {}
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                env[s[1]], defs[s[1]] = s[2], s
                continue
            if s[0] != "st" or _sid_class(s[1]) is not None:
                continue
            for got in _steps_own(s, env, wide, staged):
                w = _def_stmt(s[2], env, defs, s) if got[4] is s else got[4]
                i = at.setdefault(id(w), len(watch))
                if i == len(watch):
                    watch.append(w)
                    cells.append(set())
                cells[i].add(_base(s[1]))
                roots.setdefault(_base(s[1]), []).append(got)
                arms.setdefault(i, (_base(s[1]), got))
    return watch, cells, roots, arms


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


def _reads_cell(expr, env, cell, depth=3):
    """Does this expression read ``cell``, through the locals and the widenings it names?"""
    root = _resolve(expr, env)
    if not isinstance(root, tuple) or depth <= 0:
        return False
    if root[0] == "mem":
        return _base(root[1]) == cell
    return root[0] == "op" and any(_reads_cell(c, env, cell, depth - 1) for c in root[2])


def _carries(root, env, cell):
    """Does this store's value take the carry out of ``cell``'s own arithmetic?

    That is what makes two byte cells one 16-bit accumulator, and it is the program
    text saying so rather than the two addresses happening to be adjacent. A 6502
    borrow is the same statement written as a comparison, so both forms count."""
    for x in _sub_exprs(root, []):
        if x[0] == "op" and x[1] in ("INT_CARRY", "INT_LESSEQUAL"):
            if any(_reads_cell(k, env, cell) for k in x[2]):
                return True
    return False


def _step_mask(core, held, env, staged):
    """The mask the text applies to the step byte: the ``AND``-immediate its staging store
    names, where every store to that cell names the same one, else the whole byte."""
    for t in core[2]:
        if t in held:
            continue
        root = _resolve(t, env)
        if not (isinstance(root, tuple) and root[0] == "mem"):
            continue
        ms = {_and_imm(_resolve(w[2], env)) for w in staged.get(_base(root[1]), ())}
        if len(ms) == 1 and None not in ms:
            return ms.pop()
    return _FULL


def _turn_of(conds, roots):
    """``{sign: immediate}``: the byte the text compares a stepped value against, where the
    equal arm steps a direction cell by one. Both bounds are program text, and a bound
    fitted to the emitted values is refused for the reason a fitted step is."""
    out = {}
    for cond, env, arms in conds:
        got = _zero_test(cond)
        sign = None if got is None else roots.get(_resolve(got[0], env))
        if sign is None:
            continue
        arm = arms[0 if got[2] else 1]
        if any(
            s[0] == "st" and _step(s[2], env, _base(s[1])) is not None for s in _in_order(list(arm))
        ):
            out.setdefault(sign, got[1])
    return out


def _acc_of(roots, lo, hi, ctx):
    """The ``Acc`` a low cell and its optional carry-taking high cell describe.

    ``masks`` is every ``AND``-immediate the text applies to this accumulator's step byte:
    one store can mask the byte it adds and another take it whole, and the step is a
    declared byte under one of them."""
    wide, conds, staged = ctx
    turn = _turn_of(conds, {r[1]: r[0] for r in roots[hi]}) if hi is not None else {}
    return Acc(
        (lo,) if hi is None else (lo, hi),
        (max(r[3] for r in roots[lo]),)
        + ((max(r[3] for r in roots[hi]),) if hi is not None else ()),
        (turn[-1], turn[1]) if len(turn) == 2 else (),
        frozenset(_step_mask(r[2], r[5], wide, staged) for r in roots[lo]),
        frozenset(r[0] for r in roots[lo]),
    )


def _reached(expr, env, origins, want, depth=4):
    """The cells of ``want`` this value reaches, at the fewest staging hops any of them takes.

    A byte staged one hop away is what the store reads; a cell some *other* staging cell
    further up also touches is not what it named, and refusing on that ambiguity refuses
    accumulators the shallow reading resolves outright."""
    level, seen = [expr], set()
    for _h in range(depth):
        got, nxt, stack = set(), [], list(level)
        while stack:
            x = stack.pop()
            if not isinstance(x, tuple) or x in seen:
                continue
            seen.add(x)
            if x[0] == "op":
                stack.extend(x[2])
            elif x[0] == "mem":
                b = _base(x[1])
                if b in want:
                    got.add(b)
                nxt.extend(origins.get(b, ()))
            elif x[0] == "loc":
                nxt.extend(env.get(x[1], ()))
        if got:
            return got
        level = nxt
    return set()


def _paired_cells(roots, wide):
    """``{low cell: high cell}``: the byte above a cell, where the text takes its carry.

    A 16-bit cell is a byte and the byte above it, and what says the two are one number is
    the high store taking the carry out of the low one's own arithmetic — not the two
    addresses happening to be adjacent, and not the values they hold."""
    return {
        lo: lo + 1
        for lo in roots
        if lo >= 2 and lo + 1 in roots and any(_carries(r[1], wide, lo) for r in roots[lo + 1])
    }


def _accumulators(prog, roots):
    """``{register class: (Acc, half)}``: the accumulator a store reads, and which byte of it.

    A pw or cutoff store whose value reaches exactly one stepped cell is that
    accumulator's output; two accumulators reaching one store refuse it, unless the two
    are the halves of one 16-bit cell — the high byte takes the low byte's own carry, and
    then the plane's own low register is the low half."""
    staged, out, stepped = _staged(prog), {}, set(roots)
    ctx = (_prog_env(prog), _conditions(prog), staged)
    origins = {c: [s[2] for s in ss] for c, ss in staged.items()}
    high = _paired_cells(roots, ctx[0])
    accs = {lo: _acc_of(roots, lo, high.get(lo), ctx) for lo in stepped - set(high.values())}
    group = {c: a for a in accs.values() for c in a.cells}
    for proc in prog.procs:
        envl = {}
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                envl.setdefault(s[1], []).append(s[2])
                continue
            cls = _sid_class(s[1]) if s[0] == "st" else None
            if cls is None or not (_PLANE.get(cls) == "pw" or cls in _CUTOFF):
                continue
            hit = {group[c] for c in _reached(s[2], envl, origins, stepped) if c in group}
            if len(hit) == 1:
                out.setdefault(cls, (hit.pop(), 0 if cls in _LOW else 1))
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


def _regen(vals, wrap, turn):
    """``[(start, emits, signed step)]``: the runs a turning ramp regenerates from its seed.

    The magnitude of the first transition is the candidate step and the rest of the run is
    *predicted*, direction turns included, so the run ends where the prediction does — the
    same regenerate-or-refuse rule a wrapping run takes, with the turn in it."""
    out, i = [], 0
    while i + 1 < len(vals):
        d = (vals[i + 1] - vals[i]) % wrap
        mag, sgn = (d, 1) if d * 2 <= wrap else (wrap - d, -1)
        n = 1
        if mag:
            t = (1, vals[i], sgn)
            while i + n < len(vals):
                t = _turned(("RAMP", vals[i], sgn * mag, wrap, turn or (-1, -1)), t[0] + 1, t)
                if t[1] != vals[i + n]:
                    break
                n += 1
            if n >= 2:
                out.append((i, n, sgn * mag))
        i += max(1, n - 1)  # adjacent runs share their boundary emit
    return out


def _named_step(reg, step, pool, banks, mem0, masks):
    """Is this step a declared byte under one of the masks the program text applies to it?"""
    return any(
        byte & m == step for _k, _r, byte in _decl_cells(reg, pool, banks, mem0) for m in masks
    )


def _acc_streams(acc, pools, banks, tabs, lww, mem0, done=()):
    """``(streams, sweeps, explained)``: the sweep as a RAMP whose step the origin map names.

    A run of the register's own emits is the candidate; every stepped emit's accumulator
    execution must report an origin that is a declared byte at a non-``mut`` offset equal,
    under the mask the text applies, to that step, so a fitted step is refused. A cell
    whose high byte takes its carry is one 16-bit accumulator and emits through a ``pair``
    route: the modulus and the high register's mask are that byte's own ``AND``-immediate,
    and a byte-wide ramp on the low register would leave every carry frame residual."""
    seqs, wide = {}, {}
    for f, wr in enumerate(lww):
        for reg, (val, srcs) in wr.items():
            cls = _class_of(reg)
            got = acc.get(cls)
            if got is None or (f < len(done) and reg in done[f]):
                continue
            if _lane_key((reg, val, srcs), tabs.get(cls, ()), mem0) is not None:
                continue  # the statement's own naming: the bank fallback must not cut a run
            a, half = got
            if len(a.cells) == 2 and half:
                continue  # the high half is written by the low half's own pair emit
            hi = wr.get(reg + 1) if len(a.cells) == 2 else None
            if hi is None:
                seqs.setdefault(reg, []).append((f, val))
            elif _lane_key((reg + 1,) + hi, tabs.get(cls + 1, ()), mem0) is None:
                wide.setdefault(reg, []).append((f, val | (hi[0] << 8)))
    streams, sweeps, explained = [], [], [set() for _f in lww]
    for reg, seq in sorted(seqs.items()):
        a = acc[_class_of(reg)][0]
        claimed = set()
        for at, n, delta in _runs([v for _f, v in seq]):
            if seq[at][0] in claimed:  # adjacent runs share their boundary emit
                at, n = at + 1, n - 1
            steps = {delta if s > 0 else (-delta) & 0xFF for s in a.signs}
            if n < 2 or not any(
                all(
                    _named_step(reg, step, pools[f].get(a.cells[0], ()), banks, mem0, a.masks)
                    for f, _v in seq[at + 1 : at + n]
                )
                for step in steps
            ):
                continue
            counts = [0] * len(lww)
            for f, _v in seq[at : at + n]:
                counts[f] = 1
                claimed.add(f)
                explained[f].add(reg)
            streams.append(
                (tuple(counts), ("RAMP", seq[at][1], delta, 0x100, ()), reg, "ramp", None)
            )
    for reg, seq in sorted(wide.items()):
        a = acc[_class_of(reg)][0]
        wrap = a.wraps[0] * a.wraps[1]
        claimed = set()
        for at, n, step in _regen([v for _f, v in seq], wrap, a.turn):
            if seq[at][0] in claimed:  # adjacent runs share their boundary emit
                at, n = at + 1, n - 1
            if n < 2 or not all(
                _named_step(reg, abs(step), pools[f].get(a.cells[0], ()), banks, mem0, a.masks)
                for f, _v in seq[at + 1 : at + n]
            ):
                continue
            counts = [0] * len(lww)
            for f, _v in seq[at : at + n]:
                counts[f] = 1
                claimed.add(f)
                explained[f] |= {reg, reg + 1}
            sweeps.append(
                (
                    tuple(counts),
                    ("RAMP", seq[at][1], step, wrap, a.turn),
                    pair(reg, reg + 1, a.wraps[1] - 1),
                    reg,
                )
            )
    return streams, sweeps, explained


# ---- 4l. the accumulator as a persistent object the graph carries -----------------
ObjNode = namedtuple("ObjNode", "transfer counts rows lo hi mask ev")
ObjGroup = namedtuple("ObjGroup", "cell step reads")
_OFFS = (0, 7, 14)  # the SID's own per-voice register offsets: what `sta $d402,y` indexes


def _obj_arms(arms, roots, cells, env, staged):
    """``{watch index: (sign, mask, wide)}`` for the arms stepping an accumulator's low cell.

    ``wide`` says the step carries into the high cell: some high root takes the carry out
    of *this* arm's own arithmetic, its other terms included, which is the program text
    binding the two halves of one number rather than the two addresses being adjacent."""
    lo = cells[0]
    hi = roots.get(cells[1], ()) if len(cells) == 2 else ()
    out = {}
    for i, (cell, got) in arms.items():
        if cell != lo:
            continue
        sign, _root, core, _wrap, _at, held = got
        want = {b for t in core[2] if t not in held for b in (_read_base(t, env),) if b}
        wide = any(
            _carries(r[1], env, lo) and all(_carries(r[1], env, c) for c in want) for r in hi
        )
        out[i] = (sign, _step_mask(core, held, env, staged), wide)
    return out


def _obj_step(srcs, banks, mem0, mask):
    """The declared byte under ``mask`` this execution's own origins name, else None."""
    got = {
        mem0[x] & mask
        for x in srcs
        if (d := _decl_of(x, banks)) is not None and (x - d[0]) % _record(d[1], d[2]) not in d[3]
    }
    return got.pop() if len(got) == 1 else None


def _obj_named(srcs, decl, off):
    """The object cell these origins name at record offset ``off``, else None."""
    base, size, stride, _mut = decl
    rec = _record(size, stride)
    got = {x - off for x in srcs if base <= x < base + size and (x - base) % rec == off}
    return got.pop() if len(got) == 1 else None


def _obj_execs(oarms, wat, banks, mem0, nframes):
    """``[frame] -> [(sign, mask, wide, declared step)]``, one entry per low-cell execution."""
    out = []
    for f in range(nframes):
        out.append(
            [
                oarms[i] + (_obj_step(srcs, banks, mem0, oarms[i][1]),)
                for i, _cell, srcs in (wat[f] if f < len(wat) else ())
                if i in oarms
            ]
        )
    return out


def _obj_voices(lww, order, cls, decl, off):
    """``[frame] -> [(voice, {register: object cell or None})]`` in the machine's write order.

    One loop iteration serves one voice, so the voice its first write names is where its
    reads and its step both sit; a write whose origins name an object cell copies what
    that object holds, and every other one is the step itself."""
    out = []
    for f, seq in enumerate(order):
        row, at = [], {}
        for reg in seq:
            k = _class_of(reg) - cls
            if k not in (0, 1):
                continue
            v = reg // 7
            if v not in at:
                at[v] = len(row)
                row.append((v, {}))
            row[at[v]][1][reg] = _obj_named(lww[f][reg][1], decl, off + k)
        out.append(row)
    return out


def _obj_turn(cell, byte, sign, val, turn, bound):
    """Step a carrying object once, its direction the declared bound's to turn (§4c)."""
    st = turn.get(cell) or (1, val, sign)
    st = _turned(("RAMP", val, abs(byte), bound[0], bound[1]), st[0] + 1, st)
    turn[cell] = st
    return st[1]


def _obj_walk(execs, voices, mem0, bound):
    """``(reads, {cell: (wide, byte, sign)}, {cell: first stepped value})``, walked in order.

    Each voice either copies what its object holds or steps it, and the machine's own
    write order says which; an object two arms read different steps for is refused, so
    nothing is cut to fit."""
    val, turn, held, out, spec, first = {}, {}, {}, [], {}, {}
    for f, (ex, row) in enumerate(zip(execs, voices)):
        k, nth = 0, {}
        for j, (v, regs) in enumerate(row):
            named = {c for c in regs.values() if c is not None}
            cell = next(iter(named)) if len(named) == 1 else held.get(v)
            step = ex[k] if not named and k < len(ex) else None
            k += step is not None
            if cell is None or (not named and step is None):
                continue
            held[v] = cell
            if step is not None:
                sign, _mask, wide, byte = step
                was = spec.setdefault(cell, (wide, byte, sign))
                if was[:2] != (wide, byte) or not byte:
                    spec[cell] = (wide, byte, None)
                    continue
                v0 = val.setdefault(cell, _obj_seed(cell, mem0))
                val[cell] = (
                    _obj_turn(cell, byte, sign, v0, turn, bound)
                    if wide
                    else (v0 & ~_FULL) | ((v0 + sign * byte) & _FULL)
                )
                first.setdefault(cell, (val[cell], turn[cell][2] if wide else sign))
                nth[cell] = nth.get(cell, 0) + 1
            out.append(
                (
                    (f, j),
                    v,
                    frozenset(regs),
                    cell,
                    val.setdefault(cell, _obj_seed(cell, mem0)),
                    None if step is not None else nth.get(cell, 0),
                )
            )
    return out, spec, first


def _obj_seed(cell, mem0):
    """The object's declared first value: the post-init image at its own row."""
    return mem0[cell] | (mem0[cell + 1] << 8)


def _obj_reads(walk, lww, cls, masks):
    """``{cell: {(stepped, half): [(frame, ordinal, voice)]}}``, and the cells a read denies."""
    out, bad = {}, set()
    for (f, j), v, regs, cell, val, at in walk:
        for reg in sorted(regs):
            k = _class_of(reg) - cls
            got = lww[f].get(reg)
            want = val & _FULL if not k else (val >> 8) & masks.get(cell, _FULL)
            if got is None or got[0] != want:
                bad.add(cell)
            out.setdefault(cell, {}).setdefault((at, k), []).append((f, j, v))
    return out, bad


def _obj_group(cell, reads, spec, first, ctx, nframes):
    """The nodes one object emits through: its step, then every read of what it holds.

    A carrying object writes both halves of one number, so its step and its held reads
    are ``pair`` emits and a read of one half alone refuses it; a byte-wide one keeps the
    halves apart, its high one the declared byte no arm of this object ever steps."""
    cls, mask, turn, wrap, mem0 = ctx
    wide, byte, sign = spec.get(cell, (None, 0, None))
    seed = _obj_seed(cell, mem0)
    ran, went = first.get(cell, (seed, sign))  # the declared image advanced once, and whither
    hi = cls + 1 if wide else None
    if wide and any(
        set(rs) != set(reads.get((at, 1), ())) for (at, k), rs in reads.items() if not k
    ):
        return None
    step, out = None, []
    for (at, k), rs in sorted(reads.items(), key=lambda kv: (kv[0][0] is not None, kv[0])):
        if k and wide:
            continue
        if at is None:  # the object's own step: one RAMP over the declared byte
            if sign is None:
                return None
            step = _obj_edge(
                ("RAMP", ran if wide else ran & _FULL, went * byte, wrap, turn if wide else ()),
                rs,
                cls,
                hi,
                mask,
                "ramp",
                nframes,
            )
        elif k:  # the high half a byte-wide object never steps: the declared byte, held
            out.append(
                _obj_edge(
                    ("SELECT", ((seed >> 8) & _FULL,), ()),
                    rs,
                    cls + 1,
                    None,
                    _FULL,
                    "lane",
                    nframes,
                )
            )
        elif step is not None:
            out.append(
                _obj_edge(
                    ("HOLD", None, seed if wide else seed & _FULL, at),
                    rs,
                    cls,
                    hi,
                    mask,
                    "ramp",
                    nframes,
                )
            )
        else:  # an object no arm ever steps is its own declared word, read where it is used
            out.append(
                _obj_edge(("SELECT", (seed & _FULL,), ()), rs, cls, hi, mask, "lane", nframes)
            )
    return ObjGroup(cell, step, out) if step is not None or out else None


def _obj_edge(transfer, reads, lo, hi, mask, ev, nframes):
    """One node: its own fire counts, and the voice each fire's register offset comes from."""
    counts, rows = [0] * nframes, []
    for f, _j, v in sorted(reads):  # the machine's own order inside a frame, not the voice's
        counts[f] += 1
        rows.append(v)
    return ObjNode(transfer, tuple(counts), tuple(rows), lo, hi, mask, ev)


def _obj_streams(prog, banks, accs, arms, roots, wat, lww, order, nframes):
    """``(groups, explained)``: each accumulator cell as one object the graph carries.

    The cell is a declared region's lane, so the object's first value is the post-init
    image at its own row and every later one is the step the origin map names; a note-on
    then emits what the object holds instead of restarting a run at an observed byte."""
    groups, explained = [], [set() for _f in range(nframes)]
    env, staged = _prog_env(prog), _staged(prog)
    for cls, (a, half) in sorted(accs.items()):
        decl = None if half or cls > _VOICE_HI else _decl_of(a.cells[0], banks)
        if decl is None:
            continue
        off = (a.cells[0] - decl[0]) % _record(decl[1], decl[2])
        oarms = _obj_arms(arms, roots, a.cells, env, staged)
        if not oarms:
            continue
        wrap = a.wraps[0] * a.wraps[1] if len(a.wraps) == 2 else a.wraps[0]
        mask = a.wraps[1] - 1 if len(a.wraps) == 2 else _FULL
        walk, spec, first = _obj_walk(
            _obj_execs(oarms, wat, banks, prog.mem0, nframes),
            _obj_voices(lww, order, cls, decl, off),
            prog.mem0,
            (wrap, a.turn),
        )
        masks = {c: (mask if spec.get(c, (None,))[0] else _FULL) for c in {r[3] for r in walk}}
        reads, bad = _obj_reads(walk, lww, cls, masks)
        for cell in sorted(set(reads) - bad):
            ctx = (cls, masks[cell], a.turn, wrap if spec.get(cell, (0,))[0] else 0x100, prog.mem0)
            got = _obj_group(cell, reads[cell], spec, first, ctx, nframes)
            if got is None:
                continue
            groups.append(got)
            for (_at, k), rs in reads[cell].items():
                for f, _j, v in rs:
                    explained[f].add(cls + k + _OFFS[v])
    return groups, explained


# ---- 4m. the accumulator the note reloads ----------------------------------------
RelObj = namedtuple("RelObj", "cell table rows seeds fires step wrap first holds")


def _reload_walks(prog, banks):
    """``{cell: (steps, reloads, opaque)}``: cells the text walks and something reloads.

    §4l's object is a cell a declaration *contains*, whose first value is the post-init
    image. This is that object one step out: plain RAM whose value arrives from a store
    the text does not compute, and whose later ones are the step the text names."""
    env, staged, out = _prog_env(prog), _staged(prog), {}
    for proc in prog.procs:
        e = {}
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                e[s[1]] = s[2]
                continue
            cell = _base(s[1]) if s[0] == "st" else None
            if cell is None or cell < 0x100 or _sid_class(s[1]) is not None:
                continue
            if _decl_of(cell, banks) is not None:
                continue
            got = out.setdefault(cell, ([], [], []))
            rule = _walk_of(s, e, cell)
            if rule is not None and rule[0] == "step" and rule[1] % rule[2]:
                got[0].append((s, rule))
            elif rule is not None or _steps_own(s, e, env, staged):
                got[2].append(s)
            else:
                got[1].append(s)
    return {c: v for c, v in out.items() if v[0] and v[1]}


def _reload_reads(prog, cells):
    """``[(store, cell, capture)]``: SID stores whose value reads exactly one object cell.

    ``capture`` is the assignment the stored value resolves through, where there is one:
    a 6502 reads a cell into A before stepping it, so the register takes what the object
    held at that assignment and not what it holds after."""
    staged = _staged(prog)
    origins = {c: [s[2] for s in ss] for c, ss in staged.items()}
    out = []
    for proc in prog.procs:
        e, envl, defs = {}, {}, {}
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                e[s[1]], defs[s[1]] = s[2], s
                envl.setdefault(s[1], []).append(s[2])
                continue
            if s[0] != "st" or _sid_class(s[1]) is None:
                continue
            hit = _reached(s[2], envl, origins, cells)
            if len(hit) == 1:
                w = _def_stmt(s[2], e, defs, s)
                out.append((s, hit.pop(), None if w is s else w))
    return out


def _reload_watch(walks, reads, at, taken):
    """``([statement], {watch index: tag})``: every writer of an object cell, and its readers."""
    out, tags, seen = [], {}, dict(taken)

    def add(s, tag):
        i = seen.get(id(s))
        if i is None:
            i = seen[id(s)] = at + len(out)
            out.append(s)
        tags[i] = tag
        return i

    for cell, (steps, loads, opaque) in sorted(walks.items()):
        for s, rule in steps:
            add(s, ("step", cell, rule[1:]))
        for s in loads:
            add(s, ("load", cell, None))
        for s in opaque:
            add(s, ("dirt", cell, None))
    for s, cell, cap in reads:
        j = None if cap is None else add(cap, ("snap", None, None))
        add(s, ("read", cell, j))
    return out, tags


def _reload_seed(srcs, banks, mem0):
    """``(lane key, row, byte)`` the one declared cell a reload's origins name, else None."""
    got = []
    for src in srcs:
        d = _decl_of(src, banks)
        if d is None or (src - d[0]) % _record(d[1], d[2]) in d[3]:
            continue
        row, off = divmod(src - d[0], d[2])
        got.append((("lane", 0, d[0], d[1], d[2], off, None), row, mem0[src]))
    return got[0] if len(got) == 1 else None


def _reload_walk(walks, tags, wat, lww, banks, mem0, nframes):
    """``(claims, seeds, fires, spec, bad)``: every object cell walked in the machine's order.

    A store the text names steps the cell and a reload sets it from a declared byte; a
    store neither rule covers leaves the object undefined until the next reload, so
    nothing is claimed across a writer the text does not name."""
    val, lane, spec, seeds, fires, claims, bad = {}, {}, {}, {}, {}, {}, set()
    for f in range(nframes):
        nth, snap, stepped = {}, {}, set()
        for i, at, srcs in wat[f] if f < len(wat) else ():
            tag = tags.get(i)
            if tag is None:
                continue
            kind, base, extra = tag
            cell = at if kind in ("step", "load", "dirt") else None
            if kind == "load":
                if cell in stepped:  # one shared edge ordinal cannot say step-then-reload
                    bad.add(cell)
                got = _reload_seed(srcs, banks, mem0)
                if got is None:
                    val[cell] = None
                    continue
                key, row, byte = got
                val[cell], lane[cell] = byte, key
                seeds.setdefault((cell, key), []).append((f, row))
            elif kind == "step":
                stepped.add(cell)
                if spec.setdefault(cell, extra) != extra:
                    bad.add(cell)
                if val.get(cell) is None or cell not in lane:
                    continue
                val[cell] = (val[cell] + extra[0]) % extra[1]
            elif kind == "dirt":  # a writer the text does not name: the snapshots lose it too
                val[cell] = None
                for was, _ns, _lz in snap.values():
                    was.pop(cell, None)
                continue
            elif kind == "snap":
                snap[i] = (dict(val), dict(nth), dict(lane))
                continue
            else:
                reg = at - _SID
                obj, (vs, ns, ls) = base + reg // 7, snap.get(extra, (val, nth, lane))
                cur, took, key = vs.get(obj), lww[f].get(reg), ls.get(obj)
                if cur is None or key is None or took is None or took[1] is not srcs:
                    continue
                if took[0] != cur:
                    bad.add(obj)
                    continue
                claims.setdefault((obj, key, reg, ns.get((obj, key), 0)), []).append(f)
                continue
            key = lane[cell]
            nth[(cell, key)] = nth.get((cell, key), 0) + 1
            fires.setdefault((cell, key), [0] * nframes)[f] += 1
    return claims, seeds, fires, spec, bad


def _reload_streams(prog, banks, walks, tags, wat, lww, nframes, done=()):
    """``(objects, explained)``: an accumulator whose seed is the row a note-on names.

    The cell is walked from the byte each reload declares by the step the text names, and
    every read is checked against the byte the register took; one contradiction refuses
    that object whole, exactly as §4l's containment reading does."""
    mem0 = prog.mem0
    claims, seeds, fires, spec, bad = _reload_walk(walks, tags, wat, lww, banks, mem0, nframes)
    holds, out, explained = {}, [], [set() for _f in range(nframes)]
    for (cell, key, reg, at), fs in sorted(claims.items()):
        obj = (cell, key)
        if cell in bad or cell not in spec or not all(obj in d for d in (seeds, fires)):
            continue  # an object with no reload, or none of the steps its arms name, is not one
        got = [f for f in fs if reg not in done[f]] if done else fs
        if got:
            counts = [0] * nframes
            for f in got:
                counts[f] += 1
                explained[f].add(reg)
            holds.setdefault(obj, []).append((tuple(counts), at, reg))
    for cell, key in sorted(holds):
        counts = [0] * nframes
        for f, _r in seeds[(cell, key)]:
            counts[f] += 1
        out.append(
            RelObj(
                cell,
                _lane(key, mem0),
                tuple(r for _f, r in seeds[(cell, key)]),
                tuple(counts),
                tuple(fires[(cell, key)]),
                spec[cell][0],
                spec[cell][1],
                mem0[cell],
                tuple(holds[(cell, key)]),
            )
        )
    return out, explained


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


def _cascade_fires(dec, n, p):
    """Per frame, the ticks ``DIV(n, p)`` emits over the input ticks ``dec`` carries."""
    out, t = [], 0
    for c in dec:
        out.append(sum(1 for x in range(t, t + c) if x % n == p))
        t += c
    return out


def _clock_node(counts, seq, decs=None):
    """The clock chain that generates this edge stream — one ``DIV``, two, or the floor.

    Divisor and phase are program text (the reload, the counter's post-init byte); a
    cascade needs machine evidence too — the inner divider's own dec statement must
    execute exactly on the outer's ticks — and the whole stream matches both ways."""
    for n, phase in seq.ticks:
        if _generates(counts, n, phase):
            return [div(n, phase=phase)]
    for cell, dec in (decs or {}).items():
        if not sum(counts):
            break
        for nb, pb in seq.cells.get(cell, ()):
            if list(counts) != _cascade_fires(dec, nb, pb):
                continue
            for na, pa in seq.ticks:
                if _generates(dec, na, pa):
                    inner = Generator(("DIV", nb, pb), ("event", -1), ("fire",))
                    return [div(na, phase=pa), inner]
    return [edge(counts)]


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
            pairs.append(
                (tuple(counts), ("RAMP", run[0][1], a.step, a.wrap, ()), tabs[ptr][blk], reg)
            )
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


# ---- 4j. the song: terminator-bounded regions at cursors the program text steps ---
Chart = namedtuple("Chart", "pointer cursor terms roles blocks reads source")
Block = namedtuple("Block", "index base size data rows")


def _sub_exprs(x, acc):
    """One expression and every sub-expression under it."""
    if isinstance(x, tuple):
        acc.append(x)
        if x[0] == "op":
            for c in x[2]:
                _sub_exprs(c, acc)
        elif x[0] == "mem":
            _sub_exprs(x[1], acc)
    return acc


def _guarded(stmts, env, guards, out, cursors, held):
    """Deref reads, their destination stores and cursor steps, in order, with their guards.

    A statement's guards are the conditions the machine must satisfy to reach it, over
    the locals live there. A byte's **destination** carries guards too: that is how one
    parameter byte reaches two different cells."""
    for s in stmts:
        if s[0] in ("asg", "st"):
            got = None
            for x in _sub_exprs(s[2], []):
                if x[0] == "mem" and frameptr.deref(x[1]) is not None:
                    got = len(out)
                    out.append(("read", frameptr.deref(x[1])[0], s, tuple(guards), dict(env)))
            src = got if got is not None else held.get(_loc_of(s[2]))
            if s[0] == "st":
                if src is not None:
                    out.append(("dest", (src, _base(s[1])), s, tuple(guards), dict(env)))
                if _base(s[1]) in cursors:
                    out.append(("step", _base(s[1]), s, tuple(guards), dict(env)))
            else:
                held[s[1]] = src
                env[s[1]] = s[2]
            continue
        for k, body in enumerate(frameproc._stmt_bodies(s)):
            g = guards + [(s[2], (s[1] == "if") ^ (k == 1))] if s[0] == "if" else guards
            _guarded(list(body), env, g, out, cursors, held)


def _deref_walk(prog):
    """``[(kind, what, statement, guards, locals)]`` for every proc, in program order."""
    out, cursors = [], set(_walked(prog))
    for proc in prog.procs:
        _guarded(list(proc[3]), {}, [], out, cursors, {})
    return out


def _loc_of(expr):
    """The local an expression names outright, else None."""
    return expr[1] if isinstance(expr, tuple) and expr[0] == "loc" else None


def _read_names(events):
    """``{read: (destination cells, the read's own expression)}``: a deref byte's names.

    A byte's names are what the guards test. The local it lands in is matched by the
    read expression itself, so two uses of one register name cannot collide."""
    out = {i: (set(), e[2][2]) for i, e in enumerate(events) if e[0] == "read"}
    for kind, what, s, _g, _e in events:
        if kind == "dest" and what[0] in out:
            out[what[0]][0].add(_base(s[1]))
    return out


def _names_read(expr, key, env, depth=4):
    """Does this expression read one of a byte's names?"""
    if not isinstance(expr, tuple) or depth <= 0:
        return False
    if expr is key[1]:
        return True
    if expr[0] == "mem":
        return _base(expr[1]) in key[0]
    if expr[0] == "loc":
        return _names_read(env.get(expr[1]), key, env, depth - 1)
    return expr[0] == "op" and any(_names_read(c, key, env, depth - 1) for c in expr[2])


_CMP = ("INT_EQUAL", "INT_NOTEQUAL")


def _zero_test(cond):
    """``(value expression, immediate, true when unequal)`` for a compare, else None."""
    if not (isinstance(cond, tuple) and cond[0] == "op" and cond[1] in _CMP):
        return None
    cs = [k for k in cond[2] if isinstance(k, tuple) and k[0] == "const"]
    vs = [k for k in cond[2] if k not in cs]
    if len(cs) != 1 or len(vs) != 1:
        return None
    return (vs[0], cs[0][1] & 0xFF, cond[1] == "INT_NOTEQUAL")


def _mask_test(cond, names, env):
    """``(mask, true when set)`` for a test of one byte's masked bits, else None."""
    got = _zero_test(cond)
    if got is None or got[1] != 0:
        return None
    root = _resolve(got[0], env)
    if not (isinstance(root, tuple) and root[0] == "op" and root[1] == "INT_AND"):
        return None
    ms = [k for k in root[2] if isinstance(k, tuple) and k[0] == "const"]
    ts = [k for k in root[2] if k not in ms]
    if len(ms) != 1 or not ts or not _names_read(ts[0], names, env):
        return None
    return (ms[0][1] & 0xFF, got[2])


def _const_test(cond, key, env):
    """``(byte, true when equal)`` for a test of one byte against an immediate, else None."""
    got = _zero_test(cond)
    if got is None:
        return None
    root = _resolve(got[0], env)
    named = root is key[1] or (
        isinstance(root, tuple) and root[0] == "mem" and _base(root[1]) in key[0]
    )
    return (got[1], not got[2]) if named else None


def _holds(guards, seen, names, env):
    """Do the guards hold for the bytes read so far? Tests of other values do not bind."""
    for cond, truth in guards:
        for i, val in seen.items():
            got = _mask_test(cond, names[i], env)
            if got is not None and (bool(val & got[0]) == got[1]) != truth:
                return False
            got = _const_test(cond, names[i], env)
            if got is not None and ((val == got[0]) == got[1]) != truth:
                return False
    return True


def _conditions(prog):
    """Every ``if`` condition of the program, with the locals live at it and its arms."""
    out = []

    def go(stmts, env):
        for s in stmts:
            if s[0] == "asg":
                env[s[1]] = s[2]
            if s[0] == "if":
                arms = frameproc._stmt_bodies(s)
                out.append((s[2], dict(env), (arms[s[1] == "if"], arms[s[1] != "if"])))
            for b in frameproc._stmt_bodies(s):
                go(list(b), dict(env))

    for proc in prog.procs:
        go(list(proc[3]), {})
    return out


def _arm_planes(body):
    """The register classes the SID stores under one ``if`` arm write."""
    return {_sid_class(s[1]) for s in _in_order(list(body)) if s[0] == "st"} - {None}


def _in_order(stmts):
    """Every statement, nested bodies in place: the order the machine runs them."""
    for s in stmts:
        yield s
        for body in frameproc._stmt_bodies(s):
            yield from _in_order(list(body))


def _terminators(conds, names):
    """The immediates the program text compares a region's own bytes against."""
    return {t for cond, env, _a in conds for t in (_const_test(cond, names, env) or ())[:1]}


def _term_reads(gram, names, envs):
    """The reads whose byte ends the region: the row's first, and the reset's own guard.

    The cursor is reset by a walked comparison against the terminator byte, so that
    comparison names it; where the reset is unreachable in the walked text the region's
    first read is what the text compares instead (a stop entry, an end marker)."""
    out, stepped = set(), False
    for i, kind, rule, guards, env in gram:
        if kind == "read" and not stepped:
            out.add(i)
        if kind != "step":
            continue
        stepped = True
        if rule is None or rule[0] != "set":
            continue
        for cond, _t in guards:
            out |= {j for j in names if _const_test(cond, names[j], env) is not None}
    return out


def _grammar(events, ptr, cursor):
    """One region's row walk: cursor steps, its own reads and their destinations.

    It starts at the first read: before that the cursor is not indexing this region, so
    what the text does to it there — an init clear — is not part of the walk."""
    at = [i for i, e in enumerate(events) if e[0] == "read" and e[1] == ptr]
    mine, out = set(at), []
    for i, (kind, what, s, g, env) in list(enumerate(events))[at[0] if at else 0 :]:
        if kind == "step" and what == cursor:
            out.append((i, "step", _walk_of(s, env, cursor), g, env))
        elif kind == "read" and what == ptr:
            out.append((i, "read", None, g, env))
        elif kind == "dest" and what[0] in mine:
            out.append((i, "dest", what, g, env))
    return out


def _row_walk(data, gram, terms, names, envs):
    """``[(offset, fields)]``: the rows a terminator-bounded region holds, walked.

    Every read is placed at the cursor's live value and the cursor advances by the steps
    whose guards the bytes already read satisfy — one step per walked increment — and the
    terminator's own compare ends the region."""
    rows, off = [], 0
    while off < len(data) and data[off] not in terms:
        cur, seen, got, last = off, {}, {}, False
        for i, kind, rule, g, env in gram:
            if not _holds(g, seen, names, env):
                continue
            if kind == "step":
                if rule is None:
                    return tuple(rows)
                if rule[0] == "set":  # the cursor's reset: the region ends here
                    last = True
                    continue
                cur = (cur + rule[1]) % rule[2]
            elif kind == "dest" and rule[0] in got:
                got[rule[0]][2].add(rule[1])
            elif kind == "read" and cur < len(data):
                seen[i] = data[cur]
                got[i] = (cur, data[cur], set())
                last = last or data[cur] in terms
        rows.append(
            (off, tuple((v[0], v[1], frozenset(v[2])) for v in got.values() if off <= v[0] < cur))
        )
        if last or cur <= off:
            return tuple(rows)
        off = cur
    return tuple(rows)


def _cursor_roles(prog, banks, pitch):
    """``{cursor cell: role}``: what a cell a region is indexed by selects.

    A cursor of the pitch table selects a **note**, one of an instrument bank an
    **instrument**; a cell a divider reloads from carries a **duration**. The names are
    the roles this layer already knows, not new vocabulary."""
    out, insts = {}, set(_instruments(prog))
    base = None if pitch is None else pitch.base
    for b, curs in _pairs(prog).items():
        d = _decl_of(b, banks)
        if d is None:
            continue
        role = None
        if base is not None and d[0] <= base < d[0] + d[1]:
            role = "note"
        elif any(d[0] <= i < d[0] + d[1] for i in insts):
            role = "instrument"
        for c in curs:
            if role is not None:
                out.setdefault(c, role)
    return out


def _row_fields(prog, key, gram, conds):
    """``{mask: field}`` for the row byte's own bits, named by what each arm does.

    An arm that steps the cursor no further ties the row to the one before it; one that
    steps it further takes a parameter byte; the mask a stepped counter is reloaded
    through is the row's own duration."""
    steps = {}
    for _i, kind, _rule, guards, genv in gram:
        for cond, truth in guards:
            got = _mask_test(cond, key, genv)
            if got is not None:
                steps.setdefault(got[0], [0, 0])[int(truth == got[1])] += kind == "step"
    out = {
        m: ("tie" if on < off else "parameter" if on > off else "flag")
        for m, (off, on) in steps.items()
    }
    for cond, cenv, arms in conds:
        got = _mask_test(cond, key, cenv)
        if got is not None:
            gated = {_CTRL, 5, 6}
            on, off = (_arm_planes(arms[int(b)]) & gated for b in (got[1], not got[1]))
            out.setdefault(got[0], "release" if on else "sustain" if off else "flag")
    dur = _duration_mask(prog, key)
    if dur is not None:
        out[dur] = "duration"
    return out


def _duration_mask(prog, key):
    """The mask of the row byte a divider's reload takes: the row's own note length."""
    dividers = {c.base for c in _clocks(prog) if c.role == "divider"}
    for proc in prog.procs:
        env = {}
        for s in _proc_stmts(proc):
            if s[0] == "asg":
                env[s[1]] = s[2]
            elif s[0] == "st" and _base(s[1]) in dividers:
                root = _resolve(s[2], env)
                if isinstance(root, tuple) and root[0] == "op" and root[1] == "INT_AND":
                    ms = [k for k in root[2] if isinstance(k, tuple) and k[0] == "const"]
                    ts = [k for k in root[2] if k not in ms]
                    if len(ms) == 1 and ts and _names_read(ts[0], key, env):
                        return ms[0][1] & 0xFF
    return None


def _extent(mem0, base, terms, floor, limit=0x100):
    """The region's size: to the first terminator byte at or above its proven floor.

    The declaration's own extent is what the machine indexed, so it floors the region;
    the terminator is the immediate the program text compares against, and the byte at
    that offset is declared data. Neither is read off the row stream."""
    for i in range(max(0, floor - 1), min(limit, 0x10000 - base)):
        if mem0[base + i] in terms:
            return i + 1
    return 0


def _charts(prog, banks, pitch, diag):
    """``[Chart]``: every pointer whose blocks are terminator-bounded regions the text walks.

    The pointer table is rung (f)'s (docs/frameprog.md §4.4), the extent the terminator
    compare's, the row grammar the guarded walk's — declared data and program text only,
    with nothing taken from the row stream the machine happened to read."""
    events = _deref_walk(prog)
    names = _read_names(events)
    envs = {i: e[4] for i, e in enumerate(events) if e[0] == "read"}
    conds = _conditions(prog)
    roles = _cursor_roles(prog, banks, pitch)
    decls = {d["base"]: d for d in prog.data_decls}
    out, seen = [], set()
    for site in frameptr.analyse(prog.mem0, prog.data_decls, prog.procs):
        cell = site.ptr.cell
        if site.why is not None or not site.ptr.tables or cell in seen:
            continue
        seen.add(cell)
        reads = [i for i, e in enumerate(events) if e[0] == "read" and e[1] == cell]
        cursor = _cursor_of(events, reads, envs)
        if cursor is None:
            diag["song_no_cursor"] += 1
            continue
        gram = _grammar(events, cell, cursor)
        ends = _term_reads(gram, names, envs) & set(reads)
        terms = set().union(*[_terminators(conds, names[i]) for i in ends]) if ends else set()
        if not terms:
            diag["song_no_terminator"] += 1
            continue
        got = _blocks(prog, site, terms, decls, gram, names, envs, diag)
        if not got:
            continue
        row = next((i for i, k, _r, _g, _e in gram if k == "read"), None)
        fields = _row_fields(prog, names[row], gram, conds)
        src = _chart_source(prog, names, cell)
        out.append(
            Chart(
                cell, cursor, tuple(sorted(terms)), (roles, fields), tuple(got), tuple(reads), src
            )
        )
        diag["song_regions"] += len(got)
    return [c._replace(source=_chart_ptr(c.source, out)) for c in out]


def _chart_source(prog, names, cell):
    """The deref read whose byte indexes this pointer's own reload table, else None.

    An orderlist entry names a pattern by being the index the pointer reloads at, which
    is what links one region's rows to another region's blocks."""
    for proc in prog.procs:
        env = {}
        for s in _in_order(list(proc[3])):
            if s[0] == "asg":
                env[s[1]] = s[2]
            elif s[0] == "st" and _base(s[1]) in (cell, cell + 1):
                got = _indexed_by(_resolve(s[2], env), names, env)
                if got is not None:
                    return got
    return None


def _indexed_by(expr, names, env):
    """The deref read whose byte is the index of some load inside ``expr``, else None."""
    for x in _sub_exprs(expr, []):
        got = frameproc._index_of(x[1]) if x[0] == "mem" else None
        hits = [] if got is None else [i for i in names if _names_read(got[1], names[i], env)]
        if hits:
            return hits[0]
    return None


def _chart_ptr(read, charts):
    """The chart a linking read belongs to, so the link is between charts, not events."""
    for c in charts:
        if read in c.reads:
            return c.pointer
    return None


def _cursor_of(events, reads, envs):
    """The cell a pointer's own reads index, resolved through the locals live at each."""
    got = set()
    for i in reads:
        expr = frameptr.deref(_deref_addr(events[i]))[1]
        if expr is not None:
            cell = _read_base(expr, envs[i])
            if cell >= 0x100:
                got.add(cell)
    return got.pop() if len(got) == 1 else None


def _deref_addr(event):
    """The deref address expression one read event names."""
    for x in _sub_exprs(event[2][2], []):
        if x[0] == "mem" and frameptr.deref(x[1]) is not None:
            return x[1]
    return None


def _blocks(prog, site, terms, decls, gram, names, envs, diag):
    """``[Block]``: the declared blocks the pointer's own reload table names, walked."""
    lo, hi, n, _ix = site.ptr.tables[0]
    out = []
    for k in range(n):
        base = prog.mem0[lo + k] | (prog.mem0[hi + k] << 8)
        d = decls.get(base)
        size = _extent(prog.mem0, base, terms, d["size"] if d else 1, site.bound + 1)
        if not size:
            diag["song_block_unbounded"] += 1
            continue
        data = tuple(prog.mem0[base + i] for i in range(size))
        out.append(Block(k, base, size, data, tuple(_row_walk(data, gram, terms, names, envs))))
    return out


# ---- 4k. the song as generators: a pattern byte is an index, not a register byte --
def _row_roles(row, roles):
    """``{role: byte}`` for one walked row: the parameter bytes, by what they are copied into.

    §4j already names a byte by the cell it flows into — the pitch table's cursor makes it a
    note, an instrument bank's cursor an instrument. Here that name becomes the *table* the
    byte indexes, which is the whole difference between a pattern byte and a register byte:
    §4g's byte-equality rule can never see an index, because an index is not the byte the
    register took."""
    off, fields = row
    out = {}
    for o, val, cells in fields:
        if o == off:
            continue
        for role in {roles.get(c) for c in cells} - {None}:
            out.setdefault(role, val)
    return out


def _voice_lanes(blk, blocks, roles):
    """``{(role, held): [byte]}`` for one voice: its entries' patterns, row by row."""
    lanes, held = {}, {}
    for _o, fields in blk.rows:
        got = blocks.get(next((x for _p, x, _c in fields[:1]), None))
        if got is None:
            break
        for row in got.rows:
            seen = _row_roles(row, roles)
            for role in set(seen) | set(held):
                held[role] = seen.get(role, held.get(role))
                if role in seen:
                    lanes.setdefault((role, False), []).append(seen[role])
                lanes.setdefault((role, True), []).append(held[role])
    return lanes


def _song_lanes(charts):
    """``{voice: {(role, held): bytes}}``: the indices each voice's song plays, in order.

    An orderlist is a chart whose entries name another chart's blocks (§4j), so a voice's
    row stream is the patterns its own entries name, concatenated — declared data at
    program-text offsets, in the order the program text's own cursor reaches it, with
    nothing read off the row stream the machine happened to produce.

    Two readings of one datum, both structural: a row that names no parameter either takes
    no emit at all, or holds the one before it, which is what the player's own cell does.
    Which of the two a lane takes is settled by whether it reproduces that lane's own
    stream, exactly as §4i settles a cursor's rows — never by fitting bytes."""
    out = {}
    for seq in charts:
        pats = [c for c in charts if c.source == seq.pointer]
        if not pats:
            continue
        for v, blk in enumerate(seq.blocks):
            lanes = _voice_lanes(blk, {b.index: b for b in pats[0].blocks}, pats[0].roles[0])
            out[v] = {k: tuple(vs) for k, vs in lanes.items() if vs and None not in vs}
    return out


def _song_durs(charts):
    """``{voice: periods}``: the ticks each row of a voice's song lasts.

    The duration field is the ``AND``-immediate §4j reads off the mask a stepped-down
    counter is reloaded through, and the extra tick is the text's own ``dec``-to-negative:
    a counter reloaded with `d` runs out `d + 1` ticks later. Both are program text over
    a declared byte, which is the standing §4c's masked step already has."""
    out = {}
    for seq in charts:
        pats = [c for c in charts if c.source == seq.pointer]
        mask = next(
            (m for c in pats for m, n in c.roles[1].items() if n == "duration"),
            None,
        )
        if mask is None or not pats:
            continue
        blocks = {b.index: b for b in pats[0].blocks}
        for v, blk in enumerate(seq.blocks):
            got = []
            for _o, fields in blk.rows:
                block = blocks.get(next((x for _p, x, _c in fields[:1]), None))
                if block is None:
                    break
                for off, row in block.rows:
                    byte = next((x for o, x, _c in row if o == off), None)
                    got.append(None if byte is None else (byte & mask) // (mask & -mask) + 1)
            if got and None not in got:
                out[v] = tuple(got)
    return out


def _row_fires(tick, table, phase, nframes):
    """The frames a countdown over ``table`` fires on, clocked by the tick stream ``tick``."""
    out, left, at = [0] * nframes, phase + 1, 0
    for f in range(nframes):
        if not tick[f]:
            continue
        left -= 1
        if left <= 0:
            out[f] += 1
            left = table[at % len(table)]
            at += 1
    return out


def _song_beats(durs, cands, nframes):
    """``{fire vector: (tick, tick phase, voice, row phase)}``: what the song's own rows beat.

    Every candidate is program text — the tick a divider's reload names, under each reading
    of the branch that tests it, and the phases the post-init counters hold (§4i). The fire
    vector is the *key*, so a stream is claimed only where the countdown fires on exactly
    its frames: a missing fire refuses as loudly as a spare one, and a period read off the
    fire pattern is refused for the reason §4d refuses one.

    Built once per tune rather than searched per stream: the candidates do not depend on
    the stream, so a per-stream search re-derives the same few vectors hundreds of times."""
    out = {}
    for n, tphase in cands[0]:
        tick = [1 if f % n == tphase else 0 for f in range(nframes)]
        for v, table in sorted(durs.items()):
            for rphase in cands[1]:
                out.setdefault(
                    tuple(_row_fires(tick, table, rphase, nframes)), (n, tphase, v, rphase)
                )
    return out


def _song_cands(prog, seq):
    """``([(tick, phase)], [row phase])``: the periods and counters the program text names.

    A counter the text steps down names its period twice over, and which reading holds is
    the *branch*: one that fires where the result reaches zero runs ``n`` ticks, one that
    fires where it goes negative runs ``n + 1`` and starts a tick earlier. Both are
    readings of the same declared reload and the same post-init counter byte (§4i), and
    the whole fire stream selects between them — never a period shaped to the fires."""
    ticks = set()
    for cell, ts in seq.cells.items():
        for n, _p in ts:
            for m in (n, n + 1):
                if m > 1:
                    ticks |= {(m, int(prog.mem0[cell]) % m), (m, (int(prog.mem0[cell]) - 1) % m)}
    rows = {
        int(prog.mem0[c.base + v]) - d
        for c in _clocks(prog)
        for v in range(3)
        for d in (0, 1)
        if c.kind == "dec" and c.reload is None
    }
    return sorted(ticks), sorted({r for r in rows if r >= 0} | {0})


def _song_at(lanes, reg, rows):
    """The song lane whose own bytes are this stream's row run, or None.

    The run is *predicted* from the declared song and compared with the run the machine
    read; a stream the song does not reproduce keeps its recovered rows, exactly as a
    sweep whose step no declaration names is refused whole (§4c)."""
    got = lanes.get(reg // 7 if reg <= _VOICE_HI else None) or {}
    want = tuple(rows)
    for key in sorted(got):
        table = got[key]
        if want and all(v == table[i % len(table)] for i, v in enumerate(want)):
            return key, table
    return None


def _songed(streams, charts, diag):
    """``{stream index: (lane key, table)}``: the streams the song's own bytes row.

    This is §7.4's stated goal reached: the row a note-on selects is *generated* from the
    orderlist and the patterns, so the pattern byte reaches the register as an ``Index``
    emit and not as declared data carried beside the graph."""
    lanes = _song_lanes(charts)
    out = {}
    for i, (_c, t, reg, ev, _k) in enumerate(streams):
        if ev != "lane" or t[0] != "SELECT" or not t[2] or _generated(t[2]):
            continue
        got = _song_at(lanes, reg, t[2])
        if got is None:
            diag["song_rows_unplayed"] += len(t[2])
        else:
            diag["song_rows_generated"] += len(t[2])
            out[i] = got
    diag["song_lane_nodes"] += len(set(out.values()))
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


def _div_watch(prog, at, taken=frozenset()):
    """``([statement], {watch index: divider cell})``: each divider's own dec statement.

    A cascade's evidence is the machine's — the inner counter steps exactly on the
    outer's ticks — so the dec rides the one ``eval_watch`` run everything rides.
    ``eval_watch`` keys by statement identity, so one already watched is skipped."""
    clocks = {c.base for c in _clocks(prog) if c.role == "divider"}
    env, out, tags, seen = _prog_env(prog), [], {}, set(taken)
    for s in _stmts(prog):
        if s[0] != "st" or _base(s[1]) not in clocks or id(s) in seen:
            continue
        if _step(s[2], env, _base(s[1])) == "dec":
            seen.add(id(s))
            tags[at + len(out)] = _base(s[1])
            out.append(s)
    return out, tags


def _decs(tags, wat, nframes):
    """``{divider cell: counts}``: the frames each divider's dec statement executed."""
    out = {}
    for f in range(nframes):
        for i, _cell, _srcs in wat[f] if f < len(wat) else ():
            cell = tags.get(i)
            if cell is not None:
                out.setdefault(cell, [0] * nframes)[f] += 1
    return {c: tuple(v) for c, v in out.items()}


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
Seq = namedtuple("Seq", "ticks cursors cells")


def _sequencer(prog, banks):
    """``Seq``: the tick clock and the row cursors, both read off the program text.

    A tick is a declared divisor (§4d) at the phase its own counter's post-init byte
    fixes; a cursor is a cell whose every writer the text names (``_walked``), seeded
    by the post-init image and stepped and wrapped by the text's own rule."""
    walk = _walked(prog)
    cells = {}
    for c, ns in _reloads(prog, banks).items():
        for n in ns:
            cells.setdefault(c, set()).add((n, (int(prog.mem0[c]) - 1) % n))
    curs = {}
    for cell, rules in walk.items():
        got = _arr_rule(rules)
        if got:
            curs[cell] = (int(prog.mem0[cell]) % got[1], got[0], got[1])
    ticks = {t for ts in cells.values() for t in ts}
    return Seq(tuple(sorted(ticks)), curs, {c: tuple(sorted(ts)) for c, ts in cells.items()})


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
            return (cell, ("RAMP",) + seq.cursors[cell] + ((),), beats[cell])
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


def _planed(i, st, chained, rowed, edges, songs=(), sung=()):
    """One stream's plane generator: read at its recovered run, its cursor, or its song row."""
    counts, t, reg, _ev, _key = st
    if i in chained:
        t = (t[0], t[1], ("node", rowed[chained[i]]))
    elif i in songs:
        t = (t[0], t[1], ("node", sung[(counts, songs[i])]))
    return Generator(t, ("event", edges[counts]), ("plane", reg))


def _singers(songs, streams, edges, nodes):
    """Append one ``Index`` node per song lane a stream reads, and return where each sits.

    One lane is one node however many registers read it, as the player's own cell is: a
    voice's attack/decay and its sustain/release are two reads of one instrument index."""
    at = {}
    for i, key in songs.items():
        k = (streams[i][0], key)
        if at.setdefault(k, len(nodes)) == len(nodes):
            nodes.append(indexer(("SELECT", key[1], ()), ("event", edges[streams[i][0]])))
    return at


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
    watch, cells, roots, arms = _acc_sites(prog)
    sites, addrs = _arr_sites(prog, _prog_env(prog), _walked(prog), diag)
    astmts, tags = _arr_watch(prog, sites, len(watch))
    banks = _banks(prog)
    objs = _objects(prog, banks, diag)
    cstmts, ctags = _cur_watch(prog, objs, len(watch) + len(astmts))
    dstmts, dtags = _div_watch(
        prog,
        len(watch) + len(astmts) + len(cstmts),
        taken={id(s) for s in watch + astmts + cstmts},
    )
    prior = watch + astmts + cstmts + dstmts
    walks = _reload_walks(prog, banks)
    rstmts, rtags = _reload_watch(
        walks,
        _reload_reads(prog, set(walks)),
        len(prior),
        {id(s): i for i, s in enumerate(prior)},
    )
    frames, srcs, wat = frameval.eval_watch(prog, trace, nframes, prior + rstmts)
    ords = [[[] for _f in range(nframes)] for _v in range(3)]
    lww = [{} for _f in range(nframes)]
    order = [[] for _f in range(nframes)]
    for f, (fr, sr) in enumerate(zip(frames, srcs)):
        for (reg, val), src in zip(fr, sr):
            if reg > _FILTER_HI:
                continue
            if reg <= _VOICE_HI and reg % 7 >= 4:
                ords[reg // 7][f].append((reg, val, src))
            else:
                lww[f][reg] = (val, src)
                if reg in order[f]:  # the object's own reads run in the order the machine made
                    order[f].remove(reg)
                order[f].append(reg)
    states = _arr_states(sites, tags, wat, prog.mem0, nframes)
    curs = _cur_states(ctags, wat, objs, banks, prog.mem0, nframes)
    return (
        framelog.canonical(frames),
        ords,
        lww,
        (
            _acc_pools(cells, wat),
            roots,
            (sites, _arr_classes(prog, addrs)),
            states,
            (objs, curs, _beats(ctags, wat, nframes), _decs(dtags, wat, nframes)),
            (arms, wat, order),
            (walks, rtags),
        ),
    )  # the arrangement, the cursors and the dividers ride the same run, all watched


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
    pools, roots, arrs, states, (objs, curs, beats, decs), (arms, wat, order), rel = acc
    lanes = [[], [], []]
    anchor = [None, None, None]
    seqs = {r: [] for r in _FREQ_REGS}
    residual = []
    pre, post, ires = _instr_streams(prog, ords, tabs, banks, objs, curs, diag)
    accs = _accumulators(prog, roots)
    held, kept = _obj_streams(prog, banks, accs, arms, roots, wat, lww, order, len(frames))
    ramps, sweeps, swept = _acc_streams(accs, pools, banks, tabs, lww, prog.mem0, kept)
    held_by = [s | k for s, k in zip(swept, kept)]
    lwws, declared = _lww_streams(lww, tabs, prog.mem0, objs, curs, diag, held_by, banks)
    taken = [d | h for d, h in zip(declared, held_by)]
    notes, seeded = _reload_streams(prog, banks, rel[0], rel[1], wat, lww, len(frames), taken)
    fields = [t | n for t, n in zip(taken, seeded)]
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
    charts = _charts(prog, banks, pitch, diag)
    chained = _chain(streams, (seq, beats), diag)
    songs = {i: k for i, k in _songed(streams, charts, diag).items() if i not in chained}
    durs = _song_durs(charts)
    beats_of = _song_beats(durs, _song_cands(prog, seq), len(frames)) if durs else {}
    edges = {}
    for counts, *_rest in streams + groups + rels + pairs + sweeps:
        edges.setdefault(counts, len(edges))
    for grp in held:
        for node in ([grp.step] if grp.step else []) + list(grp.reads):
            edges.setdefault(node.counts, len(edges))
    for obj in notes:
        for c in (obj.seeds, obj.fires) + tuple(h[0] for h in obj.holds):
            edges.setdefault(c, len(edges))
    for key in chained.values():
        edges.setdefault(key[2], len(edges))
    nodes, clock_at, beaten = [], {}, {}
    for c in edges:  # a clock is one DIV, a cascade of two, the song's own row, or the floor
        got = beats_of.get(c) if sum(c) > 1 else None
        if got is None:
            chain = _clock_node(c, seq, decs)
        else:
            diag["song_fires_generated"] += sum(c)
            chain = [
                div(got[0], phase=got[1]),
                Generator(("DIV", ("node", -1), got[3]), ("event", -1), ("fire",)),
            ]
        for g in chain:
            if g.trigger == ("event", -1):
                g = g._replace(trigger=("event", len(nodes) - 1))
            nodes.append(g)
        clock_at[c] = len(nodes) - 1
        if got is not None:
            beaten[len(nodes) - 1] = got[2]
    rowed = _rowers(chained, clock_at, nodes)
    sung = _singers(songs, streams, clock_at, nodes)
    args = (chained, rowed, clock_at, songs, sung)
    fired = [_planed(i, s, *args) for i, s in enumerate(streams[: len(pre)])]
    nodes += fired + [raw(residual)]
    nodes += [_planed(i + len(pre), s, *args) for i, s in enumerate(streams[len(pre) :])]
    nodes += [
        Generator(t, ("event", clock_at[c]), plane(r, m)) for c, ps, r in groups for t, m in ps
    ]
    nodes += [Generator(t, ("event", clock_at[c]), r) for c, t, r, _reg in sweeps]
    nodes += [lookup(seqs[r], FRAME, r) for r in _FREQ_REGS if any(v is not None for v in seqs[r])]
    for counts, t, reg, op, base in rels:  # absolutes settle a register, relatives follow
        if base[0] == "gen":
            nodes.append(Generator(base[1], ("event", clock_at[counts]), plane(reg)))
            base = ("node", len(nodes) - 1)
        nodes.append(Generator(t, ("event", clock_at[counts]), relative(reg, op, base)))
    for counts, walk, table, reg in pairs:  # the row generator, then the pattern it rows
        nodes.append(indexer(walk, ("event", clock_at[counts])))
        nodes.append(select(table, ("node", len(nodes) - 1), ("event", clock_at[counts]), reg))
    for grp in held:  # the object, then every read of the value it carries
        step = None
        for node in ([grp.step] if grp.step else []) + list(grp.reads):
            nodes.append(indexer(("SELECT", _OFFS, node.rows), ("event", clock_at[node.counts])))
            route = (
                plane(node.lo, node.mask, ("node", len(nodes) - 1))
                if node.hi is None
                else pair(node.lo, node.hi, node.mask, ("node", len(nodes) - 1))
            )
            tr = ("HOLD", step) + node.transfer[2:] if node.transfer[0] == "HOLD" else node.transfer
            nodes.append(Generator(tr, ("event", clock_at[node.counts]), route))
            step = len(nodes) - 1 if node is grp.step else step
    for obj in notes:  # the note's own reload, the walk it seeds, and every read of it
        nodes.append(indexer(("SELECT", obj.table, obj.rows), ("event", clock_at[obj.seeds])))
        nodes.append(
            Generator(
                ("RAMP", ("node", len(nodes) - 1), obj.step, obj.wrap, ()),
                ("event", clock_at[obj.fires]),
                INDEX,
            )
        )
        walk = len(nodes) - 1
        for counts, at, reg in obj.holds:
            nodes.append(
                Generator(("HOLD", walk, obj.first, at), ("event", clock_at[counts]), plane(reg))
            )
    for at, v in beaten.items():  # the row divider's divisor is the row's own duration
        nodes.append(indexer(("SELECT", durs[v], ()), ("event", at)))
        nodes[at] = nodes[at]._replace(
            transfer=("DIV", ("node", len(nodes) - 1), nodes[at].transfer[2])
        )
    _attrition(seq, streams, chained, pairs, nodes, diag)
    return (
        Graph(
            nodes,
            freq_table=pitch,
            classes=_classes(streams, groups, rels, pairs, sweeps, songs, held, notes),
            charts=charts,
        ),
        lanes,
    )


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
