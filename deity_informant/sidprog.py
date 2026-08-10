"""The model machinery the emitted artifact is built from.

``_model_trees`` structures a model into region trees, ``_stmt_view`` is the
emit-side block cleanup, and the ``_*_lines`` helpers serialize the header
sections; :mod:`deity_informant.frameprog` is the one consumer that emits text.
"""

from __future__ import annotations

import re

from . import codec
from . import expr as E
from . import grammar as G
from . import procpass
from . import structured as C
from .render import _static_preds

# the grammar layer owns the name/address bijection and the region shape
_kids = G.kids
_rebuild = G.rebuild
_map_term = G.map_term
_addr_name = G.addr_name
_name_addr = G.name_addr
_check_alias = G.check_alias

_CHAINS = {"INT_OR": "|", "INT_XOR": "^", "INT_AND": "&"}
_BINS = {"INT_LEFT": "<<", "INT_RIGHT": ">>"}
_CMPS = {"INT_EQUAL": "==", "INT_NOTEQUAL": "!=", "INT_LESS": "<", "INT_LESSEQUAL": "<="}


def _split_index(addr):
    """``(base, reg)`` iff ``addr`` is the canonical zext2(reg) + const2 >= $100."""
    if addr[0] == "op" and addr[1] == "INT_ADD" and addr[3] == 2 and len(addr[2]) == 2:
        idx, base = addr[2]
        base_ok = base[0] == "const" and base[2] == 2 and base[1] >= 0x100
        idx_ok = idx[0] == "op" and idx[1] == "INT_ZEXT" and idx[3] == 2
        if base_ok and idx_ok and idx[2][0][0] == "reg":
            return base[1], idx[2][0][1]
    return None


# ---- shared literal spelling ---------------------------------------------------
def _hex(v, sz):
    return "$%0*X" % (2 * sz, v)


def _wsuf(sz):
    return "" if sz == 1 else ":%d" % sz


def _term_exprs(term):
    k = term[0]
    if k == "br":
        return [term[4]] if term[5] is None else [term[4], term[5]]
    if k == "jmpd":
        return [term[1]]
    if k == "jmpind":
        return [] if term[2] is None else [term[2]]
    if k == "jsr":
        return [] if term[3] is None else [term[3]]
    return []


# ---- emit-side statement cleanup (single-use load inlining, canonical conds) ---
_VOLS = C._VOL | C._VOL0


def _canon_cond(n):
    """``(a - b) == $00`` -> ``a == b`` and ``(a + k) == $00`` -> ``a == -k``
    (also ``!=``), equal widths only; walker-equivalent direct compares."""
    if n[0] != "op" or n[1] not in ("INT_EQUAL", "INT_NOTEQUAL"):
        return n
    lhs, rhs = n[2]
    if lhs[0] != "op" or rhs != ("const", 0, lhs[3]):
        return n
    sz = lhs[3]
    if lhs[1] == "INT_SUB" and E.width(lhs[2][0]) == sz and E.width(lhs[2][1]) == sz:
        return ("op", n[1], lhs[2], n[3])
    if lhs[1] == "INT_ADD" and len(lhs[2]) == 2:
        a, k = lhs[2]
        if k[0] == "const" and k[2] == sz and E.width(a) == sz and (-k[1]) & E.mask(sz):
            return ("op", n[1], (a, ("const", (-k[1]) & E.mask(sz), sz)), n[3])
    return n


def _bytev(n):
    """True iff ``n`` provably evaluates to one byte under ``compile_block``."""
    while n[0] == "op" and n[1] == "INT_ZEXT":
        n = n[2][0]
    if n[0] == "loc":
        return len(n) == 2  # the ONE local-width rule: a bare local is one byte
    if n[0] != "op":
        return E.width(n) == 1
    if n[1] in ("INT_ADD", "INT_SUB", "INT_LEFT"):
        return n[3] == 1  # masked to their width at evaluation
    if n[1] == "INT_RIGHT":
        return _bytev(n[2][0])
    if n[1] == "INT_CARRY" or n[1] in _CMPS:
        return True
    return all(_bytev(k) for k in n[2])  # AND/OR/XOR: closed over byte operands


