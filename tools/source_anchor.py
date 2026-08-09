"""Bind a canonical player source's labels to addresses in a corpus exemplar.

docs/idiom-catalog.md wants each row's source label and disasm_tune range to name one
code, and four of the six sources carry no addresses. Operands do not survive relocation
but the mnemonic sequence does, so the binding is an n-gram-seeded opcode alignment.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

import _sweep
import disasm_tune
import exemplars

ROOT = _sweep.ROOT
sys.path.insert(0, str(ROOT))

USAGE = """\
  python tools/source_anchor.py                          # every family in the table
  python tools/source_anchor.py defmon                   # the control: declared vs computed
  python tools/source_anchor.py --source a.asm --tune Wizball --ngram 6"""

PLAYERS = ROOT / ".oracle-cache" / "players"

# The fetched source, and the exemplar the source's own player plays (tools/exemplars.py).
FAMILIES = {k: (a.source, exemplars.BY_KEY[k].tunes[0]) for k, a in exemplars.ALIGNED.items()}

# An operand is one token or tokens joined by operators; prose is neither, and these
_EXPR = r"[^\s]+(?:[ \t]*[-+*/&|^<>][ \t]*[^\s]+)*"  # sources carry prose as well as code.
COMMENT = re.compile(r"(//|;).*")
INSN = re.compile(r"^[ \t]*(?:([\w.$@?+-]+)[ \t]+)?([A-Za-z]{3})\b[ \t]*(%s)?[ \t]*$" % _EXPR)
LABEL = re.compile(r"^[ \t]*([A-Za-z_.@][\w.$@]*)[ \t]*\{?[ \t]*$")
EQUPC = re.compile(r"^[ \t]*([A-Za-z_.@][\w.$@]*)[ \t]*(?:=|EQU)[ \t]*[*.][ \t]*$", re.I)
DECLARED = re.compile(r"//[ \t]*\$([0-9A-Fa-f]{4})\b")
NAMED = re.compile(r"\w")  # "-" and "+" are anonymous locals: seats, but not citable names

# Addressing-mode classes an operand's syntax states, held out of the alignment as its check.
CLASS = {"acc": 0, "impl": 0, "imm": 1, "zp": 2, "abs": 2, "rel": 2, "zpx": 3}
CLASS.update({"absx": 3, "zpy": 4, "absy": 4, "indx": 5, "indy": 6, "ind": 7})

_TAB = None


def op_table():
    """``(mnemonic -> id, [opcode] -> (id, length, class))``, the vocabulary from the lifter."""
    global _TAB  # pylint: disable=global-statement
    if _TAB is None:
        from deity_informant.lifter import MODE_LEN, OPS

        idx = {mn: i for i, mn in enumerate(sorted({mn for mn, _ in OPS.values()}))}
        rows = [(idx[OPS[o][0]], MODE_LEN[OPS[o][1]], CLASS[OPS[o][1]]) for o in range(256)]
        _TAB = (idx, np.array(rows, np.int64))
    return _TAB


def src_mode(operand):
    """The mode class an operand's syntax states: dialects vary in everything but this."""
    t = "" if operand is None else operand.replace(" ", "").upper()
    if not t or t == "A":
        return 0
    if t.startswith("#"):
        return 1
    if t.startswith("("):
        return 5 if t.endswith(",X)") else 6 if t.endswith("),Y") else 7 if t.endswith(")") else -1
    return 3 if t.endswith(",X") else 4 if t.endswith(",Y") else 2


