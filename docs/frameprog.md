# frameprog — the frame-program layer (specification)

frameprog is a **derived** artifact level above sidprog. It drops cycle
exactness: the only normative output is the **canonical frame projection**
of the SID write stream, one record per play-frame. sidprog remains the
cycle-exact ground truth (Gate C unchanged); frameprog is generated from the
committed model and verified against the projection of the walker's log.
Status: design for review; landed already: the projection + digi rule in the
pure log domain (`deity_informant/framelog.py`), the generator and reader
(`frameprog.py`/`frameproc.py`) and the reference evaluator plus Gate FP
(`frameval.py`, §6 M-FP1/M-FP2 for the measured extent) and rung (d)'s 16-bit
fusion (`framefuse.py`, §4.3 and §6 M-FP3). "MUST" is a gate. Measurements:
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
and the law is untouched; the tracker uses it to tell a declared-table read
from a computed value (docs/tracker.md §5).

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
set of candidate definitions. Measured effect at the tracker: docs/tracker.md §6.

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
declaration statically (docs/tracker.md §4c). A per-frame snapshot of the same
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
  words — and render freq/pulse/cutoff as u16 in the canonical section
  (presentational: the projection emits lo,hi adjacent). Premise per pair:
  provably written/consumed as a word — the datadecl pointer-pair machinery
  (`lo`/`hi` partner attrs) plus the paired-index zip invariant
  (follin-dispatch-study §4), every read using the half only inside
  `lo | hi<<8` shapes. Any lone-half access refuses that pair (stays split;
  per-pair, not per-tune). Gate: FP + a fusion proof record per pair.
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
  names the const-based reads as indexed accesses (16 tunes reach zero raw
  memrefs); what remains raw is base-less — pointer-pair derefs above all.
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
canonical fixpoint holds 645/645, Gate FP 646/646 and the tracker law 646/646,
all unchanged.

What this is *not*: a coverage step. Over the same 682 tunes the tracker's
partition is **byte-identical** before and after (interpreted 425657/1933877;
ctrl 10558/300573, ad 16890/112534, sr 19105/115963), because tracker reads the
statement trees and `frameval.eval_src`, neither of which an address *rendering*
changes. Two probes bound the alternatives: admitting impure load addresses to
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
fixpoint 649/649, both unchanged**, as the argument above requires; the tracker
law is likewise 649/649.

Of the **1296** state-pair candidates the model named — 1238 pointer pairs and 58
dispatch operand words — **584 fuse and 712 refuse**: 518 for a lone-half read,
191 for a half store with no adjacent partner, 2 for the write-order hazard and 1
with no word access at all. Per tune, of 649: **183 fuse every candidate they
have, 97 fuse some and refuse others**, 342 refuse all of them and 27 have no
state pair. Emitted text 9571703 → **9522243** bytes (−0.52%); raw `mem[`
occurrences are **unchanged at 10280**, because what fusion names is the pointer
*word* and the deref it feeds still has no const base for §4.2's indexed form to
name — precisely the residue §4(f) inherits.

The tracker's value partition over the same corpus is **byte-identical** before
and after — 752598/1942809 = 38.74%, freq 417490, pw 89850, ctrl 112502, filter
22301, sr 56019, ad 54436, triggers 300/306277 — with **zero** tunes moving in
either direction. That is the expected result and worth stating as one: the
tracker reads the SID stores' statement trees and `eval_src`'s per-register
provenance, and state-pair fusion rewrites neither.

**SID fusion is opt-in** (`frameprog.program(model, sid_fusion=True)`), and off
by default, because it is presentational and it costs the consumer. Of the
**2069** SID pairs a store site addresses, 916 fuse every site, 228 fuse some and
leave the rest split, and 925 have no adjacent lo/hi store site at all; the
tracker's value partition then falls to **699551/1942809 = 36.01%** on 250 tunes,
all of it in pw (89850 → 48346), freq (417490 → 407148) and filter (22301 →
21100), ctrl/sr/ad untouched. Gate FP and the fixpoint still hold 649/649, so
this is not a correctness cost.
The tracker keys its last-write-wins planes per register and identifies a lane by
the declaration the *store statement* names (docs/tracker.md §5); one word store
names one class, so the hi half's class loses its tree-named table and falls back
to searching every bank. That is a tracker question, not a fusion one, and it is
recorded here rather than compensated for.

