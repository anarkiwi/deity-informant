# register-model-lift: implementation plan (phased, evidence-based)

## The claims discipline, first

**A claim that something is unliftable — "refuses", "cannot fuse", "must stay",
"only X removes it" — is admissible only with the disassembly behind it and a
shredder fixture that pins it.** `tools/disasm_tune.py` reads any cached tune
(range or opcode scan); `tests/test_shred_regmodel.py` (§5.4) is the fixture
index. A mechanism is *explained* when its fixture builds, gates, and its
assert states the measured verdict — an xfail names the phase that will flip
it, an invariant names what no phase may break, a control names what is
already liftable and refutes a myth. Do not re-derive a mechanism from this
doc's narrative: read its fixture, and if a claim has no fixture, treat it as
unverified and consult the disassembly before acting on it. Both prior course
corrections on this plan were exactly that: a claim acted on without reading
the machine, resolved in minutes once the instructions were on the record.

Status: in execution. **Phases 0, 1, 2a, the whole of 2b (b0–b5), 2c, 3a and 2.5
are DONE**; the queue is **3b**, then Phase 3. 2.5 built the value walker as an
instrument and the in-edge map R8 is a precondition of: the map closes on
**6,350 of 7,278 labels and in 584 of 624 tunes**, and the edge kind 2c's
withdrawal does not name — a raw `call` into a label of the calling list — is the
one that broke a `goto`-only map, caught by the differential guard as 334
contradictions before any prose (§2 Phase 2.5 correction 1). It re-priced two
customers up (**65 of 65 G2-shaped wide stores bounded**, so R1's residue is the
38 write-throughs) and two down, and it settled the memory question: memory
value-sets are the binding premise for `extent_unmappable` **and for nothing
else**. 3a changed no lift:
it made the frameprog artifact total (major 1: `image`/`dispatch`/`evidence`) and
gave the sweeps a content-keyed decompile cache (§6, post-3a). 2b's rewrite lifted
**251 of 325 webs over 116 tunes, retiring 1,000 ⊤ loads**; the whole 74-web
residue is `web_unnamed` — rung (d)'s read-side refusal, pinned by the
`dual_store_*` fixture family. b3's static enumeration then took block-rooting
from **441 to 480 roots** and certified **99 of the 103 extents it claims**
against b0's observed run; what it does not reach is the registry's coverage of
computed rows (`extent_unmappable`, 399 webs, now **Phase 6's** to answer,
pinned by `computed_rows`). **Zero webs in the measured residue are unliftable
in principle** (§6, byte-residue challenge). 2c then took the stack fabric out
of 47 more tunes, moving R7's cap from 326 to **373 of 624**; what it withdrew is as binding as what
it landed (§2 2c correction 2: a structured statement list is not a CFG).
"MUST" is a gate. §5 records the prototypes and the shredder; §6 is the
decision log.

## 0. The evidence base: one tune per driver family

Seven tunes across six families, all building and Gate FP clean at HEAD:

| family | tune | why it represents |
|---|---|---|
| Hubbard (hand-coded, 1985) | `MUSICIANS/H/Hubbard_Rob/Commando` | §7.10.12/13's measured tune; the showcase baseline |
| Galway (hand-coded, per-voice code) | `MUSICIANS/G/Galway_Martin/Comic_Bakery` | §7.10.2/7.10.9's problem tune; register-window sweeps |
| goto80 (scene, own/defMON-line player) | `MUSICIANS/G/Goto80/Automatas` | modern scene idioms; RAM pointer cells |
| GoatTracker (tracker export) | `MUSICIANS/C/Cadaver/Aces_High` | the suite's canonical GT tune (`tests/test_streams.py`) |
| SID-Wizard (tracker export) | `MUSICIANS/C/Chabee/Angry_Birds` | player-signature-verified SW export |
| Follin (script interpreter, SMC dispatch) | `MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts` | docs/follin-dispatch-study.md's subject; the dispatch worst case |
| Follin (confirmation) | `MUSICIANS/F/Follin_Tim/Agent_X_II_The_Mad_Profs_Back` | same idiom, different sites |

