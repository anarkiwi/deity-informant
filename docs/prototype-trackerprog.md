# Prototype: trackerprog — a universal tracker representation

Design prototype, not yet a certified exemplar: the schema, the semantics, the
lift and the acceptance for a **trackerprog**, the layer above
[tuneprog-architecture.md](tuneprog-architecture.md). A tuneprog moves a tune
from opaque bytes to a certified per-tick *program*; a trackerprog moves the
music from player-specific code-plus-data to player-independent *data* — a pitch
table, instrument definitions and patterns that play pitches with instruments —
rendered by **one fixed universal player**. Effects are **bounded
accumulators**.

Empirical ground: [playroutine-anatomy.md](playroutine-anatomy.md) §2, which
shows all nine playroutines are one object (STATE, TABLES, PLAY), and the six
certified families — [GoatTracker 2](prototype-goattracker.md), [SID
Wizard](prototype-sidwizard.md), [JCH V20](prototype-jch.md),
[Hubbard](prototype-commando-floor.md), [defMON](prototype-automatas.md),
[Follin](prototype-follin.md). Galway, Walker and Blackbird are **prose-only**:
no certificate covers them (architecture §9.2) and no schema row rests on them
alone. The survey fact that sizes the layer: 91.6 % of traced HVSC by weight has
≥ 50 % of its indexed play sites on a voice-like domain (architecture §9.3, line
1009; its own summary line reads "the SID stride appears in 90 % of tunes"), so
the object this schema names is the population's.

Citations: `anatomy:N` is a line of `playroutine-anatomy.md`; `jch:N`, `gt2:N`,
`sw:N`, `commando-floor:N`, `follin:N` a line of the matching `prototype-*.md`;
`gt2.md:N`, `jch.md:N`, `sw.md:N`, `commando.md:N`, `automatas.md:N` a line of
`out/recert-main/{gt2-je-suis-linus,jch-guldkorn-intro,sw-emomyst,
commando-song1,automatas}/tuneprog.md`.

Contents: 1 definition · 2 observable and certificate · 3 schema · 4 the
universal player · 5 effects as bounded accumulators · 6 the lift · 7 what
landed · 8 refusals and boundaries · 9 acceptance · 10 open.

---

## 1. Definition

| term | meaning |
| --- | --- |
| **trackerprog** | one data object: `{meta, pitch, streams, accs, instruments, score, globals}` — no code, no bytecode escape, no per-family construct |
| **universal player** | one fixed tick procedure (§4) shared by every trackerprog; the only executable in the layer |
| **certified-equivalent (T)** | for every tick of the source tuneprog's certified horizon, the universal player's observable (§2) equals the tuneprog's |
| **accumulator** | a bounded state machine `Acc(target, width, delta, bound, policy, rate, phase, links, scope)` (§5); the only per-tick mutation an effect may be |
| **stream** | a finite table of steps with holds and one terminator (§3.3); the only sequencing an instrument, a prelude or the score may be |
| **lift (T0–T3)** | certified tuneprog + S6 naming plane → trackerprog, fail-closed: what does not fit is a `Refusal(reason, cell)`, never an approximation |

Layer invariant, as a test: two trackerprogs lifted from different families must
render on the same player with no family branch; the source family survives only
as provenance in `meta`. The schema may not add a construct for one family — the
repo rule (architecture §11: two families, or one plus a survey count) applies
to schema rows exactly as to view heuristics, and the two exceptions below are
marked at their rows. Oracle chain, one link longer: `sidplayfp ⇐ PcodeVM ⇐
tuneprog ⇐ trackerprog`.

---

## 2. The observable and the certificate

The tuneprog observable — the ordered interleaved SID write list — cannot be the
trackerprog's: *which register a player writes before which* is an idiom (GT2's
ghost flush emits 25 writes **high to low**, `for r in 24..0`, `$D418` first and
`$D400` last, anatomy:766; Hubbard writes ctrl, pw, AD, SR per fetch,
commando-floor:201-205; JCH's write-out is its own order, jch:172-181), and
reproducing it would carry the player back in. The anatomy states the license
(anatomy:151-155): *write order matters only at the frame edge and for gate
edges*, a gate 1→0→1 or TEST 1→0 inside one call being a real event
(anatomy:153-154). Extending it to AD/SR is this document's claim, not the
anatomy's, whose AD/SR point is narrower — the envelope rate counter is not
reset by gate, so a note started against a running release counter has its
attack delayed (anatomy:125-133). The extension is *conservative*: it can only
make the certificate stricter, and it keeps SW 1.6's `AD,SR` and 1.9's `SR,AD`
(anatomy:1232) distinguishable rather than silently equal.

The trackerprog observable, per tick — implemented as `grid.reduce_tick`
(`grid.py:209`), whose `TickObs(edges, values)` is the whole comparison:

| rule | registers | reduction |
| --- | --- | --- |
| 1 | `ctrl`, `AD`, `SR` (`grid.EDGE`) | per voice, **every write kept in tick order**, unchanged repeats included — these may be written more than once a tick, and GT2's flush writes all three every tick whether or not they moved |
| 2 | `freq`, `pw`, `cutoff` (`grid.PAIRS`) | one value per tick, the last the tick left: the DACs are level-sensitive, and the two 8-bit-bus writes a pair takes are already a print convention (architecture §6.1). `pw` carries the SID's 12-bit projection (`grid.PW_HI`), `cutoff` its 3+8 split (`grid.PAIRS[6]`) |
| 3 | `res_route`, `mode_vol` (`grid.LEVEL`) | one value per tick, the same way — Hubbard's drum-then-arpeggio double write of `$D401` is last-wins under rule 2; a `mode_vol` carrying a sample stream is refused outright (§8) |

