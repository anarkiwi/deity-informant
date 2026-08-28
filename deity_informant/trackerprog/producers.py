"""T3 -- the producers: every T0 write site outside the fetch regions as data.

A producer is a target register (T0's envelope, the voice its copy index) with a
value and guards over named cells: the site's store and guard path opened through
the tick's reaching definitions, stopped at named cells, parameters bound by callers.
"""

from __future__ import annotations

from ..tuneprog.accshape import Ctx
from ..tuneprog.idioms import fold
from ..tuneprog.ir import Bin, Const, Load, R16, REGVAR, Var, dec
from ..tuneprog.irwalk import addr_split
from ..tuneprog.provenance import stops
from .cursors import strides
from .fetch import Printer
from .namer import Namer
from .refuse import Refusal
from .resolve import Program, Sel, _renamer, _subst, free, walkx

DEPTH = 6
NODES = (Bin, Const, Load, R16, Var)
WHY = "producer not in IR"


class _Program(Program):
    """Reaching definitions that stop at a named cell and leave the copy index free."""

    def __init__(self, ctx, keep, copyvars):
        super().__init__(ctx)
        self.keep, self.copyvars = keep, copyvars

    def of(self, proc):
        new = proc not in self.res
        R = super().of(proc)
        if new:
            R.mem = {k: v for k, v in R.mem.items() if k[0] not in self.keep}
            R.lets = {n: v for n, v in R.lets.items() if n not in self.copyvars}
        return R


def _bare(s):
    return s[1:-1] if s.startswith("(") and s.endswith(")") and s.count("(") == 1 else s


def _under(x, known):
    """``x`` with the guards ``known`` holds dropped from its selections.

    A later alternative left unguarded supersedes the ones before it; one
    alternative, or all alike, is the value itself.
    """
    t = type(x)
    if t is Sel:
        alts = []
        for gs, y in x.alts:
            gs = tuple(g for g in gs if (repr(g[0]), bool(g[1])) not in known)
            if not gs:
                alts = []
            alts.append((gs, _under(y, known)))
        if len({repr(y) for _gs, y in alts}) == 1:
            return alts[0][1]
        return Sel(tuple(alts))
    if t is Bin:
        return Bin(x.op, _under(x.a, known), _under(x.b, known), x.w)
    if t is Load:
        return Load(x.cls, _under(x.a, known), x.w, x.lo, x.hi, x.r)
    if t is R16:
        return R16(x.lo, x.hi, _under(x.a, known))
    return x


