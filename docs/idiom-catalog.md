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
| normal form | the dialect term extraction must pick, or `named-unknown` |
| families | which families below spell it, named while few and counted once they are most of them |
| canonical cite | the label in the canonical source, `+$NN` bytes where the source names it |
| exemplar cite | the `tools/disasm_tune.py` range the same code sits at in that family's exemplar |
| `nodes` / `tunes` | the witness counts `tools/idiom_cover.py` measures over the exemplars |

Both cites are **computed, not transcribed**: `tools/idiom_cover.py` records the
labelled seat each row is witnessed at, `tools/source_anchor.py` binds source
labels to exemplar addresses, and `tools/idiom_cite.py` joins them — the
strongest-anchored family that spells the row, then the tightest label above the
seat. The exemplar cite is the labelled block the store sits in (seat to next
seat), so the two cites name one code and the delta is bytes in the image, not
lines in the source.

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

The gate is what grew this catalog: the nine exemplars added at stage 1's close
reported eight unaccounted nodes over one shape (`mem[zext(b ± k)]`, a page-zero
row whose index arithmetic wraps in 8 bits), which is the `zp-row` row below.

## Families

The corpus is not a handful of players — it is a long tail with a heavy head.
Of 173 clusters over 624 cached tunes, 9 hold more than 10 tunes and 164 hold
10 or fewer; the 24 families below are 66.8% of the corpus. Per family the
canonical source is cross-checked against its exemplar before any idiom is
recorded from it; the exemplar anchor binds source labels to exemplar addresses
so both cites in a row name the same code.

Two instruments answer "which player" independently. `tools/player_id.py`
names it from the SIDId byte signatures over the loaded image;
`tools/family_cluster.py` clusters by executed-code fingerprint (opcode
5-grams over executed instructions, MinHash, joined by **containment** — two
songs on one driver execute overlapping but differently sized instruction
sets, which Jaccard scores as difference). Over the 624 cached tunes the two
agree cluster for cluster: each of the largest clusters resolves to exactly one
SIDId name.

The exemplar set is **25 tunes over 24 clusters**, covering **417 of 624
(66.8%)**. It replaces the seven-tune set the plan opened with (49 tunes, 7.9%)
and the sixteen stage 1 measured (360, 57.7%). The table is
`tools/exemplars.py` — the one place the set is declared, read by every sweep
and gated against this document by `tests/test_idiom_catalog.py`.

