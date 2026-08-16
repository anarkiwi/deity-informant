"""framepath: the fold — the execution is the program (docs/denotation-solve.md 11).

Per-invocation paths recorded over the walker convention merge into one
procedure: a diverging fact is an if with a faulting unobserved default that
rejoins at the next shared control fact; frame-locality forwards through locals.
"""

from __future__ import annotations

import sys

from bisect import bisect_left

from . import datadecl
from . import expr as E
from . import framefuse
from . import frameproc
from . import recorder
from . import sidprog
from . import structured as C
from .grammar import addr_name

_EXIT = C._IRQ_RET  # the sentinel word the frame's balancing return names
_GUARD = 8_000_000
_OUTPUTS = range(C.SID_LO, 0xD41D)
_INPUT_NAMES = sidprog._NAMES  # volatile cell -> iota input name


class FoldError(RuntimeError):
    """The fold met a shape outside the playbook's execution idioms (loud wall)."""


def _driver(play_frame):
    """The walker's invocation convention as a recorder driver (one frame)."""

    def drive(vm, entry, cache, lifter):
        reg = vm.reg
        start = reg[3]
        if start != 0xFF:
            raise FoldError("frame entry sp $%02X is not the convention's" % start)
        reg[0] = 0
        vm.sreg[0] = E.konst(0, 1)
        vm._push(0x00)
        vm._push(0x01 if play_frame == 2 else _EXIT & 0xFF)
        if play_frame > 2:
            vm._push_status()
            reg[10] = 1
            vm.sreg[10] = E.konst(1, 1)
        for i in range(play_frame - 3):
            vm._pushb(reg[i], vm.sreg[i])
        pc = entry
        n = 0
        while reg[3] < start:
            pc = vm.step(pc, cache, lifter)
            n += 1
            if n > _GUARD:
                raise FoldError("runaway frame at $%04X" % pc)

    return drive


def _record(model, frames, assertion=False):
    """Record every play invocation over the pinned window, templates only."""
    return recorder.record(
        bytes(model.mem0),
        _driver(model.play_frame),
        model.play,
        _OUTPUTS,
        frames,
        assertion=assertion,
        light=True,
    )


def _scratch(rec):
    """Cells whose every read follows a same-frame write: frame-locality (9.1).

    A cell some invocation read before writing is state; one a computed or
    patched-operand store may reach must stay memory; the observable SID file
    is always memory. Everything else forwards through a procedure local."""
    written = set()
    for f in rec.F:
        written.update(f)
    return written - rec.rbw - rec.comp_st - set(_OUTPUTS)


def _sname(addr):
    return "s_%04X" % addr


def _uniq(paths):
    """First occurrence of each shared template, with its invocation index."""
    seen = set()
    for i, path in enumerate(paths):
        if id(path) not in seen:
            seen.add(id(path))
            yield i, path


def _lkey(saddr):
    """A computed-load address's identity: its canonical entry-pure form."""
    return E.simplify(E.to_entry(saddr))


def _cfold(n, cvals):
    """The one constant ``n`` evaluates to under the known-constant locals."""
    if n[0] == "const":
        return n[1]
    if n[0] == "loc":
        return cvals.get(n[1])
    if n[0] != "op":
        return None
    vals = [_cfold(c, cvals) for c in n[2]]
    if any(v is None for v in vals):
        return None
    return E._apply(n[1], vals, [frameproc.loc_width(c) for c in n[2]], n[3])


