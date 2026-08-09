"""End-to-end canonical example for docs/register-model-lift-impl.md.

A hand-written 6502 playroutine (8 bars, vibrato, Follin-style SMC dispatch,
deferred-carry cursor) is decompiled, minimized in the value+memory e-graph,
folded to a role-typed u16 state machine by Z3-proved rules, frame-checked."""

from __future__ import annotations

import re
import sys

from deity_informant import PcodeVM, lift, run_sub
from deity_informant import eqlift_mem
from deity_informant import framelog
from deity_informant import structured as S
from deity_informant.lifter import OPS, MODE_LEN, ILLEGAL_OPCODES

PAL_CLOCK = 985248
SID = 0xD400
INIT, PLAY = 0x0F00, 0x1000
PTR, GATE, DEPTH, BASE_LO, BASE_HI, PHASE, DUR = 0xFB, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA
FRAMES = 800  # 8 bars of 96 frames + wrap through the script-loop command

_ENC = {}
for _op in sorted(OPS):
    if _op not in ILLEGAL_OPCODES:
        _ENC.setdefault(OPS[_op], _op)
_ONE = {"imm", "zp", "zpx", "zpy", "indx", "indy", "rel"}


class Asm:
    """Two-pass label assembler (after tests/_fuzzgen.Asm), legal opcodes only."""

    def __init__(self, org):
        self.org, self.items, self.labels = org, [], {}

    def i(self, mn, mode="impl", operand=None):
        self.items.append(("i", mn, mode, operand))
        return self

    def label(self, name):
        self.items.append(("label", name))
        return self

    def byte(self, *vals):
        for v in vals:
            self.items.append(("byte", v))
        return self

    def _resolve(self, operand):
        if operand is None:
            return 0
        if isinstance(operand, int):
            return operand
        kind, name = operand[0], operand[1]
        base = self.labels[name] + (operand[2] if len(operand) > 2 else 0)
        return {"L": base & 0xFFFF, "LOL": base & 0xFF, "HIL": (base >> 8) & 0xFF}[kind]

    def assemble(self):
        pc = self.org
        for it in self.items:
            if it[0] == "label":
                self.labels[it[1]] = pc
            else:
                pc += 1 if it[0] == "byte" else MODE_LEN[it[2]]
        out, pc = bytearray(), self.org
        for it in self.items:
            if it[0] == "label":
                continue
            if it[0] == "byte":
                out.append(self._resolve(it[1]) & 0xFF)
                pc += 1
                continue
            _, mn, mode, operand = it
            out.append(_ENC[(mn, mode)])
            pc += MODE_LEN[mode]
            if mode == "impl":
                continue
            val = self._resolve(operand)
            if mode == "rel":
                out.append((val - pc) & 0xFF)
            elif mode in _ONE:
                out.append(val & 0xFF)
            else:
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
        return bytes(out)


def sid_freq(hz):
    return round(hz * 0x1000000 / PAL_CLOCK)


# Ode to Joy, 8 bars: (pitch index, frames) plus vib on/off and rest commands.
NOTES = [sid_freq(hz) for hz in (261.63, 293.66, 329.63, 349.23, 392.00)]
C, D, E, F, G = range(5)
Q, EI, DQ, H = 24, 12, 36, 48
MELODY = (
    [(E, Q), (E, Q), (F, Q), (G, Q), (G, Q), (F, Q), (E, Q), (D, Q)]
    + [(C, Q), (C, Q), (D, Q), (E, Q), (E, DQ), (D, EI)]
    + [("vib", 1), (D, H - 4), ("vib", 0), ("rest", 4)]
    + [(E, Q), (E, Q), (F, Q), (G, Q), (G, Q), (F, Q), (E, Q), (D, Q)]
    + [(C, Q), (C, Q), (D, Q), (E, Q), (D, DQ), (C, EI)]
    + [("vib", 1), (C, H - 8), ("vib", 0), ("rest", 8)]
)
VIBTAB = (0, 1, 2, 3, 4, 3, 2, 1)


