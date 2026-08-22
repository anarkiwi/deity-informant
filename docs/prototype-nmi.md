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
verifier replays that schedule at *store* granularity and **checks** the two
properties that replay rests on: that no load of the interrupted routine inside an
open preemption window reads a cell the handler stored in that window
(`schedule not store-separable`), and that a handler's `RTI` returns the A/X/Y it
interrupted (`nmi clobbers registers`) — §4. JCH's *Easy Does It*, the tune the
anatomy documents and the plan refused by design, **certifies: 1,799 ticks,
199,514 NMIs, 0 divergences, 0 envelope traps**.

**The oracle gate did not fire.** Against `sidplayfp` over 1,500 frames on three
tunes of three classes, `$D400-$D417` — everything the play routine writes — is
**0 frames differing of 1,500 on all three**, and the changed-write list is
identical *in order* in 1,500 of 1,500. `$D418`, which only the mixer writes,
differs in **10 / 21 / 54 %** of frames, and in **344 of *Easy Does It*'s 1,500
frames** a `$D418` write falls between two different play writes than the
hardware puts it between. The gate this stage was briefed with — a disagreement
in what the play routine writes, or in the order it writes it — did not fire on
any of the three. So the certificate proves **the write list under the traced
interleaving**, with store separability and register preservation checked and the
second entry's live-in registers replayed from the schedule; it does **not** prove
where the hardware places a `$D418` write, which §3 measures and §7 keeps as a
row.

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
[survey-tuneprog.md](survey-tuneprog.md) does. The tool takes the sweep's own
budget/resume contract and its helpers (`--timeout` 60 s a tune, append to
`--out`, exit 2 while work is left), and dispatches one tune per worker: at
`chunksize=4` a budget cut discarded up to four tunes per worker with the pool,
so a chunk wrote 9 rows where it now writes 1,087.

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

> **Re-scan (2026-08-22), after the two checked properties of §4.** The same
> command over the same 7,023. The class is still 195 tunes and the same 195
> tunes; two classes are new and one boundary moved:
>
> | class | before | after |
> |---|---:|---:|
> | `sample player, silent play` | 67 | 90 |
> | `sample mixer ($D418 only)` | 57 | 65 |
> | `no SID write` | 49 | 8 |
> | `nmi clobbers registers` | — | 8 |
> | `schedule not store-separable` | — | 5 |
> | `RuntimeError` | 14 | 11 |
> | `no nmi` | 6076 | 6075 (1 `wall timeout`) |
>
> 13 tunes move into the two new refusal classes (8 from
> `sample player, silent play`, 3 from `RuntimeError`/`sample mixer`, 1 from
> `no SID write`, 1 from a crash), and **41 of the 49 `no SID write` tunes move
> into the two sample classes**: the same tunes, with the handler's body traced
> where it was not before (*Dune_Cover*, the three *Comer* mixers and their
> shape: `handler_pcs` 4 → 62, `handler_ram_reads` 1 → 4,347, `handler_sid`
> `[]` → `$D418`), at an unchanged NMI count and cadence. The cause is not
> diagnosed here; the only chip-model change between the two scans is the
> conservative `cia.fired`/`_settle` gate being dropped for the chip's own
> `edge_at`. The property table below and §6's class column are from the first
> scan; §6's outcome table is from the re-run.

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

