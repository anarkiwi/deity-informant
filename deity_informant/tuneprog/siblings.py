"""S2c -- sibling copies: k static copies of one template, aligned pc by pc.

Bases are the chain the built procedures carry, a pair of copies is one gapped
opcode alignment, and a family holds while every copy's operand map is a function.

Public API: :func:`correspond`, :func:`align`, :func:`chains`, :class:`Copies`.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from difflib import SequenceMatcher

from jennings.opcodes import MEM_MODES, MODE_LEN, OPCODES as OPS

from .jumptab import dispatch

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


def _stream_stop(stops, pc, band):
    """Where a stream that starts at ``pc`` must stop: the next base, or the band."""
    return min([s for s in stops if s > pc] + [band[1]])


def _align_streams(image, xs, ys, whole=True):
    """Aligned index pairs of two instruction streams, ``None`` over a replacement.

    Streams are compared byte for byte, so an alias opcode never matches the
    opcode it aliases and the byte carries the mode. Copies of one template differ
    by gaps (Follin's ``CMP #v``); where only a prefix is asked for, the first
    replacement ends it.
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
    xs = stream(image, a, _stream_stop(stops, a, band), data=data)
    ys = stream(image, b, _stream_stop(stops, b, band), data=data)
    rows = _align_streams(image, xs, ys)
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

    def slack(self, image):
        """Instructions the copies hold that no row explains.

        A tie-break only: admission is :func:`chained` and :meth:`consistent`, and
        where two equally wide readings of one run cover the same code this
        prefers the one leaving less of it unexplained, ties going to the lower
        bases. It orders candidates; it does not admit one.
        """
        ends = list(self.bases[1:]) + [max(r[-1] for r in self.rows) + 1]
        held = sum(len(stream(image, b, e)) for b, e in zip(self.bases, ends))
        return held - self.k * len(self.rows)


# ---- chained families --------------------------------------------------------
@dataclass
class Code:
    """One procedure's instructions as the image states them: counts and transfers.

    Block boundaries are an artefact -- of which arms an execution reached and of
    which ones :mod:`.closure` joined -- so a copy's entry is read from the
    instruction stream and its executions from the sites, not from the blocks.
    """

    runs: dict  # pc -> site count, for each instruction an execution reached
    to: dict  # pc -> the addresses control reaches from the instruction
    into: dict  # pc -> the instructions that transfer to it
    pcs: list  # sorted, exactly the instructions an execution reached
    bounds: list  # sorted, pcs plus the image's own decode of the gaps between them

    def after(self, pc):
        """The first executed instruction at or after ``pc``, or ``None``.

        A copy ends at the image of the template's last instruction; what a copy
        holds past that is its own, and the next copy opens where it ends.
        """
        i = bisect_left(self.pcs, pc)
        return self.pcs[i] if i < len(self.pcs) else None

    def enters(self, a, b, c):
        """The instructions of ``[a, b)`` that transfer into ``b``, the copy ``[b, c)``.

        A transfer leaves one copy for the next -- a jump, a branch or falling in;
        one that reaches past the copy it enters steps over that code instead. A
        direction no execution took leaves nothing.
        """
        return [
            p
            for p in self.into.get(b, ())
            if a <= p < b and all(a <= t < c for t in self.to[p] if self.ran(t))
        ]

    def ran(self, pc):
        """The site count of the instruction at ``pc``; 0 where no execution reached it.

        Only ``pcs`` carries a count. ``bounds`` is wider on purpose -- it is the
        image's linear decode of the gaps, which is where a first copy the block
        graph merged away is looked for -- and every address of it that no
        execution reached answers 0 here.
        """
        return self.runs.get(pc, 0)


def _to(image, pc):
    """The addresses control reaches from the instruction at ``pc``, per the image.

    A computed jump states nothing, so it ends the walk rather than guessing.
    """
    mn, mode = OPS[image[pc]]
    if mn in ("RTS", "RTI", "BRK", "JAM"):
        return ()
    if mn == "JMP":
        return (operand(image, pc),) if mode == "abs" else ()
    nxt = (pc + MODE_LEN[mode]) & 0xFFFF
    if mode == "rel":
        return (nxt, (nxt + ((image[pc + 1] ^ 0x80) - 0x80)) & 0xFFFF)
    return (nxt,)


def _code(node, image, band=(0, 0x10000)):
    """The :class:`Code` of one procedure: its executions, and its transfers.

    ``node`` is S2b's ``{(pc, opcode): node}``, which holds every instruction an
    execution of the procedure reached and the site count of each -- the trace's
    own answer, not one inferred from the blocks a later pass made of them.
    """
    runs = {}
    for (pc, _op), n in node.items():
        if band[0] <= pc < band[1] and n["count"]:
            runs[pc] = runs.get(pc, 0) + n["count"]
    pcs = sorted(runs)
    to = {p: _to(image, p) for p in pcs}
    into = {}
    for p in pcs:
        for t in to[p]:
            into.setdefault(t, []).append(p)
    bounds = set(pcs)
    for a, b in zip(pcs, pcs[1:]):
        bounds.update(stream(image, a, b))
    return Code(runs, to, into, pcs, sorted(bounds))


def chained(node, image, fam):
    """True when every copy transfers into the next copy's entry, having run as often.

    That is what an unrolled run is: voice *j* ends by reaching voice *j+1*.
    Nothing is asked of the last copy, whose exit leaves the run.
    """
    code = _code(node, image)
    ends = list(fam.bases[1:]) + [max(r[-1] for r in fam.rows) + 1]
    return len({code.ran(b) for b in fam.bases}) == 1 and all(
        code.enters(fam.bases[j], fam.bases[j + 1], ends[j + 1]) for j in range(fam.k - 1)
    )


def _copy_window(image, xs, b, hi, whole=True):
    """``(rows, end)`` -- the window at ``b`` the template ``xs`` aligns into, and its end.

    The window grows by the template it has not reached yet, while growing reaches
    further. A copy holds the whole template, in order; the run's last copy holds
    it from the start as far as the run goes, and ends at the last row.
    """
    ys = stream(image, b, hi, len(xs))
    rows = _align_streams(image, xs, ys, whole)
    while whole and rows and rows[-1][0] < len(xs) - 1:
        more = stream(image, b, hi, len(ys) + len(xs) - 1 - rows[-1][0])
        if len(more) == len(ys):
            break
        got = _align_streams(image, xs, more)
        if not got or got[-1][0] <= rows[-1][0]:
            break
        ys, rows = more, got
    if not rows or len(rows) != (len(xs) if whole else rows[-1][0] + 1):
        return None
    ys = ys[: rows[-1][1] + 1]
    return [(xs[i], ys[j]) for i, j in rows], ys[-1] + ilen(image, ys[-1])


def _run_chain(image, code, band, first):
    """``(bases, [pc map per step])`` of the run the pair ``first`` opens, or ``None``.

    Copy *j+1*'s extent comes out of the alignment, so the base after it is not
    guessed: the run goes on exactly while that address ran as often and the copy
    before it transfers into it.
    """
    bases, steps = list(first), []
    while True:
        xs = stream(image, bases[-2], bases[-1])
        got = _copy_window(image, xs, bases[-1], band[1])
        last = got is None
        if last and steps:  # only the copy nothing follows may hold the template in part
            got = _copy_window(image, xs, bases[-1], band[1], whole=False)
        out = [] if got is None else code.enters(bases[-2], bases[-1], got[1])
        rows = dict(got[0]) if got is not None else {}
        if not out or (last and not any(u in rows for u in out)):
            bases.pop()
            return None if len(bases) < 2 else _before(image, code, band, (bases, steps))
        steps.append(rows)
        end = None if last else code.after(got[1])
        if end is None or image[end] != image[bases[-1]] or code.ran(end) != code.ran(bases[-1]):
            return _before(image, code, band, (bases, steps))
        bases.append(end)


def _before(image, code, band, run):
    """The run with the copies before it, which the run's own transfers do not name.

    A copy the one before it falls into is entered by no jump; the search back is
    bounded by the template, which no copy is longer than.
    """
    bases, steps = run
    while True:
        n = len(stream(image, bases[0], bases[1]))
        hit = None
        for a in [p for p in code.bounds if p < bases[0]][-n:]:
            if image[a] != image[bases[0]]:
                continue
            got = _copy_window(image, stream(image, a, bases[0]), bases[0], band[1])
            if (
                got is not None
                and code.after(got[1]) == bases[1]
                and code.enters(a, bases[0], got[1])
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


def _bases(image, code):
    """Where a copy may open: a leader of the image's code, or a branch.

    Which addresses the built blocks begin at is an artefact of which arms ran and
    which the static closure joined. Both products draw their boundaries at the
    same places in the image: a leader -- a transfer's target, either way out of a
    branch, an address nothing falls into -- and the branch that decides.
    """
    out, fell = set(), set()
    for p in code.pcs:
        to = code.to[p]
        if len(to) == 2:
            out.add(p)
        for t in to:
            (fell if OPS[image[p]][0] not in LEAVE and len(to) == 1 else out).add(t)
    return (out | (set(code.pcs) - fell)) & set(code.pcs)


def _candidate_pairs(image, code):
    """The candidate pairs of one procedure: same opcode, same executions, in order.

    Two copies of one template open on the same byte, and a chain runs every copy
    of it as often.
    """
    same = {}
    for p in sorted(_bases(image, code)):
        same.setdefault((image[p], code.ran(p)), []).append(p)
    return sorted((a, b) for g in same.values() for i, a in enumerate(g) for b in g[i + 1 :])


def chains(prog, image, band, procs):
    """Every chained sibling family of ``prog``, widest first, non-overlapping.

    A copy runs to where the next one starts, so its stream needs no bound of its
    own; only the arms :func:`extend` pairs are bounded by something coarser.
    """
    cands, bad = [], []
    for name in prog.procs:
        code, seen = _code(procs[name].nodes, image, band), set()
        for a, b in _candidate_pairs(image, code):
            if (a, b) in seen or not code.enters(a, b, band[1]):
                continue
            got = _run_chain(image, code, band, (a, b))
            if got is None:
                continue
            seen.update(zip(got[0], got[0][1:]))
            fam = Copies(got[0], _rows(*got), name)
            if not fam.rows:
                continue
            (cands if fam.consistent(image) else bad).append(fam)
    return _select([f for f in cands if not _span(f) & _spans(bad)], image)


def _span(fam):
    """Every ``(procedure, pc)`` a family's copies cover."""
    return {(fam.proc, p) for r in fam.rows for p in r}


def _spans(fams):
    """The union of what a list of families covers."""
    return set().union(*[_span(f) for f in fams]) if fams else set()


def _rank(fam, image):
    """Ordering only -- widest, then longest, then least left over; not admission."""
    return (-fam.k, -len(fam.rows), fam.slack(image))


def _select(cands, image):
    """The best-explaining families whose copies do not overlap an accepted one's."""
    out, taken = [], set()
    for fam in sorted(cands, key=lambda f: _rank(f, image) + (f.bases, f.proc)):
        span = _span(fam)
        if span & taken:
            continue
        out.append(fam)
        taken |= span
    return out


# ---- extension through the parallel dispatch tables --------------------------
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
    tabs = dispatch(proc, prog.by_id(), image, band)
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


def correspond(prog, image, band, procs):
    """Every sibling family of ``prog``: chained, then extended through its arms.

    ``procs`` is S2b's procedures, which carry the site count of every instruction
    an execution reached. A family whose arms make its operand map ambiguous is
    refused whole; extending can swallow a smaller family, so the widest are
    chosen again.
    """
    data = _data(prog, band)
    out = [extend(prog, image, fam, band, data) for fam in chains(prog, image, band, procs)]
    return _select([fam for fam in out if fam.consistent(image)], image)
