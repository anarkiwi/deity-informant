"""Universal codec with an optimize pass (docs/tracker-unification.md).

Extracts a tune's generator graph from a GoatTracker/DefMON/SID-Wizard parse,
then OPTIMIZES it: de-dup identical programs and patterns, and transpose-factor
patterns that are pitch-shifts of one canonical. Writes out/<stem>.utune.txt."""

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pygoattracker as PG
import pydefmon as PD
import pysidwizard as PW

from deity_informant import framelog
from deity_informant import structured as S
from deity_informant import tracker as DT
from deity_informant.c64 import load_psid

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "out"
NOTES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")
WAVE = {
    0x80: "noise",
    0x40: "pulse",
    0x20: "saw",
    0x10: "tri",
    0x50: "tri+pul",
    0x60: "saw+pul",
    0xC0: "nois+pul",
    0x00: "off",
}
IC = {
    0: "unison",
    1: "min2",
    2: "maj2",
    3: "min3",
    4: "maj3",
    5: "fourth",
    6: "tritone",
    7: "fifth",
    8: "min6",
    9: "maj6",
    10: "min7",
    11: "maj7",
}


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
    programs: dict  # id -> (signature, display)  [parse refs; kept for optimize]
    graph_programs: list = field(default_factory=list)  # from the output-state graph


@dataclass
class Opt:
    """Optimized graph + reduction stats."""

    prog_map: dict = field(default_factory=dict)  # old prog id -> canonical index
    canon_prog: dict = field(default_factory=dict)  # signature -> canonical index
    inst: dict = field(default_factory=dict)  # old pattern id -> (canon index, base)
    canon_pat: dict = field(default_factory=dict)  # normalized events -> canon index
    exact: int = 0


def optimize(tune):
    """De-dup programs; de-dup + transpose-factor patterns on NOTE content.

    A pattern's identity is its notes, not the instruments played on them -- an
    instrument is a shared resource reused across notes and voices (the bank),
    referenced separately, never part of a pattern's identity."""
    o = Opt()
    for pid, (sig, _disp) in tune.programs.items():
        o.prog_map[pid] = o.canon_prog.setdefault(sig, len(o.canon_prog))
    exact = {}
    for pid, events in tune.patterns.items():
        notes = tuple(n for n, _progs in events)
        exact.setdefault(notes, len(exact))
        pitched = [n for n in notes if isinstance(n, int)]
        base = min(pitched) if pitched else 0
        norm = tuple((n - base) if isinstance(n, int) else n for n in notes)
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
    gp = _program_bank(_grid_sid(path))[1]
    return Tune(Path(path).stem, "GoatTracker", pitch, orders, patterns, programs, gp)


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
    gp = _program_bank(_grid_sid(path))[1]
    return Tune(Path(path).stem, "DefMON", pitch, orders, patterns, programs, gp)


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
    gp = _program_bank(_grid_writes(2000, PW.iter_writes(swm, 2000)))[1]
    return Tune(
        Path(path).stem,
        "SID-Wizard",
        pitch=["semitone -> freq (player ET table)"],
        orders=orders,
        patterns=patterns,
        programs=programs,
        graph_programs=gp,
    )