| key | family | exemplar | tunes | SIDId name | canonical source |
|---|---|---|---:|---|---|
| `goattracker2` | GoatTracker 2 export | `Jammer/Grid_Runner` | 75 | `GoatTracker_V2.x` | GoatTracker 2 `player.s` |
| `dmc` | DMC / DMC V4 | `Daf/Alioth` | 59 | `DMC`, `DMC_V4.x` | — |
| `music-assembler` | Music Assembler | `Alfatech/Galway-tune` | 54 | `Music_Assembler`, `VoiceTracker` | — |
| `futurecomposer` | FutureComposer | `Beast/Discmonsters_Intro` | 42 | `MoN/FutureComposer` | — |
| `soundmonitor` | Soundmonitor | `Tel_Kees/Before_I_Forget` | 30 | `Soundmonitor`, `MusicMaster_1` | — |
| `jch` | JCH NewPlayer | `Deek/4_Tunes` | 20 | `JCH_NewPlayer` | — |
| `sidwizard` | SID-Wizard export | `Chabee/Angry_Birds` | 19 | `Hermit/SidWizard_V1.x` | SID-Wizard `player.asm` |
| `dmc5` | DMC V5 | `Cleve/ABC_Music` | 15 | `DMC_V5.x` | — |
| `master-composer` | Master Composer | `Buckley_Kevin/Down_Under` | 15 | `Master_Composer` | — |
| `follin` | Follin (script interpreter) | `Follin_Tim/Ghouls_n_Ghosts, Follin_Tim/Agent_X_II_The_Mad_Profs_Back` | 10 | `Stephen_Ruddy` | docs/follin-dispatch-study.md §3 (in-repo) |
| `dmc5b` | DMC V5 (second build) | `Extern/From_Beyond_main` | 10 | `DMC`, `DMC_V5.x` | — |
| `goattracker1b` | GoatTracker 1 (second build) | `Cerror/BubbleBobble` | 10 | `GoatTracker_V1.x` | — |
| `hubbard` | Hubbard (hand-coded) | `Hubbard_Rob/Commando` | 9 | `Rob_Hubbard` | McSweeney's commented disassembly |
| `romuzak` | RoMuzak V6 | `Albert_Christoph/Dynasty_8_tune_2` | 9 | `RoMuzak_V6.x` | — |
| `deflemask` | DefleMask v12 export | `Big_Lumby/An_Attempt_Was_Made` | 8 | `DefleMask_v12` | — |
| `electrosound` | Electrosound | `Gray_Matt/Atmosphere_II` | 6 | `Electrosound` | — |
| `cheesecutter` | CheeseCutter 2 | `Codex/Frantic_3_tune_5` | 6 | `CheeseCutter_2.x`, `Laxity_NewPlayer_V21` | — |
| `laxity` | Laxity NewPlayer V21 | `Laxity/21_G4_demo_tune_2` | 6 | `Laxity_NewPlayer_V21` | — |
| `defmon` | defMON | `Goto80/Automatas` | 6 | `DefMon` | undefmon `defmon.asm` |
| `goattracker1` | GoatTracker 1 export | `Cadaver/Aces_High` | 4 | `GoatTracker_V1.x` | — |
| `galway-wizball` | Galway 2nd gen (Wizball) | `Galway_Martin/Wizball` | 1 | `Martin_Galway` | the composer's own `wizball.asm` |
| `galway-rambo` | Galway 1st gen (Rambo) | `Galway_Martin/Rambo_First_Blood_Part_II` | 1 | `Martin_Galway` | the composer's own `rambload.asm` |
| `galway1` | Galway 1st gen (Comic Bakery) | `Galway_Martin/Comic_Bakery` | 1 | `Martin_Galway` | — |
| `galway2` | Galway 2nd gen (Athena) | `Galway_Martin/Athena` | 1 | `Martin_Galway` | — |

The `tunes` column is the exemplar's **cluster size**, not the family's corpus
population — the column sums to the 417 the set covers. A `—` in the source
column is a family whose idioms are read from the exemplar alone; those rows
carry the weaker warrant and say so by carrying no canonical cite.

Each exemplar is its cluster's highest-coverage member (most executed opcode
grams), except where a specific tune anchors the family: `Grid_Runner` pins the
major version the fetched GoatTracker source is, and the Galway and Follin rows
keep the tunes their sources and study describe. `Rambo_First_Blood_Part_II` is
the corpus's one standing Gate FP divergence; it is an exemplar because it is
what `rambload.asm` is the source *of*, and what the catalog reads from it is
its lifted dataflow and its image addresses, neither of which the frame-level
divergence bears on.

Reference sources are fetched into `.oracle-cache/players/` by
`tools/fetch_players.py` and **never vendored** (the docs/nms-provenance.md
pattern); the manifest pins each file by sha256 so a citation is reproducible.

Three findings the clustering forced:

- **GoatTracker is two families in three builds.** `Aces_High`, the plan's
  GoatTracker exemplar, runs `GoatTracker_V1.x` while the fetched canonical
  source is the **V2** player; the 75-tune V2 cluster — the corpus's largest
  single-player family — had no exemplar at all, and V1 itself splits into two
  clusters holding 4 and 10.
- **Galway is two players in four builds.** The composer's README dates the
  1st-generation player 1984–mid-1987 and names `Athena` as the first
  2nd-generation player. The fingerprint splits `Comic_Bakery`, `Rambo`,
  `Wizball` and `Athena` into four size-1 clusters; only `Rambo` and `Wizball`
  have published source, and they are the two Galway rows that can cite it.
- **DMC is three builds**, of which two are `DMC_V5.x` by signature and
  distinct by fingerprint.

Families fragment **by build**, so "one exemplar per family" is really one
exemplar per cluster, and a family's claim extends only as far as its
exemplar's cluster.

### What the set does not cover

149 clusters holding 207 tunes have no exemplar. The largest is 5 tunes
(`HardTrack_Composer`); the rest are 4 or fewer, so the head is exhausted and
each further exemplar now buys single digits. `tools/family_cluster.py` prints
the ranked orphans, so an addition is a measurement, never a discovery.

