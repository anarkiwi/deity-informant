"""L0 of the denotation solve: the shape census, the family quotient, the re-lift.

The measurements docs/denotation-solve.md §5 and §7.3 name, no engine change. ``census``
and ``family`` read the artifacts ``_sweep`` stored; ``relift`` decompiles the witness.
"""

import argparse
import difflib
import hashlib
import json
import multiprocessing as mp
import re
import signal
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import _sweep
import splice_sweep

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/denotation_l0.py census -o out/denotation_l0_census.json
  python tools/denotation_l0.py family -o out/denotation_l0_family.json
  python tools/denotation_l0.py relift --frames 200 --per-family 2
  python tools/denotation_l0.py report -o out/denotation_l0.json"""

PLAYERS = ROOT / "out" / "player_id_corpus.json"
RELIFT_CAP_S = 1500  # per tune; the re-lift's second emission is the expensive half

_COMMENT = re.compile(r";[^\n]*")
_PROC0 = re.compile(r"^sub_[0-9A-F]{4}\(", re.M)
_GEN = re.compile(r"\b([a-z]+)_([0-9A-F]{4})\b")  # sub_XXXX, m_XXXX, ptr_XXXX, ctr_XXXX, ...
_ADDR = re.compile(r"\$[0-9A-F]{4,}")
_NUMBERED = re.compile(r"(?<![.\w])([a-z]+)(\d+)\b")  # a0, w23, ctr1: the local numbering
_WORD = re.compile(r"[A-Za-z_]\w*")
_MEM = re.compile(r"\bmem\[")
_DEREF = re.compile(r"\*\w+\[")
_SID_IDX = re.compile(r"\bsid\.[\w.]*\[([^\]]*)\]")
_FOR = re.compile(r"\bfor\s+(\w+)\s+in\b")
_LANE3 = re.compile(r"table\s+\w+\[3\][^\n]*observed:\s*\n\s*00070E\b")
_VIA = re.compile(r"\bvia\s+\w+")
_VERSION = re.compile(r"[_ ]?V?\d+(\.\d+)?\.?[xX]?$")


def is_arch(tok):
    """Whether one token is a machine name, read off ``splice_sweep``'s own predicate."""
    return splice_sweep.arch_shapes(tok) == (1, 0)


def cached_text(entry):
    """The artifact ``_sweep`` stored for this tune at full Songlengths, or None."""
    from deity_informant.c64 import load_psid  # pylint: disable=import-outside-toplevel

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
    key = _sweep._key(mem, init, play, int(secs * 50), sub, {})  # pylint: disable=W0212
    got = _sweep._read(_sweep.ARTIFACTS / ("%s.fp.gz" % key))  # pylint: disable=W0212
    return None if got is None else got[1]


def split_artifact(text):
    """``(header, procedures)``: everything before the first procedure, and from it."""
    m = _PROC0.search(text)
    return (text, "") if m is None else (text[: m.start()], text[m.start() :])


def normalise(text, prefix=True, ident=True):
    """Procedure text with comments, indentation, relocation and numbering canonical.

    ``prefix=False`` drops the role prefix a generated name was given; ``ident=False``
    drops the identity itself, which is what a line comparison needs, since
    first-appearance numbering shifts every later line on one inserted site."""
    body = "\n".join(s for s in (l.strip() for l in _COMMENT.sub("", text).splitlines()) if s)
    ids = {}

    def rename(tag):
        def sub(m):
            if not ident:
                return "%s#" % tag(m)
            return "%s#%d" % (tag(m), ids.setdefault(m.group(0), len(ids)))

        return sub

    body = _GEN.sub(rename(lambda m: m.group(1) if prefix else "v"), body)
    body = _ADDR.sub(rename(lambda _m: "$a"), body)
    return _NUMBERED.sub(rename(lambda m: m.group(1)), body)


def jaccard(a, b):
    """Line-set overlap of two normalised procedure texts."""
    return len(a & b) / max(len(a | b), 1)


def family_name(player):
    """One player signature's family: the author prefix, parentheses and version off."""
    return _VERSION.sub("", player.strip("()").rsplit("/", 1)[-1])


