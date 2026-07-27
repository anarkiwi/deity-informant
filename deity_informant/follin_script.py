"""Follin command-script lane: decode the per-voice sequencer script.

Decodes the operand grammar validated in docs/follin-dispatch-study.md from the
graph-recovered zp script pointers, following call/jump control flow, and
certifies every consumed byte was observed fetched. See docs/tracker.md §4."""

from collections import namedtuple

from . import streams

_ARITY = {
    0x80: 3,
    0x81: 0,
    0x82: 1,
    0x83: 1,
    0x84: 1,
    0x86: 0,
    0x87: 2,
    0x88: 8,
    0x89: 0,
    0x8A: 2,
    0x8B: 0,
    0x8C: 1,
    0x8D: 1,
    0x8E: 4,
    0x8F: 4,
    0x90: 1,
    0x91: 3,
    0x92: 1,
    0x93: 0,
    0x94: 2,
}
_NAME = {
    0x80: "slide",
    0x81: "loopend",
    0x82: "loop",
    0x83: "gatelen",
    0x84: "durmode",
    0x85: "rawsid",
    0x86: "stop",
    0x87: "jump",
    0x88: "pulse",
    0x89: "pulsesel",
    0x8A: "call",
    0x8B: "ret",
    0x8C: "transpose",
    0x8D: "wave",
    0x8E: "vibrato",
    0x8F: "noise",
    0x90: "gateoff",
    0x91: "detune",
    0x92: "porta",
    0x93: "tie",
    0x94: "slidedef",
}
_END = frozenset((0x86, 0x8B))

Op = namedtuple("Op", "addr name args")
Script = namedtuple("Script", "pair base ops patterns consumed certified")


def script_bases(model):
    """{(zp_lo, zp_hi): base} for every zero-page pointer pair `streams` finds."""
    pairs = {}
    for e in streams.streams(model):
        if e["kind"] != "pointer":
            continue
        b = e["base"]
        if isinstance(b, tuple) and len(b) == 2 and all(isinstance(x, tuple) for x in b):
            lo, hi = b[0][0], b[1][0]
            if lo < 0x100 and hi < 0x100:
                pairs[(lo, hi)] = model.mem0[lo] | (model.mem0[hi] << 8)
    return pairs


def _decode_seg(mem0, addr, reads, starts, consumed):
    """Linearly decode one script segment from ``addr`` until a terminator."""
    ops, calls, sticky = [], [], None

    def eat(a, n):
        for k in range(a, a + n):
            consumed.add(k & 0xFFFF)

    while addr in reads and addr not in starts:
        starts.add(addr)
        b = mem0[addr]
        if b < 0x80:
            ln = 2 if sticky is None else 1
            dur = mem0[(addr + 1) & 0xFFFF] if sticky is None else sticky
            eat(addr, ln)
            ops.append(Op(addr, "note", (b, dur)))
            addr += ln
            continue
        if b == 0x85:
            i, pairs = addr + 1, []
            while mem0[i & 0xFFFF] < 0x80:
                pairs.append((mem0[i & 0xFFFF], mem0[(i + 1) & 0xFFFF]))
                i += 2
            i += 1
            eat(addr, i - addr)
            ops.append(Op(addr, "rawsid", tuple(pairs)))
            addr = i
            continue
        n = _ARITY.get(b)
        if n is None:
            ops.append(Op(addr, "bad", (b,)))
            break
        args = tuple(mem0[(addr + 1 + k) & 0xFFFF] for k in range(n))
        eat(addr, 1 + n)
        if b == 0x84:
            sticky = args[0] or None
        if b == 0x8A:
            calls.append(args[0] | (args[1] << 8))
        ops.append(Op(addr, _NAME[b], args))
        if b == 0x87:
            addr = args[0] | (args[1] << 8)
            continue
        if b in _END:
            break
        addr += 1 + n
    return ops, calls


def _decode(model, base):
    """Decode the top-level script at ``base`` plus its call-target patterns."""
    mem0 = model.mem0
    reads = {a for _pc, a in model.reads}
    starts, consumed = set(), set()
    top, calls = _decode_seg(mem0, base, reads, starts, consumed)
    patterns, work = {}, list(calls)
    while work:
        t = work.pop()
        if t in patterns or t not in reads:
            continue
        patterns[t], more = _decode_seg(mem0, t, reads, starts, consumed)
        work.extend(more)
    certified = consumed <= reads and not any(o.name == "bad" for o in top)
    return top, patterns, consumed, certified


def decode(model, min_ops=4):
    """Decode every zp pointer stream that is a clean, non-trivial script."""
    out = []
    for pair, base in sorted(script_bases(model).items()):
        top, patterns, consumed, certified = _decode(model, base)
        if certified and len(top) >= min_ops:
            out.append(Script(pair, base, top, patterns, consumed, certified))
    return out


def _fmt_op(op):
    if op.name == "note":
        return "note %02X dur=%d" % (op.args[0], op.args[1])
    if op.name == "rawsid":
        return "rawsid " + " ".join("$D4%02X=%02X" % (r, v) for r, v in op.args)
    if op.name in ("call", "jump") and len(op.args) == 2:
        return "%s $%04X" % (op.name, op.args[0] | (op.args[1] << 8))
    return op.name + ("".join(" %02X" % a for a in op.args) if op.args else "")


def render(script):
    """Readable lane text for one Script (top-level then called patterns)."""
    lines = [
        "; voice zp $%02X/$%02X  base $%04X  (%d ops, %d patterns)"
        % (script.pair[0], script.pair[1], script.base, len(script.ops), len(script.patterns))
    ]
    lines += ["%04X: %s" % (o.addr, _fmt_op(o)) for o in script.ops]
    for t, ops in sorted(script.patterns.items()):
        lines.append("pattern $%04X:" % t)
        lines += ["  %04X: %s" % (o.addr, _fmt_op(o)) for o in ops]
    return "\n".join(lines)
