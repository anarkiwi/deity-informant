#!/bin/sh
# Ghidra headless integration tests + facts-driven high P-Code export for the
# 6510 SLEIGH module.
#
#   run.sh              -- the smoke tests (illegal decode, hello-world SMC export,
#                          complexity/coverage oracle, semantic oracle, the IRQ
#                          frame, the banking gate)
#   run.sh oracles      -- export and emulate $FACTS_DIR into $OUT_DIR, one session
#   run.sh export       -- the high P-Code export alone
#   run.sh emulate      -- the semantic oracle alone
#
# Requires: GHIDRA_INSTALL_DIR set, the 6510 module installed into it
# (build.py --install), deity_informant importable. See Dockerfile.ghidra.
set -eu

: "${GHIDRA_INSTALL_DIR:?set GHIDRA_INSTALL_DIR to a Ghidra install}"
HEADLESS="$GHIDRA_INSTALL_DIR/support/analyzeHeadless"
fail() { echo "GHIDRA HEADLESS: FAIL - $1" >&2; exit 1; }

# $1 project dir, $2 facts dir, $3 out dir, $4 log, $5 mode: one import and one
# analysis serve both scripts, so a certificate costs one session, not two.
headless() {
    proj="$1"; facts="$2"; out="$3"; log="$4"; mode="$5"
    mkdir -p "$proj" "$out"
    set -- "$HEADLESS" "$proj" export \
        -import "$facts/image_post_init.bin" \
        -loader BinaryLoader -loader-baseAddr 0x0000 \
        -processor "6510:LE:16:default" \
        -noanalysis \
        -scriptPath ghidra/6510/headless
    [ "$mode" = emulate ] || set -- "$@" -postScript ExportHighPcode.java "$facts" "$out"
    [ "$mode" = export ] || set -- "$@" -postScript EmulateTrace.java "$facts" "$out"
    "$@" -deleteProject 2>&1 | tee "$log"
    [ "$mode" = emulate ] || grep "HIGHPCODE-EXPORT-OK" "$log" || fail "no export (see log above)"
    [ "$mode" = export ] || grep "EMULATE-ORACLE-OK" "$log" || fail "no emulate (see log above)"
}

case "${1:-}" in
export | emulate | oracles)
    : "${FACTS_DIR:?set FACTS_DIR to a directory holding ghidra_facts.json}"
    OUT_DIR="${OUT_DIR:-$FACTS_DIR/ghidra-out}"
    WORK="$(mktemp -d)"
    trap 'rm -rf "$WORK"' EXIT
    headless "$WORK" "$FACTS_DIR" "$OUT_DIR" "$OUT_DIR/headless.log" "$1"
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

python3 tools/tuneprog_ghidra.py --demo hello "$WORK/facts"
headless "$WORK/p2" "$WORK/facts" "$WORK/out" "$WORK/export.log" oracles

# 2. the SMC export smoke test: the self-modified STA must decompile as a store
#    through the operand cell $100A, not to the constant $0400 in the image.
C="$WORK/out/hello.c"
[ -s "$C" ] || fail "no C emitted for the hello-world entry"
grep -q "STORE" "$WORK/out/hello.pcode" \
    || fail "high P-Code has no STORE: the SMC operand was folded to a constant"
grep -q "smc_100a" "$C" || fail "SMC store did not go through the cell \$100A: $(cat "$C")"
echo "GHIDRA SMC EXPORT: PASS (self-modified STA decompiled as a store through \$100A)"

# 3. the complexity oracle: clean flow over every executed site, nothing dropped
grep -q '"unresolved": 0' "$WORK/out/stats.json" || fail "unresolved control flow in hello"
grep -q '"unreachable": 0' "$WORK/out/stats.json" || fail "Ghidra dropped a block in hello"
grep -q '"uncovered_sites": 0' "$WORK/out/coverage.json" || fail "coverage oracle: uncovered sites"
grep -q '"pcs"' "$WORK/out/stats.json" || fail "stats.json carries no per-body address set"
echo "GHIDRA COMPLEXITY/COVERAGE ORACLE: PASS (clean flow, every executed site reached)"

# 4. the semantic oracle: Ghidra's emulator must reproduce HELLO, WORLD!
grep -q '"agree": true' "$WORK/out/emulate.json" \
    || fail "emulator disagreed: $(cat "$WORK/out/emulate.json")"
echo "GHIDRA SEMANTIC ORACLE: PASS (P-Code emulator reproduced the demo's screen codes)"

# 5. the same demo entered as an installed handler: the oracle has to push the
#    frame the 6510 pushes and stop on the balanced stack, an RTI reaching no
#    fake JSR's return address.
python3 tools/tuneprog_ghidra.py --demo irq "$WORK/irq"
headless "$WORK/p3" "$WORK/irq" "$WORK/irq-out" "$WORK/irq.log" emulate
grep -q '"agree": true' "$WORK/irq-out/emulate.json" \
    || fail "RTI-framed tick did not compare: $(cat "$WORK/irq-out/emulate.json")"
echo "GHIDRA IRQ FRAME: PASS (an RTI-entered tick is emulated on the machine's own frame)"

# 6. the banking gate: both stores land on $D400, only the I/O-mapped one is a
#    register change -- a memory diff would score the RAM store as a second one.
python3 tools/tuneprog_ghidra.py --demo bank "$WORK/bank"
headless "$WORK/p4" "$WORK/bank" "$WORK/bank-out" "$WORK/bank.log" emulate
grep -q '"agree": true' "$WORK/bank-out/emulate.json" \
    || fail "banked-out store counted as a SID write: $(cat "$WORK/bank-out/emulate.json")"
echo "GHIDRA BANKING GATE: PASS (a store under banked-out I/O is RAM, not a register)"
