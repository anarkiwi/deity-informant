#!/usr/bin/env python3
"""Validate recovered layout maps: ground truth, same-player consistency, sidreloc agreement.

``python tools/layoutscore.py truth|consistency|sidreloc [--sidid HVSC.json] [--json OUT]``:
``tools/layout.py`` maps scored against GoatTracker fixtures (``out/gtruth``), compared across
tunes of one player, and their address high bytes checked against sidreloc's (docs/validation.md).
"""

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

# pylint: disable=wrong-import-position
import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from bisect import bisect_right  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from concurrent.futures import TimeoutError as FutureTimeout  # noqa: E402
from difflib import SequenceMatcher  # noqa: E402
from pathlib import Path  # noqa: E402

from jennings.opcodes import MEM_MODES, MODE_LEN, OPCODES  # noqa: E402
from pebble import ProcessPool  # noqa: E402
from tqdm import tqdm  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deity_informant.layout import address_bytes, table_entries  # noqa: E402
import vtools  # noqa: E402 pylint: disable=wrong-import-order

KINDS = ("words", "pointers", "tables", "records", "arrays", "parallel", "targets")
EXACT = ("targets", "bytes_lo", "bytes_hi")
COUNTS = ("pred", "tp", "truth", "recalled", "reached", "recalled_reached")
INDIRECT = ("indx", "indy", "ind")
STRIDE = {"bytes": 1, "addr_lo": 1, "addr_hi": 1, "words": 2, "pointers": 2}
ADDRESSED = MEM_MODES | {"ind"}


def _run_layout(path, song, calls):
    from layout import song_layout  # pylint: disable=import-outside-toplevel

    return song_layout(Path(path).read_bytes(), song, calls, progress=False)


def _pool(fn, jobs, workers, timeout, desc):
    """``{job: result}`` of ``fn(*job)`` in a process pool; a raised or timed-out job's result
    is ``{"error": ...}``."""
    out = {}
    with ProcessPool(max_workers=max(1, min(workers, len(jobs)))) as pool:
        futs = {pool.schedule(fn, j, timeout=timeout): j for j in jobs}
        for fut in tqdm(futs, desc=desc, unit="tune", disable=not jobs):
            try:
                out[futs[fut]] = fut.result()
            except (FutureTimeout, Exception) as e:  # pylint: disable=broad-except
                out[futs[fut]] = {"error": f"{type(e).__name__}: {e}"}
    return out


def maps(jobs, cache, calls=1000, workers=64, timeout=600):
    """``{(path, song): map}`` of ``tools/layout.py`` for ``jobs`` ``[(path, song)]``, cached
    under ``cache`` by the file's md5; a failed job maps to ``{"error": ...}``."""
    cache.mkdir(parents=True, exist_ok=True)
    files = {
        (p, s): cache / f"{hashlib.md5(Path(p).read_bytes()).hexdigest()}.{s}.{calls}.json"
        for p, s in jobs
    }
    todo = [(str(p), s, calls) for (p, s), f in files.items() if not f.exists()]
    for (p, s, _), m in _pool(_run_layout, todo, workers, timeout, "layout").items():
        files[Path(p), s].write_text(json.dumps(m), encoding="utf-8")
    return {k: json.loads(f.read_text(encoding="utf-8")) for k, f in files.items()}


def _span(lo, hi, **kw):
    return {"lo": lo, "hi": hi} | kw


def truth_items(t):
    """``{kind: [item]}`` of a truth record: spans ``lo``-``hi`` (inclusive) with ``stride``
    and field ``offsets`` (records, arrays), ``lo_addr``/``hi_addr`` (tables), ``addrs``
    (parallel) or the pointed-at region's ``reach`` (targets)."""
    out = defaultdict(list)
    for r in t["regions"]:
        k, a = r["kind"], r["addr"]
        span = _span(a, a + r["size"] - 1)
        if k == "parallel":
            out[k].append(span | {"addrs": r["addrs"]})
            continue
        if k == "addr_table":
            n = r["count"]
            out["tables"].append(_span(a, a + n - 1, lo_addr=a, hi_addr=r["hi"]))
            out["arrays"] += [
                _span(b, b + n - 1, stride=1, offsets={0}) for b in (a, r["hi"]) if n > 1
            ]
            continue
        if k in ("words", "pointers"):
            out[k].append(span)
        if k == "records":
            out[k].append(
                span | {"stride": r["stride"], "offsets": {f["offset"] for f in r["fields"]}}
            )
        if "target_of" in r:
            out["targets"].append(_span(a, a, reach=(a, a + r["size"] - 1)))
        elif r["count"] > 1:
            offs = {f["offset"] for f in r.get("fields", [{"offset": 0}])}
            out["arrays"].append(span | {"stride": r.get("stride", STRIDE.get(k)), "offsets": offs})
    zp = {x["target"] for x in t["refs"] if x["in"] == "code" and x["mode"] in INDIRECT}
    out["pointers"] += [_span(p, p + 1) for p in sorted(zp)]
    return dict(out)