Baselines measured at `6d19741`, 1500 frames, with the Phase 0 instruments.
One calibration correction stands: `frameval.run_frame` re-reads a just-stored
cell to buffer a SID write (`frameval.py:535`), so state-image read counters
see one echo per write; Phase 0's committed instrument counts net of echo
(Commando echo-free: 5,440 reads over 8 registers from 3 statements —
§7.10.12's conclusions unchanged).

| | Commando | Comic_Bakery | Automatas | Aces_High | Angry_Birds | Ghouls | Agent_X_II |
|---|---:|---:|---:|---:|---:|---:|---:|
| emitted lines | 432 | 1066 | 860 | 528 | 1142 | 955 | 773 |
| census sites, total | 21 | 111 | 72 | 23 | 124 | 161 | 105 |
| — `carry_val` | 1 | 50 | 20 | 6 | 38 | 20 | 18 |
| — `unnamed_addr` | 5 | 33 | 29 | 6 | 46 | 81 | 47 |
| — `word_pack` | 5 | 9 | 9 | 8 | 2 | 16 | 8 |
| — `raw_sp` | 0 | 0 | 3 | 0 | 16 | 0 | 0 |
| — `flag_bit` / `borrow` | 4 / 2 | 2 / 0 | 8 / 1 | 1 / 2 | 10 / 5 | 8 / 4 | 3 / 4 |
| lane residue (triage) | 0 | 5 `swept` | 0 | 0 | 1 `unproven` | 3 `unproven` | 2 `unproven` |
| `state { }` fields declared | 26 | 133 | 86 | 19 | 24 | 114 | 89 |
| — scratch (frame-local/RO/WO) | **22** | **52** | **15** | **11** | **9** | **41** | **30** |
| — genuinely persistent | 4 | 71 | 69 | 4 | 12 | 68 | 58 |
| frame-local cells, all regions | 20 | 33 | 11 | 5 | 11 | 4 | 7 |
| ⊤ **store** addresses | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| ⊤ **load** sites | 0 | 23 | 26 | 6 | 39 (+13 stack) | 52 | 42 |
| — their pointer roots | — | `zp_F0/F2/F4/F6` | `zp_FB`, RAM cells `m_11xx` | `zp_FB` | `zp_FE` | `zp_21/23/25` | `zp_02..07` |
| SID read-back sites (static) | 3 | 1 | 1 | 2 | **0** | **0** | **0** |
| `hi-first` stores | 3 | 0 | 0 | 0 | 2 | 0 | 0 |
| `switch goto` dispatch sites | 0 | 4 | 0 | 2 | 1 | 3 | 3 |

The ⊤-load row abbreviates roots per cell family; the emitted programs carry
**23** roots over the seven (2a's instrument is the authority) and the 188 ⊤
loads are exact.

**The corpus-wide store-reach sweep** (624 tunes): **570 of 624 (91.3%) have
no store whose reach bound exceeds the stack.** The 105 wide stores over the
remaining 54 tunes are **two** shapes, not the three this table first named
(§2 Phase 2.5 correction 2):

| shape | stores | tunes | disposition |
|---|---:|---:|---|
| `(zext2(reg) + $00NN):2`, true bound ≤ `$01FF` | **65** | 44 | 2.5's interval bounds **all 65**, inline (32) or bound to a `t0:2` temp (33) |
| a store *through* a sequence pointer | 38 | 12 | write-through players; Phase 2's certification |
| neither shape | 2 | 1 | bounded by 2.5 as well |

The two worst carriers (`C64_World`, `1st_Decent_Hardcore`, 14 apiece) are
§7.10.3's worst `unnamed` carriers: one defect wearing two counters.

## 1. Ambiguities resolved up front

Measured answers, not judgment calls. The journey to each is in git history;
only the answers bind.

**R1 — Can a store clobber a promoted cell through an alias?** No for 91.3%
of tunes; the rest are the two shapes above, and 2.5 bounded 67 of those 105
stores inside the stack, leaving the 38 write-throughs Phase 2 already owns as
the actionable residue. R1 is not an assumption: the promotion pass recomputes
the sweep per build and refuses `wide_store` per cell, ledgered per tune.

**R2 — What are the ⊤ accesses?** Sequence-pointer traffic, uniformly, in
every family including Follin: `(ptr + zext2(y+k))` walks through pointer
roots already in `streams.classify`'s vocabulary. 12 tunes also *store*
through such pointers, so the pointer question is the whole ⊤ question, load
and store — what makes Phase 2 the keystone.

**R3 — Is the `state { }` scratch finding family-specific?** No. Scratch
fraction: Hubbard 85%, GT 58%, Galway 39%, SW 38%, Follin 36%/34%, goto80
17%. Universal but 3×-variable; the dynamic verdicts are an **upper** bound
on static promotion (§7.10.13's path-dependence argument).

**R4 — Do the byte-pair idioms reduce to few shapes?** Yes: `word_pack` is
two skeletons; `carry_val` three roles (ADC-chain hi column, page-cross
guard, stored `cflag`). All collapse under admitted QF_BV rules once operands
are wide; the page-cross role vanishes under R9 (a cursor advance has no page
to cross).

**R5 — Is the write-only read-back universal?** Three families prove the
target shape at 0 sites; Phase 0 sized the class: **1,123 `sid_readback`
sites over 466 tunes** — Phase 5 is a corpus-majority fix, not a cleanup.

**R6 — Does the gate hold on the evidence set?** Yes, 7/7 clean at HEAD; any
movement is attributable to the phase that moved it.

**R7 — What metric arbitrates?** The census. Headline: **tunes wearing zero
machine shapes** (word-store rate retired, §7.10.10). Every phase MUST move
its named classes down and MUST NOT move the summed census up per tune — the
per-tune **sum** is the law: a phase that turns memory into values moves
residue between classes (a promoted word's halves land in the byte-lane
classes; Phase 4 is where they leave). The cap is 624 less the tunes wearing
refused `sp` fabric: **326 of 624** after Phase 1 (298 tunes), **373 of 624**
after 2c (251). The remaining 251 are ledgered per class at §2 2c; the
headline itself stays 0 while `unnamed_addr` and the byte-lane classes stand.

**R8 — Does the Follin dispatch break the plan?** No. The emitted dialect is
already structured: the SMC dispatchers arrive as `switch goto` over the
observed handler set with `unobserved` at the closure boundary (zero
`dgoto`/`igoto`/`pcall`/`callb` in Ghouls' text). Two consequences:

1. **Phase 3's liveness MUST be a forward written-before-read analysis over
   the emitted dialect, not a reuse of the backward `Defs` walker** —
   §7.10.5's whole-procedure kill switch is a property of the backward walk.
   A forward analysis treats the dispatch as an n-way join and `unobserved`
   as a terminator. The `wall` refusal covers only raw dyn forms: corpus
   **42 statements / 31 tunes**; `switch goto` 195 / 141 is an ordinary join
   (Galway and GT emit them too). Phase 3 re-measures both at entry — the
   counts move with tree changes and are not quotable across phases. The
   `dispatch_scratch` fixture pins both halves: the SMC-operand dispatch
   emits `switch goto` (control), and scratch promotes across the join
   (xfail Phase 3).

   **Amended by 2c (correction 2): the edge set this analysis joins over does
   not exist yet, and building it is a precondition, not a detail.** 2c built
   the label/`goto`/loop worklist this bullet assumes, passed every fixture,
   and **diverged eight tunes**; it was withdrawn and `sp_loop_edge` pins the
   withdrawal. A structured statement list is not a CFG — a label may be
   entered by a `goto` its list does not carry, a jump no list enumerates, or
   a dispatch arm, so a forward join over the list alone concludes a property
   holds on every path to a point that an unenumerated edge reaches without
   it. Written-before-read is a **must**-analysis that licenses promotion, so
   it carries strictly more risk than 2c's displacement claim, which is what
   broke. Phase 3 MUST therefore either consume an explicit in-edge map (for
   every label and dispatch arm, the sites that may reach it, across lists and
   procedures, with the `unobserved` boundary) or take the conservative value
   at every such point, which is what `_SpFlow.leaves()` does today and why it
   is correct. Whether that map closes corpus-wide is unmeasured and is the
   number Phase 2.5 owes this phase.

   **Answered by 2.5: the map closes on 6,350 of 7,278 labels and in 584 of 624
   tunes, and the edge list above is short by one kind.** A raw `call` may target
   a label of the calling list itself (ASL/04's three per-voice passes are
   `JSR $1040`/`JSR $103F` into `$103F`/`$1040`); the corpus carries 3,185 such
   edges, and a map built from `goto` alone contradicted the run on 81 tunes.
   Phase 3 MUST take the raw call as an in-edge. The 928 unclosed labels are all
   in 39 tunes, each carrying a raw dyn transfer or an RTS-trick landing on a
   label, so closing them is this bullet's `wall`, not a map defect. `switch goto` is not
   a wall — 121 of the 142 tunes carrying one close entirely — though
   `frameproc._COMPUTED` lists `swg`, so today's `Defs._verified` refuses every
   label join in all 142 (§2 Phase 2.5 corrections 1 and 7).
2. **Follin's residue is ordinary.** Zero ⊤ stores, zero `raw_sp`, zero
   read-backs; its ⊤ loads are the three script pointers. What is genuinely
   Follin-shaped is the *scale* of the pointer machinery (3-deep per-voice
   cursor call stack, loop save cells) — and the `follin_jump` /
   `follin_ret_stack` control fixtures prove that machinery is fused and
   lift-eligible in isolation. The old "Follin refuses everything" caveat is
   withdrawn as measured-false; its scratch yield is smaller (4–7 cells)
   because the interpreter's state genuinely persists.

**R9 — Are pointer variables needed, or can the lift go straight to
tables?** The measured facts: every ⊤ access is a walk `*(ptr + k)` whose
reaching definitions are (a) a row from a declared lo/hi table, (b) an
in-block advance, or (c) a save/restore of another such value. That is a
**cursor into a table's block**, and the annotation dialect says so
(`ptr_0021: u16 in m_7338`): the ⊤ class is deleted, not guarded — a read
outside the declared extent **faults**, the `unobserved`/`rmap` discipline.
The license for the extent claim is the evaluator fault, never the static
certification (resolved 2026-08-08; §6). The u16 pointer spelling stays the
fallback for a root that fails block-rooting.

## 2. The phases

Ordering by dependency: 1 cleared the stack's spills, 2a certified the
pointer traffic (the *bounds* 2c and 3 depend on), 2b ships the annotation
spelling, 2c finishes the stack, 3a made the artifact total and cached the
corpus on it, 2.5 walked values and re-priced what is left, 3 promotes
frame-local scratch, 4 coalesces byte columns, 5 retires the boundary
read-back, 6 re-measures. 2.5 was inserted after 3a on the finding that one
missing analysis blocks four phases (§2 Phase 2.5, §6). Every phase
ends with the same full-corpus sweeps (`gate_sweep`, `lift_residue`,
`fuse_measure`, `storage_census`) and its before/after table appended here.

### Phase 0 — instruments, baselines, and the metric — DONE

**Landed**: `tools/storage_census.py` (the §7.10.13 harness productionized,
echo-netted, `--frames full` mode; 15 hermetic tests), wide-store
classification inside `fuse_measure`'s walk, `sid_readback` + dyn-control
counts in `lift_residue`. Verified: every pre-existing total unchanged; the
extended `lift_residue` bit-identical to pristine HEAD on every signature,
all 624 rows. `tools/disasm_tune.py` (added during the claims-discipline
review) reads any cached tune for the disassembly this plan's claims must
cite.

**Baselines** (1500 frames; `out/` is local, the numbers are the record):
census-zero headline **0 of 624**; `state { }` **17,664 fields, 9,360 (53%)
scratch**; frame-local cells 9,436 (7% crossproc); ⊤ loads 6,715; ⊤ stores
105 / 54 tunes {g2_boundable 32, ptr_writethrough 38, loc_unresolved 33,
other 2}; `sid_readback` 1,123 / 466; dyn-control 42 / 31, `switch goto`
195 / 141. `1st_Decent_Hardcore` faults under evaluation at 1500 frames
(invisible at 300) and joins `C64_World` on the excluded list.

Gate: instruments reproduce the seven-tune table bit-for-bit; no emitted
text changes (hash-checked). Held.

### Phase 1 — the stack becomes locals (`raw_sp` -> 0-or-refused) — DONE

**Landed**: rung (d0s) in `framestack` — the spill named through `sp` — plus
two pre-existing soundness fixes it turned up, and a per-procedure refusal
ledger in `lift_residue`. The corpus classification that chose the rule: the
surviving `sp` residue is three shapes — the pull address
`(zext2(sp [+ k]) | $0100)` 1,278 / 263 tunes, the update `sp = sp ± k`
841 / 218, the `pcall` threading 533 / 172 — and only the first is a spill.

*The rule*: `_Marks` gives every statement an `(epoch, displacement,
aliases)`; `_SpSlot` re-asks rung (d0)'s premises against the relative slot,
plus two of its own: the store claims free stack space (`k <= 0`), and the
procedure balances (`_sp_state == ("entry", 0)`; the balance premise cost
eleven divergences to learn — `frameval`'s `ret` reads page one, so a
promoted slot in an unbalanced procedure deletes the byte the machine
returns through; `sp_unbalanced` fixture). Soundness fixes: `720_Degrees`
(rung (d0) counter/rewriter walked different trees; Gate FP 621 -> 622) and
`_factor_ifs` renaming two locals into one (premise: a renamed arm local may
not take a name that arm binds).

| | before (`c109a13`) | after |
|---|---:|---:|
| **Gate FP, 300 frames** | 621 clean / 623 built | **622 clean / 623 built** |
| — diverged | `720_Degrees` (B), `Rambo` (C) | **`Rambo` (C) only** |
| census `raw_sp` | 2,653 / 328 tunes | **2,379 / 298 tunes** |
| census `unnamed_addr` | 9,373 / 611 | **9,129 / 611** |
| census `sid_readback` / `narrow_sink` | 1,123 / 249 | 1,049 / 233 |
| census `hi_byte` / `lo_byte` | 2,254 / 2,172 | 2,328 / 2,248 |
| census `word_pack` / `flag_bit` / `carry_val` | 4,617 / 1,664 / 5,608 | 4,651 / 1,673 / 5,610 |
| **census sum** | **31,525** | **31,112 (-413)** |
| — tunes whose sum fell / rose | — | **92 / 33** (worst riser +4) |
| `storage_census` stack loads / stores | 974 / 889 | **753 / 661** |
| `fuse_measure` `unproven` / `provably_complete` | 217 / 487 | **201 / 488** |

Byte-lane classes rose (+74/+76/+34/+9/+2) against `raw_sp` -274,
`unnamed_addr` -244, `sid_readback` -74, `narrow_sink` -16: a destacked
spill pair becomes one u16 local whose halves are then read as truncs — two
Phase 4 sites replacing two machine-shape ones. Under §3's law (the sum is
the metric) that is a pass; "no other signature rises" is recorded as too
strong for any phase that turns memory into values.

**The refusal ledger** (`lift_residue --sig raw_sp`): every program still
carrying `sp` carries a named per-procedure refusal — "0-or-refused" holds
literally. `spslot:`/`stack:` rows are per-slot rung refusals; bare `sp_*`
rows are `drop_sp`'s per-program verdicts. 252 tunes carry conservative
ledger rows with final `raw_sp` 0 (kept: §3's shrinking-ledger rule needs
them in the baseline):

| class | refusals | tunes |
|---|---:|---:|
| `spslot`: stack effect not zero | 372 | 128 |
| `sp_linked` (a raw call keeps the machine stack alive) | 366 | 307 |
| `sp_unbalanced` | 288 | 201 |
| `stack`: read not dominated by a store of the slot | 102 | 52 |
| `spslot`: unresolvable address may alias the slot | 101 | 67 |
| `stack`: slot not both stored and read | 78 | 43 |
| `stack`: unresolvable address may alias the slot | 71 | 57 |
| `sp_read` / `sp_returned` / `sp_callee` | 58 / 47 / 2 | 58 / 47 / 2 |
| remaining `spslot`/`stack` rows | 34 | — |

**Where the plan and the corpus disagree, the corpus wins: `raw_sp -> 0` is
not reachable by destacking.** Two thirds of the class is fabric only
`drop_sp` removes, and `drop_sp` is all-or-nothing per program, blocked by
`sp_linked` (307 tunes) and `sp_unbalanced` (201). Relaxing `sp_linked`
outright clears only 71 tunes / 217 sites, so the real ceiling is the
balance analysis. Both rules moved into **Phase 2c**, which landed neither as
written: the linkage went on displacement and the balance on the call graph
(§2 2c). Two consequences owned by their phases: R7's cap (326/624, released
to 373 by 2c), and the `store_reach` interval-from-zero defect that would
have forfeited a third of Phase 3's yield (the floor is `frameproc
.addr_floor`, landed in 2a; `sp_scratch_floor` fixture pins the promise).

Shredder: `sp_spill` flipped to a hard pass at landing (marker removed in
the same change); the invariant fixture this phase wrote as `sp_unbalanced`
is stack-balanced and refused on its loop edge, so 2c renamed it
`sp_loop_edge` and gave `sp_unbalanced` a genuine member (§2 2c correction 2).

### Phase 2 — sequence traffic becomes table cursors

The keystone, split so analysis lands before dialect: 2a certification
(DONE), 2b annotation + rewrite (DONE, b0–b5), 2c the stack endgame (DONE).

#### 2a — certification (analysis only, no text change) — DONE

**Landed**: `deity_informant/ptrcert.py` — the authority on block-rooting —
plus `frameproc.addr_floor` (must-set bits, mirroring `addr_bits`' may-set:
`(zext2(sp) | $0100)` reaches `[$0100, $01FF]`, not `[$0000, $01FF]`),
certification folded into `storage_census` (`fuse_measure.root_cells`
delegates to `ptrcert`, `cert_agree` 0 disagreements / 624), 24 hermetic
tests. The certification is stated over the **emitted program** (where the ⊤
residue lives): a root is a cell the text derefs through an address naming no
datum. Three premises per root, each re-asked in sufficient form (§7.10.11
`_counter_range` discipline):

- **definitions closed** — every reaching store is a table-row reload (rung
  (f)'s premise re-asked per leg against the declared registry), an in-block
  advance, or a save/restore closed transitively over held locations. Else
  `ptr_uncertified`.
- **reads closed** — no read of the pair's bytes outside its own web (a
  counter role or page-alignment test reads what a cursor does not
  maintain). Else `role_entangled`; `role_opaque` where only an unreadable
  local may carry the pair.
- **extent declared** — every reloadable row and the post-init value inside
  a declared datum, under a bounded advance. Else `ptr_extent_open`.

**Coverage, corpus-wide** (1500 frames, 624 tunes):

| | roots | ⊤ loads | ⊤ stores | tunes |
|---|---:|---:|---:|---:|
| pointer roots the emitted text derefs | **1,044** | 6,653 | 38 | 511 |
| — block-rooted (definitions closed) | **441 (42.2%)** | 1,939 | 8 | 145 all-rooted |
| — block-rooted *and* reads closed | 307 | 1,142 | 8 | 91 |
| — cursor-ready (every premise) | **107 (10.2%)** | 157 | 1 | 9 |
| ⊤ accesses carrying no root | — | 59 | 67 | 50 |

**Definition kinds** (4,372 defs): `reload` 1,776, `save_restore` 1,034,
`advance` 504 — the three cursor shapes cover **76%** — and `other` 1,058 is
five shapes: `computed` 480 (zero-page reused as scratch), `held_open` 261,
`block_read` 133 (the interpreter's own jump/call operand — Follin, Galway),
`low_held` 96 (cursor held through page one), `opaque` 88 (param/return; the
chase is intraprocedural).

**Refusal ledger**: `ptr_uncertified` 603 / 366 tunes (316 on `other` defs
alone, 193 on an alias store alone, 94 both), `role_entangled` 418 / 302,
`ptr_extent_open` 311 / 151, `role_opaque` 7 / 7. **Premise cost** (roots
blocked by that premise alone): post-init declared **107**, defs closed 50,
reads closed 23, rows declared 17, advance bounded 2. The post-init premise
is a conservatism Phase 3's boundary analysis would discharge (cursor-ready
107 -> 214) — moot under 2b, which dissolves it at lift.

**The seven-tune table** (23 roots, 12 block-rooted, 0 cursor-ready):

| | Commando | Comic_Bakery | Automatas | Aces_High | Angry_Birds | Ghouls | Agent_X_II |
|---|---:|---:|---:|---:|---:|---:|---:|
| pointer roots | 0 | 4 | 11 | 1 | 1 | 3 | 3 |
| block-rooted | 0 | 0 | **10** | 0 | 0 | **2** | 0 |
| defs: reload/advance/save/other | — | 0/6/9/15 | 6/3/8/4 | 2/0/1/4 | 4/0/8/2 | 0/12/5/2 | 0/6/3/6 |
| refusals | — | unc 4, ent 4 | unc 1, ext 10, ent 3 | unc 1, ent 1 | unc 1, ent 1 | unc 1, ent 3 | unc 3, ent 3 |

Corpus-forced corrections, all folded above: §0's "15 roots" was a row
abbreviation (23 is the count); only goto80 certifies structurally (GT
spills a register through `zp_FB`, SW holds `zp_FE` through page one —
`low_held_cursor` fixture, from the Angry_Birds `$09F1` disassembly — and
Galway multiplexes); Follin carries a third `other` shape, `block_read` —
the transitive save closure *does* hold (it is why Ghouls' `zp_21`/`zp_25`
are block-rooted; `follin_ret_stack` fixture, from the `$6ADD`/`$6B42`
disassembly).

**Gates** (all held; the phase is read-only so the first is the point):
emitted text byte-identical corpus-wide (624/624 sha256); full suite green
with the Phase 2 fixtures xfail; gate 622/623; census sum 31,112 with every
signature unchanged; `fuse_measure`/`storage_census` identical row for row.

**What it supplies**: the reach vocabulary 2c's page-one verdict is built from
(`addr_floor` is half of Phase 1's 611-tune unprovability; the *certification*
itself could not serve as 2c's premise — §2 2c correction 4);
Phase 3's `aliased` reach vocabulary and its floor; and 2b's whole work
list. **Not done**: the certification is intraprocedural (88 `opaque`
defs); the reads closure falls back to position-blind taint (7 roots);
`block_read` is named and counted but not certified — b3's rule, landed below
(128 of the 133 defs are now a kind of their own).

#### 2b — the unified spelling under observed extents — DONE (b0–b5)

The four-construct cursor dialect and the certified-roots-only rewrite are
superseded (§6, 2026-08-08): the extent claim's license is the **evaluator
fault** (the control layer's observed-primary discipline, corpus-wide
precedent), so the spelling ships corpus-wide, guard-live where uncertified.
One constructor set covers every family — every measured vocabulary closes
single-digit (3 `sp` shapes, 3 wide-store shapes, 5 `other`-def shapes, 2
pack skeletons, 3 carry roles, 1 raw dyn form on the seven) — the families
compose the same constructors at different depths.

**The dialect**: one grammar production. The deref spells `*ptr_0021[y]`
(rung (f)'s existing form — one surface syntax, one meaning) and the
declaration gains an extent clause `ptr_0021: u16 in m_7338, m_7401`
(`sidprog.lark:57,59`, `FrameProgram.extents`). Everything else already
spells: advance `ptr:2 += k`, reload `ptr = T[i]`, byte reads as truncs, a
`block_read` as `ptr = ptr[k]:2`, save/restore as u16 moves. `frameval`
checks every access through an annotated variable against its extent and
faults outside it (fault kind `extent`; at-rest junk in a multiplexed cell
is harmless because the check is at the use, §5.2 M2).

**The pipeline** (each step a committed instrument or rung with inputs,
rule, refusals, gate):

- **(b0) Observed extents are a committed artifact** *(LANDED:
  `deity_informant/ptrextent.py`, `out/ptr_extents*.json`)*. The census run
  resolves every deref concretely; a compile-time probe charges each address
  to the web whose text spelled it (0.9% wall). Addresses attributed through
  the registry (`data_decls`, `via:` discovery); an address in no declared
  datum ledgers `extent_unmappable` and the web keeps today's spelling.
  MUST: every gate run's horizon ≤ the artifact's recorded horizon
  (`gate_sweep --extents` stops the line; a fault past the horizon is the
  claim boundary working).
- **(b1) Lift eligibility is a `ptrcert` column, separate from
  certification** *(LANDED)*. A web lifts iff (i) **web closed** — no
  unresolvable store may reach the pair (`store_reach` + `addr_floor`);
  refusal `web_alias`, the only premise doing soundness work (`alias_web`
  fixture: the wrapping `STA $zz,X` shape, ASL/04 `$128B`); (ii) **defs
  expressible** — reload, advance, save/restore over admitted holds,
  `block_read`, or a computed def whose lo/hi legs fuse at one seat; else
  `def_unliftable`; (iii) **uses expressible** — deref, store-through, byte
  extract, word compare; else `web_opaque`; (iv) **holds off the machine
  stack** — else `low_held`, which 2c measured as unmoved at 96 defs and
  handed to Phase 6 (`low_held_cursor` fixture, §2 2c correction 5). Everything 2a called a *certification* refusal stops blocking:
  entangled reads spell as extracts, unbounded advance is what the guard is
  for, and the post-init premise dissolves at lift (a value never deref'd
  faults nothing).
- **(b2) One rewrite rung** (rung (g), `deity_informant/ptrlift.py`, after
  rung (f)) *(LANDED)*: per web, b1's eligibility ∧ b0's mapped extent —
  rename the pair to one u16, rewrite defs and uses, attach the extent. The
  rung is **naming-only** (trees and values untouched, so Gate FP cannot
  move), and the extent is read from b0's artifact at build time — an
  emitted program is now a function of a recorded trace as well as of the
  binary; a build without the artifact emits today's spelling byte for
  byte.
- **(b3) Certification stays 2a's machinery, as the accounting tally**
  *(LANDED)*. `ptrcert` gained the static extent enumeration: reload targets
  from the declared row-index bound (2a's `_reload` already bounded them, so
  b3 adds nothing there); `block_read` targets as a least fixpoint
  over the finite registry (E₀ = reload rows + post-init block **+ the declared
  blocks the web reads its value out of**, see correction 2; each round adds
  every declared datum containing any 16-bit LE value readable in blocks
  already in E; monotone over a finite registry, so it terminates). A block
  some store reaches refuses `extent_mutable` (certification only — the lift
  and guard stand). A root is **extent-certified** when the fixpoint equals the
  observed extent, or run-to-recurrence closure covers the infinite run
  (`storage_census --close`). A `block_read` def arrives fused (rung (d)'s pack,
  which the shape recognizer reads as `computed`) or as the two byte lanes the
  machine wrote, so the enumeration keys on both; a lane whose partner is not
  the byte beside it names half a pointer and the extent stays open.

  | | before (`657dee9`) | after |
  |---|---:|---:|
  | pointer roots | 1,044 | 1,044 |
  | — block-rooted (definitions closed) | 441 (42.2%) | **480 (46.0%)**, 2,430 ⊤ loads |
  | — cursor-ready | 107 | **110** |
  | — tunes all-block-rooted | 145 | **157** |
  | def kinds `reload`/`advance`/`save_restore` | 1,776 / 504 / 1,034 | 1,776 / 504 / **1,076** |
  | — `block_read` (new) / `other` | — / 1,058 | **128** / **1,022** |
  | premises `defs_closed` / `rows_declared` | 441 / 642 | **480** / **595** |
  | roots reading their value out of the registry | — | **370** |
  | roots with a non-empty static extent / blocks named | — | **858 / 5,057** |
  | **extent claims** (refusals empty) | — | **103** (100 deref'd in the run) |
  | — fixpoint == observed (**extent-certified**) | — | **99** |
  | — observed block the fixpoint does not name | — | **1** |
  | tunes closing under `--close` (1,500 frames) | — | **9 of 624**, 0 extra certificates |
  | b1 `eligible` / b5 work list | 524 / 308 webs | **523** / **308 webs** (correction 1) |

  **The refusal ledger** (per root; a root may wear several classes):

  | class | roots | tunes | movement |
  |---|---:|---:|---|
  | `ptr_uncertified` | 564 | 354 | **-39 / -12** (the block reads are named) |
  | `role_entangled` | 418 | 302 | unchanged |
  | `ptr_extent_open` | 347 | 170 | **+36 / +19** (63 unpaired-lane roots) |
  | `extent_mutable` (new) | **182** | **132** | the enumeration reads out of a written block |
  | `role_opaque` | 7 | 7 | unchanged |

  Gates, all held and all full-corpus: emitted text byte-identical, 624/624
  sha256, **both with and without b0's artifact**; `gate_sweep --frames 300
  --extents out/ptr_extents_full.json` **622 clean / 623 built**, `Rambo` (C)
  the only divergence, zero `extent` faults, no run outran its horizon;
  `lift_residue` and `fuse_measure` identical field for field on all 624 rows
  (only `build_s` moves); census sum **31,112**, every signature unchanged; the
  seven review tunes Gate FP clean at full Songlengths length, 7/7, and their
  emitted-text diff is empty by the sha256 above.

  **The corpus-forced corrections:**

  1. **b3 moves b1's column by one web, downward, and the plan's "the lift and
     guard stand" survives only because the work list does not move.**
     Admitting a play-written pointer table into the web runs 2a's closure over
     its writers, and `Amazing_Spider-Man`'s `m_6367` reloads from
     `m_659F`/`m_65A2`, which are written from a register: two `held_open`
     defs, `def_unliftable`, `eligible` 524 -> 523. The web was never in the
     work list (its observed extent is unmappable), so rung (g) rewrites the
     same 308 webs and every emitted byte stands. The movement is a rule
     change and it is monotone toward refusal — b3 found a web b1 admitted
     only because 2a could not see the table behind its reload.
  2. **E₀ as specified was incomplete, and the differential guard proved it
     before any prose did.** With E₀ = reload + post-init only, six claiming
     roots had the run reach declared blocks the enumeration never named — all
     six restored from a held table (`Adidas_Football` `zp_20` from
     `m_70A9[]`, `Anastasia`, `Airwolf`, `Counterforce` `m_1E57` from
     `m_1EC1[]` (`$1E45: LDA $1EC1,Y / STA $1E57`), `Contact_Us`, `Cybernoid`).
     2a's closure proves a hold's *writers* are cursor values and never asked
     where its *rows* point, so the extent claim was open. Feeding a hold that
     lands in a declared datum into the read set — the same fixpoint edge the
     block reads use — closes five of the six.
  3. **The one survivor is a different quantity, not a defect.** `Counterforce`
     `m_1E57` is the operand pair of the instruction at `$1E56`
     (`99 D1 1E` = `STA $1ED1,Y`), and `$1E51: LDY #$0E` walks the row index
     fourteen bytes past the block the operand's *value* names; the registry
     carved that span into seven declarations. The enumeration states where a
     root's value may point, b0's record states where its derefs landed —
     value **+** row index. Ledgered as a precision item: the row-index span
     is `frameptr`'s, not b3's.
  4. **The fused `block_read` the plan told b3 to key on is 2 definitions in
     624 tunes; the byte-lane form is 126.** 2a's `block_read` *shape* (133)
     is almost entirely lanes rung (d) never fused, and the fused ones hide
     under `computed` exactly as the fixture record said. Keying only on the
     fused form — the literal reading of this bullet before it landed — would
     have reached two definitions. Both keyings are now in the rule and both
     have fixtures (`follin_jump`, `lone_lane_block_read`).
  5. **`--close` is real and nearly empty.** Nine of 624 tunes reach a
     recurring state image at 1,500 frames with no declared input; six carry a
     pointer root and one carries a claiming root, whose extent the fixpoint
     had already certified. `--close` therefore adds **zero** certificates at
     this horizon: it is a licence with no customers yet, kept because it is
     the only licence that does not depend on the enumeration being exact.
  6. **`gate_sweep --extents` must be given the `--frames full` artifact.**
     `gate_sweep` decompiles at full Songlengths length while
     `storage_census --frames 1500` decompiles at 1,500, so the 1,500-frame
     artifact describes a different model: five tunes take `extent` faults that
     have nothing to do with any rule. Measured identically at `657dee9`, so it
     is a usage trap, not a regression — but it is how a clean gate can be made
     to look like a broken one.

  **Not done**: the enumeration does not derive an extent from arithmetic —
  `computed_rows` stays xfail and its reason is now **Phase 6 alone**; the
  static extent is deliberately kept out of `aliases`' span (widening the
  alias bound would move `web_alias`, and with it the text) — a later phase
  owns that; 63 roots over 32 tunes refuse `ptr_extent_open` on an unpaired
  byte lane, which is rung (d)'s read-side rule again.
- **(b4) Evaluator cost is measured, not feared** *(LANDED)*: structurally
  zero unannotated (no guard object); ~1.06× where the guarded deref
  dominates — inside the 2× budget (docs/cycle-times.md).
- **(b5) The work list precedes the rewrite** *(LANDED)*: printed per tune
  before rung (g) ran. At 1,500 frames: 308 webs / 1,782 ⊤ loads / 158
  tunes; at full length 325 / 2,061 / 161. `web_alias` is **287 at both
  horizons** — the soundness premise is horizon-independent, the check that
  the widening is observation and not drift.

**The refusal ledger** (1,500 frames; a web may wear several classes; per
definition over 2a's 4,372: 3,876 admit (88.7%), 390 `def_unliftable`, 106
`low_held`):

| class | webs | tunes |
|---|---:|---:|
| `extent_unmappable` (b0) | **399** | 188 |
| `web_alias` (b1 i) | **287** | 132 |
| `def_unliftable` (b1 ii) | 171 | 153 |
| `web_opaque` (b1 iii) | 152 | 135 |
| `low_held` (b1 iv) | 65 | 54 |

**The rewrite record** (`--frames full`, 624 tunes, 0 build errors):

| | before | after |
|---|---:|---:|
| pointer webs the emitted text derefs | 1,079 | **828** |
| ⊤ loads, all sites | 7,296 | **6,296** |
| b5 work list (webs / loads / tunes) | **325 / 2,061 / 161** | **74 / 1,061 / 52** |
| `state { }` declared / scratch / persistent | 18,101 / 9,152 / 8,325 | **identical** |
| `sid_readback`, dyn, `switch goto`, ⊤ stores, stack rows | — | **identical** |
| evaluation faults | the two standing | **the two standing** |

**251 webs lifted** (77.2%), 729 deref sites renamed, 251 `in` clauses,
1,000 ⊤ loads retired. The 74-web residue *is* the refusal population field
for field. Ghouls and Agent_X_II each lift 0 of their 3 work-list webs (all
six pairs unfused), so the review seven are silent on this rewrite and its
evidence is the corpus. Gates: text byte-identical with no artifact; canonical fixpoint
624/624; suite 2,451 passed / 18 xfailed; `gate_sweep --frames 300
--extents` 622/623 at the pre-change baseline; **zero `extent` faults**;
Gate FP on the six lifted review tunes at full length 6/6 clean; b4 above.

**The corpus-forced corrections that still bind:**

1. **The 74-web residue is one class, `web_unnamed` — rung (d)'s read-side
   refusal — and `def_unliftable`'s computed-lone-lane population is the
   same condition counted twice** (all 135 such webs are `web_unnamed` at
   both horizons; admitting all 272 defs yields net zero rung-(g) targets).
   Any yield read off a refusal-ledger sum is suspect wherever classes
   share a premise. The idioms behind the 74 are disassembled and pinned as
   the `dual_store_*` / `stack_spill_cursor` / `deferred_carry_cursor` /
   `table_spill_cursor` / `unpaired_half_store` / `inpage_advance` fixture
   family (§5.4): the discriminator is whether any byte-lane *read* of the
   pair survives rung (d2) — not the second destination, not store order,
   not interleaving. A fix keyed on the merge premises reaches 66 of 74.
   **The "8 genuinely byte-wise, refused for good" classification of the
   remainder is refuted by disassembly (2026-08-09): it was read off the
   emitted text, and the machine disagrees in every case.** All 8
   advance-shaped members carry their carry arm in code, merely
   `unobserved` in the text — Battle_of_Midway `$0B32`, Catacombs `$1504`,
   Chiller `$61ED` (`INC/BNE/INC`), Danger_Mouse `$2B64`, Arpeggio
   `$2BEA`, Data_Data `$2C82`/`$2ECE`, 4_Red_Calx_slo `$11C7`
   (`ADC/BCC/INC`) — so they are the **deferred-carry** class
   (`deferred_carry_cursor`), whose lift route is the guard discipline
   itself: the unobserved arm is exactly what the extent fault covers. The
   division accumulator (Cool_Air `$1447..$145D`) is a power-of-two divide
   — `(m_1784[y]:2 - m_1782[y]:2) >> n` as an `LSR A`/`ROR $FB` loop; the
   dialect has `>>`, the loop-to-expression rule is parked at Phase 6
   (`shift_divide`). Air_on_a_Rasterline (`$0C1A`/`$0D05`) reloads the two
   halves in *different frames* of an SMC phase machine — each store is a
   plain lane replacement, `(ptr & $FF00) | zext2(row)`, no carry involved
   (`phase_split_reload`). No byte-scratch web exists in the residue at
   all. **Confirmed genuinely-byte-wise corpus members: zero.**
   `inpage_advance` stays as the *semantic* invariant — an advance with no
   carry arm in the machine must never fuse to the plain wide add
   (`lo = (lo+k) mod 256` diverges from `+:2` at every lane wrap,
   `$14FF -> $1400` vs `$1500`) — but it now guards a rule, not a measured
   population; if such a web ever appears, its honest lift is a u8 offset
   under a constant page selector, no u16 involved.
2. **`extent_unmappable` is the largest blocker in the pipeline** (399 webs
   at 1,500 frames; it removes 216 of b1's 524 eligible webs). `via:`
   discovery declares the *anchor* blocks, not the walked extent; a web
   whose rows are computed gets one anchor and nothing else — Galway and
   goto80 exactly (`computed_rows` fixture: b1-eligible, rows land in no
   declared datum). Of the unmappable addresses, 50.2% are `short` (ran off
   a block the web walks), the rest `foreign`. **b3 measured this and cannot
   reach it**: its fixpoint walks declared data for 16-bit LE words, and an
   arithmetic row is in no block, so the class is Phase 6's outright.
3. **The write-through class reaches the work list at zero sites** (both
   b1-eligible carriers refuse `extent_unmappable`), so Phase 3 (ii) should
   not expect certified write extents from 2b's first rewrite.
4. **The census `unnamed_addr` gate is unmeasured, not met**:
   `lift_residue._expr_sig` classifies from `addr_split` alone and never
   consults `prog.resolved`, so rung (f)'s 633 pre-existing named sites
   still count `unnamed_addr`, and rung (g)'s 729 would too. Teaching it
   moves both populations in one change — an instrument-attributable
   movement to report apart from any rewrite's own.

**Not done** (2b as a whole, after b3): the four original Phase 2 shredder
fixtures stay xfail (`_lift` passes no artifact — deliberate; of the four only
`cursor_save` would pass with one); `ptrcert._advance` through `_lane` (2
defs) and `_reload` through a pack-and-mask (3 defs, Slaygon) are ledgered
precision items, joined by b3's row-index gap (correction 3) and its
deliberate isolation from the alias span.

#### 2c — the stack fabric leaves (`raw_sp` -> 0-or-refused, per procedure) — DONE

**Landed**: `framestack._SpFlow` — the per-procedure displacement walk, which
also records where the procedure's calls stand — `_balances`, the balance
verdict as a least fixpoint over the call graph, and the `sp_linked` relaxation
in `drop_sp` with `_page_one_free` beside it. `apply_rung` reads the same
verdict, so rung (d0s) now promotes spills the old per-procedure walk refused.

*The two rules that landed*:

- **Balance is interprocedural.** A `pcall` that hands `sp` back preserves the
  caller's displacement exactly where its callee balances, so the verdict is a
  least fixpoint over the call graph: nothing assumed, a cycle stays unproven,
  only callers re-asked. `sp_returned` retires with it — once balance is proven
  the returned `sp` is what the callee was given, and `_strip_sp` already
  dropped it — and so does its mirror inside `_sp_uses` (a `pcall` returning
  `sp` was `sp_read`).
- **The linkage rests on displacement, not on page one.** The machine keeps
  pushing return bytes into page one, and dropping the program's own
  displacement moves where they land; so the linkage drops where **every call
  in the program stands at the entry displacement** (the push cannot move), or
  where **no surviving access can name page one** (nothing reads what moved).
  The premise is asked of `pcall`s too: `frameval` pushes a return address for
  every call, the real one for a raw `call`, a synthetic one for a `pcall`.
  Refusal stays `sp_linked` otherwise. `_page_one_free` is the second disjunct,
  computed from `addr_range`/`span` (the ONE span rule) and `addr_bits` with
  2a's `addr_floor` as its lower bound.

| | before (`9c91720`, post-b3) | after |
|---|---:|---:|
| **Gate FP, 300 frames** | 622 clean / 623 built | **622 clean / 623 built** |
| — diverged | `Rambo` (C) | **`Rambo` (C) only** |
| Gate FP, seven review tunes, full length | 7 / 7 | **7 / 7** |
| census `raw_sp` | 2,379 / 298 tunes | **2,117 / 251 tunes** |
| census `unnamed_addr` | 9,129 / 611 | **9,101 / 611** |
| census `sid_readback` / `narrow_sink` | 1,049 / 233 | 1,049 / 233 |
| census `hi_byte` / `lo_byte` | 2,328 / 2,248 | 2,334 / 2,254 |
| census `word_pack` / `flag_bit` / `carry_val` | 4,651 / 1,673 / 5,610 | 4,657 / 1,687 / 5,610 |
| **census sum** | **31,112** | **30,854 (-258)** |
| — tunes whose sum fell / rose | — | **64 / 5** (worst riser +1) |
| `storage_census` stack loads / stores | 753 / 661 | **733 / 640** |
| `fuse_measure` `unproven` / `provably_complete` | 201 / 488 | 201 / 488 |
| — `unnamed_ruled_out` / `unnamed_as_written` | 1,639 / 924 | **1,610 / 898** |
| stack refusal ledger, every class | 1,519 | **1,097** |
| **R7's cap** (624 less the `raw_sp` tunes) | **326 of 624** | **373 of 624** |

The byte-lane classes rise again (+6/+6/+6/+14 against `raw_sp` -262 and
`unnamed_addr` -28), for Phase 1's reason: a destacked spill pair becomes one
local whose halves are then read as truncs. Under §3's law the sum is the
metric, and it falls. **Which premise carries the linkage** (per tune, this
tree): 202 tunes make no raw call at all; of the 422 that do, **344 drop the
linkage because every call stands at the entry displacement** and **78 keep
`sp_linked`**; the page-one verdict would have carried **7** of those 344 and
**0** of the 78.

**The refusal ledger** (`lift_residue --sig raw_sp`), after 2c:

| class | refusals | tunes |
|---|---:|---:|
| `spslot`: stack effect not zero | 254 | 84 |
| `sp_unbalanced` | 148 | 111 |
| `spslot`: unresolvable address may alias the slot | 106 | 71 |
| `stack`: read not dominated by a store of the slot | 102 | 52 |
| `sp_linked` | 87 | 75 |
| `sp_read` | 79 | 77 |
| `stack`: slot not both stored and read | 78 | 43 |
| `stack`: unresolvable address may alias the slot | 71 | 57 |
| `spslot`: slot not both stored and read | 58 | 53 |
| `sp_callee` | 51 | 49 |
| `spslot`: read not dominated by a store of the slot | 40 | 40 |
| `spslot`: another resolvable access may touch the slot | 14 | 5 |
| `stack`: another procedure may touch the slot | 8 | 6 |
| `stack`: another resolvable access may touch the slot | 1 | 1 |
| `sp_returned` | **0** | retired by rule |

The ledger shrinks 1,519 -> 1,097 **with** a rule change, as §3 requires; no
class grows except by re-classification (`sp_read` 58 -> 79 and `sp_callee`
2 -> 51 are the procedures whose old first-failure was `sp_returned` or
`sp_unbalanced`, `framefuse.refusal()`'s first-failure-only rule applying here
too).

**Where the plan and the corpus disagree, the corpus wins** — six corrections,
each measured at this tree:

1. **The bullet's balance rule names the smallest of the three blockers.** The
   288 `sp_unbalanced` refusals decompose, measured at entry: a `pcall` handing
   `sp` back **110**, an `unobserved` edge standing at a displacement **108**, a
   genuine `ret`/`igoto` off the entry **32**, the label/`goto`/loop edges the
   bullet names **37**, an opaque `sp` assignment **1**. The fixpoint that pays
   is over the call graph, which the bullet does not mention.
2. **The label/`goto`/loop-edge relaxation is unsound and was withdrawn.** Its
   first form — a worklist over label edges, with a path that leaves treated as
   dead — passed every fixture and **diverged eight tunes** at the corpus gate
   (Liberty, Amazon, Big_K_O, Absolutely_Fabulous, Alien_Team, Atmosphere,
   Atmosphere_II, Chameleon; localized one rule at a time). The reason is one
   fact about this dialect: a label or a dispatch arm may be entered by an edge
   the statement list does not carry — an `igoto`/`swg` pair, a `goto` from
   another list, a block reached only past an `unobserved` — so an interior
   displacement is not a state the walk may assume, and a dead tail hides the
   stack effects of everything it skips. Phase 1's rule (every such point
   stands at the entry) is restored; `sp_loop_edge` is the fixture that pins
   it, and the 37 refusals are a ledgered precision item, not a bug.
3. **`sp_fix_balance` passes, and not for the reason it was written.** Its
   emitted form carries no interior label: with the threading refusal gone the
   structurizer gives an `if`/`else` with a `ret` in each arm. What it now pins
   is an entry-balanced procedure reached at two depths destacking, which is
   real; the interior-label case it was named for is correction 2's, still
   refused. Renamed in kind, not in name (control + landed).
4. **The page-one premise the bullet specifies is nearly inert.** It carries
   **7 of the 422 raw-call tunes**, against 344 for the displacement premise,
   and none of the 78 that stay refused: the verdict is program-wide, one
   ⊤-wide deref anywhere kills it, and 611 of 624 tunes carry `unnamed_addr`
   residue. It is kept as the second disjunct because it is sound and cheap,
   but the premise that pays is the displacement one. 2a's certification is
   *not* the premise it could have been: certification is stated over the
   emitted program and `drop_sp` runs before that program exists, so the reach
   vocabulary (`addr_range`/`span`/`addr_floor`) is what is available there.
5. **`low_held_cursor`'s fabric does not leave, and the blocker is neither of
   2c's rules.** Both of its asserts stand: the invariant
   (`refuses low_held`, `sp` survives) passes, and the xfail does not flip.
   What changed is the reason — `sp_linked` is gone from that fixture (its
   calls stand at the entry), the balance is proven, and `sp` survives because
   rung (d0s) refuses both slots with *an unresolvable address may alias the
   live slot*: the `(ptr),y` deref between the pushes and the pulls. Bounding
   that deref needs the pair's extent, and the pair is refused certification
   *because* it is held through page one — a real circularity that no scheduled
   phase breaks by construction. The xfail's reason is re-pointed at **Phase
   6**, whose value-set walker is the analysis that bounds an unresolvable
   address without certification (`alias_web`'s own case).
6. **Phase 1's balance premise was masking the per-slot blockers.** The slot
   class *stack effect not zero* falls 372 -> 254, and of the 118 refusals it
   frees, **91 re-refuse on premises it had been hiding**: *slot not both
   stored and read* 10 -> 58, *read not dominated* 1 -> 40, *unresolvable
   address may alias* 101 -> 106. Twenty-seven slots actually promote; the
   balance premise was the first failure, not the only one.

**Ledgered precision items**, all four with the measurement that priced them:
the label/`goto`/loop-edge refusals of correction 2 (measured at entry as 37 of
288, each a stack-balanced procedure whose interior edge the walk cannot
follow) and the 108 `unobserved`-edge refusals, which the same withdrawal
keeps; the genuine imbalances (`ret`/`goto` at entry+2 — a procedure discarding
its own return address, `sp_unbalanced`'s fixture); the 78 tunes whose raw call
stands displaced; and 2a's certification as a page-one premise, which needs
`drop_sp` to run after the emitted program exists. `low_held` defs are
**96 corpus-wide, unmoved** — the fabric leaving did not free one, which is
correction 5 measured rather than argued.

### Phase 2.5 — the value walker as an instrument (analysis only, no text change) — DONE

**The finding that made this phase.** Five partial value analyses are in tree,
each written for one shape: `addr_bits` (bits an address may set), `addr_floor`
(bits it must set), `addr_range`/`span`/`overlaps` (the interval on a split
address), `_counter_range` (the must-hit set of a `for` counter with no early
exit), and 2c's `_off_page`, which stacks three of the others to answer one
value question for one shape. Each approximates the same question — what values
may this expression take — and each is coarse in a different direction. That is
§3's design error of the framing document recurring inside the phases written
to cure it.

The same premise blocks the rest of the plan:

| customer | size at entry | its fixture |
|---|---:|---|
| Phase 3 (ii) `aliased`: `zp,X` clobbers zero page | census `mod_addr` | `sp_scratch_floor` (adjacent) |
| R1 `wide_store`: `loc_unresolved` at the seat | `fuse_measure` `wide_classes` | — (owed) |
| G2: `(zext2(reg)+$00NN)` bounded inside the stack | `g2_boundable` | `g2_store` (xfail G2) |
| Phase 6 `web_alias` | `ptrcert` `lift_refusals` | `alias_web` (invariant) |
| Phase 6 `extent_unmappable`, the `foreign` half only | `ptrextent` `unmappable_foreign` | `computed_rows` (invariant + xfail P6) |

Every count above MUST be re-measured at entry; the tree has moved through 2c
and 3a. `g2_boundable` is the sharpest statement of the gap: `fuse_measure`
already *names* stores whose true bound stays inside the stack, and no analysis
exists to prove the bound it names.

**Scope, and the reason it is cheap.** The proposal priced the walker over
mutable memory. The blocking specimens do not need that: `alias_web`'s X is
bounded {0..2} by control flow (`LDX #$00`/`INX` over three per-voice calls),
`mod_addr` is `zp,X`, G2's is a register addend. So the instrument is **locals
only — every memory read is ⊤** — over a strided interval (interval +
congruence; the congruence is what a modular `zp,X` wrap needs regardless).
The memory-join problem the proposal names as its primary open risk is out of
scope by construction. The first number owed is the one the plan does not have:
**how much of the standing residue is bounded by locals alone.**

**The structural deliverable: the in-edge map.** 2c settled what this is, by
building the wrong thing first. Its worklist fixpoint over label/`goto`/loop
edges passed every fixture and **diverged eight tunes**; it was withdrawn,
Phase 1's conservative rule restored, and `sp_loop_edge` pins the withdrawal.
The reason is in `_SpFlow.leaves()`'s docstring: a label may be entered by a
`goto` its list does not carry, by a jump no list enumerates, or by a dispatch
arm. **A structured statement list is not a CFG**, so a forward join over the
list alone can conclude a property holds on all paths to a point that an
unenumerated edge reaches without it.

This binds every remaining forward analysis, R8's included — and R8's is a
**must**-analysis that licenses promotion, so it carries more risk than the
displacement claim that broke. The deliverable is therefore the missing edge
set: for every label and dispatch arm, the sites that may reach it, across
lists and procedures, with the `unobserved` boundary. With that map a worklist
is sound; without it none is, and a label whose in-edges will not close is a
wall every lattice must take conservatively. Whether the map closes
corpus-wide is the number this phase owes Phase 3.

**Verdict vocabulary**, per queried expression: `bounded` (a strided interval
strictly inside the queried region), `top_memory` (a memory read reached the
expression), `top_call` (a callee's effect), `top_dyn` (a raw dyn form),
`top_width` (nothing narrower than the declared width proved), `top_edge` (an
in-edge the map could not close). Every ⊤ names which one, so the next phase
reads its prize off the class breakdown.

**This phase licenses nothing.** No rewrite, no refusal relaxation, no text
change. Verdicts are a report. Re-instantiating `_SpFlow` over the edge map is
**out of scope** — 2c's rule is sound as it stands and its relaxation is a
separate gated change. The consuming phases (3 and 6) admit the analysis on
their own gates, and the walker is written **sound by construction**: an
interval+congruence domain costs nearly nothing to keep sound, and a
measurement-grade approximation retrofitted into a license is how an instrument
becomes a defect.

**The oracle.** 3a's artifact records what the evaluator reached; §3's
differential guard applies unchanged — a value observed outside a claimed
static bound is an analysis bug found before any phase leans on it, and the
phase stops; static over-refusal is counted and reported, never failed.

**Gates.** Emitted text byte-identical **against 3a's reset baseline**
(aggregate `99d4fdec3da1107bf950a57f6a655d8109a475cca5888f1a59bf9c9b1689a942`
over the 624 built tunes at full Songlengths); `gate_sweep`, `lift_residue`,
`fuse_measure`, `storage_census` identical row for row — an instrument that
moves the census is not an instrument; `alias_web` and `computed_rows`
reproduce their pinned verdicts and the walker's verdict on each is asserted
beside them; hermetic tests in `tests/test_value_walk.py` (Phase 0's
`test_storage_census.py` precedent — an instrument owes hermetic tests, not
shredder fixtures, and §5.4 gains no rows); the 60s per-script budget holds.

**What it does not price.** Phases 4 and 5 are not value-bound — M-FP3's
dialect, the evaluator's support for it, and the boundary shadow are spelling
and grammar obligations. 2.5 re-prices 3 and 6 and nothing else, and MUST NOT
claim otherwise.

**Landed**: `tools/value_walk.py` (the domain, the in-edge map, the four customer
queries, the differential guard) and `tests/test_value_walk.py` (26 hermetic
tests; §5.4 gained no rows, and `alias_web`/`computed_rows` each carry the
walker's verdict beside their pinned one). No package file changed, so the
sweeps' decompile cache stayed warm and byte identity is by construction as well
as by measurement. The domain is one strided interval `{lo, lo+s, .. hi}` per
width with a **premise set** on every widening; the six verdict classes are read
off that set, hardest premise first. Two rules do the work five partial analyses
did separately: Warren's exact endpoint bounds for `|` and `&` (with `^` taking
`|`'s upper bound) subsume `addr_bits` **and** `addr_floor` in one step (`zext2(sp) | $0100` comes out
`[$0100,$01FF]`, not `[0,$01FF]`), and a `for` counter's **may**-set is its
bounds with no early-exit premise at all — `_counter_range`'s `_escapes` test is
a *must*-set premise and a bound does not need it.

**The in-edge map** (624 tunes, full Songlengths) — the number Phase 3 waits on:

| | corpus |
|---|---:|
| labels (label statements + `goto` targets) | **7,278** |
| — every edge into them enumerated | **6,350 (87.2%)** |
| tunes whose map closes entirely | **584 of 624 (93.6%)** |
| — walled by a raw dyn transfer (R8's `wall`) | 37 |
| — opened by an RTS-trick landing on a label pc | 3 |
| enumerated edges: `goto` / raw call / dispatch arm | **7,030 / 3,185 / 1,520** |
| — crossing a procedure boundary | 240 |
| RTS-trick landings | 659 |
| tunes carrying `switch goto` whose map closes | **121 of 142** |

**The re-pricing table** (each population re-measured at entry; `bounded` means a
strided interval strictly inside the queried region):

| customer | phase | population | bounded | top_edge | top_call | top_memory | top_dyn | top_width |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `wide_store` (R1/G2), all shapes | 3 (ii) | 105 | **67** | 6 | 5 | 27 | 0 | 0 |
| — `g2_boundable` + `loc_unresolved` | G2 | 65 | **65** | — | — | — | — | — |
| — `ptr_writethrough` | 2 | 38 | **0** | 6 | 5 | 27 | 0 | 0 |
| — `other` | — | 2 | **2** | — | — | — | — | — |
| modular **stores** | 3 (ii) | 927 | **73** | 376 | 188 | 171 | 119 | 0 |
| modular **loads** | 3 (ii) | 1,100 | **98** | 395 | 344 | 177 | 86 | 0 |
| `web_alias` | 6 | 287 | **1** | 155 | 64 | 23 | 37 | 7 |
| `extent_unmappable`, `foreign` half | 6 | 330 | **0** | 31 | 1 | 282 | 16 | 0 |

**Does the locals-only domain suffice?** For R1 and G2, yes and completely: every
one of the 65 G2-shaped wide stores is bounded inside the stack, so the bound
`g2_boundable` is *named* for is proved, and R1's actionable residue is the
38 write-throughs Phase 2 already owns. For everything else, no — but **memory is
the binding premise for exactly one customer**. Of the 330 `foreign` webs, 282
are blocked by a memory read and by nothing else (premise `memory` or
`memory|width`), which is by construction: a pointer root *is* a memory cell, so
no locals-only domain can ever give it an extent. **Memory value-sets are on the
critical path for Phase 6's registry customer and only for that one.** For
`web_alias` memory is 23 of 287 while the back edge is 155 (116 of them
loop-carried and nothing else) and the call graph 64; for Phase 3's modular
stores memory is 171 of 854 behind the back edge (376) and the call graph (188).
The premise histograms are in `out/value_walk.json` per tune.

This does **not** restate Phase 3's prize: the promotable-field count is a
product of that phase's liveness analysis (i), which 2.5 does not run. What moves
is (ii)'s input, in both directions — 67 of the 105 `wide_store` threats are
removed corpus-wide, and 854 of 927 modular stores still threaten their whole
page in the 90 tunes that carry one.

**The corpus-forced corrections:**

1. **The edge set 2c named is incomplete, and the missing kind is the raw call.**
   2c's withdrawal lists three ways a label may be entered by an edge its list
   does not carry — an `igoto`/`swg` pair, a `goto` from another list, a block
   past an `unobserved`. There is a fourth: a raw `call` whose target is a
   **label of the calling list itself**. ASL/04 `$1039`/`$103C`
   (`tools/disasm_tune.py ASL/04 --start 0x1021`) is
   `JSR $1040` / `JSR $103F` into the labels at `$103F`/`$1040`, three per-voice
   passes done by nested JSR rather than by a loop; the corpus carries **3,185**
   such edges. Built from `goto` alone the map claimed one index where three
   arrive, and §3's differential guard caught it corpus-wide before any prose
   did: **334 contradictions over 81 tunes**, each an address the run reached
   outside a claimed static bound (the sampled ones a fixed stride below a
   singleton claim, which is the missed call edge exactly). With the call edges
   the guard is **0 contradictions over 41,375 checked address sites in 624
   tunes**. This is the phase's headline and it is a
   finding about the doc, not about the corpus: Phase 3 MUST take the raw call as
   an in-edge, and `_SpFlow.leaves()`'s docstring is short by one clause.
2. **`g2_boundable` and `loc_unresolved` are one shape, not two.** All 33
   `loc_unresolved` stores resolve to `(zext2(reg) + $00NN)` — the address bound
   to a temporary because the cell is read as well as written — and all 33 are
   bounded by the rule that bounds the 32 inline ones. §0's third wide-store
   shape ("a bare `t0:2` local G1 cannot resolve at that seat", disposition
   "resolve-strengthening or per-tune refusal") is G2's own add wearing the
   emitter's other spelling. b3's standing lesson exactly: a rule stated over one
   spelling is a rule about the emitter.
3. **The census `mod_addr` signature counts a third of its own population.**
   `lift_residue._expr_sig` classifies a `mem` node, so it sees modular **loads**
   in the **inline** spelling only: 741 sites / 91 tunes. Following the temp
   binding and counting store destinations, the corpus carries **2,027 modular
   accesses over 98 tunes — 927 of them stores over 90 tunes**, and the stores are
   the half Phase 3 (ii) is actually about. The census number is not wrong for
   what it counts; it is the wrong number for that bullet to quote.
4. **Phase 6's flagship specimen is `top_call`, not a memory question.** §2 Phase
   6 says the ASL/04 `$128B` store's X "is the voice index, bounded {0..2} by
   control flow (`LDX #$00`/`INX` over three per-voice calls), so `STA $FD,X`
   writes `$FD..$FF` — precisely the bound a value-set walker reads." The machine
   agrees about the bound and the emitted dialect refutes the inference: the
   three passes are the nested-JSR loop of correction 1, so X at the store is
   clobbered by a raw call and the walker answers `top_call` with premise
   `call|width`. Reading {0..2} off it needs a callee summary over a
   self-entering call, not a memory value-set. The pricing input Phase 6 was
   promised is real (287 webs) but its type specimen is mis-diagnosed.
5. **`alias_web` does not reproduce its own specimen's premise.** The fixture
   bounds X through a RAM counter (`LDX $CTR`), so the walker answers
   `top_memory` — asserted beside the pinned refusal — while the corpus specimen
   answers `top_call`. The invariant it pins (an unresolvable store refuses the
   web) is untouched; what it does not pin is the reason.
6. **The verdict vocabulary has no class for "bounded and genuinely aliased".**
   Seven of the 287 `web_alias` webs get an **exact** bound from the walker that
   really does overlap the pair; they carry an empty premise set and are reported
   `top_width` because that is the vocabulary's only home for them. They are
   correct refusals, not precision gaps, and a phase reading the breakdown must
   subtract them before counting a prize.
7. **A `switch goto` is no wall for the map, and `Defs` refuses it anyway.**
   `frameproc._COMPUTED` includes `swg`, so `Defs._verified` refuses **every**
   label join in any program carrying one — 142 tunes. The arms are inlined
   bodies entered from the dispatch beside them, so they add no unenumerated
   edge: **121 of those 142 tunes have a fully closing map.** R8's "an ordinary
   join" is right, and the blanket refusal in the committed walker is precision,
   not soundness. Relaxing it is a later phase's gated change; this phase only
   measures it.

**Gates** (all full-corpus, all run):

| gate | verdict |
|---|---|
| byte identity vs 3a's reset baseline | aggregate **`99d4fdec3da1107bf950a57f6a655d8109a475cca5888f1a59bf9c9b1689a942`**, 624/624 built, reproduced before and after |
| `lift_residue` | 624 rows, 0 refused, census sum **30,854**, every signature unchanged |
| `fuse_measure` | 624 rows, 0 refused, wide stores **105 / 54 tunes**, class histogram unchanged |
| `storage_census --frames 1500` | 624 rows, totals unchanged |
| `gate_sweep`, full Songlengths | **622 built / 621 clean**, the three standing §4 exclusions and nothing else |
| the §3 differential guard | 41,375 sites over 624 tunes at 1,500 frames, **0 contradictions**; 40,600 over-refusals counted and reported |
| suite, `-n 24`, both passes | **2,551 passed / 495 skipped / 24 xfailed**, then **14 passed**; `test_value_walk.py` 26 of them |
| per-script budget | the per-tune unit is **≤ 2.5s**; the corpus driver is a `-j` sweep like the other four (95s at `-j 24`, 147s with the guard) |

**Not done**: the walker takes ⊤ at every loop back edge (premise `loop`), which
is the single largest ⊤ class in three of the four customers — a widening
fixpoint is the standard answer and is out of this phase's scope; no consumer
reads the walker yet, and re-instantiating `_SpFlow` or relaxing
`Defs._verified` over the edge map stays a later phase's gated change; the map's
928 unclosed labels sit in 39 tunes, every one carrying a raw dyn transfer or an
RTS-trick landing on a label, so closing them is R8's `wall` problem and not a
map problem (a 40th tune has a landing-label and no unclosed label of its own).

### Phase 3 — frame-local promotion (the scratch elimination)

Mechanical rule, both conditions computed by committed analyses:

- **(i) not live-in at the frame boundary**: a **forward** written-before-
  read analysis over the emitted dialect from `play` entry — across calls,
  joining over `if`/`loop`/`for`/`switch goto` arms and labels via worklist,
  `unobserved` as a terminator (R8; `dispatch_scratch` is the xfail). The
  `wall` refusal covers only raw dyn forms, re-sized at entry. **Its edge set is
  2.5's in-edge map** (`tools/value_walk.py:InEdges`), which closes on 6,350 of
  7,278 labels and in 584 of 624 tunes and carries the raw call as an in-edge —
  the kind R8's own list omitted and the one that contradicted the run (§2 Phase
  2.5 correction 1).
- **(ii) unthreatened**: a per-cell interval test — a cell is threatened by
  any store whose reach interval it intersects: uncertified wide stores
  (`wide_store`, which 2.5 bounds for 67 of 105), in-bounds computed stores
  (`zp,X` modular stores clobber zero page; **927 modular stores over 90 tunes**,
  of which 2.5 bounds 73 — the census `mod_addr` 741 / 91 counts modular *loads*
  in the inline spelling only, §2 Phase 2.5 correction 3), and certified cursor write
  extents (a write-through lands inside its block with the evaluator never
  faulting). Refusal `aliased`, computed from `store_reach`/`overlaps` —
  **with `addr_floor` mandatory**: without it the kept push
  `(zext2(sp [+ k]) | $0100)` reaches an interval from zero and spuriously
  threatens every zero-page cell in the 201 scratch-bearing tunes that keep
  page-one stores (3,292 fields, 35% of this phase's prize; demonstrated
  against the committed code; `sp_scratch_floor` is the xfail).

A cell passing both leaves `state { }` and becomes a local; cross-procedure
frame-locals follow the params/returns vocabulary or refuse `crossproc`.
Refusal vocabulary: `livein`, `aliased`, `crossproc`, `wall`, `wide_store`.

**The cross-frame obligation.** Promotion is licensed by the static proof
alone; dynamic verdicts are ceilings and oracle, never license.

- *Init coupling*: `state0` is the post-init image; the rule quantifies over
  every frame uniformly, so frame 0 is not a special case (`init_livein`
  invariant).
- *Late persistence*: a dynamically frame-local cell can persist on a path
  first taken at frame 9,000 (`pos_54EC`; `path_persist` invariant). Cannot
  make promotion unsound, but makes the 300-frame gate blind — so the seven
  review tunes gate at full Songlengths length every phase, and:
- *The differential guard*: `storage_census --frames full` is the oracle —
  a statically-frame-local cell the dynamic record shows carrying a value
  across any boundary is an analysis bug found before emission; the phase
  stops (§7.10.9 built in as a gate; it caught three real bugs in the
  prototype, §5.1). Static over-refusal is counted and reported, not
  failed.
- *Subtune scope*: verdicts are per-program (per-subtune), claiming nothing
  across subtunes.
- *The `for` premise*: the dialect's `for` is do-while (body before
  `fortest`), so a must-write in the body holds at the exit; owed a fixture
  when the analysis lands.

Gates: full `gate_sweep` (300f corpus + full-length seven); the
differential guard; census `unnamed_addr` down, sum down; scratch-declared-
as-state monotone toward 0; `fuse_measure` `unnamed` down.

### Phase 4 — column coalescing (the wide-value lift, M-FP3)

Scope: transient (lo,hi) locals and paired table columns still packed by
hand — R4 classes (i)/(iii), `word_pack`'s two skeletons, `hi/lo_byte`,
`shift_pair`, `borrow` (~300 sites on the seven; the census majority
corpus-wide). Rule: a candidate is a def-use-linked (lo,hi) pair in an
enumerated adjacency shape; the rewrite happens in the unified e-graph,
every instance Z3-proven per the existing admission gate (eqlift-adoption
§4). The pair-cell dialect (M-FP3) is the grammar deliverable. Follin's
handler-table pack (`m_6C76[a]<<8 | m_6C37[a]`) is in scope as a pack like
any other; keying the switch on the command byte is a Phase 6 readability
candidate.

Gates: **evaluator support is a precondition of the rewrite landing** (a
text the gate cannot execute contradicts §3 and eqlift-adoption §6; the
Z3-only window covers development, never a landing). Census block
`carry_val`+`word_pack`+`hi_byte`+`lo_byte`+`shift_pair`+`borrow` falls **as
a sum** per tune; saturation holds the 60s budget.

### Phase 5 — the boundary keeps a shadow, not a read-back

Scope: exactly `_widen`'s RMW form; corpus size 1,123 sites / 466 tunes.
Rule, in preference order: (a) where Phases 2–4 made the full word
available, emit it and delete the read; (b) otherwise a shadow variable for
the held lane (no RAM address), written alongside every store of its
register — but first cost the zero-grammar alternative: delete `_widen`'s
RMW form and emit the honest byte store, paying census `narrow_sink`
(§7.10.12's own framing argues the byte store is honest). Either way: a
register also written through an unresolved `sid.reg[i]` index cannot keep a
shadow coherent — named refusal; the shadow's initial value is defined
against `framelog`'s `held0` seeding. `framelog`'s `held` semantics are
untouched; three families already prove the target shape at 0 sites.

Gates: `sid_readback` census column to **0** (Gate FP cannot see this
defect, §7.10.12, so the census is the gate); full `gate_sweep` otherwise
unchanged; canonical fixpoint over any new declaration.

### Phase 6 — re-measure, then decide about the walker

Not a rewrite phase. With 1–5 landed: re-run triage and census, re-derive
§7.10.7's ranked list. The value-set walker's deferral test now runs in
reverse — its pricing inputs are 2b's `web_alias` ledger (287 webs, 2,751
unresolvable stores; `alias_web` is the type specimen) and the `foreign`
half of the unmappable addresses (`computed_rows`), so the 2026-08-08
expectation is that it gets built. Disassembly of the flagship specimen
(ASL/04 `$128B`) says the ledger overstates genuine aliasing: the store's X
is the voice index, bounded {0..2} by control flow (`LDX #$00`/`INX` over
three per-voice calls), so `STA $FD,X` writes `$FD..$FF` and never reaches
the `zp_FA` pair — precisely the bound a value-set walker reads.
**2.5 measured this and the inference does not hold** (§2 Phase 2.5 correction
4): the three passes are nested JSRs into the calling list's own labels
(`$1039: JSR $1040` / `$103C: JSR $103F`), so X at the store is clobbered by a
raw call and the walker answers `top_call`, not `top_memory`. Of the 287
`web_alias` webs the walker bounds **1**; 155 are `top_edge` (116 loop-carried
alone), 64 `top_call`, 37 `top_dyn`, **23 `top_memory`** and 7 exact and
genuinely aliased. The memory value-set is this phase's lever for
`extent_unmappable` — 282 of its 330 `foreign` webs are blocked by a memory read
and by nothing else — and for `web_alias` the levers are a loop widening and a
callee summary. Also
parked here: the loop-to-expression rule for loop-carried lane rotates
(`shift_divide` — a power-of-two divide is one wide variable `>>`; the
operator exists, the rule does not), and `/`/`%` themselves stay absent
from the dialect until a true shift-subtract divider appears in the
corpus, which none of the measured residue needs. G2 is partly consumed by Phase 0's
`g2_boundable`; computed-jump scoping dissolves into R8's forward analysis.
Readability candidates parked here: switch-on-command-byte, and — handed over
whole by b3 — the arithmetic row (`computed_rows`): the fixpoint reads 16-bit
LE words out of declared data, and a row built by arithmetic is in no block, so
`extent_unmappable`'s 399 webs are this phase's alone.

## 3. Divergence guards (cross-cutting, every phase)

- **The sum is the metric.** A phase that moves its own class down and the
  census sum up is rejected as bookkeeping.
- **Gate verdicts stop the line.** Any movement not predicted by the
  phase's rule is stop-and-localize (§7.10.9/§7.10.14 method).
- **The differential guard runs wherever a phase claims a static property
  an execution can witness.** Dynamic contradicts static -> stop; static
  over-refuses -> counted, reported.
- **Refusals are ledgered** per tune per sweep; a ledger that shrinks
  without a rule change is a finding.
- **The shredder is the executable spec** (§5.4): each promise is
  `xfail(strict=True)`; landing a phase flips its test to XPASS and the
  marker is removed in the same change. Claims obey the headline
  discipline: no "unliftable" without disassembly and a fixture.
- **The seven tunes are the standing review set** — emitted-text diffs read
  by hand, full-Songlengths Gate FP per phase, before any corpus sweep is
  trusted.
- **No per-tune rewrites** (eqlift-adoption §4); **sampled verification is
  not verification** (§7.10.9): every gate is the full 624-tune sweep.

## 4. What the evidence set still does not cover, stated

- **The 54 wide-store tunes** are ledgered, not solved (~13 clear on G2,
  ~12 ride Phase 2, ~30 need a resolve strengthening priced by Phase 3's
  ledger). `C64_World` is doubly excluded (14 wide stores + the standing
  evaluation fault); `1st_Decent_Hardcore` likewise at 1500 frames.
- **Digi/sample players** are outside Gate FP's input class and this plan.
- **`Rambo_First_Blood_Part_II`** (Class C, beneath every rung, §7.10.14)
  predates and is untouched by this plan.
- **Dynamic ceilings are 1500-frame** except where `--frames full` is
  noted; promotion claims are static, so this bounds reported yields, not
  soundness.

## 5. Prototypes and worked examples

### 5.1 The Phase 3 analysis, run to zero contradictions

A ~200-line forward written-before-read prototype ran on all seven against
the instrumented-execution oracle: promotable (static) 19/6/3/10/15/1/1 of
26/21/26/95/120/141/94 fields, dynamic ceilings 22/15/12/31/46/62/17,
**contradictions 0 after fixes**. The differential guard caught three
unsound bugs before any rewrite existed (a table-extent misread, a `callb`
body's `ret` read as a procedure exit, unknown `call` targets treated as
no-ops) — the guard earning its Phase 3 seat. The unknown-callee rule now
assumes a ⊤ read set; the 6 + 2 unresolved `call` targets that collapse
Comic_Bakery and Automatas to 1 promotable each are the `crossproc` class, a
named precision item. Units: "fields" is `prog.state` entries; §0 counts the
rendered `state { }` block; Phase 0's instrument reports both in one row.

### 5.2 The Phase 2 certification, run against the block registry

Two findings shaped the rule: **the block registry already exists**
(`data_decls` records pointer tables with `targets` and blocks with `via:` —
2a certifies against it, not a new analysis), and **at-rest values lie; at-
use values are the premise** (Galway multiplexes `zp_F6/F7`, observed
holding two counters — so certification is per value-web, not per cell, and
the extent check samples at the deref; harmless by construction since the
cursor faults at the read). The draft's structural-certification and
two-shape claims were corrected by the landed 2a (§2 2a).

### 5.3 The worked examples, each Z3-proved

Six theorems over all inputs (eqlift admission style), becoming rules when
their phase lands:

**Phase 2 — the cursor advance (Ghouls, `case $6858`).** PROVED
`Concat(t2,t0) == Concat(hi,lo) + 2` over the emitted two-lane advance, so
the target spelling is one wide `+= 2`; PROVED the page-cross guard equals
"the wide add changed its hi byte", so it becomes a bound check or vanishes
into the extent fault.

**Phase 4 — the carry-chain pack (Angry_Birds, freq).** PROVED the emitted
lane/carry spelling equals the one wide add
`(zext2(m_091F[x]) << 8) + zext2(w11) + zext2(w12) + zext2(cflag)` for
`cflag <= 1`; PROVED `pack(lo(x), hi(x)) == x`.

**Phase 5 — the read-back becomes a shadow (Aces_High/Comic_Bakery).**
PROVED `((s & $00FF) | zext2(h) << 8) == Concat(h, lo(s))` and the lo-lane
mirror: with the held word in a shadow, each RMW is a byte update plus a
whole-word store — no load of a write-only register survives.

### 5.4 The shredder: the plan as an executable, failing spec

`tests/test_shred_regmodel.py` is the `test_shred16.py` discipline pointed
at this plan: synthesized players pinned to the phase promises, each
building and gating today (hard asserts), each canonicality assert
`xfail(strict=True)` until its phase lands — the suite and the plan cannot
drift apart silently. Three fixture kinds: **xfail targets** (what a phase
must achieve), **standing invariants** (what no phase may break), and
**controls** (what already lifts — the executable refutation of a myth).

| fixture | kind | pins |
|---|---|---|
| `scratch` | xfail P3 | scratch cell becomes a local, not state |
| `pointer_walk` | xfail P2 | mixed reload+advance deref names a datum, no `mem[..]` |
| `borrow_chain` | xfail P4 | SBC-lane compare becomes one wide compare |
| `lone_lane` | xfail P5 | lone-lane widening owes no register read-back |
| `mux_pair` | xfail P2 | Galway's multiplexed zp pair certifies per role (M2) |
| `cursor_save` | xfail P2 | Follin's save/restore — cursor values as data |
| `writethrough` | xfail P2 | write-through pointer store becomes a bounded table write |
| `g2_store` | xfail G2 | `(zext2(y)+$NN)` store bound, asserted via `addr_bits` |
| `sp_spill` | **landed P1** | two-depth sp-relative spill destacks — hard pass |
| `sweep_blit` | invariant | a covering blit stays byte-wide (§7.7 `$CA6E`) |
| `hi_first_pair` | invariant | the `hi-first` order flag survives every rebuilder |
| `path_persist` | invariant | a path-dependent persistent cell stays state |
| `alias_state` | invariant | a cell a write-through store can clobber stays memory |
| `init_livein` | invariant | frame 0 reads init's value: the init/state0 coupling |
| `sp_unbalanced` | invariant | an unproven stack effect keeps `sp` and its stack page (720_Degrees `$C31D`: `PLA/PLA/RTS` discards its own return) |
| `sp_loop_edge` | invariant (2c) | a stack-balanced procedure whose back edge stands at a displacement keeps `sp` — the relaxation 2c withdrew |

The `web_unnamed` family (2b's rewrite record): one fixture per 6502 idiom
behind the 74-web residue, so the class is readable at suite cost. The
discriminator they establish: whether any byte-lane *read* of the pair
survives rung (d2) — refuted candidates (interleaving, store order, second
destinations) each have the fixture pair that killed them.

| fixture | kind | pins |
|---|---|---|
| `plain_advance` | control | a bare in-place advance fuses; rung (g) resolves it |
| `dual_store_word_copy` | control | a second destination is no refusal where every lane read folds to a word |
| `dual_store_advance` | xfail 2b | the Follin advance: per lane a save copy, then the pair in place |
| `dual_store_pair_first` | xfail 2b | store order within a lane does not free the pair |
| `dual_store_via_regs` | xfail 2b | adjacent pair stores still refuse — interleaving is not the discriminator |
| `dual_store_hi_first` | xfail 2b | hi lane first; rung (d2) refuses on an intervening operand change |
| `dual_store_computed` | xfail 2b | a cell-stepped advance behaves as an immediate one |
| `dual_store_lo_only` | xfail 2b | one lane's copy refuses `def_unliftable` — a rule gap the corpus never exhibits |
| `stack_spill_cursor` | xfail 2b | `PHA`/`PLA` spill: no 16-bit push, the stack descends — no pack exists |
| `deferred_carry_cursor` | xfail 2b | the carry arm never ran: the hi lane is in the code, not the text |
| `table_spill_cursor` | xfail 2b | a de-interleaved save-back destination can never pair |
| `unpaired_half_store` | xfail 2b | the only fixture reaching `unpaired half store(s)` |
| `inpage_advance` | **invariant** | an advance with no carry arm in the machine never fuses to the wide add — a semantic rule; the corpus population is zero confirmed (every measured candidate had its arm in code) |

The claims-discipline pass (2026-08-08) added the mechanisms behind every
remaining "unliftable"-shaped claim, each from the disassembly its docstring
cites (`tools/disasm_tune.py` reproduces it):

| fixture | kind | pins |
|---|---|---|
| `follin_jump` | control + **landed b3** | the script jump (Ghouls `$6AD0`) fuses and is lift-eligible; the fixpoint closes on the block it walks — hard pass, and the fused `block_read` classifying `computed` is its own assert |
| `follin_ret_stack` | control + **landed b3** | the depth-indexed split-column call stack (`$6ADD`/`$6B42`) fuses and is eligible; certification refuses `extent_mutable` and nothing else — hard pass |
| `lone_lane_block_read` | invariant (b3) | the hi lane alone read out of the block (Data_Data_Data_Data `zp_D2`, American `$B41A`): defs closed, `ptr_extent_open` — no word for the fixpoint to key on |
| `low_held_cursor` | invariant + xfail **P6** | the pair held through page one (Angry_Birds `$09F1`) refuses `low_held`, keeps `sp`; 2c measured the blocker as neither of its rules (§2 2c correction 5) |
| `alias_web` | invariant | a wrapping `STA $zz,X` with unbounded X (ASL/04 `$128B`) refuses `web_alias`; the machine spelling survives; 2.5's walker answers `top_memory` beside it (the fixture's X is a cell, the specimen's a raw call — §2 Phase 2.5 correction 5) |
| `call_returned_row` | control | a row returned in A/X across a call refuses certification, never the lift |
| `computed_rows` | invariant + xfail **P6** | an arithmetic row off the registry: b1-eligible, `extent_unmappable`, and no block read for b3 to enumerate; only the value-set walker flips it, and 2.5's locals-only one answers `top_memory` beside it |
| `shift_divide` | xfail P6 | the divide accumulator (Cool_Air `$1447`): `(T2[y]-T1[y]) >> n` as a loop-carried `LSR`/`ROR` — the `>>` exists, the loop rule does not |
| `phase_split_reload` | xfail 2b | the pair's halves reloaded in different frames (Air_on_a_Rasterline `$0C1A`/`$0D05`) — each store is a lane replacement, a masked word update away from fused |
| `dispatch_scratch` | control + xfail P3 | the SMC-operand dispatch emits `switch goto` (no wall); scratch promotes across the join |
| `sp_fix_balance` | control + **landed 2c** | an entry-balanced procedure reached at two depths destacks and drops `sp` — hard pass |
| `sp_call_at_entry` | control + **landed 2c** | a raw `call` standing at the entry displacement drops its linkage; `sp` leaves with the call still in the text |
| `sp_call_displaced` | invariant (2c) | a raw `call` at a nonzero displacement keeps `sp_linked`: the drop would move the machine's pushed return |
| `sp_scratch_floor` | xfail P3 | zero-page scratch beside kept `sp` fabric promotes once `aliased` uses `addr_floor` |

The extent guard itself (an access past the recorded extent faults at
evaluation) is pinned hermetically in `tests/test_frameval_extent.py`; the
horizon MUST is a runnable gate (`gate_sweep --extents`, given the
`--frames full` artifact — §2 2b (b3) correction 6). b3's recurrence licence
has its own hermetic player, `recurrent` in `tests/test_storage_census.py`:
one toggling cell, so the state image repeats at a frame boundary and
`--close` fires, beside `scratch`, whose counter never repeats.

Writing the first fixture family measured the ladder honestly in its
favour: the first drafts of `pointer_walk` and `borrow_chain` XPASSed
immediately (framemath already coalesces a pure advance and lifts a
lane-paired subtract), so both were hardened to the corpus's residue
shapes. `unpaired_half_store` carries one web of each verdict in the same
procedure, pinning the granularity as the web; `framefuse.refusal()`
reports first-failure-only, so a fixture's stated class is the first that
fails, not necessarily the only one.

## 6. Decision log

Adopted decisions and corrections, newest last. Full narratives are in git
history (`git log --grep=regmodel`); only what still binds is here.

- **Adversarial review (pre-execution).** Five defects folded: Phase 3's
  `aliased` covers in-bounds computed stores and certified write extents;
  Phase 4's evaluator-support precondition; Phase 1's `720_Degrees` gate
  exception; the `for`-is-do-while premise stated; §5.1 units fixed. Three
  simplifications recorded: 2b deferral (later reversed), Phase 5's
  zero-grammar alternative (stands, §2 Phase 5), Phase 0 instruments folded
  into standing tools (done).
- **Post-Phase-1 review (2026-08-08).** The heading promised `raw_sp -> 0`
  while the gate asked 0-or-refused: heading rescoped, fabric removal
  scheduled as 2c with its 2a-dependency stated. R7 capped at 326/624 while
  refused fabric stands. Phase 3 (ii)'s interval-from-zero defect found and
  floored (demonstrated against committed code; fixture `sp_scratch_floor`).
  Obligation added and since discharged in 2a: certification closed under
  **reads** as well as writes (`role_entangled`/`role_opaque`).
- **Post-2a course correction (2026-08-08).** Three corpus-forced
  corrections folded at §2 2a (root-count abbreviation; structural
  certification measured-false for GT/SW/Galway; Follin's third `other`
  shape). Status header brought to DONE-tracking; §5.4 aligned with the
  committed suite.
- **Post-2a reframe (2026-08-08).** The 2b deferral was priced by
  static-certification coverage; that criterion was wrong by R9's own text
  — the extent claim's license is the evaluator fault (the control layer's
  observed-primary discipline is the precedent). 2b respecified as the
  b0–b5 pipeline; annotation-over-constructs stands; the `web_alias` ledger
  becomes Phase 6's pricing input, its deferral expected to reverse.
- **Post-2b-analysis (2026-08-08).** The reframe was right about the
  license, wrong about where the cost sits: the registry (`extent_
  unmappable`, 399 webs) is the largest blocker, not the alias. b3 stops
  being accounting-only; Phase 6's walker gains a second customer (the
  `foreign` unmappable half).
- **Post-2b-rewrite (2026-08-08).** 251 of 325 webs lifted. `def_unliftable`
  and `web_unnamed` are one rung (d) condition counted twice; the lever is
  rung (d)'s read-side rule, not the def side (fix reaches 66 of 74; 8 stay
  refused for good); the census `unnamed_addr` gate is unmeasured until
  `lift_residue` reads `prog.resolved` and the artifact — that discharge
  moves rung (f)'s 633 sites too, to be reported apart.
- **Claims-discipline pass (2026-08-08).** The headline rule added: no
  unliftable claim without disassembly and a fixture. `tools/disasm_tune.py`
  committed (both prior course corrections were resolved by ad-hoc
  disassembly; the tool makes it one command). Every standing claim
  resolved into the nine fixtures of §5.4's third table; two findings out
  of the pass: the Follin jump and ret-stack machinery is fused and
  lift-eligible in isolation (the blocker in the real tunes is rung (d)'s
  read side plus the registry, not the script machinery), and a fused
  `block_read` def classifies `computed`, so b3's enumeration must key on
  the fused spelling. Plan prose cut ~60%; the fixture index, not the
  narrative, is the mechanism record.
- **Byte-residue challenge (2026-08-09).** Applying the discipline to its
  own last exemption dissolved it: the "8 genuinely byte-wise" webs all
  carry their carry arm in code (deferred-carry, text-read
  misclassification; sites cited in §2 2b correction 1), the division
  accumulator is a power-of-two divide (`shift_divide`, one missing loop
  rule, no missing operator — `/`/`%` stay absent until a shift-subtract
  divider appears, which nothing measured needs), and the last web is a
  cross-frame lane reload (`phase_split_reload`, a masked word update).
  **Zero webs in the measured residue are unliftable in principle**; every
  one now has a named spelling, a named missing rule, or a named owning
  phase, each with a fixture. The lesson is standing: a claim read off the
  emitted text is not a claim about the machine — `unobserved` arms are
  invisible in the text by design.
- **Post-b3 (2026-08-09).** The static enumeration landed and 2b is closed.
  Block-rooting 441 -> 480 roots, 99 of 103 extent claims certified against
  b0's observed run, `extent_mutable` 182 roots / 132 tunes; text
  byte-identical 624/624 with and without the artifact, gate 622/623, census
  sum 31,112, `lift_residue`/`fuse_measure` identical row for row. Four things
  the corpus decided against the bullet as written (§2 2b (b3)): E₀ was
  incomplete and the §3 differential guard is what proved it — six claiming
  roots restored from a held table whose rows the closure never followed, five
  closed by feeding the hold into the read set and the sixth explained as a
  row-index span, not a defect; the fused `block_read` the bullet told b3 to
  key on is **2 definitions in 624 tunes** against 126 byte-lane ones, so
  "key on the fused form too" had to mean *both* keyings and not that one;
  `--close` closes 9 tunes and buys **zero** certificates at 1,500 frames;
  and admitting a play-written pointer table into the web costs b1 one web
  (`Amazing_Spider-Man`), which the work list absorbs, so "the lift and guard
  stand" holds for the emitted text but not for the eligibility ledger. The
  standing lesson: a rule stated over one spelling of a value is a rule about
  the emitter, not about the machine — count the spellings before pricing it.
  `computed_rows` keeps its xfail and its reason narrows to **Phase 6 alone**:
  b3's fixpoint reads declared data, and an arithmetic row is in no block, so
  nothing short of the value-set walker flips it.
- **Post-2c (2026-08-09).** The stack fabric leaves where the machine's own
  pushes cannot move, and 2c's two rules are not the two the bullet named. The
  balance blocker is the **call graph** (a `pcall` handing `sp` back, 110 of
  288) and the `unobserved` edge (108), not the interior label (37); the
  linkage premise that pays is **displacement** (every call standing at the
  entry displacement), not the page-one verdict the bullet specified, which
  measured nearly inert (`sp_linked` *rose* to 404 tunes under it alone) and
  survives only as the second disjunct. 2a's certification could not be that
  premise at all: it is stated over the emitted program and `drop_sp` runs
  before that program exists. The standing lesson is the withdrawal, not the
  landing: **a structured statement list is not a control-flow graph.** The
  label/`goto`/loop-edge fixpoint — the bullet's headline rule — passed every
  fixture and diverged eight tunes, because a label or dispatch arm may be
  entered by an edge the list does not carry (`igoto`/`swg`, a `goto` from
  another list, a block past an `unobserved`), and a "dead" tail silently
  drops the stack effects of everything it skips. Phase 1's entry-displacement
  rule is restored for those edges and pinned by `sp_loop_edge`. Two ledger
  consequences: `sp_returned` retires by rule (a balanced callee hands back
  what it was given), and Phase 1's largest slot class was never about balance
  (*stack effect not zero* 372 -> 43, the freed slots re-refusing on the
  premises it had been masking). `low_held_cursor` does not re-enter 2b: its
  blocker is the `(ptr),y` deref aliasing the live slot, and the pair's own
  certification is refused *because* it is held through page one — a
  circularity handed to **Phase 6**'s walker, not to any 2b work list.
- **Post-3a (2026-08-09).** `frameprog` was never derivable from `sidprog`, and
  the decision is to **supersede, not restore**. Both project the same
  `structured.Model`, but the sidprog projection is lossy for frameprog's
  purposes — `sidprog.TextModel` carries no init tracer and sets `written` from
  the dispatch table alone — so `frameprog.program(sidprog.parse(sidprog.emit(m)))`
  is a different, silently shorter program (hermetic `t_jump_table`: **32 lines
  against 18**). 3a made frameprog the total artifact: major **1** adds
  `image { }` (sidprog's proven encoding, reused, not reinvented), `dispatch`
  header lines and an `evidence { }` section, and `frameprog.block_model` rebuilds
  the committed model the text came from — the trace, **80% of per-tune work
  against `build_all`'s 0.01s**, is not repeated. `tools/_sweep.py` caches on it,
  keyed on the mutated image, the build parameters and a **content fingerprint of
  every package source file** — not a maintained constant, which is the failure
  mode a version bump forgets; unknown keys recompute, never partial-match, and
  `DI_SWEEP_CACHE=0` bypasses. The pinning assert whose absence let the gap exist
  is `tests/test_frameprog.py`, over the fuzz-player family: re-emitted text,
  walker log and Gate FP verdict all equal. **The byte-identity baseline resets
  here** — the artifact gained sections, so identity of *statements* and of *gate
  verdicts* is the standing gate and every later phase re-inherits byte identity
  from the new hashes. Three corpus-forced corrections. (1) The brief's claim
  that `reads` has no consumer in the frameprog path is **false**:
  `datadecl.declarations` consumes it (`datadecl.py:404`), and a rebuild that
  omits it re-declares differently — measured on `Agent_X_II`, which drops two
  `state { }` fields. It is carried, and it is the largest channel. (2)
  `initcopy.reduce`'s inputs are two orders larger than its output
  (`Ghouls_n_Ghosts`: 7,159 traced cells, 6,999 of them undeclared, against 40
  surviving records), and both its other arguments are already in the artifact,
  so what is serialized is the **projection**, not the tracer. (3) A latent defect
  surfaced and is **not** fixed here: `_declare_cells` carves loose cells after
  `_state_fields` has run, so a cell can be declared twice — once in `state { }`
  and once in `data { }` (`Agent_X_II` `$6923`/`$6925`). Correcting it moves
  gate-visible text, which 3a may not do. Queue: **2c -> 3a -> 2.5 -> 3b**; 3b
  deprecates sidprog and owes `tests/test_soundness.py:403` a frameprog
  equivalent before that test retires.

  3a's gates, all full-corpus against a post-2c (`daa31a5`) baseline measured on
  the same host, and all run twice more cache-cold and cache-warm:

  | gate | verdict |
  |---|---|
  | `lift_residue` | 624 rows **identical**, census sum **30,854** unchanged |
  | `fuse_measure` | 624 rows **identical** |
  | `storage_census --frames 1500` | 624 rows **identical** |
  | `gate_sweep`, full Songlengths | 622 built / **621 clean**, identical tune for tune: one divergence (`Rambo_First_Blood_Part_II`) and two refusals (`C64_World`, `1st_Decent_Hardcore`), all three standing §4 exclusions |
  | `dumps(loads(t)) == t` | **0 failures** over 1,203 artifacts |
  | `dumps(program(block_model(loads(t)))) == t` | **0 failures** over the same 1,203 — totality on the corpus, not only on the fixture |
  | cache-cold vs cache-warm | 624 rows identical in all four sweeps |
  | suite | 2,528 passed / 492 skipped / 24 xfailed, `-n 24`, both passes |

  **The new byte-identity baseline** is the sha256 of each tune's canonical
  frameprog text at full Songlengths length; over the 624 built tunes their
  aggregate is `99d4fdec3da1107bf950a57f6a655d8109a475cca5888f1a59bf9c9b1689a942`
  (`Commando` `7f564cf0b099ba5a…`, `Ghouls_n_Ghosts` `2fa177fa165868da…`,
  `Comic_Bakery` `5bcb8107f0054cfd…`). Speedup, `-j 22` on 24 cores:
  `lift_residue` **264s bypassed -> 104s warm (2.5x)**, `fuse_measure` 257 -> 109,
  `gate_sweep` 386 -> 231 (the gate evaluation, not the trace, is what is left),
  `storage_census --frames 1500` 155 -> 134 (a 1,500-frame trace was never the
  cost). The first populating run pays ~11% to emit and store; the whole corpus
  is 18 MB over 1,203 artifacts.

- **Post-3a, four items the phases did not own (2026-08-09).**
  (i) **R8 amended in place**: its bullet still specified the forward worklist
  2c had already withdrawn, so a Phase 3 agent reading it would have rebuilt
  the analysis that diverged eight tunes. The in-edge map is now stated as
  that phase's precondition and Phase 2.5 owes the number.
  (ii) **The framing gained §2.1** (docs/register-model-lift.md): read forward,
  the persistent half is four roles — cursor, accumulator, counter, parameter
  — whose vocabulary is closed by the chip's parameter count rather than by
  enumeration of spellings, which is the closure argument the ladder lacked.
  It reorganises what P1/P2 and Phase 7 are rather than adding work, licenses
  nothing on its own (elimination needs a complete recognizer; the static
  proof stays the license), and is unmeasured pending its classifier. It also
  reads R3's scratch fractions as an architecture axis — table-per-frame
  (Commando: 4 of 26 persist) against event-script (Ghouls: 68 of 114) —
  which predicts P1/Phase 3 paying at one end and Phase 7 at the other.
  (iii) **Phase 7 (P4, §10) landed inside `daa31a5`** by squash rather than as
  its own commit, so no prior entry records its arrival; the standing
  governance violation it discharges is `follin_script._ARITY`, a hand-written
  per-tune opcode table, named as a debt at both the doc and the module.
  (iv) **`out/*.eqlift.txt` are committed artifacts last regenerated at PR
  #51 and are stale** against current output (found by 3a's smoke run, whose
  regeneration was reverted rather than folded in). They want refreshing or
  untracking; no phase owns them.

- **Value-walker-first (2026-08-09).** Four phases were queued behind one
  missing analysis — Phase 3's `aliased`, R1's `loc_unresolved`, G2
  (`g2_boundable`, a class already *named* as boundable with nothing able to
  prove the bound), and both of Phase 6's customers — and the tree already
  carried five partial value analyses, each written for one shape, 2c's
  `_off_page` being the fifth and the one that made the pattern visible. Two
  corrections to the arc follow. First, the walker was priced over mutable
  memory (proposal §4 P1) and the blocking specimens do not need it: every one
  bounds a **local** from control flow, so a locals-only strided interval with
  memory at ⊤ is the instrument, and whether that suffices is a measurement,
  not a judgement. Second, 2c's withdrawal reassigns the structural
  deliverable: not a traversal to extract, but **the in-edge map the statement
  list is not**, which is also R8's precondition. Inserted after 3a as an
  analysis-only instrument in the 2a mould — no license, no text change,
  byte-identity against 3a's reset baseline as its gate. The standing lesson
  from b3 applies to its own framing: a rule stated over one spelling is a rule
  about the emitter — the verdict classes exist so the next phase reads its
  prize off the breakdown rather than off this entry's expectation.
- **Post-2.5 (2026-08-09).** The walker landed as `tools/value_walk.py` with the
  in-edge map, and the map is the finding. **It closes on 6,350 of 7,278 labels
  and in 584 of 624 tunes**, and the reason it did not close at first is an edge
  kind neither 2c's withdrawal nor R8's amendment names: **a raw `call` whose
  target is a label of the calling list itself**. ASL/04 does its three
  per-voice passes as `JSR $1040`/`JSR $103F` into `$103F`/`$1040` rather than
  as a loop, and the corpus carries 3,185 such edges; the `goto`-only map claimed
  one index where three arrive, and §3's differential guard read it off the run
  as **334 contradictions over 81 tunes** before any prose did — the second phase
  running to the guard rather than to an argument. With the call edges the guard
  is clean over 41,375 address sites. Two further corrections are about spellings
  again: `loc_unresolved` is not a third wide-store shape but G2's own add with
  its address bound to a temporary (all 33 bounded, so G2's class is 65 of 65 and
  R1's residue is 38 write-throughs), and the census `mod_addr` signature counts
  741 modular *loads* in the *inline* spelling against a real population of 2,027
  accesses of which **927 are the stores Phase 3 (ii) is about**. The scope
  question the phase existed to answer is settled and the answer is split:
  **memory value-sets are the binding premise for `extent_unmappable` (282 of 330
  `foreign` webs, blocked by a memory read and nothing else) and for no other
  customer.** `web_alias` is 1 bounded of 287 with the back edge at 155 and the
  call graph at 64 against memory's 23, so Phase 6's flagship specimen is
  mis-diagnosed: bounding ASL/04's X needs a callee summary over a self-entering
  call, not a value-set over memory. The walker takes ⊤ at every loop back edge
  by construction, which is the largest single ⊤ class in three of the four
  customers and is the next instrument, not this one. Phases 4 and 5 are not
  priced and this entry claims nothing about them.

## 7. Briefing a subagent to execute a phase

b3 was executed from a written brief, and it measured the brief as well as the
rule: what the brief carried worked, and what it omitted cost **two hours of
wall clock across three abandoned suite runs**. Both halves are recorded here
so the next phase starts from the corrected form.

**What a brief MUST carry about the work** — b3's half that held:

- **The phase's bullet quoted verbatim**, never paraphrased. b3's headline
  correction — the fused `block_read` the bullet told it to key on is 2
  definitions against 126 byte-lane ones — is a finding *about the bullet*,
  and it was only available because the agent had the exact words to test the
  corpus against. A paraphrase absorbs that error silently.
- **The claims discipline** (this document's opening section) and
  `tools/disasm_tune.py`: no unliftable or uncertifiable claim without the
  disassembly and a fixture.
- **§5.4's fixture index as the executable spec**, named test by named test,
  with the rule that landing flips the marker in the same change. Point at
  the fixtures; never at this document's narrative.
- **The gates as full-corpus, and "do not report a gate you did not run"**
  stated outright. Sampled verification is not verification (§3).
- **This document as part of the deliverable**: the before/after table, the
  refusal ledger, the corpus-forced corrections, the §6 entry.
- **That corrections are the valuable output**, not a failure to confess.
  Every phase so far produced at least one; b3 produced six.

**What a brief MUST carry about running it** — the half b3's brief omitted:

- **The suite runs in parallel.** `pytest-xdist` is in `.venv`, and
  `.github/workflows/ci.yml` runs two passes, `-m "not oracle"` and
  `-m oracle`. Measured at b3: `-n 24` completes both in **3m46s** (2,468
  passed / 490 skipped / 25 xfailed, then 14 passed). Serially the suite did
  not finish in 41 minutes. **`-n auto` is wrong here** — 72 workers hit a
  fork limit in this container; `-n 24` is the stable setting.
- **The sweeps take `-j`/`--procs`** (`gate_sweep`, `storage_census`,
  `lift_residue`, `fuse_measure`) and default to 32.
- **The usage traps the previous phase found.** Currently: `gate_sweep
  --extents` MUST be given the `--frames full` artifact (§2 2b (b3)
  correction 6), since the 1,500-frame artifact describes a different model
  and fabricates five `extent` faults; `out/ptr_extents*.json` is **not** a
  standing artifact and the copy in a working tree is whatever the last phase
  left there, so regenerate it (`storage_census --frames full --extents ...`)
  before reading any `foreign`/`short` split off it (2.5 found the in-tree copy
  three phases stale).
- **The byte-identity aggregate is reproducible and its recipe is not written
  down anywhere else**: per tune, `sha256` of
  `frameprog.dumps(frameprog.program(model))` at full Songlengths length; the
  aggregate is `sha256` of `"".join("%s %s\n" % (tune_id, sha))` over the built
  tunes **sorted by tune id**. 2.5 recovered it by trying recipes against the
  three per-tune digests §6 quotes; a phase that cannot reproduce the aggregate
  cannot report the gate.
- **An analysis-only instrument belongs in `tools/`** (Phase 0's
  `storage_census.py` precedent): 3a's decompile cache fingerprints
  `deity_informant/*.py`, so a package file touched for a measurement invalidates
  1,203 artifacts and costs every sweep a cache-cold pass. 2.5 changed no package
  file, so byte identity held by construction as well as by measurement.
