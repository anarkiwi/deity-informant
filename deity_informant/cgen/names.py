"""Names of memory and values: regions split into atoms by aggregate structure identification
(Ramalingam, Field and Tip, POPL 1999), atoms and values named by the SID registers their data
reaches along the module's data flow (``docs/cgen.md``, *Names*).
"""

from __future__ import annotations

import keyword
from collections import defaultdict
from dataclasses import dataclass

from pycparser import c_ast

from .state import structs

#: The SID's origin and its voices' stride.
SID, VOICE = 0xD400, 7
#: The registers as the llvm-mos SDK's ``struct __sid`` names them, 16-bit fields as bytes.
VOICE_REGS = ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "ctrl", "ad", "sr")
OTHER_REGS = ("flt_freq_lo", "flt_freq_hi", "flt_ctrl", "amp", "ad1", "ad2", "noise", "read3")
#: A 16-bit register's name, for a value reaching both its bytes.
PAIRS = {"freq": "freq_lo", "pw": "pw_lo", "flt_freq": "flt_freq_lo"}
#: The label of a value added to a SID voice register's address, and of an index choosing it.
VOICE_REG, VOICE_INDEX = "voice_reg", "voice"
INDICES = (VOICE_INDEX, VOICE_REG)
#: The prefix of an atom's name, which no value's name has.
GLOBAL = "g_"
#: The role name of a 16-bit address and of a byte pair holding one, and the rank of roles.
POINTER, _LOOP = "ptr", 1 << 30
#: C's keywords and the names the runtime gives.
C_WORDS = frozenset(
    "auto break case char const continue default do double else enum extern float for goto if "
    "inline int long register restrict return short signed sizeof static struct switch typedef "
    "union unsigned void volatile while a".split()
)


def sid_register(a):
    """``(name, voice)`` of SID register address ``a`` (``voice`` 1 to 3, or None for the
    others), else None."""
    r = a - SID
    if 0 <= r < 3 * VOICE:
        return VOICE_REGS[r % VOICE], r // VOICE + 1
    if 3 * VOICE <= r < 3 * VOICE + len(OTHER_REGS):
        return OTHER_REGS[r - 3 * VOICE], None
    return None


def sid_symbol(a):
    """The ``.h`` name of SID register address ``a`` (``SID_V2_CTRL``, ``SID_AMP``), or None."""
    reg = sid_register(a)
    if reg is None:
        return None
    name, voice = reg
    return f"SID_{'' if voice is None else f'V{voice}_'}{name.upper()}"


def sid_label(a, indexed):
    """The label of a value written to SID register ``a``: its name, with its voice unless
    ``indexed`` (a voice register at a base plus an index), else None."""
    reg = sid_register(a)
    if reg is None:
        return None
    name, voice = reg
    return name if indexed or voice is None else f"{name}{voice}"


def rank(label):
    """The order of labels one node is as near to: registers by address then voice (a 16-bit
    register as its low byte), then the voice index, the voice register offset, any other."""
    base = label.rstrip("123")
    voice = int(label[len(base) :] or 0)
    base = PAIRS.get(base, base)
    if base in VOICE_REGS:
        return (0, VOICE_REGS.index(base), voice, label)
    if base in OTHER_REGS:
        return (1, OTHER_REGS.index(base), voice, label)
    return (2, INDICES.index(base) if base in INDICES else len(INDICES), 0, label)


def label_of(labels):
    """The name of a node as near to each of ``labels``: a 16-bit register's where both its
    bytes are among them, else the first by :func:`rank`; None for none."""
    names = set(labels)
    for joined, lo in PAIRS.items():
        hi = lo.replace("_lo", "_hi")
        for v in ("", "1", "2", "3"):
            if {hi + v, lo + v} <= names:
                names = (names - {hi + v, lo + v}) | {joined + v}
    return min(names, key=rank) if names else None


