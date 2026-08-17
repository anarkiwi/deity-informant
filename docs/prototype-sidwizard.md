# Prototype: the tuneprog decompiler on SID Wizard — results

The fourth exemplar of [tuneprog-decompiler-design.md](tuneprog-decompiler-design.md),
after defMON's *Automatas* ([prototype-automatas.md](prototype-automatas.md)),
Follin's *Ghouls'n'Ghosts* ([prototype-follin.md](prototype-follin.md)) and
GoatTracker 2 ([prototype-goattracker.md](prototype-goattracker.md)):
`MUSICIANS/H/Hermit/Emomyst.sid` (SW 1.6) and
`MUSICIANS/H/Hermit/End_of_the_World.sid` (SW 1.9), two exports of Hermit's SID
Wizard player (anatomy [§3.4](playroutine-anatomy.md)). Both carry a **complete**
certificate: every SID write of every call from init to the first state repeat,
plus the periodicity witness.

## 1. Why SID Wizard, and what it adds

| design mechanism | how SID Wizard stresses it |
|---|---|
| SMC as relocation (S2/S4) | the exporter emits a *position-independent* music blob, so `init` rewrites 30–36 **address operands** through a data-driven loop (`DataPtr` × `PtrValu`, `operand := blob[slot] + base + addend`) written through `(zp),Y`. Every table read in the tick is an instruction the init patched |
| SMC as a variable (S2/S3) | 20–25 play-written immediates: volume, filter band/resonance/route/cutoff, keyboard tracking, the filter's owner voice, its table index and its sweep counter (`INC $15DD` — a read-modify-write of an operand byte) |
| computed control (S2) | **three** dispatchers in three encodings: two always-taken branches whose *offset byte* comes from a table (`CLC; LDA T,X; STA INDEXJ+1; BCC *+2`), and a `JMP` whose operand comes from a 31-entry word table |
| voice loop (S5) | `LDX #14; JSR DOTRACK; LDX #7; JSR; LDX #0; JSR` — a run of three calls whose only difference is a constant that steps *down* |
| the stack as data (S4) | `PHA`/`PLA` for the hard-restart tick number and the ADSR nibble, `PHP`/`PLP` to carry the 11-bit cutoff's fraction overflow, and (1.9) a zero-page save around the whole play call |
| flag argument (§6.2) | `HARDRST` takes the tick number in `A` and ANDs it with the instrument's control byte: the tick *is* the bit mask |
| phase (S5) | `SPDCNT` counted 0,1,2,…tempo−1 with a `SEC; SBC TEMPOTBL−1,Y; BEQ; BVC` tempo test — the V flag as a value |
| dead-but-present code (§7) | Emomyst carries SFX and a slowdown dither whose first frame returns without playing; 1.9 carries a multispeed entry nothing calls |

## 2. Ground truth (anatomy §3.4 + measurement)

| fact | Emomyst | End of the World |
|---|---|---|
| container | PSID, load/init $1000, play $1003, 1 subtune, 1:51 | PSID, load $2900, init $34F0, play $2903, 1 subtune, 2:33 |
| build | SW 1.6 SWP export, SLOWDOWN + SFX, ZEROPAGESAVE off ($02/$03) | SW 1.9, SUBTUNES + MULTISPEED + FILTSHIFT, ZEROPAGESAVE ($FE/$FF) |
| cadence | 50 Hz video, 19,656 cycles/tick, one entry | same |
| executed | 859 sites | 821 |
| SMC | 79 cells: 25 play-written, 54 written by init only | 77: 20 and 57 |
| song loop | orderlists end `FF 02`, so the tune repeats | `FF 00` |

## 3. What broke, and the generic fix

Everything below is a *mechanism*, verified by hermetic snippet tests
(`tests/tuneprog/test_frame.py`, `test_jumptab.py`); no SID Wizard special case
exists anywhere in the pipeline.

