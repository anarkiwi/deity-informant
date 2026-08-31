# Prototype: trackerprog by transition — nine families, one pass

Design prototype for the layer above [tuneprog-architecture.md](tuneprog-architecture.md),
replacing the lift of [prototype-trackerprog.md](prototype-trackerprog.md) §6 and
its refusal table §8. The schema of that document is kept where it names data
(pitch, streams, instruments, score, `Acc`); what changes is **what the object
is** and **how it is obtained**:

- the object carries the tick as **data** — an ordered list of guarded assignments
  over a named STATE, TABLES and the SID (the *transition*) — plus the score and
  its token grammar (the *fetch*). This is anatomy §2's "each player is the same
  object" written out, and it is complete by construction: every store of the
  certified tick is one assignment;
- the tracker vocabulary — `Acc`, stream, `Ins`, prelude — is a **reading** of the
  transition, recognised where its forms hold and measured as coverage. It never
  decides whether a tune renders;
- there are no refusals, proposers or shape matchers. A term the lift cannot open
  is a defect of the lift, fixed there. The only stated boundary is the anatomy's
  own (§6.6): a digi mixer or a volatile input the score depends on.

Evidence base, all read for this document: [playroutine-anatomy.md](playroutine-anatomy.md)
§1–§8 (nine players to the byte), the six certified prototypes
([Commando](prototype-commando-floor.md), [GoatTracker 2](prototype-goattracker.md),
[JCH](prototype-jch.md), [SID Wizard](prototype-sidwizard.md), [Follin](prototype-follin.md),
[defMON](prototype-automatas.md)) and their printed tuneprogs. Citations `anatomy:N`,
`commando-floor:N` etc. are line numbers of those files.

Contents: 1 the object · 2 the universal player · 3 the lift · 4 the nine
families, byte by byte · 5 the tracker reading · 6 acceptance · 7 code plan.

---

## 1. The object

```
trackerprog = { meta, pitch, state, tables, score, fetch, tick, forms }
```

| section | content | source of truth |
| --- | --- | --- |
| `meta` | cadence (`cycles_per_tick`, calls per frame), voice order of the tick, provenance (tune, family, certificate digest), universal-player version | S0, certificate |
| `pitch` | the tuning and nothing else: a base note and a contiguous `u16[N]` of values **read** (Blackbird's quarter-tones as a 4N table of the sums, anatomy:2844), tuning annotation where `recover._freq` proves it. A value that is not in it is not a pitch, so it is not a note: see [prototype-commando-trackerprog.md](prototype-commando-trackerprog.md) §4.1-4.3 | S6 `freq_table` |
| `state` | STATE (anatomy:191-192): every cell the tick reads or writes outside the score tables — per-voice records `voice[v].f` (stride, copies) and globals — with width (8/16), the post-init value, and a role where S6 has one (`timer`, `cursor`, `acc`, `sid_image`, `phase`) | S3 regions, S6 views, post-init image |
| `tables` | TABLES (anatomy:193-195) other than the score: instrument records with named columns; wave/pulse/filter/speed/tempo/chord tables as byte rows; pointer tables. Bytes from the image, base and stride from S6 | S3/S6 |
| `score` | per voice: the order list (materialised over the horizon: `play(pattern, transpose, vol, tempo)`, `for`, `call/ret`, `jump`, `stop`, `horizon`) and the patterns as rows of **bytes** — the bytes the fetch consumed, per T2's record, one row per fetch event | T2 (#299) |
| `fetch` | the sequencer as a token grammar: the row clock (which cell, reload, boundary test), and per voice the **fetch body** — assignments over `byte` (the current byte), `byte[k]`, and cells, with an exit guard — run over a row's bytes until the exit holds (§2). Loops that walk a row are this construct and no other | fetch regions (#305), resolved per byte class |
| `tick` | the transition: the tick's stores outside the fetch, in program order, each `target ← value if guards`, targets being state cells or SID registers, values and guards over cells, tables, `pitch`, constants, the copy index `v` and the two closed loop forms (§3.3) | symbolic execution of the effect arm |
| `forms` | the tracker reading (§5): `instruments`, `accs`, `streams`, `preludes`, each pointing at the `tick`/`fetch` entries it summarises; per-tune coverage | derived |

Two invariants, checked in code (`certify.schema_check`):

1. **Closed.** Every name in `fetch` and `tick` is a `state` cell, a `tables`/`pitch`
   entry, `byte`, `v`, or a constant. No temporaries, addresses, program blocks,
   phis, machine registers.
2. **Ordered and total.** `tick` is one list; the player applies it top to bottom
   with no other control flow than each entry's guards. What a family's player
   does with jumps, patched operands, unrolled copies, ghost images and flush loops
   is already spent by S4–S6 and by this ordering (the GT2 flush is 25 entries
   `sid.reg[k] ← ghost.reg[k]`, k = 24..0, at the top of the list — the idiom is
   in the data, exactly).

A `tick` entry:

```
{ target: "voice[v].acc" | "sid[v].freq_lo" | "cutoff_hi" | …
, value:  expr            # over cells, tables, pitch, byte, v, consts; ops + - & | ^ << >> sext carry
, when:   [guard, …]      # conjunction, each a comparison or bit test over the same terms
, site:   "$52E2" }       # provenance only; exempt from the closure check
```

The expression language is the S4 IR's own (`ir.enc`: `Bin`, `Load`, `R16`, `Const`)
restricted to named leaves — no new vocabulary. Selections (`x if g else y`) print
as nested guards; `carry(a + b)` and `sext(k, x)` are the IR's.

---

## 2. The universal player

Normative. One fixed procedure over the object; the source family survives only in
`meta`:

```
tick():
    for v in meta.voice_order:                     # the program's own order
        clock(v)                                   # fetch.clock: step the row cell; boundary?
        if boundary(v):
            row = score.next_row(v)                # order program → pattern → next row's bytes
            run_fetch(v, row)                      # the token grammar over the row's bytes
    for e in tick:                                 # the transition, in order, v bound per entry
        if all(e.when): write(e.target, e.value)
    observe()                                      # grid.reduce_tick over this tick's SID writes

run_fetch(v, row):
    i = 0
    while True:
        byte = row[i]
        for e in fetch.body[v]:                    # assignments over byte, byte[k], cells
            if all(e.when): write(e.target, e.value)
        i += fetch.consume(v)                      # how many bytes the class took (an expr, usually 1)
        if fetch.exit(v): break                    # the terminal class: a note, a rest, end
```

- `write` to a `state` cell is a store; to `sid.*` it is appended to the tick's SID
  write list. The observable is `grid.reduce_tick` (`TickObs`), exactly as
  prototype-trackerprog §2: ctrl/AD/SR edges per voice in order, levels for the
  rest. The certificate compares that against `Verifier.obs` over the whole
  certified horizon.
- Where a family runs its sequencer *inside* the voice's effect list (Hubbard:
  fetch then `soundwork` skipped on that tick; Follin: `if --dur == 0: interpret`),
  the boundary is an entry's guard and the fetch is placed by the lift at its
  program position; the player's order above is the general case, the list's
  guards make it exact. The lift records `fetch.position[v]` = the index in `tick`
  before which the fetch runs.
- A second cadence (defMON's main/sub, Walker's 9-call tick) is a state cell
  (`call_counter`) in guards, nothing in the player.
- Voice-order inside a tick and cross-register order are preserved by the list;
  the observable drops what §2 drops and says so.

The player is ~150 lines and has no branch on `meta.family`.

---

## 3. The lift

