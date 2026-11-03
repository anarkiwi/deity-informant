"""Whole-program optimization of a lift (``wholeprog.Program.functions``) for compact C.

``build`` turns every function into basic blocks of assignments over expression trees. ``optimize``
prunes the deopt paths, propagates and folds expressions across blocks, promotes zero-page cells
to variables and removes assignments no later read reaches, interprocedurally, to a fixed point.
"""

import copy
from collections import Counter

from pcode2c import slot_name

ARITH = {"INT_ADD", "INT_SUB", "INT_MULT", "INT_AND", "INT_OR", "INT_XOR"}
COMMUTE = {"INT_ADD", "INT_MULT", "INT_AND", "INT_OR", "INT_XOR", "INT_EQUAL", "INT_NOTEQUAL"}
CMPS = {"INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL"}
BOOLS = CMPS | {"BOOL_AND", "BOOL_OR", "BOOL_NEGATE", "INT_CARRY"}
NEGATE = {"INT_EQUAL": "INT_NOTEQUAL", "INT_NOTEQUAL": "INT_EQUAL"}
HOST = {"A", "SP"}
FLAGS = {"C", "Z", "I", "D", "V", "N"}
SIZE_LIMIT = 6
BOOL_LIMIT = 16


def C(val, size):
    """A constant expression."""
    return ("c", val & (1 << 8 * size) - 1, size)


def V(name, size):
    """A variable expression."""
    return ("v", name, size)


def esize(e):
    """The byte width of expression ``e``."""
    return e[2]


def ev(name, size, args):
    """The value of op ``name`` on constant ``args`` (``(value, size)`` pairs) at ``size`` bytes."""
    mask = (1 << 8 * size) - 1
    (a, sa), b = args[0], args[1][0] if len(args) > 1 else 0
    if name in ("COPY", "INT_ZEXT"):
        return a & mask
    table = {
        "INT_ADD": a + b,
        "INT_SUB": a - b,
        "INT_MULT": a * b,
        "INT_AND": a & b,
        "INT_OR": a | b,
        "INT_XOR": a ^ b,
        "INT_LEFT": a << b if b < 8 * size else 0,
        "INT_RIGHT": a >> b if b < 8 * sa else 0,
        "INT_EQUAL": int(a == b),
        "INT_NOTEQUAL": int(a != b),
        "INT_LESS": int(a < b),
        "INT_LESSEQUAL": int(a <= b),
        "BOOL_AND": int(bool(a) and bool(b)),
        "BOOL_OR": int(bool(a) or bool(b)),
        "BOOL_NEGATE": int(a == 0),
        "INT_CARRY": int(a + b >> 8 * sa & 1),
        "INT_NEGATE": ~a,
        "INT_2COMP": -a,
        "SUBPIECE": a >> 8 * b,
    }
    return table[name] & mask


def mk(name, size, args):
    """Op ``name`` of ``size`` bytes over ``args``, simplified."""
    if all(a[0] == "c" for a in args):
        return C(ev(name, size, [(a[1], a[2]) for a in args]), size)
    if name in COMMUTE and len(args) == 2 and args[0][0] == "c" and args[1][0] != "c":
        args = (args[1], args[0])
    if name in ("COPY", "INT_ZEXT") and esize(args[0]) == size:
        return args[0]
    if name == "COPY" and esize(args[0]) > size:
        return mk("SUBPIECE", size, (args[0], C(0, 4)))
    if name == "COPY":
        return mk("INT_ZEXT", size, args)
    if name == "SUBPIECE" and args[1][1] == 0 and esize(args[0]) == size:
        return args[0]
    if name == "SUBPIECE" and args[0][0] == "o" and args[0][1] == "INT_ZEXT" and args[1][1] == 0:
        inner = args[0][3][0]
        if esize(inner) == size:
            return inner
    if len(args) == 2 and args[1][0] == "c":
        k = args[1][1]
        if name in ("INT_ADD", "INT_OR", "INT_XOR") and k == 0:
            return args[0]
        if name == "INT_MULT" and k == 1 or name == "INT_AND" and k == (1 << 8 * size) - 1:
            return args[0]
        if name in ("INT_AND", "INT_MULT") and k == 0:
            return C(0, size)
        if name == "INT_CARRY" and k == 0:
            return C(0, size)
        inner = args[0]
        if (
            name == "INT_ADD"
            and inner[0] == "o"
            and inner[1] == "INT_ADD"
            and inner[3][1][0] == "c"
        ):
            return mk("INT_ADD", size, (inner[3][0], C(k + inner[3][1][1], size)))
    if name in ("INT_SUB", "INT_LEFT", "INT_RIGHT") and args[1][0] == "c" and args[1][1] == 0:
        return args[0]
    if name == "INT_SUB" and args[1][0] == "c":
        return mk("INT_ADD", size, (args[0], C(-args[1][1], size)))
    if name == "BOOL_NEGATE" and args[0][0] == "o" and args[0][1] in NEGATE:
        return mk(NEGATE[args[0][1]], args[0][2], args[0][3])
    if name in NEGATE and args[1][0] == "c" and args[1][1] == 0 and is_bool(args[0]):
        return args[0] if name == "INT_NOTEQUAL" else mk("BOOL_NEGATE", size, (args[0],))
    return ("o", name, size, tuple(args))