def build_image():
    """Assemble play + data, then init against its labels; return (mem, labels)."""
    a = Asm(INIT)
    a.i("LDA", "imm", ("LOL", "script")).i("STA", "zp", PTR)
    a.i("LDA", "imm", ("HIL", "script")).i("STA", "zp", PTR + 1)
    a.i("LDA", "imm", 1).i("STA", "zp", DUR)
    a.i("LDA", "imm", 0).i("STA", "zp", PHASE).i("STA", "zp", DEPTH)
    a.i("LDA", "imm", 0x10).i("STA", "zp", GATE)
    a.i("LDA", "imm", 0x29).i("STA", "abs", SID + 5)
    a.i("LDA", "imm", 0x59).i("STA", "abs", SID + 6)
    a.i("LDA", "imm", 0x0F).i("STA", "abs", SID + 0x18)
    a.i("RTS")

    p = Asm(PLAY)
    p.i("DEC", "zp", DUR).i("BNE", "rel", ("L", "effects"))
    p.label("fetch").i("LDY", "imm", 0).i("LDA", "indy", PTR)
    p.i("BPL", "rel", ("L", "note"))
    p.i("AND", "imm", 3).i("TAX")  # Follin: dispatch through paired lo/hi tables
    p.i("LDA", "absx", ("L", "cmdlo")).i("STA", "abs", ("L", "jmpv", 1))
    p.i("LDA", "absx", ("L", "cmdhi")).i("STA", "abs", ("L", "jmpv", 2))
    p.label("jmpv").i("JMP", "abs", 0)  # SMC operand: the dispatch head
    p.label("c_vib")  # $80 dd: set vibrato depth (arity 1)
    p.i("INY").i("LDA", "indy", PTR).i("STA", "zp", DEPTH)
    p.i("LDA", "zp", PTR).i("CLC").i("ADC", "imm", 2).i("STA", "zp", PTR)
    p.i("BCC", "rel", ("L", "v_nc")).i("INC", "zp", PTR + 1)
    p.label("v_nc").i("JMP", "abs", ("L", "fetch"))
    p.label("c_off")  # $81 dd: gate off, rest dd frames (arity 1)
    p.i("LDA", "imm", 0x10).i("STA", "zp", GATE)
    p.i("INY").i("LDA", "indy", PTR).i("STA", "zp", DUR)
    p.i("LDA", "zp", PTR).i("CLC").i("ADC", "imm", 2).i("STA", "zp", PTR)
    p.i("BCC", "rel", ("L", "effects")).i("INC", "zp", PTR + 1)
    p.i("JMP", "abs", ("L", "effects"))
    p.label("c_loop")  # $82 ll hh: rewrite the cursor (control operator)
    p.i("INY").i("LDA", "indy", PTR).i("TAX")
    p.i("INY").i("LDA", "indy", PTR).i("STA", "zp", PTR + 1)
    p.i("STX", "zp", PTR).i("JMP", "abs", ("L", "fetch"))
    p.label("note")  # nn dd: pitch index + duration
    p.i("TAX")
    p.i("LDA", "absx", ("L", "pitchlo")).i("STA", "zp", BASE_LO)
    p.i("LDA", "absx", ("L", "pitchhi")).i("STA", "zp", BASE_HI)
    p.i("LDA", "imm", 0x11).i("STA", "zp", GATE)
    p.i("LDA", "imm", 0).i("STA", "zp", PHASE)
    p.i("INY").i("LDA", "indy", PTR).i("STA", "zp", DUR)
    p.i("LDA", "zp", PTR).i("CLC").i("ADC", "imm", 2).i("STA", "zp", PTR)
    p.i("BCC", "rel", ("L", "effects")).i("INC", "zp", PTR + 1)
    p.label("effects")
    p.i("INC", "zp", PHASE)
    p.i("LDX", "zp", DEPTH).i("BEQ", "rel", ("L", "novib"))
    p.i("LDA", "zp", PHASE).i("AND", "imm", 7).i("TAX")
    p.i("LDA", "zp", BASE_LO).i("CLC").i("ADC", "absx", ("L", "vibtab"))
    p.i("STA", "abs", SID + 0)
    p.i("LDA", "zp", BASE_HI).i("ADC", "imm", 0)  # the carry chain -> u16
    p.i("STA", "abs", SID + 1)
    p.i("JMP", "abs", ("L", "wctrl"))
    p.label("novib")
    p.i("LDA", "zp", BASE_LO).i("STA", "abs", SID + 0)
    p.i("LDA", "zp", BASE_HI).i("STA", "abs", SID + 1)
    p.label("wctrl").i("LDA", "zp", GATE).i("STA", "abs", SID + 4).i("RTS")

    p.label("cmdlo").byte(("LOL", "c_vib"), ("LOL", "c_off"), ("LOL", "c_loop"))
    p.label("cmdhi").byte(("HIL", "c_vib"), ("HIL", "c_off"), ("HIL", "c_loop"))
    p.label("pitchlo").byte(*[f & 0xFF for f in NOTES])
    p.label("pitchhi").byte(*[f >> 8 for f in NOTES])
    p.label("vibtab").byte(*VIBTAB)
    p.label("script")
    for item, arg in MELODY:
        if item == "vib":
            p.byte(0x80, arg)
        elif item == "rest":
            p.byte(0x81, arg)
        else:
            p.byte(item, arg)
    p.byte(0x82, ("LOL", "script"), ("HIL", "script"))

    mem = bytearray(0x10000)
    code = p.assemble()
    a.labels.update(p.labels)
    init_code = a.assemble()
    mem[INIT : INIT + len(init_code)] = init_code
    mem[PLAY : PLAY + len(code)] = code
    return mem, p.labels


def run_vm(mem, frames):
    """Run init+play on the P-Code VM; post-init RAM, per-frame writes, grids."""
    vm = PcodeVM(bytes(mem))
    vm.wlog = []
    cache = {}
    run_sub(vm, INIT, cache, lift)
    init_writes = [(r, v) for _c, r, v in vm.wlog]
    ram0 = bytearray(vm.mem)
    per_frame, grids = [], []
    for _ in range(frames):
        vm.wlog = []
        run_sub(vm, PLAY, cache, lift)
        per_frame.append([(r, v) for _c, r, v in vm.wlog])
        grids.append([vm.mem[SID + i] for i in range(25)])
    return init_writes, ram0, per_frame, grids


