# tuneprog — architecture

The canonical reference for `deity_informant/tuneprog/`: what a tuneprog is, how a
`.sid` becomes one, what is certified about it, and where every part of it lives.
Read this first; the records below are read after it, never instead of it.

Records, linked and never duplicated here:
[tuneprog-backlog.md](tuneprog-backlog.md) (open work by lever, done ledger),
[survey-tuneprog.md](survey-tuneprog.md) (the 7,023-tune HVSC campaign),
[playroutine-anatomy.md](playroutine-anatomy.md) (nine playroutines byte for byte),
[ghidra-highpcode-export.md](ghidra-highpcode-export.md) (the independent baseline),
`docs/prototype-*.md` (the certified exemplars), `docs/certificates/` (the evidence).

Numbers are from the tree at this document's commit, measured 2026-08-31:
69 modules, 20,663 lines, 53 certificates, 1,130 tests.

Contents: 1 definitions · 2 pipeline · 3 the lift end to end · 4 the IR ·
5 verification and the certificate · 6 presentation · 7 CLI and tools ·
8 machine model and boundaries · 9 exemplars and evidence · 10 module map ·
11 process.

---

## 1. Definitions

| term | meaning |
| --- | --- |
| **tune** | a PSID/RSID container: a load band, an `init(song)` entry, and a play entry — the header's `play`, or an interrupt handler the tune installs during `init` |
| **entry** | one procedure the machine calls on a schedule: `Entry(kind ∈ {sub, irq, nmi}, addr, cycles_per_tick, source, kernal)` |
| **schedule** | the entry list of one program: the tick entry, plus a CIA #2 NMI second entry where one is armed. Every entry shares one tick clock |
| **tick** | one call of the play entry at the cadence S0 discovered. The horizon flag spells it `--calls`, the certificate field `ticks` |
| **observable** | the ordered `(address, value)` SID writes of a tick (`$D400–$D7FF`), plus its schedule effects (VIC/CIA stores), plus `init(song)`'s own list |
| **certificate** | `certificate.json`: what was compared, over how many ticks, with what divergence, periodicity, stack status and coverage |
| **certified-equivalent** | for every tick of the horizon, on the recorded input stream and from the pre-init image, the emitted program's observable equals the trace's, byte for byte and in order. *Complete* adds a state repeat `(k, k+p)` consuming no input, so one period covers all later ticks |
| **site** | one `(pc, opcode, fixed operand bytes)` the trace executed; operand bytes the program writes drop out of the key (`tracedata.site_key`) |
| **cell** | an instruction byte some traced procedure writes — a self-modified operand or opcode |
| **region** | a connected component of the op-level access relation: one storage object with `base`, `size`, `kind`, `stride`, `fields`, `origin`, its pre-init `init` bytes and, where init wrote them, the `post` runs init left. Regions are disjoint by construction, so aliasing is exact |
| **accessor** | one load or store of one region, enumerated as `Acc(proc, region, store, base, idx, lo, hi)` — the address split included (`irwalk.accessors`) |
| **envelope** | the `[lo, hi]` extent an indexed access was observed inside; outside it the run stops with an `envelope` trap |
| **view** | a presentation copy of the certified program. S5/S6 rewrite the view; the certified IR is never edited |
| **copy / sibling** | k static copies of one template an unrolled player wrote out (Follin's three voices, defMON's cascade blocks). The **copy index** `v` is the value a merged family runs over; a **column** `T_x[v]` is the per-copy table an operand the copies disagree on is read from |
| **naming plane** | `recover.Names`: `region`, `role`, `view`, `groups`, `u16`, `slots`, `column`, `split`, `freq`, `phase`, `elem`, `notes`, `procs`, `copies`, `sidwrite`, `index`. Names never change semantics; they are serialised to `tuneprog.S6.json`, and `Names.from_dict` reads that back |
| **role** | what a region is used as: `sid_image`, `freq_table`, `counter`, `timer`, `cursor`, `ptr`, `acc`, `phase`, `table`, `voice_map`, `per_copy` |
| **coverage** | a merged block's execution count per copy (`Block.cover`); a zero says no execution of that copy reached it, and the statement is unverified |
| **input** | a read the program does not determine, classified by `tracedata.input_kind`: `raster`, `cia`, `sid_readback`, `ack`, `entry_reg`, `io`, `uninit_ram`. Equivalence is stated relative to the recorded stream |

The P/SID header is metadata, not ground truth. It seeds discovery (load band,
`init`, a candidate `play`); entries, cadence, clock and model behaviour come from
running the tune, and the `sidplayfp` oracle arbitrates.

---

## 2. Pipeline overview

`pipeline.py` drives every stage into one output directory, chunked against a CPU
budget (`run` returns `MORE = 2` while work remains). `resume.py` decides what a
resumed run may keep.

| stage | in | out | modules | artefact |
| --- | --- | --- | --- | --- |
| **S0** | `.sid` bytes | 64 KiB pre-init image, schedule, cadence | `machine`, `cia`, `nmi`, `..c64` | — |
| **S1** | image + schedule | `Trace`: sites, edges, calls, logs, inputs, per-tick hashes | `trace`, `tracevm`, `tracesite`, `traceflow`, `tracedata` | `trace.json`, `trace.npz` (+ `sNN/` per subtune under `--songs all`) |
| **S2a** | `Trace` | `{site: LiftedSite}` — residualised P-Code, SMC operands as loads | `lift` | — |
| **S2b** | sites + edges | procedures: clone-per-entry, tail calls, variant and computed switches | `cfg`, `jumptab`, `closure` | `procs.json` |
| **S2c** | post-init image + procedures | sibling families and the fold plan: one body under the copy index `v` | `siblings`, `copyrows`, `copymerge` | (reported in `certificate.json`, `tuneprog.S6.json`) |
| **S3** | `Trace` + `LiftedSite`s | regions: kinds, strides, fields, envelopes, origins | `regions` | `regions.json` |
| — | all of the above | the IR: one `Proc` per CFG procedure, one `Block` per node | `build`, `lower`, `wire`, `ir` | — |
| **S4** | IR | SSA, DCE, copy/const propagation, 6510 peepholes, stack elimination, then `jumptab.enumerate_targets` | `ssa`, `idioms`, `frames`, `stack`, `jumptab` | `tuneprog.S4.json`, `tuneprog.py` |
| **S8** | S4 program + `Trace` | per-tick differential verification, periodicity, the certificate | `verify`, `interp`, `emit`, `period`, `grid` | `certificate.json` |
| **S5** | a *copy* of the S4 IR | the structured node tree: loops, if/else, switch, `for`, phase | `structure`, `loops`, `graph` | `tuneprog.S5.json` |
| **S6** | the same copy | inlining, texture removal, region typing, 16-bit views, roles and names, per-register provenance | `inline`, `texture`, `cells`, `gated`, `ranges`, `frame`, `partition`, `halves`, `word`, `copyview`, `recover`, `facts`, `views`, `fold`, `tails`, `unroll`, `live`, `cellref`, `provenance` | `tuneprog.S6.json`, `tuneprog.T0.json` |
| **S7** | view + structure + names | Python code, the certificate document, the text form | `emit`, `pseudocode`, `printer`, `datablock` | `tuneprog.py`, `certificate.json`, `tuneprog.md` |
| driver | — | stage state, resume records, Ghidra facts | `pipeline`, `resume`, `ghidra_facts`, `ghidra_compare` | `state.json`, `tracer.pkl` (`tracerNN.pkl` per subtune), `verify.pkl`, `ghidra/` |

Stage entry points, which are also the module boundaries: `machine.find_entries`,
`nmi.entry`, `trace.run_trace`, `lift.lift_trace`, `cfg.build_procs`,
`regions.build_regions`, `build.build_ir`, `ssa.simplify`, `stack.eliminate`,
`emit.emit_python`, `verify.verify`, `siblings.correspond`, `copymerge.plan`,
`structure.structure`, `recover.recover`, `copyview.expand`,
`partition.repartition`, `views.decorate`, `provenance.document`, `printer.render`,
`datablock.section`.

`ir.py` and its reference interpreter `interp.py` are the semantics every other
executor is checked against; `irwalk.py` and `graph.py` are the IR and CFG
traversals every stage shares.

---

## 3. The lift, end to end

### 3.1 `.sid` to certified per-tick program

