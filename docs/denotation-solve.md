# The denotation solve — closing the register and pointer residue together

Status: proposal for review, 2026-08-13. It replaces the backlog's "long game"
bullet (register-model-lift-impl.md, the 2026-08-13 position) and §4.4/§4.6 of
frameprog.md as *work items*; their specifications of the artifact and Gate FP
stand unchanged.

## 1. The finding: the two residues are one residue

The claim under review is that machine-register elimination and pointer
elimination are independent rungs. They are not: each one's premise mentions the
other's conclusion. Three exhibits, all from `out/Commando.frameprog.txt`
(Hubbard, `play $5012`, full Songlengths, Gate FP green).

**Exhibit A — one machine name carries five quantities.** In the single emitted
procedure, `y` denotes, at different sites:

| site | `y` denotes |
|---|---|
| `y = idx_550C` … `y = (y - $01)` | a portamento repeat count |
| `y = m_54EB` … `sid.v1.ctrl[y]` | the SID voice displacement, `m_54E8 = 00 07 0E` |
| `y = pos_54EF[x]` … `*ptr_005F[y]` | a pattern row cursor |
| `y = (y1 << $01)` … `m_5428[y]` | a row of the pitch table |
| `sid.reg[(zext2(y) + $0001):2]` | a register-file byte displacement |

A name that means five things cannot be typed, roled, demoted or bounded. Every
downstream premise that reads "the local's range" (`streams._idx_hi`, and through
it rung (f) premise 3 and `datadecl`) is deciding over a machine location, not a
value.

**Measured at A1, the exhibit reads low.** The five above are the *denotations*
this table read off the emitted text; over the settled statement trees `y`
resolves to **eight def-use webs** (`tests/test_frameweb.py`), because the voice
displacement `m_54EB` is defined at four sites no read joins and the pulse-width
table row `y = m_5518` — which the text shows folded into `m_5591[m_5518]` — is a
sixth quantity the text never spells. In the same procedure `a` is **9** webs and
`x` is **3**. The exhibit is the argument's floor, not its ceiling.

**Exhibit B — the pointer's index is an unnamed register web.** The two pointers
are reloaded, never computed:

```
ptr_005D:2 = m_56F9[x]:2          ; pointer table, indexed by the voice
a = *ptr_005D[pos_54EC[x]]        ; the orderlist byte -> the pattern number
ptr_005F:2 = m_5711[a]:2          ; pointer table, indexed by that byte
w23 = *ptr_005F[y]                ; the pattern row
```

To demote `ptr_005F` from a 16-bit pointer to what actually varies — the entry
index into the declared table `m_5711` — that index needs a name. Its name today
is `a`, an architectural register whose web is unnamed. **The pointer cannot be
lifted until the register web is a value; the register web cannot be typed until
the deref says what it indexes.** That is a fixpoint, not a sequence.

**Exhibit C — the voice loop is the third participant.** `for x in $02..$00` is
the voice index; the per-voice state is declared `table pos_54EC[3] mut 0 1 2` in
`data { }` rather than as the `[3]` voice arrays §2's domain names; and the SID
writes go through `y = m_54EB = m_54E8[x] ∈ {0,7,14}`. So `x`, `y`, the `[3]`
tables and both pointers are one object — a per-voice sequencer record whose
fields are (orderlist position, pattern index, row) — fractured across four
spellings. Rung (e) excused this shape explicitly ("index-looped drivers …
are already parameterized and need no rung-(e) work"); they are parameterized
*by a machine register*, which is exactly the residue.

**The corpus says the shape is the norm, not Commando's quirk.** Measured at full
coverage — all 624 cached artifacts at full Songlengths, L0
([denotation-solve-baseline.md](denotation-solve-baseline.md)); the drafting
figures from a 1,200-artifact stale-cache sample are superseded and agreed
within a point on every prevalence:

| measure | count |
|---|---|
| artifacts indexing a SID write by a machine register | 539 of 624 (86.4%), 3,788 sites |
| artifacts declaring a `[3]` table holding `00 07 0E` | 271 (43.4%) |
| `for` headers whose induction variable is a machine register | 239 of 239 |
| raw `mem[` sites | 8,609 |
| `*deref` sites | 2,180 |
| `stream … via ptr` declarations | 12,424 |
| median `arch` per artifact | 303 |
| procedure body share of emitted bytes | 21.2% |

**And the ladder's own numbers already record the deadlock.** §4.6's provenance
rule resolves **1 site of 3,929**; **365** refuse for naming "2 or more target
blocks" (2 blocks at 48 sites, 3 at 36, 4 at 68, 5–9 at 78, ten or more at 135).
A pointer that ranges over 82 blocks is not unanalysable — it is an orderlist,
and the block is selected by an index the analysis refuses to name. Rung (f)
resolves 366 of 3,929 deref addresses (9.3%); its premise 4 refuses **every
pointer in a tune** when one store address cannot be placed. That is the
"expensively passes through" failure mode: a whole-tune bailout triggered by a
single unnamed value.

## 2. Why the ladder cannot close it

Each rung is a one-shot premise over the text as it stands, and each premise
mentions a fact another rung produces:

- rung (d) fuses a lo/hi pair into a `u16` **if** the halves' sites agree — but
  what the pair *is* (an address, a counter, a lane) is rung (f)'s conclusion.
- rung (f) resolves a deref **if** the row index is bounded by one row — but the
  bound is read off a local whose web is the register pass's conclusion.
- the register pass names a web **if** its sites agree on a width — but the width
  of a pointer half is rung (d)'s conclusion.
- `roles` classifies a cell **if** its update shape matches — and "licenses
  nothing", so no rung may consume it.

Mutually recursive facts computed by independent one-shot passes have exactly
two outcomes: each refuses on the others' un-computed half (today: `zero_arch`
2 of 624, deref-src 1 of 3,929), or a rung asserts a fact it has not proved.
The ladder correctly chose refusal. **The defect is the decomposition, not the
rungs' honesty.** A fixpoint is the right engine, and one is already in the tree.

