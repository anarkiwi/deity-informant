"""T3 -- the scoreprog lifted from the program's data, and its print.

A *scoreprog* is not a trackerprog: it carries the certified tick as a
``program`` key and renders on :mod:`.interp`, where a trackerprog carries no
code and renders on :mod:`.universal`.  The two share the seven key names below
and one field, ``meta.commit_order``; B6/B7 of docs/trackerprog-backlog.md are
what converge them.

The score is the certified tick's fetch regions run over the program's own
tables (:mod:`.region`, :mod:`.interp`): one row per fetch, its bytes and the
cells it set. The sounds are the tables and recurrences the rest of the tick
reads -- the instrument records the envelope writes index (T2's selector, or the
pointer table a record base goes through), the streams T2 walked, T1's
accumulators -- and the tick outside the regions is the producer list: every
SID write site of T0 with the guards it stands under. Nothing here reads the
observable; :mod:`.certify` compares the replay with it.
"""

from __future__ import annotations

import lzma
import re

from ..tuneprog.accguard import guardpath
from ..tuneprog.accshape import Ctx
from ..tuneprog.facts import SID_VOICES
from ..tuneprog.ir import Bin, Const, Load, R16, Tuneprog, Var, dec, enc
from ..tuneprog.irwalk import addr_split, walk
from ..tuneprog.tracedata import input_kind
from . import cursors, interp, region
from .refuse import Refusal
from .resolve import Program

TABLE = ("const", "init_constant")
ENVELOPE = ("ad", "sr")


