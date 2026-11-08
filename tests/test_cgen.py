"""deity_informant.cgen: programs translated to C, compiled with gcc and played against the
reference interpreter; relocation and data invariance of the ``.c``; llvm-mos builds it."""

import contextlib
import ctypes
import fcntl
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import wholeprog_progs as W
from deity_informant import NATIVE, cgen, trace, wholeprog
from deity_informant.cgen import csyntax, mlir, printer, regions, structure
from deity_informant.lifter import OPS as OPCODES

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import cclones  # noqa: E402  pylint: disable=wrong-import-position
import cgenplay  # noqa: E402  pylint: disable=wrong-import-position
import cmetrics  # noqa: E402  pylint: disable=wrong-import-position
from test_pcode2mlir import OPS, UNARY, VALUES, op_function, unary_function  # noqa: E402

pytestmark = pytest.mark.skipif(
    not NATIVE or not shutil.which("gcc") or not shutil.which("clang-format"),
    reason="needs the native extension, gcc and clang-format",
)
MOS = "ghcr.io/anarkiwi/docker-mos-llvm-sdk:v23.4.0"

TABLES = """
entry:  AND #$03
        TAX
        CLC
        LDA acc
        ADC lo,X
        STA acc
        LDA acc+1
        ADC hi,X
        STA acc+1
        STA $C000
        LDA acc
        STA $C001
        RTS
lo:     .byte $34,$FF,$80,$01
hi:     .byte $12,$00,$7F,$FF
acc:    .byte 0,0
end:
"""
SUBS = """
entry:  AND #$07
        TAX
        JSR emit
        INX
        JSR emit
        JSR twice
        RTS
twice:  INX
        JSR emit
        DEX
emit:   LDA tab,X
        STA $C002
        STX $C003
        RTS
tab:    .byte 1,2,3,4,5,6,7,8,9,10,11,12
end:
"""
PTRS = """
entry:  AND #$01
        ASL A
        TAX
        LDA ptrs,X
        STA $FB
        LDA ptrs+1,X
        STA $FC
        LDY #$00
loop:   LDA ($FB),Y
        BEQ done
        STA $C010
        INY
        BNE loop
done:   RTS
ptrs:   .byte lo(s0),hi(s0),lo(s1),hi(s1)
s0:     .byte 1,2,3,0
s1:     .byte 9,8,0
end:
"""
SONGPTR = """
entry:  TAY
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
        LDY #$00
loop:   LDA ($FB),Y
        BEQ done
        STA $C010
        TYA
        SEC
        ADC ($FB),Y
        TAY
        BCC loop
done:   RTS
plo:    .byte lo(p0),lo(p1),lo(p2)
phi:    .byte hi(p0),hi(p1),hi(p2)
p0:     .byte 1,9,2,8,7,0
p1:     .byte 3,1,2,3,0
p2:     .byte 0
end:
"""
SCRATCH = """
entry:  STA $FB
        STA $FC
        ASL $FB
        ROL $FC
        LDA $FC
        STA $C011
        LDA $FB
        STA $C012
        LDA $C013
        AND #$01
        TAY
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
        LDY #$01
        LDA ($FB),Y
        STA $C010
        RTS
other:  LDY #$00
        LDA ($FB),Y
        STA $C014
        RTS
plo:    .byte lo(p0),lo(p1)
phi:    .byte hi(p0),hi(p1)
p0:     .byte 5,6
p1:     .byte 7,8
end:
"""
RECORDS = """
plo:    .byte lo(p0),lo(p1)
phi:    .byte hi(p0),hi(p1)
p0:     .byte 1,9,2,8,7,0,3,1,2,3,0,0,0,0,0,0,0,0
p1:     .byte 0,5,1,4,2,6,0,0,0,0,0,0,0,0,0,0,0,0
end:
"""
STEP = """
init:   TAY
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
        RTS
play:   LDY #$00
        LDA ($FB),Y
        AND #$03
        TAY
        LDA ($FB),Y
        STA $C010
        INY
        TYA
        CLC
        ADC $FB
        STA $FB
        BCC done
        INC $FC
done:   RTS
""" + RECORDS
STEPBACK = STEP.replace(
    """        INY
        TYA
        CLC
        ADC $FB
        STA $FB
        BCC done
        INC $FC""",
    """        LDA $FB
        SEC
        SBC #$01
        STA $FB
        BCS done
        DEC $FC""",
).replace("init:   TAY", "init:   TAY\n        INY")
UNSET = STEP.replace(
    """init:   TAY
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
        RTS""",
    """init:   RTS""",
)
CELLS = """
init:   TAY
        LDA plo,Y
        STA ptr
        LDA phi,Y
        STA ptr+1
        RTS
play:   LDA ptr
        STA r0+1
        STA r1+1
        LDA ptr+1
        STA r0+2
        STA r1+2
r0:     LDA $FFFF
        AND #$03
        TAY
r1:     LDA $FFFF,Y
        STA $C010
        INY
        TYA
        CLC
        ADC ptr
        STA ptr
        BCC done
        INC ptr+1
done:   RTS
ptr:    .byte $FF,$FF
""" + RECORDS
ORDER = """
init:   LDA #$00
        STA pos
        RTS
play:   LDY pos
        LDX phi,Y
        BNE ok
        LDA back,Y
        TAY
        LDX phi,Y
ok:     STX $FC
        LDA plo,Y
        STA $FB
        INY
        STY pos
        LDY #$00
        LDA ($FB),Y
        STA $C010
        RTS
pos:    .byte 0
plo:    .byte lo(p0),lo(p1),lo(p0),0
phi:    .byte hi(p0),hi(p1),hi(p0),0
back:   .byte 0,0,0,1
p0:     .byte 7
p1:     .byte 9
end:
"""
ORDERBAD = ORDER.replace(
    "phi:    .byte hi(p0),hi(p1),hi(p0),0", "phi:    .byte hi(p0),hi(p1),0,0"
).replace("back:   .byte 0,0,0,1", "back:   .byte 0,0,0,2")
ZPCLEAR = """
entry:  LDX #$00
        TXA
clr:    STA $20,X
        INX
        CPX #$10
        BNE clr
        LDX #$02
        LDA #lo(msg)
        STA $20,X
        LDA #hi(msg)
        STA $21,X
        LDY #$01
        LDA ($22),Y
        STA $C000
        RTS
msg:    .byte $11,$22
end:
"""
REGS = """
entry:  AND #$03
        JSR sub
        STX $C000
        STY $C001
        JSR sub
        STX $C002
        STY $C003
        RTS
sub:    TAX
        INX
        TXA
        TAY
        INY
        RTS
end:
"""
KTAB = """
entry:  AND #$03
        TAX
        LDY voff,X
        STY idx
        LDA #$11
        STA $C0F0,Y
        RTS
voff:   .byte 0,7,14,7
idx:    .byte 0
end:
"""
OOB = """
tab:    .byte 1,2,3
entry:  LDX idx
        LDA tab,X
        STA $C020
        INC idx
        RTS
idx:    .byte 0
end:
"""
SMCOP = """
entry:  AND #$01
        BEQ add
        LDA #$E9
        STA site
        BNE go
add:    LDA #$69
        STA site
go:     LDA #$10
        SEC
site:   ADC #$03
        STA $C001
        RTS
end:
"""
ILLEGAL = """
entry:  STA $FB
        .byte $A7,$FB
        STX $C030
        .byte $8F,$31,$C0
        .byte $CF,lo(cnt),hi(cnt)
        PHP
        PLA
        STA $C032
        .byte $EF,lo(cnt),hi(cnt)
        STA $C033
        .byte $0F,lo(cnt),hi(cnt)
        STA $C034
        .byte $6F,lo(cnt),hi(cnt)
        STA $C035
        LDA cnt
        STA $C036
        RTS
cnt:    .byte $5A
end:
"""
UNTAKEN = """
entry:  LDX #$00
loop:   LDA tab,X
        BMI neg
        STA $C040
        INX
        CPX #$04
        BNE loop
        RTS
neg:    EOR #$FF
        STA $C041
        INX
        CPX #$04
        BNE loop
        RTS
tab:    .byte 1,2,3,4
end:
"""
SENS = """
entry:  TAX
        LDA $C0F0,X
        STA $C010
        LDA $C000
        STA $C011
        RTS
end:
"""
LOHI = """
entry:  AND #$03
        TAY
        LDA tlo,Y
        STA ptr
        LDA thi,Y
        STA ptr+1
        JMP (ptr)
j0:     LDA msg
        STA $C050
        RTS
j1:     LDA msg+1
        STA $C051
        RTS
j2:     LDX msg+2
        STX $C052
        RTS
j3:     STY $C053
        RTS
tlo:    .byte lo(j0),lo(j1),lo(j2),lo(j3)
thi:    .byte hi(j0),hi(j1),hi(j2),hi(j3)
msg:    .byte $11,$22,$33
ptr:    .byte 0,0
end:
"""
PAGE0 = """
entry:  AND #$01
        BEQ zero
        LDA #lo(msg)
        STA $FB
        LDA #hi(msg)
        STA $FC
        LDY #$01
        BNE read
zero:   STA $FB
        STA $FC
        LDY #$FB
read:   LDA ($FB),Y
        STA $C000
        RTS
msg:    .byte $11,$22
end:
"""
PSTORE = """
entry:  CLC
        ADC #lo(buf)
        STA $FB
        LDA sel
        STA $FC
        LDY #$00
        LDA #$5A
        STA ($FB),Y
        LDA ($FB),Y
        STA $C000
        RTS
sel:    .byte hi(buf)
buf:    .byte 0,0,0,0
end:
"""
OPJMP = """
entry:  INC cnt
        AND #$03
        TAY
        LDA tlo,Y
        STA site+1
        LDA thi,Y
        STA site+2
site:   JMP j0
j0:     LDA cnt
        STA $C000
        RTS
j1:     STY $C001
        RTS
j2:     STX $C002
        RTS
j3:     LDA #$33
        STA $C003
        RTS
tlo:    .byte lo(j0),lo(j1),lo(j2),lo(j3)
thi:    .byte hi(j0),hi(j1),hi(j2),hi(j3)
cnt:    .byte 0
end:
"""
STRADDLE = """
entry:  AND #$01
        TAY
        LDA tlo,Y
        STA site+1
site:   JMP j0
j0:     LDA buf
        LDY #$00
        STA ($FB),Y
        STA $C000
        RTS
j1:     INC buf
        LDA buf
        STA $C001
        RTS
tlo:    .byte lo(j0),lo(j1)
buf:    .byte 7
end:
"""
RELOCATOR = """
entry:  CMP #$01
        BEQ play
        LDX #$00
rloop:  LDA rtab+1,X
        BEQ rdone
        STA $FC
        LDA rtab,X
        STA $FB
        LDY #$01
        LDA #lo(data)
        STA ($FB),Y
        INY
        LDA #hi(data)
        STA ($FB),Y
        INX
        INX
        BNE rloop
rdone:  RTS
play:   LDX #$01
p1:     LDA $FFFF,X
        STA $C000
p2:     LDA $FFFF
        STA $C001
        RTS
rtab:   .byte lo(p1),hi(p1),lo(p2),hi(p2),0,0
data:   .byte $11,$22
end:
"""
BRTAB = """
entry:  AND #$03
        TAY
        LDA offs,Y
        STA br+1
        CLC
br:     BCC br+2
        LDA #$10
        STA $C000
        LDA #$11
        STA $C001
        LDA #$12
        STA $C002
        RTS
offs:   .byte 0,5,10,10
end:
"""
VOICES = """
entry:  AND #$03
        STA $10
        STA $11
        STA $12
v0:     DEC $13
        BPL n0
        LDA #$02
        STA $13
        INC c0+1
n0:     LDA $20
        CLC
c0:     ADC #$00
        STA $C000
        STA $20
        JMP v1
v1:     DEC $14
        BPL n1
        LDA #$02
        STA $14
        INC c1+1
n1:     LDA $21
        CLC
c1:     ADC #$00
        STA $C007
        STA $21
        JMP v2
v2:     DEC $15
        BPL n2
        LDA #$02
        STA $15
        INC c2+1
n2:     LDA $22
        CLC
c2:     ADC #$00
        STA $C00E
        STA $22
        RTS
end:
"""
FALLTHRU = """
entry:  AND #$03
        STA $13
        LDX #$00
        JSR voice
        LDX #$07
        JSR voice
        LDX #$0E
voice:  LDA $10,X
        CLC
        ADC $13
        STA $10,X
        STA $C000,X
        RTS
end:
"""
GUARDED = """
entry:  AND #$03
        STA $30
        STA $31
        STA $32
        LDA $30
        BNE s0
        .byte $02
s0:      STA $C000
        JMP v1
v1:     LDA $31
        BNE s1
        .byte $02
s1:      STA $C007
        JMP v2
v2:     LDA $32
        BNE s2
        .byte $02
s2:      STA $C00E
        RTS
end:
"""
TWIN = """
entry:  AND #$01
        BEQ b
        JSR v1
        JSR v1
        RTS
b:      JSR v2
        JSR v2
        RTS
v1:     LDX #$00
l1:     LDA tab,X
        STA $C000
        INX
        CPX #$04
        BNE l1
        RTS
v2:     LDX #$00
l2:     LDA tab,X
        STA $C000
        INX
        CPX #$04
        BNE l2
        RTS
tab:    .byte 1,2,3,4
end:
"""
OPCALL = """
entry:  CMP #$04
        BCS call
        TAY
        LDA tlo,Y
        STA site+1
        RTS
call:   AND #$03
        JSR sub
        PHA
        JSR sub
        PLA
        STA $C001
        RTS
sub:
site:   JSR j0
        STA $C000
        RTS
        .byte 0
j0:     LDY #$00
        LDA ($FB),Y
        RTS
j1:     LDA #$11
        RTS
j2:     LDA #$12
        RTS
j3:     LDA #$13
        RTS
tlo:    .byte lo(j0),lo(j1),lo(j2),lo(j3)
end:
"""
OPDISPATCH = """
entry:  CMP #$04
        BCS call
        TAY
        LDA tlo,Y
        STA s1+1
        STA s2+1
        RTS
call:   AND #$03
s1:     JSR h0
        STA $C000
s2:     JSR h0
        STA $C001
        RTS
h0:     LDA #$10
        JMP tail
h1:     LDA #$11
tail:   STA $C002
        RTS
h2:     LDA #$12
        RTS
h3:     LDA #$13
        RTS
tlo:    .byte lo(h0),lo(h1),lo(h2),lo(tail)
end:
"""
CMDFX = """
entry:  STA arg
        STA vars+2
        EOR #$01
        STA vars+5
        LDX #$00
        JSR chan
        LDX #$03
chan:   LDY vars+2,X
        LDA t0tbl,Y
        STA t0+1
        LDY vars,X
t0:     JSR t0a
        LDY vars,X
        LDA fxtbl,Y
        STA fx+1
fx:     JMP fxa
t0a:    LDA arg
        STA vars+1,X
        LDA vars+2,X
        STA vars,X
        RTS
t0b:    LDA #$00
        STA vars,X
        RTS
fxa:    LDA #$11
        STA $C000,X
        RTS
fxb:    LDA #$22
        STA $C000,X
        RTS
stray:  RTS
t0tbl:  .byte lo(t0a),lo(t0a),lo(t0b),lo(t0b)
fxtbl:  .byte lo(fxa),lo(fxb),lo(stray),lo(stray)
vars:   .byte 0,0,0,0,0,0
arg:    .byte 0
end:
"""
STACKOP = """
entry:  LDA site
        PHA
        LDA #$60
        STA site
        JSR site
        PLA
        STA site
        JSR site
        RTS
site:   LDA #$11
        STA $C000
        RTS
end:
"""
CLEAR = """
entry:  CMP #$01
        BEQ clear
        INC ops+1
ops:    LDA #$11
        STA $C060
        LDA #$22
        STA $C061
        LDA #$33
        STA $C062
        RTS
clear:  LDX #$0A
loop:   LDA #$00
        STA ops+1,X
        TXA
        SEC
        SBC #$05
        TAX
        BPL loop
        RTS
end:
"""
FLIP = """
entry:  CMP #$02
        BCS run
        LSR A
        LDA #$69
        BCC fill
        LDA #$E9
fill:   LDX #$05
set:    STA ops,X
        DEX
        DEX
        DEX
        DEX
        DEX
        BPL set
        RTS
run:    LDA #$10
        SEC
ops:    ADC #$03
        STA $C060
        ADC #$04
        STA $C061
        RTS
end:
"""
RELOC = """
entry:  AND #$03
        TAX
        LDA #lo(tab)
        STA $FB
        LDA #hi(tab)
        STA $FC
        LDA #lo(tab)
        STA op+1
        LDA #hi(tab)
        STA op+2
op:     LDA $FFFF,X
        STA $C050
        TXA
        TAY
        LDA ($FB),Y
        JSR emit
        LDA ptrs,X
        STA $FD
        LDA ptrs+1,X
        STA $FE
        LDA ($FD),Y
        JSR emit
        RTS
emit:   CLC
        ADC tab,X
        STA $C051
        RTS
tab:    .byte 3,1,4,1,5,9,2,6
ptrs:   .byte lo(tab),hi(tab),lo(tab+2),hi(tab+2),lo(tab+4),hi(tab+4),lo(tab+5),hi(tab+5)
end:
"""


