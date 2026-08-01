"""trackertext — a tracker ``Graph`` rendered as text, in the musical domain, for review.

Every line comes from the graph: node ``(transfer, trigger, route)`` triples, the declared
tables those transfers hold, and ``Graph.classes``. ``compare`` puts two graphs of one tune
through this same emitter — ours and a native editor's own song (docs/gt-oracle.md)."""

import math
from collections import namedtuple

from . import tracker

_VOICE_ROLE = (
    "pitch lo",
    "pitch hi",
    "pulse lo",
    "pulse hi",
    "waveform",
    "attack/decay",
    "sustain/release",
)
_FILT_ROLE = {
    0x15: "cutoff lo",
    0x16: "cutoff hi",
    0x17: "resonance/routing",
    0x18: "mode/volume",
}
_PLANES = ("freq", "pw", "ctrl", "ad", "sr", "filter", "tail")
_PLANE_NAME = {
    "freq": "pitch",
    "pw": "pulse",
    "ctrl": "waveform",
    "ad": "attack/decay",
    "sr": "sustain/release",
    "filter": "filter",
    "tail": "other",
}
_STRONG = ("lane", "gate", "ramp")
_SHALLOW = ("imm", "seed")
_ASSEMBLED = ("mask",)  # a byte several generators assemble field by field: neither of those
_SECT = ("", " hold", " gate-", " gate+")  # ctrl rows: lane byte, held, gate cleared, gate set
_RULE = "=" * 96
_FULL = tracker._FULL

_LEVEL = "level"  # a field kind: a magnitude in its own units (volume 15, resonance 10)
_FLAG = "flag"  # a field kind: one bit, on or off
_WAVES = (((0x80, "noise"), (0x40, "pulse"), (0x20, "saw"), (0x10, "tri")), "silent")
_MODES = (((0x40, "high-pass"), (0x20, "band-pass"), (0x10, "low-pass")), "no filter")
_FLAGBITS = ((0x08, "test"), (0x04, "ring"), (0x02, "sync"))
_CTRL_FIELDS = (
    (0xF0, "waveform", _WAVES),
    (0x08, "test", _FLAG),
    (0x04, "ring", _FLAG),
    (0x02, "sync", _FLAG),
    (0x01, "gate", _FLAG),
)
_FILT_FIELDS = {
    0x17: (
        (0xF0, "resonance", _LEVEL),
        (0x08, "external routing", _FLAG),
        (0x04, "voice 3 routing", _FLAG),
        (0x02, "voice 2 routing", _FLAG),
        (0x01, "voice 1 routing", _FLAG),
    ),
    0x18: (
        (0x80, "voice 3 mute", _FLAG),
        (0x70, "filter mode", _MODES),
        (0x0F, "master volume", _LEVEL),
    ),
}
_MASK_NAME = {(0x17, 0x0F): "filter routing"}  # a group whose joined field names read worse

Scan = namedtuple("Scan", "cov emits gen res notes at insts tabs ramps frames groups")
Side = namedtuple("Side", "label what graph law prog offset frames", defaults=(None, None, 0, None))


# ---- 1. musical names: what a register is, what one field of it says --------------
def _fields(reg):
    """The named bit fields of a register, most significant first."""
    if reg <= tracker._VOICE_HI and reg % 7 == tracker._CTRL:
        return _CTRL_FIELDS
    return _FILT_FIELDS.get(reg, ())


def _field_name(reg, mask):
    """The musical object a route's mask names: ``filter mode``, ``master volume``, ``gate``."""
    got = [n for m, n, _k in _fields(reg) if m & mask]
    return _MASK_NAME.get((reg, mask)) or (" + ".join(got) if got else _lane_role(reg))


