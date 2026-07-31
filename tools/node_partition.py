"""Does the frame program name the editor's node set as (declared region, cursor) pairs?

The editor's objects are located in the packed image by the decompiler's own layout
resolution, so each is an address span; the program's candidates are the loads its text
writes as ``base[index]``. The two are matched by address containment and nothing else.
"""

import collections
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
HVSC = ROOT / ".oracle-cache" / "hvsc"
OUT = ROOT / "out" / "node_partition.json"
FRAMES = 200
WORKERS = int(os.environ.get("WORKERS", "5"))

Obj = collections.namedtuple("Obj", "name base size")

# the composer object an oracle lane reads; a lane absent here has no address at all
_LANE_OBJ = {
    ("pitch", "lo"): "pitch.lo",
    ("pitch", "hi"): "pitch.hi",
    ("ins", "ad"): "ins.ad",
    ("ins", "sr"): "ins.sr",
    ("ins", "firstwave"): "ins.firstwave",
    ("wtbl", "left"): "wtbl.left",
    ("wtbl", "right"): "wtbl.right",
    ("wtbl", "silent"): "wtbl.left",
    ("ptbl", "left"): "ptbl.left",
    ("ptbl", "right"): "ptbl.right",
    ("ftbl", "left"): "ftbl.left",
    ("ftbl", "right"): "ftbl.right",
    ("ftbl", "type"): "ftbl.left",
    ("stbl", "left"): "stbl.left",
    ("stbl", "right"): "stbl.right",
    ("patt", "data"): "patterns",
    ("patt", "note"): "patterns",
    ("patt", "instr"): "patterns",
    ("row", "counter"): "patterns",
}
_NO_OBJ = {("hr", "ad"), ("hr", "sr"), ("vol", "master")}  # player constants, not song tables


# ---- 1. the editor's objects, as address spans in the packed image ----------------
def gt_layout(path, subtune):
    """pygoattracker's own packed-image layout, keeping the addresses it discards.

    `decompile_sid` resolves the layout and hands back only the decoded song, so the
    same candidate loop is rerun here to keep each table's base."""
    # pylint: disable=protected-access
    from pygoattracker import sid as S  # pylint: disable=import-outside-toplevel
    from pysidtracker import read_bytes, run_init  # pylint: disable=import-outside-toplevel

    image = S._load_image(read_bytes(str(path)))
    end, anchor = image.end, S._recognize(image)
    if anchor is None:
        run_init(image, subtune=subtune)
        end, anchor = 0x10000, S._recognize(image)
    if anchor is None:
        raise S.SidParseError("not a GT sid")
    err = S.SidParseError("no layout candidate")
    for cand in S._layout_candidates(image, image.header, anchor):
        try:
            return _gt_solve(S, image, end, anchor, cand, subtune)
        except (S.SidParseError, IndexError, ValueError) as exc:
            err = exc
    raise err


def _gt_solve(S, image, end, anchor, cand, subtune):
    """One layout hypothesis carried far enough to name every table's base."""
    # pylint: disable=protected-access
    songtbl, songs, patt_lo = cand
    mem = image.mem
    order = [mem[songtbl + i] | (mem[songtbl + 3 * songs + i] << 8) for i in range(3 * songs)]
    if any(not 0 < a < end for a in order):
        raise S.SidParseError("orderlist pointer out of range")
    order_end = max(S.decode_packed_orderlist(mem, b)[1] for b in order)
    if mem[patt_lo] != (order_end & 0xFF):
        raise S.SidParseError("could not locate pattern-pointer table")
    npat = S._count_patterns(mem, patt_lo, order_end, min(order))
    if npat is None:
        raise S.SidParseError("could not locate pattern-pointer table")
    patterns, pos = [], order_end
    for _ in range(npat):
        rows, pos = S.decode_packed_pattern(mem, pos)
        patterns.append(rows)
    start = patt_lo + 2 * npat
    if not start < min(order):
        raise S.SidParseError("bad packed layout")
    ninstr = max((r.instrument for rows in patterns for r in rows), default=1)
    eps = S._pattern_features(patterns)[1:]
    try:
        sol = S._solve_layout(mem, start, min(order), ninstr, eps)
    except S.SidParseError:
        sol = S._chain_layout(image, mem, start, ninstr, min(order), eps)
        if sol is None:
            raise
    return _gt_objects(anchor, cand, npat, order, order_end, pos, start, sol, subtune)


