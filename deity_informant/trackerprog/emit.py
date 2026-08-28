"""T3 -- the trackerprog lifted from T2 and the observable, and its print.

The score is T2's: an order of patterns whose rows hold for the ticks the cursor
did. What a row *sounds* like is lifted as a stream (section 3.3): the ordered
edge writes and the level values each tick of the row left in the observable,
as steps with holds, relative to the row's note where a value is a pitch entry.
Equal streams are one stream, and a row's instrument is the stream it arms. The
global channel -- cutoff, resonance, volume -- is one stream over the horizon.
Nothing here reads a tune's code: a residue can only come from T2 (a voice with
no cursor-shaped score), and it is refused by name.
"""

from __future__ import annotations

import json
import lzma
import re

from ..tuneprog import grid
from ..tuneprog.facts import SID_VOICE, SID_VOICES, VOICE_REG
from . import player
from .refuse import Refusal

PW_MASK = grid.PAIRS[3][3]


def fetch_ticks(t2, voice):
    """The ticks a voice's pattern channel fetched a row, from T2's events."""
    out = set()
    for v in t2["score"]:
        if v["copy"] != voice:
            continue
        for ch in v.get("pattern", ()):
            out |= {e["tick"] for e in ch["events"] if e["bytes"]}
    return out


def commit_order(obs):
    """The per-voice ad/sr/ctrl order the source writes: the first tick writing all three."""
    for o in obs or ():
        for v in range(SID_VOICES):
            got = [VOICE_REG[r % SID_VOICE] for r, _val in o.edges if r // SID_VOICE == v]
            names = [n for n in dict.fromkeys(got) if n in ("ad", "sr", "ctrl")]
            if len(names) == 3:
                return tuple(names)
    return player.DEFAULT_ORDER


def _note(pitch, value, index):
    """The pitch index ``value`` is an entry at, nearest ``index`` first, or ``None``."""
    if value is None or not pitch:
        return None
    hits = index.get(value)
    return None if not hits else min(hits, key=lambda i: abs(i - (index.get("row") or 0)))


def _nearest(pitch, value):
    """The pitch index closest to a value that is no entry."""
    return min(range(len(pitch)), key=lambda i: abs(pitch[i] - value))


def _key(x):
    return json.dumps(x, sort_keys=True)


def _stream(ticks):
    """A lane stream: its per-tick set lists as steps."""
    return {"rate": 1, "steps": steps_of(ticks), "loop": None}


class Streams:
    """Streams and instruments deduplicated by content, a prefix being its stream cut short."""

    def __init__(self):
        self.ids, self.out = {}, {}
        self.ins, self.instruments = {}, {}
        self.ticks = {}  # lane stream id -> its per-tick lists, the longest seen

    def add(self, steps, loop=None):
        key = _key([steps, loop])
        if key not in self.ids:
            self.ids[key] = "s%d" % len(self.ids)
            self.out[self.ids[key]] = {"rate": 1, "steps": steps, "loop": loop}
        return self.ids[key]

    def lane(self, ticks):
        """The stream these per-tick lists are a prefix of, extended where they reach further.

        A row cuts its instrument's stream at its own length, so the sound of a
        short note is the sound of a long one stopped early: one stream serves both.
        """
        key = _key(ticks)
        if key in self.ids:
            return self.ids[key]
        for sid, have in self.ticks.items():
            n = min(len(have), len(ticks))
            if have[:n] == ticks[:n]:
                if len(ticks) > len(have):
                    self.ticks[sid] = ticks
                    self.out[sid] = _stream(ticks)
                self.ids[key] = sid
                return sid
        sid = self.ids[key] = "s%d" % len(self.ids)
        self.ticks[sid] = ticks
        self.out[sid] = _stream(ticks)
        return sid

    def instrument(self, by_lane):
        """One instrument per distinct tuple of lane streams."""
        ref = {lane: self.lane(ticks) for lane, ticks in by_lane.items()}
        key = _key(ref)
        if key not in self.ins:
            self.ins[key] = "i%d" % len(self.ins)
            self.instruments[self.ins[key]] = ref
        return self.ins[key]


MAXSTEP, MAXSHIFT = 32, 7


def tablestep(pitch, idx, d):
    """``(m, shift)`` with ``d == m * ((pitch[idx+1] - pitch[idx]) >> shift)``, or ``None``.

    Section 5's ``tablestep``: a vibrato or slide in units of the semitone above
    the note, which is the same stream at every note.
    """
    if idx is None or not 0 <= idx + 1 < len(pitch):
        return None
    semi = pitch[idx + 1] - pitch[idx]
    hits = []
    for shift in range(MAXSHIFT + 1):
        unit = semi >> shift
        if unit > 0 and d % unit == 0 and 0 < abs(d // unit) <= MAXSTEP:
            hits.append((abs(d // unit), shift, d // unit))
    return None if not hits else (min(hits)[2], min(hits)[1])


def _delta(prev, val, key, width):
    """A level as its step from the previous tick where one is known, else absolute."""
    if prev is None:
        return (key, val)
    d = (val - prev) % (1 << width)
    return (key + "_delta", d - (1 << width) if d >= 1 << (width - 1) else d)


MAXCYCLE = 8


def _cycle(ticks, i):
    """``(length, times)`` of the longest run of a repeated cycle starting at ``i``."""
    best = (0, 0, 0)
    for n in range(1, MAXCYCLE + 1):
        if i + 2 * n > len(ticks):
            break
        unit = ticks[i : i + n]
        k = 1
        while ticks[i + k * n : i + (k + 1) * n] == unit:
            k += 1
        if k >= 2 and n * k > best[0]:
            best = (n * k, n, k)
    return best[1], best[2]


def steps_of(ticks):
    """Per-tick set lists as steps: a cycle repeated ``times``, or sets held ``hold`` ticks.

    A run of one set list every tick is a cycle of length one; a set list followed
    by empty ticks is that step with a hold. The closed form of what a table walk
    with holds, a repeated write and a periodic modulation each leave.
    """
    steps, i = [], 0
    while i < len(ticks):
        n, k = _cycle(ticks, i)
        if n and not (n == 1 and not ticks[i]):
            steps.append({"cycle": ticks[i : i + n], "times": k})
            i += n * k
            continue
        sets = ticks[i]
        hold = 1
        while i + hold < len(ticks) and not ticks[i + hold]:
            hold += 1
        steps.append({"hold": hold, "sets": sets})
        i += hold
    return steps


LANES = {
    "ad": "ad",
    "sr": "sr",
    "ctrl": "ctrl",
    "pw": "pulse",
    "pw_delta": "pulse",
    "note_off": "note",
    "note_abs": "note",
}
EDGES = ("ad", "sr", "ctrl")


def lanes(ticks, split=True):
    """The per-tick set lists split into lanes: one per edge register where the tune
    keeps one order between them (``meta.commit_order``), else one wave lane; note,
    pitch and pulse."""
    names = ("ad", "sr", "ctrl") if split else ("wave",)
    out = {n: [] for n in names + ("note", "pitch", "pulse")}
    for sets in ticks:
        for lane, got in out.items():
            got.append(
                [s for s in sets if (LANES.get(s[0], "pitch") if split else _wave(s[0])) == lane]
            )
    return out


def _wave(reg):
    return "wave" if reg in EDGES else LANES.get(reg, "pitch")


def ordered(obs):
    """The one order the edge registers keep inside every tick of every voice, or ``None``.

    Every tick's writes to a voice must be sorted by it, with no register written
    twice around another (``ad, sr, ad, sr`` keeps no such order).
    """
    seen = set()
    for ob in obs:
        per = {}
        for r, _v in ob.edges:
            per.setdefault(r // SID_VOICE, []).append(VOICE_REG[r % SID_VOICE])
        seen.update(tuple(regs) for regs in per.values())
    order = tuple(dict.fromkeys(max(seen, key=lambda s: len(set(s)), default=())))
    for got in seen:
        ranks = [order.index(r) if r in order else -1 for r in got]
        if -1 in ranks or ranks != sorted(ranks):
            return None
    return order


def row_stream(obs, voice, start, dur, pitch, index, note, state):
    """One row's observable as steps: edges in order, levels as steps from the last tick.

    ``state`` carries the voice's last frequency and pulse across rows, so a sweep
    continuing into the next row is the same delta step there.
    """
    ticks = []
    for o in range(dur):
        t = start + o
        if t >= len(obs):
            break
        ob = obs[t]
        sets = [[VOICE_REG[r % SID_VOICE], val] for r, val in ob.edges if r // SID_VOICE == voice]
        f = ob.values[voice]
        if f is not None and note is None:  # the voice's first frequency, inside this row
            note = _note(pitch, f, {**index, "row": 0})
            note = _nearest(pitch, f) if note is None else note
            sets.append(["note_abs", note])
        if f is not None:
            want = _note(pitch, f, {**index, "row": note})
            mode = ("pitch", want) if want is not None else "abs"
            if mode != state.get("mode") or (mode == "abs" and f != state.get("freq")):
                if want is not None:
                    sets.append(["note_off", want - note])
                    state["index"] = want
                else:
                    prev = state.get("freq")
                    ts = (
                        tablestep(pitch, state.get("index"), f - prev) if prev is not None else None
                    )
                    sets.append(["freq_ts", *ts] if ts else list(_delta(prev, f, "freq", 16)))
            state["mode"], state["freq"] = mode, f
        pw = ob.values[SID_VOICES + voice]
        if pw is not None and pw != state.get("pw"):
            sets.append(list(_delta(state.get("pw"), pw, "pw", 12)))
            state["pw"] = pw
        ticks.append(sets)
    state["note"] = note
    return lanes(ticks, state.get("split", True)), note


GLOBAL = (("cutoff", 6, 11), ("res_route", 7, 8), ("mode_vol", 8, 8))


def global_rows(obs, span, loop):
    """The global channel over ``span`` ticks as one row, cut at the loop."""
    cuts = sorted({0, span} | ({loop} if loop else set()))
    out, prev = [], {}
    for a, b in zip(cuts, cuts[1:]):
        lanes_ = {name: [] for name, _i, _w in GLOBAL}
        for ob in obs[a:b]:
            for name, i, w in GLOBAL:
                val, sets = ob.values[i], []
                if val is not None and prev.get(name) != val:
                    sets.append(list(_delta(prev.get(name), val, name, w)))
                    prev[name] = val
                lanes_[name].append(sets)
        out.append((a, b - a, lanes_))
    return out


def horizon(cert, obs):
    """``(span, loop tick)``: the period a complete source is materialised over (section 6).

    ``first_repeat`` is the tick whose post-state first repeats one a period back,
    so the tick after it plays as the tick after that one did.
    """
    sub = ((cert or {}).get("subtunes") or [{}])[0]
    p, f = sub.get("period") or 0, sub.get("first_repeat")
    span = f + 1 if f is not None and sub.get("complete") and p > 1 else None
    if span is not None and p <= span <= len(obs):
        return span, span - p
    return len(obs), None


def _rows(t2, voice, span, loop=None):
    """``[(tick, dur, base, pos, bytes)]`` of a voice's pattern channel over ``span`` ticks.

    A row the loop tick falls inside is cut there: its remainder is the row the
    loop re-enters, and by the period it is the same remainder the span ends on.
    """
    for v in t2["score"]:
        if v["copy"] == voice and v.get("pattern"):
            out = []
            for e in v["pattern"][0]["events"]:
                t, n = e["tick"], min(e["ticks"], span - e["tick"])
                if n <= 0 or t >= span:
                    continue
                if loop is not None and t < loop < t + n:
                    out.append((t, loop - t, e["base"], e["pos"], e["bytes"]))
                    t, n = loop, t + n - loop
                out.append((t, n, e["base"], e["pos"], e["bytes"]))
            return out
    return None


def _continues(prev, row, loop):
    """True when a row only holds the one before: same pattern and note, and its first tick's
    writes are the previous tick's again -- a flush repeating, not an event."""
    if row["tick"] == loop or row["base"] != prev["base"] or row["note"] != prev["note"]:
        return False
    edges = [l for l in row["lanes"] if l in EDGES or l == "wave"]
    same = all(row["lanes"][l][0] in ([], prev["lanes"][l][-1]) for l in edges)
    return same and not row["lanes"]["note"][0]


def _visits(rows):
    out = []
    for r in rows:
        if out and out[-1][-1]["base"] == r["base"]:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def _levels(ob, voice):
    """The absolute levels a voice carries into a tick: what ``enter`` restores at a loop."""
    out = []
    if ob.values[voice] is not None:
        out.append(["freq", ob.values[voice]])
    if ob.values[SID_VOICES + voice] is not None:
        out.append(["pw", ob.values[SID_VOICES + voice]])
    return out


def lift(t2, obs, meta, cert):
    """``(trackerprog, refusals)``: the score from T2, the sounds from the observable."""
    pitch = list((t2.get("pitch") or {}).get("entries") or [])
    index = {}
    for i, p in enumerate(pitch):
        index.setdefault(p, []).append(i)
    streams, voices, refusals = Streams(), [], list(t2["refusals"])
    span, loop = horizon(cert, obs)
    order = ordered(obs)
    for entry in (meta.get("schedule") or [])[1:]:  # a second entry is a mixer: digis are no score
        refusals.append(
            Refusal(
                "sample stream",
                "mode_vol",
                "$%04X" % entry.get("addr", 0),
                "second entry %s" % entry.get("kind"),
            ).to_dict()
        )
    for voice in range(SID_VOICES):
        rows = _rows(t2, voice, span, loop)
        if rows is None:
            if any(r // SID_VOICE == voice for o in obs for r, _v in o.edges):
                refusals.append(
                    Refusal(
                        "score not cursor-shaped", "voice %d" % voice, "", "no pattern channel"
                    ).to_dict()
                )
            continue
        decoded, state = [], {"split": order is not None}
        for tick, dur, base, pos, _bytes in rows:
            f0 = obs[tick].values[voice] if tick < len(obs) else None
            last = decoded[-1]["note"] if decoded and decoded[-1]["note"] is not None else 0
            note = _note(pitch, f0, {**index, "row": last})
            if note is None and f0 is not None and pitch:
                note = _nearest(pitch, f0)
            steps, note = row_stream(obs, voice, tick, dur, pitch, index, note, state)
            row = {"dur": dur, "note": note, "lanes": steps, "base": base, "pos": pos, "tick": tick}
            if decoded and _continues(decoded[-1], row, loop):
                decoded[-1]["dur"] += dur
                for lane, ticks in steps.items():
                    decoded[-1]["lanes"][lane] += ticks
            else:
                decoded.append(row)
        for row in decoded:
            row["ins"] = streams.instrument(row.pop("lanes"))
            row["cmds"] = []
            if row["tick"] == loop and loop > 0:
                row["enter"] = _levels(obs[loop - 1], voice)
        voices.append(_score(decoded, loop))
    sub = ((cert or {}).get("subtunes") or [{}])[0]
    if loop is not None:
        end = {"kind": "loop", "tick": loop, "span": span}
    elif sub.get("complete"):
        end = {"kind": "fixed_point"}
    else:
        end = {"kind": "horizon"}
    glob = None
    if obs:
        rows = []
        for tick, dur, lanes_ in global_rows(obs, span, loop):
            rows.append({"dur": dur, "note": None, "lanes": lanes_, "tick": tick, "base": 0})
        for row in rows:
            row["ins"] = streams.instrument(row.pop("lanes"))
            row["cmds"] = []
            if row["tick"] == loop and loop > 0:
                ob = obs[loop - 1]
                row["enter"] = [
                    [n, ob.values[i]] for n, i, _w in GLOBAL if ob.values[i] is not None
                ]
        glob = _score(rows, loop)
    tp = {
        "meta": {
            "cadence": {
                "cycles_per_tick": meta["entry"]["cycles_per_tick"],
                "source": meta["entry"]["source"],
            },
            "source": {"tune": meta.get("name"), "song": meta.get("song"), "family": None},
            "sid_model": meta.get("sid_model"),
            "player": "universal/2",
            "commit_order": list(order or commit_order(obs)),
        },
        "pitch": pitch,
        "streams": streams.out,
        "accs": {},
        "instruments": streams.instruments,
        "score": {"voices": voices, "end": end, "global": glob},
        "globals": {},
    }
    return tp, list({(r["why"], r["cell"], r["site"]): r for r in refusals}.values())


def _score(decoded, loop):
    """A voice's rows as an order of patterns, one per visit of a base, transposed."""
    patterns, order, pats, target = {}, [], {}, None
    for visit in _visits(decoded):
        if loop is not None and target is None:
            hit = next((j for j, r in enumerate(visit) if r["tick"] == loop), None)
            if hit is not None:
                target = {"pos": len(order), "row": hit}
        first = next((r["note"] for r in visit if r["note"] is not None), 0)
        key = tuple(
            (
                r["dur"],
                None if r["note"] is None else r["note"] - first,
                r["ins"],
                _key(r.get("enter")),
            )
            for r in visit
        )
        if key not in patterns:
            patterns[key] = "p%d" % len(patterns)
            pats[patterns[key]] = [
                {k: r[k] for k in ("dur", "note", "ins", "cmds", "enter") if k in r} for r in visit
            ]
            for row, r in zip(pats[patterns[key]], visit):
                row["note"] = None if r["note"] is None else r["note"] - first
        order.append({"pattern": patterns[key], "transpose": first})
    return {"order": order, "patterns": pats, "loop": target}


def document(view, t2, cert, obs, t1=None):
    """``(trackerprog, refusals)``, T1's accumulators carried as annotations."""
    tp, refusals = lift(t2, obs, view.meta, cert)
    tp["accs"] = {a["id"]: a for a in (t1 or {}).get("accs", ())}
    return tp, refusals


# ---- the print and its measure -----------------------------------------------------
def render(tp):
    """``trackerprog.md``: meta, pitch, streams, then the score."""
    m = tp["meta"]
    out = [
        "# trackerprog: %s" % m["source"]["tune"],
        "",
        "## meta",
        "",
        "```",
        "cadence   every %d cycles (%s)"
        % (m["cadence"]["cycles_per_tick"], m["cadence"]["source"]),
        "player    %s, commit order %s" % (m["player"], "/".join(m["commit_order"])),
        "end       %s" % tp["score"]["end"]["kind"],
        "streams   %d, instruments %d" % (len(tp["streams"]), len(tp["instruments"])),
        "```",
        "",
        "## pitch",
        "",
        "```",
        " ".join("%04X" % x for x in tp["pitch"] or ()),
        "```",
        "",
        "## streams",
        "",
        "```",
    ]
    for sid, s in tp["streams"].items():
        out.append("%s:" % sid)
        for st in s["steps"]:
            if "cycle" in st:
                body = " | ".join(" ".join(_set(x) for x in sets) for sets in st["cycle"])
                out.append("  x%d %s" % (st["times"], body))
            else:
                out.append("  %3d %s" % (st["hold"], " ".join(_set(x) for x in st["sets"])))
    out += ["```", "", "## score", ""]
    glob = tp["score"].get("global")
    for v, voice in enumerate(tp["score"]["voices"] + ([glob] if glob else [])):
        name = "global" if voice is glob else "voice %d" % v
        out += ["```", "%s: order %s" % (name, " ".join(o["pattern"] for o in voice["order"]))]
        for pid, rows in voice["patterns"].items():
            out.append("%s:" % pid)
            for r in rows:
                out.append(
                    "  %-4s %-5s %3d"
                    % ("---" if r["note"] is None else r["note"], r["ins"] or "", r["dur"])
                )
        out += ["```", ""]
    return "\n".join(out)


def _set(x):
    return "%s=%s" % (x[0], ",".join(str(v) for v in x[1:]))


TOKEN = re.compile(r"\$?\w+|\S")


def measure(md, section):
    """Architecture 6.2's tokens and lines over ``section``, header rows before it, ``xz -9e``."""
    body = md.split("## %s" % section, 1)[1] if "## %s" % section in md else ""
    lines = [l for l in body.splitlines() if l.strip() and not l.startswith("```")]
    head = md.split("## meta", 1)[1].split("## ", 1)[0] if "## meta" in md else ""
    hdr = [l for l in head.splitlines() if l.strip() and not l.startswith("```")]
    return {
        "tokens": sum(len(TOKEN.findall(l)) for l in lines),
        "lines": len(lines),
        "header_rows": len(hdr),
        "xz": len(lzma.compress(md.encode(), preset=9 | lzma.PRESET_EXTREME)),
    }


def numbers(tp, md):
    """The six numbers of a trackerprog print plus its ``xz -9e`` size.

    Statements are the score's rows and the streams' steps; blocks the patterns and
    streams; data rows the pitch entries and the streams' steps.
    """
    got = measure(md, "score")
    voices = tp["score"]["voices"]
    steps = sum(len(s["steps"]) for s in tp["streams"].values())
    got["statements"] = sum(len(r) for v in voices for r in v["patterns"].values()) + steps
    got["blocks"] = (
        sum(len(v["patterns"]) for v in voices) + len(tp["streams"]) + len(tp["instruments"])
    )
    got["data_rows"] = steps + len(tp["pitch"] or ())
    return got


def numbers_tuneprog(md, view):
    """The same six over a ``tuneprog.md`` and its view (architecture 6.2)."""
    got = measure(md, "program")
    got["statements"] = sum(len(b.stmts) for p in view.procs.values() for b in p.blocks.values())
    got["blocks"] = sum(len(p.blocks) for p in view.procs.values())
    data = md.split("## data", 1)[1].split("## inputs", 1)[0] if "## data" in md else ""
    got["data_rows"] = len([l for l in data.splitlines() if l.strip() and not l.startswith("```")])
    return got


def to_json(tp):
    """S4-style tagged: ``["$trackerprog", meta, pitch, streams, accs, ins, score, globals]``."""

    def enc(x):
        if isinstance(x, dict):
            return {"$dict": [[k, enc(v)] for k, v in x.items()]}
        if isinstance(x, list):
            return [enc(v) for v in x]
        return x

    return [
        "$trackerprog",
        enc(tp["meta"]),
        tp["pitch"],
        enc(tp["streams"]),
        enc(tp["accs"]),
        enc(tp["instruments"]),
        ["$score", enc(tp["score"])],
        enc(tp["globals"]),
    ]


def from_json(doc):
    def dec(x):
        if isinstance(x, dict) and "$dict" in x:
            return {k: dec(v) for k, v in x["$dict"]}
        if isinstance(x, list):
            return [dec(v) for v in x]
        return x

    assert doc[0] == "$trackerprog"
    return {
        "meta": dec(doc[1]),
        "pitch": doc[2],
        "streams": dec(doc[3]),
        "accs": dec(doc[4]),
        "instruments": dec(doc[5]),
        "score": dec(doc[6][1]),
        "globals": dec(doc[7]),
    }
