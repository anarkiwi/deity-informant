# Prototype: trackerprog — a universal tracker representation

Design prototype, not yet a certified exemplar: the schema, the semantics, the
lift and the acceptance for a **trackerprog**, the layer above
[tuneprog-architecture.md](tuneprog-architecture.md). A tuneprog moves a tune
from opaque bytes to a certified per-tick *program*; a trackerprog moves the
music from player-specific code-plus-data to player-independent *data* — a pitch
table, instrument definitions and patterns that play pitches with instruments —
rendered by **one fixed universal player**. Effects are **bounded accumulators**.

Empirical ground: [playroutine-anatomy.md](playroutine-anatomy.md) §2, which
shows all nine playroutines are one object (STATE, TABLES, PLAY), and the
certified exemplars — GoatTracker 2
([prototype-goattracker.md](prototype-goattracker.md)), SID Wizard
([prototype-sidwizard.md](prototype-sidwizard.md)), JCH V20
([prototype-jch.md](prototype-jch.md)), Hubbard
([prototype-commando-floor.md](prototype-commando-floor.md)), defMON and Follin.
The survey fact that sizes the layer: voice-stride state appears in 91.6 % of
traced HVSC by weight (architecture §9.3), so the object this schema names is
the population's, not a family's.

Contents: 1 definition · 2 observable and certificate · 3 schema ·
4 the universal player · 5 effects as bounded accumulators · 6 the lift ·
7 tuneprog extensions · 8 refusals and boundaries · 9 acceptance · 10 open.

---

## 1. Definition

| term | meaning |
| --- | --- |
| **trackerprog** | one data object: `{meta, pitch, streams, accs, instruments, score, globals}` — no code, no bytecode escape, no per-family construct |
| **universal player** | one fixed tick procedure (§4) shared by every trackerprog; the only executable in the layer |
| **certified-equivalent (T)** | for every tick of the source tuneprog's certified horizon, the universal player's observable (§2) equals the tuneprog's |
| **accumulator** | a bounded state machine `Acc(target, width, delta, bound, policy, rate, scope)` (§5); the only per-tick mutation an effect may be |
| **stream** | a finite step table with holds and one jump/halt terminator (§3.3); the only sequencing an instrument or the score may be |
| **lift (T0–T3)** | certified tuneprog + S6 naming plane → trackerprog, fail-closed: what does not fit the schema is a `Refusal(reason, cell)`, never an approximation |

The layer invariant, restated as a test: two trackerprogs lifted from different
families must render on the same player with no family branch; the source family
survives only as provenance in `meta`. The schema may not add a construct for
one family — the repo rule (architecture §11: two families, or one plus a survey
count) applies to schema rows exactly as it applies to view heuristics.

The oracle chain extends by one link:
`sidplayfp ⇐ PcodeVM ⇐ tuneprog ⇐ trackerprog`.

---

## 2. The observable and the certificate

The tuneprog observable — the ordered interleaved SID write list — cannot be the
trackerprog's: *which register a player writes before which* is an idiom (the
GT2 ghost flush emits 25 writes low-to-high; Hubbard writes freq, ctrl, pw, AD,
SR per fetch; JCH's write-out is its own order), and a representation required to
reproduce it would carry the player back in. The anatomy states the license
([playroutine-anatomy.md](playroutine-anatomy.md) §1.3): *write order matters
only at the frame edge and for gate edges* — and the envelope registers share
the gate's sensitivity, the rate counter being state a write order can change.

The trackerprog observable, per tick:

1. per voice, the **ordered write list over the control and envelope
   registers** — `ctrl` (`$D404/$D40B/$D412`), `AD`, `SR`. The envelope
   generator is stateful and edge-triggered: gate edges are counted (1→0→1 and
   TEST 1→0 inside one tick are real events), and the rate counter's
   interaction with AD/SR is what the ADSR delay bug and every hard-restart
   prelude ride, so these registers may be written more than once per tick and
   **every write is kept, in tick order**;
2. the **16-bit registers once per tick**: `freq`, `pw` and `cutoff` reduce to
   the last value the tick left, one 16-bit value each. The oscillator and
   filter DACs are level-sensitive — only the frame-edge value is audible — and
   the two 8-bit-bus writes a pair takes are already a print convention in the
   tuneprog (`sid  16-bit registers written lo then hi`, architecture §6.1);
3. the remaining 8-bit registers (`res_route`, `mode_vol`) reduce the same way,
   last value per tick — Hubbard's drum-then-arpeggio double write of `$D401`
   is last-wins under rule 2, verified in the anatomy's SID log; a `mode_vol`
   carrying a sample stream is refused outright (§8), so no edge list is needed
   there.

