# The denotation solve, as landed (A2 / L2, extended at A3 / L2b)

`deity_informant/denote.py`. The implementation of docs/denotation-solve.md §3:
one monotone analysis whose unknown is what a value **denotes**. **Neither landing
emits a byte** — `tools/emit_identity.py` is byte-identical across both — so what
this document specifies is the solution, its evidence records and the census
`tools/denote_census.py` reports.

L2b extends the *scope* and the *vocabulary* without changing the vehicle: §7 puts
procedures in the solve so a parameter is its call sites' arguments, and §8 folds
`roles.py`'s classification in as lattice constructors so there is one reading of a
scalar cell where L2 left two. Both are docs/denotation-solve.md §7.2's "same
operation at a larger scope"; §9 records what the extension did *not* move and the
premise that residue waits on.

## 1. The vehicle, and why it is not the e-graph

§3 proposes riding `eqlift_mem`'s e-class analysis. That was read and rejected,
for three reasons that are properties of the existing graph rather than of the
lattice:

- **The e-graph has no web identity.** `render_proc` walks a procedure into SSA
  versions (`a.3`) and *havocs* at every join, loop back edge and call wall
  (`havoc_locals`, `havoc_all`, `_join_mem`). A def-use web is exactly the set of
  definitions a havoc erases the relation between, so the unit A1 landed is not
  expressible as an e-class.
- **It is built at emission time, under a clock.** `render_proc` takes a
  `budget` (`EMIT_S`, default 60s across an artifact) and `saturate` stops at
  `ROUNDS`/`NODES`; sites past the deadline render own-term. A denotation census
  read off that graph would be a function of the machine it ran on. The census
  must be a function of the program.
- **`lane` is a control fact.** It is affine in the *loop's* channel index; the
  e-graph holds values, and the loop structure the claim rests on is in the
  statement forest.

The interval analysis stays exactly where it is and keeps doing what it does —
address disjointness — and the denotation is **a worklist over the web graph**:
`frameproc.ProcWebs` for the unknowns, the settled statement forest for the
facts. This is the substitution §3 explicitly permits, stated rather than taken
quietly.

## 2. The lattice

```
⊥                        no site states anything yet
const(c)                 a compile-time constant
lane(a, b)               affine in the frame's channel index: a + b·v, v < 3
idx(S, e)                an entry index into the declared columns S, extent e
row(S, e)                a row index into block-set S, bounded by e (None: open)
addr(S, r)               an address: base ∈ S, row denotation r
byte(S)                  a data byte from declared sources S
flags                    a bit-field: the update recombines it bitwise      (L2b)
acc                      an accumulated quantity: stepped by a value        (L2b)
count                    a counted quantity: some step is the machine's DEC (L2b)
pred                     a one-bit result: a comparison or a carry          (L2b)
⊤                        unknown
```

`idx` carries a *set* of columns because a record's parallel columns are read at
one index (`m_5596`/`m_5597`/`m_5598` of `m_5591[263] stride 8`), and an index
into several columns of one record is one index, not two.

The four L2b constructors are **not new vocabulary**. `flags`, `acc` and `count`
are `roles.py`'s `flags`/`accumulator`/`counter` read as denotations (§8), and
`pred` is the constructor docs/idiom-catalog.md's `compare-value` and
`carry-value` rows were carrying as `named-unknown`. `cursor`, roles' fourth, was
already in the lattice as `idx`/`row`/`addr`, which is exactly the duplication
L2b removes. Nothing carries a payload: a scalar's *value* is not what the
analysis is for, and a parameterless constructor keeps the height at one.

**Join** (`denote.join`) is pointwise, with §3.1's two constructor-crossing
rules and three subsumptions:

| crossing | result | why |
|---|---|---|
| `addr(S1,r) ⊔ addr(S2,r)` | `addr(S1 ∪ S2, r)` | §3.1: **the block set is allowed to grow** |
| `const(c1) ⊔ const(c2) ⊔ …` | `lane(a,b)` | §3.1: the loop's own affine image, at exactly 3 constants and a non-degenerate step; taken set-wise in `_consts`, since a lane is a property of three constants and not of any two |
| `byte(S1) ⊔ byte(S2)` | `byte(S1 ∪ S2)` | sources union |
| `idx(S1,e1) ⊔ idx(S2,e2)` | `idx(S1 ∪ S2, max e)` | ditto, weaker extent |
| `const(c) ⊔ lane(a,b)` | `lane(a,b)` iff `c` is one of the three channel values | a constant inside the image is a value of it |
| `const(c) ⊔ row/idx(S,e)` | the row/idx iff `c = 0` or `c < e` | a constant reset of a cursor is a row of it |
| `byte(S) ⊔ idx/row` | the idx/row | a byte of a declared source, read as a row, **is** that index |
| `const(c)/byte(S) ⊔ flags` | `flags` | a constant or a datum written into a bit-field is a value of it |
| `const(c)/byte(S) ⊔ acc/count` | the acc/count | a datum loaded and then stepped is that step's quantity |
| `acc ⊔ count` | `count` | **roles' own precedence as an order**: a quantity some step decrements is a counter whatever else steps it |
| `const(c) ⊔ pred` | `pred` iff `c ∈ {0,1}` | a one-bit result takes one-bit constants |
| anything else | `⊤` | |

