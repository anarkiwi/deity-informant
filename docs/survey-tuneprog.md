# tuneprog at survey scale — the pipeline over the stratified HVSC sample

Companion to [tuneprog-decompiler-design.md](tuneprog-decompiler-design.md)
(section 9 is the *static* survey this one joins),
[tuneprog-plan.md](tuneprog-plan.md) (section 8 item 1 is this campaign) and
[tuneprog.md](tuneprog.md). This is the measurement that turns nine certified
exemplars into a distribution: what the whole pipeline does to 7,023 tunes, what
it refuses, where it diverges, what it costs.

Nothing here was fixed while it was measured. Every failing tune is a data
point with its reason; none was retried into passing, no family was admitted,
no certificate was committed.

Contents: 1 method · 2 outcomes · 3 by family · 4 failure classes · 5 refusals ·
6 completeness and the period pass · 7 the machine stack · 8 entry and cadence ·
9 copy folding · 10 data-gated class sizes · 11 what the programs look like ·
12 cost and the fast-tracer gate (12b the tracer, built) · 13 crashes · 14 what it changes

---

## 1. Method

**Sample.** The same stratified sample design section 9 traced: up to 30 tunes
per SIDId family, seed 1, drawn from `hvsc-tracker-catalog`'s `results.csv` over
HVSC #85 as installed — **7,023 tunes present on disk, 645 families**. HVSC #85 holds 61,157 `.sid`
files and the SIDId catalogue covers 60,388 of them, which is the population the
weighting maps onto (the static survey reported 646 families for the same 7,023
tunes; the catalogue has moved by one family since 2026-08-16). The
instrument is `tools/survey/tuneprog_sweep.py`, which imports `run.py`'s
`_sample`, so the two surveys sample the same files.

**One subtune per tune** — the header's `startsong`, the pipeline's default.
This keeps the cost bounded and matches design section 9; it means a
multi-subtune tune is measured on one of its songs, and the 20 % of HVSC with
more than one subtune is under-sampled in everything below.

**Two passes.** Pass 1 runs `pipeline.run` to a **30 s horizon**
(`--seconds 30`) over all 7,023. Pass 2 re-runs tunes pass 1 certified with
`--until-period --max-calls 400000`, so a tune whose state repeats is upgraded
from a horizon to a complete program. Defaults otherwise: trace closure, sibling
copies merged, S5/S6 text on, `--prefix 2000` interpreter cross-check.

**Pass 2 is a scaled sample**, and the scaling is stated rather than hidden: the
period run costs far more per tune than the horizon run (a tune with no repeat
traces to `--max-calls` or to the wall cap), so pass 2 takes the **first three
per family** of the certified tunes in path order — **1,338 of the 5,384** — a
nesting of the same seed-1 stratification. Families with three or fewer
certified tunes are covered entirely; the largest families contribute three
each, so pass 2's raw rates are more evenly spread across families than pass 1's
and its weighted rates carry more variance per family.

**Timeout.** 120 wall seconds per tune in pass 1, 300 in pass 2, fixed before
the run from a 50-tune pilot (median 3.9 s, p90 13.5 s, max 18.4 s) and not
tuned afterwards. A tune that hits it is recorded as `timeout`, never retried.
Each worker is capped at 8 GiB of address space; an over-run is recorded as
`oom`.

**Weighting.** Rates are given raw over the sample and re-weighted to the
catalogued HVSC population by family size, exactly as design section 9 does it:
a sampled tune of family *f* counts *N_f / n_f*, so a 10,720-tune family counts
357 per sampled tune and a 3-tune family counts 1. Weighted rates answer "what
happens to a tune drawn from HVSC", raw rates answer "what happens to a player
family".

**Reproduction.**

    python tools/survey/tuneprog_sweep.py --hvsc C64Music --results results.csv \
        --out horizon.jsonl --seconds 30 --jobs 60 --timeout 120
    python tools/survey/tuneprog_sweep.py --hvsc C64Music --results results.csv \
        --out period.jsonl --from horizon.jsonl --until-period --timeout 300
    python tools/survey/tuneprog_report.py --horizon horizon.jsonl \
        --period period.jsonl --results results.csv --hvsc C64Music

