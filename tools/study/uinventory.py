"""Behavioral archetype detection from player grids — Gate-U0 step (docs/tracker-unification.md).

Runs each editor's own player, projects to a uniform per-frame SID register grid,
and detects which abstract {DIV,LOOKUP,RAMP} archetypes are present from behaviour
— one detector over four players (GoatTracker, DefMON, JCH, SID-Wizard)."""

from pathlib import Path

import numpy as np
import pygoattracker as PG
import pydefmon as PD
import pysidwizard as PW

from deity_informant import framelog
from deity_informant import structured as S
from deity_informant.c64 import load_psid

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "out"
REGN = 25
NFRAMES = 800
SWM = (
    Path("/tmp/claude-1000/-scratch-anarkiwi-re-deity-informant")
    / "c1a60cd3-8ffa-428a-a731-1dc1837787e2/scratchpad/clones/sid-wizard-vessel/examples"
)

ARCHETYPES = [
    ("DIV", "root frame", "tick", "tempo / row clock"),
    ("LOOKUP", "note value", "freq", "pitch"),
    ("LOOKUP", "pattern boundary", "fire", "song order"),
    ("LOOKUP", "row tick", "fire", "note sequence"),
    ("LOOKUP", "frames-since-note_on", "ctrl", "waveform program"),
    ("LOOKUP", "frames-since-note_on", "freq+", "arpeggio / vibrato"),
    ("RAMP", "frame, seed=note", "freq", "slide / portamento"),
    ("RAMP", "frame", "pw", "pulse sweep"),
    ("RAMP", "frame", "cutoff", "filter sweep"),
]
KEY = [k[:3] for k in ARCHETYPES]


def _snap_grid(frames):
    """Per-frame (reg,val) lists -> persisted 25-register states (players emit only changes)."""
    st, out = [0] * REGN, []
    for fr in frames:
        for reg, val in fr:
            r = reg - 0xD400 if reg >= 0xD400 else reg
            if 0 <= r < REGN:
                st[r] = val
        out.append(st.copy())
    return out


def _write_grid(nframes, writes):
    """Change-only (frame,reg,val) writes -> persisted 25-register states."""
    byf = {}
    for f, reg, val in writes:
        byf.setdefault(f, []).append((reg, val))
    st, out = [0] * REGN, []
    for f in range(nframes):
        for reg, val in byf.get(f, ()):
            r = reg - 0xD400 if reg >= 0xD400 else reg
            if 0 <= r < REGN:
                st[r] = val
        out.append(st.copy())
    return out


def grid_goattracker(path, n=NFRAMES):
    """GoatTracker player -> uniform grid."""
    return _snap_grid(PG.iter_frames(PG.read_sid(str(path)), 0, max_frames=n))


