// Apply tuneprog's dynamic facts to a raw 6510 image, export Ghidra's own high
// P-Code + C for every entry procedure, and measure both complexity and coverage.
//
// Usage (see run.sh): analyzeHeadless ... -noanalysis -postScript
//   ExportHighPcode.java <factsDir> <outDir>
// factsDir holds ghidra_facts.json + image_post_init.bin as written by
// deity_informant.tuneprog.ghidra_facts. SMC cell addresses become contextreg
// values, so the SLEIGH constructors that read an operand from the instruction's
// own bytes fire and self-modified operands decompile as globals.
//@category deity-informant
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.FlowOverride;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.ProgramContext;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.pcode.HighFunction;
import ghidra.program.model.pcode.JumpTable;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.PcodeOpAST;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.symbol.RefType;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.SourceType;

import java.io.BufferedWriter;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

public class ExportHighPcode extends GhidraScript {

    private static final int TIMEOUT = 240;
    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
    private JsonObject facts;
    private Path out;

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        Path factsDir = Paths.get(args.length > 0 ? args[0] : ".");
        out = Paths.get(args.length > 1 ? args[1] : factsDir.resolve("ghidra-out").toString());
        Files.createDirectories(out);
        try (java.io.Reader r = Files.newBufferedReader(factsDir.resolve("ghidra_facts.json"))) {
            facts = JsonParser.parseReader(r).getAsJsonObject();
        }
        // the image is C64 RAM, not ROM: without this the decompiler folds loads
        // of the SMC cells back to the bytes the post-init image happens to hold
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            b.setWrite(true);
        }
        boolean smc = !"0".equals(System.getenv("SMC"));  // SMC=0 exports the A/B baseline
        if (smc) {
            applyContext();
        }
        disassemble();
        applyReferences();
        List<Function> entries = createEntries();
        analyzeAll(currentProgram);
        renameEntries();
        int overrides = applyJumpTables();
        annotate();
        Map<String, Set<Address>> owned = ownedSites(entries);
        coverage(entries);
        int freed = smc ? freeCells(entries) : 0;
        println("APPLIED smc=" + smc + " cells=" + facts.getAsJsonArray("smc_cells").size()
                + " entries=" + entries.size() + " jumptables=" + overrides + " freed_bodies="
                + freed);
        decompileAll(entries, owned, overrides);
    }

    // ---- facts -> program ---------------------------------------------------

    private void applyContext() throws Exception {
        ProgramContext pctx = currentProgram.getProgramContext();
        for (JsonElement e : facts.getAsJsonArray("smc_cells")) {
            JsonObject c = e.getAsJsonObject();
            Address a = toAddr(c.get("pc").getAsLong());
            for (JsonElement n : c.getAsJsonArray("context")) {
                Register reg = pctx.getRegister(n.getAsString());
                if (reg == null) {
                    throw new Exception("no context register " + n.getAsString());
                }
                pctx.setValue(reg, a, a, BigInteger.ONE);
            }
        }
    }

    private AddressSet executed() {
        AddressSet set = new AddressSet();
        for (JsonElement e : facts.getAsJsonArray("insn_addrs")) {
            set.add(toAddr(e.getAsLong()));
        }
        return set;
    }

    private void disassemble() {
        AddressSet set = executed();
        for (JsonElement e : facts.getAsJsonArray("entries")) {
            set.add(toAddr(e.getAsJsonObject().get("addr").getAsLong()));
        }
        new DisassembleCommand(set, null, true).applyTo(currentProgram, monitor);
    }

    private void applyReferences() {
        for (JsonElement e : facts.getAsJsonArray("computed_jumps")) {
            JsonObject j = e.getAsJsonObject();
            Address from = toAddr(j.get("pc").getAsLong());
            for (JsonElement t : j.getAsJsonArray("targets")) {
                currentProgram.getReferenceManager().addMemoryReference(from,
                        toAddr(t.getAsLong()), RefType.COMPUTED_JUMP, SourceType.USER_DEFINED,
                        Reference.MNEMONIC);
            }
        }
        JsonArray tails = facts.getAsJsonArray("tail_calls");
        for (JsonElement e : tails == null ? new JsonArray() : tails) {
            Instruction insn =
                    getInstructionAt(toAddr(e.getAsJsonObject().get("pc").getAsLong()));
            if (insn != null) {
                insn.setFlowOverride(FlowOverride.CALL_RETURN);
            }
        }
    }

    private List<Function> createEntries() {
        List<Function> fns = new ArrayList<>();
        for (JsonElement e : facts.getAsJsonArray("entries")) {
            JsonObject p = e.getAsJsonObject();
            Address a = toAddr(p.get("addr").getAsLong());
            Function f = getFunctionAt(a);
            if (f == null) {
                f = createFunction(a, p.get("name").getAsString());
            }
            if (f != null) {
                fns.add(f);
            } else {
                println("WARN could not create function at " + a);
            }
        }
        return fns;
    }

    /** Analysis (thunks, shared returns) renames functions, so restore ours. */
    private void renameEntries() {
        for (JsonElement e : facts.getAsJsonArray("entries")) {
            JsonObject p = e.getAsJsonObject();
            Function f = getFunctionAt(toAddr(p.get("addr").getAsLong()));
            String want = p.get("name").getAsString();
            if (f == null || f.getName().equals(want)) {
                continue;
            }
            try {
                f.setName(want, SourceType.USER_DEFINED);
            } catch (Exception ex) {
                println("WARN rename " + f.getName() + " -> " + want + ": " + ex.getMessage());
            }
        }
    }

    private int applyJumpTables() {
        int n = 0;
        for (JsonElement e : facts.getAsJsonArray("computed_jumps")) {
            JsonObject j = e.getAsJsonObject();
            Address op = toAddr(j.get("pc").getAsLong());
            Function f = getFunctionContaining(op);
            ArrayList<Address> dests = new ArrayList<>();
            for (JsonElement t : j.getAsJsonArray("targets")) {
                dests.add(toAddr(t.getAsLong()));
            }
            if (f == null || dests.isEmpty()) {
                continue;
            }
            try {
                new JumpTable(op, dests, true, 0).writeOverride(f);
                n++;
            } catch (Exception ex) {
                println("WARN jump table at " + op + ": " + ex.getMessage());
            }
        }
        return n;
    }

    private void annotate() throws Exception {
        for (JsonElement e : facts.getAsJsonArray("regions")) {
            JsonObject r = e.getAsJsonObject();
            Address a = toAddr(r.get("base").getAsLong());
            createLabel(a, r.get("name").getAsString(), true);
            setPlateComment(a, String.format("%s region: %d bytes, stride %d, %d cells",
                    r.get("kind").getAsString(), r.get("size").getAsInt(),
                    r.get("stride").getAsInt(), r.get("count").getAsInt()));
        }
        for (JsonElement e : facts.getAsJsonArray("smc_cells")) {
            JsonObject c = e.getAsJsonObject();
            setPreComment(toAddr(c.get("pc").getAsLong()),
                    "SMC " + c.getAsJsonArray("kinds") + " cells=" + c.getAsJsonArray("cells")
                            + " variants=" + c.getAsJsonArray("variants"));
            for (JsonElement cell : c.getAsJsonArray("cells")) {
                long a = cell.getAsLong();
                createLabel(toAddr(a), String.format("smc_%04x", a), true);
            }
        }
        for (JsonElement e : facts.getAsJsonArray("inputs")) {
            JsonObject i = e.getAsJsonObject();
            setEOLComment(toAddr(i.get("pc").getAsLong()),
                    "pinned input " + i.get("kind").getAsString());
        }
    }

    /** Executed sites each function's body owns, before the bodies are trimmed. */
    private Map<String, Set<Address>> ownedSites(List<Function> fns) {
        Map<String, Set<Address>> owned = new LinkedHashMap<>();
        for (Function f : fns) {
            Set<Address> mine = new HashSet<>();
            for (Address a : executed().getAddresses(true)) {
                if (f.getBody().contains(a)) {
                    mine.add(a);
                }
            }
            owned.put(f.getName(), mine);
        }
        return owned;
    }

    /**
     * Take the SMC operand bytes out of every function body.
     *
     * DecompileCallback.encodeFunction declares any address inside a function body
     * CONSTANT, which folds a cell load back to the byte the post-init image holds.
     * Those bytes are data, so removing them restores the load.
     */
    private int freeCells(List<Function> fns) {
        AddressSet cells = new AddressSet();
        for (JsonElement e : facts.getAsJsonArray("smc_cells")) {
            JsonObject c = e.getAsJsonObject();
            long pc = c.get("pc").getAsLong();
            int len = c.get("len").getAsInt();
            // an opcode cell is data too: its own byte varies, so free the whole extent
            long first = c.getAsJsonArray("kinds").toString().contains("opcode") ? pc : pc + 1;
            if (pc + len - 1 >= first) {
                cells.addRange(toAddr(first), toAddr(pc + len - 1));
            }
        }
        int n = 0;
        for (Function f : fns) {
            AddressSet body = new AddressSet(f.getBody()).subtract(cells);
            if (body.getNumAddresses() == f.getBody().getNumAddresses()) {
                continue;
            }
            try {
                f.setBody(body);
                n++;
            } catch (Exception ex) {
                println("WARN body of " + f.getName() + ": " + ex.getMessage());
            }
        }
        return n;
    }

    // ---- coverage oracle ----------------------------------------------------

    private void coverage(List<Function> fns) throws Exception {
        AddressSet exec = executed();
        AddressSet reachable = new AddressSet();
        AddressSet uncovered = new AddressSet();
        int uncoveredSites = 0;
        InstructionIterator it = currentProgram.getListing().getInstructions(true);
        while (it.hasNext()) {
            Instruction insn = it.next();
            Address a = insn.getAddress();
            reachable.add(a);
            if (!exec.contains(a)) {
                uncoveredSites++;
                uncovered.addRange(a, a.add(insn.getLength() - 1));
            }
        }
        List<Map<String, Object>> ranges = new ArrayList<>();
        for (AddressRange r : uncovered.getAddressRanges()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("start", r.getMinAddress().toString());
            row.put("end", r.getMaxAddress().toString());
            row.put("bytes", r.getLength());
            Function f = getFunctionContaining(r.getMinAddress());
            row.put("function", f == null ? "" : f.getName());
            row.put("why", why(r.getMinAddress()));
            ranges.add(row);
        }
        Map<String, Object> doc = new LinkedHashMap<>();
        doc.put("executed_sites", exec.getNumAddresses());
        doc.put("reachable_sites", reachable.getNumAddresses());
        doc.put("uncovered_sites", uncoveredSites);
        doc.put("functions", fns.size());
        doc.put("uncovered", ranges);
        Files.writeString(out.resolve("coverage.json"), GSON.toJson(doc));
    }

    /** Why Ghidra reached an address the trace never executed. */
    private String why(Address a) {
        for (Reference r : currentProgram.getReferenceManager().getReferencesTo(a)) {
            if (r.getReferenceType() == RefType.COMPUTED_JUMP) {
                return "table_arm";
            }
            Instruction from = getInstructionAt(r.getFromAddress());
            if (from != null && from.getFlowType().isConditional()) {
                return "untaken_branch";
            }
        }
        Instruction prev = getInstructionBefore(a);
        if (prev == null) {
            return "other";
        }
        if (prev.getFlowType().isConditional()) {
            return "untaken_branch";
        }
        // a range Ghidra decoded past the last executed site: not entered at all
        return executed().contains(prev.getAddress()) ? "unentered_block" : "block_tail";
    }

    // ---- decompile + complexity --------------------------------------------

    private void decompileAll(List<Function> fns, Map<String, Set<Address>> owned, int overrides)
            throws Exception {
        DecompInterface ifc = new DecompInterface();
        ifc.setOptions(new DecompileOptions());
        ifc.toggleSyntaxTree(true);
        ifc.setSimplificationStyle("decompile");
        if (!ifc.openProgram(currentProgram)) {
            throw new Exception("decompiler did not open: " + ifc.getLastMessage());
        }
        List<Map<String, Object>> rows = new ArrayList<>();
        Map<String, Long> tot = new LinkedHashMap<>();
        for (Function f : fns) {
            Map<String, Object> row = decompileOne(ifc, f, owned.get(f.getName()));
            rows.add(row);
            for (String k : new String[] {"sites", "raw_pcode_ops", "pcode_ops", "c_lines",
                    "gotos", "unresolved", "warnings", "uniques", "unreachable", "ms"}) {
                tot.merge(k, ((Number) row.get(k)).longValue(), Long::sum);
            }
        }
        ifc.dispose();
        Map<String, Object> doc = new LinkedHashMap<>(tot);
        doc.put("functions", rows.size());
        doc.put("jumptable_overrides", overrides);
        doc.put("per_function", rows);
        Files.writeString(out.resolve("stats.json"), GSON.toJson(doc));
        println(String.format("HIGHPCODE-EXPORT-OK functions=%d sites=%d raw_ops=%d pcode_ops=%d"
                + " c_lines=%d gotos=%d unresolved=%d jumptables=%d", rows.size(),
                tot.get("sites"), tot.get("raw_pcode_ops"), tot.get("pcode_ops"),
                tot.get("c_lines"), tot.get("gotos"), tot.get("unresolved"), overrides));
    }

    private Map<String, Object> decompileOne(DecompInterface ifc, Function f, Set<Address> sites)
            throws Exception {
        long rawOps = 0;
        for (Address a : sites == null ? new HashSet<Address>() : sites) {
            Instruction insn = getInstructionAt(a);
            if (insn != null) {
                rawOps += insn.getPcode().length;
            }
        }
        long t0 = System.currentTimeMillis();
        DecompileResults res = ifc.decompileFunction(f, TIMEOUT, monitor);
        long ms = System.currentTimeMillis() - t0;
        String name = f.getName().replaceAll("[^A-Za-z0-9_]", "_");
        String c = res.decompileCompleted() && res.getDecompiledFunction() != null
                ? res.getDecompiledFunction().getC()
                : "";
        int nops = 0;
        Set<String> uniques = new TreeSet<>();
        try (BufferedWriter w = Files.newBufferedWriter(out.resolve(name + ".pcode"))) {
            HighFunction hf = res.getHighFunction();
            if (hf != null) {
                Iterator<PcodeOpAST> it = hf.getPcodeOps();
                while (it.hasNext()) {
                    PcodeOpAST op = it.next();
                    w.write(op.getSeqnum().getTarget() + ": " + op.toString());
                    w.newLine();
                    nops++;
                    collectUniques(op, uniques);
                }
            }
        }
        Files.writeString(out.resolve(name + ".c"), c);
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("name", f.getName());
        row.put("entry", f.getEntryPoint().toString());
        row.put("sites", sites == null ? 0 : sites.size());
        row.put("raw_pcode_ops", rawOps);
        row.put("pcode_ops", nops);
        row.put("uniques", uniques.size());
        row.put("c_lines", c.isEmpty() ? 0 : c.split("\n", -1).length);
        row.put("gotos", count(c, "goto "));
        row.put("unresolved", count(c, "switchD") + count(c, "halt_baddata")
                + count(c, "UNRECOVERED_JUMPTABLE"));
        row.put("unreachable", count(c, "Removing unreachable block"));
        row.put("warnings", count(c, "WARNING"));
        row.put("ms", ms);
        row.put("error", res.getErrorMessage() == null ? "" : res.getErrorMessage().trim());
        return row;
    }

    private static void collectUniques(PcodeOp op, Set<String> uniques) {
        Varnode[] all = new Varnode[op.getNumInputs() + 1];
        System.arraycopy(op.getInputs(), 0, all, 0, op.getNumInputs());
        all[op.getNumInputs()] = op.getOutput();
        for (Varnode v : all) {
            if (v != null && v.isUnique()) {
                uniques.add(v.getAddress().toString() + ":" + v.getSize());
            }
        }
    }

    private static int count(String hay, String needle) {
        int n = 0;
        int i = hay.indexOf(needle);
        while (i >= 0) {
            n++;
            i = hay.indexOf(needle, i + needle.length());
        }
        return n;
    }
}
