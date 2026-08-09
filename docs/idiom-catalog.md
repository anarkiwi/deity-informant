# Idiom catalog (Stage 1 of docs/register-model-lift-impl.md)

The inventory of driver idioms the rule set is tested against, derived from the
canonical player sources rather than from 624 binaries. Each row names one
spelling a driver uses, cites it in a canonical source and in a corpus
exemplar, and states **the normal form it must reduce to**. From this document
forward the catalog, not a corpus census, is the claim of coverage.

The catalog is not a license. Recognition names; the guards and the Z3
admissions license (docs/soundness.md, docs/eqlift-adoption.md §4).

## What a row means

| column | content |
|---|---|
| `id` | stable kebab-case key; the recognizer in `deity_informant/idioms.py` carries the same key, and `tests/test_idiom_catalog.py` parses this document to gate the two sets (and their order) equal |
| canonical cite | player and label/address in the canonical source the idiom is read from |
| exemplar cite | `tools/disasm_tune.py` range in a corpus exemplar carrying it |
| families | which of the families below spell it |
| normal form | the dialect term extraction must pick, or `named-unknown` |

The cite and families columns are **the stage-1 remainder**: the rows below
carry ids, normal forms and witness counts today, and their warrant is the
exemplars' lifted dataflow, not yet a reading of the canonical sources. The
anchors below (with their trust tiers) exist so those cites can be written;
until a row carries them, it claims "the exemplars spell this", nothing more.
The remainder is tracked in the plan's decision log.

A `named-unknown` row is a refusal on the record: the idiom is inventoried and
its sites are cited, and no normal form is claimed for it yet. It is not a gap
in the accounting — an unaccounted node is.

## The completeness contract

`tools/idiom_cover.py` enumerates, per exemplar, the two obligations Stage 1
names — every **SID-store dataflow slice** and every **frame-surviving cell** —
and accounts them against this catalog. The unit of accounting is a **node**,
not a slice root: every node of every slice must be consumed by some row's
recognizer, and a node no row matches is reported with its site so
`tools/disasm_tune.py` can be pointed at it. An unaccounted node names a
missing catalog row; the tool exits non-zero, so it gates.