@dataclass
class Atom:  # pylint: disable=too-many-instance-attributes
    """One object of region ``region``: the bytes ``org + d`` for ``d`` in ``offsets``,
    ``kind`` ``scalar``, ``pair`` (a 16-bit value's bytes), ``array`` (consecutive bytes) or
    ``field`` (one byte of each record of a stride); ``const`` when no store may write it."""

    region: int
    org: int
    offsets: tuple
    kind: str
    const: bool = False
    name: str = ""
    label: str | None = None

    @property
    def bytes(self):
        """The atom's addresses."""
        return [self.org + d for d in self.offsets]


def atomize(region, accesses, words=(), written=None):
    """``(atoms, straddling)`` of region ``region`` from its accesses ``[(base, addresses)]``:
    the address sets of several bytes alike up to a translation, narrowest and sparsest first,
    are fields of records (:func:`.state.structs`), other bytes scalars (pairs at ``words``);
    ``straddling`` the sets meeting another's atoms. ``written``: bytes stores may write."""
    fams = sorted({a for _, a in accesses if len(a) > 1})
    found, at, bad = structs(fams, key=lambda a: (a[-1] - a[0], len(a), a))
    out = []
    for n, base, fields in found:
        kind = "array" if n == tuple(range(len(n))) else "field"
        out += [Atom(region, base + d, n, kind) for d in sorted(fields)]
    singles = sorted({a[0] for _, a in accesses if len(a) == 1} - set(at))
    pairs, alone = set(), set(singles)
    for w in sorted(words):
        if {w, w + 1} <= alone and not pairs & {w - 1, w + 1}:
            pairs.add(w)
    out += [Atom(region, a, (0, 1), "pair") for a in sorted(pairs)]
    out += [Atom(region, a, (0,), "scalar") for a in singles if not pairs & {a, a - 1}]
    for x in out:
        x.const = written is not None and not written[x.bytes].any()
    return sorted(out, key=lambda x: x.org), bad


class Flow:
    """The data flow of a module: ``pred[n]`` the nodes whose data node ``n`` holds (values
    ``("v", function, value)``, atoms and cells), ``seeds`` the labels of known sinks."""

    def __init__(self):
        self.pred = defaultdict(set)
        self.seeds = defaultdict(set)

    def edge(self, src, dst):
        """Data of node ``src`` flows into node ``dst``."""
        if src != dst:
            self.pred[dst].add(src)

    def labels(self):
        """``{node: (distance, label)}`` of each node some seed's data flow reaches backwards:
        breadth-first from every seed at once, a node labelled by the seeds nearest it."""
        near = {n: (0, set(ls)) for n, ls in self.seeds.items()}
        frontier, dist = list(near), 0
        while frontier:
            dist += 1
            reach = defaultdict(set)
            for n in frontier:
                for p in self.pred.get(n, ()):
                    if p not in near:
                        reach[p] |= near[n][1]
            near.update({p: (dist, ls) for p, ls in reach.items()})
            frontier = list(reach)
        return {n: (d, label_of(ls)) for n, (d, ls) in near.items()}


def unique(wanted, taken):
    """``{key: name}`` of ``wanted`` (``[(key, base)]``, in order of preference), each name
    :func:`scoped` in ``taken`` (updated)."""
    return {key: scoped(base, [taken]) for key, base in wanted}


def _word_bases(f):
    """The constant addresses ``a`` whose bytes ``a`` and ``a + 1`` function ``f`` reads as
    the low and high byte of one 16-bit value (``lo | hi << 8``)."""

    def load_at(v):
        d = f.defs.get(v)
        d = f.defs.get(d.args[0]) if d is not None and d.name == "arith.extui" else None
        if d is None or d.name != "memref.load" or d.args[-2] not in f.globals:
            return None
        c, rest = f.split(f.address(d))
        return c if rest is None else None

    out = set()
    for o in f.walk():
        if o.name != "arith.ori" or f.width(o.res[0]) != 16:
            continue
        for x, y in (o.args, o.args[::-1]):
            d = f.defs.get(y)
            if d is None or d.name != "arith.shli" or f.const_of(d.args[1]) != 8:
                continue
            lo, hi = load_at(x), load_at(d.args[0])
            if lo is not None and hi == lo + 1:
                out.add(lo)
    return out