TAIL2 = """
entry:  AND #$01
        BEQ a0
        LDX #$05
        STX $C010
        JMP tail
a0:     LDX #$01
        STA $C011
tail:   STX $C000
        LDA tab,X
        STA $C001
        RTS
tab:    .byte 1,2,3,4,5,6,7,8
end:
"""
TAIL3 = """
entry:  AND #$03
        BEQ a0
        CMP #$01
        BEQ a1
        LDX #$05
        JMP tail
a0:     LDX #$01
        JMP tail
a1:     LDX #$02
tail:   STX $C000
        LDA tab,X
        STA $C001
        RTS
tab:    .byte 1,2,3,4,5,6,7,8
end:
"""
EXITS = """
entry:  AND #$03
        TAX
outer:  LDY #$00
        CPX #$02
        BEQ found
inner:  LDA tab,Y
        BEQ none
        CMP marks,X
        BEQ found
        INY
        CPY #$04
        BNE inner
none:   LDA #$00
        STA $C000,X
        JMP next
found:  TYA
        STA $C004,X
next:   DEX
        BPL outer
        RTS
tab:    .byte 3,1,2,0
marks:  .byte 1,2,9,3
end:
"""
IRRED = """
entry:  LDX #$05
        AND #$01
        BEQ b
a:      STX $C000
        DEX
        BEQ done
b:      STX $C001
        DEX
        BNE a
done:   RTS
end:
"""
CHAIN = """
entry:  CMP #$01
        BEQ c1
        CMP #$02
        BEQ c2
        CMP #$05
        BEQ c3
        STA $C000
        RTS
c1:     LDX #$11
        STX $C001
        RTS
c2:     LDX #$22
        STX $C002
        RTS
c3:     LDX #$33
        STX $C003
        RTS
end:
"""
CHAIN_TAIL = """
entry:  LDX sel
        CMP #$01
        BEQ c1
        CMP #$02
        BEQ c2
        CMP #$05
        BEQ c3
        STA $C000
        LDY flag
        BEQ test
        STY $C005
test:   CPX cur
        BNE out
        STX $C004
out:    RTS
c1:     STX cur
        STA flag
        RTS
c2:     INC sel
        RTS
c3:     LDX #$33
        STX $C003
        RTS
sel:    .byte 0
cur:    .byte 1
flag:   .byte 0
end:
"""
BLOCKS = """
entry:  CMP #$02
        BCS play
        ASL A
        TAX
        LDA dst,X
        STA $FA
        LDA dst+1,X
        STA $FB
        LDA src,X
        STA $FC
        LDA src+1,X
        STA $FD
        LDY len,X
        LDA len+1,X
        TAX
        STY rest+1
        LDY #$00
        INX
        JMP pages
copy:   LDA ($FC),Y
        STA ($FA),Y
        INY
        BNE copy
        INC $FB
        INC $FD
pages:  DEX
        BEQ rest
        JMP copy
rest:   CPY #$00
        BEQ done
        LDA ($FC),Y
        STA ($FA),Y
        INY
        JMP rest
done:   RTS
play:   AND #$7F
        TAY
        LDA #$00
        STA $F8
        LDA #$50
        STA $F9
        LDA ($F8),Y
        STA $C000
        INC $F9
        LDA ($F8),Y
        STA $C001
        LDA $6008
        STA $C002
        RTS
dst:    .byte $00, $50, $00, $60
src:    .byte lo(entry), hi(entry), lo(play), hi(play)
len:    .byte $23, $01, $10, $00
end:
"""
SHARED = """
entry:  AND #$03
        BEQ e1
        JSR p1
        RTS
e1:     JSR p2
        RTS
p1:     LDX #$01
        JMP shared
p2:     LDX #$02
shared: CMP #$01
        BEQ join
        PHA
        STX $C000
        PLA
        STA $C003
join:   STA $C001
        STX $C002
        RTS
end:
"""
BRKVEC = """
entry:  CMP #$80
        BCS play
        LDA #lo(handler)
        STA $FFFE
        LDA #hi(handler)
        STA $FFFF
        RTS
play:   AND #$03
        TAX
brk:    BRK
        .byte $EA
        STX $C000
        RTS
handler: LDA cnt
        CLC
        ADC #$01
        STA cnt
        STA $C001
        RTI
cnt:    .byte 0
end:
"""
BRK2 = BRKVEC.replace(
    """        STX $C000
        RTS
handler:""",
    """        STX $C000
brk2:   BRK
        .byte $EA
        STX $C003
        RTS
handler:""",
)
SONGINIT = """
init:   TAY
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
        RTS
play:   AND #$03
        TAY
        LDA ($FB),Y
        STA $C000
        RTS
plo:    .byte lo(d0), lo(d1), lo(far), lo(far)
phi:    .byte hi(d0), hi(d1), $C0, $C0
d0:     .byte 1,2,3,4
d1:     .byte 5,6,7,8
far:    .byte 0
end:
"""
IOCLEAR = """
init:   LDA #$00
        LDY #$03
clr:    STA $C010,Y
        DEY
        BPL clr
        LDA #$11
        STA cell
        STA $C020
put:    LDA #$22
        STA cell+1
done:   RTS
play:   LDA cell
        STA $C000
        LDA cell+1
        STA $C001
        RTS
cell:   .byte 0, 0
end:
"""
SONGDATA = """
init:   LDA #$00
        LDX #$02
clear:  STA pos,X
        DEX
        BPL clear
        LDX #$02
voice:  LDY pos,X
        JSR seq
        BPL keep
        INC pos,X
        BNE voice
keep:   STA pat,X
        DEX
        BPL voice
done:   RTS
play:   LDX #$02
pl:     LDY pos,X
        JSR seq
        EOR pat,X
        STA $C000,X
        INC pos,X
        LDA pos,X
        AND #$07
        STA pos,X
        DEX
        BPL pl
        RTS
seq:    LDA order,Y
        RTS
pos:    .byte 9,9,9
pat:    .byte 9,9,9
order:  .byte 1,2,3,4,5,6,7,8
end:
"""
SONGIO = """
init:   LDY #$00
        LDA #$0F
        STA $C018
        JSR seq
        BPL done
        STA $C010
done:   RTS
play:   AND #$07
        TAY
        JSR seq
        STA $C000
        RTS
seq:    LDA order,Y
        RTS
order:  .byte 1,2,3,4,5,6,7,8
end:
"""
PAST = """
init:   RTS
play:   TAX
        LDA tab,X
        STA $C000
        RTS
tab:    .byte 1, 2, 3, 4
end:
"""
PATNUM = (
    """
init:   LDA #$00
        STA pat
        RTS
play:   AND #$07
        JSR next
        BMI keep
        STA pat
keep:   LDY pat
        LDA tab,Y
        STA $C000
        RTS
next:   TAY
        LDA order,Y
        RTS
pat:    .byte 0
order:  .byte 1, $85, 3, 2, $90, 0, 4, $FF
tab:    .byte """
    + ", ".join(str(k % 251) for k in range(160))
    + """
end:
"""
)
INITBRK = """
init:   LDA #lo(handler)
        STA $FFFE
        LDA #hi(handler)
        STA $FFFF
        RTS
play:   AND #$03
        TAX
        BRK
        .byte $EA
        STX $C000
        RTS
handler: INC cnt
        LDA cnt
        STA $C001
        RTI
cnt:    .byte 0
end:
"""
INLINE = """
entry:  JSR arg
        .byte $11
        STA $C000
        JSR arg
        .byte $22
        STA $C001
        RTS
arg:    PLA
        STA $F0
        PLA
        STA $F1
        LDY #$01
        LDA ($F0),Y
        TAX
        LDA $F0
        CLC
        ADC #$01
        TAY
        LDA $F1
        ADC #$00
        PHA
        TYA
        PHA
        TXA
        RTS
end:
"""
UNWIND = """
entry:  AND #$01
        TAX
        JSR voice
        INX
        JSR voice
        RTS
voice:  STX keep
        TSX
        STX save
        LDX keep
        JSR deep
after:  STX $C001
        RTS
deep:   JSR deeper
        RTS
deeper: STX $C000
        LDX save
        TXS
        LDX keep
        JMP after
keep:   .byte 0
save:   .byte 0
end:
"""
SHORT = """
entry:  TAX
        LDA flags,X
        AND #$01
        BEQ skip
        LDA count,X
        BEQ skip
        DEC count,X
        STX $C000
skip:   CPX #$02
        BEQ last
        CPX #$03
        BNE out
last:   STX $C001
out:    RTS
flags:  .byte 1,0,1,1
count:  .byte 2,2,0,1
end:
"""
EFFECT = """
entry:  AND #$03
        TAX
        LDA count,X
        BEQ do
        DEC count,X
        BNE skip
do:     STX $C000
skip:   RTS
count:  .byte 0,1,2,3
end:
"""
SWEEP = """
entry:  AND #$01
        STA dir+1
dir:    LDA #$00
        BNE up
down:   DEC val
        LDA val
        CMP #$03
        BCS out
        INC dir+1
up:     INC val
        LDA val
        CMP #$08
        BCC out
        DEC dir+1
        JMP down
out:    LDA val
        STA $C000
        RTS
val:    .byte 5
end:
"""
CASES = """
entry:  AND #$03
        TAX
        LDA tlo,X
        STA $F0
        LDA thi,X
        STA $F1
        JMP ($00F0)
h0:     LDA #$01
        JMP join
h1:     LDA #$02
        JMP join
h2:     LDA #$03
        STA $C002
h3:     LDA #$04
join:   STA $C000
        STA $C001
        RTS
tlo:    .byte lo(h0),lo(h1),lo(h2),lo(h3)
thi:    .byte hi(h0),hi(h1),hi(h2),hi(h3)
end:
"""
TAILS = """
entry:  AND #$03
        TAX
        BEQ x
        STX $C001
        JMP sub
x:      STX $C002
        JMP sub
other:  JSR sub
        RTS
sub:    STX $C000
        RTS
end:
"""
CMP3 = """
entry:  CMP #$0F
        BEQ eq
        BCC lt
        BCS ge
eq:     LDX #$01
        STX $C000
        RTS
lt:     STA $C001
        RTS
ge:     STA $C002
        RTS
end:
"""
PATCHED = """
entry:  AND #$03
        TAX
        LDA #$02
        STA base
        LDA base
        STA op+1
        TXA
op:     STA $3000
        LDA $3002
        STA $C000
        RTS
base:   .byte 9
end:
"""
CONSTS = """
entry:  AND #$03
        STA $13
v0:     LDA $20
        CLC
        ADC #$05
        STA $20
        STA $C000
        JMP v1
v1:     LDA $21
        CLC
        ADC #$01
        STA $21
        STA $C007
        JMP v2
v2:     LDA $22
        CLC
        ADC #$09
        STA $22
        STA $C00E
        RTS
end:
"""
FIELDS = """
entry:  LDX #$0E
loop:   JSR voice
        TXA
        SEC
        SBC #$07
        TAX
        BPL loop
        RTS
voice:  LDA $70,X
        AND #$10
        BEQ s0
        LDA $10,X
        CLC
        ADC #$03
        STA $10,X
s0:     LDY #$01
s1:     LDA $30,X
        CLC
        ADC #$01
        STA $30,X
        DEY
        BPL s1
        LDA $50,X
        CLC
        ADC #$2B
        STA $50,X
        STA $C000,X
        RTS
end:
"""
VLOOP = """
entry:  CMP #$80
        BCS init
        LDX #$0E
loop:   JSR voice
        TXA
        SEC
        SBC #$07
        TAX
        BPL loop
        RTS
init:   LDA #lo(handler)
        STA $FFFE
        LDA #hi(handler)
        STA $FFFF
        RTS
voice:  LDA $10,X
        STA $C0F0,X
        LDY #$01
inner:  INC $30,X
"""
BRKLOOP = VLOOP + """        BRK
        .byte $EA
        DEY
        BPL inner
        RTS
handler: INC $20,X
        RTI
end:
"""
SWITCH = VLOOP + """        STX keep
        TSX
        STX save
        LDX keep
        JSR deep
after:  DEY
        BPL inner
        RTS
deep:   JSR deeper
        RTS
deeper: INC $20,X
        LDX save
        TXS
        LDX keep
        JMP after
handler: RTI
keep:   .byte 0
save:   .byte 0
end:
"""
TESTED = """
entry:  AND #$03
        STA $13
v0:     LDA #$25
        AND #$20
        BEQ n0
        INC $20
n0:     LDA $20
        STA $C000
        JMP v1
v1:     LDA #$05
        AND #$20
        BEQ n1
        INC $21
n1:     LDA $21
        STA $C007
        JMP v2
v2:     LDA #$31
        AND #$20
        BEQ n2
        INC $22
n2:     LDA $22
        STA $C00E
        RTS
end:
"""
KEEPS = """
entry:  AND #$03
        TAX
        LDY #$05
        JSR sub
        JSR idle
        STA $C000
        STX $C001
        STY $C002
        LDY #$07
        JSR idle
        JSR sub
        STA $C003
        STY $C004
        RTS
sub:    INC $10,X
        LDA $10,X
        RTS
idle:   RTS
end:
"""
RUNREL = """
init:   LDX #$00
r:      LDA ptrs,X
        STA $FB
        LDA ptrs+1,X
        STA $FC
        LDY #$00
        LDA vals,X
        CLC
        ADC base
        STA ($FB),Y
        INY
        LDA vals+1,X
        ADC base+1
        STA ($FB),Y
        INX
        INX
        CPX #$04
        BNE r
        RTS
play:   AND #$03
        TAY
o0:     LDA d0,Y
        STA $C000
o1:     LDA d1,Y
        STA $C001
        RTS
base:   .byte $C0, $0F
ptrs:   .byte lo(o0+1), hi(o0+1), lo(o1+1), hi(o1+1)
vals:   .byte lo(d0-$FC0), hi(d0-$FC0), lo(d1-$FC0), hi(d1-$FC0)
d0:     .byte 1,2,3,4
d1:     .byte 5,6,7,8
end:
"""
ADD16 = """
entry:  AND #$0F
        CLC
        ADC $20
        STA $20
        STA $C000
        LDA $21
        ADC $22
        STA $21
        STA $C001
        RTS
end:
"""
RELOCATE = """
init:   LDX #$00
r:      LDA ptrs,X
        STA $FB
        LDA ptrs+1,X
        STA $FC
        LDY #$01
        LDA rel,X
        CLC
        ADC #lo(base)
        STA ($FB),Y
        INY
        LDA rel+1,X
        ADC #hi(base)
        STA ($FB),Y
        INX
        INX
        CPX #$04
        BNE r
        RTS
play:   AND #$03
        TAY
o0:     LDA tab,Y
        STA $C000
o1:     STA state
        RTS
ptrs:   .byte lo(o0), hi(o0), lo(o1), hi(o1)
rel:    .byte lo(tab-base), hi(tab-base), lo(state-base), hi(state-base)
base:
tab:    .byte 1,2,3,4
state:  .byte 0
end:
"""
INITREL = """
init:   STA song
        LDX #$00
r:      LDA ptrs,X
        STA $FB
        LDA ptrs+1,X
        STA $FC
        LDY #$01
        LDA rel,X
        CLC
        ADC base
        STA ($FB),Y
        INY
        LDA rel+1,X
        ADC base+1
        STA ($FB),Y
        INX
        INX
        CPX #$04
        BNE r
done:   LDA song
        STA $C002
        RTS
play:   AND #$03
        TAY
o0:     LDA $FFFF,Y
        STA $C000
        INC rel
o1:     STA $FFFF
        LDA song
        STA $C001
        RTS
song:   .byte 0
base:   .byte lo(tab), hi(tab)
ptrs:   .byte lo(o0), hi(o0), lo(o1), hi(o1)
rel:    .byte 0, 0, 4, 0
tab:    .byte 1,2,3,4
state:  .byte 0
end:
"""
#: A relocator patching play's operands from song header words through a table of
#: (site, header word, offset) entries, as SID-Wizard's does: o0 and o1 from one word.
WORDREL = """
init:   LDX #$00
r:      LDA sites,X
        STA $FB
        LDA sites+1,X
        STA $FC
        LDY words,X
        CLC
        LDA hdr,Y
        ADC offs,X
        STA tmp
        LDA hdr+1,Y
        ADC #$00
        LDY #$02
        STA ($FB),Y
        DEY
        LDA tmp
        STA ($FB),Y
        INX
        INX
        CPX #$06
        BNE r
        RTS
play:   AND #$03
        TAY
o0:     LDA $FFFF,Y
        STA $C000
o1:     LDA $FFFF,Y
        STA $C001
o2:     LDA $FFFF,Y
        STA $C002
        RTS
tmp:    .byte 0
sites:  .byte lo(o0), hi(o0), lo(o1), hi(o1), lo(o2), hi(o2)
words:  .byte 0, 0, 0, 0, 2, 0
offs:   .byte 0, 0, 1, 0, 0, 0
hdr:    .byte lo(tab1), hi(tab1), lo(tab2), hi(tab2)
pad:
tab1:   .byte 1,2,3,4,5
tab2:   .byte 9,8,7,6
end:
"""
INITA = """
init:   LDX #$5A
        STX flag
bysong: AND #$03
        CLC
        ADC #lo(tab)
        STA o0+1
        LDA #hi(tab)
        ADC #$00
        STA o0+2
        RTS
play:   AND #$03
        TAY
o0:     LDA $FFFF,Y
        STA $C000
        LDA flag
        STA $C001
        RTS
flag:   .byte 0
tab:    .byte 1,2,3,4,5,6,7,8
end:
"""
WRAP = """
entry:  AND #$0F
        ORA #$70
        TAX
        LDA #$5A
        STA $90,X
        TXA
        AND #$0F
        ORA #$10
        TAY
        LDA #$A5
        STA $FFF0,Y
        LDA $90,X
        STA $C000
        LDA $FFF0,Y
        STA $C001
        RTS
end:
"""

