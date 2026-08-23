# Prototype record — CIA #2 NMI as a second entry

Closes the last design gap ([tuneprog-architecture.md](tuneprog-architecture.md)
§10; PR #272, [backlog](tuneprog-backlog.md) §3); `nmi_gate` previously only refused an
NMI.

Certificate: JCH's *Easy Does It*, 1,799 ticks, 199,514 NMIs, 0 divergences, 0
envelope traps. It claims the write list under the traced interleaving — store
separability and register preservation checked, the second entry's live-in
registers replayed — not where hardware places a `$D418` write (§3).

---

## 1. Population

`tools/tuneprog_nmi.py scan`: run init, ask `cia.CIA`/`nmi.entry` whether CIA #2
can dispatch, trace 200 ticks of both entries where it can, report what the handler
did. `report` folds the rows. Rates raw over the 7,023-tune stratified sample and
HVSC-weighted by SIDId family size ([survey-tuneprog.md](survey-tuneprog.md)). It
takes the sweep's budget/resume contract (`--timeout` 60 s a tune, append to
`--out`, exit 2 while work is left), one tune per worker — not `chunksize=4`, where
a budget cut discarded up to four tunes per worker: a chunk wrote 9 rows, now
1,087.

Counts out of 7,023; re-scan (2026-08-22) is the same command over the same 7,023
after §4's two checks, still the same 195-tune class, blank = unchanged.

| class | first scan | raw | HVSC-weighted | re-scan |
|---|---:|---|---|---:|
| `no nmi` | 6076 | 86.5 % | 95.4 % | 6075 (1 `wall timeout`) |
| `init runaway` | 520 | 7.4 % | 1.6 % | |
| `no entry` | 174 | 2.5 % | 1.5 % | |
| `sample player, silent play` | 67 | 1.0 % | 0.2 % | 90 |
| `sample mixer ($D418 only)` | 57 | 0.8 % | 1.0 % | 65 |
| `no SID write` | 49 | 0.7 % | 0.1 % | 8 |
| `no entry (nmi armed)` | 43 | 0.6 % | 0.1 % | |
| `RuntimeError` | 14 | 0.2 % | 0.1 % | 11 |
| `handler writes the register file` | 8 | 0.1 % | 0.0 % | |
| `vector banked out` | 6 | 0.1 % | 0.0 % | |
| `second interrupt source armed` | 6 | 0.1 % | 0.0 % | |
| `nmi vector banked out` | 3 | 0.0 % | 0.0 % | |
| `nmi clobbers registers` | — | — | — | 8 |
| `schedule not store-separable` | — | — | — | 5 |

13 tunes move into the two new refusal classes (7 `sample player, silent play`, 3
`RuntimeError`, 2 `sample mixer`, 1 `no SID write`); 40 of the 49 `no SID write`
move into the two sample classes (30 and 10), a 41st into `nmi clobbers registers`
— same tunes, handler body now traced (*Dune_Cover*: `handler_pcs` 4 → 62,
`handler_ram_reads` 1 → 4,347, `handler_sid` `[]` → `$D418`, unchanged 39,554 NMIs
and cadence; the three *Comer* mixers same shape). Cause not diagnosed; the only
chip-model change between scans is the conservative `cia.fired`/`_settle` gate
dropped for the chip's own `edge_at`. Property table and §6's class column: first
scan; §6's outcome table: re-run.

