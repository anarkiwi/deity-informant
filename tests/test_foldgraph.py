"""The fold's trace channels, on the driver suite (docs/fold-by-program-point.md 2).

Shapes, not magic numbers: a context's sp is one value, a node is a pc under a context,
an RTS whose successors are not its context's return is the S6/D2 fact, and a depth the
cap refuses names playbook S11.
"""

import pytest

import _callgen as G
import _idiomgen as I
import _stackgen as K
from deity_informant import structured as S

FRAMES = 8  # the row cursor reaches every arm of a four-way dispatch
_RTS = 0x60


def graph(row, label, frames=FRAMES):
    """The play-phase Graph of one driver variant."""
    mem, init, play, size = G.image(row, label)
    ev = S.trace(bytearray(mem), init, play, frames, img=(G.ORG, G.ORG + size), graph=True)
    return ev.graph


def by_ctx(g):
    """The pcs executed under each observed context."""
    out = {}
    for pc, _b, ctx, _sp in g.nodes:
        out.setdefault(ctx, set()).add(pc)
    return out


def ctxs_of(g, pc):
    """The contexts one pc was executed under."""
    return {ctx for at, _b, ctx, _sp in g.nodes if at == pc}


STRAIGHT = (("leaf-call", "head/sid", 1), ("nested", "X/post", 2), ("multi-site", "3-site/X", 1))
_SIDS = ["%s:%s" % (r, lb) for r, lb, _d in STRAIGHT]


@pytest.mark.parametrize("row,label,depth", STRAIGHT, ids=_SIDS)
def test_a_node_is_a_pc_under_a_context_and_sp_follows_from_it(row, label, depth):
    """F-ctx: no `(pc, ctx)` carries two sp, so the nodes are the per-context pcs."""
    g = graph(row, label)
    assert g.sp_violations() == []
    assert g.depth == depth
    assert len(g.nodes) == sum(len(pcs) for pcs in by_ctx(g).values())


@pytest.mark.parametrize("row,label,depth", STRAIGHT, ids=_SIDS)
def test_every_edge_relates_two_observed_nodes(row, label, depth):
    """F-cov: an edge is a transition between nodes, and only a return ends a run."""
    del depth
    g = graph(row, label)
    assert set(g.edges) <= g.nodes
    assert set().union(*g.edges.values()) <= g.nodes
    ends = {n for n in g.nodes if not g.edges.get(n)}
    assert ends and all(n[1][0] == _RTS and not n[2] for n in ends)


@pytest.mark.parametrize(
    "row,label,sites", ((("multi-site", "3-site/X", 3)), ("two-callers", "X/sid", 2))
)
def test_a_callee_reached_from_n_sites_is_n_regions(row, label, sites):
    """The measured node/pc inflation: the callee's pcs stand under one context per site."""
    g = graph(row, label)
    leaf = K.labels(row, label)["leaf"]
    assert len(ctxs_of(g, leaf)) == sites
    assert len(g.nodes) > len({pc for pc, _b, _c, _sp in g.nodes})


def test_an_rts_dispatch_lands_where_no_context_returns():
    """S6/D2: the pushed word is a table read, so the RTS successors are the arms."""
    row, label = "open-dispatch", "closed"
    g = graph(row, label)
    lab = K.labels(row, label)
    live = [n for n in g.nodes if n[1][0] == _RTS and g.edges.get(n)]
    assert len(live) == 1
    node = live[0]
    assert node[2] == ()  # a push, not a call, put the word there
    assert {s[0] for s in g.edges[node]} == {lab["sa%d" % k] for k in range(4)}


def test_recursion_grows_the_context_by_its_depth():
    """The depth is the recursion's own: `rec` stands under one context per activation."""
    row, label = "deep-recursion", "const/after"
    g = graph(row, label, frames=2)
    assert g.depth == 4  # play -> rec, then three self-calls
    assert len(ctxs_of(g, K.labels(row, label)["rec"])) == g.depth


def test_the_depth_cap_refuses_recursion_naming_its_row(monkeypatch):
    """S11: over the cap the walker refuses at the site, never traces deeper."""
    monkeypatch.setattr(S, "_CTX_CAP", 2)
    with pytest.raises(S.DecompileError) as exc:
        graph("deep-recursion", "const/after", frames=1)
    assert "depth 3 over cap 2" in str(exc.value) and "playbook S11" in str(exc.value)


def test_read_before_written_is_per_invocation():
    """F-loc: the frame cursor is read before any invocation writes it; the sink is not."""
    g = graph("leaf-call", "head/cell")
    assert I.ROW in g.rbw
    assert I.TMP not in g.rbw


def test_a_computed_load_site_records_the_span_it_walked():
    """F-loc: the site's address set is the declared column the frames indexed."""
    row, label = "leaf-call", "head/sid"
    g = graph(row, label)
    tone = K.labels(row, label)["tone"]
    assert {tone + k for k in range(8)} in g.ld_sites.values()


def test_a_computed_store_site_records_the_span_it_walked():
    """F-loc: the recursion's value stack is written and read over the same span."""
    row, label = "saved-recursion", "zp/const"
    g = graph(row, label, frames=2)
    span = {K.VSTK + k for k in (1, 2, 3)}
    assert span in g.st_sites.values()
    assert span in g.ld_sites.values()


def test_the_channels_change_nothing_the_pipeline_reads():
    """The fold's channels are additive: the static evidence is byte-identical."""
    mem, init, play, size = G.image("nested", "X/post")
    img = (G.ORG, G.ORG + size)
    off = S.trace(bytearray(mem), init, play, FRAMES, img=img)
    on = S.trace(bytearray(mem), init, play, FRAMES, img=img, graph=True)
    assert off.graph is None and on.graph is not None
    for channel in ("pcs", "leaders", "targets", "written", "wlog", "reads"):
        assert getattr(off, channel) == getattr(on, channel)
