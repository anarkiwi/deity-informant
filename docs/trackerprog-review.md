# trackerprog — a critical review of the machine and the spec

Review date 2026-09-02, against `main` at #333. The object under review is
`deity_informant/trackerprog/universal.py` (1,527 lines), the schema of
[prototype-trackerprog.md](prototype-trackerprog.md) §3–§5, and the nine hand
transliterations in `tools/trackerprog_*.py`, read as the thirty cached objects
the poison registry builds. Every number below was measured on this tree; the
scripts are one-off and not committed. Line numbers are `universal.py`'s unless
a file is named.

Contents: 1 verdict · 2 the player is not one procedure · 3 the object carries
the player · 4 dead and duplicated mechanism · 5 efficiency · 6 the spec
against the code · 7 what to do, in order.

---

## 1. Verdict

The thesis holds at its coarsest grain: no `meta.family` branch exists in
`trackerprog/`, thirty builds render on one module, and the hermetic suite
covers 94 % of that module in 1.4 s. Below that grain the claim weakens in
three ways, each measurable:

| claim | measured |
| --- | --- |
| "no per-family construct" (§1) | 18 player mechanisms have exactly one family behind them (§2.1), and a whole second modulation language — publish/subscribe over private state, 9 publish sites, 7 event kinds — exists for one family's RAM aliasing (§2.2) |
| "free of player idioms" (§4) | 5 families carry literal SID write lists the player never reads; 6 of 13 builds carry the source's voice loop direction; the SID stride `7·v` is a schema expression form; instruments and stream rows are the source's byte records under the source's own column names, and the player knows six instrument column names of its own (§3) |
| "one fixed procedure" (§4) | two target dispatchers, two act procedures, three dividers, two ends of the play list, a compiled row clock nobody calls (§4) |
| "compiled once, not walked" (§7 P8) | the row program, the clock and every `take` still walk; the machine order is rebuilt and sorted per voice-tick; guard lists of three or more terms are the top profile entry on two families (§5) |

None of this makes a certified tick wrong. It makes the *layer* harder to hold
against §4 than the document says, and it is where the object's size (§9.1)
and the lift's difficulty (backlog B6/B7) come from: what the tools hand the
player is, in large part, the source engine if-converted into guarded `sets`,
and a player that is really one procedure would refuse more of it.

---

## 2. The player is not one procedure

### 2.1 Mechanisms with one family behind them

The census is over the thirty cached objects, by writer, and the reader is
the cited line. §1's rule — two families or a marked exception — is applied
to *mechanism*, which is what a reader of `universal.py` has to hold, not to
schema rows.

