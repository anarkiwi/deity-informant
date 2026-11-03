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
| `facts` | `trace.Facts`, used only for computed-jump targets (below) |
| `loaded` | the `[lo, hi)` bytes the program was loaded to (default: its code span) |
| `header` | the name the `.c` includes |

`info` holds the stage times, the regions `[(origin, length)]`, the code span, the traps in
the `.c` (`{k: ("deopt", pc)}` or `("ind", pc, ...)` for a computed jump with no target), the
fact edges used, the lift's `stats` and whether some access has an unknown address.

The host supplies `uint8_t io_read(uint16_t)`, `void io_write(uint16_t, uint8_t)` and
`void trap(uint16_t k)`, which must not return (`longjmp`, or abort the call). Each entry
starts as `wholeprog`'s `Program.call` does: `X = Y = 0`, flags clear, `SP = $FD` below the
caller's return address. `tools/cgenplay.c` is a host harness.

## Pipeline

1. **Lift.** `wholeprog.lift(image, entries, volatile, facts)`, static: no facts but the
   targets of computed jumps. Functions are named by ascending entry PC, deopts become
   `trap(k)` with `k` numbered in the order the lift's functions and blocks meet them.
2. **MLIR** (`emit.py`). Each lifted function is a private `func.func` taking and returning the
   register file as values; its registers, stack slots and block-crossing temporaries are
   rank-0 `memref.alloca`s, memory one global `@M`, volatile accesses `io_read`/`io_write`
   calls. Each entry is a public function calling its body with the entry registers.
3. **Structure** (`mlir.py`). Upstream passes only: `canonicalize`, `mem2reg`, `inline`,
   `symbol-dce`, `cse`, `sccp`, `lift-cf-to-scf`, printed in generic form. Callees inline;
   a recursive one stays a function returning `struct cgen_regs`.
4. **Print** (`printer.py`, `regions.py`). The generic text is parsed for the operations the
   pipeline leaves (`func`, `scf.if`/`while`/`index_switch`, `arith`, `memref`, `ub.poison`;
   anything else raises), memory is partitioned, and the C is printed and run through
   `clang-format --style=LLVM`.

The printer reads `mlir-opt --mlir-print-op-generic` output: the image the tools come from
(`docker/mlir.Dockerfile`, apt.llvm.org LLVM 22) has no Python bindings, the bindings are not
published as a pinned wheel for that release, and the generic form is one regular syntax for
every operation, so a small line parser replaces a native dependency.

## Printing

Expressions carry their exact range under C's integer promotions: a `(uint8_t)` cast appears
only where a wider value would change a comparison, shift or subscript, and an operand is made
unsigned only where 16-bit `int` (llvm-mos) arithmetic could overflow. A value used once is
printed at its use (a memory read only when nothing between may write), as is a cast of or
constant offset from a variable; others become `uint8_t`/`uint16_t` variables. A loop's next
value is assigned in place when the old one is dead; `lift-cf-to-scf`'s `i32` selectors are
`uint8_t`. `scf.while` prints as `do`/`while`, `while` or `for (;;)` with `break`.

## Memory

- **Constant code** is the bytes of the planned instructions that no constant-address store
  writes. It is the `.h`'s `CODE` (the span from the first such byte to the last).
- **Data regions** `R0, R1, ...` (ascending address) are the maximal runs of other bytes that
  some access may touch, outside volatile ranges. An access's interval comes from its address
  expression (constants, an 8-bit index adds `[0, 255]`, masks, shifts, the hull of `scf`
  results, a loop-carried value that keeps its initial interval); wider than `$200` is unknown.
  An unknown access may touch every byte of `loaded`, so then every non-code byte of it is in a
  region. Zero page, the stack page and RAM outside the image are regions when accessed.
- An access whose interval lies in one region is `Rk[off + index]`, `off` relative to the
  region. One in volatile memory is `io_read`/`io_write`. One wholly in constant code is
  `oob_rd`/`oob_wr`. Any other goes through `rd(a)`/`wr(a, v)`: the regions (by the `Rk_ORG`
  symbols, so data pointers holding original addresses resolve), the volatile ranges, then
  `oob_rd`/`oob_wr`.
- `oob_rd` returns the original byte of `CODE` (0 outside it), `oob_wr` discards; defining
  `CGEN_OOB` replaces both with the host's. A store the lift found may hit constant code is
  followed by `codechk`, which traps when the byte would change.

Address constants print relative to the region, code span or loaded image holding them
(`R2_ORG + 0x10`, `CODE_ORG + 0x21`, `IMG_ORG + ...`): a constant reaching an access address
through additions, casts, selects and `scf` values, the cases of a computed-jump switch, and a
pair of constant byte stores into a pointer pair (the zero-page pointer of a `(zp),Y`/`(zp,X)`,
a `JMP (ind)` vector, a three-byte operand, or two stack bytes, as a `JSR`'s return address
when the stack is kept in memory) whose 16-bit value lies in the program: `LDA #<tab` then
prints `(uint8_t)(R3_ORG + 4)`. Not covered: an address half stored without its partner in the
same block, or used other than as an address (compared, added to data).

## Traps and computed jumps

A static lift has no targets for `JMP (ind)`, `RTS`/`RTI` used as jumps or transfers through
written operands; each becomes a switch whose default traps. `facts` supplies targets:
`jump_facts` keeps only the edges leaving those instructions. The switch cases are then code
addresses (`case CODE_ORG + 0x23:`) and the `.c` depends on the run that made the facts, which
`info["facts_edges"]` reports. Other traps: a deopt the lift keeps (an opcode cell holding a
value outside its statically stored set, a store into constant code that changes it, a guard
it could not prove). Indexed stores into code operands are not cells in a static lift, so a
player clearing its own operands with `STA abs,X` traps there.

## Validation

`tests/test_cgen.py` compiles each output with gcc (`-std=c99 -Wall -Wextra -Werror`) against
`tools/cgenplay.c` and compares the volatile writes (and sensitive reads) of many consecutive
calls, memory persisting, with `wholeprog.reference_call`; it checks relocation and data
invariance, the absence of `goto` and an llvm-mos build. `tools/cgenplay.py` does the same for
a PSID or raw image and prints `tools/cmetrics.py`'s table, the sizes and the stage times.
