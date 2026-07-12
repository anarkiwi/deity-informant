# Reference-source provenance

## Authoritative source

All illegal-opcode semantics and cycle counts in this project derive from
**"No More Secrets — NMOS 6510 Unintended Opcodes"** by Groepaz / The Solution.

- Canonical release (current authoritative edition, **V1.0**, 24 Dec 2025):
  <https://csdb.dk/release/?id=258111>

The document is external reference material and is **NOT committed** to this
repository. Nothing here reproduces its text.

## Edition history and reconciliation

NMS is published on 24 December each year. Relevant editions:

| Edition | Date | Notes |
|---|---|---|
| v0.91 | 2016 | Text consulted for the inline per-opcode page citations below. |
| v0.95 | 2020 | |
| v0.96 | 2022 | |
| v0.99 | 2024 | |
| **V1.0** | **2025** | Current authoritative edition. |

V1.0 is a maturity/version bump, not a semantic revision: the author's release
note states V1.0 was cut because there had been *no bug report since the previous
release* and new content had dried up — "it was about time to justify a 1.0
release." No edition since v0.91 changes any opcode semantics or cycle count this
project depends on, and none of the V1.0 release discussion reports an opcode,
flag, or cycle correction.

Independently of the doc edition, the implementation is pinned to a **hardware
oracle** (below), so its behaviour is hardware-correct regardless of which NMS
revision is consulted. No code change is required to track V1.0.

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

Per-opcode page numbers appear inline in
`ghidra/6510/data/languages/6510_illegal.sinc` and are collected in
[illegal-opcodes.md](illegal-opcodes.md). They cite the **v0.91** edition
consulted for those comments (V1.0 repaginates but does not change the cited
semantics): ANC p.25, ALR p.27, ARR p.29, SBX p.32, SBC p.36, LAS p.37,
NOP p.40, SH* group pp.43-50, ANE p.51, LXA p.53.