| mechanism | reader | one family | what it encodes |
| --- | --- | --- | --- |
| `policy: take` | 1343–1345 | GoatTracker 2 | tone-portamento with parameter 0: snap to target |
| `reflect-complement` | 1395–1399 | GoatTracker 2 | the vibrato phase byte's complement arm |
| `row_command: held`, `state0.held`, `stage: {hold}` | 118, 265–267, 1234–1235 | GoatTracker 2 | effect memory: the last command re-runs every row |
| `rest_arm` | 1255–1256 | GoatTracker 2 | note-on re-arms the instrument vibrato; `instruments[i].accs` already exists for this and every GT2 instrument leaves it empty |
| `pitch_links` | 808–809 | GoatTracker 2 | taking a pitch zeroes the vibrato phase — the same fact `Cmd.links` spells |
| `cmd.tie` | 988 | GoatTracker 2 | a command that re-targets without re-triggering |
| a literal register as a `sets` target | 836, 883 | GoatTracker 2 | filter registers 21–24 into the shadow, beside `globals.commit`, which eight families use for the same registers. **Struck by R6**: one row was left by R1–R5 and it is `cutoff_lo` now, the same name `globals.commit` uses |
| `pitch_target` | 811 | SID Wizard | `take` writes the cell and not the chip, because a rank-25 stream adds detune and a carry afterwards |
| `stream.rate: {cell, reload}` | 757–763 | SID Wizard | a score-settable divider; JCH writes the same thing as an accumulator stepping a cursor cell |
| `stream.epoch: entry` | 773 | SID Wizard | sweep on `n` of `n+1` ticks; JCH writes it with `hold`/`next` alone |
| `insrec` | 47 | SID Wizard | the *staged* instrument's hard-restart bytes — a hook, because `stage` can copy `ins` and nothing else out of a record |
| `clamp.edge` | 1432 | SID Wizard | where the step that lands exactly on the target stops |
| `amplitude: {count, cell}` | 1421–1424 | Walker | a triangle whose cell two modulators share turns on a count |
| `meta.stop: sequencer` | 131, 593, 606 | Galway | stop the clock, keep the engine |
| `emit: entry` | 1368 | Hubbard | produce the value the step had, not the one it leaves |
| `flag.seed` (required, 1385) | 1385 | Hubbard | the carry of a `repeat` before its first addition |
| `flag.unguarded` | 1366–1367 | defMON | the carry on a tick the delta did not run |
| `Acc.beyond`, `Acc.trap` | 815, 1346 | Hubbard | the arpeggio past the tuning; an arm the horizon never takes |
| guarded `shadow.registers` entries | 111–113 | JCH | the flush direction chosen by the frame's delay byte — a write order, which §3.1 says is not a datum |
| `stopping` + `globals.stop_writes` | 134, 515–519, 643–644, 1118–1119 | Hubbard | the tune's `$FE` end: the tick is abandoned for the remaining voices, a literal write list goes out next tick, then only the flush; eight families carry `stop_writes: []` |
| `sid_base` | 48 | Hubbard, SID Wizard | `7·v` as a value — the `X = 7v` idiom §4 says S4–S6 erase, spent by SID Wizard 1.6 to reproduce a *bug* (`freq_hi` written from the register base; `trackerprog_sidwizard.py:334,681`) and by Hubbard for a drum pitch that is a u16 of two voice-state bytes. **Struck by R6**, and the row was half wrong about Hubbard: the reader is `accs.arpeggio.beyond.words[1]` and no drum — `u16(14, sid_base(reader))`, the byte at `$54EB`, which is the routine's own index into the per-voice arrays past the pitch table. After #337 that byte is stated outright, as the voice cell `voicebase` seeded from the tune's own table at `$54E8..$54EA`, and it is read (120 of song 1's beyond-word reads over its whole horizon) so it could not be a trap. SID Wizard's is the marked form: `{"bug": "voice_base"}`, whose one value is named as the defect and whose every other name refuses to compile |

The count is eighteen if `sid_base` is taken as two-family; it is one player
idiom either way. The backlog's B8 lists about half of these as *schema* rows;
the rest are code paths, and a code path with one exemplar is a code path the
next family can silently break.

### 2.2 A second modulation language, for one family

`Player` carries `priv`, `subs`, `heard`, `owners()`, `publish()` and a
`__getstate__`/`__setstate__` pair that re-keys private state by enumeration
order (97–100, 204–231, 487–494), and nine `publish` sites for seven events —
`sound`, `note`, `instrument`, `order`, `row`, `wrap`, `turn` (878, 888, 1029,
1124–1125, 1160–1161, 1185, 1231, 1245, 1406). Every tool but one emits
`"on": []`. Hubbard's subscriptions mirror `orderpos`, `patrow`, `pwdir`,
`wave`, `note` and `ins` of *other voices* into a modulator's private state
(`trackerprog_commando.py:68–83, 236–259`), because the drum and the
arpeggio overrun read the bytes that sit after the pitch table, which are
those cells — and `cell()` states at 235 that "there is no other-voice
form". The `patrow` subscription counts `1 + sounds + field` bytes per row,
which is the packed pattern byte layout the tool's own docstring says nothing
of survives.

This is a memory model — which player variables live at which addresses past
a table — expressed as a subscription network. One expression form, a read of
a named cell of a named voice, would say the same thing directly and let the
whole mechanism go. The hermetic suite does not reach the pickle half at all
(219–231 uncovered).

### 2.3 Rules in the player that are one family's precedence

- **The `op` stand-down** (122, 706–709, 791): a stream step that produces
  stands every armed accumulator ranked after it down for the tick. It fires
  on 686–1,042 of 3,000 ticks on the two GoatTracker 2 builds and 690 on both
  SID Wizard builds, and on no tick of any other family. It is a precedence
  rule stated nowhere in the object; a `when` on the arm reading a row fact
  would be the object's own statement of it.
  **Corrected by R5**: the ticks counted here are the ticks the `op` *ran* on,
  not the ticks the rule *changed*. An armed accumulator is stood down on
  GoatTracker 2 alone — 22,485 and 22,260 arms over the two horizons, none on
  SID Wizard, whose producing steps have no armed accumulator ranked after
  them. Removing the rule outright differs on 2,028 of 8,236 and 2,873 of
  8,659 and on 0 of the other twenty-eight builds' 315,463 ticks, so it was one
  family's precedence and not two. It is now a flag the producing row leaves
  and the arm's `when` reads.
- **`stage_sounds`** (967–990): a meta-named voice cell the fetch zeroes on
  every fetch tick and sets to `keys(e)` on a staged one, four families. It is
  the P4 failure the spec itself names — a hook by name — and a `meta.stage`
  `sets` row run over the empty facts (`row_facts(None)` already exists) says
  it in the grammar. **Landed in R5**, exactly so, with one thing this row did
  not see: the cell was written *after* the fetch settled the row's tie, and
  the staging's payload carries `keys` from before it, so the two say different
  things where a row's own command ties (GoatTracker 2). The tie is now settled
  by the `{"hold"}` step that takes that command, and the facts derived from it
  move with it.
- **The prologue** (520–522, 532–539): tick 0 runs `meta.prologue` as a
  command on every voice and spends the tick, two families. It is the tune's
  init routine as a command; SID Wizard's slowdown build emits an *empty* one
  to burn the tick (`trackerprog_sidwizard.py:1257`). **Landed in R5** as
  `state0.prologue`, a `score.commands` entry, run by the same procedure the
  order's own `end` command uses — the `tick_no == 0` test is gone, and the
  line that re-seeded `held` after it was dead.
- **Player-known instrument columns**: `instr().get(...)` reads `prelude`,
  `on_note`, `accs`, `pitch`, `transpose` (1242, SID Wizard only) and `pw`
  (91–93). `adsr`, which §3.5's box puts first, is read by no line of the
  player. Everything else an instrument holds is a family column read through
  `{"ins": "path"}`: Galway's record carries `vadsc vrc pmc pmd0 pmd1 pmdly
  pmg0 pmg1 pinit fmd fmdly fmc g` and three spellings of one waveform byte
  (`wave`, `wave_test`, `wave_gate`); Walker's `m1..m4 notemode delay detune`;
  SID Wizard's `pw_base pw_index flt_base flt_index wave_base hr arpsped
  vibracnt` — two coordinate systems for one table position, and raw bytes
  beside their decoded fields.
- **Player-known cells**: `cell()` (234–256) special-cases `voice_index`,
  `counter`, `phase`, `tied`, `freq_hi`/`freq_lo` and an instrument-scoped
  `pw`; `whole()`/`put()` (1486–1509) know `tick` and `ins.pw`. The last two
  are Hubbard's pulse sweep, seeded from `instruments[*].pw` at 91–93; Galway
  and Walker carry a `pw` column and never read it as a cell. The eight cells
  `__init__` declares (67–76) are the player's state vector, and two of them
  are also the tune's: defMON's row clock counts in `rowsleft` and Follin's in
  `dur`, the cells `sequencer_step` reloads at 1086 — which is the divider
  form §3.6 describes, and also a tune naming a cell it did not declare.
- **Row 0 is no row.** A cursor on row 0 is inactive (`slots` 722,
  `stream_step` 754–756, `point(slot, 0)` stops a stream). Three families pay
  for the sentinel in data: Blackbird's every row and cursor is `+1`
  (`trackerprog_blackbird.py:235, 277–279, 333, 454, 469–470`), Walker's and
  Galway's streams open with a `trap` row 0 ("row 0 is the player's own empty
  cursor", `walker:804`; `galway:769`), and SID Wizard's `pw_base` is 1 for
  every instrument in both tunes. A cursor with an explicit `None`, or an
  `active` bit, is the object's fact; a reserved index is the player's.
  **Corrected by R5: it is five families, not three.** SID Wizard's cost is not
  `pw_base` (R3 struck that with `row_of`'s only readers) but a `no stream` row
  ahead of every instrument's three tables; and defMON, which this row does not
  name, both reserved a `no cascade runs here` row and spelled the delay byte's
  terminator as a step *to* it. GoatTracker 2 pays nothing: its tables are the
  source's own 1-based ones and the byte that names no table now points at
  null. The cursor's own value is that null, and so are a `next` and a `jump`
  of null.
- **`sets` run at hold expiry.** `stream_step` applies a row's `sets` when its
  hold runs out (777–782), so a row that acts *then* holds is two object rows.
  defMON's sidTAB programs are 178 act rows plus 158 hold-only rows plus 21
  jumps (`trackerprog_defmon.py:337–355`); JCH's column programs are split the
  same way (`jch:496–528`). The spec records this as a data form
  (#311, "a stream that acts and then holds is two rows"); it is the player's
  epoch convention doubling the object.

---

## 3. The object carries the player

### 3.1 The observable, in the object

| form | families | reader |
| --- | --- | --- |
| `globals.init_writes`: a literal `(register, value)` list | Hubbard, Follin, Blackbird, Walker, Galway | none in the player; `printer.py:211` |
| `globals.stop_writes` | Hubbard (non-empty), eight families `[]` | 518 |
| `globals.mode_vol` | Hubbard | none; the same byte is in both write lists |
| `meta.tempo.swing` | Blackbird, a prose string | none |
| `meta.player` | all nine, one string | none — a version nothing checks |
| `meta.voice_order` `[2, 1, 0]` | Hubbard, SID Wizard, JCH, Blackbird, Galway | 61, 524 |
| `shadow.registers` as a write order | GoatTracker 2, defMON, JCH | 111–114, 514 |
| JCH's `writeout` stream | JCH | a per-tick SID write list in the routine's order, `reg.22`/`reg.24` sent from every voice (`trackerprog_jch.py:904–921`) |
| Walker's `noise` stream | Walker | `$D41B` taps recorded from the oracle run, under a column named `word` (`trackerprog_walker.py:132–136, 692`) |

`init_writes` is the observable of the init call, carried where §6 says the
observable never goes, and dead. `voice_order` is subtler: §2 drops voice
order from the comparison, but the render depends on it through every shared
cell — `#globals`, `reg.N` last-writer, `cmd.all` — so it is a datum the
certificate's `dropped` list says does not exist.

### 3.2 Register numbers

The object speaks in SID register numbers in five spellings: a bare int as a
`sets` target (GoatTracker 2), `reg.N` (JCH, Follin, Walker), the first column
of `globals.commit` (eight families), `shadow.registers` (three), and the two
write lists. `REG` names seven per-voice registers and the global ones have no
name at all; `commit_order` uses the names. One naming, and the int target
folds into `globals.commit`.

### 3.3 Bytes, masks and thresholds from the assembly

Found by the tool reviews, each with its line; the pattern is one:

- **The 6502 carry as data.** SID Wizard sets `!C` on every wave row and in
  `pw_out` and consumes it in `pitch_out` (`sidwizard:538–574, 1055, 1093`),
  keeping a `raw` chord column only to recompute it; Hubbard's `flags.C`
  default is the constant-folded residue of an index shift
  (`commando:298–300`). §5 admits `carry_out`/`borrow_out` as a machine fact;
  a carry that is a constant on every path is not a fact.
- **Selector magic.** SID Wizard's `slidevib ∈ {$81, $82, $83, $FF, $30, $20}`
  as arm guards and command values (`sidwizard:919–968, 1297–1308`); Galway's
  commands named `cell:HEX` — a `DMoke` operand pair per command (`galway:560–576`);
  JCH's unreached commands named by their raw bytes (`jch:606`).
- **CMP immediates as guards.** Hubbard `dur >= 6`, `dur >= 3`, `rowsleft >=
  $80` (`commando:318, 424, 503`); GoatTracker 2 `tempo < 2`, `rowclock >=
  $80` (`goattracker:621–633`); SID Wizard's `$6B`/`$CB` exp-table compares
  (`sidwizard:712–720`); JCH's `(vol & $0F) in (0, 8)` (`jch:1134`); Walker's
  `payload.gate != $FE` — a compare against the player's own `GATE[1]`
  (`walker:546, 596`). §3.6 names "tokenizer thresholds = the `CMP`
  immediates" as the idiom the layer spends; it is spent in the note column
  and nowhere else.
- **Cell names that are the routine's variables.** SID Wizard `spdcnt arpscnt
  slidevib videlcnt vibfrequ vibracnt tmpptr tmppos …` (`sidwizard:27–53`),
  JCH `pend_* cmd_* gatemask wavecur wavetimer …` (`jch:394–425`), GoatTracker 2
  `rowclock tempo param vibtime instr gate` seeded from `L[k] + 7*v`
  (`goattracker:118–129, 524`), Walker's 43 cells × 3 that are the uncleared
  `$AD00–$AD76` engine block with its countdown residues. The name is
  provenance and harmless; the *set* is the player's state vector, which is
  what §4 says leaves no residue.
- **Engine constants.** Walker's `RESET = $64` at fourteen sites
  (`walker:66`); Galway's `testpulse: [1, 0, 1]`, a per-voice constant encoding
  one unrolled copy's mis-addressed store (`galway:999`); JCH's `["ctrl", 9]`
  literal (`jch:1113`); SID Wizard's `cycles_per_tick` typed as 19656
  (`sidwizard:1214`); Follin's bounds as byte-split compares lifted from
  `$62FE/$631A/$6810/$682A`, including a bug-compatible `_cut_high`
  (`follin:668–688`).
- **Opcode bytes as data.** defMON's `flt_shift` is the opcode byte `$0A`/`$EA`
  that decides whether a filter row doubles, `flt_dir` the `$E9` of an `SBC`
  (`defmon:677–695, 845`); its `@ctrl`/`@ctrl_eor` cell pair with
  `ctrl = xor(ctrl, ctrl_eor)` on 75 rows carries the write-out's `EOR #`
  (`defmon:373–380`); its `voice_bit` stream is `1 << voice_index` as a
  table of immediates, beside a `voice_no` cell asserted equal to
  `[0, 1, 2]` where `{"cell": "voice_index"}` exists (`defmon:592–597, 862`).
- **Commands named by their dispatch byte**, which §3.6 forbids in so many
  words: defMON `cascade.a:XX` (`defmon:570`), Blackbird `fx%d` (`blackbird:178,
  454`), Galway `cell:HEX` (`galway:560–567`), JCH `unreached:%02X%02X`
  (`jch:606`).

### 3.4 The engine, if-converted

`all: True` streams and the row lists of `on_note`, `gate` and commands are
guarded assignment lists, and the tool reviews traced them to source
addresses one for one: Walker's ~83 guarded rows / ~318 assignments are
`p_A60C..A718`, `p_A0A4`, `p_A7B1`, `$A109`; Galway's `note_on` (11 rows, 42
`sets`) *re-spells the note-on copy loop the tool also simulates* at
`galway:279–296`, so the copy exists twice; JCH's `writeout` is the write-out
routine; Follin's eight streams are all `all: True` — 60 rows, 95 `sets`, 113
guards, plus twelve control-flow flags (`blipped`, `retune`, `gsilent`, …) —
and are anatomy §3.6's pseudocode as guarded assignments, with `accs` empty;
its `$85` lists are 80 commands of literal `reg.N` writes, 21 % of the object
(`follin:333–337`). Backlog D2 says the sound half "does not want a small total language"
and D3 says the guarded-`sets` stream *is* that language, already in the
grammar. Both are true, and the consequence is the one to state: for the
families that use it, the trackerprog is the tuneprog's tick with names
substituted, run by a slower interpreter. It is what makes Walker the second
slowest family to render (§5) and the `on_note` copies 75 % of Walker's
instrument bytes and 51 % of Galway's.

