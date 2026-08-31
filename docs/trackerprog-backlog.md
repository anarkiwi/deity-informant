# trackerprog — review and backlog

Review of [prototype-trackerprog.md](prototype-trackerprog.md) against the
documents it cites and the code it assumes, and the work that enables it,
scoped module by module against `deity_informant/tuneprog/` and the recert
prints under `out/recert-main/`. Sizes follow
[tuneprog-backlog.md](tuneprog-backlog.md) §2: small ≤ 1 day of one agent,
medium ≤ 1 stage, large = a stage with a prototype.

Contents: 1 what already holds · 2 review: claims · 3 review: internal ·
4 work packages · 5 execution order.

---

## 1. What already holds

| doc assumption | state |
| --- | --- |
| §6 input set `tuneprog.S4.json`, `tuneprog.S6.json`, `certificate.json` | all emitted by `pipeline.stage_front`/`stage_print` (`pipeline.py:280,446,357`); `ir.Tuneprog.load` reloads S4 in 0.01 s; S6 has no loader — consumers read the JSON and join on `regions[].id` (`ghidra_facts.py:228`) |
| §6 roles `freq_table cursor timer acc sid_image voice_map` | all exist (`recover.py:231-327`, `facts.py:288-302`); also `table ptr counter phase per_copy`. `state`/`init_constant` are region *kinds*, not roles |
| §7 "cell histories over `interp`, no tracer change" | prototyped: `verify.Verifier` + `np.frombuffer(M.m)` sampled after each `_one()`; GT2 8,236 ticks, 28 named state regions, 3.8 s, 0 divergences, 2.1 MB |
| §2 "grid.py already frames the comparison" | four reductions exist over one log: `grid.grid` level-per-frame with a PW-nibble mask (`grid.py:34-55`), `ghidra_facts._tick_writes` ordered changes unmasked (`ghidra_facts.py:153-181`), `verify._compare` raw ordered incl. mirrors (`verify.py:267-279`), `period.Samples` raw bytes (`period.py:59-67`) |
| §6 T0 "slice `io` stores back" | `irwalk.accessors` has no `cls`; join on `Rgn.kind`. GT2 has **one** `io` store — the ghost flush; the producers are 21 stores into the `sid_image` region, bridged today by naming (`names.image`), not dataflow (no memory SSA, `ssa.py:3`) |
| §6 T1 "`ranges.py` supplies the interval, `gated.py` reads the reflect" | **false on every exemplar**: `ranges.expr_range` bails to width on any self-referential `+`/`-` (`ranges.py:44-49`), and `gated.diamonds` needs one same-name `Let` per arm (`gated.py:34-37`) — no reflect site in GT2/Commando/JCH/SW has that shape. `facts.update_role` (`facts.py:288`) is the seed: self-reference + constant delta, no bound/policy/rate/phase |
| §6 T2 "cursor roles delimit tables" | `facts.index`/`cellindex` (`facts.py:114-125`) is the fact, but it reaches `role = cursor` only for scalar regions ≤ 8 elements (`recover.py:311`), never record-split fields (`views._named_fields`, `views.py:195-203`), and is **not serialised** (`recover.py:67-85`). Every family's score cursors are record fields (`rec[x/7].f00/f03`, `voice_3[v].timer_2`, `rec[x/7+3].timer_3`) — none is named `cursor` in any S6 today |

## 2. Review: claims against their sources

Confirmed as cited (not repeated here): the anatomy license, the one-object
row, Hubbard's write order and `$D401` overwrite, the 12-TET tables, GT2 `$FF`
jumps, defMON 8×/frame, Galway 8-deep stack, Walker LFOs, SW owner voice and
`CKBDTRK`, JCH track-0 filter, `INC $15DD`, the multispeed entry, the
Commando aperiodicity, cert numbers, 51/51.

| § | claim | what the source says |
| --- | --- | --- |
| 2 | GT2 ghost flush "low-to-high" | high-to-low: `for r in 24..0` (anatomy:766, `LDX #$18 … DEX`) |
| 2 | AD/SR share the gate's write-order sensitivity per anatomy §1.3 | §1.3 names gate 1→0→1 and TEST 1→0 only; the rate-counter point is about the ADSR delay bug across notes (anatomy:126-156). An extension, state it as one |
| 3.2 | Blackbird = 4×-resolution table | two overlapped byte arrays, quarter-semitones by **summing two entries** (anatomy:146-149); not liftable as `[u16; 4N]` |
| 3.2 | GT2 `FREQ_LO/HI` 91 entries | 96 (`$14E3`/`$1543`; anatomy:742) |
| ~~3.3~~ | ~~JCH `rec6` pulse `[init, Δ, dir\|frames, next]`, `rec7` filter — shapes swapped~~ | **struck, the row was wrong**: `rec6` *is* the 4-column pulse program and `rec7` the 3-column filter one (prototype-jch:82, 106-107; `jch-guldkorn-intro/tuneprog.md:527-546` reads all seven columns). The doc's only fault was writing "four column programs" for two programs of 4 and 3 columns; W0 states both counts |
| 3.3 | `timer_4` compare is the GT2 wavetable hold | `timer_4` is a field name with no role (prototype-goattracker:84); the print confirms the mechanism (`gt2-je-suis-linus/tuneprog.md:564-569`, not `:551`) but the cited doc does not |
| 3.5 | hard restart is one fixed shape | SW 1.6 writes AD,SR and 1.9 SR,AD (anatomy:1232-1233); Blackbird has no TEST (anatomy:133-135); Walker/Galway do intra-tick gate/TEST edges (anatomy:138-140, 214) — observable under §2 rule 1, inexpressible in `{early, ad, sr, first_ctrl}` |
| 3.7 | `CKBDTRK` is a `tablestep` term | it adds an **absolute** `FREQ[$E + idx]` entry (prototype-sidwizard:110-118), not a difference |
| 5 | Commando pulse run = 16-bit wrap | 8-bit add on pw-lo (commando-floor:222-224); and its carry is **live from the vibrato block** (`commando-song1/tuneprog.md:394`) — `delta const(k)` refuses it |
| 5 | GT2 depth `T1851[y] & $7F` | that is `speedcmp`; depth is the right byte (anatomy:876) |
| 5 | `p_109E`, `p_10AB`, `p_10F5`, `T1864`, `portaval`, `pulsedir`, `pulsedelay` | names absent from the cited prototype docs (they are print/anatomy names; commando-floor uses `porta`, `pwdir`, `pwdelay`); `$10AB` in the anatomy is an SMC immediate |
| 5 | Hubbard porta = tone portamento, clamp(pitch[target]) | no target: free ±step ramp (commando-floor:236-238); it is the "free slide" row |
| 5 | pulse-sweep state is instrument-scoped | `pw` yes; `pwdir`/`pwdelay` are per-voice (commando-floor:226-228) |
| 5 | skydive | dead in the exemplar (`trap 'untaken'`, commando-floor:247) |
| 9.3 | "measured like §6.2" + `xz -9e` | §6.2's six are tokens/lines/statements/blocks/header rows/data rows; `xz` is §8.3. Architecture §11 requires §6.2's six verbatim |
| 1 | 91.6 % "voice-stride state appears" | 91.6 % weighted of tunes with ≥ 50 % voice-like indexed sites (arch:**1009**, not 940); the summary line at arch:1027 says 90 % |
| 10 | note-95 overrun reads two bytes past the table | the certified case is pitch 104 ×25, reading `voice[].ctrl` and `pwdir` — **play-written state** (commando-floor:301-310), not materialisable as `pitch` |
| ~~3–5~~ | ~~Galway, Walker, Blackbird evidence~~ | struck: all three are certified exemplars (arch §9.2) |

## 3. Review: internal

| id | inconsistency |
| --- | --- |
| I1 | §2 last-wins on 16-bit registers vs §4 `freq = pitch[…] + Σ accs`: Hubbard's vibrato/porta/drum/arp each store `freq` independently, the arp absolute — a sum cannot reproduce the observable §2 defines, on the exemplar §9 accepts seventh |
| I2 | §2 compares the ordered ctrl/AD/SR list; §4 `commit(v)` never states the order between hard-restart prelude, stream `set`, `gate(mask)` and note-on, and calls voice order immaterial. The player does not pin the one thing the certificate compares (SW 1.6 vs 1.9 is the test case) |
| I3 | `rate` is steps-per-tick on streams (§3.3) and every-k-ticks on `Acc` (§5); §4 implements neither; §10's multispeed question needs a divider on streams/tempo, which the schema lacks |
| I4 | one global `tempo.step()` (§4) vs `set_tempo` per pattern, SW per-track tempo, GT2 per-voice `F` and `funktempo` |
| I5 | `Order` grammar has no volume/tempo field, but its "degenerate case" is SW pattern/transpose/vol/tempo |
| I6 | `arm(acc_id, param)`: `Acc` has no `param`; GT2's vibrato param selects speedcmp *and* depth; GT2 commands 5/6 (ADSR from a row), 8/9/A (re-point a stream), Follin `$85` raw cross-voice register lists — all `command residue` under §8 on "all six complete" exemplars |
| I7 | §3.2 "never a raw frequency" vs §5 `delta const` on `freq` targets |
| I8 | §5 "every modulation is an Acc" includes two stream rows (arp, tremolo); `target vol, scope voice` is unrealisable (`$D418` is global) |
| I9 | §4 `clamp(note …)` vs §10 "materialise the overrun" — two edge rules, neither works for Commando |
| I10 | `complete` ⇒ "the score closes": `jch-knob-at-night` and Follin song 1 are `complete` with `period: 1`; §6's period-bounded materialisation would emit one row |
| I11 | Commando carries `inputs_pinned == ticks` (11,780); §8's `external input` refuses it as written |
| I12 | defMON is evidence for §3.3/§3.5/§5 and absent from §9's acceptance list |
| I13 | single-family rows against §1's rule: `gate.timer` (GT2), `set_tempo(stream)` (SW), `scope instrument` (Hubbard) |
| I14 | §2 says `compared` records the dropped cross-register order; the example carries only the kept classes |
| I15 | GT2's ctrl/AD/SR order *is* the ghost flush order — the idiom §2 excludes; the trackerprog must state which order it emits |

