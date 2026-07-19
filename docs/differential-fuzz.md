# Byte-exactness differential fuzzer

A seeded, reproducible corpus of synthetic 6510 playroutines run through three
independent legs; every leg must emit the **identical ordered write stream**.
Any divergence is a deity byte-exactness failure mode. Sources:
`tests/_fuzzgen.py` (generator) and `tests/test_differential_fuzz.py` (legs).

## Generator

`_fuzzgen` is a two-pass label assembler built on deity's own opcode table
(`OPS` inverted to `(mnemonic, mode) -> byte`, plus a whitelist of the illegal
bytes real players use) and a set of parameterised **idiom templates**. Each
template is a well-formed routine with a guaranteed balancing `RTS`, tagged with
the idiom class(es) it exercises, and seeded by a numpy `default_rng` so the
whole corpus is byte-reproducible. `players(per)` emits `per` instances of every
template (default `per=8` → 128 players).

## Idiom classes and coverage

`per=8` corpus (128 players). Each class is asserted to be exercised by `>0`
players (`test_class_coverage_exhaustive`).

| class          | players | templates |
|----------------|--------:|-----------|
| indexed        | 40 | table_index, jump_table, indy, indx, ram_output |
| smc            | 24 | smc_operand, smc_opcode, smc_branch |
| dispatch       | 24 | jmp_indirect, rts_trick, jump_table |
| dec_timer      | 24 | illegal_dcp_isc, dec_timer, multispeed |
| illegal        | 16 | illegal_lax_sax, illegal_dcp_isc |
| variable_row   | 16 | dec_timer, varlen_row |
| multispeed     |  8 | multispeed |
| volatile       |  8 | volatile |

Idioms covered per the brief: operand/opcode/branch self-modification;
`JMP (ind)`, RTS-trick (push addr-1 + RTS), and address-table dispatch;
`(zp),Y` / `(zp,X)` with 8-bit index/pointer wrap and page-crossing extra-cycle
cases; illegal `LAX`/`SAX`/`DCP`/`ISC`/`SLO`; `DEC` reload counters;
ctrl-byte-gated variable-length row decode; multispeed (inner-loop repeated SID
passes); and modelled volatile-IO reads (`$D41B`/`$D41C`/`$D012`).

## The three legs

1. **`PcodeVM` (`_Log` subclass + `wlog`)** — an independent oracle overriding
   `_wr` to capture the ordered `(reg, val)` stream, plus `wlog`'s
   `(cycle, reg, val)` for SID writes. Also run a `RecVM` concretely and assert
   its `wlog` and end-memory are bit-identical to the plain VM (recorder-VM
   concrete fidelity).
2. **Recorder replay** — `record(mem, run_sub, entry, outputs, frames).replay(i)`
   for each invocation `i`, asserted equal to leg 1's per-frame stream.
   Multi-frame players (timers, SMC walks, row decode) carry state across
   invocations exactly as `record` does.
3. **sidtrace oracle** — the player is wrapped as a PSID (`pysidtracker.write_psid`),
   rendered under the Dockerized `sidplayfp`/`sidtrace` image via `docker cp`
   (namespace-independent), and its SID register-change stream compared to the
   in-process change stream. Marked `@pytest.mark.oracle`; skips gracefully when
   Docker/pysidtracker is unavailable so hermetic CI stays green offline.

## Cost controls

Legs 1↔2 are cheap and run over the whole corpus under `pytest -n auto` (~9 s,
the core evidence). Leg 3 is expensive (Docker + the 60 s/script budget) and runs
only over `ORACLE_SAFE` players with SID outputs, 2 seeds each (8 players), at a
short 4-second render.

## Volatile-IO handling

`volatile` players read cycle-derived sources deity models deterministically
(`$D41B`/`$D41C`/`$D012`). Legs 1 and 2 share deity's volatile model and execute
the identical instruction trace, so the reads agree by construction (the recorder
replays each volatile read's record-time concrete value). They are **excluded
from leg 3**: deity's raster/oscillator model is an intentional approximation of
sidplayfp's, not cycle-identical, so a byte-exact SID-stream match against the
oracle is not expected and would not indicate a recorder bug. Magic-constant
illegals (`ANE`/`LXA`) are likewise excluded from leg 3 (chip-dependent).

## Result

Over the `per=8` corpus (128 players, 130 in-process cases) plus the 8-player
sidtrace-oracle subset: **0 divergences**. Recorder replay reproduces the VM
write stream byte-exact for every frame of every player; the recorder VM is
cycle-exact (`wlog`) and memory-exact against the plain VM; and every
oracle-eligible player's SID change stream matches the sidtrace oracle.

No genuine recorder/VM bug and no legitimately-undefined-behaviour divergence was
surfaced. Had one appeared, the methodology is a manual delta-debug shrink
(reduce template params / instruction count to the minimal reproducer), then
classify as a genuine byte-exactness bug versus undefined behaviour (unstable
illegal magic constants, or volatile-IO nondeterminism against a
cycle-approximate oracle). The clean result is itself the deliverable: these
idiom classes are proved byte-exact across the lifter, VM, and recorder.