Precedents: `grid.py` already frames the `sidplayfp` comparison per interrupt
period, and the Ghidra emulate oracle compares "the ordered sequence of SID
register changes, both sides reduced by the same rule" (architecture §5.4). The
reduction is a stated boundary, not a loss the certificate hides: cross-register
order inside a tick is dropped, and the certificate says so in `compared`.

`trackerprog.certificate.json`:

```jsonc
{
  "source": {"tune": "...", "certificate_digest": "..."},   // binds to the tuneprog cert
  "compared": ["ctrl/adsr write order", "freq/pw/cutoff tick values",
               "res_route/mode_vol tick values"],
  "ticks": 8236,                       // the whole certified horizon, never less
  "divergence": null,                  // else {tick, register, expected, got}
  "refusals": [],                      // non-empty ⇒ no trackerprog is emitted
  "loop": {"period": 6720, "first_repeat": 8235} | null     // inherited claim, re-checked
}
```

A `complete` tuneprog yields a trackerprog with a loop claim (the score closes);
a horizon tuneprog yields the horizon's score and no loop claim — the same
honesty split as `complete` vs `horizon` certificates.

---

## 3. The schema

Serialised as tagged JSON in the S4 style (`ir.enc` vocabulary): `$trackerprog
$pitch $stream $acc $ins $pat $ord $cmd`, dicts as `{"$dict": [[k, v], …]}`.

### 3.1 meta

Cadence (`cycles_per_tick`, `source`), SID model, subtune, the source tune and
family (provenance only), and the universal-player semantic version.

### 3.2 pitch

`pitch: [u16; N]` — the tune's own frequency table, bytes normative, plus
annotations the lift proves: tuning (`12-TET` where `recover._freq` names it),
base note, resolution. The note space is `0..N-1`, per tune: Blackbird's
quarter-semitone table is simply a 4×-resolution `N`; Hubbard's 96-entry PAL
table is `N = 96`. Every pitch elsewhere in the object is an **index** into this
table or a signed index offset — never a raw frequency, except through §5's
`tablestep`, which is *derived from* this table. Evidence that the table is
universal: every one of the nine players has one (anatomy §1.3, "frequency is a
table lookup"), lifted already as the `freq_table` role (GT2 `FREQ_LO/HI` 91
entries, JCH `rec4[96]` u16le, SW `FREQ $1859`).

### 3.3 streams

The one sequencing form. A stream is a finite table of steps:

```
Step = { op:   set(target, value)          // one register-shadow assignment
             | acc(acc_id)                 // run accumulator acc_id this segment
             | pitch(offset | absolute)    // note-space movement (arpeggio, chord)
       , hold: k ticks (k ≥ 1) }
Terminator = jump(row) | halt
```

A stream has a `rate` (steps per tick, default 1 — defMON cascades step up to
8×/frame under a CIA cadence, which is the entry's own tick, so rate stays 1
there; rate > 1 exists for a player stepping one table twice per entry).

What lands here, per family: GT2 wavetables (`T16F9` rows of wave/note with
`$FF` jump — the `timer_4` compare is the hold), GT2/SW pulse and filter and
speed tables, JCH's four column programs (`rec6` pulse `[init/keep, Δ,
dir|frames, next]`, `rec7` filter — each row one `acc` segment with `hold` and
an explicit `jump`), SW tempo programs, Galway FM/PM segments, defMON sidTAB
rows (variable-length register-column records with delay and jump — the stream
form at its most general), Walker LFO parameter sets, Blackbird wave/pitch
programs. The stream is the common grammar all of these already are:
*rows, holds, one jump*.

### 3.4 accumulators

Declared once in `accs`, referenced by streams, instruments and commands. The
full object is §5.

### 3.5 instruments

```
Ins = { adsr: (ad, sr)
      , gate: { timer: k            // gate-off k ticks before row end (GT2 gatetimer)
              , hard_restart: {early: 1|2, ad, sr, first_ctrl} | null }
      , streams: { wave: (stream, row), pulse: …, filter: …, pitch: … }  // any subset
      , accs: [acc_id, …] }         // the modulations armed at note-on
