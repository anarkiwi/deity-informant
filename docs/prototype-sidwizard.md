# Prototype: the tuneprog decompiler on SID Wizard — results

Fourth exemplar of [tuneprog-architecture.md](tuneprog-architecture.md), after
*Automatas* ([prototype-automatas.md](prototype-automatas.md)), *Ghouls'n'Ghosts*
([prototype-follin.md](prototype-follin.md)) and GoatTracker 2
([prototype-goattracker.md](prototype-goattracker.md)): `MUSICIANS/H/Hermit/Emomyst.sid`
(SW 1.6) and `MUSICIANS/H/Hermit/End_of_the_World.sid` (SW 1.9), two exports of Hermit's
player (anatomy [§3.4](playroutine-anatomy.md)). Both carry a **complete** certificate:
every SID write of every call from init to the first state repeat, plus the periodicity
witness.

## 1. What SID Wizard stresses

| design mechanism | how SID Wizard stresses it |
|---|---|
| SMC as relocation (S2/S4) | position-independent blob: `init` rewrites 30–36 **address operands** through a data-driven loop (`DataPtr` × `PtrValu`, `operand := blob[slot] + base + addend`) via `(zp),Y`; every table read in the tick is an init-patched instruction |
| SMC as a variable (S2/S3) | 20–25 play-written immediates: volume, filter band/resonance/route/cutoff, keyboard tracking, the filter's owner voice, its table index and sweep counter (`INC $15DD`, read-modify-write of an operand byte) |
| computed control (S2) | **three** dispatchers, three encodings: two always-taken branches whose offset byte comes from a table (`CLC; LDA T,X; STA INDEXJ+1; BCC *+2`), and a `JMP` whose operand comes from a 31-entry word table |
| voice loop (S5) | `LDX #14; JSR DOTRACK; LDX #7; JSR; LDX #0; JSR` — three calls differing by a constant that steps *down* |
| the stack as data (S4) | `PHA`/`PLA` for the hard-restart tick number and the ADSR nibble, `PHP`/`PLP` to carry the 11-bit cutoff's fraction overflow, (1.9) a zero-page save around the play call |
| flag argument (§6.2) | `HARDRST` takes the tick number in `A` and ANDs it with the instrument's control byte: the tick *is* the bit mask |
| phase (S5) | `SPDCNT` counts 0..tempo−1 with a `SEC; SBC TEMPOTBL−1,Y; BEQ; BVC` test — the V flag as a value |
| dead-but-present code (§7) | Emomyst carries SFX and a slowdown dither whose first frame returns without playing; 1.9 carries a multispeed entry nothing calls |

## 2. Ground truth (anatomy §3.4 + measurement)

| fact | Emomyst | End of the World |
|---|---|---|
| container | PSID, load/init $1000, play $1003, 1 subtune, 1:51 | PSID, load $2900, init $34F0, play $2903, 1 subtune, 2:33 |
| build | SW 1.6 SWP export, SLOWDOWN + SFX, ZEROPAGESAVE off ($02/$03) | SW 1.9, SUBTUNES + MULTISPEED + FILTSHIFT, ZEROPAGESAVE ($FE/$FF) |
| cadence | 50 Hz video, 19,656 cycles/tick, one entry | same |
| executed | 859 sites | 821 |
| SMC | 79 cells: 25 play-written, 54 init-written | 77: 20 and 57 |
| song loop | orderlists end `FF 02` | `FF 00` |

## 3. Symptoms and generic fixes

Verified by hermetic snippet tests (`tests/tuneprog/test_frame.py`, `test_jumptab.py`); no
SID Wizard special case in the pipeline.

