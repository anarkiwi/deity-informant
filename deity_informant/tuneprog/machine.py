"""S0 -- machine image, entry and cadence discovery, init runner, the 6510 port.

Public API:

* ``MachineImage.from_sid(data)`` -- 64 KiB pre-init image (power-on RAM overlaid
  with the load band) plus the header facts a driver needs.
* ``find_entries(data, mem=None, written=None, song=None)`` -- ``(MachineImage,
  [Entry(kind, addr, cycles_per_tick, source, kernal)])``; raises :class:`Refusal`.
* ``shared_entry(data, songs)`` -- the one entry every subtune shares, else a
  :class:`Refusal`.
* ``entry_frame(entry)`` / ``frame_slots(entry)`` -- what the machine pushed below
  the return address entering it, and the slot each byte sits at.
* ``init_runner(vm, pc, cache, lifter, budget)`` -- run ``init`` to its balancing
  RTS or to a ``JMP *`` idle loop; returns the idle pc or ``None``.
* ``port_bank(mem)`` / ``kernal_mapped(mem)`` -- what the 6510 port ($00
  direction, $01 data) maps at ``$D000-$DFFF`` and at ``$E000-$FFFF``.
* ``vector_gate(mem, written, img)`` -- which installed interrupt vector that
  port really dispatches through; raises :class:`Refusal` when none does.
"""

# pysidtracker is an optional extra: its imports are deferred into the functions
# that need it so the front end loads without it.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache

from .. import c64

INIT_BUDGET = 2_000_000
PAL_FRAME = 19656
NTSC_FRAME = 17095
FRAME = {"pal": (PAL_FRAME, "pal_video"), "ntsc": (NTSC_FRAME, "ntsc_video")}
VIDEO = {"pal_video": "pal", "ntsc_video": "ntsc"}  # the cadence sources that are a frame
HOST_LATCH = {"pal": 0x4025, "ntsc": 0x4295}  # CIA1 Timer-A as the KERNAL/psiddrv leave it


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
    source: str  # cadence source: pal_video / ntsc_video / cia_timer / pal_host_cia / ...
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
    kind = get("kind")
    if kind == "nmi":
        return (STATUS,)  # $FE43 and a raw $FFFA both enter on the status byte alone
    if kind != "irq":
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


def port_bits(mem):
    """The 6510 port's three bank lines (LORAM, HIRAM, CHAREN).

    A line configured as input ($00 bit clear) reads as 1 (the port's pull-ups),
    which is why players that clear $00 to use $00/$01 as a zero-page pointer
    keep the ROMs and I/O mapped.
    """
    return (mem[1] | ~mem[0]) & 7


def port_bank(mem):
    """``$D000-$DFFF`` mapping from the 6510 port: ``io``, ``charrom`` or ``ram``."""
    p = port_bits(mem)
    if not p & 3:
        return "ram"
    if not p & 4:
        return "charrom"
    return "io"


def kernal_mapped(mem):
    """True when the KERNAL ROM answers at ``$E000-$FFFF``: the port's HIRAM line.

    It is what decides the dispatch. With HIRAM set the 6510 takes its IRQ vector
    from the ROM's ``$FFFE``, so the entry is the ``$FF48`` prologue and CINV;
    with it clear that vector is the RAM under the ROM and no prologue runs.
    """
    return bool(port_bits(mem) & 2)


def _installed(mem, written, img, pair):
    """True when a vector carries a handler: written, or lifted from the load image."""
    if pair[0] in written or pair[1] in written:
        return True
    lo, hi = img
    return lo <= pair[0] and pair[1] < hi and bool(c64.read_vector(mem, pair[0]))


def vector_gate(mem, written, img=(0, 0), settled=True):
    """``(vector address, kernal)`` of the vector this machine really dispatches through.

    The port decides which of the two is live. Refuses (``vector banked out``)
    when the live vector carries no handler and the dead one does; over an
    unsettled port -- the pre-init image, which init still has -- it takes the
    installed one instead and :meth:`~.trace.Tracer._settle` decides.
    """
    kernal = kernal_mapped(mem)
    live, dead = (c64.IRQ_VEC, c64.HW_IRQ_VEC) if kernal else (c64.HW_IRQ_VEC, c64.IRQ_VEC)
    if _installed(mem, written, img, live):
        return live[0], kernal
    if _installed(mem, written, img, dead):
        if not settled:
            return dead[0], not kernal
        raise Refusal(
            "vector banked out",
            "$%04X is installed, but the port dispatches through $%04X" % (dead[0], live[0]),
        )
    raise Refusal("no entry", "play=0 and no interrupt vector installed")