_BIN = {
    "|": 1, "^": 2, "&": 3,
    "==": 4, "!=": 4, "<s": 4, ">=s": 4, "<=s": 4, ">s": 4, "<": 4, ">=": 4, "<=": 4, ">": 4,
    "<<": 5, ">>": 5, "+": 6, "-": 6,
}  # fmt: skip


class Tok:
    """Tokenizer for the emitted minimized dialect."""

    RE = re.compile(
        r"\s*(?:(\$[0-9A-Fa-f]+)|(\d+)|([A-Za-z_][\w.]*)"
        r"|(<<|>>|==|!=|<s|>=s|<=s|>s|<=|>=|<|>|[-+&|^!(){}\[\]:=,]))"
    )

    def __init__(self, text):
        self.toks, pos = [], 0
        while pos < len(text):
            m = self.RE.match(text, pos)
            if not m or m.end() == pos:
                raise SyntaxError("lex: %r" % text[pos : pos + 20])
            pos = m.end()
            self.toks.append(m.group(m.lastindex))
        self.k = 0

    def peek(self):
        return self.toks[self.k] if self.k < len(self.toks) else None

    def next(self):
        t = self.peek()
        self.k += 1
        return t

    def expect(self, t):
        got = self.next()
        if got != t:
            raise SyntaxError("expected %r got %r" % (t, got))


def _suffix(tk):
    while tk.peek() == ":":
        tk.next()
        tk.next()


def _atom(tk):
    t = tk.next()
    if t == "(":
        e = _expr(tk, 0)
        tk.expect(")")
        _suffix(tk)
        return e
    if t == "!":
        return ("not", _atom(tk))
    if t == "-":
        return ("neg", _atom(tk))
    if t.startswith("$"):
        return ("num", int(t[1:], 16))
    if t.isdigit():
        return ("num", int(t))
    if tk.peek() == "(":
        tk.next()
        args = [_expr(tk, 0)]
        while tk.peek() == ",":
            tk.next()
            args.append(_expr(tk, 0))
        tk.expect(")")
        _suffix(tk)
        return ("call", t, tuple(args))
    if tk.peek() == "[":
        tk.next()
        idx = _expr(tk, 0)
        _suffix(tk)
        tk.expect("]")
        _suffix(tk)
        return ("index", t, idx)
    return ("name", t)


def _expr(tk, minp):
    lhs = _atom(tk)
    while tk.peek() in _BIN and _BIN[tk.peek()] >= minp:
        op = tk.next()
        rhs = _expr(tk, _BIN[op] + 1)
        lhs = ("bin", op, lhs, rhs)
    return lhs


def parse_expr(text):
    tk = Tok(text)
    e = _expr(tk, 0)
    if tk.peek() is not None:
        raise SyntaxError("trailing %r in %r" % (tk.peek(), text))
    return e


def parse_block(lines, i):
    """Parse the emitted statement dialect into (stmts, index of closing line)."""
    out = []
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line in ("}", "} else {"):
            return out, i
        if re.fullmatch(r"\$[0-9A-Fa-f]+:", line):
            out.append(("label", line[:-1]))
        elif line in ("continue", "break", "ret"):
            out.append((line,))
        elif line.startswith("unobserved"):
            out.append(("unobserved", line.split()[1]))
        elif line.startswith("goto ("):
            out.append(("dgoto", parse_expr(line[5:])))
        elif line.startswith("goto "):
            out.append(("goto", line[5:].strip()))
        elif line == "loop {":
            body, i = parse_block(lines, i + 1)
            out.append(("loop", body))
        elif line == "switch {":
            i += 1
            cases = []
            while lines[i].strip() != "}":
                m = re.fullmatch(r"case (\$[0-9A-Fa-f]+): \{", lines[i].strip())
                if not m:
                    raise SyntaxError("switch: %r" % lines[i])
                body, i = parse_block(lines, i + 1)
                cases.append((m.group(1), body))
                i += 1
            out.append(("switch", cases))
        elif line.startswith("if ") and line.endswith("{"):
            then, i = parse_block(lines, i + 1)
            els = []
            if lines[i].strip() == "} else {":
                els, i = parse_block(lines, i + 1)
            out.append(("if", parse_expr(line[3:-1].strip()), then, els))
        else:
            m = re.fullmatch(r"([\w.]+)(?::\d)? = (.*)", line)
            if not m:
                raise SyntaxError("stmt: %r" % line)
            out.append(("asg", m.group(1), parse_expr(m.group(2))))
        i += 1
    raise SyntaxError("unterminated block")


def extract_proc(text, entry):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("sub_%04X {" % entry):
            return parse_block(lines, i + 1)[0]
    raise KeyError("sub_%04X not in emitted text" % entry)


_CELL = re.compile(r"(?:zp_([0-9A-Fa-f]{2})|(?:m|ctr|idx|pos)_([0-9A-Fa-f]{4}))$")
_PAIRRE = re.compile(r"ptr_([0-9A-Fa-f]{4})_(lo|hi)$")


