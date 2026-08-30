# Prototype: the tuneprog decompiler on GoatTracker 2 — results

Third exemplar of [tuneprog-architecture.md](tuneprog-architecture.md), after
*Automatas* ([prototype-automatas.md](prototype-automatas.md)) and *Ghouls'n'Ghosts*
([prototype-follin.md](prototype-follin.md)):
`MUSICIANS/L/Linus/Je_suis_Linus_le_salaud.sid` and `MUSICIANS/L/Linus/Do_It_Again.sid`,
two builds of GoatTracker V2.73 (anatomy [§3.3](playroutine-anatomy.md)). GT2 is 12 % of
HVSC. Both carry a **complete** certificate: every SID write of every call from init to the
first state repeat, plus the periodicity witness.

## 1. What GT2 stresses

| design mechanism | how GT2 stresses it |
|---|---|
| SID image role (S6) | 25 ghost bytes `$14CA..$14E2` flushed first thing every call by `LDX #$18; LDA $14CA,X; STA $D400,X; DEX; BPL`; all other writes go to the ghost |
| computed control from SMC (S2) | low byte of a `JSR`/`JMP` patched: `LDA $144A,Y; STA $1289; STA $1295` + `JSR $10xx` (16 tick-0 commands, one page), `LDA $145A,Y; STA $131E` + `JMP $10xx` (5 continuous effects), `$1445` (wavetable) |
| SMC immediates as globals (S2/S3) | 11 one-byte cells inside instructions: init-pending `$110D`, filter step/time/cutoff/ctrl/type, volume, effect number, vibrato compare, two calculated-speed shifts |
| index domains → struct views (S3/S6) | `X = voice*7` is both SID register offset and record offset: five 7-field blocks `$1461/$1476/$148B/$14A0/$14B5` plus the ghost |
| voice loop (S5) | `JSR mt_execchn; LDX #7; JSR; LDX #$0E; <fall through>` — three calls, the third a tail call |
| 1-based tables (S3/S6) | read at `base-1,Y` (`LDA $16F8,Y` ⇒ wavetbl `$16F9`), instrument columns at `base-1+30k` |
| shared tails, mid-entry (S5) | voice paths end `JMP $140F`; effect handlers `JMP`-entered and -exited; `mt_wavenote`, `mt_done`, `storefreqhi` entered from three places each |
| known-flag branches (S4) | `LDA #$00; BEQ`, `ORA #$F0; BNE`, `BNE` after `INC` |
| three-way phase (S5) | `DEC counter,X; BEQ tick0; BPL effects; <reload>` in six bytes |

## 2. Ground truth (anatomy §3.3 + measurement)

| fact | Je suis Linus | Do It Again |
|---|---|---|
| container | PSID, load `$1000`, init `$1000`, play `$1003`, 1 subtune, 2:15 | PSID, load `$AC00`, play `$AC03`, 1 subtune, 2:52 |
| cadence | 50 Hz video, 19656 cycles/tick, one entry (`sub`) | same |
| build | GHOSTREGS + BUFFEREDWRITES, no sfx, no author text | plus `VOLSUPPORT` and the author-info layout |
| executed | 433 instructions in 3000 frames (+32 reachable only) | 425 |
| SMC | 12 sites, 14 patched cells | fewer (no `mt_setmastervol` patch) |
| song loop | orderlists end `FF 00`, repeats at 2:15 | repeats at 2:52 |

## 3. Symptoms and generic fixes

Verified by hermetic snippet tests; no GT2 special case in the pipeline.

| symptom on GT2 | generic fix | where |
|---|---|---|
| ghost block printed `timer_4[v]`, flush loop `sid[v].freq_lo = timer_4[v]` | a region a loop copies byte-for-byte into `$D400..` is a **SID image**, so accesses print by the register mirrored (`ghost.reg[v]`, `ghost[x/7].ctrl`); an index elsewhere walking a 7-byte record is a voice | `recover.image_copy`, `facts.scales`, `printer.regcell` |
| `T16F9[$16F8 + y]` — operand printed, not index | a region's **origin** is operand + smallest index observed, and indices print from it, so a 1-based table reads `T[y]` and its sibling `T[y + 1]` | `regions._origin`, `Rgn.zero`, `printer.addr_of` |
| the three `execchn` calls printed separately, threading `sp` | a store forwards to every read it dominates over one pure address (collapsing `PHA`/`PLA`) and a JSR frame push is not a unit, so the fall-through third call joins the run | `texture.stack_temps`, `unroll` |
| 21 `goto`s in `execchn` | a region several jumps reach and nothing leaves **is a procedure**: promoted, crossing registers are its parameters, jumps become tail calls, rolled back if the residue does not drop | `tails.promote_tails` |
| `if t1 == 1:` for `DEC counter,X; BEQ` | an arm renders from the state its test saw, so `x == k` prints as the cell holding `x - k` against zero: `if voice[v].counter == 0` | `printer.arms`, `printer.expr` |
| `if (a10 \| $F0) == 0: trap 'untaken'` | `ORA #imm` with a bit set is never zero, so the branch folds and the dead arm goes | `idioms.fold` |
| `T1876[(p + (y + 1))/22]` mis-parenthesised | a stride-scaled index keeps its parentheses | `printer.index` |

