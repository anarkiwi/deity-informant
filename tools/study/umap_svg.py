"""Emit out/tracker-universal-map.svg: how trackers map to the universal format.

Rows are the 9 universal generators (grouped DIV/LOOKUP/RAMP); columns are the
native structure each editor (GoatTracker, DefMON, JCH, SID-Wizard) uses, plus a
final column for the deity-informant decompiler component that recovers it."""

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent.parent / "out" / "tracker-universal-map.svg"
COLS = ("GoatTracker", "DefMON", "JCH", "SID-Wizard", "deity-informant lift")

# JCH cells = what pyjch exposes (frame-time = image opcodes); last column = deity-informant lift.
ROWS = [
    (
        "DIV",
        "tempo / row clock",
        "frame → tick",
        [
            ("tempo", "frames/tick"),
            ("event duration", "per-note"),
            ("WORK_TEMPO", "divider 50"),
            ("frame_speed", "tempo_table"),
            ("song_model _tempo", "Counter"),
        ],
    ),
    (
        "LOOKUP",
        "pitch",
        "note → freq",
        [
            ("FREQ_TABLE", "96-note ET"),
            ("NOTE_PITCH", "128 entries"),
            ("freq_lo / freq_hi", "128 ET"),
            ("tuning table", "player ET"),
            ("tracker._pitch", "recovered ~118/121"),
        ],
    ),
    (
        "LOOKUP",
        "song order",
        "pattern-end → fire",
        [
            ("Orderlist", "Play/Transpose·restart"),
            ("arranger v1-3", "per voice"),
            ("orderlist_ptr", "ptr table"),
            ("sequences[v]", "Play/Transpose/Loop"),
            ("streams.py", "pos_* sequence"),
        ],
    ),
    (
        "LOOKUP",
        "note sequence",
        "row tick → fire",
        [
            ("Pattern.rows", "note,inst,cmd"),
            ("pattern_events", "note, slot_a/b"),
            ("opcode stream", "in image"),
            ("Pattern.rows", "note,inst,fx"),
            ("streams.py", "note stream"),
        ],
    ),
    (
        "LOOKUP",
        "waveform program",
        "since note-on → ctrl",
        [
            ("wavetable.left", "WAVECMD"),
            ("sidtab WGh/WGl", "ctrl byte"),
            ("wave opcodes", "in image"),
            ("wf_table", "per instrument"),
            ("control Automaton", "song_model item 4"),
        ],
    ),
    (
        "LOOKUP",
        "arpeggio / vibrato",
        "since note-on → freq+",
        [
            ("wavetable.right", "rel / abs note"),
            ("sidtab TR", "rel note"),
            ("note-offset op", "in image"),
            ("arp_speed / chord", "+ vibrato"),
            ("FreqDriver / pitchind", "note + detune"),
        ],
    ),
    (
        "RAMP",
        "slide / portamento",
        "frame·seed → freq",
        [
            ("toneporta cmd", "3xx"),
            ("sidtab AF", "slide mode"),
            ("porta opcode", "in image"),
            ("porta fx", "fx column"),
            ("FreqDriver", "slide kind"),
        ],
    ),
    (
        "RAMP",
        "pulse sweep",
        "frame → pw",
        [
            ("pulsetable", "Table(l,r)"),
            ("sidtab PW/PS", "pw + depth"),
            ("pulse opcodes", "in image"),
            ("pw_table", "per instrument"),
            ("PWM accumulator", "song_model item 3"),
        ],
    ),
    (
        "RAMP",
        "filter sweep",
        "frame → cutoff",
        [
            ("filtertable", "Table(l,r)"),
            ("sidtab CP/ACID", "cutoff Δ / slide"),
            ("filter opcodes", "in image"),
            ("filter_table", "per instrument"),
            ("filter accumulator", "song_model item 6"),
        ],
    ),
]

HUE = {"DIV": "#0e9f6e", "LOOKUP": "#2563eb", "RAMP": "#e0701a"}
M, TITLE_H, HEAD_H, UNI_W, COL_W, ROW_H = 24, 66, 46, 286, 196, 58
W = M * 2 + UNI_W + COL_W * len(COLS)
TOP = M + TITLE_H + HEAD_H
H = TOP + ROW_H * len(ROWS) + 44

CSS = """
  .bg{fill:#ffffff}.panel{fill:#f6f8fa}.lift{fill:#eef2ff}.head{fill:#eaeef2}
  .ink{fill:#1f2328}.mut{fill:#57606a}.line{stroke:#d0d7de}
  text{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  @media (prefers-color-scheme:dark){
    .bg{fill:#0d1117}.panel{fill:#161b22}.lift{fill:#161d2e}.head{fill:#21262d}
    .ink{fill:#e6edf3}.mut{fill:#8b949e}.line{stroke:#30363d}}
"""