Finite height: `S` is a subset of the declared bases, `addr` nests one row deep,
the four scalar constructors carry no payload and sit on the single chain
`⊥ ⊑ acc ⊑ count ⊑ ⊤` (`flags` and `pred` are their own two-element chains), and
everything else widens to ⊤. The solve ascends (`join(previous, resolved)`) so
it terminates; `_ROUNDS = 24` is the same cap every other fixpoint in the
package runs under, and an unknown still moving there is ⊤ with cause `rounds`.

## 3. Facts, not a one-way flow

A denotation is fixed by two kinds of site, and §3.1 states both kinds of
constructor:

- a **definition** says what a value is built from (`asg`, a store into a cell);
- an **index use** says what it selects — `idx(T)` is *"an entry index into
  declared table T"*, which is a fact about consumption, and `row(S,e)` likewise.

So each unknown carries a fact set. The role facts are joined, each value fact
is refined by the joined role, and the results are joined. Growing a fact set can
only weaken a denotation and the fact sets are finite, so the round cap leaves ⊤
behind and never a wrong answer.

The unknowns are:

| key | what |
|---|---|
| `("w", entry, root)` | one def-use web of one procedure (`frameproc.ProcWebs`) |
| `("c", addr)` | one persistent cell, at a constant address |
| `("t", base)` | one declared table's rows, as an index role carrier |

## 4. The transfer rules

Each is a row of docs/idiom-catalog.md, read off the **declarations** — extents,
`lo`/`hi` roles, `mut` offsets and the image bytes — never off the trace. The
evidence record names which fired, so the census can be re-read per rule.

