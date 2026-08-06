# frameprog — the frame-program layer (specification)

frameprog is a **derived** artifact level above sidprog. It drops cycle
exactness: the only normative output is the **canonical frame projection**
of the SID write stream, one record per play-frame. sidprog remains the
cycle-exact ground truth (Gate C unchanged); frameprog is generated from the
committed model and verified against the projection of the walker's log.
Status: design for review; landed already: the projection + digi rule in the
pure log domain (`deity_informant/framelog.py`), the generator and reader
(`frameprog.py`/`frameproc.py`) and the reference evaluator plus Gate FP
(`frameval.py`, §6 M-FP1/M-FP2 for the measured extent), rung (d)'s 16-bit
fusion (`framefuse.py`, §4.3 and §6 M-FP3), rung (f)'s pointer resolution
(`frameptr.py`, §4.4 and §6 M-FP5), the init-copy origin map
(`initcopy.py`, §4.5) and the proven deref's store source (§4.6). "MUST" is a gate. Measurements:
2026-07-25, 140 cached tunes, 1,000-frame windows unless noted; scratch probes,
numbers herein are the record.

## 1. Verification law (Gate FP)

### 1.1 Canonical frame projection

Input: the play-phase write log (SID offsets $00-$1C by runtime address —
projection is by address, never by syntax) with per-frame boundaries (one
play invocation per frame in the v1 class). `framelog.canonical` maps each
frame to one record of 8 sections, in this order:

- per voice `v` in 1..3 (offsets `7(v-1)+r`):
  1. `freq_lo`, `freq_hi`, `pw_lo`, `pw_hi` — each last-write-wins, elided
     when unwritten that frame;
  2. the voice's `ctrl`/`attack_decay`/`sustain_release` writes as one
     **order-preserved** section: every write, original relative order,
     multiples allowed (gate off→on retrigger and hard-restart ADSR
     sequences are frame-audible and MUST survive);
- filter tail $15-$18 (cutoff lo/hi, $D417, $D418) — each last-write-wins,
  elided when unwritten;
- residual $19-$1C writes, order-preserved.

Intermediate values of last-write-wins registers are non-normative at this
level by definition — that is the abstraction. `framelog` is the ONE
projection implementation (§3): `dumps`/`loads` round-trip the record text
exactly, `diff` reports the first divergence.

### 1.2 Input class: the digi exclusion rule

frameprog is class-scoped like v1/v2 in decompiler-implementation.md §1.
$D418 stays in the last-write-wins tail; tunes that encode audio in the
$D418 write sequence are **outside the class**:

- Per frame, collapse consecutive duplicate $D418 volume nibbles
  (`value & $0F`). FP-class iff every frame's collapsed sequence has at most
  2 steps (`framelog.digi_frames` empty): three or more steps is two-plus
  volume-level changes inside one frame — amplitude modulation above the
  frame rate, i.e. sample playback by definition. Exclusion MUST carry the
  diagnostic `digi-class: frame F, $D418 volume sequence [n0 n1 ...]`. No
  tunables beyond this definition.
- A 2-step frame (single in-frame volume step: mute-click, song restart)
  stays in class, reported per tune as the `d418_collapsed` metric.
- Excluded tunes remain fully served by sidprog; the exclusion is a class
  diagnostic at frameprog generation time, never a sidprog build failure.

Measured: 0 of 140 corpus tunes excluded; 17 write $D418 more than once per
frame, all volume-nibble-equal except Aztec_Challenge (one frame `00`,`0F` —
a 2-step restart, in class, 1 `d418_collapsed`). Differing full values are
filter-mode transients (Alternative_World_Games `0F`,`1F` in 956/1000
frames): sub-frame mode/routing transients ($D418 high nibble, $D417) are
declared non-normative — no signal is encoded in their write sequence,
unlike the volume DAC. A known digi tune (e.g. the A_Mind_Is_Born family)
MUST be added to the corpus to exercise the exclusion path.

### 1.3 Volatile inputs: the pinned trace

The v1 walker's volatile model has exactly two classes, and frameprog's
vocabulary is that model's, not a second reading of the hardware:

- **cycle-derived** (`structured._VOL`): $D011/$D012 raster, $D41B osc3, $D41C
  envelope3 — pure functions of the walker's cycle counter. frameprog has no
  cycle counter, so these and only these become **declared nondeterministic
  inputs** `raster()`, `raster_hi()`, `osc3()`, `envelope3()`.
- **constant-0 sources** (`structured._VOL0`): the interrupt-source latches
  $D019 (VIC, write-ack) and $DC0D (CIA, read-clear). Under the v1 per-frame
  driver nothing raises those flags, so both read 0 for the whole run
  (decompiler-implementation.md §8.1) — a constant, not a cycle position. They
  are therefore **neither declared inputs nor state**: not inputs, because a
  constant needs no trace and the walker inlines the read rather than calling
  the pinning hook, so `iota` cannot record one; not state, because their value
  is independent of the image byte and of any prior write. The evaluator reads
  them as the walker does. The read-clear/write-ack latches return with the
  driver cadence of v2 (§5), where a handler can dispatch on who fired.

The projection **pins** them: while projecting the walker's run, every
volatile read is also logged as `iota(f, input, k) = value` — the k-th read
of that input in frame f, valued by the walker's normative cycle formulas.
The reference evaluator resolves the k-th evaluation of an input in frame f
to `iota(f, input, k)`; an undeclared volatile read, or a read past the
trace, faults (guarded-envelope doctrine). There is exactly ONE volatile
model — the walker's — and frameprog never re-derives cycle positions; both
sides of the law consume the same `iota` by construction, so the law is
well-defined. That construction is a *set* equality as much as a value one:
the declared input set is keyed on `structured._VOL` (`frameprog._INPUTS`), so
what the evaluator demands from the trace is exactly what the walker's pinning
hooks can put there. For standalone replay the artifact MAY embed `iota`
(run-length encoded) as an `inputs` section. Measured: 3 of 140 tunes read
any volatile input in play (Atmosphere, Atmosphere_II, Chameleon — osc3
only).

### 1.4 The law

**Gate FP:** for every FP-class corpus tune at full Songlengths length,
`eval_fp(F, state0, iota)` — the frame program under its reference
evaluator, whose output semantics is *buffer the frame's SID writes, flush
one canonical record per frame* — MUST equal
`framelog.canonical(walker_frames)` frame-for-frame, `walker_frames` being
the walker's play-phase log (the Gate-C artifact) cut at frame boundaries.
The law holds at **every** lift rung (§4).

`eval_src` is the same run with **store provenance** recorded: the cell each
SID write loaded its byte from, where the value is one byte load at a pure
address (consts, locals and ops only — no memory read, so the address
re-evaluates with no side effect and consumes no volatile input). It buffers
and flushes exactly as `eval_fp` does, so the projection is byte-identical
and the law is untouched; a consumer uses it to tell a declared-table read
from a computed value. The purity gate has exactly one
exception and it is not a weakening: a deref rung (f) proved to name a single
target block reports **the proof's** address — a constant base plus the pure row
— so no impure expression is ever evaluated twice (§4.6).

Each cell is reported with its **origin** ahead of it — the cell that byte came
from, not merely the cell this value read. Otherwise a driver that stages bytes
in a RAM register mirror and flushes it to the SID (§4.1) hands the consumer a
shadow cell and a dataflow problem, and that step is this level's job. The
evaluator carries a cell → origin map: a non-SID store whose value derives from
exactly one cell records that cell's origin (path-compressed, so chasing is
transitive and crosses frames — a mirror is staged in one frame and flushed in
another); a store deriving from several cells or from none drops the entry,
because its byte is computed rather than copied; and a pushed return byte drops
the stack cells it lands on. Reporting is additive — the cell the value read is
still there — so no reading a consumer had before is taken away.

The map carries through the **locals** as well as through the cells. A driver
stages a byte in a register and stores it a statement later (`a = bank[y]` …
`ram[x] = a`), so a rule reading only the loads inside the store's own value
expression drops the byte exactly where the tree shows no load at all — and the
same rule drops the SID write whose value is that bare register. Every local
therefore carries the cell its byte came from, bound where the local is assigned
and dropped where the assigned value is computed rather than copied (a `for`
range and the frame's accumulator reset drop it; a `pcall` binds each parameter
from the argument's origin in the caller). A store's contributors are its read
cells *and* the origins of the locals whose value it reads; the one-contributor
rule is unchanged over that set, and the read cells still come first, so a
consumer that ranks by position sees what it saw before. The evaluator's hop is
exact where a static one is not: it binds one cell per assignment rather than a
set of candidate definitions.

The map does not **start empty**. `decompile` runs the init routine concretely and
keeps only its flat image, so a byte init copied out of a const table into RAM
scratch arrives here as a cell with no declaration behind it. §4.5 traces those
copies as they happen and seeds `prov` with them, so the same chasing rule crosses
the init/play boundary; everything after that is the play-phase rule unchanged, and
a play store to a staged cell rebinds or drops it exactly as it always did.

The map is also **queryable**, because reporting it alongside SID store sources
names only what a SID store reads. An accumulator's step, bound and rate are
copied out of a table into RAM at note-on and no SID write ever loads those
cells, so the map holds their origins and nothing asks for them. `eval_watch`
takes statements the consumer names off the tree — by identity, since two equal
statements in different procedures are different sites, and a watch the program
never compiles faults rather than silently reporting nothing — and returns, per
frame, one record per execution of each: `(index into the watch list, the cell
stored or None for an assignment, the same source tuple a SID store reports)`. It
is `_derived` over the same `_cells`/`_copy`/`_bind` machinery and no other path,
so a byte the play code computed carries no origin here either. The statement
worth watching is often an assignment: where the arithmetic happens in a register
(`a = ram[x] + step` … `ram[x] = a`) the store's value expression is a bare local
whose origin the one-contributor rule has already dropped.

The granularity is **per execution**, measured rather than chosen. Over the 682
cached tunes at 200 frames (646 decompile, PSID start subtune) the query resolves
an accumulator's step to a declared byte at a non-`mut` offset agreeing with the
snapshot for **27246 pw and 3353 cutoff emits** over 121 tunes, against **1420
and 0** over 13 for the same identification requiring the step to reach a
declaration statically. A per-frame snapshot of the same
map reaches 17324/3353 and an end-of-run snapshot 17111/2564: a staging cell is
re-staged mid-run — a new note-on copies a new step byte, and one statement
serves three voices inside a frame — so any snapshot names the last row written
rather than the row each step came from, and 36% of the pw figure is exactly that
difference.

This is annotation only: no value, no write and no record changes, so `eval_fp`
and Gate FP are unaffected by construction. It does not weaken the #61
invariant either — a declaration's const claim excludes every play-written
record offset (`datadecl._mut_offs`, `mut`), so an origin at a declared offset is
const data and the consumer's `mem0[cell] == value` check keeps its full
strength; at a `mut` offset the check is the only evidence.

## 2. Language domain: SMC-free by construction

**Principle (normative).** At frame level, self-modification is
indistinguishable from state mutation: a patched byte is a variable holding
its byte value, and which code runs next frame is a function of state.
frameprog therefore has **no SMC concept** — no code image, no addresses, no
`code[$XXXX]` or dispatch-cell vocabulary, no live-image reads; any
construct that would need one indicates a **generator bug, not a language
gap**. The domain is exactly: a `state { }` record, declared `inputs`,
immutable `const` tables, and procedures over them (rung (f)'s
`frame(state, in) -> writes` is the terminal shape).

The **generator** (not the language) maps the original's play-phase SMC
mechanically at entry, consuming the committed model's observed-primary
artifact sets (docs/soundness.md: every dispatch/opcode/vector site
serializes exactly its trace-observed set behind a runtime guard):

- **Patched operand bytes** → ordinary state variables; the consuming
  instruction reads the variable where it read the cell.
- **Opcode-toggle cells** → enum state variables whose observed values
  select between the variant behaviors: a plain `switch` with a faulting
  default — an ordinary language feature, not SMC modeling. A single
  observed value still emits the one-arm switch (guard explicit; the
  sidprog opswitch precedent).
- **Dynamic-dispatch / vector cells** → state variables (u16 after §4(d))
  switched over observed target labels, faulting default; labels name the
  observed word values, the variable is ordinary data.
- **Code cells read as data** → reads of the SAME variables. A variable IS
  its byte value, so reads-as-data are just variable reads (Automatas reads
  its own operand bytes back as its voice-state array — under this mapping,
  nothing but a state array). Nothing needs proving: there is no escape
  check and no self-reference-elimination stage; the mapping is total by
  definition.

Refusal boundary (honest): the mapping is total over the model's committed
variant and target sets. An executed play-phase store into code whose cell
is not classified operand/opcode/vector — unbounded play-time code copy —
has no state shape and MUST refuse the tune with a site diagnostic; the
corpus SMC census found every play-phase SMC class state-shaped, zero tunes
refuse. At run time a variant or target outside the observed set hits the
faulting default — the same guarded envelope sidprog serializes. Opcode
variants diverging only in cycle-visible ways are irrelevant here (cycles
are gone): their arms project identically and MAY merge after §4(a),
recorded in the build report.

## 3. Relationship to sidprog

- frameprog is a **dialect of the sidprog language**, not a second language:
  both are defined by the one grammar `deity_informant/sidprog.lark`
  ([grammar.md](grammar.md)) and read by the one parser
  (`deity_informant.grammar`). The dialect delta is expressed in the grammar
  itself — the cycle-annotation productions (`CYC`, `CYCT`, `PENTAG`,
  `code[...]` switch subjects) are absent from the frameprog item alphabet,
  and `state { }`/`inputs { }`, named locals, procedure calls and `for` ranges
  are added; every shared construct is one production used by both.
- A local is a byte unless its name carries the width suffix: `w:2` is a
  16-bit local (`("loc", name, 2)`; the bare `("loc", name)` stays one byte)
  and an assignment whose value is two bytes wide states that width on its
  lvalue. `trunc1(x)`/`trunc2(x)` narrow a value to that width
  (`("op", "COPY", (x,), w)`). Both are frameprog forms a sidprog document
  rejects, exactly as the width suffix and the `*ptr[i]` deref are. They are the
  notation rung (d2) writes 16-bit arithmetic in.
- sidprog is and remains the cycle-exact ground truth and the deliverable of
  the decompiler; frameprog replaces nothing and relaxes nothing below it.
- frameprog is **generated from the committed model** (post commit-phase,
  observed-primary sets), never hand-edited; changes flow from the sidprog
  side and regeneration is mandatory on any model change.
- Exactly ONE projection implementation — `framelog` — serves the
  generator's self-check, the Gate FP harness, and all tooling; a second
  projection is drift by definition and is forbidden.
- Guard semantics carry over: frameprog's faulting switch defaults are the
  sidprog runtime guards under the §2 mapping; certification (static set
  equals observed) stays upstream report metadata, never changing the arms.

## 4. The lift ladder

The §2 entry translation is applied first and is definitional, not a rung.
Ordered rungs (a)-(f) then transform the frame program; each carries a
static premise discharged by proof records (structured.Proof style) and
re-verifies Gate FP. Refusals are per-site/per-pair/per-procedure with a
diagnostic; a tune's artifact records its highest rung; every rung is a
valid, gated artifact.

- **(a) Timing-annotation elimination** (mechanical). Strip `@n`, `@tP`,
  `@x`/`@xi`. Premise: none — outputs are frame-buffered (§1.4), volatile
  reads resolve by `(frame, occurrence)` (§1.3), no construct consumes
  cycles. Switch arms identical after stripping MAY merge (report-noted).
  Gate: FP unchanged.
- **(b) Volatile reads → declared inputs.** Rewrite each constant-address
  cycle-derived load to its input expression; a constant-0 source folds to the
  constant (§1.3), which is why it is not an input. Premise: every load that
  MAY address the volatile range is statically a single volatile cell; a
  computed address unprovably intersecting the volatile set refuses (site
  diagnostic). Gate: FP; the declared input set MUST cover the domain of
  `iota` and admit no address the walker cannot pin. Equality of the two holds
  per *executed* read: `inputs` is the statically referenced set, so a read on
  a path the run never takes is declared and never recorded.
- **(c) In-frame dead-store elimination + canonical write section.** Flush
  semantics is already canonical (§1.4); rung (c) deletes SID stores
  provably non-final for a last-write-wins register (a later write to the
  same register dominates every path to frame end); order-preserved
  registers are never deleted. Premise: the dominance proof per deleted
  store; unprovable keeps the store (harmless — the buffer collapses it).
  Gate: FP.
- **(d) 16-bit fusion** (landed, `deity_informant/framefuse.py`; §4.3 for the
  measurement). Fuse lo/hi state-variable pairs — including the §2 dispatch
  words — and render freq, pulse width and filter cutoff as u16 in the canonical
  section (the projection emits lo,hi adjacent, so the word is the register's
  own shape). Unconditional: there is no switch to leave a pair split. Premise
  per pair:
  provably written/consumed as a word — the datadecl pointer-pair machinery
  (`lo`/`hi` partner attrs) plus the paired-index zip invariant
  (follin-dispatch-study §4), every read using the half only inside
  `lo | hi<<8` shapes. Any lone-half access refuses that pair (stays split;
  per-pair, not per-tune). Gate: FP + a fusion proof record per pair.
- **(d2) 16-bit arithmetic lifting** (landed, `deity_informant/framemath.py`;
  §4.3 and §7.5 for the measurement). A driver maintaining 16-bit state in 8-bit
  registers writes the update twice — the lo lane, then whatever crosses into the
  hi lane — so rung (d) sees two byte stores with statements between them and
  refuses. Rung (d2) reads that pair as the one 16-bit update it is, by one
  definition and no idiom: **two statements jointly update `W = hi<<8 | lo` iff
  the concatenation of the two values they write is a width-2 function of the
  concatenation of the two the cells held.** The two written values are
  concatenated and the admitted rule set is asked what that word is; a site lifts
  where the answer is a term over one lane pack, whatever operator that term uses
  — a shift is `W*2`, a bitwise pair `W & K`, a counter `W+1`, an add `W+step`,
  and one reader takes them all. The **sources** decide the lift — two byte lanes
  at a const base plus one shared index are one 16-bit quantity wherever their
  halves are then written — and every naming the lift emits must still hold where
  it emits it, with no statement it is hoisted past changing an operand (the other
  lane is one such read). The **destinations** decide nothing about the
  lift, only whether the two writes collapse into one `u16` store, which needs
  them adjacent; a hi half stored elsewhere still lifts (the CyberTracker case
  in §5). Which grouping a site *is* comes from the program, not from what
  extraction returned: `_pairs` names the candidate lanes and `_fuse` queries the
  fused e-class for the step each implies (§7.3). A carry written as control flow
  is normalised to a value first (`if c { INC x }` is `x = x + c`, `_predicated`),
  since the condition is a flag and so is 0 or 1. The lemmas are Z3-proven in
  `eqlift.RULES`: the pack is a homomorphism (`carry_fuse`, `carry_fuse0`,
  `borrow_fuse`, `borrow_word`, `band_fuse`/`bor_fuse`/`bxor_fuse`,
  `shl_fuse`/`rol_fuse`/`shr_fuse`/`ror_fuse`, `mask_hoist`), plus the flag
  identities (`carry_comm`, `carry_ult`, `eq_zero`, `carry_ones`, `sbc_borrow`)
  and the algebra (`add_to_sub`, `num_narrow`, `sub_add_cancel`,
  `sub_sub_cancel`). Gate: FP + a proof record per site.
- **(e) Per-voice unification.** Replace k code copies with one procedure
  parameterized by voice `v`. Premise — code isomorphism up to voice index:
  a substitution `sigma_v` maps the voice-1 region tree node-for-node onto
  voice-k's after normalization, every leaf difference being one of: SID
  base `+7(v-1)`; state variable `base + stride*(v-1)` or split-table
  `+offset*(v-1)` (Follin mirror handlers are `+$0F/+$1E`); a per-voice
  constant collected into a declared voice parameter record. Check:
  canonical tree hashes equal after `sigma_v` normalization. ANY residual
  mismatch — extra block, different guard, voice-3 special case — refuses
  the whole procedure; synthesizing `if v == 3` guards is forbidden.
  Index-looped drivers (Hubbard's `sid.v1.*[Y]` voice loop) are already
  parameterized and need no rung-(e) work. Gate: FP + isomorphism record
  (`sigma`, parameter table); unification rate is a reported metric, never
  a gate.
- **(f) The frame-function form.** The §2 domain closed: `state { }` (named
  u8/u16 fields, `[3]` voice arrays), declared `inputs`, const tables,
  `frame(state, in) -> writes`. FP-complete = no raw `mem[expr]` with
  unproven range remains; otherwise the tune rests at its highest rung. §4.2
  names the const-based reads as indexed accesses; what remained raw was
  base-less, pointer-pair derefs above all, and §4.4's pointer resolution
  (`deity_informant/frameptr.py`) takes those: a deref whose every definition
  loads a declared `lo`/`hi` pointer table is `*ptr[i]`, row `i` of one of that
  table's blocks, with the block set proved from the declaration. What stays raw
  after it is the residue §4.4 measures — pairs rung (d) refused, pointers with a
  writer the analysis cannot place, computed pointers and SMC operand words.
  Illustrative excerpt, hand-derived from Commando's decompile (the $52xx
  slide path: state `m_551D[X]`/`ctr_551A[X]`, flags `m_5520[X]`):

```
state { freq: u16[3]  fx: u8[3]  step: u8[3]  ... }
frame(state, in) {
  for v in 0..2 {
    if state.fx[v] != 0 {
      s = zext16(state.fx[v] & $7E)
      if state.fx[v] & $01 == 0 { state.freq[v] = state.freq[v] + s }
      else                      { state.freq[v] = state.freq[v] - s }
      out.freq[v] = state.freq[v]        ; canonical: lo,hi
    }
    ...
  }
}
```

### 4.1 Refused rung: SID-shadow relabelling

Drivers stage a frame's register writes in a RAM mirror and flush it to the SID
(`sid[$D400+i] = B[i]`): Krakout's 25-byte `m_E686`, the per-voice
`m_10B1[v]`/`m_EFC1[7v]` triples of the tracker-era players, Follin's
register-poke command. `movefwd.sid_shadows` detects the idiom — a parallel
indexed flush over a *writable* buffer, so read-only pitch tables are excluded —
and finds one on 146 of the 623 cached tunes.

Relabelling those stores onto the SID (buffer base → flushed SID base, reads
included so the flush reads back what it wrote) is **refused as a rung**, with
the measurement: of the 133 detected tunes that pass Gate FP today, the relabel
leaves 21 passing and breaks 112. Two structural reasons, neither a gap in the
proof machinery:

- **The mirror is state, not a redundant move.** It persists across frames — the
  flush writes every covered register every frame, staged this frame or not.
  Relabelling turns "written with the retained byte" into "elided", a different
  canonical record (§1.1).
- **Order.** A relabelled staging store lands in the order-preserved ctrl/AD/SR
  section at the staging point instead of the flush point, and a
  read-modify-write of the mirror lands there twice. Rung (c)'s rule stands:
  order-preserved registers are never moved and never deleted.

The 21 survivors are exactly the mirrors covering only last-write-wins lanes,
where the buffer collapses; no static premise bounds the covered register set
(the flush index is a loop variable), and there the relabel buys a consumer
nothing, since freq and pw come from the canonical record. Promoting the mirror
statically into an index into its bank — the only artifact-level way to name the
row — is available for about 2% of the emits, because the row a mirror holds is
a run-time quantity. `movefwd` therefore stays **analysis-only**, and what makes
the mirror transparent to a consumer is the origin rule of §1.4, which annotates
rather than moves and so cannot disturb the projection.

**Wrap-offset base normalization** (landed, `eqlift_annotate._const_base`). The
other half of the same diagnosis: a "computed" table base is often a 16-bit
wraparound negative displacement — Krakout reads `mem[idx - $19D7]`, i.e.
`mem[idx + $E629]`. Folding `base - k` as `(base - k) & $FFFF` recovers it. No
new information; the base is in the read expression.

### 4.2 Indexed access: the computed read against its declaration

A computed table read was emitted as address arithmetic — Commando's pitch-table
hi lane read as `sid.v1.freq_hi[w] = mem[(t5 + $5429):2]` — because the indexed
form `base[index]` only carried a *bare name* as index. The index of a real table
read is rarely a bare register: it is the record offset the driver computed
(`zext2((y << 1))` bound to a temporary, a state cell, a lane offset), so every
such read fell back to `mem[expr]` and the consumer had to notice for itself that
`t5 + $5429` lands in the declared `$5428` table.

The grammar's index is therefore **any expression** (`e_index`/`lv_index`,
[grammar.md](grammar.md)): `base[index]` denotes `base + zext2(index)`, `base` is
the canonical cell name of the const the address adds to — a declared table base
or one of its `+cobase` lanes, a state array, a SID register — and the reader
supplies the `zext2`, so the emitter may drop it and the text still round trips.
Nothing else moves: same cell, same value, no new range claim beyond the
declaration's own `observed` extent, and the statement trees `frameprog.program`
hands a consumer are untouched. Commando's read is now
`sid.v1.freq_hi[w9] = m_5429[t5]` — the hi lane of the split pair, named.

