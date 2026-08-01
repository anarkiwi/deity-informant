# node-partition — does the program text already name the editor's nodes?

`tools/graph_diff.py` compares our recovered `tracker.Graph` against the editor's own
node by node. Over nine GoatTracker tunes it found that **99680 of the composer's
writes are shaped differently by us and zero are never produced**, while only **1 to 11
nodes of 42 to 575 match**. The recovery reaches every byte and partitions it wrongly.

Our recovery builds nodes **register-first**: `tracker._lane_key`, `_tree_tables`,
`_acc_sites`, `_divisors` and `_walked` are all per-register searches. An editor's song
is **object-first** — a pattern chain, an instrument program, an orderlist, an arpeggio
table — and each object is *a declared table advanced by a cursor* that may feed several
registers.

**Hypothesis.** The frame program already contains a set of **(declared region, cursor
cell)** pairs corresponding to the editor's node set, and building nodes from those pairs
instead of from register streams would align the two partitions.

`tools/node_partition.py` is the measurement — `python tools/node_partition.py 0
[defmon relpath ...]`, writing `out/node_partition.json`. **The verdict is: confirmed for
every object except the arrangement, and the arrangement's failure has one named cause.**

## 1. What is measured, and against what

**A composer object is an address span.** `pygoattracker`'s `decompile_sid` resolves the
packed layout — the frequency-table anchor, the song table, the pattern-pointer table,
the orderlists, the packed patterns, the instrument arrays and the wave/pulse/filter/speed
column pairs — and then returns only the decoded song. `gt_layout` reruns that same
candidate loop and **keeps the addresses it discards**, so each of the composer's tables
becomes an `(name, base, size)` span in the same memory the frame program was lifted from.
DefMON's are `dmoracle.dm_sites`, which already carries them.

**A program pair is a load the text writes as `base[index]`.** Every procedure's
statements are walked in order, each load's index resolved against the locals defined
above it (`frameproc._index_of` names the index, `frameproc._map_exprs` reaches the
expressions), and the const bases the index reads are the **cursors**. Rung (f)'s proven
derefs contribute their target blocks indexed by the pointer's own cell. Nothing is
derived from an observed value: a cursor is a cell the program text reads into an index.

A cursor is classified off the declarations alone — `row` if it lies in a region with no
`mut` offsets (another declared table supplies the index: a generated row), `state` if the
region is play-written, `cell` if no declaration covers it.

**The match is address containment and nothing else, and it is controlled.** `SHIFT=n`
moves every object off its address before matching, so the same rule can be run against a
map that is wrong by construction (§2.1). An object is *declared* if some
`prog.data_decls` region overlaps its span, and *paired* if some indexed load base lies in
its span or in a region overlapping it. A stricter figure — the load base inside the
object's **own** span — is reported beside it. No threshold, no tuning, no scoring.

**Self-checks.**

- The address map is checked against the decompiler's own output: for the seven tables
  `pygoattracker` returns verbatim (`ins.ad`, `ins.sr`, `ins.waveptr`, both pulse columns,
  both speed columns), the bytes at the located base must equal them. **431 of 431 agree**;
  a mismatch would condemn that tune's whole map.
- The editor's node set is re-derived rather than assumed. `gtoracle._build` keeps no
  stream key, so the same rule is applied to the same frames and the key count checked
  against the graph `_build` returned (`len(keys) + rel-with-base == plane nodes`).
  **69 of 69 sound.**

## 2. The correspondence, measured

72 of 701 cached HVSC tunes decompile as GoatTracker; 69 reach the measurement (two are
init runaways that never reach the frameprog gate, one faults inside `pygoattracker`'s own
playroutine). 200 frames, PSID start subtune.

| | GoatTracker, 69 tunes | DefMON, 3 tunes |
|---|---|---|
| composer objects located | 1654 | 21 |
| a declaration overlaps it | **1617 (97.8%)** | 19 |
| a (region, cursor) pair names it | **1415 (85.6%)** | 18 |
| …with the load base inside the object's own span | 1201 (72.6%) | 18 |
| program pairs | 1763 | 87 |
| pairs on no composer object | **188 (10.7%)** | 60 |

**The cardinalities agree.** Per tune the program names **25.6** (region, cursor) pairs;
the editor's own graph carries **26.7** distinct (object, register, mask, cursor) groups.
Its raw node count is 64.8 per tune, the excess being one `RAMP` node per sweep run —
run-splitting the mapping does on its own, not a second set of objects.