### 3.5 Second spellings

Per the backlog's own §3.1 check, each of these is one fact written twice:

- `pitch_links` and `Cmd.links` (GoatTracker 2); `rest_arm` and `state0.held`
  both arming `ARMS[0]` (`goattracker:646, 688–692`).
- `state0.ins` and `state0.cells.ins` (GoatTracker 2, SID Wizard); `state0.shadow`
  and `meta.shadow.registers` (the image's registers, said twice).
- JCH: AD/SR three ways (an edge write, an `@ad` cell, and `writeout` sending
  the cells every tick, `jch:932–935, 1109–1115`); `#res`/`#mode_vol` set and
  `reg.23`/`reg.24` written in the same rows while `writeout` sends `reg.24`
  again; `wavetimer` and `wavespeed` from one nibble; `!raw` duplicating the
  wave row's `relative`; `note_count` and `prelude.early`, read by nothing.
- SID Wizard: `vibdelay arpsped chord wave gate_off` on the record, read by
  nothing because `on_note` bakes the same bytes; `vibracnt` precomputed *and*
  its raw inputs kept and re-masked at run time (`sidwizard:648, 962–964`);
  `detune` read back by `tabcell` on the same row — a build-time constant as
  a self-lookup (`sidwizard:554, 562`).
- Hubbard: `mode_vol` thrice; `phase` restating `delta.repeat[1]`; `produce`
  restating `target` + `width` on every accumulator; `meta.row` setting
  `@wave` from the instrument only so a stream can read `cell wave`.
- defMON: `casa`/`casb` are one row list serialised twice, 27 KB × 2 = 21.8 %
  of *Automatas*' object (`defmon:590–591`).
- Blackbird: 3,106 of 6,255 events (49.7 %) are held no-op rows; `pwprepare`
  is 256 rows of `{"byte": const}` read through `tabcell` (`blackbird:414–417`);
  `ins.restart` and the presence of `ins.prelude` state one fact.
- Follin: `beyond.words` (159 raw words of memory past the note table) copied
  into two streams; `@freqsh` mirrors the player's `freq` cell so a `pitch`
  set can read it (`follin:459, 489, 556, 721`); `{"transpose": 0}` stands in
  for `notefreq` because `notefreq` raises past the tuning and `transpose`
  does not (`follin:556, 721`; 286 vs 321) — two forms for one read;
  `pw_turn0`/`cutoff_turn0` declared and never set; `note_count` read by
  nothing (also defMON, JCH).
- Every family: `rate: 1` on every accumulator (the default), `flags: {}`,
  `stop_writes: []`, `target`/`scope`/`witness` on every record (annotations,
  §5 says so; but two of them are written by nine tools and read by one
  printer function).

### 3.6 What the tools simulate

A tool that must run the tune to state the object is stating something the
schema cannot: Galway's `Sequencer` runs the three byte-code interpreters over
the full horizon to intern 62 instrument records and to find block boundaries
by a `ret` fixpoint (`galway:179–496`); Hubbard's `reached()` walks the score
to find `(ins, note)` overrun pairs and then erases those notes from events
(`commando:149–159, 455–458`); Walker pre-runs the modulators' reset loop at
build time into `preload/phase0/dir0/halt` in every instrument, every filter
command and `state0` (`walker:198–224`), and decides ties by simulating
`reload = notemode - 1` (`walker:276–299`); SID Wizard runs `init` to relocate
operands and simulates the slowdown envelope remap through three tables
(`sidwizard:252–262, 656–669`); JCH models the 6510 port to run init, walks
the score to size the pitch table, and truncates the order at the
certificate's horizon (`jch:288–305, 690–785`); Blackbird drives the VM over
the whole 10,426-tick horizon to read decoded tokens off the tune's own cells,
and re-implements both stream steppers to mark 47 `trap` rows and bound the
tuning (`blackbird:124–153, 478–520`); Follin parses its byte stream as a
context-sensitive program with a 64-pass call-summary fixpoint
(`follin:153–299`); defMON materialises the score per arranger step cut at
`ticks / rate`, a horizon-derived length (`defmon:476–566`). B6 asks
whether the *schedule* is recoverable from the tick; the tools say the
*instruments and scores* are recovered by running the tune, which is the
harder half of the same question and is not on the backlog.

Two latent defects the reviews found in unexercised paths: SID Wizard's
`stream.wave/pulse/filter` commands compute a stream row as `base + index +
3·v (+ $10)` — a byte offset, where rows are keyed by `row_of` (`sidwizard:929–933,
530–532`); neither tune emits them and the arithmetic is wrong. Walker's
`main` calls `render(obj, None)` when `--ticks` is absent and `--certify` is
not given (`walker:1190`). Both are the backlog's own rule — a value no
exemplar writes is untested — at work in the tools.

---

## 4. Dead and duplicated mechanism

| what | where | evidence |
| --- | --- | --- |
| two target dispatchers | `putcode` 833–868 (compiled), `assign` 880–910 (walked) | `assign` is the path of `row_step` `sets` (1220), clock resets (678) and `take` (811): 29,317 calls per 3,000 ticks on *Knob at Night*, 5,674 on *Do It Again*. A scratch subclass routing the clock and `row_step` through `put_to`/`setcode` rendered write-for-write identical lists on nine builds × 4,000 ticks |
| a compiled row clock nobody calls | `clockplan` built at 174–179; `clock()` 654–680 re-walks the record | grep: one write, no read |
| three dividers for one word | tempo `tick_no % rate == phase` (668); stream `rate` decrement-and-fire-on-negative (757–763); accumulator `rate` counting `k−1..−1` in `self.divider` seeded from `state0.dividers` (88–90, 1348–1354) | §3.3: "`rate` — one meaning everywhere". One meaning, three procedures, three phase conventions; the clock's counter form is the general one and P7 already said so |
| two act procedures | `rows()` one act per matching row (960); `inline()` one act per list (1266) | B9, confirmed; plus `edges()` collapses a register written twice in one act to the last value (932–934) — stated nowhere in §2 or §3.1, and exercised by no build (0 collapsed writes over 13 builds × 3,000 ticks) |
| two ends of the play list | `next_event` 1020–1030 vs `next_row`/`order_end` 1096–1125 | B3's leftover; `next_event` raises `IndexError` on an empty pattern |
| two global-channel steppers | `channel()` 541–550 and `channel_after()` 552–564, byte-identical bodies | B9 |
| a dead edge branch | 928–931, "a producer inside the list" | 0 two-tuples in any edge list over 13 builds × 3,000 ticks |
| a dead voice cell the player declares | `lastnote` (75, written 807) | read nowhere |
| memos keyed by `id()` | `code`, `tests`, `plans`, `armwhen`, `rowplans[id(rows)]` plus a `kept` list to pin the objects (351–362, 499–505, 1267–1273, 1333–1339) | a compile of the object top-down, once, needs no identity keys and no pin list; `__getstate__` exists to throw this away and rebuild it |
| `guards(None)` is vacuously true | 461–464 | the backlog's own row: `row_consumes_tick: false` reached it and meant *always* |

---

## 5. Efficiency

Render speed on this machine, 6,000 ticks per build, one process:

| build | ticks/s |
| --- | --- |
| defMON *Jazzpjazz* | 29,800 |
| Hubbard song 1 | 14,200 |
| Blackbird | 12,100 |
| GoatTracker 2 *Linus* | 9,200 |
| Follin song 0 | 8,600 |
| Walker | 5,000 |
| Galway song 1 | 4,700 |
| SID Wizard *End of the World* | 4,300 |
| JCH *Knob at Night* | 3,700 |

Where it goes (`cProfile`, `tottime`, 3,000 ticks of *End of the World*, 2.8 s
total): guard lists of three or more terms as `all(genexpr)` 0.27 s (312k
generator frames), `step` 0.23, `machine` 0.17, `dict.get` 0.17 (631k calls),
the binop closure 0.13, `slots` 0.11, `stream_step` 0.10, `rows` 0.10. On
Walker, `rows()` runs 39k times per 2,000 ticks and evaluates 125k guard lists
of three or more terms: the if-converted engine of §3.4, every row's guard
re-evaluated per tick.

The structural causes, none of which is the "generate source and `exec`"
step §7 P8 declines:

1. **The compile is half done.** `assign`, `clock`, `take` and
   `hold_command.all` walk the object per call. The scratch prototype of the
   first two alone: +2 % to +15 % per build at identical output.
2. **The machine order is static and rebuilt per voice-tick.** `slots()`
   re-evaluates every ranked stream's `when` and `machine()` concatenates
   streams, instrument accs and armed accs and sorts them, on every voice of
   every tick (691–724). The ranks are fixed per object and per instrument;
   the only per-tick input is which arms are live and which cursors sit on
   row 0. A merge of three pre-sorted lists, or a per-instrument memo, removes
   the sort and most of the guard evaluations.
3. **Guard predicates.** `guardcode` special-cases one and two terms and
   falls back to a generator for three or more (461–472); a chained closure
   or a compiled `and` tree costs no frames.
4. **`dict.get` on every read.** `ev()`/`code_of()` look up `id(e)` per
   evaluation (346–349, 437–439); a top-down compile binds children directly.

A fair estimate of the headroom from 1–4 is 1.5–2×, on the same procedure,
with the acceptance P8 used: identical write lists over thirty builds. The
"5×" P8 aimed at is not available without the if-converted streams becoming
something other than a list of predicates — which is §3.4's point, not a
player's.

**Corrected by R7**, which did 1–4 and measured them: **1.40×** over the nine
families' first builds (7,699 → 10,811 ticks/s, 6,000 ticks each, best of
three), 1.14× on Follin and 1.58× on GoatTracker 2 — the low half of the band
and not above it. Two of the four causes are misstated above. **2** is right
that the order is static and wrong that a memo removes *most of the guard
evaluations* with the sort: `slots()` asks every ranked stream's `when` and
every cursor's row *before any slot runs*, and the rank order the object gives
depends on its doing so, so only the sort and the per-arm rank lookup go. **3**
is right that guards are the largest entry and wrong about why — a chained
closure costs a frame per pair, exactly as the tree does. What pays is that a
guard term almost always states one operand outright (501 of SID Wizard's 505,
407 of 407 of JCH's, 151 of 153 of Galway's), so the constant folds *into* the
comparison and the term costs one read. **1** and **4** are right as written,
and **4** understates: the payload an arm or a command fixes is compile-time
data too, and GoatTracker 2 re-evaluated 15,243 payload-bound `const` nodes per
3,000 ticks for want of spending it.