def _t(x, y, s, cls, size, weight="400", anchor="start"):
    """One text element."""
    return (
        '<text x="%.1f" y="%.1f" class="%s" font-size="%s" font-weight="%s" '
        'text-anchor="%s">%s</text>'
    ) % (x, y, cls, size, weight, anchor, escape(s))


def _badge(x, y, label):
    """A rounded transfer-class pill; returns (svg, width)."""
    w = len(label) * 6.4 + 16
    return (
        '<rect x="%.1f" y="%.1f" width="%.1f" height="17" rx="8.5" fill="%s"/>'
        '<text x="%.1f" y="%.1f" font-size="9.5" font-weight="700" fill="#ffffff" '
        'text-anchor="middle" font-family="-apple-system,Segoe UI,sans-serif">%s</text>'
    ) % (x, y, w, HUE[label], x + w / 2, y + 12.3, label), w


def build():
    """Assemble the full SVG string."""
    x_uni = M
    xs = [x_uni + UNI_W + COL_W * i for i in range(len(COLS))]
    x_lift = xs[-1]
    s = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-size="12">' % (W, H, W, H),
        "<style>%s</style>" % CSS,
        '<rect class="bg" width="%d" height="%d"/>' % (W, H),
    ]
    s.append(_t(M, 34, "How C64 trackers map to the universal generator format", "ink", 20, "700"))
    s.append(
        _t(
            M,
            54,
            "Each row is one universal generator; each cell is the native structure "
            "that editor uses. Last column: where deity-informant lifts it.",
            "mut",
            12,
        )
    )
    hy = M + TITLE_H
    s.append(
        '<rect class="head" x="%d" y="%d" width="%d" height="%d"/>' % (M, hy, W - 2 * M, HEAD_H)
    )
    s.append(
        '<rect class="lift" x="%d" y="%d" width="%d" height="%d"/>' % (x_lift, hy, COL_W, HEAD_H)
    )
    s.append(_t(x_uni + 14, hy + 28, "Universal generator", "ink", 12.5, "700"))
    for i, name in enumerate(COLS):
        s.append(_t(xs[i] + 12, hy + 28, name, "ink", 11.5 if len(name) > 11 else 12.5, "700"))
    for r, (transfer, meaning, route, cells) in enumerate(ROWS):
        top = TOP + r * ROW_H
        s.append(
            '<rect class="panel" x="%d" y="%d" width="%d" height="%d"/>' % (M, top, UNI_W, ROW_H)
        )
        s.append(
            '<rect class="lift" x="%d" y="%d" width="%d" height="%d"/>'
            % (x_lift, top, COL_W, ROW_H)
        )
        s.append(
            '<rect x="%d" y="%d" width="5" height="%d" fill="%s"/>' % (M, top, ROW_H, HUE[transfer])
        )
        badge, bw = _badge(x_uni + 14, top + 13, transfer)
        s.append(badge)
        s.append(_t(x_uni + 14 + bw + 9, top + 26, meaning, "ink", 12.5, "700"))
        s.append(_t(x_uni + 14, top + 44, route, "mut", 11))
        for i, (primary, secondary) in enumerate(cells):
            s.append(_t(xs[i] + 12, top + 26, primary, "ink", 12, "500"))
            s.append(_t(xs[i] + 12, top + 44, secondary, "mut", 10))
        s.append(
            '<line class="line" x1="%d" y1="%d" x2="%d" y2="%d" stroke-width="1"/>'
            % (M, top + ROW_H, W - M, top + ROW_H)
        )
    for x in xs:
        s.append(
            '<line class="line" x1="%d" y1="%d" x2="%d" y2="%d" stroke-width="1"/>'
            % (x, TOP, x, TOP + ROW_H * len(ROWS))
        )
    s.append(
        '<line class="line" x1="%d" y1="%d" x2="%d" y2="%d" stroke-width="1.5"/>'
        % (x_uni + UNI_W, hy, x_uni + UNI_W, TOP + ROW_H * len(ROWS))
    )
    s.append(
        _t(
            M,
            H - 16,
            "transfer classes:  DIV = clock divider    LOOKUP = table sampled by a "
            "trigger    RAMP = seed + step·count, bounded",
            "mut",
            11,
        )
    )
    s.append("</svg>")
    return "\n".join(s)


def main():
    """Write the SVG to out/."""
    OUT.write_text(build(), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