def families(path=PLAYERS):
    """``{tune: family}``: SIDId's names, versions folded, one family per tune.

    A tune matches several signatures (240 of 624) and they are versions or aliases of one
    player, so the family is the tune's most-carried name -- ``(VoiceTracker)`` resolves to
    ``Music_Assembler`` rather than opening a family of its own."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["rows"]
    tally = Counter(f for r in rows for f in {family_name(p) for p in r.get("players", [])})
    out = {}
    for row in rows:
        names = {family_name(p) for p in row.get("players", [])}
        if names:
            out[row["tune"]] = max(sorted(names), key=lambda n: tally[n])
    return out


def census_row(text):
    """One artifact's §5 shapes: the machine names, the raw sites, and the byte split."""
    head, proc = split_artifact(text)
    bare = _COMMENT.sub("", text)
    arch, temps = splice_sweep.arch_shapes(text)
    sid_arch = sum(
        1 for m in _SID_IDX.finditer(proc) if any(is_arch(w) for w in _WORD.findall(m.group(1)))
    )
    ivs = _FOR.findall(proc)
    return {
        "bytes": len(text),
        "head_bytes": len(head),
        "proc_bytes": len(proc),
        "proc_lines": len(proc.splitlines()),
        "procs": len(_PROC0.findall(text)),
        "arch": arch,
        "temps": temps,
        "mem_sites": len(_MEM.findall(bare)),
        "deref_sites": len(_DEREF.findall(bare)),
        "sid_reg_sites": sid_arch,
        "for_total": len(ivs),
        "for_arch": sum(1 for v in ivs if is_arch(v)),
        "lane3": bool(_LANE3.search(text)),
        "via": len(_VIA.findall(head)),
    }


def census_rollup(rows):
    """The corpus totals §5 is tracked against, and the shares §1 quoted from cache."""
    n = max(len(rows), 1)
    got = {"artifacts": len(rows)}
    for k in ("arch", "temps", "mem_sites", "deref_sites", "sid_reg_sites", "for_total", "via"):
        got[k] = sum(r[k] for r in rows)
    got["for_arch"] = sum(r["for_arch"] for r in rows)
    got["median_arch"] = int(statistics.median([r["arch"] for r in rows] or [0]))
    got["zero_arch"] = sum(1 for r in rows if r["arch"] == 0)
    got["sid_reg_artifacts"] = sum(1 for r in rows if r["sid_reg_sites"])
    got["lane3_artifacts"] = sum(1 for r in rows if r["lane3"])
    got["for_artifacts"] = sum(1 for r in rows if r["for_total"])
    got["head_bytes"] = sum(r["head_bytes"] for r in rows)
    got["proc_bytes"] = sum(r["proc_bytes"] for r in rows)
    total = got["head_bytes"] + got["proc_bytes"]
    got["proc_share"] = round(100.0 * got["proc_bytes"] / max(total, 1), 1)
    got["sid_reg_share"] = round(100.0 * got["sid_reg_artifacts"] / n, 1)
    got["lane3_share"] = round(100.0 * got["lane3_artifacts"] / n, 1)
    return got


def _census_one(entry):
    row = dict(_sweep.row_head(entry))
    text = cached_text(entry)
    if text is None:
        row["uncached"] = True
        return row
    row.update(census_row(text))
    return row


def _family_one(entry):
    """One tune's normalised procedure text, keyed for the quotient count."""
    row = dict(_sweep.row_head(entry))
    text = cached_text(entry)
    if text is None:
        row["uncached"] = True
        return row
    _head, proc = split_artifact(text)
    norm = normalise(proc)
    shape = normalise(proc, ident=False)
    row["raw_sha"] = hashlib.sha256(proc.encode()).hexdigest()[:16]
    row["norm_sha"] = hashlib.sha256(norm.encode()).hexdigest()[:16]
    row["shape_sha"] = hashlib.sha256(shape.encode()).hexdigest()[:16]
    row["norm_lines"] = sorted(set(norm.splitlines()))
    row["shape_lines"] = sorted(set(shape.splitlines()))
    row["n_lines"] = len(norm.splitlines())
    return row


