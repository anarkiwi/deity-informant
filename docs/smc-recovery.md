# SMC recovery pipeline

How `deity_informant.record` recovers a sound, entry-relative model of a 6510
routine **that rewrites its own code and data while it runs**. This is the
operational walk-through of the symbolic window recorder; the normative
node/evaluation contract lives in [symbolic-recorder.md](symbolic-recorder.md).

Modules: `deity_informant/recorder.py` (`record`, `Recording`, `RecVM`) and
`deity_informant/expr.py` (the expression algebra).

## The problem: self-modifying code defeats a static lift

A static disassembler lifts each address once. Real C64 playroutines rewrite
their own operands, opcodes, and jump vectors between frames, so "the instruction
at `$1009`" is not one instruction — it is a different one every pass.

Take the `HELLO, WORLD!` demo (`examples/hello_world.py`). The `ISC $100A` at
`$100C` increments the **low operand byte of the `STA` at `$1009`** every
iteration, so the store target walks `$0400, $0401, … $040C`:

```
  $1009  8D [00] 04     STA $0400        ; operand byte lives at $100A
               ▲
               └──────── mutated every pass by ↓
  $100C  EF 0A 10        ISC $100A   →   INC $100A   (self-modification)

  pass 0: STA $0400   pass 1: STA $0401   …   pass 12: STA $040C
```

A single lift of `$1009` bakes in `$0400` and is wrong for passes 1..12. The
recorder instead treats the operand byte as *data read from a mutable cell* and
recovers the dependence explicitly, so replay reproduces all 13 stores.

## The pipeline at a glance

`record` is two concrete passes over the same driver, plus a replay:

```
                     ┌───────────────────────────────────────────────┐
  mem / vm  ───────▶ │  record(mem, driver, entry, outputs, N, …)     │
  entry, outputs     └───────────────────────────────────────────────┘
                                        │
          ┌──────────────────────────────┴──────────────────────────────┐
          ▼                                                              ▼
 ┌───────────────────────────┐                       ┌───────────────────────────┐
 │ PASS 1  concrete pre-pass  │   mutable set         │ PASS 2  recording pass     │
 │ collect=True  emit=False   │   sig[0..N-1]         │ emit per distinct sig      │
 │                            │   alias_sites         │                            │
 │ run driver ×N :            │ ────────────────────▶ │ run driver ×N :            │
 │  • execute bit-exact       │                       │  • execute bit-exact       │
 │  • mark every written cell │                       │  • residualise / case-fact │
 │  • hash path → sig[i]      │                       │  • log stores, F, facts    │
 │  • flag aliasing loads     │                       │  • assert at record time   │
 └───────────────────────────┘                       └───────────────────────────┘
                                                                 │
                                                                 ▼
                                        ┌────────────────────────────────────────┐
                                        │ Recording                              │
                                        │   F[i]       entry-pure end values     │
                                        │   facts[i]   control / placement folds │
                                        │   slog[i]    ordered store templates   │
                                        │   out_seq[i] ordered writes to outputs │
                                        │   entry[i]   (mem, reg) snapshot       │
                                        │   replay(i)  → observable write stream │
                                        └────────────────────────────────────────┘
```

`RecVM` subclasses `PcodeVM`, so **any** driver (`run_sub`, `run_irq`,
`run_irq_driven`, or a closure) runs unchanged and bit-identically on both passes.

### Pass 1 — observe what is mutable and which path each frame took

The recorder is a *partial evaluation of the 6510 interpreter*: the control path
and access addresses are specialised to their concrete values while data flow is
left symbolic. Pass 1 fixes the two things Pass 2 specialises against:

```
  for frame i in 0..N-1:
      execute the driver concretely (identical to PcodeVM)
      STORE addr  ─▶  mutable |= {addr}              (every self-written cell)
      LOAD  addr  ─▶  if addr already written this frame and sz==1 and non-volatile:
                          alias_sites |= {(pc, op_index)}   (a computed load that aliases)
      _mix(pc, opcode-bytes, every load/store effective address)  ─▶  sig[i]
```

`mutable` is **observational, not an over-approximation**: because the two passes
run byte-identical deterministic traces, the written-cell set is exact for the
recorded window. `sig[i]` is a 64-bit FNV fold of the frame's whole path
(executed instruction identities + every effective address), so two frames with
equal `sig` residualise to the byte-identical template.

### Pass 2 — residualise data, case-fact control

Pass 2 replays each frame and, for the **first frame of each distinct
signature**, builds the symbolic artifacts. Every concrete quantity that is
folded into the specialised trace and depends on a mutable cell must be
*justified* — otherwise the model would silently bake in this frame's bytes.
There are exactly two justifications:

```
   concrete quantity Q folded from a cell in `mutable`
                       │
          ┌────────────┴─────────────┐
          ▼                           ▼
     Q is DATA                    Q is CONTROL
   (operand byte, pointer,       (opcode identity at a mutated pc,
    loaded/stored value)          branch/jmp/jsr/rts/rti/brk/jmpind target)
          │                           │
          ▼                           ▼
     RESIDUALISE                  CASE-FACT
   replace Q with an             record   expr(entry) == Q
   entry-pure expression         the fact is site-stable; its constant
   (mem / cur / reg / op)        distinguishes which case fired this frame

   (a quantity NOT derived from a mutable cell stays a plain constant.)
```

