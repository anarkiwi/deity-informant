# Prototype: the tuneprog decompiler on Tim Follin's *Ghouls'n'Ghosts*

Second exemplar of [tuneprog-architecture.md](tuneprog-architecture.md), after
defMON's *Automatas* ([prototype-automatas.md](prototype-automatas.md)):
`MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid`, anatomy [§3.6](playroutine-anatomy.md).
All 32 subtunes certified (`docs/certificates/ghouls-song01.json` … `-song32.json`, plus
`ghouls-songs-all.json` for the union); no Follin-specific code path exists.

## 1. Idioms under test

- 24 varying SMC sites: 21 immediates used as variables, 3 dispatch `JMP` operands —
  `init` patches the same cells, plus one of its own (`$29D8`) between two copy loops
- computed control: `LDA T1,X; STA $6375; LDA T2,X; STA $6376; JMP $xxxx`, X = command
  byte ≥ $80, tables at `base−$80`, three voice copies
- computed store: `LDA $622E,X; STA $6219`, then a store *through* the patched operand
  into one of three unequally spaced cells
- structuring: the whole frame is one linear procedure — no `JSR` on the hot path, voice
  *n* ends by `JMP` to voice *n*+1, handlers `JMP` back into their voice's sequencer
- `init` tail-jumps into a rip stub that copies two song blocks over itself, then starts
  either a song or a sound effect
- `play` returns `$FF` while any voice runs, 0 when all three stop
- the 97-entry note table the transpose can index past, into the SFX tables

## 2. Ground truth (anatomy §3.6, measured against the trace)

| fact | value |
|---|---|
| container | PSID v2, load $2980–$733F, init $6110 (A = subtune), play $6234, 32 subtunes, PAL, one call per frame (19,656 cycles), no CIA, no IRQ installed |
| subtunes | 0–10 music (0–5 end with a stop, 6–10 loop), 11–31 sound effects; HVSC lengths 259.3, 102, 12.5, 10, 4, 127, 171.1, 147.5, 138.3, 113, 170.1 s then 0.1–13 s |
| player | $6110–$6CB6 code; voices $6234/$6421/$6610 (493 B each, identical modulo ZP operands +1/+2 and one `CMP #v` insertion at $65C7/$67B6); filter $67FF; 21 handlers × 3 at $6858–$6CB6 |
| state | zero page $21–$97: stride 1 for bytes, stride 2 for the pointer/word pairs; `init` clears it with one `STA $21,X` loop, so the access relation makes it **one 118-byte region** (`b0021` in the print) |
| SMC cells | pulse mode $62EE/$64DB/$66CA, vib dir $6269/$6456/$6645, trill phase $629E/$648B/$667A, fixed length $640F/$65FE/$67ED, filter dir $6800 and bounds $6813/$6819/$682D/$6833, dispatch $6375/$6562/$6751 (2 B each), the SFX store operand $6219, the loader's `CPY #` at $29D8 |
| inputs | none volatile: no `$D012`, `$D41B`, timer or VIC read anywhere |
| SID schedule | voice 0 → 1 → 2 → filter; `$D415`/`$D416` every frame; `init` writes $08 then $00 to `$D400–$D41C` (four writes past the last register) |
| traps present | table overrun (note + transpose past 97 entries), tables at `base−$80`, a store whose address is data, a `BPL` whose own operand byte the block copy overwrites, `LDA #v; BNE` made conditional by SMC, `INC/DEC` floor idiom, frame-count durations with no tempo |

## 3. What broke, and the generic fix

