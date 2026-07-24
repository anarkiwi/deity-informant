# State-machine kernel lift

`deity_informant.kernel` lifts a :class:`Recording` one altitude higher: from `N`
per-frame, entry-relative templates to **one** compact canonical model of the
tune. A SID player is a state machine over a small persistent vector; this lift
recovers it as `(tables, S0, variants)` and proves the recovery by re-iterating
from `S0` alone.

Module: `deity_informant/kernel.py` (`lift_kernel`, `Kernel`, `Variant`). It
consumes only the recorder's public artifacts — no lifter/recorder/VM changes.

## API

```python
from deity_informant import record, run_sub, lift_kernel

rec  = record(mem, run_sub, entry, outputs, invocations)
kern = lift_kernel(rec)

kern.tables      # {addr: byte}   constant seed cells read at a constant address
kern.state       # {addr, ...}    persistent cells carried across frames
kern.s0          # {addr: byte}   initial state (frame-0 entry restricted to `state`)
kern.inputs      # {'regs_constant': bool, 'env_reads': bool}
kern.variants    # [Variant] the K distinct control paths
kern.verify()                 # (ok, divergence) — Tier A: closed-loop re-iteration
kern.verify(self_driving=True)# (ok, divergence) — Tier B: guards drive the machine
kern.pretty()                 # textual canonical dump
```

Each `Variant` carries `frames` (the frame indices it covers), `guard`
(the discriminating branch/target/opcode folds), `transition` (`{addr: expr(S,T)}`
for state cells), and `sslog` (`[(addr_expr, value_expr, sz, is_output)]`, the
ordered stores with symbolic addresses).

## Memory partition

Every recorder artifact is entry-relative. A `("mem", addr, sz)` leaf is a
**cross-frame read** (within-frame reads are `cur`), so the read-set is the union
of `mem`-leaf addresses over `F`/`slog`/`facts`. With `written` = the union of all
`slog` store addresses:

- **`state` (`S`)** `= mem_leaves ∩ written` — cells whose previous-frame value is
  consumed (song position, counters, per-voice envelope state).
- **`tables` (`T`)** `= mem_leaves \ written` — constant seed data, valued from the
  frame-0 entry image.

This is exact for the recorded window (the recorder's mutable set is
observational, not an over-approximation). Tables addressed by a **computed**
index (e.g. a wavetable at `$1400 + phase`) are read but not constant-addressed, so
they are not enumerated in `tables`; they surface in the transition/output
expressions as `M[...]` reads into the (constant) image.

## Templates → variants

The recorder already dedups frames by a path signature, but that signature keys on
concrete effective addresses, so a loop whose store *address* is a function of
state (SMC operand walking `$D40B, $D40C, …`) is split into one template per frame.
The kernel regroups by **structural expression equality**, abstracting exactly the
state-dependent placement addresses (the `place` folds where `site == observed ==
addr`). Two frames then share a variant iff they took the same control path; their
differing data addresses are recovered symbolically from the placement fold's
expression. Folds common to *every* variant are control-flow-invariant boilerplate
(the driver's `rts` return, a fully-unrolled `DEX/BPL` loop) and are dropped, so a
variant's `guard` is its true branch structure.

Result: `smc_operand` → 1 variant (`S'[op]=(S[op]+1)&$FF`, `OUT[operand-word]`),
`dec_timer` → 2 variants split by the down-counter's branch outcome.

## Verification — closed-loop re-iteration

`verify` seeds an image from `S0` (frame-0 entry) and **carries it forward with no
per-frame snapshot**, so each frame's start state equals the prior frame's end —
the state-machine closure property, proven rather than supplied.

- **Tier A** (default) drives with the recorded per-frame templates and asserts
  both closure (`image == entry[i]`) and byte-exact outputs (`== replay(i)`). This
  is the soundness gate: any divergence means a hidden input leaks across frames
  outside `S`.
- **Tier B** (`self_driving=True`) selects each frame's variant by evaluating its
  entry-pure guard folds on the current state, then applies that variant's symbolic
  store list (evaluating each store address per frame). It proves the guards alone
  drive the machine. Best-effort: a miss returns `(False, ("no-variant", i))`
  rather than proving the model wrong.

`divergence` is `("closure"|"output"|"no-variant", frame_index)`.

The environment inputs a real player legitimately reads — volatile CIA/VIC timer
and raster registers (`uni` leaves) and per-frame entry registers — are declared in
`inputs` and fed from the recording; they are not part of `S`.

## Example — the `HELLO, WORLD!` demo

```
tables: 15 cells
state:  $100A
S0:     $100A=$00
variant 0  (1 frames):
  S[$100A]' = (S[$100A] + $D)
  OUT[(INT_ZEXT(S[$100A]) | INT_LEFT(INT_ZEXT($4), $8))] = ($F7 ^ $FF)
  ... 12 more screen-code writes at $0400 + S[$100A] + k ...
```

The `ISC`-patched `STA` operand `$100A` is the one state cell; each write lands at
`$0400 + S`, value `data ^ $FF` (the `ISC` `SBC` decode). `verify()` and
`verify(self_driving=True)` are both byte-exact.

## Acceptance tests

`tests/test_kernel.py` runs over the `_fuzzgen` corpus + `hello_world`: Tier A and
Tier B byte-exact for every player; partition (`table_index` all-tables/no-state,
`smc_operand`/`dcp_isc` state cells); dedup (`smc_operand` → 1, `dec_timer` → 2,
`K ≤ frames`); the closed-form increment transition; state-dependent output
address; determinism; leaf rendering; and closure/`no-variant` divergence
reporting.
