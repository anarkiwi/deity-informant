# deity-informant requirements / gaps

Hand-off spec for features missing from the `deity-informant` P-Code lifter+VM
that block faithful execution of certain C64 `.sid` tunes. tumbler-snapper's
recovery prototype (`prototypes/recover.py`) uses deity as its only
execution/observation VM, so these gaps cap the tunes tumbler-snapper can recover
losslessly.

Source cited below is the installed copy
`/tmp/venv/lib/python3.12/site-packages/deity_informant/` (identical to
`/scratch/anarkiwi/re/deity-informant/deity_informant/`); `file:line` refers to
that tree. Do not modify tumbler-snapper code against this doc — it targets
deity-informant.

## Priority ordering

1. **Gaps 1 + 2 + 3 together** — unblock main-loop-driven RSID (the entire class
   that does synthesis in a non-returning main loop, clocked by an IRQ). Motivating
   tune: lft `A_Mind_Is_Born`. None of the three alone is sufficient: it needs
   CIA-timer readback (1), a continuous executor (2), and a deterministic reset
   environment incl. CIA timer phase (3). Implement as one coherent milestone.
2. **Gap 3(a) alone (KERNAL/CINV environment)** — lower urgency: handler-driven
   RSID (`play == $0000`, IRQ handler does the work, balances an RTI) *already
   works* via recover.py's `$EA31/$EA81` restore-RTI + A/X/Y push shim
   (`_drive_handler`/`_install_kernal_stubs`, recover.py:408-463; validated on
   `Double_Dragon_2`, `P_A_S_S_Demo_3`). A faithful environment would supersede
   the shim but unblocks no new tunes on its own.
3. **Gap 4 (SID osc3/env3 readback fidelity)** — lowest. Affects only tunes that
   read `$D41B/$D41C` as modulation/entropy; deity's cycle approximation is
   already "good enough" for tunes that merely gate on it, and a full fix needs a
   SID oscillator model.

---

## Scope & sequencing (verified against the code)

Every gap's headline acceptance criterion — "write stream matches the
`sidplayfp`/`sidtrace` oracle over N >= 3000 frames" — depends on an oracle
harness that **does not yet exist in this repo** (`sidplayfp`/`sidtrace` appear
only in test comments). That harness is a prerequisite milestone (M0), not a
footnote. It is largely a **port**: `/scratch/anarkiwi/cbm/pysidtracker` already
provides the full bridge (`make_oracle_fixtures` / `oracle_grid` /
`sidtrace_grid` / `sidtrace_cadence`, Dockerized `sidplayfp`), runtime HVSC fetch
that never commits `.sid` (`resolve_tune`/`fetch_tune` — same licensing
constraint as the ROMs, solved), and `prototype/oracle_match.py` **already drives
this exact `PcodeVM`/`run_sub`/`run_irq`/`lift`** against the oracle grid, listing
`A_Mind_Is_Born` as a target. See [oracle-testing.md](
../../../cbm/pysidtracker/docs/oracle-testing.md).

**Gap 2 perf is feasible in pure Python (spike done).** Raw `PcodeVM.step` on a
warm-cached `A_Mind_Is_Born`-shaped inner loop (reading `$DC04` + `$D41C`, so the
Gap-1/4 volatile cost is included) ran **0.52M instr/s / 2.28M cyc/s**; 3000 PAL
frames (~59M cyc) extrapolates to **~26s vs the 60s CLAUDE.md budget (2.3x)**. The
loop averaged 4.4 cyc/instr; a branch-heavier real loop (~3 cyc/instr) lands
nearer **~38s (1.6x)**, and CI Docker erodes that further. Conclusion: **no
compiled-trace rewrite needed up front** — but treat 3000 frames as the ceiling,
keep `run_continuous`'s step loop tight (hoist `vm.step`/`reg` locals; `step()`
rebuilds the `k` tuple and does a `cache.get` per instruction — the cheapest thing
to optimize if CI is tight), and keep a compiled-trace path as a documented
fallback, not a prerequisite.

**Gap 3 splits.** 3a (`$01` banking + KERNAL/CINV path) is mechanical and
*already worked around* in recover.py — deferrable, unblocks no new tunes alone.
3b (reset-state CIA Timer A latch+phase matching sidplayfp) is the crux for
entropy tunes and is coupled to Gap 1's phase — it sits on the critical path.