Per editor node, over the 69 tunes:

| the object this editor node reads | nodes | emits |
|---|---|---|
| **is paired with a cursor in the program text** | **4005** | **150400** |
| is declared, but no cursor names it | 31 | 3085 |
| has no address in the song data at all | 434 | 17709 |

**89.6% of the editor's own nodes, and 87.8% of its emits, sit on an object the frame
program already names with a cursor.**

Per object, the failure is entirely the arrangement:

| object | tunes | declared | paired |
|---|---|---|---|
| pitch.lo / pitch.hi | 69 | 69 | 69 |
| pattptr.lo / pattptr.hi, songtbl.lo / songtbl.hi | 69 | 69 | 69 |
| ins.ad / ins.sr / ins.waveptr | 69 | 67 | 69 |
| ins.pulseptr / ins.filtptr / ins.vibparam / ins.vibdelay | 48–55 | 47–55 | 48–55 |
| wtbl.left / wtbl.right, ptbl.\*, ftbl.\*, stbl.left | 55–69 | 54–68 | 54–69 |
| **orderlist.0 / .1 / .2** | 69 | 67 | **14** |
| **patterns** | 69 | 69 | **14** |
| stbl.right | 57 | **39** | 39 |

The **pitch table is read at a generated row** on 53 of 69 tunes: its cursor is not a cell
but a byte of another declared const table, read at an index of its own. On `Autumnness`,
`An_Old_Era` and `86400` that table is the **wavetable's right column** — GoatTracker's
arpeggio (its addressing base landing two bytes low, per (b) below). That is exactly the
`Index` chain of docs/tracker.md §3.2b, present in the program text and never built by the
recovery, whose structure axis reads 0.

### 2.1 The control: how much of this is containment being cheap

Address containment is only evidence if it fails when the address is wrong. `SHIFT=n`
moves every object base by `n` before matching and changes nothing else. Over the first
30 tunes (687 objects, 714 program pairs):

| every object moved by | declared | paired | paired strictly |
|---|---|---|---|
| **0 — the real map** | **667 (97.1%)** | **587 (85.4%)** | **492 (71.6%)** |
| +$40 | 548 | 458 (66.7%) | 263 |
| +$100 | 315 | 209 (30.4%) | 106 |
| +$400 | 47 | **26 (3.8%)** | 3 |

**The resolution of this match is about a page, not a table.** GoatTracker's objects are
7 to 30 bytes and packed adjacently, and `datadecl` tiles the whole block, so a 64-byte
shift usually lands on the *neighbouring* object's declaration and still "pairs". Beyond a
page the match collapses to 3.8%, so the block is found and the objects inside it are not
individually resolved by containment alone.

That is a real limit on the 85.6% figure, and the results that do **not** depend on it are
the sharp ones: the arrangement's 14-of-69 split (those objects sit in the same block as
everything else and still fail), the one-to-one with rung (f) in §3(a), and the cardinality
agreement — 25.6 pairs per tune against 26.7 editor groups — which containment cannot
manufacture.

**What this does not measure.** The match is at the level of the *object*, not the
cursor: it asks whether the program indexes the region the editor's node reads, not
whether the program's cursor is the editor's. The two are in different vocabularies — a
cell address against a song-model column name — and the only way to compare them is to
watch the cursor's per-frame values against the editor's row stream, which is precisely
the change §4 recommends. So this measurement bounds the hypothesis from above: a pair
per object is necessary for node-per-(region, cursor) to work, and it is present; whether
each pair's rows *are* the editor's rows is the next measurement, and it is the one the
implementation itself produces.

## 3. Where they do not correspond, and why

**(a) The arrangement is pointer-walked, so it has no `base[index]` load at all.** On
`MUSICIANS/A/Aleksi_Knutsi/Autumnness.sid` the three orderlists are at `$154A`, `$155C`
and `$156E` and the packed patterns at `$1580+706`. `datadecl` declares all four — as
`stream` regions of 1, 1, 1 and 14+14+6 bytes — and **no load in the program text indexes
any of them**, because the driver reaches them through `(songptr),y` and `(pattptr),y`.
`prog.resolved` is empty for this tune, so rung (f) offers no second route either.

