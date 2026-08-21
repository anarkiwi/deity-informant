# Prototype: the installed-handler family (CINV entries) — results

The exemplar prototypes ([automatas](prototype-automatas.md),
[follin](prototype-follin.md), [goattracker](prototype-goattracker.md),
[sidwizard](prototype-sidwizard.md), [jch](prototype-jch.md)) are each one
*player*. This one is a *convention*: the entry a PSID with `play == 0` really
has. Its purpose is narrow — the evidence that admits the family to the HVSC
campaign — so it is measured over a screened population and witnessed by two
certificates, not by a player anatomy.

## 1. What the family is, and what stopped it

A PSID whose header carries `play == 0` installs its own interrupt handler and
lets the machine call it. Almost all of them install it at **CINV** (`$0314`),
which is the KERNAL's vector: the hardware IRQ enters `$FF48`, which pushes A, X
and Y on top of the frame the 6510 pushed, and only then jumps through `$0314`.
The handler ends by chaining to `$EA31` (the KERNAL's own service, then the
epilogue) or straight to `$EA81`, whose `PLA; TAY; PLA; TAX; PLA` pops exactly
those three bytes before its `RTI`.

Before this stage the tracer entered the handler with the bare 6510 frame. The
three pops then took the machine's own status and return address, `while SP <
start` ended one instruction before the `RTI`, and the block that would have
carried it was `trap 'unreached'`. Screened over the 486 `play == 0` tunes of
HVSC `MUSICIANS/A`–`C` (37 of them PSID): 23 built and **all 23** diverged at
tick 0 on `unreached`; the other 14 crashed the generated code on a `Call`
emitted with no arguments (a separate bug, [below](#4-the-no-argument-call)).

## 2. The convention

`Entry` carries `kernal` — the installed vector is CINV, so the KERNAL
dispatches it — and `machine.entry_frame` is the single statement of what the
machine left on the stack, in push order:

| entry | frame (push order) | slots above the entry pointer |
|---|---|---|
| `sub` (header `play`) | — | — |
| `irq`, raw vector (`$FFFE`) | `P` | `SP+1` status |
| `irq`, CINV (`kernal: true`) | `P`, A, X, Y | `SP+1..4` = Y, X, A, status |

Which of the two it is is the **6510 port's** word, not the tune's. With HIRAM set
the CPU takes its vector from the KERNAL's own `$FFFE`, so the dispatch is `$FF48`
and CINV and a write to `$FFFE` went to the RAM under the ROM; with HIRAM clear
that RAM *is* the vector and no prologue runs. `machine.vector_gate` decides it,
so a tune that armed both is not ambiguous, and where the port forbids the only
dispatch the tune armed it refuses rather than pick:

| installed | KERNAL mapped | KERNAL banked out |
|---|---|---|
| CINV only | CINV, `kernal: true` | refuse `vector banked out` |
| `$FFFE` only | refuse `vector banked out` | raw, `kernal: false` |
| both | CINV, `kernal: true` | raw, `kernal: false` |

`find_entries` runs the gate on the pre-init image, `Tracer.run_init` re-runs it
once init has had the port — that verdict is what the ticks and the certificate
carry — and every tick re-checks HIRAM, since the frame is the tick's contract.

The tracer pushes it, `verify._enter` pushes it, and `frames.contract` names each
slot as a parameter of the tick: the status is the entry flags packed
(`lower.status_expr`), each register slot is that register's entry value. Nothing
else changes. Exactness is the must-def discipline of the frame analysis, not a
pattern: the pushed return address (now `SP+5`/`SP+6`) names no value, so a tick
that reads it is residual; a tick that reaches the status through `TSX` is
residual because that address is no slot at all; and a raw-vector entry keeps the
one-slot shape it had.

## 3. Measured over the family

The same 37 PSIDs — every one of them a CINV entry — at a 15 s horizon:

| | before | after |
|---|---|---|
| build | 23 | **37** |
| reach the `RTI` and verify | 0 | **37** |
| certify (S6) | 0 | **34** (3 die in `fold.outline`, a presentation bug) |
| divergences / envelope traps | — | **0 / 0** on all 34 |
| stack eliminated | — | 20 (14 residual) |

Two are committed as evidence:

| | `becher-jodler` | `baumrucker-professor` |
|---|---|---|
| tune | `MUSICIANS/B/Becher_Patrick/Jodler.sid` | `MUSICIANS/B/Baumrucker_Steven/Playful_Professor-Math_Tutor.sid` |
| container | PSID, load $C000–$CBA5, init $CB20, `play = 0`, 1 subtune | PSID, load $77DF–$8400, init $7F28, `play = 0`, 7 subtunes |
| entry | CINV → `$C738`, host CIA, 16,422 cycles | CINV → `$7F75`, its own CIA timer, 9,829 cycles (2 calls a frame) |
| epilogue | `JMP $EA31` | `JMP $EA31` |
| horizon | 707 ticks, period 700, **complete** | 1,503 ticks (15 s), horizon |
| divergences / traps / pinned inputs | 0 / 0 / 0 | 0 / 0 / 0 |
| stack | eliminated | eliminated |
| program | 2 procedures, 12 blocks, 41 statements, 33 regions | 8 procedures, 71 blocks, 104 statements, 36 regions |

*Jodler*'s whole tick, printed — no `sp`, no flags, no `RTI`, and the three
voices folded to one body:

```
tick():                                  # $C738
    t1 = phase
    phase -= 1
    if phase == 0:
        phase = b0342
        sid[0].ctrl = $20
        sid[1].ctrl = $10
        sid[2].ctrl = $80
        freq_hi_idx += 1
        if freq_hi_idx == $64: trap 'untaken'
        for v in 0, 1, 2:
            sid[v].freq_hi = copy[v].bC030[freq_hi_idx]
            sid[v].freq_lo = copy[v].bC15C[freq_hi_idx]
        sid[0].ctrl = $21
        sid[1].ctrl = $11
        sid[2].ctrl = $81
    return
