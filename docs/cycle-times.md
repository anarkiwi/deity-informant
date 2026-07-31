# Instruction cycle times

`CYCLETIME[op]` is the base cost of an opcode; `EXTRACYCLES[op]` is the penalty
the interpreter adds when it applies — `+1` on a page cross for the read-indexed
forms, and for a relative branch `+1` taken / `+2` taken across a page. Both are
in `deity_informant/lifter.py`.

## References

Neither document is committed here; nothing below reproduces their text.

| Set | Count | Reference |
|---|---|---|
| Documented | 151 | MOS Technology, *MCS6500 Microcomputer Family Programming Manual* (2nd ed., 1976), **Appendix B** — opcode, bytes and cycles per addressing mode, one page per mnemonic. Scan: <https://www.6502.org/documents/books/mcs6500_family_programming_manual.pdf> |
| Undocumented | 93 | *No More Secrets — NMOS 6510 Unintended Opcodes* — per-opcode `Size`/`Cycles` columns with `(+1)` page-cross markers. See [nms-provenance.md](nms-provenance.md). |
| JAM / KIL | 12 | Lock-up; no defined count. Modelled as `0` — the instruction never completes. |

Appendix B marks the page-cross forms with `*` and states the branch rule
directly: *add 1 if the branch occurs to the same page, add 2 if to a different
page*. Both tables were transcribed from those columns and diffed entry by entry
against all 512 values.

py65 is **not** a reference for this table. On the legal opcode set the two agree
everywhere — including where py65 is wrong (see below) — and elsewhere py65
leaves the 93 illegals at `0`, so it can only ever confirm what it inherited.

## Audit result

One discrepancy in 512 entries, fixed:

- **`$CE DEC abs` was charged 3 cycles; it takes 6.** Appendix B, page B-13:
  `Absolute / DEC Oper / CE / 3 bytes / 6 cycles`. It was the sole outlier in the
  read-modify-write absolute family (`$0E $2E $4E $6E $EE` were all already 6),
  and `DEC`'s other modes were all correct, so this was a single-entry typo
  inherited from py65's `@instruction(name="DEC", mode="abs", cycles=3)`.
  Anything cycle-exact touching `DEC abs` was short by 3 cycles per execution.

`EXTRACYCLES` was correct in all 256 entries, including the two cases with no
cross-check: the illegal read forms that do take the penalty (`$B3 LAX (zp),y`,
`$BB LAS abs,y`, `$BF LAX abs,y`, the six `NOP abs,x`) and the RMW indexed forms
that do not.

## The invariant the test asserts

`tests/test_cycletime.py` does not hard-code 256 numbers. Cost on the NMOS 6502
is a function of `(class, addressing mode)` alone, so the test derives every
entry from a three-row table and compares:

| Class | zp | zp,x/y | abs | abs,x | abs,y | (zp,x) | (zp),y | imm |
|---|---|---|---|---|---|---|---|---|
| read (`LDA` … `LAX` `LAS` `NOP`) | 3 | 4 | 4 | 4+1 | 4+1 | 6 | 5+1 | 2 |
| write (`STA` `SAX` `SHA` `SHX` `SHY` `TAS`) | 3 | 4 | 4 | 5 | 5 | 6 | 6 | — |
| RMW (`ASL` `ROL` `LSR` `ROR` `DEC` `INC` `SLO` `RLA` `SRE` `RRA` `DCP` `ISC`) | 5 | 6 | 6 | 7 | 7 | 8 | 8 | — |

with branches at 2 (+2), accumulator/implied at 2, and the nine control and stack
opcodes named individually. A RMW indexed form is a **fixed** 7: it always
writes, so it pays the dummy read whether or not the index crosses a page.

The suite also steps `PcodeVM` once per opcode and asserts the charged cycles
equal `CYCLETIME[op]` — the table being right and the interpreter charging it are
two separate claims.
