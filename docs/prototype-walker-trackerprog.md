# Prototype: Chameleon as a trackerprog — the eighth family, and the modulator that is four

A **hand transliteration** of Martin Walker's Chameleon player (anatomy
[§3.8](playroutine-anatomy.md)) into a trackerprog
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders [Commando](prototype-commando-trackerprog.md),
[GoatTracker 2](prototype-goattracker-trackerprog.md),
[SID Wizard](prototype-sidwizard-trackerprog.md),
[defMON](prototype-defmon-trackerprog.md), [JCH](prototype-jch-trackerprog.md),
[Follin](prototype-follin-trackerprog.md) and
[Blackbird](prototype-blackbird-trackerprog.md), and certified against the
tune's own player on the PcodeVM.

The whole song renders: 8,052 calls, 894 rows of nine, 80.3 s, **0
divergences** on §2's observable, the write lists agreeing per register value for
value and in order, `end.kind = loop` at call 8,051 with a period of 72
([walker-chameleon.json](certificates/walker-chameleon.json)). The layer gained
`amplitude.count` (§4.1). The four modulators — pitch, pulse, a second pitch and
the filter — are one machine unrolled by *modulator* and indexed by voice, the
opposite of every earlier family: four `Acc`s differing in five operands, the
cell they move, the register it reaches, their `delta` (the four bytes of RAM at
`$AD73`) and the two cells holding their phase and direction.

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the layer
needed · 5 the modulators as §5 rows · 6 finding the data · 7 measurements
· 8 boundaries.

---

## 1. The object

| block | what it holds |
| --- | --- |
| `meta` | the row clock (`cnt`, `step +1`, the row at `phase == 8`, reloaded with 0), `voice_order [0,1,2]`, `commit_order [ad, sr, ctrl]`, `tick [row, machine]`, and the twelve-step row program |
| `pitch` | 56 u16 rows, the semitone span the horizon asks of the tune's 96-entry PAL table |
| `instruments` | four melodic records and seven drums; a drum is an instrument whose pitch is absolute and whose one modulator is a one-shot |
| `accs` | `mod1`, `mod2`, `mod3`, `filter` — one triangle, four sets of operands |
| `streams` | twelve row-program streams, six engine streams (`clocks`, three resets, `mod4`, `writeout`), the two global filter streams, and `noise`, the eight pinned input bytes |
| `score` | 39 patterns over 11 blocks, 33 order steps per voice, four commands plus one filter load per block |
| `state0` | 43 voice cells and 16 globals: page 2 as `init` left it, and the engine block as the file loaded it |
| `globals` | `after [filterclock, filtermod]`, eight flags, one commit (`$D416 = cutbase + offset`) |

The four `delta` constants — `$0A`, `$10`, `$50`, `$02` — are read from
`$AD73`–`$AD76`, RAM the player never writes. They are data of the tune, not of
the family, and the object says so by reading them.

## 2. The mapping, line by line

| the tuneprog says | the trackerprog says |
| --- | --- |
| `$02AF` counted against `$02FF` | `meta.tempo`: a counter, `step +1`, the row at `phase == 8` |
| `p_A000` over `$AFE7` then `$AFCE` | the row's own note or drum: the tables are storage (§4.3) |
| `p_A485` per block, `p_A4EE` per step | `score.orders` — one play step per block — and `score.patterns`, its three tracks of L rows |
| `row_apply4` (`$A379`) | the first row's `arm`: `filter.N`, `reblock`, `refilter` |
| `row_apply2` (`$A230`) | `filter.N`: three register writes and the global modulator's four cells |
| `row_apply` (`$A109`) | `Ins.on_note` — five registers and seventeen engine cells, one act |
| `row_apply3` (`$A2AD`) | a drum record *is* an instrument: an absolute pitch, an envelope, and mod3's rate and period |
| `p_A0A4` (`$A0A4`) | `gate_lead` then `gate_edge`: `ctrl - 1`, then `ctrl - 1 + (gate & 1)`, one act each |
| `p_A02E`/`p_A073` | `retune`: `tuned(note + transpose)`, the detune by voice number, into `freqbase` and the chip |
| `p_A5C2`'s delay gate | the `run` flag, set once in `clocks` and read by every later rank |
| `p_A60C`/`p_A692`/`p_A718` | `accs.mod1`/`mod2`/`mod3` |
| `p_A7B1` (`$A7B1`) | `accs.filter`, stepped by `globals.after` once the voices have run |
| `p_A83F` (`$A83F`) | the `mod4` stream: a gate tremolo, which moves `ctrl` and not a volume |
| `p_A88B`/`A8AB`/`A8C6`/`A8D6` | the three reset streams and `reblock`: what a note-on and a block do to a modulator |
| `$AD00`–`$AD76` uncleared | `state0` |
| `LDA $D41B` at `$A74C` | `streams.noise`, eight rows, and the three sites the horizon never reaches are `trap`s |

