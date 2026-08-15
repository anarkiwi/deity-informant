"""Structured readable rendering of a decompiled playroutine (plan P5).

Nests the verified block/edge model into ``if``/``loop`` regions over named
state -- a human-facing view of the same model the walker replays byte-exact
(the executable artifact is the frame program, :mod:`deity_informant.frameprog`).
"""

from __future__ import annotations

from . import expr as E
from . import procpass
from .procpass import _dyn_targets, _idoms, _succs

# ---- named machine state ------------------------------------------------------
_VOICE = ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "ctrl", "attack_decay", "sustain_release")
_FILTER = {
    0xD415: "filter.cutoff_lo",
    0xD416: "filter.cutoff_hi",
    0xD417: "filter.resonance",
    0xD418: "filter.mode_vol",
}


def sid_name(addr):
    """Canonical SID register name for $D400-$D418, else None (normative table)."""
    if 0xD400 <= addr <= 0xD414:
        v, r = divmod(addr - 0xD400, 7)
        return "sid.v%d.%s" % (v + 1, _VOICE[r])
    return _FILTER.get(addr)


def name_addr(addr):
    """Readable name for a concrete memory address."""
    sid = sid_name(addr)
    if sid:
        return sid
    if addr < 0x0100:
        return "zp_%02X" % addr
    if 0x0100 <= addr < 0x0200:
        return "stack_%02X" % (addr & 0xFF)
    return "m_%04X" % addr


# ---- expression -> readable infix ---------------------------------------------
_INFIX = {
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_AND": "&",
    "INT_OR": "|",
    "INT_XOR": "^",
    "INT_LEFT": "<<",
    "INT_RIGHT": ">>",
    "INT_EQUAL": "==",
    "INT_NOTEQUAL": "!=",
    "INT_LESS": "<",
    "INT_LESSEQUAL": "<=",
}
_REGS = {0: "A", 1: "X", 2: "Y", 3: "SP", 8: "C", 9: "Z", 10: "I", 11: "D", 13: "V", 14: "N"}


_IO = {0xD012: "raster()", 0xD011: "raster_hi()", 0xD41B: "osc3()", 0xD41C: "env3()"}

_SPLIT_BLOCKS = 16  # blocks one node split may copy
_SPLIT_TOTAL = 1  # blocks a procedure may duplicate, as a multiple of its own count
_SPLIT_DEPTH = 3  # splits nested in a split, so one copy may be structured in turn
_STRUCT_ROUNDS = 4  # structuring passes the allow set is refined over


def _mem_text(addr):
    """A memory reference: named cell, IO source, or ``base[index]`` array."""
    if E.is_const(addr):
        a = addr[1]
        return _IO.get(a, name_addr(a))
    base, idx = _split_index(addr)
    if base is not None:
        return "%s[%s]" % (name_addr(base), fmt(idx))
    return "mem[%s]" % fmt(addr)


def _split_index(addr):
    """``(base, index)`` if ``addr`` is const-base plus a single index, else None."""
    if addr[0] == "op" and addr[1] == "INT_ADD":
        consts = [c for c in addr[2] if E.is_const(c)]
        rest = [c for c in addr[2] if not E.is_const(c)]
        if len(consts) == 1 and len(rest) == 1 and consts[0][1] >= 0x100:
            return consts[0][1], rest[0]
    return None, None


def fmt(n):
    """Readable infix for an expression node (addresses named, width noise elided)."""
    k = n[0]
    if k == "const":
        return "$%02X" % n[1] if n[2] == 1 else "$%04X" % n[1]
    if k == "reg":
        return _REGS.get(n[1], "r%d" % n[1])
    if k == "io":
        return n[1]
    if k == "uni":
        return "load%d" % n[1]
    if k in ("mem", "cur"):
        return _mem_text(n[1])
    mn, kids, _sz = n[1], n[2], n[3]
    if mn == "INT_ZEXT":
        return fmt(kids[0])
    if mn == "INT_CARRY":
        return "carry(%s, %s)" % (fmt(kids[0]), fmt(kids[1]))
    if mn == "INT_ADD":
        half = 1 << (8 * n[3] - 1)
        parts = [_paren(kids[0])]
        for c in kids[1:]:
            if c[0] == "const" and c[1] >= half:  # high two's-complement byte: subtract
                parts.append("- $%02X" % ((-c[1]) & E.mask(n[3])))
            else:
                parts.append("+ " + _paren(c))
        return " ".join(parts)
    if mn in _INFIX:
        return (" %s " % _INFIX[mn]).join(_paren(c) for c in kids)
    return "%s(%s)" % (mn, ", ".join(fmt(c) for c in kids))


def _paren(n):
    if n[0] == "op" and n[1] in _INFIX and len(n[2]) > 1:
        return "(%s)" % fmt(n)
    return fmt(n)