class _Xl:
    """One template's event stream as translated, mergeable items."""

    def __init__(self, scratch):
        self.scratch = scratch
        self.regs_read = set()
        self.uni_addrs = set()
        self.snap = set()  # cells some expression reads at entry value after a store
        self.lnames = {}  # entry-form computed-load address -> materialised local
        self.reb = set()  # registers some template rebinds to a non-identity value
        self.foldable = {}  # (site, kind) -> every occurrence everywhere folded

    def _reg(self, i):
        if i == 0:
            return ("const", 0, 1)
        if i == 3:
            return ("const", 0xFF, 1)
        self.regs_read.add(i)
        return ("loc", frameproc._reg_local(i))

    def _tr(self, n, env, memo):
        got = memo.get(id(n))
        if got is not None and got[0] is n:
            return got[1]
        k = n[0]
        if k == "const":
            r = n
        elif k == "reg":
            r = self._reg(n[1])
        elif k == "uni":
            r = env["uni"][n[1]]
        elif k == "mem":
            a = n[1]
            if a[0] == "const":
                if a[1] in env["wr"]:
                    self.snap.add(a[1])  # an entry read of a cell this frame re-stored
                    r = ("loc", "e_%04X" % a[1])
                else:
                    r = ("mem", a, n[2])
            else:
                name = self.lnames.get((_lkey(a), 0))
                if name is None or name not in env["lds"]:
                    raise FoldError("computed read with no materialised load: %r" % (a,))
                r = ("loc", name)
        elif k == "cur":
            a = n[1][1]
            name = env["curld"].get((a, n[3]))
            if name is not None:
                r = ("loc", name)
            elif a in self.scratch:
                r = ("loc", _sname(a))
            else:
                r = ("mem", n[1], n[2])
        else:
            r = ("op", n[1], tuple(self._tr(c, env, memo) for c in n[2]), n[3])
        memo[id(n)] = (n, r)
        return r

    def _ld_item(self, env, memo, saddr, addr, sz):
        """Materialise a computed load at its own position, where what the
        machine loaded is still live: the evolving image, or the forwarding
        local when the load aliased a frame-local cell (its place fact pins
        which). The cell's store version keys the local, so an aliased re-load
        never rebinds what an earlier load's consumers still read."""
        ver = env["ver"].get(addr, 0)
        name = self.lnames.setdefault((_lkey(saddr), ver), "l%d" % len(self.lnames))
        env["lds"].add(name)
        env["curld"][(addr, ver)] = name
        if addr in self.scratch and addr in env["wr"]:
            return ("asg", name, ("loc", _sname(addr)))
        return ("asg", name, ("mem", self._tr(saddr, env, memo), sz))

    def _uni_item(self, env, n, addr, sz):
        if sz != 1:
            raise FoldError("opaque word read at $%04X" % addr)
        k = env["unik"].get(addr, 0)
        env["unik"][addr] = k + 1
        name = "%s%d" % (_INPUT_NAMES.get(addr) or "io_%04X" % addr, k)
        env["uni"][n] = ("loc", name)
        self.uni_addrs.add(addr)
        return ("asg", name, ("mem", ("const", addr, 2), 1))

    def _store(self, out, env, memo, addr, expr):
        val = self._tr(expr, env, memo)
        env["wr"].add(addr)
        env["ver"][addr] = env["ver"].get(addr, 0) + 1
        memo.clear()  # entry-form validity is per store epoch
        if addr in self.scratch:
            name = _sname(addr)
            v = _cfold(val, env["cvals"])
            if v is None:
                env["cvals"].pop(name, None)
            else:
                env["cvals"][name] = v
            out.append(("asg", name, val))
        else:
            out.append(("st", ("const", addr, 2), val))

    def _guard(self, out, env, site, kind, expr, obs):
        """Emit the fact; folding is a global verdict, taken after all templates.

        A drop must be path-independent or the merge desynchronises, so each
        occurrence only votes: the site drops when every vote everywhere folds."""
        v = _cfold(expr, env["cvals"])
        if v is not None and v != obs:
            raise FoldError("constant %s guard at $%04X misses %r" % (kind, site, obs))
        key = (site, kind)
        self.foldable[key] = self.foldable.get(key, True) and v is not None
        out.append(("ck", site, kind, expr, obs))

    def template(self, path, regs):
        """Translate one template; the exit register rebinds close the frame."""
        env = {
            "uni": {},
            "cvals": {},
            "unik": {},
            "wr": set(),
            "lds": set(),
            "ver": {},
            "curld": {},
        }
        memo = {}
        out = [("asg", "e_%04X" % a, ("mem", ("const", a, 2), 1)) for a in sorted(self.snap)]
        pend = None  # a store's place fact, held for the store it belongs to
        for ev in path:
            k = ev[0]
            if pend is not None:
                site, saddr, obs = pend
                pend = None
                if k == "st" and ev[1] == site == obs:
                    val = self._tr(ev[2], env, memo)
                    out.append(("st", self._tr(saddr, env, memo), val))
                    env["wr"].add(ev[1])
                    env["ver"][ev[1]] = env["ver"].get(ev[1], 0) + 1
                    memo.clear()
                    continue
                self._guard(out, env, site, "place", self._tr(saddr, env, memo), obs)
            if k == "uni":
                out.append(self._uni_item(env, ev[1], ev[2], ev[3]))
            elif k == "ld":
                out.append(self._ld_item(env, memo, ev[1], ev[2], ev[3]))
            elif k == "st":
                if ev[3] != 1:
                    raise FoldError("wide store at $%04X" % ev[1])
                self._store(out, env, memo, ev[1], ev[2])
            elif ev[2] == "place":
                pend = (ev[1], ev[3], ev[4])
            else:
                self._guard(out, env, ev[1], ev[2], self._tr(ev[3], env, memo), ev[4])
        if pend is not None:
            self._guard(out, env, pend[0], "place", self._tr(pend[1], env, memo), pend[2])
        for i in sorted(self.regs_read):
            name = frameproc._reg_local(i)
            val = self._tr(regs[i], env, memo)
            if val != ("loc", name):
                self.reb.add(i)
            if i in self.reb:
                out.append(("asg", name, val))
        out.append(("ret",))
        return tuple(out)


