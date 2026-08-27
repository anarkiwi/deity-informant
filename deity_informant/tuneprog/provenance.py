"""S6 -- T0: per-register provenance, one record per SID write site.

Every write the program makes to the SID register file, as data: the register and
the voices it reached, the expression over *named* cells that produced the value,
the cells that expression reads, the site, and the line
:class:`~.printer.Body` renders for it. The register comes from the site's own
base and its **observed envelope** -- the addresses the access stayed inside --
which is what says which voices an indexed write covered, where the printed index
alone need not (:meth:`~.cellref.Cells.voiced` fails on an opaque one).

Roots are the ``io`` stores whose envelope lies in ``$D400..$D418`` and the stores
into a SID image region (:func:`~.facts.image_copy`), rekeyed by the flush delta:
a player that assembles its registers in RAM and flushes them writes its
provenance there, so the two sets are one plane. What the envelope cannot name is
a stated :data:`REFUSALS` entry, never a guess.
"""

from __future__ import annotations

from collections import namedtuple

from .cellref import _bare
from .facts import Facts, SID_VOICE, SID_VOICES, sid_name
from .halves import register
from .ir import Bin, Const, Load, R16, SID_REG_HI, SID_REG_LO, Store, Var, W16, enc
from .irwalk import addr_split, single_defs, walk
from .printer import Body, procs_order

DEPTH = 8
REFUSALS = ("index not a voice", "smc target", "unresolved base")
Hit = namedtuple("Hit", "stmt proc block lines idx state")
STATE = ("proc", "alias", "fvars", "tmp", "mem", "defs")  # what a printed cell reads


