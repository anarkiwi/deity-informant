#!/usr/bin/env python3
"""Ground-truth memory layouts of tunes assembled from source: GoatTracker 2's player.

``python tools/gtruth.py [SNG...] [--variant NAME=OPTS] [--out DIR]`` packs each song (default:
the examples bundled in ``di-vtools``) with the patched ``gt2reloc``, whose JSON listing of every
atom and symbol yields ``DIR/<variant>/<song>.truth.json`` (regions, address references).
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections import Counter
from pathlib import Path

from jennings.opcodes import OPCODES
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position
import vtools  # noqa: E402

PLAYER = "goattracker-2.77"
EXAMPLES = "/usr/local/share/gt2/examples"
VARIANTS = {"base": [], "w2000": ["-W20", "-Z80"]}
MODES = {1: "zp", 2: "abs", 4: "imm"}
DATA = {10: 2, 11: 1, 13: 1, 14: 1}


def _cdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": _cdiv,
    "%": lambda a, b: a - _cdiv(a, b) * b,
    "<": lambda a, b: int(a < b),
    ">": lambda a, b: int(a > b),
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "||": lambda a, b: int(bool(a) or bool(b)),
    "&&": lambda a, b: int(bool(a) and bool(b)),
    "neg": lambda a: -a,
    "!": lambda a: int(not a),
}


def ev(e, sym):
    """The value of a listing expression (C ``int`` semantics) under ``{symbol: value}``."""
    if isinstance(e, int):
        return e
    if isinstance(e, str):
        return sym[e]
    return OPS[e[0]](*(ev(a, sym) for a in e[1:]))


def names(e):
    """The symbols an expression references, in order."""
    if isinstance(e, int):
        return []
    if isinstance(e, str):
        return [e]
    return [n for a in e[1:] for n in names(a)]


def role(e, sym, labels):
    """``(role, target, symbol)`` of a byte expression: ``lo``/``hi`` of an address
    (``X % 256``/``X / 256`` with a label in ``X``), ``addr`` (a label), else ``const``."""
    for op, r in (("%", "lo"), ("/", "hi")):
        if isinstance(e, list) and e[0] == op and e[2] == 256:
            hits = [n for n in names(e[1]) if n in labels]
            if hits:
                return r, ev(e[1], sym), hits[0]
    hits = [n for n in names(e) if n in labels]
    return ("addr", ev(e, sym), hits[0]) if hits else ("const", None, None)


def load_listing(lines):
    """``(atoms, {name: (value, is_label)})`` of a listing, atoms in output order."""
    atoms, sym = [], {}
    for line in lines:
        rec = json.loads(line)
        if "sym" in rec:
            sym[rec["sym"]] = (rec["value"], bool(rec["label"]))
        else:
            atoms.append(rec)
    return atoms, sym


def elements(atoms, base, sym, labels):
    """Data elements ``(addr, size, role, target, symbol, row)``; ``row`` numbers the
    declaring atom."""
    out = []
    for row, a in enumerate(atoms):
        size = DATA.get(a["type"])
        if size is None:
            continue
        addr = base + a["off"]
        if "exprs" not in a:
            out += [(addr + i, 1, "const", None, None, row) for i in range(a["len"])]
            continue
        for i, e in enumerate(a["exprs"]):
            out.append((addr + i * size, size, *role(e, sym, labels), row))
    return out


def refs(atoms, base, sym, elems):
    """Address references: code operands (``zp``/``abs``, and ``imm`` ``lo``/``hi`` of a label)
    and data elements naming an address; each with its target and nearest symbol."""
    labels = {n for n, (_, lab) in sym.items() if lab}
    values = {n: v for n, (v, _) in sym.items()}
    out = []
    for a in atoms:
        width = MODES.get(a["type"])
        if width not in ("zp", "abs", "imm"):
            continue
        r, t, s = role(a["arg"], values, labels)
        if width == "imm" and r not in ("lo", "hi"):
            continue
        if width != "imm":
            ns = names(a["arg"])
            t, r, s = (
                ev(a["arg"], values),
                "addr",
                next(iter([n for n in ns if n in labels] + ns), None),
            )
        mn, mode = OPCODES[a["op"]]
        out.append(
            {"at": base + a["off"] + 1, "size": 1 + (width == "abs"), "in": "code", "mode": mode}
            | {"mn": mn, "role": r, "target": t, "sym": s}
        )
    for addr, size, r, t, s, _ in elems:
        if r != "const":
            out.append(
                {"at": addr, "size": size, "in": "data", "mode": "data", "role": r}
                | {"target": t, "sym": s}
            )
    for x in out:
        x["offset"] = None if x["sym"] is None else x["target"] - values[x["sym"]]
    return out


def segments(elems, sym):
    """Contiguous data elements split at label addresses: ``[(labels, [elements])]``."""
    at = {}
    for n, (v, lab) in sym.items():
        if lab:
            at.setdefault(v, []).append(n)
    segs = []
    for el in elems:
        if not segs or sum(segs[-1][1][-1][:2]) != el[0] or el[0] in at:
            segs.append((sorted(at.get(el[0], [])), []))
        segs[-1][1].append(el)
    return segs


def kind(els):
    """A segment's kind from its elements' roles."""
    roles = [e[2] for e in els]
    half = len(roles) // 2
    if all(r == "lo" for r in roles):
        return "addr_lo"
    if all(r == "hi" for r in roles):
        return "addr_hi"
    if all(r == "addr" for r in roles) or (
        len(roles) % 2 == 0
        and roles[0::2] == ["lo"] * half
        and roles[1::2] == ["hi"] * half
        and [e[3] for e in els[0::2]] == [e[3] for e in els[1::2]]
    ):
        return "pointers"
    return "words" if any(e[1] == 2 for e in els) else "bytes"


def region(labels, els):
    """The region record of one segment."""
    k = kind(els)
    r = {"kind": k, "labels": labels, "addr": els[0][0], "size": sum(e[1] for e in els)}
    if k in ("addr_lo", "addr_hi", "pointers"):
        r["targets"] = [e[3] for e in els if e[2] != "hi" or k == "addr_hi"]
    r["count"] = len(r.get("targets", els))
    return r


def records(segs, i):
    """``(k, region)`` of a record array at segment ``i``, or None: one-byte labelled fields
    ``f1..fk`` at consecutive addresses, ``fk`` followed by rows of exactly ``k`` bytes, one
    row per further record."""
    if not segs[i][0]:
        return None
    for k in range(2, len(segs) - i + 1):
        head = segs[i : i + k]
        if len(head[-2][1]) != 1 or not head[-1][0]:
            return None
        if head[-1][1][0][0] != head[0][1][0][0] + k - 1:
            return None
        last = head[-1][1]
        rows = Counter(e[5] for e in last[1:])
        if (
            rows
            and all(e[1] == 1 for e in last)
            and last[0][5] not in rows
            and set(rows.values()) == {k}
        ):
            count = 1 + len(rows)
            return k, {
                "kind": "records",
                "labels": head[0][0],
                "addr": head[0][1][0][0],
                "size": k * count,
                "stride": k,
                "count": count,
                "fields": [{"labels": h[0], "offset": j} for j, h in enumerate(head)],
            }
    return None


def pair(regions):
    """Split address tables: each ``addr_lo`` joined with the ``addr_hi`` of equal targets."""
    his = [r for r in regions if r["kind"] == "addr_hi"]
    joined = {}
    for r in regions:
        hi = next((h for h in his if r["kind"] == "addr_lo" and h["targets"] == r["targets"]), None)
        if hi is not None:
            his.remove(hi)
            joined[id(hi)] = None
            joined[id(r)] = r | {"kind": "addr_table", "hi": hi["addr"], "hi_labels": hi["labels"]}
    return [joined.get(id(r), r) for r in regions if joined.get(id(r), r) is not None]


def parallel(regions):
    """Runs of adjacent byte arrays of one length (at least two elements), none a pointer
    target: arrays indexed together."""
    out, run = [], []
    for r in regions + [None]:
        ok = r is not None and r["kind"] == "bytes" and r["count"] > 1 and "target_of" not in r
        if (
            ok
            and run
            and r["count"] == run[0]["count"]
            and r["addr"] == run[-1]["addr"] + run[-1]["size"]
        ):
            run.append(r)
            continue
        if len(run) > 1:
            out.append(
                {"kind": "parallel", "labels": [x["labels"] for x in run]}
                | {"addrs": [x["addr"] for x in run], "addr": run[0]["addr"]}
                | {"size": sum(x["size"] for x in run), "count": run[0]["count"]}
            )
        run = [r] if ok else []
    return out


def layout(atoms, sym, base):
    """``(regions, refs)`` of a listing whose first atom is at ``base``."""
    labels = {n for n, (_, lab) in sym.items() if lab}
    elems = elements(atoms, base, {n: v for n, (v, _) in sym.items()}, labels)
    segs = segments(elems, sym)
    regions, i = [], 0
    while i < len(segs):
        rec = records(segs, i)
        regions.append(rec[1] if rec else region(*segs[i]))
        i += rec[0] if rec else 1
    regions = pair(regions)
    owner = {}
    for r in regions:
        for t in r.get("targets", []):
            owner.setdefault(t, set()).add(r["addr"])
    for r in regions:
        if r["addr"] in owner:
            r["target_of"] = sorted(owner[r["addr"]])
    return regions + parallel(regions), refs(atoms, base, sym, elems)


def check(atoms, sym, base, body):
    """Addresses where the listing's bytes differ from the image ``body`` loaded at ``base``."""
    values = {n: v for n, (v, _) in sym.items()}
    bad = []
    for a in atoms:
        if "op" in a:
            v = ev(a["arg"], values) if "arg" in a else 0
            want = [a["op"], v & 0xFF, (v >> 8) & 0xFF][: a["len"]]
        elif "exprs" in a:
            size = DATA[a["type"]]
            want = [
                b
                for e in a["exprs"]
                for b in ev(e, values).to_bytes(4, "little", signed=True)[:size]
            ]
        elif "res" in a:
            want = [ev(a["res"], values) & 0xFF] * a["len"]
        else:
            continue
        if bytes(body[a["off"] : a["off"] + a["len"]]) != bytes(want):
            bad.append(base + a["off"])
    return bad


