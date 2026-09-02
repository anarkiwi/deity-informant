# Prototype: Quintessence as a trackerprog — the seventh family, and the score that is compressed

A **hand transliteration** of lft's Blackbird (anatomy
[§3.9](playroutine-anatomy.md)) into a trackerprog
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)),
GoatTracker 2 ([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)),
SID Wizard ([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md)),
defMON ([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md)),
JCH ([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md)) and
Follin ([prototype-follin-trackerprog.md](prototype-follin-trackerprog.md)),
and certified against the tune's own player on the PcodeVM.

Five results:

1. **The whole song renders.** 10,426 ticks — 2,085 rows of five frames, the
   HVSC length of 208 seconds — **0 divergences** on §2's observable, and per
   register the two write lists agree value for value and in order
   (`same_per_register_order`). `end.kind = horizon`, `loop` null: the tune's
   state never repeats, and the certificate is bound to the tune's own
   ([lft-quintessence.json](certificates/lft-quintessence.json)), whose horizon
   this is.
2. **The seventh family cost the universal player nothing.** Not a line:
   `universal.py` and `printer.py` are exactly as the sixth family left them
   (#321). Six families each needed a form; this one needed none, and that is
   the layer invariant's first free reading.
3. **The score is compressed, and none of that survives.** The tune ships one
   LZ stream of 2,961 bytes that expands to 7,579 token bytes in three 256-byte
   ring buffers, with a copy-with-transpose primitive and packed delays of up to
   sixteen rows. §6's materialisation rule says storage is dropped; what the
   object carries is **6,255 rows, every one of `dur` 1**, no decompressor, no
   buffer, no delay token — and it prints to 5,860 bytes of `xz -9e` against the
   source `tuneprog.md`'s **7,956**.
4. **§2's dropped voice order became load-bearing, and the harness was not
   dropping it.** Every earlier family finishes one voice before starting the
   next, so its writes interleave the way the player's do. This one runs a
   tokenizer pass over all three voices and *then* its audio engine over all
   three, so the two sides' writes are permuted between voices on 8,442 of the
   10,426 ticks and identical inside every one. `attest` printed "order between
   voices inside a tick" on its own `dropped` list and compared the flat edge
   list anyway; it now compares per voice, which is what
   [`certify.divergence`](../deity_informant/trackerprog/certify.py) already did
   on the certificate's side. All fourteen earlier builds re-certify unchanged,
   none losing an identical tick.
5. **The tuneprog front end had to be fixed before there was a source at all.**
   Blackbird's `X = voice×7` indexes the state arrays *and* `$D400,X`, so the
   region carrying `v_wavemask` ($12F3/$12FA/$1301) is typed `io` — and the
   interpreter took a site's class for the address's, pinning a RAM read as a
   chip input. `AND $12F3,X` trapped `input exhausted` at tick 0. The address
   decides, exactly as the tracer's own read and write decide it (§4.1).

Reproduce:

```
tools/trackerprog_blackbird.py $HVSC/MUSICIANS/L/Lft/Quintessence.sid \
    --source docs/certificates/lft-quintessence.json --certify --out out/blackbird-tp
```

One invocation, about six seconds: the tune has one subtune and one horizon.

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the layer
needed · 5 the prose-only row, corrected · 6 finding the data · 7 measurements ·
8 boundaries.

---

## 1. The object

| section | what this tune has |
| --- | --- |
| `meta` | 3 voices, order 2·1·0, `commit_order (ad, sr, ctrl)`, `tick = fetch · prelude · row · machine` |
| `meta.tempo` | a counter on `master`, `step −7`, boundary at `phase == 0`, reset to the stream's own tempo byte ($1C); `fetch` at `phase == 21`, `early` at `phase == 14` |
| `pitch` | **269 u16 rows, one per quarter semitone**, base 36 |
| `streams` | `pitch` 111 rows of the 143-byte `fxtable`, `wave` 59 of the 72-byte `wavetable`, `filter` 1, `pwprepare` 256, and four one-row act streams |
| `accs` | **one**: the pulse accumulator, `wrap` at 8 bits, a step or a reload |
| `instruments` | 14, each `adsr` + a `wavepos` its note-on points at; the 13 at or above the sort threshold carry a prelude |
| `score` | 3 patterns × **2,085 rows**, one order step each, `end: horizon`, 33 `point` commands |
| `globals` | `after: [filter]`, committing $D418, $D417, $D416 in that order |

The score's rows, by voice: 731 / 1,215 / 513 sound, 508 / 2 / 129 state a gate
off, 846 / 868 / 1,392 say nothing at all, and 731 / 1,215 / 564 re-point the
pitch program. 52 distinct pitches over the song.

## 2. The mapping, line by line

| the tuneprog says (`out/bb-quintessence/tuneprog.md`) | the trackerprog says |
| --- | --- |
| `if phase == 0: … else main(x = phase − 7)` | `meta.tempo`: a counter stepping −7, the row at `phase == 0` |
| `phase = b00EA; b00EA ^= b125F` | the reset's own reload, the tune's tempo byte; the groove mask measures `$00`, so the family's two-value swing is one value here |
| `prepare1`: `timer += 1; if timer >= 0` … the effect token | the `fetch` at `phase == 21`, and the row it stages |
| `prepare2` → `p_1059`: `sid[v].sr = 0; voice[v].ctrl = $FE` | `Ins.prelude`, run at `early` — `phase == 14`, two rows' boundaries away |
| `prepare3`: `timer = t13 \| $F0` | nothing: the packed delay is materialised into rows of its own |
| `execute`: `voice[v].ctrl = $FF` / `= $FE` | the row's `gate` statement, written to `@wavemask` |
| `execute`: `if t4 >= 2: sid[v].sr = $F` | the `restart_sr` act |
| `execute`: `voice[v].cursor_1307 = T151C[t4]` | the instrument's `on_note`: one `point` on the wave stream |
| `execute`: `if t4 >= 2: sid[v].ad = 0; sid[v].ctrl = 1` | the `restart_gate` act |
| `execute`: `sid[v].ad = ad[t4]; sid[v].sr = sr[t4]` | the `envelope` act |
| `execute`: `if cursor_12F1: freq_hi_idx = T1538[…]` | a `point` command on the pitch stream, one per effect number |
| `execute`: `freq_hi_idx_2 = b12F0 << 2` | the row's `note`, in quarter semitones |
| `p_10AE`: `fxpos += 1 + (T155D[y+1] < 0 ? … : 0)` | the `pitch` stream's per-row `next` |
| `p_10AE`: `T155D[y] == 0 ⇒ freq = $FFFF` | that row's `sets: [[pitch, $FFFF]]` |
| `p_10AE`: the four `FREQ_HI/LO` arms | `{"tuned": note + offset}` over the quarter-semitone `pitch` |
| `p_10AE`: `T15EC[y] >= $C0 ⇒ y += …` | folded into the row that lands on it; a jump is no row |
| `p_10AE`: `sid[v].ctrl = a7 & voice[v].ctrl` | the `wave` row's own `sets` |
| `p_10AE`: `b12EE = T15EF[y] < 0 ? … : … + b12EE` | `Acc(pwidth, const(b) \| reload(b<<1), wrap 8)`, `run` before the row's sets |
| `p_10AE`: `sid[v].pw_lo = pw_hi = pw_lo[b12EE]` | a `tabcell` on the `pwprepare` stream, both halves |
| `p_10AE`: `sid.mode_vol / res_route / cutoff_hi` | `globals.after` + `globals.commit`, in that order |

## 3. The certificate

```jsonc
{"ticks": 10426, "divergence": null, "writes": 158010,
 "identical_ticks": 1984, "permuted_ticks": 8442, "same_per_register_order": true,
 "loop": null, "end": {"tick": 10425, "kind": "horizon"},
 "source": {"tune": "Quintessence.sid", "certificate_digest": "625a2478df52bd7d",
            "rendered_from": "f135fd9005672134"}}
```

There is no `refusals` key because nothing refused. `identical_ticks` is the 1,984 on which the two
write lists match byte for byte and in order; the other 8,442 are permuted
between voices and never inside one, which §2 drops and §4.2 measures.

## 4. What the layer needed

### 4.1 A site's class is its envelope's; a byte is the chip's by its address

Blackbird's whole register economy is one index: `X ∈ {0, 7, 14}` reaches
`$12EE,X` (the state block), `($E0,X)` (the ring-buffer pointers) and `$D400,X`
(the chip). `regions` therefore unites the wave-mask cells with the SID into one
region of kind `io`, and `lower.cls` gave every access through that region the
class `io` — including `AND $12F3,X`, whose envelope is `[$12F3, $1301]` and
lies wholly in RAM. `Machine.ioload` then asked for a pinned input that no trace
ever recorded, and the certification stopped at tick 0 with
`{"trap": "input exhausted", "detail": "$1301"}`.

The tracer has never had this problem, because it decides by the address:
`if IO_LO <= addr <= IO_HI: chip else RAM` (`tracevm.read`, `tracevm.write`).
`Machine.ioload`'s own docstring said the same — *"a byte from $D000-$DFFF: a
pinned input when I/O is mapped, else RAM"* — and the code did not test the
address. It does now, on both sides: a load outside the window reads RAM, and a
store outside it writes RAM, marks the byte known and adds it to the footprint
rather than logging a SID write or a schedule effect. The general statement: **a
class is a property of a site's envelope and a landing is a property of an
address**, and an envelope that reaches I/O does not make every access through
it reach I/O.

Measured: `tuneprog_recert.py` reproduces all 51 committed certificates, 0
mismatched, and *Quintessence* now certifies over its whole 10,426 calls with 0
divergences and 0 envelope traps — where architecture §9.2 had it in the
"certify at 15 s, not run to length" row, and 15 seconds did not certify either.

### 4.2 The comparison drops voice order, and now it does

§2's rule 1 keeps every `ctrl`/`AD`/`SR` write in tick order because the
envelope generator is edge-triggered — which is a fact about **one voice's**
envelope generator. The certificate says so: "the interleave between voices of one
tick's writes" is on the `dropped` list, and `certify.divergence` splits the edges per
voice before comparing them. `attest`, the hand tools' harness, printed the same
`dropped` list and then compared `TickObs.edges` as one flat tuple.

Six families never noticed, because a player that finishes one voice before
starting the next produces the same interleave the universal player does.
Blackbird does not: `prepare2` writes `$D406`, `$D40D`, `$D414` for all three
voices and *then* `everyframe` writes each voice's frequency and control byte,
so on a hard-restart tick the tune writes `v1.sr v0.sr v2.ctrl v1.ctrl v0.ctrl`
where the player writes `v2.ctrl v1.sr v1.ctrl v0.sr v0.ctrl`. Per voice the two
are identical; flattened they are not.

`attest._voiced` is the fix, and it is `certify._byvoice` said once more. All
fourteen earlier builds re-certify at 0 divergences and **not one loses an
identical tick**: defMON ×2, GoatTracker 2 ×2, SID Wizard ×2, Follin ×3 and
`jch-knob-at-night` stay write-for-write identical over their whole horizons, and
Commando ×3 and `jch-guldkorn-intro` were already permuted. A weakened
comparison that hid something would show as a fallen `identical_ticks`; none did.

### 4.3 The hard restart is a pipeline, and the schema already had it

Blackbird decodes a row over four frames, one token class per frame, and the
second of them is two rows' worth of frames before the boundary. That is not a
schedule the player runs — it is where `prepare2` happens to fall — and the
author's own comments name the two halves `Hard-restart 1` and `Hard-restart 2`.
The schema needs no new construct for it: `Ins.prelude` is "the instrument's
early rows" and `meta.tempo.early` is the clock's own statement of when they run,
so the prelude is one row (`sr ← 0`, `@wavemask ← $FE`) at `phase == 14`.

Two data forms carry the rest. `meta.stage` is `[{"ins": True}]`, because the
prelude reads the instrument the *next* row will play and the tune reads that
byte in the same pass; and `meta.stage_sounds` names a cell the fetch fills, so
the `early` guard is `phase == 14 and willsound != 0` — a voice whose row is not
due neither reads a token nor restarts.

Measured: no prelude at all diverges on **1,793** ticks; the instrument taken at
the boundary instead of staged diverges on **1,434**.

### 4.4 The tuning is quarter semitones, and §3.2 predicted it

§3.2 wrote Blackbird's frequency table out as a projection while the family was
prose-only: "Blackbird's two byte arrays overlapped by 15 bytes, whose
quarter-semitone entries are the **sum of two entries of the same array at fixed
offsets**, lift to explicit u16 rows — the values read, not the bytes stored". It
lands as written. The object's `pitch` is 269 u16 rows indexed by a
quarter-semitone number, `note` is `pendnote × 4`, and a pitch program's offset
byte is added to it: `{"tuned": {"and": [{"add": [note, d]}, $1FF]}}`, the mask
being the 9-bit add the `ROR` makes.

One thing the projection did not say, because only the certified program shows
it: the two-entry sums **carry the low half's own carry-in**, `+ (t8 & 1)`,
which is the "small consistent error" the author's comment admits. Dropping it
diverges on **2,185** ticks.

The horizon asks for indices 36 through 304, so the four arms read the array at
9 through 100 of its 111 entries and **nothing runs past the tuning** — this is
the first family since Commando whose note space needs no `beyond` record at all,
and the first that never needed one.

### 4.5 A backward jump is no row

`wavetable[y] >= $C0` is a relative backward jump, resolved at the read and
never itself a control byte, so it occupies no frame. The object folds it into
the row that lands on it: row `y` carries the *target's* control byte and the
target's own advance. That keeps the stream's rows one-to-one with the frames
they occupy, which is what §3.3's `next` is, and it means every declared row's
`ctrl` constant is below `$C0` — a testable invariant.

The advance past a pulse parameter is `2 + the control byte's own sign bit`: the
`ASL A` that tested bit 6 left its bit 7 in the carry and the `ADC #$02` took it.
Over this horizon the cursor never exceeds 71, so the term measures **0**; it is
in the object because the program computes it, and it is stated as measuring
nothing.

### 4.6 A packed delay is storage

A `$B8–$C7` token holds a voice for 16 − (b & $0F) rows, and the player's
`v_trtimer` counts it out. §6 says packed rests are a storage idiom dropped by
materialisation, and §3.6's `dur` in the prefetched path is the *packed-rest*
form — a row the fetch spends without ever applying it. Blackbird's held rows are
not that: its `execute` runs on every one of them and does nothing only because
`pendfx` and `pendins` are zero. So every row cycle is one event of `dur` 1, and
a held row is an event that says nothing — which is exactly what the program does
and what §6 asks for. 6,255 rows carry what 7,579 token bytes said.

### 4.7 The row clock is the phase, and the phase is one byte

`$E6` is the row timer *in units of seven*, the four-phase selector, and the
voice index the unpacker tops up — one byte doing three jobs. Two of the three
are storage: the unpacker is gone with the buffers, and the phase selector is
the clock's own `boundary`, `fetch` and `early` guards reading `{"cell":
"phase"}`, which is §3.6's virtual cell for the step a tick is. What is left is a
counter of `step −7` with one reset clause. Lengthening the row by one frame
diverges on **10,374** of 10,426 ticks.

## 5. The prose-only row, corrected

§3.5's prelude table carried a Blackbird row written from the anatomy, marked
prose-only:

> | Blackbird (prose-only) | `early = 2`; `set(sr,0) set(ctrl, gate off)`, note row ADSR=0000 then the real AD/SR | anatomy:133-135 |

`early = 2` is right and the rest is two errors:

1. **The prelude writes no `ctrl` at all.** It writes `sr ← 0` and moves
   `v_wavemask` to `$FE`; the gate goes off because the audio engine ANDs that
   mask into every control byte it writes for the next two frames. A `set(ctrl,
   …)` in the prelude would be a fourth edge write on a tick that has one.
2. **The note row is five writes in three acts, not two.** `sr ← $0F` (the
   author's Hard-restart 2), then `ad ← 0` and `ctrl ← 1` (Hard-restart 1: the
   gate opened on a zero waveform with a zero envelope), then the instrument's
   `ad` and `sr`. The order is `sr, ad, ctrl, ad, sr`: `AD` and `SR` each appear
   twice, which is why one `commit_order` cannot carry the row and the tick is a
   sequence of acts (§3.1) — the row program's three stream steps are the three
   acts, and the only thing `commit_order` has to say is that **`ad` comes before
   both of the others**.

The corrected row belongs in §3.5, and it is no longer prose-only.

## 6. Finding the data

Every earlier family's tool reads its object off the tune's image: the tables are
where the certified program's operands say they are, and the score is a walk over
the bytes. Half of that holds here. The tables are read from the image at the
addresses `out/bb-quintessence/tuneprog.md` reads them at — `$1331`/`$1391` the
frequency array, `$1400` the pulse map, `$14FF`…`$1529` the four 1-based
instrument columns, `$1537` the effect starts, `$155D` the pitch programs,
`$1559` the filter, `$15EC` the wave programs, and `$21F8`/`$21F9` the tempo
command's own two operand bytes, which the stream's end-of-stream jump makes it
read twice.

The score is not on the image. It is one LZ stream consumed downwards from
`$221A`, interleaved across three voices in whatever order the unpacker's
lookahead test pulls it, and the three passes that tokenize it are spread over
three frames. **Materialisation is measured rather than re-implemented**: the
tool runs the tune's own player once — the same PcodeVM pass that produces the
oracle's write lists — and reads each voice's finished tokens out of the cells
the tokenizer leaves them in (`pendins`, `pendfx`, `pendnote`) at the tick
between the last pass and the boundary that applies them, with `v_trtimer == $FF`
at the first pass saying whose row is due. That is §6's "the trackerprog
represents the score the trace played", taken literally: the decompressor is
provenance, and the rows are the score.

The reached-row sets are the one thing the tool does re-derive, because a row the
horizon never steps on is a `trap` and not a row (§3.3): the walk steps both
cursors over the materialised rows with the row's own phasing — three frames on
the cursors the row before it left, then the boundary, then two more. Getting the
phasing wrong claims rows the tune never steps on, and the render is what says it
is right, because a row marked `trap` and then reached stops it. The declared
rows and the stepped rows are **exactly equal**: 111 of 143 and 59 of 72.

## 7. Measurements

Every poison rendered over the whole 10,426-tick horizon and counted against the
PcodeVM under §2's reduction.

| the form | poisoned | differing |
| --- | --- | ---: |
| the row's five frames | six frames | 10,374 |
| the gate mask | the row does not write `@wavemask` | 9,365 |
| `row_consumes_tick: false` | the row spends the voice's tick | 8,829 |
| the pulse parameter's two arms | every parameter a step, none a reload | 5,797 |
| the quarter-tone sums' carry-in | dropped | 2,185 |
| the prelude | no instrument carries one | 1,793 |
| `meta.stage` | the instrument taken at the row | 1,434 |
| `commit_order` | `(sr, ad, ctrl)`, `(sr, ctrl, ad)`, `(ctrl, sr, ad)` | 1,428 |
| | `(ctrl, ad, sr)` | 1,070 |
| | `(ad, ctrl, sr)` | **0** |
| the pitch program's re-point | the row points nothing | *trap*: `note 305 is outside the tuning` |
| `voice_order` | `0, 1, 2` | **0** |
| `globals.after` | the filter steps before the voices | **0** |
| the pulse advance's carry | `2`, without the `ASL`'s bit 7 | **0** |
| the stream ranks | the wave program before the pitch program | **0** |

Four of the zeroes are the boundaries §2 and §3 already state, measured rather
than assumed: voice order is dropped, this tune's filter reads nothing a voice
writes so its position is free, the two write-only streams cannot see each other,
and the carry the `ADC #$02` takes is provably zero while the cursor stays under
254. The fifth is the interesting one: **`commit_order` here says only "`ad`
first"**, because `sr` and `ctrl` never share an act, so two of its six values
render the tune and four do not. A datum measured to two values is still a datum;
it is a coarser one than the schema's name suggests, and this is the first family
to show it.

