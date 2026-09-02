# Prototype: SID Wizard as a trackerprog — the third family, one player

A **hand transliteration** of the two certified SID Wizard tuneprogs
([prototype-sidwizard.md](prototype-sidwizard.md), anatomy
[§3.4](playroutine-anatomy.md)) into trackerprogs
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders [Commando](prototype-commando-trackerprog.md) and
[GoatTracker 2](prototype-goattracker-trackerprog.md), and certified against each
tune's own player on the PcodeVM.

Two results. **Both builds render, on one object shape and one code path**:
8,084 and 14,465 ticks, **0 divergences** on §2's observable, and *stronger* —
the write lists are **identical**, tick for tick, value for value, register for
register. GoatTracker 2 got that free from a ghost flush; SID Wizard has no
flush, so it is earned by saying that a voice's edge writes are the tick's own
**acts** in order, which is §2 rule 1 restated as data (§4.5). **The inherited
loop claim re-verifies on the render**: rendering past the first repeat, the next
period is the previous period write for write — 6,120 ticks for *Emomyst*, 7,688
for *End of the World*.

Reproduce:

```
tools/trackerprog_sidwizard.py $HVSC/MUSICIANS/H/Hermit/Emomyst.sid \
    --source out/recert-main/sw-emomyst/certificate.json \
    --certify --out out/sw-tp/sw-emomyst
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the layer
needed · 5 what this family exercises · 6 finding the data · 7 measurements.

---

## 1. The object

| section | Emomyst (SW 1.6) | End of the World (SW 1.9) |
| --- | --- | --- |
| `meta` | 3 voices in order 2,1,0; `commit_order (ad, sr, ctrl)`; a **counter** row clock of four phases with two guarded resets, the fetch at phase 0 and the row at phase 2; the tick a sequence of **acts**; a prologue that spends the frame the slowdown gate takes | the same, `commit_order (sr, ad, ctrl)`, and no prologue |
| `pitch` | `base 0` and **96** contiguous frequencies — the tune's whole tuning | 96, the same shape |
| `streams` | **14**: `wave` (43 rows), `pulse` (26), `filter` (19, a global cursor), `exp` (107), `chords` (18), `chordstart` (6), `tempo` (8), `voice_bit` (3), plus `hard_restart`, `exit`, `gate_row`, `pitch_row`, `pitch_out`, `pw_out` | 73 / 61 / 31 / 107 / 0 / 2 / 8 / 3, the same fourteen |
| `accs` | **9** declared forms — a vibrato phase and its freq, a delay, a rising modulation, two free slides, a tone portamento, a pulse step, a cutoff step | the same nine |
| `instruments` | **11**, sixteen columns each | **21** |
| `score` | 3 order programs of `play(pattern, transpose)` ending `jump`; 21 patterns, 843 events, **44** distinct row commands | 31 patterns, 1,314 events, **60** commands |
| `globals` | one channel: cutoff as one 11-bit cell, band, resonance, routing, the owner voice, the owner's note, the master volume, the filter shift and eight tempo cells | the same |

The nine accumulator ids (`vib_phase`, `vibrato`, `vib_delay`, `freqmod_step`,
`slide_up`, `slide_down`, `toneporta`, `pulse_step`, `cutoff_step`) are labels in
the data; the player's one dispatch is on the *form* of a delta, a policy or a
stream row.

---

## 2. The mapping, line by line

Left column is the certified tuneprog's own text
(`out/recert-main/sw-*/tuneprog.md`) and anatomy §3.4. Right column is the
object.

| the player says | the trackerprog says | spec row |
| --- | --- | --- |
| `SPDCNT` post-incremented against `TEMPOTBL[TMPPOS]`, `BEQ`/`BVC` | `meta.tempo`: a cell the tick steps by `+1` and two guarded **reset** clauses; the V-flag trick is spent for what it decides (§4.1) | §3.6 |
| `TEMPOTBL` entry bit 7 = "loop the tempo program" | the second reset clause: `spdcnt >= tempo & $7F` and back to `tmpptr`; the first is `== tempo` and on to `tmppos + 1` | §3.6 |
| ticks `0/1/2` and `else` | `meta.tempo.fetch 0`, `early [phase < 2]`, `boundary 2`; every stream and arm carries its own `when` over `{"cell": "phase"}` | §3.6 |
| `READROW`'s 1–4 bytes with bit-7 continuation | `Event{sounds, note, gate, tie, ins, arm, dur}` — the note byte's token class is spent, not re-encoded | §3.6 |
| `$70–$77`, the packed rest | the event's `dur`, in **rows** | §3.6 |
| `$60–$6F` set vibrato amplitude, `$79–$7C` sync/ring | `cmds`, named by what they do | §3.6 |
| `$7D` / `$7E` gate on/off | the event's `gate`, and a `{stream}` step of `meta.row` guarded on `gate_stmt` — one stream that says what a gate statement does (the mask, and the mask re-applied to the waveform) | §3.6 (§4.10) |
| `HARDRST` at ticks 0 and 1 with the tick number as the mask | the instrument's **prelude**: one stream, two rows, each guarded by the clock's phase and the instrument's own control bit | §3.5 |
| 1.6 writes `AD` then `SR`, 1.9 `SR` then `AD` (anatomy:1232) | **`meta.commit_order`, and nothing else** | §3.1 |
| the HR reads `INS[CURIFX or CURINS]`, the tables read `INS[CURINS]` | `meta.stage`'s one row, `@hrins := payload.ins`, and `{"insrec": ["hrins", "hr.0"]}` — the prelude belongs to the row's instrument, the streams to the voice's cursor | §3.5 (§4.2) |
| `TICK_2`'s `STRTSND` | the instrument's `on_note` — one inline stream of `sets` and `point` rows, guarded by its own control bits and by whether the row named an instrument (`TABLRST`) | §3.5 |
| `WFARPTB` rows `[wave\|cmd, pitch\|chord, detune]`, `--ARPSCNT` | the `wave` stream, and `rate` — a divider kept in the cell `arpscnt`, which a row and two commands also set (§4.3) | §3.3 |
| `SETPWID` / `FILTPRG` rows `[count\|set, step, track]` | the `pulse` and `filter` streams, each record split into the row that acts and the row its landing tick holds (§4.4) | §3.3 |
| the 11-bit cutoff, `AND #7` / `LSR×3` / `PHP`/`PLP` | one global cell of `width 11` and `split(3, 8)` at the commit | §5 filter sweep |
| `CKBDTRK` / `PKBDTRK` through `EXPTABH` | `tabcell(exp[c])` on the cutoff target and on the pulse's — §3.7's own row | §5 keyboard tracking |
| `EXPTABH = FREQTBH − 11` | the `exp` table, **materialised as its values**: overlapped storage is an idiom | §3.2 |
| `CHORDS` with `$7F` = loop | the `chords` stream: a signed semitone and the row it goes on to, the loop resolved through `chordstart[curchord]` because no row knows which chord it ends | §5 arpeggio/chord |
| `SLIDEVIB` ∈ `$00/$10/$20/$30/$81/$82/$83` | nine **arms** of six accumulators, each with its guard over one cell | §5 |
| `VIDELCNT` counting down before the vibrato runs | `vib_delay`, ranked *after* the vibrato so both read the value the tick came in with | §5 |
| the orderlist `< $80` pattern · `$80–$9F` transpose · `$A0–$AF` volume · `$B0–$EF` tempo · `$FE` stop · `$FF pos` | `play(pattern, transpose, vol?, tempo?)` and `end: jump(k)`; both tunes' orderlists end `FF pos` and neither carries a volume or tempo column, so the object renders neither and the certificate cannot claim them | §3.6 |
| `WRPITCH` `sid.freq ← freq + detuner + c` | `pitch_out`, a producer that writes the chip and moves no cell, reading the flag `C` the pulse write left (§4.7) | §4 producers |
| `WRWFGHO` `sid.ctrl ← wfghost` | a `{stream}` phase of `meta.tick` | §4.1 |
| `COMMONREGS` after the three voices | `globals.commit`, run **after** the voice loop, with a guard per entry (§4.6) | §3.7 |
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