Applied to the `STA` operand from the demo: `$100A ∈ mutable`, and the operand is
data, so the store address residualises to the entry-pure expression reading
`$100A` — evolving as an SMC-faithful `cur` leaf (below). No baked-in `$0400`.

Rule 2 (mutations) is the dual: **every** write reaches `slog` and `F`, including
stack traffic done outside the P-Code op list — `jsr`/`brk` pushes, `rts`/`rti`
pulls, the pushed status byte, and driver sentinel pushes.

## Entry-pure vs evolved (`cur`), and replay's machine order

A residualised leaf comes in two forms so a producer's value is not re-inlined
into every consumer (which would mint a fresh expression per context):

```
   mem[a]   ─ reads the FROZEN invocation-entry snapshot
   cur[a,ver,fb] ─ reads the EVOLVING image at this log position,
                   carrying the load-time store version `ver` and an
                   entry-pure fallback `fb`
```

`replay(i)` (and the record-time assertion) evaluate the store log in **recorded
machine order** against an image that starts as the entry snapshot and advances
store-by-store:

```
   image := copy(entry_mem[i])
   for (pos, addr, expr, sz) in slog[i]:                 # in order
       v = evaluate(expr, entry_mem, entry_reg, image, uni)
                     │            │            │
                     │            │            └─ cur[a] reads image (post-writes so far)
                     │            └────────────── mem[a] reads the frozen entry snapshot
                     └─────────────────────────── reg / uni / const / op
       image[addr .. addr+sz-1] := v
       if addr in outputs: emit (addr, v)
```

This machine-order rule is what makes `cur` exact under SMC: a byte read after an
earlier store in the same frame sees the stored value. If a `cur` cell was
re-stored since the load (its `ver` no longer matches), it **demotes to its
entry-pure fallback** at emit time, so a stale read is a distinct recorded event,
never a wrong value.

## Signature memoization — work bounded by path vocabulary

Looping players retrace very few distinct paths. Pass 2 exploits this: the
symbolic template is a pure function of the concrete path, so it is built once per
signature and reused.

```
   sig:      A   A   A   B   A   B   B   …        (per-frame path hash from Pass 1)
             │   │   │   │   │   │   │
   build ────●   ·   ·   ●   ·   ·   ·            ● = emit=True  (build template)
   reuse         └───┴───────┘   └───┴──          · = emit=False (execute only, reuse)
```

Repeat frames still execute concretely (for state carry and their own
entry/`uni` snapshots) but skip all expression building. Symbolic cost scales
with the path vocabulary, not the window length.

## Soundness gate — the record-time assertion

Every fact and store is a template claimed to reproduce a concrete observation.
After each recorded frame, before its artifacts are returned, the assertion
re-evaluates all of them against the entry snapshot (and, for `cur`, the evolving
image at that position):

```
   store expr   == byte actually written
   fact  expr   == taken/target/value actually observed
   F[a]  expr   == end-of-frame byte at a
```

Any width, staleness, or simplification bug becomes a loud failure at record
time. On by default (`assertion=True`); pass `assertion=False` for production
runs. This is what lets the recorder claim *soundness under self-modifying code*
rather than merely *usually correct*.

## Fold channels (what Pass 2 recovers)

Each is exercised by one program in `tests/test_recorder.py`:

| Channel | Justification | Example site |
|---|---|---|
| SMC opcode identity at a mutated `pc` | case-fact `opcode(entry) == byte` | `_justify` |
| SMC operand `zp` / `abs` / `indy` (incl. derived `(lo+1) mod 256`, 16-bit word) | residualise to `mem`/`cur` | `_residual`, `_sval` |
| Branch / `jmp` / `jsr` target from mutable bytes | case-fact target | `_justify`, `_branch` |
| `jmpind` vector rewrite | case-fact `word(lo,hi) == target` | `_jmpind` |
| `rts` / `rti` return address, `brk` vector | case-fact composed return | `_rts`, `_rti`, `_brk` |
| Store placement onto a written cell | per-frame place fact | `_store` |
| Computed load aliasing a written cell | unconditional place fact at a flagged `alias_site` | `_loadsym` |

## API

```python
from deity_informant import record, run_sub, lift

rec = record(mem_or_vm, run_sub, entry, outputs=range(0x0400, 0x040D),
             invocations=N, lifter=lift, assertion=True)

rec.replay(i)   # reconstruct invocation i's observable write stream, byte-exact
rec.F[i]        # {addr: (entry_pure_expr, sz)}  end-of-frame transition function
rec.facts[i]    # [(site, kind, expr, observed)] control-flow / placement folds
rec.slog[i]     # [(pos, addr, expr, sz)]        position-attributed store log
rec.out_seq[i]  # [(addr, expr)]                 ordered writes to `outputs`
rec.entry[i]    # (entry_mem, entry_reg)         invocation-entry snapshot
```

See [symbolic-recorder.md](symbolic-recorder.md) for the expression algebra, the
simplification laws, the complexity guard, and the full acceptance-test matrix.
