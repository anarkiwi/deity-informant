# trackerprog — open work

What is left on the trackerprog layer, and the checks the nine transliterations
settled. Sizes follow [tuneprog-backlog.md](tuneprog-backlog.md) §2: small ≤ 1
day of one agent, medium ≤ 1 stage, large = a stage with a prototype. The
schema itself is [prototype-trackerprog.md](prototype-trackerprog.md); what the
review found and what landed is [trackerprog-review.md](trackerprog-review.md).

Contents: 1 state · 2 decisions · 3 open work · 4 what a family must check ·
5 presentation gaps.

---

## 1. State

All nine of the anatomy's families are certified exemplars on one universal
player, 0 divergences over their whole horizons — **thirty builds, 332,358
ticks**. `universal.py` branches on no family. That thesis is proven and nothing
below reopens it. T0–T3 land; the review's R1–R10 and the schema hardening
B1–B9 are struck, in the git log and in the review's outcome table.

Acceptance for every row below is a command:

```
tools/trackerprog_poison.py --builds all --poison <name>
tools/trackerprog_poison.py --builds all --drop PATH --set 'PATH=JSON'
tools/trackerprog_poison.py --builds all --emit-digests DIR   # then --against DIR
```

## 2. Decisions, so they are not relitigated

| # | decision |
| --- | --- |
| D1 | the hand object is the trackerprog; what the lift emits is a **scoreprog**, a different object with its own renderer and certificate, sharing one field (`meta.commit_order`). B6/B7 converge them |
| D2 | the sound half does not want a small total language: `w11-producers-archive` if-converted the tick and came out 2×–19× the S4 program it replaced |
| D3 | the form B7 needs is already in the grammar — a stream with `all: True` is a guarded assignment list, and a `{stream}` phase runs one at a declared position |
| D4 | the compression claim is measured against the tune's own **load band**, not against `tuneprog.md`; measured, the object is 1.25×–2.18× the binary (§9.1) |
| D5 | the accumulator is a reading, not a universal: Follin has none across 32 subtunes and 111,763 ticks, Galway's effects are its order program, Blackbird's are streams |
| D6 | a schema row without a runnable poison is prose. Every kept one-family form carries one; the census strike measured **0 differing of 332,358** |

## 3. Open work

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| B6 | is the schedule recoverable? **Settled for two families** ([prototype-lifter.md](prototype-lifter.md) §2.1): the hypothesis holds, and §3.6's clock is derived whole — the counter a guard on the fetch's own path reads and a store steps, its `step`, a `boundary` that is a guard *list*, its `reset` clauses, and a divider told from the clock by comparing against a **cell** rather than a constant. Hubbard's every datum agrees with the hand's but one (the segment before the row is a `{stream}` phase, not `prelude`); JCH's `voice_order`, `commit_order`, `tempo.cell`, `step` and `reset` agree and four differ, three of them because the lift's `row` phase is the fetch region and it has no `stage`. Hubbard's object is unchanged to the byte by the general form | medium | for each of the nine, the derived schedule against the hand-written one, agreeing or differing by a named datum. **2 of 9 done** |
| B7 | lower the tick, do not classify it. **The lowering lands on two families and the recognition on the plane T1 states** ([prototype-lifter.md](prototype-lifter.md) §2.2, §2.3, §4): Commando song 1 lifts to an object with no `program` key that `universal.py` renders at **0 divergences over 11,780 ticks and 133,109 writes**, from the certified artefacts and **0 hints** — 86 store sites into 80 rows and 5 `Acc` records, 4 refused, all three of T1's accumulators joined, 2.16× the load band against the hand's 1.36×. *Guldkorn Intro* lifts with **0 hints** and 3 named refusals — 132 store sites into 114 rows, 19 tables materialised as streams of their own bytes, 0 of T1's five joined — and renders its whole 2,401-tick horizon with its **first divergence named at tick 4** (§5): the fetch's own byte loop reads a byte a turn and the score supplies one a name. The object is 3.03× the band against the hand's 1.90× | large | the lift emits an object `universal.py` renders, 0 divergences, no `program` key; coverage stated per family. **1 of 9 certified, 2 of 9 lifted; JCH's divergence and the four residues are §8** |
| B10 | `Acc` demoted to a reading | the schema half is closed and machine-checked; what is left is the presentation — §5 opening with coverage per family (D5) rather than with the grammar | small | §5 opens with coverage, not with the record |