The re-point poison is the object refusing rather than diverging, which is what
§5's asserted bound is for: with the pitch program never restarted, a cursor
walks into an offset byte whose sum with the row's note is index 305, and the
tuning has 269 rows from 36. The render stops and names it.

Print, §6.2's six numbers plus `xz`:

| | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `trackerprog.md` | 6,911 | 48,584 | 6,899 | 7 | 12 | 6,899 | **5,860** |
| source `tuneprog.md` | — | — | — | — | — | — | 7,956 |

This is the exemplar that tests §9's first claim hardest, because its author
compressed the score himself. It fails it: 5,860 bytes of compressed print, and
6,352 of compressed object, against a load band that compresses to **3,772** —
**1.68×** (§9.1 of [prototype-trackerprog.md](prototype-trackerprog.md)). The earlier reading here compared the compressed print against the
*raw* 5,758-byte `.sid` file, of which 2,961 are the LZ stream, and even that
comparison had the print the larger of the two.

## 8. Boundaries

- **The syncpoint is an external input this tune never takes.** Blackbird
  exposes a host-writable shift register at `$ED`: an out-of-band command with
  bit 0 stalls the sequencer until the demo shifts a bit out. The certified
  program marks that branch `untaken` over the whole horizon, so `$ED` is never
  read and no input is pinned. A Blackbird tune that *does* use it reads a byte
  the tick was given, and §8's `external input` refusal is the right answer —
  not this object.