def truth(listing, sid):
    """The ground-truth record of a listing and the ``.sid`` assembled with it."""
    atoms, sym = load_listing(listing)
    base, _, body = vtools.sid_body(Path(sid).read_bytes())
    regions, rf = layout(atoms, sym, base)
    return {
        "player": PLAYER,
        "sid": Path(sid).name,
        "load": base,
        "size": len(body),
        "symbols": {n: {"value": v, "label": lab} for n, (v, lab) in sorted(sym.items())},
        "regions": regions,
        "refs": rf,
        "mismatches": check(atoms, sym, base, body),
    }


def pack(song, out, opts=(), image=vtools.IMAGE, timeout=300):
    """Pack ``song`` (a path in the image) with ``opts`` into ``out``; the output stem."""
    stem = Path(out) / Path(song).stem
    env = {"GT2_SOURCE": f"/out/{stem.name}.s", "GT2_LISTING": f"/out/{stem.name}.jsonl"}
    args = ["gt2reloc", song, f"/out/{stem.name}.sid", *opts]
    r = vtools.run(args, [(out, "/out", False)], env, image=image, timeout=timeout)
    if r.returncode or not stem.with_suffix(".jsonl").exists():
        raise RuntimeError(f"gt2reloc {song}: {r.stdout}{r.stderr}")
    return stem


