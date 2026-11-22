"""Classify oracle-observed writes and fetches into scenario cells.

Both oracles feed one :class:`Classifier`: ``write`` records each byte's last writer,
``fetch`` checks a fetched byte against the lift-time image and its writer, ``port_change``
and ``bank`` track the PLA map. Events the axes cannot express go to ``unclassified``.
"""

from __future__ import annotations

from functools import lru_cache

from jennings.isa import addresses, length, pointer_bytes
from jennings.opcodes import MEM_MODES, OPCODES
from vagrant_moses.bus import banking

from .space import Cell

__all__ = ["Classifier", "view", "insn_reads", "expected_writes", "expected_reads", "IO_WINDOW"]

IO_WINDOW = (0xD000, 0xDFFF)
_KVEC = range(0x0314, 0x031A)
_PUSHERS = ("irq_push", "nmi_push")
_PUSHERS_MN = frozenset(("PHA", "PHP", "JSR", "BRK"))


def view(bits, addr):
    """What the CPU sees at ``addr`` under port bits ``bits``: ``ram``, ``rom`` or ``io``.

    ``$00``/``$01`` are the 6510's port registers, read as a device.
    """
    return "io" if addr < 2 else _page_view(bits, addr >> 12)


@lru_cache(maxsize=None)
def _page_view(bits, page):
    basic, kernal, chargen, io = banking(bits)
    if page in (0xA, 0xB):
        return "rom" if basic else "ram"
    if page == 0xD:
        return "io" if io else "rom" if chargen else "ram"
    if page >= 0xE:
        return "rom" if kernal else "ram"
    return "ram"


def insn_reads(mem, pc, x, sp):
    """``(addr, role)`` of every byte an instruction at ``pc`` fetches or reads as control."""
    op = mem[pc]
    out = [(pc, "opcode")] + [((pc + k) & 0xFFFF, "operand") for k in range(1, length(op))]
    out += [(a, "ptr_indirect") for a in pointer_bytes(mem, pc, x) or ()]
    if OPCODES[op][0] in ("RTS", "RTI"):
        out += [(a, "ret_addr") for a in addresses(mem, pc, x, 0, sp)["stack"][-2:]]
    return out


def expected_writes(mem, pc, x, y, sp, a=None):
    """The addresses an instruction at ``pc`` may store to: its stack pushes, else its data
    address by its addressing form (``a`` needed by a ``SHA``/``TAS`` crossing a page)."""
    mn, mode = OPCODES[mem[pc]]
    if mn in _PUSHERS_MN:
        return set(addresses(mem, pc, x, y, sp)["stack"])
    return set(addresses(mem, pc, x, y, sp, a)["data"]) if mode in MEM_MODES else set()


def expected_reads(mem, pc, x, y, sp, store=False, a=None):
    """The data addresses an instruction at ``pc`` reads (pointer bytes, operand), by its
    addressing form; ``store`` marks a store that does not read its operand. Stack pulls and
    return-address reads are not data reads."""
    mn, mode = OPCODES[mem[pc]]
    out = set(pointer_bytes(mem, pc, x) or ())
    if mode in MEM_MODES and mn not in ("JSR", "JMP") and not store:
        out |= expected_writes(mem, pc, x, y, sp, a)
    return out


class Classifier:
    """Owner map and port state over one run; ``cells`` are the observed scenario tuples,
    ``first`` the call (or the host's ``index``, when set) each was first seen at."""

    def __init__(self, base, banked=False):
        self.base = bytes(base)
        self.banked = banked
        self.owner = {}
        self.port = ("reset", False, -1, "main", None)
        self.lift_bits = None
        self.call = -1
        self.ctx = "main"
        self.cells = set()
        self.first = {}
        self.index = None
        self.unclassified = []

    def start_calls(self, bits=None):
        """End the pre-lift phase: the image and bank bits now are the lift-time state."""
        self.call, self.lift_bits = 0, bits

    def next_call(self):
        self.call += 1

    def write(self, addr, writer, channel, rmw, pc, wview="ram"):
        """Record ``writer`` as the last writer of ``addr`` (``ctl`` marks a call frame)."""
        self.owner[addr] = (writer, channel, rmw, self.call, self.ctx, pc, wview)

    def port_change(self, channel, rmw, pc):
        """An instruction changed the PLA bits."""
        self.port = (channel, rmw, self.call, self.ctx, pc)

    def _timing(self, writer, call, ctx, wpc, pc):
        if writer in _PUSHERS or ctx in ("irq", "nmi"):
            return "async"
        if call < 0:
            return "pre_lift"
        if call < self.call:
            return "earlier_call"
        return "same_insn" if wpc == pc and ctx == self.ctx else "same_call_earlier"

    def _emit(self, writer, channel, rmw, role, timing, mapping):
        cell = Cell(writer, channel, rmw, role, timing, mapping)
        self.cells.add(cell)
        self.first.setdefault(cell, self.call if self.index is None else self.index)

    def _port_emit(self, role, pc, mapping):
        channel, rmw, call, ctx, wpc = self.port
        self._emit(
            "cpu_port", channel, rmw, role, self._timing("cpu_port", call, ctx, wpc, pc), mapping
        )

    def fetch(self, addr, value, role, pc, fview="ram", from_rom=False):
        """A fetched byte: a cell when it differs from the lift-time image or was written."""
        if self.call < 0:
            return
        if role == "ptr_indirect" and from_rom and addr in _KVEC:
            role = "kernal_vector"
        if fview != "ram":
            if fview == "io" or value != self.base[addr]:
                self._port_emit(role, pc, "rom_vs_ram")
            return
        o = self.owner.get(addr)
        if o is None:
            if value != self.base[addr]:
                self.unclassified.append(("unowned", addr, role, pc))
            return
        writer, channel, rmw, call, ctx, wpc, wview = o
        if writer == "ctl":
            return
        mapping = "flat"
        if self.banked and wview == "rom":
            mapping = "ram_under_rom"
        elif self.banked and IO_WINDOW[0] <= addr <= IO_WINDOW[1]:
            mapping = "ram_under_io"
        self._emit(writer, channel, rmw, role, self._timing(writer, call, ctx, wpc, pc), mapping)

    def bank(self, bits, pc):
        """A decode under ``bits``: a ``bank_reg`` cell when the map left its lift-time state."""
        if self.call >= 0 and bits != self.lift_bits:
            self._port_emit("bank_reg", pc, "flat")
