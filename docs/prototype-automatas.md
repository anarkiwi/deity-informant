# Prototype: the tuneprog decompiler on defMON's *Automatas* — implementation plan

An end-to-end vertical slice of [tuneprog-decompiler-design.md](tuneprog-decompiler-design.md)
built against one tune, chosen to be the hardest of the nine anatomy exemplars
for the design: `MUSICIANS/G/Goto80/Automatas.sid` (Goto80, defMON export,
2013; anatomy [§3.7](playroutine-anatomy.md)). The prototype is *real* code in
its final place (`deity_informant/tuneprog/`), generic by construction (it must
also certify a simple exemplar unchanged), and it is finished when Automatas
carries a **certificate**: per-call SID-write equivalence over the whole song
plus the periodicity witness, from a tuneprog whose printed form is the
anatomy's §3.7.7 in shape.

Contents

1. Why Automatas, and what "the design is correct" means
2. Ground truth the prototype is held to (anatomy + measurements)
3. Scope: what the prototype builds, to what depth, and what it skips
4. Package layout, data structures, file formats
5. Stage-by-stage implementation
6. Acceptance: the evidence table
6.1 Results (measured)
7. Work plan (ordered, parallelisable), tests, CI, budgets
8. Risks specific to this tune, and fallbacks
9. Appendix A — target shape of the printed tuneprog
10. Appendix B — measured SMC inventory

---

## 1. Why Automatas, and what "the design is correct" means

Every exemplar exercises some design mechanism; Automatas exercises nearly all
of them at once, so a certificate for it is evidence for the design rather than
for one trick:

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

Blackbird (§3.9) is the runner-up — the nastiest CFG (an init that calls into
the middle of play, `NOP #imm` overlapping two instruction streams, `(zp,X)`,
an LZ unpacker filling ring buffers) — but it is single-speed, tiny, has no
opcode cells in play, no volatile reads and no struct-of-code; its tricks are
listed in §8 as the second target once Automatas certifies.

**"The design is correct" for this prototype means, concretely:**

1. the S4 tuneprog (registers, flags, addressing modes and self-modification
   gone; state as regions) reproduces every SID write of every call of the
   whole song, in order, plus init's writes — a certificate with zero
   divergences (§6, E1);
2. the periodicity witness is found and the tuneprog's own state repeats at the
   same `(k, k+p)` (E2), or the horizon and reason are reported;
3. each mechanism above produces the structure the anatomy documents — the SMC
   inventory, the procedures, the regions with stride 49, the two-rate
   schedule, the pinned inputs — *from the generic algorithms alone*, with no
   Automatas-specific code path (E3–E9), and the same code certifies Commando
   (E10);
4. the printed tuneprog has the shape of anatomy §3.7.7 (E11).

---

## 2. Ground truth the prototype is held to

From anatomy §3.7 and from the survey tracer run on this tune (2026-08-16;
30 s and 700 s of music — the latter covers the whole song plus one full loop):

| fact | value |
|---|---|
| container | PSID, load $0FD0–$2FAF, init $0FD0, play $0FE3, 1 subtune, HVSC length 5:23 (323 s), 8580 |
| cadence | init writes CIA-1 TA = $0998 → 2457 cycles/tick; 8.0 ticks per PAL frame; header speed bit set; entry = header `play` (`sub`) |
| ticks in the song | 16,128 main ticks per arranger loop (Σ (d+2) over 168 rows) = 129,024 calls; the arranger jumps to row 0 at row 168 |
| executed sites | 616 total in 30 s (555 play, 61 init); 648 in 700 s (587 play, 61 init) — the anatomy quotes 811 over 24,000 calls (dead only: AF>0 detune path $1448–$1473, RE raw store $170B–$170F); the difference is probably (pc, variant) counting and must be resolved by step 1's report |
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

The full measured SMC inventory is Appendix B; the anatomy's pseudocode
(§3.7.3) and "player in ~30 lines" (§3.7.7) are the reference for the printed
form (Appendix A).

---

## 3. Scope

**Built, generic, in its final place** (`deity_informant/tuneprog/`): S0
(entries/cadence, init runner), S1 (op-level tracer, reference log, inputs,
state hashes, resumable), S2 (residualised lift, variant nodes, edges,
procedures with clone-per-entry, tail calls, computed switches), S3 (regions,
kinds, initial contents, envelope asserts), S4 (SSA over registers/flags,
DCE, constant/copy propagation, relational and counter idioms; memory ops kept
in order — no memory SSA), IR + interpreter + JSON, S7 (Python codegen,
pseudocode printer, certificate), S8 (differential verifier, periodicity),
and enough of S5/S6 to print Automatas readably: natural loops and if/else on
reducible parts with `goto` fallback, `switch` from variant/computed nodes,
`for` recognition for the `SBX #$31` loop, phase recognition for the wrapper's
`cnt & 7`, stride-49 struct view of the per-voice cells, SID-image role for the
write-band cells, role names for counters/cursors/pointers, `sid[v].reg`
printing.

