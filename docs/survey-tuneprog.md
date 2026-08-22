# tuneprog at survey scale — the pipeline over the stratified HVSC sample

Companion to [tuneprog-decompiler-design.md](tuneprog-decompiler-design.md)
(section 9 is the *static* survey this one joins),
[tuneprog-plan.md](tuneprog-plan.md) (this campaign is PR #267) and
[tuneprog.md](tuneprog.md): what the whole pipeline does to 7,023 tunes, what it
refuses, where it diverges, what it costs. Nothing was fixed while it was
measured — no tune was retried into passing, no family admitted, no certificate
committed.

Contents: 1 method · 2 outcomes · 3 by family · 4 failure classes · 5 refusals ·
6 completeness and the period pass · 7 the machine stack · 8 entry and cadence ·
9 copy folding · 10 data-gated class sizes · 11 what the programs look like ·
12 cost and tracer throughput · 13 crashes · 14 what it changes

---

## 1. Method

- **Sample.** Design section 9's stratified sample: up to 30 tunes per SIDId
  family, seed 1, from `hvsc-tracker-catalog`'s `results.csv` over HVSC #85 as
  installed — 7,023 tunes on disk, 645 families (646 in the static survey for the
  same 7,023; the catalogue moved by one family since 2026-08-16). HVSC #85 holds
  61,157 `.sid` files, of which the SIDId catalogue covers 60,388: the population
  the weighting maps onto. `tools/survey/tuneprog_sweep.py` imports `run.py`'s
  `_sample`, so both surveys sample the same files.
- **One subtune per tune** — the header's `startsong`, the pipeline default,
  matching design section 9. The 20 % of HVSC with more than one subtune is
  under-sampled throughout.
- **Two passes.** Pass 1 runs `pipeline.run` to a 30 s horizon (`--seconds 30`)
  over all 7,023. Pass 2 re-runs pass-1 certified tunes with `--until-period
  --max-calls 400000`. Defaults otherwise: trace closure, sibling copies merged,
  S5/S6 text on, `--prefix 2000` interpreter cross-check.
- **Pass 2 is a scaled sample.** The period run costs far more per tune (no
  repeat means tracing to `--max-calls` or the wall cap), so it takes the first
  three per family of the certified tunes in path order — 1,338 of 5,384, a
  nesting of the same seed-1 stratification. Families with three or fewer
  certified tunes are covered entirely, so pass 2's raw rates spread more evenly
  across families and its weighted rates carry more variance per family.
- **Timeout.** 120 wall seconds per tune in pass 1, 300 in pass 2, fixed before
  the run from a 50-tune pilot (median 3.9 s, p90 13.5 s, max 18.4 s) and not
  tuned afterwards. A tune that hits it is recorded as `timeout`, never retried.
  Each worker is capped at 8 GiB of address space; an over-run is `oom`.
- **Weighting.** Rates are given raw over the sample and re-weighted to the
  catalogued HVSC population by family size, as design section 9 does: a sampled
  tune of family *f* counts *N_f / n_f*, so a 10,720-tune family counts 357 per
  sampled tune and a 3-tune family counts 1.

**Reproduction.**

    python tools/survey/tuneprog_sweep.py --hvsc C64Music --results results.csv \
        --out horizon.jsonl --seconds 30 --jobs 60 --timeout 120
    python tools/survey/tuneprog_sweep.py --hvsc C64Music --results results.csv \
        --out period.jsonl --from horizon.jsonl --until-period --timeout 300
    python tools/survey/tuneprog_report.py --horizon horizon.jsonl \
        --period period.jsonl --results results.csv --hvsc C64Music

`--only FILE` restricts a run to the HVSC-relative paths that file lists, which
is how the corrections below re-measure one failure class without re-running the
sample. Per-tune artefacts are pruned as each row is written; only the JSONL rows
survive, and neither they nor any certificate are committed.

**Provenance.** Both passes ran on `main` at `6b8ef25`. #265 (equality saturation
in S6) merged while they ran; it is an opt-in `--eqsat` presentation flag that
never touches the certified S4 program.

---

## 2. Outcomes

| outcome | tunes | raw | HVSC-weighted |
|---|---|---|---|
| certified | 5384 / 7023 | 76.7 % | 91.2 % |
| diverged | 399 / 7023 | 5.7 % | 2.5 % |
| refused | 1222 / 7023 | 17.4 % | 6.2 % |
| crashed | 11 / 7023 | 0.2 % | 0.0 % |
| timeout | 7 / 7023 | 0.1 % | 0.0 % |

A tune drawn from HVSC certifies with probability 0.91 at a 30 s horizon;
section 6 measures what a longer one costs that figure. Definitions:

- **certified** — the emitted Python reproduced the tune's per-tick SID and
  schedule write lists against the tracer for the whole horizon, zero
  divergences: the acceptance test the 46 committed certificates pass.
- **diverged** — a certificate exists and records a divergence.
- **refused** — the pipeline diagnosed an unsupported construct (design
  principle 6) and produced nothing.
- **crashed** — an undiagnosed exception, i.e. a bug.

The raw/weighted gap is the shape of HVSC: the families that fail are numerous
and small (digi players, BASIC containers, one-off routines), the families that
pass are few and enormous (DMC, GoatTracker, Music Assembler, FutureComposer).

---

## 3. Certification rate by family

The twenty largest families, plus the nine exemplar families in bold.