Precedents: the Ghidra emulate oracle already compares "the ordered sequence of
SID register changes, both sides reduced by the same rule" (architecture §5.4).
The certificate names both halves, kept and dropped:

```jsonc
{
  "source": {"tune": "...", "certificate_digest": "..."},   // binds to the tuneprog cert
  "compared": ["per-voice ctrl/AD/SR write order", "freq/pw/cutoff tick values",
               "res_route/mode_vol tick values"],
  "dropped":  ["order between registers of different classes inside a tick",
               "order between voices inside a tick", "cycle position inside a tick"],
  "ticks": 8236,                       // the whole certified horizon, never less
  "divergence": null,                  // else {tick, register, expected, got}
  "refusals": [],                      // non-empty ⇒ no trackerprog is emitted
  "loop": {"period": 6720, "first_repeat": 8235} | null,    // inherited claim, re-checked
  "end":  {"tick": 8235, "kind": "loop" | "fixed_point" | "horizon"}
}
```

Three source shapes, three ends. `complete` with `period > 1` closes: `loop`
non-null, `end.kind = loop`. `complete` with **`period = 1`** did not loop — the
state reached a fixed point and the tune *ended* (`jch-knob-at-night` period 1
at tick 8,576, architecture §9.2; Follin song 1 period 1 at call 12,996,
follin:93): `loop` null, `end.kind = fixed_point`, materialisation (§6) to
`first_repeat`. A `horizon` tuneprog yields the horizon's score, `loop` null,
`end.kind = horizon`, `Order` ending in the `horizon` terminator (§3.6).

---

## 3. The schema

Serialised as tagged JSON in the S4 style (`ir.enc` vocabulary): `$trackerprog
$pitch $stream $acc $ins $pat $ord $cmd`, dicts as `{"$dict": [[k, v], …]}`.

### 3.1 meta

Cadence (`cycles_per_tick`, `source`), SID model, subtune, source tune and
family (provenance only), universal-player semantic version, and
**`commit_order`**: the permutation of `(ctrl, ad, sr)` the commit emits per
voice (§4) — one datum per tune, lifted like the pitch table, not a player
branch. Four certified families take three of the six values, one of them two
across its versions:

| source | `commit_order` | evidence |
| --- | --- | --- |
| JCH V20 | `(ad, sr, ctrl)` | `p_1616` writes `ad`, `sr`, `ctrl` in that order — jch:178-180, jch.md:656-658 |
| GoatTracker 2 | `(sr, ad, ctrl)` | the flush runs `$D418`→`$D400`, so per voice offset 6, 5, 4 (anatomy:766) |
| SID Wizard 1.6 / 1.9 | `(ad, sr, ctrl)` / `(sr, ad, ctrl)` | anatomy:1232, the HR and note-start frames |
| Hubbard | `(ctrl, ad, sr)` | `sid[v].ctrl`, then `pw`, then `ad`, `sr` — commando-floor:201-205 |

### 3.2 pitch

`pitch: [u16; N]` — the tune's frequency table as the lift **materialises** it,
plus annotations it proves: tuning (`12-TET` where `recover._freq` names it),
base note, resolution. Materialised, not copied: storage is an idiom like any
other, so Blackbird's two byte arrays overlapped by 15 bytes, whose
quarter-semitone entries are the **sum of two entries of the same array at fixed
offsets** (anatomy:145-147), lift to explicit u16 rows — the values read, not
the bytes stored (prose-only family, so a projection). Hubbard's PAL table is `N
= 96`; GT2's `FREQ_LO`/`FREQ_HI` at `$14E3`/`$1543` are **96** entries each
(anatomy:742), of which a tune reaches what it reaches — JCH's `rec4[96]` prints
95 (jch:107). All nine players have such a table (anatomy:141), already the
`freq_table` role.

Every *note* elsewhere is an index into this table or a signed index offset.
Accumulator deltas are not notes: they are in the **target register's own
units**, raw frequency for a `freq` target (Hubbard's `porta & $7E` step,
commando-floor:236-238; GT2's 16-bit speed-table entry, gt2.md:683-684), and
§5's `tablestep` is the bridge from a note interval into those units.

### 3.3 streams

The one sequencing form. A stream is a finite table of steps:

```
Step = { sets: [ set(target, value), … ]   // shadow assignments, in this order,
                                           // all inside one tick (Walker's gate 1→0→1)
       , op:   acc(acc_id) | pitch(offset | absolute) | none
       , hold: k ticks (k ≥ 1) }