def cell_addr(name):
    m = _CELL.match(name)
    if m:
        return int(m.group(1) or m.group(2), 16)
    m = _PAIRRE.match(name)
    if m:
        return int(m.group(1), 16) + (m.group(2) == "hi")
    return None


def _z3_expr(e, env, z3):
    k = e[0]
    if k == "num":
        return z3.BitVecVal(e[1] & 0xFFFF, 16)
    if k == "name":
        return env[e[1]]
    if k == "call" and e[1] == "zext2":
        return _z3_expr(e[2][0], env, z3)
    if k == "call" and e[1] == "carry":
        a, b = (_z3_expr(x, env, z3) for x in e[2])
        one, zero = z3.BitVecVal(1, 16), z3.BitVecVal(0, 16)
        return z3.If(z3.ULT(z3.BitVecVal(0xFF, 16), a + b), one, zero)
    if k == "bin" and e[1] in ("+", "-", "&", "|", "^"):
        a, b = _z3_expr(e[2], env, z3), _z3_expr(e[3], env, z3)
        v = {"+": a + b, "-": a - b, "&": a & b, "|": a | b, "^": a ^ b}[e[1]]
        return v & 0xFF if e[1] in ("+", "-") else v
    raise ValueError("z3: %r" % (e,))


def _names(e, out):
    if e[0] == "name":
        out.add(e[1])
    for kid in e[1:]:
        if isinstance(kid, tuple):
            _names(kid, out)


def prove_pair(lo, hi, z3):
    """Z3-prove Concat(hi, lo) equals one wide sum; return its decomposition."""
    if lo[0] == "name":
        b_lo, addend = lo[1], None
    elif lo[0] == "bin" and lo[1] == "+" and lo[2][0] == "name":
        b_lo, addend = lo[2][1], lo[3]
    else:
        return None
    if hi[0] == "name":
        b_hi = hi[1]
    elif hi[0] == "bin" and hi[1] == "+" and hi[2][0] == "name":
        b_hi = hi[2][1]
    else:
        return None
    ns = set()
    _names(lo, ns)
    _names(hi, ns)
    env = {n: z3.BitVec(n.replace(".", "_"), 16) for n in ns}
    cons = [z3.ULE(env[n], 0xFF) for n in ns]
    lo_v, hi_v = _z3_expr(lo, env, z3), _z3_expr(hi, env, z3)
    wide = (env[b_hi] << 8 | env[b_lo]) & 0xFFFF
    if addend is not None:
        wide = (wide + _z3_expr(addend, env, z3)) & 0xFFFF
    s = z3.Solver()
    s.add(z3.And(*cons), ((hi_v & 0xFF) << 8 | (lo_v & 0xFF)) != wide)
    if s.check() != z3.unsat:
        return None
    return b_lo, b_hi, addend


def prove_advance(k, carried, z3):
    """Z3-prove the deferred-carry advance equals one u16 add (or is carry-free)."""
    lo, hi = z3.BitVec("lo", 16), z3.BitVec("hi", 16)
    t = (lo + k) & 0xFF
    cflag = z3.ULT(t, k)
    wide = ((hi << 8 | lo) + k) & 0xFFFF
    s = z3.Solver()
    if carried:
        folded = (z3.If(cflag, (hi + 1) & 0xFF, hi & 0xFF) << 8 | t) & 0xFFFF
        s.add(z3.ULE(lo, 0xFF), z3.ULE(hi, 0xFF), folded != wide)
    else:
        s.add(z3.ULE(lo, 0xFF), z3.ULE(hi, 0xFF), z3.Not(cflag), ((hi << 8) | t) != wide)
    return s.check() == z3.unsat


def fold(stmts, proofs):
    """Rewrite byte-lane spellings to u16 statements, each instance Z3-proved."""
    import z3  # pylint: disable=import-outside-toplevel

    out, i = [], 0
    while i < len(stmts):
        s = stmts[i]
        if s[0] == "if":
            out.append(("if", s[1], fold(s[2], proofs), fold(s[3], proofs)))
            i += 1
            continue
        if s[0] == "loop":
            out.append(("loop", fold(s[1], proofs)))
            i += 1
            continue
        if s[0] == "switch":
            out.append(("switch", [(lbl, fold(b, proofs)) for lbl, b in s[1]]))
            i += 1
            continue
        nxt = stmts[i + 1] if i + 1 < len(stmts) else None
        if (
            s[0] == "asg"
            and s[1] == "sid.v1.freq_lo"
            and nxt is not None
            and nxt[0] == "asg"
            and nxt[1] == "sid.v1.freq_hi"
        ):
            got = prove_pair(s[2], nxt[2], z3)
            if got is not None:
                b_lo, b_hi, addend = got
                proofs.append("pair_store(%s,%s)" % (b_lo, b_hi))
                out.append(("st16", "sid.v1.freq", b_lo, b_hi, addend))
                i += 2
                continue
        ps = _match_pair_set(s, nxt)
        if ps is not None:
            pair, lanes = ps
            proofs.append("pair_set(%s)" % pair)
            out.append(("set16", pair, lanes["lo"], lanes["hi"]))
            i += 2
            continue
        adv = _match_advance(stmts, i)
        if adv is not None:
            pair, k, carried, used = adv
            if prove_advance(k, carried, z3):
                proofs.append("advance(%s,+%d,%s)" % (pair, k, "wide" if carried else "nocarry"))
                out.append(("adv16", pair, k, carried))
                i += used
                continue
        out.append(s)
        i += 1
    return out


