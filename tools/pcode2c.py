"""Print a whole-program lift (``wholeprog.Program.functions``) as C.

``python tools/pcode2c.py FUNCS.pkl --entry PC [--layout MAP.json] [--compact] [-o OUT.c]``:
registers are globals, memory is ``M[]`` (volatile accesses through ``io_read``/``io_write``) and
layout regions (``docs/layout.md``) name accesses; ``--compact`` optimizes and structures it.
"""

import argparse
import json
import pickle
import re
import sys
from collections import Counter
from pathlib import Path

REG = {0: "A", 1: "X", 2: "Y", 3: "SP", 8: "C", 9: "Z", 10: "I", 11: "D", 13: "V", 14: "N"}
BIN = {
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_AND": "&",
    "INT_OR": "|",
    "INT_XOR": "^",
    "INT_LEFT": "<<",
    "INT_RIGHT": ">>",
    "INT_MULT": "*",
}
CMP = {
    "INT_EQUAL": "==",
    "INT_NOTEQUAL": "!=",
    "INT_LESS": "<",
    "INT_LESSEQUAL": "<=",
    "BOOL_AND": "&&",
    "BOOL_OR": "||",
}


def ctype(size):
    """The C type of a varnode of ``size`` bytes."""
    return "uint8_t" if size == 1 else "uint16_t"


def slot_name(v, pc):
    """The name of register varnode ``v`` in the function at ``pc``: stack slots (registers from
    18) are bytes below that function's own entry stack pointer."""
    return REG[v[1]] if v[1] in REG else f"s{v[1]}_{pc:04X}"


class Names:
    """Layout regions as accessor macros over ``M``."""

    def __init__(self, regions):
        self.regions = [r for r in regions if not r.get("io") and "extent" in r]
        self.pointers = {r["base"] for r in regions if r["kind"] == "pointer" and not r.get("io")}
        self.used = {}

    def word(self, addr):
        """A 16-bit read at constant ``addr``: a named pointer or ``RD16(addr)``."""
        if addr in self.pointers:
            self.used[f"pointer_{addr:04X}"] = {"kind": "pointer", "base": addr, "fields": []}
            return f"pointer_{addr:04X}"
        return f"RD16(0x{addr:04X})"

    def at(self, base, index):
        """``M[base + index]`` (``index`` None: a constant address) named by the innermost
        region holding ``base``, or None; a record's accessor takes the byte offset of its
        element plus the field's offset, from the region's base."""
        hits = [r for r in self.regions if r["extent"][0] <= base <= r["extent"][1]]
        if not hits:
            return None
        r = min(hits, key=lambda r: r["extent"][1] - r["extent"][0])
        key = f"{r['kind']}_{r['base']:04X}"
        self.used[key] = r
        i, f = divmod(base - r["base"], r.get("stride") or 1)
        stride = r.get("stride") or 1
        if len(r["fields"]) > 1:
            sub = " + ".join(s for s in (index, str(i * stride) if i else "") if s) or "0"
            return f"{key}[{sub} + {f}]" if f else f"{key}[{sub}]"
        sub = " + ".join(
            s for s in (index, str(base - r["base"]) if base != r["base"] else "") if s
        )
        return f"{key}[{sub or 0}]"

    def macros(self):
        """``#define`` lines for the regions used."""
        out = []
        for key, r in sorted(self.used.items()):
            if r["kind"] == "pointer":
                out.append(f"#define {key} RD16(0x{r['base']:04X})")
            else:
                out.append(f"uint8_t *const {key} = M + 0x{r['base']:04X};")
        return out