# ---- the write sites, in the state the printer saw them -----------------------
class _Sites(Body):
    """The rendered document, plus every SID write site and the line it printed."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.hits = []
        self.here = []
        self.n = 0

    def stmt(self, s):
        """Render one statement, keeping the state a write site printed in.

        Before the statement, not after: the value a preceding store left in a cell
        is how the print spells it (:meth:`~.pseudocode.Printer.held`), and this
        statement's own temporaries are named while it renders.
        """
        if target(s, self.names) is not None:
            self.here.append((s, self.n, {k: dict(getattr(self, k)) for k in STATE[1:]}))
        self.n += 1
        return super().stmt(s)

    def blk(self, n, proc, pad):
        self.here, self.n = [], 0
        lines = super().blk(n, proc, pad)
        off = len(lines) - self.n  # the pc comment, when the block carries one
        for s, k, st in self.here:
            self.hits.append(Hit(s, proc, n.label, lines, off + k, dict(st, proc=proc)))
        return lines

    def restore(self, hit):
        """Re-enter the state one site printed in, so its cells spell as they did."""
        for k in STATE:
            setattr(self, k, hit.state[k] if k == "proc" else dict(hit.state[k]))
        return self


def target(s, names):
    """``(direct, base register, envelope)`` of a SID write site, or ``None``.

    ``direct`` is False for a store into a SID image, whose base and envelope are
    the register file's after the flush delta is added.
    """
    t, img = type(s), names.image
    if t is Store and s.cls == "io":
        return _band(True, addr_split(s.a)[0], (s.lo, s.hi))
    if t is Store and s.r in img:
        d, base = img[s.r], addr_split(s.a)[0]
        return _band(False, None if base is None else base + d, (s.lo + d, s.hi + d))
    if t is W16 and s.lo[0] in img and s.hi[0] == s.lo[0]:
        d = img[s.lo[0]]
        return _band(False, s.lo[1] + d, _env(s, d))
    if t is W16 and register((s.lo, s.hi)) is not None:
        return _band(True, s.lo[1], _env(s, 0))
    return None


def _env(s, d):
    """A 16-bit assignment's envelope: the low half's own, rekeyed by ``d``."""
    lo, hi = s.env or (s.lo[1], s.lo[1])
    return lo + d, hi + d


def _band(direct, base, env):
    """The site, once its envelope is known to start inside the register file."""
    lo, hi = env
    if not SID_REG_LO <= lo <= SID_REG_HI or hi < lo:
        return None
    return direct, base, (lo, min(hi, SID_REG_HI))


def regvoices(base, lo, hi):
    """``(register, voices)`` from a site's base register and its observed envelope.

    An indexed write to a voice register reaches whole voice blocks from its base,
    so the register is the base's and the voices are the blocks the envelope spans
    -- the same fact whether the index is a loop variable, a voice-map entry or a
    cell no name reaches. A global register takes no index. Anything else reached
    more than one register: not a voice, and not this rule's to name.
    """
    if base is None or not SID_REG_LO <= base <= SID_REG_HI or lo < base:
        return None
    field, voice = sid_name(base)
    if voice is None:
        return (field, []) if lo == hi == base else None
    a, b = lo - base, hi - base
    if a % SID_VOICE or b % SID_VOICE or voice + b // SID_VOICE >= SID_VOICES:
        return None
    return field, list(range(voice + a // SID_VOICE, voice + b // SID_VOICE + 1))


def _voices(lo, hi):
    """Every voice a multi-register write touched, in order."""
    return sorted({v for a in range(lo, hi + 1) for v in (sid_name(a)[1],) if v is not None})


# ---- the value's slice --------------------------------------------------------
def stops(names):
    """The regions a slice stops at: a role, a struct view, a record split, a slot."""
    out = {r for r, v in names.role.items() if v}
    return frozenset(out | set(names.view) | set(names.split) | {r for r, _a in names.slots})


def leaf(e, keep):
    """True for a value the slice keeps whole: a named cell, or a 16-bit view."""
    return type(e) is R16 or (type(e) is Load and e.r in keep)


def expand(e, defs, keep, depth=DEPTH):
    """``e`` with its names substituted, stopping at every cell ``keep`` names."""
    t = type(e)
    if t is Var and depth and e.n in defs:
        d = defs[e.n]
        return d if leaf(d, keep) else expand(d, defs, keep, depth - 1)
    if t is Bin:
        return Bin(e.op, expand(e.a, defs, keep, depth), expand(e.b, defs, keep, depth), e.w)
    if t is Load and not leaf(e, keep):
        return Load(e.cls, expand(e.a, defs, keep, depth), e.w, e.lo, e.hi, e.r)
    return e


def leaves(e):
    """The reads of ``e`` that are values, not parts of another read's address.

    One entry per distinct cell: an expression reading the same cell twice reads
    one cell, and the expression itself keeps both occurrences.
    """
    xs = [x for x in walk(e) if type(x) in (Load, R16)]
    inner = {id(y) for x in xs for y in walk(x.a)}
    out = {}
    for x in (x for x in xs if id(x) not in inner):
        out.setdefault(repr(x), x)
    return list(out.values())


def _cell(body, names, x):
    """One leaf of a slice, named the way the print names it."""
    rid = x.lo[0] if type(x) is R16 else x.r
    r = body.rgn.get(rid)
    text = _bare(body.expr(x, False))
    idx = body.addr_of(x.a, r)[1]
    return {
        "region": rid,
        "base": None if r is None else "$%04X" % r.base,
        "size": None if r is None else r.size,
        "name": text,
        "field": text.rsplit(".", 1)[1] if "." in text else "",
        "role": names.role.get(rid, ""),
        "voice_indexed": idx is not None and body.voiced(idx) is not None,
    }


# ---- the records --------------------------------------------------------------
def _copy(s, v, names):
    """The image region a store copies into the register file at its own delta."""
    if type(v) is not Load or v.r not in names.image:
        return None
    base, src = addr_split(s.a)[0], addr_split(v.a)[0]
    ok = base is not None and src is not None and base - src == names.image[v.r]
    return v.r if ok else None


def _refusal(base, env, addr):
    """Why a site's register cannot be named, or ``None``.

    An address with no constant base at all is one the program read: a patched
    operand, which :mod:`.lift` residualises into a load of the operand cell.
    Anything else is arithmetic no name reaches.
    """
    if base is not None:
        return None if regvoices(base, *env) else "index not a voice"
    return "smc target" if any(type(x) is Load for x in walk(addr)) else "unresolved base"


def _site(prog, hit):
    """Where the write is: the procedure, the block, the pc, and their coverage."""
    s, b = hit.stmt, prog.procs[hit.proc].blocks.get(hit.block)
    return {
        "proc": hit.proc,
        "block": hit.block,
        "pc": "$%04X" % s.src,
        "width": 2 if type(s) is W16 else s.w,
        "hifirst": s.hifirst if type(s) is W16 else None,
        "count": 0 if b is None else b.count,
        "cover": [] if b is None else list(b.cover),
    }


def _updates(s, facts):
    """True when the site writes its own cell back: a recurrence, which is T1's."""
    if type(s) is W16:
        return any(type(x) is R16 and (x.lo, x.hi) == (s.lo, s.hi) for x in walk(s.e))
    return (s.r, addr_split(s.a)[0]) in facts.cellupd