| symptom | generic fix | where |
|---|---|---|
| `tick(sp, i, d)`, `row_apply(x, sp, i, d)`: the stack pointer and two flags threaded through every signature, so the three `DOTRACK` calls were three different shapes and never folded | a procedure's stack pointer is its entry value plus its own pushes and pops, so every stack access names a **frame slot** and aliasing is exact; a push and the pops that read it are one value (two pushes one pop can read are one value, the phi a branch left), and the slot's write goes unless something the procedure calls can read a frame that is not its own. Nothing then reads the stack pointer | `frame.py` (new) |
| the flag byte survived even so: `PLP` restores `C` out of the byte `PHP` packed, which reads `I`, `D`, `V`, `N`, `Z` | a value read one bit at a time gains that bit **where it is defined**, so each flag comes back as the value that push held and the packed byte is read by nothing | `idioms.bitfields` |
| the value a `PLA` produced was read *after* the `JSR` frame that overwrote its slot | a JSR frame is memory: a raw store clobbers the slots it covers | `inline._clobbers` |
| `ptr = T2478[i] + b10A2; ptr[1] = T248D[i] + b10A3 + carry(…)` | a 16-bit pair may live in **one** region (a zero-page pointer, a per-voice accumulator inside one 105-byte block); a word operand read from a const table or an init-only cell is named by it, not called a step | `word._plan`, `word._basename` |
| the two branch dispatchers were bare switches over their observed targets (3 and 6 arms of 8 and 14) | a patched *branch* is a switch like a patched `JMP`: its arms are `site + 2 + sext(table[i])` | `jumptab._cell` |
| the jump dispatcher had 2 arms of 31: the table's other bytes were outside the region any accessor reached | a table runs from its own bytes out to the nearest instruction or foreign access (from the exact address sets, so an interleaved word table grows past the column it alternates with), stepped by the layout its two halves imply, clamped to what an unsigned index register can reach; an entry addressing a byte some access reads is data, not a target | `jumptab.spans`, `jumptab._domain` |
| `FREQTBH` printed as a SID shadow flushed to `$D401` (1.6 reads it with the *voice offset*, which looks like an image copy) | a table is read, never a shadow: only a `state` region takes the image role | `recover.image_copy` |
| `sid.reg[5 + x]` where GoatTracker prints `sid[v].ad` — the voice index also reaches a 14-byte constant pair | an index that walks records of several sizes steps by their **gcd** | `recover._scales` |
| `(($1953 + T1934[a]) - (T1934[a] << 1))` — the printer dropped the `& $80` of a sign extension | `x & $80` prints | `printer.expr` |
| `T19F2[(((a >> 1) >> 1) >> 1) >> 1]`, `row_apply(x=($E + (v * -7)))` | a shift chain is one shift; a run whose constants step down prints as a subtraction | `idioms.fold`, `unroll._Ctx.hole` |

The relocation itself needed nothing new: the operand-cell rule and the
init-only fold arrived with Follin (design S2), and here they carry 31 table
reads per tick.

## 4. Results (measured)

Certificates: `docs/certificates/sw-emomyst.json`, `sw-end-of-the-world.json`,
from `tools/tuneprog_certify.py TUNE --out DIR --until-period --resume`; printed
forms in each output directory's `tuneprog.md`. The HVSC tests
(`tests/tuneprog/test_hvsc_sidwizard.py`) assert the rows below at 30 s / 20 s.