### Milestones

| Milestone | Content | Gate |
|---|---|---|
| **M0** Oracle harness | port `oracle_match.py` -> `tests/test_oracle.py`; `pysidtracker` test-only dep; CI `oracle` job (`@pytest.mark.oracle`). `run_sub`/`run_irq` path lands now: green on returning tunes, red (`DIFF`) baseline on `A_Mind_Is_Born`. | none — do first |
| **M1** Main-loop RSID | Gap 1 + Gap 2 + Gap **3b** (reset CIA phase). Turns the `A_Mind_Is_Born` baseline green. | M0 for e2e; Gap 1 unit-testable alone |
| **M2** KERNAL env | Gap **3a** ($01 banking / CINV) — retires the recover.py stub. | independent, deferrable |
| **M3** osc3/env3 | Gap 4 — capped to voice-3 readback. | independent, anytime |

---

## Gap 1 — CIA Timer A/B readback not modeled

**Motivation / affected cases.** lft `A_Mind_Is_Born`
(`/scratch/preframr/hvsc/C64Music/MUSICIANS/L/Lft/A_Mind_Is_Born.sid`; RSID, init
`$08B2`, play `$0000`). A free-running cycle-exact synth: its zero-page main loop
reads CIA1 Timer A low byte `$DC04` as live entropy every iteration, e.g.
`LDA $DC04; ORA $D41C; ...; STA ($CB),Y; INC $CB; BNE ...`. Any tune that samples
`$DC04/$DC05` (CIA1) or `$DD04/$DD05` (CIA2) for timing/entropy is affected.

**Current behavior.** `PcodeVM._rd` (vm.py:74-97) models volatile IO only for:
`$D019` (vicirq, vm.py:77-78), `$DC0D` (ciaicr, read-clears, vm.py:79-82),
and — gated by `self.volatile and 0xD011 <= addr <= 0xD41C` (vm.py:83) — `$D012`
= `(cycles // 63) % 312 & 0xFF` (vm.py:88-89), `$D011` raster hi bit (vm.py:90-91),
and `$D41B`/`$D41C` = `(cycles >> 3) & 0xFF` (vm.py:92-93). `$DC04/$DC05` and
`$DD04/$DD05` fall through to `mem[a]` (vm.py:94-95): a **stale RAM** read. There
is no CIA timer latch, control-register, or countdown state anywhere in the VM
(`__slots__`, vm.py:58, has none). Execution therefore diverges from hardware for
any read of a live CIA timer register.

**Required behavior.** Model the CIA Timer A (and B) value as a cycle-derived
volatile read, decrementing at the system clock from its latch under the control
register (continuous/reload vs one-shot, start/stop, and — for readback fidelity —
the standard bug where reading `$xx05` mid-underflow needs latching). For a
continuous timer with 16-bit latch `L` started at cycle `c0`, the counter at cycle
`c` is `L - ((c - c0) mod (L + 1))`; `$xx04` returns its low byte, `$xx05` the high.
Cover CIA1 `$DC04/$DC05` and CIA2 `$DD04/$DD05`. Honor the case where **no latch is
set in init** (Gap 3): the timer is the KERNAL/reset-initialized free-running
Timer A, so its latch and phase come from the reset environment, not the tune.

**Acceptance criteria.**
- With the continuous executor (Gap 2) and reset environment (Gap 3), reads of
  `$DC04` during `A_Mind_Is_Born` return values consistent with a Timer A counting
  down at the system clock from the reset latch, and the tune's
  `$D400..$D418` write stream matches the `sidplayfp`/`sidtrace` oracle over N
  frames (N >= 3000).
- A unit test: set CIA1 Timer A latch `L` and start it; assert `_rd(0xDC04,1)` /
  `_rd(0xDC05,1)` track `L - ((cycles - c0) mod (L+1))` as `vm.cycles` advances,
  for continuous and one-shot control settings.
- No regression: tunes that never read `$DC04..$DD05` produce byte-identical
  write logs before and after.

