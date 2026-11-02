// Ghidra headless post-script: apply a layout plan (tools/ghidra_layout.py) to the
// current program and dump what the program then holds.
//
// Args: PLAN.json DUMP.json. Every gap of the address space gets an uninitialized
// block; struct types, data, labels, references and plate comments are applied in
// plan order and any item Ghidra refuses is recorded under "skipped" with the reason.
//@category deity-informant
import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.data.Array;
import ghidra.program.model.data.ArrayDataType;
import ghidra.program.model.data.ByteDataType;
import ghidra.program.model.data.DataType;
import ghidra.program.model.data.DataTypeComponent;
import ghidra.program.model.data.DataTypeConflictHandler;
import ghidra.program.model.data.DataTypeManager;
import ghidra.program.model.data.Pointer16DataType;
import ghidra.program.model.data.Structure;
import ghidra.program.model.data.StructureDataType;
import ghidra.program.model.data.WordDataType;
import ghidra.program.model.listing.CommentType;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.RefType;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolType;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.TreeSet;

public class ApplyLayout extends GhidraScript {
    private final Map<String, DataType> types = new HashMap<>();
    private final JsonArray skipped = new JsonArray();
    private DataTypeManager dtm;

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        JsonObject plan = JsonParser.parseString(Files.readString(Path.of(args[0])))
                .getAsJsonObject();
        dtm = currentProgram.getDataTypeManager();
        types.put("byte", dtm.resolve(ByteDataType.dataType, null));
        types.put("word", dtm.resolve(WordDataType.dataType, null));
        types.put("pointer", dtm.resolve(Pointer16DataType.dataType, null));
        fillGaps();

        for (JsonElement e : plan.getAsJsonArray("types")) {
            JsonObject t = e.getAsJsonObject();
            String name = t.get("name").getAsString();
            try {
                StructureDataType s = new StructureDataType(name, t.get("size").getAsInt(), dtm);
                for (JsonElement f : t.getAsJsonArray("fields")) {
                    JsonObject o = f.getAsJsonObject();
                    try {
                        DataType dt = type(o);
                        int off = o.get("offset").getAsInt();
                        if (off + dt.getLength() > s.getLength()) {
                            throw new IllegalArgumentException("field exceeds the struct");
                        }
                        s.replaceAtOffset(off, dt, dt.getLength(), o.get("name").getAsString(),
                                null);
                    } catch (Exception x) {
                        JsonArray key = new JsonArray();
                        key.add(name);
                        key.add(o.get("offset"));
                        skip("field", key, x);
                    }
                }
                types.put(name, dtm.addDataType(s, DataTypeConflictHandler.REPLACE_HANDLER));
            } catch (Exception x) {
                skip("type", t.get("name"), x);
            }
        }
        for (JsonElement e : plan.getAsJsonArray("data")) {
            JsonObject d = e.getAsJsonObject();
            try {
                createData(toAddr(d.get("addr").getAsLong()), type(d));
            } catch (Exception x) {
                skip("data", d.get("addr"), x);
            }
        }
        for (JsonElement e : plan.getAsJsonArray("labels")) {
            JsonObject l = e.getAsJsonObject();
            try {
                createLabel(toAddr(l.get("addr").getAsLong()), l.get("name").getAsString(),
                        false, SourceType.USER_DEFINED);
            } catch (Exception x) {
                JsonArray key = new JsonArray();
                key.add(l.get("addr"));
                key.add(l.get("name"));
                skip("label", key, x);
            }
        }
        ReferenceManager refs = currentProgram.getReferenceManager();
        TreeSet<Long> sources = new TreeSet<>();
        for (JsonElement e : plan.getAsJsonArray("refs")) {
            JsonObject r = e.getAsJsonObject();
            try {
                refs.addMemoryReference(toAddr(r.get("from").getAsLong()),
                        toAddr(r.get("to").getAsLong()), RefType.DATA, SourceType.USER_DEFINED,
                        0);
                sources.add(r.get("from").getAsLong());
            } catch (Exception x) {
                JsonArray key = new JsonArray();
                key.add(r.get("from"));
                key.add(r.get("to"));
                skip("ref", key, x);
            }
        }
        for (JsonElement e : plan.getAsJsonArray("comments")) {
            JsonObject c = e.getAsJsonObject();
            try {
                currentProgram.getListing().setComment(toAddr(c.get("addr").getAsLong()),
                        CommentType.PLATE, c.get("text").getAsString());
            } catch (Exception x) {
                skip("comment", c.get("addr"), x);
            }
        }

