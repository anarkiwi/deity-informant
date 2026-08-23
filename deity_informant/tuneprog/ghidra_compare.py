"""Differential complexity oracle: our program against Ghidra's, procedure by procedure.

Joins a pipeline output directory with the ``stats.json``/``coverage.json``/
``emulate.json`` a headless ``ExportHighPcode``/``EmulateTrace`` run wrote, and
flags procedures where our lifting is bigger than Ghidra's by more than ``tol``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import ir
from .lift import lift_trace
from .tracedata import Trace

TOL = 1.5
HEAD = re.compile(r"^(\w+)\((.*?)\):\s*#\s*\$([0-9A-Fa-f]{4})")


def md_procs(text):
    """``{entry address: (lines, gotos)}`` from the ``## program`` section."""
    body = text.split("## program", 1)[-1]
    out = {}
    for block in body.split("```")[1::2]:
        lines = block.strip("\n").splitlines()
        m = HEAD.match(lines[0].strip()) if lines else None
        if m:
            out[int(m.group(3), 16)] = (len(lines), sum(l.count("goto ") for l in lines))
    return out


def ours(out_dir):
    """``({entry: metrics}, {pc: raw P-Code ops})`` for one pipeline directory."""
    out = Path(out_dir)
    trace = Trace.load(out)
    lifted = lift_trace(trace)
    prog = ir.Tuneprog.load(out / "tuneprog.S4.json")
    md = md_procs((out / "tuneprog.md").read_text()) if (out / "tuneprog.md").exists() else {}
    by_pc = {}
    for key, ls in lifted.items():
        by_pc.setdefault(key[0], len(ls.ops))
    # the executed-site set of a procedure is S2b's, before S4 folded any of it
    cfg = json.loads((out / "procs.json").read_text())["procs"]
    sites = {p["name"]: {n["pc"] for n in p["nodes"]} for p in cfg}
    entries = {p["name"]: p["entry"] for p in cfg}
    rows = {}
    for proc in prog.procs.values():
        blocks = list(proc.blocks.values())
        pcs = sites.get(proc.name, {b.src for b in blocks if b.src})
        entry = entries.get(proc.name, blocks[0].src if blocks else 0)
        lines, gotos = md.get(entry, (0, 0))
        rows[entry] = {
            "name": proc.name,
            "entry": entry,
            "pcs": sorted(pcs),
            "sites": len(pcs),
            "raw_pcode_ops": sum(by_pc.get(p, 0) for p in pcs),
            "stmts": sum(len(b.stmts) for b in blocks),
            "blocks": len(blocks),
            "lets": sum(1 for b in blocks for s in b.stmts if isinstance(s, ir.Let)),
            "lines": lines,
            "gotos": gotos,
        }
    return rows, by_pc


def _per(d, k):
    return d[k] / max(1, d["sites"])


def _body(theirs):
    """The executed addresses one Ghidra function body owns."""
    return {int(a, 16) for a in theirs.get("pcs", ())}


def alignment(mine, theirs):
    """How our clone-per-entry procedures sit inside Ghidra's disjoint bodies."""
    merged = []
    for entry, t in sorted(theirs.items()):
        body = _body(t)
        held = sorted(m["name"] for m in mine.values() if body & set(m["pcs"]))
        if len(held) > 1:
            merged.append({"entry": "%04X" % entry, "ghidra": t["name"], "merges": held})
    by_pc = {}
    for m in mine.values():
        for p in m["pcs"]:
            by_pc.setdefault(p, set()).add(m["name"])
    clones = sorted({tuple(sorted(v)) for v in by_pc.values() if len(v) > 1})
    return {"merged": merged, "clones": [list(c) for c in clones]}