`--only FILE` restricts a run to the HVSC-relative paths that file lists, which
is how the corrections below re-measure one failure class without re-running the
sample.

Per-tune artefacts are pruned as each row is written; only the JSONL rows
survive, and neither they nor any certificate they describe are committed.

**Provenance.** Both passes ran on `main` at `6b8ef25`. #265 (equality
saturation in S6) merged while they ran; it is an opt-in `--eqsat` presentation
flag that never touches the certified S4 program, so nothing here is stale.

---

## 2. Outcomes

| outcome | tunes | raw | HVSC-weighted |
|---|---|---|---|
| certified | 5384 / 7023 | 76.7 % | 91.2 % |
| diverged | 399 / 7023 | 5.7 % | 2.5 % |
| refused | 1222 / 7023 | 17.4 % | 6.2 % |
| crashed | 11 / 7023 | 0.2 % | 0.0 % |
| timeout | 7 / 7023 | 0.1 % | 0.0 % |

**A tune drawn from HVSC certifies with probability 0.91 at a 30 s horizon** —
section 6 measures what a longer one costs that figure. Certified means the
emitted Python reproduced the tune's per-tick SID and schedule write lists
against the tracer for the whole horizon, with zero divergences — the same
acceptance test the 46 committed certificates pass. `diverged` means a
certificate exists and records a divergence; `refused` means the pipeline
diagnosed an unsupported construct (design principle 6) and produced nothing;
`crashed` means an undiagnosed exception, which is a bug in us.

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

The nine exemplars are not special: eight of their families certify 87–100 % of
a 30-tune draw without a line of family-specific code, which is the design's
principle 5 measured. **Galway is the exception at 47 %** — his 55 HVSC tunes
are hand-written engines that differ per game: fourteen of the thirty certify,
ten diverge (seven `trap switch`, three `trap unreached`) and six refuse. The
seed-1 draw did not include *Comic Bakery* itself, which the exemplar work
certifies separately.

