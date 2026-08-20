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
| 6 | three unrolled voices fold | **yes, once the siblings are closed**: the copies ran 188/211/199 of a 229-offset template (163 common), so the trace-closed programs differ in shape before any operand does; of the 420 aligned rows the copies executed 308/380/329, and the closure lifts 189 sites so that all three have the same 402 (the 18 left are rows no copy ever reached, so no sibling can donate them). One family, one loop, 44 of 283 printed statements unverified; the whole document goes from 1,421 lines to 666 |
| 7 | SMC immediates as variables | 35 cells (24 play-written, 11 init-patched); every play-written cell is a load at its instruction, every init-only cell is a constant in the tick and a store in `init`; 76 cells in the `--songs all` build |
| 8 | `init` writes $08 then $00 to `$D400–$D41C` | 58 init writes, `$D41C` down to `$D400`, values `{8, 0}` — compared byte for byte by the certificate |
| - | genericity, budget | Automatas (149,025 calls, period 129,024, both SID models), Commando songs 1–2, Emomyst at 10 s: 0 divergences with the same code. Song 1 is traced and verified in one 14 s invocation: 1,177 sites → 68 regions → 4 procedures → 1,242 statements |

---

## 5. The printed `tick()` (`tuneprog.md`, verbatim; `...` elides)

One loop over the three voices, the 21-way command switch inside it, and every
per-voice cell a field of a group whose per-copy addresses the state header lists
once -- including the SMC cells no stride describes (`$62EE`/`$64DB`/`$66CA`).

```
closure   siblings: 1 family, 189 sites lifted, 1 loop over 3 copies;
          44 of 283 statements unverified
state     voice[3]  per-copy cells, 51 fields
            .b0021   $0021 $0023 $0025 ; .timer   $0022 $0024 $0026   the stream pointer
            .pw_lo   $003F $0041 $0043 ; .pw_hi   $0040 $0042 $0044
            .freq_lo $0075 $0076 $0077 ; .freq_hi $0078 $0079 $007A
            .counter_2 $007B $007C $007D                    active[v] = $FF
            .b6269   $6269 $6456 $6645                      the vibrato direction
            .timer_9 $62EE $64DB $66CA                      the pulse mode
            .b6375   $6375 $6562 $6751                      the patched JMP
            .b640F   $640F $65FE $67ED                      the $84 fixed length
            .b6C37   $6C37 $6C4C $6C61 ; .b6C76 $6C76 $6C8B $6CA0   the two tables
          b0021 $0021 118 bytes                 the block init clears in one loop
          ...                                   b6800, b6813 .. -- the filter's own

tick():                                  # $6234, 16,000 calls
    for v in 0, 1, 2:   # x48,000
        if voice[v].counter_2 < 0:
            if voice[v].timer_8 != 0:
                ...                                        # attack blip end
                sid[v].freq_lo = voice[v].freq_lo
                sid[v].ctrl = (voice[v].ctrl | 1)
            if voice[v].b0057 != 0:                        # vibrato delay set
                ...
                sid[v].freq_lo = a448                      # +/- depth by the direction
                sid[v].freq_hi = x55
                voice[v].freq_lo = a448
                if voice[v].timer_7 == 0:
                    voice[v].timer_7 = (voice[v].b0087 << 1)
                    voice[v].b6269 = (t3 ^ $FF)             # the SMC vibrato direction
            if voice[v].timer_9 != 0:                      # $62EE pulse mode (1 = hold)
                ...
                    sid[v].pw_lo = voice[v].pw_lo
                    sid[v].pw_hi = voice[v].pw_hi
            # $6338
            voice[v].timer_4 -= 1                          # dur
            ...
            if voice[v].timer_2 == 0:
                while True:   # x2,773                     # the sequencer
                    t19 = b730E[(voice[v].timer << 8) | voice[v].b0021]
                    if t19 >= 0: break                     # a note byte leaves the loop
                    voice[v].b6375 = voice[v].b6C37[t19]   # the patched JMP, tables at base-$80
                    voice[v].b6375_2 = voice[v].b6C76[t19]
                    switch voice[v].b6375:
                        case $6858:                        # $82 loop begin
                            voice[v].timer_3 = b730E[((voice[v].timer << 8) | voice[v].b0021) + 1]
                            ...
                            continue
                        case $68EE:                        # $84 fixed note length
                            voice[v].b640F = b730E[...]
                            goto L6356_98                  # back into the sequencer
                        case $6909:                        # $85 raw register list
                            while True:   # x191
                                sid.reg[a369] = b730E[...] # the register is a variable
                                ...
                        case $693F:                        # $8D waveform
                            sid[v].ctrl = t26
                            voice[v].ctrl = t26
                            voice[v].timer_9 = voice[v].b63D4
                        ...                                # 21 arms in all: the 18 some
                        case $6AD0: trap 'unverified'      # voice sent, and 3 none did
            # $6381 note fetch
            voice[v].b0066 = x44
            sid[v].freq_lo = T6D48[voice[v].b0066]         # notetab[note + transpose]
            sid[v].ctrl = (voice[v].ctrl | 1)              # gate on
            ...
    if b6800 != 0: trap 'untaken'                          # $67FF the filter sweep
    sid.cutoff_lo = b0021[78]
    sid.cutoff_hi = (((((t46 >> 1) | ...) >> 1) | ...) | b0021[117])
    return ((voice[0].counter_2 | voice[1].counter_2) | voice[2].counter_2)
```

