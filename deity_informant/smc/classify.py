"""Classify oracle-observed writes and fetches into scenario cells.

Both oracles feed one :class:`Classifier`: ``write`` records each byte's last writer,
``fetch`` checks a fetched byte against the lift-time image and its writer, ``port_change``
and ``bank`` track the PLA map. Events the axes cannot express go to ``unclassified``.
"""

from __future__ import annotations

from functools import lru_cache

from jennings.opcodes import MEM_MODES, MODE_LEN, OPCODES
from vagrant_moses.bus import banking

from .space import Cell

__all__ = ["Classifier", "view", "insn_reads", "expected_writes", "expected_reads", "IO_WINDOW"]

IO_WINDOW = (0xD000, 0xDFFF)
_KVEC = range(0x0314, 0x031A)
_PUSHERS = ("irq_push", "nmi_push")


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
    mn, mode = OPCODES[mem[pc]]
    out = [(pc, "opcode")] + [((pc + k) & 0xFFFF, "operand") for k in range(1, MODE_LEN[mode])]
    b1 = mem[(pc + 1) & 0xFFFF]
    if mode == "ind":
        w = b1 | mem[(pc + 2) & 0xFFFF] << 8
        out += [(w, "ptr_indirect"), ((w & 0xFF00) | ((w + 1) & 0xFF), "ptr_indirect")]
    elif mode == "indy":
        out += [(b1, "ptr_indirect"), ((b1 + 1) & 0xFF, "ptr_indirect")]
    elif mode == "indx":
        p = (b1 + x) & 0xFF
        out += [(p, "ptr_indirect"), ((p + 1) & 0xFF, "ptr_indirect")]
    if mn in ("RTS", "RTI"):
        k = 1 if mn == "RTS" else 2
        out += [(0x100 | ((sp + k + i) & 0xFF), "ret_addr") for i in (0, 1)]
    return out


def expected_writes(mem, pc, x, y, sp):
    """The addresses an instruction at ``pc`` may store to, by its addressing form."""
    mn, mode = OPCODES[mem[pc]]
    b1, b2 = mem[(pc + 1) & 0xFFFF], mem[(pc + 2) & 0xFFFF]
    w = b1 | b2 << 8
    if mn in ("PHA", "PHP", "JSR", "BRK"):
        return {0x100 | ((sp - k) & 0xFF) for k in range(3)}
    if mode == "zp":
        return {b1}
    if mode in ("zpx", "zpy"):
        return {(b1 + (x if mode == "zpx" else y)) & 0xFF}
    if mode == "abs":
        return {w}
    if mode in ("absx", "absy"):
        return {(w + (x if mode == "absx" else y)) & 0xFFFF}
    if mode == "indx":
        p = (b1 + x) & 0xFF
        return {mem[p] | mem[(p + 1) & 0xFF] << 8}
    if mode == "indy":
        return {((mem[b1] | mem[(b1 + 1) & 0xFF] << 8) + y) & 0xFFFF}
    return set()


def expected_reads(mem, pc, x, y, sp, store=False):
    """The data addresses an instruction at ``pc`` reads (pointer bytes, operand), by its
    addressing form; ``store`` marks a store that does not read its operand. Stack pulls and
    return-address reads are not data reads."""
    mn, mode = OPCODES[mem[pc]]
    out = {a for a, role in insn_reads(mem, pc, x, sp) if role == "ptr_indirect"}
    if mode in MEM_MODES and mn not in ("JSR", "JMP") and not store:
        out |= expected_writes(mem, pc, x, y, sp)
    return out


class Classifier:
    """Owner map and port state over one run; ``cells`` are the observed scenario tuples."""

    def __init__(self, base, banked=False):
        self.base = bytes(base)
        self.banked = banked
        self.owner = {}
        self.port = ("reset", False, -1, "main", None)
        self.lift_bits = None
        self.call = -1
        self.ctx = "main"
        self.cells = set()
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
        self.cells.add(Cell(writer, channel, rmw, role, timing, mapping))

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