Ranked by tunes-not-certified × family size, the families that cost HVSC the
most coverage are Soundmonitor (910 tunes' worth, all refusals),
Basic_Program (522, all refusals), *Unidentified* (521), Music_Assembler (425),
**SidFactory_II/Laxity (367, all divergences)**, Reflextracker (137, all
refusals) and CyberTracker_exe (130, all refusals). SidFactory II is the single
largest divergence-only family and 23 of its 29 failures are one class
(`trap unverified`).

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

**Divergences are immediate, not drift.** Of the 399, 93 fail in init (tick −1),
123 at tick 0, 67 at tick 1 and 61 at tick 2 — **344 of 399, 86 %, before tick
3** — and only 55 fail later. These are not horizons that were too short or
tunes that slowly desynchronise: they are systematic modelling gaps the very
first tick exposes, which is good news for fixing them and bad news for reading
the current 91 % as a ceiling that only more compute would raise.

**`trap switch` (189 tunes, 2.7 % raw, 0.5 % weighted) is the largest class.**
`emit._term` gives a `Switch` a case per value the trace saw and traps
everything else, so this is the emitted program computing a dispatch value the
trace never produced at that block. It is concentrated in whole families —
Virtuoso 29/30, Ben Daglish/Gremlin 25, Element114Studio 25, Fred Gray 15,
Tiny/Sound Images 12, Galway 7 — and correlates with self-modification: 159 of
the 189 (84 %) have a play site writing an instruction byte against 54 % of
certified programs, and 116 of the 189 also folded sibling copies. Association
only; the cause is not diagnosed here, and it is a backlog row.

> **Correction (2026-08-22, Q6 — [tuneprog-plan.md](tuneprog-plan.md) §5).**
> Diagnosed, and it was three mechanisms in the front end's reading of computed
> control, none of them `emit._term`: a `JMP (ind)` whose own operand the
> program patches dispatched on the **pointer** while its cases were the
> observed **targets** (Virtuoso, Element114Studio, Fred Gray, Galway,
> Tiny/Sound Images); a patched **branch offset of zero** names the address
> after the instruction, which the "every successor but the fall-through" rule
> discarded (Ben Daglish/Gremlin, Prosonix); and the **copy index** was stepped
> before the arm that advances the run was chosen, so a family's exit carried
> `v = k` (`Bitfrost.sid`, the first-divergence example above). Re-run over the
> same 189 at 30 s: **188 `trap switch` + 1 `untaken` → 177 certified**, with 4
> `trap switch` left (all one new shape, an unmatched `RTS` return), 4 `io`, 2
> `input exhausted` and 2 wall timeouts. **The association stated here is
> refuted as a cause**: what the class is made of is self-modification of
> *control*, not of code in general. Classified on `main` at 30 s by the
> instruction that dispatches at the first divergence: `JMP (ind)` 110, a
> patched branch 62, an unmatched `RTS`/`RTI` 4, 13 the reconstruction does not
> resolve; reading the emitted scrutinee instead gives 98 / 77 / 4 with 9 on the
> copy index and 1 `untaken`. Copy folding is a bystander, under ten of the 189.

**The `io` list (73) fails at init** in 41 of 73 cases: the program's init
writes to VIC/CIA differ from the trace's. No tune in the sample diverged on
the *SID* write list; every divergence is a trap or the I/O list. Concentrated in
Geir_Tjelta/SIDSys18.6 (17), Heathcliff/DigitalArts (11) and Novaload (11).

**`trap unverified` (47) and `trap untaken` (31)** are the closure boundary
showing up as failure: an arm lifted from a sibling copy, or a branch direction
nothing executed, that the program then reaches while replaying its own trace.
SidFactory II/Laxity is 23 of the 47.

> **Correction (2026-08-22, Q6).** Not the closure boundary. Re-run over the
> same 78 at 30 s, **78 certified, 0 diverged**, with no work on `siblings` or
> `closure` at all: both classes are the two control mechanisms above seen from
> the other side — a patched `JMP (ind)` whose pointer value matched a table
> entry `jumptab.enumerate_targets` had closed as an `unverified` arm, and a
> zero branch offset whose arm the same closure supplied because the case set
> had dropped it. SidFactory II/Laxity certifies whole.

**`trap input exhausted` / `input mismatch` (38)** are volatile-input replay:
the program consumed pinned inputs in a different order or number than the trace
recorded them. Novaload and Heathcliff/DigitalArts lead.

---

## 5. Refusals

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

Every refusal is diagnosed; none is a silent approximation. **The second
interrupt is 45 % of all refusals and 3.0 % of HVSC by weight** — the single
biggest addressable population, and exactly the prototype plan section 8 item 3
already scopes (JCH's NMI sample mixer). `no entry` and `vector banked out`
together (475) are the `play == 0` population whose installed vector the 6510
port does not dispatch through, or which installed none: BASIC containers,
digi players and RSID main loops. `recursion` (118) is a `JSR` cycle in the
call graph, which `cfg._no_recursion` refuses by design. **The
`second interrupt source armed` row is corrected in 5b below**, which is added
beside it rather than replacing it.

### 5b. Correction (2026-08-22): that row counted evidence, not a schedule

`find_entries` refused any write to the CIA #2 Timer-A latch (`$DD04`/`$DD05`)
or to the NMI vector (`$0318`/`$0319`). Neither makes an NMI possible. A tune
has a second schedule iff a CIA #2 source can fire: its ICR (`$DD0D`) has been
written with bit 7 and one of bits 0-4 — a mask the chip *accumulates*, so the
last write does not give it — and, for a timer source, that timer is started
(`$DD0E`/`$DD0F` bit 0). CIA #2's interrupt line is the 6510's NMI, so an
enabled source that can have its event is the refusal whatever vector carries
it, and a vector installed over no such source is dead, exactly as
`vector_gate` already treats a dead `$FFFE` write. RESTORE is the other NMI
source and `sidplayfp` never presses it.

Re-measured over the same 547 tunes — `machine.nmi_gate` over the CIA #2 that
each tune's own init leaves, with every tune driven to the gate, so this is the
rule's verdict on the machine rather than the pipeline's refusal order; weights
are over the whole 7,023 sample, so they compose with section 2:

| what the rule says of the tune | tunes | raw | HVSC-weighted |
|---|---|---|---|
| **armed** — a CIA #2 source can fire | 311 / 7023 | 4.4 % | 1.8 % |
| … which the init trace's own last ICR/CRA writes already show | 264 / 7023 | 3.8 % | 1.6 % |
| … which only the traced CIA state shows: all 47 are Timer **B**, whose `$DD0F` start bit `InitTrace` does not carry | 47 / 7023 | 0.7 % | 0.2 % |
| **dead** — no source can fire, so the latch or the vector was the whole evidence | 81 / 7023 | 1.2 % | 0.8 % |
| **undecided** — init never returns, so the gate is never reached; still refused, now as `init runaway` | 154 / 7023 | 2.2 % | 0.4 % |
| **undecided** — the tracer faulted | 1 / 7023 | 0.0 % | 0.0 % |

**The misdiagnosed class is 81 tunes, 0.8 % of HVSC by weight**, against design
section 9.2's ≈ 1 % estimate for the vector-only/unarmed share. Nine of the 81
are tunes whose *last* ICR write enables Timer B while the accumulated mask does
not: the chip never saw that write — it lands with I/O banked out, or on an init
path the second emulation takes and the tracer does not — which is why the
tracer's own CIA is the authority and the init trace only ever the cheap
refusal.

The pipeline counts fewer than 311 armed, because `find_entries` settles the
entry before the tracer runs: 29 armed tunes refuse first with the `no entry` or
`vector banked out` they would have got anyway.

Putting all 547 back through the 30 s pipeline gives **3 certified, 1 diverged
(*Rally_Cross*, an `io` write list that differs at init), 1 crashed
(*Original_Tetris-Game*, `JAM at $0002`) and 542 refused**, and these
whole-sample rows:

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

**The second interrupt is 2.2 % of HVSC by weight, not 3.0 %** — 1.6 % armed by
the end of init plus 0.6 % armed during play — and the 0.8 % it over-counted is
released almost entirely into refusals that were already there and were being
shadowed: `no entry` and `vector banked out` take 236 of the 274 tunes, being
`play == 0` containers whose installed vector the port does not dispatch
through. **Three tunes certify.** The nine `nmi armed in play` tunes are the
fail-closed half of the rule earning its keep: each enables the CIA #2 ICR in
init and starts the timer only once the music is running (Hubbard's *Mr_Meaner*
and *Kings_of_the_Beach_intro*, two Soundmonitor tunes, GoatTracker V1, Hans
Siemons, Odie/Cosine, Georg Brandt, Vibrants/JO).

One thing the change exposed, because nothing in this class used to be traced:
`playroutine_cadence` falls through from CIA #1 to CIA #2 for the play latch and
treats an unwritten ICR as the armed KERNAL default — right for CIA #1, wrong
for CIA #2 — so a dead CIA #2 latch was being handed back as the tick period.
`_cadence` now takes a CIA period only when it is CIA #1's; *Jazzpjazz* is the
tune that showed it (1,799 ticks of `pal_host_cia`, not 2,868 of a `$DD04`
latch nothing dispatches), and `sidplayfp` is the judge: the gaps between the
interrupts the oracle attributes its writes to are whole multiples of the host
CIA's period and not of that latch.

---

## 6. Completeness and the period pass

At the 30 s horizon:

| certified program | tunes | raw | HVSC-weighted |
|---|---|---|---|
| complete (a state repeat proved inside the horizon) | 333 / 5384 | 6.2 % | 4.3 % |
| a repeat was seen but the program is not complete | 0 / 5384 | 0.0 % | 0.0 % |
| no repeat: horizon-capped | 5051 / 5384 | 93.8 % | 95.7 % |

**93.8 % of certified programs are horizon-capped at 30 s** — a state repeat
inside 30 s is the exception, not the rule, so the period pass is where
completeness comes from.

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

**Given the tracing budget, a certified program is complete 91 % of the time**
(99.4 % weighted), and over the whole pass-2 population — timeouts included —
894 of 1,338 tunes (66.8 % raw, **81.2 % weighted**) end as complete programs.
The 118 that were already complete at 30 s become 894: the period pass is worth
a 7.6× increase in completeness and is the only thing that buys it. Music
traced to the repeat: median 118 s, p90 432 s, max 5,725 s (7,495 ticks median,
400,000 max).

**The horizon is not free of correctness information.** 31 of the 1,338 tunes
certified at 30 s and did *not* certify at period scale:

| what a longer horizon found | tunes | reason |
|---|---|---|
| diverged | 16 | 7 `trap unreached` (e.g. *Space_Patrol.sid* at tick 7,935), 5 `input mismatch`, 3 `trap switch` (*Butcher_Hill.sid* at tick 3,456), 1 `io` list |
| refused | 14 | 13 `recursion` — a `JSR` cycle the 30 s trace never closed — and 1 `play runaway` |
| crashed | 1 | `RuntimeError: JAM at $00FE` (*Edge_of_Disgrace.sid*) |

So **section 2's 91.2 % weighted certification rate is a 30 s figure**, and
about 2.3 % of the tunes it counts would not survive a song-length horizon on
this evidence. It is measured at the horizon it states, not extrapolated.

The 326 timeouts are the honest cost boundary: at 300 wall seconds a tune with a
long period simply does not get there, and they are spread thinly (no family
contributes more than its three).

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

**A tune drawn from HVSC whose program is built keeps its stack with
probability 0.044.** The residual
is whole-program by construction (one unplaceable read keeps `SP` everywhere),
and the measurement says the unplaceable read is in `tick` itself 62 % of the
time and in `init` 27 % — so an interprocedural frame layout, the plan's
"residual-stack localisation" row, would have to localise inside the tick to
win most of this class, not merely keep helpers out of it. 819 of the 826 have
no computable depth at all (`Frame.events is None`: the procedure's stack is not
covered by its own pushes), so "how deep" is not the question for this class —
"whose frame" is.

---

## 8. Entry and cadence

Over the 5,783 built programs. Note the population: a tune whose entry is an
installed handler refuses far more often than one with a header `play`, so this
table is the *post-refusal* topology and under-counts interrupt entries relative
to design section 9.2 (which measured 8.7 % `irq` before any refusal).

| entry | tunes | raw | HVSC-weighted |
|---|---|---|---|
| `sub` (header play, JSR each tick) | 5675 / 5783 | 98.1 % | 99.1 % |
| `irq` (installed handler) | 108 / 5783 | 1.9 % | 0.9 % |
| … through the KERNAL vector (CINV) | 108 / 5783 | 1.9 % | 0.9 % |
| … through the hardware vector | 0 / 5783 | 0.0 % | 0.0 % |

**Every interrupt entry the pipeline built is a CINV entry** — 108 of 108, and
none through `$FFFE`. The `vector banked out` refusal (184 tunes) is where the
raw-vector population went: those tunes wrote `$FFFE` with the KERNAL mapped, so
the port dispatches through `$0314` and the write is dead. The KERNAL-frame
convention (`machine.entry_frame`, the `$FF48` prologue's A/X/Y) therefore
carries the entire installed-handler class as measured, and the raw `RTI` frame
has no population here at all.

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

The speed-flag work of 2026-08-21 is load-bearing for **435 tunes, 4.6 % of HVSC
by weight**: they program no timer, so their cadence is the host's CIA #1
Timer-A latch and nothing else decides it. The other 614 tunes with a non-zero
speed word arm their own timer, where the flag is redundant.

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

Half of all built programs fold at least one family of sibling copies, so the
unrolled-per-voice shape the anatomy documents is the population's normal form,
not an exemplar quirk. The cross-copy edge is 165 of the 293 refusals — the same
boundary Follin's sound-effect subtunes hit, at scale.

---

## 10. Data-gated class sizes (plan section 5)

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

Reading them:

- **Residual-stack localisation** is worth 826 tunes / 4.1 % of HVSC, and
  section 7 says the work is inside `tick`, not between procedures.
- **The raw `RTI` frame has no population** — every built interrupt entry is
  CINV. The hardware-vector path is exercised only by the refusal.
- **`fold.outline`'s deleted-block edge is 32 tunes (0.5 %)**, all of them
  already certified: it is presentation-only and the fix is the one condition
  the plan's row already diagnoses.
- **Non-`RTS` opcode cells are 198 tunes (3.4 % weighted)** — three quarters of
  all 263 tunes with an SMC opcode cell. The SLEIGH export's `RTS`-only overlay
  therefore covers the minority of the class, not the bulk of it.
- **Two planes (chip vs the RAM under it) is 3 tunes.** The discriminating tune
  the plan was waiting for exists but the class is negligible; 34 tunes touch
  the RAM under I/O at all.
- **The `RTS` trick is 144 tunes (0.7 % weighted)**, close to design section
  9.4's 1.7 % raw / 0.4 % weighted from the prototype tracer.
- **The periodicity obstruction is 5,051 tunes at 30 s (87.3 % weighted), and
  the period pass answers most of it**: 91 % of the tunes it re-ran to a repeat
  came back complete (section 6). What is left is 87 tunes capped at 400,000
  ticks plus the 326 that ran out of wall time — that residue, not the 5,051, is
  what a periodicity *proof* would have to address.

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

Per tune, S4 statements per executed instruction site: median **0.98**, p90
1.31, p99 1.91, max 4.04, and 46 % of programs are at or above 1.0 — the
exemplars' measured 1.0–1.6 is the upper half of the population, not an outlier.
**58.6 % of built programs pin no volatile input at all**: they are closed
functions of their own state, and the mean of 657 is a long tail of sample
players that read the chip every tick.

---

## 12. Cost, and the fast-tracer gate

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

Per-tune wall seconds: median 42.7, p90 300.1, max 303.0. **58.2 CPU-hours for
the whole campaign**, 1 h 51 m of wall time at 60 workers.

**Measured throughput.** Two figures, because the fixed per-tune overhead
matters at a 30 s horizon and not at song scale:

| | pass 1 (1,503 ticks median) | pass 2 (7,495 ticks median) | design §2's model |
|---|---|---|---|
| tracing | 199 ticks/s | **329 ticks/s** | 277 k instructions/s |
| verifying | 1,367 calls/s | **13,518 calls/s** | 10–16 k calls/s |

**Verification matches the design's model exactly once amortised** (13.5 k
calls/s); pass 1's 1,367 is the fixed `--prefix 2000` interpreter cross-check
dominating a short run, not a slow verifier. **Tracing does not match.** At
design §9.3's mean of 292 instructions per tick, 329 ticks/s is ≈ **96 k
instructions/s**, about **2.9× slower** than the model's 277 k — which was
measured on `tools/survey/tracer.py`, the prototype VM, and does not describe
the production `Tracer`.

Projections (linear in ticks, an assumption rather than a measurement):
the whole catalogued HVSC costs **≈ 131 CPU-hours at this 30 s horizon**,
**≈ 529 CPU-hours at each tune's HVSC song length** (median default-subtune
length 103 s) against the design's ≈ 300, and **≈ 1,520 CPU-hours** for a
`--until-period` pass with the same 300 s cap and its 24 % timeouts.

**The fast-tracer gate (plan section 8 item 7) fires.** Tracing is 78 % of pass
1's CPU and **96.8 % of pass 2's**; the verifier — the part that must be exact —
is 11 % and 2.4 %. A 3× tracer would put the production tracer where the design
already assumed it was, take the song-length campaign from ≈ 529 to ≈ 180
CPU-hours, remove most of the 333 wall-timeouts across both passes, and change
nothing else in the pipeline.

### 12b. Correction (2026-08-22): the fast tracer is built, and "trace" was two costs

The gate fired and the work is done ([tuneprog-plan.md](tuneprog-plan.md) §8
item 7, §5b Q7, PR #271). Two things the table above could not say.

**1. The tracer is 3.0–3.5× faster, and the design's model is now beaten.** The
profile refuted the row's own premise: the old tracer spent **5–6 %** of its self
time in the compiled P-Code and the rest on bookkeeping around it — the base VM
call chain 27–29 %, the tracing `step` prologue another 29–30 %, edges, frames
and register masks 11–12 %, per-op attribution 7–9 %, the per-tick hash **1 %**.
Seven Python calls and six dict lookups an instruction, all re-deriving what a
site already fixes. Making the site key the VM's cache key — so a site's closure,
per-op access sets, index domain, register masks and edge cells resolve once and
the loop only indexes them — gives, in one process under `process_time`:

| tune | ticks | before | after | ratio |
|---|---|---|---|---|
| *Automatas* (defMON, CIA) | 12,029 | 788 ticks/s | **2,503** | **3.18×** |
| Commando song 1 | 1,503 | 628 | **2,181** | **3.47×** |
| Ghouls song 1 (Follin) | 1,503 | 1,227 | **3,696** | **3.01×** |
| GoatTracker 2 *Do It Again* | 1,503 | 492 | **1,565** | **3.18×** |
| JCH V20 *Guldkornekspressen* | 1,503 | 444 | **1,438** | **3.24×** |
| *Experiment Zeta* `--until-period` | 6,000 | 590 | **1,972** | **3.34×** |
| *Automatas*, 40,000 ticks | 40,000 | 757 | **2,350** | **3.10×** |

**≈ 480–580 k instructions/s** against design §2's 277 k — the production tracer
is now 1.7–2.1× faster than the prototype VM that model was measured on. The
`Trace` is byte-identical: `trace.json` and every bulk array over all 82 traces
the 50 certificates hold; recert 50/50 before and after, no field moved.

**2. Pass 1's `trace` column is S0 *and* S1, and only the S1 part moves.**
Re-running the **first 200 tunes of the same seed-1 sample** (identical files and
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

Paired over the 134 tunes both runs certified, at identical tick counts
(267,735 ticks): **trace CPU 343.4 → 119.0 s, ×2.89; 780 → 2,250 ticks/s**. Of
that 119.0 s, **10.3 s is `machine._traced`** — `pysidtracker`'s
`playroutine_cadence` plus `trace_init`, which is entry discovery, S0 — so the
tracer itself went **333.1 → 108.7 s, ×3.06**, exactly the instrument's figure.

The *refused* tunes' trace CPU does not move at all — **775.1 → 765.4 s,
×1.01** — because they never reach the tracer: 46 of the 60 refuse `no entry`
and spend 14.6 CPU-seconds each in that same `_traced` call, and the eight
`vector banked out` refusals 5.5 s each. In this prefix that is 68 % of pass 1's
trace CPU. The prefix is refusal-heavy (30 % against
the sample's 17.4 %) and its refusals come mostly from one large-image family,
so the whole-sample share is smaller — but §12's "tracing is 78 % of pass 1's
CPU" is 78 % of **S0 + S1**, and only the S1 part was this gate's subject.
**Splitting S0 discovery from S1 tracing in the sweep's stage columns is a new
backlog row**, and so is the discovery cost itself: 14.6 s to decide `no entry`.

**Pass 2 is where the gate paid.** The first 50 certified tunes of the same
sample, `--until-period --max-calls 400000`, 300 s cap, 24 workers:

| pass 2, 50 tunes | before | after |
|---|---|---|
| trace | 1.6111 CPU-h (98.4 %) | 1.1028 (88.5 %) |
| verify | 0.0204 (1.2 %) | 0.1277 (10.2 %) |
| **total** | **1.6378 CPU-h**, 117.9 s/tune | **1.2464**, 89.7 s/tune |
| certified | 34 | **44** |
| wall timeouts | **16** | **6** |
| wall: median / p90 | 27.9 s / 300.1 s | 11.0 s / 300.1 s |

Paired over the 34 both runs certified (1,552,645 ticks): **trace CPU 1000.0 →
329.3 s, ×3.04; 1,553 → 4,714 ticks/s**, the whole pipeline ×2.56. **Ten of the
sixteen wall timeouts go away** and become complete programs — which is why the
*after* run's verify share rises to 10 %: there are ten more programs to verify,
each longer than the ones that were already finishing.

**Projections**, on §12's own linear-in-ticks assumption, with ×3.06 applied to
the S1 part and S0 left where it is:

| campaign | §12 | corrected |
|---|---|---|
| catalogue at a 30 s horizon | ≈ 131 CPU-h | **≈ 80–105** |
| catalogue at HVSC song length | ≈ 529 | **≈ 190–210** |
| `--until-period`, 300 s cap, same work | ≈ 1,520 | **≈ 500** |
| `--until-period`, 300 s cap, same budget | ≈ 1,520, 24 % timeouts | **≈ 1,160, ⅗ of the timeouts gone** |

The 30 s range is wide because S0 is a large fixed share at a short horizon and
this prefix cannot size it for the whole sample; at song length tracing dominates
and the range closes. The last two rows are the same measurement read two ways:
the pass costs a third of what it did for the work the old one finished, or
two-thirds of it while finishing far more.

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

And after the certificate — the program is certified, S5/S6 then failed, so the
fault is presentation only:

| exception | raised at | tunes | example |
|---|---|---|---|
| `KeyError` | `graph.py:preds_of` | 32 | Equinoxe_5.sid |
| `TrapError` | `ir.py:evalbin` | 2 | Foerklaedd_Gud_eta.sid |

Five classes, each a backlog row:

1. **`RecursionError` in the emitted program (7 tunes).** A tail call the IR
   wires as a `Call` recurses at run time; `cfg._no_recursion` lets tail edges
   through because they grow no machine frame, but the emitted Python grows a
   Python frame per edge. Two of the seven surface inside `interp.ioload`
   instead, on the interpreter path.
2. **`RuntimeError: JAM at $XXXX` out of `vm.py:step` (2 tunes).** A JAM opcode
   reached during tracing escapes as a bare `RuntimeError` where every other
   unsupported construct is a `Refusal`. Classification bug, not a decompiler
   bug.
3. **`KeyError` in `ssa._frontiers` (1).** A block label with no dominance
   frontier entry.
4. **`KeyError: 'expr'` in `lower.ctrl_expr` (1).** A control expression the
   lowering does not have.
5. **`TrapError` out of `ir.evalbin` during S5/S6 (2).** Presentation
   evaluating an expression that traps.

The seven timeouts are not bugs — they are tunes whose 30 s of music exceeds
120 wall seconds at their cadence — but they are also not answers, and a faster
tracer removes most of them.

The period pass adds one more of the same JAM class (*Edge_of_Disgrace.sid*,
`JAM at $00FE`), 16 more `fold.outline` `KeyError`s after the certificate, and
326 wall timeouts at its 300 s cap. No new crash class appeared at the longer
horizon.

---

## 14. What this campaign changes

Measured, in order of population:

1. **Divergences are first-tick modelling gaps, not horizon effects** — 344 of
   399 before tick 3. The next correctness work is diagnostic, not more compute.
2. **`trap switch` (189 tunes) is the largest single failure class** and takes
   whole families with it (Virtuoso, Daglish, Element114Studio, Fred Gray).
   *Corrected 2026-08-22 (Q6, §4): three front-end readings of computed control
   — the pointer of a patched `JMP (ind)`, a zero patched branch offset, and the
   copy index stepped on the wrong arm. 189 → 177 certified, and the 78
   `unverified`/`untaken` tunes of item 7 go with them, 78 → 78. What is left of
   the class is 4 tunes on an unmatched `RTS`.*
3. **The second interrupt is 45 % of refusals and 3.0 % of HVSC by weight** —
   the largest addressable population anywhere in this document, and already
   scoped as plan section 8 item 3. **Corrected (section 5b): 2.2 % by weight is
   a real second schedule and 0.8 % was misdiagnosed evidence — a CIA #2 latch
   or an NMI vector that no armed source can dispatch. Admitting that 0.8 %
   soundly moves 3 tunes into `certified` and the rest into the `no entry` /
   `vector banked out` refusals it had been shadowing, so the addressable
   population is the 1.8 % that really is armed** — still the largest, and still
   item 3's prototype.
4. **The fast tracer is warranted**: 78 % of pass 1's CPU and 96.8 % of pass
   2's; verification already matches the design's model (13.5 k calls/s) and
   tracing is 2.9× off it.
5. **The period pass is what buys completeness**: 118 complete at 30 s becomes
   894 of 1,338, and 99.4 % of certified programs by weight. Nothing else in the
   pipeline moves that number.
6. **The 30 s certification rate is a horizon figure**: 31 of 1,338 tunes that
   certified at 30 s did not at period scale.
7. **`SidFactory_II/Laxity`** is the largest divergence-only family (29/30, 380
   HVSC tunes), 23 of them one class (`trap unverified`). *Corrected 2026-08-22
   (Q6, §4): the family certifies whole; the class was item 2's mechanisms, not
   the sibling closure.*
8. **The raw `RTI` entry frame has no population**; every built interrupt entry
   is CINV.
9. **Copy folding is the normal form** (52 % of built programs), and the
   cross-copy edge is its dominant boundary at scale.