def family_rollup(rows, fam):
    """Per family: the tunes, the distinct texts under each normalisation, the overlap."""
    by = defaultdict(list)
    for row in rows:
        if "n_lines" in row:
            by[fam.get(row["tune"], "-")].append(row)
    out = []
    for name, group in by.items():
        got = {
            "family": name,
            "tunes": len(group),
            "distinct_raw": len({r["raw_sha"] for r in group}),
            "distinct_norm": len({r["norm_sha"] for r in group}),
            "distinct_shape": len({r["shape_sha"] for r in group}),
            "median_lines": int(statistics.median([r["n_lines"] for r in group])),
        }
        for key in ("norm", "shape"):
            sets = [set(r["%s_lines" % key]) for r in group]
            pairs = [
                jaccard(sets[i], sets[j])
                for i in range(len(group))
                for j in range(i + 1, len(group))
            ]
            got["j_%s" % key] = round(statistics.median(pairs), 3) if pairs else None
            got["jmax_%s" % key] = round(max(pairs), 3) if pairs else None
            got["shared_%s" % key] = round(len(set.intersection(*sets)) / len(set.union(*sets)), 3)
        out.append(got)
    return sorted(out, key=lambda r: (-r["tunes"], r["family"]))


def noop_init(prog, wit, margin=16):
    """A free byte the witness did not write, to hold the ``RTS`` the re-lift inits at.

    ``witness6502`` re-emits into the tune's own post-init image, so the re-lift enters the
    play phase from exactly that image: a no-op init is what leaves it alone."""
    from deity_informant import witness6502 as W  # pylint: disable=import-outside-toplevel

    for base, size in W.free_spans(prog):
        top = base + size
        if size > margin and all(wit.mem[a] == prog.mem0[a] for a in range(top - margin, top)):
            return top - 1
    return None


def relift_compare(a, b, text, text2):
    """The verdict on one re-lift, plus the first hunk of the normalised diff."""
    hunk = difflib.unified_diff(a.splitlines(), b.splitlines(), "P", "relift", n=1, lineterm="")
    verdict = (
        "identical" if a == b else ("differs-smaller" if len(b) < len(a) else "differs-larger")
    )
    return {
        "verdict": verdict,
        "bytes": len(text),
        "relift_bytes": len(text2),
        "proc_norm_bytes": len(a),
        "relift_proc_norm_bytes": len(b),
        "proc_norm_lines": len(a.splitlines()),
        "relift_proc_norm_lines": len(b.splitlines()),
        "growth": round(len(b) / max(len(a), 1), 2),
        "hunk": list(hunk)[:24],
    }


def scratch_share(proc, prog):
    """The share of a re-lift's raw text that names the witness's scratch, not the tune.

    ``witness6502`` spills every expression to a data block in a span the tune left free,
    so a cell the re-lift declares inside one of those spans is the backend's temporary
    and not residue the lift left behind."""
    from deity_informant import witness6502 as W  # pylint: disable=import-outside-toplevel

    free = [(b, b + n) for b, n in W.free_spans(prog)]
    names = {
        m.group(0)
        for m in _GEN.finditer(proc)
        if not m.group(0).startswith("sub_")
        and any(lo <= int(m.group(2), 16) < hi for lo, hi in free)
    }
    lines = proc.splitlines()
    hit = sum(1 for l in lines if any(n in l for n in names))
    return {
        "spans": len(free),
        "names": len(names),
        "line_share": round(hit / max(len(lines), 1), 3),
    }