### Anchors

An anchor is a verified source-label ↔ exemplar-address binding, so a row's two
cites name the same code, and it is what licenses a canonical cite at all: a
row's cite comes from the exemplar the anchor was computed against and from no
other build.

| family | binding | evidence |
|---|---|---|
| defMON | `player_play` = `$1003`, `sub_frame_player_update` = `$1006` | `Automatas` `play` = `$0FE3` is the export stub: an SMC frame divider (`LDA #$00` at `$0FE3` whose immediate cell `$0FE4` is `INC`'d each frame) masked `AND #$07`, calling `JSR $1003` on the main tick and `JSR $1006` otherwise — the addresses `defmon.asm` names |
| Follin | every operator's handler address, `$6858`–`$6C8A` in `Ghouls_n_Ghosts` | study §3 tabulates op byte → handler address directly; v2/v3 are `+$0F`/`+$1E` mirrors, dispatch sites `$6374`/`$6561`/`$6750` (§1) |

The Follin reference is already anchored because it was derived from the
exemplar rather than fetched: the grammar's citations *are* exemplar addresses.
`tools/idiom_cite.py` reads that table straight out of the study and maps a
v2/v3 seat back through the mirror displacement, so a Follin cite names the op
whose handler the seat sits in.

The remaining families' sources are symbolic assembly with no absolute
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

| family | source → exemplar | aligned | longest run | labels (run ≥32 / ≥64) | held-out mode | cites at run ≥ |
|---|---|--:|--:|---|--:|--:|
| `galway-rambo` | `rambload.asm` → `Rambo` | 1050 (96.2%) | 993 | 214 / 214 | 99.7% | 32 |
| `galway-wizball` | `wizball.asm` → `Wizball` | 990 (49.4%) | 161 | 139 / 91 | 100% | 32 |
| `sidwizard` | `player.asm` → `Angry_Birds` | 704 (42.0%) | 116 | 53 / 22 | 99.2% | 32 |
| `hubbard` | `rob_hubbards_music.txt` → `Commando` | 214 (55.6%) | 100 | 15 / 9 | 99.1% | 64 |
| `goattracker2` | `player.s` → `Grid_Runner` | 251 (35.2%) | 51 | 25 / 0 | 96.4% | 32 (provisional) |
| `defmon` | `defmon.asm` → `Automatas` | 470 (3.9%) | 192 | 60 / 60 | 100% | 16 |

Four of these numbers are findings, not shortfalls:

- **Hubbard's 44% non-alignment is the "slight modifications" claim, measured.**
  The source is the *Monty on the Run* driver, which the article says was
  reused with slight modifications in Commando. 55.6% aligns in 5 runs; the
  rest is the modification.
- **GoatTracker's weakness is a build difference.** `Grid_Runner` ships a
  differently-configured GoatTracker than `player.s`: the shipped player
  indexes with **X** where the source writes `,Y`, which is 9 of its mode
  mismatches, and no run reaches 64. `altplayer.s` does no better. GoatTracker
  rows are provisional until the matching build is found.
- **defMON aligns 3.9% of its source because the source is the whole editor.**
  `defmon.asm` is 12,013 instructions of editor plus player and the tune runs
  the player alone; the match is 470 instructions in 8 runs, longest 192, with
  470/470 held-out mode agreement.
- **defMON's exemplar is a later build than the disassembly.** The control
  reports four constant-displacement classes (`+$21` on 294 instructions,
  `+$28` on 86, `+$24` on 80, `0` on 10): the player head is where the source's
  declared addresses hold, and everything past the first insertion is shifted.
  This is what a correct alignment across builds looks like — noise would be
  many classes of one — and it is why a defMON cite prints the source's line
  and the exemplar's own address rather than the address the source declares.

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

### The other enumerations the catalog reuses

Stage 1 was not to re-derive what the repository already enumerated:

- **`ptrcert`'s definition-kind census** (`reload`, `advance`, `save_restore`,
  `block_read`, `other`) is the enumeration of the shapes a pointer *definition*
  takes; `deref-row` accounts their reads, and rung (g) in `ptrlift.py` already
  lifts the certified ones. The catalog adds no row per kind.
