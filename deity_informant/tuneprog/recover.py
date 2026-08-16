"""S6 -- presentation-level recovery: stride views, roles, names.

Reads the S4 IR only: loads flowing into a SID store are that register's image, a
load-modify-store by one is a counter or timer, a value that indexes a region is a
cursor, a zero-page pair used as an address is a pointer, equal strides one view.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ir import Bin, Call, Const, Let, Load, Store, Var
from .structure import phase as _phase

SID_LO, SID_HI = 0xD400, 0xD418
VOICE_REG = ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "ctrl", "ad", "sr")
GLOBAL_REG = {0xD415: "cutoff_lo", 0xD416: "cutoff_hi", 0xD417: "res_route", 0xD418: "mode_vol"}
OPNAME = {"^": "eor", "|": "or", "&": "and", "+": "add", "-": "sub", "<<": "shl", ">>": "shr"}
DEPTH, MAXOPS, MAXPAIRS = 4, 2, 24

try:
    from pysidtracker.notefreq import is_octave_ramp
except ImportError:  # pragma: no cover - only without the optional survey package

    def is_octave_ramp(values, min_steps=6):
        """Fallback for :func:`pysidtracker.notefreq.is_octave_ramp`."""
        if not values or not 0 < values[0] <= 4 or values[-1] < 0x20:
            return False
        if any(b < a for a, b in zip(values, values[1:])):
            return False
        return sum(b > a for a, b in zip(values, values[1:])) >= min_steps


@dataclass
class Names:
    """The recovered presentation: a name and a role per region, plus struct views."""

    region: dict = field(default_factory=dict)
    role: dict = field(default_factory=dict)
    view: dict = field(default_factory=dict)
    groups: dict = field(default_factory=dict)
    procs: dict = field(default_factory=dict)
    elem: dict = field(default_factory=dict)
    phase: tuple = None
    notes: dict = field(default_factory=dict)

    def of(self, rid):
        return self.region.get(rid, "r%d" % rid)

    def to_dict(self):
        return {
            "regions": [
                {
                    "id": k,
                    "name": v,
                    "role": self.role.get(k, ""),
                    "view": list(self.view[k]) if k in self.view else None,
                    "note": self.notes.get(k, ""),
                }
                for k, v in sorted(self.region.items())
            ],
            "groups": {g: dict(v, members=sorted(v["members"])) for g, v in self.groups.items()},
            "procs": self.procs,
            "phase": None if self.phase is None else {"region": self.phase[0]},
        }


# ---- expression facts --------------------------------------------------------
def _expand(e, defs, depth=DEPTH):
    """``e`` with block-local names substituted (bounded), for leaf analysis."""
    t = type(e)
    if t is Var and depth > 0 and e.n in defs:
        return _expand(defs[e.n], defs, depth - 1)
    if t is Bin:
        return Bin(e.op, _expand(e.a, defs, depth), _expand(e.b, defs, depth), e.w)
    if t is Load:
        return Load(e.cls, _expand(e.a, defs, depth), e.w, e.lo, e.hi, e.r)
    return e


def _walk(e):
    t = type(e)
    yield e
    if t is Bin:
        yield from _walk(e.a)
        yield from _walk(e.b)
    elif t is Load:
        yield from _walk(e.a)


def _ops(e):
    return sum(1 for x in _walk(e) if type(x) is Bin)


def _loads(e):
    return [x for x in _walk(e) if type(x) is Load]


def _leaf_loads(e):
    """The loads of ``e`` that are values, not parts of an address."""
    inner = {id(y) for x in _walk(e) if type(x) is Load for y in _walk(x.a)}
    return [x for x in _walk(e) if type(x) is Load and id(x) not in inner]


def _same_cell(a, b):
    return a.v == b.v if type(a) is Const and type(b) is Const else a == b


def _reachable(prog, root):
    """The procedures reachable from ``root`` through calls."""
    seen, work = set(), [root] if root in prog.procs else []
    while work:
        n = work.pop()
        if n in seen:
            continue
        seen.add(n)
        work += [s.proc for b in prog.procs[n].blocks.values() for s in b.stmts if type(s) is Call]
    return seen


class Facts:
    """Everything the roles are derived from, gathered in one pass over the IR."""

    def __init__(self, prog):
        self.prog = prog
        self.rgn = {r.id: r for r in prog.storage}
        self.sid = []
        self.updates = {}
        self.plain = set()
        self.index = {}
        self.addr = set()
        self.reads = {}
        self.writes = {}
        self.wpc = {}
        self.tick = _reachable(prog, prog.meta.get("tick_proc")) or set(prog.procs)
        for name, p in prog.procs.items():
            self.reads[name], self.writes[name] = set(), set()
            for b in p.blocks.values():
                self.block(name, b)

    def block(self, name, b):
        defs = {}
        for s in b.stmts:
            if type(s) is Let:
                if not s.n.startswith("$"):
                    defs[s.n] = s.e
                self.value(name, _expand(s.e, defs))
            elif type(s) is Store:
                v = _expand(s.v, defs)
                self.value(name, v)
                self.value(name, _expand(s.a, defs))
                self.store(name, s, v)
            elif type(s) is Call:
                for a in s.args:
                    self.value(name, _expand(a, defs))

    def value(self, name, e):
        """Record what an expression reads: regions, index uses, pointer uses."""
        for x in _walk(e):
            if type(x) is not Load:
                continue
            self.reads[name].add(x.r)
            for y in _loads(x.a):
                self.index.setdefault(y.r, set()).add(x.r)
                if y.w == 2:
                    self.addr.add(y.r)

    def store(self, name, s, v):
        """Record a store: the SID image, and whether it updates its own region."""
        if s.cls == "io" and type(s.a) is Const and SID_LO <= s.a.v <= SID_HI:
            self.sid.append((s.a.v, v))
        if s.r < 0:
            return
        self.writes[name].add(s.r)
        self.wpc.setdefault(s.r, set()).add(s.src)
        if name not in self.tick:
            return
        same = [x for x in _leaf_loads(v) if x.r == s.r and _same_cell(x.a, s.a)]
        if same and _ops(v) <= MAXOPS:
            self.updates.setdefault(s.r, set()).add(v)
        else:
            self.plain.add(s.r)


# ---- roles -------------------------------------------------------------------
def _sid_name(addr):
    """``(field name, voice)`` for a SID register address."""
    if addr in GLOBAL_REG:
        return GLOBAL_REG[addr], None
    v, k = divmod(addr - SID_LO, 7)
    return VOICE_REG[k], v


def sid_image(facts):
    """``{region: (field name, {element: voice})}`` for the regions the SID image reads."""
    out = {}
    for addr, v in facts.sid:
        if _ops(v) > MAXOPS:
            continue
        leaves = [x for x in _leaf_loads(v) if x.r in facts.rgn]
        op = next((y.op for y in _walk(v) if type(y) is Bin), "")
        for i, x in enumerate(leaves):
            name, voice = _sid_name(addr)
            if i:
                name = "%s_%s" % (name, OPNAME.get(op, i))
            r = facts.rgn[x.r]
            hit = out.setdefault(x.r, [name, {}])
            if type(x.a) is Const:
                hit[1][(x.a.v - r.base) // max(r.stride, 1)] = voice
    return {k: (n, m) for k, (n, m) in out.items()}


def _update_role(facts, rid):
    """``counter``/``timer``/``acc`` from the shape of a region's own updates."""
    steps = set()
    for e in facts.updates.get(rid, ()):
        for x in _walk(e):
            if type(x) is Bin and x.op in ("+", "-") and type(x.b) is Const:
                steps.add(x.b.v if x.op == "+" else -x.b.v)
    if not steps:
        return "acc" if rid in facts.updates else ""
    if steps <= {1, -1, 255, -255}:
        return "timer" if rid in facts.plain else "counter"
    return "acc"


