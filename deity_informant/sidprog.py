"""sidprog: the canonical structured text for decompiled playroutines.

``emit`` renders a model as procedures of nested regions (loop/if/switch over
labeled block leaves) whose block/terminator lines stay the semantic truth;
``parse`` is its exact inverse and re-verifies the structurer codec.
"""

from __future__ import annotations

import re

from . import codec
from . import stext
from . import structured as C

SIDPROG_VERSION = 1  # 1: play-phase structured program (spec section 6)

_BIND_BASE = 1 << 20  # tN bindings ride the uni namespace above any real slot
_T_REF = re.compile(r"\bt(\d+)\b")
_U_REF = re.compile(r"\bu(\d+)\b")
_CYC_LINE = re.compile(r"@\d+")
_CYC_FUSED = re.compile(r"@(\d+) (.+)$")


class SidprogVersionError(ValueError):
    """A document's ``sidprog <major>`` header is not this reader's major."""


# ---- per-block common-subexpression bindings (textual only) --------------------
def _kids(n):
    if n[0] == "mem":
        return (n[1],)
    if n[0] == "op":
        return n[2]
    return ()


def _rebuild(n, kids):
    if n[0] == "mem":
        return ("mem", kids[0], n[2])
    if n[0] == "op":
        return ("op", n[1], tuple(kids), n[3])
    return n


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


def _roots(blk):
    out = []
    for ev in blk.events:
        if ev[0] == "ld":
            out.append(ev[2])
        elif ev[0] == "st":
            out.extend((ev[1], ev[2]))
        elif ev[0] == "pen":
            out.extend((ev[2], ev[3]))
    for i in range(16):
        if blk.regs[i] != ("reg", i):
            out.append(blk.regs[i])
    out.extend(_term_exprs(blk.term))
    return out


def _map_term(term, f):
    k = term[0]
    if k == "br":
        return term[:4] + (f(term[4]), None if term[5] is None else f(term[5]))
    if k == "jmpd":
        return ("jmpd", f(term[1]))
    if k == "jmpind" and term[2] is not None:
        return ("jmpind", term[1], f(term[2]))
    if k == "jsr" and term[3] is not None:
        return ("jsr", term[1], term[2], f(term[3]))
    return term


def _rename(line):
    def sub(m):
        n = int(m.group(1))
        return "t%d" % (n - _BIND_BASE) if n >= _BIND_BASE else m.group(0)

    return _U_REF.sub(sub, line)


class _Cse:
    """Hash-consed sharing over one block's expressions: any op/mem subtree
    referenced more than once binds to ``tN``, making emission O(DAG size)."""

    def __init__(self, roots):
        self._oid = {}  # id(node) -> nid
        self._key = {}  # structural key -> nid
        self._rep = []  # nid -> representative node
        self._ref = []  # nid -> DAG reference count
        self._kid = []  # nid -> child nids
        self._out = {}  # id(node) -> reference form (bound subtrees -> tN)
        for r in roots:
            self._ref[self._intern(r)] += 1
        self.order = []
        self._name = {}
        self._len = {}
        seen = set()
        for r in roots:
            self._post(self._oid[id(r)], seen)

    def _intern(self, root):
        stack = [root]
        while stack:
            n = stack[-1]
            if id(n) in self._oid:
                stack.pop()
                continue
            todo = [k for k in _kids(n) if id(k) not in self._oid]
            if todo:
                stack.extend(todo)
                continue
            stack.pop()
            kid_ids = tuple(self._oid[id(k)] for k in _kids(n))
            if n[0] == "op":
                key = ("op", n[1], kid_ids, n[3])
            elif n[0] == "mem":
                key = ("mem", kid_ids[0], n[2])
            else:
                key = n
            nid = self._key.get(key)
            if nid is None:
                nid = len(self._rep)
                self._key[key] = nid
                self._rep.append(n)
                self._ref.append(0)
                self._kid.append(kid_ids)
                for k in kid_ids:
                    self._ref[k] += 1
            self._oid[id(n)] = nid
        return self._oid[id(root)]

    def _post(self, top, seen):
        stack = [(top, False)]
        while stack:
            nid, done = stack.pop()
            if done:
                if self._bind(nid):
                    self.order.append(nid)
                continue
            if nid in seen:
                continue
            seen.add(nid)
            stack.append((nid, True))
            stack.extend((k, False) for k in self._kid[nid])

    def _bind(self, nid):
        """Bind iff the tN definition + references print shorter than inlining."""
        n = self._rep[nid]
        kids = self._kid[nid]
        if not kids:
            self._len[nid] = len(stext.fmt_expr(n))
            return False
        base = {"INT_ZEXT": 7, "INT_CARRY": 9}.get(n[1], 3 * len(kids) + 1) if n[0] == "op" else 5
        plen = base + sum(3 if k in self._name else self._len[k] for k in kids)
        self._len[nid] = plen
        refs = self._ref[nid]
        if refs > 1 and (refs - 1) * plen > 12 + 3 * refs:
            self._name[nid] = len(self._name)
            return True
        return False

    def form(self, n):
        out = self._out
        stack = [n]
        while stack:
            x = stack[-1]
            if id(x) in out:
                stack.pop()
                continue
            name = self._name.get(self._oid[id(x)])
            if name is not None:
                out[id(x)] = ("uni", _BIND_BASE + name, 1)
                stack.pop()
                continue
            todo = [k for k in _kids(x) if id(k) not in out]
            if todo:
                stack.extend(todo)
                continue
            stack.pop()
            out[id(x)] = _rebuild(x, [out[id(k)] for k in _kids(x)])
        return out[id(n)]

    def binding_lines(self):
        out = []
        for i, nid in enumerate(self.order):
            rep = self._rep[nid]
            body = _rebuild(rep, [self.form(k) for k in _kids(rep)])
            out.append("t%d = %s" % (i, _rename(stext.fmt_expr(body))))
        return out