| symptom | generic fix | where |
|---|---|---|
| `tick(sp, i, d)`, `row_apply(x, sp, i, d)`: `sp` and two flags in every signature, so the three `DOTRACK` calls were three shapes | a stack pointer is its entry value plus the procedure's own pushes and pops, so each access names an exactly-aliased **frame slot**; a push and the pops reading it are one value, and the slot's write goes unless a callee can read a foreign frame | `frame.py` (new) |
| the flag byte survived: `PLP` restores `C` from the byte `PHP` packed, which reads `I`, `D`, `V`, `N`, `Z` | a value read one bit at a time gains that bit **where it is defined**, so nothing reads the packed byte | `idioms.bitfields` |
| the `PLA` value was read after the `JSR` frame overwrote its slot | a JSR frame is memory: a raw store clobbers the slots it covers | `inline._clobbers` |
| `ptr = T2478[i] + b10A2; ptr[1] = T248D[i] + b10A3 + carry(…)` | a 16-bit pair may live in **one** region, and a word operand read from a const table or init-only cell is named by it, not called a step | `word._plan`, `word._basename` |
| the two branch dispatchers were bare switches over observed targets (3 and 6 arms of 8 and 14) | a patched *branch* is a switch like a patched `JMP`: arms are `site + 2 + sext(table[i])` | `jumptab._cell` |
| the jump dispatcher had 2 arms of 31 | a table runs from its own bytes to the nearest instruction or foreign access, stepped by the layout its halves imply, clamped to an unsigned index's reach; an entry addressing a byte some access reads is data, not a target | `jumptab.spans`, `jumptab._domain` |
| `FREQTBH` printed as a SID shadow flushed to `$D401` (1.6 reads it with the voice offset) | a table is read, never a shadow: only a `state` region takes the image role | `recover.image_copy` |
| `sid.reg[5 + x]` where GoatTracker prints `sid[v].ad`; the voice index also reaches a 14-byte constant pair | an index walking records of several sizes steps by their **gcd** | `facts.scales` |
| `(($1953 + T1934[a]) - (T1934[a] << 1))` — the `& $80` of a sign extension dropped | `x & $80` prints | `printer.expr` |
| `T19F2[(((a >> 1) >> 1) >> 1) >> 1]`, `row_apply(x=($E + (v * -7)))` | a shift chain is one shift; a run whose constants step down prints as a subtraction | `idioms.fold`, `unroll._Ctx.hole` |

The relocation needed nothing new: the operand-cell rule and the init-only fold arrived with
Follin (design S2) and here carry 31 table reads per tick.

## 4. Results

Certificates `docs/certificates/sw-emomyst.json`, `sw-end-of-the-world.json` from
`tools/tuneprog_certify.py TUNE --out DIR --until-period --resume`; printed forms in each
output directory's `tuneprog.md`; `tests/tuneprog/test_hvsc_sidwizard.py` asserts the rows
below at 30 s / 20 s.