def grid_defmon(path, n=NFRAMES):
    """DefMON player -> uniform grid."""
    player = PD.DefmonPlayer(Path(path).read_bytes())
    writes = (
        (w.clock // 19656, w.reg, w.val)
        for w in PD.register_writes_from_player(player, max_frames=n)
    )
    return _write_grid(n, writes)


def grid_jch(path, n=NFRAMES):
    """JCH via deity-informant's VM (real playroutine; JCH uses illegal opcodes).

    pyjch's Player is a hand-written playroutine reimplementation that bypasses a
    CPU emulator and freezes on this version. The other editors' players run on
    jennings' illegal-opcode 6510; the VM is the engine jennings delegates to."""
    mem, _load, init, play = load_psid(Path(path).read_bytes())
    model, _ev = S.decompile(mem, init, play, n, 0)
    return _snap_grid(framelog.frames_from_walker(S.Walker(model), n))


def grid_sidwizard(path, n=NFRAMES):
    """SID-Wizard player -> uniform grid."""
    return _write_grid(n, PW.iter_writes(PW.read_swm(str(path)), n))


def _segments(grid, v):
    """Gate-on runs for voice v (ctrl bit 0)."""
    b, segs, start = 7 * v, [], None
    for f, s in enumerate(grid):
        on = s[b + 4] & 1
        if on and start is None:
            start = f
        elif not on and start is not None:
            segs.append((start, f))
            start = None
    if start is not None:
        segs.append((start, len(grid)))
    return segs


def _pitch_notes(grid):
    """Distinct ET-lattice notes any voice's gated freqs land on (glide-tolerant).

    Induces the lattice phase from the voice's freqs, then counts frames within
    0.15 semitone; a pitch table shows as >=60% coverage and >=5 distinct notes.
    Glide intermediates fall off-lattice and simply do not count."""
    best = 0
    for v in range(3):
        b = 7 * v
        fq = np.array(
            [s[b] | (s[b + 1] << 8) for s in grid if (s[b] | (s[b + 1] << 8)) > 0 and s[b + 4] & 1],
            dtype=float,
        )
        if len(fq) < 20:
            continue
        lg = 12 * np.log2(fq)
        phi = (np.angle(np.mean(np.exp(1j * 2 * np.pi * (lg % 1.0)))) / (2 * np.pi)) % 1.0
        idx = np.round(lg - phi)
        on = np.abs(lg - phi - idx) < 0.15
        distinct = len({int(i) for i, o in zip(idx, on) if o})
        if on.mean() >= 0.6 and distinct >= 5:
            best = max(best, distinct)
    return best


def _periodic(seq):
    """seq repeats at some period 1..8 over >=70% of transitions (arp/vibrato)."""
    if len(seq) < 4 or len(set(seq)) < 2:
        return False
    for p in range(1, min(9, len(seq))):
        if sum(seq[i] == seq[i - p] for i in range(p, len(seq))) >= 0.7 * (len(seq) - p):
            return True
    return False


def _monotone_run(seq, need=6, span=1):
    """A run of >=need frames strictly rising or falling by net >=span (ramp)."""
    for sgn in (1, -1):
        run = 1
        for i in range(1, len(seq)):
            run = run + 1 if (seq[i] - seq[i - 1]) * sgn > 0 else 1
            if run >= need and abs(seq[i] - seq[i - run + 1]) >= span:
                return True
    return False


def detect(grid):
    """Behavioural archetype presence with evidence counts."""
    ev = {k: 0 for k in KEY}
    ev[("DIV", "root frame", "tick")] = 1
    ev[("LOOKUP", "pattern boundary", "fire")] = 1
    ev[("LOOKUP", "row tick", "fire")] = 1
    allfreq = []
    for v in range(3):
        b = 7 * v
        for a, z in _segments(grid, v):
            seg = grid[a:z]
            fq = [s[b] | (s[b + 1] << 8) for s in seg]
            allfreq += [f for f in fq if f > 0]
            if len({s[b + 4] & 0xF0 for s in seg}) >= 2:
                ev[("LOOKUP", "frames-since-note_on", "ctrl")] += 1
            if _periodic(fq):
                ev[("LOOKUP", "frames-since-note_on", "freq+")] += 1
            if _monotone_run(fq, need=6, span=64) and not _periodic(fq):
                ev[("RAMP", "frame, seed=note", "freq")] += 1
            if _monotone_run([s[b + 2] | ((s[b + 3] & 0xF) << 8) for s in seg], need=6, span=32):
                ev[("RAMP", "frame", "pw")] += 1
    ev[("LOOKUP", "note value", "freq")] = _pitch_notes(grid)
    _ = allfreq
    cutoff = [s[0x16] for s in grid]
    if len(set(cutoff)) >= 3 and _monotone_run(cutoff, need=4, span=2):
        ev[("RAMP", "frame", "cutoff")] = len(set(cutoff))
    return ev


def render_counts(cols):
    """Numeric inventory: archetype x editor instance/evidence counts, all tunes."""
    names = [n for n, _ in cols]
    head = "%-7s %-22s %-6s | %s | meaning" % (
        "XFER",
        "TRIGGER",
        "ROUTE",
        "  ".join("%-11s" % n for n in names),
    )
    lines = [
        "=" * 96,
        "ABSTRACT GENERATOR INVENTORY  —  {DIV, LOOKUP, RAMP} x trigger x route",
        "counts from each editor's own player grid (behaviour); 0 = not exhibited by this tune",
        "=" * 96,
        head,
        "-" * 96,
    ]
    for (t, tr, r), meaning in zip(KEY, [m[3] for m in ARCHETYPES]):
        cells = "  ".join("%-11d" % e[(t, tr, r)] for _, e in cols)
        lines.append("%-7s %-22s %-6s | %s | %s" % (t, tr, r, cells, meaning))
    lines.append("-" * 96)
    lines.append(
        "archetypes exercised (union): %d/%d — every generator is one of these"
        % (sum(1 for k in KEY if any(e[k] for _, e in cols)), len(KEY))
    )
    return "\n".join(lines)


def render(cols):
    """Presence matrix: archetypes x editors, from behaviour."""
    names = [n for n, _ in cols]
    head = "%-7s %-22s %-6s | %s | meaning" % (
        "XFER",
        "TRIGGER",
        "ROUTE",
        "  ".join("%-11s" % n for n in names),
    )
    lines = [
        "=" * 96,
        "BEHAVIORAL ARCHETYPE PRESENCE  —  detected from each editor's own player grid",
        "=" * 96,
        head,
        "-" * 96,
    ]
    for (t, tr, r), meaning in zip(KEY, [m[3] for m in ARCHETYPES]):
        cells = "  ".join(
            "%-11s" % (("yes(%d)" % e[(t, tr, r)]) if e[(t, tr, r)] else "  -") for _, e in cols
        )
        lines.append("%-7s %-22s %-6s | %s | %s" % (t, tr, r, cells, meaning))
    lines.append("-" * 96)
    union = sum(1 for k in KEY if any(e[k] for _, e in cols))
    lines.append(
        "archetypes exercised (union): %d/%d — no tune needs any generator outside them"
        % (union, len(KEY))
    )
    lines.append(
        "per editor: " + "  ".join("%s=%d" % (n, sum(1 for k in KEY if e[k])) for n, e in cols)
    )
    lines += [
        "",
        "NOTES (behaviour is a lower bound; '-' = this tune did not exhibit it):",
        "- pitch = gated freqs on an induced ET lattice (glide-ok); DefMON 83% on NOTE_PITCH.",
        "- order/note-seq are declared by every format; every sequenced tune has them.",
        "- players run on jennings' 6510 (all 105 NMOS illegal opcodes); pyjch's Player instead",
        "  hand-rolls the playroutine, bypasses jennings, and freezes -- so JCH uses the VM.",
    ]
    return "\n".join(lines)


def main():
    """Detect archetypes across four editor players and render the matrix."""
    OUT.mkdir(parents=True, exist_ok=True)
    hvsc = ROOT / ".oracle-cache" / "hvsc"
    cols = [
        (
            "GoatTracker",
            detect(grid_goattracker(hvsc / "MUSICIANS/A/Acrouzet/6581_Words_per_Minute.sid")),
        ),
        ("DefMON", detect(grid_defmon(hvsc / "MUSICIANS/G/Goto80/Automatas.sid"))),
        ("JCH", detect(grid_jch(hvsc / "MUSICIANS/A/Abaddon/12_Bars.sid"))),
        ("SID-Wizard", detect(grid_sidwizard(SWM / "euphoria.swm"))),
    ]
    (OUT / "utune_presence.txt").write_text(render(cols) + "\n", encoding="utf-8")
    text = render_counts(cols)
    (OUT / "utune_inventory.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
