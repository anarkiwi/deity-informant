"""Regenerate out/: canonical sidprog text + metrics for representative tunes.

out/ is gitignored (HVSC-derived); this keeps it current for progress review:
one <Tune>.sidprog.txt per showcase tune (full Songlengths length, verified
bit-exact) and showcase.json with per-tune gate numbers.
"""

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


def one(rel):
    from deity_informant import sidprog
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    from _corpus import corpus_params

    hvsc = ROOT / ".oracle-cache" / "hvsc"
    entry = next((t for t in corpus_params(hvsc) if str(t[0]).endswith(rel)), None)
    if entry is None:
        return {"tune": Path(rel).stem, "error": "not cached"}
    sid, sub, secs = entry
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    frames = secs * 50
    t0 = time.monotonic()
    model, ev = S.decompile(mem, init, play, frames, sub)
    build_s = round(time.monotonic() - t0, 1)
    w = S.Walker(model)
    exact = w.run(frames) == ev.wlog
    text = sidprog.emit(model)
    tm = sidprog.parse(text)
    tw = S.Walker(tm)
    exact = exact and tw.run(frames) == ev.wlog and sidprog.emit(tm) == text
    met = sidprog.metrics(model)
    (ROOT / "out" / ("%s.sidprog.txt" % sid.stem)).write_text(text, encoding="utf-8")
    return {
        "tune": sid.stem,
        "subtune": sub,
        "secs": secs,
        "frames": frames,
        "writes": len(ev.wlog),
        "bit_exact_standalone": exact,
        "text_bytes": len(text),
        "evidence_sites": sorted("$%04X" % pc for pc in model.evidence_sites),
        "build_s": build_s,
        **met,
    }


def main():
    (ROOT / "out").mkdir(exist_ok=True)
    for old in (ROOT / "out").glob("*"):
        if old.suffix in (".sidc", ".txt", ".json"):
            old.unlink()
    with mp.Pool(min(len(SHOWCASE), 10)) as pool:
        rows = pool.map(one, SHOWCASE)
    (ROOT / "out" / "showcase.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