_INS_ORDER = ("ad", "sr", "waveptr", "pulseptr", "filtptr", "vibparam", "vibdelay")


def _gt_objects(anchor, cand, npat, order, order_end, pend, start, sol, subtune):
    """``[Obj]``: every table the composer wrote, with its base and extent."""
    addr, _first, length = anchor
    songtbl, songs, patt_lo = cand
    n = len(next(iter(sol["arrays"].values())))
    out = [
        Obj("pitch.lo", addr, length),
        Obj("pitch.hi", addr + length, length),
        Obj("songtbl.lo", songtbl, 3 * songs),
        Obj("songtbl.hi", songtbl + 3 * songs, 3 * songs),
        Obj("pattptr.lo", patt_lo, npat),
        Obj("pattptr.hi", patt_lo + npat, npat),
        Obj("patterns", order_end, pend - order_end),
    ]
    played = order[3 * subtune : 3 * subtune + 3] or order[:3]
    out += [Obj("orderlist.%d" % i, b, 1) for i, b in enumerate(played)]
    off = start
    for name in _INS_ORDER + ("gatetimer", "firstwave"):
        if name in sol["arrays"]:
            out.append(Obj("ins.%s" % name, off, n))
            off += n
    at = sol["tstart"]
    for tab, ln in (("wtbl", sol["wlen"]), ("ptbl", sol["plen"]), ("ftbl", sol["flen"])):
        if ln:
            out += [Obj("%s.left" % tab, at, ln), Obj("%s.right" % tab, at + ln, ln)]
        at += 2 * ln
    at += sol["zeros"]
    if sol["slen"]:
        out += [
            Obj("stbl.left", at, sol["slen"]),
            Obj("stbl.right", at + sol["slen"] + sol["zeros"], sol["slen"]),
        ]
    return [o for o in out if o.size > 0]


def _gt_verify(objs, song, mem0):
    """``(agreeing, checked)``: located bytes vs the tables the decompiler returns verbatim.

    The wave and filter columns are transformed on the way out and are not checked; a
    mismatch on the rest condemns the tune's whole address map."""
    want = {
        "ins.ad": tuple(i.attack_decay & 0xFF for i in song.instruments),
        "ins.sr": tuple(i.sustain_release & 0xFF for i in song.instruments),
        "ins.waveptr": tuple(i.wave_ptr & 0xFF for i in song.instruments),
        "ptbl.left": tuple(b & 0xFF for b in song.pulsetable.left),
        "ptbl.right": tuple(b & 0xFF for b in song.pulsetable.right),
        "stbl.left": tuple(b & 0xFF for b in song.speedtable.left),
        "stbl.right": tuple(b & 0xFF for b in song.speedtable.right),
    }
    ok, n = 0, 0
    for o in objs:
        exp = want.get(o.name)
        if not exp:
            continue
        n += 1
        cut = min(o.size, len(exp))
        ok += int(tuple(mem0[o.base : o.base + cut]) == exp[:cut])
    return ok, n


def dm_objects(sites):
    """DefMON's objects: the row-pointer array, the arrangers, the patterns, the pitch.

    No sidTAB column is here: the rows are bitmap-packed behind that pointer array, so
    no column is a contiguous span and none has an address to match."""
    lo, hi = sites.pitch
    return [
        Obj("sidtab.row", sites.base, 0x100),
        Obj("pitch.lo", lo, 128),
        Obj("pitch.hi", hi, 128),
        Obj("patterns", sites.patt, 0x200),
    ] + [Obj("arr.v%d" % (k + 1), sites.arr + 0x100 * k, 0x100) for k in range(3)]