def parse_source(text):
    """The source's instruction stream: ids, source lines, declared addresses, label seats.

    Dialects differ (Kick, 64tass, Exomizer, Ocean, plain text); what they share is a
    statement of an optional label then a mnemonic. ``declared`` is the address a Kick
    disassembly states in its trailing comment -- ground truth, never an input.
    """
    idx = op_table()[0]
    ids, lines, declared, modes, labels = [], [], [], [], []
    for num, raw in enumerate(text.splitlines(), 1):
        addr = DECLARED.search(raw)
        pieces = COMMENT.sub("", raw).split(":")
        for piece in pieces:
            insn = INSN.match(piece)
            if insn and insn.group(2).upper() in idx:
                if insn.group(1) and NAMED.search(insn.group(1)):
                    labels.append((insn.group(1), num, len(ids)))
                ids.append(idx[insn.group(2).upper()])
                lines.append(num)
                declared.append(int(addr.group(1), 16) if addr else -1)
                modes.append(src_mode(insn.group(3)))
                continue
            name = EQUPC.match(piece) or (LABEL.match(piece) if len(pieces) > 1 else None)
            if name:
                labels.append((name.group(1), num, len(ids)))
    return {
        "ids": np.array(ids, np.int64),
        "lines": np.array(lines, np.int64),
        "declared": np.array(declared, np.int64),
        "modes": np.array(modes, np.int64),
        "labels": labels,
    }


def seats(tune, frames, subtune):
    """The executed addresses over the subtunes: the seats a trace proves are real.

    Subtunes reach different arms of one player -- Wizball's nine reach three times the
    seats its first does -- and init mutates the image, so each is traced from a reload.
    """
    from deity_informant import structured as S
    from deity_informant.c64 import psid_songs

    path = disasm_tune.load(tune)[0]
    subs = [subtune] if subtune is not None else range(psid_songs(path.read_bytes())[0])
    out = set()
    for sub in subs:
        _p, mem, _load, init, play = disasm_tune.load(tune)
        mem[0xD418] = 0x0F
        out |= set(S.trace(mem, init, play, frames, sub).pcs)
    return np.array(sorted(out), np.int64)


def image_stream(mem, seat):
    """``(addrs, ids)`` of a linear decode over the seats' span, resynced at each seat.

    A linear walk desyncs over data and never recovers; a walk that steps over a known
    seat has provably desynced, so it restarts there. Unexecuted code between seats is
    decoded too, which is what the source's untaken arms align against.
    """
    tab = op_table()[1]
    addrs, ops, pc, end = [], [], int(seat[0]), int(seat[-1])
    while pc <= end:
        op = int(mem[pc])
        addrs.append(pc)
        ops.append(op)
        nxt = pc + int(tab[op, 1])
        k = int(np.searchsorted(seat, pc, "right"))
        pc = int(seat[k]) if k < seat.size and seat[k] < nxt else nxt
    rows = tab[np.array(ops, np.int64)]
    return np.array(addrs, np.int64), rows[:, 0], rows[:, 2]


def grams(a, n):
    """One integer per mnemonic ``n``-gram: base 128, so 8 mnemonics are exact in 64 bits."""
    if a.size < n:
        return np.zeros(0, np.uint64)
    win = np.lib.stride_tricks.sliding_window_view(a, n).astype(np.uint64)
    return win @ (np.uint64(128) ** np.arange(n - 1, -1, -1, dtype=np.uint64))


def seeds(ga, gb):
    """Every source position of a gram that occurs exactly once in the image.

    Uniqueness is asked of the image alone: a source repeating a block (defMON ships
    the player twice) would make no gram unique on both sides, while one unambiguous
    image seat with several source candidates is a choice extension and chaining make.
    """
    ub, ib, cb = np.unique(gb, return_index=True, return_counts=True)
    uniq, pos = ub[cb == 1], ib[cb == 1]
    if not uniq.size:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    k = np.searchsorted(uniq, ga).clip(0, uniq.size - 1)
    si = np.nonzero(uniq[k] == ga)[0]
    return si, pos[k[si]]


def matches(a, b, si, sj):
    """Each seed extended maximally both ways; a seed already inside its run is skipped."""
    out, seen = [], {}
    for i, j in zip(si.tolist(), sj.tolist()):
        if seen.get(i - j, -1) >= i:
            continue
        lo_i, lo_j = i, j
        while lo_i and lo_j and a[lo_i - 1] == b[lo_j - 1]:
            lo_i, lo_j = lo_i - 1, lo_j - 1
        hi_i, hi_j = i, j
        while hi_i + 1 < a.size and hi_j + 1 < b.size and a[hi_i + 1] == b[hi_j + 1]:
            hi_i, hi_j = hi_i + 1, hi_j + 1
        seen[i - j] = hi_i
        out.append((lo_i, lo_j, hi_i - lo_i + 1))
    return sorted(set(out))


