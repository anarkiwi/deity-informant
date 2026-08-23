"""S7 text form: the certified tuneprog as anatomy-style pseudocode (``tuneprog.md``).

Prints the S5 node tree with the S6 names: ``meta``, ``state`` (regions, roles and
struct views), ``data`` (:mod:`.datablock`), ``inputs``, then one procedure each.
Machine plumbing (stack frames, register copies nothing reads) is dropped for print.
"""

from __future__ import annotations

from .closure import closed_blocks
from .datablock import section
from .ir import Bin, Let, REGVAR, Var
from .irwalk import call_order, forwarder, walk as ewalk
from .live import printable
from .machine import PAL_FRAME
from .pseudocode import IND, NEG, Printer, hexlit
from .structure import Blk, Case, Cond, Exit, For, Jump, Loop, hidden, strip, walk

PHASES = {1: "init", 2: "tick", 3: "init+tick"}


class Body(Printer):
    """Renders structured nodes into indented pseudocode lines."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._shut = {}

    def shut(self, proc):
        """The blocks of ``proc`` only the static closure reaches, computed once."""
        if proc not in self._shut:
            self._shut[proc] = closed_blocks(self.prog.procs[proc])
        return self._shut[proc]

    def render(self, name, body):
        self.tmp, self.mem, self.alias, self.proc = {}, {}, {}, name
        self.lastsrc = None
        # a `for` header states its index and where it starts, inside the loop and before it
        self.hide = frozenset(n for x in walk(body) if type(x) is For for n in x.hide)
        p = self.prog.procs[name]
        args = ", ".join(REGVAR[i].lower() for i in self.params[name])
        head = "%s(%s):" % (self.names.procs.get(name, name), args)
        out = ["%-40s # $%04X, %s calls" % (head, p.blocks[p.entry].src, num(_calls(p)))]
        return out + self.nodes(body, name, 1)

    def nodes(self, body, proc, depth):
        out, marks = [], []
        for n in body:
            hit = _untaken_arm(n)
            if hit is not None:
                marks.append(self.negate(hit[0]) if hit[1] else self.expr(hit[0]))
                continue
            lines = self.node(n, proc, depth)
            if marks and _mark(lines, "untaken: %s" % ", ".join(marks)):
                marks = []
            out.extend(lines)
        if marks:
            out.append("%s# untaken: %s" % (IND * depth, ", ".join(marks)))
        return out or [IND * depth + "pass"]

    def node(self, n, proc, depth):
        pad = IND * depth
        t = type(n)
        if t is Blk:
            return self.blk(n, proc, pad)
        if t is Cond:
            return self.cond(n, proc, depth)
        if t is Case:
            return self.case(n, proc, depth)
        if t is For:
            return self.forloop(n, proc, depth)
        if t is Loop:
            return self.loop(n, proc, depth)
        if t is Jump:
            return [pad + (n.kind if n.kind != "goto" else "goto %s" % n.label)]
        if n.kind != "return":
            return [pad + "trap %r" % n.why]
        return [pad + ("return %s" % self.expr(n.e) if n.e is not None else "return")]

    def blk(self, n, proc, pad):
        live = self.live[proc]
        stmts = [s for s in n.stmts if printable(s, live) and not hidden(s, self.hide)]
        if not stmts:
            return []
        self.mem = {}
        self.defs = {s.n: s.e for s in stmts if type(s) is Let}
        head = ["%s# $%04X" % (pad, n.src)] if self.pcs and n.src != self.lastsrc else []
        self.lastsrc = n.src
        mark = self.unverified(proc, n.label)
        return head + [pad + self.stmt(s) + mark for s in stmts]

    def unverified(self, proc, label):
        """The mark a statement no execution covers carries: the static closure, or which ``v``."""
        b = self.prog.procs[proc].blocks.get(label)
        if label in self.shut(proc):
            return "  # unverified (static closure)"
        cover = tuple(getattr(b, "cover", ()) or ())
        if not cover or 0 not in cover:
            return ""
        ran = [str(i) for i, c in enumerate(cover) if c]
        return "  # unverified (ran for v = %s)" % ", ".join(ran) if ran else "  # unverified"

    def cond(self, n, proc, depth):
        pad = IND * depth
        c, flip = self.expr(n.c), False
        neg = self.negate(n.c)
        both = self.arms([n.then, n.els], proc, depth + 1)
        then, els = both[0], both[1]
        if then == [IND * (depth + 1) + "pass"] and els != [IND * (depth + 1) + "pass"]:
            then, els, flip = els, ["%spass" % (IND * (depth + 1))], True
        if flip:
            c = neg
        if len(then) == 1 and len(els) == 1 and els[-1].endswith("pass"):
            return ["%sif %s: %s" % (pad, c, then[0].strip())]
        if len(then) == 1 and len(els) == 1:
            return ["%sif %s: %s else: %s" % (pad, c, then[0].strip(), els[0].strip())]
        out = ["%sif %s:" % (pad, c)] + then
        return out if els[-1].endswith("pass") and len(els) == 1 else out + ["%selse:" % pad] + els

    def arms(self, bodies, proc, depth):
        """Render sibling arms: each starts from the state the test saw, none survives."""
        saved, out = dict(self.mem), []
        for b in bodies:
            self.mem = dict(saved)
            out.append(self.nodes(b, proc, depth))
        self.mem = {}
        return out

    def negate(self, c):
        if type(c) is Bin and c.op in NEG:
            return self.expr(Bin(NEG[c.op], c.a, c.b, c.w))
        return "not %s" % self.expr(c)

    def case(self, n, proc, depth):
        pad = IND * depth
        out = ["%sswitch %s:" % (pad, self.expr(n.e))]
        arms = self.arms([b for _v, b in n.cases], proc, depth + 2)
        for (v, _b), body in zip(n.cases, arms):
            out.append("%s%scase %s:" % (pad, IND, hexlit(v)))
            out.extend(body)
        return out

    def forloop(self, n, proc, depth):
        pad = IND * depth
        vals = tuple(v // n.scale for v in n.values)
        rng = _val_list(vals)
        alias, hide, fvars = dict(self.alias), set(self.hide), dict(self.fvars)
        var = _ivar(self.fors)
        self.alias[n.var] = (var, n.scale)
        self.hide |= n.hide
        self.fors += 1
        if n.group:
            self.fvars[n.group] = var
        body = self.arms([strip(n.body, n.label, self.hide)], proc, depth + 1)[0]
        self.alias, self.hide, self.fors = alias, hide, self.fors - 1
        self.fvars = fvars
        return ["%sfor %s in %s:%s" % (pad, var, rng, _times(n.count))] + body

    def loop(self, n, proc, depth):
        pad = IND * depth
        spin = self.spin(n)
        if spin is not None:
            return ["%swhile %s: pass%s" % (pad, spin, _times(n.count))]
        body = self.arms([n.body], proc, depth + 1)[0]
        return ["%swhile True:%s" % (pad, _times(n.count))] + body

    def spin(self, n):
        """A body that only reads and tests is a busy-wait: ``while cond: pass``."""
        conds = [x for x in n.body if type(x) is Cond]
        if any(type(x) not in (Blk, Cond, Jump) for x in n.body) or len(conds) != 1:
            return None
        c = conds[0]
        blks = [x for x in n.body + c.then + c.els if type(x) is Blk]
        if any(type(s) is not Let for b in blks for s in b.stmts):
            return None
        if any(type(x) not in (Blk, Jump) for x in c.then + c.els):
            return None
        jumps = ([x for x in c.then if type(x) is Jump], [x for x in c.els if type(x) is Jump])
        arms = [k for k, b in zip("tf", jumps) if any(x.kind == "continue" for x in b)]
        if len(arms) != 1:
            return None
        defs = {s.n: s.e for b in blks for s in b.stmts}
        if not _acyclic(defs):
            return None  # a value the body defines from itself is a recurrence, not a wait
        self.inline = defs
        out = self.expr(c.c) if arms[0] == "t" else self.negate(c.c)
        self.inline = {}
        return out


def _untaken_arm(n):
    """The condition of a branch whose one direction is a bare untaken trap, or ``None``.

    The direction is the coverage fact, not a statement of the program: it prints as
    a mark on what the covered direction reaches, and ``meta`` carries the count.
    """
    if type(n) is not Cond:
        return None
    for arm, other, neg in ((n.then, n.els, False), (n.els, n.then, True)):
        if other or len(arm) != 1 or type(arm[0]) is not Exit:
            continue
        if arm[0].kind == "trap" and arm[0].why == "untaken":
            return n.c, neg
    return None


def _mark(lines, text):
    """Append a mark to the first line that is not a pc comment; False if there is none."""
    i = next((k for k, l in enumerate(lines) if not l.lstrip().startswith("#")), None)
    if i is None:
        return False
    lines[i] += ("; " if "  # " in lines[i] else "  # ") + text
    return True


def untaken(structured):
    """How many branch directions the trace never took, over the whole program."""
    return sum(
        type(n) is Exit and n.kind == "trap" and n.why == "untaken"
        for body in structured.values()
        for n in walk(body)
    )


def _acyclic(defs):
    """True where no definition in ``defs`` reads itself, through any chain of them."""
    state = {}

    def reach(n):
        st = state.get(n)
        if st is not None:
            return st
        state[n] = 0
        for x in ewalk(defs[n]):
            if type(x) is Var and x.n in defs and not reach(x.n):
                return 0
        state[n] = 1
        return 1

    return all(reach(n) for n in defs)


def _ivar(n):
    return "vwxyz"[min(n, 4)]


def _val_list(vals):
    if len(vals) > 3 and vals == tuple(
        range(
            vals[0], vals[-1] + (1 if vals[0] < vals[-1] else -1), 1 if vals[0] < vals[-1] else -1
        )
    ):
        return "%d..%d" % (vals[0], vals[-1])
    return ", ".join(str(v) for v in vals)


def _times(n):
    return "" if not n else "   # x%s" % num(n)


def num(n):
    """A count with thousands separators."""
    return "{:,}".format(n)


def _calls(p):
    return p.blocks[p.entry].count


def _meta(prog, names, cert, shut=0):
    """The ``meta`` block: entry, cadence, subtunes, model, coverage, certificate."""
    m = prog.meta
    e = m.get("entry", {})
    rate = PAL_FRAME / e.get("cycles_per_tick", PAL_FRAME)
    out = [
        "entry     %s $%04X every %d cycles (%.1f calls/frame, %s)"
        % (e.get("kind"), e.get("addr", 0), e.get("cycles_per_tick", 0), rate, e.get("source")),
        "subtunes  %d, playing song %d; sid model %s"
        % (m.get("songs", 1), (m.get("song") or 0) + 1, m.get("sid_model") or "as traced"),
        "program   %d procedures, %d blocks, %d statements, %d regions"
        % (
            len(prog.procs),
            sum(len(p.blocks) for p in prog.procs.values()),
            sum(len(b.stmts) for p in prog.procs.values() for b in p.blocks.values()),
            len([r for r in prog.storage if r.id >= 0]),
        ),
    ]
    if shut:
        out.append("untaken   %d branch directions the trace never took (marked)" % shut)
    if names.sidwrite is not None:
        order, against = names.sidwrite
        other = "lo" if order == "hi" else "hi"
        out.append(
            "sid       16-bit registers written %s then %s%s"
            % (
                order,
                other,
                (
                    ""
                    if not against
                    else "; %s %s then %s (marked)"
                    % (_plural(against, "write", "writes"), other, order)
                ),
            )
        )
    if names.phase is not None:
        out.append("phase     %s selects the rate" % names.region.get(names.phase[0], "?"))
    if names.copies and names.copies.get("families"):
        c = names.copies
        out.append(
            "copies    %s over %s copies, %s rows; %s of %s statements unverified (marked)"
            % (
                _plural(len(c["families"]), "family", "families"),
                ", ".join(str(f["copies"]) for f in c["families"]),
                num(sum(f["rows"] for f in c["families"])),
                num(c["unverified"]),
                num(c["statements"]),
            )
        )
    for r in (names.copies or {}).get("refused", ()):
        out.append("copies    %s at %s refused: %s" % (r["proc"], r["base"], r["why"]))
    if cert:
        s = cert["subtunes"][0]
        out.append(
            "certified %s calls, %s divergences, period %s, first repeat at call %s (%s),"
            " stack %s, stage %s"
            % (
                num(s["ticks"]),
                s["divergences"],
                num(s["period"]) if s["period"] else "none",
                num(s["first_repeat"]) if s["first_repeat"] is not None else "-",
                "complete" if s["complete"] else "horizon",
                _stack(cert.get("stack")),
                cert.get("stage"),
            )
        )
    return out


def _stack(v):
    """The certificate's stack field in one phrase."""
    if isinstance(v, dict):
        return "residual, depth %s in %s" % (v.get("depth"), ", ".join(v.get("procs", ())))
    return v or "?"