The schema decisions I1–I3, I6, I10–I11 gate T1/T2 below; the rest are doc
edits. **All fifteen settled by W0 (#295)**, each with a stated decision in
[prototype-trackerprog.md](prototype-trackerprog.md): I1 an ordered producer list
per 16-bit target (§4), I2/I15 a stated `commit(v)` order plus `meta.commit_order`
(§3.1, §4), I3 `rate` is a divider everywhere (§3.3), I4 per-voice tempo (§3.6),
I5 `play` gains `vol?`/`tempo?`, I6 `arm(acc_id, overrides)` + `set`/`set_register`
/`set_stream`, I7 deltas are in the target register's units (§3.2), I8 tremolo
targets the gate mask, I9 no `clamp(note)` — the overrun is a producer (§6), I10
`period: 1` is `end.kind = fixed_point`, materialised to `first_repeat` (§2), I11
the three-part `external input` rule (§8), I12 defMON added to §9, I13
`gate.timer` folded into `early` and `set_tempo(stream)`/`scope` re-grounded, I14
the certificate gains `dropped` (§2).

## 4. Work packages

| # | item | mechanism | owner | size | acceptance |
| --- | --- | --- | --- | --- | --- |
| ~~W0~~ | schema revision of the prototype doc | settle I1–I15 and §2's corrections: per-voice ordered edge list with a stated emit order; `Acc.delta` admits `+ carry(site)`, `repeat(Δ, n)`, `tabcell`, `sext11`; `Acc.phase` may name another `Acc` or a cell or `fn(global_counter)`; `Acc.bound` carries `proved\|projected\|observed` and a projection witness (Commando `hi & $F`); `Acc.target` admits `split(k, 8)` (SW cutoff); `links` for cross-Acc resets (GT2 snap zeroes the vibrato phase); one `rate` meaning; `set_register(reg, v)` or a Follin refusal; a horizon terminator in `Order`; `period: 1` handling; Commando's per-tick input | docs | small | every §2/§3 row cites two certified families or a survey count; the §2 table above empty |
| ~~W1~~ | one observable reduction | `grid.reduce_tick(writes, prev) -> TickObs(edges, values)` + vectorised `reduce_run` over the existing `grid.grid`; constants `CTRL/AD/SR/PAIRS/LEVEL`; `grid.changes` factored out of `ghidra_facts._tick_writes`; `Verifier` gains an opt-in `obs` accumulator after `_compare` (`verify.py:336`). `verify._compare` stays raw — mirror folding, the PW nibble and the cutoff mask must not reach it | grid, verify, ghidra_facts | small (1.5–2 d) | recert 51/51 field-for-field (`compared`, `divergence` untouched); hermetic tests: gate 1→0→1 keeps three ctrl entries, `$D401` double write last-wins, `freq_lo`-only tick carries `prev` hi, PW nibble masked |
| ~~W2~~ | `history.py` | `history(prog, trace, names_doc, calls) -> {name: ndarray(ticks)}` over `Verifier` (`run_init`, then `_one` per tick, promoted to `tick()`), `np.frombuffer(M.m)[idx]`, u16 widening from S6 `u16`; sparse-stride regions sampled by `Region.addrs`; library + `tools/`, **not** a pipeline artefact | history, verify | small (1 d) | hermetic: `counter("INC cnt")` history `[1..8]`, PERIODIC snippet periodic at the cert period, a u16 pair widens; all 51 recert dirs replay with 0 divergences |
| ~~W3~~ | S6 exports T2 needs | serialise `facts.index`, `cellindex`, `idxvar` and the base-pointer relation; name record-split fields `cursor` where `cellindex` says so (`views._named_fields`); `Names.from_dict` | facts, recover, views | small (1.5 d) | the score cursors of GT2/JCH/SW/Commando named `cursor` in S6; recert prints listed line by line where they move |
| ~~W4~~ | T0 provenance | `provenance.py`: roots = `io` stores in `$D400..$D418` **and** stores into `names.image` regions (rekeyed by the flush delta); `(register, voices)` from the store's `lo/hi` envelope (more robust than `cellref.voiced`); backward substitution via `irwalk.single_defs`/`expand` stopping at a named role; leaves named through `cellref.Cells`; `ir.enc` for the expr (add `R16`/`W16` to `_NODES`); `tuneprog.T0.json` per write site with `direct`, `self_update`, `refusal`. Region ids are the presentation view's — carry `(base, size)` | provenance, ir, pipeline | medium (3 d) | every io/image write site of the 42 recert dirs is a named expr or a stated refusal; the record's `print` re-renders to the `tuneprog.md` line |
| ~~W5~~ | T1 `accum.py` | candidates from `facts.cellupd` reaching an io store (W4); `Delta`/`Dir` parser (`idioms.bit`, new `sext11`); a diamond over `Store`/`Call` arms (new — not `gated.diamonds`); the variable-shift loop `x >> cell` recogniser `loops.py` lacks (`tablestep`, GT2 `p_12E5`, Commando `$51E4`); guard walk over dominators → policy; bound from guard (`proved`), projection (`projected`), or history under a period witness (`observed`); two verifiers — interval assertion and **recurrence replay** against W2's history, divergence ⇒ `unclassified update` | accum, idioms, loops | medium–large (5.5–7 d) | hermetic snippet per policy (`wrap reflect-complement reflect-dircell clamp halt reload rate tablestep split`), refusals named with the cell; exemplar regression: GT2 vibrato+porta, Commando bounce+run+porta, JCH pw/cutoff, SW cutoff classified as W0 states |
| ~~W6~~ | T2 `trackerprog/{cursors,streams,score,pitch,refuse}.py` | cursor × history: successor relation at a fixed base → step/jump edges, rows, loop row, terminator byte, holds; nest through `names.u16` bases (depth ≤ 2 else `score not cursor-shaped`); Follin call/ret/for from the dispatch arms + the depth-1 return slot; `pitch` from `names.freq` + per-accessor origin (Commando reads `FREQ` at two bases); materialise over the horizon. **Blocker**: SW's orderlist load is erased by the copy fold (`p_17C8` prints nothing, `T1C40/T1C4E/T1C5C` have no accessors) — either `copyview` keeps the load or SW refuses | trackerprog | large (8–10 d) | goldens on GT2 (33 pattern ptrs, 9×30 instruments, `T16F9`), JCH (26 ptrs, `rec8[19]`, 3 `$FF`s), Commando (`T576B`, `T5889`, `rec2`); the SW fold produces a named refusal until fixed; recert untouched |
| ~~W7~~ | universal player + T3 | `trackerprog/{player,emit,certify}.py`: §4 made exact per W0, rendered tick-for-tick; `certify` = W1's `TickObs` equality against `Verifier.obs` over the whole horizon; S4-style tagged JSON, `trackerprog.md`, the certificate with `refusals` and the loop claim | trackerprog | medium (3–4 d) | GT2 ×2, JCH ×2 0 divergences; every refusal names its cell; §6.2's six numbers + `xz -9e` against the source `tuneprog.md` |

**W0 struck by #295**: §2's table settled row by row (two rows corrected here
rather than in the doc — the JCH `rec6`/`rec7` row was wrong, and the 91.6 % row
cited the wrong architecture line); I1–I15 each carry a stated decision; §5's
table re-derived from the exemplar shapes with two-family evidence per row and two
marked single-family exceptions; §7 rewritten as what landed (#291–#294) plus
`accum.py` remaining; §9 gained defMON and separates §6.2's six numbers from `xz`.
`sext(k, T[c])` was **not** added as a delta form — the only sign-extending
accumulator delta over the certified set is SW's filter step, which is
`tabcell(T[c], signed 11)`; `sext` in the IR is a jump offset only
(`sw-emomyst/tuneprog.md:1205`).

**W1 struck by #291**: `grid.regs/changes/reduce_tick/reduce_run` + `TickObs` and
`Verifier(obs=True)` landed; `ghidra_facts._tick_writes` is filter plus
`grid.changes`, `verify._compare` untouched, recert field-for-field on
`gt2-je-suis-linus` and `jch-guldkorn-intro`.

**W2 struck by #292**: `history.cells/history/widen_u16` + `History` sample the
verifier's own ticks (`Verifier._one` promoted to `tick()`), sparse strides off
`Region.addrs`; `tools/tuneprog_history.py` writes `tuneprog.history.npz`, no
artefact moved and recert reproduces. Replayed with 0 divergences:
`gt2-je-suis-linus` 12,000 ticks / 120 cells / 5.4 s, `jch-guldkorn-intro`
4,000 / 146 / 1.6 s, `sw-emomyst` 12,000 / 129 / 8.0 s, `commando-song1`
11,780 / 206 / 3.4 s.

**W3 struck by #293**: `facts.idxbase`/`cellsrc`/`leaf_reads` and one cell key
(`Facts.cell`: the const base of an index address, dropped when the region does
not contain it) put record fields into `cellindex`; `facts.cursor_cells` is the
one cursor rule for scalars, fold slots and split fields alike, ahead of the
update role; `recover.index_relation` serialises the relation as
`tuneprog.S6.json`'s `index` block and `Names.from_dict` reads the whole document
back. Score cursors now carrying the role: GT2 `rec[x/7].cursor` (`+0`, orderlist
position) and `.cursor_2` (`+3`, row cursor) plus `.cursor_3` (`+5`); JCH
`voice_3[v].cursor` (`+9`, row cursor) and `voice_2[x].cursor`/`.cursor_2`;
SID Wizard `rec` `+0`/`+2`/`+3`/`+4`/`+5`/`+6`; Commando unchanged, its cursors
were already scalars. Recert 4/4 field-for-field, `tuneprog.py` byte-identical;
the only moved print lines are field names.

**W4 struck by #294**: `provenance.py` writes `tuneprog.T0.json` beside S6 —
`{plane, voice_map, image, writes}`, one record per SID write site of the printed
program. Roots are the `io` stores whose envelope lies in `$D400..$D418` and the
stores into a `sid_image` region rekeyed by the flush delta, each carrying the
flush site's pc; `provenance.regvoices` reads the register off the site's base
and the voices off its envelope, which names SID Wizard's opaque
`sid.reg[saved10]` (`freq_lo`, voices 0–2) where `cellref.voiced` gives nothing.
A span no voice stride makes is `kind: file` where one value covers every
register in it (the GT2 flush `sid.reg[v] = ghost.reg[v]`, the JCH/SW
`sid.reg[v] = 0` clears) and a refusal otherwise. `expr` substitutes names
stopping at every cell S6 names, serialised with `ir.enc` — which gained
`R16`/`W16` in `_NODES` — and `W16` gained `env`, the low half's own envelope,
the one thing the 16-bit fold used to drop. Each record re-enters the printer
state its site printed in, so `cells` spell as the print spells them and `print`
is the line of `tuneprog.md` itself.

| tune | sites | direct | image | file | refusals | `print` round-trip | registers |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| `gt2-je-suis-linus` | 22 | 1 | 21 | 1 | none | 22/22 | 11, `freq` pairs included; 7 self-updating |
| `jch-guldkorn-intro` | 15 | 15 | 0 | 1 | none | 15/15 | 8, `freq`/`pw` as pairs |
| `sw-emomyst` | 17 | 17 | 0 | 1 | none | 17/17 | 11 |
| `commando-song1` | 27 | 27 | 0 | 0 | none | 27/27 | 9 |

Over all 51 recert S4 programs: **849 write sites, 849 prints re-rendering to
their own `tuneprog.md` line, 0 sites both unnamed and unrefused**. The 40
refusals are the two shapes the envelope cannot read — 36 `index not a voice`
(Follin's raw cross-voice register list `sid.reg[a75] = …` over `$D405..$D418`,
JCH's `sid.reg[v] = t1` clear at a non-constant value) and 4 `smc target`
(Baumrucker's and Follin's patched store operands, `io[b6200[5] + x2]`), each
naming its own cell. Recert 4/4 field-for-field; S5/S6/`tuneprog.md` regenerate
byte-identical to `main`'s over the same four S4 programs, and the T0 render
leaves the view untouched for the print that follows it.

**W5 struck by #296**: `accum.document` writes **`tuneprog.T1.json`** —
`{plane, horizon, accs, refusals}` — beside T0, from a library and
`tools/tuneprog_accum.py`; no pipeline artefact moves and recert reproduces 4/4.
Five modules, each under 500 lines: `accshape` (a store's guards as its
transitively closed **control dependences**, not its dominators — the bug that
made a join's block look guarded — plus the callers' arguments where a value's
free names are its procedure's parameters, the additive spine, and the
variable-shift loop `loops.repeats` refuses), `accdelta` (§5's grammar),
`accrule` (counters, bound candidates, policy, rate, phase, scope), `acchist`
(a named-cell expression over `history.py`, the interval assertion and the
recurrence replay) and `accum` (candidates, the split join, the records).

Two seeds were widened where their own limits hid an exemplar: `facts.cellupd`
is block-local and capped at two operators, which hides SID Wizard's
`cutoff_lo = ((t3 & 7) + cutoff_lo) & 7` and Hubbard's saved-register store, so
T1 scans the sites itself over proc-wide definitions; and `pipeline.present` is
**not deterministic** — the view's block order varies run to run — so every
enumeration T1 makes is sorted by tick rank and pc, which makes its output
stable (verified over four runs per tune).

| tune | ticks | accs (policy / `bound.from`) | refusals | s |
| --- | ---: | --- | --- | ---: |
| `gt2-je-suis-linus` | 12,000 | 4: vibrato phase `reflect-complement`/`observed`; vibrato and portamento freq `reflect`/`observed`, `tablestep`, the first `phase acc(id)`; filter `wrap`/`observed` `tabcell` | 3 `delta`, 1 `replay` | 7.2 |
| `jch-guldkorn-intro` | 4,000 | 2: slide and vibrato freq `wrap`/`projected`, `field`, scope voice | 3 `replay` | 2.2 |
| `sw-emomyst` | 12,000 | 4: filter `split(3, 8)` ×2 `wrap`/`observed` `tabcell` signed 11; `sid.reg` `clamp`/`projected` and `wrap`/`observed` | 4 `delta`, 1 `replay` | 10.4 |
| `commando-song1` | 11,780 | 0 | 3 `replay` | 3.9 |

**10 accumulators, 0 replay divergences, 0 interval escapes, 15 refusals**, each
naming its cell, its site and the §5 clause it failed. Hubbard refused at its
full horizon: the tick that both reloads a segment and steps it, and the arms
whose carry a call return supplies, left moves the plane could not make, and
fail-closed is the design. Hermetic tests cover `wrap`, `reflect-complement`,
`reflect` on a direction cell, `clamp` with a `links` reset, `halt`, `reload`
with a `$FF` sentinel and a countdown `rate`, `tablestep`, the 16-bit carry join
and the `unclassified update` refusal; the `hvsc` set runs T1 over
`pipeline.main` tmpdirs for all four families.

**The W5 gap closed by #297.** Six rules, each with two families, land in
[prototype-trackerprog.md](prototype-trackerprog.md) §5: reverse postorder as the
tick's own statement order (so a reload and the step that follows it are one
move, not two readings of one), `+ carry(site, flag)` for a bit another block of
the tick leaves and a refusal for one the tick is *given*, a loop's exit test
dropped from the guards of the body that precedes it, both epochs of a cell the
tick moved read for every condition, and a copy loop's scratch opened to the one
expression that fills it. `accshape` split at 500 lines: `accguard` now holds
`key_of`, the control dependences, `opened`, the scratch set and its propagation;
`graph.rpo` is the one reverse postorder. §5 also corrects a fifth row — a
direction cell the *score* sets is the free slide (`wrap`), and only one the play
turns is `reflect`.

| tune | ticks | #296 accs (policy/`bound.from`) | #297 accs | #296 refusals | #297 refusals |
| --- | ---: | --- | --- | --- | --- |
| `gt2-je-suis-linus` | 12,000 | 4: `reflect-complement`/`observed` 1, `reflect`/`observed` 2, `wrap`/`observed` 1 | 4: `reflect-complement`/`observed` 1, **`reflect`/`observed` 1**, **`wrap`/`observed` 2** | 3 `delta`, 1 `replay` | 3 `delta`, 1 `replay` |
| `jch-guldkorn-intro` | 4,000 | 2: `wrap`/`projected` 2 | **5**: `wrap`/`projected` 2, **`reload`/`projected` 2**, **`reload`/`observed` 1** | 3 `replay` | **none** |
| `sw-emomyst` | 12,000 | 4: `clamp`/`projected` 1, `wrap`/`observed` 3 | 4: unchanged | 4 `delta`, 1 `replay` | 4 `delta`, 1 `replay` |
| `commando-song1` | 11,780 | 0 | **2**: `wrap`/`projected` 2 | 3 `replay` | **1** `replay` |

**15 accumulators, 0 replay divergences, 0 interval escapes, 10 refusals.**
GoatTracker 2's only change is the policy correction (its slide's direction is
the command byte the score writes, §5 row 5); SID Wizard is field-for-field.
JCH's pulse and cutoff are `reload` streams of segments with a `tabcell` delta
and a countdown `rate`, and nothing of that tune refuses. Hubbard's portamento is
`wrap`, `field(b5520 & $7E)`, `phase bit(b5520, 0)`, scope voice; its pulse run is
`wrap`, `tabcell(rec2[voice[].cursor_54FE].b5597) + carry($5240, C#41)`, scope
instrument. Its one refusal is `acc_2_lo`, whose **value cell is copy-loop
scratch**: `$550A` is one column that p_519B rewrites once per voice inside
`oscillator`'s `for v in 2, 1, 0`, so a once-a-tick history holds the last voice's
value and cannot take the three apart — the refusal carries `scratch: true` and
its site. Over the certified 1,200-tick `hvsc` horizon the same cell *does*
classify (`repeat(tablestep(FREQ, voice[].freq_idx, timer_3), b550C)`, 0
divergences), which is what the exemplar test pins. The pulse **bounce** is not a
second Acc: both arms park their result in one `saved` name that neither
dominates, so the record carries it as an unnamed producer of the same cell.
Hermetic tests added: reload-then-step in one tick with the rank order asserted,
`graph.rpo` against the CFG, a carry another block defines (accepted, flag
`C#1`) and one the tick is given (refused, flag `C`). Recert 4/4 reproduced, no
artefact moved, T1 byte-stable over four runs under `PYTHONHASHSEED=random`.

**The last W5 refusal closed by #298.** Hubbard's vibrato keeps its value in
`acc_2` (`$550A`), one column that `p_519B` rewrites once per voice inside
`oscillator`'s `for v in 2, 1, 0`: it carries no state across ticks and a
once-a-tick history holds only the last voice, so no recurrence over the cell can
be replayed. T1 now replays such a producer against the **register** T0 says the
value lands in — one series per voice out of `Verifier(obs=True)`'s `TickObs`
(W1) over the same replay `history.py` runs — with the other T0 sites of the same
register field as the ticks what the tick left there is another producer's. Six
rules land in [prototype-trackerprog.md](prototype-trackerprog.md) §5 and one new
module, `accreg.py` (155 lines), holds the reading; `history.History` resolves a
byte by its address, because the presentation view splits regions the sampled
program does not carry.

| tune | ticks | #297 accs (policy/`bound.from`) | #298 accs | #297 refusals | #298 refusals |
| --- | ---: | --- | --- | --- | --- |
| `gt2-je-suis-linus` | 12,000 | 4: `reflect-complement`/`observed` 1, `reflect`/`observed` 1, `wrap`/`observed` 2 | 4: unchanged | 3 `delta`, 1 `replay` | 3 `delta`, 1 `replay` |
| `jch-guldkorn-intro` | 4,000 | 5: `wrap`/`projected` 2, `reload`/`projected` 2, `reload`/`observed` 1 | 5: unchanged | none | none |
| `sw-emomyst` | 12,000 | 4: `clamp`/`projected` 1, `wrap`/`observed` 3 | 4: unchanged | 4 `delta`, 1 `replay` | 4 `delta`, 1 `replay` |
| `commando-song1` | 11,780 | 2: `wrap`/`projected` 2 | **3**: `wrap`/`projected` 2, **`reload`/`observed` 1** | 1 `replay` | **none** |

**16 accumulators, 0 replay divergences, 0 interval escapes, 9 refusals**, 31 s
of CPU. The vibrato records `repeat(tablestep(FREQ, voice[].freq_idx, timer_3),
b550C)`, `phase fn(timer_6)`, `policy reload` (base `FREQ[voice[].freq_idx]`),
scope voice, `bound.from observed` over the register's own tick values with the
horizon as its witness — Commando is aperiodic (architecture §5.2), so no period
witness exists for it and the record says which. Three seeds were corrected where
they hid or crossed an exemplar: a store a *cursor* indexes is not a copy loop's
scratch (Hubbard's `rec2[].b5591`, SID Wizard's `ptr_4`), a counted loop whose
exit test guards its body runs its bound's own value of passes and not one more
(Hubbard `$520B`), and a pair's half written from several call sites is one
clause per arm, not per block. `accshape.enclosing` makes the counted loop the
innermost one, which is also what makes the shift count deterministic. Hermetic
tests: a per-voice loop reusing one scratch cell reloaded from a table each pass
and written to `$D400 + 7v` classifies per voice, the same loop writing
`$D404 + 7v` (an edge, not a level) refuses, plus `accreg.column` field algebra
and the register bound's two witnesses. Recert 4/4 reproduced, T1 byte-stable
over four runs under `PYTHONHASHSEED=random`.

**W6 struck by #299**: `trackerprog/` lands beside `tuneprog/` — `resolve`
(a table read's address as one expression: reaching definitions with guarded
alternatives (`Sel`), scratch pointer stores, joins, callers' arguments; a
definition reaches forward only, a name a loop carries round stays free and
binds per copy), `hist` (the expression over `history.py`, alternatives chosen
by their guards at the epoch the guard read), `cursors` (base / origin / cursor
/ shift of every read, the successor relation of a cursor's history), `score`
(pointer-base nests to depth 2, else `score not cursor-shaped`; the order
channels are the tables a pattern channel's selector reads, directly or through
the state cells its stores fill; terminator bytes from the cursor's own reset
stores; materialisation per voice as fetch events from the post-init sample on),
`streams` (a self-stepped cursor is a stream, one only the score sets a
selector; both carry their table's columns), `pitch`, `refuse`, `lift`, and
`tools/tuneprog_score.py` writing **`tuneprog.T2.json`**. No pipeline artefact
moves; recert reproduces.

| tune | ticks | pitch | streams / selectors | score | refusals | s |
| --- | ---: | --- | --- | --- | --- | ---: |
| `gt2-je-suis-linus` | 12,000 | `lo\|hi` 91 | wavetable `T16F9` (100 rows, 4 columns), pulse `T17C1` (29), filter `T17FB` (43, 8 columns); pattern pointers `T15A9/T15CA` **33**, instruments **9 × 30** | order `T1875` per voice through `b15A3/b15A6`, `$FF`; patterns `T18B7` through the 33 pointers, terminator `0`, depth 1 | none | 6.5 |
| `jch-guldkorn-intro` | 4,000 | `u16le` 95 | pulse `rec6` (11 rows, 5 columns), filter `rec7`, wave `T17DB/T181B` (64); instruments `rec8` **19** × 8 | order `T199D` walked by the `timer:acc` pointer pair, **three `$FF` ends**; patterns `T19FE` through `T19C6/T19E1` **[26]**, terminator `$7F`, depth 2 | none | 2.2 |
| `commando-song1` | 11,780 | `u16le` 80 | instruments `rec2` 13 × 6 | order `T576B` through `b56F9`; patterns `T5889` through `T5712/T573F` [31], terminator `$FF`, depth 2 | none | 3.9 |
| `sw-emomyst` | 12,000 | `hi\|lo` 104 | — | two ptr-based channels, no order | 7 × `score not cursor-shaped`: `rec[].cursor` (`b1024@$103B`) filled by `p_17C8`'s erased return, and the cells `p_124D`/`p_16D6`/`p_1665`/`p_1537`/`p_16BA` fill the same way | 9.5 |

The SW blocker stands as a **named refusal**: the copy fold leaves `p_17C8` an
empty body returning the pattern number, so no table read fills `rec[].cursor`
and the pattern channel's selector is opaque (§4 W6, §5). The fold fix is not
small — it is `copyview`'s treatment of a per-voice orderlist load — and is not
attempted here. Hermetic tests: a one-voice tune with an orderlist, a pointer
table, two `$FF`-ended patterns and a 12-TET table lifts to its order, its
patterns and its pitch with every row's hold; depth 3 refuses; `decompose`,
`successors`, `free`, `Refusal` and the JSON round trip.

**W7 struck by #300 — the player and the certificate land; GT2 and JCH do not
yet certify.** `trackerprog/player.py` is §4 made exact: per voice the row clock,
the sequencer step consuming an event (`note`, `ins`, `cmds`), the armed
accumulators, and `commit` — the pending `set`s of ad/sr/ctrl as edges in
`meta.commit_order`, the `pitch[note] + Σ accs` freq producer and the other
level registers as values — reduced by `grid.reduce_tick` into the same
`TickObs` the verifier keeps. `certify.py` is §2's comparison over the whole
horizon (per-voice edge order, pair and level values; voice and cross-class
order dropped and listed), the certificate with `compared`, `dropped`,
`refusals`, `emitted`, the loop claim re-checked on the render, and `rendered`
(how far a partial render agrees) as a diagnostic. `emit.py` lifts T0's write
sites into the schema — a constant or a pitch lookup on the row's note, committed
at the row boundary or every tick — and refuses every other site as `command
residue` naming its printed line and pc; T1's and T2's refusals are carried
into the certificate. `tools/tuneprog_trackerprog.py` writes
`trackerprog.certificate.json` always and `trackerprog.json` / `trackerprog.md`
only with no refusal. The hermetic tune (an orderlist, a pointer table, two
patterns, a 12-TET table, gate on at every row) lifts with no residue and
**renders its observable exactly — 0 divergences over 64 ticks**, the loop claim
re-checked; that is the universal player certified against `Verifier.obs` once.

| tune | ticks | refusals | rendered equal | trackerprog six + `xz -9e` | source `tuneprog.md` six + `xz` |
| --- | ---: | --- | ---: | --- | --- |
| `jch-guldkorn-intro` | 4,000 | 14 `command residue`, 3 `unclassified update` | 0 | 1,844 / 454 / 424 / 31 / 8 / 200, 1,056 B | 3,154 / 361 / 207 / 152 / 292 / 182, 5,696 B |
| `gt2-je-suis-linus` | 12,000 | 31 `command residue`, 4 `unclassified update` | 0 | 7,155 / 3,531 / 3,481 / 56 / 13 / 383, 2,344 B | 5,768 / 908 / 336 / 321 / 318 / 270, 8,380 B |
| `commando-song1` | 11,780 | 29 `command residue`, 3 `unclassified update` | 0 | 7,575 / 1,885 / 1,839 / 44 / 5 / 93, 1,228 B | 2,129 / 271 / 161 / 107 / 182 / 127, 4,644 B |
| `sw-emomyst` | 12,000 | T2's 7 `score not cursor-shaped` (`p_17C8`) + the write sites | 0 | — | — |

(six = tokens / lines / statements / blocks / header rows / data rows; the
trackerprog's statements are its score rows, its blocks the patterns, streams
and instrument tables, its data rows the table and pitch entries — the score
materialised over the horizon is what the sizes measure, so `xz` is the
comparable number and it is 3.6–5.4× smaller than the program's.)

**What the acceptance still lacks, by name.** The 0-divergence renders of GT2 ×2
and JCH ×2 are *not* met: every residue is a T0 write site the schema's data
forms do not yet carry — GoatTracker 2's `ghost[x/7].ctrl = …` wave-table
column with its gate mask, its `ghost.freq` producers off the vibrato and
portamento accumulators, the hard-restart `set(ad,$F)/set(sr,0)` two ticks early
(`early`), commands 5/6/8/9/A; JCH's `sid[x].ad = voice[x].ad` shadow copies,
`sid[v].ctrl = 9` at note-on beside `sid[x].ctrl = (b175D & f06)` every tick,
`sid[x].pw = voice[x].pw` off the `reload` streams; Hubbard's `sid.reg[…]`
per-fetch writes and the `rec2[…]` instrument columns. These are the
**instrument, prelude and stream lift** — T0 expression → `Ins.adsr`,
`prelude(stream, early)`, `streams.wave/pulse/filter` and `Producer` —
which #299/#300 leave as a package of its own (W8 below). T1's own refusals
(GT2 `ghost.pw_lo`, `b1461` ×2; JCH `voice[].pw_lo` ×2 and `cutoff_hi` at the
1,200-tick test horizon; Commando `acc_2_lo`, `voice[].acc`, `rec2[].b5591` at
the full one) are carried as `unclassified update`. Commando ×2 and SW ×2 are
therefore not attempted beyond the certificate: Commando's I1/I11 are settled
(W0) but its sites are residue like the trackers'; SW refuses at T2.

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| ~~W8~~ | instrument, prelude and stream lift | a T0 site whose value is an instrument column at the selector → `Ins.adsr`/`set(reg, ins.col)`; whose guards are a stream's step (a cursor's hold elapsing) → a `Step.sets` of that stream; whose guards are `k` ticks before the row boundary → the prelude's `early`; whose value reads a T1 acc cell → a `Producer` over that acc, with the acc's arming read off the note-on stores that reset it. Every other site stays `command residue` | large | JCH ×2, GT2 ×2 at 0 divergences on `tools/tuneprog_trackerprog.py`; the hermetic tune gains an instrument table and a wave stream |

**The SID Wizard blocker closed by #303 — it was never the copy fold.** The
erased body of `p_17C8` was the printer's: `ir.retexpr` shows a `return` value
only when a caller reads *exactly one* register, and `p_17C8`'s callers read `A`
and `N` — the orderlist byte and its own sign test. The general rule now: a flag
returned beside a value is that value's own test (`Z` its `== 0`, `N` its bit 7)
and not a second value, so a return of one value and its flags shows the value.
`p_17C8` prints its three arms (`return T1C4E[ptr_4[1406] + y]` …) and
`T1C40/T1C4E/T1C5C` regain their accessors. Four resolver rules followed, each
general: a name a call returns opens to the callee's exits (one alternative per
`Return`, the parameters the call's arguments); a loop-carried name whose entry
value is a cell's reading opens to that reading (a cursor stepped past skipped
entries), while one whose entry value is a constant is the loop's own index and
stays free; a pointer held in data (`ptr_4[$17CF]`, a 16-bit read of an
init-written cell) and a pair read of a table (`R16[$2477 + …]`, a lo|hi pointer
table) are pointer bases, and a base is any expression over them — a table entry
plus a relocation base with its carry spelt out included; a copy is bound where
the access's own guards over the copy index admit it (`x == 7` picks `T1C4E`).
T2 then lifts SID Wizard's score — three per-voice orderlists, one pattern channel
`T1C6A` through `T2478[rec.cursor] + base`, terminator `$FF` — with no refusal,
and T3 certifies both tunes (table below). The recert of the four exemplars
reproduces 4/4. The presentation change, measured over the 51 recert programs
with architecture §6.2's harness: **+54 tokens** (166,945 → 166,999), all in
the two SID Wizard prints (+27 each, the three `return T1C4E[ptr_4[1406] + y]`
arms); lines, statements and blocks unchanged (18,258 / 10,211 / 8,358); the
other 49 prints byte-identical.

**W8 struck by #302 — six tunes certify on the universal player at 0
divergences.** The lift changed shape rather than growing rules: a row's sound is
lifted as a **stream** (§3.3) from the observable itself — per tick of the row,
the voice's ordered ctrl/AD/SR edges and the level values it left, as steps with
holds; a frequency that is a pitch entry is `note_off(d)` from the row's note,
any other is `freq(v)`, pulse is `pw(v)`; the first frequency a voice ever
writes is `note_abs(n)`. Equal streams are one stream and a row's instrument is
the stream it arms; the global channel (cutoff, res_route, mode_vol) is one
stream over the horizon. Nothing in `emit` reads a tune's code any more, so the
only residues are T2's (a voice with no cursor-shaped score) and §8's `sample
stream` (a second schedule entry, refused by name at `mode_vol`); T1's
accumulators ride along as annotations (`accs`), the player does not step them.
`player.py` steps armed streams by their holds and commits a step's sets in the
step's own order. The hermetic tune, JCH ×3, GT2 ×2 and Commando ×2 render
`emitted: true` with `divergence: null` over their whole horizons and the loop
claim re-checked where the horizon reaches a second period; SID Wizard ×2 stay
refused at T2 (`p_17C8`).

| tune | ticks | emitted | divergence | refusals | trackerprog six + `xz -9e` | source `tuneprog.md` six + `xz` |
| --- | ---: | --- | --- | --- | --- | --- |
| `jch-guldkorn-intro` | 4,000 | yes | none | 0 | 1,568 / 505 / 5,729 / 367 / 4 / 5,354, 6,916 B | 3,154 / 361 / 207 / 152 / 292 / 182, 5,696 B |
| `jch-knob-at-night` | 12,000 | yes | none | 0 | 23,397 / 7,798 / 18,416 / 1,791 / 4 / 10,724, 23,916 B | — |
| `jch-easy-does-it` | 1,799 | **no** | — | 1 `sample stream` (`mode_vol`, the CIA #2 NMI entry) | — | — |
| `gt2-je-suis-linus` | 12,000 | yes | none | 0 | 10,636 / 3,531 / 14,988 / 1,085 / 4 / 11,598, 16,000 B | 5,768 / 908 / 336 / 321 / 318 / 270, 8,380 B |
| `gt2-do-it-again` | 12,000 | yes | none | 0 | 9,808 / 3,260 / 13,320 / 760 / 4 / 10,174, 13,904 B | — |
| `commando-song1` | 11,780 | yes | none | 0 | 9,775 / 3,256 / 15,419 / 1,263 / 4 / 12,363, 17,992 B | 2,129 / 271 / 161 / 107 / 182 / 127, 4,644 B |
| `commando-song2` | 11,780 | yes | none | 0 | 5,046 / 1,681 / 15,958 / 486 / 4 / 14,383, 13,968 B | — |
| `sw-emomyst` (#303) | 12,000 | yes | none | 0 | 8,232 / 2,724 / 15,769 / 567 / 4 / 13,179, 14,112 B | 8,102 / 1,055 / 422 / 426 / 7 / 265, 8,888 B |
| `sw-end-of-the-world` (#303) | 16,000 | yes | none | 0 | 6,693 / 2,219 / 22,729 / 1,415 / 4 / 20,648, 29,944 B | 7,596 / 1,028 / 407 / 423 / 7 / 326, 9,652 B |

(six = tokens / lines / statements / blocks / header rows / data rows over the
trackerprog print: statements are score rows plus stream steps, blocks patterns
plus streams, data rows stream steps plus pitch entries.)

**What this says, plainly.** The layer's claim in §9 — that the score compresses
*better* than the program that played it — is **not** met by this lift: `xz`
of the trackerprog print is 1.2× (JCH) to 3.9× (Commando) the source
`tuneprog.md`'s, because a row's sound is materialised per row rather than
generated from an instrument table, a wave/pulse/filter stream and the
accumulators. The certificate is exact and family-free, and the score half is
lifted (T2's order, patterns, rows and holds); the sound half is a closed form
of what the tuneprog's tables and accumulators generate. Folding those streams
back into `Ins{adsr, prelude, streams}` and `Producer`s over T1's `Acc`s — so
that equal instruments share one table row and vibrato is an `Acc` again — is
the remaining package (W9): it is the one that makes `xz` smaller, and every
rule it needs is now checkable against a certified render.

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| ~~W9~~ | streams back into instruments and accumulators | factor each row stream into `Ins.adsr` + a shared wave/pulse/filter stream (the T2 stream tables, aligned to the row's steps) + a `Producer` over a T1 `Acc` for the `freq(v)` runs a bounded recurrence regenerates; a stream that factors nowhere stays a stream | large | the same six tunes at 0 divergences with `xz` below the source's |

**W9 struck by #304 — half met.** The lift gained, each rule generic and each
render still exact: levels as **deltas** from the last tick (`pw_delta`,
`cutoff_delta`, `freq_delta`), a vibrato or slide as **`freq_ts(m, shift)`** —
§5's `tablestep`, `m` semitone-steps above the row's note shifted down, the same
stream at every note; a run of equal set lists or a short **cycle** repeated as
one step (`x6 a | b`); rows with the same note whose first tick only repeats the
previous tick's writes **merged** into the row they hold; each row's sound as
**lanes** (edge registers apart where the tune keeps one order between them,
which is `meta.commit_order`; note, pitch, pulse), a row's instrument the tuple
of its lane streams and a shorter note the same stream **cut** at its length
(prefix merging); patterns keyed on note offsets with the **transpose** in the
order; the global channel one row per register, cut at the loop; and a complete
source materialised over **its period** (span `first_repeat + 1`, the loop
re-entering the row the period starts in, with the levels the first pass
carried into it stated as `enter`). Every one of the eight certifies as before.

| tune | ticks | emitted | divergence | trackerprog six + `xz -9e` | source six + `xz` | below |
| --- | ---: | --- | --- | --- | --- | --- |
| `jch-guldkorn-intro` | 4,000 | yes | none | 1,539 / 450 / 2,631 / 662 / 4 / 2,315, 5,524 B | 3,154 / 361 / 207 / 152 / 7 / 182, 5,696 B | yes |
| `jch-knob-at-night` | 12,000 | yes | none | 24,681 / 7,746 / 13,195 / 1,572 / 4 / 5,558, 13,624 B | 2,930 / 362 / 239 / 150 / 7 / 8,715, 19,904 B | yes |
| `sw-emomyst` | 12,000 | yes | none | 8,768 / 2,464 / 10,234 / 972 / 4 / 7,911, 8,452 B | 8,102 / 1,055 / 422 / 426 / 7 / 265, 8,888 B | yes |
| `commando-song2` | 11,780 | yes | none | 5,282 / 1,684 / 2,732 / 631 / 4 / 1,157, 3,612 B | 1,872 / 238 / 146 / 99 / 8 / 84, 3,664 B | yes |
| `gt2-je-suis-linus` | 12,000 | yes | none | 10,213 / 3,011 / 10,227 / 1,212 / 4 / 7,362, 9,964 B | 5,768 / 908 / 336 / 321 / 5 / 270, 8,380 B | **no** (1.19×) |
| `gt2-do-it-again` | 12,000 | yes | none | 5,864 / 1,723 / 7,768 / 1,129 / 4 / 6,160, 8,080 B | 5,619 / 875 / 331 / 312 / 5 / 204, 7,688 B | **no** (1.05×) |
| `commando-song1` | 11,780 | yes | none | 11,208 / 3,342 / 4,572 / 1,250 / 4 / 1,436, 6,732 B | 2,129 / 271 / 161 / 107 / 7 / 127, 4,644 B | **no** (1.45×) |
| `sw-end-of-the-world` | 16,000 | yes | none | 4,764 / 1,390 / 9,787 / 1,629 / 4 / 8,539, 14,156 B | 7,596 / 1,028 / 407 / 423 / 7 / 326, 9,652 B | **no** (1.47×) |
| `jch-easy-does-it` | 1,799 | **no** | — | — | — | refused: `sample stream` (`mode_vol`, the CIA #2 NMI entry) |

(six = tokens / lines / statements / blocks / header rows / data rows; the source
`tuneprog.md` measured by the same harness — its data rows are the `## data`
lines, its header rows the `## meta` block.)

**What stays open, by name.** Four of eight prints are still larger than the
program that played them. The residue is in two places: GoatTracker 2's per-row
**instrument variety** — a row's lanes fold the instrument *and* the row's
commands (vibrato and portamento parameters, `set_stream` re-points) into one
sound, so `gt2-je-suis-linus` carries 515 instruments for 30 in the table — and
the **global filter program** re-triggered per note, materialised once over the
period (3,500 `cutoff_delta` steps); Hubbard's **pulse run with a carry**
(`pw += pspeed + C`, `C` the vibrato add's own carry, §5) and its aperiodic
horizon, so no two rows' pulse lanes agree and no period cuts the score. Two
folds were tried and taken out again because they broke exactness or bought
nothing: lanes as *table walks* (a lane whose sets are a function of a T2 stream
cursor's position — it fails at the lead-in, where the cursor stands at 0 before
any note, and at the hard-restart gate-off, written at a held wave-table
position) and a *tail* of `early` ticks split off each row (the restart's, but
the trigger from a merged prefix stream is not the row's own length). The next
package (W10) is the one §5 always named: the **producer over T1's `Acc`** —
GT2's vibrato/portamento off `acc0..3` with the row command as `arm(acc,
overrides)`, Hubbard's pulse run with `carry(site)` from the vibrato add, and the
filter as a stream the instrument arms — so that a row's sound is its instrument
and its commands, not their unfolding.

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| ~~W10~~ | the sound from the program's data, not the observable | cut the fetch regions out of the certified tick, run them over the program's own tables, and keep everything else as the producer program | large | eight of nine exemplars certify from data alone (the ninth is the sample-stream refusal); instruments are the program's table; six of eight prints below the source's `xz` |

**W8/W9 were wrong at the root, and W10 replaces them (t3-from-data).** #302
and #304 lifted a row's *sound* from `Verifier.obs`: the SID-write trace sliced
per row and re-encoded as streams (deltas, cycles, prefix-shared lanes), so
`certify` compared an encoding of the observable with the observable — 0
divergences was tautological — and the "instruments" were the distinct
slices (515 for GoatTracker 2's 30, 634 for Commando's 13), `accs` were unused
annotations, every row had `cmds: []`, and the four prints still above the
source's `xz` were exactly the tunes with generative structure the lift never
read. All of that is gone: `emit.py` no longer imports the observable, and
`Streams`/`row_stream`/`steps_of`/`lanes`/`_cycle`/`_delta`/`_nearest` with
W9's compression layer are deleted.

What replaced it, each rule generic and each checked on all four families:

* **The fetch region** (`region.py`). On the certified S4 program, the blocks
  that read a score byte — a load of an order or pattern table T2 named, or a
  name derived from one through `Let`/`Call` (not through a `Phi`: the value
  after a join is the player's) — seed a region; a loop whose back edge a score
  byte decides (the loop that walks a row) is seeded whole. Each seed cluster
  grows to the smallest single-entry region whose exit post-dominates it
  (`sese`), side doors allowed where the exit still post-dominates them
  (Hubbard's fetch either falls into the play code or skips it), clusters that
  touch merge, and a proc called only from inside a region gets no regions of
  its own. JCH: one region of 40 blocks; GoatTracker 2: the orderlist fetch (9)
  and the pattern fetch (40); Hubbard: the row fetch (16) and the pattern-end
  peek (3); SID Wizard: the pattern read (19) and the orderlist read (12, with
  `p_17C8` inside).
* **The score as data** (`player.py`, recording). The player is one interpreter
  over the S4 program from the post-init image (init run by the player itself;
  the pinned uninitialised-RAM values are data, an input whose values vary is
  the section 8 `external input` refusal). Lifting runs the tick with the
  regions executed and records, per entry, one *fetch*: the stores it made (cells
  and registers, in order), the score bytes it read, the temps it left live,
  the block it resumed at. Certification replays the same interpreter with the
  regions skipped and the fetches applied — the score tables are never read —
  and compares with `Verifier.obs`. A fetch changed by hand is a named
  divergence; a score run out of is a trap.
* **Rows, patterns, order.** A row is every fetch one voice made in one tick
  (the voice read off the index the region's addresses bind at entry); its
  `dur` is the ticks to the voice's next fetch, its `bytes` the score bytes,
  its `cmds` every store, its `sets` the cells the print shows (the score's
  own cursors and pointers left out). A visit ends where the bytes stop
  continuing the last row's; a pattern is keyed on its rows' `(dur, bytes,
  sets)`, so a second visit that decodes the same way is the same pattern.
* **Instruments** (`emit.instruments_of`). The table the `ad`/`sr` write sites
  index, through T2's resolver: the selector under their reads (JCH `rec8`,
  GoatTracker 2 `cursor_1490`, Hubbard `rec2`) or the pointer table a record
  base goes through (SID Wizard `T244E`, 11 pointers); rows are the entries
  read off the image, keyed by cursor value, `used` the ones the score reached.
* **Producers.** Every T0 write site outside the regions, with its control
  dependences as `when`, its cells, and the T1 accumulators whose `sites` it is
  (`[acc0]` tags). **Streams** are T2's cursor tables with their column bytes.
  **`accs`** are T1's records.

| tune | ticks | emitted | divergence | refusals | instruments (table / used) | accs | producers | regions | trackerprog six + `xz -9e` | source six + `xz` | below |
| --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |
| `jch-guldkorn-intro` | 4,000 | yes | none | 0 | `rec8` 19 / 19 | 5 | 15 | 1 | 57,525 / 992 / 997 / 13 / 4 / 450, 4,704 B | 3,154 / 361 / 207 / 152 / 7 / 182, 5,696 B | yes |
| `gt2-je-suis-linus` | 12,000 | yes | none | 0 | `cursor_1490` 30 / 26 | 4 | 20 | 2 | 60,120 / 2,716 / 2,656 / 89 / 4 / 1,361, 7,348 B | 5,768 / 908 / 336 / 321 / 5 / 270, 8,380 B | yes |
| `commando-song1` | 11,780 | yes | none | 0 | `rec2` 13 / 9 | 3 | 20 | 2 | 370,729 / 3,255 / 3,269 / 7 / 4 / 167, 11,412 B | 2,129 / 271 / 161 / 107 / 7 / 127, 4,644 B | **no** (2.5×) |
| `sw-emomyst` | 12,000 | yes | none | 0 | `T244E` 11 / 11 | 4 | 17 | 2 | 22,030 / 2,489 / 2,462 / 51 / 4 / 1,774, 3,816 B | 8,102 / 1,055 / 422 / 426 / 7 / 265, 8,888 B | yes |
| `jch-knob-at-night` | 12,000 | yes | none | 0 | `rec8` 5 / 5 | 2 | 29 | 1 | 274,221 / 7,777 / 7,800 / 9 / 4 / 164, 2,788 B | 2,930 / 362 / 239 / 150 / 7 / 8,715, 19,904 B | yes |
| `jch-easy-does-it` | 1,799 | **no** | tick 0, `mode_vol` | 1 `sample stream` (`mode_vol`, the CIA #2 NMI entry at `$40E9`) | `rec8` 16 / 16 | 5 | 16 | 1 | — | — | refused |
| `gt2-do-it-again` | 12,000 | yes | none | 0 | `cursor_1490` 20 / 20 | 4 | 20 | 2 | 36,813 / 1,706 / 1,665 / 70 / 4 / 1,095, 6,096 B | 5,619 / 875 / 331 / 312 / 5 / 204, 7,688 B | yes |
| `commando-song2` | 11,780 | yes | none | 0 | `rec2` 8 / 8 | 2 | 14 | 2 | 154,823 / 1,637 / 1,639 / 13 / 4 / 114, 4,204 B | 1,872 / 238 / 146 / 99 / 8 / 84, 3,664 B | **no** (1.15×) |
| `sw-end-of-the-world` | 16,000 | yes | none | 0 | `T244E` 21 / 21 | 6 | 16 | 2 | 25,659 / 3,218 / 3,168 / 71 / 4 / 4,030, 5,184 B | 7,596 / 1,028 / 407 / 423 / 7 / 326, 9,652 B | yes |

(six = tokens / lines / statements / blocks / header rows / data rows;
statements are pattern rows plus producers, blocks patterns plus streams,
regions and the instrument table, data rows pitch plus instrument and stream
entries. The T3 tool's whole run — history replay, T2, the lift and the
certified replay — is 8 s for JCH and 34 s for SID Wizard.)

**What this does and does not claim.** The score is data: the fetch never runs
at certification and the tables it read are not read. The instrument table,
the streams and the accumulators are the program's, named and printed. The
sound half is *not yet* section 4's fixed procedure over `Ins{adsr, prelude,
streams}` and `Producer`s over `Acc`s: it is the certified tick outside the
regions, carried as the S4 program in the trackerprog (`program`) and run by
the one interpreter, with its SID write sites listed as producers under their
guards. That is honest and exact, and it is the ground the section 4 reduction
has to be proved against — a producer list rendered by the fixed procedure must
reproduce what this interpreter does tick for tick. Hubbard's two prints are
above the source's `xz` because his fetch writes the SID per row (`ctrl`, `pw`,
`ad`, `sr`, `freq`), so every row's `sets` carries them; the other six are below.
A region entered straight from another (his second song's row fetch after the
pattern-end peek) ends the fetch there and starts the next.

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| ~~W12~~ | T1 `Acc.step`: an exact recurrence, not a verified claim | every acc carries `step` — clauses in call-chain rank order, each guard the branch's own condition resolved at its decider, each read a named cell at one epoch (`pre`/`post`/`mid`, the last through the cell's own clauses in `inputs`), scratch through its reaching stores, call returns as alternatives; `acchist` replays it from `cell(t-1)` and requires equality at every tick; what it cannot state or reproduce refuses by name | accstep, accshape, accguard, resolve | medium–large | JCH pw ×2 and cutoff, GT2 filter, Commando portamento exact over their horizons; every other acc a named refusal (`inexact recurrence` with term and site, `divergent recurrence` with its first tick); hermetic snippets per policy replay exactly |
| W11 | the producer program as section 4 | classify each producer's guards against the events the fixed procedure has (row, note-on, `early`, a stream's step, an accumulator's rate) and its value against the data forms (an instrument column, a stream column, a pitch lookup, an `Acc`), exact over the horizon or a named refusal; then `player` is section 4 and `program` leaves the trackerprog | large | the same nine tunes at 0 divergences with no `program` block |

Total ≈ 24–30 agent-days. W1–W3 are independent of W0 and of each other;
W4 depends on W3; W5 on W0, W2, W4; W6 on W0, W2, W3; W7 on all.

## 5. Execution order

1. **W1 + W2 + W3** in parallel — small, recert-neutral, each its own PR.
2. **W0** — the doc revision, reviewed against §2/§3 above; it fixes the
   `Acc` and `Cmd` shapes W5/W6 build to.
3. ~~**W4**~~, then ~~**W5**~~, and ~~**W6**~~ (on GT2/JCH/Commando; SW refuses
   by name until the fold fix).
4. ~~**W7**~~, ~~**W8**~~, ~~**W9**~~ (superseded: they read the
   observable), ~~**W10**~~ (the lift from data: nine tunes certified from
   their programs' tables); then **W11** for the section 4 reduction;
   ~~Follin after I6~~ — landed, all 32 subtunes certified write for write
   ([prototype-follin-trackerprog.md](prototype-follin-trackerprog.md)), and
   I6's `set_register` needed no form of its own: `$85` is §3.7's `reg.N`.

~~Deliberately not now: Galway/Walker/Blackbird~~ — all three landed. Blackbird
(#322), Walker over its whole 8,052-call horizon and with a new front-end
certificate ([prototype-walker-trackerprog.md](prototype-walker-trackerprog.md)),
and **Galway over all fourteen subtunes** with a front-end certificate the tune
had never had ([prototype-galway-trackerprog.md](prototype-galway-trackerprog.md)):
29,911 ticks, 0 divergences, write-for-write identical per register, and the two
forms the ninth family forced — a counted loop that nests, over the same stack
its calls use, and `meta.stop` ∈ {`voice`, `sequencer`} for what a score's own
stop stops. **All nine of the anatomy's families are now certified exemplars,
and none of them is prose-only.** Deliberately not now: multispeed (§10). **Not** defMON:
that line was wrong. defMON is certified four times over — `automatas`,
`automatas-6581`, `automatas-8580` (149,025 ticks, period 129,024, `complete`)
and `goto80-jazzpjazz` (1,799 ticks, `horizon`), architecture §9.2 — with recert
dirs for all four and its own [prototype-automatas.md](prototype-automatas.md).
W0 puts it in the acceptance list.

---

## 6. What the GoatTracker 2 transliteration found in the print

Five gaps in the printed tuneprog, each one a place where
[prototype-goattracker-trackerprog.md](prototype-goattracker-trackerprog.md)
§8 had to open a disassembler because the print did not settle a fact a
materialiser needs. All five are in the *presentation*, not the certified
program: the S4/S6 artefacts carry the information, and `printer` drops or
re-derives it.

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| P1 | one canonical origin per region | a region prints under several names with several derived origins (`T16F9[1 + t1]` / `T16F9[2 + r4]` / `T16F9[y]` are one array; so are `T175D`/`T1761`, `T1875`/`T1876`/`T188A`, `T17FB`/`T17FC`, `T1826`/`T1839`), and the header's "2-based, read at `$16F7,i`" names neither the base nor the basedness a reader can index by. Pick the origin once (`regions._origin` already computes it), record `base` and `first_index`, and normalise every index expression to it | small | on the four GT2/Commando/JCH/SW prints, every read of one region prints `T[e]` against one stated base; a test materialises the GT2 wavetable from the print alone |
| P2 | fold a carry the reaching compare proves | `a38 = ((T175D[y] + freq_lo_idx) + (T16F9[y] >= $E0)) & $7F` re-derives a carry as a predicate the reader must evaluate; `$12CD CMP #$E0` / `$12CF BCS` proves it 0 on that path. Constant-fold `carry(site)` where the reaching compare decides it; keep the named form (§4.11's producer/consumer pair) only where it is live | small | GT2's five re-derived carries fold to constants; Hubbard's `$5237` inherited carry stays named; recert-neutral |
| P3 | print an untaken arm's body | `p_1082` prints from `# $108B` with `# untaken: T1851[y] >= 0` and drops the two instructions the arm holds — here `LDY #$00 ; STY $FD`, which is what makes the vibrato depth 8-bit. A second build of the same player may take the arm, so a transliteration that must render both needs the semantics either way | small | every `untaken` marker carries its arm's statements, marked; the GT2 print gains the 12 (15) arms §3 of prototype-goattracker.md counts |
| P4 | state `commit_order` in the certificate | the per-voice edge-register order is recovered (the stores are named `ghost[x/7].ad` etc. and their order inside a routine is in the IR) but appears nowhere a reader can use; §3.1 of prototype-trackerprog.md needs exactly this one datum per tune | small | `certificate.json` carries `commit_order`; the six certified families' values match §3.1's table |
| P5 | dispatch on the command number, not the patched address | the tick-0 and continuous dispatches print as `switch b1295: case $1006:` — the compiled form. The command's *number* is the index into `T144A` the block above computes, so the two GT2 builds label the same command with different addresses | medium | the GT2 prints' two switches are over the index; the arms of the two builds are comparable line for line |

P1–P4 are small and independent; P5 wants the switch's index recovered from
its writer, which `resolve` already closes statically (prototype-goattracker.md
G5). None changes a certified program, so all five are recert-neutral.

### 6.1 The one the two families found together

Not a print gap: a schema one, and it is already fixed in §3.6 rather than
tracked. Recorded here because the *method* generalises.

`Event.note: index | rest | hold | keyoff | keyon` was the source byte's own
token class, not the music. GT2's `$BD`/`$BE`/`$BF` sit in the note range and
make the enum look right; SID Wizard's note column also carries `set vibrato
amplitude`, `porta`, `sync on/off` and `ring on/off` (anatomy:1204), which makes
it obviously wrong. The anatomy already names the construct as an idiom to be
spent (anatomy:2833, "byte ranges as token classes"), so the enum was the one
place the schema kept a packing it elsewhere removes.

The field the two families forced is `sounds`. Before it, the universal player
answered "does this row key a note?" from `gate == "on"` for Hubbard and from
`note is not None` for GoatTracker 2 — one fact, two computations, in one
procedure. **A second family is what makes that visible**: with one exemplar
either spelling is self-consistent. The check worth repeating on the next family
is mechanical — grep the player for every expression that decides the same
musical question, and require there be one.

### 6.2 Two more the second family found

Same shape as §6.1: things one exemplar could not show were wrong.

**A command named by its dispatch index.** The GoatTracker 2 object interned its
row commands under the nibble `T144A` indexes them with — `F:07`, `8:04`, and an
`id` field carrying the nibble itself. `prototype-goattracker.md` G5 and
anatomy:2799 both class the patched low-byte jump as an idiom the lift spends,
and prototype-goattracker-trackerprog.md §2 claims the two jump tables
disappear; keeping their index as the command's *name* kept them. Commands are
now named by what they do (`tempo:07`, `stream.wave:04`, `sr:A4`), which also
makes the two builds' commands comparable — the same music names the same
command whatever page the handler landed on. SID Wizard's `BIGFXTABLE` index
(anatomy:2799) was the same trap waiting on that family; §6.3 records that it
sprang the same way and what avoiding it cost.

**Whether a command outlives its row was implied by the clock.** GT2 re-runs the
last command the score gave at every row boundary (effect memory); Hubbard
spends it on its row. The player got this right by accident — the holding lived
in the countdown branch of the sequencer, so "countdown-clock families hold their
commands" was load-bearing and untrue in general. It is now `meta.row_command` ∈
{`held`, `spent`}, read by one procedure on both paths. The generalisable check
is §6.1's: one musical question, one place that answers it.

### 6.4 What reading the three together found

With Hubbard, GoatTracker 2 and SID Wizard all on one player, the object could
be audited the way §1 asks — every schema row against two certified families —
and the answer was that the *player's* growth, not the schema's, was the
measure. `meta` carried 12 keys at one family, 18 at two and 22 at three, and
15 of them were branch points; §9's genericity gate claims two marked
single-family rows and the audit found twenty-one unmarked ones.

Five reductions came out of it, each certified at 0 divergences on all seven
tunes. Measured together: the union of `meta` keys across the three families
26 → 21, the keys the player *branches* on 15 → 10, two row procedures → one,
three mechanisms for "run a stream at a point in the tick" → one, two guard
spellings → one, at the cost of 14 lines in the player.

**A reduction in §2 is not two forms in §4.** `meta.commit ∈ {order, acts}` let
a family collapse a tick's edge writes. Rendering the acts sequence for the two
families that do not need it is write-for-write identical over their whole
horizons — Hubbard 11,780 ticks, GoatTracker 2 12,000 × 2 — so the datum
distinguished no observation and the branch was deleted. The generalisable
check: before a schema row admits a second form, render the first for the
family that has the second and count the ticks that differ.

**A hook is a phase with a name, and names do not compose.** Five meta keys
attached a stream to a point in the row (`note_row`, `gate_row`, `pitch_row`,
`row_sets`, `row_commits`) and two more to a point in the tick
(`tempo.early_first`, `meta.voice_exit`), while a *third* mechanism — a stream
with a `rank` and a `when` — already existed and was the general one. Worse,
one name meant two things: `note_row` fired at the note-on in GoatTracker 2 and
at every row in Hubbard, because the two families ran different row procedures
(`latch` and `row`) that also differed in capability — `latch` applied no
orderlist transpose and ran its commands before the note rather than after.
`meta.row` and `meta.tick` are those seven keys and two procedures said once.
The generalisable check: two procedures for one musical act is the §4.8 failure
one level up, and a hook per call site is how it gets there.

**An ordering said twice will disagree.** The commit kept three lists so a
prelude could be emitted ahead of the tick's producers — an ordering the tick
list also states. They disagreed: GoatTracker 2's prelude runs *after* its
machine and must win the register a held `sr` command wrote, and the fixed
first list gave that register to the command. One list, and the order is the
order.

**A sigil that means two things is a grammar, not a shorthand.** `@name` was a
voice cell in every assign target and a *shadow register pair* in an
accumulator's `cell`. One vocabulary — `tick`, a voice cell, `#global`,
`ins.pw`, `shadow.<pair>`, any with `.hi`/`.lo` — retires the collision,
`voice.freq`/`voice.freq.hi`, and the four hard-coded half-names. `tablestep`
went the same way: it is `interval(n) >> shift`, and the grammar already had
`shr`.

**A guard with two spellings is two guards.** A stream row carried `when`; a
command's set, an instrument's set and a global commit carried theirs as a third
element of the list beside the value — 39 of them in SID Wizard alone. A
command's writes are now rows of the same inline stream an instrument's note-on
and a prelude are, and one procedure runs all three. The generalisable check: a
guard that lives in a tuple position cannot be read by anything that did not
already know the tuple's shape.

**And grammar with no exemplar is not grammar.** §3.3's terminator (written by
every tool, read only by the print), §3.6's nine named commands (of which the
three families emit none — the record they do emit is smaller and more general)
and `for`/`call`/`ret` in `Order` (Galway and Follin, both prose-only) are
struck. The generalisable check is §1's own rule applied to the *print* as well
as the player: a row nothing renders is a row nothing tests.

### 6.3 Three more the third family found

[prototype-sidwizard-trackerprog.md](prototype-sidwizard-trackerprog.md), same
shape again — with the difference that two of the three are things the *first
two* families had made invisible rather than merely unresolved.

**§2 rule 1 was being collapsed, and only a family without a shadow could show
it.** The player took the last value per edge register and emitted the three in
`commit_order`. For a family whose writes go through a ghost flush that is
exactly right and unobservable; for one that writes the chip as it goes it is
wrong, and SID Wizard's note-start tick writes `AD` from the instrument and again
from the row's own `attack` effect. The tick is therefore always a sequence of
acts, one act per thing the tick did, `commit_order` ordering that act's own
edges — and rendering it that way for the two families that do not need it is
write-for-write identical over their whole horizons, so no datum selects the
form. Measured: collapsing them diverges on 500 ticks of *Emomyst* and 44 of
*End of the World*. The generalisable check is that a *reduction* in §2 is not a
licence to reduce in §4 — the player must produce what the rule compares, and
only a family that exercises the rule proves it does.

**`meta.shadow` did not need widening; it needed asking.** The expectation was a
"partial shadow" — SID Wizard ghosts FREQ/PW/WF and writes AD/SR and the filter
directly (anatomy:1236) — and a `meta.shadow` that names which registers pass
through it. Reading the two binaries says there is no flush in either: a ghost
register here is a *cell* a producer reads on the tick that computed it, which
the schema already has. A shadow is a register file a tick **defers**, and a
family that defers nothing has none. The generalisable check: before widening a
field for a family, confirm the family has the thing the field is about.

**Naming a command by what it does has a price, and it is worth paying.**
`BIGFXTABLE`'s 31 words, `SMALLFXTBL`'s 14 and `NOTEFXTBL`'s 8 all disappear —
no command in the object is named by an index, and the tables are read only to
ask whether this build's exporter compiled the handler in (a bare `RTS` makes it
`nop`, and the score keeps the byte). What that costs is that three of SID
Wizard's effects have the *same encoding in two columns*: the note column's
`$60–$6F` and small effect 8, the instrument column's `$40–$7F` and small effects
4–7, and `arpeggio.speed` as small effect C and big effect C. A score naming them
by what they do cannot say which byte carried one, so the byte-for-byte round
trip reads the row's *shape* — the bit-7 continuation the layer spends — off the
tune and every value out of the object. §8 of prototype-trackerprog.md already
says the trackerprog is *a* preimage; this is the first exemplar where that is a
measurement rather than a caveat.

### 6.5 Four more the fourth family found

[prototype-defmon-trackerprog.md](prototype-defmon-trackerprog.md), same shape
again, and this time §6.4's own five checks were applied *before* anything was
added rather than after. Two of the four below are things the audit's checks
caught; two are things only a family this unlike a tracker could show.

**A field that names a set must name the set, not its size.** `meta.shadow` was
`{registers: N, order: descending|ascending}` — a count and a direction, which
is a *description* of a register set that happens to be a prefix in one of two
orders. defMON's write-out is per voice and skips two registers in the middle,
so no count and no direction reaches it. `registers` is now the ordered list the
flush writes, and §6.4's first check was run on it: GoatTracker 2's list is
`range(24, -1, -1)` and its two builds render **write for write identical** over
their whole horizons, so the count was the list said less, and the general form
costs the family that had it nothing. The generalisable check is §6.4's, with a
corollary: a field whose value space is "a prefix, forwards or backwards" is a
set described by an accident of the families that have been read so far.

**Where a write lands is a property of the register, not of the family.** defMON
defers its voice image and writes its cutoff to the chip in the middle of the
same tick. The reflex is a second datum — a per-register flag, or a second
commit list. The rule that carries both is one sentence with no new field: *the
image holds the registers the flush names, and a commit to a register the flush
does not name reaches the chip where it is made.* GoatTracker 2's filter
registers are in its flush and still defer; defMON's cutoff is not and does not.
Deferring it diverges on 12,540 of 20,000 ticks. The generalisable check is
§6.4's ordering one: when a family seems to need a flag, look for the datum it
already has that answers the same question.

**A vocabulary is only one vocabulary if every reader and every writer uses it.**
#310 gave `Acc.cell` one vocabulary and stopped there: the expression reader
`{"cell": …}` still knew only voice cells, and no `sets` target could name the
image at all. That is not a smaller vocabulary, it is *three* — and the third,
"write it as a producer and let the commit place it", is a **different tick
position**, so a family whose accumulator reads back what a stream row just
wrote silently reads the previous tick's value. defMON's pulse-width sweep is
exactly that, and it is a wrong width 132 ticks in rather than a type error.
`Player.cell` and `assign` now go through the same `whole`/`store_cell` pair
`Acc.load`/`Acc.store` use. The generalisable check: for each name space the
schema declares, grep for every *reader* and every *writer* of it and require
one implementation, not one spelling.

**A dead arm is a dead arm, and a decision made twice is two decisions.** Two
smaller ones, both caught by rendering rather than reading. `row_consumes_tick`
had three values in the schema and two in the player — `false` reached
`guards(None)`, which is vacuously true, so the one family to write it got
*always* instead of *never*; every earlier family wrote `true` or a guard list,
so the arm was dead code in a 1,009-line player. And `Acc.gate` chose its arm by
evaluating `step_when` a second time, after the store: for the one family whose
`step_when` reads the cell its own step moves, that is the opposite answer. The
generalisable check for both: a value the schema admits and no exemplar writes
is untested, and a guard evaluated twice against a moving cell is two guards —
§6.1's "one musical question, one place that answers it", applied to the
player's own control flow rather than to the object's.

**A citation fell, and a second one nearly did for the wrong reason.** §3.5's
"the sidTAB row *is* the instrument" is wrong: a voice runs two sidTAB programs
at once, so no single `Event.ins` names them and both are §3.6 `point` commands;
defMON's one `Ins` has neither `adsr` nor `prelude`, the first of any family with
neither. §5's pulse-run row names defMON as the second family for
`+ carry(site, flag)`, and the first measurement said it was wrong — the carry is
0 on every sweep step of *Jazzpjazz*'s whole horizon **and of *Automatas*' first
20,000 ticks**, a longer prefix than any other exemplar's whole horizon. Over
*Automatas*' whole 149,025 the carry is set on 9,144 of 170,702 steps and
dropping it diverges on 44,675 ticks, so the row is two-family after all. The
generalisable check, and the sharpest one this family produced: **a poison
measured on a prefix is not a poison.** §9's acceptance #1 already says the whole
certified horizon for the certificate; it goes for every count a document draws
a conclusion from, and this is the first exemplar long enough for the difference
to bite.

### 6.6 Four more the fifth family found

[prototype-jch-trackerprog.md](prototype-jch-trackerprog.md), same shape again,
and this time §6.5's own six checks were applied before anything was added.
**Two of the four below are things the checks took back out** — fields the
family was expected to force, added, measured over the whole horizon and struck.

**A datum can be a property of the *frame* and not of the tune.** `commit_order`
is one permutation per tune and `meta.shadow.registers` one ordered list per
tune, and both readings survive four families because in all four the tune
writes its registers the same way every frame. The Puterman build of JCH does
not: its wrapper flushes the same 25 registers **low to high** on a frame whose
own delay byte is zero and **high to low** on one where it is not, and both arms
are taken — 3,887 frames and 4,689. §2 rule 1 keeps every `ctrl`/`AD`/`SR` write
in tick order, so the direction is observable on every tick: fixing the flush low
to high diverges on 4,689 of 8,577 and high to low on 3,887. A flush entry may
now state the guard the image writes it under, which is the shape
`globals.commit` entries have had all along — and a bare register is the entry
with no guard, so the four families that had a plain list are unchanged, object
and render both. The generalisable check: before making a field one datum per
tune, ask what varies *within* a tune; a family whose data drives its own
write-out is where "one per tune" stops being true.

**Two fields, added because the player has them, and worth nothing.** V20's
prefetch skips the pulse, the filter and the vibrato on the row step it reads,
where a build byte says so; and its row commit copies a *staged* instrument
byte, not the row's own. Both are real things the player does, both were
foreseen, and both were built. Measured over the whole horizon of the build that
has them: the effects skip diverges on **0 of 8,577** — that build's pulse
programs are self-loops with a zero step and its cutoff is overwritten by the
wrapper before it reaches the chip — and the staged instrument on **0 of 8,577**
and **0 of 2,401**. Both are struck, and with the first goes a `meta.prefetch`
field that existed only to serve it. The staged *note* beside them survives the
same measurement at 8 and 397 ticks and stays. The generalisable check is
§6.4's, sharpened: rendering the general form and counting is not only how a
second form is refused, it is how a *faithful* form is refused. Being what the
player does is not the test; distinguishing an observation is.

**A shadow hides more than `commit_order`.** §6.5 recorded that a family whose
writes go through an image cannot tell `ad` from `sr`, and defMON measured it at
0. The second such family measures it again at 0 — and adds one: **voice order**
is invisible too. Committing JCH's voices 0, 1, 2 instead of 2, 1, 0 diverges on
all 2,401 ticks of the build with no image, because the three voices write the
one global channel's registers and the last one wins; through the other build's
flush it diverges on 0. Two data of `meta` collapse to nothing under one form of
`meta`, and the object still has to carry them, because the *other* build needs
both. The generalisable check: a datum that is unobservable in one build of a
family is not a datum the family does not have.

**A citation fell, the second one to.** §5's `links` row named JCH's re-trigger
arm as its second family — "re-points the pulse cursor **and** reloads the pw
accumulator from the stream row in one step". It is not a `links`: it is the
instrument's `on_note`, which §3.5 already makes one inline §3.3 stream, and a
`point` beside a `sets` in one act is what that stream *is*. The object uses
`links` nowhere. `links` keeps GoatTracker 2 and the hermetic clamp snippet, and
the row is marked back down to one certified family plus a snippet. The
generalisable check, which is §1's own rule read backwards: a citation written
from a *reading* of a family is a hypothesis until that family is transliterated,
and two of the five transliterations so far have taken one out.

### 6.8 Five more the sixth family found

[prototype-follin-trackerprog.md](prototype-follin-trackerprog.md), the first
family whose *score* is a program rather than a list.

**A row nothing renders is a row nothing tests — and the converse is a debt.**
§6.2 struck `for`/`call`/`ret` from §3.6's `Order` because they rested on two
prose-only families, with the promise that "when a score-as-program exemplar
lands, the grammar gains what that exemplar shows and no more". It landed, and
the grammar gained five steps: `call`, `ret`, `mark`, `loop`, `jump` — 858 of
them across 32 subtunes, beside 46 `stop`s. Two spellings the strike could not
have foreseen and the exemplar settled at once: a **call names where it comes
back to** (the machine pushes an address, and the block list's order is not the
program's), and **`mark` and `loop` are two steps, not one `for`** (two bytes in
two places over one counted-loop register per voice, so the object says the
loops do not nest by having one cell). The generalisable check: striking a row
for want of an exemplar is right, and it is a debt with a stated shape — what
comes back is not the row that went, and the difference is the exemplar's
whole content.

**A loop the other families run once is free by construction, not by
measurement.** The fetch became a walk — several rows at one boundary, because
this family's sequencer runs until a note arrives. The risk was the *group*: §2
rule 1 keeps every edge write in tick order, and a family with no shadow writes
as it goes, so the walk has to flush between rows. Flushing *after* each row
would have made a one-row family one flush where it had none, and would have
needed measuring on all eleven earlier builds. Flushing **between** two rows and
never after the last makes a one-row family bit-identical *by construction* — it
never reaches the flush at all. Measured anyway, and 0 of 236,586 ticks. The
generalisable check: when a general form must not disturb the families that do
not need it, place the new work where their control flow does not go, and the
measurement confirms rather than decides.

**A terminator can belong to a voice.** Every certified score before this one
ends the tune. `$86` ends a *voice*: its flag clears, the routine moves to the
next, and the filter goes on writing forever. So `stop` is per voice, `stopped`
is a seeded per-voice list, and a stopped voice runs no clock at all. It is not
an edge case of a song that ends — it is the ordinary state of a sound effect,
which starts one to three voices over whatever was playing; starting all three
of subtune 20's instead diverges on all 20,049 of its ticks. The generalisable
check: a terminator's *scope* is a datum, and the family that shows it is the
one whose entry starts fewer voices than it has.

**Before or after the voices is a property of the channel, not of the player.**
`globals.streams` stepped the one global channel ahead of the voices, which is
right for a channel the voices read. Follin's they write — the owner voice's
note-on reloads the cutoff the filter then sweeps — so the same list in the same
place writes the un-swept value on 383 ticks. `globals.after` is the second
list. The generalisable check is §6.4's ordering one again, one level up: when
a phase's position is right for every family so far, it is a default and not a
law, and the family that breaks it is the one whose voices *write* what the
phase reads.

**Two anatomy facts fell to the render that no census found.** The anatomy's
static census of this player counts `$93` (skip transpose) at **0** and calls
its cell unused; subtune 7 uses it three times, and rendering it as a no-op
diverges on 2,575 and 4,155 ticks. And `$8D` is documented as setting the pulse
mode "at next note"; it stores the same byte to the running mode as well, worth
2,260 ticks. Both are one instruction apart from what the document says. The
generalisable check: a static census over a player's data is a lower bound on
what its tunes do, and the render is what settles it — which is the same reason
§6.4 measures rather than argues, applied to the *source* rather than the layer.

### 6.9 Three the seventh family found

[prototype-blackbird-trackerprog.md](prototype-blackbird-trackerprog.md), the
first family whose object cost the player nothing at all.

**A comparison's `dropped` list is a claim about the code, and nobody had
checked it.** §2 drops "order between voices inside a tick", the certificate
prints it, and `certify.divergence` splits the edges per voice before comparing.
`attest` — the harness every hand exemplar certifies through — printed the same
list and compared `TickObs.edges` as one flat tuple. Six families never noticed,
because a player that finishes one voice before starting the next produces the
interleave the universal player does. The seventh runs a tokenizer pass over all
three voices and then its audio engine over all three, and diverged on the first
hard-restart tick with per-voice edge lists that were *identical*. The
generalisable check: **a boundary a document states and a comparison does not
implement is prose**, which is §5's own lesson about `bound.interval` (P2) read
one layer out — and the family that finds it is the one whose player groups its
work differently from yours. All fourteen earlier builds re-certify and not one
loses an identical tick, which is what says the weakening hid nothing.

**A datum can be coarser than its name.** `meta.commit_order` is a permutation of
`(ctrl, ad, sr)`, six values, and every earlier family's tune picks exactly one.
This one's note-on writes `sr`, then `ad, ctrl`, then `ad, sr` — three acts in
which `sr` and `ctrl` never appear together — so **two** of the six render it and
four do not: the object's content is "`ad` comes first", not an order. The
generalisable check: a schema row measured to more than one value is still a row,
and the poison sweep is what says how much of it a tune actually spends. Four
other forms measured to **0** here and are stated as such rather than dropped
(voice order, the filter's position, the two write-only streams' ranks, and a
carry the cursor's range makes provably zero).

**A packed rest and a held row are not the same thing.** §3.6's `dur > 1` in the
prefetched path is a row the fetch spends and never applies — a packed rest,
which is what GoatTracker 2's `$C0+n` and SID Wizard's `$70–$77` are. This
family's delay token looks identical in the byte stream and is not: its `execute`
runs on every row it covers and does nothing only because two cells are zero. So
every row cycle is one event of `dur` 1 and a held row is an event that says
nothing, which is §6's materialisation taken literally — 6,255 rows carrying what
7,579 token bytes said, and no decompressor, no ring buffer and no delay token in
the object. The generalisable check: **before reusing a form, check what the
program does on the rows it covers**, not what its bytes look like.

### 6.10 Four the eighth family found

[prototype-walker-trackerprog.md](prototype-walker-trackerprog.md), the first
family whose modulators are unrolled by modulator rather than by voice.

**A turn is exact only where the cell is one modulator's.** §5's `reflect` reads
the triangle's turn off the accumulator's own value, which is right for seven
families and undecidable for the eighth: Walker's pitch triangle (step `$0A`)
and pitch bend (step `$50`) both add into one 16-bit frequency offset per voice,
and on **1,140 of the horizon's 9,949 modulator steps** both have moved it. No
interval on that cell is either modulator's swing. The player's own turn is and
always was a counter — `++phase; if phase == period: phase = 0; dir ^= 1` — so
`amplitude` gains `{count, cell}` beside `{interval, shift}`, fifteen lines
including the move of the direction flip onto §5's `whole`/`put` pair so a
modulator on the global channel can count in a `#global`. The strike is a check
and not the argument: no earlier object writes `count`, so the arm is
unreachable for all of them by construction, and all fifteen earlier builds
re-certify at 0 divergences over their whole horizons. The generalisable check:
**a form that reads a shared cell must be told what is its own**, and a
projection written from prose (§7's "policy `reflect` (triangle) or `halt`
(one-shot)") is exactly where that goes unnoticed.

**A one-shot is a guard, not a policy.** The same prose row projected `policy
halt` for the bend that stops one step short of its period. The certified
reading is `delta_when` on `phase + 1` against a cell loaded beside the period —
which costs the player nothing, generalises to any stopping condition the object
can name, and stays correct when a drum record overrides both the period and the
stop. The generalisable check: before adding a policy, ask whether the guard
channel already says it.

**A modulator on the global channel is the same record.** The filter is the
fourth copy of the one template and runs once per call after the three voices.
It needed no new form: `globals.after` already steps a stream after the voices,
`stream_step` already steps an `Acc` a row's `run` names, and §5's cell
vocabulary already resolves `#global`. What it needed was for the *turn* to use
that vocabulary too, which is the same fifteen lines. Its fire is any voice's
note-on and its reset is the **owner** voice's, which is one global cell the
block header writes and the gate reads.

**A header that re-arms every voice makes a block a pattern.** Walker's block
header forces `reload` on all three voices before any of them reads a token, so
whether a note re-triggers or ties cannot depend on the order step that plays
the block. Eleven blocks therefore carry thirty-two steps and the score states
2,592 played rows as **1,134**. The generalisable check: **what a header resets
is what says whether a pattern is reusable**, and it is cheaper to read than to
compare rows.

### 6.7 What hardening the layer found, with the families all in

Eight packages over the object and the player, after the fifth family and before
any sixth. Every one is measured the way §6.4's first check asks — render both
forms and count the ticks that differ, over each build's **whole** horizon — and
every one lands at **0 differing of 243,265** across the eleven builds, because
none of them is a change to what the layer plays. The full rows are
[prototype-trackerprog.md](prototype-trackerprog.md) §7; the checks they add are
these.

**A field the object writes and no consumer reads is not a field.** §6.4 struck
§3.3's terminator from the *grammar* and four tools went on writing it, and the
print on rendering it, for two more families. Striking a row from the schema is
not striking it from the object, and the way to know is to grep for the readers
of every name the schema declares — the player's, the print's and the round-trip
tests' — and to delete what nothing answers. Eight fields went that way (P1).

**An invariant the renderer does not assert is prose.** §5 has said since the
first draft that "the trackerprog states each interval and the renderer asserts
it", and the renderer did not: `bound.interval` was read as one policy's
threshold and `from` and `witness` were read nowhere. Turning the assertion on
took **five of the sixteen accumulator records** out — not one of them a bug in
the render, every one a claim the object was making falsely, and one of them a
correction §5 had written in prose in 2026 and the object had gone on
contradicting for two more families. The corollary is the second half of the
same check: **the interval a *step* reads is not the interval a *record*
claims**, and one key cannot be both (P2).

**A constant in the player is a family in the player.** Three values sat in the
one procedure that is supposed to have no family in it: a voice cell the player
*declared* that was one family's pulse direction, a `- 1` the player did to one
family's reload and to no other value in the schema, and one musical question
answered at two sites. Each goes to where its own fact lives — the tune's
`state0.cells`, the stream row's own value, and the one place that answers it —
and what stays a constant is named beside the chip's others, because no family
varies it and by §6.4's check a datum no observation distinguishes is not a
datum (P3).

**An enum is a hook list that has not noticed yet.** `meta.prefetch` grew one
string value per family across three PRs, which is §6.4's own `note_row`/
`gate_row` failure one level down: five of its seven values did what a `sets`
row already does under a guard the grammar already had. A staging *is* a row
program; the only thing that made it not `meta.row` was when it runs (P4).

**A token class the layer has not spent is a token class the layer will pay
for.** §3.6 spends the note column's byte ranges — "a value that is not in the
pitch table is not a pitch" — and one family's wave table still carried the raw
bytes and read the three kinds back out of them every tick, with the assembly's
own `CMP` immediates as the guards. The bytes are constants of the table and the
other three families decode theirs at build time (P5).

**One machine fact, one spelling.** The 6502 carry had three: a delta form the
object never wrote, a bit of an unmasked sum written by two families, and a
`+ 2^w` bias tree whose whole content is that Python's shift on a negative
number is arithmetic and the machine's is not. The bias belongs in the player;
the two nodes that remain, `carry_out` and `borrow_out`, say what they are and
are greppable (P6).

**Three procedures for one clock is §4.8's failure with a `form` field on it.**
`meta.tempo.form` selected three procedures in `clock()` and three more
elsewhere; the counter is the general one and the other two are values of it, a
divider being the rate with a step of −1 and a countdown a step of −1 with a
reset. The tempo-over-a-stream record went with them, because it was one more
reset clause all along. The generalisable check is §6.4's ordering one read
backwards: **when three names select three procedures, one of the three is the
form and the other two are its values** (P7).

**And an interpreter that dispatches per reading can be compiled per object.**
The object is fixed for a render, so every expression, guard, target and plan is
compiled on first reading and called thereafter — 2.12× over the eleven builds'
whole horizons, with the write lists identical tick for tick. The check the
package leaves is the honest one about its own target: it aimed at 5× and the
profile says the remaining cost is flat across four procedures at 5–10 % each,
so the next factor is generating source per object, which would cost the layer
the thing it exists to have — one fixed procedure a reader can hold against §4
(P8).