def _field_str(reg, mask, v):
    """One field's value in its own terms: a level, a set of named bits, or on/off."""
    out = []
    for m, name, kind in _fields(reg):
        if not m & mask:
            continue
        b = v & m
        if kind == _LEVEL:
            out.append("%s %d" % (name, b // (m & -m)))
        elif kind == _FLAG:
            out.append("%s %s" % (name, "on" if b else "off"))
        else:
            bits, none = kind
            out.append("+".join(n for k, n in bits if b & k) or none)
    return ", ".join(out) or "%d" % (v & mask)


def _role(reg, mask=_FULL):
    """``voice 1 waveform`` / ``filter mode``: the part a register, or a field of it, plays."""
    if mask != _FULL:
        name = _field_name(reg, mask)
        return "voice %d %s" % (reg // 7 + 1, name) if reg <= tracker._VOICE_HI else name
    if reg <= tracker._VOICE_HI:
        return "voice %d %s" % (reg // 7 + 1, _VOICE_ROLE[reg % 7])
    return "filter %s" % _FILT_ROLE.get(reg, "other")


def _lane_role(reg):
    """The role of a table lane feeding ``reg``, with the voice dropped."""
    return _VOICE_ROLE[reg % 7] if reg <= tracker._VOICE_HI else _FILT_ROLE.get(reg, "other")


def _part_name(route):
    """What a lane is a lane *of*: a register's role, or the one field the route owns."""
    mask = tracker._mask_of(route)
    return _lane_role(route[1]) if mask == _FULL else _field_name(route[1], mask)


def _note_name(idx):
    """Note name for a semitone index, as the pitch table is indexed."""
    return "%s%d" % (tracker._NOTE_NAMES[idx % 12], idx // 12)


def _byte_str(reg, b):
    """One declared byte in the terms of its register: waveform bits, ADSR nibbles, level."""
    if reg <= tracker._VOICE_HI and reg % 7 == tracker._CTRL:
        got = [n for m, n in _WAVES[0] if b & m] + [n for m, n in _FLAGBITS if b & m]
        return "+".join(got or [_WAVES[1]]) + ("+gate" if b & 1 else "")
    if reg <= tracker._VOICE_HI and reg % 7 == 5:
        return "A%X D%X" % (b >> 4, b & 15)
    if reg <= tracker._VOICE_HI and reg % 7 == 6:
        return "S%X R%X" % (b >> 4, b & 15)
    return _field_str(reg, _FULL, b) if _fields(reg) else "%d" % b


def _cents(note):
    """Detune in cents: the interval from the table's note to the word actually written."""
    ref = note.word - note.detune
    return int(round(1200 * math.log2(note.word / ref))) if ref > 0 and note.word > 0 else 0


def _mmss(nframes):
    """Frame count as m:ss at 50Hz."""
    return "%d:%02d" % (nframes // 50 // 60, nframes // 50 % 60)


def _pct(a, b):
    """``a/b`` as a percentage, zero-safe."""
    return 100.0 * a / b if b else 0.0


# ---- 2. the tables a SELECT reads, numbered by first use -------------------------
def _lane_of(table, reg):
    """``(lane bytes, sections)``: a ctrl ``SELECT`` table is its lane plus 3 gate images."""
    n = len(table) // (1 + len(tracker._SECT))
    if n and reg <= tracker._VOICE_HI and reg % 7 == tracker._CTRL:
        img = tuple((b & a) | o for a, o in tracker._SECT for b in table[:n])
        if table[:n] + img == tuple(table):
            return table[:n], len(_SECT)
    return tuple(table), 1


def _decl_index(prog):
    """``{lane bytes: declaration id}``: which declared table a lane was read from.

    The inverse of ``tracker._lane``. The id is never printed — it only groups the lanes
    of one declaration into one table, which is then numbered by first use."""
    out = {}
    if prog is None:
        return out
    for d in sorted(getattr(prog, "data_decls", ()), key=lambda x: (x["base"], x["size"])):
        if d["kind"] != "table":
            continue
        stride = max(1, d.get("stride") or 1)
        for off in range(stride):
            n = (d["size"] - off + stride - 1) // stride
            lane = tuple(prog.mem0[d["base"] + off + stride * i] for i in range(n))
            out.setdefault(lane, d["base"])
    return out


def _keys(graph, index):
    """Per node, ``(table id, lane bytes, sections)`` for a SELECT at a row, else None."""
    out = []
    for g in graph.nodes:
        if g.transfer[0] != "SELECT" or not g.transfer[2] or g.route[0] != "plane":
            out.append(None)
            continue
        lane, sections = _lane_of(g.transfer[1], g.route[1])
        out.append((index.get(lane, lane), lane, sections))
    return out


class _Tables:
    """The tables the SELECTs read: their role, their lanes, and ordinals by first use."""

    def __init__(self, graph, keys):
        self.num, self.inst, self.role, self.lanes, self.rows = {}, {}, {}, {}, {}
        self.shift = bool(graph.freq_table is not None and graph.freq_table.shift)
        for g, key in zip(graph.nodes, keys):
            if key is None:
                continue
            plane = tracker._plane_of(g.route[1])
            role = {"freq": "pitch", "filter": "filter"}.get(plane, "instrument")
            if key[0] not in self.role or role == "instrument":
                self.role[key[0]] = role
            self.lanes.setdefault(key[0], {})[_part_name(g.route)] = key[1]
            self.rows[key[0]] = max(self.rows.get(key[0], 0), len(key[1]))

    def see(self, tid, row, n):
        """Register a table and a row at their first emit: the order the names follow."""
        self.num.setdefault(tid, len(self.num))
        if self.role.get(tid) != "pitch":
            self.inst.setdefault((tid, row % n), len(self.inst))

    def name(self, tid):
        """``table 2``, numbered in order of first emit."""
        return "table %d" % self.num.get(tid, len(self.num))

    def row_str(self, tid, row, n):
        """One row of a table, in the terms of the table's role."""
        if self.role.get(tid) == "pitch":
            return tracker._NOTE_NAMES[row % 12] if self.shift else _note_name(row % n)
        what = "inst" if self.role.get(tid) == "instrument" else "setting"
        return "%s %02d%s" % (what, self.inst.get((tid, row % n), 0), _SECT[min(row // n, 3)])


# ---- 3. one linear pass over the frames ------------------------------------------
def _run_note(runs, f, note):
    """Extend or open a run of one note: ``[first, last, index, name, count, lo, hi]``."""
    c = _cents(note)
    if runs and runs[-1][2] == note.index:
        r = runs[-1]
        r[1], r[4], r[5], r[6] = f, r[4] + 1, min(r[5], c), max(r[6], c)
    else:
        runs.append([f, f, note.index, note.name, 1, c, c])


def _consumers(nodes):
    """``{node: [the nodes it triggers]}``, indexed once for the per-node display."""
    out = {}
    for j, h in enumerate(nodes):
        if h.trigger != tracker.FRAME:
            out.setdefault(h.trigger[1], []).append(j)
    return out


def _see_select(scan_tabs, key, row, reg, frame, insts):
    """Name a table and a row at their first emit, and record the instrument it says."""
    tid, lane = key[0], key[1]
    scan_tabs.see(tid, row, len(lane))
    if reg <= tracker._VOICE_HI and reg % 7 == 5:
        insts[reg // 7][frame] = scan_tabs.inst.get((tid, row % len(lane)))


def _scan(graph, nframes, keys, tabs):
    """One streaming pass: counts, first-use ordinals, note runs and sweep samples.

    Linear in frames *and* in nodes. A masked group latches its fields and the last of
    them to fire writes the assembled byte, exactly as ``tracker._run`` evaluates it, so
    a register several generators drive counts as one emit and not as one per field."""
    nodes = graph.nodes
    firing = tracker._Fires(nodes)
    parts = tracker._masked(nodes)
    held = {reg: {} for reg in parts}
    counts, emits = [0] * len(nodes), [0] * len(nodes)
    gen, res, ramps, notes, st = {}, {}, {}, [[], [], []], {}
    at, insts = [[None] * nframes for _v in range(3)], [{}, {}, {}]
    pitch = graph.freq_table
    for f in range(nframes):
        fired, _ticks = firing.step(f)
        last = {r: max((i for i in ns if fired[i]), default=None) for r, ns in parts.items()}
        cur = {}
        for i, g in enumerate(nodes):
            if not fired[i]:
                continue
            if g.transfer[0] == "RAW":
                for reg, _v in g.transfer[1][f] if f < len(g.transfer[1]) else ():
                    res[reg] = res.get(reg, 0) + 1
                continue
            if g.route == tracker.INDEX:  # the arrangement: this emit is a row, not a byte
                for _t in range(fired[i]):
                    counts[i] += 1
                    cur[i] = v = tracker._value(g, i, counts[i], cur, st)
                    emits[i] += v is not None
                    ramps.setdefault(i, [[], []])[0 if emits[i] <= 12 else 1].append(v)
                    del ramps[i][1][:-4]
                continue
            if g.route[0] == "pair":  # a 16-bit emit: both halves count, the word samples
                for _t in range(fired[i]):
                    counts[i] += 1
                    v = tracker._value(g, i, counts[i], cur, st)
                    if v is None:
                        continue
                    emits[i] += 1
                    ramps.setdefault(i, [[], []])[0 if emits[i] <= 12 else 1].append(v)
                    del ramps[i][1][:-4]
                    for reg in g.route[1:3]:
                        gen[reg] = gen.get(reg, 0) + 1
                continue
            if g.route[0] != "plane":
                counts[i] += fired[i]
                continue
            reg = g.route[1]
            for _t in range(fired[i]):
                counts[i] += 1
                v = tracker._value(g, i, counts[i], cur, st)
                if v is None:
                    continue
                emits[i] += 1
                if keys[i] is not None:
                    row = tracker._row(g.transfer[2], counts[i], cur)
                    _see_select(tabs, keys[i], row, reg, f, insts)
                elif g.transfer[0] == "RAMP":
                    ramps.setdefault(i, [[], []])[0 if emits[i] <= 12 else 1].append(v & 0xFF)
                    del ramps[i][1][:-4]
                v = tracker._assemble(g, v, held.get(reg), i == last.get(reg))
                if v is None:
                    continue
                gen[reg] = gen.get(reg, 0) + 1
                cur[reg] = v & 0xFF
        for v in range(3):
            lo, hi = cur.get(7 * v), cur.get(7 * v + 1)
            note = None if lo is None or hi is None or pitch is None else _word_note(pitch, lo, hi)
            if note is not None:
                _run_note(notes[v], f, note)
                at[v][f] = note.index
    cov = tracker._coverage(gen, res, graph.classes)
    return Scan(cov, emits, gen, res, notes, at, insts, tabs, ramps, nframes, parts)


def _word_note(pitch, lo, hi):
    """The note the pitch table inverts a generated pitch word to, or None."""
    return tracker._note_of(pitch, lo | (hi << 8))


# ---- 4. compression: runs, repeated blocks, and what was collapsed ---------------
def _rle(seq):
    """``[(value, count)]`` for consecutive equal entries."""
    out = []
    for v in seq:
        if out and out[-1][0] == v:
            out[-1] = (v, out[-1][1] + 1)
        else:
            out.append((v, 1))
    return out


def _cycles(items, maxp=16):
    """``[(start, stop, period)]``: maximal segments repeating one block of <= ``maxp``.

    A repeating block is the arrangement showing through, so it is surfaced rather than
    trimmed; period 1 is a plain run. A display compression that claims no generator."""
    out, i, n = [], 0, len(items)
    while i < n:
        p = next(
            (
                q
                for q in range(1, maxp + 1)
                if i + 2 * q <= n
                and items[i] == items[i + q]
                and items[i : i + q] == items[i + q : i + 2 * q]
            ),
            1,
        )
        j = i + p
        while j + p <= n and items[j : j + p] == items[i : i + p]:
            j += p
        out.append((i, j, p))
        i = j
    return out


def _block(toks, width=84):
    """A block's tokens, cut at ``width`` with the remainder counted, never dropped."""
    out, used = [], 0
    for k, t in enumerate(toks):
        if out and used + len(t) > width:
            return "  ".join(out) + "  ...(+%d)" % (len(toks) - k)
        out.append(t)
        used += len(t) + 2
    return "  ".join(out)


def _group_toks(items, fmt):
    """``(one token per repeating group, the groups, the run-length coding)``."""
    rle = _rle(items)
    groups = _cycles(rle)
    toks = []
    for i, j, p in groups:
        body = _block([fmt(v) + ("" if n == 1 else " x%d" % n) for v, n in rle[i : i + p]])
        reps = (j - i) // p
        toks.append(body if reps == 1 else "[%s] x%d" % (body, reps))
    return toks, groups, rle


def _pack(toks, width=84):
    """Tokens grouped into lines of at most ``width``, as lists of tokens."""
    out, cur, used = [], [], 0
    for t in toks:
        if cur and used + len(t) > width:
            out.append(cur)
            cur, used = [], 0
        cur.append(t)
        used += len(t) + 2
    return out + ([cur] if cur else [])


def _stream_lines(items, fmt, width=84, cap=1):
    """A row stream over at most ``cap`` lines: run-length coded, repeated blocks factored.

    Nothing is dropped silently — a stream cut at ``cap`` lines ends with the blocks and
    the rows left out."""
    toks, groups, rle = _group_toks(items, fmt)
    rows = _pack(toks, width)
    out = ["  ".join(r) for r in rows[:cap]]
    shown = sum(len(r) for r in rows[:cap])
    if shown < len(toks):
        left = sum(n for _v, n in rle[groups[shown][0] :])
        out[-1] += "  ...(+%d blocks, %d rows)" % (len(toks) - shown, left)
    return out


def _stream(items, fmt, width=84):
    """The first line of a row stream, with what it left out counted on the end."""
    return _stream_lines(items, fmt, width)[0]


# ---- 5. header: the law verdict and the coverage partition -----------------------
_CLS = _STRONG + _SHALLOW + _ASSEMBLED + ("note",)
_COVFMT = "%-16s %7d %7d  %5.1f%% | %6d %6d %6d | %6d %6d | %6d | %6d"
_COVHDR = "%-16s %7s %7s  %6s | %6s %6s %6s | %6s %6s | %6s | %6s" % (
    ("plane", "gen", "total", "share") + _CLS
)


def _classes(graph, cov, plane):
    """The evidence classes behind one plane's generated emits, the note lane included."""
    it, _all = cov.planes.get(plane, (0, 0))
    cls = dict((graph.classes or {}).get(plane, {}))
    cls["note"] = it - sum(cls.values())
    return cls


def _header(graph, scan, title, law):
    """Tune, law verdict, node census and the per-plane value-coverage split."""
    cov = scan.cov
    kinds = _rle(sorted(g.transfer[0] for g in graph.nodes))
    out = [
        _RULE,
        "tracker  %s  %d frames (%s)" % (title, scan.frames, _mmss(scan.frames)),
        "law      %s   (the graph's projection equals the frame program's, frame for frame)"
        % (law or "not checked"),
        "graph    %d nodes: %s" % (len(graph.nodes), ", ".join("%d %s" % (n, k) for k, n in kinds)),
        "values   %d/%d = %.1f%% generated, %d replayed as observed writes"
        % (cov.interp, cov.total, _pct(cov.interp, cov.total), cov.residual),
        "",
        _COVHDR,
    ]
    tot = dict.fromkeys(_CLS, 0)
    for p in _PLANES:
        if p not in cov.planes:
            continue
        it, all_ = cov.planes[p]
        cls = _classes(graph, cov, p)
        for k in _CLS:
            tot[k] += cls.get(k, 0)
        row = (_PLANE_NAME[p], it, all_, _pct(it, all_))
        out.append(_COVFMT % (row + tuple(cls.get(k, 0) for k in _CLS)))
    all_row = ("all", cov.interp, cov.total, _pct(cov.interp, cov.total))
    return out + [
        _COVFMT % (all_row + tuple(tot[k] for k in _CLS)),
        "         strong = a declared table byte at a recovered row (lane/gate), or generated"
        " from one (ramp)",
        "         shallow = imm: a program constant, no row explained; seed: the observed byte"
        " a sweep starts from",
        "         mask = one byte several generators assemble field by field: part declared,"
        " part program constant",
        "         note = a pitch-table row recovered for an observed pitch word",
    ]


# ---- 6. engine and instruments ----------------------------------------------------
def _pitch_lines(p):
    """The pitch table: how many notes it holds, what range, and how it inverts."""
    if p is None:
        return ["pitch    (no pitch table recovered)"]
    n = len(p.words)
    how = "one octave, transposed down by octave" if p.shift else "%d octaves" % p.octaves
    span = "C .. B" if p.shift else "%s .. %s" % (_note_name(0), _note_name(n - 1))
    return ["pitch    %d notes, %s, %s, equal-tempered" % (n, span, how)]


def _field_lines(graph, scan):
    """The masked groups: which musical objects share one write, and which nodes they are."""
    out = []
    for reg, ns in sorted(scan.groups.items()):
        names = [_role(reg, tracker._mask_of(graph.nodes[i].route)) for i in ns]
        out.append(
            "fields   %s: %d generators of disjoint bits, one write between them — %s"
            % (" + ".join(names), len(ns), " ".join("n%02d" % i for i in ns))
        )
    return out


def _engine(graph, prog, scan):
    """The tempo/clock census, then the tables the generators read, numbered by first use."""
    out = ["", "; ---- engine ----"] + _pitch_lines(graph.freq_table)
    if prog is not None:
        clocks = tracker._clocks(prog)
        div = sum(c.role == "divider" for c in clocks)
        reload_ = "a divider reloads per tick" if tracker._tempo(clocks) else "no reload recovered"
        out.append(
            "tempo    dividers %d, free-running phases %d, %s" % (div, len(clocks) - div, reload_)
        )
    out += _field_lines(graph, scan)
    tabs = scan.tabs
    out.append("tables   %d read by the generators, numbered by first use" % len(tabs.num))
    for tid in sorted(tabs.num, key=lambda k: tabs.num[k]):
        out.append(
            "  %-8s %-11s %3d rows   lanes: %s"
            % (
                tabs.name(tid),
                tabs.role.get(tid, "?"),
                tabs.rows.get(tid, 0),
                ", ".join(sorted(tabs.lanes.get(tid, {}))),
            )
        )
    return out


_NO_FIELDS = tracker._FILTER_HI + 1  # a register with no named field: a bare level
_ROLE_FIELD = {n: (r, m) for r, fs in _FILT_FIELDS.items() for m, n, _k in fs}
_ROLE_FIELD.update({n: (tracker._CTRL, m) for m, n, _k in _CTRL_FIELDS})
_ROLE_FIELD.update({n: (r, m) for (r, m), n in _MASK_NAME.items()})
# a whole-register lane role wins where a field shares its name: the byte carries both
_ROLE_FIELD.update({r: (i, _FULL) for i, r in enumerate(_VOICE_ROLE)})
_ROLE_FIELD.update({name: (reg, _FULL) for reg, name in _FILT_ROLE.items()})


def _lane_byte(role, b):
    """One declared byte of a table lane, decoded by the part of the register it drives."""
    reg, mask = _ROLE_FIELD.get(role, (_NO_FIELDS, _FULL))
    return _byte_str(reg, b) if mask == _FULL else _field_str(reg, mask, b)


def _instruments(scan, cap=48):
    """The rows the generators read from the instrument tables, as an editor's list."""
    tabs = scan.tabs
    used = sorted((n, k) for k, n in tabs.inst.items())
    if not used:
        return []
    roles = sorted({r for _n, (tid, _row) in used for r in tabs.lanes.get(tid, {})})
    rows = [["  %-4s %02d" % (tabs.role.get(tid, "?")[:4], n)] for n, (tid, _r) in used[:cap]]
    for k, (_n, (tid, row)) in enumerate(used[:cap]):
        for r in roles:
            lane = tabs.lanes.get(tid, {}).get(r)
            rows[k].append(_lane_byte(r, lane[row]) if lane and row < len(lane) else "-")
    head = ["  entry  "] + list(roles)
    wide = [max(len(c[i]) for c in [head] + rows) + 2 for i in range(len(head))]
    out = [
        "",
        "; ---- instruments (rows of the instrument tables, numbered by first appearance) ----",
    ]
    out += ["".join("%-*s" % (w, c) for w, c in zip(wide, r)) for r in [head] + rows]
    if len(used) > cap:
        out.append("  ...(+%d more)" % (len(used) - cap))
    return out


# ---- 7. the generators, one entry per node ---------------------------------------
def _trig(graph, g):
    """``<- every frame``, or the upstream trigger stream and how often it fires."""
    if g.trigger == tracker.FRAME:
        return "<- every frame"
    up = graph.nodes[g.trigger[1]].transfer
    n = sum(up[1]) if up[0] == "EDGE" else 0
    return "<- n%02d%s" % (g.trigger[1], " x%d" % n if n else "")


def _route(g):
    """Where a node's emits go, in musical terms: a register's part, or one field of it."""
    if tracker._is_plane(g.route):
        role = _role(g.route[1], tracker._mask_of(g.route))
        return "-> " + (role + ", as an offset" if g.route[0] == "rel" else role)
    if g.route[0] == "pair":
        return "-> %s, as one 16-bit value" % _role(g.route[1], tracker._FULL)
    if g.route[0] == "fire":
        return "-> triggers"
    if g.route == tracker.INDEX:
        return "-> the row another generator reads"
    return "-> unexplained writes"


def _fires_str(counts, cap=4):
    """When a trigger stream fires: the first frame, and the histogram of the gaps."""
    at = [f for f, c in enumerate(counts) for _i in range(c)]
    if not at:
        return "never"
    gaps = sorted(_rle(sorted(b - a for a, b in zip(at, at[1:]))), key=lambda kv: (-kv[1], kv[0]))
    hist = ", ".join("%d apart x%d" % (g, n) for g, n in gaps[:cap])
    left = sum(n for _g, n in gaps[cap:])
    return "first f%d, gaps: %s" % (at[0], hist) + (
        ", +%d more gaps (%d lengths)" % (left, len(gaps) - cap) if len(gaps) > cap else ""
    )


def _select_lines(g, key, tabs, n_emits, cap=8):
    """A ``SELECT``: which table lane it reads, its row stream, and what the bytes say."""
    tid, lane, sections = key
    src = g.transfer[2]
    named = ", ".join("n%02d" % j for j in tracker._sources(src))
    rows = (
        ["     rows   generated by %s, not observed" % named]
        if tracker._generated(src)
        else _stream_lines(src, lambda r: tabs.row_str(tid, r, len(lane)), cap=cap)
    )
    return (
        [
            "     reads  %s %s lane, %d rows%s"
            % (
                tabs.name(tid),
                _part_name(g.route),
                len(lane),
                "" if sections == 1 else " + 3 images",
            )
        ]
        + ["     %-6s %s" % ("rows" if i == 0 else "", ln) for i, ln in enumerate(rows)]
        + ["     emits  %d" % n_emits]
    )


def _straight_str(g, n_emits):
    """A ``SELECT`` read straight through: one constant in its field's terms, or a sequence."""
    seq = g.transfer[1]
    if len(seq) == 1 and tracker._is_plane(g.route):  # an index source names no register
        mask = tracker._mask_of(g.route)
        what = (
            _field_str(g.route[1], mask, seq[0]) if mask != _FULL else _byte_str(g.route[1], seq[0])
        )
        return "constant %s, %d times" % (what, n_emits)
    vals = [v for v in seq if v is not None]
    return "%d of %d entries, %d distinct (see the note lane)" % (n_emits, len(seq), len(set(vals)))


def _node_lines(graph, i, scan, keys, cons):
    """One node: its transfer, what triggers it, where it routes, and its detail."""
    g = graph.nodes[i]
    kind = g.transfer[0]
    head = "n%02d  %-6s %-28s %s" % (i, kind, _route(g), _trig(graph, g))
    if kind == "EDGE":
        seen = cons.get(i, ())
        return [
            "%s  %d fires over %d frames -> %s"
            % (head, sum(g.transfer[1]), len(g.transfer[1]), _block(["n%02d" % j for j in seen])),
            "     when   %s" % _fires_str(g.transfer[1]),
        ]
    if kind == "SELECT" and keys[i] is not None:
        return [head] + _select_lines(g, keys[i], scan.tabs, scan.emits[i])
    if kind == "RAMP":
        _k, seed, step, bound, turn = g.transfer
        head_v, tail_v = scan.ramps.get(i, ([], []))
        return [
            head,
            "     %s  starts at %d (%s), steps %+d per fire, %s"
            % (
                "cursor" if g.route == tracker.INDEX else "sweep ",
                seed,
                "DECLARED" if g.route == tracker.INDEX else "OBSERVED",
                step,
                (
                    "turns down at high %d and up at high %d (DECLARED), wraps at %d"
                    % (turn[1], turn[0], bound)
                    if turn
                    else "wraps at %d" % bound
                ),
            ),
            "     values %s%s  (%d emits)"
            % (
                " ".join("%d" % v for v in head_v),
                " .. " + " ".join("%d" % v for v in tail_v) if tail_v else "",
                scan.emits[i],
            ),
        ]
    if kind == "SELECT":  # no row recovered: the table is read straight through
        return [head, "     emits  %s" % _straight_str(g, scan.emits[i])]
    if kind == "RAW":
        rows = g.transfer[1]
        return [
            "%s  %d writes over %d frames  (OBSERVED — see residual)"
            % (head, sum(len(r) for r in rows), len(rows))
        ]
    if kind == "DIV":
        n, phase = g.transfer[1], g.transfer[2] if len(g.transfer) > 2 else g.transfer[1] - 1
        seen = cons.get(i, ())
        rate = (
            "one tick per row, the row's own duration (n%02d)" % n[1]
            if isinstance(n, tuple)
            else "one tick per %d frames" % n
        )
        what = "ticks" if g.trigger != tracker.FRAME else "frames"
        return [
            "%s  %s, %d in after %d %s -> %s"
            % (
                head,
                rate,
                scan.emits[i] or sum(1 for _x in seen),
                phase,
                what,
                _block(["n%02d" % j for j in seen]),
            )
        ]
    return ["%s  one tick per %s" % (head, g.transfer[1])]


def _weight(g, scan, i):
    """What one node contributes: fires for a trigger stream, emits for a plane one."""
    return sum(g.transfer[1]) if g.transfer[0] == "EDGE" else scan.emits[i]


def _generators(graph, scan, keys, cap=32):
    """Every node in graph order; past ``cap`` of one kind the rest are counted, not listed.

    A whole tune's sweep is thousands of one-run ``RAMP``s of the same shape, so the
    listing is capped per transfer kind and what it left out is stated with its weight."""
    cons = _consumers(graph.nodes)
    out = [
        "",
        "; ---- generators: (transfer, trigger, route) per node ----",
        "; a row past the end of its lane is that row's byte held: 'hold', 'gate-' or 'gate+'",
        "; every EDGE fire time is OBSERVED — the trigger floor, no generator produces them",
        "; a masked group's generators each own one field and share one write (see 'fields')",
    ]
    seen, left = {}, {}
    for i, g in enumerate(graph.nodes):
        kind = g.transfer[0]
        seen[kind] = seen.get(kind, 0) + 1
        if seen[kind] <= cap:
            out += _node_lines(graph, i, scan, keys, cons)
        else:
            n, w = left.get(kind, (0, 0))
            left[kind] = (n + 1, w + _weight(g, scan, i))
    for kind, (n, w) in sorted(left.items()):
        what = "fires" if kind == "EDGE" else "emits"
        out.append("...(+%d more %s nodes, %d %s between them, not listed)" % (n, kind, w, what))
    return out


# ---- 7b. the song: the arrangement the frame program's own walk names ---------------
def _seq_name(chart, charts):
    """What one chart's regions are: the sequences that name others, or the patterns."""
    return (
        "pattern"
        if chart.source is not None
        else ("sequence" if any(c.source == chart.pointer for c in charts) else "region")
    )


def _entry_str(chart, charts, byte):
    """One region entry: the pattern it names, or its own value."""
    for c in charts:
        if c.source == chart.pointer:
            return "pat %02d" % byte if byte < len(c.blocks) else "pat ?"
    return "%d" % byte


def _inst_no(tabs, row):
    """The instrument ordinal this layer already assigned to an instrument-bank row."""
    for (tid, r), n in tabs.inst.items():
        if r == row and tabs.role.get(tid) == "instrument":
            return n
    return None


def _row_str(chart, row, pitch, tabs):
    """One pattern row in the musical domain: duration, flags, instrument and note."""
    off, fields = row
    byte = next((v for o, v, _c in fields if o == off), None)
    out, seen = [], chart.roles[1]
    for mask, name in sorted(seen.items(), key=lambda kv: -kv[0]):
        if byte is None or name == "parameter":
            continue
        val = byte & mask
        if name == "duration":
            out.append("dur %2d" % (val // (mask & -mask)))
        elif val:
            out.append(name)
    roles = [
        sorted({chart.roles[0][c] for c in cs if c in chart.roles[0]}) for _o, _v, cs in fields
    ]
    for k, (o, val, _cells) in enumerate(fields):
        if o == off:
            continue
        role = roles[k]
        if "note" in role and "note" in sum(roles[k + 1 :], []):
            out.append("param %d" % val)  # a later field is the row's note: this one is not
        elif "note" in role:
            past = pitch is None or val >= len(pitch.words)
            out.append("note %d" % val if past else _note_name(val))
        elif "instrument" in role:
            got = _inst_no(tabs, val)
            out.append("inst %02d" % (val if got is None else got))
        else:
            out.append("param %d" % val)
    return " ".join(out) or "-"


def _chart_lines(chart, charts, pitch, tabs, cap=32, rowcap=40):
    """One chart: its regions, their entries or their decoded rows."""
    kind = _seq_name(chart, charts)
    rows = sum(len(b.rows) for b in chart.blocks)
    out = [
        "%-9s %d %ss, %d rows, each ending on the byte the player's own compare names"
        % (kind + "s", len(chart.blocks), kind, rows)
    ]
    if chart.roles[1]:
        out.append(
            "          row fields: %s"
            % ", ".join(sorted({n for n in chart.roles[1].values() if n != "flag"}))
        )
    for b in chart.blocks[:cap]:
        head = "  %-8s %3d rows" % (
            (
                ("voice %d" % (b.index + 1))
                if kind == "sequence"
                else ("%s %02d" % (kind[:3], b.index))
            ),
            len(b.rows),
        )
        if kind != "pattern":
            toks = [_entry_str(chart, charts, v) for _o, f in b.rows for _p, v, _c in f[:1]]
        else:
            toks = [_row_str(chart, r, pitch, tabs) for r in b.rows]
        out += [head] + ["    " + ln for ln in _stream_lines(toks, lambda t: t, cap=rowcap)]
    if len(chart.blocks) > cap:
        out.append("  ...(+%d more %ss)" % (len(chart.blocks) - cap, kind))
    return out


def _song(graph, scan):
    """The arrangement: every terminator-bounded region the program text walks."""
    if not getattr(graph, "charts", ()):
        return []
    out = [
        "",
        "; ---- song: the arrangement, walked from the frame program's own program text ----",
        "; every byte here is declared data at an offset the player's own cursor steps to",
    ]
    for chart in graph.charts:
        out += _chart_lines(chart, graph.charts, graph.freq_table, scan.tabs)
    return out


# ---- 8. the note lane, per voice ---------------------------------------------------
def _run_str(r):
    """One note run: name, length in frames, and its detune in cents."""
    det = "" if r[5] == r[6] == 0 else " %+d..%+dc" % (r[5], r[6])
    return "%s%s%s" % (r[3], "" if r[4] == 1 else " x%d" % r[4], det)


def _voices(graph, scan, cap=200):
    """Per voice, the note lane as runs of named notes, repeated blocks factored out."""
    out = ["", "; ---- note lanes (the generated pitch, read back through the pitch table) ----"]
    if graph.freq_table is None:
        return out + ["(no pitch table recovered: no note lane)"]
    for v, runs in enumerate(scan.notes):
        out.append(
            "voice %d  %d note frames of %d, %d runs, %d distinct notes"
            % (v + 1, sum(r[4] for r in runs), scan.frames, len(runs), len({r[2] for r in runs}))
        )
        groups = _cycles([(r[2], r[4]) for r in runs])
        for i, j, p in groups[:cap]:
            reps = (j - i) // p
            toks = _block([_run_str(r) for r in runs[i : i + p]])
            out.append(
                "  f%05d-%05d  %s"
                % (runs[i][0], runs[j - 1][1], toks if reps == 1 else "[%s] x%d" % (toks, reps))
            )
        if len(groups) > cap:
            left = groups[cap][0]
            out.append(
                "  ...(+%d blocks, %d runs, %d note frames, to f%05d)"
                % (len(groups) - cap, len(runs) - left, sum(r[4] for r in runs[left:]), runs[-1][1])
            )
    return out


# ---- 9. the residual, always explicit ---------------------------------------------
def _residual(graph, scan):
    """What the graph does not explain, named the same way as what it does."""
    cov = scan.cov
    i = graph.raw_index()
    rows = graph.nodes[i].transfer[1] if i is not None else []
    out = [
        "",
        "; ---- residual: what the graph does NOT explain ----",
        "values   %d writes replayed verbatim over %d frames = %.1f%% of all writes"
        % (cov.residual, len(rows), _pct(cov.residual, cov.total)),
    ]
    for p in _PLANES:
        it, all_ = cov.planes.get(p, (0, 0))
        if it < all_:
            out.append(
                "  plane  %-16s %7d of %7d not explained (%.1f%%)"
                % (_PLANE_NAME[p], all_ - it, all_, _pct(all_ - it, all_))
            )
    for reg in sorted(scan.res):
        out.append(
            "  %-24s %7d replayed, %7d generated"
            % (_role(reg), scan.res[reg], scan.gen.get(reg, 0))
        )
    edges = [sum(g.transfer[1]) for g in graph.nodes if g.transfer[0] == "EDGE"]
    out.append(
        "timing   %d trigger streams, %d fires: every note-on time is observed, not generated"
        % (len(edges), sum(edges))
    )
    cls = {}
    for c in (graph.classes or {}).values():
        for k, n in c.items():
            cls[k] = cls.get(k, 0) + n
    out.append(
        "shallow  %d program constants (no row explained), %d observed bytes seeding a sweep"
        % (cls.get("imm", 0), cls.get("seed", 0))
    )
    for f, r in [(f, r) for f, r in enumerate(rows) if r][:2]:
        more = "  ...(+%d)" % (len(r) - 3) if len(r) > 3 else ""
        writes = ",  ".join("%s = %s" % (_role(reg), _byte_str(reg, val)) for reg, val in r[:3])
        out.append("  f%05d  %s%s   (OBSERVED)" % (f, writes, more))
    return out


# ---- 10. the whole rendering ------------------------------------------------------
def _rendered(graph, scan, keys, prog, title, law):
    """The six sections of one graph's rendering, in order."""
    return (
        _header(graph, scan, title, law)
        + _engine(graph, prog, scan)
        + _instruments(scan)
        + _generators(graph, scan, keys)
        + _song(graph, scan)
        + _voices(graph, scan)
        + _residual(graph, scan)
    )


def _joined(lines):
    """The text of a rendering: no trailing whitespace on any line."""
    return "\n".join(line.rstrip() for line in lines + [_RULE, ""])


def _scanned(graph, nframes, prog):
    """``(keys, Scan)``: one pass over the frames, with the tables named by first use."""
    keys = _keys(graph, _decl_index(prog))
    return keys, _scan(graph, nframes, keys, _Tables(graph, keys))


def emit(graph, nframes, prog=None, title="graph", law=None):
    """The rendering: header, engine, instruments, generators, note lanes, residual."""
    keys, scan = _scanned(graph, nframes, prog)
    return _joined(_rendered(graph, scan, keys, prog, title, law))


# ---- 11. two graphs of one tune, and what they agree on ----------------------------
def _bijection(pairs):
    """``(agreeing share, modal map as ``[(a, b)]``, is it onto)`` for ``(a, b)`` pairs."""
    per = {}
    for a, b in pairs:
        hist = per.setdefault(a, {})
        hist[b] = hist.get(b, 0) + 1
    votes = {a: max(hist, key=hist.get) for a, hist in per.items()}
    hit = sum(1 for a, b in pairs if votes.get(a) == b)
    return _pct(hit, len(pairs)), sorted(votes.items()), len(set(votes.values())) == len(votes)


def _items(seq):
    """``(frame, value)`` pairs of a dense per-frame lane or a sparse one."""
    return enumerate(seq) if isinstance(seq, list) else seq.items()


def _at(seq, f):
    """One frame of a dense or sparse lane, or None off the end."""
    if isinstance(seq, list):
        return seq[f] if 0 <= f < len(seq) else None
    return seq.get(f)


def _shared(a, b, off):
    """``[(ours, theirs)]`` over the frames and voices where both sides name something."""
    out = []
    for v in range(3):
        for f, x in _items(a[v]):
            y = _at(b[v], f - off)
            if x is not None and y is not None:
                out.append((x, y))
    return out


def _note_agreement(left, right, off):
    """Do the two note lanes name the same pitch, and is there a transposition between them?"""
    pairs = _shared(left.at, right.at, off)
    ours = sum(1 for v in range(3) for x in left.at[v] if x is not None)
    if not pairs:
        return ["notes    no frame names a note on both sides (ours names %d)" % ours]
    hist = {}
    for x, y in pairs:
        hist[x - y] = hist.get(x - y, 0) + 1
    best = max(hist, key=hist.get)
    how = "the same pitch" if best == 0 else "ours transposed %+d semitones" % best
    return [
        "notes    %d frames name a note on both sides, %d agree (%.1f%%) — %s"
        % (len(pairs), hist[best], _pct(hist[best], len(pairs)), how),
        "         ours names a note on %d frames in all" % ours,
    ]


def _wrap(toks, lead="         ", width=84):
    """Tokens as indented lines of at most ``width``, nothing dropped."""
    out, cur = [], []
    for t in toks:
        if cur and sum(len(x) + 2 for x in cur) + len(t) > width:
            out.append(lead + ", ".join(cur))
            cur = []
        cur.append(t)
    return out + ([lead + ", ".join(cur)] if cur else [])


def _inst_agreement(left, right, off, cap=32):
    """Is our instrument numbering a bijection onto the other side's?"""
    pairs = _shared(left.insts, right.insts, off)
    if not pairs:
        return ["instr    no frame names an instrument on both sides"]
    share, votes, onto = _bijection(pairs)
    toks = ["ours %02d = theirs %02d" % (a, b) for a, b in votes[:cap]]
    if len(votes) > cap:
        toks.append("...(+%d)" % (len(votes) - cap))
    return [
        "instr    %d emits name an instrument on both sides, %.1f%% consistent over %d rows, %s"
        % (len(pairs), share, len(votes), "a bijection" if onto else "NOT a bijection")
    ] + _wrap(toks)


def _index_nodes(graph):
    """``(index sources, tables they row)``: the arrangement a graph represents."""
    src = sum(1 for g in graph.nodes if g.route == tracker.INDEX)
    read = sum(
        1
        for g in graph.nodes
        if g.transfer[0] == "SELECT" and len(g.transfer[2]) == 2 and g.transfer[2][0] == "node"
    )
    return src, read


def _structure(sides, facts):
    """The arrangement axis: what either side represents of the song's own structure."""
    got = [(s, _index_nodes(s.graph)) for s in sides]
    out = [
        "arrange  %s" % ", ".join("%s %d" % (s.label, n[0]) for s, n in got)
        + " generators addressing another generator's index, "
        + ", ".join("%s %d" % (s.label, n[1]) for s, n in got)
        + " tables read at a generated row"
    ]
    if facts:
        out.append(
            "         the song itself holds %s" % ", ".join("%d %s" % (n, k) for k, n in facts)
        )
    if not any(n[0] for _s, n in got):
        out.append(
            "         a row stream is a recovered index on both sides, never a generated one"
        )
    return out


_CMPFMT = "%-16s %7d %7d %5.1f%% | %7d %7d %5.1f%% | %s"


def _cov_row(name, left, right):
    """One plane, both sides, and which way the difference runs."""
    d = left[0] - right[0]
    verdict = "same" if d == 0 else ("ours +%d" % d if d > 0 else "theirs +%d" % -d)
    return _CMPFMT % ((name,) + left + (_pct(*left),) + right + (_pct(*right),) + (verdict,))


def compare(sides, nframes, title="graph", facts=()):
    """Two ``Side``s of one tune through one emitter: what they agree on, then both.

    ``facts`` are the song's own structure counts, which neither graph holds; they are
    stated so the gap between a reproduced tune and a recovered arrangement is explicit."""
    left, right = sides
    scans = [_scanned(s.graph, s.frames or nframes - s.offset, s.prog) for s in sides]
    out = [_RULE, "compare  %s  %d frames (%s)" % (title, nframes, _mmss(nframes))]
    for s, (_k, sc) in zip(sides, scans):
        out.append(
            "  %-8s %-46s law %-5s %d/%d = %.1f%% generated"
            % (
                s.label,
                s.what or "",
                s.law or "?",
                sc.cov.interp,
                sc.cov.total,
                _pct(sc.cov.interp, sc.cov.total),
            )
        )
    out += [
        "",
        "%-16s %7s %7s %6s | %7s %7s %6s | %s"
        % ("plane", "ours", "of", "share", "theirs", "of", "share", "difference"),
    ]
    a, b = scans[0][1].cov, scans[1][1].cov
    for p in _PLANES:
        if p in a.planes or p in b.planes:
            out.append(_cov_row(_PLANE_NAME[p], a.planes.get(p, (0, 0)), b.planes.get(p, (0, 0))))
    out.append(_cov_row("all", (a.interp, a.total), (b.interp, b.total)))
    out += ["", "; ---- what the two agree on ----"]
    out += _note_agreement(scans[0][1], scans[1][1], right.offset - left.offset)
    out += _inst_agreement(scans[0][1], scans[1][1], right.offset - left.offset)
    out += _structure(sides, facts)
    for s, (keys, sc) in zip(sides, scans):
        out += ["", _RULE, "; ===== %s: %s =====" % (s.label, s.what or "")]
        out += _rendered(s.graph, sc, keys, s.prog, "%s  [%s]" % (title, s.label), s.law)
    return _joined(out)
