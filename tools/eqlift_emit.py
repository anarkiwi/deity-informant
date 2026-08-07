"""Regenerate out/<Tune>.eqlift.txt: whole-artifact solver-lifted texts.

Per tune: full-Songlengths decompile, walker-replay bit-exactness gate
re-asserted, emission run twice and compared (determinism gate), then the
text plus a JSON stats line written under out/.
"""

import json
import sys
import time
from pathlib import Path

import _sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

TUNES = [
    "MUSICIANS/H/Hubbard_Rob/Commando.sid",
    "MUSICIANS/D/Daglish_Ben/Krakout.sid",
    "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid",
]

# The texts are named off the stem, which is unique in this list but not in HVSC.
assert len({Path(r).stem for r in TUNES}) == len(TUNES), "two tunes share a stem"


def one(rel):
    from deity_informant import eqlift_mem
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    from _corpus import corpus_params

    hvsc = ROOT / ".oracle-cache" / "hvsc"
    entry = next((t for t in corpus_params(hvsc) if str(t[0]).endswith(rel)), None)
    if entry is None:
        return {"tune": rel[:-4], "name": Path(rel).stem, "error": "not cached"}
    sid, sub, secs = entry
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    frames = int(secs * 50)
    model, ev = S.decompile(mem, init, play, frames, sub)
    if S.Walker(model).run(frames) != ev.wlog:
        raise AssertionError("%s: model walker replay is not bit-exact" % rel)
    t0 = time.monotonic()
    text, _ = eqlift_mem.emit(model)
    emit_s = round(time.monotonic() - t0, 2)
    text2, _ = eqlift_mem.emit(model)
    if text2 != text:
        raise AssertionError("%s: emission is not deterministic" % rel)
    (ROOT / "out" / ("%s.eqlift.txt" % sid.stem)).write_text(text, encoding="utf-8")
    return {
        "tune": _sweep.tune_id(sid),
        "name": sid.stem,
        "frames": frames,
        "bit_exact": True,
        "deterministic": True,
        "lines": text.count("\n"),
        "emit_s": emit_s,
    }


def main():
    (ROOT / "out").mkdir(exist_ok=True)
    for rel in TUNES:
        print(json.dumps(one(rel)))


if __name__ == "__main__":
    main()
