#!/usr/bin/env python3
"""Aggregate the tuneprog sweep's JSONL rows into the markdown tables of the survey doc.

    python tools/survey/tuneprog_report.py --horizon h.jsonl --period p.jsonl \
        --results results.csv --hvsc C64Music > docs/survey-tuneprog.md tables

Rates are raw over the sample and re-weighted to the catalogued HVSC population
by SIDId family size, the way ``report.py`` does it for the static survey.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import Rates, q  # noqa: E402  # pylint: disable=wrong-import-position

EXEMPLARS = {
    "DefMon": "defMON, Automatas",
    "Rob_Hubbard": "Hubbard, Commando",
    "Martin_Galway": "Galway, Comic Bakery",
    "Stephen_Ruddy": "Follin, Ghouls'n'Ghosts",
    "JCH_NewPlayer": "JCH NewPlayer",
    "GoatTracker_V2.x": "GoatTracker 2",
    "Hermit/SidWizard_V1.x": "SID Wizard",
    "Electrosound": "Walker, Chameleon",
    "Blackbird/LFT": "Blackbird, Quintessence",
}
OUTCOMES = ("certified", "diverged", "refused", "crashed", "timeout", "oom", "incomplete")
OUT = []
P = OUT.append


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def population(results, hvsc):
    """``{family: tunes present on disk}`` over the whole catalogue."""
    pop, root = Counter(), Path(hvsc)
    with open(results, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (root / r["path"]).is_file():
                pop[r["player"]] += 1
    return pop


def head(*cols):
    P("| " + " | ".join(cols) + " |")
    P("|" + "|".join(["---"] * len(cols)) + "|")


def example(rs):
    return Path(min(rs, key=lambda r: r["path"])["path"]).name


def divclass(r):
    d = r.get("divergence") or {}
    if d.get("trap"):
        return "trap `%s`" % d["trap"]
    return "the `%s` write list differs" % d.get("compared")


def norm_site(r):
    """The fault's site, with generated-code frames collapsed into one class."""
    site = r.get("site") or "-"
    return "generated `tuneprog.py`" if site.startswith("<tuneprog>") else "`%s`" % site


def section_outcomes(rows, pop):
    R = Rates(rows, pop)
    P(
        "### Outcomes (sample of %d tunes, %d families)"
        % (len(rows), len({r["family"] for r in rows}))
    )
    P("")
    head("outcome", "tunes", "raw", "HVSC-weighted")
    for k in OUTCOMES:
        if any(r["outcome"] == k for r in rows):
            P(R.row(k, lambda r, k=k: r["outcome"] == k))
    P("")


def section_families(rows, pop):
    by = defaultdict(list)
    for r in rows:
        by[r["family"]].append(r)
    big = sorted(by, key=lambda f: -pop.get(f, 0))[:20]
    P("### Certification rate by family")
    P("")
    head("family", "HVSC", "sampled", "certified", "complete", "commonest other outcome")
    for fam in list(dict.fromkeys(big + [f for f in EXEMPLARS if f in by])):
        rs = by[fam]
        ok = sum(1 for r in rs if r["outcome"] == "certified")
        comp = sum(1 for r in rs if r.get("complete"))
        other = Counter(r["outcome"] for r in rs if r["outcome"] != "certified").most_common(1)
        name = "**%s** (%s)" % (fam, EXEMPLARS[fam]) if fam in EXEMPLARS else fam
        P(
            "| %s | %d | %d | %d (%.0f %%) | %d | %s |"
            % (
                name,
                pop.get(fam, 0),
                len(rs),
                ok,
                100.0 * ok / len(rs),
                comp,
                "%s ×%d" % other[0] if other else "–",
            )
        )
    P("")


