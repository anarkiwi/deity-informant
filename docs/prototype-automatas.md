# Prototype: the tuneprog decompiler on defMON's *Automatas*

Vertical slice of [tuneprog-architecture.md](tuneprog-architecture.md)
against `MUSICIANS/G/Goto80/Automatas.sid` (Goto80, defMON export, 2013; anatomy
[§3.7](playroutine-anatomy.md)), the hardest of the nine anatomy exemplars for
this design. Real code in its final place (`deity_informant/tuneprog/`), generic
by construction. Done when Automatas carries a **certificate**: per-call
SID-write equivalence over the whole song plus the periodicity witness, printed
in the shape of anatomy §3.7.7. Acceptance criteria E1–E13 and their measured
outcomes are §6.

---

## 1. Why Automatas

Automatas exercises nearly all design mechanisms at once:

| design mechanism | how Automatas stresses it |
|---|---|
| tick ≠ frame; cadence from init | CIA-1 timer $0998 → 2457 cycles/tick, **8 ticks per frame**; the wrapper's own counter selects a *main* tick (sequencer) every 8th call and a *sub* tick otherwise |
| SMC operand cells → loads (S2) | ~85 cells: the whole SID image (7 immediates × 3 voices), cascade counters/indices, row timers, pre-shifted flag copies, the filter accumulator, the *wrapper's call counter* (`INC $0FE4` = operand of `LDA #` at $0FE3) |
| SMC operand *addresses* as pointers | the pattern pointer is patched into four `LDA abs[,Y]` operands per voice (pointer broadcast) |
| SMC opcode cells → variant switch (S2) | `$10D8 LDA #`↔`RTS` (the sub-tick gate), `$10B8`/`$10BF ADC`↔`SBC` (filter slide sign), `$10D4 NOP`↔`ASL` (8580/6581 cutoff scale, set once at init) |
| regions inside code (S3) | per-voice state and SID image live in instruction operands, addressed `abs,X` with X ∈ {0,$31,$62}: struct-of-code, stride 49 |
| procedures by observed JSR/RTS with clone-per-entry (S2) | `$1022` is entered by `JMP` from `$1003` (main) and by `JSR` from `$1006` (sub); its exit in sub ticks is the patched `RTS`; the tail `$12BE–$14CA` is shared by both contexts |
| illegal opcodes | `SAX abs`, `SBX #`, `LAX zp`, `LAX (zp),Y`, `ANC #`, `ALR #` — six kinds, all on the hot path |
| volatile inputs pinned (S1) | init busy-waits on `$D012` (≈2,200 reads) then reads `$D41B` once to pick the SID model → two of the SMC cells depend on it |
| flags as values (S4) | `ASL` of a flag byte feeding `BPL`/`BCC`/`BIT`, `SBX` setting N for the loop test, `ANC #$7F` as an implicit `CLC` before `ADC` |
| unrolled + indexed voices (S5/S6) | three 49-byte write-band blocks and three row-advance blocks unrolled; the oscillator is a `SBX #$31` loop over the same cells |
| variable-length record decoder (S6 grammars) | `$168C` decodes sidTAB rows by testing flag bits in a fixed order |
| verification at call granularity (S8) | sidTAB rows last DL+1 *calls*; the SID sees the image one call late; a frame-level check would miss every 400 Hz arpeggio |

Runner-up Blackbird (§3.9) has the nastiest CFG but is single-speed, tiny, with
no play-time opcode cells, no volatile reads and no struct-of-code; second
target ([tuneprog-architecture.md](tuneprog-architecture.md) §9.1).

---

## 2. Ground truth

From anatomy §3.7 and the survey tracer run on this tune (2026-08-16; 30 s and
700 s of music, the latter the whole song plus one full loop):