| id | claim | Emomyst | End of the World |
|---|---|---|---|
| W1 | per-call equivalence from init to the first state repeat | **0** divergences over 8,084 calls, 0 envelope traps | **0** over 14,465 |
| W2 | periodicity witness, `complete: true` | period **6,120** calls (122.4 s), first repeat at call 8,083 | **7,688** (153.8 s = the HVSC 2:33), at 14,464 |
| W3 | front end | 859 sites, 96 regions, 15 procedures, 365 blocks, 1,054 statements | 821, 94, 16, 361, 1,050 |
| W4 | init-time relocation | 54 init-only cells over **39** instructions, **31** of them in the tick: every one a constant there, and none folded inside `init` (17 cells still load there) | 57 cells, 41 sites, 25 in the tick; 25 loads in `init` |
| W5 | the fixup loop stays a loop | one `while True:` over `DataPtr`/`PtrValu` with 5 stores through `(zp),Y`, not 30 unrolled stores | same |
| W6 | runtime blob base | **6** pointer sets print `ptr = T2478[i] + base`, one 16-bit view each over the word table and over `SWP_OFFSET` | 6 |
| W7 | patched immediates | **25** play-written cells, each a load at its own instruction; `res_route`, `res_route_or`, `mode_vol`, `cutoff_hi`, `cutoff_lo` named by the register they reach, `timer += 1` for `INC $15DD` | 20 |
| W8 | branch dispatch | `switch (($1953 + T1934[a]) - ((T1934[a] & $80) << 1))`: NOTEFXTBL's 8 entries, **7** distinct targets (4 unverified); SMALLFXTBL's 14, **14** arms (7 unverified) | 15 and 16 arms |
| W9 | jump dispatch | `switch b19D0`: BIGFXTABLE's 31 words, **25** distinct in-band targets (21 unverified; six dead handlers share one `RTS`) | 25 |
| W10 | the voice loop | `for v in 0, 1, 2: row_apply(x=($E - (v * 7)))` — the three `LDX #n; JSR DOTRACK` print once | same |
| W11 | the slowdown gate | the first play call writes **nothing**; the dither reload folds to `phase = $FF` | absent (no SLOWDOWN in 1.9) |
| W12 | the stack | **0** `sp`, **10** forwarded frame slots; `PHP`/`PLP` leaves only the carry | **0** `sp`, **12** slots; the zero-page save prints `saved = ptr … ptr = saved` around the tick |
| W13 | structuring | **0** `goto`, 46 `trap 'untaken'`, 32 `trap 'unverified'` in 1,419 printed lines | 0 `goto`, 51, 46 in 1,394 |
| W14 | cost | trace 12,000 calls in 32 s CPU, verify 8,084 in 0.9 s (8,617 calls/s): **one** invocation inside the 45 s budget | trace 16,000 in 33 s, verify 14,465 in 1.3 s |
| W15 | genericity | Automatas, Commando, all 32 Ghouls'n'Ghosts subtunes and both GoatTracker tunes certify with the same code (31 `hvsc` tests) | — |

## 5. The printed tuneprog (verbatim, `...` elides)

