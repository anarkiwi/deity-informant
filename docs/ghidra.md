# Use with Ghidra

The `6510` SLEIGH module makes Ghidra's disassembler and decompiler illegal-aware
(language id `6510:LE:16:default`).

## Steps

1. **Build and install** the module into a Ghidra languages directory:

   ```bash
   python ghidra/6510/build.py --install "$GHIDRA_INSTALL_DIR/Ghidra/Processors/6510/data/languages"
   ```

   `build.py` resolves the stock `6502.slaspec` and the SLEIGH compiler from
   `$GHIDRA_INSTALL_DIR`, compiles `6510.sla`, and copies `6510.sla` +
   `6510.ldefs`/`.pspec`/`.cspec` into the target directory.

2. **Restart Ghidra** so it re-scans processor languages.

3. **Import** the C64 memory image as a **Raw Binary** with language
   `6510:LE:16:default` and base address `$0000`.

4. **Disassemble / decompile.** Illegal opcodes now decode as first-class
   instructions instead of `BadData`, and the decompiler renders them in its C
   output.

5. **Magic constant override** (ANE `$8B` / LXA `$AB`): rebuild with a different
   constant, e.g. `--magic 0x00`:

   ```bash
   python ghidra/6510/build.py --magic 0x00 --install <languages-dir>
   ```

   The default is `$EE`, matching the validated sidplayfp oracle.

## pypcode

pypcode users load the same module into the pypcode processors tree:

```bash
python ghidra/6510/build.py --install "$(python -c 'import pypcode,pathlib;print(pathlib.Path(pypcode.__file__).parent/"processors/6510/data/languages")')"
```

`build.py` uses pypcode's bundled SLEIGH compiler and stock `6502.slaspec` when
`$GHIDRA_INSTALL_DIR` is unset.
