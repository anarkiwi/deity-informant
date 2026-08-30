# Prototype: JCH V20 as a trackerprog — the fifth family, and the song that ends

A **hand transliteration** of the two certified JCH NewPlayer V20 tuneprogs
([prototype-jch.md](prototype-jch.md), anatomy [§2](playroutine-anatomy.md)
fourth column) into trackerprogs
([prototype-trackerprog.md](prototype-trackerprog.md) §3), rendered by the same
universal player that renders Commando
([prototype-commando-trackerprog.md](prototype-commando-trackerprog.md)),
GoatTracker 2 ([prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)),
SID Wizard ([prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md))
and defMON ([prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md)),
and certified against each tune's own player on the PcodeVM.

Four results:

1. **Both builds render, over their whole horizons.**
   *Guldkornekspressen Intro* over its whole **2,401 ticks** with the inherited
   loop claim re-verified on the render, and *I Could Eat a Knob at Night* over
   its whole **8,577** — **0 divergences** on §2's observable, and for the
   second tune *stronger*: the write lists are **identical**, tick for tick,
   value for value, register for register, on every one of the 8,577.
2. **`end.kind = fixed_point` is taken, for the first time by any exemplar.**
   *Knob at Night* is `complete` with **period 1**: the wrapper's own two-byte
   countdown stops the player at frame 8,576 and the state never moves again.
   `loop` is null, the score is materialised to **`first_repeat`** — its orders
   end on `horizon`, not on a jump — and the render's last tick writes nothing
   while the tick before it writes 25 registers, which is what the certificate
   checks.
3. **The first family whose two builds disagree about having a shadow.** Knob at
   Night runs the whole player with I/O banked out (`$01 = $34`), so its 25
   register writes a frame are *memory* and a wrapper flushes its own copy;
   Guldkorn has no wrapper and writes the chip as it goes. And the flush's
   **order** is a datum of the *frame*, not of the tune: the same 25 registers
   low to high when the frame's own delay byte is zero and high to low when it is
   not, so a flush entry states the guard the image writes it under (§4.1).
4. **The layer invariant holds at five families**, with no branch on
   `meta.family` anywhere in `trackerprog/`. Five of the ten forms below are in
   the player and five are only in the data; two things the family was expected
   to force turn out to be worth **0 ticks** and are in neither.

Reproduce:

```
tools/trackerprog_jch.py $HVSC/MUSICIANS/J/JCH/Guldkornekspressen_Intro.sid \
    --source docs/certificates/jch-guldkorn-intro.json \
    --certify --out out/jch-tp/guldkorn

tools/trackerprog_jch.py $HVSC/MUSICIANS/P/Puterman/I_Could_Eat_a_Knob_at_Night.sid \
    --source docs/certificates/jch-knob-at-night.json \
    --certify --out out/jch-tp/knob
```

Contents: 1 the object · 2 the mapping · 3 the certificate · 4 what the spec
needed · 5 what the spec got right · 6 finding the data · 7 measurements ·
8 the ten things the family was expected to force.

The forms, in one line each. In the player: §4.1 a flush entry states its own
guard · §4.2 the row's pitch staged with the row the clock runs ahead of · §4.3
the row's commands spent at the fetch · §4.4 a register of the global channel
written by the voice whose write-out sends it · §4.5 the order's transpose
staged too, because the vibrato reads the *untransposed* note. In the data only:
§4.6 a column program is an act row, a wait row and an accumulator ranked after
them · §4.7 the wave column's jump resolved in place · §4.8 the row's duration
as the empty events that follow it · §4.9 the prelude armed by a cell the
note-on sets, and the effects skip it leaves · §4.10 the wrapper: a stream, a
countdown and seven overrides. §4.11 is the two that are worth nothing.

---

## 1. The object

`tools/trackerprog_jch.py` writes `trackerprog.json`;
`deity_informant/trackerprog/universal.py` renders it. The same seven sections:

| section | Guldkorn Intro | Knob at Night |
| --- | --- | --- |
| `meta` | 3 voices in order **2,1,0**; `commit_order (ad, sr, ctrl)`; **no shadow** — the write-out reaches the chip; a **countdown** row clock on `phase` against `speed` (3), the row at 0 and the fetch 2 steps early; `tick: fetch ; prelude ; row ; machine`; the row spends the tick when it keys | the same player, `speed` 2, and a **25-register image** whose 50 flush entries are the same registers in either direction, each under the guard of the frame's own delay byte (§4.1) |
| `pitch` | one table, `base 0` and **96** entries — the whole stored tuning, which is what the wave column's own offsets reach | `base 0`, **80** |
| `streams` | **10**: `pulse` 20 rows and `filter` 23, the two column programs; `wavetab` 64 rows, the wave table read at a cursor; `wave` and `pitch`, the five and three guarded rows that make a frequency out of it; `writeout`; `prelude`; `notestage`; `voicebits`; `channel` | the same ten, **3**/**2**/**19** rows, plus **`wrapdata`** — 8,577 rows, the wrapper's own four-byte record, one a frame |
| `accs` | **7**: the pulse sweep, the filter sweep, the wave clock, the slide, and the vibrato's three (its turn, its accumulator and its depth ramp) | the same seven |
| `instruments` | **19**, each eight columns: `adsr`, the wave flags, the volume/filter nibble byte, and the three program pointers | **5** |
| `score` | 3 order programs of 15, 8 and 8 `play` steps, all ending `jump 0`; **27** patterns from 26 the tune stores (one is reached under two different sticky instruments), **723** events, **13** commands | 45 steps each ending **`horizon`**; **18** patterns from 12, **839** events, **0** commands |
| `globals` | one channel: the cutoff and the filter program's step, the routing byte, the volume byte and its `or` mask, and one flag (`raw`) | the same, plus the data pointer, the two countdown bytes and the gate they close |

