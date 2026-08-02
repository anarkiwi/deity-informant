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
  §4.3 for the measurement). A driver maintaining 16-bit state in 8-bit
  registers writes the add twice — the lo lane, then the carry it propagates
  into the hi lane — so rung (d) sees two byte stores with statements between
  them and refuses. Rung (d2) reads that pair of updates as the one 16-bit
  add/sub it is: the two written values are concatenated `hi<<8 | lo` and the
  admitted rule set is asked what that word is; a site lifts where the answer
  has lane shape. The **sources** decide the lift — two byte lanes at a const
  base plus one shared index, linked by a carry, are one 16-bit quantity
  wherever their halves are then written — and every naming the lift emits must
  still hold where it emits it, with no intervening statement writing the hi
  lane or changing an operand. The **destinations** decide nothing about the
  lift, only whether the two writes collapse into one `u16` store, which needs
  them adjacent; a hi half stored elsewhere still lifts (the CyberTracker case
  in §5). The lemmas are Z3-proven in `eqlift.RULES` (`carry_fuse`,
  `carry_fuse0`, `borrow_fuse`, `mask_hoist`, `add_to_sub`, `num_narrow`).
  Gate: FP + a proof record per site.
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
  site, unconditionally. Gate: FP 649/649 and the canonical fixpoint 649/649,
  both unchanged over the 682-tune corpus. `tests/_fuzzgen` carries the
  `word_pair` and `lone_half` classes and `tests/test_framefuse.py` the
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

- **Only unindexed lanes widen.** `mem[$D400 + y]` is a store to register `y`,
  not to freq; widening it writes whatever cell follows, which corrupted
  `Also_Bad` and `Aiginas_Prophecy` in `v0.ord`. That leaves ~1058 byte-width
  SID stores, all indexed (`[y]` 622, `[x]` 326; 819 of them freq lanes).
- **The rule for indexed lanes** is a per-site dataflow fact about the index's
  reaching definitions, never a property of its name. `Also_Bad` uses X and Y
  for *both* the voice offset and the voice number, swapping roles between
  `JSR $C098` (X = 0/7/14) and `JSR $C1E3` (Y = 0/7/14), and loads Y from a
  table at `$C27D`/`$C0F1` where no constant is provable. So: widen iff every
  definition of the index reaching that store is a constant lane-aligned for the
  pair — a multiple of 7 and ≤ `$0E` for the voice pairs, 0 for cutoff.
  `framemath._Env.at` already answers "the definition in force here".
- **A constant *table* counts.** Commando's index is `LDY $14B5,X` with
  `$14B5` = `$00 $07 $0E`; a rule demanding immediate constants would refuse
  Commando's own pulse stores.
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

Root causes traced by instrumenting `FF._addr_split` and the aliasing
predicates, and by reading the 6502 at the refusing sites. The trajectory,
re-measured against the deterministic gate of §7.1: **77 → 76 refusals** while
lifts went **1434 → 1452** and merges **131 → 181**, Gate FP 649/649 throughout.
The classes moved as well as the totals — "lanes indexed differently" is now 8
(was 22 before the §7.1 fix) and "may alias the hi lane" 6 (was 14) — so the
counts recorded before §7.1 was settled should not be compared against these.

**The dominant class, triaged.** "The lo destination may disturb the hi lane or
the step" is 43 of the 76, and `lifttrace capture` now records the store's range
against every range it is held to reach, so the class splits by what actually
tripped it rather than by which predicate reported it:

- **33 — an unresolved *read* address**, and one shape: the step is a deref
  through a zero-page pointer pair (`Arpeggio`, `Danger_Mouse`,
  `Data_Data_Data_Data` are the same driver repeated; `$00F0/$00F1`, `$00F4/$00F5`,
  … per voice). These are **genuine** hazards as the pass stands. The deref is
  defined *inside* the lift interval, so `settle` inlines it and the load really
  does move above the lo store; with no bound on the pointer, a store to a
  constant byte cannot be excluded from it. The bound exists — it is exactly
  rung (f)'s proven target block set (`frameptr`'s `blocks()`) — but rung (f)
  runs *after* rung (d2) and is naming-only, so `_disturbs` cannot ask. Closing
  this class means making that block set available to the aliasing test, which
  is a rung-ordering change and wants its own commit: `frameptr.analyse` keys
  its sites by address *expression*, and rung (d2) rewrites expressions.
- **10 — an undeclared span over a differing index.** The store's span falls
  back to the whole 256-byte register range because `Regions.avail(base)` is 0,
  and then any constant read within 256 bytes collides. `Beat_the_System` is
  representative: `$213C,X` and `$2136,X` are per-voice 3-entry arrays that
  `datadecl` does not declare (it declares `$211E` and `$214F`, size 3, in the
  same driver), so a read of the scalar `$2165` — 41 bytes above — is held to
  alias. The index is bound by a `loop`, not a `for`, so no local reaching
  definition bounds it either; the route is `datadecl` coverage, not the
  aliasing rule. `_overlaps` already does the right thing once a span is right:
  the same-index lane read at `$2136,X` is correctly proved disjoint.

