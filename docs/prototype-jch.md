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
and both traces reproduce the `sidplayfp` oracle's register grid write for write
— the first time that has been checked frame by frame outside
`tests/test_oracle.py`.

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
| voices | 3 (no track 4, no NMI: ~~`machine.find_entries` refuses those builds~~ *2026-08-22: no longer -- the NMI is the schedule's second entry and the sample builds are that family* ([prototype-nmi.md](prototype-nmi.md))) | 3 |
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
~~sample builds (*Easy Does It*, *Shift*, *Little_Test*) stay refused by design.~~
*2026-08-22: not any more -- the NMI is the schedule's second entry, and* Easy Does It
*certifies with its mixer decompiled* ([prototype-nmi.md](prototype-nmi.md)).

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
| J4 | the state block | `voice[3]` **stride 1**, 34 fields (33 on the intro), each row three bytes at `base,X`; `timer`, `cursor`, `acc`, `counter`, `sid_image` and `voice_map` roles name 12 of them, and the two blocks `init` clears are two more records over the same index (`voice_2`, `voice_3`; Q1b) | same shape, 33 fields |
| J5 | the table programs | instruments as a stride-8 record; the pulse/filter columns as stride-4 reads | pulse `rec6[i/4]` and filter `rec7[i/4]` (4 and 3 columns), instruments `rec8[i/8]` (8), wave `T17DB`/`T181C` as parallel columns |
| J6 | the frequency table | `FREQ` 12-TET u16le, 79 entries reached | **95** entries, `freq_table` role, `12-TET u16le` |
| J7 | the two-phase tick | `phase -= 1` on the tick counter, `if phase == 0:` the commit arm, the prefetch arm two frames earlier, hard restart `AD = $F`/`SR = 0` with the gate mask `$FE` before it | same |
| J8 | the voice loop is a loop | `for v in 2, 1, 0:` (DEX/BMI), **no** sibling family (`copies` absent from both certificates) | same |
| J9 | the RAM under the SID | one `ghost` region `$D400`, 25 bytes, `sid_image` at delta 0; the player writes `ghost[x].ad`, `ghost.res_route`, `ghost.mode_vol`, and the wrapper's flush holds every chip write of the tick | not applicable (no wrapper): 3 `io` regions, the writes are `sid[x].ad` |
| J10 | pinned inputs | **2** (the uninitialised `$FB`/`$FC` the player saves and restores), down from 18,777 | 2, plus the subtune number in `A` at init |
| J11 | the oracle | **3,000 of 3,000** frames byte-exact against `sidplayfp`, both grids framed by the interrupt period each write's cycle falls in (§6, `grid.py`); against the oracle framer's half-frame anchor instead, 297 frames differ (494 as first measured), in exactly the five registers the wrapper writes last | **2,401 of 2,401** byte-exact — the whole certified horizon |
| J12 | structuring | **0** `sp`, 0 `trap 'unverified'`, 25 `trap 'untaken'`, **0** `goto` in 626 printed lines (613 before Q1b's two record headers, 7 `goto` in 562 before Q1a) | 0 `sp`, 0 unverified, 16 untaken, **0** `goto` in 582 lines (569, and 7 in 528) |
| J13 | cost | trace 12,000 ticks in ~100 s CPU over three chunks, verify 8,577 ticks in 2.3 s (3,717 ticks/s) | trace 4,000 in 9 s, verify 2,401 in 0.2 s (10,855 ticks/s) |
| J14 | genericity | the other 42 certificates reproduce field for field (`tools/tuneprog_recert.py`, 44/44) and the hermetic suite is unchanged | — |
| J15 | the port fix is guarded | `tests/test_oracle.py` renders 500 frames of the Puterman build from the tracer's SID log and compares it to `sidplayfp` frame for frame; with the direction byte back at 0 it fails on frame 1 | — |

## 5. The printed tuneprog (verbatim, `...` elides)

```
meta      entry sub $1003 every 19656 cycles (1.0 calls/frame, pal_video)
          certified 2,401 calls, 0 divergences, period 1,512, first repeat at
          call 2,400 (complete), stack eliminated, stage S6
state     voice[3] stride 1, 33 fields          # the struct-of-arrays block
            .freq_lo .freq_hi .timer .voice_map .pw_lo .pw_hi .ad .sr
            .cursor_1781 .timer_4 .cursor_1795 ...
          voice_2[3] $1014 12 bytes, stride 1, 4 fields    # the transpose split:
          voice_3[3] $1748 21 bytes, stride 1, 7 fields    # field +k, element v
            .timer +0 .f03 +3 .f06 +6 .timer_2 +9 .f0C +12 .f0F +15 .timer_3 +18
          rec6[11] stride 4, 4 fields           # the pulse program
          rec7[12] stride 4, 3 fields           # the filter program
          rec8[19] stride 8, 8 fields           # the instruments (.ad_2 .sr_2)
          rec4[96] stride 2 .FREQ freq_table 12-TET u16le, 95 entries
          step $172D u16 (lo|hi $100B) ; acc_3 $1779 u16 ; phase $1746
          cutoff_hi $1792 sid_image ; mode_vol $1793 sid_image
          b1014 12 bytes ; b1748 21 bytes       # what init clears in one loop

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
            t2 = voice_3[v].timer_3
            voice_3[v].timer_3 -= 1                # the duration countdown
            if voice_3[v].timer_3 < 0:
                voice_2[v].f06 = voice[v].b17BC    # staged -> live
                ...
                if voice_3[v].timer == 0:          # not a tie: re-trigger
                    voice[v].cursor_1795 = rec8[voice_2[v].f09/8].b18C9 # instrument
                    voice[v].timer_6 = (rec8[voice_2[v].f09/8].b18C5 & $F)
                    voice[v].pw_lo = (rec6[rec8[...]/4].b1893 & $F0)    # pulse init
                    sid.res_route = a129                                # $D417
                    sid[v].ad = rec8[...].ad_2                          # AD
                    sid[v].sr = rec8[...].sr_2                          # SR
                    sid[v].ctrl = 9                                     # TEST|GATE
                else:
                    p_1409(x=v)                    # EFFECTS: the three programs
        else:                                      # PREFETCH, two frames early
            ...
            while True:                            # the pattern command loop
                saved9 = T19FE[((ptr[1] << 8) | ptr) + voice_3[v].timer_2]
                if saved9 < 0: ...                 # $8x duration, $Ax instrument
                continue
            ...
            voice_2[v].f06 = $FE                   # gate off
            sid[v].ad = $F                         # hard restart AD = $0F
            sid[v].sr = 0                          #               SR = $00
            p_1616(x=v)                            # the write-out join
    ptr[1] = r5                                    # $FB/$FC restored
    ptr = r4
    return

p_1409(x):                               # $1409, 10,448 calls
    ...
    if x == 0:                                     # the filter runs on one track
        timer_5 -= 1
        cutoff_hi = rec7[...].b185F
        cutoff_hi += rec7[cursor_1790/4].b1860
    ...
    p_1616(x=x)

p_1616(x):                               # $1616, 11,128 calls
    sid[x].pw_lo = voice[x].pw_lo
    sid[x].pw_hi = voice[x].pw_hi
    sid.cutoff_hi = cutoff_hi
    sid[x].freq_lo = voice[x].freq_lo
    sid[x].freq_hi = voice[x].freq_hi
    sid[x].ad = voice[x].ad                             # $1740,X = 0, 7, 14 is
    sid[x].sr = voice[x].sr                             # the voice map
    sid[x].ctrl = (voice[x].b175D & voice_2[x].f06)
    sid.mode_vol = (mode_vol | mode_vol_or)
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

- **A tick is instantaneous, and one tune's music is *when* inside it** (*fixed in
  Q3, with this row's own diagnosis refuted*). The Puterman wrapper takes a delay
  count from its own data stream and spends it between each of the 25 register
  writes: the span from a tick's first SID write to its last grows from 168 cycles
  at tick 100 to 10,248 at tick 2,550. What the per-frame comparison lacked was
  not the writes' cycles but a frame *boundary* both sides agree on:
  `pysidtracker.oracle.grid_from_writes` rounds each write to the nearest frame
  measured from the first play write, i.e. a boundary 9,828 cycles into the tick,
  which the ramp crosses from tick ~2,450 on. `grid.py` frames both sides by the
  interrupt the frame *is* -- the tracer's log against tick 0's cycle plus
  `cycles_per_tick`, the CSV against `cycle - since_video_irq` -- and the
  difference is **0 of 3,000** with no sample point to pick. Fitting one instead
  reaches 1 of 3,000 at best (best near 9,400), because the two clocks disagree
  inside a frame: the play entry sits a constant 57-60 cycles later in sidplayfp's
  frame than in the tracer's, and by the last write of a ramped tick (9,312 cycles
  in on tick 2,502) it has drifted a further **+533** -- the VIC's badline DMA,
  one badline in eight raster lines, which the tracer does not model. The writes themselves match value for
  value and in order, frame by frame; what the trace's cycles buy in the helper is
  the general rule, that a tick outliving its frame lands its late writes in the
  next one.
- **The two init-cleared blocks are the transpose of the stride view** (*fixed in
  Q1b*). `init` clears `$1014` (12 bytes) and `$1748` (21) with one loop each, so
  the access relation joins each into one region; the tick then walks them as
  `base + 3k + v` — element inside, field outside — where GoatTracker's blocks
  A+B are `base + 7v + k`. `views.field_split` splits on an index whose *scale*
  is a record width, and here the scale is 1; `views.transpose_split` is the same
  rule with the two indices swapped, so what the index carries is instead the
  **element count** of the stride-1 view it walks (three tracks), every field is
  three wide, and each access confirms the layout by its own envelope staying
  inside one field. Only play-phase accesses count — the init clear loop reaches
  the whole block. The two now print `voice_2[v].f09` (the instrument) and
  `voice_3[v].timer_3` (the duration countdown), while the init clear loop -- which
  reaches all twelve bytes and so is no field of the view -- keeps `b1014[v] = 0`;
  the printed form takes the same envelope test the layout was proven with, so a
  block anything reads as a table keeps its flat address everywhere.
- **`sid.reg[5 + voice[v].b1740]` where GoatTracker prints `sid[v].ad`** (*fixed
  in Q1b*). V20 takes the voice's register offset from a per-track table
  (`$1740,X` = 0, 7, 14; `$1743,X` next to it is the V20 fine-tune constant)
  rather than from the index, so the printer saw a load, not a stride. But
  `0, 7, 14` is the SID's own voice → register-block map, the other half of the
  hardware fact `facts.VOICE_REG` already states: a read-only region whose three
  elements are exactly `7*i` is that map (`facts.voice_maps`), and an index read
  from it **is** the voice. The table is named `voice_map`, `Printer.voiced`
  accepts it beside the stride-7 forms, and the write-out reads
  `sid[x].pw_lo … sid[x].ad`; a clear loop over the register file still prints
  `sid.reg[v]`, and a table of `0, 7, 13` keeps its read.
- **The voice loop's joins are procedures (was 7 `goto` in both).** The player is
  a DAG of tail-jumps converging on the write-out (`$1616`) and the effects block
  (`$1409`); those joins are inside the loop body and fall into the `DEX; BMI`
  latch, so the old rule — a region several jumps reach and *nothing* leaves —
  could not promote them the way it promoted GoatTracker's `execchn` tails. Since
  Q1a a region that leaves **one** way promotes too: the helper returns where the
  edge went and each entry becomes `call; goto that edge`, handing back the values
  the exit reads. Both tunes print **0 `goto`**, for +41/+51 lines and +4/+6
  procedures.
- **A chip write also writes the RAM under it, in our model.** `tracevm._wr` and
  `interp.iostore` both keep `mem[a] = b` for a store the port sent to the chip,
  where the hardware leaves the RAM beneath untouched. Nothing observes it here —
  both sides agree, so no certificate can see the difference, and both tunes match
  the oracle write for write — but the RAM under I/O is now storage the front end
  types, so the two planes want separating once a tune discriminates (§5 backlog).
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
