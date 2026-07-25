"""EXPERIMENTAL (musical-structure study): tiered sidsong prototype texts.

L0 = Gate-F-collapsed canonical event lines; L1 = per-voice streams with
delta-run compression; L2 = per-voice order lists + deduplicated patterns.
Each tier emits, parses and expands back to L0 events for Gate F.
"""

V_BASE = (0, 7, 14)
CTRL_OFFS = (4, 5, 6)
EDGE_KEY = {4: "c", 5: "a", 6: "s"}
KEY_EDGE = {v: k for k, v in EDGE_KEY.items()}


def collapse_state(writes, state=None):
    """Last-write-wins fold of (reg, val) writes into a 25-reg state list."""
    st = [0] * 25 if state is None else list(state)
    for r, v in writes:
        st[r] = v
    return st


# ---- L0 ----------------------------------------------------------------------


def emit_l0(events):
    """Events [(delta, edges)] -> L0 text lines ('+gap:' frame encoding)."""
    out = []
    last = 0
    for f, (delta, edges) in enumerate(events):
        toks = []
        for vi in range(3):
            b = V_BASE[vi]
            for r in (b, b + 1, b + 2, b + 3):
                if r in delta:
                    toks.append("%02X=%02X" % (r, delta[r]))
            toks.extend("%02X=%02X" % (r, v) for r, v in edges[vi])
        for r in (21, 22, 23, 24):
            if r in delta:
                toks.append("%02X=%02X" % (r, delta[r]))
        if toks:
            out.append("+%d: %s" % (f - last, " ".join(toks)))
            last = f
    return "\n".join(out) + "\n"


