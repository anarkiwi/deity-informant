"""Per-cell storage census over an instrumented state image (docs/frameprog.md 7.10.13).

The evaluator runs against a recording state image: per-cell reads and writes
(net of the ``frameval.py:535`` write echo), first-access kind per frame, verdicts
per declared state field in both units, and the top-address/read-back site lists.
"""

import argparse
import json
import multiprocessing as mp
import re
import signal
import sys
import time
from collections import Counter, defaultdict
from functools import partial
from pathlib import Path

import _sweep
import fuse_measure
import lift_residue

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

SID_LO, SID_HI = 0xD400, 0xD41C  # the register span excluded from cell classes
ECHO_HI = 0xD418  # frameval re-reads a just-stored cell to buffer a SID write
RB_HI = 0xD416  # last write-only register a pair's read-back can land on
SCRATCH = frozenset(("framelocal", "writeonly", "data"))

USAGE = """\
  python tools/storage_census.py                                # the whole cache
  python tools/storage_census.py --tunes Hubbard_Rob/Commando --frames full
  python tools/storage_census.py --frames 1500 -o out/storage_census.json"""

_CELL = re.compile(r"_([0-9A-Fa-f]{2,4})(_lo|_hi)?$")


class Image(bytearray):
    """A state image recording reads, writes, and first access kind per frame."""

    def __init__(self, src):
        super().__init__(src)
        self.frame = 0
        self.first = defaultdict(dict)
        self.reads = defaultdict(int)
        self.writes = defaultdict(int)
        self.wframes = defaultdict(set)

    def __getitem__(self, a):
        if isinstance(a, int):
            self.reads[a] += 1
            self.first[a].setdefault(self.frame, "r")
        return bytearray.__getitem__(self, a)

    def __setitem__(self, a, v):
        if isinstance(a, int):
            self.writes[a] += 1
            self.first[a].setdefault(self.frame, "w")
            self.wframes[a].add(self.frame)
        bytearray.__setitem__(self, a, v)


def build(entry, frames=None):
    """``(model, prog, nframes)``: the tune as ``lift_triage`` builds it.

    ``frames`` caps the decompile observation length as the doc's probe did;
    ``None`` is the full Songlengths length, bit-identical to the other tools'
    build (the Phase 0 text gate compares the two at equal length)."""
    from deity_informant import frameprog
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
    nframes = int(secs * 50) if frames is None else min(int(secs * 50), frames)
    model, _ev = S.decompile(mem, init, play, nframes, sub)
    return model, frameprog.program(model), nframes


def evaluate(model, prog, nframes, probe=None):
    """``(image, frames run, fault or None)``: the instrumented evaluation.

    ``probe`` is Phase 2b (b0)'s address observer: the run already resolves every
    deref concretely, so recording where each web's derefs landed costs one set
    add per deref and nothing at all at a site carrying no root."""
    from deity_informant import frameprog, frameval

    trace, _walker = frameprog.iota(model, nframes)
    ev = frameval.Evaluator(prog, trace, probe=probe)
    image = Image(ev.m)
    ev.m = image
    fault, ran = None, 0
    for f in range(nframes):
        ev.frame = f
        image.frame = f
        try:
            ev.run_frame()
        except frameval.FrameFault as exc:
            fault = "frame %d: %s" % (f, exc)
            break
        ran = f + 1
    return image, ran, fault


def cell_classes(image):
    """Per-cell storage class from the recorded accesses, SID cells excluded.

    ``persistent`` means some frame's first access is a read with a write in an
    earlier frame: the cell carried a value across the interrupt boundary. The
    first-access kinds are echo-immune, since the write lands first."""
    cls = {}
    for a in set(image.reads) | set(image.writes):
        if SID_LO <= a <= SID_HI:
            continue
        r, w = image.reads.get(a, 0), image.writes.get(a, 0)
        if w == 0:
            cls[a] = "data"
        elif r == 0:
            cls[a] = "writeonly"
        else:
            carried = any(
                kind == "r" and any(wf < f for wf in image.wframes[a])
                for f, kind in image.first[a].items()
            )
            cls[a] = "persistent" if carried else "framelocal"
    return cls