def _region_accesses(mod):
    """``([(cfunc, op, region, based)], words)``: the accesses printed as an element of one
    region (:meth:`CFunc.based`) and the bases of byte pairs read as 16-bit values."""
    out, words = [], set()
    for c in mod.mains.values():
        f = c.f
        words |= _word_bases(f)
        for o, _, _ in f.accesses():
            if id(o) in f.dropped or id(o) in f.promoted or o.name == "func.call":
                continue
            how = c.target(o)
            if how[0] != "region" or (id(o) in f.aware and mod.var_at):
                continue
            got = c.based(o, how[1])
            if got is not None:
                out.append((c, o, how[1], got))
    return out, words


def _function_flow(flow, c, mod):
    """Add function ``c``'s data flow to ``flow``: arithmetic (not into a truth value from
    wider operands), block arguments, calls and returns, its SID writes and reads as seeds."""
    f = c.f

    def node(v):
        return ("v", f.name, v)

    for a, srcs in f.arg_src.items():
        for o, i, _ in srcs:
            flow.edge(node(o.args[i]), node(a))
    for o in f.walk():
        if o.name == "arith.kfam":
            continue
        if o.name.startswith("arith."):
            flag = all(f.iv(r)[1] <= 1 for r in o.res)
            for a in o.args:
                if not (flag and f.iv(a)[1] > 1):
                    for r in o.res:
                        flow.edge(node(a), node(r))
        elif o.name == "func.return":
            for j, a in enumerate(o.args):
                flow.edge(node(a), ("ret", f.name, j))
        elif o.name == "func.call":
            _call_flow(flow, f, o, mod, node)


def _call_flow(flow, f, o, mod, node):
    """Add call ``o`` of function ``f``: an I/O access's seeds, or a lifted callee's
    parameters and results."""
    callee = (o.prop("callee") or "@")[1:]
    if callee in ("io_write", "io_read"):
        c, rest = f.split(o.args[0])
        label = sid_label(c, rest is not None) if f.width(o.args[0]) == 16 else None
        if label and callee == "io_write":
            flow.seeds[node(o.args[1])].add(label)
        elif label:
            flow.seeds[node(o.res[0])].add(label)
        if label and rest is not None and sid_register(c)[1] is not None:
            flow.seeds[node(rest)].add(VOICE_REG)
        return
    g = mod.mains.get(callee)
    if g is None:
        return
    params = g.f.blocks[0].args
    for i, a in enumerate(o.args[: len(params)]):
        flow.edge(node(a), ("v", callee, params[i][0]))
    for j, r in enumerate(o.res):
        flow.edge(("ret", callee, j), node(r))


def _memory_flow(flow, recs, mod):
    """Add the stores and loads of atoms and promoted cells to ``flow``: ``{id(op): atom}``."""
    at = {}
    for c, o, k, (base, addrs, _) in recs:
        atom = mod.atom_for(addrs)
        if atom is not None and atom.org <= base:
            at[id(o)] = atom
            m = ("m", k, atom.org)
            if o.name == "memref.load":
                flow.edge(m, ("v", c.f.name, o.res[0]))
            else:
                flow.edge(("v", c.f.name, o.args[0]), m)
    for c in mod.mains.values():
        f = c.f
        for o in f.walk():
            p = f.promoted.get(id(o))
            if p is None:
                continue
            m = ("cell", p[1]) if p[0] == "s" else ("cell", p[1], p[2])
            if o.name == "memref.load":
                flow.edge(m, ("v", f.name, o.res[0]))
            else:
                flow.edge(("v", f.name, o.args[0]), m)
    return at