def chain(runs):
    """The heaviest subset ordered in both streams: a monotone source-to-image map.

    Relocation is not a constant index shift -- instructions differ in length and the
    streams gain and lose whole blocks -- so consistency is order, not offset.
    """
    if not runs:
        return []
    i0, j0, ln = (np.array(x, np.int64) for x in zip(*runs))
    dp, prev = ln.copy(), np.full(ln.size, -1)
    for m in range(ln.size):
        ok = np.nonzero((i0[:m] + ln[:m] <= i0[m]) & (j0[:m] + ln[:m] <= j0[m]))[0]
        if ok.size:
            k = int(ok[np.argmax(dp[ok])])
            dp[m], prev[m] = dp[m] + dp[k], k
    out, m = [], int(np.argmax(dp))
    while m >= 0:
        out.append(runs[m])
        m = int(prev[m])
    return out[::-1]


def mapping(runs, n):
    """The image position of each source instruction over the chained runs, ``-1`` elsewhere."""
    out = np.full(n, -1, np.int64)
    for i0, j0, ln in runs:
        out[i0 : i0 + ln] = np.arange(j0, j0 + ln)
    return out


def align(src, ids, ngram):
    """``(runs, image position per source instruction)`` for one source against one image."""
    runs = chain(matches(src["ids"], ids, *seeds(grams(src["ids"], ngram), grams(ids, ngram))))
    return runs, mapping(runs, src["ids"].size)


def anchors(src, addr, runs, ok, min_run):
    """Each label whose next instruction landed inside a run long enough to carry it.

    A row carries its run's length and its run's held-out mode agreement, which is what
    separates a citable binding from a short match in a repetitive band.
    """
    starts = np.array([r[0] for r in runs], np.int64)
    span = {r[0]: (r[2], round(float(ok[r[0] : r[0] + r[2]].mean()), 3)) for r in runs}
    out = []
    for name, line, idx in src["labels"]:
        if idx >= addr.size or addr[idx] < 0:
            continue
        run, mode = span[int(starts[np.searchsorted(starts, idx, "right") - 1])]
        if run >= min_run:
            row = {"label": name, "line": int(line), "addr": int(addr[idx])}
            out.append({**row, "run": int(run), "run_mode": mode})
    return out


def control(src, addr):
    """Computed against declared, where the source states addresses: the shift classes.

    A build differing from the source by an inserted block displaces everything after it,
    so a correct alignment lands in few constant-displacement classes and a broken one in
    noise; agreement at shift 0 is only the part of the exemplar the source's build shares.
    """
    have = (src["declared"] >= 0) & (addr >= 0)
    if not have.any():
        return None
    shift = src["declared"][have] - addr[have]
    val, cnt = np.unique(shift, return_counts=True)
    order = np.argsort(-cnt)
    return {
        "checked": int(have.sum()),
        "agree": int((shift == 0).sum()),
        "classes": [[int(val[k]), int(cnt[k])] for k in order[:6]],
        "n_classes": int(val.size),
    }


def held_out(src, img_modes, pos):
    """Aligned instructions whose image mode class matches the syntax the source states.

    Alignment reads mnemonics only, so the operand's mode is evidence it never saw: a
    coincidental match agrees at chance, a real one at nearly every instruction.
    """
    have = (pos >= 0) & (src["modes"] >= 0)
    if not have.any():
        return None
    same = int((img_modes[pos[have]] == src["modes"][have]).sum())
    return {"checked": int(have.sum()), "agree": same, "rate": round(same / int(have.sum()), 4)}


