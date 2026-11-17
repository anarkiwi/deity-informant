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
from types import SimpleNamespace

import numpy as np
import pytest
from vagrant_moses.c64 import poweron_ram

import wholeprog_progs as W
from deity_informant import NATIVE, cgen, postinit, trace, wholeprog
from deity_informant.cgen import csyntax, mlir, printer, regions, structure, traps
from deity_informant.lifter import OPS as OPCODES

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import cclones  # noqa: E402  pylint: disable=wrong-import-position
import cgenplay  # noqa: E402  pylint: disable=wrong-import-position
import cmetrics  # noqa: E402  pylint: disable=wrong-import-position
from testimages import MOS  # noqa: E402  pylint: disable=wrong-import-position
from test_pcode2mlir import OPS, UNARY, VALUES, op_function, unary_function  # noqa: E402
import test_polling as P  # noqa: E402  pylint: disable=wrong-import-position
import test_valuesets as VS  # noqa: E402  pylint: disable=wrong-import-position
import test_objects as TO  # noqa: E402  pylint: disable=wrong-import-position
import test_ptr_guards as PG  # noqa: E402  pylint: disable=wrong-import-position

pytestmark = pytest.mark.skipif(
    not NATIVE or not shutil.which("gcc") or not shutil.which("clang-format"),
    reason="needs the native extension, gcc and clang-format",
)

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
).replace("back:   .byte 0,0,0,1", "back:   .byte 0,0,3,2")
CURSOR = """
init:   LDA #$00
        STA cur
        LDA tab
        STA spd
        RTS
play:   LDX cur
        CPX #$05
        BNE ok
        LDA spd
        STA $C011
ok:     LDA tab,X
        STA $C010
        INX
        CPX #$03
        BNE s
        LDX #$00
s:      STX cur
        RTS
cur:    .byte 0
spd:    .byte 0
tab:    .byte 1,2,3
end:
"""
COUNTDOWN = """
init:   LDA #0
        STA pos
        LDA #3
        STA speed
        RTS
play:   DEC speed
        BNE wait
        LDA #3
        STA speed
        LDY pos
        LDA data,Y
        STA $C000
        INY
        CPY #16
        BCC keep
        LDY #0
keep:   STY pos
wait:   LDA speed
        CMP #$80
        BCC done
        LDA #$FF
        STA $C018
done:   RTS
pos:    .byte 0
speed:  .byte 0
data:   .byte 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16
end:
"""
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
STRIDED = """
entry:  LDX #14
loop:   LDA $3000,X
        CLC
        ADC $3001,X
        STA $3001,X
        STA $C000,X
        LDA $3140,X
        EOR #$5A
        STA $3140,X
        TXA
        SEC
        SBC #7
        TAX
        BPL loop
        RTS
end:
"""
STRIDED_CALLED = """
entry:  LDX #0
        JSR rec
        LDX #7
        JSR rec
        LDX #14
        JSR rec
        RTS
rec:    LDA $3000,X
        CLC
        ADC #3
        STA $3000,X
        STA $C000,X
        LDA $3001,X
        ADC $3000,X
        STA $3001,X
        RTS
end:
"""
STRIDED_ALIASED = STRIDED.replace(
    "entry:  LDX #14", "entry:  TAY\n        LDA $3000,Y\n        STA $C010\n        LDX #14"
)
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
SONGTAB = """
entry:  LDY song
        LDA tlo,Y
        STA $C000
        LDA thi,Y
        STA $C001
        INC song
        TYA
        STA thi,Y
        RTS
song:   .byte 0
tlo:    .byte $10,$20,$30,$40
thi:    .byte $11,$21,$31,$41
end:
"""
GUARDFWD = """
entry:  TAY
        LDX cell
        INX
        STX cell
        LDX $C000
        STA tab,Y
        STA $C001,X
        LDX cell
        STX $C002
        RTS
cell:   .byte 0
tab:    .byte $11,$22,$33,$44
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
deref:  LDA ($FD),Y
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
NESTED_LATCH = """
entry:  LDX #$0E
outer:  JSR sub
latch:  TXA
        SEC
        SBC #$07
        TAX
        BPL outer
        RTS
sub:    LDY #$03
inner:  INC $20,X
        DEY
        BPL inner
        LDA $20,X
        STA $C000,X
        LDA $21,X
        AND #$40
        BEQ skip
        INC $22,X
skip:   LDY #$02
again:  INC $21,X
        DEY
        BPL again
        LDA $21,X
        BPL done
        INC $22,X
done:   RTS
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
FXSHARE = """
entry:  AND #$03
        TAY
        JSR fx1
        JSR fx2
        TYA
        EOR #$01
        TAY
        JSR fx1
        JSR fx2
        RTS
fx1:    LDX #$00
        LDA tlo,Y
        STA ptr
        LDA thi,Y
        STA ptr+1
        JMP (ptr)
fx2:    LDX #$01
        STX $C010
        LDA tlo,Y
        STA ptr
        LDA thi,Y
        STA ptr+1
        JMP (ptr)
h0:     LDA #$10
        JMP t1
h1:     LDA #$11
t1:     STA $C000,X
        RTS
h2:     LDA #$12
        JMP t2
h3:     LDA #$13
t2:     STA $C004,X
        RTS
tlo:    .byte lo(h0),lo(h1),lo(h2),lo(h3)
thi:    .byte hi(h0),hi(h1),hi(h2),hi(h3)
ptr:    .byte 0,0
end:
"""
OPSHARE = """
entry:  CMP #$04
        BCS call
        TAY
        LDA tlo,Y
        STA s1+1
        RTS
call:   AND #$01
        BEQ jump
s1:     JSR h0
        STA $C000
        RTS
jump:   LDA #$20
        JSR other
        JMP other
other:  STA $C003
        JMP h2
h0:     LDA #$10
        STA $C001
        RTS
h1:     LDA #$11
        RTS
h2:     STA $C002
        RTS
tlo:    .byte lo(h0),lo(h1),lo(h2),lo(h0)
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
init:   RTS
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
entry:  AND #$03
        TAX
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
#: A relocator walking a table of operand addresses to its zero high byte, adding a base
#: word to each operand in place through the pointer an entry makes.
WALKREL = """
init:   LDX #$00
r:      LDA sites+1,X
        BEQ done
        STA $FC
        LDA sites,X
        STA $FB
        LDY #$01
        LDA ($FB),Y
        CLC
        ADC base
        STA ($FB),Y
        INY
        LDA ($FB),Y
        ADC base+1
        STA ($FB),Y
        INX
        INX
        BNE r
done:   RTS
play:   AND #$03
        TAY
o0:     LDA {d0-0x800},Y
        STA $C000
o1:     LDA {d1-0x800},Y
        STA $C001
o2:     LDA {d0-0x800},Y
        STA $C002
        RTS
base:   .byte $00, $08
sites:  .byte lo(o0), hi(o0), lo(o1), hi(o1), lo(o2), hi(o2), 0, 0
d0:     .byte 1,2,3,4
d1:     .byte 5,6,7,8
end:
"""
#: :data:`WALKREL` relocating the operands of a load and a store it then runs: a copy
#: through operands the walk patched.
PAIRREL = WALKREL.replace(
    "done:   RTS\n",
    "done:   JSR cp\n        RTS\ncp:     LDA {d1-0x800}\n        STA {d0-0x800}\n        RTS\n",
).replace("lo(o2), hi(o2), 0, 0", "lo(o2), hi(o2), lo(cp), hi(cp), lo(cp+3), hi(cp+3), 0, 0")
#: An entry walking a table of record addresses, storing into each record through the
#: pointer an entry makes.
DATAWALK = """
entry:  AND #$03
        TAY
        LDX #$00
r:      LDA recs+1,X
        BEQ done
        STA $FC
        LDA recs,X
        STA $FB
        LDA ($FB),Y
        CLC
        ADC #$01
        STA ($FB),Y
        STA $C000
        INX
        INX
        BNE r
done:   RTS
recs:   .byte lo(q0), hi(q0), lo(q1), hi(q1), 0, 0
q0:     .byte 1,2,3,4
q1:     .byte 5,6,7,8
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
    """``(c, h, info)`` of the program; every ``goto`` names a label of its function."""
    ents = [(labels[n], n) for n in entries]
    out = cgen.translate(
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
    assert not cmetrics.unlabeled(out[0])
    return out


def play(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    tmp_path, c, h, img, labels, calls, sensitive=(), trap=None
):
    """Compile ``c``/``h`` and replay ``calls`` ``[(name, a)]`` against the reference; with
    ``trap`` ``(n, k)`` exactly up to call ``n``, which traps with ``k``."""
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")
    names = {n for n, _ in calls}
    pcs = {n: labels[n] for n in names}
    return cgenplay.replay(lib, img, pcs, calls, [W.IO], sensitive, trap=trap)


def plain(c):
    """``c`` without the lines whose compiling ``CGEN_OOB`` or ``CGEN_OOB_EXACT`` decides."""
    out, skip = [], []
    for line in c.splitlines():
        if line.startswith("#if"):
            skip.append(bool(re.search(r"\bCGEN_OOB(?:_EXACT)?\b", line)))
        if not any(skip):
            out.append(line)
        if line.startswith("#endif"):
            skip.pop()
    return "\n".join(out)


def body_of(c, name):
    """The text of entry ``name``'s function in ``c``."""
    start = c.index(f"void {name}(")
    end = c.find("\nvoid ", start)
    return c[start : end if end >= 0 else len(c)]


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


# The strings are written by the entry.
PTRS_WRITTEN = PTRS.replace(
    "        INY\n        BNE loop", "        STA s1\n        INY\n        BNE loop"
)


def test_pointer_reads_go_through_regions(prefix, tmp_path):
    # The pointer's bases are the words of its table: its walks to their zero markers read
    # the span holding the strings unchecked; strings the entry writes are read plain, the
    # memory map checking the pointer plus the index, no dispatch over every region.
    c, h, info = check(prefix, tmp_path, PTRS, entry_calls([0, 1, 1, 0]))
    assert not info["unknown_access"] and "rdC" not in c and "CGEN_IX(" in c
    assert all(f"R{k}_ORG" in h for k in range(len(info["regions"])))
    (tmp_path / "w").mkdir()
    c, h, info = check(prefix, tmp_path / "w", PTRS_WRITTEN, entry_calls([0, 1, 1, 0]))
    assert not info["unknown_access"] and not re.search(r"\b(?:rd|rdC\d+)\(", c)
    assert any(g["def"] == "machine" for t in info["trap_info"].values() for g in t["guards"])


def test_pointer_reads_are_kept_off_the_stack_and_io(prefix, tmp_path):
    # Read-sensitive I/O would keep the throw-away reads of a page-crossing (zp),Y: the
    # pointer's address set (the words of its table) misses the I/O pages and page 1, so
    # there are none, no check is needed and pushes need not be in memory.
    c, _, info = check(prefix, tmp_path, PTRS, entry_calls([0, 1, 1, 0]), sensitive=(0xC0FF,))
    assert not info["traps"] and "io_read(" not in c.split("void entry")[1]
    assert all(org + n <= 0x100 or org >= 0x200 for org, n in info["regions"])


def test_pointer_from_split_tables_reads_one_span(prefix, tmp_path):
    # A data byte indexes the pointer's split tables (the index unbounded: they are read inside
    # their extent) and the offset grows by each record's length: every read is a plain read
    # of the span holding the records, checked by the memory map, with no dispatch over the
    # regions.
    c, _, info = check(prefix, tmp_path, SONGPTR, entry_calls([0, 1, 2, 1, 0]))
    assert not re.search(r"\b(?:rd|rdC\d+)\(", c) and not info["unknown_access"]
    assert "CGEN_IX(C0," in c
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
    # length (`ADC lo; BCC; INC hi`, or into a copy's operand cells), or steps it back with a
    # borrow: the pair is the song's, so every read is a plain read of the song's data block,
    # the memory map checking it there. Stepped back from the second song's start, read from
    # its tables, it leaves the block and traps at that check. Never set from the image's
    # zeros, it is no song's: its reads keep the span's reader, ``oob_rd`` below the program.
    song = src is not UNSET
    c, info, labels = songs(prefix, tmp_path, src, (0,) if src is STEPBACK else (0, 1), 0, range(2))
    assert not re.search(r"\brd\(", c) and ("rdC0(" in c) != song
    assert bool(machine_guards(info)) == song and not info["unknown_access"]
    if src is STEPBACK:
        img, _, loaded = assemble(src)
        c, h, info = translate(
            prefix, img, labels, loaded, ("init", "play"), init=0, songs=range(2)
        )
        k = machine_guard(info, labels["play"] + 2)
        calls = [("init", 1)] + [("play", a) for a in range(4)] * 2
        (tmp_path / "1").mkdir()
        play(tmp_path / "1", c, h, img, labels, calls, trap=(1, k))


def test_end_marker_tested_before_its_pointer(prefix, tmp_path):
    """The pointer's high byte comes from a table whose zero marks the end of the order: the
    branch on it leaves only indices of nonzero bytes, and the restart index the marker's
    entry gives names a nonzero byte, so at the order positions play reaches the pointer
    never names zero page and its read is plain, checked by the memory map."""
    c, _, _ = songs(prefix, tmp_path, ORDER, (0,))
    assert not re.search(r"\brd\(", c) and "rdC0(" not in c


def test_end_marker_restart_past_the_order(prefix, tmp_path):
    """When a restart play reaches names another marker, the pointer the order's tables make
    names zero page, outside the program: invalid input, whatever the tables hold. The calls
    replay exactly until that read, which traps at the memory map's check of it, as does the
    order position run on past the order's tables."""
    img, labels, loaded = assemble(ORDERBAD)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
    assert not re.search(r"\brd\(", c) and machine_guard(info, labels["play"] + 3) is not None
    k = machine_guard(info, labels["ok"] + 13)
    calls = [("init", 0)] + [("play", k) for k in range(4)] * 2
    play(tmp_path, c, h, img, labels, calls, trap=(3, k))


def test_init_exit_holds_its_constants_only():
    """Init's exit whatever the data: the cursor it zeroes from an immediate, not the byte it
    copies from the song's table."""
    img, labels, loaded = assemble(CURSOR)
    p = wholeprog.lift(img, [labels["init"]], [W.IO], once=True, entry_sp=0xFD, loaded=loaded)
    assert p.exit == {labels["cur"]: b"\x00"}


def test_cursor_bounded_across_calls(prefix, tmp_path):
    """Play steps a cursor init zeroes and wraps it at 3: the driver head holds it in 0-2 at
    every call, so its table read is plain and the branch on it reaching 5 is folded."""
    calls = [("init", 0)] + [("play", 0)] * 8
    c, _, info = check(prefix, tmp_path, CURSOR, calls, entries=("init", "play"), init=0, songs=[0])
    assert "0xC011" not in body_of(c, "play") and len(info["traps"]) == 1


COUNTWRAP = COUNTDOWN.replace(
    """wait
        LDA #3""",
    """wait
        LDA #0""",
)
COUNTPAST = COUNTDOWN.replace("CPY #16", "CPY #17")


def test_countdown_reloaded_with_zero_wraps(prefix, tmp_path):
    """Reloaded with 0, the counter wraps to $FF on the next call: the branch on its sign
    stays."""
    calls = [("init", 0)] + [("play", 0)] * 40
    c, _, _ = check(prefix, tmp_path, COUNTWRAP, calls, entries=("init", "play"), init=0, songs=[0])
    assert "0xC018" in body_of(c, "play")


def test_cursor_past_its_table_keeps_its_guard(prefix, tmp_path):
    """Reset at 17 over a 16-byte table, the position reaches the byte past it: the read keeps
    its check, which traps on the reload reaching it."""
    img, labels, loaded = assemble(COUNTPAST)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
    k = machine_guard(info, labels["play"] + 13)
    assert k is not None
    calls = [("init", 0)] + [("play", 0)] * 60
    play(tmp_path, c, h, img, labels, calls, trap=(1 + 3 * 17 - 1, k))


def test_countdown_reloaded_across_calls(prefix, tmp_path):
    """A speed counter play decrements and reloads with 3 at zero holds 1-3 at every call: the
    branch on its sign folds. The position it steps on each reload stays in its table."""
    calls = [("init", 0)] + [("play", 0)] * 40
    c, _, info = check(
        prefix, tmp_path, COUNTDOWN, calls, entries=("init", "play"), init=0, songs=[0]
    )
    assert "0xC018" not in body_of(c, "play") and len(info["traps"]) == 1


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
    # The push index grows without bound: the store writes the array its base lies in,
    # checked by the memory map against it, never into the code after it.
    img, labels, loaded = assemble(PUSH)
    c, h, info = translate(prefix, img, labels, loaded)
    play(tmp_path, c, h, img, labels, entry_calls([5, 6, 7]))
    body = c.split("void entry")[1]
    assert not re.search(r"\b(?:oob_wr|wr|codechk)\(", body)
    assert machine_guard(info, labels["entry"] + 3) is not None
    [k] = [k for k, (org, n) in enumerate(info["regions"]) if org == labels["stack"]]
    assert info["regions"][k][1] == 3 and re.search(rf"\bR{k}\[v\d+\] = a;", body)
    assert cgen.lift(img, [(labels["entry"], "entry")], [W.IO], loaded=loaded)[0].bounded == {
        labels["entry"] + 3: (labels["stack"], labels["entry"])
    }


PUSHIF = """
stack:  .byte 0,0,0
entry:  LSR A
        BCC out
        LDX depth
        STA stack,X
        CMP #$7F
        BNE out
        INC depth
out:    LDA stack
        STA $C010
        RTS
depth:  .byte 0
end:
"""


def test_checked_store_in_a_condition_is_a_statement(prefix, tmp_path):
    # A store the memory map checks is an if/else statement, no expression: the branch holding
    # it is not composed into the test before it as a comma expression.
    img, labels, loaded = assemble(PUSHIF)
    c, h, info = translate(prefix, img, labels, loaded)
    play(tmp_path, c, h, img, labels, entry_calls([3, 0xFF, 0xFF, 2, 0xFF, 0]))
    assert machine_guard(info, labels["entry"] + 6) is not None
    assert "oob_wr(" not in c and not re.search(r", +if \(", c)


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
    # command) on a stack whose depth nothing bounds and a return pops it: the pointer's
    # words, popped past the stack's bytes as the depth runs on, come from the song's data
    # whatever they hold, so its reads are plain reads of the data block, the memory map
    # checking them against it; the push is checked by the memory map against the stack's
    # bytes. Loaded from I/O instead, it may point anywhere: the span's reader reads
    # ``oob_rd`` outside the program.
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    assert not re.search(r"\brd\(", c) and ("rdC0(" in c) != bounded
    assert machine_guard(info, labels["call"] + 8) is not None
    assert not re.search(r"\b(?:oob_wr|wr)\(", plain(c))
    if bounded:
        assert machine_guard(info, labels["play"] + 2) is not None
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
    # The pair init leaves is a variable: pointing into the records' run of data bytes, play's
    # reads through it are bounded to that run in the pass that bounds pointers into the
    # program's data; pointing at an instruction, it is taken not to be read through, and the
    # reads are bounded by what play stores. Either value is the song's, init's or its
    # table's, so the reads hold to the song's data block (init's code, which play never
    # runs, among it) on every valid run, the memory map checking them.
    src = LEFT.replace("lo(read)", f"lo({at})").replace("hi(read)", f"hi({at})")
    img, labels, loaded = assemble(src)
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    p, _ = cgen.lift(img, ents, [W.IO], loaded=loaded, init=0)
    read = labels["read"] + 2
    assert p.reach[read][0] == labels["plo" if at == "p1" else "p0"]
    assert p.proven[read][:2] == (loaded[0], loaded[1] - 1)


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
    """An index run off its table reads the code after it, held; past the program's end it
    would read RAM the program never held, invalid input: the read traps at the memory map's
    check there, every call before it exact."""
    img, labels, loaded = assemble(OOB)
    c, h, info = translate(prefix, img, labels, loaded)
    k = machine_guard(info, labels["entry"] + 3)
    n = labels["end"] - labels["tab"]
    play(tmp_path, c, h, img, labels, entry_calls([0], 64), trap=(n, k))
    assert "C0[" in c
    assert info["code_span"][0] == W.assemble(OOB)[1]["entry"]
    [(lo, hi)] = info["code_held"]
    assert lo < info["code_span"][1] and info["code_span"][0] < hi


INCODE = """
entry:  AND #$07
        TAX
        LDA tab,X
        STA $C010
        JMP next
tab:    .byte 3,1,4,1,5,9,2,6
next:   TXA
        EOR #$07
        TAY
        LDA tab,Y
        STA $C011
        RTS
end:
"""


def copies_reading_tables(split):
    """Three unrolled copies, each reading its own table between its instructions by an
    index of its own byte: one table, or (``split``) a low and a high column forming a
    pointer read through ``($FB),Y``."""
    out = ["entry:"]
    for i, reg in enumerate((0xC000, 0xC007, 0xC00E)):
        out.append(f"        LDA ${0x10 + i:02X}\n        AND #$03\n        TAX")
        if split:
            out.append(
                f"        LDA lo{i},X\n        STA $FB\n        LDA hi{i},X\n        STA $FC\n"
                f"        LDY #$01\n        LDA ($FB),Y"
            )
        else:
            out.append(f"        LDA lo{i},X")
        out.append(f"        STA ${reg:04X}\n        INC ${0x10 + i:02X}\n        JMP w{i}")
        out.append(
            f"lo{i}:   .byte "
            + ",".join(f"lo(d{j})" if split else str(i * 4 + j) for j in range(4))
        )
        if split:
            out.append(f"hi{i}:   .byte " + ",".join(f"hi(d{j})" for j in range(4)))
        out.append(f"w{i}:")
    out.append("        RTS")
    out += [f"d{j}:     .byte {2 * j + 1},{2 * j + 2}" for j in range(4)]
    return "\n".join(out + ["end:", ""])


@pytest.mark.parametrize(
    "src,instances",
    [(INCODE, 0), (copies_reading_tables(False), 1), (copies_reading_tables(True), 1)],
    ids=["two-reads", "per-copy", "per-copy-split"],
)
def test_tables_inside_code_index_a_named_offset(prefix, tmp_path, src, instances):
    """A constant table between instructions, read by index, is its span's element at an
    offset the ``.h`` names (each copy's table its copy's offset beside it): no code address
    is a value, and the ``.c`` is the same at every origin."""
    outs = []
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(src, org)
        c, h, info = translate(prefix, img, labels, loaded)
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, entry_calls(range(8), 2))
        outs.append(c)
    assert outs[0] == outs[1] and info["stats"]["instances"] == instances
    assert "CODE_ORG" not in outs[0] and "IMG_ORG" not in outs[0]
    named = re.findall(r"\bC\d+\[(C\d+_AT[0-9A-F]+)\b", outs[0])
    assert named and all(f"#define {n} " in h for n in named)


