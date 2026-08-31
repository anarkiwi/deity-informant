# Prototype: Chameleon as a trackerprog — the eighth family, and the modulator that is four

A **hand transliteration** of Martin Walker's Chameleon player (anatomy
[§3.8](playroutine-anatomy.md)) into a trackerprog
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)),
GoatTracker 2 ([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)),
SID Wizard ([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md)),
defMON ([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md)),
JCH ([prototype-jch-trackerprog.md](prototype-jch-trackerprog.md)),
Follin ([prototype-follin-trackerprog.md](prototype-follin-trackerprog.md)) and
Blackbird ([prototype-blackbird-trackerprog.md](prototype-blackbird-trackerprog.md)),
and certified against the tune's own player on the PcodeVM.

Six results:

1. **The whole song renders.** 8,052 calls — 894 rows of nine calls, the HVSC
   length of 80.3 seconds — **0 divergences** on §2's observable, and per
   register the two write lists agree value for value and in order
   (`same_per_register_order`). `end.kind = loop`: the tune's state repeats at
   call 8,051 with a period of 72, and the certificate is bound to the tune's
   own ([walker-chameleon.json](certificates/walker-chameleon.json)), which is
   new — the tuneprog front end certified this tune at 15 seconds and had never
   been run to length (architecture §9.1). It needed no change to do so.
2. **The layer gained one form, and the family forced it.** §5's `reflect`
   turns a triangle where its *value* reaches a bound. Two of Walker's four
   modulators — the pitch triangle and the pitch bend — sum into **one**
   frequency offset, and on 1,140 of the horizon's 9,949 modulator steps both
   have moved it, so the value there is neither modulator's and no bound on it
   can be either's turn. `amplitude.count` is the turn a modulator counts for
   itself: its own steps against its own period. Fifteen builds of seven
   families re-certify unchanged.
3. **The four modulators are one record.** Pitch, pulse, a second pitch and the
   filter are four copies of one machine, unrolled by *modulator* and indexed by
   voice — the opposite of every earlier family. The object states them as four
   `Acc`s that differ in five operands and nothing else: the cell they move, the
   register it reaches, their `delta` (the four bytes of RAM at `$AD73`), and
   the two cells that hold their phase and direction. The fourth is on the
   global channel and steps in `globals.after`, once the voices have.
4. **The score is a keyboard, and none of that survives.** The tracks are the
   C64 keys Walker typed the music on, decoded by two linear searches over
   25-byte tables. §6's rule says a token grammar that is table membership is
   storage: what the object carries is a semitone or a drum per row, and no key
   table at all.
5. **A block header re-arms every voice, so a block is a pattern.** Eleven
   blocks carry the song's thirty-two steps: 2,592 played rows stated as
   **1,134**, because the header forces `reload` on all three voices and a
   block's rows therefore cannot depend on the step that plays it. The print is
   4,336 bytes of `xz -9e` against the source `tuneprog.md`'s **11,176**.
6. **The residue is the initial state, and it is load-bearing for 41 ticks.**
   `init` clears the 25 chip registers and 89 bytes of page 2 and stops; the
   119-byte engine block is whatever the file loaded there. Zeroing it changes
   the render on 41 of the 8,052 ticks, the last at tick 115 — well past the
   eight the anatomy names.

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the layer
needed · 5 the prose-only rows, corrected · 6 finding the data · 7 measurements
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
`$AD73`–`$AD76`, which is RAM the player never writes. They are data of the
tune, not of the family, and the object says so by reading them.

## 2. The mapping, line by line

| the tuneprog says | the trackerprog says |
| --- | --- |
| `$02AF` counted against `$02FF` | `meta.tempo`: a counter, `step +1`, the row at `phase == 8` |
| `p_A000` over `$AFE7` then `$AFCE` | the row's own note or drum: the tables are storage (§6) |
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

The 872 permuted ticks are the two-pass tick, the same shape Blackbird has: the
sequencer runs over all three voices and *then* the engine does, so the two
sides' writes are permuted **between** voices and identical inside every one.
§2 drops voice order and `attest` has compared per voice since #322.

The source certificate is the tune's own
([walker-chameleon.json](certificates/walker-chameleon.json)): 8,052 calls,
first repeat at 8,051, period 72, `complete`, 0 divergences and 0 envelope
traps, 16 pinned inputs. It reproduces through `tools/tuneprog_recert.py` like
every other.

## 4. What the layer needed

### 4.1 A triangle turns on a bound, or on a count

§5's `reflect` reads the triangle's turn off the accumulator's own value:

```python
turn = (out >> am["shift"]) == ((hi if not ph else lo) >> am["shift"])
```

That is exact for every earlier family, because in each of them the cell the
modulator moves is the modulator's own. Walker's `mod1` (the pitch triangle,
step `$0A`) and `mod3` (the pitch bend, step `$50`) both add into
`$AD5F`/`$AD62`, one 16-bit frequency offset per voice, and the write-out sends
`freqbase + freqoff` every call. Over the horizon `mod1` moves that cell on
5,150 (tick, voice) pairs and `mod3` on 5,939, and on **1,140** of the 9,949
pairs where either moved it, both did. A bound on the sum is neither
modulator's amplitude, and there is no bound that is.

The turn is a counter in the player, and always was: `++phase; if phase ==
period: phase = 0; dir ^= 1`. So `amplitude` gains one alternative to
`interval`/`shift`:

```python
def turned(self, am, out, ph, ov):
    if "count" not in am:
        lo, hi = (self.ev(x, ov) for x in am["interval"])
        return (out >> am["shift"]) == ((hi if not ph else lo) >> am["shift"])
    n = (self.whole(am["cell"]) + 1) & 0xFF
    turn = n == self.ev(am["count"], ov)
    self.put(am["cell"], 0 if turn else n)
    return turn
```