Measured (2026-07-30, 682 cached tunes, PSID start subtune, 200-frame windows;
645 emit). Raw `mem[` occurrences in the emitted text: **15086 → 10338** (−31%),
tunes with none at all **6 → 16**; Commando **17 → 5**, the five being the
pointer-pair derefs `(hi<<8|lo) + y`, which have no const base to name. The
canonical fixpoint holds 645/645 and Gate FP 646/646, both unchanged.

What this is *not*: a coverage step. An address *rendering* changes neither the
statement trees nor `frameval.eval_src`, which is what a consumer reads over the
same 682 tunes. Two probes bound the alternatives: admitting impure load addresses to
the provenance rule moves nothing (the addresses are already pure), and chasing
provenance through locals moves `ad` up 446→470 but collapses `ctrl` 402→66 on a
25-tune sample — an extra source cell mis-binds the held row. The residual is a
provenance question, not a naming one.

Soundness (#61). The form makes no const claim: `m_5428[y]` has always named the
address, exactly as `m_1500` names a scalar, and `data { }` remains the only
place a declaration is asserted. Over the first 25 corpus tunes 176 sites newly
render indexed, 135 of them at a base inside a declared table; **6** of those
name a declaration whose span also holds a play-written cell (2 are stores). That
was pre-existing — `_sound_hi` stopped a declaration at the first play-written
cell only *above* the observed read run, so a cell written inside the run did not
truncate it — and the indexed form surfaced it rather than causing it. Closed
since by per-record-offset soundness in `datadecl`: `mut` excludes the written
lane of a record array and the written cell of a flat region from the const
claim, at no cost to the extent (sidprog-language.md, §Data declarations).

The sidprog dialect keeps the register-index form. Its `tN` bindings are expanded
into the tree at parse, so a reader-supplied `zext2` around an already-widened
binding would materialise as `zext2(zext2(t0))` and break the sidprog fixpoint;
the grammar carries the wider form for both dialects, the sidprog emitter does
not use it.

### 4.3 16-bit fusion: what the evidence buys, per pair

Rung (d) is `deity_informant/framefuse.py`. A candidate pair is named by the
committed model, never by shape-matching the text: a **pointer pair** from the
`streams` classifier that `datadecl`'s `lo`/`hi` partner attributes are built on,
or a **dispatch operand word** the paired-index zip closure proved
(follin-dispatch-study §4). The premise is then discharged against the statement
trees — the halves are adjacent cells, every read of a half sits inside a
`lo | hi<<8` shape, and the two half stores are adjacent statements whose second
value provably cannot read the first cell — and each candidate leaves a
`structured.Proof` on `FrameProgram.proofs`, fused or refused, carrying the
evidence, the counts and the refusal. Fusion is notation over two adjacent cells:
`m_0021:2` reads and writes exactly the bytes the two halves did, in the same
order, so **no record can move** and the store provenance `eval_src` reports is
unchanged (a word load at a pure address contributes both of its cells, exactly
as the two byte loads did). A fused pair is one `u16` `state { }` field named off
its `_lo` suffix; the width suffix is the grammar's, now carried by an lvalue and
by the indexed and raw memref forms as well ([grammar.md](grammar.md)).

**Two granularities, one rule.** A *state* pair is a tune-wide declaration, so
one lone-half access anywhere refuses it outright and it stays two `u8` fields —
per pair, never per tune, and the rest of the tune still fuses. A *SID* register
pair declares nothing beyond the two statements it rewrites: freq, pulse and
cutoff are last-write-wins and §1.1 emits lo,hi adjacent whatever order the
driver wrote them in, so its premise is per store site — an adjacent lo/hi pair
at one index fuses (hi-first included; the packed value keeps the driver's
evaluation order), and a lone half elsewhere leaves that site alone.

Measured 2026-07-31, 682 cached tunes, PSID start subtune, 200-frame windows
(650 decompile, 649 reach the gate). **Gate FP 649/649 and the canonical
fixpoint 649/649, both unchanged**, as the argument above requires.

Of the **1296** state-pair candidates the model named — 1238 pointer pairs and 58
dispatch operand words — **584 fuse and 712 refuse**: 518 for a lone-half read,
191 for a half store with no adjacent partner, 2 for the write-order hazard and 1
with no word access at all. Per tune, of 649: **183 fuse every candidate they
have, 97 fuse some and refuse others**, 342 refuse all of them and 27 have no
state pair. Emitted text 9571703 → **9522243** bytes (−0.52%); raw `mem[`
occurrences are **unchanged at 10280**, because what fusion names is the pointer
*word* and the deref it feeds still has no const base for §4.2's indexed form to
name — precisely the residue §4(f) inherits.

**SID fusion is unconditional.** Freq, pulse width and cutoff are 16-bit
registers, the projection emits their halves adjacent, and the frame program is
the deliverable — so the word is the form, and there is no switch that leaves a
proven pair split (there was one, `sid_fusion=`; it is gone). Measured 2026-08-01
on the same corpus and window: **Gate FP 649/649 and the canonical fixpoint
649/649**, unchanged with the rung at full reach.

Of the **2069** SID pairs a store site addresses, 916 fuse every site, 228 fuse
some and leave the rest split, and 925 have no adjacent lo/hi store site at all;
at site granularity 1317 store pairs fuse and 2709 half stores stay bytes.
Emitted text 9649229 → **9660384** bytes (+0.12%) — the packed word is longer
than the two byte stores it replaces, so this rung buys shape, not size — and raw
`mem[` is unchanged at 9682. The 925 are the premise, not a defect of it: a
pre-fusion scan of the same corpus finds about two thirds (577 of the 931 pairs
it sees) write both halves and never as adjacent statements — Commando's slide
path writes `freq_lo`, then the state cell and the carry, then `freq_hi` — and
the rest write one half only. Reaching those is rung (d2) below: not statement
motion for its own sake, but reading the arithmetic that put the halves apart.

**Rung (d2): the arithmetic behind the pair.** What separates the halves is the
driver's own 16-bit add, written twice because the machine is 8-bit — Commando's
slide path is the shape:

```
      t4 = (w13 + idx3)                                  d0:2 = (((zext2(ctr_551A[x]) << $08):2
      m_551D[x] = t4                                              | zext2(w13)):2 + zext2(idx3)):2
      sid.v1.freq_lo[y] = t4                    ->       m_551D[x] = trunc1(d0:2)
      w14 = ctr_551A[x]                                  sid.v1.freq_lo[y]:2 = d0:2
      t6 = (w14 + (carry(w13, idx3) | carry(t4, $00)))   ctr_551A[x] = trunc1((d0:2 >> $08):2)
      ctr_551A[x] = t6
      sid.v1.freq_hi[y] = t6
```

The carry operator is gone, the SID write is one 16-bit store, and the state
lanes are two truncations of one word — `m_551D`/`ctr_551A` are separate tables,
so the *value* is 16 bits where the *memory* cannot be. Where the two lanes are
adjacent cells the word source is a real `:2` load and the two lane stores
collapse to one `u16` store.

The rung does not pattern-match 6502 idioms. `_site` translates the two written
values into the value graph (`eqlift.to_egg`, recording per term every pass-1
node that named it and where), `_fuse` concatenates them as `hi<<8 | lo` and
saturates `eqlift.admitted_rules()`, and `_word_form` reads
`(op, hi lane, lo lane, step, mask)` off the cheapest extraction that has lane
shape. The cases an idiom table would enumerate are rules instead, each
Z3-proven in `tests/test_eqlift.py::test_all_rules_z3_verified`: `add_to_sub`
recovers the subtract `expr` folded into an `INT_ADD` of `-k`, `mask_hoist`
lifts the 12-bit register's `AND #$0F` out of the hi lane, `carry_fuse0` is the
`ADC #0` hi half whose `+ bh` is already folded away, and `num_narrow` narrows
a word constant **only** under the guard `a & $FF00 == 0`. A guarded rule is
proven under exactly the premise the pattern enforces; a width-constrained
`ivar` without a matching `fits` guard would be proven under a premise egglog
never checks.

What a site may emit is a separate question from what it computes. The word
assignment leads the interval, so a naming valid where it was written need not
be valid where the lift emits it: `_back` returns the shallowest naming that
holds at both points and refuses a `loc`/`cell`/`load` that has none, rather
than rebuilding a memory read at a position where it was never made.

Measured 2026-08-02, same corpus and window, and **reproducible run to run**:
the whole corpus is bit-identical under two `PYTHONHASHSEED` values (§7.1).
**Gate FP 649/649 and the canonical fixpoint 649/649**, unchanged — the lift
moves no record. **1452 sites lift across 414 tunes** (1211 adds, 241
subtracts): 618 over adjacent cells (181 of them collapsing to one `u16` store)
and 834 over split tables, with **356 carrying a SID pair store onto the lifted
word**. Emitted text **9779304** bytes; raw `mem[` unchanged at 9682.

**76 sites refuse, each with its diagnostic**: 43 where the lo destination may
disturb the hi lane or the step, 16 whose lane address is not a const base plus
index — chiefly the zero-page indexed form `mem[zext2((x - $20))]`, where the
6502's `zp,X` wrap puts the add *inside* the byte so `frameproc._index_of`
(which requires a base ≥ `$100`) cannot name it — 8 whose lanes are indexed
differently, 6 where the lo destination may alias the hi lane, and 3 where an
intervening statement changes an operand. The zero-page form is a naming gap
shared with rung (f), not a soundness one: widening the splitter must also
refuse the `$FF`→`$00` straddle, since a 16-bit read at `$FF` takes `$0100` and
the `zp,X` wrap does not apply to the word access.

A form the rules prove equal is not thereby the form the *program* wrote. Two
choices are made off the fused e-class rather than read off whichever term
extraction returned. `_site` weighs **every** form `_fuse` offers and prefers
the grouping whose lanes are the two statements' own cells (`_rmw`), then one
over adjacent lanes, then the cheapest — lane *shape* is not lane *identity*,
and a step table wears the shape too (§7.3). `EQ.canon` collapses `x - K` and
`x + (2**w - K)`, one function spelled two ways, onto the spelling pass-1 uses
for an indexed address, so a provenance lookup can still name it.

Two budgets bound the search, both sound at any cutoff because extraction is:
`_NODES` caps the e-graph and `_TERM` caps the translated term, since `to_egg`
follows a local's definition at every distinct read point. `docs/eqlift-adoption.md`
§10 predicted this blowup and prescribed exactly this mitigation. The budget is
checked **every** iteration (`_STEP = 1`): allocation happens inside egglog's
`run`, so a batched step is unbounded however small the node budget is — a
5-iteration batch took one corpus worker to 44 GB and an OOM kill.

What dominated this rung was neither budget but **`str()`**. Reading an extracted
term back means `EQ.canon(EQ._parse_ir(str(x)))`, and `str()` on an egglog
expression pretty-prints through **Black** at ~16ms a term: 62.2s of 103.7s on
`Arpeggio`, against 3.5s of saturation and 4.9s of extraction. `_parse_ir` parses
that string with `ast`, which is indifferent to formatting, so the pass was pure
waste; `eqlift._Unformatted` swaps it off the printer's module reference (not off
`black`, so nothing else in the process changes) and `Arpeggio` goes **103.7s ->
22.0s**. Asking about more pairs is what a principled query costs, and it is
affordable; it was never what the profile was showing.

### 4.4 Pointer resolution: the deref against the table it is reloaded from

Rung (f) is `deity_informant/frameptr.py`. A base-less deref `mem[P + i]` has no
const base for §4.2 to name, but `P` is not an observation: the pointer state
field is *reloaded* from a table of pointers `datadecl` already declared with its
`lo`/`hi` partner attributes. Where every definition of the field is such a read,
the address is `T[k] + i` — row `i` of whichever block entry `k` names — and both
levels are named at once: `a = mem[(ptr_005D:2 + zext2(y)):2]` becomes
`a = *ptr_005D[y]`, with the block set on the proof record.

