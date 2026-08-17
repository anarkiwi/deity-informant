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
| 6 | three unrolled voices fold | **no** — see §7; the three copies are not the same trace-closed program (188/211/199 of a 229-offset template, 163 common) |
| 7 | SMC immediates as variables | 35 cells (24 play-written, 11 init-patched); every play-written cell is a load at its instruction, every init-only cell is a constant in the tick and a store in `init`; 76 cells in the `--songs all` build |
| 8 | `init` writes $08 then $00 to `$D400–$D41C` | 58 init writes, `$D41C` down to `$D400`, values `{8, 0}` — compared byte for byte by the certificate |
| - | genericity, budget | Automatas (149,025 calls, period 129,024, both SID models), Commando songs 1–2, Emomyst at 10 s: 0 divergences with the same code. Song 1 is traced and verified in one 14 s invocation: 1,177 sites → 68 regions → 4 procedures → 1,242 statements |

---

## 5. The printed `tick()` (`tuneprog.md`, verbatim; `...` elides)

```
tick():                                  # $6234, 16,000 calls
    if b0021[90] < 0:                              # active[0] = $FF
        ...                                        # attack blip end
        if b0021[54] != 0:                         # vibrato delay set
            ...
            sid[0].freq_lo = a381                  # freq += (dir ? +depth : -depth)
            sid[0].freq_hi = x55
            b0021[84] = a381
            b0021[87] = x55
            if t5 == 1:
                b0021[99] = (b0021[102] << 1)
                b6269 = (t2 ^ $FF)                 # the SMC vibrato direction
        if b0021[66] != 0: writeout2()             # portamento, outlined
        if timer != 0:                             # $62EE pulse mode (1 = hold)
            ...
                sid[0].pw_lo = b0021[30]
                sid[0].pw_hi = b0021[31]
        # $6338
        b0021[6] -= 1                              # dur
        if ((b0021[111] == b0021[6]) or (b0021[27] == 0)):
            sid[0].ctrl = (b0021[9] & $FE)         # gate off
            b0021[27] += 1
        b0021[27] -= 1                             # the INC/DEC floor idiom
        if b0021[6] == 0:
            while True:   # x911                   # the sequencer
                t15 = b730E[(b0021[1] << 8) | b0021[0]]
                if t15 >= 0: break                 # a note byte leaves the loop
                b6375 = T6CB7[t15 - $80]           # the patched JMP, tables at base-$80
                b6375[1] = T6CF6[t15 - $80]
                switch b6375:
                    case $6858:                    # $82 loop begin
                        b0021[12] = b730E[((b0021[1] << 8) | b0021[0]) + 1]
                        ...
                        continue
                    case $68EE:                    # $84 fixed note length
                        b640F = b730E[((b0021[1] << 8) | b0021[0]) + 1]
                        y85 = 2
                        goto L6356_98
                    case $6909:                    # $85 raw register list
                        y89 = 1
                        a327 = b730E[((b0021[1] << 8) | b0021[0]) + 1]
                        while True:   # x98
                            y91 = (y89 + 2)
                            sid.reg[a327] = b730E[((b0021[1] << 8) | b0021[0]) + (y89 + 1)]
                            t25 = b730E[((b0021[1] << 8) | b0021[0]) + (y89 + 2)]
                            if t25 >= 0:
                                y89 = y91
                                a327 = t25
                                continue
                            break
                        ...
                    case $693F:                    # $8D waveform
                        sid[0].ctrl = t26
                        b0021[9] = t26
                        timer = b63D4              # pulse mode for the next note
                    ...                            # 11 more commands this track sends
                    case $6AD0: trap 'unverified'  # $87, $88, $89, $8F, $91: in the
                    case $6A0B: trap 'unverified'  # table, never sent by this track
                    ...
            # $6381 note fetch
            b0021[69] = x44
            sid[0].freq_lo = freq_lo_2[x44 - $13]  # notetab[note + transpose]
            b0021[84] = freq_lo_2[x44 - $13]
            b0021[87] = freq_hi_2[x44 - $13]
            sid[0].freq_hi = b0021[87]
            sid[0].ctrl = (b0021[9] | 1)           # gate on
            if b640F != 0:                         # $84 fixed length, else a byte
                y81 = 1
                a301 = b640F
            else:
                y81 = 2
                a301 = b730E[((b0021[1] << 8) | b0021[0]) + 1]
            b0021[6] = a301
            ...
    goto L6421_A5                                  # voice 1, then voice 2, then:
        ...
        sid.cutoff_lo = b0021[78]                  # $67FF the filter sweep
        sid.cutoff_hi = (((((t69 >> 1) | (((b0021[79] >> 1) & 1) << 7)) >> 1) | ((t69 & 1) << 7)) | b0021[117])
        return ((b0021[90] | b0021[91]) | b0021[92])
```

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

- **The three voice copies do not fold into `for v in 0, 1, 2`.** The reason is
  not the operand vectors: mapped onto the 229-offset template (correcting for
  the two-byte `CMP #v` insertion), the voices executed 188, 211 and 199 offsets,
  only 163 common to all three, each missing 18–41 another ran. Three copies of
  one template that ran *different
  subsets of it* are three different trace-closed programs, so the isomorphism
  test fails on shape before it ever reaches an operand. Behind that sit three
  more obstacles: the per-voice SMC cells are separate regions and **not equally
  spaced** (`$62EE`, `$64DB`, `$66CA`), so no affine step describes them; the
  shared zero-page region is walked with two strides (1 for bytes, 2 for the word
  pairs); and the sequencer arms contain `goto`s to per-voice labels, which
  `unroll` does not tokenise. A static (rather than trace-closed) product of the
  three copies, plus a parallel-region *group* view with a per-copy address table
  instead of an affine step, is what this needs.
- **Names.** The whole of `$21–$97` is one region (init clears it with one loop)
  and is walked by 200 constant addresses, not by an index, so the stride view
  never fires and per-voice fields print as `b0021[90]` rather than
  `voice[0].active`. Splitting a region by the offsets parallel code copies touch
  would name it, and is the machinery the fold needs.
- **Subtune 21** is the one subtune with no state repeat inside 400 s: two voices
  keep a portamento and a trill moving (`$66/$67` note index, `$75/$78` frequency
  shadow, `$648B` trill phase) and the write list has no period either (317
  distinct lists over 20,049 calls). Certified to the horizon.
- **16-bit views** do not reach the frequency shadow (`$75/$78`) or the pulse
  width (`$3F/$40`) — `word.fold16` proves a pair from one carry chain, and these
  halves are stored by different instructions — and the filter's `hi:lo` to
  `$D416` shift chain prints as its shifts.