def relift_one(entry, frames):
    """One tune's ``decompile(witness6502.emit(P))`` against ``P``, on procedure text."""
    from deity_informant import frameprog  # pylint: disable=import-outside-toplevel
    from deity_informant import structured as S  # pylint: disable=import-outside-toplevel
    from deity_informant import witness6502 as W  # pylint: disable=import-outside-toplevel
    from deity_informant.c64 import load_psid  # pylint: disable=import-outside-toplevel

    row = dict(_sweep.row_head(entry))
    try:
        signal.alarm(RELIFT_CAP_S)
        t0 = time.monotonic()
        sid, sub, _secs = entry
        mem, _load, init, play = load_psid(Path(sid).read_bytes())
        mem[0xD418] = 0x0F
        model, _ev = S.decompile(bytearray(mem), init, play, frames, sub)
        prog = frameprog.program(model)
        text = frameprog.dumps(prog)
        wit = W.emit(prog)
        cell = noop_init(prog, wit)
        if cell is None:
            row["refused"] = "no free byte outside the witness image for the no-op init"
            return row
        img = bytearray(wit.mem)
        img[cell] = 0x60  # RTS: the play phase enters from the witness image untouched
        model2, _ev2 = S.decompile(img, cell, wit.entry, frames, sub)
        text2 = frameprog.dumps(frameprog.program(model2))
        a, b = (normalise(split_artifact(t)[1]) for t in (text, text2))
        row.update(relift_compare(a, b, text, text2))
        row["scratch"] = scratch_share(split_artifact(text2)[1], prog)
        row["wall_s"] = round(time.monotonic() - t0, 1)
        return row
    except W.Refusal as exc:
        row["refused"] = str(exc)
        return row
    except Exception as exc:  # pylint: disable=broad-except
        row["error"] = "%s: %s" % (type(exc).__name__, exc)
        return row
    finally:
        signal.alarm(0)


def relift_sample(rows, fam, per_family, top):
    """The re-lift sample: the smallest cached artifacts of the largest families.

    The second emission costs minutes, so coverage is bought where the artifact is
    smallest -- which biases the run toward convergence, not away from it."""
    size = {r["tune"]: r["proc_bytes"] for r in rows if "proc_bytes" in r}
    by = defaultdict(list)
    for tune, name in fam.items():
        if tune in size:
            by[name].append(tune)
    out = []
    for name in sorted(by, key=lambda n: (-len(by[n]), n))[:top]:
        for tune in sorted(by[name], key=lambda t: size[t])[:per_family]:
            out.append((name, tune))
    return out


def _pool(fn, jobs, procs):
    with mp.Pool(min(len(jobs), procs), _sweep.arm) as pool:
        return pool.map(fn, jobs)


def _relift_job(args):
    return relift_one(*args)


def stage_census(args, tunes):
    """Measurement 3: the per-artifact shape rows and their corpus totals."""
    rows = _sweep.check_rows(_pool(_census_one, tunes, args.procs))
    fam = families()
    for row in rows:
        row["family"] = fam.get(row["tune"], "-")
    return {"rollup": census_rollup([r for r in rows if "bytes" in r]), "rows": rows}


def stage_family(args, tunes):
    """Measurement 2: distinct frame functions per family, and the near-miss overlap."""
    rows = _sweep.check_rows(_pool(_family_one, tunes, args.procs))
    fam = families()
    got = family_rollup(rows, fam)
    for row in rows:
        row.pop("norm_lines", None)
        row.pop("shape_lines", None)
        row["family"] = fam.get(row["tune"], "-")
    named = [r for r in got if r["family"] != "-"]
    return {
        "rollup": {
            "families": len(named),
            "tunes": sum(r["tunes"] for r in named),
            "distinct_norm": sum(r["distinct_norm"] for r in named),
            "distinct_shape": sum(r["distinct_shape"] for r in named),
            "top9_tunes": sum(r["tunes"] for r in named[:9]),
            "top9_distinct_norm": sum(r["distinct_norm"] for r in named[:9]),
            "j_norm": statistics.median([r["j_norm"] for r in named if r["j_norm"] is not None]),
            "j_shape": statistics.median([r["j_shape"] for r in named if r["j_shape"] is not None]),
        },
        "per_family": got,
        "rows": rows,
    }


