"""S7 -- Python code generation for a tuneprog, and the certificate writer.

One Python function per IR procedure over ``(S, m, *params) -> rets``: ``m`` is
the flat image and ``S`` the :class:`~.interp.Machine` that owns the marks, the write
log and the pinned input stream. Blocks are laid out along their hottest chain
(the trace's execution counts), so control usually falls through; what does not
is a label assignment dispatched by a nested ``if lbl <= i`` cascade, grouped so
a jump costs a handful of comparisons rather than a scan.

Generated code and :class:`~.interp.Interp` must agree: :mod:`.verify` checks that on
a prefix of every certified run (evidence E12).

Public API: :func:`emit_python`, :func:`compile_prog`, :class:`PyProgram`,
:func:`certificate`, :func:`write_certificate`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .. import __version__
from .ir import (
    Assert,
    Bin,
    Call,
    Const,
    copymap_bands,
    Goto,
    If,
    Let,
    MASK,
    Phi,
    REGVAR,
    Return,
    Store,
    Trap,
    Var,
    succs,
)
from .closure import report as closure_report
from .copymerge import report as copies_report
from .idioms import CMP, width

GROUP = 24
MASKED = ("+", "-", "<<")


def _san(n):
    return re.sub(r"\W", "_", n)


def _pname(n):
    return "p_" + _san(n)


class _Fn:
    """Accumulates one procedure's source and its temporaries."""

    def __init__(self):
        self.n = 0

    def tmp(self):
        self.n += 1
        return "_g%d" % self.n


def _ex(e, pre, fn):
    """``e`` as a Python expression; address guards and temps land in ``pre``."""
    t = type(e)
    if t is Const:
        return str(e.v)
    if t is Var:
        return _san(e.n)
    if t is Bin:
        a, b = _ex(e.a, pre, fn), _ex(e.b, pre, fn)
        if e.op in CMP:
            return "(1 if %s %s %s else 0)" % (a, e.op, b)
        if e.op == "carry":
            return "(1 if %s + %s > %d else 0)" % (a, b, MASK[e.w])
        if e.op in MASKED or width(e.a) > e.w or width(e.b) > e.w:
            return "((%s %s %s) & %d)" % (a, e.op, b, MASK[e.w])
        return "(%s %s %s)" % (a, e.op, b)
    a = _addr(e.a, e.lo, e.hi, e.w, pre, fn, 0)
    if e.cls == "io":
        rd = "S.ioload(%s)"
    elif e.cls == "chk":
        rd = "S.rdk(%s)"
    else:
        rd = "m[%s]"
    if e.w == 1:
        return rd % a
    return "(%s | (%s << 8))" % (rd % a, rd % _next(a))


def _next(a):
    return "%s + 1" % a if a.isdigit() else "((%s + 1) & 65535)" % a


def _addr(e, lo, hi, w, pre, fn, src):
    """Address expression, hoisted to a temp and envelope-checked when it can move."""
    a = _ex(e, pre, fn)
    if type(e) is Const:
        if not lo <= e.v <= hi - w + 1:
            pre.append("S.env(%d, %d, %d, %d)" % (e.v, lo, hi, src))
        return a
    if w > 1 or not a.isidentifier():
        t = fn.tmp()
        pre.append("%s = %s" % (t, a))
        a = t
    if lo > 0 or hi < 0xFFFF:
        pre.append(
            "if %s < %d or %s > %d: S.env(%s, %d, %d, %d)" % (a, lo, a, hi - w + 1, a, lo, hi, src)
        )
    return a


def _cond(e, pre, fn):
    """A condition without the 0/1 boxing a comparison would otherwise emit."""
    if type(e) is Bin and e.op in CMP:
        return "%s %s %s" % (_ex(e.a, pre, fn), e.op, _ex(e.b, pre, fn))
    return "%s" % _ex(e, pre, fn)


def _guard(s, a, out, bands):
    """Trap a store that could land in a per-copy column table: they are read-only."""
    for lo, hi in bands:
        if s.lo > hi or s.hi + s.w - 1 < lo:  # its envelope cannot reach the band
            continue
        if type(s.a) is Const:
            if lo <= s.a.v + s.w - 1 and s.a.v <= hi:
                out.append("S.trap('copymap', %r)" % ("$%04X at $%04X" % (s.a.v, s.src)))
            continue
        out.append(
            "if %d <= %s <= %d: S.trap('copymap', '$%%04X at $%04X' %% %s)"
            % (lo - s.w + 1, a, hi, s.src, a)
        )


