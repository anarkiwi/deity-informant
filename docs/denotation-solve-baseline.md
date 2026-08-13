# L0 baseline for the denotation solve

Measured with `tools/denotation_l0.py` (`census`, `family`, `relift`, `report`).
The census and the quotient read the 624 artifacts `_sweep` had already cached at
full Songlengths and re-emitted nothing; the re-lift is the only stage that
decompiles, over a 200-frame window on both hops.

## (1) Criterion 3 -- re-lift convergence

`decompile(witness6502.emit(P))` against `P`, compared on procedure text with
relocation, generated names and local numbering canonicalised. `witness scratch` is
how much of the re-lift names the witness's own expression spill block.

| family | tune | verdict | P lines | re-lift lines | x | witness scratch |
|---|---|---|---|---|---|---|
| GoatTracker | 1917 | differs-larger | 201 | 1396 | 7.87 | 129 names, 84% of lines |
| GoatTracker | Croaky | differs-larger | 183 | 1289 | 7.98 | 120 names, 85% of lines |
| DMC | Contact_Tendance | differs-larger | 194 | 2327 | 9.4 | 216 names, 91% of lines |
| DMC | For_Link | differs-larger | 227 | 2700 | 9.54 | 249 names, 91% of lines |
| Music_Assembler | Best | differs-larger | 166 | 1516 | 7.61 | 149 names, 89% of lines |
| Music_Assembler | Hans_Kloss | differs-larger | 180 | 1550 | 6.92 | 145 names, 88% of lines |
| FutureComposer | Acid_Rain | differs-larger | 169 | 1457 | 6.89 | 138 names, 88% of lines |
| FutureComposer | Anti-Gang | differs-larger | 202 | 1703 | 6.86 | 156 names, 88% of lines |
| Soundmonitor | Echnaton | differs-larger | 471 | 2796 | 5.24 | 172 names, 74% of lines |
| Soundmonitor | Addiction | differs-larger | 575 | 3341 | 4.99 | 177 names, 74% of lines |
| JCH_NewPlayer | Breakbeats | differs-larger | 260 | 2422 | 7.97 | 221 names, 90% of lines |
| JCH_NewPlayer | Alles_ist_Binaer | differs-larger | 276 | 2608 | 7.84 | 240 names, 91% of lines |
| SidWizard | Asalieri | differs-larger | 427 | 3269 | 6.39 | 259 names, 89% of lines |
| SidWizard | 10_Yil_Marsi | differs-larger | 510 | 3707 | 6.19 | 286 names, 87% of lines |
| Master_Composer | Invention_13 | differs-larger | 74 | 464 | 4.88 | 39 names, 75% of lines |
| Master_Composer | Il_Dollarone | differs-larger | 80 | 517 | 5.07 | 42 names, 75% of lines |
| Rob_Hubbard | Action_Biker | differs-larger | 189 | 1356 | 6.38 | 128 names, 87% of lines |
| Rob_Hubbard | Final_Frontiers_Intro | differs-larger | 183 | 1497 | 6.98 | 139 names, 87% of lines |

Verdicts {"differs-larger": 18} over 18 attempted; median growth 6.9.

## (2) Criterion 2 -- the family quotient

Distinct emitted frame functions per player family over every cached artifact of
that family. `norm` renumbers generated names by first appearance, `shape` erases
the identity altogether so an inserted site cannot shift every later line; `J` is
the median pairwise line Jaccard, `shared` the family's intersection over union.