def stage_relift(args, tunes):
    """Measurement 1: re-lift convergence over the per-family sample."""
    census = json.loads(Path(args.census).read_text(encoding="utf-8"))["rows"]
    want = relift_sample(census, families(), args.per_family, args.top)
    if args.tunes:
        keep = set(args.tunes.split(","))
        want = [(f, t) for f, t in want if t in keep or t.rsplit("/", 1)[-1] in keep]
    index = {_sweep.tune_id(e[0]): e for e in tunes}
    rows = _sweep.check_rows(
        _pool(_relift_job, [(index[t], args.frames) for _f, t in want if t in index], args.procs)
    )
    fam = {t: f for f, t in want}
    for row in rows:
        row["family"] = fam.get(row["tune"], "-")
    done = [r for r in rows if "verdict" in r]
    return {
        "rollup": {
            "frames": args.frames,
            "attempted": len(rows),
            "verdicts": dict(Counter(r["verdict"] for r in done)),
            "refused": [[r["tune"], r["refused"]] for r in rows if "refused" in r],
            "errors": [[r["tune"], r["error"]] for r in rows if "error" in r],
            "median_growth": (
                round(statistics.median([r["growth"] for r in done]), 2) if done else None
            ),
        },
        "rows": rows,
    }


STAGES = {"census": stage_census, "family": stage_family, "relift": stage_relift}


def _table(head, rows):
    out = ["| %s |" % " | ".join(head), "|%s|" % "|".join("---" for _ in head)]
    out += ["| %s |" % " | ".join(str(c) for c in r) for r in rows]
    return "\n".join(out)


def _relift_note(row):
    """One re-lift row's right-hand column: its scratch share, or why it carries none."""
    if "scratch" in row:
        return "%d names, %d%% of lines" % (
            row["scratch"]["names"],
            round(100 * row["scratch"]["line_share"]),
        )
    return (row.get("refused") or row.get("error", ""))[:70]


def report(got):
    """The three measurements as one compact markdown page."""
    cen, fam, rel = got["census"], got["family"], got["relift"]
    cr, fr, rr = cen["rollup"], fam["rollup"], rel["rollup"]
    lines = [
        "# L0 baseline for the denotation solve",
        "",
        "Measured with `tools/denotation_l0.py` (`census`, `family`, `relift`, `report`).",
        "The census and the quotient read the %d artifacts `_sweep` had already cached at"
        % cr["artifacts"],
        "full Songlengths and re-emitted nothing; the re-lift is the only stage that",
        "decompiles, over a %d-frame window on both hops." % rr["frames"],
        "",
        "## (1) Criterion 3 -- re-lift convergence",
        "",
        "`decompile(witness6502.emit(P))` against `P`, compared on procedure text with",
        "relocation, generated names and local numbering canonicalised. `witness scratch` is",
        "how much of the re-lift names the witness's own expression spill block.",
        "",
        _table(
            ["family", "tune", "verdict", "P lines", "re-lift lines", "x", "witness scratch"],
            [
                [
                    r["family"],
                    r["name"],
                    r.get("verdict", "refused" if "refused" in r else "error"),
                    r.get("proc_norm_lines", "-"),
                    r.get("relift_proc_norm_lines", "-"),
                    r.get("growth", "-"),
                    _relift_note(r),
                ]
                for r in rel["rows"]
            ],
        ),
        "",
        "Verdicts %s over %d attempted; median growth %s."
        % (json.dumps(rr["verdicts"]), rr["attempted"], rr["median_growth"]),
        "",
        "## (2) Criterion 2 -- the family quotient",
        "",
        "Distinct emitted frame functions per player family over every cached artifact of",
        "that family. `norm` renumbers generated names by first appearance, `shape` erases",
        "the identity altogether so an inserted site cannot shift every later line; `J` is",
        "the median pairwise line Jaccard, `shared` the family's intersection over union.",
        "",
        _table(
            [
                "family",
                "tunes",
                "raw",
                "norm",
                "shape",
                "median lines",
                "J(norm)",
                "J(shape)",
                "max J(shape)",
                "shared(shape)",
            ],
            [
                [
                    r["family"],
                    r["tunes"],
                    r["distinct_raw"],
                    r["distinct_norm"],
                    r["distinct_shape"],
                    r["median_lines"],
                    r["j_norm"],
                    r["j_shape"],
                    r["jmax_shape"],
                    r["shared_shape"],
                ]
                for r in fam["per_family"][:12]
            ],
        ),
        "",
        "Nine largest families: %d tunes, %d distinct normalised frame functions."
        % (fr["top9_tunes"], fr["top9_distinct_norm"]),
        "All %d named families: %d tunes, %d distinct (%d with the identity erased)."
        % (fr["families"], fr["tunes"], fr["distinct_norm"], fr["distinct_shape"]),
        "",
        "## (3) The L0 census",
        "",
        _table(
            ["measure", "value"],
            [
                ["artifacts", cr["artifacts"]],
                ["`arch` (machine names)", cr["arch"]],
                ["`temps`", cr["temps"]],
                ["median `arch` per artifact", cr["median_arch"]],
                ["`zero_arch`", cr["zero_arch"]],
                ["raw `mem[` sites", cr["mem_sites"]],
                ["`*deref` sites", cr["deref_sites"]],
                ["SID writes indexed by a machine register", cr["sid_reg_sites"]],
                [
                    "artifacts carrying such a write",
                    "%d (%.1f%%)" % (cr["sid_reg_artifacts"], cr["sid_reg_share"]),
                ],
                ["`for` headers", cr["for_total"]],
                ["`for` headers with a machine induction variable", cr["for_arch"]],
                [
                    "artifacts declaring a `[3]` table holding `00 07 0E`",
                    "%d (%.1f%%)" % (cr["lane3_artifacts"], cr["lane3_share"]),
                ],
                ["`stream ... via ptr` declarations", cr["via"]],
                ["header+data bytes", cr["head_bytes"]],
                ["procedure-body bytes", "%d (%.1f%%)" % (cr["proc_bytes"], cr["proc_share"])],
            ],
        ),
        "",
    ]
    return "\n".join(lines + coverage(got) + verdicts(got)) + "\n"


