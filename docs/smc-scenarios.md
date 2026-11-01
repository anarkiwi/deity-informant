# SMC scenarios

A framework that *generates* (not hand-enumerates) the self-modifying-code (SMC) scenarios a
6502/6510 program on a C64 can present to the whole-program lifter, labels them with independent
emulators, verifies the set is complete, and drives lift tests against a strict-xfail
expectation table. It lives in the `deity_informant.smc` package
(`deity_informant/smc/`) with the regenerator
[`tools/smcgen.py`](../tools/smcgen.py) and the HVSC labeller [`tools/smclabel.py`](../tools/smclabel.py).

## Definition

During a lifted call the lifter (`deity_informant.wholeprog`, `crates/di-lift`) assumes: code
bytes keep their lift-time values except known cells; control values (vectors, return
addresses) behave as in the facts; a fixed flat memory map; and nothing but the instruction
stream changes state. A scenario is a causal path that violates one of these at a fetch. Its
feature tuple is `(writer x channel, target role, timing, mapping)`.

## Axes and their derivation

`deity_informant.smc.space` reads the axes off the machine, never a hand list.

- **writer x channel** — every opcode's P-Code template (`deity_informant.lifter.lift`) is
  scanned for STORE micro-ops; each store's effective-address dataflow is walked to classify
  its channel (`zp`, `zp_indexed_wrap`, `abs`, `abs_indexed`, `indirect`, `sp_rel`,
  `unstable_high` for the SHA/SHX/SHY/TAS high-byte AND) and its RMW flag (from the machine's
  `opcode_layout`). The machine adds its own writers: `irq_push`/`nmi_push`/`brk_push` (the
  interrupt entry pushes), `cpu_port` ($00/$01 PLA writes, taking each CPU channel that reaches
  the port plus the reset write) and `reu_dma` (the REU command register, axes in `REU_AXES`
  from VICE `reu.h`: transfer type x C64/REU address control x autoload x $FF00 trigger).
- **target role** — the fetch/control reads: `opcode`, `operand`, `ptr_indirect` (the
  `(zp,X)`/`(zp),Y`/`JMP (ind)` pointer bytes, from the template loads), `ret_addr` (RTS/RTI
  pulls), `hw_vector` ($FFFA-$FFFF), `kernal_vector` ($0314/$0316/$0318), `bank_reg` ($01) and
  `reu_reg`.
- **timing** — the lifter's assumption set: `pre_lift`, `earlier_call`, `same_call_earlier`,
  `same_insn` (a store or RMW modifying its own opcode/operand) and `async` (a write by an
  interrupt handler or DMA).
- **mapping** — `flat`, `ram_under_rom`, `ram_under_io`, `rom_vs_ram`, from the PLA windows.

## Feasibility

`deity_informant.smc.feasible` decides every cell of the product by explicit *hard*
constraints only: the writer's reachable address set must meet the role's fetch region under
the mapping, a port writer changes the map rather than a byte, a device register is written by
the CPU alone, an interrupt is asynchronous. Each refuted cell records the first rule it fails
as its proof (`disjoint-address`, `unbankable-role`, `fetch-not-from-ram`,
`interrupt-is-async`, `device-reg-cpu-only`, ...). Soft assumptions are not encoded: the
classifier-closure test (below) would flag any feasible cell the oracle actually produces,
and that is how two over-strict rules were found and removed.

## Two independent oracles

Labels come from emulators other than the code under test, never from the lifter's own
`lift()` templates or `PcodeVM`.

- **CPU oracle** (`oracle.py`) — each call runs on jennings' native core
  (`NativeMPU`/`NativeMemory`) with a write subscription and a traced pc stream. Memory is
  replayed from the observed writes; the shared classifier (`classify.py`) checks each
  instruction's fetched bytes and control reads (decoded from the pc stream, not the lifter)
  against the lift-time image and attributes each change to the store that made it.