The premise is discharged **per deref site**, keyed on the address expression
(same pointer, same index shape = same verdict, so two textual sites of one
address are one record), against the statement trees and the declarations only:

1. **Every** definition of the pointer word in the play code is a `lo`/`hi`
   partner-table entry read at one index — `framefuse.unpack` splits the fused
   word store, `frameproc._index_of` names each half's base, the two must read
   the same entry, and the two declarations must carry `lo T'` / `hi T` at the
   same offset. A definition that is not that shape — an advance `P = P + n`, a
   computed pointer, a store from a non-const source — refuses the site.
2. The value set `{T[k]}` is read out of `prog.mem0` at the **declared** extent
   (`min(size_lo, size_hi, index bound + 1)` entries from the definition's own
   offset), never from the trace, and a table with any `mut` offset refuses:
   `mut` is exactly the play-written lane the const claim excludes (#61).
3. The row index's range is bounded by one byte (`streams._idx_hi` over the
   frameprog local alphabet: a local is a byte unless some assignment gives it a
   16-bit value). A wider bound is not a row and refuses.
4. No other store may reach the pair. A store's span is its const address, its
   declared-base index span, the stack page (`sp | $0100` lies in
   `[$0100, $01FF]`), or the union over a local address's assignments; a store
   whose address the analysis cannot place at all refuses **every** pointer in
   that tune, which is the coarsest rule here and the second-largest refusal
   class below.

**An advance refuses, and it falls out of premise 1 rather than needing a rule of
its own.** An advanced pointer is `T[k] + n` for an `n` accumulated across frames
with no static bound, so neither the (block, row) pair nor a range claim survives
it; the reload-only pointer keeps both. The corpus cost of that choice is 168
sites (below), and it is the honest one: `P = P + n` is not a table read.

Like §4.2 this is **naming, not rewriting**. `apply_rung` returns the set of
resolved addresses and one `structured.Proof` per site; the statement trees, the
values and the store provenance are untouched, so Gate FP cannot move by
construction — and did not. The grammar carries `*base[index]` (and
`*base` for row zero) as its own production ([grammar.md](grammar.md)), so the
text distinguishes a proven deref from an unproven one: a refused site keeps its
raw `mem[...]` and its diagnostic.

Measured 2026-07-31, 682 cached tunes, PSID start subtune, 200-frame windows (650
decompile, 649 reach the gate). **Gate FP 649/649 and the canonical fixpoint
649/649**, both unchanged, with zero tunes moving in either direction. Raw `mem[`
occurrences **10280 → 9682** (−598, the 598 textual deref reads the rung names);
tunes with none at all **17 → 51**; Commando reaches zero (its five pointer
derefs were the whole residue). Emitted text grows 9522243 → 9608342 bytes, but
96701 of that is the two new header comment lines (149 B × 649): the body is
**10602 bytes smaller**.

The census is the point, and it is modest. Of **3929** distinct deref addresses
over 628 of the 649 tunes, **366 resolve (9.3%)** and 3563 refuse; by pointer,
**196 resolve and 1037 refuse**. Per tune: **95 resolve every deref site they
have, 67 resolve some**, 466 resolve none and 21 have no deref at all. The
refusal histogram, which is where the work is:

| refusal | sites |
|---|---|
| the lo/hi pair did not fuse (rung d) | 2507 |
| a store at an unproven address may write the pointer | 583 |
| the halves are not a declared lo/hi partner pair | 175 |
| a definition is not a partner-table entry read (advance, computed) | 168 |
| the reload table is not declared | 74 |
| the row index bound exceeds one row | 52 |
| another store's span may write the pointer | 4 |

Two thirds of the residue is **rung (d)'s**, not this rung's: 712 of 1296 state
pairs refuse fusion (§4.3), and an unfused pointer has no word to resolve. The
next 583 are one coarse rule — a single store the analysis cannot place voids
every pointer in that tune. Probing the shapes behind it found stack pushes
(`sp | $0100`) and address temporaries (`mem[t11] = a`), both now proven spans;
what is left is genuinely computed. Raising either number is upstream work, not a
loosening here.

**The structural finding.** For each resolved deref the artifact now knows the
triple (table `T`, entry index `k`, row `i`). Across the 366 sites the target set
averages **10.06 blocks** (max 82); 46 sites name a single block — a pointer the
play code never rewrites, i.e. a compile-time constant — and **320 range over two
or more**. The reload index is non-constant at **366 of 366** (the table is
indexed by a run-time quantity: 428 of the 519 table references index by a
machine register, 80 by an expression, 11 by a named cell), and the row index is
textually distinct from it at **362**. That is the two-level shape an orderlist
and its patterns have: one counter selects the block, another walks the rows.
Requiring in addition that every target block lands inside a `datadecl` region —
i.e. the blocks really are carved song data, not a pointer into code — leaves
**180** sites, and all four conditions together (≥2 blocks, indexed reload,
distinct row index, every block declared) hold at **134 sites over 84 tunes**.
Commando is the clean case: `ptr_005D` ranges over 3 blocks all declared,
`ptr_005F` over 20 of which 4 are declared at 200 frames (the rest are never
reached in the window, so `datadecl` carved no region there — an extent question,
not a resolution one).

What this evidence supports is **not implemented here**: rung (f) hands a
consumer a candidate orderlist per resolved pointer — the declared pointer table
`T` is the orderlist's row set, its entry index `k` is the position, the target
block is the pattern base, and the deref row index `i` is the pattern row. 84
tunes carry that shape today. A reader would take `FrameProgram.proofs` filtered
to `kind == "deref"` and `status == "resolved"`, group the sites by pointer, and
read (pattern table, pattern base set, row index) straight off the record rather
than searching banks. The honest caveat is the 84: this is evidence for a layer,
not a layer, and 466 tunes still resolve nothing — the ceiling is rung (d)'s
fusion rate and the wild-store rule, in that order.

### 4.5 The init phase's copies, named

`Model.mem0` is init run concretely and snapshotted. A driver that stages a
parameter at init — copying bytes out of a const table into RAM the play code then
reads back — leaves the bytes in that image but not their origin, so the play-phase
map of §1.4 can only ever see an undeclared RAM cell. `deity_informant/initcopy.py`
records the origin *while init runs*, and it is traced dataflow, never a value
match: a search that picks between equal bytes explains no index.

**The transfer is static, the addresses are the machine's.** Each lifted record
carries a one-off `transfer(rec)` derived from its P-Code: walking the ops in order
over a per-record environment, a `LOAD` binds its output to "the k-th load", `COPY`
between one-byte varnodes passes a binding on, and **every other op kills it** —
arithmetic is not a copy, so `mem[a] + 1` names nothing. What survives is a tuple of
`(register, source)` updates for A/X/Y and one source per `STORE`. At run time the
tracer resolves a source against the addresses that record's own loads and stores
used, path-compressed through the map so a copy of a copy names the table cell and
not the intermediate. The transfer is cached on the record and a record moving no
byte at all (a branch, a compare, a flag op) is `False` and skipped, so the cost is
O(stores) with small constants; capture is swapped in for init only
(`_EvidenceVM.capture`), so the play trace pays nothing. Measured over a 40-tune
sample single-process: **16.79/17.12 s with, 17.39/17.42 s without** — no
measurable decompile cost.

**Per cell, and refused per cell.** Last write wins, because that is the byte
`mem0` holds and the play phase reads; a cell staged twice from different origins
is counted (`conflict`) and keeps the last. A write whose value the record computed
drops the cell. A stack cell is never an origin and never gets one — `jsr`/`rts`
traffic bypasses the P-Code store, the same reason `Model.written` forces that page
mutable. Each init store *site* leaves one `structured.Proof`
(`kind == "init-copy"`) carrying the cells it staged and, in its lemma, the two
refusal counts.

**The origin must be const data, and that is where `mut` is consumed.**
`initcopy.reduce` keeps a traced origin only where it lands inside a `datadecl`
declaration at a record offset the declaration does not name `mut` — the #61 const
claim, unchanged: `mut` is exactly the lane the play phase writes, so a byte staged
from one is not a const read however well `mem0` agrees. The *destination* needs no
rule: `prov` is dynamic, so the first play-phase store to a staged cell rebinds or
drops it by the one-contributor rule and an init origin cannot outlive it (the
census reports how many staged cells the play phase writes at all). The filter is
measured from both sides — reporting *every* traced origin instead costs
**753274 → 749963** interpreted emits, because an undeclared cell reported ahead of
the read cell displaces the lane the classification picked (the mis-bind §4.2
measures from the other side).

**The census** (2026-07-31, 682 cached tunes, PSID start subtune, 200-frame
windows; 650 decompile, 649 reach the gate). Init writes **179984** cells over
**385512** executed stores, a mean of 277 cells per tune, and **124628** of those
cells (69%) end with a proven traced copy origin. Then the declaration filter bites:

| the init-written cell | cells |
|---|---|
| staged from a cell no declaration names, or a `mut` offset | 122782 |
| computed — no traced load reaches the store | 54986 |
| a stack cell | 370 |
| **a declared const byte at a non-`mut` offset (kept)** | **1846** |

with 491 cells re-staged during init from a different origin (last kept) and 1005
of the 1846 also written by the play phase, where the dynamic map supersedes them.

**What it buys, and the honest finding.** Gate FP **649/649** and the canonical
fixpoint **649/649**, both unchanged; raw `mem[` **9682** and tunes with none
**51**, both unchanged — this is annotation, it rewrites no tree and no value.
What it adds is the 1846 staged cells above, carried across the init/play
boundary with a declared const origin.

**`pw` and `filter` do not move by a single emit**, against the standing
prediction that they would: that a pw sweep's constant delta is "a step the map
traces to no declaration — a parameter filled at init". Of the 114082 pw and
28103 cutoff emits counted in that row, **0 now trace to one**.
The diagnosis was wrong, and the same run says what is actually there — classifying
each refused run by its step pool:

| the refused run's step pool is | pw | cutoff |
|---|---|---|
| a declared cell whose byte is not the step | 49606 | 5144 |
| play-written RAM carrying no declared origin | 47109 | 14004 |
| empty: the byte reaches the store computed | 14376 | 7323 |
| RAM carrying an init-traced origin no declaration names | 1474 | 723 |
| other RAM | 1517 | 909 |

**107785 of the 114082 pw emits (94%) have an accumulator cell the *play* phase
writes**, against 3775 init staged: the step is copied at note-on, not at init, and
that copy the play-phase map of §1.4 already sees. The init-attributable slice is
1474 pw and 723 cutoff emits, 1.3% and 2.6% of their rows.

**One extension is available and is not recommended.** The 122782 refused origins
are overwhelmingly reads of regions `datadecl` never declares, because `datadecl`
carves from *play*-phase reads and a table the driver relocates at init is read
exactly once, by init. Declaring those regions would name them — and the ceiling is
measured: requiring every stepped emit of a refused run to report an init origin
whose snapshot byte is the step reaches **570 pw and 255 cutoff emits**, 0.5% of the
row. That is a change to the declaration set — it moves `data { }`, `state { }`, the
pitch search's candidate pool and the emitted text — for 825 emits, so it is
recorded here rather than taken.

### 4.6 The store's source through a proven deref

`eval_src` reports, per SID write, the cells its byte derives from, and it reports a
load's address only where that address is **pure** (§1.4): re-evaluating an impure one
could consume a volatile input twice and volatile reads resolve by `(frame, occurrence)`
(§1.3), so the report would be paid for with an unsound evaluator. A deref address reads
the pointer word, so it is impure, and `_addrs` recurses into it and reports the
*pointer's own two cells* instead of the target. Every byte a driver reads through a
pointer therefore reaches the consumer with no usable source cell.

Rung (f) already proved what that deref address is: `T[k] + i`, with `{T[k]}` read at the
declared extent and `i` bounded by one row (§4.4). Where that proof names **one** target
block, the address is a constant plus a pure row, so the evaluator can report it without
evaluating anything impure: `frameptr.apply_rung` returns, beside the resolved set, a map
from the deref address to `("const", block) + i`, and `frameval._addrs` reports that in
place of the pointer's cells. **The base is the proof's; only the row evaluates**, exactly
as `m_5429[x]` evaluates today. The pointer's cells are then not reported for that load —
they are the address, not the byte's origin, which is §4.2's own rule (a read the value
only indexes through is not followed) applied at the evaluator. That is the one reading
this takes away, and it is what lets the one-contributor rule bind a staged byte: `a =
*ptr[i]` … `sid = a` carries the block cell through the local.

The proven set is `{T[k]} ∪ {the pointer's own image word}` — the tuple the rung's proof
record has always carried — because a deref reached before the first definition reads what
`mem0` holds. One block is one address; two or more is an **address space** and refuses.
Per site, with a record (`kind == "deref-src"`, one per deref site, resolved or refused,
carrying the block and the address range it claims):

| the provenance record says | sites |
|---|---|
| refused: rung (f) did not resolve the site | 3563 |
| refused: the proof names 2 or more target blocks | 365 |
| refused: the row index reads memory | 0 |
| **resolved: the proof names one block** | **1** |

**One site of 3929, on one tune of 649.** The block counts behind the 365: two blocks at 48
sites, three at 36, four at 68, five to nine at 78 and ten or more at 135 (max 82). §4.4's
census counts 46 sites naming a single *declared* block; the image word is another block at
45 of them, so the sound set is a singleton exactly once.

**Measured** 2026-07-31, 682 cached tunes, PSID start subtune, 200-frame windows (650
decompile, 649 reach the gate). **Gate FP 649/649 and the canonical fixpoint 649/649**,
both unchanged; raw `mem[` **9682**, tunes with none **51** and the emitted text
**9608342** bytes, all unchanged — the rule rewrites no tree, no value and no character of
the artifact, on every one of the 649 tunes.

**What the rule does move, and where it stops.** On the one pinned site — `*m_C0E0[x]` in
`MUSICIANS/B/Bialluch_Dirk/Helden.sid` — **582 SID writes** report the proven address
instead of the pointer's cells, and the address is right: it equals the
address the run read at **582 of 582** writes. All 582 land inside a `datadecl`
declaration of `kind == "stream"` — the pointer-target anchor `datadecl` carves `via` the
pointer pair — which a bank pool taking `kind == "table"` only does not admit, and the
declared byte there is not the byte the register took. So the recovery is **0 emits**, and
it would still be 0 with streams admitted.

**The refusal the rule rests on, priced.** An address recovered by *watching* the
execution — re-evaluating the impure address, which is the design this refuses — would
change **3751** SID writes' source tuples over **28** tunes, 481 of them gaining a cell
where they had none. Where those addresses land:

| the reported address is | writes |
|---|---|
| a declared `stream` (no bank), byte equal to the emit | 1585 |
| a declared `stream` (no bank), byte the emit does not equal | 1423 |
| a declared table byte the emit does not equal (#61 refuses) | 683 |
| **a declared table byte equal to the emit — a lane** | **60** |

The 60 sit on writes that already carried a source cell, so they add nothing. Reporting the
whole proven target set instead of one member is refused without measuring a ceiling for
it: it is the observation choosing between blocks, and its claims are a superset of the
observation-derived ones, which recover nothing.

**The structural finding, which is the point.** The proof supplies the address *space* and
not the address, and now the consequence is measured rather than argued: the entry `k` is
live state at 366 of 366 sites (§4.4 measured the reload index non-constant at all of
them), so the block is live at 365. What **is** static is the table `T`, its declared
extent, the target set read out of `mem0` at that extent, the one-byte row bound and the
row index expression; what is **not** is which entry the pointer was reloaded from, and
therefore which block. A design that needs the block at run time needs a second evaluation
of an impure address or a hop through the origin map, and both are outside this rule.

And the address is **not what a consumer was missing**. The impure deref address was named
as the wall in front of arrangement recovery; handed the address the run itself used,
recovery downstream was still zero. Two walls stand behind it, both measured above:
`datadecl` carves the deref's target as a `stream` rather than a table, so a bank pool
taking tables only excludes 3008 of the 3751 addresses; and of the 743 that do land in a
table, #61's own byte check refuses 683. The only measured headroom anywhere in this chain
is consumer-side — admitting `stream` declarations as banks, ceiling **1585 emits over 28
tunes** — and only under the observation-derived address that is itself refused. With the
proof-supplied address the ceiling is **0**.

### 4.7 The one origin relation: built, measured, and what it cannot carry

Six mechanisms on this side of the artifact answer one question — *which declared datum
does this byte come from, and at what index?* The recurring proposal is to replace them
with one abstract relation over the frame program, computed to a fixpoint across frames:

```
⊥ | const(c) | region(R, i) | cursor(R) | ⊤
```

`region(R, i)` says the byte is datum `R` read at index `i`; `cursor(R)` says the cell's
*value* indexes `R`. The relation was built and run against the mechanisms it would
replace. **It recovers zero emits.** This section is the record of why, because the reason
is structural rather than a matter of how hard the analysis tries.

**`region(R, i)` is sound only where `i` is closed.** The index of a table read is a live
value at the site that read it. A driver stages `ram[c] = T[y]` at note-on and the SID
write consuming `ram[c]` runs an arbitrary number of frames later, by which time `y` holds
another row, so re-reading the index expression where the byte is *used* names a different
cell than the byte came from — not a weaker answer but a wrong one
(`test_a_staging_index_reread_at_the_reading_site_names_the_wrong_cell`). The only sound
static value for a staged byte is `region(R, ⊤)`, which carries no row, and every consumer
at this level needs the row: a lane key is `(cell − base) // stride` off the cell together
with the `mem0[cell] == value` check, which is #61's whole const claim.

**The map it would replace is already transitive and already crosses frames.** `prov` is
built once per evaluator, never cleared between frames, and path-compressed at bind time,
so a byte staged in frame 0 through two cells is reported in frame 3 as the table cell and
not as either intermediate (`test_the_origin_map_crosses_frames_and_chases_every_hop`).
What is one hop is the *reporting*: `_derived` puts the compressed origin ahead of the cell
the value read. A cross-frame fixpoint is therefore not the missing piece; the static
relation is the side that stops short.

**Measured** 2026-08-01, 682 cached tunes, PSID start subtune, 200-frame windows (650
decompile, 649 reach the gate). Three runs of the corpus: the map as it ships, the map
replaced by the relation, and no map at all. The emit columns are the partition of the
downstream consumer of the day, since removed from this tree (#122); they are kept as the
record the refusal rests on, and are not re-measurable here.

| | dynamic map | static relation | no map |
|---|---|---|---|
| **interpreted emits** | **753971** | **455212** | **455212** |
| freq | 417498 | 389831 | 389831 |
| pw | 90000 | 14413 | 14413 |
| ctrl | 112806 | 15132 | 15132 |
| filter | 23104 | 1434 | 1434 |
| sr | 56073 | 19063 | 19063 |
| ad | 54490 | 15339 | 15339 |
| `lane` | 516986 | 59569 | 59569 |
| `gate` | 32914 | 2045 | 2045 |
| `ramp` | 25399 | 849 | 849 |
| `imm` | 25188 | 23669 | 23669 |
| `seed` | 4152 | 188 | 188 |
| `mask` / `rel` | 676 / 317 | 0 / 0 | 0 / 0 |
| **triggers** | **300** | **0** | **0** |

The relation's column is **byte-identical to no map at all**, every plane, every class and
every tune. What the dynamic map carries is **298759 emits, 39.6% of the partition**, and
the whole trigger domain; 88% of `lane`, the strongest evidence class, arrives through it.

**The relation at its strongest.** Join the origin of every store site of a cell, chase it
through the locals and through other cells to a fixpoint, and admit an origin only where
the load's address is closed. It names **3402 state cells over 431 of the 649 tunes**,
against the **33915** the dynamic map holds at end of run. Cell for cell against that map:
**1644 agree**, **300 the run chased one hop further** (the static origin is itself a
staged cell), 67 otherwise differ, and **1391 the run bound to nothing at all**, its
one-contributor rule having refused what the static join asserted. Right at 1644 of 3402 —
and those 1644 explain no emit, because a closed address is a scalar copy while what
carries the evidence is the indexed read whose row a static reading cannot have.
Commando and Krakout name **0** cells each.

| mechanism | what it computes | the relation's value | verdict |
|---|---|---|---|
| `frameval._addrs` | address expressions of every load at a nameable address | `region(R, i)`, syntactic half | **already this relation's front end**; the row evaluates because §1.4's purity invariant says it may, not because a static value is missing |
| `frameval.prov`/`ploc` | the origin cell, per execution, transitive, cross-frame | `region(R, ⊤)` for a staged byte | **not subsumable**: 298759 emits and 300/300 triggers |
| `initcopy` | the cell an init copy staged a byte from | — | **not reachable**: the frame program is the play phase and `decompile` keeps only init's flat image (§4.5), so no pass over it can see the copy. 416 emits |
| `frameptr` | `cursor(R)` for a pointer state field | `cursor(R)` exactly | **subsumed in kind, not moved**: it is this relation over one cell class, and §4.6 measured what its address buys — 1 site of 3929, 0 emits. A real fixpoint would resolve definitions it refuses today (`P = Q`), moving the resolved set and the artifact text, which a refactor may not do |
| `framefuse` | lo/hi word pairs | a width-2 cell | **not expressible**: the premise is about *accesses* — every read inside a `lo\|hi<<8` shape, adjacent half stores, the write-order hazard — not about what a cell holds, so a width-2 lattice cell states the conclusion and proves none of it |
| `datadecl` | the declarations and their `mut` offsets | the `R` every other value names | the one piece that **was** shared |

**What was taken.** One item in that list was duplication rather than a mechanism: the
containment index over the declarations — which declared datum holds a byte, at what
offset, and whether that offset is const — stood in three places (`datadecl._avail`,
`frameptr._Tables`, `initcopy._spans`/`_declared`), with the `mut` record reading in two of
them and `datadecl._mut_offs` writing it in a third. It is now `datadecl.Regions`
(`at`/`const_at`/`avail`) in the declarations' own module, `_mut_offs` shares its record
reading, and `initcopy` takes the const predicate from its caller and stops knowing about
declarations at all. Gate FP **649/649**, the canonical
fixpoint **649/649**, raw `mem[` **9682**, tunes with none **51**, the emitted text
**9608342** bytes and the value partition **753971/1942809** are byte-identical, every
plane, every class, every refusal counter, **zero tunes moving in either direction**.
Source: `deity_informant` +57/−65, three implementations of one predicate reduced to one.

**Mutation evidence.** The two properties this section rests on are now asserted, and each
wrong reading of them fails: clearing `prov` per frame breaks
`test_the_origin_map_crosses_frames_and_chases_every_hop` (the map would be per frame) and
dropping `_copy`'s path compression breaks it too (the map would be one hop), both also
taking `test_a_staging_index_reread_at_the_reading_site_names_the_wrong_cell`. On the
shared index, four wrong readings fail their tests: the record taken as the stride for a
flat region, containment without the extent check, `const_at` ignoring `mut`, and `avail`
running past the region end.

## 5. Risk register

| risk | disposition |
|---|---|
| An init origin outliving the play write that supersedes it | Closed by construction, not by a check: the init map is only the *initial value* of `frameval`'s `prov`, so the first play-phase store to a staged cell rebinds it to that store's own contributor or drops it (§1.4's one-contributor rule). What the map can still assert is that the source is const data, and that is `datadecl`'s `mut` at the origin — refused per cell and counted (§4.5). `tests/test_initcopy.py` drives both halves: the play write drops the entry, and re-seeding it across that write moves the reported record. |
| A named deref outliving its proof | Rung (f) names only what it proved, and the name is the proof's shadow: `FrameProgram.resolved` is built by `frameptr.apply_rung` and consumed only by the emitter, so a site with no record renders raw. The reader rebuilds the same map from the `*ptr[i]` text, which is why the fixpoint is the check that the two agree. The residual risk is a consumer reading `*ptr[i]` as "in bounds of one block": it is not — the claim is `address ∈ {T[k]} + [0, row bound]`, a union of intervals, and the block extents are `datadecl`'s own (180 of 366 sites have every block declared). |
| A proven address outliving its proof (§4.6) | The reported address is the proof's own: `frameptr` emits it only where the target set — the declared entries **and** the pointer's image word — is a singleton and the row expression is pure, so nothing impure is ever evaluated twice and `_pure` is untouched. What the rule can still get wrong is the *set*: dropping the image word pins 45 more sites whose pointer starts elsewhere, and dropping the row bound turns a row into a whole-address-space claim. Both are mutations `tests/test_frameptr.py` drives, and the third — an address taken from the run instead of the proof — is the design §4.6 prices and refuses. Sites the rung did not resolve keep the pointer's own cells exactly as before. |
| Multi-call-per-frame / multispeed drivers | v1 class: frame = the play invocation, settled. v2/P-INT redefines the frame as the driver-cadence tick; the projection then applies per tick and the digi rule re-triggers (fast CIA volume writes). Deferred with v2. `play == 0` tunes are in the v1 class as of the handler entry (docs/decompiler-implementation.md §8.1): one handler invocation per frame, entered through a synthetic IRQ dispatch stub, so the frame is still the play invocation and Gate FP holds unchanged. |
| Digi / $D418 order | Closed by the class rule (§1.2): $D418 is last-write-wins; a >2-step collapsed volume sequence excludes with a precise diagnostic; 2-step frames collapse with a reported metric. Corpus: 0 exclusions; a digi tune MUST be added to exercise the path. |
| The two sides disagreeing on what a volatile input *is* (fixed) | Closed. `frameprog._INPUTS` declared $DC0D a nondeterministic input `cia_icr()` while the walker's `_VOL0` inlines that read as the constant 0 at block-compile time, never calling the pinning hook: `iota` could not record what the evaluator then demanded, so the first read of frame 0 faulted `past the pinned trace` — the one-model claim of §1.3 violated in the *set* of inputs, not in a value. 3 of 682 cached tunes (`4k_Digi_Competition_Entry`, `Chotmix`, `5_Channels_of_Feekzoid_Noise`), none digi-class. The declared set is now keyed on `structured._VOL`, so an address the walker cannot pin cannot be declared, and the evaluator resolves `structured._VOL0` to 0 exactly as the walker does instead of naming $D019 alone. Repaired frameprog-side by construction: the walker's constant-0 model is the v1 ground truth Gate C already verifies (decompiler-implementation.md §8.1), not an approximation to correct here. |
| Behavior genuinely dependent on cycle position of volatile reads | The law stays well-defined: both sides consume the pinned `iota` (§1.3). The residual risk is semantic, not soundness: such a frame program is faithful only modulo its input trace, and a standalone run beyond/without the trace faults rather than improvises. 3/140 tunes affected, osc3 only. |
| Unbounded play-time code copy | The one SMC shape with no state translation (§2). Refuses with a site diagnostic; zero corpus tunes. Everything else — operand, opcode toggle, vector, reads-as-data — is state by construction, with the faulting-default guard covering unobserved values. |
| Inline parameters after `JSR` (open) | The one remaining frameprog-attributable corpus failure: `C64_World`, `FrameFault: unobserved $4ED7 reached` at frame 189. `$4ED4: JSR $4921` is followed by four data bytes; `$4921` pulls its own return address into a pointer (`PLA/PLA`, rendered `mem[(sp+1)\|$0100]`), copies the four bytes through it and pushes the address back advanced by 4, so the `RTS` skips the data. `frameproc` renders that call as a `pcall`, which drops `ret $R` and makes `_Code.synth` push a stand-in address, and `frameval`'s `ret` returns through its shadow stack rather than the patched image — so the callee rewrites a stand-in and the return lands on the inline data ($4ED7), a site the trace rightly never observed. Fix direction: refuse the `pcall` promotion where the callee reads the stack at its own return slot (the `call ... ret $R` form already pushes the real address), not a second return path in the evaluator. Not the volatile-input divergence above; distinct cause, distinct fix. |
| A 16-bit add whose halves are written to different places | Rung (d2)'s false positive would be *merging* it, and the reason the destinations are checked separately from the sources. `C64_World` (CyberTracker) at `$4953`: `LDA $14 / CLC / ADC #$04 / STA $14 / LDA $15 / ADC #$00 / PHA` is a real 16-bit add, but the hi half is *pushed*, not stored to `$15` — `$4921` pulls its own return address, skips the four inline parameter bytes and pushes the address back, so the 16-bit destination is the return-address pair on the stack and `$14` is a one-byte spill. The **sources** `$14`/`$15` are one quantity, so the arithmetic lifts and the emitted text carries the `+ $0004` as a word; the **destinations** are not the lanes, so the two writes stay apart and no `u16` store is emitted. Collapsing (`$14`, stack slot) into one word store would write the right value through the wrong cell. Pinned by `tests/test_framemath.py::test_the_c64_world_cybertracker_half_goes_elsewhere`, in both the `PHA` and the plain-`STA $16` form. The same routine holds a genuine pair nine bytes earlier (`STA $4951`/`STA $4952`, the self-modified operand of the `STY` at `$4950`), so "this tune is broken" is not a safe proxy for "this site is bad". |
| Isomorphism near-misses (voice-3 noise/filter special cases) | Rung (e) refuses; copies stay per-voice, FP still holds. Tracked via the unification-rate metric; synthesized voice guards are forbidden (they fabricate structure the code does not have). |
| Forward `goto` into a later arm (fixed) | Closed. `frameproc`'s backward liveness sweep walks an `if`'s then-arm before its else-arm, so a `goto` was seen before its target label: the label's live-set read empty and locals live across that edge looked dead, letting `_inline` delete an update the target still consumed. Two faults, both needed: `_Flow.run` now iterates label live-sets to a fixpoint (as `_loop_head` already did for loops), and `_invis_name` treats an own-procedure `goto` as consuming whatever is live at its label instead of dismissing it — `_use_count` sees no textual use, so the consumer was invisible. |
| Stack-driven dispatch (`PHA`/`RTS`, `TXS`/`RTS`) | Closed. The surface serializes the transfer as a bare `ret` and the evaluator returns machine-faithfully through `sp` and the stack image. `PHA`-pushed targets were unrecoverable only because the passes treated `sp` as an ordinary local and eliminated its updates; `sp` is machine state (`call`/`ret` move it, pushed bytes land at addresses derived from it), so it is now exempt from pruning, from inlining and from the faint-assignment rule. `_fuzzgen.t_rts_trick` passes and `_FP_GAP` is empty. |
| Inline callee body entered by `call` (fixed) | Closed. A label some `call` targets is a mini-procedure: its exit returns to the call sites and may be re-entered, so a local it updates stays live. `_scan_list` collected `goto` targets and labels but never `call` targets, so the sweep treated the body's end as textual fall-through and `_prune` deleted a live update. `_Info.call_labels` now records them and both sweeps keep the machine set live from such a label onward. |
| Envelope dispatch under frame semantics | ADSR hardware state is not modeled at this level; audibility rests on the order-preserved ctrl/ADSR section (hard restart, test-bit, retrigger survive per §1.1). `envelope3()`/`osc3()` reads are pinned inputs; a driver branching on sub-frame envelope phase degrades to trace-faithful (previous row). |
| Sub-frame filter-mode transients | Collapsed by last-write-wins and declared non-normative (§1.2); measured benign (equal volume nibble) on all 17 multi-write tunes. |
| Replacing the dynamic origin map with a static relation | Refused, priced (§4.7). The lattice's `region(R, i)` is sound only where the index is closed, and a staged byte's index is live at the staging site alone — re-read where the byte is used it names a different cell. Built and run over the corpus, the relation recovers **0** emits against the dynamic map's 298759 and the whole trigger domain, and of the 3402 cells it names it agrees with the run at 1644. What was actually shared — the declaration containment index — is now `datadecl.Regions` and the three copies are gone. |
| A rung that reads well and consumes worse | Rung (d)'s SID half was the case: fusing freq/pulse/cutoff moves no record (Gate FP 649/649) but cost the consumer of the day 752598 → 699551 of 1942809 emits, because one word store names one register class where two byte stores named two (§4.3). It was held opt-in for that consumer; the consumer is gone and the frame program is the deliverable, so the rung now applies unconditionally and a downstream reader that keys lanes off the store statement must read a `u16` store as naming both halves. Every later rung MUST still report the consumer partition beside Gate FP. |

## 6. Milestones and corpus gates

Each milestone is independently shippable, gated **full-length,
full-corpus** on the cached HVSC set (opt-in job, results recorded); the
committed synthetic corpus (`tests/_fuzzgen.py` extended) independently
covers every new code path so CI holds its gates and >85% coverage with
HVSC absent (decompiler-implementation.md §1, §7).

- **M-FP1 — projection + verifier.** Landed: `framelog`
  (canonical/dumps/loads/digi_frames/diff, walker adapter); `iota` extraction
  (`frameprog.iota` returns the pinned `{(frame, input, k): value}` trace *and*
  the frames of the same run, so both sides of the law consume one trace by
  construction — `structured.Walker.vol_read`/`dyn_read` are the hooks). The
  declared-input law of §4(b) is checked against `frameprog.declared_inputs`.
  Landed since: the buffered-flush reference evaluator and `gate_fp`
  (`deity_informant/frameval.py`), with the three M-FP1 mutations — dropped
  write, swapped ctrl order, wrong `iota` index — each shown to fail the gate.
  Remaining: the class report (exclusions, `d418_collapsed`).
- **M-FP2 — entry translation + mechanical lifts (a)-(c).** The §2 SMC-free
  generator plus rungs (a)-(c); first `frameprog` text artifact (versioned
  header, grammar published, canonical fixpoint `dumps(loads(t)) == t`).
  Gate: FP after translation and after each rung; declared-input set ==
  `iota` domain; dead-store proofs recorded; the play-time code-copy
  refusal exercised synthetically. Landed toward M-FP2: the procedural
  surface (`deity_informant/frameproc.py`): registers and temporaries lift to
  named locals, procedure parameters/returns are inferred from
  interprocedural register liveness (`x = sub_XXXX(a)`), counter loops render
  as `for x in $02..$00` ranges; and the text artifact itself — published
  grammar, versioned header, `frameprog.parse`/`loads` and the canonical
  fixpoint `dumps(loads(t)) == t` property-tested over the fuzz corpus, with
  the defined-before-use local check now running over the parsed statement
  trees; and the reference evaluator: statement trees compile to one flat op
  array over a program-wide local environment and the `mem0` state image,
  volatile reads resolve to `iota(f, input, k)`, SID writes buffer per frame
  and flush through `framelog.canonical`. Gate FP holds on **all 19**
  `tests/_fuzzgen` player classes (`_FP_GAP` empty) and on Commando at full
  Songlengths length. Measured 2026-07-31 over the **whole cached corpus** —
  682 tunes, PSID start subtune, 200-frame windows: **650 decompile**, of those
  **Gate FP passes 649**. The 32 that never reach the
  gate are sidprog refusals (19 `runaway in init`, 10 `play $0000` installing
  no interrupt vector, 3 unmodelled `brk`); **one frameprog-attributable
  failure remains** — `C64_World`'s inline-parameter `JSR` (§5), one site of
  one class. The earlier record on this line, "frameprog-attributable failures
  are zero" over a 140-tune sample at 300-frame windows (2026-07-29, 123 pass
  and 0 diverge), was true of that sample and **false at 682**: the same sweep
  found 3 tunes faulting `iota(0, cia_icr, 0) past the pinned trace`, the
  §1.3 input-set divergence now closed (§5) — Gate FP 646 → 649, the whole
  delta being those three tunes. The 140-tune sample
  scored 96 before the three liveness fixes of §5 (goto-into-later-arm,
  `call`-entered inline bodies, `sp` as machine state): 96 → 111 → 123, none
  regressed. Landed since: the **init-copy origin map**
  (`deity_informant/initcopy.py`, §4.5 for the census), which seeds §1.4's cell →
  origin map with the copies init made, one `structured.Proof` per init store site.
  Gate: FP 649/649 and the canonical fixpoint 649/649 over the 682-tune corpus,
  both unchanged. `tests/_fuzzgen` carries the `init_param` class (a sweep
  step staged in RAM at init out of the table the play phase indexes) and
  `tests/test_initcopy.py` the refusals. **M-FP2 mutation evidence (init copies)**:
  the rule annotates, so the record each mutation must move is the reported source
  tuple, and each of the three does — an origin taken from a cell that merely holds
  the same byte reports that cell instead of the traced one
  (`test_mutation_an_origin_from_a_value_match_moves_the_record`); an origin kept
  across the play-phase write that superseded it puts a table cell back into a store
  the play code computed
  (`test_mutation_keeping_an_origin_across_a_play_write_moves_the_record`); and a
  computed staging cell given an origin reports a declared byte where the sound
  answer is the cell itself
  (`test_mutation_giving_a_computed_cell_an_origin_moves_the_record`). Outstanding
  for M-FP2: the rung (a)-(c) proof records, and the upstream refusals are a sidprog
  question.
- **M-FP3 — fusion (d).** Landed (`deity_informant/framefuse.py`, §4.3 for the
  measurement): the state-pair fusion with a `structured.Proof` per candidate
  pair, and freq, pulse width and cutoff fused on the same footing — per store
  site, unconditionally, and a lane store indexed by a proven lane-aligned index
  widens with it (§7.2). Gate: FP 649/649 and the canonical fixpoint 649/649,
  both unchanged over the 682-tune corpus; byte-wide SID stores 984 -> 821, and
  a wholly play-written array now declares its extent so an index has a bound
  (§7.3), for emitted text 9787954 -> 10192917 bytes (+4.1%).
  `tests/_fuzzgen` carries the `word_pair` and `lone_half` classes and `tests/test_framefuse.py` the
  synthetic refusals — lone half, unpaired half store, write-order hazard — plus
  the mutation evidence that a wrongly fused pair moves the record (non-adjacent
  halves, swapped halves, a hazard fused anyway). Outstanding: the rung (a)-(c)
  proof records are still M-FP2's debt.
- **M-FP3b — 16-bit arithmetic (d2).** Landed (`deity_informant/framemath.py`,
  §4.3 for the measurement): the carry/borrow-chained byte-wise update read as
  one 16-bit add/sub, the shape queried off the admitted rule set rather than an
  idiom table, with a `structured.Proof` per site and every lemma Z3-proven in
  `eqlift.RULES`. Gate: FP 649/649 and the canonical fixpoint 649/649 over the
  682-tune corpus; 1452 sites lifted across 414 tunes, 76 refused with named
  diagnostics, and the corpus bit-identical under two hash seeds (§7.1).
  `tests/test_framemath.py` carries the synthetic add/sub, the split-lane and
  adjacent-cell forms, each refusal, the CyberTracker case (the arithmetic
  lifts, the destinations do not merge), the mutation evidence that a wrongly
  lifted site moves the record, and the two soundness regressions of §7.1/§7.3 —
  a definition made inside a branch arm, and a SID pair naming the lifted store.
  Outstanding: the zero-page indexed lane address (§4.3), which is rung (f)'s
  naming gap too, and the mis-grouped extraction of §7.3.
- **M-FP4 — unification (e).** Gate: FP; isomorphism records; voice-3
  near-miss refusal exercised synthetically; unification-rate metric.
- **M-FP5 — the frame function (f).** Gate: FP; FP-complete tunes reported
  (no unproven raw `mem[expr]`); the Commando-family excerpt shape achieved
  on at least the index-looped drivers; per-tune rung recorded in the build
  report. Landed toward it: **pointer resolution**
  (`deity_informant/frameptr.py`, §4.4 for the measurement) — the base-less
  pointer-pair deref rung (d) left, named `*ptr[i]` against the declared
  `lo`/`hi` table its every definition reloads from, with a `structured.Proof`
  per deref site carrying the table, the definition count, the target block set
  and the row bound. Gate: FP 649/649 and the canonical fixpoint 649/649 over the
  682-tune corpus, both unchanged; raw `mem[` 10280 → 9682 and tunes with none at
  all 17 → 51. `tests/_fuzzgen` carries the `ptr_seq` class (a pointer table
  walked by a position counter, deref'd at a separate row index) and
  `tests/test_frameptr.py` the refusals. **M-FP5 mutation evidence**: this rung
  cannot move a canonical record — it rewrites no tree and no value, which is
  the whole argument of §4.4 — so the record each mutation must move is the
  *proof*, and every one of the three flips a refusal into a resolution or
  widens the claim: resolving against a definition not proved (an advance
  `P = P + n`, a store the analysis cannot place, a half pair with no declared
  `lo`/`hi` role) turns a `refused` record into `resolved`; reading the target
  set past the declared extent turns a 1-entry declaration's one block into two
  (`test_the_target_set_is_the_declared_extent_not_the_image_run`); and dropping
  the row bound resolves an index whose sound bound is `$FFFF`, a whole-address
  space claim rather than a row. Landed since: **the store's source through a proven
  deref** (§4.6 for the census) — where the proof names one target block the evaluator
  reports that block plus the pure row as the store's source cell instead of the
  pointer's own cells, with a `structured.Proof` per deref site (`kind == "deref-src"`).
  Gate FP 649/649 and the canonical fixpoint 649/649, both unchanged, and the
  emitted text byte-identical. The census is the result:
  **1 site of 3929 pins**, because the proven set is `{T[k]}` together with the pointer's
  own image word and the entry `k` is live state at 366 of 366 resolved sites.
  `tests/_fuzzgen` carries the `ptr_pin` class (a pointer table whose every entry names
  one block, its rows also read at a const base) and `tests/test_frameptr.py` the
  refusals. **M-FP5 mutation evidence (the provenance rule)**: the rule annotates, so the
  record each mutation must move is the reported source tuple or the proof, and each of
  the three does — an address re-derived by evaluating the impure expression reports where
  the run went rather than what is proved
  (`test_mutation_an_address_from_observation_moves_the_record`); a site the proof leaves
  as an address space, pinned to one of its blocks anyway, reports rows of that block
  while the machine reads three others
  (`test_mutation_an_unresolved_site_given_an_address_moves_the_record`); and dropping the
  row bound turns the claim `block + [0, $FF]` into the whole address space
  (`test_mutation_dropping_the_row_bound_claims_the_address_space`). Outstanding: rungs
  (e)/(f) proper, and the ceiling on resolution is rung (d)'s fusion rate (§4.4 census).

Gate FP is the only correctness law at this level; no milestone may weaken
it, and sidprog's Gates A/C/L/S are untouched throughout.

## 7. Open issues

Work in flight on the lift ladder, with what is proven, what is not, and the
evidence each claim rests on. §7.1 is settled; everything measured before it was
settled has been re-measured against the deterministic gate.

### 7.1 The gate was flaky: two defects, one visible (SETTLED)

`tools/lifttrace.py repeat Andy_Capp-The_Game --runs 8` gave a **split verdict**
on identical source. The verdict turned out to be a *pure function* of
`PYTHONHASHSEED` — seeds 0, 2, 3 and 4 passed and seeds 1 and 5 failed, twice
each, reproducibly — so it was never a race, and `extract_multiple` pool
membership alone was the wrong diagnosis (`docs/eqlift-adoption.md` §10, since
corrected). Two independent defects were compounded:

- **A wrong lift the gate could reach.** `framemath._Env` indexed only the
  top-level `asg` of its own statement list, so a definition made inside an
  `if` arm was invisible and a later read resolved to the definition *before*
  the branch. In `Andy_Capp` the arm holds the `DEY` of `LDY #$00 / BCS / DEY`
  at `$FAF5`, which sign-extends the byte added to the `$F8/$F9` pointer; read
  as the `LDY #$00`, the extension folds away and the lift adds `$0100` too
  much — exactly the reported `got=(1,10) want=(1,9)`. The fix is `_kills`: a
  definition the list does not itself make (a nested body's, a call's, or one
  control reaches through a label) is recorded **with no value**, not omitted,
  so a read past it is unknown rather than the last value written textually. The
  entry still names *which* definition, so two reads either side of one do not
  compare equal.
- **Which of the two the build reached.** Extraction returns *a* representative
  of an e-class and which one is not contractual. Here the tie was between
  `x - 2405` and `x + 63131` — the same address in two's complement — and only
  one of them had a provenance naming `_back` could use, so the hash seed
  decided whether the unsound site was lifted at all. `EQ.canon` gives the pair
  one representative, the `add` spelling pass-1 writes an indexed address as.

The pass-1 input to rung (d2) was **identical** across seeds throughout; the
divergence was entirely inside `_fuse`. Evidence for the fix, at two scales:
`lifttrace stable Andy_Capp-The_Game --runs 4` — 4 seeds agree on all 38
decisions; and the whole 682-tune corpus run under two hash seeds, **672 records,
0 differ**. A corpus number is now a measurement rather than a sample.

Determinism is a property of the *decisions*, not of the verdict: `repeat`
only sees an unstable decision that happens to move an emitted frame, which is
why `stable` diffs whole traces (§7.4).

### 7.2 Rung (d): freq, pulse and cutoff are 16-bit registers

Nothing narrower than 16 bits can be written to them, so they are typed that
way unconditionally rather than fused only where a driver happens to write both
halves adjacently. `framefuse._widen` turns a lone lane store into the `u16`
store it is, the other lane keeping its value; `_Pair.refusal` never refuses a
SID pair; `framelog.canonical(frames, held0)` reports the whole word when either
lane is written, seeded from `frameval.sid_held0(prog)` (`prog.mem0`) and passed
to **both** sides in `gate_fp`.

Two measurements decided that shape rather than assumption: 64.3% of
freq/pulse/cutoff lane writes rewrite the value already held, so change-gating
the VM is not viable; and 10.19% of frame-pairs write exactly one lane, so the
projection had to compare the register's value rather than which byte moved.
Without the `held0` seed four tunes diverge as `got=(21,7) want=(21,0)` — the
sidinit value against the projection's default.

Open:

**Indexed lanes widen under a reaching-definition rule (LANDED).** `mem[$D400 + y]`
is a store to register `y`, not to freq; widening it blind writes whatever cell
follows, which corrupted `Also_Bad` and `Aiginas_Prophecy` in `v0.ord`. The rule
is a per-site dataflow fact about the index's reaching definitions, never a
property of its name — `Also_Bad` uses X and Y for *both* the voice offset and
the voice number, swapping roles between `JSR $C098` (X = 0/7/14) and
`JSR $C1E3` (Y = 0/7/14). `framefuse._consts` resolves the index through
`frameproc.Defs` and `_lane_aligned` widens iff **every** value it may take puts
the pair's lo on a register that is itself a pair's lo (`_sid_base(p.lo + k) ==
p.lo + k`). A constant *table* counts: Commando's index is `LDY $14B5,X` with
`$14B5` = `$00 $07 $0E`, and a rule demanding immediates would refuse Commando's
own pulse stores. `Also_Bad` and `Aiginas_Prophecy` both gate clean.

`framemath._Env` moved to `frameproc.Defs` for this — one implementation of "the
definition in force here", now used by rungs (d) and (d2) both — and gained an
*enclosing* scope: `Defs.resolve` follows a local out through the lists that
contain it. A body reached by a back edge sees its own later definitions on the
next iteration, so an enclosing one survives only a name that body never binds;
`at` is unchanged and list-local, because the index it returns is a position in
one list and a caller stepping to it must not be handed another's.

Measured over the corpus: **byte-wide SID stores 984 → 821, word stores 2694 →
2857**, Gate FP 649/649 and the canonical fixpoint 649/649 unchanged, and the
corpus still bit-identical under two hash seeds. 165 indexed lane stores prove
lane-aligned; 821 do not, and the two reasons are named, not guessed:

- **806 — the index is spilled through a play-written RAM cell** (PARTLY CLOSED,
  70 of 786 re-measured resolutions, 35 sites in 16 tunes). This was recorded as
  "the index table is undeclared, so `Regions.avail` is 0"; re-instrumenting
  `_consts` splits `row is None` (a scalar read, where `_consts` takes size 1 and
  never asks `avail`) from a genuinely undeclared table, and **all 806 are the
  scalar case and all 806 cells are in `model.written`** — 0 are undeclared
  tables. `Ala_Gal` is the shape: `$1150 LDA $1046,X / $1153 STA $107E / TAY`
  caches the voice offset, and every SID store then reloads `$1634 LDY $107E`.
  No `datadecl` declaration can close this — the cell is written by play, so it
  is not const under any extent claim. What was missing is a reaching definition
  *through a memory cell*, and `frameproc.Defs` now answers that too:
  `Defs.cell(cell, bound, regions)` is `_lookup` over memory rather than names,
  returning the byte store in force at a read — the last statement before it that
  may write the cell — chased out through the same enclosing scopes and refused
  on the same cyclic premise. `_consts` follows it and re-asks, so a spilled
  index proves whatever the value it was spilled from proves. `framemath`'s
  `_span`/`_overlaps`/`_ref` moved to `frameproc` for it, one aliasing rule now
  beside the one definition-in-force query, and `framemath` keeps the names.

  **Dominance was never the blocker; naming the addresses was.** As first
  written the rule closed **0 of 806**: every site died on an intervening store
  the pass declined to place, and the dominant shape is the stack push
  `mem[(zext2(sp) | $0100)]` that rung (d0) could not destack, which
  `addr_split` reports as "no base" and a walk must then read as writing
  anywhere. `frameproc.addr_bits` bounds it without naming the shape: a value
  fits the width it is read at, and `or`/`and`/`zext` over that give the bits an
  address can set, of which every address it names is a subset. The residue then
  splits: **330** an intervening *indexed* store whose base is undeclared, so its
  span falls back to 256 bytes and swallows the cell; **160** a dominating store
  whose value is not provable; **118** a label between, where control may enter
  having made no spill; **50** a loop and **24** an `if` arm that may write the
  cell; **18** a `pcall`; **12** a store whose address bits still cover the cell;
  **4** no store in the procedure. `Ala_Gal` itself refuses on the first two: 8
  on `$1064,X` and `$1073,X`, arrays inside the code image that `datadecl`
  declines to declare, so their spans reach `$107E`; 4 on a label between.

  Measured: **byte-wide SID stores 821 → 786, word stores 2857 → 2892**
  (lane-aligned indexed 165 → 200), Gate FP 649/649 and the canonical fixpoint
  649/649 unchanged, rung (d2) unchanged at 1463 lifted / 65 refused / 182
  merges, and the corpus bit-identical under two hash seeds (672 tunes, 0
  differ). Consumer partition: emitted text 10192917 → **10194792** bytes
  (+0.02%), raw `mem[` unchanged at 9682.

  **The flow-insensitive alternative was measured and not taken.** A cell's value
  set is also provable as `mem0[cell]` unioned over every store the program makes
  to it, which needs no dominance at all; instrumented over the corpus it closes
  **166** resolutions to this rule's 70, the union being 168, so it is the wider
  rule and not a superset. It buys that with a premise this rung does not
  otherwise make — that no control leaves the modelled statement tree, so the
  tree's `st` statements are *every* executed store — and it is blocked anyway on
  **476** by the same undeclared indexed spans. Bounding those is worth more than
  either rule and belongs to `datadecl`.
- **800 — the index is a procedure parameter**, so the enclosing chain runs out
  inside the procedure. The voice loop passes it: `Also_Bad`'s `JSR $C098` with
  X = 0/7/14 is three call sites each supplying a constant. Closing this wants
  the union of the constants each call site passes — an interprocedural step the
  chain cannot reach, and the last thing between the rung and the whole residue.

(Both counts are per-resolution and so double-count the measure and mutate
passes; the ratio is the finding, not the absolute.)
The spill rule is pinned by three tests in `test_framefuse.py`: the widening
itself, a store between the spill and the reload that refuses it, and
`::test_an_address_the_stack_page_bounds_does_not_kill_the_spilled_index`, which
is `addr_bits` as the thing that decides whether the first two are reachable.
The six tests that pinned the superseded contract are moved to this one and the
suite is green; `test_framelog.py` gained
`::test_a_held_lane_is_the_value_the_seed_and_earlier_frames_left`, which is the
`held0` seed and the frame-to-frame carry stated as a test.

Split-table 16-bit *state* is real and settled — do not re-litigate it. Commando
`$12E1 LDY $14B5,X / $12E4 LDA $1467,X / $12E7 STA $D402,Y / $12EA LDA $146A,X /
$12ED STA $D403,Y`: the lo and hi bytes of the 16-bit pulse width come from two
parallel 3-entry tables, one entry per voice. A split *register* is impossible;
split state is the ordinary structure-of-arrays layout.

### 7.3 Rung (d2): the refusal classes

> SUPERSEDED by §7.6 for rung (d2): there are now 0 refusals over the 610
> shapes. The classes below survive only as corpus shapes, unre-measured.

Root causes traced by instrumenting `FF._addr_split` and the aliasing
predicates, and by reading the 6502 at the refusing sites. The trajectory,
re-measured against the deterministic gate of §7.1: **77 → 76 → 65 → 62
refusals** while lifts went **1434 → 1452 → 1463 → 1466** and merges **131 → 181
→ 182 → 182**, Gate FP 649/649 and the canonical fixpoint 649/649 throughout. The
classes moved as well as the totals — "lanes indexed differently" is now 8 (was
22 before the §7.1 fix) and "may alias the hi lane" 2 (was 14, then 6) — so the
counts recorded before §7.1 was settled should not be compared against these.

**Everything in this section predates §7.5's redesign, and its counts are not
comparable to the current ones.** Every class below was measured while `_links`
still gated the rung, so the section is a record of what was reachable *through an
idiom filter*: 1466 lifts against 62 refusals. With the filter gone the rung reaches
far more sites, and both numbers rose together — **2311 lifted, 120 refused**, over
649 records at Gate FP 649/649 and fixpoint 649/649. A refusal here is a site the
rung now sees and declines, not a shape it never looked at, so the doubling is the
census widening rather than the lift regressing. The classes are unchanged in kind:
46 an unresolvable lane address, 42 the lo destination disturbing the hi lane or the
step, 20 lanes indexed differently, 5 the hi destination, 4 may-alias, 3 an
intervening statement. §7.5 carries the current measurement.

**The dominant class, triaged.** "The lo destination may disturb the hi lane or
the step" was 43 of the 76, and `lifttrace capture` now records the store's range
against every range it is held to reach, so the class splits by what actually
tripped it rather than by which predicate reported it:

- **33 — an unresolved *read* address**, and one shape: the step is a deref
  through a zero-page pointer pair (`Arpeggio`, `Danger_Mouse`,
  `Data_Data_Data_Data` are the same driver repeated; `$00F0/$00F1`, `$00F4/$00F5`,
  … per voice). These are **genuine** hazards as the pass stands. The deref is
  defined *inside* the lift interval, so `settle` inlines it and the load really
  does move above the lo store; with no bound on the pointer, a store to a
  constant byte cannot be excluded from it. **The bound does not exist, and the
  rung-ordering change is worth nothing (MEASURED, no change taken).** The
  reading above assumed rung (f)'s proven target block set (`frameptr`'s
  `blocks()`) was the missing bound and that only the rung order withheld it.
  Measured at HEAD over the 14 tunes carrying the class, the 40 refusals split
  **35 unresolved read / 5 other**, and of the 35: **2 are not a deref at all**
  (`Amazon`, `Donkey_Kong` — the latter is the local-defined-before-the-interval
  residue recorded below), and the other **33 deref through 32 pointer cells of
  which 0 resolve** — not after rung (d2), and not before it either. Running
  `frameptr.analyse` on the *pre*-(d2) trees yields the same verdict on all 32,
  and both verdicts are the same refusal: `the lo/hi pair did not fuse (rung d)`.
  The pointer is **advanced**, and that one fact refuses it three times over.
  `Arpeggio $3297 LDA $F0 / CLC / ADC #$04 / STA $F0 / BCC / INC $F1` is the
  reload-plus-advance sequencer walk: the reload at `$27DC LDA $2073,Y / STA $F0 /
  LDA $2173,Y / STA $F1` is the declared partner-table read §4.4 wants, but the
  advance is a lone-half read, which is why rung (d) refuses the fusion
  (`16-bit fusion: cells $00F0/$00F1; pointer pair (datadecl lo/hi partner table
  $2073); 5 word read(s), 1 word store(s); 1 lone-half read(s)`), and `P = P + n`
  is not a partner-table entry read, which is why §4.4 premise 1 would refuse it
  even if the pair did fuse. Checking premise 1 directly over the byte stores to
  each pair — i.e. asking the ceiling question without needing the fusion first —
  puts **32 of 32** on a non-table definition (**29** a literal advance
  `ptr + $04`/`+$05`, 3 an undeclared or computed source) and **2** additionally
  inside a tune with a wild store (premise 4). Zero are clean. The refusing site
  itself is SMC: `$32DC LDY #$02 / CLC / LDA #$00 / ADC ($F0),Y / STA $32E0 / INY /
  LDA #$00 / ADC ($F0),Y / STA $32E8` keeps its 16-bit accumulator in the two
  `LDA #$00` immediates, and `$32C3 LDA ($F0),Y / STA $00F1 / PLA / STA $00F0`
  reloads the pointer out of the stream it points at — a closure over the song
  data, not a set of table blocks. `Asterix_and_the_Magic_Cauldron` is a second,
  unrelated driver with the same answer: `$8236/$8237` is the patched operand of
  `$8235 LDA $86FE,Y`, advanced by `ptr0 + $01`. §4.4 already states the rule this
  measurement runs into — "an advanced pointer is `T[k] + n` for an `n` accumulated
  across frames with no static bound, so neither the (block, row) pair nor a range
  claim survives it" — so these 33 are refusals rung (f) is *designed* to make, not
  ones the rung order hides. All 40 stay CORRECT and the class is closed as such:
  raising it is upstream work on rung (d)'s lone-half rule, not an aliasing input.
- **10 — an undeclared span over a differing index (CLOSED, 43 → 36 here and
  6 → 2 in "may alias the hi lane"; 11 refusals in 11 tunes, none opened).** The
  store's span fell back to the whole 256-byte register range because
  `Regions.avail(base)` was 0, and then any constant read within 256 bytes
  collided. `Beat_the_System` is representative: `$1B0A LDA $213C,X / SBC $2164 /
  STA $213C,X` then `LDA $2136,X / SBC $2165 / STA $2136,X` — the per-voice
  freq lo/hi arrays and a scalar step 41 bytes above the lo array.
  `datadecl.declarations` dropped a group when **every** field base was in
  `model.written`, a filter that predates `mut`/`_sound_hi`. A declaration's
  claim is two things — an extent and a per-record-offset const set — and
  `mut` alone carries the second, so the filter suppressed extent evidence for a
  claim the declaration no longer makes: a wholly play-written array now
  declares its observed size with `mut` naming every offset, hence an empty
  const claim. `$213C` and `$2136` declare size 3, `_span` is 2, and `$2165` is
  out of reach. The mutable per-voice arrays move from unsized `state { }`
  fields to sized `data { }` tables (Commando `pos_54EC: u8[]` becomes
  `table pos_54EC[3] mut 0 1 2`), which is strictly more information and is what
  `test_frameprog.py::test_real_tune_frameprog_commando_gate` now pins.
  `_overlaps` already did the right thing once a span is right: the same-index
  lane read at `$2136,X` was correctly proved disjoint throughout.
  **The consumer partition, as §6 requires beside Gate FP:** emitted text
  9787954 → **10192917** bytes (+4.1%), raw `mem[` unchanged at 9682. The growth
  is the declarations themselves — an array that was one unsized `state` line is
  now a sized `data` table carrying its `mut` offsets and observed bytes. It buys
  the span that closed 11 refusals, and it is the same trade §4.3 records for
  rung (d)'s SID half: a rung that reads better and consumes worse is allowed
  only with the number stated.

Neither class was a defect in the rule that was consolidated; both were missing
*inputs* to it. The counts above are the re-measured trajectory, not the
pre-§7.1 ones.

**`datadecl`'s code-image filter is not what withholds the missing extents
(MEASURED, no change taken).** `declarations` drops a group whose base lies in
`_code_bytes`, and the residues above were read as that filter's cost. They are
not. The filter fires on **141 groups in 87 of 650 tunes**, and only **13 of
them (7 tunes) would declare anything at all**: `_next_bound` already treats
every code byte as a boundary, so a region *based* on one runs only to the next
code byte. Removing the filter over the whole corpus moves **one** number —
emitted text 10197544 → **10198819** bytes (+1275, the 7 tunes and no others);
Gate FP **649/649**, canonical fixpoint **649/649**, lifts 1523, refusals 52
with an identical class breakdown, merges 189, adjacent 668, SID pairs 365,
byte-wide SID stores 772, word 2899, raw `mem[` 9698, all unchanged. The extent
claim is **not separable** from the data-emission claim here, because a
declaration begins *on* the code byte: `data { table … }` would carry that
instruction byte as its first datum. Spec 2 already says what such a cell is —
a state variable — and an undeclared array base is exactly what
`frameprog._state_fields` emits as one. `mut`/`_sound_hi` are not the issue: 9
of the 141 bases are play-written SMC operands (`A_Chipful_of_Love_for_You`
`$121F`/`$122E`/`$123D` are the immediates of three `LDA #$00 / STA $D40x` at
stride 5, read back as `$121F,X`) and `mut` would carry their constness
correctly; the other 132 are const tables that merely start on an instruction
byte. Pinned by
`test_datadecl.py::test_a_base_on_the_code_image_is_not_carved_as_data`, which
fails when the filter is removed.

**What does withhold them, measured at the same HEAD.** Instrumenting
`Defs.cell`'s blocker over the corpus splits the spilled-index residue: **222**
the blocking indexed store's base is *never a group*, because `_idx_sites`
collects idx-shaped **reads** only and the base is only ever written
(`Boys_Dont`/`3_Feet_Higher`/`Bella_Rossa` `STA $1035,X`, `Ala_Gal`
`STA $1073,X`); **116** a group whose extent is 0, because `_run_reads` observed
no read at its site pcs and the index bound is unproven (`Ala_Gal` `$1064`, read
only at `$17D5`/`$17DA`, which the 200-frame window never reaches); and **0**
the code-image filter. The same split holds for the refusal class: of the 5
"undeclared span over a differing index", 3 are one driver's `STA $1035,X`, 1
(`Aaaaaargh_13` `$00BF,X`) is a modular index that no declaration may bound by
the rule above, and 1 (`Abatement`) has a store span of 0 and was bucketed on
its *reads'* spans. Closing the 222 wants an extent floored by observed
**writes**, which is a model fact that does not exist: `model.written` is a set
of cells with no site attribution, where `_run_reads` needs `pc -> addresses`.
It is also necessary and not sufficient — declaring `Ala_Gal`'s `$1064`/`$1073`
by hand removes them as blockers and moves that tune's SID store widths not at
all, because the next blocker on all 8 sites is a loop that may write `$107E`.

**"The lo destination may alias the hi lane" (2 sites, both CORRECT).** Traced
with `lifttrace`'s `alias` event, which records `_match`'s own test — the lo
store's range against the hi lane's — since `_may_disturb` fires before
`_premise` and so leaves no `premise` event.

- `Ultima_III-Exodus` `$005B/$005C`. The site is the init relocation loop at
  `$9AE0`: `LDA ($5B),Y / ADC $5B / STA $0019,Y / INY / LDA ($5B),Y / ADC $5C /
  STA $0019,Y`, adding the song-data pointer `$5B/$5C` to each entry of a pointer
  table and writing it to `$0019+Y`. The lo store reaches `$0019..$0118`, which
  contains the hi lane `$005C` at `Y = $43`; the entry count comes from the first
  byte of the pointed-at data, so nothing in the program bounds `Y`. Naming the
  base would not change the answer — the range still covers `$005C`.
- `After_the_War` `$0010/$0011`. `$122E LDA $14,X / CLC / ADC $10 / STA $14,X /
  LDA $5A,X / ADC $11 / STA $17,X`: the per-voice 16-bit accumulator at
  `$14,X`/`$5A,X` stepped by `$10/$11`. The lo store is `zp,X`, which wraps inside
  the byte, so it reaches `$0011` at `X = $FD`, and `X` enters at a label.
  **This site is the witness the `_index_of` extension above needs.** Naming
  `$14,X` as base `$0014` index `x` *without* the modulus makes `overlaps` read
  the range as `$0014..$0113` and declare `$0011` disjoint — turning a correct
  refusal into a wrong lift. `addr_bits` gives the sound answer (`$0000..$00FF`)
  and keeps the refusal.

**"An intervening statement changes an operand" (3 sites, all LIMITATION;
CLOSED, 3 → 0).** All three are rung (d2)'s *own output* blocking its second
sweep: the same 16-bit sum is written twice, the first pair lifts, and the second
pair then sees the first pair's hi-half store between its two statements. Two
independent causes, both fixed; `lifttrace` gained a `clobber` event naming the
blocking statement, its range and the operand it hit.

- **The interval was the wrong interval (`Der_Ring_der_Nibelungen` `$805E/$805F`,
  `Abatement` `$45FE/$44FE`).** `$86A5 LDA $8086,X / ADC $805E,X / STA $8086,X /
  STA $FB / LDA $8087,X / ADC $805F,X / STA $8087,X / STA $FC` — the second site
  is the `$FB/$FC` pair, and the statement that "changes an operand" is
  `STA $8087,X`, which the site's step reads. But that read was made at
  `LDA $8087,X`, *before* the store. `settle` inlined the interval's definitions
  and lost their positions, so `_premise` held every read against the whole
  interval `(i, j)`. The check is now one backward pass (`_hoist`): a statement
  blocks only what has been hoisted *past* it, and an assignment the word depends
  on is substituted rather than counted as a hazard. That is also strictly more
  correct than `_inline` was — `_inline` left a self-referential definition
  (`x = x + 1`) unresolved, which only the conservative check kept from being
  emitted as a read of the wrong `x`. `Abatement` `$5130` is the same shape
  against `$0A3F,X`.
- **An unresolvable store address was held to reach everything (`Bomb_Mania`
  `$3CB4/$3CB5`).** `$0FC5 LDA $3CB4 / CLC / ADC #$50 / STA $3CB4 / STA $08,X /
  LDA $3CB5 / ADC #$01 / STA $3CB5 / STA $09,X` mirrors a 16-bit counter into two
  zero-page pointers. `STA $08,X` names no base, so `store_ref` answered None and
  `_disturbs` returned True — yet `addr_bits` already proves a `zp,X` store cannot
  leave `$0000..$00FF`, and `frameproc.Defs._hits` has read it that way all along.
  `store_reach` puts that bound in range form beside `store_ref`, so the one
  `overlaps` rule decides it; the `UNRES` index sentinel is not a shared row, which
  is why `overlaps` now requires an index *expression* before it drops the spans.
  Here the read of `$3CB5` is later than the store, so the interval fix does not
  reach this site, and the store is later than the read at the two `alias` sites,
  so the mask does not reach those: the two fixes are disjoint.

**Measured, HEAD vs both fixes.** Refusals 65 → **62**, lifts 1463 → **1466**,
merges 182 → 182, Gate FP **649/649**, canonical fixpoint **649/649**,
`PYTHONHASHSEED=0` vs `=1` **0 of 672 records differ**. The consumer partition
§6 requires beside the gate: emitted text 10194792 → **10194957** bytes (+165),
raw `mem[` unchanged at **9682**. "The lo destination may disturb the hi lane or
the step" stayed at 36, which confirms the reading above: that class is
unresolved *read* addresses, not unresolved store addresses, and only rung (f)'s
target block set closes it. Both fixes are pinned by tests that fail when
reverted:
`test_framemath.py::test_a_write_later_than_the_read_it_would_spoil_is_no_hazard`
and `::test_a_zero_page_store_cannot_disturb_a_lane_outside_the_zero_page`.

- **Unresolved lane address: the `zp,X` wrap, named modulo 256 (CLOSED, 16 → 0).**
  Instrumenting the class over the corpus put **16 of 16** on one form,
  `zext2((x + K):1)` — the add is at width **1** because the wrap is inside the
  byte, and `_index_of` demanded `INT_ADD` at width 2 with a base ≥ `$100`. Both
  lanes of every one of the 16 carry the *same* index expression. That is the
  whole win: `$1A,X` and `$1D,X` differ by `(K1 - K2) mod 256` at every value of
  `X`, which `addr_bits` cannot say and a modulus can. With indices differing or
  unknown a `zp,X` access reaches the **whole zero page**, which is exactly what
  `addr_bits` already gave, so nothing loosens: `frameproc.span` refuses a
  declaration's bound to a modular index (the wrap is the evidence the access
  leaves its datum), and `_flat` normalises any modular range no shared row
  tightens back to `$00..$FF`. The **straddle guard** lives in
  `frameproc.addr_range`, which is where the *width* is known: a 2-byte access at
  a modular address does not resolve, since a word read at `$FF` takes `$0100`
  and the wrap does not. Donkey_Kong's `$E0,X`/`$E1,X` is the witness — adjacent
  cells that must still lift as two byte accesses, and it is `_Site.word` the
  guard denies, not the lift.
- **The naming was necessary and not sufficient; two more inputs were.** Naming
  alone closed 2 of the 16. The other 14 store through a CSE'd address local
  (`t0 = zext2((x - $7F)) … *[t0] = …`), and two predicates read the local rather
  than the address: `_lane_addr` returned the *shallowest* pass-1 naming of the
  lane address, and `store_ref` was handed the statement as written.
  `_lane_ref` now prefers, among the namings `to_egg` recorded for the same
  term, the shallowest one the range rule can read — free, since every naming
  admitted holds the same value where the lift emits, and it is admitted under
  the *strict* test (`_emittable(…, i, i)`), because a row named by a definition
  inside the interval is not the row the lift emits. `_store` spells a store's
  address through the definition in force, discharging the staleness `_disturbs`
  warns of by requiring every local that naming reads to hold at the store what
  it held at the definition. `Defs._hits` has resolved store addresses this way
  all along; this puts the same reading in front of the one `overlaps` rule.
- **Three of the four consumers must decline the modular naming, and do.** The
  plan above said fixing `_index_of` fixes four consumers at once. It does — but
  three of them answer a *naming* question, not a *range* one, and for them the
  answer is "no name": `base[index]` in the grammar means `base + zext2(index)`
  with no wrap, so `_membody` printing `zp_14[x]` for `$14,X` would emit a
  program that is not the one lifted; `frameptr._leg` reading `$14,X` as row `x`
  of the table at `$0014` would identify the wrong pointer table. `addr_split`
  therefore stays the naming form and returns nothing for a modular address, and
  `framefuse` (which is `addr_split`) is untouched. `frameptr._span` takes the
  range answer — a `zp,X` store is no longer a wild store, it reaches `$00..$FF`
  — which is strictly more information; on this corpus it changed no pointer
  proof. The one place the modulus belongs is the range tuple
  `(base, index, span, width, mod)` that `overlaps` decides, which is where it
  went.
- **Measured, HEAD vs the modulus.** Refusals 62 → **48**, lifts 1466 → **1480**,
  merges 182 → 182, adjacent 628 → 628, SID pairs 356 → 356, Gate FP **649/649**,
  canonical fixpoint **649/649**, `PYTHONHASHSEED=0` vs `=1` **0 of 672 records
  differ**. The consumer partition §6 requires beside the gate: emitted text
  10194957 → **10196432** bytes (+1475), raw `mem[` 9682 → **9697** (+15, all of
  it in the six tunes that gained lifts — a `zp,X` read the lift hoists into the
  word expression stays `mem[zext2((x + $1D))]`, since naming it would drop the
  wrap); byte-wide SID stores **786** and word **2892**, both unchanged. Eight
  tunes moved and no others: 14 sites lifted and 2 reclassified into "the lo
  destination may disturb the hi lane or the step" (36 → 38), both correctly —
  `Aaaaaargh_13`'s `$BF,X` store can wrap onto its own step at `$00E2`, and
  `Donkey_Kong`'s hi-lane *read* address is a local defined before the interval,
  which `_store` does not reach because it resolves store addresses only. That
  residue is the same shape §7.3 already names for that class: an unresolved
  *read* address. Extending the same resolution to reads is the next step and a
  separate commit.
- **Both counter-examples still refuse, and lift when the modulus is removed.**
  `After_the_War` `$0010/$0011` and `Ultima_III-Exodus` `$005B/$005C` are pinned
  by `test_framemath.py::test_a_wrapping_zero_page_store_may_reach_a_lane_below_its_base`
  and `::test_an_absolute_indexed_store_below_the_page_still_reaches_a_zero_page_lane`.
  Mutating `frameproc._flat` to drop the modulus makes the first test's synthetic
  **fail Gate FP** — it lifts, hoists the hi-lane read above the store that fills
  it, and emits a wrong SID byte — and makes the real `After_the_War` site lift
  as well; `Ultima_III` refuses either way, since `$005C` lies inside `$0019..$0118`.
  Dropping the straddle guard, or `_lane_ref`, or `_store` each fails
  `::test_the_zero_page_indexed_lanes_lift_but_never_as_one_word_access`.
- **A mismatched index, found by this work and fixed with it.** `_match` passed
  the *lo* lane's index with the *hi* lane's base to `_may_disturb`, so a hi lane
  at a constant address was claimed to sit at row `idx` of itself and `overlaps`'
  shared-row rule could prove a disjointness that does not hold. It predates this
  change. Over the corpus the configuration arises at **1 site** and the two
  answers agree, so it was unreachable — but "unreachable on this corpus" is not
  a property of the rule, and the fix is one token (`site.hidx`), so it is taken
  here rather than recorded: measured after, the corpus is unchanged in every
  figure below.
- **The modular branch of `overlaps` is sound only for one-byte ranges.** With a
  shared index and modulus it tests `(ba - bb) % m` against `(-wa, wb)`, which
  omits the wrap-around case `d > m - wa` that a range of width ≥ 2 could reach.
  Nothing constructs one: the straddle guard in `addr_range` refuses a modular
  address to any access wider than a byte, and every modular range in the pass
  comes through it. Verified rather than argued — asserting the invariant inside
  `overlaps` and running the corpus trips it in **0 of 682 tunes**. Widening the
  guard later MUST widen this test with it.
- **The grouping is now asked for, not read off an extraction (LANDED, 8 → 6).**
  `Antitrack_01 $13F4 LDA $166B,Y` is the *step*; the real lanes are `$15C8,X`
  and `$15CB,X`, parallel per-voice arrays sharing one index. `hi<<8` has a zero
  low byte, so `|` is `+` and `(hi<<8 | step) + lo == (hi<<8 | lo) + step`, and
  `_lanes` only checks that both operands are byte-wide `cell`/`load` nodes — so
  a step table wears lane shape and `_word_form` accepts it. Measured per site
  before the fix, the 8 split three ways and only the first two are defects:
  **1 never offered** (`Antitrack_01`: every extracted variant collapsed to the
  mis-grouping), **3 offered but not chosen** (`10_Days_and_No_Longer`,
  `18_Years_Mercury`, `Abatement`: the coherent pair was in the pool and `_site`
  broke the `(rmw, word)` tie by *position*, i.e. by extraction order), and
  **4 correct** (`Counterforce $2015,X`/`$1ED2,Y` ×4, `$1DB6`/`$1DD2`/`$1DF4`/
  `$1E12`, `LDA $2015,X / ADC $1ED4,Y / STA $2015,X / LDA $1ED2,Y / ADC #$00 /
  STA $1ED2,Y` — the two statements' own cells, which is exactly the shape
  `::test_the_refusal_diagnostic_names_the_premise_that_failed` pins as a
  refusal). The fix is three parts:
  - **`carry_comm` was the missing fact.** `carry_fuse0` matches the ADC chain
    with either lo addend as the lane, but only one way round, because `carry`
    was not commutative in the ruleset. Without it `add(pack(lanes), step)` is
    not in the fused e-class at all and no query can find it; measured, the
    cancellation below returns the question unchanged.
  - **The query.** `framemath._pairs` enumerates the lane pairs the *program*
    names — every byte the lo value reads against every byte the hi value reads,
    plus the two statements' own cells — and `_fuse` lets `sub(word, pack(lanes))`
    and `sub(pack(lanes), word)` for each. `pack(lanes) op step` is the word by
    construction however the step extracts, so `_asked` admits a form only where
    `sub_add_cancel`/`sub_sub_cancel` cancelled the pack back out: a step still
    reading a lane is the query answered with the question, which is the failure
    mode the sketch had to be checked for. All three rules are Z3-proven by
    `verify_rules`. A masked hi byte is stripped first (`_unmask`) and the mask
    rides on the answer, since `mask_hoist` puts the `AND` on the word — without
    that the 12-bit pulse sites (`Armalyte`, `Altered_Beast`, …) cannot cancel.
  - **The order.** `_rank` replaces the positional tie-break with a total order
    on program-visible facts: the statements' own cells, then a resolved pair,
    then one sharing a row, then adjacency, then the *nearer* bases — a step
    table sits away from the lanes it steps — then the step's cost and spelling.
    Two candidates tie only where they are the same form.
  Closing the class needed all three: with `carry_comm` alone every ADC site
  gains a second grouping and the corpus stops being hash-stable (**10 tunes
  differ**, `Armalyte $C546/$C549` against `$C4BA/$C549`); the query closes the
  candidate set so the pool cannot decide, and `_fuse` dedups it so the *trace*
  is stable too, not just the record.
  **Measured, HEAD vs the query.** Refusals 48 → **52**, lifts 1480 → **1523**
  (47 sites newly found, 43 of them lifted), merges 182 → **189**, adjacent
  628 → **668**, SID pairs 356 → **365**, Gate FP **649/649**, canonical
  fixpoint **649/649**, `PYTHONHASHSEED=0` vs `=1` **0 of 672 records differ**,
  and `lifttrace stable --runs 4` agrees on every decision of all 14 tunes that
  moved. Consumer partition: emitted text 10196432 → **10197544** bytes (+1112),
  raw `mem[` 9697 → **9698**; byte-wide SID stores 786 → **772**, word
  2892 → **2899**. The residue is 6: `Counterforce` ×4 unchanged and correct,
  plus 2 newly found sites of the same shape (`Are_Friends_Electric
  $17F9[y]/$10C8[x]`, `Flowing $1B3C[y]/$10B7[x]`) where the store cells' own
  query does not cancel, so only the mis-grouping is on offer and it refuses.
  **The merge was the hazard a mis-grouping could reach, and it is guarded.**
  `_premise` now spells its address premise as `_rmw(lst, i, j, site.addr)` —
  the same predicate `_site` ranks by — so the guard has one mutation point:
  `::test_merging_a_pair_whose_lanes_are_not_its_destinations_moves_the_record`
  forces it True on `$14`/`$15` lanes whose hi half is stored to `$16`, the u16
  store then lands on `$15`, and Gate FP fails. Lanes adjacent and destinations
  not is the only way a merge can be wrong, and `_rmw` is what forbids it.
- **The exact-index rule now lives in one place (LANDED).** Four sibling
  predicates answered "can these two address ranges intersect", and it was
  present in only two: `_disturbs` and `_may_disturb` had it, `_reads` and
  `_writes` did not, so a byte store to `T[x]` was held to clobber a load of
  `T+1[x]` — same index, one apart, provably disjoint, because an undeclared
  lane table leaves both spans at the whole register range. All four are
  collapsed onto one `_overlaps((base, idx, span, width), …)`, with `_reads` and
  `_disturbs` becoming the same predicate over a store's range. The earlier
  worry that the width terms had to be matched to each original rather than
  normalised does not survive measurement: normalising them to the general form
  is a strict *tightening* (a 2-byte store at `base` reaches `base + 1`, which
  two of the four dropped), and the consolidated version is **bit-identical to
  the previous one over all 672 corpus records**. The revert cost nothing and
  bought nothing; it was decided on one flaky sample.
- **The SID pair may name a statement the lift rewrites (FIXED).** `_lift`
  tested `k in (i, j)` before the `site.at` branch, so when the pair's first
  member *was* the lifted lo statement it emitted the lane half there and still
  dropped the pair's second member — leaving the hi SID register holding the
  previous frame's byte. Reached in `Beat_the_System` once coherence selection
  made `_site(4,8)` refuse and `_match` fall through to the degenerate pair
  (5,8), the SID lo store against a table hi store. The pair is now settled
  before the halves are, so that store carries the whole word. This was a latent
  bug in `_lift`, not a consequence of the selection rule; it is pinned by
  `test_framemath.py::test_a_sid_pair_naming_the_lifted_store_writes_the_whole_word_there`.

**The last six, each read off the 6502 (1 CLOSED, 5 measured negatives).** With
the 40 and the 4 `Counterforce` sites already settled above, these were the
residue no one had disassembled. `lifttrace capture` located each; the
disassembly decided it. Nothing here is a defect in the one aliasing rule.

- **`Dribbling` `$003F/$0058` — "an intervening statement writes the hi lane"
  (LIMITATION; CLOSED, 1 → 0).** `$A2B5 LDA $13,X / CLC / ADC $3F,X / STA $13,X /
  STA $D402,X / LDA $58,X / ADC #$00 / STA $58,X / STA $D403,X`. The first sweep
  lifts the `$13,X`/`$58,X` pair; the refusing site is the second sweep's SID
  mirror pair `$D402,X`/`$D403,X`, where `_rank` settles on lanes `$3F,X`/`$58,X`
  stepped by `$13,X` — the nearer bases, and the same word under `carry_comm`.
  The blocking statement is the *first* lift's own hi-half store to `$58,X`,
  which is **later** than the load of `$58,X` it is held to spoil, so nothing is
  hoisted past it. `_premise`'s hi-lane loop was the position-blind twin of the
  check §7.3 made position-aware above: `_hoist` already holds every read the
  word assignment leads with against the statements it is carried past, and the
  hi lane is one of those reads, so the loop could only ever fire on a
  non-hazard. It is removed and the class with it; `_writes` loses the index
  parameter only that call passed. The two shapes that *are* hazards — the hi
  lane reloaded after a store to it, and after a `zp,X` store that may reach it —
  refuse identically in both builds, as "an intervening statement changes an
  operand", which is the check that was doing the work all along.
- **`Wizball` `$0004/$0005` and `$0006/$0007` — "may alias the hi lane" (CORRECT
  as the pass stands).** `$4981 LDX $0D / LDA #$02 / CLC / ADC $04 / STA $E4,X /
  LDA #$00 / ADC $05 / STA $E9,X`, and `$4ABA` the same over `$0E`, `$06/$07`,
  `$F3,X`/`$F8,X`. The lanes are the song pointer; the destination is a
  loop-nesting stack. `STA $E4,X` is `zp,X`, so it reaches `$00..$FF` and covers
  `$0005` at `X = $21`. The program *does* bound it — `$4A0B LDY $0D / CPY #$04 /
  BEQ` caps the depth at 4, so the store only ever reaches `$E4..$E8` — but that
  is a value range over a zero-page counter, not a declaration, and no such
  machinery exists. The declaration route is measured and empty:
  `datadecl.declarations` carves **no zero-page region at all** for this tune, so
  `Regions.avail($00E4)` is 0 and the one tightening `span`'s modular rule could
  admit — a declared bound with `base + avail - 1 < 256`, where the wrap provably
  does not happen — would buy nothing here (MEASURED, no change taken). The third
  voice of the same driver, `$4BEE … STA $4708,X`, lifts: its stack is
  absolute-indexed, so `store_ref` resolves it clear of `$0008/$0009`.
- **`Are_Friends_Electric` `$17F9[y]/$10C8[x]` and `Flowing` `$1B3C[y]/$10B7[x]`
  — "the two lanes are indexed differently" (correctly classified).** `$13DC LDA
  $10C6,X / BCC $13F6 / CLC / ADC $17F9,Y / STA $10C6,X / LDA $10C8,X / ADC #$00 /
  STA $10C8,X` (`Flowing` `$1193 LDA $10B5,X`, step `$1B3C,Y`, hi `$10B7,X`). The
  real lanes are `$10C6,X`/`$10C8,X` under one index, stepped by `$17F9,Y` — an
  ordinary split-table site. `_cells` does put that pair in `_pairs` and `_fuse`
  does ask about it, but the **lo lane's load is in the dominating block**, above
  the `BCC` that separates the ADC arm from the SBC arm, so inside the interval it
  is the free local `a`: the e-graph has no fact `a == mem[$10C6,X]`, and
  `sub(word, pack(cells))` cannot cancel. That confirms the recorded suspicion —
  the store cells' own query does not cancel. What survives is the regrouping
  `(hi $10C8,X, lo $17F9,Y, step a)`, the same word with the step table wearing
  lo-lane shape, and it refuses on the one-shared-index premise §4 rung (d2)
  states. Closing these wants the lo lane's definition read out of the dominating
  block — a definition query across basic blocks, not an aliasing input.
- **`Allt_under_himmelens_faeste` — "a lane address is not a const base plus
  index" (LIMITATION, not closed).** `$0974 JSR $0B65 / PLA / CLC / ADC $FB /
  STA $0E1D,X / LDA $FC / ADC #$00 / STA $0E1E,X`: the lanes are the zero-page
  pointer `$FB/$FC` and the step is the byte pulled off the stack. This is **not**
  the `zp,X` form the modular-index work drove 16 → 0. `_fuse` does offer the
  coherent form — `_asked` returns `add hi=(cell $FC) lo=(cell $FB)
  step=zext(load $0100|sp)` — and `_site` then drops it, because `_back` cannot
  name the step: the e-graph folded the pull's address `((sp + $FF) + 1)` to
  `sp`, while `to_egg` keyed provenance by the unfolded spelling, so `prov` misses
  the extracted term. The two forms left both make the stack byte the lo lane,
  whose address `zext2(sp + $01) | $0100` is no `const base + index` — which is
  what refuses, correctly for the form it is given. Closing it wants provenance
  keyed by e-class rather than by term; `_back`'s own rule forbids the cheap
  alternative, since a load rebuilt out of the graph is a read made where it was
  not.

**Measured, HEAD vs the `_premise` collapse.** Refusals 52 → **51**, lifts
1523 → **1524**, merges 189, adjacent 668, SID pairs 365, Gate FP **649/649**,
canonical fixpoint **649/649**, `PYTHONHASHSEED=0` vs `=1` **0 of 672 records
differ**, and `lifttrace stable --runs 4` agrees on all 23 decisions of
`Dribbling`. Per (tune, lo, hi) over 1564 sites: **0 previously lifted now
refuse**, 1 newly lifts. The consumer partition §6 requires beside the gate:
emitted text 10197544 → **10197665** bytes (+121), raw `mem[` 9698 → **9699**;
byte-wide SID stores 772 and word 2899 both unchanged. Pinned by
`test_framemath.py::test_a_write_to_the_hi_lane_later_than_its_load_is_no_hazard`,
which refuses when the loop is restored, beside
`::test_the_hi_lane_reloaded_after_a_write_to_it_refuses_the_site` for the shape
that is a hazard.

### 7.5 The carry as control flow, and the definition that subsumes it (LANDED)

> Counts SUPERSEDED by §7.6: the shape matrix is now 594/16/0, and §7.5's
> attribution of 14 `adc-withzero-bidir` no-sites to the unobserved threshold
> was wrong. The corpus numbers here predate the address rule.

`tests/test_lift6502.py` enumerates the 6502 shapes that write one 16-bit
quantity as two bytes -- lane addressing (`zp`, `zp,X`, `abs`, `abs,X`, `abs,Y`)
x adjacent or split lanes x operation x step operand mode x how the carry crosses
x straight-line or a bounded accumulator reversing on a threshold. **610 shapes**,
Gate FP as the oracle, so no case states an expected lift, and it runs in seconds
where the corpus sweep cannot.

At the version that enumeration was written against it read **242 lifted, 368
never reaching a site at all** -- not refusals, so no census reported them and
none entered §7.3's ledger. Two causes, not eight. `_links` required an
`INT_CARRY`/`INT_LESS`/`INT_LESSEQUAL` in the hi statement's *value*, so a carry
crossing as control flow, as a shift bit or as a predicated increment was
invisible and a 16-bit operation with no carry at all could never qualify; and
`_word_form` matched `(op, hi lane, lo lane, step, mask)` for `op` in
`{add, sub}`, an idiom table behind a principled query. The architecture was
idiom filter, principled query, idiom reader.

**The redesign (docs/twobyte-lift.md §4).** `_links` is gone. Candidate pairs are
bounded by *program structure* -- the run of statements that write a value,
proximity in that run, a byte one value reads that the other reads, a pair the
program names that is two adjacent cells of one row -- and never by the shape of
an operator; `_linked` is a cost bound only, and cost is managed by how many pairs
are asked about. `_word_form` names no operator: it returns, for each byte pair
the extracted width-2 term packs, that term with every occurrence of the pack
standing as the word, admitted only where no lane survives outside it. One reader
takes a shift (`W*2`), a bitwise pair (`W & K`), a counter (`W+1`) and an add
(`W+step`) alike, and `_lift` substitutes the word local into whatever term came
back. Predicated updates are normalised to values before the query
(`_predicated`): `if c { INC x }` is `x = x + c` and the other arm takes `1 - c`,
sound because the condition is a comparison or a carry and so is 0 or 1, and
refused where the arm binds a register, escapes a name, or the store may read an
input or write an output. Either statement may hold the hi lane, since
`LSR hi / ROR lo` writes that one first. The general borrow is one Z3-proven rule
(`borrow_word`), as §4 requires, and the rest of what the operations needed is
the same law stated over the pack: it is a homomorphism for the bitwise ops
(`band_fuse`/`bor_fuse`/`bxor_fuse`, one builder), for the shifts and rotates
(`shl_fuse`/`rol_fuse`/`shr_fuse`/`ror_fuse`, two builders), plus three flag
identities (`carry_ult`, `eq_zero`, `carry_ones`) and the symmetry of the two
compare relations (`eq_comm`, `ne_comm`) -- a `CMP` leaves the literal on whichever
side the subtract had it, and every rule that then moves a constant step across the
equality wants it on the right. Every one is Z3-proven through `verify_rules`
before admission; 86 rule instances now prove.

**Measured over the 610 shapes: 242 lifted -> 552, 368 no site -> 34, 0 refused
-> 24.**

| operation | was | now | what is left |
|---|---|---|---|
| `ADC` | 192 lifted / 48 no site | 216 / 14 no site / 10 refused | 14 unobserved, 10 the `zp,X` wrap |
| `SBC` | 50 / 90 | 124 / 8 / 8 | 8 unobserved, 8 the `zp,X` wrap |
| 16-bit shift (`ASL`/`ROL`, `LSR`/`ROR`) | 0 / 16 | 16 / 0 | — |
| 16-bit inc/dec (`INC`/`BNE`/`INC`) | 0 / 16 | 10 / 4 / 2 | 6 the one naming defect |
| bitwise `AND`/`ORA`/`EOR` | 0 / 150 | 150 / 0 | — |
| `SLO` `RLA` `SRE` `RRA` `DCP` `ISC` | 0 / 48 | 36 / 8 / 4 | 8 `RRA`, 4 the naming defect |

The enumeration itself carried two 6502 errors, and both flattered the refusal
count rather than the lift: `ZP`/`ABS` were seeded `$F0`, at which `INC lo` cannot
wrap in eight frames, so the 16 counter shapes never ran the hi arm they exist to
exercise; and `DCP`/`ISC` were given `LDA #$00`, which makes the flag they leave a
test of something else. Those are the accumulator's fault, not the opcode's -- the
counters' flag is a compare against `A`, so only `$FF` for `DCP` and a set carry
over `A = 0` for `ISC` make it the lo lane's wrap. `build` now seeds each counter
at the value that wraps on the first frame and `_illegal` writes the real forms.

**The falsifiable prediction, answered.** 36 of the 48 undocumented-opcode shapes
lift and **no line of rung (d2) or of its rule set names SLO, RLA, SRE, RRA, DCP,
ISC or any opcode**: `git grep -inE "\b(slo|rla|sre|rra|dcp|isc)\b"` over
`framemath.py`, `eqlift.py`, `framefuse.py` and `frameproc.py` matches nothing,
and the only mnemonics in any of them are docstrings saying which 6502 sequence a
Z3-proven identity corresponds to. The decoder (`lifter.py`) names them, as the
machine model must. `SLO`'s statement list is byte-identical to `ASL`'s and
`SRE`'s to `LSR`'s, which is the prediction working exactly as stated: the value
graphs are those of the legal sequences they fuse. `DCP` and `ISC` needed no
clause either -- once the enumeration gave them their real accumulators, the one
thing between them and the existing borrow and carry laws was **orientation**:
`sub_eq0` hands a compare back as `eq(num, term)` while `addc_eq` and its
relatives match `eq(term, num)`. Stating that equality is symmetric took
`DCP` and `ISC` from 0 of 16 to 12 of 16, and it is one fact about a relation, not
a page in a catalogue. The 12 that remain are two causes:

- **`RRA hi / ROR lo` is not a 16-bit shift (8 shapes).** `RRA` is `ROR mem` then
  `ADC mem`, and the bit the following `ROR lo` takes in is the **ADC's** carry
  out, not the bit `ROR hi` shifted out: the value graph reads
  `(w1>>1) | ((carry($00,t0) | carry(t0, w0&$01)) << 7)`, where a 16-bit shift
  needs `w0 & $01`. It refuses because the pair does not compute `W>>1`, which is
  the design refusing over values, as it should.
- **4 are the naming defect below**, the `zp,X` half of the counter shapes.

**The residue is 58 shapes and three causes, one of which is a real defect.**
22 are the trace and not the pass: a branch the 8-frame window never takes leaves
its arm `unobs`, so the `SBC` borrow (8) and the bidirectional accumulator's
threshold (14) are never reached, the latter being the asymmetry §7.3 still holds
open. 8 are `RRA`, above. **The other 28 are one defect, and it is upstream of
rung (d2): the same cell named two ways.** Where the index is a known constant the
block converter folds it into some addresses and not others, so one lane arrives as
`mem[$1460]` and the other as `mem[zext(x) + $1460]` -- two terms for one byte,
which no e-graph can join because nothing has told it they are equal. It shows up
as a refusal (24) where the unfolded lane's range then covers the folded one and
`_may_disturb` correctly says they may alias, and as a no-site (4, the `abs,X` and
`zp,X` `DEC` shapes) where the two terms simply never fuse. This is the class §7.3
records for `After_the_War $0010/$0011`; **the fix belongs in address
canonicalisation, not in the lift**, and it is the one thing left that a correct
rung (d2) cannot reach on its own.

**The bidirectional accumulator: both arms lift in 65 of 100 shapes, up from 15.**
The 50 `wordstep` shapes were held by the missing general borrow and `borrow_word`
takes them. Of the 35 that still lift one arm only, the cause is the same
unobserved arm: the down arm runs only after the hi lane crosses `CMP #$08`,
which the seeded data reaches in the `wordstep` shapes and not in the `withzero`
ones. `apply_rung`'s `done` set is not involved — the two arms are different
statement lists, so they never share it.

**Measured over the corpus, HEAD vs the redesign** (682 tunes, `CAP_CPU=2400`;
see the cost note below for why the cap moved). Gate FP **649/649** and the
canonical fixpoint **649/649**, on the same 649 records HEAD reaches, with the
same 33 failures and all of them decompile-side. Rung (d2) lifts **1515 -> 2311**
and refuses **60 -> 120**; merges **189 -> 410**, adjacent **668 -> 953**, SID
pairs **365 -> 383**; byte-wide SID stores **772 -> 750** and word **2899 ->
2908**. The consumer partition §6 requires beside the gate: emitted text
10196681 -> **10222682** bytes (+0.25%), raw `mem[` 9699 -> **9703**. Bit-identical
across `PYTHONHASHSEED` 0, 1, 2 and 3 — 672 records, 0 differ on each pair — and
`lifttrace stable --runs 8` agrees on every decision of `Andy_Capp-The_Game`,
`Beat_the_System`, `Dribbling` and `Army_Moves`. One seed dependence was found
and closed on the way: `mask_hoist` gives a masked pack two spellings of one
cost, so `_asked` read the step off whichever the pool returned; it now takes the
cheapest of the class by the same total order `_fuse` uses for the word
(`_cheapest`), which is §7.1's lesson arriving in the step. The lifted
sites by what the word turns out to be: 1485 add, 509 sub, 198 the word unchanged
(a 16-bit copy), 107 `shl`, 56 `and`, 37 `or`, 34 `shr`, 5 `xor` — the 437 that
are not an add or a subtract are what `_links` never let the rung see. The
refusal classes are the ones §7.3 names, with two mirrored by the reversed
orientation: 46 "a lane address is not a const base plus index", 42 "the lo
destination may disturb the hi lane or the step" (5 more as "the hi destination
… the lo lane"), 20 "the two lanes are indexed differently", 4 "the lo
destination may alias the hi lane", 3 "an intervening statement changes an
operand".

**Two wrong lifts the gate caught, and the premise each was missing.** Both are
`_back` choosing a naming that does not mean at `i` what it meant where it was
recorded, and both predate the redesign in `_emittable` — `_links` merely kept
the pairs that reach them from forming. `80squares` `$140F/$1410`: the step is
`("loc", "a")` captured before the interval, and the interval's own `a = t4`
reassigns it, so `_hoist`'s wholesale inlining rewrote the step to the lo lane's
own result. `Army_Moves` `$E009/$EDF7`: `to_egg` keys a load by its address, so
the reads of `$ED8F,X` at `w1` and at `a1` are one term although a store to that
cell sits between them, and `_back` took the earlier, stale naming. `_emittable`
now refuses a naming the interval rebinds or that means any definition other than
the one in force at `j`, and `_stale` holds a naming recorded before `i` against
every statement between, by the same `_clobbers` predicate `_hoist` holds the
interval to. With both, all four tunes that failed gate FP pass it.

**Cost.** The rung asks every structurally admissible pair instead of one
pre-matched idiom, and the price is measured: `_fuse`'s cost is almost entirely
`egglog`'s `extract` per query pair (63.4s of 97.9s on `Arpeggio` at 40 frames,
against 4.0s of saturation), so it scales with pairs asked. One e-graph now serves
every partner of one statement rather than one per pair, which is worth about 20%;
the rest of the bound is `_linked` and `_pairs`. `Arpeggio`, the corpus's worst
case, goes **58.9s -> 95.0s at 40 frames (1.61x)**, with 936 query pairs in 66
e-graphs becoming 1281 in 202; at 200 frames it is 251s at HEAD.