class Fn:
    """One lifted function printed as C."""

    def __init__(self, fn, names, label, labels, trace=False):
        self.fn, self.names, self.label, self.labels = fn, names, label, labels
        self.trace = trace
        self.uses, self.home, self.temps = Counter(), {}, set()
        for i, b in enumerate(fn["blocks"]):
            for op in b["ops"]:
                for v in op[2]:
                    if v and v[0] == "u":
                        self.uses[(v[1], v[2])] += 1
                        self.home.setdefault((v[1], v[2]), set()).add(i)
                if op[1] and op[1][0] == "u":
                    self.temps.add((op[1][1], op[1][2]))

    def var(self, v):
        """The C name of a register, a stack slot (private to its function) or a temporary."""
        return slot_name(v, self.fn["pc"]) if v[0] == "r" else f"u{v[1]}_{v[2]}"

    def vn(self, v, env):
        """A varnode as an expression, taking a folded temporary out of ``env``."""
        if v[0] == "c":
            return str(v[1]) if v[1] < 10 else f"0x{v[1]:X}"
        return env.pop(self.var(v), None) or self.var(v)

    def addr(self, v, env):
        """A memory access by address varnode ``v``: a named access or ``M[...]``."""
        if v[0] == "c":
            return self.names.at(v[1], None) or f"v_{v[1]:04X}[0]"
        expr = self.vn(v, env)
        m = re.fullmatch(r"(?:\(uint16_t\))?\(0x([0-9A-F]+) \+ (.+)\)", expr)
        named = m and self.names.at(int(m.group(1), 16), m.group(2))
        return named or f"M[(uint16_t){expr}]"

    def value(self, op, env):
        """The value of a non-memory, non-control op."""
        name, ins = op[0], op[2]
        a = [self.vn(v, env) for v in ins if v]
        if name == "COPY":
            return a[0]
        if name == "LOAD":
            return f"io_read({a[0]})"
        if name in BIN:
            return f"({ctype(op[1][2])})({a[0]} {BIN[name]} {a[1]})"
        if name in CMP:
            return f"({a[0]} {CMP[name]} {a[1]})"
        if name == "INT_ZEXT":
            return f"(uint16_t){a[0]}"
        if name == "INT_CARRY":
            return f"(((uint32_t){a[0]} + {a[1]}) >> {8 * ins[0][2]} & 1)"
        if name == "BOOL_NEGATE":
            return f"!{a[0]}"
        if name == "INT_NEGATE":
            return f"~{a[0]}"
        if name == "INT_2COMP":
            return f"-{a[0]}"
        if name == "SUBPIECE":
            return f"(uint8_t)({a[0]} >> {8 * ins[1][1]})"
        raise ValueError(name)

    @staticmethod
    def spill(env, names):
        """Assignments materialising every folded value that reads one of ``names``
        (all of them when ``names`` is None)."""
        out = []
        for key in list(env):
            if names is None or any(re.search(rf"\b{n}\b", env[key]) for n in names):
                out.append(f"{key} = {env.pop(key)};")
        return out

    def emit(self):
        """The C text of the function."""
        targets, bodies = set(), []
        for i, b in enumerate(self.fn["blocks"]):
            lines, env = [], {}
            for op in b["ops"]:
                lines += self.op(op, env, i, targets)
            bodies.append(lines + self.spill(env, None))
        text = "\n".join(sum(bodies, []))
        out = [f"void {self.label}(void) {{"]
        out += [
            f"  {ctype(s)} u{o}_{s};"
            for o, s in sorted(self.temps)
            if re.search(rf"\bu{o}_{s}\b", text)
        ]
        for i, lines in enumerate(bodies):
            if i in targets:
                out.append(f"b{i}:")
            if self.trace:
                out.append(f"  trace(0x{self.fn['blocks'][i]['pc']:04X});")
            out += [f"  {line}" for line in lines]
        return out + ["}"]

    def op(self, op, env, here, targets):
        """Statements for one op; a temporary used once later in this block folds into
        ``env`` instead."""
        name = op[0]
        if name == "CALLOTHER" and op[3] == "codechk":
            args = ", ".join(self.vn(v, env) for v in op[2] if v)
            return self.spill(env, [self.var(op[1])]) + [f"{self.var(op[1])} = codechk({args});"]
        if name in ("BRANCH", "CBRANCH", "BRANCHIND", "CALL", "RETURN", "CALLOTHER"):
            return self.control(op, env, here, targets)
        if name == "STORE":
            addr, val = op[2]
            value = self.vn(val, env)
            if op[3] == "volatile":
                return [f"io_write({self.vn(addr, env)}, {value});"]
            if val[2] == 2:
                return [f"WR16({self.vn(addr, env)}, {value});"]
            return [f"{self.addr(addr, env)} = {value};"]
        out = op[1]
        if out is None:
            return self.dummy(op, env)
        volatile = name == "LOAD" and op[3] == "volatile"
        if name == "LOAD" and not volatile and out[2] == 2:
            src = op[2][0]
            value = self.names.word(src[1]) if src[0] == "c" else f"RD16({self.vn(src, env)})"
        elif name == "LOAD" and not volatile:
            value = self.addr(op[2][0], env)
        else:
            value = self.value(op, env)
        target = self.var(out)
        pre = self.spill(env, [target])
        key = (out[1], out[2])
        if out[0] == "u" and name != "LOAD" and self.uses[key] == 1 and self.home[key] == {here}:
            env[target] = value
            return pre
        return pre + [f"{target} = {value};"]

    def dummy(self, op, env):
        """A throw-away bus read (a ``LOAD`` with no output, predicated by its second input):
        kept only when volatile."""
        addr = self.vn(op[2][0], env)
        pred = self.vn(op[2][1], env) if len(op[2]) > 1 and op[2][1] else None
        if op[3] != "volatile":
            return []
        read = f"io_read({addr});"
        return [f"if ({pred}) {read}" if pred else read]

    def control(self, op, env, here, targets):
        """Statements for a control op."""
        name = op[0]
        if name == "BRANCH":
            if op[3] == here + 1:
                return self.spill(env, None)
            targets.add(op[3])
            return self.spill(env, None) + [f"goto b{op[3]};"]
        if name == "CBRANCH":
            targets.add(op[3])
            cond = self.vn(op[2][0], env)
            return self.spill(env, None) + [f"if ({cond}) goto b{op[3]};"]
        if name == "CALL":
            return self.spill(env, None) + [f"{self.labels[op[3]]}();"]
        if name == "RETURN":
            return self.spill(env, None) + ["return;"]
        if name == "CALLOTHER":
            return [f"deopt(0x{op[2][0][1]:04X});", "return;"]
        target = self.vn(op[2][0], env)
        out = self.spill(env, None) + [f"switch ({target}) {{"]
        for pc, block in self.fn["tables"][op[3]]:
            targets.add(block)
            out.append(f"case 0x{pc:04X}: goto b{block};")
        return out + [f"default: deopt({target}); return;", "}"]