def _slotmap(blk):
    """Slot index -> inlined load expression (``mem[..]`` or an ``io`` node)."""
    smap = {}
    for ev in blk.events:
        if ev[0] == "ld":
            addr = _inline(ev[2], smap)
            if E.is_const(addr) and addr[1] in _IO:
                smap[ev[1]] = ("io", _IO[addr[1]])
            else:
                smap[ev[1]] = ("mem", addr, 1)
    return smap


def _inline(n, smap):
    k = n[0]
    if k == "uni":
        return smap.get(n[1], n)
    if k in ("mem", "cur"):
        return (k, _inline(n[1], smap), n[2])
    if k == "op":
        return ("op", n[1], tuple(_inline(c, smap) for c in n[2]), n[3])
    return n


# ---- CFG (intra-procedural; a call is one node) -------------------------------
def _proc_cfg(model, entry):
    """``(nodes, succ, pred)`` over block pcs reachable from ``entry`` without
    leaving the procedure; the first variant of each pc represents it."""

    def rep(pc):
        v = model.variants(pc)
        return v[0][0] if v else None

    nodes = []
    succ = {}
    seen = set()
    stack = [entry]
    while stack:
        pc = stack.pop()
        if pc in seen or rep(pc) is None:
            continue
        seen.add(pc)
        nodes.append(pc)
        outs = []
        for key in model.variants(pc):  # all SMC variants extend the CFG
            for s in _succs(model, model.blocks[key]):
                if rep(s) is not None and s not in outs:
                    outs.append(s)
                    stack.append(s)
        succ[pc] = outs
    pred = {pc: [] for pc in nodes}
    for pc in nodes:
        for s in succ[pc]:
            pred[s].append(pc)
    return nodes, succ, pred


def _postdoms(nodes, succ, pred, exits):
    """Immediate post-dominators via reversed-CFG dominators from a virtual exit."""
    rentry = "EXIT"
    rs = {rentry: list(exits)}
    for n in nodes:
        rs[n] = pred[n]
    idom, _ = _idoms(rentry, rs, [rentry] + nodes)
    return idom


# ---- region tree --------------------------------------------------------------
class Region:
    __slots__ = ("kind", "a", "b", "c")

    def __init__(self, kind, a=None, b=None, c=None):
        self.kind = kind  # block | seq | if | loop | goto | frontier | brk | cont | exit
        self.a = a
        self.b = b
        self.c = c


def _cat(*regions):
    """One flat ``seq`` of the given regions; a nested ``seq`` is spliced in."""
    items = []
    for r in regions:
        if r is None:
            continue
        items.extend(r.a if r.kind == "seq" else [r])
    return Region("seq", items)


def _is_frontier(model, pc):
    """A statically proven edge target with no serialized block anywhere."""
    return not getattr(model, "all_variants", model.variants)(pc)


def _dispatch_gates(model):
    """Handler-inlining gates: ``procpass.dyn_gates`` plus the static call targets
    (real procedures are never inlined into a dispatch arm)."""
    sites, calls = procpass.dyn_gates(model)
    static = {
        b.term[1] for b in model.blocks.values() if b.term[0] == "jsr" and b.term[1] is not None
    }
    return sites, calls, static


DYN_SWITCH = frozenset(("goto", "call"))  # the selectors a block's own transfer answers


class _Splits:
    """The pcs a structuring pass may scope or split at, the ones it did, and the
    ones a copy refused for binding them asked the next pass to free."""

    __slots__ = ("allow", "chosen", "wanted")

    def __init__(self, allow):
        self.allow = allow
        self.chosen = set()
        self.wanted = set()


def _structure(model, entry):
    """Region tree + labels, scoped or split only where that empties a label.

    Pass one uses neither and names the bound pcs; each pass after it enables both
    at that set less the pcs they did not free (Janssen & Corporaal 1997; Yakdan et
    al., NDSS 2015). A copy refused for a pc it would bind asks for that pc too."""
    best = _structure_once(model, entry, frozenset())
    allow = frozenset(best[1]) | best[2].wanted
    seen = set()
    for _round in range(_STRUCT_ROUNDS):
        if not allow or allow in seen:
            break
        seen.add(allow)
        got = _structure_once(model, entry, allow)
        if len(got[1]) < len(best[1]):  # never worse than the pass that used neither
            best = got
        allow = (allow | frozenset(got[1]) | got[2].wanted) - (got[2].chosen & got[1])
    return best[0], best[1]


def _carried(model, target):
    """The pcs a ``Carry``-licensed body covers, as the copy would have to carry them."""
    carry = procpass.carry(model)
    if not carry.body(target):
        return frozenset()
    seen, stack = set(), [target]
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(t for t in carry.intra.get(n, ()) if t in carry.intra)
    return frozenset(seen)


def _unhidden_copy(model, target, attempt):
    """``attempt`` run with the body another procedure homes visible, or None.

    A copy binds no pc, so a body the serialization view hid because another
    procedure placed it is still a body this site may carry its own copy of."""
    hidden = getattr(model, "hidden", None)
    free = _carried(model, target) & hidden if hidden else frozenset()
    if not free:
        return None
    model.hidden = hidden - free
    try:
        return attempt(_proc_cfg(model, target))
    finally:
        model.hidden = hidden