def _ld_safe(addr):
    """True iff every run-time address of this load avoids the volatile cells."""
    if addr[0] == "const":
        return addr[1] & 0xFFFF not in _VOLS
    if addr[0] == "op" and addr[1] == "INT_ADD" and addr[3] == 2 and len(addr[2]) == 2:
        a, b = addr[2]
        base, idx = (a, b) if a[0] == "const" else (b, a)
        if base[0] == "const" and idx[0] != "const" and _bytev(idx):
            return all((v - base[1]) & 0xFFFF > 0xFF for v in _VOLS)
    return False


def _uni_refs(n, memo):
    """slot -> [paths, wide-ref] reference multiplicities over the DAG of ``n``."""
    if id(n) not in memo:
        stack = [(n, False)]
        while stack:
            x, done = stack.pop()
            if id(x) in memo:
                continue
            if x[0] == "uni":
                memo[id(x)] = {x[1]: [1, x[2] != 1]}
            elif not done:
                stack.append((x, True))
                stack.extend((k, False) for k in _kids(x))
            else:
                acc = {}
                for k in _kids(x):
                    for s, (c, w) in memo[id(k)].items():
                        cur = acc.setdefault(s, [0, False])
                        cur[0] += c
                        cur[1] |= w
                memo[id(x)] = acc
    return memo[id(n)]


def _pos_roots(events, term, regs):
    out = []
    for i, ev in enumerate(events):
        if ev[0] == "ld":
            out.append((i, ev[2]))
        elif ev[0] == "st":
            out.extend(((i, ev[1]), (i, ev[2])))
        elif ev[0] == "pen":
            out.extend(((i, ev[2]), (i, ev[3])))
    end = len(events)
    out.extend((end, r) for i, r in enumerate(regs) if r != ("reg", i))
    out.extend((end, x) for x in _term_exprs(term))
    return out


def _inline_pick(events, term, regs):
    """(index, slot) of the first single-use load safe to inline, else None."""
    defs = {}
    for i, ev in enumerate(events):
        if ev[0] == "ld":
            defs[ev[1]] = None if ev[1] in defs else i
    uses = {}
    memo = {}
    for pos, x in _pos_roots(events, term, regs):
        for s, (c, w) in _uni_refs(x, memo).items():
            u = uses.setdefault(s, [0, -1, False])
            u[0] += c
            u[1] = pos
            u[2] |= w
    for i, ev in enumerate(events):
        if ev[0] != "ld" or defs[ev[1]] != i:
            continue
        u = uses.get(ev[1])
        if u is None or u[0] != 1 or u[2] or u[1] <= i or not _ld_safe(ev[2]):
            continue
        span = events[i + 1 : u[1]]
        if any(e[0] == "st" for e in span):
            continue  # the moved read must not cross any store
        moved = set(_uni_refs(ev[2], memo))
        if any(e[0] == "ld" and e[1] in moved for e in span):
            continue  # nor a redefinition of a slot its address reads
        return i, ev[1]
    return None


def _subst_slot(n, slot, repl, memo):
    stack = [n]
    while stack:
        x = stack[-1]
        if id(x) in memo:
            stack.pop()
            continue
        if x == ("uni", slot, 1):
            memo[id(x)] = repl
            stack.pop()
            continue
        todo = [k for k in _kids(x) if id(k) not in memo]
        if todo:
            stack.extend(todo)
            continue
        stack.pop()
        memo[id(x)] = _rebuild(x, [memo[id(k)] for k in _kids(x)])
    return memo[id(n)]


