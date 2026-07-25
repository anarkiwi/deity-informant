"""EXPERIMENTAL (Follin dispatch study): model-side scan.

Decompiles a Follin tune full-length, then measures: block pcs with no static
predecessor, the dynamic sites (rts/jmpd/jmpind) covering them via observed
targets, and the structured_pct headroom if dispatch landings nested.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from framelog import HVSC

from deity_informant import codec, sidprog, structured
from deity_informant.c64 import load_psid, psid_songs, song_lengths, song_seconds
from deity_informant.render import _static_preds, _succs

OUT = ROOT / "out" / "study"


TT_LOG = []


def _log_term_targets():
    orig = structured.Analysis.term_targets

    def logged(self, blk):
        out = orig(self, blk)
        TT_LOG.append((blk.pcs[-1], blk.term[0], len(out)))
        return out

    structured.Analysis.term_targets = logged


def build(sid_path, frames=None, subtune=None):
    data = Path(sid_path).read_bytes()
    mem, _load, init, play = load_psid(data)
    mem[0xD418] = 0x0F
    _songs, start = psid_songs(data)
    if subtune is None:
        subtune = start - 1
    if frames is None:
        sl = song_lengths((HVSC / "Songlengths.md5").read_text("latin-1"))
        frames = song_seconds(data, sl, subtune) * 50
    _log_term_targets()
    model, _ev = structured.decompile(mem, init, play, frames, subtune)
    return model


def scan(model):
    gp = _static_preds(model)
    entries = set(codec.procedures(model)) | {model.init, model.play}
    pcs = sorted({pc for pc, _op in model.blocks})
    pcset = set(pcs)
    orphan = [pc for pc in pcs if not gp.get(pc) and pc not in entries]

    site_kind = {}
    site_targets = {}
    for (_pc, _op), blk in model.blocks.items():
        if blk.term[0] in ("rts", "jmpd", "jmpind"):
            obs = model.ev_targets.get(blk.pcs[-1])
            if obs:
                site_kind[blk.pcs[-1]] = blk.term[0]
                site_targets[blk.pcs[-1]] = sorted(obs)

    cover = {pc: sorted(s for s, ts in site_targets.items() if pc in ts) for pc in orphan}

    succ = {}
    for (pc, _op), blk in model.blocks.items():
        succ.setdefault(pc, set()).update(s for s in _succs(model, blk) if s in pcset)
        if blk.term[0] == "jsr" and blk.term[1] is not None:
            succ[pc].add(blk.term[1])
    seen = set()
    stack = list(entries)
    while stack:
        pc = stack.pop()
        if pc in seen or pc not in succ:
            continue
        seen.add(pc)
        stack.extend(succ[pc])
    only_dyn = [pc for pc in pcs if pc not in seen]

    mt = sidprog.metrics(model)
    exec_pcs = [pc for pc in pcs if pc in model.pcs]
    tt_big = sorted({(pc, k): n for pc, k, n in TT_LOG if n > 32}.items(), key=lambda kv: -kv[1])[
        :8
    ]
    return {
        "block_keys": len(model.blocks),
        "block_pcs": len(pcs),
        "exec_pcs": len(exec_pcs),
        "unexec_pcs": len(pcs) - len(exec_pcs),
        "orphan_pcs": len(orphan),
        "orphan_exec": sum(1 for pc in orphan if pc in model.pcs),
        "orphan_covered_by_sites": sum(1 for pc in orphan if cover[pc]),
        "dyn_sites": {
            "%04X" % s: {"kind": site_kind[s], "n_targets": len(site_targets[s])}
            for s in sorted(site_targets)
        },
        "closure_rounds_big": [["%04X" % pc, k, n] for (pc, k), n in tt_big],
        "static_reach_only_dyn": len(only_dyn),
        "unreach_exec": sum(1 for pc in only_dyn if pc in model.pcs),
        "metrics": mt,
    }


def pruned_metrics(model):
    """metrics() after dropping never-executed blocks (closure junk)."""
    for key in [k for k in model.blocks if k[0] not in model.pcs]:
        del model.blocks[key]
    model._by_pc = {}
    for pc, op0 in model.blocks:
        model._by_pc.setdefault(pc, []).append((pc, op0))
    for attr in ("_static_preds",):
        if hasattr(model, attr):
            delattr(model, attr)
    model.dyn_targets = {
        s: [t for t in ts if t in model.pcs] for s, ts in model.dyn_targets.items()
    }
    return sidprog.metrics(model)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid"
    t0 = time.time()
    model = build(HVSC / name)
    rep = scan(model)
    try:
        rep["pruned_metrics"] = pruned_metrics(model)
    except Exception as exc:  # pylint: disable=broad-except
        rep["pruned_metrics"] = {"error": str(exc)}
    rep["decompile_s"] = round(time.time() - t0, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / (Path(name).stem + ".modelscan.json")
    out.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))
    print("sites:", len(rep["dyn_sites"]), "->", out)


if __name__ == "__main__":
    main()
