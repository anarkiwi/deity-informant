// Semantic oracle: run the post-init image under Ghidra's own P-Code emulator and
// compare with tuneprog's trace.
//
// Usage (see run.sh): analyzeHeadless ... -noanalysis -postScript
//   EmulateTrace.java <factsDir> <outDir>
// Emulates the play entry as a subroutine call for the number of calls the facts
// carry, replaying the input sequence each call consumed, and compares the SID
// register changes it makes, in order, with the ones the trace recorded.
//@category deity-informant
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import ghidra.app.emulator.EmulatorHelper;
import ghidra.app.script.GhidraScript;
import ghidra.pcode.emulate.BreakCallBack;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
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
    private int replayed;
    private final List<String> firstCall = new ArrayList<>();
    private int sidBase;
    private byte[] sid;

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
        long play = tickEntry(facts);
        int calls = Math.min(e.get("calls").getAsInt(), envInt("EMU_CALLS", 8));
        sidBase = e.get("sid_base").getAsInt();

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
        int diverged = 0;
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
        sid = h.readMemory(toAddr(sidBase), e.get("sid_len").getAsInt());
        int leftover = 0;
        try {
            for (int c = 0; c < calls; c++) {
                pin(h, e.getAsJsonArray("pins"), c);
                Map<Long, Map<Long, Deque<Integer>>> reads = perPc(e.get("reads"), c);
                List<long[]> got = new ArrayList<>();
                steps += oneCall(h, play, known, inputs, unknown, c, reads, got);
                leftover += pending(reads);
                Map<String, Object> row = firstDiff(c, e.getAsJsonArray("writes").get(c)
                        .getAsJsonArray(), got);
                if (row != null) {
                    diverged++;
                    if (bad.size() < 8) {
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
        doc.put("mismatch", diverged);
        doc.put("sid_mismatches", bad);
        doc.put("callother", callother);
        doc.put("inputs_replayed", replayed);
        doc.put("inputs_unconsumed", leftover);
        doc.put("first_call_pcs", firstCall);
        doc.put("unpinned_inputs", e.get("unpinned_inputs"));
        doc.put("agree", unknown.isEmpty() && diverged == 0 && !doc.containsKey("error"));
        return doc;
    }

    /**
     * The tick entry the trace settled on, which the header's play field need not be:
     * an installed-handler tune carries play = $0000 and reaches its tick through CINV.
     */
    private static long tickEntry(JsonObject facts) {
        long play = facts.getAsJsonObject("meta").get("play").getAsLong();
        long first = -1;
        for (JsonElement e : facts.getAsJsonArray("entries")) {
            JsonObject o = e.getAsJsonObject();
            if (!"tick".equals(o.get("kind").getAsString())) {
                continue;
            }
            long a = o.get("addr").getAsLong();
            if (a == play) {
                return play;
            }
            first = first < 0 ? a : first;
        }
        return first < 0 ? play : first;
    }

    /** The first position where our change sequence and the trace's differ. */
    private Map<String, Object> firstDiff(int call, JsonArray want, List<long[]> got) {
        int n = Math.max(want.size(), got.size());
        for (int i = 0; i < n; i++) {
            long[] g = i < got.size() ? got.get(i) : null;
            JsonArray w = i < want.size() ? want.get(i).getAsJsonArray() : null;
            if (g != null && w != null && g[0] == w.get(0).getAsInt()
                    && g[1] == w.get(1).getAsInt()) {
                continue;
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("call", call);
            row.put("index", i);
            row.put("want", w == null ? "end"
                    : String.format("%04X=%02X", sidBase + w.get(0).getAsInt(),
                            w.get(1).getAsInt()));
            row.put("got", g == null ? "end" : String.format("%04X=%02X", sidBase + g[0], g[1]));
            row.put("pc", g == null ? "-" : String.format("%04X", g[2]));
            row.put("writes", got.size() + "/" + want.size());
            return row;
        }
        return null;
    }

    /** ``{pc: {address: values}}`` for one call's recorded volatile reads. */
    private static Map<Long, Map<Long, Deque<Integer>>> perPc(JsonElement reads, int call) {
        Map<Long, Map<Long, Deque<Integer>>> out = new LinkedHashMap<>();
        if (reads == null || reads.isJsonNull() || call >= reads.getAsJsonArray().size()) {
            return out;
        }
        for (JsonElement r : reads.getAsJsonArray().get(call).getAsJsonArray()) {
            JsonArray a = r.getAsJsonArray();
            out.computeIfAbsent(a.get(0).getAsLong(), k -> new LinkedHashMap<>())
                    .computeIfAbsent(a.get(1).getAsLong(), k -> new ArrayDeque<>())
                    .add(a.get(2).getAsInt());
        }
        return out;
    }

    private static int pending(Map<Long, Map<Long, Deque<Integer>>> reads) {
        int n = 0;
        for (Map<Long, Deque<Integer>> at : reads.values()) {
            for (Deque<Integer> q : at.values()) {
                n += q.size();
            }
        }
        return n;
    }

    /** Pin the entry registers the trace saw this call read before writing. */
    private static void pin(EmulatorHelper h, JsonArray pins, int call) {
        String[] regs = {"A", "X", "Y"};
        for (JsonElement p : pins) {
            JsonArray a = p.getAsJsonArray();
            if (a.get(0).getAsInt() == call && a.get(1).getAsInt() < regs.length) {
                h.writeRegister(regs[a.get(1).getAsInt()], a.get(2).getAsLong());
            }
        }
    }

    /** One play call: fake a JSR, single-step to the sentinel, record the pcs. */
    private long oneCall(EmulatorHelper h, long play, Set<Long> known, Map<Long, String> inputs,
            List<String> unknown, int call, Map<Long, Map<Long, Deque<Integer>>> reads,
            List<long[]> got) throws Exception {
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
            inject(h, reads.get(pc));
            if (!h.step(monitor)) {
                throw new Exception("emulator stopped at " + h.getExecutionAddress() + ": "
                        + h.getLastError());
            }
            steps++;
            sidWrites(h, pc, got);
        }
        h.clearBreakpoint(toAddr(SENTINEL));
        h.writeRegister("SP", sp);
        if (steps >= MAX_STEPS) {
            // an RTI-framed tick never reaches the sentinel this pushes; stop on the
            // first such call rather than spending MAX_STEPS on each of the rest
            throw new Exception("call " + call + " did not return within " + MAX_STEPS + " steps");
        }
        return steps;
    }

    /** Hand this pc the value the trace's executable read there, this call. */
    private void inject(EmulatorHelper h, Map<Long, Deque<Integer>> at) throws Exception {
        if (at == null) {
            return;
        }
        for (Map.Entry<Long, Deque<Integer>> en : at.entrySet()) {
            Integer v = en.getValue().poll();
            if (v != null) {
                h.writeMemoryValue(toAddr(en.getKey()), 1, v);
                replayed++;
            }
        }
    }

    /** Every SID register the last step changed, in address order, with its pc. */
    private void sidWrites(EmulatorHelper h, long pc, List<long[]> got) throws Exception {
        byte[] now = h.readMemory(toAddr(sidBase), sid.length);
        for (int i = 0; i < sid.length; i++) {
            if (now[i] != sid[i]) {
                got.add(new long[] {i, now[i] & 0xFF, pc});
            }
        }
        sid = now;
    }

    private static int envInt(String name, int dflt) {
        String v = System.getenv(name);
        try {
            return v == null ? dflt : Integer.parseInt(v.trim());
        } catch (NumberFormatException ex) {
            return dflt;
        }
    }
}
