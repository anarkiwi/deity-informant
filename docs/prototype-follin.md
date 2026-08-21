# Prototype: the tuneprog decompiler on Tim Follin's *Ghouls'n'Ghosts*

The second exemplar of [tuneprog-decompiler-design.md](tuneprog-decompiler-design.md),
after defMON's *Automatas* ([prototype-automatas.md](prototype-automatas.md)):
`MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid` (anatomy [§3.6](playroutine-anatomy.md)),
32 subtunes, three unrolled voice interpreters, a 21-way patched-`JMP` dispatch,
and a rip loader whose `init` patches its own compare. Every subtune is certified
(`docs/certificates/ghouls-song01.json` … `-song32.json`, plus
`ghouls-songs-all.json` for the union program). No Follin-specific code path
exists; Automatas, Commando and SID Wizard's Emomyst certify with the same
pipeline.

## 1. Why Follin

| design mechanism | how Ghouls'n'Ghosts stresses it |
|---|---|
| SMC operand cells (S2) | 24 varying sites: 21 immediates used as variables, 3 dispatch `JMP` operands — **and `init` patches the same cells**, plus one of its own (`$29D8`) between two copy loops |
| computed control (S2) | `LDA T1,X; STA $6375; LDA T2,X; STA $6376; JMP $xxxx`, X = command byte ≥ $80, tables at `base−$80`, three voice copies |
| computed store (S2/S3) | `LDA $622E,X; STA $6219` then a store *through* the patched operand into one of three unequally-spaced cells |
| data-dependent SID address (§7 traps) | `$85` writes `STA $D400,X` with X from the song: the register is a variable |
| per-subtune init images (S0/S3) | 32 subtunes; `init` tail-jumps into a rip stub that copies two song blocks over itself, then either starts a song or a sound effect |
| return value (§6.2) | `play` returns `A = $7B \| $7C \| $7D` — `$FF` while any voice runs, 0 when all three stop |
| copy folding, table typing (S6) | three 493-byte voice templates plus three handler copies, chained by `JMP` inside one procedure, each with its own 21-way switch; a 97-entry note table the transpose can index past, into the SFX tables |
| structuring (S5) | the whole frame is **one linear procedure**: no `JSR` on the hot path, voice *n* ends by `JMP` to voice *n*+1, handlers `JMP` back into their voice's sequencer |

---

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

---

## 3. What broke, and the generic fix

**The envelope trap in `init`.** The rip loader at `$2980` copies two song blocks
per subtune and patches the operand of its own `CPY #$00` at `$29D7` with `STY
$29D8` — once per block, consumed *inside* `init*, with a different value each
time. The front end keyed sites by `(pc, opcode, fixed operand bytes)` with only
*play*-written cells blanked, so the site took the post-init byte (the second
block's remainder) and the first copy loop overran by one byte: `$4622 outside
[$3D44,$4621]` at `$29DB`.

The rule is now the design's without the phase qualifier: **an instruction byte
any traced procedure writes, in any phase, is a variable** (`trace.py`: `cells =
code & (written_init | written_play)`) — it drops out of the site key and the lift
loads it, so one site serves both loops.

**Putting the constants back.** Residualising every init-patched operand would
cost SID Wizard its readable tick (Emomyst's `init` relocates the player and
patches ~30 operands). `ssa.Folds` folds a load at a known address to the
post-init byte when at least one of its bytes is an SMC cell and none is
play-written, and `ssa.simplify` applies it only in the procedures `init` never
reaches — inside `init` the value is the store's, not the image's. An ordinary
init-written *variable* is not a cell, so it keeps its load and its name; a
`--songs all` build folds nothing at all.

**A patched conditional branch keeps its condition.** The rip stub's `BPL` at
`$7318` sits in the band its own block copy overwrites, so its offset byte is a
cell and the branch became a bare computed `switch` — which evaluated the *taken*
target on a call that fell through (`trap 'switch'`). `cfg.py` now emits
`if cond: switch(target) else: fall-through`, with the taken side's observed
targets in the switch (`build._branch_switch`).

**Presentation** (`pseudocode.py`, no IR change): a SID store whose index does not
step by the 7-byte voice block prints as `sid.reg[i]`, because the *register* is
what the index selects (`$85`, and every `LDY #$1C` clear loop); a table's
literal operand moves into the index, so `LDA $6C37,X` on a region based at
`$6CB7` prints `T6CB7[cmd - $80]`; and a play entry's `A` prints as the tick's
`return` when every exit agrees on one computed expression (`ir.retval`).

