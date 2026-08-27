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
| 3.3 | JCH `rec6` pulse `[init, Δ, dir\|frames, next]`, `rec7` filter | shapes swapped: `rec6` 4 columns, `rec7` 3 (prototype-jch:82,106-107) |
| 3.3 | `timer_4` compare is the GT2 wavetable hold | `timer_4` is a field name with no role (prototype-goattracker:84); the print confirms the mechanism (`gt2…md:551`) but the cited doc does not |
| 3.5 | hard restart is one fixed shape | SW 1.6 writes AD,SR and 1.9 SR,AD (anatomy:1232-1233); Blackbird has no TEST; Walker/Galway do intra-tick gate/TEST edges (anatomy:137-140,214) — observable under §2 rule 1, inexpressible in `{early, ad, sr, first_ctrl}` |
| 3.7 | `CKBDTRK` is a `tablestep` term | it adds an **absolute** `FREQ[$E + idx]` entry (prototype-sidwizard:110-118), not a difference |
| 5 | Commando pulse run = 16-bit wrap | 8-bit add on pw-lo (commando-floor:222-224); and its carry is **live from the vibrato block** (`commando-song1/tuneprog.md:394`) — `delta const(k)` refuses it |
| 5 | GT2 depth `T1851[y] & $7F` | that is `speedcmp`; depth is the right byte (anatomy:876) |
| 5 | `p_109E`, `p_10AB`, `p_10F5`, `T1864`, `portaval`, `pulsedir`, `pulsedelay` | names absent from the cited prototype docs (they are print/anatomy names; commando-floor uses `porta`, `pwdir`, `pwdelay`); `$10AB` in the anatomy is an SMC immediate |
| 5 | Hubbard porta = tone portamento, clamp(pitch[target]) | no target: free ±step ramp (commando-floor:236-238); it is the "free slide" row |
| 5 | pulse-sweep state is instrument-scoped | `pw` yes; `pwdir`/`pwdelay` are per-voice (commando-floor:226-228) |
| 5 | skydive | dead in the exemplar (`trap 'untaken'`, commando-floor:247) |
| 9.3 | "measured like §6.2" + `xz -9e` | §6.2's six are tokens/lines/statements/blocks/header rows/data rows; `xz` is §8.3. Architecture §11 requires §6.2's six verbatim |
| 1 | 91.6 % "voice-stride state appears" | 91.6 % weighted of tunes with ≥ 50 % voice-like indexed sites (arch:940); the summary line says 90 % |
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
edits.

## 4. Work packages

