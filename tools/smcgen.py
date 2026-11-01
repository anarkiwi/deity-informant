"""Regenerate the SMC witness corpus and expectation table.

``python tools/smcgen.py [--fuzz N] [--seed S] [--out-dir tests]`` runs progressive generation
on the oracle, classifies each kept program's lift outcome and writes ``smc_corpus.json`` (the
synthetic programs, labels and outcomes) and ``smc_expect.json`` (every feasible cell mapped to
its witnessed outcome or a gap reason). Deterministic and progress-reported.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.smc import generate, machine  # noqa: E402
from deity_informant.smc.expect import feasible_sids, gap_reason, lift_reason  # noqa: E402
from deity_informant.smc.outcomes import classify  # noqa: E402
from deity_informant.smc.space import cells  # noqa: E402

_EXACT = ("exact_nodeopt", "exact_deopt")


def build(seed, fuzz_rounds, progress=True):
    """The corpus (kept programs with outcomes) and the expectation table."""
    kept, covered, feasible = generate.generate(seed, fuzz_rounds, progress=progress)
    corpus = []
    seen = {}
    oracle = {}
    for prog in tqdm(kept, desc="classify", unit="prog", disable=not progress):
        outcome, deopts = classify(prog)
        rec = prog.to_dict()
        rec["outcome"] = outcome
        rec["deopts"] = deopts
        corpus.append(rec)
        for sid in prog.sids:
            seen.setdefault(sid, set()).add(outcome)
            oracle[sid] = prog.oracle

    def _merge(outs):
        if "inexact" in outs:
            return "inexact"
        return "exact_deopt" if "exact_deopt" in outs else "exact_nodeopt"

    outcome_of = {sid: _merge(outs) for sid, outs in seen.items()}
    by_sid = {c.sid: c for c in cells()}
    expect = {}
    for sid in sorted(feasible):
        cell = by_sid[sid]
        lift = lift_reason(cell)
        entry = {"witnessed": sid in covered, "oracle": oracle.get(sid)}
        if lift is None:
            entry["lift"] = outcome_of.get(sid, "placement-not-synthesized")
        else:
            entry["lift"] = lift
        entry["gap"] = gap_reason(cell, sid in covered)
        expect[sid] = entry
    return corpus, expect


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fuzz", type=int, default=400)
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent.parent / "tests"))
    args = ap.parse_args(argv)
    if not machine.core():
        sys.exit("vagrant-moses' native machine is required to generate machine witnesses")
    corpus, expect = build(args.seed, args.fuzz)
    out = Path(args.out_dir)
    (out / "smc_corpus.json").write_text(json.dumps(corpus, indent=1), encoding="utf-8")
    (out / "smc_expect.json").write_text(json.dumps(expect, indent=1), encoding="utf-8")
    witnessed = sum(1 for v in expect.values() if v["witnessed"])
    exact = sum(1 for v in expect.values() if v["lift"] in _EXACT)
    print(
        json.dumps(
            {
                "programs": len(corpus),
                "feasible": len(expect),
                "witnessed": witnessed,
                "lift_exact": exact,
                "lift_gaps": len(expect) - exact,
                "feasible_sids": len(feasible_sids()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