| risk | disposition |
|---|---|
| Multi-call-per-frame / multispeed drivers | v1 class: frame = the play invocation, settled. v2/P-INT redefines the frame as the driver-cadence tick; the projection then applies per tick and the digi rule re-triggers (fast CIA volume writes). Deferred with v2. `play == 0` tunes are in the v1 class as of the handler entry (docs/decompiler-implementation.md §8.1): one handler invocation per frame, entered through a synthetic IRQ dispatch stub, so the frame is still the play invocation and Gate FP holds unchanged. |
| Digi / $D418 order | Closed by the class rule (§1.2): $D418 is last-write-wins; a >2-step collapsed volume sequence excludes with a precise diagnostic; 2-step frames collapse with a reported metric. Corpus: 0 exclusions; a digi tune MUST be added to exercise the path. |
| The two sides disagreeing on what a volatile input *is* (fixed) | Closed. `frameprog._INPUTS` declared $DC0D a nondeterministic input `cia_icr()` while the walker's `_VOL0` inlines that read as the constant 0 at block-compile time, never calling the pinning hook: `iota` could not record what the evaluator then demanded, so the first read of frame 0 faulted `past the pinned trace` — the one-model claim of §1.3 violated in the *set* of inputs, not in a value. 3 of 682 cached tunes (`4k_Digi_Competition_Entry`, `Chotmix`, `5_Channels_of_Feekzoid_Noise`), none digi-class. The declared set is now keyed on `structured._VOL`, so an address the walker cannot pin cannot be declared, and the evaluator resolves `structured._VOL0` to 0 exactly as the walker does instead of naming $D019 alone. Repaired frameprog-side by construction: the walker's constant-0 model is the v1 ground truth Gate C already verifies (decompiler-implementation.md §8.1), not an approximation to correct here. |
| Behavior genuinely dependent on cycle position of volatile reads | The law stays well-defined: both sides consume the pinned `iota` (§1.3). The residual risk is semantic, not soundness: such a frame program is faithful only modulo its input trace, and a standalone run beyond/without the trace faults rather than improvises. 3/140 tunes affected, osc3 only. |
| Unbounded play-time code copy | The one SMC shape with no state translation (§2). Refuses with a site diagnostic; zero corpus tunes. Everything else — operand, opcode toggle, vector, reads-as-data — is state by construction, with the faulting-default guard covering unobserved values. |
| Inline parameters after `JSR` (open) | The one remaining frameprog-attributable corpus failure: `C64_World`, `FrameFault: unobserved $4ED7 reached` at frame 189. `$4ED4: JSR $4921` is followed by four data bytes; `$4921` pulls its own return address into a pointer (`PLA/PLA`, rendered `mem[(sp+1)\|$0100]`), copies the four bytes through it and pushes the address back advanced by 4, so the `RTS` skips the data. `frameproc` renders that call as a `pcall`, which drops `ret $R` and makes `_Code.synth` push a stand-in address, and `frameval`'s `ret` returns through its shadow stack rather than the patched image — so the callee rewrites a stand-in and the return lands on the inline data ($4ED7), a site the trace rightly never observed. Fix direction: refuse the `pcall` promotion where the callee reads the stack at its own return slot (the `call ... ret $R` form already pushes the real address), not a second return path in the evaluator. Not the volatile-input divergence above; distinct cause, distinct fix. |
| Isomorphism near-misses (voice-3 noise/filter special cases) | Rung (e) refuses; copies stay per-voice, FP still holds. Tracked via the unification-rate metric; synthesized voice guards are forbidden (they fabricate structure the code does not have). |
| Forward `goto` into a later arm (fixed) | Closed. `frameproc`'s backward liveness sweep walks an `if`'s then-arm before its else-arm, so a `goto` was seen before its target label: the label's live-set read empty and locals live across that edge looked dead, letting `_inline` delete an update the target still consumed. Two faults, both needed: `_Flow.run` now iterates label live-sets to a fixpoint (as `_loop_head` already did for loops), and `_invis_name` treats an own-procedure `goto` as consuming whatever is live at its label instead of dismissing it — `_use_count` sees no textual use, so the consumer was invisible. |
| Stack-driven dispatch (`PHA`/`RTS`, `TXS`/`RTS`) | Closed. The surface serializes the transfer as a bare `ret` and the evaluator returns machine-faithfully through `sp` and the stack image. `PHA`-pushed targets were unrecoverable only because the passes treated `sp` as an ordinary local and eliminated its updates; `sp` is machine state (`call`/`ret` move it, pushed bytes land at addresses derived from it), so it is now exempt from pruning, from inlining and from the faint-assignment rule. `_fuzzgen.t_rts_trick` passes and `_FP_GAP` is empty. |
| Inline callee body entered by `call` (fixed) | Closed. A label some `call` targets is a mini-procedure: its exit returns to the call sites and may be re-entered, so a local it updates stays live. `_scan_list` collected `goto` targets and labels but never `call` targets, so the sweep treated the body's end as textual fall-through and `_prune` deleted a live update. `_Info.call_labels` now records them and both sweeps keep the machine set live from such a label onward. |
| Envelope dispatch under frame semantics | ADSR hardware state is not modeled at this level; audibility rests on the order-preserved ctrl/ADSR section (hard restart, test-bit, retrigger survive per §1.1). `envelope3()`/`osc3()` reads are pinned inputs; a driver branching on sub-frame envelope phase degrades to trace-faithful (previous row). |
| Sub-frame filter-mode transients | Collapsed by last-write-wins and declared non-normative (§1.2); measured benign (equal volume nibble) on all 17 multi-write tunes. |
| A rung that reads well and consumes worse | Rung (d)'s SID half is the case: fusing freq/pulse/cutoff moves no record (Gate FP 649/649) but costs the tracker 752598 → 699551 of 1942809 emits, because one word store names one register class where two byte stores named two (§4.3). Held opt-in and off by default, measured rather than compensated for; the fix belongs in whatever names a lane, not in the rung. Every later rung MUST report the consumer partition beside Gate FP for the same reason. |

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
  **Gate FP passes 649** and the tracker law 649. The 32 that never reach the
  gate are sidprog refusals (19 `runaway in init`, 10 `play $0000` installing
  no interrupt vector, 3 unmodelled `brk`); **one frameprog-attributable
  failure remains** — `C64_World`'s inline-parameter `JSR` (§5), one site of
  one class. The earlier record on this line, "frameprog-attributable failures
  are zero" over a 140-tune sample at 300-frame windows (2026-07-29, 123 pass
  and 0 diverge), was true of that sample and **false at 682**: the same sweep
  found 3 tunes faulting `iota(0, cia_icr, 0) past the pinned trace`, the
  §1.3 input-set divergence now closed (§5) — Gate FP 646 → 649, the tracker
  law 646 → 649, and the tracker's value partition 747709/1933877 = 38.66% →
  752598/1942809 = 38.74%, the whole delta being those three tunes' 4889 of
  8932 emits and every other tune's row byte-identical. The 140-tune sample
  scored 96 before the three liveness fixes of §5 (goto-into-later-arm,
  `call`-entered inline bodies, `sp` as machine state): 96 → 111 → 123, none
  regressed. Outstanding for M-FP2: the rung (a)-(c) proof records, and the
  upstream refusals are a sidprog question.
