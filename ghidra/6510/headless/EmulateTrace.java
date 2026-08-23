// Semantic oracle: run the post-init image under Ghidra's own P-Code emulator and
// compare with tuneprog's trace.
//
// Usage (see run.sh): analyzeHeadless ... -noanalysis -postScript
//   EmulateTrace.java <factsDir> <outDir>
// Emulates the schedule's first entry on the frame the machine pushes entering it,
// for the number of calls the facts carry, replaying the input sequence each call
// consumed, and compares the SID register changes it makes, in order, with the
// ones the trace recorded for that same entry.
//@category deity-informant
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import ghidra.app.emulator.EmulatorHelper;
import ghidra.app.emulator.MemoryAccessFilter;
import ghidra.app.script.GhidraScript;
import ghidra.pcode.emulate.BreakCallBack;
import ghidra.program.model.address.AddressSpace;

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
    // the return address each frame carries: RTS resumes at it + 1 -- the $0002 the
    // tracer's own dummy frame uses -- and RTI at it exactly
    private static final long SUB_RET = 0x0001L;
    private static final long IRQ_RET = 0x0000L;
    private static final String[] SAVED = {"A", "X", "Y"};  // the $FF48 prologue's pushes
    // a bound, not a verdict: a call that has not balanced its frame by here is an
    // error of this oracle, reported as one
    private static final int MAX_STEPS = 400000;
    private int callother;
    private int replayed;
    private final List<String> firstCall = new ArrayList<>();
    private int sidBase;
    private int[] sid;                    // the chip's registers: what a store reaches
    private final int[] port = {0, 0};    // the 6510 port, $0000/$0001
    private boolean io = true;
    private boolean ioRange;              // whether the compared range is behind the port
    private long pc;                      // the instruction stepping now, for attribution
    private List<long[]> got = new ArrayList<>();
    private boolean sub = true;           // the entry's frame: JSR per tick, or an interrupt
    private boolean kernal;

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
                + " agree=" + doc.get("agree") + " error=" + doc.get("error"));
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
        frame(facts);
        JsonArray writes = e.getAsJsonArray("writes");
        // the trace ran what it ran: comparing past it reports an end that is only
        // the horizon of the export
        int calls = Math.min(Math.min(e.get("calls").getAsInt(), writes.size()),
                envInt("EMU_CALLS", 8));
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
        byte[] chip = h.readMemory(toAddr(sidBase), e.get("sid_len").getAsInt());
        sid = new int[chip.length];
        for (int i = 0; i < chip.length; i++) {
            sid[i] = chip[i] & 0xFF;
        }
        byte[] p01 = h.readMemory(toAddr(0), 2);
        port[0] = p01[0] & 0xFF;
        port[1] = p01[1] & 0xFF;
        bank();
        ioRange = sidBase >= 0xD000 && sidBase <= 0xDFFF;  // the window the port maps
        h.getEmulator().addMemoryAccessFilter(new Bus());
        int leftover = 0;
        try {
            for (int c = 0; c < calls; c++) {
                pin(h, e.getAsJsonArray("pins"), c);
                Map<Long, Map<Long, Deque<Integer>>> reads = perPc(e.get("reads"), c);
                got = new ArrayList<>();
                steps += oneCall(h, play, known, inputs, unknown, c, reads);
                leftover += pending(reads);
                Map<String, Object> row = firstDiff(c, writes.get(c).getAsJsonArray(), got);
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
        doc.put("entry", sub ? "sub" : (kernal ? "irq/kernal" : "irq"));
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

    /** The frame the machine pushes entering the tick, from the schedule's first entry. */
    private void frame(JsonObject facts) {
        JsonElement sched = facts.getAsJsonObject("meta").get("schedule");
        if (sched == null || sched.isJsonNull() || sched.getAsJsonArray().isEmpty()) {
            return;
        }
        JsonObject entry = sched.getAsJsonArray().get(0).getAsJsonObject();
        sub = "sub".equals(entry.get("kind").getAsString());
        JsonElement k = entry.get("kernal");
        kernal = k != null && !k.isJsonNull() && k.getAsBoolean();
    }

    /** The first position where our change sequence and the trace's differ. */
    private Map<String, Object> firstDiff(int call, JsonArray want, List<long[]> seq) {
        int n = Math.max(want.size(), seq.size());
        for (int i = 0; i < n; i++) {
            long[] g = i < seq.size() ? seq.get(i) : null;
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
            row.put("writes", seq.size() + "/" + want.size());
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
        for (JsonElement p : pins) {
            JsonArray a = p.getAsJsonArray();
            if (a.get(0).getAsInt() == call && a.get(1).getAsInt() < SAVED.length) {
                h.writeRegister(SAVED[a.get(1).getAsInt()], a.get(2).getAsLong());
            }
        }
    }

    private void push(EmulatorHelper h, int size, long value) {
        long sp = h.readRegister("SP").longValue();
        h.writeMemoryValue(toAddr(sp - size + 1), size, value);
        h.writeRegister("SP", sp - size);
    }

    /** The status byte the 6510 pushes taking an interrupt: B clear, bit 5 set. */
    private long status(EmulatorHelper h) {
        long p = 0x20;
        String[] flags = {"C", "Z", "I", "D", "V", "N"};
        int[] bits = {0, 1, 2, 3, 6, 7};
        for (int i = 0; i < flags.length; i++) {
            p |= (h.readRegister(flags[i]).longValue() & 1) << bits[i];
        }
        return p;
    }

    /**
     * One play call on the machine's own frame, stepped until it balances it.
     *
     * The stop condition is the tracer's (``trace._one_call``): the stack pointer
     * back where the call started, which RTS and RTI reach alike -- an RTI-framed
     * tick never returns to a fake JSR's return address.
     */
    private long oneCall(EmulatorHelper h, long play, Set<Long> known, Map<Long, String> inputs,
            List<String> unknown, int call, Map<Long, Map<Long, Deque<Integer>>> reads)
            throws Exception {
        long sp0 = h.readRegister("SP").longValue();
        push(h, 2, sub ? SUB_RET : IRQ_RET);
        if (!sub) {
            push(h, 1, status(h));
            if (kernal) {
                for (String r : SAVED) {
                    push(h, 1, h.readRegister(r).longValue());
                }
            }
            h.writeRegister("I", 1);  // the interrupt disable the dispatch sets
        }
        h.writeRegister("PC", play);
        long steps = 0;
        boolean returned = false;
        while (steps < MAX_STEPS) {
            pc = h.getExecutionAddress().getOffset();
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
            if (h.readRegister("SP").longValue() >= sp0) {
                returned = true;
                break;
            }
        }
        if (!returned) {
            throw new Exception("call " + call + " did not balance its frame within " + MAX_STEPS
                    + " steps");
        }
        h.writeRegister("SP", sp0);
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

    /** Every store the program makes, gated as ``tracevm`` gates its own write log. */
    private final class Bus extends MemoryAccessFilter {

        Bus() {
            setFilterOnExecutionOnly(true);  // the frame and the replayed inputs are ours
        }

        @Override
        protected void processRead(AddressSpace space, long off, int size, byte[] values) {
        }

        @Override
        protected void processWrite(AddressSpace space, long off, int size, byte[] values) {
            if (!space.isMemorySpace()) {  // registers and uniques are not the bus
                return;
            }
            for (int k = 0; k < size; k++) {
                store((off + k) & 0xFFFF, values[k] & 0xFF);
            }
        }
    }

    /**
     * One store: the 6510 port decides whether ``$D400-$D418`` is the chip or the RAM
     * under it, so a write made with I/O banked out changes no register -- exactly the
     * gate ``tracevm._io_write`` applies, and the reason a diff of those bytes is not
     * the same thing as the register file.
     */
    private void store(long a, int v) {
        if (a <= 1) {
            port[(int) a] = v;
            bank();
            return;
        }
        int off = (int) (a - sidBase);
        if ((io || !ioRange) && off >= 0 && off < sid.length && sid[off] != v) {
            sid[off] = v;
            got.add(new long[] {off, v, pc});
        }
    }

    /** ``machine.port_bank``: I/O answers only with a bank line and CHAREN set. */
    private void bank() {
        int p = (port[1] | ~port[0]) & 7;
        io = (p & 3) != 0 && (p & 4) != 0;
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
