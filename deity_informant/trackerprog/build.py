"""B6/B7 -- the trackerprog lifted from a tune's certified artefacts.

The schedule of :mod:`.schedule`, the lowering of :mod:`.lower` and the score
the fetch regions read, assembled into the one object ``universal.py`` renders.
What the lift cannot derive it takes from a named hint, one datum a line.
"""

from __future__ import annotations

import json

from ..tuneprog import accum, pipeline, provenance
from ..tuneprog.graph import succs
from ..tuneprog.history import history
from ..tuneprog.ir import Bin, Const, Let, Load, Store, Tuneprog, Var
from ..tuneprog.irwalk import addr_split
from ..tuneprog.recover import Names
from . import lift as t2lift
from .universal import CHIP, REG

EXTERNAL = ("raster", "cia", "sid_readback", "io")


def read(out):
    """The certified artefacts of one output directory, and the presentation view."""
    prog = Tuneprog.load(out / "tuneprog.S4.json")
    s6 = json.loads((out / "tuneprog.S6.json").read_text())
    view, _st, _n = pipeline.present(prog)
    return {
        "prog": prog,
        "view": view,
        "names": Names.from_dict(s6),
        "t0": json.loads((out / "tuneprog.T0.json").read_text()),
        "t1": json.loads((out / "tuneprog.T1.json").read_text()),
        "t2": json.loads((out / "tuneprog.T2.json").read_text()),
        "cert": json.loads((out / "certificate.json").read_text()),
    }


def artefacts(prog, trace, cert, calls=None):
    """The T0/T1/T2 planes of one certified program, computed rather than read."""
    view, st, names = pipeline.present(prog)
    hist, ver = history(prog, trace, names.to_dict(), calls=calls, obs=True)
    t0 = provenance.document(view, st, names)
    t1 = accum.document(view, names, t0, hist, cert, obs=ver.obs)
    return {
        "prog": prog,
        "view": view,
        "names": names,
        "t0": t0,
        "t1": t1,
        "t2": t2lift.document(view, names, hist, cert),
        "cert": cert,
    }


def registers():
    """``{offset from the chip's own base: register name}``: its own columns.

    A store names its register by the address it stands at and not by the region
    it lands in: a family that writes the whole file through one indexed store
    has one region for all of it, and the seven of a voice are the offsets the
    voice map moves (section 3.1).
    """
    off = {v: k for k, v in REG.items()}
    off.update({v: k for k, v in CHIP.items() if "." not in k})
    return off


def divider_rate(store, low, img):
    """``rate``: the tick-level counter's reload, plus the step it takes."""
    v = low.expand(store.v)
    if type(v) is Load:
        base, idx = addr_split(v.a)
        if base is not None and idx is None:
            return int(img[base]) + 1
    return int(v.v) + 1 if type(v) is Const else 1


def divider_phase(img, addr, reload_, rate):
    """The residue class the tick-level counter admits the row clock on."""
    d = int(img[addr])
    for t in range(rate * 4):
        d = reload_ if (d - 1) & 0x80 else (d - 1) & 0xFF
        if d == reload_:
            return t % rate
    return 0


def pinned_inputs(prog, img):
    """``({address: value}, refusals)``: the tick's pinned reads as data (section 8).

    ``ack``, ``entry_reg`` and ``uninit_ram`` are never external, so the byte the
    post-init image holds is the value the run reads; one of the four external
    kinds is a refusal by name, with the site S4 records it at.
    """
    out, bad = {}, []
    for site, addr, kind, _reads, _phase in prog.inputs:
        if addr >= 0x10000:
            continue
        if kind in EXTERNAL:
            bad.append(("$%04X" % addr, "$%04X" % site, kind))
        else:
            out[addr] = int(img[addr])
    return out, bad


def prune(obj):
    """``state0`` held to the cells the object reads or writes: a dead seed is no cell."""
    live = set(obj["meta"].get("wide", ())) | {obj["meta"]["tempo"]["cell"]}
    live |= {a["cell"].lstrip("#") for a in obj["accs"].values()}
    for st in obj["streams"].values():
        for r in st["rows"]:
            live |= {s[0].lstrip("@#!*") for s in r.get("sets", ())}
    live |= _reads([obj["streams"], obj["accs"], obj["score"], obj["meta"]])
    for r in (obj["state0"].get("prologue") or {}).get("rows", ()):
        live |= {s[0].lstrip("@#!*") for s in r.get("sets", ())} | _reads(r)
    live = {n.split(".")[0] for n in live}
    obj["state0"]["cells"] = {k: v for k, v in obj["state0"]["cells"].items() if k in live}
    obj["state0"]["globals"] = {k: v for k, v in obj["state0"]["globals"].items() if k in live}
    return obj


def _reads(node):
    """Every cell and global one part of the object reads, by name."""
    out, stack = set(), [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("cell", "global") and isinstance(v, str):
                    out.add(v)
                elif k == "cell":
                    out.add(v[0])
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _cellnames(node):
    """Every cell one lowered expression reads."""
    out, stack = set(), [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "cell" and isinstance(v, str):
                    out.add(v)
                elif k == "cell":
                    out.add(v[0])
                else:
                    stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack += list(x)
    return out


def _chase(src, e):
    """One name followed through its copies to the value it is."""
    seen = 0
    while type(e) is Var and e.n in src and seen < 8:
        e, seen = src[e.n], seen + 1
    return e


def _step_of(src, e):
    """The constant a loop index moves by a turn, where its latch states one."""
    e = _chase(src, e)
    if type(e) is Bin and e.op in ("+", "-") and type(e.b) is Const:
        return -e.b.v if e.op == "-" else e.b.v
    return None


def voice_order(p, head, latches, vidx, n, stride=1):
    """The order one pass over the voices takes: the index, its start and its step.

    A voice's copies stand ``stride`` apart, so the pass visits voice
    ``(start + j * step) / stride``; the index is the name whose latch and
    pre-header state a start and a step every voice is a turn of.
    """
    src = {x.n: x.e for b in p.blocks.values() for x in b.stmts if type(x) is not Store}
    steps = {}
    for lbl in sorted(latches):
        for x in p.blocks[lbl].stmts:
            got = _step_of(src, x.e) if type(x) is Let and x.n in vidx else None
            if got:
                steps[x.n] = got
    for lbl, b in p.blocks.items():
        if lbl in latches or head not in succs(b.term):
            continue
        for x in b.stmts:
            if getattr(x, "n", None) not in steps:
                continue
            e = _chase(src, x.e)
            if type(e) is not Const:
                continue
            got = _order(e.v, steps[x.n], n, stride)
            if got is not None:
                return got
    return list(range(n - 1, -1, -1))


def _order(start, step, n, stride):
    """The voices one pass visits, where a start and a step name each of them once."""
    if start % stride or step % stride:
        return None
    got = [((start + j * step) & 0xFF) // stride for j in range(n)]
    return got if sorted(got) == list(range(n)) else None


def table_streams(voc, img):
    """The const tables the lowering read at a cell, each a stream of its own bytes."""
    return {
        name: {"rows": [{"b": int(img[a])} for a in range(base, top + 1)]}
        for name, (base, top) in sorted(voc.tables.items())
    }