The player has one dispatch and it is on the *form* of a delta, a policy or a
stream row — never on the name of an effect. The seven accumulator ids
(`pulse.step`, `filter.step`, `wave.step`, `slide`, `vibrato.turn`, `vibrato`,
`vibrato.ramp`) are labels in the data.

---

## 2. The mapping, line by line

Left column is the certified tuneprog's own text
(`out/recert-main/jch-guldkorn-intro/tuneprog.md`, cited `jch.md:N`, and
`out/recert-main/jch-knob-at-night/tuneprog.md`, cited `knob.md:N`) and the
player's own code. Right column is the object.

| the player says | the trackerprog says | §5 row |
| --- | --- | --- |
| `tick(): phase -= 1; if phase >= 0: … else phase = b1747` (jch.md:322-329) | `meta.tempo` — a **countdown** on the `phase` cell against the `speed` cell, boundary 0, `early 2`. The funk-tempo arm below it is untaken: the speed is 2 or more in both builds, and a build-time assertion says so | §3.6 the row clock |
| `p_10E9`: `for v in 2, 1, 0:` … `if phase == 0:` the commit, `elif phase == 2 and timer_3 == 0:` the prefetch, else the effects (jch.md:336-340) | `meta.tick: fetch ; prelude ; row ; machine` and `meta.voice_order [2, 1, 0]` — the phases are the list, and the voice order is the last-writer over the global channel's registers (§7) | §4.1 |
| `voice_3[v].timer_3 -= 1; if < 0:` the staged row goes live (jch.md:340-343) | the fetch stages the event and the boundary applies it; a row's own duration is the **empty events that follow it** (§4.8) | §3.6 `dur` |
| `voice_2[v].f06 ← voice[v].b17BC`, `f00 ← b17B3`, `f03 ← b17AD`, `f09 ← b17B0`, `f03/f06 ← b17B9/b17B6` | `meta.row`'s `sets` step, one assignment per staged cell, and the three `meta.stage` rows that fill them (§4.2, §4.5) | §3.6 `meta.row` |
| `if voice_3[v].timer == 0:` the note-on, else `p_1409` (jch.md:355) | `Event.tie` and `row_consumes_tick: [[keys != 0]]` — a tie row runs the machine, a keying row spends the tick on its note-on | §3.6 `tie` |
| the note-on (jch.md:357-393): the wave pointer, the wave speed, the pulse pointer and its record, the volume nibble, the routing byte, `sid[v].ad`, `sid[v].sr`, `sid[v].ctrl = 9` | `Ins.on_note`, one inline §3.3 stream of five guarded rows — `sets` for the cells, a `point` for the pulse cursor and one for the filter's, and `reg.23`/`reg.24` for the two channel registers (§4.4). It is **one act**, and its edges are `ad`, `sr`, `ctrl` in `commit_order` | §3.5 |
| the prefetch (jch.md:405-505): the order pointer, the pattern pointer pair, the row's bytes, then `voice_2[v].f06 = $FE` and, where the instrument says so, `sid[v].ad = $F` / `sid[v].sr = 0` | the `fetch` phase and `Ins.prelude` at `early 2` — two guarded rows, the gate mask and the hard restart, the second armed by a cell the note-on sets (§4.9) | §3.5 prelude |
| the pattern byte loop: `$8x` duration and tie, `$Ax` instrument, `$Cx–$FF` a two-byte command record, `$01–$7D` a note, `$00` gate off, `$7E` hold, `$7F` end (anatomy:210) | `Event{dur, tie, ins, note, sounds, gate, cmds}` — the byte-range token class is spent, and `super` is the command class (§8, row 6) | §3.6 the note column |
| `T199D[ptr]`: `$8x` a transpose, `$FF` restart from `b1734`/`b1737`, `$FE` stop the track (jch.md:407-492) | `Order.play(pattern, transpose)` and `end {"jump": 0}`; `$FE` is a build-time assertion with its reason — **neither tune takes it** | §3.6 `Order` |
| `p_1409` (jch.md:522-538): `timer_4 -= 1; if < 0:` take the record's `next`, its direction and its frames, and its initial pair unless `$FF`; then `pw ±= rec6[cursor].b1894` | the `pulse` stream — one **act** row per record and one **wait** row of its frames — and `pulse.step`, an `Acc` on the `pw` cell **ranked after it**, so the reload and the step compose in one tick (§4.6) | §5 pulse sweep, #297 |
| `if x == 0:` the filter program, the same shape on `cutoff_hi` (jch.md:539-551) | the `filter` stream and `filter.step`, an `Acc` whose cell is `#cutoff` and whose scope is **global**, both guarded `voice_index == <the byte at the filter table's own record 0>` (§8, row 10) | §3.7, §5 filter sweep |
| `T17DB[cursor]`: `$7F` jump through `T181B`, `$7E` step back, `$80+` a note outright, else an offset; then `freq = FREQ[a] + b1743`, `b175D = T181B[cursor]`, and the cursor's own countdown (jch.md:552-584) | the `wave` stream's five guarded rows over `wavetab`, the jump resolved **in place** (§4.7); `pitch`'s three rows make the frequency; `wave.step` is the cursor's countdown, an `Acc` whose gate moves the cursor when the count does *not* | §3.3, §5 |
| `if f06 != 0:` `acc_4 ±= step`, `if $100B == 0: freq += acc_4` (jch.md:617-625) | the `slide` `Acc` on `sacc`, `phase` the direction cell, and `pitch` row 2, guarded on the `raw` flag the wave row leaves | §5 free slide |
| else the vibrato (jch.md:594-613): `acc_5 = FREQ[f00+1] − FREQ[f00]`, `+ acc_2` into its high byte, `>> b177E`; `timer_3` turns `b1776`; `acc_3 ±= acc_5`; `freq += acc_3`; `acc_2 += b1770` | three `Acc`s — `vibrato.turn` (the countdown that turns the direction), `vibrato` (`delta ((interval(note − xpose) + u16(0, vramp)) & $FFFF) >> vshift`) and `vibrato.ramp` — and `pitch` row 3 | §5 vibrato, and §4.5 |
| `p_1616` (jch.md:648-659): `sid[x].pw`, `sid.cutoff_hi`, `sid[x].freq`, `sid[x].ad`, `sid[x].sr`, `sid[x].ctrl = b175D & f06`, `sid.mode_vol` | the `writeout` stream, rank last: two producers for the pulse pair, `reg.22`, the frequency, the three edge writes and `reg.24` — the registers of the one global channel written by the voice that sends them (§4.4) | §4 producers |
| the Puterman wrapper (`p_0E41`): `$01 = $34`, the player, `for v in 24..0: buf[v] = ghost[v]`, `$01 = $35`, `buf[24] = $1F`, `buf[23] = $F3`, the four-byte record, the two pulse widths and the cutoff | `meta.shadow`, `globals.streams: [channel]` and seven `globals.commit` entries over the `wrapdata` stream (§4.10) | §3.1, §3.7 |
| `sub()`: 25 writes low to high when `b0F57 == 0`, and a delay loop between 25 writes high to low when it is not | the flush's **50 guarded entries** (§4.1) | §3.1 (§4.1) |
| `$0E2D`: `X = $80; DEX; …; Y = $22; DEY; …; INC $0E23` | the `channel` stream's own four rows: the two-byte countdown, and the `#playing` gate every flush entry is guarded on | §3.3 |