B6 comes before B7: a lift that classifies every producer perfectly and recovers
no phase list renders wrong on every family with more than one commit. Both are
run by `tools/tuneprog_trackerprog.py`; what the second family cost and what the
lift still cannot state are [prototype-lifter.md](prototype-lifter.md) §8, and
what the recognition does not change is its §7.

**Not now.** Multispeed (§10); the `mods`/`arms` split of the accumulator
record — the census is about forms, not bytes, and the eight forms it struck
take one key each out of the objects that carried them. And the package split:
`deity_informant/trackerprog/` holds the trackerprog's `universal`, `printer`,
`attest`, `poison` beside the scoreprog's `emit`, `interp`, `region`, `resolve`,
`lift`, `score`, `streams`, `pitch`, `cursors`, `hist`, `certify`. B7 deletes
half of one side, so it waits for B7 to land or to be refused.

## 4. What a family must check

Each row is a check to run on the tenth family, and the exemplar that forced it.
A bare § is a section of [prototype-trackerprog.md](prototype-trackerprog.md).

### 4.1 What earns a field

| check | forced by |
| --- | --- |
| A field the object writes and no consumer reads is not a field — grep every name the schema declares | eight fields the grammar had already struck |
| A field only the *print* reads is an annotation, not a field | `Acc.bound.witness`, `scope`, `target` |
| Grammar with no exemplar is not grammar: a row nothing renders is a row nothing tests | §3.3's terminator, §3.6's nine named commands, `for`/`call`/`ret` |
| …and striking for want of an exemplar is a debt with a stated shape: what comes back is not the row that went | Follin returned `call`, `ret`, `mark`, `loop`, `jump` and two spellings the strike could not have foreseen |
| A field that names a set must name the set, not its size | defMON's flush skips two registers in the middle |
| A value the schema admits and no exemplar writes is untested | `row_consumes_tick: false` reached a vacuously true guard |
| A datum can be coarser than its name; the poison says how much of it a tune spends | Blackbird's `commit_order` content is "`ad` comes first" |
| A datum can be a property of the **frame** and not of the tune | JCH's flush direction is the frame's delay byte, both arms taken |
| A datum unobservable in one build of a family is not a datum the family lacks | voice order vanishes through JCH's flush and decides all 2,401 ticks of the build without one |
| Before widening a field for a family, confirm the family has the thing the field is about | SID Wizard defers nothing and has no shadow |
| When a family seems to need a flag, look for the datum that already answers the question | the image holds what the flush names; a commit outside it reaches the chip where it is made |

### 4.2 One question, one place that answers it

| check | forced by |
| --- | --- |
| One musical question, one place that answers it — and a second family is what makes two spellings visible | "does this row key a note?" answered from `gate` in one family and `note` in another |
| A hook is a phase with a name, and names do not compose | seven meta keys attaching a stream to a point in the tick |
| An enum is a hook list that has not noticed yet | five of `meta.prefetch`'s seven values did what a guarded `sets` row already does |
| When three names select three procedures, one of the three is the form and the other two are its values | the row clock: a counter, of which a divider and a countdown are values |
| An ordering said twice will disagree — one list, and the order is the order | three commit lists gave a held `sr` command the register the prelude must win |
| A sigil that means two things is a grammar, not a shorthand | `@name` was a voice cell and a shadow pair |
| A guard with two spellings is two guards; a guard in a tuple position cannot be read by anything that did not know the tuple | 39 of them in SID Wizard alone |
| For each name space, require one *implementation*, not one spelling | a third spelling was a different tick position, wrong 132 ticks in |
| A guard evaluated twice against a moving cell is two guards | `Acc.gate` re-evaluated `step_when` after the store |
| A constant in the player is a family in the player | three of them, each moved to where its own fact lives |
| Name a command by what it does, never by its dispatch index | GoatTracker 2's `T144A` nibble; SID Wizard's `BIGFXTABLE` |
| A token class the layer has not spent is a token class the layer will pay for | JCH's wave table re-derived three row kinds every tick from a constant byte |
| One machine fact, one spelling | the 6502 carry had three; `carry_out` and `borrow_out` remain |
| Two properties that should be orthogonal, selected by one flag, is a hole and not an inelegance — and 0 differing means unreachable, not unimportant | *when* the row is read and *what shape the sequencer is* were one flag |
| One act rule, and which one is a measurement | the row is the act at 0 differing; the list is the act at 2,943 |
| A step has one grammar and may have two readers; say which reads which field | a cursor's row carries no `when` and a guarded row no `hold` |

### 4.3 What a form must be told