| family | tunes | raw | norm | shape | median lines | J(norm) | J(shape) | max J(shape) | shared(shape) |
|---|---|---|---|---|---|---|---|---|---|
| GoatTracker | 90 | 90 | 90 | 90 | 420 | 0.136 | 0.5 | 0.981 | 0.021 |
| DMC | 84 | 84 | 82 | 82 | 354 | 0.071 | 0.422 | 1.0 | 0.027 |
| Music_Assembler | 54 | 54 | 54 | 54 | 264 | 0.114 | 0.722 | 0.993 | 0.167 |
| FutureComposer | 47 | 47 | 46 | 46 | 358 | 0.137 | 0.658 | 1.0 | 0.048 |
| Soundmonitor | 30 | 30 | 30 | 30 | 681 | 0.157 | 0.741 | 0.954 | 0.309 |
| JCH_NewPlayer | 21 | 21 | 21 | 21 | 380 | 0.079 | 0.472 | 0.99 | 0.116 |
| SidWizard | 19 | 19 | 19 | 19 | 694 | 0.139 | 0.653 | 0.879 | 0.152 |
| Master_Composer | 15 | 7 | 7 | 7 | 124 | 0.275 | 0.948 | 1.0 | 0.37 |
| Rob_Hubbard | 13 | 13 | 13 | 13 | 232 | 0.082 | 0.411 | 0.904 | 0.036 |
| - | 11 | 11 | 11 | 11 | 83 | 0.033 | 0.059 | 0.154 | 0.006 |
| Stephen_Ruddy | 11 | 11 | 11 | 11 | 809 | 0.078 | 0.217 | 0.697 | 0.037 |
| RoMuzak | 10 | 10 | 10 | 10 | 421 | 0.121 | 0.683 | 0.886 | 0.055 |

Nine largest families: 373 tunes, 362 distinct normalised frame functions.
All 113 named families: 613 tunes, 596 distinct (596 with the identity erased).

## (3) The L0 census

| measure | value |
|---|---|
| artifacts | 624 |
| `arch` (machine names) | 193979 |
| `temps` | 54986 |
| median `arch` per artifact | 303 |
| `zero_arch` | 2 |
| raw `mem[` sites | 8609 |
| `*deref` sites | 2180 |
| SID writes indexed by a machine register | 3788 |
| artifacts carrying such a write | 539 (86.4%) |
| `for` headers | 239 |
| `for` headers with a machine induction variable | 239 |
| artifacts declaring a `[3]` table holding `00 07 0E` | 271 (43.4%) |
| `stream ... via ptr` declarations | 12424 |
| header+data bytes | 22356758 |
| procedure-body bytes | 6008416 (21.2%) |

## (4) L1 -- the web census

Measured with `tools/web_census.py` over the same 624 cached artifacts, off the settled
statement trees (`frameprog.program(...).webs()`, after `repolish`/`resign`). A web is the
set of definitions that reach a common read; it is refused, and keeps its machine
spelling, when it is live where the procedure begins, when an opaque call or a transfer
the graph cannot enumerate defines or reads it, or when its sites disagree on a width.

| measure | value |
|---|---|
| procedures | 1805 |
| webs | 72095 |
| refused | 18859 (26.2%) |
| -- `entry` (live at procedure entry) | 6837 |
| -- `opaque` (opaque call / unenumerable transfer) | 14334 |
| -- `width` (sites disagree) | 0 |
| locals | 32674 |
| locals carrying 2 or more webs | 7581 (23.2%) |
| of those, machine names | 4518 |
| widest single spelling | `a`, 96 webs in one procedure |

Two readings. **The quotient is real and large**: 32,674 spellings carry 72,095 webs, so
a name-keyed analysis is deciding one thing where the value structure has 2.2, and 4,518
machine names carry more than one quantity apiece -- §1's Exhibit A is the corpus's shape,
not Commando's. **The width class is empty**: `_norm_widths` already normalises to one
spelling per name and rung (d) has fused every pair it can, so no surviving web has sites
that disagree. The refusals that do bite are `opaque` (14,334) and `entry` (6,837), which
is what L2's ⊤ population starts from.

## (5) L2 -- the denotation census, and the kill criterion

Measured with `tools/denote_census.py` over the same 624 cached artifacts, off the same
settled statement trees. The solve is `deity_informant/denote.py`; its lattice, join and
transfer rules are [denotation.md](denotation.md). **A2 emits no byte** --
`tools/emit_identity.py` reproduces `7a63a89f...`, 28,365,174 bytes, 0 refused.

