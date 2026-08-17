"""S2a -- residualised lift: self-modified operand cells become memory loads.

:func:`lift_site` lifts one site's variant bytes with
:func:`deity_informant.lifter.lift` and then rewrites every constant varnode
whose byte provenance (``rec["prov"]``) intersects the play-written cell set into
a ``LOAD`` from the cell address, so the P-Code of a self-modifying instruction
reads its operand as a variable:

* immediates and zero-page operands -> a 1-byte load;
* ``abs``/``abs,X``/``abs,Y`` address bytes -> a 16-bit load folded into the
  address expression;
* ``(zp),Y`` pointer-address bytes -> ``load(cell)`` and ``load(cell) + 1``;
* ``idx_ops`` reports which memory ops are affine in X or Y, so S3 attaches an
  index domain only to the access that really uses it;
* patched ``jmp``/``jsr``/branch operands -> a computed control target
  (``LiftedSite.ctrl_cell``), which :mod:`.cfg` turns into a switch.

Known limitation, inherited from the lifter and the base VM: ``SHA (zp),Y``
($93) folds the *pointer's* high byte into its stored value at lift time, so
neither residualisation nor the VM's ``(pc, bytes)`` record cache reacts to that
pointer changing. No SID player in the survey executes it.

Public API: :func:`lift_site`, :func:`lift_trace`, :class:`LiftedSite`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..lifter import OPS, C, lift
from .tracedata import site_key


@dataclass
class LiftedSite:
    """One site's residualised P-Code."""

    key: tuple
    pc: int
    opcode: int
    length: int
    mnemonic: str
    mode: str
    ops: list
    ctrl: tuple
    src_map: list = field(default_factory=list)  # new op index -> original index (-1 synthetic)
    cell_loads: list = field(default_factory=list)  # (addr, size) reads of residualised cells
    ptr_pairs: list = field(default_factory=list)  # (op_lo, op_hi) original indices of a zp ptr
    idx_ops: dict = field(default_factory=dict)  # original op index -> "X" | "Y"
    ctrl_cell: tuple = None  # (addr, size, form) when the control target is patched
    cyc: int = 0

    @property
    def computed_control(self):
        return self.ctrl_cell is not None


def _max_unique(ops):
    n = 0
    for _mn, out, ins in ops:
        for vn in (out,) + tuple(ins):
            if vn is not None and vn[0] == "u":
                n = max(n, vn[1] + vn[2])
    return n


class _Res:
    """Emits the extra P-Code ops that turn a byte cell into a value."""

    def __init__(self, ops):
        self.u = _max_unique(ops)
        self.loads = []

    def tmp(self, sz=1):
        v = ["u", self.u, sz]
        self.u += sz
        return v

    def load(self, pre, addr, sz=1):
        self.loads.append((addr, sz))
        t = self.tmp(sz)
        pre.append(["LOAD", t, [C(addr, 2)]])
        return t

    def value(self, pre, pc, srcs, fn, size):
        """The residualised replacement for a const varnode of ``size`` bytes."""
        if fn == "word":
            t = self.load(pre, (pc + 1) & 0xFFFF, 2)
        elif fn == "hi1":
            base = self.load(pre, (pc + srcs[0]) & 0xFFFF, 1)
            t = self.tmp()
            pre.append(["INT_ADD", t, [base, C(1)]])
        else:  # "id"
            t = self.load(pre, (pc + srcs[0]) & 0xFFFF, 1)
        if size == 2 and t[2] == 1:
            z = self.tmp(2)
            pre.append(["INT_ZEXT", z, [t]])
            t = z
        return t


_IDX = {1: "X", 2: "Y"}


def _taint(vn, seen):
    if vn[0] == "r":
        r = _IDX.get(vn[1])
        return {r} if r else set()
    return seen.get(vn[1], set()) if vn[0] == "u" else set()