def test_table_inside_code_a_store_writes_is_data(prefix, tmp_path):
    """A table between instructions that a store writes is a region's bytes, its accesses
    the region's elements, with no code check and no held span."""
    src = INCODE.replace("        JMP next", "        INC tab,X\n        JMP next")
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(8), 2))
    assert re.search(r"R0\[v1\] = v\d+ \+ 1;", c) and "R0[v1 ^ 7]" in c
    assert "codechk" not in c and not re.search(r"\bC\d+\[", c) and not info["traps"]


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


OPFLAGS = """
init:   LDA #$A9
        STA gate
        LDA #$69
        STA dir
        LDA #0
        STA val
        RTS
play:   LDA val
        CLC
dir:    ADC #1
        STA val
        STA $C001
        CMP #$0F
        BNE notop
        LDA #$E9
        STA dir
notop:  LDA val
        BNE notbot
        LDA #$69
        STA dir
notbot: LDA gate
        EOR #$C9
        STA gate
gate:   LDA #$07
        STA $C000
        RTS
val:    .byte 0
end:
"""
SONGOP = """
init:   TAY
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
        RTS
play:   LDY #0
        LDA ($FB),Y
        STA dir
        LDA #3
        CLC
dir:    ADC #1
        STA $C001
        RTS
plo:    .byte lo(s0),lo(s1)
phi:    .byte hi(s0),hi(s1)
s0:     .byte $69
s1:     .byte $E9
end:
"""


def test_opcode_cells_are_states(prefix, tmp_path):
    """Opcode cells the program uses as flags (``gate`` toggled between ``LDA #imm`` and
    ``RTS`` by ``EOR``, ``dir`` turned between ``ADC #imm`` and ``SBC #imm``) are states named
    by their instructions: play tests and assigns the names, the toggle maps state to state,
    the ``RTS`` state returns, and it replays exactly."""
    img, labels, loaded = assemble(OPFLAGS)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    body = body_of(c, "play")
    assert not re.search(r"\b0x(?:60|A9|69|E9)\b", body) and "trap(" not in body
    ops = info["state"]["opcodes"]
    gate, turn = ops[labels["gate"]], ops[labels["dir"]]
    assert gate["values"] == [0x60, 0xA9] and turn["values"] == [0x69, 0xE9]
    g, d = gate["name"], turn["name"]
    assert gate["enum"] == [f"{g}_RTS", f"{g}_LDA"] and turn["enum"] == [f"{d}_ADC", f"{d}_SBC"]
    assert re.search(rf"{g} = (v\d+) == {g}_RTS \? {g}_LDA : {g}_RTS;", body)
    assert f"{d} = {d}_SBC;" in body and f"{d} == {d}_ADC" in body
    assert f"enum {{ {g}_RTS = 0x60, {g}_LDA = 0xA9 }};" in c
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", 0)] * 200)


def test_opcode_state_names():
    """A state is named by its mnemonic, with its mode where the mnemonic repeats and its
    value where both do."""
    names = printer.enum_names("S_3", [0x02, 0x12, 0x69, 0xA9, 0xAD])
    assert list(names.values()) == [
        "S_3_JAM_IMPL_02",
        "S_3_JAM_IMPL_12",
        "S_3_ADC",
        "S_3_LDA_IMM",
        "S_3_LDA_ABS",
    ]


def test_opcode_cell_of_another_length_keeps_its_check(prefix, tmp_path):
    """Toggled between ``LDA #imm`` and ``LDA abs``, which reads its operand and the next
    byte, the cell has no state: its variant past the one it lifts traps."""
    src = OPFLAGS.replace("EOR #$C9", "EOR #$04")
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    assert labels["gate"] not in info["state"]["opcodes"] and "_LDA" not in c
    [k] = [k for k, key in info["traps"].items() if key == ("deopt", labels["gate"])]
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", 0)] * 4, trap=(1, k))


def test_opcode_cell_from_song_data_is_a_byte(prefix, tmp_path):
    """Written from the song's data through a pointer, the cell's values are unknown: no
    state, the variant the lift takes is checked, and the ``.c`` is the same whatever the
    songs hold."""
    img, labels, loaded = assemble(SONGOP)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0, 1])
    other = assemble(SONGOP.replace("$E9\n", "$29\n"))[0]
    assert translate(prefix, other, labels, loaded, ("init", "play"), init=0, songs=[0, 1])[0] == c
    assert not info["state"]["opcodes"] and "enum" not in c
    [k] = [k for k, key in info["traps"].items() if key == ("deopt", labels["dir"])]
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", 0)] * 4)
    play(tmp_path, c, h, img, labels, [("init", 1)] + [("play", 0)] * 4, trap=(1, k))


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
    # Target bytes from one table pair indexed by the register still holding the index: a
    # switch on the index's values (two targets: a test of it).
    assert "CODE_ORG" not in c
    if name != "PUSHRTS":
        step = 2 if name == "JMPIND" else 1
        assert re.search(r"switch \(v\d+\)", c)
        assert all(f"case {i * step}:" in c for i in range(3))


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
    # The stores through the pointers the table makes are checked by the memory map, whatever
    # words the table holds.
    rloop = assemble(RELOCATOR)[1]["rloop"]
    machine = {machine_guard(info, rloop + 16), machine_guard(info, rloop + 21)}
    assert None not in machine and "codechk" not in c
    rest = {k: t for k, t in info["traps"].items() if k not in machine}
    assert pointer_checks_only({**info, "traps": rest}, RELOCATOR)
    assert (st["code_guards"], st["cells"]) == (0, 4)


def test_branch_offset_written_from_a_table(prefix, tmp_path):
    # Offsets 0, 5 and 10 (and 10 again) enter one straight-line run at three points: a switch
    # on the offset's index whose cases fall through into the next.
    c, _, info = check(prefix, tmp_path, BRTAB, entry_calls([0, 1, 2, 3, 1]))
    assert re.findall(r"case (\w+):", c) == ["0", "1"] and "default:" in c
    assert "CODE_ORG" not in c and "break;" not in c
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
    assert outs[0] == outs[1] and "rd(MEM_K00FB | ((uint16_t)MEM_K00FC << 8))" in outs[0]


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


def test_intervals_found_before_the_rewrites_are_found_again(prefix, tmp_path, monkeypatch):
    """Every interval found before ``prune`` rewrites the operations (the instance index
    folded, carries joined) is found again after: the checks printed are the rewritten
    module's whatever was asked of it before."""
    img, labels, loaded = assemble(CMDFX)
    cold = translate(prefix, img, labels, loaded)[0]
    unwritten = printer.Module.unwritten

    def warm(mod):
        free = unwritten(mod)
        for f in mod.funcs:
            for o in f.walk():
                if o.name in ("memref.load", "memref.store") and o.args[-2] in f.globals:
                    f.addr_iv(o)
                for r in (r for r in o.res if f.types.get(r) in ("i1", "i8", "i16")):
                    f.iv(r)
        assert mod.results_ivs
        return free

    monkeypatch.setattr(printer.Module, "unwritten", warm)
    c, h, _ = translate(prefix, img, labels, loaded)
    assert c == cold and "oob_rd" not in c
    play(tmp_path, c, h, img, labels, entry_calls(range(4), 2))


#: Three copies, each storing at its own base plus a byte its own constant table entry holds.
PERCOPY = """
entry:  LDX $31
        STX $C001
        LDX $35
        STX $C002
        LDX $39
        STX $C003
        AND #$03
        STA $13
v0:     LDX t0
        LDA $13
        STA $30,X
        STA $C000
        JMP v1
v1:     LDX t1
        LDA $13
        STA $34,X
        STA $C007
        JMP v2
v2:     LDX t2
        LDA $13
        STA $38,X
        STA $C00E
        RTS
t0:     .byte 1
t1:     .byte 3
t2:     .byte 2
end:
"""


def test_intervals_asked_in_an_instances_context_are_its_own(prefix, tmp_path, monkeypatch):
    """Intervals an instance function finds for one copy's index stay that copy's: asked
    first for each copy (every interval forgotten before), a load of the copy's own constant
    is still a load of the copy's table entry, not one copy's constant for every copy, and
    the checks printed are those found in no context."""
    img, labels, loaded = assemble(PERCOPY)
    cold = translate(prefix, img, labels, loaded)[0]
    structure = printer.Module.structure

    def warm(mod):
        mod.forget()
        for f in (f for f in mod.funcs if f.kparam is not None):
            for o in f.walk():
                if o.name in ("memref.load", "memref.store") and o.args[-2] in f.globals:
                    assert f.kivs(f.address(o))
        return structure(mod)

    monkeypatch.setattr(printer.Module, "structure", warm)
    c, h, info = translate(prefix, img, labels, loaded)
    assert info["stats"]["instances"] == 1 and c == cold and "CODE_K" not in c
    play(tmp_path, c, h, img, labels, entry_calls(range(4), 2))


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


def arrays_end(h):
    """The end of the highest array the ``.h`` defines."""
    ends = re.findall(r"#define ([RC]\d+)_ORG (0x[0-9A-F]+)\n#define \1_LEN (\d+)", h)
    return max(int(org, 16) + int(n) for _, org, n in ends)


def regions_of(h):
    """``{name: origin}`` of the regions the ``.h`` defines."""
    return {k: int(org, 16) for k, org in re.findall(r"#define (R\d+)_ORG (0x[0-9A-F]+)\n", h)}


def test_unbounded_table_store_ends_with_the_program(prefix, tmp_path):
    """A split table at the end of the program stored at a song byte the code does not bound:
    its arrays end with the program, and a store past it traps (the loads at the byte are
    checked by the memory map)."""
    img, labels, loaded = assemble(SONGTAB)
    c, h, info = translate(prefix, img, labels, loaded)
    assert arrays_end(h) == labels["end"]
    [k] = [k for k, key in info["traps"].items() if info["trap_info"][k]["kind"] == "index"]
    assert info["traps"][k] == ("deopt", labels["entry"] + 19) and f"trap({k})" in c
    play(tmp_path, c, h, img, labels, entry_calls([0], 4))
    (tmp_path / "past").mkdir()
    with pytest.raises(AssertionError, match="trap"):
        play(tmp_path / "past", c, h, img, labels, entry_calls([0], 5))


def test_reload_forwarded_past_a_guarded_store(prefix, tmp_path):
    """A register stored to a cell and overwritten before a guarded store into a table that
    ends the program: the cell's reload past the store is forwarded, so the cell is read once
    and written once, and an index past the table traps."""
    img, labels, loaded = assemble(GUARDFWD)
    c, h, info = translate(prefix, img, labels, loaded)
    [k] = [k for k, a in regions_of(h).items() if a == labels["cell"]]
    assert body_of(c, "entry").count(f"{k}[0]") == 2
    [(t, key)] = info["traps"].items()
    assert key == ("deopt", labels["entry"] + 11) and f"trap({t})" in c
    play(tmp_path, c, h, img, labels, entry_calls(range(4), 2))
    (tmp_path / "past").mkdir()
    with pytest.raises(AssertionError, match="trap"):
        play(tmp_path / "past", c, h, img, labels, entry_calls([4]))


def test_unbounded_table_store_inside_the_program_is_unguarded(prefix, tmp_path):
    """The same table followed by data the whole index range stays inside has no guard."""
    src = SONGTAB.replace("end:\n", "        .byte 0\n" * 0x100 + "end:\n")
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded)
    assert not info["traps"] and arrays_end(h) > labels["thi"] + 4
    play(tmp_path, c, h, img, labels, entry_calls([0], 9))


GUARDLD = """
entry:  LDY song
        INC song
        TYA
        STA thi,Y
        LDA tlo,Y
        STA $C000
        INY
        LDA thi,Y
        STA $C001
        RTS
song:   .byte 0
tlo:    .byte $10,$20,$30,$40
thi:    .byte $11,$21,$31,$41
end:
"""


def test_guarded_store_and_the_loads_it_dominates_are_plain(prefix, tmp_path):
    """A store into a table ending the program past an index guard, and loads of the same
    index and of the index plus one after it, address the region with no run test (the loads
    checked by the memory map), and an index past the table traps."""
    img, labels, loaded = assemble(GUARDLD)
    c, h, info = translate(prefix, img, labels, loaded)
    body = body_of(c, "entry")
    [t] = [k for k, key in info["traps"].items() if info["trap_info"][k]["kind"] == "index"]
    assert info["traps"][t] == ("deopt", labels["entry"] + 7) and f"trap({t})" in body
    assert "oob_wr(" not in body and re.search(r"\bR0\[5 \+ (v\d+)\] = \1;", body)
    assert re.search(r"io_write\(0xC000, R0\[1 \+ v\d+\]\)", body)
    assert "oob_rd(" not in body and "R0_LEN" not in body
    # The fourth call's load of the index plus one would read past the program.
    k = machine_guard(info, labels["entry"] + 17)
    play(tmp_path, c, h, img, labels, entry_calls([0], 5), trap=(3, k))


def test_load_the_guard_does_not_dominate_keeps_its_test(prefix, tmp_path):
    """A load of the guarded index reached past the guarded store or around it is checked, by
    the memory map, plain."""
    src = GUARDLD.replace("        TYA\n", "        LSR A\n        BCC skip\n        TYA\n")
    src = src.replace("        LDA tlo,Y\n", "skip:   LDA tlo,Y\n")
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded)
    body = body_of(c, "entry")
    kinds = sorted(info["trap_info"][k]["kind"] for k in info["traps"])
    assert kinds == ["index", "objchk", "objchk"] and "oob_wr(" not in body
    assert "oob_rd(" not in body
    k = machine_guard(info, labels["entry"] + 20)
    play(tmp_path, c, h, img, labels, [("entry", a) for a in (0, 1, 2, 3)], trap=(3, k))


def meta_keyed(info):
    """``trap_info`` keyed as ``traps``, each trap at its key's address, its kind its
    guards'."""
    assert set(info["trap_info"]) == set(info["traps"])
    for k, t in info["trap_info"].items():
        key = info["traps"][k]
        assert isinstance(key, tuple) and key[0] in ("deopt", "ind") and t["pc"] == key[1]
        kinds = sorted({g["kind"] for g in t["guards"]})
        assert t["kinds"] == kinds and t["kind"] == (kinds[0] if len(kinds) == 1 else "mixed")


def trap_meta(prefix, src, entries=("entry",), **kw):
    """``(labels, info)`` of ``src`` translated, its ``trap_info`` checked by
    :func:`meta_keyed`."""
    img, labels, loaded = assemble(src)
    _, _, info = translate(prefix, img, labels, loaded, entries=entries, **kw)
    meta_keyed(info)
    return labels, info


def as_runs(ok):
    """Inclusive runs as tuples."""
    return [tuple(r) for r in ok]


def only_guard(info):
    """The one guard of the one trap of ``info``: ``(k, trap, guard)``."""
    [(k, t)] = info["trap_info"].items()
    [g] = t["guards"]
    return k, t, g


def tables_at(labels, spec):
    """Index guard tables of ``spec`` ``[(of, base, end, by, store)]``, labels as names and
    ``store`` a ``(label, offset)`` or None."""
    return [
        {
            "of": of,
            "base": labels[base],
            "end": labels[end],
            "by": by,
            "store": None if store is None else labels[store[0]] + store[1],
        }
        for of, base, end, by, store in spec
    ]


LOHI_FREE = LOHI.replace("AND #$03\n", "")
BRTAB_FREE = BRTAB.replace("entry:  AND #$03\n", "entry:  ")


@pytest.mark.parametrize(
    "src,at,ok,spec,writers",
    [
        (LOHI_FREE, ("entry", 1), 3, [("jump", "tlo", "thi", "column", None)], None),
        (
            BRTAB_FREE,
            ("entry", 1),
            3,
            [
                ("cell", "offs", "end", "loaded", ("entry", 4)),
                ("branch", "offs", "end", "loaded", None),
            ],
            [("entry", 4)],
        ),
        (
            CMDFX,
            ("chan", 3),
            3,
            [
                ("cell", "t0tbl", "fxtbl", "object", ("chan", 6)),
                ("jump", "t0tbl", "fxtbl", "object", None),
            ],
            [("chan", 6)],
        ),
        (SONGPTR, ("entry", 1), 2, [("data", "plo", "phi", "column", None)], None),
        (SONGTAB, ("entry", 19), 3, [("store", "thi", "end", "loaded", ("entry", 19))], None),
    ],
    ids=["jump", "branch", "cell", "data", "store"],
)
def test_index_trap_names_its_table(prefix, src, at, ok, spec, writers):
    """An index past its table's extent traps with the table it guards: what the table holds,
    where its column starts and what ends it, and for a code cell the store writing it."""
    labels, info = trap_meta(prefix, src)
    pc = labels[at[0]] + at[1]
    [t] = [t for t in info["trap_info"].values() if t["kind"] == "index"]
    [g] = t["guards"]
    assert (t["kind"], t["pc"], t["fn"], t["copies"]) == ("index", pc, None, [])
    assert (g["kind"], g["at"], g["reg"], as_runs(g["ok"])) == ("index", pc, "y", [(0, ok)])
    assert g["tables"] == tables_at(labels, spec) and as_runs(g["seen"]) == [(0, 0xFF)]
    want = [{"pc": labels[a] + o, "entry": "play", "exact": True} for a, o in writers or ()]
    assert g["writers"] == (want or None) and t["implied_by"] is None


#: Init storing at its song into a table ending the program; play reads the table.
SONGSTORE = """
init:   TAY
        LDA #$01
        STA tab,Y
        RTS
play:   LDA tab
        STA $C000
        RTS
tab:    .byte 0,0,0
end:
"""


def test_init_songs_imply_the_store_guard_at_its_argument(prefix, tmp_path):
    """Init's lift is for any argument: its store at the song keeps its guard on the table's
    extent, which the declared songs its entry checks imply."""
    _, info, labels = songs(prefix, tmp_path, SONGSTORE, range(3))
    meta_keyed(info)
    assert info["init"] == "translated"
    by = {key: info["trap_info"][k] for k, key in info["traps"].items()}
    [song] = by[("deopt", labels["init"])]["guards"]
    assert song["kind"] == "entry" and (song["reg"], as_runs(song["ok"])) == ("a", [(0, 2)])
    store = by[("deopt", labels["init"] + 3)]
    assert store["kind"] == "index" and store["implied_by"] == [traps.ENTRY]
    assert as_runs(store["guards"][0]["ok"]) == [(0, 2)]


#: Two stores at an unbounded song byte into a table ending the program, the tighter first.
TWOSTORES = """
entry:  LDY song
        INC song
        TYA
        STA thi+1,Y
        STA thi,Y
        RTS
song:   .byte 0
thi:    .byte 0,0,0,0
end:
"""


def test_index_guard_implied_by_a_dominating_one(prefix, tmp_path):
    """The second store's guard lets through what the first's does: past the first, ``Y``
    holds only values the second's lets pass, so it is not emitted."""
    img, labels, loaded = assemble(TWOSTORES)
    c, h, info = translate(prefix, img, labels, loaded)
    meta_keyed(info)
    first = labels["entry"] + 7
    k, t, g = only_guard(info)
    assert info["traps"][k] == ("deopt", first) and as_runs(g["ok"]) == [(0, 2)]
    table = {"of": "store", "base": labels["thi"] + 1, "end": labels["end"], "by": "loaded"}
    assert g["tables"] == [dict(table, store=first)] and t["implied_by"] is None
    play(tmp_path, c, h, img, labels, [("entry", 0)] * 3)


#: Init narrowing its song by a compare, offsetting it and keeping it on the stack across
#: device writes before it indexes the word columns of a pointer it reads through.
SONGSTACK = (
    """
init:   CMP #$0B
        BPL far
        TAY
        LDA near,Y
        STA $C001
        RTS
far:    SEC
        SBC #$0A
        PHA
        LDA #$0F
        STA $C018
        PLA
        TAX
        LDA plo,X
        STA $FB
        LDA phi,X
        STA $FC
        LDY #0
        LDA ($FB),Y
        STA $C000
        RTS
play:   LDA $C002
        STA $C003
        RTS
near:   .byte 0,1,2,3,4,5,6,7,8,9,10
plo:    .byte """
    + ", ".join(f"lo(near+{i % 11})" for i in range(22))
    + """
phi:    .byte """
    + ", ".join(f"hi(near+{i % 11})" for i in range(22))
    + """
end:
"""
)


def index_traps(info):
    """The instructions the index guards of ``info``'s traps guard."""
    guards = (g for t in info["trap_info"].values() for g in t["guards"])
    return sorted(g["at"] for g in guards if g["kind"] == "index")


@pytest.mark.parametrize("edit", [None, ("STA $C018", "STA $0100,Y")], ids=["songs", "stack"])
def test_init_song_carried_through_the_stack_bounds_its_index(prefix, tmp_path, edit):
    """The declared songs seed init's lift: a song past the compare, less ten, pushed and
    pulled back, indexes the pointer's columns inside their extent with no guard; a store
    between that may reach the pushed byte keeps the guard."""
    src = SONGSTACK if edit is None else SONGSTACK.replace(*edit)
    _, info, labels = songs(prefix, tmp_path, src, (0, 10, 11, 31), declared=range(32))
    meta_keyed(info)
    assert info["init"] == "translated"
    load = labels["far"] + 11
    assert index_traps(info) == ([] if edit is None else [load])


#: Per-voice bytes stored masked to a nibble (a voice's data byte where it is not zero), each
#: indexing the sixteen words of a pointer it reads through.
VOICEBYTE = (
    """
entry:  LDX #0
voice:  LDA $C000,X
        BEQ keep
        AND #$0F
        STA state,X
keep:   LDY state,X
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
        LDY #0
        LDA ($FB),Y
        STA $C010,X
        TXA
        CLC
        ADC #7
        TAX
        CPX #21
        BNE voice
        LDA next
        STA $C020
        RTS
state:  .byte """
    + ", ".join(["0"] * 15)
    + """
plo:    .byte """
    + ", ".join(f"lo(next+{i})" for i in range(16))
    + """
phi:    .byte """
    + ", ".join(f"hi(next+{i})" for i in range(16))
    + """
next:   .byte """
    + ", ".join(str(i * 5) for i in range(16))
    + """
end:
"""
)


@pytest.mark.parametrize("wild", [False, True])
def test_byte_bound_through_an_address_set_frees_its_index(prefix, tmp_path, wild):
    """Each byte the per-voice store's address set holds is bound by its entry byte and the
    nibbles stored: the load it indexes needs no guard. A store through a pointer of unknown
    address may leave them any value: the guard stays."""
    src = VOICEBYTE
    if wild:
        src = src.replace(
            "        LDA next",
            "        LDY #0\n        LDA $C003\n        STA ($FD),Y\n        LDA next",
        )
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded)
    meta_keyed(info)
    assert (labels["keep"] + 3 in index_traps(info)) == wild
    if not wild:
        play(tmp_path, c, h, img, labels, [("entry", 0)] * 4)