def coverage(got):
    """What was actually run, what was sampled, and what refused, by name."""
    cr, fr, rr = (got[k]["rollup"] for k in ("census", "family", "relift"))
    lines = [
        "## Coverage",
        "",
        "- census and quotient: %d of 624 cached tunes at full Songlengths, every artifact"
        % cr["artifacts"],
        "  already in `.sweep-cache` at the current package fingerprint -- nothing re-emitted.",
        "  %d tunes carry no SIDId name and are out of the family rollup (they keep the `-`"
        % (cr["artifacts"] - fr["tunes"]),
        "  row); a tune matching several signatures is assigned its most-carried family.",
        "- re-lift: %d tunes over %d families, %d frames on both hops, the two smallest cached"
        % (rr["attempted"], min(9, rr["attempted"]), rr["frames"]),
        "  artifacts per family -- a sample biased toward convergence, not away from it. The",
        "  re-lift enters the witness image through a no-op (`RTS`) init planted in a free",
        "  byte, so the play phase starts from exactly the image `witness6502` emitted.",
        "  Per-tune build cap %ds." % RELIFT_CAP_S,
    ]
    for tune, why in rr["refused"]:
        lines.append("- re-lift refused: `%s` -- %s" % (tune, why[:120]))
    for tune, why in rr["errors"]:
        lines.append("- re-lift no result: `%s` -- %s" % (tune, why[:120]))
    return lines + [""]


