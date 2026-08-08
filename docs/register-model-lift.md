# register-model-lift — the residue is one machine, not eleven classes (proposal)

Status: proposal. Re-reads the §7.10 triage (docs/frameprog.md) under one framing
and proposes the generalization. Nothing here is landed; every number is quoted
from a committed sweep or marked unsized. The phased implementation plan, with
the risks below resolved by measurement on five family-representative tunes, is
docs/register-model-lift-impl.md.

## 1. The framing

**The complexity of the lift ladder is fidelity to the 6502's register model,
and that fidelity is owed nowhere.** Gate FP's law (§1.1) is about the canonical
frame projection of the SID write log. The observable surface of a frame program
is the byte sequence reaching `$D400`–`$D41C` and nothing else; the interior of
the computation between frame entry and those stores is unobservable by
construction. Yet the IR pays fidelity to the machine's *storage model*
everywhere: memory cells are the primary storage class, values are byte-wide
with explicit `zext2`/`trunc1` seams, the carry is a value, the hardware stack
is memory like any other. Every one of those is a 6502 constraint — three 8-bit
registers, no wide ALU, architectural flags, a fixed 256-byte stack — that the
original programmer was forced to compile *around*, and that the lift is now
reverse-compiling *through*, one spelling at a time.

The machine forced unboundedly many spellings. A ladder that discharges them one
proof per shape cannot converge, and the census says it has not: the remaining
ranked items 3–5 (§7.10.7) have ceilings of 65, 56 and 107 stores — **~230
together** — against a census of **~30,000 machine-shape sites**. The ladder has
been measuring the one class with a metric (`narrow_sink`, 249 sites, the
second-smallest) because that is the class its own rungs were written for.

## 2. The triage re-read: every bucket is a register-model artifact

Map each census signature and triage bucket to the constraint that produced it.
Nothing is left over.

**(a) Three 8-bit registers → memory is the register file.**