## 3. The proposal: one denotation solve

Replace the per-rung premises with **one monotone analysis whose unknown is what
each value denotes**, solved to a fixpoint over the value+memory e-graph that
`eqlift`/`eqlift_mem` already build, and make emission a function of the
solution. The mechanism is not new to this codebase: address disjointness is
already an **e-class interval analysis** on that graph
(`eqlift_mem.py`, eqlift-adoption §2), and §7's backward reachability from SID
sinks already recovers table meaning (`eqlift_annotate.py`, today scheduled for
deletion with `emit_mem` rather than for wiring into the artifact). The
denotation lattice is a second e-class analysis on the same graph, joined on
merge, so it strengthens as the algebra normalizes.

### 3.1 The lattice

```
⊥
const(c)                 a compile-time constant
lane(a, b)               affine in the frame's channel index: a + b·v
idx(T)                   an entry index into declared table T
row(S, e)                a row index into block-set S, bounded by e
addr(S, r)               an address: base ∈ S, row denotation r
byte(src)                a data byte from declared source src
⊤                        unknown
```

Join is pointwise, with two constructor-crossing rules that carry the whole
argument:

- `const(c1) ⊔ const(c2) = lane(a,b)` where the constants are the loop's own
  affine image (`{0,7,14}` at `v ∈ {0,1,2}` is `lane(0,7)`), else ⊤.
- `addr(S1,r) ⊔ addr(S2,r) = addr(S1 ∪ S2, r)` — **the block set is allowed to
  grow.** Where `S = {T[k]}` for a declared `lo/hi` table `T`, the varying part
  is `idx(T)`, so the pointer demotes to that index instead of refusing. This one
  join rule is what converts §4.6's 365 "2 or more target blocks" refusals into
  resolutions, and it is only sound because the selector has a name — which the
  web split (§3.2) supplies.

Finite height: `S` is a subset of the declared blocks, denotation nesting is
bounded at `addr → row → idx`, everything else widens to ⊤. Standard worklist
solve; ⊤ is always sound, so termination never costs correctness.

### 3.2 Webs before names

Before the solve, the analysis unit stops being a machine location: compute
def-use webs over `frameproc`'s statement graph and give each web its own
identity (the generalisation of the prototype's `name_locals`, whose point is
*not* the spelling). Exhibit A's `y` becomes five webs, each of which the solve
can type independently. A web that is live at procedure entry, defined by an
opaque call, or whose sites disagree after the solve stays ⊤ and keeps its
machine spelling — visibly, with a number, not silently.

**This is the anti-rename commitment.** A web whose denotation is ⊤ must keep the
machine name. Renaming `a` to `t7` is forbidden by the metric in §5: `arch`
falling while `⊤-sites` holds steady is a failed landing, not a passed one.

### 3.3 Transfer rules

From the idiom catalog's own rows, stated once over the e-graph rather than per
rung: a load from a declared table at `idx(T)` yields that table's element
denotation; a fused lo/hi read of a `lo T` pair yields `addr(blocks(T), row(·))`;
`addr + row` is `addr`; a store into a `[3]` table at `lane(·)` is a per-voice
field update; a SID store at `lane(0,7)` is a voice-resolved write. Every rule
carries the same governance as today — Z3-proven over QF_BV / the array theory,
declared extents read from `mem0` and never the trace, observed-primary guards
on anything asserted about control.

### 3.4 What emission becomes

One emitter reading the solution, with no per-rung spelling decisions:

- `lane` ⇒ the voice record: `[3]` state arrays, `for voice in …`, `sid.v[voice].ctrl`.
- `idx(T)` ⇒ the pointer state field is replaced by its index; `*ptr[i]` becomes
  the two-level declared access `T[k][i]` (one new grammar production, its reader
  and its evaluator). Where `|S| = 1` it degenerates to today's `B[i]`.
- `row(S,e)` ⇒ a bounded cursor with its extent declared.
- ⊤ ⇒ exactly today's spelling, guarded, counted.

## 4. Landings, each with its gate and its kill criterion

Every landing gates on the full suite, `gate_sweep` at full Songlengths
(624/624/624, zero divergences and zero refusals) and `emit_identity`.

1. **L0 — instrument.** Denotation census and the §5 metrics over the corpus, no
   engine change. Produces the honest baseline the rest is measured against.
   *Gate: emit-identity byte-identical.* **Taken**: `tools/denotation_l0.py`,
   `docs/denotation-solve-baseline.md` (census and quotient over all 624 cached
   artifacts, re-lift over 18 tunes of the nine largest families).
2. **L1 — webs before names.** The analysis unit becomes the web; the printer
   still spells registers. *Gate: emit-identity byte-identical* — a pure
   refactor, and a strong one, because any text movement is a bug.
   **Taken**: `frameproc.ProcWebs`/`webs`/`web_counts`, `FrameProgram.webs()`,
   `tools/web_census.py`, `tests/test_frameweb.py`; width normalisation re-keyed,
   the liveness and the block converter left name-keyed with the reason in the
   docstring. Census in [denotation-solve-baseline.md](denotation-solve-baseline.md) §4.
3. **L2 — the solve.** Lattice, transfer rules, worklist, evidence records. No
   emission change. *Gate: emit-identity byte-identical; census moves off L0's
   baseline.* **Kill criterion: if fewer than 60% of value sites and 80% of
   deref sites reach a non-⊤ denotation, stop here and report** — the shape is
   not in the corpus and no emitter change will find it.
   **Taken, and it STOPS**: `deity_informant/denote.py`, `tools/denote_census.py`,
   `tests/test_denote.py`, [denotation.md](denotation.md); the `opaque` refusal
   bounded by `frameproc.call_summaries`. Emit-identity is byte-identical.
   Measured corpus-wide: **value sites 29.31%** (70,884 of 241,817) against the
   60% floor, **deref sites 83.55%** (85,075 of 101,825) against the 80% floor.
   The deref half clears; the value half does not, and it is §5's primary
   number, so L2 reports rather than proceeding. The diagnosis is in
   [denotation-solve-baseline.md](denotation-solve-baseline.md) §5: 46.1% of the
   ⊤ value sites are the `entry`/`opaque`/`call` refusals §3.2 already states —
   an entry-live web is a *parameter*, which is an interprocedural fact this
   landing does not compute — and 38.7% is scalar arithmetic and the cells it
   feeds, for which §3.1's lattice has no constructor **by design**. Neither is
   "the shape is not in the corpus". The mechanism the plan was built for does
   work where it applies (all 14,389 declared tables type, `idx` reaches 28,270
   sites, both Commando exhibits solve), but only 28.50% of the derefs that go
   through a lifted pointer get a declared block set, so L4's expected reach is
   661 sites and not 2,319. **L3–L6 are not started.**
