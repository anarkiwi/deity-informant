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
prog = wholeprog.lift(image, [(init, {"a": range(3)}), play], ...)  # entry registers' values
prog = wholeprog.lift(state, [play], ..., free={addr: [v, ...]}, alias=[(lo, g, d)])  # *Init*
prog = wholeprog.lift(image, [init], ..., once=True)  # an init run once from image (*Init*)
# sensitive=[addr, ...]: the volatile reads with effects; other throw-away reads are not made
prog.functions        # [{"pc", "entry", "recursive", "blocks": [{"pc", "ops"}], "tables", "closed",
                      #   "copies", "copy_pcs"}]: copy_pcs per merged instruction each copy's pc
                      # an op: [name, out, ins, aux, pc], pc the instruction it was emitted for
prog.stats            # insns, raw_ops, emitted_ops, ops, cells, code_guards, calls, ...
prog.chains           # [{"function", "pc", "kind", "fused", "x", "y", "r"}] carry chains
prog.induction        # [{"head", "reg", "addr", "lo", "hi", "step", "trips"}] loop counters
prog.jumps            # {pc: [target, ...]} computed transfers resolved from the code
prog.sets             # {address: [value, ...]} closed cells (opcodes and operands) and their values
prog.code             # [addr, ...] bytes treated as constant: code, tables read for jumps and cells
prog.written          # [(lo, hi), ...] runs of the bytes stores with address sets may write
prog.starts           # [pc, ...] instruction starts the functions plan (every instance copy's)
prog.addr_sets        # {(pc, store): [(lo, hi), ...]} address sets of pointer accesses
prog.ptr_sets         # {pc: [word, ...]} the words a (zp),Y access's pointer may hold
prog.reach            # {pc: (lo, hi, base_lo, base_hi)} pointer loads' hulls (*Pointer reach*;
                      #   hi past $FFFF: a pointer advanced without bound)
