# Prototype: the tuneprog decompiler on GoatTracker 2 — results

The third exemplar of [tuneprog-decompiler-design.md](tuneprog-decompiler-design.md),
after defMON's *Automatas* ([prototype-automatas.md](prototype-automatas.md)) and
Follin's *Ghouls'n'Ghosts* ([prototype-follin.md](prototype-follin.md)): `MUSICIANS/L/Linus/Je_suis_Linus_le_salaud.sid` and
`MUSICIANS/L/Linus/Do_It_Again.sid`, two builds of the GoatTracker V2.73
playroutine (anatomy [§3.3](playroutine-anatomy.md)). GT2 is the largest HVSC
family (12 % of the collection), so the point is not one tune but that the family's
idioms come out of the generic pipeline. Both tunes carry a **complete**
certificate: every SID write of every call from init to the first state repeat,
plus the periodicity witness.

## 1. Why GoatTracker, and what it adds

| design mechanism | how GT2 stresses it |
|---|---|
| SID image role (S6) | 25 ghost bytes `$14CA..$14E2` flushed by a *loop* (`LDX #$18; LDA $14CA,X; STA $D400,X; DEX; BPL`) — 25 writes per call, always, first thing; every other write in the frame goes to the ghost |
| computed control from SMC (S2) | dispatch by patching the **low byte** of a `JSR`/`JMP`: `LDA $144A,Y; STA $1289; STA $1295` then `JSR $10xx` (16 tick-0 commands in one page), `LDA $145A,Y; STA $131E` then `JMP $10xx` (5 continuous effects), and `$1445` for wavetable commands |
| SMC immediates as globals (S2/S3) | 11 one-byte cells inside instructions: init-pending `$110D`, filter step/time/cutoff/ctrl/type, volume, effect number, vibrato compare, the two calculated-speed shifts |
| index domains → struct views (S3/S6) | `X = voice*7` is *simultaneously* the SID register offset and the record offset: five 7-field blocks at `$1461/$1476/$148B/$14A0/$14B5` plus the ghost |
| voice loop (S5) | `JSR mt_execchn; LDX #7; JSR; LDX #$0E; <fall through>` — three calls of one procedure, the third a tail call by fall-through |
| 1-based tables (S3/S6) | every table is read at `base-1,Y` (`LDA $16F8,Y` ⇒ wavetbl `$16F9`), instrument columns at `base-1+30k` |
| shared tails, entry into the middle (S5) | every voice path ends `JMP $140F`; the effect handlers are `JMP`-entered and `JMP`-exited; `mt_wavenote`, `mt_done`, `storefreqhi` are entered from three places each |
| known-flag branches (S4) | `LDA #$00; BEQ`, `ORA #$F0; BNE`, `BNE` after `INC` — unconditional branches encoded as conditional ones |
| three-way phase (S5) | `DEC counter,X; BEQ tick0; BPL effects; <reload>` in six bytes |

## 2. Ground truth (anatomy §3.3 + measurement)

| fact | Je suis Linus | Do It Again |
|---|---|---|
| container | PSID, load `$1000`, init `$1000`, play `$1003`, 1 subtune, 2:15 | PSID, load `$AC00`, play `$AC03`, 1 subtune, 2:52 |
| cadence | 50 Hz video, 19656 cycles/tick, one entry (`sub`) | same |
| build | GHOSTREGS + BUFFEREDWRITES, no sfx, no author text | the same plus `VOLSUPPORT` and the author-info layout |
| executed | anatomy: 433 instructions in 3000 frames (+32 reachable only) | anatomy: 425 |
| SMC | anatomy: 12 sites, 14 patched cells | fewer (no `mt_setmastervol` patch) |
| song loop | orderlists end `FF 00`, so the tune repeats at 2:15 | repeats at 2:52 |

## 3. What broke, and the generic fix

Everything below is a *mechanism*, verified by hermetic snippet tests; no GT2
special case exists anywhere in the pipeline.