#: Per-voice commands dispatched through a call whose operand a table of targets patches; the
#: arm the first five commands share stores the command into another byte, which indexes the
#: five words of a pointer it reads through.
CMDARMS = (
    """
entry:  LDX #0
voice:  LDY cmd,X
        LDA jlo,Y
        STA call+1
        LDA jhi,Y
        STA call+2
        LDY #0
call:   JSR none
        LDY last,X
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
        LDY #0
        LDA ($FB),Y
        STA $C010,X
        LDA $C000,X
        AND #$07
        STA cmd,X
        TXA
        CLC
        ADC #7
        TAX
        CPX #21
        BNE voice
        LDA next
        STA $C020
        RTS
put:    LDA cmd,X
        STA last,X
none:   RTS
cmd:    .byte """
    + ", ".join(["0"] * 15)
    + """
last:   .byte """
    + ", ".join(["0"] * 15)
    + """
jlo:    .byte {lo}
jhi:    .byte {hi}
plo:    .byte lo(next), lo(next+1), lo(next+2), lo(next+3), lo(next+4)
phi:    .byte hi(next), hi(next+1), hi(next+2), hi(next+3), hi(next+4)
next:   .byte 4, 8, 15, 16, 23
end:
"""
)


@pytest.mark.parametrize("arms", [5, 6])
def test_switch_arm_stores_its_case_value(prefix, tmp_path, arms):
    """Inside each arm of the switch on a command byte the byte is that arm's case value: the
    byte the arm of the first five commands stores it into indexes the table of five with no
    guard; an arm a sixth command shares leaves the guard."""
    targets = ["put"] * arms + ["none"] * (8 - arms)
    src = CMDARMS.replace("{lo}", ", ".join(f"lo({t})" for t in targets))
    src = src.replace("{hi}", ", ".join(f"hi({t})" for t in targets))
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded)
    meta_keyed(info)
    assert (labels["call"] + 6 in index_traps(info)) == (arms == 6)
    if arms == 5:
        play(tmp_path, c, h, img, labels, [("entry", 0)] * 4)


#: Init writing play's opcode by its song.
INITCELL = """
init:   AND #$01
        BEQ nop
        LDA #$0A
        STA op
        RTS
nop:    LDA #$EA
        STA op
        RTS
play:   LDA $C000
op:     NOP
        STA $C001
        RTS
end:
"""


@pytest.mark.parametrize("wild", [False, True])
def test_init_alone_writing_a_cell_closes_it(prefix, tmp_path, wild):
    """A translated init's stores of known values are the only writers of play's opcode cell:
    its dispatch has no trap. A store of play writing it from data leaves the trap."""
    if wild:
        src = INITCELL.replace(
            "play:   LDA $C000", "play:   LDA $C002\n        STA op\n        LDA $C000"
        )
        labels, info = trap_meta(prefix, src, ("init", "play"), init=0, songs=range(2))
    else:
        _, info, labels = songs(prefix, tmp_path, INITCELL, (0, 1))
        meta_keyed(info)
    assert info["init"] == "translated"
    guards = (g for t in info["trap_info"].values() for g in t["guards"])
    assert [g["at"] for g in guards if g["kind"] == "opcode"] == ([labels["op"]] if wild else [])


def test_init_entry_trap_names_the_songs(prefix):
    """Init's trap on its argument lets the declared songs pass; its guards on the registers
    the C entry passes as constants, which they let pass, are left out."""
    labels, info = trap_meta(prefix, TABLECOPY, ("init", "play"), init=0, songs=range(3))
    assert info["init"] == "translated"
    [(k, key)] = info["traps"].items()
    t = info["trap_info"][k]
    assert key == ("deopt", labels["init"]) and t["kind"] == "entry" and not t["proven"]
    [g] = t["guards"]
    assert (g["reg"], g["at"], g["fn"], as_runs(g["ok"])) == (
        "a",
        labels["init"],
        labels["init"],
        [(0, 2)],
    )


def test_code_traps_name_their_writers(prefix):
    """Opcode cells of the routine init's copy may overwrite trap on bytes other than their
    own opcodes, naming the stores that may write them; a store that may write any of the
    program's bytes also checks the code."""
    labels, info = trap_meta(prefix, TABLECOPY, ("init", "play"))
    pre, copy = labels["pre"], labels["copy"]
    writers = [
        {"pc": copy + 2, "entry": "play", "exact": True},
        {"pc": pre + 2, "entry": "play", "exact": False},
    ]
    ops = {}
    for t in info["trap_info"].values():
        for g in t["guards"]:
            ops.setdefault(g["kind"], []).append(g)
    assert [(g["at"], as_runs(g["ok"])) for g in ops["opcode"]] == [
        (pre, [(0xA9, 0xA9)]),
        (pre + 2, [(0x8D, 0x8D)]),
        (pre + 5, [(0x60, 0x60)]),
    ]
    assert all(g["writers"] == writers for g in ops["opcode"])
    assert all(tuple(g["addr"]) == (g["at"],) * 2 for g in ops["opcode"])
    # The store's address is formed from the bytes the copy writes, the program's whatever
    # they hold: the memory map keeps it in the bytes the host holds, so it checks the code
    # there, and no stack.
    [code] = ops["codechk"]
    [held] = [g for g in ops["objchk"] if g["at"] == pre + 2]
    assert (code["at"], code["addr"]) == (pre + 2, held["addr"]) and held["addr"][0] == 0x1000
    assert code["writers"] == [writers[1]] and "stack" not in ops


def test_code_check_names_its_store(prefix):
    """A store through a pointer into the code checks the code it writes: its own writer."""
    labels, info = trap_meta(prefix, PSTORE)
    k, t, g = only_guard(info)
    store = labels["entry"] + 14
    assert info["traps"][k] == ("deopt", store + 2) and t["kind"] == "codechk"
    assert (g["at"], tuple(g["addr"]), g["fn"]) == (store, (0x1000, 0x10FF), labels["entry"])
    assert g["writers"] == [{"pc": store, "entry": "play", "exact": False}]


def test_zone_trap_names_the_pages_a_pointer_store_may_not_reach(prefix):
    """A store through a pointer pair holding any value traps when it reaches the stack page
    or the I/O page, and checks the code."""
    labels, info = trap_meta(prefix, FIXED, ("init", "play"))
    store = labels["play"] + 18
    by = {g["kind"]: g for t in info["trap_info"].values() for g in t["guards"]}
    assert set(by) == {"zone", "codechk"}
    zone = by["zone"]
    assert (zone["at"], tuple(zone["addr"]), zone["fn"]) == (store, (0, 0xFFFF), labels["play"])
    assert as_runs(zone["ok"]) == [(0, 0xFF), (0x200, 0xBFFF), (0xC100, 0xFFFF)]
    assert by["codechk"]["writers"] == [{"pc": store, "entry": "play", "exact": False}]


def test_window_trap_names_its_pair(prefix):
    """The pair defined from a table traps after its high byte's store when its word lies
    within a window of the stack or the I/O page. Its high byte is computed from the table's,
    no copy of it, so the memory map does not bound it."""
    src = PG.TABLE.replace("        LDA hitab,X\n", "        LDA hitab,X\n        ORA #$00\n")
    labels, info = trap_meta(prefix, src + "end:\n")
    k, t, g = only_guard(info)
    store = labels["entry"] + 14
    assert info["traps"][k] == ("deopt", store + 2) and t["kind"] == "window"
    assert (g["at"], g["pair"], tuple(g["addr"])) == (store, PG.PTR, (PG.PTR + 1,) * 2)
    assert as_runs(g["ok"]) == [(0x200, 0xBEFF), (0xC100, 0xFFFF)]


def test_entry_window_trap_is_at_the_entry(prefix):
    """A pair left by another entry is checked at the entry using it: the guard is at that
    entry, and the entry's guards on registers its C entry passes as constants are left out."""
    labels, info = trap_meta(prefix, PG.ZP_PERSIST + "end:\n", ("init", "entry"))
    [t] = [t for t in info["trap_info"].values() if t["pc"] == labels["entry"]]
    [g] = t["guards"]
    assert t["kind"] == "window" and t["kinds"] == ["window"]
    assert (g["at"], g["fn"], g["pair"], g["addr"]) == (labels["entry"],) * 2 + (PG.PTR, None)


def test_object_check_names_its_pair_and_object(prefix):
    """Records read through a pair advanced by I/O past its object trap at the load checking
    the pointer is inside it."""
    labels, info = trap_meta(prefix, TO.FAR, ("init", "play"), init=0, songs=range(4))
    [t] = [t for t in info["trap_info"].values() if t["kind"] == "objchk"]
    [g] = t["guards"]
    load = labels["play"] + 2
    assert (t["pc"], g["at"], g["fn"]) == (load, load, labels["play"])
    assert (g["pair"], g["obj"], g["def"]) == (0xFB, 0, "load")


def test_jam_trap_of_an_instance_names_its_copies(prefix):
    """Copies sharing one trap name each copy's `JAM`."""
    labels, info = trap_meta(prefix, GUARDED)
    _, t, g = only_guard(info)
    jams = [labels[s] - 1 for s in ("s0", "s1", "s2")]
    assert (t["kind"], t["pc"], t["fn"], t["copies"]) == ("jam", jams[0], labels["entry"] + 8, jams)
    assert (g["at"], g["copies"]) == (jams[0], jams)


@pytest.mark.parametrize("resolved", [True, False])
def test_switch_default_trap_names_its_targets(prefix, resolved):
    """A computed transfer's switch default traps with the table's targets and whether the
    code resolves the transfer."""
    edit = ("entry:  AND #$03", "entry:  LDX tlo\n        STX tlo\n        AND #$03")
    src = BRKVEC if resolved else LOHI.replace(*edit)
    labels, info = trap_meta(prefix, src)
    k, t, g = only_guard(info)
    assert info["traps"][k][0] == "ind" and t["kind"] == g["kind"] == "ind"
    at, targets = (labels["brk"], [labels["handler"]]) if resolved else (labels["j0"] - 3, [])
    assert (g["at"], g["targets"], g["resolved"]) == (at, targets, resolved)


BRANCHED = """
"builtin.module"() ({
  "func.func"() <{function_type = (i8, i8) -> (), sym_name = "f"}> ({
  ^bb0(%a: i8, %b: i8):
    %k3 = "arith.constant"() <{value = 3 : i8}> : () -> i8
    %k5 = "arith.constant"() <{value = 5 : i8}> : () -> i8
    %w3 = "arith.constant"() <{value = 3 : i16}> : () -> i16
    %one = "arith.constant"() <{value = true}> : () -> i1
    %x = "arith.addi"(%a, %k5) : (i8, i8) -> i8
    %w = "arith.extui"(%a) : (i8) -> i16
    %s = "arith.subi"(%a, %k3) : (i8, i8) -> i8
    %u = "arith.addi"(%a, %k3) : (i8, i8) -> i8
    %c = "arith.cmpi"(%a, %k3) <{predicate = 8 : i64}> : (i8, i8) -> i1
    %d = "arith.cmpi"(%b, %k5) <{predicate = 8 : i64}> : (i8, i8) -> i1
    {cond}
    {term}
  ^bb1:
    "cf.br"()[^bb3] : () -> ()
  ^bb2:
    "cf.br"()[^bb3] : () -> ()
  ^bb3:
    "func.return"() : () -> ()
  }) : () -> ()
}) : () -> ()
"""
BRANCH_TERM = (
    '"cf.cond_br"(%t)[^bb1, ^bb2] <{operandSegmentSizes = array<i32: 1, 0, 0>}> : (i1) -> ()'
)
SWITCH_TERM = (
    '"cf.switch"(%a)[^bb1, ^bb2] <{case_operand_segments = array<i32: 0>, case_values = '
    "dense<5> : vector<1xi8>, operandSegmentSizes = array<i32: 1, 0, 0>}> : (i8) -> ()"
)


def cmp(pred, a, b):
    """The ``%t`` of ``cmpi`` predicate ``pred`` of ``a`` and ``b``."""
    return f'%t = "arith.cmpi"({a}, {b}) <{{predicate = {pred} : i64}}> : (i8, i8) -> i1'


@pytest.mark.parametrize(
    "cond,value,block,want",
    [
        (cmp(8, "%a", "%k3"), "%a", "bb2", (0, 3)),
        (cmp(8, "%a", "%k3"), "%a", "bb1", (4, 255)),
        (cmp(8, "%a", "%k3"), "%a", "bb3", (0, 255)),
        (cmp(6, "%k3", "%a"), "%a", "bb1", (4, 255)),
        (cmp(0, "%a", "%k3"), "%a", "bb1", (3, 3)),
        (cmp(1, "%a", "%k3"), "%a", "bb1", (0, 255)),
        (cmp(4, "%a", "%k3"), "%a", "bb1", (0, 255)),
        (cmp(8, "%s", "%k5"), "%a", "bb2", (3, 8)),
        (cmp(8, "%u", "%k5"), "%a", "bb2", (0, 255)),
        (cmp(8, "%u", "%k5"), "%u", "bb2", (0, 5)),
        (cmp(8, "%a", "%k3"), "%x", "bb2", (5, 8)),
        (cmp(8, "%a", "%k3"), "%x", "bb3", (0, 255)),
        (cmp(7, "%w", "%w3").replace("(i8, i8)", "(i16, i16)"), "%a", "bb1", (0, 3)),
        ('%t = "arith.xori"(%c, %one) : (i1, i1) -> i1', "%a", "bb1", (0, 3)),
        ('%t = "arith.ori"(%c, %d) : (i1, i1) -> i1', "%b", "bb2", (0, 5)),
        ('%t = "arith.ori"(%c, %d) : (i1, i1) -> i1', "%a", "bb1", (0, 255)),
        ('%t = "arith.andi"(%c, %d) : (i1, i1) -> i1', "%a", "bb1", (4, 255)),
        ("", "%a", "bb2", (5, 5)),
        ("", "%a", "bb1", (0, 255)),
    ],
)
def test_branch_conditions_bound_the_values_they_test(cond, value, block, want):
    """The interval of a value where a block runs meets what the conditions of the branches
    every path to it takes imply."""
    text = BRANCHED.replace("{cond}", cond).replace("{term}", BRANCH_TERM if cond else SWITCH_TERM)
    f = printer.Func(mlir.parse(text).regions[0][0].ops[0], None)
    assert f.iv_at(value, block) == want


def test_or_ed_high_byte_relocates(prefix, tmp_path):
    src = OPJMP.replace("        LDA thi,Y\n        STA site+2\n", "").replace(
        "site:   JMP j0", "        CPY #2\n        BCC site\n        LDY #0\nsite:   JMP j0"
    )
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
    # The cells are one field of three copies: the loop's stores index it by the element
    # number stepped beside the index, with no region tests and no write accessor.
    body = c[c.index("void entry") :]
    assert re.search(r"\bV0_f00\[v\d+\] = 0;", body) and "_LEN" not in body and "wr(" not in c
    assert "uint8_t V0_f00[3] = {V0_f00_INIT};" in c


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
    body = body_of(c, "play")
    assert bool(re.search(r"rdC\d+\(", body)) == span != bool(re.search(r"\brd\(", body))
    assert set(info["traps"].values()) == {("deopt", labels["init"])}


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
    join at the follow, which runs once: no goto. (The memory map's check of the table read,
    whose index the entry's argument starts, is the loop's third exit.)"""
    c, _, info = check(prefix, tmp_path, TWOEXITS, entry_calls([0, 2, 4, 1]))
    assert info["structure"]["gotos"] == 0 and c.count("io_write(0xC000,") == 1


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
    """The ``.c`` is the same at either origin, the ``.h`` not; an even ``A`` replays exactly,
    an odd one makes a pointer of two words' halves (invalid input) and traps at the memory
    map's check of the read through it."""
    outs = []
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(RELOC, org)
        c, h, info = translate(prefix, img, labels, loaded)
        k = machine_guard(info, labels["deref"])
        assert k is not None
        play(tmp_path, c, h, img, labels, entry_calls([0, 2, 2, 0], 2))
        for a in (1, 3):
            play(tmp_path, c, h, img, labels, entry_calls([0, 2, a]), trap=(2, k))
        outs.append((c, h))
    assert outs[0][0] == outs[1][0] and outs[0][1] != outs[1][1]
    assert not re.search(r"0x1[0-9A-F]{3}\b|0x23[0-9A-F]{2}\b", outs[0][0])


def test_replays_load_the_library_just_built(prefix, tmp_path):
    """Each replay runs the C just compiled, not a library loaded before from the same path:
    with ``A`` doubled the reads through the pointer table reach its words, which differ
    between origins, so a library of the other origin's ``.h`` writes the other's bytes."""
    src = RELOC.replace("entry:  AND #$03\n", "entry:  AND #$03\n        ASL A\n")
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(src, org)
        c, h, _ = translate(prefix, img, labels, loaded)
        play(tmp_path, c, h, img, labels, entry_calls(range(4), 2))


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


READBACK = [
    ["LOAD", ["u", 1, 2], [["c", 0x20, 2]], None],
    ["STORE", None, [["c", 0xC000, 2], ["u", 1, 2]], None],
    ["RETURN", None, [None], None],
]


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
    # Another entry reads the result back: a store no load reads is not kept.
    probe = {**fn, "pc": 0x1100, "blocks": [{"pc": 0x1100, "ops": READBACK}]}
    p = cgen.Program(
        [fn, probe],
        [(0x1000, "entry"), (0x1100, "probe")],
        bytes(0x10000),
        set(),
        [W.IO],
        loaded=(0x10, 0x22),
    )
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
    assert '#include "prog.h"' in c and "switch (" in c
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


def entries_of(info):
    """The entries of the C functions."""
    return {f["entry"] for f in info["provenance"]["functions"].values()}


def test_shared_tail_entered_by_jumps_is_each_procedures_code(prefix, tmp_path):
    """A tail two procedures enter by `JMP` and by falling through, which no `JSR` targets,
    is code of each, also past a push and a pull returning to the entry height."""
    _, labels, _ = assemble(SHARED)
    _, _, info = check(prefix, tmp_path, SHARED, entry_calls(range(4), 2))
    assert not entries_of(info) & {labels["shared"], labels["join"], labels["join"] - 3}


def test_dispatch_arms_sharing_a_tail_stay_in_the_dispatching_function(prefix, tmp_path):
    """Two procedures jump through one table into handlers that share tails by `JMP` and
    fall-through: each procedure holds the handlers as its switch's arms, each tail printed in
    the arms entering it (code two functions hold, ending the procedure), with no goto and no
    function of its own; the `.c` is the same at another origin."""
    outs = []
    for org in (0x1000, 0x2300):
        img, labels, loaded = assemble(FXSHARE, org)
        c, h, info = translate(prefix, img, labels, loaded)
        assert entries_of(info) == {labels[n] for n in ("entry", "fx1", "fx2")}
        assert info["structure"]["gotos"] == 0 and c.count("switch (") == 2
        assert c.count("io_write(0xC004, ") == c.count("io_write(0xC005, ") == 2
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, entry_calls(range(4), 2))
        outs.append(c)
    assert outs[0] == outs[1]


def test_tails_one_procedure_holds_follow_the_goto_rule(prefix, tmp_path):
    """The same handlers reached from one procedure: its switch's two tails are joins, the
    one placed after the switch reached by ``break``, the other by a goto from each arm."""
    src = FXSHARE.replace("        JSR fx2\n", "")
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(4), 2), gotos=2)
    assert len(entries_of(info)) == 2 and c.count("io_write(0xC004, ") == 1


def test_shared_tail_a_jsr_targets_is_a_function(prefix, tmp_path):
    """A computed `JSR`'s arm another procedure jumps into is a procedure: a function both
    reach, the dispatch's other arms inline in it."""
    _, labels, _ = assemble(OPSHARE)
    calls = [("entry", a) for a in (2, 5, 4, 1, 5, 0, 4, 5)]
    _, _, info = check(prefix, tmp_path, OPSHARE, calls)
    entries = entries_of(info)
    assert labels["h2"] in entries and not entries & {labels["h0"], labels["h1"]}


def test_multi_exit_loop_breaks_to_its_follow(prefix, tmp_path):
    # The inner loop's two edges to `none` break to its follow; its exit to `found` is the
    # goto; `none`'s jump past `found` to the latch `next` continues the outer do/while.
    c, _, _ = check(prefix, tmp_path, EXITS, entry_calls(range(4), 2), gotos=1)
    assert c.count("break;") == 2 and "continue;" in c and "} while (" in c


def test_jump_from_nested_loops_to_a_latch_is_a_goto_to_its_label(prefix, tmp_path):
    """NESTED_LATCH: the do/while's latch (`sub`, inlined, returning to `latch`) is entered
    from inside two nested counted loops, where ``continue`` is the inner loop's: `BPL done`
    is a goto to the label ending the do body, before its condition."""
    c, _, info = check(prefix, tmp_path, NESTED_LATCH, entry_calls(range(2), 4), gotos=1)
    _, labels, _ = assemble(NESTED_LATCH)
    assert [e[0] for e in info["provenance"]["gotos"]] == [labels["done"] - 4]
    assert re.search(r"goto (L\d+);(?s:.*)\n\s*\1:;\n\s*\} while \(", c)


def test_unlabeled_gotos_are_found_per_function(tmp_path):
    text = "void f(void) {\n  goto L1;\nL2:;\n}\nvoid g(void) {\n  L1:;\n  goto L2;\n}\n"
    assert cmetrics.unlabeled(text) == ["L1", "L2"]
    assert not cmetrics.unlabeled(text.replace("L2:", "L1:", 1).replace("goto L2", "goto L1"))
    (tmp_path / "t.c").write_text(text)
    assert cmetrics.main(["--labels", str(tmp_path / "t.c")]) == 1
    assert cmetrics.main([str(tmp_path / "t.c")]) == 0


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


#: OPJMP with a second entry storing into one of the jump's operand bytes by an index: the
#: operand cells stay bytes of a region.
OPJMP_RESET = OPJMP.replace(
    "tlo:",
    """reset:  AND #$01
        TAX
        LDA #$00
        STA site+1,X
        RTS
tlo:""",
)


