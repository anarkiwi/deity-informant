"""gtoracle — a native editor's own song, mapped onto the universal primitive.

Reads a song out of a real editor's model and expresses it in the primitive
`tracker` uses, so the mapped graph is checkable by the same law. Two editors,
one generic vocabulary; see docs/gt-oracle.md.
"""

from collections import namedtuple

from . import framelog
from . import tracker

Cell = namedtuple("Cell", "kind lane row value step mask base src", defaults=(0xFF, None, None))
Native = namedtuple(
    "Native", "editor tables writes shape notes instrs onsets structure arrangement", defaults=({},)
)
Report = namedtuple(
    "Report", "coverage frames matched divergence offset raw_kinds arrangement", defaults=({},)
)

_VOICE_HI = 0x14
_FILTER_HI = 0x18
_PLANE = {0: "freq", 1: "freq", 2: "pw", 3: "pw", 4: "ctrl", 5: "ad", 6: "sr"}
_CTRL = 4
_FULL = 0xFF
_GATES = (0xFE, 0x00)  # the gate images a ctrl lane byte is read through
_CLASSES = ("lane", "gate", "imm", "ramp", "seed", "mask", "rel")
_COUNTER = ("row", "counter")  # a row counter's "lane": the row is the emit, not a table byte
_NONE = 0x1FF  # a pattern column entry no row declares: it matches no table row
_SW_NOTE_FX = 0x60  # at and above this a SID-Wizard note column is an effect, not a pitch


def _parts(cell):
    """The masked generators one register's byte is assembled from, or the one cell."""
    return (cell,) if isinstance(cell, Cell) else cell


def _value(cell):
    """The byte a cell writes: one generator's, or the fields several assemble."""
    v = 0
    for part in _parts(cell):
        v |= part.value & part.mask
    return v


def _plane_of(reg):
    """Canonical plane class for a SID register offset."""
    if reg <= _VOICE_HI:
        return _PLANE[reg % 7]
    return "filter" if reg <= _FILTER_HI else "tail"


def _is_ord(reg):
    """Is ``reg`` in a voice's order-preserved ctrl/AD/SR section?"""
    return reg <= _VOICE_HI and 4 <= reg % 7 <= 6


def _ctrl_table(lane):
    """A ctrl lane followed by its gate images, so every byte emitted is declared.

    The gate is one bit over a waveform byte in every editor that has one — the
    reading `tracker._key_table` already makes of a recovered ctrl lane."""
    return tuple(lane) + tuple(b & g for g in _GATES for b in lane)


def _bump(counter, why):
    """Price one refusal by name."""
    counter[why] = counter.get(why, 0) + 1


def _patt_src(tables, key, row, want, group, refused, shift=0, why="pattern_row"):
    """The pattern row whose column ``key`` names ``want``, or None, refusal priced.

    A transpose shifts the pitch table's *index* and an index route carries no delta,
    so every emit under a nonzero shift is refused rather than fitted, and counted apart."""
    lane = tables[key]
    if row is None or not 0 <= row < len(lane):
        _bump(refused, "no_" + why)
        return None
    if shift:
        _bump(refused, "transpose")
        return None
    if lane[row] != want:
        _bump(refused, why)
        return None
    return (key, row, group)


def _table_of(tables, cell):
    """The emitted table for a cell's lane, gate images included for ctrl."""
    lane = tables[cell.lane]
    return _ctrl_table(lane) if cell.kind == "ctrl" else lane


# ---- 1. runs: a sweep is a RAMP only where its step is a declared byte ------------
def _runs(seq):
    """``[(start, count)]``: maximal runs of ramp cells sharing one nonzero step.

    A run of one predicts nothing and a step of zero predicts nothing, so both are
    refused for the reason `tracker` refuses them (docs/tracker.md §4c)."""
    out, i = [], 0
    while i < len(seq):
        cell = seq[i]
        if cell is None or cell.kind != "ramp" or not cell.step:
            i += 1
            continue
        j = i + 1
        while j < len(seq) and seq[j] is not None and seq[j].kind == "ramp":
            if seq[j].step != cell.step or seq[j].value != (seq[j - 1].value + cell.step) & 0xFF:
                break
            j += 1
        if j - i >= 2:
            out.append((i, j - i))
        i = max(j, i + 1)
    return out


# ---- 2. the builder: one stream per (register, lane), the rest RAW ----------------
class _Streams:
    """Accumulates ``(counts, transfer, reg, mask, rel, arr)`` streams and their classes."""

    def __init__(self, nframes, tables):
        self.n = nframes
        self.tables = tables
        self.rows = {}
        self.classes = {}
        self.refused = {}

    def _bump(self, reg, kind, n=1):
        cls = self.classes.setdefault(_plane_of(reg), dict.fromkeys(_CLASSES, 0))
        cls[kind] += n

    def add(self, reg, frame, cell, key=None):
        """Record one typed write at ``frame``: one generator's, or a masked group's."""
        group = _parts(cell)
        if len(group) > 1:
            self._bump(reg, "mask")
        for part in group:
            counts, rows, srcs = self.rows.setdefault(
                key or _key(reg, part), ([0] * self.n, [], [])
            )
            counts[frame] += 1
            srcs.append(None if part.src is None else part.src[1])
            if part.kind == "ramp":
                rows.append(part.value)
                continue
            if part.kind == "rel":
                rows.append((part.row, 0 if part.base is None else part.base[1]))
                self._bump(reg, "rel")
                continue
            rows.append(0 if part.kind == "imm" else part.row)
            if len(group) > 1:
                continue
            if part.kind == "imm":
                self._bump(reg, "imm")
            else:
                self._bump(reg, "gate" if part.row >= len(self.tables[part.lane]) else "lane")

    def _feeder(self, key, counts, rows, srcs):
        """``(the column that names this stream's rows, the pattern rows walked)``, or None.

        One index edge carries one row per trigger, so a frame emitting twice has two
        readers of one value and is refused; a row the composer's own column does not
        reproduce is refused with it, since a generated row past the table drops the write."""
        _reg, kind, _lane, _step, _mask, arr = key
        if arr is None or kind not in ("select", "ctrl"):
            return None
        src_lane, _grp = arr
        tab = () if src_lane == _COUNTER else self.tables[src_lane]
        if max(counts) > 1 or any(s is None for s in srcs):
            _bump(self.refused, "two_emits_one_row")
        elif src_lane == _COUNTER:
            return (_COUNTER, tuple(rows))
        elif any(not 0 <= s < len(tab) or tab[s] != r for s, r in zip(srcs, rows)):
            _bump(self.refused, "row_not_declared")
        else:
            return (src_lane, tuple(srcs))
        return None

    def streams(self):
        """``[(counts, transfer, reg, mask, rel, arr)]`` for every stream recorded.

        ``rel`` is ``None`` for an absolute stream, else ``(op, base lane, base rows)``;
        ``arr`` is the arrangement generator whose emit is this stream's row index."""
        out = []
        for key, (counts, rows, srcs) in self.rows.items():
            reg, kind, lane, step, mask = key[:5]
            if kind == "ramp":
                out.append((tuple(counts), ("RAMP", rows[0], step, 0x100), reg, mask, None, None))
                self._bump(reg, "seed")
                self._bump(reg, "ramp", len(rows) - 1)
            elif kind == "imm":
                out.append((tuple(counts), ("SELECT", (lane,), ()), reg, mask, None, None))
            elif kind == "rel":
                delta, base = lane
                out.append(
                    (
                        tuple(counts),
                        ("SELECT", tuple(self.tables[delta]), tuple(r for r, _b in rows)),
                        reg,
                        mask,
                        ("ADD" if step > 0 else "SUB", base, tuple(b for _r, b in rows)),
                        None,
                    )
                )
            else:
                table = _ctrl_table(self.tables[lane]) if kind == "ctrl" else self.tables[lane]
                feeder = self._feeder(key, counts, rows, srcs)
                out.append(
                    (
                        tuple(counts),
                        ("SELECT", tuple(table), tuple(rows)),
                        reg,
                        mask,
                        None,
                        feeder and feeder + (key[5][1],),
                    )
                )
        return out