**That cost is not free at corpus scale, and the measurement says so.**
`fuse_run.py`'s `CAP_CPU` is an `RLIMIT_CPU` per *worker*, not per tune, so 16
workers share a fixed CPU budget for the whole sweep. At `CAP_CPU=300` HEAD fits
(649 of 682, all 33 failures decompile-side) and this build does not: **251 tunes
return `MemoryError: cpu cap`**, and two workers crossed the hard limit mid-task
and were `SIGKILL`ed, which deadlocks `imap_unordered` exactly as that file's
docstring warns. The sweep below is therefore run at `CAP_CPU=2400`, where the cap
binds on neither build. Cutting the extraction per query pair is the next cost
work; part of the bound is already paid for in coverage, since `_pairs` offers a
cross pair of differing rows only where no same-row pair is on offer at all, which
costs 6 `adc`-`withzero`-`bidir` shapes whose step table is walked by the other
index register.

Records a tune's ordered rung-(d2) decisions — every candidate form `_fuse`
offered, the grouping `_site` chose, the refusal `_premise` gave, the statement
`_lift` emitted — so two builds are diffed at the first disagreement instead of
by re-running the corpus and comparing totals.

  capture <tune> <out.json>   ordered decisions + gate verdict
  diff <a.json> <b.json>      first decision two builds disagree on
  repeat <tune> --runs N      N fresh processes; splits the verdict if flaky
  stable <tune> --runs N      N hash seeds; diffs whole traces, not verdicts
  verdict <tune>              one gate verdict (what repeat forks)

