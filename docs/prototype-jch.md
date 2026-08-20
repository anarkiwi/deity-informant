# Prototype: the tuneprog decompiler on JCH NewPlayer V20 — results

The fifth exemplar of [tuneprog-decompiler-design.md](tuneprog-decompiler-design.md),
after defMON's *Automatas* ([prototype-automatas.md](prototype-automatas.md)),
Follin's *Ghouls'n'Ghosts* ([prototype-follin.md](prototype-follin.md)),
GoatTracker 2 ([prototype-goattracker.md](prototype-goattracker.md)) and SID
Wizard ([prototype-sidwizard.md](prototype-sidwizard.md)):
`MUSICIANS/P/Puterman/I_Could_Eat_a_Knob_at_Night.sid` and
`MUSICIANS/J/JCH/Guldkornekspressen_Intro.sid`, two builds of the plain
(3-voice) JCH NewPlayer V20 playroutine (anatomy [§3.5](playroutine-anatomy.md),
whose exemplar is the 4-track *sample* build; the plain V20 is the same engine
minus every `CPX #$03` branch and the track-4 code). V20 is the largest HVSC
family (~1,737 tunes), so the point is not two tunes but that the family's idioms
come out of the generic pipeline. Both tunes carry a **complete** certificate,
and both traces match the `sidplayfp` oracle's register grid — the first time
that has been checked frame by frame outside `tests/test_oracle.py`.

## 1. Why JCH V20, and what it adds