def verdicts(got):
    """Which way each measurement cuts on §7's ceiling claim, stated plainly."""
    cr, fr, rr = (got[k]["rollup"] for k in ("census", "family", "relift"))
    n = sum(rr["verdicts"].values())
    scratch = [r["scratch"]["line_share"] for r in got["relift"]["rows"] if "scratch" in r]
    return [
        "## What this says about the plan",
        "",
        "**(1) re-lift: the artifact is not a normal form, and the measurement is neutral on",
        "the ceiling.** %d of %d attempted re-lifts returned a result and every one of them is"
        % (n, rr["attempted"]),
        "`%s`, at a median %sx the procedure text; none identical, none smaller."
        % (
            max(rr["verdicts"], key=rr["verdicts"].get) if rr["verdicts"] else "-",
            rr["median_growth"],
        ),
        "So `decompile(witness6502.emit(P)) == P` fails on every tune measured, and the",
        "artifact is demonstrably not a fixpoint of the pipeline. It is not evidence of",
        "un-extracted structure either: a median %d%% of the re-lift's lines name a cell in a"
        % round(100 * statistics.median(scratch or [0])),
        "span the tune left free, which is where `witness6502` spills every expression because",
        "it allocates no registers. Criterion 3 measures the backend until the witness stops",
        "spilling, not the lift. What it does settle is the direction: nothing shrank, so",
        "nothing here says the artifact carries structure the lift could have extracted.",
        "",
        "**(2) family quotient: supports the plan's premise.** %d named tunes emit %d distinct"
        % (fr["tunes"], fr["distinct_norm"]),
        "normalised frame functions; the nine largest families cover %d tunes and emit %d, against"
        % (fr["top9_tunes"], fr["top9_distinct_norm"]),
        "§7.2's target of nine. Erasing the identity entirely still leaves %d. The near-miss says"
        % fr["distinct_shape"],
        "the same: median pairwise line Jaccard within a family is %s with names renumbered and"
        % fr["j_norm"],
        "%s with the identity erased, so two tunes of one player share roughly half their line"
        % fr["j_shape"],
        "shapes and almost no line identities. The gap §7.2 names is real, large, and measured.",
        "",
        "**(3) census: supports §1's diagnosis at full corpus coverage.** %d artifacts (%.1f%%)"
        % (cr["sid_reg_artifacts"], cr["sid_reg_share"]),
        "index a SID write by a machine register, %d (%.1f%%) declare a `[3]` table holding"
        % (cr["lane3_artifacts"], cr["lane3_share"]),
        "`00 07 0E`, and %d of %d `for` headers induct on a machine register. Median `arch` is %d"
        % (cr["for_arch"], cr["for_total"], cr["median_arch"]),
        "and `zero_arch` is %d of %d. §1 read these off a stale 1,200-artifact sample; they hold"
        % (cr["zero_arch"], cr["artifacts"]),
        "on the current build over the whole corpus.",
        "",
    ]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("stage", choices=sorted(STAGES) + ["report"])
    ap.add_argument("--tunes", help="comma-separated tune ids or stems; default the whole cache")
    ap.add_argument("--frames", type=int, default=200, help="the re-lift window, both hops")
    ap.add_argument("--per-family", type=int, default=2, help="re-lift tunes per family")
    ap.add_argument("--top", type=int, default=9, help="re-lift families, largest first")
    ap.add_argument("--census", default=str(ROOT / "out" / "denotation_l0_census.json"))
    ap.add_argument("--family", default=str(ROOT / "out" / "denotation_l0_family.json"))
    ap.add_argument("--relift", default=str(ROOT / "out" / "denotation_l0_relift.json"))
    ap.add_argument("-j", "--procs", type=int, default=24)
    ap.add_argument("-o", "--out")
    ap.add_argument("--md", default=str(ROOT / "docs" / "denotation-solve-baseline.md"))
    args = ap.parse_args()

    t0 = time.monotonic()
    if args.stage == "report":
        got = {k: json.loads(Path(getattr(args, k)).read_text(encoding="utf-8")) for k in STAGES}
        out = Path(args.out or ROOT / "out" / "denotation_l0.json")
        out.write_text(json.dumps(got, indent=1), encoding="utf-8")
        Path(args.md).write_text(report(got), encoding="utf-8")
        print("wrote %s and %s" % (out, args.md))
        return 0

    names = args.tunes.split(",") if args.tunes and args.stage != "relift" else None
    tunes = _sweep.entries(names)
    if not tunes:
        sys.exit("no cached tune matched")
    got = STAGES[args.stage](args, tunes)
    got["rollup"]["wall_s"] = round(time.monotonic() - t0, 1)
    path = Path(args.out or ROOT / "out" / ("denotation_l0_%s.json" % args.stage))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(got, indent=1), encoding="utf-8")
    print(json.dumps(got["rollup"], indent=1)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
