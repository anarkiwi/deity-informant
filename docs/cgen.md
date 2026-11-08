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
                            facts=None, loaded=(0x1000, 0x1B5F), header="tune.h", init=0,
                            songs=range(3))
```

```bash
python -m deity_informant.cgen tune.bin --org 0x1000 --entry 0x1000:init --entry 0x1003:play \
    --init init --volatile 0xD400-0xD41D -o tune.c --header tune.h
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
| `init` | the index of the entry the host calls once, first, on `image` (`None`, the default: entries in any order); run, not lifted (*Init*) |
| `songs` | the range of values of `A` the `init` entry is called with (default `[0]`); another traps |
| `machine` | runs init (`postinit.run`; default the reference interpreter over `volatile`, whose device bytes hold what was written) |

`info` holds the stage times (`lift`, `mlir`, `layout`, `structure`, `print`), the regions
`[(origin, length)]`, the code span, the traps in the `.c` (`{k: ("deopt", pc)}` or `("ind", pc,
...)` for a computed jump with no target), the computed jumps resolved from the code (`jumps`),
the fact edges used, the lift's `stats`, whether some access has an unknown address and the
`structure` counts: C `functions` (and the identical ones `folded` into them) and the
`gotos` printed, the `provenance` of the printed constructs in the machine code (below) and
the instruction starts translated (`starts`).

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
   copies of one routine, whole blocks that differ only in their operands (a player's
   unrolled per-voice code), are one function of the copy's index, its operands
   `base + stride * k`. Functions are
   named by ascending entry PC, deopts become `trap(k)` with `k` numbered in the order the
   lift's functions and blocks meet them; a deopt of an instance function is one trap for every
   copy (`("deopt", pc, function)`, the first copy's start). With `init` the other entries
   are lifted from the state init leaves (*Init*).
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
   parameter needs its call sites' arguments. A result a function returns unchanged from a
   parameter (on every return, through block arguments and calls forwarding theirs) is read as
   the argument its call passes, as a register the callee leaves alone, and a function left
   with no effect and no loop is not called. Calls pass only used parameters and functions
   return only used results (none: `void`, one: its type; more: `void`, each result left in
   the static variable `rvJ` of its register-file position `J`, read into locals right after
   the call, as the 6502 leaves its registers across `RTS`). Upstream `--remove-dead-values` would do this, but in LLVM 22 it rewrites a
   `cf.cond_br` passing one value to both successors into an invalid branch, so it is not
   used.
   An 8-bit add whose carry only feeds the add of the byte above it (`lo = a + b`,
   `hi = d + e + (lo < a)`, an `ADC` chain) becomes one 16-bit add of `(d:a) + (e:b)`, whose
   bytes replace `hi` and the uses of `lo` after it.
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

## Init

With `init`, `translate` runs that entry once per song on `machine` and lifts the other
entries from the memory the first song's run leaves; init itself is not translated. The bytes
it stores outside the stack page and the volatile ranges are variables whose values are the
song's (`free`, [wholeprog](wholeprog.md#init)): the analysis takes none of them, so the `.c`
is the same for every song, song count and song data, and only the `.h` differs. Operand pairs
init sets from one word at constant offsets are one variable (`alias`), from the provenance
of its run on the jennings core (`postinit.Run`: each stored byte tagged as a constant, a byte
loaded at an address or through a word, or a byte of an addition chain on a word) when init
returns on the reference interpreter. The `.c`'s init function puts the state in place:

- a song outside `CGEN_SONG0` and the `CGEN_SONGS` after it traps;
- init's volatile writes are replayed in order (`CGEN_IO_*`, each distinct sequence once and
  `CGEN_IO_ROW` per song when there are several);