# ---- 2. the program's (declared region, cursor) candidate pairs -------------------
def _cursors(idx, env, depth=4):
    """``{cursor base}`` the index expression reads, through the locals in scope.

    Program text only, never an observed value: a load's index is followed through the
    locals defined above it and through any table it is itself read out of."""
    # pylint: disable=protected-access
    from deity_informant import frameproc, tracker  # pylint: disable=import-outside-toplevel

    out, stack, seen = set(), [(idx, depth)], set()
    while stack:
        x, k = stack.pop()
        if not isinstance(x, tuple) or (x, k) in seen:
            continue
        seen.add((x, k))
        if x[0] == "op":
            stack += [(c, k) for c in x[2]]
        elif x[0] == "mem":
            got = frameproc._index_of(x[1])
            out.add(tracker._base(x[1]))
            if got is not None and k:
                stack.append((got[1], k - 1))
        elif x[0] == "loc" and k and x[1] in env:
            stack.append((env[x[1]], k - 1))
    return {c for c in out if c >= 0x100}


def _in(decls, addr):
    """The declaration containing ``addr``, or None."""
    for d in decls:
        if d["base"] <= addr < d["base"] + d["size"]:
            return d
    return None


def _kind(decls, base):
    """What a cursor is, off the declarations alone: a row, a state array, or a cell."""
    d = _in(decls, base)
    if d is None:
        return "cell"
    return "state" if d.get("mut") else "row"


def _loads(expr, env, out):
    """Collect one expression's ``base[index]`` loads into ``{base: {cursor}}``."""
    from deity_informant import frameproc  # pylint: disable=import-outside-toplevel

    stack = [expr]
    while stack:
        x = stack.pop()
        if not isinstance(x, tuple):
            continue
        if x[0] == "mem":
            got = frameproc._index_of(x[1])  # pylint: disable=protected-access
            if got is not None:
                out.setdefault(got[0], set()).update(_cursors(got[1], env))
            stack.append(x[1])
        elif x[0] == "op":
            stack += list(x[2])


def _walk(stmts, env, out):
    """Statements in order, each load resolved against the locals live at that point."""
    from deity_informant import frameproc  # pylint: disable=import-outside-toplevel

    for s in stmts:
        frameproc._map_exprs(s, lambda e: _loads(e, env, out) or e)  # pylint: disable=W0212
        for body in frameproc._stmt_bodies(s):  # pylint: disable=protected-access
            _walk(list(body), dict(env), out)
        if s[0] == "asg":
            env[s[1]] = s[2]


def _deref_pairs(prog, out):
    """Rung (f)'s proven derefs: each target block indexed by the site's own row cell."""
    blocks = {}
    for p in prog.proofs:
        if p.kind == "deref" and p.status == "resolved":
            blocks.setdefault(p.site, set()).update(p.targets)
    for cell, idx in prog.resolved.values():
        for blk in blocks.get(cell, ()):
            got = out.setdefault(blk, set())
            got.add(cell)
            if idx is not None:
                got.update(_cursors(idx, {}))


def prog_pairs(prog):
    """``{load base: {cursor base}}`` for every indexed load the program text writes."""
    out = {}
    for proc in prog.procs:
        _walk(list(proc[3]), {}, out)
    _deref_pairs(prog, out)
    return out


# ---- 3. the correspondence -------------------------------------------------------
def _cover(objs, decls):
    """``{object name: [declarations overlapping its span]}``."""
    return {
        o.name: [d for d in decls if d["base"] < o.base + o.size and o.base < d["base"] + d["size"]]
        for o in objs
    }


