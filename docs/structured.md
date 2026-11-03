# Lifted programs as C and structured C

`wholeprog.lift` results (`Program.functions`, see [the IR](wholeprog.md#ir)) print as C or as MLIR, and MLIR's own `lift-cf-to-scf` removes the gotos.

| Tool | Does |
|---|---|
| `tools/pcode2c.py FUNCS.pkl --entry PC [--layout MAP.json]` | C with gotos: registers are globals, memory is `M[]`, layout regions name accesses |
| `tools/pcode2mlir.py FUNCS.pkl --entry PC` | MLIR: a `func.func` per function over `cf` and `arith`, no layout names |
| `tools/structure.py IN.mlir` | `mlir-opt` and `mlir-translate`: structured C |
| `tools/cslice.py IN.c` | Frama-C slice on bus writes, deopts and writes to `M` |
| `tools/cplay.py SRC.c --state IMAGE --meta META` | plays C from `A = X = Y = 0`, `SP = $FD` against `wholeprog.reference_call`: the bus writes must be equal (`--ram`: RAM too) |
| `tools/cmetrics.py A.c ...` | lines, statements, `if`, `goto`, `switch`, loops, `int32_t` variables |

`FUNCS.pkl` is a pickle of `prog.functions`; the C expects `M`, `io_read`, `io_write`, `deopt` and `codechk` from the host (`tools/cplay.c` is one).

## Semantics

Both printers map every op exactly, as the replay against the reference requires:

- a 16-bit `LOAD`/`STORE` is a little-endian word; every result is cut to its varnode width;
- a shift by the operand's width or more is 0;
- `codechk(addr, value)` is a value-producing `CALLOTHER`; `deopt(pc)` reports and returns;
- a `LOAD` with no output is kept only when volatile, guarded by its second input;
- volatile accesses are `io_read`/`io_write` calls; `BRANCHIND` dispatches on its function's table, else deopts.

In MLIR, `M` is `memref.global @M : memref<65536xi8>`, a register or stack slot a `memref<1xiN>` global, and a temporary used outside its block a rank-0 `memref.alloca` (promoted by `--mem2reg`).

## Structuring

`structure.py` runs, from `PATH` or the `di-mlir` image (`docker/mlir.Dockerfile`, apt.llvm.org LLVM 22, built on first use):

```
mlir-opt --canonicalize --cse --mem2reg --lift-cf-to-scf --canonicalize --cse
mlir-opt --canonicalize --cse --convert-to-emitc --reconcile-unrealized-casts --form-expressions
mlir-translate --mlir-to-cpp
```

Between the two `mlir-opt` runs `ub.poison` (lifting's value for an untaken path) becomes zero; after `mlir-translate` a header with the standard includes and a prototype per function is added and `extern` dropped from the register definitions. LLVM 20 lacks `--convert-to-emitc` and the `scf.while` lowering.

The output has no `goto`. Blocks several paths join are structured by threading an `int32_t` selector through the arms and a `switch` on it; irreducible regions become `do { } while (flag)`. EmitC prints signless integers as signed with unsigned casts per operation, and one-cell globals as arrays.

## Tests

`tests/test_pcode2mlir.py` lifts small programs of `tests/wholeprog_progs.py` and plays the C and the structured C against the reference, checks every op in both printers on edge values, and slices. The MLIR step uses `mlir-opt` from `PATH` or builds the `di-mlir` image on first use; the slice pulls `framac/frama-c:29.0`. Tests needing them skip where Docker is absent; CI's test job has Docker, so it runs them there (the first test builds the image, under a lock shared by the xdist workers).
