"""S0 -- machine image, entry/cadence discovery, init runner, 6510 port and CIA models.

Public API:

* ``MachineImage.from_sid(data)`` -- 64 KiB pre-init image (power-on RAM overlaid
  with the load band) plus the header facts a driver needs.
* ``find_entries(data, mem=None, written=None)`` -- ``(MachineImage, [Entry(kind,
  addr, cycles_per_tick, source, kernal)])``; raises :class:`Refusal`.
* ``entry_frame(entry)`` / ``frame_slots(entry)`` -- what the machine pushed below
  the return address entering it, and the slot each byte sits at.
* ``init_runner(vm, pc, cache, lifter, budget)`` -- run ``init`` to its balancing
  RTS or to a ``JMP *`` idle loop; returns the idle pc or ``None``.
* ``port_bank(mem)`` -- ``$D000-$DFFF`` mapping (``io``/``charrom``/``ram``) from
  the 6510 port ($00 direction, $01 data).
* ``CIA(base)`` -- minimal Timer-A/ICR model (count-down at cycle rate, ICR bit on
  underflow) so an init busy-wait on ``$DC04``/``$DC0D`` terminates.
"""

# pysidtracker is an optional extra: its imports are deferred into the functions
# that need it so the front end loads without it.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

from dataclasses import dataclass, asdict

from .. import c64

INIT_BUDGET = 2_000_000
PAL_FRAME = 19656
CIA1_BASE = 0xDC00
CIA2_BASE = 0xDD00


class Refusal(Exception):
    """A diagnosed refusal (design principle 6). ``reason`` is machine-readable."""

    def __init__(self, reason, detail=""):
        super().__init__("%s: %s" % (reason, detail) if detail else reason)
        self.reason = reason
        self.detail = detail


STATUS = "P"  # the status byte the 6510 pushes at an interrupt
KERNAL_SAVE = (0, 1, 2)  # what the $FF48 prologue pushes on top of it: A, X, Y


@dataclass(frozen=True)
class Entry:
    """One play entry of the schedule."""

    kind: str  # "sub" (header play, JSR per tick) | "irq" (installed handler)
    addr: int
    cycles_per_tick: int
    source: str  # cadence source: pal_video / ntsc_video / cia_timer / ...
    kernal: bool = False  # "irq" only: the vector is CINV, so the KERNAL dispatches

    def to_dict(self):
        d = asdict(self)
        if self.kind != "irq":
            del d["kernal"]  # a subroutine entry has no vector to dispatch through
        return d


def entry_frame(entry):
    """What the machine pushes below the return address entering ``entry``, in push order.

    :data:`STATUS` is the byte the 6510 pushes at an interrupt; a CINV entry is
    reached through the KERNAL prologue at ``$FF48``, which saves A, X and Y on
    top of it -- exactly the three ``$EA81`` pops before its ``RTI``.
    """
    get = entry.get if isinstance(entry, dict) else lambda k, d=None: getattr(entry, k, d)
    if get("kind") != "irq":
        return ()
    return (STATUS,) + (KERNAL_SAVE if get("kernal") else ())


def frame_slots(entry):
    """``{slot above the entry pointer: what the machine left there}``."""
    frame = entry_frame(entry)
    return {len(frame) - i: w for i, w in enumerate(frame)}


@dataclass
class MachineImage:
    """The 64 KiB pre-init machine image and the container's entry facts."""

    mem: bytes
    lo: int
    hi: int
    init: int
    play: int
    songs: int
    startsong: int

    @classmethod
    def from_sid(cls, data):
        band, _load, init, play = c64.load_psid(data)
        lo, hi = c64.psid_image(data)
        mem = bytearray(c64.poweron_ram())
        mem[lo:hi] = band[lo:hi]
        mem[0xD418] = 0x0F  # PSID cold start: host leaves maximum volume
        mem[0], mem[1] = 0x2F, 0x37  # the 6510 port a KERNAL-initialised host leaves
        songs, startsong = c64.psid_songs(data)
        return cls(bytes(mem), lo, hi, init, play, songs, startsong)

    def in_band(self, addr):
        return self.lo <= addr < self.hi

    def meta(self):
        return {
            "load": [self.lo, self.hi],
            "init": self.init,
            "play": self.play,
            "songs": self.songs,
            "startsong": self.startsong,
        }


def port_bank(mem):
    """``$D000-$DFFF`` mapping from the 6510 port: ``io``, ``charrom`` or ``ram``.

    A port bit configured as input ($00 bit clear) reads as 1 (the port's
    pull-ups), which is why players that clear $00 to use $00/$01 as a zero-page
    pointer keep I/O mapped.
    """
    p = (mem[1] | ~mem[0]) & 7
    if not p & 3:
        return "ram"
    if not p & 4:
        return "charrom"
    return "io"