def _extent(r):
    if "extent" in r:
        return r["extent"]
    return [r["base"], r["base"] + max(f["offset"] + f["width"] for f in r["fields"]) - 1]


def predicted_items(m):
    """``{kind: [item]}`` of a layout map's memory regions in :func:`truth_items`' terms;
    a table's ``lo_addr``/``hi_addr`` are its operand bases."""
    out = defaultdict(list)
    for r in m["regions"]:
        if r.get("io"):
            continue
        k, span = r["kind"], _span(*_extent(r))
        if k == "address_table":
            lo, hi = (r["base"] + w for w in r["words"][0])
            ent = table_entries(r) or [(lo, hi)]
            if hi == lo + 1:
                out["pointers"].append(span)
            else:
                out["tables"].append(_span(ent[0][0], ent[-1][0], lo_addr=lo, hi_addr=hi))
        elif k in ("word", "pointer"):
            out[k + "s"].append(span)
        elif k == "record":
            offs = {(r["base"] + f["offset"]) % r["stride"] for f in r["fields"]}
            out["records"].append(span | {"stride": r["stride"], "offsets": offs})
        elif k == "array":
            out["arrays"].append(span | {"stride": r["stride"]})
        elif k == "parallel_array":
            out["parallel"].append(span | {"addrs": [r["base"] + f["offset"] for f in r["fields"]]})
        out["targets"] += [_span(v, v) for v in r.get("targets", [])]
    return dict(out)


def _overlap(p, t):
    return p["lo"] <= t["hi"] and t["lo"] <= p["hi"]


def match(kind, p, t):
    """Whether predicted item ``p`` is truth item ``t`` of ``kind`` (docs/validation.md)."""
    if not _overlap(p, t):
        return False
    if kind in EXACT:
        return p["lo"] == t["lo"]
    if kind == "tables":
        return p["lo_addr"] - t["lo_addr"] == p["hi_addr"] - t["hi_addr"]
    if kind == "records":
        own = {(t["lo"] + o) % t["stride"] for o in t["offsets"]}
        return p["stride"] == t["stride"] and p["offsets"] <= own
    if kind == "arrays":
        return p["stride"] % t["stride"] == 0 and (p["lo"] - t["lo"]) % t["stride"] in t["offsets"]
    if kind == "parallel":
        return max(Counter(b - a for b in p["addrs"] for a in t["addrs"]).values()) >= 2
    return True


def judged(p, t):
    """Whether truth record ``t`` decides predicted item ``p``: it lies in zero page or
    overlaps the image."""
    return p["hi"] < 0x100 or _overlap(p, _span(t["load"], t["load"] + t["size"] - 1))


def truth_bytes(t):
    """``{address: "lo"|"hi"}`` of the address bytes in the truth's data."""
    out = {}
    for x in t["refs"]:
        if x["in"] != "data":
            continue
        if x["role"] in ("lo", "hi"):
            out[x["at"]] = x["role"]
        elif x["size"] == 2:
            out[x["at"]], out[x["at"] + 1] = "lo", "hi"
    return out


def _ratios(c):
    def q(a, b):
        return c[a] / c[b] if c[b] else None

    return c | {
        "precision": q("tp", "pred"),
        "recall": q("recalled", "truth"),
        "recall_reached": q("recalled_reached", "reached"),
    }