Neither class is a defect in the rule that was consolidated; both are missing
*inputs* to it. The counts above are the re-measured trajectory, not the
pre-§7.1 ones.

- **Unresolved lane address** feeds three classes at once — "a lane address is
  not a const base plus index", "the lo destination may alias the hi lane"
  (`_may_disturb` with `base` None), "the lo destination may disturb the hi lane
  or the step" (`_disturbs` with a ref base None). The dominant unresolvable
  form is `zext2(x + K)`, the 6502 `zp,X` wrap: the add is at width **1**
  because the wrap is inside the byte, and `frameproc._index_of` demands
  `INT_ADD` at width 2 with a base ≥ `$100`, so it declines to name it. Fix: one
  extra clause naming it as base `K`, index `x`, modulus 256, plus a straddle
  guard **at the access site** (only the access knows its width) refusing a
  2-byte access that can reach `$FF`, since a word read at `$FF` takes `$0100`.
  This is not a case the lift finds hard; it is a case one predicate will not
  look at. A separate normalisation pass would be the wrong shape — `_index_of`
  is the single point `frameproc`, `framefuse._addr_split`, `framemath` and
  `frameptr` all ask, so fixing it once fixes four consumers. It changes naming
  for every consumer at once, so it wants its own commit and its own gate run.
- **Lanes indexed differently** has two causes. One lane may be an unindexed
  zero-page cell (`American $B501[..]/$00FE`, `Endless_Sands $181F[x]/$00FC`),
  which has a constant address and so no row to disagree about — index equality
  is needed for the *merge* decision (which already demands adjacency), not for
  the lift, which emits `FF._pack(src_lo, src_hi)` and assumes no adjacency. The
  rest are genuinely different tables (`Antitrack_01 $166B[y]/$15CB[x]`).
- **Antitrack_01 is a mis-grouped extraction, not a bad site — and coherence
  selection does not reach it.** `$13F4 LDA $166B,Y` is the *step*; the real
  lanes are `$15C8,X` and `$15CB,X`, parallel per-voice arrays sharing one
  index. `hi<<8` has a zero low byte, so `|` is `+` and
  `(hi<<8 | step) + lo == (hi<<8 | lo) + step` — proved by
  `add_comm`/`add_assoc`/`or_zero`. `_lanes` only checks that both operands are
  byte-wide `cell`/`load` nodes; nothing requires them to be *related*, so
  `_fuse` can offer a grouping that pairs the hi lane with the step, and
  `_word_form` accepts it. `_site` now weighs every offered form and prefers the
  one whose lanes are the statements' own cells, but at this site **the correct
  grouping is never offered**: both extracted variants collapse to the
  mis-grouped shape, so there is nothing to prefer. Choosing better among
  extracted forms cannot fix this; the form has to be *asked for*. The fix
  direction is to query the fused e-class for an equality against lanes drawn
  from the program's own provenance — `site.addr`, as merge does — instead of
  reading lanes off whatever term came back. That is deterministic by
  construction, since the candidate lanes come from the program rather than from
  extraction. The site refuses today ("lanes indexed differently"), so nothing
  unsound is emitted; a wrong pairing driving a wrong **merge** remains
  unguarded and wants a mutation test. This is worth more than one site: **most
  of the 8 "lanes indexed differently" refusals carry the same signature** —
  lanes implausibly far apart and differently indexed (`Antitrack_01
  $166B[y]/$15CB[x]`, `10_Days_and_No_Longer $10A0/$1F25`, `Counterforce
  $2015/$1ED2` three times), which is what a step named as a lane looks like. A
  query would take the class and the last extraction dependence together. The
  obvious form of it — extract `sub(h, pack(hi_cell, lo_cell))` as the step —
  must be checked against the cancellation rules actually admitted before it is
  believed, or the lift emits `pack(lanes) + (h - pack(lanes))`: right, and
  unreadable.
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

### 7.4 tools/lifttrace.py

Records a tune's ordered rung-(d2) decisions — every candidate form `_fuse`
offered, the grouping `_site` chose, the refusal `_premise` gave, the statement
`_lift` emitted — so two builds are diffed at the first disagreement instead of
by re-running the corpus and comparing totals.

  capture <tune> <out.json>   ordered decisions + gate verdict
  diff <a.json> <b.json>      first decision two builds disagree on
  repeat <tune> --runs N      N fresh processes; splits the verdict if flaky
  stable <tune> --runs N      N hash seeds; diffs whole traces, not verdicts
  verdict <tune>              one gate verdict (what repeat forks)

Prefer this to toggling a flag and re-running the corpus: a corpus delta says a
number moved, not which decision moved. Every finding in §7.1 and §7.3 above was
located by `capture` on two builds and `diff` — the statement list at the
disagreeing site, then the 6502 behind it — not by comparing totals.