def _structure_once(model, entry, allow):
    emitted = set()
    labels = set()
    gate = _Splits(allow)
    sites, calls, static_subs = _dispatch_gates(model)
    plan = procpass.plan(model)
    inline = plan.inline
    play = getattr(model, "play", None)

    def placed(target, callers, sole, site, chain):
        """Region for a callee's body placed at this site, or None where it may not be.

        A sole site owns the body, but only text nobody placed is its own: it shares the
        label bookkeeping, so where its region runs through one already emitted it takes
        a copy first and shares only if that refuses. Every other site copies outright --
        exact where each block may be carried by one (``procpass.Carry``), the region does
        not run through the call line, and the copy binds no label."""
        if target in chain:
            return None
        if not model.variants(target):
            return _unhidden_copy(
                model, target, lambda cfg: copied(target, callers, site, cfg, chain)
            )
        cfg = _proc_cfg(model, target)
        if not sole:
            return copied(target, callers, site, cfg, chain)
        if target in callers or set(cfg[0]) & callers or target in emitted:
            return None
        if set(cfg[0]) & emitted:
            got = copied(target, callers, site, cfg, chain)
            if got is not None:
                return got
        top = _proc(model, target, cfg, (emitted, labels, handler, callee, gate), callers)
        return Region("seq", top) if top else None

    def copied(target, callers, site, cfg, chain):
        """The callee's body as this site's own copy, or None where it may not be."""
        if site in cfg[0] or not procpass.carry(model).body(target):
            return None
        labs = set()
        deep = chain | {target}
        nest = (set(), labs, _copied(handler, deep), _copied(callee, deep), gate)
        top = _proc(model, target, cfg, nest, callers)
        if labs or len(top) != 1 or top[0].kind != "seq" or not top[0].a:
            gate.wanted |= labs - gate.allow  # the pcs a scope would have to free
            return None
        return Region("seq", [_as_copy(r) for r in top[0].a])

    def _copied(place, chain):
        """``place`` inside a copy: its body is a copy too, and never the sole one."""
        return lambda target, site, callers: place(target, site, callers, chain, False)

    def handler(target, site, callers, chain=frozenset(), sole=None):
        """Region for a computed-call handler nested in its dispatch arm, or None:
        a computed goto also lands there, a static sub, or the play entry."""
        if calls.get(target) != sites.get(target) or target in static_subs or target == play:
            return None
        own = sites.get(target) == 1 if sole is None else sole
        return placed(target, callers, own, site, chain)

    def callee(target, site, callers, chain=frozenset(), sole=None):
        """Region for a static callee owned by its call line, or None where the plan
        placed no body at this site.

        A callee plain flow also reaches is owned by that flow, so its sole call site
        takes a copy like any further one would (``Plan.sole`` is the dominance)."""
        ss = inline.get(target, ())
        if site not in ss:
            return None
        own = target in plan.sole and target not in callers if sole is None else sole
        return placed(target, callers, own, site, chain)

    shared = (emitted, labels, handler, callee, gate)
    top = _proc(model, entry, _proc_cfg(model, entry), shared, frozenset())
    return Region("seq", top), labels, gate


