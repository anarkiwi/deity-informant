"""Structured readable rendering of a decompiled playroutine (plan P5).

Nests the verified block/edge model into ``if``/``loop`` regions over named
state -- a human-facing view of the same model the walker replays byte-exact
(the exact executable artifact stays the SIDC text, :mod:`deity_informant.stext`).
"""

from __future__ import annotations

from . import expr as E

# ---- named machine state ------------------------------------------------------
_VOICE = ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "ctrl", "attack_decay", "sustain_release")
_FILTER = {
    0xD415: "filter.cutoff_lo",
    0xD416: "filter.cutoff_hi",
    0xD417: "filter.resonance",
    0xD418: "filter.mode_vol",
}


def _sid_name(addr):
    if 0xD400 <= addr <= 0xD414:
        v, r = divmod(addr - 0xD400, 7)
        return "sid.v%d.%s" % (v + 1, _VOICE[r])
    return _FILTER.get(addr)


def name_addr(addr):
    """Readable name for a concrete memory address."""
    sid = _sid_name(addr)
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
def _succs(model, blk):
    t = blk.term
    if t[0] in ("goto", "jmp"):
        return [t[1]]
    if t[0] == "br":
        return ([t[2]] if t[2] is not None else []) + [t[3]]
    if t[0] == "jsr":
        return [(t[2] + 1) & 0xFFFF]
    return []  # rts / computed transfer: procedure exit for structuring


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
        for s in _succs(model, model.blocks[model.variants(pc)[0]]):
            if rep(s) is not None:
                outs.append(s)
                stack.append(s)
        succ[pc] = outs
    pred = {pc: [] for pc in nodes}
    for pc in nodes:
        for s in succ[pc]:
            pred[s].append(pc)
    return nodes, succ, pred


def _postorder(entry, succ):
    order = []
    seen = {entry}
    stack = [(entry, iter(succ.get(entry, ())))]
    while stack:
        node, it = stack[-1]
        nxt = next(it, None)
        if nxt is None:
            order.append(node)
            stack.pop()
        elif nxt not in seen:
            seen.add(nxt)
            stack.append((nxt, iter(succ.get(nxt, ()))))
    return order


def _idoms(entry, succ, nodes):
    po = _postorder(entry, succ)
    rpo_num = {n: i for i, n in enumerate(reversed(po))}
    pred = {n: [] for n in nodes}
    for n in nodes:
        for s in succ.get(n, ()):
            if s in pred:
                pred[s].append(n)
    idom = {entry: entry}
    order = [n for n in reversed(po) if n != entry]
    changed = True
    while changed:
        changed = False
        for n in order:
            ps = [p for p in pred[n] if p in idom]
            if not ps:
                continue
            new = ps[0]
            for p in ps[1:]:
                a, b = new, p
                while a != b:
                    while rpo_num[a] > rpo_num[b]:
                        a = idom[a]
                    while rpo_num[b] > rpo_num[a]:
                        b = idom[b]
                new = a
            if idom.get(n) != new:
                idom[n] = new
                changed = True
    return idom, rpo_num


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
        self.kind = kind  # block | seq | if | loop | goto | brk | cont | exit
        self.a = a
        self.b = b
        self.c = c


def _structure(model, entry):
    nodes, succ, pred = _proc_cfg(model, entry)
    if not nodes:
        return Region("seq", []), set()
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
        return min(outs, key=lambda x: rpo.get(x, 1 << 30)) if outs else None

    emitted = set()
    labels = set()

    def owned(child, parent):
        """A successor we may inline: dominated by the branch (sole entry)."""
        return idom.get(child) == parent and child not in emitted

    def build(pc, stop, loops):
        seq = []
        while pc is not None and pc in nodeset and pc != stop:
            if loops and pc == loops[-1][0] and pc in emitted:
                seq.append(Region("cont"))
                return Region("seq", seq)
            if loops and pc == loops[-1][1]:
                seq.append(Region("brk"))
                return Region("seq", seq)
            if pc in emitted:
                labels.add(pc)
                seq.append(Region("goto", pc))
                return Region("seq", seq)
            if pc in headers and (not loops or loops[-1][0] != pc):
                ex = loop_exit(pc)
                seq.append(Region("loop", build(pc, None, loops + [(pc, ex)])))
                if ex is None or ex not in nodeset:
                    return Region("seq", seq)
                pc = ex
                continue
            emitted.add(pc)
            blk = model.blocks[model.variants(pc)[0]]
            seq.append(Region("block", blk, pc))
            term = blk.term
            if term[0] == "br":
                join = ipdom.get(pc)
                join = join if join in nodeset else None
                t_pc, f_pc = term[2], term[3]
                if t_pc is None:
                    labels.add(term[3])
                    seq.append(Region("goto", term[3]))
                    return Region("seq", seq)
                then_r = _side(build, t_pc, join, loops, owned, pc, labels)
                else_r = _side(build, f_pc, join, loops, owned, pc, labels)
                cond = _inline(term[4], _slotmap(blk))
                seq.append(Region("if", (cond, term[1]), then_r, else_r))
                pc = join
            elif term[0] in ("goto", "jmp"):
                pc = term[1]
            elif term[0] == "jsr":
                pc = (term[2] + 1) & 0xFFFF
            else:
                seq.append(Region("exit", term))
                return Region("seq", seq)
        if pc is not None and pc not in nodeset and pc != stop:
            labels.add(pc)
            seq.append(Region("goto", pc))
        return Region("seq", seq)

    top = [build(entry, None, [])]
    # every reachable block must appear: emit labelled targets not yet covered
    while True:
        pending = [pc for pc in labels if pc in nodeset and pc not in emitted]
        if not pending:
            break
        for pc in sorted(pending):
            if pc not in emitted:
                top.append(build(pc, None, []))
    return Region("seq", top), labels


def _has_stmts(region):
    """Whether a region emits any visible statement (for empty-arm collapse)."""
    if region is None:
        return False
    k = region.kind
    if k == "seq":
        return any(_has_stmts(r) for r in region.a)
    if k == "block":
        return bool(_block_stmts(region.a)) or region.a.term[0] == "jsr"
    return k in ("loop", "if", "goto", "cont", "brk", "exit")


def _side(build, target, join, loops, owned, parent, labels):
    """A conditional arm: continue/break for loop edges, an inlined single-entry
    region when owned, else a goto to a labelled block."""
    if target == join:
        return Region("seq", [])
    if loops and target == loops[-1][0]:
        return Region("seq", [Region("cont")])
    if loops and target == loops[-1][1]:
        return Region("seq", [Region("brk")])
    if owned(target, parent):
        return build(target, join, loops)
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
        if term[0] == "jsr":
            lines.append("%scall sub_%04X()" % (pad, term[1] if term[1] is not None else 0))
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
    elif k == "cont":
        lines.append(pad + "continue")
    elif k == "brk":
        lines.append(pad + "break")
    elif k == "goto":
        lines.append("%sgoto L_%04X" % (pad, region.a))
    elif k == "exit":
        lines.append(pad + ("return" if region.a[0] == "rts" else "dispatch %s" % region.a[0]))


def _procedures(model):
    entries = [("play", model.play), ("init", model.init)]
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
    head = "; structured view of $%04X (play) -- see .sidc for the exact program" % model.play
    lines = [head]
    for name, pc in _procedures(model):
        root, labels = _structure(model, pc)
        body = []
        _emit(root, model, body, 1, labels)
        lines.append("")
        lines.append("%s $%04X {" % (name, pc))
        lines.extend(body or ["    return"])
        lines.append("}")
    return "\n".join(lines) + "\n"