```

Hard restart is one fixed shape because every implementing family does the same
writes: k frames early gate-off with reset ADSR, TEST/first-wave at note-on —
JCH (2 early, `$09` on the note frame), GT2 (gatetimer early, firstwave with
TEST), SW (tick 0/1 HR ADSR, tick 2 TEST), defMON (a sidTAB row program doing
exactly those writes), Blackbird (free from its pipeline). Families without it
carry `null` (Hubbard, Galway, Follin, Walker — anatomy §1.3). A player-side
flag would be an idiom; a data-side prelude is the mechanism itself.

The nine-family "sound definition" row (anatomy §2) all reduces here: Hubbard's
8-byte SID image + fx bits = `adsr` + armed `accs`; GT2's 9 columns + pointers =
`adsr`, `gate`, four stream refs; defMON's "the sidTAB row *is* the instrument"
= an instrument that is only `streams`.

### 3.6 score

```
Pattern = rows of Event
Event   = { note: index | rest | hold | keyoff | keyon
          , ins:  instrument | none
          , cmd:  Cmd | none
          , dur:  ticks }
Cmd     = set_tempo(stream) | set_vol(v) | arm(acc_id, param) | disarm(acc_id)
        | porta(acc_id, target_note) | filter_set(...) | break | gate(mask)
Order   = per-voice sequence program over
          { play(pattern, transpose, repeat) | for(n){…} | call(seq) | ret
          | jump(row) | stop }        // bounded call depth, stated (Galway: 8)
```

The order grammar is a sequence *program*, not only a list, because two families'
scores are programs (Galway's call/jmp/for-next with an 8-deep stack, Follin's
byte streams with call/loop); a flat tracker orderlist (GT2
pattern/repeat/transpose, JCH `[transpose] pattern`, SW
pattern/transpose/vol/tempo) is the degenerate case. It stays data: no
conditionals, no arithmetic, statically bounded.

Commands are the universal set only. A family command that is not expressible as
one of these plus an accumulator arm is a refusal, not a new opcode.

### 3.7 globals

The filter as a global channel (cutoff streams and accumulators, resonance,
routing), master volume, tempo. Filter ownership — SW's owner voice, JCH's
"filter runs on track 0" — is last-writer over the global channel, which the
observable (§2) makes exact without an ownership construct. Keyboard tracking
(SW `CKBDTRK`: cutoff += FREQ-derived term) is the pitch-table term of §5's
`tablestep` applied to the cutoff target — the same construct vibrato needs, not
a schema row of its own.

---

## 4. The universal player

Normative semantics — anatomy §2's pseudocode made exact. One tick:

```
tick():
    tempo.step()                                  # a stream; row clock per §3.6
    for v in voices:                              # order immaterial: observable is per-register
        if row_boundary(v): sequencer_step(v)     # consume Event; note-on arms ins streams/accs;
                                                  # hard_restart prelude scheduled `early` ticks
                                                  # before the *next* row boundary
        for s in active_streams(v): s.step_if_hold_elapsed()
        for a in active_accs(v):    a.step_if_rate()          # §5 semantics
        commit(v):                                 # shadows -> register writes
            freq  = pitch[clamp(note + transpose + pitch_stream_offset)] + Σ freq-target accs
            pw    = pulse base + Σ pw-target accs   # freq/pw: one 16-bit write each per tick
            emit ctrl/AD/SR events **in order**     # gate mask, restart prelude, note-on:
                                                    # the ordered list §2 compares
    commit_global(): cutoff one 16-bit write (+ tablestep term), res/route, mode|vol
```

Everything a real player does beyond this — ghost register files and flush
loops, unrolled voices, `X = 7v` double-duty indices, SMC-patched dispatch,
1-based tables, position-independent relocation, stack tricks — is compilation,
already decompiled away by S4–S6, and must leave no residue in the data. That
list is exactly the "symptoms" tables of the three tracker prototypes: each row
there is a player idiom the tuneprog already erased; the trackerprog is the
claim that after erasing them, what remains fits this one procedure.

---

## 5. Effects as bounded accumulators

The formal object:

```
Acc = { target : freq | pw | cutoff | vol | note | wave-param
      , width  : 8 | 11 | 12 | 16                       # the value's modulus
      , delta  : const(k)                               # signed
             | tablestep(note_src, shift)               # (pitch[n+1] - pitch[n]) >> shift
      , bound  : [lo, hi]                               # inclusive; may be the width itself
      , policy : wrap | reflect | clamp(target) | halt | reload(v)
      , rate   : every k ticks (k ≥ 1)                  # pulsedelay-style dividers
      , phase  : (start, direction)
      , scope  : voice | instrument | global }