def _plural(n, one, many):
    return "%d %s" % (n, one if n == 1 else many)


def _row(r, names, extra=""):
    return "%-16s $%04X %-14s %-10s %s" % (
        names.region.get(r.id, "r%d" % r.id),
        r.base,
        "%d bytes%s" % (r.size, "" if r.stride < 2 else " stride %d" % r.stride),
        names.role.get(r.id, ""),
        extra or names.notes.get(r.id, ""),
    )


def _state(prog, names):
    """The ``state`` block: struct views first, then the scalars."""
    out = []
    for g, d in sorted(names.groups.items()):
        if d.get("split") is not None:
            r = _rgn(prog, d["split"])
            out.append(
                "%s[%d]  $%04X %d bytes, stride %d, %d fields"
                % (g, d["n"], r.base, r.size, d["stride"], len(d["fields"]))
            )
            out += ["  .%-14s +%d" % (f, k) for k, f in sorted(d["fields"].items())]
            continue
        cells = d.get("cells") or {}
        fields = {}
        for rid in sorted(set(d["members"]), key=lambda i: _base(prog, i)):
            fields.setdefault(names.view[rid][1], []).append(rid)
        what = (["per-copy cells"] if cells else []) + (
            ["stride %d" % d["stride"]] if fields else []
        )
        out.append("%s[%d]  %s, %d fields" % (g, d["n"], ", ".join(what), len(cells) + len(fields)))
        out += [
            "  .%-14s %s" % (f, " ".join("$%04X" % a for _r, a in cs)) for f, cs in cells.items()
        ]
        for fname, rids in fields.items():
            out.append(
                "  .%-14s %-24s %-10s %s"
                % (
                    fname,
                    " ".join("$%04X" % _base(prog, i) for i in rids),
                    names.role.get(rids[0], ""),
                    names.notes.get(rids[0], ""),
                )
            )
    half = {}
    for pair in names.u16:
        for rid, addr in pair:
            half.setdefault(rid, set()).add(addr)
    for pair, name in sorted(names.u16.items(), key=lambda kv: kv[0][0][1]):
        (lo, la), (_hi, ha) = pair
        if lo in names.view:
            continue
        out.append(
            "%-16s $%04X %-14s %-10s %s"
            % (name, la, "u16", names.role.get(lo, ""), "lo|hi $%04X" % ha)
        )
    # a scalar a local group names only inside its loop is still its own row
    cells = {rid for (rid, _a), hits in names.slots.items() if any(not h[3] for h in hits)}
    for r in sorted(prog.storage, key=lambda x: x.base):
        if r.id < 0 or r.id in names.view or r.kind not in ("state", "init_constant"):
            continue
        if r.id in cells and r.size <= 2:
            continue  # a scalar the group view already lists, address by address
        if not _all_paired(r, half.get(r.id)):
            out.append(_row(r, names, "init-only" if r.kind == "init_constant" else ""))
    return out


