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
[Galway](prototype-galway-trackerprog.md). `universal.py` branches on no family:
the object is the tune and the procedure is fixed. **That thesis is proven and
nothing below reopens it.**

The planes T0 (write provenance), T1 (accumulators, with `Acc.step` an exact
recurrence), T2 (score, streams, pitch) and T3 (the certified render) all land,
and the layer has been hardened with the families all in. The review of the
prototype doc against its sources, the fifteen internal inconsistencies I1–I15,
and work packages W0–W10 and W12 are all struck; their mechanisms and
measurements are in [prototype-trackerprog.md](prototype-trackerprog.md) §7 and
in #291–#304 and #307. The per-family findings they produced are §3 below.

### 1.1 Decisions, so they are not relitigated

A first-principles review against all nine families settled six questions. Each
is a decision, not a proposal.

**D1 — the hand object is the trackerprog; the lift's object is not.**
`universal.py` renders the nine hand objects; `player.py` renders a *different*
object carrying a whole S4 `Tuneprog` in a `program` key, and that is what T0–T3
produces. They share no field but the certificate. One of the two is the layer;
the other is a partial lift that has not arrived yet. B2 names them apart, B6/B7
converge them.

**D2 — the sound half does not want a small total language.** It was tried:
`w11-producers-archive` if-converted the certified tick to a ranked item list and
came out **2×–19× the size of the S4 program it replaced**. A language whose
object is the *player* transliterated shares nothing between two families and
abandons §1's thesis. Nine hand transliterations at 0 divergences are the
evidence the tracker vocabulary suffices.

**D3 — the form W11 needs is already in the grammar.** A stream with `all: True`
is a list of guarded assignments applied in order (`universal.rows`), and a
`{"stream": s}` phase in `meta.tick` runs one at a declared position. Follin,
defMON, JCH and Walker already write it. So `program` can leave the object with a
*trivial* lowering and no classification at all, and recognition becomes a
coverage number rather than a gate on emission.

**D4 — the compression claim is measured against the tune's own load band.**
§9 asks whether the score compresses better than the program that played it. The
program that played it is the binary, not `tuneprog.md` — a pretty-printed
decompilation, which is a presentation artefact. B4 re-measures.

**D5 — the accumulator is a reading, not a universal.** Follin has none at all
across 32 subtunes and 111,763 ticks; Galway's effects are its order program,
Blackbird's are streams. Eighteen schema fields against sixteen total
instantiations, and more one-family fields than the rest of the object combined.
`bound` asserted at `store` stays — it took five false records out — but §5 stops
presenting `Acc` as the layer's centre.

**D6 — a schema row without a runnable poison test is prose.** The layer's method
is to render both forms over the whole horizon and count differing ticks, and
`tools/trackerprog_poison.py` is now that method: a stated mutation over a named
build set, the sites it matched, and the ticks that differ. Every item below
states its acceptance as a command.

## 2. Open work

Ordered. The order is the argument, so each tier says why it is where it is.

### Tier 0 — struck: the layer can check itself

**B1, the poison harness, landed.** `trackerprog/poison.py` and
`tools/trackerprog_poison.py` take an object, a stated mutation and a build set,
render both forms over each build's whole horizon, and report per build the
differing ticks, the **sites** the mutation matched and the first divergence — a
path that matches nothing renders 0 differing and is not evidence, and a poison
the renderer refuses is an asserted invariant rather than a crash. The registry
is **thirty builds, 332,358 ticks**, every horizon read from the committed
certificate that records it; the eleven builds §7's P1–P8 were measured over are
a named set totalling **236,586**, and both totals are asserted against every
number these documents quote. Each acceptance below is now a command:

```
tools/trackerprog_poison.py --builds all --poison <name>
tools/trackerprog_poison.py --builds all --drop PATH --set 'PATH=JSON'
tools/trackerprog_poison.py --builds all --emit-digests DIR   # then --against DIR
```