§2's comparison over the whole certified horizon, the reference being the tune's
own player on `deity_informant.PcodeVM`.

| tune | ins | patterns | events | tuning | streams | accs | ticks | SID writes | divergences |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Emomyst | 11 | 21 | 843 | 96 | 14 | 9 | 8,084 | 149,118 | **0** |
| End of the World | 21 | 31 | 1,314 | 96 | 14 | 9 | 14,465 | 269,309 | **0** |

**Stronger than §2, and without a shadow.** `identical_ticks` equals the whole
horizon for both tunes — the same list, not a permutation, with
`same_per_register_order` true. That is §4.5: without it §2 *itself* fails, on
500 ticks of Emomyst and 44 of End of the World, because rule 1 keeps every edge
write and collapsing two writes of one register into one is a divergence.

**The loop claim, re-verified** on the trackerprog's *own* render: rendering
`first_repeat + period` ticks, the period after the first repeat equals the
period before it, write for write.

| tune | period | first repeat | replay window | verified |
| --- | --- | --- | --- | --- |
| Emomyst | 6,120 | 8,083 | ticks 1,963..8,082 against 8,083..14,202 | **yes** |
| End of the World | 7,688 | 14,464 | ticks 6,776..14,463 against 14,464..22,151 | **yes** |

The window starts *at* the claimed first repeat rather than one tick after it,
which is GoatTracker 2's off-by-one and not this family's: there is no flush, so
a tick's writes are the tick's own state and the claim lands where it says.

