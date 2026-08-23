"""The recorded trace: the S1 result type, its files, and the union of several.

Split from :mod:`.trace` (which produces it) so the record type can be loaded and
queried without the tracer. :func:`merge` is the ``--songs all`` front end's input:
one program from every subtune's trace, keyed by the union of their SMC cells.
:func:`input_kind` classifies a pinned input's address, which is a property of the
record rather than of the machine that made it.
"""

from __future__ import annotations

import json
from array import array
from collections import Counter, defaultdict
from hashlib import blake2b
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .ir import IO_HI, IO_LO, SID_HI, SID_LO, STACK_HI, STACK_LO

ACKS = (0xD019, 0xDC0D, 0xDD0D)
REG_IN = 0x10000  # synthetic input addresses for live-in A/X/Y


def input_kind(addr):
    """Input class of ``addr`` (design section 4 ``Input.kind``)."""
    if addr >= REG_IN:
        return "entry_reg"
    if addr in ACKS:
        return "ack"
    if addr == 0xD011 or addr == 0xD012:
        return "raster"
    if SID_LO <= addr <= SID_HI:
        return "sid_readback"
    if 0xDC00 <= addr <= 0xDDFF:
        return "cia"
    if IO_LO <= addr <= IO_HI:
        return "io"
    return "uninit_ram"


class Footprint:
    """The play-phase write footprint, gathered as one numpy take per tick.

    Growth is monotone, so the sorted address vector and the stack-page mask that
    separates the two witnesses are rebuilt only when the footprint gains an
    address, and each tick is one gather over that vector.
    """

    __slots__ = ("n", "idx", "keep", "nfree", "view")

    def __init__(self):
        self.n = -1
        self.idx = np.empty(0, np.intp)
        self.keep = np.empty(0, bool)
        self.nfree = 0
        self.view = None

    def gather(self, vm):
        """``(full bytes, full size, page-free bytes, page-free size)`` of this tick."""
        w = vm.written_play
        if self.n != len(w):
            self.n = len(w)
            self.idx = a = np.fromiter(sorted(w), np.intp, self.n)
            self.keep = (a < STACK_LO) | (a > STACK_HI)
            self.nfree = int(self.keep.sum())
        if self.view is None:
            self.view = np.frombuffer(memoryview(vm.mem), dtype=np.uint8)
        b = self.view[self.idx]
        return b.tobytes(), self.n, b[self.keep].tobytes(), self.nfree

    def __getstate__(self):
        return self.n, self.idx, self.keep, self.nfree

    def __setstate__(self, st):
        self.n, self.idx, self.keep, self.nfree = st
        self.view = None


class Stream:
    """One per-tick state hash over a footprint, and its periodicity witness.

    The page-free stream leaves the stack page out: it is the machine's own
    scratch, which a program :func:`~.stack.eliminate` proved stack-free can
    neither read nor write.
    """

    __slots__ = ("hashes", "period", "first_repeat", "rows", "nrows")

    def __init__(self):
        self.hashes = {}
        self.period = None
        self.first_repeat = None
        self.rows = array("Q")
        self.nrows = array("I")

    def hash(self, buf, n, ninp, call):
        """Hash one gathered footprint; a repeat with no input consumed is a witness."""
        h = blake2b(buf, digest_size=8, key=n.to_bytes(4, "little")).digest()
        v = int.from_bytes(h, "little")
        self.rows.append(v)
        self.nrows.append(n)
        if self.period is None:
            prev = self.hashes.get((n, v))
            if prev is None:
                self.hashes[(n, v)] = (call, ninp)
            elif prev[1] == ninp:
                # design S1: a repeat is a witness only with no inputs consumed
                # between the two calls (otherwise the next tick may differ).
                self.period = call - prev[0]
                self.first_repeat = call


def site_key(pc, opcode, insn_bytes, cells):
    """``(pc, opcode, fixed operand bytes)``; operand bytes in ``cells`` drop out."""
    fixed = tuple(
        None if (pc + k) & 0xFFFF in cells else insn_bytes[k] for k in range(1, len(insn_bytes))
    )
    return pc, opcode, fixed