**Sibling copies and the copy index** (`siblings.py`, `copyrows.py`,
`copymerge.py`; S2c, in the certified program). The three voices are three copies
of one static template, and the alignment of their instruction streams recovers
that from the post-init image: equal opcodes advance all three, and a gap holds
the `CMP #v` voices 1 and 2 carry where voice 0 uses the load's own Z flag. The
dispatch is where the copies stop being one stream, so its arms are paired by
their index in the parallel tables `jumptab.dispatch` reads, which carries the
correspondence into the handlers. *Amended by #241:* discovery is exact -- the
bases are the chain the built procedures already carry, each pair of copies is one
`difflib` alignment in which only a gap may separate them, and the family holds
only while every copy's operand map is a function (an indexed base whose index is
data, `STA $D400,X`, names something else than the same literal under `abs`); the
ten thresholds are gone and song 1's family is the same three voices over 419
rows.

*Amended by this stage:* the correspondence is now spent before the IR exists.
Copy *j* executing a template row **is** that row executed with `v = j`, so the
front end builds the rows once: an operand the copies disagree on becomes a load
from a per-copy column `T_x[v]` (one read-only table, 59 columns for song 1, in a
band no access, no code and no other region can see -- outside the load image,
outside the stack page and outside I/O, where every byte is a pinned input to the
program whatever it holds); the chain edge from voice *j* to voice *j+1* becomes
`v += 1; if v < 3: header`; the count of a site becomes a vector over `v`, and a
zero says no execution of that voice reached that row -- the statement is the one
another voice ran, at the address the correspondence says this one names, and it
is marked unverified per statement. What the index cannot name is refused, not
approximated: a row whose copies do not lift to one shape (three `LDA #imm` whose
operand is a cell in one voice and not in another) stays three rows under a
`switch (v)`, and so does the dispatch, since each voice's patched `JMP` holds its
own target -- the arms then pair by the body they share, not by a case value.
Nothing is lifted into a second program, and `--no-merge` builds what S2b built
before.

**Static jump-table arms** (`jumptab.py`): when both halves of a patched `JMP`
operand are copied from constant tables indexed by one value, the table's
remaining entries are targets too, and become arms that `trap 'unverified'`
instead of a bare default.

**`--songs all`** (should-have): `tracedata.merge` builds one trace from every
subtune — sites re-keyed by the union of their cells (a wider cell set can only
merge keys, never split them), edges/calls/returns/written sets unioned, each
subtune's write log left where verification needs it. What `init` writes is typed
`state` and nothing folds; every subtune is then verified against its own trace.

---

## 4. Evidence (measured, song 1 unless stated)