def _counts(kind, pk, tk, runs):
    """Counts of predicted items ``pk`` against truth items ``tk`` given ``accessed`` runs."""
    hit = [[match(kind, p, x) for x in tk] for p in pk]
    rec = [any(c) for c in zip(*hit)] if pk else [False] * len(tk)
    reach = [_touched(runs, *x.get("reach", (x["lo"], x["hi"]))) for x in tk]
    return {
        "pred": len(pk),
        "tp": sum(map(any, hit)),
        "truth": len(tk),
        "recalled": sum(rec),
        "reached": sum(reach),
        "recalled_reached": sum(a and b for a, b in zip(reach, rec)),
    }


def _touched(runs, lo, hi):
    i = bisect_right(runs, [lo, float("inf")]) - 1
    return (i >= 0 and runs[i][1] >= lo) or (i + 1 < len(runs) and runs[i + 1][0] <= hi)


def score(m, t):
    """Per kind and address byte role (``COUNTS``): judged predictions, those matching a truth
    item, truth items, those matched, and of them those the run accessed; with the ratios."""
    ts, ps = truth_items(t), predicted_items(m)
    lo, hi = t["load"], t["load"] + t["size"]
    tb, pb = truth_bytes(t), address_bytes(m["regions"])
    for role in ("lo", "hi"):
        ts["bytes_" + role] = [_span(a, a) for a, v in sorted(tb.items()) if v == role]
        ps["bytes_" + role] = [_span(a, a) for a, v in sorted(pb.items()) if v == role]
    ps["targets"] = [_span(v, v) for v in sorted({p["lo"] for p in ps.get("targets", [])})]
    runs = m.get("accessed", [])
    out = {}
    for k in KINDS + EXACT[1:]:
        pk = [p for p in ps.get(k, []) if judged(p, t) and (k in KINDS or lo <= p["lo"] < hi)]
        out[k] = _ratios(_counts(k, pk, ts.get(k, []), runs))
    return out


def total(scores):
    """Counts summed over ``scores``, ratios recomputed."""
    return {k: _ratios({f: sum(s[k][f] for s in scores) for f in COUNTS}) for k in scores[0]}


def fixtures(root):
    """``[(variant, truth record path)]`` under a ground-truth directory, sorted."""
    return [(p.parent.name, p) for p in sorted(Path(root).glob("*/*.truth.json"))]


def _sid(truth_path):
    return truth_path.with_name(truth_path.name.replace(".truth.json", ".sid"))


def score_truth(root, cache, calls, workers):
    """``{variant: {"total", "tunes", "errors"}}`` of :func:`score` over the fixtures."""
    fx = fixtures(root)
    ms = maps([(_sid(p), 1) for _, p in fx], cache, calls, workers)
    res = defaultdict(lambda: {"tunes": {}, "errors": {}})
    for v, p in fx:
        m, name = ms[_sid(p), 1], _sid(p).name
        if "error" in m:
            res[v]["errors"][name] = m["error"]
        else:
            res[v]["tunes"][name] = score(m, json.loads(p.read_text(encoding="utf-8")))
    return {v: {"total": total(list(r["tunes"].values()))} | r for v, r in res.items()}


def _pcs(r, prefix=""):
    return {int(e.split("@")[1], 16) for e in r["evidence"] if e.startswith(prefix)}


def _image(path):
    load, _, body = vtools.sid_body(Path(path).read_bytes())
    return load, body


def _byte(img, a):
    load, body = img
    return body[a - load] if 0 <= a - load < len(body) else None


def _mode(img, pc):
    op = _byte(img, pc)
    return "impl" if op is None else OPCODES[op][1]


def operand(img, pc):
    """The address operand of the instruction at ``pc`` in the image, else None."""
    mode, lo, hi = _mode(img, pc), _byte(img, pc + 1), _byte(img, pc + 2)
    if mode not in ADDRESSED or lo is None:
        return None
    if MODE_LEN[mode] == 2:
        return lo
    return None if hi is None else lo | hi << 8


def align(img_a, starts_a, img_b, starts_b):
    """``{pc_a: pc_b}`` of instruction addresses aligned by their opcode sequences in address
    order (``difflib.SequenceMatcher`` matching blocks)."""
    sa, sb = sorted(starts_a), sorted(starts_b)
    ta, tb = [_byte(img_a, p) for p in sa], [_byte(img_b, p) for p in sb]
    blocks = SequenceMatcher(None, ta, tb, autojunk=False).get_matching_blocks()
    return {sa[i + k]: sb[j + k] for i, j, n in blocks for k in range(n)}