Where a voice ends its own note the arm now reads `continue`, not `goto L6421_A5`:
the chain edge into the next copy is the loop's back edge. What is left of
`b0021[...]` is the filter's own cells and `$74`, the voice number -- the parts of
the zero-page block that are not per-voice at all.

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

Two of the four obstacles this section used to list are gone. **The three voice
copies are one body in the certified program** (§3, §4 row 6, §5): copy *j*
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
  one region -- init clears it with one loop -- and the fold now reads its
  per-voice cells through the family's columns, so what still prints as
  `b0021[...]` is the filter's own accumulator and bounds and `$74`, the voice
  number. Naming a column `voice[v].field` is stage C's view pass; until then the
  columns print as `copies_6234[...]` and the loop as a `while` over the index.
- **The dispatch stays three dispatches over one set of arm bodies.** Each voice's
  patched `JMP` holds *its own* target, and the three copies' handlers are
  interleaved at unequal offsets, so no key pairs the arms by value: the merged
  node is `switch (v)` into each voice's own switch, whose arms jump to the one
  merged body per command. Every arm some voice sent is that body; an arm no voice
  sent is `trap 'unverified'` as before. Keying on the table index the writers
  read cannot help, because the cell is written a tick earlier and read as memory.
- **Every subtune folds or refuses with a reason, and all 32 certify.** At the
  certificate's own horizon the three voices fold in songs 1–11, 16, 26, 28,
  30–32; two voices fold in 12, 13, 15, 21, 22, 24; song 20 folds a 4-copy family;
  and 14, 17, 18, 19, 23, 25, 27, 29 refuse with *an edge from copy 0 enters copy
  1* -- an effect that uses one voice branches out of the copy the chain proof
  established, and only the chain edge may increment `v`. A silent voice is no
  longer the obstacle it was in #234: what it never ran is a zero in the coverage
  vector, not a missing donor.
- **Subtune 21** is the one subtune with no state repeat inside 400 s: two voices
  keep a portamento and a trill moving (`$66/$67` note index, `$75/$78` frequency
  shadow, `$648B` trill phase) and the write list has no period either (317
  distinct lists over 20,049 calls). Certified to the horizon.
- **16-bit views** do not reach the frequency shadow (`$75/$78`) or the pulse
  width (`$3F/$40`) — `word.fold16` proves a pair from one carry chain, and these
  halves are stored by different instructions — and the filter's `hi:lo` to
  `$D416` shift chain prints as its shifts.