| # | item | mechanism | owner | size | acceptance |
| --- | --- | --- | --- | --- | --- |
| W0 | schema revision of the prototype doc | settle I1–I15 and §2's corrections: per-voice ordered edge list with a stated emit order; `Acc.delta` admits `+ carry(site)`, `repeat(Δ, n)`, `tabcell`, `sext11`; `Acc.phase` may name another `Acc` or a cell or `fn(global_counter)`; `Acc.bound` carries `proved\|projected\|observed` and a projection witness (Commando `hi & $F`); `Acc.target` admits `split(k, 8)` (SW cutoff); `links` for cross-Acc resets (GT2 snap zeroes the vibrato phase); one `rate` meaning; `set_register(reg, v)` or a Follin refusal; a horizon terminator in `Order`; `period: 1` handling; Commando's per-tick input | docs | small | every §2/§3 row cites two certified families or a survey count; the §2 table above empty |
| W1 | one observable reduction | `grid.reduce_tick(writes, prev) -> TickObs(edges, values)` + vectorised `reduce_run` over the existing `grid.grid`; constants `CTRL/AD/SR/PAIRS/LEVEL`; `grid.changes` factored out of `ghidra_facts._tick_writes`; `Verifier` gains an opt-in `obs` accumulator after `_compare` (`verify.py:336`). `verify._compare` stays raw — mirror folding, the PW nibble and the cutoff mask must not reach it | grid, verify, ghidra_facts | small (1.5–2 d) | recert 51/51 field-for-field (`compared`, `divergence` untouched); hermetic tests: gate 1→0→1 keeps three ctrl entries, `$D401` double write last-wins, `freq_lo`-only tick carries `prev` hi, PW nibble masked |
| W2 | `history.py` | `history(prog, trace, names_doc, calls) -> {name: ndarray(ticks)}` over `Verifier` (`run_init`, then `_one` per tick, promoted to `tick()`), `np.frombuffer(M.m)[idx]`, u16 widening from S6 `u16`; sparse-stride regions sampled by `Region.addrs`; library + `tools/`, **not** a pipeline artefact | history, verify | small (1 d) | hermetic: `counter("INC cnt")` history `[1..8]`, PERIODIC snippet periodic at the cert period, a u16 pair widens; all 51 recert dirs replay with 0 divergences |
| W3 | S6 exports T2 needs | serialise `facts.index`, `cellindex`, `idxvar` and the base-pointer relation; name record-split fields `cursor` where `cellindex` says so (`views._named_fields`); `Names.from_dict` | facts, recover, views | small (1.5 d) | the score cursors of GT2/JCH/SW/Commando named `cursor` in S6; recert prints listed line by line where they move |
| W4 | T0 provenance | `provenance.py`: roots = `io` stores in `$D400..$D418` **and** stores into `names.image` regions (rekeyed by the flush delta); `(register, voices)` from the store's `lo/hi` envelope (more robust than `cellref.voiced`); backward substitution via `irwalk.single_defs`/`expand` stopping at a named role; leaves named through `cellref.Cells`; `ir.enc` for the expr (add `R16`/`W16` to `_NODES`); `tuneprog.T0.json` per write site with `direct`, `self_update`, `refusal`. Region ids are the presentation view's — carry `(base, size)` | provenance, ir, pipeline | medium (3 d) | every io/image write site of the 42 recert dirs is a named expr or a stated refusal; the record's `print` re-renders to the `tuneprog.md` line |
| W5 | T1 `accum.py` | candidates from `facts.cellupd` reaching an io store (W4); `Delta`/`Dir` parser (`idioms.bit`, new `sext11`); a diamond over `Store`/`Call` arms (new — not `gated.diamonds`); the variable-shift loop `x >> cell` recogniser `loops.py` lacks (`tablestep`, GT2 `p_12E5`, Commando `$51E4`); guard walk over dominators → policy; bound from guard (`proved`), projection (`projected`), or history under a period witness (`observed`); two verifiers — interval assertion and **recurrence replay** against W2's history, divergence ⇒ `unclassified update` | accum, idioms, loops | medium–large (5.5–7 d) | hermetic snippet per policy (`wrap reflect-complement reflect-dircell clamp halt reload rate tablestep split`), refusals named with the cell; exemplar regression: GT2 vibrato+porta, Commando bounce+run+porta, JCH pw/cutoff, SW cutoff classified as W0 states |
| W6 | T2 `trackerprog/{cursors,streams,score,pitch,refuse}.py` | cursor × history: successor relation at a fixed base → step/jump edges, rows, loop row, terminator byte, holds; nest through `names.u16` bases (depth ≤ 2 else `score not cursor-shaped`); Follin call/ret/for from the dispatch arms + the depth-1 return slot; `pitch` from `names.freq` + per-accessor origin (Commando reads `FREQ` at two bases); materialise over the horizon. **Blocker**: SW's orderlist load is erased by the copy fold (`p_17C8` prints nothing, `T1C40/T1C4E/T1C5C` have no accessors) — either `copyview` keeps the load or SW refuses | trackerprog | large (8–10 d) | goldens on GT2 (33 pattern ptrs, 9×30 instruments, `T16F9`), JCH (26 ptrs, `rec8[19]`, 3 `$FF`s), Commando (`T576B`, `T5889`, `rec2`); the SW fold produces a named refusal until fixed; recert untouched |
| W7 | universal player + T3 | `trackerprog/{player,emit,certify}.py`: §4 made exact per W0, rendered tick-for-tick; `certify` = W1's `TickObs` equality against `Verifier.obs` over the whole horizon; S4-style tagged JSON, `trackerprog.md`, the certificate with `refusals` and the loop claim | trackerprog | medium (3–4 d) | GT2 ×2, JCH ×2 0 divergences; every refusal names its cell; §6.2's six numbers + `xz -9e` against the source `tuneprog.md` |

Total ≈ 24–30 agent-days. W1–W3 are independent of W0 and of each other;
W4 depends on W3; W5 on W0, W2, W4; W6 on W0, W2, W3; W7 on all.

## 5. Execution order

1. **W1 + W2 + W3** in parallel — small, recert-neutral, each its own PR.
2. **W0** — the doc revision, reviewed against §2/§3 above; it fixes the
   `Acc` and `Cmd` shapes W5/W6 build to.
3. **W4**, then **W5** and **W6** in parallel (W5 on GT2/Commando first,
   W6 on GT2/JCH/Commando; SW gated on the fold fix).
4. **W7** on GT2 ×2, JCH ×2; then SW ×2 after the fold, Commando ×2 after
   W0 settles I1/I11, Follin after I6.

Deliberately not now: defMON (no recert dir in `out/`; add one before it is
accepted), Galway/Walker/Blackbird (uncertified), multispeed (§10).