def _all_paired(r, addrs):
    """True when every cell of a region is a half of some named 16-bit pair."""
    return addrs is not None and set(range(r.base, r.base + r.size)) <= addrs


def _inputs(prog):
    return [
        "$%04X %-14s at $%04X, %d reads (%s)" % (addr, kind, pc, count, PHASES.get(phase, phase))
        for pc, addr, kind, count, phase in prog.inputs
    ]


def _rgn(prog, rid):
    return next(r for r in prog.storage if r.id == rid)


def _base(prog, rid):
    return _rgn(prog, rid).base


def render(prog, structured, names, cert=None, pcs=True):
    """``tuneprog.md``: meta, state, data, inputs, then every procedure.

    The procedures render first: the data section states each table's accessors in
    the form the program prints them in, which is what rendering them collects.
    """
    body = Body(prog, names, pcs)
    procs = [body.render(name, structured[name]) for name in _procs_order(prog)]
    out = ["# tuneprog: %s" % prog.meta.get("name", "?"), ""]
    for title, lines in (
        ("meta", _meta(prog, names, cert, untaken(structured))),
        ("state", _state(prog, names)),
        ("data", section(prog, names, body.sites)),
        ("inputs", _inputs(prog)),
    ):
        out += ["## %s" % title, "", "```"] + (lines or ["(none)"]) + ["```", ""]
    out += ["## program", ""]
    for lines in procs:
        out += ["```"] + lines + ["```", ""]
    return "\n".join(out) + "\n"


def _procs_order(prog):
    """The tick first, then what it calls, then init and the rest; forwarders elided."""
    tick, init = prog.meta.get("tick_proc"), prog.meta.get("init_proc")
    hot = [n for n in reversed(call_order(prog)) if n != init]
    order = [n for n in (tick,) if n in hot] + [n for n in hot if n != tick]
    order += [n for n in prog.procs if n not in order]
    return [n for n in order if forwarder(prog.procs[n]) is None]