- **The §5.4 shredder fixture family** (`tests/test_shred_regmodel.py`, 24
  `xfail(strict=True)` fixtures) is not catalog material: each pending fixture
  is a stage 3 convergence seed, normalizing there or re-pinned as a guarded
  refusal with its reason.
- **The Follin grammar** enters as the anchors above and the arity debt below,
  not as rows: it names operators, and an operator is a dispatch arm, not a
  spelling of a value.

## The Follin arity debt — discharged (stage 3d)

`follin_script._ARITY` was a hand-transcribed per-tune table and the one
standing exception to the governance rule; `deity_informant/follin_arity.py`
recovers it and it is deleted, so no table stands in its place. The definition
this section recorded — **an operator's arity is the net `Y` delta of its
dispatch arm, constant on all paths through that arm** — is mechanical because
the family advances its stream pointer in batch at tick end by folding `Y` in
(`TYA`/`ADC`), so Y counts exactly the bytes the arm consumed
(docs/follin-dispatch-study.md §2). An arm that rewrites the stream pointer
rather than advancing it never folds, and that is where the definition needed
one correction.

**What the recovery landed as.** An operator's arity is the arm's
consumption footprint: the stream offsets it fetches through the voice's
pointer, walked over the lifted blocks at each block's least `Y`. On the 18
arms that fold, the footprint *is* the net `Y` delta. On `$87` (`jump`) and
`$8A` (`call`) the delta is one short — they read a 16-bit operand and rewrite
the pointer without counting its second byte — so the footprint is the reading
that covers both, and the delta is recorded beside it as the corroboration it
is. Nothing else is told to the recovery: the stream is the pointer the
dispatch's own fetch uses, the operator range is the guard's floor plus the
tightest spacing of the paired handler tables (21 slots, `$80`–`$94`, on
`Ghouls_n_Ghosts`), and the arms come from the paired table image.

**The variable-arity operator got the escape, not a refusal.** Op `$85`
(`rawsid`, handler `$6909`) consumes `(reg, val)` pairs while `reg < $80`, then
one terminator byte; its `Y` delta is data-dependent by construction. The
recovery reads its counted loop as a decoded length — first guarded offset 3,
stride 2, trailer 1, continue while the byte is under `$80` — and the decoder
consumes exactly that, so the escape is derived per build rather than written
in. An arm with no such loop stays a named refusal with its reason.

The discharge is executable: `tests/test_follin_arity.py` holds the 20-entry
transcription as the witness and asserts the recovery reproduces it op for op
on `Ghouls_n_Ghosts`. It also holds the reason the table was debt —
`Agent_X_II` is a second build with 17 operators of its own, `$84` taking no
operand where Ghouls' takes one, and the deleted table spoke for it too.

## Rows

23 rows, in match order — the order matters, since a specific idiom must be
tried before the general vocabulary that would otherwise absorb it. `nodes` and
`tunes` are the witness counts over the 25 exemplars at full Songlengths
(`tools/idiom_cover.py`, 2,205 obligations: 475 SID stores, 1,730 cell
updates); the cites are `tools/idiom_cite.py`. Every row is witnessed; rows
with no witness were deleted rather than kept on speculation, so the vocabulary
is exactly what the exemplars spell.

