"""Compact structured C of a lift: optimized by ``pcodeopt``, structured and printed here.

``compact_c(funcs, entry, regions)`` returns ``(text, info)``: if/else, ``switch`` and loops
recovered from the CFG, ``goto`` only for edges that fit neither, variables local where the
liveness summaries allow and layout regions naming the memory accesses.
"""

import re

import pcodeopt as O
from pcode2c import Names

CT = {1: "uint8_t", 2: "uint16_t"}
SYM = {
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_AND": "&",
    "INT_OR": "|",
    "INT_XOR": "^",
    "INT_MULT": "*",
    "INT_LEFT": "<<",
    "INT_RIGHT": ">>",
    "INT_EQUAL": "==",
    "INT_NOTEQUAL": "!=",
    "INT_LESS": "<",
    "INT_LESSEQUAL": "<=",
    "BOOL_AND": "&&",
    "BOOL_OR": "||",
}
TINY = 4
JUMPS = ("return;", "break;", "continue;")
ASSUMPTIONS = """Assumptions, each a fact about the tune checked by the replay against the reference:
 - the decimal flag is clear on entry and no entry check fails (no deopt is reached);
 - no code-cell check (codechk) reports a store into executing code;
 - every computed jump lands on one of its recorded targets;
 - stack guards hold and stack bytes are private (page 1 is not read as data);
 - accesses through a loaded pointer address outside page 0;
 - the host sets only A and SP before a call; X, Y and the flags are written before they are read."""


def core(xs):
    """``xs`` without label markers."""
    return [x for x in xs if not (isinstance(x, tuple) and x[0] == "label")]


def single(xs):
    """Whether the lines ``xs`` are one jump."""
    c = core(xs)
    return len(c) == 1 and (c[0] in JUMPS if isinstance(c[0], str) else c[0][0] == "goto")


def par(t):
    """``t`` parenthesized unless it has no binary operator outside brackets."""
    depth = 0
    for ch in t:
        depth += ch in "([" and 1 or ch in ")]" and -1 or 0
        if ch == " " and depth == 0:
            return f"({t})"
    return t