A refusal names a predicate, not the addresses that tripped it, so `capture`
appends one event per class that needs its terms: `disturb` (the lo store's range
against every range it is held to reach), `clobber` (the statement blocking the
hoist, its range, and which operand it hit) and `alias` (`_match`'s own test,
which fires before `_premise` and would otherwise leave no event at all).

Prefer this to toggling a flag and re-running the corpus: a corpus delta says a
number moved, not which decision moved. Every finding in §7.1 and §7.3 above was
located by `capture` on two builds and `diff` — the statement list at the
disagreeing site, then the 6502 behind it — not by comparing totals.

### 7.6 The address a value names, and the row a constant lost (IN PROGRESS)

§7.5's residue named one real defect: the same cell under two names. The block
converter spells a constant address as its constant only inside the block that
sets the index, and binds it to a temporary wherever the address takes two
operations, so `zp,X` (`add` at width 1, then `zext`) always gets one. The lane
mode is not the discriminator; whether the converter materialised a temporary is.

**The rule, stated over values.** `frameproc._one_addr`, called from
`canon_addrs` at the end of `procedures()` over every `mem` address and every
`st` destination: an address whose value is a constant is spelled as that
constant. `_const_of` evaluates it through the definition chain -- a local reads
as the definition in force leaves it, and that definition's own locals at the
point *it* was written -- and refuses a `mem` read, so a value reached through a
cell stays with rung (d)'s spill rule and its dominance conditions. A syntactic
version of this rule folds only what the converter left inline and misses every
`zp,X` site; the value form is what reaches them.