def section_failures(rows, pop):
    R = Rates(rows, pop)
    div = [r for r in rows if r["outcome"] == "diverged"]
    P("### Failure classes (%d diverged tunes)" % len(div))
    P("")
    head("class", "tunes", "raw", "weighted", "first-divergence site", "example (tick)")
    by = defaultdict(list)
    for r in div:
        by[divclass(r)].append(r)
    for k, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        rep = min(rs, key=lambda r: r["path"])
        d = rep.get("divergence") or {}
        site = d.get("site") or (("`%s`" % d["detail"]) if d.get("detail") else "-")
        P(
            R.row(k, lambda r, s=set(id(x) for x in rs): id(r) in s)
            + " %s | %s (tick %s) |" % (site, example(rs), d.get("tick"))
        )
    P("")


def section_refusals(rows, pop):
    R = Rates(rows, pop)
    ref = [r for r in rows if r["outcome"] == "refused"]
    P("### Refusal reasons (%d refused tunes)" % len(ref))
    P("")
    head("reason", "tunes", "raw", "weighted", "raised at", "example")
    by = defaultdict(list)
    for r in ref:
        by[r["fault"]].append(r)
    for k, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        P(
            R.row("`%s`" % k, lambda r, s=set(id(x) for x in rs): id(r) in s)
            + " %s | %s |" % (min(rs, key=lambda r: r["path"])["site"], example(rs))
        )
    P("")


def section_crashes(rows):
    bad = [r for r in rows if r["outcome"] in ("crashed", "oom", "timeout", "incomplete")]
    P("### Crashes and non-answers (%d tunes)" % len(bad))
    P("")
    head("kind", "exception", "raised at", "tunes", "detail", "example")
    by = defaultdict(list)
    for r in bad:
        by[(r["outcome"], r.get("fault"), norm_site(r))].append(r)
    for (kind, fault, site), rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        detail = (min(rs, key=lambda r: r["path"]).get("detail") or "").replace("|", "/")[:70]
        P(
            "| %s | `%s` | %s | %d | %s | %s |"
            % (kind, fault, site, len(rs), detail or "–", example(rs))
        )
    P("")
    after = [r for r in rows if r.get("fault_after") == "certificate"]
    P("### Faults after the certificate (presentation only, %d tunes)" % len(after))
    P("")
    head("exception", "raised at", "tunes", "example")
    by2 = defaultdict(list)
    for r in after:
        by2[(r.get("fault"), norm_site(r))].append(r)
    for (fault, site), rs in sorted(by2.items(), key=lambda kv: -len(kv[1])):
        P("| `%s` | %s | %d | %s |" % (fault, site, len(rs), example(rs)))
    P("")


def section_complete(rows, pop, label="30 s horizon"):
    ok = [r for r in rows if r["outcome"] == "certified"]
    Rok = Rates(ok, pop)
    P("### Completeness (%s, %d certified programs)" % (label, len(ok)))
    P("")
    head("certified program", "tunes", "raw", "HVSC-weighted")
    P(
        Rok.row(
            "complete (a state repeat proved inside the horizon)", lambda r: bool(r.get("complete"))
        )
    )
    P(
        Rok.row(
            "a repeat was seen but the program is not complete",
            lambda r: r.get("period") is not None and not r.get("complete"),
        )
    )
    P(Rok.row("no repeat: horizon-capped", lambda r: r.get("period") is None))
    P("")


def section_stack(rows, pop):
    built = [r for r in rows if r.get("stack")]
    Rb = Rates(built, pop)
    res = [r for r in built if r["stack"] == "residual"]
    P("### The machine stack (%d built programs)" % len(built))
    P("")
    head("stack", "tunes", "raw", "HVSC-weighted")
    P(Rb.row("eliminated", lambda r: r["stack"] == "eliminated"))
    P(Rb.row("residual", lambda r: r["stack"] == "residual"))
    P("")
    if not res:
        return
    P("Residual programs by what kept the stack and how deep:")
    P("")
    head("residual", "tunes", "share of residual")
    held = Counter(p for r in res for p in (r.get("held") or ()))
    for k, n in held.most_common(8):
        P("| held by `%s` | %d | %.1f %% |" % (k, n, 100.0 * n / len(res)))
    for k, n in Counter(r.get("depth") for r in res).most_common(6):
        P("| depth %s bytes | %d | %.1f %% |" % (k, n, 100.0 * n / len(res)))
    for k, n in Counter(r.get("entry") for r in res).most_common():
        P("| entry kind `%s` | %d | %.1f %% |" % (k, n, 100.0 * n / len(res)))
    P("")