```
   Tune.sid  (PSID / RSID container)
        |
        |  c64.load_psid   load band, init, play, speed bits, clock, model
        |  c64.poweron_ram the RAM under and around the band
        v
  +----------------------------------------------------------------------+
  | S0   machine.py . cia.py . nmi.py                                     |
  |  MachineImage.from_sid  64 KiB pre-init image ($00=$2F, $01=$37)      |
  |  init_runner            init(song) to its balancing RTS or a `JMP *`  |
  |  vector_gate            which installed vector the 6510 port takes:   |
  |                         CINV $0314 / raw $FFFE / refuse "banked out"  |
  |  find_entries           cadence: cia_timer | pal_video | ntsc_video   |
  |                                | pal_host_cia | ntsc_host_cia         |
  |  nmi.entry              a CIA #2 NMI as the schedule's second entry   |
  +----------------------------------------------------------------------+
        |
        |  MachineImage, [Entry(kind, addr, cycles_per_tick, source, kernal)]
        v
  +----------------------------------------------------------------------+
  | S1   trace.py . tracevm.py . tracesite.py . traceflow.py              |
  |  Tracer.run_init, then run_calls: one tick per cadence period         |
  |  TraceVM.step   fetch -> execute -> resolve -> dispatch -> account     |
  |  cia.CIA        two 6526s; CIA#1's line is IRQ, CIA#2's is NMI        |
  |  nmi.Separable  a second entry preempts at an instruction boundary    |
  +----------------------------------------------------------------------+
        |
        |  Trace{sites, edges, calls, rets, summaries, cells, code,
        |        wlog, iolog, nmilog, inputs, input_sites,
        |        written_init, written_play, image_pre, image_post_init,
        |        state_hash(+_free), footprint_size(+_free), chip_ops}
        v
  +----------------------------------------------------------------------+
  | S2a  lift.py     residualised lift, one LiftedSite per site           |
  |      a constant varnode whose byte provenance hits a written cell     |
  |      becomes a LOAD of that cell's address                            |
  +----------------------------------------------------------------------+
        |  {site: LiftedSite{ops, ctrl, cell_loads, ptr_pairs, idx_ops,
        |                    ctrl_cell, src_map, cyc}}
        v
  +----------------------------------------------------------------------+
  | S2b  cfg.py . jumptab.py . closure.py    |    S3  regions.py         |
  |  entries = play entries, init, every     |     union the addresses   |
  |  JSR target; nodes cloned per entry      |     one P-Code op touched |
  |  jsr -> Call; a non-jsr edge into an     |     -- the components are |
  |  entry -> tail call                      |     the regions, disjoint |
  |  unmatched RTS / JMP(ind) / patched      |     by construction       |
  |  jump -> Switch over the observed        |     kind, stride, fields, |
  |  targets, trap default                   |     origin, envelope per  |
  |  an untaken direction -> Trap            |     accessor              |
  |  jumptab: a patched jump's static table closure over its extent      |
  |  closure: the bounded static walk of untaken directions (opt-in)     |
  +----------------------------------------------------------------------+
        |  procs (nodes keyed (entry, pc, opcode)) + regions [Rgn]
        v
  +----------------------------------------------------------------------+
  | S2c  siblings.py . copyrows.py . copymerge.py                         |
  |  k copies of one template, aligned pc by pc off the post-init image;  |
  |  copymerge.plan folds them to one body under the copy index `v`,      |
  |  operands the copies disagree on read from a per-copy column T_x[v]   |
  +----------------------------------------------------------------------+
        |  Plan{unions, columns, coverage, refused}   (re-runs S2a/S2b/S3)
        v
  +----------------------------------------------------------------------+
  | ---  build.py . lower.py . wire.py                                    |
  |  one Proc per CFG procedure, one Block per node; every P-Code operand |
  |  a leaf; every memory op resolved to its region, access class and     |
  |  envelope; registers/flags are procedure params and rets              |
  +----------------------------------------------------------------------+
        |  Tuneprog{meta, storage[Rgn], inputs, procs{name: Proc}}
        v
  +----------------------------------------------------------------------+
  | S4   ssa.py . idioms.py . frames.py . stack.py                        |
  |  merge_chains, to_ssa, copyprop, constprop, fold_branches, dce,       |
  |  canonical -- to a fixpoint, with idioms.rewrite as the peephole;     |
  |  then stack.eliminate: a push is the value its pops read, SP leaves   |
  |  every signature, or the program is `stack: residual`                 |
  +----------------------------------------------------------------------+
        |  the certified program            -> tuneprog.S4.json
        |  emit.emit_python                 -> tuneprog.py
        |
        +-------------------------------+
        |                               |
        v                               v
  +--------------------------+   +----------------------------------------------+
  | S8  verify.py . interp.py|   | S5  structure.py . loops.py . graph.py       |
  |  Reference(trace) vs the |   |  structure.view: a semantics-preserving copy |
  |  PyProgram, tick by tick |   |  loops from back edges, if/else from the     |
  |  ordered SID + io writes |   |  immediate post-dominator, Switch, For,      |
  |  footprint hashes, the   |   |  the phase variable, goto for the residue    |
  |  same keyed blake2b as   |   +----------------------------------------------+
  |  the tracer; period.py   |          |  Blk / Cond / Loop / For / Case /
  |  classifies a non-repeat |          |  Jump / Exit node tree
  |  grid.py frames writes   |          v
  |  for the sidplayfp       |   +----------------------------------------------+
  |  oracle                  |   | S6  inline texture cells gated ranges frame  |
  +--------------------------+   |     partition halves word copyview recover   |
        |                        |     facts views fold tails unroll live       |
        |  certificate.json      |     cellref  -- rewrites the view only       |
        v                        +----------------------------------------------+
   verified / diverged                  |  view + structured tree + Names
                                        v
                                 +----------------------------------------------+
                                 | S7  printer.py . pseudocode.py . datablock.py|
                                 |  meta / state / data / inputs / program      |
                                 +----------------------------------------------+
                                        |
                                        v
                                   tuneprog.md, tuneprog.S5.json, tuneprog.S6.json,
                                   tuneprog.T0.json   (then T1: tuneprog.T1.json,
                                   a library and a tool, not a pipeline stage)
```

### 3.2 One instruction, from bytes to a site record

```
  mem[pc .. pc+2]                       the post-init image bytes of this variant
       |
       v
  jennings.opcodes.OPCODES[b0] -> (mnemonic, mode)      256 rows, 105 illegal
  MODE_LEN[mode] -> 1 | 2 | 3            MEM_MODES -> does it compute an address
       |
       v
  lifter.lift(mem, pc)
       |    "ops"  [[op, out varnode, [in varnodes]], ...]   varnode = [space, off, size]
       |            space c = constant, r = register file, u = unique
       |            vocabulary: COPY LOAD STORE INT_ZEXT INT_ADD INT_SUB INT_AND
       |            INT_OR INT_XOR INT_LEFT INT_RIGHT INT_EQUAL INT_NOTEQUAL
       |            INT_LESSEQUAL INT_CARRY
       |    "len"  instruction length          "cyc"  CYCLETIME[b0]
       |    "pen"  None | ("branch",) | ("ax"|"ay", abs) | ("iy", zp)
       |    "ctrl" ("next",) | ("br", flag, pol, target, fall) | ("jmp", a)
       |           | ("jmpind", a) | ("jsr", a) | ("rts",) | ("rti",) | ("brk",)
       |           | ("jam",)
       |    "prov" {"op0": b0, "ops": {(op i, arg j): (byte offsets, fn)},
       |            "ctrl": (offsets, fn, value) | None}
       |    "stk"  None | "jsr" | "brk" | "rts" | "rti"
       v
  TraceVM._fetch      cache key = (pc, b0) | (pc, b0, b1) | (pc, b0, b1, b2)
       |              -- the VM's own record cache, so a site resolves once
       |              tracesite.build compiles the op list into one Python
       |              closure t[S_F] plus its fixed read/write masks
       v
  TraceVM.step        t[S_F]() executes; the accounting runs in the same pass
       |              per op: exact read set / write set, chip-or-RAM verdict
       |              index-register domain at indexed sites (IDX_REG)
       |              entry-register live-ins -> pinned inputs
       |              edges, JSR/RTS pairing, shadow stack (traceflow)
       |              $D400-$D7FF store -> wlog;  other $D000-$DFFF -> iolog
       v
  Trace.sites[ site_key(pc, opcode, fixed operand bytes) ]
                      operand bytes in Trace.cells are None in the key, so a
                      self-modified operand is one site that loads it, not one
                      site per value
```

### 3.3 One illegal opcode all the way through

`examples/hello_world.py`'s inner loop is `LAX $1013,Y` (`$BF`, `absy`) — an
illegal opcode whose Z flag the loop's `BEQ` rides — feeding a `STA $0400` whose
low operand byte `$100A` is self-modified by the equally illegal `ISC $100A`.

1. **Decode.** `OPCODES[0xBF] = ("LAX", "absy")`, `MODE_LEN["absy"] = 3`,
   `CYCLETIME[0xBF] = 4`, `EXTRACYCLES[0xBF] = 1`. No table here treats `$BF`
   differently from `$B9`: the 105 illegals are ordinary rows.
2. **Lift.** `lift(mem, 0x1002)` returns eight P-Code ops — `INT_ZEXT u2:2 = Y`,
   `INT_ADD u0:2 = $1013, u2`, `LOAD u4:1 = [u0]`, `COPY A = u4`, `COPY X = u4`,
   `INT_EQUAL Z = A, 0`, `INT_AND u5 = A, $80`, `INT_NOTEQUAL N = u5, 0`. The two
   register writes and the flag pair are all that makes `LAX` illegal, and nothing
   downstream knows it is. `pen = ("ay", 0x1013)` carries the page-cross penalty;
   `prov["ops"] = {(1, 0): ([1, 2], "word")}` says op 1's first input is
   instruction bytes 1–2 read as a word.
3. **Execute and record.** `TraceVM._fetch` keys the record
   `(0x1002, 0xBF, 0x13, 0x10)`, `tracesite.build` compiles the eight ops to one
   closure, and the step loop only indexes it thereafter. Op 2's read set is the
   one address `$1013 + Y`, and `IDX_REG["absy"] = 2` marks it affine in Y, so S3
   takes an index domain from this op and from no other.
4. **Residualise.** Nothing writes `$1003`/`$1004`, so `lift.lift_site` leaves the
   `$1013` constant alone. At `$1009` it does not: `ISC` writes `$100A`, so that
   byte is in `Trace.cells` and the `STORE [c $0400], A` becomes a store through
   `load16($100A)` — the abstraction `ghidra/6510/smc.py` spells as the SLEIGH
   `smc_addr` context constructor.
5. **Type and build.** The addresses op 2 touched, `$1013..$101F`, union into one
   read-only region inside the load band (`kind = "const"`), and each accessor
   keeps `[lo, hi]` as its envelope; `$0400..$040C` is a second region and `$100A`
   a one-byte `state` scalar. The `LOAD` becomes `Load(cls="ram", a=Bin("+",
   Const(0x1013, 2), Var("Y")), lo=0x1013, hi=0x101F, r=<id>)` — `ram` because
   every byte of the extent is inside the load image.
6. **S4.** `Z` and `N` are ordinary SSA values; the `BEQ` consumes `Z`, so
   `idioms.rewrite` turns the pair back into a relational test on the loaded byte
   and `dce` drops `N` and the `INT_AND` behind it. Eight P-Code ops become one or
   two statements; the measured S4 rate is 1.0–1.6 statements per instruction.
7. **Verify and print.** Both executors run the result and `verify._compare`
   checks the ordered write list per tick; the load prints under its region's S6
   name and the index expression `cellref` chose, with the accessor listed once in
   `## data` beside the bytes it reads.

Step 1 is the point: a static tool that does not decode `$BF` sees data, and one
that folds `$100A` to a constant sees the wrong store. 4.7 % of traced HVSC tunes
execute an illegal opcode in play; 55.3 % self-modify an executed instruction byte.

---

## 4. The IR

`Tuneprog = {meta, storage: [Rgn], inputs, procs: {name: Proc}}`. Memory is one
flat 64 KiB image; a region is a *view* of it.

### 4.1 Nodes