| # | claim | measured |
|---|---|---|
| 1 | patched-`JMP` dispatch becomes a switch | three switches on `load16($6375/$6562/$6751)`, **21/23/23 arms** (14/18/15 observed, the rest statically enumerated as `trap 'unverified'`) — the commands the tables carry, `$93`/`$94` included since the SID Wizard pass made a table run out to the nearest instruction or foreign access rather than stopping at the bytes an accessor touched (two of the 23 are that rule over-reaching past the 21-entry table) |
| 2 | data-dependent SID address | `sid.reg[a327] = b730E[...]` inside the `$85` list loop; the write's envelope is `$D400–$D418`, and `(addr, val)` equality is part of the certificate |
| 3 | computed store operand | in the SFX subtunes' `init`: a store **through** `load16($6219)` whose region is `[$640F, $67ED]` — exactly the three voices' fixed-length cells (song 16) |
| 4 | 32 subtunes from the pre-init image | all 32 certified, 0 divergences, 0 envelope traps; 31 complete via a period (§6) |
| 5 | play returns a value | `return ((b0021[90] \| b0021[91]) \| b0021[92])` = `$7B \| $7C \| $7D`; not part of the certificate |
| 6 | three unrolled voices fold | **yes, in the certified program** (#242): one family of three copies, 400 of the 419 aligned rows folded (the 19 left are rows whose copies do not lift to one shape, or whose successors cross copies -- they stay three rows under a `switch (v)`), 60 per-copy columns. Coverage says what each voice ran: 338 statements all three, 77 only voice 2, 29 voices 2 and 3, 19 voices 1 and 3, 8 voices 1 and 2 -- 133 of 471 merged statements are unverified for some voice and marked there. S4 falls from 1,229 statements in 450 blocks to 671 in 254; the printed document from 1,421 lines to 794 |
| 7 | SMC immediates as variables | 35 cells (24 play-written, 11 init-patched); every play-written cell is a load at its instruction, every init-only cell is a constant in the tick and a store in `init`; 76 cells in the `--songs all` build |
| 8 | `init` writes $08 then $00 to `$D400–$D41C` | 58 init writes, `$D41C` down to `$D400`, values `{8, 0}` — compared byte for byte by the certificate |
| - | genericity, budget | Automatas (149,025 calls, period 129,024, both SID models), Commando songs 1–2, Emomyst at 10 s: 0 divergences with the same code. Song 1 is traced and verified in one 14 s invocation: 1,177 sites → 68 regions → 4 procedures → 1,242 statements |

---

## 5. The printed `tick()` (`tuneprog.md`, verbatim; `...` elides)

One body over the copy index, the three voices' dispatches inside it over one set
of arm bodies, and every statement no voice of its own ran marked where it is.
Every column the copies disagree on prints as the operand it stands for — an
affine one through the stride vocabulary (`sid[v].freq_lo`, `b640F[v]`), the rest
as a field of the group view whose per-copy addresses the state header lists once
(`$62EE`/`$64DB`/`$66CA`). Two of the 60 columns keep their table read: no rule
names them, so their addresses stay visible. Measured on the certified song 1
(12,997 calls): the document is 757 lines and `tick()` 495 of them, against 794
and 578 before the view pass.

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

The chain edge into the next copy is the loop's back edge, so where a voice ends
its own note the arm reads `continue`, not `goto L6421_A5`, and the `v += 1; if v
< 3` the merge laid down is the `for` header itself. What is left of `b0021[...]`
under a constant index is what is not per-voice at all — the filter's own cells
and `$74`, the voice number.

---

## 6. Certificates

`docs/certificates/ghouls-songNN.json`, from `tools/tuneprog_certify.py … --song N
--until-period --seconds S --budget 45 --resume` (music: 2.4 × the HVSC length +
20 s, covering the transient plus one loop; effects: 400 s). All 32: **0
divergences, 0 envelope traps**.

| subtune | ticks | s | period | complete |
|---|---|---|---|---|
| 1–6 (music, end with a stop) | 12,997 / 5,116 / 626 / 503 / 200 / 6,542 | 259 / 102 / 12 / 10 / 4 / 130 | 1 | yes — the state reaches a fixpoint |
| 7–11 (music, loop) | 14,337 / 12,671 / 13,093 / 9,121 / 13,280 | 286 / 253 / 261 / 182 / 265 | 8,064 / 7,392 / 6,930 / 5,664 / 6,799 | yes — a repeat after one loop |
| 12–20, 22–32 (effects) | 6–1,265 | 0.1–25 | 1 (617 for 22, 505 for 31) | yes |
| 21 (effect) | 20,049 | 400 | none | no — horizon only |

`ghouls-songs-all.json`: one tuneprog (1,442 sites, 75 regions, 1,567 statements)
from the union of all 32 traces, verified subtune by subtune — 220,049 calls,
0 divergences, 31 of 32 complete.

All 33 were re-run after the SID Wizard pass changed how far a jump table
reaches ([prototype-sidwizard.md](prototype-sidwizard.md) §3): every certified
result — ticks, periods, completeness, 0 divergences — is unchanged, and only the
`ir_blocks` count moves, by the arms the new extent adds (and the ones below a
table's base it no longer invents).

Automatas (`automatas.json`, `-6581`, `-8580`) and Commando (`commando-song1/2`)
were re-run on the new front end and come out *byte-identical* apart from the
timestamp — same 651 sites, 102 regions, 1,070 statements, same period 129,024 at
call 149,024 — so the committed files stand.

---

## 7. What remains

Four of the six obstacles this section listed after #234 are gone. **The three
voice copies are one body in the certified program** (§3, §4 row 6, §5): copy *j*
executing a template row is that row executed with `v = j`, the unequally spaced
SMC cells (`$62EE`/`$64DB`/`$66CA`) are one column of the family's per-copy table,
the chain edge into the next voice is `v += 1`, and a row a voice never ran is the
same statement with a zero in its coverage vector, marked where it prints.

- **A merged row a voice never ran is unverified.** It is the statement another
  voice ran, at the address the correspondence says this voice names. Its coverage
  entry is 0, the certificate counts it, and the printed program says so per
  statement -- no lifted site, no second program, no count-0 block reachable only
  through a former trap.
- **What is left of the zero page is what is not per-voice.** `$21–$97` is still
  one region -- init clears it with one loop -- and the fold reads its per-voice
  cells through the family's columns, so what still prints as `b0021[...]` is the
  filter's own accumulator and bounds and `$74`, the voice number. Since #244 the
  columns print as the operands they stand for: 51 fields of one `voice[3]` view
  whose per-copy addresses the state header lists once, `sid[v].freq_lo` where the
  copies step by the SID's voice block, and `copies_6234[...]` for the two of 60
  columns no rule names, whose addresses stay visible.
- **The dispatch stays three dispatches over one set of arm bodies.** Each voice's
  patched `JMP` holds *its own* target, and the three copies' handlers are
  interleaved at unequal offsets, so no key pairs the arms by value: the merged
  node is `switch (v)` into each voice's own switch, whose arms jump to the one
  merged body per command. Every arm some voice sent is that body; an arm no voice
  sent is `trap 'unverified'` as before. Keying on the table index the writers
  read cannot help, because the cell is written a tick earlier and read as memory.
  Since P1 those unsent arms are enumerated again: the merged writer names its
  cell *and* its table base through per-copy columns, and a column is read-only,
  so copy *j*'s writer is that expression with each column read replaced by its
  *j*th entry. The three tables are parallel, so each starts at index 129 and
  holds the 21 the bases are apart, and the `BPL` over the stream byte proves the
  index is 128 or more: each voice's switch is exactly 21 arms with none
  displaced, at 30 s and in the certified song 1 alike. At 30 s the merged program
  goes from 7/12/8 arms to 21/21/21 (3 `trap 'unverified'` arms → 39) and the
  unmerged one, whose extent over-reached, from 25/25/23 to 22/21/21; the
  certificate gains 16 arm blocks, 254 → 270, and no statement.
- **The `--songs all` union folds.** What blocked it in #234 -- one voice's stream
  read is access class `chk` where the others' are `ram`, because over 32 subtunes
  that voice reaches bytes outside the written set -- is not a question the fold
  asks any more: the union over `v` of a folded access is *one* region with one
  envelope, so the class is the union's. The union program goes from 1,553
  statements in 520 blocks over 75 regions to 770 in 294 over 45, with 5 of its
  481 merged statements unverified.
- **Every subtune folds its voices, and all 32 certify.** At the certificate's own horizon
  the three voices fold in songs 1–11, 16, 26, 28, 30–32; two voices fold in 12,
  13, 15, 21, 22, 24 and, since P1, in 14, 17, 18, 19, 23, 25, 27 and 29; song 20
  folds a 4-copy family, and 28 and 30 fold their `$6941` triple as well. What
  refused the eight was not the chain rule but ownership: an effect that uses one
  voice ends its copy in a tail the next copy's base sits inside, and ownership
  gave that tail to the next copy, so an ordinary branch inside it read as an edge
  crossing copies. A copy now holds only what its rows hold. What still refuses is
  in `init`, not the tick: songs 8-11 carry *the entry row does not fold* at
  `$7316`, the rip loader's own block copy. Subtune 14, whose two
  voices both play, goes from 199 blocks and 255 statements over 52 regions to 106
  and 212 over 39 (458 printed lines to 309); where the second voice is silent
  (17, 18, 19, 25, 27, 29) the fold instead *adds* about 6 % of statements and
  20 % of blocks -- the columns and the `switch (v)` are new, and there was no
  second body to remove -- and buys the per-voice cells their names
  (`copy0[0].timer` for `b0021[28]`) and a coverage vector that says the silent
  voice's code is this code. A merged access unites its regions, so a role one
  voice's access carried can move with them: in 17–19 the frequency tables printed
  as `T6D56`/`T6DB7`, not as `sid_image`. *Fixed in Q1b, and the union was not the
  cause*: `facts.sid_image` read only the SID stores whose address is a constant,
  and a merged access indexes the register file, so every per-voice store fell
  out of the role plane. The store's own base still names the register, and the
  guard that keeps a hundred-byte block from being called one register's image now
  counts the elements each access observably reached. 17–19 name `freq_lo`/
  `freq_hi` as song 1 does.
- **Subtune 21** is the one subtune with no state repeat inside 400 s: two voices
  keep a portamento and a trill moving (`$66/$67` note index, `$75/$78` frequency
  shadow, `$648B` trill phase) and the write list has no period either (317
  distinct lists over 20,049 calls). Certified to the horizon.
- **16-bit views** do not reach the frequency shadow (`$75/$78`) or the pulse
  width (`$3F/$40`), and the filter's `hi:lo` to `$D416` shift chain prints as its
  shifts. *Measured in Q1b, which refuted the stated cause and refused the row.*
  The pulse width **is** one carry chain in one block (`pw_lo += t`,
  `pw_hi += carry`); what refuses it is that `word._pairs` reads the two addresses
  with `addr_split`, while the merged body addresses both halves through per-copy
  **columns**. The frequency shadow is not a chain at all: its borrow is carried
  by a *branch* (`x16 = freq_hi` in one arm, `freq_hi - 1` in the other), which
  wants if-conversion, not a pair rule. And `names.u16` is keyed by
  `(lo region, hi region)` while Follin's zero page is **one** region, so the
  naming plane cannot express a pair of its cells at all — keying the u16 view by
  cell is the prerequisite, and it moves every certificate's u16 names.