Slices are taken with locals resolved through their defining assignments
(`frameproc.Defs.resolve`, which answers at the reader's position), because a
store whose value is a bare temporary carries its idiom in the definition, not
at the sink.

## Families

The corpus is not a handful of players — it is a long tail with a heavy head.
Of 173 clusters over 624 cached tunes, 9 hold more than 10 tunes and 164 hold
10 or fewer; the sixteen families below are 57.7% of the corpus. Per family the canonical source is
cross-checked against its exemplar before any idiom is recorded from it; the
exemplar anchor binds source labels to exemplar addresses so both cites in a
row name the same code.

Two instruments answer "which player" independently. `tools/player_id.py`
names it from the SIDId byte signatures over the loaded image;
`tools/family_cluster.py` clusters by executed-code fingerprint (opcode
5-grams over executed instructions, MinHash, joined by **containment** — two
songs on one driver execute overlapping but differently sized instruction
sets, which Jaccard scores as difference). Over the 624 cached tunes the two
agree cluster for cluster: each of the largest clusters resolves to exactly one
SIDId name.

The exemplar set is **sixteen tunes**, one per family, and it replaces the
seven-tune set the plan opened with: measured against the clustering, the seven
covered 49 of 624 tunes (7.9%). The sixteen fall in 15 clusters covering **360
of 624 (57.7%)**.

| family | exemplar | tunes | SIDId name | canonical source |
|---|---|---:|---|---|
| Hubbard (hand-coded) | `Hubbard_Rob/Commando` | 9 | `Rob_Hubbard` | McSweeney's commented disassembly |
| Galway 1st gen (1984–mid-1987) | `Galway_Martin/Comic_Bakery` | 1 | `Martin_Galway` | the composer's own sources |
| Galway 2nd gen | `Galway_Martin/Athena` | 1 | `Martin_Galway` | not published |
| Follin (script interpreter) | `Follin_Tim/Ghouls_n_Ghosts`, `Agent_X_II` | 10 | `Stephen_Ruddy` | docs/follin-dispatch-study.md §3 (in-repo) |
| GoatTracker 2 export | `Jammer/Grid_Runner` | 75 | `GoatTracker_V2.x` | GoatTracker 2 `player.s` |
| GoatTracker 1 export | `Cadaver/Aces_High` | 4 | `GoatTracker_V1.x` | — |
| DMC / DMC V4 | `Daf/Alioth` | 59 | `DMC`, `DMC_V4.x` | — |
| DMC V5 | `Cleve/ABC_Music` | 15 | `DMC_V5.x` | — |
| Music Assembler | `Alfatech/Galway-tune` | 54 | `Music_Assembler`, `VoiceTracker` | — |
| FutureComposer | `Beast/Discmonsters_Intro` | 42 | `MoN/FutureComposer` | — |
| Soundmonitor | `Tel_Kees/Before_I_Forget` | 30 | `Soundmonitor`, `MusicMaster_1` | — |
| JCH NewPlayer | `Deek/4_Tunes` | 20 | `JCH_NewPlayer` | — |
| SID-Wizard export | `Chabee/Angry_Birds` | 19 | `Hermit/SidWizard_V1.x` | SID-Wizard `player.asm` |
| Master Composer | `Buckley_Kevin/Down_Under` | 15 | `Master_Composer` | — |
| defMON | `Goto80/Automatas` | 6 | `DefMon` | undefmon `defmon.asm` |

The `tunes` column is the exemplar's **cluster size**, not the family's corpus
population — the column sums to the 360 the sixteen cover. Where SIDId names
more tunes than the cluster holds, the family fragments across clusters: SIDId
names five `Martin_Galway` tunes, and the three outside the two exemplars'
clusters (`Commando_High-Score`, `Rambo_First_Blood_Part_II`, `Wizball`) sit
in clusters with no exemplar. The anchors below still read Rambo and Wizball
directly, because the alignment targets the image, not the cluster.

Each exemplar is its cluster's highest-coverage member (most executed opcode
grams), except where a specific tune anchors the family: `Grid_Runner` is the
GoatTracker 2 exemplar because it pins the major version the fetched source is,
and the Galway and Follin rows keep the tunes their sources and study describe.

Reference sources are fetched into `.oracle-cache/players/` by
`tools/fetch_players.py` and **never vendored** (the docs/nms-provenance.md
pattern); the manifest pins each file by sha256 so a citation is reproducible.
A `—` in the source column is a family whose idioms must be read from the
exemplar alone until a canonical source is found; those rows carry the weaker
warrant and say so.

Two findings the clustering forced, both now folded into the table above:

- **GoatTracker is two families.** `Aces_High`, the plan's GoatTracker
  exemplar, runs `GoatTracker_V1.x`, while the fetched canonical source is the
  **V2** player and the 75-tune V2 cluster — the corpus's largest single-player
  family — had no exemplar at all. `Grid_Runner` anchors V2; `Aces_High` stays
  as the V1 exemplar.
- **Galway is two players.** The composer's README dates the 1st-generation
  player 1984–mid-1987 and names `Athena` as the first 2nd-generation player.
  The fingerprint splits them independently, and only the 1st generation has
  published source.

### The next exemplars, already surfaced

The same two instruments name the largest clusters the set does not cover, so
the additions the plan calls for are queued, not waiting to be discovered.
Cluster sizes from `out/family_cluster.json`, names from
`out/player_id_corpus.json`:

| cluster size | SIDId name | note |
|---:|---|---|
| 10 | `GoatTracker_V1.x` | a **second** V1 cluster; `Aces_High`'s holds only 4 of SIDId's 15 |
| 10 | `DMC`/`DMC_V5.x` | a **third** DMC cluster, containing neither DMC exemplar |
| 9 | `RoMuzak_V6.x` | no exemplar |
| 8 | `DefleMask_v12` | no exemplar |
| 6 | `Electrosound` | no exemplar |
| 6 | `CheeseCutter_2.x` | mixed signatures (Laxity-player derivative) |
| 6 | `Laxity_NewPlayer_V21` | no exemplar |

