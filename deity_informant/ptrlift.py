"""ptrlift: rung (g)'s pointer lift under b0's observed extents (register-model-lift 2b).

A web lifts iff ``ptrcert`` calls it eligible (b1's four premises) and b0 observed its
whole extent inside the declared registry: its deref sites then spell as rung (f)'s
``*ptr[i]`` and its ``u16`` state field carries the extent as an ``in`` clause.
"""

from __future__ import annotations

from types import SimpleNamespace

from . import frameptr
from . import grammar as G
from . import ptrcert
from . import ptrextent
from .render import name_addr
from .structured import Proof

REFUSALS = ("web_unnamed",)  # the spelling's own; b1's and b0's classes travel as they are


def _sites(procs):
    """``{pointer cell: {deref address: index or None}}`` over rung (f)'s own shape."""
    out = {}
    for _e, _p, _r, stmts in procs:
        for addr in frameptr._addrs(stmts):
            got = frameptr.deref(addr)
            if got is not None:
                out.setdefault(got[0], {})[addr] = got[1]
    return out


def _u16_fields(state):
    """The scalar ``u16`` state field names: what an ``in`` clause may ride."""
    return {name for name, width, array, _obs in state if width == 2 and not array}


def _blocks(rec):
    """One b0 record's observed blocks, as declaration bases."""
    return tuple(sorted(int(b[1:], 16) for b in rec["blocks"]))


class _Web:
    """One pointer web: b1's verdict, b0's extent, and the field that would name it."""

    __slots__ = ("cell", "record", "blocks", "sites", "why")

    def __init__(self, cell, record, blocks, sites, named):
        self.cell = cell
        self.record = record
        self.blocks = blocks
        self.sites = sites
        self.why = self._refuse(named)

    def _refuse(self, named):
        """The class that stops the lift, else None: b1 first, then b0, then the spelling."""
        if not self.record["eligible"]:
            return ", ".join(self.record["lift_refusals"])
        if not self.blocks:
            return "extent_unmappable"
        return None if named else "web_unnamed"

    def proof(self):
        """The rung-(g) record: the extent the declaration names, or what refused it."""
        body = "pointer lift %s: %d deref site(s), %d top load, %d top write" % (
            name_addr(self.cell),
            len(self.sites),
            self.record["top_loads"],
            self.record["top_stores"],
        )
        if self.why is not None:
            return Proof(self.cell, "ptr-lift", "refused", (), "%s; %s" % (body, self.why))
        lemma = "%s; extent %d block(s) $%04X..$%04X" % (
            body,
            len(self.blocks),
            self.blocks[0],
            self.blocks[-1],
        )
        return Proof(self.cell, "ptr-lift", "resolved", self.blocks, lemma)


def analyse(mem0, decls, procs, state, symbols, resolved, records):
    """Every web ``ptrcert`` names, b0's extent beside it and its premises discharged.

    The certification reads the program rung (f) left, so a site rung (f) already
    resolved is no ⊤ access and only a web with residue is 2b's to spell."""
    prog = SimpleNamespace(mem0=mem0, data_decls=decls, procs=procs, resolved=resolved)
    recs, _loose = ptrcert.certify(prog)
    mapped = ptrextent.mapped_cells(records)
    blocks = {int(r["root"][1:], 16): _blocks(r) for r in records}
    sites, fields = _sites(procs), _u16_fields(state)
    out = []
    for r in recs:
        cell = int(r["root"][1:], 16)
        named = (symbols.get(cell) or G.addr_name(cell)) in fields
        ext = blocks.get(cell) if cell in mapped else None
        out.append(_Web(cell, r, ext, sites.get(cell, {}), named))
    return out


def apply_rung(mem0, decls, procs, state, symbols, resolved, records=None):
    """Rung (g) over ``procs``: ``(resolved addresses, extents, proofs)``.

    ``records`` is this program's b0 artifact row -- extents are an evaluated
    observation, so they are read, never derived. Without one no web lifts."""
    if records is None:
        return {}, {}, []
    webs = analyse(mem0, decls, procs, state, symbols, resolved, records)
    lifted = [w for w in webs if w.why is None]
    named = {a: (w.cell, i) for w in lifted for a, i in w.sites.items()}
    return named, {w.cell: w.blocks for w in lifted}, [w.proof() for w in webs]