def test_operand_stores_of_a_switched_jump_are_dead(prefix, tmp_path):
    """A computed ``JMP`` that switches on its index reads its operand cells no more; no other
    load reads them, so every store into them (the dispatch's, another entry's by an index)
    is dead and dropped with the cells: no array holds them."""
    img, labels, loaded = assemble(OPJMP_RESET)
    c, h, info = translate(prefix, img, labels, loaded, entries=("entry", "reset"))
    [k] = [k for k, (org, _) in enumerate(info["regions"]) if org == labels["site"] + 1]
    storage = re.search(r"^#define CGEN_STORAGE (.*)$", h, re.M)[1]
    assert re.search(r"switch \(v\d+\)", c) and f"R{k}" not in storage + c
    assert info["state"]["cells"]["bytes"].get("indexed", 0) == 0
    assert re.search(r"void reset\(uint8_t a\) \{ \(void\)a; \}", c)
    play(tmp_path, c, h, img, labels, [(n, a) for a in range(8) for n in ("entry", "reset")])


def test_switch_from_a_jump_table(prefix, tmp_path):
    c, _, _ = check(prefix, tmp_path, LOHI, entry_calls(range(4)))
    assert "switch (" in c and "case CODE_ORG" not in c and "default:" in c
    assert [int(k) for k in re.findall(r"case (\d+):", c)] == [0, 1, 2]


#: LOHI with its handler of index 0 running into the default's (index 3), placed after it.
LOHIFALL = LOHI.replace(
    """        RTS
j1:""",
    """j3:     STY $C053
        RTS
j1:""",
).replace(
    """j3:     STY $C053
        RTS
tlo:""",
    "tlo:",
)


def test_switch_arm_running_into_another_is_followed_by_it(prefix, tmp_path):
    """The arms print in the order of their targets in the machine code, the case labels the
    index's values: the handler of index 0 runs into the default's after it, no ``goto``."""
    outs = []
    for org in (0x1000, 0x2300):
        img, labels, loaded = assemble(LOHIFALL, org)
        c, h, info = translate(prefix, img, labels, loaded)
        assert info["structure"]["gotos"] == 0 and "fallthrough" in c
        assert re.findall(r"(case \w+|default):", c) == ["case 0", "default", "case 1", "case 2"]
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, entry_calls(range(4), 2))
        outs.append(c)
    assert outs[0] == outs[1]


@pytest.mark.parametrize(
    "edit",
    [
        ("        LDA thi,Y", "        INY\n        LDA thi,Y"),
        ("site:   JMP j0", "        CPY #2\n        BCC site\n        LDY #0\nsite:   JMP j0"),
    ],
    ids=["columns-apart", "index-joined"],
)
def test_switch_on_the_target_without_one_index(prefix, tmp_path, edit):
    """A jump whose target bytes are not indexed by one value (two columns read at unlike
    values, or an index held on one path and overwritten on another) switches on the target
    address."""
    c, _, info = check(prefix, tmp_path, OPJMP.replace(*edit), entry_calls([0, 1, 2, 2, 0]))
    assert "case CODE_ORG" in c and not re.search(r"case \d+:", c)
    assert len(next(iter(info["jumps"].values()))) >= 3


LOHIX = """
entry:  AND #$03
        TAX
        LDA tlo,X
        STA ptr
        LDA thi,X
        STA ptr+1
        LDX #$00
        JMP (ptr)
j0:     STX $C050
        RTS
j1:     LDA #$11
        STA $C051
        RTS
j2:     LDA $C052
        STA $C053
        RTS
j3:     RTS
tlo:    .byte lo(j0),lo(j1),lo(j2),lo(j3)
thi:    .byte hi(j0),hi(j1),hi(j2),hi(j3)
ptr:    .byte 0,0
end:
"""

OPJSR = """
entry:  AND #$03
        TAY
        LDA tlo,Y
        STA site+1
        LDA thi,Y
        STA site+2
site:   JSR j0
        STA $C000
        RTS
j0:     LDA #$10
        RTS
j1:     LDA $C001
        RTS
j2:     STY $C002
        RTS
j3:     LDA #$13
        RTS
tlo:    .byte lo(j0),lo(j1),lo(j2),lo(j3)
thi:    .byte hi(j0),hi(j1),hi(j2),hi(j3)
end:
"""

OPJSRS = """
entry:  AND #$03
        TAY
        LDA tlo,Y
        STA s1+1
        STA s2+1
        LDA thi,Y
        STA s1+2
        STA s2+2
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
h3:     LDA $C003
        RTS
tlo:    .byte lo(h0),lo(h1),lo(h2),lo(h3)
thi:    .byte hi(h0),hi(h1),hi(h2),hi(h3)
end:
"""

TWOLEVEL = """
entry:  AND #$07
        TAX
        LDY seq,X
        LDA tlo,Y
        STA ptr
        LDA thi,Y
        STA ptr+1
        LDY #$00
        JMP (ptr)
j0:     STX $C050
        RTS
j1:     STX $C051
        RTS
j2:     STX $C052
        RTS
seq:    .byte 2,0,1,1,0,2,2,1
tlo:    .byte lo(j0),lo(j1),lo(j2)
thi:    .byte hi(j0),hi(j1),hi(j2)
ptr:    .byte 0,0
end:
"""

# The index register written by one of two instructions on the way to the jump.
TWO_WRITES = (
    "        LDY #$00\n",
    "        CPX #$04\n        BCC low\n        LDY #$01\n        JMP go\nlow:    LDY #$00\ngo:\n",
)

BRCALC = """
entry:  AND #$03
        ASL A
        ASL A
        STA br+1
        CLC
br:     BCC br+2
        STA $C000
        RTS
        STA $C001
        RTS
        STA $C002
        RTS
        STA $C003
        RTS
end:
"""


@pytest.mark.parametrize(
    "src,cases",
    [
        (LOHIX, [0, 1, 2]),
        (W.JUMPTAB + "end:\n", [0, 1, 2]),
        (OPJSR, [0, 1, 2]),
        (BRTAB, [0, 1]),
        (TWOLEVEL, [0, 1]),
        (TWOLEVEL.replace(*TWO_WRITES), [0, 1]),
        (OPJSRS, [0, 1, 2]),
    ],
    ids=[
        "carried",
        "pushed",
        "operand-jsr",
        "branch-offset",
        "two-level",
        "two-level-written-apart",
        "dispatch-function",
    ],
)
def test_computed_transfer_switches_on_its_index(prefix, tmp_path, src, cases):
    """A transfer whose target bytes are columns of one table indexed by one value switches
    on that value: a register holding it, or a variable carrying it past the register's
    overwrite (an index read from data included), the same for every origin."""
    outs = []
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(src, org)
        c, h, info = translate(prefix, img, labels, loaded)
        assert len(set(map(tuple, info["jumps"].values()))) == 1 and not info["traps"]
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, entry_calls(range(8), 2))
        outs.append(c)
    assert outs[0] == outs[1] and "CODE_ORG" not in outs[0]
    assert [int(k) for k in re.findall(r"case (\w+):", outs[0])] == cases


def carried_stages(regs):
    """Stage ``i`` per register ``r`` of ``regs``: ``LDA save`` shifted right ``2i`` times;
    ``AND #3; TAr; LDA lo,r; STA ptr; LDA hi,r; STA ptr+1; LDr #i; JMP (ptr)`` to four arms,
    each storing ``r`` and a constant and going on to the next stage."""
    out = ["entry:  STA save"]
    for i, r in enumerate(regs):
        out += ["        LDA save"] + ["        LSR A"] * (2 * i)
        out += [f"        AND #$03\n        TA{r}\n        LDA t{i}lo,{r}\n        STA ptr"]
        out += [
            f"        LDA t{i}hi,{r}\n        STA ptr+1\n        LD{r} #${i:02X}\n        JMP (ptr)"
        ]
        for j in range(4):
            out += [f"s{i}a{j}:  LDA #${16 * i + j:02X}\n        ST{r} $C0{4 * i + j:02X}"]
            out += [f"        STA $C0{0x40 + 4 * i + j:02X}\n        JMP s{i + 1}"]
        out.append(f"s{i + 1}:")
    out.append("        RTS")
    for i in range(len(regs)):
        for half in ("lo", "hi"):
            out.append(f"t{i}{half}:   .byte " + ",".join(f"{half}(s{i}a{j})" for j in range(4)))
    return "\n".join(out + ["ptr:    .byte 0,0", "save:   .byte 0", "end:"]) + "\n"


def carried_copies(n):
    """``n`` copies of ``LDA $1k; AND #3; TAX; LDA lo,X; STA op+1; LDA hi,X; STA op+2; LDX #0;
    op: JMP`` to four arms storing a constant and going on to the next copy, copy ``k``'s
    bytes at a stride, its tables its own."""
    out = ["entry:  " + "\n        ".join(f"STA $1{k}" for k in range(n))]
    for k in range(n):
        out += [f"v{k}:     LDA $1{k}\n        AND #$03\n        TAX\n        LDA tl{k},X"]
        out += [f"        STA j{k}+1\n        LDA th{k},X\n        STA j{k}+2\n        LDX #$00"]
        out.append(f"j{k}:     JMP a{k}0")
        for j in range(4):
            out += [f"a{k}{j}:    LDA #${j + 1:02X}\n        STA $C0{8 * k + j:02X}"]
            out.append(f"        JMP v{k + 1}")
        for half in ("lo", "hi"):
            out.append(f"t{half[0]}{k}:    .byte " + ",".join(f"{half}(a{k}{j})" for j in range(4)))
    return "\n".join(out + [f"v{n}:     RTS", "end:"]) + "\n"


@pytest.mark.parametrize("regs", ["XY", "XYX"])
def test_transfers_each_switch_on_their_carried_index(prefix, tmp_path, regs):
    """Transfers of one function each switching on an index carried past its register's
    write: each a switch over its own index, the same for every origin."""
    outs = []
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(carried_stages(regs), org)
        c, h, info = translate(prefix, img, labels, loaded)
        assert len(info["jumps"]) == len(regs) and not info["traps"]
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, entry_calls(range(64)))
        outs.append(c)
    assert outs[0] == outs[1] and "CODE_ORG" not in outs[0]
    assert len(set(re.findall(r"switch \((.+)\) \{", outs[0]))) == len(regs)
    assert [int(k) for k in re.findall(r"case (\w+):", outs[0])] == [0, 1, 2] * len(regs)


def test_copies_switch_on_their_carried_index(prefix, tmp_path):
    """Copies merged into an instance function, each carrying its index past its register's
    write: the instance function switches on the index."""
    c, _, info = check(prefix, tmp_path, carried_copies(3), entry_calls(range(16), 2))
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    assert not info["traps"] and "CODE_ORG" not in c
    assert [int(k) for k in re.findall(r"case (\w+):", c)] == [0, 1, 2]


# `Y` read from `sel,X` on one path and from a zero-page byte on the other, each path loading
# the table pair with it and writing `Y` before the paths join at the jump.
MERGED = """
entry:  STA $02
        AND #$03
        TAX
        LDA $02
        BMI byzp
        LDY sel,X
        LDA tlo,Y
        STA ptr
        LDA thi,Y
        STA ptr+1
        LDY #$00
        JMP go
byzp:   AND #$03
        STA $03
        LDY $03
        LDA tlo,Y
        STA ptr
        LDA thi,Y
        STA ptr+1
        STA $03
        LDY #$01
go:     JMP (ptr)
j0:     STX $C050
        STY $C054
        RTS
j1:     LDA #$11
        STA $C051
        RTS
j2:     LDA $C052
        STA $C053
        RTS
j3:     STY $C055
        RTS
sel:    .byte 2,0,3,1
tlo:    .byte lo(j0),lo(j1),lo(j2),lo(j3)
thi:    .byte hi(j0),hi(j1),hi(j2),hi(j3)
ptr:    .byte 0,0
end:
"""

# `X` indexes the table pair and is written in the caller; the jump is in the subroutine,
# called twice.
CALLED = """
entry:  AND #$03
        TAX
        LDA tlo,X
        STA ptr
        LDA thi,X
        STA ptr+1
        LDX #$00
        JSR sub
        STA $C000
        JSR sub
        STA $C001
        RTS
sub:    JMP (ptr)
j0:     STX $C050
        RTS
j1:     LDA #$11
        RTS
j2:     LDA $C052
        RTS
j3:     STX $C053
        RTS
tlo:    .byte lo(j0),lo(j1),lo(j2),lo(j3)
thi:    .byte hi(j0),hi(j1),hi(j2),hi(j3)
ptr:    .byte 0,0
end:
"""

# [`CALLED`] with the subroutine calling itself once before its jump.
RECURSIVE = (
    CALLED.replace(
        "        JSR sub\n",
        "        LDA #$01\n        STA depth\n        JSR sub\n",
    )
    .replace(
        "sub:    JMP (ptr)\n",
        "sub:    LDA depth\n        BEQ go\n        DEC depth\n        JSR sub\ngo:     JMP (ptr)\n",
    )
    .replace("ptr:    .byte 0,0\n", "ptr:    .byte 0,0\ndepth:  .byte 0\n")
)

# The write in a subroutine called before the jump's subroutine, which calls it again from an
# arm of the jump.
RERUN = """
entry:  AND #$03
        TAX
        LDA tlo,X
        STA ptr
        LDA thi,X
        STA ptr+1
        JSR clr
        JSR sub
        STA $C000
        RTS
clr:    LDX #$00
        RTS
sub:    JMP (ptr)
j0:     STX $C050
        RTS
j1:     LDA #$11
        RTS
j2:     LDA $C052
        RTS
j3:     JSR clr
        STX $C053
        RTS
tlo:    .byte lo(j0),lo(j1),lo(j2),lo(j3)
thi:    .byte hi(j0),hi(j1),hi(j2),hi(j3)
ptr:    .byte 0,0
end:
"""


@pytest.mark.parametrize("src,values", [(MERGED, range(256)), (CALLED, range(8))])
def test_carried_index_merged_or_called_switches_on_it(prefix, tmp_path, src, values):
    """An index written on each of two paths after loading the table pair, or written in a
    caller of the jump's function: a switch over the index, the same for every origin."""
    outs = []
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(src, org)
        c, h, info = translate(prefix, img, labels, loaded)
        assert len(info["jumps"]) == 1 and not info["traps"]
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, entry_calls(values))
        outs.append(c)
    assert outs[0] == outs[1] and "CODE_ORG" not in outs[0]
    assert sorted(int(k) for k in re.findall(r"case (\w+):", outs[0])) == [0, 1, 2]


@pytest.mark.parametrize("src", [RECURSIVE, RERUN], ids=["recursive", "rerun"])
def test_carried_index_recursive_or_rerun_switches_on_addresses(prefix, tmp_path, src):
    """The jump's function on a call cycle, or calling the write again: a switch over code
    addresses."""
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(8)))
    assert len(info["jumps"]) == 1 and "CODE_ORG" in c


def test_target_by_arithmetic_switches_on_the_address(prefix, tmp_path):
    """A branch offset computed from the index rather than read from a table switches on the
    target address."""
    c, _, info = check(prefix, tmp_path, BRCALC, entry_calls(range(4), 2))
    [targets] = info["jumps"].values()
    assert len(targets) == 4 and c.count("case CODE_ORG + ") == 3


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


def test_condition_literals_keep_their_evaluation_order():
    """A cover's literals follow the order the conditions were declared in, as they are
    evaluated (a literal may carry the statements before its test), whatever levels the BDD
    manager's reordering moves the variables to."""
    conds = structure.Conds()
    keys = [("c", f"%{i}") for i in range(4)]
    xs = [conds.var(k) for k in keys]
    f = xs[0] & xs[1] & ~xs[2] & xs[3]
    conds.bdd.reorder({f"x{i}": 3 - i for i in range(4)})
    assert [conds.bdd.level_of_var(f"x{i}") for i in range(4)] == [3, 2, 1, 0]
    assert conds.cover(f, conds.bdd.true) == [[(k, i != 2) for i, k in enumerate(keys)]]
    conds.close()


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
    """Each copy's own operand cell is the copy's element of one field array, indexed by the
    copy argument (no table of offsets, no multiplication, no ``rd``/``wr``)."""
    c, _, info = check(prefix, tmp_path, VOICES, entry_calls(range(4), 6))
    assert not re.search(r"\b(?:rd|wr)\(|0x18", c)
    assert len(re.findall(r"\bV0_f00\[v1\]", c)) == 3
    assert info["state"]["cells"] == {"scalars": 0, "fields": 3, "unread": 0, "bytes": {}}
    at = info["state"]["vars"]
    assert sorted(at) == [f"V0_f00[{e}]" for e in range(3)]
    assert sorted(at.values()) == list(at.values()) and set(at.values()) <= set(
        info["provenance"]["cells"]
    )


PATCHED = """
entry:  CMP #$80
        BCC op
        INC op+1
op:     LDA #$10
        STA $C000
        RTS
end:
"""
BOUND = """
entry:  CMP #$04
        BCC run
        AND #$03
        CLC
        ADC #$01
        STA lim+1
        RTS
run:    LDX #$00
loop:   TXA
        STA $C000
        INX
lim:    CPX #$02
        BCC loop
        RTS
end:
"""
STEP = """
entry:  LDX #$0E
bump:   INC c0+1,X
        TXA
        SEC
        SBC #$07
        TAX
        BPL bump
c0:     LDA #$01
        STA $C000
        NOP
        NOP
c1:     LDA #$02
        STA $C001
        NOP
        NOP
c2:     LDA #$03
        STA $C002
        NOP
        NOP
        RTS
end:
"""


def test_patched_operand_is_a_scalar(prefix, tmp_path):
    """An operand cell every access reaches at its constant address is a scalar named by its
    offset from the code's origin, its initial value in the header: the same ``.c`` at
    another origin."""
    c, h, info = check(prefix, tmp_path, PATCHED, entry_calls([0, 0x80, 0x81, 1], 3))
    assert "uint8_t S_8 = S_8_INIT;" in c and "S_8 = S_8 + 1;" in c and "R0" not in c
    assert "#define S_8_INIT 0x10" in h
    assert info["state"]["cells"] == {"scalars": 1, "fields": 0, "unread": 0, "bytes": {}}
    assert info["state"]["vars"] == {"S_8": 0x1008}
    img, labels = W.assemble(PATCHED, 0x2345)
    assert translate(prefix, img, labels, (0x2345, labels["end"]))[0] == c


def test_patched_compare_immediate_bounds_a_loop(prefix, tmp_path):
    """A compare immediate an earlier call patches is a scalar the loop it bounds tests as
    any variable: a structured loop, no goto."""
    c, _, info = check(prefix, tmp_path, BOUND, entry_calls([5, 0, 7, 1, 6, 2, 4, 3], 2))
    assert info["provenance"]["loops"] and re.search(r"S_15 > \(uint8_t\)\(v\d+ \+ 1\)", c)
    assert "S_15 = (a & 3) + 1;" in c


def test_copies_indexed_by_a_counted_loop_step_an_element(prefix, tmp_path):
    """Operand cells at a stride a counted loop indexes are one field over the copies: the
    loop steps the element number beside its index, and each copy reads its own element."""
    c, h, info = check(prefix, tmp_path, STEP, entry_calls([0], 4))
    loop = re.search(
        r"uint8_t (v\d+) = 2;\n *for \(;;\) \{\n *V0_f00\[\1\] = V0_f00\[\1\] \+ 1;", c
    )
    assert loop and f"{loop.group(1)} = {loop.group(1)} - 1;" in c
    assert "#define V0_f00_INIT 0x01, 0x02, 0x03" in h and "V0_f00[v1]" in c
    assert info["state"]["cells"]["fields"] == 3


def test_loop_over_some_copies_offsets_its_element(prefix, tmp_path):
    """A loop reaching the last copies of a field indexes it by its element number plus the
    first copy it reaches."""
    src = STEP.replace("BPL bump", "BNE bump").replace(
        "entry:  LDX", "entry:  DEC c0+1\n        LDX"
    )
    c, _, info = check(prefix, tmp_path, src, entry_calls([0], 4))
    assert re.search(r"V0_f00\[(v\d+) \+ 1\] = V0_f00\[\1 \+ 1\] \+ 1;", c)
    assert "V0_f00[0] = V0_f00[0] - 1;" in c and info["state"]["cells"]["fields"] == 3


def test_scalar_names_follow_the_code_origin():
    """A scalar is named by its offset from the code span's origin, else the loaded image's,
    else its address."""
    mod = SimpleNamespace(mem=SimpleNamespace(code_span=(0x1000, 0x1100), loaded=(0x800, 0x2000)))
    names = [printer.Module.var_name(mod, a) for a in (0x1023, 0x0900, 0x0040)]
    assert names == ["S_23", "S_I100", "S_A0040"]


def test_copies_at_uneven_distances_index_a_table(prefix, tmp_path):
    """Copies whose cells are not at one stride are the elements of one field array all the
    same, the same at another origin."""
    src = VOICES.replace("JMP v1\n", "JMP v1\n        .byte 0, 0, 0\n")
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(4), 6))
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    assert len(re.findall(r"\bV0_f00\[v1\]", c)) == 3 and "0x1B" not in c
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


OPERAND_PTR = r"rdC\d+\(S_\w+ \| \(\(uint16_t\)S_\w+ << 8\), "
#: A direct access of the span at an operand init leaves constant, named by the ``.h``.
#: A byte variable: a cell init sets, promoted or in its region.
BYTE = r"S_\w+|R\d+\[\w+\]"
OPERAND_K = r"\b[CR]\d+\[\(uint16_t\)\(\(K_\w+ \| \(\(uint16_t\)K_\w+ << 8\)\) \+ "


@pytest.mark.parametrize("src", ["RUNREL", "RELOCATE"])
def test_operands_init_relocates_by_a_constant_base_are_constant(prefix, tmp_path, src):
    """A relocator storing each operand's two bytes, a base word plus an offset, through a
    ``(zp),Y`` pointer read from a table of patch sites runs, and the other entry is lifted
    from what it leaves: init leaves the operands one value for every ``A`` and no store of
    the entry may write them, so they are constants of its code and its accesses direct;
    init is translated, its stores through the pointer writing the operands' storage."""
    img, labels, loaded = assemble(globals()[src])
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", a) for a in range(4)] * 2)
    ops = {labels[o] + k for o in ("o0", "o1") for k in (1, 2)}
    body = body_of(c, "play")
    assert ops <= set(info["fixed"]) and "codechk(" not in c and info["init"] == "translated"
    assert re.search(OPERAND_K, body) and not re.search(r"rdC|\bS_", body)
    assert "CGEN_CP_N" not in h and not DISPATCH.search(body_of(c, "init"))


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


def probed(src):
    """``src`` with init first storing a volatile byte it reads: its run depends on the
    device, so it is replayed, not translated."""
    head = "init:   PHA\n        LDA $C030\n        STA probe\n        PLA\n       "
    return src.replace("init:", head, 1).replace("end:", "probe:  .byte 0\nend:")


def songs(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    prefix, tmp_path, src, values, init=0, declared=None
):
    """``(c, info, labels)`` of ``src``'s ``init`` and ``play`` translated with ``init``
    (called with the ``declared`` songs, default the range of ``values``), replayed from a
    fresh image per song ``a`` of ``values``: ``init(a)``, then plays."""
    img, labels, loaded = assemble(src)
    declared = range(min(values), max(values) + 1) if declared is None else declared
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


