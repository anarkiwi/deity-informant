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
| 3–5 | Galway, Walker, Blackbird evidence | prose-only families; none certified (arch §9.2) |

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
| W10 | producers over T1's accumulators | replace a row's pitch and pulse lanes by `Producer`s the player steps from T1's `Acc` records (delta, bound, policy, rate, phase, `carry(site)`), armed by the row (`arm(acc, overrides)` for the command parameters) and reset by note-on; the filter as an instrument-armed stream | large | GT2 ×2, Commando ×2, SW ×2 at 0 divergences with `xz` below the source's |

Total ≈ 24–30 agent-days. W1–W3 are independent of W0 and of each other;
W4 depends on W3; W5 on W0, W2, W4; W6 on W0, W2, W3; W7 on all.

## 5. Execution order

1. **W1 + W2 + W3** in parallel — small, recert-neutral, each its own PR.
2. **W0** — the doc revision, reviewed against §2/§3 above; it fixes the
   `Acc` and `Cmd` shapes W5/W6 build to.
3. ~~**W4**~~, then ~~**W5**~~, and ~~**W6**~~ (on GT2/JCH/Commando; SW refuses
   by name until the fold fix).
4. ~~**W7**~~, ~~**W8**~~ (JCH ×2, GT2 ×2, Commando ×2 certified at 0
   divergences), SW ×2 after #303; ~~**W9**~~ (four of eight prints below
   the source's); then **W10** for the rest of the compression claim; Follin
   after I6.

Deliberately not now: Galway/Walker/Blackbird (uncertified — the anatomy
describes them, no certificate covers them), multispeed (§10). **Not** defMON:
that line was wrong. defMON is certified four times over — `automatas`,
`automatas-6581`, `automatas-8580` (149,025 ticks, period 129,024, `complete`)
and `goto80-jazzpjazz` (1,799 ticks, `horizon`), architecture §9.2 — with recert
dirs for all four and its own [prototype-automatas.md](prototype-automatas.md).
W0 puts it in the acceptance list.