def host_cia(std):
    """``(cycles_per_tick, source)`` of the host's own play interrupt on ``std``.

    Where a tune programs no timer of its own, the trigger is whatever the host
    runs: CIA #1 Timer-A at :data:`HOST_LATCH`, reloaded from the latch, so two
    underflows are ``latch + 1`` cycles apart.
    """
    return HOST_LATCH[std] + 1, std + "_host_cia"


@lru_cache(maxsize=4)
def _traced(data):
    """``(Cadence, InitTrace)`` of one image: the timer and vectors init programs."""
    try:
        from pysidtracker.cadence import playroutine_cadence
        from pysidtracker.image import SidImage
        from pysidtracker.trace import trace_init
    except ImportError:  # pragma: no cover - pysidtracker is an optional extra
        return None, None
    return playroutine_cadence(data), trace_init(SidImage.from_bytes(data), play_calls=0)


def _cadence(data, song):
    """``(cycles_per_tick, source)`` for subtune ``song`` (0-based).

    The tune's own armed timer wins (design principle: the traced machine
    decides), and that timer is CIA #1's: CIA #2's line is the NMI, so its latch
    is not a tick whatever period it holds. Where the tune programs none the
    trigger is the host's, and which host it is the container says:
    ``sidplayfp``'s PSID driver rasters at a video frame unless the header
    ``speed`` bit selects its CIA for this subtune, and an RSID runs the real
    KERNAL, whose default IRQ *is* that CIA -- unless the tune armed a raster
    compare of its own, which then keeps the frame.
    """
    cad, topo = _traced(data)
    if cad is None:  # pragma: no cover - pysidtracker is an optional extra
        return PAL_FRAME, "assumed_pal"
    if cad.source.value in VIDEO:
        cycles, source = cad.cycles_per_call, cad.source.value
    elif cad.latch == topo.cia1_timer_latch:
        return cad.cycles_per_call, cad.source.value
    else:
        cycles, source = FRAME["ntsc" if c64.is_ntsc(data) else "pal"]
    host = topo.vic_raster is None if c64.is_rsid(data) else c64.speed_cia(data, song)
    return host_cia(VIDEO[source]) if host else (cycles, source)


def _init_topology(data):
    """Installed vectors/latches observed by ``pysidtracker.trace_init``."""
    return _traced(data)[1]


def find_entries(data, mem=None, written=None, song=None):
    """``(MachineImage, [Entry])`` -- the pre-init image and the tick schedule of ``data``.

    ``play != 0`` gives a ``sub`` entry at the header play address; otherwise
    :func:`vector_gate` decides which installed vector the port dispatches
    through, over ``mem``/``written`` where the caller has them and the pre-init
    image otherwise -- a provisional answer :meth:`~.trace.Tracer.run_init`
    settles once init has had the port. ``song`` (0-based, default the header's
    ``startsong``) picks whose ``speed`` bit :func:`_cadence` reads. A second
    schedule is :mod:`.nmi`'s, decided over the chip this tracer's own init
    leaves rather than over a second emulation's last writes.
    """
    img = MachineImage.from_sid(data)
    cycles, source = _cadence(data, img.startsong - 1 if song is None else song)
    topo = _init_topology(data)
    if img.play:
        return img, [Entry("sub", img.play, cycles, source)]
    installed = {}
    if topo is not None:
        for vec, val in ((c64.IRQ_VEC, topo.irq_vector), (c64.HW_IRQ_VEC, topo.hw_irq_vector)):
            if val:
                installed[vec[0]] = val
    view = img.mem if mem is None else mem
    seen = set(written or ()) | set(installed)
    vec, kernal = vector_gate(view, seen, (img.lo, img.hi), settled=mem is not None)
    handler = installed.get(vec) or c64.read_vector(view, vec)
    if not handler:
        raise Refusal("no entry", "vector $%04X is installed but null" % vec)
    return img, [Entry("irq", handler, cycles, source, kernal)]


def shared_entry(data, songs):
    """The one entry every subtune of ``songs`` (1-based) shares.

    Raises :class:`Refusal` where they differ: one merged trace is one schedule.
    """
    seen = {find_entries(data, song=n - 1)[1][0] for n in songs}
    if len(seen) > 1:
        raise Refusal("subtunes disagree on cadence", " | ".join(sorted(map(str, seen))))
    return seen.pop()


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
    step = vm.step
    for _ in range(budget + 1):
        if reg[3] >= start:
            return None
        if is_idle(mem, pc):
            return pc
        pc = step(pc, cache, lifter)
    raise Refusal("init runaway", "%d instructions without returning" % budget)