## 3. The certificate

```
ticks 8052   divergence null   writes 91,901
identical_ticks 7180   permuted_ticks 872   same_per_register_order true
end {tick 8051, kind loop}   loop {tick 8051, period 72}
```

The 872 permuted ticks are the two-pass tick, Blackbird's shape: the sequencer
runs over all three voices and *then* the engine does, so the writes are permuted
**between** voices and identical inside every one, which §2 drops. The source is
the tune's own certificate: 8,052 calls, first repeat at 8,051, period 72,
`complete`, 0 divergences, 0 envelope traps, 16 pinned inputs.

## 4. What the layer needed

### 4.1 A triangle turns on a bound, or on a count

§5's `reflect` reads the turn off the accumulator's own value, exact wherever
the cell a modulator moves is that modulator's alone. `mod1` (pitch triangle,
step `$0A`) and `mod3` (pitch bend, step `$50`) both add into `$AD5F`/`$AD62`,
one 16-bit frequency offset per voice, and the write-out sends `freqbase +
freqoff` every call: `mod1` moves that cell on 5,150 (tick, voice) pairs and
`mod3` on 5,939, and on **1,140** of the 9,949 either moved, both did — so no
bound on the sum is either's amplitude. The turn is a counter (`++phase; if
phase == period: phase = 0; dir ^= 1`), which `amplitude: {count, cell}` states
in §5's own cell vocabulary, the same record serving the global copy in §4.2.

**A one-shot is `delta_when`, not a policy.** `mod3` and the filter stop where
`phase + 1` reaches their period; a triangle never does. The cell compared
against — `m3halt`, 0 for a triangle and the period for a one-shot — is loaded
beside the period, so a drum overriding both keeps them consistent.

### 4.2 The filter is the fourth copy, on the one global channel

The same machine over `$AD65`, one 8-bit offset, same
mode/rate/countdown/period/phase/direction/type fields, running once per call
after the three voices because the cutoff it feeds is one register:
`globals.after` carries it, `stream_step` steps the `Acc` its row names in `run`,
and the clock, the reset and its own `$D41B` refusal are an `all` stream ahead of
it. Its fire is *any* voice's note-on and its reset is the **owner** voice's —
whose instrument programmed the three filter registers — one global cell the
block header sets and the gate reads.

### 4.3 The score, the block and the residue

**A token grammar that is table membership is storage.** A track byte is decoded
by walking `$AFE7` for 25 note keys then `$AFCE` for 25 shifted ones, up to 50
compares a byte; the grammar is membership and nothing else, so a row carries the
semitone (`key + instrument transpose`, into the tuning) or the drum record.
`$B0` enters the second search as `$30`, an aliasing the object never sees.

**A block header re-arms every voice, so a block is a pattern.** `row_apply4`
sets `$02ED`–`$02EF` — reload — on all three voices before any reads a token, so
a note's re-trigger or tie is decided by that flag and the block's own rows:
every play of a block produces the same rows, one pattern per block per voice,
2,592 played rows stated as 1,134. The exception is the song's end, on the last
row of the last step: that step names a copy of the pattern whose last row
carries `songend`, and a `stop_gate` step at the end of the row program reads it
— `p_A43C`'s gate-off over all three voices, in the same call.

**The residue is the initial state.** `init` (`$A518`) writes 25 zeroes to the
chip and 89 to page 2 and leaves `$AD00`–`$AD76` alone: `$AD00 = 1` is the enable
flag, `$AD36 = $FF` the period that takes the `$D41B` arm on voice 3, `$AD45 =
$63` a gate tremolo on voice 3, and `$AD6C`/`$AD6F` and `$AD5F`/`$AD62` a base
and an offset whose sum the first call writes to the chip. Zeroing the block
changes 41 ticks, the last at tick **115** and not tick 7: a voice whose
modulation delay is 60 calls has not reached its first note-on by then.

**The delay gate is one flag the whole tick reads.** `voicemod` holds the four
modulators and the frequency and pulse write-out off for `delay` calls after a
note-on, counting instead: one predicate read by six later ranks, `clocks` (rank
0) writing `!run` and every rank after it reading `{"flag": "run"}`. The same
stream carries each modulator's countdown, since a note-on skips the count and
fires — the phase-lock putting every modulator's first step on the note.

## 5. The modulators as §5 rows

The gate tremolo (`mod4`) is a stream and not an `Acc`: its value is one bit and
what it writes is `ctrl - 1 + toggle`, an edge write and not a producer. The
three that *are* `Acc`s target freq, pw and cutoff. `target vol, scope voice`
does not exist here: `$D418` is written once per block by the filter load, from
the owner instrument's own nibble. A modulator's `rate` is **not** the player's
divider — a note-on fires it whatever the countdown holds — so the countdown is a
cell the row program reloads and the reading is `clocks` plus `when`. `mod3reset`
pre-loads `offset ← ∓step·(period − 1)`: a loop whose count is a cell and whose
body is one addition is the amplitude it reaches, and that closed value is an
instrument column.

## 6. Finding the data

Everything the object reads is at an address the certified program reads it
from, and nothing is searched for:

| datum | where |
| --- | --- |
| song list | `$AE64`/`$AE69`, indexed by `$02AB` |
| block pointers, header | `$AE6E`/`$AE86`; `hdr[1]` is L, `hdr[3..5]` the instruments, `hdr[6..8]` the gate enables, `hdr[9..11]` the routing, `hdr[12]` the owner |
| tracks | `hdr + 15 + (v * L) + pos`, position counting from 1 — byte 0 of each track is never read, which is why the header is 16 bytes and not 15 |
| instruments | `$AECE`/`$AEEE`, 30 bytes |
| drums | `$AE9E`/`$AEB6`, 7 bytes |
| tuning | `$AF0E`/`$AF6E`, 96 entries |
| key tables | `$AFE7`, `$AFCE`, 25 each |
| the four deltas | `$AD73`–`$AD76` |
| the initial engine state | `$AD00`–`$AD76` of the post-`init` image |

The one derived column is the one-shot's amplitude, `±step · (period − 1)`,
computed where the player's loop computes it and carried beside the period so a
drum record that overrides the period overrides it too.

## 7. Measurements

| | |
| --- | --- |
| horizon | 8,052 calls, 894 rows, 80.32 s; 0 divergences, `same_per_register_order` true |
| ticks identical / permuted | 7,180 / 872 — the permutation is between voices and never inside one |
| writes | 91,901 |
| score | 11 blocks, 32 steps, 39 patterns, 1,134 rows for 2,592 played |
| records / cells | 4 instruments, 7 drums; 43 cells per voice, 16 global |
| print | 72,296 B, 4,336 `xz -9e`; the object against the tune's own load band is §9.1 |
| shared frequency offset | `mod1` steps it on 5,150 (tick, voice) pairs, `mod3` on 5,939, **both on 1,140** of the 9,949 either touched |
| engine residue, poisoned to zero | 41 of 8,052 ticks differ, the last at tick 115 |
| pinned input, poisoned to zero | 8 ticks differ, all in the first 8 |
| `amplitude.count` | `--poison amplitude-count`, **7,790 of 8,052**; the arm is unreachable for an object that does not write it |

## 8. Boundaries

- **`$D41B` is an input and the object says so.** The tune reads it 8 times, all
  on voice 3 in the first eight calls, driven by the residue; the value lands in
  an additive offset and reaches no guard, so a pinned stream renders it exactly.
  The other three sites (`$A640`, `$A6C6`, `$A7ED`) are `trap` rows.
- **Two arms the horizon never takes.** A modulator whose `mode` is 1 is
  free-running and not reset by a note-on — `mod3`'s is the one place the
  object's guards and the player's differ, and no `mod3` has mode 1 here — and
  the gate-enable arm (`hdr[6..8] == 0`) is never taken, all 32 headers enabling
  all three voices, so there is no gate-enable column.
- **The sfx API, the game's IRQ installer, music state 2 and songs 0/2/3/4 are
  not in the object**: not reached by this song, and not in the certified program.
- **The cost model is the certificate's.** A tick call whose modulator reset runs
  a 254-iteration pre-load loop costs 19,081 cycles, 97 % of a frame at two calls
  a frame. The object states the value the loop reaches and not the loop, so it
  carries the *sequence* of writes and not their cycle positions, which §2 drops.