**Should-have (design-proving, cheap):** writer-derived enumeration of
unobserved opcode-cell variants (`$10D4` gets both `NOP` and `ASL` arms; the
unexecuted arm is marked unverified); pinned-input override so the tune is
certified under *both* SID models; collapse of the `$D012` busy-wait to
`while input() != c` in the printer.

**Stretch (not required for the certificate):** S6 copy folding of the three
write-band/row-advance blocks into `for v`; pointer-broadcast unification (four
patched operand pairs → one 16-bit pointer variable); sidTAB grammar table from
the `$168C` decision tree; 16-bit arithmetic folding; family name dictionary
(undefmon labels). Copy folding and 16-bit folding landed (§6.1); the other
three did not.

**Out of scope for this prototype:** Blackbird's `(zp,X)`, `NOP #imm` overlap
and LZ unpacker (they are the next target and get no special casing here);
second interrupts; ROMs; numba; a campaign driver.

**Non-negotiable:** no tune-specific branches in the code. The regression guard
is `Commando.sid` (Hubbard, §3.1): the identical pipeline must certify it at S4
over its HVSC length in the same test run.

---

## 4. Package layout, data structures, file formats

The layout as planned; for the layout as built, and its line counts, see
[`tuneprog.md`](tuneprog.md).

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
IR (design §4): Tuneprog{meta, storage, inputs, procs}; Proc{name, params, blocks}; Block{label, stmts, term}
```

Files per tune (`--out DIR`): `trace.json` + `trace.npz`, `procs.json`,
`regions.json`, `tuneprog.json` (IR after each stage as `tuneprog.S4.json`,
`.S5.json`, `.S6.json`), `tuneprog.py`, `tuneprog.md`, `certificate.json`.

---

## 5. Stage-by-stage implementation

### S0 — `machine.py`

- `MachineImage.from_sid(data)`: `c64.poweron_ram()` overlaid with the load
  band; keeps `(lo, hi)`; `header` via `pysidtracker.header` or `c64.load_psid`.
- `find_entries(data)`: `pysidtracker.trace_init` + `playroutine_cadence` →
  `schedule = [{"kind": "sub"|"irq", "addr", "cycles_per_tick", "source"}]`
  (Automatas: one entry, `sub $0FE3`, 2457, `cia_timer`); handler discovery
  as `c64.installed_handler` when `play = 0`; refuse when nothing is found or a
  second source is armed (CIA-2 latch/NMI vector) — Automatas arms none.
- `init_runner(vm, init, song, budget)`: run to RTS with a budget; treat `JMP *`
  as done; on budget exhaustion with an armed source, deliver it (design S0) —
  not needed by Automatas, implemented minimally (the `JMP *` case) with the
  interrupt-delivery path stubbed behind a clear `NotImplemented` refusal.
- Port/CIA hooks: `TraceVM` gets a `port_bank()` that reads `$00/$01` and
  classifies `$D000–$DFFF` stores (Automatas never writes `$01`; the hook is
  exercised by a unit test), and a minimal CIA timer read model (count-down
  from the latch at cycle rate, ICR bit on underflow) so busy-waits terminate.

### S1 — `trace.py`

`TraceVM(PcodeVM)`:

- **Per-op attribution.** Override `compile_record` to emit `rd(a, sz, i)` /
  `wr(a, v, sz, i)` with the op index `i` (a 20-line variant of `vm._emit_line`;
  the base VM is untouched). Pointer fetches of `(zp),Y` are their own LOAD ops
  and are attributed separately from the target load.
- **Sites.** In `step`: `key = (pc, opcode, fixed operand bytes)`; a first pass
  is not needed — record `first_bytes`, `variants` (full bytes) and, at the end,
  recompute keys once the play-written cell set is known (operand bytes ∈
  written cells drop out of the key). Record `count`, `phase`
  (init/tick+entry), index register value for indexed modes.
- **Edges and calls.** After computing `next_pc`, record the edge with its
  kind. Shadow call stack: on `jsr` push `(site, sp_after, ret_pc)`; on `rts`
  pop when the popped return address equals the top's `ret_pc` (matched) else
  record unmatched; on `rti` likewise for IRQ frames; the driver's dummy return
  is pushed as a synthetic frame. Tail entries (an edge into a known JSR target
  that is not a `jsr`) are recorded as `tail` when the target set is known
  (second pass over edges).
- **Logs.** `wlog` for `$D400–$D7FF` and `iow` for the rest of `$D000–$DFFF`,
  with call index and cycle; `init_writes`; **inputs** for every read of
  `$D000–$DFFF` (except acks, classified), of never-written addresses outside
  the load band, and of A/X/Y before their first write in a tick (live-in
  registers). Input policy: `record` (default), `replay` (feed a recorded
  stream, used by S8's interpreter run), `override {addr: value}` (pin the SID
  model: `$D41B` bit 0).
- **State hash** per call: `blake2b` over the bytes of the play-written set
  (monotone), keyed with its size; first repeat → `period`.
- **Resumable.** `run_trace(image, entry, calls, ..., resume=path)` pickles
  `(vm.mem, regs, cycles, shadow stack, accumulators)` every N calls; a rerun
  continues. Needed for the 60 s per-script rule (§7).
- Output `Trace` → `trace.json` (structure) + `trace.npz` (bulk).

Automatas expectations: 1 entry; `cycles_per_tick = 2457`; sites ≈ 620 (30 s)
→ ≈ 650 over a full loop (report the (pc, variant) count too, to reconcile the
anatomy's 811); `$D012`/`$D41B` inputs only in init; edges include
`jmp $1003→$1022`, `jsr $100F→$1022`, `jsr ×6 → $168C`, `jsr $0FE9→$1003`,
`jsr $0FEF→$1006`; matched RTS for `$10D8` (variant $60) against `$100F`, for
`$14CA` against `$0FE9`/`$0FEF`; the wrapper's `$0FE4` in the written set.

### S2 — `lift.py`, `cfg.py`

`lift_site(image_post_init, site, cells)`: call `lift(mem, pc)` on the
variant's bytes; walk `rec["prov"]["ops"]` and, for every const varnode whose
source offsets intersect `cells`, replace it by a `LOAD` from the cell address
(size 1 for immediates; the two address bytes of `abs`/`abs,X`/`abs,Y` become a
16-bit `LOAD` combined into the address expression); `prov["ctrl"]` likewise
turns a patched `jmp`/branch operand into a computed target (`switch`). Result:
a `LiftedSite` with P-code ops over registers, flags, uniques and memory —
identical to the lifter's records except that cell reads are explicit.

`build_procs(trace, lifted)`:

1. entries = tick entries ∪ {init} ∪ JSR targets. Every entry gets a `Proc`.
2. Frame model: walk the trace's edges per activation. A `jsr` edge creates a
   call statement in the caller and continues at `ret_pc`; a `tail` edge into
   an entry makes the current frame's procedure *that* entry from then on
   (`return call(entry)`); a matched `rts` is `return` of the frame's current
   procedure; an unmatched `rts` / `jmpind` / patched jump becomes
   `switch(expr) {observed targets; default: trap}` where `expr` is the value
   the site read (stack word / pointer / cell).
3. Nodes are `(proc, pc, variant)`; a pc reached under two procedures is
   cloned; a pc with several opcode variants becomes a `switch(load(pc))`
   node with one arm per variant (writer-derived enumeration adds arms for
   constant values stored by decompiled writers but never observed, marked
   unverified).
4. Call summaries from the trace: registers/flags read-before-write and written
   per callee; regions touched (after S3, a second pass).

Automatas expectations: procedures `wrapper_init($0FD0)`, `init($14FE)` (tail
of `$1000`), `sid_model_detect($14CB)`, `wrapper_tick($0FE3)`,
`p1003` (= `return call(main)` after the tail edge), `main($1022)` with the
`$10D8` switch {`$A9`: continue; `$60`: return}, `sub($1006)` containing the
call to `main` and its own clone of `$12BE–$14CA`, `row_apply($168C)`.

### S3 — `regions.py`

Op-level access relation from `Trace.sites[*].reads/writes` plus the
residualised cell reads: union-find over addresses touched by the same op;
components → `Region`s; kind by writer phase (state / init_constant / const /
image / io); `stride = gcd` of the observed index differences of the accessors,
`fields` = distinct constant offsets mod stride; `init_bytes` from
`image_pre`; envelope = observed extent per accessor.

Automatas expectations (from the survey prototype, instruction-level): ~116
regions; the write-band immediates form 8 stride-49 regions of 3 cells each
(`$1023/$1054/$1085` … `$103D/$106E/$109F`), the 9-byte voice records at
`$1019/$104A/$107B` likewise, cascade counters/indices at `$12BF/$12CE/…`, the
note cells `$12CC`/`$135E` (+$31); the four pattern-pointer operand pairs per
voice as 16-bit state cells; the freq table (312 bytes), sidTAB pointer/DL
tables, arranger, patterns and rows as `const`; the filter/status immediates
and the wrapper counter as scalars; `$FB/$FC` as a pointer region and the
sidTAB row bytes it reaches as a `const` region distinct from it. Note the
overlap the design predicts: an SMC cell region lies *inside* executed
instruction bytes — regions may include code addresses.

### S4 — `ssa.py`, `idioms.py`, `ir.py`

- IR construction from lifted P-code per procedure: registers/flags/uniques →
  SSA `let`s (dominance frontiers via `networkx`), memory ops stay ordered
  (`load`/`store` on regions, `sidw` for classified SID stores, `iow` for VIC/
  CIA); calls use summaries (defs of registers/flags the callee writes).
- DCE (flag ops nobody uses, dead register copies), copy propagation, constant
  propagation from `const`/`init_constant` regions and the post-init image.
- Idioms: compare-then-branch → relational terms; `DEC/INC` + branch → signed
  tests; `load; op; store` on one scalar region → compound assignment (print
  only); `ASL A`/`BPL`/`BCC` on a flag byte → bit tests; `ANC #$7F` → `and` +
  `C = 0`; `SBX #imm` → `X = (A & X) − imm` with flags; `LAX` → two `let`s;
  `SAX` → `store(A & X)`.