def _shadow(blk, cse):
    """The block with every bound subtree replaced by its tN placeholder."""
    events = []
    for ev in blk.events:
        if ev[0] == "ld":
            events.append(("ld", ev[1], cse.form(ev[2])))
        elif ev[0] == "st":
            events.append(("st", cse.form(ev[1]), cse.form(ev[2])))
        elif ev[0] == "pen":
            events.append(("pen", ev[1], cse.form(ev[2]), cse.form(ev[3])))
        else:
            events.append(ev)
    regs = [r if r == ("reg", i) else cse.form(r) for i, r in enumerate(blk.regs)]
    return C.Block(blk.pc, blk.op0, blk.pcs, events, _map_term(blk.term, cse.form), regs)


# ---- emission -------------------------------------------------------------------
def _image_lines(mem0):
    """``image { .. }``: runs of nonzero bytes, 16 per row, packed hex pairs."""
    out = ["image {"]
    row = []
    for a in range(0x10000):
        if mem0[a]:
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


def _items(region):
    return region.a if region.kind == "seq" else [region]


class _SortedView:
    """Model facade with canonical (sorted) variant order for structuring;
    ``hidden`` pcs (already serialized by an earlier proc) become goto labels."""

    def __init__(self, model):
        self.blocks = model.blocks
        self.mem0 = model.mem0
        self.dyn_targets = model.dyn_targets
        self.dispatch_pcs = set(model.dispatch_sets)
        self.hidden = set()
        by_pc = {}
        for key in sorted(model.blocks):
            by_pc.setdefault(key[0], []).append(key)
        self._by_pc = by_pc

    def variants(self, pc):
        return () if pc in self.hidden else self._by_pc.get(pc, ())


