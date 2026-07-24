"""Readable transcription of a :class:`~deity_informant.kernel.Kernel`.

Data-flow view: SID writes named by register semantics, per-cell state update,
and constant-table address ranges, with shared subexpressions factored into
``let`` bindings. Table *contents* are never emitted (a cell renders ``T[$addr]``).
"""

from __future__ import annotations

from . import expr as E

_VOICE_FIELDS = ("FREQ_LO", "FREQ_HI", "PW_LO", "PW_HI", "CTRL", "AD", "SR")
_GLOBAL = {0x15: "FILT_CUT_LO", 0x16: "FILT_CUT_HI", 0x17: "RES_FILT", 0x18: "MODE_VOL"}
_REG = {0: "A", 1: "X", 2: "Y", 3: "SP", 8: "C", 9: "Z", 10: "I", 11: "D", 13: "V", 14: "N"}
_SYM = {
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_OR": "|",
    "INT_AND": "&",
    "INT_XOR": "^",
    "INT_LEFT": "<<",
    "INT_RIGHT": ">>",
    "INT_MULT": "*",
}
_CMP = {"INT_EQUAL": "==", "INT_NOTEQUAL": "!=", "INT_LESS": "<", "INT_LESSEQUAL": "<="}


def sid_name(addr):
    """Semantic name of a SID register ``$D400..$D418`` (else its hex address)."""
    off = (addr - 0xD400) & 0xFFFF
    if off < 21:
        return "V%d.%s" % (off // 7 + 1, _VOICE_FIELDS[off % 7])
    if off in _GLOBAL:
        return _GLOBAL[off]
    return "$%04X" % addr


def _runs(addrs):
    """Contiguous ``[lo, hi]`` ranges covering the sorted address set."""
    out = []
    for x in sorted(addrs):
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return out


def _kids(n):
    if n[0] == "op":
        return n[2]
    if n[0] == "mem":
        return (n[1],)
    if n[0] == "cur":
        return (n[1], n[4])
    return ()


_DEPTH = {}


def _depth(n):
    if n[0] in ("const", "reg", "uni"):
        return 1
    if n in _DEPTH:
        return _DEPTH[n]
    d = 1 + max((_depth(c) for c in _kids(n)), default=0)
    _DEPTH[n] = d
    return d


class _Printer:
    """Expression stringifier: table-content-free, with shared-subexpr naming."""

    def __init__(self, kern):
        self.kern = kern
        self.names = {}

    def name_common(self, roots, thresh=3):
        """Assign ``t#`` names to op subexpressions occurring >= ``thresh`` times."""
        cnt = {}
        stack = list(roots)
        while stack:
            n = stack.pop()
            if n[0] in ("const", "reg", "uni"):
                continue
            cnt[n] = cnt.get(n, 0) + 1
            stack.extend(_kids(n))
        cand = [n for n, c in cnt.items() if c >= thresh and n[0] == "op" and _depth(n) >= 3]
        cand.sort(key=lambda n: (_depth(n), self._raw(n)))
        for i, n in enumerate(cand):
            self.names[n] = "t%d" % i

    def _word(self, kids):
        hi = lo = None
        for c in kids:
            cc = c[2][0] if c[0] == "op" and c[1] == "INT_ZEXT" else c
            if cc[0] == "op" and cc[1] == "INT_LEFT" and E.is_const(cc[2][1]) and cc[2][1][1] == 8:
                hi = cc[2][0]
            else:
                lo = c
        return "(%s:%s)" % (self.render(hi), self.render(lo)) if hi and lo else None

    def _raw(self, n):
        saved, self.names = self.names, {}
        try:
            return self.render(n)
        finally:
            self.names = saved

    def render(self, n, top=False):
        if not top and n in self.names:
            return self.names[n]
        k = n[0]
        if k == "const":
            return "$%X" % n[1]
        if k == "reg":
            return _REG.get(n[1], "r%d" % n[1])
        if k == "uni":
            return "U%d" % n[1]
        if k == "mem":
            a = n[1]
            if E.is_const(a):
                return "%s[$%04X]" % ("S" if a[1] in self.kern.state else "T", a[1])
            return "T[%s]" % self.render(a)
        if k == "cur":
            return self.render(E.to_entry(n))
        mn, kids = n[1], n[2]
        if mn in ("INT_ZEXT", "COPY"):
            return self.render(kids[0])
        if mn == "INT_OR" and len(kids) == 2:
            w = self._word(kids)
            if w:
                return w
        sym = _SYM.get(mn) or _CMP.get(mn)
        if sym:
            return "(" + (" %s " % sym).join(self.render(c) for c in kids) + ")"
        return "%s(%s)" % (mn.replace("INT_", "").lower(), ", ".join(self.render(c) for c in kids))


def _collect(kern):
    """``(writes, updates)`` maps of ``rep_addr/cell -> [entry-pure exprs]``."""
    writes, updates = {}, {}
    for v in kern.variants:
        for _saddr, ex, _sz, is_out, rep in v.sslog:
            if is_out:
                writes.setdefault(rep, []).append(E.simplify(E.to_entry(ex)))
        for addr, ex in v.transition.items():
            updates.setdefault(addr, []).append(E.simplify(E.to_entry(ex)))
    return writes, updates


def _block(head, exprs):
    pad = " " * len(head)
    return [head + exprs[0]] + [pad + e for e in exprs[1:]]


def transcribe(kern, name="tune"):
    """Render ``kern`` as a readable, table-content-free transcription string."""
    writes, updates = _collect(kern)
    pr = _Printer(kern)
    roots = [e for lst in writes.values() for e in lst]
    roots += [e for lst in updates.values() for e in lst]
    pr.name_common(roots)

    def rows(items, fmt):
        out = []
        for key in sorted(items):
            exprs = sorted({pr.render(e) for e in items[key]})
            out += _block(fmt(key), exprs)
        return out

    lines = [
        "; ===== %s =====" % name,
        "; state=%d cells  tables=%d const cells  paths=%d"
        % (len(kern.state), len(kern.tables), len(kern.variants)),
    ]
    if pr.names:
        lines += ["", "LET  (shared subexpressions):"]
        for n, nm in sorted(pr.names.items(), key=lambda kv: int(kv[1][1:])):
            lines.append("  %s = %s" % (nm, pr.render(n, top=True)))
    lines += ["", "SID WRITES  (register <- value; extra lines are data-dependent variants):"]
    lines += rows(writes, lambda r: "  %-12s $%04X <- " % (sid_name(r), r))
    lines += ["", "STATE UPDATE  (cell <- next value):"]
    lines += rows(updates, lambda a: "  S[$%04X] <- " % a)
    lines += ["", "TABLES  (constant data read at a constant address; ranges only):"]
    for lo, hi in _runs(kern.tables):
        lines.append("  $%04X..$%04X  (%d bytes)" % (lo, hi, hi - lo + 1))
    lines.append("  (indexed tables appear as T[...] reads above)")
    return "\n".join(lines)