def run_one(name, source, tune, args):
    """Anchor one source against one exemplar, and print the alignment's shape."""
    where = Path(source).resolve()
    src = parse_source(where.read_text(encoding="latin-1"))
    path, mem, _load, _init, _play = disasm_tune.load(tune)
    addrs, ids, img_modes = image_stream(mem, seats(tune, args.frames, args.subtune))
    runs, pos = align(src, ids, args.ngram)
    addr = np.where(pos >= 0, addrs[pos.clip(0)], -1)
    ok = (img_modes[pos.clip(0)] == src["modes"]) | (src["modes"] < 0)
    rows = anchors(src, addr, runs, ok, args.min_run)
    cov = int((addr >= 0).sum())
    out = {
        "family": name,
        "source": str(where.relative_to(ROOT) if where.is_relative_to(ROOT) else where),
        "tune": _sweep.tune_id(path),
        "src_insns": int(src["ids"].size),
        "img_insns": int(ids.size),
        "aligned": cov,
        "aligned_pct": round(100.0 * cov / max(src["ids"].size, 1), 1),
        "img_aligned_pct": round(100.0 * cov / max(ids.size, 1), 1),
        "runs": len(runs),
        "longest_run": max((r[2] for r in runs), default=0),
        "span": [int(addr[addr >= 0].min()), int(addr[addr >= 0].max())] if cov else None,
        "labels": len(src["labels"]),
        "anchored": len(rows),
        "anchored_at": {str(t): sum(r["run"] >= t for r in rows) for t in (16, 32, 64)},
        "mode_check": held_out(src, img_modes, pos),
        "control": control(src, addr),
        "rows": rows,
    }
    report(out, args)
    return out


def report(out, args):
    """One family's counts, its control rate where the source declares addresses, its anchors."""
    print(
        "\n%s: %s -> %s\n  %d source insns, %d image insns, %d aligned (%.1f%% of source,"
        " %.1f%% of image) in %d runs, longest %d%s\n  %d labels, %d anchored at run >= %d"
        % (
            out["family"],
            out["source"],
            out["tune"],
            out["src_insns"],
            out["img_insns"],
            out["aligned"],
            out["aligned_pct"],
            out["img_aligned_pct"],
            out["runs"],
            out["longest_run"],
            "" if not out["span"] else ", $%04X-$%04X" % tuple(out["span"]),
            out["labels"],
            out["anchored"],
            args.min_run,
        )
    )
    print("  anchors by run: %s" % ", ".join(">=%s: %d" % kv for kv in out["anchored_at"].items()))
    if out["mode_check"]:
        m = out["mode_check"]
        print(
            "  held-out mode class: %d/%d agree (%.1f%%)"
            % (m["agree"], m["checked"], 100 * m["rate"])
        )
    if out["control"]:
        c = out["control"]
        print(
            "  CONTROL %d checked, %d at shift 0, %d shift classes, largest %s"
            % (
                c["checked"],
                c["agree"],
                c["n_classes"],
                ", ".join("%+d:%d" % (s, n) for s, n in c["classes"]),
            )
        )
    for row in sorted(out["rows"], key=lambda r: r["addr"])[: args.show]:
        print(
            "  $%04X  %-32s run=%-5d mode=%-5.3g line %d"
            % (row["addr"], row["label"], row["run"], row["run_mode"], row["line"])
        )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("family", nargs="?", choices=sorted(FAMILIES), help="default: every family")
    ap.add_argument("--source", help="a source file outside the table")
    ap.add_argument("--tune", help="the exemplar --source is anchored against")
    ap.add_argument("--ngram", type=int, default=8, help="mnemonics per seed gram")
    ap.add_argument("--min-run", type=int, default=8, help="shortest run allowed to carry a label")
    ap.add_argument("--frames", type=int, default=600, help="play frames traced for seats")
    ap.add_argument("--subtune", type=int, help="one subtune; default every subtune")
    ap.add_argument("--show", type=int, default=40, help="anchors listed per family")
    ap.add_argument("-o", "--out", default=str(ROOT / "out" / "source_anchor.json"))
    args = ap.parse_args()

    if args.source:
        jobs = [("custom", args.source, args.tune)]
    else:
        picks = [args.family] if args.family else sorted(FAMILIES)
        jobs = [(f, str(PLAYERS / FAMILIES[f][0]), FAMILIES[f][1]) for f in picks]
    out = [run_one(*job, args) for job in jobs]
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\n%d families -> %s" % (len(out), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
