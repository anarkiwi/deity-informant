"""Canonical, parseable, round-tripping IR for a recorded tune.

``to_ir(kern)`` serializes the kernel to the :data:`GRAMMAR` S-expression program;
``parse_ir(text)`` rebuilds the exact expression tuples and ``Program.run(n)``
self-drives them, byte-exact against the original SID writes.
"""

from __future__ import annotations

import sys

from . import expr as E

GRAMMAR = r"""
program := '(tune' outputs tables init '(frame' node* '))'
outputs := '(outputs' (lo hi)* ')'                     ; observable address ranges
tables  := '(tables' '(t' addr byte* ')'* ')'          ; constant data read
init    := '(init' '(s' addr byte ')'* ')'             ; initial state S0
node    := '(w' addr expr size ')'                     ; store value at fixed addr
         | '(sw' expr '(case' val node* ')'* ')'       ; branch on a predicate value
expr    := '(c' val size ')'                           ; constant
         | '(r' idx ')'                                ; entry register
         | '(u' n size ')'                             ; volatile / opaque input
         | '(m' expr size ')'                          ; entry-snapshot load
         | '(v' expr size ')'                          ; evolving-image load
         | '(o' MNEMONIC size expr* ')'                ; P-code operation
all integers are hexadecimal
"""


# ---- expression (de)serialization -- faithful to the expr algebra ------------
def _emit(n):
    k = n[0]
    if k == "const":
        return "(c %x %d)" % (n[1], n[2])
    if k == "reg":
        return "(r %d)" % n[1]
    if k == "uni":
        return "(u %d %d)" % (n[1], n[2])
    if k == "mem":
        return "(m %s %d)" % (_emit(n[1]), n[2])
    if k == "cur":
        return "(v %s %d)" % (_emit(n[1]), n[2])
    if k == "op":
        return "(o %s %d %s)" % (n[1], n[3], " ".join(_emit(c) for c in n[2]))
    raise ValueError(k)


def _build(s):
    tag = s[0]
    if tag == "c":
        return ("const", int(s[1], 16), int(s[2]))
    if tag == "r":
        return ("reg", int(s[1]))
    if tag == "u":
        return ("uni", int(s[1], 16), int(s[2]))
    if tag == "m":
        return ("mem", _build(s[1]), int(s[2]))
    if tag == "v":
        return ("cur", _build(s[1]), int(s[2]), 0, ("const", 0, 1))
    if tag == "o":
        return ("op", s[1], tuple(_build(c) for c in s[3:]), int(s[2]))
    raise ValueError(tag)


# ---- S-expression reader -----------------------------------------------------
def _tokens(text):
    lines = [ln.split(";", 1)[0] for ln in text.splitlines()]
    return " ".join(lines).replace("(", " ( ").replace(")", " ) ").split()


def _read(tokens):
    """Parse the whole token stream into one nested list (iterative)."""
    stack = [[]]
    for t in tokens:
        if t == "(":
            node = []
            stack[-1].append(node)
            stack.append(node)
        elif t == ")":
            stack.pop()
        else:
            stack[-1].append(t)
    return stack[0][0]


# ---- decision tree from the recorder's interleaved event streams -------------
def _build_tree(streams):
    root = {"ch": {}}
    for ev in streams:
        node = root
        for e in ev:
            key = ("S", e[1], e[2], e[3]) if e[0] == "store" else ("G", e[1], e[2], e[3], e[4])
            node = node["ch"].setdefault(key, {"ch": {}, "e": e})
    return root


def _tree_to_stmts(node):
    """Flatten the decision tree into the executable statement structure."""
    stmts = []
    while True:
        ch = node["ch"]
        if not ch:
            return stmts
        keys = list(ch)
        if keys[0][0] == "S":
            if len(keys) != 1:
                raise ValueError("nondeterministic store")
            e = ch[keys[0]]["e"]
            stmts.append(("w", e[1], e[2], e[3]))
            node = ch[keys[0]]
            continue
        cases = {ch[key]["e"][4]: _tree_to_stmts(ch[key]) for key in keys}
        stmts.append(("sw", ch[keys[0]]["e"][3], cases))
        return stmts


def _emit_stmts(stmts, out, ind):
    for st in stmts:
        if st[0] == "w":
            out.append("%s(w %x %s %d)" % (ind, st[1], _emit(st[2]), st[3]))
        else:
            out.append("%s(sw %s" % (ind, _emit(st[1])))
            for val, body in st[2].items():
                out.append("%s  (case %x" % (ind, val))
                _emit_stmts(body, out, ind + "    ")
                out.append("%s  )" % ind)
            out.append("%s)" % ind)


def _read_set(stmts, kern):
    """Exact set of addresses the executor reads (run once on the full image)."""
    rec = kern.rec
    image = bytearray(rec.entry[0][0])
    reads, orig = set(), E._load

    def logged(buf, addr, sz):
        reads.update((addr + j) & 0xFFFF for j in range(sz))
        return orig(buf, addr, sz)

    E._load = logged
    try:
        for i in range(len(rec.events)):
            _run(stmts, bytes(image), list(rec.entry[i][1]), image, rec._uni[i], kern.outputs, [])
    finally:
        E._load = orig
    return reads | set(kern.state)


