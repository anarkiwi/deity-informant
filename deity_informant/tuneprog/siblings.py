"""S6 -- sibling copies: k static copies of one template, aligned pc by pc.

An unrolled player writes one routine k times (Follin's voices, defMON's
cascades); the copies differ in operands and, a trace seeing only the arms that
ran, in shape. Public API: :func:`correspond`, :func:`align`, :class:`Copies`.
"""

from __future__ import annotations

from dataclasses import dataclass

from jennings.opcodes import MEM_MODES, MODE_LEN, OPCODES as OPS

from .ir import Switch, succs

GRAM = 6  # opcodes a seed matches on
MINROWS = 6  # instructions a copy must align for to be one
MINARM = 2  # instructions a switch arm must align for to pair
MAXCOPIES = 12
LOOK, CONFIRM = 4, 3  # resync window, and the opcodes that confirm a resync
LIMIT = 2048  # instructions one alignment may cover


def ilen(image, pc):
    """The length in bytes of the instruction at ``pc``."""
    return MODE_LEN[OPS[image[pc]][1]]


def operand(image, pc):
    """The address the instruction at ``pc`` names, or ``None`` when it names none."""
    mode = OPS[image[pc]][1]
    if mode not in MEM_MODES:
        return None
    return image[pc + 1] if MODE_LEN[mode] == 2 else image[pc + 1] | (image[pc + 2] << 8)


def stream(image, pc, n):
    """The first ``n`` instruction addresses from ``pc``."""
    out = []
    for _ in range(n):
        out.append(pc)
        pc = (pc + ilen(image, pc)) & 0xFFFF
    return out


def _same(image, a, b, n):
    """True when the next ``n`` opcodes of the two streams are equal."""
    for _ in range(n):
        if image[a] != image[b]:
            return False
        a, b = (a + ilen(image, a)) & 0xFFFF, (b + ilen(image, b)) & 0xFFFF
    return True


def _resync(image, a, b):
    """The two streams past an insertion on one side, or ``None``."""
    x, y = a, b
    for _ in range(LOOK):
        x = (x + ilen(image, x)) & 0xFFFF
        if _same(image, x, b, CONFIRM):
            return x, b
        y = (y + ilen(image, y)) & 0xFFFF
        if _same(image, a, y, CONFIRM):
            return a, y
    return None


def _inband(pc, band):
    return band[0] <= pc < band[1]


def align(image, a, b, stop=frozenset(), band=(0, 0x10000), limit=LIMIT):
    """``[(pc_a, pc_b)]``: the instruction streams at ``a`` and ``b``, aligned.

    Equal opcodes advance both; a mismatch resyncs over an insertion of up to
    ``LOOK`` instructions on one side. Either stream stops at another copy's
    base, so one copy never aligns with the next.
    """
    sa, sb = set(stop) - {a}, set(stop) - {b}
    out = []
    while len(out) < limit:
        if a in sa or b in sb or not _inband(a, band) or not _inband(b, band):
            break
        if image[a] == image[b]:
            out.append((a, b))
            a, b = (a + ilen(image, a)) & 0xFFFF, (b + ilen(image, b)) & 0xFFFF
            continue
        hit = _resync(image, a, b)
        if hit is None:
            break
        a, b = hit
    return out


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
        """``{address copy 0 names: the one copy j names}``, ambiguities dropped."""
        out, bad = {}, set()
        for r in self.rows:
            a, b = operand(image, r[0]), operand(image, r[j])
            if a is None or b is None:
                continue
            if out.setdefault(a, b) != b:
                bad.add(a)
        return {k: v for k, v in out.items() if k not in bad}

    def spans(self):
        """The pcs each copy covers, one set per copy."""
        return [{r[j] for r in self.rows} for j in range(self.k)]


def _seeds(image, pcs, band):
    """``[[pc]]`` -- executed pcs whose next ``GRAM`` opcodes agree."""
    out = {}
    for p in sorted(pcs):
        if _inband(p, band):
            out.setdefault(tuple(image[x] for x in stream(image, p, GRAM)), []).append(p)
    return [v for v in out.values() if 2 <= len(v) <= MAXCOPIES]


def family(image, bases, band, limit=LIMIT, minrows=MINROWS):
    """The :class:`Copies` for ``bases``, or ``None`` when they do not align."""
    bases = tuple(sorted(bases))
    stop = set(bases)
    maps = [dict(align(image, bases[0], o, stop, band, limit)) for o in bases[1:]]
    common = set(maps[0]) if maps else set()
    for m in maps[1:]:
        common &= set(m)
    rows = tuple((p,) + tuple(m[p] for m in maps) for p in sorted(common))
    return Copies(bases, rows) if len(rows) >= minrows else None