def _one_typed(cell, value, tables):
    """Is one generator's emit ``value``, with the composer's table byte agreeing?

    A relative cell is never typed here: its byte is a delta over a base, so only
    `_rel_keys` — which combines the two and checks the result — can admit it."""
    if cell is None or cell.value != value or cell.kind in ("raw", "rel"):
        return False
    if cell.kind in ("imm", "ramp"):
        return True
    table = _table_of(tables, cell)
    return 0 <= cell.row < len(table) and table[cell.row] == value


def _typed(cell, value, tables):
    """Is ``cell`` a generator emit of ``value``, with the declared byte agreeing?

    The pair `tracker._lane_key` applies: the emit must equal what the register
    took, and the table byte at the recovered row must equal it too. A masked group
    types where its fields are disjoint and assemble exactly that byte."""
    if cell is None or value is None:
        return False
    parts, owned = _parts(cell), 0
    for part in parts:
        if not (0 < part.mask <= _FULL) or owned & part.mask:
            return False
        owned |= part.mask
        if not _one_typed(part, value & part.mask if len(parts) > 1 else value, tables):
            return False
    return _value(cell) == value


def _key(reg, cell):
    """The stream key one cell belongs to; a masked field owns its own stream.

    An arranged cell is keyed by the arrangement too, so one pattern is one shared
    subgraph and the song steps that reuse it fire the same node."""
    if cell.kind == "imm":
        return (reg, "imm", cell.value, 0, cell.mask, None)
    if cell.kind == "rel":  # a relative stream is keyed by both its lanes and its sign
        base = None if cell.base is None else cell.base[0]
        return (reg, "rel", (cell.lane, base), cell.step, cell.mask, None)
    return (reg, cell.kind, cell.lane, 0, cell.mask, cell.src and cell.src[::2])


def _rel_keys(native, frames, tables):
    """``{(frame, reg)}`` the relative cells the composer's own tables predict.

    The delta is a byte of the song's table at the row the player read; the base is the
    plane's own previous byte, or a second table's byte where the cell names one. A
    delta of zero predicts nothing and is refused, as `_runs` refuses a zero step."""
    out, prev = set(), {}
    for f, writes in enumerate(frames):
        for reg, val in writes:
            cell, was = native.writes[f].get(reg), prev.get(reg)
            prev[reg] = val
            if not isinstance(cell, Cell) or cell.kind != "rel" or cell.value != val:
                continue
            lane = tables[cell.lane]
            if not 0 <= cell.row < len(lane) or not lane[cell.row]:
                continue
            if cell.base is None:
                base = was
            else:
                blane, brow = cell.base
                base = tables[blane][brow] if 0 <= brow < len(tables[blane]) else None
            if base is None:
                continue
            got = base + lane[cell.row] if cell.step > 0 else base - lane[cell.row]
            if got & _FULL == val:
                out.add((f, reg))
    return out


def _ramp_keys(native, frames, tables):
    """``{(frame, reg): (run start, seed)}`` the sweep RAMPs claim.

    A run is claimed whole or not at all: one emit whose step no table names, or a
    value the arithmetic does not predict, refuses the run entire."""
    seqs, obs, claimed = {}, {}, {}
    for f, writes in enumerate(frames):
        for reg, val in writes:
            cell = native.writes[f].get(reg)
            one = cell if isinstance(cell, Cell) else None
            seqs.setdefault(reg, [None] * len(frames))[f] = one
            obs.setdefault(reg, {})[f] = val
    for reg, seq in seqs.items():
        for at, n in _runs(seq):
            if all(_typed(seq[f], obs[reg].get(f), tables) for f in range(at, at + n)):
                for f in range(at, at + n):
                    claimed[(f, reg)] = (at, seq[at].value)
    return claimed


def _subseq(short, long):
    """Is ``short`` a subsequence of ``long``?"""
    it = iter(long)
    return all(x in it for x in short)


