#!/bin/sh
# Ghidra headless integration test for the 6510 SLEIGH module: the hello-world
# demo imported and its illegal LAX/ISC decoded under the headless analyzer.
#
# Requires: GHIDRA_INSTALL_DIR set, the 6510 module installed into it
# (build.py --install), deity_informant importable. See Dockerfile.ghidra.
set -eu

: "${GHIDRA_INSTALL_DIR:?set GHIDRA_INSTALL_DIR to a Ghidra install}"
HEADLESS="$GHIDRA_INSTALL_DIR/support/analyzeHeadless"
fail() { echo "GHIDRA HEADLESS: FAIL - $1" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# the illegal-opcode decode smoke test
python3 examples/hello_world.py --write "$WORK/hello.prg"
OUT="$WORK/headless.log"
"$HEADLESS" "$WORK" hello \
    -import "$WORK/hello.prg" \
    -loader BinaryLoader -loader-baseAddr 0x1000 \
    -processor "6510:LE:16:default" \
    -noanalysis \
    -scriptPath ghidra/6510/headless -postScript DumpPcode.java \
    -deleteProject 2>&1 | tee "$OUT"

grep -q "PCODE-INTEGRATION-OK" "$OUT" || fail "OK marker missing (see log above)"
grep -qi "INSN 1002 LAX" "$OUT" || fail "LAX did not decode at \$1002"
grep -qi "INSN 100C ISC" "$OUT" || fail "ISC did not decode at \$100C"
echo "GHIDRA HEADLESS INTEGRATION: PASS (6510 decoded LAX+ISC under Ghidra headless)"
