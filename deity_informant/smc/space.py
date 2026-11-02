"""The SMC feature space, derived mechanically from the lifter templates and the machine.

The writer x channel and target-role axes are read off the P-Code templates of all 256
opcodes (each STORE/LOAD address dataflow), the machine's device write/fetch ports and the
REU command-register axes; timing and mapping are the lifter's own assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import product

from vagrant_moses.machine import SOURCES, opcode_layout

from ..lifter import OPS, lift

__all__ = [
    "CHANNELS",
    "ROLES",
    "TIMINGS",
    "MAPPINGS",
    "MACHINE_WRITERS",
    "REU_AXES",
    "Cell",
    "store_channel",
    "load_class",
    "writer_channels",
    "cells",
]

_PC = 0x1234
_B1, _B2, _P1, _P2 = 0x5A, 0xC3, 0x71, 0x9E

#: Address-expression forms a store's effective address can take, by template dataflow.
CHANNELS = (
    "zp",
    "zp_indexed_wrap",
    "abs",
    "abs_indexed",
    "indirect",
    "sp_rel",
    "unstable_high",
)
READ_CLASSES = ("data", "ptr_indirect", "stack_pull")


def _image(op):
    mem = bytearray(0x10000)
    mem[_PC], mem[_PC + 1], mem[_PC + 2] = op, _B1, _B2
    mem[_B1], mem[(_B1 + 1) & 0xFF] = _P1, _P2
    return mem


def _defs(ops):
    """Map each written varnode ``(space, offset)`` to the index of the op that wrote it."""
    out = {}
    for i, (_mn, outv, _ins) in enumerate(ops):
        if outv is not None:
            out[(outv[0], outv[1])] = i
    return out


def _prov_by_key(rec):
    """``rec`` constant provenance re-keyed by varnode ``(space, offset, size)``."""
    out = {}
    for (i, j), p in rec["prov"]["ops"].items():
        vn = rec["ops"][i][2][j]
        if vn[0] == "c":
            out[(vn[0], vn[1], vn[2])] = (list(p[0]), p[1])
    return out


def _features(ops, defs, prov, vn, acc):
    """Accumulate address-expression features of ``vn`` into ``acc`` by a dataflow walk."""
    sp, off, size = vn
    if sp == "c":
        src = prov.get((sp, off, size))
        acc.add("word" if src == ([1, 2], "word") else "byte" if src else "const")
        return
    if sp == "r":
        acc.add({0: "a", 1: "x", 2: "y", 3: "sp"}.get(off, "flag"))
        return
    d = defs.get((sp, off))
    if d is None:
        return
    mn, _outv, ins = ops[d]
    if mn == "LOAD":
        acc.add("load")
        return
    if mn == "INT_OR" and ins[1][0] == "c" and ins[1][1] == 0x100:
        acc.add("or100")
    if mn == "INT_ZEXT" and _adds_bytes(ops, defs, ins[0]):
        acc.add("zext_add")
    for src in ins:
        _features(ops, defs, prov, src, acc)


def _adds_bytes(ops, defs, vn):
    """``vn`` is an 8-bit INT_ADD: the zero-page index add that wraps in page 0."""
    if vn[0] != "u":
        return False
    d = defs.get((vn[0], vn[1]))
    return d is not None and ops[d][0] == "INT_ADD" and ops[d][1][2] == 1


def _addr_features(rec, idx):
    ops = rec["ops"]
    acc = set()
    _features(ops, _defs(ops), _prov_by_key(rec), ops[idx][2][0], acc)
    return acc


def _addr_loads(ops, defs, addr_vn):
    """The varnode keys of pointer bytes loaded while computing ``addr_vn``."""
    out = set()

    def walk(vn, seen):
        d = defs.get((vn[0], vn[1])) if vn[0] == "u" else None
        if d is None or d in seen:
            return
        seen.add(d)
        if ops[d][0] == "LOAD":
            out.add((ops[d][1][0], ops[d][1][1]))
        for src in ops[d][2]:
            walk(src, seen)

    walk(addr_vn, set())
    return out


def _value_uses_hi1(rec, idx):
    """The stored value ANDs a register with its effective address high byte + 1 (SH*)."""
    ops, defs, prov = rec["ops"], _defs(rec["ops"]), _prov_by_key(rec)
    ptr = _addr_loads(ops, defs, ops[idx][2][0])
    found = set()

    def walk(vn, seen):
        if vn[0] == "c":
            p = prov.get((vn[0], vn[1], vn[2]))
            if p is not None and p[1] == "hi1" and p[0] == [2]:
                found.add(1)
            return
        d = defs.get((vn[0], vn[1])) if vn[0] == "u" else None
        if d is None or d in seen:
            return
        seen.add(d)
        mn, _o, ins = ops[d]
        if (
            mn == "INT_ADD"
            and ins[1][0] == "c"
            and ins[1][1] == 1
            and (ins[0][0], ins[0][1]) in ptr
        ):
            found.add(1)
        for src in ins:
            walk(src, seen)

    walk(ops[idx][2][1], set())
    return bool(found)


def _classify_addr(feats):
    if "load" in feats:
        return "indirect"
    if "sp" in feats and "or100" in feats:
        return "sp_rel"
    indexed = "x" in feats or "y" in feats
    if "word" in feats:
        return "abs_indexed" if indexed else "abs"
    if "byte" in feats or "const" in feats:
        return "zp_indexed_wrap" if (indexed and "zext_add" in feats) else "zp"
    return "sp_rel" if "or100" in feats else "unknown"


@lru_cache(maxsize=None)
def store_channel(op):
    """``(channel, rmw)`` of ``op``'s store, or ``None`` when ``op`` does not store."""
    rec = lift(_image(op), _PC)
    idx = [i for i, o in enumerate(rec["ops"]) if o[0] == "STORE"]
    if not idx:
        return None
    si = idx[-1]
    channel = (
        "unstable_high" if _value_uses_hi1(rec, si) else _classify_addr(_addr_features(rec, si))
    )
    return (channel, opcode_layout(op)[2] == "rmw")


