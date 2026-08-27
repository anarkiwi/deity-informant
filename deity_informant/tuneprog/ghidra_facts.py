"""Export a finished pipeline output directory as facts for headless Ghidra.

``ghidra_facts.json`` + ``image_post_init.bin`` carry what a static tool cannot
know: the post-init image, entries, SMC cells, jump targets, regions, inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import grid
from . import ir
from . import nmi as N
from .lift import lift_trace
from .tracedata import Trace

LANGUAGE = "6510:LE:16:default"
CONTEXT = {"imm": "smc_imm", "addr": "smc_addr", "ctrl": "smc_ctrl", "opcode": "smc_var"}
RTS = 0x60
META = ("init", "play", "load", "song", "songs", "calls", "period", "first_repeat", "schedule")


def _smc_kind(ls):
    """The SMC kind of an operand cell: a patched ``JMP (ind)`` pointer is data."""
    if ls.mode == "ind":
        return "addr"
    if ls.ctrl_cell:
        return "ctrl"
    return "imm" if ls.mode == "imm" else "addr"


def smc_cells(trace, lifted):
    """One record per instruction whose own bytes the play routine writes."""
    out = {}
    for ls in lifted.values():
        cells = [a for a in ((ls.pc + k) & 0xFFFF for k in range(ls.length)) if a in trace.cells]
        if not cells:
            continue
        rec = out.setdefault(
            ls.pc, {"pc": ls.pc, "len": 0, "kinds": [], "cells": [], "variants": []}
        )
        rec["len"] = max(rec["len"], ls.length)
        rec["cells"] = sorted(set(rec["cells"]) | set(cells))
        rec["mnemonic"], rec["mode"] = ls.mnemonic, ls.mode
        kind = _smc_kind(ls)
        if any(c != ls.pc for c in cells) and kind not in rec["kinds"]:
            rec["kinds"].append(kind)
    for pc, rec in out.items():
        if pc in trace.cells:
            rec["kinds"].append("opcode")
            rec["variants"] = sorted(set(trace.variants_at(pc)) | trace.cell_values.get(pc, set()))
            rec["var_rts"] = RTS in rec["variants"]
        # smc_var already reads the operand live, so it subsumes smc_imm/smc_addr
        rec["context"] = (
            ["smc_var"]
            if rec.get("var_rts")
            else sorted({CONTEXT[k] for k in rec["kinds"] if k != "opcode"})
        )
    return [out[pc] for pc in sorted(out)]


def entries(procs_doc, names_doc=None):
    """Entry procedures: ``init``, the tick entries, every JSR/tail target."""
    named = (names_doc or {}).get("procs", {})
    rows = [
        {
            "addr": p["entry"],
            "name": named.get(p["name"], p["name"]),
            "kind": p["kind"],
            "roles": p["roles"],
            "live_in": p["summary"].get("live_in", []),
        }
        for p in procs_doc["procs"]
    ]
    rows.sort(key=lambda r: r["addr"])
    seen = set()
    for r in rows:
        if r["name"] in seen:
            r["name"] = "%s_%04X" % (r["name"], r["addr"])
        seen.add(r["name"])
    return rows


def computed_jumps(procs_doc, prog=None):
    """Computed-control sites with the target set the trace (plus S2 closure) proved."""
    out = {}
    for p in procs_doc["procs"]:
        for n in p["nodes"]:
            sw = n.get("switch")
            if not sw:
                continue
            rec = out.setdefault(
                n["pc"],
                {
                    "pc": n["pc"],
                    "mnemonic": n["mnemonic"],
                    "expr": sw["expr"]["kind"],
                    "computed": n["computed"],
                    "targets": set(),
                },
            )
            rec["targets"].update(t for t, _ref in sw["cases"])
    for proc in (prog.procs if prog else {}).values():
        for b in proc.blocks.values():
            if isinstance(b.term, ir.Switch) and b.src in out:
                out[b.src]["targets"].update(v for v, _lbl in b.term.cases)
    return [dict(r, targets=sorted(r["targets"])) for _pc, r in sorted(out.items())]


def tail_calls(procs_doc):
    """``JMP``s into a procedure entry: Ghidra needs a call+return flow override."""
    out = {}
    for p in procs_doc["procs"]:
        for n in p["nodes"]:
            if n["tail_call"] and n["call"]:
                out[n["pc"]] = {"pc": n["pc"], "target": n["call"][0]}
    return [out[pc] for pc in sorted(out)]


def regions(regions_doc, names_doc=None):
    """Regions as labels/data types: base, size, kind, stride, recovered name."""
    named = {r["id"]: r["name"] for r in (names_doc or {}).get("regions", [])}
    return [
        {
            "id": r["id"],
            "name": named.get(r["id"], r["name"]),
            "base": r["base"],
            "size": r["size"],
            "kind": r["kind"],
            "stride": r.get("stride", 1),
            "origin": r.get("origin", r["base"]),
            "fields": r.get("fields", []),
            "count": len(r["addrs"]),
        }
        for r in regions_doc
    ]


def inputs(trace):
    """The pinned-input sites: where the program read something the trace fixed."""
    return [
        {"pc": pc, "addr": addr, "kind": v["kind"], "count": v["count"]}
        for (pc, addr), v in sorted(trace.input_sites.items())
    ]


REG_IN = 0x10000
EMU_CALLS = 8


def _tick_writes(trace, calls, base, size):
    """Per-call SID register *changes* the tick entry made, in consumption order.

    Two rules the emulating side has to share, or it scores a difference where
    there is none. A store to ``$D400-$D418`` reaches the chip only while the
    6510 port maps I/O, which is why :class:`~.tracevm.TraceVM` gates the log on
    it -- a store made with I/O banked out is RAM and no register change. And a
    row the schedule's second entry made (``nmi``) belongs to that entry: this
    comparison runs the tick alone. The change rule itself is
    :func:`~.grid.changes`, seeded from the post-init image both sides start from.
    """
    w = trace.wlog
    # call < calls drops the init rows too: they carry 0xFFFFFFFF, and what is left
    # is the ascending column searchsorted needs
    keep = (w["nmi"] == 0) & (w["call"] < calls) & (w["addr"] >= base) & (w["addr"] < base + size)
    call = np.asarray(w["call"], np.int64)[keep]
    addr = np.asarray(w["addr"], np.int64)[keep] - base
    val = np.asarray(w["val"], np.int64)[keep]
    sel = grid.changes(addr, val, trace.image_post_init[base : base + size])
    call, addr, val = call[sel], addr[sel].tolist(), val[sel].tolist()
    at = np.searchsorted(call, np.arange(calls + 1))
    return [[[addr[i], val[i]] for i in range(at[c], at[c + 1])] for c in range(calls)]


def emulate_facts(trace, calls=EMU_CALLS, base=0xD400, size=0x19):
    """Each play call the trace ran, up to ``calls``, as the tick entry's SID writes.

    ``pins`` are the entry registers a call read live-in and ``reads`` every
    other volatile input it consumed, both in consumption order and both, like
    ``writes``, the tick entry's own: an input a handler of the second entry
    consumed is replayed by that entry, which this comparison does not run.
    """
    if not trace.wlog or "call" not in trace.wlog:
        return None
    calls = min(calls, int(trace.meta.get("calls") or 0))
    nsites = N.sites(trace) if N.entries(trace) else frozenset()
    pins, reads = [], [[] for _ in range(calls)]
    # init-phase inputs (call -1) are already baked into the post-init image
    for c, site, _i, a, v in trace.inputs:
        if not 0 <= c < calls or site in nsites:
            continue
        if a >= REG_IN:
            pins.append([int(c), int(a) - REG_IN, int(v)])
        else:
            reads[c].append([int(site), int(a), int(v)])
    return {
        "calls": calls,
        "entry": "tick",  # writes/reads/pins are the first schedule entry's alone
        "sid_base": base,
        "sid_len": size,
        "writes": _tick_writes(trace, calls, base, size),
        "pins": pins,
        "reads": reads,
        "regs": trace.meta.get("post_init_regs") or {},
        "unpinned_inputs": [i for i in inputs(trace) if i["addr"] < REG_IN],
    }


def _load(path):
    return json.loads(path.read_text()) if path.exists() else None


def facts(out_dir):
    """``(facts document, post-init image)`` for a finished pipeline directory."""
    out = Path(out_dir)
    trace = Trace.load(out)
    lifted = lift_trace(trace)
    procs_doc = _load(out / "procs.json")
    names_doc = _load(out / "tuneprog.S6.json")
    s4 = out / "tuneprog.S4.json"
    prog = ir.Tuneprog.load(s4) if s4.exists() else None
    doc = {
        "language": LANGUAGE,
        "meta": {k: trace.meta.get(k) for k in META},
        "image": "image_post_init.bin",
        "entries": entries(procs_doc, names_doc),
        "insn_addrs": sorted({k[0] for k in trace.sites}),
        "smc_cells": smc_cells(trace, lifted),
        "computed_jumps": computed_jumps(procs_doc, prog),
        "tail_calls": tail_calls(procs_doc),
        "regions": regions(_load(out / "regions.json"), names_doc),
        "inputs": inputs(trace),
        "emulate": emulate_facts(trace),
    }
    return doc, trace.image_post_init


def export(out_dir, dst=None):
    """Write ``ghidra_facts.json`` + ``image_post_init.bin``; returns the directory."""
    dst = Path(dst) if dst else Path(out_dir) / "ghidra"
    dst.mkdir(parents=True, exist_ok=True)
    doc, image = facts(out_dir)
    (dst / "image_post_init.bin").write_bytes(image)
    (dst / "ghidra_facts.json").write_text(json.dumps(doc, indent=1))
    return dst