Terminator = jump(row) | halt
```

A stream has a `rate: k` — **one meaning everywhere in this schema** (§3.6, §5):
a divider, the object advances once every `k` ticks, `k ≥ 1`, so a step occupies
`hold × rate` ticks. There is no "steps per tick": defMON's cascades run up to
8×/frame under a CIA cadence, but the tick *is* the entry ("sidTAB row = DL+1
calls", anatomy:213). Four families read that way — Hubbard `pwdelay -= 1; if <
0: pwdelay = pspeed & $1F` (commando-floor:223-225), JCH `phase -= 1; if phase <
0: phase = b1747` (jch:117-124), GT2's wavetable hold `if T16F9[1 + t1] ==
timer_4 … else timer_4 += 1` (gt2.md:564-569), defMON `DL+1` (anatomy:213).

What lands here: GT2 wavetables (`T16F9` rows of wave/note with `$FF` jump, the
`timer_4` compare the hold — gt2.md:564-569; the field name alone carries no
role, gt2:84); GT2/SW pulse, filter and speed tables; JCH's two column programs
— `rec6` pulse, **4** columns `[init/keep, Δ, dir|frames, next]` (`$FF` = keep,
else nibbles to `pw_lo`/`pw_hi`; `pw += b1894`; `& $80` direction, `& $7F`
frames — jch.md:527-538) and `rec7` filter, **3** columns `[init, Δ, frames]`
(jch.md:544-546), counts at jch:82 and jch:106-107; SW tempo programs; defMON
sidTAB rows (variable-length register-column records with delay and jump,
anatomy:211 — the form at its most general); and the prose-only Galway, Walker
and Blackbird programs. The stream is what all of these already are: *rows,
holds, one terminator*.

### 3.4 accumulators

Declared once in `accs`, referenced by streams, instruments, preludes and
commands. The full object is §5.

### 3.5 instruments

```
Ins = { adsr: (ad, sr)
      , prelude: (stream, early: k) | null   // ends k ticks before the next row boundary
      , streams: { wave: (stream, row), pulse: …, filter: …, pitch: … }  // any subset
      , accs: [acc_id, …] }                  // the modulations armed at note-on
```

Hard restart is **not** one fixed shape, and the first draft's `{early, ad, sr,
first_ctrl}` record cannot hold the families: SW 1.6 writes AD,SR and 1.9 SR,AD
(anatomy:1232); Blackbird's prelude has no TEST bit (anatomy:133-135); Walker
retriggers the gate off/on *inside one call* with no early frames
(anatomy:139-140, 214). A prelude is therefore just a stream (§3.3) of `set`
steps ending `early` ticks before the next row boundary — no new construct, and
the write order §2 compares is the stream's own step order:

| source | prelude | evidence |
| --- | --- | --- |
| JCH V20 | `early = 2`; row `set(ad,$0F) set(sr,$00) set(ctrl, mask $FE)`, note row `set(ctrl,$09)` | jch.md:501-511, jch:150-158 |
| GoatTracker 2 | `early = gatetimer` (instrument column 7); row `set(ctrl, wave & $FE)`, note row `set(ctrl, firstwave\|TEST)` | anatomy:214, anatomy:742 |
| SID Wizard | `early = 2`; rows in the version's own AD/SR order, then `set(ctrl, TEST\|gate)` at tick 2 | anatomy:1232-1233 |
| defMON | a sidTAB row program: `WG=00 AD=0F SR=00` → `WG=09` → sound | anatomy:214 |
| Blackbird (prose-only) | `early = 2`; `set(sr,0) set(ctrl, gate off)`, note row ADSR=0000 then the real AD/SR | anatomy:133-135 |
| Hubbard, Galway, Follin | `null` — Hubbard cuts notes with SR=0, Galway pulses TEST at note-on | anatomy:137-140 |

GT2's `gatetimer` **is** `early`, not a second field ("gatetimer frames early,
firstwave with TEST", anatomy:214), so the first draft's `gate.timer` row is
deleted — single-family only because it duplicated `early`. The nine-family
"sound definition" row (anatomy:211) reduces here: Hubbard's 8-byte SID image +
fx bits = `adsr` + armed `accs`; GT2's 9 columns + pointers = `adsr`, `prelude`,
four stream refs; defMON's "the sidTAB row *is* the instrument" = an instrument
that is only `streams`.

### 3.6 score

```
Pattern = rows of Event
Event   = { note: index | rest | hold | keyoff | keyon
          , ins:  instrument | none
          , cmds: [Cmd, …]          // in row order; §4 emits them in that order
          , dur:  ticks }
Cmd     = set_tempo(stream | k)     // a divider or a tempo stream
        | set_vol(v)                // $D418 low nibble, global, last writer wins
        | set(target, value)        // shadow assignment; commits with the tick
        | set_register(reg, value)  // immediate chip write, reg a literal 0..24
        | set_stream(slot, stream, row)          // re-point a stream, reset its hold
        | arm(acc_id, overrides) | disarm(acc_id)
        | porta(acc_id, target_note) | filter_set(…) | gate(mask) | break
Order   = per-voice sequence program over
          { play(pattern, transpose, repeat, vol?, tempo?)
          | for(n){…} | call(seq) | ret
          | jump(row) | stop | horizon }   // bounded call depth, stated (Galway: 8)