**Suggested approach.** Add per-CIA timer state (latch A/B, control byte, start
cycle, mode) to the VM. Populate it from writes to `$DC04..$DC0F`/`$DD04..$DD0F`
in `_wr`, and — on reset (Gap 3) — from the deterministic reset latch. In `_rd`,
extend the volatile branch (widen/extend the `0xD011 <= addr <= 0xD41C` gate,
vm.py:83, or add a dedicated CIA branch) to compute the countdown from
`self.cycles`. Keep it closed-form (no per-cycle stepping) so cost stays O(1) per
read.

**Risk / uncertainty.** Exact readback semantics (the two-cycle read latch on
`$xx05`, TOD, timer-B-counts-A cascade, ICR interactions) are intricate; a
closed-form countdown covers the common free-running/entropy case but not every
CIA corner. Phase depends entirely on the reset environment (Gap 3) being
deterministic and matching the oracle's; a mismatch there will surface as a
diverging write stream even with a correct countdown formula.

---

## Gap 2 — No continuous / free-running executor

**Motivation / affected cases.** Main-loop-driven RSID where synthesis happens in
a non-returning main loop and the interrupt is only a clock. `A_Mind_Is_Born`:
`init` (`$08B2`) copies code into zero page and `JMP`s into a continuous synth loop
that never returns. recover.py's own notes (docs/prototype.md:203-210) mark this
class "out of reach"; a non-returning `init`/handler trips the balancing-RTS/RTI
guards and degrades to cadence-only.

**Current behavior.** deity's three drivers all assume return/idle, none runs a
continuous main loop while delivering interrupts:
- `run_sub` (vm.py:235-249) runs to the balancing RTS (`while reg[3] < start`,
  vm.py:244); a routine that never returns hits `_GUARD` (vm.py:232, 247-248).
- `run_irq` (vm.py:252-274) enters like an IRQ and runs to the balancing RTI
  (`while reg[3] < start`, vm.py:269); same guard.
- `run_irq_driven` (vm.py:290-322) is multi-source and hardware-faithful for
  *nesting*, but **idles between handlers**: it computes the next fire
  `nxt = min(s["next"] ...)` and sets `vm.cycles = nxt` (vm.py:301-305), jumping
  the clock forward and **never executing the main program** between interrupts.
  It only ever runs handler bodies (entered via `_take_irq`, vm.py:308) down to
  their return (`while reg[3] < idle_sp`, vm.py:311). A tune whose work is in the
  main loop, not the handler, produces no synthesis under this driver.

**Required behavior.** A driver that runs the CPU **continuously** from an entry PC
(the main loop), and, whenever a source is due (`s["next"] <= vm.cycles`) **and the
I flag is clear** (`reg[10] == 0`), delivers that interrupt via the existing
`_take_irq`/`enter` path (vm.py:277-287) — raising `$D019`/`$DC0D` flags — instead
of skipping cycles. It must never advance `vm.cycles` past real work: the clock
advances only through executed instructions (`run_record` adds `rec["cyc"]`,
vm.py:148). Bound execution by a cycle/frame budget (like `total` in
`run_irq_driven`).

**Acceptance criteria.**
- A new driver (e.g. `run_continuous(vm, entry, total, sources, cache, lifter)`)
  executes `A_Mind_Is_Born`'s main loop, delivering the tune's IRQ at its due
  cycles, and its `$D400..$D418` write stream matches the `sidplayfp`/`sidtrace`
  oracle over N frames (N >= 3000).
- Between interrupts the driver executes main-loop instructions (assert the entry
  PC's instructions run and `vm.cycles` advances only via executed `rec["cyc"]`,
  never via an assignment that skips cycles).
- A due source with I set is **held pending** and delivered on the first cycle I
  clears (assert against a handler that runs with I set for a span).
- Existing `run_irq_driven`-covered tunes (defMON raster-split cases) still pass
  unchanged (the new driver is additive; do not repurpose `run_irq_driven`).

**Suggested approach.** New function alongside the others (vm.py:231+). Loop:
`pc = vm.step(pc, cache, lifter)`; after each step, if `reg[10] == 0` pick the
minimum-`next` due source, `_take_irq(vm, handler, pc, src["enter"])`, advance its
`next += period`; stop at `vm.cycles >= total`. Reuse `_take_irq` verbatim.
Determine the handler/entry from the tune's vectors (as recover.py's
`_handler_info`, recover.py:418-424, already does). Optionally fold recover.py's
`_drive`/`_drive_handler` into this so the shim can retire (Gap 3a).