| design mechanism | how V20 stresses it |
|---|---|
| the 6510 port (S0/S1) | Puterman's wrapper runs the whole player with **I/O banked out** (`$01 = $34`), so the player's 25 register writes a frame are *memory* under the SID and the wrapper flushes its own copy afterwards. Two machine facts had to be right for a single write to land: the port's reset state, and the port bytes being part of the program's own machine |
| storage typing (S3) | the state block is **struct-of-arrays**: `base,X` with X = track, rows three bytes wide, 33 of them — the transpose of GoatTracker's `voice*7` records |
| table typing (S3/S6) | four little bytecode programs in parallel columns: pulse `[init/keep, Δ, dir\|frames, next]` and filter `[cutoff/keep, Δ, frames, next]` at stride 4, instruments at stride 8, the wave table as two parallel 102-byte columns, and a 96×2 LE frequency table |
| phase (S5) | one tick counter drives a **two-phase** step: prefetch at tick 2 into staged cells, commit at tick 0, effects every other frame — the note-on lands two frames after the gate goes off |
| the voice loop (S5) | `LDX #2 … DEX; BMI` — JCH *loops* where Follin and defMON unroll, so the family machinery must find **no** family and the copy index must cost nothing |
| tail-jump structuring (S5) | zero `JSR` inside play: every path is `JMP` to the write-out or to the effects block, a DAG of joins inside the loop body |
| no SMC at all (S2) | the plain V20 play path self-modifies nothing (0 cells on the JCH build; the 3 cells on the Puterman build are its wrapper's) — the opposite end of the range from SID Wizard's 79 |

## 2. Ground truth (anatomy §3.5 + measurement)

| fact | I Could Eat a Knob at Night | Guldkornekspressen Intro |
|---|---|---|
| container | PSID, load $0E00, init $0E00, play $0E03, 1 subtune | PSID, load/init $1000, play $1003, 1 subtune |
| player | V20 at $1000 (`JMP $1040` / `JMP $10C1`) under a 512-byte wrapper at $0E00 | V20 at $1000, same two vectors, no wrapper |
| cadence | 50 Hz video, 19,656 cycles/tick, one `sub` entry | same |
| executed | 567 sites | 572 |
| SMC | 3 cells, all in the wrapper ($0E23/$0E2E/$0E33) | **0** cells |
| voices | 3 (no track 4, no NMI: `machine.find_entries` refuses those builds) | 3 |
| song loop | the song stops: from tick 8,576 the state is a fixed point (period 1) | the orderlist restarts: period 1,512 ticks = 30.24 s |

**How the second tune was chosen.** V20 is a code template — builds differ only
in immediates and table offsets — so identity is an opcode-stream question. All
270 tunes under `MUSICIANS/J/JCH/` were screened statically (no tracing): 208
carry the family's `JMP init` / `JMP play` pair at the load address, and their
`init` vectors scatter over a dozen NewPlayer versions ($1040 ×62, $1028 ×32,
$1020 ×15, …). Of those, **5** have the reference's play entry `$10C1`, and
exactly **2** align 1.000 against the reference's opcode stream over the first
1,200 bytes from `init` (`difflib.SequenceMatcher` on opcodes only, so immediates
and table offsets do not count): *Crowdnoise* and *Guldkornekspressen_Intro*
(the other three are 0.961–0.962 — near variants). The intro was taken because it
executes more of the engine (568 sites at 15 s against Crowdnoise's 502). The
sample builds (*Easy Does It*, *Shift*, *Little_Test*) stay refused by design.

## 3. What broke, and the generic fix

Everything below is a *mechanism*, verified by hermetic snippet tests
(`tests/tuneprog/test_bank.py`, `test_printer.py`); no JCH special case exists
anywhere in the pipeline.

| symptom | generic fix | where |
|---|---|---|
| every one of the Puterman build's SID writes went to the chip **twice**: the player's (which the hardware sends to RAM) and the wrapper's. Against the `sidplayfp` oracle, 400 of 400 frames differed | the 6510 port's **direction** byte decides whether `STA $01` banks anything: with `$00 = 0` every port bit is an input and reads as 1, so I/O stays mapped whatever a tune writes. A PSID host is a KERNAL-initialised machine, so the pre-init image carries `$00 = $2F`, `$01 = $37` | `machine.MachineImage.from_sid` |
| then `trap 'input exhausted'` at `$D418` on tick 0: the *program's* machine banked differently from the tracer's, because the direction byte is in no region — no traced op touches it | the port is machine state like the stack page and the RAM under I/O: the program's image carries `$0000-$0001` from the pre-init image | `build._machine_image` (`image_port`) |
| 18,777 pinned inputs over 751 ticks — the player's own register file read back as `input($D400 + v)` — and its writes printed `sid.reg[…] = ` although they are memory | an access to `$D000-$DFFF` reaches the **chip** only where the port had I/O mapped, which the tracer knows at every access: it records the `(pc, op)` pairs that did. A region no chip access reaches types like any other storage, and an access that did reach one keeps the `io` class (so a chip write is still a chip write in the same region). The RAM under I/O is `known` from the image on both sides, exactly as the tracer treats it | `tracevm.chip_ops`, `Trace.is_chip`, `regions._ram_io`, `lower.Storage.chip`, `interp.Machine`, `lower.Storage.k0` |
| the 25 bytes under `$D400` were a nameless `bD400` block | the port *aliases* them onto the register file, so they are a shadow at delta 0 — the same role a flush loop proves, by the address instead of by the copy | `facts.image_copy` |
| `RecursionError` printing the wrapper's delay loop (`y = count; while --y >= 0`) at the full horizon | a busy-wait's condition is substituted from the values its body defines; a value the body defines **from itself** is a recurrence, and a cyclic substitution is not one. The loop keeps its body | `printer._acyclic` |

Nothing else was needed. The width-3 struct-of-arrays, the 4-column table
programs, the two-phase tick, the gate mask, the `$D417` shadow and the voice
loop all came out of the machinery the four earlier exemplars built.

## 4. Results (measured)

Certificates: `docs/certificates/jch-knob-at-night.json`,
`jch-guldkorn-intro.json`, from
`tools/tuneprog_certify.py TUNE --out DIR --until-period --resume`; printed forms
in each output directory's `tuneprog.md`. The HVSC tests
(`tests/tuneprog/test_hvsc_jch.py`) assert the rows below at 15 s / 30 s.

| id | claim | Knob at Night | Guldkorn Intro |
|---|---|---|---|
| J1 | per-tick equivalence from init to the first state repeat | **0** divergences over 8,577 ticks, 0 envelope traps | **0** over 2,401 ticks |
| J2 | periodicity witness, `complete: true` | first repeat at tick 8,576, period **1** — the song ends and the state is a fixed point | period **1,512** ticks (30.24 s), first repeat at 2,400 |
| J3 | front end | 567 sites, 99 regions (49 state, 41 const, 6 init-only, 3 image), 9 procedures, 155 blocks, 472 statements | 572 sites, 103 regions, 2 procedures, 160 blocks, 443 statements |
| J4 | the state block | `voice[3]` **stride 1**, 34 fields (33 on the intro), each row three bytes at `base,X`; `timer`, `cursor`, `acc`, `counter` roles on 8 of them | same shape, 33 fields |
| J5 | the table programs | instruments as a stride-8 record; the pulse/filter columns as stride-4 reads | pulse `rec6[i/4]` and filter `rec7[i/4]` (4 and 3 columns), instruments `rec8[i/8]` (8), wave `T17DB`/`T181C` as parallel columns |
| J6 | the frequency table | `FREQ` 12-TET u16le, 79 entries reached | **95** entries, `freq_table` role, `12-TET u16le` |
| J7 | the two-phase tick | `phase -= 1` on the tick counter, `if phase == 0:` the commit arm, the prefetch arm two frames earlier, hard restart `AD = $F`/`SR = 0` with the gate mask `$FE` before it | same |
| J8 | the voice loop is a loop | `for v in 2, 1, 0:` (DEX/BMI), **no** sibling family (`copies` absent from both certificates) | same |
| J9 | the RAM under the SID | one `ghost` region `$D400`, 25 bytes, `sid_image` at delta 0; the player writes `ghost.reg[…]`, `ghost.res_route`, `ghost.mode_vol`, and the wrapper's flush is the only chip write | not applicable (no wrapper): 3 `io` regions, the writes are `sid.reg[…]` |
| J10 | pinned inputs | **2** (the uninitialised `$FB`/`$FC` the player saves and restores), down from 18,777 | 2, plus the subtune number in `A` at init |
| J11 | the oracle | 2,506 of 3,000 frames byte-exact against `sidplayfp`; the rest differ only in the five registers the wrapper writes last (§6) | **2,401 of 2,401** byte-exact — the whole certified horizon |
| J12 | structuring | **0** `sp`, 0 `trap 'unverified'`, 25 `trap 'untaken'`, 7 `goto` in 562 printed lines | 0 `sp`, 0 unverified, 16 untaken, 7 `goto` in 528 lines |
| J13 | cost | trace 12,000 ticks in ~100 s CPU over three chunks, verify 8,577 ticks in 2.3 s (3,717 ticks/s) | trace 4,000 in 9 s, verify 2,401 in 0.2 s (10,855 ticks/s) |
| J14 | genericity | the other 42 certificates reproduce field for field (`tools/tuneprog_recert.py`, 44/44) and the hermetic suite is unchanged | — |

## 5. The printed tuneprog (verbatim, `...` elides)

```
meta      entry sub $1003 every 19656 cycles (1.0 calls/frame, pal_video)
          certified 2,401 calls, 0 divergences, period 1,512, first repeat at
          call 2,400 (complete), stack eliminated, stage S6
state     voice[3] stride 1, 33 fields          # the struct-of-arrays block
            .timer .acc_2 .cursor_1781 .timer_4 .acc_5 .acc_6 .cursor_1795 ...
          rec6[11] stride 4, 4 fields           # the pulse program
          rec7[12] stride 4, 3 fields           # the filter program
          rec8[19] stride 8, 8 fields           # the instruments
          rec4[96] stride 2 .FREQ freq_table 12-TET u16le, 95 entries
          step $172D u16 (lo|hi $100B) ; acc_4 $1779 u16 ; phase $1746
          cutoff_hi $1792 sid_image ; mode_vol $1793 sid_image
          b1014 12 bytes ; b1748 21 bytes       # the two init-cleared blocks

tick():                                  # $1003, 4,000 calls
    saved = ptr                                    # the player saves $FB/$FC
    saved12 = ptr[1]
    t1 = phase
    phase -= 1
    if phase >= 0:
        p_10E9(r4=saved, r5=saved12)
        return
    else:
        phase = b1747                              # reload from the speed byte
        if (2 > phase): trap 'untaken'             # funk tempo: never taken here
        p_10E9(r4=saved, r5=saved12)
        return

p_10E9(r4, r5):                          # $10E9, 4,000 calls
    x2 = 2
    for v in 2, 1, 0:                              # DEX; BMI -- the voice loop
        if voice[v].b1006 == 0: trap 'untaken'     # track enabled?
        if phase == 0:                             # COMMIT
            t2 = b1748[v + $12]
            b1748[v + $12] -= 1                    # the duration countdown
            if b1748[v + $12] < 0:
                b1014[v + 6] = voice[v].b17BC      # staged -> live
                ...
                if b1748[v] == 0:                  # not a tie: re-trigger
                    voice[v].cursor_1795 = rec8[b1014[v + 9]/8].b18C9   # instrument
                    voice[v].timer_6 = (rec8[b1014[v + 9]/8].b18C5 & $F)
                    voice[v].acc_5 = (rec6[rec8[...]/4].b1893 & $F0)    # pulse init
                    sid.res_route = a129                                # $D417
                    sid.reg[5 + voice[v].b1740] = rec8[...].b18C3       # AD
                    sid.reg[6 + voice[v].b1740] = rec8[...].b18C4       # SR
                    sid.reg[4 + voice[v].b1740] = 9                     # TEST|GATE
                else:                              # EFFECTS: the three programs
                    ...
                    if v == 0:                     # the filter runs on one track
                        timer_5 -= 1
                        cutoff_hi = rec7[...].b185F
                        cutoff_hi += rec7[cursor_1790/4].b1860
                    ...
                    sid.reg[2 + voice[v].b1740] = voice[v].acc_5        # PW lo
                    sid.reg[3 + voice[v].b1740] = voice[v].acc_6        # PW hi
                    sid.cutoff_hi = cutoff_hi
                    sid.reg[voice[v].b1740] = voice[v].acc              # FREQ lo
                    sid.reg[1 + voice[v].b1740] = voice[v].b100F        # FREQ hi
                    sid.reg[4 + voice[v].b1740] = (voice[v].b175D & b1014[v + 6])
                    sid.mode_vol = (mode_vol | mode_vol_or)
        else:                                      # PREFETCH, two frames early
            ...
            while True:                            # the pattern command loop
                saved9 = T19FE[((ptr[1] << 8) | ptr) + b1748[v + 9]]
                if saved9 < 0: ...                 # $8x duration, $Ax instrument
                continue
            ...
            b1014[v + 6] = $FE                     # gate off
            sid.reg[5 + voice[v].b1740] = $F       # hard restart AD = $0F
            sid.reg[6 + voice[v].b1740] = 0        #               SR = $00
        goto L1616_A9                              # the write-out join
    ptr[1] = r5                                    # $FB/$FC restored
    ptr = r4
    return
```

The Puterman build prints the same player under its wrapper, with the register
file as memory:

```
tick():                                  # $0E03, 8,577 calls
    p_0EB9()                                       # flush last frame's copy
    p_0E41()
    ...

p_0E41():                                # $0E41, 8,577 calls
    b0001 = $34                                    # I/O out
    row_apply2()                                   # the whole V20 player
    for v in 24..0:
        freq_lo[v] = ghost.reg[v]                  # the register file, as RAM
    b0001 = $35                                    # I/O in
    freq_lo[24] = $1F                              # the wrapper's overrides
    ...

writeout():                              # $10E9, 8,577 calls
    for v in 2, 1, 0:
        ...
        ghost.reg[2 + voice[v].b1740] = voice[v].acc_2
        ghost.cutoff_hi = acc_4
        ghost.mode_vol = (0 | b1009)
```

## 6. What remains

- **The tick model has no cycle budget.** The Puterman wrapper takes a delay
  count from its own data stream and spends it between each of the 25 register
  writes: the span from a tick's first SID write to its last grows from 168
  cycles at tick 100 to 10,248 at tick 2,550. Against the `sidplayfp` oracle the
  first 2,506 of 3,000 frames are byte-exact and the remaining 494 differ in
  exactly the five registers written last (voice 1's), whose write has moved past
  the point the oracle samples the frame at. The certificate is unaffected — it
  compares the tuneprog against our trace, and the trace against the oracle is
  what this measures — but a cycle-accurate schedule inside a tick is the
  boundary. The JCH build is byte-exact over its whole certified horizon.
- **The two init-cleared blocks are the transpose of the stride view.** `init`
  clears `$1014` (12 bytes) and `$1748` (21) with one loop each, so the access
  relation joins each into one region; the tick then walks them as
  `base + 3k + v` — element inside, field outside — where GoatTracker's blocks
  A+B are `base + 7v + k`. `views.field_split` splits on an index whose *scale*
  is a record width, and here the scale is 1, so the two blocks keep their flat
  address (`b1748[v + $12]` is the duration countdown, `b1014[v + 9]` the
  instrument). The state block proper needs nothing: its rows are separate
  regions and print `voice[v].field` already.
- **`sid.reg[5 + voice[v].b1740]` where GoatTracker prints `sid[v].ad`.** V20
  takes the voice's register offset from a per-track table (`$1743,X` = 0, 7, 14)
  rather than from the index, so the printer sees a load, not a stride: nothing
  in the tune walks a 7-byte record, which is the evidence `facts.scales` wants.
  The offset is a constant per copy, so the copy-index vocabulary could fold it,
  but that means substituting a table read into the loop it indexes — the rule
  #248 refused for exactly this reason.
- **7 `goto` in both, all inside the voice loop.** The player is a DAG of
  tail-jumps converging on the write-out (`$1616`) and the effects block
  (`$1409`); those joins are inside the loop body and fall into the `DEX; BMI`
  latch, so `tails.promote_tails` (a region several jumps reach and *nothing
  leaves*) cannot promote them into procedures the way it promoted GoatTracker's
  `execchn` tails. Structuring a loop body's joins is the open item.
- **Names are role-derived.** `phase` is the tick counter, `b1747` the speed,
  `b1748[v + 9]` the pattern position, `voice[v].b17BC` the staged gate mask: the
  trace shows the shapes, not the words. A family dictionary keyed on the V20
  signature (which §2's screen already computes) would name them from the
  anatomy's own table.
- **16 (25) `trap 'untaken'` arms** are the paths these songs never take — funk
  tempo, track stop `$FE`, the raw-frequency wave mode, the tie prefetch, the
  `$7E` wave sentinel — every one of them a branch direction, not a gap. There is
  no `trap 'unverified'` in either tune: V20 dispatches its commands by compare
  chain, so there is no jump table to enumerate.