def _indexed_ops(ops):
    """``{op index: 'X'|'Y'}`` for memory ops whose address is affine in an index.

    Taint propagates through arithmetic only: a ``LOAD`` breaks the affine
    relation, so an ``(zp,X)`` pointer fetch is X-indexed while the stream access
    it feeds is not, and a ``(zp),Y`` pointer fetch is not indexed while the
    stream access is.
    """
    seen, out = {}, {}
    for i, (mn, res, ins) in enumerate(ops):
        if mn in ("LOAD", "STORE"):
            marks = _taint(ins[0], seen)
            if marks:
                out[i] = sorted(marks)[0]
            if mn == "LOAD":
                continue
        if res is not None and res[0] == "u":
            marks = set()
            for vn in ins:
                marks |= _taint(vn, seen)
            seen[res[1]] = marks
    return out


def _ptr_pairs(ops, provops):
    """``(lo op, hi op)`` indices of every ``(zp),Y`` pointer fetch in ``ops``."""
    lo = {}
    out = []
    for (i, j), (srcs, fn) in provops.items():
        if j or ops[i][0] != "LOAD":
            continue
        if fn == "id":
            lo[tuple(srcs)] = i
        elif fn == "hi1":
            k = lo.get(tuple(srcs))
            if k is not None:
                out.append((k, i))
    return sorted(out)


def lift_site(image, site, cells, key=None, variant=None):
    """Residualised :class:`LiftedSite` for ``site`` (a ``Trace.sites`` entry).

    ``image`` is the post-init machine image (a ``bytearray`` is patched and
    restored in place); ``cells`` the play-written instruction-byte addresses.
    """
    pc = site["pc"]
    v = variant if variant is not None else site["variants"][0]
    m = image if isinstance(image, bytearray) else bytearray(image)
    at = [(pc + k) & 0xFFFF for k in range(len(v))]
    saved = [m[a] for a in at]
    for a, b in zip(at, v):
        m[a] = b
    try:
        rec = lift(m, pc)
    finally:
        for a, b in zip(at, saved):
            m[a] = b
    prov = rec["prov"]
    res = _Res(rec["ops"])
    ops, src_map = [], []
    for i, (mn, out, ins) in enumerate(rec["ops"]):
        pre = []
        ins = list(ins)
        for j, vn in enumerate(ins):
            p = prov["ops"].get((i, j))
            if p is None or not any(((pc + s) & 0xFFFF) in cells for s in p[0]):
                continue
            ins[j] = res.value(pre, pc, p[0], p[1], vn[2])
        for o in pre:
            ops.append(o)
            src_map.append(-1)
        ops.append([mn, out, ins])
        src_map.append(i)
    ctrl_cell = None
    cp = prov["ctrl"]
    if cp is not None and any(((pc + s) & 0xFFFF) in cells for s in cp[0]):
        ctrl_cell = ((pc + cp[0][0]) & 0xFFFF, len(cp[0]), cp[1])
        res.loads.append((ctrl_cell[0], ctrl_cell[1]))
    mn, mode = OPS[site["opcode"]]
    return LiftedSite(
        key=key if key is not None else site_key(pc, site["opcode"], v, cells),
        pc=pc,
        opcode=site["opcode"],
        length=rec["len"],
        mnemonic=mn,
        mode=mode,
        ops=ops,
        ctrl=rec["ctrl"],
        src_map=src_map,
        cell_loads=res.loads,
        ptr_pairs=_ptr_pairs(rec["ops"], prov["ops"]),
        idx_ops=_indexed_ops(rec["ops"]),
        ctrl_cell=ctrl_cell,
        cyc=rec["cyc"],
    )


def lift_trace(trace):
    """``{site key: LiftedSite}`` for every site of ``trace``."""
    m = bytearray(trace.image_post_init)
    out = {}
    for key, site in trace.sites.items():
        for v in site["variants"]:
            if v[0] == key[1] and _matches(key, v, trace.cells, site["pc"]):
                out[key] = lift_site(m, site, trace.cells, key=key, variant=v)
                break
        else:
            out[key] = lift_site(m, site, trace.cells, key=key)
    return out


def _matches(key, v, cells, pc):
    return all(
        f is None or ((pc + 1 + i) & 0xFFFF) in cells or f == v[i + 1] for i, f in enumerate(key[2])
    )