def is_bool(e):
    """Whether ``e`` is known to be 0 or 1."""
    return (
        e[0] == "o"
        and e[1] in BOOLS
        or e[0] == "c"
        and e[1] in (0, 1)
        or e[0] == "v"
        and e[1] in FLAGS
    )


def subst(e, env):
    """``e`` with the variables in ``env`` replaced, simplified."""
    if e[0] == "v":
        return env.get(e[1], e)
    if e[0] == "l":
        return ("l", subst(e[1], env), e[2])
    if e[0] == "o":
        return mk(e[1], e[2], tuple(subst(a, env) for a in e[3]))
    return e


def evars(e, out=None):
    """The variable names in ``e``."""
    out = set() if out is None else out
    if e[0] == "v":
        out.add(e[1])
    elif e[0] == "l":
        evars(e[1], out)
    elif e[0] == "o":
        for a in e[3]:
            evars(a, out)
    return out


def has_load(e):
    """Whether ``e`` reads memory."""
    return e[0] == "l" or e[0] == "o" and any(has_load(a) for a in e[3])


def nodes(e):
    """The node count of ``e``."""
    return 1 + (nodes(e[1]) if e[0] == "l" else sum(map(nodes, e[3])) if e[0] == "o" else 0)


def vname(v, pc):
    """The variable name of a register or temporary varnode of the function at ``pc``."""
    return slot_name(v, pc) if v[0] == "r" else f"u{v[1]}_{v[2]}@{pc:04X}"


class Fn:
    """One function: basic blocks ``{id: {"s": statements, "t": terminator}}`` from block 0."""

    def __init__(self, pc, entry):
        self.pc, self.entry, self.blocks, self.sizes = pc, entry, {}, {}

    def succs(self, b):
        """Successor block ids of ``b``."""
        t = self.blocks[b]["t"]
        if t[0] == "br":
            return [t[1]]
        if t[0] == "cbr":
            return [t[2], t[3]]
        if t[0] == "sw":
            return list(dict.fromkeys(x for _, x in t[2]))
        return []

    def preds(self):
        """Predecessor lists by block id."""
        p = {b: [] for b in self.blocks}
        for b in self.blocks:
            for s in self.succs(b):
                p[s].append(b)
        return p

    def order(self):
        """Reachable block ids in reverse postorder."""
        seen, out = set(), []
        stack = [(0, iter(self.succs(0)))]
        seen.add(0)
        while stack:
            b, it = stack[-1]
            for s in it:
                if s not in seen:
                    seen.add(s)
                    stack.append((s, iter(self.succs(s))))
                    break
            else:
                out.append(b)
                stack.pop()
        return out[::-1]


def read(v, fn):
    """The expression of input varnode ``v``."""
    if v[0] == "c":
        return C(v[1], v[2])
    fn.sizes[vname(v, fn.pc)] = v[2]
    return V(vname(v, fn.pc), v[2])


