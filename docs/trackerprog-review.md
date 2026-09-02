# trackerprog — a critical review of the machine and the spec

Reviewed 2026-09-02 against `main` at #333: `deity_informant/trackerprog/`
(`universal.py`, then 1,527 lines), §2–§9 of
[prototype-trackerprog.md](prototype-trackerprog.md), and the nine hand
transliterations in `tools/trackerprog_*.py` read as the thirty cached objects
the poison registry builds. Ten items came out of it; all ten have landed
(#335–#344), with `tools/trackerprog_poison.py` as every row's acceptance —
`--emit-digests DIR` at the merge base, `--against DIR` after, **0 differing of
332,358** over the thirty builds unless the row says otherwise.

Contents: 1 verdict · 2 the outcome, row by row · 3 what measurement refuted ·
4 what remains.

---

## 1. Verdict

The thesis held at its coarsest grain and holds now: no `meta.family` branch in
`trackerprog/`, thirty builds on one module, the hermetic suite over it. Below
that grain the review made four claims; each is now measured rather than argued.

| claim | as reviewed | as it stands |
| --- | --- | --- |
| "no per-family construct" (§1) | 18 player mechanisms with one family behind them, plus a publish/subscribe modulation language for one family's RAM aliasing | the census is a script, not a list: **300 forms, 105 with one family**, 59 of them the family columns §3.3 and §3.5 admit and **46 forms of the player**. Eight decisions struck nine; the forty that remain are stated with their family, their reader and a poison (B8, #342/#343). The subscription network is gone — one expression, `{"cell": [name, voice]}` (R4) |
| "free of player idioms" (§4) | literal SID write lists, the source's voice loop direction, `7·v` as a value, the source's own instrument column names | the write lists, `sid_base` and the unread columns are gone (R1/R3, R6, R8); a register is a **name** everywhere; what remains is the family's own columns, which the schema admits and a hermetic test bounds |
| "one fixed procedure" (§4) | two target dispatchers, two act procedures, three dividers, two ends of the play list, a compiled row clock nobody calls | one of each (R1, R2). `meta.tempo.rate` stays beside the divider and is a different question, so two clocks remain by design and the document says why |
| "compiled once, not walked" (the compile package, #320) | four walked paths and a per-voice-tick sort; headroom estimated 1.5–2× | the compile is top down (R7): **7,699 → 10,811 ticks/s over the nine families, 1.40×** — the low half of the estimated band, not above it |

The layer's size and the lift's difficulty are where the review put them, and
[trackerprog-backlog.md](trackerprog-backlog.md) B6/B7 carry them.

---

## 2. The outcome, row by row

| # | finding | landed | measurement |
| --- | --- | --- | --- |
| R1 | two target dispatchers: `assign` walked the object per write where a stream's `sets` was compiled | #335 | `assign` deleted, all four paths compiled, `clock()` reads the `clockplan` it had been building for no reader. *Je suis Linus* 9,429 → 10,150 ticks/s |
| R2 | two act procedures, three dividers, two ends of the play list | #336 | one procedure over one plan; **the act rule was measured, not chosen** — the row is the act at 0 differing, the list is the act at **2,943** on seven builds. One divider (`dividercode`), one `order_end()` |
| R3 | the dead surface: `meta.player`, `init_writes`, `mode_vol`, `tempo.swing`, a dead edge branch, `lastnote` | #335 | six struck; `lastnote` is **not** dead (§3) |
| R4 | a publish/subscribe network — 7 event kinds, 9 sites — for one family's words past the tuning | #337 | `{"cell": [name, voice]}`; the mirror measured equal at all **2,676** reads before anything was struck; the one counting subscription is two `meta.row` steps, its reset measured against the alternative at **48 of 576**. `universal.py` 1,522 → 1,480 |
| R5 | five hooks: `stage_sounds`, the `op` stand-down, the tune stop, the prologue, row 0 as a sentinel | #338 | each a datum: **0 of 60,848**, **0 of 292,914**, **0 of 39,444**, and the padding gone from **five** families, not three |
| R6 | five spellings of a register number, four global registers with no name, `7·v` as a value | #339 | one naming; `universal.chipreg` is the only place a name becomes a number. SID Wizard 1.6's `7·v` is `{"bug": "voice_base"}`, Hubbard's is the voice cell `voicebase`. A hermetic test walks all thirty objects |
| R7 | the compile half done; the machine order rebuilt and sorted per voice-tick | #340 | `compiler.py`, the object spent once. GoatTracker 2 evaluated **15,243** payload-bound `const` nodes per 3,000 ticks and now evaluates none. **1.40×** over the nine families |
| R8 | `Ins = {adsr, …}` named a field no line of the player reads; 62-copy `ENGINE` lists | #341 | `adsr` decided by rendering it: a player note-on that emits it reproduces **SID Wizard alone**. The box is the six names the player reads; `meta.instrument` states what a family's instruments share. Instrument half 35,321 → 14,480 raw on Galway |
| R9 | the spec against the code: §4's sketch, §3.3's fields, §2's `voice_order`, §9.1's attribution | #344 | §4 is `tick`/`voice`/`clock`/`channel`/`channel_commit`/`commit` line for line; §3.3 gives each field its reader; the `dropped` list says *interleave*; *Knob at Night*'s 95 % is **12,636 of 16,252 `xz`** in one stream, `wrapdata`. Six checks in `tests/trackerprog/test_schema_doc.py` |
| R10 | two tool arms no tune reaches | #335 | `walker:main` renders the certificate's horizon; SID Wizard's three pointer commands are a named refusal with a test |
| B8 | the census the review ran by hand | #342, #343 | run as a script over the thirty objects; nineteen `POISONS` entries, one per kept mechanism |

---

## 3. What measurement refuted

The valuable half: rows this review argued and the render answered differently.

| the review said | what the render said |
| --- | --- |
| `lastnote` is a dead voice cell | it is read as `{"interval": {"cell": "lastnote"}}` on GoatTracker 2's speed table: dropping the write diverges on **4,466 of 8,236** and **4,284 of 8,659**. An unread *declaration* and an unread *cell* are not one finding |
| the `op` stand-down fires on 686–1,042 of 3,000 ticks on two families | those are the ticks the rule *ran* on, not the ticks it *changed*. Removed outright it differs on **2,028 of 8,236** and **2,873 of 8,659** and on **0 of the other twenty-eight builds' 315,463** — one family's precedence, not two |
| `__getstate__`/`__setstate__` exist to throw the memos away | a `Player` does not pickle plainly (its compiled form is closures), and two tools pickle one to resume a chunked certification. The pair stays, minus the private half |
| a memo on the machine order removes the sort *and most of the guard evaluations* | only the sort and the per-arm rank lookup: `slots()` asks every stream's `when` and every cursor's row **before any slot runs**, and the rank order depends on its doing so |
| guards cost frames because three or more terms build a generator; a chained closure fixes it | a chained closure costs a frame per pair too. What pays is that a guard term almost always states one operand outright — **501 of SID Wizard's 505**, 407 of 407 of JCH's, 79 of 79 of GoatTracker 2's, 151 of 153 of Galway's — so the constant folds into the comparison |
| the headroom from a finished compile is 1.5–2× | **1.40×**, the low half of the band |
| `rest_arm` is `meta.instrument.accs` said twice | moved there, the three arms differ on **2,714 of 8,236** and are **refused** on the second build: an instrument's arms are the voice's for as long as the instrument is, and a *rest* replaces what the score armed |
| `meta.stop: sequencer` could be a `stopped` cell guarding the clock | unreachable from where the tune stops: an order's `stop` is a step of the score and no step of a score writes a cell. Poison **837 of 29,911** |
| `amplitude: {count, cell}` would be a bound on a cell per modulator | `amplitude.interval` is *two constants* — that is what §5 means by statically known — and Walker's period is a byte of the instrument, so no interval on any cell states it |
| §3.5's `adsr` should become a player field | a note-on emitting it reproduces **SID Wizard alone**: JCH's puts `ad`, `sr` and `ctrl` in one act (**257 of 2,401**), Walker's beside sixteen other cells (**346 of 8,052**), and three families write theirs from a stream elsewhere in the tick (**1,543 of 11,780**, **1,428 of 10,426**, **1,327 of 9,450**) |
| GoatTracker 2's `policy: take` is a one-family snap | it is the `clamp` §5's own row calls it, with `delta $FFFF` passing the target from either side: **0 differing of 16,895** |
| `stream.rate` is one family's | since R2 there is one form and one procedure, read by two families — the row was not a one-family mechanism |
| row 0 as a sentinel costs three families | **five**: SID Wizard's `no stream` row and defMON's reserved cascade row with it |
| Hubbard's `sid_base` is a drum pitch | it is `$54EB`, the routine's own index into the per-voice arrays past the pitch table — the voice cell `voicebase`, read 120 times over song 1's horizon, so it could not be a trap |
| the order program under a prefetching clock is a hole | it is, and it was structurally unreachable: **0 differing of 332,358**, so the evidence is three hermetic snippets and not a tune |
| eighteen mechanisms have one family behind them | by a finer unit, **105 of 300 forms**, 46 of them the player's; eight decisions struck nine and the rest are stated with their family and a poison |

---

## 4. What remains

Nothing of R1–R10. What the review did not reopen, and still does not: the
thesis (thirty builds, one module, no family branch), the certificate, and
[trackerprog-backlog.md](trackerprog-backlog.md)'s decisions D1–D6.

The layer's open work is the backlog's: **B6** (is the schedule recoverable from
the certified tick?), **B7** (lower the tick rather than classify it), **B10**
(§5 opening with `Acc` coverage rather than the grammar) and the five
presentation gaps P1–P5.
