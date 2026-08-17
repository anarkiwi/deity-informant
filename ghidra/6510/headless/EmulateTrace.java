// Semantic oracle: run the post-init image under Ghidra's own P-Code emulator and
// compare with tuneprog's trace.
//
// Usage (see run.sh): analyzeHeadless ... -noanalysis -postScript
//   EmulateTrace.java <factsDir> <outDir>
// Emulates the play entry as a subroutine call for the number of calls the facts
// carry, recording every executed pc and the SID register state after each call,
// and reports the first disagreement with the trace.
//@category deity-informant
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import ghidra.app.emulator.EmulatorHelper;
import ghidra.app.script.GhidraScript;
import ghidra.pcode.emulate.BreakCallBack;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class EmulateTrace extends GhidraScript {

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
    // the same dummy return address tuneprog's tracer pushes, so a routine that
    // reads its own return address off the stack sees the machine we traced
    private static final long SENTINEL = 0x0002L;
    private static final int MAX_STEPS = 400000;
    private int callother;
    private final List<String> firstCall = new ArrayList<>();

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        Path factsDir = Paths.get(args.length > 0 ? args[0] : ".");
        Path out = Paths.get(args.length > 1 ? args[1] : factsDir.resolve("ghidra-out").toString());
        Files.createDirectories(out);
        JsonObject facts;
        try (java.io.Reader r = Files.newBufferedReader(factsDir.resolve("ghidra_facts.json"))) {
            facts = JsonParser.parseReader(r).getAsJsonObject();
        }
        Map<String, Object> doc = emulate(facts);
        Files.writeString(out.resolve("emulate.json"), GSON.toJson(doc));
        println("EMULATE-ORACLE-OK calls=" + doc.get("calls") + " steps=" + doc.get("steps")
                + " unknown_pcs=" + doc.get("unknown_pcs") + " sid_mismatch=" + doc.get("mismatch")
                + " agree=" + doc.get("agree"));
    }

    private Map<String, Object> emulate(JsonObject facts) throws Exception {
        JsonElement emu = facts.get("emulate");
        Map<String, Object> doc = new LinkedHashMap<>();
        if (emu == null || emu.isJsonNull()) {
            doc.put("agree", false);
            doc.put("error", "no emulate facts");
            return doc;
        }
        JsonObject e = emu.getAsJsonObject();
        Set<Long> known = new HashSet<>();
        for (JsonElement a : facts.getAsJsonArray("insn_addrs")) {
            known.add(a.getAsLong());
        }
        Map<Long, String> inputs = new LinkedHashMap<>();
        for (JsonElement i : facts.getAsJsonArray("inputs")) {
            JsonObject o = i.getAsJsonObject();
            inputs.put(o.get("pc").getAsLong(), o.get("kind").getAsString());
        }
        long play = facts.getAsJsonObject("meta").get("play").getAsLong();
        int calls = Math.min(e.get("calls").getAsInt(), envInt("EMU_CALLS", 8));
        int base = e.get("sid_base").getAsInt();

        EmulatorHelper h = new EmulatorHelper(currentProgram);
        h.registerDefaultCallOtherCallback(new BreakCallBack() {
            @Override
            public boolean pcodeCallback(ghidra.pcode.pcoderaw.PcodeOpRaw op) {
                callother++;
                return true;
            }
        });
        List<Map<String, Object>> bad = new ArrayList<>();
        List<String> unknown = new ArrayList<>();
        long steps = 0;
        h.writeRegister("SH", 1);
        h.writeRegister("S", 0xFF);
        // the CPU state init left; without it a play routine that reads a flag or
        // the stack pointer at entry starts from a different machine than we traced
        JsonElement regs = e.get("regs");
        if (regs != null && regs.isJsonObject()) {
            for (Map.Entry<String, JsonElement> r : regs.getAsJsonObject().entrySet()) {
                h.writeRegister(r.getKey(), r.getValue().getAsLong());
            }
        }
        try {
            for (int c = 0; c < calls; c++) {
                pin(h, e.getAsJsonArray("pins"), c);
                steps += oneCall(h, play, known, inputs, unknown, c);
                for (JsonElement wr : e.getAsJsonArray("writes").get(c).getAsJsonArray()) {
                    int off = wr.getAsJsonArray().get(0).getAsInt();
                    int want = wr.getAsJsonArray().get(1).getAsInt();
                    int got = h.readMemoryByte(toAddr(base + off)) & 0xFF;
                    if (got != want && bad.size() < 8) {
                        Map<String, Object> row = new LinkedHashMap<>();
                        row.put("call", c);
                        row.put("addr", String.format("%04X", base + off));
                        row.put("got", got);
                        row.put("want", want);
                        bad.add(row);
                    }
                }
            }
        } catch (Exception ex) {
            doc.put("error", ex.toString());
        }
        h.dispose();
        doc.put("calls", calls);
        doc.put("steps", steps);
        doc.put("unknown_pcs", unknown.size());
        doc.put("first_unknown_pcs", unknown.subList(0, Math.min(8, unknown.size())));
        doc.put("mismatch", bad.size());
        doc.put("sid_mismatches", bad);
        doc.put("callother", callother);
        doc.put("first_call_pcs", firstCall);
        doc.put("unpinned_inputs", e.get("unpinned_inputs"));
        doc.put("agree", unknown.isEmpty() && bad.isEmpty() && !doc.containsKey("error"));
        return doc;
    }

    /** Pin the entry registers the trace saw this call read before writing. */
    private static void pin(EmulatorHelper h, com.google.gson.JsonArray pins, int call) {
        String[] regs = {"A", "X", "Y"};
        for (JsonElement p : pins) {
            com.google.gson.JsonArray a = p.getAsJsonArray();
            if (a.get(0).getAsInt() == call && a.get(1).getAsInt() < regs.length) {
                h.writeRegister(regs[a.get(1).getAsInt()], a.get(2).getAsLong());
            }
        }
    }

    /** One play call: fake a JSR, single-step to the sentinel, record the pcs. */
    private long oneCall(EmulatorHelper h, long play, Set<Long> known, Map<Long, String> inputs,
            List<String> unknown, int call) throws Exception {
        long sp = h.readRegister("SP").longValue();
        h.writeMemoryValue(toAddr(sp - 1), 2, SENTINEL - 1);
        h.writeRegister("SP", sp - 2);
        h.writeRegister("PC", play);
        h.setBreakpoint(toAddr(SENTINEL));
        long steps = 0;
        while (steps < MAX_STEPS) {
            long pc = h.getExecutionAddress().getOffset();
            if (pc == SENTINEL) {
                break;
            }
            if (call == 0 && firstCall.size() < 64) {
                firstCall.add(String.format("%04X", pc));
            }
            if (!known.contains(pc) && unknown.size() < 64) {
                unknown.add(String.format("call=%d pc=%04X%s", call, pc,
                        inputs.containsKey(pc) ? " (pinned input " + inputs.get(pc) + ")" : ""));
            }
            if (!h.step(monitor)) {
                throw new Exception("emulator stopped at " + h.getExecutionAddress() + ": "
                        + h.getLastError());
            }
            steps++;
        }
        h.clearBreakpoint(toAddr(SENTINEL));
        h.writeRegister("SP", sp);
        return steps;
    }

    private static int envInt(String name, int dflt) {
        String v = System.getenv(name);
        try {
            return v == null ? dflt : Integer.parseInt(v.trim());
        } catch (NumberFormatException ex) {
            return dflt;
        }
    }

    private static byte[] hex(String s) {
        byte[] b = new byte[s.length() / 2];
        for (int i = 0; i < b.length; i++) {
            b[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        }
        return b;
    }
}