| symptom on GT2 | generic fix | where |
|---|---|---|
| the ghost block printed as `timer_4[v]`, the flush loop as `sid[v].freq_lo = timer_4[v]` | a region a loop copies byte-for-byte into `$D400..` is a **SID image**: `sidw($D400+i, load(R, base+i))` with one index expression gives the delta from region byte to register, so every access to it prints by the register it mirrors (`ghost.reg[v]`, `ghost[v].ctrl`, `ghost.mode_vol`); an index the program elsewhere walks a 7-byte record with is a voice, which is what makes `$14CE,X` voice `x/7`'s control register | `recover.image_copy`, `recover._scales`, `printer.regcell` |
| `T16F9[$16F8 + y]` — the operand printed instead of the index | a region records the address its index counts from (**origin** = operand + the smallest index observed); indices print from it, so a 1-based table reads `T[y]`, its look-ahead sibling `T[y + 1]`, and the note says how far the origin sits below the base | `regions._origin`, `Rgn.zero`, `printer.addr_of` |
| the three `execchn` calls printed one after another, threading `sp` | (a) a cell stored once is forwarded to every read that store reaches — for a stack slot across blocks, by dominance over one pure address, which collapses a `PHA` and the `PLA` a branch away; (b) a call argument's constant is evidence of a copy index, arguments the printer drops are not part of a shape, and a JSR frame push is not a unit — so the fall-through third call joins the run | `texture.stack_temps`, `unroll` |
| 21 `goto`s in `execchn` (shared tails, entry into the middle) | a region several jumps reach and nothing leaves **is a procedure** — the routine the player enters by `JMP`. It is promoted, its parameters are the registers that cross into it, each jump becomes a tail call, and a promotion that does not lower the residue is rolled back | `tails.promote_tails` |
| `if t1 == 1:` for `DEC counter,X; BEQ` | an arm renders from the state its test saw and nothing survives the join, so `x == k` prints as the cell that holds `x - k` against zero: `if voice[v].counter == 0` | `printer.arms`, `printer.expr` |
| `if (a10 \| $F0) == 0: trap 'untaken'` | `ORA #imm` with a bit set is never zero, so the known-flag branch folds and the dead arm goes | `idioms.fold` |
| `T1876[(p + (y + 1))/22]` mis-parenthesised | an index scaled by a stride keeps its parentheses | `printer.index` |

## 4. Results (measured)

Certificates: `docs/certificates/gt2-je-suis-linus.json`, `gt2-do-it-again.json`,
produced by `tools/tuneprog_certify.py TUNE --out DIR --until-period --resume`;
printed forms in each output directory's `tuneprog.md`. The HVSC tests
(`tests/tuneprog/test_hvsc_goattracker.py`) assert every row below at 30 s / 20 s.

| id | claim | Je suis Linus | Do It Again |
|---|---|---|---|
| G1 | per-call equivalence from init to the first state repeat | **0** divergences over 8,236 calls, 0 envelope traps | **0** over 9,956 calls |
| G2 | periodicity witness, `complete: true` | period **6,720** calls (134.4 s = the HVSC length), first repeat at call 8,235 | period **8,640** (172.8 s), first repeat at 9,955 |
| G3 | front end | 437 sites, 73 regions (29 state, 43 const), 14 procedures, 245 blocks, 580 statements | 433 sites, 73 regions, 14 procedures, 234 blocks, 569 statements |
| G4 | ghost image | one `sid_image` region `$14CA`, 25 bytes, delta `$D400-$14CA`; the flush prints `for v in 24..0: sid.reg[v] = ghost.reg[v]`, the per-voice writes `ghost[x/7].ctrl` | same at `$B0F5` |
| G5 | patched low-byte dispatch | `switch` over `$1289`/`$1295`: 9 tick-0 handlers the trace dispatched plus the table's own entries (**14** arms, 7 `trap 'unverified'`), `$131E` (4 of 5 effects), `$1445` (1); every high byte constant, no default | 13 arms, 4 effects |
| G6 | SMC immediates | **14** play-written cells, every one a `load` at its instruction; 10 are one-byte scalars, three of them named by their role (`cursor_1141`, `timer`, `acc`), the rest `bXXXX` | 13 cells |
| G7 | voice loop | `for v in 0, 1, 2: row_apply(x=(v * 7))` — the three calls print once, the third being the fall-through | same |
| G8 | per-voice records | `voice[3]` stride 7, 12 fields (blocks C/D/E); blocks A+B stay one 42-byte region (init zeroes them in one loop, as the design predicts) | same |
| G9 | 1-based tables | **18** regions whose origin is `base-1` (wavetbl `$16F9`, notetbl, the nine instrument columns at stride 30, pulse/filter/speed columns), 30 with an origin below their base | 17 |
| G10 | structuring | **0** `goto`, **0** `sp`, 12 `trap 'untaken'` arms (branch directions the song never takes) in 1,202 printed lines | 0 `goto`, 0 `sp`, 15 traps |
| G11 | cost | trace 12,000 calls in 23 s CPU, verify 8,236 calls in 0.8 s (10,445 calls/s), front end + presentation ~1 s: one invocation, under the 60 s budget | trace 24 s, verify 1.0 s |
| G12 | genericity | Automatas, Commando, Ghouls'n'Ghosts and Emomyst certify unchanged by the same code (`tests/tuneprog/test_hvsc*.py`, 21 tests) | — |

## 5. The printed tuneprog (verbatim, `...` elides)

