# Song-synthesis model recovery

Recover *how each SID register is driven over time* from the eqlift pass-1 graph,
one layer above `eqlift_annotate` (which recovers *which table feeds each
register*). Every algorithm below reuses `eqlift_annotate` machinery:
`model_procs`, `_const_base`, `_role_of`, `_backtrace` (env+cells dataflow),
`table_roles`, `et_check`/`_pitch_tables`. See `song_model.py` for the PoC
implementing items 1 and 2.

Evidence is cited by line in `out/Commando.eqlift.txt` (Hubbard),
`out/Ghouls_n_Ghosts.eqlift.txt` (Follin), `out/Krakout.eqlift.txt` (Daglish).

## Shared vocabulary (as lifted)

- Per-voice **voice-offset cell**: Commando `m_54EB = m_54E8[x]` with
  `m_54E8[3] = 00 07 0E` (Commando lines 311, 73) — the `7*v` SID register stride;
  all `sid.v1.*[w3]` stores index by it, so `_role_of(_const_base)` still resolves
  the base register.
- **Counter/index/position cells** carry recovered prefixes: `ctr_*` (frame
  counters), `pos_*` (sequence positions), `idx_*` (table indices).
- **Voice iteration**: Commando one `x=$02..0` down-loop (3 voices, line 290);
  Follin/Daglish unroll or index the three voices (Ghouls v1/v2/v3 blocks;
  Krakout `x=$00..2` at line 245).
- **Streams**: `via ptr_XXXX_lo cmp …` blocks are the note/pattern byte streams
  walked by a self-modified 16-bit pointer.

## 1. Song cadence / tempo

**Algorithm.** A *cadence counter* is a RAM cell `C` (base ≥ 2, outside
`$D400..$D41C`) with a store whose value is `INT_ADD(read(C), $FF)` /
`INT_SUB(read(C), 1)` (decrement) or `INT_ADD(read(C), 1)` (increment), operand
resolved through the reaching-def env (`_resolve`). Pair each `C` with its
*reload source*: any other store to `C` whose value resolves to `mem[const]`
(the speed/duration cell) — that source is the reload constant/cell. Classify:
a **decrement+reload** cell is a tick/duration divider (frames-per-tick =
reload); a **free-running increment** cell is the vibrato/arp LFO phase. The
note-advance condition per voice is the branch guarded by the duration cell
reaching zero.

**Commando (observed).** Global tick divider `ctr_5513`: `ctr_5513 = ($FF + ctr0)`
then reload `ctr_5513 = m_5517` on underflow (lines 303–309); the branch
`if (ctr_5513 != m_5517)` (line 312) routes non-tick frames to per-frame
modulation (TRUE, lines 313–497) and the one tick frame to the sequencer (FALSE,
lines 498–591). So **frames-per-tick = `m_5517`** (a single global cell → global
tempo). Per-voice note length: `ctr_54F2[x]` decrements in the sequencer
(lines 501–503) and reloads from the stream byte low bits
`ctr_54F2[x] = ($1F & w23)` (line 521) — **ticks-per-note = streambyte & $1F**.
Sequence positions `pos_54EC[x]`/`pos_54EF[x]` advance on note fetch
(lines 526, 536, 572). Free-running phase `ctr_5525 = (ctr_5525 + $01)` every
frame (line 280), reset only on song restart (line 287); consumed as
`ctr_5525 & $07` (arp/vibrato phase) and `& $01` (octave toggle).

**Generality.** Ghouls (Follin) has **no global divider**: each voice owns a
per-frame note-length counter `ctr_0027`/`ctr_0028`/`ctr_0029`
(`ctr_0027 = ($FF + ctr_0027)`, line 577) that reloads from the note-length byte
`m_640F` and triggers the command interpreter when it hits `$01` (line 590,
reload line 657); tempo is entirely the per-note duration bytes. A separate
`zp_90` compare (line 578) sets the gate-off lead time. Krakout (Daglish) uses a
**16-bit duration** `m_E5CF:m_E615` (borrow-chained decrement, lines 704–712)
plus a **fractional tempo accumulator** `m_E69B += m_E59C`, carry into
`m_E69C += m_E59D` (lines 240–243) — a phase-accumulator tempo. All three expose
their counters to the algorithm; only the *interpretation* (global vs per-voice
vs fractional) differs and is read off the reload topology.

**Schema.** Per counter: `{cell, kind: dec|inc, reload: cell|const|null,
role: tick_divider|note_length|lfo_phase|other}`; per voice:
`{note_length_cell, advance_condition, frames_per_tick}`.

## 2. Frequency modulation functions

