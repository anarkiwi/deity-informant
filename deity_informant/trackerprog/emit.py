"""T3 -- the trackerprog lifted from the program's data, and its print.

The score is the certified tick's fetch regions run over the program's own
tables (:mod:`.region`, :mod:`.player`): one row per fetch, its bytes and the
cells it set. The sounds are the tables and recurrences the rest of the tick
reads -- the instrument records the envelope writes index (T2's selector, or the
pointer table a record base goes through), the streams T2 walked, T1's
accumulators -- and the tick outside the regions is the producer list: the
certified tick lowered (:mod:`.sound`) and flattened to guarded producers over a
temp table (:mod:`.tick`), each SID site annotated with T0's record over named
cells (:mod:`.producers`). Nothing here reads the observable; :mod:`.certify`
compares the replay with it.
"""

from __future__ import annotations

import json
import lzma
import re

from ..tuneprog.accshape import Ctx
from ..tuneprog.ir import REGVAR, R16
from ..tuneprog.irwalk import addr_split
from ..tuneprog.tracedata import input_kind
from . import cursors, player, producers, region, rows, sound, tick, universal
from . import fetch as fetchmod
from .document import KEYS, digest, from_json, to_json
from .namer import Namer, by_name
from .refuse import Refusal
from .resolve import Program