def _match_pair_set(s, nxt):
    """Match adjacent independent stores to both lanes of one ptr pair."""
    if s[0] != "asg" or nxt is None or nxt[0] != "asg" or s[1] == nxt[1]:
        return None
    m1, m2 = _PAIRRE.match(s[1]), _PAIRRE.match(nxt[1])
    if not m1 or not m2 or m1.group(1) != m2.group(1):
        return None
    ns = set()
    _names(nxt[2], ns)
    if s[1] in ns:
        return None
    return "ptr_%s" % m1.group(1), {m1.group(2): s[2], m2.group(2): nxt[2]}


def _match_advance(stmts, i):
    """Match p=lo; t=(p+k); lo=t; cflag=(t<k); if !cflag {} else {hi+1 | fault}."""
    win = stmts[i : i + 5]
    if len(win) < 5 or any(s[0] != "asg" for s in win[:4]) or win[4][0] != "if":
        return None
    p_asg, t_asg, lo_asg, cf_asg = win[:4]
    if p_asg[2][0] != "name" or not _PAIRRE.match(p_asg[2][1]):
        return None
    pm = _PAIRRE.match(p_asg[2][1])
    pair, lane = pm.group(1), pm.group(2)
    if lane != "lo" or lo_asg[1] != "ptr_%s_lo" % pair:
        return None
    t = t_asg[2]
    if t[0] != "bin" or t[1] != "+":
        return None
    sides = {t[2][0]: t[2], t[3][0]: t[3]}
    if set(sides) != {"name", "num"} or sides["name"][1] != p_asg[1]:
        return None
    k = sides["num"][1]
    if lo_asg[2] != ("name", t_asg[1]):
        return None
    if cf_asg[2] != ("bin", "<", ("name", t_asg[1]), ("num", k)):
        return None
    cond, then, els = win[4][1], win[4][2], win[4][3]
    if cond != ("not", ("name", cf_asg[1])) or then:
        return None
    hi = "ptr_%s_hi" % pair
    if els in (
        [("asg", hi, ("bin", "+", ("name", hi), ("num", 1)))],
        [("asg", hi, ("bin", "+", ("num", 1), ("name", hi)))],
    ):
        return "ptr_%s" % pair, k, True, 5
    if len(els) == 1 and els[0][0] == "unobserved":
        return "ptr_%s" % pair, k, False, 5
    return None


_SIDREG = {"freq_lo": 0, "freq_hi": 1, "pw_lo": 2, "pw_hi": 3, "ctrl": 4, "ad": 5, "sr": 6}


def sid_target(name):
    m = re.fullmatch(r"sid\.v([123])\.(\w+)", name)
    if m:
        return 7 * (int(m.group(1)) - 1) + _SIDREG[m.group(2)]
    return {"sid.fc_lo": 21, "sid.fc_hi": 22, "sid.res": 23, "sid.vol": 24}.get(name)


class Flat:
    """Flatten folded statements to ops with resolved labels for execution."""

    def __init__(self, stmts):
        self.ops, self.labels, self.fix = [], {}, []
        self._walk(stmts, [])
        for at, lbl in self.fix:
            self.ops[at] = self.ops[at][:-1] + (self.labels[lbl],)

    def _jmp(self, lbl):
        self.fix.append((len(self.ops), lbl))
        self.ops.append(("jmp", lbl))

    def _walk(self, stmts, loops):
        k = 0
        while k < len(stmts):
            s = stmts[k]
            op = s[0]
            if op == "label":
                self.labels[s[1]] = len(self.ops)
            elif op in ("asg", "st16", "set16", "adv16"):
                self.ops.append(s)
            elif op == "if":
                l_else, l_end = "@f%d" % len(self.ops), "@e%d" % len(self.ops)
                self.fix.append((len(self.ops), l_else))
                self.ops.append(("bf", s[1], l_else))
                self._walk(s[2], loops)
                self._jmp(l_end)
                self.labels[l_else] = len(self.ops)
                self._walk(s[3], loops)
                self.labels[l_end] = len(self.ops)
            elif op == "loop":
                start, end = "@l%d" % len(self.ops), "@x%d" % len(self.ops)
                self.labels[start] = len(self.ops)
                self._walk(s[1], loops + [(start, end)])
                self._jmp(start)
                self.labels[end] = len(self.ops)
            elif op == "continue":
                self._jmp(loops[-1][0])
            elif op == "break":
                self._jmp(loops[-1][1])
            elif op == "goto":
                self._jmp(s[1])
            elif op == "dgoto":
                nxt = stmts[k + 1]
                assert nxt[0] == "switch", "computed goto without its dispatch"
                table, l_end = {}, "@s%d" % len(self.ops)
                self.ops.append(("dsw", s[1], table))
                for lbl, body in nxt[1]:
                    table[int(lbl[1:], 16)] = len(self.ops)
                    self._walk(body, loops)
                    self._jmp(l_end)
                self.labels[l_end] = len(self.ops)
                k += 1
            elif op == "unobserved":
                self.ops.append(("fault", s[1]))
            elif op == "ret":
                self.ops.append(("ret",))
            else:
                raise ValueError("flatten: %r" % (s,))
            k += 1