4. **L2b — the vocabulary extension, taken instead of proceeding.** L2's ⊤ is
   two populations the solve excluded by construction, and both are *the same
   operation at a larger scope* (§7.2), so closing them completes the mechanism
   rather than adding a rung:
   **(a) interprocedural scope** — an entry-live web is a **parameter**, so the
   solve extends across the call graph with procedures as nodes: a parameter's
   denotation is the meet over its call sites' arguments, a return's flows back
   to the callers, recursion and unresolvable transfers widen to ⊤. This is
   46.1% of L2's ⊤ value sites (`entry` 41,737, `opaque` 32,891, `call` 4,148)
   under one mechanism.
   **(b) scalar constructors** — 38.7% of L2's ⊤ is counters, accumulators,
   carries and compares, for which §3.1 has no constructor. They are
   `⊤-unvocabularised` (§5), and the vocabulary that names them already exists
   and licenses nothing: `roles.py`'s `cursor`/`accumulator`/`counter`/`flags`.
   Folding roles in **as lattice constructors** makes one classification where
   there are two, and it is on A5's deletion list either way — a role that
   licenses nothing is exactly the "computes the fact and refuses to consume it"
   shape §2 diagnoses.
   *Gate: emit-identity byte-identical; the §5 census re-measured with ⊤ split
   into refused and unvocabularised.* **What it decides:** L3 and L4 proceed
   together when pointer-deref reach clears **60%** (L2 baseline 28.50%) and the
   `⊤-refused` half falls materially; below that the finding is about the
   analysis, not the emitter, and it is reported rather than emitted around.
   **Taken, and L3/L4 DO NOT PROCEED**: `denote._CallGraph`/`_seed_param`/
   `_seed_ret` (the scope), the `flags`/`acc`/`count`/`pred` constructors and
   their rules (the vocabulary), `Solve.klass`/`roles` and the census split,
   [denotation.md](denotation.md) §§7–8, `tests/test_denote.py`. Emit-identity is
   byte-identical (`7a63a89f…`, 28,365,174 bytes, 0 refused).
   **Both gate conditions fail, and they are reported rather than moved.**
   *Pointer-deref reach is 661 of 2,319 = **28.50%**, exactly the L2 baseline*
   against the 60% floor. *`⊤-refused` **rose**, 113,160 → 116,719 (+3.1%)*; what
   fell is `⊤-unvocabularised`, 57,773 → 37,749 (−34.7%), and total ⊤,
   170,933 → 154,468 (−9.6%). Value-site reach goes 29.31% → 36.12%.
   That the refused half rises is the landing's own finding and not a regression:
   **a vocabulary extension converts unvocabularised ⊤ into refused ⊤**, because
   once a shape has a word what is left is an undischarged premise. The
   interprocedural scope moves `entry`+`opaque`+`call`+`recursion` 78,776 →
   69,034 (−12.4%), typing 1,212 parameter webs and 2,087 returns; `opaque`
   (escaping and unenumerable transfers) is untouched by design, since this entry
   itself says those widen to ⊤. The pointer number does not move because the
   premise it waits on is not a lattice one: the 1,658 ⊤ pointer derefs go through
   262 cells whose definitions are 303 `word-pack` and 335 `lane-insert`, **219 of
   the packs do name a declared `lo`/`hi` pair and none fails for want of one**,
   and the same cells are also patched one lane at a time — a shape whose transient
   makes the pair's block set unsound as `S` without an ordering proof, so
   `addr(S,⊥) ⊔ ⊤ = ⊤`. What the split did buy there is legibility: L2 classed
   1,481 of those 1,658 sites `⊤-unvocabularised`, L2b classes 252. Census and
   diagnosis: [denotation-solve-baseline.md](denotation-solve-baseline.md) §6.
5. **L3 — the voice record.** `lane` denotations become `[3]` state arrays and
   `sid.v[voice]`; the induction variable is the voice. First landing whose text
   moves. *Gate: Gate FP + reviewed 25-exemplar diff.* Expected reach: the 526
   of 1,200 artifacts carrying the displacement table, the 955 indexing SID by a
   register, and all 432 register-spelled `for` headers.
6. **L4 — pointer demotion.** `addr(S,·)` state fields are replaced by
   `idx(T)`; derefs become two-level declared accesses; the grammar gains the
   production and `frameval` learns to resolve it. **This is the first change
   that moves the state shape, so "the text cannot move" is no longer the
   argument — Gate FP is.** Expected reach: the 365 sites §4.6 refuses for
   naming 2+ blocks, plus the `via` stream declarations, which become the block
   table's rows.
7. **L5 — subsume and delete.** The rung modules the solve replaces come out:
   `frameptr` (594), `ptrlift` (114), `ptrcert` (1,157), `ptrextent` (183),
   `framefuse` (831), `framemath` (954), `roles` (245) — ~4,078 lines of premise
   code, plus the bespoke bound in `streams`. A landing that adds the solve and
   keeps the ladder beside it has failed: two analyses that can disagree is the
   condition this proposal exists to end.
8. **L6 — close.** One analysis, one refusal class (⊤), one emitter; the
   backlog's four "reducers" are consequences, not entries: the multi-reader
   forward is a shared denotation (a named value needs no synthesized
   definition), per-frame demotion is a denotation dead at the frame boundary,
   the SMC-operand evidence is `idx` on a dispatch cell.