@pytest.mark.parametrize("probe", [False, True])
def test_play_is_lifted_from_each_songs_state(prefix, tmp_path, probe):
    """Init relocating the other entry's operands by a constant word and storing ``A`` is
    run for every value: the operands are constants, the entry's accesses through them
    direct, and the byte holding ``A`` a variable. Init is translated, unless its run reads
    the device: then the init function traps an undeclared value, replays init's I/O write
    and copies each value's state from the header's literals into the variable."""
    src = probed(INITREL) if probe else INITREL
    c, info, labels = songs(prefix, tmp_path, src, (0, 1, 5))
    ops = {labels[o] + k for o in ("o0", "o1") for k in (1, 2)}
    assert ops <= set(info["fixed"]) and labels["song"] not in info["fixed"]
    assert "codechk(" not in c
    init = body_of(c, "init")
    replay = "io_write(IO_AT[i], IO_VAL[i]);" in init and "CP_DST[i][j] = CP_SRC[i][j];" in init
    assert replay == probe and info["init"] == ("replayed" if probe else "translated")
    body = body_of(c, "play")
    assert re.search(OPERAND_K, body) and not re.search(OPERAND_PTR, body)


#: Init setting a pointer pair and a count the other entry reads; ``{w}`` more of its code.
PTRSET = """
init:   LDA #lo(tab)
        STA $FB
        LDA #hi(tab)
        STA $FC
        LDA #$07
        STA cnt
        RTS
play:   AND #$03
        TAY
        LDA ($FB),Y
        STA $C000
        LDA cnt
        STA $C001
{w}        RTS
cnt:    .byte 0
tab:    .byte 1,2,3,4
end:
"""


@pytest.mark.parametrize(
    "w, held",
    [
        ("", ("$FB", "$FC", "cnt")),
        ("        DEC cnt\n", ("$FB", "$FC")),
        ("        STA $FD\n        LDA $C010\n        STA $FE\n        STA ($FD),Y\n", ()),
    ],
    ids=["constant", "written", "unknown"],
)
def test_bytes_init_leaves_constant(prefix, tmp_path, w, held):
    """The bytes init stores, the same for every ``A``, that no store of the other entry may
    write are constants of its lift: a pointer pair a constant address (its read through it
    direct), a count a constant it reads by name. A byte the entry writes, and every byte
    where a store of the entry of unknown address may reach it, stays a variable."""
    c, info, labels = songs(prefix, tmp_path, PTRSET.format(w=w), (0, 1))
    assert set(info["fixed"]) == {int(x[1:], 16) if x[0] == "$" else labels[x] for x in held}
    body = body_of(c, "play")
    assert bool(re.search(r"\bS_FB\b|rdC", body)) == (not held)
    assert bool(re.search(r"io_write\(0xC001, K_\w+\)", body)) == ("cnt" in held)


#: Init setting, from its header, an operand into a table, a pointer pair into it, a loop
#: count and the operand of a store into the table ending the program; ``{0}``-``{3}`` the
#: header's offsets and count.
FOLDS = """
init:   LDA hdr
        STA o0+1
        LDA hdr+1
        STA o0+2
        LDA hdr+2
        STA $FB
        LDA hdr+3
        STA $FC
        LDA hdr+4
        STA n+1
        LDA hdr+5
        STA o1+1
        LDA hdr+6
        STA o1+2
        RTS
play:   AND #$03
        TAY
o0:     LDA $FFFF,Y
        STA $C000
        LDA ($FB),Y
        STA $C001
        LDX #$00
lp:     STX $C002
        INX
n:      CPX #$00
        BNE lp
        LDY $C010
        TXA
o1:     STA $FFFF,Y
        RTS
hdr:    .byte lo(tab+{0}), hi(tab+{0}), lo(tab+{1}), hi(tab+{1}), {2}, lo(last+{3}), hi(last+{3})
tab:    .byte 1,2,3,4,5,6,7,8,9,10,11,12
last:   .byte 0,0,0,0,0,0
end:
"""


@pytest.mark.parametrize("probe", [False, True])
def test_constants_init_leaves_are_named_by_the_header(prefix, tmp_path, probe):
    """Programs of the same code whose init leaves other constants (operands and a pointer
    pair at other offsets into a table, another loop count, a store's operand at another
    offset into the table ending the program, so another bound for its index guard) give
    the same ``.c``: each constant is a name the ``.h`` defines, the accesses direct."""
    texts, headers = set(), set()
    for k, offs in enumerate(((0, 2, 3, 1), (1, 3, 5, 2))):
        src = FOLDS.format(*offs)
        img, labels, loaded = assemble(probed(src) if probe else src)
        c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
        (tmp_path / str(k)).mkdir()
        play(tmp_path / str(k), c, h, img, labels, [("init", 0)] + [("play", a) for a in range(4)])
        ops = {labels[o] + j for o in ("o0", "o1") for j in (1, 2)} | {labels["n"] + 1}
        assert ops | {0xFB, 0xFC} <= set(info["fixed"])
        assert info["init"] == ("replayed" if probe else "translated")
        body = body_of(c, "play")
        assert re.search(OPERAND_K, body) and re.search(r"\bG_\w+ < ", body)
        assert not re.search(r"rdC|\bS_|\brd\(", body)
        texts.add(c)
        headers.add(h)
    assert len(texts) == 1 and len(headers) == 2


def held_bytes(h):
    """The addresses the arrays the ``.h`` defines hold."""
    ends = re.findall(r"#define ([RC]\d+)_ORG (0x[0-9A-F]+)\n#define \1_LEN (\d+)", h)
    return {a for _, org, n in ends for a in range(int(org, 16), int(org, 16) + int(n))}


#: A count init patches into an immediate, which the other entry also loads by its
#: address; ``{w}`` more of its code.
COUNT = """
init:   LDA #$05
        STA n+1
        RTS
play:   LDX #$00
lp:     STX $C000
        INX
n:      CPX #$00
        BNE lp
        LDA n+1
        STA $C001
{w}        RTS
end:
"""


@pytest.mark.parametrize("indexed", [False, True])
def test_bytes_init_leaves_constant_are_held_where_read(prefix, tmp_path, indexed):
    """A byte a replayed init leaves constant that the other entry reads only by its
    address is a name the ``.h`` defines and no array holds it; read also by an access not
    at its address (an index over the instruction holding it), it is held."""
    w = "        AND #$01\n        TAY\n        LDA n,Y\n        STA $C002\n" if indexed else ""
    img, labels, loaded = assemble(probed(COUNT.format(w=w)))
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", a) for a in range(4)])
    a = labels["n"] + 1
    assert a in info["fixed"] and info["init"] == "replayed"
    assert re.search(r"io_write\(0xC001, K_\w+\)", body_of(c, "play"))
    assert (a in held_bytes(h)) == indexed


def test_init_relocating_by_the_song_leaves_pointer_cells(prefix, tmp_path):
    """Init computing an operand from ``A`` writes both its bytes: the low byte's value
    differs between values of ``A``, so the operand is a variable the other entry reads
    through as a pointer, its high byte too though init leaves it one value. Its words are
    init's, the song's: its read is a plain read of the song's data block, the memory map
    checking it there; the flag init leaves one value for every ``A`` is a constant."""
    c, info, labels = songs(prefix, tmp_path, INITA, range(4))
    assert {labels["o0"] + 1, labels["o0"] + 2} <= set(info["provenance"]["cells"])
    assert info["fixed"] == [labels["flag"]]
    body = body_of(c, "play")
    assert re.search(r"\(S_\w+ \| \(\(uint16_t\)S_\w+ << 8\)\)", body)
    assert "rdC" not in body and machine_guard(info, labels["o0"]) is not None
    assert "K_I" in body


def test_song_range_is_checked_not_assumed(prefix, tmp_path):
    """The songs the host calls init with are checked at init, and assumed nowhere: the
    pointer each song init takes leaves, which play never stores, seeds the pair, songs
    pointing at I/O among them, so the memory map checks its reads for every count of songs,
    the ``.c`` the same, the header holding each song's values; a song outside the range
    traps."""
    c, info, labels = songs(prefix, tmp_path, SONGINIT, (0, 1), declared=range(2))
    guard = r"if \(\(uint8_t\)\(a - CGEN_SONG0\) >= CGEN_SONGS\) \{\s*trap\("
    machine = [g for t in info["trap_info"].values() for g in t["guards"] if g["def"] == "machine"]
    assert machine and re.search(guard, c)
    img, labels, loaded = assemble(SONGINIT)
    one = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=range(1))
    assert one[0] == c and "#define CGEN_SONGS 1" in one[1]
    four = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=range(4))
    assert four[0] == c and "#define CGEN_SONGS 4" in four[1]
    with pytest.raises(AssertionError, match="trap"):
        songs(prefix, tmp_path / "out", SONGINIT, (2,), declared=range(2))


def test_song_values_are_a_range(prefix):
    """The songs the host calls init with are a range of values, its bounds in the header."""
    img, labels, loaded = assemble(SONGINIT)
    with pytest.raises(ValueError, match="range"):
        translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=(0, 2))
    _, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=range(1, 3))
    assert "#define CGEN_SONG0 0x01" in h and "#define CGEN_SONGS 2" in h


@pytest.mark.parametrize(
    "order",
    [[1, 2, 3, 4, 5, 6, 7, 8], [0x81, 2, 0x90, 0xA0, 5, 6, 7, 8], [0x81] * 7 + [3]],
    ids=["plain", "commands", "skips"],
)
def test_init_reading_song_data_leaves_variables(prefix, tmp_path, order):
    """Init reads each voice's first order byte with the routine play reads them with (song
    data), skipping commands: the positions and patterns it leaves are variables, so play's
    function is the same for every order list and the ``.c`` for every song count, the header
    holding their values (init's own bounds follow the data it is lifted from)."""
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
    assert len(texts) == 1 and {body_of(c, "play") for c in texts} == {body_of(base, "play")}


def test_init_io_writes_song_data_decides_are_replayed(prefix, tmp_path):
    """An I/O write the data makes conditional is one of the writes the header replays in
    order of an init that is replayed: the same ``.c`` for either data."""
    texts = set()
    for first, writes in ((1, "0xC018"), (0x81, "0xC018, 0xC010")):
        img, labels, loaded = assemble(probed(SONGIO), patch={"order": [first]})
        c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
        assert f"#define CGEN_IO_AT {writes}\n" in h
        (tmp_path / str(first)).mkdir()
        calls = [("init", 0)] + [("play", k) for k in range(4)]
        play(tmp_path / str(first), c, h, img, labels, calls)
        texts.add(c)
    assert len(texts) == 1


def test_init_io_writes_are_replayed_in_order(prefix, tmp_path):
    """A replayed init's I/O writes are replayed in their order (a clearing loop's one by
    one), and the bytes it stores, which the other entry never writes, are constants it
    reads by name, their values in the header; the translated init makes them as its code
    does."""
    c, info, _ = songs(prefix, tmp_path / "t", IOCLEAR, (0,), declared=(0,))
    assert info["init"] == "translated" and "io_write(0xC020, 0x11);" in body_of(c, "init")
    c, _, _ = songs(prefix, tmp_path, probed(IOCLEAR), (0,), declared=(0,))
    img, labels, loaded = assemble(probed(IOCLEAR))
    h = translate(prefix, img, labels, loaded, ("init", "play"), init=0)[1]
    assert "#define CGEN_IO_AT 0xC013, 0xC012, 0xC011, 0xC010, 0xC020\n" in h
    assert "#define CGEN_IO_VAL 0x00, 0x00, 0x00, 0x00, 0x11\n" in h
    assert len(re.findall(r"io_write\(0xC00[01], K_", c)) == 2


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
        LDY #$07
copy:   LDA ($FB),Y
        STA buf,Y
        DEY
        BPL copy
        RTS
play:   AND #$07
        TAX
        LDA buf,X
        STA $C000
        RTS
srclo:  .byte lo(s0), lo(s1)
srchi:  .byte hi(s0), hi(s1)
s0:     .byte 1, 2, 3, 4, 5, 6, 7, 8
s1:     .byte 11, 12, 13, 14, 15, 16, 17, 18
buf:    .byte 0, 0, 0, 0, 0, 0, 0, 0
end:
"""


@pytest.mark.parametrize("model", [0, 1])
def test_init_io_probe_is_settled_by_the_machine(prefix, tmp_path, model):
    """Init probing the hardware (an I/O read) and patching play's opcode by what it reads
    is settled at the machine's value: no ``io_read``, and the opcode init stores, the same
    for every ``A``, a constant of the other entry's code: it runs the value the machine's
    run left."""
    img, labels, loaded = assemble(PROBE)
    img[0xC030] = model
    c, h, info = translate(
        prefix, img, labels, loaded, ("init", "play"), sensitive=(0xC030,), init=0
    )
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", k) for k in range(4)])
    assert "io_read(0x" not in c and info["init"] == "replayed"
    assert labels["shift"] not in info["provenance"]["cells"]
    assert (labels["shift"] in info["fixed"]) == bool(model)


#: Init reading voice 3's output after releasing its test bit at full frequency, patching
#: play's opcode by the bit it reads: a sawtooth's first few values, both parities.
DERIVED = """
init:   STA song
        LDA #$FF
        STA $D40E
        STA $D40F
        STA $D412
        LDA #$20
        STA $D412
        LDA $D41B
        LSR A
        BCC six
        LDA #$0A
        BNE put
six:    LDA #$EA
put:    STA patch
        RTS
play:   LDA song
patch:  NOP
        STA $C000
        RTS
song:   .byte 0
end:
"""


def test_init_probe_of_a_derived_register_is_a_run_time_choice(prefix, tmp_path):
    """Init probing a register its own writes derive (voice 3's oscillator) and patching
    play's opcode by what it reads is translated: the C reads the device once and patches
    as the code does, play runs either opcode, and the C plays as the reference whichever
    value of the model's set the device answers."""
    volatile = [W.IO, (0xD000, 0xD800)]
    img, labels = W.assemble(DERIVED, 0x1000)
    img[0xD41B] = 3
    ents = [(labels[n], n) for n in ("init", "play")]
    c, h, info = cgen.translate(
        img, ents, volatile, loaded=(0x1000, labels["end"]), header="t.h", prefix=prefix, init=0
    )
    assert info["init"] == "translated" and body_of(c, "init").count("io_read(0xD41B)") == 1
    assert labels["patch"] in info["provenance"]["cells"]
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")
    calls = [("init", 0)] + [("play", k) for k in range(4)]
    for v in (2, 3):
        img[0xD41B] = v
        cgenplay.replay(lib, img, {n: labels[n] for n, _ in calls}, calls, volatile)
    p, _ = cgen.lift(img, ents, volatile, loaded=(0x1000, labels["end"]), init=0, devices=None)
    assert p.lifted is None


#: Init starting CIA 1's timer A with its interrupt masked off and waiting for its flag,
#: play acknowledging the flag twice, the reads' values unused.
TIMERFLAG = """
init:   STA song
        LDA #$7F
        STA $DC0D
        LDA #$01
        STA $DC0E
wait:   LDA $DC0D
        AND #$01
        BEQ wait
        RTS
play:   BIT $DC0D
        BIT $DC0D
        LDA song
        STA $C000
        RTS
song:   .byte 0
end:
"""


def test_timer_flag_wait_and_acknowledges_are_kept(prefix, tmp_path):
    """A wait on a timer's flag the model says recurs is translated, reading the device each
    pass; reads that acknowledge it are each made though their values are not used."""
    volatile = [W.IO, (0xDC00, 0xDD00)]
    img, labels = W.assemble(TIMERFLAG, 0x1000)
    ents = [(labels[n], n) for n in ("init", "play")]
    c, h, info = cgen.translate(
        img,
        ents,
        volatile,
        loaded=(0x1000, labels["end"]),
        header="t.h",
        prefix=prefix,
        init=0,
        machine=P.machine(flag=0x01, addr=0xDC0D, volatile=volatile),
    )
    assert info["init"] == "translated" and "io_read(0xDC0D)" in body_of(c, "init")
    assert body_of(c, "play").count("io_read(0xDC0D)") == 2
    img[0xDC0D] = 0x01
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")
    calls = [("init", 0)] + [("play", k) for k in range(4)]
    cgenplay.replay(lib, img, {n: labels[n] for n, _ in calls}, calls, volatile, (0xDC0D,))


@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    "src,polled,translated",
    [
        (P.RASTER, {0xD012: 0x30}, True),
        (P.TIMER, {0xC00D: 0x80}, True),
        (P.STATUS, {0xC00D: 0x80}, False),
        (P.PLAYWAIT, {0xC00D: 0x80}, True),
    ],
    ids=["raster", "timer", "status", "play"],
)
def test_init_waiting_on_the_device_waits_in_c(
    prefix, tmp_path, src, polled, translated
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Init waiting on the device (a raster line, a status flag) on a machine whose device
    satisfies it later is translated with the wait reading the device each pass, as play's
    wait is; one storing the byte it polls, which varies, is replayed. The C plays as the
    reference with the device holding a byte each wait leaves on."""
    img, labels, loaded = assemble(src)
    ents = [(labels[n], n) for n in ("init", "play")]
    c, h, info = cgen.translate(
        img,
        ents,
        P.VOLATILE,
        loaded=loaded,
        header="t.h",
        prefix=prefix,
        init=0,
        songs=range(2),
        machine=P.machine(),
    )
    assert info["init"] == ("translated" if translated else "replayed")
    waits = [a for a in polled if f"io_read(0x{a:04X})" in c]
    assert bool(waits) == (translated or src is P.PLAYWAIT)
    for a, v in polled.items():
        img[a] = v
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")
    calls = [("init", 1)] + [("play", k) for k in range(4)]
    cgenplay.replay(lib, img, {n: labels[n] for n, _ in calls}, calls, P.VOLATILE)


#: Init running a routine, then copying a block from word tables an index derived from ``A``
#: chooses, its length a self-modified compare: one block overwrites the routine init
#: already ran.
TABLECOPY = """
init:   AND #$03
        TAY
        JSR pre
        LDA which,Y
        ASL A
        TAX
        LDA dsts,X
        STA $FA
        LDA dsts+1,X
        STA $FB
        LDA srcs,X
        STA $FC
        LDA srcs+1,X
        STA $FD
        LDY lens,X
        STY cnt+1
        LDY #$00
copy:   LDA ($FC),Y
        STA ($FA),Y
        INY
cnt:    CPY #$00
        BNE copy
        RTS
play:   AND #$07
        TAX
        LDA buf,X
        STA $C000
        RTS
which:  .byte 2, 0, 1, 2
dsts:   .byte lo(buf), hi(buf), lo(buf), hi(buf), lo(pre), hi(pre)
srcs:   .byte lo(s0), hi(s0), lo(s1), hi(s1), lo(s2), hi(s2)
lens:   .byte 8, 0, 5, 0, 3, 0
s0:     .byte 1, 2, 3, 4, 5, 6, 7, 8
s1:     .byte 11, 12, 13, 14, 15
s2:     .byte $A9, $07, $60
buf:    .byte 0, 0, 0, 0, 0, 0, 0, 0
pre:    LDA #$0F
        STA $C018
        RTS
end:
"""


def test_init_table_block_copy_is_translated(prefix, tmp_path):
    """Init copying a block through pointers from word tables ``A`` indexes, its length a
    compare operand it patches, is translated: no copy tables, the patched operand a cell
    of init's code, the routine one block overwrites after init ran it constant code, every
    value's trace the reference's and the same ``.c`` for other copied data."""
    texts = set()
    for data in (list(range(1, 9)), list(range(9, 1, -1))):
        src = TABLECOPY.replace("1, 2, 3, 4, 5, 6, 7, 8", ", ".join(map(str, data)))
        c, info, labels = songs(prefix, tmp_path / str(data[0]), src, (0, 1, 2))
        assert info["init"] == "translated" and "CP_" not in c and "IO_AT" not in c
        cells = set(info["provenance"]["cells"])
        assert labels["cnt"] + 1 in cells and not cells & set(range(labels["pre"], labels["end"]))
        assert {labels["copy"], labels["pre"]} <= set(info["starts"])
        texts.add(c)
    assert len(texts) == 1


def test_init_run_once_leaves_code_it_ran_constant():
    """Lifted as run once, init's store over a routine it already ran leaves the routine
    constant code; lifted as an entry run any number of times, its bytes are cells. Both
    lifts give the reference's run."""
    img, labels, loaded = assemble(TABLECOPY)
    pre = set(range(labels["pre"], labels["end"]))
    for once in (False, True):
        prog = wholeprog.lift(img, [labels["init"]], [W.IO], entry_sp=0xFD, loaded=loaded,
                              fold=True, once=once)  # fmt: skip
        assert bool(pre & set(prog.code)) == once
        for a in range(3):
            mem, ref = bytearray(img), bytearray(img)
            r = prog.call(mem, 0, a=a, log=True)
            w = wholeprog.reference_call(ref, labels["init"], a=a, volatile=[W.IO], log=True)
            assert [(x, v, wr) for x, v, _, wr in r["log"]] == [
                (x, v, wr) for x, v, _, wr in w["log"]
            ]
            assert mem[0x200:] == ref[0x200:]


def test_init_block_copy_is_a_copy_of_the_header(prefix, tmp_path):
    """A replayed init copying a block from the table ``A`` indexes leaves, per value, a copy
    of the image's block: the header copies it from the array holding it, the same ``.c``
    for other copied data."""
    texts = set()
    for data in (list(range(1, 9)), list(range(9, 1, -1))):
        img, labels, loaded = assemble(probed(BLOCKCOPY), patch={"s0": data})
        c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=range(2))
        for song in (0, 1):
            (tmp_path / f"{data[0]}-{song}").mkdir()
            calls = [("init", song)] + [("play", k) for k in range(8)]
            play(tmp_path / f"{data[0]}-{song}", c, h, img, labels, calls)
        src, n = re.search(r"#define CGEN_CP_SRC (.*)\n#define CGEN_CP_LEN (.*)\n", h).groups()
        assert ("R", "8") in zip((x[0] for x in src.split(", ")), n.split(", "))
        texts.add(c)
    assert len(texts) == 1


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