class Printer:
    """Expressions and statements as C text."""

    def __init__(self, names):
        self.names, self.cells, self.rename = names, {}, {}

    def num(self, v):
        """A constant."""
        return str(v) if v < 10 else f"0x{v:X}"

    def mem(self, a, size):
        """A memory read or write place at address expression ``a``."""
        if a[0] == "c":
            if size == 2:
                return self.names.word(a[1])
            named = self.names.at(a[1], None)
            if named:
                return named
            self.cells[a[1]] = f"cell_{a[1]:04X}"
            return f"*cell_{a[1]:04X}"
        if size == 2:
            return f"RD16({self.expr(a, True)})"
        if a[0] == "o" and a[1] == "INT_ADD" and a[3][1][0] == "c":
            idx = a[3][0]
            if idx[0] == "o" and idx[1] == "INT_ZEXT":
                named = self.names.at(a[3][1][1], self.expr(idx[3][0]))
                if named:
                    return named
        return f"M[(uint16_t){par(self.expr(a, True))}]"

    def expr(self, e, ok=False):
        """``e`` as C; ``ok``: the caller truncates the result, so arithmetic needs no cast."""
        k = e[0]
        if k == "c":
            return self.num(e[1])
        if k == "v":
            return self.rename.get(e[1], e[1])
        if k == "l":
            return self.mem(e[1], e[2])
        name, size, a = e[1], e[2], e[3]

        def wrap(t):
            return t if ok else f"({CT[size]})({t})"

        if name == "INT_ADD" and a[1][0] == "c" and a[1][1] > (1 << 8 * size - 1):
            neg = (1 << 8 * size) - a[1][1]
            return wrap(f"{par(self.expr(a[0], esize(a[0]) == size))} - {self.num(neg)}")
        if name in O.ARITH:
            parts = [par(self.expr(x, esize(x) == size)) for x in a]
            return wrap(f"{parts[0]} {SYM[name]} {parts[1]}")
        if name == "INT_LEFT":
            return wrap(f"{par(self.expr(a[0], esize(a[0]) == size))} << {par(self.expr(a[1]))}")
        if name == "INT_RIGHT":
            return f"{par(self.expr(a[0]))} >> {par(self.expr(a[1]))}"
        if name in SYM:
            return f"{par(self.expr(a[0]))} {SYM[name]} {par(self.expr(a[1]))}"
        if name == "BOOL_NEGATE":
            return f"!{par(self.expr(a[0]))}"
        if name == "INT_NEGATE":
            return wrap(f"~{par(self.expr(a[0], True))}")
        if name == "INT_2COMP":
            return wrap(f"-{par(self.expr(a[0], True))}")
        if name == "INT_ZEXT":
            return self.expr(a[0])
        if name == "INT_CARRY":
            return f"{par(self.expr(a[0]))} + {par(self.expr(a[1]))} > 0x{(1 << 8 * esize(a[0])) - 1:X}"
        if name == "SUBPIECE":
            k = a[1][1]
            if k == 0:
                return f"({CT[size]}){par(self.expr(a[0], True))}"
            return f"({CT[size]})({par(self.expr(a[0]))} >> {8 * k})"
        raise ValueError(name)

    def cond(self, e, negate=False):
        """A branch condition, negated if ``negate``."""
        if e[0] == "o" and e[1] in O.NEGATE and e[3][1] == O.C(0, esize(e[3][1])):
            e = O.mk("BOOL_NEGATE", 1, (e[3][0],)) if e[1] == "INT_EQUAL" else e[3][0]
        if negate:
            if e[0] == "o" and e[1] == "BOOL_NEGATE":
                e = e[3][0]
            elif e[0] == "o" and e[1] in O.NEGATE:
                e = O.mk(O.NEGATE[e[1]], e[2], e[3])
            elif e[0] == "o" and e[1] in SYM and e[1] in O.CMPS:
                return f"!({self.expr(e)})"
            else:
                e = O.mk("BOOL_NEGATE", 1, (e,))
        return self.expr(e)

    def stmt(self, s, labels):
        """C text for one statement."""
        k = s[0]
        if k == "set":
            return f"{self.rename.get(s[1], s[1])} = {self.expr(s[2], True)};"
        if k == "st":
            if esize(s[2]) == 2:
                return f"WR16({self.expr(s[1], True)}, {self.expr(s[2], True)});"
            return f"{self.mem(s[1], 1)} = {self.expr(s[2], True)};"
        if k == "io":
            return f"io_write({self.expr(s[1], True)}, {self.expr(s[2], True)});"
        if k == "vld":
            read = f"io_read({self.expr(s[2], True)})"
            if s[1]:
                return f"{self.rename.get(s[1], s[1])} = {read};"
            return f"if ({self.cond(s[3])}) {read};" if s[3] else f"{read};"
        return f"{labels[s[1]]}();"


def esize(e):
    """The byte width of ``e``."""
    return e[2]


def idoms(nodes, preds, entry):
    """Immediate dominators of ``nodes`` (reverse postorder) from ``entry``."""
    idx = {n: i for i, n in enumerate(nodes)}
    idom = {entry: entry}
    changed = True
    while changed:
        changed = False
        for n in nodes:
            if n == entry:
                continue
            done = [p for p in preds[n] if p in idom]
            if not done:
                continue
            new = done[0]
            for p in done[1:]:
                a, b = p, new
                while a != b:
                    while idx[a] > idx[b]:
                        a = idom[a]
                    while idx[b] > idx[a]:
                        b = idom[b]
                new = a
            if idom.get(n) != new:
                idom[n] = new
                changed = True
    return idom


