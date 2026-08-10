"""Regenerate out/: canonical frameprog text plus per-tune metrics.

out/ is gitignored (HVSC-derived); this keeps it current for progress review at
full Songlengths length. The text is verified as it is written: Gate FP against
the model walker, and the loads/dumps fixpoint.
"""

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

SHOWCASE = [
    "MUSICIANS/H/Hubbard_Rob/Commando.sid",
    "MUSICIANS/H/Hubbard_Rob/Monty_on_the_Run.sid",
    "MUSICIANS/G/Goto80/Automatas.sid",
    "MUSICIANS/D/Daglish_Ben/Krakout.sid",
    "MUSICIANS/D/Daglish_Ben/Trap.sid",
    "MUSICIANS/G/Galway_Martin/Athena.sid",
    "MUSICIANS/G/Galway_Martin/Wizball.sid",
    "MUSICIANS/F/Follin_Tim/Bionic_Commando.sid",
    "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid",
    "MUSICIANS/L/Laxity/Freeze.sid",
]

# The texts are named off the stem, which is unique in this list but not in HVSC.
assert len({Path(r).stem for r in SHOWCASE}) == len(SHOWCASE), "two showcase tunes share a stem"

USAGE = """\
  python tools/showcase.py                            # every showcase tune
  python tools/showcase.py --tunes Commando,Krakout   # named tunes, the rest left alone"""


def _frameprog_text(model, ev, frames, row):
    """frameprog text, held to Gate FP and to the canonical fixpoint (docs 1.4).

    ``gate`` None is the pass: the frame program's projection equals the walker's
    for every frame. A tune that diverges still writes its text, because the text
    is what a divergence is read off."""
    from deity_informant import frameprog
    from deity_informant import frameval
    from deity_informant import structured as S

    t0 = time.monotonic()
    row["bit_exact_standalone"] = _sweep.wlog_matches(ev, S.Walker(model).run(frames))
    prog = frameprog.program(model)
    frameprog.check_locals(prog.procs)
    text = frameprog.dumps(prog)
    gate = frameval.gate_fp(model, frames, prog)
    row["frameprog_build_s"] = round(time.monotonic() - t0, 1)
    row["frameprog_bytes"] = len(text)
    row["frameprog_lines"] = text.count("\n")
    row["frameprog_fixpoint"] = frameprog.dumps(frameprog.loads(text)) == text
    row["frameprog_gate"] = None if gate is None else list(gate)
    row["hi_first_stores"] = text.count("hi-first ")
    return text


def one(rel):
    """One tune: the model built once, then the frame program emitted off it."""
    from deity_informant.c64 import load_psid

    from _corpus import corpus_params

    hvsc = ROOT / ".oracle-cache" / "hvsc"
    entry = next((t for t in corpus_params(hvsc) if str(t[0]).endswith(rel)), None)
    if entry is None:
        return {"tune": rel[:-4], "name": Path(rel).stem, "error": "not cached"}
    sid, sub, secs = entry
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
    frames = secs * 50
    t0 = time.monotonic()
    model, ev = _sweep.decompile(mem, init, play, frames, sub)
    row = {
        "tune": _sweep.tune_id(sid),
        "name": sid.stem,
        "subtune": sub,
        "secs": secs,
        "frames": frames,
        "writes": ev.wlog_len,
        "build_s": round(time.monotonic() - t0, 1),
        "guard_live": sorted(
            "$%04X" % s for s, p in model.proofs.items() if p.status != "certified"
        ),
        "certified": sum(p.status == "certified" for p in model.proofs.values()),
    }
    try:
        text = _frameprog_text(model, ev, frames, row)
    except Exception as exc:  # pylint: disable=broad-except
        row["frameprog_error"] = "%s: %s" % (type(exc).__name__, exc)
        return row
    (ROOT / "out" / ("%s.frameprog.txt" % sid.stem)).write_text(text, encoding="utf-8")
    return row


def select(names):
    """The showcase entries ``names`` picks, or every one of them."""
    if not names:
        return list(SHOWCASE)
    want = {n.strip().lower() for n in names.split(",") if n.strip()}
    hits = [r for r in SHOWCASE if Path(r).stem.lower() in want]
    missing = want - {Path(r).stem.lower() for r in hits}
    if missing:
        sys.exit(
            "not a showcase tune: %s\navailable: %s"
            % (", ".join(sorted(missing)), ", ".join(Path(r).stem for r in SHOWCASE))
        )
    return hits


def merge(path, rows):
    """This run's rows over whatever the last one left, so ``--tunes`` keeps the rest."""
    old = []
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            old = []
    keep = {r["tune"]: r for r in old if isinstance(r, dict) and "tune" in r}
    keep.update({r["tune"]: r for r in rows})
    return [keep[k] for k in sorted(keep)]


def failures(rows):
    """Every row field that says a law did not hold, as ``tune: what``."""
    out = []
    for r in rows:
        for key, val in sorted(r.items()):
            bad = val is False if key.endswith(("_fixpoint", "_standalone")) else None
            if key.endswith("error") or (key == "frameprog_gate" and val is not None):
                bad = True
            if bad:
                out.append("%s: %s=%s" % (r.get("name", r.get("tune")), key, val))
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated showcase stems; default all of them")
    ap.add_argument("-j", "--procs", type=int, default=10)
    args = ap.parse_args()

    rels = select(args.tunes)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    for rel in rels:  # only the texts this run rewrites: a stale one must not survive
        (out / ("%s.frameprog.txt" % Path(rel).stem)).unlink(missing_ok=True)
    with mp.Pool(min(len(rels), args.procs)) as pool:
        rows = pool.map(one, rels)
    (out / "showcase.json").write_text(json.dumps(merge(out / "showcase.json", rows), indent=1))
    for r in rows:
        print(json.dumps(r))
    bad = failures(rows)
    for line in bad:
        sys.stderr.write("FAILED %s\n" % line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