**Measured over the 610 shapes (tests/test_lift6502.py):** 552 lifted -> **594**,
34 no site -> **16**, 24 refused -> **0**, gate FP and the canonical fixpoint hold
on all 610, emitted text 714610 -> **705926** bytes. 42 shapes moved and every one
moved toward lifting. `DCP`/`ISC` go 6 of 8 to 8 of 8, so 40 of the 48
undocumented-opcode shapes lift. The residue is 16 and neither part is this
defect: 8 `RRA` (§7.5, refused over values and correctly) and 8
`sbc-zpstep-branch` the 8-frame trace never reaches.

**§7.5 mis-attributed 14 shapes and this corrects the record.** The
`adc-*-withzero-bidir` no-sites were recorded as the trace never reaching the
`CMP #$08` threshold. They were this defect, in the *step* operand rather than the
lane -- every one has an `absx`/`absy` step -- and they lift with no change to the
enumeration. Both arms still lift in 65 of 100, so the bidirectional asymmetry
§7.3 holds open is untouched; what closed is that those 14 now have a site at all.

**One refusal regressed, and `_pairs` is not where it is decided.**
`test_liftgaps.py::test_a_lo_lane_loaded_in_the_dominating_block_refuses` lifted
as `lanes $1400/$1462`: `$1400` is the step table, paired with the hi lane.
Before the fold the step was indexed by `Y` and the lane by `X`, so `_pairs`'
same-row concession held them apart; after it both are constant cells and
`_rowbase` gives every one the row `None`.

