"""S2c -- sibling copies: k static copies of one template, aligned pc by pc.

Bases are the chain the built procedures carry, a pair of copies is one gapped
opcode alignment, and a family holds while every copy's operand map is a function.

Public API: :func:`correspond`, :func:`align`, :func:`chains`, :class:`Copies`.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from difflib import SequenceMatcher

from jennings.opcodes import MEM_MODES, MODE_LEN, OPCODES as OPS

from .ir import Switch, succs
from .jumptab import _cell, _defs, _source, _target, _writers

LEAVE = ("JMP", "RTS", "RTI", "BRK")  # after one of these, straight-line code has ended


def ilen(image, pc):
    """The length in bytes of the instruction at ``pc``."""
    return MODE_LEN[OPS[image[pc]][1]]


def operand(image, pc):
    """The address the instruction at ``pc`` names, or ``None`` when it names none."""
    mode = OPS[image[pc]][1]
    if mode not in MEM_MODES:
        return None
    return image[pc + 1] if MODE_LEN[mode] == 2 else image[pc + 1] | (image[pc + 2] << 8)


def stream(image, pc, hi, n=None, data=()):
    """The instruction addresses from ``pc`` below ``hi``, at most ``n`` of them.

    A stream is code: it also stops where control has left and the access relation
    gives the next byte to a region -- a handler's own cells sit past its jump.
    """
    out, left = [], False
    while pc < hi and not (left and pc in data) and (n is None or len(out) < n):
        out.append(pc)
        left = OPS[image[pc]][0] in LEAVE
        pc += MODE_LEN[OPS[image[pc]][1]]
    return out


def _data(prog, band):
    """Every address in ``band`` a region owns: where a stream stops being code."""
    return {
        a
        for r in prog.storage
        if r.id >= 0
        for a in range(max(r.base, band[0]), min(r.base + r.size, band[1]))
    }


def _stop(stops, pc, band):
    """Where a stream that starts at ``pc`` must stop: the next base, or the band."""
    return min([s for s in stops if s > pc] + [band[1]])


def _match(image, xs, ys, whole=True):
    """Aligned index pairs of two instruction streams, ``None`` over a replacement.

    An opcode byte is its ``(opcode, mode)`` pair. Copies of one template differ
    by gaps (Follin's ``CMP #v``); opcodes that replace each other are different
    code. Where only a prefix is asked for, the first such difference ends it.
    """
    sm = SequenceMatcher(None, [image[p] for p in xs], [image[p] for p in ys], autojunk=False)
    out = []
    for tag, i1, i2, j1, _j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend((i, j1 + i - i1) for i in range(i1, i2))
        elif tag == "replace":
            return None if whole else out
        elif tag == "delete" and not whole:
            return out
    return out


def align(image, a, b, stops=frozenset(), band=(0, 0x10000), data=()):
    """``[(pc_a, pc_b)]``: the streams at ``a`` and ``b``, aligned; ``[]`` when they differ.

    Either stream stops at the next base in ``stops``, so one copy never aligns
    with the next.
    """
    xs = stream(image, a, _stop(stops, a, band), data=data)
    ys = stream(image, b, _stop(stops, b, band), data=data)
    rows = _match(image, xs, ys)
    return [(xs[i], ys[j]) for i, j in rows] if rows else []


@dataclass
class Copies:
    """k static copies of one template: their entries, and rows of aligned pcs.

    An instruction one copy has and another has not (Follin's ``CMP #v``) has no
    row.
    """

    bases: tuple
    rows: tuple = ()
    proc: str = ""

    @property
    def k(self):
        return len(self.bases)

    def pcmap(self, j):
        """``{pc of copy 0: pc of copy j}``."""
        return {r[0]: r[j] for r in self.rows}

    def addrmap(self, image, j):
        """``{(mode, address) copy 0 names: the address copy j names}``, or ``None``.

        A mode is part of what an operand names: an indexed base whose index is
        data (Follin's ``STA $D400,X``) is not the address the same literal is
        under ``abs``. Rows that disagree are an ambiguity, and refuse the family.
        """
        out = {}
        for r in self.rows:
            a, b = operand(image, r[0]), operand(image, r[j])
            if a is None or b is None:
                continue
            if out.setdefault((OPS[image[r[0]]][1], a), b) != b:
                return None
        return out

    def consistent(self, image):
        """True when every copy's operand map is a function over every aligned row."""
        return all(self.addrmap(image, j) is not None for j in range(1, self.k))

    def spans(self):
        """The pcs each copy covers, one set per copy."""
        return [{r[j] for r in self.rows} for j in range(self.k)]


# ---- chained families --------------------------------------------------------
@dataclass
class Cfg:
    """What one procedure says about copies: its entries, edges, runs and boundaries.

    Every clone of an entry is that entry: the trace-closed product holds a block
    once per path through it, and they are one instruction all the same.
    """

    srcs: list  # sorted block entries
    edges: dict  # entry -> [(entry with an edge into it, its lowest, its highest successor)]
    runs: list  # sorted (entry, times it ran), the entries an execution reached
    bounds: list  # every instruction address of the procedure

    def after(self, pc):
        """The first block entry at or after ``pc``, or ``None``.

        A copy ends at the image of the template's last instruction; what a copy
        holds past that is its own, and the next copy opens the next block.
        """
        i = bisect_left(self.srcs, pc)
        return self.srcs[i] if i < len(self.srcs) else None

    def enters(self, a, b, c):
        """The blocks of ``[a, b)`` that exit into the block at ``b``, the copy ``[b, c)``.

        An exit leaves one copy for the next; a branch that reaches past the copy
        it enters steps over that code instead of ending a copy of it.
        """
        return [s for s, lo, hi in self.edges.get(b, ()) if a <= s < b and a <= lo and hi < c]

    def ran(self, pc):
        """How often the block holding ``pc`` ran: the executed entry nearest below it.

        A run enters every copy of it as often as the first, which is what an
        unrolled loop is; a branch that skips one enters it less often.
        """
        i = bisect_right(self.runs, (pc, 1 << 64)) - 1
        return self.runs[i][1] if i >= 0 else 0


def _cfg(proc, image):
    """The :class:`Cfg` of one procedure."""
    to, edges, ran = {}, {}, {}
    for b in proc.blocks.values():
        to.setdefault(b.src, set()).update(
            proc.blocks[s].src for s in succs(b.term) if s in proc.blocks
        )
        ran[b.src] = ran.get(b.src, 0) + b.count
    for s, out in to.items():
        for t in out:
            edges.setdefault(t, []).append((s, min(out), max(out)))
    srcs = sorted(to)
    bounds = set(srcs)
    for s, nxt in zip(srcs, srcs[1:]):
        bounds.update(stream(image, s, nxt))
    return Cfg(srcs, edges, sorted((s, n) for s, n in ran.items() if n), sorted(bounds))


def chained(proc, image, fam):
    """True when every copy exits into the next copy's entry block, having run as often.

    That is what an unrolled run is: voice *j* ends by jumping to voice *j+1*.
    Nothing is asked of the last copy, whose exit leaves the run.
    """
    cfg = _cfg(proc, image)
    ends = list(fam.bases[1:]) + [max(r[-1] for r in fam.rows) + 1]
    return len({cfg.ran(b) for b in fam.bases}) == 1 and all(
        cfg.enters(fam.bases[j], fam.bases[j + 1], ends[j + 1]) for j in range(fam.k - 1)
    )


def _copy(image, xs, b, hi, whole=True):
    """``(rows, end)`` -- the window at ``b`` the template ``xs`` aligns into, and its end.

    The window grows by the template it has not reached yet, while growing reaches
    further. A copy holds the whole template, in order; the run's last copy holds
    it from the start as far as the run goes, and ends at the last row.
    """
    ys = stream(image, b, hi, len(xs))
    rows = _match(image, xs, ys, whole)
    while whole and rows and rows[-1][0] < len(xs) - 1:
        more = stream(image, b, hi, len(ys) + len(xs) - 1 - rows[-1][0])
        if len(more) == len(ys):
            break
        got = _match(image, xs, more)
        if not got or got[-1][0] <= rows[-1][0]:
            break
        ys, rows = more, got
    if not rows or len(rows) != (len(xs) if whole else rows[-1][0] + 1):
        return None
    ys = ys[: rows[-1][1] + 1]
    return [(xs[i], ys[j]) for i, j in rows], ys[-1] + ilen(image, ys[-1])


def _chain(image, cfg, band, first):
    """``(bases, [pc map per step])`` of the run the pair ``first`` opens, or ``None``.

    Copy *j+1*'s extent comes out of the alignment, so the base after it is not
    guessed: the run goes on exactly while that address is a block entry the copy
    before it has an edge into.
    """
    bases, steps = list(first), []
    while True:
        xs = stream(image, bases[-2], bases[-1])
        got = _copy(image, xs, bases[-1], band[1])
        last = got is None
        if last and steps:  # only the copy nothing follows may hold the template in part
            got = _copy(image, xs, bases[-1], band[1], whole=False)
        out = [] if got is None else cfg.enters(bases[-2], bases[-1], got[1])
        rows = dict(got[0]) if got is not None else {}
        if not out or (last and not any(u in rows for u in out)):
            bases.pop()
            return None if len(bases) < 2 else _before(image, cfg, band, (bases, steps))
        steps.append(rows)
        end = None if last else cfg.after(got[1])
        if end is None or image[end] != image[bases[-1]] or cfg.ran(end) != cfg.ran(bases[-1]):
            return _before(image, cfg, band, (bases, steps))
        bases.append(end)


def _before(image, cfg, band, run):
    """The run with the copies before it, which need not open a block of their own.

    The first copy is fallen into, so S2b may have merged its opening instruction
    into the block before it; the copies after it are jumped to and cannot be.
    """
    bases, steps = run
    while True:
        # a copy holds the whole template, so it is no longer than the one after it
        n = len(stream(image, bases[0], bases[1]))
        hit = None
        for a in [p for p in cfg.bounds if p < bases[0]][-n:]:
            if image[a] != image[bases[0]]:
                continue
            got = _copy(image, stream(image, a, bases[0]), bases[0], band[1])
            if (
                got is not None
                and cfg.after(got[1]) == bases[1]
                and cfg.enters(a, bases[0], got[1])
            ):
                hit = (a, dict(got[0]))
                break
        if hit is None:
            return tuple(bases), steps
        bases, steps = [hit[0]] + bases, [hit[1]] + steps


def _rows(bases, steps):
    """The rows of a run: a pc of copy 0 that every step maps on."""
    out = []
    for p in sorted(steps[0]):
        row = [p]
        for m in steps:
            q = m.get(row[-1])
            if q is None:
                break
            row.append(q)
        if len(row) == len(bases):
            out.append(tuple(row))
    return tuple(out)


def chains(prog, image, band):
    """Every chained sibling family of ``prog``, widest first, non-overlapping.

    A copy runs to where the next one starts, so its stream needs no bound of its
    own; only the arms :func:`extend` pairs are bounded by something coarser.
    """
    cands = []
    for name, proc in prog.procs.items():
        cfg, seen = _cfg(proc, image), set()
        srcs = cfg.srcs
        for i, a in enumerate(srcs):
            for b in srcs[i + 1 :]:
                if (a, b) in seen or image[a] != image[b] or cfg.ran(a) != cfg.ran(b):
                    continue
                if not cfg.enters(a, b, band[1]):
                    continue
                got = _chain(image, cfg, band, (a, b))
                if got is None:
                    continue
                seen.update(zip(got[0], got[0][1:]))
                fam = Copies(got[0], _rows(*got), name)
                if fam.rows and fam.consistent(image):
                    cands.append(fam)
    return _select(cands)


def _select(cands):
    """The widest, longest families whose copies do not overlap an accepted one's."""
    out, taken = [], set()
    for fam in sorted(cands, key=lambda f: (-f.k, -len(f.rows), f.bases, f.proc)):
        span = {(fam.proc, p) for r in fam.rows for p in r}
        if span & taken:
            continue
        out.append(fam)
        taken |= span
    return out


# ---- extension through the parallel dispatch tables --------------------------
def _dispatch(proc, rgn, image, band):
    """``{block entry: {table index: arm target}}`` of every patched jump a table writes.

    The k copies' dispatches are parallel tables, so an arm's index in its own
    table is what pairs it with the arm a sibling copy dispatches. An arm two
    indices reach names no one index, and pairs with nothing.
    """
    defs = _defs(proc)
    writers = _writers(proc, defs)
    out = {}
    for b in proc.blocks.values():
        hit = _cell(b.term, defs) if type(b.term) is Switch else None
        if hit is None:
            continue
        cell, width, base = hit
        srcs = [_source(cell + k, writers, rgn, image) for k in range(width)]
        tabs = [s for s in srcs if s and s[0] == "table"]
        if not all(srcs) or not tabs or len({t[3] for t in tabs}) != 1:
            continue
        cases = {v for v, _l in b.term.cases}
        hits = {}
        for x in range(0x100):  # the index register is an unsigned byte
            if any(not band[0] <= t[2] + x < band[1] for t in tabs):
                continue
            t = _target(srcs, x, image, base)
            if t in cases:
                hits.setdefault(t, []).append(x)
        out.setdefault(b.src, {}).update({x[0]: t for t, x in hits.items() if len(x) == 1})
    return out


def _group(image, group, stops, band, data):
    """The rows of k arm bodies, aligned as the copies of one handler they are."""
    if len(set(group)) != len(group):
        return ()
    steps = [
        dict(align(image, group[j], group[j + 1], stops, band, data)) for j in range(len(group) - 1)
    ]
    return () if not all(steps) else _rows(group, steps)


def extend(prog, image, fam, band, data=None):
    """``fam`` with the rows its copies' paired dispatch arms add, to a fixed point.

    A patched-``JMP`` dispatch is where the copies stop being one stream: each
    points at its own handlers, which is where most unexecuted arms live. An arm
    body runs to the next arm's entry, so one handler never aligns with the next.
    """
    proc = prog.procs.get(fam.proc)
    if proc is None:
        return fam
    data = _data(prog, band) if data is None else data
    tabs = _dispatch(proc, prog.by_id(), image, band)
    stops = set(fam.bases) | {t for arms in tabs.values() for t in arms.values()}
    rows, seen = set(fam.rows), set()
    while True:
        grew, span = False, fam.spans()[0]
        for src in sorted(s for s in tabs if s in span and s not in seen):
            seen.add(src)
            cols = [tabs.get(fam.pcmap(j).get(src)) for j in range(1, fam.k)]
            if any(c is None for c in cols):
                continue
            for x in sorted(set(tabs[src]).intersection(*[set(c) for c in cols])):
                arms = (tabs[src][x],) + tuple(c[x] for c in cols)
                add = _group(image, arms, stops, band, data)
                grew = grew or bool(set(add) - rows)
                rows |= set(add)
        fam = Copies(fam.bases, tuple(sorted(rows)), fam.proc)
        if not grew:
            return fam


def correspond(prog, image, pcs, band):
    """Every sibling family of ``prog``: chained, then extended through its arms.

    A family whose arms make its operand map ambiguous is refused whole;
    extending can swallow a smaller family, so the widest are chosen again.
    ``pcs`` is what an execution reached, which the built procedures already say.
    """
    data = _data(prog, band)
    out = [extend(prog, image, fam, band, data) for fam in chains(prog, image, band)]
    return _select([fam for fam in out if fam.consistent(image)])
