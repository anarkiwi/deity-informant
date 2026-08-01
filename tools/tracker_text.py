"""Regenerate out/*.txt: the recovered tracker graph as text, whole tunes.

``show`` writes one file per showcase tune at its full Songlengths duration; ``compare``
writes our recovery and the composer's own song (GoatTracker or DefMON) through the same
emitter, difference first. out/ is gitignored. See docs/tracker-text.md."""

import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HVSC = ROOT / ".oracle-cache" / "hvsc"

SHOWCASE = [
    "MUSICIANS/H/Hubbard_Rob/Commando.sid",
    "MUSICIANS/G/Goto80/Automatas.sid",
    "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid",
    "MUSICIANS/D/Daglish_Ben/Krakout.sid",
    # the corpus's one table read at a generated row, and its one phased tick (docs 4i).
    "MUSICIANS/B/Blanchette_Francois/Bird_on_the_Run_II.sid",
    "MUSICIANS/B/Bonifacio_Robert/Delta_Man.sid",
]
# tunes whose own song is ground truth: docs/gt-oracle.md §3.1's four, and a DefMON one.
ORACLES = [
    ("gt", "MUSICIANS/B/Benedens_Oliver/Dear_Enemy.sid"),
    ("gt", "MUSICIANS/C/Cubaxd/An_Old_Era.sid"),
    ("gt", "MUSICIANS/C/Chiummo_Aldo/25_Years_tune_1.sid"),
    ("dm", "MUSICIANS/G/Goto80/Automatas.sid"),
]
FACTS = ("patterns", "pattern_rows", "orderlist_entries", "instruments")
WORKERS = 4


def _tune(rel):
    """``(path, subtune, frames)`` at the tune's full Songlengths duration."""
    from deity_informant.c64 import psid_songs, song_lengths, song_seconds

    path = HVSC / rel
    lengths = song_lengths((HVSC / "Songlengths.md5").read_text(encoding="latin-1"))
    data = path.read_bytes()
    _songs, start = psid_songs(data)
    return path, start - 1, (song_seconds(data, lengths, start - 1) or 0) * 50


def _lifted(path, sub, nframes):
    """``(prog, trace)``: the tracker's whole input for one cached tune."""
    from deity_informant import frameprog
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    mem, _load, init, play = load_psid(path.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, nframes, sub)
    trace, _walker = frameprog.iota(model, nframes)
    return frameprog.program(model), trace


def _recovered(prog, trace, nframes):
    """``(graph, records, law verdict)``: the tracker's own recovery, gated on the spot."""
    from deity_informant import framelog, tracker

    gt, ords, lww, acc = tracker._observe(prog, trace, nframes)
    pitch = tracker._pitch(prog, tracker._freq_words(gt))
    graph, _lanes = tracker._graph(prog, pitch, gt, ords, lww, acc)
    div = framelog.diff(tracker.eval_graph(graph, nframes), gt)
    return graph, gt, "PASS" if div is None else "FAIL %r" % (div,)


def _write(stem, kind, text, t0, t1, extra=""):
    """Write one rendering and return the one-line log for it."""
    path = ROOT / "out" / ("%s.%s.txt" % (stem, kind))
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return "%-22s %-11s lines=%-6d recover=%4.0fs render=%5.1fs %s-> %s" % (
        stem,
        kind,
        text.count("\n"),
        t1 - t0,
        time.time() - t1,
        extra,
        path.name,
    )


def render_tune(rel):
    """Decompile one tune whole, recover its graph, write out/<Tune>.trackertext.txt."""
    from deity_informant import trackertext

    t0 = time.time()
    path, sub, nframes = _tune(rel)
    prog, trace = _lifted(path, sub, nframes)
    graph, _gt, law = _recovered(prog, trace, nframes)
    t1 = time.time()
    text = trackertext.emit(
        graph, nframes, prog, title="%s  subtune %d" % (path.stem, sub), law=law
    )
    return _write(path.stem, "trackertext", text, t0, t1, "law=%-4s " % law.split()[0])


def _native(editor, path, sub, nframes):
    """``(Native, editor name)`` for one cached tune, out of that editor's own model."""
    from deity_informant import dmoracle, gtoracle

    if editor == "gt":
        song, info, psub = gtoracle.gt_decompile(path, sub)
        return gtoracle.gt_native(song, info, psub, nframes), "GoatTracker"
    got = dmoracle.dm_decompile(path, nframes, sub)
    return (None if got is None else got[0]), "DefMON"


def _song_pitch(native):
    """The composer's own note→freq table as a `tracker.Pitch`, so their note lane inverts.

    Both editors name it `("pitch", lo/hi)`; without it the oracle graph emits pitch
    words no table reads back, and the two note lanes cannot be compared at all."""
    import numpy as np
    from deity_informant import tracker

    lo, hi = native.tables.get(("pitch", "lo")), native.tables.get(("pitch", "hi"))
    if not lo or not hi:
        return None
    words = np.array([a | (b << 8) for a, b in zip(lo, hi)], dtype=np.int64)
    return tracker._pitch_of(0, words, "<", None) if (words > 0).any() else None


def compare_tune(item):
    """Our recovery and the composer's own song, one tune, through the one emitter."""
    from deity_informant import gtoracle, tracker, trackertext

    editor, rel = item
    t0 = time.time()
    path, sub, nframes = _tune(rel)
    native, name = _native(editor, path, sub, nframes)
    if native is None:
        return "%-22s no %s song in this image" % (path.stem, name)
    prog, trace = _lifted(path, sub, nframes)
    graph, records, law = _recovered(prog, trace, nframes)
    built, report = gtoracle.graph(records, native)
    theirs = tracker.Graph(built.nodes, _song_pitch(native), classes=built.classes)
    t1 = time.time()
    sides = [
        trackertext.Side("ours", "recovered from the binary", graph, law, prog, 0),
        trackertext.Side(
            "song",
            "the composer's own %s song" % name,
            theirs,
            "PASS" if report.divergence is None else "FAIL",
            None,
            report.offset,
            report.frames,
        ),
    ]
    text = trackertext.compare(
        sides,
        nframes,
        title="%s  subtune %d" % (path.stem, sub),
        facts=[(k.replace("_", " "), native.structure[k]) for k in FACTS],
    )
    return _write(path.stem, "sidebyside", text, t0, t1, "%-11s off=%d " % (name, report.offset))


def _run(job):
    """One work item, its exception reported rather than lost in the pool."""
    fn, arg = job
    try:
        return fn(arg)
    except Exception as exc:  # pylint: disable=broad-except
        return "%-22s %s: %s" % (str(arg)[-22:], type(exc).__name__, str(exc)[:90])


def main(argv):
    """Render the showcase tunes and the side-by-side oracle comparisons, in parallel."""
    what = argv[1] if argv[1:] else "all"
    jobs = []
    if what in ("all", "show"):
        jobs += [(render_tune, r) for r in SHOWCASE]
    if what in ("all", "compare"):
        jobs += [(compare_tune, i) for i in ORACLES]
    with mp.Pool(WORKERS) as pool:
        for row in pool.imap_unordered(_run, jobs):
            print(row, flush=True)


if __name__ == "__main__":
    main(sys.argv)