| family | HVSC | sampled | certified | complete | commonest other outcome |
|---|---|---|---|---|---|
| DMC | 10720 | 30 | 30 (100 %) | 1 | – |
| **GoatTracker_V2.x** (GoatTracker 2) | 7534 | 30 | 30 (100 %) | 2 | – |
| Music_Assembler | 6376 | 30 | 28 (93 %) | 0 | diverged ×2 |
| MoN/FutureComposer | 4038 | 30 | 30 (100 %) | 0 | – |
| **JCH_NewPlayer** (JCH NewPlayer) | 3674 | 30 | 30 (100 %) | 5 | – |
| Soundmonitor | 3638 | 28 | 21 (75 %) | 0 | refused ×7 |
| GoatTracker_V1.x | 1384 | 30 | 28 (93 %) | 1 | refused ×2 |
| *Unidentified* | 1303 | 30 | 18 (60 %) | 5 | refused ×11 |
| HardTrack_Composer | 1169 | 30 | 30 (100 %) | 0 | – |
| **Hermit/SidWizard_V1.x** (SID Wizard) | 1073 | 30 | 27 (90 %) | 0 | diverged ×3 |
| Master_Composer | 1071 | 30 | 30 (100 %) | 0 | – |
| Geir_Tjelta/SIDDuzz'It | 994 | 30 | 30 (100 %) | 0 | – |
| SoedeSoft | 948 | 30 | 30 (100 %) | 2 | – |
| Digitalizer_V2.x | 680 | 30 | 27 (90 %) | 0 | diverged ×3 |
| RoMuzak_V6.x | 591 | 30 | 25 (83 %) | 0 | refused ×5 |
| Basic_Program | 522 | 30 | 0 (0 %) | 0 | refused ×30 |
| GMC/Superiors | 446 | 30 | 30 (100 %) | 0 | – |
| X-Ample | 385 | 30 | 29 (97 %) | 0 | diverged ×1 |
| SidFactory_II/Laxity | 380 | 30 | 1 (3 %) | 0 | diverged ×29 |
| Laxity_NewPlayer_V21 | 314 | 30 | 29 (97 %) | 1 | refused ×1 |
| **DefMon** (defMON, Automatas) | 106 | 30 | 29 (97 %) | 2 | refused ×1 |
| **Rob_Hubbard** (Hubbard, Commando) | 289 | 30 | 28 (93 %) | 0 | refused ×2 |
| **Martin_Galway** (Galway, Comic Bakery) | 55 | 30 | 14 (47 %) | 0 | diverged ×10 |
| **Stephen_Ruddy** (Follin, Ghouls'n'Ghosts) | 37 | 30 | 26 (87 %) | 3 | refused ×4 |
| **Electrosound** (Walker, Chameleon) | 301 | 30 | 30 (100 %) | 0 | – |
| **Blackbird/LFT** (Blackbird, Quintessence) | 40 | 30 | 26 (87 %) | 0 | diverged ×3 |

Eight of the nine exemplar families certify 87–100 % of a 30-tune draw without a
line of family-specific code (design principle 5, measured). Galway is the
exception at 47 %: his 55 HVSC tunes are hand-written engines that differ per
game — fourteen certify, ten diverge (seven `trap switch`, three `trap
unreached`), six refuse. The seed-1 draw did not include *Comic Bakery*, which
the exemplar work certifies separately.

Ranked by tunes-not-certified × family size, the families costing HVSC the most
coverage: Soundmonitor (910 tunes' worth, all refusals), Basic_Program (522, all
refusals), *Unidentified* (521), Music_Assembler (425), SidFactory_II/Laxity
(367, all divergences), Reflextracker (137, all refusals), CyberTracker_exe (130,
all refusals).

---

## 4. Failure classes

A divergence is a tick where the emitted program's write list differs from the
tracer's, or a trap it reaches. The class is the trap kind (or the compared
list); the site is the block label or address the certificate records.

| class | tunes | raw | weighted | first-divergence site | example (tick) |
|---|---|---|---|---|---|
| trap `switch` | 189 / 7023 | 2.7 % | 0.5 % | `L10D4_9D` | Bitfrost.sid (tick 2) |
| the `io` write list differs | 73 / 7023 | 1.0 % | 0.2 % | – | Music_Maker_Loader.sid (tick -1) |
| trap `unverified` | 47 / 7023 | 0.7 % | 0.6 % | `U118D_13A9` | Twilight_Destruction_Club_Mix.sid (tick 2) |
| trap `untaken` | 31 / 7023 | 0.4 % | 0.9 % | `L6583_A9` | Shes_Lost_Control.sid (tick 1) |
| trap `input exhausted` | 26 / 7023 | 0.4 % | 0.1 % | `$2042` | TPX_Musiced_01.sid (tick 1) |
| trap `unreached` | 18 / 7023 | 0.3 % | 0.2 % | `X0002` | Ocean_Conqueror.sid (tick 3583) |
| trap `input mismatch` | 12 / 7023 | 0.2 % | 0.0 % | `want $02D0 got $0001` | Break_Fever.sid (tick -1) |
| trap `brk` | 2 / 7023 | 0.0 % | 0.0 % | `LFFFF_FF` | Ocean_Ranger.sid (tick 0) |
| trap `envelope` | 1 / 7023 | 0.0 % | 0.0 % | `$AA2D outside [$A930,$AA2C] at $0000` | A_Mind_Is_Born.sid (tick -1) |