# ---- the merge: shared items emit once, a divergence forks and rejoins ----------
_ANCHOR_TRIES = 48  # candidates scanned for the cheapest rejoin before settling


class _Tab:
    """Item interning: the merge compares small ints, not expression trees."""

    def __init__(self):
        self.ids = {}
        self.items = []

    def encode(self, template):
        out = []
        for it in template:
            i = self.ids.get(it)
            if i is None:
                i = self.ids[it] = len(self.items)
                self.items.append(it)
            out.append(i)
        return tuple(out)


def _posmap(seq, tab, maps):
    """Guard key -> ascending positions in ``seq``, computed once per template.

    Only a control fact anchors a rejoin: its (site, kind, expr) witnesses that
    every path stands at the same control point, whatever it observed there."""
    got = maps.get(id(seq))
    if got is None:
        got = {}
        items = tab.items
        for i, x in enumerate(seq):
            it = items[x]
            if it[0] == "ck":
                got.setdefault(it[1:4], []).append(i)
        maps[id(seq)] = got
    return got


def _nth_last_at(posmap, key, m, lo, hi):
    """The ``m``-th-from-last position of guard ``key`` in ``[lo, hi)``, else None."""
    ps = posmap.get(key)
    if not ps:
        return None
    i = bisect_left(ps, hi) - m
    return ps[i] if i >= 0 and ps[i] >= lo else None


def _anchor(ranges, tab, maps):
    """Per-range stop positions of the cheapest shared control fact, else None.

    Occurrences count backwards from the shared frame exit, so a loop-repeated
    site aligns iteration with iteration however the divergent arms differ;
    the bounded scan takes the best rejoin it sees, not a proven minimum."""
    seq, lo, hi = min(ranges, key=lambda r: r[2] - r[1])
    base = _posmap(seq, tab, maps)
    best_score, best_stops = None, None
    tried = 0
    for p in range(lo + 1, hi):
        if tab.items[seq[p]][0] != "ck":
            continue
        tried += 1
        if tried > _ANCHOR_TRIES and best_stops is not None:
            break
        key = tab.items[seq[p]][1:4]
        m = bisect_left(base[key], hi) - bisect_left(base[key], p)
        stops = []
        for r in ranges:
            q = _nth_last_at(_posmap(r[0], tab, maps), key, m, r[1] + 1, r[2])
            if q is None:
                stops = None
                break
            stops.append(q)
        if stops is None:
            continue
        score = max(q - r[1] for q, r in zip(stops, ranges))
        if best_stops is None or score < best_score:
            best_score, best_stops = score, stops
        if score <= p - lo:
            break
    return best_stops


