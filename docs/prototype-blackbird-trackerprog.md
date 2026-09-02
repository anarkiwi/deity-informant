# Prototype: Quintessence as a trackerprog — the seventh family, and the score that is compressed

A **hand transliteration** of lft's Blackbird (anatomy
[§3.9](playroutine-anatomy.md)) into a trackerprog
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders [Commando](prototype-commando-trackerprog.md),
[GoatTracker 2](prototype-goattracker-trackerprog.md),
[SID Wizard](prototype-sidwizard-trackerprog.md),
[defMON](prototype-defmon-trackerprog.md), [JCH](prototype-jch-trackerprog.md)
and [Follin](prototype-follin-trackerprog.md), and certified against the tune's
own player on the PcodeVM.

Four results. **The whole song renders**: 10,426 ticks — 2,085 rows of five
frames, the HVSC length of 208 seconds — **0 divergences** on §2's observable,
with the write lists agreeing per register value for value and in order,
`end.kind = horizon` and `loop` null against the tune's own certificate
([lft-quintessence.json](certificates/lft-quintessence.json)). **The seventh
family cost the universal player no line**: `universal.py` and `printer.py` are
exactly as the sixth family left them. **The score is compressed, and none of
that survives**: one LZ stream of 2,961 bytes expands to 7,579 token bytes in
three 256-byte ring buffers, with a copy-with-transpose primitive and packed
delays of up to sixteen rows, and §6's materialisation leaves **6,255 rows, every
one of `dur` 1** — no decompressor, no buffer, no delay token. **§2's dropped
voice order is load-bearing here**: this player runs a tokenizer pass over all
three voices and *then* its audio engine over all three, so its writes are
permuted between voices on 8,442 of the 10,426 ticks and identical inside every
one (§4.2).

Reproduce:

```
tools/trackerprog_blackbird.py $HVSC/MUSICIANS/L/Lft/Quintessence.sid \
    --source docs/certificates/lft-quintessence.json --certify --out out/blackbird-tp
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the layer
needed · 5 the prelude and the note row · 6 finding the data · 7 measurements ·
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

There is no `refusals` key because nothing refused. `identical_ticks` is the
1,984 on which the two write lists match byte for byte and in order; the other
8,442 are permuted between voices and never inside one, which §2 drops and §4.2
measures.

## 4. What the layer needed

### 4.1 A site's class is its envelope's; a byte is the chip's by its address

Blackbird's whole register economy is one index: `X ∈ {0, 7, 14}` reaches
`$12EE,X` (the state block), `($E0,X)` (the ring-buffer pointers) and `$D400,X`
(the chip). `regions` therefore unites the wave-mask cells with the SID into one
region of kind `io`, and `lower.cls` gave every access through that region the
class `io` — including `AND $12F3,X`, whose envelope is `[$12F3, $1301]` and lies
wholly in RAM, so `Machine.ioload` asked for a pinned input no trace ever recorded
and certification stopped at tick 0 with `{"trap": "input exhausted", "detail":
"$1301"}`. The tracer decides by the address (`if IO_LO <= addr <= IO_HI: chip
else RAM`) and `Machine.ioload` now tests it on both sides: outside the window a
load reads RAM and a store writes it, marking the byte known and adding it to the
footprint rather than logging a SID write. **A class is a property of a site's
envelope and a landing is a property of an address.** With that all 51 committed
certificates reproduce and *Quintessence* certifies over its whole 10,426 calls
at 0 divergences and 0 envelope traps.

### 4.2 The comparison drops the interleave between voices

§2 rule 1 keeps every `ctrl`/`AD`/`SR` write in tick order because the envelope
generator is edge-triggered — a fact about **one voice's** envelope generator, so
"the interleave between voices of one tick's writes" is on the certificate's
`dropped` list and both `certify.divergence` and `attest` split the edges per
voice before comparing. Blackbird is the family that exercises it: `prepare2`
writes `$D406`, `$D40D`, `$D414` for all three voices and *then* `everyframe`
writes each voice's frequency and control byte, so on a hard-restart tick the tune
writes `v1.sr v0.sr v2.ctrl v1.ctrl v0.ctrl` where the player writes `v2.ctrl
v1.sr v1.ctrl v0.sr v0.ctrl`. Per voice the two are identical; flattened they are
not, and all fourteen earlier builds re-certify with **not one losing an identical
tick**.

### 4.3 Five things the data says and the player did not have to learn

**The hard restart is a pipeline the schema already had.** Blackbird decodes a
row over four frames, one token class per frame, the second falling two rows'
worth of frames before the boundary — where `prepare2` happens to be, the
author's own comments naming the halves `Hard-restart 1` and `Hard-restart 2`. So
the prelude is one row (`sr ← 0`, `@wavemask ← $FE`) at `phase == 14`, and
`meta.stage` is `[{"ins": True}, {"sets": [["@willsound", {"payload":
"keys"}]]}]`, because the prelude reads the instrument the *next* row will play
and the tune reads that byte in the same pass; the staged cell makes the `early`
guard `phase == 14 and willsound != 0`, so a voice whose row is not due neither
reads a token nor restarts. No prelude diverges on **1,793** ticks, the
instrument taken at the boundary instead of staged on **1,434**.

**The tuning is quarter semitones.** `pitch` is 269 u16 rows indexed by a
quarter-semitone number, `note` is `pendnote × 4`, and a pitch program's offset
byte is added to it: `{"tuned": {"and": [{"add": [note, d]}, $1FF]}}`, the mask
being the 9-bit add the `ROR` makes. §3.2's projection of the two overlapped byte
arrays lands as written, with one thing only the certified program shows — the
two-entry sums **carry the low half's own carry-in**, `+ (t8 & 1)`, the "small
consistent error" the author's comment admits, whose dropping diverges on
**2,185** ticks. The horizon asks indices 36 through 304, so the four arms read
the array at 9 through 100 of its 111 entries and **nothing runs past the
tuning**: the first family since Commando needing no `beyond` record.

**A backward jump is no row.** `wavetable[y] >= $C0` is a relative backward jump,
resolved at the read and never itself a control byte, so it occupies no frame:
the object folds it into the row that lands on it, row `y` carrying the
*target's* control byte and the target's own advance. Rows stay one-to-one with
the frames they occupy and every declared row's `ctrl` constant is below `$C0`, a
testable invariant. The advance past a pulse parameter is `2 + the control byte's
own sign bit` — the `ASL A` that tested bit 6 left its bit 7 in the carry and the
`ADC #$02` took it — and over this horizon the cursor never exceeds 71, so the
term measures **0**: in the object because the program computes it, stated as
measuring nothing.

**A packed delay is storage.** A `$B8–$C7` token holds a voice for 16 − (b & $0F)
rows and `v_trtimer` counts it out — but Blackbird's held rows are not §3.6's
packed-rest `dur`, a row the fetch spends without applying: `execute` runs on
every one and does nothing only because `pendfx` and `pendins` are zero. Every
row cycle is one event of `dur` 1, and 6,255 rows carry what 7,579 token bytes
said.

**The row clock is the phase, and the phase is one byte.** `$E6` is the row timer
*in units of seven*, the four-phase selector, and the voice index the unpacker
tops up. Two of the three jobs are storage — the unpacker goes with the buffers,
and the phase selector is the clock's own `boundary`, `fetch` and `early` guards
reading `{"cell": "phase"}` — leaving a counter of `step −7` with one reset
clause. Lengthening the row by one frame diverges on **10,374** of 10,426 ticks.

## 5. The prelude and the note row

The prelude writes **no `ctrl` at all** — `sr ← 0` and `v_wavemask ← $FE`, the
gate going off because the audio engine ANDs that mask into every control byte it
writes for the next two frames — and the note row is **five writes in three
acts**: `sr ← $0F` (Hard-restart 2), then `ad ← 0` and `ctrl ← 1` (Hard-restart
1: the gate opened on a zero waveform with a zero envelope), then the
instrument's `ad` and `sr`. The order is `sr, ad, ctrl, ad, sr`, so `AD` and `SR`
each appear twice, which is why one `commit_order` cannot carry the row and the
tick is a sequence of acts (§3.1): the row program's three stream steps are the
three acts, and all `commit_order` has to say is that **`ad` comes before both of
the others**.