```
meta      entry sub $1003 every 19656 cycles (1.0 calls/frame, pal_video)
          certified 8,084 calls, 0 divergences, period 6,120, first repeat at
          call 8,083 (complete), stage S6
state     voice[3] stride 7, 4 fields (the CONST_VAR triples)
          ptr $0002 u16 ptr ; base $10A2 u16 (SWP_OFFSET, init-only)
          T244E/T2478 u16 tables (INSPTLO|HI, PPTRLO|HI, lo|hi $2463/$248D)
          phase $10A4 (SLOWDCNT) ; res_route res_route_or mode_vol cutoff_hi
          cutoff_lo freq_idx timer b1464 b146E b155B b171C b19D0 b19E3 (the
          25 play-written immediates) ; b1024 105 bytes (VARIABLES)
const     FREQ $1859 (12-TET) ; T1C6A patterns ; T218F.. instrument columns ;
          T1934 NOTEFXTBL ; T19F2 SMALLFXTBL ; T1A70 BIGFXTABLE (u16, 1A6E-based)

tick():                                  # $1003, 12,000 calls
    t1 = phase                                     # LSR SLOWDCNT
    t2 = (t1 & 1)
    phase >>= 1
    if phase == 0:
        phase = $FF                                # the dither pattern, folded
    if t2 == 0:
        return                                     # frame 0 plays nothing
    else:
        for v in 0, 1, 2:                          # LDX #14/7/0; JSR DOTRACK
            row_apply(x=($E - (v * 7)))
        sid.res_route = (res_route | res_route_or)
        sid.mode_vol = ($F | mode_vol)
        if freq_idx == 0:                          # CKBDTRK: filter kbd tracking
            a11 = freq_idx
            c6 = 0
        else:
            a11 = FREQ[$E + (freq_idx + b1024[$2C + b1024_idx])]
            c6 = carry(freq_idx + b1024[$2C + b1024_idx])
        sid.cutoff_hi = ((a11 + cutoff_hi) + c6)
        sid.cutoff_lo = cutoff_lo
        return

row_apply(x):                            # $124D DOTRACK, 35,997 calls
    t1 = b2437                                     # TEMPOTBL[TMPPOS]
    if b1024[$16 + x] == t1: trap 'untaken'        # BEQ: a row never ends here
    if ((b1024[$16 + x] ^ t1) & (b1024[$16 + x] ^ (b1024[$16 + x] - t1))) < 0:
        t3 = b1024[$54 + x]                        # BVC: the tempo program loops
        b1024[$16 + x] = 0                         # SPDCNT = 0
        b1024[$55 + x] = t3                        # TMPPOS = TMPPTR
    t4 = b1024[$16 + x]                            # tick = SPDCNT++
    b1024[$16 + x] += 1
    if t4 == 0:                                    # TICK_0: read the row
        ptr = (T2478[b1024[$2A + x]] + base)       # pattern[CURPTN] + blob base
        b1024[$2D + x] = 0                         # CURIFX = CURFX2 = 0
        b1024[$2F + x] = 0
        t5 = b1024[$18 + x]                        # PTNPOS
        if b1024[$15 + x] != 0:                    # PACKCNT: a packed rest
            p_12B5(x=x, r7=t5)
            return
        else:
            t6 = T1C6A[((ptr[1] << 8) | ptr) + t5] # the row's note byte
            ...                                    # $70..$77 = packed rest
    else:
        if (t4 - 2) >= 0:
            if t4 == 2:                            # TICK_2: instrument, note
                ...
                ptr = (T244E[b1024[$2E + x]] + base)
                ...
            else:                                  # ticks 3..n: the tables
                p_1520(x=x)
                return
        else:                                      # TICK_1: advance the position
            ptr = (T2478[b1024[$2A + x]] + base)
            ...
            p_13E2(x=x, y=y83)                     # A = 1 -> HARDRST
            return

p_1337(a, x):                            # $1337 HARDRST, 4,776 calls
    saved8 = a                                     # the tick number, PHA'd
    ptr = (T244E[y40] + base)
    if (saved8 & T218F[(ptr[1] << 8) | ptr]) == 0: # ins[0] & (2 at tick 0, 1 at 1)
        p_1378(x=x)                                # HRENDER: no hard restart
        return
    else:
        b1024[5 + x] = $FE                         # PTNGATE = $FE
        b1024[4 + x] = ($FE & b1024[4 + x])        # WFGHOST &= $FE
        sid[x/7].ad = T218F[((ptr[1] << 8) | ptr) + 1]
        sid[x/7].sr = T2191[((ptr[1] << 8) | ptr) + 2]
        ...
```

## 6. What remains

- **`b1024` prints as fifteen records now.** `init` zeroes VARIABLES with one
  loop, so the access relation still joins all five bunches into one region; the
  tick's `abs,X` with X ∈ {0,7,14} is the stride `views.field_split` splits it by,
  so `b1024[$16 + x]` is `rec[x/7 + 3].timer_2` -- bunch 1, voice x/7 -- and the
  roles the cells carry (`timer`, `acc`, `cursor`) name five of the seven fields.
  What is still missing is the *word*: SPDCNT is `timer_2` because a timer is what
  the trace shows it to be.
- **Names are role-derived.** `timer` is CWEPCNT, `freq_idx` CKBDTRK, `b1464`
  TABLRST: the trace shows the shapes, not the words. A family dictionary keyed
  on the SID Wizard signature would name them from `player.asm`.
- **The two branch dispatchers keep their sign extension.** `switch (($1A15 +
  saved17) - ((saved17 & $80) << 1))` is `INDEXJ2 + 2 + sext(SMALLFXTBL[type])`
  written out; an explicit signed-byte view of a table would print it as one.
- **A procedure's return value does not print.** `a8 = p_19DB()` reads a value
  whose procedure ends in a bare `return`: `ir.retval` only recovers the tick's
  own return, so a callee that computes a byte for its caller shows an empty
  body.
- **46 (51) `trap 'untaken'` arms** are branch directions these songs never take
  (gate-off pointers, `$FE` table jumps, HR type `$18`, the SFX suppression) and
  **32 (46) `trap 'unverified'`** are table entries no row selected. Both are the
  trace-closed product, not a gap.
- **The tempo test prints as its subtraction.** `BVC` after `SBC` is the V flag
  as a value, so the "tempo entry had bit 7 set" test prints as the overflow
  expression rather than as `if (tempo & $80)`.
