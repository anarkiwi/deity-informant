"""Canonical per-frame projection of SID write logs.

Pure log domain: a frame is an iterable of (reg, val) writes with reg a SID
offset $00-$1C (absolute $D400-$D41C accepted, anything else rejected).
No imports from the rest of the package."""

from collections import namedtuple

_ABS = 0xD400
_NREG = 0x1D
_FILTER = (0x15, 0x16, 0x17, 0x18)
_LO = tuple(7 * v + o for v in range(3) for o in (0, 2)) + (0x15,)
_OTHER = {r + d: r + 1 - d for r in _LO for d in (0, 1)}

SECTIONS = ("v0.lww", "v0.ord", "v1.lww", "v1.ord", "v2.lww", "v2.ord", "filter", "residual")

Divergence = namedtuple("Divergence", "frame section pos got want")


def _pair(reg, val):
    """Normalize one write: absolute address to offset, both fields checked."""
    if _ABS <= reg < _ABS + _NREG:
        reg -= _ABS
    if not 0 <= reg < _NREG:
        raise ValueError("not a SID register: %r" % (reg,))
    if not 0 <= val <= 0xFF:
        raise ValueError("not a byte value: %r" % (val,))
    return reg, val


def canonical(frames, held0=None):
    """Per-frame canonical records: 8 tuples of (reg, val), one per SECTIONS.

    Freq/PW/cutoff are 16-bit: touching either lane reports the whole word, the
    unwritten lane seeded by ``held0``. Other last-write-wins cells are elided
    when unwritten; ctrl/AD/SR and the tail $19-$1C keep write order."""
    out, held = [], dict(held0 or {})
    for frame in frames:
        lww = {}
        ordv = ([], [], [], [])
        for reg, val in frame:
            reg, val = _pair(reg, val)
            if reg >= 0x19:
                ordv[3].append((reg, val))
            elif reg >= 0x15 or reg % 7 < 4:
                lww[reg] = held[reg] = val
                other = _OTHER.get(reg)
                if other is not None:
                    lww.setdefault(other, held.get(other, 0))
            else:
                ordv[reg // 7].append((reg, val))
        rec = []
        for v in range(3):
            b = 7 * v
            rec.append(tuple((r, lww[r]) for r in range(b, b + 4) if r in lww))
            rec.append(tuple(ordv[v]))
        rec.append(tuple((r, lww[r]) for r in _FILTER if r in lww))
        rec.append(tuple(ordv[3]))
        out.append(tuple(rec))
    return out


def dumps(frames):
    """Stable text form: one line per frame, sections joined by '|',
    entries 'RR=VV' hex joined by ','."""
    return "\n".join(
        "|".join(",".join("%02X=%02X" % e for e in sec) for sec in rec) for rec in canonical(frames)
    )


def loads(text):
    """Inverse of dumps: text back to the canonical record list."""
    out = []
    for line in text.splitlines():
        secs = line.split("|")
        if len(secs) != len(SECTIONS):
            raise ValueError("bad frame line: %r" % line)
        out.append(
            tuple(
                (
                    tuple(
                        _pair(int(r, 16), int(v, 16))
                        for r, v in (e.split("=") for e in sec.split(","))
                    )
                    if sec
                    else ()
                )
                for sec in secs
            )
        )
    return out


def digi_frames(frames):
    """(frame_index, collapsed $18 volume-nibble tuple) per digi-class frame.

    Consecutive duplicate volume nibbles collapse to one step; a frame is
    digi-class when its collapsed sequence has more than two steps.
    Empty result = FP-class clean."""
    out = []
    for i, frame in enumerate(frames):
        seq = []
        for reg, val in frame:
            reg, val = _pair(reg, val)
            if reg == 0x18:
                nib = val & 0x0F
                if not seq or seq[-1] != nib:
                    seq.append(nib)
        if len(seq) > 2:
            out.append((i, tuple(seq)))
    return out


def diff(a, b):
    """First divergence between two canonical record lists, or None.

    Returns Divergence(frame, section, pos, got, want) with None marking
    the absent side of a missing entry or missing frame."""
    for f, (ra, rb) in enumerate(zip(a, b)):
        if ra == rb:
            continue
        for name, sa, sb in zip(SECTIONS, ra, rb):
            if sa == sb:
                continue
            for p in range(max(len(sa), len(sb))):
                ea = sa[p] if p < len(sa) else None
                eb = sb[p] if p < len(sb) else None
                if ea != eb:
                    return Divergence(f, name, p, ea, eb)
    if len(a) != len(b):
        f = min(len(a), len(b))
        return Divergence(f, None, None, a[f] if f < len(a) else None, b[f] if f < len(b) else None)
    return None


def frames_from_walker(walker_like, nframes):
    """Adapter: step a walker one frame at a time into cycle-stripped frames.

    Duck-typed: walker_like needs run(1) (one per-entry run) appending
    (cycle, reg, val) entries to its wlog list."""
    out = []
    for _ in range(nframes):
        start = len(walker_like.wlog)
        walker_like.run(1)
        out.append([(reg, val) for _c, reg, val in walker_like.wlog[start:]])
    return out