# ---- chained families --------------------------------------------------------
def _srcs(proc):
    """``{block src pc: [labels]}`` of one procedure."""
    out = {}
    for lbl, b in proc.blocks.items():
        out.setdefault(b.src, []).append(lbl)
    return out


def chained(proc, fam):
    """True when every copy has an edge into the next copy's entry block.

    That is what an unrolled run is: voice *j* ends by jumping to voice *j+1*.
    Nothing is asked of the last copy, whose exit leaves the run.
    """
    srcs = _srcs(proc)
    span = fam.spans()
    for j in range(fam.k - 1):
        nxt = set(srcs.get(fam.bases[j + 1], ()))
        if not nxt or not any(
            b.src in span[j] and set(succs(b.term)) & nxt for b in proc.blocks.values()
        ):
            return False
    return True


def leftmax(image, bases, pcs):
    """True when no executed instruction extends every copy backwards.

    A family every copy can grow to the left is a shifted view of a longer one,
    so only the maximal one is kept.
    """
    pred = {p + ilen(image, p): p for p in pcs}
    ps = [pred.get(b) for b in bases]
    return None in ps or len({image[p] for p in ps}) > 1


def _holds(proc, fam):
    """True when the procedure holds the copies: every entry but the first is a block.

    The first copy's opening instruction may have been merged into the block
    before it (defMON's ``sub`` clone), and is then found by its later blocks.
    """
    srcs = _srcs(proc)
    span = fam.spans()
    return all(b in srcs for b in fam.bases[1:]) and bool(span[0] & set(srcs))


def chains(prog, image, pcs, band):
    """Every chained sibling family of ``prog``, widest first, non-overlapping."""
    cands = []
    for bases in _seeds(image, pcs, band):
        fam = family(image, bases, band) if leftmax(image, bases, pcs) else None
        if fam is None:
            continue
        for name, proc in prog.procs.items():
            if _holds(proc, fam) and chained(proc, fam):
                cands.append(Copies(fam.bases, fam.rows, name))
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


# ---- extension through matched switch arms -----------------------------------
def _switches(proc, span):
    """``{block src: terminator}`` of the switch blocks inside one copy's span."""
    return {b.src: b.term for b in proc.blocks.values() if type(b.term) is Switch and b.src in span}


def pair_arms(image, arms, band):
    """Pair k sorted arm lists in table order, by how far their targets align.

    Parallel jump tables hold the same handler once per copy in the same order;
    an entry only some copies carry pairs with nothing and is left out.
    """
    out = [(t,) for t in arms[0]]
    for other in arms[1:]:
        out = _lcs(image, out, other, band)
    return out


def _lcs(image, rows, other, band):
    """The longest order-preserving matching of ``rows`` with ``other``."""
    n, m = len(rows), len(other)
    best = [[0] * (m + 1) for _ in range(n + 1)]
    ok = [[False] * m for _ in range(n)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            ok[i][j] = len(align(image, rows[i][-1], other[j], band=band, limit=64)) >= MINARM
            hit = best[i + 1][j + 1] + 1 if ok[i][j] else 0
            best[i][j] = max(hit, best[i + 1][j], best[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if ok[i][j] and best[i][j] == best[i + 1][j + 1] + 1:
            out.append(rows[i] + (other[j],))
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def extend(prog, image, fam, band, rounds=4):
    """``fam`` with the rows its copies' paired switch arms add, to a fixed point.

    A patched-``JMP`` dispatch is where the copies stop being one stream: each
    points at its own handlers, which is where most unexecuted arms live.
    """
    proc = prog.procs.get(fam.proc)
    if proc is None:
        return fam
    seen = set()
    for _ in range(rounds):
        sw = [_switches(proc, s) for s in fam.spans()]
        rows, grew = set(fam.rows), False
        for src, term in sorted(sw[0].items()):
            others = [s.get(fam.pcmap(j).get(src)) for j, s in enumerate(sw)][1:]
            if src in seen or any(t is None for t in others):
                continue
            seen.add(src)
            arms = [sorted(v for v, _l in t.cases) for t in [term] + others]
            for group in pair_arms(image, arms, band):
                sub = family(image, group, band, minrows=1)
                if sub is not None and sub.bases == tuple(sorted(group)):
                    grew = grew or bool(set(sub.rows) - rows)
                    rows |= set(sub.rows)
        fam = Copies(fam.bases, tuple(sorted(rows)), fam.proc)
        if not grew:
            break
    return fam


def correspond(prog, image, pcs, band):
    """Every sibling family of ``prog``: chained, then extended through its arms."""
    return [extend(prog, image, fam, band) for fam in chains(prog, image, pcs, band)]
