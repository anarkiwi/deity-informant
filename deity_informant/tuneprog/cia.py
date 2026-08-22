"""S0 -- the CIA 6526 as much of it as a playroutine's schedule needs.

Two timers (Timer A on the cycle rate, Timer B on cycles or Timer A's
underflows), one-shot mode, the accumulated ICR mask, the latched flags and the
interrupt line. CIA #1's line is the 6510's IRQ and CIA #2's its NMI, so
:mod:`.nmi` reads :meth:`CIA.edge_at` to say when the second schedule dispatches.
"""

from __future__ import annotations

CIA1_BASE = 0xDC00
CIA2_BASE = 0xDD00
ICR_TA, ICR_TB = 0x01, 0x02  # CIA ICR mask bits: Timer-A, Timer-B
ICR_SOURCES = 0x1F  # ICR bits 0-4: Timer A, Timer B, TOD alarm, serial, FLAG
ICR_SET = 0x80  # an ICR write with bit 7 enables the sources it names, else disables them


def grid_count(origin, step, cycles):
    """Underflows of a timer reloaded at ``origin`` every ``step`` cycles, by ``cycles``.

    Never negative: a latch write phases the grid on the underflow still pending,
    whose origin can sit past the write.
    """
    return max((cycles - origin) // step, 0)


def grid_next(origin, step, cycles):
    """The first underflow of that timer strictly after ``cycles``."""
    return origin + (grid_count(origin, step, cycles) + 1) * step


class CIA:
    """Minimal CIA timer + ICR model (one chip at ``base``).

    Timer A counts cycles, Timer B cycles or Timer A's underflows (CRB bits 5-6),
    both halting after one underflow in one-shot mode; ICR writes accumulate the
    mask the way the chip does. :meth:`edge_at` is the cycle this chip's line next
    asserts and :meth:`sources` the sources that can still raise it.
    """

    __slots__ = (
        "base",
        "latch",
        "counter",
        "running",
        "t0",
        "cycles0",
        "icr",
        "run_b",
        "latch_b",
        "counter_b",
        "t0b",
        "cycles0b",
        "fa0",
        "fb0",
        "cra",
        "crb",
        "fl",
        "ir",
    )

    def __init__(self, base):
        self.base = base
        self.latch = 0xFFFF
        self.counter = 0xFFFF
        self.running = False
        self.t0 = 0
        self.cycles0 = 0
        self.icr = 0  # the KERNAL's reset write of $7F disables every source
        self.run_b = False
        self.latch_b = 0xFFFF
        self.counter_b = 0xFFFF
        self.t0b = 0
        self.cycles0b = 0
        self.fa0 = 0
        self.fb0 = 0
        self.cra = 0
        self.crb = 0
        self.fl = 0  # latched flag bits: set by the event, cleared by an ICR read
        self.ir = False  # the line, latched: an NMI is its rising edge

    def underflows(self, cycles):
        return grid_count(self.t0, self.latch + 1, cycles) if self.running else 0

    def _remaining(self, bit, cycles):
        """The count this timer's register pair reads back: the cycles left, less one."""
        at = self._edge(bit, cycles)
        return self.counter if at is None else at - cycles - 1

    def grid(self, bit):
        """``(origin, step)`` of this timer's underflow grid, ``None`` when it has none.

        Timer B linked to Timer A counts its underflows, so its grid is Timer A's
        coarsened by its own latch and phased on the first Timer-A underflow
        after its own reload.
        """
        if bit == ICR_TA:
            return (self.t0, self.latch + 1) if self.running else None
        if not self.run_b:
            return None
        mode = (self.crb >> 5) & 3
        if mode == 0:
            return self.t0b, self.latch_b + 1
        if mode != 2 or not self.running:
            return None  # CNT-driven: no pin model, so no schedule
        step = self.latch + 1
        j0 = grid_count(self.t0, step, self.t0b) + 1
        return self.t0 + (j0 - 1) * step, step * (self.latch_b + 1)

    def _phase(self, bit, origin, due, cycles):
        """The reload time a latch write leaves: the pending underflow keeps its cycle.

        Writing a started timer's latch does not touch its counter, so the new
        period begins at the underflow already due; a stopped one reloads now.
        """
        g = self.grid(bit)
        if due is None or g is None:
            return cycles
        return origin + due - g[0] - g[1]

    def _oneshot(self, bit):
        return bool((self.cra if bit == ICR_TA else self.crb) & 0x08)

    def _count(self, bit, cycles):
        """Underflows this timer has had by ``cycles``; one-shot mode stops after one."""
        g = self.grid(bit)
        if g is None:
            return 0
        n = grid_count(g[0], g[1], cycles)
        return min(n, 1) if self._oneshot(bit) else n

    def _edge(self, bit, cycles):
        """The cycle this timer's next underflow lands on, or ``None``."""
        g = self.grid(bit)
        if g is None or (self._oneshot(bit) and self._count(bit, cycles)):
            return None
        return grid_next(g[0], g[1], cycles)

    def _off(self, addr):
        """Register index within this chip's page (the CIA mirrors every 16 bytes)."""
        d = addr - self.base
        return (d & 0x0F) if 0 <= d < 0x100 else -1

    def read(self, addr, cycles):
        """Value for ``addr``, or ``None`` when this chip does not model it."""
        off = self._off(addr)
        if off == 0x04 or off == 0x05:
            v = self._remaining(ICR_TA, cycles) if self.running else self.counter
            return (v >> 8) & 0xFF if off == 0x05 else v & 0xFF
        if off == 0x0D:
            n = self.underflows(cycles)
            flag = 1 if n > self.cycles0 else 0
            self.cycles0 = n
            nb = self._count(ICR_TB, cycles)
            fb = 1 if nb > self.cycles0b else 0
            self.cycles0b = nb
            self._latch_flags(cycles)
            self.fl, self.ir = 0, False  # a read clears the flags and releases the line
            return flag | (fb << 1) | ((flag | fb) << 7)
        return None

    def write(self, addr, val, cycles):
        off = self._off(addr)
        if off < 0:
            return
        self._latch_flags(cycles)
        if off == 0x04 or off == 0x05:
            due = self._edge(ICR_TA, cycles)
            if off == 0x04:
                self.latch = (self.latch & 0xFF00) | (val & 0xFF)
            else:
                self.latch = (self.latch & 0x00FF) | ((val & 0xFF) << 8)
            self.counter = self.latch
            self.t0 = self._phase(ICR_TA, self.t0, due, cycles)
            self.cycles0 = 0
        elif off == 0x06 or off == 0x07:
            due = self._edge(ICR_TB, cycles)
            if off == 0x06:
                self.latch_b = (self.latch_b & 0xFF00) | (val & 0xFF)
            else:
                self.latch_b = (self.latch_b & 0x00FF) | ((val & 0xFF) << 8)
            self.counter_b = self.latch_b
            self.t0b = self._phase(ICR_TB, self.t0b, due, cycles)
            self.cycles0b = 0
        elif off == 0x0D:
            self.icr = (self.icr | val) & ICR_SOURCES if val & ICR_SET else self.icr & ~val
        elif off == 0x0E:
            self.cra = val & 0xFF
            if val & 0x10:  # force load
                self.counter = self.latch
                self.t0 = cycles
                self.cycles0 = 0
            if val & 1 and not self.running:
                self.t0 = cycles
                self.cycles0 = 0
            self.running = bool(val & 1)
        elif off == 0x0F:
            self.crb = val & 0xFF
            if val & 0x10:
                self.counter_b = self.latch_b
                self.t0b = cycles
                self.cycles0b = 0
            if val & 1 and not self.run_b:
                self.t0b = cycles
                self.cycles0b = 0
            self.run_b = bool(val & 1)
        self.fa0 = self._count(ICR_TA, cycles)
        self.fb0 = self._count(ICR_TB, cycles)

    def sources(self):
        """Enabled sources whose event can still occur: a timer source must be started."""
        m = self.icr & ICR_SOURCES
        return m & ~(0 if self.running else ICR_TA) & ~(0 if self.run_b else ICR_TB)

    def unmodelled(self):
        """Enabled sources this model has no schedule for: TOD, serial, FLAG, a CNT timer."""
        m = self.sources()
        keep = ICR_TA | (ICR_TB if self.grid(ICR_TB) is not None else 0)
        return m & ~keep

    def _latch_flags(self, cycles):
        """Latch each timer's flag bit for the underflows it has had since the last look."""
        if self._count(ICR_TA, cycles) > self.fa0:
            self.fl |= ICR_TA
        if self._count(ICR_TB, cycles) > self.fb0:
            self.fl |= ICR_TB
        self.fa0 = self._count(ICR_TA, cycles)
        self.fb0 = self._count(ICR_TB, cycles)

    def edge_at(self, cycles):
        """The cycle this chip's interrupt line next asserts, or ``None``.

        The line is edge-triggered at the 6510: an IR no ICR read has released
        raises nothing more, and a flag latched before the mask named it raises
        as soon as it is named.
        """
        self._latch_flags(cycles)
        m = self.icr & ICR_SOURCES
        if self.ir or not m:
            return None
        if self.fl & m:
            return cycles
        at = [t for b in (ICR_TA, ICR_TB) if m & b for t in (self._edge(b, cycles),) if t]
        return min(at) if at else None

    def raise_line(self):
        """Latch IR: the line stays asserted until an ICR read releases it."""
        self.ir = True
