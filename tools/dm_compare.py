"""Compare the DefMON oracle with the tracker's own recovery.

DefMON tunes are found in the cached .sids by their replay signature, mapped,
checked against the tracker's law on the frameprog boundary and compared on three
axes. Reuses tools/gt_compare.py's axes. Writes out/dm_compare.json.
"""

import json
import multiprocessing as mp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
HVSC = ROOT / ".oracle-cache" / "hvsc"
OUT = ROOT / "out" / "dm_compare.json"
FRAMES = 200
WORKERS = 5

# a RAW lane -> the deficiency it reports (docs/dm-oracle.md §4)
_DEFECT = {
    ("slide", "detune"): "relative",
    ("note", "tr"): "relative",
    ("res", "routing"): "two_generators",
    ("xor", "mask"): "two_generators",
    ("mode", "vol"): "two_generators",
    ("sweep", "pwlo"): "wide_accumulator",
    ("sweep", "carry"): "wide_accumulator",
    ("cutoff", "slide"): "wide_accumulator",
    ("pw", "step"): "wide_accumulator",
}


def _pair(lane):
    """The ``(kind, name)`` a `Report.raw_kinds` key spells, or ``()``."""
    parts = lane.strip("()").replace("'", "").split(", ")
    return tuple(p for p in parts if p)


def _classify(raw_kinds):
    """Split a strict graph's RAW residual by cause, as docs/gt-oracle.md §4.6 does."""
    out = {
        k: 0 for k in ("relative", "two_generators", "wide_accumulator", "ghost", "order", "rest")
    }
    for key, n in raw_kinds.items():
        kind, _sep, lane = str(key).partition(":")
        pair = _pair(lane)
        if pair[:1] == ("ghost",):
            out["ghost"] += n
        elif pair in _DEFECT and kind in ("raw", "ramp"):
            out[_DEFECT[pair]] += n
        elif kind in ("select", "ctrl"):
            out["order"] += n
        else:
            out["rest"] += n
    return out


def _chains(fetched, dl, jp):
    """Maximal cascade walks: successive fetches of a row and the row it steps to.

    A row is held for ``dl + 1`` frames and then the cascade advances, so a walk's
    own fire gaps are the delay bytes the song declares."""
    at = {}
    for f, row in enumerate(fetched):
        for y in row:
            at.setdefault(y, []).append(f)
    out, used = [], set()
    for f, row in enumerate(fetched):
        for y in row:
            if (f, y) in used:
                continue
            chain, cf, cy = [(f, y)], f, y
            while not dl[cy] & 0x80:
                nf, ny = cf + (dl[cy] & 0x7F) + 1, jp[(cy + 1) & 0xFF]
                if nf not in at.get(ny, ()) or (nf, ny) in used:
                    break
                chain.append((nf, ny))
                cf, cy = nf, ny
            used.update(chain)
            out.append(chain)
    return out


def _cascade(native, fetched, nframes):
    """Does the delay byte generate the cascade's fires, and at `DIV`'s own phase?"""
    dl, jp = native.tables[("clock", "dl")], native.tables[("clock", "jp")]
    seen = fetched[:nframes]
    out = {
        "fetches": sum(len(r) for r in seen),
        "frozen": 0,
        "predicted": 0,
        "delay_holds": 0,
        "chains": 0,
        "periodic": 0,
        "divisor_one": 0,
        "declared_divisor": 0,
        "at_div_phase": 0,
    }
    for f, row in enumerate(seen):
        for y in row:
            if dl[y] & 0x80:
                out["frozen"] += 1
                continue
            nxt = f + (dl[y] & 0x7F) + 1
            if nxt >= len(seen):
                continue
            out["predicted"] += 1
            out["delay_holds"] += int(jp[(y + 1) & 0xFF] in seen[nxt])
    for chain in _chains(seen, dl, jp):
        out["chains"] += 1
        if len(chain) < 3:
            continue
        gaps = {b[0] - a[0] for a, b in zip(chain, chain[1:])}
        if len(gaps) != 1:
            continue
        out["periodic"] += 1
        n = gaps.pop()
        if n < 2:
            out["divisor_one"] += 1
            continue
        out["declared_divisor"] += 1
        out["at_div_phase"] += int(all((f + 1) % n == 0 for f, _y in chain))
    return out


def _masks(native, nframes):
    """The XOR-mask census: how wide the ctrl plane's product table would have to be."""
    seen, gate, other = set(), 0, 0
    for f in range(nframes):
        for v in range(3):
            cell = native.writes[f].get(7 * v + 4)
            if cell is None:
                continue
            if cell.kind == "raw" and cell.lane == ("xor", "mask"):
                other += 1
            elif cell.kind == "ctrl":
                gate += 1
    for y, b in enumerate(native.tables[("sidtab", "xor")]):
        if native.tables[("sidtab", "wave")][y] or b:
            seen.add(b)
    return {"masks": sorted(seen), "gate_only_emits": gate, "wider_mask_emits": other}


def _pitch_axis(ours, native, nframes, off):
    """``(share at the modal offset, that offset, emits)`` for the note lane.

    gt_compare's version reads freq_lo; DefMON's tunes declare freq_hi and not
    freq_lo, so the register the recovery carries a row on is a parameter."""
    diffs = [
        ours[(f, 7 * v + off)] - native.notes[f][v]
        for f in range(nframes)
        for v in range(3)
        if (f, 7 * v + off) in ours and native.notes[f][v] >= 0
    ]
    if not diffs:
        return 0.0, None, 0
    hist = {}
    for d in diffs:
        hist[d] = hist.get(d, 0) + 1
    best = max(hist, key=hist.get)
    return hist[best] / len(diffs), best, len(diffs)