def _store(s, out, fn, bands=()):
    pre = []
    a = _addr(s.a, s.lo, s.hi, s.w, pre, fn, s.src)
    v = _ex(s.v, pre, fn)
    out.extend(pre)
    if bands:
        _guard(s, a, out, bands)
    if s.cls == "io":
        out.append("S.iostore(%s, %s, %d)" % (a, v, s.src))
        return
    if s.w > 1:
        t = fn.tmp()
        out.append("%s = %s" % (t, v))
        v = t
    for i in range(s.w):
        ai = a if i == 0 else _next(a)
        vi = v if s.w == 1 else "((%s >> %d) & 255)" % (v, 8 * i)
        out.append("m[%s] = %s" % (ai, vi))
        if s.cls != "raw":
            out.append("k[%s] = 1" % ai)
            out.append("Wadd(%s)" % ai)
    if s.lo <= 1:
        out.append("S.setbank()")


def _stmts(blk, out, fn, bands=()):
    for s in blk.stmts:
        t = type(s)
        if t is Let:
            pre = []
            e = _ex(s.e, pre, fn)
            out.extend(pre)
            out.append("%s = %s" % (_san(s.n), e))
        elif t is Store:
            _store(s, out, fn, bands)
        elif t is Call:
            pre = []
            args = [_ex(a, pre, fn) for a in s.args]
            out.extend(pre)
            lhs = ", ".join(_san(r) for r in s.rets) + ("," if len(s.rets) == 1 else "")
            call = "%s(S, m%s)" % (_pname(s.proc), "".join(", " + a for a in args))
            out.append("%s = %s" % (lhs, call) if lhs else call)
        elif t is Assert:
            pre = []
            c = _cond(s.e, pre, fn)
            out.extend(pre)
            out.append("if not (%s): S.trap(%r, %r)" % (c, s.why, blk.label))
        elif t is Phi:
            raise ValueError("phi in emitted code: run ssa.from_ssa first")


def _term(blk, idx, nxt, out, fn):
    t = blk.term
    k = type(t)
    if k is Return:
        pre = []
        vals = [_ex(v, pre, fn) for v in t.vals]
        out.extend(pre)
        out.append("return (%s)" % ("".join(v + ", " for v in vals)))
    elif k is Trap:
        out.append("S.trap(%r, %r)" % (t.why, blk.label))
    elif k is Goto:
        if idx[t.to] != nxt:
            out.append("lbl = %d" % idx[t.to])
            out.append("continue")
    elif k is If:
        pre = []
        c = _cond(t.c, pre, fn)
        out.extend(pre)
        if idx[t.t] == nxt:
            out.append("if not (%s):" % c)
            out.append("    lbl = %d" % idx[t.f])
            out.append("    continue")
        else:
            out.append("if %s:" % c)
            out.append("    lbl = %d" % idx[t.t])
            out.append("    continue")
            if idx[t.f] != nxt:
                out.append("lbl = %d" % idx[t.f])
                out.append("continue")
    else:
        pre = []
        e = _ex(t.e, pre, fn)
        out.extend(pre)
        v = fn.tmp()
        out.append("%s = %s" % (v, e))
        fall = next((j for j, c in enumerate(t.cases) if idx[c[1]] == nxt), None)
        for j, (val, lab) in enumerate(t.cases):
            if j != fall:
                out.append("if %s == %d:" % (v, val))
                out.append("    lbl = %d" % idx[lab])
                out.append("    continue")
        if fall is not None:
            out.append("if %s != %d: S.trap('switch', %r)" % (v, t.cases[fall][0], blk.label))
        else:
            out.append("S.trap('switch', %r)" % blk.label)


def layout(proc):
    """Blocks ordered along their hottest chains, so control mostly falls through."""
    left = set(proc.order())
    out, work = [], [proc.entry]
    while left:
        while work and work[0] not in left:
            work.pop(0)
        cur = work.pop(0) if work else max(left, key=lambda l: proc.blocks[l].count)
        while cur in left:
            left.discard(cur)
            out.append(cur)
            nxt = [s for s in succs(proc.blocks[cur].term) if s in left]
            if not nxt:
                break
            hot = max(nxt, key=lambda s: proc.blocks[s].count)
            work.extend(s for s in nxt if s != hot)
            cur = hot
    return out