| id | normal form | families | canonical cite | exemplar cite | nodes | tunes |
|---|---|---|---|---|---:|---:|
| `pair-row` | u16 cell/table row | 21 of 24 | `defmon.asm:4158` sidtab_cascade_entry | Automatas $12BE | 122 | 22 |
| `word-pack` | u16 value | 13 of 24 | `wizball.asm:2267` CheckFilter+$1B | Wizball $5149-$523F | 48 | 14 |
| `lane-insert` | u16 lane update | 16 of 24 | `rob_hubbards_music.txt:948` firstime | Commando $532B | 41 | 16 |
| `hi-byte` | u16 high byte | 7 of 24 | `defmon.asm:4158` sidtab_cascade_entry | Automatas $12BE | 13 | 7 |
| `lo-byte` | u16 low byte | 6 of 24 | `defmon.asm:4158` sidtab_cascade_entry | Automatas $12BE | 8 | 6 |
| `shift-pair` | u16 shift | 5 of 24 | `wizball.asm:1725` FILTER+$59 | Wizball $4D10-$4D5B | 12 | 6 |
| `adc-chain` | wide add | 13 of 24 | `player.asm:1747` combiKT+$0C | Angry_Birds $0E9B-$0EBF | 50 | 13 |
| `sbc-chain` | wide subtract | 5 of 24 | `defmon.asm:4158` sidtab_cascade_entry | Automatas $12BE | 9 | 5 |
| `shift-chain` | one shift | 16 of 24 | `wizball.asm:1725` FILTER+$59 | Wizball $4D10-$4D5B | 78 | 17 |
| `flag-bit` | flag | 4 of 24 | — | — | 11 | 4 |
| `table-row` | table[i] | 23 of 24 | `follin-dispatch-study.md` op $8B ret | Ghouls_n_Ghosts $6B31-$6B42 | 613 | 24 |
| `zp-row` | zp table[i] | deflemask, galway-wizball | `wizball.asm:1361` next0 | Wizball $49F2-$4A20 | 8 | 2 |
| `cell-read` | cell | 24 of 24 | `follin-dispatch-study.md` op $82 loop | Ghouls_n_Ghosts $6858-$6871 | 1413 | 25 |
| `stack-slot` | named-unknown | 8 of 24 | — | — | 23 | 8 |
| `deref-row` | *ptr[i] | 15 of 24 | `follin-dispatch-study.md` op $82 loop | Ghouls_n_Ghosts $6858-$6871 | 315 | 16 |
| `mask-const` | field select | 23 of 24 | `defmon.asm:3889` V0_row_read_tail | Automatas $119C-$1224 | 154 | 24 |
| `set-const` | field set | 12 of 24 | — | — | 50 | 13 |
| `alu-op` | wide operator | 24 of 24 | `follin-dispatch-study.md` op $82 loop | Ghouls_n_Ghosts $6858-$6871 | 885 | 25 |
| `widen` | width coercion | 13 of 24 | — | — | 66 | 14 |
| `carry-value` | named-unknown | 9 of 24 | `follin-dispatch-study.md` op $82 loop | Ghouls_n_Ghosts $6858-$6871 | 40 | 10 |
| `compare-value` | named-unknown | 9 of 24 | — | — | 29 | 9 |
| `const-literal` | const | 24 of 24 | `follin-dispatch-study.md` op $82 loop | Ghouls_n_Ghosts $6858-$6871 | 939 | 25 |
| `local-read` | local | 24 of 24 | `follin-dispatch-study.md` op $82 loop | Ghouls_n_Ghosts $6858-$6871 | 1330 | 25 |

Of 6,257 accounted nodes, **4,633 (74.0%) are plain vocabulary**
(`cell-read`, `local-read`, `alu-op`, `const-literal`, `widen`), **1,532
(24.5%) are idiom instances**, and **92 (1.5%) are the named-unknown residue**.
That last number is the steering metric this document offers Stage 3: it is
what the minimizer still owes, and it should fall to zero.

**18 of the 23 rows carry a canonical cite** (defmon 5, galway-wizball 4,
follin 7, hubbard 1, sidwizard 1). The five that do not — `flag-bit`,
`stack-slot`, `set-const`, `widen`, `compare-value` — are witnessed only in
families with no published source, or at seats outside the anchored runs of the
families that have one; their warrant is the exemplars' lifted dataflow and
this is where that is said. Four of the five are the general vocabulary or a
named-unknown; none is a u16 idiom.

### What a zero-gap run does and does not prove

`tools/idiom_cover.py` reports **0 unaccounted over the 25 exemplars**,
and the gate does discriminate — `INT_NEGATE`, `INT_SREM`, a bare machine
register and a `mem` whose address is neither const, base+index, page-zero
indexed nor pointer-rooted are all reported unaccounted. But the same row set
covers *every expression of every emitted program* in the exemplars with zero
gaps too. So the honest reading is:

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

The counter-instance is `zp-row`: growing the exemplar set from 16 to 25 turned
a shape that had been invisible into eight reported gaps in two families, and
the gate refused to pass until the row existed. Coverage of the *vocabulary* is
a gate; coverage of the *idioms* is the exemplar set, and it is why the set is
declared in one place and grown by measurement.

## Named unknowns

