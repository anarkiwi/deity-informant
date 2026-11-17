# Machine code as C

`deity_informant.cgen` translates the entry points of a 6502/6510 program to C99, structured
with a `goto` exactly at each edge that has no structured form (*Structuring*): a `.c` holding only the code and a `.h` holding the data (each memory region's origin,
length and initial bytes). The `.c` names nothing by address or content, nor any value init
leaves (*Init*), so two programs with the same code and different data, or the same program at
another origin, give a byte-identical `.c`. Every path the static lift reaches is translated; one it cannot express calls `trap(k)`.

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
| `init` | the index of the entry the host calls once, first, on `image` (`None`, the default: entries in any order); run per argument value, and translated where its lift is exact for every one (*Init*) |
| `songs` | the range of values of `A` the `init` entry is called with (default `[0]`); another traps |
| `machine` | runs init (`postinit.run`; default the reference interpreter over `volatile`, whose device bytes hold what was written) |
| `devices` | what the volatile addresses answer (*Devices*; default the C64's, `c64devices.MODEL`; `None`: any byte) |
| `lines` | mark the `.c` with `#line` directives naming what each line's code comes from (*Translation cost*; CLI `--lines`) |

`info` holds the stage times (`lift`, `mlir`, `layout`, `structure`, `print`), the regions
`[(origin, length)]`, the code span, the traps in the `.c` (`{k: ("deopt", pc)}` or `("ind", pc,
...)` for a computed jump with no target) and what each stands for (`trap_info`, *Trap
metadata*), the computed jumps resolved from the code (`jumps`),
per `(zp),Y` access the words its pointer may hold where the lift finds a set of them
(`ptr_sets`, `{pc: [word]}`, every lift's), the fact edges used, the lift's `stats`, whether
some access has an unknown address and the
`structure` counts: C `functions` (and the identical ones `folded` into them) and the
`gotos` printed, the `provenance` of the printed constructs in the machine code (below), the instruction
starts translated (`starts`), the cells held as C variables (`state`, *State*) and, with
`init`, whether init is `"translated"` or `"replayed"` (`init`), and what the `.c` prints per
original instruction and per check (`emission`, *Translation cost*).

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
   are lifted from the state init leaves and init from the image (*Init*).
2. **MLIR** (`emit.py`). Each lifted function is a private `func.func` taking and returning the
   register file as values; its registers, stack slots and block-crossing temporaries are
   rank-0 `memref.alloca`s, memory one global `@M`, volatile accesses `io_read`/`io_write`
   calls. Each entry is a public function calling its body with the entry registers. A call of
   a function the lift calls from more than one site carries `no_inline`, unless it is a
   continuation function (handed its return address, [wholeprog](wholeprog.md#pipeline)): each
   call is then specialized to its constant return address, so the inline arguments it reads
   through it are constant code bytes and its return a branch; a trap returns `ub.poison`, as
   `trap` does not return.
3. **Optimize** (`mlir.py`). Upstream passes only: `canonicalize`, `mem2reg`, `inline`,
   `symbol-dce`, `cse`, `sccp`, printed in generic form as a `cf` CFG (`cf.br`, `cf.cond_br`,
   `cf.switch` with block arguments). The inliner honours `no_inline`, so a callee with one
   call site is inlined and the others stay functions; when optimization leaves a `no_inline`
   callee with one call site the pipeline runs again with that call inlinable.
4. **Liveness** (`printer.Module.prune`). An optimistic liveness over the whole module: a
   store some load may read back (outside the code span, a byte no load of any entry may read
   by its address interval, nor init's copies, is never observed: such a store is dropped, as
   a return address pulled into a pointer whose reads the lift forwarded), an I/O write, a trap, a read that may meet a read-sensitive address and a branch
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
5. **Layout** (`state.py`, `regions.py`): the cells held as C variables (*State*), then the
   memory partition of the other bytes (*Memory*).
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

With `init`, `translate` runs that entry once per value of `A` in `songs` (its declared
argument values) on `machine`, for the `.h`, and decides the `.c` as if `A` may be any byte:
the declared values reach only the `.h` (`CGEN_SONG0`, `CGEN_SONGS`, a replayed init's
copies), so the `.c` is the same for every count of them. The other entries are lifted for
every value init **takes** (`_domain`), and the declared ones: of the values its run on
`machine` returns with (every `A` is run, as for the bytes it leaves constant), those whose
stores lie in what a store of init's lift may write and on which that lift does not deopt;
values whose machine runs leave one memory and volatile writes share the verdict of the least
of them. A value it does not take (an index past its tables) is one init's translation traps
on. The states the probes of a translated init may leave besides are its runs
(`postinit.forks`) for the least value taken and the declared ones; operand pairs init sets
from one word (`alias`, below) are those of the least value's runs that hold on every state. The
other entries are lifted from the memory the least value's run leaves. The bytes those runs store outside the
stack page and the volatile ranges are variables whose values are the runs' (`free`,
[wholeprog](wholeprog.md#init)): the other entries' analysis takes none of them, so their lift
holds for every run's state. Operand pairs init sets from one
word at constant offsets are one variable (`alias`), from the provenance of its run on the
jennings core (`postinit.Run`: each stored byte tagged as a constant, a byte loaded at an
address or through a word, or a byte of an addition chain on a word) when init returns on the
reference interpreter.

The bytes init leaves **constant** are the exception (`_fixed`): a byte init stores that holds
one value after init for every value of `A` it returns with on `machine` (all 256 are run, so
the values deciding it do not depend on the declared ones: `postinit.Init.same`) and that no
store of the other entries' lift may write (`Program.stored`, a store's address set, else its
address interval: a store of unknown address reaches every byte) is a byte of the image their
lift starts from, lifted again with it `held`. A pair of bytes an instruction reads as one
address is constant as a whole or not at all: a variable byte keeps the other a variable, the
pair the word init's runs bound (*Pointer pairs*). A byte whose value differs between values of
`A`, or one the other entries may write, stays a variable init sets. `info["fixed"]` lists them.

Their values are data: the other entries' functions read each as `K_<offset>`, the `.h`'s
`#define`, named by the offset its variable would have (`S_<offset>`, *State*), and the `.c`
holds no value computed from them. The lift loads a data operand of them from memory as it does
a cell's (wholeprog `held`), so an operand init relocated is `K_336 | ((uint16_t)K_337 << 8)`
(the compiler folds it), a pointer pair of them alike, a patched immediate or count `K_15`; the
analysis takes their values, so the access through such an address is direct where its value
lies in one region or span (`R2[(uint16_t)(K_... + y - R2_ORG)]`, `CGEN_IX(C0, K_... - C0_ORG,
y)`), and an index guard of the instruction reading them (its bounds the lift draws from them)
is `G_<offset>` (`if (G_1C < y) trap(k);`, the test's constants loads of `@G` no pass folds).
Which region or span such an access goes through, whether a store through it needs `codechk`,
and the code of an opcode or a branch's, jump's or call's target init leaves are decided by the
values: data that moves a folded address into another region, or changes which code runs,
changes the `.c` as code would. A byte is held only where an access other than a read by name
reaches it: one at an address it does not fold (an index over the instruction holding it), or a
translated init's store; a byte read only by name is in no array. A replayed init's `.h` holds
their values in its image (no copy writes them); a translated init's code writes them, its
storage starting from `image`, while the other entries' functions read the names.

Init is then lifted itself, from `image`, as an entry run once (`once`,
[wholeprog](wholeprog.md#init): a store into code makes cells only of the instructions that
may run after it, so a block copy over code init already ran leaves that code constant), for
any `A`. It is **translated** when that lift is exact for each declared value:

- lifted without throw-away reads, its run from `image` (`postinit.reproduces`) returns with
  no deopt and no volatile read but those of its polling loops (*Polling loops*), so it
  depends on nothing but `image` and `A`, makes the machine's volatile writes in order and
  leaves the machine's values in the bytes init stores and the image's in the others (outside
  the stack page and the volatile ranges);
- every access of the reference's run for each value (`postinit.Run.touched`, effective
  addresses per instruction) lies inside what the lift bounds it to: a pointer load's hulls
  (`reach`, `proven`), a bounded store's array (`bounded`), an address set (`addr_sets`), so no
  layout the lift's assumptions give traps on a declared value (`postinit.bounded`).

Its functions join the other entries' in one module, each reading its own lift's facts (value
sets, register sets, hulls, bounded stores, address sets: `printer.Module.fx`), init's entry
function, into which the compiler inlines its body, included: a relocator's stores through a
pointer from a table of patch sites write the sites' storage by their address set, not `wr`; its instruction
bytes decode from `image`, the bytes it patches are cells unless the other entries' lift reads
them as constants (which init then leaves), and the `.h` holds `image`. Init's entry function
traps an `A` outside `CGEN_SONG0` and the `CGEN_SONGS` after it, then runs the translated code;
a self-modified operand is a code cell (*State*), an `(zp),Y` block copy a pointer access
(*Memory*). A guard only the declared values keep from trapping (an index into a table of
the songs the argument selects, or one computed from it, past the table's extent) stays: its
`implied_by` is `"entry"` (*Trap metadata*), found by lifting init again for the declared
values alone (`checked`: the analysis takes them, no entry guard tests them) and taking the
guards that lift leaves out; that lift decides nothing of the `.c`. Init's code is lifted from
the image, so where the lift decides a branch from constant bytes init reads the `.c` follows
those bytes. A translated init's lift bounds each byte every store that may write it writes with
known addresses and values (`Program.values`, its image byte among them): the other entries'
lift takes those as the byte's values (`known`), so a cell only such stores of init write is
closed, its dispatch with no trap. What init's lift leaves at its return whatever the data (`Program.exit`: immediates
and device answers, no table) starts the other entries' driver head (`driven`,
[wholeprog](wholeprog.md#driver-loop)): a cursor init zeroes and play wraps is bounded at every
call, so a read it indexes needs no check and a branch it decides folds.

### Polling loops

A polling loop (`polling.loops`, over init's lift) is a strongly connected set of instructions
of constant bytes with no store, stack access, `SP` write or transfer but branches and jumps,
reading only constant addresses, one instruction of them the device: a wait such as
`L: LDA $D012; CMP #k; BNE L` or `L: BIT $DC0D; BPL L`. Each pass is the same function of
the byte the device answers. Entered with registers `r`, the loop settles (`polling.settle`,
each byte the device model answers in the run's device state, every byte for a register it
does not describe, run on the jennings core from `r`) when every pass it stays on keeps the
registers live into its entry (every register live where it is left), every byte it leaves on
leaves to one instruction and, where the model gives sets a read eventually hits (a counter's
values, a running timer's flag), one of them lies in the bytes it leaves on; the registers
differing between those bytes vary. Time since the device writes before a wait is unknown
after it.

The reference's run (`postinit.Run`, `polls`) answers each loop's device read with the least
byte it leaves on (`reads`), and is `exact` while every loop it enters settles, no varying
register is read before it is written (a register still varying where init returns is not
read) and it reads the device nowhere else. Init's lift is then run with those reads replayed
in order and must make exactly them: the wait leaves the state the machine's run leaves on
every device that ends it, so its exit values that stay live (`A = k` after `CMP #k`) are
constants of its code, and the C waits as the code does, the device read each pass by
`io_read`. Without `polls` a run that does not return on the reference interpreter has no
trace; with them one returning only by its waits must stay `exact`. A polling loop in another
entry is lifted as the loop it is, reading the device each pass.

### Devices

A device model (`devices.Model`; the C64's `c64devices.MODEL`: VIC-II, SID, colour RAM, CIA 1
and 2, 6510 port) describes registers by address, mirrors as one register. A read answers a
value set over the abstract device state (`devices.State`): the value each register's writes
hold and when. Kinds: a *latch* reads its bits `latch` as written, `ones` as 1, `live` any; a
*write-only* register its `live` bits any; a *counter* recurs at every value of its live
bits; a *status* register answers a set per state, may be acknowledged by a read (`ack`:
such reads are sensitive, never merged, dropped or duplicated) and says which sets a read
eventually hits (`recur`); a *derived* register's set is computed from the registers it
depends on (voice 3's output from its waveform, test bit, frequency and the cycles since its
release). An address the model does not describe answers any byte. The sets over-approximate
both SID models, PAL and NTSC, an access's cycle within its instruction and the VIC-II's DMA;
`tests/test_devices.py` checks every read of random programs on vagrant-moses' C64 lies in
them. The static lift takes the lowered model (`Model.lower`, [wholeprog](wholeprog.md),
*Devices*) with time unknown.

### Probes

A device read outside a polling loop of a register the model describes is a **probe**: the
reference's run (`postinit.forks`) is made once per value of each probe's set in the run's
device state, at most `postinit.FORKS` runs per argument value. Each must be reproduced by
init's lift with its reads replayed (`postinit.reproduces`), and one of each value's runs must
leave the machine's state. Translated, the C reads the device once (`io_read`) and takes the
path the value selects, which is sound for any value of the set: the other entries are lifted
from the states of every run (`Init.extended`), so a code byte the probe's paths patch
differently (an opcode, an operand) is a cell with each run's value, and none of those bytes
is held constant. A probe of voice 3's output after releasing its test bit (`LDA $D41B` read a
few cycles after writing `$D412`) selecting between two patchings is a two-way choice.

Otherwise init is **replayed**: an init whose run reads the device elsewhere (an undescribed
register, a probe with too many runs, which is so settled at the machine's value), keeps or
counts what it polls, deopts or leaves bounds the lift assumed. The init function then puts the state in place:

- an `A` outside `CGEN_SONG0` and the `CGEN_SONGS` after it traps;
- init's volatile writes are replayed in order (`CGEN_IO_*`, each distinct sequence once and
  `CGEN_IO_ROW` per value when there are several);
- the run's values are copied into the variables (`CGEN_CP_*`, per value `CP_FROM[s]` to
  `CP_FROM[s + 1]`): the `.h` holds the image, or the first run's state, whichever needs the
  smaller tables, and a run's values where they differ from it are copies of the bytes no run
  changes (the longest run of bytes its values continue as, `bytes.find` over doubling then
  halving lengths, when longer than a copy's six table bytes) or literal bytes (`CGEN_LIT`),
  cut where an array ends; bytes the other entries never read are not held.

A replayed init's `.c` is the same for every argument value, count of them and data init
reads; only the `.h` differs. The host calls init once, first, on the image.

## Structuring

Each function's blocks become a graph whose nodes hold statements. An edge carrying block
arguments gets a node of copies (the arguments are C variables assigned on the edge, a
parallel assignment); an edge to a block that only returns is a `return`.

A shared tail is a function only where the machine code makes it one: a `JSR` target (a
computed `JSR`'s arm included), or code that several functions reach at the entry height by
their own jumps, fall-through and instance calls' exits (no computed jump's arm), which a jump
or fall-through into it at the entry height reaches by a tail call; an instance call stands
for no instruction of its caller, its copy's instructions printed once in the instance
function. Code procedures enter only by jumps and fall-through is code of each
procedure reaching it, held once per procedure (its lift holds each instruction once per stack
height); a dispatch's handlers and the tails they share are arms of its function's switch.

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
   is `continue`, the latch's label (a `goto`'s, from a loop nested in the body) ends the body
   before the condition, and the latch's exit follows the loop.
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
8. *Shared code in place.* A `goto` to a node whose instruction two or more functions hold and
   whose code ends the procedure with no cycle and no other entry (no node of it but the first
   entered from outside it) is a copy of that code; one to a tail call continuing into code
   that does not reach it a copy of the call, the `goto` leaving it. Steps 5-7 run again until
   no `goto` goes to either: each copy has one predecessor, so a node is copied at most once
   per edge into it.

- *Branch conditions* are BDDs (CUDD through `dd`, variables declared in order of first use,
  sifted by CUDD's dynamic reordering as the diagram grows). A
  comparison of a value with a constant (after stripping zero extensions and constant
  additions, subtractions and exclusive-ors) and a `cf.switch` case are atoms `x == k` of a
  selector family whose atoms exclude each other.
- *Tests an enclosing test decides* are dropped, and each condition prints as an irredundant
  sum of products where its enclosing tests are don't-cares.
- *Switches*: a chain of three or more `if`/`else` tests of one selector family against sets of
  its constants is a `switch`; one whose arm would `break` a loop stays an `if` chain. Its arms
  (the default among them) print in the order of their targets in the machine code, each
  labelled by the constants its edge takes, so an arm running into the next falls through.
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
| `cells` | the code bytes the lift treats as variables (*State* classifies them) |

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

## State

The cells (the bytes of reached instructions the program stores: operands and opcodes it
patches) and the other variable bytes of the code span are classified by the address sets of
every access the layout places (`state.py`):

- A byte every access of which has one constant address is a **scalar**, `S_<offset>` (hex,
  from `CODE_ORG`; `S_I<offset>` from the loaded image's origin outside the code span).
- A **family** is the accesses reaching one address per copy: an instance function's access
  at a per-copy offset (the copy index's table), or a counted loop's at a constant plus a
  byte block argument whose values (enumerated to their fixpoint) step by a constant, every
  edge into its block passing a constant of them or itself plus a multiple of the step that
  cannot wrap. Families alike up to a translation, largest first, are the fields of one
  **struct**: its copies are the first family's addresses, a field the offset of a family
  less than the least distance between copies past the first copy's (a family reaching some
  copies of a field's bytes is that field). Each field is an array over the copies,
  `V<struct>_f<offset>[copy]` (structs by ascending first copy): an instance indexes it by its
  copy index (or a table of the copies it reaches), a loop by the element number the layout
  adds as a block argument beside the index (stepped as the index steps), a constant address
  by its copy. One array per field, not an array of structs: the 6502 indexes it with
  `abs,X`, where an element of a struct would be a multiplication, and separate arrays keep
  their accesses apart for the compiler's alias analysis.
- **Records.** A data byte outside the code span (not volatile, the stack page or a table) is
  a candidate too where an access reaches it at a stride of at least 2 for each value of its
  index: a counted loop's block argument (above), or a byte whose values the lift bounds to
  `n >= 2` addresses `k >= 2` apart (`Program.addr_sets`, loads and stores from a constant
  base plus an index register), the per-object state a program lays out at a fixed stride and
  addresses `abs,X`. Its families are a struct's fields as above (bases within one stride are
  fields of one record, `V0_f00`, `V0_f01`); such an access's element is `(w - base) / k`
  of the byte `w`. Any access that may reach a record byte otherwise (an interval, a pointer,
  an unknown address) keeps all of them bytes of a region.
- Any other byte stays a byte of its region (*Memory*), with the reason
  (`info["state"]`): an access touches it by an interval (`indexed`: an index, a copy's
  range, the run a bounded store writes), through a pointer whose address set holds it
  (`pointer`), at an unknown address (`unknown`), as a source of init's copies (`init`), or a
  family reaching it also reaches a byte that stays one or another struct's (`family`); a
  byte no other access reaches is `unaccessed`.
- A load through a pointer whose hull the lift bounds and no address set does (*Memory*) or
  an access of unknown address keeps no byte of the code span a byte: what it reads or writes there goes through
  `cgen_var`, the promoted byte at an address (a `switch` on its offset from `CODE_ORG`,
  after one range test), so its reader (`rdCj`, `rd`) reads the variable and `wr` writes it.
  Pointers the lift bounds to the song data reach no code byte and need none of it.

A promoted byte no load reads is dropped with its stores (`unread`), and a store into cells
left bytes no load may read is dead too (`Module.unread_stores`: only the constant bytes of the
code span count as read, a store there being a code check): a computed jump switching on its
index reads its operand cells no more, so their stores go and no array holds them. The `.c` defines the
variables (`uint8_t S_23 = S_23_INIT;`, `uint8_t V0_f0A[3] = {V0_f0A_INIT};`) and the `.h`
their initial values from the state it holds (init's copies write them by address, `&S_23`),
so the `.c` is the same for every song and placement. Promoted bytes are in no region unless
another access's interval spans them; `oob_rd` under `CGEN_OOB_EXACT` (`cgen_rd`) finds the
code span's promoted bytes through `cgen_var` first, so a read past the hull the lift assumed
reads the variable. Patched immediates are then plain variables: a compare or loop bound reads
one (`while (v < S_15)`), a patched opcode is a test of its state.

**Opcode states.** A scalar opcode cell whose values the lift bounds is an enumerated state:
each value is named by the cell's variable and the mnemonic of the instruction it makes there
(with its mode where the mnemonic repeats, and its value where both do), the enumerator's
value its byte, so the `.h`'s initial values and `cgen_var` hold as for any scalar
(`enum { S_27_RTS = 0x60, S_27_LDA = 0xA9 };`). A test computed by arithmetic from the cell's
load alone is the set of states it holds for (`S_4 == S_4_ADC`, `v3 != S_27_LDA`); a store
into the cell of a constant, or of a value computed from one such cell's load alone that
stays among the states, assigns the state, as a choice over the states loaded
(`S_27 = v3 == S_27_RTS ? S_27_LDA : S_27_RTS;`). Each state's instruction is lifted with its
own control (an `RTS` state returns). `info["state"]["opcodes"]` lists each opcode cell
whose values the lift bounds: `{address: {"values", "name", "enum"}}`, its values ascending,
its variable (None when not promoted) and its enumerators by value (empty when it has none).

`info["state"]` gives per tune `{"cells": counts, "span": counts, "vars": at}`, each
count `{"scalars", "fields", "unread", "bytes": {reason: n}}`, over the cells and the other
bytes of the span, and `at` each promoted byte's lvalue and original address (`{"S_23": 0x1023,
"V0_f0A[1]": 0x1129}`), by which a tool maps the variables back to the original's memory.

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
- **Data regions** `R0, R1, ...` (ascending address, those only init's stores, block copies
  or the sources of init's copies touch after the others, so the `.c` names the same regions
  whatever songs init runs for) are the maximal runs of other bytes that some access may touch, outside volatile
  ranges. An access's interval comes from its address
  expression (constants, an 8-bit index adds `[0, 255]`, masks, shifts, the hull of what flows
  into a block argument or a function's parameter from its call sites, a loop-carried value
  enumerated to its fixpoint or keeping its initial interval, a load of a closed cell the
  hull of its values (`Program.sets`) and of a byte with bounded stores its bound
  (`Program.bounds`: its entry value, for a code cell init leaves its values over init's runs,
  with the stored values), a mask or an or-ed constant that keeps the interval in one block);
  wider than `$200` is unknown. A lifted function's parameter takes the hull its call sites
  pass met with the values the lift finds its register holding at its entry on every run
  (`Program.regs`, every copy's entry), a call's result the hull over the callee's returns of
  those at each `RTS`, and a byte an `LDA`/`LDX`/`LDY`/`LAX` loads those its register holds
  after it: a voice index a routine saves in an operand and reloads keeps its values. The
  intervals found (every function's and result's) are dropped whenever an operation is
  rewritten or a fact they read is set, so each is the module's as it is when asked. Those an
  instance's copy index or a block's branches imply are kept per context (copy, block); what
  is kept across contexts (another function's intervals, a call's result, the constant a load
  reads) is found in no context, whichever context asks for it. A load
  the lift proves inside a hull (`Program.proven`) touches that hull alone. An access into
  the song's data block (`Program.data`, [wholeprog](wholeprog.md#static-proofs), *Machine
  model*) touches the block's runs alone, each a region whatever the lift read of it, and
  reaches them through the region holding its address; where they are several, through the
  block's one array over its hull (`Bb[a - Bb_ORG]`: the regions and held spans in it are
  its slices; its other bytes are padding, zero, `info["blocks"][b]["padding"]`, which no
  valid run reads), after the memory map's check against the runs (`cgen_outj(p, k)`:
  whether `p` lies outside every run of the `.h`'s `CGEN_OBJj_LO` and `CGEN_OBJj_LIM` with
  `k` bytes past it, the slack of a check at a pointer's definition). A load the check
  guards makes it itself (`Bbcj(base, y, k)`), leaving by the check's trap `k`, whose
  metadata stays the check's. `sensitive_reads` also goes to the lift, which makes no
  throw-away read that cannot reach one of them.
  An unknown access may touch every byte of `loaded`, so then every non-code byte of it is in a
  region. Zero page, the stack page and RAM below the image are regions when accessed; a byte
  past the image is one only when a bounded store or a store with an address set writes it (the bytes between an instance's copies' bytes stay in
  their array): a read of any other byte past the image is out of bounds, but one at a constant
  address reads the machine's byte (below). A load whose bytes
  in the image lie in one region or held span (all its bytes when none is), every one held,
  tests its address against it and reads `oob_rd` outside (`(uint16_t)(a - R2_ORG) < R2_LEN ?
  ... : oob_rd(a)`); from a constant base plus a byte inside one held span, through the span's
  bounds-checked reader `rdCj(base, index)`. One the lift proves (`Program.proven`: the bounds
  of the bytes its index comes from, or the program the memory map holds it to,
  [wholeprog](wholeprog.md#static-proofs), *Machine model*) inside one region or held span
  indexes it unchecked.
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
  spans several regions (operand cells of several copies) goes through `rd`/`wr`. A run of
  a set as wide as an access of unknown address touches its bytes alone.
- An access whose 16-bit address is computed from one byte index (a loop's progression, the
  instance index, or the values the lift finds `X` holding at a `(zp,X)` instruction, every
  copy's) through loads of bytes holding one value whenever its function runs (a pointer pair
  init leaves, `Module.known`) is that address for each value of the index; where it is a
  constant plus the index for every value, the access is rewritten to address that constant
  plus the index (`Func.fold_known_addresses`): it indexes what the pointers address (the
  records `($40,X)` reaches, `V0_f00[e]`, or `R4[x]`), no 16-bit address subscript, and the
  pairs, read no more, are neither held nor stored.
- A store the lift bounds to the array holding its base (`Program.bounded`: an index any byte,
  the store reaching code past the array) writes the region holding its base inside it and
  calls `oob_wr` past it: `if ((uint16_t)(a - Rk_ORG) < Rk_LEN) Rk[...] = v; else oob_wr(a,
  v);`, the write-side counterpart of `rdCj`.
- An access's address interval is met with what the branches on every path to it imply
  (`printer.Func.context`): each block of its dominator chain entered by one edge, a
  `cond_br` on an unsigned or equality compare of a value (offset by a constant, extended)
  with a constant, its negation, an `||` false or an `&&` true, or a `switch` case, bounds
  that value and the pure values computed from it there. A store past an index guard and the
  loads of its index the guard dominates lie in the guard's range, so inside one region they
  index it with no run test and no `oob_*` arm; an access the guard does not dominate, or at
  an index whose range leaves the region, keeps its test.
- A load of unknown address through a pointer whose hull the lift bounds (`Program.reach`,
  [wholeprog](wholeprog.md#static-proofs), *Pointer reach*: bases from tables read inside their
  extents, constants, a relocation constant added with carry, an offset in `Y`, a pointer
  advanced from them by the lengths it reads, a table byte tested before its pointer uses it, a
  pointer read through one or from song data, a return stack's pop of what its push stored, a
  pointer init leaves into the song data it unpacked (an idle voice's null taken not to be
  read through), song data offsets plus the origin a relocating init leaves)
  reads one span: from the first non-code byte of the hull inside `loaded` (and the bytes past it,
  below the volatile ranges, a block copy writes, `Program.written`) to its last, every base
  inside `loaded` in it, held as one array `Cj` (code bytes and the bytes between the program
  and those inside it held too) and printed
  `rdCj(base, y)`, the address as a 16-bit base plus a byte (`(zp),Y`'s pointer and index, a
  constant offset below 256, else 0): `noinline`, it reads `Cj` where the address lies in it,
  else `oob_rd`, so a pointer below the program (zero page, a pointer still 0) or walked past
  it reads what the 6502 would. Where the hull the
  lift proves on every run (`Program.proven`: no table taken to be read inside its extent,
  a pointer object's check keeping its pointer in bounds, or the pointer and cursor states
  the entries reach from init's runs) lies inside `loaded` and the
  bytes past it a block copy writes, the span is that hull and the
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
  to its region. Each copy's own cells are fields (*State*). An access to other bytes is
  placed per copy: its interval for each value of `k` lying in one region (or held span)
  indexes it directly; the interval over all copies is then held in one span with the
  regions it touches. Per-copy data whose offsets do not fit 8 bits (copies further apart
  than that over all copies) is reached through per-copy pointers: the accesses at one table's offsets from constants
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
  symbols, so data pointers holding original addresses resolve), then the unheld write below; each finds the
  array by tables of their origins and lengths (`OOB_*` over the regions outside the spans and
  the spans for `rd` (`cgen_rd`), `WR_*` over every region for `wr`). They and
  `codechk` are `noinline`, as llvm-mos would otherwise copy them into every caller.
- The arrays' definitions are the `.h`'s, as where a song puts its data decides them: the
  `.c` defines them by `CGEN_STORAGE` (the held spans, the regions not inside one, a span's
  data bits); a region inside a held span is a `#define` of its slice there; `cgen_rd` and
  `wr` loop over the `CGEN_ARRAYS` and `CGEN_REGIONS` lists (`wr` writes a span's data bytes
  through its slices). An access's offset into a held span, where some byte from the span's
  origin to its constant is no instruction's (a table of song data, or code past it), is a name
  the `.h` defines by the first instruction reading it (`C1_AT1A3` for the one at
  `CODE_ORG + 0x1A3`, `_1`, `_2` for its further constants). The constant is taken from every
  addition printed in the address, as an instance's table base plus its copy's offset and an
  index: `C1[C1_AT134 + v7 + v50]`, as is the offset a proven pointer read indexes (`CGEN_IX`).
  Tunes of one player that differ in their song data and its placement give the same `.c`.
- A load of a constant byte (constant code or a table the lift read as constant) at a fixed
  address prints as a name the `.h` defines, relative to the origin that places the byte
  (`CODE_KA3` for `CODE_ORG + 0xA3`; `K_A3` for a byte init leaves constant, *Init*): the `.c`
  holds neither its value nor its address, and the compiler folds it. It is no access: no array
  holds the byte for it. So is a load of a byte outside `loaded`, the volatile ranges and the
  stack page that no store may write (no address set or interval of one holds it, init stores
  none there, and no store's address is unknown): the machine's byte at entry, `image`'s
  (power-on RAM), named by its address (`MEM_K31F0`), as a different machine model changes
  the `.h` alone.
- `oob_rd`, a read outside every array its site may address, calls `trap(0xFFFF)`
  (`OOB_TRAP`). With `CGEN_OOB_EXACT` defined it is `cgen_rd`, the byte the 6502 reads there:
  a region's or span's live byte, an I/O register, and where no array holds the byte either a
  trap (`OOB_TRAP`: a byte of `loaded` or one init stores, data of the program the lift holds
  none of for the read, the `.h`'s `CGEN_PROG_ORG`/`_LEN` runs) or the machine's entry byte
  (`image`'s: the machine model defines it, so reading it is valid input), from the `.h`'s
  background table: runs (`CGEN_BG_ORG`, the last at or below the address) each reading
  `CGEN_BG_INIT[(CGEN_BG_AT + (a & CGEN_BG_M)) & 0xFFFF]`, a unit repeating every power of two
  bytes or a literal, the period the smallest table's (power-on RAM: one 8-byte unit and its
  inverse, a run per 16 KiB). `rd` is `cgen_rd` too. Pushes are stored where a load may reach
  the stack page (*Traps and computed jumps*), so its other bytes read their entry bytes.
- `oob_wr`, a write outside every array its site may address, is exact under the read rule:
  `wr`'s dispatch (a promoted cell first), then, where no array holds the byte
  (`cgen_unheld_wr`), the bytes stores may write are known statically: each store's address
  set, else its interval (each copy's in an instance) met with the run the lift bounds it to
  (`printer.Module.stored`). Exactly those of them no array holds are held, from the
  machine's entry bytes (`CGEN_RAM_ORG`/`_LEN`/`_AT` runs into `CGEN_RAM_INIT`, `CGEN_RAM_N`
  bytes; `cgen_ram`), which the write sets and `cgen_rd` reads before the background table.
  Any other byte, a constant byte of a held span or of the program (`CGEN_PROG_*`: the read
  traps, so its write does) included, keeps its value or traps (`OOB_TRAP`, invalid input).
  A store of unknown address may write every byte, so then every byte outside the arrays and
  the program is held: that store is residue to bound in the lift, not a cache's to absorb.
  Defining `CGEN_OOB` replaces `oob_rd` and `oob_wr`
  with the host's (`wr` then ends in its `oob_wr`). `trap` is declared `noreturn`
  and `leaf`: a host compiles it apart from the `.c`, and it calls back into nothing there.
  A store the lift found may hit constant code or a table it read is followed by `codechk`,
  which traps when a constant byte of a span would change.
- A pointer object's check ([wholeprog](wholeprog.md#static-proofs), *Pointer objects*:
  after a store defining a `(zp),Y` pair, before a load an entry reaches first) is the test
  `(uint16_t)(p - CGEN_OBJj_ORG) > CGEN_OBJj_LIM` of the pair's pointer `p` before its trap:
  the `.h` names object `j`'s first byte and the last less it, as the data decide them, so
  the `.c` holds no pointer init leaves and no bound. The entries' lift takes the bytes past
  the program init's lift writes as held (`holds`), as the spans hold them. A walk's check
  (*Walks*, after the instruction setting `Y`) tests `p + y` alike; a load through
  a pair init leaves that the entries never store (*Seeds*) reads its words with no test.
- An access whose bases lie in the program (an operand, a pointer's or operand cells'
  provenance; [wholeprog](wholeprog.md#static-proofs), *Machine model*) reaches the array
  holding it unchecked, after `objchk` of its address where its interval may leave the
  program (`def` `"machine"`, always for an address the program's bytes form: the check's
  bounds are the `.h`'s, so the `.c` is the same whatever the song data holds); the lift's
  proven hull decides it (`Program.proven`), cgen reads it. The C64's memory map is fixed: outside the program the access would take I/O,
  ROM or RAM the program never loaded for its own, and play after init is a closed program
  that works, so a valid run stays inside and the trap stops invalid input. Bases unknown,
  or reaching I/O (a device read through a pointer) or unloaded RAM, keep the bounds-checked
  reader.

Address constants print relative to the region holding them, or to the code span or loaded image when the program holds them (any other, a device's or a vector's, prints as it is)
(`R2_ORG + 0x10`, `CODE_ORG + 0x21`, `IMG_ORG + ...`): a constant reaching an access address
through additions, casts, selects and block arguments, the cases of a computed-jump switch, and a
pair of constant byte stores into a pointer pair (the zero-page pointer of a `(zp),Y`/`(zp,X)`,
a `JMP (ind)` vector, a three-byte operand, or two stack bytes at one SP value and that
value less one, as a `JSR`'s return address when the stack is kept in memory) whose 16-bit value lies in the program: `LDA #<tab` then
prints `(uint8_t)(R3_ORG + 4)`. A constant or-ed into a computed jump's target (the high byte of a
`JMP` whose low operand byte is a cell) prints so too: `S_2F9 | CODE_ORG`. Not covered: an
address half stored without its partner in the same block, or used other than as an address
(compared, added to data).

An access to an array never holds a code or image address: its constant is an offset
(*Memory*). `CODE_ORG` and `IMG_ORG` stay in the `.c` only where the value is a 6502 address:
the cases of a switch over code addresses (a computed jump the switch over its table index
does not cover, *Traps and computed jumps*) and the relocated high byte of a stored code pointer it switches on (`S_2F9 |
CODE_ORG`); a pointer the program stores (the pair stores above), whose bytes hold the
address its reads take apart; the address argument of `rd`, `wr`, `oob_*`, `rdCj`, `io_*` and
`codechk`, which find the byte by it; and `cgen_var`'s switch over the promoted bytes'
addresses.

## Traps and computed jumps

The lift resolves a `JMP (ind)` whose pointer is stored from loads of a constant table (or
from constants, or is a constant word of the code span no store writes), a `JMP`/`JSR` whose
operand bytes a store writes so (in the same call, or earlier: then from the values every
store may leave in them), an `RTS`/`RTI` that returns through an address pushed so, a `BRK`
through the vector the program stores, and an `RTS` returning past a call's inline arguments,
from the code alone ([wholeprog](wholeprog.md#static-proofs), *Computed jumps*; one through a
vector no store writes is a call of the handler it holds, with no trap): the targets are
the table's words over the index values inside the table's extent, which no store may change.
An index the code does not bound, such as a command byte of song data, reads the table up to
where the layout ends it (*Table extents*: a split table's low column at its high column, any
column at the next object the code accesses, the first of the jump's targets past its first
byte, or the end of `loaded`), and an index guard
traps before a load past it. A store (or read-modify-write) to a constant base plus such an
index, whose table starts inside `loaded`, is guarded alike where some column reaches past
`loaded` into bytes no store with an address set writes: it traps before a store there, so its
interval, and its region, ends with `loaded`. A load past `loaded` reads out of bounds
(`oob_rd`) and a store into code past its array writes out of bounds, as below. A transfer whose target bytes are each a constant or a column of one
table indexed by one value (`LDA lo,X; STA op+1; LDA hi,X; STA op+2; JMP op`: a `JMP (ind)`
pointer, an address pushed for `RTS`/`RTI`, a `JMP`/`JSR` operand or a branch offset written
so, the index itself possibly a byte read from data) is a switch over that value (`case 3:`,
the values selecting one target sharing its arm), the same for every origin: the register
holding it; where the register is written before the transfer, the byte of data it was read
from (read again, as no store may have written it), else a variable holding its value from
before that write, copied before each write (writes on paths that join, or of contexts the
transfer is reached in, share one variable, as their values do: `y = x` on one path, `y = b`
on the other, `switch (y)`): a local of the transfer's function where every path from its
entry to the transfer passes a write and none of its callees runs one (in an instance function
one write per merged instruction); else a parameter of the functions on the path from the
write's function (`f1(a, y)`) where the transfer's function is on no call cycle and calls no
function running a write, and no function running one is on a call cycle or an instance
function (at most eight such variables; else the switch is over addresses). Any other, as one whose index paths hold in unlike ways or a target computed by
arithmetic, is a switch over code addresses (`case CODE_ORG + 0x23:`); either has its last
target as the default, with no trap. A `JSR` through cells calls one function whose body is
the switch over its targets (the handlers its arms): over the index when every `JSR` it serves
selects by one, else over the operand it is handed, whose high byte relocates in it
(`switch (v2 | CODE_ORG)`). `info["jumps"]` lists them (`{pc: [target, ...]}`). Programs with the same
code and tables give the same `.c`; a table a store with known addresses may write leaves the
transfer unresolved. An unresolved one becomes a switch whose default traps; `facts` supplies
its targets: `jump_facts` keeps only the edges leaving those instructions, and the `.c` then
depends on the run that made the facts, which `info["facts_edges"]` reports.

Code cells come from the lift too: every byte of a reached instruction that a store's address
set holds (an indexed store's base plus each value of its bounded index, as a player clearing
its own operands with `STA abs,X` in a counted loop) is data, so its stores and the
instruction's reads go to its region with no `codechk`. An opcode cell every store writes
with known values (constants, table bytes, or its own byte saved with `PHA` and restored with
`PLA`, or a byte the straight chain of instructions ending at the store computes from one
cell's byte alone, as `LDA gate; EOR #$C9; STA gate` does: its values are the cell's values
mapped through the chain, `dispatch::cell_maps`, closed to their least fixpoint) dispatches
over them with no trap. A store whose mapped values would make an instruction that runs on
past the cell end at another byte than the cell's other values (`LDA #imm` and `LDA abs`)
leaves the cell's values unknown. Other traps: a deopt the lift keeps (an index past
its table's extent or a store's past `loaded`, an opcode cell some store writes with unknown values holding a value
outside its set, a store whose address is unknown into constant code or a table that
changes it, a store that may hit the stack, a guard it could not prove).

The stack is the calling convention's: every entry starts at `SP = $FD`, so the stack bytes
the program can hold are known (*Stack extent*). A pointer access is not checked against
page 1 by its pointer's value: loads read the stack exactly (pushes and return addresses are
also stored where a load may reach page 1), and a store that may hit the stack bytes traps
before it does.

Flags saved through the stack (`PHP`/`PLP`, a `BRK` frame and its handler's `RTI`) are the
flags themselves: the status byte (`0x30 | c | z << 1 | ...`) is packed only where it is read
as data (`PLA`, a load from page 1 such as a handler's `TSX; LDA $0101,X`), with only the flags
that read keeps (`PHP; PLA; AND #$08` packs `D`), and a byte pulled into the status
(`PHA; PLP`) is unpacked only into the flags read after it ([wholeprog](wholeprog.md#pipeline),
*status lanes*).

### Trap metadata

`info["trap_info"]` holds, per trap `k` of `info["traps"]`, what it stands for: the guards of
the lift's deopts (and the defaults of the switches) the trap is reached by, as the lift
emitted them (`crate::why`: the emitter tags each deopt and `BRANCHIND` with the guards it
leaves by, and the tags survive optimization; one trap may stand for several guards, as a
store's code check and the pointer checks of its instruction share one exit):

| key | value |
|---|---|
| `kind` | the kind of its guards, `"mixed"` when they differ (`kinds`: each) |
| `pc` | where it leaves for the reference (`info["traps"][k][1]`) |
| `fn`, `copies` | the function a trap keyed by one (`("deopt", pc, fn)`, `("ind", ...)`) is in; for an instance function each copy's `pc` |
| `guards` | one dict per guard (below) |
| `implied_by` | the union of the guards' `implied_by` when every guard is implied, else `None` |

A guard has `kind`, `at` (the instruction it guards; in an instance function the start its
node is keyed by, `copies` each copy's), `fn`, `reg` (`"a"`, `"x"`, `"y"`, `"sp"`, a flag) and
`ok`, the values it lets pass as inclusive runs `[(lo, hi)]` (of the register, the opcode
cell, the pointer or the address), and as the kind has them:

| `kind` | guards |
|---|---|
| `entry` | an entry register outside its declared values; `init`'s `A` outside `songs` |
| `entry_sp`, `port` | an entry's SP or bank port registers other than the ones it is lifted for |
| `index` | an index past its table's extent: `tables`, each `{of, base, end, by, store}`, `of` `"jump"` (a transfer's target words), `"branch"` (branch offsets), `"cell"` (values a store at `store` writes into a code cell), `"data"` (a pointer's seed words or other values) or `"store"` (a store's run inside `loaded`), the column at `base` ending at `end` (exclusive) at `by` `"object"` (the next object the code accesses), `"code"` (an instruction byte), `"loaded"` (the end of `loaded`), `"target"` (a target of the transfer), `"column"` (a byte another column reads) or `"wrap"` |
| `opcode` | an opcode cell holding a value outside its variants (`ok`) |
| `codechk` | a store (`addr` its interval) changing constant code |
| `window`, `objchk` | a pointer pair's (`pair`) word in a zone's window; outside object `obj`, checked at an entry, a defining load, after a defining store or after a walk, or before a load the memory map bounds (`def`: `entry`, `load`, `store`, `walk`, `machine`) |
| `stack`, `zone` | an access (`addr`) reaching page 1 outside the bytes it may, or a pointer store reaching a zone |
| `return`, `txs`, `decimal`, `sp`, `bank` | a callee returning elsewhere than its call pushed; a `TXS` off the saved height; a pulled status setting `D`; SP or the bank configuration at no planned node |
| `transfer`, `ind` | a computed transfer to an address its table lacks; an unresolved transfer's switch default (`targets` the table's, `resolved` whether the code resolves it) |
| `path`, `jam` | a successor the plan has no node for; a `JAM` |

`seen` is the runs of values the analysis finds the register holds at the guard (what the
lift proves). For `entry` and `index` guards on `A`, `X` or `Y`, `held` is what holds there on
every path through the function as emitted: `vals` (runs) left by the register guards and
constants before it (copies keep a register's fact; other writes and calls lose it), `by` the
guards that narrowed it and `src` the entry register it still holds; `implied_by` is then the
traps whose guards already keep the register inside `ok` (`[]`: constants alone), with
`"entry"` last where the entry's declared values do (holding init's `A` inside `songs`, or a
guard of init's lift its lift for the declared values leaves out, *Init*: an argument outside
them is invalid input the entry's trap stops), else `None`. A guard the register guards before it imply on every
path is not emitted (wholeprog *Implied guards*), nor an index guard whose register holds only
values inside `ok` on every run (`proven`), so neither has a trap. `opcode`, `codechk` and `cell` guards list their
`writers`, `{pc, entry, exact}`: the stores that may write the byte, from the entries' lift
(`"play"`) or init's (`"init"`), `exact` for a store with one address or an address set.

## Translation cost

Every printed statement and check carries a marker of what it comes from (`marks.py`); what
survives printing (folded functions, unread statements dropped) is read back in text order,
so `info["emission"]` counts what the `.c` holds (`cost.py`):

| key | value |
|---|---|
| `copies` | `{pc: n}`: the times each original instruction is printed, `n` at least 1 (below) |
| `checks` | one per printed check, in text order: `kind`, `at` (the access or guard's instruction), `defs` (the ids of the pointer definitions it checks) and `trap` (the `k` its failure takes, else `None`) |
| `traps` | `{k: n}`: the printed `trap(k)` calls |
| `defs` | the pointer definitions by id: `[loc, at, byte, by, lift]` ([wholeprog](wholeprog.md#static-proofs), *Pointer definitions*; `lift` 0 the entries', 1 init's) |
| `unproven` | `{entry: [id]}`: the definitions of the checks the entry's code reaches |

An operation is printed once per visit of the blocks printing it: a block the structuring
prints in two places (a shared tail, a tail call printed in place) prints its operations
twice, a value printed at several uses of one visit once. An instruction's operations that one
lift node holds and the inliner places through one chain of call sites (its MLIR location,
`loc("pc"("function:block"))` and the `callsite` locations inlining adds) are one emission;
`copies[pc]` sums, over its lift nodes and call chains, the most visits printing one of them.
An instance function's operation is printed for each copy's instruction.

The checks are the `objchk` tests (`cgen_outj` too, a checked read `Bbcj` one check), `codechk`, the bounds-checked readers'
calls (`rdCj`), the tests of a bounded access before its `oob_*` arm, `rd`/`wr` and `oob_*`
calls, and the `index`, `window`, `zone` and `stack` guards a deopt tests with no call (one per
printed `trap(k)`). A check's definitions: an `objchk` guard's after its defining store or
walk anchor, else those reaching the guarded instruction (of its pair where it names one); an
`index` guard's of its register; any other check's those its access's instruction reads.

`cost.sites(emission)` lists the cost sites: `E1` for each printed copy of an instruction past
its first, `E2` for each check (in text order) all of whose definitions an earlier check
already checks (`cost.redundant`: their indices), and for each printed `trap(k)` past the checks leading to it. A definition
needs one check (one per definition is the least the code can need, the minimum
`unproven` counts), covering every access through it, `p + y` with `y` a byte.

With `lines`, each line holding code is preceded by `#line N "file"` naming the marker
standing before it, and a marker inside a line breaks it there (`# line N "file"`):

| file | line | code |
|---|---|---|
| `6502` | the instruction's address | an original instruction's statements and conditions |
| `check` | the check's index in `checks` plus 1 | a check and its failure arm |
| `trap` | `k` plus 1 | `trap(k)` |
| `access` | 1 | the access helpers (`rd`, `wr`, `oob_*`, `rdCj`, `rdBj`, `cgen_outj`, `codechk`, `cgen_var`) |
| `copy` | 1 | block arguments' copies, results left in `rv*`, structuring selectors |
| `frame` | 1 | function heads and closing braces |
| `runtime` | 1 | the prologue, init's replay and whatever follows the `.c` (the host's) |

`marks.strip_lines` removes the directives, leaving the `.c` translated without `lines`
byte for byte. `tools/csize.py`'s `owners` gives the bytes of each `(file, line)` of a build
with `-g` from its DWARF line table.

## Validation

`tests/test_cgen.py` compiles each output with gcc (`-std=c99 -Wall -Wextra -Werror`) against
`tools/cgenplay.c` (each build a library of its own path, as the loader hands back one already
loaded from a path) and compares the volatile writes (and sensitive reads) of many consecutive
calls, memory persisting, with `wholeprog.reference_call`; it checks relocation and data
invariance, the gotos (a non-series-parallel join's edges, a two-entry loop's second entry, a
computed branch entering one run at three points), pointer reads from split tables an
unbounded byte indexes with a growing `Y` read through one bounds-checked span, a pointer pair
reused as scratch between its uses keeping `rd` only where its scratch values reach, and their
provenance (instruction starts of
the lift), short-circuit conditions, no structuring variables, the structuring (a shared tail kept in each procedure,
multi-exit and two-entry loops, switches from compare chains and jump tables, kept callees' parameters, wrapping
addresses), computed jumps and calls through operands written from tables (in the same call
or an earlier one), computed transfers switching on their index (carried past its register's
write, pushed, a branch offset, read from data) and on the address where no one index
selects, an index past a split table's extent trapping, a split table at the end of the program stored
at an unbounded song byte held in arrays ending with the program and trapping past it, a cell reload forwarded from the register stored to it past such a guarded store, that store and a load of its index past it untested (one around the guard or
past the table tested), an opcode restored through
the stack, a pointer into zero page with no stack check, a jump's or-ed high byte under
relocation, determinism (repeated, with the blocks shuffled and under other hash seeds), play
lifted from the state init leaves and init translated (a table-driven block copy with a
self-modified length overwriting a routine init ran, with no copy tables; an init lifted run
once against any number of times; an init reading the device replayed; a relocator's operands
pointer variables bounded to the data they point into
and one variable per source word; init relocating by `A`, reading data, making I/O writes the
data decides or copying a block per value of `A`, replayed with the same `.c` for every value,
count of values and data; a translated init storing at its song, the same `.c` for one, three or
32 declared songs and its store guard implied by the entry; a probe patching an opcode settled at the machine's value), each
value from a fresh image, and an llvm-mos build. `tools/cgenplay.py` does the
same for a PSID (its init called once, first: `--init`) or raw image (`--loaded LO-HI`
names the program's bytes in a raw 64 KiB state, as a memory dump after an init routine) and
prints `tools/cmetrics.py`'s table, the sizes, the computed jumps resolved from the code, the
traps and the stage times. It also checks the lift's tail calls (a routine fallen into for its
third voice is one function with three call sites) and instances (unrolled copies at a stride
are one function of the copy's index whose I/O register is `0xC000 + Kn[k]`, `Kn` the
offsets `{0, 7, 14}`, called for each copy in order; copies sharing one trap; determinism and
relocation invariance of a folded program; copies' operand cells as one field array indexed
by the copy, at uneven distances too; a patched operand as a scalar, a patched compare
immediate as a loop bound, cells a counted loop indexes as a field it steps an element number
through; per-copy immediates and
branches on them as constant tables; statements alike only in a prefix of their blocks not
folded; copies at a stride wider than 8 bits reach holding their cells as fields and data
indexed by a byte from per-copy pointer tables into a region; a
compare only some copies have one compare with the index or a table; copies entered at a
branch after loads they differ in one class entered at the branch), a voice loop's index bounding the throw-away read of the indexed I/O store in the
routine it calls through a `BRK` and through a stack switch, and return addresses as
continuations (inline arguments of one and two bytes read through the pulled return address
from several sites, each a constant code byte with no code address a value; one advanced by
an amount the code does not decide trapping; one a store writes a variable; `PHA`/`PHA`/`RTS`
to a constant; a `BRK` handler reading its signature byte through the pushed address; a
`BRK` through a constant vector; an `RTI` through a table frame choosing on its index; stores
no load reads dropped).

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