**Divergences are immediate, not drift.** Of the 399: 93 fail in init (tick −1),
123 at tick 0, 67 at tick 1, 61 at tick 2 — 344 of 399 (86 %) before tick 3 —
and only 55 later. They are systematic modelling gaps the first tick exposes, not
short horizons or slow desynchronisation.

**`trap switch` (189 tunes, 2.7 % raw, 0.5 % weighted) is the largest class**,
concentrated in whole families: Virtuoso 29/30, Ben Daglish/Gremlin 25,
Element114Studio 25, Fred Gray 15, Tiny/Sound Images 12, Galway 7. Diagnosed
([tuneprog-plan.md](tuneprog-plan.md) §3, PR #270) as three front-end readings of
computed control, none of them `emit._term` (corrected from an initial reading of
`emit._term` giving a `Switch` a case per traced value):

* a `JMP (ind)` whose own operand the program patches dispatched on the
  **pointer** while its cases were the observed **targets** (Virtuoso,
  Element114Studio, Fred Gray, Galway, Tiny/Sound Images);
* a patched **branch offset of zero** names the address after the instruction,
  which the "every successor but the fall-through" rule discarded (Ben
  Daglish/Gremlin, Prosonix);
* the **copy index** was stepped before the arm that advances the run was chosen,
  so a family's exit carried `v = k` (`Bitfrost.sid`, the example row above).

Re-run over the same 189 at 30 s (188 `trap switch` + 1 `untaken`): **177
certified**, leaving 4 `trap switch` (all one new shape, an unmatched `RTS`
return), 4 `io`, 2 `input exhausted`, 2 wall timeouts. Classified on `main` at
30 s by the instruction that dispatches at the first divergence: `JMP (ind)` 110,
patched branch 62, unmatched `RTS`/`RTI` 4, 13 unresolved; by the emitted
scrutinee instead, 98 / 77 / 4 with 9 on the copy index and 1 `untaken`. The
class is self-modification of *control*, not of code at large: the initially
reported correlation with self-modification generally (159 of 189, 84 %, with a
play site writing an instruction byte, against 54 % of certified programs) is
refuted as a cause, and copy folding is a bystander, under ten of the 189.

**The `io` list (73) fails at init** in 41 of 73 cases: the program's init writes
to VIC/CIA differ from the trace's. No tune diverged on the *SID* write list;
every divergence is a trap or the I/O list. Concentrated in
Geir_Tjelta/SIDSys18.6 (17), Heathcliff/DigitalArts (11), Novaload (11).

**`trap unverified` (47) and `trap untaken` (31)** are the two control mechanisms
above seen from the other side (corrected from a reading of them as the sibling
closure boundary): a patched `JMP (ind)` whose pointer value matched a table entry
`jumptab.enumerate_targets` had closed as an `unverified` arm, and a zero branch
offset whose arm the same closure supplied because the case set had dropped it.
Re-run over the same 78 at 30 s: **78 certified, 0 diverged**, with no change to
`siblings` or `closure`. SidFactory II/Laxity, 23 of the 47, certifies whole.

**`trap input exhausted` / `input mismatch` (38)** are volatile-input replay: the
program consumed pinned inputs in a different order or number than the trace
recorded. Novaload and Heathcliff/DigitalArts lead.

---

## 5. Refusals

As first measured (1,222 refusals):

| reason | tunes | raw | weighted | raised at | example |
|---|---|---|---|---|---|
| `second interrupt source armed` | 547 / 7023 | 7.8 % | 3.0 % | `machine.py:find_entries` | Bedlam_tune_2.sid |
| `no entry` | 291 / 7023 | 4.1 % | 1.7 % | `machine.py:vector_gate` | 12345.sid |
| `vector banked out` | 184 / 7023 | 2.6 % | 0.5 % | `machine.py:vector_gate` | Aefro.sid |
| `recursion` | 118 / 7023 | 1.7 % | 0.6 % | `cfg.py:visit` | Avenger.sid |
| `init runaway` | 75 / 7023 | 1.1 % | 0.3 % | `machine.py:init_runner` | Air.sid |
| `play runaway` | 5 / 7023 | 0.1 % | 0.0 % | `trace.py:_one_call` | WitchSwitch.sid |
| `port moved` | 1 / 7023 | 0.0 % | 0.0 % | `trace.py:_one_call` | Pigman.sid |
| `copy index` | 1 / 7023 | 0.0 % | 0.0 % | `wire.py:wire_one` | Densetsu_no_Stafy-Coral_Reef.sid |

Every refusal is diagnosed; none is a silent approximation. The second interrupt
is 45 % of all refusals as first measured. `no entry` and
`vector banked out` together (475) are the `play == 0` population whose installed
vector the 6510 port does not dispatch through, or which installed none: BASIC
containers, digi players, RSID main loops. `recursion` (118) is a `JSR` cycle in
the call graph, which `cfg._no_recursion` refuses by design.

**The `second interrupt source armed` row counted evidence, not a schedule**
(corrected 2026-08-22). `find_entries` refused any write to the CIA #2 Timer-A
latch (`$DD04`/`$DD05`) or to the NMI vector (`$0318`/`$0319`); neither makes an
NMI possible. A tune has a second schedule iff a CIA #2 source can fire: its ICR
(`$DD0D`) written with bit 7 and one of bits 0-4 — a mask the chip *accumulates*,
so the last write does not give it — and, for a timer source, that timer started
(`$DD0E`/`$DD0F` bit 0). CIA #2's interrupt line is the 6510's NMI, so an enabled
source that can have its event is the refusal whatever vector carries it, and a
vector installed over no such source is dead, as `vector_gate` already treats a
dead `$FFFE` write. RESTORE is the other NMI source and `sidplayfp` never presses
it.

With the exact model built ([prototype-nmi.md](prototype-nmi.md)), over the whole
sample: **195 tunes of 7,023 have a dispatching NMI beside a play entry**, 181 of
them with a classified schedule (2.6 % raw, 1.3 % weighted); 43 more have the NMI
as their only schedule. `second interrupt source armed` now means a CIA #2 source
with no schedule — 6 tunes. The misdiagnosed class is 81 tunes, 0.8 % of HVSC by
weight, against design section 9.2's ≈ 1 % estimate for the vector-only/unarmed
share. Nine of the 81 have a *last* ICR write enabling Timer B that the
accumulated mask does not, because the chip never saw it (it lands with I/O
banked out, or on an init path only the second emulation takes): the tracer's own
CIA is the authority, the init trace only ever the cheap refusal.

Putting all 547 back through the 30 s pipeline gives 3 certified, 1 diverged
(*Rally_Cross*, an `io` write list differing at init), 1 crashed
(*Original_Tetris-Game*, `JAM at $0002`) and 542 refused:

| reason | was | now | raw | HVSC-weighted |
|---|---|---|---|---|
| `second interrupt source armed` | 547 | 273 | 3.9 % | 1.6 % |
| `nmi armed in play` (new: the gate is re-checked per tick) | — | 9 | 0.1 % | 0.6 % |
| `no entry` | 291 | 467 | 6.6 % | 2.3 % |
| `vector banked out` | 184 | 244 | 3.5 % | 0.7 % |
| `recursion` | 118 | 124 | 1.8 % | 0.7 % |
| `init runaway` | 75 | 91 | 1.3 % | 0.4 % |
| `play runaway` | 5 | 6 | 0.1 % | 0.1 % |
| `port moved` | 1 | 2 | 0.0 % | 0.0 % |
| certified (outcome) | 5384 | 5387 | 76.7 % | 91.2 % |
| refused (outcome) | 1222 | 1217 | 17.3 % | 6.2 % |

The second interrupt is 2.2 % of HVSC by weight, not 3.0 % — 1.6 % armed by the
end of init plus 0.6 % armed during play. The 0.8 % over-count is released almost
entirely into refusals it had been shadowing: `no entry` and `vector banked out`
take 236 of the 274 tunes, `play == 0` containers whose installed vector the port
does not dispatch through. The nine `nmi armed in play` tunes each enable
CIA #2's ICR in init and start the timer only once the music is running
(Hubbard's *Mr_Meaner* and *Kings_of_the_Beach_intro*, two Soundmonitor tunes,
GoatTracker V1, Hans Siemons, Odie/Cosine, Georg Brandt, Vibrants/JO).

Exposed by tracing this class for the first time: `playroutine_cadence` fell
through from CIA #1 to CIA #2 for the play latch and treated an unwritten ICR as
the armed KERNAL default — right for CIA #1, wrong for CIA #2 — handing back a
dead CIA #2 latch as the tick period. `_cadence` now takes a CIA period only when
it is CIA #1's. *Jazzpjazz* showed it (1,799 ticks of `pal_host_cia`, not 2,868
of a `$DD04` latch nothing dispatches), judged by `sidplayfp`: the gaps between
the interrupts the oracle attributes its writes to are whole multiples of the
host CIA's period, not of that latch.