**And that is exactly the dividing line.** Rung (f) resolves **28 deref sites over the 69
tunes** — 2 sites each on 14 tunes and **zero on the other 55** — and those 14 tunes are
**precisely** the 14 whose orderlists and patterns pair. The correspondence between
"rung (f) proved this driver's deref" and "the arrangement has a (region, cursor) pair" is
one to one, with no exception in either direction. This is docs/tracker.md §7.4's wall
arriving from a third direction, now sized against the composer's own object list: **220
of the 239 unpaired objects** are the four arrangement objects on the 55 tunes where
rung (f) resolves nothing. The other 19 are (d): `stbl.right` on 18 tunes and
`ptbl.left` on one.

**(b) A declared region is shifted and short relative to the object it holds.** The
driver addresses a 1-based table as `lda tbl-1,x`, so `datadecl`'s region base is the
*addressing* base. On `Autumnness` every instrument array straddles two regions —
`ins.ad` is `$149D+7` against declarations `$149C+7` and `$14A3+7` — and the seven
declared regions are a one-byte rotation of the composer's seven arrays. Extents are
bounded by the observed index, so `pattptr.lo` is 16 bytes of song data declared
`$147D+3`. Containment matching survives this; an identity `region == object` does not,
and **214 of the 1415** paired objects are paired only through an overlapping region
rather than a load inside their own span. The bias is worst exactly where the addressing
bias is largest: `ftbl.right` is inside its own span on 7 of 68 tunes, `stbl.left` on 3 of
57, `pitch.hi` on 36 of 69.

**(c) A cursor is live state, not a declaration.** The instrument bank's cursor on
`Autumnness` is `$1392`, an offset of the region `$138D+15` whose `mut` set marks that
offset play-written — a per-voice variable block, not const data. Corpus-wide only **65 of
1763** cursors are cells `tracker._walked` proves the play code only steps or sets by its
own text, which is the same 42-of-366 shape docs/tracker.md §6 reports for rung (f)'s
row indices.

**(d) An object no declaration reaches.** `stbl.right` — the speed table's right column,
GoatTracker's vibrato depth — is undeclared on **18 of 57** tunes: it is the last table in
the data region and `datadecl`'s extent stops short. On `Autumnness` it is `$1548+2` and
falls outside every region.

**(e) An editor node that is not a table read at all.** 434 editor nodes (17709 emits)
have no address in the song data by construction: GoatTracker's hard restart is one
global `adparam` word in the *player* (278 nodes, 13148 emits), and `ins.firstwave` is a
player constant `$09` on the 49 tunes whose packed layout omits that array under greloc's
FIXEDPARAMS optimization (156 nodes, 4561 emits). Neither is a deficiency in the
hypothesis; both are objects the composer never wrote.

**(f) DefMON: the same verdict from an independent driver, and it isolates (a).** On
`MUSICIANS/D/Dex-D/2Manu3L.sid` the row-pointer array is declared **exactly**
`$1800+256` with cursor `$1320`, and the three arrangers — DefMON's orderlist — are
declared exactly `$1B00`, `$1C00`, `$1D00`, 256 bytes each, cursor `$10EB`. **A real
editor's orderlist does pair, where the driver indexes it rather than walking a pointer.**
The one object that fails is the packed pattern stream at `$1F00`, undeclared on 2 of 3
tunes. DefMON's fifteen sidTAB "columns" are not in the object list at all: the rows are
bitmap-packed behind that pointer array, so no column is a contiguous span — an editor
object that is genuinely not a table read, and the reason the mapping decomposes it into
lanes in the first place (docs/dm-oracle.md §1).

**SID-Wizard is out of scope, by construction.** The measurement needs a frame program
lifted from 6502, and `pysidwizard` reads `.swm` only — it has no packed-`.sid`
decompiler and no packer (docs/gt-oracle.md §3.1 states the same boundary for the law).
There is no program text to extract cursors from, so no number is reported rather than a
fabricated one.

## 4. Verdict, and the first change

**The hypothesis holds.** The frame program's (region, cursor) pairs are the editor's
object set: 25.6 pairs per tune against 26.7 editor groups, 89.3% of the pairs on a
composer object, 85.6% of the objects paired and 89.6% of the editor's nodes on a paired
object — with the residual concentrated in one place, the arrangement, whose cause
(docs/tracker.md §7.4) was already named.