def _apply_inline(events, term, regs, i):
    repl = ("mem", events[i][2], 1)
    slot = events[i][1]
    memo = {}

    def sub(x):
        return _subst_slot(x, slot, repl, memo)

    del events[i]
    for j, ev in enumerate(events):
        if ev[0] == "ld":
            events[j] = ("ld", ev[1], sub(ev[2]))
        elif ev[0] == "st":
            events[j] = ("st", sub(ev[1]), sub(ev[2]))
        elif ev[0] == "pen":
            events[j] = ("pen", ev[1], sub(ev[2]), sub(ev[3]))
    return events, _map_term(term, sub), [sub(r) for r in regs]


def _stmt_view(blk):
    """Emit-side copy of a block with canonical branch conditions and every
    single-use non-volatile load inlined at its use site (docs section on
    statement sugar); the model block is never mutated."""
    term = blk.term
    if term[0] == "br":
        cond = _canon_cond(term[4])
        if cond is not term[4]:
            term = term[:4] + (cond, term[5])
    events, regs = list(blk.events), list(blk.regs)
    changed = False
    pick = _inline_pick(events, term, regs)
    while pick is not None:
        events, term, regs = _apply_inline(events, term, regs, pick[0])
        changed = True
        pick = _inline_pick(events, term, regs)
    if not changed and term is blk.term:
        return blk
    return C.Block(blk.pc, blk.op0, blk.pcs, events, term, regs)


# ---- header sections ------------------------------------------------------------
def _image_lines(mem0, cov):
    """``image { .. }``: runs of nonzero bytes outside the declared data
    regions (``cov`` marks carved addresses), 16 per row, packed hex pairs."""
    out = ["image {"]
    row = []
    for a in range(0x10000):
        if mem0[a] and not cov[a]:
            if row and (a != row[0] + len(row) - 1 or len(row) - 1 >= 16):
                out.append(" $%04X: %s" % (row[0], "".join(row[1:])))
                row = []
            if not row:
                row = [a]
            row.append("%02X" % mem0[a])
    if row:
        out.append(" $%04X: %s" % (row[0], "".join(row[1:])))
    out.append("}")
    return out


# ---- data + symbols sections (song-data declarations, role aliases) -------------
def _decl_attrs(d):
    parts = []
    if d["stride"] > 1:
        parts.append("stride %d" % d["stride"])
    if d.get("mut"):
        parts.append("mut " + " ".join("%d" % o for o in d["mut"]))
    parts.extend("+%s" % _addr_name(b) for b in d["cobases"])
    if d["role"] is not None:
        parts.append("%s %s" % (d["role"][0], _addr_name(d["role"][1])))
    if d["via"] is not None:
        parts.append("via %s" % _addr_name(d["via"]))
    if d["targets"] is not None:
        parts.append("-> $%04X..$%04X" % d["targets"])
    if d["cmp"]:
        parts.append("cmp " + " ".join("$%02X" % v for v in d["cmp"]))
    if d["dispatch"]:
        parts.append("dispatch " + " ".join("$%04X" % v for v in d["dispatch"]))
    if d["observed"]:
        parts.append("observed")
    return "".join(" " + p for p in parts)


def _data_lines(decls, mem0):
    """``data { .. }`` plus the carved-address mask; declarations partition
    their regions out of the image (asserted disjoint, bytes attached)."""
    cov = bytearray(0x10000)
    if not decls:
        return [], cov
    out = ["data {"]
    end = 0
    for d in decls:
        assert end <= d["base"] and len(d["data"]) == d["size"] > 0
        end = d["base"] + d["size"]
        assert bytes(mem0[d["base"] : end]) == d["data"]
        cov[d["base"] : end] = b"\x01" * d["size"]
        out.append(" %s %s[%d]%s:" % (d["kind"], _addr_name(d["base"]), d["size"], _decl_attrs(d)))
        out.extend("  " + d["data"][k : k + 16].hex().upper() for k in range(0, d["size"], 16))
    out.append("}")
    return out, cov


def _symbol_lines(aliases):
    if not aliases:
        return []
    out = ["symbols {"]
    out.extend(" alias %s = %s" % (aliases[c], _addr_name(c)) for c in sorted(aliases))
    out.append("}")
    return out


