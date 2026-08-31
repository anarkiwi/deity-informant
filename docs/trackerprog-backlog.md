# trackerprog — review and backlog

What is left to do on the trackerprog layer, and the rules the nine
transliterations settled. Sizes follow [tuneprog-backlog.md](tuneprog-backlog.md)
§2: small ≤ 1 day of one agent, medium ≤ 1 stage, large = a stage with a
prototype.

Contents: 1 state · 2 open work · 3 what the transliterations settled ·
4 presentation gaps.

---

## 1. State

All nine of the anatomy's families are certified exemplars, rendered by one
universal player against `Verifier.obs` at 0 divergences over their whole
horizons: [Hubbard](prototype-commando-trackerprog.md),
[GoatTracker 2](prototype-goattracker-trackerprog.md),
[SID Wizard](prototype-sidwizard-trackerprog.md),
[defMON](prototype-defmon-trackerprog.md), [JCH V20](prototype-jch-trackerprog.md),
[Follin](prototype-follin-trackerprog.md),
[Blackbird](prototype-blackbird-trackerprog.md),
[Walker](prototype-walker-trackerprog.md) and
[Galway](prototype-galway-trackerprog.md). The planes T0 (write provenance), T1
(accumulators, with `Acc.step` an exact recurrence), T2 (score, streams,
pitch) and T3 (the certified render) all land, and the layer has been hardened
against the families all in.

Three parts of this document are gone because they closed, not because they
were dropped:

* **The review of the prototype doc against its sources**, and the fifteen
  internal inconsistencies I1–I15, were settled row by row by W0 (#295), each
  with a stated decision in
  [prototype-trackerprog.md](prototype-trackerprog.md).
* **Work packages W0–W10 and W12** are struck. Their mechanisms, measurements
  and per-tune tables are in [prototype-trackerprog.md](prototype-trackerprog.md)
  §7 and in the merge commits (#291–#304, #307); the per-family findings they
  produced are §3 below.
* **The per-family narrative** of what each transliteration found is reduced to
  §3's rules. The evidence for each is in that family's own prototype doc.

W8 and W9 are struck as **wrong at the root**, which is the one strike worth
restating: both lifted a row's sound from `Verifier.obs`, so `certify` compared
an encoding of the observable with the observable and 0 divergences was
tautological. W10 replaced them by cutting the fetch regions out of the
certified tick and running them over the program's own tables.

## 2. Open work

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| W11 | the producer program as section 4 | classify each producer's guards against the events the fixed procedure has (row, note-on, `early`, a stream's step, an accumulator's rate) and its value against the data forms (an instrument column, a stream column, a pitch lookup, an `Acc`), exact over the horizon or a named refusal; then `player` is section 4 and `program` leaves the trackerprog | large | the same nine tunes at 0 divergences with no `program` block |

W11 is what the layer still carries a program for. The sound half outside the
fetch regions rides in the object as the S4 program and is run by the one
interpreter, with its SID write sites listed as producers under their guards;
that is exact, and it is the ground the section 4 reduction must be proved
against — a producer list rendered by the fixed procedure has to reproduce what
this interpreter does tick for tick.

**A first attempt is archived at tag `w11-producers-archive`** (PR #306,
closed). It predates the nine-family campaign and cannot be rebased: its
`universal.py` is a stub that refuses every accumulator and renders nothing,
against which the certified player is now 1,522 lines over nine families, so the
two files conflict add/add. What is worth reading there is the lowering —
`sound.py` inlines the certified tick once per call path and if-converts it to a
ranked item list (`block`/`let`/`phi`/`store`/`fetch`), `fetch.py` resumes
fetches by block and chains them into a region, `producers.py` names the cells.
Redo it against the certified player.

Deliberately not now: multispeed (§10).

## 3. What the transliterations settled

Each row is a check to run on the next family, and the exemplar that forced it.
They are the durable half of nine transliterations and one hardening pass; none
is a to-do. A bare § is a section of
[prototype-trackerprog.md](prototype-trackerprog.md).

### 3.1 What earns a field

| check | forced by |
| --- | --- |
| A field the object writes and no consumer reads is not a field — grep the readers of every name the schema declares, the player's, the print's and the round trip's, and delete what nothing answers | eight fields, after the schema had already struck them from the grammar |
| Grammar with no exemplar is not grammar: a row nothing renders is a row nothing tests | §3.3's terminator, §3.6's nine named commands, `for`/`call`/`ret` in `Order` |
| …and striking for want of an exemplar is a **debt with a stated shape**: what comes back is not the row that went | Follin's score-as-program returned five steps — `call`, `ret`, `mark`, `loop`, `jump` — and settled two spellings the strike could not have foreseen |
| A field that names a set must name the set, not its size; a value space of "a prefix, forwards or backwards" describes the families read so far | defMON's flush skips two registers in the middle, so no count and no direction reaches it |
| A value the schema admits and no exemplar writes is untested | `row_consumes_tick: false` reached `guards(None)` — vacuously true — so the one family to write it got *always* instead of *never* |
| A datum can be coarser than its name; the poison sweep says how much of it a tune spends | Blackbird's `commit_order` content is "`ad` comes first": two of the six permutations render it, four do not |
| A datum can be a property of the **frame** and not of the tune — ask what varies *within* a tune before making a field one datum per tune | the Puterman JCH build flushes low-to-high on a frame whose delay byte is zero and high-to-low otherwise, both arms taken |
| A datum unobservable in one build of a family is not a datum the family does not have | voice order vanishes through JCH's flush and decides all 2,401 ticks of the build with no image |
| Before widening a field for a family, confirm the family has the thing the field is about | a shadow is a register file a tick *defers*; SID Wizard defers nothing and has none |
| When a family seems to need a flag, look for the datum it already has that answers the same question | *the image holds the registers the flush names, and a commit to a register the flush does not name reaches the chip where it is made* — one sentence, no new field, covers GoatTracker 2's deferred filter and defMON's immediate cutoff |

### 3.2 One question, one place that answers it

| check | forced by |
| --- | --- |
| One musical question, one place that answers it — and a second family is what makes two spellings visible | "does this row key a note?" answered from `gate == "on"` for one family and `note is not None` for another |
| A hook is a phase with a name, and names do not compose; a hook per call site is how two procedures for one musical act happen | seven meta keys attaching a stream to a point in the row or tick, beside the general `rank`/`when` that already existed; now `meta.row` and `meta.tick` |
| An enum is a hook list that has not noticed yet | five of `meta.prefetch`'s seven values did what a `sets` row already does under a guard the grammar already had |
| When three names select three procedures, one of the three is the form and the other two are its values | the row clock: a counter, of which a divider and a countdown are values |
| An ordering said twice will disagree — one list, and the order is the order | three commit lists gave a held `sr` command's register to the command, where the prelude must win it |
| A sigil that means two things is a grammar, not a shorthand | `@name` was a voice cell in an assign target and a shadow register pair in an accumulator's `cell` |
| A guard with two spellings is two guards: a guard in a tuple position cannot be read by anything that did not already know the tuple's shape | 39 of them in SID Wizard alone, carried as a third element beside the value |
| For each name space the schema declares, require one *implementation*, not one spelling — every reader and every writer | a third spelling was a different tick position, so defMON's pulse sweep read the previous tick's value and was a wrong width 132 ticks in |
| A guard evaluated twice against a moving cell is two guards | `Acc.gate` chose its arm by re-evaluating `step_when` after the store |
| A constant in the player is a family in the player | three values in the one procedure that is supposed to have no family in it, each moved to where its own fact lives |
| Name a command by what it does, not by its dispatch index — the index keeps the jump table the lift is supposed to spend | GoatTracker 2's `T144A` nibble; SID Wizard's `BIGFXTABLE` was the same trap waiting |
| A token class the layer has not spent is a token class the layer will pay for | one family's wave table carried raw bytes and re-read three kinds out of them every tick, with the assembly's own `CMP` immediates as guards |
| One machine fact, one spelling | the 6502 carry had three; `carry_out` and `borrow_out` remain, the bias tree belongs in the player |

### 3.3 What a form must be told

| check | forced by |
| --- | --- |
| A form that reads a shared cell must be told what is its own | Walker's pitch triangle and pitch bend both move one 16-bit offset, and on 1,140 of 9,949 modulator steps both have — no interval on that cell is either one's swing, so `amplitude` gained `{count, cell}` |
| Before adding a policy, ask whether the guard channel already says it | the one-shot that stops a step short of its period is `delta_when`, not `policy halt` |
| Before reusing a form, check what the program does on the rows it covers, not what its bytes look like | a packed rest and a held row are identical in the byte stream; one is spent and never applied, the other executes on every row it covers |
| A terminator's *scope* is a datum, and the family that shows it starts fewer voices than it has | `$86` ends a voice, not the tune: its flag clears and the filter goes on writing |
| Where a phase runs is a property of the channel, not of the player — when a position is right for every family so far it is a default, not a law | Follin's voices *write* the global channel the phase reads, so the same list in the same place writes the un-swept value on 383 ticks; `globals.after` is the second list |
| What a header resets is what says whether a pattern is reusable, and it is cheaper to read than to compare rows | Walker's header re-arms all three voices before any reads a token: 2,592 played rows state as 1,134 |
| Whether a command outlives its row is a datum, not a consequence of the clock's shape | effect memory lived in the countdown branch of the sequencer, so "countdown-clock families hold their commands" was load-bearing and untrue; now `meta.row_command` ∈ {`held`, `spent`} |

### 3.4 How a claim is settled

| check | forced by |
| --- | --- |
| Before a schema row admits a second form, render the first for the family that has the second and count the ticks that differ, over the **whole** horizon | `meta.commit ∈ {order, acts}` distinguished no observation and the branch was deleted |
| A reduction in §2 is not a licence to reduce in §4: the player must produce what the rule compares, and only a family that exercises the rule proves it does | collapsing the per-tick act sequence is unobservable through a ghost flush and diverges on 500 ticks of *Emomyst* |
| Being what the player does is not the test; distinguishing an observation is — that is how a *faithful* form is refused | two V20 fields, both real, both foreseen, both built, both 0 of 8,577 |
| **A poison measured on a prefix is not a poison** | defMON's pulse carry is 0 over *Automatas*' first 20,000 ticks — longer than any other exemplar's whole horizon — and set on 9,144 of 170,702 steps over its full 149,025 |
| When a general form must not disturb the families that do not need it, place the new work where their control flow does not go; the measurement then confirms rather than decides | flushing *between* two rows of a walk and never after the last makes a one-row family bit-identical by construction |
| A static census over a player's data is a lower bound on what its tunes do; the render settles it | the anatomy counts `$93` at 0 and one subtune uses it three times; `$8D` also stores to the running mode |
| A citation written from a *reading* of a family is a hypothesis until that family is transliterated | two transliterations took one out — §3.5's "the sidTAB row is the instrument", and §5's `links` row |
| A boundary a document states and a comparison does not implement is prose | `certify` split edges per voice and `attest` compared them flat; six families never noticed, because a player that finishes one voice before the next produces the same interleave |
| An invariant the renderer does not assert is prose | turning `bound.interval` on took five of sixteen accumulator records out — none a bug in the render, every one a false claim the object was making |
| Naming a command by what it does costs a byte-for-byte round trip its *shape*, and §8 already says the trackerprog is *a* preimage | three SID Wizard effects have the same encoding in two columns |
| An interpreter that dispatches per reading can be compiled per object — but the next factor after that would cost the layer the thing it exists to have | 2.12× over eleven builds, write lists identical tick for tick; the remaining cost is flat at 5–10 % across four procedures, so the next step is generating source per object rather than one fixed procedure a reader can hold against §4 |

## 4. Presentation gaps

Five places where a transliteration had to open a disassembler because the
printed tuneprog did not settle a fact a materialiser needs. All five are in the
*presentation*, not the certified program: the S4/S6 artefacts carry the
information and `printer` drops or re-derives it, so none changes a certified
program and all are recert-neutral. All five are still open — nine families
landed by hand without them, so none is blocking.

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| P1 | one canonical origin per region | a region prints under several names with several derived origins (`T16F9[1 + t1]` / `T16F9[2 + r4]` / `T16F9[y]` are one array; so are `T175D`/`T1761`, `T1875`/`T1876`/`T188A`, `T17FB`/`T17FC`, `T1826`/`T1839`), and the header's "2-based, read at `$16F7,i`" names neither the base nor the basedness a reader can index by. Pick the origin once (`regions._origin` already computes it), record `base` and `first_index`, and normalise every index expression to it | small | on the four GT2/Commando/JCH/SW prints, every read of one region prints `T[e]` against one stated base; a test materialises the GT2 wavetable from the print alone |
| P2 | fold a carry the reaching compare proves | `a38 = ((T175D[y] + freq_lo_idx) + (T16F9[y] >= $E0)) & $7F` re-derives a carry as a predicate the reader must evaluate; `$12CD CMP #$E0` / `$12CF BCS` proves it 0 on that path. Constant-fold `carry(site)` where the reaching compare decides it; keep the named form (§4.11's producer/consumer pair) only where it is live | small | GT2's five re-derived carries fold to constants; Hubbard's `$5237` inherited carry stays named; recert-neutral |
| P3 | print an untaken arm's body | `p_1082` prints from `# $108B` with `# untaken: T1851[y] >= 0` and drops the two instructions the arm holds — here `LDY #$00 ; STY $FD`, which is what makes the vibrato depth 8-bit. A second build of the same player may take the arm, so a transliteration that must render both needs the semantics either way | small | every `untaken` marker carries its arm's statements, marked; the GT2 print gains the 12 (15) arms §3 of prototype-goattracker.md counts |
| P4 | state `commit_order` in the certificate | the per-voice edge-register order is recovered (the stores are named `ghost[x/7].ad` etc. and their order inside a routine is in the IR) but appears nowhere a reader can use; §3.1 of prototype-trackerprog.md needs exactly this one datum per tune | small | `certificate.json` carries `commit_order`; the certified families' values match §3.1's table |
| P5 | dispatch on the command number, not the patched address | the tick-0 and continuous dispatches print as `switch b1295: case $1006:` — the compiled form. The command's *number* is the index into `T144A` the block above computes, so the two GT2 builds label the same command with different addresses | medium | the GT2 prints' two switches are over the index; the arms of the two builds are comparable line for line |

P1–P4 are small and independent; P5 wants the switch's index recovered from its
writer, which `resolve` already closes statically (prototype-goattracker.md G5).