---

## 4. What the layer needed

Eight forms and three corrections. None is a family branch, and every form is a
datum the first two families do not carry.

### 4.1 A row clock counting **up**

`SPDCNT` counts **up** from zero and the row ends where it meets the tempo — the
general clock §3.6 has, of which the other two families' are values:

```jsonc
"cell": "spdcnt", "step": 1,
"boundary": [[{"cell": "phase"}, "==", 2]],
"fetch":    [[{"cell": "phase"}, "==", 0]],
"early":    [[{"cell": "phase"}, "<",  2]],
"reset": [ {"when": [...], "sets": [["@spdcnt", 0], ["@tmppos", ...]]}, ... ]
```

The reset is guarded assignment, not a reload. `SEC; SBC TEMPOTBL−1,Y; BEQ
new_row; BVC same_row` is the anatomy's technique 8, one subtraction
distinguishing "the counter met the tempo" from "the tempo entry had bit 7 set",
and what the V flag decides is two clauses: `tempo & $80 == 0 and spdcnt ==
tempo` advances the tempo program by a row, `tempo & $80 != 0 and spdcnt >= tempo
& $7F` sends it back to `tmpptr`, both zeroing the counter. `TEMPOTBL` is
*state*, the score's own `tempo` commands writing it, so it is a stream of eight
global cells read with `tabcell`. The clauses are load-bearing: no reset at all
diverges on 8,077 of *Emomyst*'s 8,084 ticks and 14,451 of *End of the World*'s
14,465. `spdcnt` 0, 1, 2 and "anything else" are four different ticks — the row
is read, the position advances, the note starts, the tables run — and the object
exposes the clock's own step as the cell `phase`, each stream, arm and prelude
row carrying the guard it needs.

### 4.2 The prelude belongs to the row's instrument, not the voice's

`HARDRST` reads the hard-restart envelope out of `INS[CURIFX or CURINS]` — the
instrument the row is *about* to play — while the waveform table it falls through
to is still running the instrument the voice *has*, and this family's tables live
inside each instrument record, so staging `ins` early would move the tables too.
The fetch stages the row's instrument into a cell of the tune's own choosing
(`{"sets": [["@hrins", {"payload": "ins"}]]}`) and `{"insrec": [cell, column]}`
reads a column of the instrument that cell names, so a prelude row says `["ad",
{"insrec": ["hrins", "hr.0"]}]` and means it (`--poison insrec-voice`, **853 of
22,549**).

### 4.3 A stream's divider may live in a cell the score can set

The waveform table steps once every `ARPSPED & $3F` + 1 ticks, and `ARPSCNT` —
the countdown — is a cell a waveform row sets (`row[0] < $10` is a repeat count),
a small effect sets and a big effect sets:

```jsonc
"rate": {"cell": "arpscnt", "reload": {"and": [{"cell": "arpsped"}, 0x3F]}}
```

Decrement, run the row where it goes negative, reload, and let the row's own
`sets` overwrite the reload where it has one.

### 4.4 A step's counter is read before its own move

`PWEEPCNT` and `CWEPCNT` are cells the player compares against the row's own byte
*before* stepping them, so the row occupies `n + 1` ticks and sweeps on `n` of
them where GoatTracker 2's occupies `n` and sweeps on all. That is not a field:
§3.3 spells it as two rows, the row that acts with `hold: n-1` and the row its
landing tick holds and acts on, appended so the instrument's own row numbers
stand.

### 4.5 An edge register written twice in one tick is two writes

The note-start tick writes `AD` from the instrument and then `AD` again from the
row's own `attack` effect: two events, which is why the tick is a sequence of
**acts** and `commit_order` orders one act's own edges (§3.1). Collapsing them
costs 500 ticks of *Emomyst* and 44 of *End of the World*, and it makes the
version difference one datum — the prelude and note-on rows may be written in any
order, because the commit sorts them.

