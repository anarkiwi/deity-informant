# Follin dispatch / sequencer study

Empirical characterization of the Follin-player dispatch and sequencer
machinery, unblocking both open frontiers: the Gate-S RTS-dispatch structured
region (Ghouls_n_Ghosts 36.1% structured, 997 of 3,103 blocks with no static
predecessor) and M4 musical L2 recovery for pointer-sequencer players. All
numbers measured full-Songlengths on `Follin_Tim/Ghouls_n_Ghosts.sid` (subtune
0, 12,950 frames), confirmed on `Follin_Tim/Agent_X_II_The_Mad_Profs_Back.sid`
(subtune 1, 3,450 frames). (probe scripts retired with the study prototypes; findings and numbers herein are the record).5 s), `follin_model_scan.py` (full decompile +
block/closure census, 74 s — decompile-pipeline-bound), `follin_script_decode.py`
(stream decoder + tick simulator, < 1 s). Artifacts in `out/study/*.json`.

## 1. Entry mechanism: operand-SMC computed goto, NOT the RTS trick

Full-length rts census (popped-byte provenance tracked per stack cell):
Ghouls executes **zero** RTS-with-explicit-push and zero jsr returns in the
play phase — all 12,950 rts events are the play-exit sentinel. Agent_X_II:
3,450 jsr returns (one driver pair per frame) + 3,450 sentinels, again zero
pushed-target rts. The "RTS-trick" label in corpus-status is wrong for this
player; the dynamic transfers are three **`jmp abs` sites whose operands are
rewritten** from handler tables (the model's `jmpd` terminator):

```
$6360: LDY #$00
$6362: LDA ($21),Y     ; fetch stream byte, voice-1 script pointer at zp $21/$22
$6364: BPL $637F       ; < $80 -> note/rest path
$6366: INY
$6367: TAX             ; X = command byte ($80..$94)
$6368: LDA $6C37,X     ; handler lo   (effective $6CB7.. since X >= $80)
$636B: STA $6375       ; SMC: jmp operand lo
$636E: LDA $6C76,X     ; handler hi   (effective $6CF6..)
$6371: STA $6376       ; SMC: jmp operand hi
$6374: JMP $68A3       ; <- dispatch site (operand self-modified)
```

Per-voice copies: site $6374 (v1, tables $6C37/$6C76), $6561 (v2,
$6C4C/$6C8B), $6750 (v3, $6C61/$6CA0) — lo/hi split tables 21 bytes apart,
op range $80-$94 (21 slots/voice, 63 handler entries at $6CB7-$6D34, directly
followed by the 97-entry note-freq split table $6D35/$6D96). Observed
dispatch counts and target-set sizes (full length):

| site | voice | dispatches | distinct targets (of 21 slots) |
|---|---|---|---|
| $6374 | 1 | 253 | 14 |
| $6561 | 2 | 324 | 17 |
| $6750 | 3 | 204 | 14 |

Agent_X_II: same idiom, sites $69E7/$6CD4/$6FC3 (29/46/107 dispatches,
8/10/9 targets), plus a per-frame static `jmpind $0403` through a RAM vector
at $0408 (single target — driver trampoline, not dispatch).

### Where the 997 no-static-pred blocks actually come from

Model-side census (full length): of 3,103 block pcs, only **556 were ever
executed**; 2,547 are never-executed blocks materialized by the dispatch
closure. The 997 orphans are all unexecuted (0 in `ev.pcs`), covered by **no**
evidence site, and every one of the 2,454 blocks unreachable from entries via
static edges is unexecuted too. Root cause, from `term_targets` logging:
round-1 closure of the operand cells treats lo and hi **independently** over
the whole table page, yielding transient "proven" target sets of **16,128 /
5,040 / 3,364** per site; their liftable members are materialized as blocks.
A later fixpoint round downgrades the sites to `evidence` status
(observed 14/17/14), but the junk blocks are never garbage-collected and
serialize as top-level orphan procedures. This is the measured face of the
tracked dispatch-index-precision blocker (docs/soundness.md) plus a
materialization-lifecycle defect in `close_dispatch`.

## 2. Script streams

- **Three streams, one per voice**: zp pointer pairs $21/$22, $23/$24,
  $25/$26 (Agent_X_II: $02/$03, $04/$05, $06/$07). The `(zp),y` fetch census
  finds them with no tune knowledge: the only high-count indirect bases in
  play are the three pairs.
- **Bases**: init loads them per subtune from split lo/hi song tables at
  $730E/$7315 (v1), $731C/$7323 (v2), $732A/$7331 (v3); subtune-0 bases
  $7338/$75F7/$77A8. (Bank select for the 32 subtunes goes through the
  init-only opcode cell $7316 noted in soundness.md.)
- **Advance**: batch, at tick end — Y counts bytes consumed during the tick
  and `TYA / ADC $21` folds it in ($6356/$6417 for v1). Wholesale rewrites by
  script-flow ops: `call` pushes ptr+3 onto a **3-deep per-voice return stack**
  ($6B1F-$6B30, depth cells $69/$6A/$6B) and loads a new pointer; `ret` pops;
  `jump` replaces; `loop`/`loopend` save/restore a single per-voice loop start
  ($30/$31 for v1) with a repeat counter ($2D).
- **Cadence**: each voice has a frame countdown ($27/$28/$29, decremented once
  per frame); on zero the interpreter runs a chain of command ops until a
  note/rest/stop op supplies the next duration. Stream event totals: v1 911,
  v2 1,231, v3 627 (mean event spacing 14.2/10.5/20.7 frames — there is no
  row grid).

## 3. Op grammar (observed + validated)

Byte < $80: **note** (index, + transpose) or **rest** ($00); consumes one
duration byte (frames, 1-165 observed) unless sticky-duration mode is on
(op $84 arms it; then $84's operand is the duration for all following events).
Byte $80-$94: command, dispatched via the tables. v1 handlers (v2/v3 are
+$0F/+$1E mirror copies); "n" = operand bytes after the op byte:

| op | handler | n | semantics (from handler code) |
|---|---|---|---|
| $80 | $6999 | 3 | slide: rate, 16-bit target freq |
| $81 | $68A3 | 0 | loopend: dec counter, back to loop start unless zero |
| $82 | $6858 | 1 | loop: counter=arg, start=ptr+2 |
| $83 | $68D0 | 1 | gatelen default ($39) + gate mode ($36) |
| $84 | $68EE | 1 | durmode: 0=per-event duration byte, else sticky duration=arg |
| $85 | $6909 | var | rawsid: (reg,val) pairs while reg<$80, then 1 terminator byte |
| $86 | $698A | 0 | stop voice (INC active flag) |
| $87 | $6AD0 | 2 | jump: ptr = arg16 |
| $88 | $6A0B | 8 | pulse-program config (widths + 4 SMC curve params) |
| $89 | $6AA7 | 0 | pulse target select (0/1/2 per voice) |
| $8A | $6ABC | 2 | call: push ptr+3, ptr = arg16 |
| $8B | $6B31 | 0 | ret |
| $8C | $6B64 | 1 | transpose ($4E, added to note bytes) |
| $8D | $693F | 1 | waveform/gate -> $D404, sets vibrato-direction SMC flags |
| $8E | $6B7C | 4 | vibrato: delay, step, count, reload |
| $8F | $6BC1 | 4 | noise/drum config (2 params + 16-bit SMC freq) |
| $90 | $6C12 | 1 | gateoff time (counter value at which gate drops; $FF = legato) |
| $91 | $6C2A | 3 | detune/vibrato-2 periods |
| $92 | $6C60 | 1 | portamento mode |
| $93 | $6C78 | 0 | tie toggle (DEC of the note-path SMC flag) |
| $94 | $6C8A | 2 | slide default (SMC $6A05) |

**Validation**: `follin_script_decode.py` re-implements this grammar as a
~90-line tick simulator and replays 12,950 frames from the post-init image
alone. Per-handler dispatch counts match the instrumented-VM probe **exactly**
on all three voices (253/324/204 events over 14+17+14 handler targets), and
event totals match the fetch counts — the grammar is complete for this tune.

## 4. Structured-region design for the dispatch (Gate S)

The region form already exists: `jmpd` terminators emit `switch("goto")`
regions and handler arms nest via `_side` (render.py `_term_flow`). What is
missing is not a new region but **closure precision + materialization
hygiene**:

- **Switch subject**: the site's 16-bit operand word `m[C_lo] | m[C_hi]<<8`
  (for $6374: C=$6375/$6376) — same shape as the opcode-SMC
  `switch code[$pc]` precedent, over the operand cells instead of the opcode
  cell.
- **Cases**: handler pcs from the **paired** table image
  `{T_lo[i] | T_hi[i]<<8 : i in I}` — zip, not cross-product.
- **Invariant the codec verifies**: each operand cell has a single writer pair
  (`STA C_lo` fed by `LDA T_lo,X`, `STA C_hi` by `LDA T_hi,X`), both tables
  immutable, X unchanged between the two stores and the jmp, no aliasing
  store may-hit C_lo/C_hi. Then the target set is indexed by one variable.
- **Closure proof**: index range I — lower bound $80 is static (the `BPL`
  guard dominates the dispatch), the upper bound is the missing lemma (table
  extent / stream-byte value set). Until proven, the **existing guarded
  evidence envelope** applies unchanged: targets = observed pairs, walker
  faults on any other operand word, proof status `evidence`.
- **Materialization fix (the measured win — LANDED)**: only blocks in the
  **final** fixpoint target set may persist; round-transient
  over-approximations must be dropped (round-1's 16,128/5,040/3,364-member
  sets materialize 2,547 junk blocks that the final `evidence` 14/17/14 sets
  disown). `structured.collect_unreachable` now GCs `model.blocks` to the
  set reachable from play through static terms, call targets/returns and the
  final dyn/observed target sets, after the last fixpoint + resplit
  (measured: Ghouls 3,103 -> 627 blocks, 36.1% -> 99.8%, 1,714 -> 82 gotos;
  Agent_X_II 1,809 -> 344, 54.7% -> 99.1%, 777 -> 61).

Measured impact (`pruned_metrics`: drop never-executed blocks, keep the
already-present switch nesting): Ghouls goes from **3,103 blocks / 36.1%
structured / 1,714 gotos / 1,366 labels** to **556 blocks / 99.8% structured
(555 of 556 nested) / 87 gotos / 55 labels**. Agent_X_II: 1,809 blocks
(233 executed, 468 orphans all unexecuted, round-1 closure 16,128/4,356/3,894)
goes 54.7% -> **98.7%** (777 -> 53 gotos). The entire Gate-S ceiling on this
family is closure junk; even the evidence-frontier-goto share largely lives
in junk blocks. Under a future proven paired closure the ≤63 unexecuted
handler arms return as switch cases and the structured share stays ≥ ~97%.

## 5. M4 requirements (musical L2 for pointer-sequencer players)

- **Sequencer-state detection**: the Commando-style staircase detector finds
  nothing here (confirmed earlier); the pointer-sequencer class needs a
  **pointer-pair detector**: zp pairs used as high-frequency `(zp),y` fetch
  bases whose word value is piecewise-incrementing with jump/reset events.
  The `(zp),y` census (`b1_count`) found all three streams generically.
- **M4.2 replay must record**: (a) stream fetch events (voice, ptr, byte);
  (b) op boundaries — dispatch events at the jmpd sites plus consumed-byte
  counts (pointer delta at tick end); (c) pointer-rewrite events
  (call/ret/jump/loop) for pattern structure; (d) duration-store events
  (writes to $27/$28/$29) for tick boundaries. All four are cheap `PcodeVM`
  subclass hooks (the probe demonstrates each).
- **Gate-F L2 shape**: the rows+patterns+orderlist model is **not
  expressible** for this engine: there is no row grid (per-event frame
  durations 1-165, sticky-duration mode), patterns are `call`-tree
  subsequences with `loop` repeat counts (3-deep nesting), and instrument
  changes are inline `rawsid` register/value pairs. L2 for this family must be
  a **script-shaped tier**: per-voice event streams over ops
  {note(idx,dur), rest(dur), loop n, call/ret, jump, transpose, wave,
  vibrato, porta, gatelen/gateoff, durmode, rawsid}, i.e. a cleaned
  generalization of the player's own grammar. The tick simulator in
  `follin_script_decode.py` is the seed of that tier's reference player;
  full Gate F additionally needs the per-frame effect engine
  (vibrato/slide/pulse curves) carried as instrument frame-programs from the
  log layer (L1 delta-runs already capture them).
- **Naming/provenance**: freq table $6D35/$6D96 (97 entries), handler tables,
  song-base tables $730E-$7337 and the zp stream pointers all get names from
  the program layer (program-layer naming,
  now landed as sidprog data/symbols declarations).

## 6. Worked example: v1 script, top-level + one called pattern

Decoded from the subtune-0 image at base $7338 (raw bytes -> ops; excerpt of
`out/study/Ghouls_n_Ghosts.v1script.txt`, which holds the full 911-event
stream):

```
7338: 83 FF          gatelen FF
733A: 90 FF          gateoff FF          ; $FF = legato (gate never drops)
733C: 85 05 DF ... FF rawsid $D405=DF $D406=4F $D40C=00 $D40D=0F $D413=00
                       $D414=0F $D414=0F $D417=F2 $D418=1F   ; instrument+filter
7350: 8D 81          wave 81             ; noise, gate on
7352: 5F 2A          note 5F dur=42
7354: 85 05 00 FF    rawsid $D405=00
7358: 8E 01 39 00 00 vibrato 01 39 00 00
735D: 5F 56          note 5F dur=86
7364: 5D 80          note 5D dur=128     ; long held notes: legato lead
736B: 82 04          loop count=4
736D: 5A 80          note 5A dur=128
736F: 81             loopend             ; x4
...
73A0: 82 04          loop count=4
73A2: 15 28          note 15 dur=40      ; bass alternation
73A4: 21 78          note 21 dur=120
73A6: 81             loopend             ; x4
73A7: 85 ...         rawsid $D405=00 $D406=2F $D413=00 $D414=1F
73B1: 8A 84 7A       call 7A84           ; <- pattern (subsequence)
  7A84: 90 FF        gateoff FF
  7A86: 8D 15        wave 15
  7A88: 40 01        note 40 dur=1       ; grace note
  7A8A: 92 01        porta 01
  7A8C: 45 0A        note 45 dur=10      ; portamento up
  7A8E: 8E 01 17 00 00 vibrato 01 17 00 00
  7A93: 45 50        note 45 dur=80      ; held + vibrato
  ...
  7AE9: 8B           ret
73B4: 41 08          note 41 dur=8       ; continue after pattern
73C4: 83 02          gatelen 02
73C6: 8C 0C          transpose 0C        ; +12 semitone entries
73C8: 8D 50          wave 50
73CA: 82 02          loop count=2
73CC: 15 14          note 15 dur=20
...
```

Pattern reuse is real call reuse: e.g. `call 7AEA` at $73FB/$7411 (3x),
`call 7A14` at $749A (8x). The "order list" is simply the top-level script;
patterns are its call targets.

## 7. Corpus-status correction

The Gate-S item "997 of 3,103 blocks are RTS-trick/computed landings with no
static predecessor" should read: 997 orphan blocks (and 2,547 unexecuted
blocks total) are **never-executed materialization residue of transient
dispatch-closure over-approximation** at the three operand-SMC `jmp` sites;
the player performs no RTS dispatch at all. The needed work items are the
paired-index closure lemma (soundness.md blocker 3) and final-fixpoint-only
block materialization — not a new terminator form. The materialization item
is done (§4 `collect_unreachable`); the paired-index lemma remains open —
probed against the final analysis state, it does not follow from the
in-block relational machinery alone: the Ghouls operand stores land in a
predecessor block of the resplit jmp block (no in-block single-writer pair),
Bionic's handler tables are themselves mutable, and Wizball's naive pairing
does not even cover the observed target set.