def test_code_init_overwrites_is_data(prefix, tmp_path):
    """Init's code a copy overwrites (play never runs it) is data play reads: no code check or
    opcode dispatch, the copied bytes the songs' values."""
    img, labels, loaded = assemble(OVERCOPY)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=range(4))
    for song in (0, 3):
        (tmp_path / str(song)).mkdir()
        calls = [("init", song)] + [("play", k) for k in range(4)]
        play(tmp_path / str(song), c, h, img, labels, calls)
    assert labels["init"] not in set(info["provenance"]["cells"]) and "codechk(" not in c


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
    a table of patch sites, leaves them one value for every ``A``: constants, the other
    entry's accesses direct. An operand the entry also advances stays a pointer variable, the
    song's: its reads are plain reads of the song's data block, checked there by the memory
    map, and it traps there once it runs past the song; the other operand is a constant."""
    src = (
        SWPREL.replace("        STA $C001\n", "        STA $C001\n        INC o1+1\n")
        if plain
        else SWPREL
    )
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    calls = [("init", 0)] + [("play", k) for k in range(4)]
    k = machine_guard(info, labels["o1"])
    assert (k is not None) == plain
    play(tmp_path, c, h, img, labels, calls, trap=(3, k) if plain else None)
    ops = {labels[o] + k for o in ("o0", "o1") for k in (1, 2)}
    assert set(info["fixed"]) & ops == ops - (
        {labels["o1"] + 1, labels["o1"] + 2} if plain else set()
    )
    body = body_of(c, "play")
    assert not re.search(r"\b(?:rd|rdC\d+)\(", body) and re.search(OPERAND_K, body)


@pytest.mark.parametrize("bounded", [True, False])
def test_index_bound_through_a_call_keeps_a_read_inside(prefix, tmp_path, bounded):
    """A song byte stored as a pattern number only below $80 (the branch on the flags the
    callee's load left) indexes a table whose first 128 bytes lie in the image: the read is
    proved inside and reads it unchecked. Stored whatever its sign, the index may reach past
    the image: the read is plain, checked by the memory map, and the pattern $FF, past the
    program, traps there, every call before it exact."""
    src = PATNUM if bounded else PATNUM.replace("        BMI keep\n", "")
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
    body = body_of(c, "play")
    assert not re.search(r"\b(?:oob_rd|rdC\d+)\(", body)
    k = machine_guard(info, labels["keep"] + 3)
    assert (k is None) == bounded
    trap = None if bounded else (1 + 7, k)
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", k) for k in range(8)], trap=trap)


SIGNIDX = (
    """
init:   RTS
play:   TAX
        BEQ out
        BPL out
        ASL A
        TAY
TEST
        LDA tab,Y
        STA $C000
out:    RTS
tab:    .byte """
    + ", ".join(str(k) for k in range(256))
    + """
end:
"""
)


@pytest.mark.parametrize("test", ["        BPL out", "        CPY #$80\n        BCC out"])
def test_flag_narrows_every_register_holding_its_value(prefix, tmp_path, test):
    """The sign of a value two registers hold after a transfer bounds both: past the branch
    on it the index is $80-$FE, as past the equivalent compare of the index itself, and the
    table read is held as constant from its byte $80 only."""
    img, labels, loaded = assemble(SIGNIDX.replace("TEST", test))
    c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
    play(
        tmp_path,
        c,
        h,
        img,
        labels,
        [("init", 0)] + [("play", a) for a in (0, 1, 0x7F, 0x80, 0xC1, 0xFF)],
    )
    spans = re.findall(r"#define (C\d+)_ORG 0x([0-9A-F]+)", h)
    assert [int(o, 16) for _, o in spans] == [labels["tab"] + 0x80], spans


def test_regions_stop_at_the_loaded_image(prefix, tmp_path):
    """A table read with any index reaches past the image: its region ends with the image,
    and a read past it is invalid input, the memory map's check trapping it. A byte past the
    image a store writes is held, and ``oob_rd`` reads it as stored; the byte past it is
    again invalid input."""
    img, labels, loaded = assemble(PAST)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", a) for a in range(4)])
    assert "oob_rd(" not in body_of(c, "play") and machine_guard(info, labels["play"] + 1)
    assert re.search(r"#define R\d+_LEN 4\b", h)
    src = PAST.replace("init:   RTS", "init:   LDA #$07\n        STA end+2\n        RTS")
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0, songs=[0])
    (tmp_path / "w").mkdir()
    calls = [("init", 0)] + [("play", a) for a in (0, 6, 7)]
    play(
        tmp_path / "w", c, h, img, labels, calls, trap=(3, machine_guard(info, labels["play"] + 1))
    )
    assert "oob_rd(" in body_of(c, "play") and re.search(r"#define R\d+_LEN 1\b", h)


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


def test_init_stored_vector_is_a_variable(prefix, tmp_path):
    """A vector init stores outside the program is a variable the header holds, so play's
    ``BRK`` through it goes where the header says: the same ``.c`` at every origin."""
    texts = set()
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(INITBRK, org)
        c, h, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, [("init", 0)] + [("play", 1)] * 4)
        texts.add(c)
    assert len(texts) == 1


def test_program_lifted_from_init_state_matches_the_reference():
    """Play lifted from the state init leaves, every byte it stored a variable (``free``),
    makes the reference's accesses and leaves its memory (but the dead stack below SP)."""
    img, labels, loaded = assemble(INITREL)
    state = postinit.run(img, labels["init"], [3], [W.IO])
    mem = bytearray(state.states[0].tobytes())
    prog = wholeprog.lift(
        mem, [labels["play"]], [W.IO], entry_sp=0xFD, loaded=loaded, free=state.values()
    )
    ref = bytearray(mem)
    for a in range(8):
        reg = cgenplay.entry_regs(a)
        got = prog.call(mem, 0, a=a, reg=reg, log=True)["log"]
        want = wholeprog.reference_call(
            ref, labels["play"], a=a, reg=reg, volatile=[W.IO], log=True, clocked=False
        )
        assert got == want["log"]
        assert mem[0x200:] == ref[0x200:] and mem[:0x100] == ref[:0x100]


def test_cli_runs_the_named_init(prefix, tmp_path):
    # pylint: disable=import-outside-toplevel
    from deity_informant.cgen.__main__ import main

    img, labels, loaded = assemble(INITREL)
    (tmp_path / "prog.bin").write_bytes(bytes(img[loaded[0] : loaded[1]]))
    ents = [a for n in ("init", "play") for a in ("--entry", f"{labels[n]}:{n}")]
    out = tmp_path / "prog.c"
    assert prefix is not None
    main([str(tmp_path / "prog.bin"), "--org", "0x1000", *ents, "--init", "init", "-o", str(out)])
    c = out.read_text()
    assert "void init(uint8_t a)" in c and "CGEN_SONGS" in c


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
    first = labels["entry"] + 8
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


@pytest.fixture(name="blocks", scope="module")
def fixture_blocks(prefix, tmp_path_factory):
    """``(c, h, info)`` of BLOCKS, checked against the reference over a call per block."""
    calls = [("entry", 0), ("entry", 0x82), ("entry", 0x90), ("entry", 1), ("entry", 0x88)]
    return check(prefix, tmp_path_factory.mktemp("blocks"), BLOCKS, calls)


def test_block_copy_stores_have_per_block_addresses(blocks):
    """A block copy through a pointer a page loop advances (a rotated loop over pages, then the
    remainder) whose destination, source and length are table bytes of one block number: its
    stores write, per block, the destination up to the pages its length counts, so RAM past the
    image is data and no store is checked against the code."""
    c, _, info = blocks
    assert "codechk" not in c and not info["traps"]
    spans = {org: n for org, n in info["regions"]}
    assert 0x5000 in spans and 0x6000 in spans and spans[0x6000] <= 0x200


def test_block_copy_indexes_the_blocks_directly(blocks):
    """The copy's stores have address sets in the data regions: they index the regions they
    span directly, testing only those, with no wr dispatch; the bases reaching each region
    are apart, so the test reads the pointer, not the index."""
    c = blocks[0]
    img, labels, loaded = assemble(BLOCKS)
    p, _ = cgen.lift(img, [(labels["entry"], "entry")], [W.IO], loaded=loaded)
    assert any(store for _, store in p.addr_sets)
    assert " wr(" not in c and "CGEN_IX(" in c
    tests = re.findall(
        r"if \(\(uint16_t\)\(([^;]*?)\) <\s*0x[0-9A-F]+\)\s*CGEN_IX\(R\d+, [^,]*, (v\d+)\)", c
    )
    assert tests and all(not re.search(rf"\b{v}\b", t) for t, v in tests)


@pytest.mark.parametrize(
    "name",
    [
        "TABLES",
        "PTRS",
        "BLOCKS",
        "VOICES",
        "RELOCATOR",
    ],
)
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


BRKFLAGS = """
entry:  CMP #$80
        BCS play
        LDA #lo(handler)
        STA $FFFE
        LDA #hi(handler)
        STA $FFFF
        RTS
play:   AND #$03
        CMP #$02
        TAX
        BRK
        .byte $EA
        BCS big
        STX $C000
        RTS
big:    STX $C002
        RTS
handler: FRAME
        LDA cnt
        CLC
        ADC #$01
        STA cnt
        STA $C001
        RTI
cnt:    .byte 0
end:
"""
STATUS_SAVE = """
entry:  CMP #$40
        PHP
        AND #$0F
        BEQ zero
        CLC
        ADC #$70
        STA $C001
zero:   PLP
        BCS hi
        LDA #$01
        STA $C002
        RTS
hi:     LDA #$02
        STA $C002
        RTS
end:
"""
STATUS_BYTE = """
entry:  CMP #$80
        BCC bin
        SED
bin:    CMP #$40
        PHP
        CLD
        LDX #$00
        BCC lo
        LDX #$01
lo:     STX $C003
        PLA
        AND #MASK
        STA $C001
        RTS
end:
"""
STATUS_FROM_BYTE = """
entry:  ORA #$01
        PHA
        AND #$02
        TAX
        BEQ skip
        INX
skip:   STX $C003
        PLP
        BCS one
        LDA #$00
        STA $C001
        RTS
one:    LDA #$01
        STA $C001
        RTS
end:
"""
STATUS_DECIMAL = """
entry:  CMP #$80
        BCC bin
        SED
bin:    PHP
        CLD
        LDX #$00
        AND #$0F
        BEQ zero
        INX
zero:   STX $C003
        PLP
        CLC
        ADC #$19
        STA $C001
        CLD
        RTS
end:
"""
STATUS_READ = """
entry:  CMP #$40
        PHP
        TSX
        LDA $0101,X
        STA $C001
        AND #$03
        BEQ zero
        STA $C002
zero:   PLP
        LDA #$00
        ROL A
        STA $C003
        RTS
end:
"""


@pytest.mark.parametrize("byte", [False, True])
def test_status_saved_through_the_stack_is_its_flags(prefix, tmp_path, byte):
    """A status ``PHP`` pushes that only ``PLP`` pulls is the flags it saved: no packed byte,
    and the decimal flag the pull restores is the clear one pushed, so no check of it; also
    where a byte saved by ``PHA`` and restored by ``PLA`` earlier takes the same stack byte."""
    pha = "entry:  PHA\n        LDA #$00\n        STA $C004\n        PLA\n        CMP #$40"
    src = STATUS_SAVE.replace("entry:  CMP #$40", pha) if byte else STATUS_SAVE
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(0, 256, 5)))
    assert "0x30" not in body_of(c, "entry") and not info["traps"]


@pytest.mark.parametrize(
    "mask,bits", [(0x08, ["<< 3"]), (0xC3, ["<< 1", "<< 7"])], ids=["d", "czn"]
)
def test_status_read_as_a_byte_packs_the_flags_read(prefix, tmp_path, mask, bits):
    """A status pulled as data (``PHP; PLA; AND #mask``) packs only the flags the mask keeps:
    the decimal flag alone, or carry, zero and negative (overflow and interrupt are clear)."""
    src = STATUS_BYTE.replace("MASK", f"${mask:02X}")
    c, _, _ = check(prefix, tmp_path, src, entry_calls(range(0, 256, 3)))
    body = body_of(c, "entry")
    assert "0x30" not in body and all(b in body for b in bits)
    assert {"<< 2", "<< 6"}.isdisjoint(re.findall(r"<< \d", body))


def test_status_set_from_a_byte_unpacks_the_flags_read(prefix, tmp_path):
    """``PHA; PLP`` sets the flags from a byte: only those read after it (carry, and the
    decimal flag the pull is checked for) are taken from it."""
    calls = entry_calls([a for a in range(0, 256, 3) if not a & 8])
    c, _, _ = check(prefix, tmp_path, STATUS_FROM_BYTE, calls)
    assert re.findall(r">> (\d)", body_of(c, "entry")) == ["3"]


def test_pulled_decimal_flag_feeds_arithmetic(prefix, tmp_path):
    """A decimal flag saved by ``PHP`` and restored by ``PLP`` before an ``ADC`` keeps its
    value: binary and decimal sums both match the reference, with no packed byte."""
    c, _, _ = check(prefix, tmp_path, STATUS_DECIMAL, entry_calls(range(0, 256, 3)))
    assert "0x30" not in body_of(c, "entry")


def test_status_read_through_the_stack_page_stays_packed(prefix, tmp_path):
    """A status a load reads from the stack page (``TSX; LDA $0101,X``) is stored packed and
    read exactly; the ``PLP`` still restores the flags it saved."""
    c, _, _ = check(prefix, tmp_path, STATUS_READ, entry_calls(range(0, 256, 5)))
    assert "0x30" in body_of(c, "entry")


@pytest.mark.parametrize("reads", [False, True])
def test_interrupt_frame_status(prefix, tmp_path, reads):
    """A ``BRK`` handler's ``RTI`` restores the flags the frame saved; the status byte is
    packed only for a handler reading the frame as data (``TSX; LDA $0101,X``)."""
    src = BRKFLAGS.replace(
        "FRAME", "TSX\n        LDA $0101,X\n        STA $C004" if reads else "NOP"
    )
    calls = [("entry", 0)] + entry_calls([0x80, 0x81, 0x82, 0x83], 2)
    c, _, _ = check(prefix, tmp_path, src, calls)
    assert ("0x30" in body_of(c, "entry")) == reads


@pytest.mark.parametrize("sites", [2, 1])
def test_call_with_inline_arguments(prefix, tmp_path, sites):
    """A callee that pulls its return address, reads the bytes after the call and pushes the
    address back past them returns past its inline argument: called from two sites it is a
    continuation function, handed its return address and specialized to each call, from one
    it is inlined; either way each argument is a constant code byte, no code address is a
    value and the `.c` is the same at another origin."""
    src = INLINE if sites == 2 else INLINE.replace("        JSR arg\n        .byte $22\n", "")
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(3)))
    img, labels, loaded = assemble(src)
    assert info["jumps"][labels["end"] - 1] == [labels["entry"] + 4, labels["entry"] + 11][:sites]
    entries = {f["entry"] for f in info["provenance"]["functions"].values()}
    assert labels["arg"] not in entries and "CODE_ORG" not in c and "R0" not in c
    assert [f"CODE_K{k:X}" in c for k in (3, 0xA)] == [True, sites == 2]
    p, _ = cgen.lift(img, [(labels["entry"], "entry")], [W.IO], loaded=loaded)
    assert sum(f.get("continuation", False) for f in p.funcs) == (sites == 2)
    img2, labels2 = W.assemble(src, 0x2400)
    assert translate(prefix, img2, labels2, (0x2400, labels2["end"]))[0] == c


def test_inline_arguments_of_several_sites_are_constants(prefix, tmp_path):
    """One- and two-byte inline arguments read through the pulled return address, each callee
    called from two sites: every call's arguments are constant code bytes, no code address is a
    value (the pointer the callees pull it into is read back only through forwarded loads) and
    the `.c` is the same at another origin."""
    c, _, info = check(prefix, tmp_path, W.INLINE_ARGS, entry_calls(range(3), 2))
    img, labels, loaded = assemble(W.INLINE_ARGS)
    assert "CODE_ORG" not in c and "R0" not in c and not info["traps"]
    org = labels["entry"]
    args = [i for i in range(labels["arg1"] - org) if img[org + i] in (0x11, 0x22, 0x33, 0x44)]
    assert all(f"CODE_K{i:X}" in c for i in args) and len(args) == 4
    p, _ = cgen.lift(img, [(org, "entry")], [W.IO], loaded=loaded)
    assert sum(f.get("continuation", False) for f in p.funcs) == 2
    img2, labels2 = W.assemble(W.INLINE_ARGS, 0x2400)
    assert translate(prefix, img2, labels2, (0x2400, labels2["end"]))[0] == c


def test_return_past_an_unknown_amount_traps(prefix, tmp_path):
    """A callee advancing its return address by a byte the code does not decide returns
    where the call continues or traps: its targets are not the code's."""
    calls = entry_calls(range(4), 3)
    c, _, info = check(prefix, tmp_path, W.RET_UNKNOWN, calls)
    assert "trap(" in c and info["traps"]


def test_written_inline_argument_is_a_variable(prefix, tmp_path):
    """An inline argument a store writes is a variable of the code span, which the callee's
    read of it at each call reads: not a constant, and no code address a value."""
    c, _, _ = check(prefix, tmp_path, W.ARG_WRITTEN, entry_calls(range(3), 2))
    at = W.assemble(W.ARG_WRITTEN)[1]["a1"] - 0x1000
    assert f"S_{at:X} = v1;" in c and f"CODE_K{at:X}" not in c and "CODE_ORG" not in c


def test_pushed_constant_return_is_a_branch(prefix, tmp_path):
    """`PHA`/`PHA`/`RTS` to a constant target is a branch to it: no code address, no trap."""
    c, _, info = check(prefix, tmp_path, W.PUSH_RET, entry_calls(range(2)))
    assert "CODE_ORG" not in c and not info["traps"]


@pytest.mark.parametrize("sites", [1, 2])
def test_brk_signature_read_through_the_pushed_address(prefix, tmp_path, sites):
    """A `BRK` handler reading the signature byte through the address `BRK` pushed (`TSX;
    LDA $0102,X`): with one `BRK` the byte is a constant, with two the handler is a function
    reading its frame from memory; either way the replay matches."""
    src = W.BRK_SIG1 if sites == 1 else W.BRK_SIG
    c, _, _ = check(prefix, tmp_path, src, entry_calls(range(2), 2))
    img, labels, _ = assemble(src)
    sig = labels["entry"] + 11
    assert img[sig] == 0x21
    assert (f"CODE_K{sig - labels['entry']:X}" in c and "CODE_ORG" not in c) == (sites == 1)


def test_brk_through_a_constant_vector_is_a_call(prefix, tmp_path):
    """A `BRK` through a vector no store writes goes to the handler it holds: no compare of
    the vector, no trap."""
    c, _, info = check(prefix, tmp_path, W.BRK_IMAGE, entry_calls(range(2)))
    assert "CODE_ORG" not in c and not info["traps"]


def test_rti_through_a_table_frame_selects_on_its_index(prefix, tmp_path):
    """An `RTI` through a frame pushed from a table pair indexed by `Y` chooses on `Y`."""
    c, _, info = check(prefix, tmp_path, W.RTI_TABLE, entry_calls(range(4)))
    assert "CODE_ORG" not in c and not info["traps"]


def test_unread_stores_are_dropped(prefix, tmp_path):
    """A store no load of any entry reads back is dropped; one another entry reads is kept."""
    src = (
        "entry:  STA $F0\n        STA $C000\n        RTS\n"
        "probe:  LDA $F0\n        STA $C001\n        RTS\nend:\n"
    )
    img, labels, loaded = assemble(src)
    assert "R0" not in translate(prefix, img, labels, loaded)[0]
    assert "R0[0] = a;" in translate(prefix, img, labels, loaded, entries=("entry", "probe"))[0]


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
    than $80 over three copies) hold them as field arrays indexed by the copy (one field per
    cell, however far apart); data bytes at that stride read each copy's base once from a
    constant table of pointers into the region and index it by a byte, as ``(zp),Y`` does: no
    16-bit index, no ``rd``/``wr``."""
    src = _wide(inner, 0x70)
    c, _, info = check(prefix, tmp_path, src, entry_calls(range(4), 6))
    assert (info["stats"]["instances"], info["stats"]["instance_copies"]) == (1, 3)
    assert not re.search(r"\b(?:rd|wr)\(|uint16_t K\d|static const uint16_t", c)
    fields = set(re.findall(r"\b(V0_f[0-9A-F]+)\[v1\]", c))
    assert len(fields) == 2 and (max(int(f[4:], 16) for f in fields) > 0xFF) == (windows == 2)
    tabs = re.findall(r"static uint8_t \*const (K\d+)\[\] = \{([CR])(\d+)[^,]*, \2\3 \+ ", c)
    assert sorted(t[1] for t in tabs) == ["R"]
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
    img, labels, loaded = assemble(PTRS_WRITTEN)
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
    # The read of the strings the entry writes is plain, the memory map's check its trap path.
    assert 0 < entry["original"] <= labels["ptrs"] - labels["entry"] and entry.get("trap", 0) > 0
    assert not entry.get("dispatch", 0)
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


#: :data:`WORDREL` taking its words from the header row ``A`` chooses.
WORDROW = (
    WORDREL.replace("init:   LDX #$00\n", "init:   ASL A\n        ASL A\n        STA sel\n        LDX #$00\n")
    .replace("        LDY words,X\n", "        LDA words,X\n        CLC\n        ADC sel\n        TAY\n")
    .replace("tmp:    .byte 0\n", "tmp:    .byte 0\nsel:    .byte 0\n")
    .replace("hi(tab2)\n", "hi(tab2)\n        .byte lo(tab2), hi(tab2), lo(tab1), hi(tab1)\n")
)  # fmt: skip


def test_operands_set_from_one_constant_word_are_constants(prefix, tmp_path):
    """Operands init sets from header words, the same for every ``A``, are constants: the
    other entry's accesses through them direct, the ``.c`` the same wherever its tables
    lie."""
    texts = set()
    for pad in ("", "        .byte 0,0,0\n"):
        img, labels, loaded = assemble(WORDREL.replace("pad:\n", f"pad:\n{pad}"))
        c, h, info = translate(
            prefix, img, labels, loaded, entries=("init", "play"), init=0, songs=range(2)
        )
        (tmp_path / str(len(pad))).mkdir()
        play(tmp_path / str(len(pad)), c, h, img, labels, [("init", 1)] + [("play", 3)] * 2)
        ops = {labels[o] + k for o in ("o0", "o1", "o2") for k in (1, 2)}
        body = body_of(c, "play")
        assert ops <= set(info["fixed"]) and not re.search(r"rdC|\bS_", body)
        texts.add(c)
    assert len(texts) == 1


def test_operands_set_from_one_word_are_one_pointer(prefix, tmp_path):
    """Play is lifted from what init's run leaves: operand pairs init sets from one song word
    (the header row ``A`` chooses) are one pointer variable, the others read as it plus their
    constant offset, and the ``.c`` is the same wherever the tables lie."""
    texts = set()
    for pad in ("", "        .byte 0,0,0\n"):
        img, labels, loaded = assemble(WORDROW.replace("pad:\n", f"pad:\n{pad}"))
        c, h, info = translate(
            prefix, img, labels, loaded, entries=("init", "play"), init=0, songs=range(1)
        )
        (tmp_path / str(len(pad))).mkdir()
        calls = [("init", 0)] + [("play", a) for a in range(4)] * 2
        play(tmp_path / str(len(pad)), c, h, img, labels, calls)
        ops = {labels[o] + k for o in ("o0", "o1", "o2") for k in (1, 2)}
        assert ops <= set(info["provenance"]["cells"])
        body = body_of(c, "play")
        pairs = set(re.findall(rf"({BYTE}) \| \(\(uint16_t\)({BYTE}) << 8\)", body))
        assert len(pairs) == 2 and "1 + v" in body
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


STOPPER = """
init:   LDA #$2C
        STA op
        LDA #$00
        STA n
        RTS