The residualised operand cell, the `switch` over its observed targets, that switch's static
closure from the table its writer copies, and clone-per-entry procedures arrived with the
two earlier exemplars and fired on GT2 unchanged.

## 4. Results

Certificates `docs/certificates/gt2-je-suis-linus.json`, `gt2-do-it-again.json` from
`tools/tuneprog_certify.py TUNE --out DIR --until-period --resume`; printed forms in each
output directory's `tuneprog.md`; `tests/tuneprog/test_hvsc_goattracker.py` asserts every
row at 30 s / 20 s.

| id | claim | Je suis Linus | Do It Again |
|---|---|---|---|
| G1 | per-call equivalence, init to first state repeat | **0** divergences over 8,236 calls, 0 envelope traps | **0** over 9,956 |
| G2 | periodicity witness, `complete: true` | period **6,720** calls (134.4 s = HVSC length), first repeat at 8,235 | **8,640** (172.8 s), at 9,955 |
| G3 | front end | 437 sites, 73 regions (29 state, 43 const), 14 procedures, 245 blocks, 580 statements | 433, 73, 14, 234, 569 |
| G4 | ghost image | one `sid_image` region `$14CA`, 25 bytes, delta `$D400-$14CA`; flush prints `for v in 24..0: sid.reg[v] = ghost.reg[v]`, per-voice writes `ghost[x/7].ctrl` | same at `$B0F5` |
| G5 | patched low-byte dispatch | `switch` over `$1289`/`$1295`: 9 dispatched tick-0 handlers plus the table's entries (**14** arms, 7 `trap 'unverified'`), `$131E` 4 of 5 effects, `$1445` 1; every high byte constant, no default | 13 arms, 4 effects |
| G6 | SMC immediates | **14** play-written cells, each a `load` at its instruction; 10 one-byte scalars, three named by role (`cursor_1141`, `timer`, `acc`) | 13 cells |
| G7 | voice loop | `for v in 0, 1, 2: row_apply(x=(v * 7))` — three calls print once | same |
| G8 | per-voice records | `voice[3]` stride 7, 12 fields (blocks C/D/E); blocks A+B stay one 42-byte region (init zeroes them in one loop) | same |
| G9 | 1-based tables | **18** regions with origin `base-1` (wavetbl `$16F9`, notetbl, nine instrument columns stride 30, pulse/filter/speed columns), 30 with origin below base | 17 |
| G10 | structuring | **0** `goto`, **0** `sp`, 12 `trap 'untaken'` in 1,202 printed lines | 0, 0, 15 |
| G11 | cost | trace 12,000 calls 23 s CPU, verify 8,236 in 0.8 s (10,445 calls/s), front end + presentation ~1 s; one invocation, under the 60 s budget | trace 24 s, verify 1.0 s |
| G12 | genericity | Automatas, Commando, Ghouls'n'Ghosts, Emomyst certify unchanged by the same code (`tests/tuneprog/test_hvsc*.py`, 21 tests); `docs/certificates/automatas.json` (149,025 calls) and `commando-song1.json` (11,780) reproduce field for field | — |

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

## 6. The layer above

[prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md) is
both tunes transliterated by hand into a trackerprog
([prototype-trackerprog.md](prototype-trackerprog.md) §3) and rendered by the
same universal player as Commando, with no branch on the family: 0 divergences
over both horizons, the write lists identical rather than permuted (a ghost
flush is exactly what §2's reduction was written to tolerate), and G2's loop
claim re-verified on the render.

## 7. What remains

- **Blocks A+B print as six records, not two.** `init` zeroes them in one 42-byte loop, so
  the access relation joins them into one region; the tick's 7-byte stride is what
  `views.field_split` splits by, making `b1461[$17 + x]` = `rec[x/7 + 3].f02` (element 3 =
  voice 0 of block B). The split is per access: a cursor not stepping by seven keeps the
  block address. Fields are `f00`..`f06` where no role reaches them.
- **Names are role-derived.** `timer_2` = row counter, `cursor_1490` = instrument, `b148D` =
  tempo. A family dictionary keyed on the GT2 signature would name these and the promoted
  tails (`p_140F` = `mt_loadregs`) from `player.s`.
- **2 of the 16 tick-0 entries are missing.** `jumptab` enumerates arms inside the region's
  observed extent (14 of 16, 4 of 5 effects); the last entries are bytes no accessor reached.
- **12 (15) `trap 'untaken'` arms** — branch directions the song never takes (REPEAT counts,
  instrument classes, the filter-time path); trace-closed, and a static closure would lift
  them unverified.
- **The calculated-speed shift prints as its loop.** `mt_calculatedspeed` shifts a 16-bit
  difference right `n` times; the count is an SMC cell, not a constant, so the printed form
  is `while True:`, not `>> n`.