def _proc(model, entry, cfg, shared, outer, depth=_SPLIT_DEPTH, stop=None, quota=None):
    """Top regions for one procedure or inlined-handler CFG; ``shared`` carries
    the emitted/label bookkeeping across nested handler regions."""
    nodes, succ, pred = cfg
    emitted, labels, handler, callee, gate = shared
    if not nodes:
        return []
    idom, rpo = _idoms(entry, succ, nodes)
    exits = [n for n in nodes if not succ.get(n)]
    ipdom = _postdoms(nodes, succ, pred, exits)
    nodeset = set(nodes)

    def dominates(a, b):
        n = b
        while True:
            if n == a:
                return True
            p = idom.get(n)
            if p is None or p == n:
                return False
            n = p

    rcset = {  # CNS's RC-set: retreating-edge heads that do not dominate their source
        h
        for s in nodes
        for h in succ.get(s, ())
        if h in nodeset and rpo.get(h, 0) <= rpo.get(s, 0) and not dominates(h, s)
    }

    headers = {}
    for s in nodes:
        for h in succ.get(s, ()):
            if h in nodeset and dominates(h, s):
                headers.setdefault(h, set())
    for h in headers:
        body = {h}
        stack = [s for s in nodes for t in succ.get(s, ()) if t == h and dominates(h, s)]
        while stack:
            n = stack.pop()
            if n not in body:
                body.add(n)
                stack.extend(pred[n])
        headers[h] = body

    def loop_exit(h):
        outs = {t for n in headers[h] for t in succ.get(n, ()) if t not in headers[h]}
        if not outs:
            return None
        ip = ipdom.get(h)
        return ip if ip in outs else min(outs, key=lambda x: rpo.get(x, 1 << 30))

    def owned(child, parent):
        """A successor we may inline: dominated by the branch (sole entry)."""
        return idom.get(child) == parent and child not in emitted

    def claim(child, parent):
        """Inline an unowned side once no other unemitted in-proc pred remains."""
        return (
            child not in emitted
            and child not in headers
            and all(q == parent or q in emitted for q in pred[child])
        )

    def merge_join(targets, loops):
        """Fallback if-continuation when ipdom gives none: an unemitted
        multi-pred arm target (arms fall through; external preds goto it)."""
        skip = {p for hx in loops for p in hx}
        cands = [
            t
            for t in dict.fromkeys(targets)
            if t in nodeset
            and t not in emitted
            and t not in headers
            and t not in skip
            and len(pred[t]) > 1
        ]
        return max(cands, key=lambda t: (len(pred[t]), -rpo.get(t, 0)), default=None)

    def fr(pc):
        return _is_frontier(model, pc)

    def sub(target, site):
        return handler(target, site, nodeset | outer)

    def subc(target, site):
        return callee(target, site, nodeset | outer)

    loop_pcs = set().union(*headers.values()) if headers else set()
    if quota is None:
        quota = [_SPLIT_TOTAL * len(nodes)]

    def dup(pc):
        return None if pc in loop_pcs else _dup_tail(model, pc)

    def split(pc, loops, stop=None):
        """Controlled node splitting: an RC-set node copied into *this*
        predecessor's path, so the multi-entry loop it heads becomes one the
        structurer can nest (Janssen & Corporaal, TOPLAS 19(6) 1997). None
        where the copy is not exact, still binds a pc, or outruns the quota."""
        if depth <= 0 or pc not in rcset or pc == stop or quota[0] <= 0:
            return None
        if depth == _SPLIT_DEPTH and pc not in gate.allow:
            return None
        region = _reachable(pc, succ, nodeset, stop)
        cost = len(region) - (stop in region)
        if cost > _SPLIT_BLOCKS or cost > quota[0]:
            return None
        if any(h in region or x in region for h, x in loops):
            return None
        if not all(_copyable(model, n) for n in region if n != stop):
            return None
        sub_labels = set()
        cut = (
            [n for n in nodes if n in region],
            {n: [] if n == stop else [s for s in succ.get(n, ()) if s in region] for n in region},
            {n: [p for p in pred[n] if p in region] for n in region},
        )
        spent = quota[0]
        quota[0] -= cost
        top = _proc(
            model,
            pc,
            cut,
            (set(), sub_labels, _no_body, _no_body, gate),
            outer | nodeset,
            depth - 1,
            stop,
            quota,
        )
        if sub_labels or len(top) != 1 or top[0].kind != "seq" or not top[0].a:
            quota[0] = spent
            return None
        if depth == _SPLIT_DEPTH:
            gate.chosen.add(pc)
        return [_as_copy(r) for r in top[0].a]

    def stray(pc, loops, seq):
        """Place a copy of ``pc`` here, or label it and go to it (the fallback)."""
        copy = dup(pc)
        if copy is not None and copy[1] is not None:
            seq.extend(copy)
            return
        copy = split(pc, loops)
        if copy is not None:
            seq.extend(copy)
            return
        labels.add(pc)
        seq.append(Region("goto", pc))

    def straddles(anchor, m):
        """Whether a scope from ``anchor`` to ``m`` would swallow a whole loop.

        Such a scope re-nests a cycle rather than the branching that reaches the
        merge, so the label stays and the loop nesting is the one the CFG has."""
        lo, hi = rpo.get(anchor, 0), rpo.get(m, 0)
        return any(lo < rpo.get(h, 0) < hi for h in headers)

    scoped = {}  # anchor pc -> the forward merges it dominates, in RPO order
    for m in sorted(gate.allow & nodeset, key=lambda p: rpo.get(p, 0)):
        anchor = idom.get(m)
        if anchor is None or anchor == m or m in headers or len(pred[m]) < 2:
            continue
        if m not in rcset and not straddles(anchor, m):  # splitting owns the RC-set
            scoped.setdefault(anchor, []).append(m)

    def scopes_at(pc, stop, loops):
        """Merges to place as scopes here: dominated by ``pc``, ahead of ``stop``.

        None inside a cycle: a scope there re-nests a loop's interior, and every
        exit in it would count past the loop it belongs to."""
        if any(h is not None for h, _x in loops):
            return []
        end = rpo.get(stop, 1 << 30) if stop is not None else 1 << 30
        return [m for m in scoped.get(pc, ()) if m not in emitted and rpo.get(m, 0) < end]

    def wrap(pc, mids, stop, loops):
        """``pc``'s region inside one single-trip loop per merge, innermost first.

        Every edge to a merge becomes a levelled ``break``, so a reducible flow
        needs no label (Peterson, Kasami & Tokura, CACM 16(8) 1973)."""
        targets = mids + ([stop] if stop is not None else [])
        frames = [
            loops + [(None, t) for t in reversed(targets[i:])] for i in range(len(targets) + 1)
        ]
        gate.chosen.update(mids)
        region, falls = build_x(pc, targets[0], frames[0])
        for i, tgt in enumerate(targets):
            region = Region("loop", _cat(region, Region("brk", 1) if falls else None))
            falls = True
            nxt = targets[i + 1] if i + 1 < len(targets) else None
            if nxt is not None or stop is None:
                chunk, falls = build_x(tgt, nxt, frames[i + 1])
                region = _cat(region, chunk)
        return region, falls

    def leave(pc, loops):
        """``Region`` for a pc the frame stack already names, innermost first."""
        for lvl, (hdr, ex) in enumerate(reversed(loops), 1):
            if hdr is not None and pc == hdr and pc in emitted:
                return Region("cont", lvl)
            if pc == ex:
                return Region("brk", lvl)
        return None

    def build(pc, stop, loops):
        return build_x(pc, stop, loops)[0]

    def build_x(pc, stop, loops):
        seq = []
        while pc is not None and pc in nodeset and pc != stop:
            out = leave(pc, loops)
            if out is not None:
                seq.append(out)
                return Region("seq", seq), False
            if pc in emitted:
                stray(pc, loops, seq)
                return Region("seq", seq), False
            if pc in headers and not any(pc == h for h, _x in loops):
                ex = loop_exit(pc)
                seq.append(Region("loop", build(pc, None, loops + [(pc, ex)])))
                if ex is None or ex not in nodeset:
                    return Region("seq", seq), False
                pc = ex
                continue
            mids = scopes_at(pc, stop, loops)  # after the header: a loop's merges scope inside it
            if mids:
                region, falls = wrap(pc, mids, stop, loops)
                return _cat(Region("seq", seq), region), falls
            emitted.add(pc)
            variants = model.variants(pc)
            ctx = (
                build,
                owned,
                claim,
                merge_join,
                dup,
                labels,
                nodeset,
                sub,
                subc,
                fr,
                split,
                leave,
            )
            if pc in getattr(model, "dispatch_pcs", ()) and len(variants) > 1:
                join = ipdom.get(pc)
                join = join if join in nodeset else None
                cases = [
                    ("$%02X" % key[1], _case(ctx, model, key, join, loops))
                    for key in sorted(variants)
                ]
                seq.append(Region("switch", ("code[$%04X]" % pc, cases), [pc]))
                if join is None:
                    return Region("seq", seq), False
                pc = join
                continue
            blk = model.blocks[variants[0]]
            seq.append(Region("block", blk, pc))
            extra, nxt = _term_flow(ctx, model, blk, pc, loops, ipdom)
            seq.extend(extra)
            if nxt is None:
                return Region("seq", seq), False
            pc = nxt
        if pc is not None and pc not in nodeset and pc != stop:
            if fr(pc):
                seq.append(Region("frontier", pc))
                return Region("seq", seq), False
            stray(pc, loops, seq)
            return Region("seq", seq), False
        return Region("seq", seq), True

    top = [build(entry, stop, [])]
    # coverage: chain heads first; a serialization view defers foreign-claimable pcs
    deferrable = hasattr(model, "hidden")
    gpred = _static_preds(model) if deferrable else None
    while True:
        pending = sorted(pc for pc in labels if pc in nodeset and pc not in emitted)
        if not pending:
            break
        if deferrable:
            heads = [
                pc
                for pc in pending
                if all(q in emitted or q in model.hidden for q in gpred.get(pc, ()))
            ]
            if not heads:
                break
            top.append(build(heads[0], None, []))
        else:
            heads = [pc for pc in pending if all(q in emitted for q in pred[pc])]
            top.append(build((heads or pending)[0], None, []))
    return top


