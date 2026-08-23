# Prototype: the complexity floor of one simple tune

Commando (Rob Hubbard, 1985), song 1 — the simplest complete driver in the
anatomy ([§3.1](playroutine-anatomy.md): 3 voices, 12 bytes of state each, no
wave/pulse/filter programs, no dispatch tables, one SMC cell) and the smallest
full-song certificate the corpus carries (**341** S4 statements against
GoatTracker's 516 and SID Wizard's 955) — prints as a **414-line**
`tuneprog.md`. This is a measurement of the complexity floor and a
hand-derivation of how close the print gets to it.

Measured at the certified horizon (`docs/certificates/commando-song1.json`,
11,780 ticks, 0 divergences, reproduced byte-identical). No pipeline module,
certificate or printed artefact was modified.

## 1. What the 341 statements become

| stage | count |
|---|---|
| executed instruction sites | 379 |
| S4 statements (what the certificate counts) | 341 |
| S4 blocks / procedures / regions | 115 / 3 / 58 |
| S6 view statements (after texture removal) | 178 |
| printed nodes (177 statements + 77 control) | 254 |
| `tuneprog.md` lines | 414 = 56 header + 252 program + 66 pc comments + 40 fences/blanks/headings |
| printed program lines, blank/fence/pc-comment removed | 252 (2,282 tokens) |

## 2. The floor, measured

### 2.1 The tune's own bytes

`tools/tuneprog_floor.py out/c1` (`bytes`), from the trace's lifted extents and
the region table:

| segment | bytes | % |
|---|---|---|
| load band $5000-$5FC7 | 4040 | 100.0 |
| executed player code | 936 | 23.2 |
| data the trace reached | 1941 | 48.0 |
| neither (other songs, sfx, dead code) | 1163 | 28.8 |

Static split: 1,347 bytes of code in three blocks, 2,693 of data (anatomy
§3.1.1). Song 1 is 936 bytes of code and 1,941 of data — two bytes of data per
byte of program. The reach was measured here as the region *extents*; it is now
`datablock.reach_bytes`, the union of the S4 accessors' envelopes over each
region's cells, which the print's data section carries. The two differ by
`$5526`: a region the S3 relation made and no S4 accessor reads.

### 2.2 Description lengths

`tuneprog_floor.py` (`mdl`), `xz -9e`:

| artefact | raw bytes | xz -9e |
|---|---|---|
| the whole load band | 4040 | 2548 |
| executed player code only | 936 | 828 |
| data the trace reached | 1941 | 1112 |
| `tuneprog.md` (the print) | 13461 | 2956 |
| `tuneprog.py` (the executable) | 48610 | 6236 |
| SID write log, 133,109 `(reg,val)` pairs | 266218 | 36484 |
| SID register image per tick, by register | 294500 | 6664 |

- Behaviour compresses to 36 KB, the tune to 2.5 KB: the program is a 14x
  compression of its own output, and the best generic compression of the
  observable (transposed register image, 6,664 B) is 2.6x the whole band. A
  decompilation is judged against 2,548, not 36,484.
- The print is already at the tune's own compressed size (2,956 vs 2,548). It is
  verbose only in that a human reads it linearly and 48 % of the tune never
  appears in it as data. *Closed by the `## data` section*: the print now carries
  1,867 of the 1,941 reached bytes and its `xz -9e` is 4,508.
- The data floor is hard: 1,112 compressed bytes of pattern, track, instrument
  and frequency data are the tune. The frequency table is irreducible — 25 of
  its 84 octave pairs are off by one from exact doubling, so no formula
  reproduces the 192 bytes.

### 2.3 The floor estimate

    floor  =  |data, printed as data|  +  |a generic Hubbard player|

The right-hand term already exists: anatomy §3.1.3 is the whole player (`play`,
`fetch_note`, `soundwork`), hand-written from the disassembly in 65 lines / 621
tokens, covering all three songs and every fx bit.

| | lines | tokens |
|---|---|---|
| floor: anatomy §3.1.3 player pseudocode | 65 | 621 |
| this prototype's factored form (§4) | 115 (126 with the untaken arms) | 1,156 |
| the current print's program | 252 | 2,282 |

The measured gap is 252 → 65, i.e. 3.9x, of which the factoring in §4 closes
2.2x by hand. The state header is a second gap: 56 lines (5 meta, 39 state, 9
const, 2 inputs) against 14 lines of table shape in the factored form — neither
printed a byte of the 1,941 as data, which the `## data` section now does.

## 3. Where the statements live