| fact | value |
|---|---|
| container | PSID, load $0FD0–$2FAF, init $0FD0, play $0FE3, 1 subtune, HVSC length 5:23 (323 s), 8580 |
| cadence | init writes CIA-1 TA = $0998 → 2457 cycles/tick; 8.0 ticks per PAL frame; header speed bit set; entry = header `play` (`sub`) |
| ticks in the song | 16,128 main ticks per arranger loop (Σ (d+2) over 168 rows) = 129,024 calls; the arranger jumps to row 0 at row 168 |
| executed sites | 616 total in 30 s (555 play, 61 init); 648 pcs / 651 site keys in 700 s (587 play, 61 init; a pc with two opcode variants is two keys) — the anatomy quotes 811 over 24,000 calls, a static count of instruction starts in executed bytes (dead only: AF>0 detune path $1448–$1473, RE raw store $170B–$170F); no dead code missed |
| per call | 206 instructions mean, 607 max; 652 cycles mean, 1915 max (700 s run); **24 SID writes** per call (7 × 3 voice registers + $D417 + $D418 + $D416; $D415 is written by init only), 52 max on row-apply calls |
| SMC (measured) | 84 play-written cells in 70 instructions after 30 s, 88 in 73 after 700 s, plus 3 play-time opcode cells (`$10B8`, `$10BF`, `$10D8`); 35 cells written by init only (`$10D4` opcode among them); wrapper counter `$0FE4` |
| opcode-cell variants | `$10D8`: $A9 (`LDA #`) / $60 (`RTS`), writers `$100C,$1013` (the sub-tick gate) and `$10DE,$1197,$121F,$12A7` (flag stores); `$10B8`,`$10BF`: $69 (`ADC #`) / $E9 (`SBC #`), writers in `$1754–$177B` (ACID column); `$10D4`: $EA/$0A, writer `$14F9` (init) |
| illegal opcodes | $2B `ANC #`, $4B `ALR #`, $8F `SAX abs`, $A7 `LAX zp`, $B3 `LAX (zp),Y`, $CB `SBX #` — 9 sites |
| volatile reads | init: `CMP $D012` at `$14CE` (≈2,227 executions, busy-wait for line $FC), `LDA $D41B` at `$14E3` once; play: none |
| calls / returns | wrapper `JSR $1003` (→ `JMP $1022`, main) and `JSR $1006` (sub, which does `JSR $1022` and continues `JMP $12BE`); six `JSR $168C` (row apply) from the cascade blocks; init `JSR $1000` → `JMP $14FE`, `JSR $14CB` (model detect); max JSR depth 2; no unmatched RTS; no `JMP (ind)`; no `(zp,X)` |
| state footprint | 107 RAM bytes written by play (30 s), 110 (700 s) — including every SMC cell |
| **periodicity** | the play-written state repeats with **period 129,024 calls** (= 16,128 main ticks × 8 = one arranger loop), first at call 149,024 (state at call 20,000 == state at 149,024): the first pass through the song is a transient of 20,000 calls, then everything is exactly periodic. Measured with the survey tracer's footprint hash; no inputs after init |
| cost (survey tracer, Python) | 280,697 calls (700 s of music) traced in 209 s wall = 277 k instructions/s; ≈ 206 instructions per call mean, 607 max |
| tables (const) | freq lo/hi $1554/$15F0 (156 each), sidTAB ptr lo/hi $1800/$1900 (213), DL $1E00, pattern ptr lo/hi $1A00/$1A80, arranger $1B00/$1C00/$1D00, patterns $1F00–$29C8, sidTAB rows $2C8F–$2FAF |
| timing | pattern row = d+2 main ticks; sidTAB row = DL+1 calls; the SID receives the image one call late (write-out is the first thing a call does) |
| SID model | `$D41B` bit 0 at init selects `CMP #2/#0` at `$10CE` and `NOP/ASL` at `$10D4` (6581 vs 8580 cutoff scaling) |

SMC inventory: §10. Printed-form reference: anatomy §3.7.3 and §3.7.7.

---

## 3. Scope

Built, generic, in `deity_informant/tuneprog/`: S0–S8 (module map §4, stages §5),
with S5/S6 carried far enough to print Automatas readably.

- Should-have: writer-derived enumeration of unobserved opcode-cell variants
  (`$10D4` gets `NOP` and `ASL`, unexecuted arm unverified); pinned-input
  override, so the tune certifies under both SID models; `$D012` busy-wait
  printed as `while input() != c`.
- Stretch, landed: copy folding of write-band and row-advance blocks into
  `for v`; 16-bit arithmetic folding.
- Stretch, not landed: pointer-broadcast unification (four patched operand pairs
  → one 16-bit pointer); sidTAB grammar table from the `$168C` decision tree;
  family name dictionary (undefmon labels).
- Out of scope: Blackbird's `(zp,X)`, `NOP #imm` overlap, LZ unpacker; second
  interrupts; ROMs; numba; a campaign driver.
- Non-negotiable: no tune-specific branches; `Commando.sid` (Hubbard, §3.1) must
  certify at S4 over its HVSC length in the same test run.

---

## 4. Package layout, data structures, file formats

Layout as planned; as built, with line counts, in [tuneprog-architecture.md](tuneprog-architecture.md) §10.

```
deity_informant/tuneprog/
  __init__.py
  machine.py    S0  MachineImage (power-on ⊕ load band), find_entries(), init_runner(), cadence, port/CIA hooks
  trace.py      S1  TraceVM(PcodeVM), run_trace(), Trace (+ save/load, resume)
  lift.py       S2  lift_site() with residualisation; SiteKey; variants
  cfg.py        S2  build_procs(): Proc, Node, edges, tail calls, switches, clones, call summaries
  regions.py    S3  build_regions(): Region, kinds, strides, envelope
  ir.py             IR node classes, JSON (de)serialisation, Interp (reference executor)
  ssa.py        S4  SSA construction, DCE, const/copy propagation
  idioms.py     S4  peephole rewrites (relational, counters, compound assignment)
  structure.py  S5  loops/if/switch, for-recognition, phase recognition, goto fallback
  recover.py    S6  strides→struct views, roles (sid image, freq table, cursor, pointer, timer), names
  emit.py       S7  Python codegen, pseudocode printer, certificate writer
  verify.py     S8  differential run vs reference log, periodicity, chunked/resumable
  cli.py            `deity-informant tuneprog TUNE.sid --out DIR [--seconds S | --full] [--sid-model 6581|8580] [--resume]`
tests/tuneprog/   unit tests per module (assembled snippets) + end-to-end (marked)
```

Key types (Python dataclasses; `numpy` for bulk arrays):