@dataclass
class Trace:
    """The recorded run: sites, edges, calls, logs, inputs, per-call hashes.

    ``wlog``/``iolog`` are column arrays ``call, addr, val, cyc``; rows written
    during ``init`` carry ``call = 0xFFFFFFFF`` (and are also in ``init_writes``).
    ``sites`` is keyed by :func:`~deity_informant.tuneprog.trace.site_key` and its
    ``reads``/``writes`` map a P-Code op index to the exact address set it touched.
    """

    meta: dict
    image_pre: bytes
    image_post_init: bytes
    sites: dict = field(default_factory=dict)
    edges: dict = field(default_factory=dict)
    calls: dict = field(default_factory=dict)
    rets: dict = field(default_factory=dict)
    summaries: dict = field(default_factory=dict)
    inputs: list = field(default_factory=list)
    input_sites: dict = field(default_factory=dict)
    init_writes: list = field(default_factory=list)
    written_init: set = field(default_factory=set)
    written_play: set = field(default_factory=set)
    cells: set = field(default_factory=set)
    code: set = field(default_factory=set)
    cell_values: dict = field(default_factory=dict)
    jsr_targets: set = field(default_factory=set)
    wlog: dict = field(default_factory=dict)
    iolog: dict = field(default_factory=dict)
    nmilog: dict = field(default_factory=dict)
    state_hash: object = None
    footprint_size: object = None
    state_hash_free: object = None
    footprint_free: object = None
    chip_ops: set = None  # (pc, op index) pairs that reached a chip; None = every one did

    def witness(self, free=True):
        """The earliest tick a footprint repeated at, or ``None``.

        A program S4 proved stack-free may claim the page-free witness, so either
        footprint will do; a residual one must claim the page-inclusive repeat.
        """
        keys = ("first_repeat", "first_repeat_free") if free else ("first_repeat",)
        hits = [h for h in (self.meta.get(k) for k in keys) if h is not None]
        return min(hits) if hits else None

    def site_at(self, pc):
        """All site keys recorded at ``pc`` (>1 when the pc is an opcode cell)."""
        return [k for k in self.sites if k[0] == pc]

    def variants_at(self, pc):
        """Opcodes executed at ``pc`` (more than one means an SMC opcode cell)."""
        return sorted({k[1] for k in self.sites if k[0] == pc})

    def opcode_cells(self):
        """``{pc: [opcode, ...]}`` for pcs executed with more than one opcode."""
        by_pc = defaultdict(set)
        for pc, opcode, _fixed in self.sites:
            by_pc[pc].add(opcode)
        return {pc: sorted(v) for pc, v in by_pc.items() if len(v) > 1}

    def writers_of(self, addr):
        """Site keys whose write footprint contains ``addr``."""
        return [k for k, s in self.sites.items() if any(addr in w for w in s["writes"].values())]

    def is_chip(self, pc, op):
        """True where this op reached the chip at $D000-$DFFF rather than the RAM under it.

        The 6510 port decides, so the tracer records the ops that ran with I/O
        mapped; a trace that recorded none (an older one) says every op did.
        """
        return self.chip_ops is None or (pc, op) in self.chip_ops

    # ---- serialisation -----------------------------------------------------
    def save(self, path):
        """Write ``trace.json`` (structure) + ``trace.npz`` (bulk arrays) into ``path``.

        Refuses a trace :func:`~.closure.close_static` has run on: its synthesised
        sites carry a stamp this format does not hold, and a reload would take them
        for executed code.
        """
        if any(s.get("closed") for s in self.sites.values()):
            raise ValueError("a statically closed trace is an in-memory product; do not save it")
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        doc = {
            "meta": self.meta,
            "sites": [
                [
                    k[0],
                    k[1],
                    list(k[2]),
                    s["count"],
                    s["phases"],
                    [list(v) for v in s["variants"]],
                    s["idx"],
                    [[i, sorted(a)] for i, a in s["reads"].items()],
                    [[i, sorted(a)] for i, a in s["writes"].items()],
                ]
                for k, s in self.sites.items()
            ],
            "edges": [[f, o, t, k, n] for (f, o, t), (k, n) in self.edges.items()],
            "calls": [
                [p, o, v["ret_pc"], v["count"], sorted(v["targets"].items())]
                for (p, o), v in self.calls.items()
            ],
            "rets": [
                [
                    p,
                    o,
                    v["unmatched"],
                    sorted(v["matched"].items()),
                    sorted(v["targets"].items()),
                    sorted(v["loose"].items()),
                ]
                for (p, o), v in self.rets.items()
            ],
            "summaries": [[k, v["rd"], v["wr"], v["count"]] for k, v in self.summaries.items()],
            "inputs": self.inputs,
            "input_sites": [
                [k[0], k[1], v["kind"], v["count"], v["phase"]] for k, v in self.input_sites.items()
            ],
            "init_writes": self.init_writes,
            "written_init": sorted(self.written_init),
            "written_play": sorted(self.written_play),
            "chip_ops": None if self.chip_ops is None else sorted(self.chip_ops),
            "cells": sorted(self.cells),
            "code": sorted(self.code),
            "cell_values": [[a, sorted(v)] for a, v in sorted(self.cell_values.items())],
            "jsr_targets": sorted(self.jsr_targets),
        }
        (path / "trace.json").write_text(json.dumps(doc))
        np.savez_compressed(
            path / "trace.npz",
            image_pre=np.frombuffer(self.image_pre, dtype=np.uint8),
            image_post_init=np.frombuffer(self.image_post_init, dtype=np.uint8),
            state_hash=self.state_hash,
            footprint_size=self.footprint_size,
            state_hash_free=self.state_hash_free,
            footprint_free=self.footprint_free,
            **{"wlog_" + k: v for k, v in self.wlog.items()},
            **{"iolog_" + k: v for k, v in self.iolog.items()},
            **{"nmilog_" + k: v for k, v in self.nmilog.items()},
        )
        return path

    @classmethod
    def load(cls, path):
        """Read back a :meth:`save`d trace directory."""
        path = Path(path)
        doc = json.loads((path / "trace.json").read_text())
        z = np.load(path / "trace.npz")
        if "state_hash_free" not in z.files:
            raise ValueError("%s predates the two-footprint trace: re-trace it" % path)
        t = cls(
            meta=doc["meta"],
            image_pre=z["image_pre"].tobytes(),
            image_post_init=z["image_post_init"].tobytes(),
            state_hash=z["state_hash"],
            footprint_size=z["footprint_size"],
            state_hash_free=z["state_hash_free"],
            footprint_free=z["footprint_free"],
        )
        for pc, op, fixed, count, ph, variants, idx, rd, wr in doc["sites"]:
            t.sites[(pc, op, tuple(fixed))] = {
                "pc": pc,
                "opcode": op,
                "count": count,
                "phases": ph,
                "variants": [bytes(v) for v in variants],
                "idx": idx,
                "reads": {i: set(a) for i, a in rd},
                "writes": {i: set(a) for i, a in wr},
            }
        t.edges = {(f, o, tt): [k, n] for f, o, tt, k, n in doc["edges"]}
        t.calls = {
            (p, o): {"targets": Counter(dict(tg)), "ret_pc": r, "count": c}
            for p, o, r, c, tg in doc["calls"]
        }
        t.rets = {
            (p, o): {
                "matched": Counter(dict(m)),
                "targets": Counter(dict(tg)),
                "unmatched": u,
                "loose": Counter(dict(lo)),
            }
            for p, o, u, m, tg, lo in doc["rets"]
        }
        t.summaries = {k: {"rd": a, "wr": b, "count": c} for k, a, b, c in doc["summaries"]}
        t.inputs = [tuple(x) for x in doc["inputs"]]
        t.input_sites = {
            (a, b): {"kind": k, "count": n, "phase": p} for a, b, k, n, p in doc["input_sites"]
        }
        t.init_writes = [tuple(x) for x in doc["init_writes"]]
        t.written_init = set(doc["written_init"])
        t.written_play = set(doc["written_play"])
        chip = doc.get("chip_ops")
        t.chip_ops = None if chip is None else set(map(tuple, chip))
        t.cells = set(doc["cells"])
        t.code = set(doc.get("code", ()))
        t.cell_values = {a: set(v) for a, v in doc["cell_values"]}
        t.jsr_targets = set(doc["jsr_targets"])
        t.wlog = {k[5:]: z[k] for k in z.files if k.startswith("wlog_")}
        t.iolog = {k[6:]: z[k] for k in z.files if k.startswith("iolog_")}
        t.nmilog = {k[7:]: z[k] for k in z.files if k.startswith("nmilog_")}
        return t