class CIA:
    """Minimal CIA Timer-A + ICR read model (one chip at ``base``).

    Timer A counts down from its latch at the cycle rate while started; reads of
    ``$xx04``/``$xx05`` return the current counter and a read of the ICR
    (``$xx0D``) returns, and clears, the underflow flag.
    """

    __slots__ = ("base", "latch", "counter", "running", "t0", "cycles0")

    def __init__(self, base):
        self.base = base
        self.latch = 0xFFFF
        self.counter = 0xFFFF
        self.running = False
        self.t0 = 0
        self.cycles0 = 0

    def _elapsed(self, cycles):
        return (cycles - self.t0) % (self.latch + 1)

    def underflows(self, cycles):
        return (cycles - self.t0) // (self.latch + 1) if self.running else 0

    def _off(self, addr):
        """Register index within this chip's page (the CIA mirrors every 16 bytes)."""
        d = addr - self.base
        return (d & 0x0F) if 0 <= d < 0x100 else -1

    def read(self, addr, cycles):
        """Value for ``addr``, or ``None`` when this chip does not model it."""
        off = self._off(addr)
        if off == 0x04 or off == 0x05:
            v = self.latch - self._elapsed(cycles) if self.running else self.counter
            return (v >> 8) & 0xFF if off == 0x05 else v & 0xFF
        if off == 0x0D:
            n = self.underflows(cycles)
            flag = 1 if n > self.cycles0 else 0
            self.cycles0 = n
            return flag | (flag << 7)
        return None

    def write(self, addr, val, cycles):
        off = self._off(addr)
        if off == 0x04 or off == 0x05:
            if off == 0x04:
                self.latch = (self.latch & 0xFF00) | (val & 0xFF)
            else:
                self.latch = (self.latch & 0x00FF) | ((val & 0xFF) << 8)
            self.counter = self.latch
            self.t0 = cycles  # restart the count so underflows stay monotone
            self.cycles0 = 0
        elif off == 0x0E:
            if val & 0x10:  # force load
                self.counter = self.latch
                self.t0 = cycles
                self.cycles0 = 0
            if val & 1 and not self.running:
                self.t0 = cycles
                self.cycles0 = 0
            self.running = bool(val & 1)


def _cadence(data):
    """``(cycles_per_tick, source)`` from ``pysidtracker``, else a PAL frame."""
    try:
        from pysidtracker.cadence import playroutine_cadence
    except ImportError:  # pragma: no cover - pysidtracker is an optional extra
        return PAL_FRAME, "assumed_pal"
    cad = playroutine_cadence(data)
    return cad.cycles_per_call, cad.source.value


def _init_topology(data):
    """Installed vectors/latches observed by ``pysidtracker.trace_init``."""
    try:
        from pysidtracker.image import SidImage
        from pysidtracker.trace import trace_init
    except ImportError:  # pragma: no cover - pysidtracker is an optional extra
        return None
    return trace_init(SidImage.from_bytes(data), play_calls=0)


def find_entries(data, mem=None, written=None):
    """``(MachineImage, [Entry])`` -- the pre-init image and the tick schedule of ``data``.

    ``play != 0`` gives a ``sub`` entry at the header play address; otherwise the
    installed handler is taken from the init trace, falling back to
    :func:`c64.installed_handler` over the caller's own post-init ``mem`` and
    write set ``written``. A CINV vector carries ``kernal`` (see
    :func:`entry_frame`). Refuses on a second interrupt source or no entry.
    """
    img = MachineImage.from_sid(data)
    cycles, source = _cadence(data)
    topo = _init_topology(data)
    if topo is not None:
        if topo.cia2_timer_latch is not None or topo.nmi_vector is not None:
            raise Refusal(
                "second interrupt source armed",
                "cia2_latch=%s nmi=%s" % (topo.cia2_timer_latch, topo.nmi_vector),
            )
    if img.play:
        return img, [Entry("sub", img.play, cycles, source)]
    handler, kernal = None, False
    if topo is not None:
        handler, kernal = (
            (topo.irq_vector, True) if topo.irq_vector else (topo.hw_irq_vector, False)
        )
    if handler is None and mem is not None:
        found = c64.installed_handler(mem, written or set(), (img.lo, img.hi))
        handler, kernal = found if found else (None, False)
    if not handler:
        raise Refusal("no entry", "play=0 and no interrupt vector installed")
    return img, [Entry("irq", handler, cycles, source, kernal)]


def is_idle(mem, pc):
    """True when ``pc`` holds ``JMP *`` (an init that never returns)."""
    return mem[pc] == 0x4C and (mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8)) == pc


def init_runner(vm, pc, cache, lifter, budget=INIT_BUDGET):
    """Run ``init`` at ``pc`` to its balancing RTS; returns a ``JMP *`` idle pc or ``None``.

    Uses the dummy-return convention of :func:`deity_informant.run_sub`. Raises
    :class:`Refusal` (``init runaway``) when ``budget`` instructions are exceeded.
    """
    reg = vm.reg
    start = reg[3]
    vm._push(0x00)
    vm._push(0x01)
    mem = vm.mem
    n = 0
    while reg[3] < start:
        if is_idle(mem, pc):
            return pc
        pc = vm.step(pc, cache, lifter)
        n += 1
        if n > budget:
            raise Refusal("init runaway", "%d instructions without returning" % budget)
    return None
