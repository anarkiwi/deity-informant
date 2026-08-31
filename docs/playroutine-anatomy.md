# Anatomy of C64 playroutines — a field guide for decompiler writers

Nine players reverse engineered to the byte: Rob Hubbard (Commando, 1985),
Martin Galway (Comic Bakery, 1986), Tim Follin (Ghouls'n'Ghosts, 1989), Martin
Walker (Chameleon, 1990, 2× speed), JCH NewPlayer 20 with its 4-track sample
build (Easy Does It, 1991), GoatTracker 2 (v2.73 export), SID Wizard (Hermit's
own tunes, 1.6 and 1.9 exports), defMON (Goto80's Automatas, 8× multispeed),
lft's Blackbird (Quintessence, 2017 — an LZ-compressed score decompressed inside
the play call). Every claim below was checked against an
annotated, execution-counted disassembly of the tune named, against a per-call
log of the SID writes it produces, and, where source survives (Galway's own
`wizball.asm`, GoatTracker's `player.s`, SID Wizard's `player.asm`, the undefmon
reassembly of defMON, the playroutine listing in Appendix A of the Blackbird
User's Guide, and McSweeney's commented Hubbard disassembly), against
that source. Two blanket statements in this text — which players use illegal opcodes and
which read volatile hardware — were verified mechanically over every executed
and every statically reachable instruction of every exemplar, not inferred. This document
is self-contained.

Contents

1. The machine as a playroutine sees it
2. What every playroutine is
3. Nine players in depth
   3.1 Hubbard — Commando · 3.2 Galway — Comic Bakery · 3.3 GoatTracker 2 ·
   3.4 SID Wizard · 3.5 JCH NewPlayer 20 (+sample track) · 3.6 Follin —
   Ghouls'n'Ghosts · 3.7 defMON — Automatas · 3.8 Walker — Chameleon ·
   3.9 lft — Blackbird
4. The same machine nine ways — comparison
5. 6502 techniques catalogue, with the reasons behind them
6. What a decompiler must model, and how
7. Traps
8. Quick reference

---

## 1. The machine as a playroutine sees it

### 1.1 The 6510 in one page

Registers: A, X, Y (8 bit), S (stack pointer, page 1), P (flags N V - B D I Z C),
PC. There is no 16-bit register: every pointer lives in memory, and only two
addressing modes dereference one — `(zp,X)` (rare in players) and `(zp),Y`
(the pointer walk every sequencer uses). Everything else is absolute or
zero-page, optionally indexed by X or Y with an 8-bit index and no scaling.
Consequences that shape every player:

- A "struct" is addressed as `base+field,X` where X is the record number and
  fields are laid out as *separate arrays* (struct-of-arrays, stride 1), or as
  `base+field+7*n` unrolled per voice, or as `(zp),Y` after loading a record
  pointer. Which one you see is a time/space decision by the author (§5).
- A byte stream is walked with `LDA (ptr),Y ; INY` — Y is the cursor and is
  limited to 256 bytes per pointer, so patterns/tracks are ≤ 256 bytes or the
  pointer itself is bumped.
- Multiplication does not exist; record strides are powers of two (`ASL`), or
  sums of two shifts (n*6 = n*2 + n*4), or done by table lookup, or avoided by
  storing `n*stride` instead of `n`.
- Flags are values. `CMP` leaves C = (A ≥ operand); `BIT m` copies bits 7/6 of
  memory into N/V without touching A; `ASL/LSR/ROL/ROR/ADC/SBC` route data
  through C; `DEC/INC` set N/Z from the result and leave C alone. Authors pass
  arguments and results in flags across dozens of instructions and across
  `JSR`. A decompiler that treats flags as "condition of the previous compare"
  will be wrong.
- The stack is 256 bytes at $0100–$01FF; besides return addresses it is used as
  a scratch register file (`PHA … PLA`) and for computed jumps (`PHA PHA RTS`).
- Zero page ($00–$FF) is faster and smaller (2-byte instructions) and is the
  only place indirect pointers can live. $00/$01 are the CPU port ($01 selects
  ROM/RAM/IO banking; players set it to $35/$37 or leave it alone).
- Cycle counts: 2–7 per instruction; a PAL frame is 312 lines × 63 cycles =
  19656 cycles at 50.125 Hz. A player that takes 1000–3000 cycles per call is
  "cheap"; rastertime is measured in raster lines (63 cycles).

Illegal (undocumented) opcodes are load-bearing in modern hand-written players.
Of the nine analysed here, seven use none (verified both dynamically — no executed
instruction is illegal — and by a static walk of the unexecuted code); defMON
(§3.7) executes `SBX #imm` (X = (A & X) − imm, sets flags like CMP), `SAX abs`
(store A & X), `LAX zp` / `LAX (zp),Y` (load A and X), `ANC #imm` (AND, then
C = bit 7) and `ALR #imm` (AND then LSR); Blackbird (§3.9) executes `SBX #imm`
(nine sites, all of them the voice-loop step `X −= 7`), `LAX zp`, `LAX (zp),Y`
and — the sharpest case — `NOP #imm` (opcode $80, a two-byte no-op) placed so
that its *operand* is a one-byte instruction reached by a branch, giving two
overlapping instruction streams at $1146/$1147. Others seen in the wild: `DCP`,
`ISC`, `ARR`, `SLO`, `RLA`, `SRE`, `RRA`. A decompiler must decode all of them with
correct flag semantics — one that stops on `$AF` loses the rest of the routine,
one that treats `SBX` as `CMP` gets X wrong, and one that treats `$80` as a
one-byte `NOP` desynchronises.

### 1.2 Memory map relevant to players

| range | what | how players use it |
|---|---|---|
| $0000–$00FF | zero page | pointers for `(zp),Y`, hot counters; players use $02–$FF freely, some avoid $90–$FF (KERNAL) |
| $0100–$01FF | stack | return addresses; scratch |
| $0314/$0315 | KERNAL IRQ vector (CINV) | tunes that install their own IRQ with KERNAL enabled point it here; handler ends `JMP $EA31/$EA81` or restores A/X/Y and `RTI` |
| $FFFE/$FFFF | hardware IRQ vector | used when $01 banks the KERNAL out (RAM at $E000+) |
| $D000–$D02E | VIC-II | $D011/$D012 raster position and compare, $D019 IRQ ack, $D01A IRQ enable — raster-driven play, `BIT $D011` bit-7 waits |
| $D400–$D41C | SID | see 1.3; mirrored every $20 to $D7FF (some tunes write to mirrors, e.g. $D498) |
| $DC00–$DC0F, $DD00–$DD0F | CIA 1/2 | $DC04/05 timer A period, $DC0D ICR (read to ack), $DC0E control — timer-driven and multispeed play |
| $E000–$FFFF | KERNAL ROM or RAM | `JMP $EA31` (full KERNAL IRQ tail), `JMP $EA81` (just PLA/TAY/PLA/TAX/PLA/RTI) |

### 1.3 SID as the player's output device

25 write registers at $D400+; three identical 7-byte voice blocks then four
global registers. Everything a player computes ends as a store to one of these.

| offset | voice reg | bits |
|---|---|---|
| +0 / +1 | frequency lo/hi | 16-bit; f_out = value × clock/16777216 (PAL clock 985248 Hz → value ≈ 17.03 × Hz); one semitone = ×2^(1/12) |
| +2 / +3 | pulse width lo/hi | 12-bit (hi bits 4–7 unused); duty = value/4096 |
| +4 | control | bit0 GATE, 1 SYNC, 2 RING, 3 TEST, 4 triangle, 5 saw, 6 pulse, 7 noise |
| +5 | attack/decay | high nibble attack, low nibble decay (0..15 → 2 ms .. 8 s) |
| +6 | sustain/release | high nibble sustain level, low nibble release rate |
| $D415/16 | filter cutoff lo (3 bits) / hi (8 bits) | 11-bit |
| $D417 | resonance/routing | bits0–2 filter voice 1/2/3, bit3 external, bits4–7 resonance |
| $D418 | mode/volume | bits0–3 volume, 4 LP, 5 BP, 6 HP, 7 mute voice 3 |
| $D41B / $D41C | read-only: oscillator 3 output / envelope 3 output | players read them as random numbers or for effects |
| $D419/1A | paddles | read-only, irrelevant |

Semantics that drive player structure:

- **Gate.** Rising edge starts attack; falling edge starts release. Players
  therefore keep a *shadow* of the control byte and write "waveform|1" for
  note-on, "waveform&$FE" for note-off. Setting/clearing gate mid-frame more
  than once matters (edges are counted, not levels).
- **ADSR delay bug and hard restart.** The envelope rate counter is not reset by
  gate. If a note is started while the previous release counter is "past" the
  new attack's compare value, the attack is delayed (up to ~30 ms) or the note
  can effectively be lost. Tracker-era players do *hard restart*: some frames
  (1–2; the exact offset is a family-defining constant) before the next note
  they write gate off and AD/SR = a reset value ($0F00 here for JCH, GoatTracker
  and SID Wizard's defaults; $0000, $F800 elsewhere), and at note-on they may
  pulse the TEST bit ($08 in the control byte) to reset the oscillator. Of the
  nine: JCH, GoatTracker and SID Wizard implement it in code; Blackbird gets it
  free from its four-frame row pipeline (SR ← 0 and gate off two frames early,
  then gate on with ADSR = 0000 before the instrument's own AD/SR, §3.9.5);
  defMON leaves it
  to the data (a sidTAB row program does exactly the same writes); Hubbard,
  Galway, Follin and Walker do not do it at all (Hubbard cuts notes with SR=0,
  Galway pulses TEST at note-on, Walker retriggers the gate off/on inside one
  call).
- **Frequency is a table lookup.** Every player has a 96-entry (8 octaves × 12,
  or 7 × 12 + extras) table of 16-bit values, either as two 96-byte tables
  (lo/hi, indexed by note number) or interleaved (indexed by 2×note). Vibrato,
  portamento, slides and "skydives" are arithmetic on the looked-up value.
  Blackbird overlaps its two byte arrays by 15 bytes — eight octaves is a factor
  of 256, so `msb[k+96] = lsb[k]` — and reaches quarter-semitone resolution by
  summing two entries of the same array at fixed offsets (§3.9.4).
- **Registers are write-only.** No player reads back a SID register (except
  $D41B/$D41C). So all state is in RAM shadows; a decompiler can treat SID
  stores as pure outputs.
- **Write order matters only at the frame edge and for gate edges.** Within one
  play call, the last value written to a register is what the chip holds until
  the next call, *except* that a gate 1→0→1 or a TEST 1→0 sequence inside one
  call is a real event. GoatTracker offers two SID write orders for exactly this
  reason (§3.3).

### 1.4 How a player gets called

- **PSID convention.** The container gives `init` (called once with A = song
  number; X and Y carry no defined value, and one wrapper here stores X into a
  variable — junk a decompiler must not treat as a definition) and `play`
  (called once per frame with no arguments; may clobber everything). `play = 0` means
  the tune installs its own interrupt inside `init` — the decompiler must find
  the handler by watching writes to $0314/$0315 or $FFFE/$FFFF and the CIA/VIC
  enable registers, then treat that handler as `play` (with the IRQ frame:
  A/X/Y pushed by the KERNAL prologue if entered via $0314).
- **Frame vs tick.** A frame is one call. Most players divide frames by a *speed*
  (tempo) counter to get sequencer *ticks*; effects run every frame, the
  sequencer steps every tick. Hubbard, JCH and GoatTracker count "speed+1"
  frames per tick (the stored value is frames−1); SID Wizard's tempo is a
  frame count and comes from a small tempo *program*; Follin has no tempo at all
  (durations are frame counts); Galway maps a duration index through a table
  the song itself loads.
- **Multispeed.** Play is called 2×–8× per frame from a CIA timer; the player then
  divides its own frame counter, or runs effects at the higher rate and the
  sequencer at frame rate. From the decompiler's view it changes only how often
  the routine runs and how the tick counter is scaled.
- **Voice loop.** All nine players process 3 voices per call (JCH adds a 4th, non-SID track), either in a loop
  with X = voice (0..2 or 2..0, `DEX/INX` + branch, or `SBX #7` stepping the SID
  offset itself as Blackbird does) or unrolled three times
  with different absolute addresses. Some process the *sequencer* in a loop and
  the *SID write-out* unrolled, or vice versa (§4).

---

## 2. What every playroutine is

Strip away the idioms and each of the nine players is the same object:

```
STATE   : per-voice record  V[3]      (10–40 bytes each: cursors, counters, shadows)
          global record     G         (tick counter, tempo, filter, volume, flags)
TABLES  : song → tracks/orderlists → patterns/sequences → notes    (the score)
          instruments → wave/pulse/filter/arp/speed programs        (the sound)
          frequency table                                           (the tuning)
PLAY()  : G.tick--                                     ; tempo divider
          for v in voices:                             ; 3 or 4
             if new tick: sequencer_step(V[v])          ; consume note/command bytes, start note
             effects(V[v])                              ; run programs & modulations, update shadows
             write_sid(V[v])                            ; copy shadows to $D400+7v..
          write_global()                                ; filter, volume
```

The vocabulary (names vary; the concepts do not):

| concept | Hubbard | Galway | Follin | JCH | GoatTracker | SID Wizard | defMON | Walker | Blackbird |
|---|---|---|---|---|---|---|---|---|---|
| top level | song table (3 songs × 3 track ptrs) | tune table (6 × 3 sequence ptrs) + effect blocks | subtune → 3 track ptrs (+ SFX lists) | subtune header (4 track ptrs, speed) | song → 3 orderlist ptrs | subtune (3 orderlist offsets, tempos) | arranger rows (3 pattern nrs/row, `$FF` jump); subtune = start row | song table (5 songs: len, block list) | none: one LZ-compressed stream, three interleaved voice token streams, `$FC` command = jump |
| per-voice list of blocks | track: pattern numbers, $FF loop / $FE stop | none — the sequence *is* the program (call/jmp/for-next) | none — one byte stream with call/loop/jmp | track: [transpose] pattern, $FF/$FE | orderlist: pattern, repeat, transpose, loop | orderlist: pattern, transpose, volume, tempo, stop, loop | the arranger column | the song's block list (shared by 3 voices) | none: the stream *is* the list; a 256-byte ring buffer per voice is the only window |
| block of notes | pattern: (len+flags,[instr\|porta],pitch)* $FF | sequence: `note dur` + commands | track: notes [+len] + commands | pattern: dur/instr/super/note/rest/hold, $7F | pattern: [instr][fx] note/rest/keyoff/on / packed rest, $00 | pattern: 1–4-byte rows, packed rest, $FF len | pattern: rows `flag [A] [B] [note]`, flag = END/sidcallA/sidcallB/note + duration | block: 16-byte header (instr/gate/filter per voice) + 3 tracks of L *keyboard characters* | rows of `[oob][effect][instrument] note\|delay`, class by byte range, one class read per frame |
| sound definition | 8-byte SID image + fx bits | 29-byte record (FM/PM segments, wave, ADSR, gate/release) | commands latch state (`$85` raw pokes = ADSR) | 8 bytes + wave/pulse/filter pointers | 9 columns + table pointers, gate timer, first wave | 16-byte header + inline WF/PW/filter tables | none: sidTAB rows (variable-length register-column records with delay + jump) are the instrument | 30-byte instrument (ADSR, ctrl, PW, transpose, detune, filter, 4 modulator param sets, delay, tie/retrigger); 7-byte drums | 4 parallel 1-based columns: AD, SR, wave-program offset, filter-program offset |
| per-frame modulation | vibrato, pulse, porta, drum, skydive, arp | FM ramp/arp, PM ramp, gate & release timers | vibrato/slide, trill, porta, pulse bounce, blip, filter bounce | wave table, pulse/filter programs, slide, vibrato | wave/pulse/filter/speed tables, 5 effects | WF/PW/filter tables, chords, vibrato types, slide/porta, kb-tracking | sidTAB row programs at up to 8×/frame (two cascades per voice), slide acc, pulse bounce, filter acc | 4 identical triangle/one-shot LFOs per voice (pitch, pulse, pitch-2, filter) at 2 calls/frame, gate-toggle tremolo | pitch program in quarter semitones, wave program (control byte + pulse step), global filter program; all three step once per frame |
| tempo | speed/song; tick = speed+1 frames | song-loaded duration table; raw frames | none: durations are frames | speed; step = speed+1 frames | tempo/channel; row = tempo+1 frames | tempo program; 3-phase tick | CIA 8×/frame; row = (d+2) main ticks; sidTAB row = DL+1 calls | CIA 2×/frame; tick every 9 calls | row timer counts down by 7 per frame; row = tempo/7 + 1 frames; swing by `EOR` on the reload |
| hard restart | none | none (TEST pulse) | none | 2 frames early, `$09` on note frame | gatetimer frames early, firstwave with TEST | tick 0/1 HR ADSR, tick 2 TEST wave | a sidTAB row program (WGx=00 AD=0F SR=00 → WGx=09 → sound) | none (gate 1→0→1 in one call + fresh ADSR) | free from the pipeline: SR=0 + gate off 2 frames early, then gate on with ADSR=0000 and the real AD/SR |

The decompiler's job is therefore three recoveries: (1) the STATE layout — which
memory bytes are per-voice fields and which are global; (2) the TABLES — which
byte ranges are score, sound, tuning, and their grammars; (3) PLAY as a
structured procedure over (1) and (2). Everything in §5–§6 serves those three.

## 3. Nine players in depth

Each subsection has the same shape: identity → memory/state → entry points →
the play routine as pseudocode → data grammars → SID write schedule → what
the apparent complexity reduces to. Addresses are those of the tune named.

### 3.1 Rob Hubbard — Commando (1985)


#### 3.1.0 Identity

- Tune: `MUSICIANS/H/Hubbard_Rob/Commando.sid`, PSID, load $5000–$5FC6 (4039 bytes), init $5FB2, play $5012, 19 subtunes = 3 songs (0–2) + 16 sound effects (3–18). Play is called per frame by the PSID host (the rip also carries the game's original IRQ installer at $5F6A/$5F96, unused). Single speed (1 call/frame); song tempo is a divider inside the player.
- Code: $5000–$5427 (main), $5531–$5590 (sfx init), $5F0C–$5FC6 (API tails, PSID wrapper) ≈ 1.3 KB reachable; the rest is data. Executed instruction sites: 548 (all subtunes, 3000 frames) + 82 static-only (mostly the unused IRQ installer and the enable/disable-sfx entries).
- Provenance: same routine as the Monty on the Run driver in McSweeney's commented disassembly (Anthony McSweeney, "Rob Hubbard's Music: Disassembled, Commented and Explained", 1993); Commando adds: pattern-embedded portamento (present in Monty too), instrument-fx bit 3 (pulse "sawtooth" mode), sound-effect engine, and the sfx/music arbitration flag. Every routine below was matched to the Monty source; divergences are noted.

#### 3.1.1 Memory map and state

Code:
| routine | range | role |
|---|---|---|
| API jump table | $5000–$5011 | +0 init(A=song) +3 music off +6 sfx enable +9 sfx disable+silence +$C stop sfx +$F play sfx(A) |
| play | $5012–$5051 | frame counter, status decode, lazy init |
| voice loop head / NoteWork | $5052–$5173 | speed divider, note fetch, instrument write-out |
| SoundWork | $5174–$538E | gate-off, vibrato, pulse, portamento, drum, skydive, arpeggio |
| loop tail | $538F–$53A4 | recompute sfx-arbitration flag, DEX, loop |
| sfx step | $53A5–$5427 | sound-effect state machine (runs after the 3 voices) |
| sfx init | $5531–$5590 | load a 16-byte sfx record, copy 14 bytes to SID |
| init/off/sfx API bodies | $5F0C–$5F69 | |
| dead: game IRQ installer | $5F6A–$5FB1 | never reached from PSID entries |
| PSID init wrapper | $5FB2–$5FC6 | A<3: stop sfx, init song A; else play sfx A−3 with music off |

Data (all in-image; the player mutates some of it):
| name | addr | size / element | indexed by |
|---|---|---|---|
| freq table | $5428 | 96 × u16 LE, C-0 ($0116) … B-7 | note*2 (`ASL; TAY`, `LDA $5428,Y / $5429,Y`) |
| voice→SID offset | $54E8 | 3 bytes 00 07 0E | X (voice 2..0) |
| per-voice state (struct-of-arrays, stride 1, X=voice) | $54EC.. | see below | X |
| temporaries | $5501–$550C, $5518, $5523, $5524 | scalars | — |
| song speeds | $5514 | 3 bytes (02 03 02) | song |
| instruments | $5591 | 13 × 8 bytes: pw lo, pw hi, ctrl, AD, SR, vib depth, pulse speed, fx bits | instr*8 (`ASL×3`) via X or Y |
| sfx records | $55F9 | 16 × 16 bytes (see §4) | sfx*16 (`ASL×4; TAY`) |
| current track ptrs | $56F9 | lo[3], hi[3] | X (voice) |
| song table | $56FF | 3 songs × 6 bytes: track lo[3], hi[3] | song*6 |
| pattern ptr lo / hi | $5711 / $573E | 45 entries each | pattern number (Y) |
| tracks | $576B–$5886 | byte lists of pattern numbers, terminated $FF (loop) / $FE (stop) | track ptr,Y |
| patterns | $5887–$5F0B | note records (§4), terminated $FF | pattern ptr,Y |

Per-voice state (X = 0,1,2 = SID voice 1,2,3):
| addr | name (McSweeney) | meaning |
|---|---|---|
| $54EC,X | posoffset | index into track (pattern list) |
| $54EF,X | patoffset | index into pattern |
| $54F2,X | lengthleft | remaining ticks of current note (counts to −1) |
| $54F5,X | savelnthcc | note's first byte (length + 3 control bits) |
| $54F8,X | voicectrl | instrument ctrl byte at note start |
| $54FB,X | notenum | pitch 0..95 |
| $54FE,X | instrnr | instrument number |
| $5520,X | portaval | portamento byte (0 = none) |
| $551A,X / $551D,X | savefreqhi/lo | frequency written at note start (mutated by drum/skydive/portamento) |
| $550D,X | pulsedelay | pulse-mod countdown |
| $5510,X | pulsedir | 0 = up, ≠0 = down |

Global: $5525 frame counter (free-running u8, LFO time base); $5519 mstatus ($40 init, $80 off, $C0 off+silence, else playing); $5513 speed countdown; $5517 speed; $54EB current voice's SID offset (0/7/14, copy of $54E8,X); $5526 sfx-disabled flag; $5527 sfx status (bit7 = none, bit6 = start request, low nibble = sfx nr); $5528 "music may write SID" ($FF yes / 0 no); $5529 sfx freq index; $552A sfx step countdown; $552B sfx end index; $552C sfx voice-2 interval; $552D sfx gate-toggle flags; $552E/$552F sfx voice-1/2 ctrl shadows; $5530 sfx flags+speed. Zero page: $5D/$5E track pointer, $5F/$60 pattern pointer — reloaded at every note fetch, never assumed preserved.

#### 3.1.2 Entry points and conventions

- `init(A=song)` $5F0C: X=A; speed ← $5514,X; A*6 → copy 6 track pointer bytes $56FF+6A.. → $56F9..$56FE; gates of all 3 voices ← 0; $D418 ← $0F; mstatus ← $40. Nothing else: the real per-voice reset happens lazily inside play (see §3) — init is 54 bytes because play already owns the state.
- `play` $5012: no arguments; X,Y,A clobbered.
- `music off` $5F42: mstatus ← $C0 (play silences on next call, then $80).
- `play sfx(A)` $5F56: if $5526≠0 (disabled) → $5527 ← that value (negative → ignored); else $5527 ← A|$40, volume $0F. The sfx starts on the next play call.
- `stop sfx` $53CF: gates of voices 1,2 ← 0; $5527 ← $FF. Reached also as the natural end of an sfx.
- PSID wrapper $5FB2 dispatches on A: `CMP #3; BCS` — songs 0..2 → `JSR $500C` (stop sfx), `STX $5528` (junk: X is whatever the host passed), `JMP $5000`; A≥3 → `SBC #3` (C known set from the compare) → `JSR $500F` (start sfx) then `JMP $5003` (music off). Tail-call by JMP throughout; the API is a JMP table so entry offsets are stable while bodies move.
- Flags carrying meaning across instructions: `BIT $5519` decodes N (off) and V (init) in one instruction ($5015); `BIT $5502` tests bit 6 of the note byte ($50CF); `BIT $5530`/`BIT $552D` decode two sfx option bits each ($53E3, $5408); C after `CMP #3` feeds `SBC #3` ($5FBF).
- Register conventions inside play: X = voice (2→0) everywhere except two windows where X is borrowed for instr*8 ($5127–$5154, saved in $5504) and for the SID offset ($529F–$52B0); Y = SID offset (0/7/14) when writing SID, or a table index (note*2, instr*8, sfx*16, pattern/track position) otherwise. Y is always reloaded from $54EB before a `,Y` SID write.

#### 3.1.3 The play routine

```
play():
  counter++                                   ; $5525, u8, LFO clock
  if mstatus & $80:                           ; off
      if mstatus & $40: gates=0; vol=$0F; mstatus=$80
      goto sfx_step
  if mstatus & $40:                           ; lazy init
      counter=0; for v: pos[v]=pat[v]=lengthleft[v]=notenum[v]=0; mstatus=0
  if --speedctr < 0: speedctr = speed         ; $5513/$5517
  tick = (speedctr == speed)                  ; true once every speed+1 frames
  for v in 2,1,0:                             ; X
      regofs = [0,7,14][v]                    ; $54EB (Y)
      if tick:
          if --lengthleft[v] < 0: fetch_note(v); goto tail
          if music_allowed:                   ; $5528 < 0
              if !(savelnthcc[v] & $20) and lengthleft[v]==0:   ; end of note, release wanted
                  SID[4] = voicectrl[v] & $FE; SID[5]=SID[6]=0   ; gate off, AD=SR=0: hard cut
      if music_allowed: soundwork(v)
    tail:
      music_allowed = !(sfx_enabled and sfx_active)     ; $5528 recomputed per voice
  music_allowed = $FF                          ; so voice 3 always writes next frame
  sfx_step()
```
Note the ordering trick: voices are processed 3,2,1 and `music_allowed` is refreshed *after* each voice from the sfx state, but forced to $FF after the loop. So SID voice 3 always plays music, and voices 1–2 stop writing SID while an sfx is active — the sfx engine owns exactly SID voices 1 and 2. No explicit "voice ownership" variable exists; it is the loop order.

```
fetch_note(v):                                  ; $5086
  loop:
    b = track[pos[v]]
    if b == $FF: lengthleft=pos=pat=0; goto loop          ; song loops
    if b == $FE: music_off(); return                       ; song ends
    pattptr = (patlo[b], pathi[b])                          ; ZP $5F/$60
    portaval[v] = 0; gatemask = $FF
    n = patt[pat[v]]; savelnthcc[v]=n; lengthleft[v]=n & $1F
    if n & $40:                                             ; "append": no new instr/pitch
        gatemask = $FE                                      ; ctrl written with gate off
    else:
        pat++ 
        if n & $80: x = patt[pat++]; if x<$80: instrnr[v]=x else portaval[v]=x
        notenum[v] = patt[pat]
        if music_allowed: f = freq[notenum]; SID[0..1]=f; savefreq[v]=f
    i = instrnr[v]*8
    voicectrl[v] = instr[i+2]
    if music_allowed: SID[4]=instr[i+2]&gatemask; SID[2..3]=instr[i+0..1]; SID[5]=instr[i+3]; SID[6]=instr[i+4]
    pat++; if patt[pat]==$FF: pat=0; pos++                  ; eager end-of-pattern
```
So a "note" is: (length, flags, [instrument|portamento], pitch), and the fetch frame writes freq, pulse, ctrl(gate on), AD, SR — in that order — and does *not* run soundwork.

```
soundwork(v):                                   ; $519B, every frame the voice is allowed
  i = instrnr[v]*8; fx=instr[i+7]; pspeed=instr[i+6]; vdepth=instr[i+5]
  if vdepth:                                              ; vibrato
      phase = counter&7; if phase>=4: phase ^= 7          ; 0 1 2 3 3 2 1 0
      d = (freq[note+1]-freq[note]) >> (vdepth+1)          ; semitone/2^(depth+1)
      f = freq[note]; if (savelnthcc&$1F) >= 6: f += phase*d
      SID[0..1] = f                                        ; not stored back
  if fx & 8:  instr[i+0] += pspeed + C (8-bit wrap); SID[2] = instr[i+0]  ; new in Commando; C inherited (see §6)
  elif pspeed:                                             ; pulse sweep
      if --pulsedelay[v] < 0:
          pulsedelay[v] = pspeed & $1F
          step = pspeed & $E0
          if !pulsedir[v]: pw += step; if (pw>>8)==$0E: pulsedir[v]++      ; 12-bit
          else:            pw -= step; if (pw>>8)==$08: pulsedir[v]--
          instr[i+0..1] = pw; SID[2..3] = pw                                 ; state lives in the instrument
  if portaval[v]:                                          ; portamento
      savefreq[v] += (portaval&1 ? -1 : +1) * (portaval & $7E); SID[0..1] = savefreq
  if fx&1 and savefreqhi[v] and lengthleft[v]:            ; drum
      if first tick of note: SID[1]=savefreqhi; SID[4]=$80        ; noise, gate off
      else: SID[1]=savefreqhi--; SID[4]= voicectrl&$FE or $80 if that is 0
  if fx&2 and (savelnthcc&$1F)>=3 and counter&1 and savefreqhi: savefreqhi -= 1 (skip: SID[1] write)  ; skydive
  if fx&4: SID[0..1] = freq[note + (counter&1 ? 12 : 0)]  ; octave arpeggio
```
Writes within one frame to the same register are ordered as listed; the last one wins (e.g. an instrument with fx=5 has its drum freq-hi write overwritten by the arpeggio's freq write every frame — verified in the SID log: `01=1D@532E … 01=3A@5386`).

Sound effects (`sfx_step`, $53A5): if sfx disabled or none active → return. If start requested (bit 6 of $5527) → `sfx_init` (§4). Then `if --$552A >= 0 return; $552A = flags&$0F` (sfx speed); `if fidx == endidx: gates(1,2)=0; status=$FF; return`; `fidx ±= 1` (direction is an *opcode* — see §6); `Y=fidx*2`; unless flags bit7: unless bit6: SID v1 freq = freq[Y]; SID v2 freq = freq[Y − interval]; if $552D bit7: v1 ctrl ^= 1 (gate toggle, shadow $552E); if bit6: v2 ctrl ^= 1 ($552F). I.e. an sfx is a frequency-table sweep from a start index to an end index at a given rate on two voices a fixed interval apart, optionally re-triggering the gate every step.

#### 3.1.4 Data formats

Track (per voice, per song): `pattern#*  ($FF | $FE)`. Every entry ≤ 44 (45 patterns exist; all used). $FF loops the whole song (position 0), $FE stops it (music off).

Pattern: sequence of note records, terminated $FF:
```
byte0: L L L L L  bits 0-4 length in ticks (0..31)   [counted: max 31]
       bit 5 : no release at end (tie into next note)          [21 of 749 notes]
       bit 6 : "append" — no further bytes; ctrl written with gate off (soft note-off for L ticks) [16]
       bit 7 : byte1 present                                    [175]
byte1 (if bit7): $00-$7F instrument number  |  $80-$FF portamento: bit0 = down, bits1-6 = step (freq units/frame)  [15 porta]
byte2 (unless bit6): pitch 0..95   [observed 16..104 → the tune reaches into the 9th octave]
```
Terminator sensing is eager: after each note the next byte is compared with $FF, so pattern position 0 with an incremented track position is the stored state at pattern end. Note lengths are in ticks; a tick = speed+1 frames (song 0: 3 frames).

Instrument (8 bytes, `$5591 + 8n`): pw lo, pw hi (bits 0-3), ctrl (waveform|gate, e.g. $41 $43 $81 $15 $21), AD, SR, vibrato depth (0 = off; shift count = depth+1), pulse speed (bits 0-4 delay reload, bits 5-7 step ×32; or 8-bit add when fx bit3), fx bits: 0 drum, 1 skydive, 2 octave arpeggio, 3 8-bit pulse-lo run. Bytes 0-1 are *mutated* by the pulse sweep: two voices on the same instrument share one pulse state.

Frequency table: 96 × u16 at $5428 (equal temperament, PAL). Vibrato reads entry note+1, so pitch 95 reads two bytes past the table ($54E8) — benign overrun.

Sfx record (16 bytes, `$55F9 + 16n`): byte0 flags — bit7 no freq writes, bit6 no voice-1 freq write, bits4-5: $20 = sweep up else down, bits0-3 step period; bytes 1..14 = literal SID register image for $D400..$D40D (v1 freq lo/hi, pw lo/hi, ctrl, AD, SR, v2 same) — copied by a 14-iteration loop; byte1 doubles as the start freq index, byte8 (v2 pw hi position) doubles as v2 interval (bits0-5) + gate-toggle flags (bit7 v1, bit6 v2), byte15 = end freq index. Byte5/12 (the ctrl bytes) are also kept as the toggle shadows. One record, three overlapping interpretations — chosen so that the copy loop needs no decoding.

Song table: 3 × (lo,lo,lo,hi,hi,hi). Speed table 3 bytes.

#### 3.1.5 SID write schedule

Per voice per frame (only when `music_allowed`): note-fetch frames write $D400/1 (freq), $D404 (ctrl, gate per mask), $D402/3 (pw), $D405 (AD), $D406 (SR) in that order, and nothing else. Other frames: end-of-note release writes ctrl&$FE, AD=0, SR=0 (only on tick frames, only when bit5 clear); then vibrato freq; pulse; portamento freq; drum freq-hi + ctrl; skydive freq-hi; arpeggio freq. Voice order 3,2,1. No shadow registers except savefreq and the pulse width kept in the instrument. Volume ($D418=$0F) written only by init/off/sfx start. Gate handling: gate on at fetch, gate off at the tick where lengthleft reaches 0 with AD/SR zeroed (i.e. an immediate cut, no release tail unless bit5); the "append" note is the soft alternative (gate off with the instrument's SR). No hard-restart/ADSR-bug workaround, no reads of $D41B/$D41C/$D012. sfx writes $D400–$D40D as a block at start, then freq (v1, v2) and ctrl toggles per step.

#### 3.1.6 Techniques specific to this player

| technique | site | why |
|---|---|---|
| Opcode as a boolean: `DEC $5529` ($CE) ↔ `INC $5529` ($EE) at $53DE, patched by sfx init `LDY #$EE/#$CE; STY $53DE` ($5585–$558D) | the only SMC | one byte and zero cycles per step for the sweep direction; the variable *is* the instruction |
| BIT to test two bits with N/V: `BIT $5519; BMI; BVC` $5015; `BIT $5502; BVS` $50CF; `BIT $5530; BMI; BVS` $53E3; `BIT $552D` $5408 | | decode packed flags without touching A |
| Struct-of-arrays per-voice state, `abs,X`, X = voice 2..0, `DEX; BMI` loop $539F | whole player | 3-byte arrays, one index register for all voice state |
| Voice→register-offset table $54E8 + `STA $D400,Y` | every SID write | avoids voice*7 multiplication; Y reloaded from $54EB before each write |
| Index-register borrowing with a memory save: `STX $5504 … TAX … LDX $5504` $5121/$5154; `STX $5504; LDX $54EB … LDX $5504` $529C/$52B0 | | X needed as instr*8 / SID offset while voice index must survive |
| Stack as scratch: `PHA; PHA … PLA; PLA` $5272–$52AA | pulse sweep | both index registers busy; new pw needed after re-indexing |
| ZP pointer + `(zp),Y` walk with the pointer rebuilt from lo/hi tables at each use ($506E–$5076, $50AA–$50B3) | fetch | no persistent pointer state; positions are 1-byte indices → patterns ≤ 256 bytes |
| Eager terminator peek `LDA ($5F),Y; CMP #$FF` after each note ($5163) | | end-of-pattern state stored as (pat=0,pos+1): no "end" flag needed |
| Sentinel bytes $FF/$FE for loop/stop in tracks, $FF in patterns | | zero-cost length fields |
| Free-running frame counter as LFO phase: `AND #$07 / EOR #$07` triangle ($51C1), `AND #$01` for arpeggio/skydive ($5346, $5365) | | one global counter serves all voices; no per-voice phase |
| Variable-count shift loop `LSR A; ROR $5508; DEC $5506; BPL` ($51E4) | vibrato | depth as power of two of the semitone interval from the freq table difference — no multiply |
| Multiply-by-constant via shifts/adds: n*6 = n*2+n*4 ($5F15–$5F1F), n*8 `ASL×3`, n*16 `ASL×4` | | table strides |
| Countdown-to-negative counters `DEC; BPL/BMI` (speed $5054, lengthleft $5078, pulsedelay $5256, sfx $53BA) | | reload on underflow; −1 doubles as "fetch now" |
| Data-shaped-like-hardware: 14 sfx bytes copied straight into $D400.. by `STA $D400,X` loop ($5572–$557E) | sfx | no field decoding at all |
| Mutable instrument record as shared state ($5591/$5592 rewritten $52A3/$52AA) | pulse | saves 6 bytes of per-voice pw state; side effect: voices sharing an instrument share pw |
| Ordering as a variable: `music_allowed` refreshed at loop tail, forced $FF after loop ($538F–$53A7) | | voice arbitration between music and sfx without any per-voice flag |
| Tail JMPs and shared tails (`JMP $538F`, `JMP $53A5`, `JMP $53B4`), API JMP table, `JSR $5003` into own API | | stable entry offsets; no RTS chains |
| ADC without CLC ($523D, `ADC $5507` after `AND #$08; BEQ`) | fx bit3 pulse | C is inherited from the vibrato block: `CMP #$06` leaves C=1 when length≥6, and the phase loop exits without touching it when phase=0 → verified: instrument 8 (song 1) and 10 (song 2) step by speed+1 on every 8th frame (1080 and 162 occurrences), all other paths C=0. A hidden data dependency through a flag — a decompiler must model C as a value across ~40 instructions |
| Interleaved code and data ($54E8–$5530 variables sit between two code blocks) | | Hubbard assembled variables where he was; code/data boundaries are not contiguous |

No illegal opcodes, no computed jumps, no jump tables, no RTS tricks: dispatch is entirely by compare-and-branch on bits.

#### 3.1.7 What it reduces to

The whole player is a 3-voice ticker: `12 bytes of state per voice`, `4 global bytes` (counter, status, speed, speedctr), and one 16-byte sfx state. Per frame, per voice: *(a)* on a tick boundary, either decrement the note length or fetch the next 2–3-byte note and write 7 SID registers; *(b)* otherwise apply up to five per-frame modulations, each a few lines, each writing SID directly. There is no envelope of its own, no wave/pulse/filter *programs*, no arpeggio table: the instrument is a fixed 8-byte SID image plus 4 fx bits and 2 modulation speeds. All "sound design" is those bits.

Statically decidable: song structure (tracks, patterns, instruments), the freq table, the exact number of frames per tick, every SID write site's register (Y is always the voice offset from a 3-entry table). Runtime-only: which sfx is requested (external input via the API), and the note stream position (deterministic given the frame count). Volatile hardware inputs: none. SMC: one opcode cell encoding a 1-bit variable (`dir ∈ {DEC, INC}`), written only from sfx init.

Dead in this tune: skydive body ($5346–$535B: instrument 4 has fx bit1 but is only used with lengths <3), sfx enable/disable API ($5F48/$5F4E), Monty's `JSR $5003` at song end (no song ends with $FE while music is on except song 2), the IRQ installer. Family variation: later Hubbard drivers add more fx bits (filter, wave programs, "shattered" drums) and change the instrument size; the sequencer grammar above stayed.

Player in ~30 lines: see §3 — the pseudocode there *is* the player; the assembly is that pseudocode with X=voice, Y=SID offset, and flags in place of booleans.

#### 3.1.8 Decompiler notes

- Code/data boundaries: three code blocks separated by variable/table blobs; the API JMP table at the load address plus the PSID init/play addresses give all roots; everything else is reached by static descent (no indirect jumps). Data typing comes from the addressing modes: `$5428,Y` after `ASL;TAY` = u16 table indexed by note; `$5591,X` after `ASL×3` = 8-byte records; `($5F),Y` = byte stream.
- Per-voice struct recovery: every `abs,X` inside $5052–$53A4 with X∈{0,1,2} (X is set only by `LDX #2`, `DEX`, and restored from $5504) is a per-voice field; the fields form one struct with stride 1 spread over 12 base addresses. `abs,Y` with Y from $54EB is a SID voice-block field (stride 7). Y with other origins indexes tables; distinguish by the reaching definition of Y (note*2, instr*8, sfx*16, position).
- The one SMC site: model $53DE as `fidx += dir` where `dir` is a 1-bit variable stored as the opcode byte, defined at $558D. Both variants are `RMW abs`; the operand never changes.
- Flag arguments: BIT-decoded status bytes (N/V), C after CMP consumed by SBC, `ADC` with inherited carry — track flags as values, not as "condition codes of the previous compare".
- Invariants: X = voice throughout the loop except the two save/restore windows; Y is dead across every `LDY $54EB`; ZP $5D–$60 are pure temporaries (redefined at every use); `music_allowed` is a loop-carried value defined at the tail; `counter` is only read as `&7`, `&1`.
- Ordering matters: same-register writes within a frame (drum then arpeggio) — a decompiler that lifts to "per-frame register value" must keep the last write, and must keep intermediate $D404 writes if it models gate edges (frame 0 gate on, frame 1 `$80` gate off is a real transition).
- Traps: the freq table overrun at note 95 (reads $54E8/$54E9), the junk `STX $5528` in the wrapper, the unused IRQ code, `SBC #3` depending on C from `CMP`, and shared mutable instrument bytes (aliasing between voices).
- Cycle counting is irrelevant to correctness here (no raster/timer dependence).

### 3.2 Martin Galway — Comic Bakery (1986)


#### 3.2.0 Identity

- Tune: `MUSICIANS/G/Galway_Martin/Comic_Bakery.sid`, PSID, 14 subtunes, load $7F00–$9FFF, init $7F00 (A=subtune), play $7F03 (JSR each frame, no IRQ, single speed). Trace: all 14 subtunes × 3000 frames.
- Sizes: PSID glue $7F00–$7F6F (112 B); player code $8000–$8C55 (3158 B); workspace $8C56–$8D91 (316 B: S0–S2, duration table, D0–D2, globals); tables $8D92–$8EC9 (freq 190 B, 3 command vectors 90 B, tune-pointer table 36 B); song/effect data $8ECE–$9FFF.
- Subtunes 0–5 are sequenced music (0 = main theme, 1–2 further loops, 3–5 short jingles ending at frames 110/136/575); subtunes 6–13 are 3-voice one-shot sound effects (24–354 frames) driven by the same per-frame engine without a sequencer.
- Certified: [prototype-galway-trackerprog.md](prototype-galway-trackerprog.md) transliterates this family into a trackerprog and renders all 14 subtunes at 0 divergences over 29,911 ticks; [galway-comic-bakery.json](certificates/galway-comic-bakery.json) is the front-end certificate.
- Provenance: same lineage as the author-published `wizball.asm` (1987). Comic Bakery (1986) is an earlier, smaller cut: 15 sequencer commands (Wizball 22: no Master/Filter/Disown/MBend/Freq/Time), FM offset-list inline in the record instead of a pointer, fixed duration table loaded by the song instead of `CalcDurations`, no filter/`RefFilter`, no digi. Names below (PC0, CLOCK0, SP0, S0, D0, FMG/FMD/FMC, PMG/PMD/PMC, VWF/VADSD/VRD, TR, MFL, IDRT, `Moke/Soke/DMoke/FLoad/For/Next/Ret/Call/Jmp/CT/JT/Transp/Code`) are Wizball's and match the binary one-for-one where noted.

#### 3.2.1 Memory map and state

Code (per-voice code is three unrolled copies, identical modulo absolute addresses):

| routine | v0 | v1 | v2 | shared |
|---|---|---|---|---|
| API dispatch (JMP patched) | | | | $8000–$800E, vector table $800F–$8034 |
| effect starters (8 × 3 voices) | | | | $8035–$80FC |
| tune starters (6, LDY-chain) + `InitVoices` | | | | $80FD–$8140 |
| MusicTest / Reset / MasterVol+FilterOut / SetMFL | | | | $8141 / $814F / $8167,$816A / $8186 |
| transferpm (PCURR/counter reload) | $818A/$8196 | $81A3/$81AF | $81BC/$81C8 | |
| StartEffect(A/Y=block, X=voice) | | | | $81D5/$81D7–$82E2 (per-voice tails $8249/$827F/$82B1) |
| transferf (FCURR/counter reload) | $8256/$8262 | $828C/$8298 | $82BE/$82CA | |
| Play entry | | | | $82E3 |
| sequencer step | $82EC–$84AE | $860E–$87D2 | $8932–$8AF6 | |
| per-frame voice engine (gate/PM/FM) | $84AF–$860D | $87D3–$8931 | $8AF7–$8C55 | |

Data / state:

| name | address | size | notes |
|---|---|---|---|
| S0/S1/S2 "sound" (instrument) records | $8C56 / $8C8B / $8CC0 | $35 each | layout in §4; +$1D..$34 = the voice's 8-deep sequencer stack (ST L/H/C) |
| IDRT duration table | $8CF4–$8D04 | 17 | `dur n → IDRT[n]` frames; loaded by the song via `fload` into S2+$35.. (i.e. it is addressed as an extension of S2); IDRT[0] aliases ST2C[7] |
| D0/D1/D2 per-voice dynamic records | $8D05 / $8D2C / $8D53 | $27 each | layout in §1b |
| TR0..2 transpose | $8D7A–$8D7C | 3 | signed note offset added to every note |
| MFL | $8D7D | 1 | bit v = music allowed to touch voice v (set to 7 by init) |
| filter shadows / mode | $8D7E–$8D81 | 4 | copied to $D415–$D417 and OR'd into $D418 every frame; never written in this tune (all 0) |
| master volume | $8D82 | 1 | $0F from init |
| D-offset per voice | $8D83 | 3 | $17,$3E,$65 (used with `,X` on `$8D05+`... base-relative addressing in StartEffect) |
| SID offset+2 per voice | $8D86 | 3 | $02,$09,$10 |
| S-offset per voice | $8D89 | 3 | $00,$35,$6A |
| F9 "free" masks | $8D8F | 3 | $37,$2F,$1F (clear bit 3+v) |
| HiFrq / LoFrq | $8D92 / $8DF1 | 95+95 | note 0..$5D = NTSC 1 MHz table (274,291,…,59056), entry $5E = 0 (silence) |
| vt0/vt1/vt2 command vectors | $8E50 / $8E6E / $8E8C | 15 words each | index (cmd−$C0)/2 |
| tune pointer table | $8EAA | 6×3 words | three sequence starts per music subtune |
| effect blocks | $8ECE.. | 31 B each | S-record image (§4) |
| song data | $91B6–$9FFF | | instrument tables, sequences |

Zero page ($F0–$FF, = Wizball's ZERO block):
$F0/1 $F2/3 $F4/5 = PC0..2 (sequence pointers); $F6/7 = IN (temp pointer / command operand); $F8 = Z8 (note byte / voice number / temp); $F9 = flags — bit v (1,2,4) = voice v sequencer running, bit 3+v (8,$10,$20) = voice v hardware free (not held by an effect); $FA/$FB/$FC = CLOCK0..2 (frames left in current step); $FD/$FE/$FF = SP0..2 (stack index, 7 = empty, counts down).

D record (base D = $8D05 + $27·v; all bytes read/written only by voice v's code):

| off | Wizball name | meaning |
|---|---|---|
| 0–7 | FMG0..3 | 4 signed 16-bit frequency gradients — or, in arp mode, 8 note offsets |
| 8–$B | FMD0..3 | segment durations (frames); arp mode: $A = base note, $B = max list index |
| $C | FMDLY | delay before FM starts; arp mode: current list index |
| $D | FMC | 0 off; bit0 loop (reload counters only), bit7 loop (reload FCURR too), bit1 add FMG3 during delay (bend-in), bit3 arpeggio mode |
| $E,$F | PMD0,PMD1 | pulse segment durations |
| $10 | PMDLY | pulse delay |
| $11 | PMC | 0 off; bit0 loop counters, bit7 loop incl. PCURR |
| $12–$15 | PMG0,PMG1 | signed 16-bit pulse gradients |
| $16,$17 | PINIT | initial pulse (also the value first written to $D402/3) |
| $18,$19 | VFREQ | note frequency at note start |
| $1A | VWFG | wave byte (bit3 = "release VADSC frames before step end" flag, masked off before reaching SID) |
| $1B | VADSC | gate counter |
| $1C | VRC | release counter; VRC≠0 ⇔ voice engine active |
| $1D,$1E | FCURR | current frequency (what is in $D400/1) |
| $1F–$22 | FMD0C..3C | FM segment counters |
| $23,$24 | PCURR | current pulse |
| $25,$26 | PMD0C,PMD1C | PM segment counters |

Residual state: the .sid image contains leftover D/S/IDRT contents from Galway's save (e.g. D0.FMC=5, D0.VADSC=$A2). It is inert only because VRC (D+$1C) is 0 for all three voices; the main tune's voice 0 does `dmoke $1C,$FF` during its 512-frame intro rest, which switches the engine on over that residual state — the resulting $D400–$D403 writes go to a voice whose control register is 0, so they are inaudible, but they are in the write log. A decompiler must treat D/S as initialised from the image, not zero.

#### 3.2.2 Entry points and conventions

PSID glue ($7F10): A=subtune → $7F06 := {0:$0E, 1:$0A, 2:$0C, n≥3: 2n+$0A}; then writes 4 game variables ($C008–$C00A:=0, $C082:=3 — dead, never read), then API calls Y=6/X=$0F (master volume), Y=2 (reset), Y=8/X=7 (MFL=7), Y=$7F06 (start). Play glue: Y=0.

API `$8000`: `LDA $800F,Y / STA $800D; LDA $8010,Y / STA $800E; JMP $xxxx` — the JMP operand is patched (SMC) from a word table; Y is the byte offset. Vector map: 0 Play, 2 Reset, 4 MusicTest (A = F9&7 | VRC0|VRC1|VRC2), 6 MasterVol(X), 8 SetMFL(X), $0A..$14 six music starts (subtunes 1,2,0,3,4,5), $16..$24 eight 3-voice effect starts (subtunes 6..13 in the order $16,$18,$1A,$1C,$1E,$20,$22,$24 → $80E4,$80CB,$8099,$80B2,$8080,$8067,$8035,$804E).

Music start ($80FD chain): a `BIT $xxA0` chain gives six entry points that each hide one `LDY #k` (k=$05,$0B,$11,$17,$1D,$23); common tail copies 6 bytes `$8EAA+k-5..k` into $F0–$F5 (three sequence pointers), F9:=$3F (all running, all free), and per voice X=2..0: SP:=7, CLOCK:=1 (so the first play frame steps immediately), TR:=0, S.FMC:=0, S.PMC:=0, S.PINIT:=$0800. Everything else in S/D is left as it was.

Effect start (`StartEffect` $81D5: X:=0; $81D7: A/Y = block address, X = voice): F6/7:=block; F9 &= mask[X] (voice busy); patches the operand low byte of two `STA $D410,X` ($81F4,$81FF) with SIDoff[X] (2/9/$10) then writes SR,AD,wave|8,wave,pulse-hi,pulse-lo (Y=$1A..$16) each preceded by a 0 write; reuses the patched byte as an index (`LDX $81F4; STA $D3FE,X`) to write freq lo/hi from block[$1D,$1E]; copies block[$1B,$1C]→VADSC/VRC, [$18]→VWFG, [$1D,$1E]→VFREQ, block[$17..0]→D[$17..0]; then per-voice tail: if PMC≠0 transferpm; if FMC≠0 transferf. Registers: A/Y = pointer, X = voice — the only place a voice number is passed in a register.

Play ($82E3): `JSR $816A` (D418 := vol|mode; D415–D417 := shadows) ; `JSR $8932` (voice 2) ; `JSR $860E` (voice 1) ; voice 0 inline. Voices are processed 2,1,0. Each voice routine = sequencer step (only if its F9 run bit is set and `--CLOCK == 0`) then falls into / jumps to its per-frame engine ($84AF); the engine returns with RTS.

Conventions inside a voice: no voice register — the voice is the code copy. Y = offset into the sequence (`(PC),Y`) or into a data block (`(F6),Y`); X = note number, command byte, stack index or S/D offset; A carries the second operand byte into command handlers, X the first (`(PC),1 → X and F6; (PC),2 → F7 and A`). Command handlers end by `JMP fetch` (after setting PC) or by loading A with their length and jumping to `PC += A; fetch` ($82F8 = "A:=3", $82FA = "add A"). Flags are never carried across JSRs; carry is set up explicitly (`CLC` before the 16-bit adds; the note path relies on `CMP #$5E` leaving C=0 for A<$5E so that `ADC TR` needs no CLC).

#### 3.2.3 The play routine

```
play():
    D418 = vol | filtmode; D415..17 = shadows        # $816A
    voice(2); voice(1); voice(0)

voice(v):                                             # v0: $82EC
    if F9.run[v] and --CLOCK[v] == 0:
        step(v)                                       # loops over commands until a note is consumed
    engine(v)                                         # $84AF

step(v):                                              # $8303 fetch
    loop:
        b = *PC
        if b >= $C0:                                  # command; X=(PC)[1], F6=(PC)[1], F7=A=(PC)[2] pre-loaded
            goto vt[v][(b-$C0)/2]                     # via patched JMP $8323
            (handler either sets PC and 'goto loop', or 'PC += len; goto loop')
        # note event: 2 bytes [note][dur]
        Z8 = b; raw = b >= $60; if raw: b -= $60
        if b != $5F (rest):
            if b != $5E: b += TR[v]                    # $5E = silence note (freq 0), not transposed
            if MFL.bit[v] and F9.free[v]:
                D406..D402 = S[$1A..$16] (SR, AD, wave|8 then wave, pulse hi, pulse lo)   # TEST-bit pulse
                D.VWFG = S.wave
                D.VFREQ = freq[b]; D400/1 = D.VFREQ
                D.PMC = S.PMC; if PMC: D[$E..$17] = S[$E..$17]; transferpm(v)  # PCURR=PINIT, PM counters=PMD
                D[0..$D] = S[0..$D]                                            # FM block
                if S.FMC.bit3: D[$A] = note+TR (arp base)  else transferf(v)  # FCURR=VFREQ, FM counters=FMD
                D.VADSC, D.VRC = S[$1B], S[$1C]
        CLOCK = raw ? (PC)[1] : IDRT[(PC)[1]]          # 0 raw → 256 frames (DEC wraps)
        PC += 2

engine(v):                                            # $84AF
    if D.VRC == 0: return                             # voice hardware idle
    if D.VWFG & 8:                                    # release-relative-to-step-end mode
        if CLOCK < D.VADSC: D.VADSC = 0; D.VWFG &= $F6; if VWFG: D404 = VWFG   # gate off (and TEST off)
    elif D.VADSC: if --D.VADSC == 0: D404 = VWFG & $F6                            # gate off after VADSC frames
    else: if --D.VRC == 0: D400..D406 = 0; F9.free[v] = 1; return                # hard kill, voice free
    if D.PMC:                                         # pulse modulation, 2 linear segments
        if D.PMDLY: --D.PMDLY
        else: loop: if PMD0C: PCURR += PMG0; --PMD0C
                    elif PMD1C: PCURR += PMG1; --PMD1C
                    else: c = PMC & $81; if c==0: break; if c&$80: transferpm(all) else transferpm(counters); continue
              D402/3 = PCURR
    if D.FMC:
        if FMC & 8:                                   # arpeggio: offset list read backwards
            i = D[$C]; if i < 0: i = D[$B]; n = D[$A] + D[i]; D[$C] = i-1; D400/1 = FCURR = freq[n]
        elif FCURR == 0: return
        elif D.FMDLY: --D.FMDLY; if FMC & 2: FCURR += FMG3; D400/1 = FCURR      # bend during delay
        else: 4-segment version of the PM loop over FMG0..3/FMD0C..3C, loop bits $81 via transferf; D400/1 = FCURR
```
Command handlers (vt index → address for v0; identical for v1/v2 at their copies):

| # | byte | name (Wizball) | bytes | effect |
|---|---|---|---|---|
| 0 | $C0 | Ret | 1 | SP++; if SP==8: F9.run[v]=0 (voice ends), RTS; else PC = ST[SP] |
| 1 | $C2 | Call | 3 | ST[SP] = PC+3; SP--; PC = op16 |
| 2 | $C4 | Jmp | 3 | PC = op16 |
| 3 | $C6 | CT (call+transpose) | 4 | TR = op3; push PC+4; PC = op16 |
| 4 | $C8 | JT (goto+transpose) | 4 | TR = op3; PC = op16 |
| 5 | $CA | Moke | 3 | S[op1] = op2 |
| 6 | $CC | For | 2 | ST[SP] = PC+2 (loop start), STC[SP] = op1; SP--; PC += 2 |
| 7 | $CE | Next | 1 | if --STC[SP+1]: PC = ST[SP+1] else SP++, PC += 1 |
| 8 | $D0 | FLoad (block copy) | 5 | S[op1-op2 .. op1] = *op16 (op2+1 bytes, copied backwards) |
| 9 | $D2 | (load 10) | 3 | S[0..9] = *op16 (entry hidden inside `BIT`) |
| 10 | $D4 | (load 14) | 3 | S[0..$D] = *op16 (whole FM block) |
| 11 | $D6 | (load 5) | 3 | S[$18..$1C] = *op16 (wave, AD, SR, gate, release) |
| 12 | $D8 | DMoke | 3 | D[op1] = op2 |
| 13 | $DA | Code | 3 | push $82F7; JMP (op16) — user code, returns to `PC += 3` (unused in this tune) |
| 14 | $DC | Transp | 2 | TR = op1 |

Verified counts over all subtunes × 3000 frames: v0 uses $C0,$C2,$CA(39),$D0,$D4,$D6,$D8(11); v1 uses $C0,$C2,$C6,$CC,$CE,$D0,$D2,$D4,$D6; v2 uses $C0,$C2,$C6,$CA(251),$CC,$CE,$D0,$DC. `For`/`Next` on v0 and `Code` everywhere are reachable but unexecuted (x0). Note durations used: index 6 (599×) and 12 (235×) dominate; raw durations $80,$84,$C8,$CE,$FC,$FF and raw 0 (=256) occur.

#### 3.2.4 Data formats

Sequence (per voice, byte stream at PC): `note dur` pairs and commands. Note byte: $00–$5D pitch (transposed by TR), $5E = silence note (freq 0, instrument still triggered), $5F = rest (nothing written), +$60 = "R" flag: duration byte is raw frames instead of an IDRT index. Duration byte (no R): index 0..16 into IDRT (song-loaded; main tune 1,3,6,…,48; tune A 5n; tune B 6n). Commands ≥ $C0, even, table above; the sequencer executes commands back-to-back within one frame until a note is read (e.g. `fload; RESTR 128` or `moke 0D 07; moke 1B 05; note`).

Instrument = S record (29 significant bytes, $00–$1C), normally loaded with `fload $1C,$1C,ptr` (whole) or `load14` (FM part) / `load5` (regs part) / `moke` (single byte):

```
+00 FMG0 lo,hi  +02 FMG1  +04 FMG2  +06 FMG3     (or arp offsets[0..7])
+08 FMD0 +09 FMD1 +0A FMD2 (arp: base note slot) +0B FMD3 (arp: max index)
+0C FMDLY (arp: start index)  +0D FMC
+0E PMD0 +0F PMD1 +10 PMDLY +11 PMC +12 PMG0 lo,hi +14 PMG1 lo,hi +16 PINIT lo,hi
+18 wave (bit3 = release-relative flag)  +19 AD  +1A SR  +1B VADSD gate frames  +1C VRD release frames
+1D..+24 ST L, +25..+2C ST H, +2D..+34 ST C   (sequencer stack, index 7..0)
```
Examples from the song: main lead `$9856`: FM −50/+50/−50 for 2/4/2 frames, +73 unused, delay 6, FMC=5 (loop) = triangle vibrato; PM ±30 for 20/20 frames from $0800; wave $49 (pulse, release 8 frames before step end), AD $02, SR $C7, VRD 10. Chord arp `$958B`: offsets 0,4,7,12,0,4,7,12, max 7, FMC $0D. Bend-in `$91E8`: FMG3=+47, delay 10, FMC 7 (bit1 = slide during delay). Drum `$9B8B`: FMG0=−6, PMG0=+10… wave $29 (saw+TEST flag). FMC/PMC bit2 ($04) appears in every record but is not tested by the code.

Effect block (31 bytes) = S[$00..$1C] followed by freq lo/hi at +$1D/+$1E, written straight to the SID and to D by `StartEffect`; e.g. `$909F`: FM +1135×3 then −1110×5, PM 5-frame delay then +$1E1… , wave $41, AD $29, SR $69, gate 6, release 40, freq $07D0.

Frequency table: 95 entries, NTSC values (note 0 = 274; octave ratio 2.000 ± 0.2 %), entry $5E = 0. Two parallel byte tables (Hi at $8D92, Lo at $8DF1). Only note starts and arpeggios index it; vibrato/bends add 16-bit gradients to FCURR directly (no table).

#### 3.2.5 SID write schedule

Every frame, in order: $D418, $D415, $D416, $D417 (constant here: $0F,0,0,0); then per voice 2,1,0: at a note start SR, AD, wave|$08, wave, pulse hi, pulse lo, freq lo, freq hi; then engine writes: control (gate off) when VADSC expires, $D402/3 if PM active, $D400/1 if FM active or arpeggio, all seven regs := 0 when VRC expires. Effect start writes 0 then value for each of the five regs, then freq lo/hi. Reset writes $D400–$D414 := 0 (D415–D418 untouched). No reads of $D41B/$D41C/$D012; the player is oblivious to raster timing.

Gate model: gate is on from the note start; it goes off either after VADSC frames (absolute) or, if the instrument wave has bit3, when fewer than VADSC frames remain in the step (relative). VRC then counts to hard silence + "voice free". A note whose step is shorter than VADSC never releases (legato); the TEST-bit pulse at note start still resets the oscillator phase / noise LFSR (Galway's "click") **on voices 0 and 2 only** — `$8354 STA $D404` and `$899C STA $D412` name each copy's own control register, while voice 1's `$8678 STA $D409` names its own *pulse low*, two bytes below its `$D40B`, so that voice gets one control write where the others get two and no TEST edge at all (verified on the render, prototype-galway-trackerprog.md §5). Because bit3 of the stored wave never reaches the SID it is a pure player flag. No ADSR-bug hard restart, no filter, no digi in this tune.

#### 3.2.6 Techniques specific to this player

- SMC as computed jump: `LDA vt,X ; STA jmp+1 ; LDA vt+1,X ; STA jmp+2 ; JMP $xxxx` at $830C–$8323 (and $862F/$8953, API $8000–$800C). Command byte is the table index ($8D90+$C0 = $8E50), no shift needed because commands are even. Cheaper than `JMP (ind)` set-up and keeps X/Y free.
- SMC operand patch as parameter: `STA $81F4 ; STA $81FF` (voice SID base into two `STA $D410,X`), then `LDX $81F4 ; STA $D3FE,X` reuses the patched byte as an index register value ($81E4–$8212).
- BIT-skip entry chains: `$80FD LDY #$23 / BIT $1DA0 / BIT $17A0 …` — six entry points, one tail; `$8431 LDY #$0D / BIT $09A0` gives load14 vs load10 one loop.
- RTS-trick call of foreign code: `LDA #$82 PHA LDA #$F7 PHA JMP ($F6)` ($846C) — pushes return-1 so the callee's RTS lands on `PC += 3`.
- Unrolled per-voice code: three ~800-byte copies with absolute addresses instead of `,X` indexing (saves the index juggling and 1 cycle per access; costs 1.6 KB). Only shared helpers take X = voice.
- Backward-copied blocks with `DEX/DEY/BPL` ($823D, $844F): the count-1 is the loop index, no compare.
- Signed 16-bit add via `TXA ADC lo TAX TYA ADC hi TAY` on register-held values (FCURR/PCURR in X/Y, $850E–$851C) — registers as accumulators for the whole segment loop; one `CLC` set once at $8507 and reused across the loop.
- Carry from CMP as arithmetic input: `CMP #$5E ; BEQ ; ADC TR` ($8332) — A<$5E guarantees C=0.
- Branch-to-shared-RTS: `$84AF LDX VRC ; BEQ $84AE` where $84AE is a lone `RTS`.
- Countdown-to-zero timers everywhere (`DEC ; BNE`), so 0 means "off" and a raw duration 0 means 256.
- Flag byte $F9 with bit-per-voice run/free bits tested by `LSR/BCC`, `AND #`, cleared through mask tables ($8D8F).
- Structs addressed by offset tables ($8D83/$8D86/$8D89) when a shared routine must reach voice X's record; the same S/D offsets are exposed to the song as `Moke/DMoke/FLoad` operands — the data format *is* the memory layout, and the duration table is reached as S2+$35.. (fload dst $44).
- Table-driven state machines: FM and PM are 2/4-segment piecewise-linear generators whose loop policy is two bits of the control byte.
- Zero page only for the pointers/counters that need `(zp),Y` or are touched every frame; all records in absolute memory.
- No illegal opcodes, no `JMP (ind)` except in Code, no stack tricks beyond the RTS call.

#### 3.2.7 What it reduces to

State per voice: PC (2), CLOCK, SP, S record (29 + 24 stack), D record (39), TR, two F9 bits, MFL bit = ~100 bytes; global: vol, 4 filter shadows. The player is: three independent byte-code interpreters (a tiny language: 2-byte notes, 15 commands with call/return/for-next over an 8-deep stack) that on each note copy an instrument image into a working record and program the SID; plus one per-frame engine per voice that runs three little segment machines (gate/release timer, pulse ramp, frequency ramp-or-arpeggio) and writes their outputs to the SID. Effects are the same engine fed a record image directly. Everything is decidable statically except: which subtune (A at init), the frame count, and — nominally — `Code` (never used). There is no volatile input; the sequencer is deterministic and periodic (subtunes 0–2 loop via `Jmp`).

The whole player:
```
each frame: write filter/vol; for v in 2,1,0:
  if run[v] and --clock[v]==0:
     repeat: b=*pc; if b>=$C0: dispatch(b) (call/ret/jmp/for/next/transpose/poke S or D/load S block/user code)
             else: note: if not rest and voice free: S->SID (with TEST pulse), S->D, freq[note+tr]->SID; clock=dur; break
  if D.vrc: gate timing (abs or step-relative); if 0 -> silence, free
            pulse ramp -> D402/3 ; freq ramp | arp | bend -> D400/1
```
Family variation: Wizball adds Master/Filter/Disown/MBend/Freq/Time commands, filter shadows written by song, computed durations, offset-list-by-pointer arps, 4×-per-frame refresh option; the engine, record layouts and command core are the same.

#### 3.2.8 Decompiler notes

- Code/data boundary: code is exactly $8000–$8C55 (plus glue); everything from $8C56 is data. Executed-PC coverage over all subtunes reaches every routine except `Code`, v0 `For/Next` and MusicTest; static descent from the vector tables ($800F, $8E50/$8E6E/$8E8C) closes the rest. Vector tables are the only indirect-control-flow roots; every JMP-patch site ($800C,$8323,$8646,$896A) takes its operand from one of them, so model each as `switch(index) over a constant word table` — the patched JMP is a variable, not code.
- The two `STA $D410,X` patches ($81F4/$81FF) are a per-call parameter (voice SID base) — model as a variable read by three sites; note the `LDX $81F4` read of the patched byte.
- Recover the per-voice struct by diffing the three code copies: every absolute operand that differs by $27 (D), $35 (S), 2 (ZP PC), $1E (vt) or 7 (SID) is a field access on the voice record; constants that differ as 1/2/4 or 8/$10/$20 are F9 bit masks. This folds 2.4 KB into one parameterised routine `voice(v)`.
- Type the sequencer bytes with the grammar in §4; commands are even bytes ≥ $C0 whose length is fixed per command; notes are 2 bytes. Follow Call/Jmp/CT/JT targets to enumerate all sequence data; `FLoad/load*` operands are the instrument table pointers (29/14/10/5-byte records) and one 16-byte load into S2+$35 is the duration table.
- Invariants: inside voice v's copy, Y is a sequence/block offset (0..$1E) and X is note/command/stack index; A is the note byte at $8326/$8649/$896D; the fetch loop's `JMP fetch` edges from handlers form one loop with a multi-way branch. `CLOCK==0 → step` and `VRC==0 → engine off` are the two guards that partition the per-frame behaviour.
- The engine's segment loops (`$8507`, `$859E`) are `while` loops with a re-entry via JSR transfer routines; treat transferpm/transferf as reload primitives, not calls with side effects elsewhere.
- Image state matters: S/D/IDRT initial values come from the file (§1 residual note); D.VRC=0 in the image is what keeps the residual FM/PM state silent, and `DMoke` can enable it. A decompiler must not zero the workspace.
- Traps: `RESTR dur=0` = 256 frames (DEC wrap); note $5E is transposed in arp mode but not otherwise (`$839C` vs `$8332`); wave-bit3 mode with wave&$F6==0 falls through into the absolute-gate path (`$84CF BNE`); ST2C[7] and IDRT[0] are the same byte; init writes $C008–$C00A/$C082 outside the tune (dead stores); reads of $05A0/$0BA0/$11A0/$17A0/$1DA0/$09A0 are `BIT` operand fetches, not data. Cycle counting is irrelevant (main tune: mean 211 instructions / 716 cycles per frame, worst frame 1258 / 4259 when several voices step and load instruments; single-speed, no raster dependence).

### 3.3 GoatTracker 2 (V2.73 export)


#### 3.3.0 Identity

| item | value |
|---|---|
| exemplar | `MUSICIANS/L/Linus/Je_suis_Linus_le_salaud.sid` (Linus/Camelot), PSID, 1 subtune, 2:15, 50 Hz VBI, single speed |
| load / init / play | $1000 / $1000 (`JMP $10FB`) / $1003 (`JMP $10FF`); image ends $2907 (6407 bytes) |
| code | $1000–$1449 = 1098 bytes; jump tables $144A–$1460 (23 bytes); variables $1461–$14E2 (130 bytes); song data $14E3–$2906 (5156 bytes) |
| executed | 433 distinct instructions in 3000 frames (+32 reachable-only: funktempo, orderlist REPEAT, orderlist LOOP, tick-0 fx $B/$C/$D/$E); 12 SMC sites (14 patched cells) |
| provenance | byte-for-byte an assembly of `player.s` "GoatTracker V2.73 playroutine" with a specific set of the ~45 conditional-assembly flags (list in §7). GT2's *relocator/packer* emits one such build per song, stripping features the song does not use, so no two GT2 SIDs need share the same code — but every one is a subset of `player.s` (or `altplayer.s`, the alternative SID-write-order variant). |
| comparison | `MUSICIANS/L/Linus/Do_It_Again.sid` ($AC00, 6000 frames traced): the *same* build plus two flags — `VOLSUPPORT` (third `JMP mt_setmastervol` at $AC06) and `NOAUTHORINFO=0` (32-byte "Linus/Camelot" text at base+$20 with the jump tables tucked in front of it at base+$09, and the tick-0 $D command's `cmp #$10 / bcs` timing-mark branch). Its ZP pair is $FA/$FB instead of $FC/$FD, its baked constants differ (`DEFAULTTEMPO`, `FIRSTNOHRINSTR`=$15, `mt_cscount`), otherwise instruction-for-instruction identical (425 executed). Je_suis executes more of the player (433 vs 425) and has the plainer layout, so it is the exemplar; nothing in DIA is absent from the account below except the two flags just named. |
| multi-player packs | Planet_X2_1 / Docsters_Digger / Blap_N_Bash / 1000_Kung-Fu_Maniacs are several separately packed GT2 players glued by a front-end init that patches a `JMP $xx00` per subtune (their traces show 8/3/2/… disjoint copies of this code, each stripped differently). Not one player; not used. |

#### 3.3.1 Memory map and state

Code (exemplar addresses; source label → [start,end]):

| routine | range | role |
|---|---|---|
| jump table | $1000–$1005 | `JMP mt_init` / `JMP mt_play` (no `mt_playsfx`, no `mt_setmastervol`) |
| mt_tick0_0..f | $1006–$1074 | 15 tick-0 command handlers, all in page $10 (needed: dispatch patches only the low byte of a JSR) |
| mt_effect_0/4/3/12, mt_freqadd/sub, mt_effect_3_found | $1075–$10FA | continuous (tick-n) effects |
| mt_init | $10FB–$10FE | `STA $110D ; RTS` |
| mt_play, mt_initsongnum, init | $10FF–$113F | ghost-register flush, init-pending check, song reset |
| mt_filtstep … mt_masterfader | $1140–$1199 | global filter program + volume → ghost $D416/17/18 |
| 3× `JSR mt_execchn` | $119A–$11A3 | X=0, 7, 14 |
| mt_execchn / mt_notick0 | $11A4–$11C2 | speed counter, funktempo |
| mt_repeat | $11C3–$11D3 | orderlist REPEAT |
| mt_tick0 … mt_nonewpatt … newnote init | $11D4–$1290 | sequencer step + note init |
| mt_nonewnoteinit / mt_waveexec | $1291–$12D5 | wavetable step |
| mt_wavedone / mt_setspeedparam / calculated speed | $12D6–$131F | continuous-effect dispatch |
| mt_wavefreq / mt_wavenote | $1320–$133B | note → ghost freq |
| mt_done = pulse program | $133C–$1381 | pulsetable step |
| gate-timer check | $1382–$138C | `counter == gatetimer → getnewnote` |
| mt_getnewnote … mt_rest | $138D–$140E | pattern byte decoder (the row fetch) |
| mt_loadregswaveonly | $140F–$1418 | `wave AND gate → ghost $D404+X ; RTS` |
| mt_nohr_legato | $1419–$141E | instrument class test |
| mt_execwavecmd | $141F–$1449 | wavetable command ($E0–$FF rows) |
| mt_tick0jumptbl / mt_effectjumptbl / mt_funktempotbl | $144A (16) / $145A (5) / $145F (2) | low bytes of handler addresses; funk pair |

Zero page: only `mt_temp1=$FC`, `mt_temp2=$FD` (a 16-bit scratch: sequence/pattern pointer for `(zp),Y`, or the 16-bit effect speed). No other ZP.

Per-voice state. X ∈ {0,7,14} is *simultaneously* the SID voice register offset and the record offset: variables are laid out as 7-field records, 3 records per block, so `name,X` addresses field `name` of voice X/7. Blocks in the exemplar (field k of voice v lives at blockbase+k+7v; the addresses below are voice 0):

| block | fields (offset within block: 0..6) → voice0 addr | written by | read by |
|---|---|---|---|
| A $1461 | songptr $1461, trans $1462, repeat $1463, pattptr $1464, packedrest $1465, newfx $1466, newparam $1467 | sequencer ($1207,$1216,$11C5), row fetch ($13BB..$140C) | tick0 |
| B $1476 | fx $1476, param $1477, newnote $1478, waveptr $1479, wave $147A, pulseptr $147B, pulsetime $147C | note init, tick-0 cmds, wave/pulse programs | effects, wave/pulse programs, loadregs |
| C $148B | songnum $148B (=voice number, constant), pattnum $148C, tempo $148D, counter $148E, note $148F, instr $1490, gate $1491 (mask $FE/$FF) | init, sequencer, execchn, row fetch | everywhere |
| D $14A0 | vibtime $14A0, vibdelay $14A1, wavetime $14A2, freqlo/hi $14A3/4 (unused: ghost mode), pulselo/hi $14A5/6 (unused) | effects/tables | effects |
| E $14B5 | ad/sr $14B5/6 (unused: ghost), sfx/sfxlo/sfxhi $14B7–9 (unused: no SOUNDSUPPORT), gatetimer $14BA, lastnote $14BB | tick0 ($121F), note set ($1327) | gate check ($1385), calculated speed ($12FD) |
| ghost SID image | $14CA–$14E2: voice regs at $14CA+X+0..6 (freqlo,freqhi,pwlo,pwhi,ctrl,ad,sr), $14DF cutoff-lo, $14E0 cutoff-hi, $14E1 res/route, $14E2 mode/volume | everything that "writes SID" | the 25-byte flush loop at $10FF |

Global (SMC immediates, see §6): init-pending/song $110D, filtstep $1141, filttime $1145, filtcutoff $118A, filtctrl $118F, filttype $1194, masterfader $1196, effectnum $10AC, vib speedcmp $1096, cscount $1310, csresty $131A; JSR/JMP low bytes $1289, $1295, $131E, $1445.

Song data (all tables 1-based: pointer 0 = "none", entry n at base+n-1, so code reads `base-1,Y`):

| table | address | size | element |
|---|---|---|---|
| freqtbllo / freqtblhi | $14E3 / $1543 | 96+96 | PAL frequency for note 0..95 (C-0=$0117 … B-7=$FFFF); `FIRSTNOTE`=0 (relocator may trim low octaves) |
| songtbllo/hi | $15A3 / $15A6 | 3+3 | address of orderlist per (song*3+voice) |
| patttbllo/hi | $15A9 / $15CA | 33+33 | address of pattern 0..32 |
| instrument | $15EB + 30·k | 9 columns × 30 rows | k=0 AD, 1 SR, 2 waveptr, 3 pulseptr, 4 filtptr, 5 vibparam (speedtable idx), 6 vibdelay, 7 gatetimer, 8 firstwave |
| wavetbl / notetbl | $16F9 / $175D | 100+100 | left: $00–$0F delay, $10–$DF waveform+$10, $E0–$FF command; $FF loop. right: note/param/jump |
| pulsetimetbl / pulsespdtbl | $17C1 / $17DE | 29+29 | left $80–$FF set pulse (hi nibble), $01–$7F step count, $FF jump; right lo byte / signed speed / target |
| filttimetbl / filtspdtbl | $17FB / $1826 | 43+43 | left $80–$FF set type+res/route, $01–$7F step count, $00 set cutoff, $FF jump; right value/speed/target |
| speedlefttbl / speedrighttbl | $1852 / $1864 | 18+18 | 16-bit speed (left=hi, right=lo) or, bit 7 of left set, "calculated" (right = shift count) |
| orderlists | $1875, $188B, $18A1 | 22 each | see §4 (note: $1875 is both speedright[18] and orderlist0[0]=$F0=TRANS+0 — the relocator overlapped the last unused speed byte with the song; harmless) |
| patterns | $18B7 … $2906 | 33 patterns, 54–203 bytes | see §4 |

#### 3.3.2 Entry points and conventions

- `init(A=song)`: `STA mt_initsongnum+1 ; RTS` — init only *schedules*; nothing touches SID until the first play. (With `NUMSONGS>1` the prologue is `sta mt_init+5 ; asl ; adc #$00` = A*3, an SMC multiply that indexes songtbl by song*3+voice.)
- `play()`: no arguments; clobbers A/X/Y; ends with `RTS` from the third `mt_execchn` (X=14) — the last `JSR mt_execchn`s are `JSR; LDX #$0E` fall-through into `mt_execchn` itself, so the third voice runs as a tail call and its RTS is play's RTS.
- Voice routine convention: X = voice*7 on entry to `mt_execchn`, preserved throughout (never modified inside; Y is the free index register). Every per-voice `,X` and every ghost `$14CA+X` relies on it. `mt_loadregswaveonly` at $140F is the single exit of every per-voice path (all paths `JMP $140F` or fall into it) and returns.
- Tick-0 command handlers ($1006–$1074) are called with A=param, X=voice, Y=don't care; return via RTS. Continuous-effect handlers are entered by `JMP` with X=voice, Y=speed-table index, $FC/$FD=16-bit speed, and A/Z as noted below; they end by `JMP $1339`/`$1327`/`$133C` (fall to `mt_done`).
- Flags as arguments across long distances (see §6): C from a `CMP` survives 4–8 later instructions to steer `BCS/BCC` or to be the +1 in `ADC #$00`/`SBC #imm` without `SEC`.
- No IRQ, no `$D012`/`$D41B` reads, no cycle dependence: pure `play` per frame. Multispeed in GT2 is external (the caller invokes play N× per frame; tempo values in the data are pre-multiplied by the editor).

#### 3.3.3 The play routine

```
play():
  for r in 24..0: SID[r] = ghost[r]                     # $10FF: previous frame's image, $D418 first, $D400 last
  X = 0
  if initpending >= 0:                                  # $110C imm ($FF = running)
      zero blocks A,B (42 bytes) ; ghost[$15]=0 ; filtctrl=0 ; filtstep=0 ; initpending=$FF
      for X in 0,7,14: tempo[X]=DEFAULTTEMPO(5) ; counter[X]=1 ; instr[X]=1 ; ghost[$04+X]=wave&gate
      return                                            # (voice2's JMP $140F ends in RTS = play's return)
  filter_program()                                      # $1140, global, → ghost $D416/$D417/$D418
  for X in 0,7,14: execchn(X)                           # third one is a fall-through tail

execchn(X):                                             # $11A4
  if --counter[X] == 0: goto tick0
  if counter[X] < 0:                                    # went past 0: reload
      t = tempo[X]
      if t < 2: tempo[X]=t^1 ; t = funktempo[t] - 1     # funktempo: alternate the two funk values
      counter[X] = t                                    # row length = tempo+1 frames
  goto waveexec                                         # ticks 1..n

tick0:                                                  # $11D4
  jsr_lo = tick0jumptbl[newfx[X]]  → patch both JSR operands ($1289,$1295)
  if pattptr[X] == 0: sequencer_step(X)                 # fetch next pattern number (§4)
  gatetimer[X] = insgatetimer[instr[X]]
  if newnote[X] != 0:                                   # a note was fetched gatetimer frames ago
      note[X] = newnote[X]-$60 ; fx[X]=0 ; newnote[X]=0
      vibdelay[X] = insvibdelay[i] ; param[X] = insvibparam[i]
      if newfx[X] != 3:                                 # toneportamento keeps everything running
          fw = insfirstwave[i]
          if fw != 0:  (fw < $FE: wave[X]=fw ; gate=$FF) else gate[X]=fw   # $FE = keep gate off, $FF = gate on
          if inspulseptr[i]: pulseptr[X]=it ; pulsetime[X]=0
          if insfiltptr[i]:  filtstep=it ; filttime=0
          waveptr[X] = inswaveptr[i]
          ghost[SR+X]=inssr[i] ; ghost[AD+X]=insad[i]
          tick0cmd[newfx](A=newparam[X])                # JSR through patched low byte
          goto loadregs                                 # NB: no wavetable step on the note's tick 0
  tick0cmd[newfx](A=newparam[X])                        # no new note: command, then fall into waveexec

waveexec:                                               # $1297
  y = waveptr[X]; if y == 0: goto wavedone
  w = wavetbl[y]
  if w < $10:  if wavetime[X] != w: wavetime[X]++ ; goto wavedone   # delay w frames on this row
  else: w -= $10 ; if w < $E0: wave[X] = w             # $10..$DF = waveform+$10 ; $E0.. = command
  waveptr[X] = (wavetbl[y+1] == $FF) ? notetbl[y+1] : y+1  # next row, or jump (target in right col of the FF row)
  wavetime[X] = 0
  if wavetbl[y] >= $E0: goto execwavecmd(cmd=w&$0F, param=notetbl[y])   # $141F
  n = notetbl[y]
  if n == 0: goto wavedone
  if n < $80: abs = n else abs = (n + note[X]) & $7F    # $80 = "current note", $8C = +12 …
  lastnote[X] = abs ; vibtime[X]=0 ; ghost[freq+X] = freqtbl[abs]
  goto done
wavedone:                                               # continuous effect
  effectnum = fx[X] (also patched into $10AC) ; jmp_lo = effectjumptbl[fx]
  y = param[X] ; (speed) = speedtbl[y]  (or calculated: (freq[lastnote+1]-freq[lastnote]) >> right)
  effect_{0,1,2,3,4}()                                  # each ends at done (or via storefreqhi)
done:                                                   # $133C
  pulse_program(X)                                      # §4, → ghost pw
  if counter[X] == gatetimer[X]: fetch_row(X)           # $138D: read next pattern row NOW (hard restart)
loadregs:  ghost[ctrl+X] = wave[X] & gate[X] ; RTS      # $140F
```

Sequencer step ($11E5–$1218):
```
p = orderlist[songnum[X]] ; y = songptr[X] ; b = p[y]
if b == $FF: y = p[y+1] ; b = p[y]                      # LOOPSONG: jump to position
if b >= $E0: trans[X] = b-$F0 ; b = p[++y]              # TRANS  ($E0..$FF = -16..+15)
if b >= $D0: n=b-$D0 ; if ++repeat[X] != n: goto nonewpatt (replay pattnum, songptr unchanged)
             else repeat[X]=0 ; goto advance             # REPEAT: preceding pattern plays n times total
pattnum[X] = b
advance: songptr[X] = y+1
```

Row fetch ($138D–$140E, once per row, *gatetimer frames before* the row's tick 0):
```
p = pattern[pattnum[X]] ; y = pattptr[X] ; b = p[y]
if b < $40:  instr[X] = b ; b = p[++y]                  # instrument change (never 0: 0 = end, caught below)
if b < $60:  newfx[X] = b&$0F ; if newfx: newparam[X] = p[++y]
             if b >= $50: goto rest                     # FXONLY: no note this row
             b = p[++y]
if b >= $C0: (first time) packedrest[X] = b+1 (C=1) else packedrest[X]++ ;   # $C0+n = 256-n rows of rest
             if packedrest==0: goto rest (advance) else goto loadregs (pointer stays)
if b == $BD: goto rest                                  # REST
if b >  $BD: gate[X] = b | $F0 ; goto rest              # $BE keyoff → $FE, $BF keyon → $FF
newnote[X] = b + trans[X]
if newfx[X] == 3: goto rest                             # toneportamento: no gate off / HR
if instr[X] < FIRSTNOHRINSTR ($18): ghost[SR+X]=SRPARAM($00) ; ghost[AD+X]=ADPARAM($0F)   # hard restart
if instr[X] < FIRSTLEGATOINSTR ($18): gate[X] = $FE     # gate off now (legato instruments: no HR, no gate change)
rest:  y++ ; pattptr[X] = (p[y] == 0) ? 0 : y           # 0 = pattern end → sequencer at next tick 0
```

Timing consequence (verified in trace, voice 0): fetch at frame 5 (counter==2), ghost image with AD=$0F SR=$00 gate off reaches SID at frames 6–7, tick 0 at frame 7 sets firstwave $09 (test+gate) + instrument ADSR, SID sees it at frame 8; wavetable row 1 ($51, note) and the new frequency at frame 9. So a GT2 note = 2 frames HR, 1 frame test-bit "click", then the wavetable — all a byproduct of the one-frame ghost latency plus "no wave step on tick 0".

Filter program ($1140–$1199, global; runs before the voices):
```
y = filtstep ; if y == 0: goto flush
if filttime != 0: cutoff += filtspd[y] ; if --filttime: goto flush ; else goto next
t = filttime_tbl[y]
if t == 0:  cutoff = filtspd[y] ; goto next                          # SETCUTOFF
if t < $80: filttime = t ; cutoff += filtspd[y] ; if --filttime: goto flush ; goto next   # modulation step
else: filttype = t<<1 ; filtctrl = filtspd[y] ; if filttime_tbl[y+1] != 0: goto next2 ; y++ ; cutoff=filtspd[y]   # SETFILTER (+optional immediate cutoff row)
next:  filtstep = (filttime_tbl[y+1]==$FF) ? filtspd[y+1] : y+1
flush: ghost[$D416]=cutoff ; ghost[$D417]=filtctrl ; ghost[$D418]=filttype|masterfader
```
Pulse program ($133C–$1381) is the same shape per voice: left byte ≥$80 → set pw (hi=left&$0F… stored raw, lo=right), <$80 → step count with signed speed in right byte added to the 16-bit pw each frame (`bpl pulseup / dec hi` for negative), $FF → jump; the pointer advances only when a step count expires or after a set.

#### 3.3.4 Data formats

**Orderlist** (per voice, per song): bytes `00–CF` pattern number; `D0+n` REPEAT (preceding pattern n times total, $D0 = 256); `E0–FF` TRANS = value−$F0 semitones, applies to following patterns; `FF pos` LOOPSONG. Exemplar: 3 lists of 22 bytes, each `F0 <patterns…> F1 <patterns…> FF 00`; REPEAT and LOOP never executed in 3000 frames (LOOP fires at 2:15).

**Pattern** row grammar: `[instr 01–3F] [fx 40–4F param | fxonly 50–5F param] (note 60–BC | rest BD | keyoff BE | keyon BF) | packedrest C0–FF ; end 00`. Effect 0 carries no param byte (`beq mt_fx_noparam`). Note = byte−$60 → 0..92 (+trans) → freqtbl index. In the exemplar: 1487 notes ($6B..$B0), 314 rests, 25 keyoffs, 75 packed rests, instruments 1..30 all used, fx counts {0:437, 1:33, 2:34, 3:83, 4:26, 5:4, 7:58, 8:17, 9:4, A:23, F:336}. Rows are consumed by *pointer* (`pattptr` = byte index of the next row, 0 = "at end"): the decoder reads the row's leading bytes with `INY`s and stores the index of the byte after it; end is detected by look-ahead (`p[y]==0` after a row).

**Commands** (nibble; pattern fx or wavetable $Ex/$Fx). Tick-0 class (jump table $144A, handlers $1006–$1074, all `RTS`): 0 = restart instrument vibrato (param=insvibparam), 1/2 = start portamento (vibtime=0, then as 3/4), 3/4 = toneporta/vibrato: `param[X]=A ; fx[X]=newfx[X]`, 5 AD→ghost, 6 SR→ghost, 7 wave[X]=A, 8 waveptr=A (wavetime=0), 9 pulseptr=A (pulsetime=0), A filtstep=A (filttime=0), B filtctrl=A (0 also stops filtstep), C cutoff=A, D mastervol=A (or `≥$10` timing mark → author+31 when NOAUTHORINFO=0), E funktempo: `funktempotbl[0..1] = speedtbl[A]`, tempo=0 → global, F tempo: bit7 → this voice only, else all three (`sta tempo ; sta tempo+7 ; sta tempo+14`). Continuous class (jump table $145A, `JMP` low byte patched at $131E): 0 instrument vibrato after vibdelay, 1/2 portamento up/down (freq ± 16-bit speed each tick), 3 toneportamento (move toward freqtbl[note] by speed, snap when the sign of the remaining offset flips; speed index 0 = tie/instant), 4 vibrato (see below). Only 1..4 persist in `fx`; 0 is the resting state after every new note.

**Vibrato** (`mt_effect_4`, $1082): speed entry left byte & $7F = `speedcmp` (patched into `CMP #imm` at $1096), right byte = depth (16-bit if bit 7 of left set = calculated). `vibtime` is an 8-bit phase: `+2` each tick; when it exceeds speedcmp it is complemented (`EOR #$FF`) which flips bit 0; `LSR` then makes bit 0 select add (`freqadd`) or subtract (`freqsub`). Result: triangle of ±depth·steps, period 2·(speedcmp+2) frames after the first half.

**Speed table** entry: left bit7=0 → normal 16-bit speed (left=hi, right=lo); bit7=1 → *calculated*: speed = (freq[lastnote+1] − freq[lastnote]) >> right (a semitone fraction, so vibrato/porta scale with pitch). Computed by `mt_calculatedspeed` ($12F4) with the shift count patched into `LDY #imm` ($1310) and Y restored from `LDY #imm` ($131A).

**Instrument**: 9 parallel byte columns (AD, SR, waveptr, pulseptr, filtptr, vibparam, vibdelay, gatetimer, firstwave). Instrument *classes* are ranges of the instrument number baked into `CMP #imm`: 1..FIRSTNOHRINSTR−1 hard restart; FIRSTNOHRINSTR..FIRSTLEGATOINSTR−1 no HR but gate off; ≥FIRSTLEGATOINSTR legato (no HR, gate untouched, wave untouched: `firstwave $FF` = "skip wave, gate on"). Exemplar: 23 HR + 0 noHR + 7 legato (24..30, all `fw=$FF`).

**Wavetable**: left `00–0F` = delay that many frames on this row (no wave change), `10–DF` = waveform+$10 (so $11 = triangle+gate, $51 = pulse+tri+gate, $19 = tri+test+gate…), `E0–FF` = command (low nibble, param in right column; ≥5 = tick-0 command via `mt_execwavecmd`, <5 = continuous effect from this row on), `FF` = jump to right column (0 = stop). Right column for waveform rows: `00` no frequency change, `01–7F` absolute note, `80–FF` relative: (note[X] + v) & $7F ($80 = same note, $8C = +12, $FF = −1). Exemplar rows: e.g. `21 80 / 51 80 / 31 80 / 51 80 / FF 00` (a 4-frame attack then stop), drum `91 5A / 51 2D / 64 35 / 90 5A / 20 2B / F1 01 / FF 00` (noise/absolute notes then portamento cmd then stop).

**Pulse table**: left `80–FF` = set pw hi (stored as-is into ghost pwhi, so $88 → $0800 with right = lo), `01–7F` = repeat count, right = signed speed added to 16-bit pw per frame, `FF` = jump (right = target). **Filter table**: left `80–FF` = set mode: `filttype = (left<<1)` (so $98 → $30 = band+low… value pre-shifted by the editor) and `filtctrl = right` (res<<4|route), optionally followed by a `00 cutoff` row consumed the same frame; `00 v` set cutoff; `01–7F` = repeat count with signed speed; `FF` = jump. Filter is global (one program) but instruments and voice commands may (re)start it.

**Frequency table**: 96 entries lo/hi, PAL, `FFFF` at B-7. Toneporta compares 16-bit; calculated speeds subtract adjacent entries.

#### 3.3.5 SID write schedule

In this build (GHOSTREGS=1, ZPGHOSTREGS=0) exactly 25 writes per play call, always, first thing: `LDX #$18 ; LDA $14CA,X ; STA $D400,X ; DEX ; BPL` — order $D418, $D417, …, $D400 (per voice: SR, AD, CTRL, PWhi, PWlo, FREQhi, FREQlo). Everything else in the frame writes the ghost image only, which the SID sees one frame later. Filter/volume are recomputed every frame (`filtcutoff`/`filtctrl`/`filttype|vol` immediates → ghost $14E0–$14E2), ghost $14DF (cutoff lo) is zeroed at init and never touched, so $D415 is written 0 every frame. Gate on/off is `wave AND gate` written to ghost ctrl on *every* voice exit ($140F). Hard restart = writing AD=$0F, SR=$00, gate mask $FE into the ghost when the row is fetched, i.e. gatetimer (=2 here) frames before tick 0, then the note's tick 0 writes new AD/SR + firstwave (with test bit $08 set in `$09`) — the SID sees 2 frames of HR, 1 frame test+gate, then the wavetable. `altplayer.s` differs only in order: flush loop ascending ($D400 first), wave written before AD/SR at note init and before frequency in `mt_loadregs`, AD before SR in HR — a different register order for the unbuffered/buffered modes where the writes are inline. Other GT2 builds (BUFFEREDWRITES=0) write inline: `mt_loadregs` = freqlo, freqhi, wave (every voice, every frame); AD/SR at note init/HR/commands; pw in the pulse program; filter regs directly in `mt_filtstep`; BUFFEREDWRITES=1 without ghosting writes AD, SR, PWlo, PWhi, FREQlo, FREQhi, CTRL per voice at `mt_loadregs`. No reads of any SID/VIC register in any variant.

#### 3.3.6 Techniques specific to this player

| technique | citation | why |
|---|---|---|
| X = voice*7 = SID offset = record offset | `$119A JSR $11A4 / LDX #$07 / JSR / LDX #$0E` then `,X` everywhere; 7-field blocks at $1461/$1476/$148B/$14A0/$14B5; ghost `$14CA,X` mirrors `$D400,X` | one index register serves SID addressing and all per-voice state; no multiply, no pointer |
| tail-call fall-through | `$119F JSR $11A4 ; $11A2 LDX #$0E ; $11A4 mt_execchn` — voice 2 is executed by falling into the subroutine; its RTS returns from play. Same for `mt_initchn` at $1130 | saves a JSR/RTS pair |
| single exit block | every voice path ends `JMP $140F` (`wave AND gate → ghost ; RTS`) | ctrl register always refreshed; one RTS |
| SMC: immediate as variable | `LDY #$FF`@$110C init-pending/song, `LDY #imm`@$1140 filtstep, `LDA #imm`@$1144/$1189/$118E/$1193, `ORA #imm`@$1195 volume, `CMP #imm`@$1095 vib speed, `LDA #imm`@$10AB effect number, `LDY #imm`@$130F/$1319 | globals live inside the instruction: 2 bytes/2 cycles instead of 3/4; the filter and volume are global so no `,X` needed |
| SMC: multiply | `mt_init`: `sta mt_init+5 ; asl ; adc #$00` (NUMSONGS>1) | A*3 in 6 bytes |
| SMC: dispatch by patching the low byte of a JSR/JMP | `$11D7 LDA $144A,Y ; STA $1289 ; STA $1295` then `$1288 JSR $10xx`; `$12DC LDA $145A,Y ; STA $131E` then `$131D JMP $10xx`; `$143C..$1444` | jump table of 1 byte per entry, one STA; forces all handlers into one page (a relocation constraint the relocator honours) |
| 1-based table pointers | `waveptr==0` → `BEQ` skip; every table read `base-1,Y` | zero doubles as null; the test is free (`LDY` sets Z) |
| gate as AND-mask | `gate` ∈ {$FE,$FF}; `$140F LDA wave,X ; AND gate,X`; `mt_gate: ORA #$F0` turns KEYOFF $BE/KEYON $BF straight into the mask | no branch on gate state; the two note codes were chosen to make ORA the whole conversion |
| three-way branch on one DEC | `$11A4 DEC counter,X ; BEQ tick0 ; BPL effects ; (negative → reload)` | tick-0/tick-n/reload in 6 bytes |
| carry as long-lived argument | `$13C5 CMP #$50 ; AND #$0F ; STA ; BEQ ; INY ; LDA ; STA ; $13D4 BCS rest` (C set 5 insns earlier); `$13A7 CMP #$C0 … $13B2 ADC #$00` (+1 = "first time" flag from the same CMP); `CMP #$FF ; INY ; TYA ; BCC` in all three table steppers; `SBC #$10/#$D0/#$F0` after `CMP ; BCS/BCC` without `SEC` | the comparison already left the flag; reuse instead of recompute |
| known-flag unconditional branch | `LDA #$00 ; BEQ` ($1061,$12C5→…), `BNE` after `INC` ($12AB), `$1146 BNE` after `LDA #imm` | 2-byte branch instead of 3-byte JMP where the flag is known |
| shared tails | `mt_effect_12`→`mt_freqadd/sub`; `mt_effect_3_found` → `mt_wavenoteabs`; `mt_repeatdone2` inside sequencer; `mt_nonewnoteinit` inside tick0; `mt_setcutoff2` (INY into setcutoff) | code size |
| PHA/PLA to hold a 16-bit intermediate | `$10BC PHA … $10C4 PLA` (toneporta offset lo while hi computed) | A/X/Y all busy |
| lookahead terminator | `$1406 INY ; LDA (p),Y ; BEQ → pattptr=0` | end-of-pattern detected while leaving the row so `pattptr==0` can mean "need sequencer" |
| pre-shifted / pre-decremented constants in data | filttype stored >>1 in the table (`ASL` at $114F); tempo stored as frames−1 (`DEFAULTTEMPO`=5 = 6 frames/row); waveform stored +$10 | moves work from runtime to the editor |
| ghost image flush loop | `$10FF LDX #$18 ; LDA $14CA,X ; STA $D400,X ; DEX ; BPL` | fixed 25-write frame at the very start = jitter-free SID timing regardless of the frame's work |
| `LDY abs,Y` in source | source writes `ldy mt_chnfx,y` etc.; there is no such opcode, the assembler emits `LDY abs,X` ($BC) | trivia for anyone reading player.s against a binary |

No illegal opcodes, no stack tricks beyond PHA/PLA, no `JMP (ind)`, no RTS-dispatch, no play-time opcode patching (all SMC is operand bytes).

#### 3.3.7 What it reduces to

The whole player is: 3 × 35 bytes of per-voice state (blocks A–E) + 12 global bytes (filter/volume/init immediates) + a 25-byte SID image, advanced once per frame by a fixed procedure whose only "control state" is `counter` (frames until the next row) and three 1-based cursors (waveptr, pulseptr, filtstep) into three tiny bytecode tables. Per voice per frame: (1) count down; on tick 0 decode one row (already fetched) into `note/instr/newfx/newparam` and load the instrument; (2) step the wave program (one row/frame, delays and jumps) → ctrl + freq; (3) run one of five effects on freq; (4) step the pulse program → pw; (5) `gatetimer` frames before the next tick 0, decode the next row and pre-load hard restart. Filter is one more such stepper, global. Then copy the image to SID.

Statically decidable: everything except the song position (all table pointers, jump targets, instrument classes are constants in the data image; the two jump tables have 16+5 fixed targets; the SMC cells hold known-domain values: filter/volume bytes, 0..4 effect number, 0..N table indices, $FF/song). SMC never encodes code choice at play time except through the 21 low-byte targets. Nothing volatile: play is a pure function of (state, data). Dead in this song: funktempo ($1052, $11B2), REPEAT ($11C3), LOOP ($11FB, fires after 3000 frames), tick-0 $B/$C/$D. Between tunes only the flag subset, constants and base addresses vary; the code fragments themselves are fixed strings from `player.s`.

Flags of the exemplar (evidence): NUMCHANNELS=3 (`LDX #$29`=3·14−1, three `JSR mt_execchn`), NUMSONGS=1 (`mt_init` is `STA;RTS`, no `chnsongnum` store in initchn), SOUNDSUPPORT=0/VOLSUPPORT=0 (2-entry jump table, no sfx code), NOAUTHORINFO=1 (code at base+6, jump tables after code at $144A), GHOSTREGS=1/ZPGHOSTREGS=0/BUFFEREDWRITES=1 (flush loop, ghost at $14CA, tick0_5/6 store to $14CF/$14D0), zpbase=$FC; and ALL of NOEFFECTS, NOINSTRVIB, NOVIB, NOTONEPORTA, NOPORTAMENTO, NOCALCULATEDSPEED, NONORMALSPEED, NOSETAD/SR/WAVE/WAVEPTR/PULSEPTR/FILTPTR/FILTCTRL/FILTCUTOFF/MASTERVOL, NOWAVEDELAY, NOFILTERMOD, NOFUNKTEMPO, NOCHANNELTEMPO, NOGLOBALTEMPO, NOWAVECMD, NOFIRSTWAVECMD, FIXEDPARAMS, NOPULSE, NOPULSEMOD, SIMPLEPULSE, NOFILTER, NOTRANS, NOREPEAT, NOGATE, PULSEOPTIMIZATION, REALTIMEOPTIMIZATION = 0 (each corresponding block present at the addresses in §1); NUMHRINSTR=23, NUMNOHRINSTR=0, NUMLEGATOINSTR=7 (`CMP #$18` at $13F3 and $1419), ADPARAM=$0F/SRPARAM=$00 ($13F7–$13FE), DEFAULTTEMPO=5 ($1130), FIRSTNOTE=0. I.e. the full player minus sfx/volume-API/author text/multi-song — as complete a single-SID GT2 build as the packer produces.

Full-source features absent here (documented for the family): `mt_playsfx(A/Y=addr,X=voice)` sound effects with address-priority, sfx stream = AD, SR, PW, then `note($80+)` [`wave`]… `00`; `mt_setmastervol`; NUMSONGS>1 (song*3 index, `chnsongnum` per voice); ZP ghost image (`ghostregs` in ZP, `mt_temp` at zpbase+25/26); unbuffered/buffered inline SID writes; PULSEOPTIMIZATION (skip pulse on rows the sequencer ran) and REALTIMEOPTIMIZATION (no continuous effects on tick 0); FIXEDPARAMS (constant gatetimer/firstwave); NUMCHANNELS 1–2 (unused voices zeroed at init).

The player in ~30 lines:
```
play: SID[0..24]=ghost ; if init: reset ; return
      filter: step global {set|mod|jump} program → ghost[16..18]
      for v in 0,7,14:
        if --cnt[v]==0:  (tick0)
            if pattptr[v]==0: pattnum[v]=orderlist step (trans/repeat/loop)
            gatetimer[v]=ins.gatetimer
            if newnote[v]: load instrument (wave/gate/waveptr/pulseptr/filtptr/AD/SR); tick0cmd(newfx,newparam); goto out
            tick0cmd(newfx,newparam)
        elif cnt[v]<0: cnt[v]=tempo[v] (funk alternation)
        wave step: delay | ctrl=w-$10 | command ; freq=table[abs or note+rel] ; jump/stop
        effect fx[v] ∈ {instvib, porta±, toneporta, vibrato} on ghost freq
        pulse step: set | ±speed×n | jump → ghost pw
        if cnt[v]==gatetimer[v]: fetch row → instr/newfx/newparam/newnote ; HR (AD 0F SR 00, gate $FE) unless legato/toneporta
   out: ghost[ctrl+v] = wave[v] & gate[v]
```

#### 3.3.8 Decompiler notes

- **Code/data boundary**: code is a prefix from base to the byte before the (16,5,2)-byte jump tables (or, with author info, tables sit at base+$09..base+$1F and code resumes at base+$40); data begins right after the last variable block/ghost image and is fully described by the pointer tables (`songtbl`, `patttbl`) plus fixed-stride columns. Every table base appears literally as `abs-1` operands (`LDA $16F8,Y` ⇒ wavetbl=$16F9), so table bases can be recovered from `,Y` operands with the +1 rule; column count of the instrument block = the common stride between the nine `abs-1,Y` operands in the note-init sequence ($1235…$127F: stride $1E).
- **Per-voice struct**: any `abs,X` with X ∈ {0,7,14} is field (op−blockbase) of a 7-field record; group the operands by ⌊(op−lowest)/7⌋ … concretely: collect all `,X` operands executed with X ∈ {0,7,14}, sort, and split into 7-byte-stride blocks; the ghost block is the one also indexed as `$D400,X`. Fields are bytes; there are no 16-bit fields except freq/pw pairs (lo at +k, hi at +k+1) inside the ghost/D blocks.
- **Typing the tables**: 1-based, Y-indexed, read at `base-1,Y`; parallel columns share the index (wave/note, time/speed, left/right). Row semantics are exactly the three-way byte tests in the steppers (`CMP #$10/#$E0/#$FF`, `BPL`, `BEQ`).
- **Sequencer bytes**: orderlist and pattern grammars in §4 are decided by `CMP` thresholds that are constants of the source (`$40 $50 $60 $BD $C0 $D0 $E0 $F0 $FF`); a decompiler can lift the row decoder to a tokenizer with those thresholds and treat `pattptr` as a byte cursor whose 0 is the sentinel.
- **SMC**: 11 immediate cells = global variables (name them; their writers are single `STA abs`), 4 jump-operand cells = indirect calls through the two low-byte tables (target set = table contents, all in the code page); `mt_init+5` (when present) = a computed constant. No opcode patching; treat `PATCHED` immediates as memory reads.
- **Conventions to model**: X invariant (voice*7) inside execchn and its callees; Y is scratch; A carries the command parameter into tick-0 handlers; C carries a boolean across up to ~8 instructions (list in §6) — a decompiler that recomputes flags per instruction is fine, one that assumes flags die at the next ALU op is not (`AND`, `INY`, `TYA`, `STA`, `LDA` preserve C). Handlers end in RTS; effect handlers are `JMP`-entered and `JMP`-exited (treat as goto within one function, not calls). `mt_execchn` is one function with three entry points reached by fall-through (mt_execchn) — the third `JSR` is really `JSR;JSR;fallthrough`.
- **Frame model**: one play = 25 SID writes from the ghost image, so the observable output of frame n is the image computed in frame n−1; the model can be "compute image, then flush", with the HR/test-bit/wave sequence emerging from the row-fetch offset (`gatetimer`) rather than from any explicit timer.
- **Traps**: (1) `LDY abs,X` where the source says `,y`; (2) the ghost image *is* SID layout — a decompiler that treats $14CA+X writes as SID writes with a one-frame delay is exact; (3) `mt_effect_0`'s `BEQ` tests Z left by the last speed-table load — with vibparam=0 that reads the byte *before* the table (`$1851`/`$1863`), so the "no vibrato" decision depends on data adjacent to the table (relocator keeps it 0); (4) `$1875` byte shared by speedright[18] and orderlist 0; (5) `mt_tick0_e` uses `LDA #$00 ; BEQ` and `mt_tick0_f` `BMI` on the *param* → sign of the parameter selects channel/global tempo; (6) `mt_packedrest` leaves `pattptr` unchanged while counting, so a decompiler tracking "row consumed" must key on the packedrest counter reaching 0; (7) all `SBC #imm` after `CMP` rely on C=1 from the compare — no `SEC`; (8) instrument classes and `ADPARAM/SRPARAM/DEFAULTTEMPO/FIRSTNOTE` are per-tune constants inside instructions, so signature matching must wildcard those immediates (as SIDId does).

### 3.4 SID Wizard (Hermit) — Emomyst (SW 1.6) and End of the World (SW 1.9)


Exemplars: **Emomyst** (Hermit, 2016; SW 1.6 export; primary) and **End of the World** (Hermit, 2022; SW 1.9 export; secondary). Same author as the player. Ground truth cross-referenced with the published SID Wizard `player.asm` (3040 lines, ~45 `feature.*` conditional-assembly switches) — the binaries map onto it instruction for instruction; every label below (`DOTRACK`, `WFARPTB`, `SEQSUB`…) is the source label.

Why Emomyst is primary: it exercises more of the *sound engine* — chords (2341 chord-row executions), arpeggio-speed, all four vibrato types, PW keyboard-tracking (2658 frames), filter keyboard-tracking (5450 frames), 8 small-FX types, instrument-column FX 4/5/6/7 — and its build carries the SFX and "slowdown" variants (dead in the tune but present). End of the World exercises the *sequencer* extras (orderlist transpose, tempo big-FX, subtune support, multispeed entry, filter shift, fine filter sweep, all 14 small-FX). Both are single-SID "normal" players; the two together cover the family. Every address below is Emomyst unless marked `[EOTW …]`.

#### 3.4.0 Identity

| | Emomyst | End of the World |
|---|---|---|
| file | `MUSICIANS/H/Hermit/Emomyst.sid` | `MUSICIANS/H/Hermit/End_of_the_World.sid` |
| load / init / play | $1000 / $1000 / $1003 (PSID play, single speed) | $2900 / $34F0 (stub `LDA #0;LDX #0;LDY #$35;JMP $2900`) / $2903 |
| header text | `SIW-WIZARD 1.6 SWP-MUSICDATA PLAYER`, tune-header magic `SWM1`, data magic `SWP1` @ $1C00 | `SID-WIZARD 1.9`, `SWP1` @ $3500 |
| image | $1000–$24A1 (5282 B): player $1000–$1BCF, music $1C00–$24A1 | $2900–$4118 (6169 B): player $2900–$34F8, music $3500–$4118 |
| code | 1918 B executed + 457 B reachable-only (859 + 208 insns) | 1807 + 592 B (806 + 286 insns) |
| per frame | ≈420 insns (2 522 092 / 6000) | ≈323 |
| subtunes | 1 (SUBTUNESUPPORT off: byte $1012 = $00) | 1 (support on: byte $290F = $80) |
| multispeed | off (`$1006 JMP $124C` = `RTS`) | on (`MULPLY` @ $2AA2, unused; FRAME_SPD byte $2910 = 1) |
| build flags (from binary) | SWP export, SLOWDOWN_SUPP, SFX_SUPPORT, ZEROPAGESAVE off (uses $02/$03), CALCVIBRATO, CHORDS, ARPSPEED, PW+filter kbtrack, FINEFILTSWEEP, HARDRESTYPES, FRAME1SWITCH, GATEOFFPTR, TEMPOPRG, SEQ_FX, TRANSPOSE, PACKEDNOP, VIBRATOTYPES, DETUNE, WFARP_NOP; **off**: MULTISPEED, FILTSHIFT, DELAY, FILT_CTRL_FX, VIBFREQFX, FILTER_SMALLFX, DETUNE_SMALLFX, WFCTRL_SMALLFX, FASTSPEEDBIND | SWP export, ZEROPAGESAVE ($FE/$FF pushed/popped), SUBTUNES, MULTISPEED, FILTSHIFT, FILT_CTRL_FX, all small-FX; **off**: SLOWDOWN, SFX, DELAY, FASTSPEEDBIND |
| SID reads / $D012 | none | none |
| SMC sites | 59 patched instructions (32 init-time relocation, 27 play-time immediates/branch operands) | 62 (36 / 26) |

"SWP" = the exporter emits the player and the *position-independent* music blob separately; music-data pointer tables hold offsets, and `INITER` adds the blob base (X/Y on entry) into every table-base operand at init (§6.1). SW also ships light/medium/extra single-SID builds and 2SID/3SID builds (the same code duplicated per chip with `cpx #7*3` fan-outs); not analysed.

#### 3.4.1 Memory map and state (Emomyst; EOTW = same layout at base $2900+, deltas noted)

##### 3.4.1.1 Player region

| range | content |
|---|---|
| $1000–$1011 | jump table: init, play, MULPLY(`RTS`), volume (`STA MAINVOL+1`), SFXinit ($1167), setSlowdown ($1193) `[EOTW: 5 entries, no slowdown]` |
| $1012–$1013 | SUBTUNESUPP flag, FRAME_SPD |
| $1015–$101B | SWP init stub `LDX #0; LDY #$1C; JMP $10A5` (overwrites the tracker-ID string) |
| $101C–$1023 | rest of ID string / padding |
| $1024–$108C | **VARIABLES** (105 B = 5 bunches × 3 voices × 7); tune header is copied here by the exporter and zeroed by init |
| $108D–$10A1 | CONST_VAR: 3×7 constants/vars (see 1.3) |
| $10A2–$10A3 | SWP_OFFSET (music blob base, written by init) |
| $10A4 | SLOWDCNT `[EOTW: absent]` |
| $10A5–$1162 | INITER (SWP fixup loop $10BA–$111C, SETSTUNE call, zeroing, SID clear, INITPTN loop $1143–$1160) |
| $1163 | `seq_ptr: LDA $xxxx,Y; RTS` helper (SWP no-subtune) |
| $1167–$118F | SFXinit; $1190–$1192 SFXleng/SFXtimer/CURINStemp; $1193–$11A9 setSlowdown; $11AB–$11C3 slDither table |
| $11C4–$124B | PLAYER: slowdown gate, SFX check, `LDX #14/7/0; JSR DOTRACK`, COMMONREGS ($1201–$124B) |
| $124D–$1272 | DOTRACK: tempo/tick select |
| $1275–$138C | TICK_0 (READROW $1289–$12EB, SFX suppress $12EB, HARDRST $130E–$137A, HRENDER $1378) |
| $138F–$13E8 | CHKTCK1/TICK_1: PTN_SEQ orderlist advance |
| $13EB–$151D | TICK_2: instrument select, CHKNOTE, CLEGATO, STRTSND, SETADSR |
| $1520–$1536 | CNTPLAY (instrument pointer) |
| $1537–$15D0 | VIBSLIDE (portamento/slide/vibrato) |
| $15D1–$1664 | FILTPRG (flprog1) |
| $1665–$16D5 | SETPWID |
| $16D6–$1775 | WFARPTB incl. chords ($1729–$175D) |
| $1776–$17C0 | WRPITCH (slowdown form) + WRWFGHO + RTS |
| $17C1–$17C7 | SETSTUNE (no-subtune form: copy tempo) |
| $17C8–$17D9 | SEQSUB (3-way orderlist reader) |
| $17DA–$182F | SETVIB0/1/R + SETFMOD (calculated vibrato/slide amount) |
| $1830–$1938 | tables: LOGTBL $1830 (16), ADSR_OFFS $1840 (16), ADSR_EXPTB $1850 (14 zeros), EXPTABH $185E (10 zeros), $1868 zero, FREQTBH $1869 (96) + kbtrack slope $18C9 (8), FREQTBL $18D1 (96), NOTEFXTBL $1931 (8) |
| $1939–$19AA | NOTE_FX (note-column FX $60–$7F) |
| $19AB–$19D2 | INSPTFX / PATT_FX / BIGPTFX dispatcher |
| $19D3–$19F1 | SETINBL/SETNIBL/SETINBH/SETNIBH/MULTI3C helpers |
| $19F2–$19FF | SMALLFXTBL (14 branch offsets) |
| $1A00–$1A6F | SMALPFX + small-FX handlers |
| $1A70–$1AAD | BIGFXTABLE (31 words) |
| $1AAE–$1B54 | big-FX handlers |
| $1B55–$1B91 | PtrValu (slot,addend pairs), $1B92–$1BCF DataPtr (addresses of the 30 patched operands), 0-terminated |

`[EOTW]` same order at $2900: VARIABLES $2921–$2989, CONST_VAR $298A–$299E, SWP_OFFSET $299F, INITER $29A1, PLAYER $2A5D, COMMONREGS $2A72, MULPLY $2AA2, DOTRACK $2AE6, TICK_0 $2B0E, TICK_1 $2C0E, TICK_2 $2C81, CNTPLAY $2D8A, VIBSLIDE $2DA1, FILTPRG $2E3B, SETPWID $2ECF, WFARPTB $2F40, WRPITCH $2FE6, SETSTUNE $2FFE (subtune form), SEQSUB $3047, SETVIB $3059, EXPTABH $30A9, FREQTBH $30B4, FREQTBL $311C, SEQ_FX $317C, NOTE_FX $31A2, INSPTFX $3214, SMALPFX $3269, BIGFXTABLE $32FA, big-FX $3338–$33F7, PtrValu $33F8, DataPtr $343B.

##### 3.4.1.2 Music blob (SWP1, base $1C00; offsets are blob-relative)

| Emomyst | EOTW | table | format |
|---|---|---|---|
| $1C00 | $3500 | `"SWP1"` + 9 words | offsets of: SUBTUNES, PPTRLO, PPTRHI, INSPTLO, INSPTHI, CHORDS, CHDPTRLO, TEMPOTBL, TEMPTRLO (slots 4..$14 of PtrValu) |
| $1C40/$1C4E/$1C5C | $3540/$3550/$3560 | orderlists tr1/2/3 | bytes, §4.1 |
| $1C6A–$218E | $3570–$3DBC | patterns 1..20 / 1..30 | §4.2, each ends `$FF,len` |
| $218F–$2424 | $3E0B–$40B0 | instruments 1..20 | variable length, §4.3 |
| $2425 | $409E | CHORDS | signed semitone bytes, `$7F` loop, `$7E` return |
| $2437 | $409E | TEMPOTBL | [0]=main tempo,[1]=funk 2nd,[2..7]=per-track slots (TRKTMPOS 2/4/6) `[EOTW: chords empty, aliased onto TEMPOTBL]` |
| $2445 | $40A6 | SUBTUNES | per subtune 8 B: 3 orderlist word-offsets + tempo1,tempo2 (Emomyst: only +6/+7 read) |
| $2447 | $40AE | TEMPTRLO | tempo-program start indices into TEMPOTBL |
| $2448 | $40AF | CHDPTRLO | chord start indices into CHORDS |
| $244E/$2463 | $40B1/$40C6 | INSPTLO/HI | 21 word-offsets, entry 0 = entry 1 |
| $2477/$248C | $40DA/$40F9 | PPTRLO/HI | 21 / 31 word-offsets, entry 0 unused |

##### 3.4.1.3 Per-voice state — struct-of-arrays, stride 7, `base,X` with X∈{0,7,14}

All indexed `abs,X` (VARIABLES is not in zero page in either export). Bunch k of voice v is at `$1024 + 21k + v*7 + i`.

| addr (v=0) | name | meaning | writers → readers |
|---|---|---|---|
| $1024/25 | FREQLO/HI | 16-bit pitch accumulator (table value ± vibrato/slide) | WFARPTB, VIBSLIDE → WRPITCH |
| $1026/27 | PWLOGHO/PWHIGHO | 12-bit pulse width accumulator | SETPWID → SID $D402/3 |
| $1028 | WFGHOST | waveform/control shadow | STRTSND, HRGTOFF, WF table, note-FX → $D404 |
| $1029 | PTNGATE | $FF/$FE gate mask ANDed into every WF-table waveform | HR/gate-off/gate-on |
| $102A | PWEEPCNT | PW sweep frame counter | SETPWID |
| $1039 | PACKCNT | packed-rest countdown | READROW/PTN_SEQ |
| $103A | SPDCNT | tick counter 0..tempo-1 (post-increment) | DOTRACK |
| $103B | SEQPOS | orderlist index | PTN_SEQ |
| $103C | PTNPOS | byte index into current pattern (first byte of next row) | READROW/PTN_SEQ |
| $103D | WFTPOS | index into instrument (WF-ARP row) | STRTSND(=$10), WFARPTB, big-FX 9, gate-off ptr |
| $103E | PWTPOS | index into instrument (PW row) | STRTSND(=ins[$A]), SETPWID |
| $103F | ARPSCNT | arp-speed countdown; $FF = "note's first frame" flag for multispeed | WFARPTB, small-FX C |
| $104E | CURPTN | pattern number | PTN_SEQ |
| $104F | CURNOT | note column of the row (0 none, 1..$5F note, $60..$7F FX) | READROW → TICK_2 |
| $1050 | DPITCH | discrete pitch = note + ins[9] + TRANSP | CLEGATO → tables |
| $1051 | CURIFX | instrument/small-FX column, one-shot (zeroed each tick 0) | READROW → TICK_2 |
| $1052 | CURINS | selected instrument (persistent) | TICK_2 |
| $1053/54 | CURFX2/CURVAL | FX column / big-FX value, one-shot | READROW → INSPTFX |
| $1063 | SLIDEVIB | $00/$10/$20/$30 vibrato type; $81 slide up, $82 down, $83 portamento, $FF pending note-FX portamento | STRTSND, big-FX 1/2/3, FORCVIB |
| $1064/65 | FREQMODL/H | vibrato amplitude or slide speed (16-bit) | SETFMOD |
| $1066 | VIDELCNT | vibrato delay countdown ($FF = running); for type $00 = increment | SETVIB0 |
| $1067 | VIBFREQU | vibrato period = 2×freq nibble | SETVIBR |
| $1068 | VIBRACNT | vibrato phase counter | DOVIBRA |
| $1069 | TRANSP | applied transpose (delayed copy of TRANSP2) | PTN_SEQ |
| $1078 | TMPPTR | tempo-program loop start | big-FX $10–$15 |
| $1079 | TMPPOS | current TEMPOTBL index | DOTRACK |
| $107A | ARPSPED | ins[7] (b0-5 speed, b6/b7 multispeed PW/filter) | STRTSND, FX C |
| $107B | PKBDTRK | PW keyboard-track amount | PW table col 3 |
| $107C/7D | CURCHORD/CHORDPOS | chord number / running index in CHORDS | STRTSND, FX 7, chord player |
| $107E | ARPDPITCH | last table-set pitch (slowdown build only) `[EOTW: slot unused]` | — |
| $108D/8E | FLSWTBL/FLSWTB2 | consts 1/$FE, 2/$FD, 4/$FB: filter-route bit set/clear masks per voice | — |
| $108F | TRKTMPOS | consts 2,4,6: this track's TEMPOTBL slot | — |
| $1090 | TRDELAY | (DELAY off — unused) | — |
| $1091 | SEQTEMPO | pending orderlist tempo (applied next tick 0) | — |
| $1092 | TRANSP2 | orderlist transpose (applied next tick 1) | — |
| $1093 | DETUNER | WF-table detune byte, added to FREQLO at write-out | — |

Zero page: `$02/$03` = PLAYERZP, the *only* pointer: pattern base during READROW/PTN_SEQ, instrument base everywhere else `[EOTW: $FE/$FF, saved/restored around play]`.

Global state lives as **patched immediates** (§6.2): MAINVOL+1 $1209, FLTBAND+1 $120B, RESONIB+1 $1204, FSWITCH+1 $1202, CKBDTRK+1 $1211, CTFHGHO+1 $121F, CTFLGHO+1 $1247, SLOWDOWN+1 $1788 (+ mirrors $17AC,$17B5,$1809,$14EB,$14FB,$1511, $1221 SLOWDN2), FLTCTRL+1 $15D2 (voice X that owns the filter, $0F = none), FLTPOSI+1 $15D6, CWEPCNT+1 $15DD, TABLRST+1 $1464, INSCTRL+1 $146E, STORFRL+1 $155B, ASTOREZ+1 $171C, VALSTOR+1 $19E3, MERGEST+1 $19E9, MUL3TMP+1 $19F0, INDEXJ1+1 $1952, INDEXJ2+1 $1A13, INDEXJP+1/2 $19D0/1, SFXleng/timer $1190/1. `[EOTW]` MAINVOL $2A7A, SEQVOLU $2B2B, FLTBAND $2A7C, RESONIB $2A75, FSWITCH $2A73, CKBDTRK $2A82, CTFHGHO $2A90, FLSHIFT $2A92, CTFLGHO $2A97, FLTCTRL $2E3C, FLTPOSI $2E40, CWEPCNT $2E47, TABLRST $2CF6, INSCTRL $2D00, STORFRL $2DC5, ASTOREZ $2F8F, VALSTOR $324C, MERGEST $3252, INDEXJ1 $31BB, INDEXJ2 $327D, INDEXJP $3239, SUBTPOS $302E, XSTORE $3045.

#### 3.4.2 Entry points and conventions

- **init** (`$1000` → `$1015` stub → `INITER $10A5`): Emomyst's stub forces X/Y=$00/$1C (blob base); EOTW's PSID init stub sets A=subtune, X/Y=$00/$35. INITER: (1) `STA SWPdone+1` saves subtune in an immediate; (2) `STX/STY` blob base into SWP_OFFSET and into the two `LDA blob,Y` operands ($10CB/$10DA); (3) loop over DataPtr/PtrValu: for each patched instruction, operand := blob[slot] + base + addend (addend $FF = −1); with no-subtune support the first 3 entries (p_seqt1..3) get a second indirection (`JSR $1163`) so `LDA orderlist,Y` gets the orderlist address itself; (4) SETSTUNE ($17C1: copy SUBTUNES+6 → TEMPOTBL+0 `[EOTW $2FFE: 3× SETSEQB patches p_seqt1..3 from SUBTUNES[sub*8], copies tempo pair]`); (5) zero VARIABLES (105 B), zero $D400–$D417 (not $D418), zero FLTBAND/FSWITCH/CKBDTRK immediates, MAINVOL=$0F, FLTCTRL=$0F; (6) INITPTN loop X=14,7,0: TRANSP2=SEQTEMPO=0, read orderlist[0]: if <$80 → CURPTN, else if <$FE apply SEQ_FX and advance. Returns with the tick machine at SPDCNT=0 (first play call = tick 0).
- **play** (`$1003` → `$11C4`): Emomyst first `LSR SLOWDCNT`: on the very first call SLOWDCNT=0 → C=0 → returns without playing (frame 0 is silent; measured 5999 DOTRACK rounds in 6000 calls). Then SFX check, then `LDX #14; JSR DOTRACK; LDX #7; JSR; LDX #0; JSR` (voice 3, 2, 1) then COMMONREGS (filter/volume writes) then RTS `[EOTW: PLA/STA $FF, PLA/STA $FE first]`.
- **MULPLY** `[EOTW $2AA2]`: for X=14,7,0: `MULCNTP` — if ARPSCNT ≥ 0 (not the note's first frame) re-point PLAYERZP to CURINS and enter FILTPRG (ins[7] bit7) / SETPWID (bit6) / WFARPTB, which fall through to WRPITCH/WRWFGHO; then `JMP COMMONREGS`. Multispeed = tables N× per frame, sequencer 1×.
- **volume** (`$1009`): A → MAINVOL+1. **SFXinit** ($1167): A=length, X=note, Y=instrument, hijacks voice 3 (writes CURNOT+14/CURINS+14, zeroes $D412–14). **setSlowdown** ($1193): A → 7 patched immediates + SLOWDN2 = 255−A+1.
- Register convention inside DOTRACK and everything it calls: **X = voice offset (0/7/14), preserved** (SETSEQA saves/restores it via XSTORE; PORTAME/WRPITCH-slowdown save it in an immediate); **Y = table index** (pattern byte, instrument byte, table row); A = data. Flags carry results between adjacent code only; the one flag-argument is `HARDRST`'s A (=2 at tick 0, =1 at tick 1) which is ANDed with ins[0] bits 0-1.
- Tail calls/shared tails: TICK_2 paths end `JSR INSPTFX; JMP WRWFGHO` or `JMP CNTPLY2`; HRENDER `JMP WFARPTB`; every voice path ends at the shared tail `WRPITCH→WRWFGHO→RTS` ($1776–$17C0); NEWNOTE/HARDRST `JMP CNTPLAY`. `CHKNOTE`, `STRTINS`, `CNTPLY2`, `SETVIB1`, `SETVIBR`, `SETFMOD`, `FORCVIB`, `FORCVI2`, `TRKTMP2/3`, `MAINTMP/MAINTM2` are secondary entry points into routines (labels used as jump targets by FX handlers).

#### 3.4.3 The play routine

```
PLAY:
  [Emomyst] if (--slowdown-dither bit == 0) return           ; frame skip; skips frame 0
  [Emomyst] SFX timer bookkeeping on voice 3
  for X in (14, 7, 0): DOTRACK(X)
  COMMONREGS:  D417 = FSWITCH | RESONIB
               D418 = MAINVOL | FLTBAND
               cut  = (CKBDTRK ? EXPTABH[CKBDTRK + DPITCH[FLTCTRL]] : 0) + CTFHGHO [+ FLSHIFT] [slowdown remap]
               D416 = cut ; D415 = CTFLGHO (3 fractional bits)
  return

DOTRACK(X):                                    ; $124D
  Y = TMPPOS[X] + 1
  A = SPDCNT[X] - TEMPOTBL[Y-1]                ; SEC first
  if A == 0:            SPDCNT = 0; TMPPOS = Y            ; row ends, next tempo entry
  elif V flag (A==$80): SPDCNT = 0; TMPPOS = TMPPTR[X]    ; tempo entry had bit7: loop program
  tick = SPDCNT[X]++                           ; SPDCNT counts 0,1,2,..,tempo-1
  if tick == 0: TICK_0 elif tick == 1: TICK_1 elif tick == 2: TICK_2 else CNTPLAY
```

Tick semantics: a row lasts `tempo` frames; row data is read at tick 0, the *next* row/orderlist position is resolved at tick 1, the note actually starts at tick 2. Hard restart is issued at tick 0 (2 frames early, ins[0]&2) and/or tick 1 (ins[0]&1).

```
TICK_0:                                        ; $1275
  zp = pattern[CURPTN]                         ; PPTRLO/HI + blob base
  CURIFX = CURFX2 = 0 ; MAINVOL = SEQVOLU (delayed volume seq-FX)
  if SEQTEMPO: TRAKTMP(SEQTEMPO); SEQTEMPO = 0
  READROW (§4.2) into CURNOT/CURIFX/CURFX2/CURVAL, PTNPOS = last byte read
  [Emomyst voice 3 & SFX active: suppress note/FX]
  A = 2 ; goto HARDRST
TICK_1:                                        ; $1393
  zp = pattern[CURPTN]; TRANSP = TRANSP2
  if PACKCNT == 0:
     if pattern[PTNPOS+1] == $FF:  next orderlist entry (§4.1) → CURPTN, SEQPOS, PTNPOS = 0
     else PTNPOS += 1
  A = 1 ; goto HARDRST
HARDRST(A):                                    ; $1310
  if CURNOT == 0 or CURNOT >= $60 or CURFX2 == 3 or SLIDEVIB == $FF: goto CNTPLAY   ; no new note
  ins = CURIFX (if 0 or FX: CURINS) ; if ins == $3F (legato): CNTPLAY
  zp = instrument[ins]
  if (ins[0] & A) == 0: goto HRENDER            ; HR not scheduled for this tick
  PTNGATE = $FE ; WFGHOST &= $FE ; D405 = ins[1] ; D406 = ins[2]   [EOTW: D406 then D405]
  if ins[0] & 4: WFGHOST = D404 = $18 ; return          ; "test-bit mute" HR type
HRENDER: zp = instrument[CURINS] ; goto WFARPTB          ; tables keep running, no vib/PW/filter
TICK_2:                                        ; $13EB
  if 1 <= CURIFX < maxinst: CURINS = CURIFX ; TABLRST = $3F (force PW/filter reset) else TABLRST = $FF
  if CURINS == 0: goto LEGATOO(no sound)
  zp = instrument[CURINS]
  if CURNOT == 0:      INSPTFX ; goto CNTPLY2
  if CURNOT >= $60:    NOTE_FX ; goto CNTPLY2
  DPITCH = CURNOT + ins[9] + TRANSP
  if CURFX2 == 3 (portamento):        goto LEGATOO
  if SLIDEVIB == $FF (note-FX porta): SLIDEVIB = $83 ; goto LEGATOO
  if CURIFX == $3F (legato):          FREQMODH = $7F ; SLIDEVIB = $83 ; goto LEGATOO   ; instant "portamento"
  STRTSND:
     c = ins[0] & TABLRST ; INSCTRL = c ; SLIDEVIB = c & $30
     if c & 8: D401 = FREQTBH[DPITCH]  (1.6 bug: FREQTBH[X]) ; WFGHOST = ins[$F]   ; first-frame waveform (test bit)
     WFTPOS = $10 ; PTNGATE = $FF ; ARPSCNT = $FF ; ARPSPED = ins[7]
     SETVIB0: VIDELCNT = ins[6] ; SETVIB1: (VIBFREQU,VIBRACNT,FREQMOD) from ins[5] & DPITCH
     CURCHORD = ins[8] ; CHORDPOS = CHDPTRLO[ins[8]]
     if !(c & $40): PWTPOS = ins[$A]
     if !(c & $80): filter route: r = ins[ ins[$B] ]:  0 → FSWITCH |= bit(X)
                                                       $FF → if FLTCTRL==X: FLTPOSI=ins[$B]; FSWITCH &= ~bit(X)
                                                       else → FLTCTRL = X ; FLTPOSI = ins[$B] ; FSWITCH |= bit(X)
     D405 = ins[3] ; D406 = ins[4]   [Emomyst: through the slowdown ADSR remap, identity when slowdown=0]
     INSPTFX ; goto WRWFGHO           ; note-start frame writes only D401(hi), D405, D406, D404
  LEGATOO: INSPTFX ; goto WRWFGHO
CNTPLAY: if CURINS == 0: return ; zp = instrument[CURINS]
CNTPLY2:
  VIBSLIDE (§3.1) ; FILTPRG (only if FLTCTRL == X) ; SETPWID ; WFARPTB ; WRPITCH ; WRWFGHO ; return
```

##### 3.4.3.1 Effect processing (per voice, per frame)

```
VIBSLIDE:  t = SLIDEVIB
  $00: FREQMOD += VIDELCNT (16-bit)             ; "increasing" vibrato, then fall into vibrato
  $10/$20/$30: if VIDELCNT >= 0: VIDELCNT-- ; skip           ; delay
  $81: FREQ += FREQMOD   $82: FREQ -= FREQMOD                ; slides
  $83/$FF: PORTAME: d = FREQTBL[DPITCH] - FREQ ; if |d| > FREQMOD: FREQ ±= FREQMOD else FREQ = table (SLIDEVIB stays $83; PORTAVIBRA off in both builds)
  vibrato: if VIBRACNT==0: VIBRACNT=VIBFREQU ; VIBRACNT-- ; if 2*VIBRACNT < VIBFREQU: FREQ += FREQMOD else FREQ -= FREQMOD
FILTPRG (owner voice only): row = ins[FLTPOSI]:
  < $80: sweep: if CWEPCNT == row.b0: advance ; else CWEPCNT++ ; cutoff11 += signed row.b1 (3 fraction bits in CTFLGHO)
  $FE: jump to row.b1 (if == FLTPOSI: hold) ; $FF: hold
  else set: FLTBAND = b0 & $70 ; RESONIB = b0 << 4 ; CTFHGHO = b1 ; CTFLGHO = 0 ; b2: kbtrack (<$80) or $8x → FSWITCH low nibble ; FLTPOSI += 3 ; CWEPCNT = 0
SETPWID: row = ins[PWTPOS]: < $80 sweep (b0 frames, PW += signed b1) ; $FE jump ; $FF hold ; else PWHI = b0&$7F, PWLO = b1, PKBDTRK = b2
  D403 = PWHI + (PKBDTRK ? EXPTABH[PKBDTRK+DPITCH] - EXPTABH[PKBDTRK+DPITCH-1] : 0) ; D402 = PWLO
WFARPTB: if --ARPSCNT >= 0: skip ; ARPSCNT = ARPSPED & $3F
  row = ins[WFTPOS]: $FF hold ; $FE: WFTPOS = b1 (if b1 >= $80: hold) and read target row
  b0 < $10: ARPSCNT = b0 (repeat count) ; else WFGHOST = b0 & PTNGATE
  b1: $7F → chord step (CHORDS[CHORDPOS++]; $7F loops chord, $7E returns and skips row) ; $80 → keep pitch
      $00–$7E rel up, $E0–$FF rel down (+DPITCH) ; $81–$DF absolute (&$7F)   → FREQ = FREQTBL/H[pitch]
  b2 != $FF: DETUNER = b2 ; WFTPOS += 3
WRPITCH: D400 = FREQLO + DETUNER ; D401 = FREQHI + carry     [Emomyst: minus slowdown pitch delta]
WRWFGHO: D404 = WFGHOST
```

`INSPTFX` ($19AB): if CURIFX ≥ $40 → SMALPFX(CURIFX); if CURFX2: ≥ $20 → SMALPFX(CURFX2) else BIGPTFX(CURFX2, CURVAL). SMALPFX: type = A>>4 (2..F), value = A&$0F → VALSTOR; `INDEXJ2: BCC *+offset` where offset = SMALLFXTBL[type-2] (patched branch). BIGPTFX: `INDEXJP: JMP` operand := BIGFXTABLE[fx-1], A = CURVAL.

#### 3.4.4 Data formats

##### 3.4.4.1 Orderlist (one per track, read via `SEQSUB` = 3-way `CPX #7` fan-out to `LDA list_n,Y`)
byte `< $80` pattern number · `$80–$9F` transpose (v−$90 semitones, applied at next tick 1) · `$A0–$AF` volume (delayed to next tick 0) · `$B0–$EF` track tempo v−$B0 (delayed) · `$FE` stop track (SPDCNT is decremented so the track freezes at tick 1) · `$FF pos` loop to `pos` (`pos ≥ $80` = subtune jump when SUBTUNEJUMP on). Emomyst: `0A 01 0D 01 0D 04 07 0F 07 0F 07 FF 02` (no FX); EOTW track 1: `90 01 04 07 04 0C 0F 12 04 17 91 1D 1A FF 00` (transpose 0 then +1).

##### 3.4.4.2 Pattern rows (`READROW` $129E)
Row = 1..4 bytes; bit 7 of a byte = "another column follows":
```
b0: note   & $7F : 0 none | 1..$5F note (C-1 = 1) | $60–$6F set vibrato amplitude (note-FX→SMALFX8) | $70–$77 packed rest (2..9 rows) | $78 porta note-FX | $79/$7A sync on/off | $7B/$7C ring on/off | $7D gate on | $7E gate off
b1: ins    & $7F : 0 none | 1..$3E instrument | $3F legato | $40–$7F instrument-FX (type = hi nibble 4..7)
b2: fx           : $20–$FF small FX (type=hi nibble, val=lo nibble) — no b3 | $00–$1F big FX → b3 = value
```
Pattern end: `$FF` in b0 position, followed by one byte = row count (editor metadata, never read by the player; verified on all 30 EOTW patterns: expanded row counts 24/48/72 = the byte). Packed rest `$70+n` sets PACKCNT = n+2, then each tick 0 decrements and yields an empty row while PTNPOS is frozen (verified: EOTW rows expand exactly to the length byte). Measured usage — Emomyst 784 packed rows (74 packed-rest bytes), 376 notes, 90 gate-off, 3 gate-on, 174 instrument selects, 17 legato, ins-FX types 4/5/6/7 (4/84/13/1), small FX types 2,4,5,6,7,B,C, big FX 01,02,03,0C; EOTW 1269 rows (rowlens 1:722 2:410 3:25 4:112), note-FX $61–$69, small FX 2/5/6/8, big FX 01(21) 02(3) 03(77) 10(11).

##### 3.4.4.3 Instrument (variable length; INSPTLO/HI[n] → blob offset)
```
+0 ctrl: b0 HR at tick1, b1 HR at tick0, b2 HR = test-bit mute ($18), b3 first-frame waveform from +$F (else none), b4-5 vibrato type, b6 no PW reset on new note, b7 no filter reset
+1 HR AD  +2 HR SR  +3 AD  +4 SR
+5 vibrato: hi nibble amplitude, lo nibble frequency   +6 vibrato delay (or increment for type 0)
+7 arp speed (b0-5) | b6 multispeed PW | b7 multispeed filter   +8 default chord   +9 octave/semitone shift (signed, e.g. $F4 = −12, $DC = −36)
+$A PW-table start index  +$B filter-table start index  +$C/+$D/+$E gate-off jump index for WF/PW/filter (0 = none)
+$F first-frame waveform (typically $09/$19/$89: test bit + gate)
+$10.. WF-ARP rows [wave, pitch, detune]×n, then $FF (one byte)
+ins[$A].. PW rows [b0,b1,b2]×n, $FF ; +ins[$B].. filter rows ×n, $FF
```
Emomyst inst 2: `1A 0F 00 E0 6A 24 10 02 01 00 1A 27 00 00 00 09 | 51 7F 00 | 41 7F 00 | FE 10 00 | FF | 83 00 00 | 28 20 00 | 20 E0 00 | FE 1D 00 | FF | FF` — pulse+chord arpeggio at arp speed 2 (chord 1 = `FE 01 05 7F` = −2,+1,+5 loop), PW $300 then sweep +$20 ×$28 frames, −$20 ×$20, loop; no filter. Chords: signed semitones, `$7F` loop, `$7E` back to WF table.

##### 3.4.4.4 Frequency and exponent tables
FREQTBL/H (96 entries, index 0 = pad, C-1 at 1). EXPTABH = FREQTBH−11 (10 zeros + 1 zero + the hi bytes) is reused as an exponential lookup: `SETFMOD` computes vibrato amplitude / slide speed as `EXPTABH[amp*4 + DPITCH]` (or FREQTBL/H[y−$6B] for the rough half, capped at $CB) so the modulation is pitch-proportional (constant in cents); PW/filter keyboard tracking use the same table (`EXPTABH[k+pitch]`, PW additionally `− EXPTABH[k+pitch−1]`). No multiply anywhere.

##### 3.4.4.5 Tempo
TEMPOTBL: [0] main tempo, [1] funktempo 2nd value, [2..7] per-track slots. Entry bit 7 = "loop back to TMPPTR after this row". Both tunes: single tempo with bit 7 set (Emomyst $86 = 6, EOTW $8E = 14) — the "tempo program" reduces to a constant. Big FX $10 single/$11 funk/$12 program (main), $13/$14/$15 (track): all write TEMPOTBL and reset TMPPOS/TMPPTR.

#### 3.4.5 SID write schedule (per frame, in order; verified from the write log)

1. voice 3, then 2, then 1 (X=14,7,0). Per voice, by phase:
   - HR frame (tick 0 or 1): `D405,D406` = HR AD/SR (1.6 order AD,SR; 1.9 SR,AD), then WF-table tail: `D403,D402` are **not** written (SETPWID skipped), `D400,D401,D404` (WFGHOST with gate cleared).
   - note-start frame (tick 2): `D401` (hi byte only, if ctrl b3), `D405,D406` (AD,SR / 1.9 SR,AD), `D404` = first-frame waveform ($09/$19: test+gate). No PW/freq-lo write this frame.
   - every other frame: `D403,D402,D400,D401,D404` (PW hi, PW lo, freq lo, freq hi, wave). ADSR only via small-FX 2/3/5/6 or big-FX 5/6.
2. `D417 = FSWITCH|RESONIB`, `D418 = MAINVOL|FLTBAND`, `D416`, `D415`.
Everything is written unconditionally every frame (no dirty flags): a full frame = 3×5 + 4 = 19 stores plus 2–3 on note frames. Ghost registers exist only for FREQ/PW/WF; AD/SR/filter are written directly (`ALLGHOSTREGS` off). Gate: PTNGATE ($FF/$FE) is ANDed into every WF-table waveform, so gate-off/HR persist through arpeggios; gate-on ($7D) sets PTNGATE=$FF and ORs 1 into WFGHOST. Hard restart = HR ADSR + gate off at tick 0/1, then test-bit waveform + real ADSR at tick 2, real waveform from the WF table at tick 3. Filter: one owner voice (FLTCTRL) runs its filter table; routing bits accumulate in FSWITCH from all voices' instruments. No reads of $D41B/$D41C/$D012.

#### 3.4.6 Techniques specific to this player

1. **Init-time relocation of table-base operands** (`$10BA–$111C`): a data-driven fixup loop, `DataPtr` (instruction addresses) × `PtrValu` (slot, addend) → `operand = blob[slot] + base + addend` written through `(zp),Y` at Y=1,2 into the instruction. 30 sites, e.g. `$1278 LDA $2477,Y` (PPTRLO), `$1255 SBC $2436,Y` (TEMPOTBL−1), `$17CE LDA $1C40,Y` (orderlist 1). Why: position-independent music blob without a runtime add on every table access.
2. **Runtime add of the blob base on every pointer set**: `LDA lo,Y; CLC; ADC SWP_OFFSET; STA zp; LDA hi,Y; ADC SWP_OFFSET+1; STA zp+1` — 8 copies ($1275, $1338, $137B, $1396, $140B, $1526 …). Instrument/pattern pointer tables hold *offsets*.
3. **Patched immediates as global variables** (27 sites in play): `MAINVOL: LDA #$0F` ($1208), `FLTBAND: ORA #` ($120A), `RESONIB` ($1203), `FSWITCH: LDA #` ($1201), `CTFHGHO: ADC #` ($121E), `CTFLGHO: LDA #` ($1246), `CKBDTRK`, `FLTCTRL: CPX #` ($15D1), `FLTPOSI: LDY #` ($15D5), `CWEPCNT: CMP #` ($15DC, incremented in place `INC $15DD`), `INSCTRL: LDA #` ($146D), `TABLRST: AND #` ($1463: $3F or $FF), `STORFRL: LDA #` ($155A), `ASTOREZ: LDA #` ($171B), `VALSTOR/MERGEST/MUL3TMP`. Why: 2-cycle immediate reads for values touched once per frame; costs 3-cycle absolute stores where written.
4. **Computed branch/jump dispatch**: `INDEXJ1: BCC *+2` ($1951) and `INDEXJ2: BCC *+2` ($1A13) whose *rel operand* is loaded from NOTEFXTBL / SMALLFXTBL after a `CLC` (an unconditional relative jump table in 8-bit offsets — handlers must fit in +127); `INDEXJP: JMP abs` ($19CF) whose operand is loaded from BIGFXTABLE (word table). Three dispatchers, three encodings.
5. **X = voice offset, stride 7 struct-of-arrays**: every per-voice variable is `abs,X`; the voice loop is unrolled as three `LDX #n; JSR DOTRACK`. Constants also live in stride-7 tables (FLSWTBL `1,2,4`, FLSWTB2 `$FE,$FD,$FB`, TRKTMPOS `2,4,6`) so a voice's filter bit / tempo slot is a plain `LDA tbl,X`. `TXA; SEC; SBC #7; TAX; BPL` walks voices in init.
6. **One zero-page pointer, two meanings**: PLAYERZP is the pattern pointer during row reads and the instrument pointer everywhere else; every routine that needs the instrument re-points it (6 copies of the pointer set, 7 with MULCNTP) instead of keeping a second pointer.
7. **Flag argument**: HARDRST receives A=2 or 1 (`LDA #2` $130E, `LDA #1` $13E6) and does `AND (zp),0` on the instrument control byte: the *tick number is the bit mask*.
8. **V-flag trick for the tempo loop**: `SEC; SBC TEMPOTBL-1,Y; BEQ new_row; BVC same_row` — one subtraction distinguishes "counter == tempo" (Z) from "counter == tempo&$7F with bit7 set" (V) → loop the tempo program.
9. **Bit-7 continuation encoding** of pattern rows (`BMI` on the loaded byte, `AND #$7F`), `BPL SETPPOS` after `STA` reuses the N flag from the load, and `AND #$E0; BNE` splits small/big FX by magnitude.
10. **Sign-driven table row types**: first byte `BMI` = command ($FE jump/$FF end/set) vs data (sweep count); `CMP #$FE; BEQ; BCC; BCS` three-way in 8 bytes (PW $168A, filter $161B, WF $16E8).
11. **11-bit fixed-point cutoff**: signed 8-bit sweep step split `AND #7` (fraction, into CTFLGHO = D415's 3 bits) and `LSR×3` (integer), with `PHP/PLP` to carry the fraction overflow into the hi add ($15E8–$1615); negative steps `ORA #$F8` sign-extend the fraction.
12. **Signed 16-bit PW sweep** with pre-decrement: `BPL +; DEC PWHIGHO; +CLC; ADC PWLOGHO; STA; BCC; INC PWHIGHO` — adds a signed byte to a 16-bit value without a sign-extension register.
13. **Exponent table as multiplier** (`EXPTABH[k + pitch]`): keyboard tracking and pitch-proportional vibrato/slide amounts with a `TAY; LDA tbl,Y`, no multiply.
14. **PHA/PLA and immediates as scratch** instead of ZP temps (HR tick number across the pointer set $1337/$1349; slide value in `SETSLID`; `STX slXstor+1` in WRPITCH-slowdown).
15. **Chord loop by table rewind**: `$7E` in CHORDS resets CHORDPOS and does `WFTPOS += 2 (+carry)` and `JMP RDWFROW` — the WF-table row is re-parsed with the chord finished.
16. **Dither-mask frame skipper** (slowdown build): `LSR SLOWDCNT; BNE; reload from slDither[n]; BCC return` — a 1-bit-per-frame pattern table gives ratios 1/1…1/4; leading zeros encode the pattern length.
17. **`BCS`/`BNE` as unconditional jumps** after known-flag ops (`ORA #$80 … BPL`, `LDY #$81; BNE`), and instruction-boundary sharing (`.byte $BC,<SEQPOS,>SEQPOS` — a `LDY SEQPOS,X` the editor overwrites with `JMP PTNPLAY`; in the exports it is a normal `LDY $103B,X` at $13B6).
18. **Editor hooks compiled out**: no illegal opcodes, no cycle-timed code, no raster/timer reads.

#### 3.4.7 What it reduces to

Per voice the whole engine is 34 bytes of state (5×7 variables minus one spare) plus 7 shared bytes; globally about a dozen patched-immediate bytes (volume, filter cut/band/reso/route/kbtrack, filter owner/pos/sweep count) and TEMPOTBL. Per frame it is:

```
for v in (3,2,1):
  tick = advance_tempo(v)
  if tick == 0: read_row(v); maybe_hard_restart(v, mask=2)
  if tick == 1: advance_position(v); maybe_hard_restart(v, mask=1)
  if tick == 2: select_instrument(v); start_note_or_apply_fx(v)     ; writes ADSR + first waveform
  else:         run vibrato/slide, filter-table (owner only), pw-table, wf-arp-table; write PW, freq, wave
write filter/volume
```
Three tables per instrument, each the same little machine: rows of 3 bytes; row[0] ≥ $80 is a *set* (or `$FE pos` jump / `$FF` hold), row[0] < $80 is a *sweep for N frames by row[1]*; a per-voice index and (PW/filter) a per-voice frame counter. Sequencer = orderlist of pattern numbers with a handful of ≥$80 commands, patterns of 1–4-byte rows with bit-7 continuation. Everything else — 31 big-FX, 14 small-FX, 8 note-FX — is a one-instruction write to one of these state bytes (a table index, SLIDEVIB, an ADSR nibble, a patched immediate) followed by `RTS`.

Statically decidable: all table bases (after init), voice offsets, tempo (constant in both tunes), the whole dispatch (finite tables), which FX/features exist (dead handlers are `RTS`). Runtime-only: pattern bytes, instrument bytes, tick counters. What SMC really is: (a) 30–36 relocation constants fixed at init and never changed again; (b) ~27 plain variables that happen to live in operand bytes; (c) two branch-offset dispatchers and one jump-operand dispatcher. No opcode is ever patched in the exports. Volatile input: none.

Dead in the exemplars: multispeed (EOTW), SFX and slowdown (Emomyst; slowdown code still runs every frame with delta 0 — the ADSR remap and WRPITCH difference are identities), delay FX, funktempo, tempo programs, gate-off pointers, HR type "$18", subtune jump, WF `$FE` jumps, filter jump. Family variation: the exporter/relocator strips features by flag (Emomyst's small-FX 9/B/D/E/F are bare `RTS`, EOTW has them all), light/medium/extra players differ in the same way; 1.6 has the `LDA FREQTBH,X` first-frame bug ($1476, `BD 69 18`: index by voice offset instead of pitch — inaudible because the test bit is set that frame; 1.9 has `,Y`), 1.6 writes AD before SR, 1.9 SR before AD.

#### 3.4.8 Decompiler notes

- **Code/data boundary**: init and play are the two roots; the region between the jump table and INITER ($1012–$10A4) is data (flags, string, VARIABLES, CONST_VAR); tables inside the code region ($1830–$1938, $19F2, $1A70, $1B55–$1BCF, $11AB) are only reached via indexed loads — recursive descent from the jump table plus the three computed dispatchers (§6.4, tables enumerable statically) covers all code; nothing executes as data.
- **Relocation SMC**: treat every DataPtr target as `operand := blob[slot] + base (+ addend)` evaluated once; after modelling init, all `LDA tbl,Y` are constant-base indexed loads. The two `(zp),Y` stores at Y=1/2 into instructions ($10D4, $10E2, $10EB, $1118) are the only writes into code during init.
- **Patched-immediate variables**: map each `opcode #imm` whose operand byte has a writer to a named global (list in §1.3); reads are the immediate, writes are `STA imm+1`. `INC $15DD` is a read-modify-write of one (CWEPCNT). Because the same operand can be written from several routines (e.g. FSWITCH from filter table, note-start, big-FX $1F), model it as one variable, not per writer.
- **Dispatch**: `BCC *+2` with patched offset = `switch(type)` over an offset table (targets = table entries + INDEXJ+2); `JMP abs` with patched operand = `switch(fx)` over a word table. Enumerate the tables, not the writers.
- **Per-voice struct**: any `abs,X` with X∈{0,7,14} where abs ∈ [$1024,$10A1] is field `(abs−$1024) mod 7` of bunch `(abs−$1024) div 21`; the same for `abs,X` constants at $108D–$10A1. X is invariant across DOTRACK and all callees (SETSEQA and the slowdown WRPITCH save/restore it), so a decompiler can lift `DOTRACK(voice)` with X as an implicit parameter and no aliasing between voices.
- **PLAYERZP typing**: the pointer's target type is phase-dependent (pattern in READROW/PTN_SEQ, instrument otherwise); tag by the last pointer-set site (pattern sets use PPTRLO/HI, instrument sets INSPTLO/HI).
- **Table typing**: instrument bytes 0–$F are a fixed struct; from $10 the three tables are variable-length arrays of 3-byte rows terminated by $FF, addressed by *per-voice indices into the instrument*, so the same instrument byte is code-of-a-machine, not a value; WF row = (wave|cmd, pitch|chord, detune), PW/filter row = (set|sweep, value, kbtrack). Chords, orderlists and patterns are byte streams with the grammars in §4.
- **Invariants**: Y is clobbered by most subroutines (SEQSUB and TRKTMP2 deliberately return with Y meaningful); flags never cross a `JSR` except the deliberate `SEC` before SBC in DOTRACK/`CLC` before ADC in COMMONREGS; A carries the tick mask into HARDRST; every voice path ends at WRWFGHO; the tempo counter is `SPDCNT` post-incremented so a `tick` is 0-based.
- **Timing**: no cycle dependence anywhere; frame semantics only. The single trap is the slowdown gate: the first play call after init does nothing (Emomyst), and the first tick-0 runs the WF-table from index 0 of instrument 0 (garbage rows before any note; test bit keeps it silent).
- **Traps**: `LDA #imm` operands that are variables look like constants in a static disassembly (29 SMC sites flagged by the trace, several more written only in unexercised paths — take the writer set from §1.3, not from coverage); `.byte $BC` (`LDY abs,X`) at PTN_SEQ is meant to be overwritten by the editor — in exports it is ordinary code; EOTW's empty chord table aliases TEMPOTBL ($409E) — same address, two roles; big-FX numbers $17–$1B all point at the next real handler (shared label), and dead handlers are one-byte `RTS`s that several table entries share.

### 3.5 JCH NewPlayer 20, 4-track sample build — Easy Does It (1991)


#### 3.5.0 Identity

| item | value |
|---|---|
| file | `MUSICIANS/J/JCH/Easy_Does_It.sid`, PSID load $3FC0–$7D31, init $3FC0, play $0000 (RSID-style: install own IRQ) |
| wrapper | $3FC0–$3FF0 (49 bytes): SEI; $FFFE/F ← $3FE0; JSR $4000 (A=0=subtune); CIA1 timer A off ($DC0E=0), $D01A=1 (raster IRQ); CLI; `JMP *`. IRQ $3FE0: PHA/TYA/PHA/TXA/PHA; `INC $D019` (ack); JSR $4003; restore; RTI |
| player | $4000–$48E2 code (2275 bytes, 906 instructions), $48E3–$4F67 fixed tables (1669 bytes), $4F68–$564A song (tracks + patterns), $5660–$7D2A 4-bit sample data (~9.9 KB). Player writes only $D400–$D417 from IRQ; **$D418 is written only by the NMI sample mixer** ($410F/$4126/$4153) |
| speed | 1× per frame (raster IRQ) + CIA2 Timer A NMI at ~2.5–10 kHz for the sample channel |
| subtunes | 1 (header at $4BAB, 16 bytes/subtune, indexed by A<<4) |
| version | Same 906-instruction code as `JCH/Shift.sid` and `JCH/Little_Test.sid` (only patched immediates differ; Little_Test's state block is 4 bytes shorter). SIDId: matches `JCH_NewPlayer` (generic) and `JCH_DigiPlayer` (the NMI at $40F2). Its 3-voice engine is NewPlayer V20's: an opcode-sequence diff against a V20 tune (`Puterman/I_Could_Eat_a_Knob_at_Night.sid`, play $10C1) is 80 % identical instruction-for-instruction; every non-matching block is a `CPX #$03` track-4 branch or the track-4 code itself ($478A–$48E0), plus three V20-only bits listed in §7. So: **NewPlayer 20 + a fourth (sample) track**. Only JCH's own 1991 tunes use this build (4 in HVSC); 1737 HVSC tunes use plain V20 |
| source | none cached; pure binary RE, cross-checked against a 6000-frame execution trace and probes |

#### 3.5.1 Memory map and state

##### Code

| range | routine |
|---|---|
| $4000/$4003 | `JMP init` / `JMP play` |
| $4006–$4020 | **header variables** (see below); $4021–$403F: text `'EASY DOES IT' BY JCH, 11/8-91-` |
| $4040–$40E8 | `init(A=subtune)` |
| $40E9–$416E | **NMI sample mixer** (CIA2 TA); $416F/$4170 its two flags |
| $4171–$4192 | `play`: tick counter / funk tempo |
| $4193–$41B8 | per-track dispatch: enable check, tick 0 / tick 2 / other |
| $41B9–$4298 | **sequencer step** (prefetch): track byte → pattern → commands → note → `$7F` end-of-pattern |
| $4298–$42D5 | prefetch epilogue: hard-restart (gate off, ADSR $0F/$00) 2 frames early |
| $42D6–$4418 | pattern **command decoder** ($8x dur, $Ax instr, $Cx super) incl. track-4 variants $43B9–$4418 |
| $4419–$4533 | **commit** (tick 0): staged → live, then note-init (instrument load, ADSR, ctrl=$09) |
| $4534–$4741 | per-frame effects: pulse program, filter program, wave/arp table, slide, vibrato |
| $4742–$4789 | **SID write-out** for one voice, then `DEX/BMI/JMP $4195` loop, RTS |
| $478A–$483D | track-4 commit: sample rate from note table, start sample (patches NMI) |
| $483E–$48E2 | track-4 per frame: rate slide, volume envelope step, rate vibrato |

##### Fixed tables (all indexed by Y unless stated)

| addr | size | contents |
|---|---|---|
| $48E3 | 96×2 LE | frequency table, note 0..95 ($0116 … $FD2E), index = note*2 |
| $49A3 | 36×2 BE | sample-rate table (CIA period, hi/lo): $0180,$0170,…,$00C9 (12), repeated (12), then $00C0…$0064 (12); index = (note+2·T)·2 |
| $49EB | 16×16 | digi volume scaler: `out = tbl[vol<<4 | nibble]`; row 0 = all $07 (silent, DC mid), row 15 = identity |
| $4AEB | 2 | `$88 $88` = sample #0 (silence loop) |
| $4AED/$4AF1 | 4+4 | track pointer lo/hi (tracks 0..3, X) — live |
| $4AF5/$4AF9 | 4+4 | track restart pointer lo/hi |
| $4AFD | 4 | `01 02 04 08` bit-per-track (enable mask & filter-routing bit) |
| $4B01 | 4 | `FE FD FB F7` clear-my-routing-bit masks (used as $4B01,X) |
| $4B05 | 3 | `00 07 0E` SID voice register offsets |
| $4B08–$4BAA | 163 | **state block** ($4B09..$4BA4 zero-filled by init); layout below |
| $4BAB | 16/subtune | subtune header: 4×(track lo,hi) +8..+13 zero, +14 speed, +15 track mask (bit7 → 8 restart-pointer bytes follow) |
| $4BBB / $4C21 | 102 + 102 | **wave table**, two parallel columns: A = arp/note byte, B = waveform byte |
| $4C87 | 6×4 | **filter table**: [cutoff or $FF=keep, Δ/frame, frames, next-index]; entry 0 doubles as **funk tempos** ($4C87,$4C88) and **filter-track selector** ($4C8A) |
| $4C9F | 16×4 | **pulse table**: [init byte or $FF=keep, Δ/frame, dir·$80\|frames, next-index] |
| $4CDF | 31×8 | **instruments** (index = instr·8) |
| $4DD7 | 15×8 | **digi instruments** (index = instr·8) |
| $4E4F | 15×8 | **sample descriptors** [start lo,hi, end lo,hi, loop lo,hi, id, 0] |
| $4EC7 | 23 | digi volume envelopes (bytes = row of $49EB; $FF n = jump to n); entry 0 is **patched at run time** by the "set volume" command |
| $4EDE / $4F0F | 49+49 | pattern pointer lo / hi (index = pattern number) |
| $4F40 | 20×2 | **super-command table** [type·16\|p1, p2]; entry 0 = hard-restart AD/SR ($0F,$00) |
| $4F68 | 4 tracks | track data (7, 46, 41, 118 bytes) |
| $503C–$564A | 49 patterns | pattern data |
| $5660–$7D2A | | 4-bit samples, hi nibble first |

##### Header variables ($4006–$4020, X = track 0..3)

`$4006,X` track enabled (mask bit) · `$400A/$400B` scratch (also 'absolute-note' flag) · `$400C,X` hard-restart flag (instr byte2 & $80) · `$4010,X` note (live) · `$4013` track-4 last note · `$4014,X` transpose·2 (live) · `$4017` track-4 last transpose · `$4018,X` gate mask $FF/$FE (live) · `$401B` track-4 gate · `$401C,X` instrument·8 (live) · `$401F` track-4 instrument·8 · `$4020` flags ($10: honour track mask).

##### State block (struct-of-arrays, `base,X`; width 4 when the row has a track-4 slot, else 3)

| addr | w | meaning | | addr | w | meaning |
|---|---|---|---|---|---|---|
| $4B08 | 1 | $D417 shadow | | $4B59 | 3 | pulse index |
| $4B09 | 1 | tick counter (speed..0) | | $4B5C | 3 | pulse frames left |
| $4B0A | 1 | speed | | $4B5F/$4B62 | 3+3 | pulse lo/hi |
| $4B0B/$4B0C | 1 | digi env override value/flag | | $4B65 | 3 | pulse dir ($80 = down) |
| $4B0D/$4B0E | 1 | CIA period lo/hi | | $4B68/$4B69/$4B6A | 1 | filter index / frames left / cutoff |
| $4B0F | 1 | NMI timer-write lock | | $4B6B | 1 | filter type bits (→ $D418 hi nibble, NMI) |
| $4B10 | 1 | digi volume row (vol<<4) | | $4B6C | 1 | funk-tempo toggle |
| $4B11 | 1 | digi envelope index | | $4B6D | 3 | wave index |
| $4B12 | 4 | tie flag (this step) | | $4B70/$4B73 | 3+3 | wave frames left / reload |
| $4B16 | 4 | vibrato on (live) | | $4B76/$4B79 | 3+3 | slide speed lo/hi ($4B7C = trk4) |
| $4B1A | 4 | slide on (live) | | $4B7D | 3 | slide dir ($4B80 = trk4) |
| $4B1E | 4 | pattern position | | $4B81/$4B84 | 3+3 | slide accumulator lo/hi |
| $4B22 | 4 | duration (staged) | | $4B87 | 4 | transpose·2 (staged) |
| $4B26 | 4 | duration countdown (live) | | $4B8B | 4 | instrument·8 (staged) |
| $4B2A/$4B2D | 3+3 | frequency lo/hi (shadow) | | $4B8F | 4 | note (staged) |
| $4B30 | 3 | waveform (shadow) | | $4B93 | 4 | slide on (staged) |
| $4B33/$4B37/$4B3B | 4 | "slide/vib set this step" / "HR pending" (cleared each frame) | | $4B97 | 4 | vibrato on (staged) |
| $4B3E/$4B42 | 4 | vib half-period counter / reload | | $4B9B | 4 | gate mask (staged) |
| $4B46/$4B49 | 3 | vib depth increment / accumulated | | $4B9F/$4BA2 | 3+3 | AD / SR shadow |
| $4B4C | 3 | vib direction | | $4BA5 | 3 | SR override |
| $4B4F/$4B52 | 3+3 | vib offset lo/hi | | $4BAA | 1 | constant $01 (tie-prefetch mode switch, never written) |
| $4B56 | 3 | vib shift count | | | | |

Zero page: `$FB/$FC` sequencer pointer (scratch inside play), `$FD/$FE` sample pointer (owned by NMI), `$01` = $35 (set by init so $FFFA/$FFFE are RAM). Stack: only for the six `PHA/PLA` byte-splitting idioms and the wrapper.

#### 3.5.2 Entry points and conventions

- `init` ($4040): A = subtune. `Y = A<<4`; zero $4B09..$4BA4 (156 bytes); copy 4 track pointers to live and restart slots; speed ← hdr+14; if `$4020≠0`: `$4006,X ← hdr+15 & $4AFD,X` (X=3..0), and if bit 7 of the mask: restart pointers ← the 8 bytes after the header. Clear $D400–$D416; `$4B6C=1`, `$4B09=3`; SEI; `$01=$35`; CIA2: TB latch lo 0, TA hi 0, `$DD0E=1` (TA continuous), `$4170=$416F=1`, NMI vector $FFFA/B ← $40E9, TA lo ← $C0 (=`$4B0D`), `$DD0D=$81` (TA NMI on); CLI; RTS. No relocation, no code copy.
- `play` ($4171): no arguments; clobbers A/X/Y, `$FB/$FC`. Voices processed X = 3,2,1,0 (`DEX/BMI`), i.e. **track 4 first, voice 1 last**. Y is only ever a table index (never a voice). All per-voice routines end in `JMP $4742` (write-out) or `JMP $4783` (skip write-out) — tail-jumps, no JSR inside play at all (the only JSRs in the whole file are the wrapper's).
- Flag argument: `$400A` (a header byte, not a CPU flag) is set to 1 by the wave step when the arp entry is an absolute note and read by the slide code to suppress the slide.
- NMI ($40E9): saves A/Y **into the operands** of the restoring `LDA #`/`LDY #` at $4167/$4169 (`STA $4168 / STY $416A`), no stack; ends `BIT $DD0D; RTI`.

#### 3.5.3 The play routine

```
play():
  if --tick < 0:
      tick = speed
      if speed < 2:                       # 0/1 select funk tempo, not a real speed
          tick = filt[0][funk]; funk ^= 1 (via DEC/BPL/reset-to-1)
  for X in 3,2,1,0:
      if !enabled[X]: continue
      if tick == 2 and durctr[X] == 0:  PREFETCH(X)     # 2 frames before the note
      elif tick == 0 and --durctr[X] < 0: COMMIT(X)     # note-on frame
      else: EFFECTS(X)                                  # every other frame
  # (COMMIT and EFFECTS both end in WRITEOUT unless noted)
```

Frame timing: with speed s a step lasts s+1 frames (tick counts s..0). Speed 2 here → 3 frames/step; verified in the SID log (note-ons at 38, 50, 62, 74 for D2+D0 = 4 steps).

```
PREFETCH(X):                                  # $41B9
  tie[X] = 0
  b = *trackptr[X]
  if b & $80: transp[X] = b<<1; ++trackptr; b = *trackptr     # transpose byte precedes pattern byte
  pat = patptr[b]                                             # re-derived every step
  loop:                                                       # $41E7
    c = pat[pos[X]]
    if c >= $80: DECODE_CMD(c); ++pos; goto loop              # $42D6, see §4
    if c == 0:   ++tie; if gate[X]==$FF: gate_staged=$FE; (trk4: env=release; else: waveidx = instr.release_wave if != instr.wave)
    elif c == $7E: ++tie; gate_staged=$FF                     # tie/hold: keep note, apply new cmds
    else: note_staged=c; if !slide_set: slide_staged=0; if !vib_set: vib_staged=0; gate_staged=$FF
    break
  ++pos; if pat[pos] == $7F:                                  # end of pattern, advance track early
    pos=0; ++trackptr; t=*trackptr
    if t==$FF: trackptr = restart[X]
    elif t==$FE: enabled[X]=0; ctrl←0; return
  if X==3: goto TRK4_FRAME                                    # $483E
  if !tie:                                                    # $429F: hard restart, 2 frames early
    gate[X]=$FE
    if HR[X]: AD/SR ← $0F/$00 (SID + shadows); goto WRITEOUT   # effects frozen this frame
  hrpend[X]=1; goto WAVESTEP                                  # skips pulse+filter this frame ($4BAA==1)

COMMIT(X):                                    # $4419
  if X==3: goto TRK4_COMMIT
  gate=gate_staged; note=note_staged; transp=transp_staged; vib=vib_staged; instr=instr_staged
  slide=slide_staged; if !slide: slideacc=0
  durctr = dur_staged
  if tie: goto EFFECTS                                        # legato: nothing re-triggered
  I = instr table[instr]
  waveidx=I.wave; HR=I.b2&$80; wavectr=wavereload=I.b2&$0F
  pulseidx=I.pulse; if pulse[idx].init!=$FF: pulse = (init&$0F)<<8 | (init&$F0); pulsedir/ctr from entry
  filter: res=I.b3&$F0; f=I.b3&$0F
     f==0: D417 = shadow & ~mybit
     f==8: D417 = shadow & ~mybit; start filter program
     else: type=f<<4; D417 = (shadow&$0F)|mybit|res; start filter program (idx=I.filt, cutoff/dur from entry)
  AD ← I.ad; SR ← SRoverride[X] (set to I.sr by the $Ax command; $9x changes it)
  ctrl ← $09                                                  # TEST|GATE, no freq/wave this frame
  return (no WRITEOUT)                                        # $4531 JMP $4783

EFFECTS(X):                                   # $4534
  if X==3: goto TRK4_FRAME
  PULSE:  if --pulsectr<0: idx=next; load dir/dur; if init!=$FF: set pulse.  pulse ± Δ (16-bit)
  FILTER (only when X == filt[0].track): if --filtctr<0: idx=next; dur; if cut!=$FF: cutoff=cut. cutoff += Δ
  WAVESTEP:                                   # $45D2
    a = waveA[waveidx]  ($7E: stay on previous entry; $7F: waveidx = waveB[idx], re-read)
    if I.b2&$40 (raw mode): freqhi=a, freqlo=0
    elif a&$80: freq = ftab[(a<<1)&$FF]; abs=1
    else: freq = ftab[((a+note)<<1)+transp]; abs=0
    wave = waveB[waveidx]
    if --wavectr<0: wavectr=wavereload; ++waveidx
  if slide: slideacc ± speed(16-bit); if !abs: freq += slideacc; goto WRITEOUT   # slide excludes vibrato
  if !hrpend and vib:
    step = (ftab[note+1]-ftab[note]) + (depthacc<<8) >> shift
    if --vibctr<0: dir^=1; vibctr=half
    viboff ± step; freq += viboff; depthacc += depthinc
  WRITEOUT:                                   # $4742
    slide_set=vib_set=hrpend=0
    Y=7*X: PWlo,PWhi, D416=cutoff, FREQlo,FREQhi, AD,SR, CTRL = wave & gate
```

Track 4 (sample channel):

```
TRK4_COMMIT ($478A): live ← staged (vib, instr, slide, gate, dur); lock=1
  if note/transp changed: period = instr.rate if instr.ratelo!=0 else ratetab[(note+2T)*2]
  if !tie: envidx = override? $4B0B : dinstr.attack; mute=1; $FD/E=sample.start;
           patch NMI immediates: end lo/hi ($4140/$413A), loop lo/hi ($4144/$4148); mute=0
TRK4_FRAME ($483E): if slide: period ± speed(8-bit)
  v = env[envidx]; if v==$FF: envidx=env[envidx+1]; v=env[envidx];  volrow=v<<4; ++envidx
  if vib: triangle on period (±$4B55, counter $4B41 over ±$4B45)
  clear set-flags; lock=0
NMI (every CIA2 TA underflow, ~2.5–10 kHz):
  if mute: D418 = $08|type
  else: nib = odd? (*$FD)>>4 : (*$FD)&$0F, advance on the even nibble; if ptr==end: ptr=loop
        D418 = voltab[volrow|nib] | type
  if !lock: DD04/DD05 = period          # rate change takes effect at next reload
```

#### 3.5.4 Data formats

**Track** (per subtune, per track; pointer in header): sequence of `[T] P` items — `P` < $80 pattern number; an optional preceding byte ≥ $80 is a transpose ($80|t, t unsigned 0..127 semitones; used here: +0,+12,+14,+24,+34); `$FF` restart track from its restart pointer, `$FE` stop track (disable + ctrl 0). The transpose persists (it is re-read every step from the current track byte, so it belongs to the item, not the track).

**Pattern** (until `$7F`): `[cmd]* step`, where

| byte | meaning |
|---|---|
| $80–$9F | duration: bits 0-3 = steps−1, bit 4 = **tie** (no retrigger; a new note number = legato) |
| $A0–$BF | instrument 0..31 (stored ·8); resets SR override to the instrument's SR; on track 4 clears the volume override |
| $C0–$FF | super command n=0..63 → `$4F40[2n]` = type<<4\|p1, `$4F40[2n+1]` = p2 (types below) |
| $00 | rest = gate off (release), tie implied; track 4: switch to release envelope |
| $01–$7D | note (index into 96-entry table before transpose/arp) |
| $7E | hold/tie: keep note, gate on, apply preceding commands (used to add vibrato/slide mid-note) |
| $7F | end of pattern |

Counts in this song: 361 notes, 243 rests, 119 `$7E`, 565 durations (D0 176, D1 108, D2 92, DF 8, tie-bit 90), 85 instrument, 120 super. Duration/instrument/super commands are sticky across steps except that a note clears slide/vibrato unless re-issued in the same step.

**Super command types** (voices; `p1` = low nibble of byte 0, `p2` = byte 1):

| type | effect |
|---|---|
| $0x/$1x/$2x | slide: dir = bit 5 ($2x down), speed = p1<<8\|p2 added to a 16-bit accumulator each frame; excludes vibrato |
| $6x | vibrato: p1 = depth increment/frame (added to the *high byte* of the step: growing vibrato), p2 hi = half period (frames), p2 lo = right-shift of the semitone step |
| $9x | SR override = p2 (applied at next note-init) |
| $Ex | speed = p2 |
| other | `instr[byte0&$1F].wave = instr[..].release_wave = p2` — **patches the instrument table** |

Track-4 variants: $0x/$2x slide of the CIA period (speed p2, 8-bit); $6x vibrato on the period (p1 = period count, p2 = step); $9x volume-envelope override = p2; $Ex speed; other = **write p2 into env table entry 0** and select it (= "set digi volume"; 41 uses here, values 3 and 8).

Song usage: 66× vibrato, 39× slide (up 23, down 16), 15× digi volume, 0× the rest.

**Instrument** (8 bytes, index instr·8): `AD, SR, flags(b7 hard restart, b6 raw-frequency wave table, b0-3 wave step frames), filter(hi res, lo type: 0 off, 1/2/4 LP/BP/HP bits, 8 = program only), filter idx, pulse idx, wave idx, release wave idx`. 31 present; every one here has wave==release wave, so `$423B` never patches the index.

**Wave table** (2 columns, byte index): A: `$00–$7D` semitone offset added to note (+transpose), `$80|n` absolute note n (slide suppressed), `$7E` hold previous, `$7F` jump to B[i]; B: SID control byte (gate bit is masked by the gate mask at write-out; values used: $11,$21,$41,$81,$15,$17,$40,$80,$F0…). Step rate = instrument b0-3 frames per entry (0 = every frame).

**Pulse table** (4 bytes): `init` (XY → pulse $0Y·X0, $FF keep), `Δ` (added to lo with carry into hi, so $FF ≡ +255), `dir|frames` (b7 subtract), `next` (byte index of next entry; entries chain into loops, e.g. $08↔$0C = ±$40 for 8 frames = a $200-wide sweep).

**Filter table** (4 bytes): `cutoff` ($FF keep), `Δ/frame` (signed 8-bit into the 8-bit cutoff → $D416 only; $D415 is never written), `frames`, `next`. Entry 0 = [funkA, funkB, –, filter-track].

**Digi instrument** (8): `attack env idx, release env idx, rate hi, rate lo (≠0 ⇒ fixed rate), 0,0,0, sample id`. **Sample descriptor** (8): `start, end, loop` (LE words; end/loop are patched into the NMI's `CMP #`/`LDA #` operands), id, 0. **Volume envelope**: one byte/frame = row of the 16×16 scaler; `$FF n` = jump to n (used to hold: `… 0A FF 0A`).

**Frequency table**: 96 LE words C-0..B-7 (PAL, $0116 first). Vibrato uses `ftab[n+1]-ftab[n]` (one semitone) as its base step — the delta is *not* stored, it is computed from two neighbours.

#### 3.5.5 SID write schedule

Per voice per frame, in this order (voice 3 → 2 → 1): `PWlo PWhi $D416 FREQlo FREQhi AD SR CTRL`. Everything is rewritten every frame from shadows (AD/SR included), so the SID image is a pure function of the shadows. `$D416` (cutoff hi) is written three times per frame (once per voice, same value); `$D415` never; `$D417` only at note-init when the instrument's filter nibble is processed (863 writes in 6000 frames); `$D418` **never from the IRQ** — the NMI writes it thousands of times per frame as `voltab | filter type` (so the "master volume" is the sample amplitude row, and a tune with no sample playing still loops sample 0 = `$88` = DC middle).

Hard restart (measured, voice 1, speed 2): frame N−2: `AD/SR=$0F/$00` (written directly *and* by the write-out) with CTRL gate bit cleared (`$40`); N−1: hold; N: only `AD, SR, CTRL=$09` (TEST+GATE — oscillator held in reset, envelope restarted); N+1: `FREQ` (new note), `PW`, `CTRL = wave|1`. So pitch and waveform of a new note land one frame after the gate; the frame N write-out is skipped entirely (`$4531 JMP $4783`). Rests write CTRL with bit 0 cleared and leave ADSR alone (release).

Volatile reads: none ($D012/$D41B/$D41C never read). Timing-sensitive: the NMI runs concurrently; the play routine guards its two shared quantities with flags (`$4B0F` timer, `$4170` pointer) instead of SEI.

#### 3.5.6 Techniques specific to this player

| technique | citation | why |
|---|---|---|
| **Register save into immediates** in the NMI: `8D 68 41 STA $4168; 8C 6A 41 STY $416A` restored by `A9 00 / A0 00` at $4167/$4169 | $40E9 | 4 cycles cheaper than PHA/PLA pairs at ~10 kHz |
| **Operand patching of the NMI's end/loop constants** from the sample descriptor (`STA $4140` = operand of `C9 51 CMP #`, `$413A`, `$4144`, `$4148`) | $4821–$4836 | avoids 4 loads from a table inside the NMI; the mute flag `$4170` covers the non-atomic patch |
| **Data-table patching as commands**: super default writes `$4CE5,Y/$4CE6,Y` (instrument wave pointers); track-4 default writes `$4EC7` | $43B0, $4412 | "set X" commands stored where the consumer already reads |
| **Struct-of-arrays, `abs,X`, X = track**, rows 3 or 4 wide; Y reserved for table indexes; voice register base via `LDY $4B05,X; STA $D400,Y` | throughout, $474D | no pointer arithmetic; a voice loop is `DEX/BMI` |
| **Two-frame look-ahead**: sequencer runs at tick 2 into *staged* copies ($4B87–$4B9E), committed at tick 0 | $41A2/$4419 | gives hard restart its 2 frames without a second sequencer pass |
| **Tail-jump structuring**: every path is `JMP $4742` / `JMP $4783`; zero JSR/RTS depth inside play | $4531,$4586,$46AC… | saves stack and cycles; routine boundaries are jump targets, not calls |
| **Test-bit hard restart**: `LDA #$09; STA $D404,Y` on the note frame, real wave next frame | $452C | oscillator + envelope reset in one write |
| **PHA/…/PLA to split one byte into two fields** (`PHA; AND #$F0; STA; PLA; AND #$0F; STA`) | $4477,$4494,$44A3,$44B5,$4560,$454D,$42D6 | cheaper than a temp when A must survive |
| **Table columns as parallel arrays** (wave A/B; pulse/filter 4-byte records addressed as `base+k,Y`) | $4BBB/$4C21, $4C9F..$4CA2 | one Y serves both columns |
| **Overloaded table entry**: filter entry 0 = funk tempos + filter-track selector | $4C87–$4C8A | zero extra bytes |
| **Sentinel-driven programs**: `$7E/$7F` in wave, `$FF` in pulse/filter init and envelopes, `$FF/$FE` in tracks | §4 | table = tiny bytecode with jump |
| **Counter idiom** `DEC x,X; BPL keep; reload` for durations, wave, pulse, filter, tick | $4653,$453B,$45A6,$4171 | 0 means "1 more frame", stores dur−1 |
| **Two-flag negotiation with an interrupt** (`$4B0F`, `$4170`) instead of SEI/CLI | $47AA/$48DD, $480D/$483B | keeps the NMI period stable |
| **Header bytes as variables** (`$4006–$4020` inside the identification block) | $4195 etc. | the editor's "header" is live state |
| **Bit-mask arithmetic for filter routing**: `AND $4B01,X` / `ORA $4AFD,X` | $44D3,$44DF | per-voice bit set/clear without shifts |
| **Semitone step derived from the frequency table** (`ftab[n+1]-ftab[n]`) then `LSR/ROR` by a per-note shift count | $46B9–$46E1 | vibrato depth in musical units, no extra table |
| **`CPX #$03` guards** sprinkled through shared code to divert track 4 | $422B,$428B,$4298,$42FB,$4324,$4534 | one sequencer serves SID voices and the sample channel |
| **`ASL A` on the transpose byte** (drops the $80 tag and ×2 in one op) | $41CD | table index is note·2 |
| **`INC lo; BNE; INC hi`** 16-bit pointer bump; `LDA lo; ADC #1; STA; LDA hi; ADC #0` elsewhere | $41D1, $425B | — |
| No illegal opcodes; no JMP (ind); no RTS-trick dispatch; command dispatch is a **compare chain** on `AND #$E0` then on `AND #$F0` | $42D6–$4418 | 20-ish commands, ordered by frequency |

#### 3.5.7 What it reduces to

The whole player is: **a 4-track step sequencer running two frames ahead of a per-voice register image, plus three little bytecode interpreters (wave, pulse, filter) and two arithmetic effects (slide, vibrato) that modify that image before it is copied to the SID once per frame.** Per voice ≈ 45 bytes of state; globally 9 more; the SID image is fully determined by shadows (`freq $4B2A/2D`, `pulse $4B5F/62`, `wave $4B30 & gate $4018`, `AD $4B9F`, `SR $4BA2`, cutoff `$4B6A`, `$4B08`), so the play routine is `shadows' = f(shadows, tables, song[pos])` followed by 8 stores.

```
each frame:
  tick = (tick-1) mod (speed+1)
  for v in tracks (4..1):
    if tick==2 and dur[v]==0:  stage next step; if !tie: gate off (+ADSR 0F00 if HR)
    if tick==0 and --dur[v]<0: live=staged; if !tie: load instrument, ADSR, ctrl=09; continue
    pulse := pulseprog.step(); if v==filtertrack: cutoff := filtprog.step()
    (arp, wave) := waveprog.step(); freq := ftab[note+arp+T]  (or abs/raw)
    freq += slide ? slideacc : (vib ? viboffset : 0)
    SID[v] := (pulse, cutoff, freq, AD, SR, wave & gate)
  track 4 does the same with (period, volume-row) instead of (freq, wave), and an NMI turns
  (sampleptr, volume-row) into $D418 writes.
```

Statically decidable: everything except the song position — all tables are constant except the two patched cells ($4CE5/$4CE6 by super default — unused here; $4EC7 by digi volume — used, but only ever with a constant operand from the song). SMC in code is confined to the NMI's four immediates (sample end/loop) and two register-save operands; none of it is executed by the IRQ path, so **the IRQ-side player has zero self-modification** — the trace's "0 SMC sites" is exact for the play path. Volatile input: none.

Dead in this tune: track stop `$FE` ($4286), funk tempo ($4180), raw-frequency wave mode ($45DC), wave `$7E`, release-wave switch ($4246), commands $9x/$Ex/set-wave-pointer, track-4 slide/vibrato ($4843, $4892), subtune restart pointers ($408F), the `$4020==0` init branch. `$4BAA` is a constant 1 (the `BEQ $42D3` alternative — pulse/filter continue on a tie prefetch — is never taken).

Family variation: plain **NewPlayer V20** = the same engine minus every `CPX #$03` branch and $478A–$48E0; it additionally (a) saves/restores `$FB/$FC` around play, (b) keeps master volume in header byte `$1009` and writes `$D418 = filtertype|vol` at the end of every frame and when the filter is set, (c) adds a per-voice fine-tune constant to the frequency (`CLC; ADC $1743,X` at $150F; `$1743..$1745` is data, never written). Dane's NewPlayer derivatives are a different lineage (own SIDId signature). Builds differ only in immediates and table offsets; code is byte-for-byte a template — measured over 270 JCH tunes in [prototype-jch.md](prototype-jch.md) §2, which certifies two plain-V20 builds and confirms (a)-(c) in the decompiled text ($1740,X the SID register offsets, $1743,X the fine-tune, $1009 the volume).

#### 3.5.8 Decompiler notes

- **Code/data boundary**: code is exactly $4040–$48E2 (plus the NMI inside it) — data begins at the frequency table; the header block $4006–$4020 sits *inside* the code range and is read/written as variables (`LDA $4006,X`, `STA $4013`) — treat it as a struct, not as unreachable code. The 31-byte text at $4021 is dead.
- **Voice struct recovery**: every `abs,X` in play with X ∈ 0..3 addresses one field of a per-track record; rows are 4 wide iff the field is also used by track 4 (`$4B12,$4B1E,$4B22,$4B26,$4B87..$4B9E`), 3 wide otherwise — a decompiler must not assume uniform stride; the record is a union of a "sequencer" part (4) and a "SID voice" part (3), track 4 having its own scalars ($4013,$4017,$401B,$401F,$4B0B–$4B11,$4B41,$4B45,$4B55,$4B7C,$4B80).
- **Table typing**: `LDA tbl,Y` where Y came from `LDA idx,X` and `tbl+1..tbl+3` are also read with the same Y ⇒ N-byte record table (pulse/filter = 4, instrument/digi/sample = 8, super = 2, wave = 2 parallel columns of 102). Frequency table: `ASL` before use ⇒ 16-bit LE array. `$49A3`: `tbl,Y → hi; tbl+1,Y → lo` ⇒ big-endian.
- **Sequencer bytes**: typing is by the compare chain at $41EA/$42D6: bit 7 ⇒ command (`AND #$E0` selects $80/$A0/else); `$00/$7E/$7F` sentinels; else note. Track bytes: bit 7 ⇒ transpose prefix. The pattern pointer is recomputed from the track byte each step, so the "current pattern" is not a state variable — only `trackptr` and `pos` are.
- **Two-phase note**: model the staged→live copy explicitly; a naive per-frame decompilation will otherwise see the note-on ADSR/CTRL at tick 0 and freq/wave at tick 0+1 as unrelated.
- **Calling convention**: play is one procedure with a 4-iteration `X` loop whose body is a DAG of tail-jumps converging on `$4742` or `$4783`; `JSR` never occurs. Model routine boundaries as basic-block regions, and `$4783` (DEX/BMI/JMP) as the loop latch. `Y` is dead at every block entry except the write-out (`LDY $4B05,X`).
- **SMC**: (1) NMI immediates $4139/$413F/$4143/$4147 = the sample descriptor's end/loop words → variables; (2) $4168/$416A = NMI-local A/Y → callee-saved temporaries; (3) $4CE5/$4CE6/$4EC7 are data cells written by commands → ordinary stores. Nothing in the IRQ path is patched.
- **Concurrency**: the NMI and IRQ share `$FD/$FE`, `$4B0D/$4B0E`, `$4B10`, `$4B6B`, `$4170`, `$4B0F`; `$D418` belongs to the NMI. A frame-level replay must model the NMI as an independent process (or, for a pure-SID model, treat `$D418 = voltab[row|nib]|type` as "master volume ≈ row").
- **Invariants**: X = track for the whole play body; `$4B09 ∈ [0,speed]`; `$4B1E,X` < pattern length; `pulse hi` ≤ $0F; `gate mask ∈ {$FE,$FF}`; `waveidx` < 102; `$4BAA==1`.
- **Traps**: the filter program is stepped inside voice-1's (X=0) pass only, guarded by a *data* byte ($4C8A); the tick-2 prefetch requires speed ≥ 2 (a speed of 0/1 means funk tempo, and funk values < 2 would silently drop the prefetch); the wave-A `BMI` "absolute note" flag lives in the header scratch `$400A` and is consumed 100 bytes later; entry 0 of the super table is not a command but the HR ADSR pair; `$D416` triple-write and `$D415` absence are intentional (8-bit cutoff).

### 3.6 Tim Follin — Ghouls'n'Ghosts (1989)


#### 3.6.0 Identity

| item | value |
|---|---|
| tune | `MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid` (PSID v2, 32 subtunes, speed=0 → one `play` per 50 Hz frame, no CIA/multispeed, no IRQ installed) |
| load / init / play | $2980–$733F / init $6110 (A = subtune 0..31) / play $6234 |
| player proper | $6110–$6DF6 : 2947 bytes of code in $6110–$6CB6, then tables (jump table 126 B, note table 194 B, SFX tables) |
| song data | 10 blocks stored $2A44–$60B1 in the rip; a subtune's blocks are copied to $730E+ (their original run address) by the rip's loader stub at $2980/$7316 — wrapper, not player |
| subtunes | 0–10 songs (0–5 end with a stop, 6–10 loop), 11–31 sound effects (each starts 1–3 tracks on chosen voices over whatever is playing) |
| provenance | no source; pure binary RE. Cross-checked against Bionic_Commando (Tim 1988), L_E_D_Storm (Tim 1988), Sly_Spy (Geoff 1990): same lineage — same 3× unrolled voice template, same patched-`JMP` command dispatch, same `LDY #$1C` SID clear, same filter constants ($0032/$04CD). Command set grew 17/18 (BC) → 20 (LED Storm) → 21 (GnG, Sly Spy) |
| SMC | 24 varying sites, all inside the player: 21 are immediate-operand cells used as variables, 3 are the dispatch `JMP` operands |
| volatile inputs | none: no read of $D012/$D41B/$D41C/timers. The `$D41C` in the write map is the init clear loop `LDY #$1C` writing $08 then $00 to $D400..$D41C (4 harmless writes past the last R/W register) |
| play return | A = $7B \| $7C \| $7D : $FF while any voice active, 0 when all three tracks hit `$86` (game polls end-of-jingle) |

#### 3.6.1 Memory map and state

Code (all addresses post-init image = load image; the player is not relocated):

| routine | range | role |
|---|---|---|
| `init` | $6110–$6122 | Y=A; clear ZP $21–$90; $01=$37; `JMP $7316` (rip stub) |
| song setup | $6153–$61A7 | X = song: load 3 track pointers from `$730E+X` tables; set per-voice defaults; `JSR $61A8` |
| `sidclear` | $61A8–$61DC | $08 then $00 into $D400..$D41C; zero ZP $21–$96; init SMC cells |
| SFX start | $61DD–$622D | X = sfx: walk (voice*2, ptrlo, ptrhi) list; reset that voice's state |
| voice 0 | $6234–$6420 | per-voice template (493 B) |
| voice 1 | $6421–$660F | same template, +2 bytes (`CMP #$01` at $65C7) |
| voice 2 | $6610–$67FE | same template (`CMP #$02` at $67B6) |
| filter + exit | $67FF–$6857 | global cutoff sweep, $D415/$D416, return flags |
| handlers | $6858–$6CB6 | 21 commands × 3 voices, each copy `JMP`s back into its own voice's sequencer |
| tables | $6C37/$6C76 (jump lo/hi, indexed with X=cmd byte ≥$80, so real base $6CB7/$6CF6, 63 entries = 3 voices × 21) ; $6D35 note-lo, $6D96 note-hi (97 entries) ; $6DF7/$6E0D SFX pointer lo/hi (entries 1–21) ; $6E22.. SFX lists | — |
| rip stub | $2980–$29E6 loader, $7316–$733E dispatcher (overwritten by the block copy it triggers) | — |

Zero page — the whole player state is $21–$97 (119 bytes), struct-of-arrays. Voice n uses `base+n` (stride 1) except pointer/word pairs which use `base+2n`. Every cell below is verified by the dynamic access map (which PCs read and write it).

| v0 (v1,v2) | meaning | written by |
|---|---|---|
| $21/22 ($23/24, $25/26) | track pointer | init, seq advance, $81 $87 $8A $8B |
| $27 (+n) | frames left in current note | note fetch, per-frame DEC |
| $2A (+n) | waveform/control byte (last `$8D`) | $8D |
| $2D (+n) | loop counter | $82/$81 |
| $30/31 (+2n) | loop restart pointer | $82 |
| $36 (+n) | gated-instrument flag (nonzero → gate on at note, gate off timers apply) | $83, SFX start |
| $39 (+n) | gate-off delay per note (frames) | $83 |
| $3C (+n) | gate-off countdown | note fetch, per-frame |
| $3F/40 (+2n) | pulse width (16-bit, hi ≤ $0F) | $80, note fetch, pulse sweep |
| $45/46 (+2n) | pulse reset value (0 = don't reset at note) | $80 |
| $4B (+n) | pulse sweep speed | $80 |
| $4E (+n) | transpose (signed, added to note byte) | $8C |
| $51 (+n) | attack-blip length; $54 (+n) blip waveform; blip freq in code cells $6BDA/DB ($6BF5/F6, $6C10/11) | $8F |
| $57 (+n) | vibrato delay & enable; $7E (+n) delay counter; $8A (+n) depth/frame; $87 (+n) half-period; $84 (+n) half-period counter; $81 (+n) initial direction | $8E, note fetch |
| $5A (+n) | trill: frames at offset note; $5D (+n) frames at base; $93 (+n) semitone offset; $60 (+n) trill counter | $91, note fetch |
| $63 (+n) | portamento speed (note-index units/frame); $66 (+n) current note index; $6C (+n) porta target | $92, note fetch |
| $69 (+n) | call-stack depth; stacks $6B1F/$6B22 ($6B25/28, $6B2B/2E), 3 deep | $8A/$8B |
| $75 (+n), $78 (+n) | freq lo/hi shadow (what the note "should" be) | note fetch, vib, trill, porta |
| $7B (+n) | voice active: $FF running, 0 stopped | init, $86, SFX |
| $8D (+n) | blip countdown | note fetch |
| $90 (+n) | release point: gate off when $27 == $90 ($FF = never) | $90 |
| global $6F/$70 | filter cutoff (11-bit: $70:$6F, written as D416=$70<<5\|$6F>>3, D415=$6F) | $88, note fetch of owner voice, sweep |
| $71/$72 | cutoff reset value; $73 sweep speed; $74 owner voice (0/1/2) | $88, $89 |
| $96/$97 | temp (SFX list pointer; D416 assembly) | |
| $FA–$FD | loader temp (rip stub only) | |

SMC operand cells (variables that live inside instructions):

| cell (v0, v1, v2) | instruction | values | meaning |
|---|---|---|---|
| $62EE ($64DB, $66CA) | `LDA #imm; BNE` | 1 / $FF / 0 | pulse mode: hold / sweep up / sweep down |
| $63D4 ($65C1, $67B0) | `LDA #imm; STA $62EE` | 1 / $FF | pulse mode to load at note-on (from `$8D` wave bit 6) |
| $6269 ($6456, $6645) | `LDY #imm; BEQ` | 0 / 1 / $FF | vibrato direction (0 = subtract) |
| $629E ($648B, $667A) | `LDA #imm; EOR #$FF` | 0 / $FF | trill phase |
| $640F ($65FE, $67ED) | `LDA #imm; BNE` | 0..$32 | fixed note length (0 = read length byte) |
| $6382 ($656F, $675E) | `LDX #imm; BEQ` | 0 / $FF | one-shot "skip transpose" ($93, unused here) |
| $63EB ($65DA, $67C9) | `LDA #imm; STA $6800` | 0 / 2 | filter direction to load at owner note-on |
| $6375/76 ($6562/63, $6751/52) | `JMP abs` operand | 21 targets | command dispatch |
| $6800 | `LDA #imm; BNE` | 0 / $FF | filter direction (0 = down) |
| $6813, $6819, $682D, $6833 | `CMP #imm` | | filter min hi, min lo, max hi, max lo |
| $6219 | `STA abs` operand | $640F/$65FE/$67ED | SFX start: which voice's fixed-length cell to zero (computed store via table $622E/$6231) |
| $6BDA/DB, $6BF5/F6, $6C10/11, $6A05–$6A0A | data cells embedded in handler code | | blip freq per voice; default pulse width (never set: `$94` handler is buggy and unused) |
| $29D8 | `CPY #imm` | | rip loader: bytes in last partial page |

#### 3.6.2 Entry points and conventions

- `init` (A = subtune): clears $21–$90, sets $01 = $37, tail-jumps to the rip stub: A < 11 → loader copies block `$2A1A[A]` and block `$2A25[A]` to their run addresses (under $01=$35), then `X = $2A0F[A]` (song 0..6) → `$6153`. A ≥ 11 → `X = A-10`, $D418 = $0F, all six SR/AD regs = 0, `JMP $61DD` (SFX). Songs 7–10 reuse song-index 0 with different blocks (each block carries its own pointer tables at $730E).
- Song setup `$6153`: pointers `$21..$26` ← `$730E+X` (six 7-entry tables: v0lo,v0hi,v1lo,v1hi,v2lo,v2hi); `$7B..$7D = $FF`; `$90..$92 = $FF`; `$27..$29 = 1` (fetch on first frame); `$40/$42/$44 = 3` (PW $03xx); filter dir 0, min $0032, max $04CD; SMC cells: pulse mode 1, transpose-skip 0.
- SFX start `$61DD` (X = 1..21): list of `(voice*2, lo, hi)` terminated by a byte ≥ $80; for each: `$21,X` ← ptr, then with X/2 = voice: `$7B,$39,$90 = $FF`, `$27,$36 = 1`, fixed-length cell = 0 (via patched `STA` at $6218), `$4E,$93,$5A,$5D,$57,$8A,$87,$81 = 0`. Other voices keep playing.
- `play`: no arguments; voices processed 0,1,2 then filter; returns A = active-flags OR. Voice n's block ends by `JMP` to voice n+1 (`$6421`, `$6610`), voice 2 to `$67FF`. Handlers end with `JMP $6356` (advance pointer by Y, refetch) or `JMP $6360` (pointer replaced, Y=0 refetch) — v1: `$6543/$654D`, v2: `$6732/$673C`. There are no JSRs in the per-frame path except none — the whole frame is one linear procedure of `JMP`s.
- Register roles: inside the sequencer Y = byte offset from the track pointer of the byte being consumed (0 = command byte, 1.. = args); X is scratch (note index / cmd byte / SID register). Flags as arguments: `$8D` handler uses C from `ASL ASL` (wave bit 6) to choose `LDA #$FF`/`LDA #$01`; `LDA #imm; BNE/BEQ`, `LDY #imm; BEQ` branch on a constant that SMC makes variable.

#### 3.6.3 The play routine

```
play:
  VOICE(0); VOICE(1); VOICE(2); FILTER; return $7B|$7C|$7D

VOICE(v):                                  ; v0 addresses in brackets
  if active[v] >= 0 goto next voice        ; [$6234] $FF = running
  ; 1. attack blip end
  if blipcnt: if --blipcnt == 0: D400/1 = freqsh; if gated: D404 = wave|1      [$623B]
  ; 2. vibrato / slide
  if vibdelay:                                                                    [$6258]
     if vibcnt and --vibcnt: skip
     freqsh += (dir==0 ? -depth : +depth)   (16-bit); D400/1 = freqsh
     if --halfcnt == 0 and halfper: halfcnt = 2*halfper; dir ^= $FF
  ; 3. trill  (else portamento)
  if trillcnt and --trillcnt == 0:                                                [$6295]
     phase ^= $FF
     if phase: trillcnt = tA; note += off  else: trillcnt = tB; note -= off
     freqsh = notetab[note]; D400/1 = freqsh
  elif portaspd and note != target:                                               [$62BE]
     note = step(note, target, portaspd) clamped; freqsh = notetab[note]; D400/1
  ; 4. pulse
  mode = SMC: 1 hold | $FF up | 0 down                                            [$62ED]
  down: pw -= spd; if pw.hi<0 or (pw.hi==0 and pw.lo<$64): mode=$FF and do up
  up:   pw += spd; if pw.hi>$0F or (pw.hi==$0F and pw.lo>=$9B): mode=0 and do down
  if mode != 1: D402/3 = pw
  ; 5. duration / gate off
  --dur                                                                           [$6338]
  if dur == release or gateoff == 0: D404 = wave & $FE ; (gateoff floors at 0)
  else --gateoff
  if dur != 0 goto next voice
  ; 6. sequencer (runs until a note is fetched)
  Y = 0                                                                           [$6360]
  loop: b = (ptr),Y
    if b >= $80: Y++; goto HANDLER[b]        ; patched JMP; handler continues loop
    if b == 0:  Y++; gateoff = gatelen; goto LENGTH   ; (rest; unused in this tune)
    idx = b + transpose                       ; one-shot skip via SMC $6382 (unused)
    if portaspd: target = idx else note = idx ; (porta: freq stays, slides later)
    freqsh = notetab[note]; D400/1 = freqsh; Y++
    trillcnt = tA
    if vibdelay: vibcnt = vibdelay; dir = dir0; halfcnt = halfper
    phase = 0
    gateoff = gatelen
    if pwreset: pw = pwreset; mode = SMC(from last $8D)
    if filtowner == v and cutreset: cutoff = cutreset; filtdir = SMC(from $88)
    if gated: w = wave
              if bliplen: blipcnt = bliplen; D400/1 = blipfreq; w = blipwave
              D404 = w | 1
    LENGTH: dur = SMC fixedlen; if 0: dur = (ptr),Y; Y++
    ptr += Y
  goto next voice

FILTER:                                                                           [$67FF]
  dir = SMC $6800 (0 down / $FF up)
  down: cut -= spd; if cut.hi<0 or (cut.hi==minhi and cut.lo<minlo): dir=$FF, do up
  up:   cut += spd; if cut.hi>=maxhi and cut.lo>=maxlo: dir=0, do down
  D415 = cut.lo ; D416 = cut.hi<<5 | cut.lo>>3
```

There is no tempo/speed counter: `dur` is a frame count. There is no instrument table: instruments are the commands that precede a note (`$85` ADSR/filter registers, `$8D` waveform, `$8E` vibrato, `$8F` blip, `$80` pulse, `$83` gate length, `$91` trill, `$92` porta), latched into the voice state.

#### 3.6.4 Data formats

Track = one byte stream per voice; no orderlist/pattern split. Structure comes from `$8A` calls (3-deep return stack, max depth 2 used), `$82/$81` counted loops (one loop register per voice, non-nesting), `$87` jumps (song loop), `$86` end.

Byte grammar (verified: a static parse with exactly this grammar consumes all 33 tracks of the 11 songs with zero unknown bytes and terminates on `$86` (songs 0–5) or a `$87` cycle (6–10); handler execution counts match):

| byte | args | handler v0 | meaning |
|---|---|---|---|
| $01–$7F | [len] | $6381 | note index (table index = byte + transpose; 97-entry table, C-ish at 1..). `len` byte present only when fixed length = 0 |
| $00 | [len] | $6377 | rest/tie: no freq/gate write, restarts gate-off timer (never used) |
| $80 s lo hi | 3 | $6999 | pulse: speed s, reset value hi:lo (0 → default cell $6A05) |
| $81 | 0 | $68A3 | loop end: `if --cnt: ptr = loopstart` |
| $82 n | 1 | $6858 | loop begin: cnt = n, loopstart = after this cmd |
| $83 g | 1 | $68D0 | gated instrument; gate off g frames after note-on ($FF ≈ never) |
| $84 n | 1 | $68EE | fixed note length n (0 → explicit length bytes) — stored in SMC `$640F` |
| $85 (r v)* T | var | $6909 | raw SID writes `$D400+r = v`, r < $80, terminated by any byte ≥ $80 (usually $FF). Used for ADSR (r=5,6,$C,$D,$13,$14), $17, $18 — the "instrument/ADSR/volume" mechanism |
| $86 | 0 | $698A | stop voice (`INC $7B`), continue with next voice |
| $87 lo hi | 2 | $6AD0 | jump |
| $88 s d lo hi mlo mhi Mlo Mhi | 8 | $6A0B | filter: speed, direction (0/2 → SMC), cutoff = reset = hi:lo, min, max |
| $89 | 0 | $6AA7 | filter owner = this voice (note-on resets cutoff) |
| $8A lo hi | 2 | $6ABC | call (push ptr+3) |
| $8B | 0 | $6B31 | return |
| $8C t | 1 | $6B64 | transpose (signed) |
| $8D w | 1 | $693F | waveform: `D404 = w` immediately, `wave = w`, pulse mode ← (w & $40 ? sweep-up : hold) at next note |
| $8E d dp hp dir | 4 | $6B7C | vibrato: delay d (0 = off), depth dp per frame, half period hp (0 → 256-frame halves = slide), initial dir (0 down first) |
| $8F n w lo hi | 4 | $6BC1 | attack blip: first n frames of each note play freq hi:lo with wave w, then switch to note freq/wave |
| $90 r | 1 | $6C12 | release: gate off when r frames remain |
| $91 off tA tB | 3 | $6C2A | trill: base tA frames, then +off tA frames, base tB, +off tA … (`0c 01 ff` = one-frame octave flick) |
| $92 s | 1 | $6C60 | portamento speed in note-index units/frame (0 off); a note with porta on sets the target only |
| $93 | 0 | $6C78 | next note untransposed (one-shot; unused) |
| $94 lo hi | 2 | $6C8A | default pulse width (bug: both bytes go to $6A05; unused) |

Census over the 11 songs (static parse): 6 475 notes (indices 1–97, most common 65), lengths 1–255 (mode 3 and 8 frames), commands: $83 449, $8D 439, $85 423, $8A/$8B 342 each, $90 322, $8E 282, $8C 234, $82 205, $81 204, $91 171, $84 150, $8F 146, $80 109, $92 89, $87 19, $86 18, $88 15, $89 10, $93/$94 0. Waveforms used: 00 01 10 11 14 15 20 21 22 40 41 42 50 51 80 81. `$85` registers: 5,6,$C,$D,$13,$14,$17,$18 only.

Note table `$6D35`/`$6D96`: 97 words, entry k = round($010C·2^(k/12)) (entry 12 = $0218 exactly), entry 96 = $FFFF; index 0 = $010C (~15.7 Hz). Transpose values seen: 0, $0C, 5, $F4(−12), 7, $FD, $FB, 4, 2, $24 …

SFX table `$6DF7/$6E0D` (entries 1–21 ↔ subtunes 11–31): each points to `(voice*2, lo, hi)*` then ≥$80; e.g. SFX 5 starts tracks $6F05/$6F19/$6F21 on all three voices.

Rip loader tables (not player): `$2A0F` song index per subtune, `$2A1A/$2A25` two block indices per subtune, `$29E7` block src, `$29FB` dst, `$2A31/$2A30` pages/remainder.

#### 3.6.5 SID write schedule

Per frame, in this order: voice 0 → voice 1 → voice 2 → filter.
- Always: `$D415`, `$D416` (filter cutoff, even when unused).
- Voice v, conditionally: `D400/D401` (blip end, vibrato step, trill step, porta step, note-on, blip start — each writes immediately, no deferred flush; `$75/$78` shadow exists only so vibrato/blip can restore/step); `D402/D403` every frame while pulse mode ≠ hold; `D404` = wave&$FE at gate-off (repeated each frame while gateoff==0), = wave|1 (or blipwave|1) at note-on if gated, and raw `w` at every `$8D`; arbitrary `$D400+r` from `$85` lists (in practice AD/SR of any voice, $D417, $D418).
- Note-on order (gated, no blip): D400, D401, D404. With blip: D400/1 = blip freq, D404 = blipwave|1; n frames later D400/1 = note, D404 = wave|1.
- Gate handling: no hard-restart / ADSR-bug workaround; gate is only ever set from a note fetch and cleared by the two timers. `$D418` and ADSR are song data (`$85`), never computed. Init writes $08 (test bit) then $00 to every register $D400–$D41C.
- No reads of SID or VIC/CIA anywhere.

Frame log excerpt (subtune 0, frame 1): `04=00 05=DF 06=4F 0C=00 0D=0F 13=00 14=0F 14=0F 17=F2 18=1F` (an `$8D 00` then one `$85` list setting all ADSR + filter + volume from voice 0's track), `04=81 00=2E 01=FD 04=81` (`$8D 81`, note, gate), voices 1–2 similar, `15=FF 16=1F`; frames 2–40: voice 2 alternates `0E/0F` between two notes every frame (trill `$91` with tA=tB=1 — the trademark fast "arpeggio") and `15/16` step down by 1 (filter sweep, speed 1).

#### 3.6.6 Techniques specific to this player

| technique | citation | why |
|---|---|---|
| Full unrolling: one 493-byte voice template assembled three times with renamed ZP cells (`$21`→`$23`→`$25` for words, `+1` for bytes); handlers also ×3 | $6234/$6421/$6610; $6858/$6871/$688A … ; proven by mnemonic-stream diff (only difference: `CMP #1/#2` at $65C7/$67B6 vs `LDA $74; BNE` at $63D8) | no index register needed for voice → every access is `zp` (3 cycles) or `(zp),Y`; X/Y stay free for the sequencer; costs ~1 KB |
| Command dispatch by patching a `JMP` operand from lo/hi tables indexed by the command byte itself (X = $80..$94, tables placed at base−$80) | $6368 `LDA $6C37,X / STA $6375 / LDA $6C76,X / STA $6376 / JMP $xxxx` (SMC) | computed goto without `JMP (ind)` table alignment or the RTS trick; the two tables sit at $6C37 so that X≥$80 needs no subtraction |
| Immediate operands as state (`LDA #v; BNE`, `LDY #v; BEQ`, `CMP #v`) rewritten at run time | pulse mode $62EE, vib dir $6269, trill phase $629E, fixed length $640F, filter dir $6800 and bounds $6813/$6819/$682D/$6833 | a variable that is only tested in one place costs 0 extra bytes and 1 cycle less than `LDA zp`; the toggle `TYA; EOR #$FF; STA op` is as cheap as a ZP store |
| Constant-latched-at-command, applied-at-note SMC pair | `$8D`: `ASL;ASL; LDA #$FF/BCS/LDA #1 → STA $63D4 (note-time loader) and STA $62EE (now)` | pulse sweep auto-enabled iff waveform has bit 6; note-on restarts direction |
| Computed store via table of operand addresses | $61FC `LDA $622E,X; STA $6219; … STA $65FE(patched)` | the three copies of a cell are not equally spaced, so an indexed store is impossible; patch the store instead |
| Data cells inside code | $6BDA/DB blip freq, $6A05–0A defaults, $6B1F–$6B30 call stacks | keeps handler-local state adjacent to its only user |
| Flag as argument | `ASL A; ASL A; LDA #$FF; BCS +2; LDA #$01` ($6947) | select constant by a bit without AND/CMP |
| Branch-on-constant (`LDA #0 ; BNE never`, `LDY #0 ; BEQ always`) | $640E, $6268 | assemble-time "if" whose condition SMC flips at run time |
| INC/DEC floor trick | $634B `INC $3C` then $634D `DEC $3C` on the gate-off path | countdown saturates at 0 without a compare |
| Frame-count durations, no tempo | `$27` decremented once per call; lengths in track bytes | removes the speed/tick machinery entirely; tempo is baked into the data |
| 16-bit add/sub with page-crossing via `INX/DEX` on the hi register | $626C–$6279 vibrato: `CLC ADC $8A / BCC / INX` and `SEC SBC $8A / BCS / DEX` | freq hi held in X, lo in A → both go straight to `STA $D400/STX $D401` |
| Portamento in note-index space | $62BE `CMP $6C … ADC/SBC $63` on `$66`, then table lookup | slide by semitone steps, no 16-bit frequency delta math |
| Cutoff as 11-bit `hi:lo` and one shift chain to split it | $6841 `LSR ×3` of lo, `LSR;ROR;ROR;ROR` of hi, `ORA` | keeps sweep arithmetic 16-bit while SID wants 3+8 bits |
| Terminator by sign bit | `$85` list ends when the next "register" byte is ≥ $80; commands ≥ $80, notes < $80; SFX lists end on ≥ $80 | one `BPL/BMI` test doubles as end-of-list and as command/note discriminator |
| Init-time patching of the same cells that play patches | $61BF–$61D9 zero/one the SMC cells | reset means "put the immediates back" |
| Whole-block ZP clear by loop; SID clear with test-bit first | $6114 `STA $21,X` ×$70; $61A8 `LDY #$1C` | resets 119 state bytes with 4 instructions; $08 then $00 resets oscillator/envelope |
| Fixed-length mode | `$84 n` → notes carry no length byte | packs runs of equal notes (fast arpeggio passages) 2→1 byte |
| No JSR in the hot path | every handler `JMP`s back to `$6356/$6360`; voices chain by `JMP` | stack untouched; return points are static |
| Illegal opcodes | none | — |

#### 3.6.7 What it reduces to

The player is: **three identical, independent byte-stream interpreters** (one per voice), each with 27 bytes of ZP state plus 6 immediate-operand cells, plus one global 8-byte filter sweeper. Per voice, per frame: five optional 1-step modulators (blip end, vibrato, trill xor portamento, pulse bounce, gate-off) then `if --dur == 0: interpret commands until a note`. All state is decidable from the byte stream: every SMC cell is a plain variable set by a command (`$84 $8D $88 $8E $91`) or by init; the only "computed" jump is a 21-way switch on the command byte with a fixed table.

Per-voice state (27 ZP bytes): ptr(2) dur wave loopcnt loopptr(2) gated gatelen gateoff pw(2) pwreset(2) pwspd transpose bliplen blipwave vibdelay vibcnt vibdepth halfper halfcnt dir0 tA tB trilloff trillcnt portaspd note target callsp freqsh(2) active blipcnt release; + SMC: pulse mode, pulse mode0, vib dir, trill phase, fixedlen, blip freq(2). Global: cutoff(2) cutreset(2) spd owner dir min(2) max(2).

The player in 30 lines:
```
for v in 0..2:
  if !active[v]: continue
  if blip[v] and --blip[v]==0: setfreq(v, note[v]); if gated[v]: gate_on(v)
  if vib[v] and (vibcnt[v]==0 or --vibcnt[v]==0): freq[v] += dir[v]?+dp:-dp; write; if --half[v]==0 and hp: half=2hp, dir^=1
  if trill[v] and --trill[v]==0: phase^=1; note[v] += phase?+off:-off; trill=phase?tA:tB; setfreq
  elif porta[v] and note[v]!=target[v]: note[v] = towards(target, spd); setfreq
  if pwmode[v]!=hold: pw[v] += ±spd, bounce in [$0064,$0F9B]; write pw
  if --dur[v]==release[v] or gateoff[v]==0: gate_off(v) else --gateoff[v]
  if dur[v]: continue
  loop: b=*ptr++
    if b>=$80: handler[b](args at ptr) ; most set a variable, $81/$82/$87/$8A/$8B move ptr, $85 pokes SID, $86 stops; continue
    idx=b+transpose; porta? target=idx : note=idx; setfreq; restart vib/trill/gate timers; pw/cutoff reset; gate_on (blip first if set)
    dur = fixedlen or *ptr++
cutoff += ±spd, bounce in [min,max]; write D415/D416
return any(active)
```

What varies across the family: number of commands (17/18 → 20 → 21) and their numbering, ZP layout (Bionic Commando 1988 scatters cells and initialises them one `STX` at a time; GnG packs them at $21–$97 and clears with a loop), tune-specific constants. Invariant: three unrolled copies, patched-`JMP` dispatch, `$85`-style raw register lists, frame-count durations, no tempo, no hard restart, filter written every frame, `LDY #$1C` clear.

Dead/unreached in this tune: note byte $00 (rest path $6377), `$93` (one-shot no-transpose, and its `INC $6382` fix-up at $6385), `$94` (buggy default-PW setter), portamento's "overshoot" arm at $6320/$64BD/$66FC, the padding $6123–$6152 (48 zero bytes = `BRK`).

#### 3.6.8 Decompiler notes

- Code/data boundary: code is $6110–$6CB6 contiguous except the embedded cells listed in §1 (they are written by `STA abs` from handlers and read by `LDA abs`/`LDA abs,X` — treat any byte range that is both inside the code image and the target of a data write/read and never executed as a data cell). Tables follow at $6C37; song data lives outside the player image ($730E+).
- Dispatch: `LDA T1,X ; STA J+1 ; LDA T2,X ; STA J+2 ; JMP` with X ∈ [$80,$94] ⇒ a `switch(b)` with 21 cases; targets = `T1[$80..$94] | T2[..]<<8`. The three copies use three table windows (`$6C37`,`$6C4C`,`$6C61` +$80). Every case ends by jumping to one of two continuation labels of its own voice, so the switch is a loop body: `while(true){b=fetch(); switch(b){…continue}}` with `break` on note fetch.
- Per-voice struct recovery: the three copies differ only in ZP operands with deltas (+1,+2) for bytes and (+2,+4) for words; unify by diffing the mnemonic streams (they are byte-identical after operand renaming, except a 2-byte `CMP #v` insertion at one site). Model the voice as a struct and the three blocks as one procedure `voice(v)`; the handlers likewise.
- SMC classes present, and how to model each: (a) immediate operand cells ⇒ named variables (`pulse_mode`, `vib_dir`, `trill_phase`, `fixed_len`, `filt_dir`, `filt_min/max`, `skip_transpose`); the instructions become `if (var != 0)`, `cmp(x, var)`, `y = var`; (b) `JMP` operand ⇒ switch (above); (c) `STA` operand at $6218 chosen from `$622E/$6231` ⇒ `fixed_len[v] = 0` (indexed store through an address table); (d) init writes to the same cells ⇒ initialisers. No opcode patching anywhere; no relocation of the player (the rip's block copy is data movement into a region the player only reads/executes as data).
- Register roles inside `voice(v)`: Y is the sequencer cursor (offset from `ptr`), always reset to 0 at `$6360`; X is a temporary (note index into the two 97-entry tables, SID register offset in `$85`, call-stack depth in `$8A/$8B`, freq-hi in the vibrato adder). A is never live across the voice-to-voice `JMP`s. Carry is an argument only at `$6947`.
- Loop forms: `DEC zp; BNE` countdowns everywhere (frame timers), `INC/DEC` floor idiom, `BPL` sign-terminated list scans, `while(fetch)` sequencer loop with pointer arithmetic `ptr += Y` (`TYA; CLC; ADC $21; STA $21; BCC; INC $22`).
- Frequency: `freq = tab[idx]` with idx = note + transpose (+trill/porta in index space); vibrato and blip are the only places freq is manipulated as a 16-bit number ($75/$78 shadow). A decompiler can type $6D35/$6D96 as `u16 notetab[97]` split lo/hi and $6C37/$6C76 as `code* handlers[63]` split lo/hi.
- Timing: nothing is cycle-sensitive; order of SID writes within a frame is deterministic and given in §5; the model needs no raster/timer inputs. Play returns a value the host may use.
- Traps: (1) `$85` writes go anywhere in $D400–$D47F (X = data byte < $80) — a decompiler must model it as `SID[r] = v` with r data-dependent, not as fixed register writes; (2) note index up to 97+transpose can exceed the 97-entry table (reads spill into the hi table / SFX table: bug tolerated by the data); (3) `$8D` writes `D404` before the note in the same frame — the observable gate sequence depends on data order, not on a fixed per-frame register order; (4) the rip's stub at $7316 is overwritten by the copy it triggers (init is not re-entrant without re-loading; PSID players re-load the image per subtune); (5) the two `LDA $74; CMP #v` insertions break naive "three identical blocks" alignment by 2 bytes; (6) `$94` handler bug (both bytes to $6A05) — dead but present.

### 3.7 defMON (Frantic) — Goto80, Automatas (2013 export)


#### 3.7.0 Identity

| item | value |
|---|---|
| file | `MUSICIANS/G/Goto80/Automatas.sid`, PSID, load $0FD0–$2FAF (8192 bytes of image), init $0FD0, play $0FE3, 1 subtune, 5:23 |
| speed | CIA-timed: init programs CIA-1 timer A = $0998 (2456 cycles) → play is called 8.0× per PAL frame. The wrapper's counter (`INC $0FE4`, the operand of `LDA #` at $0FE3) selects **main tick** (`JSR $1003`, every 8th call, first call included) or **sub tick** (`JSR $1006`, the other 7). |
| player | $1000–$177E code (1919 bytes; 811 executed instruction sites, the only unexecuted code being the AF>0 detune path $1448–$1473 and the RE raw store $170B–$170F), $177F–$17FF `NOP` padding, frequency table $1554–$168B inside the code band |
| song data | $1800/$1900 sidTAB row pointers lo/hi (213 rows), $1A00/$1A80 pattern pointers lo/hi (96 slots, 37 used), $1B00/$1C00/$1D00 arranger V0/V1/V2 (168 rows + loop marker), $1E00 sidTAB DL (delay) per row, $1F00–$29C8 patterns, $2C8F–$2FAF sidTAB rows (last row ends exactly at the file end) |
| SMC | 89 patched instruction cells (83 whose bytes varied in 24000 calls): 4 opcode cells, the rest are immediates/operand addresses used as variables — the *entire* per-voice state and SID register image live inside instructions |
| illegal opcodes | 9 sites executed: `SAX abs` ×3, `SBX #` ×2, `LAX zp`, `LAX (zp),Y`, `ANC #`, `ALR #` (see §6) |
| volatile reads | init only: busy-wait on `$D012` = $FC then one read of `$D41B` (SID model detection). Play reads no hardware. |
| provenance | the exported player is the editor's IRQ player band ($1000–$17FF of the defMON image, the undefmon project's byte-exact reassembly `defmon.asm`) minus the editor-only bits: no arranger repeat counter (editor $10F1–$1115 → export's plain jump $10F1–$10F8), no `STX→JMP` voice-mute patching of the SID band, no SID#2 mirror; every remaining routine matches the source instruction-for-instruction at addresses shifted by −$23..−$24 after $10F8. Bytes $0FF6–$0FFB `69 43 32 30 30 39` = "iC2009" (exporter stamp, never executed). |

#### 3.7.1 Memory map and state

##### Code

| range | routine (undefmon label) | role |
|---|---|---|
| $0FD0–$0FE2 | wrapper init | counter=0; `JSR $1000`; CIA1 TA ← $0998 |
| $0FE3–$0FF5 | wrapper play | `LDA #cnt; AND #7; BNE sub; JSR $1003; JMP +; sub: JSR $1006; INC cnt; RTS` |
| $1000/$1003 | jump table | `JMP $14FE` init(A=start row) / `JMP $1022` main tick |
| $1006–$1018 | player_sound_update (sub tick) | `LDA $10D8; PHA; LDA #$60; STA $10D8; JSR $1022; PLA; STA $10D8; JMP $12BE` |
| $1019–$1021, $104A–$1052, $107B–$1083 | VoiceRecord v0/v1/v2 (9 data bytes each, stride $31) | slide acc lo/hi, AF, –, –, PS, detune (0/1/2), voice bit (1/2/4), complement ($FE/$FD/$FB) |
| $1022–$1049, $1053–$107A, $1084–$10A8 | SID write band v0/v1/v2 (49 bytes each) | `LDX #pwlo; LDA #pwhi; STX $D402; STA $D403; LDX #flo; LDA #fhi; STX $D400; STA $D401; LDX #SR; LDY #AD; LDA #WG; EOR #WGx; STX $D406; STY $D405; STA $D404; JMP next` |
| $10A9–$10B4 | globals | `LDA #res/route; STA $D417; LDA #mode; ORA #$0F; STA $D418` |
| $10B5–$10D7 | filter cutoff slide + clamp + `NOP/ASL` + `STA $D416` | see §3 |
| $10D8–$10DB | sub-frame sentinel | `LDA #flag` (opcode patched to `RTS` on sub ticks); `BPL $1126` |
| $10DC–$1125 | song advance | flag&$0F → all row timers; arranger row → pattern pointers |
| $1126–$11AD, $11AE–$1235, $1236–$12BD | row advance v0/v1/v2 (136 bytes each, unrolled) | row timer, prepare, consume (§3) |
| $12BE–$12EE, $12EF–$131F, $1320–$1350 | sidTAB cascade 1 (sidcall A) v0/v1/v2 (49 bytes each) | DL countdown → fetch next row → `JSR $168C` |
| $1351–$1381, $1382–$13B2, $13B3–$13E3 | sidTAB cascade 2 (sidcall B) v0/v1/v2 (49 bytes each) | same |
| $13E4–$14CA | pitch / pulse oscillator, X = $62,$31,$00 | freq = table[note] (+slide acc or detune) → freq immediates; PS pulse bounce |
| $14CB–$14FD | SID model detect | raster wait, osc3 read → patch $10CE (`CMP #`) and $10D4 (`NOP`/`ASL`) |
| $14FE–$1553 | init | clear SID, filter cells, per-voice cells; cascade counters ← $FF; flag ← $80 |
| $1554–$15EF / $15F0–$168B | freq lo / freq hi | 156 × u8 each: 12-TET from ~1 Hz (index 12) to $FFFF (index 155); indices 0–11 = 0,0,1,1,2,2,4,4,8,8,12,12 |
| $168C–$177E | sidtab_row_apply(A=row lo, $FC=row hi, X=voice·$31) | column decoder (§4) |

##### State — every per-voice variable is a byte inside a 49-byte-strided code block, addressed `abs,X` with X ∈ {$00,$31,$62}

| v0 addr | inside | meaning | writers → readers |
|---|---|---|---|
| $1019/$101A | data | slide accumulator lo/hi (16-bit, signed) | oscillator, note-on (0), init → oscillator |
| $101B | data | AF: 0 none · $80–$BF slide up · $C0–$FF slide down, bits 0-5 = speed index · $01–$7F fixed detune (dead) | sidTAB AF, note-on (0) → oscillator |
| $101E | data | PS: signed pulse step per call (bit 7 = add) | sidTAB PS, oscillator (sign flip) → oscillator |
| $101F | data | detune 0/1/2 (constant) | — → oscillator |
| $1020/$1021 | data | voice bit / complement (constants) | — → RE column |
| $1023 / $1025 | `LDX #`/`LDA #` operands | PW lo / PW hi (SID image) | sidTAB PW, PS sweep → SID |
| $102D / $102F | `LDX #`/`LDA #` | freq lo / hi (SID image) | oscillator → SID |
| $1037 / $1039 | `LDX #`/`LDY #` | SR / AD (SID image) | sidTAB SR/AD, init → SID |
| $103B / $103D | `LDA #`/`EOR #` | WG control byte / WGx EOR mask; SID gets WG^WGx | sidTAB, init → SID |
| $1129 | `LDY #` at $1128 | row timer (main ticks; −1 = consume next row) | consume, song advance, DEC |
| $114A/$114B, $1165/$1166, $1173/$1174, $1181/$1182 | `LDA abs`/`LDA abs,Y` operands | pattern pointer (the $1165 copy is the master; the other three are broadcast copies) | song advance, consume (+= row length) |
| $1161/$116F/$117D/$1194 | `LDA #`×3, `LDX #` | current row flag byte pre-shifted <<1, <<2, <<3 and raw | prepare → consume |
| $12BF / $1352 | `LDA #` | cascade A / B counter (DL countdown; $FF = idle) | consume (0), cascade, init ($FF) |
| $12CE / $1361 | `LDY #` | cascade A / B row index (next row to apply) | consume, cascade |
| $12CC | data | pattern note (base pitch) | consume → TR column |
| $135E | data | current note index (base + TR) | consume, TR → oscillator |

Global (all immediates unless noted): $10AA res/route ($D417 image), $10AF mode ($D418 hi nibble), $10B6/$10BE cutoff acc lo/hi, $10B9/$10C0 slide step lo/hi, $10B8/$10BF opcode `ADC`/`SBC` (direction), $10CA CP offset, $10CE clamp/reload constant (2 or 0), $10D4 opcode `NOP`/`ASL`, $10D8 opcode `LDA #`/`RTS`, $10D9 flag (bit 7 = song advance pending, low nibble = gap), $10EB arranger row, $0FE4 call counter. Zero page: only `$FB/$FC` (row pointer for `(zp),Y`) and `$96` (flag scratch), all inside `$168C`.

#### 3.7.2 Entry points and conventions

- **init** (`$0FD0` → `$1000` → `$14FE`, A = subtune): `STA $10EB` — the subtune number *is the arranger start row*. Zero $D400–$D417 (not $D418); zero the six filter cells and $10AA/$10AF; SID-model detect (§6); for X = $62,$31,$00: cascade indices, AD/SR/WG/WGx immediates, AF, PS, note ← 0, cascade counters ← $FF; `$10D9 ← $80` (song advance on first main tick). Then the wrapper zeroes the call counter and sets the CIA period. No relocation, no pointer fix-ups: the export is assembled for $1000.
- **play** (`$0FE3`): no arguments; A/X/Y clobbered. Main tick = `$1022` straight through (write-out → filter → row advance → cascades → oscillator → `RTS` at $14CA). Sub tick = write-out → filter, then the sentinel `RTS` at $10D8 returns to `$1012`, which restores the `LDA #` opcode and `JMP $12BE` (cascades → oscillator → RTS). So per call: **always** write-out + filter + both cascades + oscillator; **main only**: row advance. Sub-frame effects therefore run at 8× frame rate; the sequencer at 1×.
- Register conventions: `X = voice·$31` in the oscillator loop and in `$168C` (also the offset for cascade/write-band cells); in the row-advance blocks X = 0 (used as a zero source: `STX counter`, `STX slide cells`) and briefly the flag byte (`LDX #flag` → `SAX`); Y = row byte cursor (`(FB),Y` in $168C, `abs,Y` in consume) or table index (`$1800,Y` cascade, arranger `,Y`). A carries the row lo byte into `$168C`; `$FC` the hi byte.
- Flags: `ASL` of the flag byte puts bit 7 into C and bit 6 into N so one shift feeds `BPL`/`BCC`/`BIT`; `SBX #$31` sets N for the loop test; `ANC #$7F` yields C=0 for the following `ADC`.
- No JSR except `$1022` (from $1003/$100F), `$168C` (6 cascade sites), `$14CB` (init); every other transfer is a `JMP`/branch chain, and blocks fall through voice to voice.

#### 3.7.3 The play routine

```
wrapper():                                       ; $0FE3, 8× per frame
  if (cnt++ & 7) == 0: main()  else: sub()

main():   writeout(); filter(); rowadvance(); cascades(); oscillator()
sub():    writeout(); filter();                  cascades(); oscillator()   ; via the RTS patch at $10D8

writeout():                                      ; $1022–$10B4, 25 stores, values = immediates
  for v: D402/3 = PW ; D400/1 = FREQ ; D406 = SR ; D405 = AD ; D404 = WG ^ WGx
  D417 = RE ; D418 = FV | $0F
filter():                                        ; $10B5–$10D7
  acc = acc ±16 step                              ; opcode-encoded direction; step 0 = hold
  if acc.hi < 0: acc.hi = thr                     ; thr = 2 (6581) / 0 (8580)
  c = acc.hi + CP ; if c < 0 or c < thr: c = thr  ; CP = per-row cutoff offset
  D416 = c  (or c<<1 on 8580)                     ; D415 never written (always 0 from init)
```
```
rowadvance():                                    ; $10D8: LDA #flag ; BPL voices
  if flag < 0:                                   ; a voice hit an END row last tick
      gap = flag & $0F ; flag = gap ; timer[0..2] = gap
      y = arrow ; p = V0[y] ; if p >= $80: y = V1[y] ; p = V0[y]     ; $FF marker: jump to row V1[y]
      pattern ptr[v] = patptr[Vv[y]] for v in 0..2 ; arrow = y+1     ; patched into 4 LDA operands per voice
  for v in 0,1,2 (unrolled):                     ; $1126 / $11AE / $1236
      if timer[v] < 0:  CONSUME(v)
      elif --timer[v] < 0: PREPARE(v)
PREPARE(v):  broadcast ptr into the three LDA operands; f = pat[0]; flag_raw=f; f1=f<<1; f2=f<<2; f3=f<<3
CONSUME(v):  y = 1
      if f1 < 0 (bit6): cascA.idx = pat[y++] ; cascA.cnt = 0          ; sidcall A starts next cascade pass
      if f2 < 0 (bit5): cascB.idx = pat[y++] ; cascB.cnt = 0          ; sidcall B
      if f3 < 0 (bit4): note = base = pat[y++] ; slide acc = 0 ; AF = 0
      if flag_raw < 0 (bit7): flag = flag_raw ; return                ; END: pointer not advanced; song advance next tick
      ptr += y ; timer[v] = flag_raw & $0F                             ; SAX
```
Row length = `d+2` main ticks (consume tick, `d` decrements, the tick that reaches −1 prepares, next tick consumes) — verified: pattern 6's `d = 1,14,0,14,0,10,…` gave consume-to-consume gaps of 3,16,2,16,2,12 ticks; END rows also cost `d+2` (consume, next tick song-advance sets all timers to `d`, …). Summing `d+2` over the 168 arranger rows gives 16128 ticks = 322.6 s = the HVSC length 5:23. All three voices' patterns in a song row have equal total length; an END in *any* voice resynchronises all three (all timers ← gap).

```
cascades():                                      ; six 49-byte blocks: A0 A1 A2 B0 B1 B2
  for each block: if cnt == 0: APPLY ; elif cnt < 0: skip ; else cnt--
APPLY:  y = idx ; if hi[y] == 0: y = lo[y]        ; JP row: redirect (and continue from there)
        $FC = hi[y] ; cnt = DL[y] ; A = lo[y] ; idx = y+1 ; X = voice·$31 ; JSR $168C
```
A row is applied for `DL+1` calls (DL = 0 → every call = 8× per frame; DL ≥ $80 → hold this row forever, i.e. program end).

```
oscillator():                                    ; $13E4, X = $62, $31, $00 (SBX #$31)
  af = AF[v]
  if af == 0:      freq = tab[36 + note] + detune (lo only, no carry)
  elif af < 0:     acc ±= tab[af & $3F] (16-bit; bit 6 = down) ; freq = tab[36+note] + acc
  else (dead):     freq = tab[36+note] + (tab[note+af+12] − tab[note+af+11])   ; fixed detune
  ps = PS[v]
  if ps > 0:  pw.lo -= ps ; on borrow: if pw.hi == 0: pw.lo = 1, ps = −ps  else pw.hi--
  if ps < 0:  pw.lo += ps&$7F (ANC clears C) ; on carry: if pw.hi == $0F: pw.lo = $F8, ps = −ps  else pw.hi++
```
(Sweeps and slides therefore advance 8× per frame; a note-on zeroes acc/AF, so a slide must be restarted by the sidTAB.)

`$168C` row apply — see §4; every column write is a store into one of the immediates above.

#### 3.7.4 Data formats

**Arranger** ($1B00/$1C00/$1D00, one byte per voice per song row, indexed by the arranger row): pattern number (< $80); V0 = $FF marks a jump, the target row is V1's byte (V2's byte — the editor's repeat count — is ignored by the export). Automatas: 168 rows, marker at 168 → row 0. Init's A selects the start row (subtune = start position).

**Pattern** (pointer table $1A00/$1A80, 96 slots): a byte stream of rows:
```
flag  b7 END (song advance after this row; the row is not consumed past)   b6 sidcall A follows   b5 sidcall B follows   b4 note follows   b0-3 duration d  (row lasts d+2 ticks)
[A]   sidTAB row index for cascade A          [B]  row index for cascade B          [note]  0..$7F, freq index = note+36 (+TR)
```
Automatas: 37 patterns, 477 rows; d ∈ {1:157, 4:142, 15:66, 10:33, 0:23, 14:21, 7:20, …}; hi-nibble classes: 0 (bare rest/hold) 115, 4 (note only) 133, 2/6 (B only / A+B) 74, 1/5/7 (note+…) 91, END rows 37; notes 12..100.

**sidTAB row** (213 rows; each = pointer table entry lo/hi at $1800/$1900 → variable-length record, plus DL at $1E00; hi = 0 marks a JP whose lo = target row):
```
byte 0  flags1: b6 WG (control byte)  b7 WGx (EOR mask)  b5 AD  b4 SR  b3 TR  b2 AF  b1 PW      — bytes follow IN THIS ORDER (b6 before b7)
byte n  flags2: b7 PS  b6 RE  b5 FV  b4 CP  b3 ACID(2 bytes: lo, hi)                              — b0-2 unused
```
Column semantics (writer in `$168C`):
| col | store | meaning |
|---|---|---|
| WG | $103B,X | SID control byte proper (waveform, sync/ring/test, gate) |
| WGx | $103D,X | EOR mask applied at write-out: `D404 = WG ^ WGx` — gate/test toggling without knowing the waveform (`EOR=01` gate flip, `09` test+gate) |
| AD / SR | $1039,X / $1037,X | envelope bytes |
| TR | $135E,X = byte + base note ($12CC,X) | note relative to the pattern note (arpeggio, drum pitch); freq index may exceed the table (e.g. TR=$40) — reads run into the hi table |
| AF | $101B,X | slide: $80\|dir\|speed (bit 6 = down, speed = table index 0..63 → units per call), 0 = off |
| PW | $1025,X = byte ; $1023,X = byte & $F0 | 12-bit PW from one byte: PW = (b&$0F)<<8 \| (b&$F0) |
| PS | $101E,X | signed pulse step per call, bouncing between $0001 and $0FF8 |
| RE | $10AA | 0 → clear this voice's routing bit; bit 3 set → `$10AA = ($10AA & $0F) \| byte \| voicebit` (resonance nibble + route this voice); other → raw (dead) |
| FV | $10AF | $D418 high nibble (filter mode); volume is a constant $0F |
| CP | $10CA | cutoff offset added to the accumulator's high byte before clamping |
| ACID | lo,hi | hi < $80: cutoff acc = hi:lo, step = 0 (absolute set); hi ≥ $80: step = (hi&$3F):lo, direction = hi bit 6 (1 = down; patches `ADC`↔`SBC` opcodes at $10B8/$10BF) |
| DL | $1E00,Y | calls to hold this row minus one; ≥ $80 = stop |
| JP | hi==0 | jump to row lo (loop point) |
Column census over the 213 rows: WGx 61, WG 51, AF 53, TR 45, SR 40, AD 39, CP 35, RE 24, ACID 19, PW 13, PS 11, FV 7, empty 17, JP 21. Every row's decoded length equals the distance to the next pointer (rows are stored consecutively), and the last row ends at $2FAF = end of image.

Typical programs (row: DL cols): hard restart = `2A: 0F WGx=00 AD=0F SR=00` (16 calls = 2 frames gate off, ADSR reset) → `2B: 07 WGx=09` (8 calls test+gate) → `2C: 00 WG=50 WGx=05 AD=01 SR=51 TR=20` (sound); arpeggio = `72: 00 TR=00 AF=00 · 73: 00 TR=05 · 74: 00 TR=07 · 75: JP→72` (one row per call: 3-note chord cycling at 133 Hz); filter offset ramp = `11..19: 03 CP=FC,F8,…,E0`; drum = `27: 05 WG=80 WGx=01 AD=05 SR=00 TR=40 AF=F4 RE=00` (noise, +64 semitones, fast slide down); PW sweep started by `PS=1F`.

**Instrument** = nothing: a defMON "instrument" is whichever sidTAB row a step's sidcall points at plus the rows it chains to (DL/JP). Two sidcalls per step give two concurrent programs per voice (e.g. tone + filter).

**Frequency table**: 156 entries; the same table supplies pitch (`+36+note`), slide speeds (`entry[af&$3F]`, so speeds are exponentially spaced) and the dead detune path.

#### 3.7.5 SID write schedule

Every call (8× per frame), first thing, in this order: `D402 D403 D400 D401 D406 D405 D404` for voice 1, same for voice 2, voice 3, then `D417 D418`, then (after the filter arithmetic) `D416`. 25 stores from immediates; `D415` is only written by init (0). What the chip receives at call k is the image as left by call k−1 (one-call latency = 1/8 frame). Gate on/off, test bit, ADSR resets, hard restart — all are sidTAB rows writing WG/WGx/AD/SR at DL granularity; the player has no notion of note-on/off (a pattern note only sets the pitch, resets the slide and starts up to two row programs). No reads of any SID/VIC/CIA register in play; init reads `$D012` (raster wait) and `$D41B` (model detection).

Verified in the log: init call 0 flushes zeros; the first note frame (call 33 = frame 4.1: pattern 6's first row `d=1` = 3 ticks of silence, then `sc1=10 n=12`); pulse `$09/$0A` on voice 2 stepping by $1F every call and bouncing; cutoff `$16` stepping every call.

#### 3.7.6 Techniques specific to this player

| technique | citation | why |
|---|---|---|
| **The SID image is code**: 7 register values per voice + 2 globals live as `LDX #/LDY #/LDA #/EOR #` operands in a 25-store block | $1022–$10B4 | the write-out is straight-line immediate loads and absolute stores — 2+4 cycles per register, no indexing, no shadow copy loop |
| **All per-voice code blocks are exactly 49 bytes** so one X (voice·$31) indexes cells inside the write band, both cascade blocks and the voice records | `$1023,X`, `$12CC,X`, `$135E,X`, `$12BF+$31v` … | struct-of-code: the "record" is the instruction stream; stride = block size |
| **Opcode as sub-frame gate**: `$10D8` `LDA #flag` ↔ `RTS`, patched by `$1006` around a `JSR $1022` and restored | $1006–$1015 | one entry point serves both cadences; the sub path skips exactly the sequencer with 2 stores |
| **Opcode as sign**: `ADC #` ↔ `SBC #` at $10B8/$10BF for cutoff slide direction | patched at $1767/$1778, $176A/$177B | direction costs no test |
| **Opcode as configuration**: `NOP` ↔ `ASL` at $10D4 chosen once from SID model | $14F9 | 8580/6581 cutoff scaling for free at run time |
| **SID model detection**: raster wait, `$FF→$D412/$D40E/$D40F`, `$20→$D412`, read `$D41B`, bit 0 selects | $14CB–$14FD | the standard osc3 model test; makes the filter range hardware-dependent |
| **Pointer broadcast instead of ZP indirection**: pattern pointer patched into 4 `LDA abs[,Y]` operands at prepare time | $1131–$1148 (×3) | row reads become 4-cycle `abs,Y`; the pointer add is done with `ADC/INC` on the master copy |
| **Pre-shifted flag copies** (`f<<1`, `f<<2`, `f<<3`) stored into `LDA #`/`LDX #` operands, tested with `BPL` | $114F–$1158, $1160/$116E/$117C | each field test is 2+2 cycles, no `AND` |
| **`SAX` to mask-and-store** (`LDA #$0F; SAX timer` = timer ← flag & $0F) | $11A9–$11AB, $1231, $12B9 | 1 instruction instead of `TXA; AND; STA` (A must stay… it doesn't matter, X holds the flag) |
| **`SBX #$31`** as loop counter step (`TXA; SBX #$31; BMI`) | $14C2, $154A | X −= $31 with N set, no `SEC/SBC/TAX` |
| **`ANC #$7F`** = `AND #$7F` + `CLC` (bit 7 of the result is 0) before `ADC` | $147C | saves the `CLC` |
| **`ALR #$7F`** = `AND #$7F; LSR` | $1771 | halves the masked ACID hi byte in one instruction |
| **`LAX zp` / `LAX (zp),Y`** to hold the flag word in both A and X (`TXA` re-tests are 1 byte) and to fetch a 16-bit value in two registers | $1726, $1745 | register pressure in the column decoder |
| **`ASL` of a flag byte to split bits 7/6 into C/N**, `BIT` for bit 5 (V) | $1694–$16A9, $16EF | one shift, three flags |
| **`BEQ`/`BMI`/`DEC` three-way** on a countdown immediate | cascade heads $12BE, $12EF … | 0 = fire, <0 = idle, >0 = wait, in 8 bytes |
| **DL/JP bytecode tables in parallel arrays** (ptr lo/hi + delay), JP encoded as hi = 0 | $1800/$1900/$1E00 | table = program with loops |
| **Row records with flag-bit presence** (variable length, order fixed by test order) | `$168C` | dense storage of sparse register updates |
| **PW packed into one byte** (`b&$0F`<<8 \| `b&$F0`) | $16E2–$16E7 | 8-bit resolution of the 12-bit register in one column |
| **EOR mask column** on the control byte | $103C, `$103D,X` | gate/test edits are waveform-independent |
| **Bounce arithmetic on the SID image itself** (PS sweep patches the PW immediates; slide acc added to freq immediates) | $147B–$14BF, $1419–$142C | no separate working copy |
| **Constant per-voice detune (0/1/2 units)** added to freq lo | $1439 | de-phases unison voices; lo-only add drops carry |
| **Voice arbitration of the filter by bit masks in the record** (`AND $1021,X` / `ORA $1020,X`) | $1713, $1720 | routing without shifts |
| **Subtune = start row** (`STA $10EB`) | $14FE | zero-cost subtune support |
| **`INC` on an operand** as the call counter | $0FF2 | — |

#### 3.7.7 What it reduces to

The player is **two rates and three tables**. Per voice the state is 9 record bytes + 8 SID-image bytes + 2 cascade cursors + 2 counters + timer + note/base (≈ 24 bytes); globally 10 filter/status bytes. Every call: copy image to SID; step the filter accumulator; step six DL counters and, when one expires, decode one variable-length row into the image (`$168C`); recompute pitch (table + slide acc) and bounce the pulse. Every 8th call: decrement three row timers and, when one expires, take the next 1–4-byte pattern row (start ≤2 row programs, set the note). Every 8th call when an END was hit: advance the arranger, reload three pattern pointers.

There are no envelopes, vibrato, portamento, arpeggio or hard-restart *mechanisms*: the sidTAB rows are the instrument, played at up to 400 Hz, and every classic effect is a row loop (`TR` rows = arpeggio, `AF` rows = vibrato/slide, `CP` rows = filter LFO, `WGx`/`AD`/`SR` rows = gate and hard restart). SMC is storage: 4 opcode cells (`RTS` gate, `ADC/SBC` sign, `NOP/ASL` scale, plus the same `ADC/SBC` pair) and ~85 operand cells that are ordinary variables in a struct-of-code layout. Volatile inputs: `$D41B` bit 0 once at init (SID model), `$D012` as a wait — after init the output is a pure function of (start row, call index) *per SID model*. Illegal opcodes: 6 kinds, 9 sites, all for register economy.

Dead in this tune: AF positive (fixed detune) path $1448–$1473 and the RE "raw" store $170B — nothing else; every other instruction of the export executes. Between the editor and the export: arranger repeat counts and per-voice mute (`STX→JMP`) are stripped, SID#2 mirror absent; between tunes only the data and the CIA period differ (Automatas 8×; other exports use 1×/2×/4× — the DL/PS/AF units scale with it).

The player in ~30 lines:
```
call():                       ; every 1/8 frame
  SID[0..24] = image          ; immediates
  acc ±= step ; D416 = clamp(acc.hi + CP)
  if main tick:
     if flag<0: gap=flag&15; timers=gap; row=arranger[arrow]; (jump if $FF); ptr[v]=pat[row[v]]; arrow++
     for v: if timer[v]<0: consume row (A/B row idx→cascade cnt=0, note→base/note, END→flag, ptr+=len, timer=d)
            elif --timer[v]<0: prepare (broadcast ptr, preload flag)
  for each of 6 cascades: if cnt==0: apply row (JP redirect, cnt=DL, idx++) elif cnt>0: cnt--
  for v: freq = tab[36+note] + (AF<0 ? (acc ±= tab[AF&63]) : detune) ; if PS: pw ±= PS with bounce
```

#### 3.7.8 Decompiler notes

- **Code/data boundary**: everything $1000–$177E executes; the frequency table $1554–$168B sits inside the code band and is reached only by `,Y` loads; the nine-byte voice records at $1019/$104A/$107B and the note cells $12CC/$135E (+$31) are data inside code. Rule: a byte is data iff some executed instruction reads/writes it through a data mode — here that set is exactly the 89 operand cells + those records.
- **The register file is the instruction stream.** Model each `LDX #/LDA #/LDY #/EOR #` in the write band as a load from a named per-voice variable (`pw_lo[v]` …) and each `STA cell,X` as a store to it. Because X = v·$31 addresses cells inside *code*, the per-voice struct is recovered by collecting all `abs,X` operands executed with X ∈ {0,$31,$62} and reading field = operand − block base; the SID band's 8 immediates, the two cascade blocks' 4 cells and the record's 9 bytes fall out as three sub-structs at strides $31.
- **Opcode cells** ($10D8, $10B8, $10BF, $10D4): 1-bit variables. $10D8 is `if (subtick) return;` — lift the sub path as a second procedure that shares the write-out block (the parent's `JSR $1022` + patched `RTS` is a call to `writeout_and_filter()`). $10B8/$10BF: `acc += dir ? −step : +step`. $10D4: `scale ∈ {1,2}` fixed at init from a hardware input.
- **Pointer-in-operand**: `$1165/$1166` is a 16-bit pointer variable; the three broadcast copies are just cached reads (treat `LDA $xxxx,Y` at $1164/$1172/$1180 and `LDA $xxxx` at $1149 as `ptr[Y]`).
- **Cadence**: the top-level phase variable is the wrapper's `cnt & 7`; inside, `flag` (bit 7), the three `timer`s (sign/zero) and six cascade counters (0 / <0 / >0) are the only control state. Recover those first; everything else is straight-line.
- **Row grammars** are the `ASL/BPL/BCC/BIT/AND` test order in `$168C` (flags1) and the four `BPL` on pre-shifted copies in consume (pattern flag) — lift them as tokenizers with the field order given in §4 (note WG before WGx although WGx is bit 7).
- **Timing model**: a row lasts d+2 main ticks (8·(d+2) calls); a sidTAB row DL+1 calls; the SID sees the image one call late; the ACID/PS/AF units are per call. A verifier must run at call granularity (8/frame), not frame granularity.
- **Volatile**: `$D41B` at init decides `$10CE`/`$10D4` — a decompiler must either pin the SID model or carry it as a parameter; `$D012` wait is a no-op semantically.
- **Illegal opcodes** must decode: `SAX abs` (M ← A&X, no flags), `SBX #` (X ← (A&X)−imm, C/N/Z), `LAX` (A,X ← M), `ANC #` (A ← A&imm, C ← bit7), `ALR #` (A ← (A&imm)>>1, C ← old bit 0). A stock 6502 disassembler stops dead at $8F/$CB/$A7/$B3/$2B/$4B.
- **Traps**: the detune add drops its carry ($1439–$1442); TR can index past the 156-entry table (reads spill into the hi table); the row-advance blocks use `LDX #0` as a zero source, so `STX` there means "store 0" (not X = voice); END rows are consumed twice in a sense (consume sets flag, next tick song advance) — the pattern pointer is not advanced past END; `d` of an END row is the inter-pattern gap; the three `RE` semantics depend on bit 3 of the byte; sc rows are shared between voices and both cascades (a row is a program, not an instrument); rows D5–D7 in the pointer table point outside the image (unused).

### 3.8 Martin Walker — Chameleon (1990), the typed-keyboard player at 2× speed


#### 3.8.0 Identity

| item | value |
|---|---|
| file | `MUSICIANS/W/Walker_Martin/Chameleon.sid`, PSID, load $A000–$B81C, init $AC00, play $AA65, 1 subtune, HVSC length 1:20 |
| speed | PSID CIA flag; init programs CIA-1 TA = $2663 = 9827 cycles → **2.000 play calls per PAL frame**. Sequencer tick every `$02FF` = 9 calls (4.5 frames); modulators/filter/write-out run every call |
| player | $A000–$AA86 (2695 bytes, 1059 executed instruction sites of 1240 reachable), plus the ripper's init stub $AC00–$AC1A ("Fix by iAN CooG 20081108" text follows at $AC1B). $AA45–$AA64 = the game's KERNAL-IRQ installer/handler (unused). Init at $A518 is the original entry (song number is *not* an argument — the stub hard-codes song 1) |
| data | instrument bank $AA87–$ACFF (30-byte records, interleaved with the stub), engine state $AD00–$AD76, drum records $AD78–$AE1F (24 × 7), song/block/drum/instrument pointer tables $AE64–$AF0D, freq lo/hi $AF0E/$AF6E (96), shifted-key table $AFCE (25), note-key table $AFE7 (25), blocks $B000–$B77B (20), songs $B77C–$B7A4 (5), tune instruments $B7A5–$B81C |
| SMC | none. Illegal opcodes: none |
| volatile | `LDA $D41B` at four sites ($A640, $A6C6, $A74C, $A7ED) — one per modulator (pitch-1, pulse, pitch-2, filter): "period = $FF ⇒ random offset". In this tune only $A74C fires, 8 times, all in the first 8 calls, on voice 3, driven by **residual engine state left in the image** ($AD36 = $FF before the first note; init never clears $AD01–$AD76). No `$D011/$D012` anywhere in the reachable code; a byte scan of all 20 Walker HVSC tunes finds no raster read in any of them (Dragon Breed's RSID wrapper *writes* $D011/$D012 to set up its IRQ). `$D41B` sites exist in 6 of the 20 (Chameleon 4, Atomic Robo-Kid 5, Rodland/SWIV/Ninja Spirit/Indiana Jones 1 each) |
| family | Chameleon is its own generation: 1059 executed sites, ASCII "keyboard" tracks; Walker's other tunes (Armalyte/Citadel 1988, Aggressor, Dominion, Altered Beast, Speedball 2 …) run a ~400–450-instruction player with a different layout (not analysed here) |
| provenance | none; pure binary RE, verified by execution counts, a per-call SID log and probes (song parser reproduces the 1:20 length: the song *stops* at call 7775 = 77.8 s and then idles) |

#### 3.8.1 Memory map and state

##### Code

| range | routine | role |
|---|---|---|
| $A000–$A02D | `classify(A=char)` | linear search of the 25 note keys ($AFE7) then the 25 shifted keys ($AFCE); `$B0` is aliased to `$30`; → `$02B5` = class (0 note / 1 drum), `$02B6` = index |
| $A02E–$A072 | `notefreq` | freq = table[idx + transpose[v]] → `$02BA/BB`; voice 2: −detune[1], voice 3: +detune[2] |
| $A073–$A08C | `writefreq` | `$D400/1,Y` ← `$02BA/BB`, and base freq shadow `$AD6C/$AD6F,X` |
| $A08D–$A09F | `voffs` | X (0..2) → Y = 0/7/14 by a `CPX` chain (no table) |
| $A0A0–$A0E6 | `gate` | write ctrl: gate off first (unless legato mode), then on/off per `$02BC`; sets new-note flag |
| $A0E7–$A0F6 | all-voices gate | (unused) |
| $A0F7–$A108 | `nextvoice` | `$02B1` = 1..3 wraps via `$02C7/$02C8` |
| $A109–$A22F | `loadins` | 30-byte instrument → SID AD/SR/ctrl(gate off)/PW + 17 engine parameters (per-register `CPX` chains instead of `,Y`) |
| $A230–$A28C | `loadfilt` | instrument of voice `$02D9`: $D418, $D416, $D417 (+routing bits from block header), filter modulator params |
| $A28D–$A30D | `drum` | preset engine cells, then 7-byte drum record → freq/ctrl/AD/PW |
| $A30E–$A323 | voice-counter init | 3 / 4 / 1 |
| $A336–$A378 | `blockptr` / `blockstep` | `$FB/FC` = block[$02AA] (+15), `$02` = track length; `$FB += $02` |
| $A379–$A3BC | `blockhdr` | 16-byte header → per-voice instrument/gate/filter, filter owner; reset modulators |
| $A3BD–$A43B | `rest` / `note` / `drumnote` | the three token handlers |
| $A445–$A484 | song setup | |
| $A485–$A517 | `step` | one sequencer tick: block header on position 1, then 3 voices × classify/dispatch, position/song advance |
| $A518–$A53C | `init` | SID := 0, `$02A7–$02FF` := 0, song setup, sfx slots |
| $A53D–$A573 | `seq` | music state machine (0 restart, 1 play, 2 sfx-only) |
| $A574–$A593 | offset clears | |
| $A594–$A5C1 | `engine` | 3 × `voicemod`, filter, `$D416`, clear new-note flags |
| $A5C2–$A60B | `voicemod` | delay gate, mod1/mod2/mod3, mod4, freq/PW = base + offset → SID |
| $A60C–$A691, $A692–$A717, $A718–$A7B0 | mod1 (pitch), mod2 (pulse), mod3 (pitch, one-shot/triangle) | three copies of one modulator template |
| $A7B1–$A83E | filter modulator | fourth copy, global |
| $A83F–$A88A | mod4 | gate toggle |
| $A88B–$A940 | modulator resets | mod1/mod2/mod4/mod3 (with pre-load loops) |
| $A941–$A957, $A958–$A9B0 | all-mod reset, filter reset | |
| $A9B1–$AA2F | sfx slots | game sound-effect API (unused by the tune) |
| $AA30–$AA44 | sfx init | |
| $AA45–$AA64 | game IRQ install/uninstall/handler | unused |
| $AA65–$AA84 | `play` | call counter, tick/sub dispatch |
| $AC00–$AC1A | ripper init stub | `JSR $A518`; song 1; state 1; speed 9; CIA |

##### Sequencer state ($02A7–$02FF, zeroed by init; struct-of-arrays stride 1, X = voice 0..2)

| addr | meaning |
|---|---|
| $02AA | current block number |
| $02AB | song number (1) · $02AC song length · $02AD block hdr[0] (stored, never read) |
| $02AE | position in block (1..L) · $02AF call counter (0..8) · $02B0 song position (1..len) |
| $02B1 | current voice 1..3 (sequencer's voice cursor; X = $02B1−1) |
| $02B2–B4 | transpose per voice (ins[6]) · $02B5 char class · $02B6 char index · $02BA/BB freq temp · $02BC gate-on request |
| $02BD–BF | detune per voice (ins[7]) · $02C0–C2 note mode per voice (ins[$1D]: 1 tie/legato, 2 retrigger) · $02C3–C5 ctrl byte per voice |
| $02C6/C7/C8 | 3, 4, 1 (voice-loop constants) · $02C9 voice iteration count |
| $02CA–CC | per-voice gate enable (block hdr[6..8]) · $02CD–CF per-voice filter routing bit (hdr[9..11]) |
| $02D4–D6 | instrument per voice (hdr[3..5]) · $02D7 saved X · $02D9 filter-owner voice 1..3 (hdr[12]) |
| $02DA–DC | new-note flag (set at note-on, cleared at the end of every call) · $02DD–DF gate state |
| $02EB | hold-position flag (0) · $02ED–EF "reload instrument at next note" · $02F3–F5 drum active |
| $02F6–F8 | sfx request per voice ($40 idle) · $02F9 music state · $02FA sfx voice · $02FB–FD last sfx · $02FE sfx active · $02FF speed (9) |
| ZP | `$FB/FC` block/track pointer, `$FD/FE` song pointer, `$41/42` instrument/drum pointer, `$02` track length, `$12` temp |

##### Engine state ($AD00–$AD76; **not** cleared by init — residual image values are live until overwritten)

| addr (v0, +1, +2) | meaning | source |
|---|---|---|
| $AD00 | engine enable (image: 1) | — |
| $AD01 | modulation delay (calls) | ins[$1C] |
| $AD04 | delay counter | reset by mod resets |
| $AD07/$AD0A/$AD0D/$AD10/$AD13/$AD16 | mod1: mode / rate / countdown / period / phase / direction | ins[$B]/[$C]/–/[$D] |
| $AD19/$AD1C/$AD1F/$AD22/$AD25/$AD28 | mod2 (pulse): same fields | ins[$E]/[$F]/–/[$10] |
| $AD2B/$AD2E/$AD31/$AD34/$AD37/$AD3A/$AD3D | mod3 (pitch): mode / rate / countdown / period / phase / dir / type | ins[$11]/[$12]/–/[$13]/[$14]; drums: [0]→rate, [1]→period |
| $AD40/$AD43/$AD46/$AD49/$AD4F | mod4 (gate toggle): mode / rate / countdown / (ins[$17], unread) / toggle | ins[$15]/[$16]/–/[$17] |
| $AD52/53/54/55/56/57/58 | filter mod: mode/rate/countdown/period/phase/dir/type (global) | filter-owner ins[$18..$1B] |
| $AD59/$AD5C | pulse offset lo/hi · $AD5F/$AD62 freq offset lo/hi (mod1 **and** mod3 both add into it) · $AD65 filter offset | modulators |
| $AD66/$AD69 | pulse base lo/hi · $AD6C/$AD6F freq base lo/hi · $AD72 cutoff base | note-on / instrument |
| $AD73/74/75/76 | step sizes: mod1 $0A, mod2 $10, mod3 $50, filter $02 | **constants in the image, never written** |

#### 3.8.2 Entry points and conventions

- Original `init` $A518: SID[0..24] := 0; `$02A7..$02FF` := 0; `$02B0` = 1; song setup ($A468: song pointer from `$AE64/$AE69[$02AB]`, first block, header load, voice constants); sfx slots idle, `$02F9` = 0. The stub then sets `$02AB` = 1 (song), `$02F9` = 1 (play), `$02FF` = 9 (speed), CIA. Note `$AD00–$AD76` is untouched: the engine starts from whatever the image holds.
- `play` $AA65: `INC $02AF; CMP $02FF; BNE sub` — **tick**: `JSR seq ($A53D)`, `$02AF` = 0, `JSR engine`; **sub**: `JSR sfxpoll ($A566)`, `JSR engine`. No arguments; A/X/Y clobbered.
- Sequencer voice = `$02B1` (1..3) in memory; every per-voice routine begins `LDX $02B1; DEX`. Engine voice = X (0..2) via `LDX #0 … INX; CPX #3` loops. SID offset always from `voffs` ($A08D: `CPX #0/1/2 → LDY #0/7/14`), executed 8000× in 3000 calls.
- Flags: none carried across calls; `CMP #$FF` / `BEQ` chains everywhere; carry set explicitly (`SEC/CLC`) before every add/sub.
- Music state `$02F9`: 0 → reset position and do nothing (this is where the tune parks after its last block: it plays once and stays silent until the host sets 1); 1 → step; 2 → sfx only.
- sfx API (game side): store an effect number into `$02F6+voice` (≠ `$40`); `sfxpoll` starts instrument `10+n` on that voice, sets `$02FE` (which suspends note reading for all voices) and clears when done. Not exercised.

#### 3.8.3 The play routine

```
play():                                   ; 2 calls per frame
  if ++$02AF != speed(9): sfxpoll(); engine(); return
  $02AF = 0
  seq(); engine()

seq():                                    ; $A53D, once per 9 calls
  if state == 0: pos = 1; blockpos = 1; sfxpoll(); return         ; parked
  if state == 2: sfxpoll(); return
  songptr = songs[$02AB]; songlen = song[0]; block = song[pos]
  step()
  if pos == songlen: allgateoff(); state = 0                       ; song end (once-through)

step():                                   ; $A485
  if blockpos == 1: blockhdr()            ; new block: instruments, gate/filter flags, filter regs, mod resets, reload flags
  blockptr()                              ; $FB = block+15, $02 = L
  for v = 1..3 ($02B1):
      sfxslot(v)
      if !$02FE:
          ch = track_v[blockpos]          ; LDA ($FB),Y with Y = blockpos (1..L)
          classify(ch)                    ; note index / drum index / rest
          if idx == 24: rest(v)
          elif class == 0: note(v)
          else: drumnote(v)
      $FB += L                            ; next voice's track
  if ++blockpos > L: blockpos = 1; pos++  ; (unless hold flag)

note(v):                                  ; $A3CF
  if drumactive[v]: clear offsets; drumactive = 0
  if reload[v]:                           ; first note after a rest, or retrigger-mode instrument
      loadins(v)                          ; AD, SR, ctrl&~1 (gate off), PW → SID; 17 engine params
      mod3reset(v)                        ; pre-load bend
      reload[v] = newnote[v] = mode[v]-1  ; mode 1 (tie): 0 → next note is legato; mode 2: 1
      notefreq(v); writefreq(v)           ; base freq (+transpose, ±detune) → SID + shadow
      gate(v, on)                         ; ctrl gate off then gate on (retrigger); newnote = 1 if it was off
  else: notefreq(v); writefreq(v)         ; legato: pitch only

rest(v):  gate(v, off) ; reload[v] = 1
drumnote(v): drumactive=1; presets (mods off, mod3 type 1 mode 2, retrigger); drum record → mod3 rate/period, abs freq,
             ctrl, AD (SR = AD & $0F), PW; writefreq; gate off; clear offsets; gate on; reload = 1

engine():                                 ; $A594, every call
  if !$AD00: return
  for X in 0..2: voicemod(X)
  filtermod(); $D416 = cutbase + cutoffset
  newnote[0..2] = 0

voicemod(X):                              ; $A5C2
  if !newnote[X] and delayctr[X] != delay[X]: delayctr[X]++; return      ; hold modulation `delay` calls after note-on
  mod1(X); mod2(X); mod3(X); mod4(X)
  $D400/1 = freqbase + freqoff (16-bit); $D402/3 = pwbase + pwoff (16-bit)

modN(X):                                  ; template ($A60C mod1 / $A692 mod2 / $A718 mod3 / $A7B1 filter)
  if rate == 0: return
  if !newnote: if --countdown != rate: return       ; fires every (100 − rate) calls
  countdown = 100
  if mode != 1 and newnote: resetN(X)               ; mode 1 = free-running (no reset on notes)
  if period == $FF: offset = $D41B (lo = hi = same byte); return     ; random modulation
  [mod3 only: if type != 2 and phase+1 == period: return]           ; one-shot: stop at the end
  offset ±= step (16-bit; direction bit)             ; mod1/mod3 → freq offset, mod2 → pw offset, filter → cutoff offset
  if ++phase == period: phase = 0; direction ^= 1     ; triangle
resetN: offset = 0; phase = period/2; dir = 0 (subtract first); countdown = 100     ; centred triangle
mod3reset: type 0: dir=0(add), offset = −step·(period−1) (bend up into the note); type 1: dir=1, offset = +step·(period−1) (bend down);
           type 2: phase = period/2 (triangle)   — the pre-load is a loop of period−1 16-bit adds
mod4(X): if rate and gateenable[X]: every (100−rate) calls: toggle ^= 1; $D404 = ctrl−1+toggle   ; gate tremolo
```
Verified in the log: ticks at calls 8, 17, 26 … (every 9); voice-1 note-on at call 8 writes `05 06 04(=$40) 02 03 00 01 04(=$41)` in that order; voice-3 drum writes freq $6479 then ctrl `$14 $14 $14 $15`; the drum's mod3 (type 1, rate $63 → every call, period $14, step $50) shows as `0F/0E` stepping down $50 per call until the next note; the song stops at call 7775 (= 3887 frames ≈ 1:18; HVSC 1:20).

#### 3.8.4 Data formats

**Song** (`songs[n]` via `$AE64/$AE69`, 5 entries): `len, block[1..len]`. Song 1: 33 entries over 12 distinct blocks (7 7 9 8 8 8 12 9 16 9 15 13 8 13 14 13 8 13 14 11 11 7 7 15 10 10 10 15 17 17 16 15 2); block 2 = 16 steps of silence with zero instruments = the tail. Songs 0/2/3/4 (2 entries each) are the game's other jingles.

**Block** (`$AE6E/$AE86[n]`, 20 blocks $B000–$B77B): 16-byte header + 3 contiguous tracks of L bytes.
```
hdr[0] $D4/$00 (stored to $02AD, unused)   hdr[1] L = steps (12, 16, 24, 96)   hdr[2] $10/$0C (unused)
hdr[3..5] instrument per voice   hdr[6..8] gate enable per voice (0 = voice cannot gate on)   hdr[9..11] filter routing bit per voice
hdr[12] filter-owner voice 1..3 (its instrument's bytes 8-10/$18-$1B program $D416/17/18 and the filter modulator)   hdr[13..15] unused
track_v[1..L] at hdr+16+(v-1)*L   (position counts from 1: byte 0 of each track is never read — that is why the header is 16 bytes)
```
**Track byte = a C64 keyboard character** (Walker typed the music): note keys `Q 2 W 3 E R 5 T 6 Y 7 U I 9 O 0 P @ - * \ ^ HOME DEL` = semitones 0..23 (two octaves on the top two rows), `space` = rest; shifted keys (`$AFCE`: `$ 24 FF 39 23 C5 D2 25 D4 26 D9 27 D5 C9 29 CF 30 D0 BA DD C0 A9 DE 93 94 A0`) = drum/effect 0..23, shift-space = rest, `$B0` aliased to `$30`. Census over song 1: 1800 note keys (P 250, O 235, U 210, Y 185, * 170, T 155 …), 718 spaces, 122 drum keys (shift-I `$A9` 54, shift-Y `$D9` 30, shift-P `$D0` 12, `$93` 12, `$B0` 10, `$C0` 2, `$DD` 2). Pitch = key index + instrument transpose (ins[6] = $14/$06/$24 here) → 96-entry freq table.

**Instrument** (30 bytes, `$AECE/$AEEE[n]`, 32 slots; tune uses 0–3 at $B7A5+, 5/6 point past the image = zeros = silent):
```
[0] attack [1] decay ($D405 = [0]<<4|[1])   [2] sustain (value $F is remapped to $E) [3] release
[4] ctrl byte with gate (e.g. $41)   [5] pulse: hi nibble → PW hi, lo nibble<<4 → PW lo
[6] transpose (semitones)   [7] detune (voice 2 subtracts, voice 3 adds; voice 1 ignores)
[8] filter mode nibble ($D418 = [8]<<4 | $0F)   [9] cutoff ($D416 base)   [10] resonance ($D417 hi nibble | routing bits from block)
[$B] mod1 mode  [$C] mod1 rate  [$D] mod1 period ($FF = random)        — pitch triangle (step $0A)
[$E] mod2 mode  [$F] mod2 rate  [$10] mod2 period ($FF = random)       — pulse triangle (step $10)
[$11] mod3 mode [$12] mod3 rate [$13] mod3 period ($FF = random) [$14] mod3 type (0 bend-up-in, 1 bend-down-in, 2 triangle) — pitch (step $50)
[$15] mod4 mode [$16] mod4 rate [$17] (stored, unread)                  — gate toggle
[$18] filter mode [$19] rate [$1A] period ($FF = random) [$1B] type   — cutoff triangle (step $02), only from the filter-owner instrument
[$1C] modulation delay (calls after note-on)   [$1D] note mode: 1 = tie (reload only after a rest), 2 = retrigger every note
```
Rate semantics: the modulator fires when its countdown (reloaded to 100) reaches `rate`, i.e. every `100 − rate` calls; rate 0 = off, $63 = every call. Example ins 0: `AD $03 SR $A0 ctrl $41 pw $7… tr $14 det $30 vol $1 cut $6E res $A | mod1 mode 1 rate 0 | mod2 mode 2 rate 1 period $63 (slow pulse sweep) | mod3 mode 2 rate $63 period 2 type 1 | mod4 off | filter mode 1 rate 0 | delay 9 | mode 1`.

**Drum record** (7 bytes, `$AE9E/$AEB6[n]`, 24): `[0] mod3 rate, [1] mod3 period, [2,3] absolute freq lo/hi, [4] ctrl, [5] AD (SR := AD & $0F), [6] PW nibbles`; `drumnote` presets mod1/mod2/mod4 off, delay 0, mod3 type 1 mode 2, note mode 2 — so a drum is "absolute pitch + one-shot downward bend of period×$50 at `rate`" (e.g. drum 20: `63 1E 63 38 81 29 00` = noise $81, freq $3863, bend down 30 steps every call).

**Frequency table**: 96 × (lo at $AF0E, hi at $AF6E), C-0 = $0116, PAL. Index = key (0..23) + transpose; the two "extra" keys HOME/DEL give 25 chords of range beyond two octaves.

#### 3.8.5 SID write schedule

Every call: for each voice with the modulation delay expired: `$D400,$D401,$D402,$D403` (base + offsets, 16-bit each), and `$D404` from mod4 when it fires; then `$D416` = cutoff base + offset (every call, always). On a tick: note-on writes `$D405,$D406,$D404(gate off),$D402,$D403` (instrument load), `$D400,$D401` (freq), `$D404` (gate off again if retrigger mode) `$D404` (gate on) — all in one call; drums the same with their record. Block start writes `$D418,$D416,$D417`. `$D415` is written only by init (0); AD/SR only at instrument load. No hard restart: gate 1→0→1 inside one call (envelope retrigger from the current level) plus fresh AD/SR — Walker's "click"; legato (mode 1) writes only the frequency.

Order per voice: voice 1, 2, 3 in the engine (X = 0..2) and 1..3 in the sequencer; the sequencer runs *before* the engine on tick calls, so a note's base freq is written by `writefreq` and then again (base + offset) by the engine in the same call.

Volatile: `$D41B` when a modulator's period is `$FF` (random offset each fire; both offset bytes get the same random byte). Given a pinned `$D41B` byte stream the output is deterministic — the value only lands in an additive offset, no control flow depends on it. In this tune the only reads are the 8 first calls' voice-3 mod3 (residual `$AD36 = $FF`); the residual engine state also makes calls 0–7 emit garbage frequency/pulse/gate writes on all three voices (inaudible: ADSR are 0 from init's SID clear).

#### 3.8.6 Techniques specific to this player

| technique | citation | why |
|---|---|---|
| **Score as keyboard characters**: track bytes are the C64 keys of a two-row piano; note = linear scan of a 25-byte key table, then a 25-byte shifted-key table (up to 50 `CMP abs,X` per byte, mean 17) | $A000–$A02D, tables $AFE7/$AFCE | the "editor" was typing into memory; decode cost is irrelevant at 3 lookups per 4.5 frames |
| Voice number kept in memory (`$02B1` = 1..3), `LDX $02B1; DEX` at the head of every per-voice routine | $A073, $A0A0, $A109, $A28D, $A3CF … | routines are callable from the sequencer without register discipline |
| SID offset by `CPX` chain (`CPX #0 → LDY #0; CPX #1 → LDY #7; CPX #2 → LDY #14`) | $A08D (8000 executions) | no table; 3 compares |
| Per-register `CPX #0/#1` chains selecting `$D405/$D40C/$D413` etc. instead of `STA $D405,Y` | $A126–$A1C3 (six registers × 3 targets) | code written register-by-register; a decompiler sees 18 absolute SID stores that are one indexed store |
| Modulator template ×4 (pitch, pulse, pitch-2, filter): identical 100-countdown / period / phase / direction machines with different bases and step constants | $A60C, $A692, $A718, $A7B1 | unrolled by *modulator*, indexed by voice — the opposite of Galway/Follin |
| Rate as inverted countdown target (`countdown = 100; DEC; CMP rate`) | $A616–$A61F | one byte per modulator gives periods 1..100 calls; 0 = off |
| Two modulators (mod1, mod3) summing into one frequency offset; freq/PW written as base + offset every call | $A5E5–$A60A | vibrato + bend compose additively; the base shadow makes note-on cheap |
| One-shot bend by pre-loading the offset with `−step×(period−1)` via a repeated-add loop | $A8EF–$A911, $A916–$A938 (and filter $A96F/$A98E) | multiply by loop; worst case 254 iterations → a 19081-cycle tick call (≈ 97 % of a frame at 2× speed) |
| Random modulation from `$D41B` when period = `$FF` | $A640/$A6C6/$A74C/$A7ED | one compare turns any modulator into a noise source; the same byte goes to lo and hi |
| Gate retrigger by writing ctrl−1 then ctrl in the same call, with fresh AD/SR | $A0A0–$A0E6, $A172 | envelope restart without hard-restart timing |
| Instrument mode byte doubling as a flag source: `mode−1` → both `reload` and `newnote` | $A3EB–$A3F4 | 1 = tie, 2 = retrigger in one subtraction |
| Sustain nibble `$F` remapped to `$E` | $A140–$A146 | avoids the 6581 full-sustain quirk (or a data-entry convention) |
| Block header as the per-block "mixer": instrument per voice, gate enable per voice, filter routing bits, filter-owner voice | $A379–$A3BC, $A230 | orchestration lives in the score, not in instruments |
| Position starts at 1 → track byte 0 unused → 16-byte header | $A454, $A4A1 | off-by-one turned into layout |
| Detune by voice *number* (voice 2 −, voice 3 +) | $A045–$A072 | chorus without per-note data |
| Zero SMC, zero jump tables, zero stack tricks; `PHA/PLA` only to keep ctrl across the gate logic | $A0B7… | straightforward code |
| Residual state as initial state: engine block never cleared, `$AD00 = 1` from the image, step constants in RAM | $AD00–$AD76 | init clears only page 2 |
| Data-file layout with the ripper's stub in the middle of the instrument bank | $AC00 | — |

#### 3.8.7 What it reduces to

Chameleon is a **typed score interpreter with a four-oscillator LFO bank per voice**. State: 89 bytes of sequencer variables in page 2 (of which ~40 are live), 119 bytes of engine state (four modulators × 6 fields × 3 voices, offsets and bases). Per call: for each voice, run up to four identical "every N calls, step a triangle (or one-shot ramp) by a constant, flip at the period" machines and write base+offset to freq and pulse; step the filter's copy; write cutoff. Every 9th call: for each voice read one character, map it through the keyboard table to a semitone or a drum, and either change pitch (tie), retrigger the instrument (ADSR, ctrl, PW, freq, gate off/on), start a drum (absolute pitch + bend), or gate off. Every L characters: load a 16-byte block header (instruments, gate enables, filter routing/owner) and reset the modulators.

Statically decidable: everything — song, blocks, instruments, drums, key tables, the four step constants, the CIA period, the tick divider — is constant data; the only runtime input is `$D41B` on the "period $FF" path (never taken by this song's data after call 8) and, indirectly, the residual image state (fixed by the file). Dead in this tune: sfx API and state 2, the game IRQ installer, `$A587` engine clear (never called by anything), the mod1/mod2/filter random paths, drum "period $FF" (drum 7 exists but is unused), songs 0/2/3/4.

The player in ~25 lines:
```
each call:
  if ++cnt == 9: cnt = 0
     if state==1: (blockpos==1 → header: ins/gate/filter per voice, resets)
                  for v: ch = track[v][blockpos]; idx = keytable(ch)
                         rest → gate off, reload=1
                         note → if reload: load ins (ADSR, ctrl-1, PW), mod3 preload; freq=tab[idx+tr[v]]±det; gate off,on
                                else freq only
                         drum → presets, drum record → freq/ctrl/AD/PW, gate off, on
                  blockpos++ (wrap → pos++; pos==len → gate off all, state=0)
  for v: if delay expired: for m in mod1,mod2,mod3,mod4: if due: (reset on new note) offset ±= step, phase/dir; (period $FF → random)
         SID freq = base+off; SID pw = base+off; mod4 → ctrl toggle
  filter mod → SID cutoff = base+off
```

#### 3.8.8 Decompiler notes

- Code/data: code $A000–$AA86 and the stub $AC00–$AC1A; everything else data. Data reached by index: `$AF0E,X/$AF6E,X` (freq), `$AFE7,X/$AFCE,X` (key tables), `$AE64/69/6E/86/9E/B6/CE/EE,X` (pointer tables), `($41),Y` (instrument/drum records), `($FB),Y` (block/track), `($FD),Y` (song). The engine cells `$AD01–$AD76` are data *with initial values from the image* — a decompiler must import them, not zero them (calls 0–7 depend on them; so does `$AD00`, the enable flag, and the four step constants).
- Per-voice struct: `abs,X` with X ∈ {0,1,2} → stride-1 fields; two structs (page 2 sequencer, `$AD01+` engine). Note the double convention: sequencer routines derive X from `$02B1−1`; engine routines take X from the loop. Type `$02B1` as `voice+1`.
- The four modulator copies are one function of (mode, rate, countdown, period, phase, dir, step, offset-lo, offset-hi, [type]) at four base tuples; recover by diffing $A60C/$A692/$A718/$A7B1 (identical instruction streams modulo operands, plus mod3's `type` test and the filter's global addressing).
- The `CPX #n` chains in `loadins` are `STA $D405,Y`-equivalents: fold to indexed stores with Y = voffs(X).
- Cadence: two-level phase — `$02AF` (0..8) selects sequencer vs not; there is no per-frame notion at all (2 calls/frame is only the CIA period). Model at call granularity; the sequencer step is 9 calls = 4.5 frames, so note events alternate between the first and second call of a frame.
- Volatile: `$D41B` reads are data-only sinks (offset ← random); model as an input stream; in this tune it matters only for the first 8 calls, and those writes are masked by ADSR = 0.
- Timing trap: a tick call can cost 19081 cycles (mod3/filter pre-load loops with period up to 254) — at 2 calls per frame that is a real jitter/latency hazard on hardware and a cost a cycle-model must reproduce if it cares about the CIA phase; the SID write *sequence* is unaffected.
- Traps: position-from-1 track indexing (16-byte header, byte 0 of each track dead); instruments 5/6 point outside the image (silent zeros); song end parks the player in state 0 forever (the tune plays once; HVSC's 1:20 is the stop time); `$02AD`, `$02D0`, `$AD49`, `$AD4C` are stored and never read; both `hdr[6..8]` gate enables and `mode` gate the same `$D404` write in `gate` — a voice can be muted per block; the mod3 `type != 2` one-shot test compares `phase+1` with `period`, so period 1 never moves; drum SR = AD & $0F is a deliberate reuse of one byte.

### 3.9 lft — Blackbird (Quintessence, 2017), the LZ-compressed player


#### 3.9.0 Identity

| item | value |
|---|---|
| file | `MUSICIANS/L/Lft/Quintessence.sid`, PSID, load $1000–$25FF (5632 bytes), init $1000, play $1003, 1 subtune, HVSC length 3:28 (208 s) |
| speed | PSID speed word 0 → 1 call per frame. There is no tempo *counter*: the row timer `$E6` is decremented by 7 every call and the row is 5 frames (`$EA` = $1C = 4·7; frames per row = `$EA`/7 + 1). Row frames measured at call 3, 8, 13, … |
| player | **emitted per tune.** $1003–$12ED play code (747 bytes; 325 executed instruction sites over the full 10426-frame trace, 711 executed code bytes, 17 statically reachable sites never executed), init $1634–$1689 (86 bytes). All 40 HVSC Blackbird tunes carry a *different* player image (byte-compare of the load band); executed instruction sites range 265–349 across the 39 that trace |
| cost | 193 instructions / 623 cycles per call mean, 327 / 1022 max over the full song (row frames 221/720, unpack frames 203/651, engine-only frames 136/441). The published guide states an 18-rasterline (1134-cycle) worst case |
| song data | one **LZ-compressed byte stream** $168A–$221A (2961 bytes) read *downwards* from $221A, expanding to 7579 bytes of note tokens in three 256-byte ring buffers at $2300/$2400/$2500 (2.56:1) |
| SMC | 7 play-time cells — 1 `JMP` operand ($12EC), 3 operand-address high bytes carrying one pointer ($12A6/$12D4/$12DC) and 3 immediates ($125F swing mask, $12D9 transpose, $12E0 copy end) — plus 2 written at init only (the `JMP`→`RTS` opcode at $12EB, the cutoff accumulator $1189, whose instruction this tune never executes) |
| illegal opcodes | 14 sites, 3 kinds: `SBX #imm` ×9, `LAX zp` ×3, `LAX (zp),Y` ×1, `NOP #imm` ×1 (the last one used as a *skip*, §3.9.6) |
| volatile | **none.** Not one instruction — executed or statically reachable from the executed set — names $D011/$D012/$D019/$D41B/$D41C or any CIA register, and the play-phase `(site, address)` read set contains no I/O address at all (it spans $00E0–$25FF, i.e. zero page, the tables and the ring buffers). Verified for all 39 traceable HVSC Blackbird tunes, not just this one. $D415 is the only SID register the player never writes |
| stack | zero. No `JSR` on the play path (0 sites), no `PHA`, no writes to $0100–$01FF at all; init uses two `JSR`s into the middle of play |
| provenance | Blackbird 1.0 (lft, released at Datastorm 2017) publishes the complete playroutine source in Appendix A of its User's Guide. Every structural claim below comes from the bytes of this tune; the guide supplied the author's *names* (`zp_bufs`, `zp_inptr`, `zp_master`, `zp_pendoob`, `v_trtimer`, `v_wavemask`, `fxtable`, `wavetable`, `filttable`, `pwprepare`, `prepare1/2/3`, `everyframe`, `execute`, `unpackvoice`, `preparejmp`, `m_groove`, `m_cutoff`, `m_transp`, `INS_RESTART`) and three cross-checks (the 18-rasterline bound, the 9–12-page footprint, and "a variant of Lempel-Ziv compression featuring a copy-with-transpose primitive"). Where the guide's editor vocabulary and the binary disagree the binary is used and the difference is stated |

#### 3.9.1 Memory map and state

##### Code

| range | routine (author's label) | role |
|---|---|---|
| $1000–$1002 | — | `JMP $1634` (init) |
| $1003–$1017 | `playroutine` | phase decode: `LAX $E6; BEQ $1015; SBX #$07; STX $E6; CPX #$15; BCS $1012; JMP $1272` — three-way on the row timer |
| $1018–$1042 | `prepare1` | per voice: `INC` the row timer; if it expired, take an optional out-of-band command ($F9–$FF) and an optional effect token ($C8–$F8) |
| $1043–$1080 | `prepare2` | per voice: take an optional instrument token ($80–$B7); start the hard restart |
| $1081–$10AD | `prepare3` | per voice: take the note or the delay token; set the row timer |
| $10AE–$1194 | `everyframe` | the whole audible engine: per voice pitch program → $D400/1, wave program → $D404 (+$D402/3), then the filter program → $D418/$D417/$D416; `RTS` at $1194 |
| $1195–$1197 | `sync_error` | `JMP $10AE` — stall the sequencer when an external syncpoint has not arrived (never executed here) |
| $1198–$11ED | `execute` (head) | out-of-band command decode: syncpoint / tempo+groove / end-of-stream |
| $11EE–$1269 | `execute` (tail) | per voice note-on (effect, instrument, ADSR, hard restart), then `$E6 ← $EA`, groove `EOR`, `preparejmp ← prepare1` |
| $126A–$1271 | `stopstream` | emit one `$C0` byte forever after the stream ends (never executed here) |
| $1272–$12E3 | `unpackvoice` | decompress **one** LZ token into voice `$E6`'s ring buffer |
| $12E4–$12EA | `postunpack` | store the write cursor; `LDX #$0E` |
| $12EB–$12ED | `preparejmp` | `JMP $1018` — opcode patched to `RTS` by init, operand low byte patched to `$18`/`$43`/`$81` by the three prepare passes |
| $1634–$1689 | `initroutine` | |

##### Data

| range | size | what |
|---|---|---|
| $12EE–$1302 | 21 | per-voice block A, stride 7 (3 × 7 fields) |
| $1303–$1315 | 19 | per-voice block B, stride 7 (3 × 5 fields + 2 pad) |
| $1316–$1330 | 27 | `$EE` padding so the next table lands 207 bytes below $1400 |
| $1331–$13FF | 207 | frequency table: `freq_msb` at $1331 (96 bytes) and `freq_lsb` at $1391 (111 bytes) — **the two arrays overlap by 15 bytes**, because 96 semitones = 8 octaves = ×256, so `freq_msb[k+96] = freq_lsb[k]`. `freq(k) = mem[$1331+k]<<8 \| mem[$1391+k]`, equal temperament, `freq(97)` = 29970 = A-440 exactly |
| $1400–$14FF | 256 | `pwprepare`: page-aligned pulse-width map. `PW = (b&$0F)<<8 \| b`, so this table linearises the packing — PW runs $0F8F down to $0808 in steps of 16 and back: a **triangle in 12-bit pulse space** driven by an 8-bit accumulator |
| $1500–$150D | 14 | `ins_ad` (operand `$14FF,Y`: 1-based) |
| $150E–$151B | 14 | `ins_sr` (operand `$150D,Y`) |
| $151C–$1529 | 14 | `ins_wave`: wave-program offset (operand `$151B,Y`) |
| $152A–$1537 | 14 | `ins_filt`: filter-program offset, 0 = leave the filter alone (operand `$1529,Y`); all 14 are 0 in this tune |
| $1538–$1558 | 33 | `fx_start`: effect-program offset (operand `$1537,Y`) |
| $1559–$155C | 4 | `filttable`: one 3-byte row `1F 00 80` + terminator `FF` |
| $155D–$15EB | 143 | `fxtable`: the pitch/arpeggio programs |
| $15EC–$1633 | 72 | `wavetable`: the control-byte/pulse programs |
| $168A–$221A | 2961 | the compressed stream (`streamstart` = $221A, consumed downwards; the last thing read is the end-of-stream command's 2-byte operand at $168A/$168B, which sets the pointer back to $220A) |
| $221B–$22FF | 229 | unused zeros |
| $2300–$25FF | 768 | `unpackbufs`: three 256-byte ring buffers, one per voice, zero in the file |

##### Per-voice state — two struct-of-arrays blocks, **stride 7 = the SID voice stride**, X ∈ {0,7,14}

| v1 addr | author's name | meaning |
|---|---|---|
| $12EE,X | `v_pwidth` | pulse-width accumulator (index into `pwprepare`) |
| $12EF,X | `v_trwpos` | ring-buffer **write** cursor |
| $12F0,X | `v_pendnote` | note pending for the next row boundary (0..63) |
| $12F1,X | `v_pendfx` | effect number pending (0 = none) |
| $12F2,X | `v_pendins` | instrument pending (0 none, $FE gate-off, $FF legato, 1..14 instrument) |
| $12F3,X | `v_wavemask` | AND-mask applied to every control-byte write: $FF normal, $FE gate off |
| $12F4,X | `v_trtimer` | row countdown, negative, `INC`ed once per row; 0 = read this row |
| $1303,X | `v_fxpos` | cursor into `fxtable` |
| $1304,X | `v_currfx` | sticky effect number |
| $1305,X | `v_currins` | sticky instrument number |
| $1306,X | `v_basepitch` | note × 4 (quarter-semitone units) |
| $1307,X | `v_wavepos` | cursor into `wavetable` |

Three of the seven block-A cells (`v_pwidth`, `v_pendnote`, `v_wavemask`) are **never written by init**: they take the assembler's `.byt` values from the image (`00 00 00 00 00 FE FF` per voice, visible at $12EE). A decompiler must import them (§4, observation 9).

##### Zero page ($E0–$EF; `zp_base` = $E0) — the same stride 7, with the globals living in the unused slots

| addr | name | meaning |
|---|---|---|
| $E0/$E1, $E7/$E8, $EE/$EF | `zp_bufs` | per-voice ring-buffer **read** cursor + buffer page, addressed `LDA ($E0,X)` with X = 0/7/14 |
| $E2/$E3 | `zp_inptr` | compressed-stream pointer, `LDA (zp),Y`, decremented |
| $E4 | `zp_trwpos` | write cursor scratch during one unpack |
| $E5 | `zp_pendoob` | pending out-of-band command byte |
| $E6 | `zp_master` | row timer in units of 7 — **and** the voice offset the unpacker uses |
| $E9 | `zp_filtpos` | cursor into `filttable` (global, one filter program for the tune) |
| $EA | `zp_tempo` | row length in units of 7 |
| $ED | `zp_extsync` | external-sync shift register, written by the host, never by the player |

#### 3.9.2 Entry points and conventions

- **init** `$1000 → $1634`: `zp_inptr ← $221A`; `preparejmp` operand ← `$18`; `$ED = $E5 = $E9 = 0`; $D400–$D418 ← 0; `m_cutoff` ($1189) ← $80; for X = $0E, 7, 0: buffer page ← $25/$24/$23, read cursor ← 0, `v_trwpos` ← 0, `v_pendfx` = `v_pendins` ← 0, `v_trtimer` ← $FF. Then `LDA #$60; STA $12EB` — **the tail dispatch's opcode is patched to `RTS`** — `LDX #$07; JSR $1009`, `JSR $1003`, `LDA #$4C; STA $12EB` restores the `JMP`, `$E6 ← $15`, `RTS`. The two `JSR`s prime the ring buffers of voices 2 and 1 by entering `playroutine` *in the middle* ($1009 = `STX $E6`, so the first call runs with $E6 = 7 and X = 7). The subtune number in A is ignored.
- **play** `$1003`: no arguments; A/X/Y/flags clobbered; returns by the `RTS` at $1194 (the engine's tail). Nothing else in the player returns.
- Register conventions: **X is the SID voice offset 0/7/14 everywhere** — it indexes the per-voice arrays (`$12EE,X`), the zero-page pointer pairs (`($E0,X)`), and the SID itself (`STA $D400,X`). The voice loop is always `TXA; SBX #$07; BPL head` (or `BMI done`) — five sites: $1037, $106B, $10AA, $1156, $1256. Y is a table cursor (`fxtable`, `wavetable`, `filttable`, the instrument columns) or the stream offset in `(zp),Y`.
- Flags as arguments: C is carried from the `LSR A` at $108A across eight instructions to the `ROL A` at $10A1 (it becomes the note's 1-or-2-row duration bit); `$1021 BCC $102A` branches *into the middle* of a `CLC; SBC #$C7` pair because the `CMP #$F9` already left C = 0, so both paths compute `A − $C8`; and several `ADC`/`SBC` sites run on an inherited carry rather than a fresh one — the frequency sums at $10D4/$10DD/$10ED/$10F6/$110F/$1118, the stream-pointer subtracts at $11A7/$11C1/$12BC, the pulse add at $1143 and the transpose `SBC #$1F` at $12B1 (the author's own comment on the frequency sum is that it "adds a small consistent error").
- No `JSR`, no `JMP (ind)`, no stack use on the play path.

#### 3.9.3 The play routine

```
play():                                        ; $1003, once per frame
  t = master                                   ; $E6, counts down by 7
  if t == 0:  execute() ; return               ; $1198 — the row boundary
  master = t - 7                               ; SBX #$07
  if master >= 21:  everyframe() ; return      ; $1012 — nothing to prepare or unpack
  unpackvoice(master)                          ; $1272 — master is 0/7/14 = the voice offset
  preparejmp()                                 ; $12EB — prepare1 / prepare2 / prepare3
  everyframe()                                 ; every path ends here; the RTS is at $1194
```
So one row of five frames runs, in order: `everyframe` alone (t = 28) · unpack voice 3 + `prepare1` (21) · unpack voice 2 + `prepare2` (14) · unpack voice 1 + `prepare3` (7) · `execute` (0). The three prepare passes are selected by the low byte of one `JMP`, each pass patching it to the next; `execute` patches it back to `prepare1`. The row's tokens are therefore read one *class* per frame, three frames deep — which is exactly what makes the hard restart free (see below).

```
prepare1(v):                                   ; $1018, per voice, X = 14,7,0
  if ++trtimer[v] < 0: return                  ; note still sounding
  b = buf[v][read[v]]
  if b >= $F9: pendoob = b ; read[v]++ ; b = buf[v][read[v]]     ; out-of-band command
  a = b - $C8 ; if a < 0: return                                 ; shared tail, C already 0
  read[v]++ ; currfx[v] = pendfx[v] = a                          ; effect number 1..48

prepare2(v):                                   ; $1043
  if trtimer[v] < 0: return
  b = buf[v][read[v]]
  if b < $80:  a = currins[v]                  ; bare note: retrigger the sticky instrument
  elif b >= $B8: return                        ; a delay token, left for prepare3
  else: read[v]++ ; a = b - $82                ; $80 -> $FE gate off, $81 -> $FF legato
  pendins[v] = a ; if a >= 0: currins[v] = a
  if a >= INS_RESTART+1 (= 2):                 ; hard restart, two frames early
      $D406+7v = 0 ; wavemask[v] = $FE         ;   SR = 0 and the gate goes off

prepare3(v):                                   ; $1081
  if trtimer[v] < 0: return
  b = buf[v][read[v]] ; read[v]++
  if b >= $80: trtimer[v] = b | $F0            ; delay token: 1..16 rows
  else: pendnote[v] = b >> 1                   ; note 0..63
        if pendins[v] == 0: pendins[v] = currins[v]      ; (dead here)
        pendfx[v] = currfx[v]
        trtimer[v] = $FE | C                   ; C = bit 0 of the note byte: 2 or 1 rows

execute():                                     ; $1198
  o = pendoob
  if o & 1 and (extsync >>= 1) has no bit: goto everyframe        ; syncpoint stall
  if o & 2: inptr -= 2 ; tempo = [inptr+2] ; m_groove = [inptr+1] ; ; two raw stream bytes
  if o & 4: inptr -= 2 ; inptr = ([inptr+1], [inptr+2])           ; end of stream: jump
            if !(o & 1): trwpos[2] = read[2] ; trwpos[1] = read[1]+7 ; trwpos[0] = read[0]+7
  pendoob = 0
  for v in 3,2,1:
      basepitch[v] = pendnote[v] << 2
      if pendfx[v]: fxpos[v] = fx_start[pendfx[v]]
      y = pendins[v]
      if y == 0: pass
      elif y < 0: if y == $FE: wavemask[v] = $FE                  ; gate off ($FF = legato: nothing)
      else:
          if y >= INS_RESTART2+1 (= 2): $D406+7v = $0F            ; hard restart 2
          wavemask[v] = $FF
          if ins_filt[y]: filtpos = ins_filt[y]
          wavepos[v] = ins_wave[y]
          if y >= INS_RESTART+1: $D405+7v = $00 ; $D404+7v = $01  ; gate on with ADSR 0000
          $D405+7v = ins_ad[y] ; $D406+7v = ins_sr[y]
      pendfx[v] = pendins[v] = 0
  master = tempo ; tempo ^= m_groove ; preparejmp <- prepare1     ; swing
  everyframe()

everyframe():                                  ; $10AE, X = 14,7,0 (`TXA; SBX #$07; BMI done`)
 for v in 3,2,1:
  y = fxpos[v]                                                    ; --- pitch
  fxpos[v] += 1 + (fxtable[y+1] < 0 ? fxtable[y+1] : 0)           ; negative next byte = loop back
  d = fxtable[y]
  if d == 0: $D400/1 = $FFFF                                      ; "fixed frequency"
  else:
     p = d + basepitch[v]                       ; 9 bits: 4*note + quarter-semitone offset
     y = p >> 2 (ROR then LSR, the carry supplying bit 8); q = p & 3
     q=0: F[y+24]                q=1: F[y+19] + F[y+1]
     q=2: F[y+12] + F[y+13]      q=3: F[y+0]  + F[y+20]           ; 16-bit, carry-chained
     -> $D400/$D401
  y = wavepos[v]                                                  ; --- waveform / pulse
  w = wavetable[y] ; if w >= $C0: y += w+1 ; w = wavetable[y]      ; relative backward jump
  $D404+7v = w & wavemask[v]
  if w & $40:                                   ; a pulse waveform: a parameter byte follows
     wavepos[v] = y + 2 ; b = wavetable[y+1]
     pwidth[v] = (b < 0) ? b<<1 : b + pwidth[v]
     $D402+7v = $D403+7v = pwprepare[pwidth[v]]
  else: wavepos[v] = y + 1
  ; --- filter (global), $1165
  y = filtpos ; filtpos += (filttable[y+3] < 0 ? filttable[y+3] : 2) + 1
  $D418 = filttable[y] ; $D417 = filttable[y+1]
  c = filttable[y+2] ; if c & $80: m_cutoff = c<<1 ; $D416 = (c<<1) ^ $80
                      else:        a = m_cutoff + (c<<1 with sign restored)
                                   if !overflow: m_cutoff = a ; $D416 = a ^ $80
```

Verified in the write log: row frames at calls 3, 8, 13, …; at call 21 (`prepare2`) voices 2 and 1 get `0D=00 06=00` (SR ← 0) and their control bytes lose bit 0 for the next two frames; at call 23 (`execute`) voice 2 writes `0D=0F 0C=00 0B=01 0C=00 0D=76` and voice 1 `06=0F 05=00 04=01 05=12 06=3A` — SR = $0F, AD = 0, gate on with waveform 0, then the instrument's AD and SR, all inside one call. 2085 row frames × 5 frames = 10425 = the HVSC length of 208 s.

#### 3.9.4 Data formats

**The score is compressed and there are no patterns in memory.** Each voice's note list is one run-length-ish token stream; the three streams are interleaved into a single LZ-compressed stream, and the player decompresses one token per call into a per-voice ring buffer. What the sequencer reads is the buffer, not the file.

**LZ stream** (`$168A–$221A`, consumed *downwards*; one token per `unpackvoice` call, only while `trwpos[v] − read[v] ≥ 0` signed, i.e. fewer than 128 bytes of lookahead):
```
control byte  t t t t t n n n
  t == 0, n > 0 : literal run of n bytes, stored below the control byte and copied in reverse
  t == 0, n == 0: end of stream — emit $C0 for ever (never taken here)
  t > 0         : copy n+3 bytes from earlier in this voice's buffer, transposed by t-16 semitones
                  (the byte added is 2*(t-16), computed as `LSR;LSR;SBC #$1F` with C = 0);
                  one offset byte follows; the copy adds the transpose only to bytes with bit 7
                  clear, so it moves *notes* and leaves instrument/effect/delay tokens alone
```
Census over the whole song: 1332 tokens = 302 literal runs (lengths 1–7) + 1030 matches (lengths 3–10, each followed by one offset byte added to the write position, so the window is the whole 256-byte buffer). Transposes are **always even** (−30…+28, 455 of them zero) because a note byte carries the semitone in bits 1–7 and the row-duration flag in bit 0: an even delta transposes by delta/2 semitones without disturbing the duration. 2961 stream bytes expand to 7579 token bytes.

**Token stream** (per voice, after decompression; the three `prepare` passes are the tokenizer, one class per frame):
```
$F9–$FF  out-of-band command; bit0 syncpoint, bit1 tempo+groove follow, bit2 end of stream   [3 in this song]
$C8–$F8  effect (pitch program) number, minus $C8                       [1443; values $C9-$E9 = 1..33]
$80      gate off      $81  legato/no instrument change                 [639 gate-offs]
$82–$B7  instrument number, minus $82                                   [1672; values $83-$90 = 1..14]
$B8–$C7  delay: this voice holds for (16 − (b & $0F)) rows              [930]
$00–$7F  note: pitch = b >> 1 (0..63 semitones), duration = 1 row if bit 0 else 2   [2459]
```
The ranges are forced by the read order, not by a tag: `prepare1` eats $C8–$F8 first, so a delay token must live *below* $C8 — which is exactly why the delay band is the sixteen values $B8–$C7. A re-implementation of this grammar consumes all 2782 + 3114 + 1250 buffer bytes of the three voices exactly, in 3389 rows.

**Instrument** (1..14 here; the editor allows 48): five parallel 1-based columns — `ins_ad`, `ins_sr`, `ins_wave` (offset into `wavetable`), `ins_filt` (offset into `filttable`, 0 = do not touch the filter). Two thresholds partition the numbering: instruments ≥ `INS_RESTART2`+1 get `$D406 = $0F` at note-on and instruments ≥ `INS_RESTART`+1 get the two-frames-early `SR = 0` plus the `AD = 0, ctrl = $01` gate-on. Both constants are 1 in this build, so instrument 1 alone is exempt. The exporter *sorts instruments* so that a compare against a constant replaces a per-instrument flag.

**Effect = the pitch program** (`fxtable` $155D, 33 programs): a byte array. `fxtable[y]` is a pitch offset in **quarter semitones** ($40 = the unison default, so ±1 is ±¼ semitone and ±4 is ±1 semitone); 0 means "fixed frequency" ($D400/1 ← $FFFF). `fxtable[y+1]` — the *next* entry — is tested for bit 7 and, when negative, is the signed jump-back added to the cursor (`$FF` = hold this row, `$FD` = a two-entry loop, `$F7` = an eight-entry loop). Examples from this tune: `41 42 42 41 40 3F 3E 3E 3F F7` = a quarter-tone vibrato looping its last eight steps; `52 43 38 40 FF` = a downward attack blip; `42 33 18 10 FF` = a drum sweep; `70 48 38 FD` (×25 of the 33) = a three-step arpeggio whose last two entries loop, i.e. a chord.

**Wave program** (`wavetable` $15EC, 72 bytes): a byte stream of SID control bytes. A byte < $C0 is written as `byte & v_wavemask` to $D404 and the cursor advances by 1 — unless bit 6 (pulse) is set, in which case the next byte is a pulse parameter and the cursor advances by 2. A byte ≥ $C0 is a relative backward jump (`cursor += byte + 1`, reach −64…−1) and is not itself a control byte. Pulse parameter: bit 7 set → `pwidth = byte<<1` (absolute), bit 7 clear → `pwidth += byte` (a sweep); `pwprepare[pwidth]` is then written to **both** $D402 and $D403.

**Filter program** (`filttable` $1559): 3-byte rows `[$D418, $D417, cutoff]` with the *next row's first byte* doubling as the terminator — bit 7 set means "jump back by that signed amount" (`$FF` = hold). The cutoff byte's bit 7 selects absolute (`m_cutoff = c<<1`) or relative (`m_cutoff += c` with an overflow clamp that skips the write). Quintessence has exactly one row, `1F 00 80 FF`: volume 15 + low-pass, no resonance or routing, cutoff 0, held for ever — so $D416/$D417/$D418 are rewritten with the same values every frame.

**Frequency table**: one 207-byte array. `freq(k) = mem[$1331+k]<<8 | mem[$1391+k]`; the two arrays overlap by 15 bytes because eight octaves is a factor of 256. Quarter-semitone pitches are made without a multiply or a second table by summing two entries at fixed offsets — measured error ≤ 0.10 % (≤ 0.017 semitone) at every index the tune reaches.

#### 3.9.5 SID write schedule

Every call, `everyframe` writes, per voice in the order 3, 2, 1: `$D400, $D401` (frequency), `$D404` (control byte ANDed with the gate mask), and — only when the current wave byte has bit 6 set — `$D402, $D403` (the same byte to both). Then `$D418, $D417, $D416`. That is 12 writes when no voice is on a pulse waveform and 18 when all three are, plus the note-on writes on a row frame (measured 15.16 mean, 27 max); the totals over the song are $D400/$D401/$D407/$D408/$D40E/$D40F/$D416/$D417/$D418 = 10426 each (every frame), $D404/$D40B/$D412 = 11157/11283/10939, $D402/$D403 = 1700, $D405 = 1462, $D406 = 2193. `$D415` is never written by anything.

AD and SR are written only at note-on and at the hard restart. There are no shadow registers: every value is computed and stored in the same instruction sequence, and the only per-voice image cells are the pulse accumulator and the two program cursors.

Hard restart, as an emergent property of the four-frame pipeline: `prepare2` runs two frames before the note and writes `$D406 = 0` and `v_wavemask = $FE`, so the two intervening `everyframe` calls write the control byte with the gate cleared and the envelope in its fastest release; `execute` then writes `$D406 = $0F`, `$D405 = $00`, `$D404 = $01` (gate on with a zero waveform and ADSR 0000) and immediately the instrument's AD and SR. The author's comments name the two mechanisms `Hard-restart 1` (gate enabled with ADSR = 0000) and `Hard-restart 2` (switch to rate 0 with the counter past 32) and state that both avoid the decay-rate bug.

#### 3.9.6 Techniques specific to this player

| technique | citation | why |
|---|---|---|
| **Real-time LZ decompression inside the play call.** The score is one compressed stream; the player expands one token per call into three 256-byte ring buffers and the sequencer reads the buffers | $1272–$12E3, buffers $2300–$25FF, stream $168A–$221A | 2961 bytes of file become 7579 bytes of score; the cost is bounded because exactly one token (≤ 10 bytes) is expanded per call |
| **Copy-with-transpose LZ primitive**: a match adds a constant to the copied bytes, but only to those with bit 7 clear | `$12D5 BMI $12DA` skips `$12D7 CLC; ADC #imm` ($12D9 = `m_transp`) | a repeated phrase at another pitch is still a match; the sign bit separates notes from instrument/effect/delay tokens, so one test does the type check |
| **The compressed stream is consumed downwards** (`SBC #$01` / `SBC #$02` on the pointer, literals copied in reverse with `DEY`) | $12BA, $12C4–$12CB, $12A2–$12A9 | the literal copy loop ends on `DEY; BNE` with no separate counter |
| **`(zp,X)` as the per-voice stream pointer** — three 16-bit pointers at $E0/$E7/$EE, selected by the same X that indexes the SID | `LDA ($E0,X)` at $101D, $1027, $1048, $1086; `INC $E0,X` | the rarest 6502 addressing mode used for exactly what it is for: a table of pointers indexed by a register; no ZP pointer reload per voice |
| **Globals live in the unused slots of the stride-7 zero-page array** ($E2–$E6, $E9, $EA sit between the three pointer pairs) | $E0–$EF | 16 bytes of zero page hold all of the player's pointers and phase |
| **The row timer doubles as the phase selector and as the unpack voice index**: `$E6` counts 28, 21, 14, 7, 0 and its value *is* 0/7/14 = the voice whose buffer is topped up | $1003–$1011, `LDX $E6` at $12E4 | one byte drives the tempo, the four-phase pipeline and the round-robin unpacker |
| **Four-phase row pipeline dispatched by one patched `JMP` low byte** (`prepare1 → prepare2 → prepare3 → execute → prepare1`) | operand $12EC written at $103D, $1071, $1264 | spreads a row's decode over four frames, which *is* the hard restart: `prepare2` is two frames before the note |
| **Opcode patched from `JMP` to `RTS` so a tail dispatch becomes a callable subroutine**, and init then calls play twice to prime the ring buffers | $12EB ← `#$60` at $1673, restored `#$4C` at $1680; `JSR $1009`, `JSR $1003` | init and play share the whole unpacker with no second copy and no flag |
| **Entry into the middle of `play`** (`LDX #$07; JSR $1009` starts at the `STX $E6` inside the phase decode) | $1678–$167A | passes the phase in X without a parameter |
| **Quarter-semitone pitch from one table read twice at fixed offsets**: `freq = F[y+24]`, `F[y+19]+F[y+1]`, `F[y+12]+F[y+13]`, `F[y]+F[y+20]` for phases 0..3 | $10CD–$111D | 1/48-octave resolution with no multiply, no interpolation table and no second frequency table; error ≤ 0.10 % |
| **9-bit right-shift-by-2 with the carry supplying bit 8** (`ADC; ROR A; LSR A; TAY`), the two shifted-out bits selecting the quarter-tone case | $10C7–$10D0, $10FC | one add, two shifts and two branches yield both the semitone index and the fraction |
| **Frequency msb/lsb arrays overlapped by 15 bytes** because 8 octaves = ×256, so `msb[k+96] = lsb[k]` | $1331 / $1391, table ends exactly at $13FF | 207 bytes for 111 sixteen-bit entries |
| **`NOP #imm` ($80) as a two-byte skip over a one-byte instruction** — `ADC v_pwidth,X` falls through `80 0A` which eats the `ASL A` that the `BMI` path lands on | $1146 (both $1146 and $1147 execute) | absolute-vs-relative pulse in one code path, no branch over |
| **Pulse width as one byte through a linearising table**: `PW = (b&$0F)<<8 \| b` is non-linear, so an accumulator is mapped through a page-aligned 256-byte table that makes it a 12-bit triangle | $114C `LDA $1400,Y`, both $D402 and $D403 written | 8-bit PWM state, 12-bit output, arbitrary LFO shape, one `LDA abs,Y` |
| **The *next* table byte is the loop marker**: `fxtable[y+1]` and `filttable[y+3]` are tested for bit 7 and, if negative, added to the cursor | $10B0–$10C0, $1165–$1172 | no terminator row, no separate jump column; the cursor advance is `SEC; ADC` = +1 (or +3) plus the delta |
| **Relative backward jumps encoded as "byte ≥ $C0"** in the wave program (`cursor += byte + 1`, C from the compare) | $1124–$112E | a control byte and a jump share one byte range; the compare supplies the +1 |
| **Branch into the middle of a `CLC; SBC` pair** because the preceding `CMP` already left C = 0 | `$1021 BCC $102A` over `$1029 CLC` | one subtract serves both "after a command byte" and "no command byte" |
| **Carry carried across eight instructions** from `LSR A` ($108A, the note byte's bit 0) to `LDA #$FF; ROL A` ($109F) to become the 1-or-2-row duration | $108A–$10A1 | the duration flag costs zero bytes in the token |
| **`ORA #$F0` shared by the note path and the delay path** (it is the branch target of `BMI $10A2`) | $10A2 | one instruction clamps both a computed $FE/$FF and a data nibble into the timer's range |
| **`LAX zp; SBX #imm` as "load and add a constant to X"** (`LAX $E7; SBX #$F9` = X ← $E7 + 7) | $11E0, $11E7 | 4 bytes, 5 cycles, and A is loaded too |
| **`SBX #$07` as the voice-loop step**, the constant being the SID voice stride | $1037, $106B, $10AA, $1156, $1256 | X −= 7 with N set, no `SEC/SBC/TAX` |
| **Sticky "current" vs "pending" per-voice registers**: a bare note re-triggers `v_currins`/`v_currfx`; `execute` clears only the pending pair | $1077, $1099, $124D | the token stream carries an instrument only when it changes |
| **Instrument numbering partitioned by two compare constants** (`INS_RESTART`, `INS_RESTART2`) instead of per-instrument flags; the exporter sorts the table | `CMP #$02` at $105C, `CPY #$02` at $1218 and $1233 | a hard-restart flag costs no byte and no load |
| **Immediate operands as accumulators**: `m_cutoff` ($1189) is the filter's running cutoff, `m_groove` ($125F) is the swing `EOR` mask, `m_transp`/`m_copyend` ($12D9/$12E0) are the unpacker's loop parameters | $1188, $125E, $12D8, $12DF | the variable *is* the instruction |
| **Operand-address bytes as a pointer**: the buffer page is written into the high byte of three absolute stores/loads instead of a ZP indirection | `$12A6`, `$12D4`, `$12DC` patched from `LDA $E1,X` at $1272 | the unpack loop is `LDA abs,X` / `STA abs,Y` at 4–5 cycles |
| **A host-writable shift register as a demo sync channel**: an out-of-band command with bit 0 stalls the sequencer unless `LSR $ED` shifts out a 1 | $1198–$119F, `sync_error` $1195 | the music waits for the demo, not the other way round; the player still runs `everyframe` |
| **The player is emitted per tune**: conditional assembly (`REPEAT`), table sizes and the instrument thresholds vary; all 40 HVSC Blackbird tunes have different player bytes | byte-compare of the 40 load images | the "family" is a compiler output, not a fixed blob |

#### 3.9.7 What it reduces to

Blackbird is **a streaming decompressor with a three-voice table interpreter bolted to it**. Per voice the state is 12 bytes (two stride-7 blocks) plus a 2-byte zero-page cursor plus a 256-byte ring buffer; globally there are 8 zero-page bytes and four immediate cells. Every frame: for each voice step one pitch-program cursor and emit a 16-bit quarter-tone frequency, step one wave-program cursor and emit a control byte (and maybe a pulse width), then step the filter program and emit three global registers. Four frames out of five also expand one LZ token for one voice and run one of three tokenizer passes; the fifth applies the row (effect, instrument, ADSR, hard restart) and reloads the row timer with the swing `EOR`.

There is no orderlist, no pattern table, no note-length counter beyond a single per-voice row timer, no vibrato/portamento/arpeggio *mechanism* — arpeggios and vibrato are pitch programs, and the pitch program's quarter-semitone unit is what makes them expressive. There is also no tempo divider: the row timer counts in sevens so that the same byte selects the pipeline phase.

Statically decidable: everything. The output is a pure function of the call index — no volatile read anywhere, no SID-model probe (of the 40 HVSC Blackbird tunes exactly one, `Reminiscence` (2016), carries an `LDA $D41B` at all: its init pokes oscillator 3 and polls the read-back to patch one immediate, and even there play reads nothing), no residual workspace beyond three per-voice cells that init leaves at their assembled values.

Never executed by this tune, though statically reachable: the relative-cutoff path $1185–$118A (this song's only filter row sets the cutoff absolutely — the sibling export `To_Die_For_II`, whose player is byte-identical here, does execute it); the syncpoint stall $119D/$119F/$1195; `stopstream` $126A–$1271 (the song loops instead of ending); the `pendins == 0` fallback $1093/$1096; the constant-advance filter step $116C; two `DEC zp_inptr+1` page-crossing arms $11AD/$11C7; and the `ins_filt` store $122B (all 14 instruments leave the filter alone). Seventeen sites in all.

The player in ~20 lines:
```
each call:
  t = master
  if t == 0: execute()
  else:
     master = t-7
     if master < 21:                       ; the last three frames of the row
        unpack one LZ token into buf[master/7]      ; literal run, or copy n+3 with transpose
        prepare1|prepare2|prepare3         ; patched JMP low byte, one token class per frame
  for v in 3,2,1:
     y = fxpos[v]; fxpos[v] += 1 + (fx[y+1] < 0 ? fx[y+1] : 0)
     p = fx[y] + 4*note[v]; SID.freq = F[p>>2] or F[..]+F[..] by (p & 3)
     w = wave[wavepos[v]]; if w >= $C0: w = wave[wavepos[v] += w+1]
     SID.ctrl = w & wavemask[v]
     if w & $40: pw[v] = absolute-or-add(wave[+1]); SID.pw = pwprepare[pw[v]]; wavepos[v] += 2
     else: wavepos[v] += 1
  y = filtpos; filtpos += 1 + (filt[y+3] < 0 ? filt[y+3] : 2)
  SID.$D418,$D417 = filt[y],filt[y+1]; SID.$D416 = cutoff(filt[y+2])
```

#### 3.9.8 Decompiler notes

- **The score does not exist until the player builds it.** Static analysis of $168A–$221A yields nothing: it is an LZ stream. A decompiler must lift `unpackvoice` as a decompressor, run it, and only then apply the token grammar to the ring buffers. This is the first exemplar here where the sequencer's *input* is produced by the player itself; treat `unpackbufs` as a derived array, not as data in the file.
- **The transpose is type-directed.** The copy adds `m_transp` only when the copied byte has bit 7 clear. Model the buffer as a byte array with a tag, or the transposed repeats come out as garbage instrument numbers.
- **Recover the phase variable first, as always — but here it is also an index.** `$E6` is the row timer *and* the voice offset for `unpackvoice` *and* the source of `LDX $E6` at $12E4. Do not type it as a scalar counter.
- **Two stride-7 struct-of-arrays blocks plus a stride-7 zero page.** Collect every `abs,X` and `zp,X` operand executed with X ∈ {0,7,14}: the bases are the fields. The trap is that the zero-page array's "unused" fields are globals ($E2–$E6, $E9, $EA), so `$E0,X` is per-voice while `$E2` is not.
- **`(zp,X)` must be modelled.** `LDA ($E0,X)` with X = 0/7/14 reads through three different pointers; a lifter that folds `(zp,X)` to a single ZP location loses the voice dimension entirely.
- **`NOP #imm` at $1146 creates two overlapping instruction streams.** Both $1146 and $1147 are executed program counters. A linear disassembler desynchronises; a recursive one must keep both decodings and must know that opcode $80 is a two-byte no-op.
- **Every SMC cell is an ordinary variable except one opcode.** $12EC is a three-valued code pointer (enumerate the writers: `#$18/#$43/#$81`); $1189, $125F, $12D9, $12E0 are byte variables; $12A6, $12D4, $12DC are the high byte of one pointer variable broadcast into three instructions; $12EB's *opcode* is `JMP`/`RTS` and is only ever `RTS` during init — fold it by lifting the init calls as `unpack_and_prepare()` returning normally.
- **Flags cross long distances.** C from `LSR A` at $108A is live to $10A1; C from `CMP #$F9` at $101F is live to the `SBC` at $102A through a branch that skips the `CLC`; the frequency sums chain carry from the low byte's `ADC` to the high byte's without an intervening `CLC` *by design*. A model that resets C at basic-block boundaries produces wrong pitches.
- **Timing model**: one call per frame, five frames per row here, but `frames per row = zp_tempo/7 + 1` and `zp_tempo` is XORed with `m_groove` after every row — so the row length alternates between two values whenever the groove byte is non-zero. Verify at frame granularity; the song's total length falls out as `Σ (tempo/7 + 1)` and matches HVSC exactly.
- **Traps**: the delay band is $B8–$C7 only because `prepare1` claims $C8–$F8 first — the token classes are positional, not tagged; `freq_msb` and `freq_lsb` overlap by 15 bytes, so a naive "two 96-entry tables" model reads the wrong bytes above index 95; the pitch program's byte 0 means *maximum* frequency, not zero; `$D402` and `$D403` receive the same byte; instrument 0 means "no instrument" and every instrument table is read as `base−1,Y`; and the end-of-stream token is `$00`, which is also a perfectly ordinary note byte inside a literal run — only the control-byte position distinguishes them.

## 4. The same machine nine ways — comparison

Measured on the exemplars (main subtune, 1500 frames, PAL): mean/max instructions
and cycles per play call (defMON: per call, 8 calls/frame), then the structural choices.

| | Hubbard 1985 | Galway 1986 | Follin 1989 | JCH NP20(+digi) 1991 | GoatTracker 2.73 | SID Wizard 1.6/1.9 | defMON (Automatas) | Walker (Chameleon) 1990 | Blackbird (Quintessence) 2017 |
|---|---|---|---|---|---|---|---|---|---|
| player code | ≈1.3 KB (incl. sfx) | 3158 B | 2947 B | 2275 B (906 insns) | 1098 B (433 insns) | 1918–2400 B | 1919 B (811 insn sites) | 2695 B (1059 executed sites) | 747 B play + 86 B init (325 executed sites, 711 executed bytes); **emitted per tune** — all 40 HVSC Blackbird tunes differ |
| insns/frame mean · max | 264 · 375 | 195 · 994 | 128 · 711 | 439 · 678 | 312 · 472 | 413 · 549 | 230 (main) / 195 (sub) per call ≈ 1600/frame | 2 calls/frame: sub 314, tick 1169 per call ≈ 820/frame · max call 5677 | 193 · 327 |
| cycles/frame mean · max | 894 · 1336 | 665 · 3365 | 385 · 2196 | 1586 · 2394 | 1083 · 1632 | 1402 · 1832 | 711 / 612 per call ≈ 5000/frame | ≈ 2900/frame · max call 19081 (pre-load loops) | 623 · 1022 (guide states an 1134-cycle bound) |
| voice code | loop, X = voice 2..0 | 3 unrolled copies | 3 unrolled copies | loop, X = track 3..0 | loop, X = 0/7/14 (tail-call third) | 3 × `LDX #n; JSR DOTRACK` | 3 unrolled 49-byte blocks per phase + `SBX #$31` loop for the oscillator | indexed by X (0..2) in the engine, voice number in memory (`LDX $02B1; DEX`) in the sequencer; 4 modulator copies | one loop, X = 0/7/14, stepped `TXA; SBX #$07` at five sites |
| per-voice state | 12 B, stride-1 arrays | ≈100 B (S 29+24, D 39, ZP) | 27 ZP B + 6 SMC cells | ≈45 B, rows 3/4 | 35 B in five 7-field blocks | 34 B, stride-7 bunches | ≈24 B: 9 record bytes + 8 image immediates + cascade cells, all inside code | ≈13 sequencer + 24 engine bytes, stride-1 arrays; engine block never cleared | 12 B in two stride-7 blocks + 2 ZP bytes + a 256-byte ring buffer |
| voice → SID | Y from `00 07 0E` table | absolute per copy | absolute per copy | Y from `00 07 0E` | X itself | X itself | absolute per band | `CPX` chain → Y = 0/7/14; per-register `CPX` chains for ADSR/PW | X itself |
| zero page | $5D–$60 (2 pointers) | $F0–$FF | $21–$97 (all state) | $FB/$FC (+$FD/$FE NMI) | $FC/$FD | $02/$03 (or $FE/$FF) | $FB/$FC, $96 | $02, $41/$42, $FB/$FC, $FD/$FE | $E0–$EF: three `(zp,X)` pointer pairs at stride 7, globals in the unused slots |
| tempo | speed table/song; tick = speed+1 frames | song-loaded 17-entry duration table + raw frames | none: durations are frames | speed; step = speed+1 frames; funk from filter entry 0 | tempo per channel; row = tempo+1 frames; funktempo | tempo *program*; row = tempo frames; tick 0/1/2 phases | CIA 8×/frame; row = d+2 main ticks; sidTAB row = DL+1 calls | CIA 2×/frame; tick = 9 calls | 1 call/frame; row timer counts down by 7; row = tempo/7 + 1 frames (5 here); swing `EOR` |
| score structure | song→3 tracks (pattern nrs, $FF/$FE)→patterns (len/flags[,instr\|porta],pitch) | 3 sequences: `note dur` + 15 commands (call/ret/for/next/jmp/transpose/poke/load) | 3 byte streams: notes + 21 commands (call/ret/loop/jmp/pokes/effect setters) | 4 tracks ([$80\|T] pat, $FF/$FE) → patterns (dur/instr/super/note/rest/hold/end) | orderlists (pat, $D0 repeat, $E0 trans, $FF loop) → patterns (instr, fx, note/rest/keyoff/on, packed rest, 0 end) | orderlists (pat, transpose, volume, tempo, $FE/$FF) → patterns (1–4 byte rows, bit-7 continuation, packed rest, $FF+len) | arranger (3 pattern nrs/row, `$FF` jump) → patterns of `flag [A] [B] [note]` rows | song (block list) → block (16-byte header + 3 tracks of keyboard characters) | one LZ stream (copy-with-transpose) → three 256-byte ring buffers → rows `[oob][fx][ins] note\|delay` by byte range |
| instrument | 8-byte SID image + fx bits | 29-byte record → 39-byte working record | none: commands latch state ($85 raw pokes) | 8 bytes + wave/pulse/filter table pointers | 9 columns + wave/pulse/filter/speed tables | 16-byte header + inline WF/PW/filter tables | none: sidTAB row programs (2 per step) | 30 bytes + 4 modulator param sets; 7-byte drums | 4 parallel 1-based columns (AD, SR, wave-program offset, filter-program offset) |
| modulation engines | vibrato, pulse, porta, drum, skydive, arp (bit flags) | FM (4-seg 16-bit ramp or 8-step arp), PM (2-seg), gate/release timers | vibrato/slide, trill, porta (index space), pulse bounce, blip, filter bounce | wave/arp table, pulse & filter 4-byte programs, slide, growing vibrato | wave/pulse/filter/speed tables, 5 continuous effects | WF/PW/filter tables (same machine), chords, 4 vibrato types, slide/porta, keyboard tracking | sidTAB rows @ up to 400 Hz, slide acc, pulse bounce, filter acc ± opcode-signed step | 4 × triangle/one-shot LFO template (rate = 100−N calls), gate tremolo, filter LFO | pitch program in quarter semitones, wave program (ctrl byte + pulse step through a 256-byte linearising LUT), global 3-byte-row filter program |
| dispatch | bit tests + compare | patched JMP ← word tables (index = command byte) | patched JMP ← lo/hi tables (index = command byte) | compare chain | patched JSR/JMP low byte ← tables | patched `BCC` offset ×2, patched `JMP` ← word table | flag-bit tests (`ASL`/`BPL`/`BIT`), presence-bit records | linear table search of the character; `CMP #$FF`/`BEQ` chains | one patched `JMP` low byte cycling `prepare1/2/3`; token classes by `CMP` ranges |
| SMC in play | 1 opcode cell (DEC/INC) | 3 JMP operands (+2 init-time bytes) | 3 JMP operands + 21 immediate cells | none (NMI: 6 cells) | 11 immediates + 4 jump low bytes | ≈27 immediates + 3 dispatch operands (+30–36 init relocation) | 4 opcode cells + ≈85 operand cells (state = code) | none | 7 cells: 1 `JMP` operand, 3 operand-address bytes (one broadcast pointer), 3 immediates (+2 init-time: the `JMP`→`RTS` opcode, the cutoff accumulator) |
| SID writes/frame | on change; freq/ctrl per effect | on change; filter/vol always | on change; filter always | 8 per voice + filter, all from shadows | 25 (ghost image flush) | 19 unconditional (5/voice + 4) | 25 per call from immediates (200/frame) | freq+PW per voice per call, cutoff per call; ADSR at note-on | 15.16 mean: freq + ctrl per voice per frame (+PW when the wave byte has bit 6), $D416/17/18 always; AD/SR only at note-on |
| hard restart | none (SR=0 cut at note end) | none (TEST pulse at note-on) | none | N−2 gate off + $0F00, N: `$09` | gatetimer frames early: AD $0F SR $00 gate off; tick 0 firstwave (test) | tick 0/1 HR ADSR + gate off; tick 2 test-bit wave | data (row program) | none (gate off/on in one call) | emergent: SR=0 + gate mask $FE two frames early, then ADSR=0000 with gate on, then the instrument's AD/SR |
| filter | none | shadows only (unused) | global cutoff bounce | 4-byte program, one owner voice, $D416 only | global program, ghost | 3-byte program, owner voice, 11-bit fixed point | 16-bit accumulator ± step + per-row offset, clamped; model-scaled | per-block owner voice's instrument + triangle LFO on cutoff | one global 3-byte-row program; cutoff absolute or a signed accumulator in an immediate operand |
| interrupt use | none (PSID play) | none | none | raster IRQ wrapper + CIA2 NMI sample player | none | none (multispeed via extra entry) | CIA1 timer 8×/frame (PSID speed bit) | CIA1 timer 2×/frame (PSID speed bit) | none (PSID play, 1×) |
| illegal opcodes / JMP (ind) / RTS trick | – / – / – | – / `Code` cmd only / `Code` cmd | – / – / – | – / – / – | – / – / – | – / – / – | SBX SAX LAX ANC ALR / – / – | – / – / – | SBX LAX `NOP #imm` / – / – |
| volatile reads | none | none | none | none | none | none | init: `$D012` wait, `$D41B` model test | `$D41B` when a modulator period = $FF (data sink) | none — no instruction, executed or statically reachable, names a VIC/CIA/$D41B address (checked for all 39 traceable HVSC Blackbird tunes) |

Observations that hold across all nine:

1. **State per voice is small (12–45 bytes, ~100 with Galway's working record) and flat.** No player uses a linked structure, a heap, or recursion. The whole state of a tune at any frame is those bytes plus a handful of globals — which is what makes frame-level replay verification tractable. Blackbird is the only one that also carries a *buffer*: 256 bytes per voice of decompressed score, which is state a verifier must reproduce byte for byte.
2. **The frame is a fixed pipeline**: sequencer decision (rarely) then modulation (always) then output. Two players (JCH, GoatTracker) pipeline the sequencer 2 frames early to implement hard restart; SID Wizard splits it into three ticks; Blackbird splits it into four frames, one token *class* per frame, so the hard restart falls out of the pipeline for free. In every case the "phase" is a small counter compared with constants — the decompiler should recover the phase variable first, because every other branch is conditioned on it. Blackbird's phase counter is also an index (the voice whose buffer is refilled).
3. **Effects are one-step-per-frame machines with 1–3 bytes of state**: a counter, a direction/phase, an accumulator. Table-driven variants add a cursor into a byte table with `set / step-for-N / jump / hold` rows; six of the nine (GoatTracker, JCH, SID Wizard, defMON, Blackbird, and Galway's segment records) use that same row vocabulary; Walker's four LFOs are the parameter-record form of the same idea. Blackbird's variant puts the jump marker in the *next* byte, so a program needs no terminator row.
4. **Dispatch is by patched jump when the command set is large (Galway 15, Follin 21, GoatTracker 16+5, SID Wizard 31+14+8) and by compare chain when it is small (Hubbard, JCH ~6 classes, Blackbird 5 token classes).** All patched-jump sites read a constant table, so the target set is statically known — Blackbird's single patched `JMP` has three writers and therefore three targets.
5. **SMC is storage, not code generation.** 100 % of play-time SMC in these nine is: an operand byte used as a variable, an operand *address* byte used as a pointer, a jump operand used as a switch, or an opcode used as a 1-bit variable. Init-time SMC is relocation, or — in Blackbird — one `JMP`→`RTS` patch that makes the play routine's tail dispatch callable so init can prime the buffers through it. There is no run-time code synthesis.
6. **Almost nothing is volatile.** No exemplar reads $D011/$D012 or a timer inside play; the sample-playing JCH build reads shared RAM from an NMI but the SID-writing path is deterministic; Walker reads $D41B only into an additive modulation offset (a data sink, never a branch condition). A tune's SID output is a function of (subtune, call index) — plus, for defMON, the SID model detected once at init, and for Walker, the pinned $D41B stream. Blackbird is the strictest case: not one instruction of its play code, executed or statically reachable, names a VIC, CIA or read-only SID register in any of the 39 traceable HVSC Blackbird tunes — but it exposes a *deliberate* external input instead, a zero-page shift register ($ED) the host writes to release syncpoints, which stalls the sequencer (never exercised by this tune).
7. **The unrolled players are the oldest and the newest** (Galway, Follin; defMON): they trade ROM for the freedom to use every register, and are the ones whose "voice struct" must be recovered by *diffing three code copies*. The indexed players (Hubbard, JCH, GT, SW, Blackbird) expose the struct directly through `,X`. defMON is the extreme: it unrolls *and* indexes, by making every per-voice block exactly $31 bytes so `abs,X` reaches cells inside code. Blackbird is the opposite extreme: nothing is unrolled at all, because X = 0/7/14 indexes the state arrays, the zero-page pointer pairs (`(zp,X)`) and the SID with one register.
8. **Multispeed changes cadence, not structure.** defMON runs its write-out, filter, table programs and oscillator 8× per frame and its sequencer 1×, using one entry point and an opcode cell as the gate; Walker runs everything 2× per frame with a sequencer tick every 9 calls (4.5 frames — note events alternate between the two calls of a frame); JCH's sample channel and SID Wizard's `MULPLY` entry are the other forms. The decompiler's frame model becomes a call model with a cadence variable. Blackbird reaches the same effect at single speed by *dividing the row* rather than multiplying the frame: five frames per row, each running a different quarter of the sequencer plus the whole audio engine.
9. **Initial state can come from the file.** Galway's workspace and Walker's engine block are not cleared by init; both tunes' first calls depend on bytes the author's save left in the image (Walker's residual `$FF` period is what triggers its only `$D41B` reads). Blackbird is the tidy version of the same hazard: init writes four of its seven per-voice cells and the other three (`v_pwidth`, `v_pendnote`, `v_wavemask`) keep the assembler's `.byt` values. A decompiler must initialise its model from the loaded image, never from zeros.

## 5. 6502 techniques catalogue, with the reasons behind them

Every technique below is used by at least one of the nine players; the citations
are exemplar addresses from §3. They are grouped by the resource being saved,
because that is how the authors chose them. A decompiler needs each one twice:
to recognise it, and to know what it *means* (the "model" column).

### 5.1 Addressing per-voice state

| technique | seen in | model |
|---|---|---|
| **Struct-of-arrays, stride 1**: field arrays `f0[3] f1[3] …`, X = voice 0..2, `LDA f0,X` | Hubbard ($54EC,X…), Follin (ZP $21+n, words at base+2n), JCH (rows 3 or 4 wide, X = track) | `voice[X].f0`; recover fields as the set of `,X` bases reached with X ∈ {0,1,2} |
| **Stride 7 = SID stride**: X = voice×7 so that `state,X` and `$D400,X` share the index; state blocks are 7-field records ×3 | GoatTracker (blocks $1461/$1476/…, ghost image $14CA,X), SID Wizard (5 bunches × 3 × 7 at $1024; constants `1,2,4` / `$FE,$FD,$FB` / `2,4,6` also stride-7 so a voice's mask is `LDA tbl,X`), Blackbird (two blocks at $12EE and $1303, **and** the zero page at $E0) | `voice[X/7].field(k)` where k = base offset mod 7; the ghost block is the SID image |
| **Voice → SID-offset table**: `LDY tbl,X` with tbl = 00 07 0E, then `STA $D400,Y` | Hubbard ($54E8), JCH ($4B05), GoatTracker (`X` itself), Galway ($8D86 = offset+2 for the `$D3FE,X`/`$D410,X` idiom) | `sid[voice].reg` |
| Voice → SID offset by `CPX` chain (`CPX #0 → LDY #0; CPX #1 → LDY #7; …`) and per-register `CPX` chains selecting `$D405/$D40C/$D413` by absolute stores | Walker ($A08D; $A126–$A1C3) | the same `sid[voice].reg` — 18 absolute stores that are one indexed store |
| Unrolled by *modulator*, indexed by voice: one LFO template copied four times (pitch, pulse, pitch-2, filter) with different bases | Walker ($A60C/$A692/$A718/$A7B1) | one function at four base tuples |
| Voice number kept in memory (`$02B1` = 1..3) and re-derived at every routine head (`LDX $02B1; DEX`) | Walker | `voice+1` variable; routines have no register contract |
| **Full unrolling**: three copies of the voice code with different absolute operands; no voice register at all | Follin (3 × 493 B + 3 × handlers), Galway (3 × ~800 B) | diff the copies; operands that differ by the record stride are fields; fold into `voice(v)` |
| **Offset tables for shared helpers** into unrolled records: `LDX voice; LDA off,X; …,Y` | Galway ($8D83/$8D86/$8D89) | index arithmetic on the record base |
| **Instrument = SID register image**: the record's bytes are copied straight to consecutive registers | Hubbard (8-byte instrument, 14-byte sfx image), Galway (S record $16..$1A → $D402..$D406, 31-byte effect blocks), Follin (`$85` = literal (reg,val) list) | typed as `struct sid_voice`; the copy loop is a memcpy |
| **The SID image is code**: register values live as `LDX #/LDY #/LDA #/EOR #` operands in a straight-line store block; per-voice blocks are all $31 bytes so X = voice·$31 indexes cells *inside* code (write band, both cascade blocks, 9-byte records) | defMON ($1022–$10B4 write band; `$1023,X`, `$12CC,X` …) | struct-of-code: fields at (operand − block base) with stride $31; the write-out is 25 loads-of-variables + stores |
| **Live state inside the instrument record**: pulse width mutated in place | Hubbard ($5591/$5592 rewritten) | shared mutable data — aliasing between voices on the same instrument |
| **`(zp,X)` as the per-voice stream pointer**: three 16-bit pointers at stride 7 in zero page, dereferenced `LDA ($E0,X)` with the *same* X that indexes the state arrays and the SID | Blackbird ($101D, $1027, $1048, $1086; `INC $E0,X`) | `voice[X/7].cursor`; the rarest addressing mode used for exactly its purpose — a table of pointers indexed by a register, with no ZP reload per voice |
| **Globals in the unused slots of a stride-k per-voice array**: only fields 0 and 1 of the stride-7 zero page are per-voice; fields 2–6 of voice 0 and 2–4 of voice 1 are the player's globals | Blackbird ($E0/$E1 per voice; $E2–$E6, $E9, $EA global) | do not type a `zp,X` base as per-voice without checking which X values actually reach it |

### 5.2 Index-register economy

| technique | seen in | model |
|---|---|---|
| Borrow X for a second purpose and restore from memory (`STX tmp … TAX … LDX tmp`) | Hubbard ($5121/$5154, $529C/$52B0) | two live variables, one register; the memory cell is a spill slot |
| Y always reloaded from a per-voice cell before a SID write (`LDY $54EB`) | Hubbard | Y is dead across the reload; not a loop-carried variable |
| Stack as scratch when A/X/Y are all live (`PHA … PLA`; `PHA; AND #$F0; STA; PLA; AND #$0F; STA` to split a byte; `PHP/PLP` to carry a fraction overflow) | Hubbard pulse ($5272), GoatTracker ($10BC), JCH (six sites: $4477 …), SID Wizard ($15E8 filter fixed point) | temporaries |
| Register-held 16-bit accumulators (`TXA ADC lo TAX TYA ADC hi TAY`) across a whole loop | Galway FM/PM loops ($850E) | a 16-bit local held in X:Y |
| `INX/DEX` on the high byte after `BCC/BCS` for 16-bit ± with the value split A:X | Follin vibrato ($626C) | 16-bit add |
| Y as byte cursor of a `(zp),Y` stream, reset to 0 at each fetch, and `ptr += Y` at the end (`TYA CLC ADC zp STA zp BCC INC zp+1`) | Follin ($6356), Galway (`PC += A`), Hubbard keeps a stored index instead | stream cursor; the pointer bump is `ptr += consumed` |
| Illegal opcodes for register economy: `SAX abs` (M ← A&X: mask-and-store), `SBX #$31` (X ← (A&X)−$31, N/Z/C: loop step and test in one), `LAX zp` / `LAX (zp),Y` (A and X ← M), `ANC #$7F` (AND with C ← 0 = an implicit CLC before ADC), `ALR #$7F` (AND then LSR) | defMON ($11A9, $14C2/$154A, $1726/$1745, $147C, $1771), Blackbird (`SBX #$07` ×9 as the voice-loop step, `LAX zp` ×3, `LAX (zp),Y` at $1285) | decode with exact flag semantics; `SBX` is not `CMP` |
| **`LAX zp; SBX #imm` as "X ← memory + constant"** (`LAX $E7; SBX #$F9` = X ← [$E7] + 7) | Blackbird ($11E0, $11E7) | 4 bytes, 5 cycles, and A is loaded too |
| **`NOP #imm` ($80) as a two-byte skip over a one-byte instruction**: the operand byte *is* the instruction a branch lands on, so two decodings of the same bytes both execute | Blackbird ($1146 `80 0A` eats the `ASL A` at $1147; both are executed PCs) | absolute-vs-relative in one path; a linear disassembler desynchronises, a recursive one must keep both decodings |

### 5.3 Flags as data

| technique | seen in | model |
|---|---|---|
| `BIT m` decoding two option bits into N and V, then `BMI/BVS` | Hubbard ($5015 status, $50CF note byte, $53E3/$5408 sfx flags), Galway (`BIT` in effect FMC tests) | `if (m & $80)`, `if (m & $40)` |
| V flag from one `SBC` distinguishing "equal" from "equal with bit 7 set" (`SEC; SBC tbl,Y; BEQ new; BVC same`) | SID Wizard tempo program ($1255) | two compares in one |
| The tick number passed in A and used as a *bit mask* against a data byte (`AND (zp),Y`) | SID Wizard HARDRST (A=2 at tick 0, A=1 at tick 1) | argument doubles as selector |
| Bit 7 of a byte as "another field follows" (`BMI` continuation) | SID Wizard pattern rows | variable-length record |
| C from `CMP` consumed several instructions later by `BCS/BCC`, `ADC #0`, `SBC #imm` without `SEC` | GoatTracker (`CMP #$50 … BCS` 5 insns later, `CMP #$C0 … ADC #$00`, every `SBC #$10/#$D0/#$F0`), Hubbard (`CMP #3; BCS; SBC #3`), Galway (`CMP #$5E; ADC TR`), Hubbard's inherited C into `ADC` at $523D (a data-dependent +1 — the *bug/feature* case), Blackbird (C from `LSR A` at $108A is live to `ROL A` at $10A1, eight instructions and four stores later, where it becomes the note's 1-or-2-row duration) | C is a 1-bit value with a definition site; propagate it |
| **Branch *into the middle* of a two-instruction sequence because the flag it needs is already set**: `BCC` skips a `CLC` that the other path needs, so one `SBC` serves both | Blackbird (`$1021 BCC $102A` over `$1029 CLC`; `CMP #$F9` already left C = 0) | one subtract, two callers; a block-local flag model gets the fall-through path wrong |
| **9-bit shift with the carry supplying bit 8** (`ADC; ROR A; LSR A; TAY`), the two shifted-out bits used as a 2-bit selector | Blackbird ($10C7–$10D0, $10CD, $10FC) | `y = v >> 2`, `q = v & 3` on a value that does not fit in a byte |
| Known-flag branches as short jumps: `LDA #0; BEQ`, `BNE` after `INC` that cannot wrap, `LDA #imm; BNE` | GoatTracker ($1061, $12AB), Follin ($640E, $6268 with SMC making it conditional) | unconditional jump — unless the immediate is an SMC cell, then a real `if` |
| Sign bit as list terminator and as command/note discriminator (`BPL/BMI`) | Follin (`$85` lists, commands ≥ $80, SFX lists), Galway (commands ≥ $C0 via `CMP`), GoatTracker (`BMI` on the parameter selects channel/global tempo) | range test |
| ASL/LSR moving a tag bit into C and scaling in one op (`ASL A` on `$80|transpose`) | JCH ($41CD) | `(b & $7F) << 1` |
| Return value in A as OR of flags | Follin play returns `$7B|$7C|$7D` | function result |
| N/Z from `DEC` giving a three-way branch (`DEC c,X; BEQ tick0; BPL run; …reload`) | GoatTracker ($11A4) | `if (--c == 0) … else if (c > 0) … else …` |

### 5.4 Control flow without calls

| technique | seen in | model |
|---|---|---|
| API as a `JMP` table at the load address | Hubbard ($5000..$500F), GoatTracker, JCH ($1000/$1003 – here $4000/$4003), SID Wizard | entry points; offsets are the ABI |
| Tail `JMP` instead of `JSR/RTS`; shared exit blocks; every path of a voice ends at one label | Hubbard (`JMP $538F`), GoatTracker (`JMP $140F` single exit), JCH (`JMP $4742/$4783`, zero JSR in play), Follin (voices chained by `JMP`, handlers `JMP` back into the fetch loop) | procedure = dominator region; exits are the shared labels |
| Fall-through into the next routine as a tail call (`JSR f; LDX #14;` then `f:` itself) | GoatTracker (`mt_execchn`, `mt_initchn`), Hubbard's wrapper | last call inlined |
| Loop entered from the middle (`LDX #2 … JMP head`, `DEX; BMI end; JMP head` at the bottom) | Hubbard ($5052/$539F) | for-loop with header after init |
| `BIT abs` skip chains: several entry points each hiding a `LDY #k` inside a `BIT` operand | Galway ($80FD music starts, load14/load10) | N entry points into one routine with a parameter |
| Branch to a lone `RTS` (`BEQ rts`) | Galway ($84AF) | early return |
| Computed jump by **patching a `JMP` operand** from lo/hi tables indexed by the command byte | Follin ($6368: tables at base−$80 so X = $80..$94 needs no subtraction), Galway ($8323 etc., API $800C, index = even command byte), GoatTracker (low byte only, all handlers in one page: $1289/$1295 JSR, $131E JMP), SID Wizard (`JMP` operand ← 31-word BIGFXTABLE at $19CF), multi-player packs (`JMP $xx00` per subtune) | `switch(b)` over a constant table; the patched cell is a variable of enumerated type |
| Computed **branch**: `CLC; BCC *+2` whose relative operand is loaded from a table of 8-bit offsets | SID Wizard ($1951 note-FX, $1A13 small-FX) | `switch` over offsets; targets = site+2+offset (all within +127) |
| RTS trick (`LDA #hi PHA LDA #lo PHA JMP (zp)` → callee's RTS lands at lo+1) | Galway `Code` command ($846C) | call through pointer with an explicit continuation |
| Compare-chain dispatch, ordered by frequency | JCH ($42D6: `AND #$E0` then `AND #$F0`), Hubbard (bit tests), GoatTracker patterns (`CMP #$40/#$60/#$BD/#$C0`) | decision tree = switch on ranges |
| Entry into the middle of a routine to pass an argument in a register (`LDX #$07; JSR $1009` starts inside the phase decode) | Blackbird (init at $1678–$167A) | a second entry point, not a second routine |
| No `JMP (ind)` in eight of the nine (Galway's `Code` command only); Blackbird's play path goes further — no `JSR`, no `JMP (ind)`, and not one write to the stack page | — | — |

### 5.5 Self-modifying code as storage

| technique | seen in | model |
|---|---|---|
| Immediate operand as a global variable (`LDA #v`, `LDY #v`, `CMP #v`, `ORA #v` rewritten by `STA op+1`, even `INC op+1`) | GoatTracker (11 cells: init-pending, filter step/time/cutoff/ctrl/type, volume, effect number, vibrato compare, calculated-speed shifts), Follin (pulse mode, vibrato direction, trill phase, fixed length, filter direction/min/max, transpose-skip; ×3 voices), SID Wizard (≈27: volume, filter band/resonance/route/cutoff hi+lo/keyboard-track/owner voice/position/sweep count, temps) | named byte variable; the instruction reads it |
| **Init-time relocation loop**: a table of instruction addresses × (slot, addend) rewrites 30–36 operands as `blob[slot] + base + addend`, so a position-independent data blob costs nothing per access | SID Wizard SWP export ($10BA–$111C, DataPtr/PtrValu) | evaluate once; thereafter constant bases |
| Runtime base add on every pointer set (`LDA lo,Y; CLC; ADC base; STA zp; LDA hi,Y; ADC base+1; STA zp+1`) | SID Wizard (8 copies) | pointer = table[i] + base |
| Immediate cell latched by a command and *applied* at note-on (`STA cell_now; STA cell_at_note`) | Follin `$8D` (pulse mode), `$88` (filter direction) | two variables |
| Opcode as a 1-bit variable (`DEC` ↔ `INC`) | Hubbard $53DE | `x += dir` |
| **Opcode patched `JMP` ↔ `RTS` so a tail dispatch becomes a callable subroutine**, letting init drive the play routine's own machinery | Blackbird ($12EB ← `#$60` at $1673, restored `#$4C` at $1680, around two `JSR`s into play) | the patched site is one procedure with two exits: `goto next_phase` and `return` |
| Operand of a `STA` chosen from a table of addresses (computed store) | Follin ($6219 ← $622E,X) | indexed store through an address table |
| Register save into the operands of the restoring `LDA #/LDY #` (interrupt handler) | JCH NMI ($4168/$416A) | spill slots |
| Opcode as sub-frame gate: `LDA #flag` ↔ `RTS` patched around a `JSR` so one routine serves two cadences | defMON $10D8 (`$1006` patches, calls `$1022`, restores, continues at `$12BE`) | `writeout_and_filter()` shared by two procedures |
| Opcode as arithmetic sign (`ADC #`↔`SBC #`) and as configuration (`NOP`↔`ASL` chosen at init from a hardware read) | defMON $10B8/$10BF, $10D4 | 1-bit variables; the config one is a parameter |
| Pointer broadcast: one pointer patched into four `LDA abs[,Y]` operands at prepare time instead of a ZP indirect | defMON $1131–$1148 ×3, Blackbird (the ring-buffer page written into three absolute operand high bytes $12A6/$12D4/$12DC at $1272) | cached reads of one 16-bit variable |
| Pre-shifted copies of a flag byte (`f<<1`, `f<<2`, `f<<3`) stored into `LDA #` operands and tested with `BPL` | defMON $114F–$1158 | bit tests without `AND` |
| Constants of an interrupt handler patched from a descriptor (`CMP #end`, `LDA #loop`) | JCH ($413A/$4140/$4144/$4148) | variables with a mute-flag critical section |
| Multiply by SMC (`STA op; ASL; ADC #imm` = A×3) | GoatTracker `mt_init` when NUMSONGS>1 | constant folding |
| Patching *data tables* from the song (commands that overwrite an instrument's wave pointer or an envelope entry) | JCH ($4CE5/$4CE6, $4EC7), Hubbard pulse in instrument | the table is mutable state, not a constant |
| Init-time patching only (song pointers into operands, relocation) | Galway API `JMP` and effect starter `STA $D410,X` low bytes ($81F4/$81FF, then `LDX $81F4` reads the patched byte as an index) | fold after init; note the read-back |

### 5.6 Sequencer and table encodings

| technique | seen in | model |
|---|---|---|
| Sentinel bytes: $FF/$FE (loop/stop), $00 (end), $7F (end), $7E (hold) | all nine | grammar terminals |
| Variable-length note records with bit flags in the first byte | Hubbard (len + bits 5/6/7 + optional instr/porta + pitch) | tagged union |
| Byte ranges as token classes (`< $40` instr, `$40–$5F` fx, `$60–$BC` note, `$BD–$BF` specials, `≥ $C0` packed rest) | GoatTracker; JCH ($80–$9F dur, $A0–$BF instr, $C0+ super, $01–$7D note); Galway (`≥ $C0` even = commands, else note+dur); Follin (`≥ $80` command, else note) | tokenizer thresholds = the `CMP` immediates |
| Note codes chosen so the gate mask is `ORA #$F0` of the code ($BE keyoff → $FE, $BF keyon → $FF) | GoatTracker | `gate = b | $F0` |
| Packed rests / fixed-length mode / durations as table indices | GoatTracker (`$C0+n` = 256−n rows), Follin (`$84 n`), Galway (17-entry IDRT loaded *by the song*) | run-length |
| Eager terminator peek: after consuming a row, test the next byte for end so "position 0" means "advance the orderlist" | Hubbard ($5163), GoatTracker ($1406), JCH ($4270) | look-ahead |
| 1-based table pointers with 0 = none, read as `base-1,Y` | GoatTracker, Blackbird (all five instrument columns: `$14FF,Y`, `$150D,Y`, `$151B,Y`, `$1529,Y`, `$1537,Y`) | null pointer = 0; table base = operand + 1 |
| Parallel columns sharing one index (wave/note; time/speed; lo/hi) | GoatTracker, JCH (A/B wave columns; 4-byte pulse/filter records addressed `base+k,Y`), Follin note lo/hi, Galway freq Hi/Lo | array of structs addressed column-wise |
| Bytecode tables with `delay`, `set`, `jump`, `loop` rows | GoatTracker (wave: <$10 delay, $10–$DF wave+$10, $E0+ cmd, $FF jump; pulse/filter: ≥$80 set, <$80 count, $FF jump), JCH ($7E/$7F; `[init|$FF, Δ, dir·n, next]`), SID Wizard, Blackbird (wave program: <$C0 = a SID control byte, ≥$C0 = a relative backward jump) | interpreter over a tiny language |
| **The loop marker is the *next* row's first byte**, tested for bit 7 and, when negative, added to the cursor — so a program needs no terminator row and no jump column | Blackbird (`fxtable[y+1]` at $10B0–$10C0, `filttable[y+3]` at $1165–$1172; the advance is `SEC; ADC` = +1 or +3 plus the delta) | `cursor += stride + (next < 0 ? next : 0)`; the marker byte is skipped, never interpreted as data |
| Overloaded table entries (entry 0 = tempos + filter track; last speed byte = first orderlist byte) | JCH ($4C87), GoatTracker ($1875) | aliasing to be modelled explicitly |
| Pre-shifted / pre-decremented constants stored in data (tempo−1, waveform+$10, filter type ≫1) | GoatTracker | editor did the arithmetic |
| Frequency deltas computed from adjacent table entries (`ftab[n+1] − ftab[n]`) then shifted | Hubbard vibrato, GoatTracker calculated speed, JCH vibrato | semitone-scaled modulation |
| **Quarter-semitone pitch by summing two entries of one table at fixed offsets** — `F[y+24]`, `F[y+19]+F[y+1]`, `F[y+12]+F[y+13]`, `F[y]+F[y+20]` for the four fractional phases, carry chained from the low byte's `ADC` to the high byte's | Blackbird ($10CD–$111D) | 1/48-octave resolution with no multiply, no interpolation table and no second frequency table; measured error ≤ 0.10 % |
| **Frequency msb/lsb byte arrays deliberately overlapped**: eight octaves is ×256, so `msb[k+96] = lsb[k]` and one 207-byte array holds 111 sixteen-bit entries | Blackbird ($1331 msb, $1391 lsb, ending exactly at $13FF) | one array, two bases 96 apart — a "two 96-entry tables" model reads the wrong bytes above index 95 |
| **A page-aligned 256-byte table that linearises a packed register format**: `PW = (b&$0F)<<8 \| b` is non-linear, so an 8-bit accumulator is mapped through a LUT that makes it a 12-bit triangle, and the same byte is written to both $D402 and $D403 | Blackbird (`pwprepare` $1400, `LDA $1400,Y` at $114C) | 8-bit state, 12-bit output, arbitrary LFO shape, one indexed load |
| **The score is LZ-compressed and decompressed inside the play call** into a per-voice ring buffer; the match primitive is *copy with transpose*, applied only to bytes with bit 7 clear so it moves notes and leaves instrument/effect/delay tokens alone | Blackbird (unpacker $1272–$12E3, control byte `ttttt nnn`, buffers $2300–$25FF, stream $168A–$221A read downwards) | the sequencer's input is produced by the player; the file contains no pattern to disassemble. Model `unpack` first, then apply the grammar to the buffer |
| **Token classes disambiguated by read *order*, not by a tag**: the pass that runs first claims a byte range, so a later class must live below it | Blackbird (delay tokens occupy exactly $B8–$C7 because `prepare1` claims $C8–$F8 first) | the grammar is the sequence of passes, not a single switch |
| Exponential table as a multiplier (`EXPTABH[k + pitch]`, the freq-hi table re-used with an offset) for pitch-proportional vibrato/slide and keyboard tracking | SID Wizard | lookup replaces multiply |
| One zero-page pointer with two meanings (pattern during row read, instrument otherwise), re-pointed at each use | SID Wizard PLAYERZP | pointer type is phase-dependent |
| Fixed-point cutoff (11 bits + 3 fraction bits split by `AND #7` / `LSR×3`) | SID Wizard | q11.3 arithmetic |
| Portamento in note-index space (step the index, then look up) | Follin | integer slide |
| Data cells embedded between/inside code | Hubbard variables ($54E8–$5530), Follin handler-local cells, JCH header bytes ($4006–$4020) | data, regardless of neighbourhood |

### 5.7 Timing and the SID

| technique | seen in | model |
|---|---|---|
| Free-running frame counter's low bits as LFO phase (`AND #7 / EOR #7` triangle, `AND #1`) | Hubbard | `phase = frame & 7` |
| Rate as inverted countdown target (`countdown = 100; DEC; CMP rate` fires every 100−rate calls; 0 = off) | Walker | period encoding in one byte |
| One-shot bend by pre-loading an offset with −step×(period−1) through a repeated-add loop (up to 254 iterations) | Walker ($A8EF–$A938) | multiply by loop; a 19081-cycle worst call — a real jitter hazard at 2×/frame |
| Random modulation: `LDA $D41B` into an LFO offset when its period byte is `$FF` | Walker (4 sites, one per modulator copy) | volatile data sink |
| Score as keyboard characters decoded by linear search of two 25-byte key tables | Walker ($A000, tables $AFE7/$AFCE) | tokenizer = table membership; ~17 `CMP abs,X` per byte, irrelevant at 3 lookups per 4.5 frames |
| Countdown-to-negative timers, reload on underflow; store `dur−1` | all (Hubbard `DEC;BMI`, JCH `DEC;BPL`, GoatTracker, Follin `DEC;BNE`, Blackbird `INC v_trtimer,X; BMI` counting *up* from a negative row count) | `if (--t < 0) t = reload` |
| No tempo counter at all: durations are frame counts in the data | Follin | tick = frame |
| Ghost image + fixed flush loop at the start of play (25 writes, `DEX;BPL` from $D418 down) | GoatTracker | output(n) = image(n−1) |
| Everything rewritten from shadows every frame (AD/SR included) | JCH (8 writes per voice per frame) | image = f(shadows) |
| Immediate writes wherever computed, no shadow except frequency | Follin, Galway, Hubbard | last write per frame wins; keep gate edges |
| Hard restart by look-ahead: sequencer runs N frames early into staged variables | JCH (tick 2, staged $4B87–$4B9E), GoatTracker (`counter == gatetimer` row fetch) | pipeline of depth 2 |
| **Hard restart as a by-product of a four-frame row pipeline**: one token class is read per frame, so the pass that reads the instrument number is structurally two frames ahead of the note; it writes SR = 0 and sets the gate mask to $FE, and the note frame writes gate-on with ADSR = 0000 before the real AD/SR | Blackbird (`prepare2` $105C–$1069, `execute` $1218/$1237–$1240) | the restart offset is not a constant anywhere in the code — it is the pipeline depth |
| **Hard-restart eligibility partitioned by a compare against a constant instead of a per-instrument flag**, the exporter sorting the instrument table so the test is `CMP #k` | Blackbird (`CMP #$02` at $105C, `CPY #$02` at $1218 and $1233 — the author's `INS_RESTART`/`INS_RESTART2`) | an enum whose ordering carries a boolean |
| TEST-bit pulse at note-on ($08 then wave, or ctrl=$09 for one frame) | Galway (wave\|8 then wave), JCH (`$09` on the note frame), GoatTracker (firstwave with $08) | oscillator reset event |
| Voice arbitration by loop order and a flag refreshed per iteration | Hubbard (`music_allowed`, voice 3 first) | ordered side effects |
| Two-flag handshake with a concurrent interrupt instead of SEI | JCH ($4B0F/$4170) | concurrency; NMI reads shared cells |
| Init clears SID with TEST set then 0 (`$08` then `$00` to every register) | Follin (`LDY #$1C`, four writes past $D418) | reset sequence |
| Frame skipper by dither mask (`LSR mask; BNE; reload; BCC return`) — a 1-bit-per-frame pattern giving 1/1…1/4 rates | SID Wizard slowdown build | frame gating |
| Three-phase tick (row read / position advance / note start) so hard restart needs no look-ahead copy | SID Wizard | pipeline of depth 3 with one state set |
| **The row timer counts in units of the voice stride** so that the same byte is the tempo counter, the pipeline phase selector and the index of the voice whose buffer is refilled | Blackbird (`zp_master` $E6 = 28, 21, 14, 7, 0; `SBX #$07`; `LDX $E6` at $12E4) | one variable with three types — recover the roles per use site |
| **Swing by `EOR` on the row-length reload** (`master = tempo; tempo ^= groove`), the mask being an immediate operand | Blackbird ($125A–$1261, `m_groove` = $125F; 0 here, so the rows are even) | a two-element cycle of row lengths, one byte of data |
| **A host-writable shift register as an external sync channel**: a score command stalls the sequencer (but not the audio engine) until the host shifts a 1 into a zero-page byte | Blackbird (`LSR $ED; BCC sync_error` at $119D, `sync_error` = `JMP everyframe`) | a non-hardware volatile input; the play routine is a function of (call index, host writes) |

## 6. What a decompiler must model, and how

### 6.1 Roots, reachability, and code/data boundaries

Roots are: PSID init and play; the entries of a JMP table at the load address
when the first bytes are `JMP`s (Hubbard $5000–$500F, GoatTracker `jmp init /
jmp play [/ jmp playsfx / jmp setvol]`, JCH $1000/$1003, SID Wizard $1000/$1003,
Blackbird `jmp initroutine` at load+0 with play falling through at load+3);
and, when play = 0, the interrupt handler discovered by executing init and
watching $0314/$0315, $FFFE/$FFFF, $DC0D/$DC0E, $D01A/$D012.

Static recursive descent from the roots (follow branches, `JSR`, `JMP abs`;
stop at `RTS/RTI/JMP`) recovers most code. It fails at: computed jumps
(`JMP (abs)`, `PHA/PHA/RTS`, opcode-patched jumps), and it happily walks into
data after a `JSR` that never returns or a branch that is never taken into a
data table. Two guards keep it honest: (a) do not disassemble an address that a
*data* access mode (`abs,X`/`abs,Y`/`(zp),Y`) reaches from already-known code —
tables are read, code is not; (b) prefer dynamic evidence: run init and some
thousand frames of play in an emulator, record every executed PC, every read
and write address per PC, and every instruction whose bytes changed. In all nine
players the executed set plus a bounded static walk from executed branch
targets is the complete play-time code; the residue is init-only paths and
unreached features (dead sub-features are common: Hubbard's skydive, GoatTracker
options the packer left in, JCH's 4th track, Blackbird's 17 sites — its relative
cutoff mode, its syncpoint stall and its end-of-stream handler). One caveat is
new with Blackbird: because the player is *emitted per tune*, a feature absent
from one export may not exist in its image at all, so "statically reachable" must
be read per tune, not per family.

Data is what code addresses through data modes; its type comes from the
addressing pattern that reaches it (§6.3). Two refinements matter in practice:

- **Disassemble the post-init image.** Init may relocate operands (SID Wizard's
  30–36 fixups), copy blocks (Follin's rip loader), patch a `JMP` per subtune
  (GoatTracker packs) or install a handler. Run init for each subtune first,
  then analyse the image play actually executes.
- **Data lives inside code regions.** Hubbard keeps its variables between two
  code blocks; Follin keeps handler-local cells and call stacks between
  handlers; JCH's live variables are the "header" bytes at $4006–$4020 next
  to the tune title; GoatTracker/SID Wizard have tables and patched
  immediates inside the code. "Executed" and "read/written as data" are the
  only reliable labels; contiguity is not.

### 6.2 Calling conventions to model

- `init(A = song)`: A is the only argument. Some wrappers dispatch on ranges of A
  (Hubbard: A<3 song, A≥3 sound effect). Init may relocate/copy the player, patch
  operand bytes with song-specific pointers, or only set a status byte that play
  decodes lazily.
- `play()`: no arguments; A/X/Y/flags clobbered; return by RTS. IRQ-installed
  players wrap play in `PHA/TXA/PHA/TYA/PHA … PLA/TAY/PLA/TAX/PLA/RTI` or end with
  `JMP $EA31/$EA81`.
- Internal subroutines pass arguments in A/X/Y *and in flags*: C as a boolean in
  (`BCS` after return), N/V from a `BIT` before the call, Z from a `LDA` at the
  end. Model the flags as ordinary 1-bit values that live across `JSR/RTS`.
- Tail calls: `JMP routine` instead of `JSR routine; RTS`; shared tails
  (several routines jump to one exit sequence); fall-through into the next
  routine (no `RTS` — control simply continues). Recover procedures by dominance
  over the executed CFG, not by `JSR` targets alone.
- Loop bodies are entered from the middle: `LDX #2` then `JMP loophead` and a
  `DEX; BPL loophead` at the bottom is a for-loop whose header sits after the
  init code.
- Interrupt handlers: a raster/CIA IRQ wrapper (`PHA TXA PHA TYA PHA … JSR play …
  PLA TAY PLA TAX PLA RTI`) is transparent; a *second* interrupt (JCH's CIA2 NMI
  sample mixer) is a concurrent routine sharing RAM cells with play through
  flags — model it as a separate procedure with the shared cells as volatile
  between the two, and its own SMC (register-save-into-immediates, patched
  end/loop constants).
- Return values exist: Follin's play returns A = OR of the voice-active flags;
  Galway's `MusicTest` returns a status. Treat A at RTS as a live out when a
  caller reads it.

### 6.3 Recovering state and tables (typing memory)

Classify each accessed address by the reaching definition of its index:

| access pattern | meaning |
|---|---|
| `abs,X` where X ∈ {0,1,2} is loaded from `LDX #n` / `DEX/INX` in the voice loop | per-voice field, struct-of-arrays, stride 1 (Hubbard $54EC,X…) |
| `abs,X` where X ∈ {0,7,14} or {0,1,2}*k with a table lookup `LDX table,Y` | per-voice field with stride k (many trackers), or SID voice base |
| `abs,Y` where Y = `[0,7,14][voice]` and abs ∈ $D400.. | SID voice register write |
| `abs,Y` where Y = value×2 (`ASL; TAY`) | table of 16-bit entries (frequency table) |
| `abs,X` where X = value×8 (`ASL ASL ASL`) or `×16` | record array with 8/16-byte records (instruments, sound effects) |
| `(zp),Y` with zp loaded from a lo/hi pair `lo,X`/`hi,X` or `lo,Y`/`hi,Y` | byte stream (pattern/track); the lo/hi tables are pointer tables indexed by pattern/track number |
| absolute address written once at init and read in play | global scalar |
| absolute address in the same page as executed code | still data — code and variables interleave freely (Hubbard keeps its variables between two code blocks) |
| an *instruction operand byte* that is written by a store | a variable that happens to be stored inside an instruction (SMC, §6.5) |

Per-voice struct recovery: collect all `base,X` sites in the voice loop with the
same X definition; the set of bases is the field list; verify by dynamic access
map (each base sees exactly addresses base..base+2). For stride-7 layouts the
field list is `base+i` for i in the record.

**Unrolled voices.** When there is no `,X` (Galway, Follin), diff the mnemonic
streams of the candidate copies: Follin's three 493-byte blocks are identical
except one 2-byte `CMP #v` insertion; Galway's three ~800-byte blocks differ
only in absolute operands. Operands that differ by a constant stride across
copies (Galway: $27 for D, $35 for S, 2 for ZP pointer, $1E for the vector
table, 7 for SID; Follin: 1 for byte cells, 2 for word cells) are fields of the
voice record; immediates that differ as 1/2/4 or 8/$10/$20 are per-voice bit
masks. The result is one `voice(v)` procedure with a record parameter.

**Stride-7 layouts** (GoatTracker, SID Wizard): collect all `,X` operands executed
with X ∈ {0,7,14}, sort, split into 7-byte-stride blocks; field = (operand −
block base) mod 7. The block that is also addressed as `$D400,X` is the ghost SID
image.

**Shadows and images.** Identify SID shadow variables as the RAM cells whose
value flows into a `STA $D4xx` unchanged (GoatTracker's 25-byte image, JCH's
freq/pulse/wave/AD/SR shadows, SID Wizard's FREQ/PW/WF ghosts, Follin's
freq shadow, Hubbard's savefreq). Model the frame as `image = f(state)`;
`SID = image` (immediately or one frame later, as the flush position dictates).

Table typing: element width from the index scaling; length from the maximum
index observed dynamically plus the static bound (a `CMP #n` on the index, or the
next table start); grammar of byte streams from the *consumer* (§6.4). Table
bases appear literally as operands — including `base−1` for 1-based tables
(GoatTracker) and `base−$80` for tables indexed by a byte ≥ $80 (Follin's
dispatch tables) — so recover the base as `operand + minimum observed index`.

### 6.4 Sequencer grammars and dispatch mechanisms

A note stream is decoded by one of four mechanisms; each leaves a fingerprint:

1. **Bit tests and compare chains** — `AND #mask; BEQ`, `BIT`, `CMP #imm; BCC/BEQ`
   in sequence. Grammar = a decision tree over the byte's ranges/bits. (Hubbard:
   bits 5/6/7 of the first byte + $FF/$FE sentinels; GoatTracker pattern bytes are
   ranges $00/$01–$3F instrument/$40–$4F effect/$60–$BC note/$BD–$BF rest·keyoff·keyon/$C0+ packed rest.)
2. **Jump tables** — `ASL; TAY; LDA tbl,Y; STA ptr; LDA tbl+1,Y; STA ptr+1; JMP (ptr)`
   or lo/hi tables + `PHA PHA RTS` (push hi then lo of target−1, then RTS).
   Grammar = an opcode byte selecting one of N handlers; the handler table is
   data typed "code addresses".
3. **Self-modified branch/jump** — the operand of a `JMP abs` or the offset of a
   branch is written at run time (GoatTracker's tick0 dispatch stores a handler's
   low byte into a `JSR`/`JMP` operand; Galway and Follin store both bytes from
   word/lo-hi tables; SID Wizard stores an 8-bit offset into a `BCC *+2` — a
   relative jump table; multi-player packs patch `JMP $xx00` in init).
4. **Table-driven state machines** — a byte is an index into a parameter table
   whose entries themselves say what to do next (wave tables with jump/loop
   entries; SID Wizard tempo programs).

Model each as a `switch` whose case labels are the byte ranges the tests
partition, and whose arms are the handlers; for mechanism 3, the patched
operand is a variable of type "code address" whose domain is the table's
contents — enumerate the table, not the writers. Handlers return to a fixed
continuation (an `RTS` to the fetch loop, or a `JMP fetch`), so the switch sits
inside `while (true) { b = fetch(); switch (b) … }` with `break` on "note read".

**Find the phase variable first.** Every player's top-level control is a small
counter compared with constants: Hubbard `speedctr == speed`, GoatTracker
`counter` (0 → tick 0, <0 → reload, == gatetimer → prefetch), JCH `tick ∈ {2,0,other}`,
SID Wizard `SPDCNT ∈ {0,1,2,other}`, Galway/Follin `--CLOCK/dur == 0`. Recovering it
turns the per-frame procedure into `switch(phase)` with three or four arms, and
every effect routine becomes unconditional inside its arm.

### 6.5 Self-modifying code, classified

Every SMC site in the nine players is one of these, and each has a clean model:

| form | example | model |
|---|---|---|
| operand byte = variable | `LDA #imm` whose imm is stored by another instruction (tempo, transpose, current instrument) | a byte variable; the instruction reads it |
| operand address = pointer | `LDA $xxxx,Y` whose $xxxx is patched with a table/pattern address | a 16-bit pointer variable; `LDA (ptr),Y` |
| opcode byte = boolean | Hubbard $53DE `DEC`↔`INC` selecting sweep direction; defMON `ADC`↔`SBC` (filter slide sign), `NOP`↔`ASL` (8580 cutoff scale, set once at init), `LDA #`↔`RTS` (sub-tick gate that makes one entry point serve two cadences) | a 1-bit variable, `x += dir ? +1 : −1`; `if (subtick) return` |
| branch offset / JMP operand = code address | dispatch by patching a jump (Galway, Follin, GoatTracker, SID Wizard `BCC` offsets) | a switch on a variable of enumerated type |
| operand of a `STA` = computed store target | Follin $6219 (which voice's fixed-length cell to clear) | indexed store through an address table |
| register save into `LDA #/LDY #` operands | JCH NMI | spill slots |
| data table patched by the song | JCH instrument wave pointers, envelope entry 0; Hubbard pulse in the instrument | mutable table |
| init-time relocation / copy | init copies the player or writes song-specific pointers into operands once | constant after init: fold it, disassemble the *post-init* image |
| play-time relocation | play copies a block over itself (rare; not in these nine) | treat the copied region as a separate program version |
| operand *address* byte = a pointer broadcast into several instructions | Blackbird ($12A6/$12D4/$12DC ← the ring-buffer page; defMON's four `LDA abs,Y` copies) | one 16-bit pointer variable, several cached reads |
| opcode = `JMP`/`RTS` making a tail dispatch callable | Blackbird $12EB, patched by init around two `JSR`s | one procedure with two exits |

The practical rule: disassemble the image *after* init has run (the "post-init
image"), record every write whose target lies inside an executed instruction, and
give each such operand cell a variable name. Opcode-cell patches are the only
ones needing per-variant lifting, and they have tiny observed domains (2 in
Hubbard and defMON's four cells). Counted over the exemplars: play-time
SMC = Hubbard 1 opcode cell; Galway 3 jump operands; Follin 3 jump operands + 21
immediates + 1 store operand; JCH 0 on the IRQ path (6 in the NMI); GoatTracker
11 immediates + 4 jump low bytes; SID Wizard ≈27 immediates + 3 dispatch
operands; defMON 4 opcode cells + ≈85 operand cells (the entire per-voice
state and SID image); Blackbird 7 cells (1 `JMP` operand with three writers,
3 operand-address bytes carrying one pointer, 3 immediates — the cutoff
accumulator, the swing mask and the unpacker's transpose/end parameters).
Init-time: SID Wizard 30–36 relocations, Galway 3,
defMON 2 (SID-model patch), Blackbird 2 (the `JMP`→`RTS` opcode at $12EB and the
cutoff accumulator $1189), JCH 0, others 0.

### 6.6 Values that are not functions of the program

Volatile inputs: $D012/$D011 (raster), $DC04–$DC0D (CIA timers/ICR), $D41B/$D41C
(SID oscillator/envelope 3 — used as random sources by some players), and the
song number passed to init. A player that reads none of them is a pure function
of (song, frame index); seven of the nine are (JCH's sample NMI reads RAM shared
with play, but the SID-writing path stays deterministic; Blackbird reads no
hardware at all, in any of the 39 traceable HVSC tunes of its family, but does
expose a *software* volatile — the host-written zero-page shift register `$ED`
that releases syncpoints, which gates the sequencer via `LSR $ED; BCC`; model it
as an input stream, not as memory the player owns). Walker's Chameleon
reads `$D41B` inside play — but only as an additive sink: a modulator whose
period parameter is `$FF` loads the oscillator-3 byte into its offset. No control
flow depends on it, so given a pinned `$D41B` stream the output is deterministic;
in the exemplar it fires 8 times in the first 8 calls, from residual engine state
the image carries, and never again. A byte scan of all 20 Walker tunes in HVSC
finds `$D41B` sites in 6 and **no `$D011/$D012` read in any** — the folklore that
Walker's player reads the raster is not borne out. defMON is the exception in a
way that matters: init busy-waits on `$D012` (semantically a
no-op) and reads `$D41B` once after poking oscillator 3 — the standard SID-model
test — and patches two cells (`CMP #2/#0`, `NOP/ASL`) that scale the filter for
6581 vs 8580. Its output is a function of (start row, call index, SID model): a
decompiler must carry the model as a parameter or pin it.

### 6.7 What to verify against

The observable behaviour of a player is its sequence of SID writes.
Every exemplar was checked this way (per-frame `(register, value, writing PC)`
logs), which is also how the timing claims in §3 — hard-restart offsets, ghost
latency, tick phases, the carry-jitter in Hubbard's pulse — were established. Verify a
decompilation at *frame granularity*: for each play call, the ordered list of
(register, value) writes — or, if the decompiler drops order, the final value
per register plus the multiset of gate/TEST edges. Cycle-exact position within
the frame matters only for raster-split or multispeed tunes that read $D012 or
race the CIA. A model that reproduces the per-frame write log for the full song
length of every subtune has captured the program.

## 7. Traps

Concrete things that broke, or would break, a naive decompiler on these nine
players. Each was observed in the exemplar named.

**Flags**
- Carry inherited into `ADC` with no `CLC` (Hubbard $523D): a data-dependent +1
  that depends on a `CMP` ~40 instructions earlier along one path. Model C as a
  value.
- `SBC #imm` after `CMP` with no `SEC` (GoatTracker, Hubbard's wrapper): correct
  only because C is known set — the same modelling.
- `BEQ` on Z left by a table load two instructions back (GoatTracker
  `mt_effect_0`): with parameter 0 it tests the byte *before* the table.
- `LDA #0; BEQ` / `LDY #v; BEQ` "unconditional" branches become conditional the
  moment the immediate is an SMC cell (Follin). Decide by "does anything write
  the operand", not by the opcode.

**Addressing and tables**
- Table overruns tolerated by the data: Hubbard's vibrato reads entry note+1
  (past the table at note 95); Follin's note index + transpose can exceed the
  97-entry table; GoatTracker's `$1875` is both the last speed byte and the
  first orderlist byte; JCH's filter entry 0 is also the funk tempos and the
  filter-track selector; SID Wizard's empty chord table aliases the tempo table.
- 1-based tables read as `base−1,Y` (GoatTracker) and dispatch tables placed at
  `base−$80` (Follin): the literal operand is not the table start.
- `LDY abs,X` in the binary where the source says `,y` (GoatTracker): a
  source/binary mismatch — trust the bytes.
- Reads of `$05A0/$0BA0/$11A0/…` that are `BIT` operand fetches in a skip
  chain, not data (Galway).
- The `.sid` image carries *residual* workspace (Galway's S/D/IDRT from the
  author's save): the player never zeroes it and one command (`DMoke VRC`)
  can switch the engine on over it. Initialise the model from the image, not
  from zeros.

**Control flow**
- Fall-through and shared tails: `JSR f; JSR f; <f:>` (GoatTracker), routines
  that end by jumping into the middle of another (Follin handlers, SID Wizard
  `CNTPLY2`, Galway `PC += A`), single exits (`JMP $140F`), a play routine whose
  RTS is the callee's.
- The wrapper's `STX $5528` (Hubbard) stores whatever X the host passed — a
  harmless junk store a decompiler must not turn into a live definition.
- A play call that does nothing (SID Wizard slowdown build: frame 0 returns
  before playing); an init that only schedules (GoatTracker `STA;RTS`).
- Wrappers: RSID-style `JMP *` after installing an IRQ (JCH), a rip loader
  that copies song blocks over the region containing the loader's own
  dispatcher (Follin: init is not re-entrant), multi-player packs whose init
  patches a `JMP $xx00` per subtune (GoatTracker packs).

**SID**
- Same register written several times per frame with different values (Hubbard
  drum then arpeggio; SID Wizard $D416 three times; JCH AD/SR every frame);
  intermediate control-register values matter (gate/TEST edges), final values
  matter for everything else.
- Writes past $D418 (Follin's `LDY #$1C` clear) and to mirrors ($D498): harmless,
  but a decoder keyed on exact addresses must accept them.
- Writes whose register is *data* (Follin `$85`: `STA $D400,X` with X from the
  song, any register < $80): the register is a variable.
- The ghost image (GoatTracker) means every "SID write" in the code is a RAM
  write and the real writes are one loop; the JCH build writes $D418 only from
  its NMI. Do not equate "stores to $D4xx" with "the SID output" without the
  frame model.
- Hard-restart timing is an emergent property of the pipeline (row fetch N
  frames early, ghost latency, tick phases), not a constant in the code —
  verify it from a write log, not from reading the source.

**Multispeed and hardware dependence (defMON)**
- The frame is not the unit: sidTAB rows last DL+1 *calls* (8 per frame), the
  SID sees the image one call late, and a verifier at frame granularity misses
  every 400 Hz arpeggio. Model at call granularity with the wrapper's
  `cnt & 7` as the phase.
- Output depends on the SID model read once at init (`$D41B` after poking
  oscillator 3): two patched cells scale the filter. Pin or parameterise.
- `LDX #0` in the row-advance blocks makes X a *zero source* (`STX cell` = store
  0), while in the oscillator X = voice·$31: the same register has two roles in
  one routine — recover roles per region, not per routine.
- The detune add drops its carry; TR rows can index past the 156-entry
  frequency table; the pattern pointer is not advanced past an END row (the
  END row's duration nibble is the inter-pattern gap).

**Residual state and cadence (Walker)**
- The engine block ($AD01–$AD76) is never cleared by init; the first 8 calls
  emit garbage freq/PW/gate writes on all voices (inaudible: ADSR are 0) and the
  only `$D41B` reads of the tune come from a residual `$FF` period byte. Import
  the image; do not zero.
- Track positions count from 1 (byte 0 of every track is dead — which is why
  the block header is 16 bytes); instruments 5/6 point past the image (zeros);
  the song plays once and parks the state machine at 0.
- Two calls per frame with a 9-call tick: note events land on alternating
  half-frames; a tick call can cost 19081 cycles.

**A generated player and a compressed score (Blackbird)**
- **The player is emitted per tune.** All 40 HVSC Blackbird tunes carry a
  different player image: conditional assembly (the exporter's `REPEAT` flag
  changes the LZ offset from relative to absolute — `CLC; ADC (zp),Y` versus
  `LAX (zp),Y`), table sizes and the hard-restart thresholds all vary, and the
  routine addresses shift. Signature-matching names the family; it does not
  license reusing one tune's addresses or one tune's feature set on another.
- **The score is not in the file.** $168A–$221A is an LZ stream read *downwards*;
  the pattern data only exists in three 256-byte ring buffers the player fills at
  one token per call. A decompiler must lift and run the unpacker before any
  grammar applies, and must model the copy-with-transpose primitive's type test
  (the transpose is added only to bytes with bit 7 clear).
- **Token classes are positional.** The delay tokens occupy exactly $B8–$C7 only
  because `prepare1` claims $C8–$F8 one frame earlier; the same byte value means
  different things depending on which of the three passes reads it.
- Two overlapping instruction streams at $1146/$1147 (`NOP #imm` eating an
  `ASL A`); a carry live across eight instructions ($108A → $10A1); a `BCC` that
  branches over a `CLC` the other path needs ($1021 → $102A); frequency msb/lsb
  arrays that overlap by 15 bytes; `$D402` and `$D403` written the same byte; a
  pitch-program byte of 0 meaning *maximum* frequency; and an end-of-stream token
  of `$00`, which is also an ordinary note byte when it appears inside a literal
  run — only the control-byte position distinguishes them.

**Bugs in the players (present in shipped tunes)**
- SID Wizard 1.6: `LDA FREQTBH,X` on the first note frame indexes by voice
  offset instead of pitch (masked by the TEST bit; fixed in 1.9).
- Follin `$94` handler writes both operand bytes to the same cell (dead
  command); note $5E is transposed in one path and not in another (Galway).
- Hubbard skydive requires length ≥ 3 and the instrument that has it is only
  used with shorter notes: reachable code that never runs. Every exemplar has
  such dead features; do not conclude a feature is absent from the family
  because one tune never exercises it.

## 8. Quick reference

### 8.1 Where to look first in an unknown player

1. Load address bytes: `JMP init / JMP play [/ JMP …]` → API. Else PSID init/play.
2. Run init (all subtunes), then a few thousand play calls in an emulator with
   per-PC execution counts and per-PC read/write sets. Executed set = the code;
   read/write sets = the data; instructions whose bytes changed = SMC cells.
3. Find the phase variable: the first `DEC abs` / `DEC abs,X` in play whose result
   is compared with 0 and reloaded (Hubbard $5054, GoatTracker $11A4, JCH $4171,
   SID Wizard $124D) — or `DEC zp` per voice (Galway/Follin), or the first
   instruction of play when it is `LAX zp; BEQ; SBX #k` (Blackbird $1003).
4. Find the voice iterator: `LDX #2` / `LDX #$0E` + `DEX`, `TXA; SBX #$07`, or three
   `LDX #n; JSR`,
   or three near-identical code blocks. Every `,X` in its scope is a per-voice field.
5. Find the frequency table: 96 (or 95/97) 16-bit ascending values with ratio
   2^(1/12), read after `ASL; TAY` (interleaved) or as lo/hi pairs — check whether
   the lo/hi bases differ by exactly 96, in which case the two arrays overlap and
   there is really one (Blackbird).
6. Find the note fetch: the `(zp),Y` — or `(zp,X)` — load whose result is compared
   with $FF/$FE/$00
   or split by bit 7 — that is the sequencer; the compare constants are the grammar.
   If the bytes it reads are written by the player itself, the score is compressed
   and the sequencer's input is a buffer, not a file region (Blackbird).
7. Find the SID write sites (`STA $D4xx` / `STA $D400,X|Y` / ghost image + copy
   loop) and trace each value back to its shadow or table: that is the output map.
8. Classify SMC cells: written from where, read as what (immediate → variable;
   JMP/JSR/branch operand → switch; opcode → 1-bit variable; init-only → constant).

### 8.2 SID register cheat sheet (offsets within a 7-byte voice block)

`+0/+1` freq lo/hi · `+2/+3` pulse lo/hi(4b) · `+4` ctrl (gate 1, sync 2, ring 4,
test 8, tri $10, saw $20, pulse $40, noise $80) · `+5` AD · `+6` SR ·
`$D415/16` cutoff lo(3b)/hi · `$D417` res(hi nibble)|route(bits 0–2) · `$D418`
mode(hi nibble)|volume · `$D41B/$D41C` read-only osc3/env3.

### 8.3 Grammar constants seen (the `CMP` immediates that define token classes)

| player | pattern/sequence tokens |
|---|---|
| Hubbard | first byte: bits 5/6/7 flags, low 5 bits length; second byte bit 7 = portamento; `$FF` end; tracks `$FF` loop `$FE` stop |
| Galway | `≥ $C0` even = command (15); else `note dur`; note `$5E` silence `$5F` rest, `+$60` raw duration |
| Follin | `≥ $80` command (21, fixed arg counts, `$85` list ends on `≥ $80`); `$01–$7F` note [+len]; `$00` rest |
| JCH | `$80–$9F` dur (bit 4 tie) · `$A0–$BF` instr · `$C0–$FF` super · `$00` rest · `$7E` hold · `$7F` end; track `$80|T`, `$FF`, `$FE` |
| GoatTracker | `$01–$3F` instr · `$40–$4F` fx (+param) · `$50–$5F` fx-only · `$60–$BC` note · `$BD` rest `$BE` keyoff `$BF` keyon · `$C0+` packed rest · `$00` end; orderlist `$D0+` repeat, `$E0+` transpose, `$FF pos` loop |
| Walker | track byte = C64 keyboard character: `Q2W3ER5T6Y7UI9O0P@-*\^` + HOME/DEL = semitones 0..23 (+instrument transpose), space = rest, shifted keys = drums 0..23; block = 16-byte header + 3×L bytes; song = len + block list |
| defMON | pattern row `flag [A] [B] [note]`: flag b7 END, b6 sidcall A, b5 sidcall B, b4 note, b0–3 duration (row = d+2 ticks); arranger byte < $80 pattern, V0 = $FF jump to V1's byte |
| Blackbird | per voice, after decompression, one class read per frame: `$F9–$FF` out-of-band command (b0 syncpoint, b1 tempo+groove follow, b2 end of stream) · `$C8–$F8` effect nr − $C8 · `$80` gate off `$81` legato · `$82–$B7` instrument nr − $82 · `$B8–$C7` delay of 16 − (b & $0F) rows · `$00–$7F` note, pitch = b ≫ 1, duration 1 row if b0 else 2. LZ control byte `t t t t t n n n`: t = 0 → n literal bytes (n = 0 → end of stream), t > 0 → copy n+3 bytes transposed by t − 16 semitones, offset byte follows |
| SID Wizard | rows of 1–4 bytes with bit-7 continuation; note `$60–$6F` vib, `$70–$77` packed rest, `$78–$7E` note-FX; ins `$3F` legato, `$40+` ins-FX; fx `≥ $20` small / `< $20` big+value; `$FF len` end; orderlist `$80–$9F` transpose `$A0–$AF` volume `$B0–$EF` tempo `$FE` stop `$FF pos` loop |

### 8.4 Table-program vocabularies

| player | row form | meaning |
|---|---|---|
| GoatTracker wave | `left,right` | left `<$10` delay, `$10–$DF` wave+$10, `$E0+` cmd, `$FF` jump→right; right 0 none, `<$80` abs note, `≥$80` note+rel |
| GoatTracker pulse/filter | `left,right` | left `≥$80` set, `<$80` count with signed speed right, `$FF` jump |
| JCH wave | A,B columns | A `<$7E` rel, `$80|n` abs, `$7E` hold, `$7F` jump→B; B = ctrl byte |
| JCH pulse/filter | `[init|$FF, Δ, dir·frames, next]` | 4-byte chained records |
| SID Wizard WF/PW/filter | 3-byte rows | row[0] `≥$80` set (`$FE pos` jump, `$FF` hold), `<$80` sweep N frames by row[1] |
| Galway FM/PM | record fields | 4 (2) signed 16-bit gradients × durations, delay, loop policy bits `$81`; FMC bit 3 = 8-entry arp list |
| Follin | commands | `$8E` vibrato(4), `$91` trill(3), `$92` porta(1), `$80` pulse(3), `$88` filter(8), `$8F` blip(4) — no tables |
| Hubbard | instrument bits | fx bit0 drum, bit1 skydive, bit2 arp, bit3 8-bit pulse run; vib depth; pulse speed |
| Walker modulator | 4 param sets (mode, rate, period[, type]) per instrument | triangle of `period` steps of a constant ($0A/$10/$50/$02) every 100−rate calls; period $FF = random from $D41B; type 0/1 = one-shot bend in, 2 = triangle |
| defMON sidTAB | flags1 {WGx b7, WG b6, AD b5, SR b4, TR b3, AF b2, PW b1} then flags2 {PS b7, RE b6, FV b5, CP b4, ACID b3 (2 bytes)}, values follow in test order; DL table = calls−1 (≥$80 stop); pointer hi = 0 → JP to lo | register-column records = the instrument, chained by DL/JP |
| Blackbird effect (pitch) | one byte per step | value = pitch offset in **quarter semitones** ($40 = unison, 0 = fixed max frequency); the *next* byte, if ≥ $80, is the signed jump-back added to the cursor ($FF hold, $FD 2-step loop, $F7 8-step loop) |
| Blackbird wave | one or two bytes per step | `<$C0` = a SID control byte, ANDed with the voice's gate mask; if bit 6 (pulse) is set a parameter byte follows (`≥$80` set `pw = b<<1`, else `pw += b`), advance 2 instead of 1; `≥$C0` = relative backward jump `cursor += b + 1` |
| Blackbird filter | 3-byte rows `[$D418, $D417, cutoff]` | the *next* row's first byte, if ≥ $80, is the jump-back; cutoff bit 7 set = absolute, clear = a signed step added to an accumulator held in an immediate operand, with an overflow clamp that skips the write |
