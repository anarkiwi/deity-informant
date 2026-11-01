"""Synthetic 6502 programs for whole-program lifting tests, assembled with jennings.

Code lives at $1000, data at $1800, generated code at $3000; ``IO`` is an arbitrary test I/O
page treated as volatile. Each program is ``(source, entries, calls)``.
"""

import re

from jennings.assembler import Assembler
from jennings.devices.mpu6502 import MPU

IO = (0xC000, 0xC100)
_ASM = Assembler(MPU())


def lo(v):
    return v & 0xFF


def hi(v):
    return v >> 8 & 0xFF


def assemble(src, org=0x1000):
    """Two-pass assembly of ``label:``, ``.byte``, ``.org`` and instructions whose operands
    may hold ``{expr}`` over labels; the image and the labels."""
    lines = [ln.split(";")[0].strip() for ln in src.strip().splitlines()]
    labels = {}
    for final in (False, True):
        img = bytearray(0x10000)
        pc = org
        for ln in lines:
            m = re.match(r"(\w+):\s*(.*)", ln)
            if m:
                labels[m.group(1)] = pc
                ln = m.group(2)
            if not ln:
                continue

            def sub(mt, final=final):
                v = eval(mt.group(2), {"lo": lo, "hi": hi}, labels) if final else 0x1234
                return mt.group(1) + ("$%02X" % (v & 0xFF) if mt.group(1) == "#" else "$%04X" % v)

            if ln.startswith(".org"):
                pc = int(ln.split()[1].lstrip("$"), 16)
                continue
            if ln.startswith(".byte"):
                vals = [
                    eval(x.replace("$", "0x"), {"lo": lo, "hi": hi}, labels) if final else 0
                    for x in ln[5:].split(",")
                ]
                img[pc : pc + len(vals)] = bytes(v & 0xFF for v in vals)
                pc += len(vals)
                continue
            parts = ln.split(None, 1)
            if len(parts) == 2:
                m2 = re.match(r"^(#)(.+)()$|^(\(?)([^,)]+)(.*)$", parts[1])
                g = [x for x in m2.groups() if x is not None] if m2 else []
                if g and g[1] != "A" and g[1][0] not in "${":
                    ln = "%s %s{%s}%s" % (parts[0], g[0], g[1], g[2])
            text = re.sub(r"(#?)\{([^}]*)\}", sub, ln)
            b = _ASM.assemble(text, pc)
            img[pc : pc + len(b)] = bytes(b)
            pc += len(b)
    return img, labels


PATCH = """
entry:  LDA cnt
        CLC
        ADC #$01
        STA cnt
        AND #$07
        STA imm+1
        ASL A
        STA addr+1
        LDA cnt
        AND #$01
        CLC
        ADC #$18
        STA src+2
imm:    LDA #$00
addr:   STA $C000
src:    LDA $1820
        STA $C010
        RTS
.org $1800
cnt:    .byte 0
.org $1820
        .byte 7
.org $1920
        .byte 9
"""

TOGGLE = """
entry:  LDA site
        EOR #$80
        STA site
        LDA rsite
        EOR #$CD
        STA rsite
        LDA #$10
        CLC
site:   ADC #$03
        STA $C001
        JSR sub
        STA $C002
        RTS
sub:    LDA #$22
rsite:  RTS
        .byte $03,$C0
        STA $C004
        RTS
"""

GEN = """
gen:    TAX
        LDA #$A9
        STA $3000
        STX $3001
        TXA
        AND #$01
        BEQ even
        LDA #$49
        STA $3002
        LDA #$FF
        STA $3003
        LDY #$04
        BNE tail
even:   LDY #$02
tail:   LDA #$8D
        STA $3000,Y
        LDA #$10
        STA $3001,Y
        LDA #$C0
        STA $3002,Y
        LDA #$60
        STA $3003,Y
        RTS
run:    JSR $3000
        STA $C011
        RTS
"""

