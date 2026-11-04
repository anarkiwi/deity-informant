# Machine code as C

`deity_informant.cgen` translates the entry points of a 6502/6510 program to C99, structured
with a `goto` exactly at each edge that has no structured form (*Structuring*): a `.c` holding only the code and a `.h` holding the data (each memory region's origin,
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
`structure` counts: C `functions` (and the identical ones `folded` into them) and the
`gotos` printed, and the `provenance` of the printed constructs in the machine code (below).

The host supplies `uint8_t io_read(uint16_t)`, `void io_write(uint16_t, uint8_t)` and
`void trap(uint16_t k)`, which must not return (`longjmp`, or abort the call). Each entry
starts as `wholeprog`'s `Program.call` does: `X = Y = 0`, flags clear, `SP = $FD` below the
caller's return address. `tools/cgenplay.c` is a host harness.

## Pipeline

1. **Lift.** `wholeprog.lift(image, entries, volatile, facts, entry_sp=0xFD, loaded)`,
   static: no facts but the targets of computed jumps the code does not resolve; the entry SP
   is the calling convention below and `loaded` ends every table
   ([wholeprog](wholeprog.md#static-proofs), *Table extents*, *Stack extent*), with `fold=True`
   ([wholeprog](wholeprog.md#pipeline), *Tail calls*, *Instances*): a routine fallen or jumped
   into is a tail call of it, code two functions share at their entry height is a function, and
   copies of one routine that differ only in their operands (a player's unrolled per-voice code)
   are one function of the copy's index, its operands `base + stride * k`. Functions are
   named by ascending entry PC, deopts become `trap(k)` with `k` numbered in the order the
   lift's functions and blocks meet them; a deopt of an instance function is one trap for every
   copy (`("deopt", pc, function)`, the first copy's start).
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
6. **Structure** (`structure.py`): the dominator-tree translation below, each function whole.
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

A shared tail is a function only where the machine code makes it a procedure: a `JSR` target
or code two lifted functions enter at their entry height (the lift's tail calls); a join inside
one function stays in it, as does code past such an entry that the functions only share
through it.

**The goto rule.** The structured forms are exactly: a fall-through (the machine code's next
instruction), an if/else whose arms rejoin at the branch's immediate post-dominator, a
short-circuit condition, a loop's `continue` (to its head, or to its latch when the latch is
the loop's `do`/`while` condition) and `break` (to its one follow), a switch arm's `break` (to
what follows the switch) and fall-through (into the next arm), a tail call (`return f(...)`)
and `return`. Every other edge is a `goto` to a label; no structuring variables are made. A
block calling `trap`, which does not return, ends its path. Each function goes through these
steps in order, each deciding on the graph the earlier ones leave:

1. *Decided edges.* An edge whose condition contradicts every path to its branch (a `BCS`
   after a `BCC` on the same carry) is dropped; path conditions run over the forward edges
   from the entry and restart at each loop head.
2. *Short-circuit conditions.* A conditional branch entered only from another branch that shares
   one of its targets is composed into it: its edges become the first branch's under both
   tests, joined with the first branch's edge to the same target (two edges to returns of the
   same values, or to copies of the same values into one block, are one target), to a
   fixpoint. Its operations either have no effect and read only data regions or code (so they
   may run before the first test), or are stores and I/O accesses whose values only the block
   reads: then they are its test literal's effects, a comma expression before the literal
   (`a || (R0[x] = v - 1, v != 1)`), kept only where the composed conditions, printed as sums
   of products with no don't-cares and evaluated left to right, read the literal once and
   exactly where the machine code ran the block. The condition prints as `a && b` / `a || b`
   (Minato-Morreale ISOP of the composed BDD).
3. *Tail calls.* A node that only copies and calls lifted functions and returns, or continues
   into such a node, gets a copy per incoming edge, so each jump into a procedure prints in
   place as a call then return (its provenance site is the instruction taking the edge).
4. *Entry selectors.* A cycle entered at two nodes, both from one two-way branch (Ghouls'
   sweeps, entered by a self-modified direction), gets a head choosing the entry by a
   selector variable: the branch's own condition, assigned once on entry, and the constant
   choosing the entry each retreating edge into an entry takes, assigned on that edge. The
   cycle is then one natural loop whose first iteration the condition selects.
5. *Loops* (natural loops of the depth-first back edges; the search takes successors in
   machine-code order, so of a two-entry loop the entry the code falls into is the head). A
   back edge is `continue`; a loop's follow, placed after it, is of the exits it dominates that
   do not end the procedure the one all the others reach, else the one most loop edges leave
   to, else the first; an edge to it is `break`. A loop whose one back edge leaves (through
   copies) a branch with no effect, entered by two or more forward edges and whose other edge
   leaves the loop, prints as `do` ... `while` with that latch as its condition (its
   computations, its test and the back edge's copies as one expression): an edge to the latch
   is `continue`, and the latch's exit follows the loop.
6. *Placement* after Ramsey, "Beyond Relooper: recursive translation of unstructured control
   flow to structured control flow" (ICFP 2022): each node is followed by the joins it
   immediately dominates (nodes with two or more forward edges in) and, heading a loop, the
   loop's follow, in a topological order of the forward edges that takes the ready node first
   in the machine code. A case of a multiway branch it immediately dominates stays in place as
   its arm (arms in machine-code order). An edge to a node not placed so is that node's code in
   place.
7. *Jumps.* An edge to a placed node or a back edge is a jump: to the innermost loop's head (or
   latch) `continue`, to its follow `break`, to the statement that runs next nothing when the
   edge is the machine code's fall-through or the end of an arm of the nearest branch whose
   paths all meet there (no path from the branch reaches what follows the target without
   passing it, a back edge, a loop latch, an exit of its loop and a jump to a node placed after
   the target ending a path), else a `goto`. In a switch arm a `goto` to what follows the
   switch is `break`, and one ending the arm to the case the next arm starts with is a
   fall-through (`__attribute__((fallthrough))`). A jump every arm of an `if`/`else` or of a
   `switch` with a default ends with is printed once after it.

- *Branch conditions* are BDDs (CUDD through `dd`, variable order fixed by first use). A
  comparison of a value with a constant (after stripping zero extensions and constant
  additions, subtractions and exclusive-ors) and a `cf.switch` case are atoms `x == k` of a
  selector family whose atoms exclude each other.
- *Tests an enclosing test decides* are dropped, and each condition prints as an irredundant
  sum of products where its enclosing tests are don't-cares.
- *Switches*: a chain of three or more `if`/`else` tests of one selector family against sets of
  its constants is a `switch`; one whose arm would `break` a loop stays an `if` chain.
- *Loops* print as `while` when the body starts with its exit test, as `do` ... `while` when it
  ends with one and has no other `continue`, else `for (;;)`.

**Provenance.** `info["provenance"]` names, by original machine-code addresses (not in the
`.c`, which stays relocation-invariant), where each printed construct comes from:

| Key | Value |
|---|---|
| `functions` | `{C name: {"entry", "index", "calls", "copies"}}`: the entry pc, whether it takes an instance index, `[site, target]` of each call and tail call of it (a tail call printed in place names the instruction taking the edge), the entries of the copies folded into it |
| `gotos` | `[from, to]` per printed `goto`: the instruction taking the edge (a branch, or the `JMP` the lift threaded into it; each branch of a short-circuit condition with an edge to the target) and its target |
| `loops` | the head of each printed loop |
| `conditions` | the branch addresses composed into each short-circuit condition |
| `switches` | the dispatch, or the first compare of the chain, of each printed `switch` |
| `cells` | the code bytes the lift treats as variables |

In an instance function each construct names every copy's instruction (`Program.functions`'
`copy_pcs`).

Each lifted op carries the instruction it was emitted for ([wholeprog](wholeprog.md#python));
the MLIR carries it as the op's location (`--mlir-print-debuginfo`), which the printer reads.

Names come from structure alone: C functions are `f1, f2, ...` in the order they are first
called when printing (entries first), variables `v1, v2, ...` in order of first use. Callees
are defined before callers; only recursive functions need prototypes. Functions whose
definitions are the same up to their names and the functions they call (those merged so
far) are kept once, until none is left (`info["structure"]["folded"]`), as a player's
per-voice routines often are; the rest are renumbered in order.

## Memory

- **Constant code** is the bytes of the planned instructions (`Program.starts`) that the lift treats as constant
  (no store's address set holds them) and that no constant-address store writes. The `.h`'s
  `CODE` is the span from the first such byte, or byte of a table the lift read jump targets
  or cell values from, to the last, widened over the data regions it overlaps; when the `.c`
  needs it, those regions are its slices (`#define R2 (CODE + (R2_ORG - CODE_ORG))`), so
  each byte is held once, and `CODE_DATA` marks its data bytes.
- **Data regions** `R0, R1, ...` (ascending address) are the maximal runs of other bytes that
  some access may touch, outside volatile ranges. An access's interval comes from its address
  expression (constants, an 8-bit index adds `[0, 255]`, masks, shifts, the hull of what flows
  into a block argument or a function's parameter from its call sites, a loop-carried value
  enumerated to its fixpoint or keeping its initial interval, a load of a closed cell the
  hull of its values (`Program.sets`)); wider than `$200` is unknown.
  An unknown access may touch every byte of `loaded`, so then every non-code byte of it is in a
  region. Zero page, the stack page and RAM outside the image are regions when accessed, and
  so is every byte a store with an address set may write (`Program.written`: a block copy's
  destinations past the image, which pointer reads then find through `rd`).
- An access whose interval lies in one region is `Rk[off + index]`, `off` relative to the
  region and the address taken modulo its width (16 bits, 8 for a zero-page one) as the 6502
  wraps it. One in volatile memory is `io_read`/`io_write`. One wholly in constant code is
  `oob_rd`/`oob_wr`. Any other goes through `rd(a)`/`wr(a, v)`: the regions outside the code
  span (by the `Rk_ORG` symbols, so data pointers holding original addresses resolve), the
  volatile ranges, the code span in memory (`wr` writing its data bytes only), then
  `oob_rd`/`oob_wr`.
- `oob_rd` returns the original byte of `CODE` (0 outside it), `oob_wr` discards; defining
  `CGEN_OOB` replaces both with the host's. A store the lift found may hit constant code or a
  table it read is followed by `codechk`, which traps when a byte of the span outside the data
  regions would change.

Address constants print relative to the region, code span or loaded image holding them
(`R2_ORG + 0x10`, `CODE_ORG + 0x21`, `IMG_ORG + ...`): a constant reaching an access address
through additions, casts, selects and block arguments, the cases of a computed-jump switch, and a
pair of constant byte stores into a pointer pair (the zero-page pointer of a `(zp),Y`/`(zp,X)`,
a `JMP (ind)` vector, a three-byte operand, or two stack bytes at one SP value and that
value less one, as a `JSR`'s return address when the stack is kept in memory) whose 16-bit value lies in the program: `LDA #<tab` then
prints `(uint8_t)(R3_ORG + 4)`. A constant or-ed into a computed jump's target (the high byte of a
`JMP` whose low operand byte is a cell) prints so too: `R16[0] | CODE_ORG`. Not covered: an
address half stored without its partner in the same block, or used other than as an address
(compared, added to data).

## Traps and computed jumps

The lift resolves a `JMP (ind)` whose pointer is stored from loads of a constant table (or
from constants, or is a constant word of the code span no store writes), a `JMP`/`JSR` whose
operand bytes a store writes so (in the same call, or earlier: then from the values every
store may leave in them), an `RTS`/`RTI` that returns through an address pushed so, a `BRK`
through the vector the program stores, and an `RTS` returning past a call's inline arguments,
from the code alone ([wholeprog](wholeprog.md#static-proofs), *Computed jumps*): the targets are
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
invariance, the gotos (a non-series-parallel join's edges, a two-entry loop's second entry, a
computed branch entering one run at three points) and their provenance (instruction starts of
the lift), short-circuit conditions, no structuring variables, the structuring (a shared tail kept in its function,
multi-exit and two-entry loops, switches from compare chains and jump tables, kept callees' parameters, wrapping
addresses), computed jumps and calls through operands written from tables (in the same call
or an earlier one), an index past a split table's extent trapping, an opcode restored through
the stack, a pointer into zero page with no stack check, a jump's or-ed high byte under
relocation, determinism (repeated, with the blocks shuffled and under other hash seeds) and an
llvm-mos build. `tools/cgenplay.py` does the same for a PSID or raw image (`--loaded LO-HI`
names the program's bytes in a raw 64 KiB state, as a memory dump after an init routine) and
prints `tools/cmetrics.py`'s table, the sizes, the computed jumps resolved from the code, the
traps and the stage times. It also checks the lift's tail calls (a routine fallen into for its
third voice is one function with three call sites) and instances (unrolled copies at a stride
are one function of the copy's index whose I/O register is `0xC000 + k * 7`, called for each
copy in order; copies sharing one trap; determinism and relocation invariance of a folded
program).

`tools/cgengraph.py TUNE.sid --pc ADDR` prints the structuring graph of the functions holding
an instruction (nodes by instruction address, back edges, copies, `--ops` the operations) and
the printed gotos and loop heads.

`tools/cclones.py` measures how much of a `.c` repeats up to names and constants (and which
functions are near-identical).

Upstream LLVM does not recover the per-voice routines from cgen's code: lowered to LLVM IR
(`--convert-to-llvm`, `mlir-translate --mlir-to-llvmir`), `opt -passes=iroutliner,mergefunc`
removes almost none of the per-voice code (MergeFunctions
merges only identical functions; IROutliner outlines similar straight-line sequences of one
block whose cost model accepts them, while each voice copy is a multi-block region whose
blocks interleave with the other copies' in the layout), and without its cost model
(`-ir-outlining-no-cost`) the IR grows. The copies are recovered in the lift instead
([wholeprog](wholeprog.md#pipeline), *Instances*).