**Algorithm.** For every store to `freq_lo`/`freq_hi` (base `$D400`/`$D401 + 7v`),
resolve the value's env root and `_backtrace` its table set:
- root is a direct `mem[T + idx]` with `T` inside a confirmed pitch-table span
  (`et_check`) → **note lookup**; if `idx` derives from the LFO-phase counter
  (item 1) → **arpeggio**.
- root reads a per-voice cell that has a self `INT_ADD`/`INT_SUB` update store
  (an *accumulator*), or is itself an inline `INT_ADD`/`INT_SUB` of a delta cell
  → **slide/vibrato**; sub-classify by the sign source: constant signed step =
  portamento, table/counter-indexed delta = vibrato.

**Commando (observed).**
- Note lookup: `idx_5503 = m_5428[y]; freq_hi = m_5429[y]` with `y = y1<<1` over
  the stride-2 ET table `m_5428[192]` (lines 543–549, 493–496; table + pitch
  verdict line 59).
- Arpeggio (`m_5523 & $04`): index toggles `idx_54FB[x]` vs `+$0C` by
  `ctr_5525 & $01`, re-looked-up in `m_5428` each frame (lines 485–497).
- Portamento/slide: control byte `m_5520[x]`; `m_551D[x]` (freq_lo) and
  `ctr_551A[x]` (freq_hi) form a signed 16-bit accumulator, `+= (m_5520 & $7E)`
  or `-=` selected by bit 0 (lines 423–446).
- Downward pitch-bend (`m_5523 & $01`): `ctr_551A[x]` decremented while the
  note-length gate `ctr_54F2[x] <= (m_54F5[x] & $1F) - 1` holds (lines 447–472).
- Vibrato/detune triangle: `a = ctr_5525 & $07`, reflected `a ^ $07` for `a>=4`
  → `idx_550C` (0..3..0 over 8 frames, lines 329–334), driving the 16-bit
  add/shift loop feeding `idx_550A`/`idx_550B` → freq (lines 344–377).

The flag byte `m_5523 = mem[t1 + $5598]` (line 322) selects which modulators run
via bits `$01/$02/$04/$08`.

**Generality.** Ghouls computes vibrato **inline**: `sid.v1.freq_lo = a` where
`a = a0 ± zp_8A` with direction `m_6269` toggled `(y ^ $FF)` and half-period
`ctr_0084`/onset `ctr_007E` (lines 448–477); index-domain portamento steps
`idx_0066` by `idx_0063` toward target `idx_006C`, re-looked-up in `m_6D35`/
`m_6D96` (lines 491–521). Krakout writes freq only via a **SID shadow buffer**
`sid.v1.freq_lo[x] = m_E686[x]` flushed for all 25 registers (lines 807–817); the
real pitch ROM sits behind a **computed base** (`… - $19D7`, `sub_E536` line 833;
`… - $19BC`, line 274), so the constant-base backtrace finds no pitch table and
every freq store classifies as `other` — the documented provenance boundary.

**Schema.** Per voice: `{base: pitch_table, modulators: [{op:
note|arp|portamento|vibrato|bend, param cells/tables, phase_source}]}`.

## 3. Pulse-width modulation

**Algorithm.** Backtrace stores to `pw_lo`/`pw_hi` (`$D402`/`$D403 + 7v`). A
counter-gated `INT_ADD`/`INT_SUB` of a step onto a per-voice pw accumulator, with
a direction cell toggled at high-nibble bounds, is a **triangle sweep**
(rate = the gating counter's reload, step = the added constant/mask, bounds = the
compared constants). A direct `mem[T]` is a **table sweep**.

**Commando (observed).** `m_5523 & $08` clear → ramp: rate counter
`ctr_550D[x]` reloads from `idx_5507 & $1F` (lines 384–389), direction
`ctr_5510[x]` chooses subtract `(a & $E0)` with turnaround when the pw high
nibble reaches `$08` (lines 391–399) vs add with turnaround at `$0E`
(lines 400–406); result writes `m_5591[y]`/`m_5592[y]` → `pw_lo`/`pw_hi`
(lines 411, 413). Bit set → single `m_5591[y] = a + w12 + cflag` add (lines 417–421).
The per-instrument seed lives in the stride-8 block `m_5591[104]` (line 74).

**Generality.** Ghouls sweeps 16-bit pw `zp_3F:zp_40 += zp_4B` bounded at high
nibble `$0F` and low `$9B` up / `$64` down, counter `ctr_62EE`, params seeded at
note-on from `m_63D4`/`zp_45`/`zp_46` (lines 528–572); same triangle shape,
different bound encoding. Krakout PW rides the shadow buffer (`m_E688`/`m_E689`,
lines 686–695) — recoverable structure, table provenance defeated as in item 2.