class Machine:
    """Execute the flattened, folded program over post-init RAM per frame."""

    def __init__(self, flat, ram0):
        self.flat, self.ram = flat, bytearray(ram0)

    def _val(self, e, env):
        k = e[0]
        if k == "num":
            return e[1], 2 if e[1] > 0xFF else 1
        if k == "name":
            addr = cell_addr(e[1])
            if addr is not None:
                return self.ram[addr], 1
            return env[e[1]], 1
        if k == "index":
            if e[1] == "mem":
                a, _ = self._val(e[2], env)
                return self.ram[a & 0xFFFF], 1
            base = cell_addr(e[1])
            i, _ = self._val(e[2], env)
            return self.ram[(base + i) & 0xFFFF], 1
        if k == "call" and e[1] == "zext2":
            return self._val(e[2][0], env)[0], 2
        if k == "call" and e[1] == "carry":
            a, _ = self._val(e[2][0], env)
            b, _ = self._val(e[2][1], env)
            return int(a + b > 0xFF), 1
        if k == "not":
            return int(self._val(e[1], env)[0] == 0), 1
        if k == "neg":
            v, w = self._val(e[1], env)
            return (-v) & (0xFFFF if w == 2 else 0xFF), w
        a, wa = self._val(e[2], env)
        b, wb = self._val(e[3], env)
        w = max(wa, wb)
        op = e[1]
        if op in ("==", "!=", "<", ">=", "<=", ">", "<s", ">=s", "<=s", ">s"):
            if op.endswith("s"):
                a = a - (1 << (8 * wa)) if a >> (8 * wa - 1) else a
                b = b - (1 << (8 * wb)) if b >> (8 * wb - 1) else b
                op = op[:-1]
            res = {"==": a == b, "!=": a != b, "<": a < b, ">=": a >= b, "<=": a <= b, ">": a > b}
            return int(res[op]), 1
        val = {
            "+": a + b,
            "-": a - b,
            "&": a & b,
            "|": a | b,
            "^": a ^ b,
            "<<": a << b,
            ">>": a >> b,
        }[op]
        if op == "<<":
            return val & 0xFFFF, 2
        return val & (0xFFFF if w == 2 else 0xFF), w

    def frame(self):
        env = {"a": 0, "x": 0, "y": 0}  # CPU entry registers, unread by the driver
        out, pc, ops, steps = [], 0, self.flat.ops, 0
        while pc < len(ops):
            steps += 1
            assert steps < 10000, "runaway frame"
            s = ops[pc]
            op = s[0]
            if op == "asg":
                sid = sid_target(s[1])
                v = self._val(s[2], env)[0]
                if sid is not None:
                    out.append((sid, v & 0xFF))
                    self.ram[SID + sid] = v & 0xFF
                else:
                    addr = cell_addr(s[1])
                    if addr is not None:
                        self.ram[addr] = v & 0xFF
                    else:
                        env[s[1]] = v & 0xFF
            elif op == "st16":
                _t, _n, b_lo, b_hi, addend = s
                wide = (self.ram[cell_addr(b_hi)] << 8) | self.ram[cell_addr(b_lo)]
                if addend is not None:
                    wide = (wide + self._val(addend, env)[0]) & 0xFFFF
                out.append((0, wide & 0xFF))
                out.append((1, wide >> 8))
                self.ram[SID], self.ram[SID + 1] = wide & 0xFF, wide >> 8
            elif op == "set16":
                base = cell_addr(s[1] + "_lo")
                lo = self._val(s[2], env)[0]
                hi = self._val(s[3], env)[0]
                self.ram[base + 1], self.ram[base] = hi & 0xFF, lo & 0xFF
            elif op == "adv16":
                base = cell_addr(s[1] + "_lo")
                assert s[3] or (self.ram[base] + s[2]) <= 0xFF, "nocarry guard"
                wide = ((self.ram[base + 1] << 8) | self.ram[base]) + s[2]
                self.ram[base] = wide & 0xFF
                self.ram[base + 1] = (wide >> 8) & 0xFF
            elif op == "bf":
                if self._val(s[1], env)[0] == 0:
                    pc = s[2]
                    continue
            elif op == "jmp":
                pc = s[1]
                continue
            elif op == "dsw":
                v = self._val(s[1], env)[0]
                assert v in s[2], "dispatch outside observed set: $%04X" % v
                pc = s[2][v]
                continue
            elif op == "fault":
                raise AssertionError("reached unobserved %s" % s[1])
            elif op == "ret":
                break
            pc += 1
        return out