def build(funcs, entry):
    """The ``Fn`` list of ``funcs`` (``entry``: the play address)."""
    out = []
    for f in funcs:
        fn = Fn(f["pc"], bool(f["entry"] and f["pc"] == entry))
        first, nid = {}, [0]

        def new(fn=fn, nid=nid):
            fn.blocks[nid[0]] = {"s": [], "t": None}
            nid[0] += 1
            return nid[0] - 1

        for i, b in enumerate(f["blocks"]):
            cur = first[i] = new()
            for op in b["ops"]:
                if fn.blocks[cur]["t"] is not None:
                    break
                cur = lower(op, fn, f, cur, new, i)
            else:
                if fn.blocks[cur]["t"] is None:
                    last = i + 1 < len(f["blocks"])
                    fn.blocks[cur]["t"] = ("br", ("P", i + 1)) if last else ("ret",)
        for blk in fn.blocks.values():
            blk["t"] = relabel(blk["t"], first)
        out.append(fn)
    return out


def relabel(t, first):
    """Terminator ``t`` with block references ``("P", i)`` resolved through ``first``."""

    def fix(x):
        return first[x[1]] if isinstance(x, tuple) else x

    if t[0] == "br":
        return ("br", fix(t[1]))
    if t[0] == "cbr":
        return ("cbr", t[1], fix(t[2]), t[3])
    if t[0] == "sw":
        return ("sw", t[1], [(pc, fix(x)) for pc, x in t[2]])
    return t


def lower(op, fn, f, cur, new, here):
    """Append the statements of ``op`` to block ``cur``; the block the next op goes to."""
    name, out, ins, aux = op[0], op[1], op[2], op[3]
    blk = fn.blocks[cur]
    stmts = blk["s"]
    if name == "CBRANCH":
        nxt = new()
        blk["t"] = (
            "cbr",
            mk("INT_NOTEQUAL", 1, (read(ins[0], fn), C(0, ins[0][2]))),
            ("P", aux),
            nxt,
        )
        return nxt
    if name == "BRANCH":
        blk["t"] = ("br", ("P", aux))
    elif name == "RETURN":
        blk["t"] = ("ret",)
    elif name == "BRANCHIND":
        blk["t"] = ("sw", read(ins[0], fn), [(pc, ("P", x)) for pc, x in f["tables"][aux]])
    elif name == "CALL":
        stmts.append(("call", aux))
    elif name == "CALLOTHER" and aux == "codechk":
        fn.sizes[vname(out, fn.pc)] = out[2]
        stmts.append(("set", vname(out, fn.pc), C(0, out[2])))
    elif name == "CALLOTHER":
        blk["t"] = ("dead",)
    elif name == "STORE":
        addr, val = read(ins[0], fn), read(ins[1], fn)
        stmts.append(("io", addr, val) if aux == "volatile" else ("st", addr, val))
    elif name == "LOAD" and aux == "volatile":
        pred = read(ins[1], fn) if len(ins) > 1 and ins[1] else None
        dest = vname(out, fn.pc) if out else None
        if out:
            fn.sizes[dest] = out[2]
        stmts.append(("vld", dest, read(ins[0], fn), pred))
    elif name == "LOAD" and out:
        fn.sizes[vname(out, fn.pc)] = out[2]
        stmts.append(("set", vname(out, fn.pc), ("l", read(ins[0], fn), out[2])))
    elif name == "LOAD":
        pass
    else:
        fn.sizes[vname(out, fn.pc)] = out[2]
        stmts.append(
            ("set", vname(out, fn.pc), mk(name, out[2], tuple(read(v, fn) for v in ins if v)))
        )
    return cur


def prune(fns):
    """Drop the deopt paths: edges into blocks that only deopt, and the table defaults."""
    for fn in fns:
        dead = {b for b, x in fn.blocks.items() if x["t"][0] == "dead"}
        changed = True
        while changed:
            changed = False
            for b, x in fn.blocks.items():
                t = x["t"]
                if b in dead:
                    continue
                if t[0] == "br" and t[1] in dead or t[0] == "cbr" and {t[2], t[3]} <= dead:
                    dead.add(b)
                    changed = True
                elif t[0] == "cbr" and (t[2] in dead or t[3] in dead):
                    x["t"] = ("br", t[3] if t[2] in dead else t[2])
                    changed = True
                elif t[0] == "sw" and any(c in dead for _, c in t[2]):
                    keep = [(pc, c) for pc, c in t[2] if c not in dead]
                    x["t"] = ("sw", t[1], keep) if keep else ("dead",)
                    changed = True
        for b in dead:
            fn.blocks[b] = {"s": [], "t": ("dead",)}
    return fns