### 4.6 The global channel commits after the voices

`COMMONREGS` runs when the three voices have, reading `FSWITCH`, `CTFHGHO` and
the owner's pitch — all of which the voices just moved — so this family's
registers are `globals.commit` and not `globals.streams` (§3.7). A commit entry
may carry a guard: the keyboard-tracking arm and the arm without it are two
entries for one register under exclusive guards (`--poison commit-guard`,
**22,548 of 22,549**).

### 4.7 A producer that writes the chip without moving a cell, and the flag between two

`WRPITCH` writes `sid.freq = freq + detuner + c`, where `freq` is the voice's own
16-bit cell and `c` is whatever carry the tick's last addition left. `assign`'s
target `pitch` emits the pair without storing, so the cell keeps the value the
waveform table's step took — `meta.pitch_target` says where that step puts it,
`"@freq"` here rather than the chip (`--poison pitch-target`, **13,994 of
22,549**). The carry is §5's live carry with the *producer* stated: the pulse
write leaves `C`, each waveform row leaves `C`, `globals.flags.C.default` is what
it is when neither ran, and `pitch_out` reads `{"flag": "C"}` — each producer an
expression written as the carry it is, `carry_out(e, 8)` for an eight-bit add and
`borrow_out(e, 8)` for a subtraction (the pulse's high half, the vibrato's phase).

### 4.8 `clamp(target)` has an edge

§5's tone portamento snaps where the step would reach or cross the target.
`PORTAME` compares 16-bit and then adds through the compare's own carry, so the
step is one larger than the speed and the test one tighter: it snaps where the
step would *cross*, and steps `speed + 1` where it would not. `policy: {"clamp":
..., "edge": 1}` is that boundary as a datum, `edge: 0` being GoatTracker 2's and
the default. Worth **1,129** of the two builds' 22,549 ticks.

### 4.9 A partial shadow is not a shadow

**There is no flush.** Nothing in either binary sweeps `$D400..$D418`; `WRPITCH`
and `WRWFGHO` write the chip from the cells on the tick that computed them, and a
"ghost register" here is a *cell* a producer reads. A family that defers nothing
has no `meta.shadow`, and pays for it in §4.5 instead.

### 4.10 The note column, and what naming a command costs

`$7D`/`$7E` are the `gate` field, but this family's gate is a **mask** ANDed into
every waveform the table produces, not a ctrl bit, so what the field *does* is a
`gate_row` stream the row program runs when the event carries a gate statement:
one row, `gate := mask ; wave := (wave & mask) | (mask & 1)`. And `$3F` in the
instrument column is `tie` **plus** a command: legato writes `FREQMODH = $7F` and
turns the note into a portamento so wide it snaps — a command named `legato`, the
one place the object emits a command the score's bytes carry no number for.

Naming a command by what it does has a cost: three effects have the *same*
encoding in two columns (the note column's `$60–$6F` and small effect 8; the
instrument column's `$40–$7F` and small effects 4–7; `arpeggio.speed` as small
effect C and big effect C), so a score naming them by what they do cannot say
which byte carried one. The round-trip test therefore reads the row's *shape* —
how many of its four columns it has — off the tune and every value out of the
object, which is what §8's "a trackerprog is *a* preimage" costs on a real
pattern.

---

## 5. What this family exercises

- **Keyboard tracking is `tabcell(T[c])`, not a `tablestep`**, on the cutoff
  target (`EXPTABH[ckbdtrk + dpitch]`) and, in the same shape, on the pulse's — a
  *difference* of two adjacent entries, where a `tablestep` would have been wrong
  twice over: the entries are not adjacent notes and the table is not the tuning.
- **The filter sweep is `split(3, 8)` with a signed 11-bit delta.** The object
  carries one global cell of width 11 and lets the commit split it; the
  `PHP`/`PLP` that carries the fraction's overflow into the high half is the
  split's own arithmetic and disappears.
- **Filter ownership is last-writer over one global channel**, with no ownership
  construct: `FLTCTRL` is a global cell a note-on writes and the filter stream's
  own guard reads. `EXPTABH` is `FREQTBH − 11` and the object carries it as its
  own 107 values, materialisation on a table that is literally another's bytes.