def rendered_fields(text):
    """Rendered ``state { }`` fields as ``{name: (base cell, width)}``.

    The doc's declared-field unit: only address-named fields map to cells, so
    aliased and flag names fall out exactly as the 7.10.13 probe dropped them."""
    out, in_state = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s == "state {":
            in_state = True
        elif in_state:
            if s == "}":
                break
            name, width = s.split(":", 1)
            try:
                base = int(name.strip().rsplit("_", 1)[-1], 16)
            except ValueError:
                continue
            out[name.strip()] = (base, 2 if width.strip() == "u16" else 1)
    return out


def field_cells(name, width):
    """Cells of a ``prog.state`` entry from its canonical name, else None."""
    m = _CELL.search(name)
    if m is None:
        return None
    base = int(m.group(1), 16) + (1 if m.group(2) == "_hi" else 0)
    return range(base, base + width)


def field_verdict(cls, cells):
    """One field's verdict from its cells' classes, mixed verdicts spelled out."""
    verdicts = {cls.get(a, "untouched") for a in cells}
    if "persistent" in verdicts:
        return "persistent"
    if "framelocal" in verdicts:
        return "framelocal"
    return "/".join(sorted(verdicts))


def top_sites(prog):
    """Unnamed ``st``/``asg`` access sites past $FF, split at the stack bound.

    ``top`` is a G1-resolved ``addr_bits`` above $01FF, ``stack`` the $0100-
    $01FF band; each record carries its pointer-root cells and a top store
    carries its ``fuse_measure.wide_class`` shape."""
    from deity_informant import frameproc

    out = {"load_top": [], "load_stack": [], "store_top": [], "store_stack": []}

    def record(addr, at, kind):
        bits = frameproc.addr_bits(addr, at)
        if bits <= 0xFF:
            return
        bucket = "top" if bits > 0x1FF else "stack"
        rec = {"addr": frameproc._fmt(addr), "bits": "$%04X" % bits}
        roots = sorted(set(fuse_measure.root_cells(addr)))
        if roots:
            rec["roots"] = ["$%04X" % c for c in roots]
        if kind == "store" and bucket == "top":
            rec["shape"] = fuse_measure.wide_class(addr)
        out["%s_%s" % (kind, bucket)].append(rec)

    def scan(n, at):
        stack = [n]
        while stack:
            x = stack.pop()
            if not isinstance(x, tuple):
                continue
            if x[0] == "mem":
                if frameproc.addr_split(x[1])[0] is None and x[1] not in prog.resolved:
                    record(x[1], at, "load")
                stack.append(x[1])
            elif x[0] == "op":
                stack.extend(x[2])

    def walk(stmts, outer, cyclic):
        env = frameproc.Defs(stmts, outer, cyclic)
        for k, s in enumerate(stmts):
            for body in frameproc._stmt_bodies(s):
                walk(body, (env, k), s[0] in frameproc._CYCLIC)
            at = frameproc.DefsAt(env, k)
            if s[0] == "st":
                if frameproc.addr_split(s[1])[0] is None and s[1] not in prog.resolved:
                    record(s[1], at, "store")
                scan(s[2], at)
                scan(s[1], at)
            elif s[0] == "asg":
                scan(s[2], at)

    for _e, _p, _r, stmts in prog.procs:
        walk(stmts, None, False)
    return out


def census_roots(tops):
    """``ptr_roots`` as this instrument derives it: the roots of the top loads."""
    return sorted({c for rec in tops["load_top"] for c in rec.get("roots", ())})


def top_roots(prog):
    """The pointer roots of the top-wide loads, read off ``ptrcert``'s own walk.

    The same population ``ptr_roots`` names, derived by the Phase 2a analysis
    rather than by this file: where the two disagree the row says so, which is
    the "two instruments agree store for store" discipline for roots."""
    from deity_informant import ptrcert

    per, _loose = ptrcert.sites(prog)
    return sorted("$%04X" % c for c, n in per.items() if n.get("load"))


