// Ghidra headless post-script: prove the installed 6510:LE:16:default language
// decodes the load-bearing illegal opcodes the hello-world demo relies on, and
// dump their P-Code straight from Ghidra's decompiler engine.
//
// Run by ghidra/6510/headless/run.sh via analyzeHeadless. The raw 33-byte demo
// image is imported at base $1000; this script disassembles from $1000, walks the
// code region [$1000..$1012], prints each instruction and its raw P-Code, and
// asserts LAX@$1002 and ISC@$100C decoded (stock 6502 raises BadData on both).
//@category deity-informant
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.pcode.PcodeOp;

public class DumpPcode extends GhidraScript {
    private static final long ORG = 0x1000L;
    private static final long CODE_END = 0x1012L; // last opcode (RTS); $1013.. is data

    @Override
    public void run() throws Exception {
        Address start = toAddr(ORG);
        new DisassembleCommand(start, null, true).applyTo(currentProgram, monitor);

        boolean lax = false;
        boolean isc = false;
        InstructionIterator it = currentProgram.getListing().getInstructions(start, true);
        while (it.hasNext()) {
            Instruction insn = it.next();
            long addr = insn.getAddress().getOffset();
            if (addr > CODE_END) {
                break;
            }
            println(String.format("INSN %04X %s", addr, insn.toString()));
            for (PcodeOp op : insn.getPcode()) {
                println("  PCODE " + op.toString());
            }
            String mnem = insn.getMnemonicString();
            lax |= addr == 0x1002L && "LAX".equals(mnem);
            isc |= addr == 0x100cL && "ISC".equals(mnem);
        }

        if (lax && isc) {
            println("PCODE-INTEGRATION-OK LAX@1002 ISC@100C");
        } else {
            String msg = "PCODE-INTEGRATION-FAIL lax=" + lax + " isc=" + isc;
            printerr(msg);
            throw new Exception(msg);
        }
    }
}