def _alias_sub(aliases):
    """Body-line cell-name substitution for the {cell: alias} table, else None;
    the table is the only mapping (strict bijection, checked here)."""
    if not aliases:
        return None
    fwd = {_addr_name(cell): _check_alias(name) for cell, name in aliases.items()}
    if len(set(fwd.values())) != len(fwd):
        raise ValueError("alias table is not a bijection")
    pat = re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, fwd)))
    return lambda s: pat.sub(lambda m: fwd[m.group(0)], s)


def _items(region):
    return region.a if region.kind == "seq" else [region]


class _SortedView:
    """Model facade with canonical (sorted) variant order for structuring;
    ``hidden`` pcs (already serialized by an earlier proc) become goto labels
    and unkept blocks drop to the evidence frontier (keep rule: ``_kept_pcs``)."""

    def __init__(self, model):
        self.mem0 = model.mem0
        self.play = getattr(model, "play", None)
        self.dyn_targets = model.dyn_targets
        self.dispatch_pcs = set(model.dispatch_sets)
        self.hidden = set()
        kept, self.need = _kept_pcs(model)
        self.blocks = {key: blk for key, blk in model.blocks.items() if key[0] in kept}
        by_pc = {}
        for key in sorted(self.blocks):
            by_pc.setdefault(key[0], []).append(key)
        self._by_pc = by_pc

    def variants(self, pc):
        return () if pc in self.hidden else self._by_pc.get(pc, ())

    def all_variants(self, pc):
        return self._by_pc.get(pc, ())


def _left_entry(view, left):
    """Next leftover-fragment entry: prefer a chain head (no unserialized pred)."""
    gp = _static_preds(view)
    for key in left:
        if all(q in view.hidden for q in gp.get(key[0], ())):
            return key[0]
    return left[0][0]


def _kept_pcs(model):
    """``(kept pcs, dynamic-landing need)`` of the serialization keep rule:
    evidence-executed pcs plus the dynamic-landing closure over kept blocks;
    every other block's edges serialize as ``unobserved`` frontier markers."""
    all_pcs = {key[0] for key in model.blocks}
    pcs = getattr(model, "pcs", None)
    if pcs is None:
        return all_pcs, _need_pcs(model, model.blocks.values())
    kept = {pc for pc in all_pcs if pc in pcs or pc in model.dispatch_sets}
    while True:
        need = _need_pcs(model, (b for k, b in model.blocks.items() if k[0] in kept))
        grow = (need & all_pcs) - kept
        if not grow:
            return kept, need
        kept |= grow


def _need_pcs(model, blocks):
    """Pcs dynamic control can land on at run time (must stay resolvable)."""
    blocks = list(blocks)
    need = set()
    rets = {(b.term[2] + 1) & 0xFFFF for b in blocks if b.term[0] == "jsr"}
    ev_tg = getattr(model, "ev_targets", {})
    for blk in blocks:
        t = blk.term
        site = blk.pcs[-1]
        if t[0] == "jsr" and t[1] is not None:
            need.add(t[1])
        elif t[0] in ("jsr", "jmpd", "jmpind") or (t[0] == "br" and t[5] is not None):
            need.update(model.dyn_targets.get(site, ()))
        elif t[0] == "rts":  # observed RTS-trick landings (call returns excluded)
            need.update(set(ev_tg.get(site, ())) - rets)
    return need


def _tree_keys(root, dups=None):
    """Primary block keys a region tree serializes (dispatch and call arms
    included); duplicate-copy keys collect into ``dups`` instead."""
    keys = set()
    stack = [root]
    while stack:
        r = stack.pop()
        k = r.kind
        if k == "block":
            if r.b is None and r.c is not None:
                if dups is not None:
                    dups.add((r.a.pc, r.a.op0))
            else:
                keys.add((r.a.pc, r.a.op0))
        elif k == "seq":
            stack.extend(r.a)
        elif k == "loop":
            stack.append(r.a)
        elif k == "if":
            stack.extend(c for c in (r.b, r.c) if c is not None)
        elif k == "call":
            if r.b is not None:
                stack.append(r.b)
        elif k == "switch":
            for _lbl, body in r.a[1]:
                if body is None:
                    continue
                if body.kind == "call":
                    if body.b is not None:
                        stack.append(body.b)
                else:
                    stack.append(body)
    return keys