def shape(r):
    """A region's layout modulo placement and element count: the members of parallel arrays
    and split tables lie where the data puts them, so only their widths and words count."""
    fields = tuple((f["offset"], f["width"]) for f in r["fields"])
    words = tuple(map(tuple, r.get("words", ())))
    if r["kind"] == "parallel_array" or (r["kind"] == "address_table" and words != ((0, 1),)):
        fields, words = tuple(sorted(w for _, w in fields)), len(words)
    return r["kind"], r.get("stride"), fields, words


def _same_place(ia, ib, pcmap, x, y):
    """Whether ``y`` sits where ``x`` does relative to the operand of an instruction accessing
    both (``base - operand`` equal across the aligned code)."""
    for p in _pcs(x, "ea@"):
        q = pcmap.get(p)
        if q in _pcs(y, "ea@"):
            oa, ob = operand(ia, p), operand(ib, q)
            if None not in (oa, ob) and y["base"] - ob == x["base"] - oa:
                return True
    return False


def correspond(a, b):
    """Counts over two tunes' memory regions (``{"map", "image"}``): ``corresponding`` (one of
    the other map has its kind and evidence at aligned code), ``same_shape``, ``placed``
    (instruction evidence, parallel arrays apart: their members place them), ``same_place``."""
    code = [set(t["map"]["starts"]).union(*map(_pcs, t["map"]["regions"])) for t in (a, b)]
    pcmap = align(a["image"], code[0], b["image"], code[1])
    ra, rb = ([r for r in t["map"]["regions"] if not r.get("io")] for t in (a, b))
    pairs = []
    for x in ra:
        mapped = {pcmap[p] for p in _pcs(x) if p in pcmap}
        pairs += [(x, y) for y in rb if y["kind"] == x["kind"] and mapped & _pcs(y)]
    same = [(x, y) for x, y in pairs if shape(x) == shape(y)]
    placed = [(x, y) for x, y in same if _pcs(x, "ea@") and x["kind"] != "parallel_array"]

    def sides(ps):
        return len({id(x) for x, _ in ps}) + len({id(y) for _, y in ps})

    return {
        "regions": len(ra) + len(rb),
        "corresponding": sides(pairs),
        "same_shape": sides(same),
        "placed": sides(placed),
        "same_place": sides(
            [(x, y) for x, y in placed if _same_place(a["image"], b["image"], pcmap, x, y)]
        ),
    }


def consistency(groups, cache, calls, workers):
    """``{group: counts}`` of :func:`correspond` summed over each group's tune pairs
    ``{name: [((path, song), (path, song))]}``."""
    ms = maps({j for g in groups.values() for pr in g for j in pr}, cache, calls, workers)
    out = {}
    for name, pairs in groups.items():
        ok = [pr for pr in pairs if not any("error" in ms[j] for j in pr)]
        cs = [
            correspond(*({"map": ms[j], "image": _image(j[0])} for j in pr))
            for pr in tqdm(ok, desc=name, unit="pair", leave=False)
        ]
        out[name] = {"pairs": len(pairs), "mapped": len(ok)} | {
            k: sum(c[k] for c in cs) for k in (cs[0] if cs else ())
        }
    return out


def code_bytes(img, starts):
    """Addresses of the executed instructions' bytes."""
    return {a for pc in starts for a in range(pc, pc + MODE_LEN[_mode(img, pc)])}


def agree(m, img, rep):
    """The map's implied address high bytes in the image against sidreloc's report ``rep``:
    ``layout``, ``sidreloc`` (its high bytes outside executed code), ``both``, ``static``
    (layout high bytes sidreloc keeps) and ``undecided`` (sidreloc left open)."""
    load, body = img
    hi = {a for a, r in address_bytes(m["regions"]).items() if r == "hi"}
    hi = {a for a in hi if load <= a < load + len(body)}
    roles = {a: r for r, v in rep["roles"].items() for a in v}
    srel = set(rep["roles"]["hi"]) - code_bytes(img, m["starts"])
    return {
        "layout": len(hi),
        "sidreloc": len(srel),
        "both": len(hi & srel),
        "static": sum(roles.get(a) in ("static", "zp") for a in hi),
        "undecided": sum(roles.get(a) not in ("hi", "static", "zp") for a in hi),
    }