What disappears: `$FB`/`$FC` and the `(ptr),Y` walk of the order and the
patterns; the two-level pointer nest through `T19C5`/`T19E0`; the byte cursors
`timer_2`, `cursor_1781`, `cursor_1790` and `cursor_1795`; the `voice_map`
(`$1740,X = 0, 7, 14`) and every `X = 7v`; the packed `dir|frames` byte, the
`$F0`/`$0F` nibble split of the pulse record's own initial byte, and the `& $3F`
of the command byte; the wrapper's nibble shift and its delay loop; and the
three `$FF` terminators. Not one byte of the object names a memory location.

---

## 3. The certificate

`deity_informant/trackerprog/attest.py`'s comparison, inlined in the tool: §2's
observable over the **whole** certified horizon, the reference being the tune's
own player on `deity_informant.PcodeVM` — with the 6510 port modelled, because
one of the two builds banks the chip out and its writes are memory until its own
wrapper flushes them (§6).

| tune | ins | patterns | events | tuning | pulse | filter | wave | accs | ticks | SID writes | divergences | identical | permuted |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Guldkorn Intro | 19 | 27 | 723 | 96 | 20 | 23 | 64 | 7 | **2,401** | 63,229 | **0** | 119 | 2,282 |
| Knob at Night | 5 | 18 | 839 | 80 | 3 | 2 | 19 | 7 | **8,577** | 214,400 | **0** | 8,577 | 0 |

`compared` and `dropped` are §2's own three and three. Knob at Night's
`identical_ticks` is its whole tick count, which is stronger than the
certificate asks — per register, per value, per position in the tick. Guldkorn's
lists are **permutations** of the player's on 2,282 of 2,401 ticks and identical
on the other 119: its write-out sends `$D416` and `$D418` between the frequency
and the envelope of every voice, and §4's commit emits a voice's producers
before its edges, which is exactly the "order between registers of different
classes inside a tick" that §2's `dropped` list names. The multiset of writes is
the same on every tick of both tunes.

`end`: `{"tick": 2400, "kind": "loop"}` and `{"tick": 8576, "kind":
"fixed_point", "verified": true}`.

* Guldkorn's inherited loop claim **re-verifies on the render**: rendering past
  the first repeat, the next 1,512 ticks are the previous 1,512 write for write.
  The score says the same thing from the other side — the three orders are 378
  row steps each and the row clock is four ticks, so the order is 1,512 ticks
  long, which is the period the tuneprog measured.
* Knob at Night's **period is 1**, so there is no loop to re-verify and §6's
  materialisation is to `first_repeat`: the object's orders end on `horizon`
  after 45 steps, the wrapper's countdown closes `#playing` at frame 8,575, and
  the render's tick 8,576 emits **nothing** while 8,575 emits 25 registers.
  That pair is what `end.verified` checks.

