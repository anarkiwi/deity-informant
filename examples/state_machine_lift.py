"""End-to-end canonical example for docs/register-model-lift-impl.md.

A hand-written 6502 playroutine (three parallel voices, a RAM SID shadow, hard
restart, vibrato over an ADC carry chain, SMC dispatch, deferred-carry cursors)
is decompiled, e-graph-minimized, Z3-folded to a role-typed u16 state machine."""

from __future__ import annotations

import re
import sys

from deity_informant import PcodeVM, lift, run_sub
from deity_informant import eqlift_mem
from deity_informant import framelog
from deity_informant import render as R
from deity_informant import structured as S
from deity_informant.lifter import OPS, MODE_LEN, ILLEGAL_OPCODES

PAL_CLOCK = 985248
PAL_CYCLES = 19656
SID = 0xD400
INIT, PLAY = 0x0F00, 0x1000
VOICES = 3
ZPV = (0x40, 0x50, 0x60)  # per-voice state block; identical layout, shifted base
PTR, DUR, PHASE, NLO, NHI, DEPTH, WAVE, CTL, AD, SR = 0, 2, 3, 4, 5, 6, 7, 8, 9, 10
SHADOW = 0x0340  # 3 x 7-byte SID image; the envelope/control lanes are flushed
FLUSH = (5, 6, 4)  # ADSR before the gate: ctrl is written last
WAVEF = (0x40, 0x20, 0x40)  # pulse lead, saw bass, pulse arpeggio
ADSR = ((0x08, 0xA9), (0x09, 0x68), (0x00, 0x86))
PW = ((0x00, 0x08), (0x00, 0x08), (0x00, 0x03))
TEST_BIT, GATE_BIT = 0x08, 0x01
BARS, FRAMES = 768, 800  # 8 bars of 96 frames, plus wrap through the loop command

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
                delta = val - pc
                assert -128 <= delta <= 127, "branch out of range: %r" % (operand,)
                out.append(delta & 0xFF)
            elif mode in _ONE:
                out.append(val & 0xFF)
            else:
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
        return bytes(out)


def sid_freq(hz):
    return round(hz * 0x1000000 / PAL_CLOCK)