def _groups(prog, names):
    """Struct views: regions of equal stride and element count are one view."""
    groups = {}
    for r in prog.storage:
        n = (r.size + r.stride - 1) // r.stride if r.stride else 1
        if r.id < 0 or r.stride < 1 or r.kind == "io" or r.size != (n - 1) * r.stride + 1:
            continue
        if n < 3 or (r.stride == 1 and n != 3):
            continue
        groups.setdefault((r.stride, n), []).append(r.id)
    out = {}
    for i, ((stride, n), members) in enumerate(sorted(groups.items())):
        if len(members) < 2:
            continue
        g = "voice" if n == 3 else "rec%d" % (i + 1)
        out[g] = {"stride": stride, "n": n, "members": members}
        for rid in members:
            names.view[rid] = (g, "")
    return out


def _layouts(data, n):
    """The (name, lo, hi) column pairs a table of ``n`` entries could be stored as."""
    return (
        ("lo|hi", data[:n], data[n : 2 * n]),
        ("hi|lo", data[n : 2 * n], data[:n]),
        ("u16le", data[0 : 2 * n : 2], data[1 : 2 * n : 2]),
    )


def _freq_layout(data, least=48, most=256):
    """A note-frequency layout of ``data`` (parallel columns or u16), or ``''``."""
    for n in range(min(len(data) // 2, most), least - 1, -1):
        for name, lo, hi in _layouts(data, n):
            cut = next((i for i, v in enumerate(hi) if v), n)
            if n - cut < least or not is_octave_ramp(list(hi[cut:])):
                continue
            v = [(h << 8) | l for l, h in zip(lo[cut:], hi[cut:])]
            if _semitones(v) >= least:
                return "12-TET %s, %d entries (%d below one octave)" % (name, n, cut)
    return ""


def _semitones(v, lo=1.04, hi=1.08):
    """The longest run of consecutive entries one 12-TET semitone apart."""
    best = run = 0
    for a, b in zip(v, v[1:]):
        run = run + 1 if a and lo < b / a < hi else 0
        best = max(best, run)
    return best


def _tables(prog, facts, names):
    """Read-only regions: an array read through an index is a table; the rest keep an address."""
    indexed = {t for s in facts.index.values() for t in s}
    for r in prog.storage:
        if r.id < 0 or r.id in names.region or r.kind not in ("const", "image", "init_constant"):
            continue
        names.role[r.id] = names.role.get(r.id) or ("table" if r.id in indexed else "")
        _uniq(names, r.id, "b%04X" % r.base if r.id in names.view else "T%04X" % r.base)


def _uniq(names, rid, want):
    """Give region ``rid`` the name ``want``, made unique against the ones taken."""
    taken = set(names.region.values())
    n, i = want, 2
    while n in taken:
        n, i = "%s_%d" % (want, i), i + 1
    names.region[rid] = n
    return n


def _basename(r, role, facts, names):
    """The name a region gets from its role, its target, or its address."""
    if role == "cursor":
        tgt = sorted(facts.index.get(r.id, ()))
        name = names.region.get(tgt[0]) if tgt else None
        return "%s_idx" % name.lower() if name else "cursor_%04X" % r.base
    if role in ("timer", "counter", "acc", "ptr"):
        return role
    return "b%04X" % r.base


def _unique_proc(names, want):
    taken = set(names.procs.values())
    n, i = want, 2
    while n in taken:
        n, i = "%s%d" % (want, i), i + 1
    return n


def _tail_target(prog, name):
    """The procedure ``name`` exists only to call, or ``name`` itself."""
    p = prog.procs[name]
    stmts = [s for b in p.blocks.values() for s in b.stmts]
    if len(p.blocks) == 1 and len(stmts) == 1 and type(stmts[0]) is Call:
        return _tail_target(prog, stmts[0].proc)
    return name


def _procs(prog, facts, names, structured):
    """Procedure names: the phase arms are the two rates, a record decoder is row_apply."""
    for name in prog.procs:
        names.procs[name] = name
    if names.phase is not None:
        for arm, tag in ((names.phase[3], "main"), (names.phase[2], "sub")):
            for callee in arm[:1]:
                names.procs[_tail_target(prog, callee)] = _unique_proc(names, tag)
    ptr = {r for r, k in names.role.items() if k == "ptr"}
    for name, p in prog.procs.items():
        wr = {r for r in facts.writes[name] if facts.rgn.get(r) and facts.rgn[r].kind == "state"}
        if names.procs[name] != name or p.kind != "sub":
            continue
        if len(wr) >= 4 and facts.reads[name] & ptr:
            names.procs[name] = _unique_proc(names, "row_apply")
    for name in prog.procs:
        tgt = _tail_target(prog, name)
        if tgt != name:
            names.procs[name] = names.procs[tgt]


def _freq(prog, names):
    """Name every note-frequency table: one region, or two adjacent parallel columns."""
    rs = [r for r in prog.storage if r.id >= 0 and 8 <= r.size <= 4096]
    for r in rs:
        note = _freq_layout(r.init)
        if note:
            _name_freq(names, [r], note, ["FREQ"])
    sized = {}
    for r in rs:
        sized.setdefault((r.size, r.kind), []).append(r)
    for group in sorted(sized.values(), key=lambda g: -g[0].size):
        for a, q in [(a, q) for a in group for q in group if a.base < q.base][:MAXPAIRS]:
            if {a.id, q.id} & set(names.region):
                continue
            note = _freq_layout(a.init + q.init)
            if note:
                cols = ["FREQ_LO", "FREQ_HI"] if "lo|hi" in note else ["FREQ_HI", "FREQ_LO"]
                _name_freq(names, [a, q], note, cols)


def _name_freq(names, regions, note, cols):
    for r, col in zip(regions, cols):
        names.role[r.id] = "freq_table"
        names.notes[r.id] = note
        _uniq(names, r.id, col)


def _unrolled(prog, facts, names):
    """Unrolled copies: one variable per voice, written by the same code ``d`` bytes on.

    Three regions of equal shape whose writer pcs are the same set shifted by ``d``
    are the same variable in three unrolled blocks, so they are one struct field.
    """
    left = [r for r in prog.storage if r.id >= 0 and r.kind == "state" and r.id not in names.view]
    by, tri = {}, {}
    for r in left:
        if facts.wpc.get(r.id):
            by.setdefault((r.size, r.stride, names.role.get(r.id, "")), {})[r.base] = r
    for bases in by.values():
        for b in sorted(bases):
            for d in sorted(x - b for x in bases if x > b):
                rs = [bases.get(b + i * d) for i in range(3)]
                if all(rs) and _shifted(facts, rs, d) and not any(r.id in names.view for r in rs):
                    tri.setdefault(d, []).append(rs)
                    for i, r in enumerate(rs):
                        names.view[r.id] = (None, r.base)
                        names.elem[r.id] = i
                    break
    g = "voice" if names.groups.get("voice", {}).get("n") == 3 else None
    d = max(tri, key=lambda k: len(tri[k]), default=None)
    for k, chains in tri.items():
        for rs in chains:
            for r in rs:
                if k != d or g is None:
                    del names.view[r.id], names.elem[r.id]
                else:
                    names.view[r.id] = (g, names.region.get(rs[0].id, "b%04X" % rs[0].base))
                    names.groups[g]["members"].append(r.id)


def _shifted(facts, rs, d):
    """True when the regions are written by as many pcs, at least half of them ``d`` apart."""
    pcs = [facts.wpc.get(r.id, set()) for r in rs]
    if any(len(p) != len(pcs[0]) for p in pcs):
        return False
    return all(2 * len({x + i * d for x in pcs[0]} & p) >= len(p) for i, p in enumerate(pcs))


def recover(prog, structured=None):
    """The :class:`Names` for ``prog``; pass :func:`~.structure.structure`'s result."""
    facts = Facts(prog)
    names = Names()
    tick = prog.meta.get("tick_proc")
    if structured and tick in structured:
        names.phase = _phase(structured[tick], prog.storage)
    _freq(prog, names)
    names.groups = _groups(prog, names)
    for rid, (fname, _elems) in sorted(sid_image(facts).items()):
        names.role[rid] = "sid_image"
        _uniq(names, rid, fname)
    for r in prog.storage:
        if r.id < 0 or r.id in names.region or r.kind not in ("state", "init_constant"):
            continue
        ptr = r.id in facts.addr or (r.size == 2 and r.id in facts.index)
        role = names.role.get(r.id) or ("ptr" if ptr else "")
        role = role or ("cursor" if r.id in facts.index else "") or _update_role(facts, r.id)
        names.role[r.id] = role
        _uniq(names, r.id, _basename(r, role, facts, names))
    if names.phase is not None:
        rid = names.phase[0]
        names.role[rid] = "phase"
        del names.region[rid]
        _uniq(names, rid, "call_counter" if _update_role(facts, rid) == "counter" else "phase")
    _tables(prog, facts, names)
    for rid, (g, _f) in list(names.view.items()):
        names.view[rid] = (g, names.region.get(rid, "b%04X" % facts.rgn[rid].base))
    _unrolled(prog, facts, names)
    _procs(prog, facts, names, structured)
    return names