class _Writer:
    """Serializes each procedure's region tree; term lines carry the flow."""

    def __init__(self, model):
        self.model = _SortedView(model)
        self.dispatch = set(model.dispatch_sets)
        self.out = []
        self.done = set()

    def line(self, text, d):
        self.out.append(" " * d + text)

    def proc(self, entry):
        root, _labels = codec.structure(self.model, entry)
        seen = set(self.done)
        self.out.append("proc $%04X {" % entry)
        for r in _items(root):
            self.seq(_items(r), 1)
        self.out.append("}")
        self.model.hidden.update(k[0] for k in self.done - seen)

    def capture(self, items, d):
        keep, self.out = self.out, []
        self.seq(items, d)
        buf, self.out = self.out, keep
        return buf

    def seq(self, items, d):
        i = 0
        while i < len(items):
            r = items[i]
            k = r.kind
            if k == "block":
                i += self.block(r.a, items[i + 1] if i + 1 < len(items) else None, d)
                continue
            if k == "seq":
                self.seq(r.a, d)
            elif k == "loop":
                self.line("loop {", d)
                self.seq(_items(r.a), d + 1)
                self.line("}", d)
            elif k == "switch":
                self.opcode_switch(r, d)
            elif k == "goto":
                echo = "goto $%04X" % r.a
                if not self.out or self.out[-1].lstrip("\x00 ") != echo:
                    self.line(echo, d)
            elif k in ("cont", "brk"):
                self.line("continue" if k == "cont" else "break", d)
            else:
                raise ValueError("unexpected %s region in sequence" % k)
            i += 1

    def block(self, blk, nxt, d):
        self.done.add((blk.pc, blk.op0))
        cse = _Cse(_roots(blk))
        self.line(stext._label((blk.pc, blk.op0), self.dispatch) + ":", d)
        shadow = _shadow(blk, cse)
        for l in cse.binding_lines():
            self.line(l, d + 1)
        pend = None
        for l in stext._block_lines(shadow):
            l = _rename(l)
            if _CYC_LINE.fullmatch(l):
                pend = l
                continue
            self.line(pend + " " + l if pend else l, d + 1)
            pend = None
        if pend:
            self.line(pend, d + 1)
        term = [_rename(l) for l in stext._term_lines(shadow.term, None)]
        if nxt is not None and nxt.kind == "if":
            self.line(term[0] + " {", d)
            self.seq(_items(nxt.b), d + 1)
            els = self.capture(_items(nxt.c), d + 1)
            if els:
                self.line("} else {", d)
                self.out.extend(els)
            self.line("}", d)
            return 2
        if nxt is not None and nxt.kind == "switch" and not nxt.b:
            for l in term:
                self.line(l, d + 1)
            sel, cases = nxt.a
            t = blk.term
            vector = t[0] == "jmpind" and t[1] is not None
            if vector and not self.model.dyn_targets.get(blk.pcs[-1]):
                for _lbl, arm in cases:  # image-derived target: never recorded
                    self.seq(_items(arm), d)
            elif sel == "call":
                body = " ".join(lbl for lbl, _r in cases)
                self.line("switch call { %s }" % body if body else "switch call { }", d)
            else:
                self.line("switch goto {", d)
                for lbl, arm in cases:
                    self.line("case %s: {" % lbl, d + 1)
                    self.seq(_items(arm), d + 2)
                    self.line("}", d + 1)
                self.line("}", d)
            return 2
        if blk.term[0] in ("goto", "jmp"):
            self.out.append("\x00" + " " * (d + 1) + term[0])  # elidable fallthrough
        else:
            for l in term:
                self.line(l, d + 1)
        return 2 if nxt is not None and nxt.kind == "exit" else 1

    def opcode_switch(self, r, d):
        sel, cases = r.a
        self.line("switch %s {" % sel, d)
        for lbl, body in cases:
            self.line("case %s: {" % lbl, d + 1)
            self.seq(_items(body), d + 2)
            self.line("}", d + 1)
        self.line("}", d)


def _entries(model):
    """Procedure entries: play, then every static or proven-dynamic call target."""
    extra = set()
    for blk in model.blocks.values():
        if blk.term[0] == "jsr":
            if blk.term[1] is not None:
                extra.add(blk.term[1])
            else:
                extra.update(model.dyn_targets.get(blk.pcs[-1], ()))
    return [model.play] + sorted(extra - {model.play})


def emit(model):
    """Canonical sidprog text (``parse`` is its exact inverse)."""
    out = ["sidprog %d" % SIDPROG_VERSION, "play $%04X" % model.play, "init $%04X" % model.init]
    if getattr(model, "subtune", 0):
        out.append("subtune %d" % model.subtune)
    prologue = getattr(model, "prologue", ())
    if prologue:
        out.append("sid-init {")
        out.extend("  $%02X = $%02X" % (r, v) for r, v in prologue)
        out.append("}")
    for pc in sorted(model.dispatch_sets):
        out.append(
            "dispatch $%04X: %s"
            % (pc, " ".join("$%02X" % v for v in sorted(model.dispatch_sets[pc])))
        )
    out.extend(_image_lines(model.mem0))
    w = _Writer(model)
    for entry in _entries(model):
        w.proc(entry)
    left = sorted(k for k in model.blocks if k not in w.done)
    while left:
        w.proc(left[0][0])
        still = sorted(k for k in model.blocks if k not in w.done)
        if len(still) == len(left):
            raise ValueError("blocks unreachable from any procedure: %s" % still[:4])
        left = still
    out.extend(_resolve_fallthrough(w.out))
    return "\n".join(out) + "\n"