def parse_l0(text, frames):
    """L0 text -> events [(delta, edges)]."""
    events = [({}, ((), (), ())) for _ in range(frames)]
    last = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] != "+":
            continue
        head, _, rest = line.partition(":")
        f = last + int(head[1:])
        last = f
        delta = {}
        edges = ([], [], [])
        for tok in rest.split():
            rr, vv = tok.split("=")
            r, v = int(rr, 16), int(vv, 16)
            if r < 21 and (r % 7) in CTRL_OFFS:
                edges[r // 7].append((r, v))
            delta[r] = v
        events[f] = (delta, tuple(tuple(e) for e in edges))
    return events


# ---- L1 ----------------------------------------------------------------------


def build_l1(events, init_state=None):
    """Events -> (voice_streams, global_stream) of (frame, kind, value) items.

    kinds: voice 'f'/'p' (16-bit freq/pulse), 'e' (ordered edge list);
    global 'fc' (16-bit cutoff), 'rf', 'vol'.
    """
    state = [0] * 25 if init_state is None else list(init_state)
    voices = ([], [], [])
    glob = []
    for f, (delta, edges) in enumerate(events):
        for vi in range(3):
            b = V_BASE[vi]
            if b in delta or b + 1 in delta:
                st = collapse_state(delta.items(), state)
                voices[vi].append((f, "f", st[b] | (st[b + 1] << 8)))
            if b + 2 in delta or b + 3 in delta:
                st = collapse_state(delta.items(), state)
                voices[vi].append((f, "p", st[b + 2] | (st[b + 3] << 8)))
            if edges[vi]:
                voices[vi].append((f, "e", tuple(edges[vi])))
        if 21 in delta or 22 in delta:
            st = collapse_state(delta.items(), state)
            glob.append((f, "fc", st[21] | (st[22] << 8)))
        if 23 in delta:
            glob.append((f, "rf", delta[23]))
        if 24 in delta:
            glob.append((f, "vol", delta[24]))
        state = collapse_state(delta.items(), state)
    return voices, glob


def rle(stream, min_run=3):
    """Merge consecutive-frame constant-delta numeric runs into '~' items."""
    out = []
    i = 0
    while i < len(stream):
        f, kind, val = stream[i]
        if kind in ("f", "p", "fc"):
            j = i + 1
            d = None
            while j < len(stream):
                fj, kj, vj = stream[j]
                if kj != kind or fj != stream[j - 1][0] + 1:
                    break
                step = vj - stream[j - 1][2]
                if d is None:
                    d = step
                elif step != d:
                    break
                j += 1
            if d is not None and j - i >= min_run:
                out.append((f, kind, val))
                out.append((f + 1, kind + "~", (d, j - i - 1)))
                i = j
                continue
        out.append((f, kind, val))
        i += 1
    return out


def _emit_items(items):
    lines = []
    last = 0
    for f, kind, val in items:
        g = "+%d" % (f - last)
        last = f
        if kind == "e":
            toks = " ".join("%s=%02X" % (EDGE_KEY[r % 7], v) for r, v in val)
            lines.append("%s e %s" % (g, toks))
        elif kind.endswith("~"):
            d, n = val
            lines.append("%s %s%+dx%d" % (g, kind, d, n))
        elif kind in ("f", "p", "fc"):
            lines.append("%s %s=%04X" % (g, kind, val))
        else:
            lines.append("%s %s=%02X" % (g, kind, val))
    return lines


def emit_l1(voices, glob, header=()):
    out = list(header)
    for vi in range(3):
        out.append("voice %d {" % (vi + 1))
        out.extend(_emit_items(rle(voices[vi])))
        out.append("}")
    out.append("global {")
    out.extend(_emit_items(rle(glob)))
    out.append("}")
    return "\n".join(out) + "\n"


def _parse_items(lines):
    items = []
    last = 0
    for line in lines:
        parts = line.split()
        f = last + int(parts[0])
        last = f
        if parts[1] == "e":
            edges = tuple((KEY_EDGE[t.split("=")[0]], int(t.split("=")[1], 16)) for t in parts[2:])
            items.append((f, "e", edges))
        elif "~" in parts[1]:
            kind, spec = parts[1].split("~")
            d, n = spec.split("x")
            items.append((f, kind + "~", (int(d), int(n))))
        else:
            k, v = parts[1].split("=")
            items.append((f, k, int(v, 16)))
    return items


def parse_l1(text):
    """L1 text -> (voices, glob) with runs expanded."""
    voices = [[], [], []]
    glob = []
    cur = None
    buf = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("voice "):
            cur, buf = voices[int(line.split()[1]) - 1], []
        elif line.startswith("global"):
            cur, buf = glob, []
        elif line == "}":
            if cur is not None:
                cur.extend(_parse_items(buf))
            cur = None
        elif cur is not None:
            buf.append(line)
    return [expand_runs(v) for v in voices], expand_runs(glob)


def expand_runs(items):
    out = []
    for f, kind, val in items:
        if kind.endswith("~"):
            d, n = val
            base = out[-1][2]
            for i in range(n):
                out.append((f + i, kind[:-1], base + d * (i + 1)))
        else:
            out.append((f, kind, val))
    return out


def l1_to_events(voices, glob, frames):
    """Expanded L1 streams -> L0 events."""
    events = [[{}, [[], [], []]] for _ in range(frames)]
    for vi in range(3):
        b = V_BASE[vi]
        for f, kind, val in voices[vi]:
            delta, edges = events[f]
            if kind == "f":
                delta[b] = val & 0xFF
                delta[b + 1] = (val >> 8) & 0xFF
            elif kind == "p":
                delta[b + 2] = val & 0xFF
                delta[b + 3] = (val >> 8) & 0xFF
            else:
                rebased = tuple((b + (r % 7), v) for r, v in val)
                edges[vi].extend(rebased)
                for r, v in rebased:
                    delta[r] = v
    for f, kind, val in glob:
        delta = events[f][0]
        if kind == "fc":
            delta[21] = val & 0xFF
            delta[22] = (val >> 8) & 0xFF
        elif kind == "rf":
            delta[23] = val
        else:
            delta[24] = val
    return [(d, tuple(tuple(e) for e in es)) for d, es in events]


# ---- L2 ----------------------------------------------------------------------


def build_l2(voices, boundaries, frames):
    """Per-voice segmentation + dedup -> (orders, patterns per voice).

    boundaries[vi] = ascending segment-start frames (0 implied). A pattern is
    the voice's L1 slice with frames rebased; identical bodies share an id.
    """
    orders = []
    patterns = []
    for vi in range(3):
        bounds = [0] + [f for f in boundaries[vi] if 0 < f < frames] + [frames]
        stream = voices[vi]
        index = {}
        pats = []
        order = []
        for a, b in zip(bounds, bounds[1:]):
            seg = tuple((f - a, k, v) for f, k, v in stream if a <= f < b)
            key = (b - a, seg)
            if key not in index:
                index[key] = len(pats)
                pats.append((b - a, seg))
            order.append(index[key])
        orders.append(order)
        patterns.append(pats)
    return orders, patterns


def emit_l2(orders, patterns, glob, header=()):
    out = list(header)
    for vi in range(3):
        out.append("order %d: %s" % (vi + 1, " ".join("%02d" % p for p in orders[vi])))
    for vi in range(3):
        for pid, (length, seg) in enumerate(patterns[vi]):
            out.append("pattern %d.%02d frames %d {" % (vi + 1, pid, length))
            out.extend(_emit_items(rle(list(seg))))
            out.append("}")
    out.append("global {")
    out.extend(_emit_items(rle(glob)))
    out.append("}")
    return "\n".join(out) + "\n"


def parse_l2(text):
    """L2 text -> (orders, patterns, glob)."""
    orders = [[], [], []]
    patterns = [{}, {}, {}]
    glob = []
    buf = None
    meta = None
    isglob = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("order "):
            head, _, rest = line.partition(":")
            orders[int(head.split()[1]) - 1] = [int(t) for t in rest.split()]
        elif line.startswith("pattern "):
            parts = line.split()
            vi, pid = parts[1].split(".")
            meta = (int(vi) - 1, int(pid), int(parts[3]))
            buf, isglob = [], False
        elif line.startswith("global"):
            buf, meta, isglob = [], None, True
        elif line == "}":
            if meta is not None:
                patterns[meta[0]][meta[1]] = (meta[2], expand_runs(_parse_items(buf)))
                meta = None
            elif isglob:
                glob.extend(expand_runs(_parse_items(buf)))
                isglob = False
            buf = None
        elif buf is not None:
            buf.append(line)
    pats = [[p[i] for i in sorted(p)] for p in patterns]
    return orders, pats, glob


def l2_to_l1(orders, patterns):
    """Concatenate each voice's patterns per its order list."""
    voices = []
    for vi in range(3):
        stream = []
        base = 0
        for pid in orders[vi]:
            length, seg = patterns[vi][pid]
            stream.extend((base + f, k, v) for f, k, v in seg)
            base += length
        voices.append(stream)
    return voices