def _on(objs, pairs, decls):
    """``{object: [base]}``: the load bases inside an object's span or its declarations."""
    cover, out = _cover(objs, decls), {}
    for o in objs:
        bases = {d["base"] for d in cover[o.name]}
        out[o.name] = [
            b
            for b in pairs
            if o.base <= b < o.base + o.size or (_in(decls, b) or {}).get("base") in bases
        ]
    return cover, out


def _pairs_on(objs, pairs, decls, cover, on):
    """``{object: (declared, paired, strict, cursor kinds)}`` — address containment only.

    ``strict`` needs the load base inside the object's own span rather than merely inside
    a declaration overlapping it, so the looser rule cannot flatter the result unchecked."""
    return {
        o.name: (
            bool(cover[o.name]),
            bool(on[o.name]),
            any(pairs[b] and o.base <= b < o.base + o.size for b in on[o.name]),
            sorted({_kind(decls, c) for b in on[o.name] for c in pairs[b]}),
        )
        for o in objs
    }


def _unmatched(pairs, on):
    """Program pairs whose load base no editor object, declared or not, covers."""
    claimed = {b for bs in on.values() for b in bs}
    return sum(len(pairs[b]) for b in pairs if b not in claimed), len(set(pairs) - claimed)


def oracle_keys(native, records, offset):
    """``(graph, {stream key: emits}, sound)``: the editor's own node set, re-derived.

    `_build` keeps no key, so the same rule is applied to the same frames and the count
    checked against the graph it returned — which is what makes the re-derivation sound."""
    # pylint: disable=protected-access
    from deity_informant import gtoracle, tracker  # pylint: disable=import-outside-toplevel

    frames = gtoracle._predicted(native, records, offset)
    graph, residual, _c = gtoracle._build(frames, native)
    ramps = gtoracle._ramp_keys(native, frames, native.tables)
    resid = {(f, reg) for f, row in enumerate(residual) for reg, _v in row}
    keys, lanes = collections.Counter(), {}
    for f, writes in enumerate(frames):
        for reg, _val in writes:
            cell = native.writes[f].get(reg)
            if (f, reg) in resid or cell is None:
                continue
            if (f, reg) in ramps:
                key = (reg, "ramp", ramps[(f, reg)][0], cell.step, 0xFF, None)
                keys[key] += 1
                lanes[key] = cell.lane
                continue
            for part in gtoracle._parts(cell):
                keys[gtoracle._key(reg, part)] += 1
    based = sum(1 for k in keys if k[1] == "rel" and k[2][1] is not None)
    planes = sum(1 for g in graph.nodes if tracker._is_plane(g.route))
    return graph, keys, lanes, len(keys) + based == planes


def _key_object(key, lanes):
    """``(object name, cursor lane)`` for one editor stream key."""
    kind, lane, arr = key[1], key[2], key[5]
    if kind == "imm":
        return ("player-imm", None)
    if kind == "ramp":
        lane = lanes.get(key)
    elif kind == "rel":
        lane = lane[0]
    if lane in _NO_OBJ:
        return ("player-const", None)
    return (_LANE_OBJ.get(lane, "?%s" % (lane,)), arr and arr[0])