def certification(model, prog):
    """Phase 2a's per-root block-rooting record for one program (read-only)."""
    from deity_informant import ptrcert, streams

    return ptrcert.summary(prog, streams.classify(model))


def readback_sites(prog):
    """Static loads of a write-only SID register: ``_widen``'s RMW emission."""
    from deity_informant import frameproc

    sites = []

    def scan(n):
        stack = [n]
        while stack:
            x = stack.pop()
            if not isinstance(x, tuple):
                continue
            if x[0] == "mem":
                base, _idx = frameproc.addr_split(x[1])
                if base is None and x[1][0] == "const":
                    base = x[1][1]
                if base is not None and SID_LO <= base <= RB_HI:
                    sites.append(frameproc._fmt(x))
                stack.append(x[1])
            elif x[0] == "op":
                stack.extend(x[2])

    def walk(stmts):
        for s in stmts:
            for body in frameproc._stmt_bodies(s):
                walk(body)
            if s[0] == "st":
                scan(s[1])
                scan(s[2])
            elif s[0] == "asg":
                scan(s[2])

    for _e, _p, _r, stmts in prog.procs:
        walk(stmts)
    return sites


def crossproc(prog, cells):
    """``{cell: procedure count}`` for the cells at least two procedures touch.

    Static plain named accesses only (loads and stores, both widths): an
    indexed row access names its table, not the cell a row lands on."""
    from deity_informant import frameproc
    from deity_informant import grammar as G

    want = set(cells)
    per = defaultdict(set)

    def scan(n, acc):
        stack = [n]
        while stack:
            x = stack.pop()
            if not isinstance(x, tuple):
                continue
            if x[0] == "mem":
                base, idx = frameproc.addr_split(x[1])
                if base is not None and idx is None:
                    acc.update(range(base, base + x[2]))
                stack.append(x[1])
            elif x[0] == "op":
                stack.extend(x[2])

    def walk(stmts, acc):
        for s in stmts:
            for body in frameproc._stmt_bodies(s):
                walk(body, acc)
            if s[0] == "st":
                base, idx = frameproc.addr_split(s[1])
                if base is not None and idx is None:
                    acc.update(range(base, base + G.store_width(s[2])))
                scan(s[2], acc)
                scan(s[1], acc)
            elif s[0] == "asg":
                scan(s[2], acc)

    for entry, _p, _r, stmts in prog.procs:
        acc = set()
        walk(stmts, acc)
        for a in acc & want:
            per[a].add(entry)
    return {a: len(es) for a, es in per.items() if len(es) > 1}


def work_list(cert, ext):
    """Phase 2b (b5): the webs rung (g) may rewrite, and the sites they carry.

    The lift's own column (b1) intersected with an observed extent the registry
    maps (b0): a web whose derefs left the declared data keeps today's spelling,
    so it is no target however well its definitions spell."""
    from deity_informant import ptrextent

    mapped = ptrextent.mapped_cells(ext["records"])
    fit = [r for r in cert["records"] if r["eligible"] and int(r["root"][1:], 16) in mapped]
    return {
        "webs": len(fit),
        "loads": sum(r["top_loads"] for r in fit),
        "stores": sum(r["top_stores"] for r in fit),
        "blocks": sum(
            len(e["blocks"]) for e in ext["records"] if e["root"] in {r["root"] for r in fit}
        ),
        "roots": [r["root"] for r in fit],
    }


