"""T3 -- the trackerprog lifted from T0, T1 and T2, and its print.

Every SID write site (T0) must be one of the schema's forms -- a pitch lookup
on the score's note, a constant or an instrument column set at the row, a T1
accumulator on a level register -- committed at a moment the universal player
has: the row boundary or every tick. What is not is a named refusal
(``command residue``), and a trackerprog with a refusal is not emitted
(prototype section 8).
"""

from __future__ import annotations

import lzma
import re

import numpy as np

from ..tuneprog.acchist import Cells
from ..tuneprog.accshape import Ctx
from ..tuneprog.facts import VOICE_REG
from ..tuneprog.accguard import afterwrites
from ..tuneprog.ir import Const, Load, Store, W16
from ..tuneprog.irwalk import addr_split
from . import player
from .cursors import TABLE, strides
from .hist import Eval
from .refuse import Refusal
from .resolve import Program, walkx

PAIRS = {
    "freq": ("freq_lo", "freq_hi"),
    "pw": ("pw_lo", "pw_hi"),
    "cutoff": ("cutoff_lo", "cutoff_hi"),
}


# ---- the sites -------------------------------------------------------------------
def _stmt(view, site):
    b = view.procs[site["proc"]].blocks[site["block"]]
    pc = int(site["pc"][1:], 16)
    return next((i, s) for i, s in enumerate(b.stmts) if getattr(s, "src", None) == pc)


def _value_kind(v, rgn, prid, selectors):
    """``("const", k)``, ``("pitch", index, region)``, ``("column", region, sel)`` or ``None``."""
    if type(v) is Const:
        return ("const", v.v)
    if type(v) is Load and v.r in prid:
        return ("pitch", addr_split(v.a)[1], v.r)
    if type(v) is Load and v.r in rgn and rgn[v.r].kind in TABLE:
        for sel in selectors:
            if any(names_match(v.a, sel) for _ in (0,)):
                return ("column", v.r, sel)
    return None


def names_match(addr, sel):
    """True when an address indexes by the selector cell ``(region, address)``."""
    return any(
        type(x) is Load
        and (x.r, addr_split(x.a)[0]) == sel
        and addr_split(x.a)[1] is not None
        or type(x) is Load
        and (x.r, addr_split(x.a)[0]) == sel
        for x in walkx(addr)
    )


def fetch_ticks(t2, voice):
    """The ticks a voice's pattern channel fetched a row, from T2's events."""
    out = set()
    for v in t2["score"]:
        if v["copy"] != voice:
            continue
        for ch in v.get("pattern", ()):
            out |= {e["tick"] for e in ch["events"] if e["bytes"]}
    return out


