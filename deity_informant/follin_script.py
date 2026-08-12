"""Test-only witness that the hand ``_ARITY`` table is discharged.

No production path imports it: it decodes a Follin per-voice script from the
recovered zp pointers at lengths ``opdispatch`` reads off the model, so the study
(docs/follin-dispatch-study.md) survives as ``_NAME``'s labels and as no length.
"""

from collections import namedtuple

from . import opdispatch, streams

_TURNS = 128  # decoded-length turns before a variable-arity op reads as junk
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
_STICKY, _JUMP, _CALL = 0x84, 0x87, 0x8A

Op = namedtuple("Op", "addr name args")
Script = namedtuple("Script", "pair base ops patterns consumed certified")
Grammar = namedtuple("Grammar", "arity escape")


def grammar(model):
    """The model's own operator lengths: constant arities and decoded escapes."""
    arity, escape, _refused = opdispatch.operators(model)
    return Grammar(arity, escape)


def _escaped(mem0, addr, esc):
    """``(length, operand pairs)`` of a decoded-length op, counted as its arm counts.

    The arm tests the byte at ``first + k*stride``; the first one outside
    ``cont`` ends the operand run, ``trailer`` bytes later. One turn spans
    ``first - 1`` bytes, so only ``(2, 3)`` renders as ``(reg, val)`` pairs."""
    i = addr + esc.first
    for turn in range(_TURNS):
        if mem0[i & 0xFFFF] not in esc.cont:
            return i + esc.trailer - addr, tuple(
                (
                    mem0[(addr + 1 + esc.stride * j) & 0xFFFF],
                    mem0[(addr + 2 + esc.stride * j) & 0xFFFF],
                )
                for j in range(turn + 1)
            )
        i += esc.stride
    return None, ()


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


def _decode_seg(mem0, addr, reads, starts, consumed, gram):
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
        esc = gram.escape.get(b)
        if esc is not None:
            ln, pairs = _escaped(mem0, addr, esc)
            if ln is None or (esc.stride, esc.first) != (2, 3):
                ops.append(Op(addr, "bad", (b,)))
                break
            eat(addr, ln)
            ops.append(Op(addr, _NAME.get(b, "op%02X" % b), pairs))
            addr += ln
            continue
        n = gram.arity.get(b)
        if n is None:
            ops.append(Op(addr, "bad", (b,)))
            break
        args = tuple(mem0[(addr + 1 + k) & 0xFFFF] for k in range(n))
        eat(addr, 1 + n)
        if b == _STICKY and n == 1:
            sticky = args[0] or None
        if b == _CALL and n == 2:
            calls.append(args[0] | (args[1] << 8))
        ops.append(Op(addr, _NAME.get(b, "op%02X" % b), args))
        if b == _JUMP and n == 2:
            addr = args[0] | (args[1] << 8)
            continue
        if b in _END:
            break
        addr += 1 + n
    return ops, calls


def _decode(model, base, gram):
    """Decode the top-level script at ``base`` plus its call-target patterns."""
    mem0 = model.mem0
    reads = {a for _pc, a in model.reads}
    starts, consumed = set(), set()
    top, calls = _decode_seg(mem0, base, reads, starts, consumed, gram)
    patterns, work = {}, list(calls)
    while work:
        t = work.pop()
        if t in patterns or t not in reads:
            continue
        patterns[t], more = _decode_seg(mem0, t, reads, starts, consumed, gram)
        work.extend(more)
    certified = consumed <= reads and not any(o.name == "bad" for o in top)
    return top, patterns, consumed, certified


def decode(model, min_ops=4):
    """Decode every zp pointer stream that is a clean, non-trivial script."""
    gram = grammar(model)
    if not gram.arity:
        return []  # no script VM dispatch: no operator lengths to decode with
    out = []
    for pair, base in sorted(script_bases(model).items()):
        top, patterns, consumed, certified = _decode(model, base, gram)
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