# ---- render ----------------------------------------------------------------
def render(tune, opt):
    """Optimized universal graph + reduction stats."""
    lines = [
        "=" * 80,
        "TUNE: %s   EDITOR: %s" % (tune.stem, tune.editor),
        "=" * 80,
        "",
        "OPTIMIZE (universal codec de-dup + transpose-factor)",
        "  programs   %3d editor entries -> %3d graph programs (output-state cycles, deduped)"
        % (len(tune.programs), len(tune.graph_programs)),
        "  patterns   %3d -> %3d note-dedup -> %3d transpose-factored (instrument factored out)"
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
        # pattern c<id> played with its (C-0-normalized) base note at this pitch
        refs = [
            "c%d@%s" % (opt.inst[p][0], _nn(t + opt.inst[p][1])) for p, t in seq if p in opt.inst
        ]
        lines += [
            "",
            "gO%d [LOOKUP] pattern_end.v%d -> fire pattern @<base note>   ; order v%d (loop@%d)"
            % (v + 1, v + 1, v + 1, loop),
        ]
        lines += ["      " + row for row in _wrap(refs, 12)]
    seen = set()
    for pid, events in tune.patterns.items():
        cidx, base = opt.inst[pid]
        if cidx in seen:
            continue
        seen.add(cidx)
        toks = [_nn((n - base) if isinstance(n, int) else n) for n, _p in events]
        lines += [
            "",
            "c%d  [LOOKUP] row_clock -> fire note_on   ; canonical pattern, notes only (%d rows)"
            % (cidx, len(events)),
        ]
        lines += ["      " + row for row in _wrap(toks, 12)]
    lines += [
        "",
        "; ---- instruments: a shared bank, reused across notes and voices (from the",
        ";      output-state graph: local CYCLE = arp/PWM, RAMP = attack/sweep).",
        ";      %d editor entries -> %d programs, no table decode."
        % (len(tune.programs), len(tune.graph_programs)),
    ]
    for i, disp in enumerate(tune.graph_programs):
        lines.append("p%-2d %s" % (i, disp))
    return "\n".join(lines)


@dataclass
class DiTune:
    """deity-informant lift of any tune into the universal graph."""

    stem: str
    pitch: object
    tempo: int
    clocks: list
    lanes: list  # per voice: [(note_name, instrument_id)]
    programs: list  # instrument id -> phase-string
    trigger: list  # shared-trigger evidence
    binds: list  # cross-voice freq binds


def _voice_state(frames):
    """Persisted per-frame 25-register state from cycle-stripped frames."""
    st, out = [0] * 25, []
    for fr in frames:
        for reg, val in fr:
            r = reg - 0xD400 if reg >= 0xD400 else reg
            if 0 <= r < 25:
                st[r] = val
        out.append(st.copy())
    return out


def _vstates(grid, v):
    """Per-frame (waveform, pw, freq) for one voice."""
    b = 7 * v
    return [
        (g[b + 4] & 0xF0, g[b + 2] | ((g[b + 3] & 0xF) << 8), g[b] | (g[b + 1] << 8)) for g in grid
    ]


def _local_period(st, i, maxp=12):
    """Tightest local loop at i: smallest p with 3 confirmed repeats, else 0."""
    for p in range(1, maxp + 1):
        if i - 2 * p >= 0 and all(st[i - k] == st[i - k - p] for k in range(3)):
            return p
    return 0


def _onsets(grid, v):
    """Note triggers = gate 0->1 (release is gate 1->0; the note keeps sounding)."""
    b = 7 * v
    return [f for f in range(1, len(grid)) if grid[f][b + 4] & 1 and not grid[f - 1][b + 4] & 1]


def _program(st, start, dur):
    """Instrument program as phases over frames-since-note-on; note-relative signature.

    Each phase is a local CYCLE (arp/PWM, note-relative offsets) or a RAMP
    (attack/sweep). The signature drops absolute pitch so one instrument dedups
    across the notes it plays."""
    per = [_local_period(st, f) for f in range(start, start + dur)]
    disp, sig, i = [], [], 0
    while i < dur:
        p = per[i]
        j = i
        while j < dur and (per[j] == p or (p and per[j] == 0)):
            j += 1
        seg = [st[start + k] for k in range(i, j)]
        wf = "/".join(dict.fromkeys(WAVE.get(s[0], "%02x" % s[0]) for s in seg))
        if p:
            base = min((s[2] for s in seg if s[2] > 0), default=1) or 1
            offs = tuple(sorted({round(12 * math.log2(s[2] / base)) for s in seg if s[2] > 0}))
            disp.append(
                "[%df cycle p%d %s arp%s]"
                % (j - i, p, wf, "".join(" +%d" % o for o in offs) if len(offs) > 1 else " -")
            )
            sig.append(("cyc", p, wf, offs))
        else:
            pws, fqs = [s[1] for s in seg], [s[2] for s in seg]
            if pws[0] != pws[-1]:
                disp.append("[%df %s pw ramp %d->%d]" % (j - i, wf, pws[0], pws[-1]))
                sig.append(("pw", wf, pws[0], pws[-1]))
            elif fqs and fqs[0] != fqs[-1]:
                r = round(fqs[-1] / max(fqs[0], 1), 2)
                disp.append("[%df %s pitch ramp x%.2f]" % (j - i, wf, r))
                sig.append(("fr", wf, r))
            else:
                disp.append("[%df %s hold]" % (j - i, wf))
                sig.append(("hold", wf))
        i = j
    return "  ->  ".join(disp), tuple(sig)


def _program_bank(grid):
    """Instrument programs from the player-output graph: local cycles, deduped.

    Universal (no per-format table decode): keyed by note-on identity (waveform,
    AD, SR) so a format's cascade/table rows collapse to the real instruments that
    play them; the program is shown from the longest note. Returns (key->id, [disp])."""
    key_id, prog = {}, {}
    for v in range(3):
        b, st, ons = 7 * v, _vstates(grid, v), _onsets(grid, v)
        for oi, f in enumerate(ons):
            dur = min((ons[oi + 1] if oi + 1 < len(ons) else len(grid)) - f, 40)
            iid = key_id.setdefault(
                (grid[f][b + 4] & 0xF0, grid[f][b + 5], grid[f][b + 6]), len(key_id)
            )
            if iid not in prog or dur > prog[iid][0]:
                prog[iid] = (dur, _program(st, f, dur)[0])
    return key_id, [prog[i][1] for i in range(len(prog))]


def _grid_sid(path, nframes=2000):
    """Player-output grid for a .sid via deity-informant's own VM (any player)."""
    mem, _l, init, play = load_psid(Path(path).read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, nframes, 0)
    return _voice_state(framelog.frames_from_walker(S.Walker(model), nframes))


def _grid_writes(nframes, writes):
    """Player-output grid from (frame, reg, val) writes (SID-Wizard player)."""
    byf = {}
    for f, reg, val in writes:
        byf.setdefault(f, []).append((reg, val))
    st, out = [0] * 25, []
    for f in range(nframes):
        for reg, val in byf.get(f, ()):
            r = reg - 0xD400 if reg >= 0xD400 else reg
            if 0 <= r < 25:
                st[r] = val
        out.append(st.copy())
    return out


def _sync(grid):
    """Cross-voice trigger coincidence + dominant locked pitch bind per voice pair."""
    ons = [_onsets(grid, v) for v in range(3)]

    def vf(v, f):
        return grid[f][7 * v] | (grid[f][7 * v + 1] << 8)

    trig, binds = [], []
    for a, b in ((0, 1), (0, 2), (1, 2)):
        sb = set(ons[b])
        hit = sum(1 for f in ons[a] if sb & {f - 1, f, f + 1})
        pct = 100 * hit / max(len(ons[a]), 1)
        if pct >= 50:
            trig.append("v%d+v%d %.0f%%" % (a + 1, b + 1, pct))
        seq = [
            (f, 12 * math.log2(vf(b, f) / vf(a, f)))
            for f in range(len(grid))
            if vf(a, f) > 0 and vf(b, f) > 0
        ]
        locked, cur = {}, []
        for f, iv in seq + [(-9, 1e9)]:
            if cur and (abs(iv - sum(x for _, x in cur) / len(cur)) > 0.12 or f != cur[-1][0] + 1):
                if len(cur) >= 6:
                    mean = sum(x for _, x in cur) / len(cur)
                    rec = locked.setdefault(round(mean), [0, 0.0])
                    rec[0] += len(cur)
                    rec[1] += (mean - round(mean)) * len(cur)
                cur = []
            cur.append((f, iv))
        if not locked:
            continue
        semi, (w, csum) = max(locked.items(), key=lambda kv: kv[1][0])
        octs = semi // 12  # floor: round() relabels a fifth as "fourth +1oct" (17 semitones)
        cents = csum / w * 100
        binds.append(
            "v%d.freq = v%d.note %s%s%s   [locked %d frames]"
            % (
                b + 1,
                a + 1,
                IC[semi % 12],
                "" if octs == 0 else " %+doct" % octs,
                " (detune %+.0f cents)" % cents if abs(cents) >= 3 else "",
                w,
            )
        )
    return trig, binds


def extract_di(path, subtune=0, nframes=2000):
    """deity-informant lift -> DiTune (works on any player, incl. custom).

    Instruments are keyed by their note-on identity (waveform, AD, SR); each one's
    program is lifted from its longest note as local-cycle phases."""
    mem, _l, init, play = load_psid(Path(path).read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, nframes, subtune)
    t = DT.lift(model)
    grid = _voice_state(framelog.frames_from_walker(S.Walker(model), nframes))
    words = np.asarray(t.pitch.words) if t.pitch else np.asarray([])
    key_id, progs = _program_bank(grid)
    lanes_out = []
    for v in range(3):
        b, st, notes = 7 * v, _vstates(grid, v), []
        for f in _onsets(grid, v):
            iid = key_id[(grid[f][b + 4] & 0xF0, grid[f][b + 5], grid[f][b + 6])]
            fq = min((st[k][2] for k in range(f, f + 6) if st[k][2] > 0), default=0)
            idx = int(np.argmin(np.abs(words - fq))) if len(words) and fq else -1
            notes.append((_nn(idx) if idx >= 0 else "?", iid))
        lanes_out.append(notes)
    trig, binds = _sync(grid)
    return DiTune(Path(path).stem, t.pitch, t.tempo or 0, t.clocks, lanes_out, progs, trig, binds)


def render_di(d):
    """Universal graph text for a deity-informant lift."""
    divs = [c for c in d.clocks if c.kind == "dec"]
    lfos = [c for c in d.clocks if c.kind == "inc"]
    lines = [
        "=" * 84,
        "TUNE: %s   EDITOR: deity-informant lift" % d.stem,
        "=" * 84,
        "",
        "g0  [DIV]     frame -> row_clock             ; tempo $%04X" % d.tempo,
        "g1  [DIV]     note_on -> reload              ; note-length %s"
        % " ".join("$%04X" % c.base for c in divs),
        "g2  [DIV]     frame -> phase                 ; LFO %s"
        % " ".join("$%04X" % c.base for c in lfos),
    ]
    if d.pitch:
        lines.append(
            "g3  [LOOKUP]  note value -> freq             ; pitch (ET %d $%04X)"
            % (len(d.pitch.words), d.pitch.base)
        )
        lines += [
            "      " + r
            for r in _wrap(["%s=%d" % (_nn(i), int(w)) for i, w in enumerate(d.pitch.words)], 8)
        ]
    lines += [
        "",
        "; ---- sync generators (cross-voice) ----",
        "gT  [TRIGGER] row_clock -> note_on.v1,v2,v3   ; %s" % ("  ".join(d.trigger) or "none"),
    ]
    lines += ["gB  [BIND]    %s" % b for b in d.binds]
    for v, notes in enumerate(d.lanes):
        runs = []
        for name, iid in notes:
            tok = "%s/p%d" % (name, iid)
            runs.append(tok)
        lines += [
            "",
            "g%d  [LOOKUP]  row_clock.v%d -> note_on      ; note lane v%d (%d notes)"
            % (4 + v, v + 1, v + 1, len(notes)),
        ]
        lines += ["      " + r for r in _wrap(runs, 10)]
    lines += ["", "; ---- instrument programs (local CYCLE = arp/PWM, RAMP = attack/sweep) ----"]
    for i, disp in enumerate(d.programs):
        lines += ["p%-2d %s" % (i, disp)]
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
    for rel in ("MUSICIANS/H/Hubbard_Rob/Commando.sid",):
        di = extract_di(hvsc / rel)
        (OUT / ("%s.utune.txt" % di.stem)).write_text(render_di(di) + "\n", encoding="utf-8")
        print(
            "out/%s.utune.txt  (deity-informant lift) %d instruments, %d binds"
            % (di.stem, len(di.programs), len(di.binds))
        )


if __name__ == "__main__":
    main()