### Tier 1 — the layer states things that are not true

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| B2 | one schema, or two names | `universal.py` reads `{meta, pitch, streams, accs, instruments, score, state0, globals}` — the nine hand objects, byte-identical in their key sets. `emit.py` writes `{…, producers, program, inputs}` and `player.py` interprets its `program` as S4 IR. §1 presents one object with a residual `program` block; they are two artefacts. Name the lift's for what it is, state both paths in §1, and stop asserting the lift produces a trackerprog | small | §1 and §6 describe two artefacts and one target; no doc says the trackerprog carries a program |
| B3 | the order program is unreachable when the clock prefetches | `advance` (`universal.py:994`) never calls `order_step`; both call sites (`:1086`, `:1107`) are on the non-prefetch path, and `advance` ignores the `op` `play_of` returns. The three prefetching families have flat orders and the two with order programs do not prefetch, so it has never fired. A prefetching family with a called or counted score walks past `call`/`mark`/`loop` as though it were `play`, silently. `advance` calls `order_step` at the wrap | small | 0 differing of 332,358 over thirty builds; a hermetic snippet with `fetch` in `meta.tick` and a `mark`/`loop` pair mis-renders before the change and renders after |
| B4 | measure §9 against the load band | re-measure §6.2's six and `xz` against the bytes the tune actually occupies — the figure each family doc already reports — instead of against `tuneprog.md`. State the result whatever it is; if the layer's object is larger than the binary it came from, that is the finding | small | §9 carries one table against the load band, and the claim is met or restated |
| B5 | the doc audit | roughly a dozen places where [prototype-trackerprog.md](prototype-trackerprog.md) is contradicted by the code or by a family doc. Confirm each against the code, then fix the doc or change the code — including §3.5's "one procedure runs all three" inline streams (there are two), §3.4's `Acc.step` as what a player computes `cell(t+1)` with (no player does), §5's `Acc` grammar (omits fields the player reads, lists fields it never reads), §3.5's "GT2's `gatetimer` **is** `early`" against `trackerprog_goattracker.py:523`'s assert of the opposite, §7's "six families transliterated by hand", §1 on Galway as prose-only, and the rows §3 struck that three family docs still describe as live. **Already fixed:** §7's horizon total, and `Event.cmds` → `Event.arm` | small–medium | every row confirmed, then closed on one side or the other; no claim in the spec that the code refutes |

These are one tier because they share a cause. Each is a statement nothing
executes: a schema in prose, a claim measured against the wrong thing, a doc row
no reader checks. B3 is here rather than in a defect list because it is the same
failure — two properties that should be orthogonal, coupled by a flag, and no
exemplar yet that separates them.

### Tier 2 — the open design work

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| B6 | is the schedule recoverable? | the experiment W11 needs and nobody has run. There are **nine ground truths in `tools/`** — nine hand-written `meta.tick` phase lists, `meta.row`/`meta.stage` row programs, stream `rank`s and `commit_order`s. Ask whether they are derivable from the certified tick: the hypothesis is that the phases are the maximal segments of the tick's reverse postorder between the T0 commit sites, with the fetch regions as `row`/`fetch`. Nine families is enough to refute it outright | medium | for each of the nine, the derived schedule against the hand-written one, agreeing or differing by a named datum |
| B7 | W11 restated — lower the tick, do not classify it | lower every store site of the certified tick outside the fetch regions into `sets` rows of an `all: True` stream, placed at a `{stream: s}` phase of `meta.tick` at its program position, every leaf opened to a named cell, a table entry, `pitch`, `byte` or a constant. `program`, `producers` and the S4 interpreter leave the object. Recognition — an `Acc` is a row whose sets are `c ← c ± Δ`, an instrument a row group reading one record's columns — is then a pass *over the lowered stream*, reported as coverage | large | the lift emits an object `universal.py` renders, at 0 divergences, with no `program` key; coverage stated per family |