| id | claim | Emomyst | End of the World |
|---|---|---|---|
| W1 | per-call equivalence, init to first state repeat | **0** divergences over 8,084 calls, 0 envelope traps | **0** over 14,465 |
| W2 | periodicity witness, `complete: true` | period **6,120** calls (122.4 s), first repeat at 8,083 | **7,688** (153.8 s = the HVSC 2:33), at 14,464 |
| W3 | front end | 859 sites, 96 regions, 15 procedures, 365 blocks, 1,054 statements | 821, 94, 16, 361, 1,050 |
| W4 | init-time relocation | 54 init-only cells over **39** instructions, **31** in the tick: constant there, none folded inside `init` (17 cells still load there) | 57 cells, 41 sites, 25 in the tick; 25 loads in `init` |
| W5 | the fixup loop stays a loop | one `while True:` over `DataPtr`/`PtrValu`, 5 stores through `(zp),Y`, not 30 unrolled | same |
| W6 | runtime blob base | **6** pointer sets print `ptr = T2478[i] + base`, one 16-bit view each over the word table and over `SWP_OFFSET` | 6 |
| W7 | patched immediates | **25** play-written cells, each a load at its own instruction; `res_route`, `res_route_or`, `mode_vol`, `cutoff_hi`, `cutoff_lo` named by the register reached, `timer += 1` for `INC $15DD` | 20 |
| W8 | branch dispatch | `switch (($1953 + T1934[a]) - ((T1934[a] & $80) << 1))`: NOTEFXTBL 8 entries, **7** distinct targets (4 unverified); SMALLFXTBL 14, **14** arms (7 unverified) | 15 and 16 arms |
| W9 | jump dispatch | `switch b19D0`: BIGFXTABLE 31 words, **25** distinct in-band targets (21 unverified; six dead handlers share one `RTS`) | 25 |
| W10 | the voice loop | `for v in 0, 1, 2: row_apply(x=($E - (v * 7)))` — the three `LDX #n; JSR DOTRACK` print once | same |
| W11 | the slowdown gate | first play call writes **nothing**; the dither reload folds to `phase = $FF` | absent (no SLOWDOWN in 1.9) |
| W12 | the stack | **0** `sp`, **10** forwarded frame slots; `PHP`/`PLP` leaves only the carry | **0** `sp`, **12** slots; zero-page save prints `saved = ptr … ptr = saved` around the tick |
| W13 | structuring | **0** `goto`, 46 `trap 'untaken'`, 32 `trap 'unverified'` in 1,419 printed lines | 0, 51, 46 in 1,394 |
| W14 | cost | trace 12,000 calls 32 s CPU, verify 8,084 in 0.9 s (8,617 calls/s): **one** invocation inside the 45 s budget | trace 16,000 in 33 s, verify 14,465 in 1.3 s |
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

## 6. The layer above

[prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md) is both
builds transliterated by hand into a trackerprog
([prototype-trackerprog.md](prototype-trackerprog.md) §3) and rendered by the
same universal player as Commando and GoatTracker 2, with no branch on the
family: 0 divergences over both horizons, the write lists identical rather than
permuted (and earned rather than free, because this family has no ghost flush),
and both loop claims re-verified on the render. Its §8 records what the two
builds' difference really is — `commit_order` for the sound, nine build flags
for everything else — and its §7 the poison table saying what each datum is
worth in ticks.

## 7. What remains

- **`b1024` prints as fifteen records.** `init` zeroes VARIABLES in one loop, so the access
  relation joins all five bunches into one region; the tick's `abs,X` with X ∈ {0,7,14} is
  the stride `views.field_split` splits by, so `b1024[$16 + x]` is `rec[x/7 + 3].timer_2`
  (bunch 1, voice x/7). Roles (`timer`, `acc`, `cursor`) name five of the seven fields.
- **Names are role-derived.** `timer` is CWEPCNT, `freq_idx` CKBDTRK, `b1464` TABLRST. A
  family dictionary keyed on the SID Wizard signature would name them from `player.asm`.
- **The branch dispatchers print `switch ($1A15 + sext(saved17))`** (corrected from
  `switch (($1A15 + saved17) - ((saved17 & $80) << 1))`): subtracting `$100` exactly when
  bit 7 is set is sign extension, an identity over eight bits. It lives in the printer
  (`idioms.sext_of`, `pseudocode.expr`); `idioms.fold`, which S4 runs, is untouched, so the
  certified IR does not move.
- **A procedure's return value does not print.** `a8 = p_19DB()` reads a value whose
  procedure ends in a bare `return`: `ir.retval` recovers only the tick's own return.
- **46 (51) `trap 'untaken'` arms** are branch directions these songs never take (gate-off
  pointers, `$FE` table jumps, HR type `$18`, the SFX suppression); **32 (46)
  `trap 'unverified'`** are table entries no row selected. Both are trace-closed.
- **The tempo test prints `overflow(A - M)`** (corrected from
  `((A ^ M) & (A ^ (A - M))) < 0`; the `if (tempo & $80)` form is refuted). `BVC` after `SBC`
  is the V flag as a value, in the same vocabulary as `carry(x + y)`. V is
  `sign(A^M) & sign(A^R)`, which collapses to one operand's sign bit only given a range proof
  on both operands that the trace does not supply.
