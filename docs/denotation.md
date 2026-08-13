# The denotation solve, as landed (A2 / L2)

`deity_informant/denote.py`. The implementation of docs/denotation-solve.md §3:
one monotone analysis whose unknown is what a value **denotes**. **A2 emits no
byte** — `tools/emit_identity.py` is byte-identical across it — so what this
document specifies is the solution, its evidence records and the census
`tools/denote_census.py` reports.

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
⊤                        unknown
```

`idx` carries a *set* of columns because a record's parallel columns are read at
one index (`m_5596`/`m_5597`/`m_5598` of `m_5591[263] stride 8`), and an index
into several columns of one record is one index, not two.

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
| anything else | `⊤` | |

Finite height: `S` is a subset of the declared bases, `addr` nests one row deep,
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

Two soundness clauses worth stating because they are the ones that could be got
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
