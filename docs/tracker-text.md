# tracker-text — the recovered graph, rendered for a human

`deity_informant/trackertext.py` renders a `tracker.Graph` as text.
`tools/tracker_text.py` writes one file per showcase tune, at its full Songlengths
duration, to `out/<Tune>.trackertext.txt`, and one **side by side** file per oracle
tune to `out/<Tune>.sidebyside.txt`. A **prototype view for review**, not the codec of
docs/tracker.md §7.5: there is no parser and no `parse(emit(t)) == t` claim.

## Two rules

**Render the graph, never the observation.** The song section is the strongest case of
this rule rather than an exception to it: `Graph.charts` is declared data at offsets the
program text's own cursor steps to, so not one byte of it is read off a frame.

 Every line is derived from the nodes'
`(transfer, trigger, route)` triples, the **declared tables** those transfers hold,
and `Graph.classes` — the same discipline `tracker.render` follows (docs/tracker.md
§1, "Nothing is passed through"). The note lane is inverted from the **generators'
own** pitch emits, so a frame whose pitch word is still in `RAW` has no note here.
The one exception is the `RAW` node, whose contents are observed by definition; its
lines are labelled `(OBSERVED)`, as are the `EDGE` fire patterns (the trigger floor)
and a sweep's seed.

**Nothing of the machine survives.** Addresses, cells, register numbers and raw bytes
belong to sidprog and frameprog, the layers whose job is provenance. This artifact is
in the musical domain: voices, notes, instruments, tables, rows, frames, tempo,
waveform, attack/decay, sustain/release, pulse width, cutoff, resonance, filter mode,
master volume. A value is said in its field's own units — `master volume 15`,
`resonance 10`, `low-pass`, `gate on` — and never as a hex byte. Entities carry stable
ordinal names assigned **by first appearance** — `table 0`, `inst 00` — the way an
editor numbers them, not the way memory lays them out; two runs over one tune assign
the same names. `emit()` still takes the `frameprog.FrameProgram`, for naming only:
the clock census, and which declaration a lane belongs to so its sibling lanes group
into one table. Without it the rendering still stands, each lane its own table.

## The sections

| section | what it shows |
|---|---|
| header | tune, frames, the law verdict, the node census, and the value partition per plane with its evidence classes |
| engine | the pitch table (how many notes, what range, how it inverts), the clock census, the masked field groups, and the tables the generators read |
| instruments | the rows those tables hold, decoded as an editor shows them: waveform bits, attack/decay and sustain/release nibbles |
| generators | one entry per node in graph order: transfer, trigger, route, and the detail that shows the primitive |
| song | the arrangement (docs/tracker.md §4j): each voice's sequence as named patterns, and each pattern's rows as note, instrument, duration, tie and sustain |
| note lanes | per voice, the note lane as runs of named notes with frame spans and detune in cents |
| residual | the replayed writes per plane and per part, the observed trigger count, and the shallow classes |

Per transfer kind: a `SELECT` names the table and lane it reads and its row stream
(rows of a pitch table print as notes, rows of an instrument table as `inst NN`, and a
row past the end of the lane as that row's byte `hold`, `gate-` or `gate+`); a
a table read straight through its constant or its sequence; a `RAMP` its seed, step and wrap with the
values it generates; an `EDGE` when it fires and the histogram of its gaps; `RAW` its
counts.

Evidence classes come straight from `Graph.classes` (docs/tracker.md §6) and are never
folded together: `lane`/`gate`/`ramp` are **strong** (a declared byte at a recovered
row, or generated from one), `imm`/`seed` are **shallow** (they pass the law without
explaining a row), `mask` is a byte several generators assemble field by field and is
folded into neither. A last column, `note`, is the plane's generated emits that no class
covers — the pitch-table note lane, which recovers a row for an **observed**
word (§4).

## A masked route is a musical object, not an annotation

docs/tracker.md §2/§4e gives a route a bit **mask**, because `$18` is a filter mode ORed
with a master volume and `$17` a resonance ORed with three voices' routing flags. Those
are two — or four — independent musical objects sharing one register, and that is how
they render: a node routes `-> filter mode` or `-> master volume`, never to a byte with
a mask beside it, and its emits are said in that field's terms (`low-pass`,
`master volume 15`, `voice 2 routing on`). The group is stated once in the engine
section:

```
fields   filter mode + master volume: 2 generators of disjoint bits, one write between them — n11 n12
```

A group's fields **share one write**, so the coverage counts it as one emit and not one
per field — `_scan` latches and assembles exactly as `tracker._run` evaluates, and the
test asserts the rendering's own `Coverage` equals `tracker.coverage`'s.