| class | fields | notes |
| --- | --- | --- |
| `Const` | `v, w` | `w` is the byte width, 1 or 2 |
| `Var` | `n, w` | a `let`-bound value; registers and flags are ordinary `Var`s |
| `Load` | `cls, a, w, lo, hi, r` | `mem[a]` as `w` little-endian bytes through access class `cls`, inside envelope `[lo, hi]` of region `r` |
| `Bin` | `op, a, b, w` | `w` is the width the result is masked to (`carry`: its inputs') |
| `R16` | `lo, hi, a` | **S6 only** — a 16-bit read of two cells; a cell is `(region, constant address)` |
| `Let` | `n, e` | |
| `Store` | `cls, a, v, w, lo, hi, r, src` | `src` is the pc that made it |
| `Call` | `proc, args, rets` | |
| `Assert` | `e, why` | the envelope check; failure is a trap |
| `Phi` | `n, args` | SSA only, gone after `from_ssa` |
| `W16` | `lo, hi, a, e, src, hifirst` | **S6 only** — a 16-bit assignment; `hifirst` records the order the executable wrote the bytes in |
| `Goto` | `to` | |
| `If` | `c, t, f` | |
| `Switch` | `e, cases, default` | `default` is a successor no execution takes; the interpreter traps on an unnamed value |
| `Return` | `vals` | the values of the procedure's `rets`, in order |
| `Trap` | `why` | |
| `Block` | `label, stmts, term, src, count, cover, closed` | `count` is the trace's execution count, `cover` the per-copy counts, `closed` the pass that added an unexecuted block |
| `Proc` | `name, params, rets, blocks, entry, kind` | `kind ∈ {sub, tick, init, nmi}`; `order()` is reverse postorder |
| `Rgn` | `id, name, base, size, kind, stride, init, fields, origin, post` | `init` is the *pre*-init image; `post` is `{address: bytes}`, the runs init wrote in the region (`regions.post_runs` over `trace.written_init`), and only an `init_constant` region carries any. `zero` = `origin or base`; `extent(lo, hi)` is the one containment test |

Widths are explicit: `MASK = (0, 0xFF, 0xFFFF)`, so 1 = byte and 2 = word, and
wraparound is a mask, never implicit. `Bin.op ∈ {+, -, &, |, ^, <<, >>, ==, !=,
<, <=, carry}`, evaluated by `ir.evalbin` byte-exactly as `vm._emit_line`
generates it, which is what makes the interpreter and the generated Python agree.

Access classes mirror the tracer exactly:

| `cls` | meaning |
| --- | --- |
| `ram` | plain memory |
| `chk` | memory when the byte was ever written or lies inside the load image, else a pinned input |
| `io` | `$D000–$DFFF`: a SID write, an `iow` schedule effect, or the RAM under the chip, as the 6510 port decides |
| `raw` | memory without marks: the CPU's own JSR/RTS frames, which the write log and the footprint do not see either. `stack.eliminate` removes them unless the program is residual |

`Tuneprog.image()` is the pre-init 64 KiB image rebuilt from every region's
`init`, and what `emit` initialises the executable from. `Tuneprog.reads()` is that
image with every region's `post` runs over it — what the tick actually reads, and
what the `## data` section prints. The machine-image regions are last in `storage`
and carry no `post`, so the overlay follows them.

Region kinds: `state` (written at play time, or by init in a `--songs all` union
build), `init_constant` (written by init only), `const` (read-only, inside the
load band), `image` (read-only, outside it), `io`, `copymap` (a per-copy column
table a fold laid down; read-only by construction, and a store into one traps on
both executors).

Trap kinds emitted by the front end: `untaken` (a branch direction the trace never
took), `unverified` (a variant switch arm a writer stored but no run executed),
`unreached`, `unstated` (the static walk's frontier, where the image is silent),
`switch`. Runtime traps raised by the executors: `envelope`, `switch`,
`input exhausted`, `input mismatch`, `nmi handler`, `bad op`.

### 4.2 Invariants

- **SSA over values only.** Memory stays in program order — there is no memory
  SSA. Only the 6510 register file, the flags and the lifter's uniques are
  renamed, and phis appear only for the register file (uniques are already
  single-assignment, named per block by `build`).
- **One statement, one effect.** A block is a flat `let` sequence; every P-Code
  operand is a leaf. A `Store` is the only statement that writes memory.
- **No machine registers cross a call.** A procedure takes its live-in registers
  as `params` and returns the ones it or a callee defines as `rets` (`wire.py`
  computes both by liveness over the call graph).
- **Stack elimination or an explicit residual.** `frames.py` proves which pushes
  and pops are values; `stack.py` performs the substitution. Where every load on
  the page is must-defined by pushes of its own frame, no `SP` survives and
  `meta["stack"] == "eliminated"`. A read the analysis cannot place keeps the
  machine stack for the whole program, and the certificate names the procedures
  that did it.
- **Every indexed access is bounded.** `Load`/`Store` carry the observed envelope;
  outside it the run traps rather than aliasing a promoted scalar.
- **The certified IR is never edited by S5/S6.** `structure.view` deep-copies it
  first; the certificate's `stage` moves `S4 → S6` and records
  `"presentation": "S5/S6 annotate the certified S4 IR; the program is unchanged"`.

### 4.3 The S4 JSON

`tuneprog.S4.json` is `ir.enc(prog)`: every IR node is a tagged list of its fields
in declaration order.

```jsonc
["$tuneprog",
  // meta: name, sid_model, entry, schedule, stack, songs, song,
  //       tick_proc, init_proc, copies, static_closure
  {"$dict": [["name", "Automatas.sid"], ["sid_model", null], ...]},
  // storage: id, name, base, size, kind, stride, init, fields, origin, post
  [["$rgn", 0, "", 4384, 3, "state", 1, ["$hex", "000000"], [], 0,
    {"$dict": []}], ...],
  [ ... ],                                                        // inputs
  {"$dict": [["tick",
    // proc: name, params, rets, blocks, entry, kind
    ["$proc", "tick", [], [0], {"$dict": [["B0",
      // block: label, stmts, term, src, count, cover, closed
      ["$block", "B0",
        [["$let", "t0", ["$load", "chk", ["$const", 4368, 2], 1, 4368, 4368, 7]],
         ["$store", "io", ["$const", 54272, 2], ["$var", "t0", 1],
          1, 54272, 54272, 3, 4370]],
        ["$goto", "B1"], 4365, 149025, [], ""]]]},
     "B0", "tick"]]]}
]
```

Tags, in `ir._NODES` order: `$const $var $load $bin $let $store $call $assert
$phi $goto $if $switch $return $trap $block $proc $rgn $tuneprog`; plus
`["$hex", …]` for `bytes` and `{"$dict": [[k, v], …]}` for every dict, so a
non-string key survives the round trip. `R16` and `W16` carry no tag: they exist
only in the S6 view, which is never serialised as IR.

### 4.4 The three executors

All three must agree; the interpreter is the semantics.

| executor | module | use |
| --- | --- | --- |
| reference interpreter over the IR | `interp.Interp` over `interp.Machine` | the definition; the `--prefix` cross-check (default 2,000 ticks) |
| generated Python, one function per proc | `emit.emit_python` / `emit.PyProgram` | the default S8 executor; blocks laid along the trace's hottest chain |
| the tracing VM itself | `tracevm.TraceVM` over `lifter.lift` | produces the reference log S8 compares against |

---

## 5. Verification and the certificate

### 5.1 What is compared

`verify.Verifier` runs `init(song)` and then tick after tick from the **pre-init**
image, feeding the recorded input stream. Per tick, `verify._compare` compares two
ordered lists, packed as `(address << 8) | value`:

1. the SID writes (`Machine.sid`), against the trace's `wlog` for that tick;
2. the schedule effects (`Machine.io`) — VIC/CIA stores — against `iolog`.

`init(song)`'s own write list is compared the same way before tick 0. A mismatch
is reported by `_diff` as `{tick, index, compared, expected, got, site}`, `site`
being the pc of the IR statement that made the write. `TrapError` — an envelope
violation, an unverified path, an exhausted or mismatched input — arrives as a
divergence too.

With a second entry two more properties are checked on every NMI of every tick
(`compared` gains `"nmi preemption schedule"` and `"nmi store separability"`): the
handler is entered exactly where the trace put it, at store granularity; and no
play-routine load in an open preemption window reads a cell the handler stamped in
that window.

### 5.2 Periodicity

Each tick's footprint hash is computed exactly as the tracer computes it — the same
address set, the same keyed `blake2b` — so the program's `(period, first_repeat)`
is directly comparable with the trace's `(trace_period, trace_first_repeat)`.
`complete` is true when both agree, the run passed the repeat, and there is no
divergence.

Two footprints are hashed, the whole play-written set and that set without the
stack page (`$0100–$01FF`), because only S4 can say which one a certificate may
claim on: an eliminated-stack program claims the page-free stream, a residual one
the page-inclusive stream (`verify.page_free`, `Trace.witness(free)`). Eliminating
a stack therefore moves no period and can only shorten a horizon.

A value that is itself an observable cannot be reduced away: *Commando*'s
per-voice pulse-width accumulators are the `$D410` writes, so both subtunes are
aperiodic at any practical horizon, and `period.py` classifies rather than proves.

`period.py` says *why* a subtune never repeats: the smallest period of each
footprint cell (KMP failure function), the loop the SID stream has, and the cells
whose period does not divide it. Its verdicts are `periodic`, `state only` (the
blockers never reach the SID) and `aperiodic` (the SID stream itself does not
repeat). The window must cover at least two loops.

### 5.3 The certificate

`emit.certificate` builds the document and `emit.write_certificate` writes it
sorted and indented. Fields, all verified against `docs/certificates/*.json`:

```jsonc
{
  "tune": "Automatas.sid",             // the file's basename; the key in tunes.HVSC
  "sid_model": null,                   // "6581"/"8580" when $D41B bit 0 was pinned
  "oracle": "deity_informant.PcodeVM@0.5.0",
  "reference_validated_against": "none",
  "compared": ["init writes", "tick sid writes", "tick schedule effects"],
                                       // + "nmi preemption schedule" and
                                       //   "nmi store separability" with a second entry
  "entry": {"kind": "sub", "addr": 4067, "cycles_per_tick": 2457,
            "source": "cia_timer"},    // "irq"/"nmi" also carry "kernal"
  "schedule": [ ... ],                 // only where a second entry exists; each nmi row
                                       // adds "replayed_registers" (6 per NMI)
  "stack": "eliminated",               // else {"depth": n|"unknown", "procs": [...]}
  "stage": "S6",                       // "S4" until S5/S6 annotate it
  "presentation": "S5/S6 annotate the certified S4 IR; the program is unchanged",
  "divergence": null,                  // else {tick, index, compared, expected, got, site}
  "cost": {"trace_calls": 149025, "sites": 651, "regions": 78,
           "ir_procs": 8, "ir_blocks": 227, "ir_statements": 737,
           "verify_cpu_seconds": 9.2, "calls_per_second": 16136},
  "subtunes": [{
      "song": 1, "ticks": 149025, "seconds": 371.64, "cycles_per_tick": 2457,
      "divergences": 0, "envelope_traps": 0,
      "period": 129024, "first_repeat": 149024,               // the tuneprog's own
      "trace_period": 129024, "trace_first_repeat": 149024,   // the trace's
      "complete": true, "closure": "trace",
      "inputs_pinned": 2228, "interp_prefix": 2000,
      "nmis": 199514, "nmi_entries": ["nmi"]   // only with a second entry
  }],
  "copies": { ... },                   // only where a family folded or refused
  "closure": { ... },                  // only under --closure static
  "generated": "2026-08-16T20:12:05Z"
}
```

- **`ticks`** is what was *verified*, not traced. A horizon is reached on a chunk
  boundary, so the trace can hold up to `--chunk` ticks more; those add nothing
  after a state repeat, and on a tick horizon the two counts are equal.
- **`copies`** carries `families` (`proc`, `bases`, `copies`, `rows`, `columns`,
  `table`), `refused` (`proc`, `base`, `why`), `statements`, `unverified` and
  `coverage` by copy pattern. Discovery reads the image, not the blocks a build
  happened to make, so the families are the same under either `--closure`.
- **`closure`** (opt-in) counts the bounded static walk: `arms`/`closed`
  directions, `stops` by reason, `blocks`/`statements` only a closed path reaches,
  `verified_statements`, `untaken` traps left and `frontier` paths ending in
  `trap 'unstated'`. Closed code is reachable only through edges that were traps,
  so the state hashes, `period`, `complete` and `divergences` are unchanged.

### 5.4 The oracles

| oracle | what it checks | where |
| --- | --- | --- |
| `sidplayfp` register grid | the tracer's own write log against a `sidtrace` CSV, framed by the interrupt period each write's cycle falls in | `grid.py`, `tests/test_oracle.py` (marker `oracle`) — 3,000 of 3,000 frames agree on *I Could Eat a Knob at Night* |
| trackerprog observable | `grid.reduce_tick`/`reduce_run`: ctrl/AD/SR kept write for write, the register pairs and levels reduced to one value a tick (prototype-trackerprog §2). One reduction, two directions — per tick off `Verifier(obs=True)`, vectorised off `grid.grid` — and not what `verify._compare` certifies, which stays the raw ordered list | `grid.py`, `verify.Verifier.obs` |
| Ghidra complexity | our statements/site and gotos/site against Ghidra's high P-Code, per procedure, tolerance 1.5×. `_flag` subtracts the two bodies' pc *sets*, so a Ghidra body holding more sites than ours is not scored as if it covered them; `ghidra_compare.alignment` states both shapes, `merged` (one Ghidra body over several of our procedures) and `clones` (our procedures sharing a site) | `ghidra_compare.compare`, `ghidra/6510/headless/ExportHighPcode.java` |
| Ghidra coverage | Ghidra's static reachability from our entries minus the trace's executed sites — i.e. the `trap` arms, classified | `coverage.json` |
| Ghidra semantics | Ghidra's P-Code emulator over the post-init image, replaying the per-call `reads` the facts export carries: the ordered sequence of SID register *changes*, both sides reduced by the same rule (`grid.changes`) from the same post-init bytes, with the first difference reported as call, index, register, wanted/got and pc. All 51 certificates agree | `EmulateTrace.java`, `ghidra_facts.emulate_facts` |
| interpreter vs generated Python | the two executors on a prefix of every certified run | `verify.prefix_check`, `--prefix` |
| `tools/tuneprog_recert.py` | every committed certificate reproduced from the run it records and diffed field for field, timestamp and the two timing fields excepted | 51 of 51 |

The chain is `sidplayfp ⇐ PcodeVM ⇐ tuneprog`.

Three things make that comparison the machine's own. Each call is entered on the
frame `machine.entry_frame` describes and stepped until `SP` is back where it
started — the tracer's own stop condition, which `RTS` and `RTI` reach alike, so an
RTI-framed tick compares. A store to `$D400`–`$D418` is a register change only
while the 6510 port maps I/O, the gate `tracevm` applies, so a raw diff of those
bytes is not the register file. And the rows compared are the tick entry's: a
second entry's are `verify.py`'s to replay, and `ghidra_facts` drops them by
`wlog`'s `nmi` column.

The two emulator disagreements the semantics oracle used to report were one bug,
and not ours: the stock 6502 spec's `subtraction_flags1` sets `C` to the borrow,
the 6510's complement ([ghidra#3189](https://github.com/NationalSecurityAgency/ghidra/issues/3189)),
which `SBC` and our `ISC`/`SBX` share. `smc.PATCHES` flips it where `build.py`
already patches `JSR`/`RTS` for the hardware stack convention. Our own lifter is
an independent table and was always right, which `tests/test_smc_sleigh.py` now
pins with a raw-P-Code evaluator against `lift.py`.

---

## 6. Presentation (S5–S7)

S5 and S6 run over a deep copy of the certified program. The order is
`pipeline.present`, read from the code:

| # | call | what it does |
| ---: | --- | --- |
| 1 | `live.needed` / `live.wants` | the values, arguments and return registers a reader must see |
| 2 | `structure.view` | the semantics-preserving copy everything below rewrites |
| 3 | `copyview.expand` | a per-copy column read as the operand it stands for |
| 4 | `texture.clean` (+ `frame.deltas`) | machine texture: register shuffling, frame plumbing |
| 5 | `structure.inline` | fold a `let` into its uses |
| 6 | `texture.tidy` | the residue of 4 |
| 7 | `copyview.naming_facts` | the per-cell facts the naming plane is derived from |
| 8 | `partition.repartition` | region typing by accessor shape: the narrow claim wins, the overrunner keeps the fused region as the bound its envelope asserts (re-runs 7 when it cuts) |
| 9 | `recover.recover` (over `structure.structure`) | stride views, roles, names |
| 10 | `views.decorate` | group views: struct fields that are a per-copy address table |
| 11 | `recover.name_u16` (over `word.fold16`) | 16-bit views: `halves.py`'s byte vocabulary applied to a block's statements |
| 12 | `cells.forward` | the three readings of one storage cell — mirrors, a slot stored once, what a read-modify-write leaves |
| 13 | `structure.inline` (`dup=False`) | re-inline after 11–12 |
| 14 | `fold.outline` | a run of blocks with one role, or shared by two procedures, becomes a procedure |
| 15 | `tails.promote_tails` | the routine a 6510 player enters by `JMP` becomes a procedure |
| 16 | `views.decorate` | re-decorate after outlining |
| 17 | `live.dead`, `live.coalesce` | dead values and the copies a join leaves |
| 18 | `structure.structure` | the final node tree: `Blk`, `Cond`, `Loop`, `For`, `Case`, `Jump`, `Exit` |
| 19 | `unroll.unroll` | consecutive isomorphic runs inside one body print once over an index |
| 20 | `views.decorate` + `recover.index_relation` | with the groups `unroll` made, over one `Facts` of the final program — the same facts the `index` block is serialised from |
| 21 | `word.fold_sid` + `word.sidorder` | the SID's own 16-bit registers as one write apiece, over the rows `unroll` aligned |
| 22 | `copyview.mark` | the coverage marks on merged statements |

Regions need per-phase views: a one-loop `init` clear merges every field into one
region, so the group view is built from the *play*-phase accessors (`views.py`),
and `partition.repartition` re-types the S6 copy over the same rule. S4's region
ids and `regions.json` are untouched by either.

Two rewrites that are proofs rather than heuristics: `ranges.py` is the one interval
domain over what the certified IR proves about a byte, and `gated.py` is the pass
that applies it (masks, comparisons, the borrow a two-armed branch hides).
`irwalk.accessors` is the one accessor enumeration; `Rgn.extent` the one containment
test; `facts.per_region` the one reading of the indices that walk a region;
`facts.unclaimed` the one "already named" predicate; `views.record_split` the one
record view (a record stride, or its transpose); `facts.cursor_cells` the one
cursor rule.

That rule reads: a cell is a **`cursor`** when it is loaded as part of an address
and some region that address reaches has more than `facts.MAXROLE` elements. One
rule for all three kinds of cell — a scalar region, a per-copy slot
(`views.cell_field`), a field of a record split (`views._named_fields`) — and the
mirror of the scalar guard: a scalar must be a *variable* and not a block to earn
any role, a field already is one, so what it must show instead is a *block* on the
other side. It runs ahead of the role a cell's own updates give it, because a
score cursor is also stepped by one. Evidence, four certified families: GoatTracker 2
`rec[x/7]` `+0`/`+3` (the orderlist position into `T1875`, the row cursor into
`T18B7`, both through the `$15A9` pointer table), JCH V20 `voice_3[v]` `+9`
(the row cursor into `T19FE`, through the zero-page pair), SID Wizard `rec` `+0`
(the pattern number into `T2478`), `+2` (the orderlist position) and `+3` (the row
cursor into `T1C6A`) — every one of which printed as `fNN`, `timer` or `acc`
before — and Commando, whose score cursors are scalars the same rule already named.

The relation itself is serialised: `tuneprog.S6.json`'s `index` block
(`recover.index_relation`) carries one plain record per index cell and target
region — `region`/`addr` the cell (`addr` null when the whole region is the
index), `target` what it indexes, `base` how the address reached it (`const`,
`ptr`, `other`), and for `ptr` the pair's name and the regions the pair's low byte
is loaded from, so a table reached through a pointer table is two records that
join on `target`.

### 6.0 Per-register provenance (T0)

`provenance.document(view, structured, names)` emits `tuneprog.T0.json` beside
S6: `{plane, voice_map, image, writes}`, one `writes` record per SID write site
of the printed program, in print order. The roots are the `io` stores whose
envelope lies in `$D400..$D418` and the stores into a `sid_image` region
(`facts.image_copy`), rekeyed by the flush delta — a player that assembles its
registers in RAM and flushes them writes its provenance there, so the two sets
are one plane and each rekeyed record carries the flush site's pc.

The register comes from the site's own base and its **envelope**, not from the
printed index: an indexed write to a voice register reaches whole voice blocks
from its base, so `provenance.regvoices` reads the register off the base and the
voices off the span — the same fact whether the index is a loop variable, a
voice-map entry (`sid[v]`) or a cell no name reaches (SID Wizard's
`sid.reg[saved10] = freq_lo - saved9`, `$D400..$D40E`: `freq_lo`, voices 0–2,
where `cellref.Cells.voiced` gives nothing). A span no voice stride makes reached
more than one register, and is a `kind: file` record only when one value covers
every register in it — the flush copy the image naming already proved, or a
file-wide constant (`sid.reg[v] = 0`); otherwise it is a refusal.

`expr` is the store's value with its names substituted (`provenance.expand`),
stopping at every cell S6 names — a role, a struct view, a record split, a slot —
so the slice bottoms out in named cells and not in address arithmetic; it is
serialised with `ir.enc` (`R16`/`W16` are tagged nodes like the rest). `cells` is
one entry per distinct leaf, named exactly as the print names it: the record
re-enters the printer state its site printed in, so `print` is the very line of
`tuneprog.md`, which is the acceptance the exemplars check. `self_update` marks a
site that reads its own cell back — a recurrence, which is T1's to classify — and
`refusal` is one of `index not a voice`, `smc target` (an address with no
constant base at all: a patched operand `lift` residualised into a load) or
`unresolved base`.

Evidence over the 51 certified programs: 849 write sites, every one of them a
named register or a stated refusal, and every one's `print` a line of its own
`tuneprog.md`. Two families per rule, as §11 requires — the rekeyed image is
GoatTracker 2's ghost and JCH V20's `knob-at-night` (and Daglish's), the
constant `file` write is JCH's and SID Wizard's register clear, `index not a
voice` is Follin's raw cross-voice register list and JCH's non-constant clear
(36 sites), `smc target` is Baumrucker's and Follin's patched store operands
(4 sites).

### 6.0.0 The trackerprog layer (T2, T3)

`deity_informant/trackerprog/` lifts the certified artefacts one layer up
([prototype-trackerprog.md](prototype-trackerprog.md)): `tools/tuneprog_score.py`
writes `tuneprog.T2.json` (cursors, streams, selectors, the pitch table and the
score materialised over the horizon) and `tools/tuneprog_trackerprog.py` lifts
the trackerprog from the program's data — the fetch regions cut out of the
certified tick and recorded as fetches, the instrument table, T2's streams,
T1's accumulators, T0's write sites as producers — replays it with the score
tables never read and writes `trackerprog.certificate.json`, plus
`trackerprog.json`/`trackerprog.md` when it certifies. Neither is a pipeline
stage.

### 6.0.1 The accumulator plane (T1)

`accum.document(view, names, t0_doc, history, certificate)` writes
`tuneprog.T1.json` — `{plane, horizon, accs, refusals}` — beside T0, from a
library and `tools/tuneprog_accum.py`, never from the pipeline: no artefact of
the tuneprog moves. Candidates are the cells a store updates from themselves
whose region a T0 record reads; a cell whose every step is ±1 is a counter (the
divider `rate` names) and a cell whose own recurrence halves a table difference
is a `tablestep` delta, so neither is an accumulator of its own.

The guards a record carries are the *control dependences* of the store,
transitively closed — not its dominators, which a join carries either way — with
the callers' arguments substituted where a value's free names are its
procedure's parameters (`accshape.arms`): GoatTracker 2's vibrato phase is
stored in `p_109E` from a value four arms of `p_1082` supply. Two verifiers run
over `history.py`: the interval the record claims, and the replay — every move
the value made must be one the plane's own clauses make. What neither accepts is
an `unclassified update` refusal naming the cell, the site and the clause.

The epochs are stated, not guessed. A cell is read before it is written, so
inside its own update an accumulator reads last tick's value; a countdown's own
borrow is last tick's value minus one where the tick ran it; and a guard beside a
store reads what the blocks after that branch had not yet changed. Where the
value a producer sets is a table read no name indexes, T1 records *when* the
producer ran and leaves the value to §4 — an absolute `set` is not a recurrence.

### 6.1 The print

`printer.render(view, structured, names, cert, pcs=True)` emits `tuneprog.md`:

| block | contents |
| --- | --- |
| `## meta` | entry and cadence, subtunes and model, program size, `untaken` count, the SID write order, the phase, the copy families and refusals, the certificate line |
| `## state` | struct views first (`voice[3]` with its fields), then named 16-bit pairs, then the scalar rows: name, base, size and stride, role, note |
| `## data` | every run of storage the program reads that no store's envelope reaches, printed from `Tuneprog.reads()`: a note table as 16-bit entries, equal-stride columns as one row per record, everything else as 32-byte hex rows; then one line per distinct printed accessor. A block whose bytes init wrote says `init-written`, the image file not holding them |
| `## inputs` | the pinned input classes and their addresses |
| `## program` | one fenced block per procedure, tick first, then what it calls, then `init` |

Two conventions the block states rather than hides. The SID's `freq`, `pw` and
`cutoff` are one register the chip's 8-bit bus takes two writes to set, so
`sid[v].freq = f` is a *print* convention: `meta` states the order once
(`sid  16-bit registers written lo then hi`) and a write in the other order carries
`# hi then lo` on its own line. `verify._compare` is over the executable's ordered
byte writes and is untouched. A branch whose one direction is a bare untaken trap
prints as a `# untaken: <condition>` **mark** on the first line the covered
direction reaches, counted in `meta`; a switch arm still prints its trap.

Two rules decide what the data section may carry. `datablock.carried` is the cells
a region carries post-init bytes for — a store of an `init_constant` region is
init's own, so only the rest of its envelope types state, and a fused extent's
other cells, which init never wrote, stay out. `datablock.paired` adds the cells of
every folded 16-bit access: `irwalk.accessors` yields `Load`/`Store`, but a pair
`word.fold16` joined is an `R16`/`W16` the walk never saw, and without it bytes the
program writes at play time would print as data.

The procedures render *before* the header, because the data section states each
table's accessors in the form the program printed them in.

### 6.2 The measurement harness

Every presentation change is measured over the 51 certificates with
`printer.render(..., pcs=False)` — the same print without the per-statement pc
comments — and the six numbers are stated before and after:

| metric | definition |
| --- | --- |
| tokens | the regex `\$?\w+` alternated with `\S`, over the `## program` section |
| lines | non-blank lines of the `## program` section |
| statements | `sum(len(b.stmts))` over the view |
| blocks | `sum(len(p.blocks))` over the view |
| header rows | the `meta` + `state` + `data` + `inputs` rows |
| data rows | the rows `datablock.section` emitted, and the bytes they carry — 19,343 rows carrying 246,267 bytes over the 51, of which 57,075 in 216 blocks are init-written |

A presentation change leaves `tuneprog.py` byte-identical or explains the change,
and `tools/tuneprog_recert.py` is green before and after.

---

## 7. CLI and tools

### 7.1 `deity-informant tuneprog`

```bash
deity-informant tuneprog TUNE.sid --out DIR \
    [--song N | --songs all] [--calls N | --seconds S | --until-period] \
    [--max-calls N] [--sid-model 6581|8580] [--no-merge] \
    [--closure trace|static] [--resume] [--budget S] [--chunk N] \
    [--prefix N] [--no-verify] [--no-text] [--ghidra-facts]
```

| flag | default | effect |
| --- | --- | --- |
| `--out` | required | the output directory; every artefact of §2 lands here |
| `--song N` | the header's `startsong` | 1-based subtune |
| `--songs all` | — | one tuneprog over every subtune's trace (`tracedata.merge`); refuses a tune whose subtunes disagree on cadence |
| `--calls N` / `--seconds S` | — | the horizon in ticks, or in seconds of music at this tune's cadence |
| `--until-period` | off | trace to the first state repeat of the footprint S4's verdict allows |
| `--max-calls` | 400,000 | the cap on any of the above |
| `--sid-model` | as traced | pin `$D41B` bit 0 |
| `--no-merge` | off (copies fold) | build the unfolded S2b form |
| `--closure` | `trace` | `static` also decompiles the untaken directions the post-init image states, as zero-coverage code |
| `--resume` | off | continue a chunked run from `state.json` |
| `--budget` | 45.0 | CPU seconds per invocation; exit 2 while work remains |
| `--chunk` | 4,000 | ticks per progress step |
| `--prefix` | 2,000 | ticks to re-run on the interpreter as the executor cross-check |
| `--no-verify` / `--no-text` | off | skip S8 / skip S5–S7 |
| `--ghidra-facts` | off | also write `OUT/ghidra` for a headless Ghidra run |

`--closure static` removes nearly every `trap 'untaken'` and costs the *covered*
program its structuring, which is why the default is `trace` and every committed
certificate is trace-closed.

The other subcommands: `disasm` (illegal-aware disassembly), `pcode` (raw P-Code
for one instruction), `run` (execute in `PcodeVM`, dump the `$D400..` grid),
`emit-sleigh` (build and install the 6510 SLEIGH module).

### 7.2 Tools

All long-running tools obey the same convention: `--budget` per invocation,
`--resume` to continue, and **exit 2 while work remains**, so no single process
exceeds the 60 s CPU rule. The budget is CPU seconds for the single-tune drivers
(`tuneprog_certify`, `tuneprog_recert`, `tuneprog_period`, default 45) and wall
seconds for the parallel population drivers (`tuneprog_nmi`,
`survey/tuneprog_sweep`, default 1,800), where the CPU is the workers'.

```bash
until python3 tools/tuneprog_certify.py TUNE.sid --out out/tune --until-period --resume; do :; done
until python3 tools/tuneprog_recert.py --out out/recert --resume; do :; done
```

| tool | lines | purpose |
| --- | ---: | --- |
| `tools/tuneprog_certify.py` | 36 | the whole pipeline standalone; a thin wrapper around `pipeline.main` |
| `tools/tuneprog_recert.py` | 327 | reproduce every certificate under `docs/certificates/` from the run it records and diff it field for field (`--only`, `--update`, `--certs`, `--hvsc`). `--shard I/N` takes every Nth certificate; `--ghidra-dir DIR` exports the headless facts as it replays and then runs the three Ghidra oracles against the export in `DIR/<certificate>`, exiting 1 on any `ours_bigger` no `--known CERT:ENTRY` names |
| `tools/tuneprog_period.py` | 71 | why a subtune has no state repeat: each cell's smallest period, the SID stream's loop, the cells whose period does not divide it (`period.classify`) |
| `tools/tuneprog_floor.py` | 288 | the complexity floor of one output directory against its tune: the load band split into executed code / reached data / neither, `xz -9e` of each, printed statements by code range and kind, and every `(b, b+1)` pair the print reads at one index |
| `tools/tuneprog_ghidra.py` | 210 | write `ghidra_facts.json` + `image_post_init.bin` from an output directory (or the hello-world demo), and join a finished headless run back with `--compare GOUT` (`comparison.json`, `comparison.md`) |
| `tools/tuneprog_nmi.py` | 285 | classify the CIA #2 NMI schedules of a tune population (`scan` / `report`) |
| `tools/survey/headers.py` | 65 | static header census over every HVSC `.sid`, joined with the SIDId family |
| `tools/survey/tracer.py` | 420 | the prototype dynamic per-site tracer on `PcodeVM`, the survey instrument |
| `tools/survey/run.py` | 126 | the stratified parallel driver behind it (up to `--cap` tunes per family, seeded) |
| `tools/survey/report.py` | 411 | its markdown tables, raw and HVSC-weighted |
| `tools/survey/tuneprog_sweep.py` | 311 | the whole pipeline over the same sample, resumable and parallel |
| `tools/survey/tuneprog_report.py` | 425 | the tables of [survey-tuneprog.md](survey-tuneprog.md) |

Every tune the certificates, tools and tests name lives once in
`deity_informant/tuneprog/tunes.py`: a certificate's `tune` field is a basename,
that map's key, and `tunes.resolve` finds the file under `$HVSC` or in the
`$DEITY_ORACLE_CACHE/hvsc` fetch cache. Tunes are copyright works and are never
committed; a hermetic test refuses an HVSC path written anywhere else.

### 7.3 Python API

```python
from deity_informant.tuneprog import pipeline, printer, verify
from deity_informant.tuneprog.tracedata import Trace

trace = Trace.load("out/tune")                            # S0/S1 already run
prog, regions, procs = pipeline.build(trace, "TUNE.sid")  # S2..S4: the certified program
cert = verify.certify(prog, verify.verify(prog, trace))   # S8
view, structured, names = pipeline.present(prog)          # S5/S6 over a copy
text = printer.render(view, structured, names, cert)      # S7
```

---

## 8. Machine model and known boundaries

### 8.1 The chips

| chip | model | where |
| --- | --- | --- |
| 6510 CPU | every documented opcode including all 105 illegals, exact cycle counts, `JAM` halts, an unimplemented opcode is a hard error | `lifter.py` (1,007 lines), `vm.py` (318) |
| 6510 port | `$00` direction and `$01` data decide what `$D000–$DFFF` and `$E000–$FFFF` are at every access | `machine.port_bits`, `port_bank`, `kernal_mapped`, `tracevm` |
| SID | not modelled as a device. Writes are the observable; `$D41B`/`$D41C` and reads of write-only registers are pinned inputs | `ir.SID_LO..SID_HI`, `tracevm` |
| VIC | raster from the cycle counter (`$D011`/`$D012`), `$D019` acknowledge; other registers are `io` stores | `vm._rd`, `tracevm` |
| CIA 6526 ×2 | two timers (B on cycles or A's underflows), one-shot mode, the accumulated ICR mask, latched flags, the interrupt line. CIA #1's line is the IRQ, CIA #2's the NMI | `cia.py` (248), `nmi.py` (204) |
| KERNAL | not executed. `c64.irq_stubs` supplies the `$FF48` prologue, the `$EA31`/`$EA81` body and epilogue, and `$FEBC`; `nmi.KERNAL_STUB` models `$FE43`'s own cost | `c64.py` (173) |

The **entry frame** is a contract, not storage. An `irq` tick is entered with the
frame the machine pushed; every byte is a parameter, the terminating `RTI` consumes
exactly those bytes, and the interrupt disable is the tick's first statement
(`build._irq_entry`). Which bytes they are is the entry's `kernal` field: a raw
`$FFFE` vector is entered by the 6510 alone, so `SP+1` is the packed entry flags; a
CINV `$0314` entry comes through `$FF48`, which saves A, X and Y on top, so
`SP+1..4` are entry Y, X, A, status. Which applies is the port's word
(`machine.vector_gate`), re-run once init has had the port; a tick entered with the
port on the other side of HIRAM refuses (`port moved`).

The tracer hashes **two** footprints per tick (§5.2) and records which `(pc, op)`
pairs reached a chip, so an address only ever touched with I/O banked out is the
RAM under the chip — ordinary storage, and a region under the SID register file
takes the `sid_image` role at delta 0.

### 8.2 Refused by design

Refusals are diagnosed, never approximated: `machine.Refusal` carries a reason and
a detail, and the pipeline produces nothing. Every reason the tree raises:

| reason | raised by | when |
| --- | --- | --- |
| `no entry` | `machine.find_entries`, `trace` | `play = 0` and no interrupt vector installed, or the installed vector is null |
| `vector banked out` | `machine.vector_gate` | a vector is installed, but the 6510 port dispatches through the other one |
| `nmi vector banked out` | `nmi.entry`, `trace` | the NMI vector the port selects carries no handler |
| `second interrupt source armed` | `nmi.check` | CIA #2's accumulated ICR enables a source this model carries no schedule for (TOD alarm, serial, FLAG, CNT timer) — 6 of 7,023; re-checked every tick |
| `port moved` | `trace` | the entry contract does not hold at every tick: a tick entered with the port on the other side of HIRAM |
| `init runaway` | `machine.init_runner` | `init` neither returns nor idles within `INIT_BUDGET` = 2,000,000 instructions |
| `play runaway` | `trace` | a tick past its instruction budget |
| `recursion` | `cfg._no_recursion` | a cycle in the JSR call graph |
| `subtunes disagree on cadence` | `machine.shared_entry` | `--songs all` where one merged trace cannot be one schedule |
| `copy index` | `wire.wire` | a value live at a procedure entry that is not a 6510 register |
| `nmi clobbers registers` | `nmi` | the handler's `RTI` does not return the A/X/Y it interrupted — 8 of 7,023 |
| `schedule not store-separable` | `nmi.Separable` | a play load in an open preemption window reads a cell the handler stamped in that window — 5 of 7,023 |
| `input replay mismatch` | `tracevm` | a replay run consumed a pinned input the recording did not |

The last two are the second entry's own checked properties, both fail-closed and
checked on every NMI of every tick. *Armed* is the chip's rule and not the
evidence: a CIA #2 source exists iff `$DD0D` enables one of bits 0–4 (accumulated
over the writes, bit 7 saying whether a write enables or disables what it names)
and, for a timer, iff that timer is started; a `$DD04`/`$DD05` latch or a
`$0318`/`$FFFA` vector over no such source is dead, and by the same rule a CIA #2
period is never a play cadence.

Deliberately not modelled: unexecuted code (a `trap`), cycle-level timing (an
optional annotation), the SID/VIC/CIA as devices, and musical semantics beyond
exact storage typing.

### 8.3 Boundaries

Measured limits that are *not* work items: the shapes a rule refuses, and why.
Two are the largest and are worth knowing before reading a certificate.

- **A second entry's instant is early, not its effect.** A CIA #2 NMI lands tens of
  cycles early from unmodelled VIC DMA; play-routine writes are unaffected, but
  `$D418` in a sample mixer lands on the neighbouring sample nibble in 10–54 % of
  frames. The certificate claims the write list *under the traced interleaving*.
- **Trace closure.** A branch direction or table entry no run took is a trap, not a
  decompiled path, unless `--closure static` is on.

The rest, each with its measurement over the 51 certificates and the modules it
belongs to:

| boundary | measurement |
|---|---|
| two halves separated by a control join (the low half in each arm, the high half's store in the join) | the shape exists once over the 51 -- `jch-easy-does-it`, one `Cond` whose arms end in one cell's store and whose join opens with the paired cell's. One site on one family is under the bar a view rule has to clear (`word`, `structure`) |
| a nested-borrow compare as `u16 < u16` | `halves.compare` reads the two nested borrow-outs of `CMP lo; LDA hi; SBC hi; BCS` in either spelling, and *no* site over the 51 meets the row's own condition, that a branch alone reads the chain: at every one of them the byte differences and the intermediate carry are read on. Folding the condition anyway prints the compare the branch really makes (`ghost[x/7].freq >= FREQ[i] + (b10AC < 2)` for `(FREQ_HI[i] + t4) <= ghost[x/7].freq_hi`) and is worse, the byte chain staying live: +36 tokens and +4 header rows over 4 tunes of 2 families (both GT2, both SID Wizard), 0 lines. Refused, the code with it (`halves`, `word`) |
| folding a copy nothing ran costs more than it saves: a silent copy adds the columns and the `switch (v)` with no second body to remove | Follin 17-19, 25, 27, 29 grow ~6 % statements, ~20 % blocks, 24–49 printed lines each; buys per-voice names and the coverage vector; `--no-merge` is the escape (`copymerge`) |
| an edge into another copy at a row that is not that copy's entry (*Automatas* `$16AB`) | lowering as `v += 1; goto` the template row is sound and folds it, but the merged body gets two entries, costing a `goto` and the `ad`/`ctrl` names — refused as the narrower rule says (`copyrows`) |
| a copy's stream runs past a jump into unreached code (the Knob's `$1167`), carried as an unverified row | rejected on the image: it shatters Ghouls' 3×237 families to 3×2 and breaks closure invariance on song 31; ending the stream there needs `jumptab`'s transfer facts, not the image alone (`siblings`, `jumptab`) |
| the phase of a repeating cascade is a convention where two readings tie exactly (*Automatas* `p_168C` `$172C` vs `$1734`) | pinned by `slack` then lowest base; refusing ties also refuses the two real 5-copy cascades (`siblings`) |
| a fold takes cells the naming plane had (the Knob's `$17B9`/`$17BC` → `b17B9[v + 3]`, Commando 2's `$54F8`) | measured in P1: the merged access's region does not keep the field names of the cells it unites (`copymerge`, `views`) |
| a hex row costs about 1.4x, compressed, the bytes it prints | Commando's data section carries 1,867 B (1,100 B `xz`) and grows the print's `xz -9e` by 1,564; `gt2-je-suis-linus` 5,174 B / 1,896 → +3,136, `sw-emomyst` 1,858 / 1,080 → +1,428. Three ASCII characters a byte recover to ~1.4× the binary's compressed size; the accessor lines and the block headers are the rest. A denser encoding would not be a listing (`datablock`) |
| a record block prints one row per record, whatever the stride | `jch-knob-at-night`'s stride-4 block is 8,576 records, 8,675 of the section's 19,343 rows over the 51. Packing records to a fixed row width was rejected: the column header then labels one group of several on the row (`datablock`) |
| an `R16`/`W16` keeps its two cell *bases*, not the envelope the pair of stores had | the node is the 16-bit view, and `word.fold16` builds it from two cells one index reaches; `datablock.paired` therefore fails closed over the store's own region and claims only the named cell for a read. Restoring the envelope means putting it on the node, which every S6 pass would then have to maintain (`ir`, `word`, `datablock`) |
| the stack page is a second inexact direction of the store-granularity replay: an IR store wholly inside `$0100`–`$01FF` between the last counted store and the NMI instant replays the handler before it | needs the instruction index the trace records (the emitted program cannot count) or a shared stack store count (stack elimination removes); the no-hook converse was measured and rejected (JCH diverges at tick 6); narrow, undiagnosed in population (`nmi`, `interp`, `verify`) |
| a write to `$D000`–`$DFFF` with I/O mapped also writes the RAM under it (`tracevm.write`, `interp.iostore`) where the hardware writes only the chip | the honest model is two planes, chip and RAM beneath; unobservable in every exemplar, 3 tunes of 7,023 discriminate |
| the tracer counts CPU cycles where the sampler's clock also spends VIC DMA | 57–60 cycles per frame, +533 inside one Knob tick; free today, both sides framed by the interrupt, so a raster model is needed only if a comparison ever needs sub-frame time (`tracevm`, `machine`) |

---

## 9. Exemplars and evidence

### 9.1 Where we are

| | |
| --- | --- |
| certified | 54 certificates, 807,742 ticks, 0 divergences, 0 envelope traps; 43 complete via periodicity, `--songs all` complete on 31 of 32 subtunes and on 11 of 14; no tune-specific code in the front end for any anatomy mechanism |
| families | defMON (*Automatas*, both SID models), Hubbard (*Commando* 1–2), Follin (*Ghouls'n'Ghosts*, 32 subtunes + the union), GoatTracker 2 ×2, SID Wizard ×2, JCH V20 ×3 (including the two-entry *Easy Does It*), installed-handler ×2 (*Jodler*, *Playful Professor*), dead-NMI ×2 (*Alien 3*, *Jazzpjazz*), patched-dispatch ×2 (*Experiment Zeta*, *Deflektor*), Blackbird (*Quintessence*), Walker (*Chameleon*, 2× speed), Galway (*Comic Bakery*, all 14 subtunes) |
| refused by design | a CIA #2 source with no schedule (TOD alarm, serial, FLAG, CNT timer): 6 of 7,023 |
| survey | 7,023-tune stratified sample at 30 s: **91.2 % of HVSC by weight certifies** (76.7 % raw), 2.5 % diverges, 6.2 % refused with a diagnosis, 0.26 % crashes; `--until-period` over 1,338: 99.4 % of certified programs complete by weight ([survey-tuneprog.md](survey-tuneprog.md)) |
| code | `deity_informant/tuneprog/`, 69 modules, 20,663 lines, the largest 511; 917 hermetic + 203 HVSC + 10 oracle tests, 94 % coverage; SSA 1.0–1.6 statements per instruction |
| baseline | the Ghidra high-P-Code export with SMC context ([ghidra-highpcode-export.md](ghidra-highpcode-export.md)), 8.3–16.5× our S4 — a baseline, not core. The three Ghidra oracles run nightly over all 51 certificates: 51 exports, the emulator agreeing with every one, no `ERROR` row, and the 2 standing `ours_bigger` flags carried as `--known` ([tuneprog-backlog.md](tuneprog-backlog.md) §2.6), so the gate is clean. Beside it the `sidplayfp` grid oracle |
| merged PRs | #225–#286, one stage each, every one on green CI with recert reproduced |

Open work, by lever, and the done ledger: [tuneprog-backlog.md](tuneprog-backlog.md).

### 9.2 The certified set

54 certificates, 807,742 verified ticks, **0 divergences and 0 envelope traps**,
43 complete via periodicity. Numbers below are read from `docs/certificates/`.

| certificate | tune | player | ticks | period | procs | blocks | stmts | regions | certified |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `automatas` | Automatas.sid | defMON | 149,025 | 129,024 | 8 | 227 | 737 | 78 | complete |
| `automatas-6581` / `-8580` | Automatas.sid | defMON, `$D41B` pinned | 149,025 | 129,024 | 8 | 227 / 226 | 737 / 726 | 78 | complete |
| `commando-song1` / `-song2` | Commando.sid | Hubbard | 11,780 | — | 3 | 115 / 100 | 341 / 288 | 58 / 59 | horizon |
| `ghouls-song01`…`32` | Ghouls_n_Ghosts.sid | Follin | 6…20,049 | 1…8,064 | 2–4 | 108–271 | 199–671 | 37–59 | complete (31 of 32) |
| `ghouls-songs-all` | Ghouls_n_Ghosts.sid | Follin, all 32 subtunes | 111,763 | per subtune | 4 | 299 | 770 | 45 | complete (31 of 32) |
| `gt2-je-suis-linus` | Je_suis_Linus_le_salaud.sid | GoatTracker 2 | 8,236 | 6,720 | 14 | 245 | 526 | 73 | complete |
| `gt2-do-it-again` | Do_It_Again.sid | GoatTracker 2 | 8,659 | 8,640 | 14 | 236 | 516 | 73 | complete |
| `jch-knob-at-night` | I_Could_Eat_a_Knob_at_Night.sid | JCH V20 + a banking wrapper | 8,577 | 1 | 9 | 160 | 477 | 98 | complete |
| `jch-guldkorn-intro` | Guldkornekspressen_Intro.sid | JCH V20 | 2,401 | 1,512 | 2 | 160 | 443 | 103 | complete |
| `jch-easy-does-it` | Easy_Does_It.sid | JCH V20 + a CIA #2 NMI sample mixer | 1,799 | — | 5 | 211 | 669 | 107 | horizon |
| `lft-quintessence` | Quintessence.sid | Blackbird, an LZ score in three ring buffers | 10,426 | — | 3 | 176 | 429 | 54 | horizon |
| `walker-chameleon` | Chameleon.sid | Walker, a typed keyboard at 2× speed | 8,052 | 72 | 52 | 320 | 1,169 | 101 | complete |
| `galway-comic-bakery` | Comic_Bakery.sid | Galway, 14 subtunes: 3 sequenced, 3 jingles, 8 effects | 29,911 | 1 | 20 | 524 | 1,485 | 102 | complete (11 of 14) |
| `sw-emomyst` | Emomyst.sid | SID Wizard 1.6 | 8,084 | 6,120 | 15 | 368 | 955 | 96 | complete |
| `sw-end-of-the-world` | End_of_the_World.sid | SID Wizard 1.9 | 14,465 | 7,688 | 16 | 364 | 939 | 94 | complete |
| `becher-jodler` | Jodler.sid | installed CINV handler | 707 | 700 | 2 | 11 | 54 | 36 | complete |
| `baumrucker-professor` | Playful_Professor-Math_Tutor.sid | installed CINV handler | 1,503 | — | 6 | 58 | 179 | 40 | horizon |
| `rodger-alien3` | Alien_3.sid | hardware-vector entry, dead NMI | 1,503 | — | 7 | 148 | 505 | 25 | horizon |
| `goto80-jazzpjazz` | Jazzpjazz.sid | defMON, dead NMI | 1,799 | — | 4 | 190 | 629 | 95 | horizon |
| `necropolo-experiment-zeta` | Experiment_Zeta.sid | Virtuoso, patched `JMP (ind)` | 5,956 | 5,184 | 2 | 185 | 356 | 65 | complete |
| `daglish-deflektor` | Deflektor.sid | Daglish, patched branch offset | 1,503 | — | 4 | 180 | 630 | 78 | horizon |

Two certificates are `stack: residual`: `jch-easy-does-it` (depth 8, held by `nmi`)
and `rodger-alien3` (depth 11, held by `tick`, whose hardware-vector entry makes
the pushed A/X/Y live-in). Every other one is `stack: eliminated`.

| family | record |
| --- | --- |
| defMON (*Automatas*) — the vertical slice realising S0–S8 | [prototype-automatas.md](prototype-automatas.md) |
| Follin, *Ghouls'n'Ghosts*, 32 subtunes + the union | [prototype-follin.md](prototype-follin.md) |
| GoatTracker 2 | [prototype-goattracker.md](prototype-goattracker.md) |
| SID Wizard 1.6 / 1.9 | [prototype-sidwizard.md](prototype-sidwizard.md) |
| JCH NewPlayer V20 | [prototype-jch.md](prototype-jch.md) |
| the installed-handler family (PSID `play == 0`, CINV entries) | [prototype-kernal-entry.md](prototype-kernal-entry.md) |
| the second interrupt (a CIA #2 NMI as the schedule's second entry) | [prototype-nmi.md](prototype-nmi.md) |
| the complexity floor of one simple tune (Hubbard's *Commando*) | [prototype-commando-floor.md](prototype-commando-floor.md) |
| Blackbird (*Quintessence*) — the read whose class is not its address | [prototype-blackbird-trackerprog.md](prototype-blackbird-trackerprog.md) §4.1 |
| Walker (*Chameleon*) — four modulators over two shared offsets | [prototype-walker-trackerprog.md](prototype-walker-trackerprog.md) |

[survey-tuneprog.md](survey-tuneprog.md) is the campaign record behind §9.1's
survey row: the whole pipeline over the same stratified sample, by family, with
the failure classes, refusal reasons, stack/entry/fold distributions and cost.

### 9.3 The population the pipeline serves

Two instruments in `tools/survey/`: `headers.py`, a static census over all 61,157
`.sid` files of HVSC #85 joined with the SIDId family from `hvsc-tracker-catalog`;
and `tracer.py`/`run.py`, a prototype of the S1 tracer over the stratified sample
(up to 30 tunes per family, seed 1 — 7,023 tunes, 646 families, 60 s of music each,
the default subtune). Runs of 2026-08-16. Rates are raw over the sample and
re-weighted to HVSC by family size. `tools/survey/tuneprog_sweep.py` imports
`run.py`'s sampler, so the pipeline campaign of
[survey-tuneprog.md](survey-tuneprog.md) covers the same files.

| static census, 61,157 files | value |
| --- | --- |
| PSID / RSID | 57,233 / 3,924 (93.6 % / 6.4 %) |
| `play = 0` (the tune installs its own interrupt) | 4,035 (6.6 %; PSID 0.2 %) |
| header speed bits claim CIA for some subtune | 7,466 (12.2 %) |
| more than one subtune | 4,796 (7.8 %); mean 1.44, max 256 |
| clock PAL / NTSC / both / unknown | 89.3 / 5.1 / 0.1 / 5.5 % |
| SID model 6581 / 8580 / both / unknown | 40.2 / 41.8 / 1.2 / 16.9 % |
| 2SID / 3SID | 364 / 27 |
| load band median / p90 / p99 / max | 3.6 / 11.2 / 42.6 / 63.5 KiB |
| song length (87,868 subtunes) median / p90 / max | 93 s / 233 s / 2,026 s |

Of the sample, 6,363 of 7,023 traced (90.6 % raw, **97.0 % weighted**); the rest is
`init` never returning (7.0 %, RSID main loops, digi, BASIC), no play entry found
(2.2 %), and a handful of play errors and timeouts.

| topology, over the 6,363 traced | raw | weighted |
| --- | ---: | ---: |
| video-frame cadence (PAL or NTSC) | 88.4 % | 89.1 % |
| CIA-timer cadence | 11.6 % | 10.9 % |
| entry = the header's `play` (a JSR each tick) | 91.3 % | 96.3 % |
| entry = an installed IRQ handler ($0314 / $FFFE) | 8.7 % | 3.7 % |
| CIA #2 timer armed at init (a second interrupt) | 4.0 % | 2.0 % |
| writes `$01` (banking) in play | 7.2 % | 7.3 % |

| construct the front end must model | raw | weighted |
| --- | ---: | ---: |
| play-time SMC — some play site writes executed instruction bytes | 55.3 % | 57.1 % |
| … operand cells only / opcode cells | 50.3 % / 5.1 % | 52.8 % / 4.4 % |
| init-time writes into the load image (relocation, patching) | 88.7 % | 95.8 % |
| `(zp),Y` addressing in play | 85.2 % | 94.6 % |
| illegal opcodes executed in play | 4.7 % | 1.5 % |
| `JMP (ind)` in play | 7.1 % | 1.0 % |
| RTS not matching a JSR (the RTS trick) | 1.7 % | 0.4 % |
| any volatile read in play (excluding acks) | 8.6 % | 6.9 % |
| reads uninitialised RAM (power-on pattern dependence) | 12.4 % | 7.8 % |
| ≥ 50 % of indexed sites have a voice-like domain ({0,1,2}, {0,7,14}, {0..3}) | 75.1 % | 91.6 % |

The programs are small and flat: median 400 executed play sites (p99 ≈ 1,100, max
2,092), median 268 instructions per tick, median 100 bytes of state footprint, JSR
depth p90 = 2, no recursion. Every graph algorithm in S2–S6 may therefore be
quadratic; the tracer and the verifier are the cost. Measured throughput is
480–580 k instructions/s, so a full HVSC pass is a few hundred CPU-hours.

Five things the survey settles, each an architectural commitment above:

1. **The unit is the tick and the observable is the write list** — a third of the
   CIA-timed 11 % run at rates that are not frame multiples, so "per frame" would
   be undefined.
2. **Dynamic first is not optional** — 57 % of tunes by weight modify executed
   instruction bytes during play and 96 % patch the load image at init, so static
   disassembly of the file bytes is wrong for most of HVSC.
3. **The inputs are few and mechanically classifiable**, so pinned streams cover
   all of them, and the power-on pattern is part of the image.
4. **Voice state announces itself** — the SID stride appears in 90 % of tunes, so
   region stride analysis recovers per-voice structs without family knowledge.
5. **Decompile per tune** — exact executed-code identity within a family is ~5 %,
   so family knowledge may enter only as a name dictionary aligned by structure.

The done ledger — one line per landed stage, with its PR, headline measurement and
record — is [tuneprog-backlog.md](tuneprog-backlog.md) §3; the open work by lever is §2.

---

## 10. Module map

`deity_informant/tuneprog/`, 61 modules, 17,922 lines, none over 500
(`pipeline.py` is the longest at 494). Line counts from `wc -l` at this commit.

**Front end — S0/S1, the traced machine**

| module | lines | role |
| --- | ---: | --- |
| `machine` | 305 | S0: machine image, entry and cadence discovery, init runner, the 6510 port |
| `cia` | 248 | S0: the CIA 6526, as much of it as a schedule needs |
| `nmi` | 204 | S1: the second schedule — the CIA #2 NMI, when it fires and what it enters |
| `tracevm` | 490 | S1: the tracing VM — per-P-Code-op access attribution, sites, edges, logs |
| `trace` | 464 | S1: the tracer — init plus *n* ticks of one entry, and the `Trace` it makes |
| `tracedata` | 449 | the recorded trace: the S1 result type, its files, the union of several |
| `tracesite` | 185 | S1: one site, resolved once and compiled to Python |
| `traceflow` | 101 | S1: edges, call/return sites and the shadow stack |

**Front end — S2/S3, code and storage recovery**

| module | lines | role |
| --- | ---: | --- |
| `lift` | 227 | S2a: residualised lift — self-modified operand cells become memory loads |
| `cfg` | 351 | S2b: procedures from observed edges — clone-per-entry, tail calls, switches |
| `jumptab` | 373 | S2: a patched jump's domain from the tables its writers copy |
| `closure` | 347 | S2: the bounded static closure of the branch directions the trace never took |
| `siblings` | 476 | S2c: k static copies of one template, aligned pc by pc |
| `copyrows` | 452 | S2c: what folds — the rows of one family, and what each copy holds |
| `copymerge` | 165 | S2c: the copy index as a value — k chained copies plan down to one body |
| `regions` | 263 | S3: storage typing from the exact op-level access relation |

**The program — IR, S4, S8**

| module | lines | role |
| --- | ---: | --- |
| `ir` | 486 | the IR: node types, their JSON form, their algebra |
| `interp` | 288 | the machine state and the reference interpreter (the semantics) |
| `irwalk` | 349 | traversal of the IR: sub-expressions, values read, names, call order |
| `graph` | 99 | the CFG of one procedure: predecessors, dominators, natural loops, reverse postorder |
| `lower` | 266 | residualised P-Code → statements, the access typing, the machine's frames |
| `build` | 484 | front end → IR: one `Proc` per CFG procedure, one block per node |
| `wire` | 78 | the procedure interface: params, rets and call arguments, by liveness |
| `ssa` | 431 | S4: SSA over registers/flags/uniques, then DCE and copy/const propagation |
| `idioms` | 402 | S4: peepholes turning 6510 flag algebra back into ordinary expressions |
| `frames` | 409 | S4: the machine stack as frames — which pushes and pops are values |
| `stack` | 218 | S4: eliminating the machine stack |
| `emit` | 403 | S7: Python code generation, and the certificate writer |
| `verify` | 439 | S8: differential verification against the trace, and the certificate |
| `history` | 102 | S8: the per-tick history of every named cell, off the verified ticks (library only) |
| `period` | 113 | why a subtune's state does not repeat: per-cell periods, drift, the observable |

**Presentation — S5/S6**

| module | lines | role |
| --- | ---: | --- |
| `structure` | 413 | S5: loops, if/else, switch, `for`, phase, over a copy of the S4 IR |
| `loops` | 393 | S5: the loop domain — what a counted loop's index runs over |
| `inline` | 192 | S6: value inlining — a `let` folded into its uses |
| `texture` | 308 | S6: machine-texture removal over the presentation copy |
| `cells` | 275 | S6: a storage cell that holds one value — mirrors, slots, its own update |
| `gated` | 130 | S6: the expression rewrites the certified IR's intervals prove |
| `ranges` | 76 | what the certified IR proves about the value of a byte of memory |
| `frame` | 51 | S6: naming the frames `frames` proves — a push and its pop are one value |
| `partition` | 257 | S6: region typing by accessor-shape partition, and its mirror, the merge |
| `halves` | 240 | S6: the two halves of a 16-bit value — the cell pair, and the byte shapes |
| `word` | 265 | S6: where those byte shapes land in the program, and the SID's own pairs |
| `facts` | 361 | S6: the facts the names are derived from — one pass over the IR, per cell |
| `recover` | 477 | S6: stride views, roles, names — the naming plane |
| `views` | 276 | S6: group views — struct fields that are a per-copy address table |
| `copyview` | 312 | S6: a per-copy column read as the operand it stands for |
| `fold` | 472 | S6: outlining — a run of blocks with one role, or shared by two procedures |
| `tails` | 290 | S6: shared tails become procedures |
| `unroll` | 414 | S6: consecutive isomorphic siblings print once over an index |
| `live` | 249 | S6/S7: what a reader must see — live values, arguments, return registers |
| `provenance` | 315 | S6: T0 — one record per SID write site, `tuneprog.T0.json` |
| `accguard` | 232 | T1: control dependence without the back edges, the copy loop's scratch, `opened` |
| `accshape` | 411 | T1: the store's arms over its callers, the additive spine, the shift loop |
| `accdelta` | 133 | T1: section 5's delta grammar and how a cell spells in a record |
| `accrule` | 334 | T1: counters, bound, policy, rate, phase and scope of one recurrence |
| `acchist` | 445 | T1: a named-cell expression over the horizon; the interval and the replay |
| `accum` | 453 | T1: the accumulator plane, `tuneprog.T1.json` (library and tool, not a stage) |

**Text — S7**

| module | lines | role |
| --- | ---: | --- |
| `printer` | 468 | the `tuneprog.md` document: `meta`, `state`, `data`, `inputs`, `program` |
| `pseudocode` | 233 | one expression, one statement, one structured node |
| `cellref` | 340 | how storage is spelled: a cell, a struct field, a copy's slot, a register |
| `datablock` | 288 | the `data` section: the bytes the program reads and no store reaches |

**Driver, oracles, baseline**

| module | lines | role |
| --- | ---: | --- |
| `pipeline` | 497 | the end-to-end driver, chunked against a CPU budget |
| `resume` | 67 | what a resumed run may keep |
| `__init__` | 138 | the package guide and its public API |
| `grid` | 157 | per-frame SID register grids, every write framed by the interrupt |
| `tunes` | 60 | every HVSC tune this repository names, once |
| `ghidra_facts` | 254 | export a finished output directory as facts for headless Ghidra |
| `ghidra_compare` | 223 | the differential complexity oracle, procedure by procedure |

Outside the package: `deity_informant/lifter.py` (1,007), `vm.py` (318),
`c64.py` (173), `cli.py` (149).

### 10.1 Tests

883 tests: **807 hermetic**, **66 `hvsc`**, **10 `oracle`**; coverage gate 85 %
(`--cov-fail-under=85`), measured at 96 %.

| path | contents |
| --- | --- |
| `tests/` | the lifter, cycle tables, illegals, the VM, `c64`, the CLI, `hello_world`, the SLEIGH SMC spec, the `sidplayfp` oracle, the survey sweep |
| `tests/tuneprog/` | one file per module or mechanism; `_asm.py` assembles snippets with the `jennings` assembler, `_prog.py` runs a snippet through the whole pipeline to its print, `_hvsc.py` resolves a real tune |
| `tests/tuneprog/test_hvsc_*.py` | marker `hvsc`: the exemplar families end to end, and `test_hvsc_certify.py` against `docs/certificates/` |
| `tests/test_oracle.py` | marker `oracle`: byte-exact SID grids against the Dockerized `sidtrace` oracle |

Markers are declared in `pyproject.toml`. `.github/workflows/ci.yml` runs three
jobs: `test` (`-m "not oracle and not hvsc"`, `-n auto`, `--cov-fail-under=85`),
`oracle` (both markers, so a flaky oracle or an HVSC fetch never blocks the unit
build) and `ghidra-integration`, which builds `Dockerfile.ghidra` and runs the four
headless tests of `ghidra/6510/headless/run.sh` — the illegal `LAX`/`ISC` decode,
the hello-world SMC export, the complexity/coverage oracle and the P-Code emulator
oracle.

`.github/workflows/nightly.yml` (cron plus `workflow_dispatch`) is the fourth job:
it reproduces every committed certificate and runs the three Ghidra oracles over
the result, in four shards of every fourth certificate, because one headless export
is 13 s and its emulate 9 s. It fails on a certificate that does not reproduce and
on `ours_bigger > 0`; the emulate verdict is reported, not enforced, the oracle
emulating one entry.

---

## 11. Process

- **One agent per stage**, in its own worktree with `PYTHONPATH` pinned, the
  certificate as acceptance, and a read-only reviewer between the stage and the
  merge. The reviewer refutes new tunable constants, duplicated mechanisms, tests
  that encode an exemplar rather than an invariant, and modules over 500 lines.
- **Every module ≤ 500 lines.** A new mechanism names the view or pass vocabulary
  it belongs to — views over regions, the naming plane, structural passes proven
  by alpha-equivalence — or it is not added.
- **No new role or view heuristic** without a hermetic snippet test and two
  families that need it, or one family plus a survey count.
- **The certificate is the only acceptance test.** A presentation change leaves
  `tuneprog.py` byte-identical or explains the change; `tools/tuneprog_recert.py`
  is green (51 of 51) before and after; a consolidation pass follows every three
  stages.
- **The prototype documents are records**; the living documents are this one,
  [tuneprog-backlog.md](tuneprog-backlog.md) and
  [survey-tuneprog.md](survey-tuneprog.md).
- **Every brief carries the global directives**: no tuning constants; `black`,
  `pylint` and `pytest -n auto` clean; coverage above 85 %; 60 CPU-seconds per
  script, which is why every long tool is `--budget`/`--resume` and exits 2 while
  work remains. Each stage ends with a "what remains" list, which becomes the
  backlog's §2.

Every presentation change states the six numbers of §6.2 before and after, over
the 51 certificates, and every stage lands on green CI with the recert reproduced.
The open packages, in order, are [tuneprog-backlog.md](tuneprog-backlog.md) §4.