---

## 6. The spec against the code

Beyond B5's eight rows, which are closed:

| §, claim | what the code does |
| --- | --- |
| §1 "eight keys, no per-family construct" | §2.1's eighteen mechanisms; `meta.pitch_links`, `pitch_target`, `rest_arm`, `stop`, `tempo.swing`, `stage: hold`, `stage: commands` each have one writer |
| §2 rule 1 "every write kept" | `edges()` keeps at most one write per register per *act* (932–934); the act is a deduplication unit the spec does not name, and Blackbird's prelude needs three acts for five writes for exactly this reason (§3.5's own table) |
| §2 "order between voices dropped" | `meta.voice_order` changes the render through shared cells (§3.1); the certificate says the boundary is dropped and the object carries it |
| §3.5 `Ins = {adsr, prelude, on_note, accs}` | the player reads `prelude`, `on_note`, `accs`, `pitch`, `transpose`, `pw`; never `adsr`. An instrument is a family record plus six names |
| §3.3 `Step.point`, `Step.op`, `Step.run` | `point` is read by `inline` only (B5); `op` and `run` by `stream_step` only; `on_note` rows cannot `op` and a stream row cannot `point` |
| §4's `tick()` pseudocode | omits the flush (512–514), the prologue (520–522), the channel before the voices (523), the stop (515–519), `channel_after` and `channel_commit`. A reader holding the code against §4 holds it against a sketch |
| §5 "eighteen fields the player reads" | true; and eleven of the twenty-one are written by one family (census: `beyond`, `emit`, `trap`, `flag.seed`, `flag.unguarded`, `amplitude.count`, `amplitude.fold`, `amplitude.turn`, `note`, `step_when` ×2, `take`) |
| §9.1 "the sound half doubles the total, 95 % of *Knob at Night*" | *Knob at Night*'s 790 KB object is 685 KB of `wrapdata`: the rip wrapper's own 4-byte-per-frame table, 8,577 rows, read through `dptr` (`jch:1182–1211`). That is tune data and not the sound vocabulary; the sentence attributes it to instruments, streams and accumulators |
| §7 P8 "the object is compiled on first reading and called thereafter" | §4 above: four walked paths and a per-voice-tick sort remain |
| §3.6 "the row is a program, and one procedure runs it" | `row_step`'s `sets` arm goes through `assign` and a stream's through `putcode`; one grammar, two dispatchers |