COMPARED = """
entry:  AND #$03
        STA $20
v0:     LDA $20
        BNE n0
        INC $30
n0:     LDA $30
        STA $C000
        JMP v1
v1:     LDA $20
        CMP #${one}
        BNE n1
        INC $31
n1:     LDA $31
        STA $C007
        JMP v2
v2:     LDA $20
        CMP #${two}
        BNE n2
        INC $32
n2:     LDA $32
        STA $C00E
        RTS
end:
"""
ARMS = """
entry:  AND #$81
        STA $13
        LDY $13
v0:     BMI m0
        DEC $20
        DEC $20
        LDA $20
        STA $C001
        JMP n0
m0:     INC $20
        INC $20
        LDA $20
        STA $C002
n0:     LDA $20
        STA $C000
        LDA $13
v1:     BMI m1
        DEC $21
        DEC $21
        LDA $21
        STA $C008
        JMP n1
m1:     INC $21
        INC $21
        LDA $21
        STA $C009
n1:     LDA $21
        STA $C007
        LDA $13
v2:     BMI m2
        DEC $22
        DEC $22
        LDA $22
        STA $C00F
        JMP n2
m2:     INC $22
        INC $22
        LDA $22
        STA $C010
n2:     LDA $22
        STA $C00E
        RTS
end:
"""
WIDE = """
entry:  AND #$03
        STA $13
"""


@contextlib.contextmanager
def locked(tmp_path_factory):
    """An exclusive lock shared by the xdist workers, for building or pulling images."""
    with open(tmp_path_factory.getbasetemp().parent / "docker.lock", "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


@pytest.fixture(name="prefix", scope="session")
def fixture_prefix(tmp_path_factory):
    """The command prefix running ``mlir-opt``; skips without it or Docker."""
    with locked(tmp_path_factory):
        prefix = mlir.runner(build=True)
    if prefix is None:
        pytest.skip("needs mlir-opt or Docker")
    return prefix


def assemble(src, org=0x1000, patch=None):
    """``(image, labels, loaded range)`` of ``src`` at ``org``, ``patch`` bytes at labels."""
    img, labels = W.assemble(src, org)
    for label, data in (patch or {}).items():
        img[labels[label] : labels[label] + len(data)] = bytes(data)
    return img, labels, (org, labels["end"])


def translate(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    prefix,
    img,
    labels,
    loaded,
    entries=("entry",),
    facts=None,
    sensitive=(),
    init=None,
    songs=None,
):
    """``(c, h, info)`` of the program."""
    ents = [(labels[n], n) for n in entries]
    return cgen.translate(
        img,
        ents,
        [W.IO],
        sensitive,
        facts,
        loaded=loaded,
        header="t.h",
        prefix=prefix,
        init=init,
        songs=songs,
    )


def play(tmp_path, c, h, img, labels, calls, sensitive=()):
    """Compile ``c``/``h`` and replay ``calls`` ``[(name, a)]`` against the reference."""
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")
    names = {n for n, _ in calls}
    return cgenplay.replay(lib, img, {n: labels[n] for n in names}, calls, [W.IO], sensitive)


def check(prefix, tmp_path, src, calls, gotos=0, **kw):
    """Translate, play and return ``(c, h, info)``; the ``.c`` has ``gotos`` gotos."""
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, **kw)
    assert cmetrics.metrics(c)["goto"] == gotos == info["structure"]["gotos"]
    assert {"flags", "exits", "entries"}.isdisjoint(info["structure"])
    assert len(info["provenance"]["gotos"]) == gotos
    play(tmp_path, c, h, img, labels, calls, kw.get("sensitive", ()))
    return c, h, info


def pointer_checks_only(info, src, org=0x1000):
    """Whether every trap of ``info`` is a deopt before an access through a zero-page pointer
    (the check keeping it out of page 1 and the I/O pages)."""
    img = assemble(src, org)[0]
    return all(
        key[0] == "deopt" and OPCODES[img[key[1]]][1] in ("indx", "indy")
        for key in info["traps"].values()
    )


def entry_calls(values, n=1):
    """``entry`` called with each ``A`` of ``values``, ``n`` times over."""
    return [("entry", a) for a in values] * n


@pytest.mark.parametrize(
    "src,values",
    [
        (TABLES, range(4)),
        (SUBS, range(8)),
        (PTRS, range(2)),
        (ILLEGAL, range(0, 256, 7)),
        (W.RECURSE + "end:\n", range(2)),
    ],
    ids=["tables", "subs", "ptrs", "illegal", "recurse"],
)
def test_matches_reference(prefix, tmp_path, src, values):
    c, _, info = check(prefix, tmp_path, src, entry_calls(values, 8))
    assert "trap(" not in c.split("void entry")[1] or pointer_checks_only(info, src)


def test_pointer_reads_go_through_regions(prefix, tmp_path):
    # The pointer's bases are the words of its table: the read is a bounds-checked read of
    # the span holding the strings, no dispatch over every region.
    c, h, info = check(prefix, tmp_path, PTRS, entry_calls([0, 1, 1, 0]))
    assert not info["unknown_access"] and not re.search(r"\brd\(", c) and "rdC0(" in c
    assert all(f"R{k}_ORG" in h for k in range(len(info["regions"])))


def test_pointer_reads_are_kept_off_the_stack_and_io(prefix, tmp_path):
    # Read-sensitive I/O would keep the throw-away reads of a page-crossing (zp),Y: the
    # pointer's address set (the words of its table) misses the I/O pages and page 1, so
    # there are none, no check is needed and pushes need not be in memory.
    c, _, info = check(prefix, tmp_path, PTRS, entry_calls([0, 1, 1, 0]), sensitive=(0xC0FF,))
    assert not info["traps"] and "io_read(" not in c.split("void entry")[1]
    assert all(org + n <= 0x100 or org >= 0x200 for org, n in info["regions"])


def test_pointer_from_split_tables_reads_one_span(prefix, tmp_path):
    # A data byte indexes the pointer's split tables (the index unbounded: they are read inside
    # their extent) and the offset grows by each record's length: every read is a bounds-checked
    # read of the span holding the records, with no dispatch over the regions.
    c, _, info = check(prefix, tmp_path, SONGPTR, entry_calls([0, 1, 2, 1, 0]))
    assert not re.search(r"\brd\(", c) and "rdC0(" in c and not info["unknown_access"]
    labels = assemble(SONGPTR)[1]
    [(lo, hi)] = info["code_held"]
    assert lo <= labels["p0"] and labels["end"] <= hi


def test_pointer_pair_reused_as_scratch(prefix, tmp_path):
    # The pair holds shifted data between its uses as a pointer: the read after it is set from
    # the tables reads the table its value comes from, the read in `other` (whatever the pair
    # last held) the span where it lands in the program and ``oob_rd`` elsewhere.
    calls = [("entry", 3), ("other", 0), ("entry", 0x81), ("other", 0)]
    c, _, _ = check(prefix, tmp_path, SCRATCH, calls, entries=("entry", "other"))
    entry, other = c.split("void entry")[1].split("void other")[0], c.split("void other")[1]
    assert not re.search(r"\brd\(|rdC0\(", entry) and "rdC0(" in other


@pytest.mark.parametrize(
    "src", [STEP, CELLS, STEPBACK, UNSET], ids=["zero-page", "cells", "backward", "unset"]
)
def test_pointer_advanced_by_record_lengths(prefix, tmp_path, src):
    # Init points the pair at a song's records; play reads one and advances the pair by its
    # length (`ADC lo; BCC; INC hi`, or into a copy's operand cells): the pair stays at or
    # above the least record start, so every read is one of the span holding the records.
    # Stepped back with a borrow, or never set from the image's zeros, it is not bounded: its
    # reads below the program read ``oob_rd``.
    c, info, labels = songs(prefix, tmp_path, src, (0, 1))
    assert not re.search(r"\brd\(", c) and "rdC0(" in c
    [(lo, hi)] = info["code_held"]
    assert lo <= labels["p0"] and labels["end"] <= hi and not info["unknown_access"]


@pytest.mark.parametrize("src", [ORDER, ORDERBAD], ids=["ok", "bad"])
def test_end_marker_tested_before_its_pointer(prefix, tmp_path, src):
    # The pointer's high byte comes from a table whose zero marks the end of the order: the
    # branch on it leaves only indices of nonzero bytes, and the restart index the marker's
    # entry gives names a nonzero byte, so the pointer never names zero page. When a restart
    # names another marker, it may: the span's reader reads zero page through ``oob_rd``.
    c, _, _ = songs(prefix, tmp_path, src, (0,))
    assert not re.search(r"\brd\(", c) and "rdC0(" in c


PUSH = """
stack:  .byte 0,0,0
entry:  LDX depth
        STA stack,X
        INX
        STX depth
        LDA stack
        STA $C010
        RTS
depth:  .byte 0
end:
"""


def test_store_past_its_array_is_out_of_bounds(prefix, tmp_path):
    # The push index grows without bound: the store is bounds-checked against the array its
    # base lies in and goes to oob_wr past it, not into the code after it.
    img, labels, loaded = assemble(PUSH)
    c, h, info = translate(prefix, img, labels, loaded)
    play(tmp_path, c, h, img, labels, entry_calls([5, 6, 7]))
    body = c.split("void entry")[1]
    assert "oob_wr(" in body and not re.search(r"\b(?:wr|codechk)\(", body)
    [k] = [k for k, (org, n) in enumerate(info["regions"]) if org == labels["stack"]]
    assert info["regions"][k][1] == 3 and re.search(rf"if \(.*R{k}_LEN\)", body)
    assert cgen.lift(img, [(labels["entry"], "entry")], [W.IO], loaded=loaded)[0].bounded == {
        labels["entry"] + 3: (labels["stack"], labels["entry"])
    }


CALLRET = """
stlo:   .byte 0,0
sthi:   .byte 0,0
depth:  .byte 0
init:   LDA #lo(song)
        STA $FB
        LDA #hi(song)
        STA $FC
        LDA #$00
        STA depth
        RTS
play:   LDY #$00
        LDA ($FB),Y
        BMI cmd
        STA $C010
        LDA $FB
        CLC
        ADC #$01
        STA $FB
        BCC done
        INC $FC
done:   RTS
cmd:    CMP #$81
        BEQ call
        CMP #$82
        BEQ ret
jump:   INY
        LDA ($FB),Y
        TAX
        INY
        LDA ($FB),Y
        STA $FC
        STX $FB
        JMP play
call:   LDX depth
        LDA $FB
        CLC
        ADC #$03
        STA stlo,X
        LDA $FC
        ADC #$00
        STA sthi,X
        INX
        STX depth
        JMP jump
ret:    DEC depth
        LDX depth
        LDA stlo,X
        STA $FB
        LDA sthi,X
        STA $FC
        JMP play
song:   .byte 1,2,$81,lo(sub),hi(sub),3,$80,lo(song),hi(song)
sub:    .byte 4,5,$82
end:
"""
IOJUMP = CALLRET.replace(
    """jump:   INY
        LDA ($FB),Y
        TAX
        INY
        LDA ($FB),Y""",
    """jump:   LDX $C013
        LDA $C014""",
)


@pytest.mark.parametrize("src,bounded", [(CALLRET, True), (IOJUMP, False)], ids=["song", "io"])
def test_pointer_loaded_from_song_data_and_its_stack(prefix, tmp_path, src, bounded):
    # A jump loads the song pointer from two song bytes, a call pushes it (advanced past the
    # command) on a stack whose depth nothing bounds and a return pops it: the pointer points
    # into the song on every path, so its reads are one span's bounds-checked reads, and the
    # push is bounds-checked against the stack. Loaded from I/O instead, it may point anywhere:
    # the same reader reads ``oob_rd`` outside the program.
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    assert not re.search(r"\brd\(", c) and "rdC0(" in c
    assert "oob_wr(" in c and not re.search(r"\bwr\(", c)
    if bounded:
        [(lo, hi)] = info["code_held"]
        assert lo <= labels["song"] and labels["end"] <= hi
        calls = [("init", 0)] + [("play", 0)] * 12
        play(tmp_path, c, h, img, labels, calls)


VOICE = """
state2: .byte 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
entry:  LDX #$0E
        JSR voice
        LDA state2,X
        STA $C011
        LDX #$07
        JSR voice
        LDA state2,X
        STA $C011
        LDX #$00
        JSR voice
        LDA state2,X
        STA $C011
        RTS
voice:  STX sav+1
        LDA #$05
        TAX
        LDA tab,X
        STA $C010
sav:    LDX #$00
        RTS
tab:    .byte 1,2,3,4,5,6,7,8
end:
"""
VOICEBAD = VOICE.replace(
    "        LDA #$05\n        TAX\n", "        LDA $C013\n        STA sav+1\n        TAX\n"
)


@pytest.mark.parametrize("src,kept", [(VOICE, True), (VOICEBAD, False)], ids=["saved", "other"])
def test_saved_index_reloaded_from_its_operand(prefix, tmp_path, src, kept):
    # The voice routine saves X in an operand and reloads it: the lift's value sets carry
    # the voice indices through the reload to the caller's indexed loads, which then stay in
    # their array. Another value stored into the operand reaches the reload too.
    img, labels, loaded = assemble(src)
    p, _ = cgen.lift(img, [(labels["entry"], "entry")], [W.IO], loaded=loaded)
    after = set(p.regs[labels["sav"] + 2][1])
    assert (after == {0, 7, 14}) == kept and {0, 7, 14} <= after
    c, h, info = translate(prefix, img, labels, loaded)
    held = any(lo <= labels["entry"] < hi for lo, hi in info["code_held"])
    assert held != kept
    if kept:
        play(tmp_path, c, h, img, labels, entry_calls([0, 1]))


