"""defMON as a cycle-timestamped CIA-driven play, framed at the VIC rate."""
import sys; sys.path.insert(0,"/tmp/claude-1000/-scratch-anarkiwi-cbm-pysidtracker/7eaea9e5-2b6d-46f5-9697-d13313f69bef/scratchpad")
import deity_informant as P; P.load_cycle_tables()
from deity_informant import PcodeVM, lift, run_sub, run_irq
from pysidtracker import registers as reg
from pysidtracker.image import SidImage
from pysidtracker.trace import trace_init
from pysidtracker.oracle import read_sidtrace, sidtrace_grid, grid_from_writes
PW=set(reg.PW_HI_REGS)
def mask(g): return [[(v&0xF) if i in PW else v for i,v in enumerate(r)] for r in g]
def align(orc,my,rng=10):
    best=(0,-1,0)
    for off in range(rng+1):
        n=min(len(orc)-off,len(my))
        c=sum(1 for i in range(n) if orc[off+i]==my[i])
        if c>best[1]: best=(off,c,n)
    return best

CIA=19760; VIC=19656
data=open(f"/tmp/claude-1000/-scratch-anarkiwi-cbm-pysidtracker/7eaea9e5-2b6d-46f5-9697-d13313f69bef/scratchpad/tunes/Stella_defMON.sid","rb").read()
img=SidImage.from_bytes(data); h=img.header
tr=trace_init(img,play_calls=0); handler=tr.irq_vector or tr.hw_irq_vector
orc=mask(sidtrace_grid(read_sidtrace(f"/scratch/anarkiwi/cbm/pysidtracker/.sidtrace_oracle/Stella_defMON.csv.zst")))

vm=PcodeVM(bytes(img.mem)); vm.mem[0xD418]=0x0F
writes=[]
_orig=PcodeVM._wr
def hook(self,a,v,sz):
    if 0xD400<=a<=0xD418: writes.append((self.cycles,a-0xD400,v))
    return _orig(self,a,v,sz)
PcodeVM._wr=hook
run_sub(vm,h.init_address,{},lift)
cache={}
total=len(orc)*VIC + 4*CIA
t=vm.cycles
while t < total:
    vm.cycles=t
    run_irq(vm,handler,cache,lift)
    t+=CIA
PcodeVM._wr=_orig
my=mask(grid_from_writes(writes,cycles_per_frame=VIC))
off,c,n=align(orc,my)
print(f"defMON CIA-play framed@VIC: lead={off} {c}/{n} ({'EXACT' if c==n else 'DIFF'})")
if c!=n:
    for i in range(n):
        if orc[off+i]!=my[i]:
            d=[(hex(0xD400+j),orc[off+i][j],my[i][j]) for j in range(25) if orc[off+i][j]!=my[i][j]]
            print(f"  first diff frame {i}: {d[:6]}"); break
