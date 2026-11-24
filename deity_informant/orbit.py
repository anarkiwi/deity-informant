"""The orbit of an entry called with no input (the lifter brief's play-entry invariant): from a
state the host leaves, repeated calls are a deterministic map on memory, so the states up to
the first repeat (by digest) are every state the calls reach, and the addresses each
instruction's data accesses take over them are exact. Where memory as a whole does not repeat
soon, :func:`cover` stops where jennings' components say every access and edge has been seen."""

import numpy as np
from jennings.coverage import components
from jennings.devices.mpu6502 import NativeMPU, Remap
from jennings.memory import NativeMemory
from jennings.opcodes import OPCODES

from . import masks

#: The calls :func:`cover` follows an entry for at most.
COVER_LIMIT = 2_000_000
#: The calls jennings' components are first learned over; more where they prove too few.
LEARN = 4096


def sources(site):
    """Per address, what a data access by overrun ``site``'s instruction there reads (as
    ``info["overruns"]`` gives it): -1 the address itself, -2 0, else the address of the same
    byte of the last entry of the run below it (the first run's where none is)."""
    out = np.full(0x10000, -1, np.int64)
    out[site["addr"][0] : site["addr"][1] + 1] = -2
    for lo, hi in site["ok"]:
        out[lo : hi + 1] = -1
    last, at = site.get("last"), np.flatnonzero(out == -2)
    if last and at.size:
        first, step = np.array(last, np.int64).reshape(-1, 2).T
        ends = np.array([hi for _, hi in site["ok"]], np.int64)
        i = np.maximum(np.searchsorted(ends, at) - 1, 0)
        out[at] = first[i] + (at - first[i]) % step[i]
    return out


def orbit(image, pc, volatile, limit, sites=(), max_cycles=10_000_000):
    """``(frames, {(pc, store): sorted addresses}, {pc: sorted successors})`` of calls of
    ``pc`` from ``image`` up to the first entry state seen before, under the overrun ``sites``
    (a load outside one's object reads what :func:`sources` gives, a store there writes
    nothing): the addresses each instruction's data accesses take and the instructions each
    one is followed by; None where :func:`walk` does not close."""
    how, *got = walk(image, pc, volatile, limit, sites, max_cycles)
    return tuple(got) if how == "closed" else None


def walk(image, pc, volatile, limit, sites=(), max_cycles=10_000_000):
    """``(how, frames, accesses, successors)`` of the calls of :func:`orbit` up to its first
    repeated state (``how`` ``"closed"``), ``limit`` calls (``"open"``) or the first call that
    reads a ``volatile`` address (``"volatile"``) or jams (``"jam"``), that call included: one
    native run (:meth:`~jennings.devices.mpu6502.NativeMPU.orbit`)."""
    vol = masks.mask(volatile)
    return _walk(image, pc, vol, limit, _remap(sites, vol), max_cycles)


def cover(image, pc, volatile, limit=COVER_LIMIT, sites=(), max_cycles=10_000_000):
    """``(calls, accesses, successors)`` of the calls of ``pc`` from ``image`` up to jennings'
    cover point under the overrun ``sites`` (as :func:`orbit`): the calls after which every data
    access and control edge has been seen, the feed cells' first repeat
    (:func:`jennings.coverage.components.cover_calls`, its components learned and widened until
    autonomous, their periods checked over as many calls again), the accesses and edges those
    calls first take in the census that checks them; None where they do not repeat within
    ``limit`` calls or a call reads a ``volatile`` address or does not return."""
    vol = masks.mask(volatile)
    got = _cover(image, pc, vol, limit, _remap(sites, vol), max_cycles)
    return None if got is None else got[:3]


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def _cover(image, pc, vol, limit, remap, max_cycles, start=None):
    """:func:`cover` under the volatile mask ``vol`` and ``remap``, with the
    :class:`~jennings.coverage.components.Calls` it ended on: ``start(cells)`` gives the first
    such calls recording ``cells`` where they can be taken on from another run's (None to run
    them anew)."""
    kw = {"max_cycles": max_cycles, "remap": remap}
    exclude = (components.STACK, *_spans(vol))
    learned, seen = min(LEARN, limit), set()
    try:
        comps = components.learn(image, pc, learned, exclude=exclude, **kw)
        while (comps.feed, learned) not in seen:
            seen.add((comps.feed, learned))
            ran = start and start(comps.persistent)
            start = None
            ran = ran or components.Calls(image, pc, comps.persistent, **kw)
            calls = ran.repeat(comps.feed, limit, learned)
            got = None if calls is None else _seen(ran, calls, vol)
            if got is None:
                return None
            if not comps.persistent:
                # No call reads what one before it wrote: every call runs alike.
                return calls, *got, ran
            try:
                n, wider = components.cover_calls(
                    image,
                    pc,
                    comps,
                    min(limit, 2 * calls + learned),
                    exclude=exclude,
                    ran=ran,
                    **kw,
                )
            except RuntimeError:
                # A conflict no persistent cell explains: a cell the calls learned never wrote.
                learned = min(limit, max(8 * learned, 2 * calls))
                comps = components.learn(image, pc, learned, exclude=exclude, **kw)
                continue
            if n == calls and wider.feed == comps.feed:
                return calls, *got, ran
            learned, comps = max(learned, wider.calls), wider
    except RuntimeError:
        pass
    return None