LEFT = """
init:   LDA #lo(read)
        STA $FB
        LDA #hi(read)
        STA $FC
        RTS
play:   AND #$01
        BEQ read
        TAY
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
read:   LDY #$00
        LDA ($FB),Y
        STA $C010
        RTS
""" + RECORDS


@pytest.mark.parametrize("at", ["read", "p1"], ids=["code", "data"])
def test_pointer_init_leaves_bounds_its_reads(at):
    # Init runs first: play may read through the pair before it sets it from its table, so
    # the value init leaves (an instruction or a record alike) is in the reads' hull.
    src = LEFT.replace("lo(read)", f"lo({at})").replace("hi(read)", f"hi({at})")
    img, labels, loaded = assemble(src)
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    p, _ = cgen.lift(img, ents, [W.IO], loaded=loaded, init=0)
    lo = min(labels[at], labels["p0"])
    assert p.reach[labels["read"] + 2][0] == lo == p.proven[labels["read"] + 2][0]


def test_zero_page_cleared_then_a_pointer_set_through_an_index(prefix, tmp_path):
    # Every value the pair's bytes may hold (zero, the message's address) names a page outside
    # page 1 and the I/O pages: the read needs no check.
    _, _, info = check(prefix, tmp_path, ZPCLEAR, entry_calls([0, 0]))
    assert not info["traps"] and not info["stats"]["zone_guards"]


def test_constant_code_is_held_only_where_an_access_reaches(prefix, tmp_path):
    c, h, info = check(prefix, tmp_path, PTRS, entry_calls([0, 1]))
    # Only the pointer table and the strings it points at (tables) are held, not the code.
    _, labels, _ = assemble(PTRS)
    [(lo, hi)] = info["code_held"]
    assert lo == labels["ptrs"] and hi <= labels["end"]


def test_registers_left_in_register_variables(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, REGS, entry_calls([0, 1, 2, 3]))
    assert "struct" not in c and "static uint8_t rv" in c


def test_constant_table_bounds_an_index(prefix, tmp_path):
    # Y is a byte of a table no store writes: STA $C0F0,Y stays below the sensitive $C0FF,
    # so its throw-away read is not made; the byte Y is stored to keeps the table's bounds.
    c, _, _ = check(prefix, tmp_path, KTAB, entry_calls([0, 1, 2, 3]), sensitive=(0xC0FF,))
    assert "io_read(" not in c.split("void entry")[1]
    img, labels, loaded = assemble(KTAB)
    p, _ = cgen.lift(img, [(labels["entry"], "entry")], [W.IO], loaded=loaded)
    assert p.bounds[labels["idx"]] == (0, 14)


def test_read_off_a_table_into_code(prefix, tmp_path):
    c, _, info = check(prefix, tmp_path, OOB, entry_calls([0], 64))
    assert "C0[" in c
    assert info["code_span"][0] == W.assemble(OOB)[1]["entry"]
    [(lo, hi)] = info["code_held"]
    assert lo < info["code_span"][1] and info["code_span"][0] < hi


def test_smc_operand_cells(prefix, tmp_path):
    img, labels = W.assemble(W.PATCH)
    c, h, _ = cgen.translate(
        img,
        [(labels["entry"], "entry")],
        [W.IO],
        loaded=(0x1000, 0x1921),
        header="t.h",
        prefix=prefix,
    )
    play(tmp_path, c, h, img, labels, entry_calls([0] * 20))


def test_smc_opcode_cell(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, SMCOP, entry_calls([0, 1, 1, 0, 1], 4))
    assert "0xE9" in c and "0x69" in c


@pytest.mark.parametrize("name", ["JUMPTAB", "JMPIND", "PUSHRTS"])
def test_computed_jumps_from_facts(prefix, tmp_path, name):
    img, labels = W.assemble(getattr(W, name))
    pc = labels["entry"]
    facts = trace.collect(img, lambda s: [s.call(pc, a) for a in range(4)])
    c, h, info = cgen.translate(
        img,
        [(pc, "entry")],
        [W.IO],
        facts=facts,
        loaded=(0x1000, 0x1900),
        header="t.h",
        prefix=prefix,
    )
    assert info["facts_edges"]
    assert cmetrics.metrics(c)["goto"] == 0
    play(tmp_path, c, h, img, labels, entry_calls(range(4), 3))


@pytest.mark.parametrize("name", ["LOHI", "JMPIND", "JUMPTAB", "PUSHRTS"])
def test_computed_jumps_from_the_code(prefix, tmp_path, name):
    src = LOHI if name == "LOHI" else getattr(W, name) + "end:\n"
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(4), 3))
    [targets] = info["jumps"].values()
    assert len(targets) == (2 if name == "PUSHRTS" else 4)
    assert all(key[0] != "ind" for key in info["traps"].values())
    assert name == "PUSHRTS" or not info["traps"]
    org = info["code_span"][0]
    if name in ("LOHI", "JMPIND"):
        # Target bytes from one table pair indexed by the register still holding the index:
        # a switch on the index's values.
        step = 2 if name == "JMPIND" else 1
        assert "case CODE_ORG" not in c and re.search(r"switch \(v\d+\)", c)
        assert all(f"case {i * step}:" in c for i in range(3))
    else:
        assert all(f"CODE_ORG + 0x{t - org:X}" in c for t in targets[:-1])


def test_unresolved_jump_traps(prefix, tmp_path):
    edit = ("entry:  AND #$03", "entry:  LDX tlo\n        STX tlo\n        AND #$03")
    img, labels, loaded = assemble(LOHI.replace(*edit))
    c, h, info = translate(prefix, img, labels, loaded)
    assert not info["jumps"]
    k = next(k for k, key in info["traps"].items() if key[0] == "ind")
    assert f"trap({k})" in c
    with pytest.raises(AssertionError, match="trap"):
        play(tmp_path, c, h, img, labels, [("entry", 1)])
    facts = trace.collect(img, lambda s: [s.call(labels["entry"], a) for a in range(4)])
    c, h, info = translate(prefix, img, labels, loaded, facts=facts)
    assert info["facts_edges"] and not info["jumps"]
    (tmp_path / "facts").mkdir()
    play(tmp_path / "facts", c, h, img, labels, entry_calls(range(4), 2))


def test_page0_pointer_is_not_a_stack_access(prefix, tmp_path):
    _, _, info = check(prefix, tmp_path, PAGE0, entry_calls([0, 1, 0, 1], 2))
    assert pointer_checks_only(info, PAGE0) and info["stats"]["stack_guards"] == 0


def test_only_a_store_that_may_hit_the_stack_is_checked(prefix, tmp_path):
    _, _, info = check(prefix, tmp_path, PSTORE, entry_calls([0, 1, 2, 3, 1], 2))
    st = info["stats"]
    assert (st["stack_guards"], st["code_guards"]) == (0, 1)
    assert len(info["traps"]) == 1


def test_jump_through_operands_written_from_a_table(prefix, tmp_path):
    c, _, info = check(prefix, tmp_path, OPJMP, entry_calls([0, 1, 2, 3, 2, 0]))
    [(site, targets)] = info["jumps"].items()
    _, labels, _ = assemble(OPJMP)
    assert site == labels["site"] and targets == [labels[f"j{i}"] for i in range(4)]
    assert not info["traps"] and "switch (" in c


def test_table_region_past_the_code_span(prefix, tmp_path):
    c, h, info = check(prefix, tmp_path, STRADDLE, entry_calls([0, 1, 1, 0, 1, 0]))
    assert "codechk" in c and "C0_INIT" in h and len(info["jumps"]) == 1
    assert "static __attribute__((noinline)) uint8_t codechk(" in c
    _, labels, _ = assemble(STRADDLE)
    [k] = [k for k, (org, n) in enumerate(info["regions"]) if org <= labels["buf"] < org + n]
    assert f"#define R{k} (C0 + " in h and info["code_span"][1] == labels["buf"]


def test_pointer_stores_from_a_terminated_table_make_cells(prefix, tmp_path):
    c, _, info = check(prefix, tmp_path, RELOCATOR, entry_calls([0, 1, 1, 0, 1]))
    st = info["stats"]
    assert pointer_checks_only(info, RELOCATOR) and "codechk" not in c
    assert (st["code_guards"], st["cells"]) == (0, 4)


def test_branch_offset_written_from_a_table(prefix, tmp_path):
    # Offsets 0, 5 and 10 enter one straight-line run at three points: a switch whose cases
    # fall through into the next.
    c, _, info = check(prefix, tmp_path, BRTAB, entry_calls([0, 1, 2, 3, 1]))
    assert c.count("case CODE_ORG") == 2 and "break;" not in c
    _, labels, _ = assemble(BRTAB)
    br = labels["br"]
    assert info["jumps"] == {br: [br + 2, br + 7, br + 12]} and not info["traps"]


def test_identical_functions_fold(prefix, tmp_path):
    c, _, info = check(prefix, tmp_path, TWIN, entry_calls([0, 1, 1, 0]))
    names = sorted({int(n) for n in re.findall(r"\bf(\d+)\(", c)})
    assert info["structure"]["folded"] == 1 and names == list(range(1, len(names) + 1))


def test_computed_jsr_is_one_dispatch_function(prefix, tmp_path):
    """`JSR`s through operands written from one table call one function: a switch over the
    targets, its arms the handlers (one jumping into another's tail), handed the operand's low
    byte so its high byte relocates inside it."""
    calls = [("entry", a) for a in (1, 4, 4, 3, 5, 0, 6, 2, 7)]
    outs = []
    for org in (0x1000, 0x2300):
        img, labels, loaded = assemble(OPDISPATCH, org)
        c, h, info = translate(prefix, img, labels, loaded)
        prov = info["provenance"]
        entries = {f["entry"] for f in prov["functions"].values()}
        assert labels["s1"] in entries and labels["s1"] in prov["switches"]
        assert not entries & ({labels[f"h{i}"] for i in range(4)} | {labels["tail"]})
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, calls)
        outs.append(c)
    assert outs[0] == outs[1]


def test_call_through_an_operand_written_earlier(prefix, tmp_path):
    calls = [("entry", a) for a in (1, 4, 4, 3, 5, 0, 6, 2, 7)]
    outs = []
    for org in (0x1000, 0x2300):
        img, labels, loaded = assemble(OPCALL, org)
        c, h, info = translate(prefix, img, labels, loaded)
        assert info["jumps"] == {labels["site"]: [labels[f"j{i}"] for i in range(4)]}
        assert pointer_checks_only(info, OPCALL, org)
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, calls)
        outs.append(c)
    assert outs[0] == outs[1] and "rd(R0[0] | ((uint16_t)R0[1] << 8))" in outs[0]


def test_effect_index_copied_by_its_command_handler(prefix, tmp_path):
    """A per-channel command picks a `JSR` handler from a table; only the handler of
    commands 0 and 1 copies the command into the effect byte, which picks a `JMP` from a
    table whose later entries are no handlers: the effect jump goes to the two."""
    outs = []
    for org in (0x1000, 0x2300):
        img, labels, loaded = assemble(CMDFX, org)
        c, h, info = translate(prefix, img, labels, loaded)
        t0 = sorted(labels[n] for n in ("t0a", "t0b"))
        assert info["jumps"] == {labels["t0"]: t0, labels["fx"]: [labels["fxa"], labels["fxb"]]}
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, entry_calls(range(4), 2))
        outs.append(c)
    assert outs[0] == outs[1]


@pytest.mark.parametrize(
    "edit",
    [
        ("        LDA vars+2,X\n", "        INC vars+2,X\n        LDA vars+2,X\n"),
        ("        LDA vars+2,X\n", "        INY\n        TYA\n"),
        ("        LDA vars+2,X\n", "        INX\n        DEX\n        LDA vars+2,X\n"),
    ],
    ids=["byte-written", "not-the-byte", "index-changed"],
)
def test_effect_index_not_the_dispatched_byte(edit):
    """The effect byte holds the dispatched command only while the command byte and its
    index stand: otherwise the effect jump reads its table to its extent."""
    img, labels, loaded = assemble(CMDFX.replace(*edit))
    p, _ = cgen.lift(img, [(labels["entry"], "entry")], [W.IO], loaded=loaded)
    assert labels["stray"] in p.jumps[labels["fx"]]


def test_opcode_restored_through_the_stack(prefix, tmp_path):
    _, _, info = check(prefix, tmp_path, STACKOP, entry_calls([0, 1, 2]))
    assert not info["traps"]
    assert (info["stats"]["opcode_cells"], info["stats"]["code_guards"]) == (1, 0)


def test_split_table_index_past_its_extent_traps(prefix, tmp_path):
    img, labels, loaded = assemble(LOHI.replace("AND #$03\n", ""))
    c, h, info = translate(prefix, img, labels, loaded)
    [targets] = info["jumps"].values()
    assert targets == sorted(labels[f"j{i}"] for i in range(4))
    [(k, key)] = info["traps"].items()
    assert key == ("deopt", labels["entry"] + 1) and f"trap({k})" in c
    play(tmp_path, c, h, img, labels, entry_calls(range(4), 2))
    for a in (4, 0xFF):
        (tmp_path / str(a)).mkdir(parents=True)
        with pytest.raises(AssertionError, match="trap"):
            play(tmp_path / str(a), c, h, img, labels, [("entry", a)])


def test_or_ed_high_byte_relocates(prefix, tmp_path):
    src = OPJMP.replace("        LDA thi,Y\n        STA site+2\n", "")
    outs = []
    for org in (0x1000, 0x2300):
        img, labels, loaded = assemble(src, org)
        c, h, info = translate(prefix, img, labels, loaded)
        assert len(info["jumps"][labels["site"]]) == 4 and not info["traps"]
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, entry_calls(range(4), 2))
        outs.append(c)
    assert outs[0] == outs[1] and "| CODE_ORG" in outs[0]
    assert not re.search(r"0x1[0-9A-F]{3}\b|0x23[0-9A-F]{2}\b", outs[0])


def test_jump_table_data_and_relocation_invariance(prefix, tmp_path):
    outs = []
    runs = ((0x1000, [0x11, 0x22, 0x33]), (0x1000, [0x44, 0x55, 0x66]), (0x2345, [1, 2, 3]))
    for i, (org, msg) in enumerate(runs):
        img, labels, loaded = assemble(LOHI, org, patch={"msg": msg})
        c, h, _ = translate(prefix, img, labels, loaded)
        (tmp_path / str(i)).mkdir()
        play(tmp_path / str(i), c, h, img, labels, entry_calls(range(4), 2))
        outs.append((c, h))
    assert outs[0][0] == outs[1][0] == outs[2][0] and len({h for _, h in outs}) == 3
    assert not re.search(r"0x1[0-9A-F]{3}\b|0x23[0-9A-F]{2}\b", outs[0][0])


def test_indexed_stores_clear_operand_cells(prefix, tmp_path):
    c, _, info = check(prefix, tmp_path, CLEAR, entry_calls([0, 0, 1, 0, 2, 0, 1, 0]))
    assert "codechk" not in c and not info["traps"]
    assert (info["stats"]["cells"], info["stats"]["code_guards"]) == (3, 0)
    # The cells lie in three regions slicing one span: a store at a constant base plus an
    # index indexes the span, with no region tests and no write accessor.
    body = c[c.index("void entry") :]
    assert re.search(r"\bC0\[v\d+\] = 0;", body) and "_LEN" not in body and "wr(" not in c


ZPPTR = """
init:   LDA #lo(song)
        STA $FB
        LDA #hi(song)
        STA $FC
        RTS
play:   INC $FD
        TAX
        BEQ read
        LDA #$00
        STA $FC
        LDA #$FD
        STA $FB
read:   LDY #$00
        LDA ($FB),Y
        STA $C010
        RTS
song:   .byte 1,2,3
end:
"""