```

**Bounded** is the invariant, not a hint: `bound × policy` makes the reachable
value set finite and statically known; the trackerprog states each accumulator's
reachable interval and the renderer asserts it — the tuneprog's envelope
discipline (every indexed access bounded, trap outside) carried up one layer.
`wrap` at `width` is a bound too: Commando's per-voice pulse-width accumulators
— the writes that make both subtunes aperiodic (architecture §5.2) — are 16-bit
wrap accumulators, rendered exactly, aperiodicity included.

Every per-frame modulation in the nine-family anatomy row lands on one line of
the table:

| effect | Acc | evidence |
| --- | --- | --- |
| vibrato (triangle) | target freq, reflect in `[-depth, +depth]`, delta `tablestep(note, speed)` | GT2: `p_109E` steps `voice.b14A0` by 2 with the direction in bit 0, depth `T1851[y] & $7F`, step `(FREQ[n+1] - FREQ[n]) >> T1864[y]` (`p_12E5`'s shift loop); Hubbard `$51C1`'s `AND #$07 / EOR #$07` triangle, depth a shift of the semitone interval |
| tone portamento | target freq, clamp(pitch[target]), delta const | GT2 `p_10AB` case 3: the 16-bit compare chain against `FREQ[idx]`, snapping at `p_10F5`; Hubbard `portaval` bit 0 = direction, bits 1–6 = step |
| free slide / skydive | target freq, halt or wrap at width, delta const | JCH slide acc; Hubbard skydive (freq-hi ramp) |
| pulse sweep (bounce) | target pw, reflect in `[lo, hi]` (12-bit) | Hubbard: `pw += step` until `(pw >> 8) == $0E`, down until `$08`, `pulsedir` the phase, `pulsedelay` the rate — and the state lives in the *instrument* record, two voices sharing it: `scope = instrument`, not a special case |
| pulse run (unbounded) | target pw, wrap at width | Commando fx bit 3 (8-bit add, carry inherited); the aperiodic observable above |
| filter sweep | target cutoff (11-bit), reflect/clamp | JCH `rec7` segments; SW's `INC $15DD` sweep counter; defMON filter acc |
| arpeggio / chord | target note, wrap over an offset list (a `pitch` stream) | Hubbard octave arp (`counter & 1 ? +12 : 0` — a 2-step stream), Galway FM arpeggio mode, SW chords, GT2 wavetable note column |
| LFOs generally | the schema verbatim | Walker: four identical triangle/one-shot modulators per voice (pitch, pulse, pitch-2, filter) — triangle = reflect, one-shot = halt |
| tremolo | target vol or gate mask, reflect / stream | Walker gate-toggle tremolo |
| keyboard tracking | `tablestep` term on cutoff | SW CKBDTRK (§3.7) |

Piecewise envelopes (JCH/Galway two-segment ramps) are streams of `acc`
segments (§3.3): the stream sequences, the accumulator moves. Nothing else
moves a shadow between rows — that is the whole discipline.

---

## 6. The lift, T0–T3

Input: a certified tuneprog — `tuneprog.S4.json` (the program),
`tuneprog.S6.json` (the naming plane: roles `freq_table`, `cursor`, `timer`,
`acc`, `sid_image`, `voice_map`; views; u16 pairs), `certificate.json`. The lift
consumes the *certified* object, never the trace or the binary: family
knowledge may steer extraction heuristics but can never reach the output, which
renders on §4 alone.

| stage | in | out | mechanism |
| --- | --- | --- | --- |
| **T0 channels** | S4 IR + names | per-register provenance: for each SID register, the expression over named cells that produced each write | slice `irwalk.accessors` stores with `cls = io` back through the tick's dataflow; the print already spells these (`sid[v].freq_lo = …`), T0 makes them machine-readable |
| **T1 accumulators** | T0 + cell histories (§7) | `Acc` set | a `state` cell whose update is `±delta` guarded by compares against constants or cells → `(delta, bound, policy)`; `ranges.py` supplies the interval, `gated.py`'s borrow-diamond reading the reflect phase; direction bits (GT2 bit 0, Hubbard `pulsedir`) are `phase`, rate dividers (`pulsedelay`) are `rate` |
| **T2 grammars** | cursor roles + histories | streams, patterns, orderlists, pitch table | a `cursor`'s observed successor relation (step +1 runs, jump targets — the `$FF`-terminator reloads the exemplars all show) delimits its table's rows and loop row; the two-level cursor nest (row cursor over a pattern table indexed through an orderlist cursor) is the score; `freq_table` regions are `pitch` |
| **T3 emit + certify** | all | `trackerprog.json`, `trackerprog.md`, `trackerprog.certificate.json` | render on the universal player tick-for-tick against the tuneprog's own render over the whole certified horizon, §2 observable; any residue → `Refusal`, nothing emitted |

T2's materialisation rule: the trackerprog represents the score the trace
played. Storage idioms of the score — Blackbird's LZ stream and ring buffers,
packed rests, 1-based columns, interleaving — are dropped by materialising the
decoded rows over the certified horizon; the period witness bounds the
materialisation for a `complete` source, the horizon for the rest (§2).

