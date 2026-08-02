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

## 6. What this does not change

Gate FP 649/649 and the canonical fixpoint 649/649 are hard gates throughout, and
determinism across at least eight `PYTHONHASHSEED` values is required - two seeds
missed a real divergence on this branch (§7.1). Anything unproven MUST refuse:
refusing is always safe, and a wrong lift corrupts the SID output silently.