@pytest.mark.parametrize(
    "src,org,span",
    [(ZPPTR, 0x1000, True), (ZPPTR.replace("song:", ".org $C100\nsong:"), 0xBF00, False)],
    ids=["program", "io"],
)
def test_pointer_read_reaching_zero_page_reads_its_live_state(
    prefix, tmp_path, src, org, span
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """A pointer into the song or at a zero-page byte play increments reads through the song
    span's reader: the zero-page byte, outside it, is ``oob_rd``'s, the byte's value now. A
    pointer whose bytes in the program meet the I/O pages has no span and reads ``rd``."""
    img, labels, loaded = assemble(src, org)
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    calls = [("init", 0), ("play", 0), ("play", 0), ("play", 1), ("play", 0), ("play", 2)]
    play(tmp_path, c, h, img, labels, calls)
    body = c[c.index("void play") :]
    assert bool(re.search(r"rdC\d+\(", body)) == span != bool(re.search(r"\brd\(", body))
    assert not info["traps"]


def test_read_outside_the_program_traps_by_default(prefix, tmp_path):
    """Without ``CGEN_OOB_EXACT`` the span reader's read outside the program traps with
    ``OOB_TRAP``; reads inside it run as before."""
    img, labels, loaded = assemble(ZPPTR)
    c, h, _ = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    flags = [f for f in cgenplay.CFLAGS if f != "-DCGEN_OOB_EXACT"]
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so", flags)
    lib.call.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
    fn = {n: ctypes.cast(getattr(lib, n), ctypes.c_void_p) for n in ("init", "play")}
    got = [lib.call(fn[n], a) for n, a in [("init", 0), ("play", 0), ("play", 1)]]
    assert got[:2] == [-1, -1] and got[2] == cgen.OOB_TRAP


ANYPTR = """
entry:  STA $FC
        LDA #$00
        STA $FB
        LDA #$5A
        STA $2000
        LDY #$00
        LDA ($FB),Y
        STA $C000
        RTS
end:
"""


def test_pointer_read_that_may_reach_a_zone_reads_memory(prefix, tmp_path):
    """A read through a pointer whose high byte is any value is not checked against page 1
    and the I/O pages: with no data byte in the program to hold, it reads memory through
    ``rd``, the I/O pages' ``io_read`` and zero page's live bytes among it, no deopt."""
    calls = [("entry", a) for a in (0x20, 0xC0, 0, 0x20)]
    c, _, info = check(prefix, tmp_path, ANYPTR, calls)
    assert not info["traps"] and info["stats"]["zone_guards"] == 0
    assert "rd((uint16_t)a << 8)" in c


TWOEXITS = """
entry:  TAX
        LDY #$00
loop:   LDA tab,X
        BEQ done
        LDY tab+1,X
        INX
        INX
        BNE loop
done:   STY $C000
        RTS
tab:    .byte 1,2,3,4,0,0
end:
"""


def test_loop_exits_through_copies_break_to_their_follow(prefix, tmp_path):
    """A loop's two exits to one follow, each copying another value of a register into it,
    are its copies then ``break``: no goto."""
    c, _, info = check(prefix, tmp_path, TWOEXITS, entry_calls([0, 2, 4, 1]))
    assert info["structure"]["gotos"] == 0 and c.count("break;") == 2


@pytest.mark.parametrize("gap_data", [False, True])
def test_held_spans_join_only_across_data(gap_data):
    """Two runs of needed constants are one held span only when the bytes between them are
    data region bytes (its slices); a constant between them is not held."""
    code = np.zeros(0x10000, dtype=bool)
    code[0x1000:0x1010] = True
    accesses = [(0x1000, 0x1003), (0x1008, 0x100A)]
    if gap_data:
        code[0x1004:0x1008] = False
        accesses.append((0x1004, 0x1007))
    mem = regions.Memory(bytes(0x10000), code, [W.IO], (0x1000, 0x1010))
    mem.partition(accesses, [], unknown_store=False)
    want = [(0x1000, 0x100B)] if gap_data else [(0x1000, 0x1004), (0x1008, 0x100B)]
    assert mem.held == want
    assert mem.regions == ([(0x1004, 4)] if gap_data else [])


@pytest.mark.parametrize(
    "written,want",
    [
        ((), (0x1008, 0x100F)),
        (((0x1100, 0x1180),), (0x1008, 0x117F)),
        (((0xC010, 0xC020),), (0x1008, 0x100F)),
    ],
    ids=["program", "copy", "io"],
)
def test_pointer_span_reaches_a_block_copy_past_the_program(written, want):
    """A pointer's span runs from its hull's first data byte in the program to its last, and on
    to the end of what a block copy writes past the program, below the volatile ranges: none
    when the program holds no data byte of it."""
    code = np.zeros(0x10000, dtype=bool)
    code[0x1000:0x1008] = True
    mem = regions.Memory(bytes(0x10000), code, [W.IO], (0x1000, 0x1010), written=written)
    assert mem.pointer_run((0x1000, 0xFFFF)) == want == mem.pointer_run((0, 0xFFFF))
    assert mem.pointer_run((0, 0xFFF)) is None and mem.pointer_run((0, 0x1007)) is None
    code[0x1008:0x1010] = True
    mem = regions.Memory(bytes(0x10000), code, [W.IO], (0x1000, 0x1010), written=written)
    assert mem.pointer_run((0, 0xFFFF)) is None


def test_bounded_load_reads_its_region_in_the_image():
    """A load whose bytes in the image lie in one region reads it, any other byte (a region
    below the image) through ``oob_rd``; with two regions in the image it has no one array."""
    code = np.zeros(0x10000, dtype=bool)
    code[0x1008:0x1010] = True
    mem = regions.Memory(bytes(0x10000), code, [W.IO], (0x1000, 0x1010))
    mem.partition([(0xFF0, 0xFF7), (0x1000, 0x1003), (0x1005, 0x1007)], [], unknown_store=False)
    assert mem.regions == [(0xFF0, 8), (0x1000, 4), (0x1005, 3)]
    assert mem.bounded(0xFF0, 0x1003) == ((1,), ()) and mem.bounded(0xFF0, 0xFFF) == ((0,), ())
    assert mem.bounded(0xFF0, 0x1007) is None and mem.bounded(0x1003, 0x1006) is None


def test_pointer_span_meeting_a_volatile_range_in_the_program():
    """A hull whose part inside the program meets a volatile range is no span: its in-image
    addresses are not all one array's."""
    mem = regions.Memory(bytes(0x10000), np.zeros(0x10000, dtype=bool), [W.IO], (0xBF00, 0xC100))
    assert mem.pointer_run((0, 0xBFFF)) == (0xBF00, 0xBFFF) and mem.pointer_run((0, 0xFFFF)) is None


def test_indexed_store_of_known_opcodes(prefix, tmp_path):
    c, _, info = check(prefix, tmp_path, FLIP, entry_calls([2, 1, 2, 0, 2, 3, 1, 2]))
    assert "codechk" not in c and not info["traps"]
    assert (info["stats"]["opcode_cells"], info["stats"]["code_guards"]) == (2, 0)


def test_untaken_path_and_data_invariance(prefix, tmp_path):
    img_a, labels, loaded = assemble(UNTAKEN)
    img_b, _, _ = assemble(UNTAKEN, patch={"tab": [1, 0x80, 3, 0xFF]})
    c_a, h_a, _ = translate(prefix, img_a, labels, loaded)
    c_b, h_b, _ = translate(prefix, img_b, labels, loaded)
    assert c_a == c_b and h_a != h_b
    assert "0xFF" in c_a
    play(tmp_path, c_a, h_b, img_b, labels, entry_calls([0], 3))


def test_relocation_invariance(prefix, tmp_path):
    outs = []
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(RELOC, org)
        c, h, _ = translate(prefix, img, labels, loaded)
        play(tmp_path, c, h, img, labels, entry_calls(range(4), 2))
        outs.append((c, h))
    assert outs[0][0] == outs[1][0] and outs[0][1] != outs[1][1]
    assert not re.search(r"0x1[0-9A-F]{3}\b|0x23[0-9A-F]{2}\b", outs[0][0])


def test_sensitive_reads_are_kept(prefix, tmp_path):
    img, labels, loaded = assemble(SENS)
    calls = entry_calls([0x15, 0x02, 0x20, 0x15])
    plain, _, _ = translate(prefix, img, labels, loaded)
    c, h, _ = translate(prefix, img, labels, loaded, sensitive=[0xC005, 0xC000])
    assert plain.count("io_read") < c.count("io_read")
    play(tmp_path, c, h, img, labels, calls, [0xC005, 0xC000])


def test_mos_build(prefix, tmp_path_factory):
    if not shutil.which("docker"):
        pytest.skip("needs Docker")
    with locked(tmp_path_factory):
        if subprocess.run(
            ["docker", "pull", "-q", MOS], capture_output=True, check=False
        ).returncode:
            pytest.skip("llvm-mos image unavailable")
    img, labels, loaded = assemble(RELOC)
    c, h, _ = translate(prefix, img, labels, loaded)
    out = Path(__file__).resolve().parents[1] / "out" / "cgen-mos"
    out.mkdir(parents=True, exist_ok=True)
    (out / "t.c").write_text(c)
    (out / "t.h").write_text(h)
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{out}:/w",
        "-w",
        "/w",
        MOS,
        "mos-c64-clang",
        "-std=c99",
        "-Os",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-c",
        "t.c",
        "-o",
        "t.o",
    ]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def compiled(tmp_path, c, h):
    """The library of ``c``/``h`` with the harness."""
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    return cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")


@pytest.mark.parametrize("name", sorted(OPS) + sorted(UNARY))
def test_op_semantics(prefix, tmp_path, name):
    """Every P-Code op through MLIR and the printer, on edge values, against its meaning."""
    if name in OPS:
        sizes, out, meaning = OPS[name]
        fn = op_function(name.removesuffix("16"), sizes, out)
    else:
        size, out, extra, unary = UNARY[name]
        sizes, fn = (size, 1), unary_function(name, size, out, extra)
        meaning = lambda a, b, s, f=unary: f(a)  # noqa: E731
    p = cgen.Program([fn], [(0x1000, "entry")], bytes(0x10000), set(), [], loaded=(0x10, 0x22))
    c, h, info = cgen.render(p, header="t.h", prefix=prefix)
    lib = compiled(tmp_path, c, h)
    cells = {}
    for k, (org, n) in enumerate(info["regions"]):
        arr = (ctypes.c_uint8 * n).in_dll(lib, f"R{k}")
        cells.update({org + i: (arr, i) for i in range(n)})
    for a in VALUES[sizes[0]]:
        for b in VALUES[sizes[1]] if name in OPS else [0]:
            for at, v, s in ((0x10, a, sizes[0]), (0x14, b, sizes[1])):
                for i, byte in enumerate(v.to_bytes(s, "little")):
                    if at + i in cells:
                        arr, j = cells[at + i]
                        arr[j] = byte
            lib.call(ctypes.cast(lib.entry, ctypes.c_void_p), 0)
            got = sum(cells[0x20 + i][0][cells[0x20 + i][1]] << 8 * i for i in range(out))
            assert got == meaning(a, b, sizes[0]) & (1 << 8 * out) - 1, (name, a, b)


def test_cli_writes_c_and_header(prefix, tmp_path):
    # pylint: disable=import-outside-toplevel
    from deity_informant.cgen.__main__ import main

    img, labels = W.assemble(W.JUMPTAB)
    pc = labels["entry"]
    facts = trace.collect(img, lambda s: [s.call(pc, a) for a in range(4)])
    (tmp_path / "facts.json").write_text(facts.dumps())
    (tmp_path / "prog.bin").write_bytes(bytes(img[0x1000:0x1100]))
    args = ["--org", "0x1000", "--entry", f"{pc}:entry", "--volatile", "0xC000-0xC100"]
    assert prefix is not None
    out = tmp_path / "prog.c"
    main(
        [str(tmp_path / "prog.bin"), *args, "--facts", str(tmp_path / "facts.json"), "-o", str(out)]
    )
    c, h = out.read_text(), (tmp_path / "prog.h").read_text()
    assert '#include "prog.h"' in c and "case CODE_ORG + " in c
    play(tmp_path, c, h, img, labels, entry_calls(range(4)))


def test_jump_facts_keep_computed_transfers_only():
    img, labels = W.assemble(W.JUMPTAB)
    pc = labels["entry"]
    facts = trace.collect(img, lambda s: [s.call(pc, a) for a in range(4)])
    kept = cgen.jump_facts(facts.to_dict(), bytes(img)).edges
    assert kept and all(img[src] in (0x60, 0x6C, 0x40) for src, _ in kept)
    assert len(kept) < len(facts.edges)


def test_parse_rejects_unknown_syntax():
    with pytest.raises(AssertionError):
        mlir.parse('"builtin.module"() ({\n  this is not mlir\n}) : () -> ()\n')


@pytest.mark.parametrize("src,places", [(TAIL2, 2), (TAIL3, 3)], ids=["two", "three"])
def test_shared_tail_stays_in_its_function(prefix, tmp_path, src, places):
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(4), 2))
    assert info["structure"]["functions"] == 1 and info["structure"]["gotos"] == 0
    assert c.count("io_write(0xC000") == 1
    assert c.count("entry") == 1 and places > 1


def test_code_past_a_shared_tail_stays_in_it(prefix, tmp_path):
    """Code two procedures share past the shared tail they enter stays in its function, also
    where it returns to the entry height after a push and a pull."""
    img, labels, _ = assemble(SHARED)
    _, _, info = check(prefix, tmp_path, SHARED, entry_calls(range(4), 2))
    entries = {f["entry"] for f in info["provenance"]["functions"].values()}
    assert labels["shared"] in entries and labels["join"] not in entries
    assert labels["join"] - 3 not in entries


def test_multi_exit_loop_breaks_to_its_follow(prefix, tmp_path):
    # The inner loop's two edges to `none` break to its follow; its exit to `found` is the
    # goto; `none`'s jump past `found` to the latch `next` continues the outer do/while.
    c, _, _ = check(prefix, tmp_path, EXITS, entry_calls(range(4), 2), gotos=1)
    assert c.count("break;") == 2 and "continue;" in c and "} while (" in c


def test_two_entry_loop_jumps_to_its_second_entry(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, IRRED, entry_calls(range(4), 2), gotos=2)
    assert "for (" not in c


def test_switch_from_a_compare_chain(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, CHAIN, entry_calls([0, 1, 2, 3, 5, 9, 0x81]))
    assert "switch (a)" in c and all(f"case {k}:" in c for k in (1, 2, 5))


def test_switch_default_keeps_its_tests_operands(prefix, tmp_path):
    """A test in a compare chain's default arm whose operand is read from memory after an
    effect keeps the read: the chain's switch drops only the quiet blocks it absorbs."""
    calls = entry_calls([0, 2, 0, 1, 0, 2, 0, 5, 9, 1, 0])
    c, _, _ = check(prefix, tmp_path, CHAIN_TAIL, calls)
    assert "switch (" in c and "default:" in c


def test_switch_from_a_jump_table(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, LOHI, entry_calls(range(4)))
    assert "switch (" in c and "case CODE_ORG" not in c and "default:" in c
    assert [int(k) for k in re.findall(r"case (\d+):", c)] == [0, 1, 2]


@pytest.mark.parametrize(
    "edit",
    [
        ("site:   JMP j0", "        LDY #0\nsite:   JMP j0"),
        ("        LDA thi,Y", "        INY\n        LDA thi,Y"),
    ],
    ids=["index-overwritten", "columns-apart"],
)
def test_switch_on_the_target_without_a_live_index(prefix, tmp_path, edit):
    """A jump whose target bytes no live register indexes as one table switches on the
    target address."""
    c, _, info = check(prefix, tmp_path, OPJMP.replace(*edit), entry_calls([0, 1, 2, 2, 0]))
    assert "case CODE_ORG" in c and not re.search(r"case \d+:", c)
    assert len(next(iter(info["jumps"].values()))) >= 3


def test_callee_called_twice_keeps_its_used_parameters(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, SUBS, entry_calls(range(8)))
    [name] = re.findall(r"static \w+ (f\d+)\(uint8_t v1\)", c)
    # Its definition, the two `JSR`s and the tail call `twice` falls through to; `twice`, with
    # one call site, is inlined.
    assert c.count(f"{name}(") == 5


def test_indexed_addresses_wrap(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, WRAP, entry_calls(range(0, 256, 15)))
    assert not re.search(r"\[0xFF[0-9A-F]{2}", c)


def _shuffled(parse, seed):
    def run(text):
        top = parse(text)
        rng = random.Random(seed)
        for f in top.regions[0][0].ops:
            for region in f.regions:
                rest = region[1:]
                rng.shuffle(rest)
                region[1:] = rest
        return top

    return run