---

## 7. What tuneprog needs

The efficient answer is: almost nothing in the front end. The trackerprog is a
consumer of the certified artefacts; the IR, the tracer and S8 do not change.
Four extensions, backlog-style:

| item | mechanism | owner | size |
|---|---|---|---|
| the grid as a first-class comparison | the §2 reduction — ordered ctrl/AD/SR lists, 16-bit tick values —, shared by S8-style differential runs — `grid.py` already frames the `sidplayfp` CSV this way; factor the reduction out of the oracle so `verify` and T3 compare one thing | grid, verify | small |
| cell histories without touching S1 | replay the *certified program* over the horizon on `interp`, recording the value sequence of each named `state` cell — no tracer change, no new log; the program is certified-equivalent, so the histories are the trace's | new `history.py` over interp | small |
| per-register provenance export (T0) | the dataflow slice from each `io` store to named cells, serialised beside `tuneprog.S6.json` — the facts pass the print already computes, exported | facts, irwalk | medium |
| accumulator recognition (T1) | the `(delta, bound, policy)` classifier over update shapes + `ranges` intervals | new `accum.py` (S6 family) | medium |

Everything else — streams, score, emit, the universal player, T3 verification —
is the new `trackerprog/` package beside `tuneprog/`, under the same rules
(≤ 500 lines per module, hermetic snippet tests, the certificate as acceptance).

## 8. Refusals and boundaries

Fail-closed, diagnosed, in the tuneprog refusal style — a trackerprog with a
residue is not emitted:

| reason | when |
| --- | --- |
| `sample stream` | a CIA #2 NMI sample mixer or `$D418` nibble stream (the *Easy Does It* mixer): digis are not a score |
| `external input` | the tuneprog's play phase consumes pinned inputs beyond acks (SFX arbitration, raster-reactive tunes): the score is not closed |
| `unclassified update` | a state cell reaching a SID register whose update T1 cannot bound — the accumulator invariant is the claim, so an unbounded or data-dependent-in-an-unmodelled-way update refuses |
| `score not cursor-shaped` | a pattern fetch T2 cannot express as the cursor grammar (a genuinely computed score) |
| `command residue` | a pattern command not expressible in §3.6's universal set plus an `arm` |

Boundaries stated, not hidden: cross-register intra-tick write order (§2, with
its license); cycle positions inside a tick (already a tuneprog boundary,
architecture §8.3); the trackerprog is *a* preimage — many trackerprogs render
the same grid, and the lift picks the one the source's own tables induce, so
round-tripping to the source *format* (a `.sng`, an `.swm`) is a separate,
family-specific exporter that consumes a trackerprog and is out of this layer by
construction.

## 9. Acceptance

Prototype exemplars, in order: **GT2 ×2, JCH ×2, SW ×2** (the tracker end — all
six `complete`), then **Commando ×2** (effects-rich, non-tracker, aperiodic
observable), then **Follin** (score-as-program). Per exemplar:

1. `trackerprog.certificate.json`: 0 divergences over the whole certified
   horizon on the §2 observable, loop claim re-verified for `complete` sources;
2. every refusal named with its cell — no partial emit;
3. the print measured like §6.2: pattern rows, instrument count, stream rows,
   accumulator count, refused cells, and `xz -9e` of `trackerprog.md` against
   the source `tuneprog.md`'s — the layer's claim is that the score compresses
   *better* than the program that played it;
4. recert untouched: 51/51, no tuneprog artefact moves.

The genericity gate: the six tracker exemplars must lift with zero
family-conditioned code in `trackerprog/`, checked the way the tuneprog checks
it — the same modules, hermetic snippet tests per mechanism, and each schema
row's two-family evidence recorded in this document.

## 10. Open

- **Multispeed scaling.** A 2×–8× CIA cadence makes the tick the entry's; whether
  `rate` on streams suffices for a player running its sequencer at frame rate
  under a faster entry is to be measured on a multispeed exemplar (SW 1.9
  carries an unused multispeed entry; a used one is needed).
- **Instrument-scoped accumulator sharing** (Hubbard's mutated instrument
  record) is in the schema (`scope`); whether any family needs `global` scope
  beyond the filter is a survey question.
- **The second entry.** A tune whose NMI is a second *musical* entry (not a
  mixer) has two tick clocks; the schema has one. Refuse until an exemplar
  demands otherwise.
- **Note-space clamping** at the pitch-table edge: Hubbard's benign note-95
  overrun reads two bytes past the table — the lift materialises the read
  values as pitch entries (the table is the trace's reach, not the family's
  declared size).