---

## 6. Completeness and the period pass

At the 30 s horizon:

| certified program | tunes | raw | HVSC-weighted |
|---|---|---|---|
| complete (a state repeat proved inside the horizon) | 333 / 5384 | 6.2 % | 4.3 % |
| a repeat was seen but the program is not complete | 0 / 5384 | 0.0 % | 0.0 % |
| no repeat: horizon-capped | 5051 / 5384 | 93.8 % | 95.7 % |

A state repeat inside 30 s is the exception, so completeness comes from the
period pass.

### The `--until-period` pass

1,338 tunes (the first three certified per family), `--until-period
--max-calls 400000`, 300 s wall cap:

| outcome | tunes | raw | HVSC-weighted |
|---|---|---|---|
| certified | 981 / 1338 | 73.3 % | 81.7 % |
| diverged | 16 / 1338 | 1.2 % | 0.2 % |
| refused | 14 / 1338 | 1.0 % | 0.3 % |
| crashed | 1 / 1338 | 0.1 % | 0.0 % |
| timeout | 326 / 1338 | 24.4 % | 17.8 % |

| certified program | tunes | raw | HVSC-weighted |
|---|---|---|---|
| complete (a state repeat proved inside the horizon) | 894 / 981 | 91.1 % | 99.4 % |
| no repeat: capped at 400,000 ticks | 87 / 981 | 8.9 % | 0.6 % |

Given the tracing budget a certified program is complete 91 % of the time
(99.4 % weighted); over the whole pass-2 population, timeouts included, 894 of
1,338 tunes (66.8 % raw, 81.2 % weighted) end as complete programs. The 118
already complete at 30 s become 894, a 7.6× increase. Music traced to the repeat:
median 118 s, p90 432 s, max 5,725 s (7,495 ticks median, 400,000 max). The 326
timeouts are the cost boundary at 300 wall seconds, spread thinly (no family
contributes more than its three).