@pytest.mark.parametrize("name", ["EXITS", "IRRED", "TAIL3", "SUBS", "VOICES"])
def test_deterministic(prefix, monkeypatch, name):
    img, labels, loaded = assemble(globals()[name])
    texts = {translate(prefix, img, labels, loaded)[0] for _ in range(2)}
    parse = mlir.parse
    for seed in range(3):
        monkeypatch.setattr(cgen.mlir, "parse", _shuffled(parse, seed))
        texts.add(translate(prefix, img, labels, loaded)[0])
    monkeypatch.setattr(cgen.mlir, "parse", parse)
    code = (
        "import sys; sys.path[:0] = sys.argv[1:]; import test_cgen as T; "
        f"img, labels, loaded = T.assemble(T.{name}); "
        "sys.stdout.write(T.translate(None, img, labels, loaded)[0])"
    )
    for seed in ("0", "1"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        here = str(Path(__file__).parent)
        done = subprocess.run(
            [sys.executable, "-c", code, here, str(Path(here).parent / "tools")],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        texts.add(done.stdout)
    assert len(texts) == 1


def test_conditions_cover_and_definite_assignment():
    conds = structure.Conds()
    a, b = conds.var(("c", "%a")), conds.var(("c", "%b"))
    assert conds.cover((a & b) | (a & ~b), conds.bdd.true) == [[(("c", "%a"), True)]]
    one, two = conds.var(("e", "%x", 1)), conds.var(("e", "%x", 2))
    assert conds.cover(~one & two, conds.care) == [[(("e", "%x", 2), True)]]
    conds.close()
    assign = ("a", "x", "1", frozenset())
    read = ("s", "f(x);", frozenset({"x"}))
    assert csyntax.assigned_first([("if", "c", frozenset(), [assign], [assign]), read], "x")
    assert not csyntax.assigned_first([("if", "c", frozenset(), [assign], []), read], "x")
    jump = ("j", "return;", frozenset())
    assert csyntax.assigned_first([("if", "c", frozenset(), [assign], [jump]), read], "x")
    label = ("l", "L1:;", frozenset())
    assert not csyntax.assigned_first([assign, ("j", "goto L1;", frozenset()), label, read], "x")
    body = [("if", "c", frozenset(), [assign], []), read]
    assert csyntax.render(csyntax.declare(body, {"x": "uint8_t"}))[0] == "uint8_t x = 0;"


def test_clone_metrics():
    body = "  v1 = R0[3];\n  io_write(0xD400, v1);\n  v2 = v1 + 1;\n  R0[4] = v2;\n"
    text = (
        f"static void f1(void) {{\n{body}}}\nstatic void f2(void) {{\n{body.replace('3', '7')}}}\n"
    )
    total, dup = cclones.cloned(text)
    assert total == len(text) and dup == total - len("}\n") * 2
    assert cclones.pairs(text * 3, least=0.9) == []
    assert cclones.main([__file__]) == 0


def _voice_writes(c):
    """The ``io_write`` sites of ``c`` with a constant voice register address."""
    return re.findall(r"io_write\(0xC0(?:07|0E)\b", c)


def test_unrolled_copies_fold_into_one_function(prefix, tmp_path):
    outs = []
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(VOICES, org)
        c, h, info = translate(prefix, img, labels, loaded)
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, entry_calls(range(4), 6))
        outs.append(c)
    c = outs[0]
    assert outs[0] == outs[1] and not re.search(r"0x1[0-9A-F]{3}\b|0x23[0-9A-F]{2}\b", c)
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    assert not _voice_writes(c) and c.count("io_write(0xC000") == 1
    [(name, param)] = re.findall(r"static \w+ (f\d+)\(uint8_t (v\d+)\)", c)
    [tab] = re.findall(rf"io_write\(0xC000 \+ (K\d+)\[{param}\],", c)
    assert f"static const uint8_t {tab}[] = {{0, 7, 0xE}};" in c and " * " not in c
    assert len(re.findall(rf"\b{name}\([0-2]\)", c)) == 3


def test_copies_operand_cells_indexed_directly(prefix, tmp_path):
    """Each copy's own operand cell lies in one held span: the copies index it at their
    stride, read once per call from a constant table of 8-bit offsets (no multiplication),
    with no ``rd``/``wr``."""
    c, _, _ = check(prefix, tmp_path, VOICES, entry_calls(range(4), 6))
    assert not re.search(r"\b(?:rd|wr)\(", c)
    [(off, tab)] = re.findall(r"uint8_t (v\d+) = (K\d+)\[v1\];", c)
    assert f"static const uint8_t {tab}[] = {{0, 0x18, 0x30}};" in c
    assert len(re.findall(rf"C\d+\[{off}\]", c)) == 3


def test_copies_at_uneven_distances_index_a_table(prefix, tmp_path):
    """Copies whose cells are not at one stride index their span by a constant table of
    the offsets, the same at another origin."""
    src = VOICES.replace("JMP v1\n", "JMP v1\n        .byte 0, 0, 0\n")
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(4), 6))
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    assert "static const uint8_t K0[] = {0, 0x1B, 0x33};" in c
    assert not re.search(r"\b(?:rd|wr)\(|==", c)
    img, labels = W.assemble(src, 0x2345)
    assert translate(prefix, img, labels, (0x2345, labels["end"]))[0] == c


def test_per_copy_constants_index_a_table(prefix, tmp_path):
    """Per-copy immediates not affine in the copy are a constant table indexed by it."""
    c, _, _ = check(prefix, tmp_path, CONSTS, entry_calls(range(4), 4))
    assert "static const uint8_t K0[] = {5, 1, 9};" in c and "==" not in c


def test_registers_a_callee_leaves_are_its_arguments(prefix, tmp_path):
    """A result a callee returns unchanged from a parameter is the argument its call passes,
    so the callee returning one register returns it plainly, with no ``rv`` variable, and a
    callee left with no effect is not called."""
    c, _, _ = check(prefix, tmp_path, KEEPS, entry_calls(range(4), 3))
    assert (
        "rv" not in c
        and "f2" not in c
        and len(re.findall(r"static uint8_t f1\(uint8_t v1\)", c)) == 1
    )
    assert re.search(r"uint8_t (v\d+) = f1\(v1\);\n *io_write\(0xC000, \1\);", c)


OPERAND_PTR = r"rdC\d+\(R\d+\[0\] \| \(\(uint16_t\)R\d+\[1\] << 8\), "


@pytest.mark.parametrize("src", ["RUNREL", "RELOCATE"])
def test_operands_init_writes_through_a_pointer_are_pointer_variables(prefix, tmp_path, src):
    """A relocator storing each operand's two bytes through a ``(zp),Y`` pointer read from a
    table of patch sites is code, run on every call: both bytes of each operand are cells
    (the stores' address sets are the sites, the second store's found once the first is
    known to write only its sites), and play reads through each operand as a pointer
    variable."""
    img, labels, loaded = assemble(globals()[src])
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    calls = [("init", 0)] + [("play", a) for a in range(4)] * 2 + [("init", 0), ("play", 1)]
    play(tmp_path, c, h, img, labels, calls)
    ops = {labels[o] + k for o in ("o0", "o1") for k in (1, 2)}
    assert ops <= set(info["provenance"]["cells"])
    init, body = c[c.index("void init") : c.index("void play")], c[c.index("void play") :]
    assert "for (" in init and "codechk(" not in init
    assert re.search(OPERAND_PTR, body)


def test_carry_chain_identity():
    """``lo = a + b``, ``hi = d + e + (lo < a)`` are the bytes of ``(d:a) + (e:b)``, for every
    ``a``, ``b`` and every value of ``d + e``."""
    a, b, de = np.meshgrid(*(np.arange(256),) * 3, indexing="ij")
    lo = (a + b) & 0xFF
    hi = (de + (lo < a)) & 0xFF
    w = ((de << 8) + a + b) & 0xFFFF
    assert (lo == (w & 0xFF)).all() and (hi == (w >> 8)).all()


def test_carry_chain_is_one_16_bit_add(prefix, tmp_path):
    """An `ADC` whose carry only feeds the `ADC` of the byte above prints as one 16-bit add."""
    c, _, _ = check(prefix, tmp_path, ADD16, entry_calls(range(0, 16, 3), 40))
    assert "<< 8" in c and not re.search(r"\(v\d+ < v\d+\)", c)


def songs(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    prefix, tmp_path, src, values, init=0, declared=None
):
    """``(c, info, labels)`` of ``src``'s ``init`` and ``play`` translated with ``init``
    (called with the ``declared`` songs), replayed from a fresh image per song ``a`` of
    ``values``: ``init(a)``, then plays."""
    img, labels, loaded = assemble(src)
    c, h, info = translate(
        prefix, img, labels, loaded, entries=("init", "play"), init=init, songs=declared
    )
    for a in values:
        (tmp_path / str(a)).mkdir(parents=True)
        play(
            tmp_path / str(a),
            c,
            h,
            img,
            labels,
            [("init", a)] + [("play", k) for k in range(4)] * 2,
        )
    return c, info, labels


def test_operand_stores_through_a_written_site_table_are_checked(prefix, tmp_path):
    """A relocator whose table of patch sites play writes has no address sets for its
    stores: they write through ``wr`` and check constant code (trapping were one to change
    it), and the operands they would patch stay constant code."""
    src = RUNREL.replace(
        "        STA $C001\n        RTS", "        STA $C001\n        INC ptrs\n        RTS"
    )
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", a) for a in range(4)])
    init = c[c.index("void init") : c.index("void play")]
    assert not info["provenance"]["cells"] and init.count("codechk(") == 2


def test_init_runs_as_code_for_every_song(prefix, tmp_path):
    """An init the host runs once, first, relocating play's operands from a table play also
    writes is lifted as code: the relocator is a loop of the init function, the song it
    stores a store of ``A``, and play reads through the operand it patches as a pointer
    variable. Play's store through the other patched operand, whose values the analysis
    does not bound, writes through ``wr`` and checks constant code."""
    c, info, labels = songs(prefix, tmp_path, INITREL, (0, 1, 5))
    ops = {labels[o] + k for o in ("o0", "o1") for k in (1, 2)}
    assert ops <= set(info["provenance"]["cells"]) and "init" not in info
    init, body = c[c.index("void init") : c.index("void play")], c[c.index("void play") :]
    assert "for (" in init and re.search(r"R\d+\[0\] = a;", init)
    assert re.search(OPERAND_PTR, body) and "wr(" in body and "codechk(" in body


def test_init_relocating_by_the_song_leaves_pointer_cells(prefix, tmp_path):
    """Init computing an operand from the song writes both its bytes: play reads through
    them as a pointer, bounded to the table the song indexes."""
    c, info, labels = songs(prefix, tmp_path, INITA, range(4))
    assert {labels["o0"] + 1, labels["o0"] + 2} <= set(info["provenance"]["cells"])
    assert re.search(r"rdC0\(R\d+\[0\] \| \(\(uint16_t\)R\d+\[1\] << 8\), a & 3\)", c)


def test_song_range_is_checked_not_assumed(prefix, tmp_path):
    """The songs the host calls init with are checked at init, not assumed: init's run is
    taken for every song, a table entry pointing at I/O among them, so the read through the
    pointer it leaves is bounds-checked, the ``.c`` is the same for every song count, and a
    song outside the range traps."""
    c, _, labels = songs(prefix, tmp_path, SONGINIT, (0, 1), declared=range(2))
    body = c[c.index("void play") :]
    assert "rdC0(" in body
    guard = r"if \(\(uint8_t\)\(a - CGEN_SONG0\) >= CGEN_SONGS\) \{\s*trap\("
    assert re.search(guard, c)
    img, labels, loaded = assemble(SONGINIT)
    other = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=range(4))
    assert other[0] == c and "#define CGEN_SONGS 0x04" in other[1]
    with pytest.raises(AssertionError, match="trap"):
        songs(prefix, tmp_path / "out", SONGINIT, (2,), declared=range(2))


def test_song_values_are_a_range(prefix):
    """The songs the host calls init with are a range of values, its bounds in the header."""
    img, labels, loaded = assemble(SONGINIT)
    with pytest.raises(ValueError, match="range"):
        translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=(0, 2))
    _, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=range(1, 3))
    assert "#define CGEN_SONG0 0x01" in h and "#define CGEN_SONGS 0x02" in h


@pytest.mark.parametrize(
    "order",
    [[1, 2, 3, 4, 5, 6, 7, 8], [0x81, 2, 0x90, 0xA0, 5, 6, 7, 8], [0x81] * 7 + [3]],
    ids=["plain", "commands", "skips"],
)
def test_init_reading_song_data_is_code(prefix, tmp_path, order):
    """Init reads each voice's first order byte with the routine play reads them with (song
    data), skipping commands: lifted as code, its loop is the same for every song, so the
    ``.c`` is the same for every order list and song count, the order positions it leaves
    the song's run."""
    img, labels, loaded = assemble(SONGDATA, patch={"order": order})
    texts = set()
    for count in (1, 2):
        c, h, _ = translate(
            prefix, img, labels, loaded, ("init", "play"), init=0, songs=range(count)
        )
        (tmp_path / str(count)).mkdir()
        play(tmp_path / str(count), c, h, img, labels, [("init", 0)] + [("play", 0)] * 9)
        texts.add(c)
    base = translate(prefix, *assemble(SONGDATA), ("init", "play"), init=0, songs=range(1))[0]
    assert texts == {base} and "for (" in base[base.index("void init") : base.index("void play")]


def test_init_io_writes_song_data_decides_are_its_code(prefix, tmp_path):
    """An I/O write only some songs make is a write of init's code, under the branch the song
    byte decides: the same ``.c`` for every song."""
    texts = set()
    for first in (1, 0x81):
        img, labels, loaded = assemble(SONGIO, patch={"order": [first]})
        c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
        init = c[c.index("void init") : c.index("void play")]
        assert "io_write(0xC018, 0xF)" in init and "io_write(0xC010," in init
        (tmp_path / str(first)).mkdir()
        calls = [("init", 0)] + [("play", k) for k in range(4)]
        play(tmp_path / str(first), c, h, img, labels, calls)
        texts.add(c)
    assert len(texts) == 1


def test_init_io_writes_stay_in_order_in_its_code(prefix, tmp_path):
    """Init's I/O writes are its code's, in order (the clearing loop a loop), and the bytes it
    stores are data play reads."""
    c, _, labels = songs(prefix, tmp_path, IOCLEAR, (0,), declared=(0,))
    init = c[c.index("void init") : c.index("void play")]
    assert "for (" in init and "io_write(0xC010 + " in init and "io_write(0xC020, 0x11)" in init
    assert not re.search(r"io_write\(0xC00[01], CODE_K", c)


PROBE = """
init:   LDA $C030
        AND #$01
        BEQ keep
        LDA #$0A
        STA shift
keep:   RTS
play:   AND #$07
shift:  NOP
        STA $C000
        RTS
end:
"""

BLOCKCOPY = """
init:   TAX
        LDA srclo,X
        STA $FB
        LDA srchi,X
        STA $FC
        LDY #$03
copy:   LDA ($FB),Y
        STA buf,Y
        DEY
        BPL copy
        RTS
play:   AND #$03
        TAX
        LDA buf,X
        STA $C000
        RTS
srclo:  .byte lo(s0), lo(s1)
srchi:  .byte hi(s0), hi(s1)
s0:     .byte 1, 2, 3, 4
s1:     .byte 5, 6, 7, 8
buf:    .byte 0, 0, 0, 0
end:
"""


def test_init_io_probe_stays_runtime_code(prefix, tmp_path):
    """Init probing the hardware (an I/O read) and patching play's opcode by what it reads
    stays code run at init: the read is an ``io_read`` of the init function, and play
    dispatches on the opcode cell's two values. Nothing of it is decided at lift time."""
    img, labels, loaded = assemble(PROBE)
    c, h, info = translate(
        prefix, img, labels, loaded, ("init", "play"), sensitive=(0xC030,), init=0
    )
    calls = [("init", 0)] + [("play", k) for k in range(4)]
    play(tmp_path, c, h, img, labels, calls, (0xC030,))
    init, body = c[c.index("void init") : c.index("void play")], c[c.index("void play") :]
    assert "io_read(0xC030)" in init and labels["shift"] in info["provenance"]["cells"]
    assert "R0[0] = 0xA;" in init and "trap(" not in body


