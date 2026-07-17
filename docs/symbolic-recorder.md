# Symbolic window recorder

A third product beside the lifter and the concrete VM: a *recording decompiler*
for repeated invocations of 6510 code. `record` executes each invocation
bit-identically to `PcodeVM` and simultaneously produces an entry-relative
symbolic model whose replay reproduces the machine's observable writes
byte-exactly, in order. Soundness is checked at record time.

Modules: `deity_informant/expr.py` (expression algebra) and
`deity_informant/recorder.py` (`record`, `Recording`, `RecVM`).

## API

```python
from deity_informant import record, run_sub, lift

rec = record(mem_or_vm, run_sub, entry, outputs=range(0x0400, 0x040D),
             invocations=N, lifter=lift, assertion=True)

rec.F[i]        # {addr: (entry_pure_expr, sz)} end-of-invocation values
rec.facts[i]    # [(site, kind, expr, observed)] control-flow / placement folds
rec.slog[i]     # [(pos, addr, expr, sz)] position-attributed store log
rec.out_seq[i]  # [(addr, expr)] ordered writes to `outputs`
rec.entry[i]    # (entry_mem, entry_reg) snapshot
rec.replay(i)   # reconstruct the observable write sequence from slog + entry
```

`driver` is any VM driver called as `driver(vm, entry, cache, lifter)`
(`run_sub`, or a closure adapting `run_irq` / `run_irq_driven`). `outputs` is the
observable address set; the recorder is device-agnostic.

## Soundness invariant

The recorder is a per-invocation **partial evaluation of the 6510 interpreter**:
control path and access addresses are specialised to concrete values; data flow
is residualised over the entry state.

- **Rule 1 (folds).** Every concrete value folded into the specialisation that
  depends on *mutable* state (any byte written during any recorded invocation)
  is justified by either *residualisation* (replaced by an entry-pure
  expression) or a *case fact* `expr(entry) == folded_value`. This covers opcode
  identity at a mutated pc, operand bytes (including derived constants — `indy`'s
  `(lo+1) mod 256` pointer-high, the 16-bit `abs` word), branch/`jmp`/`jsr`
  targets, `jmpind` vectors, `rts`/`rti` return targets, and **placement** (a
  load/store whose address may land on a cell written earlier records
  `addr_expr == addr`). Store placement is per-frame conditional on the concrete
  alias; a **computed load** whose address aliases a written cell on any frame is
  flagged (by `(pc, op_index)`) in the pre-pass and records a placement fact on
  **every** frame, keyed on the load `pc`, with the frame's concrete address as
  the case constant — the fact's presence is site-stable while its constant
  distinguishes which cell was read. This keeps a downstream walk model's guard
  path deterministic regardless of whether a given frame actually aliases.
- **Rule 2 (mutations).** Every mutation appears in the store log and `F`,
  including stack traffic performed outside P-Code (`jsr`/`brk` pushes,
  `rts`/`rti` pulls, the status byte, driver sentinel pushes). Flags restored by
  `rti` are residualised into the symbolic flag registers, not left stale.

"Mutable" is instantiated observationally: a concrete pre-pass over the same
invocations collects the exact written-address set. Because the pre-pass and the
recording pass execute byte-identical deterministic traces, the set is exact for
the recorded window, not an approximation.

## Lifter provenance metadata

`lift` returns two extra keys (inert for other users; `ops`/`len`/`cyc`/`pen`/
`ctrl` are unchanged):

- `prov`: `{"op0": opcode, "ops": {(op_index, arg_index): (srcs, fn)}, "ctrl":
  (srcs, fn, value) | None}`. `srcs` are instruction-byte offsets and `fn` names
  the derivation (`id`, `hi1` = `(byte+1) mod 256`, `word` = 16-bit compose,
  `rel` = branch target). The recorder residualises each op-list const whose
  source bytes are mutable, and case-facts the opcode identity and control
  target.
- `stk`: `"jsr"`/`"brk"`/`"rts"`/`"rti"`/`None` — the stack traffic the record's
  `ctrl` moves outside its op list.

## Expression algebra