def _resolve_fallthrough(lines):
    """Drop an elidable ``goto`` whose target label is the very next line."""
    out = []
    for i, l in enumerate(lines):
        if not l.startswith("\x00"):
            out.append(l)
            continue
        tgt = l.rsplit("$", 1)[1]
        nxt = lines[i + 1].lstrip() if i + 1 < len(lines) else ""
        if nxt.startswith("$%s" % tgt) and nxt.endswith(":") and nxt[len(tgt) + 1] in ":/":
            continue
        out.append(l[1:])
    return out


# ---- parsing ----------------------------------------------------------------------
class TextModel(stext.TextModel):
    """Parsed sidprog program; duck-types ``structured.Model`` for walker+codec."""

    def __init__(self, mem0, init, play, blocks, dispatch, subtune=0, prologue=(), dyn=None):
        super().__init__(mem0, init, play, blocks, dispatch, subtune, prologue)
        self.dispatch_pcs = set(dispatch)
        self.dyn_targets = dict(dyn or {})
        by_pc = {}
        for key in sorted(blocks):
            by_pc.setdefault(key[0], []).append(key)
        self._by_pc = by_pc

    def variants(self, pc):
        return self._by_pc.get(pc, ())


class _Acc(stext._BlockAccum):
    def __init__(self, pc, op0):
        super().__init__(pc, op0)
        self.bind = {}


def _t2u(line):
    return _T_REF.sub(lambda m: "u%d" % (int(m.group(1)) + _BIND_BASE), line)


def _expand(n, bind, memo):
    stack = [n]
    while stack:
        x = stack[-1]
        if id(x) in memo:
            stack.pop()
            continue
        if x[0] == "uni" and x[1] >= _BIND_BASE:
            memo[id(x)] = bind[x[1] - _BIND_BASE]
            stack.pop()
            continue
        todo = [k for k in _kids(x) if id(k) not in memo]
        if todo:
            stack.extend(todo)
            continue
        stack.pop()
        memo[id(x)] = _rebuild(x, [memo[id(k)] for k in _kids(x)])
    return memo[id(n)]


def _finish(acc, blocks):
    memo = {}

    def x(n):
        return _expand(n, acc.bind, memo)

    events = []
    for ev in acc.events:
        if ev[0] == "ld":
            events.append(("ld", ev[1], x(ev[2])))
        elif ev[0] == "st":
            events.append(("st", x(ev[1]), x(ev[2])))
        elif ev[0] == "pen":
            events.append(("pen", ev[1], x(ev[2]), x(ev[3])))
        else:
            events.append(ev)
    acc.events = events
    acc.regs = [r if r == ("reg", i) else x(r) for i, r in enumerate(acc.regs)]
    if acc.term is None:
        raise ValueError("block $%04X has no terminator" % acc.key[0])
    acc.term = _map_term(acc.term, x)
    blk = acc.finish(None)
    blocks[blk.pc, blk.op0] = blk


_LABEL = re.compile(r"\$([0-9A-Fa-f]{1,4})(?:/\$([0-9A-Fa-f]{1,2}))?:$")
_BINDING = re.compile(r"t(\d+) = (.*)$")
_CASE = re.compile(r"case \$([0-9A-Fa-f]+): \{$")
_SW_CODE = re.compile(r"switch code\[\$[0-9A-Fa-f]{4}\] \{$")
_SW_CALL = re.compile(r"switch call \{ ?(.*?) ?\}$")