Adding these seven raises coverage 360 → 415 of 624 (66.5%). The GoatTracker
and DMC rows carry the standing lesson: families fragment **by build**, so
"one exemplar per family" is really one exemplar per cluster, and a family's
claim extends only as far as its exemplar's cluster.

### Anchors

An anchor is a verified source-label ↔ exemplar-address binding, so a row's two
cites name the same code. Established so far:

| family | binding | evidence |
|---|---|---|
| defMON | `player_play` = `$1003`, `sub_frame_player_update` = `$1006` | `Automatas` `play` = `$0FE3` is the export stub: an SMC frame divider (`LDA #$00` at `$0FE3` whose immediate cell `$0FE4` is `INC`'d each frame) masked `AND #$07`, calling `JSR $1003` on the main tick and `JSR $1006` otherwise — the addresses `defmon.asm` names |
| Follin | every operator's handler address, `$6858`–`$6C8A` in `Ghouls_n_Ghosts` | study §3 tabulates op byte → handler address directly; v2/v3 are `+$0F`/`+$1E` mirrors, dispatch sites `$6374`/`$6561`/`$6750` (§1) |

The Follin reference is already anchored because it was derived from the
exemplar rather than fetched: the grammar's citations *are* exemplar addresses.

The remaining four families' sources are symbolic assembly with no absolute
addresses, so `tools/source_anchor.py` computes their anchors by
**opcode-sequence alignment**: operands carry absolute addresses and are
discarded, the mnemonic sequence survives relocation, and unique 8-gram seeds
are extended and chained. The image side decodes linearly but **resyncs at
traced instruction seats**, so unexecuted arms decode too.

Three checks stand behind the method, none of which the aligner can see:

- **defMON control.** Computed `player_init`/`player_play`/
  `player_sound_update` = `$1000`/`$1003`/`$1006`, matching both the source's
  declared addresses and the export stub's `JSR` targets.
- **External addresses.** `playmusic` → `$5012` is exactly Commando's PSID
  `play`; `MUS` → `$6F00` is exactly Rambo's load address. The aligner never
  reads the PSID header.
- **Held-out validator.** The addressing-mode class the source operand's
  *syntax* states, against the mode in the image's opcode byte — a signal the
  alignment never consumes. 470/470 on defMON, ≥96% on every family.

| family | source → exemplar | aligned | longest run | labels (run ≥32 / ≥64) | held-out mode | trust |
|---|---|--:|--:|---|--:|---|
| galway | `rambload.asm` → `Rambo` | 1050 (96.2%) | 993 | 214 / 214 | 99.7% | **cite freely** |
| galway | `wizball.asm` → `Wizball` | 990 (49.4%) | 161 | 139 / 91 | 100% | **cite freely** |
| sidwizard | `player.asm` → `Angry_Birds` | 704 (42.0%) | 116 | 53 / 22 | 99.2% | cite at run ≥32 |
| hubbard | `rob_hubbards_music.txt` → `Commando` | 214 (55.6%) | 100 | 15 / 9 | 99.1% | cite the 9 anchors on runs ≥64 |
| goattracker | `player.s` → `Grid_Runner` | 251 (35.2%) | 51 | 25 / 0 | 96.4% | **provisional** |

Two of these numbers are findings, not shortfalls:

- **Hubbard's 44% non-alignment is the "slight modifications" claim, measured.**
  The source is the *Monty on the Run* driver, which the article says was
  reused with slight modifications in Commando. 55.6% aligns in 5 runs; the
  rest is the modification.
- **GoatTracker's weakness is a build difference.** `Grid_Runner` ships a
  differently-configured GoatTracker than `player.s`: the shipped player
  indexes with **X** where the source writes `,Y`, which is 9 of its mode
  mismatches, and no run reaches 64. `altplayer.s` does no better. GoatTracker
  rows are provisional until the matching build is found.

Every row in `out/source_anchor.json` carries its run length and its run's mode
agreement, so a thin match inside a repetitive band is visible as one.

## The two-byte axis

docs/twobyte-lift.md already enumerated the shapes a 6502 has for updating one
16-bit quantity as two bytes: lane addressing (`zp`, `zp,X`, `abs`, `abs,X`,
`abs,Y`) x adjacent or split lanes x operation x step operand mode x how the
carry crosses x straight-line or bounded-accumulator. **610 shapes.** That
enumeration is not re-derived here; it enters the catalog as one row's declared
parameter space, whose normal form is a u16 update of the pair.

The distinct spellings on that axis are exactly what Stage 3's convergence
tests must merge into one e-class — the carry crossing as an `INT_CARRY` value,
as control flow (`BCC`/`INC`), as a shift bit (`ROL`/`ROR`), as a predicated
counter (`INC`/`BNE`/`INC`, `DCP`, `ISC`), or absent entirely (the 150 bitwise
shapes). The definition that covers all of them mentions no carry: two byte
cells jointly update one 16-bit quantity iff the concatenation of the values
they write is a width-2 function of the concatenation of the values they held.

## The Follin arity debt

`follin_script._ARITY` is a hand-transcribed per-tune table and the one
standing exception to the governance rule; no second table may join it. The
mechanical definition that replaces it, recorded here so Stage 3 can execute
it: **an operator's arity is the net `Y` delta of its dispatch arm, constant on
all paths through that arm.** The definition is mechanical because the family
advances its stream pointer in batch at tick end by folding `Y` in
(`TYA`/`ADC`), so Y counts exactly the bytes the arm consumed
(docs/follin-dispatch-study.md §2). An arm that rewrites the stream pointer
rather than advancing it is control, not consumption, and is classified
separately.

The transcription is already corroborated in-repo, which lowers the risk on
that discharge: study §3's operator table, read off the handler code and
validated against instrumented dispatch counts, agrees with all **20** of
`_ARITY`'s constant arities, op for op.

**The definition is incomplete, and §3 says where.** Op `$85` (`rawsid`,
handler `$6909`) has arity `var` — it consumes `(reg, val)` pairs while
`reg < $80`, then one terminator byte. Its `Y` delta is data-dependent by
construction, so no "constant on all paths" rule can recover it, and that is
exactly why `_ARITY` has no `$85` entry and the decoder stops there. Stage 3
therefore owes either a decoded-length escape for variable-arity arms or a
named refusal for this one; a constancy check alone will report `$85` as a
failure of the mechanism when it is a property of the operator.

`_ARITY` is deleted when the recovered arities equal the transcription on the
20 constant ops and `$85` is handled explicitly — the executable test that the
mechanism is real.

## Rows

22 rows, in match order — the order matters, since a specific idiom must be
tried before the general vocabulary that would otherwise absorb it. `nodes` and
`tunes` are the witness counts over the sixteen exemplars at full Songlengths
(`tools/idiom_cover.py`, 1,474 obligations: 326 SID stores, 1,148 cell
updates). Every row is witnessed; rows with no witness were deleted rather than
kept on speculation, so the vocabulary is exactly what the exemplars spell.

| id | normal form | nodes | tunes |
|---|---|---:|---:|
| `pair-row` | u16 cell/table row | 91 | 14 |
| `word-pack` | u16 value | 32 | 12 |
| `lane-insert` | u16 lane update | 21 | 10 |
| `hi-byte` | u16 high byte | 13 | 7 |
| `lo-byte` | u16 low byte | 8 | 6 |
| `shift-pair` | u16 shift | 8 | 4 |
| `adc-chain` | wide add | 23 | 9 |
| `sbc-chain` | wide subtract | 8 | 4 |
| `shift-chain` | one shift | 59 | 11 |
| `flag-bit` | flag | 7 | 2 |
| `table-row` | `table[i]` | 471 | 16 |
| `cell-read` | cell | 955 | 16 |
| `stack-slot` | `named-unknown` | 16 | 6 |
| `deref-row` | `*ptr[i]` | 245 | 11 |
| `mask-const` | field select | 106 | 15 |
| `set-const` | field set | 43 | 11 |
| `alu-op` | wide operator | 622 | 16 |
| `widen` | width coercion | 55 | 11 |
| `carry-value` | `named-unknown` | 38 | 9 |
| `compare-value` | `named-unknown` | 14 | 5 |
| `const-literal` | const | 601 | 16 |
| `local-read` | local | 941 | 16 |

Of 4,377 accounted nodes, **3,174 (72.5%) are plain vocabulary**
(`cell-read`, `local-read`, `alu-op`, `const-literal`, `widen`), **1,135
(25.9%) are idiom instances**, and **68 (1.6%) are the named-unknown residue**.
That last number is the steering metric this document offers Stage 3: it is
what the minimizer still owes, and it should fall to zero.

### What a zero-gap run does and does not prove

`tools/idiom_cover.py` reports **0 unaccounted over the sixteen exemplars**,
and the gate does discriminate — `INT_NEGATE`, `INT_SREM`, a bare machine
register and a `mem` whose address is neither const, base+index nor
pointer-rooted are all reported unaccounted. But the same row set covers
*every expression of every emitted program* in the exemplars with zero gaps
too. So the honest reading is:

**a zero-gap run says every node is inside the closed dialect vocabulary; it
does not say every idiom is catalogued.** It is a vocabulary tripwire and a
regression gate, not a proof of idiom coverage. The claim of coverage the
plan wants is carried by the witnessed idiom rows above and by the residue
counter, not by the gap count.

A worked instance of the difference: `adc-chain` matches an `INT_ADD` of
exactly three operands. A carry-out add spelled with four operands
(`a + b + t + or(carry(..), carry(..))`) does not match it and decomposes into
`alu-op` plus `carry-value` — accounted, but as residue rather than as the
wide add it is. That is not a soundness fault (nothing is miscompiled and the
guards are untouched); it is the reason the residue counter, not the gap
count, is the number to steer on.

## Named unknowns

Three rows carry `named-unknown`: the idiom is inventoried and its sites are on
the record, and no normal form is claimed for it yet. Sites are in
`out/idiom_cover.json`.

| id | nodes | tunes | what it is | what it owes |
|---|---:|---:|---|---|
| `carry-value` | 38 | 9 | `carry(..)` surviving as a value — the flag outlived the operation that set it | fusing into `adc-chain`/`sbc-chain`'s wide form, the `carry_fuse` Stage 3 names |
| `stack-slot` | 16 | 6 | a stack byte the sp rung did not lift to a local | Phase 1's rung generalized, or a named refusal |
| `compare-value` | 14 | 5 | a comparison feeding arithmetic — a borrow chain, unfolded | the wide compare it is a lane of |

## Obligations the exemplars do not pose

Two accounting facts the tool reports so the "frame-surviving" claim is
audited rather than assumed:

- **247 of 887 state cells receive no store during play** (Comic_Bakery
  69/155, Before_I_Forget 63/134): read-only rows staged by init and declared
  as state. There is nothing for the catalog to account, but they are counted
  and listed per tune.
- **25 stores across the sixteen have addresses that do not resolve** to a
  base+index form, two of which can reach `$D400`–`$D41C`. These are
  enumerated at base `unresolved` via `addr_bits` rather than dropped, so no
  SID store is silently missing from the claim.

## Provenance

`tools/fetch_players.py --list` prints this table live. Every file is cached
under `.oracle-cache/players/` (gitignored) and pinned by sha256; SourceForge
URLs carry a revision and GitHub URLs a commit, so no branch moves under a pin.

| family | source | files |
|---|---|---|
| hubbard | [1xn.org](https://www.1xn.org/text/C64/rob_hubbards_music.txt) — "Rob Hubbard's Music: Disassembled, Commented and Explained", Anthony McSweeney | `rob_hubbards_music.txt` (51 KB) |
| galway | [github.com/MartinGalway/C64_music](https://github.com/MartinGalway/C64_music) @ `a458a36` — the composer's own sources | `wizball.asm` (104 KB), `rambload.asm` (44 KB), `ocean_assembler_directives.txt` |
| defmon | [github.com/anarkiwi/undefmon](https://github.com/anarkiwi/undefmon) @ `ea029d0` — annotated disassembly reassembling byte-for-byte | `defmon.asm` (1.5 MB) |
| goattracker | goattracker2 SourceForge trunk r172 | `player.s` (53 KB), `altplayer.s` (54 KB) |
| sidwizard | sid-wizard SourceForge trunk r398 | `player.asm` (114 KB), `playadapter.inc` (21 KB) |
| follin | none published | — |

The Galway sources are author-published under the repository's own terms and
are cited, not vendored, like every other reference here.