- `interp.Interp`: the reference executor over the JSON IR with the flat memory
  image as backing store; regions are views; envelope asserts on every indexed
  access; `sidw`/`iow`/`input` hooks. **Certificate #1** is produced here (S8).

Automatas expectations: the write band becomes 23 `sidw` statements (7 per
voice + `$D417` + `$D418`; the filter adds `$D416` = the measured 24 per call)
whose values are `load(cell)`; the filter block becomes `acc ±= step` with the sign
selected by `switch(load($10B8))` arms; the wrapper becomes
`cnt = load($0FE4); if (cnt & 7) == 0: call main else call sub; store($0FE4, cnt+1)`;
no `A`/`X`/`Y`/flag names survive in the emitted Python except as locals.

### S5 — `structure.py`

Dominator-tree natural loops → `while`; if/else via post-dominance; the
variant/computed nodes → `switch`; `goto` for the residue; `for` when a loop's
induction variable is a register with a small observed domain stepped by a
constant (`X` ∈ {$62,$31,0}, `SBX #$31`); phase recognition: the tick's first
branch on a state scalar compared with constants (`cnt & 7`). Structural only.

### S6 — `recover.py`

Stride views (`voice[v].pw_lo` for the stride-49 triples), roles: a region
whose loads flow unchanged into `sidw($D400+7v+k)` → `sid_image[v].reg_k`;
`DEC…BPL/BMI` + reload → `timer`; a region indexed by another region's value →
`cursor into R`; zero-page pairs used by `(zp),Y` → `ptr`; the freq table via
`pysidtracker.notefreq.locate_note_freq` (falls back to "u16 table" if the
detector rejects the 12 leading pseudo-entries); names by role and voice.