**What the table says.** **195 tunes of 7,023 have a dispatching CIA #2 NMI
beside a play entry the port dispatches** — the addressable class, and the
manifest §6 runs. **181 of them classify into the four schedule classes** (2.6 %
raw, 1.3 % weighted); the other 14 the 200-tick classifier downgraded before it
got that far — 11 `RuntimeError` (a `JAM` in the handler's own trace) and 3
`nmi vector banked out`. The two numbers are used that way throughout: 195 is the
class, 181 is the part of it with a classified schedule, and the property table
below is over the 181. The sample mixer JCH's engine exemplifies is the largest
by weight. Two more rows are NMI populations the model does not address:

* **`no entry (nmi armed)` — 43 tunes (0.1 % weighted).** `play == 0`, no IRQ
  vector the port dispatches, and an armed CIA #2: the NMI is the tune's *only*
  schedule, not a second one. That is a single-entry program whose cadence is a
  CIA #2 timer, and nothing here builds it.
* **`init runaway` — 520 tunes (1.6 % weighted)** and **`no entry` — 174 (1.5 %)**
  are the pre-existing refusals, unrelated to the NMI, that the gate never
  reaches.

Against the [survey §5b](survey-tuneprog.md) figure of *311 armed / 1.8 %
weighted*: that was `machine.nmi_gate`'s verdict over **the 547 tunes the campaign
had already refused** `second interrupt source armed`, each driven past the
pipeline's refusal order to the gate. It was neither a scan of the sample nor
"every gate bypassed" — §5b names 29 of the 311 as tunes that refuse first with
the `no entry` or `vector banked out` they would have had anyway. This scan is the
whole 7,023 under the exact chip model, so the two reconcile rather than
partition:

| the 311 armed (survey §5b) | tunes |
|---|---|
| a dispatching NMI beside a play entry — the class of this record | 181 |
| the NMI as the tune's *only* schedule (`no entry (nmi armed)`) | 43 |
| the remainder: armed, and refused before or beside the chip — `no entry`, `init runaway`, a CIA #2 source with no schedule (6), a line no vector answers (3) | 87 |

The last row is arithmetic, not a measured set: **the 311 is no longer
reproducible from this tree**, because `nmi_gate` was deleted when the exact model
replaced it. 181 and 43 are the numbers that stand.

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
counter reloaded on the write, *Easy Does It* ran 81 NMIs per frame, and the frame
grid differed from the oracle from frame 584 on. With the chip's rule it runs
**110.9 NMIs per tick** (199,514 over the certificate's 1,799) and the oracle
counts **112.9 per frame** over 1,500. 19,656/193 = 101.8 is neither: 193 cycles
is the latch the handler starts from, and it reprograms the latch per sample, so
the period moves within the run. The gaps between two NMIs are exactly the timer's
*current* period in 91 % of cases and a multiple of it in the rest.

`nmi.py` is the schedule: which vector the port dispatches the line through
(`$0318` with the KERNAL mapped — `$FFFA` is ROM and reaches `$FE43`, whose
`JMP ($0318)` is the dispatch; the RAM `$FFFA` with it banked out), the entry
that names it, and the refusals — `second interrupt source armed` now means only
*a source this model has no schedule for* (TOD alarm, serial, FLAG, a CNT-driven
timer: 6 tunes of 7,023), and `nmi vector banked out` a line no vector answers.

Neither dispatch path saves a register, so the NMI's entry frame is the status
byte alone (`machine.entry_frame`), against the CINV entry's status + A/X/Y. The
two paths do not cost the same, though: the 6510 spends `nmi.DISPATCH` = 7 cycles
taking the line, and the KERNAL path spends `nmi.KERNAL_STUB` = 7 more in
`$FE43`'s own `SEI` (2) and `JMP ($0318)` (5) before the handler's first
instruction. The port decides which, so the cycle count is the entry's `kernal`
flag by construction — it is in the certificate as `schedule[1].kernal` — and §3
measures what modelling it bought.

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

An NMI the host takes in the idle time interrupts the player's own wait loop, so
the return address it pushes is init's `JMP *` where the tune has one, and
`nmi.IDLE_PC = 0x0000` only where it does not — the documented convention for a
program with no idle pc of its own. The `JMP *` itself lowers to a `Return`, and
only inside the init procedure: any other procedure falling onto that address is
a path no execution took, which a negative test pins.

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
oracle's own `since_nmi` column (nearest match, 1,500 frames) the offset has
**two** causes, and only one of them is the plan's Q3 row.

* **The `$0318` dispatch stub, which is now modelled.** With the KERNAL mapped
  the line reaches `$FE43`, whose `SEI; JMP ($0318)` costs 7 cycles the tracer
  used to skip (`nmi.KERNAL_STUB`, §2). On Mixer *Iisibiisi*, which dispatches
  that way, modelling it moves the median offset from **−1 to +8** cycles and the
  instants that match an oracle instant from **1,781 to 4,119 within 2 cycles**
  and from **5,503 to 12,356 within 8**, of 64,664 (8.5 % → 19.1 %). JCH's *Easy
  Does It* takes the raw `$FFFA` and is unmoved: 256 within 2, 936 within 8, of
  21,149. So on the `$0318` path — **84 of 181 tunes, 85.3 % of the class by
  weight** — half of the offset was this stub, not the VIC.
* **VIC DMA, which is not modelled.** A CPU a badline stalls takes the NMI later
  than we do, and at a ~193-cycle sample period a few tens of cycles decide
  *which* sample nibble is the frame's last `$D418` write. That is what is left,
  and it is the whole of the `$FFFA` path's offset. A raster model would be the
  fix; design §12 keeps it out of scope, and this measurement says what it would
  buy: `$D418` differing in 10 / 21 / 54 % of frames on the three tunes, and 344
  of 1,500 frames whose *ordered* changed-write list places a `$D418` write
  between two different play writes.

(*Iisibiisi* was measured with the store-separability check of §4 stubbed out: it
is now refused as `schedule not store-separable`, so the tune is a measurement
here and not a certificate.)

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
store outside the stack page a preemption point, and both executors hook it in
the same order — address, value, the store's own preemption point, the guard,
then the store — so `Interp` and the emitted Python agree even on a store through
an indexed pointer (a hermetic test on both backends). This is what option **(b)**
— handshake-proven independence — would have had to establish per tune.

**Store separability is checked, not assumed.** "Between two stores nothing
either entry reads of the other can move" is a claim about the *interrupted*
routine's loads: a load inside an open preemption window that reads a cell the
handler stored in that window has no place in the schedule, because the schedule
places the handler between two stores and not between two loads. Only that
direction needs checking — the interrupted routine makes no store inside its own
window by construction, and `$D000`-`$DFFF` reads are pinned per entry, so no
reordering can move them. `nmi.Separable` stamps every cell a handler writes with
the inter-store epoch, a play load in the same window that reads such a cell
refuses **`schedule not store-separable`**, and `compared` carries
`"nmi store separability"` on a certificate that passed it. Hermetically: the
reviewer's fixture — the play routine reading `$2000` between two of its own
stores while the handler does `INC $2000` — is refused; a handler writing a cell
the play routine never reads certifies. 5 tunes of 7,023 refuse here, and three
that had certified before the check (*Sulfo_64*, *Hittibiisi*, *Iisibiisi*) were
resting on the assumption it now proves.

**Register preservation is checked too.** At the `RTI` unwinding an NMI taken
*inside* a tick, A/X/Y must be what they were at the handler's entry, else
**`nmi clobbers registers`**: the replay hands the interrupted registers back
from the schedule row, so a handler that really left them changed would be
replayed wrong. An NMI in the host's idle time is exempt — the next tick's entry
registers are verified anyway. JCH's shape, which saves A and Y into its own
`LDA #`/`LDY #` operands, certifies, and so does a `PHA`/`PLA` handler; a handler
that returns different registers is refused. 8 tunes of 7,023.

**What is replayed rather than computed.** The interrupted state — the stack
pointer, the pushed status, the return address and A/X/Y — is handed to the replay
from the schedule row (`nmi.REPLAYED`): between two stores those *can* move, and
they are the second entry's live-in. They are **not** part of `inputs_pinned`,
which counts this run's own pinned reads: *Easy Does It*'s 206,710 is 199,514
`$DD0D` acknowledge reads inside the handler plus 7,196 on the tick side. The
schedule's own contribution is counted beside it, in the certificate's NMI
schedule entry: `replayed_registers` = 6 values × 199,514 NMIs = **1,197,084**.
One consequence is visible in the decompiled mixer: the `LDA #`/`LDY #` operand
bytes JCH saves A and Y into are *reproduced from the schedule*, not computed.
The alternative — pass them as the `nmi` entry's arguments from the preempted
procedure's live values, the way `frames.contract` passes the status byte — would
compute them and would also settle the `stack: residual` row; it is a plan §5 row.
Everything the handler reads from memory is computed.

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

The certificate says what it proved: `compared` gains `"nmi preemption schedule"`
and `"nmi store separability"`, a `schedule` block names both entries (with the
NMI entry's dispatch path in `kernal` and its `replayed_registers`), and the
subtune carries `nmis` and `nmi_entries`. The claim is *the write list under the
traced interleaving*, with the preemption points recorded, separability and
register preservation checked, and the second entry's live-in registers replayed.
The trace identity digest hashes `nmilog` too, so a schedule cannot move without
the digest moving: no fixture digest changed (none has a second entry) and a test
proves the digest is not blind to one.

---

## 5. The certificate

`docs/certificates/jch-easy-does-it.json` — JCH's *Easy Does It*
([playroutine-anatomy.md](playroutine-anatomy.md) §3.5), the sample build the
plan refused by design:

```
tune       Easy_Does_It.sid, song 1, 1,799 ticks (35.9 s of music)
schedule   irq  $3FE0 every 19,656 cycles (pal_video)
           nmi  $40E9 every 193 cycles (cia2_timer_a), $FFFA, 199,514 preemptions,
                1,197,084 replayed registers
compared   init writes, tick sid writes, tick schedule effects, nmi preemption schedule,
           nmi store separability
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

193 cycles is the latch the handler starts from and not the tune's rate: it
reprograms the latch every sample, and the run averages 110.9 NMIs a tick (§2).

It is `complete: false`: a sample player consumes its sample stream, so there is
no state repeat to find at any practical horizon. That is the aperiodic verdict
of [tuneprog-plan.md](tuneprog-plan.md) L6, not a gap in this stage.

---

## 6. The class at a 30 s horizon

The 195 tunes of §1 — a dispatching NMI beside a play entry the port dispatches —
through the whole pipeline at 30 s (`tools/survey/tuneprog_sweep.py --only`),
96 CPU-min:

| outcome | tunes | raw | HVSC-weighted |
|---|---|---|---|
| `certified` | 130 / 195 | 66.7 % | 69.5 % |
| `diverged` | 30 / 195 | 15.4 % | 17.5 % |
| `refused` | 27 / 195 | 13.8 % | 1.4 % |
| `crashed` | 8 / 195 | 4.1 % | 11.6 % |

**130 certified, 218,502 ticks, 2 complete.** The run moved twice as the two
checked properties of §4 went in:

| the class at 30 s | certified | diverged | refused | crashed |
|---|---:|---:|---:|---:|
| at review | 134 | 41 | 9 | 11 |
| + store separability | 130 | 41 | 15 | 9 |
| + register preservation | **130** | **30** | **27** | **8** |

Separability cost 4 certificates and that is the point of it: *Sulfo_64*,
*Hittibiisi* and *Iisibiisi* now refuse `schedule not store-separable` — each was
resting on the assumption the check proves — and *Dune_Cover* diverges on
`entry register` at tick 33, which the `$0318` stub cycles of §3 account for. It
also turned *Crazy World 3 digipart* from a divergence into a refusal and
diagnosed 2 of the 11 JAM crashes. Register preservation took **no** certificate:
it moved 12 tunes to `nmi clobbers registers`, 11 of them divergences
(`entry register` 8, `state hash` 2, `io` 1) and one a crash.

| class (first scan) | tunes | certified | diverged | refused | crashed |
|---|---|---|---|---|---|
| `sample player, silent play` | 67 | 43 | 14 | 10 | 0 |
| `sample mixer ($D418 only)` | 57 | 45 | 9 | 3 | 0 |
| `no SID write` | 49 | 38 | 5 | 6 | 0 |
| a `JAM` the classifier already met | 11 | 0 | 0 | 3 | 8 |
| `handler writes the register file` | 8 | 4 | 2 | 2 | 0 |
| `nmi vector banked out` | 3 | 0 | 0 | 3 | 0 |

What is left, by first cause: `state hash` 11, `sid` 6, `io` 5, `entry register`
3, `input mismatch` 2, and one each of `switch`, `input exhausted`, `brk`. The
refusals are `nmi clobbers registers` 12, `schedule not store-separable` 6,
`recursion` 6 and `nmi vector banked out` 3; the 8 crashes are `JAM`s in the
tracer, a handler that runs off into RAM. 18 of the 30 divergences are past tick 5, so
these are not first-tick modelling gaps like the campaign's (survey §4) — they
are the schedule drifting from the machine's, which is the residual §3 measures.

The one-line comparison: **before this stage all 195 refused with `second
interrupt source armed`.** The first run of this sweep, before the pinned input
stream was partitioned by entry (§4), certified 58 and lost 88 tunes to
`input mismatch` alone.

### The 317 the port fix released

`find_entries` used to refuse over the **pre-init** port, which is a guess about a
port init moves; it no longer does, and `Tracer._settle` decides once init has had
it (`vector banked out` 323 → 6 on the sample). 318 tunes refuse `vector banked
out` under the pre-PR rule; 317 are released, and one still refuses. All of them
through the same pipeline at 30 s:

| outcome | tunes | raw | HVSC-weighted |
|---|---|---|---|
| `certified` | 175 / 318 | 55.0 % | 36.4 % |
| `refused` | 108 / 318 | 34.0 % | 57.9 % |
| `diverged` | 26 / 318 | 8.2 % | 5.2 % |
| `crashed` | 9 / 318 | 2.8 % | 0.5 % |

**175 certified, 691,335 ticks.** The refusals are `init runaway` 83,
`schedule not store-separable` 7, `recursion` 5, `nmi clobbers registers` 4,
`second interrupt source armed` 4, `nmi vector banked out` 3, `play runaway` 1
and the one `vector banked out` left; the divergences are `state hash` 9, `sid` 5,
`unreached` 3, `io` 3 and six singletons; the crashes are 6 `RuntimeError` and 3
`KeyError`. **All 201 built programs are `entry: irq, kernal: false`** — one
shape, the tune that writes `$FFFE` and banks the KERNAL out in init, which the
pre-init rule could not see.

---

## 7. What is proven, and what remains

**Proven.** A CIA #2 NMI is a modelled entry, not a refusal. One tune of the
largest class certifies over 1,799 ticks and 199,514 preemptions with the
schedule in the certificate. Two properties the replay rests on are *checked* per
NMI rather than assumed — store separability and register preservation — and the
oracle gate on what the play routine writes did not fire on any of three tunes.
The chip model is exercised by hermetic tests (the edge, the acknowledge,
one-shot, Timer B in both count modes, the latch rule, both vectors and the
`$0318` stub, the frame contract, the idle-time NMIs and their return address, a
repointing vector, both separability directions, both register shapes) and the
three executors — tracer, interpreter, generated Python — agree over the
interleaving, including a store through an indexed pointer. Recert 51/51, trace
byte-identity intact (the digest now covers `nmilog`), zero cost on every program
with one entry.

**Remains** (plan §5 rows):

* the NMI instant is still early without a VIC-DMA model — after the `$0318` stub,
  that is what is left of the `$D418` disagreement;
* the second entry's live-in registers are *replayed* from the schedule rather
  than passed as the `nmi` entry's arguments, so the mixer's `LDA #`/`LDY #`
  operand bytes are reproduced and not computed; naming them in `frames.contract`
  would settle the `stack: residual` row too;
* the 43 tunes whose NMI is the *only* schedule need the NMI as the tick entry,
  not as a second one;
* `grid.sidtrace_clock` still refuses a CSV whose raises do not agree on one
  period, which is three tunes of this class and the reprogrammed-clock row;
* a moving NMI vector becomes k entries, which is right for a two-phase handler
  chain and would be wrong for a genuinely computed vector — nothing bounds k;
* an NMI procedure's pushed status byte has no name in `frames.contract`, so the
  program stays `stack: residual` where it might eliminate;
* the horizon `--seconds` computes is taken from the pre-settle cadence, so a
  tune whose tick period settles later certifies a little past the horizon asked
  for (*Easy Does It*: 1,799 ticks = 35.9 s for `--seconds 30`);
* the 30 divergences, 27 refusals and 8 `JAM`s left in the class at 30 s, and the
  12 + 6 tunes the two new checks refuse there (5 + 8 over the whole sample);
* `tools/tuneprog_recert.py --resume` replays the previous tree's `state.json`
  when an `--out` is reused across a code change; a fresh `--out` per tree avoids
  it, stamping tree identity in `recert.json` would fix it.