def test_init_block_copy_is_a_runtime_copy(prefix, tmp_path):
    """Init copying the song's block from the table the song indexes is a copy loop of the
    init function run for that song, its source a bounded read through the pointer: the same
    ``.c`` for other song data, which the header holds."""
    texts = set()
    for data in ([1, 2, 3, 4], [9, 8, 7, 6]):
        img, labels, loaded = assemble(BLOCKCOPY, patch={"s0": data})
        c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
        for song in (0, 1):
            (tmp_path / f"{data[0]}-{song}").mkdir()
            calls = [("init", song)] + [("play", k) for k in range(4)]
            play(tmp_path / f"{data[0]}-{song}", c, h, img, labels, calls)
        texts.add(c)
    [c] = texts
    init = c[c.index("void init") : c.index("void play")]
    assert "for (" in init and re.search(r"(rdC0|CGEN_IX)\(", init) and not re.search(r"\brd\(", c)


OVERCOPY = """
init:   CMP #$02
        BCS eff
        LDX #$03
c:      LDA blk,X
        STA init,X
        DEX
        BPL c
        RTS
eff:    LDA #$0F
        STA $C018
        RTS
play:   AND #$03
        TAX
        LDA init,X
        STA $C000
        RTS
blk:    .byte 1, 2, 3, 4
end:
"""


@pytest.mark.parametrize("rerun", [False, True])
def test_code_init_overwrites_after_running_it_is_data(prefix, tmp_path, rerun):
    """Init's code a copy overwrites after init has run it (play never runs it) is data: no
    instruction runs at those bytes after the copy, so the copy writes data play reads and
    init's code runs with its entry bytes, with no code check or opcode dispatch. Where play
    runs that code too, its bytes are cells."""
    src = (
        OVERCOPY.replace("play:   AND #$03", "play:   JSR init\n        AND #$03")
        if rerun
        else OVERCOPY
    )
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    for song in (0, 3):
        (tmp_path / str(song)).mkdir()
        calls = [("init", song)] + [("play", k) for k in range(4)]
        play(tmp_path / str(song), c, h, img, labels, calls)
    cells = set(info["provenance"]["cells"])
    init = c[c.index("void init") : c.index("void play")]
    assert (labels["init"] in cells) == rerun
    if not rerun:
        assert "codechk(" not in c and not re.search(r"if \(\w+\[[^]]*\] == 0x", init)


SWPREL = """
init:   LDA #lo(song)
        STA $FD
        LDA #hi(song)
        STA $FE
        LDX #$00
r:      LDA sites+1,X
        BEQ done
        STA $FC
        LDA sites,X
        STA $FB
        LDY offs,X
        LDA ($FD),Y
        CLC
        ADC $FD
        LDY #$01
        STA ($FB),Y
        LDY offs,X
        INY
        LDA ($FD),Y
        ADC $FE
        LDY #$02
        STA ($FB),Y
        INX
        INX
        BNE r
done:   RTS
play:   AND #$03
        TAY
o0:     LDA $0000,Y
        STA $C000
o1:     LDA $0000,Y
        STA $C001
        RTS
sites:  .byte lo(o0), hi(o0), lo(o1), hi(o1), 0, 0
offs:   .byte 0, 0, 2, 0
song:   .byte 4, 0, 8, 0
        .byte 1, 2, 3, 4, 5, 6, 7, 8
end:
"""


@pytest.mark.parametrize("plain", [False, True])
def test_relocated_operands_point_into_the_song(prefix, tmp_path, plain):
    """Init relocating play's operands from the song's header words plus the song's base, through
    a table of patch sites, leaves pointer variables bounded to the song: play reads through
    them with the song span's bounds-checked reader, not ``rd``. An operand play also writes
    with a byte of no pointer stays a pointer variable of unbounded values, read through the
    same reader, ``oob_rd`` outside the song."""
    src = (
        SWPREL.replace("        STA $C001\n", "        STA $C001\n        INC o1+1\n")
        if plain
        else SWPREL
    )
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    calls = [("init", 0)] + [("play", k) for k in range(4)]
    play(tmp_path, c, h, img, labels, calls)
    ops = {labels[o] + k for o in ("o0", "o1") for k in (1, 2)}
    assert ops <= set(info["provenance"]["cells"])
    body = c[c.index("void play") :]
    assert re.search(r"rdC\d+\(", body) and not re.search(r"\brd\(", body)


OPPATCH = """
init:   LSR A
        TAX
        LDA plo,X
        STA op+1
        LDA phi,X
        STA op+2
        LDA #$00
op:     STA $FFFF
        RTS
play:   LDA v0
        STA $C000
        LDA v1
        STA $C001
        RTS
plo:    .byte lo(v0), lo(v1)
phi:    .byte hi(v0), hi(v1)
v0:     .byte 7
v1:     .byte 9
end:
"""


def test_operand_patched_from_table_words_has_their_addresses(prefix, tmp_path):
    """A store through its own operand bytes, each a table byte of one index (an effect init
    patching the voice it names), has the words of the tables as its addresses: a plain store
    choosing among them, with no ``wr`` or code check, and an index guard past the tables."""
    img, labels, loaded = assemble(OPPATCH)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    for song in (0, 2):
        (tmp_path / str(song)).mkdir()
        play(tmp_path / str(song), c, h, img, labels, [("init", song)] + [("play", 0)] * 2)
    init = c[c.index("void init") : c.index("void play")]
    assert " wr(" not in c and "codechk(" not in c and "trap(" in init


@pytest.mark.parametrize("bounded", [True, False])
def test_index_bound_through_a_call_keeps_a_read_inside(prefix, tmp_path, bounded):
    """A song byte stored as a pattern number only below $80 (the branch on the flags the
    callee's load left) indexes a table whose first 128 bytes lie in the image: the read is
    proved inside and reads it unchecked. Stored whatever its sign, the index may reach past
    the image: the read goes through the span's bounds-checked reader."""
    src = PATNUM if bounded else PATNUM.replace("        BMI keep\n", "")
    img, labels, loaded = assemble(src)
    c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", k) for k in range(8)])
    body = c[c.index("void play") :]
    assert (not re.search(r"\b(?:oob_rd|rdC\d+)\(", body)) == bounded
    assert ("rdC0(" in body) != bounded


def test_regions_stop_at_the_loaded_image(prefix, tmp_path):
    """A table read with any index reaches past the image: its region ends with the image,
    and a read past it is out of bounds. A byte past the image a store writes is held, and
    ``oob_rd`` reads it as stored."""
    img, labels, loaded = assemble(PAST)
    c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", a) for a in range(4)])
    assert "oob_rd(" in c[c.index("void play") :]
    assert re.search(r"#define R\d+_LEN 4\b", h)
    src = PAST.replace("init:   RTS", "init:   LDA #$07\n        STA end+2\n        RTS")
    img, labels, loaded = assemble(src)
    c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
    (tmp_path / "w").mkdir()
    play(tmp_path / "w", c, h, img, labels, [("init", 0)] + [("play", a) for a in (0, 6, 7)])
    assert "oob_rd(" in c[c.index("void play") :] and re.search(r"#define R\d+_LEN 1\b", h)


KONST = """
init:   RTS
play:   LDA k
        STA $C000
        RTS
k:      .byte $11
end:
"""


def test_constant_bytes_are_names_in_the_header(prefix, tmp_path):
    """A constant byte read at a fixed address is a name the header defines, with each
    origin's value, in one ``.c``."""
    texts = set()
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(KONST, org)
        c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
        assert re.search(r"io_write\(0xC000, CODE_K[0-9A-F]+\)", c)
        assert re.search(r"#define CODE_K[0-9A-F]+ 0x11", h)
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, [("init", 0), ("play", 0)])
        texts.add(c)
    assert len(texts) == 1


def test_init_stored_vector_is_a_machine_address(prefix, tmp_path):
    """A vector init stores outside the program is a store of init's code, its value an
    address relative to the code, and the addresses beside it (the I/O the handler writes)
    are absolute: the same ``.c`` at every origin."""
    texts = set()
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(INITBRK, org)
        c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
        assert "= (uint8_t)(CODE_ORG + " in c[c.index("void init") : c.index("void play")]
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, [("init", 0)] + [("play", 1)] * 4)
        texts.add(c)
    assert len(texts) == 1


def test_init_program_calls_match_the_reference():
    """The lifted program of an init entry, called on the image, makes the reference's
    accesses and leaves its memory (but the dead stack below SP)."""
    img, labels, loaded = assemble(INITREL)
    pcs = [labels["init"], labels["play"]]
    prog = wholeprog.lift(img, pcs, [W.IO], entry_sp=0xFD, loaded=loaded, init=0)
    assert prog.entries[0] == labels["init"] and not hasattr(prog, "init")
    mem, ref = bytearray(img), bytearray(img)
    for e, a in [(0, 3)] + [(1, k) for k in range(4)] * 2:
        reg = cgenplay.entry_regs(a)
        got = prog.call(mem, e, a=a, reg=reg, log=True)["log"]
        want = wholeprog.reference_call(
            ref, pcs[e], a=a, reg=reg, volatile=[W.IO], log=True, clocked=False
        )
        assert got == want["log"]
        assert mem[0x200:] == ref[0x200:] and mem[:0x100] == ref[:0x100]


def test_cli_lifts_the_named_init(prefix, tmp_path):
    # pylint: disable=import-outside-toplevel
    from deity_informant.cgen.__main__ import main

    img, labels, loaded = assemble(INITREL)
    (tmp_path / "prog.bin").write_bytes(bytes(img[loaded[0] : loaded[1]]))
    ents = [a for n in ("init", "play") for a in ("--entry", f"{labels[n]}:{n}")]
    out = tmp_path / "prog.c"
    assert prefix is not None
    main([str(tmp_path / "prog.bin"), "--org", "0x1000", *ents, "--init", "init", "-o", str(out)])
    c = out.read_text()
    assert "for (" in c[c.index("void init") : c.index("void play")]


def test_fall_through_into_a_routine_is_a_tail_call(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, FALLTHRU, entry_calls(range(4), 4))
    [name] = re.findall(r"static \w+ (f\d+)\(uint8_t v1\)", c)
    assert c.count(f"{name}(") == 4 and not _voice_writes(c)


def test_copies_share_their_trap(prefix, tmp_path):
    img, labels, loaded = assemble(GUARDED)
    c, h, info = translate(prefix, img, labels, loaded)
    assert info["stats"]["instances"] == 1 and not _voice_writes(c)
    [(k, key)] = info["traps"].items()
    assert key[0] == "deopt" and len(key) == 3 and c.count(f"trap({k})") == 1
    play(tmp_path, c, h, img, labels, entry_calls(range(1, 4), 2))
    (tmp_path / "jam").mkdir()
    with pytest.raises(AssertionError, match="trap"):
        play(tmp_path / "jam", c, h, img, labels, [("entry", 0)])


def test_short_circuit_conditions(prefix, tmp_path):
    """Branches sharing a target, each entered only from the last, are one condition: the
    load between them runs before the test, as it has no effect."""
    c, _, info = check(prefix, tmp_path, SHORT, entry_calls(range(4), 3))
    _, labels, _ = assemble(SHORT)
    first = labels["entry"] + 6
    assert info["provenance"]["conditions"] == [
        [first, first + 5],
        [labels["skip"] + 2, labels["skip"] + 6],
    ]
    assert "&&" in c and "||" in c


def test_non_series_parallel_joins_are_gotos(prefix, tmp_path):
    """EXITS: the inner loop's exit to `found` is a branch to a block the outer test also
    reaches (not its loop's follow); `JMP next` into the latch is a ``continue``."""
    _, _, info = check(prefix, tmp_path, EXITS, entry_calls(range(4), 2), gotos=1)
    _, labels, _ = assemble(EXITS)
    assert info["provenance"]["gotos"] == [[labels["inner"] + 8, labels["found"]]]
    assert info["provenance"]["loops"] == [labels["inner"], labels["outer"]]


@pytest.mark.parametrize("name", ["EXITS", "IRRED", "VOICES", "LOHI", "CHAIN", "SHORT", "SUBS"])
def test_provenance_names_instruction_starts(prefix, name):
    """Every address the provenance gives is an instruction start of the lift."""
    img, labels, loaded = assemble(globals()[name])
    p, _ = cgen.lift(img, [(labels["entry"], "entry")], [W.IO], None, loaded)
    _, _, info = translate(prefix, img, labels, loaded)
    pv = info["provenance"]
    pcs = [pc for e in pv["gotos"] for pc in e] + pv["loops"] + pv["switches"]
    pcs += [pc for ch in pv["conditions"] for pc in ch]
    for f in pv["functions"].values():
        pcs += [f["entry"], *f["copies"], *(pc for call in f["calls"] for pc in call)]
    assert pcs and set(pcs) <= p.starts


def test_provenance_of_calls_and_copies(prefix):
    """A folded class lists its copies and the sites calling it; the cells are its operand
    bytes the copies write."""
    img, labels, loaded = assemble(VOICES)
    _, _, info = translate(prefix, img, labels, loaded)
    f = next(f for f in info["provenance"]["functions"].values() if f["index"])
    copies = [labels[f"v{k}"] for k in range(3)]
    assert f["entry"] == copies[0] and f["copies"] == copies
    assert [t for _, t in f["calls"]] == [copies[0]] * 3


def test_instruction_edges():
    """An edge is named by the instruction taking it: a branch, or a JMP the lift threaded."""
    img, labels = W.assemble("a: BEQ b\n   NOP\nj: JMP c\nb: NOP\nc: RTS\n", 0x1000)
    a, j, b, c = (labels[k] for k in "ajbc")
    assert structure_edges(img, a, b) == [a, b]
    assert structure_edges(img, a, c) == [a, b]
    assert structure_edges(img, a + 2, c) == [j, c]
    assert structure_edges(img, a, a + 2) == [a, a + 2]


def structure_edges(img, p, q):
    return regions.edge(img, p, q)


def test_short_circuit_with_effects(prefix, tmp_path):
    """A test entered only from the last, sharing its target, whose block has effects (`DEC`)
    is one condition: the effects run as the literal's comma expression, exactly when the
    machine code runs them."""
    c, _, info = check(prefix, tmp_path, EFFECT, entry_calls(range(4), 3))
    _, labels, _ = assemble(EFFECT)
    assert re.search(r"\|\| \(R\d+\[[^]]*\] = [^,]*, ", c), c
    assert info["provenance"]["conditions"] == [[labels["do"] - 7, labels["do"] - 2]]


def test_cycle_entered_at_two_points_selects_its_entry(prefix, tmp_path):
    """A cycle entered at two nodes by one branch (a self-modified direction) is one loop whose
    first iteration that branch's condition, saved on entry, selects."""
    c, _, info = check(prefix, tmp_path, SWEEP, entry_calls([0, 1, 1, 0, 1], 2))
    _, labels, _ = assemble(SWEEP)
    assert info["provenance"]["loops"] == [labels["down"]] and "for (;;)" in c


def test_switch_arms_break_and_fall_through(prefix, tmp_path):
    """Jump-table handlers jumping to what follows the dispatch break; one running into the
    next handler falls through into its case."""
    c, _, _ = check(prefix, tmp_path, CASES, entry_calls(range(4), 2))
    assert c.count("break;") >= 2 and "switch (" in c


def test_tail_calls_print_in_place(prefix, tmp_path):
    """Each jump into a routine called elsewhere is a call then return where it is taken."""
    _, _, info = check(
        prefix, tmp_path, TAILS, entry_calls(range(4), 2), entries=("entry", "other")
    )
    _, labels, _ = assemble(TAILS)
    sub = next(f for f in info["provenance"]["functions"].values() if f["entry"] == labels["sub"])
    sites = {s for s, _ in sub["calls"]}
    assert {labels["x"] - 3, labels["other"] - 3} <= sites and labels["other"] in sites


def test_decided_edges_are_dropped(prefix, tmp_path):
    """`BCS` after `BCC` on the same carry always branches: its fall-through is no edge."""
    c, _, _ = check(prefix, tmp_path, CMP3, entry_calls([0, 0x0F, 0x10, 0x80]))
    assert c.count("io_write(0xC000") == 1


