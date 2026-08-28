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


class Streams:
    """Streams deduplicated by content; ``ids`` map a step list to its id."""

    def __init__(self):
        self.ids = {}
        self.out = {}

    def add(self, steps):
        key = tuple((s["hold"], tuple(map(tuple, s["sets"]))) for s in steps)
        if key not in self.ids:
            self.ids[key] = "s%d" % len(self.ids)
            self.out[self.ids[key]] = {"rate": 1, "steps": steps, "end": "halt"}
        return self.ids[key]


def row_stream(obs, voice, start, dur, pitch, index, note):
    """One row's observable as steps: edges in order, levels and pitch operands as sets."""
    steps, prev = [], {}
    for o in range(dur):
        t = start + o
        if t >= len(obs):
            break
        ob = obs[t]
        sets = [(VOICE_REG[r % SID_VOICE], val) for r, val in ob.edges if r // SID_VOICE == voice]
        f = ob.values[voice]
        if f is not None and note is None:  # the voice's first frequency, inside this row
            note = _note(pitch, f, {**index, "row": 0})
            note = _nearest(pitch, f) if note is None else note
            sets.append(("note_abs", note))
        if f is not None:
            want = _note(pitch, f, {**index, "row": note})
            key = ("note_off", want - note) if want is not None else ("freq", f)
            if prev.get("freq", ("note_off", 0)) != key:
                sets.append(key)
                prev["freq"] = key
        pw = ob.values[SID_VOICES + voice]
        if pw is not None and prev.get("pw") != pw:
            sets.append(("pw", pw))
            prev["pw"] = pw
        if sets or not steps:
            steps.append({"hold": 1, "sets": [list(s) for s in sets]})
        else:
            steps[-1]["hold"] += 1
    return steps


def global_stream(obs):
    """The global channel over the horizon: cutoff, res_route and mode_vol as they moved."""
    steps, prev = [], {}
    for ob in obs:
        sets = []
        for name, i in (("cutoff", 6), ("res_route", 7), ("mode_vol", 8)):
            val = ob.values[i]
            if val is not None and prev.get(name) != val:
                sets.append([name, val])
                prev[name] = val
        if sets or not steps:
            steps.append({"hold": 1, "sets": sets})
        else:
            steps[-1]["hold"] += 1
    return steps


def _rows(t2, voice):
    """``[(tick, dur, base, pos, bytes)]`` of a voice's pattern channel, the horizon partitioned."""
    for v in t2["score"]:
        if v["copy"] == voice and v.get("pattern"):
            ch = v["pattern"][0]
            return [
                (e["tick"], e["ticks"], e["base"], e["pos"], e["bytes"])
                for e in ch["events"]
                if e["ticks"]
            ]
    return None


def _visits(rows):
    out = []
    for r in rows:
        if out and out[-1][-1]["base"] == r["base"]:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def lift(t2, obs, meta, cert):
    """``(trackerprog, refusals)``: the score from T2, the sounds from the observable."""
    pitch = list((t2.get("pitch") or {}).get("entries") or [])
    index = {}
    for i, p in enumerate(pitch):
        index.setdefault(p, []).append(i)
    streams, voices, refusals = Streams(), [], list(t2["refusals"])
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
        rows = _rows(t2, voice)
        if rows is None:
            if any(r // SID_VOICE == voice for o in obs for r, _v in o.edges):
                refusals.append(
                    Refusal(
                        "score not cursor-shaped", "voice %d" % voice, "", "no pattern channel"
                    ).to_dict()
                )
            continue
        decoded = []
        for tick, dur, base, pos, _bytes in rows:
            f0 = obs[tick].values[voice] if tick < len(obs) else None
            note = _note(
                pitch,
                f0,
                {
                    **index,
                    "row": (
                        decoded[-1]["note"] if decoded and decoded[-1]["note"] is not None else 0
                    ),
                },
            )
            if note is None and f0 is not None and pitch:
                note = _nearest(pitch, f0)
            steps = row_stream(obs, voice, tick, dur, pitch, index, note)
            decoded.append(
                {
                    "dur": dur,
                    "note": note,
                    "ins": streams.add(steps),
                    "cmds": [],
                    "base": base,
                    "pos": pos,
                }
            )
        patterns, order = {}, []
        for visit in _visits(decoded):
            key = tuple((r["dur"], r["note"], r["ins"]) for r in visit)
            if key not in patterns:
                patterns[key] = "p%d" % len(patterns)
            order.append({"pattern": patterns[key], "transpose": 0})
        pats = {}
        for visit, pid in zip(_visits(decoded), [o["pattern"] for o in order]):
            pats.setdefault(pid, [{k: r[k] for k in ("dur", "note", "ins", "cmds")} for r in visit])
        voices.append({"order": order, "patterns": pats})
    sub = ((cert or {}).get("subtunes") or [{}])[0]
    if sub.get("complete") and (sub.get("period") or 0) > 1:
        end = {"kind": "loop", "row": 0}
    elif sub.get("complete"):
        end = {"kind": "fixed_point"}
    else:
        end = {"kind": "horizon"}
    gid = streams.add(global_stream(obs)) if obs else None
    tp = {
        "meta": {
            "cadence": {
                "cycles_per_tick": meta["entry"]["cycles_per_tick"],
                "source": meta["entry"]["source"],
            },
            "source": {"tune": meta.get("name"), "song": meta.get("song"), "family": None},
            "sid_model": meta.get("sid_model"),
            "player": "universal/2",
            "commit_order": list(commit_order(obs)),
        },
        "pitch": pitch,
        "streams": streams.out,
        "accs": {},
        "instruments": {},
        "score": {"voices": voices, "end": end},
        "globals": {"stream": gid},
    }
    return tp, list({(r["why"], r["cell"], r["site"]): r for r in refusals}.values())


def document(view, t2, cert, obs, t1=None):
    """``(trackerprog, refusals)``, T1's accumulators carried as annotations."""
    tp, refusals = lift(t2, obs, view.meta, cert)
    tp["accs"] = {a["id"]: a for a in (t1 or {}).get("accs", ())}
    tp["instruments"] = {s["cursor"]: s for s in t2.get("selectors", ())}
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
        "streams   %d, global %s" % (len(tp["streams"]), tp["globals"].get("stream")),
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
            out.append("  %3d %s" % (st["hold"], " ".join("%s=%s" % (r, v) for r, v in st["sets"])))
    out += ["```", "", "## score", ""]
    for v, voice in enumerate(tp["score"]["voices"]):
        out += ["```", "voice %d: order %s" % (v, " ".join(o["pattern"] for o in voice["order"]))]
        for pid, rows in voice["patterns"].items():
            out.append("%s:" % pid)
            for r in rows:
                out.append(
                    "  %-4s %-5s %3d"
                    % ("---" if r["note"] is None else r["note"], r["ins"] or "", r["dur"])
                )
        out += ["```", ""]
    return "\n".join(out)


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
    got["blocks"] = sum(len(v["patterns"]) for v in voices) + len(tp["streams"])
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