| property of the 181 traced schedules | tunes / 181 | raw | HVSC-weighted |
|---|---|---|---|
| Timer A | 128 | 70.7 % | 91.7 % |
| Timer B | 53 | 29.3 % | 8.3 % |
| vector `$0318` (KERNAL mapped) | 84 | 46.4 % | 85.3 % |
| vector `$FFFA` (KERNAL banked out) | 97 | 53.6 % | 14.7 % |
| handler acknowledges the ICR | 170 | 93.9 % | 98.5 % |
| handler rewrites a CIA #2 register | 34 | 18.8 % | 4.5 % |
| handler self-modifies | 120 | 66.3 % | 35.1 % |
| handler shares code with the play routine | 0 | 0.0 % | 0.0 % |
| shared RAM: NMI writes, play reads | 72 | 39.8 % | 66.5 % |
| shared RAM: play writes, NMI reads | 76 | 42.0 % | 67.7 % |
| shared RAM: both write | 59 | 32.6 % | 24.9 % |
| the play routine writes no SID register | 99 | 54.7 % | 20.0 % |
| more than one NMI per tick | 151 | 83.4 % | 79.0 % |
| every NMI ran in the idle time | 17 | 9.4 % | 19.3 % |
| the RTI frames balance | 177 | 97.8 % | 99.4 % |