def emit_proc(proc, bands=()):
    """One IR procedure as a Python function's source lines."""
    order = layout(proc)
    idx = {lbl: i for i, lbl in enumerate(order)}
    fn = _Fn()
    args = "".join(", " + REGVAR[i] for i in proc.params)
    src = [
        "def %s(S, m%s):" % (_pname(proc.name), args),
        "    k = S.k",
        "    Wadd = S.W.add",
        "    lbl = 0",
        "    while True:",
    ]
    pad = " " * 12
    for i, lbl in enumerate(order):
        if i % GROUP == 0:
            src.append("        if lbl < %d:" % min(i + GROUP, len(order)))
        body = []
        _stmts(proc.blocks[lbl], body, fn, bands)
        _term(proc.blocks[lbl], idx, i + 1, body, fn)
        src.append("%sif lbl <= %d:" % (pad, i))
        src.extend(pad + "    " + line for line in body or ["pass"])
    src.append("        break")
    src.append("    S.trap('fallthrough', %r)" % proc.name)
    return src


def emit_python(prog):
    """The whole tuneprog as an importable Python module (source text)."""
    head = [
        '"""Generated by deity_informant.tuneprog.emit -- do not edit.',
        "",
        "%s, song %s, entry $%04X (%s)."
        % (
            prog.meta.get("name", "tuneprog"),
            prog.meta.get("song"),
            prog.meta.get("entry", {}).get("addr", 0),
            prog.meta.get("entry", {}).get("kind", "?"),
        ),
        '"""',
        "",
        "META = %r" % (prog.meta,),
        "",
    ]
    body = []
    bands = copymap_bands(prog.storage)
    for p in prog.procs.values():
        body.extend(emit_proc(p, bands))
        body.append("")
    body.append("PROCS = {%s}" % ", ".join("%r: %s" % (n, _pname(n)) for n in prog.procs))
    body.append(
        "PARAMS = {%s}" % ", ".join("%r: %r" % (n, p.params) for n, p in prog.procs.items())
    )
    body.append("RETS = {%s}" % ", ".join("%r: %r" % (n, p.rets) for n, p in prog.procs.items()))
    return "\n".join(head + body) + "\n"


def compile_prog(src, name="tuneprog"):
    """Exec ``src`` into a fresh namespace and return it."""
    ns = {"__name__": name}
    exec(compile(src, "<%s>" % name, "exec"), ns)  # noqa: S102 - generated IR code
    return ns


class PyProgram:
    """The generated module behind :class:`~.interp.Interp`'s interface."""

    def __init__(self, prog, machine, ns=None, src=None):
        self.prog = prog
        self.M = machine
        self.src = src if src is not None else emit_python(prog)
        self.ns = ns if ns is not None else compile_prog(self.src)
        self.procs = self.ns["PROCS"]

    def run(self, name, args=()):
        return self.procs[name](self.M, self.M.m, *args)


# ---- certificate (design section 7) -----------------------------------------
def certificate(prog, subtunes, cost, divergence=None, stage="S4", oracle=None, compared=None):
    """The design's ``certificate.json`` document.

    ``stack`` is ``"eliminated"`` where the program has no machine stack left, else
    the depth and the procedures that kept one (:func:`~.stack.eliminate`).
    ``copies`` is the merged families and their per-statement coverage, if any,
    ``closure`` what the bounded static walk added and what stayed trapped.
    """
    copies = copies_report(prog)
    closed = closure_report(prog)
    doc = {
        "tune": prog.meta.get("name"),
        "sid_model": prog.meta.get("sid_model"),
        "oracle": oracle or "deity_informant.PcodeVM@%s" % __version__,
        "reference_validated_against": prog.meta.get("reference_validated_against", "none"),
        "compared": compared or ["init writes", "tick sid writes", "tick schedule effects"],
        "entry": prog.meta.get("entry"),
        "subtunes": subtunes,
        "stack": prog.meta.get("stack"),
        "stage": stage,
        "divergence": divergence,
        "cost": cost,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if copies is not None:
        doc["copies"] = copies
    if closed:
        doc["closure"] = closed
    return doc


def write_certificate(path, doc):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    return path