def _instr_axis(ours, native, nframes, off):
    """``(consistency, distinct rows, is a bijection, emits)`` on one instrument plane."""
    import gt_compare as GC  # pylint: disable=import-outside-toplevel,import-error

    pairs = [
        (ours[(f, 7 * v + off)], native.instrs[f][v])
        for f in range(nframes)
        for v in range(3)
        if (f, 7 * v + off) in ours and native.instrs[f][v] >= 0
    ]
    share, size, onto = GC._bijection(pairs)  # pylint: disable=protected-access
    return share, size, onto, len(pairs)


def _best(axis, ours, native, nframes, offs):
    """The register offset the recovery actually carries a row on, and its result."""
    best, key = None, -1
    for off in offs:
        got = axis(ours, native, nframes, off)
        if got[-1] > key:
            best, key = (off,) + got, got[-1]
    return best


def _columns(native):
    """How many sidTAB rows carry each column: the decomposition, measured."""
    return {
        "%s.%s" % lane: sum(1 for b in tab if b)
        for lane, tab in native.tables.items()
        if lane[0] in ("sidtab", "ins", "pw", "filt", "note")
    }


def dm_one(rel):
    """Oracle, recovery and the three axes for one cached DefMON .sid."""
    import gt_compare as GC  # pylint: disable=import-outside-toplevel,import-error

    # pylint: disable=import-outside-toplevel
    from deity_informant import dmoracle, gtoracle, tracker
    from deity_informant.c64 import psid_songs

    path, out = HVSC / rel, {"editor": "defmon", "name": rel}
    try:
        _songs, start = psid_songs(path.read_bytes())
        got = dmoracle.dm_decompile(path, FRAMES, start - 1)
        if got is None:
            out["error"] = "unmapped"
            return out
        native, own, fetched = got
        _g, self_law = gtoracle.strict(native, own, 0)
        prog, trace = GC._lifted(path, start - 1, FRAMES)  # pylint: disable=protected-access
        records = tracker.oracle(prog, trace, FRAMES)
        offset = gtoracle.align(records, native)
        _g2, admitted = gtoracle.graph(records, native, offset)
        _g3, strict = gtoracle.strict(native, records, offset)
        ours_graph, ours_cov = GC._ours(prog, trace, FRAMES)  # pylint: disable=protected-access
    except Exception as exc:  # pylint: disable=broad-except
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:70])
        return out
    rows = {
        (f - offset, r): v
        for (f, r), v in GC._rows(ours_graph, FRAMES).items()  # pylint: disable=protected-access
        if f >= offset
    }
    n = admitted.frames
    preg, share, poff, emits = _best(_pitch_axis, rows, native, n, (0, 1))
    ireg, isha, isize, onto, iemits = _best(_instr_axis, rows, native, n, (5, 6, 4))
    out.update(
        {
            "frames": n,
            "offset": offset,
            "self_matched": self_law.matched,
            "admitted_law": admitted.divergence is None,
            "admitted": GC._cov(admitted.coverage),  # pylint: disable=protected-access
            "strict_matched": strict.matched,
            "strict_law": strict.divergence is None,
            "strict_divergence": None if strict.divergence is None else str(strict.divergence),
            "raw_kinds": {str(k): v for k, v in strict.raw_kinds.items()},
            "defects": _classify(strict.raw_kinds),
            "strict_raw": strict.coverage.residual,
            "ours": GC._cov(ours_cov),  # pylint: disable=protected-access
            "pitch": {"share": share, "offset": poff, "emits": emits, "reg": preg},
            "instruments": {
                "share": isha,
                "rows": isize,
                "bijection": onto,
                "emits": iemits,
                "reg": ireg,
            },
            "structure": dict(
                GC._struct_axis(ours_graph, native, n),  # pylint: disable=protected-access
                **{"song_%s" % k: v for k, v in native.structure.items()},
            ),
            "cascade": _cascade(native, fetched, n),
            "xor": _masks(native, n),
            "columns": _columns(native),
        }
    )
    return out


def defmon_tunes():
    """Every cached ``.sid`` whose image carries the DefMON replay signature."""
    from pydefmon._sid_format import find_signature  # pylint: disable=import-outside-toplevel
    from pysidtracker import SidImage  # pylint: disable=import-outside-toplevel

    out = []
    for path in sorted(HVSC.rglob("*.sid")):
        try:
            if find_signature(SidImage.from_bytes(path.read_bytes()).mem) >= 0:
                out.append(str(path.relative_to(HVSC)))
        except Exception:  # pylint: disable=broad-except
            continue
    return out


def main(argv):
    """Run the DefMON oracle over the cache and write the JSON report."""
    rels = argv[1:] or defmon_tunes()
    print("defmon tunes cached: %d" % len(rels))
    with mp.Pool(WORKERS) as pool:
        res = list(pool.imap_unordered(dm_one, rels, chunksize=1))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1))
    ok = [r for r in res if "error" not in r]
    print("ran %d, mapped %d" % (len(res), len(ok)))
    for r in sorted(ok, key=lambda x: x["name"]):
        cov = r["admitted"]
        print(
            "%-44s law %-5s strict %3d/%-3d self %3d interp %5d/%-5d %5.1f%%"
            % (
                r["name"][-44:],
                r["admitted_law"],
                r["strict_matched"],
                r["frames"],
                r["self_matched"],
                cov["interp"],
                cov["total"],
                100.0 * cov["interp"] / max(1, cov["total"]),
            )
        )


if __name__ == "__main__":
    main(sys.argv)