**Schema.** Per voice: `{pw_accumulator: {lo, hi}, rate_counter, step, bounds:
[lo, hi], direction_cell}`.

## 4. Control ($D404) transition drivers

**Algorithm.** For each store to `control` (`$D404 + 7v`) record its guard and
value provenance. `value & $FE` / `value | $01` masks are gate-off / gate-on;
`mem[T]` provenance is the waveform table; guards are the branch conditions
(counter-zero, flag-bit tests) reaching the store.

**Commando (observed).** Note-on: `sid.v1.ctrl[y] = (m_5593[x] & ctr_5501)`
(line 559) — waveform byte `m_5593` (stride-8 instrument block, `role control`)
ANDed with the gate mask `ctr_5501` (`$FF` or `$FE`, set from the tie flag
`idx_5502 & $40`, lines 517–524). Note-off:
`sid.v1.ctrl[y] = ($FE & m_54F8[x])` with AD/SR zeroed, guarded by
`(m_54F5[x] & $20) == 0` and `w22 == 1` (lines 580–587). Mid-note (`m_5523 & $01`):
`sid.v1.ctrl[y] = a` where `a = $80` (test bit) or `m_54F8[x] & $FE`
(lines 458–472). Triggers are the flag bits of `m_5523` (`& $01/$02/$04/$08`,
lines 379, 447, 476, 485).

**Generality.** Ghouls drives control from the **jump-table command interpreter**
(`goto (m_6C37[a] | …)`, line 674): command `$693F` sets waveform
`sid.v1.ctrl = w12; zp_2A = w12` and seeds PW-sign from `(w12 << 1) < 0`
(lines 746–759); gate-on `sid.v1.ctrl = (a | $01)` (line 649), gate-off
`sid.v1.ctrl = (zp_2A & $FE)` when remaining duration `<= zp_90` (lines 578–587).
Krakout writes control via the shadow byte `m_E68A` masked `$FE`
(lines 785–787) then the bulk flush. Guard/mask recovery is uniform; only the
dispatch shape (flag byte vs jump table) differs.

**Schema.** Per voice: `{gate_on: {guard, waveform_source}, gate_off: {guard,
mask}, waveform_table}`.

## 5. AD/SR ($D405/$D406) transition drivers

**Algorithm.** Backtrace stores to `attack_decay`/`sustain_release`
(`$D405`/`$D406 + 7v`) to the selecting index and the store guard. Per-instrument
selection appears as `mem[block + k*stride]` indexed by the note-on instrument
number; mid-note rewrites appear as stores guarded by a running counter.

**Commando (observed).** Selected at note-on from the stride-8 instrument block:
`sid.v1.attack_decay[y] = m_5594[x]`, `sid.v1.sustain_release[y] = m_5595[x]`
with `x = idx << 3` (lines 562–563); zeroed at note-off with the gate clear
(lines 585–586). **No mid-note envelope rewrite is observed** — AD/SR are static
per note.

**Generality.** Ghouls has **no dedicated AD/SR store**: envelope bytes are
emitted by the generic register-poke command `$6909`
(`sid.v1.freq_lo[a] = w11`, register index `a` from the stream, lines 729–744),
so the constant-base backtrace cannot attribute an AD/SR *table* — the values are
stream immediates. Krakout stages AD/SR in shadow bytes `m_E68B`/`m_E68C`
(lines 730–732) before the bulk flush. Boundary: dedicated per-instrument tables
(Commando) are fully recovered; data-driven register pokes (Follin) yield the
*when* but not a static table.

**Schema.** Per voice: `{ad_source, sr_source, select: note_on|counter, guard}`.

## 6. Filter ($D415–$D418) modulation and transition drivers

Filter is **song-global**, so its schema is one description, not per-voice.
`_role_of` already maps `$D415→cutoff_lo`, `$D416→cutoff_hi`, `$D417→res_filt`
(low nibble = voice routing, high nibble = resonance), `$D418→mode_vol` (low
nibble = master volume, high nibble = LP/BP/HP/3-off bits).

**Algorithm.** `_backtrace` the stores to `$D415`/`$D416`: a per-frame
`INT_SUB`/`INT_ADD` on a cutoff accumulator (item-3 shape) is a **cutoff sweep /
filter envelope** (rate = step, bounds = compared targets, onset = a gating
counter); a `mem[T]` is a table LFO. For `$D417`/`$D418`: if the only store is in
`sid-init` (constant), report **static** routing/resonance/mode/volume from that
constant; if written in `play`, classify like the cutoff.

