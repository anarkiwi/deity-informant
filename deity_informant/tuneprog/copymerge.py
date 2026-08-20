"""S2c -- the copy index as a value: k chained copies plan down to one body.

Copy *j* running a template row is that row with ``v = j``: rows fold onto the
template's, a differing operand becomes a column ``T_x[v]``, the chain edge
increments ``v``, a count becomes a vector over ``v``. API: :func:`plan`.
"""

from __future__ import annotations

from .copyrows import SPACE, family


class Plan:
    """What the front end builds differently, keyed by procedure and site."""

    def __init__(self):
        self.fams = []
        self.nodes = {}  # (proc, pc, op) -> MNode
        self.absorbed = set()  # (proc, pc, op) -- a copy's node, now the template's
        self.tmpls = {}  # (proc, pc) -> template pc
        self.unions = []
        self.refused = []

    def __bool__(self):
        return bool(self.fams)

    def fams_of(self, proc):
        return [f for f in self.fams if f.proc == proc]

    def node(self, proc, pc, op):
        return self.nodes.get((proc, pc, op))

    def nodes_of(self, proc):
        """``[(pc, opcode, node)]`` of every merged node of one procedure."""
        return [(pc, op, m) for (p, pc, op), m in self.nodes.items() if p == proc]

    def op_at(self, proc, pc):
        """The opcode of the merged node at ``pc``, or ``None``."""
        return next((op for (p, q, op) in self.nodes if p == proc and q == pc), None)

    def chain(self, proc, src, to):
        """The family whose next copy this edge enters, or ``None``.

        A copy that does not fold keeps its own blocks, and the edge out of the
        last of them is still the loop's back edge.
        """
        for f in self.fams:
            j = f.column(src) if f.proc == proc else None
            if j is not None and j + 1 < f.k and to == f.bases[j + 1]:
                return f
        return None

    def tmpl(self, proc, pc):
        """The template pc a copy's ``pc`` folded onto (itself when it did not)."""
        return self.tmpls.get((proc, pc), pc)

    def entry_of(self, proc, pc):
        """The family whose loop ``pc`` opens, or ``None``."""
        return next((f for f in self.fams if f.proc == proc and f.entry == pc), None)

    def owner(self, proc, pc):
        """The family whose copies hold ``pc``, or ``None``."""
        return next((f for f in self.fams if f.proc == proc and f.column(pc) is not None), None)

    def to_dict(self):
        return {
            "families": [f.to_dict() for f in self.fams],
            "refused": [{"proc": p, "base": "$%04X" % b, "why": w} for p, b, w in self.refused],
        }


def _place(trace, regions, size, taken):
    """A band of ``size`` bytes no access, no code and no other region can see.

    Outside the load image, the stack page and I/O every byte is unknown to the
    machine, so a read of one is a pinned input whatever the byte holds.
    """
    lo, hi = trace.meta["load"]
    busy = bytearray(SPACE)
    for a, b in ((0, 0x200), (lo, hi), (0xD000, 0xE000)):
        busy[a:b] = b"\1" * (b - a)
    for r in regions:
        busy[r.base : r.base + r.size] = b"\1" * r.size  # a sparse region owns its span
    for a in trace.code | trace.written_play | trace.written_init | taken:
        busy[a] = 1
    run = 0
    for a in range(SPACE):
        run = run + 1 if not busy[a] else 0
        if run == size:
            return a - size + 1
    return None


def _opsat(cp, pc):
    return [o for (p, o) in cp.nodes if p == pc]


def _accept(out, cp, got, ctx):
    """Place one family's columns and record its nodes, or refuse it for want of room.

    Two families whose columns hold the same bytes are the same table: one clone
    of a procedure is not a second copy of its data.
    """
    trace, regions, taken = ctx
    fam, nodes, unions, tmpl = got
    same = next((f for f in out.fams if f.bytes() == fam.bytes() and fam.size), None)
    base = same.base if same else (_place(trace, regions, fam.size, taken) if fam.size else 0)
    if base is None:
        out.refused.append((fam.proc, fam.entry, "no room for the per-copy columns"))
        return
    fam.base = base
    fam.rid = same.rid if same else -4 - len(out.fams)
    taken.update(range(fam.base, fam.base + fam.size))
    out.fams.append(fam)
    out.unions += unions
    for (t0, op), mn in nodes.items():
        out.nodes[(fam.proc, t0, op)] = mn
    for pc, t0 in tmpl.items():
        out.tmpls[(fam.proc, pc)] = t0
        if pc != t0:
            out.absorbed.update((fam.proc, pc, o) for o in _opsat(cp, pc))


def plan(procs, trace, lifted, fams, regions=(), log=None):
    """The merge plan for every sibling family; a family that refuses is reported."""
    ctx = (trace, lifted, bytearray(trace.image_post_init))
    out, taken = Plan(), set()
    for sib in fams:
        cp = procs.get(sib.proc)
        got = "no such procedure" if cp is None else family(cp, sib, len(out.fams), ctx)
        if isinstance(got, str):
            out.refused.append((sib.proc, sib.bases[0], got))
            if log:
                log("  copies $%04X x%d refused: %s" % (sib.bases[0], len(sib.bases), got))
        else:
            _accept(out, cp, got, (trace, regions, taken))
    if log:
        for f in out.fams:
            log(
                "  copies $%04X x%d: %d rows, %d columns, table $%04X"
                % (f.entry, f.k, len(f.rows), len(f.cols), f.base)
            )
    return out


def report(prog):
    """The copy record of a built program: its families, and what no copy ran.

    A statement in a block some copy never reached is unverified: the trace saw
    the row in another copy, and the correspondence says it is this one's too.
    """
    doc = prog.meta.get("copies")
    if not doc:
        return None
    stmts, unver, cover = 0, 0, {}
    for p in prog.procs.values():
        for b in p.blocks.values():
            if not b.cover:
                continue
            stmts += len(b.stmts)
            unver += len(b.stmts) if 0 in tuple(b.cover) else 0
            key = ",".join(str(int(bool(c))) for c in b.cover)
            cover[key] = cover.get(key, 0) + len(b.stmts)
    return dict(doc, statements=stmts, unverified=unver, coverage=dict(sorted(cover.items())))