def _layout(frames):
    """Per voice, a register order every frame's ord section is a subsequence of.

    A frame that writes only ctrl and one that writes sr/ad/ctrl are then both
    renderable from one node layout — the stream simply does not fire."""
    out = []
    for v in range(3):
        secs = [tuple(r for r, _x in w if _is_ord(r) and r // 7 == v) for w in frames]
        regs = {r for s in secs for r in s}
        after = {r: set() for r in regs}
        for sec in secs:
            for i, a in enumerate(sec):
                after[a].update(sec[i + 1 :])
        order = tuple(sorted(regs, key=lambda r, a=after: (-len(a[r]), r)))
        counts = {}
        for sec in secs:
            counts[sec] = counts.get(sec, 0) + 1
        out.append(
            order
            if all(_subseq(s, order) for s in secs)
            else (max(counts, key=counts.get) if counts else ())
        )
    return out


def _counter(walked):
    """The row counter: a `RAMP` where the walk steps evenly, else the unrolled walk.

    `RAMP` wraps at its bound and a straight-through `SELECT` at the end of its table, so
    a walk that restarts inside the window is carried unrolled, not by a wrong back-edge."""
    step = walked[1] - walked[0] if len(walked) > 1 else 0
    if step and all(b - a == step for a, b in zip(walked, walked[1:])):
        return ("RAMP", walked[0], step, 0)
    return ("SELECT", tuple(walked), ())


def _arranged(nodes, counts, transfer, route, arr, ctx):
    """Append the arrangement chain that generates a stream's rows, then its plane node.

    The row counter names the pattern row, the pattern generator reads its own column
    at that row, and the plane generator reads its table at what the pattern names —
    one index edge per link, and one chain shared by every reader of the same walk."""
    tables, edges, census, made = ctx
    src_lane, walked, group = arr
    trig = ("event", edges[counts])
    key = (counts, src_lane, walked)
    if key not in made:
        nodes.append(tracker.indexer(_counter(walked), trig))
        if src_lane != _COUNTER:  # the pattern's own column, read at the row the counter names
            nodes.append(
                tracker.indexer(("SELECT", tuple(tables[src_lane]), ("node", len(nodes) - 1)), trig)
            )
        made[key] = len(nodes) - 1
    nodes.append(tracker.Generator(transfer[:2] + (("node", made[key]),), trig, route))
    census["groups"].add(group)
    census["rows"].update((group, r) for r in walked)
    census["emits"] += sum(counts)


def _build(frames, native):
    """``(Graph, residual, arrangement census)`` reproducing ``frames`` from the song.

    A voice's order-preserved section is typed whole or replayed whole, since its
    streams render as whole buckets in node order; the last-write-wins registers
    are typed per write, because the projection sorts them."""
    tables, n = native.tables, len(frames)
    acc, ramps, layout = _Streams(n, tables), _ramp_keys(native, frames, tables), _layout(frames)
    rels = _rel_keys(native, frames, tables)
    residual = []
    for f, writes in enumerate(frames):
        sec = [[] for _v in range(3)]
        for reg, val in writes:
            if _is_ord(reg):
                sec[reg // 7].append((reg, val))
        ok = [
            _subseq(tuple(r for r, _v in sec[v]), layout[v])
            and all(_typed(native.writes[f].get(r), val, tables) for r, val in sec[v])
            for v in range(3)
        ]
        rest = []
        for reg, val in writes:
            cell = native.writes[f].get(reg)
            if _is_ord(reg):
                if ok[reg // 7]:
                    acc.add(reg, f, cell)
                else:
                    rest.append((reg, val))
            elif (f, reg) in ramps:
                at, seed = ramps[(f, reg)]
                acc.add(
                    reg, f, cell._replace(value=seed), key=(reg, "ramp", at, cell.step, _FULL, None)
                )
            elif (f, reg) in rels:
                acc.add(reg, f, cell)
            elif cell is not None and _parts(cell)[0].kind != "ramp" and _typed(cell, val, tables):
                acc.add(reg, f, cell)
            else:
                rest.append((reg, val))
        residual.append(tuple(rest))
    streams = acc.streams()
    pos = {r: (v, i) for v in range(3) for i, r in enumerate(layout[v])}
    edges = {}
    for counts, *_rest in streams:
        edges.setdefault(counts, len(edges))
    nodes = [tracker.edge(c) for c in edges]
    census = {"groups": set(), "rows": set(), "emits": 0, "refused": acc.refused}
    ctx = (tables, edges, census, {})
    for c, t, r, m, _x, arr in sorted(
        (s for s in streams if _is_ord(s[2])), key=lambda s: pos[s[2]]
    ):
        if arr is None:
            nodes.append(tracker.Generator(t, ("event", edges[c]), tracker.plane(r, m)))
        else:
            _arranged(nodes, c, t, tracker.plane(r, m), arr, ctx)
    nodes.append(tracker.raw(residual))
    for c, t, r, m, rel, arr in streams:
        if _is_ord(r):
            continue
        if arr is not None:
            _arranged(nodes, c, t, tracker.plane(r, m), arr, ctx)
            continue
        if rel is None:
            nodes.append(tracker.Generator(t, ("event", edges[c]), tracker.plane(r, m)))
            continue
        op, blane, brows = rel
        base = ("prev",)
        if blane is not None:  # the base is a generator of its own, consumed not written
            nodes.append(
                tracker.Generator(
                    ("SELECT", tuple(tables[blane]), brows),
                    ("event", edges[c]),
                    tracker.plane(r, m),
                )
            )
            base = ("node", len(nodes) - 1)
        nodes.append(tracker.Generator(t, ("event", edges[c]), tracker.relative(r, op, base, m)))
    return tracker.Graph(nodes, classes=acc.classes), residual, census


def _frames_of(records):
    """Per-frame ordered write lists of a canonical record list."""
    return [[w for sec in rec for w in sec] for rec in records]


def _predicted(native, records=None, offset=0):
    """The write list the native song and its driver predict, frame by frame.

    The *schedule* — which registers a frame writes, in what order — is the
    driver's, taken from the projection where one is given, exactly as `tracker`
    takes its `EDGE` counts; every *byte* is the song's."""
    shapes = list(native.shape)
    if records is not None:
        n = max(0, min(len(records) - offset, len(shapes)))
        shapes = [tuple(r for r, _v in _frames_of([records[offset + f]])[0]) for f in range(n)]
    return [
        [(r, _value(native.writes[f][r])) for r in shape if r in native.writes[f]]
        for f, shape in enumerate(shapes)
    ]


def _raw_kinds(native, residual):
    """``{lane or reason: emits}`` for the writes that stayed in RAW."""
    out = {}
    for f, row in enumerate(residual):
        for reg, _v in row:
            cell = native.writes[f].get(reg)
            group = () if cell is None else _parts(cell)
            kind = "mask" if len(group) > 1 else (group[0].kind if group else None)
            key = "unmapped" if kind is None else "%s:%s" % (kind, group[0].lane)
            out[key] = out.get(key, 0) + 1
    return out


def index_nodes(built):
    """``(index-routed nodes, SELECTs whose row a generator supplies)`` of any graph.

    The one predicate the structure axis asks of a graph, and it asks it of the
    recovery's as well as the oracle's — a row is generated or it is observed."""
    nodes = built.nodes
    gen = sum(
        1
        for g in nodes
        if g.transfer[0] == "SELECT" and len(g.transfer[2]) == 2 and g.transfer[2][0] == "node"
    )
    return sum(1 for g in nodes if g.route == tracker.INDEX), gen


def arrangement(census, native):
    """What the mapped graph represents of the composer's arrangement, and what it refused.

    Two denominators are kept apart: the whole song's patterns/rows/entries, and the
    part of it the editor's own player reached inside the measured window."""
    walk = native.arrangement or {}
    groups, rows = census["groups"], census["rows"]
    steps = walk.get("steps", set())
    out = {
        "patterns": len({p for _v, p in groups}),
        "pattern_rows": len({(p, r) for (_v, p), r in rows}),
        "orderlist_entries": sum(1 for v, _s, p in steps if (v, p) in groups),
        "emits": census["emits"],
        "walked_patterns": len({p for _v, _s, p in steps}),
        "walked_rows": len({(p, r) for _v, p, r in walk.get("rows", ())}),
        "walked_steps": len(steps),
        "song_patterns": native.structure.get("patterns", 0),
        "song_rows": native.structure.get("pattern_rows", 0),
        "song_orderlist": native.structure.get("orderlist_entries", 0),
        "loop_at_end": walk.get("loop_at_end", 0),
        "loop_elsewhere": walk.get("loop_elsewhere", 0),
    }
    for why, n in list(census["refused"].items()) + list(walk.get("refused", {}).items()):
        out["refused_%s" % why] = out.get("refused_%s" % why, 0) + n
    return out


def align(records, native, span=4):
    """Driver frame offset: which projection frame the native player's frame 0 is.

    A packed driver's first play call emits the init tail rather than a song frame,
    so the two clocks start a fixed distance apart. Searched, never assumed."""
    best, key, pred = 0, -1, [set(w) for w in _predicted(native)]
    for off in range(span):
        m = max(0, min(len(records) - off, len(pred)))
        hit = sum(len(pred[f] & set(_frames_of([records[f + off]])[0])) for f in range(m))
        if hit > key:
            best, key = off, hit
    return best


def graph(records, native, offset=None):
    """``(Graph, Report)``: the admitted oracle — song tables, observed residual.

    Every emit is a byte of the composer's own table at a row the editor's player
    reached, admitted only where it equals the byte the projection wrote."""
    off = align(records, native) if offset is None else offset
    n = max(0, min(len(records) - off, len(native.shape)))
    win = records[off : off + n]
    g, residual, census = _build(_frames_of(win), native)
    div = framelog.diff(tracker.eval_graph(g, n), win)
    return g, Report(
        tracker.coverage(g, n),
        n,
        n if div is None else div.frame,
        div,
        off,
        _raw_kinds(native, residual),
        arrangement(census, native),
    )


def strict(native, records=None, offset=0):
    """``(Graph, Report)``: the strict oracle — every byte from the song, none observed.

    RAW here replays what the native model predicts, so the law is a real test and
    `Report.matched` is how many frames the song data alone reproduces."""
    pred = _predicted(native, records, offset)
    n, div = len(pred), None
    g, residual, census = _build(pred, native)
    if records is not None:
        div = framelog.diff(tracker.eval_graph(g, n), records[offset : offset + n])
    return g, Report(
        tracker.coverage(g, n),
        n,
        n if div is None else div.frame,
        div,
        offset,
        _raw_kinds(native, residual),
        arrangement(census, native),
    )


# ---- 3. GoatTracker: the model, and the driver that reads it ----------------------
def gt_available():
    """Is `pygoattracker` importable? The oracle is an optional extra."""
    try:
        import pygoattracker  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
    except ImportError:
        return False
    return True


def _gt_probe_class():
    """The instrumented GoatTracker player, built lazily off the optional import.

    Records where each register's byte came from, and keeps the packed driver's
    wider pulse ghost: gt2reloc holds the whole set-pulse byte in $D403."""
    from pygoattracker import constants as gtc  # pylint: disable=import-outside-toplevel
    from pygoattracker.player import Player  # pylint: disable=import-outside-toplevel

    class _Probe(Player):
        """A GoatTracker player naming the table cell behind every register."""

        def _init(self, subtune):
            self.src = [{} for _ in range(3)]
            self.fsrc = {}
            self.newrow = [0, 0, 0]
            self.pattbase = [0] * gtc.MAX_PATT
            self.reads = []
            self.lreads = []
            self.onset = [0, 0, 0]
            self.noterow = [(None, 0)] * 3  # the pattern row and transpose naming the note
            self.pendrow = [(None, 0)] * 3
            self.insrow = [None, None, None]  # the pattern row naming the instrument
            super()._init(subtune)
            self.vol0 = self._masterfader  # the editor's own default master volume

        def _rt(self, table, ptr):
            val = super()._rt(table, ptr)
            self.reads.append((table, (ptr - 1) & 0xFF, val))
            return val

        def _lt(self, table, ptr):
            val = super()._lt(table, ptr)
            self.lreads.append((table, (ptr - 1) & 0xFF, val))
            return val

        @property
        def channels(self):
            """The per-voice playroutine state, under a public name."""
            return self._channels

        def _voice(self, chan):
            return self._channels.index(chan)

        def _pulse_exec(self, chan):
            if self.simplepulse:
                super()._pulse_exec(chan)
                return
            ptbl, v = gtc.PTBL, self._voice(chan)
            if self._lt(ptbl, chan.pulse_table_ptr) == gtc.TABLEJUMP:
                chan.pulse_table_ptr = self._rt(ptbl, chan.pulse_table_ptr)
                if not chan.pulse_table_ptr:
                    return
            if not chan.pulsetime:
                left = self._lt(ptbl, chan.pulse_table_ptr)
                if left >= 0x80:
                    self.src[v]["pulse"] = ("set", chan.pulse_table_ptr - 1, 0)
                    chan.pulse = (left << 8) | self._rt(ptbl, chan.pulse_table_ptr)
                    chan.pulse_table_ptr = (chan.pulse_table_ptr + 1) & 0xFF
                    return
                chan.pulsetime = left
            if chan.pulsetime:
                speed = self._rt(ptbl, chan.pulse_table_ptr)
                self.src[v]["pulse"] = ("mod", chan.pulse_table_ptr - 1, speed)
                chan.pulse = (chan.pulse + (speed - 0x100 if speed >= 0x80 else speed)) & 0xFFFF
                chan.pulsetime -= 1
                if not chan.pulsetime:
                    chan.pulse_table_ptr = (chan.pulse_table_ptr + 1) & 0xFF

        def _new_note_init(self, channel, chan, iptr):
            self.onset[channel] = 1
            self.noterow[channel] = self.pendrow[channel]
            super()._new_note_init(channel, chan, iptr)
            if chan.newcommand == gtc.CMD_TONEPORTA:
                return
            num = chan.instr & 0x3F
            self.src[channel]["ad"] = (("ins", "ad"), num, 0)
            self.src[channel]["sr"] = (("ins", "sr"), num, 0)
            if iptr.firstwave and iptr.firstwave < 0xFE:
                self.src[channel]["wave"] = (("ins", "firstwave"), num, 0)

        def _get_new_notes(self, channel, chan):
            self.newrow[channel] = self.pattbase[chan.pattnum] + max(0, chan.pattptr) // 4
            was = chan.instr
            super()._get_new_notes(channel, chan)
            if chan.instr != was:  # this row's instrument column named the bank row
                self.insrow[channel] = self.newrow[channel]
            if chan.newnote:
                self.pendrow[channel] = (self.newrow[channel], chan.trans)
            if chan.newnote and chan.newcommand != gtc.CMD_TONEPORTA:
                if not self._instr[chan.instr & 0x3F].gatetimer & 0xC0:
                    self.src[channel]["ad"] = (("hr", "ad"), 0, 0)
                    self.src[channel]["sr"] = (("hr", "sr"), 0, 0)

        def _tick0_command(self, channel, chan, iptr):
            cmd = chan.newcommand
            super()._tick0_command(channel, chan, iptr)
            for want, fld in (
                (gtc.CMD_SETAD, "ad"),
                (gtc.CMD_SETSR, "sr"),
                (gtc.CMD_SETWAVE, "wave"),
            ):
                if cmd == want:
                    self.src[channel][fld] = (("patt", "data"), self.newrow[channel], 0)
            if cmd == gtc.CMD_SETMASTERVOL:
                self.fsrc["vol"] = (("patt", "data"), self.newrow[channel], 0)

        def _wave_command(self, channel, chan, command):
            row = chan.wave_table_ptr - 1
            super()._wave_command(channel, chan, command)
            for want, fld in (
                (gtc.CMD_SETAD, "ad"),
                (gtc.CMD_SETSR, "sr"),
                (gtc.CMD_SETWAVE, "wave"),
            ):
                if command == want:
                    self.src[channel][fld] = (("wtbl", "right"), row, 0)
            if command == gtc.CMD_SETMASTERVOL:
                self.fsrc["vol"] = (("wtbl", "right"), row, 0)

        def _wave_exec(self, channel, chan):
            ptr = chan.wave_table_ptr
            row = ptr - 1
            wave = self._lt(gtc.WTBL, ptr) if ptr else 0
            note = self._rt(gtc.WTBL, ptr) if ptr else 0x80
            out = super()._wave_exec(channel, chan)
            if ptr and gtc.WAVELASTDELAY < wave < gtc.WAVECMD:
                lane = "silent" if wave >= gtc.WAVESILENT else "left"
                self.src[channel]["wave"] = (("wtbl", lane), row, 0)
            if out and note != 0x80:
                self.src[channel]["freq"] = (("pitch", None), chan.lastnote, 0)
            return out

        def _toneporta_reached(self, chan):
            super()._toneporta_reached(chan)
            self.src[self._voice(chan)]["freq"] = (("pitch", None), chan.note, 0)

        def _porta_up(self, chan, idx):
            step = self._speed_value(idx, chan)
            super()._porta_up(chan, idx)
            self.src[self._voice(chan)]["freq"] = (("stbl", None), idx, step)

        def _porta_down(self, chan, idx):
            step = self._speed_value(idx, chan)
            super()._porta_down(chan, idx)
            self.src[self._voice(chan)]["freq"] = (("stbl", None), idx, -step)

        def _vibrato(self, chan, idx):
            super()._vibrato(chan, idx)
            left = self._ltable[gtc.STBL][(idx - 1) & 0xFF] if idx else 0x80
            self.src[self._voice(chan)]["freq"] = (
                (("stbl", "right"), (idx - 1) & 0xFF, -1 if chan.vibtime & 1 else 1)
                if idx and left < 0x80  # bit 7 computes the step off the note interval
                else (("vibrato", None), idx, 0)
            )

        def _filter_routine(self):
            # pylint: disable=attribute-defined-outside-init
            self.reads, self.lreads = [], []
            cut, ctl = self._filtercutoff, self._filterctrl
            super()._filter_routine()
            rd = [(r, v) for t, r, v in self.reads if t == gtc.FTBL]
            got = _pick(rd, self._filterctrl)
            if got is not None and (self._filterctrl != ctl or "ctrl" not in self.fsrc):
                self.fsrc["ctrl"] = got
            step = _pick(rd, (self._filtercutoff - cut) & 0xFF, self._filtercutoff != cut)
            got = _pick(rd, self._filtercutoff) or step
            if got is not None:
                self.fsrc["cutoff"] = got
            lt = [(r, v) for t, r, v in self.lreads if t == gtc.FTBL and v >= 0x80]
            got = _pick([(r, v & 0x70) for r, v in lt], self._filtertype)
            if got is not None:  # the set row the mode nibble was masked out of
                self.fsrc["type"] = (("ftbl", "type"),) + got[1:]

    return _Probe


def _pick(reads, value, step=False):
    """The first filter-table cell read this frame holding ``value``, else None.

    Provenance among the cells the machine actually read, the basis
    `tracker._acc_pools` takes for a sweep's step (docs/tracker.md §4c)."""
    for row, got in reads:
        if got == value:
            return (("ftbl", "right"), row, value if step else 0)
    return None


_GT_LANES = (("ad", "attack_decay"), ("sr", "sustain_release"), ("firstwave", "first_wave"))


def _gt_tables(song, adparam, freq_table, vol0=0x0F):
    """``(tables, pattern bases)``: the song's byte tables under generic names.

    `pitch` is the note→freq table, `ins` the instrument bank, `hr` the ADSR
    preamble a note-on edge emits, `wtbl`/`ptbl`/`ftbl` the three programs, and
    `ftbl.type`/`vol.master` the two fields of $18 (§4.3)."""
    tables = {
        ("pitch", "lo"): tuple(w & 0xFF for w in freq_table),
        ("pitch", "hi"): tuple((w >> 8) & 0xFF for w in freq_table),
        ("hr", "ad"): ((adparam >> 8) & 0xFF,),
        ("hr", "sr"): (adparam & 0xFF,),
        ("vol", "master"): (vol0 & 0x0F,),
    }
    for name, attr in _GT_LANES:
        tables[("ins", name)] = tuple(getattr(song.instrument(i), attr) & 0xFF for i in range(64))
    for key, tab in (
        ("wtbl", song.wavetable),
        ("ptbl", song.pulsetable),
        ("ftbl", song.filtertable),
    ):
        tables[(key, "left")] = tuple(b & 0xFF for b in tab.left)
        tables[(key, "right")] = tuple(b & 0xFF for b in tab.right)
    tables[("stbl", "right")] = tuple(b & 0xFF for b in song.speedtable.right)
    tables[("wtbl", "silent")] = tuple(b & 0x0F for b in song.wavetable.left)
    tables[("ftbl", "type")] = tuple(b & 0x70 for b in song.filtertable.left)
    from pygoattracker import constants as gtc  # pylint: disable=import-outside-toplevel

    data, note, instr, base, cur = [], [], [], [], 0
    for pat in song.patterns:
        base.append(cur)
        data += [r.data & 0xFF for r in pat.rows]
        note += [(r.note - gtc.FIRSTNOTE) & 0xFF for r in pat.rows]
        instr += [r.instrument & 0x3F for r in pat.rows]
        cur += len(pat.rows)
    tables[("patt", "data")] = tuple(data)
    tables[("patt", "note")] = tuple(note)  # the driver's own reading: newnote - FIRSTNOTE
    tables[("patt", "instr")] = tuple(instr)
    return tables, base


_GT_SHAPE = tuple(
    r
    for v in range(3)
    for r in (7 * v, 7 * v + 1, 7 * v + 2, 7 * v + 3, 7 * v + 6, 7 * v + 5, 7 * v + 4)
) + (0x16, 0x17, 0x18)


def gt_native(song, info, subtune, nframes, adparam=None):
    """A `Native` for one GoatTracker subtune: tables, per-frame cells, structure.

    Runs the editor's own playroutine and records, per register it writes, which
    table lane and row the byte came from — provenance off the machine."""
    from pygoattracker import constants as gtc  # pylint: disable=import-outside-toplevel

    par = gtc.DEFAULT_ADPARAM if adparam is None else adparam
    player = _gt_probe_class()(
        song,
        subtune=subtune,
        adparam=par,
        freq_table=info.freq_table or None,
        simplepulse=info.simplepulse,
        live_vibrato=info.live_vibrato,
    )
    tables, pattbase = _gt_tables(song, par, info.freq_table or gtc.FREQ_TABLE, player.vol0)
    player.pattbase[: len(pattbase)] = pattbase
    writes, notes, instrs, onsets = [], [], [], []
    walk = {"steps": set(), "rows": set(), "refused": {}}
    for _f in range(nframes):
        player.onset = [0, 0, 0]  # pylint: disable=attribute-defined-outside-init
        player.play_frame()
        cells = {}
        for v in range(3):
            _gt_voice_cells(player, tables, v, cells, walk["refused"])
        _gt_filter_cells(player, cells)
        writes.append(cells)
        chans = player.channels
        for v, chan in enumerate(chans):
            walk["steps"].add((v, chan.songptr, chan.pattnum))
            walk["rows"].add((v, chan.pattnum, player.newrow[v]))
        notes.append(tuple(c.lastnote for c in chans))
        instrs.append(tuple(c.instr & 0x3F for c in chans))
        onsets.append(tuple(player.onset))
    walk.update(_gt_loops(song, subtune))
    return Native(
        "goattracker",
        tables,
        writes,
        [_GT_SHAPE] * nframes,
        notes,
        instrs,
        onsets,
        _gt_structure(song, subtune),
        walk,
    )


def _gt_loops(song, subtune):
    """Where each channel's orderlist loops back to: the table's start, or elsewhere.

    A `SELECT` wraps at the end of its table, so the wrap IS the back-edge only
    for a channel whose restart is entry 0; any other restart needs the walk unrolled."""
    chans = song.subtunes[subtune].channels
    at_end = sum(1 for c in chans if not c.restart)
    return {"loop_at_end": at_end, "loop_elsewhere": len(chans) - at_end}


def _gt_freq_cells(regs, base, freq, cells, src=None):
    """freq_lo/freq_hi: the pitch table at a note row, a portamento RAMP, or a vibrato step.

    Vibrato adds or subtracts one speedtable byte to the frequency the plane already
    holds, which is the relative route over ``Prev``; the high byte moves only on carry."""
    for off, lane in ((0, ("pitch", "lo")), (1, ("pitch", "hi"))):
        val = regs[base + off]
        if freq and freq[0] == ("pitch", None):
            cells[base + off] = Cell("select", lane, freq[1], val, 0, src=src)
        elif freq and freq[0] == ("stbl", None):
            cells[base + off] = (
                Cell("ramp", ("pitch", "lo"), freq[1], val, freq[2] & 0xFF)
                if off == 0
                else Cell("raw", ("porta", "carry"), 0, val, 0)
            )
        elif freq and freq[0] == ("stbl", "right"):
            cells[base + off] = (
                Cell("rel", ("stbl", "right"), freq[1], val, freq[2])
                if off == 0
                else Cell("raw", ("vibrato", "carry"), 0, val, 0)
            )
        else:
            cells[base + off] = Cell("raw", freq[0] if freq else ("ghost", "freq"), 0, val, 0)


def _gt_pulse_cells(regs, base, pulse, cells):
    """pw_lo/pw_hi: a pulse-table set step, or the sweep the same table steps."""
    for off, lane in ((2, ("ptbl", "right")), (3, ("ptbl", "left"))):
        val = regs[base + off]
        if pulse and pulse[0] == "set":
            cells[base + off] = Cell("select", lane, pulse[1], val, 0)
        elif pulse and pulse[0] == "mod":
            cells[base + off] = (
                Cell("ramp", ("ptbl", "right"), pulse[1], val, pulse[2])
                if off == 2
                else Cell("select", lane, pulse[1] - 1, val, 0)
            )
        else:
            cells[base + off] = Cell("raw", ("ghost", "pw"), 0, val, 0)


def _gt_row_src(tables, lane, row, player, v, group, refused):
    """Where a voice register's own row comes from: a pattern column, or nowhere.

    The pattern's data column is walked by a row counter; its instrument column names
    a row of the bank, which is the same index link one step further up."""
    if lane[0] == "patt":
        return (_COUNTER, row, group)
    if lane[0] == "ins":
        return _patt_src(
            tables, ("patt", "instr"), player.insrow[v], row, group, refused, 0, "instrument_row"
        )
    return None


def _gt_voice_cells(player, tables, v, cells, refused=None):
    """The seven voice registers of one channel, each named by its table cell."""
    base, regs, src = 7 * v, player.regs, player.src[v]
    refused = {} if refused is None else refused
    group, freq = (v, player.channels[v].pattnum), src.get("freq")
    nsrc = None
    if freq and freq[0] == ("pitch", None):
        row, shift = player.noterow[v]
        nsrc = _patt_src(tables, ("patt", "note"), row, freq[1], group, refused, shift, "arpeggio")
    _gt_freq_cells(regs, base, freq, cells, nsrc)
    _gt_pulse_cells(regs, base, src.get("pulse"), cells)
    wave, val = src.get("wave"), regs[base + _CTRL]
    if wave is not None and wave[0] in tables and 0 <= wave[1] < len(tables[wave[0]]):
        lane = tables[wave[0]]
        row = next(
            (
                wave[1] + (1 + i) * len(lane)
                for i, g in enumerate(_GATES)
                if lane[wave[1]] & g == val
            ),
            wave[1],
        )
        plain = lane[wave[1]] == val
        cells[base + _CTRL] = Cell(
            "ctrl",
            wave[0],
            wave[1] if plain else row,
            val,
            0,
            src=_gt_row_src(tables, wave[0], wave[1], player, v, group, refused) if plain else None,
        )
    else:
        cells[base + _CTRL] = Cell("raw", ("ghost", "ctrl"), 0, val, 0)
    for off, field in ((5, "ad"), (6, "sr")):
        got, val = src.get(field), regs[base + off]
        cells[base + off] = (
            Cell(
                "select",
                got[0],
                got[1],
                val,
                0,
                src=_gt_row_src(tables, got[0], got[1], player, v, group, refused),
            )
            if got is not None and got[0] in tables
            else Cell("raw", ("ghost", "adsr"), 0, val, 0)
        )


def _mode_vol(regs, fsrc, reg=0x18):
    """$18 as two masked generators: the mode nibble and the master volume.

    The mode is the filter program's set row masked as the driver masks it; the
    volume is the byte a `SETMASTERVOL` names, or the editor's own default. A
    register no song datum reaches is the driver's ghost and stays RAW (§4.6)."""
    typ, vol = fsrc.get("type"), fsrc.get("vol", (("vol", "master"), 0, 0))
    if typ is None:
        return Cell("raw", ("mode", "vol"), 0, regs[reg], 0)
    return (
        Cell("select", typ[0], typ[1], regs[reg] & 0x70, 0, 0x70),
        Cell("select", vol[0], vol[1], regs[reg] & 0x0F, 0, 0x0F),
    )


def _gt_filter_cells(player, cells):
    """The three global filter registers the driver writes each frame.

    $18 is a mode nibble ORed with a volume level — two generators, one plane —
    which the masked route expresses as two disjoint fields (§4.3)."""
    regs, cut, ctl = player.regs, player.fsrc.get("cutoff"), player.fsrc.get("ctrl")
    cells[0x15] = Cell("raw", ("ghost", "filter"), 0, regs[0x15], 0)
    if cut and cut[2]:
        cells[0x16] = Cell("ramp", ("ftbl", "right"), cut[1], regs[0x16], cut[2])
    elif cut:
        cells[0x16] = Cell("select", ("ftbl", "right"), cut[1], regs[0x16], 0)
    else:
        cells[0x16] = Cell("raw", ("ghost", "filter"), 0, regs[0x16], 0)
    cells[0x17] = (
        Cell("select", ("ftbl", "right"), ctl[1], regs[0x17], 0)
        if ctl
        else Cell("raw", ("ghost", "filter"), 0, regs[0x17], 0)
    )
    cells[0x18] = _mode_vol(regs, player.fsrc)


def _gt_structure(song, subtune):
    """Counts of the arrangement the composer wrote, for the structure comparison."""
    chans = song.subtunes[subtune].channels
    return {
        "patterns": len(song.patterns),
        "pattern_rows": sum(len(p.rows) for p in song.patterns),
        "orderlist_entries": sum(len(c.entries) for c in chans),
        "instruments": len(song.instruments),
        "wavetable": len(song.wavetable),
        "pulsetable": len(song.pulsetable),
        "filtertable": len(song.filtertable),
        "speedtable": len(song.speedtable),
    }


def gt_decompile(path, subtune=0):
    """``(song, info, subtune)`` for a GT-packed ``.sid``, or raise `SidParseError`."""
    from pygoattracker import sid as gtsid  # pylint: disable=import-outside-toplevel

    res = gtsid.decompile_sid(str(path), subtune=subtune)
    return res.song, res.info, min(subtune, len(res.song.subtunes) - 1)


# ---- 4. SID-Wizard: the same generic lanes, a different editor's spelling ---------
def sw_available():
    """Is `pysidwizard` importable? The oracle is an optional extra."""
    try:
        import pysidwizard  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
    except ImportError:
        return False
    return True


def _sw_tables(swm, freq_lo, freq_hi):
    """``(tables, instrument program bases)``: SID-Wizard's model in generic lanes.

    `hr` is the ADSR preamble four ``hr_*`` fields spell — the structure
    GoatTracker spells as one gate-off timer; `wf` is the per-instrument program,
    flattened into one bank so a row names a step."""
    ins = swm.instruments
    tables = {
        ("pitch", "lo"): tuple(freq_lo),
        ("pitch", "hi"): tuple(freq_hi),
        ("ins", "ad"): tuple(((i.attack & 0xF) << 4) | (i.decay & 0xF) for i in ins),
        ("ins", "sr"): tuple(((i.sustain & 0xF) << 4) | (i.release & 0xF) for i in ins),
        ("hr", "ad"): tuple(((i.hr_attack & 0xF) << 4) | (i.hr_decay & 0xF) for i in ins),
        ("hr", "sr"): tuple(((i.hr_sustain & 0xF) << 4) | (i.hr_release & 0xF) for i in ins),
        ("ins", "firstwave"): tuple(i.first_waveform & 0xFF for i in ins),
        ("chord", "step"): tuple(swm.chord_table),
        ("ins", "octave"): tuple(i.octave_shift & 0xFF for i in ins),
        ("vol", "master"): (0x0F,),
    }
    prog, base, fbase, cur = [], [], [], 0
    for i in ins:
        base.append(cur)
        img = i.table_image()
        fbase.append(cur + len(img) - len(i.filter_table))
        prog += list(img)
        cur += len(img)
    tables[("wf", "left")] = tuple(prog)
    tables[("wf", "hi7")] = tuple(b & 0x7F for b in prog)
    tables[("wf", "res")] = tuple((b & 0x0F) << 4 for b in prog)
    tables[("wf", "mode")] = tuple(b & 0x70 for b in prog)
    for v in range(3):
        tables[("ins", "route%d" % v)] = tuple(
            (1 << v) if i.filter_table and i.filter_table[0] != 0xFF else 0 for i in ins
        ) + (
            0,
        )  # the last row is "no instrument selected": this voice routes nothing
    tables[("patt", "note")], tables[("patt", "instr")], pbase = _sw_patterns(swm)
    return tables, base, fbase, pbase


def _sw_patterns(swm):
    """``(note lane, instrument lane, pattern bases)``: the patterns as two columns.

    A column entry no row declares is `_NONE`, which no table row can match, so a
    held row is admitted only where the composer's own column still names it."""
    note, instr, base, cur = [], [], [], 0
    for pat in swm.patterns:
        base.append(cur)
        for row in pat.rows:
            note.append(_NONE if row.note is None else row.note & 0xFF)
            got = row.instrument
            instr.append((got - 1) if got is not None and 1 <= got <= 0x3E else _NONE)
        cur += len(pat.rows)
    return tuple(note), tuple(instr), base


def _sw_filter_row(player, tables, fbase, held):
    """The filter-program set row the resonance and mode nibbles were masked out of.

    The row is the controlling voice's own pointer, held across the frames the set
    row still stands; a row whose bytes no longer agree is refused by `_typed`."""
    v = player.filter_controller_voice
    num = ((v.instrument_idx or 0) - 1) if v is not None else -1
    if 0 <= num < len(fbase):
        prog, res = tables[("wf", "left")], tables[("wf", "res")]
        for cand in (v.filt_pos - 3, v.filt_pos):
            row = fbase[num] + cand
            if not 0 <= row < len(prog) or not prog[row] & 0x80:
                continue
            if res[row] == player.filter_resonance & 0xF0:
                held["row"] = row
                break
    return held.get("row")


def _sw_filter_cells(player, tables, fbase, held, cells):
    """$17 and $18: four and two generators over one register each (§4.3).

    $17 is the resonance nibble the filter program sets plus one routing bit per
    voice, each the voice's own instrument flag; $18 is that program's mode nibble
    plus the master volume."""
    row = _sw_filter_row(player, tables, fbase, held)
    res, mvol = player.filter_resonance & 0xFF, player.filter_mode_vol & 0xFF
    routing = 0
    for i, voice in enumerate(player.voices):
        routing |= (1 << i) if voice.voice_in_filter else 0
    if row is None:
        cells[0x17] = Cell("raw", ("res", "routing"), 0, (res & 0xF0) | routing, 0)
        cells[0x18] = Cell("raw", ("mode", "vol"), 0, mvol, 0)
        return
    parts = [Cell("select", ("wf", "res"), row, res & 0xF0, 0, 0xF0)]
    for i, voice in enumerate(player.voices):
        lane = ("ins", "route%d" % i)
        num = (voice.instrument_idx or 0) - 1
        at = num if 0 <= num < len(tables[lane]) - 1 else len(tables[lane]) - 1
        parts.append(Cell("select", lane, at, routing & (1 << i), 0, 1 << i))
    cells[0x17] = tuple(parts)
    cells[0x18] = (
        Cell("select", ("wf", "mode"), row, mvol & 0x70, 0, 0x70),
        Cell("select", ("vol", "master"), 0, mvol & 0x0F, 0, 0x0F),
    )


def _sw_pulse(voice, tables, base, num, cells, reg):
    """pw_lo/pw_hi: the instrument's pulse program at the row its pointer names.

    A set row holds both bytes; a sweep row holds the signed step, so the low byte
    is a RAMP and the high byte is the set row still standing (or a carry: RAW)."""
    lo, hi = voice.sid_pw_lo, voice.sid_pw_hi
    prog, mask = tables[("wf", "left")], tables[("wf", "hi7")]
    cells[reg + 2] = Cell("raw", ("ghost", "pw"), 0, lo, 0)
    cells[reg + 3] = Cell("raw", ("ghost", "pw"), 0, hi, 0)
    if not 0 <= num < len(base):
        return
    for idx in (voice.pw_pos - 0x10 - 3, voice.pw_pos - 0x10):
        row = base[num] + idx
        if not base[num] <= row < len(prog) - 1:
            continue
        if prog[row] & 0x80 and prog[row + 1] == lo and mask[row] == hi:
            cells[reg + 2] = Cell("select", ("wf", "left"), row + 1, lo, 0)
            cells[reg + 3] = Cell("select", ("wf", "hi7"), row, hi, 0)
            return
        if not prog[row] & 0x80 and prog[row + 1]:
            cells[reg + 2] = Cell("ramp", ("wf", "left"), row + 1, lo, prog[row + 1])
            return


def _sw_ctrl(voice, tables, base, num, src=None):
    """ctrl: the instrument's waveform program at the row its own pointer names."""
    val, prog, first = voice.sid_ctrl, tables[("wf", "left")], tables[("ins", "firstwave")]
    if 0 <= num < len(first) and first[num] == val:
        return Cell("ctrl", ("ins", "firstwave"), num, val, 0, src=src)
    if 0 <= num < len(base):
        for cand in (voice.wf_pos - 3, voice.wf_pos):
            row = base[num] + cand
            if not base[num] <= row < len(prog):
                continue
            for k, gate in enumerate((0xFF,) + _GATES):
                if prog[row] & gate == val:
                    return Cell("ctrl", ("wf", "left"), row + k * len(prog), val, 0)
    return Cell("raw", ("ghost", "ctrl"), 0, val, 0)


def _sw_detune_row(voice, tables, base, num, held):
    """The instrument program's row whose detune column holds this voice's detune byte.

    The row is the editor's own pointer, held across the frames the byte still stands;
    ``$FF`` is the model's "inherit" marker and a zero offset predicts nothing."""
    prog, want = tables[("wf", "left")], voice.detune & 0xFF
    if want in (0, 0xFF) or not 0 <= num < len(base):
        return None
    for cand in (voice.wf_pos - 3, voice.wf_pos):
        row = base[num] + cand + 2
        if base[num] <= row < len(prog) and prog[row] == want:
            held["det"] = row
    row = held.get("det")
    return row if row is not None and row < len(prog) and prog[row] == want else None


def _sw_walk(player, pnum, pbase, walk):
    """Per voice, the pattern row that last named a note and the one that named an instrument.

    The row the player just applied is ``pattern_row - 1``; a column that row leaves
    empty holds the earlier row that filled it, the same held reading §5 states."""
    for idx, voice in enumerate(player.voices):
        pat = pnum.get(id(voice.pattern))
        if pat is None or not 0 <= voice.pattern_row - 1 < len(voice.pattern.rows):
            continue
        at = voice.pattern_row - 1
        row, abs_row = voice.pattern.rows[at], pbase[pat] + at
        walk["steps"].add((idx, voice.seq_pos, pat))
        walk["rows"].add((idx, pat, abs_row))
        state = walk.setdefault("at", {}).setdefault(idx, {})
        state["group"] = (idx, pat)
        if row.note is not None and 0 < row.note < _SW_NOTE_FX:  # a pitch, not a note-column fx
            state["note"] = abs_row
        if row.instrument is not None and 1 <= row.instrument <= 0x3E:
            state["instr"] = abs_row


def _sw_cells(player, tables, base, held, cells, walk=None):
    """One frame's voice registers, each named by the lane the player's state holds.

    freq_lo is the pitch lane plus the instrument program's detune byte — a second
    generator's value, combined by the driver's own 8-bit add (§4.2)."""
    walk = {"refused": {}} if walk is None else walk
    refused = walk["refused"]
    for idx, voice in enumerate(player.voices):
        reg, num = 7 * idx, (voice.instrument_idx or 0) - 1
        span = len(tables[("pitch", "lo")]) - 1
        shift = voice.transpose + voice.octave_shift
        note = max(0, min(span, voice.note + shift))
        at = walk.get("at", {}).get(idx, {})
        group = at.get("group")
        nsrc = isrc = None
        if group is not None:
            nsrc = _patt_src(
                tables, ("patt", "note"), at.get("note"), note, group, refused, shift, "arpeggio"
            )
            isrc = _patt_src(
                tables, ("patt", "instr"), at.get("instr"), num, group, refused, 0, "instrument_row"
            )
        drow = _sw_detune_row(voice, tables, base, num, held.setdefault(idx, {}))
        for off, lane in ((0, ("pitch", "lo")), (1, ("pitch", "hi"))):
            val = voice.sid_freq_lo if off == 0 else voice.sid_freq_hi
            if tables[lane][note] == val:
                cells[reg + off] = Cell("select", lane, note, val, 0, src=nsrc)
            elif off == 0 and drow is not None:
                cells[reg + off] = Cell("rel", ("wf", "left"), drow, val, 1, _FULL, (lane, note))
            else:
                cells[reg + off] = Cell("raw", ("detune", "vibrato"), 0, val, 0)
        _sw_pulse(voice, tables, base, num, cells, reg)
        cells[reg + _CTRL] = _sw_ctrl(voice, tables, base, num, isrc)
        for off, val, name in ((5, voice.sid_ad, "ad"), (6, voice.sid_sr, "sr")):
            got = None
            for key in (("ins", name), ("hr", name)):
                if 0 <= num < len(tables[key]) and tables[key][num] == val:
                    got = Cell("select", key, num, val, 0, src=isrc)
                    break
            cells[reg + off] = got or Cell("raw", ("small_fx", name), 0, val, 0)


def sw_native(swm, nframes):
    """``(Native, records)`` for a SID-Wizard module's first subtune.

    `pysidwizard` reads ``.swm`` only — it has no packed-``.sid`` decompiler — so
    the reference records here are the editor's own player's, one boundary short
    of frameprog."""
    from pysidwizard.player import (  # pylint: disable=import-outside-toplevel
        NOTE_FREQ_HI,
        NOTE_FREQ_LO,
        SID_REG_BASE,
        SWMPlayer,
    )

    tables, base, fbase, pbase = _sw_tables(swm, NOTE_FREQ_LO, NOTE_FREQ_HI)
    player = SWMPlayer(swm)
    writes, notes, instrs, onsets, shape, frames = [], [], [], [], [], []
    held = {}
    pnum = {id(p): i for i, p in enumerate(swm.patterns)}
    walk = {"steps": set(), "rows": set(), "refused": {}, **_sw_loops(swm)}
    for _f in range(nframes):
        prev = [v.hr_timer for v in player.voices]
        got = [
            (r - SID_REG_BASE, x & 0xFF)
            for r, x in player.play_frame()
            if 0 <= r - SID_REG_BASE <= _FILTER_HI
        ]
        frames.append(got)
        shape.append(tuple(r for r, _x in got))
        cells = {}
        _sw_walk(player, pnum, pbase, walk)
        _sw_cells(player, tables, base, held, cells, walk)
        cells[0x15] = Cell("raw", ("ghost", "filter"), 0, player.filter_cutoff_lo & 0xFF, 0)
        cells[0x16] = Cell("raw", ("ghost", "filter"), 0, player.filter_cutoff_hi & 0xFF, 0)
        _sw_filter_cells(player, tables, fbase, held, cells)
        for reg, val in got:
            cell = cells.get(reg)
            if cell is None or _value(cell) != val:
                cells[reg] = Cell("raw", ("ghost", "held"), 0, val, 0)
        writes.append(cells)
        notes.append(tuple(v.note + v.transpose + v.octave_shift for v in player.voices))
        instrs.append(tuple(v.instrument_idx or 0 for v in player.voices))
        onsets.append(tuple(int(v.hr_timer > 0 and not p) for v, p in zip(player.voices, prev)))
    return (
        Native("sidwizard", tables, writes, shape, notes, instrs, onsets, _sw_structure(swm), walk),
        framelog.canonical(frames),
    )


def _sw_loops(swm):
    """Where each sequence loops back to: its own start, or a position inside it."""
    from pysidwizard.model import Loop  # pylint: disable=import-outside-toplevel,import-error

    at_end = other = 0
    for seq in swm.sequences[:3]:
        for cmd in seq:
            if isinstance(cmd, Loop):
                at_end += int(not cmd.position)
                other += int(bool(cmd.position))
    return {"loop_at_end": at_end, "loop_elsewhere": other}


def _sw_structure(swm):
    """Counts of the arrangement the composer wrote, in the same generic names."""
    return {
        "patterns": len(swm.patterns),
        "pattern_rows": sum(len(p.rows) for p in swm.patterns),
        "orderlist_entries": sum(len(s) for s in swm.sequences[:3]),
        "instruments": len(swm.instruments),
        "wavetable": sum(len(i.wf_table) for i in swm.instruments),
        "pulsetable": sum(len(i.pw_table) for i in swm.instruments),
        "filtertable": sum(len(i.filter_table) for i in swm.instruments),
        "speedtable": len(swm.chord_table),
    }
