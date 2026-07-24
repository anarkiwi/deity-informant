# Canonical IR

`deity_informant.canonical` is the tune's **canonical form**: a parseable
S-expression program that **round-trips** — serialize a kernel, parse the text
back, execute it, and reproduce the original SID register-write stream byte-exact.

Module: `deity_informant/canonical.py` (`to_ir`, `parse_ir`, `Program`,
`roundtrip`, `GRAMMAR`).

```python
from deity_informant import lift_kernel, record, run_sub, to_ir, parse_ir, roundtrip

kern = lift_kernel(record(mem, run_sub, play, range(0xD400, 0xD419), frames))
text = to_ir(kern, "my_tune")        # canonical program (string)
prog = parse_ir(text)                # parse back
writes = prog.run(frames)            # execute -> [(addr, value), ...]
ok, _ = roundtrip(kern)              # byte-exact == the recorded writes
```

## Why a decision tree

The kernel records each frame as one of `K` variant traces. Selecting the right
variant to self-drive a frame cannot be done by entry-state guards (real
playroutines branch mid-frame) nor by testing each trace for self-consistency
(several traces are self-consistent on a given state). The sound construction is
to **merge the variant event streams into a per-frame decision tree**: traces
share a common prefix and split at each guard by its observed value. Executing the
tree — evaluating each guard on the evolving image and following the matching
branch — follows the real control-flow path. This is also the control-flow
reconstruction the readable views want.

## Grammar

`canonical.GRAMMAR` is the normative grammar; all integers are hexadecimal.

```
program := '(tune' outputs tables init '(frame' node* '))'
outputs := '(outputs' (lo hi)* ')'              ; observable address ranges
tables  := '(tables' '(t' addr byte* ')'* ')'   ; constant data the frame reads
init    := '(init' '(s' addr byte ')'* ')'      ; initial state S0
node    := '(w' addr expr size ')'              ; store value at a fixed address
         | '(sw' expr '(case' val node* ')'* ')'; branch on a predicate's value
expr    := '(c' val size ')' | '(r' idx ')' | '(u' n size ')'
         | '(m' expr size ')'                    ; entry-snapshot load
         | '(v' expr size ')'                    ; evolving-image load
         | '(o' MNEMONIC size expr* ')'          ; P-code operation
```

`expr` is a faithful concrete syntax for the recorder's expression algebra —
`(m …)` is a frozen frame-entry read, `(v …)` reads the evolving image (so a value
stored earlier in the frame is seen), and `(o …)` is a P-Code op the VM interprets.
`parse_ir` rebuilds the exact algebra tuples, so `E.evaluate` drives the program.

## Execution semantics

`Program.run(n)` seeds a 64 KiB image from `tables` + `init` and, per frame, freezes
the entry snapshot and walks the `frame` tree: a `(w …)` evaluates its value against
the frozen entry (`m`) and evolving image (`v`) and writes it (emitting it if the
address is in `outputs`); a `(sw …)` evaluates the predicate and recurses into the
matching `(case …)`. The image carries forward between frames — that carry is the
tune's state machine.

The `tables` section is exactly the set of addresses the executor reads, computed
by running the tree once and logging every load (so a residualised read of a table
gap is still seeded). Register/volatile inputs are the frame-entry environment; for
the tested tunes they do not affect output, so the program is **self-contained**
(`roundtrip(kern, self_contained=True)`).

## Round-trip guarantee

`roundtrip` proves `parse_ir(to_ir(kern)).run(N)` equals the recorded write stream.
Verified byte-exact across the synthetic `_fuzzgen` corpus and full-length real
HVSC playroutines. Because `parse_ir ∘ _emit` is identity on the expression tuples
and the executor is the same evaluator the recorder validates against, the IR is a
lossless canonical form, not a lossy rendering.