_LOAD, _STORE = components.ACCESSES.index("load"), components.ACCESSES.index("store")


def _seen(ran, calls, vol):
    """``(accesses, successors)`` (as :func:`orbit`) of the first ``calls`` of ``ran``, None
    where one loads a ``vol`` address."""
    got = np.unique(ran.seen("accesses", calls)[0].astype(np.int64))
    kind = (got >> 16) & 7
    got = got[(kind == _LOAD) | (kind == _STORE)]
    if vol[got[(got >> 16) & 7 == _LOAD] & 0xFFFF].any():
        return None
    key = (got >> 19) << 1 | ((got >> 16) & 7 == _STORE)
    edges = np.unique(ran.seen("edges", calls)[0].astype(np.int64))
    out, succ = {}, {}
    for k, at in zip(*_groups(key)):
        out[(int(k) >> 1, bool(k & 1))] = (got[at] & 0xFFFF).tolist()
    for p, at in zip(*_groups(edges >> 16)):
        succ[int(p)] = (edges[at] & 0xFFFF).tolist()
    return out, succ


def _diverges(ran, a, b):
    """The first call of ``ran`` (a run under remap tables ``a``, :func:`_tables`) with an
    access that tables ``b`` redirect otherwise; its calls where none does."""
    keys, at = ran.seen("accesses", ran.calls)
    keys = keys.astype(np.int64)
    first = ran.calls
    for p in a.keys() | b.keys():
        ta, tb = a.get(p, _PLAIN), b.get(p, _PLAIN)
        mine = (keys >> 19) == p
        if mine.any():
            addr = keys[mine] & 0xFFFF
            differ = ta[addr] != tb[addr]
            if differ.any():
                first = min(first, int(at[mine][differ].min()))
    return first


def _walk(image, pc, vol, limit, remap, max_cycles, cells=None):
    """:func:`walk` under the volatile mask ``vol`` and ``remap``, its state the ``cells``
    (locations as jennings names them) where given, else all of memory."""
    mpu = NativeMPU(memory=NativeMemory(bytes(image)))
    keep = None if cells is not None else _spans(~vol)
    how, frames, got, edges = mpu.orbit(
        pc,
        0,
        max_cycles,
        limit,
        keep,
        _spans(vol),
        remap,
        None if cells is None else sorted(cells),
    )
    got, edges = np.asarray(got, np.int64), np.asarray(edges, np.int64)
    out, succ = {}, {}
    for key, at in zip(*_groups(got >> 16)):
        out[(int(key) >> 1, bool(key & 1))] = (got[at] & 0xFFFF).tolist()
    for p, at in zip(*_groups(edges >> 16)):
        succ[int(p)] = (edges[at] & 0xFFFF).tolist()
    return how, frames, out, succ


def _spans(mask):
    """The runs of ``mask`` as ``range`` objects."""
    return [range(lo, lo + n) for lo, n in masks.runs(mask)]


def _groups(keys):
    """``(distinct keys, [slice of each])`` of sorted ``keys``."""
    start = np.flatnonzero(np.diff(keys, prepend=-1))
    ends = np.append(start[1:], len(keys))
    return keys[start], [slice(a, b) for a, b in zip(start, ends)]


def _remap(sites, vol):
    """The :class:`~jennings.devices.mpu6502.Remap` of the overrun ``sites``' rule (a volatile
    address read as it is, the pointer bytes of an indirect instruction too); None for none."""
    at = _tables(sites, vol)
    if not at:
        return None
    return Remap(at, [o for o, (_, mode) in OPCODES.items() if mode in ("indx", "indy")])


def _tables(sites, vol):
    """``{pc: int32 per address}`` of the overrun ``sites`` that redirect some access (as
    :func:`sources`, a ``vol`` address read as it is)."""
    at = {s["pc"]: sources(s) for s in sites}
    for t in at.values():
        t[vol] = -1
    return {p: t.astype(np.int32) for p, t in at.items() if (t != -1).any()}