```

Six changes from the first draft, each forced by a source:

| change | why, and the evidence |
| --- | --- |
| `play` gains `vol?`, `tempo?` | SW's orderlist columns are pattern, transpose, **volume, tempo**, stop, loop; GT2's pattern, repeat, transpose, loop; JCH's `[transpose] pattern` (all anatomy:209). Optional, `none` where a family has no column. `vol` lands on the one global `$D418` nibble (sw:109), so three voices' columns resolve by last-writer, which §2 makes exact |
| a `horizon` terminator | a source materialised only as far as the certified ticks reach, distinct from `stop` (Hubbard's `$FE`, SW's stop — anatomy:209) and from `jump`. The same fact as `end.kind = horizon`, stated twice |
| `arm(acc_id, overrides)` replaces `arm(acc_id, param)` | `Acc` has no `param` and should not: GT2's vibrato parameter selects a bound *and* a step (`b1096 = T1851[y] & $7F` is speedcmp, gt2.md:812; `T1863[y]` the depth or shift, gt2.md:653-684), so the command re-binds a subset of `{delta, bound, rate, phase}` on a declared `Acc` |
| `set` for a shadow, `set_register` for the chip | two families write registers from a command and differ in *when*. GT2 commands 5/6 put AD/SR into the ghost, which the flush emits at the commit (anatomy:876) — that is `set`, the assignment §3.3 already has. Follin's `$85` lists write `$D400+r` **immediately, no deferred flush**, for an arbitrary register of any voice (anatomy:1803; `sid.reg[a75] = …`, follin:160-167) — that is `set_register` |
| `set_register`'s index is a literal `0..24` | Follin's resolves, because T2 materialises decoded score bytes exactly as it materialises pattern rows. Where it does not, the refusal is `command residue` (§8) — the 36 `index not a voice` sites T0's sweep already names one layer down (backlog §4, W4) |
| `set_stream(slot, stream, row)` | GT2 commands 8/9/A re-point the wave, pulse and filter tables and zero the matching hold (`waveptr=A (wavetime=0)`, anatomy:876) — a re-point plus a link (§5), not two opcodes |

`set_tempo(stream | k)` has two certified families: GT2's `funktempo`, loading a
two-entry alternating tempo (`funktempotbl[0..1] = speedtbl[A]`, anatomy:876),
and SW's tempo program (anatomy:213). Per-**voice** tempo likewise: GT2's
command F sets one voice when bit 7 is set and all three otherwise
(anatomy:876), its tempo per channel (anatomy:213), and SW carries a tempo
column per orderlist, one per voice (anatomy:209). So `tempo` is per voice with
a global default.

The order grammar is a sequence *program*, not only a list, because two
families' scores are programs (Galway's call/jmp/for-next with an 8-deep stack,
Follin's byte streams with call/loop — the dispatch at follin:160-175); a flat
tracker orderlist is the degenerate case. It stays data: no conditionals, no
arithmetic, statically bounded. Commands are the universal set only; a family
command not expressible as one of these plus an `arm` is a refusal, not a new
opcode.

### 3.7 globals

The filter as a global channel (cutoff streams and accumulators, resonance,
routing), master volume, per-voice and default tempo. Filter ownership — SW's
owner voice, JCH's "filter runs on track 0" — is last-writer over the global
channel, which the observable makes exact without an ownership construct.
Keyboard tracking (SW `CKBDTRK`) is **not** a `tablestep` term: it adds an
*absolute* table entry, `a11 = FREQ[$E + (freq_idx + b1024[$2C + b1024_idx])]`
then `cutoff_hi = (a11 + cutoff_hi) + c6` (sw:110-116), where `tablestep` is a
difference of adjacent entries. It is §5's `tabcell(T[c])` delta on the cutoff
target — the same construct defMON's oscillator uses (automatas.md:433-437), so
it earns its row on two families and needs none of its own.

---

## 4. The universal player

Normative semantics — anatomy §2's pseudocode made exact. One tick:

```
tick():
    for v in voices:                              # per-voice tempo, §3.6
        tempo[v].step()                           # a divider or a stream; row clock
        if row_boundary(v): sequencer_step(v)     # consume Event; note-on arms the
                                                  # instrument's streams and accs
        for s in active_streams(v): s.step_if_hold_elapsed()
        for a in active_accs(v):    a.step_if_rate()          # §5 semantics
        commit(v)
    commit_global(): cutoff one value (split 3+8), res_route, mode_vol

commit(v):                                        # the tick's per-voice edge list
    1  prelude steps due this tick, in the prelude stream's row order
    2  the voice's other stream `set` steps, in stream declaration order
    3  the Event's `set_register` writes, in `cmds` order      # Follin $85, immediate
    4  the voice's freq/pw producers, in declared order        # §2 rule 2 keeps the last
    5  ad, sr, ctrl in `meta.commit_order`                     # §3.1