**Corrected — this recommendation was built and measured, and it moves nothing.** The
pairs turn out to be one to one with the lane keys the recovery already builds, so
`graph_diff`'s matched count stays at 71 of 1648 and the value partition is byte-identical;
keying the row on the cursor's *observed* value instead costs 26 of those 71, because only
9.1% of pair emits have a cursor whose value anything but the output names. The pair is a
better **name** for a node, not a finer partition. docs/tracker.md §4h is the change and
§6 the measurement, including the ceiling: 795 of the editor's nodes are reachable by
repartitioning at all, and 523 of those are trigger nodes.

**Node-per-(region, cursor) is the right partition to build.** But the first concrete
change is not the partition — it is the cursor's rows, and it is small.

`tracker._lane_key` already keys a stream by (declared lane, register), which is nearly
the same object identity. What it lacks is the **cursor**: it emits one node per
(lane, register) over the whole window, while the editor emits one per (lane, register,
arrangement group) — 4470 editor nodes against 1844 distinct groups. Our node's write set
is the *union* of several of theirs, and `graph_diff` matches on the exact set of
`(frame, register, value)` triples a node produces, so a union matches nothing. That is
why the matched count is 1–11 and not zero: the few that match are the streams the editor
happens not to split.

**Corrected again — the cursor now *generates* the row, and the population is one tune.**
docs/tracker.md §4i builds the chain the paragraph below asks for, but from the text
rather than from the value: the cursor's seed is the post-init byte, its step and modulus
are the program text, and its trigger is its own step statement watched on the same
`eval_watch` run. The rows are then *predicted* and checked against the run the machine
read. Over the corpus **203 tunes have a declared region the text indexes at a walked
cursor and 1 tune's row run the cursor reproduces** (15 emits); 58436 rows are refused
because the walk does not produce them, and 217 cursors because some writer reloads them.
Using the cursor's *observed* value as the row instead is the split docs/tracker.md §6
already priced at 26 of the 71 matched nodes, and it stays refused.

So: **watch the cursor cell and use its value as the row.** `tracker._observe` already
watches cells on the one `frameval.eval_watch` run the recovery makes (the accumulator
sites and the arrangement's own walks ride it). Adding the cursor cells of the
(region, cursor) pairs to that watch list, and building `SELECT(region lane, rows = the
cursor's per-frame values)` per pair rather than per register source, would:

1. partition per cursor rather than per register-source, so one register several cursors
   drive becomes several nodes and one cursor several registers read becomes one shared
   node — the editor's own shape;
2. give the row generator an identity, so an `Index` route has a source and the three-node
   chain of docs/tracker.md §3.2b becomes buildable for the objects that pair;
3. stay inside the project's refusal rule. The cursor **cell** is named by the program
   text; only its per-frame value is observed, which is exactly the standing the `EDGE`
   counts (docs/tracker.md §5) and the sweep seeds (§4c) already have. A row *segmented
   off the observed output stream* — the 54557 emits §6 prices and refuses — is a
   different thing and stays refused.

Only after that does the arrangement matter, and its price is already known: it is
`frameval` reporting the **resolved deref address** as the store's source cell
(docs/tracker.md §6, "What would move it is upstream"). This measurement sizes the same
change from the object side, and §3(a)'s one-to-one split says the lever is rung (f)
itself: where it resolves, the arrangement already pairs (14/14); where it does not, it
never does (0/55). Widening rung (f) past 28 sites — not a new generator shape — is what
takes 220 of the remaining 239 unpaired objects, and GoatTracker's object coverage from
85.6% to 98.9%.

## 5. A tool dependency that produced nothing

`tools/gt_compare.py` reads `out/gt_scan.json` and **no committed code produced it**; the
file is gitignored, so a clean checkout cannot run the GoatTracker comparison at all.
`tools/gt_scan.py` now produces it: it decompiles every cached `.sid` with `pygoattracker`
and records the verdict, fetching more tunes round-robin across composers first when asked.
Over the 701 cached tunes it finds **72 GoatTracker**, which is the figure
docs/gt-oracle.md §3 reports.

The same gap exists one step further out and is *not* closed here: `.oracle-cache/swm`,
which `tools/gt_compare.py` and `tests/test_gtoracle.py` both read, has no fetcher in the
tree either. SID-Wizard modules are not redistributable the way an HVSC relpath is, so
that one needs a source decision rather than a script.