- **M-FP3 — fusion (d).** Landed (`deity_informant/framefuse.py`, §4.3 for the
  measurement): the state-pair fusion with a `structured.Proof` per candidate
  pair, and the SID register pairs analysed and recorded but rewritten only
  under `sid_fusion=True`. Gate: FP 649/649 and the canonical fixpoint 649/649,
  both unchanged over the 682-tune corpus. `tests/_fuzzgen` carries the
  `word_pair` and `lone_half` classes and `tests/test_framefuse.py` the
  synthetic refusals — lone half, unpaired half store, write-order hazard — plus
  the mutation evidence that a wrongly fused pair moves the record (non-adjacent
  halves, swapped halves, a hazard fused anyway). Outstanding: the rung (a)-(c)
  proof records are still M-FP2's debt, and whether SID fusion ships by default
  is a tracker decision, not a frameprog one.
- **M-FP4 — unification (e).** Gate: FP; isomorphism records; voice-3
  near-miss refusal exercised synthetically; unification-rate metric.
- **M-FP5 — the frame function (f).** Gate: FP; FP-complete tunes reported
  (no unproven raw `mem[expr]`); the Commando-family excerpt shape achieved
  on at least the index-looped drivers; per-tune rung recorded in the build
  report.

Gate FP is the only correctness law at this level; no milestone may weaken
it, and sidprog's Gates A/C/L/S are untouched throughout.