def _reloc(path, frames):
    import sidreloc  # pylint: disable=import-outside-toplevel

    return sidreloc.relocate(path, frames=frames)


def sidreloc_agreement(jobs, cache, calls, workers, frames=None):
    """``{"total", "tunes", "failed"}`` of :func:`agree` over ``jobs`` ``[(path, song)]``;
    ``precision`` is over the layout high bytes sidreloc decided, ``recall`` over its own."""
    ms = maps(jobs, cache, calls, workers)
    ok = sorted((str(p), frames) for p, s in jobs if "error" not in ms[p, s])
    song = dict((str(p), s) for p, s in jobs)
    res, failed = {}, {}
    for (p, _), rep in _pool(_reloc, ok, workers, 1800, "sidreloc").items():
        if rep["error"] != "ok":
            failed[p] = rep["error"]
        else:
            res[p] = agree(ms[Path(p), song[p]], _image(p), rep)
    tot = {k: sum(c[k] for c in res.values()) for k in next(iter(res.values()), {})}
    if tot:
        decided = tot["layout"] - tot["undecided"]
        tot["precision"] = tot["both"] / decided if decided else None
        tot["recall"] = tot["both"] / tot["sidreloc"] if tot["sidreloc"] else None
    return {"total": tot, "tunes": res, "failed": failed}


def player_groups(sidid_json, players):
    """``{player: [(path, start song)]}`` of PSID tunes with a play routine whose SIDId names
    start with one of ``players``, by their most specific such name, in path order."""
    from wpcorpus import header  # pylint: disable=import-outside-toplevel

    names = json.loads(Path(sidid_json).read_text(encoding="utf-8"))
    out = defaultdict(list)
    for p in sorted(names):
        hits = [n.strip("()") for n in names[p] if n.strip("()").startswith(tuple(players))]
        h = header(Path(p).read_bytes()) if hits else None
        if h and h["kind"] == "PSID" and h["play"]:
            out[max(hits, key=len)].append((Path(p), h["start"]))
    return dict(out)


def variant_pairs(root):
    """``[((sid, 1), (sid, 1))]``: each fixture song's variants paired with its first."""
    by = defaultdict(list)
    for _, p in fixtures(root):
        by[p.name].append((_sid(p), 1))
    return [(vs[0], v) for vs in by.values() for v in vs[1:]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["truth", "consistency", "sidreloc"])
    ap.add_argument("--gtruth", type=Path, default=vtools.ROOT / "out" / "gtruth")
    ap.add_argument("--sidid", type=Path, help="tools/sidid.py --json output to group tunes by")
    ap.add_argument("--players", nargs="*", default=["GoatTracker_V2", "JCH_NewPlayer"])
    ap.add_argument("--calls", type=int, default=1000)
    ap.add_argument("--jobs", type=int, default=64)
    ap.add_argument("--cache", type=Path, default=vtools.ROOT / "out" / "layoutscore")
    ap.add_argument("--json", type=Path, help="write the full result here")
    args = ap.parse_args(argv)
    common = (args.cache, args.calls, args.jobs)
    tunes = player_groups(args.sidid, args.players) if args.sidid else {}
    if args.command == "truth":
        res = score_truth(args.gtruth, *common)
        brief = {v: r["total"] for v, r in res.items()}
    elif args.command == "consistency":
        groups = {"gtruth variants": variant_pairs(args.gtruth)}
        groups |= {g: list(zip(ts, ts[1:])) for g, ts in tunes.items()}
        res = brief = consistency(groups, *common)
    else:
        jobs = [j for pr in variant_pairs(args.gtruth) for j in pr]
        jobs += [j for ts in tunes.values() for j in ts]
        res = sidreloc_agreement(sorted(set(jobs)), *common)
        brief = res["total"] | {"failed": len(res["failed"])}
    print(json.dumps(brief, indent=1))
    if args.json:
        args.json.write_text(json.dumps(res, indent=1, default=sorted), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