```

Steps 1–3 and 5 deposit into the ordered ctrl/AD/SR list §2 compares; step 4
deposits 16-bit values §2 reduces to the tick's last; voice order inside a tick
is dropped by §2 and said so in the certificate.

**Producers, not a sum.** The first draft's `freq = pitch[note + …] + Σ accs`
cannot reproduce Hubbard: within one tick its vibrato, portamento, drum and
arpeggio each *store* `freq` independently, the arpeggio storing an absolute
`FREQ[note + $C]` (commando-floor:213-251) — a sum of deltas is the wrong
algebra. A voice carries an ordered **producer list** per 16-bit target, each
`Producer(target, mode, value)` with `mode add` meaning `pitch[note + transpose
+ offset] + Σ accs(this)` and `mode set` an absolute value — a table entry or a
live cell. `commit` evaluates them in declared order and §2 rule 2 makes only
the last observable, which is the chip's own semantics. GT2, JCH, SW and defMON
declare one `add` producer per target and degenerate to the old formula; Hubbard
declares four on `freq`.

Everything a real player does beyond this — ghost register files and flush
loops, unrolled voices, `X = 7v` double-duty indices, SMC-patched dispatch,
1-based tables, relocation, stack tricks — is compilation, already decompiled
away by S4–S6, and leaves no residue in the data. That list is the "symptoms"
tables of the tracker prototypes, each row a player idiom the tuneprog erased;
the trackerprog is the claim that what remains fits this procedure.

---

## 5. Effects as bounded accumulators

```
Acc = { target : freq | pw | cutoff | note | wave-param | gate-mask
                 | split(k, 8)                    # one value across two registers
      , width  : 8 | 11 | 12 | 16                 # the value's modulus
      , delta  : const(k)                             # signed
             | field(cell, mask)                      # a masked field of a live cell
             | tabcell(T[c], signed = k | unsigned)   # an absolute table entry at a cell
             | tablestep(P, n, shift)                 # (P[n+1] - P[n]) >> shift
             | repeat(Δ, n)                           # n·Δ, a triangle's closed form
             | Δ + carry(site)                        # any of the above, plus a live carry
      , bound  : { interval: [lo, hi], from: proved | projected | observed
                 , witness: <guard | mask | period> }
      , policy : wrap | reflect | reflect-complement | clamp(v) | halt | reload(v)
      , rate   : every k ticks (k ≥ 1)            # the §3.3 divider, one meaning
      , phase  : bit(self, k) | bit(cell, k) | cell != 0 | fn(global_counter) | acc(id)
      , links  : [ reset(acc_id | stream_slot), … ]   # what this Acc's events zero
      , scope  : read from the value cell's region index domain }
```

**Bounded** is the invariant, not a hint: `bound × policy` makes the reachable
value set finite and statically known; the trackerprog states each interval and
the renderer asserts it — the tuneprog's envelope discipline one layer up. The
three `bound.from` tags differ:

| `from` | source of the interval | evidence |
| --- | --- | --- |
| `proved` | a guard on the update path | GT2 `if b14A0 < b1096` against the speedcmp cell (gt2.md:812,819); Hubbard `if ins.pw_hi == $E` … `== $8` (commando-floor:230-233) |
| `projected` | the write's own mask — the interval the chip can see | Hubbard's pw is 12-bit only because the store is `(pw_hi + carry) & $F` (commando.md:380, commando-floor:325); SW's `cutoff_lo & 7` (sw.md:873,881); `grid.PW_HI` is the same projection on the observable side |
| `observed` | `history.py` over the certified horizon, under the period witness | JCH's pulse and filter segments have no guard and no mask: `voice[x].pw += rec6[…].b1894` for `timer_4` frames (jch.md:527-538), `cutoff_hi += rec7[…].b1860` for `timer_5` (jch.md:544-546). The bound is the register width; the *stream* ends the segment, not a compare |

`scope` is no enumeration the schema picks per family: it is read off the region
the value cell lives in, which S6 already recovers, and it is per **cell**, not
per Acc — Hubbard's pulse sweep keeps its value in the instrument record
(`ins.pw`, shared by two voices) while its direction and divider are per-voice
(`voice[v].pwdir`, `voice[v].pwdelay`, commando-floor:222-233). Global scope
occurs for the filter in three families (JCH and SW `cutoff_hi`, defMON
`filter.acc`).

`links` carries the cross-Acc effects a family's commands really have. GT2's
tone-portamento snap is the case: `p_1327` sets the note index *and* zeroes the
vibrato phase — `voice[x/7].freq_lo_idx_2 = a; voice[x/7].b14A0 = 0`
(gt2.md:798-801) — and the tick-0 handlers 1/2 zero `vibtime` the same way
(anatomy:876). Second family: JCH's re-trigger arm re-points the pulse cursor
and reloads the pw accumulator from the stream row in one step (jch.md:363-366).

Every per-frame modulation in the anatomy's row (anatomy:212) lands on one line,
each with two certified families or a marked single-family exception:

| effect | Acc | evidence |
| --- | --- | --- |
| vibrato (triangle) | **two coupled Accs**: a phase Acc `delta const(+2)`, `bound [0, speedcmp] proved`, `policy reflect-complement`; and a freq Acc whose `phase` is `acc(phase_id)` bit 0 and whose `delta` is `tablestep` or `const` | GT2: `voice[x/7].b14A0 = (a + 2) + c`, `t4 = b14A0 & 1`, then `ghost.freq += ptr` or `-= ptr` (gt2.md:852-862); the bound is the SMC cell `b1096 = T1851[y] & $7F` (gt2.md:812 — speedcmp, **not** the depth) and the complement is `a57 = ~b14A0` (gt2.md:835); `ptr` is either the 16-bit const `(T1851[y] << 8) \| T1863[y]` or `tablestep(FREQ, freq_lo_idx_2, T1863[y])` through the variable-shift loop `p_12E5` (gt2.md:653-684). JCH: the same two-cell shape on its slide/vibrato (jch:82) |
| vibrato, stateless phase | one freq producer, `delta repeat(tablestep(FREQ, note, ins.vib + 1), n)`, `phase fn(global_counter)` | **single-family exception (Hubbard)**: `phase = counter & 7; if phase >= 4: phase ^= 7`, then `for _ in 0..phase-1: f += step` (commando-floor:215-221). It is the closed form of the triangle every other family accumulates, not a new mechanism; admitted because Hubbard is §9's certified non-tracker exemplar and nothing else makes its `freq` exact |
| tone portamento | target freq, `policy clamp(pitch[target])`, `delta const`, `links [reset(vibrato phase)]` | GT2 `p_10AB` case 3: the 16-bit compare chain against `FREQ[freq_lo_idx]`, snapping in `p_1327` (gt2.md:798-801). JCH's slide is the same shape with the compare on its own target |
| free slide | target freq, `policy halt` or `wrap` at width, `delta field(cell, mask)`, `phase bit(cell, 0)` | Hubbard: `d = voice[v].porta & $7E; freq += -d if porta & 1 else d` — a free ±step ramp with **no target**, so this row and not the portamento row (commando-floor:236-238). JCH slide acc (jch:82) |
| pulse sweep (bounce) | target pw, `policy reflect`, `bound [$8xx, $Exx] proved`, `rate` a divider, `phase cell != 0` | Hubbard: `pw += d` until `pw_hi == $E`, down until `$8`; `pwdir` the phase, `pwdelay` the divider, `ins.pw` the instrument-scoped value (commando-floor:222-233). JCH `rec6` segments, direction column `& $80` (jch.md:527) |
| pulse run (unbounded) | target pw, `delta const(k) + carry(site)`, `bound` **`projected`** at 12 bits | Hubbard: an **8-bit** add on `pw_lo` with the carry **live from the vibrato block** — `ins.pw_lo += ins.pspeed + C  # C inherited from $51FA` (commando-floor:222-224, `+ carry` at commando.md:394); the 12 bits come from the store's `& $F` (commando.md:380). defMON: `voice[v].pw_lo -= (b101E + (1 - carry_2))` with `carry_2` produced by the freq add above it (automatas.md:427-447). These are the writes that make both Commando subtunes aperiodic (architecture §5.2), rendered exactly, aperiodicity included |
| filter sweep | target `split(3, 8)` on cutoff, `delta tabcell(T[c], signed 11)`, `bound observed` | SW: the filter program's step byte is a signed 11-bit delta — `cutoff_lo = ((t3 & 7) + cutoff_lo) & 7` with the carry out, `cutoff_hi += (t3 >> 3) + carry`, the negative arm's shift arithmetic as `~(~t3 >> 3)` (sw.md:868-885, joined in `p_1611`). JCH `rec7` segments and defMON's `filter.acc` write the high half only, the same split with the low half pinned (jch.md:654, automatas.md:420) — and the split is the *chip's*, already `grid.PAIRS[6]`, not a family's |
| keyboard tracking | `tabcell(T[c])` on the cutoff target | SW `CKBDTRK` (§3.7, sw:110-116); defMON's oscillator uses the same form on freq, `voice[v].acc += FREQ[$80 + (pw_hi[v] << 1)]` with the sign from `bit(cell, 7)` (automatas.md:433-437) |
| arpeggio / chord | target note, a `pitch` stream, or an absolute producer where the phase is stateless | Hubbard octave arp: `f = FREQ[note + ($C if counter & 1 else 0)]` — an **absolute `set` producer** (§4), `phase fn(global_counter)` (commando-floor:249-251). GT2 wavetable note column (gt2.md:564-569); SW chords |
| tremolo, LFOs | target **gate-mask**, `policy reflect` (triangle) or `halt` (one-shot), or a stream | Walker's gate-toggle tremolo and its four identical modulators per voice (anatomy:212) move the ctrl gate bit, not a volume. `$D418` is one global register, so `target vol, scope voice` does not exist and is removed; per-pattern volume is `set_vol` (§3.6), global, last-writer. Prose-only family, so both are projections |

Two first-draft rows are struck. **Skydive** is dead in the only family that has
it — `if ins.fx & 2 and (row & $1F) >= 3: trap 'untaken'` (commando-floor:247) —
so there is no observation to fit. **Piecewise envelopes** are not a row: they
are streams of `acc` segments (§3.3), the stream sequencing and the accumulator
moving. Nothing else moves a shadow between rows — that is the discipline.

---

## 6. The lift, T0–T3

Input: a certified tuneprog — `tuneprog.S4.json` (the program),
`tuneprog.S6.json` (the naming plane: roles `freq_table`, `cursor`, `timer`,
`acc`, `sid_image`, `voice_map`; views; u16 pairs; the `index` relation),
`tuneprog.T0.json` (per-write-site provenance) and `certificate.json`. The lift
consumes the *certified* object, never the trace or the binary: family knowledge
may steer extraction but can never reach the output, which renders on §4 alone.

| stage | in | out | mechanism |
| --- | --- | --- | --- |
| **T0 channels** | S4 IR + names | per-register provenance | **landed** (§7): `provenance.document` writes `tuneprog.T0.json`, one record per SID write site — register, voices, the expression over named cells, its leaves, the site, the printed line |
| **T1 accumulators** | T0 + `history.py` | `Acc` set | a `state` cell whose update matches a §5 `delta` and whose guards, masks or history give a `bound` with its `from` tag. Not `ranges.py` and not `gated.py`: `ranges.expr_range` bails to width on any self-referential `+`/`-` (`ranges.py:44-49`) and `gated.diamonds` needs one same-name `Let` per arm (`gated.py:34-37`), which no reflect site in GT2/Commando/JCH/SW has. `facts.update_role` (`facts.py:288`) is the seed; the rest is new |
| **T2 grammars** | the S6 `index` relation + histories | streams, patterns, orderlists, pitch table | a `cursor`'s observed successor relation (step +1 runs, jump targets, the `$FF`-terminator reloads) delimits its table's rows and loop row; the two-level cursor nest (row cursor over a pattern table indexed through an orderlist cursor) is the score; `freq_table` regions are `pitch` |
| **T3 emit + certify** | all | `trackerprog.json`, `trackerprog.md`, `trackerprog.certificate.json` | render on the universal player tick-for-tick against `Verifier.obs` over the whole certified horizon, §2 observable; any residue → `Refusal`, nothing emitted |

T2's materialisation rule: the trackerprog represents the score the trace
played. Storage idioms — Blackbird's LZ stream and ring buffers, packed rests,
1-based columns, interleaving, Follin's `$85` byte lists — are dropped by
materialising the decoded rows over the horizon, which is `period` for a
`complete` source with `period > 1`, `first_repeat` for a `period = 1` source
(the tune ended; §2), and the certified tick count for a `horizon` source.

The note space is `0..N-1` where `N` is the trace's reach; there is no
`clamp(note)` rule. A read past a **const** table's declared size extends
`pitch` with the values read; a read landing on a **play-written** cell is not a
pitch entry at all. Commando song 1 plays pitch 104 twenty-five times, `$5428 +
2*104` is `voice[0].ctrl` / `voice[1].ctrl`, and the arpeggio's `+12` reaches
the two `pwdir` bytes (commando-floor:295-310), so that starting frequency lifts
as an absolute `set` producer over `field(cell)` (§4, §5) — named, not
materialised as pitch. The first draft's "reads two bytes past the table" was
wrong on both the distance and the kind.

---

## 7. What landed, and what remains

Nothing in the front end changed: the IR, the tracer and S8 are untouched and
the trackerprog consumes certified artefacts. Four of the five enabling packages
have merged.

| item | what landed | modules / artefacts |
| --- | --- | --- |
| the grid as a first-class comparison (**#291**) | `grid.regs`, `grid.changes`, `grid.reduce_tick` → `TickObs(edges, values)`, `grid.reduce_run`; constants `CTRL/AD/SR/EDGE/PAIRS/LEVEL/PW_HI`; `ghidra_facts._tick_writes` is now a filter plus `grid.changes`; `Verifier(obs=True)` accumulates one `TickObs` per verified tick (`verify.py:165,286,350`). `verify._compare` stays raw — mirror folding, the PW nibble and the cutoff mask do not reach it | `grid`, `verify`, `ghidra_facts` |
| cell histories without touching S1 (**#292**) | `history.cells`, `history.history`, `history.widen_u16`, `History`, over the verifier's own ticks (`Verifier._one` promoted to `tick()`), `np.frombuffer(M.m)` at a fixed index, sparse strides off `Region.addrs`; `tools/tuneprog_history.py` writes `tuneprog.history.npz`. A library and a tool, not a pipeline artefact | `history`, `verify` |
| the S6 exports T2 needs (**#293**) | `facts.idxbase`/`cellsrc`/`leaf_reads` and one cell key put record fields into `cellindex`; `facts.cursor_cells` is one cursor rule for scalars, fold slots and split fields alike; `recover.index_relation` serialises the relation as `tuneprog.S6.json`'s **`index`** block; `Names.from_dict` reads the whole document back. Score cursors now carry the role — GT2 `rec[x/7].cursor`/`.cursor_2`/`.cursor_3`, JCH `voice_3[v].cursor`, SW `rec` `+0`…`+6` | `facts`, `recover`, `views` |
| per-register provenance, T0 (**#294**) | `provenance.py` writes **`tuneprog.T0.json`** beside S6: `{plane, voice_map, image, writes}`, one record per SID write site. Roots are `io` stores whose envelope lies in `$D400..$D418` plus stores into a `sid_image` region rekeyed by the flush delta; `provenance.regvoices` reads the register off the site's base and the voices off its envelope; `expr` substitutes names stopping at every cell S6 names, serialised with `ir.enc` (`R16`/`W16` added to `_NODES`, `W16` gaining `env`); each record's `print` is the `tuneprog.md` line itself | `provenance`, `ir`, `pipeline` |

Measured over the 51 recert programs: **849 write sites, 849 prints re-rendering
to their own line, 0 sites both unnamed and unrefused**; the 40 refusals are 36
`index not a voice` (Follin's `$85` cross-voice list, JCH's non-constant clear)
and 4 `smc target`. Replay cost from #292, ticks / cells / seconds:
`gt2-je-suis-linus` 12,000 / 120 / 5.4, `jch-guldkorn-intro` 4,000 / 146 / 1.6,
`sw-emomyst` 12,000 / 129 / 8.0, `commando-song1` 11,780 / 206 / 3.4.

**What remains before T1** is `accum.py` (backlog §4, W5): the `(delta, bound,
policy, phase)` classifier of §5 over `facts.cellupd` candidates reaching a T0
write site; a `Delta`/`Dir` parser; a diamond over `Store`/`Call` arms; the
variable-shift loop recogniser `loops.py` lacks (GT2 `p_12E5`, gt2.md:665-680);
a guard walk over dominators for `policy`; and two verifiers, an interval
assertion and a recurrence replay against `history.py`, divergence ⇒
`unclassified update`. Everything after it is the new `trackerprog/` package
beside `tuneprog/`, under the same rules (≤ 500 lines per module, hermetic
tests, the certificate).

## 8. Refusals and boundaries

Fail-closed, diagnosed, in the tuneprog refusal style — a trackerprog with a
residue is not emitted:

| reason | when |
| --- | --- |
| `sample stream` | a CIA #2 NMI sample mixer or `$D418` nibble stream (the *Easy Does It* mixer): digis are not a score |
| `external input` | see the rule below |
| `unclassified update` | a state cell reaching a SID register whose update T1 cannot bound — the accumulator invariant is the claim, so an unbounded or unmodelled data-dependent update refuses |
| `score not cursor-shaped` | a pattern fetch T2 cannot express as the cursor grammar (a genuinely computed score) |
| `command residue` | a pattern command not expressible in §3.6's set, `set_register` with a non-literal register index included |

**The `external input` rule**, restated so it does not refuse the arithmetic. A
pinned input refuses only when *all three* hold: its `tracedata.input_kind`
(architecture §1, line 45) is `raster`, `cia`, `sid_readback` or `io`; its
recorded values are not constant over the horizon; and T0's provenance shows it
reaching a SID write or a score cursor. `ack`, `entry_reg` and `uninit_ram` are
never external — an ack's value is discarded, `entry_reg` is the caller's
register at the entry, and the power-on pattern is part of the image
(architecture §9.3, point 3). That distinguishes an ack from an input, and it is
why Commando's 11,780 pinned reads — one `entry_reg` read per tick at `$5015`
(commando.md:198-202) — do **not** refuse. Without the rule the effects-rich
exemplar §9 accepts seventh would be unreachable on a technicality.

Boundaries stated, not hidden: cross-class intra-tick write order and voice
order (§2, with the license and the certificate's `dropped` list); cycle
positions inside a tick (architecture §8.3); and the trackerprog is *a* preimage
— many render the same grid, so round-tripping to a source *format* (a `.sng`,
an `.swm`) is a separate family-specific exporter, out of this layer by
construction.

## 9. Acceptance

Exemplars, in order: **GT2 ×2, JCH ×2, SW ×2** (the tracker end — all six
`complete`), then **defMON ×2**, then **Commando ×2** (effects-rich,
non-tracker, aperiodic observable), then **Follin** (score-as-program).

defMON belongs in the list, not in the deferred set: certified twice over —
`automatas` (149,025 ticks, period 129,024, `complete`) and `goto80-jazzpjazz`
(1,799 ticks, `horizon`), architecture §9.2 — with its own prototype document,
and the evidence §3.3, §3.5 and §5 lean on for the general stream form, the
data-side prelude and the second family for `carry(site)`. Two costs:
`automatas` needs `--budget`/`--resume` like every long tool (architecture §11),
and `goto80-jazzpjazz` being `horizon` exercises that terminator, not the loop
claim. Per exemplar:

| # | acceptance |
| --- | --- |
| 1 | `trackerprog.certificate.json`: 0 divergences over the whole certified horizon on the §2 observable, `compared` and `dropped` both populated, the loop claim re-verified where `end.kind = loop` |
| 2 | every refusal named with its cell — no partial emit |
| 3 | the print measured with **§6.2's six numbers** — tokens, lines, statements, blocks, header rows, data rows, which architecture §11 requires verbatim of every presentation change — plus **one extra**, `xz -9e` of `trackerprog.md` against the source `tuneprog.md`'s. `xz` is §8.3's own unit and no substitute for the six. The layer's claim is that the score compresses *better* than the program that played it |
| 4 | recert untouched: 51/51, no tuneprog artefact moves |

The genericity gate: the six tracker exemplars must lift with zero
family-conditioned code in `trackerprog/` — the same modules, hermetic snippet
tests per mechanism, each schema row's two-family evidence recorded here. The
two single-family rows (§5's stateless-phase vibrato, §3.6's Follin
`set_register`) are data forms, not code branches.

## 10. Open

| question | state |
| --- | --- |
| multispeed scaling | `rate` is now one thing, a divider (§3.3), so a sequencer running at frame rate under an n× entry is `rate = n` on that voice's tempo. Still to be *measured* on a used multispeed entry; SW 1.9 carries an unused one |
| `sext` as a delta | `sext(k, T[c])` appears in the IR only as a jump offset (`switch ($1953 + sext(T1934[a]))`, sw.md:1205). The one accumulator delta that sign-extends is SW's filter step, and it lifts as `tabcell(T[c], signed 11)` (§5). If an exemplar shows a sign-extended table entry that is *not* an absolute table cell, `delta` gains a form; until then it does not |
| global-scope accumulators beyond the filter | a survey question, not a schema one: `scope` is read from the value cell's region (§5) |
| the second entry | a tune whose NMI is a second *musical* entry (not a mixer) has two tick clocks; the schema has one cadence with per-voice dividers over it. Refuse until an exemplar demands otherwise |
| the SW orderlist fold | T2's blocker, not the schema's: SW's orderlist load is erased by the copy fold (`p_17C8` prints nothing, `T1C40`/`T1C4E`/`T1C5C` have no accessors), so either `copyview` keeps the load or SW's score refuses (backlog §4, W6) |

Settled since the first draft and dropped from this list: note-space clamping
(§6 — the note space is the trace's reach, and Commando's overrun is a producer,
not a pitch entry) and instrument-scoped accumulator sharing (§5 — `scope` is
read off the region, per cell).