class Structurer:
    """if/else, ``switch`` and loops from the CFG of one optimized function."""

    def __init__(self, fn, printer, labels):
        self.fn, self.pr, self.labels = fn, printer, labels
        self.order = fn.order()
        self.preds = fn.preds()
        self.dom = idoms(self.order, self.preds, 0)
        self.exit = -1
        succ = {b: fn.succs(b) for b in self.order}
        rets = [b for b in self.order if fn.blocks[b]["t"][0] == "ret"]
        rpreds = {b: list(succ[b]) + ([self.exit] if b in rets else []) for b in self.order}
        rpreds[self.exit] = []
        post = self.post_order(succ, rets)
        self.pdom = idoms(post, {n: list(rpreds[n]) for n in post}, self.exit)
        self.loops = {}
        for b in self.order:
            for s in fn.succs(b):
                if self.dominates(s, b):
                    self.loops.setdefault(s, set()).update(self.natural(s, b))
        self.done, self.used, self.cond_after = set(), set(), {}

    def post_order(self, succ, rets):
        """Reverse postorder of the reversed CFG from the virtual exit."""
        rev = {b: [] for b in self.order}
        rev[self.exit] = list(rets)
        for b in self.order:
            for s in succ[b]:
                rev[s].append(b)
        seen, out = {self.exit}, []
        stack = [(self.exit, iter(rev[self.exit]))]
        while stack:
            n, it = stack[-1]
            for s in it:
                if s not in seen:
                    seen.add(s)
                    stack.append((s, iter(rev[s])))
                    break
            else:
                out.append(n)
                stack.pop()
        return out[::-1]

    def dominates(self, a, b):
        """Whether ``a`` dominates ``b``."""
        while b != a:
            if b == 0:
                return False
            b = self.dom[b]
        return True

    def natural(self, h, tail):
        """The natural loop of back edge ``tail -> h``."""
        body, stack = {h, tail}, [tail] if tail != h else []
        while stack:
            for p in self.preds[stack.pop()]:
                if p not in body:
                    body.add(p)
                    stack.append(p)
        return body

    def ipdom(self, b):
        """The immediate post-dominator of ``b`` (-1: the function exit)."""
        return self.pdom.get(b, self.exit)

    def tiny(self, nb):
        """Whether block ``nb`` is short and plain enough to copy instead of jumping to."""
        blk = self.fn.blocks[nb]
        return len(blk["s"]) <= TINY and blk["t"][0] in ("ret", "br") and nb not in self.loops

    def jump(self, nb, stops, lp, depth=0):
        """``(lines, next)`` for control reaching block ``nb``: a jump statement or inline code."""
        if lp and nb == lp["h"]:
            return ["continue;"], None
        if lp and nb == lp["exit"]:
            return ["break;"], None
        if nb in stops:
            return [], None
        if (lp and nb not in lp["body"] or nb in self.done) and depth < 3 and self.tiny(nb):
            blk = self.fn.blocks[nb]
            out = [self.pr.stmt(x, self.labels) for x in blk["s"]]
            if blk["t"][0] == "ret":
                return out + ["return;"], None
            more, nxt = self.jump(blk["t"][1], stops, lp, depth + 1)
            return out + more, nxt
        if lp and nb not in lp["body"] or nb in self.done:
            self.used.add(nb)
            return [("goto", nb)], None
        return [], nb

    def seq(self, b, stops, lp):
        """Lines for the code from block ``b`` until a stop or exit."""
        out = []
        while b is not None:
            if b in self.loops and (lp is None or lp["h"] != b) and b not in self.done:
                lines, b = self.loop(b, stops, lp)
                out += lines
                continue
            self.done.add(b)
            out.append(("label", b))
            blk = self.fn.blocks[b]
            out += [self.pr.stmt(s, self.labels) for s in blk["s"]]
            t = blk["t"]
            if lp and lp.get("latch") == b:
                self.cond_after[lp["h"]] = t
                return out
            if t[0] == "ret":
                out.append("return;")
                return out
            if t[0] == "br":
                lines, b = self.jump(t[1], stops, lp)
                out += lines
            elif t[0] == "cbr":
                lines, b = self.branch(b, t, stops, lp)
                out += lines
            else:
                lines, b = self.switch(b, t, stops, lp)
                out += lines
        return out

    def join(self, b, stops, lp):
        """The block both arms of ``b`` meet at, or None."""
        j = self.ipdom(b)
        if j == self.exit or j in stops or j in self.done:
            return None
        if lp and (j not in lp["body"] or j == lp["h"]):
            return None
        return j

    def arm(self, x, stops, lp):
        """Lines of one arm starting at block ``x``."""
        lines, nxt = self.jump(x, stops, lp)
        return lines + (self.seq(nxt, stops, lp) if nxt is not None else [])

    def branch(self, b, t, stops, lp):
        """An ``if`` for conditional terminator ``t`` of ``b``; the lines and the join block."""
        j = self.join(b, stops, lp)
        inner = stops | {j} if j is not None else stops
        then = self.arm(t[2], inner, lp)
        other = self.arm(t[3], inner, lp)
        c = self.pr.cond(t[1])
        nc = self.pr.cond(t[1], True)
        if not core(then) and not core(other):
            return then + other, j
        if not core(other):
            return [f"if ({c}) {{"] + then + ["}"] + other, j
        if not core(then):
            return [f"if ({nc}) {{"] + other + ["}"] + then, j
        if j is None and single(then):
            return [f"if ({c}) {{"] + then + ["}"] + other, None
        if j is None and single(other):
            return [f"if ({nc}) {{"] + other + ["}"] + then, None
        return [f"if ({c}) {{"] + then + ["} else {"] + other + ["}"], j

    def switch(self, b, t, stops, lp):
        """A ``switch`` for ``BRANCHIND`` terminator ``t`` of ``b``."""
        j = self.join(b, stops, lp)
        inner = stops | {j} if j is not None else stops
        groups = {}
        for pc, x in t[2]:
            groups.setdefault(x, []).append(pc)
        if len(groups) == 1:
            return self.arm(next(iter(groups)), stops, lp), j
        out = [f"switch ({self.pr.expr(t[1])}) {{"]
        items = list(groups.items())
        for n, (x, pcs) in enumerate(items):
            labs = [f"case 0x{pc:04X}:" for pc in pcs]
            if n == len(items) - 1:
                labs = labs[:-1] + ["default:"] if len(labs) > 1 else ["default:"]
            body = self.arm(x, inner, lp)
            out += labs + body
            last = core(body)[-1:]
            if not last or not (isinstance(last[0], str) and last[0] in JUMPS):
                out.append("break;")
        return out + ["}"], j

    def loop(self, h, stops, lp):
        """Lines for the loop headed at ``h`` and the block after it."""
        body = self.loops[h]
        exits = []
        for b in self.order:
            if b in body:
                exits += [s for s in self.fn.succs(b) if s not in body and s not in exits]
        head = self.fn.blocks[h]
        t = head["t"]
        primary = next((s for s in (t[2:4] if t[0] == "cbr" else []) if s in exits), None)
        e = primary if primary is not None else (exits[0] if exits else None)
        backs = [b for b in body if h in self.fn.succs(b)]
        latch = None
        if len(backs) == 1:
            lt = self.fn.blocks[backs[0]]["t"]
            if lt[0] == "cbr" and {lt[2], lt[3]} == {h, e}:
                latch = backs[0]
        inner = {"h": h, "exit": e, "body": body, "latch": latch}
        mark = ("label", h)
        outer = stops if lp is None else stops
        while_cond = None
        if latch is None and not head["s"] and t[0] == "cbr" and e in (t[2], t[3]):
            in_arm = t[3] if e == t[2] else t[2]
            if in_arm in body:
                while_cond = self.pr.cond(t[1], e == t[2])
                self.done.add(h)
                lines = self.arm(in_arm, outer, inner)
        if while_cond is not None:
            lines = [f"while ({while_cond}) {{"] + lines
            lines.append("}")
        else:
            lines = self.seq(h, outer, inner)
            if latch is not None:
                lt = self.cond_after.pop(h)
                lines = ["do {"] + lines + ["} while (" + self.pr.cond(lt[1], lt[2] != h) + ");"]
            else:
                if lines and lines[-1] == "continue;":
                    lines.pop()
                lines = ["while (1) {"] + lines + ["}"]
        lines = [mark] + lines
        nxt = None
        if e is not None:
            lines2, nxt = self.jump(e, outer, lp)
            lines += lines2
        return lines, nxt


