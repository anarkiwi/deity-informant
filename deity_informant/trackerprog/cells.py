"""B7 -- where a certified tune's state lives, in section 5's cell vocabulary.

One question, asked of an address: which named cell of the object holds it,
answered from the S6 view alone. Four answers: a ``voice`` cell, a ``global``
scalar, a ``pitch`` entry, ``ins.pw``; past a fused tuning, the cell there.
A voice cell is a member of the ``voice`` group or a field of a record S6 split
into one copy per voice, which is the same datum written the other way about.
"""

from __future__ import annotations

import re

TABLEKIND = ("const", "init_constant")
RECORDS = 13
# the names section 5 answers itself: a cell of a tune may not take one of them
RESERVED = ("voice_index", "counter", "phase", "tied", "freq_hi", "freq_lo", "pw_lo", "pw_hi", "pw")


def ident(s):
    """A name the object may carry: the printed one, with nothing else in it."""
    return re.sub(r"[^A-Za-z0-9_.]", "_", s)


class Cells:
    """The address map of one certified tune, in the object's own vocabulary."""

    def __init__(self, view, names, pitch=None, inspw=()):
        self.view, self.names = view, names
        self.rgn = sorted((r for r in view.storage if r.id >= 0), key=lambda r: (r.base, r.size))
        self.byid = {r.id: r for r in self.rgn}
        self.group = {}
        for g, d in sorted((names.groups or {}).items()):
            for rid in sorted(d.get("members", ())):
                field = ident((names.view.get(rid) or (g, names.of(rid)))[1])
                if field in RESERVED:  # a name section 5 answers itself is not a tune's
                    field = ident(g) + "_" + field
                self.group[rid] = (g, field, max(int(d.get("stride", 1)), 1), int(d["n"]))
        self.voices = next(
            (int(d["n"]) for g, d in (names.groups or {}).items() if g == "voice"), 1
        )
        self.split = self._splits(names)
        self.pitch = pitch  # (region id, base address, entry count)
        self.inspw = dict(inspw)  # region id -> "lo" | "hi"
        self.used, self.vcells, self.rename, self.bcast = {}, {}, {}, set()

    def _splits(self, names):
        """``{region: ((offset, name), ...)}`` for a record split one copy per voice.

        A field of such a record is a per-voice cell: its own copies stand one
        after another, which is what a stride of one over ``n`` voices says. Two
        records may name one field, so a name a second record repeats is qualified
        by the group S6 gives it.
        """
        seen = {n for _g, n, _s, _k in self.group.values()}
        out = {}
        for g, d in sorted((names.groups or {}).items()):
            rid = d.get("split")
            if rid is None or int(d["n"]) != self.voices or int(d.get("stride", 1)) != 1:
                continue
            got = []
            for k, name in sorted((int(k), ident(v)) for k, v in (d.get("fields") or {}).items()):
                name = name if name not in seen and name not in RESERVED else ident(g) + "_" + name
                seen.add(name)
                got.append((k, name))
            if got:
                out[rid] = tuple(got)
        return out

    def declare(self, name, base, copies=None):
        """A cell the lowering needs by name: a voice's own, seeded from the image.

        ``copies`` is how many the image holds: one for a scalar the whole tune
        shares, which every voice's own copy then enters the horizon with.
        """
        self.vcells.setdefault(name, base)
        if copies == 1:
            self.bcast.add(name)
        self.used.setdefault(name, set()).add(0)
        return name

    def voicecell(self, base):
        """The name a per-voice array based at ``base`` is read and written under."""
        if base in self.rename:
            return self.declare(self.rename[base], base)
        got = self.at(base)
        if got and got[0] == "voice":
            return self.declare(got[1][0], base)
        return self.declare("c%04X" % base, base)

    def scalarcell(self, base, name=None):
        """A scalar the whole tune shares, read as a cell: one copy per voice, equal.

        Every voice steps its own copy by the same rule, so the copies stay the
        one value the tune keeps -- which is what a clock outside the voice loop
        (section 3.6) needs of a per-voice ``tempo.cell``.
        """
        got = ident(name or self.names.of(self.region(base).id if self.region(base) else -1))
        if got in RESERVED or got in self.vcells and self.vcells[got] != base:
            got = "c%04X" % base
        self.rename[base] = got
        return self.declare(got, base, copies=1)

    def region(self, addr):
        """The narrowest region holding ``addr``, a state cell before a table."""
        hits = [r for r in self.rgn if r.base <= addr < r.base + r.size]
        return min(hits, key=lambda r: (r.kind not in ("state", "const"), r.size), default=None)

    def kind_of(self, rid):
        r = self.byid.get(rid)
        return None if r is None else r.kind

    def istable(self, rid):
        """A region the play never writes: its bytes are the object's data."""
        return self.kind_of(rid) in TABLEKIND

    def at(self, addr):
        """``(kind, payload)`` for one address, or ``None`` where the object has no cell."""
        for rid, part in self.inspw.items():
            r = self.byid[rid]
            k, rem = divmod(addr - r.base, max(r.stride, 1))
            if addr >= r.base and not rem and 0 <= k < RECORDS:
                return ("inspw", (part, k))
        r = self.region(addr)
        if r is None:
            return None
        if self.pitch is not None and r.id == self.pitch[0]:
            base, n = self.pitch[1], self.pitch[2]
            if base <= addr < base + 2 * n:
                return ("pitch", divmod(addr - base, 2))
            return self._narrower(addr, r.id)
        if r.id in self.split:
            return self._field(addr, r)
        if r.id in self.group:
            return self._grouped(addr, r)
        if r.size == 1 or (r.kind in TABLEKIND and r.stride == 1):
            return ("global", ident(self.names.of(r.id)))
        return None

    def _field(self, addr, r):
        """One field of a record split per voice: its name, and the copy it is."""
        k = addr - r.base
        got = [x for x in self.split[r.id] if x[0] <= k]
        if not got:
            return None
        off, name = got[-1]
        return ("voice", (name, k - off)) if k - off < self.voices else None

    def _grouped(self, addr, r):
        g, field, stride, n = self.group[r.id]
        k = (addr - r.base) // stride
        return ("voice", (field, k)) if g == "voice" and 0 <= k < n else None

    def _narrower(self, addr, skip):
        """The cell a fused region's own state holds, past the tuning it also holds."""
        for r in self.rgn:
            if r.id == skip or not r.base <= addr < r.base + r.size:
                continue
            if r.id in self.split:
                got = self._field(addr, r)
                if got is not None:
                    return got
            if r.id in self.group:
                got = self._grouped(addr, r)
                if got is not None:
                    return got
            elif r.size == 1:
                return ("global", ident(self.names.of(r.id)))
        return None

    def wordat(self, addr):
        """One byte past a tuning: the cell that holds it, declared or named."""
        got = self.at(addr)
        if got is not None:
            return got
        for name, base in self.vcells.items():
            if base is not None and base <= addr < base + self.voices:
                return ("voice", (name, addr - base))
        return None

    def name(self, addr, voice_indexed):
        """The §5 cell name one address is read and written under, or ``None``."""
        got = self.at(addr)
        if got is None:
            return None
        kind, pay = got
        if kind == "voice":
            self.used.setdefault(pay[0], set()).add(pay[1])
            return pay[0] if voice_indexed else None
        if kind == "global":
            self.used.setdefault("#" + pay, {0})
            return "#" + pay
        return "ins.pw." + pay[0] if kind == "inspw" else None

    def seed(self, img):
        """``state0.cells`` and ``state0.globals``: what the post-init image left."""
        cells, glob = {}, {}
        for name in sorted(self.used):
            base = self.vcells.get(name)
            if base is None:
                base = self.baseof(name.lstrip("#"))
            if base is None:
                continue
            if name.startswith("#"):
                glob[name[1:]] = int(img[base])
            elif name in self.bcast:  # one scalar, entered by every voice's own copy
                cells[name] = [int(img[base])] * self.voices
            else:
                cells[name] = [int(img[base + k]) for k in range(self.voices)]
        for name, base in self.vcells.items():
            if base is None:
                cells[name] = [0] * self.voices
        return cells, glob

    def baseof(self, field):
        for rid, (g, f, _s, _n) in self.group.items():
            if f == field and g == "voice":
                return self.byid[rid].base
        for rid, fields in self.split.items():
            for off, name in fields:
                if name == field:
                    return self.byid[rid].base + off
        for r in self.rgn:
            if ident(self.names.of(r.id)) == field:
                return r.base
        return None