def clean(fn):
    """Simplify the CFG: constant branches, merged chains, empty and unreachable blocks."""
    changed = True
    while changed:
        changed = False
        for b in list(fn.blocks):
            t = fn.blocks[b]["t"]
            if t[0] == "sw" and len({x for _, x in t[2]}) == 1:
                fn.blocks[b]["t"] = ("br", t[2][0][1])
                changed = True
            if t[0] == "cbr" and (t[1][0] == "c" or t[2] == t[3]):
                fn.blocks[b]["t"] = ("br", t[2] if t[1][0] != "c" or t[1][1] else t[3])
                changed = True
        reach = set(fn.order())
        for b in list(fn.blocks):
            if b not in reach:
                del fn.blocks[b]
                changed = True
        preds = fn.preds()
        for b in list(fn.blocks):
            if b not in fn.blocks:
                continue
            t = fn.blocks[b]["t"]
            if t[0] != "br" or t[1] == b:
                continue
            s = t[1]
            if len(preds[s]) == 1 and s != 0:
                fn.blocks[b]["s"] += fn.blocks[s]["s"]
                fn.blocks[b]["t"] = fn.blocks[s]["t"]
                del fn.blocks[s]
                changed = True
                break
            if not fn.blocks[b]["s"] and b != 0:
                for p in preds[b]:
                    fn.blocks[p]["t"] = retarget(fn.blocks[p]["t"], b, s)
                changed = True
                break
    return fn


def retarget(t, old, new):
    """Terminator ``t`` with successor ``old`` replaced by ``new``."""

    def sw(x):
        return new if x == old else x

    if t[0] == "br":
        return ("br", sw(t[1]))
    if t[0] == "cbr":
        return ("cbr", t[1], sw(t[2]), sw(t[3]))
    if t[0] == "sw":
        return ("sw", t[1], [(pc, sw(x)) for pc, x in t[2]])
    return t


def uses_defs(s, sumry):
    """``(uses, defs, kills)`` of statement ``s``: defs may be skipped, kills always happen."""
    k = s[0]
    if k == "set":
        return evars(s[2]), {s[1]}, {s[1]}
    if k in ("st", "io"):
        return evars(s[1]) | evars(s[2]), set(), set()
    if k == "vld":
        u = evars(s[2]) | (evars(s[3]) if s[3] else set())
        return u, ({s[1]} if s[1] else set()), ({s[1]} if s[1] and s[3] is None else set())
    return sumry["livein"][s[1]], sumry["maydef"][s[1]], sumry["mustdef"][s[1]]


def term_uses(t):
    """Variables read by terminator ``t``."""
    return evars(t[1]) if t[0] in ("cbr", "sw") else set()


def summaries(fns):
    """May/must definitions and store flags per function, to a fixed point."""
    n = len(fns)
    allv = {v for fn in fns for v in fn.sizes}
    sm = {"maydef": [set() for _ in fns], "mustdef": [set(allv) for _ in fns], "hasst": [False] * n}
    sm["livein"] = [set() for _ in fns]
    changed = True
    while changed:
        changed = False
        for i, fn in enumerate(fns):
            may, hasst = set(), False
            for blk in fn.blocks.values():
                for s in blk["s"]:
                    if s[0] == "call":
                        may |= sm["maydef"][s[1]]
                        hasst |= sm["hasst"][s[1]]
                    else:
                        may |= uses_defs(s, sm)[1]
                        hasst |= s[0] == "st"
            must = must_defined(fn, sm)
            if (may, hasst, must) != (sm["maydef"][i], sm["hasst"][i], sm["mustdef"][i]):
                sm["maydef"][i], sm["hasst"][i], sm["mustdef"][i] = may, hasst, must
                changed = True
    return sm


