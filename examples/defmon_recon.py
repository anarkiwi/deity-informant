"""Reverse-engineer defMON's IRQ setup: control regs after init + handler disasm."""
import sys; sys.path.insert(0,"/tmp/claude-1000/-scratch-anarkiwi-cbm-pysidtracker/7eaea9e5-2b6d-46f5-9697-d13313f69bef/scratchpad")
import deity_informant as P
from pysidtracker.image import SidImage
from pysidtracker.detect import run_init
from pysidtracker.trace import trace_init

data=open(f"{sys.argv[1] if len(sys.argv)>1 else '/tmp/claude-1000/-scratch-anarkiwi-cbm-pysidtracker/7eaea9e5-2b6d-46f5-9697-d13313f69bef/scratchpad/tunes/Stella_defMON.sid'}",'rb').read()
img=SidImage.from_bytes(data); h=img.header
tr=trace_init(img,play_calls=0)
run_init(img)  # materialise; leaves control regs in mem
m=img.mem
def w(a): return m[a]|(m[a+1]<<8)
print("init=%04x handler(irq_vec $0314)=%04x hw_irq($FFFE)=%04x"%(h.init_address,w(0x0314),w(0xFFFE)))
print("VIC: D011=%02x D012=%02x D019=%02x D01A=%02x  raster_cmp=%d"%(m[0xD011],m[0xD012],m[0xD019],m[0xD01A],((m[0xD011]&0x80)<<1)|m[0xD012]))
print("CIA1: TA=%04x DC0E=%02x DC0D(mask RAM)=%02x  TB=%04x"%(w(0xDC04),m[0xDC0E],m[0xDC0D],w(0xDC06)))
print("CIA2: TA=%04x DD0E=%02x DD0D=%02x"%(w(0xDD04),m[0xDD0E],m[0xDD0D]))

# recursive-descent disassembler over deity_informant.OPS
STOP={"RTS","RTI","JMP","BRK","JAM"}
def dis(start, limit=400):
    seen=set(); q=[start]; out={}
    while q:
        pc=q.pop()
        while 0<=pc<0x10000 and pc not in seen and len(out)<limit:
            seen.add(pc); op=m[pc]
            if op not in P.OPS: out[pc]=("??%02x"%op,); pc+=1; continue
            mn,md=P.OPS[op]; ln=P.MODE_LEN[md]
            lo=m[(pc+1)&0xFFFF]; hi=m[(pc+2)&0xFFFF]; word=lo|(hi<<8)
            if md=="imm": txt="%s #$%02x"%(mn,lo)
            elif md=="rel":
                tgt=(pc+2+(lo-256 if lo&0x80 else lo))&0xFFFF; txt="%s $%04x"%(mn,tgt); q.append(tgt)
            elif md in("abs","absx","absy","ind"): txt="%s $%04x%s"%(mn,word,{"absx":",x","absy":",y","ind":" (ind)"}.get(md,""))
            elif md in("zp","zpx","zpy","indx","indy"): txt="%s $%02x%s"%(mn,lo,{"zpx":",x","zpy":",y","indx":",(x)","indy":",(y)"}.get(md,""))
            else: txt=mn
            out[pc]=(txt,)
            if mn=="JSR": q.append(word)
            if mn in STOP:
                if mn=="JMP" and md=="abs": pc=word; continue
                break
            pc+=ln
    return out
HANDLER=w(0xFFFE)
print("\n=== handler @ %04x ==="%HANDLER)
d=dis(HANDLER)
for a in sorted(d): print("  %04x  %s"%(a,d[a][0]))
