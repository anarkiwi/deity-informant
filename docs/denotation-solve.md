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
4. **L3 — the voice record.** `lane` denotations become `[3]` state arrays and
   `sid.v[voice]`; the induction variable is the voice. First landing whose text
   moves. *Gate: Gate FP + reviewed 25-exemplar diff.* Expected reach: the 526
   of 1,200 artifacts carrying the displacement table, the 955 indexing SID by a
   register, and all 432 register-spelled `for` headers.
5. **L4 — pointer demotion.** `addr(S,·)` state fields are replaced by
   `idx(T)`; derefs become two-level declared accesses; the grammar gains the
   production and `frameval` learns to resolve it. **This is the first change
   that moves the state shape, so "the text cannot move" is no longer the
   argument — Gate FP is.** Expected reach: the 365 sites §4.6 refuses for
   naming 2+ blocks, plus the `via` stream declarations, which become the block
   table's rows.
6. **L5 — subsume and delete.** The rung modules the solve replaces come out:
   `frameptr` (594), `ptrlift` (114), `ptrcert` (1,157), `ptrextent` (183),
   `framefuse` (831), `framemath` (954), `roles` (245) — ~4,078 lines of premise
   code, plus the bespoke bound in `streams`. A landing that adds the solve and
   keeps the ladder beside it has failed: two analyses that can disagree is the
   condition this proposal exists to end.
7. **L6 — close.** One analysis, one refusal class (⊤), one emitter; the
   backlog's four "reducers" are consequences, not entries: the multi-reader
   forward is a shared denotation (a named value needs no synthesized
   definition), per-frame demotion is a denotation dead at the frame boundary,
   the SMC-operand evidence is `idx` on a dispatch cell.

## 5. The metrics, chosen so they cannot be gamed

Reported together, per landing, corpus-wide:

- **`⊤-sites`** — value sites with no denotation. The primary number; it is what
  the work actually reduces.
- **`arch` / `temps`** — machine names and emitter temporaries, the existing
  `splice_sweep` predicate. Kept **only** as a pair with `⊤-sites`: `arch` down
  with `⊤-sites` flat is a rename and is rejected in review.
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