def record(body, hit, prog, facts, keep):
    """One write site as data: what it wrote, where the value came from, its line."""
    s, names = hit.stmt, body.names
    direct, base, env = target(s, names)
    body.restore(hit)
    defs = single_defs(prog.procs[hit.proc])
    e = expand(s.e if type(s) is W16 else s.v, defs, keep)
    copied = _copy(s, e, names) if direct else None
    why = None if copied is not None else _refusal(base, env, expand(s.a, defs, keep))
    # one value for every register the envelope covers is provenance for each of
    # them: the flush copy the image naming already proved, and a file-wide constant
    whole = copied is not None or (why == "index not a voice" and type(e) is Const)
    got = None if why or whole else regvoices(base, *env)
    if type(s) is W16 and got is not None:
        got = (register(((0, base), (0, base + 1))), got[1])
    rec = {
        "direct": direct,
        "kind": "file" if whole else "register",
        "register": got and got[0],
        "voices": got[1] if got else _voices(*env),
        "envelope": ["$%04X" % a for a in env],
        "expr": enc(e),
        "cells": [_cell(body, names, x) for x in leaves(e)],
        "site": _site(prog, hit),
        "self_update": _updates(s, facts),
        "print": hit.lines[hit.idx].strip(),
        "refusal": None,
    }
    if whole:
        rec["copies"] = copied
    elif why:
        rec["refusal"] = {"why": why, "cell": body.expr(s.a, False), "site": rec["site"]["pc"]}
    if not direct:
        rec["image"] = _image(s, names, body)
    return rec


def _image(s, names, body):
    """The image region a rekeyed record was written into."""
    rid = s.lo[0] if type(s) is W16 else s.r
    return {"region": rid, "name": names.of(rid), "delta": names.image[rid]}


def provenance(prog, structured, names, facts=None):
    """One record per SID write site of the presentation view, in print order."""
    facts, keep = facts or Facts(prog), stops(names)
    body = _Sites(prog, names)
    for name in procs_order(prog):
        body.render(name, structured[name])
    out = [record(body, h, prog, facts, keep) for h in body.hits]
    flush = {r["copies"]: r["site"] for r in out if r.get("copies") is not None}
    for r in (x for x in out if not x["direct"]):
        hit = flush.get(r["image"]["region"])
        r["image"]["flush_pc"] = hit and hit["pc"]
        r["image"]["flush_proc"] = hit and hit["proc"]
    return out


def document(prog, structured, names, facts=None):
    """``tuneprog.T0.json``: the naming plane's voice maps, images and write sites."""
    rgn = prog.by_id()
    return {
        "plane": "S6-view",
        "voice_map": [
            {
                "region": rid,
                "name": names.of(rid),
                "base": "$%04X" % rgn[rid].base,
                "entries": list(rgn[rid].init[:: max(rgn[rid].stride, 1)]),
            }
            for rid in sorted(names.voicemap)
            if rid in rgn
        ],
        "image": [
            {
                "region": rid,
                "name": names.of(rid),
                "base": "$%04X" % rgn[rid].base,
                "size": rgn[rid].size,
                "delta": delta,
            }
            for rid, delta in sorted(names.image.items())
            if rid in rgn
        ],
        "writes": provenance(prog, structured, names, facts),
    }