# ---- naming by address --------------------------------------------------------
class Namer:
    """A cell's printed name from its address, off the presentation view."""

    def __init__(self, view, names):
        self.names = names
        self.rgn = sorted((r for r in view.storage if r.id >= 0), key=lambda r: r.base)
        self.split = {
            d["split"]: (g, d) for g, d in names.groups.items() if d.get("split") is not None
        }

    def region(self, addr):
        """The region holding ``addr``: a state cell before a table that overlaps it."""
        hits = [r for r in self.rgn if r.base <= addr < r.base + r.size]
        return min(hits, key=lambda r: (r.kind != "state", r.size), default=None)

    def role(self, addr):
        r = self.region(addr)
        return None if r is None else self.names.role.get(r.id)

    def cell(self, addr):
        r = self.region(addr)
        if r is None:
            return "$%04X" % addr
        off = addr - r.base
        if r.id in self.split:
            g, d = self.split[r.id]
            stride, n = max(int(d["stride"]), 1), int(d["n"])
            fields = {int(k): f for k, f in d["fields"].items()}
            f = max((k for k in fields if k <= off and (off - k) // stride < n), default=None)
            if f is not None:
                return "%s[%d].%s" % (g, (off - f) // stride, fields[f])
        hit = self.names.view.get(r.id)
        if hit is not None:
            g, field = hit
            grp = self.names.groups.get(g) or {}
            k = off // max(int(grp.get("stride", 1)), 1)
            return "%s[%d].%s" % (g, k, field) if int(grp.get("n", 1)) > 1 else "%s.%s" % (g, field)
        name = self.names.of(r.id)
        return name if r.size == 1 or off == 0 else "%s[%d]" % (name, off)

    def expr(self, e):
        """A view expression, compactly."""
        t = type(e)
        if t is Const:
            return "$%X" % e.v if e.v > 9 else str(e.v)
        if t is Var:
            return e.n
        if t is Bin:
            return "(%s %s %s)" % (self.expr(e.a), e.op, self.expr(e.b))
        if t is R16:
            return self.names.of(e.lo[0])
        if t is Load:
            base, idx = addr_split(e.a)
            if base is not None and idx is None:
                return self.cell(base)
            r = self.rgn and next((r for r in self.rgn if r.id == e.r), None)
            name = self.names.of(e.r) if r is not None else "mem"
            return "%s[%s]" % (name, self.expr(e.a))
        return repr(e)


def by_name(view, names):
    """``{name: region}`` over the view's regions."""
    return {names.of(r.id): r for r in view.storage if r.id >= 0}


# ---- the score tables and the pinned inputs ----------------------------------
def tables_of(t2, view, names):
    """The address envelopes of the order and pattern tables T2's score reads."""
    regs = by_name(view, names)
    out = set()
    for v in t2["score"]:
        for role in ("order", "pattern"):
            for ch in v.get(role, ()):
                r = regs.get(ch["table"])
                if r is not None:
                    out.add((r.base, r.base + r.size - 1))
    return out


def pinned(inputs):
    """``(values by address, refusals)``: the tick's pinned reads as data (section 8)."""
    seen, out, bad = {}, {}, []
    for _c, _site, _op, addr, val in inputs:
        if addr >= 0x10000:
            continue
        seen.setdefault(addr, set()).add(int(val))
    for addr, vals in sorted(seen.items()):
        kind = input_kind(addr)
        if kind in ("raster", "cia", "sid_readback", "io") and len(vals) > 1:
            bad.append(Refusal("external input", "$%04X" % addr, "", kind))
            continue
        out[addr] = min(vals)
    return out, bad


# ---- instruments ---------------------------------------------------------------
def _sites(t0, registers):
    """``{(proc, block)}`` of the T0 write sites of ``registers``."""
    return {
        (w["site"]["proc"], w["site"]["block"])
        for w in t0.get("writes") or ()
        if w.get("register") in registers
    }


def instruments_of(view, names, t2, t0):
    """The instrument table the envelope writes index, its rows read off the image.

    The table is the selector T2 found under the ``ad``/``sr`` sites' reads, or
    the pointer table a record base goes through; a row is one entry's fields.
    """
    rgn = view.by_id()
    ctx = Ctx(view, names)
    P = Program(ctx)
    accs = cursors.accesses(ctx, names, P)
    sites = _sites(t0, ENVELOPE)
    cells = {
        c["region"]
        for w in t0.get("writes") or ()
        if w.get("register") in ENVELOPE
        for c in w.get("cells") or ()
    }
    img = view.reads()
    hits = [
        a for a in accs if a.site[:2] in sites and a.table in cells and rgn[a.table].kind in TABLE
    ]
    if not hits:
        return None
    a = hits[0]
    if a.cursor is not None:
        key = "%s@$%04X" % (names.of(a.cursor.region), a.cursor.addr)
        sel = next((s for s in t2["selectors"] + t2["streams"] if s["cursor"] == key), None)
        if sel is None:
            return None
        regs = by_name(view, names)
        cols = [(c["table"], regs[c["table"]].base + c["origin"]) for c in sel["columns"]]
        shift, stride = sel.get("shift") or 0, max(sel["columns"][0]["stride"], 1)
        first = min(sel["visited"], default=0)
        rows = {
            int(v): {name: int(img[(base + (v << shift)) & 0xFFFF]) for name, base in cols}
            for v in (first + i * stride for i in range(sel["entries"]))
        }
        return {
            "kind": "selector",
            "cursor": key,
            "entries": len(rows),
            "used": len(sel["visited"]),
            "rows": rows,
        }
    base = a.base
    ptr = next(
        (
            x
            for x in cursors.leaf_loads(base)
            if cursors.istable(x, rgn) and addr_split(x.a)[1] is not None
        ),
        None,
    )
    if ptr is None:
        return None
    lo = rgn[ptr.lo[0]] if type(ptr) is R16 else rgn[ptr.r]
    hi = rgn[ptr.hi[0]] if type(ptr) is R16 else None
    n = lo.size // max(lo.stride, 1)
    origins = sorted({x.origin for x in hits})
    rows = {}
    for i in range(n):
        p = int(img[lo.base + i * max(lo.stride, 1)])
        if hi is not None:
            p |= int(img[hi.base + i * max(hi.stride, 1)]) << 8
        rows[i] = {"+%X" % o: int(img[(p + o) & 0xFFFF]) for o in origins}
    return {"kind": "pointers", "cursor": names.of(lo.id), "entries": n, "used": n, "rows": rows}


# ---- streams and accumulators ---------------------------------------------------
def streams_of(view, names, t2, tables):
    """T2's streams with their column bytes, the score's own tables left out."""
    regs = by_name(view, names)
    img = view.reads()
    out = []
    for s in t2["streams"] + t2["selectors"]:
        cols = []
        for c in s["columns"]:
            r = regs.get(c["table"])
            if r is None or any(lo <= r.base <= hi for lo, hi in tables):
                continue
            base, stride = r.base + c["origin"], max(c["stride"], 1)
            n = min(s["entries"], (r.base + r.size - base + stride - 1) // stride)
            cols.append(
                {
                    "table": c["table"],
                    "origin": c["origin"],
                    "stride": stride,
                    "bytes": [int(img[(base + i * stride) & 0xFFFF]) for i in range(max(n, 0))],
                }
            )
        if cols:
            out.append(
                {
                    "cursor": s["cursor"],
                    "kind": s["kind"],
                    "step": s["step"],
                    "entries": s["entries"],
                    "visited": s["visited"],
                    "terminator": s["terminator"],
                    "columns": cols,
                }
            )
    return out


# ---- the producers ---------------------------------------------------------------
def producers_of(view, names, t0, t1, fetch):
    """Every T0 write site outside the fetch regions, with the guards it stands under
    and the accumulators whose value cell it reads."""
    namer = Namer(view, names)
    guards = {}
    by_region = {}
    for a in (t1 or {}).get("accs") or ():
        for rid in a.get("regions") or [a["cell"]["region"]]:
            by_region.setdefault(rid, []).append(a["id"])
    out = []
    for w in t0.get("writes") or ():
        site = w["site"]
        pc = int(site["pc"][1:], 16)
        if pc in fetch.pcs:
            continue
        proc = site["proc"]
        if proc not in guards and proc in view.procs:
            guards[proc] = guardpath(view.procs[proc])
        gs = (guards.get(proc) or {}).get(site["block"], ())
        out.append(
            {
                "register": w.get("register"),
                "voices": w.get("voices"),
                "kind": w.get("kind"),
                "print": w.get("print"),
                "site": {"proc": proc, "block": site["block"], "pc": site["pc"]},
                "when": ["%s%s" % ("" if t else "not ", namer.expr(c)) for c, t, _w in gs],
                "cells": [c["name"] for c in w.get("cells") or ()],
                "accs": sorted(
                    {x for c in w.get("cells") or () for x in by_region.get(c["region"], ())}
                ),
                "refusal": w.get("refusal"),
            }
        )
    return out


# ---- the score -------------------------------------------------------------------
def _copyvar(fetches):
    """``{region key: (var, sorted values)}``: the index a region's fetch runs under."""
    out = {}
    for key, got in fetches.items():
        vals = {}
        for f in got:
            for n, v in f.get("env", {}).items():
                vals.setdefault(n, set()).add(v)
        hit = next((n for n, vs in vals.items() if len(vs) == SID_VOICES), None)
        out[key] = (hit, sorted(vals[hit])) if hit else (None, [])
    return out


def cursor_cells(t2, view, names):
    """Every copy of every score channel's cursor cell: the bookkeeping a row sets."""
    regs = by_name(view, names)
    out = set()
    for v in t2["score"]:
        for role in ("order", "pattern"):
            for ch in v.get(role, ()):
                for part in ch["cursor"].split(":"):
                    name, _at, addr = part.partition("@$")
                    r = regs.get(name)
                    if r is None:
                        continue
                    g = next(
                        (
                            d
                            for d in names.groups.values()
                            if r.id in d.get("members", ()) or d.get("split") == r.id
                        ),
                        {},
                    )
                    stride, n = max(int(g.get("stride", 1)), 1), int(g.get("n", 1))
                    base = int(addr, 16) if addr else r.base
                    out |= {base + k * stride for k in range(n)}
    return out


def score_of(fetches, prog, namer, ticks, fetch, bookkeeping=frozenset()):
    """The fetches as per-voice rows: bytes read, cells and registers set, temps left.

    A row is every fetch one voice made in one tick. ``cmds`` names every store;
    ``sets`` is the part the print shows -- the score's own cursors and pointers
    (``bookkeeping``, the regions' own cells) left out.
    """
    copies = _copyvar(fetches)
    img = prog.reads()
    tables = fetch.tables
    own = [c for r in fetch.regions.values() for c in r.cells]
    voices = {}
    for key, got in fetches.items():
        var, vals = copies[key]
        for f in got:
            v = vals.index(f["env"][var]) if var else None
            cmds = [
                [namer.cell(a) if cls != "io" else "sid[$%04X]" % a, v_, w]
                for cls, a, v_, w, _src in f["cmds"]
            ]
            keep = [
                cls == "io"
                or not (
                    a in bookkeeping
                    or any(lo <= a <= hi for lo, hi in own)
                    or namer.role(a) == "ptr"
                )
                for cls, a, _v, _w, _src in f["cmds"]
            ]
            row = voices.setdefault(v, {}).setdefault(
                f["tick"],
                {
                    "tick": f["tick"],
                    "regions": [],
                    "bytes": [],
                    "cmds": [],
                    "sets": [],
                    "temps": {},
                },
            )
            row["regions"].append("%s:%s" % key)
            row["bytes"] += [
                [a, int(img[a])] for a in f["reads"] if any(lo <= a <= hi for lo, hi in tables)
            ]
            row["cmds"] += cmds
            row["sets"] += [c for c, k in zip(cmds, keep) if k]
            row["temps"].update(f["temps"])
    out = []
    for v in sorted(voices, key=lambda x: (x is None, x)):
        rows = [voices[v][t] for t in sorted(voices[v])]
        for r, nxt in zip(rows, rows[1:] + [None]):
            r["dur"] = (nxt["tick"] if nxt else ticks) - r["tick"]
        out.append({"copy": v, "rows": rows, **patterns(rows)})
    return out


def patterns(rows):
    """Rows grouped into patterns at the fetches that read the order: ``(order, patterns)``.

    A visit ends where the bytes read stop continuing the last row's; a pattern is
    keyed on its rows' bytes, holds and sets, so a second visit that decodes the
    same way is the same pattern, and the order lists the visits.
    """
    visits, cur, lo, hi = [], [], None, None
    for r in rows:
        addrs = [a for a, _b in r["bytes"]]
        if addrs and lo is not None and not lo <= min(addrs) <= hi + 1:
            visits.append(cur)
            cur = []
        if addrs:
            lo, hi = min(addrs), max(addrs)
        cur.append(r)
    if cur:
        visits.append(cur)
    pats, order = {}, []
    for visit in visits:
        key = tuple(
            (r["dur"], tuple(b for _a, b in r["bytes"]), tuple(tuple(c) for c in r["sets"]))
            for r in visit
        )
        if key not in pats:
            pats[key] = "p%d" % len(pats)
        order.append({"pattern": pats[key], "tick": visit[0]["tick"], "rows": len(visit)})
    return {
        "order": order,
        "patterns": {
            pid: [
                {"dur": d, "bytes": list(b), "sets": [list(c) for c in sets]} for d, b, sets in key
            ]
            for key, pid in pats.items()
        },
    }


# ---- the lift ----------------------------------------------------------------------
def horizon(cert, t2):
    sub = ((cert or {}).get("subtunes") or [{}])[0]
    ticks = int(t2["horizon"]["ticks"])
    if sub.get("complete"):
        kind = "loop" if (sub.get("period") or 0) > 1 else "fixed_point"
    else:
        kind = "horizon"
    return ticks, {"kind": kind, "ticks": ticks, "period": sub.get("period")}


def _envvars(fetch, prog):
    """The index names a region's addresses read: bound at entry, they say the copy."""
    out = {}
    for key, r in fetch.regions.items():
        names = set()
        for l in r.blocks:
            for s in prog.procs[r.proc].blocks[l].stmts:
                for e in (getattr(s, "a", None),):
                    if e is not None:
                        names |= {x.n for x in walk(e) if type(x) is Var}
        out[key] = sorted(names)
    return out


def lift(prog, view, names, t0, t1, t2, cert, inputs=()):
    """``(scoreprog, refusals, recorded observable)``: the lift, from the data alone."""
    refusals = [Refusal(**r) if isinstance(r, dict) else r for r in t2.get("refusals") or ()]
    refusals = [r for r in refusals if isinstance(r, Refusal)]
    for entry in (prog.meta.get("schedule") or [])[1:]:
        refusals.append(
            Refusal(
                "sample stream",
                "mode_vol",
                "$%04X" % entry.get("addr", 0),
                "second entry %s" % entry.get("kind"),
            )
        )
    pins, bad = pinned(inputs)
    refusals += bad
    tables = tables_of(t2, view, names)
    fetch, bad = region.fetch(prog, tables)
    refusals += bad
    ticks, end = horizon(cert, t2)
    envvars = _envvars(fetch, prog)
    P = interp.Player(prog, fetch, pins, envvars=envvars).run_init()
    obs, trap = P.render(ticks)
    if trap is not None:
        why = "external input" if trap["trap"] == "external input" else "score not cursor-shaped"
        refusals.append(Refusal(why, trap["detail"], "", trap["trap"]))
    namer = Namer(view, names)
    tp = {
        "meta": {
            "cadence": {
                "cycles_per_tick": prog.meta["entry"]["cycles_per_tick"],
                "source": prog.meta["entry"]["source"],
            },
            "source": {"tune": prog.meta.get("name"), "song": prog.meta.get("song")},
            "sid_model": prog.meta.get("sid_model"),
            "player": "trackerprog/interp.py",
            "commit_order": list(commit_order(t0)),
            "horizon": ticks,
        },
        "pitch": list((t2.get("pitch") or {}).get("entries") or []),
        "instruments": instruments_of(view, names, t2, t0),
        "streams": streams_of(view, names, t2, tables),
        "accs": {a["id"]: a for a in (t1 or {}).get("accs") or ()},
        "producers": producers_of(view, names, t0, t1, fetch),
        "score": {
            "voices": score_of(P.fetches, prog, namer, ticks, fetch, cursor_cells(t2, view, names)),
            "end": end,
            "regions": [
                {
                    "proc": r.proc,
                    "entry": r.entry,
                    "exit": r.exit,
                    "exits": sorted(r.exits),
                    "blocks": sorted(r.blocks),
                    "liveout": list(r.liveout),
                    "cells": sorted(r.cells),
                }
                for r in fetch.regions.values()
            ],
            "tables": sorted(tables),
            "fetches": {"%s:%s" % k: v for k, v in P.fetches.items()},
        },
        "globals": {},
        "program": prog,
        "inputs": pins,
    }
    return tp, list({(r.why, r.cell, r.site): r for r in refusals}.values()), obs


def commit_order(t0):
    """The per-voice ad/sr/ctrl order the program's own write sites keep, by pc."""
    pcs = {}
    for w in t0.get("writes") or ():
        if w.get("register") in ("ad", "sr", "ctrl"):
            pcs.setdefault(w["register"], []).append(int(w["site"]["pc"][1:], 16))
    got = sorted(pcs, key=lambda r: min(pcs[r]))
    return tuple(got) if len(got) == 3 else interp.DEFAULT_ORDER


def fetch_of(tp):
    """The :class:`~.region.Fetch` a scoreprog carries."""
    F = region.Fetch(tables=tuple(tuple(t) for t in tp["score"]["tables"]))
    for r in tp["score"]["regions"]:
        F.regions[(r["proc"], r["entry"])] = region.Region(
            r["proc"],
            frozenset(r["blocks"]),
            r["entry"],
            r["exit"],
            frozenset(r["exits"]),
            frozenset(tuple(c) for c in r["cells"]),
            tuple(r["liveout"]),
        )
    return F


def replay(tp, ticks=None):
    """``(observable, trap)``: the scoreprog rendered on :mod:`.interp` from its data."""
    prog = (
        tp["program"] if isinstance(tp["program"], Tuneprog) else Tuneprog.from_json(tp["program"])
    )
    fetches = {}
    for k, v in tp["score"]["fetches"].items():
        proc, entry = k.split(":", 1)
        fetches[(proc, entry)] = v
    P = interp.Player(prog, fetch_of(tp), tp["inputs"], fetches=fetches).run_init()
    return P.render(ticks or tp["meta"]["horizon"])


# ---- the print and its measure -----------------------------------------------------
def render(tp):
    """``scoreprog.md``: meta, pitch, instruments, streams, accumulators, producers, score."""
    m = tp["meta"]
    ins = tp["instruments"]
    out = [
        "# scoreprog: %s" % m["source"]["tune"],
        "",
        "## meta",
        "",
        "```",
        "cadence   every %d cycles (%s)"
        % (m["cadence"]["cycles_per_tick"], m["cadence"]["source"]),
        "player    %s, commit order %s" % (m["player"], "/".join(m["commit_order"])),
        "end       %s over %d ticks" % (tp["score"]["end"]["kind"], m["horizon"]),
        "instruments %d, streams %d, accs %d, producers %d, regions %d"
        % (
            ins["entries"] if ins else 0,
            len(tp["streams"]),
            len(tp["accs"]),
            len(tp["producers"]),
            len(tp["score"]["regions"]),
        ),
        "```",
        "",
        "## pitch",
        "",
        "```",
        " ".join("%04X" % x for x in tp["pitch"] or ()),
        "```",
        "",
        "## instruments",
        "",
        "```",
    ]
    if ins:
        fields = list(next(iter(ins["rows"].values()))) if ins["rows"] else []
        out.append("%s via %s: %s" % (ins["kind"], ins["cursor"], " ".join(fields)))
        for i, row in sorted(ins["rows"].items()):
            out.append("  [%3d] %s" % (i, " ".join("%02X" % row[f] for f in fields)))
    out += ["```", "", "## streams", "", "```"]
    for s in tp["streams"]:
        out.append(
            "%s %s step %s, %d entries, terminator %s"
            % (s["kind"], s["cursor"], s["step"], s["entries"], s["terminator"])
        )
        for c in s["columns"]:
            out.append("  %-8s %s" % (c["table"], " ".join("%02X" % b for b in c["bytes"])))
    out += ["```", "", "## accs", "", "```"]
    for a in tp["accs"].values():
        out.append(
            "%s %s <- %s: %s %s, delta %s, policy %s, rate %s, phase %s, scope %s"
            % (
                a["id"],
                a["target"].get("register"),
                a["cell"]["name"],
                a["target"].get("kind"),
                a["width"],
                (a.get("delta") or {}).get("kind"),
                a.get("policy"),
                (a.get("rate") or {}).get("kind"),
                (a.get("phase") or {}).get("kind"),
                a.get("scope"),
            )
        )
    out += ["```", "", "## producers", "", "```"]
    for p in tp["producers"]:
        tags = "".join(" [%s]" % a for a in p["accs"])
        when = (" if " + " and ".join(p["when"])) if p["when"] else ""
        out.append("%s%s%s" % (p["print"], tags, when))
    out += ["```", "", "## score", ""]
    for v in tp["score"]["voices"]:
        name = "global" if v["copy"] is None else "voice %d" % v["copy"]
        out += ["```", "%s: order %s" % (name, " ".join(o["pattern"] for o in v["order"]))]
        for pid, rows in v["patterns"].items():
            out.append("%s:" % pid)
            for r in rows:
                out.append(
                    "  %3d %-12s %s"
                    % (
                        r["dur"],
                        " ".join("%02X" % b for b in r["bytes"]),
                        " ".join("%s=%d" % (c[0], c[1]) for c in r["sets"]),
                    )
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
    """The six numbers of a scoreprog print plus its ``xz -9e`` size.

    Statements are the score's rows and the producers; blocks the patterns, streams,
    regions and the instrument table; data rows the pitch, instrument and stream entries.
    """
    got = measure(md, "score")
    voices = tp["score"]["voices"]
    ins = tp["instruments"]
    got["statements"] = sum(len(r) for v in voices for r in v["patterns"].values()) + len(
        tp["producers"]
    )
    got["blocks"] = (
        sum(len(v["patterns"]) for v in voices)
        + len(tp["streams"])
        + len(tp["score"]["regions"])
        + (1 if ins else 0)
    )
    got["data_rows"] = (
        len(tp["pitch"] or ())
        + (ins["entries"] if ins else 0)
        + sum(len(c["bytes"]) for s in tp["streams"] for c in s["columns"])
    )
    return got


def numbers_tuneprog(md, view):
    """The same six over a ``tuneprog.md`` and its view (architecture 6.2)."""
    got = measure(md, "program")
    got["statements"] = sum(len(b.stmts) for p in view.procs.values() for b in p.blocks.values())
    got["blocks"] = sum(len(p.blocks) for p in view.procs.values())
    data = md.split("## data", 1)[1].split("## inputs", 1)[0] if "## data" in md else ""
    got["data_rows"] = len([l for l in data.splitlines() if l.strip() and not l.startswith("```")])
    return got


# a scoreprog's ten keys.  Seven of the names are a trackerprog's, at disjoint
# shapes; there is no ``state0``, and ``producers``/``program``/``inputs`` are
# the lift's alone.  Only ``meta.horizon``, ``score``, ``program`` and ``inputs``
# are read by :func:`replay`; the rest are readings the print carries
KEYS = (
    "meta",
    "pitch",
    "streams",
    "accs",
    "instruments",
    "producers",
    "score",
    "globals",
    "program",
    "inputs",
)


def to_json(tp):
    """S4-style tagged: ``["$scoreprog", *KEYS]`` with every IR node and dict encoded."""
    return ["$scoreprog"] + [enc(tp[k]) for k in KEYS]


def from_json(doc):
    assert doc[0] == "$scoreprog"
    out = {k: dec(v) for k, v in zip(KEYS, doc[1:])}
    out["inputs"] = {int(k): v for k, v in out["inputs"].items()}
    if out["instruments"]:
        out["instruments"]["rows"] = {int(k): v for k, v in out["instruments"]["rows"].items()}
    return out