**Risk / uncertainty.** Cost: a continuous main loop is far more instructions than
a per-frame `play`; the analysis-time budget (60s hard timeout, CLAUDE.md) must
hold — the loop is small and cache-hot, but N frames of it may need a tighter
step loop or a cycle cap. Interrupt-delivery timing (which instruction boundary the
IRQ lands on) must match hardware closely enough that cycle-exact synth reading
`$DC04` stays phase-aligned; small timing errors compound over frames. Nested/
pending-interrupt edge cases (I set across the due cycle) need care to match the
faithful nesting `run_irq_driven` already models.

---

## Gap 3 — No faithful C64 environment / reset state

**Motivation / affected cases.**
- (a) RSID tunes using the KERNAL CINV IRQ path (`$0314` -> KERNAL `$FF48`
  pushes A/X/Y -> `JMP ($0314)` -> handler -> `JMP $EA31` restore+RTI). Currently
  worked around; e.g. `Double_Dragon_2`, `P_A_S_S_Demo_3`.
- (b) Entropy-driven tunes whose output depends on the exact reset-state CIA timer
  phase and default vectors that a real environment fixes deterministically:
  `A_Mind_Is_Born` (ties to Gaps 1 and 2).

**Current behavior.** deity runs over flat 64 KiB RAM (`self.mem = bytearray(...)`,
vm.py:61). It does **not** model:
- `$01` processor-port banking — there is no `$01`-conditioned read path in `_rd`
  (vm.py:74-97); `$A000`-`$BFFF`/`$D000`-`$DFFF`/`$E000`-`$FFFF` are always RAM,
  no KERNAL/BASIC/CHAR/IO overlay.
- ROM images — none are loaded anywhere (no ROM in the package; cli.py loads only
  the tune).
- Power-on/reset state — `__init__` (vm.py:60-72) sets only `reg[3] = 0xFF`
  (vm.py:63); `$01` is not set to `$37`, no CIA timer latch/phase is established,
  and the `$0314/$0318/$FFFE` vectors are whatever the tune wrote (no KERNAL
  defaults). Because there is no KERNAL, the CINV return path has nothing to route
  through: recover.py compensates by installing tiny `$EA31`/`$EA81` restore+RTI
  stubs (`_EA31`/`_EA81`, recover.py:409-410; `_install_kernal_stubs`,
  recover.py:413-415) and hand-pushing A/X/Y for CINV handlers (`_drive_handler`,
  recover.py:441-444).

**Required behavior.** An **optional** faithful-environment mode:
- `$01` processor-port banking with correct LORAM/HIRAM/CHAREN decode, overlaying
  KERNAL/BASIC/CHAR/IO into `_rd`/`_wr` per the current `$00/$01` state.
- ROM images loadable as **fixtures** (KERNAL, BASIC, CHAR). **Not committed** —
  they are copyrighted (CLAUDE.md hard constraint 7 / project hygiene); fetched or
  supplied at runtime, path configurable, absent by default.
- Deterministic reset state: `$00/$01` defaults (`$2F`/`$37`), standard CINV/CBINV/
  NMI/reset vectors, and the reset-state CIA Timer A latch + phase (matching the
  oracle/sidplayfp), so entropy-driven tunes are reproducible.

**Acceptance criteria.**
- With the environment enabled and KERNAL ROM present, a CINV tune
  (`Double_Dragon_2`) runs the real `$FF48`/`$EA31` path (no recover.py stub) and
  its `$D400..$D418` write stream matches the oracle over N frames; the recovered
  IR is identical to the shim-driven result.
- With the environment enabled, `A_Mind_Is_Born`'s reset-state CIA Timer A phase
  matches sidplayfp's, and (with Gaps 1+2) its write stream matches the oracle.
