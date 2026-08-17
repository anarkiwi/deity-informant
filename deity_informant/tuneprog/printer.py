"""S7 text form: the certified tuneprog as anatomy-style pseudocode (``tuneprog.md``).

Prints the S5 node tree with the S6 names: ``meta``, ``state`` (regions, roles and
struct views), ``const`` tables, ``inputs``, then one procedure each. Machine
plumbing (stack frames, register copies nothing reads) is dropped for print only.
"""

from __future__ import annotations

from .ir import Bin, Let, REGVAR
from .irwalk import call_order, forwarder
from .live import printable
from .machine import PAL_FRAME
from .pseudocode import IND, NEG, Printer, _hex
from .structure import Blk, Case, Cond, For, Jump, Loop

PHASES = {1: "init", 2: "tick", 3: "init+tick"}


class Body(Printer):
    """Renders structured nodes into indented pseudocode lines."""

    def render(self, name, body):
        self.tmp, self.mem, self.alias, self.proc = {}, {}, {}, name
        self.lastsrc = None
        p = self.prog.procs[name]
        args = ", ".join(REGVAR[i].lower() for i in self.params[name])
        head = "%s(%s):" % (self.names.procs.get(name, name), args)
        out = ["%-40s # $%04X, %s calls" % (head, p.blocks[p.entry].src, num(_calls(p)))]
        return out + self.nodes(body, name, 1)

    def nodes(self, body, proc, depth):
        out = []
        for n in body:
            out.extend(self.node(n, proc, depth))
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
        stmts = [s for s in n.stmts if printable(s, live) and not _hidden(s, self.hide)]
        if not stmts:
            return []
        self.mem = {}
        self.defs = {s.n: s.e for s in stmts if type(s) is Let}
        head = ["%s# $%04X" % (pad, n.src)] if self.pcs and n.src != self.lastsrc else []
        self.lastsrc = n.src
        return head + [pad + self.stmt(s) for s in stmts]

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
            out.append("%s%scase %s:" % (pad, IND, _hex(v)))
            out.extend(body)
        return out

    def forloop(self, n, proc, depth):
        pad = IND * depth
        vals = tuple(v // n.scale for v in n.values)
        rng = _range(vals)
        alias, hide = dict(self.alias), set(self.hide)
        group, fvar = self.fgroup, self.fvar
        var = _ivar(self.fors)
        self.alias[n.var] = (var, n.scale)
        self.hide |= n.hide
        self.fors += 1
        if n.group:
            self.fgroup, self.fvar = n.group, var
        body = self.arms([_strip(n.body, n.label, self.hide)], proc, depth + 1)[0]
        self.alias, self.hide, self.fors = alias, hide, self.fors - 1
        self.fgroup, self.fvar = group, fvar
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
        self.inline = {s.n: s.e for b in blks for s in b.stmts}
        out = self.expr(c.c) if arms[0] == "t" else self.negate(c.c)
        self.inline = {}
        return out


def _hidden(s, hide):
    return type(s) is Let and s.n in hide


def _strip(body, label, hide):
    """Drop the induction test and the back edge a ``for`` header already states."""
    out = []
    for n in body:
        if type(n) is Cond and _jumps_only(n.then + n.els, hide):
            continue
        if type(n) is Jump and n.label == label:
            continue
        out.append(n)
    return out


def _jumps_only(nodes, hide):
    """True when a branch arm only jumps (its blocks are empty or hidden)."""
    for n in nodes:
        if type(n) is Jump:
            continue
        if type(n) is not Blk or any(not _hidden(s, hide) for s in n.stmts):
            return False
    return True


def _ivar(n):
    return "vwxyz"[min(n, 4)]


def _range(vals):
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


def _meta(prog, names, cert):
    """The ``meta`` block: entry, cadence, subtunes, model, certificate."""
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
    if names.phase is not None:
        out.append("phase     %s selects the rate" % names.region.get(names.phase[0], "?"))
    if cert:
        s = cert["subtunes"][0]
        out.append(
            "certified %s calls, %s divergences, period %s, first repeat at call %s (%s), stage %s"
            % (
                num(s["ticks"]),
                s["divergences"],
                num(s["period"]) if s["period"] else "none",
                num(s["first_repeat"]) if s["first_repeat"] is not None else "-",
                "complete" if s["complete"] else "horizon",
                cert.get("stage"),
            )
        )
    return out


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
        if d.get("cells"):
            out.append("%s[%d]  per-copy cells, %d fields" % (g, d["n"], len(d["cells"])))
            out += [
                "  .%-14s %s" % (f, " ".join("$%04X" % a for _r, a in cells))
                for f, cells in d["cells"].items()
            ]
            continue
        fields = {}
        for rid in sorted(set(d["members"]), key=lambda i: _base(prog, i)):
            fields.setdefault(names.view[rid][1], []).append(rid)
        out.append("%s[%d]  stride %d, %d fields" % (g, d["n"], d["stride"], len(fields)))
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
    half = {r for p in names.u16 for r in p}
    for (lo, hi), name in sorted(names.u16.items(), key=lambda kv: _base(prog, kv[0][0])):
        if lo in names.view:
            continue
        out.append(
            "%-16s $%04X %-14s %-10s %s"
            % (
                name,
                _base(prog, lo),
                "u16",
                names.role.get(lo, ""),
                "lo|hi $%04X" % _base(prog, hi),
            )
        )
    for r in sorted(prog.storage, key=lambda x: x.base):
        if r.id < 0 or r.id in names.view or r.kind not in ("state", "init_constant"):
            continue
        if r.id not in half:
            out.append(_row(r, names, "init-only" if r.kind == "init_constant" else ""))
    return out


def _const(prog, names):
    return [
        _row(r, names)
        for r in sorted(prog.storage, key=lambda x: x.base)
        if r.id >= 0 and r.kind in ("const", "image") and r.id not in names.view
    ]


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
    """``tuneprog.md``: meta, state, const, inputs, then every procedure."""
    body = Body(prog, names, pcs)
    out = ["# tuneprog: %s" % prog.meta.get("name", "?"), ""]
    for title, lines in (
        ("meta", _meta(prog, names, cert)),
        ("state", _state(prog, names)),
        ("const", _const(prog, names)),
        ("inputs", _inputs(prog)),
    ):
        out += ["## %s" % title, "", "```"] + (lines or ["(none)"]) + ["```", ""]
    out += ["## program", ""]
    for name in _procs_order(prog):
        out += ["```"] + body.render(name, structured[name]) + ["```", ""]
    return "\n".join(out) + "\n"


def _procs_order(prog):
    """The tick first, then what it calls, then init and the rest; forwarders elided."""
    tick, init = prog.meta.get("tick_proc"), prog.meta.get("init_proc")
    hot = [n for n in reversed(call_order(prog)) if n != init]
    order = [n for n in (tick,) if n in hot] + [n for n in hot if n != tick]
    order += [n for n in prog.procs if n not in order]
    return [n for n in order if forwarder(prog.procs[n]) is None]
