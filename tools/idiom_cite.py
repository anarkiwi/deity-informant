"""Every catalog row's two cites, joined from the completeness gate and the anchors.

A row's warrant is a canonical-source label and an exemplar address naming one code:
``idiom_cover`` records the sites each row is witnessed at, ``source_anchor`` binds
source labels to exemplar addresses, and this joins them into the Rows table.
"""

import argparse
import json
import re
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

import _sweep
import exemplars

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/idiom_cite.py                     # the Rows table, from out/*.json
  python tools/idiom_cite.py --show-uncited      # rows no canonical source reaches"""

STUDY = ROOT / "docs" / "follin-dispatch-study.md"
OP_ROW = re.compile(r"^\| \$([0-9A-F]{2}) \| \$([0-9A-F]{4}) \| \S+ \| (\w+)", re.M)
MIRRORS = (0x00, 0x0F, 0x1E)  # v1 handlers; v2/v3 are displaced copies (study section 3)
SPAN = 0x100  # bytes a label may be cited across: past that the binding says nothing


def anchor_index(runs, key):
    """``(addresses, rows)`` of one family's citable anchors, ascending by address."""
    fam = exemplars.ANCHORS[key]
    got = next(r for r in runs if r["family"] == key)
    rows = sorted((r for r in got["rows"] if r["run"] >= fam.min_run), key=lambda r: r["addr"])
    return [r["addr"] for r in rows], rows


def follin_index():
    """The study's operator handlers as anchors: op byte -> handler address, v1 only."""
    rows = [
        {"label": "op $%s %s" % (op, name), "addr": int(addr, 16), "line": 0, "run": 0}
        for op, addr, name in OP_ROW.findall(STUDY.read_text(encoding="utf-8"))
    ]
    rows.sort(key=lambda r: r["addr"])
    return [r["addr"] for r in rows], rows


def cite_at(index, pc):
    """The nearest label at or below ``pc``, if ``pc`` sits inside anchored territory."""
    addrs, rows = index
    k = bisect_right(addrs, pc) - 1
    if k < 0 or k + 1 >= len(addrs) or pc - addrs[k] > SPAN:
        return None
    return {**rows[k], "delta": pc - addrs[k]}


def indexes(runs):
    """One address index per anchored family, strongest binding first."""
    out = {}
    for key in exemplars.ANCHORS:
        try:
            out[key] = follin_index() if key == "follin" else anchor_index(runs, key)
        except StopIteration:
            continue
    return out


def witnesses(cover):
    """``row id -> [(family rank, family, tune, site)]`` over the anchored exemplars.

    An anchor binds one source to one image, so only the exemplar it was computed
    against can carry a cite: a second exemplar of the family is a different build."""
    out = defaultdict(list)
    order = {k: i for i, k in enumerate(exemplars.ANCHORS)}
    for row in cover["rows"]:
        fam = exemplars.FAMILY_OF.get(row["tune"])
        if fam not in order or exemplars.BY_KEY[fam].tunes[0] != row["tune"]:
            continue
        for rid, sites in row.get("row_sites", {}).items():
            out[rid] += [(order[fam], fam, row["tune"], pc) for pc in sites]
    return out


def block(seats, pc):
    """``(start, end)`` of the labelled block ``pc`` opens: the range disasm_tune reads."""
    k = bisect_right(seats, pc)
    return pc, seats[k] if k < len(seats) and seats[k] - pc <= SPAN else None


def pick(hits, index, seats):
    """The strongest cite over a row's witnesses: the best-anchored family, tightest label.

    Follin's mirror copies are the same handler code at a fixed displacement, so a
    v2/v3 site cites the v1 handler the study names."""
    cands = []
    for rank, fam, tune, pc in hits:
        if fam not in index:
            continue
        for off in MIRRORS if fam == "follin" else (0,):
            got = cite_at(index[fam], pc - off)
            if got is not None:
                cands.append((rank, got["delta"] + off, pc, fam, tune, got))
                break
    if not cands:
        return None
    rank, delta, pc, fam, tune, got = min(cands, key=lambda c: c[:3])
    start, end = block(seats[tune], pc)
    return {
        "family": fam,
        "source": exemplars.ANCHORS[fam].source,
        "label": got["label"],
        "line": got["line"],
        "delta": delta,
        "run": got["run"],
        "tune": tune,
        "start": start,
        "end": end,
    }