def _alike(a, b, accesses, image):
    """Whether remap tables ``a`` and ``b`` (:func:`_tables`) treat every access of
    ``accesses`` (``{(pc, store): addresses}``, a run from ``image`` under ``a``) alike, so
    calls run under one as under the other: each store is redirected under both or neither,
    each load reads the same byte, or under both a byte of ``image`` no store of the run writes
    that holds the same value (0 for a load redirected to read 0); with no accesses, whether the
    tables are equal."""
    if accesses is None:
        return a.keys() == b.keys() and all(np.array_equal(a[p], b[p]) for p in a)
    mem = np.frombuffer(image, np.uint8).astype(np.int16)
    written = np.zeros(0x10000, bool)
    for (pc, store), at in accesses.items():
        if store:
            at = np.asarray(at)
            written[at[a.get(pc, _PLAIN)[at] == -1]] = True
    for pc in a.keys() | b.keys():
        ta, tb = a.get(pc, _PLAIN), b.get(pc, _PLAIN)
        at = np.asarray(accesses.get((pc, True), []), np.int64)
        if ((ta[at] == -1) != (tb[at] == -1)).any():
            return False
        at = np.asarray(accesses.get((pc, False), []), np.int64)
        at = at[(ta[at] != tb[at]) & ~np.isin(at, _pointer(pc, image, written))]
        if at.size:
            (va, ua), (vb, ub) = _read(ta, at, mem, written), _read(tb, at, mem, written)
            if ua.any() or ub.any() or (va != vb).any():
                return False
    return True


def _pointer(pc, image, written):
    """The pointer bytes a ``(zp),Y`` instruction at ``pc`` of ``image`` loads where its operand
    is no byte the run writes (``written``): every remap reads them as they are."""
    if OPCODES[image[pc]][1] != "indy" or written[(pc + 1) & 0xFFFF]:
        return []
    zp = image[(pc + 1) & 0xFFFF]
    return [zp, (zp + 1) & 0xFF]


def _read(table, at, mem, written):
    """``(values, unknown)`` per load at ``at`` under ``table``: the byte of ``mem`` it reads
    (0 for one redirected to read 0), and whether that byte is one ``written``."""
    src = np.where(table[at] == -1, at, table[at])
    real = np.maximum(src, 0)
    return np.where(src == -2, 0, mem[real]), (src >= 0) & written[real]


#: The remap table of an instruction no site redirects.
_PLAIN = np.full(0x10000, -1, np.int32)


class Runs:
    """The runs of the entry at ``pc`` from states under overrun sites: :func:`cover` where
    ``limit`` is ``"cover"``, else :func:`orbit` within ``limit`` calls. A state's run under
    new sites is an earlier one's where the sites' rule treats every access it made alike
    (:func:`_alike`), else a cover is taken on from an earlier one's census up to the first
    call whose accesses the new sites redirect otherwise (:func:`_diverges`); ``ran`` counts
    the runs made."""

    def __init__(self, pc, volatile, limit):
        self.pc, self.volatile, self.limit = pc, volatile, limit
        self.vol = masks.mask(volatile)
        self.done, self.census = {}, {}

    @property
    def ran(self):
        """The runs made, over all states."""
        return sum(map(len, self.done.values()))

    def __call__(self, state, sites=()):
        """``(calls, accesses, successors)`` of the run from ``state`` under ``sites``, or
        None (:func:`cover`, :func:`orbit`)."""
        state = bytes(state)
        tables = _tables(sites, self.vol)
        for old, got in self.done.get(state, ()):
            if _alike(old, tables, None if got is None else got[1], state):
                return got
        if self.limit == "cover":
            got = _cover(
                state,
                self.pc,
                self.vol,
                COVER_LIMIT,
                _remap(sites, self.vol),
                10_000_000,
                self._start(state, tables, sites),
            )
            if got is not None:
                self.census.setdefault(state, []).append((tables, got[3]))
                got = got[:3]
        else:
            got = orbit(state, self.pc, self.volatile, self.limit, sites)
        self.done.setdefault(state, []).append((tables, got))
        return got

    def _start(self, state, tables, sites):
        """``start`` (as :func:`_cover` takes it) for a cover from ``state`` under ``sites``
        (remap ``tables``): the last earlier census of that state, where it records the cells,
        taken on from its first call the new tables redirect otherwise."""
        if not self.census.get(state):
            return None
        old, ran = self.census[state][-1]
        first = _diverges(ran, old, tables)

        def start(cells):
            if first == 0 or not ran.holds(cells):
                return None
            return ran.fork(first, _remap(sites, self.vol))

        return start