### S7 — `emit.py`

`tuneprog.py`: one Python function per procedure over `mem: bytearray`,
`sidw(addr, val)`, `iow(addr, val)`, `inp(name)`; `tuneprog.md`: the printer;
`certificate.json` (design §7 schema, `compared` includes `init writes`,
`tick sid writes`, `tick schedule effects`).

### S8 — `verify.py`

`verify(tuneprog, trace, calls, resume)`: run `init(song)` then `tick()` × N on
the emitted Python (and, on a shorter prefix, on `interp.Interp` to prove codegen =
interpreter), feeding pinned inputs; compare per-call `(addr, val)` lists and
init's list with the reference; on mismatch report tick, index, expected/got
and the IR statement's origin site; compute the tuneprog's own state hash per
call and check periodicity against the trace's `(k, k+p)`; envelope traps
count as divergences. Chunked and resumable like the tracer.

---

## 6. Acceptance: the evidence table

| id | design claim | check | expected on Automatas |
|---|---|---|---|
| E1 | per-call equivalence | `certificate.divergence == null` from init through the first state repeat (149,024 calls, §2: the transient plus one full period) + init writes; also under `--sid-model` overridden to the other value | 0 divergences, both models |
| E2 | periodicity certificate | trace and tuneprog state hashes repeat at the same `(k, k+p)`; no inputs after init | `k = 20,000`, `p = 129,024` (measured, §2) → `complete: true`: writes verified over `[0, k+p)` and equal states at `k` and `k+p` cover every later call by determinism |
| E3 | tick model | `schedule` = one entry, 2457 cycles/tick; the wrapper's `cnt & 7` becomes the tick's top-level `switch/if` | as stated |
| E4 | SMC operand residualisation | every play-written operand cell in Appendix B is a `state` region read by `load` at its instruction; zero constants remain for those operands | 84+ cells, incl. `$0FE4` and the 4-per-voice pointer pairs |
| E5 | SMC opcode variants | `$10D8` → switch {continue, return}; `$10B8/$10BF` → {ADC, SBC}; `$10D4` → {NOP, ASL} with one arm unverified (writer-derived) | as stated |
| E6 | procedures / clone-per-entry | procs as listed in S2; `main` has two exits; `sub`'s clone of `$12BE–$14CA`; matched RTS accounting exact (0 unmatched) | as stated |
| E7 | regions / struct-of-code | ≥ 8 stride-49 triples in the write band + records + cascade cells; kinds as in S3; envelope asserts never fire during E1 | as stated |
| E8 | illegal opcodes | the six kinds lift and the certificate holds (E1) | — |
| E9 | inputs | exactly two input sites (`$D012` wait, `$D41B`), both in init; play consumes none | as stated |
| E10 | genericity | `Commando.sid` certified at S4 over its HVSC length by the same code, no flags | 0 divergences |
| E11 | readability | `tuneprog.md` has the structure of Appendix A: two rates, three tables, per-call `writeout → filter → cascades → oscillator`, main-only row advance; per-voice fields named by role | reviewer checklist |
| E12 | codegen = interpreter | `interp.Interp` and `tuneprog.py` agree on a 5,000-call prefix | 0 divergences |
| E13 | budgets | any single script invocation ≤ 60 s CPU via chunking; full E1 wall ≤ 15 min on one core-equivalent | measured, reported in the certificate's `cost` |

Everything in E3–E9 is checked mechanically against expected values written
into `tests/tuneprog/test_automatas.py` from Appendix B and anatomy §3.7.

---

## 6.1 Results (measured)