def fmt_cite(cite):
    """``file:line label+delta``: the canonical cite as the catalog prints it.

    The delta is bytes past the label's bound address, since the seat the row is
    witnessed at is an image address and the label is where the source names it."""
    if cite is None:
        return "—"
    at = "+$%02X" % cite["delta"] if cite["delta"] else ""
    where = ":%d" % cite["line"] if cite["line"] else ""
    return "`%s%s` %s%s" % (Path(cite["source"]).name, where, cite["label"], at)


def fmt_exemplar(cite):
    """``tune $start-$end``: the disasm_tune range the same code sits at."""
    if cite is None:
        return "—"
    span = "$%04X" % cite["start"] + ("-$%04X" % cite["end"] if cite["end"] else "")
    return "%s %s" % (_sweep.tune_name(cite["tune"]), span)


def fmt_families(fams, total):
    """The families spelling a row: named while few, counted once they are most of them."""
    return ", ".join(sorted(fams)) if len(fams) <= 3 else "%d of %d" % (len(fams), total)


def table(rows):
    """The Rows table of docs/idiom-catalog.md, ready to paste."""
    out = [
        "| id | normal form | families | canonical cite | exemplar cite | nodes | tunes |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for r in rows:
        out.append(
            "| `%s` | %s | %s | %s | %s | %d | %d |"
            % (
                r["id"],
                r["form"],
                r["families_text"],
                r["cite_text"],
                r["exemplar_text"],
                r["nodes"],
                r["tunes"],
            )
        )
    return "\n".join(out)


def build(cover, runs):
    """One record per catalog row: its counts, the families spelling it, and its two cites."""
    from deity_informant import idioms

    index, hits = indexes(runs), witnesses(cover)
    seats = {r["tune"]: r["seats"] for r in cover["rows"] if "seats" in r}
    counts, tunes, fams = Counter(), Counter(), defaultdict(set)
    for row in cover["rows"]:
        for rid, n in row.get("rows", {}).items():
            counts[rid] += n
            tunes[rid] += 1
            fams[rid].add(exemplars.FAMILY_OF.get(row["tune"], "?"))
    out = []
    for row in idioms.ROWS:
        cite = pick(hits.get(row.id, []), index, seats)
        out.append(
            {
                "id": row.id,
                "form": row.form,
                "nodes": counts[row.id],
                "tunes": tunes[row.id],
                "families": sorted(fams[row.id]),
                "families_text": fmt_families(fams[row.id], len(exemplars.FAMILIES)),
                "cite": cite,
                "cite_text": fmt_cite(cite),
                "exemplar_text": fmt_exemplar(cite),
            }
        )
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--cover", default=str(ROOT / "out" / "idiom_cover.json"))
    ap.add_argument("--anchors", default=str(ROOT / "out" / "source_anchor.json"))
    ap.add_argument("--show-uncited", action="store_true", help="rows with no canonical cite")
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "idiom_cite.json"))
    args = ap.parse_args()

    cover = json.loads(Path(args.cover).read_text(encoding="utf-8"))
    runs = json.loads(Path(args.anchors).read_text(encoding="utf-8"))
    rows = build(cover, runs)
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(table(rows))
    cited = [r for r in rows if r["cite"]]
    print(
        "\n%d of %d rows carry a canonical cite (%s); %d exemplars, %d families"
        % (
            len(cited),
            len(rows),
            ", ".join("%s %d" % kv for kv in Counter(r["cite"]["family"] for r in cited).items()),
            len(exemplars.EXEMPLARS),
            len(exemplars.FAMILIES),
        )
    )
    if args.show_uncited:
        for r in rows:
            if not r["cite"]:
                print("  %-16s families: %s" % (r["id"], ", ".join(r["families"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