- Environment is **opt-in**: with it disabled, all existing flat-RAM behavior and
  write logs are byte-identical (default path unchanged; ROMs never required for
  tunes that don't need them).
- No ROM bytes are committed to the repo (CI check).

**Suggested approach.** Add an environment object (ROM images + `$00/$01` state +
CIA reset) attached to `PcodeVM`; branch `_rd`/`_wr` (vm.py:74-107) on
`$01` when the environment is active, else fall through to today's flat model.
Provide a `reset()` that installs default vectors, `$01 = $37`, and the CIA reset
latch/phase. Load ROMs from a fixture path (env var / arg), skip banking overlays
when absent. Once present, retire recover.py's `_EA31/_EA81` stub and manual A/X/Y
push in favor of the real KERNAL path.

**Risk / uncertainty.** ROM licensing forces optionality — the environment cannot
be a hard dependency; both code paths must be maintained. Banking correctness
(CHAREN, IO-vs-CHAR at `$D000`) is fiddly; getting the reset CIA phase to match the
oracle exactly is the crux for entropy tunes and may require aligning to
sidplayfp's specific reset model rather than "real hardware" in the abstract. Cost
of banking checks on the hot `_rd`/`_wr` path — keep the disabled path branch-free.

---

## Gap 4 — SID `$D41B/$D41C` (osc3/env3 readback) is a cycle approximation

**Motivation / affected cases.** Tunes that read SID voice-3 oscillator output
(`$D41B`) or envelope (`$D41C`) as a modulation source or entropy (vibrato/LFO via
osc3, envelope-following). `A_Mind_Is_Born` also `ORA $D41C`s into its entropy, but
its dependence there is coarse; tunes that use osc3/env3 as a precise modulation
value are the ones this gap fails.

**Current behavior.** `_rd` returns `$D41B` and `$D41C` both as
`(self.cycles >> 3) & 0xFF` (vm.py:92-93) — a cycle-derived ramp, **not** a SID
model. It ignores voice-3's frequency, waveform, and envelope registers entirely;
`$D41B` (osc3) and `$D41C` (env3) return the same value. This diverges from real
SID osc3/env3 output for any tune that reads them as data.

**Required behavior (lower priority).** A faithful model would derive `$D41B` from
voice-3's oscillator given its frequency (`$D40E/$D40F`) and waveform (`$D412`)
register state and the elapsed cycles, and `$D41C` from voice-3's envelope given
its ADSR (`$D413/$D414`) and gate. Minimum viable: distinguish osc3 from env3 and
track voice-3 oscillator phase from its frequency register rather than a fixed
`cycles>>3` ramp.

**Acceptance criteria.**
- `_rd(0xD41B,1)` reflects voice-3 oscillator phase as a function of `$D40E/$D40F`
  and the selected waveform, not `cycles>>3`; `_rd(0xD41C,1)` reflects the voice-3
  envelope. The two return distinct values for a nonzero voice-3 config.
- On a tune that reads osc3/env3 as modulation, the `$D400..$D418` write stream
  matches the oracle over N frames where today it diverges.
- Document explicitly in deity that, until implemented, `$D41B/$D41C` are a
  cycle-clock approximation and osc3/env3-modulated tunes will mismatch.

**Suggested approach.** Add a minimal SID voice-3 model (phase accumulator from
`$D40E/$D40F`, waveform selection from `$D412` for osc3; ADSR state machine from
`$D413/$D414` + gate for env3) advanced by `self.cycles`. Compute lazily on read,
closed-form where possible (sawtooth/triangle/pulse are cheap; noise needs the LFSR).

**Risk / uncertainty.** A faithful SID is substantial (waveform combining, ring/
sync, noise LFSR, envelope ADSR curves and the well-known ADSR-delay bug); scope
must be capped to voice-3 osc/env readback, not full 3-voice audio. Even a partial
model may not match sidplayfp's SID exactly (reSID vs reSIDfp differences),
limiting achievable losslessness — record the residual as a known fidelity gap
rather than chasing bit-exactness.

---

## Cross-references

- recover.py shim to be superseded by Gaps 2+3a: `_drive` (recover.py:392-405),
  `_install_kernal_stubs`/`_EA31`/`_EA81` (recover.py:408-415), `_handler_info`
  (recover.py:418-424), `_drive_handler` (recover.py:427-452), `_frame_driver`
  (recover.py:455-463).
- deity drivers to extend/augment: `run_sub`/`run_irq`/`_take_irq`/
  `run_irq_driven` (vm.py:235-322).
- Volatile-read model to extend for Gaps 1 and 4: `PcodeVM._rd` (vm.py:74-97);
  write side for CIA latch capture and `$D019` ack: `PcodeVM._wr` (vm.py:99-106).
- prototype scope notes on this class of tune: docs/prototype.md:194-211.