def load_class(op):
    """Read-classes of ``op``'s loads (throw-away reads aside): ``data``, ``ptr_indirect``
    or ``stack_pull``."""
    rec = lift(_image(op), _PC)
    ops, defs, prov = rec["ops"], _defs(rec["ops"]), _prov_by_key(rec)
    out = []
    for o in ops:
        if o[0] != "LOAD" or o[1] is None:
            continue
        feats = set()
        _features(ops, defs, prov, o[2][0], feats)
        if "or100" in feats and "sp" in feats:
            out.append("stack_pull")
        elif _feeds_addr(ops, o[1]):
            out.append("ptr_indirect")
        else:
            out.append("data")
    return out


def _feeds_addr(ops, loaded):
    """The loaded byte is shifted or zero-extended into an address (an indirect pointer)."""
    key = (loaded[0], loaded[1])
    return any(
        mn in ("INT_LEFT", "INT_ZEXT") and (ins[0][0], ins[0][1]) == key for mn, _o, ins in ops
    )


#: The machine's interrupt entry pushes per source (vagrant-moses ``SOURCES``) and BRK's.
MACHINE_WRITERS = {
    **{("nmi_push" if s == "nmi" else "irq_push"): "sp_rel" for s in SOURCES},
    "brk_push": "sp_rel",
    "reu_dma": "dma",
}
#: Channels that cannot reach the CPU port at $00/$01.
_NO_PORT = ("sp_rel",)

#: Target roles a fetch or control read can play.
ROLES = (
    "opcode",
    "operand",
    "ptr_indirect",
    "ret_addr",
    "hw_vector",
    "kernal_vector",
    "bank_reg",
    "reu_reg",
)

#: The lifter's timing assumptions: when the violating write happened.
TIMINGS = (
    "pre_lift",
    "earlier_call",
    "same_call_earlier",
    "same_insn",
    "async",
)

#: The lifter's flat-memory assumption: the bank config a byte is written vs fetched through.
MAPPINGS = ("flat", "ram_under_rom", "ram_under_io", "rom_vs_ram")

#: The REU command-register axes (VICE reu.h): transfer type x address controls x options.
REU_AXES = {
    "transfer": ("to_reu", "from_reu", "swap", "verify"),
    "c64_addr": ("increment", "fixed"),
    "reu_addr": ("increment", "fixed"),
    "autoload": (False, True),
    "ff00_trigger": (False, True),
}


def writer_channels():
    """Every ``(writer, channel, rmw)`` the templates and the machine produce.

    ``cpu_port`` takes each CPU channel that reaches $00/$01 and the machine reset's port write.
    """
    out = set()
    for op in OPS:
        sc = store_channel(op)
        if sc is not None:
            out.add(("cpu", sc[0], sc[1]))
            if sc[0] not in _NO_PORT:
                out.add(("cpu_port", sc[0], sc[1]))
    out.add(("cpu_port", "reset", False))
    for writer, channel in MACHINE_WRITERS.items():
        out.add((writer, channel, False))
    return sorted(out)


@dataclass(frozen=True)
class Cell:
    """One scenario cell in the (writer x channel, role, timing, mapping) product."""

    writer: str
    channel: str
    rmw: bool
    role: str
    timing: str
    mapping: str

    @property
    def sid(self):
        return "%s.%s%s.%s.%s.%s" % (
            self.writer,
            self.channel,
            "_rmw" if self.rmw else "",
            self.role,
            self.timing,
            self.mapping,
        )


def cells():
    """The full scenario product as :class:`Cell` tuples."""
    return [
        Cell(w, ch, rmw, role, timing, mapping)
        for (w, ch, rmw), role, timing, mapping in product(
            writer_channels(), ROLES, TIMINGS, MAPPINGS
        )
    ]
