"""Partition every byte-wide SID store in the cached corpus (docs/frameprog.md 7.7).

Rung (d) has run by the time a frame program exists, so a widened or merged lane
store is a word store and what stays byte-wide is the residue: an index the model
cannot resolve (work) against one it resolves off a pair lo (irreducible).
"""

import json
import multiprocessing as mp
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HVSC = ROOT / ".oracle-cache" / "hvsc"
CAP_S = 1800  # wall seconds per tune, so one pathological build cannot hold the sweep open
SID_LO, SID_HI = 0xD400, 0xD41C
NREG = SID_HI - SID_LO + 1

BYTE = (
    "unproven",  # indexed lane store, index unresolved: the actionable residue
    "notaligned",  # indexed lane store, index resolved off a pair lo: irreducible
    "plain_lane",  # unindexed lane store: rung (d) widens every one of these
    "bytereg_idx",  # indexed store to ctrl/AD/SR/$D417-$D41C: no 16-bit form exists
    "bytereg_plain",  # unindexed store to the same 8-bit registers
    "unnamed",  # an address addr_split cannot name that may still reach the SID
)


def lane_offsets():
    """SID offsets that are a lane of a 16-bit register, per framefuse and framelog."""
    from deity_informant import framefuse
    from deity_informant import framelog

    fuse = {r for r in range(NREG) if framefuse._sid_base(SID_LO + r) is not None}
    assert fuse == set(framelog._OTHER), (sorted(fuse), sorted(framelog._OTHER))
    return sorted(fuse)


def _may_reach_sid(addr):
    """True where an address the emitter cannot name may still land on a register.

    ``addr_bits`` bounds it: an address that cannot set every bit $D400 and $D41C
    share names no SID register, which is what rules the ``zp,X`` wrap out."""
    from deity_informant import frameproc

    return frameproc.addr_bits(addr) & SID_LO == SID_LO


def _stores(prog):
    """Every store as ``(width, address, const base, indexed, named)``."""
    from deity_informant import framefuse
    from deity_informant import frameproc
    from deity_informant import grammar as G

    for _e, _p, _r, stmts in prog.procs:
        for s in framefuse.stmts_of(stmts):
            if s[0] == "st":
                base, idx = frameproc.addr_split(s[1])
                named = base is not None or s[1] in prog.resolved
                yield G.store_width(s[2]), s[1], base, idx is not None, named


def _partnered(prog):
    """Byte-wide indexed lane stores whose partner lane is stored at the same index.

    The upper bound on step 2 (merge, do not widen): a pair rung (d2) brings
    together needs no fact about the index, only both halves under one."""
    from deity_informant import framefuse
    from deity_informant import frameproc
    from deity_informant import grammar as G

    out = 0
    for _e, _p, _r, stmts in prog.procs:
        keys = []
        for s in framefuse.stmts_of(stmts):
            base, idx = (None, None) if s[0] != "st" else frameproc.addr_split(s[1])
            lo = None if base is None else framefuse._sid_base(base)
            if lo is not None and idx is not None and G.store_width(s[2]) == 1:
                keys.append((base, lo + (base == lo), idx))
        seen = {(b, i) for b, _o, i in keys}
        out += sum((other, idx) in seen for _b, other, idx in keys)
    return out


def _residue(model, prog):
    """``(unproven, notaligned)`` over the SID pairs, from rung (d)'s own counters.

    The measure pass re-run over the lifted program: a store rung (d) widened is
    no longer a lane half, so what it counts here is what it left behind."""
    from deity_informant import framefuse

    ctx = framefuse.contexts(model, prog.data_decls, prog.procs)
    unproven = notaligned = 0
    cands = framefuse.candidates(model, prog.data_decls, prog.procs)
    for (lo, hi), (kind, evidence) in sorted(cands.items()):
        if kind != "sid":
            continue
        p = framefuse._Pair(lo, hi, kind, evidence)
        for e, _pa, _r, stmts in prog.procs:
            framefuse._visit(stmts, p, False, ctx[e])
        assert not p.indexed, "a lane-aligned indexed store survived rung (d)"
        unproven += p.unproven
        notaligned += p.notaligned
    return unproven, notaligned