def _merge(ranges, out, tab, maps=None, anchors=True):
    """Merge interned item ranges into ``out``; every divergence is a shared fact."""
    maps = {} if maps is None else maps
    while True:
        if any(lo >= hi for _i, lo, hi in ranges):
            if any(lo < hi for _i, lo, hi in ranges):
                raise FoldError("a path ends inside another's body")
            return
        heads = {seq[lo] for seq, lo, _hi in ranges}
        if len(heads) == 1:
            out.append(tab.items[next(iter(heads))])
            ranges = [(i, lo + 1, hi) for i, lo, hi in ranges]
            continue
        _fork(ranges, out, tab, maps, anchors)
        return


def _fork(ranges, out, tab, maps, anchors):
    """One divergence: partition on the shared fact, rejoin at a common anchor.

    An anchor is a bet that the paths reconverge there; the continuation is
    merged inside the bet, so a false rejoin rolls back to honest arm-to-end
    duplication, which bottoms out at single paths and cannot diverge."""
    heads = [tab.items[seq[lo]] for seq, lo, _hi in ranges]
    fams = {h[1:4] for h in heads if h[0] == "ck"}
    if len(fams) != 1 or any(h[0] != "ck" for h in heads):
        raise FoldError(
            "divergence without a shared guard: %s"
            % " / ".join(sorted({repr(h[:3]) for h in heads}))
        )
    site, kind, expr = next(iter(fams))
    ends = [hi for _i, _lo, hi in ranges]
    attempts = []
    if anchors:
        stops = _anchor(ranges, tab, maps)
        if stops is not None and stops != ends:
            attempts.append(stops)
    attempts.append(ends)
    mark = len(out)
    for stops in attempts:
        try:
            groups = {}
            for r, h, stop in zip(ranges, heads, stops):
                groups.setdefault(h[4], []).append((r[0], r[1] + 1, stop))
            arms = []
            for obs in sorted(groups):
                body = []
                _merge(groups[obs], body, tab, maps, anchors)
                arms.append((obs, tuple(body)))
            out.append(("fork", site, kind, expr, tuple(arms)))
            _merge([(r[0], stop, r[2]) for r, stop in zip(ranges, stops)], out, tab, maps, anchors)
            return
        except FoldError:
            del out[mark:]
    raise FoldError("no consistent fork at $%04X" % site)


# ---- emission: items to dialect statements --------------------------------------
def _eq(expr, obs):
    w = frameproc.loc_width(expr)
    return ("op", "INT_EQUAL", (expr, ("const", obs & E.mask(w), w)), 1)


def _stmts(items):
    out = []
    for it in items:
        k = it[0]
        if k == "asg":
            out.append(("asg", it[1], it[2]))
        elif k == "st":
            out.append(("st", it[1], it[2]))
        elif k == "ret":
            out.append(("ret", False))
        elif k == "ck":
            _k, site, kind, expr, obs = it
            if kind == "branch":
                out.append(("if", "if" if obs == 0 else "ifnot", expr, [("unobs", site)], []))
            else:
                out.append(("if", "ifnot", _eq(expr, obs), [("unobs", site)], []))
        else:
            _k, site, kind, expr, arms = it
            if kind == "branch" and len(arms) == 2 and {a[0] for a in arms} == {0, 1}:
                out.append(("if", "if", expr, _stmts(arms[1][1]), _stmts(arms[0][1])))
                continue
            node = [("unobs", site)]
            for obs, body in reversed(arms):
                node = [("if", "if", _eq(expr, obs), _stmts(body), node)]
            out.extend(node)
    return out


# ---- liveness: an unread frame-local write is no write at all --------------------
def _expr_locs(n, out):
    stack = [n]
    while stack:
        x = stack.pop()
        if x[0] == "loc":
            out.add(x[1])
        else:
            stack.extend(frameproc._kids(x))


def _item_reads(items, out):
    for it in items:
        k = it[0]
        if k in ("asg", "st"):
            _expr_locs(it[2], out)
            if k == "st":
                _expr_locs(it[1], out)
        elif k == "ck":
            _expr_locs(it[3], out)
        elif k == "fork":
            _expr_locs(it[3], out)
            for _obs, body in it[4]:
                _item_reads(body, out)


