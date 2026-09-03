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

    def __init__(self, view, names, pitch=None, inspw=(), words=()):
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
        voice = next((d for g, d in (names.groups or {}).items() if g == "voice"), {})
        self.voices = int(voice.get("n", 1))
        # a voice's copies stand this far apart: S6's own stride for the group
        self.stride = max(int(voice.get("stride", 1)), 1)
        self.split = self._splits(names)
        self.pitch = pitch  # (region ids, the halves' entry-0 addresses, step, entries)
        self.inspw = dict(inspw)  # region id -> "lo" | "hi"
        self.used, self.vcells, self.rename, self.bcast = {}, {}, {}, set()
        self.words = dict(words)  # the widest access each constant address takes
        self.widths = {}  # a declared cell wider than a byte: what its seed reads

    def _splits(self, names):
        """``{region: (fields, the copy stride)}`` for a record split per voice.

        A field of such a record is a per-voice cell, and S6 states the record
        either way about: copies one after another under fields at their own
        offsets (a stride of 1), or copies a record apart with the fields inside
        it. Two records may name one field, so a repeated name is qualified.
        """
        seen = {n for _g, n, _s, _k in self.group.values()}
        out = {}
        for g, d in sorted((names.groups or {}).items()):
            rid, n = d.get("split"), int(d["n"])
            stride = max(int(d.get("stride", 1)), 1)
            flat = stride == 1 and n == self.voices
            if rid is None or not (flat or (stride == self.stride and n and not n % self.voices)):
                continue
            got, blocks = [], 1 if flat else n // self.voices
            for b in range(blocks):
                for k, name in sorted(
                    (int(k), ident(v)) for k, v in (d.get("fields") or {}).items()
                ):
                    name = name if not b else "%s_%d" % (name, b)
                    if name in seen or name in RESERVED:
                        name = ident(g) + "_" + name
                    seen.add(name)
                    got.append((b * self.voices * stride + k, name))
            if got:
                out[rid] = (tuple(got), stride if not flat else 1)
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

    def bytecell(self, base):
        """Memory the play writes that no region names: a global of the object, by address."""
        name = "#c%04X" % base
        self.widths[name] = self.words.get(base, 1)
        return self.declare(name, base)

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
        if self.pitch is not None and r.id in self.pitch[0]:
            got = self._tuned(addr)
            if got is not None:
                return got
        got = self._named(addr, r)
        return got if got is not None else self._narrower(addr, r.id)

    def _named(self, addr, r):
        """What one region calls an address of its own, or ``None`` where it names none."""
        if self.pitch is not None and r.id in self.pitch[0]:
            return None
        if r.id in self.split:
            return self._field(addr, r)
        if r.id in self.group:
            return self._grouped(addr, r)
        if r.size == 1 or (r.kind in TABLEKIND and r.stride == 1):
            return ("global", ident(self.names.of(r.id)))
        return None

    def _tuned(self, addr):
        """``("pitch", (entry, half))`` where one address is a byte of the tuning."""
        _rids, bases, step, n = self.pitch
        for half, base in enumerate(bases):
            k, rem = divmod(addr - base, step)
            if not rem and 0 <= k < n:
                return ("pitch", (k, half))
        return None

    def _field(self, addr, r):
        """One field of a record split per voice: its name, and the copy it is."""
        fields, stride = self.split[r.id]
        k = addr - r.base
        if stride > 1:
            copy, off = divmod(k, stride)
            got = dict(fields).get(copy // self.voices * self.voices * stride + off)
            return ("voice", (got, copy % self.voices)) if got else None
        hit = [x for x in fields if x[0] <= k]
        if not hit:
            return None
        off, name = hit[-1]
        return ("voice", (name, k - off)) if k - off < self.voices else None

    def _grouped(self, addr, r):
        """One copy of a per-voice array: the copies stand the group's own stride apart.

        Arrays at a stride overlap, so an address off the stride is another
        array's and not this one's copy of it.
        """
        g, field, stride, n = self.group[r.id]
        k, rem = divmod(addr - r.base, stride)
        return ("voice", (field, k)) if g == "voice" and not rem and 0 <= k < n else None

    def _narrower(self, addr, skip):
        """The cell another region holds this address in: past a tuning, or off a stride."""
        for r in self.rgn:
            if r.id == skip or not r.base <= addr < r.base + r.size:
                continue
            got = self._named(addr, r)
            if got is not None:
                return got
        return None

    def wordat(self, addr):
        """One byte past a tuning: the cell that holds it, declared or named."""
        got = self.at(addr)
        if got is not None:
            return got
        for name, base in self.vcells.items():
            if base is None:
                continue
            k, rem = divmod(addr - base, self.stride)
            if not rem and 0 <= k < self.voices:
                return ("voice", (name, k))
        return None

    def name(self, addr, voice_indexed):
        """The §5 cell name one address is read and written under, or ``None``.

        An address that names one *copy* of a per-voice cell is the committing
        voice's own where it is the first, and no cell of the object otherwise.
        """
        got = self.at(addr)
        if got is None:
            return None
        kind, pay = got
        if kind == "voice":
            self.used.setdefault(pay[0], set()).add(pay[1])
            return pay[0] if voice_indexed and not pay[1] else None
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
                w = self.widths.get(name, 1)
                glob[name[1:]] = int.from_bytes(img[base : base + w], "little")
            elif name in self.bcast:  # one scalar, entered by every voice's own copy
                cells[name] = [int(img[base])] * self.voices
            else:
                cells[name] = [int(img[base + k * self.stride]) for k in range(self.voices)]
        for name, base in self.vcells.items():
            if base is not None:
                continue
            if name.startswith("#"):
                glob[name[1:]] = 0
            else:
                cells[name] = [0] * self.voices
        return cells, glob

    def baseof(self, field):
        for rid, (g, f, _s, _n) in self.group.items():
            if f == field and g == "voice":
                return self.byid[rid].base
        for rid, (fields, _s) in self.split.items():
            for off, name in fields:
                if name == field:
                    return self.byid[rid].base + off
        for r in self.rgn:
            if ident(self.names.of(r.id)) == field:
                return r.base
        return None