# C major over three octaves from C3; scripts index this row by scale degree.
_DEG = (0, 2, 4, 5, 7, 9, 11)
NOTES = [sid_freq(130.81 * 2 ** ((12 * (i // 7) + _DEG[i % 7]) / 12)) for i in range(22)]
C3, F3, G3 = 0, 3, 4
C4, D4, E4, F4, G4 = 7, 8, 9, 10, 11
Q, EI, DQ, H = 24, 12, 36, 48
VIBTAB = (0, 1, 2, 3, 4, 3, 2, 1)
ON, OFF = 0x07, 0x00  # phase mask: 0 pins the table at its zero entry

_PHRASE = [(E4, Q), (E4, Q), (F4, Q), (G4, Q), (G4, Q), (F4, Q), (E4, Q), (D4, Q)]
LEAD = (
    _PHRASE
    + [(C4, Q), (C4, Q), (D4, Q), (E4, Q), (E4, DQ), (D4, EI)]
    + [("vib", ON), (D4, H - 4), ("vib", OFF), ("rest", 4)]
    + _PHRASE
    + [(C4, Q), (C4, Q), (D4, Q), (E4, Q), (D4, DQ), (C4, EI)]
    + [("vib", ON), (C4, H - 8), ("vib", OFF), ("rest", 8)]
)
_ROOTS = (C3, C3, F3, C3, G3, C3, G3, C3, C3, C3, F3, C3, G3, G3, C3, C3)
BASS = (
    [("vib", OFF)]
    + [(n, H) for n in _ROOTS[:11]]
    + [("vib", ON)]
    + [(n, H) for n in _ROOTS[11:15]]
    + [("vib", OFF), (_ROOTS[15], H - 8), ("rest", 8)]
)
_ARP = (C4, E4, G4, E4)
ARP = (
    [("vib", OFF)]
    + [(_ARP[k % 4], EI) for k in range(30)]
    + [("vib", ON)]
    + [(_ARP[k % 4], EI) for k in range(30, 62)]
    + [("vib", OFF), ("rest", 24)]
)
SCRIPTS = (LEAD, BASS, ARP)


def script_frames(script):
    return sum(n for op, n in script if op == "rest" or isinstance(op, int))


def _voice(p, v):
    """Emit voice ``v``: identical structure, shifted zp/shadow/SID bases."""
    b, sh, sidb = ZPV[v], SHADOW + 7 * v, SID + 7 * v

    def n(tag):
        return "v%d_%s" % (v, tag)

    def lb(tag, off=0):
        return ("L", n(tag), off) if off else ("L", n(tag))

    p.label(n("tick"))
    p.i("DEC", "zp", b + DUR).i("BEQ", "rel", lb("fetch"))
    p.i("LDA", "zp", b + DUR)
    p.i("CMP", "imm", 2).i("BEQ", "rel", lb("kill"))
    p.i("CMP", "imm", 1).i("BEQ", "rel", lb("test"))
    p.i("JMP", "abs", lb("tail"))
    p.label(n("kill"))  # hard restart at -2 frames: zero ADSR, drop the gate
    p.i("LDA", "imm", 0).i("STA", "zp", b + AD).i("STA", "zp", b + SR)
    p.i("LDA", "zp", b + CTL).i("AND", "imm", 0xFF ^ GATE_BIT).i("STA", "zp", b + CTL)
    p.i("JMP", "abs", lb("tail"))
    p.label(n("test"))  # hard restart at -1 frame: TEST resets the oscillator
    p.i("LDA", "zp", b + WAVE).i("ORA", "imm", TEST_BIT).i("STA", "zp", b + CTL)
    p.i("JMP", "abs", lb("tail"))
    p.label(n("fetch")).i("LDY", "imm", 0).i("LDA", "indy", b + PTR)
    p.i("BPL", "rel", lb("note"))
    p.i("AND", "imm", 3).i("TAX")  # Follin: dispatch through paired lo/hi tables
    p.i("LDA", "absx", lb("cmdlo")).i("STA", "abs", lb("jmpv", 1))
    p.i("LDA", "absx", lb("cmdhi")).i("STA", "abs", lb("jmpv", 2))
    p.label(n("jmpv")).i("JMP", "abs", 0)  # SMC operand: the dispatch head
    p.label(n("c_vib"))  # $80 dd: vibrato depth mask (arity 1)
    p.i("INY").i("LDA", "indy", b + PTR).i("STA", "zp", b + DEPTH)
    p.i("LDA", "zp", b + PTR).i("CLC").i("ADC", "imm", 2).i("STA", "zp", b + PTR)
    p.i("BCC", "rel", lb("v_nc")).i("INC", "zp", b + PTR + 1)
    p.label(n("v_nc")).i("JMP", "abs", lb("fetch"))
    p.label(n("c_off"))  # $81 dd: gate off, rest dd frames (arity 1)
    p.i("LDA", "zp", b + WAVE).i("STA", "zp", b + CTL)
    p.i("INY").i("LDA", "indy", b + PTR).i("STA", "zp", b + DUR)
    p.i("LDA", "zp", b + PTR).i("CLC").i("ADC", "imm", 2).i("STA", "zp", b + PTR)
    p.i("BCC", "rel", lb("tail")).i("INC", "zp", b + PTR + 1)
    p.i("JMP", "abs", lb("tail"))
    p.label(n("c_loop"))  # $82 ll hh: rewrite the cursor (control operator)
    p.i("INY").i("LDA", "indy", b + PTR).i("TAX")
    p.i("INY").i("LDA", "indy", b + PTR).i("STA", "zp", b + PTR + 1)
    p.i("STX", "zp", b + PTR).i("JMP", "abs", lb("fetch"))
    p.label(n("note"))  # nn dd: scale degree + duration; clears TEST, gates on
    p.i("TAX")
    p.i("LDA", "absx", ("L", "pitchlo")).i("STA", "zp", b + NLO)
    p.i("LDA", "absx", ("L", "pitchhi")).i("STA", "zp", b + NHI)
    p.i("LDA", "zp", b + WAVE).i("ORA", "imm", GATE_BIT).i("STA", "zp", b + CTL)
    p.i("LDA", "imm", ADSR[v][0]).i("STA", "zp", b + AD)
    p.i("LDA", "imm", ADSR[v][1]).i("STA", "zp", b + SR)
    p.i("LDA", "imm", 0).i("STA", "zp", b + PHASE)
    p.i("INY").i("LDA", "indy", b + PTR).i("STA", "zp", b + DUR)
    p.i("LDA", "zp", b + PTR).i("CLC").i("ADC", "imm", 2).i("STA", "zp", b + PTR)
    p.i("BCC", "rel", lb("tail")).i("INC", "zp", b + PTR + 1)
    p.label(n("tail"))  # vibrato straight to the chip, envelope/control via the shadow
    p.i("INC", "zp", b + PHASE)
    p.i("LDA", "zp", b + PHASE).i("AND", "zp", b + DEPTH).i("TAX")
    p.i("LDA", "absx", ("L", "vibtab"))
    p.i("CLC").i("ADC", "zp", b + NLO).i("STA", "abs", sidb + 0)
    p.i("LDA", "zp", b + NHI).i("ADC", "imm", 0).i("STA", "abs", sidb + 1)  # carry chain -> u16
    p.i("LDA", "zp", b + AD).i("STA", "abs", sh + 5)
    p.i("LDA", "zp", b + SR).i("STA", "abs", sh + 6)
    p.i("LDA", "zp", b + CTL).i("STA", "abs", sh + 4)
    for k in FLUSH:
        p.i("LDA", "abs", sh + k).i("STA", "abs", sidb + k)


def build_image():
    """Assemble play + data, then init against its labels; return (mem, labels)."""
    p = Asm(PLAY)
    for v in range(VOICES):
        _voice(p, v)
    p.i("RTS")
    for v in range(VOICES):
        cmds = ("v%d_c_vib" % v, "v%d_c_off" % v, "v%d_c_loop" % v)
        p.label("v%d_cmdlo" % v).byte(*[("LOL", c) for c in cmds])
        p.label("v%d_cmdhi" % v).byte(*[("HIL", c) for c in cmds])
    p.label("pitchlo").byte(*[f & 0xFF for f in NOTES])
    p.label("pitchhi").byte(*[f >> 8 for f in NOTES])
    p.label("vibtab").byte(*VIBTAB)
    for v, script in enumerate(SCRIPTS):
        assert script_frames(script) == BARS, "voice %d spans %d frames" % (
            v,
            script_frames(script),
        )
        p.label("script%d" % v)
        for item, arg in script:
            if item == "vib":
                p.byte(0x80, arg)
            elif item == "rest":
                p.byte(0x81, arg)
            else:
                p.byte(item, arg)
        p.byte(0x82, ("LOL", "script%d" % v), ("HIL", "script%d" % v))

    a = Asm(INIT)
    for v in range(VOICES):
        b = ZPV[v]
        a.i("LDA", "imm", ("LOL", "script%d" % v)).i("STA", "zp", b + PTR)
        a.i("LDA", "imm", ("HIL", "script%d" % v)).i("STA", "zp", b + PTR + 1)
        a.i("LDA", "imm", 1).i("STA", "zp", b + DUR)
        a.i("LDA", "imm", 0)
        for off in (PHASE, NLO, NHI, DEPTH, CTL, AD, SR):
            a.i("STA", "zp", b + off)
        a.i("LDA", "imm", WAVEF[v]).i("STA", "zp", b + WAVE)
        a.i("LDA", "imm", PW[v][0]).i("STA", "abs", SID + 7 * v + 2)
        a.i("LDA", "imm", PW[v][1]).i("STA", "abs", SID + 7 * v + 3)
    a.i("LDA", "imm", 0x0F).i("STA", "abs", SID + 0x18)
    a.i("RTS")

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
_PAIRBASE = re.compile(r"ptr_([0-9A-Fa-f]{4})(?:_(lo|hi))?$")
_SIDNAME = {R.sid_name(SID + r): r for r in range(25) if R.sid_name(SID + r)}


def cell_addr(name):
    m = _CELL.match(name)
    if m:
        return int(m.group(1) or m.group(2), 16)
    m = _PAIRRE.match(name)
    if m:
        return int(m.group(1), 16) + (m.group(2) == "hi")
    return None


def sid_target(name):
    """SID register index 0-24 for a hardware sink name, else None."""
    return _SIDNAME.get(name)


def is_shadow(name):
    a = cell_addr(name)
    return a is not None and SHADOW <= a < SHADOW + 7 * VOICES


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


def _base_split(e):
    """``(base cell, addend)`` for ``b`` or ``b + x`` / ``x + b``, else None."""
    if e[0] == "name":
        return e[1], None
    if e[0] == "bin" and e[1] == "+":
        for base, other in ((e[2], e[3]), (e[3], e[2])):
            if base[0] == "name" and cell_addr(base[1]) is not None:
                return base[1], other
    return None


def prove_pair(lo, hi, z3):
    """Z3-prove Concat(hi, lo) equals one wide sum; return its decomposition."""
    got_lo, got_hi = _base_split(lo), _base_split(hi)
    if got_lo is None or got_hi is None:
        return None
    (b_lo, addend), b_hi = got_lo, got_hi[0]
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


_CMPZ = {"<": "ULT", "<=": "ULE", ">": "UGT", ">=": "UGE"}


def _z3_cond(e, env, z3):
    """The emitted guard as a Z3 Bool; raises on anything not modelled."""
    if e[0] == "not":
        return z3.Not(_z3_cond(e[1], env, z3))
    if e[0] == "bin" and e[1] in _CMPZ:
        return getattr(z3, _CMPZ[e[1]])(_z3_expr(e[2], env, z3), _z3_expr(e[3], env, z3))
    if e[0] == "bin" and e[1] in ("==", "!="):
        a, b = _z3_expr(e[2], env, z3), _z3_expr(e[3], env, z3)
        return a == b if e[1] == "==" else a != b
    return _z3_expr(e, env, z3) != 0


def prove_advance(k, carried, cond, p, z3):
    """Z3-prove ``lo = p + k`` under guard ``cond`` is one u16 add on the pair.

    The guard's meaning is proved, not matched, so every spelling the extractor
    picks for the same deferred carry folds through this one rule."""
    lo, hi = z3.BitVec(p.replace(".", "_"), 16), z3.BitVec("hi", 16)
    try:
        nocarry = _z3_cond(cond, {p: lo}, z3)
    except (KeyError, ValueError):
        return False
    t = (lo + k) & 0xFF
    wide = ((hi << 8 | lo) + k) & 0xFFFF
    s = z3.Solver()
    if carried:
        folded = (z3.If(nocarry, hi & 0xFF, (hi + 1) & 0xFF) << 8 | t) & 0xFFFF
        s.add(z3.ULE(lo, 0xFF), z3.ULE(hi, 0xFF), folded != wide)
    else:
        s.add(z3.ULE(lo, 0xFF), z3.ULE(hi, 0xFF), nocarry, ((hi << 8) | t) != wide)
    return s.check() == z3.unsat


class _NoAbstraction(Exception):
    """The window holds a term the array abstraction does not model."""


def _store_addr(name):
    """Concrete address a statement target writes (state cell or SID sink)."""
    a = cell_addr(name)
    if a is not None:
        return a
    t = sid_target(name)
    return None if t is None else SID + t


def prove_forward(window, si, li, z3):
    """Z3-prove the SID sink at ``li`` reads what the shadow store at ``si`` composed.

    Intervening writes are ``Store``s at concrete addresses, so aliasing is decided;
    operators become uninterpreted functions (sound: the concrete ops refine them)
    and locals are version-keyed, so a reassignment between the sites refutes."""
    ufs, ver = {}, {}
    bv8, bv16 = z3.BitVecSort(8), z3.BitVecSort(16)
    at = z3.Function("at", bv16, bv8, bv16)  # a table read's address, unconstrained

    def uf(key, n):
        f = ufs.get((key, n))
        if f is None:
            f = z3.Function("f_%s_%d" % (re.sub(r"\W", "_", key), n), *([bv8] * (n + 1)))
            ufs[(key, n)] = f
        return f

    def ab(e, m):
        k = e[0]
        if k == "num":
            return z3.BitVecVal(e[1] & 0xFF, 8)
        if k == "name":
            a = cell_addr(e[1])
            if a is not None:
                return z3.Select(m, z3.BitVecVal(a, 16))
            return z3.BitVec("%s#%d" % (e[1], ver.get(e[1], 0)), 8)
        if k == "index":
            base = cell_addr(e[1])
            if base is None:
                raise _NoAbstraction(e[1])
            return z3.Select(m, at(z3.BitVecVal(base, 16), ab(e[2], m)))
        if k in ("not", "neg"):
            return uf(k, 1)(ab(e[1], m))
        if k == "call":
            return uf(e[1], len(e[2]))(*[ab(x, m) for x in e[2]])
        if k == "bin":
            return uf(e[1], 2)(ab(e[2], m), ab(e[3], m))
        raise _NoAbstraction(k)

    m, val = z3.Array("m", bv16, bv8), None
    try:
        for i, s in enumerate(window[: li + 1]):
            if i == li:
                sv = z3.Solver()
                sv.add(z3.Select(m, z3.BitVecVal(cell_addr(window[si][1]), 16)) != val)
                return sv.check() == z3.unsat
            a = _store_addr(s[1])
            if a is None:
                ver[s[1]] = ver.get(s[1], 0) + 1
                continue
            rhs = ab(s[2], m)
            if i == si:
                val = rhs
            m = z3.Store(m, z3.BitVecVal(a, 16), rhs)
    except _NoAbstraction:
        return False
    return False


def forward_shadow(stmts, proofs):
    """Store-to-load forward the RAM SID shadow into the hardware sinks.

    Each substitution is an array-theory proof instance; the e-graph already
    unions the two terms, but its printer refuses every spelling that mentions
    memory and so falls back to the raw shadow read-back."""
    import z3  # pylint: disable=import-outside-toplevel

    out, window, src = [], [], {}
    for s in stmts:
        if s[0] == "if":
            out.append(("if", s[1], forward_shadow(s[2], proofs), forward_shadow(s[3], proofs)))
        elif s[0] == "loop":
            out.append(("loop", forward_shadow(s[1], proofs)))
        elif s[0] == "switch":
            out.append(("switch", [(l, forward_shadow(b, proofs)) for l, b in s[1]]))
        elif s[0] != "asg":
            out.append(s)
        else:
            rhs = s[2]
            window.append(s)
            if sid_target(s[1]) is not None and rhs[0] == "name" and rhs[1] in src:
                if prove_forward(window, src[rhs[1]], len(window) - 1, z3):
                    proofs.append("forward_shadow(%s->%s)" % (rhs[1], s[1]))
                    s = ("asg", s[1], window[src[rhs[1]]][2])
                    window[-1] = s
            elif is_shadow(s[1]):
                src[s[1]] = len(window) - 1
            out.append(s)
            continue
        window, src = [], {}
    return out


def _reads(stmts, out):
    for s in stmts:
        for part in s[1:]:
            if isinstance(part, tuple):
                _names(part, out)
        for b in _bodies(s):
            _reads(b, out)
    return out


def drop_dead_shadow(stmts):
    """Drop shadow stores no expression reads: unread and not hardware, so
    unobservable. Iterated, since dropping one can free the store it read."""
    while True:
        pruned = _prune(stmts, _reads(stmts, set()))
        if pruned == stmts:
            return stmts
        stmts = pruned


def _prune(stmts, live):
    out = []
    for s in stmts:
        if s[0] == "asg" and is_shadow(s[1]) and s[1] not in live:
            continue
        if s[0] == "if":
            s = ("if", s[1], _prune(s[2], live), _prune(s[3], live))
        elif s[0] == "loop":
            s = ("loop", _prune(s[1], live))
        elif s[0] == "switch":
            s = ("switch", [(l, _prune(b, live)) for l, b in s[1]])
        out.append(s)
    return out


_FREQ = re.compile(r"(sid\.v[123])\.freq_(lo|hi)$")


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
        voice = _match_freq_pair(s, nxt)
        if voice is not None:
            got = prove_pair(s[2], nxt[2], z3)
            if got is not None:
                b_lo, b_hi, addend = got
                proofs.append("pair_store(%s,%s)" % (b_lo, b_hi))
                out.append(("st16", voice + ".freq", b_lo, b_hi, addend))
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
            pair, k, carried, cond, p, used = adv
            if prove_advance(k, carried, cond, p, z3):
                proofs.append("advance(%s,+%d,%s)" % (pair, k, "wide" if carried else "nocarry"))
                out.append(("adv16", pair, k, carried))
                i += used
                continue
        out.append(s)
        i += 1
    return out


def _match_freq_pair(s, nxt):
    """The voice whose freq lo/hi sinks these adjacent stores are, else None."""
    if s[0] != "asg" or nxt is None or nxt[0] != "asg":
        return None
    m1, m2 = _FREQ.match(s[1]), _FREQ.match(nxt[1])
    if not m1 or not m2 or m1.group(1) != m2.group(1):
        return None
    return m1.group(1) if (m1.group(2), m2.group(2)) == ("lo", "hi") else None


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


def _subst(e, env):
    """Inline the window's local bindings so the guard names only the read cell."""
    if e[0] == "name":
        return env.get(e[1], e)
    if e[0] in ("not", "neg"):
        return (e[0], _subst(e[1], env))
    if e[0] == "bin":
        return (e[0], e[1], _subst(e[2], env), _subst(e[3], env))
    if e[0] == "call":
        return (e[0], e[1], tuple(_subst(a, env) for a in e[2]))
    if e[0] == "index":
        return (e[0], e[1], _subst(e[2], env))
    return e


def _add_const(e, p):
    """``k`` if ``e`` is ``p + k`` in either order, else None."""
    if e[0] != "bin" or e[1] != "+":
        return None
    for a, b in ((e[2], e[3]), (e[3], e[2])):
        if a == ("name", p) and b[0] == "num":
            return b[1]
    return None


def _match_advance(stmts, i):
    """Match ``p = lo; ...; lo = p + k; if <guard> {} else {hi+1 | fault}``.

    The temporaries the extractor may or may not name are inlined; the guard is
    handed to Z3 rather than compared, so every spelling of the carry folds."""
    if stmts[i][0] != "asg" or stmts[i][2][0] != "name":
        return None
    pm = _PAIRRE.match(stmts[i][2][1])
    if not pm or pm.group(2) != "lo":
        return None
    pair, p = pm.group(1), stmts[i][1]
    lo, hi = "ptr_%s_lo" % pair, "ptr_%s_hi" % pair
    if cell_addr(p) is not None:
        return None
    env, k, j = {}, None, i + 1
    while j < len(stmts) and stmts[j][0] == "asg" and j - i <= 4:
        tgt, rhs = stmts[j][1], _subst(stmts[j][2], env)
        if tgt == lo:
            if k is not None:
                return None
            k = _add_const(rhs, p)
            if k is None:
                return None
        elif cell_addr(tgt) is not None or sid_target(tgt) is not None:
            return None
        else:
            env[tgt] = rhs
        j += 1
    if k is None or j >= len(stmts) or stmts[j][0] != "if" or stmts[j][2]:
        return None
    els = stmts[j][3]
    if els in (
        [("asg", hi, ("bin", "+", ("name", hi), ("num", 1)))],
        [("asg", hi, ("bin", "+", ("num", 1), ("name", hi)))],
    ):
        carried = True
    elif len(els) == 1 and els[0][0] == "unobserved":
        carried = False
    else:
        return None
    return "ptr_%s" % pair, k, carried, _subst(stmts[j][1], env), p, j + 1 - i


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
                reg = sid_target(s[1] + "_lo")
                wide = (self.ram[cell_addr(s[3])] << 8) | self.ram[cell_addr(s[2])]
                if s[4] is not None:
                    wide = (wide + self._val(s[4], env)[0]) & 0xFFFF
                out.append((reg, wide & 0xFF))
                out.append((reg + 1, wide >> 8))
                self.ram[SID + reg] = wide & 0xFF
                self.ram[SID + reg + 1] = wide >> 8
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


_FIELD = {
    PTR: "pos", DUR: "dur", PHASE: "phase", NLO: "note_lo", NHI: "note_hi",
    DEPTH: "vib", WAVE: "wave", CTL: "ctl", AD: "ad", SR: "sr",
}  # fmt: skip


def pretty(name):
    """Per-voice readable name for a state cell, whatever alias the emitter chose."""
    m = _PAIRBASE.match(name)
    if m:
        addr, lane = int(m.group(1), 16), ("_" + m.group(2) if m.group(2) else "")
    else:
        addr, lane = cell_addr(name), ""
    if addr is None:
        return name
    for v, b in enumerate(ZPV):
        if addr - b in _FIELD:
            return "v%d_%s%s" % (v + 1, _FIELD[addr - b], lane)
    return name


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
    for n in sorted(roles, key=lambda n: (order[roles[n]], pretty(n))):
        w = "u16" if roles[n] == "cursor" else "u8"
        lines.append("  %s: %s %s" % (pretty(n), roles[n], w))
    for v in range(1, VOICES + 1):
        lines.append("  v%d_note: parameter u16   ; note_hi:note_lo as one word" % v)
    lines.append("}")
    lines.append("pitch: u16[%d] = %s" % (len(NOTES), " ".join("$%04X" % f for f in NOTES)))
    lines.append("play {")
    _render(folded, lines, 1)
    lines.append("}")
    return "\n".join(_rename_line(ln) for ln in lines)


def _rename_line(ln):
    return re.sub(
        r"\b(?:ptr|zp|ctr|idx|pos|m)_[0-9A-Fa-f]+(?:_(?:lo|hi))?\b",
        lambda m: pretty(m.group(0)),
        ln,
    )


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
            lines.append("%s%s:u16 = %s" % (pad, s[1], rhs))
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


def image_end(labels):
    return labels["script%d" % (VOICES - 1)] + 0x200


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
    state, out = [0] * 25, []  # power-on zeros: a write of 0 is not a change
    if volume is not None:  # the PSID environment seeds $D418 before init runs
        state[24] = volume
    for writes in [list(init_writes)] + list(per_frame):
        for r, v in writes:
            if state[r] != v:
                state[r] = v
                out.append((r, v))
    return out


def _docker(args):
    import subprocess  # pylint: disable=import-outside-toplevel

    return subprocess.run(
        ["docker", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )


def _in_container(entrypoint, argv, tune, gets):
    """Run ``entrypoint argv`` on ``tune`` in the sidtrace image; copy ``gets`` out."""
    from pysidtracker.oracle import SIDTRACE_IMAGE  # pylint: disable=import-outside-toplevel

    create = ["create", "-w", "/work", "--entrypoint", entrypoint, SIDTRACE_IMAGE, *argv]
    cid = _docker(create).stdout.decode().strip()
    try:
        _docker(["cp", str(tune), "%s:/work/e2e.sid" % cid])
        _docker(["start", "-a", cid])
        for src, dst in gets:
            _docker(["cp", "%s:/work/%s" % (cid, src), str(dst)])
    finally:
        _docker(["rm", "-f", cid])


def sidtrace_stream(mem, labels):
    import tempfile  # pylint: disable=import-outside-toplevel
    from pathlib import Path  # pylint: disable=import-outside-toplevel
    from pysidtracker.oracle import read_sidtrace  # pylint: disable=import-outside-toplevel

    with tempfile.TemporaryDirectory() as td:
        tune = Path(td) / "e2e.sid"
        tune.write_bytes(to_psid(mem, image_end(labels)))
        out = Path(td) / "t.csv.zst"
        _in_container("sidtrace", ["t.csv.zst", "e2e.sid", "-t17"], tune, [("t.csv.zst", out)])
        rows = read_sidtrace(out)
    return [(row.reg, row.value) for row in rows if row.chip == 0 and 0 <= row.reg < 25]


def sidplayfp_wav(mem, labels, dst, seconds=20):
    """Render the tune to WAV with the dockerized sidplayfp; return ``dst``."""
    import tempfile  # pylint: disable=import-outside-toplevel
    from pathlib import Path  # pylint: disable=import-outside-toplevel

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tune = Path(td) / "e2e.sid"
        tune.write_bytes(to_psid(mem, image_end(labels)))
        argv = ["-wout.wav", "-t%d" % seconds, "-q", "e2e.sid"]
        _in_container("sidplayfp", argv, tune, [("out.wav", dst)])
    return dst


def minimized_wav(art, dst, seconds=20, model="8580"):
    """Render the MINIMIZED program's own write stream on an emulated SID."""
    from pathlib import Path  # pylint: disable=import-outside-toplevel
    from pysidtracker.audio import render_wav  # pylint: disable=import-outside-toplevel

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * PAL_CLOCK / PAL_CYCLES)
    stream = [list(art["init_writes"])] + art["min_frames"][:frames]
    return render_wav(
        stream,
        dst,
        model=model,
        cycles_per_frame=PAL_CYCLES,
        clock_frequency=float(PAL_CLOCK),
    )


def note_starts(grids, voice):
    """Frames on which ``voice``'s gate goes 0 -> 1 (a note attack)."""
    reg, out, prev = 7 * voice + 4, [], 0
    for i, g in enumerate(grids):
        if g[reg] & GATE_BIT and not prev & GATE_BIT:
            out.append(i)
        prev = g[reg]
    return out


def oscillator_reset_frames(grids, voice):
    """Frames on which ``voice`` holds the TEST bit (the oscillator reset)."""
    reg = 7 * voice + 4
    return [i for i, g in enumerate(grids) if g[reg] & TEST_BIT]


def restart_shape(grids, voice):
    """Per note attack, the (ad, sr, ctrl) of the two frames that precede it."""
    b = 7 * voice
    return [
        tuple((grids[i][b + 5], grids[i][b + 6], grids[i][b + 4]) for i in (f - 2, f - 1))
        for f in note_starts(grids, voice)
        if f >= 2
    ]


def adsr_before_gate(per_frame):
    """True if every frame writes each voice's AD/SR strictly before its ctrl."""
    for writes in per_frame:
        for v in range(VOICES):
            b, seen = 7 * v, {}
            for k, (r, _val) in enumerate(writes):
                if r - b in (4, 5, 6):
                    seen.setdefault(r - b, k)
            if 4 in seen and not all(seen.get(r, -1) < seen[4] for r in (5, 6)):
                return False
    return True


def pipeline(frames=FRAMES):
    """Build, verify, decompile, minimize, fold, execute; return the artifacts."""
    mem, labels = build_image()
    init_writes, ram0, orig_frames, orig_grids = run_vm(mem, frames)
    model, ev = S.decompile(bytearray(mem), INIT, PLAY, frames)
    assert S.Walker(model).run(frames) == ev.wlog, "walker replay is not bit-exact"
    text, _ = eqlift_mem.emit(model)
    play_ast = extract_proc(text, PLAY)
    proofs = []
    folded = fold(drop_dead_shadow(forward_shadow(play_ast, proofs)), proofs)
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
    kinds = {p.split("(")[0] for p in art["proofs"]}
    assert kinds == {"forward_shadow", "pair_store", "pair_set", "advance"}, kinds
    assert framelog.canonical(art["min_frames"]) == framelog.canonical(
        art["orig_frames"]
    ), "minimized program diverges from the VM frame projection"
    roles = classify_roles(art["folded"])
    text = render(art["folded"], roles)
    print(text)
    print()
    assert not re.search(r"m_03[0-9A-Fa-f]{2}", text), "shadow survives on the SID path"
    print("folds proved by Z3: %s" % ", ".join(sorted(kinds)))
    print("frame projection: minimized == VM over %d frames" % FRAMES)

    min_grids = grids_from_writes(art["init_writes"], art["min_frames"])
    assert min_grids == art["orig_grids"], "write-application grid diverges from VM grid"
    assert adsr_before_gate(art["min_frames"]), "minimized frame gates before writing the ADSR"
    for v in range(VOICES):
        shapes = restart_shape(art["orig_grids"], v)
        want = ((0, 0, WAVEF[v]), (0, 0, WAVEF[v] | TEST_BIT))
        assert shapes and all(s == want for s in shapes), "voice %d hard restart shape" % v
    print("hard restart: %d voices, ADSR-zero then TEST, ADSR written before the gate" % VOICES)

    try:
        from pysidtracker.oracle import register_grid  # pylint: disable=import-outside-toplevel

        psid = to_psid(art["mem"], image_end(art["labels"]))
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
    if "--wav" in argv:
        print("wav (minimized program, reSID): %s" % minimized_wav(art, "out/minimized.wav"))
        print("wav (sidplayfp): %s" % sidplayfp_wav(art["mem"], art["labels"], "out/tune.wav"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