TABLE = ("const", "init_constant")
ENVELOPE = ("ad", "sr")


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
        entries = {
            int(v): {name: int(img[(base + (v << shift)) & 0xFFFF]) for name, base in cols}
            for v in (first + i * stride for i in range(sel["entries"]))
        }
        return {
            "kind": "selector",
            "cursor": key,
            "entries": len(entries),
            "used": len(sel["visited"]),
            "rows": entries,
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
    entries = {}
    for i in range(n):
        p = int(img[lo.base + i * max(lo.stride, 1)])
        if hi is not None:
            p |= int(img[hi.base + i * max(hi.stride, 1)]) << 8
        entries[i] = {"+%X" % o: int(img[(p + o) & 0xFFFF]) for o in origins}
    return {"kind": "pointers", "cursor": names.of(lo.id), "entries": n, "used": n, "rows": entries}


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


# ---- the lift ----------------------------------------------------------------------
def horizon(cert, t2):
    sub = ((cert or {}).get("subtunes") or [{}])[0]
    ticks = int(t2["horizon"]["ticks"])
    if sub.get("complete"):
        kind = "loop" if (sub.get("period") or 0) > 1 else "fixed_point"
    else:
        kind = "horizon"
    return ticks, {"kind": kind, "ticks": ticks, "period": sub.get("period")}


def lift(prog, view, names, t0, t1, t2, cert, inputs=()):
    """``(trackerprog, refusals, recorded observable)``: the lift, from the data."""
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
    chans = fetchmod.channels_of(t2, view, names)
    FS = fetchmod.Fetches(prog, names, fetch, chans, Namer(view, names))
    refusals += [r for D in FS.out.values() for r in D["refusals"] + D["unset"]]
    entries = {
        key: {
            t: (D["chans"][t]["cursor"], D["chans"][t]["addr"], D["chans"][t]["base"])
            for t in D["order"]
        }
        for key, D in FS.out.items()
    }
    tabs = [(c["lo"], c["hi"], t) for t, c in chans.items()]
    P = player.Player(prog, fetch, pins, chans=entries, tables=tabs).run_init()
    image, regs = bytes(P.m), list(P.regs)
    obs, trap = P.render(ticks)
    if trap is not None:
        why = "external input" if trap["trap"] == "external input" else "score not cursor-shaped"
        refusals.append(Refusal(why, trap["detail"], "", trap["trap"]))
    PR = producers.Producers(view, names, fetch, chans)
    prods, bad = PR.producers(t0, t1)
    refusals += bad
    score_fetch, local = fetchmod.document(FS, chans)
    prods, loops, registers, spans, bad = tick_of(prog, fetch, score_fetch, local, prods)
    refusals += bad
    spans |= fetchmod.spans(FS)
    voices, bad = rows.voices(P.fetches, chans, prog.reads(), ticks)
    refusals += bad
    pointers = rows.pointer_table(t2, view, names, prog.reads())
    pattern = next((t for t, c in chans.items() if c["role"] == "pattern"), None)
    for v in voices:
        v["order"], v["patterns"] = rows.patterns(v, pattern, pointers.get(pattern) or [])
    tp = {
        "meta": {
            "cadence": {
                "cycles_per_tick": prog.meta["entry"]["cycles_per_tick"],
                "source": prog.meta["entry"]["source"],
            },
            "source": {"tune": prog.meta.get("name"), "song": prog.meta.get("song")},
            "sid_model": prog.meta.get("sid_model"),
            "player": "universal/3",
            "commit_order": list(commit_order(t0)),
            "horizon": ticks,
        },
        "pitch": list((t2.get("pitch") or {}).get("entries") or []),
        "instruments": instruments_of(view, names, t2, t0),
        "streams": streams_of(view, names, t2, tables),
        "accs": producers.accs_of(t1, PR.pr),
        "producers": prods,
        "loops": loops,
        "registers": dict(registers, values=regs),
        "memory": memory_of(image, spans),
        "score": {
            "voices": [rows.emitted(v) for v in voices],
            "end": end,
            "channels": [
                {"table": t, "cursor": c["cursor"], "role": c["role"], "stride": c["stride"]}
                for t, c in sorted(chans.items(), key=lambda kv: kv[1]["role"] != "order")
            ],
            "fetch": score_fetch,
            "regions": [
                {
                    "proc": r.proc,
                    "entry": r.entry,
                    "exit": r.exit,
                    "exits": sorted(r.exits),
                    "blocks": sorted(r.blocks),
                    "cells": sorted(r.cells),
                }
                for r in fetch.regions.values()
            ],
            "tables": sorted(tables),
        },
        "globals": {},
        "inputs": pins,
    }
    return tp, list({(r.why, r.cell, r.site): r for r in refusals}.values()), obs


def tick_of(prog, fetch, score_fetch, local, named):
    """``(producers, loops, registers, spans, refusals)``: the tick outside the regions as data.

    The lowering's items flatten to a producer list (:mod:`.tick`); a fetch binds
    its region's own temps (``local``) to its call path's; each SID store takes
    T0's record for its site, one per caller path in order.
    """
    unit = sound.Unit(prog, fetch)
    L = sound.Lowering(prog, fetch, unit)
    items, refusals = L.run()
    for it in items:
        if it["kind"] == "fetch":
            rgn = next(f for f in score_fetch if f["region"] == it["region"])
            it["exits"] = rgn["exits"]
            it["tmps"] = {
                new: it["tmps"][old]
                for new, old in local[it["region"]].items()
                if old in it["tmps"]
            }
    proc = prog.procs[prog.meta["tick_proc"]]
    rets = {
        "params": [[i, REGVAR[i]] for i in proc.params],
        "rets": [[i, "$ret%d" % j] for j, i in enumerate(proc.rets)],
    }
    reads = {f["region"]: {n: local[f["region"]][n] for n in f["reads"]} for f in score_fetch}
    prods, loops, registers = tick.flatten(items, L.loops(), rets, reads)
    prods = tick.rename(tick.reduce(prods, loops, registers), loops, registers)
    loops = tick.index(prods, loops)
    by_pc = {}
    for p in named:
        by_pc.setdefault(p["site"]["pc"], []).append(p)
    seen = {}
    for it in prods:
        if it["kind"] == "store" and it["cls"] == "io":
            pc = it["site"]["pc"]
            got = by_pc.get(pc)
            if got:
                k = seen.get(pc, 0)
                seen[pc] = k + 1
                it.update(got[min(k, len(got) - 1)])
    return prods, loops, registers, L.spans, refusals


def memory_of(image, spans):
    """The post-init bytes of every envelope the producers read or write, spans merged."""
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [{"base": lo, "bytes": image[lo : hi + 1].hex()} for lo, hi in out]


def commit_order(t0):
    """The per-voice ad/sr/ctrl order the program's own write sites keep, by pc."""
    pcs = {}
    for w in t0.get("writes") or ():
        if w.get("register") in ("ad", "sr", "ctrl"):
            pcs.setdefault(w["register"], []).append(int(w["site"]["pc"][1:], 16))
    got = sorted(pcs, key=lambda r: min(pcs[r]))
    return tuple(got) if len(got) == 3 else player.DEFAULT_ORDER


def fetch_of(tp):
    """The :class:`~.region.Fetch` a trackerprog carries."""
    F = region.Fetch(tables=tuple(tuple(t) for t in tp["score"]["tables"]))
    for r in tp["score"]["regions"]:
        F.regions[(r["proc"], r["entry"])] = region.Region(
            r["proc"],
            frozenset(r["blocks"]),
            r["entry"],
            r["exit"],
            frozenset(r["exits"]),
            frozenset(tuple(c) for c in r["cells"]),
        )
    return F


def replay(tp, ticks=None):
    """``(observable, trap, digest)``: the universal player over the trackerprog's own document.

    The player reads the document text and nothing else; ``digest`` names that
    text, and :func:`~.certify.certificate` binds the render to it.
    """
    doc = json.loads(json.dumps(to_json(tp)))
    P = universal.DataPlayer(doc)
    assert P.digest == digest(tp)
    obs, trap = P.render(ticks or tp["meta"]["horizon"])
    return obs, trap, P.digest


def oracle(prog, tp, ticks=None):
    """``(observable, trap)``: the certified program, its regions run -- the reference."""
    P = player.Player(prog, fetch_of(tp), tp["inputs"]).run_init()
    return P.render(ticks or tp["meta"]["horizon"])


# ---- the print and its measure -----------------------------------------------------
def render(tp):
    """``trackerprog.md``: meta, pitch, instruments, streams, accs, producers, score."""
    m = tp["meta"]
    ins = tp["instruments"]
    out = [
        "# trackerprog: %s" % m["source"]["tune"],
        "",
        "## meta",
        "",
        "```",
        "cadence   every %d cycles (%s)"
        % (m["cadence"]["cycles_per_tick"], m["cadence"]["source"]),
        "player    %s, commit order %s" % (m["player"], "/".join(m["commit_order"])),
        "end       %s over %d ticks" % (tp["score"]["end"]["kind"], m["horizon"]),
        "instruments %d, streams %d, accs %d, producers %d, temps %d, regions %d"
        % (
            ins["entries"] if ins else 0,
            len(tp["streams"]),
            len(tp["accs"]),
            len(stores(tp)),
            sum(1 for p in tp["producers"] if p["kind"] == "let"),
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
                a["register"],
                a["cell"],
                a["kind"],
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
        if "target" in p:
            tags = "".join(" [%s]" % a for a in p["accs"])
            when = (" if " + " and ".join(p["when"])) if p["when"] else ""
            out.append("%s = %s%s%s" % (p["target"], p["value"], tags, when))
    for f in tp["score"]["fetch"]:
        out += ["", "fetch %s:" % f["region"]]
        out += ["  " + p["print"] for p in f["producers"] if "print" in p]
        out += ["  refused %s: %s" % (r["cell"], r["detail"]) for r in f["refusals"]]
    out += ["```", "", "## score", ""]
    tables = [c["table"] for c in tp["score"]["channels"]]
    for v in tp["score"]["voices"]:
        out += [
            "```",
            "voice %d: order %s" % (v["copy"], " ".join(o["pattern"] for o in v["order"])),
        ]
        for pid, rows_ in v["patterns"].items():
            out.append("%s:" % pid)
            out += ["  %3d %s" % (r["dur"], rowtext(r, tables)) for r in rows_]
        out += ["```", ""]
    return "\n".join(out)


def rowtext(r, tables):
    """A row's bytes per channel, ``@k`` where the span starts off the cursor."""
    out = []
    for t in tables:
        if t in r["bytes"]:
            at = r["at"].get(t)
            out.append(("@%d " % at if at else "") + " ".join("%02X" % b for b in r["bytes"][t]))
    return " | ".join(out)


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


def stores(tp):
    """The producers that write: the tick's and the fetches' stores."""
    return [p for p in tp["producers"] if p["kind"] == "store"] + [
        p for f in tp["score"]["fetch"] for p in f["producers"] if p["kind"] == "store"
    ]


def numbers(tp, md):
    """The six numbers of a trackerprog print plus its ``xz -9e`` size.

    Statements are the score's rows and the producers; blocks the patterns, streams,
    regions and the instrument table; data rows the pitch, instrument and stream entries.
    """
    got = measure(md, "score")
    voices = tp["score"]["voices"]
    ins = tp["instruments"]
    got["statements"] = sum(len(r) for v in voices for r in v["patterns"].values()) + len(
        stores(tp)
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


__all__ = ["KEYS", "to_json", "from_json", "digest"]