## 5. The metrics, chosen so they cannot be gamed

**Amended after L0 and L2, because two of the three quantitative criteria failed
on their specification rather than on the work.** Criterion 3 measured the
witness backend (§7.3). L2's value-site fraction diluted its denominator with
every scalar site the lattice was never drawn to type, while its deref fraction
inflated its own with every declared-table address the engine could already
place — the two errors run in opposite directions, which is what a denominator
nobody stated looks like. The discipline that follows is the fix:

> **Every metric names its denominator and states what it predicts.** A number
> that predicts nothing about the next landing is not a gate, whatever it
> measures.

And the primary number splits, because the two halves mean different things and
only one of them is a cost:

- **`⊤-refused`** — sites the solve *could* type but will not, because a premise
  is undischarged (an entry-live web, an opaque transfer, an unplaceable store).
  This is what soundness costs, and it is the number a landing reduces.
- **`⊤-unvocabularised`** — sites for which the lattice has no constructor at
  all. This is a **design gap, not a refusal**: it is closed by adding a
  constructor, never by weakening a premise, and it must never be reported as if
  the shape were absent from the corpus.

Reported together, per landing, corpus-wide:

- **`⊤-refused` and `⊤-unvocabularised`**, separately, each by cause.
- **Pointer-deref reach** — derefs through a lifted pointer that get a declared
  block set. **L2 baseline: 661 of 2,319 = 28.50%.** This is the number that
  predicts whether L4 removes pointers or merely renames a few, and it is L4's
  own gate.
- **`arch` / `temps`** — machine names and emitter temporaries, the existing
  `splice_sweep` predicate. Kept **only** as a pair with `⊤-refused`: `arch`
  down with the ⊤ population flat is a rename and is rejected in review.
- **`zero_arch`** — tunes wearing no machine shape (today 2 of 624).
- **Gate FP and emit-identity**, unchanged in role.

## 6. What stays refused, stated in advance

- Genuinely computed pointers (advance-only webs with no static bound) stay
  `addr(S, ⊤)` and spell as today's deref — named residue with a number, not a
  whole-tune bailout.
- SMC dispatch stays the observed-variant `switch` behind guards.
- Volatile inputs stay declared inputs; `Atmosphere_II`'s `osc3` remains the
  witness's claim boundary.
- Any web the solve leaves ⊤ keeps its machine spelling. That is the point of
  the metric pairing: the residue is visible, counted, and not renamed away.

## 7. The ceiling: how far this line of reasoning goes

§1–§6 close one tune's residue. The same reasoning does not stop there, and it
is worth stating where it does stop, because that is the target the work should
be held to rather than the next rung.

### 7.1 The lift is minimisation inside an equivalence class

Gate FP defines an observational equivalence: two programs are interchangeable
iff their canonical per-frame SID write projections agree for the full song.
Everything the projection does not distinguish is **discardable** — cycle
structure, register allocation, code layout, and the driver's own control flow.
The lift is therefore not "decompilation"; it is picking a minimal
representative of the tune's equivalence class.

Minimality over all programs is not computable. Minimality **within a fixed
vocabulary under a stated cost** is, and the e-graph already computes exactly
that at extraction. So the ceiling is set by one question only: *how high can the
vocabulary go while every term in it stays derivable from the image?*

### 7.2 The vocabulary ladder, and the derivability of each rung

Each level is the same operation — anti-unification against a denotation
fixpoint — applied at a larger scope. **If a level needs a different mechanism,
that is the smell that the decomposition has fractured again.**

| level | unifies | derivable from | status |
|---|---|---|---|
| **V1 values** | definitions → webs; webs → `lane`/`idx`/`row`/`addr` | declarations, loop structure | §3 of this document |
| **V2 aggregates** | cells → records; tables → instrument/pitch/pulse/filter roles | `datadecl` partnerships + §7 backward reachability from the SID sinks (`eqlift_annotate`) | mechanism exists, unwired |
| **V3 the interpreter** | handler paths → an event/operator set; the frame function → dispatch over it | the observed value set of the command byte; the arm table is the transfer's successor set (already landed for SMC operands) | **1 of 1,200 artifacts** carry an `operators { }` block |
| **V4 the family quotient** | tunes → one player + N song data instances | anti-unification across a family, partitioned by an *independent* signal (SIDId bytes, opcode-shingle MinHash) — so it is not circular | unbuilt; tooling in tree (`tools/player_id.py`, `tools/family_cluster.py`) |
| **V5 the normal form** | — | idempotence, not derivation | unmeasured |

**V4 is the real ceiling, and L0 measured both ends of it.** The 624-tune corpus
carries **147 distinct SIDId player identifications** (11 tunes unnamed) and
**173 opcode-shingle clusters**. Folded to families, nine cover **373 tunes**:
GoatTracker 90, DMC 84, Music_Assembler 54, FutureComposer 47, Soundmonitor 30,
JCH_NewPlayer 21, SidWizard 19, Master_Composer 15, Rob_Hubbard 13. At maximum
lift those 373 tunes emit **nine** frame functions. **Today they emit 362, and
the 613 named tunes emit 596** — normalisation already merges 17 corpus-wide
(relocated groups), and Master_Composer already emits 7 texts for 15 tunes,
which is the one family where the quotient is visibly working.

That is the ungameable statement of "lifted": **two tunes running one player must
emit one program and differ only in `data { }`.** If they don't, the driver was
not lifted — the tune was.

**And it is where the artifact stops being a decompilation.** Today 78.8% of
emitted bytes are header/data/evidence and 21.2% is procedure text. At V4 the
per-tune artifact is the song data in the player's own recovered schema plus a
reference to the shared player; `image { }` and `evidence { }` are trace
scaffolding that a schema-total artifact no longer needs. The output stops being
"28.3 MB of decompiled tunes" and becomes "147 players and 624 song files".

Going past V4 — translating the recovered schema into a standard tracker format —
adds no information and can lose some. It is a translation, not a lift. **V4/V5
is the maximum.**