| rule | shape | yields |
|---|---|---|
| `lane-table` | a byte load from an exactly-declared const table of 3 elements whose data is the non-degenerate affine image `a + b·v` | `lane(a,b)` |
| `table-row` | any other load from a declared table or record column `T` | `byte({T})` |
| `pair-row` | a width-2 load, or the catalog's `word-pack` of two byte columns, at one index off a declared `lo`/`hi` pair `T` | `addr(blocks(T), ⊥)`, blocks read from `mem0`; the index is recorded as the selector |
| `deref-row` | a load through a pointer cell rung (f) resolved, whose cell is `addr(S,·)` | `byte(S)` |
| `cell-read` | a read at a constant address inside a declaration | `const(image word)` if const, else `byte({T})` |
| `staged-init` | a cell no play store reaches | `const(the word init left)` |
| `field-select` | `AND`/`OR`/`XOR`/shift of a `byte(S)` against a literal (catalog `mask-const`, `set-const`) | `byte(S)` |
| `addr-row` | `addr + const` (§3.3's "`addr + row` is `addr`") | the `addr` |
| `cursor-step` | `row/idx ± const` | the same block set with the bound opened, which is §6's "advance-only webs with no static bound" |
| `affine-const` | the constant set of a web, incl. a `for` header's own range | `_consts` above |
| `index-use` | the web indexes declared columns `S` of extent `e` | `idx(S, e)` |
| `word-pack` (L2b) | one term assembling both byte lanes of a word, the lanes taken **denotationally** rather than as bare loads | `addr(blocks(T), ⊥)` where the lanes' `byte` sources are one declared pair's two columns, else ⊤ with cause `addr` |
| `lane-pack` (L2b) | the catalog's `lane-insert`: a word one of whose lanes a definition writes while reading the other back | ⊤ with cause `addr` — recognised, claimed for nothing; see the third clause below |
| `counter-step` (L2b) | the quantity ± a constant, its own operand read out of the solution | `count` where some step is the machine's own `DEC`, else `acc` |
| `accumulate` (L2b) | the quantity ± a value | `acc` |
| `bit-field` (L2b) | an `AND`/`OR`/`XOR`/shift the quantity itself reaches the top of (catalog `flag-bit`) | `flags` |
| `compare-value` (L2b) | `INT_EQUAL`/`NOTEQUAL`/`LESS`/`LESSEQUAL`/signed forms/`BOOL_*` | `pred` |
| `carry-value` (L2b) | `INT_CARRY`/`INT_SCARRY`/`INT_SBORROW` | `pred` |
| `param-arg` (L2b) | a parameter web, at a call site the graph enumerates (§7) | the argument's own denotation, joined over the sites |
| `call-return` (L2b) | a `pcall`'s bound name, where the callee's `ret` statements state it (§7) | the callee's return webs, joined |

`field-select` is widened at L2b from "against a literal" to "every operand a
`const` or a `byte(S)`" for `AND`/`OR`/`XOR`, and to "the datum alone, at any shift
distance" for the shifts — the same catalog rows (`mask-const`, `set-const`) read
over declared sources rather than over constants alone. The yield is the union of
the sources, since a field built out of two declared data is a byte of both.

Three soundness clauses worth stating because they are the ones that could be got
wrong:

- **A `const` claim on a cell must agree with the image.** A cell holds what init
  left in it until a store lands, so a cell whose play stores are all `$00` but
  whose image byte is `$40` is ⊤, not `const(0)`. Every other constructor carries
  no such obligation: the artifact already prints the staged word on the `state`
  row beside the field, so it is declared rather than denoted.
- **Three affine constants alone name no loop.** The `lane` reading of a constant
  set fires only where the web *also* indexes a declared `[3]` table whose `mut`
  offsets cover all three lanes — a per-voice state array, §1's Exhibit C. That
  is the observed-primary guard on the one claim here that is about control.
- **A lane insert is not a pair read.** `pair-row`/`word-pack` are *one* store of
  *one* word, so the cell is exactly one of the pair's words. A `lane-insert` is
  two stores, and between them the cell holds one old lane and one new one — a
  value a deref may reach and that the pair's block set does not contain. So the
  rule recognises the shape and claims nothing: the cell is ⊤ with cause `addr`.
  What the recognition buys is the *cause*, and that is not cosmetic — it is
  1,177 pointer cells that L2 filed under `op`, "no constructor exists", and that
  are in fact a stated refusal with a named missing premise (§9).

## 5. The census, and what it counts

`tools/denote_census.py`.

- **value site** — one occurrence of a local, a definition or a read, over the
  settled statement forest. This is exactly the population `arch`/`temps` count,
  which is what §5 pairs `⊤-sites` with.
- **deref site** — one memory access whose address is not a compile-time
  constant: the sites that need a denotation to be *placed*. Reported with the
  pointer-rooted subset (`*ptr[i]`, rung (f)'s own population) broken out, since
  that is the one §4.6 refuses today.

A deref site is typed by its **address** (`addr(S,r)`), not by the byte it
yields: where the access lands is what the site owes.

**⊤ is reported split** (docs/denotation-solve.md §5). `T_OP` — an operator no
constructor covers — is the whole of `⊤-unvocabularised`; every other cause is
`⊤-refused`, a premise the solve will not discharge. `T_CELL` is **not a class of
its own**: a site refused for reading a refused unknown inherits *that unknown's*
class, so `Record.src` carries the unknown the ⊤ came from and `Solve.klass`
follows the chain (a cycle among ⊤ cells reads as refused). `T_MIXED` is filed
refused, and the reading is worth stating because it decides thousands of sites:
where the facts join across constructors the lattice *does* have words for both
sides, so what is undischarged is the premise that a web is one quantity — the
analysis unit's own boundary — not a missing word.

## 6. The `opaque` refusal, bounded

A1 modelled a raw `call` as defining and reading every register the procedure
names, which is right only where the ABI is not in the text. `frameproc`'s
`_Info` already computes per-callee may-define and live-in sets, so
`frameproc.call_summaries` reads them at their fixpoint and `_Graph` bounds each
call node by its callee's pair. A call whose callee this program does not hold —
a target outside it, a `call` to a label rather than an entry, a `swc` arm set,
a dynamic `dcall` — stays opaque, and so does every escaping transfer.

`_width_webs` (inside `repolish`, ahead of `_norm_widths`) keeps the
conservative model deliberately: it runs over **unsettled** trees where no
callee's summary has settled, and the web partition it computes decides emitted
spellings. That is the same reason A1 left the liveness and the block converter
name-keyed.

## 7. The interprocedural scope (L2b(a))

An entry-live web is a **parameter**, and a parameter's denotation is a fact about
the call graph. L2 refused all of them (`WEB_ENTRY` → `T_ENTRY`). L2b puts
procedures in the same solve rather than beside it: `denote._CallGraph` collects
every `pcall` site with its statement path, and

- a **parameter** takes one `("arg", caller, path, expression)` fact per call
  site. `_ev` evaluates the argument *at the caller's own path*, so the caller's
  webs answer for it — one mechanism at both scopes, which is the plan's own test
  in its §7.2. The join over the sites is the meet the plan asks for.
- a **return** — a `pcall`'s bound name — takes one `("retof", key)` fact per web
  the callee's `ret` statements leave in that name. Where control can fall off the
  callee's end, or a `ret` names no web for it, the caller keeps `T_CALL`.

**Which call graphs close.** `frameproc.Calls` already decides this and L2b reuses
it rather than restating it: a computed transfer no arm set enumerates
(`open_flow`), the play entry, an RTS-trick landing (`prog.landings`), a procedure
a foreign `goto` enters, and a `call`/`callb`/`swc` arm target all leave the
callers unenumerable, and there the parameter keeps `T_ENTRY`. A program whose
`landings` is `None` — a *parsed* one, which has not enumerated its entries — is
open for every parameter. **Recursion** (`_cyclic` over the `pcall`/`call`/`callb`
edges) widens to `T_RECUR`, which the charter states; the fixpoint would in fact
converge through a cycle, and the rule is kept because it is what was specified.

**Monotonicity and finite height still hold, and here is why.** Nothing about the
lattice changed, so the height argument of §2 is untouched: the new facts are
ordinary value facts over the same finite lattice. The new *edges* are
caller→callee (an argument) and callee→caller (a return), and both read
`self.val` — the previous round's solution — so every unknown's value is still a
join of monotone functions of other unknowns' values, and `_run` still ascends
(`join(previous, resolved)`). The constraint graph gains cycles where the call
graph has them, and a cycle in an ascending fixpoint over a finite lattice
terminates; `_ROUNDS = 24` bounds it regardless, and an unknown still moving there
is ⊤ with cause `rounds`. The fact sets are seeded once and never grow, so the
round cap can only leave ⊤ behind and never a wrong answer — the same clause §3
already carried.

**What still widens, and why.** `T_OPAQUE` is untouched: an escaping transfer and
an unenumerable computed transfer are exactly what the charter says widen to ⊤,
and they are `frameproc._Graph`'s own refusal, not a scope the call graph can
reach. `T_ENTRY` survives for every procedure above whose callers do not close.

## 8. roles folded in (L2b(b))

`roles.py` reads a persistent cell's *update shape* and names it
`cursor`/`accumulator`/`counter`/`flags`/`parameter`/`vm`, and **licenses
nothing** — the "computes the fact and refuses to consume it" shape
docs/denotation-solve.md §2 diagnoses. L2b makes the lattice carry that reading
instead: `count`/`acc`/`flags` are roles' three scalar names, `cursor` was already
`idx`/`row`/`addr`, and `denote.ROLE_OF` is the one map between the two
vocabularies. `pred` has no roles name and `vm` has no constructor, and both gaps
are reported rather than closed by fiat.

The solve **derives** the reading from its own transfer rules over both webs and
cells; it does not import `roles`. That is deliberate: consuming roles' answer
would make the two agree by construction, and the point of the landing is that
they can be *measured against each other*. `tools/denote_census.py` prints the
cell-for-cell matrix, and the disagreements are the finding
(docs/denotation-solve-baseline.md §6).

Two structural reasons the two disagree at all, both visible in the matrix:

- **roles reads updates, the lattice reads definitions and uses.** roles classifies
  a cell from the shape of the term stored into it; the lattice joins every fact
  about it, an index use included. A cell roles calls a `counter` whose value the
  lattice cannot place is ⊤ in the lattice and `counter` in roles.
- **The populations are not the same.** roles' cells are `idioms.state_cells` with
  a witnessed update; the lattice's are every constant-address store target. The
  census reports both totals beside the shared intersection.

## 9. The pointer residue, and the premise it waits on

L2b's gate was pointer-deref reach and it did not move (661 of 2,319, 28.50%).
The landing's contribution there is that the residue is now *named*: of the 1,658
⊤ pointer-rooted derefs, L2 classed 1,481 as `⊤-unvocabularised` and L2b classes
252, because the shapes are the catalog's `word-pack` and `lane-insert` rows and
the lattice now reads both.

What the remaining refusal waits on is **not a lattice change**. Measured at the
fixpoint over 624 artifacts, those sites go through 262 pointer cells whose
definitions are 303 `word-pack`, 335 `lane-insert` and 87 neither, and:

- **219 of the 303 packs do name a declared pair.** None has two byte lanes and no
  pair, so `datadecl.decl_pairs` is not what is missing.
- **The same cells are also patched one lane at a time**, and a lane insert claims
  nothing (§4's third clause: the transient between the two stores is a value a
  deref may reach), so the cell is `addr(S,⊥) ⊔ ⊤ = ⊤`. The premise is an
  **ordering proof**, and it is rung (f)'s kind of obligation rather than the
  lattice's.
- **Its ceiling is small.** Of the 147 cells carrying a lane insert, 134 have a
  lane whose own denotation is ⊤, so an ordering proof would leave them where they
  are.

Stated so the next landing has a target rather than a number.