Certificates: `docs/certificates/{automatas,automatas-6581,automatas-8580,commando-song1,commando-song2}.json`,
produced by `tools/tuneprog_certify.py` (= `deity-informant tuneprog`) and re-run
against the committed traces after S5/S6 landed. Printed forms:
`tuneprog.md` per output directory (E11 excerpts below).

| id | expected | measured |
|---|---|---|
| E1 | 0 divergences over the whole song, both SID models | **0**, 149,025 calls from init, under the traced model and under `--sid-model 6581` and `8580` separately (three certificates); Commando songs 1 and 2 likewise 0 over 11,780 calls each |
| E2 | period 129,024, `complete: true` | **period 129,024, first repeat at call 149,024** (transient 20,000), trace and tuneprog hashes agree at the same `(k, k+p)`; `complete: true` |
| E3 | one entry, 2457 cycles/tick, `cnt & 7` at the top of the tick | `schedule = [sub $0FE3, 2457, cia_timer]` (8.0 calls/frame); the printed tick is `if (call_counter & 7) != 0: sub() else: main()` |
| E4 | every play-written operand cell is a load | **88 cells** (110 play-written bytes), all of them constant-address loads in the IR, `$0FE4` included; the 35 cells only `init` patches are loads there and constants in the tick (the rule the Follin prototype corrected, `docs/prototype-follin.md` section 3) |
| E5 | opcode cells become switches | `$10D8` {`LDA #`, `RTS`}, `$10B8`/`$10BF` {`ADC`, `SBC`} -- three variant switches with a trap default; `$10D4` has one arm per model (no writer-derived arm: init's writer stores a value the trace computes, so the second variant comes from the model override) |
| E6 | procedures, clone-per-entry, exact RTS accounting | **8 procedures** (`init`, `tick`, `p_1000`, `p_1003`, `p_1006`, `p_1022`, `p_14CB`, `p_168C`), 305 blocks, **0 unmatched RTS**, 6 JSR targets, `$1022` entered by both `JMP` and `JSR` |
| E7 | >= 8 stride-49 triples, no envelope trap | **21 stride-49 regions** of 102, **0 envelope traps** over the whole song |
| E8 | six illegal-opcode kinds on the hot path | **6** (`ANC`, `ALR`, `SAX`, `LAX zp`, `LAX (zp),Y`, `SBX`) |
| E9 | two input sites, both in init | `$D012` at `$14CE` (2,227 reads) and `$D41B` at `$14E3` (1 read), both `init`; play consumes none |
| E10 | Commando certified by the same code | songs 1 and 2, 0 divergences, no flags but `--song` |
| E11 | `tuneprog.md` reads like appendix A | see below; asserted mechanically in `tests/tuneprog/test_hvsc_print.py` |
| E12 | interpreter and generated Python agree | 2,000-call prefix on every certificate (500 for the 10 s model-override run) |
| E13 | every invocation <= 60 s CPU | trace 149,025 calls in ~2 chunks; front end + S4 0.2 s, verification 9.2 s (16,136 calls/s), S5/S6 + printing 0.4 s |

E11, the printed Automatas tuneprog (`tuneprog.md`, verbatim excerpts, `...` elides):

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

and Commando song 1 (`tuneprog.md`, verbatim), the design's section 4 illustration:

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

**Reconciliations with the plan**

- **811 executed sites (section 2) is a static count.** The anatomy's 811 counts
  instruction starts in the executed *bytes*; the tracer counts executed pcs:
  616 at 30 s, **648 pcs / 651 site keys over the full run** (a pc with two
  opcode variants is two keys). No dead code was missed; the difference is the
  counting rule.
- **`$10D4`'s second variant is not writer-derived.** Init computes the value it
  stores from `$D41B`, so the decompiled writer offers no constant to enumerate;
  the arm exists only under the other SID model. Both models are therefore
  certified separately (`--sid-model`), which is stronger than one unverified arm.
- **Commando's `$54EF`/`$54F8` merge into the frequency-table region.** The
  vibrato reads entry `note+1` and overruns the 96-entry table, so union-find
  joins the table's tail with the per-voice cells that follow it -- exactly the
  overrun the design predicts (section 6, "traps"). The merged region is still
  recognised as the note table (`FREQ`, u16le, 80 entries at the traced horizon).
- **S5/S6 do not edit the IR.** Everything below is a *view*: a copy of the
  certified S4 program that structuring, texture removal, 16-bit views,
  outlining and copy folding reshape, plus a print-only dead-value pass.
  `tuneprog.S5.json`/`.S6.json` are annotations; the certificate's `stage` reads
  `S6` with a `presentation` note, and the tests assert the S4 JSON is
  byte-identical before and after (`pipeline.present` is the whole stack).
- **Names are role-derived, so appendix A's semantic names do not all appear.**
  `pw_lo`/`freq_hi`/`ctrl_eor` come from the data flow into a SID register,
  `timer`/`counter` from `DEC`/`INC` with a reload, `cursor`/`ptr` from being an
  index or an address, `FREQ` from the octave ramp, `voice[v]` from stride and
  element count. Fields no role reaches (`af`, `ps`, `detune`, the pre-shifted
  flag copies) print as `voice[v].b101B` etc.; naming them needs the anatomy's
  reading, not the trace.
- **At a short horizon the note table is two parallel columns.** `FREQ_LO`/`FREQ_HI`
  at 30 s; over the full run the TR overrun merges them into one `FREQ` region.
- **Copies fold when their shapes are equal, and only then** (`unroll.py`). The
  write-out is one `for v in 0, 1, 2:` over seven registers: every difference
  between the copies is a constant that steps by the struct stride (49), by the
  SID voice size (7) or by the argument scale (`row_apply(x=(v * $31))`), the
  region ids agree, and one region is walked with one stride.
- **The cascade is one body under the copy index** (`copyrows.py`,
  `copymerge.py`; S2c, in the certified program). The cascade at `$12BE` is five
  chained copies of one 18-row block over per-copy cells at 30 s -- six of 19 rows
  over the whole song, where cascade B1 runs -- in both the procedure that falls
  into it and the one that jumps to it; the fold makes each of them one
  body over `v`, with the three addresses the copies disagree on in one per-copy
  table -- shared between the two, since two clones of one procedure are not two
  copies of its data, which is what lets `fold.outline` keep them one helper.
  The oscillator's `$16CD` pair folds the same way. Two families refuse: `$112A`
  ×3 and `$16AB` ×2 each have an edge that leaves one copy for another anywhere
  but the chain edge, which `v` cannot name -- the reason is in the certificate's
  `copies.refused` and in the printed header, and their copies stay as they were.
  At 30 s the S4 program falls from 895 statements to 815; the printed document
  from 880 lines to 801. The three row-advance blocks still do not fold: they read
  three different table regions, whose addresses are not one relocation of each
  other, and no chain edge joins them.
- **The cascade prints as its own loop** (`copyview.py`, `loops.py`; S6, #244).
  Each merged cascade is `for v in 0..5:` over the whole song (`0..4` at 30 s, where
  cascade B1 never runs) with the row cursor and timer as
  `rec2[v].cursor_12CE`/`rec2[v].timer_4` — the columns whose values
  step by the 49-byte record become that step in `v`, and the printer's existing
  stride view names the field. Two columns keep their table read
  (`copies_12BE[v + $18]`, the `row_apply` argument, and `copies_16CD[...]` in the
  oscillator pair): their copies name cells at different offsets of one record, so
  no field name covers them and the address stays visible. The `for` comes from
  the coverage vector, not from the exit tests — this loop leaves through a
  `switch` arm, which the recurrence analysis cannot read. The printed document
  falls 744 → 716 lines over the whole song (800 → 782 at 30 s), and
  `fold.outline` still keeps the procedure and its clone one `cascades()` helper.
- **Runs become helpers when they are shared or when they name a part**
  (`fold.py`). `main` is `writeout(); filter(); switch {row_advance(); cascades();
  oscillator()}` and `sub` is the RTS patch, `main()`, then the two helpers it
  shares -- one printed copy each, ~200 duplicated lines gone. A run is outlined
  only when no live value crosses its boundary, and two runs share a helper only
  when their alpha-renamed statement form is equal (the machine's frame pushes
  and the arguments no callee reads are excluded, as the printer drops both);
  `sub`'s clone, which S4 block merging glued to its own prologue, is matched by
  skipping that prologue.
- **The machine texture is gone from the hot path.** No `sp` (a push and a pop
  of one stack slot become `saved = ...`/`... = saved`, and a JSR frame is the
  call), no `goto` (nested tests that share a target print as `or`/`and`), 156
  machine temporaries down to 27 (a value folds into its uses across statements
  that cannot alias it -- regions are disjoint -- but never past a store to its
  own region, a call or another input read), and the filter's carry chain prints
  as `filter.acc += filter.step` over a named `lo|hi` view after the two patched
  ADC/SBC cells are proved to be one variable and their switches merged.
- **The `$D012` busy-wait collapses** to `while input($D012) != $FC: pass`, and
  the printer keeps the machine's own plumbing (JSR frames, register copies
  nothing reads) out of the text without touching the executable IR.
- **What is still texture.** Five `carry(` remain, none in `main` or `filter`:
  three page-crossing assertions on the pattern pointers in `row_advance` (a
  16-bit add whose high byte the tune never touches, so there is no second byte
  to fold with) and two in the oscillator, where the frequency add's carry is
  consumed by the pulse bounce as a borrow. The filter's clamp prints its pre-
  and post-clamp high byte as two values (`a19`, `a20`) because only one of the
  two definitions of the stored byte folds. A folded run whose first copy is not
  element 0 prints `voice[v + 1]` (the 30 s horizon's cascade fold) instead of
  renumbering the loop. Cells no role reaches still print as `b101B`/`b1194`,
  and `voice[v].acc_2`/`freq_idx_2` show where two fields want one name.

---

## 7. Work plan, tests, CI, budgets

Ordered steps; each is a PR-sized unit with its own tests; A/B/C… mark units
that can proceed in parallel once their inputs exist.

| step | unit | depends on | tests | ~size |
|---|---|---|---|---|
| 1 | `trace.py` per-op attribution, sites/edges/calls, logs, inputs, hashes, resume, `trace.json/npz` | `PcodeVM` | assembled snippets (jennings): per-op sets, JSR/RTS pairing incl. tail entry, variant keys, input record/replay/override, resume equivalence | 400 |
| 2 | `machine.py` entries/cadence/init runner/port+CIA hooks | 1 | Automatas: schedule; a synthetic `$01` banking snippet; a CIA busy-wait snippet | 200 |
| 3A | `lift.py` residualisation + variants | 1 | immediate cell → load; abs-address cells → load16; opcode variants; prov coverage of all modes | 200 |
| 3B | `regions.py` | 1 | stride/field detection on synthetic stride-1/7/49 layouts; pointer vs stream separation; kinds | 250 |
| 4 | `cfg.py` procedures/clones/switches/summaries | 3A | tail call, shared tail cloning, unmatched RTS switch, `JMP (ind)`, dual-entry routine (a synthetic model of `$1022`) | 400 |
| 5 | `ir.py` IR + JSON + `Interp` | 3A, 3B | differential fuzz: random straight-line 6502 (as `tests/test_vm.py` does) lifted → IR → `Interp` equals `PcodeVM` | 400 |
| 6 | `ssa.py` + `idioms.py` | 4, 5 | each pass preserves `Interp` results on fuzz programs; DCE removes all flag ops in a flag-free program | 500 |
| 7 | `emit.py` (Python codegen + certificate) + `verify.py` | 5, 6 | E12 on Commando; **milestone A: E1/E2/E4–E10 on Automatas** | 400 |
| 8 | `structure.py` | 6 | reducible synthetic CFGs; `for` recognition; phase; goto fallback keeps E1 | 400 |
| 9 | `recover.py` + printer | 8 | roles on Automatas/Commando; **milestone B: E11** | 400 |
| 10 | `cli.py`, docs, CI wiring | 7, 9 | CLI smoke on Commando (short horizon) | 100 |

Parallelisable: 3A ∥ 3B; 5 ∥ 4; 8 ∥ 9's role logic; each unit is small enough
for one agent with the interface above.

**Tests and CI.** Unit tests are hermetic (assembled snippets) and run in the
default job with `-n auto`, coverage ≥ 85 % of `deity_informant/`. End-to-end
tests fetch `Commando.sid` and `Automatas.sid` through
`pysidtracker.testing.resolve_tune` into `.oracle-cache` (gitignored) and are
marked `hvsc`; the CI `oracle` job runs them at a **short horizon** (Commando
20 s, Automatas 30 s of music ≈ 12k calls, ≈ 40 s of tracing + verify — under
the per-script budget; note that 30 s of Automatas never reaches cascade B1's
`JSR $13B0`, so short-horizon tests assert equivalence, not the full-song
structure of E3–E9); the **full certificate** (E1/E2 to the first state repeat, 149,024 calls) is
`tools/tuneprog_certify.py`, run in resumable chunks (`--resume`), each
invocation < 60 s CPU, results committed as `docs/certificates/automatas.json`.

**Budgets** (measured with the survey tracer, Python): 277 k instructions/s
traced; 12,029 calls (30 s of music) in 9 s wall; the complete certificate
needs 149,024 calls ≈ 110 s of tracing and a similar verify → 2–3 resumable
chunks each. Trace memory: sites × per-op sets ≈ MBs; `wlog` 3.6 M rows as
`npz` ≈ 25 MB.

**Coding rules** (global CLAUDE.md): black, pylint clean (no unused
imports/vars), xdist, numpy for bulk arrays, no narrative comments; every
module ≤ ~500 lines; the survey tracer is *replaced* by `trace.py` (the survey
tools then import it).

---

## 8. Risks specific to this tune, and fallbacks

- **Site identity vs varying operands.** `$10D8` alone shows six byte patterns
  (`RTS` + `LDA #` with five flag values). Keying sites by `(pc, opcode, fixed
  operand bytes)` after the written-cell set is known is essential; the naive
  full-bytes key explodes the CFG. Fallback: two-pass tracing (a first short
  run to learn cells).
- **The `$1022` dual entry.** If the frame model mishandles the tail edge
  `$1003 → $1022`, `main`'s `$14CA RTS` looks unmatched. The unit test in step
  4 models exactly this shape (JMP-entered and JSR-entered routine with a
  patched exit) before Automatas is attempted.
- **Regions over code bytes.** Union-find must run over *all* addresses,
  including executed instruction bytes; residualised cell reads are ops like
  any other. A region that spans an opcode byte (`$10D8`) and its operand
  (`$10D9`)? They are touched by different ops (variant switch reads `$10D8`,
  the `LDA #` reads `$10D9`) so they stay separate — asserted in step 3B.
- **Periodicity closes only after a transient.** The state at the first
  arranger jump differs from the state at init (slide accumulators, PW bounce
  phase, cascade counters); the survey run shows the repeat at call 149,024
  against call 20,000, so the tracer must keep every call's hash and run to
  the first repeat rather than stopping at the first arranger jump (129,024).
- **Cost.** 100 s traced per full pass exceeds the per-script rule; chunking is
  mandatory from step 1 (resume state), not an afterthought.
- **The two SID models.** `PcodeVM`'s `$D41B` model returns `(cycles>>3)&0xFF`,
  so which model the default trace picks depends on init timing; the
  `override` policy makes both explicit and both must certify (E1).
- **`LAX (zp),Y` at `$1745`** reads the row through `$FB/$FC`; the pointer
  region and the row region must not merge (op-level attribution) or the row
  bytes appear as `state`. Covered by 3B's pointer/stream test.
- **After Automatas: Blackbird.** The next target's checklist: `(zp,X)`
  addressing (`LDA ($E0,X)` — pointer *table* indexed by X: regions must key
  the pointer fetch by X), `NOP #imm` two overlapping sites, `JMP`↔`RTS`
  opcode patched around init's `JSR $1009`/`JSR $1003` (a JSR target inside
  play — clone-per-entry again), the four-phase patched `JMP` low byte (switch
  with three targets), carry live across eight instructions (SSA), the LZ
  unpacker's ring buffers as `state` arrays. No new mechanism is expected; it
  is the CFG stress test.

---

## 9. Appendix A — target shape of the printed tuneprog

What `tuneprog.md` should read like after S5/S6 (from anatomy §3.7.7; names
are role-derived, not the undefmon labels unless the stretch dictionary is
used):

```
meta:    entry sub $0FE3 every 2457 cycles (8.0/frame, CIA-1); 1 subtune; sid_model = pinned($D41B) → 8580
state:   call_counter (@$0FE4)
         voice[3] stride 49 { slide_lo, slide_hi, af, ps, detune, vbit, vmask,        -- record @$1019+49v
                              pw_lo, pw_hi, freq_lo, freq_hi, sr, ad, wg, wgx,        -- SID image @$1023.. (immediates)
                              row_timer, pat_ptr(4 copies), flag_raw, flag1, flag2, flag3,
                              cascA.cnt, cascA.idx, cascB.cnt, cascB.idx, note_base, note }
         filter { res_route(@$10AA), mode(@$10AF), acc_lo, acc_hi, step_lo, step_hi, dir(opcode @$10B8/$10BF),
                  cp(@$10CA), thr(@$10CE), scale(opcode @$10D4) }, flag(@$10D9), arr_row(@$10EB)
const:   FREQ_LO/HI u8[156] @$1554/$15F0 ; SIDTAB_LO/HI @$1800/$1900 ; DL @$1E00 ; PAT_LO/HI @$1A00/$1A80
         ARR0/1/2 @$1B00/$1C00/$1D00 ; PATTERNS @$1F00.. (stream) ; SIDROWS @$2C8F.. (records)
inputs:  init: $D012 wait ×2227, $D41B ×1

tick():                                  -- $0FE3
  cnt = call_counter; call_counter += 1
  if cnt & 7 == 0: main() else: sub()

main():  writeout(); filter(); rowadvance(); cascades(); oscillator()
sub():   writeout(); filter();               cascades(); oscillator()      -- via the $10D8 return

writeout():  for v: sid[v].pw = voice[v].pw_lo/hi ; sid[v].freq = …; sid[v].sr; sid[v].ad; sid[v].ctrl = wg ^ wgx
             sid.res_route = filter.res_route ; sid.mode_vol = filter.mode | $0F
filter():    acc ±= step (switch dir) ; if acc_hi < 0: acc_hi = thr ; c = acc_hi + cp ; clamp ; sid.cutoff_hi = c (<<1 if scale)
rowadvance(): if flag < 0: gap = flag & $0F ; timers = gap ; row = ARR[arr_row] (jump on $FF) ; pat_ptr[v] = PAT[row[v]] ; arr_row++
             for v (unrolled ×3): if timer < 0: consume(v) elif --timer < 0: prepare(v)
cascades():  for each of 6 (unrolled): if cnt == 0: apply(row) elif cnt > 0: cnt--
oscillator(): for v in ($62,$31,0): freq = FREQ[36+note] (+ slide acc | + detune) ; pulse bounce by ps
row_apply(row):  flags1/flags2 column decoder → stores into the cells above     -- $168C
```

The S4 form is the same program with `load(cell)`/`store(cell)` and explicit
byte arithmetic; the S6 names come from roles (SID image by data flow to
`sidw`, timers by `DEC…reload`, cursors by "indexes region R").

---

## 10. Appendix B — measured SMC inventory (30 s of music; the 700 s run adds cascade B1's `1382 LDA #`/`1391 LDY #`, raises some writer counts — e.g. `1022 LDX #` to 5 — and totals 88 cells in 73 instructions)

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
immediates and cascade cells (zeroed/reset), `10CD` (`CMP #` threshold),
`10D4` (opcode `NOP`/`ASL`), `10D8`, `10EA`, `1382`, `13B3`, `13C2`.

Volatile reads: `14CE CMP $D012` (init, 2,227×), `14E3 LDA $D41B` (init, 1×).
Illegal opcodes executed: `ANC`, `ALR`, `SAX`, `LAX zp`, `LAX (zp),Y`, `SBX`.