def _static_preds(model):
    """pc -> static predecessor pcs over every block variant (memoized)."""
    gp = getattr(model, "_static_preds", None)
    if gp is None:
        gp = {}
        for blk in model.blocks.values():
            for s in _succs(model, blk):
                gp.setdefault(s, set()).add(blk.pc)
        model._static_preds = gp
    return gp


def _reachable(pc, succ, nodeset, stop=None):
    """Nodes reachable from ``pc`` inside ``nodeset``, cut at ``stop``.

    Closed under ``succ`` bar ``stop``, so a copy of it leaves only where the
    machine leaves the procedure or where the caller places the continuation."""
    seen, stack = set(), [pc]
    while stack:
        n = stack.pop()
        if n in seen or n not in nodeset:
            continue
        seen.add(n)
        if n != stop:
            stack.extend(succ.get(n, ()))
    return seen


_copyable = procpass.copyable  # a block on its own text; a whole body is procpass.Carry


def _no_body(*_args):
    """Handler/callee inlining, refused: a copy owns no body a primary placed."""
    return None


def _as_copy(region):
    """``region`` with every block leaf marked a duplicate (labelled nowhere)."""
    k = region.kind
    if k == "block":
        return Region("block", region.a, None, region.b if region.b is not None else region.c)
    if k == "seq":
        return Region("seq", [_as_copy(r) for r in region.a])
    if k == "loop":
        return Region("loop", _as_copy(region.a))
    if k == "if":
        return Region("if", region.a, _as_copy(region.b), _as_copy(region.c))
    if k == "call":
        return Region("call", region.a, None if region.b is None else _as_copy(region.b))
    if k == "switch":
        sel, cases = region.a
        arms = [(l, _as_copy(b)) for l, b in cases]
        return Region("switch", (sel, arms), None, region.b or region.c)
    return region