`tuneprog_floor.py --code LO-HI:NAME ...`, one row per anatomy §3.1.1 routine,
one column per statement kind. `control` is a structured node (`if`, `for`,
`while`, `return`, `trap`) or a call; `16-bit half/carry` is a statement carrying
a `carry`/borrow term or reading the high half of a `(b, b+1)` pair a sibling
statement reads the low half of — what a 16-bit view deletes; `index plumbing`
is a statement that touches no storage at all.

| code range | sid write | 16-bit half/carry | index plumbing | data | control | all |
|---|---|---|---|---|---|---|
| play $5012 | 0 | 0 | 3 | 7 | 1 | 11 |
| NoteWork $5052 | 6 | 8 | 6 | 29 | 1 | 50 |
| SoundWork $5174 | 15 | 9 | 16 | 50 | 3 | 93 |
| loop tail $538F | 0 | 0 | 2 | 1 | 0 | 3 |
| API $5000 | 2 | 0 | 0 | 1 | 0 | 3 |
| init $5F0C | 4 | 0 | 5 | 6 | 1 | 16 |
| other | 0 | 0 | 0 | 1 | 0 | 1 |
| (control nodes) | 0 | 0 | 0 | 0 | 77 | 77 |
| **total** | 27 | 17 | 32 | 95 | 83 | 254 |

- `SoundWork` is 93 of 254: the per-frame modulations (vibrato, pulse in two
  forms, portamento, drum, skydive, arpeggio) are more than a third of the
  program. Anatomy §3.1.4: "all 'sound design' is those bits".
- 49 statements — one in five — are machine encoding, none musical: 17 halves
  and carries, and 32 touching no storage at all (the voice loop's counter and
  its phi copies, the borrowed `X` saved and restored around the instrument
  index, the carry flag threaded as a value through five copies, two `saved2`
  spills).
- 27 SID writes against 133,109 executed writes: the write-out is already
  compact.
- 95 statements are data statements, and most of what they name is not named as
  data: 56 of the 252 printed lines mention `FREQ[`, and 23 of those 56 are
  `FREQ[195]` — not a frequency but the SID register offset `$54EB`, swallowed
  by the same region.

## 4. The factored form

Full text: `out/commando-factored.md` (not committed — its data half is the
tune). Six typings, each licensed by a fact the pipeline already computed.

| # | rewrite | the fact that licenses it |
|---|---|---|
| T1 | region `$5448` (202 B, `state`) splits at `$54E8` into `FREQ` and six 3-byte per-voice arrays | 26 of its **51** accessors have extent exactly 3 with index set `[0,1,2]`, at bases `$54E8 $54EC $54EF $54F8 $54FB $54FE`; 10 are the scalar `$54EB`; 5 index `$5510` alone; the last 10 are five `(b, b+1)` pairs at one index |
| T2 | `FREQ[n]` is one indexed u16 read | those five pairs read bases `$5428+i` and `$5429+i` with the *same* index expression inside one block (§5) |
| T3 | `sid[v].field` for `sid.reg[k + FREQ[195]]` | `$54EB` has one writer, `$5062`, whose value is `SIDOFS[v]`; the io regions have stride 7 |
| T4 | `INS[n].field` for `rec2[(FREQ[$D6+x] << 3)/8].bXXXX` | the eight `$5591..$5598` regions have stride 8; columns 0-4 are named by the SID register each is stored to, 5-7 by their only use |
| T5 | `PAT[p][i]`, `TRACK[v][i]` | `T5889`/`T588A`/`T588B` are three extents of **one** pattern block, reached through a ZP pointer built at `$50AA` from `T5712`/`T573F` at one index |
| T6 | note-byte and fx-bit names | the IR's own masks (`& $1F $20 $40 $80`, `& 1 2 4 8`) and what each arm does; the *words* are anatomy §3.1.4 and are not IR facts |

The result, verbatim:

```
tick():                                          # $5012, 11,780 calls
    counter += 1                                              # $5525
    if mstatus & $40:                                         # BIT $5519 -> V
        counter = 0
        for v in 2, 1, 0:
            voice[v].pos = voice[v].pat = voice[v].len = voice[v].note = 0
        mstatus = 0
    voices()
    allowed = $FF                                             # $539C
    return

voices():                                        # $5052, 11,780 calls
    speedctr -= 1
    if speedctr < 0:
        speedctr = speed                                      # SPEED[song], init-only
    for v in 2, 1, 0:
        regofs = SIDOFS[v]                                    # $5062
        if speedctr != speed:                                 # not a tick boundary
            soundwork(v)
        else:
            voice[v].len -= 1
            if voice[v].len < 0:
                fetch(v)
            else:
                if not (voice[v].row & $20) and voice[v].len == 0:
                    sid[v].ctrl = voice[v].ctrl & $FE         # $518B, hard cut
                    sid[v].ad = sid[v].sr = 0
                soundwork(v)
        allowed = $FF                                         # $539C
    return

fetch(v):                                        # $5086
    pat = PAT[TRACK[v][voice[v].pos]]                         # $5086, $50AA
    voice[v].porta = 0
    i, gate = voice[v].pat, $FF
    row = pat[i]                                              # $50C2
    voice[v].row = row
    voice[v].len = row & $1F
    if row & $40:
        gate = $FE                                            # $5118, a soft note-off
    else:
        voice[v].pat += 1
        if row & $80:                                         # $50DC
            x = pat[i + 1]
            if x < $80: voice[v].ins = x                      # $50E7
            else:       voice[v].porta = x                    # $50E1
            voice[v].pat += 1
            i += 1
        voice[v].note = pat[i + 1]                            # $50ED
        f = FREQ[voice[v].note]                               # $50FA/$5100, T2
        sid[v].freq_hi = f.hi                                 # $5103 -- two writes:
        sid[v].freq_lo = f.lo                                 # $510A    the chip is 8-bit
        voice[v].freq = f                                     # $5107/$510D ($551A|$551D)
    ins = INS[voice[v].ins]                                   # $511B, T4
    sid[v].ctrl  = ins.ctrl & gate                            # $5133
    sid[v].pw_lo = ins.pw_lo                                  # $513C
    sid[v].pw_hi = ins.pw_hi                                  # $5142
    sid[v].ad    = ins.ad                                     # $5148
    sid[v].sr    = ins.sr                                     # $514E
    voice[v].ctrl = ins.ctrl                                  # $5157
    voice[v].pat += 1
    if pat[voice[v].pat] == $FF:                              # $5163, eager peek
        voice[v].pat, voice[v].pos = 0, voice[v].pos + 1
    return

soundwork(v):                                    # $519B, 32,091 calls
    ins = INS[voice[v].ins]                                   # $51A3, T4
    if ins.vib != 0:                                          # vibrato
        phase = counter & 7                                   # $51C1: 0 1 2 3 3 2 1 0
        if phase >= 4: phase ^= 7
        step = (FREQ[voice[v].note + 1] - FREQ[voice[v].note]) >> (ins.vib + 1)
        f = FREQ[voice[v].note]                               # $51ED, T2
        if (voice[v].row & $1F) >= 6:
            for _ in 0..phase-1: f += step                    # $5208
        sid[v].freq_lo, sid[v].freq_hi = f.lo, f.hi           # $5221/$5227
    if ins.fx & 8:                                            # $5237, 8-bit pw run
        ins.pw_lo += ins.pspeed + C                           # C inherited from $51FA
        sid[v].pw_lo = ins.pw_lo
    elif ins.pspeed != 0:                                     # $5251, pulse sweep
        voice[v].pwdelay -= 1
        if voice[v].pwdelay < 0:
            voice[v].pwdelay = ins.pspeed & $1F
            d = ins.pspeed & $E0
            if voice[v].pwdir == 0:
                ins.pw += d;  if ins.pw_hi == $E: voice[v].pwdir += 1
            else:
                ins.pw -= d;  if ins.pw_hi == $8: voice[v].pwdir -= 1
            sid[v].pw_hi, sid[v].pw_lo = ins.pw_hi, ins.pw_lo
    if voice[v].porta != 0:                                   # $52BB
        d = voice[v].porta & $7E
        voice[v].freq += -d if voice[v].porta & 1 else d      # $52C7/$52E2, 16-bit
        sid[v].freq_lo, sid[v].freq_hi = voice[v].freq.lo, voice[v].freq.hi
    if ins.fx & 1 and voice[v].freq.hi != 0 and voice[v].len != 0:   # $530B, drum
        if (voice[v].row & $1F) - 1 < voice[v].len:           # first tick of the note
            sid[v].freq_hi = voice[v].freq.hi
            sid[v].ctrl = $80
        else:
            sid[v].freq_hi = voice[v].freq.hi
            voice[v].freq.hi -= 1
            sid[v].ctrl = voice[v].ctrl & $FE
    if ins.fx & 2 and (voice[v].row & $1F) >= 3: trap 'untaken'      # skydive, dead
    if ins.fx & 4:                                            # $535E, octave arpeggio
        f = FREQ[voice[v].note + ($C if counter & 1 else 0)]  # $5378, T2
        sid[v].freq_hi, sid[v].freq_lo = f.hi, f.lo
    return

init(a):                                         # $5FB2, 1 call
    if a >= 3: trap 'untaken'
    stop_sfx()                                                # $5FB6
    allowed, speed = $FF, SPEED[a]                            # $5F0C
    for k in 0..5: trkptr[k] = SONG[a * 6 + k]                # $5F20
    sid[0].ctrl = sid[1].ctrl = sid[2].ctrl = 0               # $5F2C
    sid.mode_vol = $F
    mstatus = $40
    return

stop_sfx():                                      # $500C, 1 call
    sid[0].ctrl = sid[1].ctrl = 0
    sfx = $FF
    return
```