def section_entry(rows, pop):
    built = [r for r in rows if r.get("entry")]
    Rb = Rates(built, pop)
    P("### Entry kind and cadence (%d built programs)" % len(built))
    P("")
    head("entry", "tunes", "raw", "HVSC-weighted")
    P(Rb.row("`sub` (header play, JSR each tick)", lambda r: r["entry"] == "sub"))
    P(Rb.row("`irq` (installed handler)", lambda r: r["entry"] == "irq"))
    P(Rb.row("… through the KERNAL vector (CINV)", lambda r: bool(r.get("kernal"))))
    P(
        Rb.row(
            "… through the hardware vector", lambda r: r["entry"] == "irq" and not r.get("kernal")
        )
    )
    P("")
    head("cadence source", "tunes", "raw", "HVSC-weighted")
    for src, _n in Counter(r["source"] for r in built).most_common():
        P(Rb.row("`%s`" % src, lambda r, s=src: r["source"] == s))
    P("")
    head("PSID speed bits", "tunes", "raw", "HVSC-weighted")
    P(Rb.row("speed word 0 (every subtune host-framed)", lambda r: not r.get("speed_any_cia")))
    P(Rb.row("speed word non-zero", lambda r: bool(r.get("speed_any_cia"))))
    P(
        Rb.row(
            "… and the tune arms no timer of its own (host CIA cadence)",
            lambda r: "host_cia" in (r["source"] or ""),
        )
    )
    P("")


def section_copies(rows, pop):
    built = [r for r in rows if r.get("stack")]
    Rb = Rates(built, pop)
    fold = [r for r in built if (r.get("copy_families") or 0) > 0]
    P("### Copy folding (%d built programs)" % len(built))
    P("")
    head("copies", "tunes", "raw", "HVSC-weighted")
    P(Rb.row("at least one folded family", lambda r: (r.get("copy_families") or 0) > 0))
    P(Rb.row("two or more folded families", lambda r: (r.get("copy_families") or 0) > 1))
    P(
        Rb.row(
            "folded statements carry unverified arms", lambda r: (r.get("copy_unverified") or 0) > 0
        )
    )
    P(Rb.row("the fold refused a candidate", lambda r: bool(r.get("copy_refused"))))
    P("")
    if fold:
        fams = [r["copy_families"] for r in fold]
        stmts = [r.get("copy_statements") or 0 for r in fold]
        P(
            "Folded families per tune: median %s, p90 %s, max %s;"
            " folded statements median %s, max %s."
            % (
                statistics.median(fams),
                q(fams, 0.9),
                max(fams),
                statistics.median(stmts),
                max(stmts),
            )
        )
        P("")
    refused = Counter(x for r in built for x in (r.get("copy_refused") or ()))
    if refused:
        head("fold refused because", "occurrences")
        for k, n in refused.most_common(10):
            P("| `%s` | %d |" % (k, n))
        P("")


GATED = (
    ("residual-stack localisation", lambda r: r.get("stack") == "residual"),
    ("`irq` entry through CINV (the KERNAL frame)", lambda r: bool(r.get("kernal"))),
    (
        "`irq` entry through the hardware vector (raw `RTI` frame)",
        lambda r: r.get("entry") == "irq" and not r.get("kernal"),
    ),
    ("PSID speed word non-zero", lambda r: bool(r.get("speed_any_cia"))),
    (
        "host-CIA cadence (the speed flag decides it)",
        lambda r: "host_cia" in (r.get("source") or ""),
    ),
    (
        "periodicity obstruction: certified, no state repeat in 30 s",
        lambda r: r.get("outcome") == "certified" and r.get("period") is None,
    ),
    (
        "`fold.outline` leaves an edge to a deleted block (S6 `KeyError`)",
        lambda r: r.get("fault") == "KeyError" and r.get("site") == "graph.py:preds_of",
    ),
    (
        "an opcode cell whose alternatives exclude `RTS`",
        lambda r: (r.get("opcode_cells_non_rts") or 0) > 0,
    ),
    ("any SMC opcode cell", lambda r: (r.get("opcode_cells") or 0) > 0),
    (
        "two planes: a $D000-$DFFF byte reached as chip and as RAM",
        lambda r: (r.get("two_plane_bytes") or 0) > 0,
    ),
    ("reads the RAM under I/O at all", lambda r: (r.get("io_ram_bytes") or 0) > 0),
    ("an `RTS` that matched no `JSR` (the RTS trick)", lambda r: (r.get("rts_unmatched") or 0) > 0),
)