def one(entry):
    """One tune's partition: the ``BYTE`` buckets plus the 16-bit context counts.

    A tune the decompiler refuses returns its exception instead; the sweep is the
    whole cache, and §7.7's numbers are over the same 682 files."""
    try:
        signal.alarm(CAP_S)
        return _one(entry)
    except Exception as exc:  # pylint: disable=broad-except
        return {"tune": Path(entry[0]).stem, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        signal.alarm(0)


def _one(entry):
    from deity_informant import framefuse
    from deity_informant import frameprog
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    sid, sub, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F
    t0 = time.monotonic()
    model, _ev = S.decompile(mem, init, play, int(secs * 50), sub)
    prog = frameprog.program(model)
    row = {"tune": Path(sid).stem, "build_s": round(time.monotonic() - t0, 1)}
    row.update({k: 0 for k in BYTE})
    row["aligned"] = row["word_plain"] = row["unnamed_ruled_out"] = 0
    idx_lane = 0
    for width, addr, base, indexed, named in _stores(prog):
        if base is None:
            if width == 1 and not named:
                row["unnamed" if _may_reach_sid(addr) else "unnamed_ruled_out"] += 1
            continue
        if not SID_LO <= base <= SID_HI:
            continue
        lane = framefuse._sid_base(base) is not None
        if width == 2:
            row["aligned" if indexed else "word_plain"] += 1
        elif not lane:
            row["bytereg_idx" if indexed else "bytereg_plain"] += 1
        elif indexed:
            idx_lane += 1
        else:
            row["plain_lane"] += 1
    row["unproven"], row["notaligned"] = _residue(model, prog)
    assert row["unproven"] + row["notaligned"] == idx_lane, row
    row["partnered"] = _partnered(prog)
    row["byte_total"] = sum(row[k] for k in BYTE)
    row["lane_byte_total"] = row["unproven"] + row["notaligned"] + row["plain_lane"]
    return row


def entries():
    """``[(path, subtune, secs)]`` for every cached tune, at full Songlengths length."""
    from deity_informant.c64 import load_psid, psid_songs, song_lengths, song_seconds

    lengths = song_lengths((HVSC / "Songlengths.md5").read_text(encoding="latin-1"))
    out = []
    for path in sorted(HVSC.rglob("*.sid")):
        data = path.read_bytes()
        _mem, _load, _init, play = load_psid(data)
        sub = psid_songs(data)[1] - 1
        secs = song_seconds(data, lengths, sub)
        if play and secs:
            out.append((str(path), sub, secs))
    return out


def _alarm(_sig, _frame):
    raise TimeoutError("build cap")


def _arm():
    signal.signal(signal.SIGALRM, _alarm)


def main():
    tunes = entries()
    t0 = time.monotonic()
    with mp.Pool(min(len(tunes), 32), _arm) as pool:
        rows = pool.map(one, tunes)
    done = [r for r in rows if "error" not in r]
    total = {k: sum(r[k] for r in done) for k in done[0] if k != "tune"}
    assert total["byte_total"] == sum(total[k] for k in BYTE)
    out = {
        "tunes": len(done),
        "refused": [r for r in rows if "error" in r],
        "wall_s": round(time.monotonic() - t0, 1),
        "lane_offsets": ["$%02X" % r for r in lane_offsets()],
        "of_registers": NREG,
        "total": total,
        "rows": rows,
    }
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "fuse_measure.json").write_text(json.dumps(out, indent=1))
    brief = {k: v for k, v in out.items() if k != "rows"}
    brief["refused"] = len(out["refused"])
    print(json.dumps(brief, indent=1))


if __name__ == "__main__":
    main()
