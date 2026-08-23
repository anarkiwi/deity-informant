"""S6 -- 16-bit views: where the byte shapes of :mod:`.halves` land in the program.

Two halves of one cell pair in one block, and the ``lo += 1; if lo == 0: hi += 1``
diamond, become one assignment over the pair; a pointer read becomes one 16-bit read.
:func:`~.recover.name_u16` names what this returns.
"""

from __future__ import annotations

from .graph import preds_of
from .halves import bumped, cell, cells, norm, operand, read, register, same, value
from .ir import Bin, Call, Const, Goto, If, Let, Load, R16, Store, Var, W16
from .irwalk import apply_stmt, apply_term, node_loads, renamer, single_defs
from .irwalk import use_counts, walk
from .structure import Blk, walk as nodewalk


def _hits(x, pair):
    """True when one access can touch a cell of ``pair``."""
    c = cell(x)
    return c in pair if c is not None else x.r in {r for r, _a in pair}


def _crosses(stmts, pair, first):
    """True when a call, a write to the pair, or a read of ``first`` separates the halves."""
    for x in stmts:
        if type(x) is Call or (type(x) is Store and (x.r < 0 or _hits(x, pair))):
            return True
        if any(_hits(y, (first,)) for y in node_loads(x)):
            return True
    return False


def _reload(b, j, both):
    """A use of a stored half's value is a read of its cell -- never of a chip register."""
    sub = [
        (s.v, Load(s.cls, s.a, 1, s.lo, s.hi, s.r))
        for s in both
        if type(s.v) is not Const and s.cls != "io"
    ]

    def fn(e):
        return next((c for v, c in sub if e == v), e)

    for x in b.stmts[j + 1 :]:
        apply_stmt(x, fn)
    apply_term(b.term, fn)


def _word(s1, s2, defs):
    """``(the one 16-bit assignment two halves make, the carries it subsumes)``."""
    if s1.cls != s2.cls:
        return None  # one word is stored through one access class
    for lo, hi in ((s1, s2), (s2, s1)):
        pair = cells(lo, hi)
        got = None if pair is None else value(pair, norm(lo.v, defs), norm(hi.v, defs))
        if got is not None:
            return W16(pair[0], pair[1], lo.a, got[0], lo.src, lo is s2), got[1]
    return None


def _partners(s, x, defs):
    """``x`` as the other half's store: itself, or the synthetic store of a computed half."""
    if type(x) is Store:
        if x.w == 1 and x.r >= 0:
            yield x
        return
    for y in walk(norm(x.e, defs)) if type(x) is Let else ():
        if type(y) is Load and (cells(s, y) or cells(y, s)):
            # a half a callee stores is addressed by the read it makes of the pair
            yield Store(s.cls, y.a, x.e, 1, y.lo, y.hi, y.r, s.src)


def _match_half(b, i, defs, taken):
    """``(index, store, word)`` of the half pairing with ``i``; a stored half wins over a value."""
    for want in (Store, Let):
        for j in range(i + 1, len(b.stmts)):
            if j in taken or type(b.stmts[j]) is not want:
                continue
            for other in _partners(b.stmts[i], b.stmts[j], defs):
                got = _word(b.stmts[i], other, defs)
                gap = () if got is None else b.stmts[i + 1 : j]
                if got is not None and not _crosses(gap, (got[0].lo, got[0].hi), cell(b.stmts[i])):
                    return j, other, got
    return None


def _stores(b, defs, pairs, flags):
    """Replace each pair of halves of one block with one 16-bit assignment."""
    at = [i for i, s in enumerate(b.stmts) if type(s) is Store and s.w == 1 and s.r >= 0]
    taken, put, dead = set(), {}, set()
    for i in at:
        hit = None if i in taken else _match_half(b, i, defs, taken)
        if hit is None:
            continue
        j, other, got = hit
        keep = b.stmts[j]
        held = Load(other.cls, other.a, 1, other.lo, other.hi, other.r)
        put[j] = [got[0]] if type(keep) is Store else [got[0], Let(keep.n, held)]
        dead |= {x.v.n for x in (b.stmts[i], keep) if type(x) is Store and type(x.v) is Var}
        taken |= {i, j}
        pairs.append(got[0])
        flags.extend(got[1])
        _reload(b, j, (b.stmts[i], other))
    if put:
        out = []
        for i, x in enumerate(b.stmts):
            out += put.get(i, () if i in taken else (x,))
        b.stmts[:] = out
    return dead


def _prune(proc, names):
    """Drop a half's value where the fold left nothing reading it."""
    uses = use_counts(proc)
    gone = {n for n in names if not uses[n]}
    for b in proc.blocks.values() if gone else ():
        b.stmts[:] = [s for s in b.stmts if not (type(s) is Let and s.n in gone)]