## 6. Finding the data

The tables are read from the image at the addresses
`out/bb-quintessence/tuneprog.md` reads them at — `$1331`/`$1391` the frequency
array, `$1400` the pulse map, `$14FF`…`$1529` the four 1-based instrument
columns, `$1537` the effect starts, `$155D` the pitch programs, `$1559` the
filter, `$15EC` the wave programs, and `$21F8`/`$21F9` the tempo command's own two
operand bytes, which the stream's end-of-stream jump makes it read twice.

The score is not on the image: it is one LZ stream consumed downwards from
`$221A`, interleaved across three voices in whatever order the unpacker's
lookahead test pulls it, tokenized over three frames. **Materialisation is
measured rather than re-implemented** — the tool runs the tune's own player once,
the same PcodeVM pass that produces the oracle's write lists, and reads each
voice's finished tokens out of `pendins`, `pendfx` and `pendnote` at the tick
between the last pass and the boundary that applies them, with `v_trtimer == $FF`
at the first pass saying whose row is due. The decompressor is provenance and the
rows are the score.

The reached-row sets are the one thing the tool re-derives, because a row the
horizon never steps on is a `trap` and not a row (§3.3): the walk steps both
cursors over the materialised rows with the row's own phasing — three frames on
the cursors the row before it left, then the boundary, then two more. The declared
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

Four of the zeroes are boundaries §2 and §3 already state, measured rather than
assumed: voice order is dropped, this tune's filter reads nothing a voice writes,
the two write-only streams cannot see each other, and the carry the `ADC #$02`
takes is provably zero while the cursor stays under 254. The fifth:
**`commit_order` here says only "`ad` first"**, because `sr` and `ctrl` never
share an act, so two of its six values render the tune and four do not. The
re-point poison is the object refusing rather than diverging, which is what §5's
asserted bound is for — a cursor walks into an offset byte whose sum with the
row's note is index 305, and the tuning has 269 rows from 36.

Print, architecture §6.2's six numbers plus `xz`: `trackerprog.md` is 6,911 lines, 48,584
tokens, 6,899 statements, 7 blocks, 12 header rows, 6,899 data rows and **5,860**
`xz -9e`, with the compressed object at 6,352 against a load band that compresses
to **3,772**; §9.1 of [prototype-trackerprog.md](prototype-trackerprog.md)
carries the current table, at **1.63×**. This is the exemplar that tests §9's
first claim hardest, because its author compressed the score himself, and it
fails it.

## 8. Boundaries

- **The syncpoint is an external input this tune never takes.** `$ED` is a
  host-writable shift register whose bit 0 stalls the sequencer until the demo
  shifts a bit out; the certified program marks that branch `untaken` over the
  whole horizon, so `$ED` is never read and no input is pinned.
- **The player is emitted per tune.** All 40 HVSC Blackbird tunes carry different
  player bytes — conditional assembly, table sizes, and the two instrument
  thresholds the exporter sorts the table for. The threshold is a **compare
  immediate of this build**, read from its own `CMP #imm` at `$105C` and `CPY
  #imm` at `$1218`/`$1233`, which the tool asserts agree. The object is one
  tune's; the *reading* is the family's.
- **The three out-of-band commands are two tempo writes and one stream jump.**
  Both tempo writes read the same two bytes, the stream's own end jumping back
  over them, and both write `$1C`/`$00`, so the reload is one constant over the
  horizon and the swing `EOR` is inert. A tune whose groove mask is non-zero needs
  the reset's second clause, which §3.6's row clock already has.
- **`end.kind = horizon`.** The music repeats — the stream's own end jumps back —
  but no state repeat is found over the 10,426 calls, so the source certificate's
  `period` is null and the score is materialised to the certified horizon rather
  than to a period. The terminator is Commando's, and so is the discipline of not
  guessing at it.