class Producers:
    """T0's write sites outside the fetch regions, opened and named."""

    def __init__(self, view, names, fetch, chans=None):
        self.view, self.names = view, names
        self.copyvars = strides(view, names)
        self.P = _Program(Ctx(view, names), stops(names), set(self.copyvars))
        self.pr = Printer(Namer(view, names), chans or {}, self.copyvars)
        self.pcs = fetch.pcs

    def site(self, w):
        """``(proc, label, index, statement)`` of a T0 record's write."""
        s = w["site"]
        pc = int(s["pc"][1:], 16)
        b = self.view.procs[s["proc"]].blocks[s["block"]]
        i = next(i for i, st in enumerate(b.stmts) if getattr(st, "src", None) == pc)
        return s["proc"], s["block"], i, b.stmts[i]

    def paths(self, proc, v, gs, depth=DEPTH):
        """``[(value, guards)]``: one per caller path that binds the proc's parameters.

        A parameter is the argument at the call, opened there; the call's own guard
        path joins the site's.
        """
        names = [REGVAR[k] for k in self.view.procs[proc].params]
        want = set(names) - set(self.copyvars)
        if not depth or all(free(x).isdisjoint(want) for x in [v] + [c for c, _t in gs]):
            return [(v, gs)]
        out = {}
        for cproc, clbl, cidx, call in self.P.callers.get(proc, ()):
            fn, R = _renamer(dict(zip(names, call.args))), self.P.of(cproc)
            v2 = R.open(_subst(v, fn), clbl, cidx)
            gs2 = [(c, t) for c, t, _w in R.guards(clbl)]
            gs2 += [(R.open(_subst(c, fn), clbl, cidx), t) for c, t in gs]
            for got in self.paths(cproc, v2, gs2, depth - 1):
                out.setdefault(repr(got), got)
        if not out:
            raise Refusal(WHY, "", "", "parameter %s bound by no caller" % sorted(want))
        return list(out.values())

    def check(self, x):
        for y in walkx(x):
            if type(y) is Var and y.n not in self.copyvars:
                raise Refusal(WHY, "", "", "temp %s does not open" % y.n)
            if type(y) is Load and y.cls == "io":
                raise Refusal(WHY, "", "", "reads input $%04X" % y.lo)
            if type(y) is Load and self.unnamed(y):
                raise Refusal(WHY, "", "", "reads $%04X..$%04X: no cell" % (y.lo, y.hi))
        return x

    def unnamed(self, y):
        """True for a read no region of the naming plane covers."""
        namer, base = self.pr.namer, addr_split(y.a)[0]
        return (base is None or namer.region(base) is None) and namer.region(y.lo) is None

    def opened(self, w):
        """``[(value, [(guard, truth)])]`` of a site over named cells and the copy index."""
        proc, lbl, i, _s = self.site(w)
        R = self.P.of(proc)
        v = R.open(dec(w["expr"]), lbl, i)
        gs = [(c, t) for c, t, _w in R.guards(lbl)]
        out = []
        for v, gs in self.paths(proc, v, gs):
            gs = {(repr(c), bool(t)): (c, t) for c, t in ((fold(c), t) for c, t in gs)}
            gs = [(c, t) for c, t in gs.values() if type(c) is not Const or bool(c.v) != bool(t)]
            v = _under(v, {(repr(c), bool(t)) for c, t in gs})
            for x in [v] + [c for c, _t in gs]:
                self.check(x)
            out.append((v, gs))
        return out

    def target(self, w, s):
        """``sid[v].reg`` by the envelope; the voice number where the site's address is one."""
        reg, voices = w["register"], w["voices"]
        if w["kind"] == "file":
            return "sid"
        if not voices:
            return "sid.%s" % reg
        idx = addr_split(s.a)[1]
        return "sid[%s].%s" % ("v" if idx is not None or len(voices) > 1 else voices[0], reg)

    def producer(self, w, accs):
        """One T0 record as producers, one per caller path, or the refusal standing for it."""
        pc = w["site"]["pc"]
        if w.get("refusal"):
            raise Refusal(WHY, w["refusal"]["cell"], pc, w["refusal"]["why"])
        s = self.site(w)[3]
        target = self.target(w, s)
        try:
            if w["kind"] == "file" and w.get("copies") is not None:
                got = [(self.names.of(w["copies"]), [])]
            else:
                got = [(self.pr.expr(v), gs) for v, gs in self.opened(w)]
        except Refusal as r:
            raise Refusal(WHY, target, pc, r.detail) from None
        out = []
        for text, gs in got:
            when = [_bare(self.pr.guards([(c, t)])) for c, t in gs]
            out.append(
                {
                    "register": w["register"],
                    "voices": w["voices"],
                    "envelope": w["kind"],
                    "target": target,
                    "value": text,
                    "when": when,
                    "print": "%s = %s%s"
                    % (target, text, (" if " + " and ".join(when)) if when else ""),
                    "cells": [c["name"] for c in w.get("cells") or ()],
                    "accs": sorted(
                        {x for c in w.get("cells") or () for x in accs.get(c["region"], ())}
                    ),
                    "site": {"proc": w["site"]["proc"], "block": w["site"]["block"], "pc": pc},
                }
            )
        return out

    def producers(self, t0, t1):
        """``(producers, refusals)`` over T0's sites outside the fetch regions."""
        by_region = {}
        for a in (t1 or {}).get("accs") or ():
            for rid in a.get("regions") or [a["cell"]["region"]]:
                by_region.setdefault(rid, []).append(a["id"])
        out, bad = [], []
        for w in t0.get("writes") or ():
            if int(w["site"]["pc"][1:], 16) in self.pcs:
                continue
            try:
                out += self.producer(w, by_region)
            except Refusal as r:
                bad.append(r)
        return out, bad


def _named(x, pr):
    """T1's record over names: a cell by its name, an encoded expression printed."""
    if isinstance(x, dict):
        if "addr" in x and "name" in x:
            return x["name"]
        return {k: _named(v, pr) for k, v in x.items()}
    if isinstance(x, list):
        y = dec(x)
        return pr.expr(y) if type(y) in NODES else [_named(v, pr) for v in x]
    return x


def _carry(delta):
    """A live carry's site and flag are where the bit came from: under ``site``."""
    c = delta.get("carry")
    return delta if not isinstance(c, dict) else {**delta, "carry": {"site": c}}


def accs_of(t1, pr):
    """``accs``: the W0 record per T1 accumulator, its addresses under ``site``."""
    out = {}
    for a in (t1 or {}).get("accs") or ():
        out[a["id"]] = {
            "id": a["id"],
            "register": a["target"]["register"],
            "voices": a["target"]["voices"],
            "kind": a["target"]["kind"],
            "split": a["target"]["split"],
            "cell": a["cell"]["name"],
            "width": a["width"],
            "delta": _carry(_named(a["delta"], pr)),
            "bound": _named(a["bound"], pr),
            "policy": a["policy"],
            "policy_value": _named(a["policy_value"], pr),
            "rate": _named(a["rate"], pr),
            "phase": _named(a["phase"], pr),
            "links": a["links"],
            "scope": a["scope"],
            "site": {
                "sites": a["sites"],
                "cell": a["cell"]["addr"],
                "regions": a["regions"],
                "index": a["index"],
            },
        }
    return out