def _dup_tail(model, pc):
    """Duplicable tiny tail block (single variant, <= 3 stores, rts or static
    jump terminator): ``[copy, flow]`` regions with flow None for a jump whose
    continuation the caller must place, else None."""
    variants = getattr(model, "all_variants", model.variants)(pc)
    if len(variants) != 1 or pc in getattr(model, "dispatch_pcs", ()):
        return None
    blk = model.blocks[variants[0]]
    if blk.term[0] not in ("rts", "goto", "jmp") or sum(ev[0] == "st" for ev in blk.events) > 3:
        return None
    copy = Region("block", blk, None, pc)
    if blk.term[0] == "rts":
        return [copy, Region("exit", blk.term)]
    return [copy, None]


def _term_flow(ctx, model, blk, pc, loops, ipdom):
    """Regions + continuation pc for a block's terminator (branch, call, jump-
    table switch, or return)."""
    _build, _owned, _claim, merge_join, _dup, labels, nodeset, sub, subc, fr, _split, _lv = ctx
    term = blk.term
    if term[0] == "br":
        join = ipdom.get(pc)
        join = join if join in nodeset else None
        if term[2] is None:  # dynamic branch target
            if fr(term[3]):
                return [Region("frontier", term[3])], None
            labels.add(term[3])
            return [Region("goto", term[3])], None
        if join is None:
            join = merge_join((term[2], term[3]), loops)
        cond = _inline(term[4], _slotmap(blk))
        then_r = _side(ctx, term[2], join, loops, pc)
        else_r = _side(ctx, term[3], join, loops, pc)
        return [Region("if", (cond, term[1]), then_r, else_r)], join
    if term[0] in ("goto", "jmp"):
        return [], term[1]
    if term[0] == "jsr":
        if term[1] is None:  # computed call: dispatch over the handler set
            targets = model.dyn_targets.get(blk.pcs[-1], [])
            cases = [("$%04X" % t, Region("call", t, sub(t, pc))) for t in targets]
            return [Region("switch", ("call", cases), [])], (term[2] + 1) & 0xFFFF
        body = subc(term[1], pc)
        if body is not None:  # single-site callee: owned region after the call line
            return [Region("call", term[1], body)], (term[2] + 1) & 0xFFFF
        return [], (term[2] + 1) & 0xFFFF
    if term[0] in ("jmpd", "jmpind"):
        targets = _dyn_targets(model, blk)
        cases = []
        for t in targets:
            arm = _side(ctx, t, None, loops, pc)
            cases.append(("$%04X" % t, arm))
        return [Region("switch", ("goto", cases), [])], None
    return [Region("exit", term)], None


def _case(ctx, model, key, join, loops):
    """One arm of an opcode-dispatch switch: the variant's body then its flow."""
    nodeset = ctx[6]
    blk = model.blocks[key]
    seq = [Region("block", blk, None)]
    extra, nxt = _term_flow(ctx, model, blk, key[0], loops, {key[0]: join})
    seq.extend(extra)
    if nxt is not None and nxt != join and nxt in nodeset:
        seq.append(_side(ctx, nxt, join, loops, key[0]))
    return Region("seq", seq)


# ---- comparison-chain -> switch (note/command dispatch) -----------------------
def _norm_eq(cond):
    """``(subject, const)`` for an equality test, normalising the CMP idiom
    ``(subject +/- k) == 0`` (subtract-then-test-zero) back to ``subject == c``."""
    a, b = cond[2]
    if b[0] == "const" and a[0] != "const":
        subj, const = a, b[1]
    elif a[0] == "const" and b[0] != "const":
        subj, const = b, a[1]
    else:
        return None
    if const == 0 and subj[0] == "op" and subj[1] in ("INT_ADD", "INT_SUB"):
        kids = subj[2]
        consts = [c for c in kids if c[0] == "const"]
        rest = [c for c in kids if c[0] != "const"]
        if len(consts) == 1 and len(rest) == 1:
            k = consts[0][1]
            base = rest[0]
            const = k if subj[1] == "INT_SUB" else (-k) & E.mask(subj[3])
            return base, const
    return subj, const


