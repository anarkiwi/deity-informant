#!/bin/sh
# Ghidra headless integration tests + facts-driven high P-Code export for the
# 6510 SLEIGH module.
#
#   run.sh              -- the two smoke tests (illegal decode, hello-world SMC export)
#   run.sh export       -- run ExportHighPcode over $FACTS_DIR into $OUT_DIR
#
# Requires: GHIDRA_INSTALL_DIR set, the 6510 module installed into it
# (build.py --install), deity_informant importable. See Dockerfile.ghidra.
set -eu

: "${GHIDRA_INSTALL_DIR:?set GHIDRA_INSTALL_DIR to a Ghidra install}"
HEADLESS="$GHIDRA_INSTALL_DIR/support/analyzeHeadless"
fail() { echo "GHIDRA HEADLESS: FAIL - $1" >&2; exit 1; }

# $1 project dir, $2 facts dir, $3 out dir, $4 log, $5 script, $6 marker
headless() {
    mkdir -p "$1" "$3"
    "$HEADLESS" "$1" export \
        -import "$2/image_post_init.bin" \
        -loader BinaryLoader -loader-baseAddr 0x0000 \
        -processor "6510:LE:16:default" \
        -noanalysis \
        -scriptPath ghidra/6510/headless \
        -postScript "$5" "$2" "$3" \
        -deleteProject 2>&1 | tee "$4"
    grep -q "$6" "$4" || fail "$6 missing (see log above)"
}

export_facts() {
    headless "$1" "$2" "$3" "$4" ExportHighPcode.java HIGHPCODE-EXPORT-OK
}

case "${1:-}" in
export | emulate)
    : "${FACTS_DIR:?set FACTS_DIR to a directory holding ghidra_facts.json}"
    OUT_DIR="${OUT_DIR:-$FACTS_DIR/ghidra-out}"
    WORK="$(mktemp -d)"
    trap 'rm -rf "$WORK"' EXIT
    if [ "$1" = "export" ]; then
        export_facts "$WORK" "$FACTS_DIR" "$OUT_DIR" "$OUT_DIR/headless.log"
        grep "HIGHPCODE-EXPORT-OK" "$OUT_DIR/headless.log"
    else
        headless "$WORK" "$FACTS_DIR" "$OUT_DIR" "$OUT_DIR/emulate.log" \
            EmulateTrace.java EMULATE-ORACLE-OK
        grep "EMULATE-ORACLE-OK" "$OUT_DIR/emulate.log"
    fi
    exit 0
    ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. the illegal-opcode decode smoke test (unchanged)
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

# 2. the SMC export smoke test: the self-modified STA must decompile as a store
#    through the operand cell $100A, not to the constant $0400 in the image.
python3 tools/tuneprog_ghidra.py --hello "$WORK/facts"
export_facts "$WORK/p2" "$WORK/facts" "$WORK/out" "$WORK/export.log"
C="$WORK/out/hello.c"
[ -s "$C" ] || fail "no C emitted for the hello-world entry"
grep -q "STORE" "$WORK/out/hello.pcode" \
    || fail "high P-Code has no STORE: the SMC operand was folded to a constant"
grep -q "smc_100a" "$C" || fail "SMC store did not go through the cell \$100A: $(cat "$C")"
grep -q "HIGHPCODE-EXPORT-OK" "$WORK/export.log"
echo "GHIDRA SMC EXPORT: PASS (self-modified STA decompiled as a store through \$100A)"

# 3. the complexity oracle: clean flow over every executed site, nothing dropped
grep -q '"unresolved": 0' "$WORK/out/stats.json" || fail "unresolved control flow in hello"
grep -q '"unreachable": 0' "$WORK/out/stats.json" || fail "Ghidra dropped a block in hello"
grep -q '"uncovered_sites": 0' "$WORK/out/coverage.json" || fail "coverage oracle: uncovered sites"
echo "GHIDRA COMPLEXITY/COVERAGE ORACLE: PASS (clean flow, every executed site reached)"

# 4. the semantic oracle: Ghidra's emulator must reproduce HELLO, WORLD!
headless "$WORK/p3" "$WORK/facts" "$WORK/out" "$WORK/emu.log" EmulateTrace.java EMULATE-ORACLE-OK
grep -q '"agree": true' "$WORK/out/emulate.json" || fail "emulator disagreed: $(cat "$WORK/out/emulate.json")"
echo "GHIDRA SEMANTIC ORACLE: PASS (P-Code emulator reproduced the demo's screen codes)"