def emit(funcs, entry, regions=(), trace=False):
    """The whole program as C text; ``trace`` calls ``trace(pc)`` at every block."""
    names = Names(regions)
    labels = ["play" if f["entry"] and f["pc"] == entry else f"f_{f['pc']:04X}" for f in funcs]
    bodies = [
        line
        for f, label in zip(funcs, labels)
        for line in Fn(f, names, label, labels, trace).emit() + [""]
    ]
    scalars = sorted(set(re.findall(r"\bv_[0-9A-F]{4}\b", "\n".join(bodies))))
    regs = sorted(set(REG.values()) | set(re.findall(r"\bs\d+_[0-9A-F]{4}\b", "\n".join(bodies))))
    head = [
        "#include <stdint.h>",
        "extern uint8_t M[65536];",
        "uint8_t io_read(uint16_t a);",
        "void io_write(uint16_t a, uint8_t v);",
        "void deopt(uint16_t pc);",
        "uint8_t codechk(uint16_t a, uint8_t v);",
        "#define RD16(a) ((uint16_t)(M[(uint16_t)(a)] | M[(uint16_t)((a) + 1)] << 8))",
        "#define WR16(a, v) (M[(uint16_t)(a)] = (uint8_t)(v), M[(uint16_t)((a) + 1)] = (uint8_t)((v) >> 8))",
        "uint8_t " + ", ".join(regs) + ";",
    ] + (["void trace(uint16_t pc);"] if trace else [])
    head += names.macros() + [f"uint8_t *const {s} = M + 0x{s[2:]};" for s in scalars]
    return "\n".join(head + [f"void {label}(void);" for label in labels] + [""] + bodies)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("funcs")
    ap.add_argument("--entry", type=lambda s: int(s, 0), required=True)
    ap.add_argument("--layout")
    ap.add_argument("-o", "--out")
    ap.add_argument("--trace", action="store_true", help="call trace(pc) at every block")
    ap.add_argument("--compact", action="store_true", help="optimized, structured C (pcodec.py)")
    args = ap.parse_args(argv)
    funcs = pickle.loads(Path(args.funcs).read_bytes())
    regions = (
        json.loads(Path(args.layout).read_text(encoding="utf-8"))["regions"] if args.layout else ()
    )
    if args.compact:
        from pcodec import compact_c  # pylint: disable=import-outside-toplevel

        text = compact_c(funcs, args.entry, regions)[0]
    else:
        text = emit(funcs, args.entry, regions, args.trace) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
