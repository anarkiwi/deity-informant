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

CORRELATED = """
entry:  STA $FB
        LDX #$03
loop:   LDA $FB
        AND bits,X
        BEQ s1
        TXA
        PHA
s1:     JSR sub
        LDA $FB
        AND bits,X
        BEQ s2
        PLA
        STA $C0E0
s2:     DEX
        BPL loop
        RTS
sub:    PHA
        TXA
        PHA
        LDA $C0E1
        STA $C0E2
        PLA
        TAX
        PLA
        RTS
.org $1800
bits:   .byte 1,2,4,8
"""

BRKZERO = """
entry:  JSR sub
        CMP #$80
        BCS trap
        STA $C0E8
        RTS
sub:    CMP #$40
        BEQ trap
        RTS
trap:   BRK
"""

SPRESTORE = """
entry:  TSX
        STX $FB
        PHA
        PHA
        STA $C0F0
        CMP #$80
        BCC keep
        DEX
keep:   TXS
        TSX
        CPX $FB
        BEQ done
        PLA
done:   STA $C0F1
        RTS
"""

DROPRET = """
entry:  JSR sub
        STA $C0F8
        RTS
sub:    CMP #$80
        BCS keep
        PLA
        PLA
keep:   LDA #$11
        STA $C0F9
        RTS
"""

PUSHRTS = """
entry:  STA $FB
        BIT $FB
        BPL go
        PHA
go:     AND #$01
        TAX
        LDA th,X
        PHA
        LDA tl,X
        PHA
        RTS
t0:     STX $C0FA
        JMP fin
t1:     LDA $C0FB
        STA $C0FC
fin:    BIT $FB
        BPL done
        PLA
done:   RTS
th:     .byte hi(t0-1),hi(t1-1)
tl:     .byte lo(t0-1),lo(t1-1)
"""

SPTABLE = """
entry:  TSX
        STX $FB
        AND #$01
        ASL A
        TAX
        TXS
        RTS
t0:     LDA #$01
        JMP fin
t1:     LDA #$02
fin:    LDX $FB
        TXS
        STA $C0E4
        RTS
.org $0101
        .byte lo(t0-1),hi(t0-1),lo(t1-1),hi(t1-1)
"""

CODEHIT = """
entry:  TAY
        LDX xs,Y
        LDA #$00
        STA tgt+1,X
        STA $1800,X
        LDA #$07
tgt:    STA $C0E6
        RTS
xs:     .byte $40,$00
"""

DATAINCODE = """
entry:  TAY
        BEQ skip
        JMP tbl
skip:   LDX $1800
        INC tbl,X
        LDA tbl,X
        STA $C000
        RTS
        .byte $00,$00
tbl:    .byte $EA,$00,$EA,$60
        .org $1800
        .byte $01
"""

UNEXEC = """
entry:  CMP #$C0
        BCS tas
        CMP #$80
        BCS ind
        PHA
        STA $C0D8
        PLA
        RTS
tas:    .byte $9B,$00,$01
        RTS
ind:    JMP ($0180)
done:   STA $C0D9
        RTS
.org $0180
        .byte lo(done),hi(done)
"""

# Locals addressed absolute,X from a frame pointer (`.byte`: the assembler would pick
# zero page,X, which wraps within page 0).
FRAME = """
entry:  TSX
        INX
        PHA
        LDA #$00
        PHA
        PHP
        LDA $C0D0
        BMI neg
        .byte $FE,$FE,$00
        JMP join
neg:    .byte $DE,$FE,$00
join:   PLP
        .byte $BD,$FF,$00
        STA $C0D1
        .byte $BD,$FE,$00
        STA $C0D2
        PLA
        PLA
        RTS
"""