class Lift:
    """One tune's T3: the sites classified, the score's events decoded, the object built."""

    def __init__(self, view, names, t0, t1, t2, hist, cert, obs=None):
        self.view, self.names, self.t0, self.t1, self.t2, self.cert = view, names, t0, t1, t2, cert
        self.rgn = view.by_id()
        self.ctx = Ctx(view, names)
        self.P = Program(self.ctx)
        self.cells = Cells(view, names, hist)
        self.ev = Eval(self.cells)
        self.stride = strides(view, names)
        self.prid = set((t2.get("pitch") or {}).get("regions") or ())
        self.selectors = {self._selcell(s) for s in t2.get("selectors", ())}
        self.refusals = []
        self.obs = obs
        self.events = {}  # (voice, tick) -> {note, ins, cmds}
        self.rows = {v: set() for v in range(3)}

    def _selcell(self, s):
        name, addr = s["cursor"].split("@$")
        rid = next(r for r, n in self.names.region.items() if n == name)
        return (rid, int(addr, 16))

    def refuse(self, why, cell, site, detail=""):
        self.refusals.append(Refusal(why, cell, site, detail))

    def classify_sites(self):
        for w in self.t0["writes"]:
            if w.get("refusal"):
                self.refuse("command residue", w["print"], w["site"]["pc"], w["refusal"]["why"])
                continue
            if w["kind"] == "file":
                continue
            i, s = _stmt(self.view, w["site"])
            raw = s.e if type(s) is W16 else s.v
            for gs, v in self.P.resolve(w["site"]["proc"], w["site"]["block"], i, raw):
                self._site(w, gs, v)

    def _site(self, w, gs, v):
        reg = w["register"]
        kind = _value_kind(v, self.rgn, self.prid, self.selectors)
        if kind is None:
            self.refuse("command residue", w["print"], w["site"]["pc"], "value is no schema form")
            return
        voices = w["voices"] or [None]
        for voice in voices:
            env = {n: (voice or 0) * st for n, st in self.stride.items()}
            ran = self.ev.truth(gs, env)
            fetched = fetch_ticks(self.t2, voice or 0)
            at = set(np.nonzero(ran)[0].tolist())
            if at == fetched:
                when = "row"
            elif ran.all():
                when = "tick"
            else:
                self.refuse(
                    "command residue",
                    w["print"],
                    w["site"]["pc"],
                    "written on %d ticks, neither every tick nor the %d row fetches"
                    % (len(at), len(fetched)),
                )
                return
            self._apply(w, reg, voice, kind, when, env, at)

    def epoch(self, site):
        """The cells a site's value read at last tick's value: those the tick writes after it."""
        p = self.view.procs[site["proc"]]
        i, _s = _stmt(self.view, site)
        after = set(afterwrites(p).get(site["block"], ()))
        for s in p.blocks[site["block"]].stmts[i + 1 :]:
            after |= {
                r
                for r in (
                    (s.lo[0], s.hi[0]) if type(s) is W16 else (s.r,) if type(s) is Store else ()
                )
                if r >= 0
            }
        return self.ev.lagged(after)

    def value_at(self, e, env, site):
        """``e`` over the horizon, read at the site's own epoch."""
        was, self.cells.subst = self.cells.subst, {**self.cells.subst, **self.epoch(site)}
        try:
            return self.ev.value(e, env)
        finally:
            self.cells.subst = was

    def _apply(self, w, reg, voice, kind, when, env, at):
        v = voice or 0
        if kind[0] == "pitch":
            idx = self.value_at(kind[1], env, w["site"])
            if idx is None:
                self.refuse("command residue", w["print"], w["site"]["pc"], "pitch index unread")
                return
            p = self.t2["pitch"]
            r = self.rgn[kind[2]]
            shift = 1 if p["layout"] == "u16le" else 0
            base = min(self.rgn[x].base for x in p["regions"])
            for t in sorted(at):
                note = (
                    int((idx[t] + r.base - base) >> shift) if shift else int(idx[t] + r.base - base)
                )
                if p["layout"] in ("lo|hi", "hi|lo") and kind[2] != p["regions"][0]:
                    note = int(idx[t])
                self.events.setdefault((v, t), {})["note"] = note
        elif kind[0] == "const" and when == "row":
            for t in sorted(at):
                self.events.setdefault((v, t), {}).setdefault("cmds", []).append(
                    ["set", reg, kind[1]]
                )
        elif kind[0] == "const":
            self.events.setdefault((v, -1), {}).setdefault("cmds", []).append(["set", reg, kind[1]])
        else:
            self.refuse(
                "command residue", w["print"], w["site"]["pc"], "%s at %s" % (kind[0], when)
            )

    def build(self):
        self.classify_sites()
        voices = []
        for v in self.t2["score"]:
            chans = v.get("pattern") or []
            if not chans:
                continue
            ch = chans[0]
            patterns, order, rows = {}, [], []
            for e in ch["events"]:
                if not e["bytes"] and e["ticks"] == 0:
                    continue
                got = self.events.get((v["copy"], e["tick"]), {})
                rows.append(
                    {
                        "dur": e["ticks"],
                        "note": got.get("note"),
                        "ins": got.get("ins"),
                        "cmds": got.get("cmds", []),
                        "bytes": e["bytes"],
                        "pos": e["pos"],
                        "base": e["base"],
                    }
                )
            # one pattern per visit of a base, deduplicated by content
            for visit in _visits(rows):
                key = tuple(
                    (r["dur"], r["note"], r["ins"], tuple(map(tuple, r["cmds"]))) for r in visit
                )
                pid = (
                    "p%04X_%d" % (visit[0]["base"], len(patterns))
                    if key not in patterns
                    else patterns[key]
                )
                if key not in patterns:
                    patterns[key] = pid
                order.append({"pattern": pid, "transpose": 0})
            pats = {
                pid: [dict(r, base=None, pos=None) for r in visit]
                for visit, pid in zip(_visits(rows), [o["pattern"] for o in order])
            }
            voices.append({"order": order, "patterns": pats, "accs": []})
        sub = ((self.cert or {}).get("subtunes") or [{}])[0]
        if sub.get("complete") and (sub.get("period") or 0) > 1:
            end = {"kind": "loop", "row": 0}
        elif sub.get("complete"):
            end = {"kind": "fixed_point"}
        else:
            end = {"kind": "horizon"}
        meta = self.view.meta
        return {
            "meta": {
                "cadence": {
                    "cycles_per_tick": meta["entry"]["cycles_per_tick"],
                    "source": meta["entry"]["source"],
                },
                "source": {"tune": meta.get("name"), "song": meta.get("song"), "family": None},
                "sid_model": meta.get("sid_model"),
                "player": "universal/1",
                "commit_order": list(commit_order(self.obs)),
            },
            "pitch": (self.t2.get("pitch") or {}).get("entries"),
            "streams": {s["cursor"]: s for s in self.t2.get("streams", ())},
            "instruments": {s["cursor"]: s for s in self.t2.get("selectors", ())},
            "accs": {a["id"]: a for a in (self.t1 or {}).get("accs", ())},
            "score": {"voices": voices, "end": end},
            "globals": {},
        }