W11's old acceptance — *"the same nine tunes at 0 divergences with no `program`
block"* — is **already met nine times**: no hand tool emits a `program`. It was
never a schema question. It is a lift question, and its hard part is not producer
values (mostly a stream column, a pitch lookup or a constant) but the *schedule*,
which W11's old mechanism did not mention at all. Hence B6 before B7: a lift that
classifies every producer perfectly and recovers no phase list renders wrong on
every family with more than one commit.

### Tier 3 — schema hygiene, unblocked by B1

| # | item | mechanism | size | acceptance |
| --- | --- | --- | --- | --- |
| B8 | the one-family forms | roughly twenty-five forms with one family behind them, each either deleted, expressed in the general vocabulary, or kept with its single family stated. `globals.stop_writes` (Hubbard: a literal write list — the observable, in the object), `meta.rest_arm`, `meta.pitch_links` + `Cmd.links`, `Acc.policy: "take"`, `reflect-complement`, `state0.held` (GT2); `Acc.beyond`/`emit`/`trap`/`flag.seed`/`amplitude.shift`, `state0.dividers`, `end: "jump"` as a bare string (Hubbard); `meta.pitch_target`, `Stream.epoch` (SW); `amplitude.count` (Walker); the two forms under `meta.prologue`; and the certificate's loop shape, which Walker alone spells `{tick, period}` against `certify.py:83`'s `first_repeat` | medium | each row deleted at 0 differing, or kept with its family and its survey named |
| B9 | the duplicated procedures | `channel()` and `channel_after()` are byte-identical bodies over two lists; `certify.divergence` and `attest.attest` are two comparisons over one §2 rule, with `COMPARED`/`DROPPED` declared twice; the three inline streams run through two procedures that disagree about acts; three vocabularies name one concept, the epoch of a read (`Acc.emit`, `Stream.epoch`, and the `pre`/`post`/`mid` of `Acc.step`). One implementation each | medium | 0 differing of 332,358 over thirty builds; the certificate has one implementation |
| B10 | `Acc` demoted to a reading | rewrite §5's grammar box from the code (it is stale by eight fields), move `Acc.step` out of §3.4, mark `bound.witness`/`scope`/`target` as annotations rather than semantics, delete `policy: halt` (spec-only: no tool writes it, `apply()` has no arm for it), and state coverage per family rather than presenting the accumulator as the layer's centre | small | §5 matches what the player reads, field for field |

Tier 3 is deliberately last. Every row is real, none is load-bearing, and each
costs a render of thirty builds to settle — which is what B1 bought, and what
makes doing them cheap rather than careful.

### Not now

The five presentation gaps of §4; multispeed (§10); the `mods`/`arms` split of
the accumulator record, which is the largest migration anyone has proposed and
should stay behind B8's measurement — if JCH's object does not shrink, it is not
worth it.

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
| A field only the *print* reads is an annotation, not a field — the print is a consumer, so "no consumer reads it" passes things the player never sees | `Acc.bound.witness`, `Acc.scope` and `Acc.target` are written by nine tools, printed, and read by the player never; §5 presents them as semantics |

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
| Two properties that should be orthogonal, selected by one flag, is a hole and not an inelegance — and it stays invisible while no exemplar separates them | *when* the row is read and *what shape the sequencer is* are one flag: the order program is unreachable to any family whose clock prefetches, and the three that prefetch happen to have flat orders |

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
| **A number no harness generates is a number nobody checked** — the method is not the tool | §7 quoted "render both forms and count differing ticks" forty-odd times with no tool in the tree doing it, and its headline horizon total was wrong six times over against its own per-build list; `tools/trackerprog_poison.py` is the tool, and every horizon it uses is the certificate's |
| A claim measured against a presentation artefact is not measured | §9 compares the object to `tuneprog.md`, a pretty-printed decompilation, where the program that played the tune is the binary |
| Two artefacts that share a certificate and no field are two artefacts, whatever one document calls them | the nine hand objects and what the T0–T3 lift emits have disjoint key sets and two players; the spec presents one object with a residual `program` block |
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