| evidence | size | the machine fact behind it |
|---|---:|---|
| `unnamed_addr` | 9257 sites / 604 tunes | scratch cells addressed, not named |
| `mod_addr` | 741 / 91 | zero page indexed as a register array (`zp,X`) |
| `loc_zext` (G1's table) | 320 stores | zero-page stores through a computed byte |
| `state { }` residency (§7.10.13) | 20 of 26 cells frame-local, 3 never written | temporaries wearing memory addresses |

`m_5523` is the type specimen: the busiest cell in `Commando` at 16,524 reads,
and it never once survives a frame. It is a register the CPU did not have,
declared as per-tune state because the IR has no other place to put it.

**(b) No wide ALU → every 16-bit value is two byte columns.**

| evidence | size |
|---|---:|
| `word_pack` (`hi<<8 \| lo` by hand) | 4617 / 556 |
| `hi_byte` + `lo_byte` (a word read as bytes) | 4402 sites |
| `shift_pair` (one shift threaded across two columns) | 169 / 94 |
| `narrow_sink` + the whole rung-(d) subject | 249 |

The blocking matrix already says these are not four classes: **3998 of the 4402
`hi_byte`/`lo_byte` sites read off a `word_pack` site** — one residue, a word
read as bytes because the pack never made it. Item 1 (§7.10.10) measured the
same fact from the other side: merging 132 byte stores *raised* `word_pack`
4583 → 4617, converting one residue into a larger one, visible only to the
census.

**(c) Flags are architectural → the carry is data.**

| evidence | size |
|---|---:|
| `carry_val` (the flag outlived the operation) | 5594 / 550 |
| `flag_bit` (a status bit recomputed as mask-and-compare) | 1649 / 476 |
| `borrow` (a comparison feeding arithmetic) | 892 / 359 |

These are not independent of (b): a carry chain *is* the wide add the machine
could not express, and a borrow chain is the wide compare. `carry_val` is the
largest census class precisely because every multi-precision operation in every
tune leaks one.

**(d) The hardware stack → procedure linkage as memory traffic.**

| evidence | size |
|---|---:|
| `raw_sp` (a procedure did not balance) | 2604 / 323 |
| `loc_stack` (G1's table) | 711 stores |
| `raw_sp -> unnamed_addr` blocking edges | 891 |

G1 (§7.10.3) proved the point without meaning to: 1031 of 1139 "possibly-SID"
unnamed stores were **stack and zero-page traffic** — 90.5% of what bounded the
completeness claim was spill traffic that can never reach the chip, misfiled
because `addr_bits` had no notion of a storage class.

**The walker's own defects are the same evidence.** The last three correctness
fixes on this branch — the stale `_Jumps` cache (§7.10.6), the `for`-loop
liveness hole (§7.10.9), the width-blind `_use_count` (§7.10.14/15) — all sit in
the bespoke machinery (`frameproc.py`, 3102 lines) that re-derives, per query,
facts an SSA form over variables would carry by construction: which definition
reaches, what is live, what is a use. The complexity concentrates exactly where
the fidelity is.

**And §7.10.12 is the framing's sharpest symptom.** `_widen` reads back a
write-only register because the IR offers no storage class for "the driver's own
shadow of the chip" except a memory address — so the held lane, which is real
driver state, got spelled as a load no 6502 can perform. The defect is not in
`_widen`; it is that the IR's only noun is the cell.

## 3. The design error, named

The ladder generalizes **per shape**: rung (d) for the unindexed lane pair,
`hi-first` for the store order, `_counter_range` for the covering sweep, G1 for
the one-assignment local, G2 (proposed) for the `INT_ADD` bound, the value-set
fixpoint (proposed) for the multi-definition index. Each rung is a bespoke proof
that one byte-level spelling equals one wide operation, and each was correct and
worth its cost — item 1 deleted a premise, item 2 deleted a false floor. But the
spelling population is the image of every 6502 code generator and every
hand-optimizer in the corpus, and it does not shrink by enumeration.

The generalization is **per storage class**: decide once, per cell, what kind of
thing it is — spill slot, frame-local scratch, per-tune state, data table,
hardware boundary — and let that verdict license every rewrite touching it. That
is one analysis and one proof obligation per *class*, not per shape, and the
engine for it already exists in-tree: the unified value+memory e-graph
(docs/eqlift-adoption.md §2), whose root-extraction-from-observable-sinks rule
is precisely "fidelity is owed at the boundary and nowhere else", stated as a
mechanism.

## 4. The proposal: three promotions

### P1. Scratch elimination — mem2reg at two boundaries

Classify every RAM cell the play routine touches by **observability**, not by
address shape:

1. **Stack cells** (`$0100`–`$01FF`): provably unobservable already — G1's own
   lattice rules them out on sight (`addr_bits = $01FF`). Every balanced
   push/pop pair is a procedure-local temporary; every unbalanced one
   (`raw_sp`, 2604 sites) is `framestack`'s unfinished work and becomes a
   parameter/return or a refusal, never an address.
2. **Frame-local cells**: a cell that is (i) written before read on every path
   from frame entry — **may-live-in analysis at the frame boundary**, the
   analysis §7.10.13 already names — and (ii) alias-free, i.e. no computed
   address can reach it, which is the e-graph's interval-disjointness relation
   asked per cell instead of per load. Such a cell is a procedure local; it
   leaves `state { }`, its address leaves the population G1 bounds, and its
   traffic leaves the def-walker's crossing-store walls.
3. **Per-tune state**: cells that may carry a value across the interrupt stay
   declared — as the 4-of-26 measurement predicts, this is the tracker's cursor
   set, and it is small.
4. **Data tables and the SID boundary**: unchanged; already classified.

Soundness is the eqlift contract, not a new argument: a cell is promoted iff no
observable sink reads it *as memory* — root extraction with the frame boundary
added to the sink set. The dynamic §7.10.13 counts are an upper bound on the
static verdict and a cross-check, never the license.

**What it dissolves.** `unnamed_addr` (9257) collapses to its genuine-pointer
core; `raw_sp` (2604) goes to zero or to named refusals; `state { }` shrinks to
actual state; and — the compounding effect — **§7.10.5's walls fall with it**,
because "a crossing store may write the index cell" (54 stores) and most
loop-carried rebinding (107) are aliasing questions about scratch, which no
longer exists. The value-set fixpoint (item 5) over SSA variables is a textbook
forward analysis; over mutable memory it was the hard novel machinery its cost
deferred.

### P2. Width recovery as unification, not per-store proof

With scratch promoted to variables, a 16-bit datum is a *pair of variables*
joined by carry chains and packs. Replace the per-store rungs with **column
coalescing over the value graph**: unify `(lo, hi)` pairs whose definitions and
uses are linked by `carry(a,b)` adjacency, `hi<<8|lo` packs, or paired table
strides (`_pair_tables` already finds the data side). Then the admitted QF_BV
rules do the rest with no new proof surface: `carry_fuse` collapses the chain to
one wide add, pack/unpack cancel, a borrow chain becomes a wide subtract, a
`shift_pair` becomes one wide shift. `carry_val`, `flag_bit` and `borrow`
disappear as a **consequence** of widening — they are not their own residue and
should never have been separate classes.

Prerequisite, already named: the **M-FP3 pair-cell dialect**
(eqlift-adoption.md §11) — the grammar must be able to spell a wide local before
extraction can choose one. That is the single blocking dependency of this
proposal and it is spelling, not analysis.

Target: `word_pack` 4617 + `hi/lo_byte` 4402 + `carry_val` 5594 + `flag_bit`
1649 + `borrow` 892 + `shift_pair` 169 ≈ **17,300 sites** — against items 3–5's
combined ceiling of ~230 stores.

### P3. Byte fidelity retreats to the boundary, and is stated there

The facts that are genuinely order- and width-sensitive all live in the store
layer at `$D400`–`$D41C`, and each already has or needs one boundary construct:

- **Write order** is the store's own (`hi-first`, landed, §7.10.10).
- **Covering sweeps** stay byte-wide blits (landed, §7.10.11) — the §7.7 `$CA6E`
  argument is a fact about the log, i.e. about the boundary, and survives
  unchanged.
- **Write-only registers are never read** (§7.10.12): the held lane becomes a
  **declared shadow variable** written alongside the chip store — which is what
  the driver actually maintains — and `_widen`'s read-back form is deleted. This
  settles item 6 generically instead of per-site, and it is only expressible
  once P1 gives the IR a storage class for driver-owned state that is not an
  address.

Everything interior is wide, variable-based, and free; everything at the
boundary states its byte order and width explicitly. Fidelity becomes a property
of one layer instead of a tax on all of them.

## 5. What this subsumes from the ranked list

| §7.10.7 item | disposition under this proposal |
|---|---|
| 3. G2 `INT_ADD` bound | subsumed: a patch to a walker P1 mostly retires; the 65 stores are scratch addressing |
| 4. computed-jump scoping | shrinks: the 56-store wall is a per-procedure kill switch in the same walker; post-P1 the residual is measured before any target-set machinery is built |
| 5. value-set fixpoint | becomes standard: forward analysis over SSA variables, not over mutable memory |
| 6. write-only read-back | settled by P3, generically |
| 7. `state { }` scratch | settled by P1 — the may-live-in analysis is P1's core obligation, built once, not as a side tool |

The `unproven` 217 do not all fall — a genuinely dynamic index keeps the
`sid.reg[i]` byte view, which remains the honest answer — but the walls table
(§7.10.5) says most of them were never about the index.

## 6. The metric, decided

§7.10.8 declined to choose the census rate; this proposal needs it chosen, and
the framing chooses it: **machine-shape sites per tune, from `lift_residue`,
with the headline "tunes wearing zero machine shapes."** The word-store rate
(93.83%) is retired as a headline — item 1 proved it can rise while the artifact
gets *more* machine-shaped. Every step below gates on the census dropping
monotonically and on the Gate FP full-corpus sweep (`tools/gate_sweep.py`,
currently 621/623 clean) holding or improving.

## 7. Order, and what each step owes

0. **Size it first.** Commit the §7.10.13 scratch harness as a tool: per-cell
   storage-class census over the corpus (stack / frame-local / state / table /
   boundary), plus a corpus-wide count of `_widen` read-backs for §7.10.12. Both
   are unsized today; nothing below should start until they are numbers.
1. **Stack promotion** (P1.1). Smallest sound step, already half-proven by G1's
   lattice; target `raw_sp -> 0` with unbalanced sites becoming named refusals.
2. **Frame-local promotion** (P1.2). Needs the static may-live-in analysis and
   the per-cell alias verdict; lands the memory-join model the eqlift plan
   already names as its primary open problem (opaque-reset default, weakened
   per storage class, not per site).
3. **Column coalescing + M-FP3 dialect** (P2). The big one; gated on the census,
   verified by the existing all-sites Z3 proofs plus Gate FP once M-FP2's
   evaluator speaks the dialect.
4. **Boundary shadow variables** (P3). Deletes the read-back form; Gate FP
   cannot see it (§7.10.12), so its gate is structural: zero loads of
   `$D400`–`$D414` anywhere in emitted text, asserted by `lift_residue`.

## 8. Risks, stated against the record

- **Memory joins** (eqlift-adoption §5) move onto the critical path: P1's
  promotion verdict must hold across branch joins and loop heads. Mitigation is
  already the plan's: opaque-reset by default, weakened only by admitted
  argument, all-sites proofs behind it.
- **Text churn.** P1–P3 move emitted text corpus-wide, far past item 1's 213
  tunes. The full gate sweep per step is mandatory, and the §7.10.9 lesson
  applies: sampled verification is how three failures hid.
- **The static/dynamic gap.** §7.10.13's counts are one tune, one subtune, 1500
  of 11,750 frames; the static analysis will promote strictly less. If the gap
  is large the win shrinks honestly — step 0 exists to learn that before the
  machinery is built.
- **Promotion walls remain walls.** A computed jump or `pcall` still blocks the
  cells it may reach; the difference is the wall is paid once per cell at
  classification, not once per query in a backward walk — a cost model change,
  not a soundness change.
- **Saturation cost** over whole procedures with wide values: the 60s
  per-procedure budget stands; bounded schedules are sound at any cutoff because
  every admitted rule is an equivalence.

## 9. What is deliberately not proposed

- No change to Gate FP's law or the canonical projection: the boundary's
  byte-order fidelity is the point of P3, not a casualty of it.
- No per-tune rewrites, per the eqlift governance rule: every finding becomes a
  storage-class rule, an admitted axiom, or an analysis strengthening.
- No deletion of the `sid.reg[i]` byte view: it remains the model's answer for
  a genuinely unresolvable index, and the covering-sweep and ord-section
  arguments keep it load-bearing.