def _eq_case(region):
    """Normalise an equality test to ``(subject, const, case_body, continuation)``
    where ``case_body`` runs when ``subject == const`` (any op/polarity)."""
    if region is None or region.kind != "if":
        return None
    cond, pol = region.a
    if cond[0] != "op" or cond[1] not in ("INT_EQUAL", "INT_NOTEQUAL"):
        return None
    sc = _norm_eq(cond)
    if sc is None:
        return None
    subj, const = sc
    eq_is_then = (cond[1] == "INT_EQUAL") == bool(pol)  # does subject==const take `then`?
    if eq_is_then:
        return subj, const, region.b, region.c
    return subj, const, region.c, region.b


def _peel_else(els):
    """Skip statement-less comparison blocks, returning ``(skipped_pcs, next_if)``
    when the else reduces to a single trailing ``if``, else ``next_if`` is None."""
    if els is None:
        return [], None
    items = els.a if els.kind == "seq" else [els]
    pcs = []
    i = 0
    while i < len(items) and items[i].kind == "block" and not _has_stmts(items[i]):
        if items[i].b is not None:
            pcs.append(items[i].b)
        i += 1
    rest = items[i:]
    if len(rest) == 1 and rest[0].kind == "if":
        return pcs, rest[0]
    return pcs, None


def _switchify(region):
    """Rewrite a same-subject equality chain into one ``switch`` (note/command
    dispatch); recurse everywhere else. Every subsumed block pc is preserved."""
    if region is None:
        return None
    k = region.kind
    if k == "seq":
        return Region("seq", [_switchify(r) for r in region.a])
    if k == "loop":
        return Region("loop", _switchify(region.a))
    if k == "switch":
        sel, cases = region.a
        return Region("switch", (sel, [(l, _switchify(b)) for l, b in cases]), region.b)
    if k == "call":
        return region if region.b is None else Region("call", region.a, _switchify(region.b))
    if k != "if":
        return region
    first = _eq_case(region)
    if first is None:
        return Region("if", region.a, _switchify(region.b), _switchify(region.c))
    subj = first[0]
    cases = [("$%02X" % first[1], _switchify(first[2]))]
    subsumed = []
    els = first[3]
    while True:
        pcs, nxt = _peel_else(els)
        ec = _eq_case(nxt) if nxt is not None else None
        if ec is None or ec[0] != subj:
            default = _switchify(els)
            break
        subsumed += pcs
        cases.append(("$%02X" % ec[1], _switchify(ec[2])))
        els = ec[3]
    if len(cases) < 2:
        return Region("if", region.a, _switchify(region.b), _switchify(region.c))
    cases.append(("default", default))
    return Region("switch", (fmt(subj), cases), subsumed)


def _has_stmts(region):
    """Whether a region emits any visible statement (for empty-arm collapse)."""
    if region is None:
        return False
    k = region.kind
    if k == "seq":
        return any(_has_stmts(r) for r in region.a)
    if k == "block":
        return bool(_block_stmts(region.a)) or region.a.term[0] == "jsr"
    if k == "switch":
        return True
    return k in ("loop", "if", "goto", "cont", "brk", "exit", "call")


def _side(ctx, target, join, loops, parent):
    """A conditional arm: continue/break for loop edges, an inlined region when
    owned or last-claimable, a duplicated tiny tail, a node split, else a
    labelled goto."""
    build, owned, claim, _mj, dup, labels, nodeset, _sub, _subc, fr, split, leave = ctx
    if target == join:
        return Region("seq", [])
    out = leave(target, loops)
    if out is not None:
        return Region("seq", [out])
    if target in nodeset and (owned(target, parent) or claim(target, parent)):
        return build(target, join, loops)
    copy = dup(target)
    if copy is not None:
        if copy[1] is not None:
            return Region("seq", copy)
        nxt = copy[0].a.term[1]  # jump tail: its continuation must place here
        if nxt == join:
            return Region("seq", copy[:1])
        out = leave(nxt, loops)
        if out is not None:
            return Region("seq", copy[:1] + [out])
    if fr(target):
        return Region("seq", [Region("frontier", target)])
    copy = split(target, loops, join)
    if copy is not None:
        return Region("seq", copy)
    labels.add(target)
    return Region("seq", [Region("goto", target)])


# ---- readable emission --------------------------------------------------------
_NEG = {"==": "!=", "!=": "==", "<": ">=", "<=": ">"}


def _cond_text(cond, pol):
    """Readable condition true when the branch is taken (flag == ``pol``)."""
    if cond[0] == "op" and cond[1] in ("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL"):
        op = _INFIX[cond[1]]
        if not pol:
            op = _NEG[op]
        return "%s %s %s" % (_paren(cond[2][0]), op, _paren(cond[2][1]))
    txt = fmt(cond)
    return "%s != 0" % txt if pol else "%s == 0" % txt