def must_defined(fn, sm):
    """Variables defined on every path from entry to a return."""
    order = fn.order()
    preds = fn.preds()
    out = {}
    final = None
    for _ in range(len(order) + 2):
        stable = True
        for b in order:
            ps = [out[p] for p in preds[b] if p in out]
            cur = set.intersection(*ps) if ps and b != 0 else set()
            cur = set(cur)
            for s in fn.blocks[b]["s"]:
                cur |= uses_defs(s, sm)[2]
            if out.get(b) != cur:
                out[b] = cur
                stable = False
        if stable:
            break
    rets = [out[b] for b in order if fn.blocks[b]["t"][0] == "ret"]
    final = set.intersection(*rets) if rets else set()
    return final


def solve(fn, sm, ret):
    """Live-in and live-out per block of ``fn`` whose returns leave ``ret`` live."""
    live_in, live_out = {}, {}
    order = fn.order()[::-1]
    stable = False
    while not stable:
        stable = True
        for b in order:
            out = set(ret) if fn.blocks[b]["t"][0] == "ret" else set()
            for s in fn.succs(b):
                out |= live_in.get(s, set())
            live_out[b] = out
            cur = block_live(fn.blocks[b], out, sm)
            if live_in.get(b) != cur:
                live_in[b] = cur
                stable = False
    return live_in, live_out


def liveness(fns, sm, later, entry_idx):
    """Live variables after each function's returns (from its call sites; ``later`` for the
    entry); ``sm["livein"]`` is updated to the live-in sets of the entries."""
    ret = [set() for _ in fns]
    ret[entry_idx] = set(later)
    changed = True
    while changed:
        changed = False
        for i, fn in enumerate(fns):
            live_in, live_out = solve(fn, sm, ret[i])
            if live_in[0] != sm["livein"][i]:
                sm["livein"][i] = live_in[0]
                changed = True
            for b, blk in fn.blocks.items():

                def hook(callee, live):
                    nonlocal changed
                    if not live <= ret[callee]:
                        ret[callee] |= live
                        changed = True

                block_live(blk, live_out[b], sm, hook)
    return ret


def block_live(blk, out, sm, hook=None):
    """Live-in of ``blk`` given live-out ``out``; ``hook(callee, live)`` sees each call's live-out."""
    live = set(out) | term_uses(blk["t"])
    for s in reversed(blk["s"]):
        if s[0] == "call":
            if hook:
                hook(s[1], live)
            live = sm["livein"][s[1]] | (live - sm["mustdef"][s[1]])
            continue
        u, _, killed = uses_defs(s, sm)
        if s[0] == "set" and s[1] not in live:
            continue
        live = (live - killed) | u
    return live


def dce(fns, sm, ret):
    """Remove assignments whose value no later read reaches; whether anything changed."""
    changed = False
    for i, fn in enumerate(fns):
        _, live_out = solve(fn, sm, ret[i])
        for b, blk in fn.blocks.items():
            live = set(live_out[b]) | term_uses(blk["t"])
            keep = []
            for s in reversed(blk["s"]):
                if s[0] == "call":
                    live = sm["livein"][s[1]] | (live - sm["mustdef"][s[1]])
                    keep.append(s)
                    continue
                if s[0] == "set" and s[1] not in live:
                    changed = True
                    continue
                if s[0] == "vld" and s[1] and s[1] not in live:
                    s = ("vld", None, s[2], s[3])
                    changed = True
                u, _, killed = uses_defs(s, sm)
                live = (live - killed) | u
                keep.append(s)
            blk["s"] = keep[::-1]
    return changed


