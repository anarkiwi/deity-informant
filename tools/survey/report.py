"""Aggregate survey.jsonl.gz (+ headers.csv) into the markdown tables of the design doc.

    python tools/survey/report.py --survey survey.jsonl.gz --headers headers.csv > report.md

Rates are reported raw over the sample and re-weighted to the HVSC population
by SIDId family (family_size / sampled_in_family).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import statistics
from collections import Counter, defaultdict

PAL = 19656


def load(path):
    return [json.loads(line) for line in gzip.open(path, "rt")]


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


def dist(xs, name, fmt="%.0f"):
    xs = list(xs)
    if not xs:
        return "| %s | – | – | – | – | – |" % name
    return "| %s | %s | %s | %s | %s | %s |" % (
        name,
        fmt % statistics.median(xs),
        fmt % statistics.mean(xs),
        fmt % q(xs, 0.9),
        fmt % q(xs, 0.99),
        fmt % max(xs),
    )


class Rates:
    """Raw and family-weighted rates of boolean predicates over rows."""

    def __init__(self, rows, pop):
        self.rows = rows
        by = Counter(r["family"] for r in rows)
        self.w = {r["path"]: pop.get(r["family"], by[r["family"]]) / by[r["family"]] for r in rows}
        self.total_w = sum(self.w.values())

    def rate(self, pred, subset=None):
        rows = self.rows if subset is None else subset
        n = sum(1 for r in rows if pred(r))
        wsum = sum(self.w[r["path"]] for r in rows)
        wn = sum(self.w[r["path"]] for r in rows if pred(r))
        return n, len(rows), 100.0 * n / max(1, len(rows)), 100.0 * wn / max(1e-9, wsum)

    def row(self, name, pred, subset=None):
        n, d, raw, wt = self.rate(pred, subset)
        return "| %s | %d / %d | %.1f %% | %.1f %% |" % (name, n, d, raw, wt)


def calls_per_frame(r):
    return PAL / r["cadence"]["cycles_per_call"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--survey", required=True)
    ap.add_argument("--headers", required=True)
    a = ap.parse_args()
    rows = load(a.survey)
    hdr = list(csv.DictReader(open(a.headers)))
    pop = Counter(h["family"] for h in hdr)
    R = Rates(rows, pop)
    ok = [r for r in rows if not r.get("error")]
    Rok = Rates(ok, pop)
    out = []
    P = out.append

    P(
        "### 9.1 Outcomes of the dynamic trace (sample of %d tunes, %d families)"
        % (len(rows), len(pop))
    )
    P("")
    P("| outcome | tunes | raw | HVSC-weighted |")
    P("|---|---|---|---|")

    def cls(r):
        e = r.get("error") or "ok"
        if e.startswith("init:runaway"):
            return "init never returns/idles (RSID main loops, digi, BASIC)"
        if e.startswith("init"):
            return "init error"
        if e.startswith("no play"):
            return "no play entry found (play=0, no vector installed)"
        if "timeout" in e:
            return "per-tune wall timeout (very fast CIA cadence or heavy ticks)"
        if e.startswith("play"):
            return "play error (runaway/JAM/unlifted opcode)"
        if e.startswith("trace"):
            return "harness error"
        return "traced OK"

    for k in [
        "traced OK",
        "init never returns/idles (RSID main loops, digi, BASIC)",
        "no play entry found (play=0, no vector installed)",
        "play error (runaway/JAM/unlifted opcode)",
        "per-tune wall timeout (very fast CIA cadence or heavy ticks)",
        "init error",
        "harness error",
    ]:
        P(R.row(k, lambda r, k=k: cls(r) == k))
    P("")
    P(
        "Failure families (top, by HVSC weight): "
        + ", ".join(
            "%s (%d)" % (f, n)
            for f, n in Counter(r["family"] for r in rows if r.get("error")).most_common(12)
        )
    )
    P("")

    P("### 9.2 Cadence, entry and interrupt topology (traced tunes)")
    P("")
    P("| property | tunes | raw | HVSC-weighted |")
    P("|---|---|---|---|")
    P(
        Rok.row(
            "video-frame cadence (PAL or NTSC)",
            lambda r: r["cadence"]["source"] in ("pal_video", "ntsc_video"),
        )
    )
    P(Rok.row("CIA-timer cadence", lambda r: r["cadence"]["source"] == "cia_timer"))
    P(
        Rok.row(
            "  … 1× per frame (±2 %)",
            lambda r: r["cadence"]["source"] == "cia_timer" and abs(calls_per_frame(r) - 1) < 0.02,
        )
    )
    P(
        Rok.row(
            "  … 2×/3×/4×/6×/8× per frame",
            lambda r: r["cadence"]["source"] == "cia_timer"
            and any(abs(calls_per_frame(r) - k) < 0.05 for k in (2, 3, 4, 6, 8)),
        )
    )
    P(
        Rok.row(
            "  … > 16× per frame (sample-rate players)",
            lambda r: r["cadence"]["source"] == "cia_timer" and calls_per_frame(r) > 16,
        )
    )
    P(
        Rok.row(
            "  … other non-integer rates",
            lambda r: r["cadence"]["source"] == "cia_timer"
            and calls_per_frame(r) <= 16
            and not any(abs(calls_per_frame(r) - k) < 0.05 for k in (1, 2, 3, 4, 6, 8)),
        )
    )
    P(Rok.row("entry = header play (JSR each tick)", lambda r: r["entry"]["kind"] == "sub"))
    P(Rok.row("entry = installed IRQ handler", lambda r: r["entry"]["kind"] == "irq"))
    P(
        Rok.row(
            "  … through KERNAL vector $0314",
            lambda r: r["entry"]["kind"] == "irq" and r.get("topo", {}).get("irq_vector"),
        )
    )
    P(
        Rok.row(
            "  … through hardware vector $FFFE",
            lambda r: r["entry"]["kind"] == "irq"
            and not r.get("topo", {}).get("irq_vector")
            and r.get("topo", {}).get("hw_irq_vector"),
        )
    )
    P(
        Rok.row(
            "CIA-2 timer armed at init (second interrupt: NMI digi/sync)",
            lambda r: r.get("topo", {}).get("cia2_latch") is not None,
        )
    )
    P(
        Rok.row(
            "NMI vector installed at init",
            lambda r: r.get("topo", {}).get("nmi_vector") is not None,
        )
    )
    P(
        Rok.row(
            "writes $01 (banking) in init",
            lambda r: r.get("bank01", {}).get("sites", {}).get("init", 0) > 0,
        )
    )
    P(
        Rok.row(
            "writes $01 (banking) in play",
            lambda r: r.get("bank01", {}).get("sites", {}).get("play", 0) > 0,
        )
    )
    P(Rok.row("writes VIC registers in play", lambda r: r.get("io_writes", {}).get("VIC", 0) > 0))
    P(Rok.row("writes CIA registers in play", lambda r: r.get("io_writes", {}).get("CIA", 0) > 0))
    P(Rok.row("subtunes > 1 (header)", lambda r: r["hdr"]["songs"] > 1))
    P("")

    P("### 9.3 What the executed code looks like (traced tunes)")
    P("")
    P("| metric | median | mean | p90 | p99 | max |")
    P("|---|---|---|---|---|---|")
    P(dist([r["sites"] for r in ok], "executed play sites (instructions)"))
    P(dist([r["code_bytes"] for r in ok], "executed code bytes (init+play)"))
    P(
        dist(
            [r["per_call"]["insn_mean"] for r in ok if r.get("per_call")],
            "instructions per tick (mean)",
        )
    )
    P(
        dist(
            [r["per_call"]["insn_max"] for r in ok if r.get("per_call")],
            "instructions per tick (max)",
        )
    )
    P(dist([r["per_call"]["cyc_max"] for r in ok if r.get("per_call")], "cycles per tick (max)"))
    P(
        dist(
            [r["per_call"]["sidw_mean"] for r in ok if r.get("per_call")],
            "SID writes per tick (mean)",
            "%.1f",
        )
    )
    P(dist([r["sid_sites"] for r in ok], "distinct SID-writing sites"))
    P(dist([r["footprint"] for r in ok], "state footprint (RAM bytes written by play)"))
    P(dist([r["stack"]["max_jsr_depth"] for r in ok], "max JSR depth"))
    P(
        dist(
            [r["smc"]["cells"] for r in ok if r["smc"]["play_writer_sites"]],
            "SMC cells (tunes with play-time SMC)",
        )
    )
    P(dist([r["wall"] for r in ok], "trace wall seconds (60 s of music, Python)", "%.1f"))
    P("")

    P("### 9.4 Constructs the decompiler must model (traced tunes)")
    P("")
    P("| construct | tunes | raw | HVSC-weighted |")
    P("|---|---|---|---|")
    P(
        Rok.row(
            "play-time SMC (some play site writes executed instruction bytes)",
            lambda r: r["smc"]["play_writer_sites"] > 0,
        )
    )
    P(
        Rok.row(
            "  … operand cells only",
            lambda r: r["smc"]["play_writer_sites"] > 0 and r["smc"]["opcode_cells"] == 0,
        )
    )
    P(
        Rok.row(
            "  … opcode cells (instruction changes kind)", lambda r: r["smc"]["opcode_cells"] > 0
        )
    )
    P(
        Rok.row(
            "init-time writes into the load image (relocation/patching)",
            lambda r: r.get("init_image_writes", 0) > 0,
        )
    )
    P(Rok.row("illegal opcodes executed in play", lambda r: bool(r["illegal"])))
    P(Rok.row("`(zp,X)` addressing in play", lambda r: r["modes"].get("indx", 0) > 0))
    P(Rok.row("`(zp),Y` addressing in play", lambda r: r["modes"].get("indy", 0) > 0))
    P(Rok.row("`JMP (ind)` in play", lambda r: r["modes"].get("ind", 0) > 0))
    P(
        Rok.row(
            "RTS not matching a JSR (RTS trick / stack games)",
            lambda r: r["stack"]["unbalanced_rts"] > 0,
        )
    )
    P(Rok.row("JSR depth ≥ 3", lambda r: r["stack"]["max_jsr_depth"] >= 3))
    P(Rok.row("no JSR at all in play", lambda r: r["stack"]["max_jsr_depth"] == 0))
    vol = lambda r, k: r.get("volatile", {}).get(k, 0) > 0
    P(
        Rok.row(
            "volatile read in play: any (excluding $D019/CIA-ICR acks)",
            lambda r: any(vol(r, k) for k in ("D011", "D012", "D41B", "D41C", "D4xx", "VICother")),
        )
    )
    P(Rok.row("  … raster $D011/$D012", lambda r: vol(r, "D011") or vol(r, "D012")))
    P(Rok.row("  … SID read-back $D41B/$D41C", lambda r: vol(r, "D41B") or vol(r, "D41C")))
    P(Rok.row("  … reads of write-only SID registers", lambda r: vol(r, "D4xx")))
    P(Rok.row("  … other VIC registers", lambda r: vol(r, "VICother")))
    P(
        Rok.row(
            "  … CIA registers (incl. timer/ICR reads)", lambda r: vol(r, "CIA1") or vol(r, "CIA2")
        )
    )
    P(Rok.row("interrupt-ack read $D019 in play", lambda r: vol(r, "D019")))
    P(
        Rok.row(
            "reads uninitialised RAM (power-on pattern dependence)",
            lambda r: r.get("uninit_reads", 0) > 0,
        )
    )
    P(Rok.row("state repeated within 60 s (period found)", lambda r: r.get("period") is not None))
    P(
        Rok.row(
            "state repeated and song is ≤ 60 s (HVSC length)",
            lambda r: r.get("period") is not None
            and r.get("songlength")
            and r["songlength"][0] <= 60,
        )
    )
    P("")

    P("### 9.5 Index-register domains (how voice state shows itself)")
    P("")

    def dom_stats(r):
        idx = r.get("idx", {})
        sizes = []
        voicey = 0
        for pc, (mode, vals) in idx.items():
            if mode in ("absx", "absy", "zpx", "zpy"):
                s = set(vals)
                sizes.append(len(s))
                if s <= {0, 1, 2} or s <= {0, 7, 14} or s <= {0, 1, 2, 3}:
                    voicey += 1
        return sizes, voicey

    frac_voicey = []
    all_sizes = []
    for r in ok:
        sizes, voicey = dom_stats(r)
        if sizes:
            frac_voicey.append(voicey / len(sizes))
            all_sizes.extend(sizes)
    P("| metric | median | mean | p90 | p99 | max |")
    P("|---|---|---|---|---|---|")
    P(dist(all_sizes, "distinct index values per indexed site (all traced sites)"))
    P(
        dist(
            [100 * f for f in frac_voicey],
            "% of a tune's indexed sites with domain ⊆ {0,1,2} / {0,7,14} / {0..3}",
        )
    )
    P("")
    P("| property | tunes | raw | HVSC-weighted |")
    P("|---|---|---|---|")
    P(
        Rok.row(
            "≥ 50 % of indexed sites have a voice-like domain",
            lambda r: (lambda s: s[0] and s[1] / len(s[0]) >= 0.5)(dom_stats(r)),
        )
    )
    P(
        Rok.row(
            "uses X or Y ∈ {0,7,14} (SID-stride) somewhere",
            lambda r: any(set(v) == {0, 7, 14} for m, v in r.get("idx", {}).values()),
        )
    )
    P(
        Rok.row(
            "uses X or Y ∈ {0,1,2} somewhere",
            lambda r: any(set(v) == {0, 1, 2} for m, v in r.get("idx", {}).values()),
        )
    )
    P("")

    P("### 9.6 Engine identity within families (is decompilation reusable across a family?)")
    P("")
    fam = defaultdict(list)
    for r in ok:
        fam[r["family"]].append(r)
    lines = []
    identical_w = 0.0
    tot_w = 0.0
    for f, rs in fam.items():
        if len(rs) < 5:
            continue
        c = Counter(r["opseq_sha"] for r in rs)
        modal = c.most_common(1)[0][1]
        w = pop[f]
        tot_w += w
        identical_w += w * modal / len(rs)
        lines.append((w, f, len(rs), len(c), modal))
    lines.sort(reverse=True)
    P(
        "Executed-opcode-sequence signature (operands masked, relocation-invariant): distinct signatures per family in the sample."
    )
    P("")
    P("| family (HVSC size) | sampled | distinct engines | largest identical group |")
    P("|---|---|---|---|")
    for w, f, n, d, modal in lines[:30]:
        P("| %s (%d) | %d | %d | %d |" % (f, w, n, d, modal))
    P("")
    P(
        "Weighted over families with ≥ 5 traced samples: **%.0f %%** of tunes share their exact executed opcode sequence with the modal tune of their family (upper bound on 'decompile the engine once' reuse; lower bound because different songs exercise different code)."
        % (100 * identical_w / max(1e-9, tot_w))
    )
    P("")
    print("\n".join(out))


if __name__ == "__main__":
    main()
