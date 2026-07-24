"""Structurer codec: region tree <-> block model, with a build-time inverse.

``structure`` nests a procedure's blocks into the region tree; ``flatten``
re-derives every block's successors from tree position alone; ``verify``
requires the two to agree on every play-phase procedure (spec 5).
"""

from __future__ import annotations

from . import render as R


class CodecError(ValueError):
    """The region tree is not a lossless rearrangement of the model CFG."""


def structure(model, entry):
    """Canonical (pre-switchify) region tree + labels for one procedure."""
    return R._structure(model, entry)


def procedures(model):
    """Play-phase procedure entries: play plus every static call target."""
    entries = [model.play]
    for blk in model.blocks.values():
        if blk.term[0] == "jsr" and blk.term[1] is not None:
            entries.append(blk.term[1])
    seen = set()
    return [e for e in entries if not (e in seen or seen.add(e))]


def _leaf_pc(region, loops):
    """The pc control reaches when entering ``region``, or None for no flow."""
    k = region.kind
    if k == "block":
        return region.b if region.b is not None else region.a.pc
    if k == "goto":
        return region.a
    if k == "cont":
        return loops[-1][0]
    if k == "brk":
        return loops[-1][1]
    if k == "loop":
        return _entry(region.a, None, loops)
    if k == "switch" and region.b:
        return region.b[0]
    return None


def _entry(region, default, loops):
    if region.kind == "seq":
        for r in region.a:
            pc = _leaf_pc(r, loops)
            if pc is not None:
                return pc
        return default
    pc = _leaf_pc(region, loops)
    return pc if pc is not None else default


class _Flatten:
    def __init__(self, model):
        self.model = model
        self.edges = {}  # block-leaf key -> successor pc set implied by the tree
        self.visited = []  # block pcs in emission order

    def _record(self, blk, key_pc, outs):
        if None in outs:
            raise CodecError("$%04X: tree flow dangles (no continuation)" % blk.pc)
        want = set(R._succs(self.model, blk))
        if outs != want:
            raise CodecError(
                "$%04X/$%02X: tree flow %s != term flow %s"
                % (blk.pc, blk.op0, sorted(outs), sorted(want))
            )
        self.edges[(key_pc, blk.op0)] = outs

    def _follow(self, items, i, follow, loops):
        for r in items[i:]:
            pc = _leaf_pc(r, loops)
            if pc is not None:
                return pc
            if r.kind in ("if", "exit", "switch"):
                raise CodecError("flow region %s reached as continuation" % r.kind)
        return follow

    def seq(self, items, follow, loops):
        i = 0
        while i < len(items):
            r = items[i]
            k = r.kind
            if k == "block":
                i = self._block(items, i, follow, loops)
            elif k == "seq":
                self.seq(r.a, self._follow(items, i + 1, follow, loops), loops)
                i += 1
            elif k == "loop":
                header = _entry(r.a, None, loops)
                exit_pc = self._follow(items, i + 1, follow, loops)
                body = r.a.a if r.a.kind == "seq" else [r.a]
                self.seq(body, None, loops + [(header, exit_pc)])
                i += 1
            elif k == "switch":
                i = self._dispatch(items, i, follow, loops)
            elif k in ("goto", "cont", "brk"):
                i += 1  # pure flow: consumed as a predecessor's continuation
            else:
                raise CodecError("unexpected %s region in sequence" % k)

    def _arm(self, arm, join, loops):
        items = arm.a if arm.kind == "seq" else [arm]
        self.seq(items, join, loops)
        return _entry(arm, join, loops)

    def _block(self, items, i, follow, loops):
        r = items[i]
        blk = r.a
        pc = blk.pc
        if r.b is not None:  # dispatch-arm leaves (b None) are owned by their switch
            self.visited.append(pc)
        nxt = items[i + 1] if i + 1 < len(items) else None
        if nxt is not None and nxt.kind == "if":
            join = self._follow(items, i + 2, follow, loops)
            outs = {self._arm(nxt.b, join, loops), self._arm(nxt.c, join, loops)}
            self._record(blk, pc, outs)
            return i + 2
        if nxt is not None and nxt.kind == "switch" and not nxt.b:
            sel, cases = nxt.a
            if sel == "call":
                self._record(blk, pc, {self._follow(items, i + 2, follow, loops)})
                return i + 2
            self._record(blk, pc, {self._arm(body, None, loops) for _l, body in cases})
            return i + 2
        if nxt is not None and nxt.kind == "exit":
            self._record(blk, pc, set())
            return i + 2
        self._record(blk, pc, {self._follow(items, i + 1, follow, loops)})
        return i + 1

    def _dispatch(self, items, i, follow, loops):
        r = items[i]
        _sel, cases = r.a
        pc = r.b[0]
        self.visited.append(pc)
        join = self._follow(items, i + 1, follow, loops)
        for _label, body in cases:
            arm = body.a if body.kind == "seq" else [body]
            if not arm or arm[0].kind != "block":
                raise CodecError("$%04X: dispatch case without a variant block" % pc)
            self.seq(arm, join, loops)
        return i + 1


def flatten(model, entry, root):
    """Successor sets implied by tree position for every block leaf, checked
    against each block's terminator on the way; raises :class:`CodecError`."""
    f = _Flatten(model)
    top = root.a if root.kind == "seq" else [root]
    for r in top:
        f.seq(r.a if r.kind == "seq" else [r], None, [])
    nodes, _succ, _pred = R._proc_cfg(model, entry)
    once = {}
    for pc in f.visited:
        once[pc] = once.get(pc, 0) + 1
    dup = sorted(pc for pc, n in once.items() if n > 1)
    if dup:
        raise CodecError("blocks emitted twice: %s" % ["$%04X" % p for p in dup])
    missing = sorted(set(nodes) - set(once))
    if missing:
        raise CodecError("reachable blocks dropped: %s" % ["$%04X" % p for p in missing])
    return f.edges


def verify(model):
    """flatten(structure(model)) == model CFG over every procedure, or raise."""
    for entry in procedures(model):
        root, _labels = structure(model, entry)
        flatten(model, entry, root)
    return model
