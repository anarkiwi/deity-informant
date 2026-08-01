"""universality ratchet: what the graph still projects once its RAW nodes are ablated.

``from_frames`` replays every write verbatim, so the law and corpus byte-identity both
hold at zero generation. The share of ground truth the generators alone reproduce cannot
be carried in a side-car field; ``universality.json`` ledgers it, bound by measured bound."""

import json
from pathlib import Path

import pytest

from deity_informant import framelog as F
from deity_informant import frameprog
from deity_informant import structured as S
from deity_informant import tracker as T
from deity_informant.c64 import load_psid

from _corpus import corpus_params

ROOT = Path(__file__).resolve().parent.parent
HVSC = ROOT / ".oracle-cache" / "hvsc"
LEDGER = json.loads((Path(__file__).parent / "universality.json").read_text(encoding="utf-8"))
CORPUS = {p: sub for p, sub, _secs in corpus_params(HVSC)}  # resolving is what fetches them

# `charts` is on notice: it carries the recovered song beside the generators (docs §4j).
CHANNELS = frozenset({"nodes", "freq_table", "classes", "charts"})


def _ablated(graph):
    """The graph with its RAW replay emptied — node indices, and so every trigger, intact."""
    nodes = [g._replace(transfer=("RAW", ())) if g.transfer[0] == "RAW" else g for g in graph.nodes]
    return T.Graph(nodes, graph.freq_table, graph.classes)


def _share(graph, gt):
    """Share of ``gt``'s canonical entries the ablated graph still projects.

    Counted exactly where ``framelog.diff`` looks — section by section, position by
    position over the canonical records — so an entry agrees only where the law would
    call it equal, and no set-membership slack can inflate the figure."""
    got = T.eval_graph(_ablated(graph), len(gt))
    agree = total = 0
    for want, have in zip(gt, got):
        for sw, sh in zip(want, have):
            total += len(sw)
            agree += sum(1 for p in range(min(len(sw), len(sh))) if sw[p] == sh[p])
    return agree / total if total else 0.0


def _shallow(cov):
    """``imm`` + ``seed`` emits: the classes a byte is observed into, never generated."""
    return sum(c["imm"] + c["seed"] for c in (cov.classes or {}).values())


def _recovered(rel, nframes):
    """``(graph, ground truth)`` for a cached tune, by the pipeline tools/tracker_text.py runs."""
    path = HVSC / rel
    if path not in CORPUS:
        pytest.skip("corpus tune absent")
    mem, _load, init, play = load_psid(path.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, nframes, CORPUS[path])
    prog = frameprog.program(model)
    trace, _walker = frameprog.iota(model, nframes)
    gt, ords, lww, acc = T._observe(prog, trace, nframes)
    graph, _lanes = T._graph(prog, T._pitch(prog, T._freq_words(gt)), gt, ords, lww, acc)
    return graph, gt


def test_metric_is_honest():
    """The measure pins at both ends: verbatim replay scores 0, whole generation scores 1."""
    frames = [[(0, (7 * i) % 256)] for i in range(64)]
    gt = F.canonical(frames)
    assert _share(T.from_frames(frames), gt) == 0.0
    generated = T.Graph([T.ramp(0, 7, 256, T.FRAME, 0)])
    assert F.diff(T.eval_graph(generated, len(gt)), gt) is None
    assert _share(generated, gt) == 1.0


def test_ablation_scores_the_generated_half():
    """Half generator, half replay scores half — the law cannot tell the two apart."""
    replayed = [[(7, i % 256)] for i in range(64)]
    frames = [[(0, (7 * i) % 256)] + r for i, r in enumerate(replayed)]
    gt = F.canonical(frames)
    graph = T.Graph([T.ramp(0, 7, 256, T.FRAME, 0), T.raw(replayed)])
    assert F.diff(T.eval_graph(graph, len(gt)), gt) is None
    assert _share(graph, gt) == 0.5


def test_graph_carries_no_new_channel():
    """The allowlist is frozen, so a new channel beside the generators is a reviewed diff line."""
    assert frozenset(vars(T.Graph([]))) == CHANNELS
    assert not [k for k, v in vars(T.Graph).items() if not k.startswith("_") and not callable(v)]


@pytest.mark.parametrize("tune", LEDGER["tunes"], ids=lambda t: Path(t["rel"]).stem)
def test_universality_ratchets(tune):
    """Generation only grows; replay, observed fires and the shallow classes only shrink."""
    nframes = tune["frames"]
    graph, gt = _recovered(tune["rel"], nframes)
    assert F.diff(T.eval_graph(graph, nframes), gt) is None
    cov = T.coverage(graph, nframes)
    assert _share(graph, gt) >= tune["generative_share"]
    assert cov.residual <= tune["residual"]
    assert cov.triggers[1] - cov.triggers[0] <= tune["observed_fires"]
    assert _shallow(cov) <= tune["shallow"]