def _block_stmts(blk):
    """Observable statements of a block: named memory stores (register/flag
    bookkeeping and cycle markers are elided; the branch renders its own cond)."""
    smap = _slotmap(blk)
    out = []
    for ev in blk.events:
        if ev[0] != "st":
            continue
        addr, val = _inline(ev[1], smap), _inline(ev[2], smap)
        out.append("%s = %s" % (_mem_text(addr), fmt(val)))
    return out


def _emit(region, model, lines, depth, labels):
    pad = "    " * depth
    k = region.kind
    if k == "seq":
        for r in region.a:
            _emit(r, model, lines, depth, labels)
    elif k == "block":
        if region.b in labels:
            lines.append("  " * depth + "L_%04X:" % region.b)
        for s in _block_stmts(region.a):
            lines.append(pad + s)
        term = region.a.term
        if term[0] == "jsr" and term[1] is not None:
            lines.append("%scall sub_%04X()" % (pad, term[1]))
    elif k == "loop":
        lines.append(pad + "loop {")
        _emit(region.a, model, lines, depth + 1, labels)
        lines.append(pad + "}")
    elif k == "if":
        cond, pol = region.a
        then_empty = not _has_stmts(region.b)
        else_empty = region.c is None or not _has_stmts(region.c)
        if E.is_const(cond):  # folded condition: emit only the taken side
            taken = region.b if (cond[1] != 0) == bool(pol) else region.c
            _emit(taken or Region("seq", []), model, lines, depth, labels)
        elif then_empty and not else_empty:  # collapse empty then
            lines.append("%sif %s {" % (pad, _cond_text(cond, 1 - pol)))
            _emit(region.c, model, lines, depth + 1, labels)
            lines.append(pad + "}")
        else:
            lines.append("%sif %s {" % (pad, _cond_text(cond, pol)))
            _emit(region.b, model, lines, depth + 1, labels)
            if not else_empty:
                lines.append(pad + "} else {")
                _emit(region.c, model, lines, depth + 1, labels)
            lines.append(pad + "}")
    elif k == "switch":
        sel, cases = region.a
        if sel == "call":  # computed call: nested handler arms + bare handler subs
            bare = [body.a for _lbl, body in cases if body.b is None]
            bodied = [body for _lbl, body in cases if body.b is not None]
            if not bodied:
                lines.append(
                    "%scall one of { %s }" % (pad, ", ".join("sub_%04X" % t for t in bare))
                )
            else:
                lines.append(pad + "switch call {")
                for body in bodied:
                    lines.append("%s    case sub_%04X:" % (pad, body.a))
                    _emit(body.b, model, lines, depth + 2, labels)
                if bare:
                    lines.append(
                        "%s    default: one of { %s }"
                        % (pad, ", ".join("sub_%04X" % t for t in bare))
                    )
                lines.append(pad + "}")
        else:
            for pc in region.b or ():  # subsumed comparison-block labels, if referenced
                if pc in labels:
                    lines.append("  " * depth + "L_%04X:" % pc)
            head = "switch %s {" % ("(computed goto)" if sel == "goto" else sel)
            lines.append(pad + head)
            for label, body in cases:
                if label == "default" and not _has_stmts(body):
                    continue
                lines.append(
                    "%s    %s" % (pad, "default:" if label == "default" else "case %s:" % label)
                )
                _emit(body, model, lines, depth + 2, labels)
            lines.append(pad + "}")
    elif k == "call":
        if region.b is None:
            lines.append("%scall sub_%04X()" % (pad, region.a))
        else:  # the block's call line precedes; the owned body nests after it
            lines.append("%sinlined sub_%04X {" % (pad, region.a))
            _emit(region.b, model, lines, depth + 1, labels)
            lines.append(pad + "}")
    elif k in ("cont", "brk"):
        n = region.a or 1
        lines.append(pad + ("continue" if k == "cont" else "break") + ("" if n == 1 else " %d" % n))
    elif k == "goto":
        lines.append("%sgoto L_%04X" % (pad, region.a))
    elif k == "frontier":
        lines.append("%sunobserved $%04X" % (pad, region.a))
    elif k == "exit":
        lines.append(pad + ("return" if region.a[0] == "rts" else "dispatch %s" % region.a[0]))


def _procedures(model):
    entries = [("play", model.play)]
    for blk in model.blocks.values():
        if blk.term[0] == "jsr" and blk.term[1] is not None:
            entries.append(("sub_%04X" % blk.term[1], blk.term[1]))
    seen = set()
    out = []
    for name, pc in entries:
        if pc not in seen:
            seen.add(pc)
            out.append((name, pc))
    return out


def render(model):
    """Readable structured pseudocode for the whole program."""
    head = "; structured view of $%04X (play) -- the frame program is the artifact" % model.play
    lines = [head]
    for name, pc in _procedures(model):
        root, labels = _structure(model, pc)
        root = _switchify(root)
        body = []
        _emit(root, model, body, 1, labels)
        lines.append("")
        lines.append("%s $%04X {" % (name, pc))
        lines.extend(body or ["    return"])
        lines.append("}")
    return "\n".join(lines) + "\n"