RENAME = {
    "ptr_00FB": "song_pos", "ctr_00FA": "dur", "ctr_00F9": "phase",
    "zp_F7": "note_lo", "zp_F8": "note_hi", "zp_F6": "vib_on", "zp_F5": "wave",
}  # fmt: skip


def classify_roles(folded):
    """Read each state cell's role off its folded update shapes (the plan's rule)."""
    shapes = {}

    def walk(sl):
        for s in sl:
            if s[0] == "adv16":
                shapes.setdefault(s[1], set()).add("advance")
            elif s[0] == "set16":
                shapes.setdefault(s[1], set()).add("rewrite")
            elif s[0] == "asg" and cell_addr(s[1]) is not None:
                r = s[2]
                inc = r[0] == "bin" and r[1] in "+-" and ("name", s[1]) in (r[2], r[3])
                ctr = r[0] == "bin" and r[1] == "-" and r[2][0] == "name" and r[3] == ("num", 1)
                shapes.setdefault(s[1], set()).add("dec" if ctr else "inc" if inc else "set")
            for b in _bodies(s):
                walk(b)

    walk(folded)
    roles = {}
    for name, got in shapes.items():
        if "advance" in got or "rewrite" in got:
            roles[name] = "cursor"
        elif "dec" in got:
            roles[name] = "counter"
        elif "inc" in got:
            roles[name] = "accumulator"
        else:
            roles[name] = "parameter"
    return roles


def _bodies(s):
    if s[0] == "if":
        return [s[2], s[3]]
    if s[0] == "loop":
        return [s[1]]
    if s[0] == "switch":
        return [b for _l, b in s[1]]
    return []


def _fmt(e):
    k = e[0]
    if k == "num":
        return "$%02X" % e[1] if e[1] <= 0xFF else "$%04X" % e[1]
    if k == "name":
        return e[1]
    if k == "index":
        return "%s[%s]" % (e[1], _fmt(e[2]))
    if k == "call":
        return "%s(%s)" % (e[1], ", ".join(_fmt(a) for a in e[2]))
    if k == "not":
        return "!%s" % _fmt(e[1])
    if k == "neg":
        return "-%s" % _fmt(e[1])
    return "(%s %s %s)" % (_fmt(e[2]), e[1], _fmt(e[3]))


def render(folded, roles):
    """Print the folded program as the role-typed state machine.

    The field line is the dialect's own (``name: <role> uN``, sidprog.lark
    ``statedef``), so what this prints is what stage 4 emits."""
    lines = ["state {"]
    order = {"cursor": 0, "counter": 1, "accumulator": 2, "parameter": 3}
    for n in sorted(roles, key=lambda n: (order[roles[n]], n)):
        w = "u16" if roles[n] == "cursor" else "u8"
        lines.append("  %s: %s %s" % (RENAME.get(n, n), roles[n], w))
    lines.append("  note: parameter u16   ; note_hi:note_lo as one word (pitch row)")
    lines.append("}")
    lines.append("pitch: u16[%d] = %s" % (len(NOTES), " ".join("$%04X" % f for f in NOTES)))
    lines.append("play {")
    _render(folded, lines, 1)
    lines.append("}")
    return "\n".join(_rename_line(ln) for ln in lines)


def _rename_line(ln):
    for old, new in RENAME.items():
        ln = re.sub(r"\b%s\b" % old, new, ln)
    return ln


def _render(sl, lines, d):
    pad = "  " * d
    for s in sl:
        op = s[0]
        if op == "asg":
            lines.append("%s%s = %s" % (pad, s[1], _fmt(s[2])))
        elif op == "st16":
            rhs = "%s:%s as u16" % (s[3], s[2])
            if s[4] is not None:
                rhs += " + zext2(%s)" % _fmt(s[4])
            lines.append("%ssid.v1.freq:u16 = %s" % (pad, rhs))
        elif op == "set16":
            lines.append("%s%s:u16 = %s:%s" % (pad, s[1], _fmt(s[3]), _fmt(s[2])))
        elif op == "adv16":
            guard = "" if s[3] else "   ; guard: no page cross observed"
            lines.append("%s%s:u16 += %d%s" % (pad, s[1], s[2], guard))
        elif op == "if":
            lines.append("%sif %s {" % (pad, _fmt(s[1])))
            _render(s[2], lines, d + 1)
            if s[3]:
                lines.append("%s} else {" % pad)
                _render(s[3], lines, d + 1)
            lines.append("%s}" % pad)
        elif op == "loop":
            lines.append("%sloop {" % pad)
            _render(s[1], lines, d + 1)
            lines.append("%s}" % pad)
        elif op == "switch":
            lines.append("%sdispatch {" % pad)
            for lbl, b in s[1]:
                lines.append("%s  op %s: {" % (pad, lbl))
                _render(b, lines, d + 2)
                lines.append("%s  }" % pad)
            lines.append("%s}" % pad)
        elif op == "dgoto":
            continue
        elif op == "label":
            lines.append("%s%s:" % ("  " * max(d - 1, 0), s[1]))
        elif op == "goto":
            lines.append("%sgoto %s" % (pad, s[1]))
        elif op == "unobserved":
            lines.append("%sunobserved %s" % (pad, s[1]))
        else:
            lines.append("%s%s" % (pad, op))


