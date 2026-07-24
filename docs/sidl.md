# SIDL — guarded frame-template representation

SIDL is a text-based, lossless, executable representation of a C64 playroutine:
the decompiler output of `deity_informant.sidl`. A `.sidl` document replays the
original program's observable register/value write stream byte-exactly, in
order, without any 6502 code — and parses back (`loads`) into the same program
it was emitted from (`dumps`).

Module: `deity_informant/sidl.py` (`build`, `dumps`, `loads`, `Program`,
`reference_log`). Built on the symbolic window recorder
([symbolic-recorder.md](symbolic-recorder.md), [smc-recovery.md](smc-recovery.md)).

## Semantic model

A playroutine is *N* invocations of a play entry over an evolving machine
state. The recorder proves each invocation is a **straight-line template**: an
ordered list of stores whose values are entry-state expressions, justified by
**case facts** (branch/target/place/opcode folds) that pin the control path.
SIDL reifies exactly that:

```
state    = sparse cell image (only the bytes the templates read) + 8 registers/flags
frame    = the first template whose checks all hold, evaluated in machine order
template = ordered events:  ck <kind> @site expr = observed   (guard)
                            st $addr <- expr                  (store)
           + end-of-frame register expressions
output   = every st to an address in `outputs`, emitted in order
```

There is no program counter, no opcode decoding, no self-modifying code: SMC,
computed dispatch, and illegal-opcode data flow have already been residualised
into the expressions by the recorder.

### Dispatch soundness

A template's checks are the recorder's fold justifications: every concrete
quantity specialised into the trace that depended on mutable state is either an
entry-pure expression (residualised — stays symbolic in SIDL) or a case fact
(becomes a `ck`). Conditional branches are always facted. Therefore:

- If a state satisfies all checks of a template (in machine order), the
  concrete 6510 from that state follows the recorded path, and the template's
  stores equal the machine's writes byte-for-byte — replay is exact, including
  on frames the recorder never saw.
- Two distinct templates cannot both match: their paths diverge at some first
  control decision, which appears as a `ck` with different observed values
  after an identical event prefix. The interpreter exploits exactly this shape:
  templates dispatch through a decision trie (shared prefixes evaluate once per
  frame; each diverging check branches on its value), so a frame costs one
  template's events, independent of the template count.
- If no template matches (state left the recorded path vocabulary) or an
  expression reads a cell outside the captured image, the interpreter raises
  `DispatchError` — a loud fault, never a silent wrong write.

Templates are deduplicated by content, not by path signature: two recorded
paths that differ only in effective addresses (e.g. successive rows of a music
table read through the same index expression) collapse into one generic
template. This is what makes SIDL *generative*: a 2-template program replays
arbitrarily many frames of a looping player.

### Why it is smaller and simpler than the disassembly

- Code bytes never read as data are not represented at all — the logic lives in
  the templates. The `mem` section holds only cells that are actually read:
  music data tables, counters, pointers, the driver stack bytes.
- Loops are unrolled per frame but deduplicated across frames, and templates
  are factored through a shared **segment dictionary** (runs of events ending
  at a check), so a template is one line of segment references.
- All 6502 idiom noise (flag micro-updates, RMW illegals, SMC patching) is
  already folded into simplified expressions. Checks whose predicate folded to
  a constant carry no dispatch information and are dropped (asserted at build).
- End-of-frame register templates are kept only for registers some expression
  actually consumes at frame entry (computed to a fixpoint); repeated
  subexpressions are bound once by global deterministic `let` CSE.

## Document layout

```
sidl 0                          ; format version
play $1003                      ; play entry (metadata)
frames 100                      ; recorded window length
outputs $D400..$D418            ; observable address set (ranges, comma-separated)
regs A=$00 X=$00 ... N=$00      ; post-init register/flag state
init {                          ; observable writes made by the one-shot init call
  st $D418 <- $0F
}
mem {                           ; initial value of every referenced cell
  $1369: 00 03 00 00 06 06 00
  $13BA: 00 00 00 00 09 00 00 F0 F0 00 00 00 00 09 00 00
}
let t0 = zext2(mem[$10EB])      ; global CSE bindings (deterministic, def before use)
let t28 = (t0 + $15FE):2
seg S0 {                        ; shared event runs, each ending at a check
  st $01FF <- $00
  st $01FE <- $01
  st $D400 <- mem[$13BA]
  ck branch @$117E (mem[$1393] == $01) = $00
}
seg S1 {
  st $1393 <- (mem[$1393] - $01)
  ck target @$13B4 ((zext2(cur[$01FC]) | (zext2(cur[$01FD]) << $08):2):2 + $0001):2 = $10DA
}
template T0 = S0 S1 S4 S5       ; a frame path: segment refs in machine order
  reg A <- mem[t28]             ; end-of-frame register templates (only live ones)
template T1 = S0 S2 S3 S5
uni {                           ; only for volatile readers: per-frame opaque reads
  0: 0=$1B 1=$52
}
```

`;` starts a comment anywhere. Init runs once and deterministically, so it is
carried as its literal observable writes; its full effect on memory is already
baked into `mem`/`regs` (the post-init snapshot). `let`/`seg`/`template` are a
purely textual factoring: `loads` expands them, and `dumps` re-derives them
deterministically, so the canonical text is a `loads`/`dumps` fixpoint.