```
SiteKey   = (pc:int, opcode:int, fixed_operand_bytes:tuple)   # operand bytes that are SMC cells are excluded
SiteRec   = { count, phases:set, first_bytes, variants:set[bytes], idx_values:set[int],
              reads[op_i]:set[int], writes[op_i]:set[int] }
Edge      = (from:SiteKey, kind ∈ {fall,br_taken,br_not,jmp,jmpind,jsr,ret,rti,brk,tail}, to_pc:int) → count
CallRec   = (jsr_site, target_pc, ret_pc, depth)  ;  RetRec = (rts_site, matched_jsr_site|None, ret_pc)
Trace     = { image_pre, image_post_init, entries, schedule, sites, edges, calls, rets,
              wlog: npz(call:u32, addr:u16, val:u8, cyc:u32), iow: same, init_writes,
              inputs: [(call, site, op_i, addr, value)], state_hash: npz(u64 per call),
              footprint_size: npz(u16 per call), calls_done, resume_state }
Node      = (proc:int, pc:int, variant:int) ; Proc = { entry, nodes, edges, exits, summary(live_in, defs, regions) }
Region    = { id, name, base, size, addrs:sorted, kind ∈ {state, init_constant, const, image, io},
              accessors: [(site, op_i, index_expr)], stride, fields, init_bytes }
IR ([tuneprog-architecture.md](tuneprog-architecture.md) §4): Tuneprog{meta, storage, inputs, procs}; Proc{name, params, blocks}; Block{label, stmts, term}
```

Files per tune (`--out DIR`): `trace.json` + `trace.npz`, `procs.json`,
`regions.json`, `tuneprog.json` (IR per stage as `tuneprog.S4.json`, `.S5.json`,
`.S6.json`), `tuneprog.py`, `tuneprog.md`, `certificate.json`.

---

## 5. Stage-by-stage implementation

**S0 `machine.py`**

- `MachineImage.from_sid`: `c64.poweron_ram()` ⊕ load band, keeps `(lo, hi)`;
  header from `pysidtracker.header` or `c64.load_psid`.
- `find_entries`: `pysidtracker.trace_init` + `playroutine_cadence` →
  `schedule = [{"kind","addr","cycles_per_tick","source"}]`;
  `c64.installed_handler` when `play = 0`.
- Refuses on no entry, or on a second armed source (CIA-2 latch/NMI vector) whose
  undelivered interrupt would break equivalence; Automatas arms none.
- `init_runner(vm, init, song, budget)`: run to RTS under budget, `JMP *` = done;
  interrupt delivery on exhaustion is a `NotImplemented` refusal, unused here.
- `port_bank()` reads `$00/$01` and classifies `$D000–$DFFF` stores; Automatas
  never writes `$01` (unit-tested).
- CIA timer read model: count-down from latch at cycle rate, ICR bit on
  underflow, so busy-waits terminate.

**S1 `trace.py`** — `TraceVM(PcodeVM)`

- `compile_record` override emits `rd(a,sz,i)` / `wr(a,v,sz,i)` per op index; a
  20-line variant of `vm._emit_line`, base VM untouched.
- `(zp),Y` pointer fetches are their own LOAD ops, attributed apart from the
  target load.
- Site key `(pc, opcode, fixed operand bytes)`, recomputed once the play-written
  cells are known so cell operands drop out; also records `count`, `phase`,
  `first_bytes`, `variants`, index-register values.
- Shadow stack: `jsr` pushes `(site, sp_after, ret_pc)`; `rts`/`rti` pop on
  `ret_pc` match else record unmatched; the driver's return is synthetic; a
  non-`jsr` edge into a JSR target is `tail`, marked on a second pass.
- `wlog` `$D400–$D7FF`, `iow` the rest of `$D000–$DFFF`, both with call index and
  cycle; `init_writes` kept apart.
- Inputs: reads of `$D000–$DFFF` (acks classified out), of never-written
  addresses outside the load band, and of A/X/Y before their first write in a
  tick. Policies `record`, `replay`, `override {addr: value}` (pins `$D41B`
  bit 0).
- State hash per call: `blake2b` over the play-written set keyed with its size;
  first repeat → `period`.
- Resume pickles `(vm.mem, regs, cycles, shadow stack, accumulators)` every N
  calls, for the 60 s per-script rule (§7).
- Output `trace.json` (structure) + `trace.npz` (bulk), plus the (pc, variant)
  count that reconciles the anatomy's 811.

**S2 `lift.py`, `cfg.py`**

- `lift_site`: const varnodes in `rec["prov"]["ops"]` meeting `cells` become
  `LOAD`s from those cells — 1 byte for immediates, the two address bytes of
  `abs`/`abs,X`/`abs,Y` a 16-bit `LOAD` folded into the address expression;
  `prov["ctrl"]` makes a patched `jmp`/branch operand a computed target.
- `build_procs` entries = tick entries ∪ {init} ∪ JSR targets, one `Proc` each.
- Frame model per activation: `jsr` → call, continue at `ret_pc`; `tail` →
  `return call(entry)`; matched `rts` → `return`; unmatched `rts` / `jmpind` /
  patched jump → `switch(expr) {observed targets; default: trap}`, `expr` being
  the value the site read.