- **Seven `trap` arms neither tune takes** are data with a reason attached: a
  waveform-table jump, a filter-table jump, a pulse jump onto a row that takes a
  width, a chord's `$7E` return, the test-bit hard restart, an instrument's
  gate-off pointers, and the modulation amount's own clamp. The three tables are
  the instrument's own three-byte rows, and the *initial* cursor's rows — the ones
  a voice walks through the instrument header before any note has started a table
  — are rows of the same stream, on their own grid, ending in a trap.

The one §5 delta form no family exercises is the sign-extended table entry that
is not an absolute table cell; this family's filter step is the `tabcell(T[c],
signed 11)` that §10's open question already excludes.

---

## 6. Finding the data

The music blob is position-independent: `INITER` adds its base into every
table-base operand, so **before init those operands hold offsets and no signature
over them means anything** (54 init-only cells over 39 instructions, 31 of them
in the tick — prototype-sidwizard.md W4). The tool runs the tune's own `init` on
the PcodeVM and reads the image the *tick* sees.

On that image, each datum is located by the **operand of the instruction that
reads it**, found by a wildcarded opcode pattern — **32 signatures**, the whole
per-voice state coming from one of them, because `SPDCNT`'s operand fixes the base
of the five stride-7 bunches and `player.asm`'s layout fixes the rest (the
constants bunch comes from the filter-route site, the 1.6 export leaving a
four-byte gap the 1.9 one does not).

The two builds are not the same code. **Seven** signatures carry alternatives,
exactly one of which may match, and which one is itself a datum: the hard-restart
envelope pair's order (and so `commit_order`), the note-start pair's and whether
it goes through the slowdown envelope remap, the 1.6 `LDA FREQTBH,X` first-frame
bug against 1.9's `,Y` (reproduced as a *marked defect*, `bug(voice_base)`, never
as a note), whether the note start resets `ARPSCNT`, whether the waveform step
keeps the pitch it computed in a cell, whether the cutoff commit has a filter
shift, and whether `WRPITCH` adds the detune or takes the slowdown build's path.
**Six** more are optional outright — the slowdown gate, the orderlist's effects,
its transpose, the filter shift, the owner re-check on a release, the effects that
clear a sweep counter — and a build without the feature has no site.

The three dispatch tables are read only to ask whether this build's handler
exists: an entry pointing at a bare `RTS` is an effect the exporter compiled out,
which the score names `nop` while keeping the byte. Emomyst has five such small
effects (9, B, D, E, F) and nine big ones ($17–$1F); End of the World has two
($1D, $1E). That the object *is* the tune's data and not a reading of it is
checked rather than asserted:
`test_every_byte_of_the_tune_s_data_is_in_the_object` reconstructs the tuning,
the exponent table, the chord table and its starts, the tempo table, every
instrument's sixteen-byte header and all three of its tables, and every pattern,
and diffs them against the image byte for byte on both builds.

---

## 7. Measurements

The print, `trackerprog.md`, measured the way architecture §11 asks:

| tune | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Emomyst | 1,408 | 13,990 | 1,378 | 7 | 30 | 1,378 | 5,896 |
| End of the World | 2,012 | 22,123 | 1,972 | 7 | 40 | 1,972 | 7,448 |

Measured against the binary, the layer's first claim **does not hold**: the
objects are 6,288 and 7,876 compressed against load bands of 3,576 and 3,992 —
**1.76×** and **1.97×**, with §9.1 of
[prototype-trackerprog.md](prototype-trackerprog.md) carrying the current table.
The reason is in the object: SID Wizard's tables are *per instrument*, so eleven
instruments carry eleven waveform tables where GoatTracker 2's thirty share one.

**What the object needs, poisoned one datum at a time.** Each row is the object
with one thing taken out, re-rendered against the same reference; the count is the
ticks whose write list is no longer identical.

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
| the rows before the first note | 6 | 2 |

Every non-zero row is a §2 **divergence**, not a permutation: rule 1 keeps every
edge write and rule 2 the tick's last value, so nothing measured here hides in the
reduction. Every zero is an arm the build does not have, and the two columns
disagreeing is the point — the object carries both builds' data and each build's
own signature decides which arm is live.

The instrument columns `wave_base`, `pw_base` and `flt_base` — an instrument's
first row in each of its three tables — are **struck**, with the three `stream.*`
commands that were their only reader: `param` counts three-byte rows of the
instrument's *record* while a cursor counts rows of the concatenated stream, whose
map is a build-time table per instrument that a command naming no instrument
cannot carry, so the three are a named refusal (`DEAD`'s `fx.pointer`) instead of
a computed row. Neither tune emits one, so the arithmetic was never rendered.