prog.proven           # the hulls of reach that hold on every run (*Pointer reach*, *Pointer objects*)
prog.objects          # [(lo, hi, [pc, ...])] pointer objects and the loads each serves (*Pointer objects*)
prog.regs             # {pc: (a, x, y)} values the registers hold there on every run, as bytes
prog.bounded          # {pc: (lo, hi)} stores bounded to the array holding their base (*Pointer reach*)
prog.bounds           # {address: (lo, hi)} bytes whose every store has an exact address
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
| `free` | `{addr: [values]}`: the variables an init the host ran on `image` left, and their values over its runs (*Init*; a static lift). |
| `alias` | `[(lo, g, delta)]`: operand pairs of `free` holding pair `g` plus `delta`, which play reads instead (*Init*). |
| `once` | Each entry runs once, from `image` (an init): stores into code make cells only of what may run after them (*Init*). |
| `devices` | A `devices.Model` of what the volatile addresses answer (*Devices*; default none: any byte). |
| `holds` | `[(lo, hi)]`: bytes past the program the host holds as data besides those the lift's stores write (another lift's stores), which a pointer object's loads may read (*Pointer objects*). |

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
   address set holds (*Stores*) (with `once`, where that instruction may run after the store:
   *Init*). An
   opcode cell's values are its entry byte, the observed
   ones and those of the stores that may write it. The cells, their values and the resolved
   targets are a joint least fixpoint (each round decodes the program they give, an opcode
   cell's values decode new variants, and runs the interval analysis on it). An `RTS`/`RTI`
   goes to the return sites of its calls and, once the code gives targets for the address it
   pulls (pushed so on every path, *Computed jumps*), jumps through it to those targets: its
   successors grow with the fixpoint, in the same pass. Which transfers stay unresolved is
   decided on a converged program, and the fixpoint is recomputed until that does not change,
   nor the index guards (*Table extents*) the resolved transfers and cell values need, which
   only narrow their registers. All other code bytes are constants of the entry state, so code generated
   by an earlier routine is constant. With a bank map each start is decoded in the configuration
   the facts first saw it in (else the one it is first reached in), its bytes from ROM where
   that configuration maps ROM; ROM bytes are constant code, never cells.
   The same analysis (`vsa.rs`) gives every access its address interval, which the guards
   consult (see *Static proofs*).
2. **Functions and stack heights** (`func.rs`). Per function, plan nodes keyed by
   instruction start and stack height relative to its entry. A start the facts never ran
   and the interval analysis never reaches whatever the data (no table taken as constant:
   behind a branch on a flag the code already knows) is not planned: an edge to it deopts. In an outermost plan a start
   gets a node per height the facts saw it at (SP minus the host call's entry SP as
   integers, the plan's own frame), or only the first static height reaching it if it never
   ran; an edge at any other height deopts, and after `TXS`/`TAS`/`LAS` the successor is
   each height the facts saw it at. A `TXS` restoring a stack pointer saved by `TSX; STX a`
   (it is reached only from `LDX a`, every store to `a` is such an `STX`, and those `TSX`s run
   at one height in the plan) continues at that height, a non-local exit leaving the frames
   between: an outermost plan checks it by its SP dispatch, a callee compares `X` with SP
   plus the height difference before the `TXS` and deopts. A callee has one height per start: a conflict at an executed start makes it
   unknown (the callee is then inlined into its caller), at any other start the conflicting
   edge deopts. A `JSR` target is lifted as a separate function (`CALL`) when every `RTS` in
   it is at height 0 or below and nothing pulls above its entry (a decreasing fixpoint over
   the candidates); other callees are inlined: the `JSR` pushes and jumps. A callee that
   pulls and pushes back its own return address (inline arguments: it never goes more than
   the address's two bytes above its entry) is lifted separately when two or more `JSR`s call
   it (a procedure), else inlined. With the stack private, no bank map and only `JSR`s
   entering it, the procedure is a **continuation function** (`Function::cont`): each call
   hands it its return address in registers 6 and 7 (`OPLO`/`OPHI`) instead of memory, it
   holds the two bytes above its entry in two slot registers (pulls past its entry read them,
   pushes there write them, and the stores too where pushes are shadowed), and its `RETURN`
   hands them back; the call continues through a computed jump on the address handed back
   plus one, whose one target is where the callee returns in that call's context (past its
   inline arguments, `Graph::past`), any other deopts. A deopt writes the two slots as the
   frame's return address. Otherwise its calls keep the return address in memory and continue
   through the same computed jump on the address the callee left there. An `RTS`/`RTI`
   below the entry height is a computed jump to its facts' targets plus the return sites
   inline `JSR`s pushed at the height it lands on, or to the targets the code gives it
   (*Computed jumps*) alone. At an entry, the instruction whose height
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
   that two or more plans reach at their entry height, entered there from a node no other plan
   reaches at the same height, is a *shared tail*: one a `JSR` targets (a computed `JSR`'s arm
   included) becomes a function entered by tail calls when its plan is balanced (plans are made
   again until no new shared tail appears), and code the plans share past it stays in that
   function, also where it returns to the entry height after a push and a pull; any other is
   code of each plan reaching it. A join reached only within one procedure is not a function. So a voice routine called with
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
   out and the walk repeated. Each copy is whole blocks: its exits enter block starts (a
   branch's, jump's, call's or return's successor, an entry or a callee), as walked before
   the per-copy constants are left out, unless they run straight into the next copy's entry;
   statements alike only in a prefix of their blocks (loads of fields at a stride, each
   followed by its own immediate) are not copies of one routine. The copies must hold state of their own (an address they
   differ in, a cell, a jump through tables shifted per copy): copies alike in every address
   are one routine repeated, whatever their immediates. A per-copy constant saves nothing (each
   copy keeps its value), and a class with one has every exit of each copy run straight into
   the next copy's entry (an unrolled body, not a test choosing among alternatives). Per candidate entry the set of copies
   saving the most instructions (those held, less the merged function's and one call per
   copy) is kept when it has at least three copies: two fit any line, only a third shows the
   operands are a family indexed by the copy. A class starts where its copies' shared run
   does: while every copy's entry is entered from one instruction only (falling through or
   branching to it), the class walked from those instructions replaces it when it saves more,
   so copies entered at a branch on flags the instructions before it set differently (`LDY`
   before the first copy, `LDA` before the others) are one class entered at the branch, not
   one class per arm. Classes are taken greedily by saving, no start in
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
5. **Optimization** (`opt.rs`). First *status lanes* (`lanes.rs`): per web of a stack
   slot (its definitions and the reads they reach), when every definition packs one-bit
   values (the status `PHP`, `BRK` or an interrupt frame pushes), each bit not constant
   across them is a lane register copied beside each definition and every read reads the
   byte rebuilt from the lanes it observes (demanded bits to the end of its block, where every
   register and every temporary another block reads is demanded whole): a `PLP`
   or `RTI` restores each flag from its lane, `PHP; PLA; AND #$08` builds only `D`. The
   slot keeps its packed definition for deopts and the memory copy a load other than a pull
   may read. Then, to a fixpoint: CFG cleanup (constant branches, threading,
   merging, unreachable blocks); temporaries renumbered per extended basic block; register
   and stack-slot constant propagation across blocks and across calls (keeping what the
   callee cannot write); value numbering over extended basic blocks (constant folding with
   interval, known-bit (a mask or right shift through disjoint ors and lossless left shifts)
   and algebraic rules, copy propagation, common subexpressions, store-to-load
   forwarding with alias analysis over constant, stack-relative and interval addresses);
   combining adds to a register; liveness-based dead-code elimination over registers, stack
   slots and temporaries; without cycle counting, carry-chain fusion (*Carry chains*). Once
   these converge, a register copy dead on some path from its block (the register written
   before it is read, as past a side exit whose deopt alone reads it) is sunk to the extended
   basic block descendants that read the register, and the rounds run again: the register
   keeps its old value on the other paths, so a later load of a cell it was stored to is
   forwarded from it. Without cycle counting the functions are then summarized (registers
   each may and must define by fixpoints over the call graph, then the registers each uses
   and its callers read after it returns by a least fixpoint of liveness over the whole
   program, so recursion converges like a loop) and optimized again: a `CALL` uses and kills
   only its callee's registers, a callee computes only what its callers read, and a deopt
   whose continuation is lifted (a checked access, a code check) needs only what that
   continuation reads, what the instruction reads when the reference re-executes it
   (a check before an instruction), and the registers its condition reads (the continuation
   takes them to hold what the check excludes, as a pulled `D` clear).

## Init

A host whose program has an init runs it and lifts the other entries from the memory it leaves
(`cgen`, [*Init*](cgen.md#init)), and lifts init alone with `once`; its run replays the reads
of init's polling loops (`Program.call`'s `reads`, [*Polling loops*](cgen.md#polling-loops)).

`once` declares that each entry runs once, from `image`: a store makes cells only of the bytes
of the instructions that may run after it in the same call (reachable from it, a call matched
to its return: the routines holding it from it on, every routine they call, and each caller from
the call's return site on once a routine holding it returns), and needs no code check where it
has an address set; one of an interval needs it only where the interval meets a table read as
constant or code that may run after it. Code a store overwrites after it ran (a block copy
over the routine that chose its source) stays constant code. Interrupts are not modelled.

With one entry and a declared `entry_sp`, an entry no input decides makes one run
(`once.rs`): its inputs are the registers not declared one value, the bytes outside `loaded`,
the volatile bytes, `free` and the stack above SP. The entry runs on the reference interpreter
from `image` while the interval interpreter follows each instruction with every input unknown;
when every branch's flag, jump or call operand, return address, SP and store address of the
run is exact up to the entry's return (a `BRK` ends it), the run is the only one, and each
instruction it ran read, on every run, the operand bytes it read at each of its executions
when those were the same. A store or load through operand cells such a run decided has the
address they make (`Program.addr_sets`), with no page check: a relocator's walk over a table
ending at a zero, then a copy through the operands it patched, is a load and a store at the
addresses the walk wrote.

For the entries lifted from what init leaves, `free` (`{addr: values}`) names the
bytes init stored, whose values are the song's, and their values on each of its runs in order;
each is a variable:

- never constant: an instruction byte is a cell (a free opcode byte dispatches over its values),
  a free byte no constant table, and with the facts' writes, data stores with address sets
  write (the runs of *Pointers into the song* reach the bytes it filled past the program);
- its entry value unknown: no byte bound, cell value set or vector is taken from the image;
  a free opcode byte still decodes, beside its values, the values stores of known values
  write to it (play storing an `RTS` over the opcode init set);
- a pointer pair with a free byte has as its floor (*Pointer pairs*) the run of data bytes
  holding every word its bytes' values make (the words it holds on each run, where both bytes
  are free: *Pointers into the song*), else any word: play reads through an operand init
  relocated with that run's bounds-checked reader, and a store through it writes that run
  (*Bounded stores*).

`alias` (`[(lo, g, delta)]`, from `deity_informant.postinit`'s provenance of init's run) makes
an operand pair `lo` read as pair `g` plus `delta`, where all four bytes are free cells no lifted
store may write: the operands init set from one word at constant offsets are one variable.

`stored` (runs `[lo, hi)`) are the bytes any store of the lift may write: its address set where
it has one, else its address interval. A host passes as `held` instead of `free` the bytes init
leaves one value on every run that none of them is (cgen [*Init*](cgen.md#init)): the lift
reads them from the image like any other byte, and they are data the program has past its
image, as `free` bytes are, for the runs a pointer pair's floor is taken from. Its analysis
takes their values; its code loads a data operand of them from memory as a cell's (a branch's,
jump's or call's stays its target), so the values are data of the code, not code.

`known` (`{addr: values}`) gives a byte of `free` every value it may hold after init, where
init's own lift bounds it (`Program.values` of that lift: the byte's entry value and those of
every store that may write it, each of known addresses and values): its value set, so a cell
only init writes, with known values, is closed. `checked=True` declares that the host checks
the entries' declared `A`, `X` and `Y` values before each call: the analysis takes them, and
no entry guard tests them. A translated init is lifted for any `A`, so its lift holds whatever
values the host declares; cgen lifts it again `checked` for the declared songs only to name the
guards they imply (cgen [*Init*](cgen.md#init)).

### Driver loop

A host that runs init and then calls the other entries in a loop (`init(A); for (;;) play();`)
passes `driven` (`{addr: values}`): what init leaves whatever the data, `Program.exit` of its
`once` lift. That lift finds it with no table taken as constant, with `A` unknown, every byte a
store of known address writes tracked, devices answering what their model allows: the join of
the tracked bytes at every return that may reach the host (all but a callee's at its entry
height to its call's return site and one at a known height above its entry, through what it
pushed; a return at an unknown height counts). Immediates and device answers reach it, a byte
loaded from the song does not, so it holds for every song and none of its data reaches the
`.c`.

The entries' lift (its pointer reach pass) then solves the driver head: what every call of an
entry starts with of the tracked bytes, the least fixpoint of `driven` joined with what every
return to the host leaves, widened at the head with the loop thresholds, the value sets of
tracked bytes joined exactly (each at most `CAP` values), again with no table taken as
constant. Every analysis of that pass starts each entry from the head, and its byte bounds
start from the head's intervals where init's variables have none. Per byte the head knows,
the hull of what it holds at every start the entries reach (still with no table taken as
constant) bounds each plain load of its address in the optimizer's value numbering, so a
branch on a cursor init zeroes and play wraps folds where its bound decides it. A store
keeps a register's value set in a tracked byte only where no table is taken as constant:
a set read from the song's tables never crosses a store. A tracked byte with a value set has
the set's hull as its interval, so widening never loses it; a flag an instruction sets from
a tracked byte it reads (`DEC speed`) narrows the byte's set on each branch on it.

## Guards

| Assumption | Check | Exit |
|---|---|---|
| Opcode cell holds a value of its set | dispatch chain; none for a *closed* cell, which every store that may write it does with known addresses and values | deopt before the instruction |
| Computed target is in the table | `BRANCHIND` table lookup; a table resolved from the code holds every target (the function's `closed` tables) | deopt at the target |
| A table read for a computed transfer or a cell's values stays in the table's extent (*Table extents*) | the index register is compared with the extent's index range before the load; none where its values on every run lie inside (`Program.regs`, *Pointer reach*) or the register guards before it keep it there on every path (*Implied guards*) | deopt before the instruction |
| A store to a constant base plus an index the code does not bound stays inside the loaded program (*Table extents*) | the index register is compared with the index range before the store | deopt before the instruction |
| An edge reaches a start at a stack height the plan has no node for (never seen there) | none: the edge is the deopt | deopt at the start |
| A callee's `TXS` restoring a saved stack pointer sets SP to its saved height | `X` compared with SP plus the height difference | deopt before the instruction |
| After an SP-setting instruction, SP is at a height the facts saw the next start at | SP dispatch: `SP - E` compared per height without wrapping (`stats["sp_dispatch"]`), SP restated as `E + height - 2`; a function with a dispatch loads its stack slots from memory at entry | deopt at the next start |
| A store may hit constant code | `codechk(addr, value)` after the store, when the address interval (*Static proofs*) meets constant code or a table the lift read (*Computed jumps*, *Stores*) and its address set, when it has one, does too | deopt after the instruction; the program stays invalid until its constant code matches again |
| A zero-page pointer pair whose loads read unchecked lies in its object (*Pointer objects*) | a store that may write a byte of the pair reads it back after its instruction (a pair's two stores on one instruction chain once after the second; a low byte left to a flag adds the flag times 256), and a load an entry reaches before any store checks the pair before its instruction (`stats["obj_guards"]`, `objchk`) | deopt after the store; deopt before the instruction |
| A walk's pointer plus `Y` lies in its object (*Walks*) | after the anchor setting `Y`, the pair's pointer plus `Y` (`stats["obj_guards"]`, `objchk`) | deopt after the anchor |
| An access whose bases lie in the program stays in it (*Machine model*) | before the access, its address (`stats["obj_guards"]`, `objchk`, `def` `"machine"`) | deopt before the instruction |
| A zero-page pointer pair that accesses rely on stays outside the window of their indices (*Pointer windows*) | an entry reads back the pairs some access reads before every path to it stores their high byte; a store that may write a byte of one and may leave it in its window reads the pair back after the instruction, a pair's two stores on one instruction chain once after the second (`stats["ptr_guards"]`) | deopt at the entry; deopt after the store |
| An access that may reach page 1 (by its address interval) does not, while stack bytes are kept out of memory | the address's page is compared with 1 before the access (`stats["stack_guards"]`); with `entry_sp`, only a store that may hit the *Stack extent*, against it | deopt before the instruction |
| A callee that may store into page 1 returns to its caller (stack not private) | the return-address bytes are compared after `CALL` | deopt at the address the `RTS` pulled |
| Entry register value (its declared values) | compare at entry: one value, a range check for a run of values, else a test of each; none for `A`, `X`, `Y` with `checked` (the host's) | deopt at the entry |
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
  transfers) are kept as predicates on the value compared, held by every one of `A`, `X`
  and `Y` that holds it plus an offset of its own (`TAY` leaves it in both `A` and `Y`), so a
  conditional branch narrows each of them on each edge before intervals are hulled (`DEX;
  BPL` keeps `[0, n]` from a loop that counts down from `n`; `TAY; BPL` bounds the `Y` index
  as `CPY #$80; BCC` would); a callee's return keeps them, so a branch after `JSR` on the flags of the
  callee's last load narrows what it returns. An edge whose flag is already known is not taken. Loop heads widen to the
  next constant a `CMP`/`CPX`/`CPY` immediate compares with, `$7F`/`$80` or the extremes.
  `A`, `X` and `Y` also keep their known bits (`known.rs`, joined bitwise), so the values of
  a register are its interval cut by them: `{0, 2}` after `ASL` of `0` or `1`, or where paths
  holding 0 and 2 meet. For computed jumps (below) a byte loaded from a constant base plus an
  index register is tagged with its load while it sits in `A`/`X`/`Y`, the top three stack
  bytes pushed since the function's entry, or a `JMP (ind)` pointer byte stored at an exact
  address, as is a constant there; the tag records whether the index is still the register's
  value, and where an instruction writes the register the bytes it indexed form a group that
  shares one index value. Groups of writes whose carried values meet (paths or contexts
  joining, two loads of one table indexed by them) form one carry class, whose writes all
  carry into one register: the fixpoint reruns with the classes united, each write of a class
  then forming its group (and undoing the one an earlier write of the class made); bytes two
  loads of one table read by one class's value are that value's.
- **Calls.** A callee is analysed in the context of each `JSR` return site and its `RTS`s
  return there; it also records each of `A`/`X`/`Y` as its entry value plus an offset where
  that holds, so a caller keeps a register through any depth of calls that preserve it.
  An `RTS` whose function's pushes and pulls do not balance (or any `RTS`/`RTI` reached by a
  return that did not go to its call's site) reaches every return site and carries nothing.
  Balance is judged over the starts the analysis reaches whatever the data (no table taken as
  constant), again until no more returns pair: an edge it never takes (a branch on a flag the
  code already knows, into data) unbalances nothing.
  The same happens everywhere when the stack is not private: other stores may then rewrite
  return addresses, so the lift analyses the program again without the call pairing.
- **Memory.** The state tracks the contents of the cell operand bytes of instructions and of
  the bytes the code both loads and stores at a constant address outside the volatile
  ranges (with the `JMP (ind)` and `(zp),Y` pointers): a store at an exact address sets the byte's interval, a store that may
  reach it forgets it, and a load or an instruction's operand reads it. A store's values are
  those of the definitions reaching it, else its register's interval at the store; a store
  that steers control (into an opcode or a jump's operand) takes the table bytes its register
  was loaded from when it holds one on every path, which are exact where the interval is a
  hull.
- **Devices** (`dev.rs`, from `deity_informant.devices`). A read of a volatile address the
  model describes answers its register's set: the bits `latch` the value last written (to
  any of its mirrors), `ones` 1, `live` any; a derived or status register the set of the
  value bits `cmask` of its control register hold (time unknown). The values written to the
  registers reads depend on are tracked bytes at their canonical addresses: a store to a
  mirror sets its register's, one of unknown address that may reach a register forgets them
  all, and so does a callee storing so. Any other volatile read is any byte.
- **Constant tables.** A volatile byte is never constant: a device answers any value. A load
  whose address interval (at most a page) holds only loaded bytes taken as constant (*Table extents*: none a store with an address set writes) and no cell
  reads the hull of those bytes, and they join the tables read as constant, so a store of
  unknown address that may hit them is checked like one into code (`codechk`). The constant
  set is an assumption discovery verifies: it starts at every loaded byte, the analysis
  resolves the stores' address sets under it (a store through a pointer from a table of
  addresses, `LDA tab,X`/`LDA tab+1,X` into the pair, has the words the table holds), and a
  byte a store with an address set may write is not constant from the next round of discovery
  on, nor is a table byte inside the address interval of a store from a constant base plus an
  index with no address set (a page from its base at most): data it may write. Only the bytes
  a store reaches leave, so the set settles at the greatest one no resolved store writes; a
  table entry naming the table leaves the store it makes with no set, checked. An init lifted
  once is its own program: the other entries run after it.
- **Value sets.** `A`, `X` and `Y` keep the set of values they may hold where their interval
  and known bits do not give it, at most 64 values (past them, the interval). A load from a
  constant base plus an index register whose addresses at the index's values are all
  constant bytes gives the set of those bytes, which join the tables read as constant;
  transfers and constant offsets move a set, and an instruction that reads a set and no
  memory runs on each combination of its inputs' values (a flag's interval; at most 128)
  where every result is exact (`LSR`, `AND`, `ADC #k`). A conditional branch cuts the sets
  of the registers its predicate holds (an index's set by the bytes it read, *Pointer
  reach*, only there: the guards on a table's extent do not depend on its bytes). Joins
  unite sets. So a selector read from a constant table bounds the index set a later
  table read, a store's address set or a computed jump's words come from (`Sets::values`).
  The top three bytes pushed since the function's entry keep what was pushed (`A`'s
  interval, known bits and set; for `PHP` the status byte's known bits) through a call that
  returns to its site, and lose it to an instruction that may store into page 1 or sets SP:
  a `PLA` takes it, a `PLP` the flags its known bits give. A declared entry value seeds its
  register (`SEC; SBC #$0A; PHA; ...; PLA; TAX` after `CMP #$0B; BPL` carries the songs past
  ten into `X`).
- **Implied guards** (`why.rs`). Over each function as emitted, a register keeps its values
  through copies, takes a constant's, is narrowed past each register guard (an entry's or an
  index guard) and is unknown after any other write or a call; a register guard whose values
  there lie inside its range on every path is not taken (`info["trap_info"]`'s `held`): the
  later of two guards on one index is left out where the earlier lets through no more.
- **Pointer word sets.** The analysis runs twice when the first run finds a store at an
  exact address into a byte of a `(zp),Y` pointer pair after which both bytes hold tagged
  table bytes or constants in every context (*Computed jumps*): the words they make (at most
  64, inside the tables' extent, from constant bytes no store with an address set writes;
  an index guard only for an index the code does not bound) *seed* the second run, in which
  that store leaves the pair holding those words, each with the values `Y` holds with it:
  `{(p, y)}`. `Y` moving by a constant moves every word's values, any other write of `Y`
  gives each its new values, and a store that may reach either byte drops the pair. A
  `(zp),Y` access reaches `p + y` for each pair; a load of constant bytes there gives their
  set, and a register holding it (until `Y` or the pair changes) cuts the pairs on a branch
  to those whose byte passes, so a walk over records ends per word at its own end marker and
  reads only the column `Y` steps over. Such an access has that address set
  (`Program.addr_sets`; a store's where it has none), its pointer the words
  (`Program.ptr_sets`). The tables a pair is seeded from (at most 64 entries) join the
  tables read and their guards are emitted whatever words their bytes make, so the lifted
  code does not depend on those bytes' values. A store through two cell operand bytes tagged on every
  path into it writes their words the same way.
- **Byte bounds.** `Program.bounds` gives, for each byte (outside page 1, not a cell) every
  store that may write has an exact address and a value narrower than a byte, the hull of
  its entry byte and the stored values: what it holds whenever lifted code runs.
- **Unknown.** Other memory contents (a loaded value is its full range); the stack; `SP`.
- **Dependencies.** The analysis assumes the entries are called with the registers they
  declare (guarded at entry, or by the host with `checked`) and that control follows the edges the lift contains (every
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

  With a declared entry SP (a static lift) the windows keep pointers out of every *zone*:
  page 1 and the volatile ranges widened to whole pages (`ptr::zones`), each window
  `[lo - m, hi - 1]` of a zone `[lo, hi)`. An access through protected pairs then reaches no
  device, so its throw-away read is not made and it is not volatile. With no facts a pair is
  not protected when a lifted load reads one of its bytes other than to dereference or step
  it (a pair also used as data may hold any value between its uses as a pointer), and its
  window is narrowed to `$FF` (else given up) when a store to its high byte names only pages
  inside the `$100` window (a pair cleared to zero). A store of unknown address checks only
  the pair it wrote (`a - q < 3`, the completing store's partner included); an indexed store
  completes a pair when a later one on its chain stores at its addresses plus one through the
  same, unchanged index register (`STA $21,X; ...; STA $22,X`). Every other access through a
  pointer that may reach a zone (a pair no window keeps, or an absolute operand that is a
  cell) is *zone-checked*: a store's address and its throw-away read's high bytes are
  compared with the zone pages before the instruction (`stats["zone_guards"]`, deopt before
  it); a load reads memory with no throw-away read (a pointer read leaving the program's data
  is the program's error, and reads the translation's memory: in cgen, `rd`), unless every value its pointer's high byte may hold (the initial byte and every stored
  value, or a closed cell's values) names, with the next page when indexed, no zone page. A
  pointer whose high byte some store sets to a value naming only zone pages, or an operand
  whose high byte names one in the image, means the device or the stack and is left as it is. Pushes are stored to memory only for loads that may still
  reach page 1.
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
  variable plus a constant, so `TXA; SBX #k` (`X` minus `k` while `A` = `X`) steps `X`. A `JSR`
  whose callee returns to its return site on every path is one step to that site (the edges
  to it from the returns the call runs are no edges into the loop, so a routine other sites
  also call does not enter the loop), changing the bytes the callee may write and the
  registers that do not hold their values at the call when it returns. Which places (data
  registers and constant addresses) hold the value a place held at the call is a must
  analysis over the callee's instructions: a transfer or a load of a place holding it, a
  store of it to a constant address (`STX keep` ... `LDX keep`), any other store forgetting
  the addresses it may reach; a nested call composes the places its own analysis gives for
  each place holding the value at it, a nested call that does not return to its site (one
  unwinding by `TXS` to a stack pointer saved above it) is walked through, a `BRK` is a call
  of its handlers returning past its signature byte, and a return or computed jump elsewhere
  continues at its targets. A register holding its value at the call at a callee's
  instruction holds there the values it has at the call (when only the loop's calls run
  them), so a voice loop's `X` (`14, 7, 0` by `TXA; SBC #7; TAX`) bounds the routine it calls,
  through a `BRK` or a stack switch in it. The `X` and `Y`
  intervals a trip bound gives at an instruction of the loop narrow the ones the interval
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
  In a static lift a store whose address is unknown only through its own operand cells has
  the addresses the values those cells may hold give, the cells' values and those addresses
  found together from none (*Termination*).
  A cell is closed when every store that may write it has an address set and known values:
  an opcode cell's dispatch chain then has no deopt, and `Program.sets` holds it. A store
  through a `(zp),Y` pointer whose two bytes hold table bytes or constants on every path
  (inside the tables' extent, from bytes no store with an address set writes) has, as its
  address set, each word whose bytes lie in the pointer bytes' intervals plus each value of
  `Y`, and its access interval is the set's hull (its throw-away read the pages from the
  hull's least address less a page to its greatest): a relocator patching operands through a
  pointer from its table makes them cells. The pointer bytes of `(zp),Y` accesses are tracked
  like `JMP (ind)` pointers, with their value intervals: a pointer access reaches the
  pointer's hull plus its index when that does not wrap, and a store through a pointer stops
  tracking only the bytes it may reach (the low addresses a wrap reaches included).
  A store through a pointer a loop advances (a block copy: `INC hi` per page, counted by an
  index register) is evaluated per value of the index its tables are read with, which relates
  the pointer to the count: its loop's head must dominate the store and be entered by one edge
  ending a straight chain on which the pointer bytes and the counter are table bytes of one
  index value (or constants), and no instruction between the head and the store outside the
  loop may write the pointer bytes. Per index value the chain runs exactly to the head, so the
  loop's induction variables start at known values and its trip bound follows (taking stores
  through the pointer not to write its bytes); each pointer byte the loop advances takes its
  start plus the loop's changes to it over the trips, the other its value at the head, and the
  store's address set is the words they make plus each value of `Y`. A set reaching zero page
  or the stack is not taken, so the assumption holds. Bytes past the image such a set holds
  are data (`Program.written`), and the reached instructions it overwrites are cells. A load
  through such a pointer gets its set the same way (when its tables need no index guard: it
  is a fact the lift does not rely on), and its interval is the set's hull.
  A load from an operand plus index registers whose index sets are bounded has the address
  for each of their values likewise.
  `Program.addr_sets` gives these sets per instruction and kind (`{(pc, store): [(lo, hi)]}`).
  Discovery's rounds take each store's address set as what the store writes: a store with a
  set stops tracking only the bytes the set holds, so a relocator's store of an operand's low
  byte through its pointer leaves the pointer's tags for the store of its high byte. A store
  keeps its set while every round finds one, the set growing to those found; one a round finds
  none for is dropped for good, so the rounds end at sets that hold of the states computed
  with them.
- **Pointer reach** (`vsa.rs`, static lifts). `Program.reach` gives, per load through a
  `(zp)` pointer or a cell operand, the hull of its addresses and of its base (the index
  registers zero) from a second pass of the analysis that assumes what *Table extents*
  assumes: an indexed load whose index the code does not bound, or that reads bytes no store
  writes, reads its table only inside its extent (a table one value of a bounded index fills
  is read whole: its columns overlap), and so does an indexed store whose index the code does
  not bound. Every byte only stores with bounded addresses write (each store's address set
  where it has one) holds the hull of its entry byte and the values they store (those its
  definitions give where known: inside an arm of a switch on a byte, a store of the byte
  writes the arm's case values, *Computed jumps*); a zero-page
  byte every path since the entry stored, of those values alone. Loads take a byte's hull where it is not tracked (of the bytes its
  index register's values address, for a constant base plus an index register). A store whose
  index register holds a value set writes the addresses of its values alone.
  Starting from no hulls, each round of the pass gives hulls that hold of every run, met
  with the last round's, until a round changes none. The pass changes no lifted code: the
  hulls hold only for runs that keep those assumptions (cgen reads past them through
  `oob_rd`). It runs again with none (`Program.proven`, which also holds the address hulls
  of loads from a constant base plus an index register): an indexed access reaches every byte
  its index set gives, so those hulls hold on every run, and so do its registers
  (`Program.regs`): an index guard whose register's values there lie inside its range is
  not emitted, as no run that stays in lifted code leaves them. The proof reads memory, so
  the bytes a load may read keep their bounds while the lifted code runs (`Program.kept`,
  which `matches` checks after a deopt, *Limitations*). Both passes run once, on the program
  discovery settles on, and refine the domain:
  - *Value sets* (*Value sets* above) over the bytes' bounds too: a load gives the set of
    the bytes at the index register's values (inside the extent) where each byte's bound is
    one value. A conditional branch also cuts the set of the index register of a table byte
    a register holds (the index still its value) to the indices whose byte passes: an end
    marker tested before its pointer is used leaves the pointer.
  - *Pointer pairs* (`word.rs`). A pair of tracked bytes `q, q + 1` holds a 16-bit value as
    integers that do not wrap: a base plus a non-negative offset, unbounded above. `CLC; ADC`
    of the pair's low byte stored back into it owes the high byte the carry until the branch
    on the carry and an `INC` of the high byte (or an `ADC #0` of it stored there) settle it,
    and a pair whose two bytes are stored from the two bytes of one pair or sum holds that
    value; `INC lo; BNE; INC hi` advances it by one alike, owing the high byte while the zero
    flag is set; any other store gives a pair its bytes' hull, or, where its bytes hold table bytes
    indexed by one value, the hull of the words of the index values both loads read. A value past `$FFFF` is a pointer
    walked past the top of memory, which reads out of bounds. A pair no instruction since
    the entry stored holds a value in its floor: the hull of its entry word (*Init* for a
    pair with a free byte) and of the values every return of an entry leaves, a least
    fixpoint the rounds reach once the byte bounds hold, whose upper bound, where a return
    raises it, widens as at a loop head (a pointer advanced on every call). At a loop head
    an upper bound that rises widens to the end of the run of data bytes it lies in, past
    every run to the top; a pair's first value and its bytes' hull do not widen. A callee
    keeps the caller's tracked bytes and pairs it stores into none of (the address intervals
    of the stores it runs until it returns). `ADC #0` of a
    byte holding a pair's high byte while the carry holds a sum of the pair's low byte gives
    the sum's high byte, wherever the sum is stored.
  - *Pointers into the song* (the first pass only). The program's data bytes are its loaded
    bytes of no instruction and the bytes from its start up that stores with address sets
    write (a block copy's destination, the host init's stores: song data it unpacks), past the
    program up to the last of them below the volatile ranges with the bytes between, in runs.
    A pair of the host init's variables (*Init*) has as its floor the run holding every word
    it holds on one of init's runs that points into a run (one pointing into none, an idle
    voice's null or the pointer init's own patching of code leaves, is taken not to be read
    through, as a table is taken not to be read past its extent: cgen reads it through
    `oob_rd`; no such word: no value, until play's returns give it one). A
    byte of a run (read through a pointer into it, or from a table inside it) added with a
    clear carry to the low byte of such a pair pointing into that run on each of init's runs,
    and a byte of it added with the carry left to the high byte, are the parts of a
    relocated word (song data offsets plus the origin a relocating init left): a pair stored
    from the two parts points into that run. A pointer whose two bytes are read through
    one pointer (a jump command's operand) points into what that pointer points into: it
    holds that pointer's values. A pair whose two bytes are table bytes indexed by one value,
    not all constant (a store with an address set or a bounded address writes one), read from
    one run, points into that run. Two stores at one index (the
    instructions between them falling through and writing no index register) of the low and
    high bytes of one pair or sum make their tables' bases a word column when no other store
    writes a byte of either base's run and every store into those runs is such a store: a
    pair loaded from the two columns at one index holds the pointers the columns' stores
    write (a return stack's pop reads what its push stored), a least fixpoint from none. The
    pass's registers on every run (`Program.regs`) are the second pass's.
  - *Bounded stores* (`Program.bounded`). A store from a constant base plus an index register
    that may hold any byte on some run of the second pass, whose base is a data byte of the
    program and that may reach an instruction byte past it, writes the run of data bytes
    holding its base, the array it indexes; past it the write is out of bounds (no code
    check). Its access is that run. So is a store through a cell operand pair holding a
    pointer into one run of data bytes.
  An index register is one the instruction's template reads before writing (`LDX abs,Y`
  reads `Y`).
- **Pointer objects** (`objects.rs`, static lifts). The hulls of *Pointer reach* hold of the
  loads through a `(zp),Y` pair only while tables are read inside their extents; checks at
  the pair's definitions make them hold on every run. The definitions of a pair are the
  stores that may write either byte (one whose address is exact replaces it), and, where a
  path from an entry reaches a load before any store or other load, that load: the pointer
  an entry finds (one init left per argument). A forward dataflow over every flow edge (a
  return whose targets are not computed reaching every return site) gives the definitions
  reaching each load; definitions sharing a load, and the loads they reach, are one
  *object*: from the least base of the loads' reach hulls (a data byte) to the greatest, up
  to the end of the last run of data bytes starting between them that the host holds (a
  pointer may stand one past its last byte). Each
  store definition reads the pair back after its instruction and each load definition before
  it, and deopts where the pointer lies outside the object; two stores on one straight chain
  are read back once, after the second, and a store of the low byte that leaves the high byte
  to a flag (`STA lo; BCC l; INC hi` after an add, `INC lo; BNE l; INC hi`; nothing between
  writes the flag or the pair or reads through it, the increment entered from the branch
  alone) checks the pointer plus the flag times 256, the increment none. Between those points
  only lifted stores, whose address intervals are sound, write the pair, so a load of the
  object reads its base plus `Y` (its values on every run, `Program.regs`): `Program.proven`
  holds that hull for each load whose `Y` stays within the object's fields (the largest `Y`
  a load adds as one value) and whose hull lies in the bytes the host holds (`loaded`, and
  the bytes past it below the volatile ranges that stores with address sets or `holds`
  write); a load reading until its data says stop keeps its hull. An object is made where
  the loads it serves outnumber its checks; `Program.objects` lists each object's bounds and
  the loads it serves, which the checks (`objchk`) name.
- **Seeds** (`objects::seeds`, static lifts). A pair some pointer access reads through
  (`(zp),Y`, or `(zp,X)` for each value of `X`) that no lifted store may write holds, at
  every entry, one of its *words*: the image's, or, for bytes of `free`, those of init's runs
  (lo and hi paired by run). A greatest fixpoint drops a pair a store may write while every
  remaining pair holds its words (a store through such pairs writes their words plus `Y`).
  An access through seeds alone reaches its words plus `Y`: its address interval narrows to
  their hull and, where they are a few, they are its address set (`Program.addr_sets`; a
  store's are then written, and with no code byte need no `codechk`). A load through a seed
  needs no check: `Program.proven` holds its words plus `Y` where that lies in held bytes.
- **Walks** (`walks.rs`, static lifts). The loads of a `(zp),Y` pair neither an object nor a
  seed serves are read from an *anchor*: an instruction writing `Y` other than `INY`/`DEY`.
  A forward dataflow per pair carries, from each anchor until another or a store that may
  write the pair, the offsets of `Y` from the anchor's (`INY` adds one, `DEY` takes one;
  offsets wider than a byte are none) and a *marker walk*: an `INY` on the edge where the byte
  at `p + Y` was found no marker (after its `LDA`/`LAX`, `N` clear (sign bit) or `Z` clear
  (zero); after a `CMP #k` of it, `Z` clear (`k`) or `C` clear (at least `k`)) leaves every
  byte from the walk's start to `p + Y` short of a marker. An `INY` without the test, or a
  `DEY`, ends the walk; joins hull offsets and keep a marker both paths imply. A load at
  offsets `d` reads the anchor's `p + Y` plus `d`; one walking reads from its start to the
  first marker after it, on the image's bytes and each of init's runs, where no store may
  write them. Where the pair's words are known (a seed's, or `Program.ptr_sets`) and each
  word plus each value `Y` leaves the anchor with reads held bytes on every walk, the loads
  need no check. Otherwise the anchor checks its pointer plus
  `Y` (`objchk`, deopt after it) against an object: from the least start (a data byte of the
  loads' reach bases, or the words, plus `Y`) to the greatest, where every load then ends in
  the data bytes the host holds and no offset carries `Y` past `$FF`. Only walks' starts end
  before the greatest, at the last marker every run holds: a valid run's walk ends at one, so
  no bound below what the reach hulls assume fails a run that keeps them. The anchor's check is
  made where it serves more than one load or a walk.
- **Machine model** (`objects::machine`, static lifts with an entry SP over flat memory
  whose entries are called repeatedly, not `once`). The memory map is fixed: the program,
  other RAM, the I/O pages and the ROMs. Play after init is closed and deterministic and it
  works, so an access whose every base lies in the bytes the host holds of the program (its
  image and the bytes past it its stores write, *Pointer objects*) stays in them on every
  valid run: outside them it would take I/O, ROM or RAM the program never held for its own.
  The rule is one over every load and store of no one address, whatever its addressing mode
  (`Graph::bases`), and the code alone decides it, so song data reaches only the `.h`: along
  the definitions reaching the access, through copies (loads, transfers, stores), the
  sources of its address's bytes (its pointer pair's, the pairs `X` selects for `(zp,X)`, or
  its operand's) are the program's bytes (a table the code indexes, or the held bytes and
  init's runs at the entries), constants (the code's, or the machine's state outside the
  program at the entries), or anything else (arithmetic on them, an entry register, a
  device). An address the program's bytes form, whatever they hold, stays in its block on
  every valid run: a load's the run of data bytes holding every table its high byte is
  copied from, or with no such table the held bytes where no cell (a scalar in the C) lies
  in them; a store's the held bytes, its stores' checks keeping the code it may write. It
  is checked before its instruction against the block (`objchk` of the address itself,
  `def` `"machine"`), always: an address outside the block is invalid input, trapped there.
  An address the code's constants form, its bases in the held bytes (a store *Bounded
  stores* holds to its run of data bytes, which it takes to change no code, that run), is
  proven there (`Program.proven`), with no check where its index (one the code's constants
  and their arithmetic give, else any its index guard passes) keeps it inside. Any other
  source, a constant base outside the held bytes (a device, RAM never loaded) or one of a
  page holding a cell, leaves the access as it was. Zero-page indexing wraps inside zero
  page: its base is no byte of the program and it needs none.
- **Computed jumps** (`dispatch.rs`). A `JMP (ind)` whose pointer bytes are tagged on every
  path into it, a `JMP`/`JSR` whose cell operand bytes are (a byte that is not a cell is a
  constant), and an `RTS`/`RTI` whose return-address bytes are pushed so on every path, goes
  to the words the tagged loads read for each index value inside the tables' extent (*Table
  extents*): two bytes indexed by the register's current value, or by one group, pair value
  by value, a constant pairs with each byte (`RTS` adds 1). A `JMP`/`JSR` whose operand cells
  are not tagged on some path goes to every word their closed values (*Stores*) make, as when
  the operand was written by an earlier call. A `JMP (ind)` through a pointer in the code
  span that no store may write goes to the pointer's word. The bytes read must be ones no
  store with an address set writes, the facts did not see written (a store of unknown
  address is checked against them, `codechk`) and outside the volatile ranges; otherwise, or when a byte is untagged on a
  path or two bytes are indexed by unrelated values, the transfer is unresolved and keeps its
  facts' targets. An index the code does not bound that its register holds as a byte of the
  loaded program (outside page 1 and the volatile ranges), read from an operand plus an index
  register both standing since (no store since may write the byte, no instruction writes the
  register), takes the values that byte may hold at the addresses the register's values
  give: its entry byte and the values of every store the analysis reaches that may write it
  (none of unknown address; a store's index register held as its function's entry value of a
  register plus a constant takes the values that register holds in the calls entering its
  context). A tag whose index register is written while it holds such a
  byte stays indexed by the byte. On the edge to each target, a transfer through tagged bytes
  records which of its targets the path took; a store of a register holding, in every
  context, the byte a transfer the path took is indexed by stores only the index values
  (inside the tables' extent, guarded) whose word is a target the path took: a command
  handler that copies its command into a byte another dispatch reads stores only the commands
  its own table entries name. The words are its targets in every context, an `RTS`/`RTI` resolved so is
  analysed as a jump (its function stays balanced), the bytes read join the program's
  constant bytes, and its table is closed. A `JMP (ind)` or `JMP` whose low and high target
  bytes are columns of one table indexed, in every context, by the current value of one index
  register (`Selector`) dispatches on that register: each index value inside the extent goes to
  its word's target (in an instance function, each copy's targets to the node they enter, when
  every copy selects so and one value enters one node). The analysis also keeps the two
  bytes above a called function's entry (its `JSR`'s return address, as constants) and its pushes less
  pulls since the entry: a pull past the entry reads them, a push past it writes them, so a
  callee that pulls its return address into a pointer, reads the bytes after the call through
  it and pushes the address back advanced past them has an `RTS` at its entry depth through a
  return address the `JSR` did not push. That `RTS` returns, in its caller's context, to the
  address it pulls plus one (a call with inline arguments, returning past them). A load of a
  tracked byte stored on every path since the entry holds what was stored (the pointer's
  bytes loaded back to advance it), and the reads through the pointer, its value known in
  each context, read the argument bytes as constant bytes. While `X` holds the SP a `TSX` took
  (no store into page 1 since), `LDA`/`LDY abs,X` from `$0101`-`$0103` reads the top three
  stack bytes pushed then: a `BRK` handler reads its frame's return address. A `BRK`
  goes to the words its vector `$FFFE`/`$FFFF` may hold: the values the stores with known
  addresses write there (a vector the program sets before it runs `BRK`; any other deopts),
  else the image's bytes, which then are constant: their words are every target, with no
  deopt; it calls the handler in the context of its address plus two, which
  it pushes as constants, so the handler's `RTI` returns there, past the signature byte; a
  routine running such a `BRK` whose handlers' `RTI`s run at their entry height is as
  balanced as one making a call, its `RTS` returning to its own call sites. A
  handler that two or more `BRK`s reach (a procedure) is a function: the `BRK` pushes its
  address, keeps the status as the `CALL`'s return address (with the low byte, what a deopt
  writes), sets I and calls it; the handler's `RTI` at its entry height pops the three bytes
  and returns; the caller restores the flags it pushed and, where the handler may store into
  page 1, checks the address it returned through. Any other handler is inlined. A `JSR` through cells with targets calls a *dispatch
  function* (`Graph::dispatch`), one per target set (keyed by its least site that no call or
  entry enters): the call hands it the operand bytes (registers 6 and 7, never handed back to
  the machine; only the low byte when every site's high byte is the same constant, which
  then stays a constant in the function), and its entry is a `BRANCHIND` through them to the
  targets. The targets are its arms, inlined, unless a `JSR` also calls one directly: that
  one stays a function the arm tail-calls. A branch whose offset
  byte is a cell goes to its fall-through address plus each offset the byte may hold, found
  the same way.
- **Table extents** (`dispatch.rs`). A table indexed by a register reads only its own bytes:
  each column (the loads one dispatch pairs by index value: a split table's low and high
  columns, an interleaved table's two bytes) extends from its byte at the least index value
  to the first byte of an instruction the analysis reaches, the end of the loaded program
  (`loaded`), or the first address past it at which the code starts accessing a base other
  than the table's columns (a constant operand, indexed or not, starts at its least index
  value there); and an index value stops being read when one column's byte reaches its end
  or a byte another column read at an earlier value. A computed transfer's table also ends at
  the first target its words give past its first byte: what it goes to is code, though the
  code only that transfer reaches is not yet decoded when the table is first read. A table's
  columns include every base the code reads with the same
  index value (a table load while its register still holds the index another table byte was
  read with), so storing one byte of an interleaved pair alone sees its partner as part of
  the table. The index values read so are a prefix of the index's values (an index the code
  does not bound has every value its known bits allow), so a split table's low column ends
  where its high column starts and an interleaved table where another object starts. Where
  an index may take a value past the extent, an index guard compares the register with the
  extent's range before each column's load and deopts (the program reads outside its
  table). Guards do not narrow the analysis: they are what the final converged program reads
  past its tables. A store (or read-modify-write, at an instruction of one variant) to a
  constant base plus an index register the code does not bound (no index set, nor a byte of
  the program with known values), whose table's columns start inside `loaded`, writes the
  index values before the first at which some column's byte lies past `loaded` where no store
  with an address set writes; where the index may take a value past them, an index guard
  deopts before the store. Loads are not guarded so: one past `loaded` reads out of bounds,
  and code reads a table through bases offset into it (`tab-1,Y` beside `tab,Y`) and on into
  the code after it. A static lift meets each guarded access's interval, a load's proven hull
  (`Program.proven`) and a store's run (`Program.bounded`) with the addresses the guard's
  range gives.
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
and the return addresses of active `CALL`s when the program elides them (a continuation
function's as its slots hold it), and runs it to the entry's return. An outermost plan
specialized to its entry SP restates SP as that constant past its check, so its stack
addresses are constants the forwarding resolves. `stats["stack_private"]` tells whether stack traffic was removed;
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
may be set the operations stay, tagged `ir::DECIMAL`. After a `PLP` or `RTI` each flag is the
pulled byte's bit where the push is known to have stored it (a `PHP`'s status, a `PHA`'s known
bits, the status a `BRK` frame holds), else either value. When the program has no `SED` variant and no
entry declares `d` set, nothing but a pull from the stack can set the flag: `PLP` and `RTI` are
then checked after the pull (the lifted code deopts if the pulled byte has `D` set) and the flag
is clear everywhere (`vsa::pull_clear`); a pull of a status a `PHP` saved restores the
clear `D` it pushed (status lanes), so its check folds away. `tests/test_decimal*.py` run Clark's
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
cycle counting), the file's carry registers (18-25, `carry.rs`: indices a computed transfer
switches on past their register's write in another function, passed through calls with the
file) and per function its stack slots (26 + `i`: the byte `i` below its entry SP,
printed `s<i>`; a continuation function's two return-address bytes last) followed by its carry
registers (`carry.rs`: per carry class a computed transfer switches on past its register's
write, the register copied before each write of the class; never machine state, as the file's
are not, so a deopt hands none over) and its status lanes (one bit each, never stack bytes). Block 0 of each function is its entry; the executor (`exec.rs`) runs the
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

## Termination

Every iteration of the lift ends on every input; where precision runs out a value is unknown
and the code using it deopts or traps.

- Discovery repeats (`analysis::discover`) only when bytes leave the constant set or a
  transfer joins the unresolved ones: at most once per address. The constant set only
  shrinks (it starts at the loaded bytes, `loaded`), so it reruns at most once per loaded
  byte plus once per transfer. The one run of an entry no input decides (*Init*) is at most
  `once::STEPS` instructions, else it gives nothing. Its rounds
  (`Fixpoint::run`) unite the cells, their values, the resolved targets and the stores'
  address sets with the last round's; a store a round finds no set for, or no longer has, is
  dropped for good. All are finite sets of addresses and bytes.
- The interval analysis (`vsa::fixpoint`) has finitely many keys (start, call context). At
  loop heads, the targets of depth-first back edges over every control edge (a return's to
  its sites included, so every cycle of keys passes one), an interval rises only to the next
  program constant; the rest of a state (tags, known bits, value sets, stack bytes, depth,
  tracked bytes) is of finite height: a register's value set is at most 64 of the 256 byte
  values (more: its interval), a pointer pair's words at most 64 of its seeds' words (more:
  dropped), each with a subset of the 256 `Y` values, and joins unite them or drop them.
  The abstract device state is finite: the tracked bytes of the model's registers, intervals
  of bytes like any tracked byte; a read's set is a function of them.
  Balance is refined while fewer returns are wild. The analysis runs at most twice per
  round, the second time with the seeds the first found, which it does not change.
- Cell values (`dispatch::cell_values`) are a least fixpoint of byte-set unions. A store whose
  address is unknown only through its own operand cells may write those cells unknown values,
  which leave it unresolved and writing nowhere known, so the values it is resolved with would
  alternate; each round of `dispatch::cell_addressed` joins them with the last round's instead
  (a missing cell holds its entry byte, below any set; unknown absorbs), so they only rise and
  the rounds end at values that hold of the stores resolved with them.
- Continuation functions and the stack bytes the interval analysis tracks (pushes, the bytes
  above the entry, a `TSX`'s snapshot of the top three) add no iteration: the return
  addresses are per context, whose keys are finite, and the rest is of finite height.
- Carry classes only unite, so the fixpoint reruns (`vsa::analyze`) at most once per pair of
  write groups joins find to meet, ending when no join meets two classes. Carries
  (`carry::alloc`) add no iteration: per function one walk of its callees and per carried
  transfer one of its nodes, each visited once.
- Pushed values are of finite height (intervals widen at loop heads as registers do), and
  the implied guards are one forward pass over each function's blocks (a union of byte
  sets per register: each block is revisited only when its entry facts grow); the guards the
  proof pass leaves out are read once off its registers.
- Pointer reach (`vsa::reach`): byte bounds only descend (each round's meet with the last),
  word columns and floors only rise, to the end of the run of data bytes they move in or the
  extremes, so a pointer every call advances rises once per run; the callees' store
  intervals are recomputed until they repeat.
- Pointer objects (`objects::objects`): per pair a reaching-definitions fixpoint of bit sets
  over its definitions (unions only rise), run twice, the second with the loads the first
  found an entry reaching.
- Seeds (`objects::seeds`): the pairs only shrink, by at least one a round.
- Walks (`walks::walks`): per pair and instruction an anchor, offsets whose hull only widens
  (to none past a byte's width) and a marker only weakening (to none); an anchor or a store
  of the pair restarts it.

`tests/test_termination.py` lifts such programs under a wall-clock limit and replays them.
`tools/liftsweep.py` translates PSID files to C, each under a limit, and replays the C: per file
the time, whether it replays exactly, deopts or differs, and the calls the reference returns
from.

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
  `matches` holds: its constant bytes, every closed opcode cell's value in its set and every
  byte of `Program.kept` inside its bound (the guards the proof pass leaves out rely on it),
  which the reference may have changed running code the lift does not contain.
  After a deopt the reference may have stored into constant code, so the next call compares it
  with the lifted bytes first and runs on the reference while they differ.
- A start runs from one backing: one executed from ROM in some configurations and from RAM in
  others is planned only where it is fetched as decoded (the rest deopt). `lift_many` is flat.