def _label(labels, c, v):
    """The label of value ``v`` of function ``c``, or None."""
    return labels.get(("v", c.f.name, v), (0, None))[1]


def _index_seeds(flow, labels, recs, mod):
    """Seed :data:`VOICE_INDEX` on the index of each table load whose value is a voice
    register's offset (its terms past its base, an instance's copy index), and each index
    label on the indices of every access of an atom some access indexes by that label."""
    seeds = defaultdict(set)
    for c in mod.mains.values():
        f = c.f
        for o in f.walk():
            if o.name == "arith.kfam" and _label(labels, c, o.res[0]) == VOICE_REG:
                seeds[VOICE_INDEX].add(("v", f.name, o.args[0]))
            elif o.name == "memref.load" and _label(labels, c, o.res[0]) == VOICE_REG:
                rest = f.split(f.address(o))[1] if o.args[-2] in f.globals else None
                seeds[VOICE_INDEX] |= {("v", f.name, rest)} if rest is not None else set()
    indexed = [(c, id(mod.atom_of.get(id(o))), parts) for c, o, _, (_, _, parts) in recs]
    for c in mod.mains.values():
        for o in c.f.walk():
            p = c.f.promoted.get(id(o))
            if p is not None and p[0] == "v" and isinstance(p[3], str):
                indexed.append((c, ("cell", p[1], p[2]), [p[3]]))
    by = defaultdict(set)
    for c, key, parts in indexed:
        for t in parts:
            n = ("v", c.f.name, t)
            by[key] |= {_label(labels, c, t)} & set(INDICES) | {i for i in INDICES if n in seeds[i]}
    for c, key, parts in indexed:
        for lab in by.get(key, ()):
            seeds[lab] |= {("v", c.f.name, t) for t in parts}
    for lab, nodes in seeds.items():
        for n in nodes:
            flow.seeds[n].add(lab)
    return any(seeds.values())


def name_module(mod):
    """Split every region of ``mod`` into atoms (:func:`atomize`, ``mod.atoms``) and label the
    module's data flow (``mod.labels``); name each atom and promoted cell by its label (``g_``
    and the label, ``_tab`` for a constant array), else an atom ``M_`` and its offset as
    :meth:`Module.var_name` gives it."""
    recs, words = _region_accesses(mod)
    per = defaultdict(list)
    for _, _, k, (base, addrs, _) in recs:
        per[k].append((base, addrs))
    written = mod.stored()
    mod.atoms = {}
    for k, acc in sorted(per.items()):
        mod.atoms[k], bad = atomize(k, acc, words, written)
        mod.straddling[k] = [[a[0], a[-1]] for a in bad]
    mod.atom_bytes = {a: x for xs in mod.atoms.values() for x in xs for a in x.bytes}
    flow = Flow()
    for c in mod.mains.values():
        _function_flow(flow, c, mod)
    mod.atom_of = _memory_flow(flow, recs, mod)
    labels = flow.labels()
    if _index_seeds(flow, labels, recs, mod):
        labels = flow.labels()
    mod.labels = labels
    wanted = []
    for atoms in mod.atoms.values():
        for x in atoms:
            d, x.label = labels.get(("m", x.region, x.org), (None, None))
            tab = "_tab" if x.const and x.kind == "array" else ""
            role = x.label or (POINTER if x.kind == "pair" else None)
            base = f"{GLOBAL}{role}{tab}" if role else "M" + mod.var_name(x.org)[1:]
            wanted.append(((d is None, d or 0, rank(x.label) if x.label else (), x.org), x, base))
    cells = [("cell", a) for a in mod.state.scalars]
    cells += [("cell", s, d) for s, (_, fields) in enumerate(mod.state.structs) for d in fields]
    for key in cells:
        d, lab = labels.get(key, (None, None))
        if lab:
            wanted.append(((False, d, rank(lab), -1), key, f"{GLOBAL}{lab}"))
    names = unique([(_key(x), b) for _, x, b in sorted(wanted, key=lambda w: w[0])], set())
    for _, x, _ in wanted:
        if isinstance(x, Atom):
            x.name = names[_key(x)]
        else:
            mod.cell_names[x] = names[x]