def finish(lines, labels_used):
    """Flatten ``lines`` with indentation, labels where jumped to, and gotos."""
    out = []
    for ln in lines:
        if isinstance(ln, tuple) and ln[0] == "label":
            if ln[1] in labels_used:
                out.append(f"L{ln[1]}:;")
        elif isinstance(ln, tuple):
            out.append(f"goto L{ln[1]};")
        else:
            out.append(ln)
    return out


def indent(lines):
    """``lines`` with brace-driven indentation; labels stay at the margin."""
    out, depth = [], 1
    for ln in lines:
        s = ln.strip()
        if s.startswith("}"):
            depth -= 1
        out.append(s if re.fullmatch(r"L\d+:;", s) else "  " * depth + s)
        if s.endswith("{"):
            depth += 1
    return out


def compact_c(funcs, entry, regions=()):
    """``(C text, info)`` for ``funcs`` with play address ``entry``."""
    fns, _, _, info = O.optimize(funcs, entry)
    names = Names(regions)
    pr = Printer(names)
    labels = ["play" if f.entry else f"f_{f.pc:04X}" for f in fns]
    refs = {}
    for i, fn in enumerate(fns):
        text = repr(fn.blocks)
        for v in fn.sizes:
            if re.search(rf"'{v}'", text):
                refs.setdefault(v, set()).add(i)
    recursive = {i for i in range(len(fns)) if reaches(fns, i)}
    glob = {v for v, s in refs.items() if len(s) > 1 or next(iter(s)) in recursive}
    glob |= set(info["reads"]) | {"A", "SP"}
    sizes = {v: fn.sizes[v] for fn in fns for v in fn.sizes}
    bodies = {}
    gren = {v: v.replace("@", "_") for v in glob}
    for i, fn in enumerate(fns):
        used = {v for v in fn.sizes if v in refs and i in refs[v] and v not in glob}
        pr.rename = {**gren, **{v: v.split("@")[0] for v in used}}
        st = Structurer(fn, pr, labels)
        raw = st.seq(0, set(), None)
        for b in st.order:
            if b not in st.done:
                st.used.add(b)
                raw += ["return;"] + st.seq(b, set(), None)
        lines = finish(raw, st.used)
        if lines and lines[-1] == "return;":
            lines.pop()
        decl = [
            f"{CT[s]} {', '.join(sorted(pr.rename[v] for v in used if sizes[v] == s))};"
            for s in (1, 2)
            if any(sizes[v] == s for v in used)
        ]
        bodies[i] = (
            [f"void {labels[i]}(void) {{"] + ["  " + d for d in decl] + indent(lines) + ["}"]
        )
    order = []

    def visit(i, seen=()):
        if i in order or i in seen:
            return
        for s in fns[i].blocks.values():
            for st in s["s"]:
                if st[0] == "call":
                    visit(st[1], seen + (i,))
        order.append(i)

    for i in range(len(fns)):
        visit(i)
    head = ["/*", ASSUMPTIONS, "*/", "#include <stdint.h>", "extern uint8_t M[65536];"]
    head += [
        "uint8_t io_read(uint16_t a);",
        "void io_write(uint16_t a, uint8_t v);",
        "#define RD16(a) ((uint16_t)(M[(uint16_t)(a)] | M[(uint16_t)((a) + 1)] << 8))",
        "#define WR16(a, v) (M[(uint16_t)(a)] = (uint8_t)(v), M[(uint16_t)((a) + 1)] = (uint8_t)((v) >> 8))",
    ]
    for s in (1, 2):
        g = sorted(v for v in glob if sizes.get(v, 1) == s and (v in refs or v in O.HOST))
        if g:
            head.append(f"{CT[s]} {', '.join(dict.fromkeys(gren.get(v, v) for v in g))};")
    head += names.macros()
    head += [f"uint8_t *const {n} = M + 0x{a:04X};" for a, n in sorted(pr.cells.items())]
    protos = [f"void {labels[i]}(void);" for i in recursive]
    text = "\n".join(head + protos + [x for i in order for x in bodies[i]]) + "\n"
    return text, info


def reaches(fns, i):
    """Whether function ``i`` can call itself."""
    seen, stack = set(), [i]
    while stack:
        f = stack.pop()
        for blk in fns[f].blocks.values():
            for s in blk["s"]:
                if s[0] == "call":
                    if s[1] == i:
                        return True
                    if s[1] not in seen:
                        seen.add(s[1])
                        stack.append(s[1])
    return False
