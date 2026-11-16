"""What each trap of a translation stands for (``info["trap_info"]``): the guards and
untranslated paths of the lift's deopts and switch defaults reaching it, the values each lets
pass and the analysis finds, the traps implying it, and the stores writing code it checks."""

from ..wholeprog import REGS
from .emit import ENTRY_REGS

__all__ = ["stores", "trap_info"]


def stores(prog):
    """The stores of lift ``prog``: ``[(pc, lo, hi, addresses or None)]``, an address set
    where it has one."""
    sets = dict(prog.addr_sets)
    out = []
    for pc, lo, hi in prog.stores:
        runs = sets.get((pc, True))
        addrs = None if runs is None else {a for r_lo, r_hi in runs for a in range(r_lo, r_hi)}
        out.append((pc, lo, hi, addrs))
    return out


def _writers(lifts, lo, hi):
    """The stores of ``lifts`` (``{entry: stores}``) that may write a byte of ``[lo, hi]``."""
    out = []
    for entry, ss in sorted(lifts.items()):
        for pc, s_lo, s_hi, addrs in ss:
            if s_hi < lo or s_lo > hi:
                continue
            if addrs is not None and not any(lo <= a <= hi for a in addrs):
                continue
            out.append({"pc": pc, "entry": entry, "exact": s_lo == s_hi or addrs is not None})
    return out


def _values(runs):
    """The values of inclusive ``runs``."""
    return {v for lo, hi in runs for v in range(lo, hi + 1)}


def _passes(ok, vals):
    """Whether every one of ``vals`` lies in a run of ``ok``."""
    return all(any(lo <= v <= hi for lo, hi in ok) for v in vals)


class _Census:
    """The trap metadata of one render (:func:`trap_info`)."""

    def __init__(self, funcs, traps, whys, ctx):
        self.funcs, self.whys, self.ctx = funcs, whys, ctx
        self.of = {w: traps[key] for key, ws in whys.items() for w in ws}

    def copies(self, fi, pc):
        """Each copy's instruction start of ``pc`` in instance function ``fi``."""
        for row in self.funcs[fi].get("copy_pcs", ()):
            if pc in row:
                return [q for q in row if q]
        return []

    def implied(self, fi, w):
        """The traps implying register guard ``w`` of function ``fi`` (``[]``: constants
        alone), or None."""
        held = w["held"]
        if held is None or not w["ok"]:
            return None
        by = {self.of[(fi, j)] for j in held["by"] if (fi, j) in self.of}
        vals = _values(held["vals"])
        if _passes(w["ok"], vals):
            return sorted(by)
        song = self.ctx["song"]
        if song is not None and held["src"] == "a" and self.funcs[fi]["pc"] == song["fn"]:
            vals &= set(range(song["lo"], song["hi"] + 1))
            if _passes(w["ok"], vals):
                return sorted(by | {song["k"]})
        return None

    def writers(self, fi, w):
        """The stores writing what guard ``w`` of function ``fi`` checks, if it checks code."""
        origin = self.ctx["origin"]
        entry = "init" if origin is not None and origin[fi] else "play"
        if w["kind"] == "opcode":
            return _writers(self.ctx["stores"], w["at"], w["at"])
        if w["kind"] == "codechk":
            return [{"pc": w["at"], "entry": entry, "exact": False}]
        pcs = sorted({t["store"] for t in w["tables"] if t["of"] == "cell"})
        return [{"pc": pc, "entry": entry, "exact": True} for pc in pcs] if pcs else None

    def passed(self, fi, j):
        """Whether guard ``j`` of function ``fi`` is an entry's on a register the C entry
        passes as a constant (all but ``A``) that it lets pass."""
        fn, w = self.funcs[fi], self.funcs[fi]["whys"][j]
        if w["kind"] not in ("entry", "entry_sp") or not fn["entry"] or w["at"] != fn["pc"]:
            return False
        r = REGS.get(w["reg"], 0)
        return r != 0 and _passes(w["ok"], [ENTRY_REGS.get(r, 0)])

    def guard(self, fi, j, ind):
        """Guard ``j`` of function ``fi`` as reported (``ind``: a switch's default)."""
        fn = self.funcs[fi]
        w = dict(fn["whys"][j], fn=fn["pc"])
        w["proven"] = w["seen"] is not None and _passes(w["ok"], _values(w["seen"]))
        w["copies"] = self.copies(fi, w["at"]) if fn.get("copies") else []
        w["implied_by"] = self.implied(fi, w)
        w["writers"] = self.writers(fi, w)
        if ind:
            w["kind"] = "ind"
            tables = [
                fn["tables"][op[3]]
                for op in (op for b in fn["blocks"] for op in b["ops"])
                if op[0] == "BRANCHIND" and len(op) > 5 and j in fn["exits"][op[5]]
            ]
            w["targets"] = sorted({pc for t in tables for pc, _ in t})
            w["resolved"] = w["at"] in self.ctx["jumps"]
        return w

    def trap(self, key):
        """The metadata of the trap of ``key``."""
        whys = [(fi, j) for fi, j in self.whys.get(key, ()) if not self.passed(fi, j)]
        guards = [self.guard(fi, j, key[0] == "ind") for fi, j in whys]
        if key in self.ctx["extra"]:
            guards.insert(0, self.ctx["extra"][key])
        kinds = sorted({g["kind"] for g in guards})
        fis = {fi for fi, _ in whys if self.funcs[fi].get("copies")}
        implied = [g["implied_by"] for g in guards]
        return {
            "kind": kinds[0] if len(kinds) == 1 else "mixed",
            "kinds": kinds,
            "pc": key[1],
            "fn": key[2] if len(key) > 2 else None,
            "copies": sorted({q for fi in fis for q in self.copies(fi, key[1])}),
            "guards": guards,
            "implied_by": (
                sorted({k for i in implied for k in i}) if implied and None not in implied else None
            ),
            "proven": bool(guards) and all(g["proven"] for g in guards),
        }


def trap_info(funcs, traps, whys, used, ctx):
    """``{k: metadata}`` of the traps ``used`` of ``traps`` (``{key: k}``) of the lifted
    ``funcs``, from ``whys`` (:func:`deity_informant.cgen.emit.emit`) and ``ctx``: ``origin``,
    ``stores`` (``{entry: stores(...)}``), ``jumps``, ``song`` (init's ``{"fn", "lo", "hi",
    "k"}`` or None) and ``extra`` (``{key: guard}`` the render makes)."""
    c = _Census(funcs, traps, whys, ctx)
    return {k: c.trap(key) for key, k in traps.items() if k in used}