### 7.3 The criteria that say the ceiling is reached

All five are mechanically checkable with in-tree machinery; none reads emitted
text as evidence of anything but itself.

1. **⊤-sites zero** outside the named residue of §7.4.
2. **Family quotient**: distinct emitted frame functions ≈ distinct players.
   **Measured at L0: 613 named tunes emit 596 distinct normalised frame
   functions; the nine largest families cover 373 tunes and emit 362.** The
   near-miss is the number that matters and it is the plan's strongest
   evidence: with generated names renumbered, median in-family pairwise line
   Jaccard is **0.07–0.28**; with identity erased entirely (shape only) it is
   **0.41–0.95**, corpus median 0.55, best pairs 0.98–1.00. Same-player tunes
   are roughly half-shared structurally and near-zero by identity, so **the gap
   is a naming/denotation gap, not a structural one** — which is exactly what
   §3 solves.
3. **Idempotence under re-lift** — **demoted to a diagnostic at L0; it is not a
   gate.** `decompile(witness6502.emit(P)) == P` measures the *witness backend*,
   not the lift: `witness6502` allocates no registers and spills every
   expression to a data block in a free span, so the re-lift declares those
   spill cells as state and re-derives every temporary as a memory site. All 18
   tunes measured came back **differs-larger, median 6.9×**, with a median
   **87%** of re-lift lines naming a witness spill cell — and the sample was
   deliberately biased toward convergence (the two smallest artifacts per
   family), which strengthens the negative. Nothing shrank, so there is no
   evidence here of un-extracted structure either. The criterion becomes usable
   only when the comparison quotients out the witness's scratch span (the cells
   are identifiable — they lie in a span the tune left free) or the witness
   stops spilling; until then it says nothing about minimality and MUST NOT be
   cited as if it did.
4. **Cross-tune Gate FP**: every tune of a family verifies through the one
   shared program plus its own data.
5. **Data-only diff**: two tunes of a family differ in `data { }` and nowhere
   else.

Criteria 2, 4 and 5 carry the ceiling. Criterion 1 carries §3. Criterion 3 is a
diagnostic until its confound is removed, and the removal is not queued: it is
work on the witness, not on the lift.

Criteria 2–5 are cross-tune, which the plan's own law already demands
("verification never samples"): the corpus is the gate, not the sample.

### 7.4 What can never be lifted, named in advance

The ceiling is not zero residue. It is exactly this, and each item has a reason
that is a property of the problem rather than of the engine:

- **Volatile inputs** (`$D41B`/`$D41C`, timers): the value is not in the image.
  Declared `inputs`, resolved from a pinned trace. `Atmosphere_II`'s `osc3` is
  the standing case.
- **Genuine opcode SMC**: an observed-variant `switch` with a faulting default.
  The observed-primary law makes this a guard, never a claim.
- **Unobserved arms**: you cannot lift what never executed — only guard it.
  `--close`'s recurrence certification bounds this; it does not remove it.
- **Per-tune driver edits**: at V4 a hand-patched player surfaces as a diff
  against its family's program. That is information gained, not a failure — and
  it is the first time the artifact could tell you *what someone changed in the
  player*, which no per-tune decompilation can express.
- **Over-quotienting is forbidden**: total anti-unification or keep the copies,
  the same rule rung (e) already carries for voices. Synthesizing a guard to
  force two tunes into one player is the failure mode that would make criterion
  2 a lie, and it is banned by the same clause.

## 8. The position (2026-08-14): what landed, what the method became

### 8.1 The landings