---

## 7. What to do, in order

Each row's acceptance is the poison harness's: `tools/trackerprog_poison.py
--builds all --emit-digests DIR` at the merge base, `--against DIR` after, 0
differing of 332,358 unless the row says otherwise.

| # | item | mechanism | size |
| --- | --- | --- | --- |
| R1 | one target dispatch | delete `assign`; route `row_step` `sets`, the clock's resets, `take` and `hold_command.all` through `put_to`/`setcode`; use `clockplan` or delete it. Measured identical on nine builds already. **Landed #335**: `assign` deleted, all four on the compiled path, `meta.row` and `meta.stage` compiled to a predicate and a setter list per step (`rowcode`), `clock()` reading the `clockplan`. 0 differing of 332,358 over the thirty builds; 6,000 ticks in one process, *Je suis Linus* 9,429 → 10,150 ticks/s and *End of the World* 4,371 → 4,515 | small |
| R2 | one act, one divider, one end | B9's rows plus the third: `stream.rate` and the accumulator `rate` become the clock's counter form (a cell, a step, a boundary, a reset), or the accumulator's `rate` stays and the stream's goes. State the act's last-write-wins rule in §2 or make an act keep every write and measure. **Landed #336**: `rows()` and `inline()` are one procedure over one compiled plan reading `sets` and `point` in both, and **which act rule survives was measured, not chosen** — each rendered over the whole horizon of every build against the merge base's digests: *the row is the act* at **0 differing of 332,358 on all thirty**, *the list is the act* at **2,943**, differing on seven (walker-chameleon 1; galway-song1 994, song2 931, song3 928, song4 28, song5 11, song6 50) and 0 on the other twenty-three. So the act is the row's datum and not the call site's, and §2 rule 1 and §3.1 now say so with the limit this review found unstated and unexercised — `edges()` keeps one write per register per act — under a hermetic snippet that writes one twice in one act. `channel()`/`channel_after()` are one procedure over two lists. `order_end()` is the one answer to *the play list ended* for the fetch and the walk alike, returning whether the list goes on, with hermetic snippets for the empty pattern `next_event` used to raise `IndexError` on and for a bare `end: "stop"` reached through the fetch. The stream divider and the accumulator divider are one compiled procedure (`dividercode`) over one form, `{cell, reload}`: Hubbard's pulse bounce counts in the engine's own `pwdelay` cell seeded through `state0.cells`, so **the migration did not diverge** and `state0.dividers` and `Player.divider` are gone, a bare `k` naming no counter being refused. `meta.tempo.rate` stays the clock's and is not the same question — with `phase` it selects which ticks the tune's one clock steps on, once per tune, against a divider that is per voice and per run — so this row leaves two, not one. Measured: 0 differing of 332,358 over the thirty builds, every object rebuilt from the edited tool; 6,000 ticks in one process, *Je suis Linus* 9,734 → 9,939 ticks/s, *End of the World* 4,231 → 4,426, *Chameleon* 5,183 → 5,226, flat inside this machine's ±2 % | medium |
| R3 | delete the dead | `lastnote`, the 2-tuple branch, `init_writes`, `mode_vol`, `meta.player`, `tempo.swing` → a `note`, the two `rest_arm`/`pitch_links` spellings, the SID Wizard and JCH record fields §3.5 lists. Each at 0 differing; `init_writes` needs no render, nothing reads it. **Landed #335**, in part: `meta.player`, `init_writes`, `mode_vol`, the 2-tuple branch, `tempo.swing` → a `note`, and — with R10's refusal — SID Wizard's `wave_base`/`pw_base`/`flt_base`, which lose their only reader. 0 differing of 332,358 with every object rebuilt. **`lastnote` is not dead and stays**: `goattracker:473` reads it as `{"interval": {"cell": "lastnote"}}` on the speed table's calculated arm — 9 of 19 speed rows of *Je suis Linus*, 6 of 19 of *Do It Again* — and `take` is its only writer, so dropping the write diverges on 4,466 of 8,236 and 4,284 of 8,659 ticks. The row above confused an unread *declaration* with an unread *cell*. The `rest_arm`/`pitch_links` spellings and the JCH record fields are not in this package | small |
| R4 | one other-voice cell read, and no subscriptions | add `{"cell": [name, voice]}` (or a `voice_index`-bound `cell`), rewrite Hubbard's `beyond` words and drum pitches over it, delete `priv`/`subs`/`heard`/`owners`/`publish` and the nine sites. Acceptance: Commando ×3 at 0 differing and 219–231 gone. **Landed #337**: `{"cell": [name, voice]}` beside `{"cell": name}` — one name, one space, one half, read on the voice the word names — and the twelve `beyond` words and the two drums' `value`/`octave` are expressions over `orderpos`, `patrow`, `rowsleft`, `wave`, `note`, `ins` and `pwdir` of the voices they name. `rowsleft` had been a trap only because no event published it, so word 5 is live and the two traps left are the packed row byte the score no longer keeps. **The mirror was measured against the live cell before anything was struck**: over song 1's whole horizon the two are equal at all **2,676** reads the three modulators make. The one subscription that *counted* rather than mirrored — the tune's byte cursor into a pattern, `1 + sounds + field` per row — is two steps of `meta.row` over a cell of its own, `@patrow += 1 + sounds + field` and `@patrow := 0 when wraps != 0`, `wraps` being a fact of the row beside `sounds` and `field`; **which reset survives was measured too**, the alternative (reset at the pattern's first row, needing no new fact) differing on **48 of song 1's 576 `patrow` reads**, so the reset is the cursor's. `priv`, `subs`, `heard`, `owners`, `publish`, `private`, `sounded`, the `own` form, the nine sites and the printer's private-state rendering are gone, and `beyond`/`pitch` lose `state`/`on` in all four tools that wrote them. **`__getstate__`/`__setstate__` stay, minus the private half, and the row above was wrong about them**: a `Player` does not pickle plainly — its compiled form is closures (`Can't pickle local object 'Player.build.<locals>.<lambda>'`) — and `trackerprog_jch.py` and `trackerprog_defmon.py` pickle one to resume a chunked certification under a CPU budget, so `_DERIVED` stays and the pair now drops the derived form and recompiles, under the hermetic snippet §2.2 said the suite never reached. Measured: **0 differing of 332,358** over all thirty builds against `main`'s digests, with every object and every render rebuilt on both sides; 6,000 ticks in one process, *Commando song 1* **15,235 → 16,056** ticks/s and *Je suis Linus* **9,411 → 10,042**; `universal.py` 1,522 → 1,480, hermetic coverage 95 % | medium |
| R5 | hooks into the grammar | `stage_sounds` → a `meta.stage` row over the empty facts; the `op` stand-down → a `when` on the arm; `stopping`/`stop_writes` → the order's `stop` plus a command the end runs (`sets` on `reg.N`/`#` and `globals.commit` already reach every register it writes); `prologue` → the same command at `state0`. Each at 0 differing on its families. **Landed #338**, five hooks, each measured against the merge base's digests over whole horizons: `stage_sounds` → a `{"sets": [["@cell", {"payload": "keys"}]]}` row of `meta.stage`, with the fetch that stages no row running the same program over `row_facts(None)` and the two unguarded stagings taking `dur != 0`, the term `row_ends_fetch` already spells — **0 of 60,848** on the four families' seven builds. The `op` stand-down → `!produced`, a flag the producing row's own `sets` leaves and the arm's `when` reads: the rule simply removed differs on **2,028 of 8,236** and **2,873 of 8,659** (GoatTracker 2) and **0 of 315,463** on the other twenty-eight, which is the evidence it was one family's — §2.3 above is corrected, SID Wizard's producing steps stand nothing down. The tune stop → the order's own: an `end` that is not a `jump` stops that voice, and `end: {"stop": name}` names the command the tune runs on a tick of its own; Hubbard's four writes are that command's `sets` on `reg.N`, and the abandoned tick needed no abort because `row_consumes_tick` already spends it — **0 of 292,914** over the twenty-six builds whose lists can run out, subtune 3's stop at 384 and 11,395 silent ticks after it included. The prologue → `state0.prologue`, run by the same procedure — 0 of 39,444. Row 0 → a cursor is `null` where it runs nothing, a `next` or `jump` of null stops a stream, and the padding goes from **five** families, not three. `stopping`, `stop_writes`, `Player.op`, `stage_sounds`, `meta.prologue`, the abort path in `voice()`/`tick()` and the vacuous `guards(None)` for `row_consumes_tick: false` are all gone. **0 differing of 332,358** over all thirty builds, every object rebuilt from all nine tools; 6,000 ticks in one process, *Je suis Linus* **9,930 → 9,340** ticks/s (the arm is now reached and its guard evaluated where the player skipped it: 24,684 `step()` calls per 3,000 ticks against 16,397), *End of the World* **4,480 → 4,810** and *Quintessence* **13,370 → 13,100**; `universal.py` 1,480 → 1,477, hermetic coverage 96 % | medium |
| R6 | one register naming | name the global registers beside `REG`; `reg.N`, the int target, `globals.commit`'s column and `shadow.registers` take the name; `sid_base` becomes a refusal or a marked `bug` form with its family. **Landed #339**: the four global registers take `tuneprog/grid.py`'s own names (`cutoff_lo cutoff_hi res_route mode_vol`) beside `REG`'s seven, and a register named outright — a global name, or a voice's own as `v1.pw_lo` — is one spelling for a command's `sets`, a voice's write-out, `globals.commit`'s first column and `meta.shadow.registers`, the guarded flush entry keeping its `[name, guards]` shape and JCH's overrides naming what their operands resolve to. The bare int target and `reg.N` are gone from the grammar; `universal.chipreg` is the only place a name becomes a number, `putcode` spending it at compile time for a register named outright and `emit` reading a per-voice table `chipreg` fills. `sid_base` is gone with them, each family measured: SID Wizard 1.6's is `{"bug": "voice_base"}`, a marked defect no reader can take for a musical value, and Hubbard's word is the voice cell `voicebase` — the §2.1 row above is corrected with its reader. Measured: **0 differing of 332,358** over all thirty builds against the merge base's digests, every object rebuilt from all nine tools and every render recomputed; 6,000 ticks in one process, best of three, *Je suis Linus* **9,120 → 9,306** ticks/s, *Knob at Night* **4,042 → 4,145**, *Ghouls song 0* **10,818 → 10,853**; `universal.py` stays at 1,477 lines, `printer.py` 626 → 625, hermetic coverage 96 %. A hermetic test walks all thirty cached objects and asserts no number and no `reg.N` reaches a `sets` target, `globals.commit` or `shadow.registers` | small |
| R7 | the compile, finished | §5's 1–4: static machine order, chained guards, direct child binding, no `id()` memos. Acceptance as P8's: identical write lists, thirty builds; report ticks/s per family. **Landed #340**: §5's expression forms, guard lists and cells are `trackerprog/compiler.py` (327 lines, a mixin `Player` is), and the object is compiled top down from `compile()` — a node bound to its children's closures, an accumulator bound to **its arm**, a row program to its stream's rows, a command to its own — so an arm's or a command's own numbers are spent at compile time rather than read per step: GoatTracker 2 evaluated **15,243** payload-bound `const` nodes per 3,000 ticks and now evaluates none. The rank order is memoised on `(voice, instrument, id(armed))` and merged and sorted once for that key; `slots()`, the per-voice-tick sort and the per-arm rank lookup are gone, and **the two per-tick facts are still asked of every slot before any slot runs** — which is what `slots()` did, and what §5's item 2 got wrong in saying the sort takes *most of the guard evaluations* with it. Item 3 is wrong in its reason too: a chained closure costs a frame per pair, and what pays is that **501 of SID Wizard's 505 guard terms, 407 of 407 of JCH's, 79 of 79 of GoatTracker 2's and 151 of 153 of Galway's state one operand outright**, so the constant folds into the comparison and a term costs one read, three terms to a frame and a chain past that. The `kept` pin list and the `code`/`tests`/`plans`/`armwhen`/`rowplans` memos are gone; three memos remain, each holding the record it is keyed on so its identity cannot be reused — an arm, an inline command a row carries, and the machine order — and each carries the one line that says so. Measured: **0 differing of 332,358** over all thirty builds against the merge base's digests; 6,000 ticks in one process, best of three, over the nine families' first builds — Hubbard 15,695 → **23,811** ticks/s, GoatTracker 2 9,059 → **14,282**, SID Wizard 4,347 → **6,541**, defMON 25,671 → **33,125**, JCH 5,014 → **7,149**, Follin 10,623 → **12,142**, Blackbird 12,755 → **17,003**, Walker 5,926 → **8,639**, Galway 5,399 → **7,112**, which is **7,699 → 10,811 ticks/s over the nine, 1.40×** and the low half of §5's band. `universal.py` 1,477 → 1,399, `compiler.py` 327, hermetic coverage 97 % and 100 %, and a stored `Player` still drops the derived form and recompiles | medium |
| R8 | the instrument schema | either the player reads `adsr` (an instrument's note-on emits it, and the tools stop spelling it in `on_note`) or §3.5 says what is true: an instrument is a family record and six player names. The 62-copy `ENGINE` list and the 11-copy `on_note` are the same question — a record the tools inline per instrument wants a reference | medium |
| R9 | the spec | §4's pseudocode completed; §2's act rule and `voice_order` boundary stated; §9.1's *Knob at Night* row attributed to `wrapdata`; §2.1's table carried into B8 with its readers, so the B8 census is the code's and not the schema's | small |
| R10 | the tools' unexercised arms | `sidwizard:929–933`'s row arithmetic and `walker:1190` fixed or refused; a hermetic test per tool command that no tune emits, or the command deleted under the backlog's own rule. **Landed #335**: `walker:1190` renders the certificate's horizon; the three SID Wizard commands are a named refusal (`DEAD["fx.pointer"]`) — their parameter counts rows of the instrument's own *record* and `row_of` is that instrument's map, which no command names — exercised by `tests/trackerprog/test_tool_refusals.py` | small |

What this review does not reopen: the thesis (thirty builds, one module, no
family branch), the certificate, or D1–D6. It says the module is one
procedure the way a family of nine procedures with shared helpers is one, and
lists what would make it one the way §4 describes.
