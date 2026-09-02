"""B7 -- where a certified tune's state lives, in section 5's cell vocabulary.

One question, asked of an address: which named cell of the object holds it,
answered from the S6 view alone. Four answers: a ``voice`` cell, a ``global``
scalar, a ``pitch`` entry, ``ins.pw``; past a fused tuning, the cell there.
"""

from __future__ import annotations

import re

TABLEKIND = ("const", "init_constant")
RECORDS = 13


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
        for g, d in (names.groups or {}).items():
            for rid in d.get("members", ()):
                field = (names.view.get(rid) or (g, names.of(rid)))[1]
                self.group[rid] = (g, ident(field), max(int(d.get("stride", 1)), 1), int(d["n"]))
        self.voices = next(
            (int(d["n"]) for g, d in (names.groups or {}).items() if g == "voice"), 1
        )
        self.pitch = pitch  # (region id, base address, entry count)
        self.inspw = dict(inspw)  # region id -> "lo" | "hi"
        self.used, self.vcells, self.rename = {}, {}, {}

    def declare(self, name, base):
        """A cell the lowering needs by name: a voice's own, seeded from the image."""
        self.vcells.setdefault(name, base)
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
        if r.id in self.group:
            return self._grouped(addr, r)
        if r.size == 1 or (r.kind in TABLEKIND and r.stride == 1):
            return ("global", ident(self.names.of(r.id)))
        return None

    def _grouped(self, addr, r):
        g, field, stride, n = self.group[r.id]
        k = (addr - r.base) // stride
        return ("voice", (field, k)) if g == "voice" and 0 <= k < n else None

    def _narrower(self, addr, skip):
        """The cell a fused region's own state holds, past the tuning it also holds."""
        for r in self.rgn:
            if r.id == skip or not r.base <= addr < r.base + r.size:
                continue
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
        for r in self.rgn:
            if ident(self.names.of(r.id)) == field:
                return r.base
        return None