A **value site** is one occurrence of a local, a definition or a read: exactly the
population `arch`/`temps` count. A **deref site** is one memory access whose address is
not a compile-time constant, typed by *where it lands* (`addr(S,r)`) rather than by the
byte it yields.

| measure | value |
|---|---|
| value sites | 241817 |
| -- non-⊤ | **70884 (29.31%)** |
| deref sites | 101825 |
| -- non-⊤ | **85075 (83.55%)** |
| deref sites through a lifted pointer | 2319 |
| -- non-⊤ | 661 (28.50%) |
| webs | 71555 |
| -- non-⊤ | 19048 (26.62%) |
| persistent cells non-⊤ | 4738 of 18967 |
| declared tables non-⊤ | 14389 of 14389 |

Value sites by constructor: `byte` 36429, `idx` 28270, `const` 3396, `lane` 2369, `row`
251, `addr` 169. Deref sites are `addr` by construction: 85075.

⊤ by cause, value sites (170,933 in all):

| cause | count | what it is |
|---|---:|---|
| `op` | 43808 | an operator §3.1's lattice has no constructor for -- counters, accumulators, carries, compares |
| `entry` | 41737 | the web is live where the procedure begins (§3.2's stated refusal) |
| `opaque` | 32891 | an opaque call or an unenumerable transfer defines or reads it (§3.2's stated refusal) |
| `cell` | 22341 | it reads a cell or table that is itself ⊤, almost always for `op` |
| `addr` | 19701 | a memory site whose address names no declared base: an unlifted pointer, a stack slot |
| `mixed` | 5301 | the facts join across constructors the lattice does not cross |
| `call` | 4148 | a call return |
| `nofact` | 1006 | no site states anything |

⊤ by cause, deref sites (16,750 in all): `addr` 12985, `mixed` 2107, `cell` 1658 (the
pointer's own cell is ⊤).

Rule tally, so the census can be read per rule rather than as one number: `table-row`
19710, `index-use` 3725, `field-select` 2087, `pair-row` 2006, `cell-read` 1048,
`lane-table` 619, `cursor-step` 613, `deref-row` 598, `addr-row` 73, `affine-const` 33,
`staged-init` 33.

### The verdict: STOP

§4's L2 kill criterion is 60% of value sites and 80% of deref sites. **Value sites reach
29.31%.** Deref sites reach 83.55% and clear their threshold; the value-site rate does
not, and it is the plan's primary number (§5: "the primary number; it is what the work
actually reduces"). **L2 stops here and reports.** Nothing was tuned to move it and the
lattice was not extended to chase it.

*(§4 spells the criterion with "and", which would read as "stop only if both fail". The
A2 instruction spells it with "or". Both readings are recorded here: under "or" this is a
STOP, under "and" it is not, and the deref rate is the half that passes.)*

### What the ⊤ population actually is

Three findings, none of which is "the shape is not in the corpus":

**(1) 46.1% of the ⊤ value sites are refusals the plan already stated, not lattice
gaps.** `entry` + `opaque` + `call` is 78,776 of 170,933. §3.2 names all three in advance:
"A web that is live at procedure entry, defined by an opaque call, or whose sites disagree
after the solve stays ⊤ and keeps its machine spelling." An entry-live web is a
*parameter*, and its denotation is the caller's argument -- an interprocedural fact this
landing does not compute. These are not evidence about the corpus's shape; they are the
analysis unit's own boundary, and they are the population an interprocedural landing would
address.

**(2) 38.7% is scalar arithmetic and the cells it feeds.** `op` + `cell` is 66,149. A
driver's register traffic is mostly counters, envelope accumulators, carries, borrows and
comparison results. §3.1's lattice has **no constructor for a scalar quantity** -- by
design, since it was drawn to close the *pointer and lane* residue of §1. So these sites
are ⊤ correctly: the lattice does not claim them and must not. This is the finding that
decides the criterion, and it is a statement about the lattice's scope rather than about
the corpus.

**(3) The mechanism the plan was built for does work, where it applies.** All 14,389
declared tables type; `idx` reaches 28,270 sites; `pair-row` fires 2,006 times and
`lane-table` 619; the deref population is 83.55% placed. Commando's own exhibits both
solve: `m_54EB` is `lane(0,7)` and `ptr_005F` is `addr(S,·)` over the 32 declared blocks
`$5887..$5D7D` with its selector web named (`tests/test_denote.py`). But **only 28.50% of
the derefs that go through a lifted pointer get a declared block set** -- 1,658 of 2,319
have a pointer cell the solve leaves ⊤ -- which is better than §4.6's 366 of 3,929 (9.3%)
and nowhere near closing. L4's expected reach is that 661, not the 2,319.

### The `opaque` refusal, tightened

A2 bounds a raw `call` by its callee's own may-define and live-in sets
(`frameproc.call_summaries`), so a register the callee does not touch flows through.

| | A1 | A2 |
|---|---:|---:|
| webs | 72095 | 71555 |
| refused | 18859 (26.2%) | 16841 (23.5%) |
| -- `entry` | 6837 | 6712 |
| -- `opaque` | 14334 | **12238** |
| -- `width` | 0 | 0 |

−2,096 opaque refusals (−14.6%). The residue is not raw calls to procedures: it is
escaping transfers (`goto` out of the procedure, a depth-carrying `ret`, `dcall`/`dgoto`/
`dbr`), `swc` arm sets, and `call`s whose target is a *label inside* a procedure rather
than an entry -- `_Info` has no summary for a label either, which is why the engine's own
`may` set falls back to every register there too.

## Coverage

- webs and denotations: 624 of 624 cached tunes at full Songlengths, nothing re-emitted;
  both censuses read the same artifacts the emit-identity gate hashes, and the denotation
  census was run twice with identical totals.
- census and quotient: 624 of 624 cached tunes at full Songlengths, every artifact
  already in `.sweep-cache` at the current package fingerprint -- nothing re-emitted.
  11 tunes carry no SIDId name and are out of the family rollup (they keep the `-`
  row); a tune matching several signatures is assigned its most-carried family.
- re-lift: 18 tunes over 9 families, 200 frames on both hops, the two smallest cached
  artifacts per family -- a sample biased toward convergence, not away from it. The
  re-lift enters the witness image through a no-op (`RTS`) init planted in a free
  byte, so the play phase starts from exactly the image `witness6502` emitted.
  Per-tune build cap 1500s.

## What this says about the plan

**(1) re-lift: the artifact is not a normal form, and the measurement is neutral on
the ceiling.** 18 of 18 attempted re-lifts returned a result and every one of them is
`differs-larger`, at a median 6.9x the procedure text; none identical, none smaller.
So `decompile(witness6502.emit(P)) == P` fails on every tune measured, and the
artifact is demonstrably not a fixpoint of the pipeline. It is not evidence of
un-extracted structure either: a median 87% of the re-lift's lines name a cell in a
span the tune left free, which is where `witness6502` spills every expression because
it allocates no registers. Criterion 3 measures the backend until the witness stops
spilling, not the lift. What it does settle is the direction: nothing shrank, so
nothing here says the artifact carries structure the lift could have extracted.

**(2) family quotient: supports the plan's premise.** 613 named tunes emit 596 distinct
normalised frame functions; the nine largest families cover 373 tunes and emit 362, against
§7.2's target of nine. Erasing the identity entirely still leaves 596. The near-miss says
the same: median pairwise line Jaccard within a family is 0.1095 with names renumbered and
0.552 with the identity erased, so two tunes of one player share roughly half their line
shapes and almost no line identities. The gap §7.2 names is real, large, and measured.

**(3) census: supports §1's diagnosis at full corpus coverage.** 539 artifacts (86.4%)
index a SID write by a machine register, 271 (43.4%) declare a `[3]` table holding
`00 07 0E`, and 239 of 239 `for` headers induct on a machine register. Median `arch` is 303
and `zero_arch` is 2 of 624. §1 read these off a stale 1,200-artifact sample; they hold
on the current build over the whole corpus.