**The horizon is not free of correctness information.** 31 of the 1,338 tunes
certified at 30 s and did *not* certify at period scale:

| what a longer horizon found | tunes | reason |
|---|---|---|
| diverged | 16 | 7 `trap unreached` (e.g. *Space_Patrol.sid* at tick 7,935), 5 `input mismatch`, 3 `trap switch` (*Butcher_Hill.sid* at tick 3,456), 1 `io` list |
| refused | 14 | 13 `recursion` — a `JSR` cycle the 30 s trace never closed — and 1 `play runaway` |
| crashed | 1 | `RuntimeError: JAM at $00FE` (*Edge_of_Disgrace.sid*) |

Section 2's 91.2 % weighted certification rate is therefore a 30 s figure, and
about 2.3 % of the tunes it counts would not survive a song-length horizon on
this evidence.

---

## 7. The machine stack

Over the 5,783 programs that were built (certified plus diverged — a divergence
is found after S4, so its program exists):

| stack | tunes | raw | HVSC-weighted |
|---|---|---|---|
| eliminated | 4957 / 5783 | 85.7 % | 95.6 % |
| residual | 826 / 5783 | 14.3 % | 4.4 % |

A residual program can be held by more than one procedure, so the first three
rows overlap:

| residual (826) | tunes | share of residual |
|---|---|---|
| held by `tick` | 512 | 62.0 % |
| held by `init` | 219 | 26.5 % |
| held by a helper procedure (`p_XXXX`) | 365 | 44.2 % |
| depth not computed (the frame is opaque) | 819 | 99.2 % |
| entry kind `sub` | 785 | 95.0 % |
| entry kind `irq` | 41 | 5.0 % |