- Nodes `(proc, pc, variant)`: a pc under two procedures is cloned; several
  opcode variants → `switch(load(pc))`, one arm each, plus writer-derived arms
  for constants stored but never observed (marked unverified).
- Call summaries: registers/flags read-before-write and written per callee;
  regions touched on a second pass after S3.
- Expected procs (E6 names them): `wrapper_init($0FD0)`, `init($14FE)` tail of
  `$1000`, `sid_model_detect($14CB)`, `wrapper_tick($0FE3)`, `p1003`
  (= `return call(main)`), `main($1022)` switching on `$10D8`
  {`$A9`: continue, `$60`: return}, `sub($1006)` holding the `main` call and its
  own clone of `$12BE–$14CA`, `row_apply($168C)`.

**S3 `regions.py`** — union-find over addresses touched by the same op, from
`Trace.sites[*].reads/writes` plus residualised cell reads; components →
`Region`s; kind by writer phase; `stride = gcd` of accessor index differences;
`fields` = distinct constant offsets mod stride; `init_bytes` from `image_pre`;
envelope = observed extent per accessor. Regions may cover code addresses, an SMC
cell region lying inside executed instruction bytes.

Expected: ~116 regions; write-band immediates as 8 stride-49 regions of 3 cells
(`$1023/$1054/$1085` … `$103D/$106E/$109F`); 9-byte voice records at
`$1019/$104A/$107B`; cascade counters/indices at `$12BF/$12CE/…`; note cells
`$12CC`/`$135E` (+$31); four pattern-pointer operand pairs per voice as 16-bit
state cells; §2's tables as `const`; filter/status immediates and the wrapper
counter as scalars; `$FB/$FC` a pointer region, the sidTAB rows it reaches a
separate `const`.

**S4 `ssa.py`, `idioms.py`, `ir.py`**

- Registers/flags/uniques → SSA `let`s (dominance frontiers via `networkx`);
  memory ops stay ordered (`load`/`store` on regions, `sidw` for SID stores,
  `iow` for VIC/CIA); calls use summaries.
- DCE (unused flag ops, dead register copies), copy propagation, constant
  propagation from `const`/`init_constant` regions and the post-init image.
- Idioms: compare-then-branch → relational terms; `DEC/INC` + branch → signed
  tests; `load; op; store` on one scalar region → compound assignment (print
  only); `ASL A`/`BPL`/`BCC` on a flag byte → bit tests; `ANC #$7F` → `and` +
  `C = 0`; `SBX #imm` → `X = (A & X) − imm` with flags; `LAX` → two `let`s;
  `SAX` → `store(A & X)`.
- `interp.Interp` is the reference executor over the JSON IR: flat image as
  backing store, regions as views, envelope asserts on every indexed access,
  `sidw`/`iow`/`input` hooks; certificate #1 comes from here.
- Expected: 23 `sidw` in the write band (7 per voice + `$D417` + `$D418`), values
  `load(cell)`; filter as `acc ±= step` with sign from `switch(load($10B8))`; no
  `A`/`X`/`Y`/flag names in the emitted Python except as locals.

**S5 `structure.py`** (structural only) — dominator-tree natural loops → `while`;
if/else via post-dominance; variant/computed nodes → `switch`; `goto` for the
residue; `for` when the induction variable is a register with a small observed
domain stepped by a constant (`X` ∈ {$62,$31,0}, `SBX #$31`); phase recognition
on the tick's first branch comparing a state scalar with constants (`cnt & 7`).

**S6 `recover.py`** — stride views (`voice[v].pw_lo`) plus roles: loads flowing
unchanged into `sidw($D400+7v+k)` → `sid_image[v].reg_k`; `DEC…BPL/BMI` + reload
→ `timer`; a region indexed by another region's value → `cursor into R`;
zero-page pairs under `(zp),Y` → `ptr`; freq table via
`pysidtracker.notefreq.locate_note_freq`, falling back to "u16 table" when the
detector rejects the 12 leading pseudo-entries; names by role and voice.

**S7 `emit.py`** — `tuneprog.py` = one Python function per procedure over
`mem: bytearray`, `sidw(addr, val)`, `iow(addr, val)`, `inp(name)`;
`tuneprog.md` = the printer; `certificate.json` = [tuneprog-architecture.md](tuneprog-architecture.md) §5 schema, `compared`
including `init writes`, `tick sid writes`, `tick schedule effects`.

**S8 `verify.py`** — `verify(tuneprog, trace, calls, resume)` runs `init(song)`
then `tick()` × N on the emitted Python, and on a shorter prefix on
`interp.Interp` to prove codegen = interpreter, feeding pinned inputs; compares
per-call `(addr, val)` lists and init's list against the reference; on mismatch
reports tick, index, expected/got and the IR statement's origin site; checks the
tuneprog's per-call state hash against the trace's `(k, k+p)`. Envelope traps
count as divergences; chunked and resumable like the tracer.

---

## 6. Acceptance: evidence and results

Certificates `docs/certificates/{automatas,automatas-6581,automatas-8580,commando-song1,commando-song2}.json`,
from `tools/tuneprog_certify.py` (= `deity-informant tuneprog`), re-run against
the committed traces after S5/S6 landed. E3–E9 are checked mechanically in
`tests/tuneprog/test_automatas.py` against §10 and anatomy §3.7.