def _ranges(addrs):
    out = []
    for a in sorted(addrs):
        if out and a == out[-1][1] + 1:
            out[-1][1] = a
        else:
            out.append([a, a])
    return out


def to_ir(kern, name="tune"):
    """Serialize ``kern`` to a canonical round-tripping IR string."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 100000))
    rec = kern.rec
    entry0 = rec.entry[0][0]
    streams, seen = [], set()
    for ev in rec.events:
        if id(ev) not in seen:
            seen.add(id(ev))
            streams.append(ev)
    stmts = _tree_to_stmts(_build_tree(streams))
    seed = _read_set(stmts, kern)
    lines = ["(tune ; %s" % name]
    lines.append(
        "  (outputs %s)" % " ".join("%x %x" % (lo, hi) for lo, hi in _ranges(kern.outputs))
    )
    lines.append("  (tables")
    for lo, hi in _ranges(seed):
        data = " ".join("%x" % entry0[a] for a in range(lo, hi + 1))
        lines.append("    (t %x %s)" % (lo, data))
    lines.append("  )")
    lines.append("  (init %s)" % " ".join("(s %x %x)" % (a, v) for a, v in sorted(kern.s0.items())))
    lines.append("  (frame")
    _emit_stmts(stmts, lines, "    ")
    lines.append("  ))")
    return "\n".join(lines)


# ---- parsed program + self-driving executor ---------------------------------
class Program:
    """A parsed canonical IR program; :meth:`run` self-drives the write stream."""

    def __init__(self, outputs, seed, frame):
        self.outputs = outputs
        self.seed = seed
        self.frame = frame

    def run(self, nframes, uni=None, regs=None):
        """Execute ``nframes`` frames from the seed; return ordered output writes."""
        sys.setrecursionlimit(max(sys.getrecursionlimit(), 100000))
        image = bytearray(0x10000)
        for a, b in self.seed.items():
            image[a] = b
        out = []
        for i in range(nframes):
            frozen = bytes(image)
            u = (uni[i] if uni else {}) or {}
            rg = (regs[i] if regs else None) or [0] * 16
            _run(self.frame, frozen, rg, image, u, self.outputs, out)
        return out


def _run(stmts, frozen, regs, image, uni, outputs, out):
    for st in stmts:
        if st[0] == "w":
            _, addr, ex, sz = st
            v = E.evaluate(ex, frozen, regs, image, uni)
            for k in range(sz):
                image[(addr + k) & 0xFFFF] = (v >> (8 * k)) & 0xFF
            if addr in outputs:
                out.append((addr, v))
        else:
            val = E.evaluate(st[1], frozen, regs, image, uni)
            body = st[2].get(val)
            if body is None:
                raise KeyError("no case for %r at switch" % val)
            _run(body, frozen, regs, image, uni, outputs, out)
            return


def _parse_frame(nodes):
    stmts = []
    for nd in nodes:
        if nd[0] == "w":
            stmts.append(("w", int(nd[1], 16), _build(nd[2]), int(nd[3])))
        else:
            cases = {}
            for case in nd[2:]:
                cases[int(case[1], 16)] = _parse_frame(case[2:])
            stmts.append(("sw", _build(nd[1]), cases))
    return stmts


def parse_ir(text):
    """Parse a canonical IR string into an executable :class:`Program`."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 100000))
    sexpr = _read(_tokens(text))
    sections = {s[0]: s for s in sexpr[1:] if isinstance(s, list)}
    outputs, nums = set(), [int(x, 16) for x in sections["outputs"][1:]]
    for lo, hi in zip(nums[0::2], nums[1::2]):
        outputs.update(range(lo, hi + 1))
    seed = {}
    for t in sections["tables"][1:]:
        base = int(t[1], 16)
        for k, b in enumerate(t[2:]):
            seed[base + k] = int(b, 16)
    for s in sections["init"][1:]:
        seed[int(s[1], 16)] = int(s[2], 16)
    return Program(outputs, seed, _parse_frame(sections["frame"][1:]))


def roundtrip(kern, nframes=None, self_contained=False):
    """``(ok, ir_text)``: parse ``to_ir(kern)`` and check it reproduces the outputs.

    Feeds the recorded per-frame entry registers / volatile inputs (the frame-entry
    environment) unless ``self_contained``, which drives from ``S0`` + tables alone.
    """
    rec = kern.rec
    n = nframes if nframes is not None else len(rec.events)
    ir = to_ir(kern)
    if self_contained:
        uni = regs = None
    else:
        uni = [rec._uni[i] for i in range(n)]
        regs = [list(rec.entry[i][1]) for i in range(n)]
    try:
        got = parse_ir(ir).run(n, uni=uni, regs=regs)
    except KeyError:
        return False, ir
    want = [w for i in range(n) for w in rec.replay(i)]
    return got == want, ir