Input: the certified tuneprog — `tuneprog.S4.json` (program), `tuneprog.S6.json`
(naming plane), the post-init image, `certificate.json`; T2's cursor nest for the
score (#299). Output: the object above. Four steps, each total over its input.

### 3.1 STATE and TABLES

Every S3 region the tick reads or writes is a `state` cell set or a table:

| region | becomes | rule |
| --- | --- | --- |
| `state` kind, stride `k`, `n` copies indexed by the voice loop (S6 `voice[]`, `rec[]`, transpose split) | per-voice record fields | S6's views as they are; copies fold into `v` |
| `state` kind, one copy | global cell | — |
| SMC operand cell (`load` at its instruction) | a cell like any other (its name is S6's) | anatomy §5.5: SMC is storage |
| `sid_image` region | state cells; the flush is entries in `tick` | anatomy:2866 |
| `const`/`init_constant` read through a `cursor` or a pointer the score selects | `tables` rows (stride from S6; the instrument's columns named by the register their bytes reach, else by column index) | anatomy §6.3 |
| `freq_table` | `pitch` | S6 role |
| score tables (order lists, patterns) | `score` | T2 |

Initial values: the post-init image, never zero (anatomy:2731 — Galway, Walker,
Blackbird carry live residue).

### 3.2 The fetch

The fetch regions of #305 (`region.fetch`: blocks tainted by a score-byte read,
cut to minimal single-entry regions) are the sequencer. Per voice:

- the **row clock** is the region's guard: the `timer`/`phase` cell it tests and
  the reload the tick performs (anatomy §6.4 "find the phase variable first");
- the **body** is the region's stores and exits opened by `resolve` over the
  region's own definitions, with every score read replaced by `byte`
  (`fetch.Byte`) relative to the row cursor — what `fetch.py` does today for
  Commando (27 of 27 stores) and GT2 (16 of 17);
- a region whose body **loops over bytes** (JCH `p_10E9`, Galway/Follin command
  loops, SW's orderlist reader `p_17C8`, Blackbird's three `prepare` passes, the
  `$85` list) lifts as the loop body over `byte` with the loop's exit as
  `fetch.exit`. It is not unrolled and not refused: it is the grammar.

The row's bytes and count come from T2's fetch record, so `score` rows are exactly
the bytes the program read; patterns are keyed on their rows, the order list on
the pointer table the fetch indexes.

### 3.3 The transition

Every store of the tick outside the fetch regions, in reverse-postorder statement
order (`graph.rpo`, the order #297 proved), opened to an expression over named
cells:

- **values**: `resolve.Resolver` reaching definitions (the mechanism of
  `producers.py`), stopped at every S6 cell, parameters bound per caller path,
  scratch pointers opened, copy index left free as `v`;
- **guards**: the store's transitively closed control dependences (`accshape`,
  #296) with a loop's own back-edge test removed (#297), each opened the same way;
- **procedure summaries, not path enumeration**: a callee's stores are opened once
  over its parameters and composed at each call site with the caller's arguments
  and guards. This replaces `MAXARMS`/`DEPTH` budgets (GT2's 9–12-hop chains) with
  a memoised summary per procedure — interprocedural dataflow, not a search;
- **the two loop forms** — the only loops inside any effect arm across the nine
  (anatomy:415, 2843, 2861): a counted loop whose body is one shift is `x >> n`
  with `n` the count cell's entry value (Hubbard `$51E4`, GT2 `p_12E5`, JCH's
  `LSR/ROR`); a counted loop whose body is one add is `x + n·d` (Hubbard
  `$520B`, Walker's pre-load). Both are read off the loop's induction cell and
  body, exactly; Galway's segment `while` is bounded by its segment count and
  unrolls to guarded entries.

The list is complete: `len(tick)` equals the number of store sites outside the
fetch, each site contributing one entry per caller path. A term that remains a
machine register or an SSA temporary after opening is an assertion failure in
the lift — a bug to fix, with the site named in the traceback — never an object
field.

### 3.4 The reading

§5. Derived from `tick`, `fetch` and `tables`; writes `forms` and the coverage
numbers.

---

## 4. The nine families, byte by byte

Each family below is the object's four data halves as the anatomy gives them,
with the tick written as the transition list in the anatomy's own pseudocode
vocabulary (its §3.x.3), so the lift's output for the certified six can be
checked line by line against the print, and the two prose-only families are
shown to fit the same object with nothing added. `v` is the voice; SID targets
are `sid[v].reg`; `gs ⇒` prefixes guards.

### 4.1 Hubbard — Commando (anatomy §3.1, commando-floor §4)

**state** (stride 1, X = voice; commando-floor:137): `voice[v].{pos, pat, len,
row, ctrl, note, ins, porta, freq(u16 $551D|$551A), pwdelay, pwdir}`, globals
`counter $5525, mstatus $5519, speedctr $5513, speed $5517, regofs $54EB`.
**tables**: `INS[13] × 8` at `$5591` (pw_lo, pw_hi, ctrl, ad, sr, vib, pspeed,
fx) — *mutable* (the pulse sweep writes columns 0–1, anatomy:389), so it is a
`state` record with 13 copies indexed by `voice[v].ins`, not a const table;
`SIDOFS[3]`, `SPEED[3]`. **pitch**: `FREQ[80]` u16 at `$5448`, notes 16..95 — the tuning, and no more.
The 25 overrun reads into `voice[].ctrl`/`pwdir` (commando-floor:301-310) are
cell reads in the transition, not pitch entries; the layer above places them
where they belong, and the hand exemplar
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md) §4.2-4.3)
shows which is which — the arpeggio's own behaviour past the tuning, and the
drum instruments' own pitch modulator. **score**: `TRACK[v]` bytes (pattern nrs,
`$FF` loop, `$FE` stop) → `PAT[p]` rows.

**fetch** (anatomy:324-343). Row clock: `speedctr` countdown reloaded from `speed`;
boundary when `speedctr == speed` and `--len[v] < 0`. Body over the row's bytes:

```
row ← byte ; len ← byte & $1F ; porta ← 0 ; gate ← $FF
byte & $40 ⇒ gate ← $FE                                 # exit: append
¬(byte & $40) ⇒ pat += 1
¬(byte & $40) ∧ (byte & $80) ⇒ byte[1] < $80 ? ins ← byte[1] : porta ← byte[1] ; pat += 1
¬(byte & $40) ⇒ note ← byte[k] ; sid[v].freq_hi, freq_lo ← FREQ[note] ; freq ← FREQ[note]
ctrl ← INS[ins].ctrl ; sid[v].ctrl ← INS[ins].ctrl & gate ; sid[v].pw_lo/hi, ad, sr ← INS[ins].*
pat += 1 ; PAT[p][pat] == $FF ⇒ pat ← 0 ; pos += 1        # eager peek: the terminator
```

Order program: `TRACK[v][pos]` is `play(p)`, `$FF` → `jump(0)`, `$FE` → `stop`.

**tick** (soundwork, anatomy:347-369, one entry per store; `l = row & $1F`):

```
INS[ins].vib ≠ 0 ⇒ phase ← counter & 7 ; phase ≥ 4 ⇒ phase ^= 7
INS[ins].vib ≠ 0 ⇒ step ← (FREQ[note+1] − FREQ[note]) >> (INS[ins].vib + 1)         # shift loop, closed
INS[ins].vib ≠ 0 ⇒ f ← FREQ[note] ; l ≥ 6 ⇒ f ← f + phase·step                       # repeat loop, closed
INS[ins].vib ≠ 0 ⇒ sid[v].freq_lo, freq_hi ← f
INS[ins].fx & 8 ⇒ INS[ins].pw_lo ← INS[ins].pw_lo + INS[ins].pspeed + carry($51FA)   # the inherited C: a named flag
INS[ins].fx & 8 ⇒ sid[v].pw_lo ← INS[ins].pw_lo
¬(fx & 8) ∧ pspeed ≠ 0 ⇒ pwdelay −= 1 ; pwdelay < 0 ⇒ pwdelay ← pspeed & $1F
… ∧ pwdir == 0 ⇒ INS[ins].pw += pspeed & $E0 ; INS[ins].pw_hi & $F == $E ⇒ pwdir += 1
… ∧ pwdir ≠ 0 ⇒ INS[ins].pw −= pspeed & $E0 ; INS[ins].pw_hi & $F == $8 ⇒ pwdir −= 1
… ⇒ sid[v].pw_hi, pw_lo ← INS[ins].pw
porta ≠ 0 ⇒ freq ← freq ± (porta & $7E) by porta & 1 ; sid[v].freq_lo, freq_hi ← freq
fx & 1 ∧ freq_hi ≠ 0 ∧ len ≠ 0 ∧ l − 1 < len ⇒ sid[v].freq_hi ← freq_hi ; sid[v].ctrl ← $80
fx & 1 ∧ … ∧ ¬(l − 1 < len) ⇒ sid[v].freq_hi ← freq_hi ; freq_hi −= 1 ; sid[v].ctrl ← ctrl & $FE
fx & 4 ⇒ sid[v].freq_hi, freq_lo ← FREQ[note + (counter & 1 ? 12 : 0)]
```

plus the tick-boundary cut `¬(row & $20) ∧ len == 0 ⇒ sid[v].ctrl ← ctrl & $FE ;
sid[v].ad, sr ← 0` and the voice-loop tail `music_allowed ← …` (a global cell the
guards read). This is the print's 20 SID producers (#306, step 3) with the
non-SID stores added — nothing in it is new; the `carry($51FA)` is #297's named
flag, closed because the flag's defining site (`CMP #6` at `$51FA`, anatomy:422)
is in the list before it.

**reading**: free slide `Acc(freq, delta field(porta,$7E), phase bit(porta,0),
wrap)`; vibrato `Producer(freq, set, FREQ[note] + repeat(tablestep(FREQ,note,vib+1), phase(counter)))`;
pulse bounce `Acc(INS[ins].pw, reflect [$8xx,$Exx] projected, rate pwdelay, phase pwdir, scope instrument)`;
pulse run `Acc(pw_lo, const(pspeed) + carry, wrap 8)`; drum and arpeggio as `set`
producers on `fn(counter)`; `Ins = {adsr: (INS.ad, INS.sr), prelude: null, accs by
fx bits}`. Coverage: every `tick` entry is under one form.

**Hand exemplar.** [prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)
is this family transliterated by hand into the tracker reading and certified on
one universal player: all three subtunes, 11,780 ticks each, 0 divergences on
§2's observable. It is the oracle a lift of this family should reproduce, and it
records the thirteen schema additions the tune forced — including three rules
the reading here should keep. A pitch table holds the tuning and nothing else,
and the modulators are expressions over it (`tablestep` and the octave are read,
never tabulated per note). Where a transposition leaves the tuning, that is the
*modulator's* own behaviour at its bound, with its own private state, indexed by
how far past it went and never by a note. Where a sound has no pitch at all --
Hubbard's drum, whose frequency is the waveform the other voices are sounding --
that is a modulator on the instrument, and the score gives the event no note.

### 4.2 Galway — Comic Bakery (anatomy §3.2; certified, [prototype-galway-trackerprog.md](prototype-galway-trackerprog.md))

**state**: `D[v]` 39 bytes (FMG0..3 u16, FMD0..3, FMDLY, FMC, PMD0/1, PMDLY, PMC,
PMG0/1 u16, PINIT u16, VFREQ u16, VWFG, VADSC, VRC, FCURR u16, FMD0C..3C, PCURR u16,
PMD0C/1C; anatomy:499-519), `S[v]` 29 + the 8-deep stack `ST L/H/C`, zero page
`PC[v] u16, CLOCK[v], SP[v], F9, TR[v]`, globals `MFL, vol, filter shadows`. The
three unrolled copies fold by the diff rule (anatomy:2975-2982; `siblings.py`
does this for Follin) into `v`. **tables**: `IDRT[17]` (song-loaded, so state),
`vt[15]` command vectors (the switch), tune pointer table. **pitch**: `HiFrq/LoFrq`
95 + entry `$5E = 0`.

**fetch** (anatomy:549-568): row clock `--CLOCK[v] == 0`, run bit `F9.run[v]`. Body:
a command loop — `byte ≥ $C0 ⇒ dispatch (byte−$C0)/2` with the 15 handlers'
assignments (`Ret, Call, Jmp, CT, JT, Moke S[op1]←op2, For, Next, FLoad, load10/14/5,
DMoke, Code, Transp`), each ending `PC += len` or `PC ← op16`; exit at a note byte:

```
raw ← byte ≥ $60 ; b ← byte − (raw ? $60 : 0)
b ≠ $5F ∧ b ≠ $5E ⇒ b += TR[v]
b ≠ $5F ∧ MFL.bit[v] ∧ F9.free[v] ⇒ sid[v].sr, ad ← S[$1A], S[$19] ; sid[v].ctrl ← S[$18] | 8 ; sid[v].ctrl ← S[$18]
   ; sid[v].pw_hi, pw_lo ← S[$17], S[$16] ; D.VWFG ← S.wave ; D.VFREQ ← freq[b] ; sid[v].freq ← D.VFREQ
   ; D.PMC ← S.PMC ; PMC ⇒ D[$E..$17] ← S[..] ; PCURR ← PINIT ; PMD*C ← PMD* ; D[0..$D] ← S[0..$D]
   ; S.FMC & 8 ⇒ D[$A] ← b  else FCURR ← VFREQ ; FMD*C ← FMD* ; VADSC, VRC ← S[$1B], S[$1C]
CLOCK ← raw ? byte[1] : IDRT[byte[1]] ; PC += 2
```

The order program is the sequence itself: `Call/Ret/For/Next/Jmp/CT/JT` are
`call/ret/for/jump` of prototype-trackerprog §3.6's grammar with the 8-deep stack
stated; `Moke/DMoke/FLoad/load*` are `set(cell, byte)` commands on the instrument
record — the instrument *is* score data here (anatomy:613-615).

**tick** (engine, anatomy:570-588): `VRC == 0 ⇒ skip all`; the gate timer
(`VWFG & 8 ⇒ CLOCK < VADSC ⇒ …` else `--VADSC == 0 ⇒ sid[v].ctrl ← VWFG & $F6`;
`--VRC == 0 ⇒ sid[v].regs ← 0; F9.free ← 1`); the PM two-segment ramp
(`PMDLY ⇒ −−` else `PMD0C ⇒ PCURR += PMG0 ; PMD0C −= 1` / `PMD1C …` / loop bits
`$81` → reload) with `sid[v].pw ← PCURR`; the FM four-segment ramp or the arp
list (`FMC & 8 ⇒ i ← D[$C] ; i < 0 ⇒ i ← D[$B] ; FCURR ← freq[D[$A] + D[i]] ; D[$C] ← i − 1`)
or the bend-during-delay, with `sid[v].freq ← FCURR`. The segment `while` is
bounded by 2 (PM) / 4 (FM) segments per tick and unrolls to guarded entries.

**reading**: FM/PM segments are `streams` of `Acc` segments (`delta const(FMGk)`,
hold `FMDk`, terminator by `FMC & $81`); the arp list is a `pitch` stream; gate
and release timers are `prelude`-shaped `set` steps at `early = VADSC`. Every
entry is under a form.

**Hand exemplar.** [prototype-galway-trackerprog.md](prototype-galway-trackerprog.md)
is this family transliterated by hand and certified on the same universal
player, at 0 divergences over **all fourteen subtunes** (29,911 ticks,
write-for-write identical per register). Three of the sketch above are corrected
by the render. The segments' gradients and durations are *cells* and not stream
rows, because `DMoke` pokes them mid-note — so an `Acc` per segment with a
`delta` reading the cells, and the stream form is the arpeggio's alone. The gate
is not a `prelude`: both its modes are guarded rows of a stream the tick ends on,
because the relative mode compares `VADSC` against the row clock rather than
scheduling anything. And the *score* is where the family costs the layer: the
counted loop nests (§3.6), and a `stop` ends a sequencer and not a voice.

### 4.3 GoatTracker 2 (anatomy §3.3, prototype-goattracker)

**state** (stride 7, X = v·7; anatomy:725-736): blocks A–E → `voice[v].{songptr,
trans, repeat, pattptr, packedrest, newfx, newparam, fx, param, newnote, waveptr,
wave, pulseptr, pulsetime, pattnum, tempo, counter, note, instr, gate, vibtime,
vibdelay, wavetime, gatetimer, lastnote}`; `ghost[v].{freq u16, pw u16, ctrl, ad,
sr}` + `ghost.{cutoff_lo, cutoff_hi, res_route, mode_vol}` (`sid_image`); SMC
globals `initpending $110D, filtstep, filttime, cutoff, filtctrl, filttype,
masterfader, effectnum $10AC, speedcmp $1096, cscount, csresty`, `funktempo[2]`.
**tables** (1-based, anatomy:740-751): `INS[30]` 9 columns (ad, sr, waveptr,
pulseptr, filtptr, vibparam, vibdelay, gatetimer, firstwave); `WAVE[100]`
left/right; `PULSE[29]`, `FILT[43]`, `SPEED[18]` left/right; `songtbl`, `patttbl`.
**pitch**: 96 lo/hi. **score**: orderlists (`$D0+n` repeat, `$E0+t` transpose,
`$FF pos` loop) → 33 patterns of `[instr][fx param] note|rest|keyoff|keyon|packed`.

**fetch**: two regions, both on the row clock `counter[v]` (`DEC; BEQ tick0; BPL
effects; reload tempo`, anatomy:776-781):

- the **row fetch** at `counter == gatetimer` (anatomy:836-851):
  `byte < $40 ⇒ instr ← byte ; consume` · `byte < $60 ⇒ newfx ← byte & $F ; newfx ⇒ newparam ← byte[1]
  ; byte ≥ $50 ⇒ exit rest` · `byte ≥ $C0 ⇒ packedrest …` · `byte == $BD ⇒ exit` ·
  `byte > $BD ⇒ gate ← byte | $F0 ; exit` · `newnote ← byte + trans ; newfx ≠ 3 ∧ instr < $18 ⇒
  ghost[v].sr ← 0 ; ghost[v].ad ← $F ; gate ← $FE` · `pattptr ← PAT[y+1] == 0 ? 0 : y + 1`;
- the **sequencer step** at `counter == 0 ∧ pattptr == 0` (anatomy:824-833):
  `byte == $FF ⇒ y ← byte[1]` · `byte ≥ $E0 ⇒ trans ← byte − $F0 ; consume` ·
  `byte ≥ $D0 ⇒ repeat …` · `pattnum ← byte ; songptr ← y + 1`.

The tick-0 note init (anatomy:783-799) is transition, not fetch — it reads cells
only: `newnote ≠ 0 ⇒ note ← newnote − $60 ; fx ← 0 ; vibdelay ← INS[i].vibdelay ;
param ← INS[i].vibparam ; newfx ≠ 3 ⇒ wave/gate from firstwave ; pulseptr, filtstep, waveptr
← INS[i].* ; ghost[v].sr, ad ← INS[i].sr, ad` then the command dispatch `switch
newfx` — 15 arms, each a few assignments (5/6 set ghost ad/sr, 7 wave, 8/9/A
re-point a cursor and zero its time, E funktempo, F tempo), the switch being
guards `newfx == k` (the patched JSR is spent by S2's `switch` over `$144A`).

**tick** (anatomy:801-822, 855-866): the flush `sid.reg[k] ← ghost.reg[k]`, k =
24..0, first; the filter program (`filtstep ≠ 0 ⇒ … FILT[y].left` three-way:
`== 0 ⇒ cutoff ← right`, `< $80 ⇒ filttime ← left ; cutoff += right`, `≥ $80 ⇒ filttype ←
left << 1 ; filtctrl ← right`; `filtstep ← left[y+1] == $FF ? right[y+1] : y + 1`;
`ghost.cutoff_hi ← cutoff ; ghost.res_route ← filtctrl ; ghost.mode_vol ← filttype | masterfader`);
per voice the wave step (`waveptr ≠ 0 ⇒ w ← WAVE.left[y] ; w < $10 ⇒ wavetime ≠ w ⇒ wavetime += 1
else wave ← w − $10 ; waveptr ← left[y+1] == $FF ? right[y+1] : y + 1 ; wavetime ← 0 ; n ← right[y]
; n < $80 ⇒ abs ← n else abs ← (n + note) & $7F ; lastnote ← abs ; vibtime ← 0 ; ghost[v].freq ← FREQ[abs]`),
the continuous effect `switch fx` (0 instrument vibrato after `vibdelay`; 1/2
`ghost[v].freq ± speed`; 3 toneporta: the 16-bit compare chain against
`FREQ[note]` then snap in `p_1327`; 4 vibrato: `vibtime += 2 ; vibtime > speedcmp ⇒
vibtime ← ~vibtime ; vibtime & 1 ⇒ ghost.freq −= speed else += speed`) with `speed ←
SPEED[param].left & $80 ? (FREQ[lastnote+1] − FREQ[lastnote]) >> SPEED[param].right : SPEED[param]`
(the shift loop `p_12E5`, closed); the pulse step (`PULSE.left ≥ $80 ⇒ ghost.pw ←
(left, right)` / `< $80 ⇒ ghost.pw += sext(right) ; pulsetime …` / `$FF ⇒ jump`); and
the single exit `ghost[v].ctrl ← wave & gate`.

**Hand exemplar.** [prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)
is this family transliterated by hand into the tracker reading and certified on the
same universal player Commando renders on: both builds, 8,236 and 8,659 ticks,
0 divergences and the write lists *identical* rather than permuted, with the
inherited loop claim re-verified on the render. It records the seven forms the
family forced -- a shadow and its flush, a countdown row clock, a fetch that runs
ahead of its row, a held row command, a stream step's `op`, `clamp` and its
degenerate `take`, and a prologue -- and, in its §8, the five things the printed
tuneprog could not settle.

**reading**: `Ins = {adsr, prelude: early = gatetimer, rows set(sr,0) set(ad,$F)
set(ctrl, wave & $FE), note row set(ctrl, firstwave), streams: wave/pulse/filter
by pointer, accs: [vibrato]}`; `WAVE`/`PULSE`/`FILT` are streams with the three
row kinds; vibrato the coupled pair of Accs (`vibtime` reflect-complement,
freq `tablestep` or `const`); toneporta `clamp(FREQ[note])` with `links reset(vibtime)`;
funktempo `set_tempo(stream)`. Coverage: complete.

### 4.4 SID Wizard 1.6 / 1.9 (anatomy §3.4, prototype-sidwizard)

**state** (stride 7 bunches at `$1024`, anatomy:1041-1081): `voice[v].{freq u16,
pw u16, wfghost, ptngate, pweepcnt, packcnt, spdcnt, seqpos, ptnpos, wftpos, pwtpos,
arpscnt, curptn, curnot, dpitch, curifx, curins, curfx2, curval, slidevib, freqmod
u16, videlcnt, vibfrequ, vibracnt, transp, tmpptr, tmppos, arpsped, pkbdtrk,
curchord, chordpos}`; the 27 patched immediates as globals (`mainvol, fltband,
resonib, fswitch, ckbdtrk, ctfhgho, ctflgho, fltctrl, fltposi, cwepcnt, tablrst,
inscntrl, …`); `TEMPOTBL[8]` is state (big-FX write it). **tables**: instruments
variable-length records (16-byte header + WF/PW/filter rows), `CHORDS`, the blob
pointer tables (after init's relocation: constants), `EXPTABH`. **pitch**:
`FREQTBL/H[96]`, index 0 a pad. **score**: orderlists (`< $80` pattern, `$80–$9F`
transpose, `$A0–$AF` volume, `$B0–$EF` tempo, `$FE` stop, `$FF pos`) → patterns of
1–4-byte rows with bit-7 continuation, `$70+n` packed rest, `$FF len` end.

**fetch**: the row clock is `spdcnt[v]` against `TEMPOTBL[tmppos]` with the
V-flag loop test (anatomy:1109-1114) — the three ticks `0/1/2` are guards on
`spdcnt`. `READROW` at tick 0 is the body over the row's 1–4 bytes:
`curnot ← byte & $7F ; byte & $80 ⇒ curifx ← byte[1] & $7F ; byte[1] & $80 ⇒ (byte[2] ≥ $20 ⇒
curfx2 ← byte[2] else curfx2 ← byte[2] ; curval ← byte[3])`; `curnot ∈ $70..$77 ⇒ packcnt ←
curnot − $6E`; `ptnpos ← last byte read`. The orderlist advance at tick 1
(`PTN_SEQ`, `SEQSUB` — the `p_17C8` return of a value and its flags, #303) is the
order program: `byte < $80 ⇒ curptn ← byte` · `$80–$9F ⇒ transp2 ← byte − $90` ·
`$A0–$AF ⇒ seqvolu` · `$B0–$EF ⇒ seqtempo` · `$FE ⇒ stop` · `$FF ⇒ jump(byte[1])`.

**tick** (anatomy:1133-1192): `HARDRST` at ticks 0/1 with the tick number as the
mask (`INS[curins][0] & (spdcnt == 0 ? 2 : 1) ⇒ ptngate ← $FE ; wfghost &= $FE ; sid[v].ad, sr ←
INS[1], INS[2]` — 1.9 writes sr then ad: two list entries in the build's order,
which is the whole difference, exactly); `TICK_2` note start (`dpitch ← curnot +
INS[9] + transp ; wftpos ← $10 ; ptngate ← $FF ; arpscnt ← $FF ; videlcnt ← INS[6] ; vibfrequ,
vibracnt, freqmod from INS[5] & dpitch via EXPTABH ; curchord ← INS[8] ; chordpos ← CHDPTR[..] ;
pwtpos ← INS[$A] ; filter route by INS[$B] ; sid[v].ad, sr ← INS[3], INS[4] ; sid[v].ctrl ← INS[$F]`);
`VIBSLIDE` by `slidevib` (`$00 ⇒ freqmod += videlcnt` · `$10/$20/$30 ⇒ delay` · `$81/$82 ⇒
freq ± freqmod` · `$83 ⇒ porta toward FREQTBL[dpitch] by freqmod` · then `vibracnt == 0 ⇒
vibracnt ← vibfrequ ; vibracnt −= 1 ; 2·vibracnt < vibfrequ ⇒ freq += freqmod else −=`);
`FILTPRG` (owner voice only: `fltctrl == v`) over the instrument's filter rows
(`< $80 ⇒ sweep by sext(row[1]) into the 11-bit split (cwepcnt frames)` · `$FE ⇒ jump` ·
`$FF ⇒ hold` · `≥ $80 ⇒ fltband, resonib, ctfhgho ← …`); `SETPWID` the same three-way
over PW rows with keyboard tracking `EXPTABH[pkbdtrk + dpitch] − EXPTABH[.. − 1]`;
`WFARPTB` (`--arpscnt ≥ 0 ⇒ skip ; arpscnt ← arpsped & $3F ; row ← INS[wftpos] ; $FF hold ; $FE jump ;
row[0] < $10 ⇒ arpscnt ← row[0] else wfghost ← row[0] & ptngate ; row[1]: $7F chord step /
$80 keep / rel / abs → freq ← FREQTBL[pitch] ; detuner ← row[2] ; wftpos += 3`); `WRPITCH`
`sid[v].freq_lo ← freq_lo + detuner ; sid[v].freq_hi ← freq_hi + carry` ; `sid[v].ctrl ← wfghost`;
`COMMONREGS` `sid.res_route ← fswitch | resonib ; sid.mode_vol ← mainvol | fltband ;
sid.cutoff_hi ← (ckbdtrk ? EXPTABH[ckbdtrk + dpitch[fltctrl]] : 0) + ctfhgho + c ;
sid.cutoff_lo ← ctflgho`. The three dispatchers (`BCC` offsets, `JMP` word table)
are S2 switches; each FX handler is one or two assignments under `curifx == k`.

**Hand exemplar.** [prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md)
is this family transliterated by hand into the tracker reading and certified on
the same universal player Commando and GoatTracker 2 render on: both builds,
8,084 and 14,465 ticks, 0 divergences and the write lists *identical* rather
than permuted -- earned rather than free, because this family has no ghost flush
-- with both inherited loop claims re-verified on the render. It records the
seven forms the family forced (a counter row clock whose phases are guards, a
prelude belonging to the row's instrument, a stream divider kept in a cell, a
step's epoch, an edge written twice in one tick, a global channel committed
after the voices, a producer that moves no cell), and in its §8 what the two
versions really differ in.

**reading**: instrument = `Ins{adsr: (INS[3],INS[4]), prelude: rows by INS[0] bits
at early 2/1 (1.6: ad,sr; 1.9: sr,ad), note row set(ctrl, INS[$F]), streams: wf/pw/filter
rows at wftpos/pwtpos/fltposi, accs: [slide/vib by slidevib]}`; the WF/PW/filter
rows are streams (3-byte rows: set / sweep-N / jump / hold); keyboard tracking
`tabcell(EXPTABH[..])`; chords a `pitch` stream; tempo programs `set_tempo(stream)`.
Coverage: complete.

### 4.5 JCH NewPlayer V20 (anatomy §3.5, prototype-jch)

**state** (struct-of-arrays, X = track; anatomy:1361-1384 for the 4-track build,
3-track in the plain V20): `voice[v].{tie, vib, slide, pos, dur_staged, dur, freq u16,
wave, hrpend, vibctr, vibreload, depthinc, depthacc, vibdir, viboff u16, vibshift,
pulseidx, pulsectr, pulse u16, pulsedir, waveidx, wavectr, wavereload, slidespd u16,
slidedir, slideacc u16, transp_staged, instr_staged, note_staged, slide_staged,
vib_staged, gate_staged, ad, sr, srover}`, header cells `enabled, note, transp,
gate, instr·8` (`$4006–$4020`), globals `d417 shadow, tick, speed, filtidx,
filtctr, cutoff, filttype, funk`. **tables**: `FREQ[96]` (pitch), `WAVE` A/B 102
columns, `FILT[6]×4` (entry 0 = funk tempos + filter track), `PULSE[16]×4`,
`INS[31]×8` (ad, sr, flags, filter, filtidx, pulseidx, waveidx, relwaveidx) —
mutable by the super default (`$423B` writes columns 6/7: state record),
`SUPER[20]×2`, pattern pointers. **score**: tracks `[$80|T] P`, `$FF` restart,
`$FE` stop → patterns of `[cmd]* step` (`$80–$9F` dur|tie, `$A0–$BF` instr,
`$C0–$FF` super, `$00` rest, `$01–$7D` note, `$7E` hold, `$7F` end).

**fetch** (`PREFETCH` at `tick == 2 ∧ dur == 0`, anatomy:1414-1434 — the
row-walking loop `p_10E9` *is* this construct): row clock `tick` countdown
reloaded from `speed` (or the funk pair). Body over bytes:

```
byte ≥ $80 ⇒ decode: $8x ⇒ dur_staged ← byte & $F ; tie ← byte & $10
             $Ax ⇒ instr_staged ← (byte & $1F) << 3 ; srover ← INS[..].sr
             $Cx ⇒ SUPER[n]: type $0/1/2 slide(dir, speed) ; $6 vibrato(depthinc, half, shift)
                             ; $9 srover ← p2 ; $E speed ← p2 ; else INS[p1].wave, relwave ← p2
             ; consume                                              # loop continues
byte == 0  ⇒ tie += 1 ; gate == $FF ⇒ gate_staged ← $FE ; exit
byte == $7E ⇒ tie += 1 ; gate_staged ← $FF ; exit
else       ⇒ note_staged ← byte ; ¬slide_set ⇒ slide_staged ← 0 ; ¬vib_set ⇒ vib_staged ← 0 ; gate_staged ← $FF ; exit
pos += 1 ; PAT[pos] == $7F ⇒ pos ← 0 ; trackptr += 1 ; t ← TRACK[..] ; t == $FF ⇒ restart ; t == $FE ⇒ stop
```

then the prefetch epilogue in the transition: `¬tie ⇒ gate ← $FE ; HR ⇒ ad, sr ←
$F, 0 ; sid[v].ad, sr ← $F, 0`. `COMMIT` at `tick == 0 ∧ --dur < 0`: `live ← staged`
(eight copies), `¬tie ⇒ waveidx ← INS.wave ; HR ← INS.flags & $80 ; wavectr ← flags & $F ;
pulseidx ← INS.pulse ; PULSE[idx].init ≠ $FF ⇒ pulse ← … ; filter by INS.filt nibble → sid.res_route
; ad ← INS.ad ; sr ← srover ; sid[v].ctrl ← 9` — all cell reads, so transition.

**tick** (`EFFECTS`, anatomy:1453-1471): pulse program (`--pulsectr < 0 ⇒ idx ←
PULSE[idx].next ; dir, ctr from entry ; init ≠ $FF ⇒ pulse ← init` ; `pulse ± PULSE[idx].Δ`);
filter (`v == FILT[0].track` only: the same over `FILT`, `cutoff += Δ`); wave step
(`a ← WAVE.A[waveidx]` with `$7E` stay / `$7F` jump ; `INS.flags & $40 ⇒ freq_hi ← a` ;
`a & $80 ⇒ freq ← FREQ[a & $7F]` ; else `freq ← FREQ[a + note + transp]` ; `wave ← WAVE.B[waveidx]`
; `--wavectr < 0 ⇒ wavectr ← wavereload ; waveidx += 1`); slide (`slideacc ± slidespd ;
¬abs ⇒ freq += slideacc`) else vibrato (`step ← (FREQ[note+1] − FREQ[note] + depthacc << 8) >>
vibshift` — closed shift; `--vibctr < 0 ⇒ vibdir ^= 1 ; vibctr ← half` ; `viboff ± step ;
freq += viboff ; depthacc += depthinc`); then the write-out `sid[v].pw_lo, pw_hi ←
pulse ; sid.cutoff_hi ← cutoff ; sid[v].freq_lo, freq_hi ← freq ; sid[v].ad ← ad ; sid[v].sr ← sr ;
sid[v].ctrl ← wave & gate` (plain V20: `sid.mode_vol ← filttype | vol`, `$1740,X` the
voice map, `$1743,X` the fine-tune added to freq — three list entries).

**reading**: `Ins{adsr, prelude: early 2, rows set(ad,$F) set(sr,0) set(ctrl mask $FE),
note row set(ctrl, 9), streams: wave (A/B columns, `$7E/$7F`), pulse and filter as
4-byte chained records = streams with `init|$FF, Δ, dir·frames, next`, accs: [slide,
vibrato]}`; growing vibrato is `Acc(freq, tablestep(FREQ, note, shift) + field(depthacc))`
with its own `Acc(depthacc, const(depthinc))`. Coverage: complete.

### 4.6 Follin — Ghouls'n'Ghosts (anatomy §3.6, prototype-follin)

**state**: zero page `$21–$97` as `voice[v]` (stride 1 bytes, 2 words;
anatomy:1643-1668): `ptr u16, dur, wave, loopcnt, loopptr u16, gated, gatelen,
gateoff, pw u16, pwreset u16, pwspd, transpose, bliplen, blipwave, vibdelay, vibcnt,
vibdepth, halfper, halfcnt, dir0, tA, tB, trilloff, trillcnt, portaspd, note,
target, callsp, freqsh u16, active, blipcnt, release`; the SMC immediates as cells
(`pulse_mode, pulse_mode0, vib_dir, trill_phase, fixed_len, skip_transpose, filt_dir0`)
per voice, `blipfreq u16` per voice; globals `cutoff u16, cutreset u16, filtspd,
owner, filt_dir, filt_min u16, filt_max u16`; call stacks `ST[v][3]`. The three
493-byte copies fold (`siblings.py`, follin:48-66). **tables**: none but the
dispatch (a switch) and `SFX` lists. **pitch**: `notetab[97]` lo/hi. **score**:
one byte stream per voice — the order program *is* the stream (`$8A call, $8B ret,
$82/$81 loop n, $87 jump, $86 stop`).

**fetch** (anatomy:1731-1747; the command loop): row clock `--dur == 0`. Body:

```
byte ≥ $80 ⇒ handler[byte]: $80 pwspd, pwreset ← byte[1..3] · $83 gated ← 1 ; gatelen ← byte[1]
   · $84 fixed_len ← byte[1] · $85 (r v)* ⇒ sid.reg[r] ← v until byte ≥ $80   # set_register, literal r
   · $88 filtspd, filt_dir0, cutoff, cutreset, min, max ← byte[1..8] · $89 owner ← v · $8C transpose ← byte[1]
   · $8D sid[v].ctrl ← byte[1] ; wave ← byte[1] ; pulse_mode0 ← byte[1] & $40 ? $FF : 1
   · $8E vibdelay, vibdepth, halfper, dir0 ← byte[1..4] · $8F bliplen, blipwave, blipfreq ← byte[1..4]
   · $90 release ← byte[1] · $91 trilloff, tA, tB ← byte[1..3] · $92 portaspd ← byte[1]
   · $8A/$8B/$82/$81/$87/$86 order program ; consume by the command's length
byte == 0 ⇒ gateoff ← gatelen ; exit
else ⇒ idx ← byte + transpose ; portaspd ⇒ target ← idx else note ← idx ; freqsh ← notetab[note]
   ; sid[v].freq ← freqsh ; trillcnt ← tA ; vibdelay ⇒ vibcnt ← vibdelay ; vib_dir ← dir0 ; halfcnt ← halfper
   ; trill_phase ← 0 ; gateoff ← gatelen ; pwreset ⇒ pw ← pwreset ; pulse_mode ← pulse_mode0
   ; owner == v ∧ cutreset ⇒ cutoff ← cutreset ; filt_dir ← filt_dir0
   ; gated ⇒ (bliplen ⇒ blipcnt ← bliplen ; sid[v].freq ← blipfreq ; sid[v].ctrl ← blipwave | 1
              else sid[v].ctrl ← wave | 1)
   ; dur ← fixed_len ≠ 0 ? fixed_len : byte[1] ; exit
```

`$85`'s register operand is a row byte (`< $80`), so `set_register(byte, byte[1])`
is a literal per row — prototype-trackerprog §3.6's rule, exact by materialisation.

**tick** (anatomy:1705-1728, 1750-1754): `active ≥ 0 ⇒ skip`; blip end; vibrato
(`vibdelay ⇒ vibcnt ∧ --vibcnt ⇒ skip ; freqsh ± vibdepth by vib_dir ; sid[v].freq ← freqsh ;
--halfcnt == 0 ∧ halfper ⇒ halfcnt ← 2·halfper ; vib_dir ^= $FF`); trill (`trillcnt ∧
--trillcnt == 0 ⇒ trill_phase ^= $FF ; phase ⇒ trillcnt ← tA ; note += trilloff else trillcnt ← tB ;
note −= trilloff ; freqsh ← notetab[note] ; sid[v].freq ← freqsh`) else portamento in
index space (`portaspd ∧ note ≠ target ⇒ note ← toward(target, portaspd) ; freqsh ← notetab[note] ;
sid[v].freq`); pulse bounce (`pulse_mode == 0 ⇒ pw −= pwspd ; pw < $0064 ⇒ pulse_mode ← $FF …`
/ `$FF ⇒ pw += pwspd ; pw ≥ $0F9B ⇒ pulse_mode ← 0` ; `pulse_mode ≠ 1 ⇒ sid[v].pw ← pw`);
gate-off (`--dur == release ∨ gateoff == 0 ⇒ sid[v].ctrl ← wave & $FE` else `gateoff −= 1`);
the filter (`filt_dir ⇒ cutoff += filtspd ; ≥ max ⇒ filt_dir ← 0` / `−= ; < min ⇒ ← $FF` ;
`sid.cutoff_lo ← cutoff_lo ; sid.cutoff_hi ← cutoff_hi << 5 | cutoff_lo >> 3`); the return
value `active[0] | active[1] | active[2]` is `meta`, not observable.

**reading**: no instrument table — `Ins` is the latched cell set at a note, so
`forms.instruments` is per distinct latched set (the anatomy's "commands latch
state", anatomy:211); vibrato `Acc(freqsh, const(depth), reflect by halfper)`;
trill a two-row `pitch` stream; porta `Acc(note, const(spd), clamp(target))`; pulse
`Acc(pw, reflect [$64,$F9B] proved)`; filter `Acc(cutoff, reflect [min,max])`;
blip a `prelude` with `early` negative (it runs *after* note-on for `bliplen`
ticks — the prelude form with a signed offset, one datum). Coverage: complete.

### 4.7 defMON — Automatas (anatomy §3.7, prototype-automatas)

**state** (struct-of-code, stride $31; anatomy:1918-1937): `voice[v].{slideacc u16,
af, ps, detune, voicebit, complement, pw_lo, pw_hi, freq_lo, freq_hi, sr, ad, wg, wgx,
timer, ptr u16 (+3 broadcast copies = the same cell), flag, flag1, flag2, flag3,
cascA.cnt, cascA.idx, cascB.cnt, cascB.idx, note, base}`, globals `res_route, mode,
cutoff_acc u16, step u16, dir (opcode cell `ADC/SBC` = 1 bit), cp, thr, scale (`NOP/ASL`),
subtick (`LDA/RTS`), flag, arrow, call_counter`. **tables**: sidTAB rows (213,
variable-length register-column records: `flags1 {WG b6, WGx b7, AD b5, SR b4, TR b3,
AF b2, PW b1}` then `flags2 {PS, RE, FV, CP, ACID(2)}`, bytes in test order), `DL[213]`,
row pointers (`hi == 0` = JP to `lo`), arranger `V0/V1/V2[168]`, pattern pointers.
**pitch**: `FREQ[156]` u16 (index 12 ≈ 1 Hz, `+36` for notes; also the slide-speed
table). **score**: arranger rows → patterns of `flag [A] [B] [note]`.

**fetch**: two clocks. The **main-tick** row (`call_counter & 7 == 0`, anatomy:1966-1981):
song advance `flag < 0 ⇒ gap ← flag & $F ; timers ← gap ; y ← arrow ; V0[y] ≥ $80 ⇒ y ← V1[y] ;
ptr[v] ← patptr[Vv[y]] ; arrow ← y + 1`, then per voice `timer < 0 ⇒ CONSUME` / `--timer < 0 ⇒
PREPARE`. `CONSUME` body over the row's 1–4 bytes: `flag ← byte ; byte & $40 ⇒ cascA.idx ←
byte[k] ; cascA.cnt ← 0 ; byte & $20 ⇒ cascB.idx ← byte[k] ; cascB.cnt ← 0 ; byte & $10 ⇒ note ← base ←
byte[k] ; slideacc ← 0 ; af ← 0 ; byte & $80 ⇒ flag ← byte ; exit (pointer not advanced) ; ptr += k ;
timer ← byte & $F`. The **cascade** (every call, six blocks, anatomy:1985-1990): `cnt == 0 ⇒
y ← idx ; hi[y] == 0 ⇒ y ← lo[y] ; cnt ← DL[y] ; idx ← y + 1 ; apply row y` — a second fetch on a
second clock over the sidTAB row's bytes: `flags1 & $40 ⇒ wg ← byte[k]` … `flags2 & $80 ⇒ ps`,
`RE`, `FV`, `CP`, `ACID: hi < $80 ⇒ cutoff_acc ← hi:lo ; step ← 0 else step ← (hi & $3F):lo ; dir ← hi & $40`.
The sidTAB row is the anatomy's most general stream row (anatomy:2039); it is a
`fetch` body like any other, on the cascade's clock.

**tick** (anatomy:1956-1963, 1993-2001): the write-out `sid[v].pw_lo, pw_hi, freq_lo,
freq_hi, sr, ad ← image ; sid[v].ctrl ← wg ^ wgx ; sid.res_route ← res_route ; sid.mode_vol ←
mode | $F`; the filter `cutoff_acc ± step by dir ; acc_hi < 0 ⇒ acc_hi ← thr ; c ← acc_hi + cp ;
c < 0 ∨ c < thr ⇒ c ← thr ; sid.cutoff_hi ← scale ? c << 1 : c`; the oscillator `af == 0 ⇒
freq ← FREQ[36 + note] + detune (lo only)` / `af < 0 ⇒ slideacc ± FREQ[af & $3F] ; freq ← FREQ[36 + note]
+ slideacc` ; `ps > 0 ⇒ pw_lo −= ps ; borrow ∧ pw_hi == 0 ⇒ pw_lo ← 1 ; ps ← −ps …` (the bounce).
The `subtick` opcode cell guards the row fetch; `scale` and `thr` are init-time
cells from the SID-model read (`meta.inputs`, pinned).

**reading**: no instrument table — `Ins` is a sidTAB row program: `streams` in the
general row form with `hold = DL + 1` calls and `jump` by JP rows; a pattern step
arms up to two. Slide `Acc(slideacc, tabcell(FREQ[af & $3F]), phase bit(af, 6))`;
pulse `Acc(pw, reflect [$0001,$0FF8] proved, const(ps) with sign in ps)`; filter
`Acc(cutoff_acc, const(step), clamp)`. `rate` is the cascade's `DL` divider.
Coverage: complete.

### 4.8 Walker — Chameleon (anatomy §3.8; **certified**, [prototype-walker-trackerprog.md](prototype-walker-trackerprog.md))

**state**: page-2 sequencer cells (`$02A7–$02FF`: block, song, blockpos, callctr,
songpos, voicecur, transpose[v], detune[v], mode[v], ctrl[v], gateen[v], route[v],
instr[v], owner, newnote[v], gate[v], reload[v], drum[v], state, speed`) and the
engine block `$AD00–$AD76` (per voice: `delay, delayctr, mod1..4 {mode, rate,
countdown, period, phase, dir[, type]}, pwoff u16, freqoff u16, pwbase u16,
freqbase u16`; global `filter mod, cutoff base/off, step constants`). Residue is
initial state (anatomy:2320). **tables**: `INS[32]×30`, `DRUM[24]×7`, key tables
`$AFE7/$AFCE` (25 each), pointer tables. **pitch**: 96 lo/hi. **score**: song =
block list; block = 16-byte header + 3 tracks of L keyboard characters.

**fetch** (anatomy:2227-2253): row clock `++callctr == speed(9)`. The order
program: `song[pos]` → block; `blockpos == 1 ⇒ header: instr[v], gateen[v], route[v] ←
hdr[3..11] ; owner ← hdr[12] ; loadfilt(owner) ; mod resets`. Body per voice over one
byte: `idx ← keytable(byte)` — the linear search over two 25-byte tables is a
`tables` lookup (the token grammar is table membership, anatomy:2863); `idx == 24 ⇒
rest: sid[v].ctrl ← ctrl − 1 ; reload ← 1` · `class == note ⇒ (reload ⇒ loadins: sid[v].ad, sr ←
INS ; sid[v].ctrl ← INS.ctrl − 1 ; sid[v].pw ← … ; 17 engine cells ← INS[$B..$1D] ; mod3 preload ;
reload, newnote ← mode − 1) ; freqbase ← FREQ[idx + transpose] ± detune ; sid[v].freq ; gate off/on`
· `class == drum ⇒ presets ; DRUM[idx] → mod3 rate/period, sid[v].freq abs, ctrl, ad, pw`.

**tick** (engine, anatomy:2255-2278, every call): per voice `¬newnote ∧ delayctr ≠ delay
⇒ delayctr += 1 ; skip` ; four modulator copies of one template `rate ≠ 0 ⇒ (¬newnote ⇒
--countdown ≠ rate ⇒ skip) ; countdown ← 100 ; mode ≠ 1 ∧ newnote ⇒ reset ; period == $FF ⇒ offset ←
input($D41B) ; offset ± step ; ++phase == period ⇒ phase ← 0 ; dir ^= 1` (mod1/mod3 → `freqoff`,
mod2 → `pwoff`, filter → `cutoff_off`; mod4 `toggle ^= 1 ; sid[v].ctrl ← ctrl − 1 + toggle`);
`sid[v].freq ← freqbase + freqoff ; sid[v].pw ← pwbase + pwoff` ; `sid.cutoff_hi ← cutbase + cutoff_off`.
`mod3reset` pre-loads `offset ← ∓step·(period − 1)` — the repeat-add loop, closed.

**reading**: `Ins{adsr, prelude: null, accs: [mod1, mod2, mod3, filter, mod4]}` with
each modulator `Acc(target, const(step), reflect by period, rate 100 − rate, phase dir)`
and mod3's one-shot as `policy halt`; `period == $FF` is the anatomy's volatile sink
(`external input`, the one stated boundary — it lands in an additive offset, so
with `$D41B` pinned the object renders). Coverage: complete but for that input.

### 4.9 Blackbird — Quintessence (anatomy §3.9; **certified**, [prototype-blackbird-trackerprog.md](prototype-blackbird-trackerprog.md))

**state** (stride 7, anatomy:2440-2469): `voice[v].{pwidth, trwpos, pendnote,
pendfx, pendins, wavemask, trtimer, fxpos, currfx, currins, basepitch, wavepos,
read u16 (zp)}`, globals `inptr u16, trwpos_scratch, pendoob, master, filtpos, tempo,
extsync`, immediates `m_cutoff, m_groove, m_transp, m_copyend`, the patched `JMP`
low byte (three values = the pipeline phase). Three cells keep the assembler's
values (anatomy:2455). **tables**: instrument columns `ins_ad/sr/wave/filt` (14,
1-based), `fx_start[33]`, `fxtable[143]`, `wavetable[72]`, `filttable`,
`pwprepare[256]`. **pitch**: the 207-byte overlapped array read as `F(k)`, and the
four quarter-tone sums as a 4N value table (§1). **score**: the LZ stream.

**fetch**: the score does not exist until decompressed (anatomy:2683), so the fetch
has two levels, both token grammars: the **unpacker** (`unpackvoice`, one LZ
token per call into `buf[v]`: control byte `t t t t t n n n`: `t == 0 ⇒ n literals
reversed` / `t > 0 ⇒ copy n + 3 from `read − offset` with `byte & $80 == 0 ⇒ + 2(t − 16)`) and
the **tokenizer** — the three `prepare` passes on `master ∈ {21, 14, 7}` and
`execute` on `master == 0`, each a body over `buf[v][read]`: `prepare1`: `byte ≥ $F9 ⇒
pendoob ← byte ; read += 1 ; byte ← next ; byte − $C8 ≥ 0 ⇒ currfx, pendfx ← byte − $C8 ; read += 1`;
`prepare2`: `byte < $80 ⇒ a ← currins` / `≥ $B8 ⇒ skip` / `else read += 1 ; a ← byte − $82 ;
pendins ← a ; a ≥ 0 ⇒ currins ← a ; a ≥ 2 ⇒ sid[v].sr ← 0 ; wavemask ← $FE`; `prepare3`:
`read += 1 ; byte ≥ $80 ⇒ trtimer ← byte | $F0 else pendnote ← byte >> 1 ; pendfx ← currfx ;
trtimer ← $FE | (byte & 1)`; `execute`: the out-of-band command, then per voice `basepitch
← pendnote << 2 ; pendfx ⇒ fxpos ← fx_start[pendfx] ; pendins by class: gate off / legato /
instrument (y ≥ 2 ⇒ sid[v].sr ← $F ; wavemask ← $FF ; filtpos ← ins_filt[y] ; wavepos ← ins_wave[y]
; y ≥ 2 ⇒ sid[v].ad ← 0 ; sid[v].ctrl ← 1 ; sid[v].ad, sr ← ins_ad[y], ins_sr[y])` ; `master ← tempo ;
tempo ^= m_groove`. The row clock is `master` counting by 7. The materialised
`score` is the decompressed token stream per voice (the buffers' contents over
the horizon) — storage idioms are dropped by materialisation (prototype-trackerprog
§6), and the unpacker is kept in `fetch` only as provenance of that materialisation.

**tick** (`everyframe`, anatomy:2543-2568): per voice `y ← fxpos ; fxpos += 1 +
(fxtable[y+1] < 0 ? fxtable[y+1] : 0) ; d ← fxtable[y] ; d == 0 ⇒ sid[v].freq ← $FFFF else p ← d +
basepitch ; sid[v].freq ← pitch4[p]` (the 4N table); `w ← wavetable[wavepos] ; w ≥ $C0 ⇒ wavepos +=
w + 1 ; w ← wavetable[wavepos] ; sid[v].ctrl ← w & wavemask ; w & $40 ⇒ b ← wavetable[+1] ; pwidth ←
b < 0 ? b << 1 : b + pwidth ; sid[v].pw_lo, pw_hi ← pwprepare[pwidth] ; wavepos += 2 else += 1`;
the filter row `sid.mode_vol, res_route ← filttable[y], [y+1] ; cutoff byte absolute or
`m_cutoff += c` with the overflow clamp ; sid.cutoff_hi ← m_cutoff ^ $80`.

**reading**: `Ins{adsr: columns, prelude: rows set(sr,0) set(ctrl mask $FE) at early 2
then set(sr,$F) set(ad,0) set(ctrl,1) on the note row, streams: pitch program (a
`pitch` stream in quarter semitones with the *next-byte* loop marker), wave program
(ctrl + pulse steps), filter program}`; `pwidth` is `Acc(pw, const(b), wrap 8)` read
through `pwprepare` as a `tabcell` producer. Coverage: complete.

### 4.10 What the nine share, measured on the object

| | fetch clock | fetch bodies (token classes) | tick entries (stores outside fetch) | loop forms used | forms coverage |
| --- | --- | --- | --- | --- | --- |
| Hubbard | `speedctr` | 1 row grammar, 6 classes | ≈ 45 | shift, repeat | 100 % |
| Galway | `CLOCK[v]` | 1 command loop, 15 + note | ≈ 60 (segments unrolled) | — | 100 % |
| GT2 | `counter[v]` | 2 (row fetch 8 classes, order 4) + tick-0 switch 15 arms | ≈ 90 | shift | 100 % |
| SID Wizard | `spdcnt[v]` vs `TEMPOTBL` | 2 (row 4 bytes, order 6 classes) + 3 dispatch switches | ≈ 120 | — | 100 % |
| JCH | `tick` | 1 command loop, 4 classes + super table | ≈ 70 | shift | 100 % |
| Follin | `dur[v]` | 1 command loop, 21 + note | ≈ 55 | — | 100 % |
| defMON | `call_counter & 7`, `DL` | 2 (pattern row, sidTAB row) | ≈ 50 | — | 100 % |
| Walker | `callctr == 9` | 1 (key class) + block header | ≈ 40 (4 modulator copies fold) | repeat | 100 % − `$D41B` |
| Blackbird | `master` by 7 | 4 passes + the unpacker | ≈ 25 | — | 100 % |

Counts are from the anatomy's pseudocode (one entry per assignment line); the lift
reports the measured ones. The forms column is the claim §5 makes and §6 tests:
every entry of every family is under one tracker form. Where it is not, the object
still renders; the coverage number drops, and that is the whole consequence.

---

## 5. The tracker reading (`forms`)

Recognition over `tick`, `fetch` and `tables`, each rule a syntactic match on the
closed object with the anatomy's two-family evidence (prototype-trackerprog §5's
tables stand; they are now *matched*, not *proposed*):

| form | matched on | rule |
| --- | --- | --- |
| stream | a `tables` row set read through one `cursor` cell whose `tick` entries step it (`+1`, `+stride`, jump by a row byte, hold by a countdown cell) | the cursor's own entries: step, jump, hold |
| generator | a value a producer reads that no table and no tuning holds: a self-contained record with private state, its own subscriptions to what the player publishes, and a value over that state alone. It reads no cell of another voice, so cross-voice dependence exists only inside one | the reads a `tick` entry makes outside its own copy |
| `Ins` | the cells a note-on entry group writes from one record's columns, plus the streams it re-points and the accs it resets | the fetch's note class |
| prelude | SID edge entries (`ctrl/ad/sr`) guarded by the row clock at a fixed offset before the note class | `early` = that offset; rows = the entries in order |
| `Acc` | an entry `c ← c ± Δ` with Δ a `const`, a `field(cell, mask)`, a `tabcell`, a `tablestep` (the closed shift) or `repeat` (the closed multiply), optionally `+ carry`; `rate` the countdown cell in its guards; `phase` the direction cell; `policy` from the guards that reload, clamp or flip the direction; `bound` proved from those guards, projected from the store's mask, or observed over the horizon | one match per entry |
| producer | any `sid[v].freq/pw/cutoff ← value` entry: `mode set` for a table entry or cell, `mode add` where the value is `pitch[..] + accs` | every 16-bit SID entry |
| command | a fetch class whose assignments are `set(cell)`, `set_stream`, `arm`, `set_tempo`, `set_vol`, `set_register` | the class's entries |

`forms` records, per family and per tune: entries matched / total, and the
list of unmatched entries by site. That list replaces §8's refusal table and is
the layer's only "residue" — a measurement, not a gate.

One correction the hand exemplar forces on that last sentence: a `residue`
section is not a place to put things. Commando's reading has none. What a
family's routine does that the tracker vocabulary has no *word* for still has an
owner — the modulator that does it, or the instrument that sounds it — and it
belongs there, in that owner's own private state, rather than in a table beside
the tuning or in a list of exceptions. The measurement is of coverage; it is not
a home for the uncovered.

---

## 6. Acceptance

Per certified tune (GT2 ×2, JCH ×3, SW ×2, Commando ×2, defMON ×2, Follin ×32):

1. **Renders.** `certify`: the universal player over the JSON round-tripped object
   alone, 0 divergences on `grid.reduce_tick` against `Verifier.obs` over the whole
   certified horizon; `rendered_from == digest(object)`.
2. **Closed.** `schema_check` empty (§1's invariants); `refuse.py` does not exist;
   no `family`, `hubbard`, `gt2`, … in `trackerprog/`.
3. **Total.** `len(tick)` = the number of store sites outside the fetch regions,
   accounted per site; `len(fetch.body)` = the region's stores. Reported.
4. **Measured.** `forms` coverage; §6.2's six numbers and `xz -9e` of the print
   against the source `tuneprog.md` (reported, not gated — the transition is the
   program's own size class; the score compresses, the tick does not, and the
   claim is exactness).
5. **Untouched.** recert 51/51; no `tuneprog` artefact moves.

**Landed for Follin** ([prototype-follin-trackerprog.md](prototype-follin-trackerprog.md)):
all 32 subtunes render write for write over their whole horizons, 111,763 ticks,
0 divergences. §4.6's reading holds as written, with three corrections the render
forced — `$8D` sets the running pulse mode as well as the note-on one, `$93` is
not unused, and the note index is a byte, so what lies past the 97-entry tuning
is bounded by the index and not by the score's own note bytes.

**Landed for Blackbird** ([prototype-blackbird-trackerprog.md](prototype-blackbird-trackerprog.md)):
*Quintessence* renders over its whole 10,426-tick horizon, 0 divergences, and
§4.9's reading holds but for its last sentence — the unpacker is not "kept in
`fetch` as provenance of that materialisation", it is gone, and the tool reads
the rows the tokenizer finished off the tune's own cells instead of
re-implementing the two-level fetch. The `pwidth` accumulator, the four
quarter-tone sums and the *next*-byte loop marker are as §4.9 wrote them.

Prose-only families (Galway, Walker) — since certified, §4.2 and §4.9 —
are covered by §4 on paper and by
the hermetic snippet tests of each construct they need (a command loop with
call/ret, a table-membership token class); they enter the
acceptance when a certificate exists (architecture §9.2).

---

## 7. Code plan

`deity_informant/trackerprog/`, every module ≤ 500 lines, no tune-family code:

| module | role | state |
| --- | --- | --- |
| `resolve`, `cursors`, `score`, `streams`, `pitch`, `hist` | T2: the score as a cursor nest, materialised rows | **kept** (#299, #303) |
| `region` | the fetch regions | kept (#305) |
| `fetch` | the fetch bodies over `byte` with the row-walk loop as the grammar (`consume`, `exit`) | extend: the loop body form for JCH/Galway/Follin/SW |
| `transition` | §3.3: every store outside the fetch as an entry; procedure summaries; the two loop closures | new, from `producers.py` (which does this for SID sites) |
| `state` | §3.1: the STATE layout and post-init values; TABLES | new, small |
| `universal` | §2: the player | rewrite, ~150 lines |
| `forms` | §5 | new |
| `document`, `certify`, `emit` | tagged JSON, digest binding, `schema_check`, the print and the six numbers | kept, `schema_check` extended to §1's invariants |
| `sound`, `player` (S4 interpreter), `producers`, `refuse` | | **deleted** |

Order: Commando end to end (§4.1 is the check), then GT2, JCH, SW, defMON, Follin
on the same code — each a certificate, none a design decision. The T1 plane
(`tuneprog/acc*.py`) becomes an input to `forms` (its `Acc` records are one of
the readings) and is no longer on the render path.
