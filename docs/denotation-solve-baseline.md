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

## Coverage

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

