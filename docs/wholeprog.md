# Whole-program lifting

`deity_informant.wholeprog` lifts the entry points of a 6502/6510 program to optimized,
executable P-Code: code bytes the program writes become memory reads, provably balanced
`JSR`/`RTS` pairs become `CALL`/`RETURN` with no return address in memory, pushes and pulls
become stack-slot registers, cycles are optionally counted by ordinary ops, and every
assumption is guarded by an exit (deopt) to the reference interpreter at an instruction
boundary. Results are exact against `di_pcode::Cpu`: the volatile access stream (address,
value, and with cycle counting the cycle), final registers (and cycles), and RAM except dead
stack bytes below SP. A read-modify-write writes its operand back unchanged before the
result, as the NMOS bus does, so a device sees both writes, and the 6502's throw-away reads
are bus reads (below). Nothing in it knows a host.

**Throw-away reads.** As libsidplayfp's `MOS6510` and the vagrant-moses machine make them:
the byte after a one-byte opcode (implied, accumulator, push, pull, `RTS`, `RTI`, `BRK`'s
signature byte), the unfixed address of an `abs,X`/`abs,Y`/`(zp),Y` access (the base's page
with the indexed low byte: on a page cross for reads, always for stores and read-modify-writes),
a taken branch's next byte and, on a page cross, its unfixed target, and the address an `RTS`
pulled. Those of pages `$00`-`$01` (`zp,X`, `(zp,X)`, stack cycles) are not made. Indexed ones
are template ops (a `LOAD` with no output, predicated by its second input when present); the
others belong to the instruction's control. The emitter keeps only those whose address
interval may meet a volatile range in the node's configuration, so elsewhere they cost
nothing; optimization drops those whose address or predicate folds away, and an unfixed
address is the access's own when the index cannot cross a page. An entry's exit `RTS` reads
the host's return address less one (`Program.call` pushes `$0001`); it is not made when
nothing but pushes reaches page 1 and none lands above the entry.

The Rust core is `crates/di-lift`; `deity_informant._native` exposes it.

## Python

```python
from deity_informant import trace, wholeprog

facts = trace.collect(state, lambda s: [s.call(play) for _ in range(3000)])
prog = wholeprog.lift(state, [play], volatile=[(lo, hi)], facts=facts)   # cycles=False
prog = wholeprog.lift(image, [init, play], volatile, entry_sp=0xFD, loaded=(lo, hi))  # static
prog.functions        # [{"pc", "entry", "recursive", "blocks": [{"pc", "ops"}], "tables", "closed",
                      #   "copies", "copy_pcs"}]: copy_pcs per merged instruction each copy's pc
                      # an op: [name, out, ins, aux, pc], pc the instruction it was emitted for
prog.stats            # insns, raw_ops, emitted_ops, ops, cells, code_guards, calls, ...
prog.chains           # [{"function", "pc", "kind", "fused", "x", "y", "r"}] carry chains
prog.induction        # [{"head", "reg", "addr", "lo", "hi", "step", "trips"}] loop counters
prog.jumps            # {pc: [target, ...]} computed transfers resolved from the code
prog.sets             # {address: [value, ...]} closed cells (opcodes and operands) and their values
prog.code             # [addr, ...] bytes treated as constant: code, tables read for jumps and cells
prog.guard_evals      # conditional branches to a deopt the interpreter backend has run
r = prog.call(mem, 0, a=0, reg=None, cycles=0, max_cycles=10_000_000, log=True)  # backend=
prog.backend          # "jit" (default) or "interp"; prog.compile() -> compile seconds
r["reg"], r["pc"], r["cycles"], r["halt"], r["deopt"], r["log"]
prog.deopts           # {pc: count}
prog.matches(mem)     # constant bytes and closed cells still hold lifted values
progs = wholeprog.lift_many([(state, [play], [(lo, hi)], facts), ...])   # rayon, progress bar
wholeprog.reference_call(mem, play, volatile=[(lo, hi)], log=True, clocked=False)
```

Inputs:

| Input | Meaning |
|---|---|
| `image` | 64 KiB memory every entry starts from (e.g. after an init routine ran). |
| `entries` | `pc` or `(pc, {"a": [values], ...})`; a single value is assumed (guarded) at entry. |
| `volatile` | `[(lo, hi)]` address ranges whose accesses are kept verbatim, in order, at their cycle. |
| `facts` | `trace.Facts` (or its dict): executed `starts`, `(pc, next)` `edges`, stored values `writes` per address, `page1` sites with the `[lo, hi]` page-1 addresses each reached, stack `heights` per start, `entry_sp` values per entry, with a bank map the configurations (`banks`) each start ran in and the port registers (`entry_port`) each entry was called with. The effective-address evidence `ea` ([layout](layout.md#effective-address-evidence)) is ignored. |
| `bank` | A bank map memory is seen through (default flat): the port's direction and data cells, `mask` and `pull` bits, `lut` (configuration per effective port value, undriven bits high) and per configuration each 256-byte page as RAM, a constant ROM image or I/O. `deity_informant.c64.bank_map()` is the C64's (ROM-less ROMs, or `c64.real_roms()`). |
| `cycles` | Count cycles (default off): `CY`, cycle-budget checks, volatile accesses at their cycle. |
| `optimize` | Optimize (default on); off keeps the emitted P-Code. |
| `entry_sp` | The SP every entry is called at (after its return address), declared rather than observed: outermost plans are specialized to it and page-1 checks guard the *Stack extent* only. |
| `loaded` | The `[lo, hi)` the program was loaded to: no table extends past it (*Table extents*). |

`trace.collect(image, driver)` runs `driver(session)` twice on the jennings native core over a
copy of `image`: `session.call(pc, a)` is `run_to_rts`; `session.mem`/`session.mpu` are the
core. The traced pass gives starts and transfers, the observed pass the values stored to each
address (the lift uses those of reached code bytes) and the instructions whose accesses other than pushes and pulls reached
page 1 (`page1`). `Facts.dumps()`/`loads()` serialize them as JSON. For banked programs
`c64.machine_facts(image, calls, init, ports)` records the same facts plus `banks` and
`entry_port` on vagrant-moses' native C64 machine; `c64.machine_session` records each call's
halt, registers, I/O accesses (`c64.DEVICES`, $D000-$DFFF, as the machine performed them) and RAM there, and `tools/wpbank.py` checks bank-aware lifts of
HVSC tunes against it. Both collect through vagrant-moses' native trace beside its SMC recorder
(`c64.TRACE_NATIVE`; opcode table `c64.trace_table()`), else, and as the reference, a Python
decode hook and the machine's I/O hook; `tools/traceequiv.py ROWS --hvsc ROOT` checks the two agree on
banked HVSC songs (facts, every record, `c64.check`, cells) and reports each one's cost per call.
Natively the calls run from vagrant-moses' call queue (`smc.Calls`) with no Python per call,
a run of one call queued once with its count (`smc.machine.Queue`). The machine runs
`smc.machine.WINDOW` cycles at a time; after each window the records taken so far (packed:
`c64.CallRecords`, RAM as per-call changes) join a `c64.CallStream` and the SID writes logged
so far join the chip's pending writes, each a `c64.Spill` holding `c64.SPILL` bytes in memory
and the rest zstd-compressed in an unlinked temporary file, so a song's memory does not grow
with its length. The chip is clocked through its pending writes only when the program reads
it, `c64.SPAN` cycles of its (dropped) audio at a time. `c64.check` hands each window's records
to `Program.check`, the same comparison in one native loop, carrying the lift's registers and
memory across windows; the machine's RAM is each window's `origin` plus its records' changes.
Machines, their chips and their views take part in Python's cyclic GC (vagrant-moses
0.12.0); the hooks a `MachineOracle` gives its machine hold it weakly (`smc.machine.weak`),
since its numpy view of the machine's RAM would close a cycle the GC cannot see. A banked `Program.call`/`reference_call` sends non-volatile device accesses to a copy of
`mem`, RAM accesses to `mem`; `reads=` (bytes, or `(address, value)` pairs) replays its volatile reads in order (0 past the
end). `c64.check` lifts with `c64.DEVICES` volatile and replays each call's machine reads
(`c64.read_entries(record)`), so a lift is exact against the machine's devices by construction: one
reading another address, or more or fewer times, differs in the log. Each call pushes the
machine's return address (`ret=`, its sentinel - 1), so the frame and the return match the
machine's. `c64.check` reports the first mismatch, or the agreement
(`c64.outcome`) `halted` when the run's last record stopped without returning and the lift
reaches that halt (a `jam` with the same log, registers and memory; for the machine's
`cycles` cap, every logged access in order with the lift still running at the end of the
shorter record). The interrupts the machine took inside a call are events of its record and
are taken at the same instruction boundary (below). The machine's SID is
vagrant-moses' bus model (unreadable registers read the decaying data-bus latch).

`call(..., irq=True)` (with `ret`, the interrupted PC less one) enters as an interrupt: it
pushes the PC, the status (B clear) and sets I, and the entry returns by `RTI` to the PC; the
lifted code assumes the exit at SP + 3 (`deity_informant.frames`).

`nested=[(icount, pos, entry, a, reg, depth)]` takes the interrupts the machine took inside the
call, in its order: `entry` runs as an interrupt with registers `reg` and `A = a` at the
boundary after `icount` instructions of the context it interrupts (`depth` 0: the call, `n`:
the handler of the last interrupt at depth `n - 1`) have retired, on the same memory and device
log, and the registers the handler leaves are the interrupted code's. A call with such
interrupts runs on the reference interpreter, instruction by instruction, since the lifted
code keeps neither the registers nor the memory of an arbitrary boundary; its handlers run on
the lifted program, a handler interrupted in turn on the reference too. At each boundary the
interpreter's registers must be `reg` and its volatile accesses `pos`: else the result's
`boundary` is the index of the first interrupt that was not at the machine's boundary
(`("boundary", i)` from `Program.check`), as is one that never came. A handler that does not
return ends the call with its halt. `Program.check(..., starts=, nested=)` checks records of
such calls (7 bytes per record: entry u16, a, x, y, sp, p; 20 per interrupt: record u32, icount
u32, pos u32, entry u16, a, x, y, sp, p, depth).

**Code fetched from I/O.** The reference interpreter makes the instruction fetches of code in a
device page (a handler vectored to a CIA register, say) as the machine logs them: replayed
reads (`reads=` pairs with addresses) place the opcode fetch after the byte after a one-byte
opcode, which the machine reads first, and a `BRK` has no opcode fetch. The lift never plans
code in I/O: an edge to it deopts to the reference interpreter.

`call` is `run_to_rts`: push the dummy return `$0001` (or `ret`), set A, run until SP rises
above its start (with `ret`, only at `ret + 1`, as a host trapping that address sees the
return: a stack that wraps is not one), `max_cycles` elapse or a JAM (`halt` is `return`, `cycles` or `jam`). With `log=True`
the bus records `(addr, value, cycle, is_write)` for volatile addresses; `synth=True` makes
the `n`th volatile access read `(cycle ^ n ^ addr) & 0xff`, which makes any cycle or ordering
error visible. Without cycle counting volatile accesses carry the starting cycle, `cycles`
counts only what the reference runs after a deopt (`reference_call(..., clocked=False)` gives
the same stream), and `max_cycles` is an iteration budget: the executor allows
`max_cycles // 2 + 1` passes through loop heads and recursive entries (every instruction takes
at least 2 cycles), so when it runs out the reference has run past `max_cycles` on the same
path and the call ends with halt `cycles` (registers, PC and stack bytes are then not the
reference's). A deopt hands the reference what remains of `max_cycles` after the cycles the
lifted code is known to have covered.

`tools/wplift.py IMAGE --org A --entry PC ... --calls PC:A*N,... --volatile LO-HI --check`
lifts an image file (`--init PC:A` runs an init routine on the reference first, so the entries
are lifted from its result; `--cycles` to count cycles), optionally saves or reuses facts and dumps
the program, and checks it against the reference.

## Pipeline

1. **Discovery** (`analysis.rs`). From the entries, decode every reachable instruction start.
   Targets of `JMP (ind)` and of `RTS`/`RTI` used as jumps come from the code where the
   analysis resolves them (*Computed jumps*), else, like transfers through written operands,
   from the facts' edges. **Code cells** are the bytes of reached instructions that the
   program writes: observed writes, plus every byte of a reached instruction that a store's
   address set holds (*Stores*). An opcode cell's values are its entry byte, the observed
   ones and those of the stores that may write it. The cells, their values and the resolved
   targets are a joint least fixpoint (each round decodes the program they give, an opcode
   cell's values decode new variants, and runs the interval analysis on it); which `RTS`/`RTI`s
   jump through a pushed address and which transfers stay unresolved is decided on a
   converged program, and the fixpoint is recomputed until neither changes, nor the index
   guards (*Table extents*) the resolved transfers and cell values need, which only narrow
   their registers. All other code bytes are constants of the entry state, so code generated
   by an earlier routine is constant. With a bank map each start is decoded in the configuration
   the facts first saw it in (else the one it is first reached in), its bytes from ROM where
   that configuration maps ROM; ROM bytes are constant code, never cells.
   The same analysis (`vsa.rs`) gives every access its address interval, which the guards
   consult (see *Static proofs*).
2. **Functions and stack heights** (`func.rs`). Per function, plan nodes keyed by
   instruction start and stack height relative to its entry. In an outermost plan a start
   gets a node per height the facts saw it at (SP minus the host call's entry SP as
   integers, the plan's own frame), or only the first static height reaching it if it never
   ran; an edge at any other height deopts, and after `TXS`/`TAS`/`LAS` the successor is
   each height the facts saw it at. A callee has one height per start: a conflict at an executed start makes it
   unknown (the callee is then inlined into its caller), at any other start the conflicting
   edge deopts. A `JSR` target is lifted as a separate function (`CALL`) when every `RTS` in
   it is at height 0 or below and nothing pulls above its entry (a decreasing fixpoint over
   the candidates); other callees are inlined: the `JSR` pushes and jumps. An `RTS`/`RTI`
   below the entry height is a computed jump to its facts' targets plus the return sites
   inline `JSR`s pushed at the height it lands on. At an entry, the instruction whose height
   reaches +2 (the entry's own return) exits; after an SP-setting instruction SP is compared
   with the exit threshold at run time. With a bank map nodes are also keyed by the bank
   configuration: a start gets a node per configuration the facts saw it in (or the first
   reaching it if it never ran) from which its bytes are fetched as decoded; an instruction
   that may store to the port cells, and a `CALL` whose callee may (a least fixpoint over the
   plans), continues in each configuration the facts saw its successor in. Callees are planned
   per entry configuration.
   **Tail calls.** An edge at a function's entry height into the entry of a function lifted
   separately (a fall-through, `JMP` or branch into it, whose `RTS` then returns from the
   caller too) is a `CALL` at the caller's SP with no return address, followed by the caller's
   return; a deopt in the callee writes no return address for that frame. An instruction start
   that two or more plans reach at their entry height, from a node that is not such a start,
   is a *shared tail*: it becomes a function entered by tail calls when its plan is balanced
   (plans are made again until no new shared tail appears). So a voice routine called with
   `JSR` for two voices and fallen into for the third is one function with three call sites,
   not a function plus a copy specialized to the third voice's `X`.
   **Instances** (`inst.rs`, `fold=True`; without facts, a bank map or cycle counting). Copies
   of one routine that differ only in their operand bytes, as a player unrolls its per-voice
   code, are lifted as one function of the copy's index `k` (register 4). Copies are paired
   by a lockstep walk of their instructions from candidate entries (each start entered other
   than by falling through, against every start of its opcode): paired instructions agree in
   opcode, which operand bytes are cells, decimal mode, index guard, `JSR` target and code and
   pointer checks; branches and jumps pair their targets, a jump through cells pairs its
   targets in the order of the table bytes they were read from (the tables shifted by one
   offset per copy), and an instruction some copies have and the others lack, straight-line
   and followed by what the others run, is kept and run only by those copies. A tuple whose
   instructions do not pair, or that holds an instruction already in the region, is an exit.
   A class is kept when each copy is a single-entry region (no start but its entry is entered
   from outside it or is an entry or callee, and no exit enters it), the copies' pushes and
   pulls agree and return to the entry height at every exit, and every operand the copies
   differ in is affine in `k` (`base + stride * k`: per-voice zero-page, I/O and table
   addresses), lies in each copy's own code (its cells and jump targets) or is an immediate (a
   per-copy constant, `v0 + sum((k == i) * (vi - v0))`); the starts breaking this are left
   out and the walk repeated. The copies must hold state of their own (an address they
   differ in, a cell, a jump through tables shifted per copy): copies alike in every address
   are one routine repeated, whatever their immediates. A per-copy constant saves nothing (each
   copy keeps its value). Per candidate entry the set of copies
   saving the most instructions (those held, less the merged function's and one call per
   copy) is kept when it has at least three copies: two fit any line, only a third shows the
   operands are a family indexed by the copy. Classes are taken greedily by saving, no start in
   two. Each copy's entry becomes a call at the caller's SP (no return address, as a tail
   call) that sets `k` and continues at the copy's target of the exit taken (register 5
   numbers the exits when there are several). In the merged function a constant operand is
   `a + s * k` where affine, else `v0 + sum((k == i) * (vi - v0))`, a two-byte operand as one
   word; a cell is read at the copy's operand address; a deopt resumes at the copy's start, a
   `JSR` returns to the copy's return address, and a jump through cells dispatches each copy's
   targets to the merged instruction they enter. Accesses carry the hull of the copies'
   intervals.
3. **Stack privacy** (`lib.rs`). With facts, an unexecuted start whose page-1 access cannot
   be checked at its instruction boundary (an earlier op of the instruction has an effect,
   or a fixed page-1 `JMP (ind)` pointer) is not planned. An access other than a push or pull
   is *unguarded* when it may reach page 1 (by its address interval) and did so in the facts
   or cannot be checked. Each function's slots are the stack bytes it pushes (offset `d <= 0`
   from the entry SP is register `NREG - d`) minus, in an outermost plan, the bytes its
   unguarded accesses may hit and those a pull may read before the call pushed them (after
   an SP-setting instruction: a must-analysis of pushed bytes). Those stay in memory, so
   unguarded accesses in an outermost plan never block privacy. An entry called at a single
   SP is specialized to it when that keeps more slots: an interval dataflow over its nodes,
   with SP the entry SP plus the node height, then locates each access, and an unguarded
   access the facts saw whose interval is wider than the page-1 range it reached is guarded
   to that range, which is then all it may hit. The stack is private
   when every plan node's height is known, no volatile range, code cell or reached
   instruction lies in page 1, and no callee has an unguarded access. Then `CALL`s push no
   return address, pushes and pulls of slots are copies, and `RTS` jumps through return
   addresses an inline `JSR` pushed become branches once the values are known. An access
   that may hit a slot kept above SP (or any slot once SP may be set), or any page-1 byte in
   a callee, is preceded by a page-1 check that deopts before the instruction.
4. **Emission** (`emit.rs`). Each instruction's template with constants bound; a cell operand
   byte is a `LOAD` of its address, read once before the instruction's ops; an opcode cell is
   a `CBRANCH` chain over its value set (entry byte, observed and statically stored values)
   ending in a deopt. With cycle counting, cycles accumulate as constants and penalty values
   (page crosses from the effective address, taken branches) and are added to `CY` before any
   possibly volatile access, so each sees its instruction's start cycle, and at block ends;
   without it none of this is emitted.
5. **Optimization** (`opt.rs`), to a fixpoint: CFG cleanup (constant branches, threading,
   merging, unreachable blocks); temporaries renumbered per extended basic block; register
   and stack-slot constant propagation across blocks and across calls (keeping what the
   callee cannot write); value numbering over extended basic blocks (constant folding with
   interval and algebraic rules, copy propagation, common subexpressions, store-to-load
   forwarding with alias analysis over constant, stack-relative and interval addresses);
   combining adds to a register; liveness-based dead-code elimination over registers, stack
   slots and temporaries; without cycle counting, carry-chain fusion (*Carry chains*). Without cycle counting the functions are then summarized (registers
   each may and must define by fixpoints over the call graph, then the registers each uses
   and its callers read after it returns by a least fixpoint of liveness over the whole
   program, so recursion converges like a loop) and optimized again: a `CALL` uses and kills
   only its callee's registers, a callee computes only what its callers read, and a deopt
   whose continuation is lifted (a checked access, a code check) needs only what that
   continuation reads, and what the instruction reads when the reference re-executes it
   (a check before an instruction).

## Guards

| Assumption | Check | Exit |
|---|---|---|
| Opcode cell holds a value of its set | dispatch chain; none for a *closed* cell, which every store that may write it does with known addresses and values | deopt before the instruction |
| Computed target is in the table | `BRANCHIND` table lookup; a table resolved from the code holds every target (the function's `closed` tables) | deopt at the target |
| A table read for a computed transfer or a cell's values stays in the table's extent (*Table extents*) | the index register is compared with the extent's index range before the load | deopt before the instruction |
| An edge reaches a start at a stack height the plan has no node for (never seen there) | none: the edge is the deopt | deopt at the start |
| After an SP-setting instruction, SP is at a height the facts saw the next start at | SP dispatch: `SP - E` compared per height without wrapping (`stats["sp_dispatch"]`), SP restated as `E + height - 2`; a function with a dispatch loads its stack slots from memory at entry | deopt at the next start |
| A store may hit constant code | `codechk(addr, value)` after the store, when the address interval (*Static proofs*) meets constant code or a table the lift read (*Computed jumps*, *Stores*) and its address set, when it has one, does too | deopt after the instruction; the program stays invalid until its constant code matches again |
| A zero-page pointer pair that accesses rely on stays outside the window of their indices (*Pointer windows*) | an entry reads back the pairs some access reads before every path to it stores their high byte; a store that may write a byte of one and may leave it in its window reads the pair back after the instruction, a pair's two stores on one instruction chain once after the second (`stats["ptr_guards"]`) | deopt at the entry; deopt after the store |
| An access that may reach page 1 (by its address interval) does not, while stack bytes are kept out of memory | the address's page is compared with 1 before the access (`stats["stack_guards"]`); with `entry_sp`, only a store that may hit the *Stack extent*, against it | deopt before the instruction |
| A callee that may store into page 1 returns to its caller (stack not private) | the return-address bytes are compared after `CALL` | deopt at the address the `RTS` pulled |
| Entry register value | compare at entry | deopt at the entry |
| The decimal flag is clear at an entry (unless the entry declares `d`) | compare at entry | deopt at the entry |
| Entry SP of a specialized entry (`stats["sp_guards"]`) | compare at entry | deopt at the entry |
| The port registers an entry was always called with (banked) | compare at entry; restated so later configurations fold | deopt at the entry |
| The bank configuration after a port store or a port-writing `CALL`, and at an entry called in several, is one the facts saw there (`stats["bank_dispatch"]`) | dispatch on the effective port value; folds when the stored value is known | deopt at the next start |
| A seen page-1 access of a specialized entry stays in the page-1 range the facts saw | the address is compared with the range before the access (`stats["stack_guards"]`) | deopt before the instruction |
| An unexecuted start with an uncheckable page-1 access, or in a device (a volatile range, or I/O under the bank map), is not reached | none: edges to it are the deopt | deopt at the start |
| Stack wrap | without cycles: at outermost and recursive entries (counting the deepest growth of the callees that do not check), after SP-setting instructions and at blocks reached at different SP offsets; with cycles: as below | deopt at the block |
| Cycle budget (cycles only) | at the entry, loop headers, `CALL` returns and after SP-setting instructions: the longest cycle path and deepest stack growth to the next such block | deopt at the block |
| Iteration budget (without cycles; `stats["budget_checks"]`) | the executor spends one unit entering each loop head (target of a depth-first back edge of the final CFG, through `BRANCHIND` tables too) and recursive entry; no ops are emitted and optimization is unaffected | halt `cycles` |

## Static proofs

A guard is emitted only for an access whose address interval may meet what the guard protects
(constant code, or page 1 where stack bytes are kept out of memory). The interval comes from
a flow-sensitive interval analysis over the whole discovered program (`vsa.rs`), not from
the facts, so a proof holds on every execution that stays in lifted code.

- **Domain.** One interval per register at every instruction start and call context. The flags
  that compare a register with a constant (`CMP`/`CPX`/`CPY`, `INX`/`DEX`, loads and
  transfers) are kept as predicates on the register, so a conditional branch narrows it on
  each edge before intervals are hulled (`DEX; BPL` keeps `[0, n]` from a loop that counts
  down from `n`). An edge whose flag is already known is not taken. Loop heads widen to the
  next constant a `CMP`/`CPX`/`CPY` immediate compares with, `$7F`/`$80` or the extremes.
  `A`, `X` and `Y` also keep their known bits (`known.rs`, joined bitwise), so the values of
  a register are its interval cut by them: `{0, 2}` after `ASL` of `0` or `1`, or where paths
  holding 0 and 2 meet. For computed jumps (below) a byte loaded from a constant base plus an
  index register is tagged with its load while it sits in `A`/`X`/`Y`, the top three stack
  bytes pushed since the function's entry, or a `JMP (ind)` pointer byte stored at an exact
  address, as is a constant there; the tag records whether the index is still the register's
  value, and where an instruction writes the register the bytes it indexed form a group that
  shares one index value.
- **Calls.** A callee is analysed in the context of each `JSR` return site and its `RTS`s
  return there; it also records each of `A`/`X`/`Y` as its entry value plus an offset where
  that holds, so a caller keeps a register through any depth of calls that preserve it.
  An `RTS` whose function's pushes and pulls do not balance (or any `RTS`/`RTI` reached by a
  return that did not go to its call's site) reaches every return site and carries nothing.
  The same happens everywhere when the stack is not private: other stores may then rewrite
  return addresses, so the lift analyses the program again without the call pairing.
- **Memory.** The state tracks the contents of the cell operand bytes of instructions and of
  the bytes the code both loads and stores at a constant address (with the `JMP (ind)` and
  `(zp),Y` pointers): a store at an exact address sets the byte's interval, a store that may
  reach it forgets it, and a load or an instruction's operand reads it. A store's values are
  those of the definitions reaching it, else its register's interval at the store.
- **Unknown.** Other memory contents (a loaded value is its full range); the stack; `SP`.
- **Dependencies.** The analysis assumes the entries are called with the registers they
  declare (guarded at entry) and that control follows the edges the lift contains (every
  other edge deopts). It reads no runtime fact beyond those edges and no data byte, so it
  remains valid after any deopt and from any memory state. The stack-privacy decision still
  uses the facts' page-1 accesses as before.

- **Pointer windows.** The address of a `(zp),Y` or `(zp,X)` access is a zero-page pointer
  `p = mem[q] | mem[q+1] << 8` plus an index `i <= m` (the upper bound of `Y` at a `(zp),Y`
  access; 0 for `(zp,X)`, whose index selects the pair), which is outside page 1 whenever `p`
  is outside the window `[$100 - m, $1FF]`: below it `p + i` stays in page 0, from `$200` up it
  stays at or above `$200` or wraps past `$FFFF` into page 0. A high byte of at least 2 is
  outside every window, and a high byte of 0 is outside it when `lo + m < $100`, so pointers
  into zero page qualify. The analysis (`analysis::Access::ptr`, a tag on the template's op
  chain) names the pair's low and high byte addresses and the index; a pointer whose low byte
  is not a known pair of its high byte needs `m = $100` (high byte at least 2) when indexed.
  `ptr.rs` protects pairs with the window of the largest index of the accesses they free,
  when those accesses outnumber the checks they cost, never one with a byte that is volatile,
  code or a port register, whose high byte the facts saw stored as 1 or as 0 with a stored low
  byte past `$FF - m` (its checks would fail on every call), nor for an access that reached
  page 1 in the facts. An access whose pairs all hold windows covering its index needs no
  page-1 guard. The invariant holds because
  - every lifted store whose address interval meets a byte of a protected pair, except one
    that writes back the byte it just loaded, reads the pair back after its instruction and
    deopts when it is in its window, unless the value interval proves it is not: a high byte
    that is not 1 (nor 0, for `m > 0`), a low byte when `m` is 0 or `$100` (the low byte does
    not matter), or a low byte `v` with `v + m < $100` when every path to the store stored the
    high byte (so the pair held its window before);
  - two stores of a pair on one straight instruction chain (fall-through instructions of one
    variant, as `LDA lo,X; STA q; LDA hi,X; STA q+1`) with nothing between them that reads
    through the pair or may store into it are read back once, after the second: the pointer
    between them is transient and no access sees it;
  - every entry reads back the pairs some access it reaches reads before a store on every path
    has stored their high byte (`St::stored` in `vsa.rs`, an intersection over paths and a
    union over a balanced call), as memory may have been written by the host or by the
    reference after a deopt, which ends the call;
  - lifted code is never interrupted: a call with interrupts runs on the reference, and each
    handler it enters is a lifted entry that checks its own pairs.

  A read-back is two byte loads combined into a word (one 2-byte load where nothing between
  may store into them, *Carry chains*), so value numbering folds it where the stored bytes are
  known; a deopt fires when `(p - ($100 - m)) & $FFFF < $100 + m`.
- **Induction variables.** `scev.rs` finds, per loop head entered only at its head, each index
  register and each byte a loop instruction loads and stores back (`INC`/`DEC`) that changes by
  one constant on every path from the head to each instruction start of the loop, and by a
  nonzero step around it (`INX`/`DEY`, transfers, `ADC #k`/`SBC #k` with the carry known). Its
  start is the hull of its intervals on the entry edges (the byte's: a forward analysis of its
  stores, any value at entries and widened at loop heads). A test every iteration passes (the
  conditional branch of the loop's one back edge, or an exit branch dominating every back
  edge's source) whose flag is set on the straight chain before it from the variable and
  constant bytes (evaluated on each of its 256 values; a value it is not known at continues)
  bounds the trips: the most head visits from any start until the test leaves (`DEX; BPL`,
  `INY; CPY #n; BNE`, `CPX #n; BCC`, `DEC c; BNE`, `TXA; SBX #k; BMI`, and `LDA tab+1,X;
  BEQ done; ...; INX; INX; BNE loop` over a table ending in a zero). The bytes taken as
  constant are the loaded bytes (`loaded`) no cell, the facts nor a store with an address set
  in an earlier round writes: discovery assumes them, and reruns without the bytes a store's
  address set holds that a bound read; the bytes read join the program's constant bytes. A
  loop is the natural loop of the head's back edges (from the starts it dominates), a branch
  may reach them through `JMP`s, and the walk from the head keeps each register as the
  variable plus a constant, so `TXA; SBX #k` (`X` minus `k` while `A` = `X`) steps `X`. The `Y`
  interval a trip bound gives at an instruction of the loop narrows the one the interval
  analysis gives (so the index `m` of a pointer access), and `Program.induction` lists the
  variables.
- **Stores** (`dispatch.rs`). An index register's values at an instruction are the union over
  its call contexts of its interval cut by its known bits, cut by the progression
  `start + k * step` (`k` below the trip bound) of every loop counter it is. It is unbounded
  when its interval is every byte and no trip bound limits it (known bits refine an index,
  they do not bound it). A store whose address is its operand plus index registers has, as its
  address set, the address for each of their values, each evaluated exactly; one through a
  pointer, a cell operand or an unbounded index has none. A stored register's values are those
  of the definitions reaching the store (each instruction writing it on a path to the store
  with no other between, with the values its interval and known bits allow after it), so
  paths that load different constants stay apart; a definition that may leave any byte makes
  them unknown. A store into an opcode or into the operand of a `JMP`/`JSR` through cells
  whose register holds, on every path, a constant, a table byte (read inside its extent, *Table
  extents*, from bytes no store writes) or the byte of a cell (`LDA cell; PHA; ...; PLA; STA
  cell` restores it) stores those values; a cell's values are its entry byte and those of
  every store that may write it, those of the cells they copy included (a least fixpoint).
  A cell is closed when every store that may write it has an address set and known values:
  an opcode cell's dispatch chain then has no deopt, and `Program.sets` holds it. A store
  through a `(zp),Y` pointer whose two bytes hold table bytes or constants on every path
  (inside the tables' extent, from bytes no store with an address set writes) has, as its
  address set, each word whose bytes lie in the pointer bytes' intervals plus each value of
  `Y`, and its access interval is the set's hull: a relocator patching operands through a
  pointer from its table makes them cells. The pointer bytes of `(zp),Y` accesses are tracked
  like `JMP (ind)` pointers, with their value intervals: a pointer access reaches the
  pointer's hull plus its index when that does not wrap, and a store through a pointer stops
  tracking only the bytes it may reach (the low addresses a wrap reaches included).
- **Computed jumps** (`dispatch.rs`). A `JMP (ind)` whose pointer bytes are tagged on every
  path into it, a `JMP`/`JSR` whose cell operand bytes are (a byte that is not a cell is a
  constant), and an `RTS`/`RTI` whose return-address bytes are pushed so on every path, goes
  to the words the tagged loads read for each index value inside the tables' extent (*Table
  extents*): two bytes indexed by the register's current value, or by one group, pair value
  by value, a constant pairs with each byte (`RTS` adds 1). A `JMP`/`JSR` whose operand cells
  are not tagged on some path goes to every word their closed values (*Stores*) make, as when
  the operand was written by an earlier call. A `JMP (ind)` through a pointer in the code
  span that no store may write goes to the pointer's word. The bytes read must be ones no
  store with an address set writes and the facts did not see written (a store of unknown
  address is checked against them, `codechk`); otherwise, or when a byte is untagged on a
  path or two bytes are indexed by unrelated values, the transfer is unresolved and keeps its
  facts' targets. The words are its targets in every context, an `RTS`/`RTI` resolved so is
  analysed as a jump (its function stays balanced), the bytes read join the program's
  constant bytes, and its table is closed. A `JSR` through cells whose targets are all
  functions lifted separately is a `BRANCHIND` to a `CALL` of each. A branch whose offset
  byte is a cell goes to its fall-through address plus each offset the byte may hold, found
  the same way.
- **Table extents** (`dispatch.rs`). A table indexed by a register reads only its own bytes:
  each column (the loads one dispatch pairs by index value: a split table's low and high
  columns, an interleaved table's two bytes) extends from its byte at the least index value
  to the first instruction byte, the end of the loaded program (`loaded`), or the first
  address past it at which the code starts accessing a base other than the table's columns
  (a constant operand, indexed or not, starts at its least index value there); and an index
  value stops being read when one column's byte reaches its end or a byte another column read
  at an earlier value. A table's columns include every base the code reads with the same
  index value (a table load while its register still holds the index another table byte was
  read with), so storing one byte of an interleaved pair alone sees its partner as part of
  the table. The index values read so are a prefix of the index's values (an index the code
  does not bound has every value its known bits allow), so a split table's low column ends
  where its high column starts and an interleaved table where another object starts. Where
  an index may take a value past the extent, an index guard compares the register with the
  extent's range before each column's load and deopts (the program reads outside its
  table). Guards do not narrow the analysis: they are what the final converged program reads
  past its tables.
- **Stack extent** (`lib.rs`). With `entry_sp` declared, the stack bytes the program can hold
  are `[$100 + s + 1 - D, $1FF]`, `D` the deepest byte a push or an elided `CALL`'s return
  address reaches through the plans' call chains (all of page 1 for a recursive plan or an
  unknown height). Zero-page pointers keep no windows: while the stack is private every push
  and every `CALL` also stores its bytes where a load may reach page 1, so loads read the
  stack exactly and need no check, and only a store that may hit the extent is checked
  against it before the instruction. A pointer into zero page or elsewhere in page 1 is
  never checked.

A deopt sets the reference `Cpu` to the architectural state (registers, PC, and with cycle
counting cycles), writes the stack bytes each active frame pushed (from its slot registers)
and the return addresses of active `CALL`s when the program elides them, and runs it to the
entry's return. `stats["stack_private"]` tells whether stack traffic was removed;
`page1_sites` counts the accesses that prevented it, `unknown_heights` the plan nodes of unknown
height, `slot_ops` the pushes and pulls lifted as slots and `stack_ops` the stack accesses left
in memory (an entry's `RTS` reads its caller's return address); `split_nodes` counts plan nodes
beyond the first per instruction start, and `kept_bytes` the stack bytes outermost functions
push that stay in memory (bytes their page-1 accesses may hit).

## Carry chains

Multi-byte arithmetic is a chain of one-byte *links*, each taking the carry of the link below:
`ADC`/`INC` additions (`r = x + y + c`), `SBC`/`CMP`/`DEC` subtractions and comparisons
(`r = x - y - borrow`), `ASL`/`ROL` and `LSR`/`ROR` shifts. `wide.rs` recognizes chains in
each optimized block from the forms value numbering leaves, the carry-in of a link being the
carry-out of the link below (as Ghidra's decompiler matches double-precision `INT_CARRY` /
`INT_LESS` forms, `double.cc`), through negations of the carry (branches over the carried
byte) and the borrow of `DEC`'s `LDA lo; BNE` test. Bytes may be in memory, registers or stack
slots; `stats["chains"]` counts the chains and `Program.chains` lists them with the place of
each operand and result byte (`["abs", a]`, `["x"|"y", base]` indexed, `["v", base, op]`
indexed by the value of the block's op `op`, `None`).

Two plain one-byte loads of adjacent bytes combined into a word (`ZEXT(lo) | ZEXT(hi) << 8`,
as every `(zp),Y` and `(zp,X)` pointer is built) become one 2-byte load where nothing between
the loads and the combination may store into either byte and that leaves an op dead.

A chain whose operand bytes are adjacent plain loads (or a constant) is fused
(`stats["fused_chains"]`): at its first redefined op the operands are loaded wide and the
result computed wide; each result byte, each operand byte loaded later and the top carry-out
(`C`) are redefined from the wide values, so every flag and byte something still reads keeps
its value (`V`, `N`, `Z` of the top byte, an intermediate carry pushed or branched on); the
result's byte stores, when adjacent and nothing between them can read or write their bytes or
leave the block, become one wide store. Loads move up only past stores that cannot reach their
bytes (by base and offset, else by address interval), never past a `CALL`. The fused chain is
kept when dead-code elimination (by the function's liveness) would then leave fewer ops in its
block, which it does when the intermediate carries flow only into the next link. Fusion runs
once the other passes have converged, and they run again after it.

A carry branch over a block that only increments or decrements one byte (`BCC`/`BNE` over
`INC hi`, `BNE`/`BCS` over `DEC hi`) is if-converted into its predecessor as `hi ± carry` when
that block writes no register (the flags it sets are dead at the join) and its load and store
are plain, so the byte is re-stored unchanged when the carry is clear; a join only the two
blocks reach continues the converted block (`LDA lo; BNE; DEC hi; DEC lo`). A conversion whose
block holds a chain through the update is re-optimized and kept only when that completes a
fused chain storing the byte with fewer ops. Chains are matched with reloads of a byte no store
may have reached read as the first load.

Links emitted at an instruction where the interval analysis finds the decimal flag may be set
carry `ir::DECIMAL` and are never fused: entries are guarded to run with `D` clear, so only
code after a `SED`, `PLP` or `RTI` keeps byte-wise arithmetic.

## Decimal mode

`ADC`, `SBC` and the opcodes that run them (`RRA`, `ISC`, `SBC #imm` at `$EB`) and `ARR` follow
the NMOS 6502 in decimal mode (Bruce Clark, "Decimal Mode"): `ADC` takes `N` and `V` from the
intermediate sum before the high-digit correction and `Z` from the binary sum; `SBC` takes every
flag from the binary difference; `ARR` takes `N`, `Z` and `V` from the rotated value and `C` from
the high-digit correction. Invalid BCD digits are corrected as the chip does. There is no cycle
penalty. A template computes both results branch-free and picks by `D` (`lifter._pick`), so one
template serves the interpreter, the generated native templates and the lift. At an instruction
where the interval analysis proves `D` clear the template reads `D` as the constant 0 and the
decimal operations fold away, leaving the binary form the carry-chain matcher and the induction
analysis (`vsa::symbolic_in` evaluates constants, `AND 0` and `OR`/`XOR 0`) recognize. Where `D`
may be set the operations stay, tagged `ir::DECIMAL`. When the program has no `SED` variant and no
entry declares `d` set, nothing but a pull from the stack can set the flag: `PLP` and `RTI` are
then checked after the pull (the lifted code deopts if the pulled byte has `D` set) and the flag
is clear everywhere (`vsa::pull_clear`). `tests/test_decimal*.py` run Clark's
exhaustive cases (every accumulator, operand and carry, decimal flag set and clear) on the
Python VM, the native interpreter, the lift (entries that declare `d`, do not declare it, or set
it in the code) and the SLEIGH module.

## IR

Ops are `di_pcode::Opc` over `di_pcode::Vn` varnodes; Python sees `[mnemonic, out, [inputs],
aux]` with varnodes `[space, offset, size]` (`c` constant, `r` register, `u` temporary).

| Op | Inputs | `aux` |
|---|---|---|
| `COPY` ... `INT_CARRY`, `LOAD`, `STORE` | as `lift()`; a `LOAD` with no output is a throw-away read, made when its second input (if any) is nonzero; a `LOAD` with an output of `n > 1` bytes (a `STORE` of such a value) accesses the `n` bytes from the address up, least significant first, never past `$FFFF` (fused chains); a `COPY` to fewer bytes truncates | `LOAD`/`STORE`: `"volatile"` when the address may be volatile; with a bank map the access's backing in its node's configuration (`ram`: RAM under any device; `rom`: the bank map's ROM image; `bank<c>`: resolved per address in configuration `c` at run time; device otherwise). A ROM byte at a constant address is a constant, a read of the port's data cell is computed from its registers. |
| `BRANCH` | | target block |
| `CBRANCH` | condition | target block (falls through to the next op) |
| `BRANCHIND` | address | table `[(address, block)]`; an address outside it deopts there |
| `CALL` | return address it stands for | callee function |
| `RETURN` | exit PC at the outermost frame | |
| `CALLOTHER` | `deopt(pc)`, `codechk(addr, value)` | operation name |

Registers are `lift()`'s file (A X Y SP = 0-3, C Z I D B V N = 8-14) plus `E` (16, 2 bytes:
the entry returns once `SP >= E`), `CY` (17, 4 bytes: cycles since the entry started, with
cycle counting) and per function its stack slots (18 + `i`: the byte `i` below its entry SP,
printed `s<i>`). Block 0 of each function is its entry; the executor (`exec.rs`) runs the
program over any `di_pcode::Bus`, relocating each function's slots in its value file and,
with cycle counting, calling `Bus::now(cycle)` before possibly volatile accesses.

The IR prints as C and as structured C: [structured.md](structured.md).

### Backends

`Runner::backend` (Python: `Program.backend`, `wholeprog.lift(..., backend=)` or
`wholeprog.BACKEND`, a per-call `call(..., backend=)`; tools: `--backend`) selects the engine:

- `jit` (default): `jit.rs` compiles every function with Cranelift (`opt_level` speed) on first use
  (`Exec::jit`, `Program.compile()` returns the compile seconds) to one native function. The
  value-file cells it uses are SSA variables, written back before a `CALL` and at exits and
  reloaded after a `CALL`. A guard branches straight to the shared exit block with the written cells as the branch's arguments: the optimizer computes a pure value at its first use in the dominator tree and not again below it, so state that only an exit block read would be recomputed in every exit (a chain of guards squares the work and the code). `CALL` is a native call that pushes the same frame record the
  interpreter keeps, so a deopt or budget stop in a callee unwinds with the interpreter's
  frames, slots and resume state. `BRANCHIND` is a `Switch` whose default deopts; block entries
  reached by a branch carry the interpreter's depth check and budget spend (fall-through does
  not). Accesses `Bus::flat` covers (unflagged non-volatile, `ram`, `rom`) are direct loads and
  stores; volatile, device and per-configuration (`bank<c>`) accesses call back into the bus.
  Programs counting cycles, and buses without `flat` memory, run on the interpreter.
- `interp`: the interpreter.

Both give the same registers, memory, volatile stream, deopts and halts; the differential
tests run every lift test on both and random programs through both (`tests/jit.rs`).

## Limitations

- Instruction bytes in volatile ranges are treated as memory.
- With the stack private, stack bytes below SP (popped pushes, returned frames) are not in
  memory unless a load other than a pull may reach page 1 (by its address interval alone,
  whatever the facts saw and the pointer windows assume): then every push and every `CALL`
  also stores its bytes, so a read of them, by lifted code or by the reference after a deopt,
  sees what the machine's stack holds. Code that sets SP (`TXS`, `TAS`, `LAS`) can still expose
  bytes the program never stored, so the random-program tests never store those opcodes.
- One access in a callee that reached page 1 in the facts keeps the whole program's stack in
  memory: it may read the frames of any active function and stack bytes below SP. In an
  outermost plan such accesses keep only the bytes they may hit in memory (all of its pushes
  unless the entry is specialized to its SP).
- A callee whose height conflicts at an executed start is inlined, so its heights come from
  the facts of its callers' frames.
- Recursive functions keep their pushes and pulls in memory (their return addresses are
  still elided).
- Without cycle counting, a call that does not return within `max_cycles` ends with the
  reference's halt (`cycles`) but not its state.
- The host must keep constant code (with the jump tables resolved from the code) unchanged
  between calls, or check `matches` and re-lift. After a deopt the program is invalid until
  `matches` holds: its constant bytes and every closed opcode cell's value in its set, which
  the reference may have changed running code the lift does not contain.
  After a deopt the reference may have stored into constant code, so the next call compares it
  with the lifted bytes first and runs on the reference while they differ.
- A start runs from one backing: one executed from ROM in some configurations and from RAM in
  others is planned only where it is fetched as decoded (the rest deopt). `lift_many` is flat.