def parse(text):
    """Parse canonical sidprog text into a walkable, codec-verified model."""
    lines = []
    for raw in text.splitlines():
        s = raw.split(";", 1)[0].strip()
        if s:
            lines.append(s)
    head = lines.pop(0).split() if lines else []
    if len(head) != 2 or head[0] != "sidprog" or not head[1].isdigit():
        raise ValueError("not a sidprog document")
    if int(head[1]) != SIDPROG_VERSION:
        raise SidprogVersionError(
            "sidprog major %s: this reader speaks major %d" % (head[1], SIDPROG_VERSION)
        )
    init = play = None
    subtune = 0
    mem0 = bytearray(0x10000)
    dispatch_sets = {}
    blocks = {}
    prologue = []
    dyn_targets = {}
    accs = []
    cur = None
    fall = None  # acc that may adopt an immediately following label as fallthrough
    stack = []  # open braces; a list value collects a goto-switch's case targets
    i = 0
    while i < len(lines):
        line = lines[i]
        adjacent, fall = fall, None
        if line.startswith("play "):
            play = int(line.split()[1].lstrip("$"), 16)
        elif line.startswith("init "):
            init = int(line.split()[1].lstrip("$"), 16)
        elif line.startswith("subtune "):
            subtune = int(line.split()[1])
        elif line.startswith("dispatch "):
            site, vals = line[9:].split(":")
            dispatch_sets[int(site.strip().lstrip("$"), 16)] = {
                int(v.lstrip("$"), 16) for v in vals.split()
            }
        elif line == "sid-init {":
            i += 1
            while lines[i] != "}":
                reg, val = lines[i].split("=")
                prologue.append(
                    (int(reg.strip().lstrip("$"), 16), int(val.strip().lstrip("$"), 16))
                )
                i += 1
        elif line == "image {":
            i += 1
            while lines[i] != "}":
                addr, bytestr = lines[i].split(":", 1)
                a = int(addr.strip().lstrip("$"), 16)
                run = bytestr.strip()
                for k in range(0, len(run), 2):
                    mem0[a + k // 2] = int(run[k : k + 2], 16)
                i += 1
        elif line.startswith("proc ") or line == "loop {":
            stack.append(None)
        elif line in ("}", "} else {"):
            if stack:
                stack.pop()
            if line == "} else {":
                stack.append(None)
        elif line in ("continue", "break"):
            pass
        elif line == "switch goto {":
            dyn_targets[cur.key[0]] = tgts = []
            stack.append(tgts)
        elif _SW_CODE.match(line):
            stack.append(None)
        else:
            m = _SW_CALL.match(line)
            if m:
                dyn_targets[cur.key[0]] = [int(t.lstrip("$"), 16) for t in m.group(1).split()]
                i += 1
                continue
            m = _CASE.match(line)
            if m:
                if stack and isinstance(stack[-1], list):
                    stack[-1].append(int(m.group(1), 16))
                stack.append(None)
                i += 1
                continue
            m = _LABEL.match(line)
            if m:
                pc = int(m.group(1), 16)
                op0 = int(m.group(2), 16) if m.group(2) else mem0[pc]
                if adjacent is not None and adjacent.term is None:
                    adjacent.term = ("goto", pc)  # elided fallthrough goto
                cur = _Acc(pc, op0)
                accs.append(cur)
                fall = cur
                i += 1
                continue
            m = _BINDING.match(line)
            if m:
                node = stext.parse_expr(_t2u(m.group(2)))
                cur.bind[int(m.group(1))] = _expand(node, cur.bind, {})
                fall = cur
            elif line.endswith(" {"):  # if/ifnot header: the block's terminator line
                stext._parse_line(cur, _t2u(line[:-2]))
                stack.append(None)
            elif cur is not None and cur.term is not None and line.startswith("goto "):
                pass  # region-flow echo; the terminator line already recorded it
            else:
                m = _CYC_FUSED.match(line)
                if m:
                    stext._parse_line(cur, "@" + m.group(1))
                    line = m.group(2)
                stext._parse_line(cur, _t2u(line))
                fall = cur
        i += 1
    for acc in accs:
        _finish(acc, blocks)
    if init is None or play is None:
        raise ValueError("missing init/play header")
    tm = TextModel(mem0, init, play, blocks, dispatch_sets, subtune, prologue, dyn_targets)
    codec.verify(tm)
    return tm


dumps = emit  # spec vocabulary (section 6); loads/dumps are inverses
loads = parse