Three rows carry `named-unknown`: the idiom is inventoried and its sites are on
the record, and no normal form is claimed for it yet. Sites are in
`out/idiom_cover.json`.

| id | nodes | tunes | what it is | what it owes |
|---|---:|---:|---|---|
| `carry-value` | 40 | 10 | `carry(..)` surviving as a value — the flag outlived the operation that set it | fusing into `adc-chain`/`sbc-chain`'s wide form, the `carry_fuse` Stage 3 names |
| `compare-value` | 29 | 9 | a comparison feeding arithmetic — a borrow chain, unfolded | the wide compare it is a lane of |
| `stack-slot` | 23 | 8 | a byte of the `$0100` page read as a value | the memory sort plus the joins, per shape — the seats are read below |

### The `stack-slot` seats, read

All 12 seats `out/idiom_cover.json` records were read against the machine
(claims discipline). They fall in four shapes, and each is a memory-sort
obligation rather than a missing pass. The `$0100` interval every one of them
needs is the one stage 3b's bridge seeds from `zext2(sp) | $0100`.

- **Push, use, pull — one statement list.** `4_Tunes` `$13F7`
  (`LDA table,Y; PHA; LSR×4; STA hi; PLA; AND #$0F; STA lo`), `Alioth` `$E0B0`
  (three of them: `mem[t:2] = w; …; w' = mem[t:2]`, nibble splits feeding
  `sustain_release`, `filter.mode_vol` and `m_E771`/`m_E774`),
  `Discmonsters_Intro` `$1806` and one of `Angry_Birds` `$0CFC`'s two. Nothing
  writes the slot between store and load, so `sel_store_same` discharges it.
  **Already measured working**: 3b's exemplar measurement deleted 2 statements
  from `4_Tunes`, both stack pushes the interval bridge bounds.
- **A pushed value crossing a join.** `Atmosphere_II` `$0FA4`/`$0FCE` (5 nodes,
  conditional pop/re-push, final `PLA` → `STA $D404,Y`); `Athena` `$C17D`,
  where each arm pushes its own SMC opcode byte (`$C185 LDA #$A9 / PHA` against
  `$C198 LDA #$60 / PHA`), the arms converge at `$C1A1` and `$C1AA PLA /
  STA $C325` pulls it; `Angry_Birds` `$0CFC`'s other one, a `PHP` in both arms
  of an `if`/`else` pulled after the join; `4_Tunes` `$11D9`, a command byte
  popped in dispatch arms; and `Alioth`'s push across `call $E84B`. The
  memory join 3b's second landing owes is the discharge — a branch join for all
  but the last, a call boundary for it.
- **An entry save/restore of a zero-page pair.** `Angry_Birds` `$0903` →
  `$09F1: LDA $FE / PHA / LDA $FF / PHA … $0A2F: PLA / STA $FF / PLA / STA $FE
  / RTS`; `From_Beyond_main` `$1003` → `$1095` with `$FA`/`$FB`; `4_Tunes`
  `$1003` → `$10C0` with `$FB`/`$FC`. All three seats are `JMP` trampolines —
  the pushes and pulls sit in the block they enter. This is
  `store(m,a,sel(m,a)) = m`, the redundant-store axiom, with phase 2c's balance
  fixpoint supplying the disjointness of the callees' own stack traffic between
  push and pull.
- **Return-address bytes.** `Automatas` `$0FE3`, in the documented export stub.
  Phase 2c's balance fixpoint owns the call fabric; this is a census-accounting
  exclusion, not an unsolved lift.

## Obligations the exemplars do not pose

Two accounting facts the tool reports so the "frame-surviving" claim is
audited rather than assumed:

- **454 of 1,417 state cells receive no store during play** (Wizball 125/252,
  Comic_Bakery 69/155, Before_I_Forget 63/134): read-only rows staged by init
  and declared as state. There is nothing for the catalog to account, but they
  are counted and listed per tune.
- **42 stores across the 25 have addresses that do not resolve** to a
  base+index form. 27 of them are wholly open — `addr_bits` leaves the high
  byte unconstrained — so they are enumerated at base `unresolved` as possible
  SID stores, which is the conservative attribution `_target` owes; the other
  15 are constrained enough to name a state cell and no further. No SID store
  is silently missing from the claim.

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
