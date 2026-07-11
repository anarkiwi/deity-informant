# Demo: HELLO, WORLD! with illegal opcodes + self-modifying code

A 33-byte C64 program at load address `$1000` that writes the 13 screen codes for
`HELLO, WORLD!` into screen RAM `$0400..$040C`. Self-contained: the VM run and the
Ghidra/pypcode disassembly below use only `deity_informant` + the 6510 SLEIGH spec.
Source: [`examples/hello_world.py`](../examples/hello_world.py).

## Program (org $1000)

```
$1000  A0 00        LDY #$00
$1002  BF 13 10     LAX $1013,Y     ; illegal: A=X=data[Y]; Z=1 at terminator
$1005  F0 0B        BEQ $1012        ; done when byte == 0 (rides LAX's Z flag)
$1007  49 FF        EOR #$FF         ; decrypt (data is bit-inverted)
$1009  8D 00 04     STA $0400        ; screen RAM; low operand byte is self-modified
$100C  EF 0A 10     ISC $100A        ; illegal: INC store-operand (SMC advance) + SBC(discarded)
$100F  C8           INY
$1010  D0 F0        BNE $1002        ; loop
$1012  60           RTS
$1013  F7 FA F3 F3 F0 D3 DF E8 F0 ED F3 FB DE 00   ; "HELLO, WORLD!" EOR $FF, then $00
```

Raw bytes (33 = 19 code [`$1000..$1012`] + 14 data [`$1013..$1020`]):

```
A0 00 BF 13 10 F0 0B 49 FF 8D 00 04 EF 0A 10 C8 D0 F0 60
F7 FA F3 F3 F0 D3 DF E8 F0 ED F3 FB DE 00
```

### Two load-bearing illegal opcodes + self-modification

- **LAX `$1013,Y` (`$BF`)** loads *both* A and X from `data[Y]` and sets Z in one
  instruction. The loop terminator `BEQ` at `$1005` rides on that Z flag: when LAX
  reads the `$00` terminator, Z=1 and the loop exits. A legal `LDA` would work, but
  the point is that LAX is the load and the flag-setter; remove it and there is no
  terminator test.
- **ISC `$100A` (`$EF`)** is read-modify-write: `INC $100A` then `SBC`. `$100A` is the
  **low byte of the `STA $0400` operand** (the `00` in `8D 00 04` at `$1009`). Each
  pass INCs it: `$00 -> $01 -> $02 ...`, so the very next store lands at
  `$0401, $0402, ...` -- the code rewrites its own store target. ISC's SBC side
  effect writes garbage into A, which is immediately overwritten by the next LAX, so
  only the INC (the self-modification) is load-bearing.
- The message is stored EOR-`$FF` (bit-inverted) and decrypted at `$1007` with
  `EOR #$FF` -- so `$F7 -> $08` (screen code `H`), etc.

Because `$100A` changes every iteration, the VM's `(pc, bytes)`-keyed lift cache sees
a new instruction at `$1009` each pass and **re-lifts** it -- 13 distinct operands,
exercising the self-modifying-code path.

## Run it (pure Python VM)

```
$ python examples/hello_world.py
HELLO, WORLD!
illegal opcodes executed: $BF LAX, $EF ISC
self-modifying STA at $1009 re-lifted 13 times (one per store target)
```

The VM lays `PROGRAM` at `$1000`, runs it to the `RTS` with `run_sub`, and reads back
`$0400..$040C` = `08 05 0C 0C 0F 2C 20 17 0F 12 0C 04 21`.

## Through Ghidra

pypcode *is* Ghidra's SLEIGH engine (libsla), so disassembling with the compiled
`6510` spec exercises the exact path Ghidra's GUI uses.

### (a) Automated -- what CI checks

Dump the raw program and disassemble it two ways. First build+install the 6510 spec
(see [docs/ghidra.md](ghidra.md)), then:

```
$ python examples/hello_world.py --write /tmp/hello.prg
wrote 33 raw bytes to /tmp/hello.prg (org $1000)

$ deity-informant disasm /tmp/hello.prg --org 0x1000 --count 19
$1000: A0  LDY  #$00
$1002: BF  LAX  $1013,Y  ; illegal
$1005: F0  BEQ  $1012
$1007: 49  EOR  #$FF
$1009: 8D  STA  $0400
$100C: EF  ISC  $100A  ; illegal
$100F: C8  INY
$1010: D0  BNE  $1002
$1012: 60  RTS
```

Same bytes through the **6510 SLEIGH engine** (pypcode / libsla):

```python
>>> import pypcode
>>> ctx = pypcode.Context("6510:LE:16:default")     # after build.py --install
>>> for i in ctx.disassemble(open("/tmp/hello.prg","rb").read()[:19], 0x1000, 0).instructions:
...     print("$%04X %s %s" % (i.addr.offset, i.mnem, i.body))
$1000 LDY #0x0
$1002 LAX 0x1013,Y
$1005 BEQ 0x1012
$1007 EOR #0xff
$1009 STA 0x400
$100C ISC 0x100a
$100F INY
$1010 BNE 0x1002
$1012 RTS
```

Stock 6502 (which pypcode vendors verbatim, zero illegals) cannot decode the two
illegal bytes at all:

```python
>>> stock = pypcode.Context("6502:LE:16:default")
>>> stock.disassemble(bytes([0xBF,0x13,0x10]), 0x1000, 0)   # LAX
pypcode.BadDataError: r0x1000: Unable to resolve constructor
>>> stock.disassemble(bytes([0xEF,0x0A,0x10]), 0x1000, 0)   # ISC
pypcode.BadDataError: r0x1000: Unable to resolve constructor
```

`tests/test_hello_world.py::test_disassembles_through_ghidra_sleigh` asserts exactly
this: `6510` decodes LAX at `$1002` and ISC at `$100C`, while stock `6502` raises on
both -- the gap this project closes.

### (b) Manual -- Ghidra GUI

1. Build + install the module, then restart Ghidra:
   ```bash
   python ghidra/6510/build.py --install "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages"
   ```
2. `python examples/hello_world.py --write hello.prg` to get the 33 raw bytes.
3. `File > Import` `hello.prg` as **Raw Binary**, language `6510:LE:16:default`, base
   `$0000` (or set the memory block to load at `$1000`).
4. Go to `$1000`, `Disassemble` (`D`). The illegal bytes `$BF`/`$EF` now decode as
   **LAX**/**ISC** instead of `??` -- exactly what the automated leg verifies.
5. Open the **Decompiler**. It shows the copy loop, the `EOR #$FF` decrypt, and the
   store into screen RAM. Because `ISC` writes into the `STA` operand at `$100A`, the
   decompiler renders a store *into code space* -- the self-modification made visible.

Honest scope: CI verifies the disassembly and P-Code through pypcode (the same libsla
engine); the full GUI decompile is the manual step above.

## See also

- [docs/ghidra.md](ghidra.md) -- building/installing the 6510 SLEIGH module.
- [docs/illegal-opcodes.md](illegal-opcodes.md) -- LAX, ISC, and the full illegal set.
