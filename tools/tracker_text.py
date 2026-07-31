"""Regenerate out/<Tune>.trackertext.txt: the recovered tracker graph as text.

One file per tune at its full Songlengths duration — the law verdict and the coverage
partition, the engine, the instruments, every generator node, the per-voice note lane
and the explicit residual. out/ is gitignored. See docs/tracker-text.md."""

import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

SHOWCASE = [
    "MUSICIANS/H/Hubbard_Rob/Commando.sid",
    "MUSICIANS/G/Goto80/Automatas.sid",
    "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid",
    "MUSICIANS/D/Daglish_Ben/Krakout.sid",
]


def render_tune(rel):
    """Decompile one tune full-length, recover its graph, write out/<Tune>.trackertext.txt."""
    from deity_informant import framelog
    from deity_informant import frameprog
    from deity_informant import structured as S
    from deity_informant import tracker
    from deity_informant import trackertext
    from deity_informant.c64 import load_psid

    from _corpus import corpus_params

    t0 = time.time()
    hvsc = ROOT / ".oracle-cache" / "hvsc"
    entry = next((t for t in corpus_params(hvsc) if str(t[0]).endswith(rel)), None)
    if entry is None:
        return "%s: not cached" % Path(rel).stem
    sid, sub, secs = entry
    nframes = secs * 50
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, nframes, sub)
    prog = frameprog.program(model)
    trace, _walker = frameprog.iota(model, nframes)
    gt, ords, lww = tracker._observe(prog, trace, nframes)
    pitch = tracker._pitch(prog, tracker._freq_words(gt))
    graph, _lanes = tracker._graph(prog, pitch, gt, ords, lww)
    div = framelog.diff(tracker.eval_graph(graph, nframes), gt)  # the law, on this graph
    t1 = time.time()
    text = trackertext.emit(
        graph,
        nframes,
        prog,
        title="%s  subtune %d" % (sid.stem, sub),
        law="PASS" if div is None else "FAIL %r" % (div,),
    )
    path = ROOT / "out" / ("%s.trackertext.txt" % sid.stem)
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return "%-16s law=%s frames=%-6d nodes=%-4d lines=%-5d recover=%.0fs render=%.1fs -> %s" % (
        sid.stem,
        "PASS" if div is None else "FAIL",
        nframes,
        len(graph.nodes),
        text.count("\n"),
        t1 - t0,
        time.time() - t1,
        path.name,
    )


def main():
    """Render the showcase tunes at full length, four at a time."""
    with mp.Pool(4) as pool:
        for row in pool.map(render_tune, SHOWCASE):
            print(row)


if __name__ == "__main__":
    main()