def _diamond(proc, lbl, base, pairs):
    """``lo += 1; if lo == 0: hi += 1`` over one pair is a 16-bit increment."""
    b = proc.blocks[lbl]
    s = b.stmts[-1] if b.stmts else None
    if type(b.term) is not If or type(s) is not Store or s.w != 1 or s.r < 0:
        return
    for arm, join, op in ((b.term.f, b.term.t, "!="), (b.term.t, b.term.f, "==")):
        blk = proc.blocks.get(arm)
        if blk is None or len(blk.stmts) != 1 or type(blk.term) is not Goto:
            continue
        up = blk.stmts[0]
        if blk.term.to != join or preds_of(proc)[arm] != [lbl] or type(up) is not Store:
            continue
        defs = _local(base, b, blk)
        pair = cells(s, up) if up.w == 1 and up.r >= 0 else None
        low = None if pair is None else bumped(pair, 0, norm(s.v, defs))
        if low is None or bumped(pair, 1, norm(up.v, defs)) is None:
            continue
        want = Bin(op, Bin("+", low, Const(1, 1), 1), Const(0), 1)
        if not same(norm(b.term.c, defs), norm(want)):
            continue
        e = Bin("+", R16(pair[0], pair[1], s.a), Const(1, 2), 2)
        b.stmts[-1] = W16(pair[0], pair[1], s.a, e, s.src)
        b.term = Goto(join)
        del proc.blocks[arm]
        pairs.append(b.stmts[-1])
        return


def _local(base, *blocks):
    """The single definitions, plus this block's own: two arms reuse a register's name."""
    out = dict(base)
    for b in blocks:
        out.update({s.n: s.e for s in b.stmts if type(s) is Let})
    return out


def _flags(proc, base, carries):
    """A name every definition of which is a folded carry keeps the flag's name."""
    seen, hit = {}, set()
    for b in proc.blocks.values():
        defs = _local(base, b)
        for s in b.stmts:
            if type(s) is Let:
                ok = any(same(norm(s.e, defs), c) for c in carries)
                a, n = seen.get(s.n, (0, 0))
                seen[s.n] = (a + ok, n + 1)
                hit |= {id(s)} if ok else set()
    gone = {n for n, (a, t) in seen.items() if a == t}
    fn = renamer({n: Var("$carry") for n in gone})
    for b in proc.blocks.values():
        b.stmts[:] = [s for s in b.stmts if not (type(s) is Let and s.n in gone)]
        for s in b.stmts:
            if id(s) in hit and s.n not in gone:
                s.e = Var("$carry")  # one arm's carry, where another arm's is not
            apply_stmt(s, fn)
        apply_term(b.term, fn)


def fold16(prog):
    """Fold every 16-bit shape into one statement; returns the ``W16`` s it made."""
    pairs = []
    for proc in prog.procs.values():
        for b in proc.blocks.values():
            for s in b.stmts:
                apply_stmt(s, read)
            apply_term(b.term, read)
        base, flags = single_defs(proc), []
        for lbl in list(proc.blocks):
            if lbl in proc.blocks:
                _diamond(proc, lbl, base, pairs)
        dead = set()
        for b in proc.blocks.values():
            dead |= _stores(b, _local(base, b), pairs, flags)
        if flags:
            _flags(proc, base, flags)
        _prune(proc, dead)
    return pairs


def _sidword(s1, s2, defs, rgn):
    """The one assignment two consecutive writes of a 16-bit SID register make.

    The value must be a word the program already holds: bytes the print would have
    to join with ``|`` and ``<< 8`` are two bytes that happen to reach one register.
    """
    if any(type(x) is not Store or x.w != 1 or x.r < 0 for x in (s1, s2)):
        return None
    for lo, hi in ((s1, s2), (s2, s1)):
        pair = cells(lo, hi)
        if pair is None or register(pair) is None:
            continue
        if any((rgn.get(c[0]) or lo).kind != "io" for c in pair):
            continue  # the RAM under the register file is memory, not the chip
        got = operand(norm(lo.v, defs), norm(hi.v, defs))
        if got is not None:
            return W16(pair[0], pair[1], lo.a, got, lo.src, lo is s2)
    return None


def fold_sid(structured, rgn):
    """Print each 16-bit SID register's two byte writes as one assignment.

    Run after the rows are aligned (:mod:`.unroll`): the chip takes two writes and
    the print takes one, so this is a convention over the rows, never a row of its
    own. Returns the ``W16`` s it made.
    """
    out = []
    for body in structured.values():
        for n in nodewalk(body):
            if type(n) is Blk:
                out += _sidblock(n, rgn)
    return out


def sidorder(words):
    """``(the order the register writes are in, how many pairs are not)``, or ``None``.

    The convention is the order more of them use; the header states it once and the
    odd statement carries its own (:meth:`~.pseudocode.Printer.against`).
    """
    if not words:
        return None
    hi = sum(w.hifirst for w in words)
    return ("hi", len(words) - hi) if hi * 2 > len(words) else ("lo", hi)


def _sidblock(b, rgn):
    """Fold the register pairs of one printed block; returns the ``W16`` s it made."""
    defs = {s.n: s.e for s in b.stmts if type(s) is Let}
    stmts, out, i = [], [], 0
    while i < len(b.stmts):
        pair = (b.stmts[i], b.stmts[i + 1], defs, rgn) if i + 1 < len(b.stmts) else None
        got = None if pair is None else _sidword(*pair)
        stmts.append(got if got is not None else b.stmts[i])
        out += [got] if got is not None else []
        i += 2 if got is not None else 1
    b.stmts[:] = stmts
    return out