def to_psid(mem, end):
    from pysidtracker import write_psid  # pylint: disable=import-outside-toplevel

    body = bytes((INIT & 0xFF, INIT >> 8)) + bytes(mem[INIT:end])
    return write_psid(load=0, init=INIT, play=PLAY, image=body, kind="PSID")


def grids_from_writes(init_writes, per_frame):
    state = [0] * 25
    for r, v in init_writes:
        state[r] = v
    out = []
    for frame in per_frame:
        for r, v in frame:
            state[r] = v
        out.append(list(state))
    return out


def change_stream(init_writes, per_frame, volume=None):
    state, out = [None] * 25, []
    if volume is not None:  # the PSID environment seeds $D418 before init runs
        state[24] = volume
    for writes in [list(init_writes)] + list(per_frame):
        for r, v in writes:
            if state[r] != v:
                state[r] = v
                out.append((r, v))
    return out


def sidtrace_stream(mem, labels):
    import subprocess  # pylint: disable=import-outside-toplevel
    import tempfile  # pylint: disable=import-outside-toplevel
    from pathlib import Path  # pylint: disable=import-outside-toplevel
    from pysidtracker.oracle import SIDTRACE_IMAGE  # pylint: disable=import-outside-toplevel
    from pysidtracker.oracle import read_sidtrace  # pylint: disable=import-outside-toplevel

    with tempfile.TemporaryDirectory() as td:
        tune = Path(td) / "e2e.sid"
        tune.write_bytes(to_psid(mem, labels["script"] + 0x100))
        out = Path(td) / "t.csv.zst"

        def d(args):
            return subprocess.run(
                ["docker", *args],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=180,
            )

        args = ["create", "-w", "/work", "--entrypoint", "sidtrace", SIDTRACE_IMAGE]
        cid = d(args + ["t.csv.zst", "e2e.sid", "-t17"]).stdout.decode().strip()
        try:
            d(["cp", str(tune), f"{cid}:/work/e2e.sid"])
            d(["start", "-a", cid])
            d(["cp", f"{cid}:/work/t.csv.zst", str(out)])
        finally:
            d(["rm", "-f", cid])
        rows = read_sidtrace(out)
    return [(row.reg, row.value) for row in rows if row.chip == 0 and 0 <= row.reg < 25]


def pipeline(frames=FRAMES):
    """Build, verify, decompile, minimize, fold, execute; return the artifacts."""
    mem, labels = build_image()
    init_writes, ram0, orig_frames, orig_grids = run_vm(mem, frames)
    model, ev = S.decompile(bytearray(mem), INIT, PLAY, frames)
    assert S.Walker(model).run(frames) == ev.wlog, "walker replay is not bit-exact"
    text, _ = eqlift_mem.emit(model)
    play_ast = extract_proc(text, PLAY)
    proofs = []
    folded = fold(play_ast, proofs)
    machine = Machine(Flat(folded), ram0)
    min_frames = [machine.frame() for _ in range(frames)]
    return {
        "mem": mem,
        "labels": labels,
        "init_writes": init_writes,
        "orig_frames": orig_frames,
        "orig_grids": orig_grids,
        "eqlift_text": text,
        "folded": folded,
        "proofs": proofs,
        "min_frames": min_frames,
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    art = pipeline()
    if "--dump" in argv:
        print(art["eqlift_text"])
        return 0
    assert len(art["proofs"]) >= 4, "expected pair/advance folds, got %r" % art["proofs"]
    assert framelog.canonical(art["min_frames"]) == framelog.canonical(
        art["orig_frames"]
    ), "minimized program diverges from the VM frame projection"
    roles = classify_roles(art["folded"])
    print(render(art["folded"], roles))
    print()
    print("folds proved by Z3: %s" % ", ".join(sorted(set(art["proofs"]))))
    print("frame projection: minimized == VM over %d frames" % FRAMES)

    min_grids = grids_from_writes(art["init_writes"], art["min_frames"])
    assert min_grids == art["orig_grids"], "write-application grid diverges from VM grid"

    try:
        from pysidtracker.oracle import register_grid  # pylint: disable=import-outside-toplevel

        psid = to_psid(art["mem"], art["labels"]["script"] + 0x100)
        oracle = [g[:25] for g in register_grid(psid, FRAMES)]
        assert oracle == art["orig_grids"], "pysidtracker oracle grid diverges"
        print("oracle grid: pysidtracker == VM == minimized over %d frames" % FRAMES)
    except ImportError:
        print("oracle grid: pysidtracker not installed, skipped")

    if "--sidtrace" in argv:
        stream = sidtrace_stream(art["mem"], art["labels"])
        if stream and stream[0] == (24, 0x0F):
            stream = stream[1:]
        mine = change_stream(art["init_writes"], art["min_frames"], volume=0x0F)
        n = min(len(stream), len(mine))
        assert n and mine[:n] == stream[:n], "sidtrace oracle diverges"
        print("sidplayfp/sidtrace oracle: %d register changes match (minimized side)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