def census(model, prog, nframes):
    """One tune's storage row from an in-memory build: the testable core."""
    from deity_informant import frameprog, ptrextent

    probe = ptrextent.Probe()
    image, ran, fault = evaluate(model, prog, nframes, probe)
    cls = cell_classes(image)
    decl = rendered_fields(frameprog.dumps(prog))
    decl_verdict = {n: field_verdict(cls, range(b, b + w)) for n, (b, w) in decl.items()}
    fields = {}
    for f in prog.state:
        cells = field_cells(f[0], f[1])
        fields[f[0]] = "unparsed" if cells is None else field_verdict(cls, cells)
    by_region = defaultdict(lambda: defaultdict(int))
    for a, c in cls.items():
        by_region[c]["zp" if a < 0x100 else "stack" if a < 0x200 else "ram"] += 1
    gross = {a: n for a, n in image.reads.items() if SID_LO <= a <= ECHO_HI}
    net = {a: n - image.writes.get(a, 0) for a, n in gross.items()}
    net = {a: n for a, n in net.items() if n > 0}
    tops = top_sites(prog)
    rbs = readback_sites(prog)
    dyn, swgs = lift_residue.dyn_counts(prog.procs)
    locals_ = sorted(a for a, c in cls.items() if c == "framelocal")
    xp = crossproc(prog, locals_)
    row = {
        "frames": ran,
        "state_fields": len(prog.state),
        "field_verdicts": dict(Counter(fields.values())),
        "state_decl": len(decl),
        "decl_verdicts": dict(Counter(decl_verdict.values())),
        "scratch": sum(1 for v in decl_verdict.values() if v in SCRATCH),
        "persistent": sum(1 for v in decl_verdict.values() if v == "persistent"),
        "untouched": sum(1 for v in decl_verdict.values() if v == "untouched"),
        "decl_verdict": decl_verdict,
        "cells_touched": len(cls),
        "by_class_region": {c: dict(v) for c, v in by_region.items()},
        "framelocal_cells": len(locals_),
        "crossproc": {"$%04X" % a: n for a, n in sorted(xp.items())},
        "top_loads": len(tops["load_top"]),
        "stack_loads": len(tops["load_stack"]),
        "top_stores": len(tops["store_top"]),
        "stack_stores": len(tops["store_stack"]),
        "top_load_sites": tops["load_top"],
        "top_store_sites": tops["store_top"],
        "ptr_roots": census_roots(tops),
        "wide_classes": dict(Counter(rec["shape"] for rec in tops["store_top"])),
        "readback_sites": len(rbs),
        "readback": rbs,
        "sid_gross_reads": sum(gross.values()),
        "sid_net_reads": sum(net.values()),
        "sid_net_regs": len(net),
        "dyn_stmts": dyn,
        "switch_gotos": swgs,
    }
    cert = certification(model, prog)
    row["cert"] = cert
    row["cert_agree"] = row["ptr_roots"] == top_roots(prog)
    row["extents"] = ptrextent.summary(ptrextent.extents(prog, probe.hits), ran)
    row["work_list"] = work_list(cert, row["extents"])
    if fault is not None:
        row["eval_fault"] = fault
    return row


