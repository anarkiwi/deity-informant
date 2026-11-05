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
from deity_informant import NATIVE, cgen, trace
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


def translate(prefix, img, labels, loaded, entries=("entry",), facts=None, sensitive=()):
    """``(c, h, info)`` of the program."""
    ents = [(labels[n], n) for n in entries]
    return cgen.translate(
        img, ents, [W.IO], sensitive, facts, loaded=loaded, header="t.h", prefix=prefix
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
    c, h, info = check(prefix, tmp_path, PTRS, entry_calls([0, 1, 1, 0]))
    assert info["unknown_access"] and "rd(" in c
    assert all(f"R{k}_ORG" in h for k in range(len(info["regions"])))


def test_pointer_reads_are_kept_off_the_stack_and_io(prefix, tmp_path):
    # Read-sensitive I/O would keep the throw-away reads of a page-crossing (zp),Y: the
    # pointer's address set (the words of its table) misses the I/O pages and page 1, so
    # there are none, no check is needed and pushes need not be in memory.
    c, _, info = check(prefix, tmp_path, PTRS, entry_calls([0, 1, 1, 0]), sensitive=(0xC0FF,))
    assert not info["traps"] and "io_read(" not in c.split("void entry")[1]
    assert all(org + n <= 0x100 or org >= 0x200 for org, n in info["regions"])


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
    assert f"#define R{k} (C0 + " in c and info["code_span"][1] == labels["buf"]


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
        (tmp_path / str(a)).mkdir()
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
    and the I/O pages: it reads memory through ``rd``, no deopt."""
    c, _, info = check(prefix, tmp_path, ANYPTR, [("entry", 0x20)] * 2)
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
    assert re.search(rf"io_write\(0xC000 \+ {param} \* 7,", c)
    assert len(re.findall(rf"\b{name}\([0-2]\)", c)) == 3


def test_copies_operand_cells_indexed_directly(prefix, tmp_path):
    """Each copy's own operand cell lies in one held span: the copies index it at their
    stride, computed once per call in 8 bits, with no ``rd``/``wr``."""
    c, _, _ = check(prefix, tmp_path, VOICES, entry_calls(range(4), 6))
    assert not re.search(r"\b(?:rd|wr)\(", c)
    [stride] = re.findall(r"uint8_t (v\d+) = v1 \* 0x18;", c)
    assert len(re.findall(rf"C\d+\[{stride}\]", c)) == 3


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


def test_entry_run_closes_the_operands_it_writes(prefix, tmp_path):
    """An init whose run is the same on every call (its reads constant or its own stores, its
    branches decided) writes each operand a sum carried across its two bytes, the value it
    holds: the run pairs each store's addresses with its values, so the operands stay closed
    at them and the stores need no code check."""
    img, labels, loaded = assemble(RUNREL)
    c, h, info = translate(prefix, img, labels, loaded, entries=("init", "play"))
    play(tmp_path, c, h, img, labels, [("init", 0)] + [("play", a) for a in range(4)] * 2)
    p, _ = cgen.lift(img, [(labels[e], e) for e in ("init", "play")], [W.IO], loaded=loaded)
    ops = [labels[o] + k for o in ("o0", "o1") for k in (1, 2)]
    assert {a: bytes(p.sets[a]) for a in ops} == {a: img[a : a + 1] for a in ops}
    assert info["stats"]["code_guards"] == 0


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
