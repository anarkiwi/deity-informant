"""trackertext — a tracker ``Graph`` rendered as text, in the musical domain, for review.

Every line comes from the graph: node ``(transfer, trigger, route)`` triples, the declared
tables those transfers hold, and ``Graph.classes``; the observed frames are read only through
the ``RAW`` node, which is labelled. Voices, notes, instruments and tables, no addresses."""

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
_SECT = ("", " hold", " gate-", " gate+")  # ctrl rows: lane byte, held, gate cleared, gate set
_WAVES = ((0x80, "noise"), (0x40, "pulse"), (0x20, "saw"), (0x10, "tri"))
_FLAGS = ((0x08, "test"), (0x04, "ring"), (0x02, "sync"))
_RULE = "=" * 96

Scan = namedtuple("Scan", "cov emits gen res notes tabs ramps frames")


# ---- 1. musical names: what a register is, what a byte says ----------------------
def _role(reg):
    """``voice 1 waveform`` / ``filter cutoff hi``: the part a register plays."""
    if reg <= tracker._VOICE_HI:
        return "voice %d %s" % (reg // 7 + 1, _VOICE_ROLE[reg % 7])
    return "filter %s" % _FILT_ROLE.get(reg, "reg %d" % reg)


def _lane_role(reg):
    """The role of a table lane feeding ``reg``, with the voice dropped."""
    return _VOICE_ROLE[reg % 7] if reg <= tracker._VOICE_HI else _FILT_ROLE.get(reg, "filter")


def _note_name(idx):
    """Note name for a semitone index, as the pitch table is indexed."""
    return "%s%d" % (tracker._NOTE_NAMES[idx % 12], idx // 12)


def _byte_str(reg, b):
    """One declared byte in the terms of its register: waveform bits, ADSR nibbles, level."""
    if reg <= tracker._VOICE_HI and reg % 7 == tracker._CTRL:
        got = [n for m, n in _WAVES if b & m] + [n for m, n in _FLAGS if b & m]
        return "+".join(got or ["silent"]) + ("+gate" if b & 1 else "")
    if reg <= tracker._VOICE_HI and reg % 7 == 5:
        return "A%X D%X" % (b >> 4, b & 15)
    if reg <= tracker._VOICE_HI and reg % 7 == 6:
        return "S%X R%X" % (b >> 4, b & 15)
    return "%02X" % b


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
    """Per node, ``(table id, lane bytes, sections)`` for a SELECT, else None."""
    out = []
    for g in graph.nodes:
        if g.transfer[0] != "SELECT" or g.route[0] != "plane":
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
            self.lanes.setdefault(key[0], {})[_lane_role(g.route[1])] = key[1]
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


def _scan(graph, nframes, keys, tabs):
    """One streaming pass: counts, first-use ordinals, note runs and sweep samples.

    Linear in frames and constant in memory per node apart from the note runs, so a whole
    tune renders without materialising its frames."""
    nodes = graph.nodes
    counts, emits = [0] * len(nodes), [0] * len(nodes)
    gen, res, ramps, notes = {}, {}, {}, [[], [], []]
    pitch = graph.freq_table
    for f in range(nframes):
        fires = tracker._fired(nodes, f)
        cur = {}
        for i, g in enumerate(nodes):
            if not fires[i]:
                continue
            if g.transfer[0] == "RAW":
                for reg, _v in g.transfer[1][f] if f < len(g.transfer[1]) else ():
                    res[reg] = res.get(reg, 0) + 1
                continue
            if g.route[0] != "plane":
                counts[i] += fires[i]
                continue
            for _t in range(fires[i]):
                counts[i] += 1
                v = tracker._emit(g, counts[i])
                if v is None:
                    continue
                emits[i] += 1
                gen[g.route[1]] = gen.get(g.route[1], 0) + 1
                cur[g.route[1]] = v & 0xFF
                if keys[i] is not None:
                    seq = g.transfer[2]
                    tabs.see(keys[i][0], seq[(counts[i] - 1) % len(seq)], len(keys[i][1]))
                elif g.transfer[0] == "RAMP":
                    ramps.setdefault(i, [[], []])[0 if emits[i] <= 12 else 1].append(v & 0xFF)
                    del ramps[i][1][:-4]
        for v in range(3):
            lo, hi = cur.get(7 * v), cur.get(7 * v + 1)
            note = None if lo is None or hi is None or pitch is None else _word_note(pitch, lo, hi)
            if note is not None:
                _run_note(notes[v], f, note)
    cov = tracker._coverage(gen, res, graph.classes)
    return Scan(cov, emits, gen, res, notes, tabs, ramps, nframes)


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


def _stream(items, fmt, width=84):
    """A row stream: run-length tokens with repeated blocks factored out.

    Nothing is dropped silently — a stream cut at ``width`` reports the blocks and the
    rows left out."""
    rle = _rle(items)
    groups = _cycles(rle)
    out, used = [], 0
    for k, (i, j, p) in enumerate(groups):
        toks = [fmt(v) + ("" if n == 1 else " x%d" % n) for v, n in rle[i : i + p]]
        reps = (j - i) // p
        tok = _block(toks) if reps == 1 else "[%s] x%d" % (_block(toks), reps)
        if out and used + len(tok) > width:
            left = sum(n for _v, n in rle[i:])
            out.append("...(+%d blocks, %d rows)" % (len(groups) - k, left))
            break
        out.append(tok)
        used += len(tok) + 2
    return "  ".join(out)


# ---- 5. header: the law verdict and the coverage partition -----------------------
_COVFMT = "%-16s %7d %7d  %5.1f%% | %6d %6d %6d | %6d %6d | %6d"
_COVHDR = "%-16s %7s %7s  %6s | %6s %6s %6s | %6s %6s | %6s" % (
    ("plane", "gen", "total", "share") + _STRONG + _SHALLOW + ("note",)
)


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
    keys = _STRONG + _SHALLOW + ("note",)
    tot = dict.fromkeys(keys, 0)
    for p in _PLANES:
        if p not in cov.planes:
            continue
        it, all_ = cov.planes[p]
        cls = dict((graph.classes or {}).get(p, {}))
        cls["note"] = it - sum(cls.values())
        for k in keys:
            tot[k] += cls.get(k, 0)
        row = (_PLANE_NAME[p], it, all_, _pct(it, all_))
        out.append(_COVFMT % (row + tuple(cls.get(k, 0) for k in keys)))
    all_row = ("all", cov.interp, cov.total, _pct(cov.interp, cov.total))
    return out + [
        _COVFMT % (all_row + tuple(tot[k] for k in keys)),
        "         strong = a declared table byte at a recovered row (lane/gate), or generated"
        " from one (ramp)",
        "         shallow = imm: a program constant, no row explained; seed: the observed byte"
        " a sweep starts from",
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


_REG_OF_ROLE = {r: i for i, r in enumerate(_VOICE_ROLE)}


def _instruments(scan, cap=48):
    """The rows the generators read from the instrument tables, as an editor's list."""
    tabs = scan.tabs
    used = sorted((n, k) for k, n in tabs.inst.items())
    if not used:
        return []
    roles = sorted({r for _n, (tid, _row) in used for r in tabs.lanes.get(tid, {})})
    out = [
        "",
        "; ---- instruments (rows of the instrument tables, numbered by first appearance) ----",
        "  entry     " + "".join("%-20s" % r for r in roles),
    ]
    for n, (tid, row) in used[:cap]:
        cells = []
        for r in roles:
            lane = tabs.lanes.get(tid, {}).get(r)
            reg = _REG_OF_ROLE.get(r, tracker._CTRL)
            cells.append("%-20s" % (_byte_str(reg, lane[row]) if lane and row < len(lane) else "-"))
        out.append("  %-4s %02d   %s" % (tabs.role.get(tid, "?")[:4], n, "".join(cells)))
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
    """Where a node's emits go, in musical terms."""
    if g.route[0] == "plane":
        return "-> " + _role(g.route[1])
    return "-> " + ("triggers" if g.route[0] == "fire" else "unexplained writes")


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


def _select_lines(g, key, tabs, n_emits):
    """A ``SELECT``: which table lane it reads, its row stream, and what the bytes say."""
    tid, lane, sections = key
    reg = g.route[1]
    return [
        "     reads  %s %s lane, %d rows%s"
        % (tabs.name(tid), _lane_role(reg), len(lane), "" if sections == 1 else " + 3 gate images"),
        "     rows   %s" % _stream(g.transfer[2], lambda r: tabs.row_str(tid, r, len(lane))),
        "     emits  %d" % n_emits,
    ]


def _node_lines(graph, i, scan, keys):
    """One node: its transfer, what triggers it, where it routes, and its detail."""
    g = graph.nodes[i]
    kind = g.transfer[0]
    head = "n%02d  %-6s %-28s %s" % (i, kind, _route(g), _trig(graph, g))
    if kind == "EDGE":
        seen = [j for j, h in enumerate(graph.nodes) if h.trigger == ("event", i)]
        return [
            "%s  %d fires over %d frames -> %s"
            % (head, sum(g.transfer[1]), len(g.transfer[1]), " ".join("n%02d" % j for j in seen)),
            "     when   %s" % _fires_str(g.transfer[1]),
        ]
    if kind == "SELECT" and keys[i] is not None:
        return [head] + _select_lines(g, keys[i], scan.tabs, scan.emits[i])
    if kind == "RAMP":
        _k, seed, step, bound = g.transfer
        head_v, tail_v = scan.ramps.get(i, ([], []))
        return [
            head,
            "     sweep  starts at %02X (OBSERVED), steps %+d per fire, wraps at %d"
            % (seed, step, bound),
            "     values %s%s  (%d emits)"
            % (
                " ".join("%02X" % v for v in head_v),
                " .. " + " ".join("%02X" % v for v in tail_v) if tail_v else "",
                scan.emits[i],
            ),
        ]
    if kind == "LOOKUP":
        seq = g.transfer[1]
        vals = [v for v in seq if v is not None]
        what = (
            "constant %s, %d times" % (_byte_str(g.route[1], seq[0]), scan.emits[i])
            if len(seq) == 1
            else "%d of %d entries, %d distinct (see the note lane)"
            % (scan.emits[i], len(seq), len(set(vals)))
        )
        return [head, "     emits  %s" % what]
    if kind == "RAW":
        rows = g.transfer[1]
        return [
            "%s  %d writes over %d frames  (OBSERVED — see residual)"
            % (head, sum(len(r) for r in rows), len(rows))
        ]
    return ["%s  one tick per %s" % (head, g.transfer[1])]


def _generators(graph, scan, keys):
    """Every node, in graph order, so a rendering is diffable between runs and tunes."""
    out = [
        "",
        "; ---- generators: (transfer, trigger, route) per node ----",
        "; a row past the end of its lane is that row's byte held: 'hold', 'gate-' or 'gate+'",
        "; every EDGE fire time is OBSERVED — the trigger floor, no generator produces them",
    ]
    for i in range(len(graph.nodes)):
        out += _node_lines(graph, i, scan, keys)
    return out


# ---- 8. the note lane, per voice ---------------------------------------------------
def _run_str(r):
    """One note run: name, length in frames, and its detune in cents."""
    det = "" if r[5] == r[6] == 0 else " %+d..%+dc" % (r[5], r[6])
    return "%s%s%s" % (r[3], "" if r[4] == 1 else " x%d" % r[4], det)


def _voices(graph, scan, cap=24):
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
def emit(graph, nframes, prog=None, title="graph", law=None):
    """The rendering: header, engine, instruments, generators, note lanes, residual."""
    keys = _keys(graph, _decl_index(prog))
    scan = _scan(graph, nframes, keys, _Tables(graph, keys))
    out = _header(graph, scan, title, law)
    out += _engine(graph, prog, scan)
    out += _instruments(scan)
    out += _generators(graph, scan, keys)
    out += _voices(graph, scan)
    out += _residual(graph, scan)
    return "\n".join(out + [_RULE, ""])
