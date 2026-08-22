# Prototype record — the second interrupt: a CIA #2 NMI beside the play routine

The last design gap ([tuneprog-decompiler-design.md](tuneprog-decompiler-design.md)
§10, [tuneprog-plan.md](tuneprog-plan.md) §8 item 3). Until this stage nothing
modelled an NMI: `nmi_gate` only refused one. What follows is the record — the
population measured rather than guessed, the chip model the tracer now carries,
the interleaving measured against `sidplayfp`, the program model chosen and what
its certificate claims, and what remains.

**Verdict.** A CIA #2 NMI is now the schedule's second entry. The tracer takes it
at the instruction boundary the chip's line asserts at — inside a tick and in the
host's idle time between two — and records the preemption schedule as data. The
verifier replays that schedule at *store* granularity, which is exact for
everything either entry reads of the other. JCH's *Easy Does It*, the tune the
anatomy documents and the plan refused by design, **certifies: 1,799 ticks,
199,514 NMIs, 0 divergences, 0 envelope traps**. Against `sidplayfp` over 1,500
frames the play routine's registers `$D400-$D417` agree **frame for frame and
write for write in order**; `$D418`, which only the mixer writes, differs in 10 %
of frames by one sample step — the residual is the missing VIC-DMA model, exactly
as the plan's Q3 row predicted.

Contents

1. The population, measured
2. What the chip does, and what the tracer now models
3. The interleaving, against the oracle
4. The program model
5. The certificate
6. The class at a 30 s horizon
7. What is proven, and what remains

---

## 1. The population, measured

`tools/tuneprog_nmi.py scan` runs each tune's own init, asks
`cia.CIA`/`nmi.entry` whether CIA #2 can dispatch, and — where it can —
traces 200 ticks of both entries and reports what the handler did. `report`
folds the rows into the tables below; rates are raw over the 7,023-tune
stratified sample and re-weighted to HVSC by SIDId family size, the way
[survey-tuneprog.md](survey-tuneprog.md) does.

| class | tunes | raw | HVSC-weighted |
|---|---|---|---|
| `no nmi` | 6076 / 7023 | 86.5 % | 95.4 % |
| `init runaway` | 520 / 7023 | 7.4 % | 1.6 % |
| `no entry` | 174 / 7023 | 2.5 % | 1.5 % |
| `sample player, silent play` | 67 / 7023 | 1.0 % | 0.2 % |
| `sample mixer ($D418 only)` | 57 / 7023 | 0.8 % | 1.0 % |
| `no SID write` | 49 / 7023 | 0.7 % | 0.1 % |
| `no entry (nmi armed)` | 43 / 7023 | 0.6 % | 0.1 % |
| `RuntimeError` | 14 / 7023 | 0.2 % | 0.1 % |
| `handler writes the register file` | 8 / 7023 | 0.1 % | 0.0 % |
| `vector banked out` | 6 / 7023 | 0.1 % | 0.0 % |
| `second interrupt source armed` | 6 / 7023 | 0.1 % | 0.0 % |
| `nmi vector banked out` | 3 / 7023 | 0.0 % | 0.0 % |

| property of the 181 traced schedules | tunes | raw | HVSC-weighted |
|---|---|---|---|
| Timer A | 128 / 181 | 70.7 % | 91.7 % |
| Timer B | 53 / 181 | 29.3 % | 8.3 % |
| vector `$0318` (KERNAL mapped) | 84 / 181 | 46.4 % | 85.3 % |
| vector `$FFFA` (KERNAL banked out) | 97 / 181 | 53.6 % | 14.7 % |
| handler acknowledges the ICR | 170 / 181 | 93.9 % | 98.5 % |
| handler rewrites a CIA #2 register | 34 / 181 | 18.8 % | 4.5 % |
| handler self-modifies | 120 / 181 | 66.3 % | 35.1 % |
| handler shares code with the play routine | 0 / 181 | 0.0 % | 0.0 % |
| shared RAM: NMI writes, play reads | 72 / 181 | 39.8 % | 66.5 % |
| shared RAM: play writes, NMI reads | 76 / 181 | 42.0 % | 67.7 % |
| shared RAM: both write | 59 / 181 | 32.6 % | 24.9 % |
| the play routine writes no SID register | 99 / 181 | 54.7 % | 20.0 % |
| more than one NMI per tick | 151 / 181 | 83.4 % | 79.0 % |
| every NMI ran in the idle time | 17 / 181 | 9.4 % | 19.3 % |
| the RTI frames balance | 177 / 181 | 97.8 % | 99.4 % |

**What the table says.** The tunes with a *dispatching* NMI beside a play entry
are **181 of 7,023 (2.6 % raw, 1.3 % weighted)**, in four classes, and the
sample mixer JCH's engine exemplifies is the largest by weight. Two more rows are
NMI populations the model does not address:

* **`no entry (nmi armed)` — 43 tunes (0.1 % weighted).** `play == 0`, no IRQ
  vector the port dispatches, and an armed CIA #2: the NMI is the tune's *only*
  schedule, not a second one. That is a single-entry program whose cadence is a
  CIA #2 timer, and nothing here builds it.
* **`init runaway` — 520 tunes (1.6 % weighted)** and **`no entry` — 174 (1.5 %)**
  are the pre-existing refusals, unrelated to the NMI, that the gate never
  reaches.

Against the [survey §5b](survey-tuneprog.md) figure of *311 armed / 1.8 %
weighted*: that count was taken with every gate bypassed, so it included tunes
that refuse for reasons an NMI model cannot fix. The addressable class is 181 +
43, and 181 of those have both entries.

Two facts from the property table drive the whole design:

* **`handler shares code with the play routine`: 0 of 181.** The two entries are
  disjoint bodies. That is what lets the pinned input stream partition by entry
  (§4) and what makes two procedures the right program shape.
* **`shared RAM` in both directions is common** (67 % weighted each way). The
  entries *do* share state; only the code is disjoint. So the schedule has to
  place a preemption finely enough that the shared state is right, which §4 is
  about.

---

## 2. What the chip does, and what the tracer now models

CIA #2's interrupt line is the 6510's NMI. The line is *edge*-triggered at the
CPU and *level*-held at the chip: a source's event latches its flag bit, the line
asserts when a latched flag meets the ICR mask, and it stays asserted until a
read of `$DD0D` clears the flags. So a handler that acknowledges gets one NMI per
underflow and one that does not gets exactly one, ever. `cia.CIA` (split out of `machine.py`, which the two together outgrew) carries
that (`fl`, `ir`, `edge_at`, `raise_line`), plus three things the old model did
not have and this population needs:

| what | why it is here | population |
|---|---|---|
| **Timer B**, counting φ2 or Timer A's underflows (CRB bits 5-6) | 29 % of the class dispatches on Timer B | 53 of 181 |
| **one-shot mode** (CRx bit 3), which halts the timer after one underflow | a handler that re-arms per sample | in the `cra`/`crb` census |
| a **latch write to a running timer lands at the pending underflow**, it does not reload the counter | JCH's mixer rewrites `$DD04`/`$DD05` inside the handler every NMI to set the next sample's rate; reloading instead made the rate 26 % too slow and the write order wrong | every rate-controlled mixer |

The last one is the single correction that moved the measurement most: with the
counter reloaded on the write, *Easy Does It* ran 81 NMIs per frame instead of
101.8 = 19,656/193, and the frame grid differed from the oracle from frame 584
on. With the chip's rule the gaps between two NMIs are exactly the timer period
in 91 % of cases and a multiple of it in the rest.

`nmi.py` is the schedule: which vector the port dispatches the line through
(`$0318` with the KERNAL mapped — `$FFFA` is ROM and reaches `$FE43`, whose
`JMP ($0318)` is the dispatch; the RAM `$FFFA` with it banked out), the entry
that names it, and the refusals — `second interrupt source armed` now means only
*a source this model has no schedule for* (TOD alarm, serial, FLAG, a CNT-driven
timer: 6 tunes of 7,023), and `nmi vector banked out` a line no vector answers.

Neither dispatch path saves a register, so the NMI's entry frame is the status
byte alone (`machine.entry_frame`), against the CINV entry's status + A/X/Y.

---

## 3. The interleaving, against the oracle

`Tracer` checks one integer per instruction — the cycle CIA #2's line next
asserts — and takes the NMI at that boundary: inside the play routine, and in the
host's idle time after it returns, which is where most of them are (the mixer
runs at ~5 kHz against a 50 Hz tick). Nesting is modelled: a handler that
acknowledges early can be preempted inside itself. The cost on a tune with no
second schedule is **1-3 %** of the tracer (494/548/539/499/494 k instructions/s
before, 487/554/534/493/480 k after, on the five tunes of the plan's §8 item 7
table), and the `Trace` of such a tune is byte-identical — the pinned digests of
`tests/tuneprog/test_trace_identity.py` are unmoved.

One correction the oracle forced: the tick clock is the interrupt's own grid,
`cycles_init + k * cycles_per_tick`, not "wherever the last tick ended". A tick
an NMI made overrun used to move the origin of every tick after it, and the drift
accumulated to a whole frame after ~580 frames.

