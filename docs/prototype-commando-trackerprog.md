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
2. **The schema needs eleven additions**, listed in §4. Nine are one datum
   each. The other two are the layer's own discipline made explicit: the pitch
   table is **bounded** — no index arithmetic, no stride, no read past its end —
   and where a tune's value is not a table read it is a **generator**, a
   self-contained little machine with private state fed by the player's
   published events. Nothing in the tune needed a new *mechanism*: every §5 row
   Hubbard is cited for held exactly as written.

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
`deity_informant/trackerprog/universal.py` (391 lines, no tune, no family, no
table of its own) renders it. The object is §3's seven sections:

| section | Commando song 1 |
| --- | --- |
| `meta` | 3 voices in order 2,1,0; `commit_order (ctrl, ad, sr)`; `tempo` a divider, `rate = speed + 1 = 3`; `cycles_per_tick 19656` |
| `pitch` | a bounded table of **70 rows** with three columns — `freq`, `step` (the interval above a note), `octave` (the arpeggio's second row) — so no producer does arithmetic on an index (§4.1) |
| `generators` | **8**, each with private state, its event subscriptions and a value; subtunes 2 and 3 need **none** (§4.2) |
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
| `FREQ[n]`, the u16 at `$5428 + 2n` | `pitch.freq[i]`, `i` a row of the bounded note space | §3.2, §4.1 |
| `FREQ[n+1] - FREQ[n]` (the vibrato's interval) | `pitch.step[i]` — a column, not an offset | §4.1 |
| `FREQ[n+12]` (the arpeggio's octave) | `pitch.octave[i]` — a row of the space, or a generator | §4.1, §4.2 |
| the table read that leaves the table | a **generator**: private state, published events, a value | new (§4.2) |
| `INS[i]`, 8 columns | `Ins{adsr, wave, pw, prelude, accs}` | §3.5 |
| `TRACK[v]` / `PAT[p]` | `score.orders` / `score.patterns` of events | §3.6 |
| `speedctr`, `speed` | `meta.tempo` — a divider, `rate = 3` | §3.3 |
| `row & $1F` | the event's `dur`, in row ticks | §3.6 |
| `row & $20` | the event's `tie`: it disarms the prelude | new (§4.7) |
| `row & $40` | the event's `gate: off` — a keyoff | §3.6 |
| the extra byte `< $80` / `≥ $80` | the event's `ins` / `porta` | §3.6 |
| `$518B` hard cut | the instrument's **prelude**, `early = 1` row tick, rows `set(ctrl, wave & $FE) set(ad,0) set(sr,0)` | §3.5 |
| `ins.vib ≠ 0` | `Acc(freq, repeat(tablestep(pitch, note, vib+1), phase(fold(counter,7))), policy reload(pitch[note]))` | §5 vibrato, stateless phase |
| `ins.fx & 8` | `Acc(pw_lo, const(pspeed) + carry(C), width 8, wrap, scope instrument)` | §5 pulse run |
| `ins.pspeed ≠ 0` | `Acc(pw, const(pspeed & $E0), width 12, reflect, bound [$800,$EFF] projected, rate (pspeed & $1F)+1, phase cell pwdir, scope instrument)` | §5 pulse sweep |
| `voice.porta ≠ 0` | `Acc(freq, field(porta, $7E), phase bit(porta, 0), wrap, scope voice)`, armed by the score | §5 free slide |
| `ins.fx & 1` | `Acc(freq_hi, const(-1), emit entry)` plus two ctrl rows its own guard selects | new (§4.4, §4.5) |
| `ins.fx & 4` | `Acc(freq, policy reload(pitch[note + arp[counter & 1]]))` | §5 arpeggio |
| `ins.fx & 2` | `skydive`, declared with its guard and `trap: true` | §5 (struck row, kept as data) |
| `trkptr` `$FF` / `$FE` | the order program's `jump(0)` / `stop` | §3.6 |

The three routines the print names disappear: `fetch` is the sequencer step,
`soundwork` is the accumulator list in rank order, and `voices`' `X = 2,1,0`
loop is `meta.voice_order`. The SID register offset `$54EB`, the scratch pair
`$550A/$550B`, the `PHA`/`PLA` pulse spill and `$5504`'s saved `X` are gone with
them entirely: not one byte of the object names a memory location.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`, §2's comparison over the whole horizon,
the reference being the tune's own player on `deity_informant.PcodeVM` — the
same interpreter the tuneprog certificate is verified against.

| subtune | ins | patterns | events | notes | gens | acc arms | ticks | SID writes | divergences | identical ticks | permuted ticks |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 9 | 31 | 570 | 70 | 8 | 18 | 11,780 | 133,109 | **0** | 3,410 | 8,370 |
| 2 | 4 | 10 | 118 | 30 | 0 | 8 | 11,780 | 128,953 | **0** | 2,944 | 8,836 |
| 3 | 1 | 4 | 61 | 22 | 0 | 4 | 11,780 | 7,219 | **0** | 11,561 | 219 |

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

Eleven additions. Nine are one datum each; §4.1 and §4.2 are the layer's own
discipline made explicit. None is a player branch and none is a new mechanism.

### 4.1 The pitch table is bounded, and carries columns instead of arithmetic

§3.2 says `pitch: [u16; N]` and "every *note* elsewhere is an index into this
table or a signed index offset". The offset is the problem. Commando's table is
the frequency table **fused with the six per-voice arrays** — `$5448`'s 202
bytes tune notes 16..95 and then become state — and the tune's own arithmetic
walks off the end twenty-five notes' worth (commando-floor §5, "const is
refuted by the tune"). Two producers do the walking: the vibrato's
`FREQ[n+1] - FREQ[n]` and the arpeggio's `FREQ[n+12]`.

A trackerprog must not have an index that leaves its table. So the offsets
become **columns**, materialised per note, and the table is a bounded space:

```jsonc
"pitch": {
  "notes":  [16, 18, 19, 21, …, 104],   // the space: the notes the score plays,
  "index":  {"16": 0, "18": 1, …},      //   plus the in-range octave targets
  "freq":   [ u16 | {"gen": …} ],       // the tuning
  "step":   [ u16 | {"sub": […]} ],     // the interval above this note
  "octave": [ {"at": j} | {"gen": …} ]  // the arpeggio's second row
}
```

`{"at": j}` is another row of the same space, so the octave of a note is *that
note* and no addition happens anywhere. `tablestep(n, k)` reads `step[n] >> k`
instead of differencing two entries. A note byte the score plays that is not a
row of the space is an error, and so is reading `step` or `octave` of a row that
is only an octave target. The space is closed by construction and 70 rows wide.

Cost, measured: bounding the table moved song 1's `xz -9e` from 3,252 to 3,804
— 552 bytes for the two columns. It buys a table that cannot be walked off,
which is the whole point, and it deletes `Event.bytes`-style index plumbing from
every producer.

### 4.2 `generators`: a self-contained source, with private state

Seven of the values the escaping indices carried are not tuning at all. §6 says
so in prose — "a read landing on a play-written cell is not a pitch entry at
all" — and this is that, given a home. A generator is declared once and named by
a column:

```jsonc
"wave0_wave1": {
  "state": {"lo": 0, "hi": 0},
  "on": [{"event": "sound", "voice": 0, "set": {"lo": {"payload": "wave"}}},
         {"event": "sound", "voice": 1, "set": {"hi": {"payload": "wave"}}}],
  "value": {"u16": [{"own": "lo"}, {"own": "hi"}]}
}
```

It reads **nothing** of the player's: no cell of another voice, no table, no
address, no index past the end of anything. What it needs it *mirrors*, by
subscribing to the events the player publishes, and two generators mirroring the
same fact keep two copies. That is deliberately cheaper than a shared state
namespace, because a private copy cannot alias.

The player publishes seven events, each a musical fact and none a memory
location:

| event | when | payload |
| --- | --- | --- |
| `note` | the row latched a note | `note` |
| `instrument` | the row carried an instrument | `ins` |
| `sound` | any fetch, once the instrument's registers are emitted | `wave` |
| `row` | any fetch, once the row is consumed | `bytes` |
| `wrap` | the pattern restarted | — |
| `order` | the order position moved | `pos` |
| `turn` | an accumulator's phase turned | `acc`, `phase` |

Song 1's eight generators and what each mirrors:

| generator | reached as | mirrors |
| --- | --- | --- |
| `sidofs0_sidofs1` | `step` of note 95 | nothing — `u16(sid_base 0, sid_base 1)` |
| `sidofs2_voice_base` | `octave` of note 85 | nothing — `u16(sid_base 2, sid_base of the reader)` |
| `orderpos0_orderpos1` | `octave` of note 86 | two `order` positions |
| `patrow1_patrow2` | `octave` of note 88 | two byte cursors, by `row` and `wrap` |
| `wave0_wave1` | `freq` of note 104 | two `sound` waves |
| `wave2_note0` | `step` of note 104, `octave` of 93 | one `sound` wave, one `note` |
| `ins0_ins1` | `octave` of note 95 | two `instrument` ids |
| `pwdir0_pwdir1` | `octave` of note 104 | two pulse-sweep phases, by `turn` |

`sid_base` is the chip's own register layout — the offset the player computes
for every write it emits — so even the SID stride is not a constant in the data.

**The invariant this buys, and the player enforces it:** no expression reads
another voice's state. `{"cell": name}` is the voice being committed and nothing
else; there is no `{"cell": [name, voice]}` form. Cross-voice dependence exists
only as a generator, declared, initialised and fed by published events. A test
walks the whole object and asserts it.

Two-thirds of the machinery is free where it is not needed: **subtunes 2 and 3
carry no generators at all**, and neither would GT2, JCH, SID Wizard, defMON or
Follin.


### 4.3 An instrument has a note row, whether or not it has a prelude

§3.5's table says Hubbard's prelude is `null`, and then there is nowhere for the
five sets a note-on emits (`ctrl = wave & gate`, `pw_lo`, `pw_hi`, `ad`, `sr`).
Expressed here as `meta.note_row`, a §3.3 stream of `set` steps whose values
read the instrument's own columns and its **live** pw cell. Proposal: every
`Ins` has a note row; `prelude: null` means no *early* rows, not no note row.

### 4.4 `Acc.emit ∈ {entry, exit}`

The drum writes `freq_hi` and *then* decrements it, so the producer sees the
value the tick came in with. #297's "epoch of a cell the tick moved" rule names
both epochs and leaves the choice to inference; the object must state it.

### 4.5 An `Acc` may carry the gate rows its own guard selects

The drum's first row tick sets `ctrl = $80` (TEST, gate off) and every later
tick `ctrl = wave & $FE`, chosen by the same guard that decides whether the
accumulator steps. §5 has a `target: gate-mask` row (Walker, prose-only) but no
place for two `set` rows under one Acc. Written here as
`gate: {true: [...], false: [...]}`; the cleaner statement is a two-row §3.3
stream with a `select` on the Acc's guard.

### 4.6 `meta.row_consumes_tick`

On the tick a voice takes a new row, its accumulators do not run. §4's tick runs
the sequencer step and then the streams and accs unconditionally. One bit.

### 4.7 `Event.tie`

§3.5's prelude belongs to the instrument; whether it fires belongs to the
**row** — bit 5 of the row byte. One bit per event. (§3.6's nearest existing
spelling is `cmds: [gate(mask)]`.)

### 4.8 `Acc.when` and `Acc.delta_when`

§5's record has `bound`, `policy`, `rate`, `phase` and no guards, while §5's
*rules* table is about nothing else ("the store's transitively closed control
dependences", #296). The drum needs three guards and the vibrato's delta one
(`dur >= 6`); the accumulator needs a `when` list of comparisons over named
cells — the same `when` the transition prototype gives every entry of its
tick (`w11-producers`, unmerged).

### 4.9 The flag's producer, not only its consumer

§5 gives the consumer `Δ + carry(site, flag)`. The producer needs stating too:
the vibrato's `repeat` loop leaves `C`, the value it leaves when the loop does
not run (`seed: 1`, the compare's own carry), the value when its guard fails
(`unguarded: 0`), and the default when no vibrato is armed at all
(`globals.flags.C.default = bit(ins, 5)`, the residue of the instrument index's
three shifts — always 0 for this tune, and stated rather than assumed).

The term is load-bearing: delete `+ carry` and subtune 2 diverges on 11,747 of
11,780 ticks. It is the write that makes the subtunes aperiodic
(architecture §5.2), and it is exercised on 2,106 ticks.

### 4.10 `stop` is four writes and an abandoned tick

§3.6 has the terminator; the writes it makes are data —
`$D404 = $D40B = $D412 = 0`, `$D418 = $0F`, on the tick *after* the one the
terminator was read on, which is abandoned mid-voice-loop. Subtune 3 exercises
it.

### 4.11 `trap`: the arm the horizon never takes

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
| `trackerprog.json`, song 1, compact | 54,060 | **3,804** |
| — its `score` half | 45,151 | 1,448 |
| — everything else (tuning, generators, accs, instruments) | 8,909 | 2,496 |
| — the eight generators alone | 1,592 | — |
| `tuneprog.md`, the source print | 21,679 | 4,644 |
| the whole load band | 4,039 | 2,548 |
| the tune's data floor (commando-floor §2.2) | 1,941 | 1,112 |
| `trackerprog.json`, song 2 / song 3 | 14,497 / 9,504 | 2,180 / 1,912 |

The layer's claim holds: the score compresses better than the program that
played it (3,804 against 4,644), and the score half alone lands within 30 % of
the tune's own compressed data. Bounding the pitch table cost 552 compressed
bytes against the first draft's seven cell-valued entries — the price of a table
that cannot be walked off, paid once. The JSON's raw size is key repetition and
nothing else.

Code, all new, no existing module touched:

| file | lines | role |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 391 | §4 + §5, one procedure over the object; publishes the seven events |
| `deity_informant/trackerprog/attest.py` | 81 | §2's comparison |
| `tools/trackerprog_commando.py` | 547 | the transliteration and the PcodeVM reference |
| `tests/trackerprog/test_commando_oracle.py` | 134 | the three certificates and six claims |
| `tests/trackerprog/test_universal.py` | 331 | hermetic snippets, one per section 5 mechanism |

Against the floor: the player pseudocode
[playroutine-anatomy.md](playroutine-anatomy.md) §3.1.3 is 65 lines and covers
all three songs and every fx bit. The universal player is 391 lines and covers
*no* song — it has no Commando in it. That is the trade the layer is for.
