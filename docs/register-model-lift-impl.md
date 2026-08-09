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

Status: in execution. **Phases 0, 1, 2a and the whole of 2b (b0–b5) are DONE**;
2c and 3 are next (both depend on 2a's bounds only). 2b's rewrite lifted
**251 of 325 webs over 116 tunes, retiring 1,000 ⊤ loads**; the whole 74-web
residue is `web_unnamed` — rung (d)'s read-side refusal, pinned by the
`dual_store_*` fixture family. b3's static enumeration then took block-rooting
from **441 to 480 roots** and certified **99 of the 103 extents it claims**
against b0's observed run; what it does not reach is the registry's coverage of
computed rows (`extent_unmappable`, 399 webs, now **Phase 6's** to answer,
pinned by `computed_rows`). **Zero webs in the measured residue are unliftable
in principle** (§6, byte-residue challenge). "MUST" is a gate. §5 records the
prototypes and the shredder; §6 is the decision log.

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
remaining 54 tunes are three shapes:

| shape | stores | tunes | disposition |
|---|---:|---:|---|
| `(zext2(reg) + $00NN):2`, true bound ≤ `$01FF` | ~20 | 15 | G2's `INT_ADD` carry rule bounds them (`g2_store` fixture) |
| a store *through* a sequence pointer | ~50 | 12 | write-through players; Phase 2's certification |
| a bare `t0:2` local G1 cannot resolve at that seat | ~31 | 30 | resolve-strengthening or per-tune refusal |

The two worst carriers (`C64_World`, `1st_Decent_Hardcore`, 14 apiece) are
§7.10.3's worst `unnamed` carriers: one defect wearing two counters.

## 1. Ambiguities resolved up front

Measured answers, not judgment calls. The journey to each is in git history;
only the answers bind.

**R1 — Can a store clobber a promoted cell through an alias?** No for 91.3%
of tunes; the rest are the three shapes above. R1 is not an assumption: the
promotion pass recomputes the sweep per build and refuses `wide_store` per
cell, ledgered per tune.

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
classes; Phase 4 is where they leave). Post-Phase-1 cap: while 298 tunes wear
refused `sp` fabric the headline is capped at **326 of 624**; Phase 2c owns
releasing the cap.

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
spelling, 2c finishes the stack, 3 promotes frame-local scratch, 4 coalesces
byte columns, 5 retires the boundary read-back, 6 re-measures. Every phase
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
balance analysis. Both rules moved into **Phase 2c** (they are dependents of
2a's bounds). Two consequences owned by their phases: R7's cap (326/624,
2c's to release), and the `store_reach` interval-from-zero defect that would
have forfeited a third of Phase 3's yield (the floor is `frameproc
.addr_floor`, landed in 2a; `sp_scratch_floor` fixture pins the promise).

Shredder: `sp_spill` flipped to a hard pass at landing (marker removed in
the same change); `sp_unbalanced` stands as the invariant.

### Phase 2 — sequence traffic becomes table cursors

The keystone, split so analysis lands before dialect: 2a certification
(DONE), 2b annotation + rewrite (DONE, b0–b5), 2c the stack endgame
(scheduled).

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

**What it supplies**: 2c's `sp_linked` premise ("an access certified into a
declared extent is bounded off page one") — half of Phase 1's 611-tune
unprovability was the missing `addr_floor`, not the missing certification;
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
  stack** — else `low_held`, re-enters after 2c (`low_held_cursor`
  fixture). Everything 2a called a *certification* refusal stops blocking:
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

#### 2c — the stack fabric leaves (`raw_sp` -> 0, scheduled)

Phase 1's finding made a phase: two thirds of the surviving class is fabric
only `drop_sp` removes, blocked by `sp_linked` (307 tunes) and
`sp_unbalanced` (201). Two rules, premises supplied by 2a and Phase 1:

- **The `sp_linked` relaxation.** Sound form: a raw `call`'s linkage may be
  dropped where the program makes no surviving page-one access other than
  through `sp` itself — computed per tune from 2a's records (an access
  certified into a declared extent is bounded off page one) plus
  `addr_floor`. Refusal stays `sp_linked` where an uncertified access
  survives. Direct prize measured at 71 tunes / 217 sites; the real yield is
  the compound with the balance rule, re-measured at landing.
- **The balance fixpoint.** `_sp_state` currently demands the entry
  displacement at every label, `goto`, `ret` and loop edge; the rule becomes
  a worklist fixpoint over those edges, so a procedure whose displacement
  provably returns to entry on every path balances even with an interior
  nonzero-displacement label. `sp_fix_balance` is the xfail; `sp_unbalanced`
  remains the invariant for genuine imbalance.

Gates: full `gate_sweep`, no verdict regression; census `raw_sp` monotone
down, sum down; the ledger MUST NOT grow without a rule change; R7's cap
(326) is this phase's number to release. `low_held_cursor` re-enters 2b's
work list when the fabric leaves (its xfail names this phase).

### Phase 3 — frame-local promotion (the scratch elimination)

Mechanical rule, both conditions computed by committed analyses:

- **(i) not live-in at the frame boundary**: a **forward** written-before-
  read analysis over the emitted dialect from `play` entry — across calls,
  joining over `if`/`loop`/`for`/`switch goto` arms and labels via worklist,
  `unobserved` as a terminator (R8; `dispatch_scratch` is the xfail). The
  `wall` refusal covers only raw dyn forms, re-sized at entry.
- **(ii) unthreatened**: a per-cell interval test — a cell is threatened by
  any store whose reach interval it intersects: uncertified wide stores
  (`wide_store`), in-bounds computed stores (`zp,X` modular stores clobber
  zero page; census `mod_addr` 741 / 91 tunes), and certified cursor write
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
the `zp_FA` pair — precisely the bound a value-set walker reads. Also
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
| `sp_unbalanced` | invariant | an unproven stack effect keeps `sp` and its stack page |

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
| `low_held_cursor` | invariant + xfail 2c | the pair held through page one (Angry_Birds `$09F1`) refuses `low_held`, keeps `sp`; re-enters after 2c |
| `alias_web` | invariant | a wrapping `STA $zz,X` with unbounded X (ASL/04 `$128B`) refuses `web_alias`; the machine spelling survives |
| `call_returned_row` | control | a row returned in A/X across a call refuses certification, never the lift |
| `computed_rows` | invariant + xfail **P6** | an arithmetic row off the registry: b1-eligible, `extent_unmappable`, and no block read for b3 to enumerate; only the value-set walker flips it |
| `shift_divide` | xfail P6 | the divide accumulator (Cool_Air `$1447`): `(T2[y]-T1[y]) >> n` as a loop-carried `LSR`/`ROR` — the `>>` exists, the loop rule does not |
| `phase_split_reload` | xfail 2b | the pair's halves reloaded in different frames (Air_on_a_Rasterline `$0C1A`/`$0D05`) — each store is a lane replacement, a masked word update away from fused |
| `dispatch_scratch` | control + xfail P3 | the SMC-operand dispatch emits `switch goto` (no wall); scratch promotes across the join |
| `sp_fix_balance` | xfail 2c | interior label at nonzero displacement, entry-balanced on every path, destacks under the fixpoint |
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