## Side by side: our recovery against the composer's own song

`compare` puts two `Side`s of one tune through this same emitter: ours, recovered from
the binary, and the native editor's own song mapped onto the same primitive
(docs/gt-oracle.md, docs/dm-oracle.md). The oracle's graph is the **admitted** one, so
it passes the law by construction and its coverage is how much of the tune the
composer's own data explains. A `Side` carries the frame offset it starts at, because a
packed driver's frame 0 is not the tune's (`gtoracle.align`).

The difference is stated before either rendering:

- **coverage**, per plane, both sides, with the direction of the difference;
- **notes** — how many frames name a note on both sides, how many agree, and whether
  there is a transposition between them;
- **instruments** — whether our instrument numbering is a **bijection** onto the song's;
- **arrangement** — how many generators on either side address another generator's
  index, beside the pattern, orderlist-entry, row and instrument counts the song itself
  holds.

That last row is the point of the artifact and it reads **zero on both sides**. Pitch
and instruments are recovered; the arrangement is not represented at all (docs/tracker.md
§7.4, docs/gt-oracle.md §3.2).

## Compression, and what it costs

A full tune is tens of thousands of frames, so streams are run-length coded **and**
repeated blocks are factored out: `[inst 00  inst 01  inst 02] x14`. Repetition is the
arrangement showing through, so it is surfaced rather than trimmed. It is a display
compression and claims no generator (§7.3, §7.4 are unbuilt).

**No silent caps.** Where a line is cut for width, it ends with the count of what was
left out — blocks, rows, runs, note frames and the last frame reached — so the view can
never read as complete when it is not.

## What the view does not claim

- **Not a codec.** Nothing parses this text back; it is not the tune's normal form.
- **No timing is explained.** Every `EDGE` count is observed: note-on times are the
  trigger floor (docs/tracker.md §5, §7.4).
- **A row field is named by the cell it flows into, and that naming is only as sharp as
  `_pairs`.** A byte the text copies into a cell `tools/node_partition.py`'s pairing rule
  calls a cursor of the pitch table renders as a note; where a row holds two such fields
  the earlier one is the parameter and prints as `param N`, and a byte past the table's
  end prints as `note N` rather than a name it does not have.
- **The arrangement is shown but not generated.** The song section renders the orderlists
  and the patterns the frame program's own walk names (docs/tracker.md §4j) — on Commando
  they are byte-identical to the composer's source — but no generator is fed from them: a
  row stream in the *generator* listing is still a recovered index, not a generated one
  (§7.4), and the side-by-side view measures that rather than asserting it.
- **The residual is the point.** The generated share, the per-plane residual and the
  per-part replay counts are always printed.
- **A residual emit is not a missing byte.** The coverage figures on both sides measure
  *justification*, never reach: `tools/graph_diff.py` matched the two graphs node by
  node and found **zero** of the composer's writes are ones the recovery never produces
  (docs/tracker.md §0). What the side-by-side shows is the same tune expressed as two
  different partitions of the same writes — 1 to 11 nodes match out of 42 to 575, and
  the mismatch runs both ways. Read a low `ours` figure as "cannot be attributed to a
  declaration", never as "cannot be reproduced".

## Running it, and what it costs

```
python tools/tracker_text.py            # both; out/ is gitignored
python tools/tracker_text.py show       # out/<Tune>.trackertext.txt
python tools/tracker_text.py compare    # out/<Tune>.sidebyside.txt
```

Needs the HVSC cache under `.oracle-cache/hvsc`, and the side-by-side mode needs the
native-editor extras (`pip install -e '.[nativeoracle]'`).

**The rendering is linear in frames and in nodes.** A whole tune is thousands of nodes —
Commando at 11750 frames is 1812 — and `tracker._fired` rescans every node per firing
trigger, which makes a pass quadratic; `_consumers` indexes the trigger fan-out once, so
`_scan` is one linear pass instead: **45.8s → 17.7s** on that graph, while doing strictly
more work (the per-frame note index and the instrument map the side-by-side view needs).
What dominates the wall clock is **recovering** the graph, which is `tracker`'s cost and
not this layer's: 183s for Commando's 11750 frames against 17.7s to render them, and 11s
against 0.3s for Ghouls_n_Ghosts. The driver runs the tunes in a pool, so a whole run is
one tune's recovery wide, and no tune is windowed.

The tests (`tests/test_trackertext.py`) are hermetic: they render hand-built graphs
carrying every transfer kind and a masked group over two registers, compare two sides,
and assert the linear fire index agrees with `tracker._fired` frame for frame.