| # | landing | result |
|---|---|---|
| L0 | instrument (#213) | baseline measured; criterion 3 demoted to a diagnostic on its own evidence |
| L1 | webs before names (#214) | **emit-identity byte-identical**; 72,095 webs / 1,805 procedures, 26.2% refused; 7,581 spellings carry 2+ webs, 4,518 of them machine names, in 621 of 624 tunes |
| L2 | the solve (#215) | **STOP**: value sites 29.31% non-⊤ against a 60% floor; the exhibits solve (`lane(0,7)`, `addr(S,·)` with its selector named) but pointer-deref reach is 661 of 2,319 |
| L2b | vocabulary extension (#216) | value reach 29.31% → 36.12%, `⊤-unvocabularised` −35%; **pointer-deref reach unmoved at 28.50%**, so L3/L4 did not proceed |
| — | canonical idiom suite (#217) | 137 variants over all 23 rows in **5.6 s**; 6 rows invariant, 17 not; 72 strict xfails naming 14 mechanisms |
| L-SMC | de-SMC (#218) | **landed**: zero writes to executable memory **624/624** (baseline 364 of 624 wrote code), Gate FP **624/624 clean**, zero refusals corpus-wide |

### 8.2 The method changed, and that is the substantive result

The corpus sweep was the development loop for the first four landings and it was
the wrong instrument: hours per answer, aggregates that moved for reasons no
aggregate could name, and three criteria that failed on their **denominators**
rather than on the work (§5, §7.3). Canonical synthesis replaced it — minimal
complete drivers assembled from first principles, lifted and solved, **seconds
per answer** — and it immediately did what the sweeps could not:

- it refuted two of the three diagnoses the seed produced, with programs. The
  "three spellings of one 16-bit step give three denotations" defect is
  **observation, not spelling**: with the carry arm taken, all three normalise
  identically, and the `ADC` pair was immune only because it is unconditional.
  The "general scalar vocabulary absorbs the specific address idiom" claim is
  false in its causal part: `join` is commutative and carries no match order at
  all;
- it confirmed and localised the one that held: **0 of 9** self-modified-operand
  spellings reach `addr`, because the operand bytes are declared as separate
  lane columns and `pair-row` has nothing to fire on;
- it surfaced a defect nobody was looking for: a cell whose only definition is
  its own step never leaves **⊥**, and ⊥ is *below* ⊤, so §5's census counted
  those sites neither typed nor refused. Every reach figure above carries that
  silent third bucket.

**The corpus verifies; it does not search.** A claim is proved on a generated
program first and confirmed corpus-wide once, at the end.

### 8.3 The ordering principle the work now follows

Two removals came before any recovery, and they share a shape: **each undoes an
optimisation the author made for the machine, which the frame-program level does
not need.**

- **Self-modification is an indirection inlined to save cycles.** Patching an
  operand *is* a deref with the pointer in code space; patching a vector *is* an
  indirect call; patching an opcode *is* a mode flag tested at that site.
  Measured: opcode/vector patching is rare and 2-way (23 dispatch cells over 6 of
  400 artifacts, max variant set 2), so there is no multiplication to fear.
- **Procedure structure is factoring done to save space** (§8.4).

De-SMC landed as **one** rewrite rather than four, because the classes are one
shape at different widths: `desmc.py` relocates every const base an emitted
access names inside an executed-instruction span, each maximal run of code pages
moving by one displacement so every index offset survives. The corpus carried a
fifth class the design did not name — **patched branch displacements**, 44 cells
over 32 tunes — and it relocated identically, which is the test of a mechanism
against a rule set. Baseline census: 3,577 patched cells (immediate 2,482,
abs-operand 550, vector 441, branch 44, opcode 60), **zero** of which were an
operand on one path and an opcode on another, so the refusal class anticipated in
advance does not exist in this corpus. Emitted text grew +812,065 bytes (+2.9%,
405 tunes larger, none smaller) — the moved pages in `image { }` — and byte
identity was correctly not the gate.

Two engineering findings the landing paid for:
**the evidence is not enough to pick a destination** (a page free by the evidence
still carried bytes some path loads, breaking 64 tunes; the destination must also
be vacant in the image, with the vacancy test admitting the relocation's own copy
so re-emission is stable), and **precision belongs at the address, not the index
bound** (bounding a patched indexed store by its index width over-approximates
into neighbouring data and refused 10 tunes; declaring the run as
`relocated $LO..$HI -> $DST` and naming its code lets the bound over-approximate
with nothing refusing).

**What de-SMC did not buy, stated plainly:** the `smc-operand` idiom pins did not
flip. The operand pair is now a fused `u16` cell outside the instruction stream,
but `datadecl.declarations` is carved *before* relocation, so a patched indexed
read is still skipped and no `stream … via` anchor may sit on a code cell; the
reload tables take no `lo`/`hi` roles and the deref stays unlifted. Making
declarations see the relocation is its own landing, and the pin text now names
that blocker so its fix flips it.

Recovery — the voice record, the sequencer levels, the family quotient — comes
*after*, because each removal deletes a class of accident that recovery would
otherwise have to model.

### 8.4 The next removal: the play routine is one procedure

After de-SMC, the largest remaining machine accident is the **call graph**.

- It is the biggest measured ⊤ population: L2b left `entry` 23,169, `opaque`
  44,470, `call` 1,379 and `recursion` 16 — **69,034 sites**, refused because a
  parameter's denotation is an interprocedural fact.
- It is cheap to remove: **2.9 procedures per artifact on average** (max 26), and
  Commando is already one. A player is a few KB under a hard frame budget; it was
  factored for space, and space is not a frame-program cost.
- It deletes machinery rather than adding it: `framestack`, `slot_reader`,
  `pcall`, parameter/return inference, the `stack-slot` catalog row and the whole
  interprocedural scope L2b built all become unreachable.
- Every later analysis becomes **intraprocedural**, which is what makes the
  structural recovery (§8.5) tractable.
- De-SMC is its enabler: once computed transfers are declared-table gotos, the
  targets inlining must resolve are already named.

**The invariant, in the same form as memory protection:** the emitted frame
program holds **exactly one procedure and no call, return or stack access**.
Binary, machine-checked, not a percentage.

**Refusals to name in advance**, each owing a disassembly and a fixture per the
claims discipline: genuine recursion (expected absent — a player has no stack
depth to spare, and that must be proven, not assumed); a computed transfer whose
target set the trace never closed (already guarded, stays guarded); and a helper
whose inlined copies exceed the observed call sites, which is a bound, not a
blow-up.

### 8.5 What recovery then faces

With SMC and the call graph gone, the artifact is one procedure over declared
data with no code writes — and the residue is exactly the *structure* §7 names:
the voice record, the sequencer levels, the effect machines. That is the point at
which the structural design (channels from the observed write map, records from
the selector's value set, levels from address-position reachability) has nothing
machine-shaped left to see through.

## 9. The next removal: the address space leaves the state (2026-08-14)

§8.4's removal has landed on `stack-removal`: the call graph and the machine
stack went as one feature (the rung ladder was retired for the reason §8.4
predicted — no slot proof can admit a spill that lives across a call, and the
evaluator keeps page one machine-owned while any call form exists). Commando and
Grid_Runner hold the one-procedure invariant; Automatas is at 0 calls / 0
page-one faults with its last `sp` tokens one pinned mechanism away
(M_SPILL_SPAN); corpus-wide, call lines fell 42% and the §8.4-clean count more
than doubled, with every landing gated at zero clean→worse.

After it, the largest remaining machine accident is the **state model itself**:
bytes at addresses, because the 6502 offers nothing else. Every structure the
tune has was flattened through that model — 16-bit values into carry-threaded
byte columns (the 8-bit ALU), records into parallel byte arrays (`freqlo,x` /
`freqhi,x`: struct-of-arrays, because `abs,X` is the only indexing), booleans
into flag bytes, fields into masks. §7.10.1 measured the flattening as the four
largest residue classes in the output (`carry_val` 5,524 sites, `word_pack`
4,472, `hi_byte`/`lo_byte` 4,293), and its blocking matrix showed they are one
feature, not four.

Two facts make the removal well-posed, and both were established the hard way:

- **Gate FP observes the SID write stream and the declared inputs. Nothing
  else.** Private-state addresses are unobservable; two programs keeping the
  same values under different layouts are indistinguishable at the gate.
- **An address is semantics only where an unresolved access may alias it.** The
  64K array is the top of the aliasing lattice — the "else" branch of naming —
  not the state model. Two corollaries the prior framing got wrong: an
  *operation's* width is a value-graph fact (the carry edge threads or it does
  not), while a *destination's* width is placement the program owns — a
  discarded or rerouted half is a `trunc` of the lifted word, never a
  counterexample, and the C64_World pin guards editing placement, not
  acknowledging width; and where a web is closed, layout is free.

**The invariant, in the same form as §8.4:** every private-state access names a
declared datum, and **a datum is its closed web, not its address**. The state
block is typed named declarations; an address is provenance on a closed web and
semantics only on an open one; the open count is the per-tune residue. Binary at
the datum; the corpus figure is the fraction of state bytes web-closed, driven
to 1.0 or named.

**The mechanism is one verdict, not a ladder.** Closure — every reaching
definition and use of a web resolved, no ⊤ access able to touch it — is a read
of machinery that already exists (L1's web partition, G1's reach bounds, the
extent guard). Three consequences fall out of the one verdict:

1. **Width becomes denotational.** Over a closed web the carry-threaded column
   pair *is* one `u16` variable, unconditionally; the column, lane-thread and
   pure-loop closed-form axioms (`sbc-chain`/`adc-chain`/`shift-pair`, the
   catalog's own rows, with SID-Wizard's `player.asm:1747` as the wide-add
   canonical cite) run over the same graph, and `carry(..)`-as-value,
   hand-packs and lane extracts leave the expression language.
2. **Flags return to control.** A flag def whose only consumers are branches is
   those branches' condition; a dead flag def drops; a flag stored as data is a
   named refusal.
3. **Records are a layout theorem.** Parallel byte arrays driven by one index
   web are one array of records — the struct-of-arrays accident inverted, which
   is §8.5's "channels from the observed write map" arriving as a consequence
   of closure rather than a framework of its own.

**The exhibit is Commando's slide step** (the region a defined-but-unread lane
review surfaced). Today: two `u8` cells written by a subtraction, a borrow
chain and a bit-per-iteration ROR loop, read once as `idx_5508:2`. After:

    step: u16                                      ; @ $5508 (provenance)
    step = (freq_a[i]:2 - freq_b[i]:2) >> (speed + 1)
    acc  = acc + step * n

and the four voice arrays under the one `x` web regroup as
`voice[x].{pos, gate, ctr, idx}`. The lanes cease to exist as declared cells,
so the audit question they raised cannot recur.

**Refusals to name in advance**, each owing a driver: an open web keeps its
address (that is the residue metric, not a failure — today's genuine
pointer-deref remainder); a pair with no carry edge stays two `u8` facts; SID
registers and declared volatile inputs stay addressed, being the observable
interface; an image table read by a computed index stays a declared array with
a proven extent; store *events* keep their order and framing — value width
lifts, event timing is placement.

**Gates, per the standing doctrine.** Gate FP byte-exact and unmoved by
construction — re-layout of unobservable state cannot move the log, and the
evaluator executes the named model directly. Round-trip totality holds through
provenance. Drivers first: the idiom suite plus three canonical shapes — the
parallel-array record, the discarded-half word (C64_World minimised, asserting
the lift rather than the refusal), the open-web fallback. The corpus runs once:
closure fraction per tune, expression residue at zero, open webs counted and
named.

**Reviewed against the two hardest families (2026-08-14), with two amendments.**
Automatas is ready almost by renaming: zero undeclared census, zero stack, and
its whole play body holds two computed accesses (the in-flight spill pair).
Ghouls_n_Ghosts holds under stress and sharpens the section: the interpreter is
already a declared operator algebra (`operators { }`, 21 ops with arities and
write-sets), the script "program counter" is a closed cursor web per voice, and
the script-level `call`/`ret`/`loop` — the feared interpreted stack — is
depth-1 per-voice scalars (`m_6B1F`/`m_6B22` pair rows, `ctr_0069-6B`,
`zp_30-35`): record fields, no stack semantics. Its 81 computed accesses are
all one shape, cursor-driven image reads, and its `undeclared 6999` is script
data awaiting the same extent proof that bounds those reads — one mechanism,
two numbers. The amendments: (1) closure accounting is three-way — **closed
web / closed by extent / open** — since a cursor-driven read of a proven
declared array retains address semantics deliberately and must not count
against the family doing it right; (2) record formation has **two licenses**:
the index web (struct-of-arrays under one index — GoatTracker, Hubbard) and
**base-displacement isomorphism** (code-copied voice instances — Follin,
defMON, and the copies call-flattening itself manufactures), the latter being
the law `test_voices_are_isomorphic_up_to_base_displacement` already pins and
the seed of §8.5's quotient.

What this deletes: the ALU residue as a campaign of its own, destination-fusion
rules as semantics, the lanes-of dialect question, and the address-anchored
half of `datadecl`'s carving. What recovery then faces is §8.5 over variables
and records — the family quotient (one player per family) taken over a language
with no memory map left in it.

### 9.1 The first landing: scratch leaves the state

**A private cell is scratch iff every read of it is dominated by a same-frame
write.** Gate FP observes frame boundaries; a cell dead at every frame exit is
not state — it is a wire. The rule is rung (d0)'s locality question asked of
*all* private memory, and in a flat call-free procedure it is a single
dominating-store/available-load pass: page one was never special, and the stack
campaign was this landing restricted to one page. Generalizing it deletes the
special-casing along with the cells.

Consequences, measured on the exemplars: Wizball's working set — 138 role-less
zp cells, the corpus maximum, zero page used as the machine's register file for
per-frame synthesis — largely dissolves into locals and then into expressions;
Follin's op-argument unpacking (`op $8E arity 4 writes zp_87..zp_8C`, handler
reads them same-frame) is argument passing through memory and becomes ports.
The state block then declares *persistent state only*, which is the precondition
that makes §9's closure and record formation small.

The unit is the web, not the cell. An overlaid cell — two objects
time-sharing one address with disjoint live ranges (7,581 spellings carry 2+
webs, L1; defMON's `$1800` region holds load-phase tables and the runtime
pitch LUT) — splits into its webs first, and each web classifies on its own: a
cell can host a scratch web and a persistent web at once. A connected web whose
value crosses uses is one datum, whatever it is used for.

Refusals: a web an open access may reach stays addressed (§9's residue); a web
read before any same-frame write on a non-faulting path is persistent; a read
reachable only through an `unobserved` guard is scratch with the guard carrying
the claim. Binary per web; the per-tune figure is state fields before/after.

Ordering: this lands first within §9 — before width and records — because it
deletes cells outright, so every later verdict has fewer webs to close.

### 9.2 What landed, and the two loop readings it priced (2026-08-15)

**§8.4's invariant is reached on Automatas** — one procedure, no call, no return,
no `sp`, no page-one access, Gate FP clean — by two corrections to the destack
rungs. §9.1's rung was wired to the emit, measured, and **reverted**: it costs a
Gate FP verdict (below). The two loop readings it exposed are wrong on their own
terms and stayed.

- **The fault is the bottom of the displacement lattice.** Reaching `unobserved`
  is a fault (`frameval._s_unobs`), so the displacement walk holds nothing there
  and a faulted path constrains no join. Reading it as an edge standing at the
  entry refused balances that hold.
- **Every surviving call pushes.** `frameval.run_frame` pushes a return word at a
  text-threaded `pcall` as much as at a raw call, so the drop's linkage guard is
  about every call, not the forms the machine threads. This *fixed* eleven
  corpus tunes that faulted at baseline (`load from the stack page $01FC`).
- **The held verdict is provisional.** Rung (d0s) holds a slot where an
  unresolvable address may alias it; those addresses are pointer derefs, which
  rung (f) bounds — but only once rung (d) has fused the pair. `deref_bounds`
  takes rung (f)'s reading where fusion has run, and `drop_spills` re-asks the
  held verdict: page one is the machine's, and where every procedure balances the
  pushed return word stands above every entry-epoch slot.
- **Two loop readings.** A `for` leaves by its own bottom, so its body's last trip
  falls out of the loop — reading the body's fall-through as the head alone made a
  loop-carried local dead at its own update. And a levelled exit escapes as many
  loops as its level counts, where the inline context carried only the innermost.
  Neither could be reached by a memory cell; the accumulator §9.1 dissolves is
  exactly the shape that reaches them.

Measured on the two landed rungs: both corpus gates with zero clean→worse, Gate FP
568 → 612 clean of 614 built (44 tunes that faulted at baseline now gate), inv_probe
232 → 238 §8.4-clean.

**What §9.1 owes, measured.** Wired to the emit the rung takes Commando 25 → 15
state fields and Blueprint 20 → 19, and it moves the log on exactly one corpus
tune: `Compo_Music_1-Puke_4_4`, clean → diverged at frame 796 (`v0.lww`, got
`(1,255)`, want `(1,33)`), on the single cell `$171F` — three stores, two reads,
every read dominated, and dropping the two stores the emitted text does not read
changes what the machine writes. **The walk's read set is not the whole read
set**, and that is the premise the rung owes before it lands.

Two more owed mechanisms, both named by Grid_Runner:

- Its four latches (`m_13CF..m_13D2`) are read only by the covering blit
  `sid.reg[x] = m_13BA[x]`, which the walk counts as no read of any cell it
  covers, so they classify as neither scratch nor state. A dead-store verdict
  over them was measured and rejected: it diverged Gate FP on Grid_Runner
  (filter, frame 2) and Automatas (`v1.lww`, frame 33). What is owed is an
  indexed read read as a read of every cell its span covers — the same
  mechanism the `$171F` divergence points at.
- `$040B`/`$0414`/`$045D`, SMC vector high bytes no store reaches, still declare
  as state. `framestack.unwritten` names them, and demoting them is a **data
  declaration**, not a role: a constant is not an update shape and `roles.ROLES`
  is closed over the update shapes (`test_the_shape_and_role_vocabularies_are_closed`).

## 10. The recovery: the accumulator machine is a transliteration (2026-08-15)

§8.5's machine — clocked, triggered, bounded accumulators cascading into SID
writes — needs no instrumentation to recover. The post-removal artifact is a
closed term: total semantics, closed webs, structured control, every open edge
guarded. Every question a run would answer is decidable by reading:

- **Accumulator spec = a cell's update sites plus their dominating guards.**
  The unguarded `ctr = ctr - 1` is the step and the clock; the reload dominated
  by the wrap comparison is the bound and the reload source. One to three sites
  per cell in a flat procedure; all four parameters are the text.
- **Trigger edge = dominance, not coincidence.** B is triggered by A exactly
  where B's reload site is dominated by A's wrap condition — a region-tree
  fact, read once.
- **Cascade order = statement order. Clock dividers = gating nesting.** A cell
  updated only inside another counter's wrap arm is the divided clock.
- **Ports = the leaves**: a step drawn from a table row or an `iota` input.
- **Mode-dependent cells**: update sites under two mode guards are two
  accumulators selected by a static mode variable, read off the `switch`.

The observational facts the graph needs are already embedded — `unobserved`
guards, observed dispatch sets, loop evidence, declared inputs. The text is the
trace's residue; running it again to learn structure would re-derive what the
guards state. The run survives as verification only: the transliterated machine
replays `framelog.canonical` once, the gate it always was.

The pattern, named because it recurred five times: rungs collapsed into one
rewrite, the census into a gate, destination rules into placement, addresses
into webs, instrumentation into transliteration. Each mechanism was sized for
the pre-removal problem; the artifact remaining is a few hundred statements of
arithmetic on named variables, and passes over it are one syntax-directed walk.

Metric: fraction of state cells whose update sites transliterate; residue
spelled inline (the machine is hybrid by construction). Drivers first: a
synthesized divider -> row -> arpeggio cascade recovered exactly, then the
Commando machine (frame clock -> period-8 vibrato triangle and row timer in
parallel -> wrap triggers the cursor cascade -> note-on reloads slide step and
duration). The graph is the player; the constants, tables and scripts are the
tune — the family quotient's concrete object.