def propagate(fns, sm):
    """Forward expression propagation across blocks; whether anything changed."""
    changed = False
    for fn in fns:
        uc = Counter()
        for blk in fn.blocks.values():
            for s in blk["s"]:
                for v in uses_defs(s, sm)[0]:
                    uc[v] += 1
            for v in term_uses(blk["t"]):
                uc[v] += 1
        order = fn.order()
        preds = fn.preds()
        out = {}
        for _ in range(len(order) + 2):
            stable = True
            for b in order:
                env = meet(fn, b, out, preds)
                env = transfer(fn, fn.blocks[b], env, sm, uc, False)
                if out.get(b) != env:
                    out[b] = env
                    stable = False
            if stable:
                break
        for b in order:
            env = meet(fn, b, out, preds)
            before = (fn.blocks[b]["s"], fn.blocks[b]["t"])
            transfer(fn, fn.blocks[b], env, sm, uc, True)
            changed |= before != (fn.blocks[b]["s"], fn.blocks[b]["t"])
    return changed


def meet(fn, b, out, preds):
    """The expressions known at entry to ``b``: those every processed predecessor edge agrees on."""
    edges = [edge_env(fn, p, b, out[p]) for p in preds[b] if p in out]
    if not edges or b == 0:
        return {}
    return {k: v for k, v in edges[0].items() if all(e.get(k) == v for e in edges[1:])}


def edge_env(fn, p, b, env):
    """``env`` at the end of ``p`` as it holds on the edge to ``b``: a flag tested by the branch
    has the value that took the edge."""
    t = fn.blocks[p]["t"]
    if t[0] != "cbr" or t[2] == t[3]:
        return env
    c, val = t[1], int(b == t[2])
    if c[0] == "o" and c[1] == "BOOL_NEGATE":
        c, val = c[3][0], 1 - val
    if c[0] == "v" and c[1] in FLAGS:
        env = dict(env)
        env[c[1]] = C(val, 1)
    return env


def worth(e, uses):
    """Whether expression ``e`` of a variable read ``uses`` times is propagated into its readers:
    small ones, and any whose single reader would otherwise need its own statement; memory reads
    only into one reader."""
    if has_load(e) and uses != 1:
        return False
    return nodes(e) <= (BOOL_LIMIT if is_bool(e) else SIZE_LIMIT) or uses == 1


def forget(env, names):
    """Drop the entries of ``env`` that are, or read, one of ``names``."""
    for k in [k for k, e in env.items() if k in names or evars(e) & names]:
        del env[k]


def transfer(fn, blk, env, sm, uc, rewrite):
    """Run propagation over ``blk`` from ``env``; with ``rewrite`` the statements are rewritten."""
    new = []
    for s in blk["s"]:
        k = s[0]
        if k == "set":
            e = subst(s[2], env)
            forget(env, {s[1]})
            if s[1] not in evars(e) and (
                nodes(e) <= (BOOL_LIMIT if is_bool(e) else SIZE_LIMIT) or uc[s[1]] == 1
            ):
                env[s[1]] = e
            s = ("set", s[1], e)
        elif k == "st":
            s = ("st", subst(s[1], env), subst(s[2], env))
            for key in [key for key, e in env.items() if has_load(e)]:
                del env[key]
        elif k == "io":
            s = ("io", subst(s[1], env), subst(s[2], env))
        elif k == "vld":
            if s[1]:
                forget(env, {s[1]})
            s = ("vld", s[1], subst(s[2], env), subst(s[3], env) if s[3] else None)
        else:
            forget(env, sm["maydef"][s[1]])
            if sm["hasst"][s[1]]:
                for key in [key for key, e in env.items() if has_load(e)]:
                    del env[key]
        new.append(s)
    t = blk["t"]
    if t[0] == "cbr":
        t = ("cbr", subst(t[1], env), t[2], t[3])
    elif t[0] == "sw":
        t = ("sw", subst(t[1], env), t[2])
    if rewrite:
        blk["s"], blk["t"] = new, t
    return env