PTABLE = """
entry:  TSX
        STX $FB
        LDA cnt
        INC cnt
        AND #$03
        BNE run
        LDA #hi(t0-1)
        STA $0142
        LDA #lo(t0-1)
        STA $0141
run:    LDX #$40
        TXS
        RTS
t0:     STA $C0DA
        JSR pause
        STA $C0DB
        JMP t0
pause:  PHA
        PLA
        LDX $FB
        TXS
        RTS
.org $1800
cnt:    .byte 0
.org $0141
        .byte lo(t0-1),hi(t0-1)
"""

# A resume table at $0141 written through an index loaded from a table (an unbounded
# interval), around ordinary pushes.
PXTABLE = """
entry:  TSX
        STX $FB
        TAY
        PHA
        LDX xs,Y
        LDA cnt
        INC cnt
        AND #$03
        BNE run
        LDA #hi(t0-1)
        STA $0142,X
        LDA #lo(t0-1)
        STA $0141,X
run:    PLA
        LDX #$40
        TXS
        RTS
t0:     STA $C0DA
        JSR pause
        STA $C0DB
        JMP t0
pause:  PHA
        PLA
        LDX $FB
        TXS
        RTS
xs:     .byte 0,0,2
.org $1800
cnt:    .byte 0
.org $0141
        .byte lo(t0-1),hi(t0-1)
"""

SPIN = """
entry:  STA $C0E0
spin:   JMP spin
"""

INDSPIN = """
entry:  STA $C0E1
ind:    JMP (ptr)
.org $1810
ptr:    .byte lo(ind),hi(ind)
"""

POPREC = """
entry:  STA $C0E2
        JSR rec
        RTS
rec:    PLA
        PLA
        STA $C0E3
        JSR rec
"""

COUNT = """
entry:  LDX #$00
        LDY #$00
loop:   INY
        BNE loop
        INX
        CPX #$04
        BNE loop
        JMP (ptr)
done:   STX $C0E4
        RTS
.org $1810
ptr:    .byte lo(done),hi(done)
"""

# Banked (C64 map, ROM-less image of the tests' bank map): RAM under KERNAL, BASIC and I/O
# read with $01 switched mid-call, a ROM byte and the port read back, the SID written with
# I/O in.
BANKRD = """
entry:  LDA #$34
        STA $01
        LDA $E000
        STA $C000
        LDA $A000
        STA $C001
        LDA $D400
        STA $C002
        LDA #$37
        STA $01
        LDA $E000
        STA $D400
        LDA $01
        STA $C003
        RTS
.org $A000
        .byte $A5
.org $D400
        .byte $D4
.org $E000
        .byte $E7
"""

# Code written into RAM under the KERNAL (ROM in), run with it banked out; a store into the
# ROM window lands in that RAM.
ROMRAM = """
entry:  LDA #$A9
        STA $E100
        LDA $C010
        STA $E101
        LDA #$60
        STA $E102
        LDA #$35
        STA $01
        JSR $E100
        STA $C011
        LDA #$37
        STA $01
        RTS
"""

# ROM code called from RAM code: the bank map's ROM routine at $E000 (`LDA #$42; RTS`).
ROMCALL = """
entry:  JSR $E000
        STA $C020
        LDA #$36
        STA $01
        LDA $E000
        STA $C021
        LDA #$37
        STA $01
        RTS
"""

# A configuration the facts never saw at `skip` (input 1) deopts there.
BANKSEEN = """
entry:  CMP #$01
        BNE skip
        LDA #$35
        STA $01
skip:   LDA $E000
        STA $C030
        LDA #$37
        STA $01
        RTS
.org $E000
        .byte $E5
"""

# ROM code (a ROM-less KERNAL stub's NOPs and RTS) called from RAM code, then the RAM under it
# read with BASIC/KERNAL out.
ROMCALLK = """
entry:  JSR $E4F0
        STA $C020
        LDA #$36
        STA $01
        LDA $E4F0
        STA $C021
        LDA #$37
        STA $01
        RTS
.org $E4F0
        .byte $5A
"""