def test_copies_provenance():
    """An instance function's printed constructs name every copy's instruction."""
    pcs = {1: [1, 11, 21], 2: [2, 12, 0]}
    assert printer._copies([1, [1, 2]], pcs) == [1, [1, 2], 11, [11, 12], 21]
    assert printer._copies([5], {}) == [5]


def test_operand_patched_from_constants_is_a_known_store(prefix, tmp_path):
    """An operand written with a byte the code stored at a constant address holds its value:
    the store through it has a known address, so needs no code check."""
    c, _, info = check(prefix, tmp_path, PATCHED, entry_calls(range(4), 2))
    assert "codechk" not in c and not info["traps"] and "wr(" not in c


def test_block_copy_stores_have_per_block_addresses(prefix, tmp_path):
    """A block copy through a pointer a page loop advances (a rotated loop over pages, then the
    remainder) whose destination, source and length are table bytes of one block number: its
    stores write, per block, the destination up to the pages its length counts, so RAM past the
    image is data and no store is checked against the code."""
    calls = [("entry", 0), ("entry", 0x82), ("entry", 0x90), ("entry", 1), ("entry", 0x88)]
    c, _, info = check(prefix, tmp_path, BLOCKS, calls)
    assert "codechk" not in c and not info["traps"]
    spans = {org: n for org, n in info["regions"]}
    assert 0x5000 in spans and 0x6000 in spans and spans[0x6000] <= 0x200


def test_block_copy_indexes_the_blocks_directly(prefix, tmp_path):
    """The copy's stores have address sets in the data regions: they index the regions they
    span directly, testing only those, with no wr dispatch; the bases reaching each region
    are apart, so the test reads the pointer, not the index."""
    calls = [("entry", 0), ("entry", 0x82), ("entry", 0x90), ("entry", 1), ("entry", 0x88)]
    c, _, _ = check(prefix, tmp_path, BLOCKS, calls)
    img, labels, loaded = assemble(BLOCKS)
    p, _ = cgen.lift(img, [(labels["entry"], "entry")], [W.IO], loaded=loaded)
    assert any(store for _, store in p.addr_sets)
    assert " wr(" not in c and "CGEN_IX(" in c
    tests = re.findall(
        r"if \(\(uint16_t\)\(([^;]*?)\) <\s*0x[0-9A-F]+\)\s*CGEN_IX\(R\d+, [^,]*, (v\d+)\)", c
    )
    assert tests and all(not re.search(rf"\b{v}\b", t) for t, v in tests)


@pytest.mark.parametrize("name", ["TABLES", "PTRS", "BLOCKS", "VOICES", "RELOCATOR"])
def test_regions_abutting_a_held_span_are_its_slices(prefix, name):
    """A region that abuts a held span is held in it: one array, so rd/wr test one range."""
    img, labels, loaded = assemble(globals()[name])
    _, _, info = translate(prefix, img, labels, loaded)
    held = info["code_held"]
    for org, n in info["regions"]:
        for a, b in held:
            assert a <= org and org + n <= b or not (org + n == a or org == b), (org, n, a, b)


def test_brk_through_its_written_vector(prefix, tmp_path):
    """A `BRK` goes through the vector the program stores, and the handler's `RTI` returns
    past the signature byte: no trap on the way."""
    calls = [("entry", 0)] + entry_calls([0x80, 0x81, 0x82, 0x83], 2)
    c, _, info = check(prefix, tmp_path, BRKVEC, calls)
    img, labels, _ = assemble(BRKVEC)
    assert info["jumps"][labels["brk"]] == [labels["handler"]]
    assert info["jumps"][labels["cnt"] - 1] == [labels["brk"] + 2]


def test_brk_handler_of_two_sites_is_a_function(prefix, tmp_path):
    """A `BRK` handler two `BRK`s reach is a function each calls: the status and return address
    pushed, the handler's `RTI` a return, the flags restored; the `.c` is the same at another
    origin."""
    calls = [("entry", 0)] + entry_calls([0x80, 0x81, 0x82, 0x83], 2)
    c, _, info = check(prefix, tmp_path, BRK2, calls)
    img, labels, _ = assemble(BRK2)
    entries = {f["entry"] for f in info["provenance"]["functions"].values()}
    assert labels["handler"] in entries and not info["traps"] or labels["handler"] in entries
    img2, labels2 = W.assemble(BRK2, 0x2400)
    assert translate(prefix, img2, labels2, (0x2400, labels2["end"]))[0] == c


@pytest.mark.parametrize("sites", [2, 1])
def test_call_with_inline_arguments(prefix, tmp_path, sites):
    """A callee that pulls its return address, reads the bytes after the call and pushes the
    address back past them returns past its inline argument: called from two sites it is a
    function whose calls keep the address in memory, from one it is inlined; either way the
    `.c` is the same at another origin."""
    src = INLINE if sites == 2 else INLINE.replace("        JSR arg\n        .byte $22\n", "")
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(3)))
    img, labels, _ = assemble(src)
    assert info["jumps"][labels["end"] - 1] == [labels["entry"] + 4, labels["entry"] + 11][:sites]
    entries = {f["entry"] for f in info["provenance"]["functions"].values()}
    assert (labels["arg"] in entries) == (sites == 2)
    img2, labels2 = W.assemble(src, 0x2400)
    assert translate(prefix, img2, labels2, (0x2400, labels2["end"]))[0] == c


def test_txs_to_a_saved_stack_pointer_unwinds(prefix, tmp_path):
    """`TXS` to the stack pointer `TSX; STX save` took in an enclosing frame leaves the frames
    between as a non-local exit."""
    _, _, info = check(prefix, tmp_path, UNWIND, entry_calls(range(4), 2))
    assert info["stats"]["unknown_heights"] == 0


def test_per_copy_immediates_are_constants(prefix, tmp_path):
    """Unrolled copies whose state is per copy and whose immediates differ (not affine in the
    copy) are one function; the immediates are its per-copy constants."""
    c, _, info = check(prefix, tmp_path, CONSTS, entry_calls(range(4), 4))
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    assert not _voice_writes(c)


def test_copies_differing_only_in_immediates_stay_apart(prefix, tmp_path):
    """Copies alike in every address are one routine repeated, not a family indexed by the
    copy, whatever their immediates."""
    src = CONSTS.replace("$21", "$20").replace("$22", "$20").replace("$C007", "$C000")
    _, _, info = check(prefix, tmp_path, src.replace("$C00E", "$C000"), entry_calls(range(4)))
    assert info["stats"]["instances"] == 0


def test_statements_alike_in_a_prefix_are_not_copies(prefix, tmp_path):
    """Statements loading fields $20 apart, each a block whose rest differs (the immediate
    added), are alike only up to the middle of their blocks: no instance class, no index."""
    c, _, info = check(prefix, tmp_path, FIELDS, entry_calls(range(2), 4))
    assert info["stats"]["instances"] == 0 and " * " not in c and "static const" not in c


@pytest.mark.parametrize("src", [BRKLOOP, SWITCH], ids=["brk", "switch"])
def test_a_loops_index_holds_through_a_brk_or_a_stack_switch(prefix, tmp_path, src):
    """A voice loop's X (14, 7, 0) holds in the routine it calls through a `BRK` whose
    handler returns past it, or a nested call unwinding by `TXS` to the stack pointer the
    routine saved, its X restored from the byte it stored: the indexed I/O store's
    throw-away read stays below the read-sensitive register."""
    calls = [("entry", 0x80)] + entry_calls(range(3), 2)
    c, _, _ = check(prefix, tmp_path, src, calls, sensitive=(0xC0FF,))
    assert "io_read(" not in c.split("void entry")[1]


def test_per_copy_conditions_index_a_table(prefix, tmp_path):
    """A branch on a per-copy immediate tests the copy's value from a constant table, not a
    sum of comparisons with the index."""
    c, _, info = check(prefix, tmp_path, TESTED, entry_calls(range(4), 3))
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    assert "==" not in c and re.search(r"if \(K\d+\[v1\] != 0\)", c)


def _wide(inner, outer):
    """Three copies, each bumping two of its own operand cells ``inner`` bytes apart (past a
    jump over them) and a byte past the program at the copies' stride, and storing them to its
    voice's registers, ``outer`` bytes between."""

    def pad(n):
        return f"        .byte {', '.join(['0'] * n)}\n" if n else ""

    src = WIDE
    for k in range(3):
        last = "RTS" if k == 2 else f"JMP v{k + 1}"
        src += (
            f"v{k}:     INC a{k}+1\n        INC b{k}+1\n        INC {{end + {0x90 * k}}}\n"
            f"a{k}:     LDA #$00\n"
            f"        STA ${0xC000 + 7 * k:04X}\n        JMP b{k}\n{pad(inner)}"
            f"b{k}:     LDA #$00\n        STA ${0xC001 + 7 * k:04X}\n"
            f"        LDA {{end + {0x90 * k}}}\n        STA ${0xC002 + 7 * k:04X}\n"
            f"        {last}\n" + pad(outer if k < 2 else 0)
        )
    return src + "end:\n"


@pytest.mark.parametrize("inner,windows", [(0, 1), (0x100, 2)], ids=["one", "two"])
def test_copies_at_a_wide_stride_index_per_copy_pointers(prefix, tmp_path, inner, windows):
    """Copies whose operand cells lie further apart than 8 bits reach (a stride of more
    than $80 over three copies), and data bytes at that stride, read each copy's base once
    from a constant table of pointers into the span (or region) and index it by a byte, as
    ``(zp),Y`` does: no 16-bit index, no ``rd``/``wr``; fields of a copy further apart than 256
    bytes take a pointer per window."""
    src = _wide(inner, 0x70)
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(4), 6))
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    assert not re.search(r"\b(?:rd|wr)\(|uint16_t K\d|static const uint16_t", c)
    tabs = re.findall(r"static uint8_t \*const (K\d+)\[\] = \{([CR])(\d+)[^,]*, \2\3 \+ ", c)
    assert sorted(t[1] for t in tabs) == ["C"] * windows + ["R"]
    for tab, _, _ in tabs:
        [ptr] = re.findall(rf"uint8_t \*(v\d+) = {tab}\[v1\];", c)
        assert re.search(rf"\b{ptr}\[(?:0|0x[0-9A-F]{{1,2}})\]", c)
    img, labels = W.assemble(src, 0x2345)
    assert translate(prefix, img, labels, (0x2345, labels["end"]))[0] == c


@pytest.mark.parametrize(
    "one,two,form",
    [("01", "02", r"== v1\b"), ("05", "09", r"== (K\d+)\[v1\]")],
    ids=["index", "table"],
)
def test_a_compare_some_copies_lack_is_one_per_copy_compare(prefix, tmp_path, one, two, form):
    """A compare with an immediate that only some copies have (``CMP #k``; the first copy
    tests the loaded value against zero) compares the value with the copy's immediate, the
    index itself or a constant table of them, not a choice of comparisons on the index."""
    src = COMPARED.replace("${one}", f"${one}").replace("${two}", f"${two}")
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(4), 3))
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    assert re.search(form, c) and not re.search(r"v1 == \d", c)
    m = re.search(form, c)
    if m.groups():
        assert f"static const uint8_t {m.group(1)}[] = {{0, 5, 9}};" in c


def test_copies_entered_at_a_branch_after_unlike_loads_are_one_class(prefix, tmp_path):
    """Copies entered at a branch on the flags of a load the copies differ in (``LDY`` before
    the first, ``LDA`` before the others), each arm a block of its own, are one class entered
    at the branch: its arms are not two classes."""
    _, _, info = check(prefix, tmp_path, ARMS, entry_calls([0, 1, 0x80, 0x81], 3))
    _, labels, _ = assemble(ARMS)
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    [fn] = [f for f in info["provenance"]["functions"].values() if f["index"]]
    assert fn["copies"] == [labels[f"v{k}"] for k in range(3)]


def test_size_attribution(prefix, tmp_path):
    """tools/csize.py splits a compiled translation's bytes by C function and origin, each
    function against the instruction bytes of the 6502 code its provenance names."""
    csize = pytest.importorskip("csize")
    img, labels, loaded = assemble(PTRS)
    c, h, info = translate(prefix, img, labels, loaded)
    (tmp_path / "t.c").write_text(c, encoding="utf-8")
    (tmp_path / "t.h").write_text(h, encoding="utf-8")
    so = tmp_path / "t.so"
    subprocess.run(
        [
            "gcc",
            *cgenplay.CFLAGS,
            "-g",
            "-o",
            str(so),
            str(tmp_path / "t.c"),
            str(cgenplay.HARNESS),
        ],
        check=True,
    )
    (tmp_path / "info.json").write_text(json.dumps({"provenance": info["provenance"]}))
    image = bytes(img[loaded[0] : loaded[1]])
    per = csize.attribute(tmp_path / "t.c", so, info, image, loaded[0], tmp_path / "t.h")
    entry = per["entry"]
    assert entry["text"] == sum(entry.get(o, 0) for o in csize.ORIGINS) > 0
    assert 0 < entry["original"] <= labels["ptrs"] - labels["entry"] and entry["dispatch"] > 0
    table = csize.report(per)
    assert table.splitlines()[-1].startswith("| total |")
    args = [str(tmp_path / "t.c"), str(so), "--info", str(tmp_path / "info.json")]
    image_file = tmp_path / "t.bin"
    image_file.write_bytes(image)
    assert csize.main(args + ["--image", str(image_file), "--org", hex(loaded[0])]) == 0


def test_size_origins_of_lines():
    """Lines are classified by what they cost: trap paths with their tests, dispatch, tests of
    code cells, 16-bit arithmetic, the rest."""
    csize = pytest.importorskip("csize")
    text = """static uint8_t rd(uint16_t a) {
  return R0[a - R0_ORG];
}
void entry(uint8_t a) {
  if (R1[0x2] == 0xA9) {
    R0[0] = rd(R0_ORG + a);
  }
  if (a > 3) {
    trap(1);
  }
  uint16_t v = a << 8;
  R0[1] = a;
}"""
    kinds, owner = csize.line_origins(text, {0x1002}, {"R1": 0x1000, "R0": 0x10})
    assert [kinds[n] for n in range(1, 13)] == [
        "dispatch",
        "dispatch",
        "dispatch",
        "other",
        "cell",
        "dispatch",
        "other",
        "trap",
        "trap",
        "other",
        "arith16",
        "other",
    ]
    assert owner[6] == "entry" and owner[2] == "rd"


def test_operands_set_from_one_word_are_one_pointer(prefix, tmp_path):
    """Play is lifted from what init's run leaves: operand pairs init sets from one song word
    are one pointer variable, the others read as it plus their constant offset, and the
    ``.c`` is the same wherever the song puts its tables."""
    texts = set()
    for pad in ("", "        .byte 0,0,0\n"):
        img, labels, loaded = assemble(WORDREL.replace("pad:\n", f"pad:\n{pad}"))
        c, h, info = translate(
            prefix, img, labels, loaded, entries=("init", "play"), init=0, songs=range(1)
        )
        (tmp_path / str(len(pad))).mkdir()
        calls = [("init", 0)] + [("play", a) for a in range(4)] * 2
        play(tmp_path / str(len(pad)), c, h, img, labels, calls)
        ops = {labels[o] + k for o in ("o0", "o1", "o2") for k in (1, 2)}
        assert ops <= set(info["provenance"]["cells"])
        body = c[c.index("void play") :]
        assert len(set(re.findall(r"(R\d+)\[0\]", body))) == 2 and "1 + v" in body
        texts.add(c)
    assert len(texts) == 1


def test_song_table_offsets_are_named_in_the_header(prefix, tmp_path):
    """A table of song data inside a held span is indexed at an offset the ``.h`` names by the
    instruction reading it, so moving the song's tables leaves the ``.c`` unchanged."""
    texts = set()
    for pad in ("", ".byte 0,0,0,0,0\n"):
        src = TABLES.replace("lo:     .byte", f"        {pad}lo:     .byte") if pad else TABLES
        img, labels, loaded = assemble(src)
        c, h, _ = translate(prefix, img, labels, loaded)
        (tmp_path / str(len(pad))).mkdir()
        play(tmp_path / str(len(pad)), c, h, img, labels, entry_calls(range(8), 2))
        texts.add(c)
    assert len(texts) == 1