| id | claim | check / expected | measured |
|---|---|---|---|
| E1 | per-call equivalence | `certificate.divergence == null` from init through the first state repeat (149,024 calls = transient plus one full period, §2) plus init writes; also under `--sid-model` overridden to the other value | **0** divergences, 149,025 calls from init, under the traced model and under `--sid-model 6581` and `8580` separately (three certificates); Commando songs 1 and 2 likewise 0 over 11,780 calls each |
| E2 | periodicity certificate | trace and tuneprog state hashes repeat at the same `(k, k+p)`, no inputs after init; expect `k = 20,000`, `p = 129,024` (§2) → `complete: true`, writes verified over `[0, k+p)` and equal states at `k` and `k+p` covering every later call by determinism | **period 129,024, first repeat at call 149,024** (transient 20,000), trace and tuneprog hashes agree at the same `(k, k+p)`; `complete: true` |
| E3 | tick model | `schedule` = one entry, 2457 cycles/tick; the wrapper's `cnt & 7` becomes the tick's top-level `switch/if` | `schedule = [sub $0FE3, 2457, cia_timer]` (8.0 calls/frame); printed tick `if (call_counter & 7) != 0: sub() else: main()` |
| E4 | SMC operand residualisation | every play-written operand cell in §10 is a `state` region read by `load` at its instruction, zero constants left for those operands; 84+ cells incl. `$0FE4` and the 4-per-voice pointer pairs | **88 cells** (110 play-written bytes), all constant-address loads in the IR, `$0FE4` included; the 35 cells only `init` patches are loads there and constants in the tick (the rule the Follin prototype corrected, `docs/prototype-follin.md` section 3) |
| E5 | SMC opcode variants | `$10D8` → switch {continue, return}; `$10B8/$10BF` → {ADC, SBC}; `$10D4` → {NOP, ASL} with one arm unverified (writer-derived) | `$10D8` {`LDA #`, `RTS`}, `$10B8`/`$10BF` {`ADC`, `SBC`} -- three variant switches with a trap default; `$10D4` has one arm per model (no writer-derived arm: init's writer stores a value the trace computes, so the second variant comes from the model override) |
| E6 | procedures / clone-per-entry | procs as listed in S2; `main` has two exits; `sub`'s clone of `$12BE–$14CA`; 0 unmatched RTS | **8 procedures** (`init`, `tick`, `p_1000`, `p_1003`, `p_1006`, `p_1022`, `p_14CB`, `p_168C`), 305 blocks, **0 unmatched RTS**, 6 JSR targets, `$1022` entered by both `JMP` and `JSR` |
| E7 | regions / struct-of-code | >= 8 stride-49 triples in the write band plus records and cascade cells; kinds as in S3; envelope asserts never fire during E1 | **21 stride-49 regions** of 102, **0 envelope traps** over the whole song |
| E8 | illegal opcodes | the six kinds lift and the certificate holds (E1) | **6** (`ANC`, `ALR`, `SAX`, `LAX zp`, `LAX (zp),Y`, `SBX`) |
| E9 | inputs | exactly two input sites (`$D012` wait, `$D41B`), both in init; play consumes none | `$D012` at `$14CE` (2,227 reads), `$D41B` at `$14E3` (1 read), both `init`; play consumes none |
| E10 | genericity | `Commando.sid` certified at S4 over its HVSC length by the same code, no flags, 0 divergences | songs 1 and 2, 0 divergences, no flags but `--song` |
| E11 | readability | `tuneprog.md` prints two rates; three tables (`state`, `const`, `inputs`); the per-call chain `writeout → filter → cascades → oscillator`; row advance on main ticks only; per-voice fields named by role | §6.1 excerpts; asserted mechanically in `tests/tuneprog/test_hvsc_print.py` |
| E12 | codegen = interpreter | `interp.Interp` and `tuneprog.py` agree on a 5,000-call prefix, 0 divergences | 2,000-call prefix on every certificate (500 for the 10 s model-override run) |
| E13 | budgets | any single script invocation <= 60 s CPU via chunking; full E1 wall <= 15 min on one core-equivalent; reported in the certificate's `cost` | trace 149,025 calls in ~2 chunks; front end + S4 0.2 s, verification 9.2 s (16,136 calls/s), S5/S6 + printing 0.4 s |

---

## 6.1 Printed forms and reconciliations

E11, Automatas (`tuneprog.md`, verbatim, `...` elides):

```
meta      entry sub $0FE3 every 2457 cycles (8.0 calls/frame, cia_timer)
          phase call_counter selects the rate
          certified 149,025 calls, 0 divergences, period 129,024, first repeat at
          call 149,024 (complete), stage S6
state     copy[2] per-copy cells, 10 fields          (the row-advance pair)
            .timer $1129 $11B1 ; .ptr_2 $114A $11D2 ; .b1161 .ptr_3 .ptr_3_2 ..
          rec2[6] stride 49, 2 fields                 (the six cascade blocks)
            .timer_4 $12BF timer ; .cursor_12CE $12CE cursor
          voice[3] stride 49, 24 fields
            .pw_lo .freq_lo .freq_hi .sr .ad .ctrl .ctrl_eor           sid_image
            .timer $1129 $11B1 $1239   timer ; .freq_idx $135E  cursor
            .ptr_2 .ptr_3 .ptr_4 .ptr_5                                ptr
            .acc $1019 acc ; .b101A .b101E .b101F .b1020 .b1021 .b1161 .b116F ..
          filter.acc $10B6 u16 lo|hi $10BE  acc ; filter.step $10B9 u16 lo|hi $10C0
          call_counter $0FE4 phase ; res_route $10AA / mode_vol $10AF sid_image ; ptr $00FB
const     FREQ $1554 361 bytes u16  freq_table  12-TET lo|hi, 156 entries (59 below one octave)
          T1800 T1900 T1A00 T1A80 T1B00 T1C00 T1D00 T1E00 T1F00.. T2C8F..   table
inputs    $D012 raster at $14CE, 2227 reads (init) ; $D41B sid_readback at $14E3, 1 read

tick():                                  # $0FE3, 152,000 calls
    if (call_counter & 7) != 0:
        sub()
    else:
        main()
    call_counter += 1
    return

main():                                  # $1022, 152,000 calls
    writeout()
    filter()
    switch b10D8:                        # the sub-tick gate
        case $60:
            return
        case $A9:
            row_advance()                # main ticks only
            cascades()
            oscillator()
            return

sub():                                   # $1006, 133,000 calls
    saved = b10D8
    b10D8 = $60                          # patch main's exit to RTS
    main()
    b10D8 = saved
    cascades()
    oscillator()
    return

writeout():                              # $1022, 152,000 calls
    for v in 0, 1, 2:
        sid[v].pw_lo = voice[v].pw_lo
        sid[v].pw_hi = voice[v].pw_hi
        sid[v].freq_lo = voice[v].freq_lo
        sid[v].freq_hi = voice[v].freq_hi
        sid[v].sr = voice[v].sr
        sid[v].ad = voice[v].ad
        sid[v].ctrl = (voice[v].ctrl ^ voice[v].ctrl_eor)
    sid.res_route = res_route
    sid.mode_vol = (mode_vol | $F)
    return

filter():                                # $1022, 152,000 calls
    switch b10B8:                        # the patched ADC/SBC: the filter slide sign
        case $69:
            filter.acc += filter.step
            a19 = filter.acc_hi
            n30 = filter.acc_hi < 0
        case $E9:
            filter.acc -= (filter.step + 1)
            ...
    if n30 == 0: a20 = a19 else: a20 = b10CE       # the accumulator clamps at the sign
    filter.acc_hi = a20
    t4 = ((filter.acc_hi + b10CA) + carry)         # carry: the 16-bit op's, into the cutoff
    if ((((filter.acc_hi + b10CA) + carry) < 0) or (t4 < 2)):
        a22 = b10CE
    else:
        a22 = t4
    sid.cutoff_hi = a22
    return

cascades():                              # $12BE, 912,000 calls
    for v in 0..5:   # x912,000          # the six cascade blocks, one body
        # $12BE
        t1 = copies_12BE[v + $18]        # the one column no rule names
        if rec2[v].timer_4 == 0:
            if T1900[rec2[v].cursor_12CE] != 0:
                # $12CD
                y10 = rec2[v].cursor_12CE
                a59 = T1900[rec2[v].cursor_12CE]
            else:
                # $12D4                  # the sidTAB row pointer wraps
                y10 = T1824[rec2[v].cursor_12CE]
                a59 = T1900[T1824[rec2[v].cursor_12CE]]
            # $12DB
            ptr[1] = a59
            rec2[v].timer_4 = T1E00[y10]
            rec2[v].cursor_12CE = (y10 + 1)
            row_apply(a=T1800[y10], x=t1)
        else:
            if rec2[v].timer_4 >= 0:
                # $12C4
                rec2[v].timer_4 -= 1
    return

oscillator():                            # $13E4, 152,000 calls
    for v in 2, 1, 0:                    # x456,000 -- the SBX #$31 loop
        if voice[v].freq_idx == 0:
            voice[v].freq_lo = (FREQ[$24 + voice[v].freq_idx_2] + voice[v].b101F)
            voice[v].freq_hi = FREQ[$C0 + voice[v].freq_idx_2]
        else:
            if (voice[v].freq_idx << 1) >= 0:
                voice[v].acc_2 += FREQ[voice[v].freq_idx << 1]       # the slide, 16-bit
            else:
                voice[v].acc_2 -= (FREQ[$14D4 + (voice[v].freq_idx << 1)] + ...)
            voice[v].freq = (voice[v].acc_2 + FREQ[$24 + voice[v].freq_idx_2])
        ...                              # then the pulse bounce

row_advance():                           # $10D8, 19,000 calls
    ...                                  # the arranger row, the pattern pointers,
    ...                                  # then the three per-voice row timers

row_apply(a, x):                         # $168C, 103,249 calls
    ptr = a
    ...                                  # the sidTAB column decoder
```

Commando song 1 (`tuneprog.md`, verbatim), the illustration of [tuneprog-architecture.md](tuneprog-architecture.md) §4:

```
tick():                                  # $5012, 11,780 calls
    timer_7 += 1                         # the free-running frame counter
    if phase < 0: trap 'untaken'         # $5519 mstatus; the "music off" path never ran
    if (phase & $40) != 0:               # lazy init
        timer_7 = 0
        for v in 2, 1, 0:
            FREQ[v + $A4] = 0            # the per-voice cells the freq table overruns into
            FREQ[v + $A7] = 0
            voice[v].timer = 0
            FREQ[v + $B3] = 0
        phase = 0
    t2 = timer_5                         # $5513 speedctr
    timer_5 -= 1
    if timer_5 >= 0: ...
    else: timer_5 = b5517                # = speed
    for v in 2, 1, 0:                    # x35,340 -- the voice loop, X carried through $5504
        FREQ[163] = FREQ[v + $A0]        # the voice -> SID offset table
        if timer_5 != b5517: ...         # soundwork
        else: ...                        # tick boundary: lengthleft, fetch_note, gate off
```

Reconciliations with the plan:

- Commando's `$54EF`/`$54F8` merge into the frequency-table region: vibrato reads
  entry `note+1`, overrunning the 96-entry table, so union-find joins the table's
  tail with the per-voice cells after it. Still recognised as the note table
  (`FREQ`, u16le, 80 entries at the traced horizon).
- S5/S6 do not edit the IR: structuring, texture removal, 16-bit views,
  outlining, copy folding and a print-only dead-value pass act on a copy.
  `tuneprog.S5.json`/`.S6.json` are annotations; the certificate's `stage` is
  `S6` with a `presentation` note; tests assert the S4 JSON is byte-identical
  before and after (`pipeline.present`).
- Names come from S6's roles, so the semantic names of anatomy §3.7.7 do not all
  appear: `af`, `ps`, `detune` and the pre-shifted flag copies reach no role and
  print as `voice[v].b101B`; `voice[v]` itself comes from stride and element count.
- Note table: `FREQ_LO`/`FREQ_HI` at 30 s, merged into one `FREQ` by the TR
  overrun over the full run.
- Copies fold only when shapes are equal (`unroll.py`): write-out folds to
  `for v in 0, 1, 2:` over seven registers because every inter-copy difference is
  a constant stepping by struct stride 49, SID voice size 7 or argument scale
  (`row_apply(x=(v * $31))`), region ids agree, and one region is walked with one
  stride.
- Cascade merges to one body under the copy index (`copyrows.py`,
  `copymerge.py`; S2c, certified): `$12BE` is five chained copies of one 18-row
  block at 30 s, six of 19 rows over the whole song (cascade B1 runs), in both the
  procedure that falls into it and the one that jumps to it; the three addresses
  the copies disagree on go in one per-copy table shared by both clones, which
  lets `fold.outline` keep one helper. The oscillator's `$16CD` pair folds
  likewise.
- Row-advance blocks `$112A`/`$119A`/`$1222` fold (P1) to one 45-row body over 22
  columns once a copy holds only what its rows hold; the two instructions before
  `$11B2` are copy 0's tail plus a copy-1 preamble no template row covers, so the
  run leaves the family and re-enters at the row. Their three unrelated table
  regions keep their table read.
- At 30 s `$16AB` ×2 refuses to fold: its skip enters the next copy at that
  copy's fourth row, not its entry, and only a copy's own entry advances `v`
  (reason in the certificate's `copies.refused` and the printed header). Over the
  whole song that pair is not a family at all, so nothing is refused.
- Fold sizes: whole song, S4 895 → 733 statements, 241 → 227 blocks, 98 → 80
  regions; 30 s, 815 → 754. State header 782 → 772 lines at 30 s, 716 → 756 over
  the whole song, cells still listed field by field.
- The merged row-advance body has k entries, each copy's preamble being its own;
  since Q1a those k prologues are the loop's step, `loops.copies` proves the chain
  from the assignments (copy 0 from outside, 1 and 2 on the back edges, each
  edge's count its own copy's share of the cover) and the body prints
  `for v in 0, 1, 2:` with three `switch v` chain edges, index hidden. Six `goto`
  into the prologues stay, since no helper may hand the copy index back.
- Cascade prints as its own loop (`copyview.py`, `loops.py`; S6, #244):
  `for v in 0..5:` over the whole song (`0..4` at 30 s, where B1 never runs) with
  `rec2[v].cursor_12CE`/`rec2[v].timer_4`, columns stepping by the 49-byte record
  becoming that step in `v`. Two columns keep their table read
  (`copies_12BE[v + $18]`, the `row_apply` argument, and `copies_16CD[...]` in the
  oscillator pair), their copies naming cells at different offsets of one record.
  The `for` comes from the coverage vector, not the exit tests, the loop leaving
  through a `switch` arm the recurrence analysis cannot read. Printed document
  744 → 716 lines over the whole song, 800 → 782 at 30 s; `fold.outline` keeps
  the procedure and its clone one `cascades()` helper.
- Runs become helpers when shared or when they name a part (`fold.py`): `main` =
  `writeout(); filter(); switch {row_advance(); cascades(); oscillator()}`,
  `sub` = the RTS patch, `main()`, then the two shared helpers — one printed copy
  each, ~200 duplicated lines gone. Outlined only when no live value crosses the
  boundary; shared only when alpha-renamed statement forms are equal (frame pushes
  and arguments no callee reads excluded, as the printer drops both); `sub`'s
  clone, glued by S4 block merging to its own prologue, matches by skipping it.
- Machine texture gone from the hot path: no `sp` (push and pop of one stack slot
  become `saved = ...`/`... = saved`, a JSR frame is the call), no `goto` (nested
  tests sharing a target print as `or`/`and`), 156 machine temporaries down to 27
  (values fold into their uses across non-aliasing statements, regions being
  disjoint, but never past a store to their own region, a call or another input
  read), and the filter's carry chain prints as `filter.acc += filter.step` over a
  named `lo|hi` view once the two patched ADC/SBC cells are proved one variable
  and their switches merged.
- The `$D012` busy-wait collapses to `while input($D012) != $FC: pass`; JSR frames
  and register copies nothing reads stay out of the text without touching the
  executable IR.
- Remaining texture: five `carry(`, none in `main` or `filter` — three
  page-crossing assertions on `row_advance`'s pattern pointers (a 16-bit add whose
  high byte the tune never touches) and two in the oscillator, where the frequency
  add's carry is consumed by the pulse bounce as a borrow. The filter's clamp
  prints pre- and post-clamp high byte as `a19`/`a20`, only one of the two
  definitions of the stored byte folding. A folded run whose first copy is not
  element 0 prints `voice[v + 1]` (the 30 s cascade fold). Cells no role reaches
  print as `b101B`/`b1194`; `voice[v].acc_2`/`freq_idx_2` show two fields wanting
  one name.

---

## 7. Budgets and CI

- Full certificate (E1/E2, 149,024 calls): `tools/tuneprog_certify.py` in
  resumable chunks (`--resume`), each invocation < 60 s CPU.
- `wlog` 3.6 M rows as `npz` ≈ 25 MB; coverage ≥ 85 % of `deity_informant/`.

---

## 8. Open risks

- Site identity: `$10D8` alone takes six byte patterns (`RTS` plus `LDA #` with
  five flag values), so the site key drops play-written operand bytes (§4);
  the naive full-bytes key explodes the CFG.

---

## 10. Appendix — measured SMC inventory

Measured over 30 s of music. The 700 s run adds cascade B1's `1382 LDA #` /
`1391 LDY #`, raises some writer counts (e.g. `1022 LDX #` to 5), and totals 88
cells in 73 instructions.

Play-written cells (instruction → operand offsets, number of writer sites):

```
0FE3 LDA #  [1] 1 (wrapper counter)              10D8 LDA #/RTS [0,1] 4+2 (flag / gate)
1022 LDX #  [1] 1   1024 LDA # [1] 1   102C LDX # [1] 2   102E LDA # [1] 2   1036 LDX # [1] 1
1038 LDY #  [1] 1   103A LDA # [1] 1   103C EOR # [1] 1                        (voice 1 SID image)
1053 LDX #  [1] 5   1055 LDA # [1] 3   105D LDX # [1] 1   105F LDA # [1] 1   1067 LDX # [1] 1
1069 LDY #  [1] 1   106B LDA # [1] 1   106D EOR # [1] 1                        (voice 2)
1084 LDX #  [1] 5   1086 LDA # [1] 3   108E LDX # [1] 1   1090 LDA # [1] 1   1098 LDX # [1] 1
109A LDY #  [1] 1   109C LDA # [1] 1   109E EOR # [1] 1                        (voice 3)
10A9 LDA #  [1] 1 (res/route)   10AE LDA # [1] 1 (mode)   10B5 LDA # [1] 2  10B8 ADC/SBC # [0,1] 5+3
10BD LDA #  [1] 2   10BF ADC/SBC # [0,1] 5+3   10C9 ADC # [1] 1 (CP)   10EA LDY # [1] 1 (arranger row)
1128 LDY #  [1] 3 (row timer v1)  1149 LDA abs [1,2] 2 (pattern ptr master v1)  1160 LDA # [1] 1
1164 LDA abs,Y [1,2] 4   116E LDA # [1] 1   1172 LDA abs,Y [1,2] 2   117C LDA # [1] 1   1180 LDA abs,Y [1,2] 2
1193 LDX #  [1] 1  (v1 flag copies / pointer broadcast)
11B0 … 121B (voice 2: same shape)   1238 … 12A3 (voice 3: same shape)
12BE LDA #  [1] 3   12CD LDY # [1] 2   12EF LDA # [1] 3   12FE LDY # [1] 2   1320 LDA # [1] 3   132F LDY # [1] 2
1351 LDA #  [1] 3   1360 LDY # [1] 2   13B3 LDA # [1] 3   13C2 LDY # [1] 2  (cascade A/B counters and row indices)
```

Init-written cells (constants for play unless also above): the SID image
immediates and cascade cells (zeroed/reset), `10CD` (`CMP #` threshold), `10D4`
(opcode `NOP`/`ASL`), `10D8`, `10EA`, `1382`, `13B3`, `13C2`.

Volatile reads: `14CE CMP $D012` (init, 2,227×), `14E3 LDA $D41B` (init, 1×).
Illegal opcodes executed: `ANC`, `ALR`, `SAX`, `LAX zp`, `LAX (zp),Y`, `SBX`.