play:   INC n
op:     BIT n
        LDA n
        STA $C010
        CMP #$03
        BNE done
        LDA #$60
        STA op
done:   RTS
n:      .byte 0
end:
"""


def test_play_stores_an_opcode_into_a_cell_init_sets(prefix, tmp_path):
    """Init sets an opcode cell, so its entry byte is the host's; the opcode play stores
    there is still lifted as a variant: after it play returns at the cell."""
    img, labels, loaded = assemble(STOPPER)
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    assert labels["op"] in info["provenance"]["cells"]
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", 0)] * 6)


FIXED = """
init:   LDA #$40
        STA $40
        LDA #$30
        STA $41
        LDA #$05
        STA $3040
        STA $3047
        RTS
play:   LDX #$07
loop:   LDA $3040,X
        CLC
        ADC #$03
        STA $3040,X
        STA $C010
        LDA ($40,X)
        ADC #$01
        STA ($40,X)
        TXA
        SEC
        SBC #$07
        TAX
        BPL loop
        RTS
end:
"""


def test_state_past_the_image_keeps_its_addresses_when_relocated(prefix, tmp_path):
    """State at fixed addresses past the image, in a region that starts where the image
    ends (a pointer init sets may write any byte up to it), is offset by the region's
    origin: the program assembled at another origin translates to the same ``.c``."""
    texts = set()
    for org in (0x1000, 0x2000):
        img, labels, loaded = assemble(FIXED, org)
        c, h, _ = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, [("init", 0)] + [("play", 0)] * 4)
        texts.add(c)
    assert len(texts) == 1


#: Init unpacking its records past the image and leaving a voice pointer into them, or null
#: for a song whose voice is idle; play reads through the pointer while the voice is on and
#: patches an immediate with what it read.
UNPACK = """
init:   TAX
        LDY #$00
copy:   LDA recs,Y
        STA $3000,Y
        INY
        CPY #$08
        BNE copy
        LDA vlo,X
        STA $FB
        LDA vhi,X
        STA $FC
        LDA von,X
        STA on
        RTS
play:
lv:     LDA #$00
        STA $C001
        LDA on
        BEQ idle
        LDY #$00
read:   LDA ($FB),Y
        STA $C000
        STA lv+1
idle:   RTS
vlo:    .byte $00,$00
vhi:    .byte $30,$00
von:    .byte 1,0
on:     .byte 0
recs:   .byte 9,8,7,6,5,4,3,2
end:
"""


def test_null_voice_pointer_into_unpacked_records_is_bounded(prefix, tmp_path):
    """The words init leaves in a pointer pair over its songs bound play's reads through it
    to the records it unpacked past the image, the null of an idle voice's song taken not to
    be read through: the read reaches no code, and the immediate play patches is a scalar."""
    img, labels, loaded = assemble(UNPACK)
    p, _ = cgen.lift(
        img,
        [(labels["init"], "init"), (labels["play"], "play")],
        [W.IO],
        loaded=loaded,
        init=0,
        songs=range(2),
    )
    lo, hi = p.reach[labels["read"]][:2]
    assert labels["vlo"] <= lo < labels["end"] and 0x3007 <= hi < 0x3100
    c, info, _ = songs(prefix, tmp_path, UNPACK, (0, 1))
    assert info["state"]["cells"]["scalars"] == 1 and "cgen_var" not in plain(c)


#: Play forming its pointer from a table of offsets plus the origin init leaves (a
#: relocating player's song data base), then patching an immediate with the byte it reads.
ORIGIN = """
init:   LDA #lo(recs)
        STA $FD
        LDA #hi(recs)
        STA $FE
        RTS
play:   AND #$01
        TAY
lv:     LDA #$00
        STA $C001
        LDA offlo,Y
        CLC
        ADC $FD
        STA $FB
        LDA offhi,Y
        ADC $FE
        STA $FC
        LDY #$00
read:   LDA ($FB),Y
        STA $C000
        STA lv+1
        RTS
offlo:  .byte 0,5
offhi:  .byte 0,0
recs:   .byte 9,8,7,6,5,4,3,2
end:
"""


def test_offsets_plus_init_origin_point_into_its_run(prefix, tmp_path):
    """A pair stored from the two parts of a table byte plus the origin init leaves (added
    with carry) points into the run of data bytes the origin points into: the read reaches
    no code, so the patched immediate is a scalar. The origin is one value for every ``A``,
    a constant. The read stays in that run's part of the song's data block, the records."""
    img, labels, loaded = assemble(ORIGIN)
    ents = [(labels["init"], "init"), (labels["play"], "play")]
    p, _ = cgen.lift(img, ents, [W.IO], loaded=loaded, init=0)
    lo, hi = p.reach[labels["read"]][:2]
    assert labels["offlo"] <= lo and hi < labels["end"] + 0x100
    assert p.proven[labels["read"]][:2] == (labels["recs"], labels["end"] - 1)
    assert p.fixed.tolist() == [0xFD, 0xFE]
    c, info, _ = songs(prefix, tmp_path, ORIGIN, (0,))
    assert info["state"]["cells"]["scalars"] == 1 and "cgen_var" not in plain(c)


#: :data:`INITREL` then adding ``A``'s low bit to the operands' low bytes.
INITBIT = INITREL.replace(
    "done:   LDA song\n",
    "".join(
        f"        LDA song\n        AND #$01\n        CLC\n        ADC {o}+1\n        STA {o}+1\n"
        for o in ("o0", "o1")
    )
    + "done:   LDA song\n",
).replace("rel:    .byte 0, 0, 4, 0", "rel:    .byte 0, 0, 3, 0")


def test_bounded_store_leaves_cells_to_promotion(prefix, tmp_path):
    """A store through an operand init relocated into a run of data bytes touches that run,
    not every byte: the operand cells the entry reads at their addresses (the low bytes,
    which differ between values of ``A``) are scalars where init is replayed; a translated
    init's stores through its pointer write them by their address set, so they stay bytes of
    their region and no access finds them by address (``cgen_var``)."""
    for probe in (False, True):
        src = probed(INITBIT) if probe else INITBIT
        c, info, labels = songs(prefix, tmp_path / str(probe), src, (0, 1, 5))
        cells = info["state"]["cells"]
        assert {labels["o0"] + 1, labels["o1"] + 1}.isdisjoint(info["fixed"])
        assert "unknown" not in cells["bytes"] and "cgen_var" not in body_of(c, "play")
        if probe:
            assert cells["scalars"] >= 2
        else:
            assert cells["bytes"]["pointer"] >= 2 and not DISPATCH.search(plain(c))
            assert "cgen_var" not in plain(c)


#: A pointer whose high byte is the argument reads, and writes, the immediate play patches.
VARPTR = """
entry:  STA $FC
        LDA #lo(lv+1)
        STA $FB
        LDY #$00
        LDA ($FB),Y
        CLC
        ADC #$01
        STA ($FB),Y
        STA $C000
lv:     LDA #$07
        STA $C001
        INC lv+1
        RTS
end:
"""


def test_unbounded_pointer_finds_the_promoted_cell(prefix, tmp_path):
    """A load and a store through a pointer the lift does not bound leave the code span's
    cells to promotion: the span's reader and ``wr`` find a promoted cell's variable
    (``cgen_var``)."""
    _, labels, _ = assemble(VARPTR)
    hi = labels["lv"] + 1 >> 8
    c, _, info = check(prefix, tmp_path, VARPTR, entry_calls([hi], 4))
    assert info["state"]["cells"] == {"scalars": 1, "fields": 0, "unread": 0, "bytes": {}}
    reader = c[c.index("uint8_t rdC0(") :]
    assert "cgen_var_at(a)" in reader[: reader.index("}")] and re.search(r"\bwr\(", c)


#: Init leaving its pointer at the immediate play patches; play reads through it until it
#: points it into its records.
STRAY = """
init:   LDA #lo(lv+1)
        STA $FB
        LDA #hi(lv+1)
        STA $FC
        RTS
play:   AND #$01
        BEQ read
        TAY
        LDA plo,Y
        STA $FB
        LDA phi,Y
        STA $FC
read:
lv:     LDA #$00
        STA $C011
        LDY #$00
        LDA ($FB),Y
        STA $C010
        STA lv+1
        RTS
""" + RECORDS


def test_read_past_the_assumed_hull_reads_the_promoted_cell(prefix, tmp_path):
    """The word init leaves pointing at code is the song's: play's reads are bounded to the
    song's data block and the patched immediate is a scalar no read finds by address. A read
    through it anyway would take the code for the song's data: it traps at the memory map's
    check, the calls before it exact."""
    img, labels, loaded = assemble(STRAY)
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    assert info["state"]["cells"]["scalars"] == 1 and "cgen_var" not in c
    k = machine_guard(info, labels["read"] + 7)
    calls = [("init", 0), ("play", 0), ("play", 0), ("play", 1), ("play", 0), ("play", 3)]
    play(tmp_path, c, h, img, labels, calls, trap=(1, k))


#: An index kept in a compare's operand, which init and play patch with bytes they bound.
CELLIDX = """
init:   LDA #$03
        STA lim+1
        RTS
play:   TAX
        LDY lim+1
        LDA tab,Y
        STA $C000
        TXA
        AND #$01
lim:    CMP #$00
        BEQ out
        STA lim+1
out:    RTS
tab:    .byte 1,2,3,4
pad:    .byte """ + ",".join(["0"] * 40) + "\nend:\n"


def test_patched_index_cell_bounded_by_its_stores(prefix, tmp_path):
    """A code cell init and play patch is an index bounded by the values init leaves and the
    values play stores: the table it indexes is held as its four bytes, not the bytes past it a
    whole byte of index would reach."""
    img, labels, loaded = assemble(CELLIDX)
    c, h, _ = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    tab = labels["tab"]
    regions = dict(re.findall(r"#define ([RC]\d+)_ORG 0x([0-9A-F]+)", h))
    lens = dict(re.findall(r"#define ([RC]\d+)_LEN (\d+)", h))
    held = {int(o, 16): int(lens[r]) for r, o in regions.items()}
    assert held.get(tab) == 4, (hex(tab), held)
    calls = [("init", 0), ("play", 0), ("play", 1), ("play", 3)]
    play(tmp_path, c, h, img, labels, calls)


def test_strided_records_are_struct_fields(prefix, tmp_path):
    # X steps 14, 7, 0: $3000/$3001 are two fields of one array of three 7-byte records,
    # $3140 a field of another; each is an array over the records, indexed by the element.
    c, h, info = check(prefix, tmp_path, STRIDED, entry_calls([0], 6))
    body = body_of(c, "entry")
    assert "rd(" not in body and "R0[" not in body
    assert {"V0_f00[0]", "V0_f01[2]", "V1_f00[1]"} <= set(info["state"]["vars"])
    assert info["state"]["vars"]["V0_f01[2]"] == 0x300F
    assert "V0_f00_INIT" in h


def test_strided_records_indexed_by_a_parameter(prefix, tmp_path):
    # A routine called with X = 0, 7, 14 indexes the records by X / 7.
    c, _, info = check(prefix, tmp_path, STRIDED_CALLED, entry_calls([0], 6))
    assert "rd(" not in c and "/ 7]" in c
    img, labels, loaded = assemble(STRIDED_CALLED)
    p, _ = cgen.lift(img, [(labels["entry"], "entry")], [W.IO], loaded=loaded)
    rec = labels["rec"]
    assert p.addr_sets[rec, False] == [(0x3000, 0x3001), (0x3007, 0x3008), (0x300E, 0x300F)]
    assert info["state"]["vars"]["V0_f01[1]"] == 0x3008


def test_strided_records_aliased_by_an_interval_stay_memory(prefix, tmp_path):
    # A load at $3000 plus the entry's A may reach any record byte at $3000: those stay bytes
    # of a region, the records at $3140 no other access reaches are fields.
    c, _, info = check(prefix, tmp_path, STRIDED_ALIASED, entry_calls(range(16), 3))
    held = info["state"]["vars"].values()
    assert not [a for a in held if 0x3000 <= a < 0x3010] and 0x3147 in held
    assert "R0[" in body_of(c, "entry")


def test_record_walk_store_is_c_state(prefix, tmp_path):
    """The store patched from a record walk's selectors writes its three words: its operand
    is C state, with no check against code and no dispatch, the memory map checking it."""
    c, _, info = check(prefix, tmp_path, VS.WALK, entry_calls(range(4), 2))
    body = body_of(c, "entry")
    assert "codechk" not in c and "rd(" not in body and "wr(" not in body
    img, labels, _ = assemble(VS.WALK)
    # The words the walk's selectors copy, whatever they hold, the memory map bounds alone.
    guards = [g for t in info["trap_info"].values() for g in t["guards"]]
    assert machine_guard(info, labels["dst"]) is not None
    assert all(g["def"] == "machine" for g in guards)
    assert info["ptr_sets"][labels["loop"]] == sorted([labels["r0"], labels["r1"]])
    assert img[labels["dst"]] == 0x8D


def test_records_read_their_object_unchecked(prefix, tmp_path):
    """The pointer init leaves per argument in one of the streams reads its fields unchecked
    from one array: one test of the pointer against its object, whose bounds the ``.h``
    names, and the streams' addresses in no literal of the ``.c``."""
    img, labels, loaded = assemble(TO.STREAM)
    c, h, _ = translate(
        prefix, img, labels, loaded, entries=("init", "play"), init=0, songs=range(4)
    )
    body = body_of(c, "play")
    assert "rdC" not in c and body.count("CGEN_IX(") == 4
    assert not re.search(r"_LEN\b|\boob_", body)
    assert body.count("CGEN_OBJ0_ORG") == 1 and "#define CGEN_OBJ0_ORG" in h
    words = {labels[f"s{i}"] for i in range(4)} | {labels["play"]}
    assert not {int(x, 16) for x in re.findall(r"0x([0-9A-F]{3,4})\b", c)} & words
    for song in range(4):
        play(tmp_path, c, h, img, labels, [("init", song)] + [("play", 0)] * 120)


def walk_c(prefix, src, songs=range(4)):
    """``(c, h, image, labels)`` of ``src``'s ``init`` and ``play`` translated, init called
    with ``A`` each of ``songs``."""
    img, labels, loaded = assemble(src)
    c, h, _ = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0, songs=songs)
    return c, h, img, labels


def test_walks_read_their_runs_unchecked(prefix, tmp_path):
    """A walk to a sign-bit marker from the pointer init leaves per argument reads plain:
    no bounds-checked reader, and every argument replays."""
    c, h, img, labels = walk_c(prefix, TO.SIGN)
    assert "rdC" not in c and "CGEN_IX(" in body_of(c, "play")
    for song in range(4):
        play(tmp_path, c, h, img, labels, [("init", song)] + [("play", 0)] * 20)


def test_walk_anchor_checks_pointer_plus_y(prefix, tmp_path):
    """A walk from a pointer play sets reads plain after one test of the pointer plus ``Y``
    against its object, whose bounds the ``.h`` names."""
    c, h, img, labels = walk_c(prefix, TO.SET)
    body = body_of(c, "play")
    assert "rdC" not in body and "CGEN_OBJ0_ORG" in body and "#define CGEN_OBJ0_LIM" in h
    for song in range(4):
        play(tmp_path, c, h, img, labels, [("init", song)] + [("play", song)] * 20)


def test_seeded_walks_c_is_the_same_across_runs(prefix, tmp_path):
    """Runs that differ only in where their zero markers fall translate to the same ``.c``:
    their bounds are the ``.h``'s; each replays every argument."""
    out = []
    for i, lens in enumerate(([5, 9, 3, 12], [2, 13, 7, 4])):
        src = TO.walk_src(TO.runs(0, lens), test="BEQ done")
        c, h, img, labels = walk_c(prefix, src)
        assert "rdC" not in c
        (tmp_path / str(i)).mkdir()
        for song in range(4):
            calls = [("init", song)] + [("play", 0)] * 20
            play(tmp_path / str(i), c, h, img, labels, calls)
        out.append((c, h))
    assert out[0][0] == out[1][0] and out[0][1] != out[1][1]


@pytest.mark.parametrize("src", [TO.CURSOR, TO.COUNTED], ids=["plain", "counted"])
def test_cursors_read_plain(prefix, tmp_path, src):
    """Cursors play keeps between calls, reset at their data's markers, read the records and
    segments with no bounds-checked reader: the states play reaches from each of init's runs
    bound them; every argument replays past many resets."""
    c, h, img, labels = walk_c(prefix, src)
    assert "rdC" not in c and "CGEN_IX(" in body_of(c, "play")
    for song in range(4):
        play(tmp_path, c, h, img, labels, [("init", song)] + [("play", 0)] * 150)


def test_cursor_stepped_by_a_modelled_read_reads_plain(prefix, tmp_path):
    """A cursor stepped by an I/O read the model answers with 0 or 1 reads plain; the same
    read answering any byte reads plain too, the memory map checking it."""
    img, labels, loaded = assemble(TO.POLLED)
    img[0xC003] = 1
    ents = [(labels[n], n) for n in ("init", "play")]
    kw = {"loaded": loaded, "header": "t.h", "prefix": prefix, "init": 0, "songs": range(4)}
    c, h, _ = cgen.translate(img, ents, [W.IO], devices=TO.STEP, **kw)
    assert "rdC" not in c
    for song in range(4):
        play(tmp_path, c, h, img, labels, [("init", song)] + [("play", 0)] * 150)
    assert "rdC" not in cgen.translate(img, ents, [W.IO], devices=None, **kw)[0]


def test_cursor_a_store_may_write_keeps_its_check(prefix, tmp_path):
    """A cursor another entry's store of unknown address may write bounds nothing, but the
    record pointer's words, which play stores just before its load, lie in the program: the
    load reads plain, the memory map checking the pointer plus the cursor, and the program
    replays."""
    img, labels, loaded = assemble(TO.POKED)
    c, h, _ = translate(
        prefix, img, labels, loaded, entries=("init", "play", "poke"), init=0, songs=range(4)
    )
    assert "rdC" not in body_of(c, "play") and "CGEN_OBJ" in body_of(c, "play")
    play(tmp_path, c, h, img, labels, [("init", 1)] + [("play", 0), ("poke", 0)] * 40)


def test_cursor_bounds_are_the_headers(prefix):
    """The bounds the reachable states give the cursors' loads are the ``.h``'s: the regions
    they read are named there, and no record or segment address is a literal of the ``.c``."""
    c, h, _, labels = walk_c(prefix, TO.CURSOR)
    assert "rdC" not in c and re.search(r"#define R\d+_ORG", h)
    starts = {labels[f"r{i}"] for i in range(4)} | {labels["flat"]}
    assert not {int(x, 16) for x in re.findall(r"0x([0-9A-F]{3,4})\b", c)} & starts


INDX = """
init:   AND #$03
        ASL A
        ASL A
        ASL A
        TAY
        LDX #0
fill:   TYA
        STA $40,X
        LDA #$30
        STA $41,X
        LDA #7
        STA $3000,Y
        INY
        INY
        INX
        INX
        CPX #6
        BNE fill
        RTS
play:   LDX #4
loop:   LDA ($40,X)
        CLC
        ADC #1
        STA ($40,X)
        STA $C001
        DEX
        DEX
        BPL loop
        RTS
end:
"""


def test_indexed_pointers_init_fills_are_plain(prefix, tmp_path):
    """``(zp,X)`` pointers init fills per argument, which play never stores, read and write
    their words: no dispatch, no bounds-checked reader and no code check."""
    img, labels, loaded = assemble(INDX)
    c, h, _ = translate(
        prefix, img, labels, loaded, entries=("init", "play"), init=0, songs=range(4)
    )
    body = body_of(c, "play")
    assert not any(f in body for f in ("rdC", "rd(", "wr(", "codechk"))
    for song in range(4):
        play(tmp_path, c, h, img, labels, [("init", song)] + [("play", 0)] * 20)


UNHELD = """
entry:  LDA ADDR
        STA $C000
        RTS
end:
"""
UNHELD_PTR = """
entry:  STA $FC
        LDA #$00
        STA $FB
        LDY #$00
        LDA ($FB),Y
        STA $C000
        RTS
end:
"""


def poweron(src, org=0x1000):
    """:func:`assemble` of ``src`` loaded over the C64's power-on RAM."""
    img, labels, (lo, hi) = assemble(src, org)
    ram = bytearray(poweron_ram())
    ram[lo:hi] = img[lo:hi]
    return ram, labels, (lo, hi)


@pytest.mark.parametrize("addr,value", [(0x31EA, 0xFF), (0x31F0, 0x00)])
def test_unwritten_machine_byte_is_named(prefix, tmp_path, addr, value):
    """A constant-address load of a byte past the image that no store writes reads the
    machine's entry byte there, which the ``.h`` names: no dispatch, no array."""
    img, labels, loaded = poweron(UNHELD.replace("ADDR", f"${addr:04X}"))
    assert img[addr] == value
    c, h, info = translate(prefix, img, labels, loaded)
    play(tmp_path, c, h, img, labels, entry_calls([0, 1]))
    assert re.search(rf"^#define MEM_K{addr:04X} 0x{value:02X}$", h, re.M)
    assert f"MEM_K{addr:04X}" in body_of(c, "entry") and "rd(" not in c
    assert not info["traps"] and not info["regions"]


def test_written_machine_byte_is_not_named(prefix, tmp_path):
    """A byte past the image some store writes is a variable: its region holds it."""
    src = UNHELD.replace("ADDR", "$31EA").replace("RTS", "INC $31EA\n        RTS")
    img, labels, loaded = poweron(src)
    c, h, info = translate(prefix, img, labels, loaded)
    play(tmp_path, c, h, img, labels, entry_calls(range(4)))
    assert "MEM_K" not in h and (0x31EA, 1) in info["regions"]