def _key(x):
    """The key of atom or cell ``x`` among the names."""
    return ("atom", x.region, x.org) if isinstance(x, Atom) else x


def _counter(c, v):
    """Whether value ``v`` of function ``c`` is a loop counter: a block argument each edge
    passes a constant or itself plus or less a constant, at least one edge the latter."""
    f, rep = c.f, c.classes.get(v, v)
    srcs = [o.args[i] for o, i, _ in f.arg_src.get(v, ())]
    steps = 0
    for s in srcs:
        d = f.defs.get(s)
        if f.const_of(s) is not None:
            continue
        if d is None or d.name not in ("arith.addi", "arith.subi"):
            return False
        x, k = d.args
        if f.const_of(k) is None or c.classes.get(x, x) != rep:
            return False
        steps += 1
    return steps > 0


def _identifiers(nodes):
    """Every identifier and declarator name in ``nodes``."""
    out = set()
    stack = list(nodes)
    while stack:
        n = stack.pop()
        if isinstance(n, (c_ast.ID, c_ast.Decl)) and isinstance(n.name, str):
            out.add(n.name)
        elif isinstance(n, c_ast.TypeDecl) and n.declname:
            out.add(n.declname)
        stack.extend(child for _, child in n.children())
    return out


def local_names(c, labels, nodes):
    """``{variable: new name}`` of C function ``c`` printed as ``nodes``: each ``vN`` holding
    a labelled value (its class's member nearest a seed) named by its label, else a loop
    counter ``i`` and an address's 16-bit base ``ptr``; unique in each function of ``nodes``,
    the nearest first, then the one in most of them."""
    f, best = c.f, {}
    members = defaultdict(set)
    for v, rep in c.classes.items():
        members[rep].add(v)
    for rep, name in c.names.items():
        if not (name[:1] == "v" and name[1:].isdigit()):
            continue
        got = [labels.get(("v", f.name, v)) for v in members[rep] | {rep}]
        got = [(d, rank(lab), lab) for d, lab in filter(None, got) if lab]
        if got:
            best[name] = min(got)
        elif any(v in f.arg_block and _counter(c, v) for v in members[rep] | {rep}):
            best[name] = (_LOOP, (), "i")
        elif any(_pointer(f, v, set()) for v in members[rep] | {rep}):
            best[name] = (_LOOP, (), POINTER)
    scopes = [_identifiers([x]) for x in nodes if isinstance(x, c_ast.FuncDef)]
    wide = {n: -sum(n in ids for ids in scopes) for n in best}
    order = sorted(best, key=lambda n: (best[n][:2], wide[n], int(n[1:])))
    taken = [ids - set(best) for ids in scopes]
    out = {}
    for n in order:
        mine = [t for t, ids in zip(taken, scopes) if n in ids]
        out[n] = scoped(best[n][2], mine)
    return out


def scoped(base, scopes):
    """The first of ``base``, ``base_2``, ... no C keyword and in none of the name sets
    ``scopes``, added to each."""
    k = 1
    while True:
        name = base if k == 1 else f"{base}_{k}"
        if (
            not any(name in t for t in scopes)
            and not keyword.iskeyword(name)
            and name not in C_WORDS
        ):
            break
        k += 1
    for t in scopes:
        t.add(name)
    return name


def _pointer(f, v, seen):
    """Whether 16-bit value ``v`` of function ``f`` is a memory address or added into one."""
    if v not in f.types or f.width(v) != 16 or v in seen:
        return False
    seen.add(v)
    for u in f.users.get(v, ()):
        if u.name == "arith.index_castui" or u.name == "func.call" and u.args[0] == v:
            return True
        if u.name == "arith.addi" and _pointer(f, u.res[0], seen):
            return True
    return False