A tune drawn from HVSC whose program is built keeps its stack with probability
0.044. The residual is whole-program by construction (one unplaceable read keeps
`SP` everywhere), and the unplaceable read is in `tick` itself 62 % of the time
and in `init` 27 %, so the "residual-stack localisation" row (plan §2) would have to
localise inside the tick to win most of the class, not merely keep helpers out of
it. 819 of the 826 have no computable depth at all (`Frame.events is None`: the
procedure's stack is not covered by its own pushes), so the question for this
class is whose frame, not how deep.

---

## 8. Entry and cadence

Over the 5,783 built programs. This is the *post-refusal* topology: a tune whose
entry is an installed handler refuses far more often than one with a header
`play`, so interrupt entries are under-counted relative to design section 9.2
(8.7 % `irq` before any refusal).

| entry | tunes | raw | HVSC-weighted |
|---|---|---|---|
| `sub` (header play, JSR each tick) | 5675 / 5783 | 98.1 % | 99.1 % |
| `irq` (installed handler) | 108 / 5783 | 1.9 % | 0.9 % |
| … through the KERNAL vector (CINV) | 108 / 5783 | 1.9 % | 0.9 % |
| … through the hardware vector | 0 / 5783 | 0.0 % | 0.0 % |

Every interrupt entry the pipeline built is a CINV entry, none through `$FFFE`.
The `vector banked out` refusal (184 tunes) is where the raw-vector population
went: those tunes wrote `$FFFE` with the KERNAL mapped, so the port dispatches
through `$0314` and the write is dead. The KERNAL-frame convention
(`machine.entry_frame`, the `$FF48` prologue's A/X/Y) carries the entire
installed-handler class as measured; the raw `RTI` frame has no population here.

| cadence source | tunes | raw | HVSC-weighted |
|---|---|---|---|
| `pal_video` | 4374 / 5783 | 75.6 % | 80.3 % |
| `cia_timer` | 666 / 5783 | 11.5 % | 12.1 % |
| `pal_host_cia` | 379 / 5783 | 6.6 % | 2.7 % |
| `ntsc_video` | 308 / 5783 | 5.3 % | 3.0 % |
| `ntsc_host_cia` | 56 / 5783 | 1.0 % | 1.8 % |

| PSID speed bits | tunes | raw | HVSC-weighted |
|---|---|---|---|
| speed word 0 (every subtune host-framed) | 4734 / 5783 | 81.9 % | 84.0 % |
| speed word non-zero | 1049 / 5783 | 18.1 % | 16.0 % |
| … and the tune arms no timer of its own (host CIA cadence) | 435 / 5783 | 7.5 % | 4.6 % |

The speed-flag work of 2026-08-21 is load-bearing for 435 tunes, 4.6 % of HVSC by
weight: they program no timer, so their cadence is the host's CIA #1 Timer-A
latch and nothing else decides it. The other 614 tunes with a non-zero speed word
arm their own timer, where the flag is redundant.

---

## 9. Copy folding

| copies | tunes | raw | HVSC-weighted |
|---|---|---|---|
| at least one folded family | 3033 / 5783 | 52.4 % | 40.4 % |
| two or more folded families | 1385 / 5783 | 23.9 % | 17.1 % |
| folded statements carry unverified arms | 1005 / 5783 | 17.4 % | 14.1 % |
| the fold refused a candidate | 248 / 5783 | 4.3 % | 7.9 % |

Folded families per tune: median 1, p90 3, max 15; folded statements median 10,
max 660.

| fold refused because | occurrences |
|---|---|
| `an edge from copy 0 enters copy 1` | 162 |
| `the entry row's successors cross copies` | 79 |
| `the entry row does not fold` | 42 |
| `no room for the per-copy columns` | 7 |
| `an edge from copy 0 enters copy 2` | 2 |
| `an edge from copy 1 enters copy 0` | 1 |

Half of all built programs fold at least one family of sibling copies: the
unrolled-per-voice shape the anatomy documents is the population's normal form.
The cross-copy edge is 165 of the 293 refusals — the boundary Follin's
sound-effect subtunes hit, at scale.

---

## 10. Data-gated class sizes (plan §2)

Each row of the plan's backlog that was waiting on a population count. Rates are
over the whole 7,023-tune sample so they compose with section 2.

| class | tunes | raw | HVSC-weighted |
|---|---|---|---|
| residual-stack localisation | 826 / 7023 | 11.8 % | 4.1 % |
| `irq` entry through CINV (the KERNAL frame) | 108 / 7023 | 1.5 % | 0.6 % |
| `irq` entry through the hardware vector (raw `RTI` frame) | 0 / 7023 | 0.0 % | 0.0 % |
| PSID speed word non-zero | 1064 / 7023 | 15.2 % | 13.8 % |
| host-CIA cadence (the speed flag decides it) | 435 / 7023 | 6.2 % | 4.0 % |
| periodicity obstruction: certified, no state repeat in 30 s | 5051 / 7023 | 71.9 % | 87.3 % |
| `fold.outline` leaves an edge to a deleted block (S6 `KeyError`) | 32 / 7023 | 0.5 % | 0.1 % |
| an opcode cell whose alternatives exclude `RTS` | 198 / 7023 | 2.8 % | 3.4 % |
| any SMC opcode cell | 263 / 7023 | 3.7 % | 3.6 % |
| two planes: a $D000–$DFFF byte reached as chip and as RAM | 3 / 7023 | 0.0 % | 0.0 % |
| reads the RAM under I/O at all | 34 / 7023 | 0.5 % | 0.2 % |
| an `RTS` that matched no `JSR` (the RTS trick) | 144 / 7023 | 2.1 % | 0.7 % |

- Residual-stack localisation: section 7 puts the work inside `tick`, not between
  procedures.
- The hardware-vector path is exercised only by the refusal — no built entry uses
  it.
- The `fold.outline` deleted-block edge is presentation-only: all 32 tunes are
  already certified.
- Non-`RTS` opcode cells are three quarters of all 263 tunes with an SMC opcode
  cell, so the SLEIGH export's `RTS`-only overlay covers the minority of the
  class.
- Two planes (chip vs the RAM under it) is 3 tunes: the discriminating tune
  exists but the class is negligible.
- The `RTS` trick's 0.7 % weighted is close to design section 9.4's 1.7 % raw /
  0.4 % weighted from the prototype tracer.
- The periodicity obstruction (5,051 tunes at 30 s) is mostly answered by the
  period pass: 91 % of re-run tunes come back complete (section 6). What is left
  is 87 tunes capped at 400,000 ticks plus the 326 out of wall time — that
  residue, not the 5,051, is what a periodicity *proof* would address.

---

## 11. What the certified programs look like

Over the 5,783 built programs, at the 30 s horizon.

| metric | median | mean | p90 | p99 | max |
|---|---|---|---|---|---|
| executed sites | 442 | 447 | 679 | 1259 | 2005 |
| regions | 65 | 67 | 109 | 189 | 482 |
| procedures | 5 | 7 | 14 | 41 | 59 |
| S4 statements | 422 | 454 | 720 | 1489 | 3566 |
| ticks certified | 1503 | 1717 | 1799 | 11818 | 108269 |
| inputs pinned | 0 | 657 | 1503 | 8640 | 466627 |
| SMC cells | 3 | 61 | 27 | 1556 | 3047 |
| SMC cells a play site writes | 2 | 5 | 11 | 82 | 150 |

S4 statements per executed instruction site, per tune: median 0.98, p90 1.31,
p99 1.91, max 4.04, with 46 % of programs at or above 1.0 — the exemplars'
measured 1.0–1.6 is the upper half of the population. 58.6 % of built programs
pin no volatile input at all; the mean of 657 is a long tail of sample players
that read the chip every tick.

---

## 12. Cost and tracer throughput

Pass 1 (30 s horizon, 7,023 tunes, 60 workers, 1,093 s wall):

| stage | CPU hours | share | per tune (s) |
|---|---|---|---|
| trace | 13.83 | 78.0 % | 7.09 |
| front | 0.94 | 5.3 % | 0.48 |
| verify | 2.02 | 11.4 % | 1.03 |
| print | 0.93 | 5.2 % | 0.47 |
| **total** | **17.73** | 100 % | 9.09 |

Per-tune wall seconds: median 6.7, p90 20.6, p99 45.6, max 124.5.

Pass 2 (`--until-period`, 1,338 tunes, 300 s cap):

| stage | CPU hours | share | per tune (s) |
|---|---|---|---|
| trace | 39.15 | 96.8 % | 105.34 |
| front | 0.18 | 0.4 % | 0.48 |
| verify | 0.95 | 2.4 % | 2.57 |
| print | 0.13 | 0.3 % | 0.36 |
| **total** | **40.44** | 100 % | 108.81 |

Per-tune wall seconds: median 42.7, p90 300.1, max 303.0. 58.2 CPU-hours for the
whole campaign, 1 h 51 m of wall time at 60 workers.

Throughput at the two horizons (fixed per-tune overhead matters at 30 s and not
at song scale):

| | pass 1 (1,503 ticks median) | pass 2 (7,495 ticks median) | design §2's model |
|---|---|---|---|
| tracing | 199 ticks/s | 329 ticks/s | 277 k instructions/s |
| verifying | 1,367 calls/s | 13,518 calls/s | 10–16 k calls/s |

Verification matches the design's model once amortised (13.5 k calls/s); pass 1's
1,367 is the fixed `--prefix 2000` interpreter cross-check dominating a short run.
The tracer measured here does not: at design §9.3's mean of 292 instructions per
tick, 329 ticks/s is ≈ 96 k instructions/s, ≈ 2.9× slower than the model's 277 k,
which was measured on `tools/survey/tracer.py`, the prototype VM.

**The fast tracer (PR #271) is built.** The profile
refuted the gate's premise that the P-Code was the cost: the old tracer spent
5–6 % of its self time there and the rest on bookkeeping — base VM call chain
27–29 %, tracing `step` prologue 29–30 %, edges/frames/register masks 11–12 %,
per-op attribution 7–9 %, per-tick hash 1 % — seven Python calls and six dict
lookups an instruction, all re-deriving what a site already fixes. Making the
site the VM's cache key (a site's closure, per-op access sets, index domain,
register masks and edge cells resolve once; the loop only indexes them) gives, in
one process under `process_time`:

| tune | ticks | before | after | ratio |
|---|---|---|---|---|
| *Automatas* (defMON, CIA) | 12,029 | 788 ticks/s | 2,503 | 3.18× |
| Commando song 1 | 1,503 | 628 | 2,181 | 3.47× |
| Ghouls song 1 (Follin) | 1,503 | 1,227 | 3,696 | 3.01× |
| GoatTracker 2 *Do It Again* | 1,503 | 492 | 1,565 | 3.18× |
| JCH V20 *Guldkornekspressen* | 1,503 | 444 | 1,438 | 3.24× |
| *Experiment Zeta* `--until-period` | 6,000 | 590 | 1,972 | 3.34× |
| *Automatas*, 40,000 ticks | 40,000 | 757 | 2,350 | 3.10× |

≈ 480–580 k instructions/s against design §2's 277 k: the production tracer is
now 1.7–2.1× faster than the prototype VM the model was measured on. The `Trace`
is byte-identical (`trace.json` and every bulk array over all 82 traces the 50
certificates hold); recert 50/50, no field moved.

**Pass 1's `trace` column is S0 *and* S1, and only the S1 part moves.**
Re-running the first 200 tunes of the same seed-1 sample (identical files and
order, 24 workers, `--seconds 30`, 120 s cap) on both tracers:

| pass 1, 200 tunes | before | after |
|---|---|---|
| trace | 0.3163 CPU-h (85.1 %) | 0.2476 (81.7 %) |
| front | 0.0130 (3.5 %) | 0.0131 (4.3 %) |
| verify | 0.0245 (6.6 %) | 0.0246 (8.1 %) |
| print | 0.0176 (4.7 %) | 0.0178 (5.9 %) |
| **total** | **0.3715 CPU-h**, 6.69 s/tune | **0.3030**, 5.45 s/tune |
| wall: median / max | 5.3 s / 27.2 s | 3.0 s / 20.1 s |
| outcome | 134 certified, 6 diverged, 60 refused | identical |

Paired over the 134 tunes both runs certified, at identical tick counts (267,735
ticks): trace CPU 343.4 → 119.0 s, ×2.89; 780 → 2,250 ticks/s. Of that 119.0 s,
10.3 s is `machine._traced` — `pysidtracker`'s `playroutine_cadence` plus
`trace_init`, i.e. S0 entry discovery — so the tracer itself went 333.1 → 108.7 s,
×3.06, matching the instrument's figure.

Refused tunes' trace CPU does not move (775.1 → 765.4 s, ×1.01) because they
never reach the tracer: 46 of the 60 refuse `no entry` and spend 14.6 CPU-seconds
each in that same `_traced` call, and the eight `vector banked out` refusals 5.5 s
each — 68 % of the before run's pass-1 trace CPU (775.1 of 1,138.7) and 86 % of
the after run's (765.4 of 891.3). This prefix is refusal-heavy (30 % against the
sample's 17.4 %), mostly from one large-image family, so the whole-sample share
is smaller. Two new backlog rows: split S0 discovery from S1 tracing in the
sweep's stage columns, and reduce the discovery cost itself (14.6 s to decide
`no entry`).

Pass 2, first 50 certified tunes of the same sample, `--until-period --max-calls
400000`, 300 s cap, 24 workers:

| pass 2, 50 tunes | before | after |
|---|---|---|
| trace | 1.6111 CPU-h (98.4 %) | 1.1028 (88.5 %) |
| verify | 0.0204 (1.2 %) | 0.1277 (10.2 %) |
| **total** | **1.6378 CPU-h**, 117.9 s/tune | **1.2464**, 89.7 s/tune |
| certified | 34 | 44 |
| wall timeouts | 16 | 6 |
| wall: median / p90 | 27.9 s / 300.1 s | 11.0 s / 300.1 s |

Paired over the 34 both runs certified (1,552,645 ticks): trace CPU 1000.0 →
329.3 s, ×3.04; 1,553 → 4,714 ticks/s, whole pipeline ×2.56. Ten of the sixteen
wall timeouts become complete programs, which is why the after run's verify share
rises to 10 %.

Projections, linear in ticks (an assumption, not a measurement), with ×3.06
applied to the S1 part and S0 left where it is:

| campaign | pre-tracer | corrected |
|---|---|---|
| catalogue at a 30 s horizon | ≈ 131 CPU-h | ≈ 80–105 |
| catalogue at HVSC song length | ≈ 529 | ≈ 190–210 |
| `--until-period`, 300 s cap, same work | ≈ 1,520 | ≈ 500 |
| `--until-period`, 300 s cap, same budget | ≈ 1,520, 24 % timeouts | ≈ 1,160, ⅗ of the timeouts gone |

Median default-subtune length is 103 s; the design's own song-length estimate was
≈ 300 CPU-h. The 30 s range is wide because S0 is a large fixed share at a short
horizon and this prefix cannot size it for the whole sample; at song length
tracing dominates and the range closes. The last two rows are one measurement
read two ways.

---

## 13. Crashes

A crash is an undiagnosed exception: not a refusal, not a divergence, a bug.
Eighteen tunes (0.26 %) did not produce an answer.

| kind | exception | raised at | tunes | detail | example |
|---|---|---|---|---|---|
| timeout | `wall timeout` | driver | 7 | – | Cave_Fighter.sid |
| crashed | `RecursionError` | generated `tuneprog.py` | 5 | maximum recursion depth exceeded | Skate_Crazy.sid |
| crashed | `RuntimeError` | `vm.py:step` | 2 | `JAM at 8B06` | Mystery_Voyage.sid |
| crashed | `RecursionError` | `interp.py:ioload` | 2 | maximum recursion depth exceeded | Pro_Tennis_Simulator.sid |
| crashed | `KeyError` | `ssa.py:_frontiers` | 1 | `'L102D_20'` | Green_Tea.sid |
| crashed | `KeyError` | `lower.py:ctrl_expr` | 1 | `'expr'` | Examples.sid |

After the certificate — the program is certified, S5/S6 then failed, so the fault
is presentation only:

| exception | raised at | tunes | example |
|---|---|---|---|
| `KeyError` | `graph.py:preds_of` | 32 | Equinoxe_5.sid |
| `TrapError` | `ir.py:evalbin` | 2 | Foerklaedd_Gud_eta.sid |

Five classes, each a backlog row:

1. **`RecursionError` in the emitted program (7).** A tail call the IR wires as a
   `Call` recurses at run time: `cfg._no_recursion` lets tail edges through
   because they grow no machine frame, but the emitted Python grows a Python
   frame per edge. Two of the seven surface inside `interp.ioload`.
2. **`RuntimeError: JAM at $XXXX` out of `vm.py:step` (2).** A JAM opcode reached
   during tracing escapes as a bare `RuntimeError` where every other unsupported
   construct is a `Refusal`. Classification bug.
3. **`KeyError` in `ssa._frontiers` (1).** A block label with no dominance
   frontier entry.
4. **`KeyError: 'expr'` in `lower.ctrl_expr` (1).** A control expression the
   lowering does not have.
5. **`TrapError` out of `ir.evalbin` during S5/S6 (2).** Presentation evaluating
   an expression that traps.

The seven timeouts are tunes whose 30 s of music exceeds 120 wall seconds at
their cadence, not bugs; a faster tracer removes most of them. The period pass
adds one more of the JAM class (*Edge_of_Disgrace.sid*, `JAM at $00FE`), 16 more
`fold.outline` `KeyError`s after the certificate, and 326 wall timeouts at its
300 s cap. No new crash class appeared at the longer horizon.

---

## 14. Consequences

| finding | § | consequence |
|---|---|---|
| 344 of 399 divergences before tick 3 | 4 | the next correctness work is diagnostic, not more compute |
| `trap switch` (189) and `unverified`/`untaken` (78) were one cause: three front-end readings of computed control | 4 | 189 → 177 and 78 → 78 certified; 4 tunes left, on an unmatched `RTS` |
| `SidFactory_II/Laxity`, the largest divergence-only family (29/30, 380 HVSC tunes), certifies whole | 3, 4 | the class was those mechanisms, not the sibling closure |
| a real second schedule is 2.2 % of HVSC by weight (1.8 % armed by end of init); 0.8 % was misdiagnosed evidence | 5 | the largest addressable population, modelled in PR #272; admitting the 0.8 % moves 3 tunes to `certified`, the rest to `no entry` / `vector banked out` |
| tracing was 78 % of pass 1 CPU, 96.8 % of pass 2, and 2.9× off the design's model | 12 | fast tracer built: ×3.06 on S1, 1.7–2.1× faster than the prototype VM. Verification already matched the model |
| 118 complete at 30 s becomes 894 of 1,338 | 6 | the period pass is what buys completeness (99.4 % of certified programs by weight) |
| 31 of 1,338 tunes certified at 30 s did not at period scale | 6 | the 91.2 % rate is a horizon figure |
| every built interrupt entry is CINV | 8 | the raw `RTI` entry frame has no population |
| 52 % of built programs fold sibling copies | 9 | copy folding is the normal form; the cross-copy edge is its dominant boundary |
