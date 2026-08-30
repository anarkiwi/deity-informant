# Prototype: SID Wizard as a trackerprog — the third family, one player

A **hand transliteration** of the two certified SID Wizard tuneprogs
([prototype-sidwizard.md](prototype-sidwizard.md), anatomy
[§3.4](playroutine-anatomy.md)) into trackerprogs
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)) and
GoatTracker 2 ([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)),
and certified against each tune's own player on the PcodeVM.

Three results:

1. **Both builds render, on one object shape and one code path.** 8,084 and
   14,465 ticks, **0 divergences** on §2's observable — and *stronger*: the write
   lists are **identical**, tick for tick, value for value, register for
   register. GoatTracker 2 got that free from a ghost flush; SID Wizard has no
   flush, so it is earned instead — by saying that a voice's edge writes are the
   tick's own **acts** in order, which is §2 rule 1 restated as data (§4.5).
2. **The inherited loop claim re-verifies on the render.** Rendering past the
   first repeat, the next period is the previous period write for write: 6,120
   ticks for *Emomyst*, 7,688 for *End of the World*.
3. **The layer invariant holds at three families.** Hubbard, GoatTracker 2 and
   SID Wizard lift to the same `Player`, with no branch on `meta.family`.
   Everything SID Wizard needs beyond the first two is a **form** (§4) — a
   counter clock whose phases are guards, a prelude that belongs to the row's
   instrument, a divider kept in a cell, a step's epoch, an edge written twice,
   a global channel committed after the voices, a producer that moves no cell,
   a clamp with an edge — and every form is stated as data the other two simply
   do not carry.

Reproduce:

```
tools/trackerprog_sidwizard.py $HVSC/MUSICIANS/H/Hermit/Emomyst.sid \
    --source out/recert-main/sw-emomyst/certificate.json \
    --certify --out out/sw-tp/sw-emomyst
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 finding the data · 7 measurements ·
8 the four things the family was expected to force.

The forms, in one line each: §4.1 a counter row clock whose phases are guards ·
§4.2 a prelude that belongs to the row's instrument · §4.3 a stream divider kept
in a cell · §4.4 a step's epoch · §4.5 an edge written twice in one tick · §4.6
a global channel committed after the voices · §4.7 a producer that moves no cell
and the flag between two · §4.8 a clamp with an edge. The corrections: §4.9 a
partial shadow is not a shadow · §4.10 what naming a command costs · §4.11 one
musical question, one place that answers it.

---

## 1. The object

`tools/trackerprog_sidwizard.py` writes `trackerprog.json`;
`deity_informant/trackerprog/universal.py` renders it. The same seven sections:

| section | Emomyst (SW 1.6) | End of the World (SW 1.9) |
| --- | --- | --- |
| `meta` | 3 voices in order 2,1,0; `commit_order (ad, sr, ctrl)`; a **counter** row clock of four phases with two guarded resets, the fetch at phase 0 and the row at phase 2; the tick a sequence of **acts**; a prologue that spends the frame the slowdown gate takes | the same, `commit_order (sr, ad, ctrl)`, and no prologue |
| `pitch` | `base 0` and **96** contiguous frequencies — the tune's whole tuning | 96, the same shape |
| `streams` | **14**: `wave` (43 rows), `pulse` (26), `filter` (19, a global cursor), `exp` (107), `chords` (18), `chordstart` (6), `tempo` (8), `voice_bit` (3), plus `hard_restart`, `exit`, `gate_row`, `pitch_row`, `pitch_out`, `pw_out` | 73 / 61 / 31 / 107 / 0 / 2 / 8 / 3, the same fourteen |
| `accs` | **9** declared forms — a vibrato phase and its freq, a delay, a rising modulation, two free slides, a tone portamento, a pulse step, a cutoff step | the same nine |
| `instruments` | **11**, sixteen columns each | **21** |
| `score` | 3 order programs of `play(pattern, transpose)` ending `jump`; 21 patterns, 843 events, **44** distinct row commands | 31 patterns, 1,314 events, **60** commands |
| `globals` | one channel: cutoff as one 11-bit cell, band, resonance, routing, the owner voice, the owner's note, the master volume, the filter shift and eight tempo cells | the same |

The player has one dispatch and it is on the *form* of a delta, a policy or a
stream row — never on the name of an effect. The nine accumulator ids
(`vib_phase`, `vibrato`, `vib_delay`, `freqmod_step`, `slide_up`, `slide_down`,
`toneporta`, `pulse_step`, `cutoff_step`) are labels in the data.

---

## 2. The mapping, line by line

Left column is the certified tuneprog's own text
(`out/recert-main/sw-*/tuneprog.md`) and anatomy §3.4. Right column is the
object.

| the player says | the trackerprog says | §5 row |
| --- | --- | --- |
| `SPDCNT` post-incremented against `TEMPOTBL[TMPPOS]`, `BEQ`/`BVC` | `meta.tempo.form: counter` — a cell the tick steps and two guarded **reset** clauses; the V-flag trick is spent for what it decides | new (§4.1) |
| `TEMPOTBL` entry bit 7 = "loop the tempo program" | the second reset clause: `spdcnt >= tempo & $7F` and back to `tmpptr`; the first is `== tempo` and on to `tmppos + 1` | new (§4.1) |
| ticks `0/1/2` and `else` | `meta.tempo.fetch 0`, `early [phase < 2]`, `boundary 2`; every stream and arm carries its own `when` over `{"cell": "phase"}` | new (§4.1) |
| `READROW`'s 1–4 bytes with bit-7 continuation | `Event{sounds, note, gate, tie, ins, arm, dur}` — the note byte's token class is spent, not re-encoded | §3.6 |
| `$70–$77`, the packed rest | the event's `dur`, in **rows** | §3.6 |
| `$60–$6F` set vibrato amplitude, `$79–$7C` sync/ring | `cmds`, named by what they do | §3.6 |
| `$7D` / `$7E` gate on/off | the event's `gate`, and a `{stream}` step of `meta.row` guarded on `gate_stmt` — one stream that says what a gate statement does (the mask, and the mask re-applied to the waveform) | §3.6 (§4.10) |
| `HARDRST` at ticks 0 and 1 with the tick number as the mask | the instrument's **prelude**: one stream, two rows, each guarded by the clock's phase and the instrument's own control bit | §3.5 |
| 1.6 writes `AD` then `SR`, 1.9 `SR` then `AD` (anatomy:1232) | **`meta.commit_order`, and nothing else** | §3.1 |
| the HR reads `INS[CURIFX or CURINS]`, the tables read `INS[CURINS]` | `meta.stage`'s one row, `@hrins := payload.ins`, and `{"insrec": ["hrins", "hr.0"]}` — the prelude belongs to the row's instrument, the streams to the voice's cursor | new (§4.2) |
| `TICK_2`'s `STRTSND` | the instrument's `note_sets` and `points`, guarded by its own control bits and by whether the row named an instrument (`TABLRST`) | §3.5 |
| `WFARPTB` rows `[wave\|cmd, pitch\|chord, detune]`, `--ARPSCNT` | the `wave` stream, and `rate` — a divider kept in the cell `arpscnt`, which a row and two commands also set | new (§4.3) |
| `SETPWID` / `FILTPRG` rows `[count\|set, step, track]` | the `pulse` and `filter` streams, `epoch: entry` — the counter is read before its own move, so the consuming tick does not sweep | new (§4.4) |
| the 11-bit cutoff, `AND #7` / `LSR×3` / `PHP`/`PLP` | one global cell of `width 11` and `split(3, 8)` at the commit | §5 filter sweep |
| `CKBDTRK` / `PKBDTRK` through `EXPTABH` | `tabcell(exp[c])` on the cutoff target and on the pulse's — §3.7's own row, not a `tablestep` | §5 keyboard tracking |
| `EXPTABH = FREQTBH − 11` | the `exp` table, **materialised as its values**: overlapped storage is an idiom | §3.2 |
| `CHORDS` with `$7F` = loop | the `chords` stream: a signed semitone and the row it goes on to, the loop resolved through `chordstart[curchord]` because no row knows which chord it ends | §5 arpeggio/chord |
| `SLIDEVIB` ∈ `$00/$10/$20/$30/$81/$82/$83` | nine **arms** of six accumulators, each with its guard over one cell | §5 |
| `VIDELCNT` counting down before the vibrato runs | `vib_delay`, ranked *after* the vibrato so both read the value the tick came in with (#297's epochs) | §5 (a counter is a divider) |
| the orderlist `< $80` pattern · `$80–$9F` transpose · `$A0–$AF` volume · `$B0–$EF` tempo · `$FE` stop · `$FF pos` | `play(pattern, transpose, vol?, tempo?)` and `end: jump(k)`; §8 says which columns these tunes leave unexercised | §3.6 |
| `WRPITCH` `sid.freq ← freq + detuner + c` | `pitch_out`, a producer that writes the chip and moves no cell, reading the flag `C` the pulse write left | new (§4.7) |
| `WRWFGHO` `sid.ctrl ← wfghost` | a `{stream}` phase of `meta.tick` | §4.1 |
| `COMMONREGS` after the three voices | `globals.commit`, run **after** the voice loop, with a guard per entry | new (§4.6) |
| `NOTEFXTBL` 8, `SMALLFXTBL` 14, `BIGFXTABLE` 31 words | `score.commands`, **named by what they do** — `portamento:34`, `sustain:06`, `arpeggio.speed:03` — never by the index one of the three tables dispatched them with | §3.6 |
| `INITER`'s 30 relocated operands | nothing: the object is read from the image the tick sees (§6) | — |

What disappears: `PLAYERZP` and its seven re-points; `X = voice offset` and the
stride-7 struct; the 1-based `TEMPOTBL−1,Y`; the `SEC/SBC/BEQ/BVC` tempo trick;
the `PHA`/`PLA` and patched-immediate scratch (`ASTOREZ`, `VALSTOR`, `MERGEST`,
`MUL3TMP`, `STORFRL`, `TABLRST`, `INSCTRL`); the two `BCC`-offset dispatchers and
the `JMP` word table; the bit-7 continuation; the packed rest; and every byte
cursor — `PTNPOS` and `SEQPOS` become the score's own events, `WFTPOS`, `PWTPOS`
and `FLTPOSI` become stream cursors over materialised rows. Not one byte of the
object names a memory location.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`, §2's comparison over the whole
certified horizon, the reference being the tune's own player on
`deity_informant.PcodeVM`.

| tune | ins | patterns | events | tuning | streams | accs | ticks | SID writes | divergences |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Emomyst | 11 | 21 | 843 | 96 | 14 | 9 | 8,084 | 149,118 | **0** |
| End of the World | 21 | 31 | 1,314 | 96 | 14 | 9 | 14,465 | 269,309 | **0** |

**Stronger than §2, and without a shadow.** `identical_ticks` equals the whole
horizon for both tunes: not a permutation, not a multiset — the same list.
`same_per_register_order` is true. GoatTracker 2 got this for free because a
ghost flush emits the whole image in one fixed order; SID Wizard writes the chip
as it goes, so the player has to produce the sequence, which is §4.5. It is not a nicety: without it §2 *itself* fails, on 500 ticks of
Emomyst and 44 of End of the World, because rule 1 keeps every edge write and
collapsing two writes of one register into one is a divergence rather than a
permutation.

**The loop claim, re-verified.** The source certificate claims `complete` with
period 6,120 (7,688), first repeat at call 8,083 (14,464). The trackerprog
re-checks it on its *own* render: rendering `first_repeat + period` ticks, the
period after the first repeat equals the period before it, write for write.

| tune | period | first repeat | replay window | verified |
| --- | --- | --- | --- | --- |
| Emomyst | 6,120 | 8,083 | ticks 1,963..8,082 against 8,083..14,202 | **yes** |
| End of the World | 7,688 | 14,464 | ticks 6,776..14,463 against 14,464..22,151 | **yes** |

The window starts *at* the claimed first repeat rather than one tick after it,
which is GoatTracker 2's off-by-one and is not this family's: there is no flush,
so a tick's writes are the tick's own state and the claim lands where it says.

---

## 4. What the spec needed

Eight forms and three corrections. None is a family branch, none is a new
mechanism, and every form is a datum the first two families do not carry.

### 4.1 A row clock is a divider, a countdown **or a counter**

§3.6 says tempo is "a divider or a tempo stream", and GoatTracker 2 added
`meta.tempo.form: countdown`. SID Wizard's is neither: `SPDCNT` counts **up**
from zero and the row ends where it meets the tempo. So `form: counter`, with

```jsonc
"cell": "spdcnt", "boundary": 2, "fetch": 0,
"early": [[{"cell": "phase"}, "<", 2]],
"reset": [ {"when": [...], "sets": [["@spdcnt", 0], ["@tmppos", ...]]}, ... ]
```

Two things it needed. First, **the reset is guarded assignment, not a reload.**
`SEC; SBC TEMPOTBL−1,Y; BEQ new_row; BVC same_row` is the anatomy's technique 8:
one subtraction distinguishing "the counter met the tempo" from "the tempo entry
had bit 7 set". The V flag is a player idiom, and what it decides is two clauses:
`tempo & $80 == 0 and spdcnt == tempo` advances the tempo program by a row;
`tempo & $80 != 0 and spdcnt >= tempo & $7F` sends it back to `tmpptr`. Both
zero the counter. That is the whole tempo program, and `TEMPOTBL` being *state* —
the score's own `tempo` commands write it — makes it a stream of eight global
cells read with `tabcell`, which is §3.6's tempo over a stream of cells.

Second, **the phases are guards.** `spdcnt` 0, 1, 2 and "anything else" are four
different ticks: the row is read, the position advances, the note starts, the
tables run. The object does not enumerate them; it exposes the clock's own step
as a cell named `phase` and lets each stream, arm and prelude row carry the guard
it needs. The two that the *player* reads are `boundary` (the tick the row
sounds) and `fetch` (the tick it is read), which are §3.5's `early` given a third
job: the fetch runs ahead of the row it stages, and here it runs ahead of the
tick's modulators too, because the row it stages decides what they do — which
is `meta.tick` putting `fetch` and `prelude` ahead of `row` and `machine` (§4.1)
rather than a flag beside the clock.

### 4.2 The prelude belongs to the row's instrument, not the voice's

`HARDRST` reads the hard-restart envelope out of `INS[CURIFX or CURINS]` — the
instrument the row is *about* to play — while the waveform table it falls
through to is still running the instrument the voice *has*. GoatTracker 2 solves
the same problem by staging `ins` at the fetch, which works because its tables
are one global table its cursors index. SID Wizard's tables live inside each
instrument record, so staging `ins` early would move the tables too.

Two data. The fetch stages the row's instrument into a cell of the tune's own
choosing — `{"sets": [["@hrins", {"payload": "ins"}]]}`, "the row's instrument,
or the voice's own where the row names none" — and `{"insrec": [cell, column]}`
reads a column of the instrument a cell names. The prelude's rows then say `["ad", {"insrec": ["hrins", "hr.0"]}]` and
mean it.

### 4.3 A stream's divider may live in a cell the score can set

§3.3 gives a stream `rate: k`, a constant divider. SID Wizard's waveform table
steps once every `ARPSPED & $3F` + 1 ticks, and `ARPSCNT` — the countdown — is a
cell a waveform row sets (`row[0] < $10` is a repeat count), a small effect sets
and a big effect sets. So `rate` becomes

```jsonc
"rate": {"cell": "arpscnt", "reload": {"and": [{"cell": "arpsped"}, 0x3F]}}
```

— decrement, run the row where it goes negative, reload, and let the row's own
`sets` overwrite the reload where it has one. `k` is the degenerate case and no
family that has it changes.

### 4.4 A step's counter has an epoch

§3.3's `hold` is a count the player keeps; SID Wizard's is a cell (`PWEEPCNT`,
`CWEPCNT`) it compares against the row's own byte *before* stepping it. The
difference is exactly one tick's worth of sweeping: the row occupies `n + 1`
ticks and sweeps on `n` of them, where GoatTracker 2's occupies `n` and sweeps on
all of them. That is #297's epoch rule, so the object says which:
`streams.pulse.epoch: "entry"`. Absent, the consuming tick runs the step; present,
it does not.

### 4.5 An edge register written twice in one tick is two writes

§2 rule 1 says every `ctrl`/`AD`/`SR` write is kept, in tick order, unchanged
repeats included. The player was collapsing them: `commit` took the last value
per register and emitted the three in `commit_order`. That is right for a family
whose writes go through a flush, and wrong for one whose do not — SID Wizard's
note-start tick writes `AD` from the instrument and then `AD` again from the
row's own `attack` effect, and those are two events.

The commit says so, and what it says is not "in list order": it is
**the tick's acts in order, each act's own edges in `commit_order`**. An act is
one thing the tick did — a stream row's `sets`, an instrument's note-on, one row
command — and the object already delimits them. That keeps §3.1's datum doing
the work it was written for (the pair a single act writes) while §2 rule 1 gets
the sequence it asks for. It is also what makes the version difference *one*
datum rather than two: the 1.6/1.9 order lives in `commit_order` and the prelude
and note-on rows are written in whatever order, because the commit sorts them.

### 4.6 The global channel commits after the voices

`COMMONREGS` runs when the three voices have. GoatTracker 2's global commit runs
at the head of the tick because a flush defers it anyway; SID Wizard's reads
`FSWITCH`, `CTFHGHO` and the owner's pitch — all of which the voices just moved.
So `channel()` splits: the global channel's *streams* step before the voices, its
*registers* commit after them, and a commit entry may carry a guard (the
keyboard-tracking arm and the arm without it are two entries for one register,
their guards exclusive, and §2 rule 2 makes the last one the tick's value).

### 4.7 A producer that writes the chip without moving a cell, and the flag between two

`WRPITCH` writes `sid.freq = freq + detuner + c` where `freq` is the voice's own
16-bit cell and `c` is whatever carry the tick's last addition left. Two data.

`assign`'s target `pitch` emits the pair without storing, so the cell keeps the
value the waveform table's step took (`meta.pitch_target` says where that step
puts it, `"@freq"` here rather than the chip). And the carry is §5's
`Δ + carry(site, flag)` with the *producer* stated as Commando's §4.11 asks: the
pulse write leaves `C`, each waveform row leaves `C`, `globals.flags.C.default`
is what it is when neither ran, and `pitch_out` reads `{"flag": "C"}`. It is
load-bearing: §7 measures what dropping it costs.

### 4.8 `clamp(target)` has an edge

§5's tone portamento is "snap where the step would reach or cross the target".
SID Wizard's `PORTAME` compares 16-bit and then adds through the compare's own
carry, so the step is one larger than the speed and the test is one tighter: it
snaps where the step would *cross*, and steps `speed + 1` where it would not.
`policy: {"clamp": ..., "edge": 1}` is the boundary as a datum; `edge: 0` is
GoatTracker 2's and is the default.

### 4.9 A partial shadow is not a shadow

The expectation this family carried was that `meta.shadow` would have to say
*which* registers pass through it, because SID Wizard ghosts FREQ/PW/WF and
writes AD/SR and the filter directly (`ALLGHOSTREGS` off, anatomy:1236). Reading
the code says the opposite: **there is no flush.** Nothing in either binary
sweeps `$D400..$D418`; `WRPITCH` and `WRWFGHO` write the chip from the cells on
the tick that computed them. A "ghost register" here is a *cell* a producer
reads, which the schema already has, and the honest fix is to delete the
question rather than widen the field: a shadow is a register file a tick defers,
and a family that defers nothing has none. What SID Wizard needed instead was
§4.5 — the ordering `meta.shadow` was making unnecessary for GoatTracker 2 has to
be stated for a family without one.

### 4.10 The note column, a third time — and what naming a command costs

§3.6's table was written from this family's note column, and it holds: `$01–$5F`
is a pitch and everything else is a field or a command. Two refinements the tune
forced.

`$7D`/`$7E` are the `gate` field — but SID Wizard's gate is a **mask** ANDed into
every waveform the table produces, not a ctrl bit, so what the field *does* is
a `gate_row` stream the row program runs when the event carries a gate
statement: one row, `gate := mask ; wave := (wave & mask) | (mask & 1)`.
The instrument's gate-off table pointers (`ins[$C..$E]`) are an arm neither tune
takes; the object carries them and traps on them, §4.13's discipline.

And `$3F` in the instrument column is `tie` **plus** a command: legato is not
only "do not retrigger", it writes `FREQMODH = $7F` and turns the note into a
portamento so wide it snaps. That is a command named `legato`, and it is the one
place the object emits a command the score's bytes do not carry a number for.

The cost of naming a command by what it does, measured: three of SID Wizard's
effects have the *same* encoding in two columns (the note column's `$60–$6F` and
small effect 8; the instrument column's `$40–$7F` and small effects 4–7; and
`arpeggio.speed` as small effect C and big effect C), so a score that names them
by what they do cannot say which byte carried one. The round-trip test therefore
reads the row's *shape* — how many of its four columns it has, which is the
bit-7 continuation the layer spends — off the tune and every value out of the
object. §8 of prototype-trackerprog.md already says the trackerprog is *a*
preimage; this is what that costs on a real pattern.

### 4.11 One musical question, one place that answers it

The check the second family's §6.1 asks for, run on the third. Two answers had
to be merged:

- **"does this row key a note?"** was computed in `take_row` (for the payload)
  and again in `fetch` (for the cell the fetch stages). One expression,
  `Player.keys`, now answers it, and both read it.
- **"what frequency does a note past the top of the tuning have?"** was in
  `transpose` (Commando's arpeggio) and again in `take` (this family's waveform
  step). One expression, `Player.freq_of` over `Player.past`, now answers it,
  and the modulator that asks still owns the words — the `beyond` record moved
  from the accumulator to whatever is stepping, which for SID Wizard is the
  waveform stream.

---

## 5. What the spec got right

- **§3.1's `commit_order` is one datum per tune, and this is the case that
  proves it.** The two builds are one player under different flags, and the only
  thing the object says differently about their *sound* is `(ad, sr, ctrl)`
  against `(sr, ad, ctrl)`. §7 measures it: swap it and one tune's certificate
  fails on exactly the ticks a hard restart or a note start writes the pair.
- **§3.7's keyboard tracking is `tabcell(T[c])`, not a `tablestep`** — the row is
  cited to SID Wizard and it held exactly as written, on the cutoff target
  (`EXPTABH[ckbdtrk + dpitch]`) and, in the same shape, on the pulse's (a
  *difference* of two adjacent entries, which is where a `tablestep` would have
  been wrong twice over: the entries are not adjacent notes and the table is not
  the tuning).
- **§5's filter sweep is `split(3, 8)` with a signed 11-bit delta** — also cited
  to this family, also exactly as written. The object carries one global cell of
  width 11 and lets the commit split it; the `PHP`/`PLP` that carries the
  fraction's overflow into the high half is the split's own arithmetic and
  disappears.
- **§3.7's filter ownership is last-writer over one global channel, with no
  ownership construct.** `FLTCTRL` is a global cell a note-on writes and the
  filter stream's own guard reads; nothing else was needed.
- **§3.6's `play(pattern, transpose)`** — End of the World's orderlist carries
  two transposes and they land where the schema says, one row late, because
  `TRANSP2 → TRANSP` is the delay the sequencer already has.
- **§3.2's materialisation rule**, on a table that is literally another table's
  bytes: `EXPTABH` is `FREQTBH − 11`, and the object carries it as its own 107
  values. That is Blackbird's overlapped-array row, met for real.
- **§4.13's `trap`** — seven arms neither tune takes are data with a reason
  attached rather than omissions: a waveform-table jump, a filter-table jump, a
  pulse jump onto a row that takes a width, a chord's `$7E` return, the test-bit
  hard restart, an instrument's gate-off pointers, and the modulation amount's
  own clamp. The certificate is the proof they are untaken.
- **§6's materialisation over the horizon.** The three tables are the
  instrument's own three-byte rows, and the *initial* cursor's rows — the ones a
  voice walks through the instrument header before any note has started a table
  — are rows of the same stream, on their own grid, ending in a trap.

One §5 delta form remains unexercised by any of the three families: the
sign-extended table entry that is not an absolute table cell (§10 keeps it
open, and this family's filter step is the `tabcell(T[c], signed 11)` that the
open question already excludes).

---

## 6. Finding the data

The music blob is position-independent: `INITER` adds its base into every
table-base operand, so **before init those operands hold offsets and no
signature over them means anything** (54 init-only cells over 39 instructions,
31 of them in the tick — prototype-sidwizard.md W4). The tool therefore runs the
tune's own `init` on the PcodeVM and reads the image the *tick* sees. That is
not a shortcut around the relocation; it is the relocation, evaluated once, by
the program that defines it.

On that image the GoatTracker 2 method works unchanged: locate each datum by the
**operand of the instruction that reads it**, found by a wildcarded opcode
pattern. **32 signatures**; the whole per-voice state comes from one of them,
because `SPDCNT`'s operand fixes the base of the five stride-7 bunches and the
layout `player.asm` gives fixes the rest (the constants bunch comes from the
filter-route site, because the 1.6 export leaves a four-byte gap the 1.9 one
does not).

What is new is that the two builds are not the same code. **Seven** signatures
carry alternatives, exactly one of which may match, and which one is itself a
datum: the hard-restart envelope pair's order (and so `commit_order`), the
note-start pair's and whether it goes through the slowdown envelope remap, the
1.6 `LDA FREQTBH,X` first-frame bug against 1.9's `,Y`, whether the note start
resets `ARPSCNT`, whether the waveform step keeps the pitch it computed in a
cell, whether the cutoff commit has a filter shift, and whether `WRPITCH` adds
the detune or takes the slowdown build's path. **Six** more signatures are
optional outright — the slowdown gate, the orderlist's effects, its transpose,
the filter shift, the owner re-check on a release, and the effects that clear a
sweep counter — and a build without the feature simply has no site.

The three dispatch tables are read too, but only to ask one question of each
entry: **does this build's handler exist?** An entry pointing at a bare `RTS` is
an effect the exporter compiled out, and the score names it `nop` and keeps the
byte. Emomyst has five such small effects (9, B, D, E, F) and nine big ones
($17–$1F); End of the World has two ($1D, $1E). That is the one thing the naming
has to consult the table for, and it is not the index — it is whether the
handler is there.

That the object *is* the tune's data and not a reading of it is checked rather
than asserted: `test_every_byte_of_the_tune_s_data_is_in_the_object` reconstructs
the tuning, the exponent table, the chord table and its starts, the tempo table,
every instrument's sixteen-byte header and all three of its tables, and every
pattern, and diffs them against the image, byte for byte, on both builds. The
orderlist is compared as the steps it decodes to, as GoatTracker 2's is.

---

## 7. Measurements

The print, `trackerprog.md`, measured the way architecture §11 asks:

| tune | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Emomyst | 1,408 | 13,990 | 1,378 | 7 | 30 | 1,378 | 5,896 |
| End of the World | 2,012 | 22,123 | 1,972 | 7 | 40 | 1,972 | 7,448 |

`xz -9e` of the serialised object, §9's acceptance #3:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.md`, Emomyst | 77,397 | **5,896** |
| `trackerprog.json`, Emomyst, compact | 130,209 | 6,272 |
| — its `score` half (orders, patterns, 44 commands) | 85,297 | 2,372 |
| — everything else (tuning, streams, accs, instruments) | 44,903 | 4,276 |
| `tuneprog.md`, the source print | 51,300 | 8,904 |
| the whole load band | 5,284 | 3,576 |
| `trackerprog.md`, End of the World | 121,772 | **7,448** |
| `trackerprog.json`, compact / its `score` half | 204,570 / 132,196 | 7,852 / 3,544 |
| its `tuneprog.md` / load band | 54,965 / 6,171 | 9,652 / 3,992 |

The layer's claim holds on both tunes: the score compresses better than the
program that played it (5,896 against 8,904; 7,448 against 9,652). The margin is
narrower than GoatTracker 2's, and the reason is in the object: SID Wizard's
tables are *per instrument*, so eleven instruments carry eleven waveform tables
where GoatTracker 2's thirty share one.

**What the object needs, poisoned one datum at a time.** Each row is the object
with one thing taken out, re-rendered against the same reference; the count is
the ticks whose write list is no longer identical.

| datum taken out | Emomyst | End of the World |
| --- | --- | --- |
| `commit_order` swapped, 1.6's for 1.9's | 2,225 | 1,897 |
| the tick as a sequence of acts (§4.5) | 500 | 44 |
| the filter's keyboard tracking | 7,379 | 0 — `CKBDTRK` is zero throughout |
| the pulse's keyboard tracking | 3,464 | 0 — `PKBDTRK` is zero throughout |
| the carry the pulse write leaves (§4.7) | 0 — 1.6's write-out drops it | 14,245 |
| the detune column | 0 — 1.6's write-out drops it | 760 |
| the clamp's `edge` (§4.8) | 317 | 812 |
| the frame the slowdown gate spends | 8,084 — every tick moves | 0 — 1.9 has no gate |
| the rows before the first note (§5) | 6 | 2 |

Every non-zero row is a §2 **divergence**, not a permutation: rule 1 keeps every
edge write and rule 2 the tick's last value, so nothing measured here hides in
the reduction. Every zero is an arm the build does not have, and the two columns
disagreeing is the point — the object carries both builds' data and each build's
own signature decides which arm is live.

Code, against GoatTracker 2's:

| file | lines | role |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 1,009 (was 821) | §4 + §5, one procedure over the object |
| `deity_informant/trackerprog/printer.py` | 608 (was 507) | the flattened form and §6.2's numbers |
| `deity_informant/trackerprog/attest.py` | 81 (unchanged) | §2's comparison |
| `tools/trackerprog_sidwizard.py` | 1,504 | the transliteration, the signatures and the PcodeVM reference |
| `tests/trackerprog/test_sidwizard_oracle.py` | 330 | the two certificates, the loop, and the byte-for-byte round trip |
| `tests/trackerprog/test_universal_phases.py` | 345 | hermetic snippets, one per form of §4 |

The player grew by 188 lines to carry a third family and covers *no* tune: it
still has neither Commando, nor GoatTracker, nor SID Wizard in it. Reading the
three objects together afterwards took the *object's* grammar back down — the
union of `meta` keys 26 → 21 and the keys the player branches on 15 → 10
(prototype-trackerprog §7, backlog §6.4) — for 14 more lines here, which is
where the generality belongs: paid once, in the player, rather than once per
family, in the schema.

**Three instrument columns neither build reads.** The audit that pruned the
object's dead surface (prototype-trackerprog §7) found `wave_base`, `pw_base`
and `flt_base` — an instrument's first row in each of its three tables — named
by exactly one reader, the `stream.wave` / `stream.pulse` / `stream.filter`
commands, which re-point a cursor to `base + index + 3·param`. Neither Emomyst
nor End of the World emits one of those commands: GoatTracker 2's two builds
emit eighteen between them and SID Wizard's two emit none. So the three columns
are written and read by nothing in either certified object, and they are *not*
struck, because the reader is real and the tool would answer a build that has
one with a `KeyError` rather than a refusal. It is §4's own distinction stated
about a column instead of a form: a field no exemplar exercises is untested,
which is not the same thing as a field no consumer has.

---

## 8. The four things the family was expected to force

Four expectations came with this exemplar. Two held, one inverted, one could not
be exercised — and saying which is which is the point of writing it down.

| # | the expectation | what the code said |
| --- | --- | --- |
| 1 | the two versions differ in `commit_order` **and almost nothing else**, the sharpest available test that a version difference is one datum | **held, for the sound.** The object says one thing differently about what the chip hears. It says a dozen more about what the *exporter* compiled in — a first-frame indexing bug, an envelope remap, a detune column, an orderlist with effects, a slowdown gate, a filter shift, an owner re-check, an `ARPSCNT` reset, five dead small effects, nine dead big ones. Those are build flags, not versions, and §6 reads each off its own signature |
| 2 | `meta.shadow` is all-or-nothing and needs to say *which* registers pass through it | **inverted (§4.9).** There is no flush in either binary, so there is no shadow to make partial. The field stays as it is and the family that has none says so by not carrying it — and pays for it in §4.5 instead |
| 3 | `BIGFXTABLE`'s 31 words and the two `BCC`-offset tables are the same trap GoatTracker 2's nibble was | **held.** No command is named by an index; the tables are read only to ask whether a handler exists. What the naming *costs* is §4.10's: three effects have one name and two encodings, and the round trip has to read the row's shape off the tune |
| 4 | `$FE` is the second exercise of the `stop` terminator, and `play(vol?, tempo?)` exists for this family's orderlist columns | **not exercised.** Both tunes' orderlists end `FF pos`, and neither carries a volume or tempo column: Emomyst's has no effect bytes at all and End of the World's has two transposes. The object carries `stop` and the two columns and renders neither, and the certificate cannot claim them. `play(pattern, transpose)` *is* exercised, on End of the World, and is the first certified family to do it |