def test_volatile_byte_is_not_named(prefix, tmp_path):
    """A constant-address load of a volatile byte is a device read, whatever the image holds."""
    img, labels, loaded = poweron(UNHELD.replace("ADDR", "$C080"))
    c, h, _ = translate(prefix, img, labels, loaded)
    play(tmp_path, c, h, img, labels, entry_calls([0, 1]))
    assert "MEM_K" not in h and "io_read(0xC080)" in body_of(c, "entry")


def test_unknown_read_of_unheld_byte(prefix, tmp_path):
    """A load of unknown address reads the machine's entry byte where no array holds the byte
    (``cgen_rd``'s background table), and traps on a byte of the program it holds none of."""
    img, labels, loaded = poweron(UNHELD_PTR)
    c, h, _ = translate(prefix, img, labels, loaded)
    assert "rd(" in body_of(c, "entry") and "#define CGEN_BG_ORG 0x0, 0x4000" in h
    pages = [0x00, 0x31, 0x50, 0x90, 0xE5]
    assert {img[p << 8] for p in pages} == {0x00, 0xFF}
    play(tmp_path, c, h, img, labels, entry_calls(pages))
    lib = cgenplay.build(tmp_path / "t.c", tmp_path / "t.so")
    k = lib.call(ctypes.cast(lib.entry, ctypes.c_void_p), labels["entry"] >> 8)
    assert k == cgen.OOB_TRAP


UNHELD_RMW = UNHELD_PTR.replace(
    "        STA $C000",
    "        CLC\n        ADC #$01\n        STA ($FB),Y\n        LDA ($FB),Y\n        STA $C000",
)


def test_unknown_write_of_unheld_byte_reads_back(prefix, tmp_path):
    """A store of unknown address may write any byte, so every byte no array holds outside
    the program is held (``CGEN_RAM``, from the machine's entry bytes): each of more pages
    than any fixed count of written bytes reads back what was written."""
    img, labels, loaded = poweron(UNHELD_RMW)
    c, h, _ = translate(prefix, img, labels, loaded)
    assert re.search(r"\bwr\(", body_of(c, "entry")) and "#define CGEN_RAM_LEN 0xFFFF, 0x1" not in h
    n = int(re.search(r"^#define CGEN_RAM_N (\d+)$", h, re.M)[1])
    lo, hi = loaded
    assert n >= 0xFF00 - (hi - lo) - (W.IO[1] - W.IO[0])
    pages = [p for p in range(0x02, 0xC0) if not lo >> 8 <= p <= hi >> 8][::3]
    assert len(pages) > 32
    play(tmp_path, c, h, img, labels, entry_calls(pages, 2))


def test_store_past_its_array_into_the_program_traps(prefix, tmp_path):
    """A store past its array into the program traps where the 6502 would change the
    program: the memory map holds the store to the array its base lies in, so the store
    past it is invalid input, trapped at its check."""
    img, labels, loaded = assemble(PUSH)
    c, h, info = translate(prefix, img, labels, loaded)
    k = machine_guard(info, labels["entry"] + 3)
    play(tmp_path, c, h, img, labels, entry_calls([5, 6, 7, 8]), trap=(3, k))


@pytest.mark.parametrize("src", ["PUSH", "UNHELD_RMW", "ZPPTR", "STRAY", "CALLRET"])
def test_dispatch_compiles_for_every_host(prefix, tmp_path, src):
    """The dispatch compiles with no warning with the host's ``oob_*`` (``CGEN_OOB``), with
    ``CGEN_OOB_EXACT`` and with neither."""
    text = globals()[src]
    img, labels, loaded = assemble(text)
    kw = {"entries": ("init", "play"), "init": 0} if "init:" in text else {}
    c, h, _ = translate(prefix, img, labels, loaded, **kw)
    (tmp_path / "t.c").write_text(c)
    (tmp_path / "t.h").write_text(h)
    base = [f for f in cgenplay.CFLAGS if f not in ("-DCGEN_OOB_EXACT", "-shared")]
    for k, extra in enumerate(([], ["-DCGEN_OOB_EXACT"], ["-DCGEN_OOB"])):
        out = str(tmp_path / f"t{k}.o")
        subprocess.run(["gcc", *base, *extra, "-c", "-o", out, str(tmp_path / "t.c")], check=True)


def test_background_table_is_exact_and_small():
    """The background table reads back every cared byte, holding a periodic half in less than
    a unit per run; power-on RAM is four runs of two eight-byte units."""
    rng = np.random.default_rng(0)
    values = rng.integers(0, 256, 0x10000).astype(np.uint8)
    values[0x8000:] = np.resize(rng.integers(0, 256, 4), 0x8000)
    care = rng.random(0x10000) < 0.3
    entries, pool = regions.background(values, care)
    org = np.array([e[0] for e in entries])
    run = np.searchsorted(org, np.arange(0x10000), side="right") - 1
    at, mask = (np.array([e[i] for e in entries])[run] for i in (1, 2))
    got = np.frombuffer(pool, np.uint8)[(at + (np.arange(0x10000) & mask)) & 0xFFFF]
    assert org[0] == 0 and (got[care] == values[care]).all()
    assert len(entries) * regions.ENTRY + len(pool) < 0x8000
    entries, pool = regions.background(poweron_ram(), np.ones(0x10000, dtype=bool))
    assert [(o, m) for o, _, m in entries] == [(q << 14, 7) for q in range(4)] and len(pool) == 16


#: Three records 7 bytes apart, each reached through its own zero-page pointer pair init
#: leaves: ``($40,X)`` for ``X`` = 14, 7, 0.
INDXREC = """
init:   LDA #lo(rec)
        STA $40
        LDA #hi(rec)
        STA $41
        LDA #lo(rec+7)
        STA $47
        LDA #hi(rec+7)
        STA $48
        LDA #lo(rec+14)
        STA $4E
        LDA #hi(rec+14)
        STA $4F
        RTS
play:   LDX #14
pl:     JSR voice
        TXA
        SEC
        SBC #7
        TAX
        BPL pl
        RTS
voice:  LDA ($40,X)
        CLC
        ADC #$22
        STA ($40,X)
        STA $C000,X
        RTS
rec:    .byte 1,0,0,0,0,0,0,2,0,0,0,0,0,0,3
end:
"""


#: :data:`INDXREC` calling its voice routine with each ``X`` from its own site: ``X`` is the
#: routine's parameter, no loop's progression.
INDXREC_SITES = INDXREC.replace(
    """play:   LDX #14
pl:     JSR voice
        TXA
        SEC
        SBC #7
        TAX
        BPL pl
        RTS""",
    """play:   LDX #14
        JSR voice
        LDX #7
        JSR voice
        LDX #0
        JSR voice
        RTS""",
)


@pytest.mark.parametrize("src", ["INDXREC", "INDXREC_SITES"])
def test_zero_page_pairs_init_leaves_index_their_records(prefix, tmp_path, src):
    """``(zp,X)`` through pairs init leaves, each pointing at its own record: the address is
    the record's for each ``X`` (a loop's progression, or the values the lift finds ``X``
    holding), so the access indexes the records by ``X`` or the element, no 16-bit address
    subscript; the pairs are then never read, so no zero-page byte is held
    and init stores none of them; the ``.c`` is the same wherever the program lies."""
    outs = []
    for org in (0x1000, 0x2345):
        img, labels, loaded = assemble(globals()[src], org)
        c, h, info = translate(
            prefix, img, labels, loaded, entries=("init", "play"), init=0, songs=range(2)
        )
        assert not [
            r
            for r in info["regions"]
            if r[0] < 0x100 and re.search(rf"\bR{info['regions'].index(r)}\b", c)
        ]
        lifted = c[c.index("CGEN_STORAGE") : c.index("void init(")]
        assert not re.search(r"_ORG\)?\]", lifted)
        assert re.search(r"\b(?:R\d+|V\d+_f00)\[v\d+\] = ", lifted)
        (tmp_path / str(org)).mkdir()
        play(tmp_path / str(org), c, h, img, labels, [("init", 0)] + [("play", 0)] * 8)
        outs.append(c)
    assert outs[0] == outs[1]


#: The helpers a lifted access of unknown address goes through.
DISPATCH = re.compile(r"\b(?:wr|rd|codechk)\(")


def test_init_walking_a_table_of_operands_writes_them_directly(prefix, tmp_path):
    """An init relocating operands in place through a pointer from a table of their
    addresses ending at a zero high byte: the table is constant, so the stores' address sets
    are the operands, which init's translated code writes in their storage directly, no
    access of unknown address left; the ``.c`` is the same whichever order the table lists
    them (its values are the ``.h``'s)."""
    texts = set()
    for order in (
        "o0), hi(o0), lo(o1), hi(o1), lo(o2), hi(o2)",
        "o2), hi(o2), lo(o0), hi(o0), lo(o1), hi(o1)",
    ):
        src = WALKREL.replace("o0), hi(o0), lo(o1), hi(o1), lo(o2), hi(o2)", order)
        img, labels, loaded = assemble(src)
        c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
        (tmp_path / order[:2]).mkdir()
        play(
            tmp_path / order[:2],
            c,
            h,
            img,
            labels,
            [("init", 0)] + [("play", a) for a in range(4)] * 2,
        )
        ops = {labels[o] + k for o in ("o0", "o1", "o2") for k in (1, 2)}
        assert info["init"] == "translated" and ops <= set(info["fixed"])
        assert not DISPATCH.search(c) and re.search(OPERAND_K, body_of(c, "play"))
        texts.add(c)
    assert len(texts) == 1


def test_table_walk_stores_into_its_records_directly(prefix, tmp_path):
    """Stores through a pointer from a table of record addresses write the records' region
    directly: no access of unknown address."""
    c, _, _ = check(prefix, tmp_path, DATAWALK, entry_calls(range(4), 3))
    assert not DISPATCH.search(c)


def test_init_running_operands_it_relocated_reads_and_writes_directly(prefix, tmp_path):
    """Init runs a copy through operands its walk patched: no input decides init's run, so
    the copy's load and store have the addresses that run patched in, with no check of the
    pages they reach and no ``codechk``."""
    img, labels, loaded = assemble(PAIRREL)
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"), init=0)
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", a) for a in range(4)])
    assert info["init"] == "translated" and not DISPATCH.search(c)
    assert {labels["cp"], labels["cp"] + 3}.isdisjoint(pc for _, pc in info["traps"].values())


#: Init storing its song's step from a table of 32 at the song into a table of 32 ending the
#: program, and into play's immediate.
SONGTABLES = (
    """
init:   TAY
        LDA step,Y
        STA tab,Y
        STA add+1
        RTS
play:   LDA tab
        CLC
add:    ADC #$00
        STA $C000
        RTS
step:   .byte """
    + ", ".join(str(i * 5 % 32) for i in range(32))
    + """
tab:    .byte """
    + ", ".join(["0"] * 32)
    + """
end:
"""
)


def test_c_holds_for_any_count_of_declared_songs(prefix, tmp_path):
    """The ``.c`` is decided as if init's argument may be any byte: one, three or 32 declared
    songs give the same ``.c``, the ``.h`` their count, and each declared song replays. The
    guard on the store at the song, which only the declared songs keep inside its table, is in
    the ``.c``, implied by the entry."""
    out = {}
    for n in (1, 3, 32):
        values = sorted({0, n // 2, n - 1})
        c, info, labels = songs(prefix, tmp_path / str(n), SONGTABLES, values, declared=range(n))
        meta_keyed(info)
        assert info["init"] == "translated"
        out[n] = (c, (tmp_path / str(n) / "0" / "t.h").read_text(), info)
    assert out[1][0] == out[3][0] == out[32][0]
    assert len({h for _, h, _ in out.values()}) == 3
    store = labels["init"] + 4
    for _, _, info in out.values():
        [t] = [t for t in info["trap_info"].values() if t["pc"] == store]
        assert t["kind"] == "index" and t["implied_by"] == [traps.ENTRY]
        assert as_runs(t["guards"][0]["ok"]) == [(0, 31)]


#: A record cursor into patterns a table of pointers names, stepped a byte a call and reset
#: at a pattern's 0 terminator to the next pattern; ``{set}`` forms the read ``rd`` of the
#: pattern's byte at the cursor from ``Y`` the pattern number, ``{p2}`` the last pattern's
#: bytes (the program's last).
MACHINE = """
init:   LDA #$00
        STA pat
        STA pos
        RTS
play:   LDY pat
{set}
        BEQ wrap
        STA $C000
        INC pos
        RTS
wrap:   STA pos
        LDY pat
        LDA nxt,Y
        STA pat
        RTS
pat:    .byte 0
pos:    .byte 0
plo:    .byte lo(p0), lo(p1), {lo2}
phi:    .byte hi(p0), hi(p1), {hi2}
nxt:    .byte 1, 2, 0
p0:     .byte 1, 2, 3, 0
p1:     .byte 4, 5, 0
p2:     .byte {p2}
end:
"""
#: The read of each addressing mode, ``rd`` its instruction.
MODES = {
    "zpy": """        LDA plo,Y
        STA $FB
        LDA {hi},Y
        STA $FC
        LDY pos
rd:     LDA ($FB),Y""",
    "smc": """        LDA plo,Y
        STA rd+1
        LDA {hi},Y
        STA rd+2
        LDY pos
rd:     LDA $FFFF,Y""",
}


#: A stream of record pointers ended by a 0 high byte, each record's byte read through
#: ``(zp,X)``; ``{t}`` the terminators.
ZPX = """
init:   LDA #$00
        STA pos
        RTS
play:   LDY pos
        LDA rlo,Y
        STA $FB
        LDA rhi,Y
        BEQ wrap
        STA $FC
        LDX #$00
rd:     LDA ($FB,X)
        STA $C000
        INC pos
        RTS
wrap:   STA pos
        RTS
pos:    .byte 0
rlo:    .byte lo(d0), lo(d1), lo(d2), lo(d1){t}
rhi:    .byte hi(d0), hi(d1), hi(d2), hi(d1){t}
d0:     .byte 1
d1:     .byte 2
d2:     .byte 3
end:
"""


def machine_src(mode, p2="6, 7, 8, 9, 0", hi2="hi(p2)", lo2="lo(p2)", hi="phi"):
    """:data:`MACHINE` reading through ``mode`` (:data:`MODES`, or ``zpx``: :data:`ZPX`)."""
    if mode == "zpx":
        return ZPX.format(t=", 0" if p2.endswith("0") else "")
    return MACHINE.format(set=MODES[mode].format(hi=hi), p2=p2, lo2=lo2, hi2=hi2)


def machine_guards(info):
    """The traps of the memory map's checks, ascending."""
    return sorted(
        {
            int(k)
            for k, t in info["trap_info"].items()
            for g in t["guards"]
            if g["kind"] == "objchk" and g["def"] == "machine"
        }
    )


def machine_guard(info, pc):
    """The trap of the memory map's check of the access at ``pc``, or None."""
    ks = [
        int(k)
        for k, t in info["trap_info"].items()
        for g in t["guards"]
        if g["kind"] == "objchk" and g["def"] == "machine" and g["at"] == pc
    ]
    assert len(ks) <= 1
    return ks[0] if ks else None


@pytest.mark.parametrize("mode", [*MODES, "zpx"])
def test_machine_model_bounds_every_mode(prefix, tmp_path, mode):
    """A read whose bases (a pointer table's words, through a zero-page pair with ``Y``, a pair
    read ``(zp,X)``, or self-modified operand cells) lie in the program reads it with
    no bounds-checked reader, its address checked first (``objchk``, ``def`` "machine"):
    leaving the program would read I/O, ROM or RAM it never held. Every pattern replays;
    without the last pattern's terminator the cursor runs off the program and the C traps at
    that check, the calls before it exact."""
    img, labels, loaded = assemble(machine_src(mode))
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    body = body_of(c, "play")
    assert not re.search(r"\b(?:rd|oob_rd|rdC\d+)\(", body) and "CGEN_OBJ" in body
    k = machine_guard(info, labels["rd"])
    assert k is not None and info["trap_info"][k]["implied_by"] is None
    calls = [("init", 0)] + [("play", 0)] * 40
    play(tmp_path, c, h, img, labels, calls)
    img, labels, loaded = assemble(machine_src(mode, p2="6, 7, 8, 9"))
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    (tmp_path / "bad").mkdir()
    k = machine_guard(info, labels["rd"])
    # ``(zp,X)``: past its records the stream's next high byte is a record's byte, naming a
    # page outside the program.
    n = 1 + 4 if mode == "zpx" else 1 + 4 + 3 + 4
    play(tmp_path / "bad", c, h, img, labels, calls, trap=(n, k))


@pytest.mark.parametrize("mode", list(MODES))
def test_machine_model_is_independent_of_the_data(prefix, tmp_path, mode):
    """Song data reaches only the .h: whatever words the pointer table holds, the read through
    the pointer they make is checked by the memory map, the .c the same. A word naming bytes
    outside the program (RAM it never loaded) is invalid input: the calls
    replay exactly up to the read through it, which traps at that check."""
    img, labels, loaded = assemble(machine_src(mode))
    c, _, _ = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    img, labels, loaded = assemble(machine_src(mode, lo2="$00", hi2="$40"))
    c2, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    k = machine_guard(info, labels["rd"])
    assert c2 == c and k is not None
    calls = [("init", 0)] + [("play", 0)] * 20
    play(tmp_path, c2, h, img, labels, calls, trap=(1 + 4 + 3, k))


@pytest.mark.parametrize(
    "src",
    [
        machine_src("zpy", hi="$C011").replace("LDA $C011,Y", "LDA $C011"),
        machine_src("zpy").replace("LDA phi,Y", "LDA #$C0"),
    ],
    ids=["unknown", "constant"],
)
def test_machine_model_leaves_bases_outside_the_program_checked(prefix, tmp_path, src):
    """A pointer whose high byte is unknown (read from I/O) or a constant of the code naming
    an I/O page keeps its bounds-checked or device read, no check of the memory map's; the
    program replays."""
    img, labels, loaded = assemble(src)
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    assert machine_guard(info, labels["rd"]) is None
    assert re.search(r"\b(?:rd|rdC\d+|io_read)\(", body_of(c, "play"))
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", 0)] * 20)


JUMPS = """
init:   LDA #$00
        STA pos
        RTS
play:   LDY pos
        LDA cmds,Y
        TAY
        LDA jlo,Y
        STA vec
        LDA jhi,Y
        STA vec+1
        INC pos
        JMP (vec)
h0:     LDA #$00
        STA pos
        RTS
h1:     STA $C000
        RTS
h2:     STA $C001
        RTS
vec:    .byte 0, 0
pos:    .byte 0
jlo:    .byte lo(h0), lo(h1), lo(h2)
jhi:    .byte hi(h0), hi(h1), hi(h2)
cmds:   .byte {cmds}
end:
"""


def test_machine_model_bounds_a_jump_tables_command_stream(prefix, tmp_path):
    """A ``JMP (ind)`` through a vector stored from a table a command stream indexes: the
    stream's read, its cursor unbounded, reads the program checked by the memory map; the
    table's index guard keeps the jump inside its table. Without its 0 terminator the
    stream runs off the program and the C traps at the stream read's check."""
    img, labels, loaded = assemble(JUMPS.format(cmds="1, 2, 1, 1, 2, 0"))
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    rd = labels["play"] + 3
    assert machine_guard(info, rd) is not None
    assert not re.search(r"\b(?:rd|oob_rd|rdC\d+)\(", body_of(c, "play"))
    calls = [("init", 0)] + [("play", 0)] * 24
    play(tmp_path, c, h, img, labels, calls)
    img, labels, loaded = assemble(JUMPS.format(cmds="1, 2, 1, 1, 2"))
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    (tmp_path / "bad").mkdir()
    play(tmp_path / "bad", c, h, img, labels, calls, trap=(1 + 5, machine_guard(info, rd)))


ZPWRAP = """
init:   LDA #$00
        STA pos
        RTS
play:   LDX pos
        LDA $F8,X
        STA $C000
        INC pos
        RTS
pos:    .byte 0
end:
"""


def test_zero_page_index_wraps_inside_zero_page(prefix, tmp_path):
    """``zp,X`` wraps inside zero page for every ``X``: its base is no byte of the program, its
    address never leaves zero page, so the memory map adds no check and the read is plain
    past many wraps."""
    img, labels, loaded = assemble(ZPWRAP)
    for a in range(0x100):
        img[a] = a ^ 0x5A
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    assert machine_guard(info, labels["play"] + 2) is None
    assert not re.search(r"\b(?:rd|oob_rd|rdC\d+)\(", body_of(c, "play"))
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", 0)] * 600)


SONGBLOCK = """
init:   LDX #2
        LDA #0
iloop:  STA cur,X
        DEX
        BPL iloop
        RTS
play:   LDX #2
vloop:  LDA ordlo,X
        CLC
        ADC base
        STA $FB
        LDA ordhi,X
        ADC base+1
        STA $FC
        LDY cur,X
rd:     LDA ($FB),Y
        CMP #$FF
        BNE note
        LDA #0
        STA cur,X
        BEQ next
note:   STA add+1
        CLC
add:    ADC #0
        STA $C000,X
        INC cur,X
next:   DEX
        BPL vloop
        RTS
cur:    .byte 0,0,0
base:   .byte lo(pats),hi(pats)
ordlo:  .byte 0,4,10
ordhi:  .byte 0,0,0
pats:   .byte 1,2,3,$FF
        .byte 4,5,6,7,8,$FF
        .byte 9,$FF
end:
"""


@pytest.mark.parametrize("aim", [None, "play"])
def test_pointer_from_song_data_stays_in_the_data_block(prefix, tmp_path, aim):
    """A pattern pointer formed from the program's data (a table's offset plus a base word the
    program holds) stays in the data block, the program's data bytes, none of its code or
    cells: reaching the code, the player would read its own instructions as song data. Its
    reads are plain reads of the data, checked against the block first (``objchk``, ``def``
    "machine"), with no cell found by address and no code held; init's code, which play never
    runs, is bytes play may read. A song pointer aimed into play's code traps at that check."""
    img, labels, loaded = assemble(SONGBLOCK)
    if aim:
        off = (labels[aim] - labels["pats"]) & 0xFFFF
        img[labels["ordlo"] + 1], img[labels["ordhi"] + 1] = off & 0xFF, off >> 8
    c, h, info = translate(prefix, img, labels, loaded, ("init", "play"), init=0)
    body = body_of(c, "play")
    assert not re.search(r"\b(?:rd|oob_rd|rdC\d+|cgen_var)\(", body)
    assert all(labels["cur"] <= lo and hi <= labels["end"] for lo, hi in info["code_held"])
    k = machine_guard(info, labels["rd"])
    [ok] = [g["ok"] for g in info["trap_info"][k]["guards"]]
    assert ok == [(labels["init"], labels["play"] - 1), (labels["cur"], labels["end"] - 1)]
    calls = [("init", 0)] + [("play", 0)] * 200
    play(tmp_path, c, h, img, labels, calls, trap=(1, k) if aim else None)