def _inlined_arms(trees):
    """Call targets whose body tree is inlined at a call line or case arm."""
    out = set()
    stack = [root for _e, root in trees]
    while stack:
        r = stack.pop()
        k = r.kind
        if k == "seq":
            stack.extend(r.a)
        elif k == "loop":
            stack.append(r.a)
        elif k == "if":
            stack.extend(c for c in (r.b, r.c) if c is not None)
        elif k == "call":
            if r.b is not None:
                out.add(r.a)
                stack.append(r.b)
        elif k == "switch":
            for _lbl, body in r.a[1]:
                if body is None:
                    continue
                if body.kind == "call":
                    if body.b is not None:
                        out.add(body.a)
                        stack.append(body.b)
                else:
                    stack.append(body)
    return out


def _model_trees(model):
    """``(trees, labels, view)``: region trees for every planned procedure
    (block homes per :mod:`procpass`, leftovers as fragment procs) and the
    full label set (goto + dynamic-branch targets; proc entries need no label)."""
    view = _SortedView(model)
    plan = procpass.plan(view)
    by_home = {}
    for pc, home in plan.homes.items():
        by_home.setdefault(home, set()).add(pc)
    homed = set(plan.homes)
    trees = []
    labels = set()
    done = set()
    dupped = set()
    owners = set()  # proc entries whose own tree serializes their entry block
    placed = set()
    need = view.need  # dynamic landings must resolve at run time

    def build(entry, foreign):
        view.hidden = foreign | placed
        root, labs = codec.structure(view, entry)
        keys = _tree_keys(root, dupped)
        if not keys and not _items(root):
            return  # fully inlined or blockless entry: nothing to serialize
        labels.update(labs)
        if any(k[0] == entry for k in keys):
            owners.add(entry)
        done.update(keys)
        placed.update(k[0] for k in keys)
        trees.append((entry, root))

    def left_keys():
        return sorted(
            k
            for k in view.blocks
            if k not in done and not (k in dupped and k[0] not in need and k[0] not in labels)
        )

    for entry in plan.entries:
        build(entry, homed - by_home.get(entry, set()))
    left = left_keys()
    while left:
        view.hidden = set(placed)
        build(_left_entry(view, left), set())
        still = left_keys()
        if len(still) == len(left):
            raise ValueError("blocks unreachable from any procedure: %s" % still[:4])
        left = still
    labels |= (need & set(view._by_pc)) - _inlined_arms(trees)
    return trees, labels - owners, view


# ---- a bare block model (the fields the structurer and frameprog read) -----------
class BlockModel:
    """Hand-built stand-in for a committed ``structured.Model``: pc-keyed blocks
    plus the header fields, with ``written`` taken from the dispatch table."""

    def __init__(self, mem0, init, play, blocks, dispatch, subtune=0, prologue=(), dyn=None):
        self.mem0 = bytes(mem0)
        self.init = init
        self.play = play
        self.subtune = subtune
        self.prologue = list(prologue)
        self.blocks = blocks
        self.dispatch_sets = dispatch
        self.dispatch_pcs = set(dispatch)
        self.written = set(dispatch)
        self.pcs = {pc: {op} for pc, op in blocks if pc not in dispatch}
        self.dyn_targets = dict(dyn or {})
        self.data_decls = []  # data { } declarations (bytes carved from mem0)
        self.symbols = {}  # {cell: alias} role-alias bijection
        by_pc = {}
        for key in sorted(blocks):
            by_pc.setdefault(key[0], []).append(key)
        self._by_pc = by_pc

    def variants(self, pc):
        return self._by_pc.get(pc, ())