There is no refusal to name on either tune: nothing is emitted partially, and
the arms neither horizon takes carry their reason — a `trap` row for the wave
column's step-back token, and build-time assertions for the order's `$FE` stop,
the funk tempo, the raw-frequency wave mode, an instrument whose release wave
pointer differs from its own, and the three pattern commands (`$9x`, `$Ex`, the
instrument patch) no score byte of either tune reaches. The third V20 tune of
[prototype-jch.md](prototype-jch.md), *Easy Does It*, **refuses by name**:

```
{"emitted": false, "refusals": [{"why": "sample stream", "cell": "mode_vol",
  "site": "$3FE0", "detail": "the tick is an entry of kind irq on pal_host_cia,
  not the player's own play call"}]}
```

which is §9's acceptance #2 demonstrated rather than asserted. The tool refuses
before it reads a byte of the tune's data, so there is nothing to emit partially.

---

## 4. What the spec needed

Ten forms. Five are in the player — three of them entries in one list that
already existed — five are only in the data, and §4.11 is the two the family was
expected to force that are worth nothing at all.

### 4.1 A flush entry states the guard the image writes it under

`meta.shadow.registers` is the ordered list of registers the image carries
(defmon-trackerprog §4.1). Knob at Night carries 25 and writes them **low to
high** on a frame whose own delay byte is zero and **high to low** on one where
it is not — the wrapper's `sub()` has two arms, a straight 25-store run and a
loop that spends the delay between each store. Both are taken: 3,887 frames take
the first and 4,689 the second.

Per voice that is `ctrl, ad, sr` against `sr, ad, ctrl`, which §2 rule 1 keeps
in tick order, so the direction is fully observable. Measured over the whole
8,577-tick horizon: flushing low to high on every frame diverges on **4,689**
ticks and high to low on **3,887**, which sum to the 8,576 frames the wrapper
runs: there is no one order, and every frame takes one of the two.

The form: a flush entry is a register, **or a register and the guard the image
writes it under** — the same shape `globals.commit` entries already have, whose
third element is an optional guard list. The four families that had a bare list
keep it (a bare register is the entry with no guard, and their objects and
renders are unchanged), and this build's flush is 50 entries, the same 25
registers twice under complementary guards. `Player.imaged` is the set the
entries name, which is what `channel_commit` asks about; the order is the list's.

The same measurement retires `commit_order` for this build and confirms §3.1's
claim about a shadowed family for the second time: swapping the edge registers
diverges on **0** of 8,577 ticks, because every edge write goes through the
image and the flush re-orders them. On Guldkorn, which has no image, either
other order diverges on **2,401 of 2,401** — so `(ad, sr, ctrl)`, which is what
`p_1616` writes, is the tune's and the two builds cannot disagree about it.

### 4.2 The row's pitch, staged with the row the clock runs ahead of

V20's prefetch stages the row's note in `$17B3` and its commit copies it to the
live `f00` two ticks later, so a row that does **not** sound — a `$00` gate-off
or a `$7E` hold — still moves the live note to whatever the last staged byte
was. §3.6's `{"note"}` row step takes the note from the *event*, which is right
for a row that sounds and says nothing at all for a row that does not.

The fetch's staging gains a row: the pitch the fetch read, `{"sets":
[["@pend_note", {"payload": "note"}]], "when": [["sounds", "!=", 0]]}`, into a
cell like the gate and the transpose beside it. The row program then has two writers and
neither is the other's spelling — the row that sounds takes its own note through
§3.6's step, and the rows that step takes no interest in take the one the fetch
left, `@note := pend_note + xpose`.

Measured: reading the note from the row alone diverges on **8** of Guldkorn's
2,401 ticks and **397** of Knob at Night's 8,577. Both counts are the tune's own
first frames plus every rest that follows a pattern the order enters under a
different state — small, and not zero.

### 4.3 The row's commands, spent at the fetch

`meta.row_command` says whether a command outlives its row; it does not say
*when* the row spends it. V20 spends it at the **prefetch**: a slide command
writes the step, the direction and the staged flag two ticks before the row
boundary that makes the flag live, and a vibrato command zeroes the running
accumulator there. A vibrato already sounding therefore changes shape two frames
early, which is a thing the player does and the object has to say.

The staging carries `{"commands": true}`, which is §3.6's own row step run at the
fetch instead of at the boundary — one implementation, one spelling, two
positions in the tick. Rendering them at the boundary instead, which is what every earlier family
does, diverges on **38** of Guldkorn's 2,401 ticks (the first at tick 1,004) and
**0** of Knob at Night's 8,577, which carries no command at all. This is the
check §6.4 asks for, applied before the field was added rather than after.

### 4.4 A register of the global channel, written by a voice

V20's write-out sends `$D416` and `$D418` **inside every voice's own write-out**
and `$D417` inside its note-on. §3.7 already says the filter is a global channel
resolved by last-writer; what it lacked was a way for a *voice* to write one of
the channel's registers, so the first draft of this object snapshotted the two
global cells into two more and emitted them from `globals.commit` after the
voices.

That is wrong in a way §2 rule 2 makes exact: the value the tick leaves is the
one the **last voice to reach its write-out** left, and a note-on later in voice
order moves the cell without sending it. Measured on Guldkorn, the snapshot
diverges on 0 ticks only because the two spare cells reproduce the ordering by
hand; the object with the spare cells needs five globals and three guards that
the direct form does not.

The form is one target: `reg.N` in a `sets` list is register `N` of the global
channel, appended to the voice's producer list and emitted where the voice
commits — which is the chip for a family with no image and the image for a
family with one. `globals.commit` keeps its job, which is the writes that happen
**after** all the voices (the wrapper's seven overrides, §4.10), and the object
loses `#cut_out`, `#vol_out`, `#wrote`, `#res_wrote` and `#vol_wrote`. The
render is also closer: Guldkorn's write list becomes a permutation of the
player's on every tick instead of a strict subset of one.