Nodes (tuples): `("const", v, sz)`, `("reg", i)` (entry register, width 1),
`("uni", n, sz)` (opaque — volatile reads / unmodelled), `("mem", addr, sz)`
(entry-image read), `("cur", addr, sz, ver, fb)` (evolving-image read carrying
the cell's load-time store-version and its entry-pure fallback), and
`("op", MN, kids, sz)` over the P-Code mnemonics the VM interprets. The
associative ops `INT_ADD`/`INT_OR`/`INT_AND`/`INT_XOR` are **flat**: one node
with `N >= 2` operands. An accumulator (`acc = acc op x` over an unrolled loop)
is therefore a single depth-`~2` node, never an `N`-deep chain, so every
traversal (`simplify`, `to_entry`, `to_evolved`, `evaluate`) recurses only to an
expression's logical depth and never needs a raised recursion limit.

**Evolved-state templates are required, not optional.** Substituting a
producer's whole entry-pure composition into every downstream consumer mints a
distinct expression per invocation context. Instead the recorder emits a `cur`
leaf that reads the evolving image at its own log position. At emission, a `cur`
leaf whose cell was re-stored since the load (`ver` mismatch) fails validation
and falls back to the entry-pure producer composition. Fact identity keys on the
`(entry-form, evolved-form)` pair, so a stale placement is a distinct recorded
event rather than a global demotion of the site.

Both forms are part of the contract: `F` carries entry-pure expressions (a
per-invocation transition function); facts and stores carry evolved forms
(position-faithful templates).

### Simplification laws

- **Width law:** a narrower arithmetic node wraps at its own width and stays
  whole when spliced into a wider parent (only same-width same-op children flatten
  in). This keeps an index register's `mod 256` wrap correct inside a 16-bit
  address.
- **Subtraction law:** `INT_SUB` by a constant folds into the flat sum as its
  same-width two's complement, so nested `jsr`/`rts` stack arithmetic canonicalises
  to a single `SP + k` node — bounded in nesting depth, not linear.
- **Flat associative folding:** same-op same-width operands splice into one node
  and constants fold; `simplify` is identity-memoised so an already-canonical
  child is not re-descended (accumulation is O(1) per step, not O(N²)).

### Complexity guard

`simplify` tracks each result's depth and raises `ExprTooComplex` once it exceeds
`expr.MAX_DEPTH` (256). Flat folding keeps genuine playroutine arithmetic far
below that, so the guard fires only on a runaway — e.g. a mis-driven interrupt
executing uninitialised RAM — converting an unbounded walk into a clean,
catchable skip instead of a hang.

Lifter invariants the algebra relies on: `LOAD`/`STORE` are 1-byte; widening is
only via `INT_ZEXT`.

## Evaluation semantics (normative)

Used by the record-time assertion and `replay`: stores from the log apply
one-by-one in recorded order; a `mem` leaf always reads the invocation-entry
snapshot; a `cur` leaf reads the evolving image as of its own log position;
register leaves read entry registers; `uni` leaves read the concrete value
observed at record time. This machine-order rule makes `cur` exact.

## Record-time assertion

After each invocation, before returning artifacts, every recorded fact and store
is evaluated against the entry snapshot (and, for `cur` nodes, the evolving image
at its position): predicate == taken/value, store expr == byte written, `F[a]` ==
end-of-invocation byte. On by default (`assertion=True`); it converts every
expression-domain bug (width, staleness, bad simplification) into a loud failure
at record time. Pass `assertion=False` for production runs.

## Acceptance tests

`tests/test_recorder.py` covers: `hello_world` full run replayed byte-exact; one
program per fold channel (SMC opcode/operand `zp`/`abs`/`indy` incl. the derived
`+1`, SMC branch target, `jmpind` rewrite, push/push/`rts` dispatch, load- and
store-placement alias, unconditional load placement at a flagged alias site,
`brk`, mid-invocation `rti` with modified status); the
`abs,Y` mod-256 width wrap; pointer-walk template stability; stale-`cur`
fallback; bounded stack-tower node count; determinism; and concrete fidelity
(`wlog` and end memory bit-identical to a plain `PcodeVM` run).