| tune | class | NMIs / 1,500 frames | frames differing | which register |
|---|---|---:|---:|---|
| JCH *Easy Does It* | sample mixer (`$D418` only) | 169,419 | **150** (10.0 %) | `$D418` only |
| Sphere/Chromance *Digi Zak 2* | a vector the handlers repoint, Timer B | 224,586 | **318** (21.2 %) | `$D418` only |
| Mixer *Iisibiisi* | sample mixer (`$D418` only) | 233,380 | **812** (54.1 %) | `$D418` only |

Every one of `$D400-$D417` -- everything the play routine writes -- is **0 frames
differing on all three**.

**What the tracer gets right.** Every register the play routine writes, on every
frame, in the order it wrote them. Filtering both sides to the writes that
*change* a register (which is what a `sidtrace` CSV records), *Easy Does It*'s
per-frame changed-write list ignoring `$D418` is **identical in 1,500 of 1,500
frames**.

**What it does not.** The exact cycle each NMI lands on. Compared with the
oracle's own `since_nmi` column, our NMI instants sit **+17 to +25 cycles** from
the oracle's most of the time, with a tail to ±80; only 936 of 21,149 are within
8 cycles. The cause is the one the plan's Q3 row names: the tracer has no VIC
model, so a CPU stalled by a badline takes the NMI later than we do, and at a
193-cycle sample period a few tens of cycles decide *which* sample nibble is the
frame's last `$D418` write. That shows up as 10 % / 21 % / 54 % of frames
differing in `$D418` alone on the three tunes, and as 344 of 1,500 frames whose
*ordered* changed-write list places a `$D418` write between two different play
writes. A raster model would be the fix; design §12 keeps it out of scope, and
this is the measurement that says what it would buy.

`grid.sidtrace_clock` had to be relaxed to reach two of the three: it refused a
CSV in which any write is more than one period after its interrupt raise, which
is exactly what a second entry writing while the first is idle looks like. The
period check (every gap a whole multiple of one period) still refuses a
reprogrammed or split clock, and three more tunes of the class are refused by it
— a `grid` row that was already backlog.

---

## 4. The program model

The design's question is what the certified program *is* when two entries share
memory. The step-1 data answers most of it:

* the entries **share no code** (0 of 181), so they are two procedures, named
  `tick` and `nmi` by their role in the schedule (a vector the handlers repoint
  gives `nmi0`, `nmi1`, ... — one entry per address it took);
* they **do share RAM**, in both directions, so the schedule must place a
  preemption where the shared state is what the handler really saw.

The model built is the plan's option **(a)**, *call granularity with a recorded
schedule*, at a granularity the data chose:

**The trace records the schedule as data.** Per NMI: the tick, the instruction
index within it, the cycle, the handler address, **the number of stores the tick
had made**, the stack pointer, the pushed status, the interrupted pc and A/X/Y.

**The verifier replays it at store granularity.** An `NmiMachine` makes every
store outside the stack page a preemption point; between two stores nothing
either entry reads of the other can move, so the handler's view of shared RAM is
*exact*, not approximate. This is what option **(b)** — handshake-proven
independence — would have had to establish per tune, obtained instead as a
property of the replay and checked by the write list on every tick.

**What is pinned rather than computed.** The interrupted registers (A/X/Y, the
status, the stack pointer, the return address): between two stores those *can*
move, and they are the second entry's live-in. The design already pins values a
program cannot compute; these are of that kind and are counted in
`inputs_pinned`. Everything the handler reads from memory is computed.

**The two input streams.** Because the entries share no code, the pinned input
stream partitions by site: each entry reads its own in its own order, and the
interleaving between them cannot break either. Without that split, 88 of 117
divergences in the first class-wide run were `input mismatch` — the tick's
`$D019` acknowledge and the handler's `$DD0D` acknowledge racing in one stream.

**The cost on a program with one entry is zero, not small.** `emit_python` emits
the preemption point only when the program has an NMI entry, so a single-entry
program's generated `tuneprog.py` is byte-identical (sha1 of the emitted text
unchanged on Commando, *Automatas* and *Do It Again*), it runs on the plain
`Machine`, and the verifier's throughput is unmoved (17,955 / 14,703 / 9,975
calls/s before, 19,266 / 14,444 / 9,883 after — noise). `tools/tuneprog_recert.py`
reproduces **51/51 with 0 fields moved** -- the 50 that were there, and this one.

The certificate says what it proved: `compared` gains `"nmi preemption
schedule"`, a `schedule` block names both entries, and the subtune carries
`nmis` and `nmi_entries`. The claim is *the write list under the traced
interleaving*, with the preemption points recorded and the second entry's
live-in registers pinned.

---

## 5. The certificate

`docs/certificates/jch-easy-does-it.json` — JCH's *Easy Does It*
([playroutine-anatomy.md](playroutine-anatomy.md) §3.5), the sample build the
plan refused by design:

```
tune       Easy_Does_It.sid, song 1, 1,799 ticks (35.9 s of music)
schedule   irq  $3FE0 every 19,656 cycles (pal_video)
           nmi  $40E9 every 193 cycles (cia2_timer_a), 199,514 preemptions
compared   init writes, tick sid writes, tick schedule effects, nmi preemption schedule
program    5 procedures (init, tick, nmi, two subroutines), 211 blocks, 669 statements,
           107 regions; stack residual, depth 8, held by nmi
result     0 divergences, 0 envelope traps, 206,710 inputs pinned, no state repeat at 30 s
```

`nmi` is JCH's mixer at `$40E9`: `$D418 = voltab[volrow | nibble] | filtertype`
every 193 cycles, the sample pointer at `$FD`/`$FE`, `$DD04`/`$DD05` rewritten
for the next sample's rate under the play routine's lock, and A/Y saved into its
own `LDA #`/`LDY #` operands instead of the stack -- all of it decompiled, none
of it special-cased. `tick` is the plain NewPlayer V20 engine two other
certificates already hold, with the `CPX #$03` track-4 branches live.

It is `complete: false`: a sample player consumes its sample stream, so there is
no state repeat to find at any practical horizon. That is the aperiodic verdict
of [tuneprog-plan.md](tuneprog-plan.md) L6, not a gap in this stage.

---

## 6. The class at a 30 s horizon

The 195 tunes with both entries and a play entry the port dispatches, through
the whole pipeline at 30 s (`tools/survey/tuneprog_sweep.py --only`), 84 CPU-min:

| outcome | tunes | raw | HVSC-weighted |
|---|---|---|---|
| `certified` | 134 / 195 | 68.7 % | 69.7 % |
| `diverged` | 41 / 195 | 21.0 % | 18.2 % |
| `crashed` | 11 / 195 | 5.6 % | 11.7 % |
| `refused` | 9 / 195 | 4.6 % | 0.4 % |

| class | tunes | certified | diverged | refused | crashed |
|---|---|---|---|---|---|
| `sample player, silent play` | 67 | 44 | 23 | 0 | 0 |
| `sample mixer ($D418 only)` | 57 | 47 | 10 | 0 | 0 |
| `no SID write` | 49 | 39 | 6 | 4 | 0 |
| a `JAM` the classifier already met | 11 | 0 | 0 | 0 | 11 |
| `handler writes the register file` | 8 | 4 | 2 | 2 | 0 |
| `nmi vector banked out` | 3 | 0 | 0 | 3 | 0 |

**134 certified, 224,514 ticks, 2 complete.** What is left, by first cause:
`state hash` 13, `entry register` 9, `sid` 6, `io` 6, `input mismatch` 3, and one
each of `switch`, `unreached`, `input exhausted`, `brk`; the crashes are 11
`JAM`s in the tracer (a handler that runs off into RAM), 6 `recursion` and 4
`KeyError` in `graph.preds_of`. 24 of the 41 divergences are past tick 5, so
these are not first-tick modelling gaps like the campaign's (survey §4) -- they
are the schedule drifting from the machine's, which is the same residual the
oracle measures in §3.

The one-line comparison: **before this stage all 195 refused with `second
interrupt source armed`.** The first run of this sweep, before the pinned input
stream was partitioned by entry (§4), certified 58 and lost 88 tunes to
`input mismatch` alone.

---

## 7. What is proven, and what remains

**Proven.** A CIA #2 NMI is a modelled entry, not a refusal. One tune of the
largest class certifies over 1,799 ticks and 199,514 preemptions with the
schedule in the certificate. The chip model is exercised by 25 hermetic tests
(the edge, the acknowledge, one-shot, Timer B in both count modes, the latch
rule, both vectors, the frame contract, the idle-time NMIs, a repointing vector)
and the three executors — tracer, interpreter, generated Python — agree over the
interleaving on the hermetic image. Recert 51/51, trace byte-identity intact,
zero cost on every program with one entry.

**Remains** (plan §5 rows):

* the NMI instant is up to ~80 cycles early without a VIC-DMA model, which is the
  whole of the residual `$D418` disagreement;
* the 43 tunes whose NMI is the *only* schedule need the NMI as the tick entry,
  not as a second one;
* `grid.sidtrace_clock` still refuses a CSV whose raises do not agree on one
  period, which is three tunes of this class and the reprogrammed-clock row;
* a moving NMI vector becomes k entries, which is right for a two-phase handler
  chain and would be wrong for a genuinely computed vector — nothing bounds k;
* the horizon `--seconds` computes is taken from the pre-settle cadence, so a
  tune whose tick period settles later certifies a little past the horizon asked
  for (*Easy Does It*: 1,799 ticks = 35.9 s for `--seconds 30`).