### 4.5 The order's transpose in a cell, because the vibrato reads the note without it

The order's transpose column is `play.transpose`, and §4's `sound()` fuses it
into the note cell, which is the canonical form: a note cell holds a pitch index
into the tuning. V20's vibrato does not read that index. Its depth is
`FREQ[f00 + 1] − FREQ[f00]` where `f00` is the pattern's **own** note byte, with
neither the order's transpose nor the wave row's offset — a semitone of the
untransposed note, which at a transpose of 12 is a different semitone.

The third and last row the staging carries: `{"sets": [["@pend_xpose",
{"payload": "transpose"}]]}` puts the play step's column into a cell beside the
pitch and the gate, and the accumulator's delta is `interval(note − xpose)`. The order's column stays where §3.6 puts it
and the note cell stays what §4 makes it; what the cell adds is the *other*
reader. Measured: taking
the interval at the transposed note diverges on **240** of Guldkorn's 2,401
ticks, and on 0 of Knob at Night's, which has no vibrato and a transpose of zero.

### 4.6 A column program is an act row, a wait row, and an accumulator after them

§3.3 names JCH's `rec6` and `rec7` as the form the stream was written from, and
§5's `reload` row and #297's "reload *then* step, in one tick" name the shape:
`pw = rec6[t2/4].b1893` unless `$FF`, then `pw ± rec6[…].b1894` in the same call.
The question the spec left open was whether that is one `Acc` with
`policy.reload` or two things.

It is two, and the split is defMON's (§4.8 there): a record **acts** — reloads
the pair unless its first column is `$FF`, takes its direction and its step —
and then **holds** for as many frames as its own column says, so a record is two
rows of a §3.3 stream. The step is an `Acc` on the same cell **ranked after the
stream**, so on the frame the cursor takes a link the record's own sets land
first and the step that follows uses the new one. Nothing in the player changed;
the rank order is the object's.

Measured, because the rank is the whole content of the claim: ranking the stream
*after* the accumulator diverges on **1,821** of Guldkorn's 2,401 ticks. On Knob
at Night it diverges on 0 — that build's three pulse records are self-loops with
a zero step, which is what a build that overwrites two voices' pulse widths from
a data stream has left of its pulse programs.

The note-on points the cursor at the row **after** the act it has already made,
which is how an instrument enters a program mid-record. The tune's own post-init
state enters one mid-*hold* — Guldkorn's three voices start at pulse records 12,
32 and 0 with 8, 3 and 0 frames left, and its filter at record 16 with 2 of 4 —
so `state0.cursors` carries the row **and** the hold the record has already
spent, which is `enter()` in §6.

### 4.7 The wave column's jump, resolved in place

`T17DB[cursor] == $7F` re-points the cursor through the *other* column and reads
the row it lands on **in the same frame** — and the waveform byte the tick sends
to `ctrl` is the one at the new cursor, not the old. That is defMON's `enter()`
one layer up, and it needs no field: the `wave` stream's first guarded row is
the jump and the rows after it read the cursor the first row moved. Rows are
applied in order inside one `rows()` call, so "resolved in place" is the row
order and nothing else.

The `$7E` token beside it — step the cursor *back* one and read that — is untaken
in both builds and is a `trap` row carrying its reason.

### 4.8 A row's duration is the empty events that follow it

V20's `$8x` byte sets a **sticky** duration cell, and the row it prefixes lasts
that many row steps *more*. §3.6's `dur` is "rows the event spans", and §4's
fetch spends a `dur > 1` event without applying it — which is right for the
packed rests GoatTracker 2 and SID Wizard have, and would drop a JCH row on the
floor.

The materialisation says it instead, which is §6's own rule: a row of duration
`d` is **one event and `d` empty ones**, each of which the fetch spends and the
boundary applies to nothing. The duration byte comes back out of the object as
the empty event that follows the row, which is what the byte-for-byte round trip
reads. The stickiness is the order walk's: a pattern entered under two different
duration or instrument states is two materialised patterns, and Guldkorn has one
such pair (26 stored patterns, 27 materialised).

### 4.9 The prelude is armed by a cell the note-on sets, and the skip it leaves