```

## 4. The no-argument `Call`

The 14 crashes were not the entry convention: the IR call graph can have
**cycles**. `cfg._no_recursion` refuses a `JSR` cycle but deliberately lets a
*tail* call through — it grows no frame — and a tail edge is still a `Call` in
the IR, so a player whose routine jumps back to a `JSR`ed label is self-recursive
there (the four Crowther examples' `$CD45`, and ten Android tunes). Wiring the
interfaces in one post-order pass then gave that site the callee's params before
they were computed: `args = ()` where the callee ends up taking eight, and
`p_p_CD45() missing 8 required positional arguments` at tick 0. Params and rets
only grow, so `wire.wire` iterates them to a fixpoint — a worklist in post-order
rank, a caller re-queued when its callee moves, one pass where the graph is
acyclic.

## 5. The oracle guard

The frame is a machine-model claim, so the check is `sidplayfp`, as it was for the
6510 port in [prototype-jch.md](prototype-jch.md). `tests/test_oracle.py` runs
lft's `A_Mind_Is_Born.sid` — a CINV entry at `$0031` that chains to `$EA31` —
through the tuneprog tracer and compares its interrupt-framed grid with the
oracle's: **0 of 3,000 frames differ**. It reaches its `RTI` only because the
tracer pushes the three bytes `$FF48` saved.

That guard also found the cadence defect this prototype left open, now fixed:
`Jodler` carries PSID `speed = 1` and programs no timer of its own, so the driver
ticks it on the host's CIA at 16,422 cycles where we said 19,656 (PAL video), and
lft's RSID was mis-clocked the same way. `test_the_cadence_is_the_oracles_own_interrupt_period`
now decides all four classes against the CSV's raises; 28 of the 37 carry a set
speed bit. `Playful Professor` and Cox's `Caverns of Eriban` are still refused by
`grid.sidtrace_clock`, which takes the period from the median gap between raises
that carried a write: that is the *burst* period of a tune writing every 6th or
7th frame, not the frame. That one stays a plan row.

## 6. What the model still does not carry

The real `$FF48` does more than save A/X/Y: `TSX; LDA $0104,X; AND #$10` (the BRK
test) leaves A = 0, X = SP and Z set at the handler's first instruction, where
the tracer hands it the registers the previous tick left. Measured over the 37:
31 read no entry register at all, 2 read A and see the same 0, and 4 (Boray) read
A/X/Y live-in and would see other bytes on hardware. It is not a certificate
question — the tracer and the verifier agree, and such a read is a pinned
`entry_reg` input either way — but `X = SP` cannot simply be modelled: an `SP`
value that survives makes the whole program residual. The plan keeps it as a
model row, to be decided against the `sidplayfp` grid on one Boray tune.