        JsonObject out = new JsonObject();
        out.add("data", dumpData());
        out.add("types", dumpTypes(plan));
        out.add("labels", dumpLabels());
        out.add("refs", dumpRefs(refs, sources));
        out.add("comments", dumpComments(plan));
        out.add("skipped", skipped);
        Files.writeString(Path.of(args[1]), new Gson().toJson(out));
        println("APPLY-LAYOUT-OK data=" + out.getAsJsonArray("data").size() + " skipped="
                + skipped.size());
    }

    private DataType type(JsonObject o) {
        DataType e = types.get(o.get("type").getAsString());
        if (e == null) {
            throw new IllegalArgumentException("unknown type " + o.get("type").getAsString());
        }
        int n = o.get("count").getAsInt();
        return n > 1 ? new ArrayDataType(e, n, e.getLength(), dtm) : e;
    }

    private void skip(String what, JsonElement key, Exception x) {
        JsonObject s = new JsonObject();
        s.addProperty("what", what);
        s.add("key", key);
        s.addProperty("reason", x.getClass().getSimpleName() + ": " + x.getMessage());
        skipped.add(s);
    }

    private void fillGaps() throws Exception {
        Memory mem = currentProgram.getMemory();
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        AddressSet gaps = new AddressSet(sp.getMinAddress(), sp.getMaxAddress()).subtract(mem);
        for (AddressRange r : gaps) {
            mem.createUninitializedBlock(String.format("ram_%04X", r.getMinAddress().getOffset()),
                    r.getMinAddress(), r.getLength(), false);
        }
    }

    private static void describe(JsonObject o, DataType dt) {
        if (dt instanceof Array a) {
            o.addProperty("type", a.getDataType().getName());
            o.addProperty("count", a.getNumElements());
        } else {
            o.addProperty("type", dt.getName());
            o.addProperty("count", 1);
        }
    }

    private JsonArray dumpData() {
        JsonArray out = new JsonArray();
        DataIterator it = currentProgram.getListing().getDefinedData(true);
        while (it.hasNext()) {
            Data d = it.next();
            JsonObject o = new JsonObject();
            o.addProperty("addr", d.getAddress().getOffset());
            describe(o, d.getDataType());
            o.addProperty("length", d.getLength());
            out.add(o);
        }
        return out;
    }

    private JsonArray dumpTypes(JsonObject plan) {
        JsonArray out = new JsonArray();
        for (JsonElement e : plan.getAsJsonArray("types")) {
            DataType t = types.get(e.getAsJsonObject().get("name").getAsString());
            if (!(t instanceof Structure s)) {
                continue;
            }
            JsonObject o = new JsonObject();
            o.addProperty("name", s.getName());
            o.addProperty("size", s.getLength());
            JsonArray fields = new JsonArray();
            for (DataTypeComponent c : s.getDefinedComponents()) {
                JsonObject f = new JsonObject();
                f.addProperty("offset", c.getOffset());
                f.addProperty("name", c.getFieldName());
                describe(f, c.getDataType());
                f.addProperty("length", c.getLength());
                fields.add(f);
            }
            o.add("fields", fields);
            out.add(o);
        }
        return out;
    }

    private JsonArray dumpLabels() {
        JsonArray out = new JsonArray();
        for (Symbol s : currentProgram.getSymbolTable().getAllSymbols(true)) {
            if (s.getSource() == SourceType.USER_DEFINED && s.getSymbolType() == SymbolType.LABEL) {
                JsonObject o = new JsonObject();
                o.addProperty("addr", s.getAddress().getOffset());
                o.addProperty("name", s.getName());
                out.add(o);
            }
        }
        return out;
    }

    private JsonArray dumpRefs(ReferenceManager refs, TreeSet<Long> sources) {
        JsonArray out = new JsonArray();
        for (long from : sources) {
            for (Reference r : refs.getReferencesFrom(toAddr(from))) {
                if (r.isMemoryReference()) {
                    JsonObject o = new JsonObject();
                    o.addProperty("from", from);
                    o.addProperty("to", r.getToAddress().getOffset());
                    o.addProperty("source", r.getSource().toString());
                    out.add(o);
                }
            }
        }
        return out;
    }

    private JsonArray dumpComments(JsonObject plan) {
        JsonArray out = new JsonArray();
        for (JsonElement e : plan.getAsJsonArray("comments")) {
            long addr = e.getAsJsonObject().get("addr").getAsLong();
            String text =
                    currentProgram.getListing().getComment(CommentType.PLATE, toAddr(addr));
            if (text != null) {
                JsonObject o = new JsonObject();
                o.addProperty("addr", addr);
                o.addProperty("text", text);
                out.add(o);
            }
        }
        return out;
    }
}
