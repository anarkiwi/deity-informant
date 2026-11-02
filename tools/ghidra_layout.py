#!/usr/bin/env python3
"""Apply a layout map to a Ghidra program headlessly and read back what Ghidra holds.

``python tools/ghidra_layout.py SIDFILE LAYOUT.json [--out DUMP.json]`` imports the tune's
loaded body as Raw Binary (6510), applies the map's plan with ``ApplyLayout.java`` and checks
the dump against the plan; ``--selftest`` does the same for a synthetic fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
import vtools  # noqa: E402
from deity_informant import layout  # noqa: E402

IMAGE = "di-ghidra"
SCRIPTS = vtools.ROOT / "ghidra" / "6510" / "headless"
GHIDRA_IN_IMAGE = "/opt/ghidra"
TIMEOUT = 1800
ORDER = ("address_table", "pointer", "word", "record", "array", "parallel_array")
SIZE = {"byte": 1, "word": 2, "pointer": 2}
GHIDRA_NAME = {"byte": "byte", "word": "word", "pointer": "pointer16"}
PASS = "GHIDRA LAYOUT SELFTEST: PASS"


def _atom(width):
    """``(type, count)`` of a ``width``-byte scalar."""
    return ("byte", 1) if width == 1 else ("word", 1) if width == 2 else ("byte", width)


class _Plan:
    def __init__(self):
        self.out = {"types": [], "data": [], "labels": [], "refs": [], "comments": []}

    def size(self, t):
        return SIZE.get(t) or next(s["size"] for s in self.out["types"] if s["name"] == t)

    def struct(self, name, size, fields):
        """A struct type named ``name`` (made unique) of ``fields`` ``(offset, type, count)``."""
        names = {s["name"] for s in self.out["types"]}
        base, k = name[:-2], 2
        while name in names:
            name, k = f"{base}_{k}_t", k + 1
        fs = [{"offset": o, "type": t, "count": n, "name": f"f{o}"} for o, t, n in fields]
        self.out["types"].append({"name": name, "size": size, "fields": fs})
        return name

    def element(self, name, stride, fields):
        """``(type, per-element count)``: the lone field itself when it fills the stride."""
        if (
            len(fields) == 1
            and fields[0][0] == 0
            and self.size(fields[0][1]) * fields[0][2] == stride
        ):
            return fields[0][1], fields[0][2]
        return self.struct(name, stride, fields), 1

    def data(self, addr, t, count, name):
        self.out["data"].append({"addr": addr, "type": t, "count": count, "name": name})

    def label(self, addr, name):
        self.out["labels"].append({"addr": addr, "name": name})
        return name

    def ref(self, frm, to):
        self.out["refs"].append({"from": frm, "to": to})


def _table(p, r, image, load):
    (lo, hi), b = r["words"][0], r["base"]
    ent = layout.table_entries(r)
    inside = all(0 <= a - load < len(image) for e in ent for a in e)
    if not r.get("written") and inside:
        for a, c in ent:
            to = image[a - load] | image[c - load] << 8
            p.ref(a, to)
            p.ref(c, to)
    if hi == lo + 1:
        name = p.label(b, f"tab_{b:04X}")
        if ent and r["stride"] >= 2 and not r.get("io"):
            t, n = p.element(name + "_t", r["stride"], [(0, "pointer", 1)])
            p.data(ent[0][0], t, n * len(ent), name)
        return
    for half, off in (("lo", lo), ("hi", hi)):
        name = p.label(b + off, f"tab_{b:04X}_{half}")
        if ent and not r.get("io"):
            t, n = p.element(name + "_t", r["stride"], [(0, "byte", 1)])
            p.data(ent[0][half == "hi"], t, n * len(ent), name)


def plan(m, image, load):
    """The Ghidra application plan (``types``, ``data``, ``labels``, ``refs``, ``comments``)
    of layout map ``m`` for ``image`` loaded at ``load``; regions in :data:`ORDER`, by base."""
    p = _Plan()
    for r in sorted(m["regions"], key=lambda r: (ORDER.index(r["kind"]), r["base"])):
        k, b, io = r["kind"], r["base"], r.get("io", False)
        if k == "address_table":
            _table(p, r, image, load)
        elif k in ("pointer", "word"):
            name = p.label(b, f"{'ptr' if k == 'pointer' else 'word'}_{b:04X}")
            for f in r["fields"] if not io else ():
                o, w = f["offset"], f["width"]
                t, n = ("pointer", 1) if k == "pointer" and (o, w) == (0, 2) else _atom(w)
                p.data(b + o, t, n, name)
            for to in r.get("targets", []):
                p.ref(b, to)
        elif k in ("record", "array"):
            name = p.label(b, f"{'rec' if k == 'record' else 'arr'}_{b:04X}")
            if not io:
                fs = [(f["offset"], *_atom(f["width"])) for f in r["fields"]]
                if k == "record":
                    t, n = p.struct(name + "_t", r["stride"], fs), 1
                else:
                    t, n = p.element(name + "_t", r["stride"], fs)
                p.data(layout.elements(r)[0], t, n * r["count"], name)
        else:
            p.label(b, f"par_{b:04X}")
            members = ", ".join(f"${b + f['offset']:04X}" for f in r["fields"])
            p.out["comments"].append({"addr": b, "text": f"parallel array: {members}"})
    return p.out


def _headless(ghidra, work, scripts, load):
    """The ``analyzeHeadless`` argv of Ghidra at ``ghidra`` over work dir ``work`` (``body.bin``,
    ``plan.json`` in, ``dump.json`` out) with ``ApplyLayout.java`` from ``scripts``."""
    return [
        f"{ghidra}/support/analyzeHeadless",
        work,
        "layout",
        "-import",
        f"{work}/body.bin",
        "-loader",
        "BinaryLoader",
        "-loader-baseAddr",
        f"0x{load:04X}",
        "-processor",
        "6510:LE:16:default",
        "-noanalysis",
        "-scriptPath",
        scripts,
        "-postScript",
        "ApplyLayout.java",
        f"{work}/plan.json",
        f"{work}/dump.json",
        "-deleteProject",
    ]


def execute(body, load, pl, image=IMAGE, timeout=TIMEOUT):
    """Ghidra's dump after applying plan ``pl`` to ``body`` loaded at ``load``: run in place
    when ``GHIDRA_INSTALL_DIR`` holds Ghidra, else in ``image`` with a work dir under out/."""
    ghidra = os.environ.get("GHIDRA_INSTALL_DIR", "")
    local = Path(ghidra, "support", "analyzeHeadless").is_file()
    work = (
        Path(tempfile.mkdtemp(prefix="ghidra-layout-"))
        if local
        else vtools.workdir("ghidra-layout")
    )
    try:
        (work / "body.bin").write_bytes(bytes(body))
        (work / "plan.json").write_text(json.dumps(pl), encoding="utf-8")
        if local:
            argv = _headless(ghidra, str(work), str(SCRIPTS), load)
        else:
            argv = vtools.docker_cmd(
                _headless(GHIDRA_IN_IMAGE, "/work", "/scripts", load),
                mounts=[(work, "/work", False), (SCRIPTS, "/scripts", True)],
                env={"HOME": "/work", "JAVA_TOOL_OPTIONS": "-Duser.home=/work"},
                image=image,
            )
        r = subprocess.run(
            argv, capture_output=True, encoding="latin-1", timeout=timeout, check=False
        )
        dump = work / "dump.json"
        if r.returncode or not dump.exists():
            raise RuntimeError(f"analyzeHeadless failed ({r.returncode}):\n{r.stdout[-4000:]}")
        return json.loads(dump.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run(sid_path, layout_json_path, image=IMAGE, timeout=TIMEOUT):
    """``(plan, dump)`` of the layout map at ``layout_json_path`` applied to the loaded body
    of the PSID/RSID file ``sid_path``."""
    load, _, body = vtools.sid_body(Path(sid_path).read_bytes())
    body = body[: 0x10000 - load]
    pl = plan(json.loads(Path(layout_json_path).read_text(encoding="utf-8")), body, load)
    return pl, execute(body, load, pl, image, timeout)


def _key(what, key):
    return what, json.dumps(key)


def check(pl, dump):
    """Mismatches between plan ``pl`` and Ghidra's ``dump``, less the dump's ``skipped``."""
    skip = {_key(s["what"], s["key"]) for s in dump["skipped"]}
    names = {t["name"] for t in pl["types"]}
    size = {**SIZE, **{t["name"]: t["size"] for t in pl["types"]}}

    def gname(t):
        return t if t in names else GHIDRA_NAME[t]

    bad = []
    got = {t["name"]: t for t in dump["types"]}
    for t in pl["types"]:
        if _key("type", t["name"]) in skip:
            continue
        g = got.get(t["name"])
        if g is None or g["size"] != t["size"]:
            bad.append(f"type {t['name']}: want size {t['size']}, got {g and g['size']}")
            continue
        want = {
            (f["offset"], f["name"], gname(f["type"]), f["count"])
            for f in t["fields"]
            if _key("field", [t["name"], f["offset"]]) not in skip
        }
        have = {(f["offset"], f["name"], f["type"], f["count"]) for f in g["fields"]}
        bad += [f"type {t['name']}: field {w} missing" for w in sorted(want - have)]
        bad += [f"type {t['name']}: field {h} unplanned" for h in sorted(have - want)]
    data = {d["addr"]: d for d in dump["data"]}
    for d in pl["data"]:
        if _key("data", d["addr"]) in skip:
            continue
        want = (gname(d["type"]), d["count"], size[d["type"]] * d["count"])
        g = data.get(d["addr"])
        have = g and (g["type"], g["count"], g["length"])
        if have != want:
            bad.append(f"data {d['name']}@{d['addr']:04X}: want {want}, got {have}")
    labels = {(x["addr"], x["name"]) for x in dump["labels"]}
    for x in pl["labels"]:
        if (x["addr"], x["name"]) not in labels and _key(
            "label", [x["addr"], x["name"]]
        ) not in skip:
            bad.append(f"label {x['name']}@{x['addr']:04X} missing")
    refs = {(x["from"], x["to"]) for x in dump["refs"]}
    for x in pl["refs"]:
        if (x["from"], x["to"]) not in refs and _key("ref", [x["from"], x["to"]]) not in skip:
            bad.append(f"ref {x['from']:04X}->{x['to']:04X} missing")
    comments = {x["addr"]: x["text"] for x in dump["comments"]}
    for x in pl["comments"]:
        if comments.get(x["addr"]) != x["text"] and _key("comment", x["addr"]) not in skip:
            bad.append(f"comment @{x['addr']:04X} missing")
    return bad


FIXTURE_LOAD = 0x1000


def fixture():
    """``(image, map)``: a synthetic 128-byte program at $1000 and a map of every kind, with a
    word overlapping a table and an array whose field outruns its stride."""
    img = bytearray(0x80)
    img[0x00:0x06] = bytes([0x10, 0x18, 0x20, 0x10, 0x10, 0x10])
    img[0x08:0x0C] = bytes([0x30, 0x10, 0x38, 0x10])
    one = [{"offset": 0, "width": 1}]
    regions = [
        {"kind": "array", "base": 0x1060, "stride": 1, "count": 8, "extent": [0x1060, 0x1067]}
        | {"fields": one},
        {"kind": "array", "base": 0x1068, "stride": 4, "count": 3, "extent": [0x106C, 0x1074]}
        | {"fields": one},
        {"kind": "array", "base": 0x1020, "stride": 1, "count": 4, "extent": [0x1020, 0x1024]}
        | {"fields": [{"offset": 0, "width": 2}]},
        {"kind": "word", "base": 0x1040, "size": 2, "fields": [{"offset": 0, "width": 2}]},
        {"kind": "word", "base": 0x100A, "size": 2, "fields": [{"offset": 0, "width": 2}]},
        {"kind": "pointer", "base": 0xFB, "size": 2, "fields": [{"offset": 0, "width": 2}]}
        | {"targets": [0x1010, 0x1020]},
        {"kind": "pointer", "base": 0xFD, "size": 2, "fields": [{"offset": 0, "width": 2}]}
        | {"target_range": [0x2000, 0x3000]},
        {"kind": "address_table", "base": 0x1000, "stride": 1, "count": 3}
        | {"extent": [0x1000, 0x1005], "words": [[0, 3]], "pointers": [0xFB]}
        | {"fields": [{"offset": 0, "width": 1}, {"offset": 3, "width": 1}]},
        {"kind": "address_table", "base": 0x1008, "stride": 2, "count": 2}
        | {"extent": [0x1008, 0x100B], "words": [[0, 1]], "pointers": [0xFB]}
        | {"fields": [{"offset": 0, "width": 2}]},
        {"kind": "record", "base": 0x1050, "stride": 3, "count": 4, "extent": [0x1050, 0x105B]}
        | {"fields": [{"offset": 0, "width": 1}, {"offset": 1, "width": 2}]},
        {"kind": "parallel_array", "base": 0x1078, "stride": 1, "count": 4}
        | {"extent": [0x1078, 0x107F], "words": [[0, 4]]}
        | {"fields": [{"offset": 0, "width": 1}, {"offset": 4, "width": 1}]},
        {"kind": "array", "base": 0x1078, "stride": 1, "count": 4, "extent": [0x1078, 0x107B]}
        | {"fields": one},
        {"kind": "array", "base": 0x107C, "stride": 1, "count": 4, "extent": [0x107C, 0x107F]}
        | {"fields": one},
        {"kind": "record", "base": 0xD400, "stride": 7, "count": 3, "extent": [0xD400, 0xD414]}
        | {"fields": [{"offset": 0, "width": 1}, {"offset": 4, "width": 1}], "io": True},
    ]
    return bytes(img), {"regions": regions, "conflicts": []}


EXPECT = {
    "types": {
        "rec_1050_t": (3, {(0, "f0", "byte", 1), (1, "f1", "word", 1)}),
        "arr_1068_t": (4, {(0, "f0", "byte", 1)}),
        "arr_1020_t": (1, set()),
    },
    "data": {
        0x1000: ("byte", 3, 3),
        0x1003: ("byte", 3, 3),
        0x1008: ("pointer16", 2, 4),
        0x1040: ("word", 1, 2),
        0x00FB: ("pointer16", 1, 2),
        0x00FD: ("pointer16", 1, 2),
        0x1050: ("rec_1050_t", 4, 12),
        0x1020: ("arr_1020_t", 4, 4),
        0x1060: ("byte", 8, 8),
        0x106C: ("arr_1068_t", 3, 12),
        0x1078: ("byte", 4, 4),
        0x107C: ("byte", 4, 4),
    },
    "labels": {
        (0x1000, "tab_1000_lo"),
        (0x1003, "tab_1000_hi"),
        (0x1008, "tab_1008"),
        (0x1040, "word_1040"),
        (0x100A, "word_100A"),
        (0x00FB, "ptr_00FB"),
        (0x00FD, "ptr_00FD"),
        (0x1050, "rec_1050"),
        (0x1020, "arr_1020"),
        (0x1060, "arr_1060"),
        (0x1068, "arr_1068"),
        (0x1078, "par_1078"),
        (0x1078, "arr_1078"),
        (0x107C, "arr_107C"),
        (0xD400, "rec_D400"),
    },
    "refs": {
        (0x00FB, 0x1010),
        (0x00FB, 0x1020),
        (0x1000, 0x1010),
        (0x1003, 0x1010),
        (0x1001, 0x1018),
        (0x1004, 0x1018),
        (0x1002, 0x1020),
        (0x1005, 0x1020),
        (0x1008, 0x1030),
        (0x1009, 0x1030),
        (0x100A, 0x1038),
        (0x100B, 0x1038),
    },
    "skipped": {_key("data", 0x100A), _key("field", ["arr_1020_t", 0])},
    "comments": {0x1078: "parallel array: $1078, $107C"},
}


def expect_mismatches(dump, expect=None):
    """Differences between ``dump`` and the hand-written fixture expectation ``expect``."""
    expect = expect or EXPECT
    bad = []
    types = {t["name"]: t for t in dump["types"]}
    for name, (size, fields) in expect["types"].items():
        t = types.get(name)
        have = t and (
            t["size"],
            {(f["offset"], f["name"], f["type"], f["count"]) for f in t["fields"]},
        )
        if have != (size, fields):
            bad.append(f"type {name}: want {(size, fields)}, got {have}")
    data = {d["addr"]: (d["type"], d["count"], d["length"]) for d in dump["data"]}
    if data != expect["data"]:
        bad.append(f"data: want {expect['data']}, got {data}")
    labels = {(x["addr"], x["name"]) for x in dump["labels"]}
    if labels != expect["labels"]:
        bad.append(f"labels: want {sorted(expect['labels'])}, got {sorted(labels)}")
    refs = {(x["from"], x["to"]) for x in dump["refs"]}
    if not expect["refs"] <= refs or any(f == 0xFD for f, _ in refs):
        bad.append(f"refs: want {sorted(expect['refs'])}, got {sorted(refs)}")
    skipped = {_key(s["what"], s["key"]) for s in dump["skipped"]}
    if skipped != expect["skipped"]:
        bad.append(f"skipped: want {expect['skipped']}, got {skipped}")
    comments = {x["addr"]: x["text"] for x in dump["comments"]}
    if comments != expect["comments"]:
        bad.append(f"comments: want {expect['comments']}, got {comments}")
    return bad


def selftest(image=IMAGE, timeout=TIMEOUT):
    """Mismatches of the fixture run: plan against dump, and dump against :data:`EXPECT`."""
    img, m = fixture()
    pl = plan(m, img, FIXTURE_LOAD)
    dump = execute(img, FIXTURE_LOAD, pl, image, timeout)
    return check(pl, dump) + expect_mismatches(dump)


def summary(pl, dump):
    """One line: planned, applied and skipped counts per item kind."""
    sk = {}
    for s in dump["skipped"]:
        sk[s["what"]] = sk.get(s["what"], 0) + 1
    return (
        " ".join(
            f"{k}={len(pl[k])}(skipped {sk.get(w, 0)})"
            for k, w in (("types", "type"), ("data", "data"), ("labels", "label"), ("refs", "ref"))
        )
        + f" fields_skipped={sk.get('field', 0)}"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sid", nargs="?")
    ap.add_argument("layout", nargs="?")
    ap.add_argument("--out", help="write the dump (with the plan) as JSON")
    ap.add_argument("--image", default=IMAGE)
    ap.add_argument("--timeout", type=int, default=TIMEOUT)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        bad = selftest(args.image, args.timeout)
        print("\n".join(bad) or PASS)
        return 1 if bad else 0
    if not (args.sid and args.layout):
        ap.error("SIDFILE and LAYOUT.json are required without --selftest")
    pl, dump = run(args.sid, args.layout, args.image, args.timeout)
    bad = check(pl, dump)
    if args.out:
        Path(args.out).write_text(
            json.dumps({"plan": pl, "dump": dump}, indent=1), encoding="utf-8"
        )
    print(summary(pl, dump))
    for s in dump["skipped"]:
        print(f"skipped {s['what']} {s['key']}: {s['reason']}")
    print("\n".join(bad) or "consistent")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