`count` is the period and `cell` is where the count lives, in §5's own
vocabulary — so the same record works for a voice cell and for a global one,
which is what §4.2 needs. The direction flip moves from `self.c[...]` to the
same `whole`/`put` pair for the same reason; for a voice cell the two are the
same statement. No earlier family writes `count`, so the arm is unreachable for
all of them by construction, and the strike is a check rather than the argument
([§7](#7-measurements)).

**A one-shot is `delta_when`, not a policy.** `mod3` and the filter stop where
`phase + 1` reaches their period; a triangle never does. That is one guard over
cells the object already has, and the cell it compares against — `m3halt`, 0
for a triangle and the period for a one-shot — is loaded beside the period, so
a drum that overrides both keeps them consistent. The prose-only row in §7 of
prototype-trackerprog.md projected `policy halt`; the guard is more general and
costs the player nothing.

### 4.2 The filter is the fourth copy, on the one global channel

The filter modulator is the same machine over `$AD65`, one 8-bit offset, with
the same mode/rate/countdown/period/phase/direction/type fields — and it runs
once per call after the three voices, because the cutoff it feeds is one
register. `globals.after` is where §4.4 puts a channel the voices feed, and
`stream_step` already steps an `Acc` named in a row's `run`, so the filter is:

```json
"filtermod": {"rows": [{"trap": "..."}, {"run": [{"acc": "filter"}], "next": 1}]}
```

with its clock, its reset and its own `$D41B` refusal in an `all` stream ahead
of it. The `Acc` names its cells with a `#` and §5's `whole`/`put` resolve them;
nothing else in the record differs from `mod3`'s. Its fire is *any* voice's
note-on and its reset is the **owner** voice's — the voice whose instrument
programmed the three filter registers — which is one global cell the block
header sets and the gate reads.

### 4.3 A token grammar that is table membership is storage

A track byte is decoded by walking `$AFE7` for 25 note keys and then `$AFCE`
for 25 shifted ones, up to 50 compares a byte. The anatomy calls the cost
irrelevant at three lookups per four and a half frames; §6 says the same thing
about the *object*: the grammar is membership and nothing else, so the tables
are an encoding of the score and not part of it. What the rows carry is the
semitone (`key + instrument transpose`, into the tuning) or the drum record.
One byte, `$B0`, enters the second search as `$30` — an aliasing the decode
does and the object never sees.

### 4.4 A block header re-arms every voice, so a block is a pattern

`row_apply4` sets `$02ED`–`$02EF` — reload — on all three voices before any of
them reads a token. Whether a note re-triggers or ties is decided by that flag
and by the block's own rows and by nothing else, so **every play of a block
produces the same rows**. The score is therefore one pattern per block per
voice, and the order list plays eleven blocks over thirty-two steps: 2,592
played rows stated as 1,134.

The one exception is the song's own end, which falls on the last row of the
last step and not on every play of that block. The order's last step names a
copy of the pattern whose last row carries a `songend` command, and a
`stop_gate` step at the end of the row program reads it — which is
`p_A43C`'s gate-off over all three voices, in the same call as the row that
ended the song.

### 4.5 The residue is the initial state

`init` ($A518) writes 25 zeroes to the chip and 89 to page 2. The engine block
`$AD00`–`$AD76` is untouched, so the modulators start from whatever the file
loaded there: `$AD00 = 1` is the enable flag, `$AD36 = $FF` is the period that
takes the `$D41B` arm on voice 3, `$AD45 = $63` is a gate tremolo running on
voice 3, and `$AD6C`/`$AD6F` and `$AD5F`/`$AD62` are a base and an offset whose
sum the first call writes to the chip. §5's `state0` is exactly the place for
this, and it is where the four `delta` constants live too.

Zeroing the engine block changes 41 of the 8,052 ticks and the last of them is
tick **115**, not tick 7: a voice whose modulation delay is 60 calls has not
reached its first note-on by then, and its residual base is still what the
write-out sends.

### 4.6 The delay gate is one flag the whole tick reads

`voicemod` holds the four modulators *and* the frequency and pulse write-out
off for `delay` calls after a note-on, incrementing a counter instead. That is
one predicate evaluated once and read by six later ranks, which is what §7's
flag channel is: `clocks` (rank 0) writes `!run` and every rank after it reads
`{"flag": "run"}`. The same stream carries each modulator's countdown, because
a note-on skips the count and fires — the phase-lock that puts every
modulator's first step on the note.

## 5. The prose-only rows, corrected

Walker was prose-only in prototype-trackerprog.md §7, so its two rows were
projections. Both land, with corrections:

| row, as written | as the certified reading has it |
| --- | --- |
| "tremolo, LFOs: target **gate-mask**, `policy reflect` (triangle) or `halt` (one-shot), or a stream" | the gate tremolo (`mod4`) is a stream and not an `Acc`: its value is one bit and what it writes is `ctrl - 1 + toggle`, an edge write and not a producer. The three that *are* `Acc`s target freq, pw and cutoff, and `halt` is `delta_when` and not a policy |
| "`target vol, scope voice` does not exist" | stands: `$D418` is written once per block by the filter load, from the owner instrument's own nibble, and no voice writes it |
| §4.8 of the transition: "each modulator `Acc(target, const(step), reflect by period, rate 100 - rate, phase dir)`" | the `rate` is **not** the player's divider: a note-on fires the modulator whatever the countdown holds, so the countdown is a cell the row program can reload and the reading is `clocks` plus `when` |
| §4.8: "`mod3reset` pre-loads `offset ← ∓step·(period − 1)` — the repeat-add loop, closed" | stands, and the closed value is an instrument column: a loop whose count is a cell and whose body is one addition is the amplitude it reaches |
| §4.8: "`period == $FF` is the anatomy's volatile sink … with `$D41B` pinned the object renders" | stands: eight rows, worth exactly 8 ticks, and the other three sites are `trap`s |

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
| records | 4 instruments, 7 drums |
| cells | 43 per voice, 16 global |
| print | 72,296 B, 4,336 `xz -9e`, against the source `tuneprog.md`'s 147,424 B / 11,176 |
| shared frequency offset | `mod1` steps it on 5,150 (tick, voice) pairs, `mod3` on 5,939, **both on 1,140** of the 9,949 either touched |
| engine residue, poisoned to zero | 41 of 8,052 ticks differ, the last at tick 115 |
| pinned input, poisoned to zero | 8 ticks differ, all in the first 8 |
| the strike | the fifteen earlier builds re-certify with 0 divergences: the `count` arm is unreachable for an object that does not write it |

## 8. Boundaries

- **`$D41B` is an input and the object says so.** The tune reads it 8 times, all
  on voice 3 in the first eight calls, driven by the residue. The value reaches
  no guard — it lands in an additive offset — so a pinned stream renders the
  tune exactly. The other three sites (`$A640`, `$A6C6`, `$A7ED`) are `trap`
  rows: an object that reached one would stop the render rather than guess.
- **Three arms the horizon never takes, and each refuses by name.** A modulator
  whose `mode` is 1 is free-running and is not reset by a note-on; `mod3`'s is
  the one place the object's guards and the player's differ, and over this
  horizon no `mod3` has mode 1. The gate-enable arm (`hdr[6..8] == 0`, a voice
  a block mutes) is never taken either: all 32 headers enable all three voices,
  so the object carries no gate-enable column.
- **The sfx API, the game's IRQ installer, music state 2 and songs 0/2/3/4 are
  not in the object.** They are not reached by this song and the certified
  program does not contain them.
- **The cost model is the certificate's.** A tick call whose modulator reset
  runs a 254-iteration pre-load loop costs 19,081 cycles on the real machine —
  97 % of a frame at two calls a frame. The object states the value the loop
  reaches and not the loop, so the trackerprog carries the *sequence* of writes
  and not their cycle positions, which §2 drops.