```
meta      entry sub $1003 every 19656 cycles (1.0 calls/frame, pal_video)
          certified 8,236 calls, 0 divergences, period 6,720, first repeat at
          call 8,235 (complete), stage S6
state     voice[3] stride 7, 12 fields
            .b148B .cursor_148C .b148D .timer_2 .freq_lo_idx .cursor_1490 .b1491
            .b14A0 .timer_3 .timer_4 .b14BA .freq_lo_idx_2
          ghost $14CA 25 bytes  sid_image  flushed to $D400..
          b1461 $1461 42 bytes                  # blocks A+B, one region
          b1289 b1295 b131E b1445 (the patched jump cells) ; b1096 b10AC b110D
          cursor_1141 timer acc b118F b1194 b1310 b131A (the SMC immediates)
const     FREQ_LO $14E3 / FREQ_HI $1543 12-TET ; T144A 16 bytes (the tick-0 jump
          table) ; T15EB..T16DB nine instrument columns, 1-based, stride 30 ;
          T16F9 wavetbl 2-based, read at $16F7,i ; T18B7.. the patterns

tick():                                  # $1003, 12,000 calls
    x2 = $18
    for v in 24..0:                      # the ghost flush, 25 writes a call
        sid.reg[v] = ghost.reg[v]
    if b110D < 0:                        # init pending? ($FF = running)
        row_advance()                    # the global filter program
        cascades()
        return
    else:
        x8 = $29
        for v in 41..0:                  # init: zero blocks A and B
            b1461[v] = 0
        ghost.cutoff_lo = 0
        ...
        for v in 0, 1, 2:
            p_1130(x=(v * 7))            # three initchn calls, the third a tail
        return

cascades():                              # $1189, 11,999 calls
    ghost.cutoff_hi = acc
    ghost.res_route = b118F
    ghost.mode_vol = (b1194 | $F)
    for v in 0, 1, 2:                    # JSR, JSR, fall-through
        row_apply(x=(v * 7))
    return

row_apply(x):                            # $11A4 mt_execchn, 35,997 calls
    t1 = voice[x/7].timer_2
    voice[x/7].timer_2 -= 1
    if voice[x/7].timer_2 == 0:                    # tick 0
        b1289 = T144A[b1461[5 + x]]                # patch both JSR operands
        b1295 = b1289
        if b1461[3 + x] == 0:                      # pattptr == 0: sequencer
            ptr = voice[voice[x/7].b148B].b15A3
            ptr[1] = voice[voice[x/7].b148B].b15A6
            t3 = T1875[((ptr[1] << 8) | ptr) + b1461[x]]
            if t3 < $FF: ...                       # LOOPSONG / TRANS / pattern
            voice[x/7].cursor_148C = a134
            b1461[x] = (y41 + 1)
        t9 = voice[x/7].cursor_1490                # instrument
        voice[x/7].b14BA = T16BD[t9]               # gatetimer, 1-based column
        if b1461[$17 + x] == 0:                    # no new note
            p_1291(x=x, r5=t9)                     # -> the tick-0 command
            return
        else:
            voice[x/7].freq_lo_idx = (b1461[$17 + x] - $60)
            ...                                    # load the instrument
            p_1256(a=a116, x=x, r4=t9)
            return
    else:
        if voice[x/7].timer_2 >= 0:                # ticks 1..n
            p_1297(x=x)                            # -> mt_waveexec
            return
        else:                                      # went past 0: reload
            voice[x/7].timer_2 = voice[x/7].b148D  # tempo
            p_1297(x=x)
            return

p_1291(x, r5):                           # $1291, 2,688 calls
    switch b1295:                        # the patched JSR: 14 arms
        case $1006:
            p_1013(x=x, y=r5)
            p_1297(x=x)
            return
        ...
        case $1044:
            trap 'unverified'            # a table entry the song never used
        ...
p_1297: mt_waveexec   p_1327: mt_wavenote   p_1339: storefreqhi
p_1363/p_1373: the pulse program   p_1382: the row fetch + hard restart
p_1406/p_140F: pattptr, then ghost[x/7].ctrl = wave & gate
```

## 6. What remains

- **Blocks A+B are one 42-byte region.** `init` zeroes them with one 42-byte loop,
  so the access relation joins them; the play-time accesses are stride 7 and would
  split them into two 7-field records. A per-phase stride view would recover that,
  and would also give the block a role again (over eight elements it keeps its
  address, `b1461[$17 + x]`, which is honest but wordless).
- **Names are role-derived.** `timer_2` is the row counter, `cursor_1490` the
  instrument, `b148D` the tempo: the trace shows the shapes, not the words. A
  family dictionary keyed on the GT2 signature would name them from `player.s`,
  and would name the promoted tails (`p_140F` is `mt_loadregs`).
- **Two of the 16 tick-0 entries are still missing.** `jumptab` enumerates the
  arms the table carries inside the region's observed extent (14 of 16 here, 4 of
  5 effects); the last entries are bytes no accessor ever reached, so the region
  does not contain them.
- **12 (15) `trap 'untaken'` arms.** Branch directions the song never takes
  (REPEAT counts, instrument classes, the filter-time path). They are the
  trace-closed product, not a gap: a static closure would lift them unverified.
- **The calculated-speed shift prints as its loop.** `mt_calculatedspeed` shifts a
  16-bit difference right `n` times; the printed form is the `while True:` the
  6510 runs, not `>> n`, because the count is an SMC cell rather than a constant.