- the song's values are copied into the variables (`CGEN_CP_*`, per song `CP_FROM[s]` to
  `CP_FROM[s + 1]`): the `.h` holds the image, or the first song's state, whichever needs the
  smaller tables, and a song's values where they differ from it are copies of the bytes no
  song changes (the longest run its values continue as, `bytes.find` over doubling then
  halving lengths, when longer than a copy's six table bytes) or literal bytes (`CGEN_LIT`),
  cut where an array ends; bytes play never reads are not held.

The host calls init once, first, on the image. An init that probes the hardware and patches
play (a SID-model probe) is settled at the machine's value.

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
   may run before the first test), or are stores (none bounds-checked or picked among regions:
   those print as `if`/`else`) and I/O accesses whose values only the block reads: then they
   are its test literal's effects, a comma expression before the literal
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
| `functions` | `{C name: {"entry", "index", "calls", "copies", "pcs"}}`: the entry pc, whether it takes an instance index, `[site, target]` of each call and tail call of it (a tail call printed in place names the instruction taking the edge), the entries of the copies folded into it, the instructions (every copy's) its operations were emitted for |
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

- **Constant code** is the bytes of the planned instructions (`Program.starts`) that the lift
  treats as constant
  (no store's address set holds them) and that no constant-address store writes, and the
  bytes of the tables the lift read as constant (jump targets, cell values, loads given their
  bytes' hull). `CODE_ORG` is the first of them (constants into code print relative to it).
  Only the tables and the constant bytes a bounded access may reach are held in memory (all
  of them when a store of unknown address may need them for its code check): their runs,
  joined across gaps of data region bytes only (any other byte between them would be held as
  data the program never reads) and widened over the data regions they overlap or abut (one
  array, so `rd`/`wr` test one range for both), are the spans `C0, C1, ...`; those regions are
  their slices (`#define R2 (C0 + (R2_ORG - C0_ORG))`), so each byte is held once, and
  `Cj_DATA` marks a span's data bytes up to its last constant byte (`Cj_BITS`; every byte past
  it is data). A load of unknown address
  reaching other code reads `oob_rd`.
- **Data regions** `R0, R1, ...` (ascending address) are the maximal runs of other bytes that
  some access may touch, outside volatile ranges. An access's interval comes from its address
  expression (constants, an 8-bit index adds `[0, 255]`, masks, shifts, the hull of what flows
  into a block argument or a function's parameter from its call sites, a loop-carried value
  enumerated to its fixpoint or keeping its initial interval, a load of a closed cell the
  hull of its values (`Program.sets`) and of a byte with bounded stores its bound
  (`Program.bounds`), a mask or an or-ed constant that keeps the interval in one block);
  wider than `$200` is unknown. A lifted function's parameter takes the hull its call sites
  pass met with the values the lift finds its register holding at its entry on every run
  (`Program.regs`, every copy's entry), a call's result the hull over the callee's returns of
  those at each `RTS`, and a byte an `LDA`/`LDX`/`LDY`/`LAX` loads those its register holds
  after it: a voice index a routine saves in an operand and reloads keeps its values. A load
  the lift proves inside a hull (`Program.proven`) touches that hull alone. `sensitive_reads` also goes to the lift, which makes no
  throw-away read that cannot reach one of them.
  An unknown access may touch every byte of `loaded`, so then every non-code byte of it is in a
  region. Zero page, the stack page and RAM below the image are regions when accessed; a byte
  past the image is one only when a bounded store or a store with an address set writes it (the bytes between an instance's copies' bytes stay in
  their array): a read of any other byte past the image is out of bounds. A load whose bytes
  in the image lie in one region or held span (all its bytes when none is), every one held,
  tests its address against it and reads `oob_rd` outside (`(uint16_t)(a - R2_ORG) < R2_LEN ?
  ... : oob_rd(a)`); from a constant base plus a byte inside one held span, through the span's
  bounds-checked reader `rdCj(base, index)`. One the lift proves (`Program.proven`: the bounds
  of the bytes its index comes from) inside one region or held span indexes it unchecked.
  Every byte a store with an address set may write (`Program.written`: a block copy's
  destinations past the image) is in a region, which pointer reads find through `rd` or a
  held span; the stack page
  is accessed only by a load that may read it, since pointer accesses are kept out of page 1
  ([wholeprog](wholeprog.md#static-proofs), *Pointer windows*).
- An access through a pointer with an address set (`Program.addr_sets`: a block copy's
  loads and stores) that lies in the regions and held spans indexes them directly, testing
  only those it spans (the last untested); a 16-bit base plus a byte index is the base's
  element indexed by the byte, as `(zp),Y` is. When the bases that reach each tested part of
  the set (its addresses less the index's interval) are disjoint intervals, the test reads the
  base alone, the same for every index (a block copy's loop over a page tests its pointer, not
  each byte). One at a constant base plus an index whose set
  spans several regions (operand cells of several copies) goes through `rd`/`wr`.
- A store the lift bounds to the array holding its base (`Program.bounded`: an index any byte,
  the store reaching code past the array) writes the region holding its base inside it and
  calls `oob_wr` past it: `if ((uint16_t)(a - Rk_ORG) < Rk_LEN) Rk[...] = v; else oob_wr(a,
  v);`, the write-side counterpart of `rdCj`.
- A load of unknown address through a pointer whose hull the lift bounds (`Program.reach`,
  [wholeprog](wholeprog.md#static-proofs), *Pointer reach*: bases from tables read inside their
  extents, constants, a relocation constant added with carry, an offset in `Y`, a pointer
  advanced from them by the lengths it reads, a table byte tested before its pointer uses it, a
  pointer read through one or from song data, a return stack's pop of what its push stored)
  reads one span: from the first non-code byte of the hull inside `loaded` (and the bytes past it,
  below the volatile ranges, a block copy writes, `Program.written`) to its last, every base
  inside `loaded` in it, held as one array `Cj` (code bytes and the bytes between the program
  and those inside it held too) and printed
  `rdCj(base, y)`, the address as a 16-bit base plus a byte (`(zp),Y`'s pointer and index, a
  constant offset below 256, else 0): `noinline`, it reads `Cj` where the address lies in it,
  else `oob_rd`, so a pointer below the program (zero page, a pointer still 0) or walked past
  it reads what the 6502 would. Where the hull the
  lift proves on every run (`Program.proven`: no table taken to be read inside its extent)
  lies inside `loaded`, the span is that hull and the
  load indexes it unchecked: `CGEN_IX(Cj, base - Cj_ORG, y)`. Not taken when the
  hull meets a volatile range inside `loaded`, or holds no data byte of it; then `rd`.
  `oob_rd` and the regions only it reaches are left out when no load needs it.
- In an instance function (unrolled copies folded into one function of the copy's index `k`,
  every call passing a constant), a value computed from `k` alone (not a branch condition: a
  condition of `k` alone tests such a value) has one definition at the function's entry per
  distinct set of per-copy values: `base + k` (or `base - k`) where they step by one with `k`,
  else an element of a `static const` table `Kn` indexed by `k`, in 8 bits where the offsets
  from the least value fit (one indexed load, where a stride would be a multiplication the
  6502 does not have); an address keeps its least value as a constant, so it prints relative
  to its region. An access is placed per copy: its
  interval for each value of `k` lying in one region (or held span) indexes it directly, as
  each copy's own operand cells at a stride do; the interval over all copies is then held in
  one span with the regions it touches. Per-copy state whose offsets do not fit 8 bits (copies
  further apart than that over all copies, as each copy's operand cells at a stride of `$88`)
  is reached through per-copy pointers: the accesses at one table's offsets from constants
  that lie in one region or held span for every copy are grouped, their constants covered by
  the fewest windows of 256 bytes, and each window is a `static uint8_t *const Kn[]` of the
  copies' addresses of its least constant (`C1 + 0x13`, read once per call); each access is
  that pointer indexed by a byte (`v3[0x3C]`), as `(zp),Y` reads a field at a base held per
  copy, with no 16-bit index.
- A value a block of an instance function receives on edges the index alone chooses (every
  branch from the block's immediate dominator decided by `k`, no back edge into it), alike on
  those edges up to per-copy values, is the operation of the values `k` chooses, placed in
  the block: a compare that only some copies have (`CMP #k`, the first copy testing its load
  against zero) is one compare of the value with the index or a constant table (`v == k`,
  `v == Kn[k]`), not a choice of compares on `k`. An equality of a difference with zero reads
  as the equality of its operands.
- An access whose interval lies in one region is `Rk[off + index]`, `off` relative to the
  region and the address taken modulo its width (16 bits, 8 for a zero-page one) as the 6502
  wraps it (a fixed address in a region whose origin moves with the loaded image, one
  starting in it or where it ends, is `Rk[addr + index - Rk_ORG]`, so relocating the
  image leaves the `.c` unchanged); a load of constants in one span is `Cj[off + index]`
  alike. One in volatile memory is `io_read`/`io_write`. One wholly in other constant code is `oob_rd`/`oob_wr`. Any
  other goes through `rd(a)` or `wr(a, v)`: the volatile ranges and the arrays (by the `Rk_ORG`
  symbols, so data pointers holding original addresses resolve), then `oob_wr`; each finds the
  array by tables of their origins and lengths (`OOB_*` over the regions outside the spans and
  the spans for `rd` (`cgen_rd`), `WR_*` over every region for `wr`). They and
  `codechk` are `noinline`, as llvm-mos would otherwise copy them into every caller.
- The arrays' definitions are the `.h`'s, as where a song puts its data decides them: the
  `.c` defines them by `CGEN_STORAGE` (the held spans, the regions not inside one, a span's
  data bits); a region inside a held span is a `#define` of its slice there; `cgen_rd` and
  `wr` loop over the `CGEN_ARRAYS` and `CGEN_REGIONS` lists (`wr` writes a span's data bytes
  through its slices). An access's offset into a held span at a byte of no instruction (a
  table of song data) is a name the `.h` defines by the first instruction reading it (`C1_AT1A3`
  for the one at `CODE_ORG + 0x1A3`, `_1`, `_2` for its further constants). Tunes of one
  player that differ in their song data and its placement give the same `.c`.
- A load of a constant byte (constant code or a table the lift read as constant) at a fixed
  address prints as a name the `.h` defines, relative to the origin that places the byte
  (`CODE_KA3` for `CODE_ORG + 0xA3`): the `.c` holds neither its value nor its address, and the
  compiler folds it.
- `oob_rd`, a read outside every array its site may address, calls `trap(0xFFFF)`
  (`OOB_TRAP`). With `CGEN_OOB_EXACT` defined it is `cgen_rd`, the byte the 6502 reads there:
  a region's or span's live byte, an I/O register, 0 where the program holds none. `oob_wr`
  discards. Defining `CGEN_OOB` replaces both with the host's. `trap` is declared `noreturn`
  and `leaf`: a host compiles it apart from the `.c`, and it calls back into nothing there.
  A store the lift found may hit constant code or a table it read is followed by `codechk`,
  which traps when a constant byte of a span would change.

Address constants print relative to the region holding them, or to the code span or loaded image when the program holds them (any other, a device's or a vector's, prints as it is)
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
column at the next object the code accesses, the first of the jump's targets past its first
byte, or the end of `loaded`), and an index guard
traps before a load past it. Such a jump is a switch over code addresses (`case CODE_ORG +
0x23:`) whose last target is the default, with no trap; one whose low and high target bytes
are a table pair indexed by a register still holding the index (`LDA lo,X; STA op+1; LDA hi,X;
STA op+2; JMP op`) is a switch over the index's values (`case 3:`), the same for every origin; a `JSR` through cells calls one
function whose body is the switch over its targets (the handlers its arms), handed the
operand's low byte, so the high byte relocates in it (`switch (v2 | CODE_ORG)`). `info["jumps"]` lists them (`{pc: [target, ...]}`). Programs with the same
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
computed branch entering one run at three points), pointer reads from split tables an
unbounded byte indexes with a growing `Y` read through one bounds-checked span, a pointer pair
reused as scratch between its uses keeping `rd` only where its scratch values reach, and their
provenance (instruction starts of
the lift), short-circuit conditions, no structuring variables, the structuring (a shared tail kept in its function,
multi-exit and two-entry loops, switches from compare chains and jump tables, kept callees' parameters, wrapping
addresses), computed jumps and calls through operands written from tables (in the same call
or an earlier one), an index past a split table's extent trapping, an opcode restored through
the stack, a pointer into zero page with no stack check, a jump's or-ed high byte under
relocation, determinism (repeated, with the blocks shuffled and under other hash seeds), play
lifted from the state init leaves (a relocator's operands pointer variables bounded to the song
and one variable per source word; init relocating by the song, reading song data, making I/O
writes song data decides or copying a block per song, with the same `.c` for every song, song
count and song data; a probe patching play's opcode settled at the machine's value), each song
from a fresh image, and an llvm-mos build. `tools/cgenplay.py` does the
same for a PSID (its init called once, first: `--init`) or raw image (`--loaded LO-HI`
names the program's bytes in a raw 64 KiB state, as a memory dump after an init routine) and
prints `tools/cmetrics.py`'s table, the sizes, the computed jumps resolved from the code, the
traps and the stage times. It also checks the lift's tail calls (a routine fallen into for its
third voice is one function with three call sites) and instances (unrolled copies at a stride
are one function of the copy's index whose I/O register is `0xC000 + Kn[k]`, `Kn` the
offsets `{0, 7, 14}`, called for each copy in order; copies sharing one trap; determinism and
relocation invariance of a folded program; copies' operand cells indexed directly at their
stride, read once from an 8-bit table; cells at uneven distances, per-copy immediates and
branches on them as constant tables; statements alike only in a prefix of their blocks not
folded; copies at a stride wider than 8 bits reach indexed by a byte from per-copy pointer
tables into a held span and a region, a table per 256-byte window of a copy's fields; a
compare only some copies have one compare with the index or a table; copies entered at a
branch after loads they differ in one class entered at the branch), and a voice loop's index bounding the throw-away read of the indexed I/O store in the
routine it calls through a `BRK` and through a stack switch.

`tools/cgengraph.py TUNE.sid --pc ADDR` prints the structuring graph of the functions holding
an instruction (nodes by instruction address, back edges, copies, `--ops` the operations) and
the printed gotos and loop heads.

`tools/csize.py OUT.c OUT.elf --info info.json --image IMAGE --org ADDR [--header OUT.h]`
attributes a compiled translation's code (an ELF built with `-g`): per C function its bytes
from the line table (inlined code counted where it came from) against the original instruction
bytes of its `pcs` (the instructions code is left for after optimization), split by origin: `trap` (a `trap()` call and the test guarding it), `dispatch`
(`rd`/`wr`/`rdCj`/`oob_*`/`codechk` and the lines calling them), `cell` (a test of a code cell's byte),
`arith16` (16-bit values) and `other`.

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