alongside 14 lines of data shape (`FREQ` 96 x u16, `SIDOFS` 3, `SPEED` 3, `SONG`
3 x 6, `INS` 13 x 8, `PATPTR` 45 x u16 split lo|hi, `TRACK` 3 lists of 64/63/123,
`PAT` 31 of 45 patterns, 1,290 bytes) and the data itself dumped once. Song 1's
pattern block decodes to 570 rows, 560 with a pitch, 10 `hold`, 21 `tie`, 129
with an extra byte of which 15 are portamento — a note stream, not a program.

| | lines | tokens |
|---|---|---|
| current print, program section | 252 | 2,282 |
| factored (as printed above) | 115 | 1,156 |
| factored + the 11 elided `trap 'untaken'` arms | 126 | — |
| floor (anatomy §3.1.3) | 65 | 621 |

## 5. Verified, refuted, derived

Verified mechanically (`tuneprog_floor.py`, `pairs`; rule: two accesses of one
region in one block, at the same index expression, with adjacent constant
bases):

| block | region | the two bases | reaches | written cells in reach | const row |
|---|---|---|---|---|---|
| $50FA | `state_5448` | `$5428+i, $5429+i` | $5448-$54F9 | $54EB (15) | no |
| $51CC | `state_5448` | `$5428+i, $5429+i, $542A+i, $542B+i` | $5496-$54FB | $54EB (17) | no |
| $51ED | `state_5448` | `$5428+i, $5429+i` | $5496-$54F9 | $54EB (15) | no |
| $5378 | `state_5448` | `$5428+i, $5429+i` | $5472-$5511 | $54EB (36) | no |
| $506E, $5086 | `state_005D` | `$005D+i, $005E+i` | $005D-$005E | 2 | no |
| $50AA, $50DC, $50ED | `state_005F` | `$005F+i, $0060+i` | $005F-$0060 | 2 | no |

- **Pairing holds.** Every frequency read is a `(T[i], T[i+1])` pair at one index
  inside one block: five pairs in four blocks, `$51CC` (the vibrato semitone
  difference) carrying two of them, for adjacent rows `n` and `n+1`. So
  `freq = FREQ[note]` is a sound 16-bit view.
- **Const is refuted by the tune.** All four blocks reach past `$54E8` into cells
  the play routine writes. Song 1 plays pitch 104 twenty-five times (patterns 8,
  10 and 31, drum instruments 4 `fx=$03` and 7 `fx=$05`); `$5428 + 2*104 =
  $54F8` is `voice[0].ctrl`, `$54F9` is `voice[1].ctrl`, and the arpeggio's `+12`
  reaches `$5510`/`$5511`, the two `pwdir` bytes. The drum's starting frequency
  is the two control bytes currently in the voice array. The overrun anatomy
  §3.1.4 calls benign is load-bearing, 25 notes' worth, and is what fuses the
  192-byte const table with the 36-byte per-voice record into one 202-byte
  `state` region — why `posoffset[v]` prints as `FREQ[v + $C4]` and the SID
  offset as `FREQ[195]`.
- **A second fusion, in the other direction**: one pattern block prints as three
  regions (`T5889` 1290 B, `T588A` 1285 B, `T588B` 1287 B) because three
  accessors reached three extents of it.
- **`voice[v].freq` (`$551D` lo, `$551A` hi) refuses to fold.** Instrumented:
  `word._pairs` accepts the two addresses (it requires neither adjacency nor
  lo-below-hi, and Hubbard put hi first); `word._crosses` returns False at all
  three portamento sites, so the SID write of the low byte between the two
  stores does not block it; `word._operand` does build the `R16($551D, $551A)`.
  The refusal is inside `_match`'s reading of `hi = X + carry(lo)`; which clause
  of `_parses`/`_same` declines it is open. Cost: 7 printed lines (the two
  portamento arms are 13 lines where the folded pair would be 6).