def _flag(mine, theirs, tol):
    """``(verdict, detail)`` for one procedure Ghidra decompiled cleanly."""
    clean = theirs["unresolved"] == 0 and theirs["unreachable"] == 0
    if not clean:
        return "ghidra_incomplete", "unresolved=%d unreachable=%d" % (
            theirs["unresolved"],
            theirs["unreachable"],
        )
    missed = sorted(set(mine["pcs"]) - _body(theirs))
    if missed:
        return "ghidra_partial", "body misses %d of %d executed sites (%s)" % (
            len(missed),
            len(mine["pcs"]),
            " ".join("%04X" % a for a in missed[:4]),
        )
    if _per(mine, "stmts") > tol * _per(theirs, "pcode_ops"):
        return "ours_bigger", "%.2f stmts/site vs %.2f ops/site" % (
            _per(mine, "stmts"),
            _per(theirs, "pcode_ops"),
        )
    if mine["gotos"] > tol * theirs["gotos"] + 1:
        return "ours_bigger", "%d gotos vs %d" % (mine["gotos"], theirs["gotos"])
    if theirs["pcode_ops"] < mine["stmts"]:
        return "ghidra_lead", "%d ops vs %d stmts" % (theirs["pcode_ops"], mine["stmts"])
    return "ok", ""


def compare(out_dir, ghidra_dir, tol=TOL):
    """The joined per-procedure table plus the coverage and semantic oracles."""
    g = json.loads((Path(ghidra_dir) / "stats.json").read_text())
    theirs = {int(r["entry"], 16): r for r in g["per_function"]}
    mine, by_pc = ours(out_dir)
    rows = []
    for entry in sorted(set(mine) | set(theirs)):
        m, t = mine.get(entry), theirs.get(entry)
        verdict, detail = ("unmatched", "") if not (m and t) else _flag(m, t, tol)
        rows.append(
            {"entry": "%04X" % entry, "ours": m, "ghidra": t, "verdict": verdict, "detail": detail}
        )
    align = alignment(mine, theirs)
    # procedures are cloned per entry, so distinct pcs -- not the per-proc sum
    union = {p for r in mine.values() for p in r.pop("pcs")}
    tot_m = {
        k: sum(r[k] for r in mine.values()) for k in ("stmts", "blocks", "lets", "lines", "gotos")
    }
    tot_m["sites"] = len(union)
    tot_m["raw_pcode_ops"] = sum(by_pc.get(p, 0) for p in union)
    doc = {
        "tolerance": tol,
        "totals": {"ours": tot_m, "ghidra": {k: g[k] for k in g if k != "per_function"}},
        "procs": rows,
        "alignment": align,
        "flags": [r for r in rows if r["verdict"] == "ours_bigger"],
    }
    for name in ("coverage", "emulate"):
        p = Path(ghidra_dir) / (name + ".json")
        if p.exists():
            doc[name] = json.loads(p.read_text())
    return doc


COLS = ("entry", "name", "sites", "raw", "ghidra_ops", "stmts", "c_lines", "md_lines", "gotos")


def markdown(doc):
    """The comparison as a markdown table (one row per procedure, then totals)."""
    lines = [
        "| proc | sites | raw pcode (G/us) | high pcode | S4 stmts | C lines | md lines"
        " | gotos (G/us) | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in doc["procs"]:
        m, t = r["ours"], r["ghidra"]
        if not m or not t:
            lines.append("| %s | - | - | - | - | - | - | - | %s |" % (r["entry"], r["verdict"]))
            continue
        lines.append(
            "| %s `%s` | %d | %d/%d | %d | %d | %d | %d | %d/%d | %s %s |"
            % (
                r["entry"],
                m["name"],
                m["sites"],
                t["raw_pcode_ops"],
                m["raw_pcode_ops"],
                t["pcode_ops"],
                m["stmts"],
                t["c_lines"],
                m["lines"],
                t["gotos"],
                m["gotos"],
                r["verdict"],
                r["detail"],
            )
        )
    tm, tg = doc["totals"]["ours"], doc["totals"]["ghidra"]
    lines.append(
        "| **total** | %d | %d/%d | %d | %d | %d | %d | %d/%d | |"
        % (
            tm["sites"],
            tg["raw_pcode_ops"],
            tm["raw_pcode_ops"],
            tg["pcode_ops"],
            tm["stmts"],
            tg["c_lines"],
            tm["lines"],
            tg["gotos"],
            tm["gotos"],
        )
    )
    align = doc.get("alignment") or {}
    for row in align.get("merged", ()):
        lines.append(
            "\nghidra `%s` (%s) merges %s" % (row["ghidra"], row["entry"], ", ".join(row["merges"]))
        )
    for group in align.get("clones", ()):
        lines.append("\nclone-per-entry: %s share executed sites" % ", ".join(group))
    return "\n".join(lines)
