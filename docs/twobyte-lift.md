# The two-byte lift, redesigned from the machine

A design note for rung (d2), reasoned from `tests/test_lift6502.py` rather than
from the drivers that happened to be triaged. "MUST" is a gate.

## 1. What the enumeration says

The suite enumerates the shapes a 6502 has for writing one 16-bit quantity as two
bytes: lane addressing (`zp`, `zp,X`, `abs`, `abs,X`, `abs,Y`) x adjacent or split
lanes x operation x step operand mode x how the carry crosses x straight-line or a
bounded accumulator reversing on a threshold. Gate FP is the oracle, so no case
states an expected lift. **610 shapes, 242 lifted, 368 never reach a site at all.**

| operation | lifted | no site |
|---|---|---|
| `ADC` | 192 | 48 |
| `SBC` | 50 | 90 |
| 16-bit shift (`ASL`/`ROL`, `LSR`/`ROR`) | 0 | 16 |
| 16-bit inc/dec (`INC`/`BNE`/`INC`) | 0 | 16 |
| bitwise `AND`/`ORA`/`EOR` both lanes | 0 | 150 |
| `SLO` `RLA` `SRE` `RRA` `DCP` `ISC` | 0 | 48 |

These are not refusals. They are never counted, so no census on this branch
reports them and none entered §7.3's ledger.

## 2. The cause is one line, and it is an idiom

`_match` will not pair two statements unless `_links` finds an `INT_CARRY`,
`INT_LESS` or `INT_LESSEQUAL` in the hi statement's **value**. That single
predicate excludes:

- a carry crossing as **control flow** (`BCC`/`INC`) - 80 shapes;
- a carry crossing as a **shift bit** (`ROL`/`ROR`) - 16;
- a carry crossing as a **predicated counter** (`INC`/`BNE`/`INC`, `DCP`, `ISC`) - 24;
- an operation with **no carry at all** - the 150 bitwise shapes;
- and every undocumented RMW opcode, which reaches its hi lane one of those ways.

The remaining 50 are the standard 16-bit subtract, where only `step_hi = 0` has an
admitted rule. That one is a missing rule, which §4's governance already provides
for. The other 318 are a missing *definition*.

## 3. The definition, from first principles

Two byte cells `lo` and `hi` hold one 16-bit quantity `W = hi<<8 | lo`. Two
statements jointly update `W` **iff the concatenation of the two values they write
is a width-2 function of the concatenation of the two values the cells held**.
That is the whole definition. It mentions no carry, no add, no subtract and no
addressing mode.

The pass already computes exactly this: `_split(hi, lo)` builds `hi'<<8 | lo'` and
`_fuse` saturates the admitted rules over it. The e-graph is asked the right
question. `_links` is a **cost heuristic in front of it**, and `_word_form` -
which matches `(op, hi lane, lo lane, step, mask)` for `op` in `{add, sub}` - is
an idiom table behind it. The architecture is: idiom filter, principled query,
idiom reader. Commit db162df removed the idiom table from the middle and left one
on each side.

## 4. The redesign

1. **`_links` stops being a correctness gate.** Candidate pairs are bounded by
   *program structure* - proximity in the statement list, a shared cell or index -
   never by the shape of an operator. Cost is managed by how many pairs are asked
   about, not by pre-matching an idiom.
2. **`_word_form` stops naming operators.** The query returns whatever width-2
   term the e-graph proves `hi'<<8 | lo'` equal to, over `pack(hi, lo)`. A 16-bit
   shift is `W*2`; a bitwise pair is `W & K`; a predicated counter is `W+1`; an
   add is `W+step`. One reader, no catalogue.
3. **Predicated updates are normalised to values before the query.**
   `if carry(a, b) { INC c }` is `c = c + carry(a, b)`: the condition IS the carry
   the lo statement produced, so the conversion is provable, and it belongs in one
   if-conversion pass so the rung keeps working on values alone.
4. **The general borrow becomes one Z3-proven rule**, as §4 requires - not a pass.

## 5. The falsifiable prediction

The 48 undocumented-opcode shapes are the test of whether this is an abstraction
or another point fix. `SLO` is `ASL` plus `ORA`; `DCP` is `DEC` plus `CMP`; the
lifter already models them as pcode and their value graphs are those of the legal
sequences they fuse. **If the redesign is right, all 48 lift without one line
naming them.** If any of them needs its own clause, the design has failed and the
catalogue has grown a new page.

The same test applies to what is left of §7.3's refusals: a correct design refuses
them for reasons stated over values and program structure, never over an operator
name.

## 5.1 The prediction, answered (LANDED)

Implemented as §4 states. Over the same 610 shapes: **242 lifted -> 552, 368 no
site -> 34, 0 refused -> 24** (frameprog §7.5 for the table and the residue).

**36 of the 48 undocumented-opcode shapes lift, and no line of rung (d2) or of
its rule set names an opcode.** `SLO`'s statement list is byte-identical to
`ASL`'s and `SRE`'s to `LSR`'s, which is the abstraction working: the value
graphs are those of the legal sequences they fuse, and one general reader takes
them. `RLA` needed the rotate form of the shift law, which is one Z3-proven rule
in the same family, not a clause.

**`DCP` and `ISC` are the sharper test, because they first appeared to be a
refusal the machine justified.** They were enumerated with `LDA #$00`, which does
not make their flag the lo lane's wrap at all, so the shape genuinely was not
`W ± 1` and the first reading of the result was that the 6502 said no. It did not:
given the accumulators the real idioms use -- `$FF` for `DCP`, a set carry over
`A = 0` for `ISC` -- the only thing between them and the existing borrow and carry
laws was that `sub_eq0` hands a compare back as `eq(num, term)` while every rule
that moves a constant step across an equality matches `eq(term, num)`. One rule
saying equality is symmetric took them from 0 of 16 to 12 of 16. **The lesson is
the one §5 predicts: when a shape does not lift, the question is whether the
machine refuses it or the rule set merely cannot say it yet, and only the
disassembly settles which.**

**8 `RRA` shapes do not lift, and there the 6502 really does say so.** `RRA` is
`ROR` then `ADC`, so the bit the following `ROR lo` takes in is the **ADC's**
carry out, not the bit `ROR hi` shifted out. The pair does not compute `W>>1`, and
the refusal is stated over values.

**Cost first read as the thing the design could not absorb, and that was a
misattribution.** Asking every structurally admissible pair rather than one
pre-matched idiom does cost more saturation and more extraction, and the profile
was read that way. It is not where the time went. On `Arpeggio`, 62.2s of 103.7s
sat in `black.format_str`: `str()` on an extracted expression runs egglog's pretty
printer, which formats the Python source with **Black**, at ~16ms a term -- and
`_parse_ir` hands that string straight to `ast`, which does not care how it is
laid out. egglog's own note at that line proposes removing the pass. Bypassing it
takes `Arpeggio` from **103.7s to 22.0s**; measured directly, equality saturation
is 3.5s and extraction 4.9s of the original 88.9s, so the two things the design
actually added were never more than a tenth of it.

The lesson is worth as much as the redesign: a cost attributed to the algorithm
was accidental, in a string conversion used only to parse the term back. Profile
before you pay for a design decision.

## 6. What this does not change

Gate FP 649/649 and the canonical fixpoint 649/649 are hard gates throughout, and
determinism across at least eight `PYTHONHASHSEED` values is required - two seeds
missed a real divergence on this branch (§7.1). Anything unproven MUST refuse:
refusing is always safe, and a wrong lift corrupts the SID output silently.