def _prune(items, used):
    out = []
    for it in items:
        if it[0] == "asg" and it[1] not in used:
            continue
        if it[0] == "fork":
            arms = tuple((obs, _prune(body, used)) for obs, body in it[4])
            it = it[:4] + (arms,)
        out.append(it)
    return tuple(out)


def _sweep(items, keep):
    """Items less every local assignment no surviving expression reads."""
    while True:
        used = set(keep)
        _item_reads(items, used)
        pruned = _prune(items, used)
        if pruned == items:
            return items
        items = pruned


# ---- the program ------------------------------------------------------------------
def _state_fields(stmts, decls, dispatch, uni_addrs):
    """The record header off the folded text: cells it names, inputs it reads."""
    scalars, arrays = set(), set()
    for s in framefuse.stmts_of(stmts):
        for x in frameproc._stmt_exprs(s):
            sidprog._scan(x, scalars, arrays)
    spans = [(d["base"], d["base"] + d["size"]) for d in decls]

    def hidden(a):
        if a in _INPUT_NAMES or a in C._VOL0 or C.SID_LO <= a <= 0xD41C:
            return True
        return any(lo <= a < hi for lo, hi in spans)

    fields = [
        (addr_name(a), 1, False, sorted(dispatch.get(a, ())))
        for a in sorted((scalars | set(dispatch)) - arrays)
        if not hidden(a)
    ]
    fields += [(addr_name(a), 1, True, []) for a in sorted(arrays) if not hidden(a)]
    inputs = sorted(_INPUT_NAMES[a] for a in uni_addrs if a in _INPUT_NAMES)
    return fields, inputs


def fold(model, frames, assertion=False):
    """``(statements, params, opaque-read cells)`` of the folded frame body."""
    rec = _record(model, frames, assertion)
    xl = _Xl(_scratch(rec))
    templates = []
    for _round in range(64):  # the read/rebind/snapshot sets close monotonically
        before = (set(xl.regs_read), set(xl.snap), set(xl.reb))
        xl.foldable = {}
        templates = [xl.template(p, rec.regs[i]) for i, p in _uniq(rec.paths)]
        if (xl.regs_read, xl.snap, xl.reb) == before:
            break
    drop = {key for key, always in xl.foldable.items() if always}
    tab = _Tab()
    seqs = dict.fromkeys(
        tab.encode(it for it in t if not (it[0] == "ck" and it[1:3] in drop)) for t in templates
    )
    keep = {frameproc._reg_local(i) for i in xl.regs_read}
    ranges = [(s, 0, len(s)) for s in seqs]
    merged = []
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 200_000))  # fork chains are item-bounded
    try:
        _merge(ranges, merged, tab)
    finally:
        sys.setrecursionlimit(limit)
    stmts = _stmts(_sweep(tuple(merged), keep))
    params = frameproc._by_reg(keep)
    return stmts, params, xl.uni_addrs


def program(model, frames):
    """The frame program of a committed model, folded from its own execution."""
    from . import frameprog  # pylint: disable=import-outside-toplevel,cyclic-import

    decls, aliases = datadecl.declarations(model)
    symbols = dict(aliases or {})
    stmts, params, uni_addrs = fold(model, frames)
    fields, inputs = _state_fields(stmts, decls, model.dispatch_sets, uni_addrs)
    prov0, sites, census = frameprog._init_copies(model, decls)
    init_proofs = [frameprog._init_proof(pc, *v) for pc, v in sites.items()]
    prog = frameprog.FrameProgram(
        model.play,
        model.init,
        getattr(model, "subtune", 0),
        getattr(model, "prologue", ()),
        inputs,
        fields,
        decls,
        symbols,
        [(model.play, list(params), [], stmts)],
        bytearray(model.mem0),
        frameprog._aliased(init_proofs, symbols),
        (),
        {},
        (),
        prov0,
        census,
        (),
        model.dispatch_sets,
        frameprog._evidence(model, prov0, sites, census),
        entry_frame=model.play_frame,
    )
    prog.roles, prog.bounds = frameprog._roles(prog)
    prog.operators = frameprog._operators(model)
    return prog