| check | forced by |
| --- | --- |
| A form that reads a shared cell must be told what is its own | Walker's two modulators move one offset on 1,140 of 9,949 steps |
| Before adding a policy, ask whether a guard channel already says it | the one-shot is `delta_when`, not `policy halt` |
| Before reusing a form, check what the program does on the rows it covers, not what its bytes look like | a packed rest and a held row are identical in the byte stream |
| A terminator's *scope* is a datum, and the family that shows it starts fewer voices than it has | `$86` ends a voice, not the tune |
| Where a phase runs is a property of the channel, not of the player | Follin's voices *write* the channel the phase reads: 383 ticks |
| What a header resets says whether a pattern is reusable, and it is cheaper to read than to compare rows | Walker's 2,592 played rows state as 1,134 |
| Whether a command outlives its row is a datum, not a consequence of the clock's shape | effect memory had lived in the countdown branch |

### 4.4 How a claim is settled

| check | forced by |
| --- | --- |
| Before a schema row admits a second form, render the first for the family that has the second, over the **whole** horizon | `meta.commit ∈ {order, acts}` distinguished no observation |
| A reduction in §2 is not a licence to reduce in §4 | collapsing the act sequence diverges on 500 ticks of *Emomyst* |
| Being what the player does is not the test; distinguishing an observation is | two V20 fields, both real, both built, both 0 of 8,577 |
| **A poison measured on a prefix is not a poison** | defMON's pulse carry is 0 over the first 20,000 ticks and set on 9,144 of 170,702 steps over 149,025 |
| When a general form must not disturb the families that do not need it, place it where their control flow does not go | a fetch that flushes *between* two rows is bit-identical for a one-row family by construction |
| A static census over a player's data is a lower bound on what its tunes do; the render settles it | the anatomy counts `$93` at 0; one subtune uses it three times |
| A citation written from a *reading* of a family is a hypothesis until that family is transliterated | "the sidTAB row is the instrument"; §5's `links` row |
| A boundary a document states and a comparison does not implement is prose | `certify` split edges per voice and `attest` compared them flat |
| An invariant the renderer does not assert is prose | asserting `bound.interval` took five of sixteen records out |
| Naming a command by what it does costs a byte-for-byte round trip its *shape* | three SID Wizard effects share an encoding across two columns |
| **A number no harness generates is a number nobody checked** | §7 quoted "render both forms and count" forty-odd times with no tool doing it |
| A claim measured against a presentation artefact is not measured, and re-measuring may refute it | the compression claim was an artefact of its yardstick (D4) |
| Two artefacts are two artefacts whatever one document calls them — count the shared **fields**, not the key names | seven shared names, one shared field |
| An interpreter that dispatches per reading can be compiled per object; the next factor would cost the layer a procedure a reader can hold | 2.14× then 1.40×, write lists identical throughout |

## 5. Presentation gaps

Five places where a transliteration had to open a disassembler because the
printed tuneprog did not settle a fact a materialiser needs. All five are in the
*presentation*: the S4/S6 artefacts carry the information and `printer` drops or
re-derives it, so all are recert-neutral, and nine families landed by hand
without them.

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| P1 | one canonical origin per region | a region prints under several names with several derived origins (`T16F9[1 + t1]` / `T16F9[2 + r4]` / `T16F9[y]` are one array); pick the origin once (`regions._origin` computes it), record `base` and `first_index`, normalise every index expression to it | small | every read of one region prints `T[e]` against one stated base; a test materialises the GT2 wavetable from the print alone |
| P2 | fold a carry the reaching compare proves | `a38 = ((T175D[y] + freq_lo_idx) + (T16F9[y] >= $E0)) & $7F` re-derives a carry the reaching `CMP`/`BCS` proves 0; constant-fold it, keep the named form only where it is live | small | GT2's five re-derived carries fold; Hubbard's `$5237` inherited carry stays named; recert-neutral |
| P3 | print an untaken arm's body | `p_1082` prints `# untaken: …` and drops the two instructions the arm holds, which are what make the vibrato depth 8-bit; a second build may take the arm | small | every `untaken` marker carries its arm's statements, marked |
| P4 | state `commit_order` in the certificate | the per-voice edge-register order is recovered but appears nowhere a reader can use; §3.1 needs exactly this one datum per tune | small | `certificate.json` carries `commit_order`, matching §3.1's table |
| P5 | dispatch on the command number, not the patched address | the tick-0 and continuous dispatches print as `switch b1295: case $1006:`, so the two GT2 builds label one command with different addresses | medium | both switches are over the index; the two builds' arms are comparable line for line |