| broke | generic fix |
|---|---|
| envelope trap `$4622 outside [$3D44,$4621]` at `$29DB`: the rip loader at `$2980` patches its own `CPY #$00` at `$29D7` through `STY $29D8`, once per song block, consumed inside `init` | an instruction byte any traced procedure writes, **in any phase**, is a variable (`trace.py`: `cells = code & (written_init \| written_play)`) — it leaves the site key and one site serves both copy loops |
| residualising every init-patched operand would cost SID Wizard its readable tick (Emomyst's `init` patches ~30 operands) | `ssa.Folds` folds a known-address load to the post-init byte when ≥ 1 of its bytes is an SMC cell and none is play-written; `ssa.simplify` applies it only in procedures `init` never reaches. An init-written *variable* is not a cell and keeps its load and name; `--songs all` folds nothing |
| the rip stub's `BPL` at `$7318` sits in the band its own block copy overwrites, so its offset byte is a cell: the branch became a bare computed `switch` and hit `trap 'switch'` on a call that fell through | `cfg.py` emits `if cond: switch(target) else: fall-through`, the taken side's observed targets in the switch (`build._branch_switch`) |
| presentation only (`pseudocode.py`, no IR change) | a SID store whose index does not step by the 7-byte voice block prints `sid.reg[i]`; a table's literal operand moves into the index (`LDA $6C37,X` over a region based at `$6CB7` → `T6CB7[cmd - $80]`); a play entry's `A` prints as `return` when every exit agrees on one computed expression (`ir.retval`) |
| a patched `JMP` whose operand halves come from constant tables under one index left a bare default | the tables' remaining entries are targets too, and become arms that `trap 'unverified'` (`jumptab.py`) |
| `--songs all` | `tracedata.merge` builds one trace from every subtune: sites re-keyed by the union of their cells (a wider cell set can only merge keys, never split them), edges/calls/returns/written sets unioned, each subtune's write log kept. What `init` writes types as `state`, nothing folds, every subtune verifies against its own trace |

**Sibling copies** (`siblings.py`, `copyrows.py`, `copymerge.py`; S2c). The three voices
are copies of one static template, recovered from the post-init image by aligning
instruction streams: equal opcodes advance all three, a gap holds the `CMP #v` voices 1
and 2 carry where voice 0 uses the load's Z flag; dispatch arms pair by their index in the
parallel tables `jumptab.dispatch` reads. Discovery is exact (corrected from ten
thresholds): bases are the chain the built procedures carry, each pair of copies is one
`difflib` alignment in which only a gap may separate them, and the family holds only while
every copy's operand map is a function. Song 1: three voices, 419 rows.

**The copy index is spent before the IR exists** — copy *j* running a template row *is*
that row with `v = j`:

- a disagreeing operand becomes a read-only per-copy column `T_x[v]` (59 columns for song
  1) in a band outside the load image, the stack page and I/O, every byte a pinned input;
- the chain edge from voice *j* to *j*+1 becomes `v += 1; if v < 3: header`;
- a site's count becomes a vector over `v`, a zero marking that statement unverified;
- a row whose copies do not lift to one shape stays three rows under `switch (v)`, as does
  the dispatch, each voice's patched `JMP` holding its own target.

`--no-merge` builds what S2b built.

## 4. Evidence (song 1 unless stated)

| # | claim | measured |
|---|---|---|
| 1 | patched-`JMP` dispatch becomes a switch | three switches on `load16($6375/$6562/$6751)`, **21/23/23 arms** (14/18/15 observed, the rest statically enumerated as `trap 'unverified'`) — the commands the tables carry, `$93`/`$94` included; two of the 23 are the SID Wizard extent rule (a table runs out to the nearest instruction or foreign access, not to the bytes an accessor touched) over-reaching past the 21-entry table |
| 2 | data-dependent SID address | `sid.reg[a327] = b730E[...]` inside the `$85` list loop; the write's envelope is `$D400–$D418`, and `(addr, val)` equality is part of the certificate |
| 3 | computed store operand | in the SFX subtunes' `init`: a store **through** `load16($6219)` whose region is `[$640F, $67ED]` — exactly the three voices' fixed-length cells (song 16) |
| 4 | 32 subtunes from the pre-init image | all 32 certified, 0 divergences, 0 envelope traps; 31 complete via a period (§6) |
| 5 | play returns a value | `return ((b0021[90] \| b0021[91]) \| b0021[92])` = `$7B \| $7C \| $7D`; not part of the certificate |
| 6 | three unrolled voices fold | yes, in the certified program: one family of three copies, 400 of 419 aligned rows folded, 60 per-copy columns; the 19 left are rows whose copies do not lift to one shape or whose successors cross copies. Coverage: 338 statements all three voices, 77 only voice 2, 29 voices 2 and 3, 19 voices 1 and 3, 8 voices 1 and 2 — 133 of 471 merged statements unverified for some voice. S4 falls from 1,229 statements in 450 blocks to 671 in 254, the printed document from 1,421 lines to 794 |
| 7 | SMC immediates as variables | 35 cells (24 play-written, 11 init-patched); every play-written cell is a load at its instruction, every init-only cell is a constant in the tick and a store in `init`; 76 cells in the `--songs all` build |
| 8 | `init` writes $08 then $00 to `$D400–$D41C` | 58 init writes, `$D41C` down to `$D400`, values `{8, 0}` — compared byte for byte by the certificate |
| - | genericity, budget | Automatas (149,025 calls, period 129,024, both SID models), Commando songs 1–2, Emomyst at 10 s: 0 divergences with the same code. Song 1 is traced and verified in one 14 s invocation: 1,177 sites → 68 regions → 4 procedures → 1,242 statements |

## 5. Printed `tick()` (verbatim; `...` elides)

Columns the copies disagree on print as the operand they stand for: affine ones through
the stride vocabulary (`sid[v].freq_lo`, `b640F[v]`), the rest as fields of the group
view. Certified song 1 (12,997 calls): 757 document lines, `tick()` 495 of them, against
794 and 578 before the view pass.

```
program   4 procedures, 176 blocks, 295 statements, 42 regions
copies    1 family over 3 copies, 400 rows; 133 of 471 statements unverified (marked)
certified 12,997 calls, 0 divergences, period 1, first repeat at call 12,996 (complete), ...

voice[3]  per-copy cells, stride 6, 51 fields
  .b0021          $0021 $0023 $0025            # the stream pointer
  .timer          $0022 $0024 $0026
  .pw_lo          $003F $0041 $0043
  .pw_hi          $0040 $0042 $0044
  .freq_lo        $0075 $0076 $0077
  .freq_hi        $0078 $0079 $007A
  .counter_2      $007B $007C $007D            # active[v] = $FF
  .b6269          $6269 $6456 $6645            # the vibrato direction
  .b62EE          $62EE $64DB $66CA            # the pulse mode
  .b6375          $6375 $6562 $6751            # the patched JMP
  .b6C37          $6C37 $6C4C $6C61            # the two arm tables
  ...                                          # 51 fields in all
b0021            $0021 118 bytes               # what is left is not per-voice

tick():                                  # $6234, 48,000 calls
    for v in 0, 1, 2:   # x48,000
        # $6234
        t1 = copies_6234[$13E + (v << 1)]      # the two columns no rule names
        t2 = copies_6234[$144 + (v << 1)]
        if voice[v].counter_2 < 0:
            if voice[v].timer_8 != 0:
                # $623F
                t3 = voice[v].timer_8  # unverified (ran for v = 1)
                voice[v].timer_8 -= 1  # unverified (ran for v = 1)
                if voice[v].timer_8 != 0: trap 'untaken'
                # $6243
                sid[v].freq_lo = voice[v].freq_lo  # unverified (ran for v = 1)
                sid[v].freq_hi = voice[v].freq_hi  # unverified (ran for v = 1)
                if voice[v].b0036 == 0: trap 'untaken'
                # $6251
                sid[v].ctrl = (voice[v].ctrl | 1)  # unverified (ran for v = 1)
            ...
                # $627A                                 # +/- depth by the direction
                sid[v].freq_lo = a156
                sid[v].freq_hi = x18
                voice[v].freq_lo = a156
                voice[v].freq_hi = x18
            ...
            if voice[v].timer_2 == 0:
                while True:   # x2,773                  # the sequencer
                    # $6360
                    t21 = b730E[(voice[v].timer << 8) | voice[v].b0021]
                    if t21 >= 0:
                        break                           # a note byte leaves the loop
                    else:
                        # $6366                         # the patched JMP, tables at base-$80
                        voice[v].b6375 = voice[v].b6C37[t21]
                        voice[v].b6376 = voice[v].b6C76[t21]
                        switch v:                       # each voice dispatches on its own
                            case 0:
                                switch voice[0].b6375:
                                    case $6858:         # $82 loop begin
                                        # $6858
                                        voice[v].timer_3 = b730E[((voice[v].timer << 8) | voice[v].b0021) + 1]
                                        t22 = voice[v].b0021
                                        voice[v].b0030 = (t22 + 2)
                                        voice[v].b0021 += 2
                                        ...
                                        continue
                                    case $68EE:         # $84 fixed note length
                                        # $68EE
                                        b640F[v] = b730E[((voice[v].timer << 8) | voice[v].b0021) + 1]
                                        y10 = 2
                                        goto L6356_98   # back into the sequencer
                                    case $6909:         # $85 raw register list
                                        # $6909
                                        y14 = 1
                                        a75 = b730E[((voice[v].timer << 8) | voice[v].b0021) + 1]
                                        while True:   # x191
                                            # $690B
                                            y16 = (y14 + 2)
                                            sid.reg[a75] = b730E[...]   # the register is a variable
                                            ...
                            case 1:
                                switch voice[1].b6375:
                                    case $6871:
                                        goto L6858_B1   # the same body, voice 2's target
                                    case $68B2:
                                        goto L68A3_C6
                                    ...
                                    case $6ADD:         # an arm only voice 2 sent
                                        voice[v].b0093 = b730E[...]  # unverified (ran for v = 1, 2)
                                        ...
                            case 2:
                                switch voice[2].b6375:
                                    case $688A:
                                        goto L6858_B1
                                    ...                 # 47 cases over 21 bodies in all
                switch v:                               # the `CMP #v` one voice has and
                    case 0:                             # another has not: three rows, not one
                        # $63D8
                        z47 = b0021[83] == 0
                    case 1:
                        # $65C7
                        z47 = b0021[83] == 1
                    case 2:
                        # $67B6
                        z47 = b0021[83] == 2
                ...
    if b6800 != 0: trap 'untaken'                       # $67FF the filter sweep
    # $683C
    sid.cutoff_lo = b0021[78]
    b0021[117] = (b0021[78] >> 3)
    t51 = ((b0021[79] >> 2) | ((b0021[79] & 1) << 7))
    sid.cutoff_hi = (((((t51 >> 1) | (((b0021[79] >> 1) & 1) << 7)) >> 1) | ((t51 & 1) << 7)) | b0021[117])
    return ((voice[0].counter_2 | voice[1].counter_2) | voice[2].counter_2)
```

## 6. Certificates

`docs/certificates/ghouls-songNN.json`, from `tools/tuneprog_certify.py … --song N
--until-period --seconds S --budget 45 --resume` (music: 2.4 × the HVSC length + 20 s;
effects: 400 s). All 32: **0 divergences, 0 envelope traps**.

| subtune | ticks | s | period | complete |
|---|---|---|---|---|
| 1–6 (music, end with a stop) | 12,997 / 5,116 / 626 / 503 / 200 / 6,542 | 259 / 102 / 12 / 10 / 4 / 130 | 1 | yes — the state reaches a fixpoint |
| 7–11 (music, loop) | 14,337 / 12,671 / 13,093 / 9,121 / 13,280 | 286 / 253 / 261 / 182 / 265 | 8,064 / 7,392 / 6,930 / 5,664 / 6,799 | yes — a repeat after one loop |
| 12–20, 22–32 (effects) | 6–1,265 | 0.1–25 | 1 (617 for 22, 505 for 31) | yes |
| 21 (effect) | 20,049 | 400 | none | no — horizon only |

`ghouls-songs-all.json`: one tuneprog (1,442 sites, 75 regions, 1,567 statements) over the
union of all 32 traces, verified subtune by subtune — 220,049 calls, 0 divergences, 31 of
32 complete.

Re-run of all 33 after the SID Wizard jump-table extent change
([prototype-sidwizard.md](prototype-sidwizard.md) §3): ticks, periods, completeness and 0
divergences unchanged; only `ir_blocks` moves, by the arms the new extent adds and the
sub-base ones it no longer invents. Automatas (`automatas.json`, `-6581`, `-8580`) and
Commando (`commando-song1/2`) stay byte-identical apart from the timestamp: 651 sites,
102 regions, 1,070 statements, period 129,024 at call 149,024.

## 7. Fold results and open items

| scope | result |
|---|---|
| songs 1–11, 16, 26, 28, 30–32 | three voices fold |
| songs 12–15, 17–19, 21–25, 27, 29 | two voices fold |
| song 20 | a 4-copy family folds |
| songs 28, 30 | the `$6941` triple folds as well |
| song 14 (both voices play) | 199 blocks / 255 statements / 52 regions → 106 / 212 / 39; 458 printed lines → 309 |
| songs 17–19, 25, 27, 29 (second voice silent) | fold *adds* ~6 % of statements and 20 % of blocks (new columns and `switch (v)`, no second body to remove); buys per-voice names (`copy0[0].timer` for `b0021[28]`) and a coverage vector |
| songs 8–11 | refuses: *the entry row does not fold* at `$7316`, in the rip loader's block copy — in `init`, not the tick |
| `--songs all` union | 1,553 statements / 520 blocks / 75 regions → 770 / 294 / 45; 5 of 481 merged statements unverified. One voice's stream read is class `chk` where the others' are `ram`, but the union over `v` of a folded access is one region with one envelope, so the class is the union's |

Ownership, not the chain rule, refused the last eight: an effect using one voice ends its
copy in a tail the next copy's base sits inside, so an ordinary branch read as a
cross-copy edge. A copy now holds only what its rows hold. Songs 17–19 printed their
frequency tables as `T6D56`/`T6DB7`, not `sid_image`, because `facts.sid_image` read only
constant-address SID stores while a merged access indexes the register file; the store's
base names the register and the hundred-byte guard now counts elements observably reached,
so 17–19 name `freq_lo`/`freq_hi` as song 1 does.

Open:

- **The dispatch stays three dispatches over one set of arm bodies**: each voice's patched
  `JMP` holds its own target and the handlers interleave at unequal offsets, so no key
  pairs arms by value. Unsent arms are still enumerated — the merged writer names its cell
  and its table base through read-only columns, so copy *j*'s writer is that expression
  with each column read replaced by its *j*th entry; the three tables are parallel, each
  starting at index 129 and holding the 21 the bases are apart, and the `BPL` over the
  stream byte proves the index ≥ 128. Exactly 21 arms per voice, none displaced, at 30 s
  and in certified song 1 alike; at 30 s merged goes 7/12/8 → 21/21/21 arms (3
  `trap 'unverified'` → 39), unmerged 25/25/23 → 22/21/21, certificate +16 arm blocks
  (254 → 270) and no statement.
- **Subtune 21** has no state repeat inside 400 s: two voices keep a portamento and a
  trill moving (`$66/$67` note index, `$75/$78` frequency shadow, `$648B` trill phase) and
  the write list has no period either (317 distinct lists over 20,049 calls). Certified to
  the horizon.
- **16-bit views** miss the frequency shadow (`$75/$78`) and the pulse width (`$3F/$40`);
  the filter's `hi:lo` → `$D416` chain prints as its shifts. The pulse width *is* one carry
  chain in one block (`pw_lo += t`, `pw_hi += carry`), refused because `word._pairs` reads
  the two addresses with `addr_split` while the merged body addresses both halves through
  per-copy columns. The frequency shadow is no chain: its borrow is carried by a branch
  (`x16 = freq_hi` in one arm, `freq_hi - 1` in the other), which wants if-conversion. And
  `names.u16` is keyed by `(lo region, hi region)` while Follin's zero page is one region,
  so keying the u16 view by cell is the prerequisite — and it moves every certificate's
  u16 names.