def songs(image=vtools.IMAGE):
    """The example songs bundled in the image."""
    return vtools.run(["sh", "-c", f"ls {EXAMPLES}/*.sng"], image=image, timeout=120).stdout.split()


def summarize(rec):
    """Region counts by kind, reference counts by role, and mismatches of a truth record."""
    return {
        "regions": dict(Counter(r["kind"] for r in rec["regions"])),
        "refs": dict(Counter(f"{r['in']}:{r['role']}" for r in rec["refs"])),
        "mismatches": len(rec["mismatches"]),
    }


def produce(out, sngs=None, variants=None, image=vtools.IMAGE):
    """Pack every song in every variant and write its truth; ``{variant/song: summary}``."""
    sngs = sngs or songs(image)
    variants = variants or VARIANTS
    summary = {}
    for s, v in tqdm([(s, v) for s in sngs for v in variants], desc="gtruth", unit="tune"):
        d = Path(out) / v
        d.mkdir(parents=True, exist_ok=True)
        stem = pack(s, d, variants[v], image)
        rec = truth(stem.with_suffix(".jsonl").read_text().splitlines(), stem.with_suffix(".sid"))
        rec |= {"song": Path(s).name, "variant": v, "options": variants[v]}
        stem.with_suffix(".truth.json").write_text(json.dumps(rec))
        summary[f"{v}/{stem.name}"] = summarize(rec)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("songs", nargs="*", help=f"song paths in the image (default {EXAMPLES})")
    ap.add_argument(
        "--variant",
        action="append",
        default=[],
        metavar="NAME=OPTS",
        help=f"gt2reloc options per variant (default {VARIANTS})",
    )
    ap.add_argument("--out", type=Path, default=vtools.ROOT / "out" / "gtruth")
    ap.add_argument("--image", default=vtools.IMAGE)
    args = ap.parse_args(argv)
    variants = {k: shlex.split(o) for k, _, o in (v.partition("=") for v in args.variant)}
    summary = produce(args.out.resolve(), args.songs, variants or None, args.image)
    for k, s in summary.items():
        print(k, json.dumps(s))
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    return int(any(s["mismatches"] for s in summary.values()))


if __name__ == "__main__":
    sys.exit(main())