def _visits(rows):
    out = []
    for r in rows:
        if out and out[-1][-1]["base"] == r["base"]:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def commit_order(obs):
    """The per-voice ad/sr/ctrl order the source writes: the first tick writing all three."""
    if not obs:
        return player.DEFAULT_ORDER
    for o in obs:
        for v in range(3):
            got = [VOICE_REG[r % 7] for r, _val in o.edges if r // 7 == v]
            names = [n for n in dict.fromkeys(got) if n in ("ad", "sr", "ctrl")]
            if len(names) == 3:
                return tuple(names)
    return player.DEFAULT_ORDER


def document(view, names, t0, t1, t2, hist, cert, obs=None):
    """``(trackerprog, refusals)``."""
    L = Lift(view, names, t0, t1, t2, hist, cert, obs)
    tp = L.build()
    return tp, list(dict.fromkeys(L.refusals))


# ---- the print and its measure -----------------------------------------------------
def render(tp):
    """``trackerprog.md``: meta, pitch, instruments, streams, then the score."""
    out = ["# trackerprog: %s" % tp["meta"]["source"]["tune"], "", "## meta", "", "```"]
    m = tp["meta"]
    out += [
        "cadence   every %d cycles (%s)"
        % (m["cadence"]["cycles_per_tick"], m["cadence"]["source"]),
        "player    %s, commit order %s" % (m["player"], "/".join(m["commit_order"])),
        "end       %s" % tp["score"]["end"]["kind"],
        "```",
        "",
    ]
    out += ["## pitch", "", "```", " ".join("%04X" % x for x in tp["pitch"] or ()), "```", ""]
    for title, key in (("instruments", "instruments"), ("streams", "streams")):
        out += ["## %s" % title, "", "```"]
        for name, s in tp[key].items():
            out.append(
                "%s  %d x %d %s"
                % (
                    name,
                    len(s["columns"]),
                    s["entries"],
                    " ".join(c["table"] for c in s["columns"]),
                )
            )
        out += ["```", ""]
    out += ["## score", ""]
    for v, voice in enumerate(tp["score"]["voices"]):
        out += ["```", "voice %d: order %s" % (v, " ".join(o["pattern"] for o in voice["order"]))]
        for pid, rows in voice["patterns"].items():
            out.append("%s:" % pid)
            for r in rows:
                cmds = " ".join("%s=%s" % (c[1], c[2]) for c in r["cmds"])
                out.append(
                    "  %-4s %-4s %3d %s"
                    % (
                        "---" if r["note"] is None else r["note"],
                        "" if r["ins"] is None else r["ins"],
                        r["dur"],
                        cmds,
                    )
                )
        out += ["```", ""]
    return "\n".join(out)


TOKEN = re.compile(r"\$?\w+|\S")


def measure(md, section):
    """Architecture 6.2's six numbers over one print: tokens and lines over ``section``."""
    body = md.split("## %s" % section, 1)[1] if "## %s" % section in md else ""
    lines = [l for l in body.splitlines() if l.strip() and not l.startswith("```")]
    head = md.split("## %s" % section, 1)[0]
    hdr = [
        l
        for l in head.splitlines()
        if l.strip() and not l.startswith("```") and not l.startswith("#")
    ]
    return {
        "tokens": sum(len(TOKEN.findall(l)) for l in lines),
        "lines": len(lines),
        "header_rows": len(hdr),
        "xz": len(lzma.compress(md.encode(), preset=9 | lzma.PRESET_EXTREME)),
    }


def numbers(tp, md):
    """The six numbers of a trackerprog print plus its ``xz -9e`` size."""
    got = measure(md, "score")
    voices = tp["score"]["voices"]
    got["statements"] = sum(len(r) for v in voices for r in v["patterns"].values())
    got["blocks"] = (
        sum(len(v["patterns"]) for v in voices) + len(tp["streams"]) + len(tp["instruments"])
    )
    got["data_rows"] = sum(
        s["entries"] for s in list(tp["streams"].values()) + list(tp["instruments"].values())
    ) + len(tp["pitch"] or ())
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