- **The player is emitted per tune.** All 40 HVSC Blackbird tunes carry
  different player bytes — conditional assembly, table sizes, and the two
  instrument thresholds the exporter sorts the table for. The threshold is
  therefore not a constant of the family but a **compare immediate of this
  build**, read from the operands of its own `CMP #imm` at `$105C` and `CPY
  #imm` at `$1218`/`$1233`, which the tool asserts agree; a build whose
  `INS_RESTART` and `INS_RESTART2` differ needs two thresholds and says so by
  failing that assertion. The object is one tune's; the *reading* is the
  family's.
- **The three out-of-band commands are two tempo writes and one stream jump.**
  Both tempo writes read the same two bytes, because the stream's own end jumps
  back over them, and both write `$1C`/`$00` — so the reload is one constant over
  the horizon and the swing `EOR` is inert. A tune whose groove mask is non-zero
  needs the reset's second clause, which §3.6's row clock already has and this
  tune does not exercise.
- **`end.kind = horizon`.** The music repeats — the stream's own end jumps
  back — but no state repeat is found over the 10,426 calls, so the source
  certificate's `period` is null and the score is materialised to the certified
  horizon rather than to a period. Why it does not repeat is
  `tools/tuneprog_period.py`'s question and is not asked here; the terminator is
  Commando's, and so is the discipline of not guessing at it.
