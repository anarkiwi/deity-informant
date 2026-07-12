# NMOS 6510 illegal opcodes

All 105 documented undocumented ("illegal") NMOS 6510 opcodes, lifted to genuine P-Code by `deity_informant.lift` and decoded by the `6510` SLEIGH spec (`ghidra/6510/data/languages/6510_illegal.sinc`). Semantics and cycle counts follow "No More Secrets — NMOS 6510 Unintended Opcodes" (current edition **V1.0**, 24 Dec 2025; page citations below are to the v0.91 edition consulted — see [nms-provenance.md](nms-provenance.md)). Page numbers cite NMS where a page comment exists in the `.sinc`; `—` = covered under NMS's combined-ops chapter without a per-opcode page comment.

Opcode bytes are taken from `deity_informant/lifter.py` `_build_ops()`.

## RMW combos (read-modify-write, then ALU on A)

Each spans the LDA-family memory modes: `(zp,X)` `zp` `abs` `(zp),Y` `zp,X` `abs,Y` `abs,X`.

| Mnemonic | Opcodes | Operation | NMS page |
|---|---|---|---|
| SLO (ASO) | 03 07 0F 13 17 1B 1F | ASL mem; A = A OR mem | — |
| RLA (RLN) | 23 27 2F 33 37 3B 3F | ROL mem; A = A AND mem | — |
| SRE (LSE) | 43 47 4F 53 57 5B 5F | LSR mem; A = A EOR mem | — |
| RRA (RRD) | 63 67 6F 73 77 7B 7F | ROR mem; A = A ADC mem | — |
| DCP (DCM) | C3 C7 CF D3 D7 DB DF | DEC mem; CMP A,mem | — |
| ISC (ISB) | E3 E7 EF F3 F7 FB FF | INC mem; SBC A,mem | — |

## Store combos

| Mnemonic | Opcodes | Operation | NMS page |
|---|---|---|---|
| SAX (AXS, AAX) | 83 87 8F 97 | mem = A AND X (no flags) | — |

Modes: `(zp,X)` `zp` `abs` `zp,Y`.

## Load combos

| Mnemonic | Opcodes | Operation | NMS page |
|---|---|---|---|
| LAX | A3 A7 AF B3 B7 BF | A = X = mem; set N,Z | — |

Modes: `(zp,X)` `zp` `abs` `(zp),Y` `zp,Y` `abs,Y`.

## Immediate / implied ALU

| Mnemonic | Opcodes | Operation | NMS page |
|---|---|---|---|
| ANC | 0B 2B | A = A AND #imm; C = bit7 | 25 |
| ALR (ASR) | 4B | A = A AND #imm; then LSR A | 27 |
| ARR | 6B | A = A AND #imm; then ROR A; C = bit6, V = bit6 EOR bit5 | 29 |
| SBX (AXS) | CB | X = (A AND X) - #imm; C from the compare | 32 |
| SBC (alias) | EB | identical to legal SBC #imm | 36 |
| ANE (XAA) | 8B | A = (A OR CONST) AND X AND #imm (magic constant) | 51 |
| LXA (LAX #, OAL) | AB | A = X = (A OR CONST) AND #imm (magic constant) | 53 |

## LAS

| Mnemonic | Opcodes | Operation | NMS page |
|---|---|---|---|
| LAS (LAR, LAE) | BB | A = X = S = mem AND S (`abs,Y`) | 37 |

## Unstable "store high" group (SH*)

Stable AND form: `mem = REG AND (high(base) + 1)`.

| Mnemonic | Opcodes | Operation | NMS page |
|---|---|---|---|
| SHA (AHX) | 93 9F | mem = A AND X AND (H+1) (`(zp),Y`, `abs,Y`) | 43-50 |
| SHX (SXA) | 9E | mem = X AND (H+1) (`abs,Y`) | 43-50 |
| SHY (SYA) | 9C | mem = Y AND (H+1) (`abs,X`) | 43-50 |
| TAS (SHS) | 9B | S = A AND X; mem = S AND (H+1) (`abs,Y`) | 43-50 |

## NOP illegals (DOP/TOP)

No architectural effect; the memory-mode forms still perform the read.

| Mnemonic | Opcodes | Mode | NMS page |
|---|---|---|---|
| NOP (implied) | 1A 3A 5A 7A DA FA | impl | 40 |
| NOP #imm | 80 82 89 C2 E2 | imm | 40 |
| NOP zp | 04 44 64 | zp | 40 |
| NOP zp,X | 14 34 54 74 D4 F4 | zp,X | 40 |
| NOP abs | 0C | abs | 40 |
| NOP abs,X | 1C 3C 5C 7C DC FC | abs,X | 40 |

## JAM (KIL, HLT)

| Mnemonic | Opcodes | Operation | NMS page |
|---|---|---|---|
| JAM | 02 12 22 32 42 52 62 72 92 B2 D2 F2 | CPU lock-up (halt / re-fetch self) | — |

---

## Magic constant

ANE (`$8B`) and LXA (`$AB`) mix a chip-/temperature-dependent constant into the result: `A = (A OR CONST) AND ...`. `CONST` is unstable; NMS lists common values `$00`/`$FF`/`$EE` (p.51, p.53). Default is `$EE` (matches the validated sidplayfp oracle), exposed as `deity_informant.MAGIC` and overridable at SLEIGH build time via `sleigh -D MAGIC=0x00` or `build.py --magic 0x00`.

## Unstable SH* group

Only the stable `REG AND (high(base)+1)` form is modelled. The page-cross and RDY-drop-off instabilities are non-deterministic per NMS (pp.43-50) and are intentionally not modelled.

## Decimal mode

ADC/SBC and the derived ARR/RRA/ISC do not model BCD (decimal) mode — matching the base 6502 SLEIGH spec. C64 playroutines execute `CLD`, so this is a documented limitation, not a correctness gap for the target domain.

## py65 is not a valid reference for RMW illegals

py65 stubs the RMW illegals (`SLO/RLA/SRE/RRA/DCP/ISC`, `LAS/ANE/SH*`) as `inst_not_implemented` — a bare 2-byte, 0-cycle skip with no semantics. It is therefore only a valid oracle for legal opcodes. The illegals are instead validated by NMS decomposition (each combo = its two documented micro-ops) and end-to-end against the sidplayfp oracle over 60 s of real playback.