Derived, not verified: every name (`pos`, `pat`, `len`, `row`, `ctrl`, `note`,
`ins`, `pwdelay`, `pwdir`, `porta`, `mstatus`, `allowed`, `counter`) is anatomy
§3.1.1's word for a shape the IR gives only a role for; `ins.pw` as a 12-bit
accumulator is the anatomy's reading of `(pw_hi + carry) & $F`; the fx-bit
meanings are the anatomy's. The factored text elides 11 of the print's 13
`trap 'untaken'` arms (7 of them the `allowed` guard, which song 1 never fails
because no sound effect ever starts) — counted back in above.

## 6. Conclusions

`present()` with and without `--eqsat` on this exact certified program:

| | lines | code lines | tokens | S6 statements | CPU |
|---|---|---|---|---|---|
| default | 377 | 252 | 2,282 | 178 | 0.6 s |
| `--eqsat` | 377 | 252 | 2,274 | 178 | 2.8 s |

Eight tokens at 4.7x the CPU, the whole difference being two deleted `0 +` in a
borrow. An expression rewriter rewrites expressions: it cannot dump `PAT` as
1,290 bytes of note records, split a region, decide that `FREQ[195]` is a
register offset and `FREQ[t << 1]` a pitch, or rename a derivation. The
bottleneck is storage typing, upstream of every expression the e-graph sees.

Of the 254 printed nodes, 49 are machine encoding (17 halves and carries, 32
touching no storage) and those are exactly what T1-T4 remove; the rest of
252 → 115 lines is the printer's temporaries becoming inline once the storage
has a shape. Two typings do almost all of it: T1 (split the fused region)
touches 56 of the 252 printed lines, and the 16-bit view — T2 plus the
`voice[v].freq` pair the pipeline already attempts — removes all 17 half/carry
statements.

The one generic mechanism, a rule about region extents:

> A region's extent is the union of its accessors' observed spans, but the
> accessors are not all the same shape. Partition them: an accessor whose span is
> `k` bytes at index set `0..k-1` names a `k`-element array; an accessor that
> reads `(b, b+1)` at one index names a row of a `u16` table; a constant-address
> accessor names a scalar. Where those partitions disagree about a byte, the
> region **splits at the boundary and the overrunning accessor keeps a bound
> assertion**, instead of the whole region collapsing to the coarsest kind. A
> region every access reaches only through `(b, b+1)` pairs at one index is a
> `u16` table; a byte no store ever writes is `const` even when a neighbour is
> `state`.

On Commando that rule turns one 202-byte `state` region into `FREQ` (u16 const
table, with the pitch-104 read carrying an explicit out-of-range assertion), six
3-byte per-voice arrays, one scalar register offset, and one `pwdir` cell —
T1, T2 and T3 together, i.e. most of the 252 → 115. Its mirror handles the
pattern block: three extents of one array whose accessors agree on shape are one
region, not three.

The floor for Commando song 1 is 65 lines of player pseudocode plus 1,941 bytes
(1,112 compressed) of data printed as data; the data half is irreducible. The
print was at 252 lines and printed none of the data as data; the `## data`
section now carries 1,867 of the 1,941 in 51 rows, the 74 left being cells a
store's envelope reaches -- the voice array this section's pitch-104 overrun
fuses with the table. The factored form
reaches 115–126 lines and names all of it, closing 2.2x of the 3.9x by storage
typing alone. Two thirds of the tune's live bytes are tables (1,941 of 2,877
reached).

The residual 115 → 65 is part floor, part presentation. The two-writes-per
16-bit register change is a hard fact about the *emitted executable*: the
certificate compares the executable's ordered byte writes per tick
(`verify._compare`), so both halves must be written separately and in order. It
is not a fact about the *print*: a u16 view can print one statement per 16-bit
register change under a stated write-order convention — this tune writes
hi-then-lo in `fetch` and the arpeggio, lo-then-hi in vibrato — so about 9 of
the factored form's 21 SID-write lines (against the anatomy's 12) are
presentation, not floor. The rest of the residual is the branch arms the trace
took that a human summary elides, and the position bookkeeping a human writes as
`pat++`.

## 7. Reproducing

```
python3 tools/tuneprog_certify.py $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid \
    --out out/c1 --song 1 --calls 11780 --resume --budget 50
python3 tools/tuneprog_floor.py out/c1 \
    --code '5012-5051:play $5012'      --code '5052-5173:NoteWork $5052' \
    --code '5174-538E:SoundWork $5174' --code '538F-53A4:loop tail $538F' \
    --code '5000-5011:API $5000'       --code '5F0C-5FC6:init $5F0C'
```

`--calls 11780` is the horizon the certificate records; Commando has no state
repeat inside its HVSC length (`period.py` classifies the obstruction). The
factored document is hand-derived from `out/c1/tuneprog.md` and the tables above.