- **Machine oracle** (`machine.py`) — the cycle-exact vagrant-moses C64
  (`vagrant_moses._core.Machine`: 6510 on jennings-core, $01-banked PLA, VIC-II and CIAs, the
  ROM-less ROM set) runs calls from a trapped sentinel. A decode hook diffs RAM per
  instruction, follows interrupt entries/RTI as asynchronous contexts, and reads fetches
  through the live bank map, so banking, interrupt and port scenarios are labelled with the
  same classifier. The native machine ships in vagrant-moses' wheels (`vagrant-moses[cpu]`,
  in the `dev` extra); `deity_informant.smc.machine.core()` reports whether it is present.
  The REU plugs in as a DMA device once vagrant-moses lands its model; until then REU cells
  are gaps.

## Generation

`deity_informant.smc.generate` combines parametric CPU synthesis
(`synth.witness(channel, role, timing)`: a placement solver, a per-channel writer gadget, a
per-role reader and a timing harness, assembled with jennings), machine-level witnesses
(`synthm.py`: bank RAM under ROM/IO and fetch through another configuration, or a CIA-timer
IRQ whose handler patches code and vectors) and seeded SMC fuzzing of random programs. A
program is kept only when an oracle labels it with a new feature tuple, and a witness that does
not cleanly return is discarded; generation is deterministic and progress-reported.

## Completeness checks (tests)

`tests/test_smc.py` asserts: (i) the derived axes classify every STORE/LOAD of all 256
opcodes and every machine writer/device port, so a new opcode or device fails; (ii) every
feasible cell is witnessed by a corpus program or carries a lift/gap reason; (iii) classifier
closure — fuzzed programs produce only feasible cells; (iv) the generation coverage reaches a
fixpoint; plus that the CPU oracle module names jennings and not the lifter. The corpus
(`tests/smc_corpus.json`, labelled by both oracles) and the expectation table
(`tests/smc_expect.json`) are committed; regenerate with `python tools/smcgen.py`.

## Lift outcomes and the gap table

Each flat CPU corpus program is lifted (`wholeprog.lift`) and differentially checked against
the reference, classified `exact_nodeopt`, `exact_deopt`, `inexact` or `not_runnable`; every
flat CPU witness lifts exactly. A machine-level cell is not lifted (the lifter is flat RAM):
its expected outcome is the reason the flat lifter cannot model it. Every non-exact cell is
marked `pytest.mark.xfail(strict=True)`, so a cell that starts lifting exactly (the lifter
gains banking/interrupt support) XPASSes and forces a table update.

### Lift-gap summary

| Lift reason | Meaning |
|---|---|
| `lift-no-banking` | a non-flat mapping, a `cpu_port` writer, or a `bank_reg`/`hw_vector`/`kernal_vector` role — the lifter has a fixed flat map. |
| `lift-no-interrupts` | an `irq_push`/`nmi_push` writer or `async` timing — the lifter models no interrupt between instructions. |
| `lift-no-dma` | a `reu_dma` writer or `reu_reg` role — the lifter models no DMA. |

Unwitnessed feasible cells additionally carry a gap reason: `machine-not-synthesized` (a
banking/interrupt cell the machine gadgets do not individually place), `reu-no-oracle` (no
REU device yet) and `placement-not-synthesized` (a flat CPU cell the generator does not place,
e.g. stack-page code or SH* value aiming).

## Labelling real tunes

`tools/smclabel.py ROOT --sample N` runs each HVSC PSID/RSID tune's init and play on the
machine oracle (PSID called per frame, RSID driven by the tune's own IRQ) and records the
feature cells it exercises, pebble-parallel with a per-tune timeout. Its summary counts, per
cell and per lift-gap reason, how many tunes need it — a 500-tune sample found most SMC is
flat-liftable, with banking and interrupts the next most-needed categories, which is what the
lifter would gain most from supporting next.
