# Reference-source provenance

## Authoritative source

All illegal-opcode semantics and cycle counts in this project derive from
**"No More Secrets — NMOS 6510 Unintended Opcodes"** by Groepaz / The Solution.

- Canonical release (current authoritative edition, **V1.0**):
  <https://csdb.dk/release/?id=258111>

The document is external reference material and is **NOT committed** to this
repository. Nothing here reproduces its text.

## Validation

The implementation follows No More Secrets and was validated:

- against the **v0.91** NMS text (semantics and the cycle chart), and
- **byte-exact against the sidplayfp hardware oracle** over 60 s of real
  playback, with illegal opcodes executing as load-bearing instructions.

Because the implementation is pinned to the hardware oracle, its semantics are
hardware-correct and hold across NMS doc revisions; V1.0 is the current
authoritative edition and does not change the validated behaviour.

## Cycle-count cross-check

The frozen `CYCLETIME` / `EXTRACYCLES` tables in `deity_informant/lifter.py` were
cross-checked against the NMS reference chart. Spot checks:

- RMW illegals (SLO/RLA/SRE/RRA/DCP/ISC) `abs,X` / `abs,Y` = **7** cycles.
- RMW illegals `(zp,X)` / `(zp),Y` = **8** cycles.
- LAX `(zp),Y` = **5 + 1** on page cross.

These match the values in the frozen tables, which carry no runtime py65
dependency.

## Per-opcode page citations

Per-opcode NMS page numbers appear inline in
`ghidra/6510/data/languages/6510_illegal.sinc` and are collected in
[illegal-opcodes.md](illegal-opcodes.md): ANC p.25, ALR p.27, ARR p.29,
SBX p.32, SBC p.36, LAS p.37, NOP p.40, SH* group pp.43-50, ANE p.51, LXA p.53.
