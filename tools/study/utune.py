"""Universal codec with an optimize pass (docs/tracker-unification.md).

Extracts a tune's generator graph from a GoatTracker/DefMON/SID-Wizard parse,
then OPTIMIZES it: de-dup identical programs and patterns, and transpose-factor
patterns that are pitch-shifts of one canonical. Writes out/<stem>.utune.txt."""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pygoattracker as PG
import pydefmon as PD
import pysidwizard as PW

from deity_informant import structured as S
from deity_informant import tracker as DT
from deity_informant.c64 import load_psid

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "out"
NOTES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")


def _nn(note):
    """Note token: semitone (C-0=0) -> name, 'OOB'/str passthrough, None -> '..'."""
    if note is None:
        return ".."
    if isinstance(note, str):
        return note
    return "%s%d" % (NOTES[note % 12], note // 12) if 0 <= note < 128 else "x%d" % note


def _et_extent(mem, lo_base, hi_base, decl, cap=128):
    """True ET extent of a split lo/hi table: scan past the declared size until the
    chromatic run breaks. The verifier's capped-checker bounds notes to this."""
    words = [mem[lo_base + i] | (mem[hi_base + i] << 8) for i in range(min(cap, 256))]
    anchor_i = decl - 1
    anchor = words[anchor_i]
    ext = decl
    for i in range(decl, len(words)):
        want = min(anchor * 2 ** ((i - anchor_i) / 12), 65535)
        got = words[i]
        if got > 0 and abs(12 * np.log2(max(got, 1) / want)) < 0.5:
            ext = i + 1
        else:
            break
    return ext, words[:ext]


def _wrap(items, per):
    """Group tokens into readable lines of `per`."""
    return ["  ".join(items[i : i + per]) for i in range(0, len(items), per)] or ["(empty)"]


@dataclass
class Tune:
    """Raw extracted graph before optimization."""

    stem: str
    editor: str
    pitch: list
    orders: list  # per voice: (list of (pattern_id, transpose), loop)
    patterns: dict  # id -> list of (note|str|None, progrefs tuple)
    programs: dict  # id -> (signature, display)


@dataclass
class Opt:
    """Optimized graph + reduction stats."""

    prog_map: dict = field(default_factory=dict)  # old prog id -> canonical index
    canon_prog: dict = field(default_factory=dict)  # signature -> canonical index
    inst: dict = field(default_factory=dict)  # old pattern id -> (canon index, base)
    canon_pat: dict = field(default_factory=dict)  # normalized events -> canon index
    exact: int = 0


def optimize(tune):
    """De-dup programs, de-dup + transpose-factor patterns."""
    o = Opt()
    for pid, (sig, _disp) in tune.programs.items():
        o.prog_map[pid] = o.canon_prog.setdefault(sig, len(o.canon_prog))
    exact = {}
    for pid, events in tune.patterns.items():
        rem = tuple((n, tuple(o.prog_map.get(p, p) for p in progs)) for n, progs in events)
        exact.setdefault(rem, len(exact))
        pitched = [n for n, _ in rem if isinstance(n, int)]
        base = min(pitched) if pitched else 0
        norm = tuple(((n - base) if isinstance(n, int) else n, progs) for n, progs in rem)
        o.inst[pid] = (o.canon_pat.setdefault(norm, len(o.canon_pat)), base)
    o.exact = len(exact)
    return o


# ---- extractors ------------------------------------------------------------
def _gt_ops(song, ptr):
    """GoatTracker wavetable at ptr -> universal op tokens."""
    wl, wr, ops = song.wavetable.left, song.wavetable.right, []
    for i in range(ptr, min(ptr + 16, len(wl))):
        left, right = wl[i], wr[i]
        if left == 0xFF:
            ops.append("jump(%d)" % right)
            break
        if 0x01 <= left <= 0x0F:
            ops.append("delay(%d)" % left)
        elif 0xE0 <= left <= 0xEF:
            ops.append("silent")
        elif 0xF0 <= left <= 0xFE:
            ops.append("cmd(%d,%d)" % (left, right))
        else:
            if left:
                ops.append("ctrl=%d" % left)
            ops.append("freq:=%d" % (right & 0x7F) if right & 0x80 else "freq+=%d" % right)
    return ops


def extract_gt(path, subtune=0):
    """GoatTracker parse -> raw Tune."""
    song = PG.read_sid(str(path))
    c = PG.constants
    pitch = _wrap(["%s=%d" % (_nn(i), w) for i, w in enumerate(c.FREQ_TABLE[: c.MAX_NOTES])], 8)
    orders, used = [], set()
    for ch in song.subtunes[subtune].channels:
        seq, tr = [], 0
        for e in ch.entries:
            if isinstance(e, PG.PlayPattern):
                seq.append((e.num, tr))
                used.add(e.num)
            elif isinstance(e, PG.Transpose):
                tr = e.semitones
        orders.append((seq, ch.restart))
    patterns, inst = {}, set()
    for pi in used:
        ev = []
        for r in song.patterns[pi].rows:
            if c.FIRSTNOTE <= r.note <= c.LASTNOTE:
                ev.append((r.note - c.FIRSTNOTE, (r.instrument,)))
                inst.add(r.instrument)
            elif r.note == c.KEYOFF:
                ev.append(("===", ()))
            else:
                ev.append((None, ()))
        patterns[pi] = ev
    programs = {}
    for k in sorted(x for x in inst if 0 < x < len(song.instruments)):
        it = song.instruments[k]
        ops = _gt_ops(song, it.wave_ptr)
        sig = (tuple(ops), it.attack_decay, it.sustain_release, it.pulse_ptr, it.filter_ptr)
        programs[k] = (sig, " ; ".join(ops))
    return Tune(Path(path).stem, "GoatTracker", pitch, orders, patterns, programs)


_DM_PLANE = {
    "WGh": "ctrl",
    "WGl": "ctrl",
    "AD": "ad",
    "SR": "sr",
    "PW": "pw",
    "PS": "pw",
    "RE": "res",
    "FV": "vol",
    "CP": "cutoff",
    "ACID": "cutoff",
}


def _dm_ops(song, slot):
    """DefMON sidtab slot rows -> universal op tokens."""
    ops = []
    for i in range(slot, min(slot + 10, 250)):
        vals = song.sidtab_row(i).values()
        if not vals:
            break
        for fld, val in vals.items():
            if fld == "TR":
                ops.append("freq:=%d" % (val & 0x7F) if val & 0x80 else "freq+=%d" % val)
            elif fld == "AF":
                ops.append("freq~>%d" % val)
            elif fld in _DM_PLANE:
                ops.append("%s=%d" % (_DM_PLANE[fld], val))
    return ops


def _dm_pitch(path):
    """Recover the true ET extent of the DefMON note table (verifier under-sizes it).

    tracker._pitch trusts the declared table size; the real chromatic run in memory
    runs further. Returns (extent, decl, words) so notes past extent can be capped."""
    mem, _l, init, play = load_psid(Path(path).read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, 300, 0)
    p = DT._pitch(model)  # pylint: disable=protected-access
    if p is None or p.endian != "split":
        return 96, 96, [(PD.NOTE_PITCH_HI[i] << 8) | PD.NOTE_PITCH_LO[i] for i in range(96)]
    m, lo, decl = bytes(model.mem0), int(p.base), len(p.words)
    hb = bytes((int(w) >> 8) & 0xFF for w in p.words)[decl // 2 :]
    idx = m.find(hb)
    hi = idx - decl // 2 if idx >= 0 else lo
    ext, words = _et_extent(m, lo, hi, decl)
    return ext, decl, words


def extract_dm(path):
    """DefMON parse -> raw Tune (pitch table capped to its true recovered extent)."""
    song = PD.DefmonSong.from_sid_bytes(Path(path).read_bytes())
    ext, decl, words = _dm_pitch(path)
    pitch = [
        "extent %d notes (verifier decl=%d; extended by ET scan) -- notes >= %d cap to OOB"
        % (ext, decl, ext)
    ]
    pitch += _wrap(["%s=%d" % (_nn(i), w) for i, w in enumerate(words)], 8)
    orders = [
        ([(x, 0) for x in bytes(a) if x], 0)
        for a in (song.arranger_v1, song.arranger_v2, song.arranger_v3)
    ]
    patterns, slots, capped = {}, set(), 0
    for pi in range(1, 200):
        try:
            ev = song.pattern_events(pi)
        except Exception:  # pylint: disable=broad-except
            continue
        events = []
        for e in ev:
            if e.gate_n:
                progs = tuple(s for s in (e.slot_a, e.slot_b) if s)
                if e.note < ext:
                    events.append((e.note, progs))
                else:
                    events.append(("OOB", progs))
                    capped += 1
                slots.update(progs)
        if events:
            patterns[pi] = events
    pitch[0] += "  [%d note-events capped]" % capped
    programs = {
        slot: (tuple(_dm_ops(song, slot)), " ; ".join(_dm_ops(song, slot))) for slot in slots
    }
    return Tune(Path(path).stem, "DefMON", pitch, orders, patterns, programs)


def extract_sw(path):
    """SID-Wizard parse -> raw Tune."""
    swm = PW.read_swm(str(path))
    orders, used = [], set()
    for seq in swm.sequences:
        s, tr = [], 0
        for cmd in seq:
            if isinstance(cmd, PW.PlayPattern):
                s.append((cmd.pattern, tr))
                used.add(cmd.pattern)
            elif isinstance(cmd, PW.Transpose):
                tr = cmd.semitones
        orders.append((s, 0))
    patterns, inst = {}, set()
    for pi in sorted(p for p in used if p < len(swm.patterns)):
        ev = []
        for r in swm.patterns[pi].rows:
            if r.note is not None and 0 <= r.note < 96 and r.instrument is not None:
                ev.append((r.note, (r.instrument,)))
                inst.add(r.instrument)
            else:
                ev.append((None, ()))
        patterns[pi] = ev
    programs = {}
    for k in sorted(x for x in inst if 0 <= x < len(swm.instruments)):
        it = swm.instruments[k]
        sig = (
            tuple(it.wf_table),
            tuple(it.pw_table),
            tuple(it.filter_table),
            it.attack,
            it.decay,
            it.sustain,
            it.release,
            it.arp_speed,
            it.vibrato,
        )
        disp = "wave=%s pulse=%s filter=%s arp=%d vib=%d" % (
            list(it.wf_table),
            list(it.pw_table),
            list(it.filter_table),
            it.arp_speed,
            it.vibrato,
        )
        programs[k] = (sig, disp)
    return Tune(
        Path(path).stem,
        "SID-Wizard",
        pitch=["semitone -> freq (player ET table)"],
        orders=orders,
        patterns=patterns,
        programs=programs,
    )


# ---- render ----------------------------------------------------------------
def _events_line(events, prog_disp):
    """Render normalized pattern events as note>prog tokens."""
    toks = []
    for note, progs in events:
        ref = "+".join(prog_disp.get(p, "i%d" % p) for p in progs) if progs else ""
        toks.append("%s>%s" % (_nn(note), ref) if ref else _nn(note))
    return _wrap(toks, 10)


def render(tune, opt):
    """Optimized universal graph + reduction stats."""
    prog_ids = {}
    for _pid, cidx in sorted(opt.prog_map.items(), key=lambda kv: kv[1]):
        prog_ids.setdefault(cidx, "p%d" % cidx)
    lines = [
        "=" * 80,
        "TUNE: %s   EDITOR: %s" % (tune.stem, tune.editor),
        "=" * 80,
        "",
        "OPTIMIZE (universal codec de-dup + transpose-factor)",
        "  programs   %3d -> %3d canonical" % (len(tune.programs), len(opt.canon_prog)),
        "  patterns   %3d -> %3d exact-dup -> %3d transpose-factored"
        % (len(tune.patterns), opt.exact, len(opt.canon_pat)),
        "  (%d patterns were pitch-shifts of another -> 1 canonical + a transpose)"
        % (opt.exact - len(opt.canon_pat)),
        "",
    ]
    lines += [
        "g0  [DIV   ] frame -> fire row_clock   ; tempo",
        "",
        "g1  [LOOKUP] note value -> freq   ; pitch",
    ]
    lines += ["      " + row for row in tune.pitch]
    for v, (seq, loop) in enumerate(tune.orders):
        refs = ["c%d@%+d" % (opt.inst[p][0], t + opt.inst[p][1]) for p, t in seq if p in opt.inst]
        lines += [
            "",
            "gO%d [LOOKUP] pattern_end.v%d -> fire pattern   ; order v%d (loop@%d)"
            % (v + 1, v + 1, v + 1, loop),
        ]
        lines += ["      " + row for row in _wrap(refs, 12)]
    prog_disp = {}
    for pid, cidx in opt.prog_map.items():
        prog_disp[pid] = prog_ids[cidx]
    seen = set()
    for pid, events in tune.patterns.items():
        cidx, base = opt.inst[pid]
        if cidx in seen:
            continue
        seen.add(cidx)
        norm = [((n - base) if isinstance(n, int) else n, progs) for n, progs in events]
        lines += [
            "",
            "c%d  [LOOKUP] row_clock -> fire note_on   ; canonical pattern (%d rows)"
            % (cidx, len(events)),
        ]
        lines += ["      " + row for row in _events_line(norm, prog_disp)]
    for sig, cidx in sorted(opt.canon_prog.items(), key=lambda kv: kv[1]):
        disp = next(d for _s, d in tune.programs.values() if _s == sig)
        lines += [
            "",
            "p%d  [LOOKUP/RAMP] note_on + frame -> planes   ; canonical program" % cidx,
            "      " + disp,
        ]
    return "\n".join(lines)


def main():
    """Extract, optimize, and render one tune per editor."""
    OUT.mkdir(parents=True, exist_ok=True)
    hvsc = ROOT / ".oracle-cache" / "hvsc"
    swm = (
        Path("/tmp/claude-1000/-scratch-anarkiwi-re-deity-informant")
        / "c1a60cd3-8ffa-428a-a731-1dc1837787e2/scratchpad/clones/sid-wizard-vessel/examples"
    )
    tunes = [
        extract_gt(hvsc / "MUSICIANS/A/Acrouzet/6581_Words_per_Minute.sid"),
        extract_dm(hvsc / "MUSICIANS/G/Goto80/Automatas.sid"),
        extract_sw(swm / "euphoria.swm"),
    ]
    for tune in tunes:
        opt = optimize(tune)
        (OUT / ("%s.utune.txt" % tune.stem)).write_text(render(tune, opt) + "\n", encoding="utf-8")
        print(
            "out/%s.utune.txt  programs %d->%d  patterns %d->%d->%d"
            % (
                tune.stem,
                len(tune.programs),
                len(opt.canon_prog),
                len(tune.patterns),
                opt.exact,
                len(opt.canon_pat),
            )
        )


if __name__ == "__main__":
    main()
