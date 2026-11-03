# Machine code as C

`deity_informant.cgen` translates the entry points of a 6502/6510 program to C99 with no
`goto`: a `.c` holding only the code and a `.h` holding the data (each memory region's origin,
length and initial bytes). The `.c` names nothing by address or content, so two programs with
the same code and different data, or the same program at another origin, give a byte-identical
`.c`. Every path the static lift reaches is translated; one it cannot express calls `trap(k)`.

```python
from deity_informant import cgen

c, h, info = cgen.translate(image, [(0x1000, "init"), (0x1003, "play")],
                            volatile=[(0xD400, 0xD41D)], sensitive_reads=(),
                            facts=None, loaded=(0x1000, 0x1B5F), header="tune.h")
```

```bash
python -m deity_informant.cgen tune.bin --org 0x1000 --entry 0x1000:init --entry 0x1003:play \
    --volatile 0xD400-0xD41D -o tune.c --header tune.h
python tools/cgenplay.py tune.sid --psid --volatile 0xD400-0xD41D -o out/   # translate + check
```

| Input | Meaning |
|---|---|
| `image` | the 64 KiB memory every entry starts from (the load image over power-on RAM) |
| `entries` | `(pc, name)`: each becomes `void name(uint8_t a)`, `a` the accumulator |
| `volatile` | `[(lo, hi)]` I/O ranges (`hi` exclusive): their writes are kept in order |
| `sensitive_reads` | I/O addresses whose reads are kept, throw-away reads included |
| `facts` | `trace.Facts`, used only for targets of computed jumps the code does not resolve (below) |
| `loaded` | the `[lo, hi)` bytes the program was loaded to (default: its code span) |
| `header` | the name the `.c` includes |

`info` holds the stage times (`lift`, `mlir`, `layout`, `structure`, `print`), the regions
`[(origin, length)]`, the code span, the traps in the `.c` (`{k: ("deopt", pc)}` or `("ind", pc,
...)` for a computed jump with no target), the computed jumps resolved from the code (`jumps`),
the fact edges used, the lift's `stats`, whether some access has an unknown address and the
`structure` counts: C `functions`, outlined `tails`, and the structuring variables made for
multi-entry loops (`entries`), multi-exit loops (`exits`) and joins (`flags`).

The host supplies `uint8_t io_read(uint16_t)`, `void io_write(uint16_t, uint8_t)` and
`void trap(uint16_t k)`, which must not return (`longjmp`, or abort the call). Each entry
starts as `wholeprog`'s `Program.call` does: `X = Y = 0`, flags clear, `SP = $FD` below the
caller's return address. `tools/cgenplay.c` is a host harness.

## Pipeline