195 of 7,023 have a dispatching CIA #2 NMI beside a play entry the port dispatches:
the addressable class, what §6 runs. 181 of those classify into the four schedule
classes (2.6 % raw, 1.3 % weighted) and are what the property table covers; the
classifier downgraded the other 14 first (11 `RuntimeError`, a `JAM` in the
handler's own trace; 3 `nmi vector banked out`). Largest by weight: the sample mixer
JCH's engine exemplifies. Design consequences: no shared code
→ two procedures and an input stream partitioned by entry (§4); shared RAM both ways
→ preemptions placed finely enough that shared state is right. `no entry (nmi
armed)` (`play == 0`, no IRQ vector the port dispatches, armed CIA #2) is the NMI as
a tune's *only* schedule, not modelled; `init runaway` and `no entry` are
pre-existing refusals unrelated to the NMI.

Survey §5's *311 armed / 1.8 % weighted* was `machine.nmi_gate` over the 547 tunes
already refused `second interrupt source armed`, driven past the pipeline's refusal
order — not a scan of the sample, and not every gate bypassed (§5 names 29 of the
311 as refusing first with `no entry` or `vector banked out`). This scan is the
whole 7,023 under the exact chip model:

| the 311 armed (survey §5) | tunes |
|---|---|
| a dispatching NMI beside a play entry — the class of this record | 181 |
| the NMI as the tune's *only* schedule (`no entry (nmi armed)`) | 43 |
| the remainder: armed, and refused before or beside the chip — `no entry`, `init runaway`, a CIA #2 source with no schedule (6), a line no vector answers (3) | 87 |

Last row is arithmetic, not a measured set: the 311 is not reproducible from this
tree, `nmi_gate` deleted when the exact model replaced it. 181 and 43 stand.

---

## 2. Chip model

CIA #2's line is the 6510's NMI: edge-triggered at the CPU, level-held at the chip.
An event latches a flag bit; the line asserts when a latched flag meets the ICR
mask; a `$DD0D` read clears the flags. Acknowledging handler: one NMI per
underflow. Non-acknowledging: exactly one, ever. `cia.CIA` (split from
`machine.py`) has `fl`, `ir`, `edge_at`, `raise_line`, plus three things the old
model lacked:

| what | why it is here | population |
|---|---|---|
| **Timer B**, counting φ2 or Timer A's underflows (CRB bits 5-6) | 29 % of the class dispatches on Timer B | 53 of 181 |
| **one-shot mode** (CRx bit 3), halting the timer after one underflow | a handler that re-arms per sample | in the `cra`/`crb` census |
| a **latch write to a running timer lands at the pending underflow**, it does not reload the counter | JCH's mixer rewrites `$DD04`/`$DD05` in the handler every NMI for the next sample's rate; reloading made the rate 26 % too slow and the write order wrong | every rate-controlled mixer |

Latch rule, the largest correction: reloading the counter gave *Easy Does It* 81
NMIs per frame and an oracle-differing frame grid from frame 584 on. Chip's rule:
110.9 NMIs per tick (199,514 over the certificate's 1,799) against the oracle's
112.9 per frame over 1,500. 19,656/193 = 101.8 is neither — 193 cycles is the
starting latch, reprogrammed per sample, so the period moves within the run. NMI
gaps are exactly the timer's *current* period in 91 % of cases, a multiple in the
rest.

`nmi.py` is the schedule: the dispatch vector (`$0318` with the KERNAL mapped —
`$FFFA` is ROM, reaching `$FE43`, whose `JMP ($0318)` dispatches; the RAM `$FFFA`
banked out), the entry naming it, and the refusals — `second interrupt source
armed` now only a source with no schedule in this model (TOD alarm, serial, FLAG, a
CNT-driven timer: 6 tunes of 7,023), `nmi vector banked out` a line no vector
answers.

Neither path saves a register: the NMI's entry frame is the status byte alone
(`machine.entry_frame`) against CINV's status + A/X/Y. `nmi.DISPATCH` = 7 cycles to
take the line; the KERNAL path adds `nmi.KERNAL_STUB` = 7 for `$FE43`'s `SEI` (2)
and `JMP ($0318)` (5). The port decides which, so the count is the entry's `kernal`
flag by construction (certificate: `schedule[1].kernal`).

---

## 3. Interleaving, against the oracle

`Tracer` checks one integer per instruction — the cycle CIA #2's line next asserts
— and takes the NMI at that boundary: inside the play routine, and in the host's
idle time after it returns, where most are (mixer ~5 kHz against a 50 Hz tick).
Nesting modelled: a handler acknowledging early can be preempted inside itself.

* Cost on a tune with no second schedule: 1-3 % (494/548/539/499/494 k
  instructions/s before, 487/554/534/493/480 k after, on PR #271's five tunes); its
  `Trace` byte-identical, the pinned digests in
  `tests/tuneprog/test_trace_identity.py` unmoved.
* An idle-time NMI interrupts the player's wait loop: pushed return address is
  init's `JMP *` where the tune has one, else `nmi.IDLE_PC = 0x0000`. That `JMP *`
  lowers to a `Return` in the init procedure only; any other procedure falling onto
  it is a path no execution took, pinned by a negative test.
* Tick clock is the interrupt's own grid, `cycles_init + k * cycles_per_tick`
  (corrected from "wherever the last tick ended", which let an overrunning tick
  move every later tick's origin: a whole frame of drift after ~580 frames).

Against `sidplayfp`, 1,500 frames:

| tune | class | NMIs / 1,500 frames | frames differing | which register |
|---|---|---:|---:|---|
| JCH *Easy Does It* | sample mixer (`$D418` only) | 169,419 | **150** (10.0 %) | `$D418` only |
| Sphere/Chromance *Digi Zak 2* | a vector the handlers repoint, Timer B | 224,586 | **318** (21.2 %) | `$D418` only |
| Mixer *Iisibiisi* | sample mixer (`$D418` only) | 233,380 | **812** (54.1 %) | `$D418` only |

`$D400-$D417` — everything the play routine writes — is 0 frames differing on all
three. Filtering both sides to writes that *change* a register (what a `sidtrace`
CSV records), *Easy Does It*'s per-frame changed-write list ignoring `$D418` is
identical *in order* 1,500 / 1,500; with `$D418`, 344 of 1,500 frames place it
between two different play writes than the hardware does.

Not reproduced: the exact cycle each NMI lands on. Against the oracle's `since_nmi`
column (nearest match, 1,500 frames), two causes.

* **`$0318` dispatch stub — modelled** (`nmi.KERNAL_STUB`, §2): with the KERNAL
  mapped the line reaches `$FE43`, whose `SEI; JMP ($0318)` costs 7 cycles the
  tracer charges. On *Iisibiisi*, modelling it moves the median offset −1 → +8
  cycles and instants matching an oracle instant 1,781 → 4,119 within 2 cycles,
  5,503 → 12,356 within 8, of 64,664 (8.5 % → 19.1 %). *Easy Does It* takes the raw
  `$FFFA`, unmoved: 256 within 2, 936 within 8, of 21,149. On the `$0318` path — 84
  of 181 tunes, 85.3 % by weight — half the offset was this stub, not the VIC.
* **VIC DMA — not modelled.** A badline-stalled CPU takes the NMI later than we do;
  at a ~193-cycle sample period a few tens of cycles decide which sample nibble is
  the frame's last `$D418` write. It is the whole of the `$FFFA` path's offset. A
  raster model would fix it and buy the `$D418` figures above; [tuneprog-architecture.md](tuneprog-architecture.md) §8.3 keeps it
  out of scope.

*Iisibiisi* was measured with §4's separability check stubbed out; it now refuses
`schedule not store-separable`, so it is a measurement here, not a certificate.

`grid.sidtrace_clock` was relaxed to reach two of the three: it refused any CSV
write more than one period after its raise (a second entry writes while the first
is idle). Its period check — every gap a whole multiple of one period — still
refuses a reprogrammed or split clock and three more tunes of the class; a `grid`
backlog row.

---

## 4. The program model

From §1: entries share no code → two procedures, `tick` and `nmi` (a vector the
handlers repoint gives `nmi0`, `nmi1`, ..., one entry per address taken); they
share RAM both ways → preemptions must be placed where shared state is what the
handler saw. Model: plan option (a), call granularity with a recorded schedule.

* **Schedule recorded per NMI:** tick, instruction index within it, cycle, handler
  address, stores the tick had made, stack pointer, pushed status, interrupted pc
  and A/X/Y.
* **Replay at store granularity.** `NmiMachine` makes every store outside the stack
  page a preemption point; both executors hook it in one order (address, value,
  preemption point, guard, store), so `Interp` and the emitted Python agree even on
  a store through an indexed pointer (hermetic test, both backends). Option (b),
  handshake-proven independence, would need this per tune.
* **Store separability checked, not assumed.** `nmi.Separable` stamps each cell a
  handler writes with the inter-store epoch; a play load in the same window reading
  such a cell refuses `schedule not store-separable`; `compared` carries
  `"nmi store separability"` when passed. Only the interrupted routine's loads need
  checking: it makes no store inside its own window by construction, and
  `$D000`-`$DFFF` reads are pinned per entry. Hermetic: play reading `$2000`
  between two of its own stores against a handler's `INC $2000` is refused; a
  handler writing a cell play never reads certifies. 5 of 7,023 in the 200-tick
  scan, 6 of the 195 at 30 s.
* **Register preservation checked.** At the `RTI` unwinding an NMI taken *inside* a
  tick, A/X/Y must equal the handler's entry values, else `nmi clobbers registers`
  — the replay hands them back from the schedule row, so a changed register would
  replay wrong. Idle-time NMIs exempt: the next tick's entry registers are verified
  anyway. JCH's shape (A/Y into its own `LDA #`/`LDY #` operands) and a `PHA`/`PLA`
  handler certify; returning different registers is refused. 8 of 7,023 in the
  200-tick scan, 12 of the 195 at 30 s.
* **Interrupted state replayed, not computed.** Stack pointer, pushed status,
  return address and A/X/Y come from the schedule row (`nmi.REPLAYED`): they can
  move between two stores, and they are the second entry's live-in. Not in
  `inputs_pinned`, this run's own pinned reads (*Easy Does It*: 206,710 = 199,514
  handler `$DD0D` acknowledge reads + 7,196 tick side), but in the schedule entry's
  `replayed_registers` = 6 values × 199,514 NMIs = 1,197,084. So the decompiled
  mixer's `LDA #`/`LDY #` operand bytes are reproduced, not computed. Alternative
  (plan §2): pass them as the `nmi` entry's arguments from the preempted
  procedure's live values, as `frames.contract` passes the status byte — computes
  them, and settles `stack: residual`. Everything the handler reads from memory is
  computed.
* **Input stream partitions by site**, which the disjoint bodies allow: each entry
  reads its own in its own order. Without the split, 88 of 117 divergences in the
  first class-wide run were `input mismatch` (tick `$D019` against handler `$DD0D`
  acknowledges in one stream).
* **Zero cost on a one-entry program.** `emit_python` emits the preemption point
  only for a program with an NMI entry: generated `tuneprog.py` byte-identical
  (sha1 of the emitted text unchanged on Commando, *Automatas*, *Do It Again*),
  runs on the plain `Machine`, verifier throughput unmoved (17,955 / 14,703 / 9,975
  calls/s before, 19,266 / 14,444 / 9,883 after — noise),
  `tools/tuneprog_recert.py` 51/51 with 0 fields moved.
* **Certificate:** `compared` gains the two `nmi` entries; a `schedule` block names
  both entries (NMI dispatch path in `kernal`, plus `replayed_registers`); the
  subtune carries `nmis` and `nmi_entries`. The trace identity digest hashes
  `nmilog`, so a schedule cannot move without it: no fixture digest changed (none
  has a second entry), and a test proves the digest is not blind to one.

---

## 5. The certificate

`docs/certificates/jch-easy-does-it.json` — JCH's *Easy Does It*
([playroutine-anatomy.md](playroutine-anatomy.md) §3.5), the sample build the plan
refused by design:

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
every 193 cycles, sample pointer at `$FD`/`$FE`, `$DD04`/`$DD05` rewritten per
sample under the play routine's lock, A/Y into its own operands (§4) — all
decompiled, none special-cased. `tick` is the plain NewPlayer V20 engine two other
certificates already hold, with the `CPX #$03` track-4 branches live. The 193
cycles is the starting latch, not the tune's rate: reprogrammed per sample,
averaging 110.9 NMIs a tick (§2).

`complete: false`: a sample player consumes its sample stream, so no state repeat
exists at any practical horizon — aperiodic per
[tuneprog-backlog.md](tuneprog-backlog.md) §2, not a gap in this stage.

---

## 6. The class at a 30 s horizon

The 195 tunes of §1 through the whole pipeline at 30 s
(`tools/survey/tuneprog_sweep.py --only`), 96 CPU-min: 218,502 ticks, 2 complete.

| outcome | tunes / 195 | raw | HVSC-weighted |
|---|---|---|---|
| `certified` | 130 | 66.7 % | 69.5 % |
| `diverged` | 30 | 15.4 % | 17.5 % |
| `refused` | 27 | 13.8 % | 1.4 % |
| `crashed` | 8 | 4.1 % | 11.6 % |

The run moved twice as §4's two checks went in:

| the class at 30 s | certified | diverged | refused | crashed |
|---|---:|---:|---:|---:|
| at review | 134 | 41 | 9 | 11 |
| + store separability | 130 | 41 | 15 | 9 |
| + register preservation | **130** | **30** | **27** | **8** |

Separability cost 4 certificates: *Sulfo_64*, *Hittibiisi* and *Iisibiisi* refuse
`schedule not store-separable`; *Dune_Cover* diverges on `entry register` at tick
33, which §3's `$0318` stub cycles account for. It also turned *Crazy World 3
digipart* from a divergence into a refusal and diagnosed 2 of the 11 JAM crashes.
Register preservation cost no certificate: 12 tunes moved to
`nmi clobbers registers`, 11 of them divergences (`entry register` 8, `state hash`
2, `io` 1), one a crash.

| class (first scan) | tunes | certified | diverged | refused | crashed |
|---|---|---|---|---|---|
| `sample player, silent play` | 67 | 43 | 14 | 10 | 0 |
| `sample mixer ($D418 only)` | 57 | 45 | 9 | 3 | 0 |
| `no SID write` | 49 | 38 | 5 | 6 | 0 |
| a `JAM` the classifier already met | 11 | 0 | 0 | 3 | 8 |
| `handler writes the register file` | 8 | 4 | 2 | 2 | 0 |
| `nmi vector banked out` | 3 | 0 | 0 | 3 | 0 |

Divergences by first cause: `state hash` 11, `sid` 6, `io` 5, `entry register` 3,
`input mismatch` 2, one each of `switch`, `input exhausted`, `brk`. Refusals:
`nmi clobbers registers` 12, `schedule not store-separable` 6, `recursion` 6,
`nmi vector banked out` 3. The 8 crashes are `JAM`s in the tracer, a handler
running off into RAM. 18 of the 30 divergences are past tick 5: the schedule
drifting from the machine's, the residual §3 measures, not first-tick modelling
gaps like the campaign's (survey §4).

Before this stage all 195 refused `second interrupt source armed`. This sweep's
first run, before the pinned input stream was partitioned by entry (§4), certified
58 and lost 88 tunes to `input mismatch` alone.

### The 317 the port fix released

`Tracer._settle` decides the entry vector once init has had the port, not
`find_entries` over the pre-init port (`vector banked out` 323 → 6 on the
sample). 318 tunes refuse `vector banked out` under the pre-PR rule; 317
released, one still refuses. All through the same pipeline at 30 s: 691,335
ticks.

| outcome | tunes / 318 | raw | HVSC-weighted |
|---|---|---|---|
| `certified` | 175 | 55.0 % | 36.4 % |
| `refused` | 108 | 34.0 % | 57.9 % |
| `diverged` | 26 | 8.2 % | 5.2 % |
| `crashed` | 9 | 2.8 % | 0.5 % |

Refusals: `init runaway` 83, `schedule not store-separable` 7, `recursion` 5,
`nmi clobbers registers` 4, `second interrupt source armed` 4,
`nmi vector banked out` 3, `play runaway` 1, the one `vector banked out` left.
Divergences: `state hash` 9, `sid` 5, `unreached` 3, `io` 3, six singletons.
Crashes: 6 `RuntimeError`, 3 `KeyError`. All 201 built programs are `entry: irq,
kernal: false` — one shape, the tune that writes `$FFFE` and banks the KERNAL out
in init, which the pre-init rule could not see.

---

## 7. What remains

Hermetic tests cover the chip model and the schedule: the edge, the acknowledge,
one-shot, Timer B in both count modes, the latch rule, both vectors and the `$0318`
stub, the frame contract, the idle-time NMIs and their return address, a repointing
vector, both separability directions, both register shapes. Tracer, interpreter and
generated Python agree over the interleaving, including a store through an indexed
pointer.

Open (plan §2):

* the NMI instant is still early without a VIC-DMA model (§3);
* live-in registers are replayed rather than passed as the `nmi` entry's arguments
  (§4);
* the 43 tunes whose NMI is the only schedule need it as the tick entry (§1);
* `grid.sidtrace_clock` still refuses a CSV whose raises do not agree on one
  period: three tunes of this class, and the reprogrammed-clock row;
* a moving NMI vector becomes k entries — right for a two-phase handler chain,
  wrong for a genuinely computed vector; nothing bounds k;
* an NMI procedure's pushed status byte has no name in `frames.contract`, so the
  program stays `stack: residual` where it might eliminate;
* `--seconds` computes its horizon from the pre-settle cadence, so a tune whose
  tick period settles later certifies a little past the horizon asked for (*Easy
  Does It*: 1,799 ticks = 35.9 s for `--seconds 30`);
* the 30 divergences, 27 refusals and 8 `JAM`s left in the class at 30 s (§6);
* `tools/tuneprog_recert.py --resume` reads the state `recert.json` holds without
  asking which tree wrote it, so an `--out` reused across a code change replays the
  previous tree's verdicts; a fresh `--out` per tree avoids it, stamping tree
  identity in `recert.json` fixes it.
