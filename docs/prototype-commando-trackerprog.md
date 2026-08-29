# Prototype: Commando as a trackerprog — the oracle reference tune

A **hand transliteration** of the certified Commando tuneprog
([prototype-commando-floor.md](prototype-commando-floor.md) §4) into a
trackerprog ([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered
by one universal player (§4, §5) and certified against the tune's own player on
the PcodeVM. No lift, no decompiler, no proposer: every structure below is the
factored form's own text restated one layer up, by hand, and the point of the
exercise is what that costs.

Two results:

1. **It renders.** All three Commando subtunes, 11,780 ticks each, **0
   divergences** on §2's observable — and the raw ordered write lists are
   *permutations* of the oracle's on every tick, with each register's own
   sequence of values identical. The only difference is the interleave §2 drops.
2. **The schema needs ten additions**, listed in §4. Nine are small and each is
   one datum; the tenth — pitch entries that name state cells — is already
   §6's own prose and needs only a schema row. Nothing in the tune needed a new
   *mechanism*: every §5 row Hubbard is cited for held exactly as written.

Reproduce:

```
tools/trackerprog_commando.py $HVSC/MUSICIANS/H/Hubbard_Rob/Commando.sid \
    --song 0 --certify --out out/commando-tp
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 measurements.

---

## 1. The object

`tools/trackerprog_commando.py` writes `trackerprog.json`;
`deity_informant/trackerprog/universal.py` (326 lines, no tune, no family, no
table of its own) renders it. The object is §3's seven sections:

| section | Commando song 1 |
| --- | --- |
| `meta` | 3 voices in order 2,1,0; `commit_order (ctrl, ad, sr)`; `tempo` a divider, `rate = speed + 1 = 3`; `cycles_per_tick 19656` |
| `pitch` | 72 entries: 65 `u16` constants and 7 that name **two state cells each** (§4.1) |
| `streams` | three: `note_on` (the note row's five sets), `note_off` (the prelude), `arp` (a two-row pitch stream `[0, 12]`) |
| `accs` | seven declared forms, 18 arms across the 9 instruments (§2) |
| `instruments` | **9** — the subtune's reach; the file carries 13 |
| `score` | 3 order programs (64 / 63 / 123 `play` steps, `jump(0)`), 31 patterns, 570 events |
| `globals` | `mode_vol $0F`, the flag `C`'s default, the init and stop write lists |

The player has one dispatch, and it is on the *form* of a delta — `const`,
`field`, `tablestep`, `repeat`, `add`, `flag` — never on the name of an effect.
The seven accumulator ids (`vibrato`, `pulse_run`, `pulse_bounce`, `slide`,
`drum`, `skydive`, `arpeggio`) are labels in the data.

---

## 2. The mapping, line by line

Left column is [prototype-commando-floor.md](prototype-commando-floor.md) §4's
factored form — the certified program. Right column is the object.

| the tuneprog says | the trackerprog says | §5 row |
| --- | --- | --- |
| `FREQ[n]`, the u16 at `$5428 + 2n` | `pitch[n]` | §3.2 |
| `INS[i]`, 8 columns | `Ins{adsr, wave, pw, prelude, accs}` | §3.5 |
| `TRACK[v]` / `PAT[p]` | `score.orders` / `score.patterns` of events | §3.6 |
| `speedctr`, `speed` | `meta.tempo` — a divider, `rate = 3` | §3.3 |
| `row & $1F` | the event's `dur`, in row ticks | §3.6 |
| `row & $20` | the event's `tie`: it disarms the prelude | new (§4.6) |
| `row & $40` | the event's `gate: off` — a keyoff | §3.6 |
| the extra byte `< $80` / `≥ $80` | the event's `ins` / `porta` | §3.6 |
| `$518B` hard cut | the instrument's **prelude**, `early = 1` row tick, rows `set(ctrl, wave & $FE) set(ad,0) set(sr,0)` | §3.5 |
| `ins.vib ≠ 0` | `Acc(freq, repeat(tablestep(pitch, note, vib+1), phase(fold(counter,7))), policy reload(pitch[note]))` | §5 vibrato, stateless phase |
| `ins.fx & 8` | `Acc(pw_lo, const(pspeed) + carry(C), width 8, wrap, scope instrument)` | §5 pulse run |
| `ins.pspeed ≠ 0` | `Acc(pw, const(pspeed & $E0), width 12, reflect, bound [$800,$EFF] projected, rate (pspeed & $1F)+1, phase cell pwdir, scope instrument)` | §5 pulse sweep |
| `voice.porta ≠ 0` | `Acc(freq, field(porta, $7E), phase bit(porta, 0), wrap, scope voice)`, armed by the score | §5 free slide |
| `ins.fx & 1` | `Acc(freq_hi, const(-1), emit entry)` plus two ctrl rows its own guard selects | new (§4.3, §4.4) |
| `ins.fx & 4` | `Acc(freq, policy reload(pitch[note + arp[counter & 1]]))` | §5 arpeggio |
| `ins.fx & 2` | `skydive`, declared with its guard and `trap: true` | §5 (struck row, kept as data) |
| `trkptr` `$FF` / `$FE` | the order program's `jump(0)` / `stop` | §3.6 |

The three routines the print names disappear: `fetch` is the sequencer step,
`soundwork` is the accumulator list in rank order, and `voices`' `X = 2,1,0`
loop is `meta.voice_order`. The SID register offset `$54EB`, the scratch pair
`$550A/$550B`, the `PHA`/`PLA` pulse spill and `$5504`'s saved `X` are gone with
them — except for one byte, and §4.1 says which.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`, §2's comparison over the whole horizon,
the reference being the tune's own player on `deity_informant.PcodeVM` — the
same interpreter the tuneprog certificate is verified against.

| subtune | ins | patterns | events | pitch | acc arms | ticks | SID writes | divergences | identical ticks | permuted ticks |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 9 | 31 | 570 | 72 | 18 | 11,780 | 133,109 | **0** | 3,410 | 8,370 |
| 2 | 4 | 10 | 118 | 24 | 8 | 11,780 | 128,953 | **0** | 2,944 | 8,836 |
| 3 | 1 | 4 | 61 | 25 | 4 | 11,780 | 7,219 | **0** | 11,561 | 219 |

133,109 is the write count [prototype-commando-floor.md](prototype-commando-floor.md)
§2.2 measures on the trace, to the write.

**A claim stronger than §2, and free.** On every tick of every subtune the two
sides' write lists have the same *multiset*, and for every register the sequence
of values it is written is identical (`same_per_register_order`). The whole
difference is where `ctrl` sits relative to `pw` on a note-on tick, because §4's
`commit` emits producers (step 4) before the edge registers (step 5) and Hubbard
emits `ctrl, pw_lo, pw_hi, ad, sr`. That is exactly the idiom §2 cites as its
reason for dropping cross-class order — measured here, not assumed.

Subtune 3 is not in the certified set and earns its place anyway: its order
program ends in `stop`, which subtunes 1 and 2 never reach inside the horizon.

---

## 4. What the spec needed

Ten additions. Each is one datum in the object, none is a player branch, and
none is a new mechanism.

### 4.1 A `pitch` entry may name two state cells

§3.2 says `pitch: [u16; N]`. Commando's is not: `$5448`'s 202 bytes are the
frequency table **fused with the six per-voice arrays**, and the tune reads past
the table twenty-five notes' worth (commando-floor §5, "const is refuted by the
tune"). Seven entries of song 1's `pitch` are pairs of live cells:

| entry | reached by | the two cells |
| --- | --- | --- |
| 97 | arpeggio, note 85 + 12 | `$0E`, **`voice_base`** |
| 98 | arpeggio, note 86 + 12 | `orderpos[0]`, `orderpos[1]` |
| 100 | arpeggio, note 88 + 12 | `patrow[1]`, `patrow[2]` |
| 104 | the drum note itself | `wave[0]`, `wave[1]` |
| 105 | vibrato `note+1`, arpeggio 93 + 12 | `wave[2]`, `note[0]` |
| 107 | arpeggio, note 95 + 12 | `ins[0]`, `ins[1]` |
| 116 | arpeggio, note 104 + 12 | `pwdir[0]`, `pwdir[1]` |

§6 already states the reading — "that starting frequency lifts as an absolute
`set` producer over `field(cell)` — named, not materialised as pitch" — and this
is that, with a place to put it: **an entry is `u16` or `cells(lo, hi)`**.

The result worth having: six of the seven name state §4's player *already
holds* — the order cursor, the pattern cursor, the wave shadow, the note index,
the instrument index, the pulse phase. Only `$54EB` does not: it is the SID
register offset `7·v` the voice loop parks there, so **one machine idiom
survives the lift**, as the high byte of pitch entry 97. It is named
`voice_base` and it is the object's only such cell.

A consequence: `patrow` is a **byte** cursor, so an event must carry how many
bytes the fetch consumed (`Event.bytes`, the transition prototype's
`fetch.consume`). Without the overrun that number is storage idiom and
materialisation drops it; with it, it is observable.

### 4.2 An instrument has a note row, whether or not it has a prelude

§3.5's table says Hubbard's prelude is `null`, and then there is nowhere for the
five sets a note-on emits (`ctrl = wave & gate`, `pw_lo`, `pw_hi`, `ad`, `sr`).
Expressed here as `meta.note_row`, a §3.3 stream of `set` steps whose values
read the instrument's own columns and its **live** pw cell. Proposal: every
`Ins` has a note row; `prelude: null` means no *early* rows, not no note row.

### 4.3 `Acc.emit ∈ {entry, exit}`

The drum writes `freq_hi` and *then* decrements it, so the producer sees the
value the tick came in with. #297's "epoch of a cell the tick moved" rule names
both epochs and leaves the choice to inference; the object must state it.

### 4.4 An `Acc` may carry the gate rows its own guard selects

The drum's first row tick sets `ctrl = $80` (TEST, gate off) and every later
tick `ctrl = wave & $FE`, chosen by the same guard that decides whether the
accumulator steps. §5 has a `target: gate-mask` row (Walker, prose-only) but no
place for two `set` rows under one Acc. Written here as
`gate: {true: [...], false: [...]}`; the cleaner statement is a two-row §3.3
stream with a `select` on the Acc's guard.

### 4.5 `meta.row_consumes_tick`

On the tick a voice takes a new row, its accumulators do not run. §4's tick runs
the sequencer step and then the streams and accs unconditionally. One bit.

### 4.6 `Event.tie`

§3.5's prelude belongs to the instrument; whether it fires belongs to the
**row** — bit 5 of the row byte. One bit per event. (§3.6's nearest existing
spelling is `cmds: [gate(mask)]`.)

### 4.7 `Acc.when` and `Acc.delta_when`

§5's record has `bound`, `policy`, `rate`, `phase` and no guards, while §5's
*rules* table is about nothing else ("the store's transitively closed control
dependences", #296). The drum needs three guards and the vibrato's delta one
(`dur >= 6`); the accumulator needs a `when` list of comparisons over named
cells — the same `when` the transition prototype gives every entry of its
tick (`w11-producers`, unmerged).

### 4.8 The flag's producer, not only its consumer

§5 gives the consumer `Δ + carry(site, flag)`. The producer needs stating too:
the vibrato's `repeat` loop leaves `C`, the value it leaves when the loop does
not run (`seed: 1`, the compare's own carry), the value when its guard fails
(`unguarded: 0`), and the default when no vibrato is armed at all
(`globals.flags.C.default = bit(ins, 5)`, the residue of the instrument index's
three shifts — always 0 for this tune, and stated rather than assumed).

The term is load-bearing: delete `+ carry` and subtune 2 diverges on 11,747 of
11,780 ticks. It is the write that makes the subtunes aperiodic
(architecture §5.2), and it is exercised on 2,106 ticks.

### 4.9 `stop` is four writes and an abandoned tick

§3.6 has the terminator; the writes it makes are data —
`$D404 = $D40B = $D412 = 0`, `$D418 = $0F`, on the tick *after* the one the
terminator was read on, which is abandoned mid-voice-loop. Subtune 3 exercises
it.

### 4.10 `trap`: the arm the horizon never takes

§5 struck the skydive row for lack of an observation. The object keeps it as
data — the guard, and `trap: true` — and the player raises if it is ever taken,
which is the print's `trap 'untaken'` carried one layer up instead of dropped.

---

## 5. What the spec got right

Every §5 row Hubbard is cited for held **exactly as written**, with no widening:

- **stateless-phase vibrato** — `repeat(tablestep(FREQ, note, ins.vib + 1), n)`,
  `phase fn(global_counter)`, the single-family exception. The fold
  (`counter & 7`, `^ 7` above 3) is the triangle; the shift loop's count is the
  instrument byte; the `dur >= 6` guard is the delta's, not the producer's.
- **free slide is `wrap`, not `reflect`** (§5's correction 5): `porta` is set by
  the score and never turns.
- **pulse sweep** — `reflect`, `bound [$800,$EFF]` **projected** from the store's
  `& $F` (correction 4), `rate` the divider, `phase` the per-voice cell, `scope`
  **instrument** while its phase and divider are per voice (§5's `scope` rule,
  read off the cell).
- **pulse run** — `const(k) + carry(site)`, `bound` projected at 12 bits.
- **arpeggio** — an absolute `set` producer over a pitch stream, `phase
  fn(counter)`.
- **`commit_order (ctrl, ad, sr)`** — §3.1's row for Hubbard, unchanged.
- **§2's observable is the right strength.** Not one comparison was lost to the
  reduction, and §6 measures exactly what it drops.

Two §5 delta forms are not exercised by this tune: `tabcell` and the
sign-extended table entry (§10's open question). Neither is missing.

---

## 6. Measurements

`xz -9e`, the extra number §9's acceptance #3 asks for:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.json`, song 1, compact | 51,933 | **3,252** |
| — its `score` half | 45,151 | 1,448 |
| — everything else | 6,773 | 1,932 |
| `tuneprog.md`, the source print | 21,679 | 4,644 |
| the whole load band | 4,039 | 2,548 |
| the tune's data floor (commando-floor §2.2) | 1,941 | 1,112 |
| `trackerprog.json`, song 2 / song 3 | 14,066 / 9,312 | 1,980 / 1,788 |

The layer's claim holds: the score compresses better than the program that
played it (3,252 against 4,644), and the score half alone lands within 30 % of
the tune's own compressed data. The JSON's raw size is key repetition and
nothing else.

Code, all new, no existing module touched:

| file | lines | role |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 326 | §4 + §5, one procedure over the object |
| `deity_informant/trackerprog/attest.py` | 81 | §2's comparison |
| `tools/trackerprog_commando.py` | 475 | the transliteration and the PcodeVM reference |
| `tests/trackerprog/test_commando_oracle.py` | 90 | the three certificates and five claims |
| `tests/trackerprog/test_universal.py` | 289 | hermetic snippets, one per section 5 mechanism |

Against the floor: the player pseudocode
[playroutine-anatomy.md](playroutine-anatomy.md) §3.1.3 is 65 lines and covers
all three songs and every fx bit. The universal player is 326 lines and covers
*no* song — it has no Commando in it. That is the trade the layer is for.