def section_gated(rows, pop):
    """Class sizes for the plan's data-gated backlog rows, over the whole sample."""
    R = Rates(rows, pop)
    P("### Data-gated class sizes (plan section 5)")
    P("")
    head("class", "tunes", "raw", "HVSC-weighted")
    for name, pred in GATED:
        P(R.row(name, pred))
    P("")


def section_shape(rows):
    built = [r for r in rows if r.get("stack")]
    P("### What the certified programs look like (%d built)" % len(built))
    P("")
    head("metric", "median", "mean", "p90", "p99", "max")
    for name, key in (
        ("executed sites", "sites"),
        ("regions", "regions"),
        ("procedures", "ir_procs"),
        ("S4 statements", "ir_statements"),
        ("ticks certified", "ticks"),
        ("inputs pinned", "inputs_pinned"),
        ("SMC cells", "smc_cells"),
        ("SMC cells a play site writes", "smc_play"),
    ):
        xs = [r[key] for r in built if r.get(key) is not None]
        P(
            "| %s | %.0f | %.0f | %.0f | %.0f | %.0f |"
            % (name, statistics.median(xs), statistics.mean(xs), q(xs, 0.9), q(xs, 0.99), max(xs))
        )
    P("")


def section_cost(rows, label="30 s horizon"):
    cpu = sum(r.get("cpu", 0.0) for r in rows)
    P("### Cost (%s)" % label)
    P("")
    head("stage", "CPU hours", "share", "per tune (s)")
    for k in ("trace", "front", "verify", "print"):
        v = sum(r.get("cpu_" + k) or 0.0 for r in rows)
        P(
            "| %s | %.2f | %.1f %% | %.2f |"
            % (k, v / 3600.0, 100.0 * v / max(cpu, 1e-9), v / len(rows))
        )
    P("| **total** | **%.2f** | 100 %% | %.2f |" % (cpu / 3600.0, cpu / len(rows)))
    P("")
    walls = [r["wall"] for r in rows]
    P(
        "Per-tune wall seconds: median %.1f, p90 %.1f, p99 %.1f, max %.1f."
        % (statistics.median(walls), q(walls, 0.9), q(walls, 0.99), max(walls))
    )
    P("")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--horizon", required=True)
    ap.add_argument("--period")
    ap.add_argument("--results", required=True)
    ap.add_argument("--hvsc", required=True)
    a = ap.parse_args(argv)
    pop = population(a.results, a.hvsc)
    rows = load(a.horizon)
    section_outcomes(rows, pop)
    section_families(rows, pop)
    section_failures(rows, pop)
    section_refusals(rows, pop)
    section_complete(rows, pop)
    section_stack(rows, pop)
    section_entry(rows, pop)
    section_copies(rows, pop)
    section_gated(rows, pop)
    section_shape(rows)
    section_cost(rows)
    section_crashes(rows)
    if a.period:
        prows = load(a.period)
        P("## The `--until-period` pass")
        P("")
        section_outcomes(prows, pop)
        section_complete(prows, pop, label="--until-period")
        section_refusals(prows, pop)
        section_cost(prows, label="--until-period")
        section_failures(prows, pop)
        section_crashes(prows)
    print("\n".join(OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