JUMPTAB = """
entry:  AND #$03
        JSR disp
        STA $C021
        RTS
disp:   TAX
        LDA tabh,X
        PHA
        LDA tabl,X
        PHA
        RTS
t0:     LDA #$01
        RTS
t1:     LDA #$02
        STA $C020
        RTS
t2:     LDX #$03
t2l:    STX $C022
        DEX
        BNE t2l
        RTS
t3:     LDA $C023
        RTS
tabh:   .byte hi(t0-1),hi(t1-1),hi(t2-1),hi(t3-1)
tabl:   .byte lo(t0-1),lo(t1-1),lo(t2-1),lo(t3-1)
"""

PUSHPULL = """
entry:  LDX #$07
        PHA
        TXA
        PHA
        LDA #$05
        STA $C030
        JSR inner
        PLA
        TAX
        PLA
        STA $C031
        STX $C032
        RTS
inner:  PHP
        PHA
        LDA #$99
        STA $C033
        PLA
        PLP
        RTS
"""

TSXPROG = """
entry:  JSR sub
        STA $C042
        RTS
sub:    TSX
        LDA $0101,X
        STA $C040
        LDA $0102,X
        STA $C041
        RTS
"""

JMPIND = """
entry:  AND #$03
        ASL A
        TAX
        LDA vec,X
        STA ptr
        LDA vec+1,X
        STA ptr+1
        JMP (ptr)
j0:     STA $C050
        RTS
j1:     STX $C051
        RTS
j2:     LDA $C052
        STA $C053
        RTS
j3:     RTS
vec:    .byte lo(j0),hi(j0),lo(j1),hi(j1),lo(j2),hi(j2),lo(j3),hi(j3)
.org $1810
ptr:    .byte 0,0
"""

NESTED = """
entry:  LDA #$00
        STA $FB
        LDA #$18
        STA $FC
        JSR a1
        RTS
a1:     LDY #$00
a1l:    JSR a2
        INY
        CPY #$03
        BNE a1l
        RTS
a2:     LDA ($FB),Y
        JSR a3
        RTS
a3:     STA $C060,Y
        LDA $C061
        AND #$01
        BEQ a3x
        INC $1810
a3x:    RTS
.org $1800
        .byte 4,5,6
"""

TIMING = """
entry:  LDX #$00
loop:   LDA $C070
        AND #$03
        BEQ skip
        LDA tbl,X
        STA $C071
skip:   INX
        CPX #$06
        BNE loop
        LDA $C072
        BMI near
        RTS
near:   JMP far
.org $10F8
far:    LDY $10FF,X
        BNE far2
far2:   STY $C073
        RTS
.org $18FC
tbl:    .byte 1,2,3,4,5,6,7,8,9,10
"""

BRKRTI = """
entry:  LDA #$05
        SEC
        BRK
        .byte $EA
        STA $C0A0
        RTS
handler:
        PHA
        CLC
        LDA #$77
        STA $C0A1
        PLA
        RTI
.org $FFFE
        .byte lo(handler),hi(handler)
"""

WRAP = """
entry:  LDA $C090
        BMI deep
        RTS
deep:   PHA
        JMP deep
"""

INVALIDATE = """
entry:  LDA cnt
        CLC
        ADC #$01
        STA cnt
        CMP #$05
        BNE ok
        LDY #$00
        LDA #$EA
        STA ($FB),Y
ok:     LDA #$01
tgt:    STA $C080
        RTS
init:   LDA #lo(tgt)
        STA $FB
        LDA #hi(tgt)
        STA $FC
        RTS
.org $1800
cnt:    .byte 0
"""

GUARDED = """
entry:  STA $FC
        LDA #$FB
        STA $FB
        LDX idx
        JSR sub
        STA $C0B0
        RTS
sub:    PHA
        LDY #$00
loop:   LDA ($FB),Y
        STA $C0B1,Y
        INY
        CPY #$05
        BNE loop
        .byte $BD,$50,$00
        STA $C0B6
        PLA
        RTS
.org $1810
idx:    .byte $20
.org $18FB
        .byte 1,2,3,4,5
"""

RECURSE = """
entry:  LDX #$04
        JSR rec
        STA $C0C0
        RTS
rec:    DEX
        BEQ done
        TXA
        PHA
        JSR rec
        PLA
        STA $C0C1
done:   LDA #$07
        RTS
"""

DEADFLAGS = """
entry:  SEC
        LDA $C0D0
        JSR sub
        STA $C0D1
        RTS
sub:    CLC
        ADC #$01
        RTS
"""