## Expression syntax

Exactly the recorder's evolved-form algebra, rendered bijectively
(`parse_expr(fmt_expr(n)) == n`):

| Text | Node | Meaning |
|---|---|---|
| `$3F` / `$1400` | const (1 / 2 bytes by digit count) | literal |
| `A X Y SP C Z I D B V N` | reg | frame-entry register/flag |
| `mem[e]` | mem | byte at `e` in the **frame-entry** image |
| `cur[e]` | cur | byte at `e` in the **evolving** image (post prior stores) |
| `u7`, `u7:2` | uni | opaque recorded value (volatile read) |
| `(a + b - $01)` | INT_ADD (n-ary, flat) | wrap at node width; `- k` is two's-complement sugar |
| `(a - b)` | INT_SUB | non-constant subtrahend only |
| `(a \| b)` `(a ^ b)` `(a & b)` | INT_OR/XOR/AND (n-ary, flat) | bitwise |
| `(a << b)` `(a >> b)` | INT_LEFT/RIGHT | shifts |
| `(a == b)` `(a != b)` `(a < b)` `(a <= b)` | comparisons | unsigned, width 1 |
| `zext2(a)` | INT_ZEXT | widen to 2 bytes |
| `carry(a, b)` | INT_CARRY | unsigned carry-out |
| `(...):2` | width suffix | node width 2 (default 1) |
| `t7` | `let` reference | textual alias for a bound subexpression |

Evaluation follows the recorder's normative machine-order rule: within a frame,
`mem` reads the frame-entry snapshot, `cur` reads the image as of the event's
own position, stores apply one by one. `ck` kinds (`branch`, `target`, `place`,
`opcode`) name the fold channel that produced the guard (see
[smc-recovery.md](smc-recovery.md)); all evaluate identically.

## Volatile reads

Reads of modelled volatile IO ($D011..$D41C raster/oscillator/envelope, $D019,
$DC0D) are opaque `uni` leaves; the `uni` section carries their recorded
per-frame values, keeping replay lossless over the recorded window. Beyond it,
a template needing an unrecorded `uni` faults loudly. Deriving these values
from the deterministic cycle model instead (making volatile programs
generative) is future work — it requires the cycle layer below.

## Losslessness contract

`Program.run(n)` returns `(init_writes, [per-frame ordered (addr, value)
writes])` and is asserted byte-identical to the concrete `PcodeVM` stream
(`reference_log`) — by construction over the recorded window (the recorder's
record-time assertion gates every artifact, including the end-of-frame register
templates), and by the dispatch-soundness argument on any later frame that
stays within the recorded path vocabulary and captured data. Outside that
envelope the interpreter raises `DispatchError` rather than approximating.
Write *cycle timestamps* are not yet represented: the stream is
order-lossless, not cycle-stamped. Since paths pin every effective address,
per-store cycle offsets are template-constants — attaching them (and an
interrupt-cadence header for `run_irq_driven` tunes) is the planned extension.

## Real-tune results

100/500 frames of two HVSC tunes (PSID, `run_sub` per frame, `--verify`
byte-exact, ~1s/4s build):

| Tune | frames | templates | segments-per-template shared | cells | text |
|---|---|---|---|---|---|
| Jammer, *Grid Runner* | 100 / 500 | 68 / 167 | ~10x event dedup | 291 / 414 | 49 / 76 KiB |
| Rob Hubbard, *Commando* | 100 / 500 | 58 / 108 | ~10x event dedup | 240 / 371 | 56 / 79 KiB |

Text grows sub-linearly (5x frames -> ~1.5x text) as the path vocabulary
saturates. Template count tracks *distinct frame paths*; factoring below the
frame (sharing the per-voice sub-paths a tracker player composes) is the next
compression level and the main future-work item, alongside dead-store pruning
and cycle-stamped writes.

## API and CLI

```python
from deity_informant import sidl

prog = sidl.build(mem, play, frames, init=init, outputs=sidl.SID_OUTPUTS,
                  window=None)   # window=N: parallel N-frame recording windows,
                                 # byte-identical output by concrete determinism
text = sidl.dumps(prog)            # canonical text; loads() is its exact inverse
prog2 = sidl.loads(text)
prog2.run()                        # (init_writes, frame_writes) — byte-exact
sidl.reference_log(mem, play, frames, init=init)   # concrete-VM oracle, same shape
```

```bash
deity-informant sidl IMAGE --play ADDR [--init ADDR] [--frames N] [--window W] [--verify] [-o FILE]
deity-informant sidl-run FILE [--frames N]      # interpret; prints the $D400.. grid
```

`--verify` round-trips the text and replays it against the VM before writing.

## Acceptance tests

`tests/test_sidl.py`: byte-exact replay + exact text round-trip over the full
`_fuzzgen` corpus (SMC, computed dispatch, illegals, volatile, variable-row);
generalization to 3× the recorded window for cyclic players; loud
`DispatchError` on missing templates, missing cells, and finite-stream
overrun; expression-text bijectivity; parser rejection cases.