def one(entry, frames):
    """One tune's census row, or the exception that stopped it."""
    try:
        signal.alarm(_sweep.CAP_S)
        t0 = time.monotonic()
        model, prog, nframes = build(entry, frames)
        row = {**_sweep.row_head(entry), "build_s": round(time.monotonic() - t0, 1)}
        row.update(census(model, prog, nframes))
        return row
    except Exception as exc:  # pylint: disable=broad-except
        return {**_sweep.row_head(entry), "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _cert_totals(done):
    """Phase 2a's coverage: roots certified, what each premise cost, refusals."""
    from deity_informant import ptrcert

    ref, prem, shapes, kinds, loose = Counter(), Counter(), Counter(), Counter(), Counter()
    for r in done:
        c = r["cert"]
        ref.update(c["refusals"])
        prem.update(c["premises"])
        shapes.update(c["shapes"])
        kinds.update(c["def_kinds"])
        loose.update(c["unrooted"])
    have = [r for r in done if r["cert"]["roots"]]

    def per(k):
        return sum(r["cert"][k] for r in done)

    return {
        "cert_roots": per("roots"),
        "cert_block_rooted": per("block_rooted"),
        "cert_ready": per("cursor_ready"),
        "cert_top_loads": per("top_loads"),
        "cert_rooted_loads": per("rooted_loads"),
        "cert_ready_loads": per("ready_loads"),
        "cert_top_stores": per("top_stores"),
        "cert_rooted_stores": per("rooted_stores"),
        "cert_ready_stores": per("ready_stores"),
        "cert_unrooted": dict(loose),
        "cert_def_kinds": {k: kinds[k] for k in ptrcert.KINDS},
        "cert_premises": dict(prem),
        "cert_shapes": dict(shapes),
        "cert_refusals": dict(ref),
        "cert_refusal_tunes": {
            k: sum(1 for r in done if k in r["cert"]["refusals"]) for k in ptrcert.REFUSALS
        },
        "tunes_with_roots": len(have),
        "tunes_all_block_rooted": sum(
            1 for r in have if r["cert"]["block_rooted"] == r["cert"]["roots"]
        ),
        "tunes_all_ready": sum(1 for r in have if r["cert"]["cursor_ready"] == r["cert"]["roots"]),
        "cert_disagree": sorted(r["tune"] for r in done if not r["cert_agree"]),
        **_lift_totals(done),
    }


def _lift_totals(done):
    """Phase 2b's two columns: b1's eligibility, b0's extents, b5's work list."""
    from deity_informant import ptrcert, ptrextent

    lift, alone, prem, defs, held = Counter(), Counter(), Counter(), Counter(), Counter()
    ext = Counter()
    for r in done:
        lift.update(r["cert"]["lift_refusals"])
        alone.update(r["cert"]["lift_alone"])
        prem.update(r["cert"]["lift_premises"])
        defs.update(r["cert"]["lift_defs"])
        held.update(r["cert"]["held_writers"])
        ext.update(r["extents"]["refusals"])
    return {
        "lift_eligible": sum(r["cert"]["eligible"] for r in done),
        "lift_eligible_loads": sum(r["cert"]["eligible_loads"] for r in done),
        "lift_eligible_stores": sum(r["cert"]["eligible_stores"] for r in done),
        "lift_refusals": {k: lift[k] for k in ptrcert.LIFT_REFUSALS if lift[k]},
        "lift_alone": {k: alone[k] for k in ptrcert.LIFT_REFUSALS if alone[k]},
        "lift_premises": dict(prem),
        "lift_defs": dict(defs),
        "lift_held_writers": dict(held),
        "lift_refusal_tunes": {
            k: sum(1 for r in done if k in r["cert"]["lift_refusals"])
            for k in ptrcert.LIFT_REFUSALS
        },
        "extent_webs": sum(r["extents"]["webs"] for r in done),
        "extent_observed": sum(r["extents"]["webs_observed"] for r in done),
        "extent_mapped": sum(r["extents"]["webs_mapped"] for r in done),
        "extent_via": sum(r["extents"]["webs_via"] for r in done),
        "extent_blocks": sum(r["extents"]["blocks"] for r in done),
        "extent_addrs": sum(r["extents"]["addrs"] for r in done),
        "extent_unmappable_addrs": sum(r["extents"]["unmappable_addrs"] for r in done),
        "extent_unmappable_short": sum(r["extents"]["unmappable_short"] for r in done),
        "extent_webs_short": sum(r["extents"]["webs_short"] for r in done),
        "extent_overshoot": max((r["extents"]["overshoot"] for r in done), default=0),
        "extent_refusals": {k: ext[k] for k in ptrextent.REFUSALS},
        "extent_refusal_tunes": {
            k: sum(1 for r in done if r["extents"]["refusals"].get(k)) for k in ptrextent.REFUSALS
        },
        "work_webs": sum(r["work_list"]["webs"] for r in done),
        "work_loads": sum(r["work_list"]["loads"] for r in done),
        "work_stores": sum(r["work_list"]["stores"] for r in done),
        "work_blocks": sum(r["work_list"]["blocks"] for r in done),
        "work_tunes": sum(1 for r in done if r["work_list"]["webs"]),
    }


def _totals(done):
    """The corpus totals the plan's gates read, merged over clean rows."""
    wide, roots = Counter(), Counter()
    for r in done:
        wide.update(r["wide_classes"])
        roots.update(r["ptr_roots"])
    return {
        **_cert_totals(done),
        "state_decl_total": sum(r["state_decl"] for r in done),
        "scratch_total": sum(r["scratch"] for r in done),
        "persistent_total": sum(r["persistent"] for r in done),
        "untouched_total": sum(r["untouched"] for r in done),
        "tunes_zero_scratch": sum(1 for r in done if not r["scratch"]),
        "framelocal_cell_total": sum(r["framelocal_cells"] for r in done),
        "crossproc_cell_total": sum(len(r["crossproc"]) for r in done),
        "top_load_total": sum(r["top_loads"] for r in done),
        "stack_load_total": sum(r["stack_loads"] for r in done),
        "top_store_total": sum(r["top_stores"] for r in done),
        "stack_store_total": sum(r["stack_stores"] for r in done),
        "wide_class_total": dict(wide),
        "readback_site_total": sum(r["readback_sites"] for r in done),
        "readback_tunes": sum(1 for r in done if r["readback_sites"]),
        "sid_net_read_total": sum(r["sid_net_reads"] for r in done),
        "dyn_stmt_total": sum(r["dyn_stmts"] for r in done),
        "switch_goto_total": sum(r["switch_gotos"] for r in done),
        "tunes_with_dyn": sum(1 for r in done if r["dyn_stmts"]),
        "tunes_with_switch_goto": sum(1 for r in done if r["switch_gotos"]),
        "eval_faults": sorted(r["tune"] for r in done if "eval_fault" in r),
    }


def artifact(out):
    """Phase 2b (b0)'s artifact: per tune, the horizon and every web's extent."""
    return {
        "frames": out["frames"],
        "tunes": out["tunes"],
        "rows": [
            {"tune": r["tune"], "name": r["name"], "extents": r["extents"]}
            for r in out["rows"]
            if "extents" in r
        ],
    }


def worklist_lines(done, cap=20):
    """Phase 2b (b5): the measured target, per tune, printed before rung (g) exists."""
    rows = [r for r in done if r["work_list"]["webs"]]
    total = sum(r["work_list"]["webs"] for r in rows)
    out = ["", "work list (b5): %d web(s) over %d tune(s)" % (total, len(rows))]
    if len(rows) > cap:
        return out + ["  per-tune rows in the artifact; rerun with --tunes to print them"]
    for r in rows:
        w = r["work_list"]
        out.append(
            "  %-46s %2d web(s) %4d load(s) %2d store(s)  %s"
            % (r["tune"], w["webs"], w["loads"], w["stores"], ", ".join(w["roots"]))
        )
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("--frames", default="1500", help="frame cap, or 'full' (default 1500)")
    ap.add_argument("-j", "--procs", type=int, default=32)
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "storage_census.json"))
    ap.add_argument(
        "--extents",
        default=str(ROOT / "out" / "ptr_extents.json"),
        help="Phase 2b (b0)'s observed-extent artifact, keyed and horizoned per tune",
    )
    args = ap.parse_args()

    frames = None if args.frames == "full" else int(args.frames)
    tunes = _sweep.entries(args.tunes.split(",") if args.tunes else None)
    if not tunes:
        sys.exit("no cached tune matched")
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), args.procs), _sweep.arm) as pool:
        rows = _sweep.check_rows(pool.map(partial(one, frames=frames), tunes))
    done = [r for r in rows if "error" not in r]
    out = {
        "tunes": len(done),
        "refused": [r for r in rows if "error" in r],
        "wall_s": round(time.monotonic() - t0, 1),
        "frames": args.frames,
        **_totals(done),
        "rows": rows,
    }
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    Path(args.extents).write_text(json.dumps(artifact(out), indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("rows", "refused")}, indent=1))
    for line in worklist_lines(done):
        print(line)
    print("%d refused" % len(out["refused"]))


if __name__ == "__main__":
    main()