**Giving a constant cell the declaration containing it as its row does not close
it, measured both ways.** The three cells are three declarations -- `$1400` size
96, `$1460` size 2, `$1462` size 256 -- so no pair shares a row, `(row or cross)`
falls through to `cross`, and the offers are bit-identical to today's. Forcing the
exclusion instead, so `_pairs` returns only the true pair `($1462, $1460)`, leaves
the site at `lo=$1400` regardless: lanes are read off the extracted form by
`_lane_addr`, and `_pairs` bounds what the e-graph is *asked*, not what it may
name.

**The premise missing is that two split lanes are one datum, and `role` alone does
not state it.** `_Site` spelled `split tables` wherever `not self.word`, with no
condition on the two declarations. `datadecl` does recover a lo/hi table pair
(`dl["role"], dh["role"] = ("lo", ht), ("hi", lt)`), but requiring that pairing
refuses far too much: 53 of the enumeration's split-lane decisions pair two
`role=None` tables and lift correctly, and no site in the suite or in a 12-tune
corpus probe reaches a declaration carrying a role at all. The separating fact is
`mut`. `$1400` is a wholly const table and `$1462` is written, and a datum the
program never writes is not the half of one it does.

**`framemath._one_datum`, the first clause of `_premise`.** Over the chosen site's
lanes, not `_pairs` over the offers: a split-lane site whose two declarations
disagree on being written is refused as `a const lane and a written lane are not
one declared datum`, unless `role` pairs them. A lane in no declaration is no
evidence either way and is left alone, which is the stance the other clauses take
on an operand they cannot name -- and it is what keeps the 284 enumeration
decisions whose lanes are undeclared lifting.

**Measured:** 594 lifted / 16 no site / 0 refused over the 610 shapes, unchanged
by the refusal, and the suite over the cached HVSC selection is 1622 passed, 2
skipped, 0 failed. Over 12 corpus tunes the rule fires on nothing: of 18
split-lane sites, 11 have both lanes written, 1 has both wholly const (which the
`role` rule would have wrongly refused) and 6 have a lane in no declaration.
Still unmeasured: the consumer partition (§6) and determinism across four seeds.

### 7.7 Byte-wide SID stores: the target, and why the metric is wrong

The goal is that freq, pulse width and cutoff are 16-bit everywhere, i.e. zero
byte-wide SID stores. **Zero is not reachable as `fuse_measure._BYTE_SID` defines
it, and the obstacle is the metric, not a proof gap.**

`_BYTE_SID` counts a byte store whose *base* is a lane. That conflates two things:
a byte-wide store to a register proven 16-bit (should be zero, and can be), and a
store to an **unidentified** register merely named after its base. `Also_Bad`
`$CA6A`, reachable from play via `$C475`/`$C90D`/`$CA6A`:

    $CA6A LDY #$17 / $CA6E STA $D400,Y / $CA77 DEY / $CA78 BPL $CA6E

is emitted as `sid.v1.freq_lo[y] = $00` and writes **every** SID register. At
`Y=4` it selects `ctrl`. Only 14 of the 29 offsets are lanes of a 16-bit register;
9 are 8-bit order-preserved (ctrl/AD/SR), 2 are `$D417`/`$D418`, 4 are
`$D419`-`$D41C`. No 16-bit reformulation of that store exists.

**`_lane_aligned` is the tight condition, not a conservative one.**
`framelog.canonical` keys by byte offset and partner-completes through `_OTHER`;
a widened store's record contribution equals the byte store's **iff
`other(r) == r+1`**, i.e. iff `r` is a pair lo. `held0` agrees the untouched
lane's *value*; the divergence is *presence* -- a spurious entry in an
order-preserved section, which §1.1 requires to survive verbatim. That is the
`v0.ord` corruption. So no better proof recovers anything along this axis, and a
stronger index analysis moves `$CA6E` from "index unproven" to "index proven and
proven not lane-aligned" -- byte-wide either way. **Proofs relabel this residue;
they do not shrink it.**

Next steps, in order:

1. **Split the metric.** Report "index provably not lane-aligned" apart from
   "index unproven". Only the second is work. This number is unmeasured and it
   decides whether anything below is worth doing.
2. **Merge, do not widen.** `_pair_at` already merges two adjacent lane stores at
   the same symbolic index with no alignment proof, because it invents no write
   (Commando's `$12E7`/`$12ED` pulse pair fuses with `Y` symbolic). §4.3 records
   925 SID pairs with no adjacent store site, ~577 writing both halves
   non-adjacently; each one rung (d2) brings together turns two byte-wide stores
   into one word store with no fact about the index at all. This is the only route
   that needs no new premise.
3. **The control-flow join.** `Defs` gives up at a merge, so
   `LDY #$00 / … / LDY #$07 / join / STA $D400,Y` refuses though both values are
   aligned. `test_liftgaps::test_a_lane_index_set_in_a_branch_arm_does_not_widen`
   pins it as the largest widening residue, and it is strictly easier than the
   interprocedural case.
4. **The parameter union** (§7.2's ~800). `stash@{0}` holds it and applies to HEAD
   cleanly -- its pre-image blobs are identical to HEAD's, the d2 redesign never
   moved `framefuse.py` or `frameproc.py`. Its `ENTRY` sentinel splits "no
   definition anywhere" from "a definition whose value cannot be read off"; of 940
   bare-local failures only 272 are genuine entry values, so reading them all as
   parameters would be unsound on 71%. **Two blockers before it lands:**
   `_const_of` (§7.6) must handle `ENTRY` -- `ENTRY is None` is False and
   `ENTRY[2]` raises, on every tune, and `git apply` is silent because the hunks
   are textually disjoint; and `model.ev_targets` (RTS-trick landings) must fold
   into `Calls.opaque`, or a procedure that is both a `JSR` target and an
   RTS-dispatch landing takes its union over the `JSR` sites only.
5. **Stop naming an unresolved indexed SID store after its base.**
   `sid.v1.freq_lo[y]` asserts a register the store need not touch. Ship only with
   (1), or it is laundering.

**Two unproven premises found on the way, both unrelated to the above.**
`_pair_at` accepts a hi-first adjacent SID pair, but `frameval`'s `stw` always
logs lo then hi; an indexed hi-first pair landing both cells in an order-preserved
section would reverse two `ord` entries. The corpus does not contain one, which is
observation, not proof. And `_BYTE_SID`/`_WORD_SID` match only named lvalues, so a
SID store whose address `addr_split` cannot resolve (the `zp,X` form, modulus
`$100`) is counted in neither column: the true byte-wide total is `750 + unnamed`.

### 7.8 The environment this branch was measured in

> SUPERSEDED where the host mounts `/scratch` and `/tmp` on local disk, which is
> the case as of the §7.6 measurement above: `/dev/sdb1` ext4, `import egglog`
> 0.4s, the hermetic suite 15s, the 610 shapes 19s at `-n 6`, the full suite with
> the HVSC cache 11m. None of the serialisation below applies there -- build the
> venv from PyPI and run `pytest -n` normally. Keep the rest for an NFS host.

`/scratch` is NFS serving **~1.5 file-opens/sec**, measured: 20 uncached small
files take 15s serially, and 64 files 32-way parallel take 35.8s -- concurrency
buys 1.35x, so it is global throughput, not per-connection latency. `import
egglog` costs 2-15 minutes on ~0.5s of CPU; one `pytest` run of five files took
35m58s wall for 34s of CPU. Consequences: run ONE python process at a time, never
poll a running job with `ls`/`du`/`wc` against /scratch (it spends from the same
budget), and expect a 610-shape run to cost ~9 minutes.

Copying the venv does not work -- 14,419 files at that rate is ~3 hours, and
parallelism does not help. **Building one locally does:** PyPI answers in 1.4s and
`/` is a local overlay. `/home/josh/di-venv` is created but pip is not yet
bootstrapped; `ensurepip` must run with **`TMPDIR` on local disk**, because `/tmp`
is another mount of the same NFS export. Put the repo on `PYTHONPATH` rather than
`pip install -e .` so no `egg-info` lands in the tree; a second editable install,
`pysidwizard` from `/scratch/anarkiwi/cbm/pysidwizard/src`, is on the same path.
Verify any new interpreter reproduces `shape_base.json` before trusting a number
from it.