def promote(fns, cells):
    """Replace constant-address accesses to the page-0 ``cells`` by variables."""
    name = lambda c: f"z{c:02X}"  # noqa: E731

    def ld(e):
        if e[0] == "l":
            a = e[1]
            if a[0] == "c" and a[1] in cells:
                return byte_var(a[1], e[2])
            return ("l", ld(a), e[2])
        if e[0] == "o":
            return mk(e[1], e[2], tuple(ld(x) for x in e[3]))
        return e

    def byte_var(a, size):
        v = V(name(a), 1)
        if size == 1:
            return v
        hi = mk("INT_LEFT", 2, (mk("INT_ZEXT", 2, (V(name(a + 1), 1),)), C(8, 1)))
        return mk("INT_OR", 2, (mk("INT_ZEXT", 2, (v,)), hi))

    for fn in fns:
        for blk in fn.blocks.values():
            out = []
            for s in blk["s"]:
                if s[0] == "st" and s[1][0] == "c" and s[1][1] in cells:
                    a, val = s[1][1], ld(s[2])
                    if esize(val) == 1:
                        fn.sizes[name(a)] = 1
                        out.append(("set", name(a), val))
                    else:
                        fn.sizes[name(a)] = fn.sizes[name(a + 1)] = 1
                        out.append(("set", name(a), mk("SUBPIECE", 1, (val, C(0, 4)))))
                        out.append(("set", name(a + 1), mk("SUBPIECE", 1, (val, C(1, 4)))))
                    continue
                out.append(tuple(ld(x) if isinstance(x, tuple) else x for x in s))
            blk["s"] = out
            t = blk["t"]
            if t[0] in ("cbr", "sw"):
                blk["t"] = (t[0], ld(t[1])) + t[2:]
        for c in cells:
            fn.sizes[name(c)] = 1


def candidate_cells(fns):
    """Zero-page cells accessed only at constant addresses, and the facts assumed for the rest."""
    cells, aliased = Counter(), set()

    def visit(a, size, is_store):
        if a[0] == "c":
            for k in range(size):
                cells[a[1] + k] += 1
            return
        if a[0] == "o" and a[1] == "INT_ADD" and a[3][1][0] == "c":
            base = a[3][1][1]
            span = 256 if a[3][0][0] == "o" and a[3][0][1] == "INT_ZEXT" else 0x10000
            aliased.update(range(base, min(base + span, 0x100)))

    def walk(e):
        if e[0] == "l":
            visit(e[1], e[2], False)
            walk(e[1])
        elif e[0] == "o":
            for x in e[3]:
                walk(x)

    for fn in fns:
        for blk in fn.blocks.values():
            for s in blk["s"]:
                if s[0] == "st":
                    visit(s[1], esize(s[2]), True)
                for x in s[1:]:
                    if isinstance(x, tuple):
                        walk(x)
            t = blk["t"]
            if t[0] in ("cbr", "sw"):
                walk(t[1])
    return {c for c in cells if c < 0x100 and c not in aliased}


def settle(fns, times=3):
    """Propagate and clean the CFG ``times`` times; the summaries."""
    for _ in range(times):
        sm = summaries(fns)
        propagate(fns, sm)
        for fn in fns:
            clean(fn)
    return summaries(fns)


def fixpoint(fns, idx):
    """Optimize ``fns`` until stable; ``(summaries, return-liveness, later)`` where ``later`` is
    what a later call reads before writing it (a greatest fixed point from the empty set)."""
    later = set()
    while True:
        sm = settle(fns)
        while True:
            ret = liveness(fns, sm, later, idx)
            more = sm["livein"][idx] - HOST
            if more <= later:
                break
            later |= more
        if not dce(fns, sm, ret):
            return sm, ret, later


def optimize(funcs, entry):
    """Optimized ``Fn`` list for ``funcs`` with play address ``entry``, and an info dict.

    ``info``: ``unobserved`` (zero-page cells held as variables: no later call reads them),
    ``reads`` (variables a call reads before writing, other than the host's ``A`` and ``SP``)."""
    base = prune(build(funcs, entry))
    idx = next(i for i, f in enumerate(base) if f.entry)
    banned = set()
    while True:
        fns = copy.deepcopy(base)
        settle(fns, 2)
        cells = candidate_cells(fns) - banned
        promote(fns, cells)
        sm, ret, later = fixpoint(fns, idx)
        held = {int(v[1:], 16) for v in later if v[0] == "z"}
        if not held:
            break
        banned |= held
    info = {"reads": sorted(later), "entry": idx}
    info["unobserved"] = sorted(c for c in cells if any(f"z{c:02X}" in fn.sizes for fn in fns))
    return fns, sm, ret, info