1. **Lift.** `wholeprog.lift(image, entries, volatile, facts, entry_sp=0xFD, loaded)`,
   static: no facts but the targets of computed jumps the code does not resolve; the entry SP
   is the calling convention below and `loaded` ends every table
   ([wholeprog](wholeprog.md#static-proofs), *Table extents*, *Stack extent*). Functions are
   named by ascending entry PC, deopts become `trap(k)` with `k` numbered in the order the
   lift's functions and blocks meet them.
2. **MLIR** (`emit.py`). Each lifted function is a private `func.func` taking and returning the
   register file as values; its registers, stack slots and block-crossing temporaries are
   rank-0 `memref.alloca`s, memory one global `@M`, volatile accesses `io_read`/`io_write`
   calls. Each entry is a public function calling its body with the entry registers. A call of
   a function the lift calls from more than one site carries `no_inline`; a trap returns
   `ub.poison`, as `trap` does not return.
3. **Optimize** (`mlir.py`). Upstream passes only: `canonicalize`, `mem2reg`, `inline`,
   `symbol-dce`, `cse`, `sccp`, printed in generic form as a `cf` CFG (`cf.br`, `cf.cond_br`,
   `cf.switch` with block arguments). The inliner honours `no_inline`, so a callee with one
   call site is inlined and the others stay functions; when optimization leaves a `no_inline`
   callee with one call site the pipeline runs again with that call inlinable.
4. **Liveness** (`printer.Module.prune`). An optimistic liveness over the whole module: a
   store, an I/O write, a trap, a read that may meet a read-sensitive address and a branch
   need their operands; a used result of a function needs the values its returns give; a used
   parameter needs its call sites' arguments. Calls pass only used parameters and functions
   return only used results (none: `void`, one: its type, more: a `struct rW_W...` named by
   the widths). Upstream `--remove-dead-values` would do this, but in LLVM 22 it rewrites a
   `cf.cond_br` passing one value to both successors into an invalid branch, so it is not
   used.
5. **Layout** (`regions.py`): the memory partition below.
6. **Structure** (`structure.py`): tail outlining, then DREAM (below).
7. **Print** (`printer.py`, `csyntax.py`). The generic text is parsed for the operations the
   pipeline leaves (`func`, `cf`, `arith`, `memref`, `ub.poison`; anything else raises), the
   structured statements are printed with each variable declared at the innermost scope
   holding its uses, and the C is run through `clang-format --style=LLVM`.

The printer reads `mlir-opt --mlir-print-op-generic` output: the image the tools come from
(`docker/mlir.Dockerfile`, apt.llvm.org LLVM 22) has no Python bindings, the bindings are not
published as a pinned wheel for that release, and the generic form is one regular syntax for
every operation, so a small line parser replaces a native dependency.

## Structuring

Each function's blocks become a graph whose nodes hold statements. An edge carrying block
arguments gets a node of copies (the arguments are C variables assigned on the edge, a
parallel assignment); an edge to a block that only returns is a `return`.

**Tail outlining.** A node other than the entry that has two or more predecessors outside the
nodes it dominates, and from whose dominated nodes no edge leaves (every path from it returns),
becomes a static function taking the block arguments it is entered with and the values its
nodes use but do not define (constants are reprinted); each entering edge becomes `f(...);
return;` (`return f(...);` for a function with results). Tails are found innermost first, in
post order of the dominator tree, until none is left, so a nested shared tail is a call inside
the outer one.

**DREAM.** The rest is structured by the pattern-independent algorithm of Yakdan, Eschweiler,
Gerhards-Padilla and Smith, "No More Gotos: Decompilation Using Pattern-Independent
Control-Flow Structuring and Semantics-Preserving Transformations" (NDSS 2015). Nodes are
taken in DFS post order. A node heading a cycle has its loop structured; otherwise the region
it dominates, when it has at most one successor outside, is replaced by its statements. An edge
to the function's exit, `break` and `continue` are jumps, not successors, so most regions
qualify.

- *Reaching conditions* are BDDs (CUDD through `dd`, variable order fixed by first use in
  DFS order). Branch conditions are variables; a comparison of a value with a constant (after
  stripping zero extensions and constant additions, subtractions and exclusive-ors) and a
  `cf.switch` case are atoms `x == k` of a selector family whose atoms exclude each other.
- *Condition-based refinement*: the region's nodes, in topological order, are emitted under
  their reaching conditions; the first literal (least BDD level) the head node's condition
  implies, and no enclosing test decides, splits the remaining nodes into `if`/`else` groups,
  a node joining a group only after its predecessors in the region have. A group placed after
  a node that jumps away no longer has that node's reaching condition in its context, and the
  last node of a region without successor is unconditional.
- *Condition-aware refinement*: when that literal is an atom and the nodes fall into three or
  more groups of disjoint outcomes of its family, they become a `switch`; after structuring, a
  chain of three or more `if`/`else` tests of one family is one too. A `switch` arm that would
  `break` a loop stays an `if` chain.
- *Joins*: a node whose reaching condition needs more products than it has entering edges is
  reached by a flag those edges set instead (initialized at the region's head).
- *Loops*: a loop is the natural loop of the back edges into its head (nodes reachable from
  the head that reach a latch without it). A multi-entry (irreducible) loop gets a dispatcher
  on a structuring variable set on every entering edge and on the back edges, as in DREAM. The
  loop grows by the exits only it reaches, keeping the exit of its last node as the successor;
  if more than one successor remains, each exit sets a structuring variable and leaves through
  one dispatcher after the loop. The body, its back edges `continue` and its exits `break`, is
  structured as above; the loop prints as `while` when it starts with its exit test, as `do`
  ... `while` when it ends with one and has no other `continue`, else `for (;;)`.
- *Tests an enclosing test decides* are dropped, and each condition prints as an irredundant
  sum of products (Minato-Morreale ISOP) where its enclosing tests and context are don't-cares.
  Structuring variables are left out of those contexts, as statements assign them.

Names come from structure alone: C functions are `f1, f2, ...` in the order they are first
called when printing (entries first), variables `v1, v2, ...` in order of first use. Callees
are defined before callers; only recursive functions need prototypes.

## Memory

- **Constant code** is the bytes of the planned instructions that the lift treats as constant
  (no store's address set holds them) and that no constant-address store writes. The `.h`'s
  `CODE` is the span from the first such byte, or byte of a table the lift read jump targets
  or cell values from, to the last; when the `.c` needs it, the data regions inside it are
  its slices (`#define R2 (CODE + (R2_ORG - CODE_ORG))`), so each byte is held once.
- **Data regions** `R0, R1, ...` (ascending address) are the maximal runs of other bytes that
  some access may touch, outside volatile ranges. An access's interval comes from its address
  expression (constants, an 8-bit index adds `[0, 255]`, masks, shifts, the hull of what flows
  into a block argument or a function's parameter from its call sites, a loop-carried value
  enumerated to its fixpoint or keeping its initial interval); wider than `$200` is unknown.
  An unknown access may touch every byte of `loaded`, so then every non-code byte of it is in a
  region. Zero page, the stack page and RAM outside the image are regions when accessed.
- An access whose interval lies in one region is `Rk[off + index]`, `off` relative to the
  region and the address taken modulo its width (16 bits, 8 for a zero-page one) as the 6502
  wraps it. One in volatile memory is `io_read`/`io_write`. One wholly in constant code is
  `oob_rd`/`oob_wr`. Any other goes through `rd(a)`/`wr(a, v)`: the regions (by the `Rk_ORG`
  symbols, so data pointers holding original addresses resolve), the volatile ranges, then
  `oob_rd`/`oob_wr`.
- `oob_rd` returns the original byte of `CODE` (0 outside it), `oob_wr` discards; defining
  `CGEN_OOB` replaces both with the host's. A store the lift found may hit constant code or a
  table it read is followed by `codechk`, which traps when a byte of the span outside the data
  regions would change.

Address constants print relative to the region, code span or loaded image holding them
(`R2_ORG + 0x10`, `CODE_ORG + 0x21`, `IMG_ORG + ...`): a constant reaching an access address
through additions, casts, selects and block arguments, the cases of a computed-jump switch, and a
pair of constant byte stores into a pointer pair (the zero-page pointer of a `(zp),Y`/`(zp,X)`,
a `JMP (ind)` vector, a three-byte operand, or two stack bytes, as a `JSR`'s return address
when the stack is kept in memory) whose 16-bit value lies in the program: `LDA #<tab` then
prints `(uint8_t)(R3_ORG + 4)`. A constant or-ed into a computed jump's target (the high byte of a
`JMP` whose low operand byte is a cell) prints so too: `R16[0] | CODE_ORG`. Not covered: an
address half stored without its partner in the same block, or used other than as an address
(compared, added to data).

## Traps and computed jumps

The lift resolves a `JMP (ind)` whose pointer is stored from loads of a constant table (or
from constants, or is a constant word of the code span no store writes), a `JMP`/`JSR` whose
operand bytes a store writes so (in the same call, or earlier: then from the values every
store may leave in them), and an `RTS`/`RTI` that returns through an address pushed so, from
the code alone ([wholeprog](wholeprog.md#static-proofs), *Computed jumps*): the targets are
the table's words over the index values inside the table's extent, which no store may change.
An index the code does not bound, such as a command byte of song data, reads the table up to
where the layout ends it (*Table extents*: a split table's low column at its high column, any
column at the next object the code accesses or the end of `loaded`), and an index guard
traps before a load past it. Such a jump is a switch over code addresses (`case CODE_ORG +
0x23:`) whose last target is the default, with no trap; a `JSR` through cells to functions is
a switch of calls. `info["jumps"]` lists them (`{pc: [target, ...]}`). Programs with the same
code and tables give the same `.c`; a table a store with known addresses may write leaves the
transfer unresolved. An unresolved one becomes a switch whose default traps; `facts` supplies
its targets: `jump_facts` keeps only the edges leaving those instructions, and the `.c` then
depends on the run that made the facts, which `info["facts_edges"]` reports.

Code cells come from the lift too: every byte of a reached instruction that a store's address
set holds (an indexed store's base plus each value of its bounded index, as a player clearing
its own operands with `STA abs,X` in a counted loop) is data, so its stores and the
instruction's reads go to its region with no `codechk`. An opcode cell every store writes
with known values (constants, table bytes, or its own byte saved with `PHA` and restored with
`PLA`) dispatches over them with no trap. Other traps: a deopt the lift keeps (an index past
its table's extent, an opcode cell some store writes with unknown values holding a value
outside its set, a store whose address is unknown into constant code or a table that
changes it, a store that may hit the stack, a guard it could not prove).

The stack is the calling convention's: every entry starts at `SP = $FD`, so the stack bytes
the program can hold are known (*Stack extent*). A pointer access is not checked against
page 1 by its pointer's value: loads read the stack exactly (pushes and return addresses are
also stored where a load may reach page 1), and a store that may hit the stack bytes traps
before it does.

## Validation

`tests/test_cgen.py` compiles each output with gcc (`-std=c99 -Wall -Wextra -Werror`) against
`tools/cgenplay.c` and compares the volatile writes (and sensitive reads) of many consecutive
calls, memory persisting, with `wholeprog.reference_call`; it checks relocation and data
invariance, the absence of `goto`, the structuring (shared tails, multi-exit and two-entry
loops, switches from compare chains and jump tables, kept callees' parameters, wrapping
addresses), computed jumps and calls through operands written from tables (in the same call
or an earlier one), an index past a split table's extent trapping, an opcode restored through
the stack, a pointer into zero page with no stack check, a jump's or-ed high byte under
relocation, determinism (repeated, with the blocks shuffled and under other hash seeds) and an
llvm-mos build. `tools/cgenplay.py` does the same for a PSID or raw image (`--loaded LO-HI`
names the program's bytes in a raw 64 KiB state, as a memory dump after an init routine) and
prints `tools/cmetrics.py`'s table, the sizes, the computed jumps resolved from the code, the
traps and the stage times.

`tools/cclones.py` measures how much of a `.c` repeats up to names and constants (and which
functions are near-identical): the share a clone-folding step could remove where a player
unrolls its per-voice code.