**Observed.** Only **Ghouls** drives the filter, from the voice-3 slot. Params
loaded by command `$6A3F`: step `zp_73`, onset `m_65DA`, start `zp_6F`, `ctr_0070`,
targets `m_6819`/`m_6813` (lines 1116–1137), reseeded at note-on (lines 1272–1274).
Per frame: `zp_6F = (zp_6F - zp_73)` → `filter.cutoff_lo = zp_6F` (lines 1738–1755),
with `filter.cutoff_hi` assembled from `ctr_0070` rotations plus the accumulator's
high bits (line 1757), bounded against `m_6813`/`m_6819` — a downward per-note
**cutoff envelope**. Ghouls writes **no** `$D417`/`$D418` in `play`
(resonance/routing/volume static). **Commando leaves the filter unused**: no
`filter.*` store anywhere, only `$18 = $0F` once in `sid-init` (line 13) —
volume 15, filter off. **Krakout** never writes `$D415–$D418` explicitly; its
25-register shadow flush (lines 807–817) copies `m_E686[$15..$18]` into the
filter registers, so any filter state is data behind the same computed-pointer
boundary as items 2–5 and is not attributable to a filter-specific driver.

**Generality.** Cutoff-sweep recovery (accumulator + bounds + onset) is the same
graph pattern as PWM (item 3) and works wherever the sweep touches `$D415`/`$D416`
through constant-base cells (Ghouls). Bulk-shadow players (Krakout) expose that
the filter *is* written but not *how*. Tunes with a static filter (Commando)
report the init constants and "unused in play".

**Schema.** Global `{cutoff: {accumulator, step, bounds, onset_counter,
reseed_at_note_on} | table | static, resonance, routing_voices, mode, volume,
source_voice_slot}`.

## Consolidated output schema

```
song_model = {
  tempo:   {frames_per_tick, global|per_voice|fractional, reload_cells},
  voices:  [ { note_length_cell, advance_condition,
               freq:    {pitch_table, modulators:[…]},   # item 2
               pw:      {accumulator, rate, step, bounds},# item 3
               control: {gate_on, gate_off, waveform},    # item 4
               adsr:    {ad_source, sr_source, select} }  # item 5
           ],
  filter:  { cutoff, resonance, routing_voices, mode, volume }  # item 6, global
}
```

## PoC status

`song_model.py` implements items 1 and 2, plus a control/envelope **automaton**
covering items 4–5, reusing `eqlift_annotate` provenance:

- `recover(stmts, model)` / `analyze(model)` return
  `SongModel(counters=[Counter(base, kind, reload)],
   freq=[FreqDriver(role, source, pitch, slide, kind)],
   control=Automaton(states, transitions))`.
- Counters use the item-1 step/reload pattern; freq drivers use the item-2
  root+pitch classification (`note`/`slide`/`other`).
- **Automaton.** Each store to `control`/`attack_decay`/`sustain_release` becomes
  a `Transition(role, action, to, source, guards)`: the guard is the conjunction
  of enclosing branch conditions on the path (rendered, e.g. `(m_5523 & $01)`,
  `(m_54F5 & $20) == $00`), `action` classifies the `$D404` value
  (`gate_off` = mask clears bit 0, `gate_on` = OR bit 0, else `waveform`),
  `to` is the note-lifecycle state (`off`/`on`), and `source` is the
  backtraced waveform/AD/SR table base. States are `{off, on}`. This is items
  4–5 as an extracted finite automaton; the *when* (guards) and *what*
  (action + table) are recovered together. Commando yields 7 edges: note-on
  waveform under the `m_5523 & $01` flag bit, note-off `gate_off` under
  `(m_54F5 & $20)==$00 & (m_54F2==$00)`, and the note-on AD/SR selections.

Verified (`tests/test_song_model.py`, 200-frame decompile, no solver needed):

| tune | player | counters | freq kinds | notes |
|------|--------|----------|-----------|-------|
| Commando | Hubbard | 8 (incl. `ctr_5513` dec reload `m_5517`, `ctr_54F2` dec, `ctr_5525` inc) | note, slide | full recovery |
| Ghouls | Follin | 11 (per-voice `ctr_0027/28/29`, vibrato/filter counters, ZP) | note, slide, other | inline vibrato → `slide` |
| Krakout | Daglish | 5 (incl. 16-bit duration `m_E615`) | other | pitch ROM behind computed base → no `note` |

**Not yet in the PoC (documented above as future work):** sub-classifying
`slide` into portamento/vibrato/arpeggio by sign source (item 2); PWM and filter
recovery (items 3, 6), which reuse the same counter/backtrace and automaton
primitives; merging the per-store automaton edges into a fully minimised per-voice
state machine; and following Krakout's computed pitch base (would need
self-modified-pointer resolution, out of scope for constant-base provenance).