# ---- 4. one tune, and the corpus -------------------------------------------------
def _row(prog, objs, keys, lanes):
    """The measurement proper: objects declared, objects paired, pairs unmatched."""
    from deity_informant import tracker  # pylint: disable=import-outside-toplevel

    decls, pairs = prog.data_decls, prog_pairs(prog)
    cover, on = _on(objs, pairs, decls)
    per = _pairs_on(objs, pairs, decls, cover, on)
    walked = tracker._walked(prog)  # pylint: disable=protected-access
    unpaired, unbases = _unmatched(pairs, on)
    nodes, groups = collections.defaultdict(lambda: [0, 0]), set()
    for key, emits in keys.items():
        name, cur = _key_object(key, lanes)
        got = per.get(name, (False, False, False, ()))
        at = nodes[(name, "paired" if got[1] else ("declared" if got[0] else "no"), bool(cur))]
        at[0] += 1
        at[1] += emits
        groups.add((name, key[0], key[4], cur))
    return {
        "objects": len(objs),
        "declared": sum(1 for v in per.values() if v[0]),
        "paired": sum(1 for v in per.values() if v[1]),
        "paired_strict": sum(1 for v in per.values() if v[2]),
        "gen_row": sum(1 for v in per.values() if "row" in v[3]),
        "per_object": {k: list(v) for k, v in sorted(per.items())},
        "resolved": len(prog.resolved),
        "prog_bases": len(pairs),
        "prog_pairs": sum(len(v) for v in pairs.values()),
        "pairs_unmatched": unpaired,
        "bases_unmatched": unbases,
        "bases_no_cursor": sum(1 for v in pairs.values() if not v),
        "walked_cursors": sum(1 for cs in pairs.values() for c in cs if c in walked),
        "decls": len(decls),
        "editor_nodes": len(keys),
        "editor_groups": len(groups),
        "bases_on_object": sum(len(v) for v in on.values()),
        "editor_emits": sum(keys.values()),
        "emits_by_object": sorted((list(k), v) for k, v in nodes.items()),
    }


def gt_one(rel):
    """The correspondence row for one GoatTracker-packed tune."""
    from deity_informant import gtoracle, tracker  # pylint: disable=import-outside-toplevel
    from deity_informant.c64 import psid_songs  # pylint: disable=import-outside-toplevel
    from gt_compare import _lifted  # pylint: disable=import-outside-toplevel

    path = HVSC / rel
    _songs, start = psid_songs(path.read_bytes())
    song, info, psub = gtoracle.gt_decompile(path, start - 1)
    objs = gt_layout(path, start - 1)
    prog, trace = _lifted(path, start - 1, FRAMES)
    records = tracker.oracle(prog, trace, FRAMES)
    native = gtoracle.gt_native(song, info, psub, FRAMES)
    align = gtoracle.align(records, native)
    _graph, keys, lanes, sound = oracle_keys(native, records, align)
    out = {"editor": "goattracker", "tune": rel, "key_sound": sound}
    out["verified"], out["checkable"] = _gt_verify(objs, song, prog.mem0)
    out.update(_row(prog, objs, keys, lanes))
    return out


def dm_one(rel):
    """The same row for one DefMON-packed tune, whose node keys are not re-derived."""
    from deity_informant import dmoracle  # pylint: disable=import-outside-toplevel
    from deity_informant.c64 import psid_songs  # pylint: disable=import-outside-toplevel
    from gt_compare import _lifted  # pylint: disable=import-outside-toplevel
    from pydefmon._sid_format import find_signature  # pylint: disable=E0401,C0415
    from pysidtracker import SidImage  # pylint: disable=import-outside-toplevel

    path = HVSC / rel
    raw = path.read_bytes()
    _songs, start = psid_songs(raw)
    mem = SidImage.from_sid(raw).mem
    sig = find_signature(mem)
    sites = None if sig is None else dmoracle.dm_sites(mem, sig)
    if sites is None:
        raise ValueError("not a DefMON replay")
    prog, _trace = _lifted(path, start - 1, FRAMES)
    out = {"editor": "defmon", "tune": rel, "key_sound": None, "verified": 0, "checkable": 0}
    out.update(_row(prog, dm_objects(sites), collections.Counter(), {}))
    return out


def _run(item):
    kind, rel = item
    try:
        return (gt_one if kind == "gt" else dm_one)(rel)
    except Exception as exc:  # pylint: disable=broad-except
        return {"editor": kind, "tune": rel, "error": ("%s: %s" % (type(exc).__name__, exc))[:120]}


def _scan():
    """The GoatTracker tunes of out/gt_scan.json, regenerating it when absent."""
    path = ROOT / "out" / "gt_scan.json"
    if not path.exists():
        import gt_scan  # pylint: disable=import-outside-toplevel

        gt_scan.main(["gt_scan.py"])
    return [r["rel"] for r in json.loads(path.read_text()) if r.get("ok")]