§3.5's table has JCH at `early = 2` with `set(ad,$0F) set(sr,$00) set(ctrl, mask
$FE)` and the note row's `set(ctrl,$09)`, and all of that holds. What it does not
say is what arms it: the hard restart runs where the **instrument's own flag
byte** says so, but the player reads that flag out of a cell (`$1754`) that only
a *note-on* writes, so a tie row that changes the instrument leaves the previous
instrument's flag armed. `Ins.prelude` is therefore the same stream for every
instrument and its rows are guarded on that cell, which the `on_note` sets —
`@hrflag := ins.flags & $80` — and on `keyed`, the field `meta.stage_sounds`
already stages with the row.

The prelude also **leaves the tick**: a hard restart jumps straight to the
write-out, so the pulse, the filter, the wave and the modulators do not run on
that frame. The object says it with a cell the prelude raises and the write-out
lowers, and every stream and arm of the machine is guarded on it. Measured:
running the machine on the prelude's own frame diverges on **586** of Guldkorn's
2,401 ticks and **816** of Knob at Night's 8,577.

### 4.10 The wrapper: a stream, a countdown and seven overrides

Knob at Night's player is the same V20 under 512 bytes of wrapper. Everything the
wrapper does is data:

* the ghost and the buffer are **one image** — the wrapper copies all 25 bytes
  every frame, so nothing distinguishes them — and `meta.shadow` is that image;
* the four-byte record it reads a frame is the `wrapdata` stream, 8,577 rows of
  two pulse widths (a nibble shift, not the reversal a reader might expect from
  the `LSR`/`ROR` chain), a cutoff and a delay;
* the pointer it walks is `#dptr`, advanced by one row of the `channel` stream,
  which runs at the head of the tick — so the record the *voices* leave is
  committed at the end of the same tick and flushed at the head of the next,
  which is the wrapper's own order;
* the seven bytes it writes over the player's image are seven `globals.commit`
  entries, after the voices and therefore after the image the voices wrote;
* its two-byte countdown is three more `channel` rows, and the `#playing` cell
  they close is a guard on every flush entry. `$0E2E = $80` and `$0E33 = $22`
  are 128 + 33·256 = **8,576** frames, which is the tick the certificate calls
  the first repeat.

Row 0 of `wrapdata` is the record the tune's own init leaves, which is what the
first frame's flush is guarded on and the only row no `globals.commit` reads.

### 4.11 Two things the family was expected to force, and neither is worth a tick

**The build byte's own effects skip.** `$17CA` is 0 in Guldkorn and 1 in Knob at
Night, and where it is set the prefetch frame skips the pulse, the filter and the
vibrato (`$1766`, `knob.md:9093-9096`, and `$1201`/`$1206` in the player). The first
object said so, and needed a
`meta.stage` row of its own — a cell saying the fetch had read a row at all —
to say it. Measured over the **whole 8,577-tick horizon of the only build that
sets the byte**: **0** divergences. That build's pulse programs are self-loops
with a zero step, its cutoff is overwritten by the wrapper before it reaches the
chip, and it has no vibrato, so skipping their frame is a thing no observation
distinguishes. The field is struck and the object states the skip nowhere.

**The staged instrument.** V20's commit copies a staged instrument byte the same
way it copies the staged note, and the first object staged it the same way.
Measured: reading the instrument from the row instead — §3.6's own `{"ins"}`
step — diverges on **0** ticks of either tune. The general form costs the family
nothing, so the object keeps it and the staging is struck. The note's staging
(§4.2) survives the same measurement at 8 and 397, which is the difference
between a form and a habit.

---

## 5. What the spec got right

Eight things this exemplar exercised without changing:

1. **`meta.tick` as a list.** `fetch ; prelude ; row ; machine` — a fourth
   ordering, and the first with the prelude *between* the fetch and the row.
   The five hooks and two flags §6.4 struck would have needed a sixth spelling.
2. **`row_consumes_tick` as a guard list.** V20's note-on writes `ad`, `sr` and
   `ctrl` and returns; every other path runs the machine. `[[keys != 0]]` is the
   whole of it, and the `false` arm defMON fixed (§4.5 there) is untouched.
3. **One inline stream, three places.** The instrument's `on_note`, the prelude
   and a command's rows are the same guarded object here, and the note-on's
   `when tie == 0` is the guard §3.5 says it is rather than a field.
4. **`Acc.gate` reporting the decision the step made.** Both of this family's
   countdowns — the wave cursor's and the vibrato's direction — are an
   accumulator that steps while its cell is not zero and, on the frame it does
   *not* step, reloads and turns something. `gate.false` is that frame, and the
   defMON fix (§4.6 there) is why reading the cell again would have been wrong.
5. **The cell vocabulary.** `#cutoff` is an accumulator's cell, a `sets` target
   and a `{"global": …}` read, through one implementation. `pw.lo` and `pw.hi`
   are halves of a 16-bit voice cell in a `sets` value.
6. **A command named by what it does.** `slide.up:0200`, `vibrato:031` — the
   `& $3F` index into the two-byte table is spent, and the two builds' commands
   would be comparable if the second had any.
7. **`interval(n)`**, and the `shr` that scales it. §3.2 struck `tablestep` for
   `interval(n) >> shift`, and this family's vibrato is exactly that plus a
   depth ramp in the high byte, which `u16(0, ramp)` says with the grammar §5
   already has.
8. **The producer list.** The write-out's five producers in declared order —
   the pulse pair, the cutoff, the frequency pair, the volume — with §2 rule 2
   keeping the last, is §4's own procedure with nothing added.

---

## 6. Finding the data

V20 is a code *template*: the two builds' player bytes from `$1000` to `$1666`
differ in **105 bytes over 49 runs**, and every one of them is a table operand.
So the extraction is one signature per block of the player, wildcarded at every
address operand and literal at every opcode, immediate and branch offset — and
each datum is the operand at a fixed offset of the match, which is the same rule
`trackerprog_defmon.py` uses.

Ten signatures cover the player (the tick, the voice loop, the prefetch, the
pattern command dispatch, the row commit, the pulse block, the filter block, the
wave block, the effects and `init`) and four more the wrapper, which only one
build has: `out["wrapper"]` is a datum like any other. Each signature matches
**exactly once** in the band, which is asserted, and the layout then proves the
shapes it depends on rather than assuming them:

* the four columns of each program are consecutive (`fcol1 == fcol0 + 1` …), so
  the print's "3-column filter" is an artefact of the region's origin (backlog
  P1) and not a fact about the tune: the filter program's fourth column is the
  `next` link, at the address the print calls `rec6[…].b1862`;
* the filter's **track byte** is column 3 of its own reserved record 0, which is
  the byte `CMP $185E,Y` with `Y = 0` reads;
* the hard restart's two bytes are record 0 of the **command** table, which is
  where `$11EC`/`$11F5` read them;
* the tuning's stored length is `(acc5 − freq) / 2`, the accumulator cell that
  sits immediately above it: 96 entries in both builds;
* the wave table's length is the distance between its two parallel columns.

The post-init image is the tune's own `init` run on the PcodeVM over
`machine.MachineImage.from_sid`'s pre-init image — power-on RAM under the load
band, and `$00`/`$01` as a KERNAL-initialised host leaves them — because one
build's uninitialised RAM is part of what its wrapper flushes. The reference VM
subclasses `PcodeVM` to log a SID write only where `machine.port_bank` says the
chip is mapped, which is prototype-jch.md §3's own direction-byte fix on the
oracle side: with I/O banked out, the player's 25 writes a frame are memory.

The tuning is materialised over the indices the tune's own reads reach — the
wave rows an instrument's cursor can walk against the notes its patterns play,
a wave row that is a note outright, and the vibrato's neighbours — and the build
asserts the top is inside the stored table, which it is for both (95 and 79).

---

## 7. Measurements

The print, `trackerprog.md`, measured the way architecture §11 asks:

| tune | lines | tokens | statements | blocks | header rows | data rows | `xz -9e` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Guldkorn Intro | 1,225 | 10,972 | 1,189 | 7 | 36 | 1,189 | 4,536 |
| Knob at Night | 9,699 | 34,195 | 9,672 | 7 | 27 | 9,672 | 6,068 |

`xz -9e` against the program that played it, §9's acceptance #3:

| artefact | raw | `xz -9e` |
| --- | --- | --- |
| `trackerprog.md`, Guldkorn | 60,955 | **4,536** |
| `trackerprog.json`, compact | 97,993 | 4,752 |
| — its `score` half (3 orders, 27 patterns, 13 commands) | 61,800 | 1,584 |
| — everything else (tuning, streams, accs, 19 instruments) | 36,184 | 3,360 |
| `tuneprog.md`, the source print | 30,082 | 5,664 |
| the whole load band | 3,343 | 2,472 |
| `trackerprog.md`, Knob at Night | 193,457 | **6,068** |
| `trackerprog.json`, compact / its `score` half | 690,967 / 72,333 | 16,268 / 792 |
| its `tuneprog.md` / load band | 308,909 / 39,424 | 19,904 / 9,600 |

The layer's claim holds on both, and Knob at Night's is the **widest margin of
any exemplar**: 6,068 against 19,904, a third of the print that carries the same
tune. Both prints carry the same 34,304 bytes of wrapper data, one as rows of a
stream and one as a hex dump of a region, and the score's own half compresses to
792 bytes. Guldkorn's margin is the ordinary one — 4,536 against 5,664 — and the
object says where it goes: seven accumulator arms are printed once per
instrument, which is nineteen times.

**What the object needs, poisoned one datum at a time.** Each row is the object
with one thing taken out, re-rendered against the tune's own player; the count is
§2 **divergences**, not permutations. **Over the whole horizon of each tune** —
which is where the last row was decided, and the row above it. Every poison but
one is a mutation of the emitted object; the row's commands are the tool's own
`--late`, because *when* they run is not a field of the object.

| datum taken out | Guldkorn / 2,401 | Knob / 8,577 |
| --- | --- | --- |
| `commit_order`, run `(ctrl, ad, sr)` | 2,401 | **0** — the image re-orders them |
| `commit_order`, run `(sr, ad, ctrl)` | 2,401 | **0** |
| the voices committed 0, 1, 2 | 2,401 | **0** — the image hides voice order too |
| the pulse program stepped before it reloads | 1,821 | 0 — three self-loops with no step |
| the image flushed low to high on every frame | — no image | 4,689 |
| the image flushed high to low on every frame | — no image | 3,887 |
| the hard restart's own skip of the machine | 586 | 816 |
| the vibrato's interval at the transposed note | 240 | 0 — no vibrato, no transpose |
| the note read from the row and not from the fetch | 8 | 397 |
| the row's commands at its boundary, not at the fetch | 38 | 0 — no commands |
| the build byte's own effects skip | — byte is 0 | **0** — and the field is struck |
| the instrument read from the row and not from the fetch | **0** | **0** — and the staging is struck |

The four zeros in the right column that are not "the family has none" are this
section's content. Two are §3.1's own claim about a shadowed family, measured for
the second time and now with a second consequence: an image hides **voice order**
as well as `commit_order`, because every write the voices make lands in it and
the flush emits the whole thing in its own order. The other two are §6.4's first
check, applied to two fields that were expected and then measured away (§4.11).

Code, against defMON's:

| file | lines | role |
| --- | --- | --- |
| `deity_informant/trackerprog/universal.py` | 1,063 (was 1,033) | §4 + §5, one procedure over the object |
| `deity_informant/trackerprog/printer.py` | 628 (was 618) | the flattened form and §6.2's numbers |
| `deity_informant/trackerprog/attest.py` | 81 (unchanged) | §2's comparison |
| `tools/trackerprog_jch.py` | 1,546 | the transliteration, 14 signatures, the two builds |
| `tests/trackerprog/test_jch_oracle.py` | 304 | the two certificates and the byte-for-byte round trip |
| `tests/trackerprog/test_universal_fetch.py` | 169 | hermetic snippets, one per form of §4 |

The player grew by **30 lines** to carry a fifth family — 38 added against 8
taken out, of which 16 are comment or docstring, because all five forms are
single-family data forms and each is marked at its own row with its reason and
its measurement. Three of the five (§4.2, §4.3 and §4.5) are entries in a list
that already existed, one (§4.1) is a guard on an entry of a list that already
existed, and one (§4.4) is a target spelling. It still has neither Commando, nor
GoatTracker, nor SID Wizard, nor defMON, nor JCH in it.

---

## 8. The ten things the family was expected to force

Ten expectations came with this exemplar. Six held (1, 2, 4, 6, 7, 9), two held
in kind but not in spelling (8, 10), and **two were wrong** (3, 5) — and saying
which is which is the point of writing it down.

| # | the expectation | what the code said |
| --- | --- | --- |
| 1 | `end.kind = fixed_point`, which no exemplar had taken: period 1, `loop` null, materialise to `first_repeat` | **held.** Knob at Night's period is 1 at tick 8,576 and the object carries the reason: the wrapper's two-byte countdown, `$80` and `$22`, is 128 + 33·256 = 8,576 frames, and the `#playing` cell it closes guards every flush entry. The orders end `horizon`, the score is materialised as far as the horizon reaches, and `end.verified` is the render's own last two ticks — 25 writes, then none |
| 2 | a family whose two tunes disagree about having a shadow | **held, and it is the first.** One build's `meta.shadow` is 25 registers and the other has no `shadow` key at all. What was not foreseen is that the flush **order** is a datum of the frame rather than of the tune (§4.1), which is the one form of the five the player gained that is a new field |
| 3 | §3.3's two column programs, `rec6` 4 columns and `rec7` 3 | **wrong in fact, held in kind.** *Both* are four columns: the filter program's fourth is its `next` link, at the address the print attributes to `rec6` because a region prints under several derived origins (backlog P1). The corrected reading is in §6, and the `$FF`-keeps-the-value sentinel, the direction bit and the frame count are exactly where §3.3 says |
| 4 | reload *then* step, in one tick: one `Acc` with `policy.reload`, or two — measure it | **held, and it is two.** A record acts and then holds — defMON's own split — and the step is an `Acc` **ranked after** the stream. Ranking it the other way diverges on 1,821 of Guldkorn's 2,401 ticks (§4.6) |
| 5 | `links` on a re-trigger: §5's second family, the arm re-pointing the pulse cursor *and* reloading the accumulator in one step | **wrong.** It is not an `Acc.links` at all: it is the instrument's `on_note`, which is one inline §3.3 stream, and a `point` beside a `sets` in the same act is what §3.5 already says an instrument's note-on is. The object uses `links` nowhere. §5's `links` row keeps GoatTracker 2 and the hermetic clamp snippet, and this citation should go |
| 6 | the note column's `super` token, spent the way the layer spends a byte-range token class | **held.** `super` is the `$C0–$FF` class, an index into a two-byte command record, and it becomes `Event.cmds`: 13 commands in Guldkorn (11 slides and 2 vibratos) and **0** in Knob at Night, which drives its pulse and cutoff from the wrapper's data stream instead |
| 7 | the order's `$FF`/`$FE` pair and the transpose column; `$FE` unexercised by any family so far | **held, and `$FE` still is.** Guldkorn's three orders each end `$FF` — a jump to step 0, which is where the restart pointer the init saves points — and the transpose column is `play.transpose`. Neither tune stops a track, so `$FE` is a build-time assertion carrying its reason, and this document does not claim it |
| 8 | the prelude, a fourth time: `early = 2`, the gate mask and the hard restart | **held in kind, not in spelling.** The rows are §3.5's, but the flag that arms them is a *cell the note-on sets*, not a column of the instrument the prelude reads — a tie row can change the instrument without changing the flag (§4.9). And the prelude leaves the tick: 586 and 816 ticks diverge if the machine runs on its frame |
| 9 | `commit_order (ad, sr, ctrl)`, and a measurement rather than a caveat if the ghost flush hides it | **held, and it is a measurement.** `(ad, sr, ctrl)` is `p_1616`'s order, and any other diverges on all 2,401 of Guldkorn's ticks; through Knob at Night's flush, both other orders diverge on **0** of 8,577 (§4.1) |
| 10 | the filter as a global channel with an owner: last-writer, or a guard like SID Wizard's | **held, with a fact the spec did not have.** It is a guard on `voice_index`, like SID Wizard's — but the value is not a constant in the player: it is **a byte of the tune's own filter table**, column 3 of the reserved record 0, which `CMP $185E,Y` reads with `Y = 0`. "JCH's filter runs on track 0" is a datum of these two tunes, not of the family |

Two more the exemplar produced without being asked: the two fields measured to
zero (§4.11), which are the same shape as §6.4's first check and the reason it is
worth running before a field is added rather than after.