def rekey(trace, cells, out):
    """Merge ``trace``'s sites into ``out`` under the wider cell set ``cells``.

    A wider cell set only blanks more operand bytes, so keys can merge but never
    split: every variant of one key still maps to one key.
    """
    for s in trace.sites.values():
        k = site_key(s["pc"], s["opcode"], s["variants"][0], cells)
        d = out.get(k)
        if d is None:
            d = out[k] = {
                "pc": s["pc"],
                "opcode": s["opcode"],
                "count": 0,
                "phases": 0,
                "variants": [],
                "idx": set(),
                "reads": {},
                "writes": {},
            }
        d["count"] += s["count"]
        d["closed"] = d.get("closed", False) or s.get("closed", False)
        d["phases"] |= s["phases"]
        d["variants"] += s["variants"]
        d["idx"].update(s["idx"])
        for name in ("reads", "writes"):
            for i, a in s[name].items():
                d[name].setdefault(i, set()).update(a)
    return out


def _counters(dst, src, keys):
    for k in keys:
        dst[k].update(src[k])


def merge(traces):
    """One :class:`Trace` over every subtune: shared code, subtune 0's logs.

    Sites, edges, calls, returns and the written sets are the union; the write log,
    inputs and state hashes stay the first trace's, because verification runs each
    subtune against its own trace.
    """
    first = traces[0]
    code = set().union(*(t.code for t in traces))
    written_init = set().union(*(t.written_init for t in traces))
    written_play = set().union(*(t.written_play for t in traces))
    chip = (
        None
        if any(t.chip_ops is None for t in traces)
        else set().union(*(t.chip_ops for t in traces))
    )
    cells = code & (written_init | written_play)
    jsr = set().union(*(t.jsr_targets for t in traces))
    out = Trace(
        meta={**first.meta, "songs_traced": [t.meta["song"] for t in traces]},
        image_pre=first.image_pre,
        image_post_init=first.image_post_init,
        inputs=first.inputs,
        init_writes=first.init_writes,
        written_init=written_init,
        written_play=written_play,
        chip_ops=chip,
        cells=cells,
        code=code,
        jsr_targets=jsr,
        wlog=first.wlog,
        iolog=first.iolog,
        nmilog=first.nmilog,
        state_hash=first.state_hash,
        footprint_size=first.footprint_size,
        state_hash_free=first.state_hash_free,
        footprint_free=first.footprint_free,
    )
    for t in traces:
        rekey(t, cells, out.sites)
        for e, (kind, n) in t.edges.items():
            hit = out.edges.get(e)
            out.edges[e] = [
                kind if hit is None else _edge_kind(hit[0], kind),
                n + (hit[1] if hit else 0),
            ]
        for k, v in t.calls.items():
            d = out.calls.setdefault(k, {"targets": Counter(), "ret_pc": v["ret_pc"], "count": 0})
            d["targets"].update(v["targets"])
            d["count"] += v["count"]
        for k, v in t.rets.items():
            d = out.rets.setdefault(
                k,
                {"matched": Counter(), "targets": Counter(), "unmatched": 0, "loose": Counter()},
            )
            _counters(d, v, ("matched", "targets", "loose"))
            d["unmatched"] += v["unmatched"]
        for k, v in t.summaries.items():
            d = out.summaries.setdefault(k, {"rd": 0, "wr": 0, "count": 0})
            d["rd"] |= v["rd"]
            d["wr"] |= v["wr"]
            d["count"] += v["count"]
        for k, v in t.input_sites.items():
            d = out.input_sites.setdefault(k, {"kind": v["kind"], "count": 0, "phase": 0})
            d["count"] += v["count"]
            d["phase"] |= v["phase"]
        for a, vs in t.cell_values.items():
            if a in cells:
                out.cell_values.setdefault(a, set()).update(vs)
    for s in out.sites.values():
        s["idx"] = sorted(s["idx"])
        s["variants"] = sorted(set(s["variants"]))
    for (_f, _o, t), e in out.edges.items():
        if t in jsr and e[0] in ("fall", "br_taken", "br_not", "jmp"):
            e[0] = "tail"
    return out


def _edge_kind(a, b):
    """The edge kind two subtunes agree on: a tail entry wins over a plain jump."""
    return a if a == b else ("tail" if "tail" in (a, b) else a)