def report(res):
    """Per tune, then the aggregate over the objects and over the pairs."""
    ok = [r for r in res if "error" not in r]
    head = ("tune", "objs", "decl", "pair", "genrow", "pairs", "unmat", "chk")
    print("%-44s %4s %4s %4s %6s %6s %6s %6s" % head)
    for r in sorted(ok, key=lambda x: (x["editor"], x["tune"])):
        print(
            "%-44s %4d %4d %4d %6d %6d %6d %3d/%-2d"
            % (
                r["tune"][-44:],
                r["objects"],
                r["declared"],
                r["paired"],
                r["gen_row"],
                r["prog_pairs"],
                r["pairs_unmatched"],
                r["verified"],
                r["checkable"],
            )
        )
    for r in res:
        if "error" in r:
            print("%-44s FAILED %s" % (r["tune"][-44:], r["error"]))
    _aggregate(ok)


def _aggregate(ok):
    """The object census and the totals, per editor."""
    for editor in sorted({r["editor"] for r in ok}):
        rows = [r for r in ok if r["editor"] == editor]
        per, tot = collections.Counter(), collections.Counter()
        for r in rows:
            for name, (dec, pair, strict, kinds) in r["per_object"].items():
                tot[name] += 1
                per[(name, "d")] += int(dec)
                per[(name, "p")] += int(pair)
                per[(name, "s")] += int(strict)
                per[(name, "t")] += int("row" in kinds)
        print("\n== %s, %d tunes ==" % (editor, len(rows)))
        head = ("object", "tunes", "declared", "paired", "strict", "gen-row")
        print("%-16s %6s %9s %8s %8s %8s" % head)
        for name in sorted(tot):
            print(
                "%-16s %6d %9d %8d %8d %8d"
                % (
                    name,
                    tot[name],
                    per[(name, "d")],
                    per[(name, "p")],
                    per[(name, "s")],
                    per[(name, "t")],
                )
            )
        for k in (
            "objects",
            "declared",
            "paired",
            "paired_strict",
            "gen_row",
            "prog_pairs",
            "pairs_unmatched",
            "resolved",
        ):
            print("%-16s %d" % (k, sum(r[k] for r in rows)))
        sound = [r for r in rows if r["key_sound"] is not None]
        print(
            "address maps %d/%d bytes-agree, key re-derivation sound %d/%d"
            % (
                sum(r["verified"] for r in rows),
                sum(r["checkable"] for r in rows),
                sum(1 for r in sound if r["key_sound"]),
                len(sound),
            )
        )
        _nodes(rows)


def _nodes(rows):
    """The editor's own nodes and emits, split by whether a pair names their object."""
    nodes, emits = collections.Counter(), collections.Counter()
    for r in rows:
        for (name, state, _arr), (cnt, n) in r["emits_by_object"]:
            key = name if name.startswith("player") else state
            nodes[key] += cnt
            emits[key] += n
    print("\n%-12s %8s %10s" % ("editor node", "nodes", "emits"))
    for k in sorted(nodes):
        print("%-12s %8d %10d" % (k, nodes[k], emits[k]))


def main(argv):
    """Measure the correspondence over the corpus and print the aggregate.

    ``argv[1]`` caps the GoatTracker set (0 for all of out/gt_scan.json); ``argv[2:]``
    are DefMON relpaths, which carry their own object map and no re-derived node set."""
    limit = int(argv[1]) if len(argv) > 1 else 0
    rels = _scan()
    items = [("gt", r) for r in (rels[:limit] if limit else rels)]
    items += [("dm", r) for r in argv[2:]]
    with mp.Pool(WORKERS) as pool:
        res = list(pool.imap_unordered(_run, items, chunksize=1))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1))
    report(res)


if __name__ == "__main__":
    main(sys.argv)
